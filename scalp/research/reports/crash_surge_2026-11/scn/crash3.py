# -*- coding: utf-8 -*-
"""급락 뒤 반등 — 같은 사건에서 '바로 진입' 과 '첫 5분봉 반등 확인 뒤 진입' 을 나란히 비교, 그리고 4시간·24시간 조합 재현. 연구용.
자료: 2023-01 ~ 2026-11 고정 20종목의 알트 15분 급락 신호와 그 주변 5분봉(이미 받은 것), 2017~ 15분봉 신호(조합 재현).
정의 (결과를 보기 전에 고정, 2026-11-21):
  사건        알트(BTC 제외 19종목)의 완료된 15분봉 종가 수익 ≤ −3%. 같은 종목은 60분 안에 다시 세지 않는다(기존 정의). 신호 시각 = 그 봉 마감.
  거래대금 3배  신호 봉 거래대금 ≥ 직전 15분봉 96개 중앙값 × 3 (기존 정의).
  BTC 신호     BTC 15분 종가 기준 1시간 수익이 −1.821% 이하로 처음 내려간 봉(기존 정의).
  구분 (서로 겹치지 않게, 위에서부터 먼저 해당하는 것):
    후행  알트 급락 봉의 15~60분 전(1~4봉 전)에 BTC 신호가 있었다  [기존 3번]
    동반  후행이 아니고, 알트 급락 봉 마감 때 BTC 1시간 수익 ≤ −1.821%  [기존 2번에서 후행을 뺀 것]
    알트만 그 밖
  ① 바로 진입  신호 직후 첫 5분봉 시가.
  ② 확인 진입  신호 뒤 5분봉 가운데 종가가 바로 앞 5분봉 종가보다 높은 첫 봉이 나오면 그다음 5분봉 시가(기존 정의). 최대 대기 30분
              (신호 뒤 30분 안에 마감한 봉까지만 확인 → 늦어도 신호 뒤 30분에 진입). 그 안에 없으면 미진입.
  청산        두 방식 모두 원래 신호 뒤 60분(12번째 5분봉 종가). 미진입은 손익 0 으로 전체 신호당 성적에 넣는다.
  비용        편도 ETH 0.07%, 그 밖 0.10% (BTC 0.07%). 추가 슬리피지 0 — 따로 적는다.
사용: crash3.py REBOUND_DATA LONG_PKL OUT_DIR"""
import csv
import datetime as dt
import gzip
import json
import math
import pickle
import statistics as st
import sys
from pathlib import Path

RD, LONG_PKL, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
BTC, ETH, TH, VOL_X, WAIT_BARS, CUT = "KRW-BTC", "KRW-ETH", -0.01821, 3.0, 6, "2025-01-01"
COST = {BTC: 0.0007, ETH: 0.0007}
A4H, B24 = -0.0592, -0.0173
UTC = dt.timezone.utc
T975 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101,
        2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)
S = pickle.loads((RD / "signals.pkl").read_bytes())
P15 = pickle.loads((RD / "panel15.pkl").read_bytes())
IDX = {m: {q: i for i, q in enumerate(b["ts"])} for m, b in P15.items()}
M5 = {}


def m5(m):
    if m not in M5:
        d = {}
        for line in (RD / ("m5_%s.jsonl" % m)).read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            d.update({int(k): v for k, v in rec["bars"].items()})
        M5[m] = d
    return M5[m]


def bar(m, x):
    """분 x 에서 시작하는 5분봉. 거래가 없어 빠졌으면 직전 30분 안의 마지막 종가로 채운다. 없으면 None."""
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


def ret15(m, q, k):
    i, j = IDX[m].get(q), IDX[m].get(q - k)
    if i is None or j is None or P15[m]["c"][j] <= 0:
        return None
    return P15[m]["c"][i] / P15[m]["c"][j] - 1


