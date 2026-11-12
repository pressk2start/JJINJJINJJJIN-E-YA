# -*- coding: utf-8 -*-
"""discover 테스트 — 심어 둔 '시장 상태 조건'을 찾아 재현까지 확인하는지, 잡음에서는 아무것도 등록하지 않는지,
미래 봉을 보지 않는지, 등록·채점·판정이 한 번씩만 일어나는지."""
import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("disc", ROOT / "scalp" / "research" / "discover.py")
D = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(D)
H0 = D.day_hour("2026-01-01")


def flat(n, price=100.0, value=1e9, start=H0):
    return {start + k: (price, price, price, price, value) for k in range(n)}


def world(days=420, seed=3, planted=True, n_alts=7):
    """BTC 는 5일마다 오르는 국면·내리는 국면이 바뀐다. 알트는 가끔 한 봉에 −5% 급락한다.
    planted 면 급락 뒤 4봉 동안: BTC 가 내리는 국면이었으면 봉당 +0.6%, 오르는 국면이었으면 봉당 −0.6%."""
    rng = random.Random(seed)
    names = ["KRW-BTC"] + ["KRW-A%d" % i for i in range(n_alts)]
    panel, px, pending = {m: {} for m in names}, {m: 100.0 for m in names}, {m: [] for m in names}
    for k in range(days * 24):
        h, down = H0 + k, (k // 120) % 2 == 1
        for m in names:
            o = px[m]
            if m == "KRW-BTC":
                r = (-0.0015 if down else 0.0015) + rng.gauss(0, 0.001)
            elif pending[m]:
                r = rng.gauss(0, 0.006) + pending[m].pop(0)
            elif rng.random() < 0.012:
                r = -0.05
                pending[m] = [(0.006 if down else -0.006) if planted else 0.0] * 4
            else:
                r = rng.gauss(0, 0.006)
            c = o * (1 + r)
            panel[m][h] = (o, max(o, c) * 1.001, min(o, c) * 0.999, c, 1e9 * (1 + rng.random()))
            px[m] = c
    return panel


def serve(panel):
    return lambda m, s, e: {h: v for h, v in panel[m].items() if s <= h < e}


class Events(unittest.TestCase):
    def test_surge_and_crash_thresholds(self):
        bars = flat(30)
        h = H0 + 29
        for close, want in ((103.0, {"SURGE"}), (97.0, {"CRASH"}), (102.9, set()), (97.1, set())):
            bars[h] = (100.0, max(100.0, close), min(100.0, close), close, 1e9)
            self.assertEqual(set(D.bar_events(bars, h)), want, close)
        del bars[h - 1]
        self.assertEqual(D.bar_events(bars, h), {})                      # 직전 봉이 없으면 판단하지 않는다

    def test_breakout_needs_a_new_high_and_three_times_the_volume(self):
        bars = flat(30)
        h = H0 + 29
        bars[h] = (100.0, 101.0, 100.0, 101.0, 3.5e9)
        self.assertEqual(set(D.bar_events(bars, h)), {"BREAKOUT"})
        bars[h] = (100.0, 101.0, 100.0, 101.0, 2.5e9)                    # 거래대금 부족
        self.assertEqual(D.bar_events(bars, h), {})
        bars[h - 5] = (100.0, 102.0, 100.0, 100.0, 1e9)                  # 이미 더 높은 고가가 있었다
        bars[h] = (100.0, 101.0, 100.0, 101.0, 3.5e9)
        self.assertEqual(D.bar_events(bars, h), {})

    def test_indicators_use_nothing_after_the_event_bar(self):
        panel = world(days=70, seed=5)
        h = H0 + 60 * 24
        before = D.indicators(panel, "KRW-A1", h)
        for m in panel:
            for t in range(h + 1, h + 200):
                if t in panel[m]:
                    panel[m][t] = (1.0, 9.0, 0.5, 5.0, 9e12)             # 미래 봉을 아무 값으로 바꿔도
        self.assertEqual(D.indicators(panel, "KRW-A1", h), before)       # 결과는 같다
        for name in ("btc_ret_1h", "btc_ret_24h", "btc_trend", "mkt_ret_1h", "breadth_24h", "size", "rel_24h", "vol_ratio",
                     "compress", "dist_7d", "session", "weekend"):
            self.assertIn(name, before)


class Trades(unittest.TestCase):
    def crash_panel(self, at=(40,), n=120):
        bars = flat(n)
        for k in at:
            bars[H0 + k] = (100.0, 100.0, 95.0, 95.0, 1e9)               # 급락 봉
            for j in range(k + 1, n):
                bars[H0 + j] = (95.0, 95.0, 95.0, 95.0, 1e9)
        return {"KRW-BTC": flat(n), "KRW-AAA": bars}

    def test_buy_next_open_sell_after_the_hold_and_pay_the_cost(self):
        panel = self.crash_panel()
        panel["KRW-AAA"][H0 + 45] = (97.0, 97.0, 97.0, 97.0, 1e9)        # 진입 4시간 뒤 봉의 시가 97
        trades = [t for t in D.build_trades(panel, H0, H0 + 120) if t["pop"] == "CRASH/4"]
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual((t["entry_h"], t["exit_h"]), (H0 + 41, H0 + 45))
        self.assertAlmostEqual(t["gross"], 97.0 / 95.0 - 1)
        self.assertAlmostEqual(t["net"], 97.0 / 95.0 * (1 - 0.0010) / (1 + 0.0010) - 1)       # 알트 비용 편도 0.10%
        btc = {"KRW-BTC": panel["KRW-AAA"]}
        self.assertAlmostEqual([x for x in D.build_trades(btc, H0, H0 + 120) if x["pop"] == "CRASH/4"][0]["net"],
                               97.0 / 95.0 * (1 - 0.0007) / (1 + 0.0007) - 1)                 # BTC 는 0.07%

    def test_a_missing_exit_bar_means_no_trade(self):
        panel = self.crash_panel()
        del panel["KRW-AAA"][H0 + 45]
        pops = {t["pop"] for t in D.build_trades(panel, H0, H0 + 120)}
        self.assertNotIn("CRASH/4", pops)
        self.assertIn("CRASH/1", pops)

    def test_one_position_at_a_time_per_population(self):
        bars = flat(120)
        for k in (40, 42, 70):                                           # 42 는 40 의 4시간 보유 안, 70 은 밖
            prev = bars[H0 + k - 1][3]
            bars[H0 + k] = (prev, prev, prev * 0.95, prev * 0.95, 1e9)
            for j in range(k + 1, 120):
                bars[H0 + j] = (prev * 0.95,) * 4 + (1e9,)
        panel = {"KRW-BTC": flat(120), "KRW-AAA": bars}
        four = [t["entry_h"] - H0 for t in D.build_trades(panel, H0, H0 + 120) if t["pop"] == "CRASH/4"]
        one = [t["entry_h"] - H0 for t in D.build_trades(panel, H0, H0 + 120) if t["pop"] == "CRASH/1"]
        self.assertEqual(four, [41, 71])
        self.assertEqual(one, [41, 43, 71])                              # 1시간 보유는 42 의 신호도 받는다


class Search(unittest.TestCase):
    def setUp(self):
        self.saved = (D.TRAIN_MIN_N, D.TRAIN_MIN_DAYS, D.HOLD_MIN_N, D.HOLD_MIN_DAYS)
        D.TRAIN_MIN_N, D.TRAIN_MIN_DAYS, D.HOLD_MIN_N, D.HOLD_MIN_DAYS = 60, 20, 30, 10

    def tearDown(self):
        D.TRAIN_MIN_N, D.TRAIN_MIN_DAYS, D.HOLD_MIN_N, D.HOLD_MIN_DAYS = self.saved

    def run_world(self, panel, days=360):
        today = D.hour_day(H0 + 420 * 24)
        return D.run(today, days, tuple(sorted(panel)), fetch=serve(panel), log=lambda s: None)

    def test_a_planted_market_condition_is_found_and_replicated(self):
        found, lines = self.run_world(world(planted=True))
        four = [f for f in found if f["pop"] == "CRASH/4"]
        self.assertEqual(len(four), 1)
        cond = four[0]["cond"]
        self.assertIn(cond[1], ("btc_ret_24h", "btc_ret_1h"))           # 갈림선은 시장 상태(BTC 가 내리는 중)
        self.assertEqual(cond[2], "<=")
        self.assertGreater(four[0]["holdout"]["net"], 0.01)
        base = next(ln for ln in lines if ln.startswith("== CRASH/4"))
        self.assertIn("net -", base)                                     # 조건 없이 통째로는 음수인 모집단이다

    def test_noise_finds_nothing(self):
        for seed in (1, 2, 3):
            found, _ = self.run_world(world(seed=seed, planted=False))
            self.assertEqual(found, [], seed)


class Registry(unittest.TestCase):
    def found(self, pop, key, t):
        return {"pop": pop, "cond": [key + " low", key, "<=", -0.01], "train": {"t": 3.0}, "holdout": {"t": t}}

    def test_at_most_three_a_month_best_first_and_no_duplicates(self):
        found = sorted([self.found("CRASH/%d" % h, k, t) for h, k, t in ((1, "a", 2.1), (4, "a", 4.0), (24, "a", 3.0), (4, "b", 2.5), (1, "c", 2.2))],
                       key=lambda f: -f["holdout"]["t"])
        new = D.register([], found, "2026-12-01", ("KRW-BTC", "KRW-AAA"))
        self.assertEqual([c["id"] for c in new], ["D-202612-01", "D-202612-02", "D-202612-03"])
        self.assertEqual([(c["hold"], c["cond"][1]) for c in new], [(4, "a"), (24, "a"), (4, "b")])
        self.assertTrue(all(c["status"] == "ACTIVE" and c["engine"] == D.ENGINE and c["move"] == D.MOVE for c in new))
        again = D.register(new, found, "2026-12-01", ("KRW-BTC", "KRW-AAA"))   # 같은 달에 한 번 더 돌려도
        self.assertEqual([(c["hold"], c["cond"][1]) for c in again], [(1, "c"), (1, "a")])   # 이미 있는 것은 빼고 번호는 이어진다
        self.assertEqual([c["id"] for c in again], ["D-202612-04", "D-202612-05"])

    def test_register_writes_nothing_while_the_frame_is_a_draft(self):
        panel = world(planted=True)
        saved = (D.fetch_hours, D.TRAIN_MIN_N, D.TRAIN_MIN_DAYS, D.HOLD_MIN_N, D.HOLD_MIN_DAYS, D.pick_universe, D.HISTORY_DAYS, D.FRAME_APPROVED)
        D.fetch_hours, D.pick_universe, D.HISTORY_DAYS = serve(panel), (lambda today: tuple(sorted(panel))), 360
        D.TRAIN_MIN_N, D.TRAIN_MIN_DAYS, D.HOLD_MIN_N, D.HOLD_MIN_DAYS = 60, 20, 30, 10
        try:
            with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
                today = D.hour_day(H0 + 420 * 24)
                D.FRAME_APPROVED = False
                D.main(["--register", "--out", tmp, "--today", today])
                self.assertFalse((Path(tmp) / "registry.json").exists())
                self.assertIn("등록하지 않음", (Path(tmp) / "DISCOVERY.md").read_text(encoding="utf-8"))
                D.FRAME_APPROVED = True
                D.main(["--register", "--out", tmp, "--today", today])
                reg = json.loads((Path(tmp) / "registry.json").read_text(encoding="utf-8"))
                self.assertTrue(1 <= len(reg) <= D.MAX_NEW)
                self.assertEqual({c["registered"] for c in reg}, {today})
                self.assertEqual({tuple(c["universe"]) for c in reg}, {tuple(sorted(panel))})      # 그때의 종목 목록이 후보에 고정된다
        finally:
            (D.fetch_hours, D.TRAIN_MIN_N, D.TRAIN_MIN_DAYS, D.HOLD_MIN_N, D.HOLD_MIN_DAYS, D.pick_universe, D.HISTORY_DAYS, D.FRAME_APPROVED) = saved


class Forward(unittest.TestCase):
    def cand(self, registered):
        return {"id": "D-1", "engine": D.ENGINE, "registered": registered, "event": "CRASH", "hold": 4, "cond": None, "universe": [],
                "move": 0.04, "vol_x": 3.0, "cost": {"KRW-BTC": 0.0007}, "cost_other": 0.0010, "status": "ACTIVE"}

    def test_only_events_after_registration_count_and_stored_definitions_are_used(self):
        panel = world(days=120, seed=8)
        reg_day = D.hour_day(H0 + 80 * 24)
        rows = D.candidate_trades(panel, self.cand(reg_day), H0 + 120 * 24)
        self.assertTrue(rows)
        self.assertGreaterEqual(min(r["entry_h"] for r in rows), D.day_hour(reg_day) + 1)
        strict = dict(self.cand(reg_day), move=0.06)                     # 등록 때 문턱이 6% 였다면 5% 급락은 사건이 아니다
        self.assertEqual(D.candidate_trades(panel, strict, H0 + 120 * 24), [])
        with self.assertRaises(RuntimeError):
            D.candidate_trades(panel, dict(self.cand(reg_day), engine="0"), H0 + 120 * 24)

    def test_condition_filters_forward_trades(self):
        panel = world(days=120, seed=8)
        reg_day = D.hour_day(H0 + 80 * 24)
        base = D.candidate_trades(panel, self.cand(reg_day), H0 + 120 * 24)
        cond = dict(self.cand(reg_day), cond=["btc_ret_24h low", "btc_ret_24h", "<=", 0.0])
        some = D.candidate_trades(panel, cond, H0 + 120 * 24)
        self.assertTrue(0 < len(some) < len(base))

    def row(self, k, net, per_day=3):
        entry = H0 + (k // per_day) * 24 + (k % per_day) * 5
        return {"market": "KRW-AAA", "entry_h": entry, "exit_h": entry + 4, "day": D.hour_day(entry), "gross": net, "net": net, "move": -0.05}

    def test_verdict_once_on_the_first_thirty_trades_and_ten_days(self):
        cand = self.cand("2026-01-01")
        entry = {"rows": [self.row(k, 0.01) for k in range(29)], "decision": None}
        D.decide_once(entry, cand, "2026-02-01")
        self.assertIsNone(entry["decision"])
        entry["rows"] = [self.row(k, 0.01) for k in range(30)] + [self.row(k, -0.9) for k in range(30, 40)]
        D.decide_once(entry, cand, "2026-02-08")
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("PASS", 30))
        frozen = copy.deepcopy(entry["decision"])
        D.decide_once(entry, cand, "2026-03-01")
        self.assertEqual(entry["decision"], frozen)
        losing = {"rows": [self.row(k, -0.01) for k in range(40)], "decision": None}
        self.assertEqual(D.decide_once(losing, cand, "2026-02-08")["decision"]["verdict"], "FAIL")

    def test_a_candidate_that_never_fills_up_expires(self):
        cand = self.cand("2026-01-01")
        entry = {"rows": [self.row(k, 0.01) for k in range(5)], "decision": None}
        D.decide_once(entry, cand, "2026-06-01")
        self.assertIsNone(entry["decision"])
        D.decide_once(entry, cand, "2026-07-15")
        self.assertEqual(entry["decision"]["verdict"], "EXPIRED")

    def test_ledger_rows_are_append_only(self):
        old = [self.row(0, 0.01), self.row(1, -0.02)]
        merged, added, mismatch = D.merge_trades(old, [dict(self.row(1, -0.02), net=0.5), self.row(2, 0.03)])
        self.assertEqual([r["net"] for r in merged], [0.01, -0.02, 0.03])
        self.assertEqual((added, len(mismatch)), (1, 1))


class Fetching(unittest.TestCase):
    def test_hour_bars_page_backwards_and_exclude_the_end_hour(self):
        origin, calls = H0, []

        def fake_get(url):
            stamp = url.split("to=")[1].replace("%3A", ":")
            to = D.to_hour(dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=D.UTC))
            calls.append(to)
            hours = [h for h in range(to - 1, to - 201, -1) if h >= origin]
            return [{"candle_date_time_utc": dt.datetime.fromtimestamp(h * 3600, tz=D.UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                     "opening_price": 1.0, "high_price": 2.0, "low_price": 0.5, "trade_price": 1.5, "candle_acc_trade_price": float(h)} for h in hours]

        bars = D.fetch_hours("KRW-BTC", origin + 10, origin + 510, get=fake_get)
        self.assertEqual((min(bars), max(bars), len(bars)), (origin + 10, origin + 509, 500))
        self.assertEqual(len(calls), 3)
        self.assertEqual(bars[origin + 10], (1.0, 2.0, 0.5, 1.5, float(origin + 10)))

    def test_one_failed_market_fails_the_whole_panel(self):
        def fetch(m, s, e):
            if m == "KRW-B":
                raise RuntimeError("조회 실패")
            return {}
        with self.assertRaises(RuntimeError):
            D.load_panel(0, 10, ("KRW-A", "KRW-B"), fetch)


class Universe(unittest.TestCase):
    def test_top_by_traded_value_without_stablecoins_and_always_with_btc(self):
        value = {"KRW-USDT": 9e12, "KRW-AAA": 5e12, "KRW-BBB": 4e12, "KRW-CCC": 3e12, "KRW-BTC": 1e9, "KRW-DDD": 2e12}
        top = D.pick_universe("2026-12-01", n=3, list_markets=lambda: list(value), daily_value=lambda m: value[m],
                              listed_before=lambda m: m != "KRW-AAA")
        self.assertEqual(top, ("KRW-BBB", "KRW-BTC", "KRW-CCC"))          # 스테이블코인·상장 2년 미만(AAA) 제외, BTC 는 항상 포함
        self.assertEqual(D.describe({"event": "SURGE", "hold": 4, "cond": None, "move": 0.03, "vol_x": 3.0}),
                         "1시간 +3% 이상 급등 → 4시간 보유, 조건 없음")


if __name__ == "__main__":
    unittest.main()
