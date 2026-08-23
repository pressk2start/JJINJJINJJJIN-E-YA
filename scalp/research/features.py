# -*- coding: utf-8 -*-
"""features.py — 분 격자 피처/결과 행렬 생성 (탐색 단계).

설계 원칙
---------
1) 신호를 먼저 가정하지 않는다.
   "CLM/브레이크아웃 신호를 정의 → 그 이벤트만 라벨링"은 이미 그 신호가 옳다고 전제한다.
   여기선 **모든 분봉을 표본**으로 놓고, 결과(forward return)를 먼저 정의한 뒤
   사전조건 분포를 역으로 비교한다. → 임계치가 데이터에서 나오게 만드는 유일한 배치.

2) 분 격자(minute grid) 위에서 계산한다.
   Upbit 1분봉은 무체결 분을 건너뛴다(실측: 30위권 coverage 0.71~0.78).
   봉 인덱스로 "직전 60봉"을 잡으면 한산한 구간에서 60봉이 실제 몇 시간이 될 수 있다.
   → 전 구간 분 격자로 펴고, 가격은 마지막 체결가로 ffill, 거래대금은 0.
     (ffill은 조작이 아님: 체결이 없었으므로 가격이 실제로 안 움직인 것)
   → present 마스크로 "그 분에 실제 체결이 있었는가"를 보존.

3) 진입 가능성 제약을 표본 단계에서 건다.
   결정 시점 = 봉 t 완성. 체결 = t+1 open. 따라서
   present[t] AND present[t+1] 인 t만 유효 표본 (체결 없는 분에 체결시키지 않음).

4) 룩어헤드 차단: 모든 피처는 [t-W, t] 구간만 사용. 롤링 통계는 현재 봉 제외(직전 W분).
   결과(f*/mfe*/mae*)는 전부 t+1 이후만 사용.

출력: data/feat/{market}.npz  (float32)
  X       — 피처 행렬 (n_valid, len(FEATURES))
  Y       — 결과 행렬 (n_valid, len(OUTCOMES))
  tmin    — 각 표본의 분 오프셋(int32, t0 기준) → 시간순 OOS 분할용
  t0      — 격자 시작 분 문자열
"""
import os, sys, json, gzip, datetime
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "feat")
FMT = "%Y-%m-%dT%H:%M"

# 결정 시점 t에 확정 가능한 피처만. (룩어헤드 자동 배제 = 정의상 t 이하만 참조)
FEATURES = [
    "ret1", "ret3", "ret5", "ret15", "ret60",   # 다중 스케일 모멘텀
    "accel",                                     # ret1 - 직전 ret1 (가속)
    "vz60", "vz1440",                            # 거래대금 z-score (직전 60분 / 1440분)
    "vratio60",                                  # v_t / 직전 60분 평균 (z가 sd=0에서 죽는 구간 보완)
    "vcnt60",                                    # 직전 60분 중 체결 있었던 분 비율 (활성도)
    "body", "cstr", "rng", "uwick",              # 봉 형태
    "atr14p",                                    # ATR14 / close
    "dhi60", "dlo60",                            # 직전 60분 고가/저가 대비 위치
    "smadev60",                                  # close / SMA60 - 1
    "consec",                                    # 연속 양봉 수
]

# 결과: 전부 t+1 open 체결 기준 (현실적 진입가). k = 진입 후 분.
HORIZONS = [1, 2, 5, 10]
OUTCOMES = ([f"f{k}" for k in HORIZONS] +
            [f"mfe{k}" for k in HORIZONS] +
            [f"mae{k}" for k in HORIZONS])

MAXH = max(HORIZONS)
WARMUP = 1440 + 60      # vz1440 창 + 여유


