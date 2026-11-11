# -*- coding: utf-8 -*-
"""shadow_salvage(H27) 테스트 — 심어 둔 '조건부 엣지'를 TRAIN 에서 찾아 HOLDOUT 에서 통과시키는지, 잡음은 통과시키지 않는지,
진입 뒤에 생기는 값(mfe 등)을 조건으로 쓰지 않는지."""
import contextlib
import datetime as dt
import importlib.util
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ss", ROOT / "scalp" / "research" / "shadow_salvage.py")
SS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SS)
T0 = dt.datetime(2026, 9, 1, 0, 0, tzinfo=SS.KST).timestamp()


def rec(day, i, pnl, inds, hold=120.0, reason="AT익절", market="KRW-AAA"):
    entry = T0 + day * 86400 + i * 3600 + 600
    full = {"ob_slip_buy_100k": 0.05, "ob_exit_slip_sell_100k": 0.05}
    full.update(inds)
    return {"pnl": pnl, "mfe": max(pnl, 0.0), "hold": hold, "exit_reason": reason, "exit_ts": entry + hold,
            "signal_id": "%s:%d" % (market, int(entry)), "inds": full}


def synth(planted, seed=3, days=60, per_day=8):
    """planted 면 rsi_5m < 40 인 거래만 비용 전 +0.6%, 나머지 −0.1%. 비용은 왕복 0.20%."""
    rng = random.Random(seed)
    recs = []
    for d in range(days):
        day_fx = rng.gauss(0, 0.001)
        for i in range(per_day):
            rsi = rng.uniform(20, 80)
            mean = 0.006 if (planted and rsi < 40) else -0.001
            pnl = mean + day_fx + rng.gauss(0, 0.005)
            inds = {"rsi_5m": rsi, "vr5": rng.uniform(1, 9), "mfe_60s": max(pnl, 0.0) + 0.001, "dd_peak_60s": rng.random() * 0.01}
            inds.update({"x%d" % k: rng.gauss(0, 1) for k in range(6)})
            recs.append(rec(d, i, pnl, inds))
    return {"R1:strat": {"route": "R1", "trade_records": recs}}


def load(stats):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "shadow_stats.json"
        p.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
        return SS.load_snapshots([str(p)])


class ToTrade(unittest.TestCase):
    def test_net_subtracts_fee_and_measured_book_cost_and_drops_post_entry_keys(self):
        r = rec(0, 0, 0.004, {"rsi_5m": 33.0, "mfe_60s": 0.01, "ob_exit_bid1_krw": 5e6, "at_tier": 1, "mae_60s": -0.002,
                              "ob_bid_cum5_krw": 3e6, "ob_ask_cum5_krw": 1e6})
        tr, why = SS.to_trade("R1:s", r)
        self.assertIsNone(why)
        self.assertAlmostEqual(tr["net"], 0.004 - 0.0010 - 0.0010)
        self.assertEqual(tr["market"], "KRW-AAA")
        self.assertEqual(tr["day"], "2026-09-01")
        self.assertEqual(tr["ind"]["rsi_5m"], 33.0)
        self.assertAlmostEqual(tr["ind"]["ob_imb5"], 0.75)
        for leak in ("mfe_60s", "ob_exit_bid1_krw", "at_tier", "mae_60s", "ob_exit_slip_sell_100k"):
            self.assertNotIn(leak, tr["ind"])

    def test_exit_cost_falls_back_to_entry_time_sell_slip(self):
        r = rec(0, 0, 0.0, {})
        del r["inds"]["ob_exit_slip_sell_100k"]
        self.assertEqual(SS.to_trade("k", r), (None, "no_cost"))
        r["inds"]["ob_slip_sell_100k"] = 0.20
        self.assertAlmostEqual(SS.to_trade("k", r)[0]["cost"], 0.0025)

    def test_unusable_records_are_dropped_with_a_reason(self):
        self.assertEqual(SS.to_trade("k", rec(0, 0, 0.001, {}, reason="가격없음"))[1], "price_unavailable")
        r = rec(0, 0, 0.001, {})
        del r["exit_ts"]
        self.assertEqual(SS.to_trade("k", r)[1], "no_time_or_pnl")

    def test_day_is_the_kst_date_of_entry_not_of_exit(self):
        r = rec(0, 0, 0.0, {})
        r["exit_ts"] = T0 + 86400 + 30            # 다음 날 00:00:30 KST 청산, 60초 보유 → 진입은 전날 23:59:30
        r["hold"] = 60.0
        self.assertEqual(SS.to_trade("k", r)[0]["day"], "2026-09-01")


