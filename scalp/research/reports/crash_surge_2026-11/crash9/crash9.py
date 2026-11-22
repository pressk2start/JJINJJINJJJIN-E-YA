# -*- coding: utf-8 -*-
"""급락 추가 계산 — 9년(2017-09 ~ 2026-11), 고문 판정 2026-11-21 ④ 2번. 연구용(돈에 바로 쓰지 않는다).
정의 (판정 ④ 그대로, 결과를 보기 전에 고정):
  사건      알트(BTC 제외 19종목) 완료 15분봉의 종가 수익 ≤ −3%. 같은 종목 60분 안 재신호 제외. 신호 시각 = 그 봉 마감. (long/study.py 가족 A)
  조합      그 종목 4시간 수익 ≤ −5.92% 그리고 BTC 24시간 수익 ≤ −1.73% (둘 다 신호 봉 마감 기준).
  큰 5종목  조합 가운데 ETH·XRP·SOL·DOGE·ADA.
  ① 바로    다음 15분봉 시가(= 신호 뒤 15분 안의 첫 체결 가격).
  ② 확인    신호 뒤 5분봉의 종가가 직전 5분봉 종가보다 높게 마감하면 그다음 5분봉 시가. 신호 뒤 45분 안에 확인이 없으면 미진입.
  청산      두 방식 모두 원래 신호 뒤 60분의 종가. ② 는 진입 건당 성적과, 미진입을 0 으로 넣은 신호당 성적을 둘 다 낸다.
  채택 기준  ② 의 신호당 일별 평균이 2017–22 와 2023–26 양쪽에서 ① 보다 높을 때만 ② 를 쓴다. 아니면 ①.
  비용      편도 BTC·ETH 0.07%, 그 밖 0.10%. 추가 슬리피지 0.
  5분봉     거래가 없어 빠진 5분봉은 직전 종가로 채운다. 한 봉 안의 고점·저점 순서는 '미상'.
  일별 평균  같은 15분봉의 사건을 먼저 평균 → 날마다 평균 → 날들을 같은 무게로 평균. [ ] 는 95% 구간.
사용: crash9.py SIGNALS.pkl(long/study.py 산출) M5_CACHE OUT_DIR"""
import csv
import datetime as dt
import gzip
import io
import json
import math
import pickle
import statistics as st
import sys
from pathlib import Path

SIG, RD, OUT = pickle.load(open(sys.argv[1], "rb"))["A"], Path(sys.argv[2]), Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
BTC, ETH = "KRW-BTC", "KRW-ETH"
COST = {BTC: 0.0007, ETH: 0.0007}
A4H, B24, WAIT_BARS = -0.0592, -0.0173, 9                     # 45분 안에 마감하는 5분봉 9개
BIG = ("KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE", "KRW-ADA")
UTC = dt.timezone.utc
T975 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101,
        2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)
M5 = {}


def m5(m):
    if m not in M5:
        d = {}
        f = RD / ("m5_%s.jsonl" % m)
        if f.exists():
            for line in f.read_text().splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                d.update({int(k): v for k, v in rec["bars"].items()})
        M5[m] = d
    return M5[m]


def bar(m, x):
    d = m5(m)
    if x in d:
        return d[x]
    for y in range(x - 5, x - 35, -5):
        if y in d:
            p = d[y][3]
            return [p, p, p, p]
    return None


def net(m, r):
    c = COST.get(m, 0.001)
    return (1 + r) * (1 - c) / (1 + c) - 1


def pair(m, t):
    """같은 시간 BTC·ETH: 신호 뒤 첫 5분봉 시가에 사서 5·15·30·60분 뒤 종가 (비용 뒤)."""
    bb = [bar(m, t + 5 * j) for j in (0, 2, 5, 11)]
    if any(x is None for x in bb) or bb[0][0] <= 0:
        return [None] * 4
    return [net(m, x[3] / bb[0][0] - 1) for x in bb]