def _grid(rec):
    """봉 리스트 → 분 격자 배열 (o,h,l,c는 ffill, v는 0충전, present 마스크)."""
    t = rec["t"]
    base = datetime.datetime.strptime(t[0], FMT)
    last = datetime.datetime.strptime(t[-1], FMT)
    n = int((last - base).total_seconds() // 60) + 1
    idx = np.array([int((datetime.datetime.strptime(x, FMT) - base).total_seconds() // 60)
                    for x in t], dtype=np.int64)
    o = np.full(n, np.nan, np.float64); h = np.full(n, np.nan, np.float64)
    l = np.full(n, np.nan, np.float64); c = np.full(n, np.nan, np.float64)
    v = np.zeros(n, np.float64); present = np.zeros(n, bool)
    o[idx] = rec["o"]; h[idx] = rec["h"]; l[idx] = rec["l"]; c[idx] = rec["c"]
    v[idx] = rec["v"]; present[idx] = True
    # ffill: 무체결 분의 가격 = 마지막 체결가 (o=h=l=c)
    fill = np.maximum.accumulate(np.where(present, np.arange(n), 0))
    cf = c[fill]
    o = np.where(present, o, cf); h = np.where(present, h, cf)
    l = np.where(present, l, cf); c = cf
    return o, h, l, c, v, present, base


def _roll(a, w, fn):
    """직전 w칸(현재 제외) 롤링. 앞쪽 w칸은 nan."""
    out = np.full(a.shape, np.nan)
    if len(a) > w:
        sw = np.lib.stride_tricks.sliding_window_view(a[:-1], w)   # 현재 제외
        out[w:] = fn(sw, axis=1)
    return out


def _roll_sum(a, w):
    cs = np.concatenate(([0.0], np.cumsum(a)))
    out = np.full(a.shape, np.nan)
    if len(a) > w:
        out[w:] = cs[w:-1] - cs[:-w-1]
    return out


def build(market):
    rec = collect.load(market)
    if rec is None or len(rec["t"]) < WARMUP + MAXH + 100:
        return None
    o, h, l, c, v, present, base = _grid(rec)
    n = len(c)
    if n < WARMUP + MAXH + 100:
        return None
    eps = 1e-12

    def ret(k):
        r = np.full(n, np.nan)
        r[k:] = c[k:] / np.maximum(c[:-k], eps) - 1.0
        return r

    ret1, ret3, ret5, ret15, ret60 = ret(1), ret(3), ret(5), ret(15), ret(60)
    accel = np.full(n, np.nan); accel[1:] = ret1[1:] - ret1[:-1]

    # 거래대금 z-score (직전 W분, 현재 제외). sd=0 → nan (판정 불가)
    def vz(w):
        s = _roll_sum(v, w); s2 = _roll_sum(v * v, w)
        m = s / w
        var = np.maximum((s2 - w * m * m) / (w - 1), 0.0)
        sd = np.sqrt(var)
        return np.where(sd > 0, (v - m) / np.maximum(sd, eps), np.nan), m

    vz60, vmean60 = vz(60)
    vz1440, _ = vz(1440)
    vratio60 = v / np.maximum(vmean60, eps)
    vcnt60 = _roll_sum(present.astype(np.float64), 60) / 60.0

    body = (c - o) / np.maximum(o, eps)
    hl = np.maximum(h - l, eps)
    cstr = np.where(h > l, (c - l) / hl, 0.5)
    rng = (h - l) / np.maximum(c, eps)
    uwick = (h - np.maximum(o, c)) / np.maximum(c, eps)

    # Wilder ATR14
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = np.full(n, np.nan); atr[13] = tr[:14].mean()
    for i in range(14, n):
        atr[i] = (13 * atr[i - 1] + tr[i]) / 14
    atr14p = atr / np.maximum(c, eps)

    hi60 = _roll(h, 60, np.max); lo60 = _roll(l, 60, np.min)
    dhi60 = c / np.maximum(hi60, eps) - 1.0
    dlo60 = c / np.maximum(lo60, eps) - 1.0
    sma60 = _roll_sum(c, 60) / 60.0
    smadev60 = c / np.maximum(sma60, eps) - 1.0

    up = (c > o).astype(np.int32)
    consec = np.zeros(n, np.float64)
    run = 0
    for i in range(n):
        run = run + 1 if up[i] else 0
        consec[i] = run

    F = {"ret1": ret1, "ret3": ret3, "ret5": ret5, "ret15": ret15, "ret60": ret60,
         "accel": accel, "vz60": vz60, "vz1440": vz1440, "vratio60": vratio60,
         "vcnt60": vcnt60, "body": body, "cstr": cstr, "rng": rng, "uwick": uwick,
         "atr14p": atr14p, "dhi60": dhi60, "dlo60": dlo60, "smadev60": smadev60,
         "consec": consec}

    # ---- 결과: 진입가 = open[t+1] ----
    ent = np.full(n, np.nan); ent[:-1] = o[1:]
    O = {}
    for k in HORIZONS:
        fk = np.full(n, np.nan)
        fk[:n - k] = c[k:] / np.maximum(ent[:n - k], eps) - 1.0     # t+k 종가
        O[f"f{k}"] = fk
        # t+1 .. t+k 구간의 고가/저가
        mx = np.full(n, np.nan); mn = np.full(n, np.nan)
        if n > k:
            sw_h = np.lib.stride_tricks.sliding_window_view(h[1:], k)
            sw_l = np.lib.stride_tricks.sliding_window_view(l[1:], k)
            m = len(sw_h)
            mx[:m] = sw_h.max(axis=1); mn[:m] = sw_l.min(axis=1)
        O[f"mfe{k}"] = mx / np.maximum(ent, eps) - 1.0
        O[f"mae{k}"] = mn / np.maximum(ent, eps) - 1.0

    X = np.column_stack([F[k] for k in FEATURES])
    Y = np.column_stack([O[k] for k in OUTCOMES])

    valid = (present & np.roll(present, -1))                 # t, t+1 모두 체결 존재
    valid[-(MAXH + 1):] = False
    valid[:WARMUP] = False
    valid &= np.isfinite(X).all(axis=1) & np.isfinite(Y).all(axis=1)

    tmin = np.nonzero(valid)[0].astype(np.int32)
    return {"X": X[valid].astype(np.float32), "Y": Y[valid].astype(np.float32),
            "tmin": tmin, "t0": base.strftime(FMT), "n_grid": n,
            "present_rate": float(present.mean())}


def main():
    os.makedirs(DIR, exist_ok=True)
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "min1")
    mks = sorted(f[:-8] for f in os.listdir(src) if f.endswith(".json.gz"))
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    if only: mks = [m for m in mks if m in only]
    print(f"[feat] {len(mks)} markets · {len(FEATURES)} features · {len(OUTCOMES)} outcomes", flush=True)
    tot = 0
    for m in mks:
        try:
            r = build(m)
        except Exception as e:
            print(f"  {m:14} ERR {e}", flush=True); continue
        if r is None:
            print(f"  {m:14} skip (데이터 부족)", flush=True); continue
        np.savez_compressed(os.path.join(DIR, m + ".npz"), **r)
        tot += len(r["X"])
        print(f"  {m:14} n={len(r['X']):7} grid={r['n_grid']:7} present={r['present_rate']:.3f}", flush=True)
    print(f"[feat] 총 표본 {tot:,}", flush=True)
    json.dump({"features": FEATURES, "outcomes": OUTCOMES, "horizons": HORIZONS},
              open(os.path.join(DIR, "_schema.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
