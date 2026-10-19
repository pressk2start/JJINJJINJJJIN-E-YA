"""Synthetic candle comparison checks; these do not demonstrate trading profit."""
import copy
import importlib.util
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "candle_compare_under_test", ROOT / "scalp/research/micro_edge_probe.py")
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)
MARKET = "KRW-TEST"
MINUTE = 60000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
STEP = 5 * MINUTE
START = 1767225600000  # 2026-01-01 UTC
END = START + 40 * DAY
SPLIT = START + 28 * DAY
FEE = .0005
ORDER_KRW = 6000
HORIZONS = (5, 15, 60, 240)
PHASES = ("TRAIN", "HISTORICAL_HOLDOUT")


def flat_books():
    return {MARKET: {t: {"open": 100., "high": 100., "low": 100., "close": 100.}
                     for t in range(START - 2 * HOUR, END, STEP)}}


def evaluate(books, **kwargs):
    options = dict(fee_rate=FEE, order_krw=ORDER_KRW, slip_bp=0, stress_slip_bp=20)
    options.update(kwargs)
    return probe.candle_evaluate(books, START, END, **options)


def result(report, phase, signal, horizon):
    rows = [r for r in report["results"]
            if r["phase"] == phase and r["signal"] == signal and r["hold_min"] == horizon]
    if len(rows) != 1:
        raise AssertionError("expected one comparison row")
    return rows[0]


def raw_candle(stamp="2026-01-01T00:00:00", market=MARKET,
               open_price=100., high=101., low=99., close=100.):
    return {"market": market, "candle_date_time_utc": stamp, "unit": 5,
            "opening_price": open_price, "high_price": high,
            "low_price": low, "trade_price": close}


class CandleNormalizationTests(unittest.TestCase):
    def test_dates_and_times_are_utc(self):
        self.assertEqual(probe.CANDLE_STEP_MS, STEP)
        for value in ("2026-01-01", "2026-01-01T00:00:00", "2026-01-01T00:00:00Z"):
            self.assertEqual(probe.candle_time_ms(value), START)

    def test_only_completed_bars_and_identical_duplicates(self):
        first = raw_candle()
        first.pop("unit")
        rows = [raw_candle("2026-01-01T00:10:00"),
                raw_candle("2026-01-01T00:05:00"), first, copy.deepcopy(first)]
        actual = probe.candle_normalize(rows, MARKET, START + 10 * MINUTE)
        self.assertEqual(set(actual), {START, START + STEP})
        self.assertEqual(actual[START],
                         {"open": 100., "high": 101., "low": 99., "close": 100.})

    def test_invalid_market_time_unit_and_prices(self):
        wrong_unit = raw_candle()
        wrong_unit["unit"] = 1
        rows = [raw_candle(market="KRW-OTHER"), raw_candle("2026-01-01T00:01:00"),
                wrong_unit, raw_candle(open_price=math.nan), raw_candle(high=math.inf),
                raw_candle(close=0), raw_candle(high=99, low=101), raw_candle(close=102)]
        for row in rows:
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    probe.candle_normalize([row], MARKET, END)

    def test_conflicting_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            probe.candle_normalize([raw_candle(), raw_candle(close=100.5)], MARKET, END)


class CandleComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flat_report = evaluate(flat_books())

    def test_flat_prices_charge_both_fees_and_adverse_stress(self):
        expected = ((1 - FEE) / (1 + FEE) - 1) * 100
        stress = ((1 - .002) / (1 + .002) * (1 - FEE) / (1 + FEE) - 1) * 100
        for phase in PHASES:
            for horizon in HORIZONS:
                row = result(self.flat_report, phase, "ALL", horizon)
                self.assertGreater(row["completed_roundtrips"], 0)
                self.assertEqual(row["unresolved_opportunities"], 0)
                self.assertAlmostEqual(row["mean_net_pct"], expected, places=9)
                self.assertAlmostEqual(row["fee_only_mean_net_pct"], expected, places=9)
                self.assertAlmostEqual(row["stress_mean_net_pct"], stress, places=9)
                self.assertAlmostEqual(row["mean_event_net_krw"], -2 * ORDER_KRW * FEE, places=8)

    def test_skips_zero_on_identical_common_opportunities(self):
        for phase in PHASES:
            base = result(self.flat_report, phase, "ALL", 5)
            for signal in ("MOMENTUM_60M", "REVERSION_60M"):
                row = result(self.flat_report, phase, signal, 5)
                self.assertEqual(row["selected_signals"], 0)
                self.assertEqual(row["completed_roundtrips"], 0)
                self.assertEqual(row["unresolved_opportunities"], 0)
                self.assertIsNone(row["mean_net_pct"])
                self.assertEqual(row["paired_complete_opportunities"],
                                 base["paired_complete_opportunities"])
                self.assertEqual(row["signals_skipped"], base["selected_signals"])
                self.assertEqual(row["net_pct_per_complete_opportunity_including_skips"], 0)
                self.assertEqual(row["all_opportunity_mean_net_pct"], 0)

    def test_missing_execution_is_unknown_not_zero_or_later_substitution(self):
        books = flat_books()
        del books[MARKET][SPLIT + 2 * DAY + 12 * HOUR + STEP]
        damaged = result(evaluate(books), "HISTORICAL_HOLDOUT", "ALL", 5)
        base = result(self.flat_report, "HISTORICAL_HOLDOUT", "ALL", 5)
        self.assertEqual(damaged["selected_signals"], base["selected_signals"])
        self.assertGreater(damaged["unresolved_opportunities"], 0)
        self.assertLess(damaged["completed_roundtrips"], base["completed_roundtrips"])
        self.assertLess(damaged["paired_complete_opportunities"],
                        base["paired_complete_opportunities"])
        self.assertEqual(damaged["completed_roundtrips"] + damaged["unresolved_opportunities"],
                         damaged["selected_signals"])
        self.assertIsNone(damaged["all_opportunity_mean_net_pct"])
        self.assertAlmostEqual(damaged["mean_net_pct"], base["mean_net_pct"], places=9)

    def test_completed_sixty_minute_endpoint_signals(self):
        books = flat_books()
        for stamp, bar in books[MARKET].items():
            if (stamp - START) % HOUR == 55 * MINUTE:
                hour = (stamp - START) // HOUR
                close = 110. if hour % 2 else 90.
                bar.update(close=close, high=max(100., close), low=min(100., close))
        actual = evaluate(books)
        for phase in PHASES:
            base = result(actual, phase, "ALL", 5)
            momentum = result(actual, phase, "MOMENTUM_60M", 5)
            reversion = result(actual, phase, "REVERSION_60M", 5)
            self.assertGreater(momentum["selected_signals"], 0)
            self.assertGreater(reversion["selected_signals"], 0)
            self.assertEqual(momentum["selected_signals"] + reversion["selected_signals"],
                             base["selected_signals"])
            for row in (momentum, reversion):
                self.assertEqual(row["unresolved_opportunities"], 0)
                self.assertEqual(row["paired_complete_opportunities"],
                                 base["paired_complete_opportunities"])
                self.assertAlmostEqual(row["mean_net_pct"], base["mean_net_pct"], places=9)

    def test_current_and_entry_bar_closes_cannot_leak(self):
        books = flat_books()
        for stamp, bar in books[MARKET].items():
            if (stamp - START) % HOUR in (0, STEP):
                bar.update(high=1000., close=1000.)
        self.assertEqual(evaluate(books)["results"], self.flat_report["results"])

    def test_entry_waits_full_bar_and_holding_starts_at_entry(self):
        books = flat_books()
        for stamp in books[MARKET]:
            minute = ((stamp - START) % HOUR) // MINUTE
            price = 50. if minute == 0 else 120. if minute == 10 else 100.
            books[MARKET][stamp] = dict(open=price, high=price, low=price, close=price)
        actual = evaluate(books)
        up = (1.2 * (1 - FEE) / (1 + FEE) - 1) * 100
        flat = ((1 - FEE) / (1 + FEE) - 1) * 100
        for phase in PHASES:
            self.assertAlmostEqual(result(actual, phase, "ALL", 5)["mean_net_pct"], up, places=9)
            self.assertAlmostEqual(result(actual, phase, "ALL", 60)["mean_net_pct"], flat, places=9)

    def test_shared_maximum_horizon_purge(self):
        for phase, count in (("TRAIN", 28 * 24 - 4), ("HISTORICAL_HOLDOUT", 12 * 24 - 4)):
            for horizon in HORIZONS:
                row = result(self.flat_report, phase, "ALL", horizon)
                self.assertEqual(row["selected_signals"], count)
                self.assertEqual(row["completed_roundtrips"], count)
                self.assertEqual(row["paired_complete_opportunities"], count)
                self.assertEqual(row["unresolved_opportunities"], 0)

    def test_future_holdout_prices_cannot_change_training(self):
        books = flat_books()
        for stamp in books[MARKET]:
            if stamp >= SPLIT:
                books[MARKET][stamp] = dict(open=500., high=500., low=500., close=500.)
        changed = evaluate(books)
        self.assertEqual([r for r in self.flat_report["results"] if r["phase"] == "TRAIN"],
                         [r for r in changed["results"] if r["phase"] == "TRAIN"])


class CandleDownloadTests(unittest.TestCase):
    def test_exclusive_utc_pagination_and_cache_reuse(self):
        end = START + DAY
        catalog = [raw_candle(
            probe.dt.datetime.fromtimestamp((end - i * STEP) / 1000,
                                             probe.dt.timezone.utc).isoformat())
            for i in range(1, 305)]
        requests = []
        def fake_get(request, timeout):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            cursor = probe.candle_time_ms(query["to"][0])
            self.assertEqual(query["market"], [MARKET])
            self.assertEqual(query["count"], ["200"])
            self.assertNotIn("Authorization", request.headers)
            requests.append(cursor)
            batch = [r for r in catalog
                     if probe.candle_time_ms(r["candle_date_time_utc"]) < cursor][:200]
            return io.BytesIO(json.dumps(batch).encode("utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            bars, manifest = probe.candle_fetch(MARKET, START, end, tmp,
                                                opener=fake_get, sleeper=lambda _: None)
            self.assertEqual(len(requests), 2)
            self.assertLess(requests[1], requests[0])
            self.assertEqual(len(bars), 304)
            self.assertEqual(len(manifest), 2)
            def forbidden(*args, **kwargs):
                self.fail("cached fetch must not call public API again")
            again, again_manifest = probe.candle_fetch(
                MARKET, START, end, tmp, opener=forbidden, sleeper=lambda _: None)
            self.assertEqual(again, bars)
            self.assertEqual(again_manifest, manifest)

    def test_bad_exclusive_response_is_not_cached(self):
        raw = raw_candle("2026-01-02T00:00:00")
        def fake_get(*args, **kwargs):
            return io.BytesIO(json.dumps([raw]).encode("utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                probe.candle_fetch(MARKET, START, START + DAY, tmp,
                                   opener=fake_get, sleeper=lambda _: None)
            self.assertEqual(list(Path(tmp).rglob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
