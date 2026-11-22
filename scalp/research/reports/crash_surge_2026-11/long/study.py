# -*- coding: utf-8 -*-
"""긴 기간(2017~) 15분봉으로 '이긴 신호의 공통점' 찾기 — 관찰·선별용(돈을 넣는 근거 아님).
가족 A: 알트 15분 −3% 급락 뒤 매수, B: 알트 15분 +3% 급등 뒤 매수, C: BTC 1시간 −1.821% 첫 충족 뒤 BTC 매수.
진입 = 신호 다음 15분봉 시가, 성적 = 60분 뒤 종가(비용 뒤). 지표·봉 모양은 신호 봉 마감 때 알 수 있는 값만 쓴다.
구간은 2023–24 의 3분위로 정하고, 2025–26 과 2022년 이전(이번에 처음 보는 자료)에서 같은지 본다.
사용: study.py DATA(c15_*.jsonl.gz) REPORT.md"""
import bisect
import datetime as dt
import gzip
import json
import math
import pickle
import statistics as st
import sys
from pathlib import Path

DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2])
UNI = ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-WAVES", "KRW-AXS", "KRW-ETC", "KRW-CHZ", "KRW-SAND", "KRW-IQ", "KRW-APT",
       "KRW-ADA", "KRW-GMT", "KRW-KAVA", "KRW-POL", "KRW-TRX", "KRW-CELO", "KRW-ARK", "KRW-ALGO")
BTC, ETH = "KRW-BTC", "KRW-ETH"
COST = {BTC: 0.0007, ETH: 0.0007}
TH_BTC, MOVE, COOL, VOLB, EXTRA = -0.01821, 0.03, 4, 96, 0.003
HZ = (1, 2, 4, 16)                                           # 15분, 30분, 60분, 4시간
PERIODS = (("~2022", "0000", "2023-01-01"), ("2023–24", "2023-01-01", "2025-01-01"), ("2025–26", "2025-01-01", "9999"))
BASE = 1                                                     # 구간을 정하는 기간 = 2023–24
UTC = dt.timezone.utc
T975 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101,
        2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)


