# -*- coding: utf-8 -*-
"""대조표 (고문 판정 2026-11-22 ③ 5): 2023–26 조합 사건을 추적기 코드로 다시 계산한 것과 연구 원자료를 사건 ID 로 맞춘다.
추적기 쪽 = reg_past.py 가 남긴 거래(저장소 discover.py 로 계산: 다음 봉 시가 진입, 진입 4봉 뒤 봉의 시가 청산, 한 종목 동시 1건).
연구 쪽 = crash9/crash_events_9y.csv.gz (다음 15분봉의 첫 체결가 진입, 신호 뒤 60분의 종가 청산, 60분 안 재신호 제외).
사용: compare.py TRACKER.pkl crash_events_9y.csv.gz OUT.md"""
import csv, datetime as dt, gzip, pickle, statistics as st, sys
UTC = dt.timezone.utc
P = pickle.load(open(sys.argv[1], "rb"))
sid = lambda m, eq: "%s_%s" % (m[4:], dt.datetime.fromtimestamp(eq * 900, tz=UTC).strftime("%Y%m%dT%H%M"))
TR = {sid(t["market"], t["entry_q"]): t for t in P["combo"] if t["day"] >= "2023"}
TR_CRASH = {}
for m, eq in P["crash"]:
    TR_CRASH.setdefault(m[4:], set()).add(eq)
RS, RS_ALL = {}, {}
for r in csv.DictReader(gzip.open(sys.argv[2], "rt", encoding="utf-8")):
    t = int(dt.datetime.strptime(r["신호 시각(UTC)"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp() // 900)
    RS_ALL.setdefault(r["종목"], {})[t] = r
    if r["조합"] == "1" and r["날"] >= "2023":
        RS[r["사건 ID"]] = dict(r, eq=t)
pct = lambda v, d=3: ("%+." + str(d) + "f%%") % (100 * v)
both = sorted(set(TR) & set(RS))
only_t, only_r = sorted(set(TR) - set(RS)), sorted(set(RS) - set(TR))
same_entry, exit_diff, net_diff = 0, [], []
for k in both:
    t, r = TR[k], RS[k]
    c = 0.0007 if t["market"] in ("KRW-BTC", "KRW-ETH") else 0.001
    e1 = float(r["① 진입가"])
    rx = e1 * ((1 + float(r["① 60분"])) * (1 + c) / (1 - c))
    same_entry += abs(e1 / t["entry_px"] - 1) < 1e-9
    exit_diff.append(t["exit_px"] / rx - 1)
    net_diff.append(t["net"] - float(r["① 60분"]))
q = lambda v, p: sorted(v)[min(len(v) - 1, max(0, int(round(p * (len(v) - 1)))))]


def why_only_research(k):
    r = RS[k]
    coin, eq = r["종목"], r["eq"]
    crash = TR_CRASH.get(coin, set())
    if eq in crash:
        return "추적기에서도 급락 거래지만 조건 값이 달랐다(지표는 정확히 그 봉이 있을 때만 계산)"
    if any((eq - d) in crash for d in range(1, 5)):
        return "추적기: 앞 급락의 보유 중이라 세지 않음(한 종목 동시 1건 — 60분 뒤 바로가 아니라 한 봉 더 뒤부터)"
    return "추적기에서 거래가 되지 않음(진입 봉이나 청산 봉이 없음 등)"


def why_only_tracker(k):
    t = TR[k]
    coin, eq = t["market"][4:], t["entry_q"]
    allr = RS_ALL.get(coin, {})
    if eq in allr:
        return "연구에서도 급락 사건이지만 조건 값이 달랐다(연구는 빠진 봉을 직전 종가로 채워 계산)"
    if any((eq - d) in allr for d in range(1, 4)):
        return "연구: 앞 급락 뒤 60분 안이라 세지 않음(연구의 앞 급락은 추적기에서는 보유 중이라 빠진 것)"
    return "연구 쪽에 사건이 없음(5분봉 없음 등)"


L = ["## 대조표 — 추적기 코드로 다시 계산한 것 vs 연구 원자료 (2023–26 조합 사건, 사건 ID 기준)", "",
     "| 항목 | 값 |", "|---|---|",
     "| 추적기 쪽 사건 | %d |" % len(TR), "| 연구 쪽 사건 | %d |" % len(RS), "| 양쪽에 다 있는 사건 | %d |" % len(both),
     "| 그중 진입가가 같은 사건 | %d / %d |" % (same_entry, len(both)),
     "| 청산가 차이 (추적기 다음 봉 시가 ÷ 연구 60분째 종가 − 1): 중앙값 / 평균 | %s / %s |" % (pct(st.median(exit_diff)), pct(sum(exit_diff) / len(exit_diff))),
     "| 〃 하위 10%% / 상위 10%% / 가장 큰 차이 | %s / %s / %s |" % (pct(q(exit_diff, 0.1)), pct(q(exit_diff, 0.9)), pct(max(exit_diff, key=abs))),
     "| 수익 차이 (추적기 − 연구, 거래당): 평균 / 중앙값 | %s / %s |" % (pct(sum(net_diff) / len(net_diff)), pct(st.median(net_diff))),
     "| 추적기에만 있는 사건 | %d |" % len(only_t), "| 연구에만 있는 사건 | %d |" % len(only_r), "",
     "### 한쪽에만 있는 사건과 이유", "", "| 어느 쪽 | 사건 ID | 이유 |", "|---|---|---|"]
cnt = {}
for k in only_t:
    w = why_only_tracker(k)
    cnt[("추적기에만", w)] = cnt.get(("추적기에만", w), 0) + 1
    L.append("| 추적기에만 | %s | %s |" % (k, w))
for k in only_r:
    w = why_only_research(k)
    cnt[("연구에만", w)] = cnt.get(("연구에만", w), 0) + 1
    L.append("| 연구에만 | %s | %s |" % (k, w))
L += ["", "이유별 건수: " + "; ".join("%s — %s %d건" % (a, b, n) for (a, b), n in sorted(cnt.items())) + ".", ""]
open(sys.argv[3], "w", encoding="utf-8").write("\n".join(L) + "\n")
print("\n".join(L[:14])); print(L[-2][:900])
