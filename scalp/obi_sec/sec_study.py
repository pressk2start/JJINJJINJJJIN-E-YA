"""sec_study.py — 초봉 표본으로 스켈핑 후보 신호를 **탐색**한다 (임계치 발견 단계).

두 방향을 같이 본다:
  [역방향] "향후 60초 +0.5%"가 실제로 일어난 시점들의 **직전** 특징 분포 vs 전체 분포.
           → 급등 전에 무엇이 달라지는가. 임계치 후보의 출처.
  [순방향] 그 임계치를 실제로 걸었을 때의 N / 승률 / 평균 / 중앙 / MFE / MAE / PF / 비용후 기대값.
           → 역방향에서 보인 차이가 거래로 환금되는가. (대개 안 된다 — 그걸 확인하는 게 목적)

저장소 규율 준수 (ARCHIVE.md · PR2_LEVER_A_SPEC.md · backtest_exit.py):
  · 비용은 **%p 뺄셈** (곱셈 haircut 금지 — H2 버그가 부호를 뒤집은 전례)
  · BASE 0.20%p / STRESS 0.30%p 두 시나리오 병기
  · 시간순 train/test 60:40 분할. 임계치는 train에서만 고르고 test는 확인만
  · 셀 최소 표본 n≥100 (backtest_exit.py:564)
  · 승률 46~54%는 base-rate 노이즈 → 효과로 읽지 않는다 (PR2_LEVER_A_SPEC.md:163)
  · 검정한 셀 개수를 명시 (다중검정 노출 공개)

⚠ 이 스크립트의 출력은 **탐색 결과**다. 여기서 고른 임계치의 성과를 같은 데이터로 주장하면
   in-sample 과적합이다. 확정은 봉인 후 새 forward 구간에서만.

사용:
  python3 sec_study.py data/sec_*.jsonl.gz
"""
import os, sys, gzip, json, math, glob, argparse
from collections import defaultdict

COST_BASE = 0.20      # %p 왕복 (research/cohort.py:19-21 표준)
COST_STRESS = 0.30    # %p 왕복 (research/live_cohort_resim.py:49 STRESS_COST)
HORIZONS = [30, 60, 120]
SEG_GAP = 120.0       # 이 이상 벌어지면 다른 표본 구간으로 간주


