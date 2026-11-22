# -*- coding: utf-8 -*-
"""1분 급등 — 1년치에서 '이긴 신호의 공통점' 찾기 (관찰·선별용).
신호: 1분봉 시가 대비 종가 +1% 이상이고 호가 3칸 이상 움직인 봉(한두 칸짜리는 호가 사이를 오간 것이라 뺀다). 같은 종목 10분 안 재신호는 세지 않는다.
진입: 신호 봉 다음에 거래가 난 첫 1분봉 시가. 성적: 진입 뒤 10분 시점 종가(비용 뒤). 3·5·30·60분도 함께 적는다.
구간은 앞 구간(가장 오래된 8개월)의 3분위로 정하고 뒤 구간(최근 4개월)에서 같은지 본다. 사용: surge.py DATA(c1_*.jsonl.gz) REPORT.md"""
import bisect
import datetime as dt
import gzip
import json
import math
import pickle
import statistics as st
import sys
from array import array
from pathlib import Path

DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2])
UNI = ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-WAVES", "KRW-AXS", "KRW-ETC", "KRW-CHZ", "KRW-SAND", "KRW-IQ", "KRW-APT",
       "KRW-ADA", "KRW-GMT", "KRW-KAVA", "KRW-POL", "KRW-TRX", "KRW-CELO", "KRW-ARK", "KRW-ALGO")
BTC = "KRW-BTC"
TH, TICKS, COOL, VOL_N, EXTRA = 0.01, 3, 10, 20, 0.003
HZ = (3, 5, 10, 30, 60)
CUT = sys.argv[3] if len(sys.argv) > 3 else "2026-07-20"                                           # 앞 구간(구간을 정함) < CUT ≤ 뒤 구간(확인)
UTC = dt.timezone.utc


def load(m):
    rows = {}
    try:
        with gzip.open(DATA / ("c1_%s.jsonl.gz" % m), "rt") as g:
            for line in g:
                try:
                    page = json.loads(line)
                except ValueError:
                    continue
                for r in page:
                    rows[r[0]] = r
    except (EOFError, OSError):
        pass
    ts = sorted(rows)
    col = lambda k: array("d", [float(rows[t][k]) for t in ts])
    return {"t": array("q", ts), "o": col(1), "h": col(2), "l": col(3), "c": col(4), "v": col(6)}


def tick_at(B, k):
    best = None
    for j in range(max(1, k - 120), min(len(B["t"]), k + 11)):
        for d in (abs(B["c"][j] - B["o"][j]), B["h"][j] - B["l"][j], abs(B["c"][j] - B["c"][j - 1])):
            d = round(d, 10)
            if d > 0 and (best is None or d < best):
                best = d
    return best


def close_at(B, t):
    i = bisect.bisect_right(B["t"], t) - 1
    return B["c"][i] if i >= 0 else None


def ret(B, t, k):
    a, b = close_at(B, t), close_at(B, t - k)
    return a / b - 1 if a and b else None


# 1차: 종목마다 신호를 잡고, 같은 분에 오른 종목 수를 센다
UPC, cand = {}, {}
for m in UNI:
    B = load(m)
    t, o, c = B["t"], B["o"], B["c"]
    out, last = [], -10 ** 9
    for k in range(VOL_N + 1, len(t) - 1):
        if o[k] <= 0:
            continue
        r = c[k] / o[k] - 1
        if r >= 0.005:
            UPC[t[k]] = UPC.get(t[k], 0) + 1
        if r >= TH and t[k] - last >= COOL:
            tk = tick_at(B, k)
            if tk and round((c[k] - o[k]) / tk) >= TICKS:
                last = t[k]
                out.append(k)
    cand[m] = out
    print("%s 1분봉 %d, 신호 %d" % (m[4:], len(t), len(out)), flush=True)
    del B