EV, skipped = [], 0
for s in SIG:
    m, q = s["m"], s["q"]
    t = (q + 1) * 15
    d = m5(m)
    first = next((d[x] for x in (t, t + 5, t + 10) if x in d), None)
    bars = [bar(m, t + 5 * j) for j in range(12)]
    pre = bar(m, t - 5)
    if first is None or pre is None or any(x is None for x in bars) or first[0] <= 0:
        skipped += 1
        continue
    e1, X = first[0], bars[11][3]
    hi = max(range(12), key=lambda j: (bars[j][1], -j))
    lo = min(range(12), key=lambda j: (bars[j][2], j))
    e2 = wait = None
    prevc = pre[3]
    for j in range(WAIT_BARS):
        if bars[j][3] > prevc:
            wait, e2 = 5 * (j + 1), bars[j + 1][0]
            break
        prevc = bars[j][3]
    f = s["f"]
    c4 = f.get("4시간 수익") is not None and f["4시간 수익"] <= A4H
    cb = f.get("BTC 24시간 수익") is not None and f["BTC 24시간 수익"] <= B24
    bt, et = pair(BTC, t), (pair(ETH, t) if m != ETH else None)
    ev = {"사건 ID": "%s_%s" % (m[4:], dt.datetime.fromtimestamp(t * 60, tz=UTC).strftime("%Y%m%dT%H%M")), "종목": m[4:], "m": m, "q": q,
          "신호 시각(UTC)": dt.datetime.fromtimestamp(t * 60, tz=UTC).strftime("%Y-%m-%d %H:%M"), "날": s["day"],
          "15분 수익": f.get("신호 봉 15분 수익"), "알트 4시간": f.get("4시간 수익"), "BTC 24시간": f.get("BTC 24시간 수익"), "BTC 1시간": f.get("BTC 1시간 수익"),
          "4시간 조건": int(c4), "BTC24 조건": int(cb), "조합": int(c4 and cb), "큰 5종목": int(c4 and cb and m in BIG),
          "① 진입가": e1, "① 5분": net(m, bars[0][3] / e1 - 1), "① 15분": net(m, bars[2][3] / e1 - 1), "① 30분": net(m, bars[5][3] / e1 - 1), "① 60분": net(m, X / e1 - 1),
          "15분봉 기준 60분(대조)": s["net"][4],
          "최대 반등": bars[hi][1] / e1 - 1, "고점까지 분": 5 * hi + 5, "최대 추가 하락": bars[lo][2] / e1 - 1, "저점까지 분": 5 * lo + 5,
          "순서": "고점 먼저" if hi < lo else ("저점 먼저" if lo < hi else "미상"),
          "② 진입": int(e2 is not None), "② 기다린 분": wait, "② 진입가": e2, "② 진입가 ÷ ① 진입가": (e2 / e1 - 1) if e2 else None,
          "② 60분(진입 건)": net(m, X / e2 - 1) if e2 else None, "② 신호당(미진입 0)": net(m, X / e2 - 1) if e2 else 0.0}
    for lab, i in (("5분", 0), ("15분", 1), ("30분", 2), ("60분", 3)):
        ev["BTC " + lab] = bt[i]
        ev["ETH " + lab] = ev["① " + lab] if m == ETH else et[i]
    EV.append(ev)
