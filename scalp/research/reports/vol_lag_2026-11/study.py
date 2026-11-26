# -*- coding: utf-8 -*-
"""거래대금 증가와 알트 후행 상승 단타 검증 (사용자 설계안 2026-11-25) — 탐색용. 돈을 넣는 근거가 아니다.
자료: 업비트 1분봉, 고정 20종목, 2025-11-19 ~ 2026-11-19 (탐색 구간). 2025-11 이전 1분봉은 열지 않는다(후보를 고정한 뒤 확인용).

정의 (결과를 보기 전에 고정, 2026-11-25. [안] = 설계안 그대로, [정함] = 설계안이 비워 둔 것을 이번에 정함)
 공통
  봉           1·3·5분봉을 각각 독립으로. 시계에 맞춘 봉(3분봉은 :00·:03…, 5분봉은 :00·:05…)을 1분봉으로 만든다 [정함]. 거래 없는 분은 직전 종가, 거래대금 0.
  신호 시각     그 봉이 닫힌 때. 같은 종목·같은 조합에서 신호 뒤 15분 안의 신호는 같은 사건 [안].
  진입         신호 뒤 거래가 난 첫 1분봉의 시가 [안: 분봉만 있으면 다음 봉 시가]. 5분 안에 거래가 없으면 미체결 [정함].
  고정 청산     진입 뒤 1·3·5·10·15분(참고 30·60분) 시점의 1분봉 종가 [안]. 기준은 5분 [안].
  추세 청산     1분 종가가 직전 완료 1분봉의 저가 아래로 마감하면 다음 분 시가에 청산, 아니면 15분에 청산 [안].
  목표·손절     진입가 +0.5% / −0.5% 중 15분 안에 먼저 닿은 쪽. 같은 1분봉에서 둘 다 닿으면 순서 미상 [정함: 폭].
  비용         왕복 0.20%(편도 수수료 0.05% + 체결 불리 0.05%), 민감도 0.40% [안].
  대조군       같은 종목 · 같은 달 · 같은 시간(한국 시각 1시간 단위) 칸에서, 신호 조건 가운데 '거래 증가'(또는 '선행 상승')만 없는 봉 [정함: 칸의 정의].
  날짜별 평균   같은 분에 난 신호를 먼저 평균 → 날(UTC)마다 평균 → 날들의 평균과 95% 구간.
  구간         앞 2025-11-19 ~ 2026-07-19, 뒤 2026-07-20 ~ 2026-11-19 (1분 급등 검토와 같은 경계).
  거래소 점검   BTC 가 30분 넘게 거래가 없던 구간과 그 뒤 60분은 신호와 대조군에서 뺀다(전 종목이 멈췄다 풀리는 때라 거래 증가가 아니다) [정함].
  호가 한 칸    신호 앞 24시간 동안 1분 종가 사이의 가장 작은 가격 차로 추정(신호 뒤 자료는 쓰지 않는다). 가격 대비 0.15% 이상이면 '호가 큰 종목' [정함].
 시나리오 1 — 횡보 중 거래 증가
  횡보         신호 봉을 포함한 최근 60분의 (고가 − 저가) ÷ 저가 ≤ 1%, 그리고 종가가 신호 봉 이전 24시간의 가격 범위 안 [안]. 15·30분 횡보도 따로 [안].
  거래 증가     막 닫힌 봉의 거래대금 ÷ 직전 24시간 같은 길이 봉들의 중앙값 ≥ 3배가 처음 된 때(직전 봉은 3배 미만) [안]. 2배·5배도 [안].
               중앙값이 0 이면(절반 넘는 봉에 거래가 없으면) 신호로 보지 않는다 [정함]. 두 봉 연속 = 처음 3배가 된 다음 봉도 3배 이상(그 봉이 닫힐 때 확정) [안].
 시나리오 2 — 뒤처진 알트의 상승
  선행 상승     직전 5분 동안 비교 알트 18개 중 12개 이상이 오르고 그 수익 중앙값 ≥ +0.3%, 대상은 같은 5분에 ±0.2% 안, BTC 는 ±0.3% 안 [안]. 돌아보는 길이 3·10분도 [안].
  기본 칸       두 시나리오 모두 1분봉 신호 · 5분 청산 · 왕복 0.20% [정함: 판단은 이 칸으로, 나머지는 참고].
 후보로 남기는 기준 (미리 정함): 기본 칸에서 앞·뒤 구간 모두 날짜별 평균 > 0, 전체 95% 하한 > 0, 대조군보다 높고, 가장 좋은 하루를 빼도 > 0. 아니면 접거나 보류.
사용: study.py DATA(c1_*.jsonl.gz) OUT_DIR"""
import bisect
import datetime as dt
import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv

DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
UNI = ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-WAVES", "KRW-AXS", "KRW-ETC", "KRW-CHZ", "KRW-SAND", "KRW-IQ", "KRW-APT",
       "KRW-ADA", "KRW-GMT", "KRW-KAVA", "KRW-POL", "KRW-TRX", "KRW-CELO", "KRW-ARK", "KRW-ALGO")
