# -*- coding: utf-8 -*-
"""conditional_salvage(H26) 테스트 — 심어 둔 '조건부 엣지'를 TRAIN 에서 찾고 HOLDOUT 에서 통과시키는지, 잡음에서는 통과시키지 않는지."""
import datetime as dt
import importlib.util
import random
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cs", ROOT / "scalp" / "research" / "conditional_salvage.py")
CS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CS)
D0 = dt.date(2023, 9, 1)
N_DAYS = 760


def day(k):
    return (D0 + dt.timedelta(days=k)).isoformat()


def synth(planted, seed=5, n_alts=60):
    """BTC 는 60일마다 상승/하락 국면 교대. planted 면: 전날 −3% 넘게 빠진 알트는 BTC 상승 국면에서 다음 날 +4%, 하락 국면에서 −3%."""
    rng = random.Random(seed)
    data = {"KRW-BTC": {}}
    regime_up, price = [], 100.0
    for k in range(N_DAYS):
        up = (k // 60) % 2 == 0
        regime_up.append(up)
        new = price * (1 + (0.006 if up else -0.006) + rng.gauss(0, 0.004))
        data["KRW-BTC"][day(k)] = (price, new, 1e12)
        price = new
    for i in range(n_alts):
        m, price, prev_r = "KRW-A%02d" % i, 100.0, 0.0
        data[m] = {}
        for k in range(N_DAYS):
            r = rng.gauss(0, 0.03)
            if planted and prev_r < -0.03:
                r += 0.04 if regime_up[k] else -0.03
            new = price * (1 + r)
            data[m][day(k)] = (price, new, 1e9 * (n_alts - i))
            price, prev_r = new, r
    return data


class Base(unittest.TestCase):
    def setUp(self):
        self.old = (CS.START, CS.TRAIN_END, CS.HOLD_START)
        CS.START, CS.TRAIN_END, CS.HOLD_START = day(80), day(480), day(481)
        self.addCleanup(lambda: setattr(CS, "START", self.old[0]) or setattr(CS, "TRAIN_END", self.old[1])
                        or setattr(CS, "HOLD_START", self.old[2]))


class TestPieces(Base):
    def test_period_stats_uses_days_not_trades(self):
        tr = [{"day": "d1", "net": 0.02, "ew": 0.0}, {"day": "d1", "net": 0.04, "ew": 0.0},
              {"day": "d2", "net": -0.01, "ew": 0.01}, {"day": "d3", "net": 0.01, "ew": 0.0}]
        st = CS.period_stats(tr)
        self.assertEqual((st["n"], st["trades"]), (3, 4))
        self.assertAlmostEqual(st["net"], (0.03 - 0.01 + 0.01) / 3)
        self.assertAlmostEqual(st["excess"], (0.03 - 0.02 + 0.01) / 3)
        self.assertEqual(CS.period_stats([])["n"], 0)

    def test_conditions_and_passes(self):
        trades = [{"ind": {"btc_trend": x / 100.0 - 0.5, "ret7": x, "weekend": float(x % 7 >= 5)}} for x in range(99)]
        conds = dict((c[0], c) for c in CS.make_conditions(trades, "D_REV1"))
        self.assertIn("btc_trend>=0", conds)
        self.assertIn("weekend", conds)
        self.assertEqual(conds["ret7 low"][3], 33)              # 3분위 문턱은 TRAIN 값에서
        self.assertEqual(conds["ret7 high"][3], 66)
        self.assertNotIn("weekend", dict((c[0], c) for c in CS.make_conditions(trades, "W_MOM28")))
        t = {"ind": {"btc_trend": 0.0, "ret7": 33, "vol_ratio": None}}
        self.assertTrue(CS.passes(t, conds["btc_trend>=0"]))
        self.assertFalse(CS.passes(t, conds["btc_trend<0"]))
        self.assertTrue(CS.passes(t, conds["ret7 low"]))
        self.assertFalse(CS.passes(t, ("x", "vol_ratio", ">=", 1.0)))   # 결측은 통과시키지 않는다
        st = {"n": 100, "net": 0.01, "sd": 0.02}
        self.assertAlmostEqual(CS.lower_bound(st, 0.05), 0.01 - 1.6448536 * 0.02 / 10, places=6)

    def test_indicators_use_only_past_data(self):
        data = synth(False)
        d = day(200)
        uni = CS.DF.universe(data, d)
        mkt = CS.market_state(data, uni, d)
        coin = CS.coin_state(data, uni[3], d, mkt, 3)
        tampered = {m: dict(s) for m, s in data.items()}
        for m in tampered:                                       # 결정일 이후 자료를 망가뜨려도 지표는 같아야 한다
            for k in range(200, N_DAYS):
                tampered[m][day(k)] = (1.0, 1.0, 1.0)
        self.assertEqual(mkt, CS.market_state(tampered, uni, d))
        self.assertEqual(coin, CS.coin_state(tampered, uni[3], d, mkt, 3))
        self.assertEqual(CS.pick_scenarios(data, uni, d), CS.pick_scenarios(tampered, uni, d))
        self.assertEqual(len(CS.pick_scenarios(data, uni, d)["D_REV1"]), 5)


class TestSalvage(Base):
    def test_planted_conditional_edge_is_found_and_passes(self):
        data = synth(True)
        trades = CS.build_trades(data, CS.START, day(N_DAYS - 2))
        rev_train = [t for t in trades if t["sc"] == "D_REV1" and t["day"] <= CS.TRAIN_END]
        base = CS.period_stats(rev_train)
        scans = dict((s["cond"][0], s) for s in CS.scan(rev_train, "D_REV1"))
        self.assertTrue(scans["btc_trend>=0"]["qualified"])      # BTC 추세 위에서만 반등이 남는다
        self.assertFalse(scans["btc_trend<0"]["qualified"])
        self.assertGreater(scans["btc_trend>=0"]["net"], base["net"] + 0.01)
        self.assertLess(scans["btc_trend<0"]["net"], 0)
        lines = []
        res = CS.run(trades, log=lines.append)
        rev = [v for v in res["verdicts"] if v["sc"] == "D_REV1"]
        self.assertTrue(rev and all(v["hold"]["pass"] for v in rev), "\n".join(lines))
        self.assertTrue(any("H26 VERDICT: PASS" in ln for ln in lines))

    def test_noise_does_not_pass(self):
        data = synth(False, seed=11)
        lines = []
        res = CS.run(CS.build_trades(data, CS.START, day(N_DAYS - 2)), log=lines.append)
        self.assertEqual(sum(1 for v in res["verdicts"] if v["hold"]["pass"]), 0, "\n".join(lines))
        self.assertFalse(any("H26 VERDICT: PASS" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
