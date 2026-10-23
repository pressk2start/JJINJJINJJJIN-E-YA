"""Offline calendar, transaction-cost, and portfolio regressions for H3."""
import contextlib
import datetime as dt
import importlib.util
import io
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "trend_paper_under_test", ROOT / "scalp" / "research" / "trend_paper.py")
TP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TP)
START = "2026-10-24"
EXPECTED_BUY = 1.001 * 1.0005
EXPECTED_SELL = 0.999 * 0.9995


def fixture():
    return {
        "2026-10-21": (80.0, 80.0),
        "2026-10-22": (90.0, 90.0),
        "2026-10-23": (110.0, 110.0),
        "2026-10-24": (100.0, 80.0),
        "2026-10-25": (120.0, 130.0),
        "2026-10-26": (125.0, 125.0),
    }


class TrendPaperTests(unittest.TestCase):
    def test_not_started_has_no_invented_position_or_trades(self):
        result = TP.simulate_leg({}, START, "2026-10-23", n=2)
        self.assertEqual(result["status"], "NOT_STARTED")
        self.assertEqual(result["equity"], 1.0)
        self.assertFalse(result["position"])
        self.assertEqual(result["trades"], [])

    def test_start_day_buys_from_cash_even_when_signal_was_already_on(self):
        data = fixture()
        self.assertTrue(TP.signal_for_open(data, "2026-10-23", n=2)["on"])
        result = TP.simulate_leg(data, START, START, n=2)
        self.assertTrue(result["position"])
        self.assertEqual(result["latest_action"], "BUY")
        self.assertEqual([(t["day"], t["action"]) for t in result["trades"]],
                         [(START, "BUY")])
        self.assertAlmostEqual(result["equity"], 1 / EXPECTED_BUY)
        self.assertAlmostEqual(result["benchmark_equity"], 1 / EXPECTED_BUY)

    def test_latest_day_sell_includes_marked_return_and_sell_cost(self):
        result = TP.simulate_leg(fixture(), START, "2026-10-25", n=2)
        expected = 1.2 * EXPECTED_SELL / EXPECTED_BUY
        self.assertFalse(result["position"])
        self.assertEqual(result["latest_action"], "SELL")
        self.assertEqual([(t["day"], t["action"]) for t in result["trades"]],
                         [(START, "BUY"), ("2026-10-25", "SELL")])
        self.assertAlmostEqual(result["equity"], expected)
        self.assertAlmostEqual(result["liquidation_equity"], expected)

    def test_continuing_hold_does_not_pay_another_entry_cost(self):
        data = fixture()
        data["2026-10-24"] = (100.0, 120.0)
        result = TP.simulate_leg(data, START, "2026-10-25", n=2)
        self.assertTrue(result["position"])
        self.assertEqual(len(result["trades"]), 1)
        self.assertAlmostEqual(result["equity"], 1.2 / EXPECTED_BUY)
        self.assertAlmostEqual(result["liquidation_equity"],
                               result["equity"] * EXPECTED_SELL)

    def test_current_unfinished_close_cannot_change_open_decision(self):
        low, high = fixture(), fixture()
        low["2026-10-24"] = high["2026-10-24"] = (100.0, 120.0)
        low["2026-10-25"] = (120.0, 0.01)
        high["2026-10-25"] = (120.0, 1_000_000.0)
        a = TP.simulate_leg(low, START, "2026-10-25", n=2)
        b = TP.simulate_leg(high, START, "2026-10-25", n=2)
        self.assertEqual(a["position"], b["position"])
        self.assertEqual(a["trades"], b["trades"])
        self.assertAlmostEqual(a["equity"], b["equity"])

    def test_missing_prior_calendar_close_is_not_backfilled(self):
        data = fixture()
        del data["2026-10-23"]
        with self.assertRaises(ValueError):
            TP.signal_for_open(data, START, n=2)
        with self.assertRaises(ValueError):
            TP.simulate_leg(data, START, START, n=2)

    def test_missing_execution_day_is_not_skipped(self):
        data = fixture()
        del data["2026-10-25"]
        with self.assertRaises(ValueError):
            TP.simulate_leg(data, START, "2026-10-26", n=2)

    def test_future_open_cannot_enter_current_valuation(self):
        a, b = fixture(), fixture()
        b["2026-10-25"] = (10_000_000.0, 130.0)
        left = TP.simulate_leg(a, START, START, n=2)
        right = TP.simulate_leg(b, START, START, n=2)
        self.assertAlmostEqual(left["equity"], right["equity"])
        self.assertAlmostEqual(left["benchmark_equity"], right["benchmark_equity"])
        self.assertEqual(left["trades"], right["trades"])

    def test_cash_sleeve_stays_cash_and_benchmark_costs_are_explicit(self):
        data = fixture()
        data["2026-10-22"] = (110.0, 110.0)
        data["2026-10-23"] = (90.0, 90.0)
        result = TP.simulate_leg(data, START, "2026-10-25", n=2)
        self.assertFalse(result["position"])
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["equity"], 1.0)
        self.assertEqual(result["liquidation_equity"], 1.0)
        self.assertAlmostEqual(result["benchmark_equity"], 1.2 / EXPECTED_BUY)
        self.assertAlmostEqual(result["benchmark_liquidation_equity"],
                               1.2 * EXPECTED_SELL / EXPECTED_BUY)

    def test_default_signal_uses_fifty_prior_completed_closes(self):
        start = dt.date.fromisoformat(START)
        data = {(start - dt.timedelta(days=age)).isoformat():
                (100.0, 110.0 if age == 1 else 100.0) for age in range(1, 51)}
        data[START] = (100.0, 1_000_000.0)
        result = TP.signal_for_open(data, START)
        self.assertTrue(result["on"])
        self.assertEqual(result["decision_day"], "2026-10-23")
        self.assertAlmostEqual(result["average"], 100.2)

    def test_fifty_records_do_not_replace_fifty_calendar_days(self):
        start = dt.date.fromisoformat(START)
        data = {(start - dt.timedelta(days=age)).isoformat(): (100.0, 100.0)
                for age in range(1, 52)}
        del data[(start - dt.timedelta(days=25)).isoformat()]
        self.assertEqual(len(data), 50)
        with self.assertRaises(ValueError):
            TP.signal_for_open(data, START)

    def test_portfolio_has_independent_half_initial_capital_sleeves(self):
        invested, cash = fixture(), fixture()
        invested["2026-10-24"] = (100.0, 120.0)
        cash["2026-10-22"], cash["2026-10-23"] = (110.0, 110.0), (90.0, 90.0)
        btc = TP.simulate_leg(invested, START, "2026-10-25", n=2)
        eth = TP.simulate_leg(cash, START, "2026-10-25", n=2)
        result = TP.portfolio_result({"KRW-BTC": btc, "KRW-ETH": eth})
        self.assertAlmostEqual(result["equity"], 0.5 * 1.2 / EXPECTED_BUY + 0.5)
        self.assertAlmostEqual(result["liquidation_equity"],
                               0.5 * 1.2 * EXPECTED_SELL / EXPECTED_BUY + 0.5)

    def test_portfolio_rejects_missing_asset_or_mismatched_mark_day(self):
        leg = TP.simulate_leg(fixture(), START, START, n=2)
        with self.assertRaises(ValueError):
            TP.portfolio_result({"KRW-BTC": leg})
        other = dict(leg, mark_day="2026-10-25")
        with self.assertRaises(ValueError):
            TP.portfolio_result({"KRW-BTC": leg, "KRW-ETH": other})

    def test_nonfinite_or_nonpositive_required_prices_are_unknown(self):
        for price in (float("nan"), float("inf"), 0.0, -1.0, True):
            with self.subTest(price=price):
                data = fixture()
                data["2026-10-23"] = (110.0, price)
                with self.assertRaises(ValueError):
                    TP.simulate_leg(data, START, START, n=2)

    def test_history_fetch_reaches_fixed_start_beyond_four_hundred_bars(self):
        end = (dt.date.fromisoformat(START) + dt.timedelta(days=500)).isoformat()
        first = dt.date.fromisoformat(START) - dt.timedelta(days=50)
        days = [(first + dt.timedelta(days=i)).isoformat() for i in range(551)]
        calls = []
        def reader(url):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            cutoff = query.get("to", [None])[0]
            eligible = [day for day in days if cutoff is None or day < cutoff[:10]]
            calls.append(query)
            return [{"market": "KRW-BTC", "candle_date_time_utc": day + "T00:00:00",
                     "opening_price": 100.0, "trade_price": 100.0}
                    for day in sorted(eligible, reverse=True)[:200]]
        data = TP.candles("KRW-BTC", through=end, reader=reader)
        self.assertEqual(len(calls), 3)
        self.assertIn(first.isoformat(), data)
        self.assertIn(START, data)
        self.assertEqual(TP.simulate_leg(data, START, end)["equity"], 1.0)

    def test_nonprogressing_history_is_rejected(self):
        rows = [{"market": "KRW-BTC", "candle_date_time_utc": START + "T00:00:00",
                 "opening_price": 100.0, "trade_price": 100.0}]
        with self.assertRaisesRegex(ValueError, "pagination"):
            TP.candles("KRW-BTC", through=START, reader=lambda _: rows)

    def test_before_start_main_reports_not_started_without_zero_profit_claim(self):
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 23, 12, tzinfo=dt.timezone.utc)
        stream = io.StringIO()
        with patch.object(TP.dt, "datetime", Clock), patch.object(TP, "candles", return_value={}):
            with contextlib.redirect_stdout(stream):
                result = TP.main()
        self.assertEqual(result, 0)
        self.assertIn("TOTAL: NOT_STARTED", stream.getvalue())
        self.assertNotIn("strategy=+0.0000%", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
