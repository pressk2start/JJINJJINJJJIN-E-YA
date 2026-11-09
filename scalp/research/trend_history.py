#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""trend_history — H3 동결 규칙(trend_paper.py: 일봉 종가 > SMA50 → 다음 시가부터 보유, 아니면 현금)을 업비트 전체 일봉 이력에
그대로 적용한 '기술(記述)용' 이력 계산. 공개 일봉만, 키·주문 없음, 표준 라이브러리, Python 3.7+.

왜: H3 의 3개월 페이퍼(2026-10-24~)는 거래가 몇 건뿐이라 통계적 근거가 될 수 없다(운영 검증용). 이 규칙을 실제 돈으로 따를지
    판단하려면 긴 이력에서 무슨 일이 있었는지 — 수익·낙폭·거래 횟수·연도별 — 를 봐야 한다. 규칙·비용은 trend_paper 의 함수와
    상수를 import 해서 그대로 쓴다(복제 없음). 일별 자산곡선의 끝값은 trend_paper.simulate_leg 과 일치함을 실행마다 확인한다.

이것은 검증이 아니다: 추세 추종이 BTC 에서 통했다는 것을 아는 상태에서 고른 규칙의 표본 안 기록이다. N 민감도(20~200)는 결과가
    N=50 한 점에 매달려 있지 않은지를 보는 용도일 뿐 N 을 바꾸자는 것이 아니다. 미래 수익을 보장하지 않는다.

결과 2026-11-09 (2018-01-01~2026-11-09, 비용 편도 0.15% = 수수료 0.05 + 불리한 가격 0.10, BTC·ETH 초기 50/50 독립 슬리브):
  전체    전략 ×99 (연 +68%, 최대낙폭 −57%)  vs 보유 ×4.1 (연 +17%, 최대낙폭 −92%).  종목당 거래 연 17회, 시장 참여 51%.
  연도별  2018 −9/−81 · 2019 +46/+55 · 2020 +301/+326 · 2021 +358/+198 · 2022 −25/−65 · 2023 +95/+130 · 2024 +77/+102 ·
          2025 +40/−10 · 2026 +11/−25  (전략/보유 %). 상승장에선 보유에 뒤지고, 하락장을 피한 것이 초과의 전부다.
  최근    2022~ 전략 ×3.9 (낙폭 −33%) vs 보유 ×1.2 (−65%).  2024~ 전략 ×2.7 (−28%) vs 보유 ×1.4 (−56%).
  N 민감도(2018-06~) N=20·30·50·100·150·200 모두 보유를 이기고 낙폭이 작다(연 +41~+71% vs +29%). 50 이 유독 좋은 점은 아니다.
  거래 성격 왕복 77~79회 중 승률 26~27%, 평균 이익 +48~50%, 평균 손실 −3.4%, 최장 연속 손실 BTC 15회·ETH 10회, 보유 중앙값 3~5일.
          대부분은 며칠 만에 작은 손실로 끝나는 휩쏘이고 소수의 긴 추세가 전부를 번다. 횡보장에서는 연 17회 × 왕복 0.3% 비용과
          휩쏘가 그대로 손실이 된다.
  전략 자체의 최대낙폭이 −57%(전체)·−33%(2022~) 였다 — 이 규칙을 따르려면 그만한 낙폭을 견뎌야 한다.