BTC_Q = sorted(s["q"] for s in S["btc"])
BTC_SET = set(BTC_Q)
EV, skipped = [], 0
for s in S["alt"]:
    m, q = s["m"], s["q"]
    t = (q + 1) * 15
    bars = [bar(m, t + 5 * j) for j in range(12)]
    pre = bar(m, t - 5)
    bb = [bar(BTC, t + 5 * j) for j in range(12)]
    if any(x is None for x in bars) or pre is None or bars[0][0] <= 0:
        skipped += 1
        continue
    e1, X = bars[0][0], bars[11][3]
    hi = max(range(12), key=lambda j: (bars[j][1], -j))
    lo = min(range(12), key=lambda j: (bars[j][2], j))
    peak = bars[hi][1]
    after = [bars[j][2] for j in range(hi + 1, 12)]
    e2 = wait = None
    prevc = pre[3]
    for j in range(WAIT_BARS):
        if bars[j][3] > prevc:
            wait, e2 = 5 * (j + 1), bars[j + 1][0]
            break
        prevc = bars[j][3]
    lag = any((q - d) in BTC_SET for d in range(1, 5))
    kind = "후행" if lag else ("동반" if s["btc1h"] is not None and s["btc1h"] <= TH else "알트만")
    ev = {"사건 ID": "%s_%s" % (m[4:], dt.datetime.fromtimestamp(t * 60, tz=UTC).strftime("%Y%m%dT%H%M")), "종목": m[4:], "m": m, "q": q,
          "신호 시각(UTC)": dt.datetime.fromtimestamp(t * 60, tz=UTC).strftime("%Y-%m-%d %H:%M"), "날": s["day"], "구분": kind,
          "15분 수익": s["r15"], "거래대금 배수": s["mult"], "3배 이상": int(s["mult"] >= VOL_X), "BTC 1시간": s["btc1h"], "BTC 24시간": s["btc24"],
          "알트 4시간": ret15(m, q, 16), "기존 2번(동반)": int(s["btc1h"] is not None and s["btc1h"] <= TH), "기존 3번(후행)": int(lag),
          "① 진입가": e1, "① 5분": net(m, bars[0][3] / e1 - 1), "① 15분": net(m, bars[2][3] / e1 - 1), "① 30분": net(m, bars[5][3] / e1 - 1), "① 60분": net(m, X / e1 - 1),
          "최대 반등": peak / e1 - 1, "고점까지 분": 5 * hi + 5, "추가 하락": bars[lo][2] / e1 - 1, "저점까지 분": 5 * lo + 5,
          "고점 뒤 되밀림": (min(after) / peak - 1) if after else None, "순서": "고점 먼저" if hi < lo else ("저점 먼저" if lo < hi else "순서 미상"),
          "② 진입": int(e2 is not None), "② 기다린 분": wait, "② 진입가": e2, "② 진입가 ÷ ① 진입가": (e2 / e1 - 1) if e2 else None,
          "② 60분(신호 기준 청산)": net(m, X / e2 - 1) if e2 else None}
    ev["② 전체 신호 기준"] = ev["② 60분(신호 기준 청산)"] if e2 else 0.0
    if all(x is not None for x in bb) and bb[0][0] > 0:
        for lab, j in (("5분", 0), ("15분", 2), ("30분", 5), ("60분", 11)):
            ev["BTC " + lab] = net(BTC, bb[j][3] / bb[0][0] - 1)
    EV.append(ev)
print("사건 %d (5분봉이 모자라 뺀 것 %d)" % (len(EV), skipped), flush=True)
cols = [k for k in EV[0] if k not in ("m", "q")] + [k for k in ("BTC 5분", "BTC 15분", "BTC 30분", "BTC 60분") if k not in EV[0]]
with gzip.open(OUT / "crash_events.csv.gz", "wt", newline="", encoding="utf-8") as g:
    w = csv.DictWriter(g, fieldnames=cols, extrasaction="ignore")
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


def by_day(evs, val, dk="날", qk="q"):
    g = {}
    for e in evs:
        v = val(e)
        if v is not None:
            g.setdefault((e[dk], e[qk]), []).append(v)
    d = {}
    for (day, _q), v in g.items():
        d.setdefault(day, []).append(mean(v))
    return [mean(v) for v in d.values()]


def ci(d):
    d = [x for x in d if x is not None]
    if not d:
        return "-"
    m_ = mean(d)
    if len(d) < 2:
        return pct(m_)
    hw = (T975[len(d) - 2] if len(d) - 1 <= 30 else 1.96) * st.stdev(d) / math.sqrt(len(d))
    return "%s [%s, %s]" % (pct(m_), pct(m_ - hw), pct(m_ + hw))


def conc(evs, key):
    """이익 집중도: 합이 플러스면 '이익 상위 1일(종목)이 전체 순이익에서 차지하는 비율', 아니면 신호가 가장 몰린 곳의 신호 비중."""
    out = []
    for k in ("날", "종목"):
        tot, cnt = {}, {}
        for e in evs:
            tot[e[k]] = tot.get(e[k], 0.0) + e[key]
            cnt[e[k]] = cnt.get(e[k], 0) + 1
        s_ = sum(tot.values())
        if s_ > 0:
            a, b = max(tot.items(), key=lambda x: x[1])
            out.append("%s 이익의 %.0f%%" % (a, 100 * b / s_))
        else:
            a, b = max(cnt.items(), key=lambda x: x[1])
            out.append("%s 신호의 %.0f%%" % (a, 100.0 * b / len(evs)))
    return " · ".join(out)


