# -*- coding: utf-8 -*-
"""1분 급등 — 여덟 조합(+0.5/1/1.5/2% × 거래량 3배 유무)과 직전 5분 모양 비교, 그리고 기존 결과(+1%·3틱, 3,443개) 재현. 연구용.
정의 (결과를 보기 전에 고정, 2026-11-21):
  신호      완료된 1분봉의 (종가 ÷ 시가 − 1) ≥ 기준. 같은 종목·같은 기준에서 신호 뒤 10분 안의 신호는 세지 않는다(기존 정의).
  거래량 3배  신호 봉 거래량(코인 수량) ≥ 직전 완료 1분봉 20개(거래가 난 봉) 평균 × 3 (기존 정의).
  진입      신호 봉 다음에 거래가 난 첫 1분봉의 시가(체결 추정값). 창 = 진입 분부터 1·3·5·10분.
  직전 5분   신호 봉 앞 5분(시각 기준). 거래 없는 분은 가격이 그대로였다고 본다.
  선행 상승  직전 5분 수익(신호 봉 직전 종가 ÷ 그 5분 전 종가 − 1) ≥ +0.5%  [제안 정의 — 기존 정의 없음]
  횡보 후 돌파 선행 상승이 아니고, 직전 5분 고저 폭 ≤ 0.8%(1,000원 미만) / 0.5%(1,000원 이상) [bot.py is_sideways_regime 의 폭 기준],
            그리고 신호 봉 종가 > 직전 5분 최고가.
  그 밖      위 둘이 아닌 것.
  비용      편도 BTC·ETH 0.07%, 그 밖 0.10%. 추가 슬리피지는 0 으로 두고 따로 적는다.
사용: surge8.py DATA(c1_*.jsonl.gz) OUT_DIR"""
import bisect
import csv
import datetime as dt
import gzip
import json
import math
import statistics as st
import sys
from array import array
from pathlib import Path

DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
UNI = ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-WAVES", "KRW-AXS", "KRW-ETC", "KRW-CHZ", "KRW-SAND", "KRW-IQ", "KRW-APT",
       "KRW-ADA", "KRW-GMT", "KRW-KAVA", "KRW-POL", "KRW-TRX", "KRW-CELO", "KRW-ARK", "KRW-ALGO")
THS, WINS, VOL_N, VOL_X, COOL, PRE = (0.005, 0.01, 0.015, 0.02), (1, 3, 5, 10), 20, 3.0, 10, 5
RUN_UP, CUT = 0.005, "2026-07-20"
UTC = dt.timezone.utc
iso = lambda t: dt.datetime.fromtimestamp(t * 60, tz=UTC).strftime("%Y-%m-%d %H:%M")


def load(m):
    rows = {}
    with gzip.open(DATA / ("c1_%s.jsonl.gz" % m), "rt") as g:
        for line in g:
            try:
                page = json.loads(line)
            except ValueError:
                continue
            for r in page:
                rows[r[0]] = r
    ts = sorted(rows)
    col = lambda k: array("d", [float(rows[t][k]) for t in ts])
    return array("q", ts), col(1), col(2), col(3), col(4), col(5)


def tick_at(o, h, l, c, k, n):
    best = None
    for j in range(max(1, k - 120), min(n, k + 11)):
        for d in (abs(c[j] - o[j]), h[j] - l[j], abs(c[j] - c[j - 1])):
            d = round(d, 10)
            if d > 0 and (best is None or d < best):
                best = d
    return best


