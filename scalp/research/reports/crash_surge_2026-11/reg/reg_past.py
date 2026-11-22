# -*- coding: utf-8 -*-
"""A-202611-02 의 과거 값을 추적기 관례로 다시 계산한다 (고문 판정 2026-11-22 ② 3·5번). 저장소의 discover.py 함수를 그대로 쓴다.
추적기 관례: 사건 = discover.detect(15분 종가 수익 ≤ −3%, 거래대금 조건 없음), 진입 = 다음 15분봉 시가, 청산 = 진입 4봉 뒤 봉의 시가,
한 종목 동시 1건(build_trades 의 busy — 조건을 못 채운 급락도 자리를 차지한다), 지표는 정확히 그 봉이 있을 때만(Bars.ret).
조건: 그 종목 4시간 수익(ret(q, 16)) ≤ −5.92%, BTC 24시간 수익(btc_ret_1d) ≤ −1.73%. BTC 는 지표용이고 거래 대상이 아니다.
일별 평균 = 진입일(UTC)마다 그날 거래의 평균 → 날들의 평균.
배분 모의(미리 정한 가정): 자금 5칸, 신호마다 1칸(자금의 20%, 복리 없음), 동시 5종목까지, 넘는 신호는 건너뜀. 같은 봉에 신호가 빈 칸보다 많으면 큰 5종목 먼저, 그다음 신호 봉 거래대금이 큰 순(고문 판정 2026-11-22 ③ 4).
사용: reg_past.py REPO DATA(c15_*.jsonl.gz) OUT.md"""
import gzip, importlib.util, json, pickle, sys
from pathlib import Path
REPO, DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
spec = importlib.util.spec_from_file_location("discover", str(REPO / "scalp/research/discover.py"))
D = importlib.util.module_from_spec(spec)
sys.argv = [sys.argv[0]]
spec.loader.exec_module(D)
BTC, BIG = "KRW-BTC", ("KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE", "KRW-ADA")
A4H, B24 = -0.0592, -0.0173
panel = {}
for m in D.REPRO_UNIVERSE:
    rows = {}
    with gzip.open(DATA / ("c15_%s.jsonl.gz" % m), "rt") as g:
        for line in g:
            try:
                page = json.loads(line)
            except ValueError:
                continue
            for r in page:
                rows[r[0] // 15] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6]))
    panel[m] = D.Bars(rows)
print("panel", len(panel), flush=True)
allt = D.build_trades(panel, D.day_q("2017-09-25"), D.day_q("2026-11-20"), 0.03, 0.0, {"KRW-BTC": 0.0007, "KRW-ETH": 0.0007}, 0.001, only=("CRASH", 4))
T = []
for t in allt:
    m = t["market"]
    r4 = panel[m].ret(t["entry_q"] - 1, 16)
    b1 = t["ind"].get("btc_ret_1d")
    t["ret_4h"], t["ok"] = r4, bool(m != BTC and r4 is not None and r4 <= A4H and b1 is not None and b1 <= B24)
    b = panel[m]
    t["value"], t["entry_px"], t["exit_px"] = b.v[b.idx(t["entry_q"] - 1)], b.o[b.idx(t["entry_q"])], b.o[b.idx(t["exit_q"])]
    if t["ok"]:
        T.append(t)
pickle.dump({"combo": [{k: t[k] for k in ("market", "entry_q", "exit_q", "day", "gross", "net", "move", "ret_4h", "value", "entry_px", "exit_px")} | {"btc_ret_1d": t["ind"].get("btc_ret_1d")} for t in T],
             "crash": [(t["market"], t["entry_q"]) for t in allt]}, open(str(OUT) + ".pkl", "wb"))
print("crash trades", len(allt), "combo", len(T), flush=True)
pct = lambda v, d=2: "-" if v is None else ("%+." + str(d) + "f%%") % (100 * v)
mean = lambda v: sum(v) / len(v) if v else None


def stat(part):
    by = {}
    for t in part:
        by.setdefault(t["day"], []).append(t["net"])
    d = [mean(v) for v in by.values()]
    return len(part), len(d), mean([t["net"] for t in part]), mean(d)


PER = (("2017–22", "0000", "2023"), ("2023–24", "2023", "2025"), ("2025–26", "2025", "9999"))
UNIV = (("전체 19종목", lambda t: True), ("주 대상: 큰 5종목", lambda t: t["market"] in BIG), ("비교용: 나머지 14종목", lambda t: t["market"] not in BIG))
L = ["# A-202611-02 과거 값 — 추적기 관례 (다음 봉 시가 진입, 진입 4봉 뒤 봉의 시가 청산, 한 종목 동시 1건)", "",
     "저장소 discover.py 의 detect·build_trades·indicators 를 그대로 써서 15분봉 9년(2017-09 ~ 2026-11)에 적용했다. 비용 편도 BTC·ETH 0.07%, 그 밖 0.10%.",
     "−3%% 급락으로 잡힌 거래 %d건 가운데 두 조건을 채운 알트 거래 %d건." % (len(allt), len(T)), "",
     "## 등록부에 적을 값", "", "| 대상 | 기간 | 거래 수 | 날 수 | 거래당 평균 | 일별 평균 |", "|---|---|---|---|---|---|"]
for un, uf in UNIV:
    for pn, a, b in PER:
        n, days, net, dm = stat([t for t in T if uf(t) and a <= t["day"][:4] < b])
        L.append("| %s | %s | %d | %d | %s | %s |" % (un, pn, n, days, pct(net), pct(dm)))
L += ["", "고문 참고 값(시가 청산, 일별 평균, 2023–24 / 2025–26): 전체 +0.89% / +0.94%, 큰 5종목 +1.92% / +2.00%.", ""]


def simulate(part, order):
    part = sorted(part, key=lambda t: (t["entry_q"], order(t)))
    open_x, taken, skipped = [], [], 0
    for t in part:
        open_x = [x for x in open_x if x > t["entry_q"]]        # 이 봉 시가에 청산되는 자리는 비었다고 본다
        if len(open_x) >= 5:
            skipped += 1
            continue
        open_x.append(t["exit_q"])
        taken.append(t)
    return taken, skipped


ORD = (("큰 5종목 먼저, 그다음 신호 봉 거래대금이 큰 순", lambda t: (0 if t["market"] in BIG else 1, -t["value"], t["market"])),)   # 고문 판정 2026-11-22 ③ 4
L += ["## 배분 모의 — 자금 5칸, 신호마다 1칸, 동시 5종목까지 (복리 없음, 자금 대비)", "",
      "| 대상 | 고르는 순서 | 잡은 / 건너뛴 | 9년 합계 | 최대 낙폭 | 최악의 하루 | 손실인 해 |", "|---|---|---|---|---|---|---|"]
years = {}
OUTJ = {"past": {}, "sim": {}}
for un, uf in UNIV:
    OUTJ["past"][un] = [dict(zip(("period", "n", "days", "net", "daymean"), (pn,) + stat([t for t in T if uf(t) and a <= t["day"][:4] < b]))) for pn, a, b in PER]
for un, uf in UNIV:
    for on, of in ORD:
        taken, skipped = simulate([t for t in T if uf(t)], of)
        cum = peak = mdd = 0.0
        for t in sorted(taken, key=lambda t: (t["exit_q"], t["market"])):
            cum += t["net"] / 5
            peak = max(peak, cum)
            mdd = min(mdd, cum - peak)
        by_day, by_year = {}, {}
        for t in taken:
            by_day[t["day"]] = by_day.get(t["day"], 0.0) + t["net"] / 5
            a = by_year.setdefault(t["day"][:4], [0, 0.0])
            a[0] += 1
            a[1] += t["net"] / 5
        wd = min(by_day.items(), key=lambda x: x[1])
        label = un
        years[label] = by_year
        OUTJ["sim"][un] = {"taken": len(taken), "skipped": skipped, "total": cum, "mdd": mdd, "worst_day": wd[1], "worst_date": wd[0], "loss_years": [y for y in sorted(by_year) if by_year[y][1] < 0]}
        L.append("| %s | %s | %d / %d | %s | %s | %s (%s) | %s |" % (un, on if skipped else "(칸이 모자란 적 없음)", len(taken), skipped, pct(cum, 1), pct(mdd, 1), pct(wd[1], 1), wd[0],
                                                           ", ".join(y for y in sorted(by_year) if by_year[y][1] < 0) or "없음"))
L += ["", "고문 계산(원자료, 종가 청산): 큰 5종목 515건 +146%, 10개 연도 모두 플러스, 최대 낙폭 −12%, 최악의 하루 −8%. 전체 1,698건 +209%, 2019·2025 마이너스, 최대 낙폭 −30%.", "",
      "### 연도별 (자금 대비, 잡은 신호 수)", "", "| 연도 | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
for y in sorted({y for v in years.values() for y in v}):
    L.append("| %s | %s |" % (y, " | ".join("%s (%d)" % (pct(v[y][1], 1), v[y][0]) if y in v else "-" for v in years.values())))
import json as _json
open(str(OUT) + ".json", "w", encoding="utf-8").write(_json.dumps(OUTJ, ensure_ascii=False, indent=1))
OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print("\n".join(l[:230] for l in L[4:]))