print("사건 %d (5분봉이 없어 뺀 것 %d / 전체 %d)" % (len(EV), skipped, len(SIG)), flush=True)
cols = [k for k in EV[0] if k not in ("m", "q")]
with open(OUT / "crash_events_9y.csv.gz", "wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        w = csv.DictWriter(io.TextIOWrapper(gz, encoding="utf-8", newline="", write_through=True), fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for e in EV:
            w.writerow({k: ("" if e.get(k) is None else ("%.6f" % e[k] if isinstance(e[k], float) else e[k])) for k in cols})


# ---------------------------------------------------------------- 통계
def pct(v, d=2):
    return "-" if v is None else ("%+." + str(d) + "f%%") % (100 * v)


def mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def med(v):
    v = [x for x in v if x is not None]
    return st.median(v) if v else None


def days(evs, val):
    g = {}
    for e in evs:
        v = val(e)
        if v is not None:
            g.setdefault((e["날"], e["q"]), []).append(v)
    d = {}
    for (day, _q), v in g.items():
        d.setdefault(day, []).append(mean(v))
    return {k: mean(v) for k, v in d.items()}


def ci(d):
    d = [x for x in d if x is not None]
    if not d:
        return "-"
    m_ = mean(d)
    if len(d) < 2:
        return pct(m_)
    hw = (T975[len(d) - 2] if len(d) - 1 <= 30 else 1.96) * st.stdev(d) / math.sqrt(len(d))
    return "%s [%s, %s]" % (pct(m_), pct(m_ - hw), pct(m_ + hw))


K1, K2 = (lambda e: e["① 60분"]), (lambda e: e["② 신호당(미진입 0)"])
DIFF = lambda e: e["② 신호당(미진입 0)"] - e["① 60분"]
PER = (("2017–22", lambda e: e["날"] < "2023"), ("2023–26", lambda e: e["날"] >= "2023"), ("2023–24", lambda e: "2023" <= e["날"] < "2025"), ("2025–26", lambda e: e["날"] >= "2025"))
GROUPS = (("급락 전체", lambda e: True), ("조합 (4시간 + BTC24)", lambda e: e["조합"]), ("조합의 큰 5종목", lambda e: e["큰 5종목"]))
dv = lambda evs, k: list(days(evs, k).values())
bad = sum(1 for e in EV if abs(e["① 60분"] - e["15분봉 기준 60분(대조)"]) > 0.001)
L = ["# 급락 추가 계산 — 9년 (고문 판정 2026-11-21 ④ 2번)", "",
     "연구용 관찰 결과다. 여기서 나온 규칙을 돈에 바로 쓰지 않는다.", "",
     "자료: 고정 20종목 15분봉(2017-09 ~ 2026-11)의 알트 급락 신호 %d건 가운데 5분봉을 받은 %d건(5분봉이 없어 뺀 것 %d건). "
     "정의·채택 기준은 코드 머리말에 결과를 보기 전에 적었다(판정 ④ 그대로). 비용은 편도 BTC·ETH 0.07%%, 그 밖 0.10%%, 추가 슬리피지 0. "
     "지금 상장된 종목만 있는 자료라 실제보다 좋게 나오는 쪽이고, 봉 가격 기준이라 실제 체결은 확인하지 않았다." % (len(SIG), len(EV), skipped), "",
     "대조: 5분봉으로 다시 잰 ① 60분 수익이 15분봉으로 잰 값(어제 보고)과 0.1%%p 넘게 다른 사건 %d건 / %d건." % (bad, len(EV)), "",
     "## a) 진입 방식 비교 — ① 급락 확정 직후 vs ② 반등 확인 뒤 (둘 다 신호 뒤 60분 청산, 비용 뒤)", "",
     "| 대상 | 기간 | 사건 / 일수 | ① 일별 평균 [95%] | ② 진입 / 미진입 | ② 기다린 분(중앙) · 진입가 차이(중앙) | ② 진입 건당 평균 | ② 신호당 일별 평균 [95%] | ② − ① 일별 [95%] |",
     "|---|---|---|---|---|---|---|---|---|"]
verdicts = []
for gn, gf in GROUPS:
    base = [e for e in EV if gf(e)]
    res = {}
    for pn, pf in PER:
        part = [e for e in base if pf(e)]
        if not part:
            continue
        ent = [e for e in part if e["② 진입"]]
        m1, m2 = mean(dv(part, K1)), mean(dv(part, K2))
        res[pn] = (m1, m2)
        L.append("| %s | %s | %d / %d | %s | %d / %d | %s분 · %s | %s | %s | %s |" % (
            gn, pn, len(part), len(days(part, K1)), ci(dv(part, K1)), len(ent), len(part) - len(ent), "-" if not ent else "%g" % med([e["② 기다린 분"] for e in ent]),
            pct(med([e["② 진입가 ÷ ① 진입가"] for e in ent])), pct(mean([e["② 60분(진입 건)"] for e in ent])), ci(dv(part, K2)), ci(dv(part, DIFF))))
    if "2017–22" in res and "2023–26" in res:
        ok = all(res[p][1] > res[p][0] for p in ("2017–22", "2023–26"))
        verdicts.append("- **%s:** 2017–22 ① %s / ② %s, 2023–26 ① %s / ② %s → %s" % (
            gn, pct(res["2017–22"][0]), pct(res["2017–22"][1]), pct(res["2023–26"][0]), pct(res["2023–26"][1]),
            "② 가 양쪽에서 높다 → 기준상 ② 채택" if ok else "② 가 양쪽에서 높지 않다 → 기준상 **① (바로 진입)**"))
L += ["", "채택 기준(미리 정함): ② 의 신호당 일별 평균이 2017–22 와 2023–26 양쪽에서 ① 보다 높을 때만 ②.", ""] + verdicts + [""]

ADV = {"급락만": ("-0.10%", "-0.32%"), "+ 4시간": ("+0.63%", "+0.74%"), "+ BTC24": ("+0.56%", "-0.25%"), "둘 다": ("+0.89%", "+0.94%")}
L += ["## b) 조건별 분해 — ① 바로 진입, 60분 (일별 평균 [95%], 사건/일수)", "",
      "| 조건 | 2017–22 | 2023–24 | 2025–26 | 고문 값 2023–24 / 2025–26 |", "|---|---|---|---|---|"]
for name, f in (("급락만", lambda e: True), ("+ 4시간", lambda e: e["4시간 조건"]), ("+ BTC24", lambda e: e["BTC24 조건"]), ("둘 다", lambda e: e["조합"]),
                ("(나눠 보기) 4시간만", lambda e: e["4시간 조건"] and not e["BTC24 조건"]), ("(나눠 보기) BTC24 만", lambda e: e["BTC24 조건"] and not e["4시간 조건"]),
                ("(나눠 보기) 둘 다 아님", lambda e: not e["4시간 조건"] and not e["BTC24 조건"])):
    cells = []
    for pn in ("2017–22", "2023–24", "2025–26"):
        part = [e for e in EV if f(e) and dict(PER)[pn](e)]
        cells.append("%s (%d/%d일)" % (ci(dv(part, K1)), len(part), len(days(part, K1))) if part else "-")
    L.append("| %s | %s | %s |" % (name, " | ".join(cells), " / ".join(ADV[name]) if name in ADV else "-"))
L.append("")

combo = [e for e in EV if e["조합"]]
L += ["## c) 조합 사건의 신호 뒤 60분 (① 진입가 대비)", "",
      "5·15·30·60분은 그 시점에 팔았을 때의 비용 뒤 일별 평균. 최대 반등·최대 추가 하락은 비용 빼기 전 중앙값(걸린 분 중앙값) — 그 값에 팔 수 있었다는 뜻이 아니다. 사건별 값은 원자료에 있다.", "",
      "| 대상 | 기간 | 사건 | 5분 | 15분 | 30분 | 60분 | 같은 시간 BTC 5 / 15 / 30 / 60분 | 같은 시간 ETH 5 / 15 / 30 / 60분 | 최대 반등 (분) | 최대 추가 하락 (분) | 고점 먼저 / 저점 먼저 / 미상 |",
      "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for gn, gf in GROUPS[1:]:
    for pn, pf in PER[:2]:
        part = [e for e in EV if gf(e) and pf(e)]
        if not part:
            continue
        dm = lambda k: pct(mean(dv(part, lambda e, k=k: e.get(k))))
        od = [e["순서"] for e in part]
        L.append("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %s (%g) | %s (%g) | %.0f%% / %.0f%% / %.0f%% |" % (
            gn, pn, len(part), dm("① 5분"), dm("① 15분"), dm("① 30분"), dm("① 60분"), " / ".join(dm("BTC " + x) for x in ("5분", "15분", "30분", "60분")),
            " / ".join(dm("ETH " + x) for x in ("5분", "15분", "30분", "60분")), pct(med([e["최대 반등"] for e in part])), med([e["고점까지 분"] for e in part]),
            pct(med([e["최대 추가 하락"] for e in part])), med([e["저점까지 분"] for e in part]),
            100.0 * od.count("고점 먼저") / len(od), 100.0 * od.count("저점 먼저") / len(od), 100.0 * od.count("미상") / len(od)))
L.append("")


def robust(part, key):
    d = days(part, key)
    v = sorted(d.values())
    tot = {}
    for e in part:
        tot[e["날"]] = tot.get(e["날"], 0.0) + key(e)
    s_ = sum(tot.values())
    top = max(tot.items(), key=lambda x: x[1])
    return "%s | %.0f%% | %s | %s" % (ci(v), 100.0 * sum(1 for x in v if x > 0) / len(v), pct(mean(v[:-5])) if len(v) > 5 else "-",
                                     "%s %s" % (top[0], ("%.0f%%" % (100 * top[1] / s_)) if s_ > 0 else "(합계 마이너스)"))


L += ["## d) 요약 — 상위 5일을 빼면, 그리고 종목별", "",
      "| 대상 | 기간 | 방식 | 사건 / 일수 | 일별 평균 [95%] | 플러스인 날 | 가장 좋은 5일을 뺀 일별 평균 | 신호 합계 기준 이익 상위 1일의 비중 |", "|---|---|---|---|---|---|---|---|"]
for gn, gf in GROUPS[1:]:
    for pn, pf in PER:
        part = [e for e in EV if gf(e) and pf(e)]
        if part:
            for kn, key in (("① 바로", K1), ("② 확인(신호당)", K2)):
                L.append("| %s | %s | %s | %d / %d | %s |" % (gn, pn, kn, len(part), len(days(part, key)), robust(part, key)))
L += ["", "### 종목별 — 조합, 전체 기간", "", "| 종목 | 사건 / 일수 | ① 일별 평균 [95%] | ② 신호당 일별 평균 | ② − ① 일별 [95%] | 2017–22 ① | 2023–26 ① |", "|---|---|---|---|---|---|---|"]
for m in sorted({e["종목"] for e in combo}):
    part = [e for e in combo if e["종목"] == m]
    L.append("| %s | %d / %d | %s | %s | %s | %s | %s |" % (m, len(part), len(days(part, K1)), ci(dv(part, K1)), pct(mean(dv(part, K2))), ci(dv(part, DIFF)),
                                                    pct(mean(dv([e for e in part if e["날"] < "2023"], K1))), pct(mean(dv([e for e in part if e["날"] >= "2023"], K1)))))
L.append("")
(OUT / "crash9_body.md").write_text("\n".join(L), encoding="utf-8")
print("\n".join(L[:40]))