# 2차: 지표와 성적
BB = load(BTC)
END_T = BB["t"][-1]
SIG = []
for m in UNI:
    B = BB if m == BTC else load(m)
    t, o, h, l, c, v = B["t"], B["o"], B["h"], B["l"], B["c"], B["v"]
    cost = 0.0007 if m in (BTC, "KRW-ETH") else 0.001
    for k in cand[m]:
        e = k + 1
        te, entry = t[e], o[e]
        if entry <= 0 or te + HZ[-1] > END_T:
            continue
        netr, hi, lo = {}, {}, {}
        for w in HZ:
            j = bisect.bisect_left(t, te + w) - 1                # 진입부터 w분 안의 마지막 거래 봉
            netr[w] = c[j] / entry * (1 - cost) / (1 + cost) - 1
            hi[w], lo[w] = max(h[e:j + 1]) / entry - 1, min(l[e:j + 1]) / entry - 1
        avg = sum(v[k - VOL_N:k]) / VOL_N
        up = 0
        while up < 30 and c[k - up] > o[k - up]:
            up += 1
        ref = [v[j] for j in range(max(0, k - 1440), k)]
        tk = tick_at(B, k)
        hour = (t[k] // 60 + 9) % 24
        f = {"신호 봉 1분 수익": c[k] / o[k] - 1, "거래대금 배수(직전 20봉 평균 대비)": v[k] / avg if avg > 0 else None,
             "신호 봉 거래대금(로그)": math.log10(v[k]) if v[k] > 0 else None,
             "평소 1분 거래대금(로그, 직전 1440봉 중앙값)": math.log10(st.median(ref)) if ref and st.median(ref) > 0 else None,
             "직전 5분 수익(신호 봉 앞)": ret(B, t[k] - 1, 5), "직전 15분 수익": ret(B, t[k] - 1, 15), "직전 1시간 수익": ret(B, t[k] - 1, 60),
             "직전 24시간 수익": ret(B, t[k] - 1, 1440), "연속 양봉 수": float(up),
             "봉 안 종가 위치(0=저가,1=고가)": (c[k] - l[k]) / (h[k] - l[k]) if h[k] > l[k] else None,
             "고가에서 밀린 폭": c[k] / h[k] - 1, "신호 봉 고저 폭": h[k] / l[k] - 1 if l[k] > 0 else None,
             "24시간 고점 대비": c[k] / max(h[max(0, k - 1440):k + 1]) - 1, "호가 한 칸 크기(가격 대비)": tk / o[k] if tk else None,
             "진입까지 빈 분": float(te - t[k] - 1), "진입가가 신호 종가보다 비싼 정도": entry / c[k] - 1,
             "같은 분 +0.5% 이상 종목 수": float(UPC.get(t[k], 0)),
             "BTC 직전 5분 수익": ret(BB, t[k], 5), "BTC 직전 1시간 수익": ret(BB, t[k], 60),
             "시간대(한국)": "%02d~%02d시" % (hour // 6 * 6, hour // 6 * 6 + 6), "종목": m[4:]}
        SIG.append({"m": m, "t": t[k], "day": dt.datetime.fromtimestamp(te * 60, tz=UTC).strftime("%Y-%m-%d"), "net": netr, "hi": hi, "lo": lo, "f": f})
    if m != BTC:
        del B
pickle.dump(SIG, open(str(OUT) + ".pkl", "wb"))
print("신호 %d" % len(SIG), flush=True)


# ---------------------------------------------------------------- 통계 (날마다 묶은 평균)
def pct(v, d=2):
    return "-" if v is None else ("%+." + str(d) + "f%%") % (100 * v)


def mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def stat(sigs, w=10):
    by = {}
    for s in sigs:
        by.setdefault(s["day"], []).append(s["net"][w])
    d = [mean(x) for x in by.values()]
    if not d:
        return 0, 0, None, None, None, None
    m = mean(d)
    hw = 1.96 * st.stdev(d) / math.sqrt(len(d)) if len(d) >= 30 else None
    return len(sigs), len(d), mean([1.0 if s["net"][w] > 0 else 0.0 for s in sigs]), m, None if hw is None else m - hw, None if hw is None else m + hw


def cell(x):
    n, days, win, m, lo, hi = x
    return "-" if not n else "%d/%d일 · %.0f%% · %s [%s, %s]" % (n, days, 100 * win, pct(m), pct(lo), pct(hi))


parts = [("앞 구간 (~%s)" % CUT, [s for s in SIG if s["day"] < CUT]), ("뒤 구간 (%s~)" % CUT, [s for s in SIG if s["day"] >= CUT])]
days_all = sorted({s["day"] for s in SIG})
L = ["# 1분 급등 긴 기간 검토 — 이긴 신호의 공통점 (고정 20종목, %s ~ %s)" % (days_all[0] if days_all else "-", days_all[-1] if days_all else "-"), "",
     "## 미리 적은 기준 (결과를 보기 전에 적음)", "",
     "1. 신호 = 1분봉 +1% 이상이면서 호가 3칸 이상. 성적 = 진입 뒤 10분 시점 종가, 비용 뒤. 3·5·30·60분은 참고.",
     "2. 지표마다 앞 구간의 3분위로 구간을 정하고 뒤 구간에 그대로 적용한다.",
     "3. **통과** = 두 구간 모두 30일 이상이고 10분 비용 뒤 일별 평균이 +0.3%p(급등 순간의 추가 체결 손실 가정) 위. **강한 통과** = 통과 + 두 구간 모두 95% 하한이 0 위.",
     "4. 통과는 '추적할 후보'이지 증명이 아니다. 본 구간 수와 통과 수를 같이 적는다.", "",
     "표의 칸 = 신호 수/일수 · 10분 승률 · 10분 비용 뒤 일별 평균 [95% 구간].", "",
     "## 0. 기본 성적", "", "| 구간 | 신호/일수 · 승률 · 10분 [95%] | 3분 | 5분 | 30분 | 60분 | 10분 최고 상승 중앙값 | 10분 최저 하락 중앙값 |", "|---|---|---|---|---|---|---|---|"]
for name, part in parts:
    if part:
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (name, cell(stat(part)), pct(stat(part, 3)[3]), pct(stat(part, 5)[3]), pct(stat(part, 30)[3]),
                                                           pct(stat(part, 60)[3]), pct(st.median([s["hi"][10] for s in part])), pct(st.median([s["lo"][10] for s in part]))))
L.append("")

RAWNUM = ("거래대금 배수(직전 20봉 평균 대비)", "신호 봉 거래대금(로그)", "평소 1분 거래대금(로그, 직전 1440봉 중앙값)", "연속 양봉 수", "봉 안 종가 위치(0=저가,1=고가)",
          "진입까지 빈 분", "같은 분 +0.5% 이상 종목 수")
fmt = lambda name, v: ("%.2f" % v) if name in RAWNUM else pct(v)


def bins(name):
    base = [s["f"].get(name) for s in parts[0][1]]
    base = [x for x in base if x is not None]
    if not base:
        return []
    if isinstance(base[0], str):
        return [(v, lambda s, v=v: s["f"].get(name) == v) for v in sorted(set(base))]
    xs = sorted(base)
    a, b = xs[len(xs) // 3], xs[2 * len(xs) // 3]
    g = lambda s: s["f"].get(name)
    if a == b:
        return [("%s 이하" % fmt(name, a), lambda s: g(s) is not None and g(s) <= a), ("%s 초과" % fmt(name, a), lambda s: g(s) is not None and g(s) > a)]
    return [("낮음 (%s 이하)" % fmt(name, a), lambda s: g(s) is not None and g(s) <= a), ("중간", lambda s: g(s) is not None and a < g(s) <= b),
            ("높음 (%s 초과)" % fmt(name, b), lambda s: g(s) is not None and g(s) > b)]


names, passed, tested = [], [], 0
for s in SIG:
    for n in s["f"]:
        if n not in names:
            names.append(n)
body = ["## 1. 지표별 구간 성적", "", "| 지표 | 구간 | %s | %s | 판정 |" % (parts[0][0], parts[1][0]), "|---|---|---|---|---|"]
for name in names:
    for label, pick in bins(name):
        sts = [stat([s for s in part if pick(s)]) for _n, part in parts]
        tested += 1
        ok = all(x[1] >= 30 and x[3] is not None and x[3] > EXTRA for x in sts)
        strong = ok and all(x[4] is not None and x[4] > 0 for x in sts)
        if ok:
            passed.append((name, label, sts, strong))
        body.append("| %s | %s | %s | %s | %s |" % (name, label, cell(sts[0]), cell(sts[1]), "**강한 통과**" if strong else ("통과" if ok else "")))
body.append("")
head = ["## 요약 — 통과한 구간 (본 구간 %d개 중 %d개, 강한 통과 %d개)" % (tested, len(passed), sum(1 for p in passed if p[3])), "",
        "| 지표 | 구간 | %s | %s | 판정 |" % (parts[0][0], parts[1][0]), "|---|---|---|---|---|"]
for name, label, sts, strong in passed:
    head.append("| %s | %s | %s | %s | %s |" % (name, label, cell(sts[0]), cell(sts[1]), "**강한 통과**" if strong else "통과"))
head.append("")
i0 = L.index("## 0. 기본 성적")
L = L[:i0] + head + L[i0:] + body
OUT.write_text("\n".join(L), encoding="utf-8")
print("\n".join(head))
