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
        self.assertTrue(self.cfg.TG_REPORT_BRIEF)

    def test_bot_reads_each_flag(self):
        for flag in ("TG_PIPELINE_REPORT_SEC", "TG_RESEARCH_DETAIL", "TG_HEARTBEAT_SEC", "TG_REPORT_BRIEF"):
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


class TestBriefReport(unittest.TestCase):
    """텔레그램 요약을 3줄(상태·보유·지연)로 (고문 결정 2026-11-13). 상세는 로그에만 남는다. bot import 없음."""

    def setUp(self):
        self.src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
        self.tree = ast.parse(self.src)

    def fn_source(self, name):
        fn = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment(self.src, fn)

    def brief(self):
        """_brief_report 만 떼어 실행한다 — 인자만 쓰는 순수 함수여야 가능하다."""
        scope = {}
        exec(self.fn_source("_brief_report"), scope)
        return scope["_brief_report"]

    def sample(self, **over):
        args = dict(live_cnt=0, live_wins=0, live_pnl=0.0, limit_str="-2.0%", guard_warn="", open_markets=[], raw=46, gate_pass=14,
                    sent=0, entry_disabled=True, scan_p95_s=9.0, slow_limit_s=25.0, cache_rate=60.0, suspend_block=0,
                    deploy_hash="b53abfa", deploy_ts="11/10 02:35")
        args.update(over)
        return self.brief()(**args)

    def test_the_report_of_2026_11_13_becomes_three_lines(self):
        self.assertEqual(self.sample().split("\n"), [
            "⚪ LIVE 0전0승 PnL+0.00%/-2.0% | 누적 신호 46 → 통과 14 → 주문 0 (진입 차단 중)",
            "보유 0개",
            "지연 p95 9.0s · cache 60% | b53abfa 배포 11/10 02:35",
        ])

    def test_holdings_trades_and_warnings_show_up(self):
        text = self.sample(live_cnt=4, live_wins=3, live_pnl=-0.0151, guard_warn=" ⚠한도-2.0%의 76%", entry_disabled=False, sent=4,
                           open_markets=["KRW-BTC", "KRW-ETH"], scan_p95_s=31.2, suspend_block=2)
        lines = text.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "🟢 LIVE 4전3승 wr75% PnL-1.51%/-2.0% ⚠한도-2.0%의 76% | 누적 신호 46 → 통과 14 → 주문 4")
        self.assertEqual(lines[1], "보유 2개 (BTC, ETH)")
        self.assertEqual(lines[2], "지연 p95 31.2s⚠ · cache 60% · ⚠차단 2 | b53abfa 배포 11/10 02:35")
        many = self.sample(open_markets=["KRW-A%d" % k for k in range(7)]).split("\n")[1]
        self.assertEqual(many, "보유 7개 (A0, A1, A2, A3, A4 …)")
        self.assertTrue(self.sample(scan_p95_s=0).split("\n")[2].startswith("지연 측정 전 · cache 60%"))

    def test_closed_experiment_numbers_never_reach_the_brief_text(self):
        body = self.fn_source("_brief_report")
        for text in (self.sample(), self.sample(live_cnt=4, live_wins=3, open_markets=["KRW-BTC"], suspend_block=1)):
            for word in ("COHORT", "A2", "net/건", "통과못함", "duplicate", "기회비용", "CURRENT_LIVE_ROUTE", "SCORE", "PASS:"):
                self.assertNotIn(word, text, word)
        names = {n.id for n in ast.walk(ast.parse(body)) if isinstance(n, ast.Name)}
        params = {"live_cnt", "live_wins", "live_pnl", "limit_str", "guard_warn", "open_markets", "raw", "gate_pass", "sent", "entry_disabled",
                  "scan_p95_s", "slow_limit_s", "cache_rate", "suspend_block", "deploy_hash", "deploy_ts"}
        self.assertLessEqual(names - params, {"wr", "state", "flow", "names", "held", "lag", "m", "len"})   # 전역을 읽지 않는다

    def test_the_log_keeps_the_full_report_and_telegram_gets_the_brief_one(self):
        body = self.fn_source("_pipeline_report")
        i = body.index('msg = "\\n".join(lines)')
        tail = body[i:i + 1100]
        self.assertLess(tail.index("print(msg)"), tail.index('if globals().get("TG_REPORT_BRIEF", True):'))   # 상세는 항상 로그에
        self.assertIn("_tg_msg = _brief_report(", tail)
        self.assertIn("tg_send(_tg_msg)", tail)
        self.assertNotIn("tg_send(msg)", body)                                                              # 긴 요약을 직접 보내는 길은 없다
        self.assertIn("except Exception as _brief_err:", tail)                                              # 요약 실패 시 예전 것으로
        self.assertIn("_tg_msg = msg", tail[tail.index("except Exception as _brief_err:"):])

    def test_the_call_passes_values_that_exist_in_the_report_function(self):
        body = self.fn_source("_pipeline_report")
        call = body[body.index("_tg_msg = _brief_report("):]
        call = call[:call.index("except Exception")]
        for name in ("_live_cnt", "_live_wins", "_live_pnl", "_limit_str", "_guard_warn", "_open_pos", "_raw", "_gp", "_succ",
                     "_scan_p95_s", "_cache_rate"):
            self.assertIn(name, call, name)
            self.assertRegex(body[:body.index("_tg_msg = _brief_report(")], r"(?m)^\s+%s\s*=" % name, name)     # 그 전에 값이 정해져 있다
        self.assertEqual(len(ast.parse("f(" + call.split("_brief_report(", 1)[1].rsplit(")", 1)[0] + ")").body[0].value.args), 16)


if __name__ == "__main__":
    unittest.main(verbosity=2)