ENDS = {}
rows_out, repro = [], []
for m in UNI:
    t, o, h, l, c, v = load(m)
    n = len(t)
    ENDS[m] = t[-1]
    cost = 0.0007 if m in ("KRW-BTC", "KRW-ETH") else 0.001
    last = {th: -10 ** 9 for th in THS}
    last_rep = -10 ** 9
    for k in range(VOL_N + 1, n - 1):
        if o[k] <= 0:
            continue
        r = c[k] / o[k] - 1
        if r < THS[0]:
            continue
        e = k + 1
        te, entry = t[e], o[e]
        if entry <= 0:
            continue
        tk = tick_at(o, h, l, c, k, n)
        nt = round((c[k] - o[k]) / tk) if tk else None
        # 기존 결과 재현용: +1%·3틱 이상·10분 재신호 제외·진입 뒤 60분 자료가 있는 것
        if r >= 0.01 and t[k] - last_rep >= COOL and nt is not None and nt >= 3:
            last_rep = t[k]
            repro.append((m, t[k], te, entry, bisect.bisect_left(t, te + 10) - 1, c[bisect.bisect_left(t, te + 10) - 1], cost))
        hit = [th for th in THS if r >= th and t[k] - last[th] >= COOL]
        if not hit or te + WINS[-1] > t[-1]:
            continue
        for th in hit:
            last[th] = t[k]
        avg = sum(v[k - VOL_N:k]) / VOL_N
        mult = v[k] / avg if avg > 0 else None
        # 직전 5분 모양 (시각 기준)
        i0 = bisect.bisect_left(t, t[k] - PRE)                # 직전 5분 창의 첫 봉
        base_i = i0 - 1                                       # 창 바로 앞의 마지막 거래 봉
        base = c[base_i] if base_i >= 0 else None
        prev_c = c[k - 1]
        pre_ret = prev_c / base - 1 if base else None
        if i0 <= k - 1:
            ph, pl = max(h[i0:k]), min(l[i0:k])
            if base:
                ph, pl = max(ph, base), min(pl, base)
        else:
            ph = pl = base
        pre_rng = (ph - pl) / pl if ph and pl else None
        thr = 0.008 if c[k] < 1000 else 0.005
        if pre_ret is not None and pre_ret >= RUN_UP:
            cat = "선행 상승"
        elif pre_rng is not None and pre_rng <= thr and ph is not None and c[k] > ph:
            cat = "횡보 후 돌파"
        else:
            cat = "그 밖"
        rec = {"종목": m[4:], "신호 시각(UTC)": iso(t[k]), "날": dt.datetime.fromtimestamp(te * 60, tz=UTC).strftime("%Y-%m-%d"),
               "신호 봉 수익": r, "거래량 배수": mult, "거래량 3배": int(bool(mult and mult >= VOL_X)), "직전 5분 수익": pre_ret, "직전 5분 고저 폭": pre_rng,
               "모양": cat, "호가 한 칸": tk / o[k] if tk else None, "움직인 칸 수": nt, "진입 시각(UTC)": iso(te), "진입가": entry, "진입까지 빈 분": te - t[k] - 1}
        for w in WINS:
            j = bisect.bisect_left(t, te + w) - 1
            hi_i = lo_i = e
            for x in range(e, j + 1):
                if h[x] > h[hi_i]:
                    hi_i = x
                if l[x] < l[lo_i]:
                    lo_i = x
            rec["%d분 수익(비용 뒤)" % w] = c[j] / entry * (1 - cost) / (1 + cost) - 1
            rec["%d분 수익(비용 전)" % w] = c[j] / entry - 1
            rec["%d분 최고 상승" % w] = h[hi_i] / entry - 1
            rec["%d분 최대 하락" % w] = l[lo_i] / entry - 1
            rec["%d분 고점→끝 되밀림" % w] = c[j] / h[hi_i] - 1
            rec["%d분 고점까지 분" % w] = t[hi_i] - te + 1
            rec["%d분 고점 전 밀림" % w] = (min(l[e:hi_i]) / entry - 1) if hi_i > e else None     # 고점이 진입 봉 안이면 순서 미상
            rec["%d분 순서" % w] = "고점 먼저" if hi_i < lo_i else ("저점 먼저" if lo_i < hi_i else "순서 미상")
        for th in hit:
            rows_out.append(dict(rec, **{"기준": th}))
    print("%s 1분봉 %d" % (m[4:], n), flush=True)

