"""Run: python3 -m unittest discover -s scalp/research -p 'test_micro_edge_probe.py'."""
import contextlib
import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import micro_edge_probe as probe


class ReplayTests(unittest.TestCase):
    def test_depth_and_fee(self):
        probe.self_test()

    def run_fixture(self, disconnect=False):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "input" / "2026-10-01"
            folder.mkdir(parents=True)
            records = []
            seq = 0
            for t in range(0, 14001, 1000):
                for kind in ("trade", "orderbook"):
                    seq += 1
                    r = {"type": kind, "code": "KRW-TEST", "recv_ts": t,
                         "timestamp": t, "_seq": seq}
                    if kind == "trade":
                        r.update(sequential_id=seq, ask_bid="BID",
                                 trade_price=100, trade_volume=2)
                    else:
                        r["orderbook_units"] = [
                            {"ask_price": 101, "ask_size": 100,
                             "bid_price": 100, "bid_size": 100}]
                    records.append(r)
                if disconnect and t == 6000:
                    records.append({"_meta": "connect", "recv_ts": t})
                    seq = 0
            with gzip.open(folder / "00.jsonl.gz", "wt") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")
            output = Path(root) / "out"
            argv = ["probe", str(folder.parent), "--output", str(output),
                    "--horizons", "5", "--latency-ms", "0"]
            with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
                probe.main()
            return json.loads((output / "report.json").read_text())

    def test_flat_market_loses_spread_and_fee_once(self):
        report = self.run_fixture()
        result = report["results"]["5"]["all"]
        self.assertEqual(result["n"], 1)
        expected = (100 / 101 * .9995 - 1.0005) / 1.0005 * 100
        self.assertAlmostEqual(result["mean_net_pct"], expected, places=9)
        self.assertEqual(report["status"], "EXPLORATORY_ONLY")
        self.assertIn("input_sha256_uncompressed_stream", report)

    def test_reconnect_invalidates_pending_exit(self):
        report = self.run_fixture(disconnect=True)
        self.assertEqual(report["quality_counts"]["invalidated_session_boundary"], 1)
        self.assertEqual(report["results"]["5"]["all"]["n"], 0)
        self.assertEqual(report["status"], "DATA_INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
