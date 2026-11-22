# -*- coding: utf-8 -*-
"""긴 기간 검토에서 통과한 급락 조건을 연도별·보유 시간별·유동성별로 다시 본다 (study.py 가 남긴 신호 pkl 사용)."""
import math, pickle, statistics as st, sys
SIG = pickle.load(open(sys.argv[1], "rb"))
A = SIG["A"]
pct = lambda v: "-" if v is None else "%+.2f%%" % (100 * v)
mean = lambda v: sum(v) / len(v) if v else None
def days(sigs, val):
    ev = {}
    for s in sigs:
        v = val(s)
        if v is not None: ev.setdefault((s["day"], s["q"]), []).append(v)
    d = {}
    for (day, _q), v in ev.items(): d.setdefault(day, []).append(mean(v))
    return [mean(v) for v in d.values()]
def ci(d):
    if len(d) < 2: return mean(d), None
    return mean(d), 1.96 * st.stdev(d) / math.sqrt(len(d))
def row(sigs, val=lambda s: s["net"][4]):
    d = days(sigs, val); m, h = ci(d)
    if not d: return "-"
    win = mean([1.0 if s["net"][4] > 0 else 0.0 for s in sigs])
    return "%d/%d일 · %.0f%% · %s [%s, %s]" % (len(sigs), len(d), 100 * win, pct(m), pct(None if h is None else m - h), pct(None if h is None else m + h))
f = lambda s, n: s["f"].get(n)
R1 = [s for s in A if f(s, "4시간 수익") is not None and f(s, "4시간 수익") <= -0.0592]
R2 = [s for s in R1 if f(s, "BTC 24시간 수익") is not None and f(s, "BTC 24시간 수익") <= -0.0173]
BAD = [s for s in A if f(s, "4시간 수익") is not None and f(s, "4시간 수익") > 0]
out = ["## 5. 통과한 급락 조건 다시 보기", "", "규칙 1 = 알트 15분 −3% 급락 + 그 종목 4시간 수익 −5.92% 이하. 규칙 2 = 규칙 1 + BTC 24시간 수익 −1.73% 이하. 반대 = 4시간 수익이 플러스인 급락.", "",
       "### 연도별 (60분 비용 뒤, 신호/일수 · 승률 · 일별 평균 [95%])", "", "| 연도 | 규칙 1 | 규칙 2 | 반대 (오른 뒤 급락) |", "|---|---|---|---|"]
for y in range(2017, 2027):
    p = lambda S_: [s for s in S_ if s["day"][:4] == str(y)]
    out.append("| %d | %s | %s | %s |" % (y, row(p(R1)), row(p(R2)), row(p(BAD))))
out += ["", "### 보유 시간별 (비용 뒤 일별 평균)과 같은 순간 BTC·ETH", "", "| 규칙 | 기간 | 15분 | 30분 | 60분 | 4시간 | 같은 순간 BTC 60분 | 같은 순간 ETH 60분 | 알트 − BTC 60분 [95%] |", "|---|---|---|---|---|---|---|---|---|"]
PER = (("~2022", "0000", "2023"), ("2023–24", "2023", "2025"), ("2025–26", "2025", "9999"))
for name, R in (("규칙 1", R1), ("규칙 2", R2)):
    for pn, a, b in PER:
        part = [s for s in R if a <= s["day"] < b + "-01-01" if True] if False else [s for s in R if a <= s["day"][:4] < b]
        hz = [pct(mean(days(part, lambda s, k=k: s["net"][k]))) for k in (1, 2, 4, 16)]
        bt = pct(mean(days(part, lambda s: s["btc"][4] if s.get("btc") else None)))
        et = pct(mean(days(part, lambda s: s["eth"][4] if s.get("eth") else None)))
        m, h = ci(days(part, lambda s: s["net"][4] - s["btc"][4] if s.get("btc") else None))
        out.append("| %s | %s | %s | %s | %s |" % (name, pn, " | ".join(hz), bt + " | " + et, "%s [%s, %s]" % (pct(m), pct(m - h), pct(m + h)) if h else pct(m)))
out += ["", "### 종목 유동성별 — 규칙 2 (평소 15분 거래대금, 60분 비용 뒤)", "", "| 평소 15분 거래대금 | ~2022 | 2023–24 | 2025–26 |", "|---|---|---|---|"]
for label, lo, hi in (("1억 원 미만", -99, 8.0), ("1억~5억 원", 8.0, 8.7), ("5억 원 이상", 8.7, 99)):
    sel = [s for s in R2 if f(s, "평소 거래대금(로그)") is not None and lo <= f(s, "평소 거래대금(로그)") < hi]
    out.append("| %s | %s |" % (label, " | ".join(row([s for s in sel if a <= s["day"][:4] < b]) for _n, a, b in PER)))
out += ["", "### 종목별 — 규칙 2 (전체 기간, 60분 비용 뒤)", "", "| 종목 | 신호/일수 · 승률 · 평균 [95%] |", "|---|---|"]
for m in sorted({s["m"] for s in R2}):
    out.append("| %s | %s |" % (m[4:], row([s for s in R2 if s["m"] == m])))
out += ["", "### 추가 체결 손실을 더 빼면 — 규칙 2 (60분 일별 평균)", "", "| 추가 손실 | ~2022 | 2023–24 | 2025–26 |", "|---|---|---|---|"]
for x in (0.0, 0.003, 0.005, 0.008):
    out.append("| %.1f%%p | %s |" % (100 * x, " | ".join(pct(mean(days([s for s in R2 if a <= s["day"][:4] < b], lambda s: s["net"][4] - x))) for _n, a, b in PER)))
n_days = len({s["day"] for s in R2}); yrs = (2026.9 - 2017.75)
out += ["", "규칙 2 신호가 난 날: 전체 %d일 (1년에 약 %.0f일), 2023년 이후 %d일." % (n_days, n_days / yrs, len({s["day"] for s in R2 if s["day"] >= "2023"})), ""]
open(sys.argv[2], "a", encoding="utf-8").write("\n" + "\n".join(out))
print("\n".join(out))