진입·청산 시점 변형 비교 (2026-11-09, 사용자 질문: 한 번에 전부 vs 분할 매수·분할 매도. 변형 목록은 실행 전에 고정, 전부 보고):
  2018-06~ (보유 ×8.5 연 +29% 낙폭 −74%)                         2022~ (보유 ×1.2 연 +4% 낙폭 −65%)
  A 기준: 첫날 전부 사고 전부 팖      ×92  +71% −44%  주문 18/년    ×3.9 +32% −33%
  B 3일 분할 매수                     ×73  +66% −45%                ×3.0 +26% −32%
  C 3일 분할 매도                     ×74  +66% −44%                ×3.3 +28% −39%
  D 분할 매수 + 분할 매도             ×57  +62% −45%                ×2.7 +22% −41%
  E 2일 연속 확인 후 전부             ×42  +56% −49%  주문 11/년    ×2.0 +16% −45%
  F 3일 연속 확인 후 전부             ×38  +54% −49%                ×2.1 +16% −45%
  G 완충 ±1%                          ×114 +75% −48%  주문 11/년    ×4.8 +38% −29%
  H 완충 ±2%                          ×76  +67% −47%                ×2.7 +23% −37%
  I 완충 ±3%                          ×61  +63% −52%                ×2.3 +19% −40%
  J 기간 4개(20·50·100·200) 1/4씩     ×54  +60% −41%                ×2.7 +23% −27%
  재현: py -3 -X utf8 trend_history.py --variants  (아래 표를 그대로 다시 계산한다. 변형 코드는 이 파일의 variant_* 함수들)
  주의: 위 '전체' 절의 최대낙폭 −57% 는 2018-01 시작 창, 이 표의 −44% 는 2018-06 시작 창이다. 서로 다른 창이라 직접 비교하지 말 것.
  읽기: 늦추는 변형(B~F)은 두 기간 모두 기준보다 나쁘다. 이익은 갑자기 시작하는 큰 추세의 초반에서 나오므로, 나눠 사거나 확인을
        기다리면 그 초반을 놓치고 낙폭은 줄지 않는다. 완충은 ±1% 만 좋고 ±2·3% 는 나빠 방향이 일정하지 않다(잡음으로 본다).
        실제 맞교환이 있는 것은 J 하나: 낙폭이 조금 작고 수익이 낮다. 결론: 동결 규칙(A, 첫날 전부)을 바꿀 근거 없음.
        정확히 말하면 A 가 '모든 변형보다 우월' 한 것은 아니다 — 완충 ±1%(G)는 두 기간 모두 A 보다 높았다. 다만 ±2·3% 는 낮아
        폭에 따른 방향이 일정하지 않고, 같은 과거 자료에서 여러 변형을 비교한 결과라 미래 우위를 입증하지 않는다.
        A 를 유지하는 근거는 '분할·확인이 개선이라는 증거가 없다' 이지 'A 가 최적' 이 아니다.
  추가 확인 (같은 날, 다른 세션이 제안한 2일 분할 4종을 그대로): 2018-06~ / 2022~ / 2024~ 배수, 편도 0.15% → 0.30% 스트레스
        A 첫날 100%                      ×92 / ×3.9 / ×2.7   →  ×74 / ×3.4 / ×2.5
        B 첫날 50% + 다음 날 50%         ×85 / ×3.4 / ×2.4   →  ×70 / ×3.0 / ×2.2
        C 2일 연속 확인 후 100%          ×78 / ×3.0 / ×2.1   →  ×66 / ×2.7 / ×1.9
        D 50%+50% 진입, 50%+50% 청산     ×67 / ×3.0 / ×2.4   →  ×55 / ×2.6 / ×2.2
        세 기간·두 비용 수준 모두 A 가 최고이고, B 의 낙폭은 A 보다 작지 않다(−45/−32/−29% vs −44/−33/−28%).
        고문의 독립 실행(변형 9종)도 같은 결론. 진입·청산 방식은 A 로 확정한다.