class Snapshots(unittest.TestCase):
    def test_overlapping_snapshots_count_each_trade_once(self):
        a = synth(False, days=20)
        b = {"R1:strat": {"route": "R1", "trade_records": a["R1:strat"]["trade_records"][80:] + [rec(30, 0, 0.001, {"rsi_5m": 50})]}}
        with tempfile.TemporaryDirectory() as tmp:
            pa, pb = Path(tmp) / "a.json", Path(tmp) / "b.json"
            pa.write_text(json.dumps(a), encoding="utf-8")
            pb.write_text(json.dumps(b), encoding="utf-8")
            by_key, _ = SS.load_snapshots([str(pa), str(pb)])
        self.assertEqual(len(by_key["R1:strat"]), 161)
        ts = [t["entry_ts"] for t in by_key["R1:strat"]]
        self.assertEqual(ts, sorted(ts))

    def test_split_cuts_on_a_day_boundary_near_sixty_percent(self):
        by_key, _ = load(synth(False))
        train, hold = SS.split(by_key["R1:strat"])
        self.assertEqual(len(train) + len(hold), 480)
        self.assertLess(max(t["day"] for t in train), min(t["day"] for t in hold))
        self.assertAlmostEqual(len(train) / 480.0, 0.60, delta=0.03)


class Stats(unittest.TestCase):
    def test_t_quantile_matches_tables(self):
        for p, df, want in ((0.975, 10, 2.228), (0.95, 5, 2.015), (0.99, 20, 2.528), (0.95, 1000, 1.646), (0.9975, 8, 3.833)):
            self.assertAlmostEqual(SS.t_quantile(p, df), want, places=2)

    def test_same_day_trades_do_not_count_as_independent(self):
        def mk(day, net):
            return {"day": "2026-09-%02d" % (day + 1), "net": net, "gross": net, "ind": {}}
        day_means = [0.004, -0.002, 0.003, 0.001, -0.001, 0.005]
        once = SS.cluster_stats([mk(d, v) for d, v in enumerate(day_means)])
        many = SS.cluster_stats([mk(d, v) for d, v in enumerate(day_means) for _ in range(20)])
        self.assertEqual(many["n"], 120)
        self.assertEqual(many["df"], 5)
        self.assertAlmostEqual(many["t"], once["t"], places=9)     # 같은 날 값을 20번 복제해도 t 는 그대로