def load(m):
    rows = {}
    try:
        with gzip.open(DATA / ("c15_%s.jsonl.gz" % m), "rt") as g:
            for line in g:
                try:
                    page = json.loads(line)
                except ValueError:
                    continue
                for r in page:
                    rows[r[0] // 15] = r
    except (EOFError, OSError):
        pass
    qs = sorted(rows)
    col = lambda k: [float(rows[q][k]) for q in qs]
    return {"q": qs, "o": col(1), "h": col(2), "l": col(3), "c": col(4), "v": col(6), "i": {q: i for i, q in enumerate(qs)}}


P = {m: load(m) for m in UNI}
LAST = min(P[m]["q"][-1] for m in UNI if P[m]["q"])
print("15분봉: " + ", ".join("%s %d(%s~)" % (m[4:], len(P[m]["q"]), dt.datetime.fromtimestamp(P[m]["q"][0] * 900, tz=UTC).strftime("%Y-%m") if P[m]["q"] else "-")
                           for m in UNI), flush=True)


def net(m, r):
    c = COST.get(m, 0.001)
    return (1 + r) * (1 - c) / (1 + c) - 1


def close_at(B, q):
    i = bisect.bisect_right(B["q"], q) - 1
    return B["c"][i] if i >= 0 else None


def fwd(m, q):
    """신호 봉 q 다음 봉 시가에 사서 k봉 뒤 종가(거래 없는 봉은 직전 종가) → {k: 비용 뒤 수익}. 다음 봉이 없으면 None."""
    B = P[m]
    i = B["i"].get(q + 1)
    if i is None or q + HZ[-1] > LAST or B["o"][i] <= 0:
        return None
    out = {}
    for k in HZ:
        j = bisect.bisect_right(B["q"], q + k) - 1
        out[k] = net(m, B["c"][j] / B["o"][i] - 1)
    return out


def ret(B, i, k):
    p = close_at(B, B["q"][i] - k)
    return B["c"][i] / p - 1 if p else None


def feats(m, i):
    """신호 봉 i 마감 때 알 수 있는 지표와 봉 모양."""
    B = P[m]
    o, h, l, c, v, q = B["o"], B["h"], B["l"], B["c"], B["v"], B["q"][i]
    rets = [c[j] / c[j - 1] - 1 for j in range(i - VOLB + 1, i) if c[j - 1] > 0]
    vol24 = st.pstdev(rets) if len(rets) > 10 else None
    ref = st.median(v[i - VOLB:i])
    chg = [c[j] - c[j - 1] for j in range(i - 13, i + 1)]
    up, dn = sum(x for x in chg if x > 0), -sum(x for x in chg if x < 0)
    streak = 0
    while streak < 20 and c[i - streak] < o[i - streak]:
        streak += 1
    up_streak = 0
    while up_streak < 20 and c[i - up_streak] > o[i - up_streak]:
        up_streak += 1
    r15 = c[i] / c[i - 1] - 1
    hour = (q * 15 // 60 + 9) % 24                           # 한국 시각
    wd = dt.datetime.fromtimestamp(q * 900 + 9 * 3600, tz=UTC).weekday()
    bi = P[BTC]["i"].get(q)
    f = {"신호 봉 15분 수익": r15, "직전 봉 15분 수익": c[i - 1] / c[i - 2] - 1 if c[i - 2] > 0 else None,
         "1시간 수익": ret(B, i, 4), "4시간 수익": ret(B, i, 16), "24시간 수익": ret(B, i, 96),
         "24시간 고점 대비": c[i] / max(h[i - VOLB:i + 1]) - 1, "24시간 저점 대비": c[i] / min(l[i - VOLB:i + 1]) - 1,
         "연속 음봉 수": float(streak), "연속 양봉 수": float(up_streak), "RSI(14)": 100.0 * up / (up + dn) if up + dn > 0 else None,
         "24시간 변동성(15분 표준편차)": vol24, "신호 봉 크기 ÷ 변동성": r15 / vol24 if vol24 else None,
         "거래대금 배수": v[i] / ref if ref > 0 else None, "평소 거래대금(로그)": math.log10(ref) if ref > 0 else None,
         "봉 안 종가 위치(0=저가,1=고가)": (c[i] - l[i]) / (h[i] - l[i]) if h[i] > l[i] else None,
         "저가에서 되올린 폭": c[i] / l[i] - 1 if l[i] > 0 else None, "고가에서 밀린 폭": c[i] / h[i] - 1 if h[i] > 0 else None,
         "신호 봉 고저 폭": h[i] / l[i] - 1 if l[i] > 0 else None,
         "같은 봉 −1.5% 이하 종목 수": float(DOWN.get(q, 0)), "같은 봉 +1.5% 이상 종목 수": float(UPC.get(q, 0)),
         "시간대(한국)": "%02d~%02d시" % (hour // 6 * 6, hour // 6 * 6 + 6), "요일": "주말" if wd >= 5 else "평일"}
    if bi is not None and bi >= VOLB:
        bb = P[BTC]
        f.update({"BTC 15분 수익": bb["c"][bi] / bb["c"][bi - 1] - 1, "BTC 1시간 수익": ret(bb, bi, 4), "BTC 24시간 수익": ret(bb, bi, 96)})
        f["BTC 대비 15분 차이"] = r15 - f["BTC 15분 수익"]
    return f


# ---------------------------------------------------------------- 신호
DOWN, UPC, raw = {}, {}, {"A": [], "B": [], "C": []}
for m in UNI:
    B = P[m]
    q, c = B["q"], B["c"]
    last = {"A": -99, "B": -99, "C": -99}
    for i in range(VOLB, len(q)):
        if q[i - 1] != q[i] - 1 or c[i - 1] <= 0:
            continue
        r = c[i] / c[i - 1] - 1
        if r <= -0.015:
            DOWN[q[i]] = DOWN.get(q[i], 0) + 1
        if r >= 0.015:
            UPC[q[i]] = UPC.get(q[i], 0) + 1
        if m == BTC:
            r1, p1 = ret(B, i, 4), ret(B, i - 1, 4)
            if r1 is not None and r1 <= TH_BTC and (p1 is None or p1 > TH_BTC) and q[i] - last["C"] >= COOL:
                last["C"] = q[i]
                raw["C"].append((m, i))
            continue
        for fam, hit in (("A", r <= -MOVE), ("B", r >= MOVE)):
            if hit and q[i] - last[fam] >= COOL:
                last[fam] = q[i]
                raw[fam].append((m, i))
CRASH_Q = {}
for m, i in raw["A"]:
    CRASH_Q[P[m]["q"][i]] = CRASH_Q.get(P[m]["q"][i], 0) + 1

SIG = {}
for fam, items in raw.items():
    out = []
    for m, i in items:
        q = P[m]["q"][i]
        o = fwd(m, q)
        if o is None:
            continue
        s = {"m": m, "q": q, "day": dt.datetime.fromtimestamp((q + 1) * 900, tz=UTC).strftime("%Y-%m-%d"), "net": o, "f": feats(m, i),
             "btc": fwd(BTC, q) if m != BTC else o, "eth": fwd(ETH, q)}
        if fam == "C":
            s["f"]["앞 1시간 알트 급락 신호 수"] = float(sum(CRASH_Q.get(q - d, 0) for d in range(4)))
        out.append(s)
    SIG[fam] = out
    print("가족 %s 신호 %d" % (fam, len(out)), flush=True)
pickle.dump(SIG, open(str(OUT) + ".pkl", "wb"))


# ---------------------------------------------------------------- 통계
def pct(v, d=2):
    return "-" if v is None else ("%+." + str(d) + "f%%") % (100 * v)


def mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def ci95(vals):
    vals = [x for x in vals if x is not None]
    if not vals:
        return None, None, None
    m = mean(vals)
    if len(vals) < 2:
        return m, None, None
    h = (T975[len(vals) - 2] if len(vals) - 1 <= 30 else 1.96) * st.stdev(vals) / math.sqrt(len(vals))
    return m, m - h, m + h


def by_day(sigs, val):
    ev = {}
    for s in sigs:
        v = val(s)
        if v is not None:
            ev.setdefault((s["day"], s["q"]), []).append(v)
    days = {}
    for (d, _q), v in ev.items():
        days.setdefault(d, []).append(mean(v))
    return [mean(v) for v in days.values()]


def per(sigs, k):
    return [s for s in sigs if PERIODS[k][1] <= s["day"] < PERIODS[k][2]]


N60 = lambda s: s["net"][4]


def stat(sigs):
    """→ (신호, 일수, 승률, 일별 평균, 하한, 상한)"""
    d = by_day(sigs, N60)
    m, lo, hi = ci95(d)
    return len(sigs), len(d), mean([1.0 if N60(s) > 0 else 0.0 for s in sigs]), m, lo, hi


def cell(x):
    n, days, win, m, lo, hi = x
    if not n:
        return "-"
    return "%d/%d일 · %.0f%% · %s [%s, %s]" % (n, days, 100 * win, pct(m), pct(lo), pct(hi))


FAM = {"A": "A. 알트 15분 −3% 급락 뒤 매수", "B": "B. 알트 15분 +3% 급등 뒤 매수", "C": "C. BTC 1시간 −1.821% 첫 충족 뒤 BTC 매수"}
NEED = {"A": EXTRA, "B": EXTRA, "C": 0.0}                    # 통과 기준에 넣는 추가 체결 손실
L = ["# 긴 기간 검토 — 이긴 신호의 공통점 (15분봉, 고정 20종목, 2017~2026-11)", "",
     "## 미리 적은 기준 (결과를 보기 전에 적음)", "",
     "1. 지표마다 2023–24 신호의 3분위로 구간을 정한다(범주형은 그대로). 그 구간을 2025–26 과 2022년 이전에 그대로 적용한다. 2022년 이전 자료는 이번에 처음 본다.",
     "2. **통과** = 세 기간 모두 급락(급등) 일수 10일 이상이고, 세 기간 모두 60분 비용 뒤 일별 평균이 기준선 위(알트는 추가 체결 손실 0.3%p, BTC 는 0).",
     "3. **강한 통과** = 통과 + 세 기간 중 둘 이상에서 95% 구간 하한이 0 위.",
     "4. 구간을 수십 개 보면 우연히 통과하는 것이 나온다. 통과한 것은 '앞으로 추적할 후보'이지 증명이 아니다. 몇 개를 봤고 몇 개가 통과했는지 같이 적는다.",
     "5. 조합은 2023–24 성적만으로 기계적으로 고른다(구간 간 차이가 가장 큰 지표 3개의 가장 좋은 구간끼리). 다른 두 기간을 보고 고르지 않는다.", "",
     "표의 칸 = 신호 수/일수 · 60분 승률 · 60분 비용 뒤 일별 평균 [95% 구간]. 진입 = 신호 다음 15분봉 시가. 비용 = 편도 BTC·ETH 0.07%, 그 밖 0.10%. "
     "※ 지금 상장된 20종목만 있어 상장폐지 종목이 빠진 낙관적 자료다. 2022년 이전은 그때 상장돼 있던 종목만 들어 있다.", ""]

# 0. 기간별 기본 성적
L += ["## 0. 기간별 기본 성적", "", "| 가족 | 기간 | 신호/일수 · 승률 · 60분 [95%] | 15분 | 30분 | 4시간 | 같은 시각 BTC 60분 | 신호 종목 − BTC [95%] |", "|---|---|---|---|---|---|---|---|"]
for fam in "ABC":
    for k in range(3):
        part = per(SIG[fam], k)
        if not part:
            continue
        h = [pct(mean(by_day(part, lambda s, kk=kk: s["net"][kk]))) for kk in (1, 2, 16)]
        b = by_day(part, lambda s: s["btc"][4] if s.get("btc") else None)
        d = ci95(by_day(part, lambda s: s["net"][4] - s["btc"][4] if s.get("btc") else None))
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (FAM[fam], PERIODS[k][0], cell(stat(part)), h[0], h[1], h[2], pct(mean(b)),
                                                         "-" if fam == "C" else "%s [%s, %s]" % (pct(d[0]), pct(d[1]), pct(d[2]))))
L.append("")

# 1. 앞서 찾은 후보의 새 자료 확인: BTC 와 알트가 같이 급락한 순간 BTC·ETH 매수
L += ["## 1. 앞서 찾은 후보 확인 — BTC 1시간 −1.821% 이하이면서 알트가 15분 −3% 급락한 순간에 BTC·ETH 를 샀다면", "",
      "2023–26 에서 찾은 후보다. 2022년 이전은 그 뒤 처음 보는 자료라 확인 가치가 있다. 사건(같은 15분봉)마다 한 번 → 날 순으로 묶음.", "",
      "| 기간 | 급락 일수 (사건) | BTC 60분 [95%] | ETH 60분 [95%] | 그 순간 알트 60분 | BTC 4시간 |", "|---|---|---|---|---|---|"]
co = [s for s in SIG["A"] if s["f"].get("BTC 1시간 수익") is not None and s["f"]["BTC 1시간 수익"] <= TH_BTC and s.get("btc") and s.get("eth")]
for k in range(3):
    part = per(co, k)
    if not part:
        continue
    b, e = ci95(by_day(part, lambda s: s["btc"][4])), ci95(by_day(part, lambda s: s["eth"][4]))
    L.append("| %s | %d (%d) | %s [%s, %s] | %s [%s, %s] | %s | %s |" % (
        PERIODS[k][0], len(by_day(part, N60)), len({s["q"] for s in part}), pct(b[0]), pct(b[1]), pct(b[2]), pct(e[0]), pct(e[1]), pct(e[2]),
        pct(mean(by_day(part, N60))), pct(mean(by_day(part, lambda s: s["btc"][16])))))
L.append("")


# 2. 지표별 구간 성적
def bins(fam, name):
    """→ [(구간 이름, 고르는 함수)]. 숫자 지표는 2023–24 의 3분위."""
    base = [s["f"].get(name) for s in per(SIG[fam], BASE)]
    base = [x for x in base if x is not None]
    if not base:
        return []
    if isinstance(base[0], str):
        return [(v, lambda s, v=v: s["f"].get(name) == v) for v in sorted(set(base))]
    xs = sorted(base)
    a, b = xs[len(xs) // 3], xs[2 * len(xs) // 3]
    if a == b:                                               # 값이 몰려 있는 지표(개수 등) — 그 값 이하 / 초과 둘로
        return [("%s 이하" % fmt(name, a), lambda s: s["f"].get(name) is not None and s["f"][name] <= a),
                ("%s 초과" % fmt(name, a), lambda s: s["f"].get(name) is not None and s["f"][name] > a)]
    return [("낮음 (%s 이하)" % fmt(name, a), lambda s: s["f"].get(name) is not None and s["f"][name] <= a),
            ("중간", lambda s: s["f"].get(name) is not None and a < s["f"][name] <= b),
            ("높음 (%s 초과)" % fmt(name, b), lambda s: s["f"].get(name) is not None and s["f"][name] > b)]


RAWNUM = ("연속 음봉 수", "연속 양봉 수", "RSI(14)", "신호 봉 크기 ÷ 변동성", "거래대금 배수", "평소 거래대금(로그)", "봉 안 종가 위치(0=저가,1=고가)",
          "같은 봉 −1.5% 이하 종목 수", "같은 봉 +1.5% 이상 종목 수", "앞 1시간 알트 급락 신호 수")


def fmt(name, v):
    return ("%.2f" % v) if name in RAWNUM else pct(v)


passed, tested, spread = [], 0, {}
for fam in "ABC":
    names = []
    for s in SIG[fam]:
        for n in s["f"]:
            if n not in names:
                names.append(n)
    L += ["## 2-%s. 지표별 구간 성적 — %s" % (fam, FAM[fam]), "", "| 지표 | 구간 | ~2022 | 2023–24 | 2025–26 | 판정 |", "|---|---|---|---|---|---|"]
    for name in names:
        rows = []
        for label, pick in bins(fam, name):
            sel = [s for s in SIG[fam] if pick(s)]
            sts = [stat(per(sel, k)) for k in range(3)]
            tested += 1
            ok = all(x[1] >= 10 and x[3] is not None and x[3] > NEED[fam] for x in sts)
            strong = ok and sum(1 for x in sts if x[4] is not None and x[4] > 0) >= 2
            verdict = "**강한 통과**" if strong else ("통과" if ok else "")
            if ok:
                passed.append((fam, name, label, sts, strong))
            rows.append((label, sts, verdict, pick))
            L.append("| %s | %s | %s | %s | %s | %s |" % (name, label, cell(sts[0]), cell(sts[1]), cell(sts[2]), verdict))
        base_m = [(r[1][BASE][3], r) for r in rows if r[1][BASE][3] is not None and r[1][BASE][1] >= 10]
        if len(base_m) >= 2 and not isinstance(next(iter([s["f"].get(name) for s in SIG[fam] if s["f"].get(name) is not None]), 0.0), str):
            best, worst = max(base_m, key=lambda x: x[0]), min(base_m, key=lambda x: x[0])
            spread[(fam, name)] = (best[0] - worst[0], best[1])
    L.append("")

# 3. 종목별
L += ["## 3. 종목별 성적 (60분 비용 뒤)", "", "| 가족 | 종목 | ~2022 | 2023–24 | 2025–26 |", "|---|---|---|---|---|"]
for fam in "AB":
    for m in UNI[1:]:
        sel = [s for s in SIG[fam] if s["m"] == m]
        if sel:
            L.append("| %s | %s | %s | %s | %s |" % (fam, m[4:], cell(stat(per(sel, 0))), cell(stat(per(sel, 1))), cell(stat(per(sel, 2)))))
L.append("")

# 4. 기계적 조합 (2023–24 만 보고 고름)
L += ["## 4. 조합 — 2023–24 에서 구간 간 차이가 가장 큰 지표 3개의 가장 좋은 구간끼리", "",
      "| 가족 | 조합 | ~2022 | 2023–24 (고른 기간) | 2025–26 | 판정 |", "|---|---|---|---|---|---|"]
for fam in "ABC":
    top = sorted([(v[0], n, v[1]) for (f_, n), v in spread.items() if f_ == fam], key=lambda x: -x[0])[:3]
    combos = [(a, b) for x, a in enumerate(top) for b in top[x + 1:]] + ([tuple(top)] if len(top) == 3 else [])
    for combo in combos:
        sel = [s for s in SIG[fam] if all(c[2][3](s) for c in combo)]
        sts = [stat(per(sel, k)) for k in range(3)]
        ok = all(x[1] >= 10 and x[3] is not None and x[3] > NEED[fam] for x in sts)
        strong = ok and sum(1 for x in sts if x[4] is not None and x[4] > 0) >= 2
        L.append("| %s | %s | %s | %s | %s | %s |" % (fam, " + ".join("%s: %s" % (c[1], c[2][0]) for c in combo), cell(sts[0]), cell(sts[1]), cell(sts[2]),
                                                 "**강한 통과**" if strong else ("통과" if ok else "")))
L.append("")

head = ["## 요약 — 통과한 구간 (본 구간 %d개 중 %d개, 강한 통과 %d개)" % (tested, len(passed), sum(1 for p in passed if p[4])), "",
        "| 가족 | 지표 | 구간 | ~2022 | 2023–24 | 2025–26 | 판정 |", "|---|---|---|---|---|---|---|"]
for fam, name, label, sts, strong in passed:
    head.append("| %s | %s | %s | %s | %s | %s | %s |" % (fam, name, label, cell(sts[0]), cell(sts[1]), cell(sts[2]), "**강한 통과**" if strong else "통과"))
head.append("")
i0 = L.index("## 0. 기간별 기본 성적")
L = L[:i0] + head + L[i0:]
OUT.write_text("\n".join(L), encoding="utf-8")
print("\n".join(head))