# ------------------------------------------------------------------
# 로드 & 구간 분할
# ------------------------------------------------------------------
def load(paths):
    rows = []
    for pat in paths:
        for p in sorted(glob.glob(pat)):
            op = gzip.open if p.endswith(".gz") else open
            with op(p, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
    by = defaultdict(list)
    for r in rows:
        by[r["market"]].append(r)
    segs = []
    for m, rs in by.items():
        rs.sort(key=lambda x: x["ts"])
        cur = [rs[0]]
        for a, b in zip(rs, rs[1:]):
            if b["ts"] == a["ts"]:
                continue
            if b["ts"] - a["ts"] > SEG_GAP:
                segs.append((m, cur)); cur = [b]
            else:
                cur.append(b)
        segs.append((m, cur))
    return [(m, s) for m, s in segs if len(s) >= 120]


# ------------------------------------------------------------------
# 특징량 — 전부 시점 t 이하 데이터만 사용 (룩어헤드 금지)
# ------------------------------------------------------------------
def _idx_at_or_before(seg, i, back_sec):
    """t-back_sec 이하의 마지막 인덱스. 없으면 None."""
    target = seg[i]["ts"] - back_sec
    j = i
    while j >= 0 and seg[j]["ts"] > target:
        j -= 1
    return j if j >= 0 else None


def _win(seg, i, back_sec):
    """(t-back_sec, t] 구간 인덱스 슬라이스."""
    lo = seg[i]["ts"] - back_sec
    j = i
    while j >= 0 and seg[j]["ts"] > lo:
        j -= 1
    return seg[j + 1:i + 1]


def _std(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def features(seg, i):
    """시점 i의 특징. 데이터 부족이면 None."""
    c = seg[i]["c"]
    if c <= 0:
        return None
    f = {}
    for h in (5, 10, 30, 60):
        j = _idx_at_or_before(seg, i, h)
        if j is None:
            return None
        p = seg[j]["c"]
        if p <= 0:
            return None
        f[f"ret_{h}s"] = (c / p - 1.0) * 100.0

    w10 = _win(seg, i, 10)
    base = _win(seg, i, 310)
    base = [r for r in base if r["ts"] <= seg[i]["ts"] - 10]
    if len(base) < 30 or len(w10) < 1:
        return None

    # 거래대금 z: 최근 10초 대금 vs 직전 300초를 10초 버킷으로 나눈 분포
    v_now = sum(r["value"] for r in w10)
    t0 = seg[i]["ts"] - 310
    buckets = defaultdict(float); cnts = defaultdict(int)
    for r in base:
        k = int((r["ts"] - t0) // 10)
        buckets[k] += r["value"]; cnts[k] += 1
    bv = list(buckets.values())
    if len(bv) < 20:
        return None
    mu = sum(bv) / len(bv); sd = _std(bv)
    f["val_z"] = 0.0 if sd <= 0 else (v_now - mu) / sd
    f["val_now"] = v_now

    # 체결 빈도 z: 10초당 거래발생 초 수
    nv = list(cnts.values())
    mun = sum(nv) / len(nv); sdn = _std(nv)
    f["n_z"] = 0.0 if sdn <= 0 else (len(w10) - mun) / sdn

    # 60초 레인지 내 위치 (1 = 신고가)
    w60 = _win(seg, i, 60)
    hi = max(r["h"] for r in w60); lo = min(r["l"] for r in w60)
    f["rpos"] = 0.5 if hi <= lo else (c - lo) / (hi - lo)

    # 60초 실현변동성 (bp, 초당 수익률 표준편차)
    rets = []
    for a, b in zip(w60, w60[1:]):
        if a["c"] > 0:
            rets.append((b["c"] / a["c"] - 1.0) * 1e4)
    f["vol60_bp"] = _std(rets)

    # 가속: 5초 속도가 30초 평균속도보다 얼마나 빠른가
    f["accel"] = f["ret_5s"] - f["ret_30s"] / 6.0
    return f


def labels(seg, i):
    """진입 = seg[i]['c']. 각 horizon의 fwd/MFE/MAE (%). 커버리지 부족이면 None."""
    c = seg[i]["c"]
    out = {}
    for h in HORIZONS:
        t_end = seg[i]["ts"] + h
        if seg[-1]["ts"] < t_end:
            return None
        j = i
        hi = -1e18; lo = 1e18
        while j + 1 < len(seg) and seg[j + 1]["ts"] <= t_end:
            j += 1
            hi = max(hi, seg[j]["h"]); lo = min(lo, seg[j]["l"])
        if j == i:
            return None
        out[f"fwd_{h}"] = (seg[j]["c"] / c - 1.0) * 100.0
        out[f"mfe_{h}"] = (hi / c - 1.0) * 100.0
        out[f"mae_{h}"] = (lo / c - 1.0) * 100.0
        # --- 보수 체결(conservative fill) ---
        # 초봉 close는 '마지막 체결가'다. 하락 직후의 close는 대개 **매도호가를 때린 프린트**라
        # 그 가격에 매수할 수 없다. 실제로는 다음 체결 구간의 불리한 가격에 사게 된다.
        # 반전 신호의 최대 함정(호가 바운스 착시)을 가르는 유일한 검정이므로 항상 같이 계산한다.
        #   진입 = 다음 체결 초의 고가 (최악 매수)  ·  청산 = 종료 초의 저가 (최악 매도)
        # 중간 가정(mid): 진입 = 다음 체결 초의 시가(신호 직후 첫 체결) · 청산 = 종료 초 종가.
        # 낙관(fwd)과 보수(cons) 사이의 현실적 중앙값. 세 가정의 부호가 갈리면 "확정 불가"가 결론.
        if i + 1 < len(seg):
            e = seg[i + 1]["h"]
            out[f"cons_{h}"] = ((seg[j]["l"] / e - 1.0) * 100.0) if e > 0 else None
            o = seg[i + 1]["o"]
            out[f"mid_{h}"] = ((seg[j]["c"] / o - 1.0) * 100.0) if o > 0 else None
        else:
            out[f"cons_{h}"] = None
            out[f"mid_{h}"] = None
    return out


def build(segs):
    obs = []
    for m, seg in segs:
        for i in range(len(seg)):
            f = features(seg, i)
            if f is None:
                continue
            l = labels(seg, i)
            if l is None:
                continue
            row = {"market": m, "ts": seg[i]["ts"]}
            row.update(f); row.update(l)
            obs.append(row)
    obs.sort(key=lambda r: r["ts"])
    return obs


# ------------------------------------------------------------------
# 통계 헬퍼
# ------------------------------------------------------------------
def q(xs, p):
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))]