class Salvage(unittest.TestCase):
    def test_planted_condition_is_found_on_train_and_passes_holdout(self):
        by_key, dropped = load(synth(True))
        out = SS.run(by_key, dropped, log=lambda s: None)
        self.assertEqual(out["eligible"], ["R1:strat"])
        self.assertEqual(len(out["locked"]), 1)
        self.assertEqual(out["locked"][0]["cond"][1:3], ["rsi_5m", "<="])
        v = out["verdicts"][0]
        self.assertTrue(v["hold"]["pass"])
        self.assertLess(v["hold_base_net"], 0)                      # 통째로는 음수인 시나리오가
        self.assertGreater(v["hold"]["low"], 0)                     # 조건을 붙이면 하한까지 양수

    def test_noise_does_not_pass(self):
        passed = 0
        for seed in range(8):
            by_key, dropped = load(synth(False, seed=seed))
            out = SS.run(by_key, dropped, log=lambda s: None)
            passed += sum(1 for v in out["verdicts"] if v["hold"]["pass"])
        self.assertEqual(passed, 0)

    def test_post_entry_values_are_never_offered_as_conditions(self):
        by_key, _ = load(synth(True))
        train, _ = SS.split(by_key["R1:strat"])
        names = {c[1] for c in SS.make_conditions(train)}
        self.assertIn("rsi_5m", names)
        self.assertFalse({"mfe_60s", "dd_peak_60s", "ob_exit_slip_sell_100k"} & names)

    def test_small_routes_are_listed_but_not_judged(self):
        by_key, dropped = load(synth(True, days=10))
        out = SS.run(by_key, dropped, log=lambda s: None)
        self.assertEqual((out["eligible"], out["locked"]), ([], []))

    def test_two_valued_indicator_becomes_one_condition_per_value(self):
        train = [{"day": "d", "net": 0.0, "gross": 0.0, "ind": {"flag": float(i % 4 == 0)}} for i in range(40)]
        self.assertEqual([(c[2], c[3]) for c in SS.make_conditions(train)], [("<=", 0.0), (">=", 1.0)])

    def test_forward_judges_only_trades_after_the_freeze(self):
        by_key, dropped = load(synth(True))
        frozen = SS.run(by_key, dropped, log=lambda s: None)
        self.assertGreater(frozen["freeze_ts"], T0)
        later = synth(True, seed=11)
        for r in later["R1:strat"]["trade_records"]:               # 같은 규칙의 새 기간: 70일 뒤로 민다
            r["exit_ts"] += 70 * 86400
            r["signal_id"] = "KRW-AAA:%d" % int(r["exit_ts"] - r["hold"])
        merged = {"R1:strat": {"trade_records": synth(True)["R1:strat"]["trade_records"] + later["R1:strat"]["trade_records"]}}
        by_key2, _ = load(merged)
        out = SS.forward(by_key2, frozen, log=lambda s: None)
        fw = out["forward"][0]["forward"]
        self.assertTrue(fw["pass"])
        self.assertLess(fw["n"], 480 // 2)                          # 동결 이전 거래는 세지 않는다
        none_yet = SS.forward(by_key, frozen, log=lambda s: None)["forward"][0]["forward"]
        self.assertEqual((none_yet["n"], none_yet["pass"]), (0, False))


def bars_from(prices, start):
    """5분봉 종가 dict. prices[i] 는 start + 300*i 에 시작한 봉의 종가."""
    return {int(start) + 300 * i: float(v) for i, v in enumerate(prices)}


class MarketState(unittest.TestCase):
    def test_uses_only_the_bar_that_closed_before_entry(self):
        start = int(T0)                                             # 00:00 KST, 5분 경계
        bars = bars_from([100 + i for i in range(40)], start)       # 봉 i 종가 = 100 + i
        entry = start + 300 * 20 + 130                              # 봉 20 이 진행 중인 시각 → 마지막으로 끝난 봉은 19
        st = SS.btc_state(bars, entry)
        self.assertAlmostEqual(st["btc_ret_15m"], 119.0 / 116.0 - 1)
        self.assertAlmostEqual(st["btc_ret_60m"], 119.0 / 107.0 - 1)
        bars[start + 300 * 20] = 9999.0                             # 진행 중인 봉이 어떻게 끝나든 결과는 같다
        self.assertEqual(SS.btc_state(bars, entry), st)
        self.assertAlmostEqual(SS.btc_state(bars, start + 300 * 20)["btc_ret_15m"], 119.0 / 116.0 - 1)   # 정각 진입도 봉 19

    def test_missing_bars_give_no_value_instead_of_a_guess(self):
        start = int(T0)
        bars = bars_from([100.0] * 40, start)
        del bars[start + 300 * 7]                                   # 60분 전 기준 봉이 없다
        st = SS.btc_state(bars, start + 300 * 20 + 10)
        self.assertIn("btc_ret_15m", st)
        self.assertNotIn("btc_ret_60m", st)
        self.assertEqual(SS.btc_state({}, start + 6000), {})

    def test_market_condition_can_rescue_a_route(self):
        """코인 지표로는 안 갈리고 'BTC 가 직전 1시간에 빠졌는가' 로만 갈리는 route → btc_ret_60m low 가 잠기고 통과."""
        rng = random.Random(21)
        start = int(T0) - 7200
        price, bars = 100.0, {}
        for i in range(62 * 288 + 48):
            price *= 1 + rng.gauss(0, 0.002)
            bars[start + 300 * i] = price
        recs = []
        for d in range(60):
            for i in range(8):
                entry = T0 + d * 86400 + i * 3600 + 600
                dip = SS.btc_state(bars, entry)["btc_ret_60m"] < -0.004
                pnl = (0.008 if dip else -0.001) + rng.gauss(0, 0.005)
                recs.append(rec(d, i, pnl, {"rsi_5m": rng.uniform(20, 80), "vr5": rng.uniform(1, 9)}))
        by_key, dropped = load({"R1:strat": {"trade_records": recs}})
        self.assertEqual(SS.attach_market(by_key, bars), 1.0)
        out = SS.run(by_key, dropped, log=lambda s: None)
        self.assertEqual(out["locked"][0]["cond"][1:3], ["btc_ret_60m", "<="])
        self.assertTrue(out["verdicts"][0]["hold"]["pass"])

    def test_download_pages_backwards_and_the_cache_is_reused(self):
        origin, calls = 1790000100, []                              # 5분 경계

        def fake_get(url):
            to = dt.datetime.strptime(url.split("to=")[1].split("&")[0].replace("%3A", ":"), "%Y-%m-%dT%H:%M:%SZ")
            to = int(to.replace(tzinfo=SS.UTC).timestamp())
            calls.append(to)
            last = (to - 1) // 300 * 300
            starts = [t for t in range(last, last - 300 * 200, -300) if t >= origin]
            return [{"candle_date_time_utc": dt.datetime.fromtimestamp(t, SS.UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                     "trade_price": 1000.0 + (t - origin) / 300} for t in starts]

        real, SS._get = SS._get, fake_get
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cache = Path(tmp) / "btc_5m.json"
                t_from, t_to = origin + 300 * 10, origin + 300 * 510
                bars = SS.load_btc_bars(t_from, t_to, cache)
                self.assertEqual((min(bars), max(bars), len(bars)), (t_from, t_to - 300, 500))
                self.assertEqual(bars[t_from], 1010.0)
                n = len(calls)
                self.assertEqual(n, 3)                              # 200개씩 뒤로 세 번
                self.assertEqual(SS.load_btc_bars(t_from, t_to, cache), bars)
                self.assertEqual(len(calls), n)                     # 두 번째는 캐시만
        finally:
            SS._get = real


class Cli(unittest.TestCase):
    def test_runs_offline_and_skips_patterns_that_match_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "shadow_stats_a.json").write_text(json.dumps(synth(True)), encoding="utf-8")
            out = Path(tmp) / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = SS.main([str(Path(tmp) / "shadow_stats*.json"), str(Path(tmp) / "nowhere" / "*.json"),
                                "--no-market", "--out", str(out)])
                none = SS.main([str(Path(tmp) / "nowhere" / "*.json"), "--out", str(out)])
            self.assertEqual((code, none), (0, 2))
            brief = (out / "h27_brief.txt").read_text(encoding="utf-8")
            self.assertIn("MARKET STATE: UNAVAILABLE", brief)
            self.assertIn("H27 VERDICT: PASS", brief)

    def test_records_with_an_impossible_time_are_dropped(self):
        r = rec(0, 0, 0.001, {})
        r["exit_ts"] = 120.0
        self.assertEqual(SS.to_trade("k", r)[1], "bad_time")


class Reporting(unittest.TestCase):
    def test_daymean_counts_each_day_once(self):
        def mk(day, net):
            return {"day": day, "net": net, "gross": net, "ind": {}}
        st = SS.cluster_stats([mk("a", 0.01)] * 9 + [mk("b", -0.01)])
        self.assertAlmostEqual(st["net"], 0.008)                    # 거래 평균은 하루에 몰린 9건이 끌어올린다
        self.assertAlmostEqual(st["daymean"], 0.0)                  # 하루 한 표로 세면 0

    def test_excluded_names_and_kept_share_are_reported_for_audit(self):
        stats = synth(True)
        for r in stats["R1:strat"]["trade_records"][:48]:
            del r["inds"]["ob_slip_buy_100k"]                       # 비용 지표가 없는 기록 10%
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "shadow_stats.json"
            p.write_text(json.dumps(stats), encoding="utf-8")
            excluded, lines = set(), []
            by_key, dropped = SS.load_snapshots([str(p)], excluded=excluded)
            out = SS.run(by_key, dropped, log=lines.append, excluded=excluded)
        self.assertEqual(dropped["R1:strat"], {"no_cost": 48})
        self.assertTrue(any("kept 90%" in ln for ln in lines))
        self.assertEqual(out["indicators_excluded"], ["dd_peak_60s", "mfe_60s", "ob_exit_slip_sell_100k"])
        self.assertIn("rsi_5m", out["indicators_offered"])
        self.assertFalse(set(out["indicators_offered"]) & set(out["indicators_excluded"]))

    def test_post_entry_keys_written_by_the_bot_are_all_excluded(self):
        """bot.py 가 청산 시점에 inds 에 더하는 키(2026-11-10 대조)가 하나도 조건 후보가 되지 않는다."""
        late = ["mfe_peak_sec", "mfe_30s", "dd_peak_30s", "mfe_60s", "dd_peak_60s", "mae_60s", "at_ob_slip", "at_trail_pct",
                "at_tier", "at_max_hold", "ob_exit_bid1_krw", "ob_exit_ask1_krw", "ob_exit_slip_sell_100k",
                "ob_exit_slip_sell_300k", "ob_exit_slip_sell_500k"]
        self.assertFalse([k for k in late if SS.is_decision_key(k)])
        for early in ("atr_pct", "rsi_5m", "ob_slip_buy_100k", "gate_dd_peak", "sve2_score", "tick_buy_10s", "btc_ret_60m"):
            self.assertTrue(SS.is_decision_key(early))


if __name__ == "__main__":
    unittest.main()
