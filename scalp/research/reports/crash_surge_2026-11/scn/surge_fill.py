# -*- coding: utf-8 -*-
"""1분 급등 — 기존 표에서 빠진 칸 채우기(제3세션 요청 2026-11-22). 새로 시세를 받지 않고 신호별 원자료(surge_signals.csv.gz)만 다시 센다.
호가 3칸 조건 적용 전후, 모양별 1·3·5·10분 순수익·승률·이겼을 때 평균 이익·졌을 때 평균 손실, 횡보 폭 기준(0.5% / 0.8%) 구분.
'3칸 이상' 은 10분 재신호 제외를 먼저 한 신호 가운데 고른 것이다(재현표의 3,443개는 3칸 이상끼리만 재신호를 제외해 수가 조금 다르다).
사용: surge_fill.py surge_signals.csv.gz OUT.md"""
import csv, gzip, math, statistics as st, sys
R = list(csv.DictReader(gzip.open(sys.argv[1], "rt", encoding="utf-8")))
WINS = (1, 3, 5, 10)
for r in R:
    r["th"] = float(r["기준"]); r["vol"] = r["거래량 3배"] == "1"; r["nt"] = int(r["움직인 칸 수"]) if r["움직인 칸 수"] else None
    r["px"] = float(r["진입가"]); r["rng"] = float(r["직전 5분 고저 폭"]) if r["직전 5분 고저 폭"] else None
    for w in WINS:
        r[w] = float(r["%d분 수익(비용 뒤)" % w])
pct = lambda v: "-" if v is None else "%+.2f%%" % (100 * v)
mean = lambda v: sum(v) / len(v) if v else None


def day_ci(part):
    by = {}
    for s in part:
        by.setdefault(s["날"], []).append(s[10])
    d = [mean(x) for x in by.values()]
    if len(d) < 30:
        return "%s (날 %d)" % (pct(mean(d)), len(d))
    hw = 1.96 * st.stdev(d) / math.sqrt(len(d))
    return "%s [%s, %s]" % (pct(mean(d)), pct(mean(d) - hw), pct(mean(d) + hw))


def line(part):
    if not part:
        return "0 | - | - | - | - | - | - | - | - | -"
    w_ = [s[10] for s in part if s[10] > 0]
    l_ = [s[10] for s in part if s[10] <= 0]
    return "%d | %d | %s | %s | %s | %s | %s | %.0f%% | %s | %s" % (len(part), len({s["날"] for s in part}), pct(mean([s[1] for s in part])), pct(mean([s[3] for s in part])),
                                                                 pct(mean([s[5] for s in part])), pct(mean([s[10] for s in part])), day_ci(part), 100.0 * len(w_) / len(part), pct(mean(w_)), pct(mean(l_)))


HDR = "신호 | 날 | 1분 | 3분 | 5분 | 10분 | 10분 날짜별 평균 [95%] | 10분 승률 | 이겼을 때 평균 이익 | 졌을 때 평균 손실"
L = ["# 1분 급등 — 빠진 칸 채우기 (고정 20종목, 1년, 비용 뒤)", "",
     "scn/report.md 와 같은 신호별 원자료를 다시 센 것이다. 새 시세도 새 정의도 없다. 1분 급등 추격은 접기로 한 뒤의 보충 표이고, 여기서 규칙을 새로 고르지 않는다.",
     "이김 = 10분 시점 비용 뒤 수익 > 0. '3칸 이상' = 신호 봉이 호가 3칸 이상 움직인 신호(10분 재신호 제외를 먼저 한 신호 가운데 고름).", "",
     "## 1. 여덟 조합 — 호가 3칸 조건 적용 전후", "", "| 기준 | 거래량 | 3칸 조건 | %s |" % HDR, "|---|---|---|" + "---|" * 10]
for th in (0.005, 0.01, 0.015, 0.02):
    for vol in (False, True):
        base = [r for r in R if abs(r["th"] - th) < 1e-9 and (r["vol"] or not vol)]
        for tn, tf in (("없음", lambda r: True), ("3칸 이상", lambda r: r["nt"] is not None and r["nt"] >= 3)):
            L.append("| +%.1f%% | %s | %s | %s |" % (100 * th, "3배 이상" if vol else "제한 없음", tn, line([r for r in base if tf(r)])))
L += ["", "## 2. 모양별 — 호가 3칸 조건 적용 전후", "", "| 기준 | 거래량 | 3칸 조건 | 모양 | %s |" % HDR, "|---|---|---|---|" + "---|" * 10]
for th in (0.005, 0.01, 0.015, 0.02):
    for vol in (False, True):
        base = [r for r in R if abs(r["th"] - th) < 1e-9 and (r["vol"] or not vol)]
        for tn, tf in (("없음", lambda r: True), ("3칸 이상", lambda r: r["nt"] is not None and r["nt"] >= 3)):
            for shape in ("횡보 후 돌파", "선행 상승", "그 밖"):
                L.append("| +%.1f%% | %s | %s | %s | %s |" % (100 * th, "3배 이상" if vol else "제한 없음", tn, shape, line([r for r in base if tf(r) and r["모양"] == shape])))
L += ["", "## 3. 횡보 폭 기준 구분 — '횡보 후 돌파' 신호 (거래량 제한 없음)", "",
      "scn 계산은 1,000원 미만 종목에 폭 0.8%, 그 이상에 0.5% 를 썼다(bot.py 기준). 판정 ④ 기본안은 모두 0.5%(그리고 직전 5분 수익 ±0.25% 안)다. 아래에서 0.8% 덕에 들어온 신호를 따로 본다.", "",
      "| 기준 | 3칸 조건 | 구분 | %s |" % HDR, "|---|---|---|" + "---|" * 10]
for th in (0.005, 0.01, 0.015, 0.02):
    base = [r for r in R if abs(r["th"] - th) < 1e-9 and r["모양"] == "횡보 후 돌파"]
    for tn, tf in (("없음", lambda r: True), ("3칸 이상", lambda r: r["nt"] is not None and r["nt"] >= 3)):
        for kn, kf in (("1,000원 이상 (폭 ≤ 0.5%)", lambda r: r["px"] >= 1000), ("1,000원 미만, 폭 ≤ 0.5% (기본안과 같음)", lambda r: r["px"] < 1000 and r["rng"] is not None and r["rng"] <= 0.005),
                       ("1,000원 미만, 폭 0.5~0.8% (0.8% 기준이라 들어온 것)", lambda r: r["px"] < 1000 and r["rng"] is not None and r["rng"] > 0.005)):
            L.append("| +%.1f%% | %s | %s | %s |" % (100 * th, tn, kn, line([r for r in base if tf(r) and kf(r)])))
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(L) + "\n")
print("\n".join(l[:230] for l in L[6:25]))