def cell_stats(rows, h, cost, field="fwd"):
    """셀 성과. net = ret - cost (%p 뺄셈). field='cons'면 보수 체결 기준."""
    rows = [r for r in rows if r.get(f"{field}_{h}") is not None]
    if not rows:
        return None
    net = [r[f"{field}_{h}"] - cost for r in rows]
    wins = [x for x in net if x > 0]; losses = [x for x in net if x <= 0]
    gp = sum(wins); gl = -sum(losses)
    return {
        "n": len(rows),
        "wr": len(wins) / len(rows),
        "avg": sum(net) / len(net),
        "med": q(net, 0.5),
        "mfe": sum(r[f"mfe_{h}"] for r in rows) / len(rows),
        "mae": sum(r[f"mae_{h}"] for r in rows) / len(rows),
        "pf": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0),
    }


def fmt(x, n=3):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    return f"{x:,.{n}f}"


# ------------------------------------------------------------------
# 사전등록 그리드 — 실행 전에 고정한다 (사후에 늘리면 다중검정 은폐)
# ------------------------------------------------------------------
GRID = {
    "ret_5s":  [0.10, 0.20, 0.30, 0.50],
    "ret_10s": [0.15, 0.30, 0.50, 0.80],
    "ret_30s": [0.30, 0.50, 0.80, 1.20],
    "ret_60s": [0.50, 1.00, 1.50, 2.00],
    "val_z":   [1.0, 2.0, 3.0, 4.0, 5.0],
    "n_z":     [1.0, 2.0, 3.0],
    "rpos":    [0.80, 0.90, 0.95, 0.99],
    "accel":   [0.05, 0.15, 0.30],
}
PAIRS = [("val_z", "ret_10s"), ("val_z", "rpos"), ("ret_10s", "rpos"),
         ("n_z", "ret_10s"), ("val_z", "accel")]