END_T = ENDS["KRW-BTC"]
repro = [x for x in repro if x[2] + 60 <= END_T]
cols = ["기준"] + [k for k in rows_out[0] if k != "기준"]
with gzip.open(OUT / "surge_signals.csv.gz", "wt", newline="", encoding="utf-8") as g:
    w = csv.DictWriter(g, fieldnames=cols)
    w.writeheader()
    for r_ in rows_out:
        w.writerow({k: ("" if v is None else ("%.6f" % v if isinstance(v, float) else v)) for k, v in r_.items()})


# ---------------------------------------------------------------- 요약
def pct(v, d=2):
    return "-" if v is None else ("%+." + str(d) + "f%%") % (100 * v)


def mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def med(v):
    v = [x for x in v if x is not None]
    return st.median(v) if v else None


def day_ci(part, key):
    by = {}
    for s in part:
        by.setdefault(s["날"], []).append(s[key])
    d = [mean(x) for x in by.values()]
    m_ = mean(d)
    if len(d) < 30:
        return "%s (날 %d, 구간 생략)" % (pct(m_), len(d))
    hw = 1.96 * st.stdev(d) / math.sqrt(len(d))
    return "%s [%s, %s]" % (pct(m_), pct(m_ - hw), pct(m_ + hw))


def share(part, key):
    cnt = {}
    for s in part:
        cnt[s[key]] = cnt.get(s[key], 0) + 1
    k, v = max(cnt.items(), key=lambda x: x[1])
    return "%s %.0f%%" % (k, 100.0 * v / len(part))


L = ["## A. 기존 결과 재현 — 1분 +1% 이상·호가 3칸 이상 (고정 20종목, 1년)", ""]
rep_days = {}
for m, ts_, te, entry, j, cj, cost in repro:
    rep_days.setdefault(dt.datetime.fromtimestamp(te * 60, tz=UTC).strftime("%Y-%m-%d"), []).append(cj / entry * (1 - cost) / (1 + cost) - 1)
a = [mean(v) for d, v in rep_days.items() if d < CUT]
b = [mean(v) for d, v in rep_days.items() if d >= CUT]
L += ["| 항목 | 어제 보고 | 이번 재계산(별도 코드) |", "|---|---|---|",
      "| 신호 수 | 3,443 | %d |" % len(repro), "| 날 수 | 317 | %d |" % len(rep_days),
      "| 앞 구간(~%s) 10분 비용 뒤 일별 평균 | −0.52%% | %s (%d일) |" % (CUT, pct(mean(a)), len(a)),
      "| 뒤 구간(%s~) 10분 비용 뒤 일별 평균 | −0.50%% | %s (%d일) |" % (CUT, pct(mean(b)), len(b)), ""]

