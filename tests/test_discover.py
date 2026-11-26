# -*- coding: utf-8 -*-
"""discover 테스트 — 고문 틀(15분봉·거래대금 3배·분위수 문턱·HOLDOUT 여섯 기준)대로 도는지,
심어 둔 '시장 상태 조건'을 찾아 통과시키고 잡음은 통과시키지 않는지, 미래 봉을 보지 않는지, 잠금·기록·판정이 맞는지."""
import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
import random
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("disc", ROOT / "scalp" / "research" / "discover.py")
D = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(D)
Q0 = D.day_q("2026-01-01")


def flat(n, price=100.0, value=1e9, start=Q0):
    return {start + k: (price, price, price, price, value) for k in range(n)}


def panel_of(**rows):
    return {"KRW-" + name: D.Bars(r) for name, r in rows.items()}


def world(days=220, seed=3, planted=True, n_alts=7):
    """BTC 는 5일마다 오르는 국면·내리는 국면이 바뀐다. 알트는 가끔 한 봉에 −5% 급락하고 그 봉의 거래대금이 6배로 뛴다.
    planted 면 급락 뒤 4봉 동안: BTC 가 내리는 국면이었으면 봉당 +0.6%, 오르는 국면이었으면 봉당 −0.6%."""
    rng = random.Random(seed)
    names = ["KRW-BTC"] + ["KRW-A%d" % i for i in range(n_alts)]
    rows, px, pending = {m: {} for m in names}, {m: 100.0 for m in names}, {m: [] for m in names}
    for k in range(days * D.BARS_D):
        q, down = Q0 + k, (k // (5 * D.BARS_D)) % 2 == 1
        for m in names:
            o, value = px[m], 1e9 * (1 + rng.random())
            if m == "KRW-BTC":
                r = (-0.0004 if down else 0.0004) + rng.gauss(0, 0.0003)
            elif pending[m]:
                r = rng.gauss(0, 0.003) + pending[m].pop(0)
            elif rng.random() < 0.004:
                r, value = -0.05, 6e9
                pending[m] = [(0.006 if down else -0.006) if planted else 0.0] * 4
            else:
                r = rng.gauss(0, 0.003)
            c = o * (1 + r)
            rows[m][q] = (o, max(o, c), min(o, c), c, value)
            px[m] = c
    return {m: D.Bars(r) for m, r in rows.items()}


def serve(panel):
    def fetch(m, s, e):
        b = panel[m]
        i, j = b.window(s, e - 1)
        return D.Bars({b.ts[t]: (b.o[t], b.h[t], 0.0, b.c[t], b.v[t]) for t in range(i, j)})
    return fetch


def cand_rule(c):
    return D.cand_key(c)


class BarsAndEvents(unittest.TestCase):
    def bars(self, close, value, n=120):
        rows = flat(n)
        rows[Q0 + n - 1] = (100.0, max(100.0, close), min(100.0, close), close, value)
        return D.Bars(rows)

    def test_surge_and_crash_need_three_percent_and_three_times_the_volume(self):
        for close, value, want in ((103.0, 3e9, {"SURGE", "BREAKOUT"}), (97.0, 3e9, {"CRASH"}), (102.9, 3e9, {"BREAKOUT"}), (97.1, 3e9, set()),
                                   (103.0, 2.9e9, set()), (97.0, 2.9e9, set())):
            found = D.detect(self.bars(close, value))
            self.assertEqual(set(found[0][1]) if found else set(), want, (close, value))

    def test_breakout_needs_a_close_above_the_one_day_high(self):
        rows = flat(120)
        rows[Q0 + 100] = (100.0, 102.0, 100.0, 100.0, 1e9)              # 1일 안에 이미 102 를 찍었다
        rows[Q0 + 119] = (100.0, 101.5, 100.0, 101.5, 4e9)
        self.assertEqual(D.detect(D.Bars(rows)), [])
        rows[Q0 + 119] = (100.0, 102.5, 100.0, 102.5, 4e9)
        found = D.detect(D.Bars(rows))
        self.assertEqual((found[0][0], set(found[0][1])), (119, {"BREAKOUT"}))
        self.assertAlmostEqual(found[0][2], 4.0)                        # 거래대금 배수

    def test_no_event_when_the_previous_bar_is_missing_or_volume_history_is_short(self):
        rows = flat(120)
        rows[Q0 + 119] = (100.0, 100.0, 95.0, 95.0, 5e9)
        del rows[Q0 + 118]
        self.assertEqual(D.detect(D.Bars(rows)), [])
        short = flat(40)
        short[Q0 + 39] = (100.0, 100.0, 95.0, 95.0, 5e9)
        self.assertEqual(D.detect(D.Bars(short)), [])

    def test_volume_base_is_the_median_of_the_previous_96_bars(self):
        rows = flat(130)
        for k in range(40, 60):                                         # 직전 1일 안의 큰 거래대금 20개 → 평균은 3 배로 뛰지만 중앙값은 그대로
            rows[Q0 + k] = (100.0, 100.0, 100.0, 100.0, 1.1e10)
        rows[Q0 + 129] = (100.0, 100.0, 96.0, 96.0, 4e9)                # 중앙값의 4배, 평균(약 3.1e9)의 1.3배
        found = D.detect(D.Bars(rows))
        self.assertEqual([(k, set(e)) for k, e, _m in found if k == 129], [(129, {"CRASH"})])
        self.assertAlmostEqual(found[-1][2], 4.0)

    def test_the_96_bars_are_counted_by_rows_not_by_clock_time(self):
        rows = flat(200)
        for k in range(120, 170):                                       # 50개 봉이 빠졌다 (거래 없음)
            del rows[Q0 + k]
        rows[Q0 + 199] = (100.0, 100.0, 96.0, 96.0, 4e9)
        found = D.detect(D.Bars(rows))                                   # 직전 1일(96칸)에는 봉이 46개뿐이지만, 직전 96개 봉은 있다
        self.assertEqual([set(e) for _k, e, _m in found], [{"CRASH"}])
        few = {q: b for q, b in rows.items() if q >= Q0 + 110}          # 전체가 90개 남짓이면 판단하지 않는다
        self.assertEqual(D.detect(D.Bars(few)), [])

    def test_bars_lookups(self):
        b = D.Bars({Q0: (1, 2, 0, 1.5, 10), Q0 + 1: (1.5, 3, 1, 3.0, 20), Q0 + 3: (3, 3, 3, 3.0, 30)})
        self.assertEqual((b.idx(Q0 + 1), b.idx(Q0 + 2)), (1, -1))
        self.assertAlmostEqual(b.ret(Q0 + 1, 1), 1.0)
        self.assertIsNone(b.ret(Q0 + 3, 1))                             # 빈 봉을 건너뛰어 비교하지 않는다
        self.assertEqual(b.window(Q0 + 1, Q0 + 3), (1, 3))

    def test_indicators_use_nothing_after_the_event_bar(self):
        full = world(days=70, seed=5)
        m = "KRW-A1"
        k = full[m].idx(Q0 + 60 * D.BARS_D)
        cut = {name: D.Bars({b.ts[t]: (b.o[t], b.h[t], 0.0, b.c[t], b.v[t]) for t in range(b.window(Q0, Q0 + 60 * D.BARS_D)[1])})
               for name, b in full.items()}
        before = D.indicators(cut, m, k)
        self.assertEqual(D.indicators(full, m, k), before)               # 뒤에 봉이 더 있어도 결과는 같다
        for name in ("btc_ret_1h", "btc_ret_4h", "btc_ret_1d", "btc_above_50d", "btc_dist_50d", "up_ratio", "mkt_ret_1h", "bar_size",
                     "ret_1d", "pos_1d_high", "compress", "hour_kst"):
            self.assertIn(name, before)


class Trades(unittest.TestCase):
    def crash_rows(self, at=(100,), n=260, after=95.0):
        rows = flat(n)
        price = 100.0
        for k in at:
            price *= 0.95
            rows[Q0 + k] = (price / 0.95, price / 0.95, price, price, 5e9)
            for j in range(k + 1, n):
                rows[Q0 + j] = (price, price, price, price, 1e9)
        return rows

    def test_buy_next_open_sell_after_the_hold_and_pay_the_cost(self):
        rows = self.crash_rows()
        rows[Q0 + 105] = (97.0, 97.0, 97.0, 97.0, 1e9)                   # 진입(101번 봉 시가) 1시간 뒤 봉의 시가 97
        panel = panel_of(BTC=flat(260), AAA=rows)
        trades = [t for t in D.build_trades(panel, Q0, Q0 + 260) if t["pop"] == "CRASH/1h"]
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual((t["entry_q"], t["exit_q"], t["hold"]), (Q0 + 101, Q0 + 105, 4))
        self.assertAlmostEqual(t["net"], 97.0 / 95.0 * (1 - 0.0010) / (1 + 0.0010) - 1)       # 알트 비용 편도 0.10%
        self.assertAlmostEqual(t["ind"]["vol_mult"], 5.0)
        self.assertEqual(t["ind"]["same_events_1h"], 0.0)
        btc = panel_of(BTC=rows)
        self.assertAlmostEqual([x for x in D.build_trades(btc, Q0, Q0 + 260) if x["pop"] == "CRASH/1h"][0]["net"],
                               97.0 / 95.0 * (1 - 0.0007) / (1 + 0.0007) - 1)                 # BTC 는 0.07%

    def test_a_missing_bar_inside_the_hold_means_no_trade(self):
        rows = self.crash_rows()
        del rows[Q0 + 110]                                               # 1시간 보유(101→105)는 온전하고, 4시간 보유(101→117) 안에는 빈 봉이 있다
        pops = {t["pop"] for t in D.build_trades(panel_of(BTC=flat(260), AAA=rows), Q0, Q0 + 260)}
        self.assertEqual(pops, {"CRASH/1h"})                             # 고문의 급락 반등 규칙과 같은 처리
        del rows[Q0 + 101]                                               # 진입 봉이 없으면 둘 다 없다
        self.assertEqual(D.build_trades(panel_of(BTC=flat(260), AAA=rows), Q0, Q0 + 260), [])

    def test_one_position_at_a_time_per_population(self):
        rows = self.crash_rows(at=(100, 102, 130))                       # 102 는 100 의 보유 시간 안
        trades = D.build_trades(panel_of(BTC=flat(260), AAA=rows), Q0, Q0 + 260)
        self.assertEqual([t["entry_q"] - Q0 for t in trades if t["pop"] == "CRASH/1h"], [101, 131])
        self.assertEqual([t["entry_q"] - Q0 for t in trades if t["pop"] == "CRASH/4h"], [101, 131])

    def test_same_events_counts_other_coins_in_the_last_hour(self):
        panel = panel_of(BTC=flat(260), AAA=self.crash_rows(at=(100,)), BBB=self.crash_rows(at=(98,)), CCC=self.crash_rows(at=(90,)))
        t = [t for t in D.build_trades(panel, Q0, Q0 + 260) if t["market"] == "KRW-AAA" and t["pop"] == "CRASH/1h"][0]
        self.assertEqual(t["ind"]["same_events_1h"], 1.0)                # BBB(2봉 전)만. CCC 는 1시간 밖


class Selection(unittest.TestCase):
    def trade(self, k, net, x):
        return {"pop": "CRASH/1h", "event": "CRASH", "hold": 4, "market": "KRW-AAA", "entry_q": Q0 + k * 10, "exit_q": Q0 + k * 10 + 4,
                "day": D.q_day(Q0 + k * 10), "gross": net, "net": net, "ind": {"x": float(x), "flag": float(k % 2)}}

    def test_thresholds_are_the_ten_train_quantiles_without_duplicates(self):
        train = [self.trade(k, 0.0, k) for k in range(100)]
        conds = D.make_conditions(train)
        x = [(c[2], c[3]) for c in conds if c[1] == "x"]
        self.assertEqual(x, [("<=", 5.0), ("<=", 10.0), ("<=", 20.0), ("<=", 33.0), ("<=", 50.0),
                             (">=", 50.0), (">=", 67.0), (">=", 80.0), (">=", 90.0), (">=", 95.0)])
        flag = [(c[2], c[3]) for c in conds if c[1] == "flag"]          # 값이 두 가지뿐이면 겹치는 문턱은 한 번만
        self.assertEqual(sorted(flag), [("<=", 0.0), ("<=", 1.0), (">=", 1.0)])

    def good(self, n=120, per_day=3):
        return [dict(self.trade(k, 0.01 + 0.001 * (k % 3), k), entry_q=Q0 + (k // per_day) * D.BARS_D + k % per_day,
                     day=D.q_day(Q0 + (k // per_day) * D.BARS_D)) for k in range(n)]

    def test_holdout_passes_only_when_all_six_hold(self):
        self.assertTrue(D.holdout_checks(self.good())["pass"])
        self.assertFalse(D.holdout_checks(self.good(n=99))["pass"])                               # 100건 미만
        late_loss = self.good()
        for t in late_loss[60:]:
            t["net"] = -0.002                                                                    # 후반이 음수
        hc = D.holdout_checks(late_loss)
        self.assertTrue(hc["net"] > 0 and hc["first"] > 0 and hc["second"] < 0)
        self.assertFalse(hc["pass"])
        few_days = [dict(t, net=-0.001) for t in self.good()]
        for t in few_days[:15]:
            t["net"] = 0.05                                                                      # 수익이 처음 5일에 몰려 있다
        hc = D.holdout_checks(few_days)
        self.assertTrue(hc["net"] > 0 and hc["ex_top5"] < 0)
        self.assertFalse(hc["pass"])


class Search(unittest.TestCase):
    def setUp(self):
        self.saved = (D.TRAIN_MIN_N, D.HOLD_MIN_N)
        D.TRAIN_MIN_N, D.HOLD_MIN_N = 40, 25

    def tearDown(self):
        D.TRAIN_MIN_N, D.HOLD_MIN_N = self.saved

    def run_world(self, panel):
        start, end, cut = D.q_day(Q0 + 60 * D.BARS_D), D.q_day(Q0 + 220 * D.BARS_D), D.q_day(Q0 + 156 * D.BARS_D)
        return D.run(start, end, cut, tuple(sorted(panel)), fetch=serve(panel), log=lambda s: None)

    def test_a_trade_bought_before_the_cut_and_sold_after_it_is_in_neither_half(self):
        """2026-11-14 검토: 경계에 걸친 거래가 TRAIN 에 들어가 뒤 구간의 가격이 선별에 섞였다."""
        cut_q = D.day_q("2026-06-10")

        def tr(entry_q, hold, net):
            return {"pop": "CRASH/%dh" % (hold // 4), "event": "CRASH", "hold": hold, "market": "KRW-AAA", "entry_q": entry_q,
                    "exit_q": entry_q + hold, "day": D.q_day(entry_q), "gross": net, "net": net, "move": -0.05, "ind": {"x": 1.0}}
        inside = [tr(cut_q - 400 - 10 * k, 96, 0.01) for k in range(5)]                         # 청산까지 앞 구간에서 끝난다
        straddle = tr(cut_q - 20, 96, 9.99)                                                     # 앞 구간에 사서 뒤 구간에 판다
        after = [tr(cut_q + 10 * k, 96, 0.02) for k in range(3)]
        seen = []
        D.search(inside + [straddle] + after, "2026-06-10", log=seen.append)
        self.assertIn("TRAIN n=5 ", seen[0])                                                    # 걸친 거래는 TRAIN 에 없다
        self.assertIn("HOLDOUT n=3 ", seen[0])                                                  # HOLDOUT 에도 없다
        self.assertIn("net +1.000%", seen[0])                                                   # 9.99 가 평균에 섞이지 않았다

    def test_a_planted_market_condition_is_selected_and_passes_all_six(self):
        passed, counts, lines, trades = self.run_world(world(planted=True))
        one = [f for f in passed if f["pop"] == "CRASH/1h"]
        self.assertTrue(one)
        self.assertTrue(all(f["cond"][1].startswith("btc_") and f["cond"][2] == "<=" for f in one))   # 갈림선은 BTC 가 내리는 중
        self.assertTrue(all(f["holdout"]["day_low"] > 0 and f["holdout"]["ex_top5"] > 0 for f in one))
        base = next(ln for ln in lines if ln.startswith("== CRASH/1h"))
        self.assertIn("net -", base)                                     # 조건 없이 통째로는 음수인 모집단
        self.assertEqual(counts["passed"], len(passed))
        self.assertGreater(counts["scanned"], 100)
        self.assertGreaterEqual(counts["selected"], counts["passed"])
        new, _held = D.register([], passed, "2026-12-01", ("KRW-BTC",))
        self.assertEqual([c["event"] for c in new], ["CRASH"])           # 사건마다 1개만 등록한다
        self.assertEqual(new[0]["id"], "D-202612-01")

    def test_holdout_lines_show_entry_days_daily_win_rate_and_daily_median(self):
        """고문 판정 2026-11-19: 급락형은 거래가 몇 날에 몰린다. HOLDOUT 줄에 진입일 수를 함께 찍는다 (선별 기준은 그대로)."""
        trades = [{"day": d, "net": v, "gross": v, "entry_q": k, "market": "KRW-A"} for k, (d, v) in enumerate(
            [("2025-05-01", 0.03), ("2025-05-01", 0.01), ("2025-05-02", -0.02), ("2025-05-03", 0.004)])]
        hc = D.holdout_checks(trades)
        self.assertEqual((hc["days"], round(hc["day_win"], 4)), (3, round(2 / 3.0, 4)))
        self.assertAlmostEqual(hc["day_median"], 0.004)
        self.assertEqual(D._days(hc), "진입일 3일 일별승률 67% 일별중앙값 +0.400%")
        self.assertEqual(D._days(D.holdout_checks([])), "진입일 0일")
        many = [{"day": "2025-%02d-%02d" % (6 + k // 28, 1 + k % 28), "net": 0.01 + 0.001 * (k % 7), "gross": 0.01, "entry_q": k, "market": "KRW-A"}
                for k in range(120)]
        self.assertTrue(D.holdout_checks(many)["pass"])
        real = D.median
        D.median = lambda v: -1.0                                                                     # 일별 중앙값이 음수여도 통과는 그대로 — 표시용이다
        try:
            self.assertEqual((D.holdout_checks(many)["pass"], D.holdout_checks(many)["day_median"]), (True, -1.0))
        finally:
            D.median = real
        passed, counts, lines, _ = self.run_world(world())
        rule_lines = [ln for ln in lines if "| HOLDOUT n=" in ln]
        self.assertTrue(rule_lines and all("진입일 " in ln for ln in rule_lines))
        self.assertTrue(all("day_win" in f["holdout"] and "day_median" in f["holdout"] for f in passed))
        per_rule = [ln for ln in rule_lines if not ln.startswith("==")]
        self.assertTrue(per_rule and all(re.search(r" 비용2배 [+-]\d", ln) for ln in per_rule))       # 4판: 비용 2배 평균도 찍는다
        later = D.q_day(Q0 + 250 * D.BARS_D)                                                           # 자료는 220일째에 끝나도 뒤 구간은 실행일까지다
        passed_late, _c, _l, _t = D.run(D.q_day(Q0 + 60 * D.BARS_D), later, D.q_day(Q0 + 156 * D.BARS_D), tuple(sorted(world())),
                                        fetch=serve(world()), log=lambda s: None)
        late_span = (dt.date.fromisoformat(later) - dt.date.fromisoformat(D.q_day(Q0 + 156 * D.BARS_D))).days
        self.assertTrue(passed_late and all(f["holdout"]["span_days"] == late_span for f in passed_late))
        span = (dt.date.fromisoformat(D.q_day(Q0 + 220 * D.BARS_D)) - dt.date.fromisoformat(D.q_day(Q0 + 156 * D.BARS_D))).days
        self.assertTrue(passed and all(f["holdout"]["span_days"] == span for f in passed))              # 뒤 구간 = 경계 ~ 실행일
        self.assertTrue(all(abs(f["holdout"]["expect_days"] - D.expect_days(f["holdout"], span)) < 1e-9 for f in passed))

    def test_noise_passes_nothing(self):
        for seed in (1, 2, 3):
            passed, counts, _, _ = self.run_world(world(seed=seed, planted=False))
            self.assertEqual(passed, [], seed)


class Registry(unittest.TestCase):
    def found(self, event, hold, key, low):
        return {"pop": "%s/%dh" % (event, hold // 4), "event": event, "hold": hold, "cond": [key, key, "<=", -0.01], "train": {},
                "holdout": {"day_low": low}}

    def test_one_in_progress_candidate_per_event_and_the_rest_are_only_recorded(self):
        passed = [self.found("CRASH", 4, "a", 0.002), self.found("CRASH", 16, "a", 0.005), self.found("SURGE", 4, "b", 0.001),
                  self.found("BREAKOUT", 4, "c", 0.003), self.found("BREAKOUT", 16, "c", 0.0005)]
        new, held = D.register([], passed, "2026-12-01", ("KRW-BTC", "KRW-AAA"))
        self.assertEqual([(c["event"], c["hold"]) for c in new], [("CRASH", 16), ("BREAKOUT", 4), ("SURGE", 4)])   # 사건마다 하한이 가장 높은 것
        self.assertEqual([c["id"] for c in new], ["D-202612-01", "D-202612-02", "D-202612-03"])
        self.assertTrue(all(c["status"] == "ACTIVE" and c["engine"] == D.ENGINE and c["universe"] == ["KRW-BTC", "KRW-AAA"] for c in new))
        self.assertEqual([(c["event"], c["hold"], why[:11]) for c, why in held],
                         [("CRASH", 4, "D-202612-01"), ("BREAKOUT", 16, "D-202612-02")])            # 같은 사건의 나머지는 기록만
        again, held = D.register(new, passed, "2027-01-01", ("KRW-BTC", "KRW-AAA"))                  # 다음 달: 셋 다 아직 진행 중
        self.assertEqual((again, len(held)), ([], 5))
        again, held = D.register(new, passed, "2027-01-01", ("KRW-BTC", "KRW-AAA"), decided={"D-202612-01"})   # 급락 후보만 판정이 끝났다
        self.assertEqual([(c["event"], c["hold"], c["id"]) for c in again], [("CRASH", 4, "D-202701-01")])
        self.assertIn("같은 규칙이 전에 등록된 적 있음", [why for c, why in held if (c["event"], c["hold"]) == ("CRASH", 16)])   # 끝난 규칙을 다시 걸지 않는다

    def test_three_a_calendar_month_counts_every_run_of_that_month(self):
        """탐색이 매주도 돈다(고문 판정 2026-11-17 ①). 한 달 상한 3개는 그 달에 등록된 후보를 전부 합쳐 센다."""
        def done(cid, event, key, day):
            return dict(self.found(event, 4, key, 0.001), id=cid, status="ACTIVE", registered=day, engine=D.ENGINE)
        registry = [done("D-202612-01", "CRASH", "x", "2026-12-01"), done("D-202612-02", "SURGE", "y", "2026-12-07")]
        decided = {"D-202612-01", "D-202612-02", "A-202612-01"}                                       # 판정이 끝나 사건 자리는 비어 있다 — 그래도 그 달의 수에는 든다
        passed = [self.found("CRASH", 4, "a", 0.005), self.found("SURGE", 4, "b", 0.004), self.found("BREAKOUT", 4, "c", 0.003)]
        new, held = D.register(registry, passed, "2026-12-14", ("KRW-BTC",), decided=decided)         # 12월 셋째 실행: 이번 달 자리는 하나 남았다
        self.assertEqual([(c["event"], c["id"]) for c in new], [("CRASH", "D-202612-03")])
        self.assertEqual([(c["event"], why) for c, why in held],
                         [("SURGE", "달력 한 달 등록 한도 3개 (이번 달 3개 등록됨)"), ("BREAKOUT", "달력 한 달 등록 한도 3개 (이번 달 3개 등록됨)")])
        again, held = D.register(registry + new, passed[1:], "2026-12-21", ("KRW-BTC",), decided=decided)   # 그 뒤의 12월 실행은 등록하지 못한다
        self.assertEqual((again, len(held)), ([], 2))
        manual = registry + [done("A-202612-01", "BREAKOUT", "z", "2026-12-03")]                      # 고문이 직접 등록한 것도 그 달의 수에 든다
        new, held = D.register(manual, passed, "2026-12-14", ("KRW-BTC",), decided=decided)
        self.assertEqual((new, len(held)), ([], 3))
        new, held = D.register(manual, passed, "2027-01-04", ("KRW-BTC",), decided=decided)           # 달이 바뀌면 다시 3개
        self.assertEqual([c["id"] for c in new], ["D-202701-01", "D-202701-02", "D-202701-03"])
        self.assertEqual(D.MAX_NEW, 3)

    def test_a_new_candidate_is_scored_from_the_day_after_registration(self):
        """고문이 직접 등록한 후보들과 같은 관례: 전진 채점은 등록 다음 날 00:00 UTC 부터."""
        new, _held = D.register([], [self.found("CRASH", 4, "a", 0.005)], "2026-11-30", ("KRW-BTC",))
        self.assertEqual((new[0]["registered"], new[0]["start"], D.cand_start(new[0])), ("2026-11-30", "2026-12-01", "2026-12-01"))

    def test_a_candidate_whose_verdict_is_on_hold_still_occupies_its_event(self):
        """값 차이로 판정이 보류된 후보는 끝난 후보가 아니다 — 보류가 풀릴 때까지 그 사건에 새 후보를 걸지 않는다."""
        verdict = {"verdict": "FAIL", "decided_on": "2026-12-01"}
        self.assertTrue(D.settled({"decision": verdict}))
        self.assertFalse(D.settled({"decision": None}))
        self.assertFalse(D.settled({"decision": verdict, "hold": {"since": "2026-12-02", "released": None}}))
        self.assertTrue(D.settled({"decision": verdict, "hold": {"since": "2026-12-02", "released": {"on": "2026-12-09", "reason": "고문 판정"}}}))

    def test_crash_waits_for_the_advisors_candidate(self):
        """급락은 고문이 등록한 A-202611-01 이 판정되거나 만료될 때까지 새로 등록하지 않는다 (고문 결정 2026-11-13)."""
        registry = json.loads((ROOT / "scalp" / "research" / "forward" / "registry.json").read_text(encoding="utf-8"))
        passed = [self.found("CRASH", 4, "btc_ret_1d", 0.004), self.found("SURGE", 4, "b", 0.001)]
        new, held = D.register(registry, passed, "2026-12-01", ("KRW-BTC",))
        self.assertEqual([c["event"] for c in new], ["SURGE"])
        self.assertEqual([(c["event"], why) for c, why in held], [("CRASH", "A-202611-01 진행 중 (사건 종류마다 진행 중인 후보는 1개까지)")])
        new, held = D.register(registry, passed, "2027-06-01", ("KRW-BTC",), decided={"A-202611-01"})
        self.assertEqual([(c["event"], why) for c, why in held], [("CRASH", "A-202611-02 진행 중 (사건 종류마다 진행 중인 후보는 1개까지)")])   # 고문이 예외로 더 등록한 것이 남아 있다
        new, held = D.register(registry, passed, "2027-06-01", ("KRW-BTC",), decided={"A-202611-01", "A-202611-02"})
        self.assertEqual(sorted(c["event"] for c in new), ["CRASH", "SURGE"])                          # 비교용·체결 관찰 항목은 자리를 차지하지 않는다

    def test_engine_four_is_unlocked_by_the_advisors_verdict(self):
        """고문 판정 2026-11-19 ②: 4판은 잠근 채로 올렸다. 고문 판정 2026-11-22 ④ 2번: 읽은 뒤 풀었다.
        사건·거래 정의는 3판과 같아 3판 후보는 그대로 채점한다."""
        self.assertEqual((D.ENGINE, D.FRAME_APPROVED, D.SAME_TRADES, D.COST_STRESS), ("4", True, ("3", "4"), 2.0))

    def test_a_rule_that_would_take_more_than_a_year_to_judge_is_not_registered(self):
        slow = dict(self.found("SURGE", 4, "b", 0.009), holdout={"day_low": 0.009, "expect_days": 400.0})
        fast = dict(self.found("SURGE", 16, "c", 0.001), holdout={"day_low": 0.001, "expect_days": 90.0})
        new, held = D.register([], [slow, fast], "2026-12-01", ("KRW-BTC",))
        self.assertEqual([(c["event"], c["hold"]) for c in new], [("SURGE", 16)])                     # 느린 것이 하한은 높아도 빠른 것이 자리를 얻는다
        self.assertEqual([why for _c, why in held], ["예상 판정 시점 13.1개월 — 기한 12개월 안에 30건·10일을 채우기 어렵다"])
        edge = dict(slow, holdout={"day_low": 0.009, "expect_days": float(D.FWD_EXPIRE_DAYS)})       # 딱 12개월은 받는다
        self.assertEqual(len(D.register([], [edge], "2026-12-01", ("KRW-BTC",))[0]), 1)

    def test_expected_verdict_time_comes_from_holdout_frequency(self):
        hc = {"n": 146, "days": 20}
        self.assertAlmostEqual(D.expect_days(hc, 678), max(30 * 678 / 146.0, 10 * 678 / 20.0))         # 알트 동반 급락 뒤 구간: 날 수가 묶는다(약 11개월)
        self.assertAlmostEqual(D.expect_days(hc, 678), 339.0)
        self.assertIsNone(D.expect_days({"n": 0, "days": 0}, 678))

    def test_the_cost_doubled_mean_must_stay_positive(self):
        def tr(k, gross, market="KRW-AAA"):
            return {"day": "2025-%02d-%02d" % (6 + k // 28, 1 + k % 28), "gross": gross, "net": (1 + gross) * (1 - 0.001) / (1 + 0.001) - 1,
                    "entry_q": k, "market": market}
        thin = [tr(k, 0.0028 + 0.0004 * (k % 3)) for k in range(120)]                                   # 비용 뒤 +0.1%, 비용 2배면 −0.1% 근처
        hc = D.holdout_checks(thin)
        self.assertGreater(hc["net"], 0)
        self.assertLess(hc["net_2x"], 0)
        self.assertFalse(hc["pass"])
        self.assertAlmostEqual(D.stressed(thin[0]), (1 + 0.0028) * (1 - 0.002) / (1 + 0.002) - 1)
        self.assertAlmostEqual(D.stressed(tr(0, 0.01, "KRW-BTC")), (1.01) * (1 - 0.0014) / (1 + 0.0014) - 1)   # BTC·ETH 는 0.07% × 2
        fat = [tr(k, 0.01 + 0.001 * (k % 3)) for k in range(120)]
        self.assertTrue(D.holdout_checks(fat)["pass"])

    def with_world(self, fn):
        panel = world(planted=True)
        saved = (D.fetch_bars, D.TRAIN_MIN_N, D.HOLD_MIN_N, D.pick_universe, D.HISTORY_DAYS, D.FRAME_APPROVED)
        self.asked = []
        D.fetch_bars, D.HISTORY_DAYS, D.TRAIN_MIN_N, D.HOLD_MIN_N = serve(panel), 160, 40, 25
        D.pick_universe = lambda as_of: self.asked.append(as_of) or tuple(sorted(panel))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    fn(Path(tmp), D.q_day(Q0 + 220 * D.BARS_D))
                return buf.getvalue()
        finally:
            (D.fetch_bars, D.TRAIN_MIN_N, D.HOLD_MIN_N, D.pick_universe, D.HISTORY_DAYS, D.FRAME_APPROVED) = saved

    def test_the_lock_blocks_registration_but_every_run_is_logged(self):
        def body(tmp, today):
            D.FRAME_APPROVED = False
            D.main(["--register", "--out", str(tmp), "--today", today])
            self.assertFalse((tmp / "registry.json").exists())
            log = json.loads((tmp / "scan_log.json").read_text(encoding="utf-8"))
            self.assertEqual((len(log), log[0]["locked"], log[0]["registered"]), (1, True, []))
            self.assertGreater(log[0]["scanned"], 100)
            self.assertGreaterEqual(log[0]["passed"], 1)
            self.assertIn("등록하지 않음", (tmp / "DISCOVERY.md").read_text(encoding="utf-8"))
            D.FRAME_APPROVED = True
            D.main(["--register", "--out", str(tmp), "--today", today])
            reg = json.loads((tmp / "registry.json").read_text(encoding="utf-8"))
            self.assertEqual([c["event"] for c in reg], ["CRASH"])
            log = json.loads((tmp / "scan_log.json").read_text(encoding="utf-8"))
            self.assertEqual((len(log), log[1]["locked"], log[1]["registered"]), (2, False, [reg[0]["id"]]))
            self.assertEqual(log[1]["mode"], "register")                                              # 주간이든 월간이든 등록 실행은 같은 이름으로 남는다
            D.main(["--register", "--out", str(tmp), "--today", today])                       # 같은 통과가 또 나와도 진행 중이면 기록만
            log = json.loads((tmp / "scan_log.json").read_text(encoding="utf-8"))
            self.assertEqual((log[2]["registered"], len(json.loads((tmp / "registry.json").read_text(encoding="utf-8")))), ([], 1))
            self.assertTrue(log[2]["held"] and all(reg[0]["id"] + " 진행 중" in h["why"] for h in log[2]["held"]))
            self.assertIn("통과했으나 등록하지 않음", (tmp / "DISCOVERY.md").read_text(encoding="utf-8"))
            held_entry = {"decision": {"verdict": "FAIL"}, "hold": {"since": today, "why": "값 차이", "released": None}}
            (tmp / "ledger.json").write_text(json.dumps({"candidates": {reg[0]["id"]: held_entry}}), encoding="utf-8")
            D.main(["--register", "--out", str(tmp), "--today", today])                       # 판정이 보류 중이면 그 사건 자리는 비지 않는다
            log = json.loads((tmp / "scan_log.json").read_text(encoding="utf-8"))
            self.assertEqual(log[-1]["registered"], [])
            self.assertTrue(log[-1]["held"] and all(reg[0]["id"] + " 진행 중" in h["why"] for h in log[-1]["held"]))
            (tmp / "ledger.json").write_text(json.dumps({"candidates": {reg[0]["id"]: {"decision": {"verdict": "FAIL"}}}}), encoding="utf-8")
            D.main(["--register", "--out", str(tmp), "--today", today])                       # 판정이 끝났어도 같은 규칙은 다시 걸지 않는다
            log = json.loads((tmp / "scan_log.json").read_text(encoding="utf-8"))
            self.assertNotIn(cand_rule(reg[0]), [cand_rule(c) for c in json.loads((tmp / "registry.json").read_text(encoding="utf-8"))[1:]])
            self.assertEqual(set(self.asked), {log[0]["cut"]})          # 대상 종목은 HOLDOUT 시작일 기준으로 고른다
            self.assertLess(log[0]["cut"], today)
        self.with_world(body)

    def test_diagnostic_records_without_registering_or_touching_monthly_history(self):
        def body(tmp, today):
            official, reports = tmp / "official", tmp / "reports"
            official.mkdir()
            monthly = '[{"run": "2026-11-01"}]\n'
            (official / "scan_log.json").write_text(monthly, encoding="utf-8")
            (official / "registry.json").write_text("[]", encoding="utf-8")
            saved_out = D.OUT_DIR
            D.OUT_DIR = official
            try:
                D.FRAME_APPROVED = True
                self.assertEqual(D.main(["--report-only", "--out", str(reports), "--today", today]), 0)
            finally:
                D.OUT_DIR = saved_out
            log = json.loads((reports / "scan_log.json").read_text(encoding="utf-8"))
            self.assertGreater(log[0]["passed"], 0)
            self.assertEqual((log[0]["mode"], log[0]["registered"]), ("diagnostic", []))
            self.assertFalse((reports / "registry.json").exists())
            self.assertEqual((official / "registry.json").read_text(encoding="utf-8"), "[]")
            self.assertEqual((official / "scan_log.json").read_text(encoding="utf-8"), monthly)
            self.assertIn("결과만 기록", (reports / "DISCOVERY.md").read_text(encoding="utf-8"))
        self.with_world(body)

    def test_diagnostic_rejects_the_official_output_folder_before_fetching(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(D.main(["--report-only"]), 2)

    def test_a_trial_range_never_writes_anything(self):
        def body(tmp, today):
            D.FRAME_APPROVED = True
            D.main(["--register", "--out", str(tmp), "--today", today, "--days", "150"])
            self.assertEqual(list(tmp.iterdir()), [])
        self.assertIn("점검용 범위", self.with_world(body))

    def test_repro_waits_for_the_advisors_fixed_list(self):
        saved = D.REPRO_UNIVERSE
        D.REPRO_UNIVERSE = ()
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(D.main(["--repro"]), 2)          # 돌지 않았으면 성공(0)으로 끝나지 않는다
            self.assertIn("고문의 고정 20종목이 없습니다", buf.getvalue())
        finally:
            D.REPRO_UNIVERSE = saved

    def test_universe_is_picked_as_of_the_holdout_start(self):
        value = {"KRW-USDT": 9e12, "KRW-AAA": 5e12, "KRW-BBB": 4e12, "KRW-CCC": 3e12, "KRW-BTC": 1e9, "KRW-DDD": 2e12, "KRW-NEW": 0.0}
        top = D.pick_universe("2025-06-01", n=3, list_markets=lambda: list(value), daily_value=lambda m: value[m],
                              listed_before=lambda m: m != "KRW-AAA")
        self.assertEqual(top, ("KRW-BBB", "KRW-BTC", "KRW-CCC"))          # 스테이블코인·상장 2년 미만(AAA) 제외, BTC 는 항상 포함
        urls = []

        def fake_get(url):
            urls.append(url)
            if "market/all" in url:
                return [{"market": "KRW-BTC"}, {"market": "KRW-AAA"}, {"market": "BTC-XYZ"}]
            if "count=1&" in url:
                return [{"candle_date_time_utc": "2023-05-01T00:00:00"}]
            return [{"candle_date_time_utc": "2025-06-01T00:00:00", "candle_acc_trade_price": 9e15},      # 기준일 당일은 세지 않는다
                    {"candle_date_time_utc": "2025-05-31T00:00:00", "candle_acc_trade_price": 2.0}]
        real, D.AM.http_get = D.AM.http_get, fake_get
        try:
            self.assertEqual(D.pick_universe("2025-06-01", n=2), ("KRW-AAA", "KRW-BTC"))
        finally:
            D.AM.http_get = real
        self.assertTrue(all("to=2025-06-01T00%3A00%3A00Z" in u for u in urls if "count=200" in u))      # 기준일 이전 자료만 요청
        self.assertTrue(all("to=2023-06-02T00%3A00%3A00Z" in u for u in urls if "count=1&" in u))        # 2년 전에 이미 상장

    def advisor_world(self):
        """고문 수치와 똑같이 나오도록 만든 거래 목록."""
        def tr(event, hold, day, net, market="KRW-BTC", btc=0.0):
            return {"event": event, "hold": hold, "day": day, "net": net, "market": market, "ind": {"btc_ret_1h": btc}}
        front, back, trades = "2024-05-01", "2025-05-01", []
        for (event, hold), ((n1, r1), (n2, r2)) in D.ADVISOR_REF.items():
            if (event, hold) == ("SURGE", 4):
                for coin, (a, b) in D.ADVISOR_SURGE_1H.items():
                    trades += [tr(event, hold, front, r1, "KRW-" + coin)] * a + [tr(event, hold, back, r2, "KRW-" + coin)] * b
            elif (event, hold) == ("CRASH", 4):
                (a1, m1), (a2, m2) = D.ADVISOR_ALT["ref"]
                trades += [tr(event, hold, front, m1, btc=-0.02)] * a1 + [tr(event, hold, front, (n1 * r1 - a1 * m1) / (n1 - a1))] * (n1 - a1)
                trades += [tr(event, hold, back, m2, btc=-0.02)] * a2 + [tr(event, hold, back, (n2 * r2 - a2 * m2) / (n2 - a2))] * (n2 - a2)
            else:
                trades += [tr(event, hold, front, r1)] * n1 + [tr(event, hold, back, r2)] * n2
        return trades, tr

    def test_the_fixed_list_and_targets_are_the_advisors(self):
        self.assertEqual(len(D.REPRO_UNIVERSE), 20)
        self.assertEqual(sorted(m[4:] for m in D.REPRO_UNIVERSE), sorted(D.ADVISOR_SURGE_1H))
        self.assertEqual(tuple(sum(v[i] for v in D.ADVISOR_SURGE_1H.values()) for i in (0, 1)),
                         tuple(D.ADVISOR_REF[("SURGE", 4)][i][0] for i in (0, 1)))               # 코인별 건수의 합 = 전체 건수
        self.assertEqual((D.REPRO_DATA_START, D.REPRO_START, D.REPRO_CUT, D.REPRO_END), ("2022-12-20", "2023-01-01", "2025-01-01", "2026-11-10"))

    def test_repro_says_match_only_when_counts_are_exact_and_means_are_within_tolerance(self):
        trades, tr = self.advisor_world()
        lines, bad = D.repro_lines(trades)
        self.assertEqual(bad, 0, "\n".join(lines))
        self.assertIn("SURGE    1h  여기 1506건 -0.399% | 924건 -0.770%   고문 1506건 -0.399% | 924건 -0.770%   일치", lines[1])
        self.assertIn("불일치 0", lines[7])
        self.assertIn("알트 동반 급락", lines[8])
        self.assertEqual(lines[-1], "결과: 전부 일치")
        lines, bad = D.repro_lines(trades + [tr("SURGE", 4, "2024-06-01", -0.00399, "KRW-ADA")])          # 한 건만 더 있어도 어긋난다
        self.assertEqual(bad, 2)                                                                       # 급등 1h 전체 건수 + ADA 코인별 건수
        self.assertIn("ADA 45/32(고문 44/32)", lines[7])
        lines, bad = D.repro_lines([dict(t, net=t["net"] + 0.0001) if (t["event"], t["hold"]) == ("BREAKOUT", 16) else t for t in trades])
        self.assertEqual(bad, 1)                                                                       # 평균이 0.01%p 어긋나면 불일치
        outside = [tr("SURGE", 4, "2022-12-25", 0.5), tr("SURGE", 4, "2026-11-10", 0.5)]               # 진입일이 범위 밖인 거래는 세지 않는다
        self.assertEqual(D.repro_lines(trades + outside)[1], 0)


class Forward(unittest.TestCase):
    def cand(self, registered, **kw):
        base = {"id": "D-1", "engine": D.ENGINE, "registered": registered, "event": "CRASH", "hold": 4, "cond": None,
                "universe": ["KRW-BTC"] + ["KRW-A%d" % i for i in range(7)],
                "move": 0.03, "vol_x": 3.0, "cost": {"KRW-BTC": 0.0007}, "cost_other": 0.0010, "status": "ACTIVE"}
        base.update(kw)
        return base

    def test_only_events_after_registration_count_and_stored_definitions_are_used(self):
        panel = world(days=120, seed=8)
        reg_day, end = D.q_day(Q0 + 80 * D.BARS_D), Q0 + 120 * D.BARS_D
        rows = D.candidate_trades(panel, self.cand(reg_day), end)
        self.assertTrue(rows)
        self.assertGreaterEqual(min(r["entry_q"] for r in rows), D.day_q(reg_day) + 1)
        self.assertEqual(D.candidate_trades(panel, self.cand(reg_day, move=0.06), end), [])     # 등록 때 문턱이 6% 였다면 5% 급락은 사건이 아니다
        some = D.candidate_trades(panel, self.cand(reg_day, cond=["c", "btc_ret_1d", "<=", 0.0]), end)
        self.assertTrue(0 < len(some) < len(rows))                                               # 조건이 거래를 거른다
        with self.assertRaises(RuntimeError):
            D.candidate_trades(panel, self.cand(reg_day, engine="1"), end)

    def test_several_conditions_a_candidate_only_indicator_and_indicator_only_markets(self):
        """고문 판정 2026-11-22 ②·③: 조건 여러 개(모두 채워야 한다), 등록 후보 전용 지표 ret_4h, 지표용으로만 넣은 종목(no_trade)."""
        panel = world(days=120, seed=8)
        reg_day, end = D.q_day(Q0 + 80 * D.BARS_D), Q0 + 120 * D.BARS_D
        key = lambda r: (r["market"], r["entry_q"])
        r4 = lambda r: panel[r["market"]].ret(r["entry_q"] - 1, 4 * D.BARS_H)
        base = D.candidate_trades(panel, self.cand(reg_day, vol_x=0.0), end)
        self.assertTrue({key(r) for r in D.candidate_trades(panel, self.cand(reg_day), end)} <= {key(r) for r in base})   # 거래대금 조건을 빼면 사건이 줄지 않는다
        vals = sorted(v for v in map(r4, base) if v is not None)
        thr = vals[len(vals) // 2]
        one = D.candidate_trades(panel, self.cand(reg_day, vol_x=0.0, conds=[["a", "ret_4h", "<=", thr]]), end)
        self.assertEqual({key(r) for r in one}, {key(r) for r in base if r4(r) is not None and r4(r) <= thr})   # 그 종목의 16봉 수익으로 거른다
        self.assertTrue(0 < len(one) < len(base))
        btc_only = D.candidate_trades(panel, self.cand(reg_day, vol_x=0.0, cond=["b", "btc_ret_1d", "<=", 0.0]), end)
        both = D.candidate_trades(panel, self.cand(reg_day, vol_x=0.0, conds=[["a", "ret_4h", "<=", thr], ["b", "btc_ret_1d", "<=", 0.0]]), end)
        self.assertEqual({key(r) for r in both}, {key(r) for r in one} & {key(r) for r in btc_only})   # 둘 다 채운 것만
        self.assertTrue(len(both) < len(one) and len(both) < len(btc_only))
        self.assertEqual(D.cand_conds(self.cand(reg_day, cond=["b", "btc_ret_1d", "<=", 0.0])), [["b", "btc_ret_1d", "<=", 0.0]])   # 예전 방식도 그대로
        self.assertEqual(D.cand_conds(self.cand(reg_day)), [])
        seen = {n for t in D.build_trades(panel, Q0, end) for n in t["ind"]}                           # 탐색이 훑는 지표 목록은 그대로다
        self.assertNotIn("ret_4h", seen)
        self.assertIn("btc_ret_1d", seen)
        busiest = max({r["market"] for r in base}, key=lambda m: sum(1 for r in base if r["market"] == m))
        kept = D.candidate_trades(panel, self.cand(reg_day, vol_x=0.0, no_trade=[busiest]), end)
        self.assertEqual({key(r) for r in kept}, {key(r) for r in base if r["market"] != busiest})     # 지표용 종목은 거래로 적지 않는다
        text = D.describe(self.cand(reg_day, vol_x=0.0, label="주 대상", conds=[["a", "ret_4h", "<=", -0.0592], ["b", "btc_ret_1d", "<=", -0.0173]]))
        self.assertEqual(text, "15분 −3% 이하 급락(거래대금 조건 없음) → 1시간 보유, ret_4h <= -0.0592 그리고 btc_ret_1d <= -0.0173 [주 대상]")
        self.assertEqual(D.describe(self.cand(reg_day)), "15분 −3% 이하 급락(거래대금 3배) → 1시간 보유, 조건 없음")

    def row(self, k, net, per_day=3):
        entry = Q0 + (k // per_day) * D.BARS_D + (k % per_day) * 20
        return {"market": "KRW-AAA", "entry_q": entry, "exit_q": entry + 4, "day": D.q_day(entry), "gross": net, "net": net, "move": -0.05}

    def test_verdict_once_on_the_first_thirty_trades_and_ten_days(self):
        cand = self.cand("2026-01-01")
        entry = {"rows": [self.row(k, 0.01) for k in range(29)], "decision": None}
        self.assertIsNone(D.decide_once(entry, cand, "2026-02-01")["decision"])
        entry["rows"] = [self.row(k, 0.01) for k in range(30)] + [self.row(k, -0.9) for k in range(30, 40)]
        D.decide_once(entry, cand, "2026-02-08")
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("PASS", 30))
        frozen = copy.deepcopy(entry["decision"])
        self.assertEqual(D.decide_once(entry, cand, "2026-03-01")["decision"], frozen)
        losing = {"rows": [self.row(k, -0.01) for k in range(40)], "decision": None}
        self.assertEqual(D.decide_once(losing, cand, "2026-02-08")["decision"]["verdict"], "FAIL")
        thin = {"rows": [self.row(k, 0.01) for k in range(5)], "decision": None}
        self.assertIsNone(D.decide_once(thin, cand, "2026-12-31")["decision"])                    # 12개월까지는 기다린다
        self.assertEqual(D.decide_once(copy.deepcopy(thin), self.cand("2026-01-01", expire_days=730), "2027-06-01")["decision"], None)
        self.assertEqual(D.decide_once(thin, cand, "2027-01-02")["decision"]["verdict"], "EXPIRED")   # 표본 부족 종료 (탈락이 아니다)

    def test_trades_after_the_deadline_cannot_fill_the_sample(self):
        """제3세션 지적(2026-11-13): 기한 전 27건·9일, 기한 뒤 3건이 더 들어와 30건·10일이 돼도 PASS 가 아니라 EXPIRED 여야 한다."""
        cand = self.cand(D.q_day(Q0), expire_days=20)                                              # 기한 = 시작 + 20일
        early = [self.row(k, 0.01) for k in range(27)]                                             # 하루 3건 × 9일 (시작 0~8일째)
        late = [self.row(90 + k, 0.01) for k in range(3)]                                          # 30일째 — 기한 밖
        waiting = D.decide_once({"rows": list(early), "decision": None}, cand, D.q_day(Q0 + 20 * D.BARS_D))
        self.assertIsNone(waiting["decision"])                                                     # 기한 당일까지는 기다린다
        entry = D.decide_once({"rows": early + late, "decision": None}, cand, D.q_day(Q0 + 31 * D.BARS_D))
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("EXPIRED", 27))
        filled = [self.row(k, 0.01) for k in range(30)]                                            # 기한 안에 30건·10일을 채웠다
        entry = D.decide_once({"rows": filled + late, "decision": None}, cand, D.q_day(Q0 + 200 * D.BARS_D))
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("PASS", 30))   # 실행이 늦었어도 그 자료로 판정한다

    def test_a_candidate_can_also_require_surviving_without_its_best_day(self):
        rows = [self.row(k, 0.20 if k < 3 else -0.001) for k in range(30)]                      # 첫날만 크게 벌고 나머지 9일은 조금씩 잃는다
        plain = D.decide_once({"rows": list(rows), "decision": None}, self.cand("2026-01-01"), "2026-03-01")
        self.assertEqual(plain["decision"]["verdict"], "PASS")
        strict = D.decide_once({"rows": list(rows), "decision": None}, self.cand("2026-01-01", verdict="ex_best_day"), "2026-03-01")
        self.assertEqual(strict["decision"]["verdict"], "FAIL")
        self.assertAlmostEqual(strict["decision"]["stats"]["ex_best_day"], -0.001)
        steady = [self.row(k, 0.01) for k in range(30)]
        self.assertEqual(D.decide_once({"rows": steady, "decision": None}, self.cand("2026-01-01", verdict="ex_best_day"), "2026-03-01")["decision"]["verdict"], "PASS")

    def test_a_candidate_is_scored_only_on_its_own_coins(self):
        """2026-11-14 검토: 후보가 둘이면 서로의 종목이 섞여 채점됐다. 큰 패널을 넘겨도 자기 종목의 거래만 나와야 한다."""
        panel = world(days=120, seed=8)
        reg_day, end = D.q_day(Q0 + 80 * D.BARS_D), Q0 + 120 * D.BARS_D
        small = ["KRW-BTC", "KRW-A0", "KRW-A1"]
        rows = D.candidate_trades(panel, self.cand(reg_day, universe=small), end)                 # 패널에는 알트 7개가 다 들어 있다
        self.assertTrue(rows)
        self.assertLessEqual({r["market"] for r in rows}, set(small))
        alone = D.candidate_trades({m: panel[m] for m in small}, self.cand(reg_day, universe=small), end)
        self.assertEqual(rows, alone)                                                              # 자기 종목만 받았을 때와 완전히 같다
        with self.assertRaises(RuntimeError):
            D.candidate_trades({m: panel[m] for m in small}, self.cand(reg_day), end)             # 자기 종목의 시세가 빠져 있으면 채점하지 않는다

    def test_scoring_starts_at_the_start_day_when_one_is_given(self):
        panel = world(days=120, seed=8)
        reg_day, later, end = D.q_day(Q0 + 80 * D.BARS_D), D.q_day(Q0 + 100 * D.BARS_D), Q0 + 120 * D.BARS_D
        all_rows = D.candidate_trades(panel, self.cand(reg_day), end)
        late = D.candidate_trades(panel, self.cand(reg_day, start=later), end)
        self.assertTrue(0 < len(late) < len(all_rows))
        self.assertGreaterEqual(min(r["entry_q"] for r in late), D.day_q(later) + 1)

    def test_ledger_rows_are_append_only(self):
        old = [self.row(0, 0.01), self.row(1, -0.02)]
        merged, added, mismatch = D.merge_trades(old, [dict(self.row(1, -0.02), net=0.5), self.row(2, 0.03)])
        self.assertEqual([r["net"] for r in merged], [0.01, -0.02, 0.03])
        self.assertEqual((added, len(mismatch)), (1, 1))

    def test_describe_reads_from_the_stored_definition(self):
        self.assertEqual(D.describe(self.cand("2026-01-01", hold=16, cond=["c", "btc_ret_1d", "<=", -0.02])),
                         "15분 −3% 이하 급락(거래대금 3배) → 4시간 보유, btc_ret_1d <= -0.02")


class Fetching(unittest.TestCase):
    def test_bars_page_backwards_and_exclude_the_end(self):
        calls = []

        def fake_get(url):
            self.assertIn("/candles/minutes/15?", url)
            stamp = url.split("to=")[1].replace("%3A", ":")
            to = D.to_q(dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=D.UTC))
            calls.append(to)
            qs = [q for q in range(to - 1, to - 201, -1) if q >= Q0]
            return [{"candle_date_time_utc": dt.datetime.fromtimestamp(q * 900, tz=D.UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                     "opening_price": 1.0, "high_price": 2.0, "low_price": 0.5, "trade_price": 1.5, "candle_acc_trade_price": float(q)} for q in qs]

        bars = D.fetch_bars("KRW-BTC", Q0 + 10, Q0 + 510, get=fake_get)
        self.assertEqual((bars.ts[0], bars.ts[-1], len(bars)), (Q0 + 10, Q0 + 509, 500))
        self.assertEqual(len(calls), 3)
        self.assertEqual((bars.o[0], bars.h[0], bars.c[0], bars.v[0]), (1.0, 2.0, 1.5, float(Q0 + 10)))

    def test_one_failed_market_fails_the_whole_panel(self):
        def fetch(m, s, e):
            if m == "KRW-B":
                raise RuntimeError("조회 실패")
            return D.Bars({})
        with self.assertRaises(RuntimeError):
            D.load_panel(0, 10, ("KRW-A", "KRW-B"), fetch)


if __name__ == "__main__":
    unittest.main()
