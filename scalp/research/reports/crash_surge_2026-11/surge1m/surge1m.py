# -*- coding: utf-8 -*-
"""2번 시나리오 (사용자 설계 2026-11-20): 1분 급등 뒤 추가 상승폭 — 관찰만.
신호: 1분봉 시가 대비 종가 상승률 ≥ +0.5 / 1 / 1.5 / 2%. 진입: 신호 봉 다음에 거래가 난 첫 1분봉의 시가.
관찰: 진입부터 1·3·5·10분 동안의 최고 상승폭(고가 최대 ÷ 진입가 − 1). 거래량 조건: 신호 봉 거래량 ≥ 직전 20개 1분봉 평균 × 3.
같은 종목·같은 기준에서 신호 뒤 10분 안의 신호는 다시 세지 않는다. 거래량 조건은 그 신호들 가운데 고른 부분집합이다.
고정 20종목, 최근 90일. 공개 시세만. 받은 1분봉은 OUT 에 저장해 다시 돌릴 때 이어 쓴다."""
import datetime as dt
import importlib.util
import math
import pickle
import statistics as st
import sys
import urllib.parse
from pathlib import Path

R = Path(__file__).resolve().parents[3]                   # scalp/research


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(R / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = _load("discover")
AM = D.AM
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path(sys.argv[2])
UNI = D.REPRO_UNIVERSE
END = dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc)
DAYS = 90
START = END - dt.timedelta(days=DAYS)
THS = (0.005, 0.01, 0.015, 0.02)
WINS = (1, 3, 5, 10)
VOL_N, VOL_X, COOL = 20, 3.0, 10
log = lambda s: print(s, flush=True)


def local_tick(bars, k):
    """신호 봉 주변(앞 120봉 ~ 뒤 10봉)에서 본 가장 작은 가격 차 = 그 가격대의 호가 단위로 본다."""
    best = None
    for j in range(max(1, k - 120), min(len(bars), k + 11)):
        _t, o, h, c, _v = bars[j]
        for d in (abs(c - o), abs(h - o), abs(h - c), abs(c - bars[j - 1][3])):
            d = round(d, 10)
            if d > 0 and (best is None or d < best):
                best = d
    return best


