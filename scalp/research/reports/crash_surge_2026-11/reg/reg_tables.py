# -*- coding: utf-8 -*-
"""A-202611-02 등록 보고서에 넣을 표 (고문 판정 2026-11-22 5번). 9년치 급락 사건 원자료(crash_events_9y.csv.gz)만 쓴다. 연구용.
규칙: 알트 15분 −3% 이하 + 그 종목 4시간 −5.92% 이하 + BTC 24시간 −1.73% 이하 → 다음 15분봉 시가 매수, 60분 뒤 매도. 비용 편도 ETH 0.07%, 그 밖 0.10%.
a) 신호마다 같은 금액 / 날마다 같은 금액, 상위 5일 제외.
b) 배분 모의(미리 정한 가정): 자금을 5칸으로 나눠 신호 하나에 한 칸(자금의 20%, 복리 없음). 이미 5종목을 들고 있으면 그 신호는 건너뛴다.
   한 15분봉에 신호가 빈 칸보다 많으면 고르는 순서를 두 가지로 본다: 알파벳 순 / 큰 5종목 먼저(ETH·XRP·SOL·DOGE·ADA) 그다음 알파벳 순.
   보유는 신호 다음 봉부터 4봉. 수익·낙폭은 자금 대비 %. 낙폭은 청산 순서대로 쌓은 누적 손익의 고점 대비 최대 하락.
c) 사건마다 60분 안 최대 추가 하락폭의 분포.
사용: reg_tables.py crash_events_9y.csv.gz OUT.md"""
import csv, datetime as dt, gzip, math, statistics as st, sys
UTC = dt.timezone.utc
BIG = ("ETH", "XRP", "SOL", "DOGE", "ADA")
R = []
for r in csv.DictReader(gzip.open(sys.argv[1], "rt", encoding="utf-8")):
    if r["조합"] != "1":
        continue
    t = int(dt.datetime.strptime(r["신호 시각(UTC)"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp() // 60)
    R.append({"id": r["사건 ID"], "m": r["종목"], "q": t // 15 - 1, "day": r["날"], "net": float(r["① 60분"]), "dd": float(r["최대 추가 하락"]), "big": r["종목"] in BIG})
pct = lambda v, d=2: "-" if v is None else ("%+." + str(d) + "f%%") % (100 * v)
mean = lambda v: sum(v) / len(v) if v else None


def daymeans(part):
    g = {}
    for e in part:
        g.setdefault((e["day"], e["q"]), []).append(e["net"])
    d = {}
    for (day, _q), v in g.items():
        d.setdefault(day, []).append(mean(v))
    return {k: mean(v) for k, v in d.items()}


PER = (("2017–22", lambda e: e["day"] < "2023"), ("2023–26", lambda e: e["day"] >= "2023"), ("9년 전체", lambda e: True))
UNIV = (("전체 (BTC 제외 19종목)", lambda e: True), ("큰 5종목", lambda e: e["big"]), ("나머지 14종목", lambda e: not e["big"]))
L = ["# A-202611-02 등록 보고서용 표 (고문 판정 2026-11-22 5번)", "", "연구용 과거 값이다. 봉 가격 기준, 추가 슬리피지 0, 지금 상장된 종목만. 9년 자료에서 구간 230개를 본 뒤 고른 규칙이다.", "",
     "## a) 신호마다 같은 금액 vs 날마다 같은 금액 (60분, 비용 뒤)", "",
     "| 대상 | 기간 | 신호 / 일수 | 신호마다 같은 금액: 평균 | 〃 상위 5일 제외 | 날마다 같은 금액: 평균 | 〃 상위 5일 제외 | 신호 기준 승률 | 플러스인 날 |", "|---|---|---|---|---|---|---|---|---|"]
for un, uf in UNIV:
    for pn, pf in PER:
        part = [e for e in R if uf(e) and pf(e)]
        if not part:
            continue
        tot = {}
        for e in part:
            tot[e["day"]] = tot.get(e["day"], 0.0) + e["net"]
        top5 = set(sorted(tot, key=lambda d: -tot[d])[:5])
        dm = daymeans(part)
        v = sorted(dm.values())
        L.append("| %s | %s | %d / %d | %s | %s | %s | %s | %.0f%% | %.0f%% |" % (
            un, pn, len(part), len(dm), pct(mean([e["net"] for e in part])), pct(mean([e["net"] for e in part if e["day"] not in top5])),
            pct(mean(v)), pct(mean(v[:-5])) if len(v) > 5 else "-", 100.0 * sum(1 for e in part if e["net"] > 0) / len(part), 100.0 * sum(1 for x in v if x > 0) / len(v)))


def simulate(part, order):
    part = sorted(part, key=lambda e: (e["q"], order(e)))
    open_q, taken, skipped = [], [], 0
    for e in part:
        open_q = [q for q in open_q if q >= e["q"] - 3]          # 4봉 보유: q-3 이후에 잡은 것은 아직 들고 있다
        if len(open_q) >= 5:
            skipped += 1
            continue
        open_q.append(e["q"])
        taken.append(e)
    return taken, skipped


L += ["", "## b) 배분 모의 — 신호당 자금의 20%, 동시 보유 최대 5종목 (넘는 신호는 건너뜀, 복리 없음)", "",
      "| 대상 | 고르는 순서 | 잡은 신호 / 건너뛴 신호 | 9년 합계 수익(자금 대비) | 최대 낙폭 | 최악의 하루 | 손실인 해 |", "|---|---|---|---|---|---|---|"]
ORD = (("알파벳 순", lambda e: e["m"]), ("큰 5종목 먼저", lambda e: (0, BIG.index(e["m"])) if e["m"] in BIG else (1, e["m"])))
years = {}
for un, uf in UNIV[:2]:
    for on, of in (ORD if un.startswith("전체") else ORD[:1]):
        taken, skipped = simulate([e for e in R if uf(e)], of)
        cum = peak = mdd = 0.0
        for e in sorted(taken, key=lambda e: e["q"] + 4):
            cum += e["net"] / 5
            peak = max(peak, cum)
            mdd = min(mdd, cum - peak)
        by_day, by_year = {}, {}
        for e in taken:
            by_day[e["day"]] = by_day.get(e["day"], 0.0) + e["net"] / 5
            y = e["day"][:4]
            a = by_year.setdefault(y, [0, 0.0])
            a[0] += 1
            a[1] += e["net"] / 5
        wd = min(by_day.items(), key=lambda x: x[1])
        label = "%s · %s" % (un, on) if un.startswith("전체") else un
        years[label] = by_year
        L.append("| %s | %s | %d / %d | %s | %s | %s (%s) | %s |" % (un, on if un.startswith("전체") else "(겹칠 일 없음)", len(taken), skipped, pct(cum, 1), pct(mdd, 1), pct(wd[1], 1), wd[0],
                                                           ", ".join(y for y in sorted(by_year) if by_year[y][1] < 0) or "없음"))
L += ["", "### 연도별 수익 (자금 대비, 잡은 신호 수)", "", "| 연도 | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
for y in sorted({y for v in years.values() for y in v}):
    L.append("| %s | %s |" % (y, " | ".join("%s (%d)" % (pct(v[y][1], 1), v[y][0]) if y in v else "-" for v in years.values())))


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(round(p * (len(v) - 1)))))]


L += ["", "## c) 사건마다 신호 뒤 60분 안 최대 추가 하락폭 (진입가 대비, 비용 빼기 전)", "",
      "판정은 5분봉이 있는 2023–26 만 보라고 했다. 이번에 2017–22 사건의 5분봉도 받았으므로 참고로 같이 적는다.", "",
      "| 대상 | 기간 | 사건 | 중앙값 | 하위 10% (열 번 중 한 번은 이보다 더 빠짐) | 하위 1% | 가장 나빴던 사건 |", "|---|---|---|---|---|---|---|"]
for un, uf in UNIV[:2]:
    for pn, pf in (("2023–26", lambda e: e["day"] >= "2023"), ("2017–22 (참고)", lambda e: e["day"] < "2023")):
        part = [e for e in R if uf(e) and pf(e)]
        w = min(part, key=lambda e: e["dd"])
        L.append("| %s | %s | %d | %s | %s | %s | %s (%s) |" % (un, pn, len(part), pct(st.median([e["dd"] for e in part])), pct(q([e["dd"] for e in part], 0.10)), pct(q([e["dd"] for e in part], 0.01)), pct(w["dd"]), w["id"]))
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(L) + "\n")
print("\n".join(l[:260] for l in L[4:]))
