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
        new = D.register([], passed, "2026-12-01", ("KRW-BTC",))
        self.assertEqual([c["event"] for c in new], ["CRASH"])           # 사건마다 1개만 등록한다
        self.assertEqual(new[0]["id"], "D-202612-01")

    def test_noise_passes_nothing(self):
        for seed in (1, 2, 3):
            passed, counts, _, _ = self.run_world(world(seed=seed, planted=False))
            self.assertEqual(passed, [], seed)


class Registry(unittest.TestCase):
    def found(self, event, hold, key, low):
        return {"pop": "%s/%dh" % (event, hold // 4), "event": event, "hold": hold, "cond": [key, key, "<=", -0.01], "train": {},
                "holdout": {"day_low": low}}

    def test_one_per_event_best_lower_bound_first_and_no_duplicates(self):
        passed = [self.found("CRASH", 4, "a", 0.002), self.found("CRASH", 16, "a", 0.005), self.found("SURGE", 4, "b", 0.001),
                  self.found("BREAKOUT", 4, "c", 0.003), self.found("BREAKOUT", 16, "c", 0.0005)]
        new = D.register([], passed, "2026-12-01", ("KRW-BTC", "KRW-AAA"))
        self.assertEqual([(c["event"], c["hold"]) for c in new], [("CRASH", 16), ("BREAKOUT", 4), ("SURGE", 4)])
        self.assertEqual([c["id"] for c in new], ["D-202612-01", "D-202612-02", "D-202612-03"])
        self.assertTrue(all(c["status"] == "ACTIVE" and c["engine"] == D.ENGINE and c["universe"] == ["KRW-BTC", "KRW-AAA"] for c in new))
        again = D.register(new, passed, "2026-12-01", ("KRW-BTC", "KRW-AAA"))
        self.assertEqual([(c["event"], c["hold"], c["id"]) for c in again], [("CRASH", 4, "D-202612-04"), ("BREAKOUT", 16, "D-202612-05")])

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
            self.assertEqual(set(self.asked), {log[0]["cut"]})          # 대상 종목은 HOLDOUT 시작일 기준으로 고른다
            self.assertLess(log[0]["cut"], today)
        self.with_world(body)

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
                self.assertEqual(D.main(["--repro"]), 0)
            self.assertIn("고문의 고정 20종목이 아직 없습니다", buf.getvalue())
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

    def test_repro_prints_base_one_hour_means_next_to_the_advisors(self):
        def tr(event, hold, day, net):
            return {"event": event, "hold": hold, "day": day, "net": net}
        trades = [tr("SURGE", 4, "2024-05-01", -0.004), tr("SURGE", 4, "2025-05-01", -0.008), tr("SURGE", 16, "2024-05-01", 0.5),
                  tr("CRASH", 4, "2023-03-01", 0.002)]
        lines = D.repro_lines(trades, "2025-01-01")
        self.assertIn("SURGE    여기 1건 -0.400% / 1건 -0.800%   고문 1506건 -0.400% / 924건 -0.770%", lines[1])
        self.assertIn("BREAKOUT 여기 0건 n/a / 0건 n/a   고문 8673건 -0.280% / 7205건 -0.270%", lines[2])
        self.assertIn("CRASH    여기 1건 +0.200% / 0건 n/a   고문 1168건 +0.260% / 825건 -0.010%", lines[3])


class Forward(unittest.TestCase):
    def cand(self, registered, **kw):
        base = {"id": "D-1", "engine": D.ENGINE, "registered": registered, "event": "CRASH", "hold": 4, "cond": None, "universe": [],
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
        self.assertIsNone(D.decide_once(thin, cand, "2026-06-01")["decision"])
        self.assertEqual(D.decide_once(thin, cand, "2026-07-15")["decision"]["verdict"], "EXPIRED")

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
