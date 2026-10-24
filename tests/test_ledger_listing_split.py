# -*- coding: utf-8 -*-
"""ledger_listing_split 순수 로직 테스트 (원장 없음)."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lls", ROOT / "scalp" / "research" / "ledger_listing_split.py")
LS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LS)


class TestLedgerListingSplit(unittest.TestCase):
    def test_buckets_and_join(self):
        listed = {"KRW-NEW": "2026-09-01", "KRW-MID": "2026-08-01"}
        rows = [
            {"event_type": "ENTRY_FILLED", "trade_id": "a", "market": "KRW-NEW", "entry_time": "2026-09-03T01:00:00"},
            {"event_type": "TRADE_CLOSED", "trade_id": "a", "market": "KRW-NEW", "net_pnl_krw_cashflow": -120.0,
             "net_pnl_pct": -1.2, "exit_reason": "stop"},
            {"event_type": "ENTRY_FILLED", "trade_id": "b", "market": "KRW-MID", "entry_time": "2026-08-20T01:00:00"},
            {"event_type": "TRADE_CLOSED", "trade_id": "b", "market": "KRW-MID", "net_pnl_krw": 50.0, "net_pnl_pct": 0.5},
            {"event_type": "ENTRY_FILLED", "trade_id": "c", "market": "KRW-BTC", "entry_time": "2026-08-20T01:00:00"},
            {"event_type": "ENTRY_FILLED", "trade_id": "a", "leg": "add", "market": "KRW-NEW", "entry_time": "2026-09-04T01:00:00"},
        ]
        per, summary = LS.split(rows, listed)
        by = {p["market"]: p for p in per}
        self.assertEqual((by["KRW-NEW"]["age"], by["KRW-NEW"]["bucket"], by["KRW-NEW"]["pnl_krw"]), (2, "D0-7", -120.0))
        self.assertEqual((by["KRW-MID"]["age"], by["KRW-MID"]["bucket"], by["KRW-MID"]["pnl_krw"]), (19, "D8-30", 50.0))
        self.assertEqual((by["KRW-BTC"]["bucket"], by["KRW-BTC"]["closed"]), ("unlisted/old", False))
        self.assertEqual(len(per), 3)                                 # add leg 는 seed 에 합쳐짐
        self.assertEqual(summary["D0-7"], {"n": 1, "sum_krw": -120, "mean_pct": -1.2, "win": 0.0})
        self.assertEqual(summary["unlisted/old"]["n"], 0)            # 미종결은 집계 제외

    def test_listed_snapshot_sane(self):
        self.assertGreater(len(LS.LISTED), 150)
        self.assertTrue(all(len(v) == 10 and v > "2024-09-01" for v in LS.LISTED.values()))
        self.assertEqual(LS.LISTED["KRW-CASHCAT"], "2026-09-28")


if __name__ == "__main__":
    unittest.main(verbosity=2)