UTC = dt.timezone.utc
T0 = int(dt.datetime(2025, 11, 19, tzinfo=UTC).timestamp() // 60)
T1 = int(dt.datetime(2026, 11, 20, tzinfo=UTC).timestamp() // 60)
N = T1 - T0
CUT = int(dt.datetime(2026, 7, 20, tzinfo=UTC).timestamp() // 60) - T0
HZ = (1, 3, 5, 10, 15, 30, 60)
COOL, WAIT, BIG_TICK = 15, 5, 0.0015
NC = len(UNI)
log = lambda s: print(s, flush=True)


# ---------------------------------------------------------------- 자료
def load():
    cache = OUT / "dense.npz"
    if cache.exists():
        z = np.load(cache)
        return z["O"], z["H"], z["L"], z["C"], z["V"], z["TR"]
    O, H, L, C, V = (np.full((NC, N), np.nan) for _ in range(5))
    V[:] = 0.0
    TR = np.zeros((NC, N), dtype=bool)
    for k, m in enumerate(UNI):
        with gzip.open(DATA / ("c1_%s.jsonl.gz" % m), "rt") as g:
            for line in g:
                try:
                    page = json.loads(line)
                except ValueError:
                    continue
                for r in page:
                    i = r[0] - T0
                    if 0 <= i < N:
                        O[k, i], H[k, i], L[k, i], C[k, i], V[k, i], TR[k, i] = r[1], r[2], r[3], r[4], r[6], True
        idx = np.where(TR[k], np.arange(N), -1)
        np.maximum.accumulate(idx, out=idx)                 # 직전 거래 분
        ok = idx >= 0
        ff = np.where(ok, C[k, np.maximum(idx, 0)], np.nan)
        C[k] = ff
        prev = np.concatenate(([np.nan], ff[:-1]))
        for A in (O, H, L):
            A[k] = np.where(TR[k], A[k], prev)
        log("%s 거래 분 %d" % (m[4:], int(TR[k].sum())))
    np.savez(cache, O=O, H=H, L=L, C=C, V=V, TR=TR)
    return O, H, L, C, V, TR


O, H, L, C, V, TR = load()
NT = np.empty((NC, N + 1), dtype=np.int64)                  # 그 분 이후 처음 거래가 난 분
for k in range(NC):
    idx = np.where(TR[k], np.arange(N), N)
    NT[k, :N] = np.minimum.accumulate(idx[::-1])[::-1]
    NT[k, N] = N
DAY = (T0 + np.arange(N)) // 1440
HOUR = ((T0 + np.arange(N)) // 60 + 9) % 24                  # 한국 시각
MONTH = np.array([(dt.datetime.fromtimestamp((T0 + i) * 60, tz=UTC).year * 12 + dt.datetime.fromtimestamp((T0 + i) * 60, tz=UTC).month) for i in range(0, N, 1440)]).repeat(1440)


def roll(a, w, fn):
    """r[i] = fn(a[i-w+1 .. i]) (앞 w-1 개는 nan)."""
    out = np.full(a.shape[-1], np.nan)
    out[w - 1:] = fn(swv(a, w), axis=1)
    return out


def prev_median(vals, w):
    """r[i] = vals[i-w .. i-1] 의 중앙값 (앞 w 개는 nan). w 는 짝수."""
    out = np.full(len(vals), np.nan)
    win = sorted(vals[:w].tolist())
    lst = vals.tolist()
    for i in range(w, len(lst)):
        out[i] = (win[w // 2 - 1] + win[w // 2]) / 2.0
        del win[bisect.bisect_left(win, lst[i - w])]
        bisect.insort(win, lst[i])
    return out


def net(g, rt=0.002):
    return (1 + g) * (1 - rt / 2) / (1 + rt / 2) - 1


def outcomes(k, e):
    """종목 k, 신호 봉의 마지막 분 e(배열) → 결과 dict. 진입 = e+1 이후 첫 거래 분의 시가."""
    ei = NT[k, e + 1]
    delay = ei - (e + 1)
    ok = (delay < WAIT) & (ei + HZ[-1] < N)
    ei = np.where(ok, ei, 0)
    entry = O[k, ei]
    out = {"ok": ok, "delay": delay, "ei": ei, "entry": entry, "gap": entry / C[k, e] - 1}
    for h in HZ:
        out["g%d" % h] = C[k, ei + h - 1] / entry - 1
    win = ei[:, None] + np.arange(15)
    Hw, Lw, Cw = H[k][win], L[k][win], C[k][win]
    out["rise"], out["t_rise"] = Hw.max(1) / entry - 1, Hw.argmax(1) + 1
    out["drop"], out["t_drop"] = Lw.min(1) / entry - 1, Lw.argmin(1) + 1
    up, dn = Hw >= entry[:, None] * 1.005, Lw <= entry[:, None] * 0.995
    tu, td = np.where(up.any(1), up.argmax(1), 99), np.where(dn.any(1), dn.argmax(1), 99)
    out["touch"] = np.where((tu == 99) & (td == 99), 0, np.where(tu == td, 3, np.where(tu < td, 1, 2)))   # 0 없음 1 목표 먼저 2 손절 먼저 3 미상
    brk = Cw < L[k][win - 1]                                  # 종가가 직전 완료 봉의 저가 아래
    first = np.where(brk.any(1), brk.argmax(1), 99)
    px = np.where(first < 14, O[k][np.minimum(ei + first + 1, N - 1)], Cw[:, 14])
    out["g_trend"], out["t_trend"] = px / entry - 1, np.where(first < 14, first + 1, 15)
    return out


# ---------------------------------------------------------------- 공통 지표
log("지표 계산")
d = np.abs(np.diff(C, axis=1, prepend=np.nan))
d[~(d > 0)] = np.inf
TICK = np.empty((NC, N))
HI60, LO60, HI30, LO30, HI15, LO15, HI24, LO24 = (np.empty((NC, N)) for _ in range(8))
for k in range(NC):
    TICK[k] = roll(d[k], 1440, np.min) / C[k]
    for w, hi, lo in ((60, HI60, LO60), (30, HI30, LO30), (15, HI15, LO15), (1440, HI24, LO24)):
        hi[k], lo[k] = roll(H[k], w, np.max), roll(L[k], w, np.min)
    log("  %s" % UNI[k][4:])
_last = np.where(TR[0], np.arange(N), -1)
np.maximum.accumulate(_last, out=_last)
_halt = ((np.arange(N) - _last) >= 30).astype(float)
BAD = np.zeros(N, dtype=bool)
BAD[59:] = swv(_halt, 60).max(1) > 0
BAD[:1500] = True
ACT5 = np.array([roll(TR[k].astype(float), 5, np.sum) > 0 for k in range(NC)])
NACT = ACT5[1:].sum(0)
FLAT = {60: (HI60 - LO60) / LO60 <= 0.01, 30: (HI30 - LO30) / LO30 <= 0.01, 15: (HI15 - LO15) / LO15 <= 0.01}


def first_events(mask_idx, cool=COOL):
    """신호가 난 분들(오름차순) → 앞 신호에서 cool 분 지난 것만."""
    out, last = [], -10 ** 9
    for i in mask_idx.tolist():
        if i - last >= cool:
            out.append(i)
            last = i
    return np.array(out, dtype=np.int64)


# ---------------------------------------------------------------- 시나리오 1: 횡보 중 거래 증가
S1, CTRL1, MULT = {}, {}, {}
BASKET = V.sum(0)
for Lb in (1, 3, 5):
    nb = N // Lb
    ends = np.arange(nb) * Lb + Lb - 1                       # 봉의 마지막 분
    bmult = BASKET.reshape(nb, Lb).sum(1)
    with np.errstate(divide='ignore', invalid='ignore'):
        bmult = bmult / prev_median(bmult, 1440 // Lb)
    for k in range(NC):
        val = V[k].reshape(nb, Lb).sum(1)
        med = prev_median(val, 1440 // Lb)
        with np.errstate(divide='ignore', invalid='ignore'):
            mult = np.where(med > 0, val / med, np.nan)
        MULT[(Lb, k)] = (mult, med, bmult)
        prev = np.concatenate(([np.nan], mult[:-1]))
        start = ends - Lb + 1
        in24 = (C[k, ends] >= LO24[k, np.maximum(start - 1, 0)]) & (C[k, ends] <= HI24[k, np.maximum(start - 1, 0)])
        for thr, fw in ((3, 60), (2, 60), (5, 60), (3, 30), (3, 15)):
            cond = (mult >= thr) & ~(prev >= thr) & FLAT[fw][k, ends] & in24 & ~BAD[ends]
            S1.setdefault((Lb, thr, fw, 1), []).append((k, first_events(ends[cond])))
        both = (mult >= 3) & (prev >= 3) & ~(np.concatenate(([np.nan, np.nan], mult[:-2])) >= 3) & FLAT[60][k, ends] & in24 & ~BAD[ends]
        S1.setdefault((Lb, 3, 60, 2), []).append((k, first_events(ends[both])))
        ctrl = (mult >= 0.8) & (mult <= 1.2) & FLAT[60][k, ends] & in24 & ~BAD[ends]
        CTRL1.setdefault(Lb, []).append((k, ends[ctrl]))
    log("시나리오 1 · %d분봉 신호 %d" % (Lb, sum(len(x[1]) for x in S1[(Lb, 3, 60, 1)])))

# ---------------------------------------------------------------- 시나리오 2: 뒤처진 알트
S2, CTRL2, S2X = {}, {}, {}
ALT = np.arange(1, NC)
for Lb, W in ((1, 5), (3, 5), (5, 5), (1, 3), (1, 10)):
    ends = np.arange(N // Lb) * Lb + Lb - 1
    ends = ends[ends >= W + 1440]
    R = C[:, ends] / C[:, ends - W] - 1                       # (종목, 봉)
    upc = (R[ALT] > 0).sum(0)
    quiet = np.abs(R[0]) <= 0.003
    for k in ALT:
        peers = np.delete(R[ALT], k - 1, axis=0)
        n_up = upc - (R[k] > 0)
        medp = np.median(peers, axis=0)
        flat = np.abs(R[k]) <= 0.002
        lead = (n_up >= 12) & (medp >= 0.003)
        cond = lead & flat & quiet & ~BAD[ends]
        prev = np.concatenate(([False], cond[:-1]))
        ev = first_events(ends[cond & ~prev])
        S2.setdefault((Lb, W), []).append((k, ev))
        if (Lb, W) == (1, 5):
            pos = np.searchsorted(ends, ev)
            S2X[k] = {"n_up": n_up[pos], "medp": medp[pos], "rx": R[k][pos], "nact": (NACT - ACT5[k])[ev]}
            CTRL2.setdefault(1, []).append((k, ends[flat & quiet & ~lead & ~BAD[ends]]))
    log("시나리오 2 · %d분봉 · 돌아보기 %d분 신호 %d" % (Lb, W, sum(len(x[1]) for x in S2[(Lb, W)])))


# ---------------------------------------------------------------- 통계
def pct(v, d=2):
    return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else ("%+." + str(d) + "f%%") % (100 * v)


def by_day(vals, minute):
    """같은 분의 신호를 먼저 평균 → 날마다 평균 → 일별 값 배열."""
    if len(vals) == 0:
        return np.array([])
    u, inv = np.unique(minute, return_inverse=True)
    m = np.bincount(inv, weights=vals) / np.bincount(inv)
    du, dinv = np.unique(DAY[u], return_inverse=True)
    return np.bincount(dinv, weights=m) / np.bincount(dinv)


def ci(dv):
    if len(dv) == 0:
        return "-"
    m = dv.mean()
    if len(dv) < 30:
        return "%s (날 %d)" % (pct(m), len(dv))
    h = 1.96 * dv.std(ddof=1) / math.sqrt(len(dv))
    return "%s [%s, %s]" % (pct(m), pct(m - h), pct(m + h))


def gather(sets, extra=None):
    """[(종목, 신호 분 배열)] → 한 묶음의 결과 배열 dict (체결된 것만) + 미체결 수."""
    parts, unfilled, total = [], 0, 0
    for k, e in sets:
        e = e[(e >= 1500) & (e < N - 80)]
        if len(e) == 0:
            continue
        o = outcomes(k, e)
        ok = o.pop("ok")
        total += len(e)
        unfilled += int((~ok).sum())
        o = {a: b[ok] for a, b in o.items()}
        o["k"], o["e"] = np.full(int(ok.sum()), k), e[ok]
        o["tick"] = TICK[k, e[ok]]
        if extra:
            for name, fn in extra.items():
                o[name] = fn(k, e[ok])
        parts.append(o)
    if not parts:
        return None, unfilled, total
    return {a: np.concatenate([p[a] for p in parts]) for a in parts[0]}, unfilled, total


def control_means(ctrl_sets):
    """대조군: (종목, 달, 시간) 칸마다 5·15분 수익(비용 전) 평균과 5분 오름·내림 비율. 칸에 20개 미만이면 (종목, 시간) 으로."""
    cell, coarse = {}, {}
    for k, e in ctrl_sets:
        e = e[(e >= 1500) & (e < N - 80)]
        ei = NT[k, e + 1]
        ok = (ei - (e + 1) < WAIT) & (ei + 60 < N)
        e, ei = e[ok], ei[ok]
        g5, g15 = C[k, ei + 4] / O[k, ei] - 1, C[k, ei + 14] / O[k, ei] - 1
        key = MONTH[e] * 100 + HOUR[e]
        for kk, store in ((key, cell), (HOUR[e], coarse)):
            u, inv = np.unique(kk, return_inverse=True)
            cnt = np.bincount(inv)
            s5, s15 = np.bincount(inv, weights=g5), np.bincount(inv, weights=g15)
            up, dn, ab = np.bincount(inv, weights=(g5 > 0)), np.bincount(inv, weights=(g5 < 0)), np.bincount(inv, weights=np.abs(g5))
            for j, uu in enumerate(u.tolist()):
                store[(k, uu)] = (int(cnt[j]), s5[j] / cnt[j], s15[j] / cnt[j], up[j] / cnt[j], dn[j] / cnt[j], ab[j] / cnt[j])
    return cell, coarse


def control_for(o, cell, coarse):
    c5, c15, cu, cd, ca = (np.full(len(o["e"]), np.nan) for _ in range(5))
    for i, (k, e) in enumerate(zip(o["k"].tolist(), o["e"].tolist())):
        got = cell.get((k, int(MONTH[e]) * 100 + int(HOUR[e])))
        if got is None or got[0] < 20:
            got = coarse.get((k, int(HOUR[e])))
        if got is not None:
            c5[i], c15[i], cu[i], cd[i], ca[i] = got[1], got[2], got[3], got[4], got[5]
    return c5, c15, cu, cd, ca


CELLS = [0]


def summary(o, unfilled, total, ctrl=None):
    """한 묶음 → 표 한 줄의 값들."""
    CELLS[0] += 1
    if o is None or len(o["e"]) == 0:
        return None
    n5 = net(o["g5"])
    dv = by_day(n5, o["e"])
    a, b = o["e"] < CUT, o["e"] >= CUT
    best = np.sort(dv)[:-1].mean() if len(dv) > 1 else float("nan")
    kc = np.bincount(o["k"], minlength=NC)
    row = {"n": len(n5), "total": total, "unfilled": unfilled, "days": len(dv), "coins": int((kc > 0).sum()),
           "net": {h: (net(o["g%d" % h]).mean(), float(np.median(net(o["g%d" % h])))) for h in HZ},
           "win5": float((n5 > 0).mean()), "p10": float(np.percentile(n5, 10)), "day5": ci(dv), "day5_lo": dv.mean() - 1.96 * dv.std(ddof=1) / math.sqrt(len(dv)) if len(dv) >= 30 else float("nan"),
           "day5_m": float(dv.mean()), "day5_40": ci(by_day(net(o["g5"], 0.004), o["e"])), "breakeven": float(by_day(o["g5"], o["e"]).mean()),
           "front": ci(by_day(n5[a], o["e"][a])), "back": ci(by_day(n5[b], o["e"][b])),
           "front_m": float(by_day(n5[a], o["e"][a]).mean()) if a.any() else float("nan"), "back_m": float(by_day(n5[b], o["e"][b]).mean()) if b.any() else float("nan"),
           "ex_best": best, "top_coin": "%s %.0f%%" % (UNI[int(kc.argmax())][4:], 100.0 * kc.max() / len(n5)),
           "rise": float(np.median(o["rise"])), "drop": float(np.median(o["drop"])), "t_rise": float(np.median(o["t_rise"])), "t_drop": float(np.median(o["t_drop"])),
           "touch": [float((o["touch"] == j).mean()) for j in (1, 2, 3, 0)], "trend": ci(by_day(net(o["g_trend"]), o["e"])), "t_trend": float(np.median(o["t_trend"])),
           "delay": float((o["delay"] > 0).mean()), "gap": float(o["gap"].mean()), "big_tick": float((o["tick"] >= BIG_TICK).mean())}
    if ctrl is not None:
        c5, c15, cu, cd, ca = control_for(o, *ctrl)
        row["absmove"] = (float(np.abs(o["g5"]).mean()), float(np.nanmean(ca)))
        okc = ~np.isnan(c5)
        row["vs_ctrl"] = ci(by_day((o["g5"] - c5)[okc], o["e"][okc]))
        row["vs_ctrl_m"] = float(by_day((o["g5"] - c5)[okc], o["e"][okc]).mean()) if okc.any() else float("nan")
        row["updown"] = (float((o["g5"] > 0).mean()), float((o["g5"] < 0).mean()), float(np.nanmean(cu)), float(np.nanmean(cd)))
    return row


def verdict(r):
    if r is None:
        return "신호 없음"
    ok = r["front_m"] > 0 and r["back_m"] > 0 and r["day5_lo"] > 0 and r.get("vs_ctrl_m", 0) > 0 and r["ex_best"] > 0
    return "후보로 남김" if ok else "기준 미달"


def line(label, r):
    if r is None:
        return "| %s | 0 | | | | | | | | | | | |" % label
    return "| %s | %d / %d일 / %d종목 | %d | %s | %s | %s | %s | %s | %s | %.0f%% | %s | %s | %s |" % (
        label, r["n"], r["days"], r["coins"], r["unfilled"], *["%s / %s" % (pct(r["net"][h][0]), pct(r["net"][h][1])) for h in (1, 3, 5, 10, 15)],
        r["day5"], 100 * r["win5"], pct(r["p10"]), r["day5_40"], r.get("vs_ctrl", "-"))


HEAD = ["| 조합 | 신호 / 날 / 종목 | 미체결 | 1분 평균 / 중앙 | 3분 | 5분 | 10분 | 15분 | 5분 날짜별 [95%] | 5분 승률 | 5분 하위 10% | 왕복 0.40%일 때 5분 | 대조군 대비 5분(비용 전) [95%] |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
LNAME = {1: "1분봉", 3: "3분봉", 5: "5분봉"}
R = []

np.savez(OUT / "signals.npz", **{"s1_%d" % k: e for k, e in S1[(1, 3, 60, 1)]}, **{"s2_%d" % k: e for k, e in S2[(1, 5)]})
log("시나리오 1 집계")
DATA_ROWS = []
for k in range(NC):
    live = np.where(TR[k])[0]
    span = slice(live[0], live[-1] + 1)
    row = [UNI[k][4:], "%.0f%%" % (100.0 * TR[k][span].mean())]
    for Lb in (1, 3, 5):
        med = MULT[(Lb, k)][1]
        okm = ~np.isnan(med)
        row.append("%.0f%%" % (100.0 * (med[okm] > 0).mean()))
    tk = TICK[k][span]
    row.append("%.2f%%" % (100 * float(np.nanmedian(tk[np.isfinite(tk)]))))
    row.append("%s" % format(int(np.nanmedian(MULT[(5, k)][1]) / 5), ","))
    DATA_ROWS.append(row)
ctrl1 = {Lb: control_means(CTRL1[Lb]) for Lb in (1, 3, 5)}
extra1 = lambda Lb: {"pos": lambda k, e: (C[k, e] - LO60[k, e]) / np.where(HI60[k, e] > LO60[k, e], HI60[k, e] - LO60[k, e], np.nan),
                     "bar": lambda k, e: C[k, e] / O[k, e - Lb + 1] - 1, "r5": lambda k, e: C[k, e] / C[k, e - 5] - 1,
                     "mult": lambda k, e: MULT[(Lb, k)][0][e // Lb], "med": lambda k, e: MULT[(Lb, k)][1][e // Lb] / Lb, "bmult": lambda k, e: MULT[(Lb, k)][2][e // Lb],
                     "val": lambda k, e: V[k].reshape(N // Lb, Lb).sum(1)[e // Lb], "btc15": lambda k, e: C[0, e] / C[0, e - 15] - 1}
R += ["## 자료 — 종목별로 신호를 계산할 수 있었나", "",
      "| 종목 | 거래가 있던 분 | 24시간 중앙값 > 0 인 때: 1분봉 | 3분봉 | 5분봉 | 호가 한 칸 ÷ 가격(중앙값) | 평소 1분 거래대금(5분봉 중앙값 ÷ 5, 원) | 시나리오 1 기본 칸 신호 | 시나리오 2 기본 칸 신호 |", "|---|---|---|---|---|---|---|---|---|"]
for k in range(NC):
    n1 = sum(len(e) for kk, e in S1[(1, 3, 60, 1)] if kk == k)
    n2 = sum(len(e) for kk, e in S2[(1, 5)] if kk == k)
    R.append("| " + " | ".join(DATA_ROWS[k] + [str(n1), "-" if k == 0 else str(n2)]) + " |")
R += ["", "중앙값이 0 이면(24시간의 절반 넘는 봉에 거래가 없으면) '평소의 3배' 를 계산할 수 없어 신호로 보지 않았다. 점검 구간(BTC 30분 넘게 무거래)과 그 뒤 60분도 뺐다.", ""]
res1 = {}
R += ["## 시나리오 1 — 횡보 중 거래 증가", "", "### 1-1. 조합별 성적", ""] + HEAD
for key in sorted(S1, key=lambda x: (x[3], x[2] != 60, x[1] != 3, x[0], x[1], x[2])):
    Lb, thr, fw, run = key
    o, unf, tot = gather(S1[key], extra1(Lb))
    r = summary(o, unf, tot, ctrl1[Lb])
    res1[key] = (o, r)
    label = "%s · %d배 · %d분 횡보%s%s" % (LNAME[Lb], thr, fw, " · 두 봉 연속" if run == 2 else "", " **(기본 칸)**" if key == (1, 3, 60, 1) else "")
    R.append(line(label, r))
o1, r1 = res1[(1, 3, 60, 1)]
R += ["", "기본 칸 판정: **%s** — 앞 구간 %s, 뒤 구간 %s, 가장 좋은 하루를 빼면 %s, 본전이 되는 왕복 비용 %s." % (
    verdict(r1), r1["front"], r1["back"], pct(r1["ex_best"]), pct(r1["breakeven"])), ""]


def split_table(title, o, splits, ctrl):
    out = ["### " + title, "", "| 조건 | 구간 | 신호 / 날 | 5분 날짜별 [95%] | 5분 승률 | 15분 평균 | 대조군 대비 5분(비용 전) | 15분 안 최고 상승 / 최대 하락(중앙) |", "|---|---|---|---|---|---|---|---|"]
    for name, parts in splits:
        for label, mask in parts:
            sub = {a: b[mask] for a, b in o.items()}
            r = summary(sub, 0, int(mask.sum()), ctrl)
            if r is None:
                out.append("| %s | %s | 0 | | | | | |" % (name, label))
                continue
            out.append("| %s | %s | %d / %d | %s | %.0f%% | %s | %s | %s / %s |" % (name, label, r["n"], r["days"], r["day5"], 100 * r["win5"], pct(r["net"][15][0]),
                                                                        r.get("vs_ctrl", "-"), pct(r["rise"]), pct(r["drop"])))
    return out + [""]


hr = HOUR[o1["e"]]
R += split_table("1-2. 같은 거래 증가라도 무엇이 달랐나 (기본 칸: 1분봉 · 3배 · 60분 횡보)", o1, [
    ("호가 한 칸", [("작은 종목(가격의 0.15% 미만)", o1["tick"] < BIG_TICK), ("큰 종목(0.15% 이상)", o1["tick"] >= BIG_TICK)]),
    ("횡보 범위 안 종가 위치", [("하단 1/3", o1["pos"] < 1 / 3), ("중단", (o1["pos"] >= 1 / 3) & (o1["pos"] < 2 / 3)), ("상단 1/3", o1["pos"] >= 2 / 3)]),
    ("신호 봉에서 이미 오른 폭", [("내림 또는 제자리(≤ 0)", o1["bar"] <= 0), ("0 ~ +0.3%", (o1["bar"] > 0) & (o1["bar"] <= 0.003)), ("+0.3% 초과", o1["bar"] > 0.003)]),
    ("신호 봉까지 5분 등락", [("내림", o1["r5"] < 0), ("제자리", o1["r5"] == 0), ("오름", o1["r5"] > 0)]),
    ("거래대금 배수", [("3~5배", o1["mult"] < 5), ("5~10배", (o1["mult"] >= 5) & (o1["mult"] < 10)), ("10~20배", (o1["mult"] >= 10) & (o1["mult"] < 20)), ("20~50배", (o1["mult"] >= 20) & (o1["mult"] < 50)), ("50배 이상", o1["mult"] >= 50)]),
    ("시장 전체 거래대금", [("바스켓도 2배 이상", o1["bmult"] >= 2), ("바스켓은 2배 미만(이 종목만)", o1["bmult"] < 2)]),
    ("신호 봉 거래대금", [("1천만 원 미만", o1["val"] < 1e7), ("1천만~1억 원", (o1["val"] >= 1e7) & (o1["val"] < 1e8)), ("1억 원 이상", o1["val"] >= 1e8)]),
    ("평소 1분 거래대금(중앙값)", [("100만 원 미만(얇은 종목)", o1["med"] < 1e6), ("100만 원 이상", o1["med"] >= 1e6)]),
    ("BTC 직전 15분", [("내림", o1["btc15"] < 0), ("오름 또는 제자리", o1["btc15"] >= 0)]),
    ("시간대(한국)", [("09시대(일봉 바뀌는 시각)", hr == 9), ("10~17시", (hr >= 10) & (hr < 18)), ("18~24시", hr >= 18), ("00~08시", hr < 9)]),
    ("종목", [(UNI[k][4:], o1["k"] == k) for k in range(NC) if (o1["k"] == k).sum() >= 30]),
], ctrl1[1])
ud = r1["updown"]
R += ["### 1-3. 기본 칸의 경로와 조건부 청산", "",
      "| 항목 | 값 |", "|---|---|",
      "| 5분 뒤 오름 / 내림 (비용 전, 실제 진입가 기준) — 신호 | %.0f%% / %.0f%% |" % (100 * ud[0], 100 * ud[1]), "| 〃 — 대조군(같은 종목·달·시간, 거래대금 0.8~1.2배) | %.0f%% / %.0f%% |" % (100 * ud[2], 100 * ud[3]),
      "| 5분 동안 움직인 크기(방향 무시, 평균) — 신호 / 대조군 | %s / %s |" % (pct(r1["absmove"][0]).lstrip("+"), pct(r1["absmove"][1]).lstrip("+")),
      "| 신호 봉 종가 → 실제 진입가(다음 거래 시가) 사이에 이미 움직인 폭 (평균) | %s |" % pct(r1["gap"], 3),
      "| 15분 안 최고 상승 / 최대 하락 (중앙값, 걸린 분) | %s (%g분) / %s (%g분) |" % (pct(r1["rise"]), r1["t_rise"], pct(r1["drop"]), r1["t_drop"]),
      "| +0.5%% 먼저 / −0.5%% 먼저 / 같은 분(순서 미상) / 둘 다 안 닿음 | %.0f%% / %.0f%% / %.0f%% / %.0f%% |" % tuple(100 * x for x in r1["touch"]),
      "| 추세 청산(종가가 직전 봉 저가 아래로 마감하면 청산, 최대 15분) 날짜별 | %s (보유 중앙값 %g분) |" % (r1["trend"], r1["t_trend"]),
      "| 고정 청산 3 → 5분, 5 → 10분의 추가 이득 (평균) | %s, %s |" % (pct(r1["net"][5][0] - r1["net"][3][0]), pct(r1["net"][10][0] - r1["net"][5][0])),
      "| 참고: 30분 / 60분 평균 | %s / %s |" % (pct(r1["net"][30][0]), pct(r1["net"][60][0])),
      "| 진입이 한 분 넘게 늦은 신호 / 미체결 | %.0f%% / %d건 |" % (100 * r1["delay"], r1["unfilled"]),
      "| 가장 몰린 종목 / 호가 큰 종목의 비중 | %s / %.0f%% |" % (r1["top_coin"], 100 * r1["big_tick"]), ""]

log("시나리오 2 집계")
ctrl2 = control_means(CTRL2[1])


def s2_extra(k, e):
    return np.zeros(len(e))


res2 = {}
R += ["## 시나리오 2 — 다른 알트가 먼저 오르고 한 종목이 뒤처질 때", "", "### 2-1. 조합별 성적", ""] + HEAD
for key in ((1, 5), (3, 5), (5, 5), (1, 3), (1, 10)):
    Lb, W = key
    ex = {"val5": lambda k, e: roll(V[k], 5, np.sum)[e], "med1": lambda k, e: MULT[(1, k)][1][e], "act": lambda k, e: roll(TR[k].astype(float), 5, np.sum)[e],
          "peer5": lambda k, e: np.median(np.delete(C[1:, e + 5] / C[1:, e] - 1, k - 1, axis=0), axis=0), "self5": lambda k, e: C[k, e + 5] / C[k, e] - 1,
          "btc5": lambda k, e: C[0, e + 5] / C[0, e] - 1}
    o, unf, tot = gather(S2[key], ex)
    r = summary(o, unf, tot, ctrl2 if Lb == 1 else None)
    res2[key] = (o, r)
    R.append(line("%s · 돌아보기 %d분%s" % (LNAME[Lb], W, " **(기본 칸)**" if key == (1, 5) else ""), r))
o2, r2 = res2[(1, 5)]
if o2 is not None:
    # 신호 때의 값(오른 종목 수 등)을 결과 배열 순서에 맞춘다
    nu, mp, na = (np.empty(len(o2["e"])) for _ in range(3))
    for k, ev in S2[(1, 5)]:
        ev2 = ev[(ev >= 1500) & (ev < N - 80)]
        sel = o2["k"] == k
        pos = np.searchsorted(ev, o2["e"][sel])
        nu[sel], mp[sel], na[sel] = S2X[k]["n_up"][pos], S2X[k]["medp"][pos], S2X[k]["nact"][pos]
    R += ["", "기본 칸 판정: **%s** — 앞 구간 %s, 뒤 구간 %s, 가장 좋은 하루를 빼면 %s, 본전이 되는 왕복 비용 %s." % (
        verdict(r2), r2["front"], r2["back"], pct(r2["ex_best"]), pct(r2["breakeven"])), ""]
    hr = HOUR[o2["e"]]
    s1min = {k: set() for k in range(NC)}
    for k, ev in S1[(1, 3, 60, 1)]:
        s1min[k] = ev
    near = np.array([bool(len(s1min[k])) and bool(np.any((s1min[k] <= e) & (s1min[k] >= e - 5))) for k, e in zip(o2["k"].tolist(), o2["e"].tolist())])
    R += split_table("2-2. 따라 오른 것인지 가려낼 조건 (기본 칸: 1분봉 · 돌아보기 5분)", o2, [
        ("호가 한 칸", [("작은 종목(0.15% 미만)", o2["tick"] < BIG_TICK), ("큰 종목(0.15% 이상)", o2["tick"] >= BIG_TICK)]),
        ("얼마나 널리 올랐나", [("12~14개 상승", nu < 15), ("15개 이상 상승", nu >= 15)]),
        ("비교군 18개 중 직전 5분에 거래가 있던 종목", [("15개 이상", na >= 15), ("14개 이하", na < 15)]),
        ("비교군 수익 중앙값", [("+0.3~0.5%", mp < 0.005), ("+0.5% 이상", mp >= 0.005)]),
        ("대상의 거래", [("직전 5분 중 3분 이상 거래(활발)", o2["act"] >= 3), ("2분 이하(거래 드묾)", o2["act"] < 3)]),
        ("대상의 거래대금(5분, 평소 대비)", [("평소 5분치 이상", o2["val5"] >= 5 * o2["med1"]), ("평소 미만", o2["val5"] < 5 * o2["med1"])]),
        ("시나리오 1 과 겹침", [("직전 5분 안에 시나리오 1 신호도 있었다", near), ("시나리오 2 만", ~near)]),
        ("시간대(한국)", [("09시대", hr == 9), ("10~17시", (hr >= 10) & (hr < 18)), ("18~24시", hr >= 18), ("00~08시", hr < 9)]),
        ("종목", [(UNI[k][4:], o2["k"] == k) for k in range(1, NC) if (o2["k"] == k).sum() >= 30]),
    ], ctrl2)
    t_up, p_dn = o2["self5"] >= 0.001, o2["peer5"] <= -0.001
    ud = r2["updown"]
    R += ["### 2-3. 무엇이 움직였나 (비용 전)", "", "| 항목 | 값 |", "|---|---|",
          "| 신호 시각 종가에서 잰 5분 뒤: 대상 평균 / 비교군 중앙값 평균 / BTC 평균 | %s / %s / %s |" % (pct(o2["self5"].mean()), pct(o2["peer5"].mean()), pct(o2["btc5"].mean())),
          "| 그 가운데 신호 시각 종가 → 실제 진입가(다음 거래 시가) 사이에 이미 오른 폭 (평균) | %s (호가 작은 종목 %s, 큰 종목 %s) |" % (pct(o2["gap"].mean()), pct(o2["gap"][o2["tick"] < BIG_TICK].mean()), pct(o2["gap"][o2["tick"] >= BIG_TICK].mean())),
          "| 실제 진입가에서 잰 5분 수익 (비용 전, 평균) | %s |" % pct(o2["g5"].mean()),
          "| 5분 동안 움직인 크기(방향 무시, 평균) — 신호 / 대조군 | %s / %s |" % (pct(r2["absmove"][0]).lstrip("+"), pct(r2["absmove"][1]).lstrip("+")),
          "| 〃 대상이 올라 격차를 좁힘 (대상 +0.1%% 이상, 비교군은 안 내림) | %.0f%% |" % (100 * (t_up & ~p_dn).mean()),
          "| 〃 비교군이 되돌아옴 (비교군 −0.1%% 이하, 대상은 안 오름) | %.0f%% |" % (100 * (~t_up & p_dn).mean()),
          "| 〃 양쪽 다 움직임 | %.0f%% |" % (100 * (t_up & p_dn).mean()), "| 〃 격차 유지 또는 변화 없음 | %.0f%% |" % (100 * (~t_up & ~p_dn).mean()),
          "| 실제 진입가에서 잰 5분 뒤 오름 / 내림 — 신호 | %.0f%% / %.0f%% |" % (100 * ud[0], 100 * ud[1]), "| 〃 — 대조군(대상은 제자리·BTC 조용, 알트 상승은 안 퍼짐) | %.0f%% / %.0f%% |" % (100 * ud[2], 100 * ud[3]),
          "| 실제 진입가에서 잰 15분 안 최고 상승 / 최대 하락 (중앙값, 걸린 분) | %s (%g분) / %s (%g분) |" % (pct(r2["rise"]), r2["t_rise"], pct(r2["drop"]), r2["t_drop"]),
          "| +0.5%% 먼저 / −0.5%% 먼저 / 순서 미상 / 둘 다 안 닿음 | %.0f%% / %.0f%% / %.0f%% / %.0f%% |" % tuple(100 * x for x in r2["touch"]),
          "| 추세 청산 날짜별 | %s (보유 중앙값 %g분) |" % (r2["trend"], r2["t_trend"]),
          "| 참고: 30분 / 60분 평균 | %s / %s |" % (pct(r2["net"][30][0]), pct(r2["net"][60][0])),
          "| 가장 몰린 종목 / 호가 큰 종목의 비중 | %s / %.0f%% |" % (r2["top_coin"], 100 * r2["big_tick"]), ""]
else:
    R += ["", "기본 칸에 신호가 없다.", ""]

# ---------------------------------------------------------------- 포착과 미포착
log("포착·미포착")
cover = np.zeros((NC, N), dtype=bool)
first_by = {}
for Lb in (1, 3, 5):
    for k, ev in S1[(Lb, 3, 60, 1)]:
        for e in ev.tolist():
            cover[k, e + 1:e + 1 + COOL] = True
            first_by.setdefault(k, []).append((e, Lb))
    for k, ev in S2[(Lb, 5)]:
        for e in ev.tolist():
            cover[k, e + 1:e + 1 + COOL] = True
opp = cap = allmin = covmin = 0
for k in range(NC):
    i = np.arange(1500, N - 80)
    ei = NT[k, i + 1]
    ok = ei == i + 1
    g5 = net(C[k, ei[ok] + 4] / O[k, ei[ok]] - 1)
    hit = g5 >= 0.005
    opp += int(hit.sum())
    allmin += int(ok.sum())
    covmin += int(cover[k, i[ok] + 1].sum())
    cap += int(cover[k, i[ok] + 1][hit].sum())
only = {1: 0, 3: 0, 5: 0}
multi = 0
for k, lst in first_by.items():
    lst.sort()
    j = 0
    while j < len(lst):
        grp = [lst[j]]
        while j + 1 < len(lst) and lst[j + 1][0] - grp[0][0] < COOL:
            j += 1
            grp.append(lst[j])
        kinds = {x[1] for x in grp}
        if len(kinds) == 1:
            only[grp[0][1]] += 1
        else:
            multi += 1
        j += 1
R += ["## 포착과 미포착", "",
      "- 시나리오 1 의 1·3·5분봉 신호를 종목별로 15분 안에서 묶으면: 1분봉에서만 %d건, 3분봉에서만 %d건, 5분봉에서만 %d건, 둘 이상에서 겹친 사건 %d건." % (only[1], only[3], only[5], multi),
      "- 사후 기회 기준(다음 분 시가에 사서 5분 뒤 왕복 0.20%% 를 빼고 +0.5%% 이상): 전체 %s번의 분 가운데, 두 시나리오의 어느 신호(1·3·5분봉) 뒤 15분 안에도 들지 않은 것이 %.1f%% 다. "
      "거꾸로, 신호 뒤 15분 안에 든 분은 전체 거래 분의 %.1f%% 인데 기회는 %.1f%% 만 그 안에 있었다 — 아무 분이나 고른 것보다 기회가 드물다(횡보 조건이 조용한 때만 고르기 때문이다). 이 표식은 진입에 쓰지 않았다." % (format(opp, ","), 100.0 * (1 - cap / max(1, opp)), 100.0 * covmin / max(1, allmin), 100.0 * cap / max(1, opp)), "",
      "## 본 칸의 수", "", "요약표와 조건표에서 성적을 낸 줄은 모두 %d개다(한 줄에 청산 시점이 여러 칸 있다). 이만큼 보면 우연히 좋아 보이는 칸이 나온다 — 판단은 미리 정한 기본 칸 둘로만 했다." % CELLS[0], ""]
(OUT / "report_body.md").write_text("\n".join(R), encoding="utf-8")
log("끝 — 칸 %d개" % CELLS[0])