L += ["## B. 여덟 조합 × 직전 5분 모양 — 진입 뒤 수익 (비용 뒤)", "",
      "한 사건이 여러 기준에 들어가므로 조합별 건수를 더하면 안 된다. '1~2칸' = 신호 봉이 호가 한두 칸만 움직인 신호의 비중(호가 사이를 오간 것일 수 있음).", "",
      "| 기준 | 거래량 | 모양 | 신호 | 날 | 1~2칸 | 1분 평균 / 중앙 | 3분 | 5분 | 10분 | 10분 날짜별 평균 [95%] | 10분 승률 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
P2 = ["## C. 같은 조합 — 진입 뒤 10분 동안의 움직임 (진입가 대비, 비용 빼기 전)", "",
      "최고 상승은 그 값에 팔 수 있었다는 뜻이 아니다. '고점 전 밀림' 은 고점이 진입 봉 밖에 있어 순서를 아는 신호만의 값이고, 고점이 진입 봉 안이면 순서 미상이다.", "",
      "| 기준 | 거래량 | 모양 | 신호 | 최고 상승 평균 / 중앙 | 최대 하락 평균 / 중앙 | 고점→끝 되밀림 중앙 | 고점까지 분(중앙) | 고점 전 밀림 중앙 | 고점이 진입 봉 안(순서 미상) | 가장 몰린 날 | 가장 몰린 종목 |",
      "|---|---|---|---|---|---|---|---|---|---|---|---|"]
P3 = ["## D. 조합별 1·3·5분 창의 움직임 (모양 구분 없이, 진입가 대비)", "",
      "| 기준 | 거래량 | 창 | 신호 | 최고 상승 평균 / 중앙 | 최대 하락 평균 / 중앙 | 고점→끝 되밀림 중앙 | 끝 수익(비용 뒤) 평균 / 중앙 | 고점 먼저 / 저점 먼저 / 순서 미상 |", "|---|---|---|---|---|---|---|---|---|"]
for th in THS:
    for vol in (0, 1):
        base = [s for s in rows_out if s["기준"] == th and (s["거래량 3배"] or not vol)]
        for cat in ("전체", "횡보 후 돌파", "선행 상승", "그 밖"):
            part = base if cat == "전체" else [s for s in base if s["모양"] == cat]
            tag = "| +%.1f%% | %s | %s " % (100 * th, "3배 이상" if vol else "제한 없음", cat)
            if not part:
                L.append(tag + "| 0 | | | | | | | | |")
                continue
            small = sum(1 for s in part if s["움직인 칸 수"] is not None and s["움직인 칸 수"] < 3)
            cells = ["%s / %s" % (pct(mean([s["%d분 수익(비용 뒤)" % w] for s in part])), pct(med([s["%d분 수익(비용 뒤)" % w] for s in part]))) for w in WINS]
            L.append(tag + "| %d | %d | %.0f%% | %s | %s | %.0f%% |" % (
                len(part), len({s["날"] for s in part}), 100.0 * small / len(part), " | ".join(cells), day_ci(part, "10분 수익(비용 뒤)"),
                100 * mean([1.0 if s["10분 수익(비용 뒤)"] > 0 else 0.0 for s in part])))
            unk = sum(1 for s in part if s["10분 고점 전 밀림"] is None)
            P2.append(tag + "| %d | %s / %s | %s / %s | %s | %s | %s | %.0f%% | %s | %s |" % (
                len(part), pct(mean([s["10분 최고 상승"] for s in part])), pct(med([s["10분 최고 상승"] for s in part])),
                pct(mean([s["10분 최대 하락"] for s in part])), pct(med([s["10분 최대 하락"] for s in part])), pct(med([s["10분 고점→끝 되밀림"] for s in part])),
                "%g" % med([s["10분 고점까지 분"] for s in part]), pct(med([s["10분 고점 전 밀림"] for s in part])), 100.0 * unk / len(part),
                share(part, "날"), share(part, "종목")))
        for w in WINS[:3]:
            if base:
                od = [s["%d분 순서" % w] for s in base]
                P3.append("| +%.1f%% | %s | %d분 | %d | %s / %s | %s / %s | %s | %s / %s | %.0f%% / %.0f%% / %.0f%% |" % (
                    100 * th, "3배 이상" if vol else "제한 없음", w, len(base), pct(mean([s["%d분 최고 상승" % w] for s in base])), pct(med([s["%d분 최고 상승" % w] for s in base])),
                    pct(mean([s["%d분 최대 하락" % w] for s in base])), pct(med([s["%d분 최대 하락" % w] for s in base])), pct(med([s["%d분 고점→끝 되밀림" % w] for s in base])),
                    pct(mean([s["%d분 수익(비용 뒤)" % w] for s in base])), pct(med([s["%d분 수익(비용 뒤)" % w] for s in base])),
                    100.0 * od.count("고점 먼저") / len(od), 100.0 * od.count("저점 먼저") / len(od), 100.0 * od.count("순서 미상") / len(od)))
uniq = len({(s["종목"], s["신호 시각(UTC)"]) for s in rows_out})
L += ["", "서로 다른 사건(종목·분) 수: %d. 자료 기간: %s ~ %s." % (uniq, min(s["날"] for s in rows_out), max(s["날"] for s in rows_out)), ""]
(OUT / "surge_report.md").write_text("\n".join(L + P2 + [""] + P3 + [""]), encoding="utf-8")
print("\n".join(L[:9]))
print("rows", len(rows_out), "unique", uniq)
