# -*- coding: utf-8 -*-
"""bot_version_check 테스트 — 배포 스크립트의 '실행 중인 봇 = 디스크의 bot.py' 판단."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bvc", ROOT / "trend" / "bot_version_check.py")
BV = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BV)


class TestBotVersionCheck(unittest.TestCase):
    def test_restart_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = Path(tmp, "bot.py")
            bot.write_text("print('v2')\n")
            md5 = hashlib.md5(bot.read_bytes()).hexdigest()
            marker = Path(tmp, "m.json")
            need = lambda pid: BV.needs_restart(str(marker), str(bot), pid)
            self.assertTrue(need(100)[0])                                   # 마커 없음 = 가드 이전 코드
            self.assertTrue(need(0)[0])                                     # 봇이 안 돌고 있음
            self.assertTrue(need("")[0])
            marker.write_text(json.dumps({"pid": 99, "bot_md5": md5, "order_block": True}))
            self.assertTrue(need(100)[0])                                   # 마커가 다른 프로세스 것
            marker.write_text(json.dumps({"pid": 100, "bot_md5": "old", "order_block": True}))
            restart, why = need(100)
            self.assertTrue(restart)                                        # git reset 뒤: 디스크는 새 코드, 프로세스는 옛 코드
            self.assertIn("different bot.py", why)
            marker.write_text(json.dumps({"pid": 100, "bot_md5": md5}))
            self.assertTrue(need(100)[0])                                   # 주문 차단이 없는 버전
            marker.write_text(json.dumps({"pid": 100, "bot_md5": md5, "order_block": True}))
            self.assertEqual(need(100)[0], False)
            self.assertEqual(need("100")[0], False)                         # systemctl 출력은 문자열
            self.assertEqual(BV.main(["--marker", str(marker), "--bot-file", str(bot), "--pid", "100"]), 0)
            self.assertEqual(BV.main(["--marker", str(marker), "--bot-file", str(bot), "--pid", "7"]), 1)

    def test_deploy_script_uses_the_running_process_check(self):
        sh = (ROOT / "deploy_restart.sh").read_text(encoding="utf-8")
        self.assertIn("trend/bot_version_check.py --marker", sh)
        self.assertIn("검증 OK", sh)
        self.assertLess(sh.index("git reset --hard origin/main"), sh.index("bot_version_check.py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