PER = (("2023–24", lambda e: e["날"] < CUT), ("2025–26", lambda e: e["날"] >= CUT), ("전체", lambda e: True))
KINDS = ("동반", "후행", "알트만")
L = ["## E. 급락 사건 재현과 구분", "",
     "| 항목 | 어제 보고 | 이번 재계산 |", "|---|---|---|",
     "| 알트 급락 신호(5분봉 있는 것) | 2,271 | %d |" % len(EV),
     "| 기존 2번: 급락 봉에서 BTC 1시간 ≤ −1.821%% | 415 | %d |" % sum(e["기존 2번(동반)"] for e in EV),
     "| 기존 3번: BTC 신호 15~60분 뒤 | 173 | %d |" % sum(e["기존 3번(후행)"] for e in EV),
     "| 기존 4번: BTC 1시간 > −1.821%% | 1,856 | %d |" % sum(1 for e in EV if e["BTC 1시간"] is not None and e["BTC 1시간"] > TH), "",
     "이번 구분은 서로 겹치지 않는다: " + ", ".join("%s %d" % (k, sum(1 for e in EV if e["구분"] == k)) for k in KINDS) +
     ". 기존 2번과 3번은 %d개가 겹쳤고(이번에는 후행으로), 기존 3번 중 %d개는 급락 봉 때 BTC 가 이미 −1.821%% 위로 돌아와 있었다(기존 4번과 겹침)." % (
         sum(1 for e in EV if e["기존 2번(동반)"] and e["기존 3번(후행)"]), sum(1 for e in EV if e["기존 3번(후행)"] and not e["기존 2번(동반)"])), "",
     "## F. 같은 사건에서 ① 바로 진입 vs ② 첫 5분봉 반등 확인 뒤 진입 (둘 다 신호 뒤 60분 청산, 비용 뒤)", "",
     "'날짜별' = 같은 15분봉의 사건들을 먼저 평균 → 날마다 평균 → 그 평균과 95% 구간. ② 전체 신호당 = 미진입을 손익 0 으로 넣은 값. ② − ① 도 같은 방식.", "",
     "| 구분 | 거래대금 | 기간 | 사건 / 일수 | ① 평균 / 중앙 · 승률 | ① 날짜별 [95%] | ② 진입 / 미진입 | ② 기다린 분(중앙) · 진입가 차이(중앙) | ② 진입 건당 평균 | ② 전체 신호당 평균 | ② 날짜별 [95%] | ② − ① 날짜별 [95%] | 같은 시각 BTC 60분(날짜별) |",
     "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
G = ["## G. 급락 신호 뒤 60분의 움직임 (① 진입가 대비, 전체 기간)", "",
     "5·15·30·60분 값은 그 시점에 팔았을 때의 비용 뒤 날짜별 평균. 최대 반등·추가 하락은 비용 빼기 전 중앙값(걸린 분 중앙값). 최고점에 팔 수 있었다는 뜻이 아니다.", "",
     "| 구분 | 거래대금 | 사건 | 5분 | 15분 | 30분 | 60분 | 같은 시각 BTC 5 / 15 / 30 / 60분 | 최대 반등 (분) | 추가 하락 (분) | 고점 뒤 되밀림 | 고점 먼저 / 저점 먼저 / 순서 미상 | 이익 집중도 (①) |",
     "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
for kind in KINDS + ("전체",):
    for vol in (0, 1):
        base = [e for e in EV if (kind == "전체" or e["구분"] == kind) and (e["3배 이상"] or not vol)]
        vl = "3배 이상" if vol else "제한 없음"
        for pn, pf in PER:
            part = [e for e in base if pf(e)]
            if not part:
                continue
            ent = [e for e in part if e["② 진입"]]
            L.append("| %s | %s | %s | %d / %d | %s / %s · %.0f%% | %s | %d / %d | %s분 · %s | %s | %s | %s | %s | %s |" % (
                kind, vl, pn, len(part), len(by_day(part, lambda e: e["① 60분"])), pct(mean([e["① 60분"] for e in part])), pct(med([e["① 60분"] for e in part])),
                100 * mean([1.0 if e["① 60분"] > 0 else 0.0 for e in part]), ci(by_day(part, lambda e: e["① 60분"])), len(ent), len(part) - len(ent),
                "-" if not ent else "%g" % med([e["② 기다린 분"] for e in ent]), pct(med([e["② 진입가 ÷ ① 진입가"] for e in ent])),
                pct(mean([e["② 60분(신호 기준 청산)"] for e in ent])), pct(mean([e["② 전체 신호 기준"] for e in part])),
                ci(by_day(part, lambda e: e["② 전체 신호 기준"])), ci(by_day(part, lambda e: e["② 전체 신호 기준"] - e["① 60분"])),
                pct(mean(by_day(part, lambda e: e.get("BTC 60분"))))))
        if base:
            od = [e["순서"] for e in base]
            dm = lambda k: pct(mean(by_day(base, lambda e, k=k: e.get(k))))
            G.append("| %s | %s | %d | %s | %s | %s | %s | %s / %s / %s / %s | %s (%g) | %s (%g) | %s | %.0f%% / %.0f%% / %.0f%% | %s |" % (
                kind, vl, len(base), dm("① 5분"), dm("① 15분"), dm("① 30분"), dm("① 60분"), dm("BTC 5분"), dm("BTC 15분"), dm("BTC 30분"), dm("BTC 60분"),
                pct(med([e["최대 반등"] for e in base])), med([e["고점까지 분"] for e in base]), pct(med([e["추가 하락"] for e in base])), med([e["저점까지 분"] for e in base]),
                pct(med([e["고점 뒤 되밀림"] for e in base])), 100.0 * od.count("고점 먼저") / len(od), 100.0 * od.count("저점 먼저") / len(od),
                100.0 * od.count("순서 미상") / len(od), conc(base, "① 60분")))

# ---------------------------------------------------------------- 4시간·24시간 조합 재현 (2017~, 15분봉)
A = pickle.load(open(LONG_PKL, "rb"))["A"]
P3 = (("2017~22", "0000", "2023"), ("2023–24", "2023", "2025"), ("2025–26", "2025", "9999"))
f4 = lambda s: s["f"].get("4시간 수익") is not None and s["f"]["4시간 수익"] <= A4H
fb = lambda s: s["f"].get("BTC 24시간 수익") is not None and s["f"]["BTC 24시간 수익"] <= B24


def cell(sigs):
    if not sigs:
        return "-"
    d = by_day(sigs, lambda s: s["net"][4], "day", "q")
    return "%d/%d일 · %.0f%% · %s" % (len(sigs), len(d), 100 * mean([1.0 if s["net"][4] > 0 else 0.0 for s in sigs]), ci(d))


H = ["## H. 플러스였던 조합 재현 — 조건을 하나씩 더하면 (2017~, 15분봉, 다음 봉 시가 매수·60분 뒤 매도, 비용 뒤)", "",
     "칸 = 신호/일수 · 승률 · 날짜별 평균 [95%]. 알트 4시간 조건 = 그 종목 4시간 수익 ≤ −5.92%, BTC 24시간 조건 = BTC 24시간 수익 ≤ −1.73%.", "",
     "| 조건 | 2017~22 | 2023–24 | 2025–26 |", "|---|---|---|---|"]
for name, f in (("기본 급락만 (알트 15분 −3%)", lambda s: True), ("+ 알트 4시간 조건", f4), ("+ BTC 24시간 조건", fb), ("+ 둘 다", lambda s: f4(s) and fb(s)),
                ("(나눠 보기) 4시간만 충족, BTC 조건 불충족", lambda s: f4(s) and not fb(s)), ("(나눠 보기) BTC 조건만 충족, 4시간 불충족", lambda s: fb(s) and not f4(s)),
                ("(나눠 보기) 둘 다 불충족", lambda s: not f4(s) and not fb(s))):
    H.append("| %s | %s |" % (name, " | ".join(cell([s for s in A if f(s) and a <= s["day"][:4] < b]) for _n, a, b in P3)))
both = [e for e in EV if e["알트 4시간"] is not None and e["알트 4시간"] <= A4H and e["BTC 24시간"] is not None and e["BTC 24시간"] <= B24]
H += ["", "### 조합(둘 다) 안에서 ① 바로 진입 vs ② 확인 뒤 진입 (2023–26, 5분봉, 신호 뒤 60분 청산)", "",
      "| 기간 | 사건 / 일수 | ① 날짜별 [95%] | ② 진입 / 미진입 | ② 전체 신호당 날짜별 [95%] | ② − ① 날짜별 [95%] | 이익 집중도 (①) |", "|---|---|---|---|---|---|---|"]
for pn, pf in PER:
    part = [e for e in both if pf(e)]
    if part:
        ent = sum(e["② 진입"] for e in part)
        H.append("| %s | %d / %d | %s | %d / %d | %s | %s | %s |" % (pn, len(part), len(by_day(part, lambda e: e["① 60분"])), ci(by_day(part, lambda e: e["① 60분"])), ent, len(part) - ent,
                                                           ci(by_day(part, lambda e: e["② 전체 신호 기준"])), ci(by_day(part, lambda e: e["② 전체 신호 기준"] - e["① 60분"])), conc(part, "① 60분")))
(OUT / "crash_report.md").write_text("\n".join(L + [""] + G + [""] + H + [""]), encoding="utf-8")
print("\n".join(L[:12]))
