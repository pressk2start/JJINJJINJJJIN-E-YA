# -*- coding: utf-8 -*-
"""seconds.py — 초봉 해상도 이벤트 수집·라벨링 (탐색 2단계).

왜 초봉이 필수인가 (1단계 sweep.py 실측 근거)
  1분봉 브래킷 평가에서 보수(SL 우선 가정)와 낙관(TP 우선 가정)의 간격이 0.3%p 이상.
  찾으려는 엣지는 0.1%대 → **해상도가 엣지보다 거칠어서 판정 자체가 불가능**.
  초봉이면 TP/SL 중 무엇이 먼저 닿았는지 실제 순서로 확정된다.

무엇을 묻는가
  1단계 결론: 1분봉 캔들 피처는 변동성만 알려주고 방향은 못 알려줌 (d_up ≈ d_dn).
  → 그렇다면 "장이 살아있는 분"(고변동성)만 후보로 놓고,
    **진입 직전 5~30초 마이크로구조**가 방향을 가르는지 본다.
    이게 사용자가 요구한 "그 급등 전 5~30초에 무슨 일이 있었나"의 정확한 형태.

표본 설계 (선택편향 차단)
  · 후보 = train 분위수 기준 atr14p 상위 구간 (관찰 cutpoint 아님)
  · 대조 = 같은 마켓·같은 날의 무작위 분 (matched control)
    → 후보군만 보면 "고변동성이면 원래 그렇다"와 구분이 안 됨

API 비용: 이벤트당 4요청 (pre 200초 + post 600초). 8req/s 공유 리미터.
"""
import os, sys, json, gzip, time, argparse, datetime, random
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect, features as FT

DIR = os.path.dirname(os.path.abspath(__file__))
FEAT = os.path.join(DIR, "data", "feat")
OUTF = os.path.join(DIR, "data", "sec_events.json.gz")
SFMT = "%Y-%m-%dT%H:%M:%S"
MFMT = "%Y-%m-%dT%H:%M"

PRE_SEC = 200          # 진입 직전 관측 창(초) — 1요청
POST_SEC = 600         # 진입 후 관측 창(초) — 3요청
TP_GRID = [0.2, 0.3, 0.5, 0.8, 1.2]
SL_GRID = [0.2, 0.3, 0.5]


def fetch_seconds(market, to_dt, count=200):
    return collect.get("/candles/seconds",
                       {"market": market, "count": count, "to": to_dt.strftime(SFMT)})


def window(market, entry_dt):
    """entry_dt(=분 t+1 시작) 기준 [entry-PRE, entry+POST] 초봉 (오름차순, sparse)."""
    rows = {}
    for off in (POST_SEC, 400, 200, 0):
        for c in fetch_seconds(market, entry_dt + datetime.timedelta(seconds=off)):
            rows[c["candle_date_time_utc"]] = (
                c["opening_price"], c["high_price"], c["low_price"],
                c["trade_price"], c.get("candle_acc_trade_price", 0.0))
    out = []
    for k in sorted(rows):
        dt = datetime.datetime.strptime(k, SFMT)
        out.append((int((dt - entry_dt).total_seconds()),) + rows[k])
    return [r for r in out if -PRE_SEC <= r[0] <= POST_SEC]


def pre_features(w):
    """진입 직전 마이크로구조 (전부 t 이하 = 룩어헤드 없음)."""
    pre = [r for r in w if r[0] <= 0]
    if len(pre) < 5: return None
    last = pre[-1][4]                                     # 직전 체결가
    def ret(sec):
        base = None
        for r in pre:
            if r[0] >= -sec: base = base if base is not None else r[1]
        return (last / base - 1.0) * 100.0 if base else 0.0
    act = lambda sec: sum(1 for r in pre if r[0] >= -sec) / float(sec)
    vol = lambda sec: sum(r[5] for r in pre if r[0] >= -sec)
    v30, v200 = vol(30), vol(200)
    return {"ret5s": ret(5), "ret10s": ret(10), "ret30s": ret(30), "ret60s": ret(60),
            "act5s": act(5), "act30s": act(30), "act60s": act(60),
            "vshare30": (v30 / v200 * (200.0 / 30.0)) if v200 > 0 else 0.0,
            "n_pre": len(pre), "last": last}