# 역방향(반전) 그리드 — "x <= th" 조건.
# [1] 역방향 분석에서 이벤트 직전 ret_*가 **음수**, rpos가 **바닥**으로 나오면
# 추격이 아니라 반전이 후보라는 뜻이므로 반드시 같이 검정해야 한다.
# ⚠ 이 그리드는 데이터를 보고 추가된 것이다 = in-sample 힌트. train/test 통과해도 '후보'일 뿐.
REV_GRID = {
    "ret_5s":  [-0.10, -0.20, -0.30, -0.50],
    "ret_10s": [-0.15, -0.30, -0.50, -0.80],
    "ret_30s": [-0.30, -0.50, -0.80, -1.20],
    "ret_60s": [-0.50, -1.00, -1.50, -2.00],
    "rpos":    [0.20, 0.10, 0.05, 0.01],
}
REV_PAIRS = [("ret_10s", "rpos"), ("ret_30s", "rpos"), ("ret_60s", "rpos")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--horizon", type=int, default=60)
    ap.add_argument("--min-n", type=int, default=100)
    ap.add_argument("--event-bp", type=float, default=0.5, help="역방향 이벤트 정의 (%%)")
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()

    segs = load(a.paths)
    obs = build(segs)
    if not obs:
        print("관측 없음 — sec_collect.py 로 먼저 수집하세요."); sys.exit(1)

    H = a.horizon
    split = int(len(obs) * 0.6)
    train, test = obs[:split], obs[split:]
    n_cells = 0

    print("=" * 84)
    print(f"SEC STUDY · 구간={len(segs)} · 관측={len(obs):,} · horizon={H}s "
          f"· train={len(train):,} / test={len(test):,} (시간순 60:40)")
    print(f"비용: BASE {COST_BASE}%p · STRESS {COST_STRESS}%p (%p 뺄셈)")
    print("=" * 84)

    # ---------- 기준선 ----------
    print("\n[0] 기준선 — 아무 때나 진입 (이 값을 못 넘으면 신호가 아니다)")
    print(f"    {'구간':<8} {'cost':<8} {'n':>8} {'승률':>7} {'평균%':>9} {'중앙%':>9} {'PF':>7}")
    for name, rows in (("ALL", obs), ("TRAIN", train), ("TEST", test)):
        for cname, cost in (("BASE", COST_BASE), ("STRESS", COST_STRESS)):
            s = cell_stats(rows, H, cost)
            print(f"    {name:<8} {cname:<8} {s['n']:>8,} {fmt(s['wr'],3):>7} "
                  f"{fmt(s['avg'],4):>9} {fmt(s['med'],4):>9} {fmt(s['pf'],2):>7}")
    base_all = cell_stats(obs, H, COST_BASE)

    # ---------- [0b] 구조적 상한: 가격이 비용만큼이라도 움직이는가 ----------
    # 신호가 아무리 좋아도 |가격변화| < 비용이면 이길 수 없다. 신호 논의 이전의 관문.
    print("\n[0b] 구조적 상한 — 애초에 가격이 비용만큼 움직이는가 (신호와 무관)")
    print(f"    {'H':>5} {'|Δ|p50%':>9} {'|Δ|p75%':>9} {'|Δ|p90%':>9} "
          f"{'P(Δ>0.20)':>10} {'P(Δ>0.30)':>10} {'MFE p50%':>9} {'P(MFE>0.20)':>12}")
    for h in HORIZONS:
        d = [abs(r[f"fwd_{h}"]) for r in obs]
        up = [r[f"fwd_{h}"] for r in obs]
        mfe = [r[f"mfe_{h}"] for r in obs]
        p20 = sum(1 for x in up if x > COST_BASE) / len(up)
        p30 = sum(1 for x in up if x > COST_STRESS) / len(up)
        pm = sum(1 for x in mfe if x > COST_BASE) / len(mfe)
        print(f"    {h:>5} {fmt(q(d,0.5),3):>9} {fmt(q(d,0.75),3):>9} {fmt(q(d,0.9),3):>9} "
              f"{fmt(p20,3):>10} {fmt(p30,3):>10} {fmt(q(mfe,0.5),3):>9} {fmt(pm,3):>12}")
    print("    * P(Δ>0.20) = 비용(BASE)을 넘는 상승이 나올 확률. 이게 낮으면 승률 상한이 낮다는 뜻.")
    print("    * MFE는 '완벽한 타이밍에 팔았다면'의 상한 — 실제로는 못 잡는다. 상한 진단용.")

    # ---------- [역방향] 이벤트 직전 분포 ----------
    ev = [r for r in obs if r[f"fwd_{H}"] >= a.event_bp]
    print(f"\n[1] 역방향 — '향후 {H}초 ≥ +{a.event_bp}%' 이벤트 직전 특징 분포")
    print(f"    이벤트 {len(ev):,}건 / 전체 {len(obs):,}건 = {len(ev)/len(obs)*100:.2f}%")
    if len(ev) >= 30:
        print(f"    {'feature':<10} {'전체p50':>10} {'이벤트p50':>10} {'전체p90':>10} {'이벤트p90':>10} {'분리도':>8}")
        for k in ["ret_5s", "ret_10s", "ret_30s", "ret_60s", "val_z", "n_z", "rpos", "vol60_bp", "accel"]:
            allv = [r[k] for r in obs]; evv = [r[k] for r in ev]
            sd = _std(allv)
            # Cohen's d — feature_screen.py:154 관행 (|d| ≥ 0.20 이어야 후보)
            d = 0.0 if sd <= 0 else (sum(evv) / len(evv) - sum(allv) / len(allv)) / sd
            print(f"    {k:<10} {fmt(q(allv,0.5),3):>10} {fmt(q(evv,0.5),3):>10} "
                  f"{fmt(q(allv,0.9),3):>10} {fmt(q(evv,0.9),3):>10} {fmt(d,2):>8}")
        print("    * 분리도 = Cohen's d. |d| < 0.20 이면 후보 자격 없음 (feature_screen.py:154)")
    else:
        print("    이벤트 표본 부족 — 분포 비교 생략")

    # ---------- [순방향] 단일 임계 ----------
    print(f"\n[2] 순방향 단일 임계 — TRAIN에서 통과한 셀만 TEST 표시 (n≥{a.min_n})")
    print(f"    {'조건':<20} {'n_tr':>7} {'avg_tr':>9} {'wr_tr':>7} "
          f"{'n_te':>7} {'avg_te':>9} {'wr_te':>7} {'MFE':>7} {'MAE':>7} {'PF':>6}")
    survivors = []
    for feat, ths in GRID.items():
        for th in ths:
            n_cells += 1
            tr = [r for r in train if r[feat] >= th]
            te = [r for r in test if r[feat] >= th]
            if len(tr) < a.min_n:
                continue
            s_tr = cell_stats(tr, H, COST_BASE)
            s_te = cell_stats(te, H, COST_BASE) if len(te) >= a.min_n else None
            mark = ""
            if s_tr["avg"] > 0:
                mark = "*"
                if s_te and s_te["avg"] > 0 and abs(s_te["avg"] - s_tr["avg"]) < 0.30:
                    mark = "**"
                    survivors.append({"cond": f"{feat}>={th}", "spec": [(feat, ">=", th)], "train": s_tr, "test": s_te})
            print(f"    {feat+'>='+str(th):<20} {s_tr['n']:>7,} {fmt(s_tr['avg'],4):>9} {fmt(s_tr['wr'],3):>7} "
                  f"{(s_te['n'] if s_te else 0):>7,} {fmt(s_te['avg'],4) if s_te else 'n/a':>9} "
                  f"{fmt(s_te['wr'],3) if s_te else 'n/a':>7} {fmt(s_tr['mfe'],3):>7} "
                  f"{fmt(s_tr['mae'],3):>7} {fmt(s_tr['pf'],2):>6} {mark}")

    # ---------- [순방향] 2중 조합 ----------
    print(f"\n[3] 순방향 2중 조합 (사전등록 {len(PAIRS)}쌍) — train 평균 양수 셀만 출력")
    printed = 0
    for f1, f2 in PAIRS:
        for t1 in GRID[f1]:
            for t2 in GRID[f2]:
                n_cells += 1
                tr = [r for r in train if r[f1] >= t1 and r[f2] >= t2]
                if len(tr) < a.min_n:
                    continue
                s_tr = cell_stats(tr, H, COST_BASE)
                if s_tr["avg"] <= 0:
                    continue
                te = [r for r in test if r[f1] >= t1 and r[f2] >= t2]
                s_te = cell_stats(te, H, COST_BASE) if len(te) >= a.min_n else None
                cond = f"{f1}>={t1} & {f2}>={t2}"
                mark = "*"
                if s_te and s_te["avg"] > 0 and abs(s_te["avg"] - s_tr["avg"]) < 0.30:
                    mark = "**"
                    survivors.append({"cond": cond, "spec": [(f1, ">=", t1), (f2, ">=", t2)], "train": s_tr, "test": s_te})
                print(f"    {cond:<30} n_tr={s_tr['n']:>6,} avg_tr={fmt(s_tr['avg'],4):>8} "
                      f"wr={fmt(s_tr['wr'],3)} | n_te={(s_te['n'] if s_te else 0):>6,} "
                      f"avg_te={fmt(s_te['avg'],4) if s_te else 'n/a':>8} {mark}")
                printed += 1
    if printed == 0:
        print("    train 평균이 양수인 조합 없음.")

    # ---------- [4] 반전(역방향) 그리드 ----------
    print(f"\n[4] 반전 그리드 — 'x <= 임계' (하락 직후 되돌림). [1]이 반전을 가리키면 필수 검정")
    print(f"    {'조건':<20} {'n_tr':>7} {'avg_tr':>9} {'wr_tr':>7} "
          f"{'n_te':>7} {'avg_te':>9} {'wr_te':>7} {'MFE':>7} {'MAE':>7} {'PF':>6}")
    for feat, ths in REV_GRID.items():
        for th in ths:
            n_cells += 1
            tr = [r for r in train if r[feat] <= th]
            te = [r for r in test if r[feat] <= th]
            if len(tr) < a.min_n:
                continue
            s_tr = cell_stats(tr, H, COST_BASE)
            s_te = cell_stats(te, H, COST_BASE) if len(te) >= a.min_n else None
            mark = ""
            if s_tr["avg"] > 0:
                mark = "*"
                if s_te and s_te["avg"] > 0 and abs(s_te["avg"] - s_tr["avg"]) < 0.30:
                    mark = "**"
                    survivors.append({"cond": f"{feat}<={th}", "spec": [(feat, "<=", th)], "train": s_tr, "test": s_te})
            print(f"    {feat+'<='+str(th):<20} {s_tr['n']:>7,} {fmt(s_tr['avg'],4):>9} {fmt(s_tr['wr'],3):>7} "
                  f"{(s_te['n'] if s_te else 0):>7,} {fmt(s_te['avg'],4) if s_te else 'n/a':>9} "
                  f"{fmt(s_te['wr'],3) if s_te else 'n/a':>7} {fmt(s_tr['mfe'],3):>7} "
                  f"{fmt(s_tr['mae'],3):>7} {fmt(s_tr['pf'],2):>6} {mark}")
    for f1, f2 in REV_PAIRS:
        for t1 in REV_GRID[f1]:
            for t2 in REV_GRID[f2]:
                n_cells += 1
                tr = [r for r in train if r[f1] <= t1 and r[f2] <= t2]
                if len(tr) < a.min_n:
                    continue
                s_tr = cell_stats(tr, H, COST_BASE)
                if s_tr["avg"] <= 0:
                    continue
                te = [r for r in test if r[f1] <= t1 and r[f2] <= t2]
                s_te = cell_stats(te, H, COST_BASE) if len(te) >= a.min_n else None
                cond = f"{f1}<={t1} & {f2}<={t2}"
                if s_te and s_te["avg"] > 0 and abs(s_te["avg"] - s_tr["avg"]) < 0.30:
                    survivors.append({"cond": cond, "spec": [(f1, "<=", t1), (f2, "<=", t2)], "train": s_tr, "test": s_te})
                print(f"    {cond:<30} n_tr={s_tr['n']:>6,} avg_tr={fmt(s_tr['avg'],4):>8} | "
                      f"n_te={(s_te['n'] if s_te else 0):>6,} avg_te={fmt(s_te['avg'],4) if s_te else 'n/a':>8} *")

    # ---------- 판정 ----------
    print("\n" + "=" * 84)
    print(f"판정 · 검정 셀 {n_cells}개 (다중검정 노출) · OOS 통과 = train 양수 AND test 양수 AND |Δ|<0.30%p")
    print("=" * 84)
    print(f"  기준선(무조건) 평균 = {fmt(base_all['avg'],4)}%p · 승률 {fmt(base_all['wr'],3)}")
    if not survivors:
        print("  ✗ OOS 통과 셀 0개.")
        print("    → 이 데이터·이 해상도에서 사전등록 그리드로는 엣지 없음이 결론.")
        print("    → 임계치를 더 조이는 게 아니라 축(오더북/체결틱)으로 내려가야 함.")
    else:
        print(f"  통과 셀 {len(survivors)}개 (기대 오탐 ≈ {n_cells*0.05:.0f}개 — 이 수보다 적으면 노이즈 의심):")
        for s in sorted(survivors, key=lambda x: -x["test"]["avg"]):
            t = s["test"]; r = s["train"]
            wrnote = " [wr 46~54% = base-rate 노이즈]" if 0.46 <= t["wr"] <= 0.54 else ""
            print(f"    · {s['cond']:<32} train {fmt(r['avg'],4)}%p (n={r['n']:,}) | "
                  f"test {fmt(t['avg'],4)}%p (n={t['n']:,}, wr={fmt(t['wr'],3)}){wrnote}")
    print("\n  ⚠ 통과 셀이 있어도 이건 **탐색 결과**다. 확정은 봉인 후 새 forward 구간에서만.")

    # ---------- [5] 생존 셀 정밀검증 — 이 저장소가 과거에 당한 두 함정 ----------
    if survivors:
        def match(r, spec):
            return all((r[f] >= t) if op == ">=" else (r[f] <= t) for f, op, t in spec)

        print("\n" + "=" * 84)
        print("[5] 생존 셀 정밀검증 — 통과했다고 채택하는 게 아니라, 여기서 죽이는 게 목적")
        print("=" * 84)
        print("\n  (a) 보수 체결 검정 — 초봉 close는 '마지막 체결가'라 그 가격에 살 수 없다.")
        print("      진입=다음 체결초 고가 / 청산=종료초 저가 로 바꾸면 남는가?")
        print("      → 여기서 죽으면 그건 알파가 아니라 **호가 바운스 착시**다.")
        print(f"      {'조건':<32} {'낙관avg':>9} {'중간avg':>9} {'보수avg':>9} {'보수wr':>8} {'n':>7} {'판정':>8}")
        for s in sorted(survivors, key=lambda x: -x["test"]["avg"])[:12]:
            sel = [r for r in obs if match(r, s["spec"])]
            opt = cell_stats(sel, H, COST_BASE, "fwd")
            con = cell_stats(sel, H, COST_BASE, "cons")
            mid = cell_stats(sel, H, COST_BASE, "mid")
            verdict = "생존" if (con and con["avg"] > 0) else ("불확정" if (mid and mid["avg"] > 0) else "소멸")
            print(f"      {s['cond']:<32} {fmt(opt['avg'],4):>9} {fmt(mid['avg'],4) if mid else 'n/a':>9} "
                  f"{fmt(con['avg'],4) if con else 'n/a':>9} {fmt(con['wr'],3) if con else 'n/a':>8} "
                  f"{(con['n'] if con else 0):>7,} {verdict:>8}")

        print("\n  (b) 종목 집중도 검정 — 과거 이 저장소의 모든 '엣지'는 단일종목 산물이었다")
        print("      (VANA→SOL→EGLD→XRP→BONK, ARCHIVE.md:76). 최다 기여 종목을 빼면 남는가?")
        print(f"      {'조건':<32} {'전체avg':>9} {'최다종목':>12} {'비중':>7} {'제외후avg':>10} {'판정':>8}")
        for s in sorted(survivors, key=lambda x: -x["test"]["avg"])[:12]:
            sel = [r for r in obs if match(r, s["spec"])]
            cnt = defaultdict(int)
            for r in sel:
                cnt[r["market"]] += 1
            if not cnt:
                continue
            top = max(cnt, key=cnt.get)
            share = cnt[top] / len(sel)
            full = cell_stats(sel, H, COST_BASE)
            excl = cell_stats([r for r in sel if r["market"] != top], H, COST_BASE)
            verdict = "생존" if (excl and excl["avg"] > 0) else "소멸"
            print(f"      {s['cond']:<32} {fmt(full['avg'],4):>9} {top:>12} {fmt(share,2):>7} "
                  f"{fmt(excl['avg'],4) if excl else 'n/a':>10} {verdict:>8}")

        print("\n  (a)와 (b)를 **둘 다** 통과한 셀만 후보다. 하나라도 소멸하면 기각.")

    if a.json_out:
        json.dump({"n_obs": len(obs), "n_cells": n_cells, "horizon": H,
                   "baseline": base_all, "survivors": survivors},
                  open(a.json_out, "w"), indent=1, default=str)
        print(f"\n  → {a.json_out}")


if __name__ == "__main__":
    main()