def minute(dt_):
    return int(dt_.timestamp() // 60)


def fetch(m):
    """[START, END) 의 1분봉 → (분, 시가, 고가, 종가, 거래량) 시간순. 디스크에 저장해 둔다."""
    p = OUT / ("%s.pkl" % m)
    if p.exists():
        return pickle.loads(p.read_bytes())
    t0, to, rows = minute(START), END, {}
    while True:
        got = AM.http_get("%s/v1/candles/minutes/1?market=%s&count=200&to=%s" % (AM.UPBIT, m, urllib.parse.quote(to.strftime("%Y-%m-%dT%H:%M:%SZ"))))
        if not got:
            break
        oldest = None
        for r in got:
            t = minute(AM.parse_utc(r["candle_date_time_utc"]))
            oldest = t if oldest is None else min(oldest, t)
            if t0 <= t < minute(END):
                rows[t] = (t, float(r["opening_price"]), float(r["high_price"]), float(r["trade_price"]), float(r["candle_acc_trade_volume"]))
        if oldest is None or oldest <= t0 or len(got) < 200:
            break
        to = dt.datetime.fromtimestamp(oldest * 60, tz=dt.timezone.utc)
    bars = [rows[t] for t in sorted(rows)]
    p.write_bytes(pickle.dumps(bars))
    return bars


sig = []                                                     # (기준, 종목, 신호 분, 거래량 조건, 진입 지연, {창: 최고 상승폭})
for m in UNI:
    bars = fetch(m)
    log("%s 1분봉 %d" % (m[4:], len(bars)))
    times = [b[0] for b in bars]
    last = {th: -10 ** 9 for th in THS}
    for k in range(VOL_N, len(bars) - 1):
        t, o, _h, c, v = bars[k]
        if o <= 0:
            continue
        r = c / o - 1
        if r < THS[0]:
            continue
        avg = sum(b[4] for b in bars[k - VOL_N:k]) / VOL_N
        vol_ok = avg > 0 and v >= VOL_X * avg
        e = k + 1                                            # 신호 봉 다음에 거래가 난 첫 봉
        te, entry = bars[e][0], bars[e][1]
        if entry <= 0:
            continue
        rise, close = {}, {}
        cost = 0.0007 if m in ("KRW-BTC", "KRW-ETH") else 0.001
        for w in WINS:
            win = [b for b in bars[e:e + w + 1] if b[0] < te + w]
            rise[w] = max(b[2] for b in win) / entry - 1
            close[w] = win[-1][3] / entry * (1 - cost) / (1 + cost) - 1      # 참고: 그 시점 종가에 팔았다면 (봉 기준 비용 뒤)
        tick = local_tick(bars, k)
        nt = round((c - o) / tick) if tick else None              # 신호 봉이 움직인 호가 칸 수
        for th in THS:
            if r >= th and t - last[th] >= COOL:
                last[th] = t
                sig.append((th, m, t, vol_ok, te - t - 1, rise, r, v / avg if avg > 0 else None, close, nt, tick / o if tick else None))
(OUT / "signals.pkl").write_bytes(pickle.dumps(sig))


def pct(v):
    return "-" if v is None else "%+.2f%%" % (100 * v)


lines = ["# 2번 시나리오 — 1분 급등 뒤 최고 상승폭 (관찰만)", "",
         "고정 20종목, %s ~ %s (UTC, %d일), 업비트 1분봉. 신호 = 1분봉 시가 대비 종가 상승률이 기준 이상. 진입 = 신호 봉 다음에 거래가 난 첫 1분봉의 시가. "
         "최고 상승폭 = 진입부터 그 시간 동안의 고가 최대 ÷ 진입가 − 1. 같은 종목·같은 기준에서 신호 뒤 10분 안의 신호는 세지 않는다. "
         "거래량 조건 = 신호 봉 거래량(코인 수량)이 직전 20개 1분봉 평균의 3배 이상인 신호만(같은 신호들의 부분집합). "
         "최고 상승폭은 그 고점에 팔 수 있었다는 뜻이 아니다. 비용(왕복 수수료 약 0.1%%, 급등 순간의 호가 차이)도 빼지 않았다." % (
             START.date(), (END - dt.timedelta(days=1)).date(), DAYS), "",
         "| 기준 | 거래량 조건 | 신호 | 종목 | 날 | 하루 평균 | 진입까지 빈 분(평균) | %s |" % " | ".join("%d분 최고 상승 중앙값 / 평균" % w for w in WINS),
         "|---|---|---|---|---|---|---|" + "---|" * len(WINS)]
for th in THS:
    for vol in (False, True):
        part = [s for s in sig if s[0] == th and (s[3] or not vol)]
        if not part:
            lines.append("| +%.1f%% | %s | 0 | | | | |%s" % (100 * th, "3배 이상" if vol else "없음", " - |" * len(WINS)))
            continue
        days = {dt.datetime.fromtimestamp(s[2] * 60, tz=dt.timezone.utc).date() for s in part}
        cells = []
        for w in WINS:
            vals = [s[5][w] for s in part]
            cells.append("%s / %s" % (pct(st.median(vals)), pct(sum(vals) / len(vals))))
        lines.append("| +%.1f%% | %s | %d | %d | %d | %.1f | %.2f | %s |" % (
            100 * th, "3배 이상" if vol else "없음", len(part), len({s[1] for s in part}), len(days), len(part) / float(DAYS),
            sum(s[4] for s in part) / len(part), " | ".join(cells)))
lines.append("")
TICK_MIN = 3
lines += ["## 같은 표 — 신호 봉이 호가 %d칸 이상 움직인 신호만" % TICK_MIN, "",
          "DOGE·ALGO·POL·CHZ·IQ·GMT·CELO 는 호가 한 칸이 가격의 0.5~0.9% 라서(업비트 호가 단위: 100~1,000원은 1원, 10~100원은 0.1원, 1~10원은 0.01원), "
          "한 칸만 올라도 +0.5% 신호가 된다(아래 종목 표). "
          "그런 신호는 매수·매도 호가 사이를 오간 것일 수 있어 급등이라 보기 어렵다. 본표는 설계대로 두고, 이 표를 따로 둔다.", "",
          "| 기준 | 거래량 조건 | 신호 | 종목 | 날 | 하루 평균 | 진입까지 빈 분(평균) | %s |" % " | ".join("%d분 최고 상승 중앙값 / 평균" % w for w in WINS),
          "|---|---|---|---|---|---|---|" + "---|" * len(WINS)]
for th in THS:
    for vol in (False, True):
        part = [s for s in sig if s[0] == th and (s[3] or not vol) and s[9] is not None and s[9] >= TICK_MIN]
        if not part:
            lines.append("| +%.1f%% | %s | 0 | | | | |%s" % (100 * th, "3배 이상" if vol else "없음", " - |" * len(WINS)))
            continue
        days = {dt.datetime.fromtimestamp(s[2] * 60, tz=dt.timezone.utc).date() for s in part}
        cells = []
        for w in WINS:
            vals = [s[5][w] for s in part]
            cells.append("%s / %s" % (pct(st.median(vals)), pct(sum(vals) / len(vals))))
        lines.append("| +%.1f%% | %s | %d | %d | %d | %.1f | %.2f | %s |" % (
            100 * th, "3배 이상" if vol else "없음", len(part), len({s[1] for s in part}), len(days), len(part) / float(DAYS),
            sum(s[4] for s in part) / len(part), " | ".join(cells)))
lines.append("")


def day_ci(part, w):
    """날마다 평균 → 일별 평균과 양측 95% 구간(정규 근사, 날 30일 이상일 때만)."""
    by = {}
    for s in part:
        by.setdefault(dt.datetime.fromtimestamp(s[2] * 60, tz=dt.timezone.utc).date(), []).append(s[8][w])
    d = [sum(v) / len(v) for v in by.values()]
    m = sum(d) / len(d)
    if len(d) < 30:
        return "%s (날 %d)" % (pct(m), len(d))
    h = 1.96 * st.stdev(d) / math.sqrt(len(d))
    return "%s [%s, %s]" % (pct(m), pct(m - h), pct(m + h))


lines += ["## 참고 — 같은 시점 종가 수익 (봉 기준 비용 뒤, 편도 BTC·ETH 0.07%, 그 밖 0.10%)", "",
          "최고 상승폭은 아무 의미 없는 흔들림에서도 늘 플러스로 나온다. 정해진 시점에 팔았다면 어땠는지를 함께 본다(검토 의견 2026-11-20). "
          "급등 순간의 실제 호가 차이는 들어 있지 않다.", "",
          "| 기준 | 거래량 조건 | 신호 | %s |" % " | ".join("%d분 시점 중앙값 / 평균" % w for w in WINS), "|---|---|---|" + "---|" * len(WINS)]
for th in THS:
    for vol in (False, True):
        part = [s for s in sig if s[0] == th and (s[3] or not vol)]
        cells = []
        for w in WINS:
            vals = [s[8][w] for s in part]
            cells.append("%s / %s" % (pct(st.median(vals)), pct(sum(vals) / len(vals))) if vals else "-")
        lines.append("| +%.1f%% | %s | %d | %s |" % (100 * th, "3배 이상" if vol else "없음", len(part), " | ".join(cells)))
lines.append("")
lines += ["### 같은 종가 수익 — 날마다 묶은 평균 [95%%] (호가 %d칸 이상 신호만)" % TICK_MIN, "",
          "하루에 신호가 몰린 날이 평균을 끌지 않도록 날마다 평균을 낸 뒤 그 평균을 본다.", "",
          "| 기준 | 거래량 조건 | 신호 | %s |" % " | ".join("%d분 시점" % w for w in WINS), "|---|---|---|" + "---|" * len(WINS)]
for th in THS:
    for vol in (False, True):
        part = [s for s in sig if s[0] == th and (s[3] or not vol) and s[9] is not None and s[9] >= TICK_MIN]
        if part:
            lines.append("| +%.1f%% | %s | %d | %s |" % (100 * th, "3배 이상" if vol else "없음", len(part), " | ".join(day_ci(part, w) for w in WINS)))
lines.append("")
lines += ["## 종목별 신호 수와 호가 한 칸의 크기", "",
          "| 종목 | 호가 한 칸 (가격 대비, 신호 때 중앙값) | +0.5% 없음 | +0.5% 3배 이상 | +0.5% 중 1~2칸 짜리 | +2.0% 없음 |", "|---|---|---|---|---|---|"]
for m in UNI:
    a = [s for s in sig if s[0] == THS[0] and s[1] == m]
    b = [s for s in sig if s[0] == THS[-1] and s[1] == m]
    tk = [s[10] for s in a if s[10] is not None]
    lines.append("| %s | %s | %d | %d | %d | %d |" % (m[4:], "-" if not tk else "%.3f%%" % (100 * st.median(tk)), len(a), sum(1 for s in a if s[3]),
                                                   sum(1 for s in a if s[9] is not None and s[9] < TICK_MIN), len(b)))
lines.append("")

# ---------------------------------------------------------------- 보완 (검토 의견 2026-11-20 ②)
import json  # noqa: E402

real = lambda s: s[9] is not None and s[9] >= TICK_MIN


def te_of(s):
    return s[2] + 1 + s[4]                                   # 진입 봉의 분 = 신호 분 + 1 + 빈 분


# ② 진입 지연 — 다음 1분에 거래가 있었나
lines += ["## 보완 1 — 진입 지연: 신호 다음 1분 안에 거래가 있었나", "",
          "'바로' = 신호 봉 바로 다음 1분봉에 거래가 있어 그 시가에 들어간 경우. '늦음' = 그 사이 거래 없는 1분봉이 있어 더 늦게 들어간 경우. "
          "다음 거래 가격도 우리 주문의 체결가는 아니다(시장가 매수는 보통 더 비싸다).", "",
          "| 기준 | 대상 | 신호 | 바로 (비율) | 늦음 | 늦음: 빈 분 중앙값 / 최대 | 10분 끝 수익 평균(비용 뒤): 바로 / 늦음 |", "|---|---|---|---|---|---|---|"]
for th in THS:
    for label, f in (("전체 (설계대로)", lambda s: True), ("호가 %d칸 이상" % TICK_MIN, real)):
        part = [s for s in sig if s[0] == th and f(s)]
        now = [s for s in part if s[4] == 0]
        late = [s for s in part if s[4] > 0]
        med = lambda v: "-" if not v else pct(sum(v) / len(v))
        lines.append("| +%.1f%% | %s | %d | %d (%.0f%%) | %d | %s | %s / %s |" % (
            100 * th, label, len(part), len(now), 100.0 * len(now) / max(1, len(part)), len(late),
            "-" if not late else "%d / %d분" % (st.median([s[4] for s in late]), max(s[4] for s in late)),
            med([s[8][10] for s in now]), med([s[8][10] for s in late])))
lines.append("")

# ③ 거래량 3배 이상 vs 3배 미만 직접 비교
lines += ["## 보완 2 — 거래량 3배 이상 vs 3배 미만 (호가 %d칸 이상 신호, 끝 수익 비용 뒤, 날마다 묶은 평균 [95%%])" % TICK_MIN, "",
          "| 기준 | 3배 이상: 신호 | 5분 끝 | 10분 끝 | 3배 미만: 신호 | 5분 끝 | 10분 끝 |", "|---|---|---|---|---|---|---|"]
for th in THS:
    hi_ = [s for s in sig if s[0] == th and real(s) and s[3]]
    lo_ = [s for s in sig if s[0] == th and real(s) and not s[3]]
    cell = lambda p: ("%d | %s | %s" % (len(p), day_ci(p, 5), day_ci(p, 10))) if p else "0 | - | -"
    lines.append("| +%.1f%% | %s | %s |" % (100 * th, cell(hi_), cell(lo_)))
lines.append("")

# ① 최저 하락폭과 순서 — 처음에 저가를 저장하지 않아 신호 구간만 다시 받은 저가로 계산 (fetch_lows.py)
LOWS = {}
for m in UNI:
    f = OUT / ("lows_%s.jsonl" % m)
    d = {}
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            d.update({int(k): v for k, v in rec["bars"].items()})
    LOWS[m] = d
if any(LOWS.values()):
    cached = {m: {b[0]: b for b in fetch(m)} for m in UNI}
    same = diff = 0
    for m in UNI:
        for t, (o, h, _l, c) in LOWS[m].items():
            b = cached[m].get(t)
            if b is None:
                continue
            if abs(b[1] - o) <= 1e-9 * o and abs(b[2] - h) <= 1e-9 * h and abs(b[3] - c) <= 1e-9 * c:
                same += 1
            else:
                diff += 1

    def path(s, w):
        """진입부터 w분 → (최고 상승, 최저 하락, 순서). 순서: 'hi' 고점 먼저, 'lo' 저점 먼저, 'same' 같은 봉(알 수 없음)."""
        m, te = s[1], te_of(s)
        lw = LOWS[m]
        mins = [te + i for i in range(w) if (te + i) in cached[m]]          # 그 창에서 거래가 난 1분봉 (끝 수익과 같은 봉들)
        if not mins or any(x not in lw for x in mins):
            return None
        entry = lw[te][0]
        hi_t = lo_t = mins[0]
        for x in mins:
            if lw[x][1] > lw[hi_t][1]:
                hi_t = x
            if lw[x][2] < lw[lo_t][2]:
                lo_t = x
        order = "hi" if hi_t < lo_t else ("lo" if lo_t < hi_t else "same")
        return lw[hi_t][1] / entry - 1, lw[lo_t][2] / entry - 1, order

    lines += ["## 보완 3 — 최고 상승, 최저 하락, 끝 수익을 같이, 그리고 어느 쪽이 먼저였나 (호가 %d칸 이상 신호)" % TICK_MIN, "",
              "최고·최저는 진입가 대비(비용 빼기 전), 끝 수익은 그 시점 종가에 팔았을 때(비용 뒤). 고점과 저점이 같은 1분봉 안이면 순서를 알 수 없다"
              "(1분 창은 진입 봉 하나뿐이라 늘 '알 수 없음'). 저가는 신호 구간만 다시 받았다: 겹친 1분봉 %d개 중 시가·고가·종가가 처음 받은 값과 다른 봉 %d개." % (same, diff), "",
              "| 기준 | 창 | 신호 (저가 있음) | 최고 상승 중앙값 | 최저 하락 중앙값 | 끝 수익 중앙값 / 평균 | 고점 먼저 | 저점 먼저 | 같은 봉 (알 수 없음) | 최저 −1% 이하 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for th in THS:
        part = [s for s in sig if s[0] == th and real(s)]
        for w in WINS:
            got = [(s, path(s, w)) for s in part]
            got = [(s, p) for s, p in got if p is not None]
            if not got:
                continue
            n = float(len(got))
            ends = [s[8][w] for s, _p in got]
            lines.append("| +%.1f%% | %d분 | %d (%d) | %s | %s | %s / %s | %.0f%% | %.0f%% | %.0f%% | %.0f%% |" % (
                100 * th, w, len(part), len(got), pct(st.median([p[0] for _s, p in got])), pct(st.median([p[1] for _s, p in got])),
                pct(st.median(ends)), pct(sum(ends) / n),
                100 * sum(1 for _s, p in got if p[2] == "hi") / n, 100 * sum(1 for _s, p in got if p[2] == "lo") / n,
                100 * sum(1 for _s, p in got if p[2] == "same") / n, 100 * sum(1 for _s, p in got if p[1] <= -0.01) / n))
    lines.append("")
REPORT.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
