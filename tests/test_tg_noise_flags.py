# -*- coding: utf-8 -*-
"""텔레그램 발송 범위 플래그 (2026-10-18) — config 기본값과 bot.py 게이트 연결 검증. bot import 없음."""
import ast
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config():
    spec = importlib.util.spec_from_file_location("cfg", os.path.join(ROOT, "config.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestTelegramNoiseFlags(unittest.TestCase):
    def setUp(self):
        self.cfg = _config()
        self.src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
        self.tree = ast.parse(self.src)

    def test_defaults_are_minimal(self):
        self.assertTrue(self.cfg.SILENT_MIDDLE_ALERTS)
        self.assertEqual(self.cfg.TG_PIPELINE_REPORT_SEC, 3600)
        self.assertFalse(self.cfg.TG_RESEARCH_DETAIL)
        self.assertEqual(self.cfg.TG_HEARTBEAT_SEC, 0)

    def test_bot_reads_each_flag(self):
        for flag in ("TG_PIPELINE_REPORT_SEC", "TG_RESEARCH_DETAIL", "TG_HEARTBEAT_SEC"):
            self.assertIn(flag, self.src, flag)

    def test_pipeline_report_gate_handles_zero_interval(self):
        fn = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "_pipeline_report")
        body = ast.get_source_segment(self.src, fn)
        self.assertIn("_PIPELINE_REPORT_INTERVAL <= 0", body)

    def test_research_detail_send_is_gated_but_still_printed(self):
        i = self.src.index('if globals().get("TG_RESEARCH_DETAIL", True):')
        before = self.src[max(0, i - 200):i]
        self.assertIn("print(_research_msg)", before)
        self.assertIn("tg_send(_research_msg)", self.src[i:i + 120])

    def test_heartbeat_gated_on_positive_interval(self):
        self.assertIn("if _HEARTBEAT_INTERVAL > 0 and time.time() - _last_heartbeat_ts", self.src)

    def test_partial_sell_failure_not_silenced(self):
        i = self.src.index("부분청산 주문 실패")
        line_start = self.src.rfind("\n", 0, i)
        self.assertIn("tg_send(", self.src[line_start:i])
        self.assertNotIn("tg_send_mid(", self.src[line_start:i])


if __name__ == "__main__":
    unittest.main(verbosity=2)