def bracket_exact(w, tp, sl, slip_bps=5.0):
    """초 단위 실제 순서로 TP/SL 확정. 진입가 = 진입 후 첫 초봉 open (+슬리피지)."""
    post = [r for r in w if r[0] > 0]
    if not post: return None
    entry = post[0][1] * (1.0 + slip_bps / 10000.0)
    tp_px = entry * (1 + tp / 100.0); sl_px = entry * (1 - sl / 100.0)
    for off, o, h, l, c, v in post:
        if l <= sl_px and h >= tp_px:                     # 같은 1초 안에서 둘 다 → 보수적으로 SL
            return {"outcome": "sl_same_sec", "ret": -sl, "t": off}
        if l <= sl_px: return {"outcome": "sl", "ret": -sl, "t": off}
        if h >= tp_px: return {"outcome": "tp", "ret": tp, "t": off}
    last = post[-1][4] * (1.0 - slip_bps / 10000.0)
    return {"outcome": "timeout", "ret": (last / entry - 1.0) * 100.0, "t": post[-1][0]}


def select(n_event, n_control, quantile, seed=7):
    """후보(고변동성 분) + 대조(같은 마켓·같은 날 무작위 분)."""
    rnd = random.Random(seed)
    files = sorted(f for f in os.listdir(FEAT) if f.endswith(".npz"))
    ai = FT.FEATURES.index("atr14p")
    ev, ct = [], []
    for f in files:
        m = f[:-4]; z = np.load(os.path.join(FEAT, f))
        X = z["X"]; tmin = z["tmin"]; t0 = str(z["t0"])
        base = datetime.datetime.strptime(t0, MFMT)
        col = X[:, ai]
        cutoff = int(len(col) * 0.70)
        thr = float(np.quantile(col[:cutoff], quantile))   # train 구간 분위수
        hit = np.nonzero(col >= thr)[0]
        picked, lastmin = [], -10**9
        for j in hit:                                       # cooldown 10분
            if tmin[j] - lastmin >= 10:
                picked.append(j); lastmin = tmin[j]
        rnd.shuffle(picked)
        for j in picked[:n_event]:
            ev.append({"market": m, "min": int(tmin[j]),
                       "dt": (base + datetime.timedelta(minutes=int(tmin[j]) + 1)).strftime(SFMT),
                       "kind": "event", "atr14p": float(col[j])})
        allidx = list(range(len(col))); rnd.shuffle(allidx)
        for j in allidx[:n_control]:
            ct.append({"market": m, "min": int(tmin[j]),
                       "dt": (base + datetime.timedelta(minutes=int(tmin[j]) + 1)).strftime(SFMT),
                       "kind": "control", "atr14p": float(col[j])})
    return ev + ct


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=40, help="마켓당 이벤트 표본")
    ap.add_argument("--controls", type=int, default=15, help="마켓당 대조 표본")
    ap.add_argument("--quantile", type=float, default=0.95)
    a = ap.parse_args()
    cands = select(a.events, a.controls, a.quantile)
    print(f"[sec] 후보 {sum(1 for c in cands if c['kind']=='event')} · "
          f"대조 {sum(1 for c in cands if c['kind']=='control')} · 총 {len(cands)} "
          f"(예상 {len(cands)*4/8/60:.1f}분)", flush=True)
    out = []; t0 = time.time()
    for i, c in enumerate(cands):
        try:
            w = window(c["market"], datetime.datetime.strptime(c["dt"], SFMT))
            pf = pre_features(w)
            if pf is None: continue
            rec = dict(c); rec.update(pf); rec["brackets"] = {}
            for tp in TP_GRID:
                for sl in SL_GRID:
                    b = bracket_exact(w, tp, sl)
                    if b: rec["brackets"][f"{tp}/{sl}"] = b
            rec["n_post"] = sum(1 for r in w if r[0] > 0)
            out.append(rec)
        except Exception as e:
            print(f"  {c['market']} {c['dt']} ERR {e}", flush=True)
        if (i + 1) % 50 == 0:
            el = time.time() - t0
            print(f"  [{i+1}/{len(cands)}] {el/60:.1f}분 · 남은 {el/(i+1)*(len(cands)-i-1)/60:.1f}분", flush=True)
    with gzip.open(OUTF, "wt", encoding="utf-8") as f:
        json.dump(out, f)
    print(f"[sec] 저장 {len(out)}건 → {OUTF}", flush=True)


if __name__ == "__main__":
    main()