실행 (PC):  py -3 -X utf8 trend_history.py            (업비트에서 일봉을 받아 계산, 1~2분)
"""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("trend_paper", str(Path(__file__).resolve().parent / "trend_paper.py"))
TP = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(TP)


def fill_missing(data):
    """달력일 누락을 직전 종가로 채운다(시가=종가=직전 종가) → (data, 채운 수). 동결 함수는 누락일에서 멈추기 때문."""
    days = sorted(data)
    out, miss, day = dict(data), 0, days[0]
    while day <= days[-1]:
        if day not in out:
            prev = out[TP._shift(day, -1)][1]
            out[day] = (prev, prev)
            miss += 1
        day = TP._shift(day, 1)
    return out, miss


def path(data, start, end, n=TP.N):
    """simulate_leg 과 같은 로직의 일별 자산곡선 → (curve[(day, equity)], bench[...], trips[(buy_day, sell_day, ret)], 참여율)."""
    eq, pos, prev, first = 1.0, False, None, None
    curve, bench, trips, in_mkt, buy_day, buy_eq = [], [], [], 0, None, None
    day = start
    while day <= end:
        o = data[day][0]
        want = TP.signal_for_open(data, day, n)["on"]
        if prev is not None and pos:
            eq *= o / prev
        if first is None:
            first = o
        if want and not pos:
            buy_day, buy_eq = day, eq
            eq /= TP.BUY
        elif pos and not want:
            eq *= TP.SELL
            trips.append((buy_day, day, eq / buy_eq - 1))
        pos, prev = want, o
        in_mkt += 1 if pos else 0
        curve.append((day, eq))
        bench.append((day, o / first / TP.BUY))
        day = TP._shift(day, 1)
    return curve, bench, trips, in_mkt / max(len(curve), 1)


def max_drawdown(curve):
    peak, worst = curve[0][1], 0.0
    for _, v in curve:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def cagr(curve):
    years = (dt.date.fromisoformat(curve[-1][0]) - dt.date.fromisoformat(curve[0][0])).days / 365.25
    return (curve[-1][1] / curve[0][1]) ** (1 / years) - 1 if years > 0 else 0.0


def yearly(curve):
    out, prev_end = {}, None
    for year in sorted(set(d[:4] for d, _ in curve)):
        vals = [v for d, v in curve if d[:4] == year]
        out[year] = vals[-1] / (prev_end if prev_end is not None else vals[0]) - 1
        prev_end = vals[-1]
    return out


def trip_stats(trips):
    if not trips:
        return {"n": 0}
    rets = [r for _, _, r in trips]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    streak = worst = 0
    for r in rets:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    hold = [(dt.date.fromisoformat(s) - dt.date.fromisoformat(b)).days for b, s, _ in trips]
    return {"n": len(rets), "win_pct": 100.0 * len(wins) / len(rets),
            "avg_win": 100 * sum(wins) / len(wins) if wins else 0.0,
            "avg_loss": 100 * sum(losses) / len(losses) if losses else 0.0,
            "best": 100 * max(rets), "worst": 100 * min(rets), "max_losing_streak": worst,
            "median_hold_days": sorted(hold)[len(hold) // 2]}


# ----------------------------------------------------------------------------------------------------------------------
# 진입·청산 시점 변형 (기술용 비교). 모든 변형은 '하위 슬리브' 들의 평균으로 표현한다: 분할 = 진입/청산 지연이 다른 슬리브 여러 개.
def _days(start, end):
    out, day = [], start
    while day <= end:
        out.append(day)
        day = TP._shift(day, 1)
    return out


def _close_and_mean(data, day, n):
    closes = [data[TP._shift(day, -k)][1] for k in range(n, 0, -1)]
    return closes[-1], sum(closes) / n


def variant_basic(data, days, n=TP.N):
    out = []
    for d in days:
        close, mean = _close_and_mean(data, d, n)
        out.append(close > mean)
    return out


def variant_band(data, days, band, n=TP.N):
    """평균보다 band 위에서 켜지고 band 아래에서 꺼진다(이력 완충)."""
    out, on = [], False
    for d in days:
        close, mean = _close_and_mean(data, d, n)
        if not on and close > mean * (1 + band):
            on = True
        elif on and close < mean * (1 - band):
            on = False
        out.append(on)
    return out


def variant_staged(base, enter_delay=0, exit_delay=0):
    """기본 신호가 enter_delay+1 일 연속 ON 이면 진입, exit_delay+1 일 연속 OFF 면 청산."""
    out, on, run_on, run_off = [], False, 0, 0
    for b in base:
        run_on = run_on + 1 if b else 0
        run_off = run_off + 1 if not b else 0
        if not on and run_on >= enter_delay + 1:
            on = True
        elif on and run_off >= exit_delay + 1:
            on = False
        out.append(on)
    return out


def variant_confirm(base, k):
    """k 일 연속 같은 쪽이어야 뒤집힌다(진입·청산 모두)."""
    out, on = [], False
    for i in range(len(base)):
        window = base[max(0, i - k + 1):i + 1]
        if len(window) == k and all(window):
            on = True
        elif len(window) == k and not any(window):
            on = False
        out.append(on)
    return out


def simulate_wants(data, days, wants):
    """주어진 보유 여부 열을 동결 규칙과 같은 비용으로 체결 → (curve, 주문 수)."""
    eq, pos, prev, orders, curve = 1.0, False, None, 0, []
    for day, want in zip(days, wants):
        o = data[day][0]
        if prev is not None and pos:
            eq *= o / prev
        if want and not pos:
            eq /= TP.BUY
            orders += 1
        elif pos and not want:
            eq *= TP.SELL
            orders += 1
        pos, prev = want, o
        curve.append((day, eq))
    return curve, orders


VARIANTS = (
    ("A baseline: all in / all out on day 1", lambda d, ds: [variant_basic(d, ds)]),
    ("B buy over 3 days, sell at once", lambda d, ds: [variant_staged(variant_basic(d, ds), enter_delay=j) for j in range(3)]),
    ("C buy at once, sell over 3 days", lambda d, ds: [variant_staged(variant_basic(d, ds), exit_delay=j) for j in range(3)]),
    ("D buy and sell over 3 days", lambda d, ds: [variant_staged(variant_basic(d, ds), j, j) for j in range(3)]),
    ("E confirm 2 days both ways", lambda d, ds: [variant_confirm(variant_basic(d, ds), 2)]),
    ("F confirm 3 days both ways", lambda d, ds: [variant_confirm(variant_basic(d, ds), 3)]),
    ("G band +-1%", lambda d, ds: [variant_band(d, ds, 0.01)]),
    ("H band +-2%", lambda d, ds: [variant_band(d, ds, 0.02)]),
    ("I band +-3%", lambda d, ds: [variant_band(d, ds, 0.03)]),
    ("J four periods 20/50/100/200, 1/4 each", lambda d, ds: [variant_basic(d, ds, n) for n in (20, 50, 100, 200)]),
    ("B2 50% day 1 + 50% day 2, sell at once", lambda d, ds: [variant_staged(variant_basic(d, ds), enter_delay=j) for j in range(2)]),
    ("C2 buy after 2 straight days, sell day 1", lambda d, ds: [variant_staged(variant_basic(d, ds), enter_delay=1)]),
    ("D2 50%+50% in, 50%+50% out", lambda d, ds: [variant_staged(variant_basic(d, ds), j, j) for j in range(2)]),
)


def variant_portfolio(data, start, end, builder):
    days = _days(start, end)
    curves, orders, k = [], 0, 1
    for m in TP.ASSETS:
        subs = builder(data[m], days)
        k = len(subs)
        for wants in subs:
            curve, n = simulate_wants(data[m], days, wants)
            curves.append(curve)
            orders += n
    port = [(days[i], sum(c[i][1] for c in curves) / len(curves)) for i in range(len(days))]
    years = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days / 365.25
    return port, orders / len(TP.ASSETS) / years, k


def report_variants(data, end, log=print):
    for start in ("2018-06-01", "2022-01-01", "2024-01-01"):
        log("== variants %s ~ %s (BTC/ETH 50:50, cost per side %.2f%%) ==" % (start, end, 100 * (TP.BUY - 1)))
        for name, builder in VARIANTS:
            port, per_year, k = variant_portfolio(data, start, end, builder)
            log("  %-42s x%-8.2f CAGR %+6.1f%% MDD %4.0f%% orders/yr/asset %5.1f (each = %s of the sleeve)" % (
                name, port[-1][1], 100 * cagr(port), 100 * max_drawdown(port), per_year, "all" if k == 1 else "1/%d" % k))
    days = _days("2018-06-01", end)
    for m in TP.ASSETS:                                          # 기준 변형이 동결 규칙과 같은 값인지 확인
        curve, _ = simulate_wants(data[m], days, variant_basic(data[m], days))
        frozen = TP.simulate_leg(data[m], "2018-06-01", end)["equity"]
        if abs(curve[-1][1] - frozen) > 1e-9 * max(1.0, frozen):
            raise ValueError("variant A != frozen rule for " + m)
    log("check: variant A equals the frozen rule. In-sample comparison of many variants — not evidence of future superiority.")


def combine(a, b):
    return [(a[i][0], (a[i][1] + b[i][1]) / 2) for i in range(len(a))]


def report(data, start, end, n=TP.N, log=print, years=False, trips_out=False):
    legs = {}
    span_years = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days / 365.25
    for m in TP.ASSETS:
        curve, bench, trips, share = path(data[m], start, end, n)
        check = TP.simulate_leg(data[m], start, end, n)["equity"]
        if abs(check - curve[-1][1]) > 1e-9 * max(1.0, check):
            raise ValueError("path != frozen simulate_leg for " + m)
        legs[m] = (curve, bench, trips)
        ntr = 2 * len(trips) + (1 if len(curve) and TP.signal_for_open(data[m], end, n)["on"] else 0)
        log("  %-4s N=%-3d %s~  strat x%-7.2f CAGR %+6.1f%% MDD %4.0f%% | hold x%-6.2f CAGR %+6.1f%% MDD %4.0f%% | "
            "orders/yr %.1f in-market %.0f%%" % (m[4:], n, start, curve[-1][1], 100 * cagr(curve), 100 * max_drawdown(curve),
                                                 bench[-1][1], 100 * cagr(bench), 100 * max_drawdown(bench),
                                                 ntr / span_years, 100 * share))
        if trips_out:
            s = trip_stats(trips)
            log("       round trips %d: win %.0f%% avg win %+.1f%% avg loss %+.1f%% best %+.0f%% worst %+.0f%% "
                "max losing streak %d median hold %dd" % (s["n"], s["win_pct"], s["avg_win"], s["avg_loss"], s["best"],
                                                           s["worst"], s["max_losing_streak"], s["median_hold_days"]))
    pc = combine(legs[TP.ASSETS[0]][0], legs[TP.ASSETS[1]][0])
    pb = combine(legs[TP.ASSETS[0]][1], legs[TP.ASSETS[1]][1])
    log("  PORT N=%-3d %s~  strat x%-7.2f CAGR %+6.1f%% MDD %4.0f%% | hold x%-6.2f CAGR %+6.1f%% MDD %4.0f%%" % (
        n, start, pc[-1][1], 100 * cagr(pc), 100 * max_drawdown(pc), pb[-1][1], 100 * cagr(pb), 100 * max_drawdown(pb)))
    if years:
        ys, yb = yearly(pc), yearly(pb)
        log("  year    strat     hold")
        for y in sorted(ys):
            log("  %s %+8.1f%% %+8.1f%%" % (y, 100 * ys[y], 100 * yb[y]))
    return pc, pb


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    end = dt.datetime.now(dt.timezone.utc).date().isoformat()
    data = {}
    for m in TP.ASSETS:
        raw = TP.candles(m, through=end, start="2017-12-01")
        data[m], miss = fill_missing(raw)
        print("%s: %d daily candles %s..%s (filled %d missing days)" % (m, len(data[m]), min(data[m]), max(data[m]), miss))
    if "--variants" in argv:
        report_variants(data, end)
        return 0
    print("H3 FROZEN RULE HISTORY (descriptive, in-sample) | cost per side %.2f%% | through %s open" % (
        100 * (TP.BUY - 1), end))
    print("== full history, N=%d ==" % TP.N)
    report(data, "2018-01-01", end, years=True, trips_out=True)
    print("== recent regimes, N=%d ==" % TP.N)
    report(data, "2022-01-01", end)
    report(data, "2024-01-01", end)
    print("== sensitivity to N (not a proposal to change N) ==")
    for n in (20, 30, 50, 100, 150, 200):
        report(data, "2018-06-01", end, n)
    for m in TP.ASSETS:
        sig = TP.signal_for_open(data[m], end, TP.N)
        print("NOW %s: close %s=%.0f SMA%d=%.0f -> %s (needs %+.1f%% to flip)" % (
            m, sig["decision_day"], sig["close"], TP.N, sig["average"], "HOLD" if sig["on"] else "CASH",
            100 * (sig["average"] / sig["close"] - 1)))
    print("In-sample description of a rule family known to have worked on BTC. Not a forecast, not advice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
