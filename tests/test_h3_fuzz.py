# -*- coding: utf-8 -*-
"""h3_executor 무작위 장애 검사(tests/fuzz_h3_executor.py)를 정해 둔 seed 로 조금만 돌린다.
더 많이 돌리려면: python3 -B -X utf8 tests/fuzz_h3_executor.py 0 3000  (그리고 --no-kills)"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("h3_fuzz", ROOT / "tests" / "fuzz_h3_executor.py")
FZ = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FZ)


class FixedSeeds(unittest.TestCase):
    def test_no_rule_is_broken_with_process_kills(self):
        shown = []
        kinds, worst = FZ.run(0, 60, kills=True, out=shown.append)
        self.assertEqual(kinds, {}, "\n".join(shown))
        self.assertLessEqual(worst[0], 20)                      # 하루 알림 수가 폭주하지 않는다

    def test_no_rule_is_broken_with_exchange_faults_only(self):
        shown = []
        kinds, _worst = FZ.run(0, 40, kills=False, out=shown.append)
        self.assertEqual(kinds, {}, "\n".join(shown))

    def test_the_check_covers_orders_that_stay_invisible_across_days(self):
        self.assertGreaterEqual(max(FZ.LAGS_MIN), 3 * 24 * 60)  # 날짜를 넘겨 미해결로 남는 주문이 들어 있다
        self.assertGreater(FZ.QUIET_DAYS * 24 * 60, max(FZ.LAGS_MIN))     # 조용한 기간이 가장 긴 지연보다 길다

    def test_the_same_seed_gives_the_same_run(self):
        first = FZ.one(7)
        again = FZ.one(7)
        self.assertEqual((first[0], first[1]), (again[0], again[1]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
