# -*- coding: utf-8 -*-
"""전진 조건 기록 (고문 판정 2026-11-22 ⑤) — scalp/research/crash_log.py 의 정의와 저장 방식."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("crash_log_t", str(ROOT / "scalp" / "research" / "crash_log.py"))
CL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CL)

Q0 = 2000000                                                 # 15분봉 번호의 시작 (아무 값)
A = Q0 + 200                                                 # BTC 신호가 나는 봉


class Bars(object):
    def __init__(self, rows):
        self.ts = sorted(rows)
        self.o, self.c, self.v = [rows[q][0] for q in self.ts], [rows[q][1] for q in self.ts], [rows[q][2] for q in self.ts]


def series(n=400, price=100.0, drops=None, skip=()):
    """평평한 15분봉 n개. drops = {봉 번호: 한 봉 수익}. 급락 봉의 거래대금은 평소의 5배. skip 의 봉은 없다(거래 없음)."""
    rows, p = {}, price
    for k in range(n):
        q = Q0 + k
        o = p
        if drops and q in drops:
            p = o * (1 + drops[q])
        if q not in skip:
            rows[q] = (o, p, 5e9 if drops and q in drops else 1e9)
    return Bars(rows)


def five_flat(panel, minutes, overrides=None):
    """각 종목의 5분봉을 15분봉 종가로 평평하게 채운다. overrides = {(종목, 분): [시, 고, 저, 종]} 로 덮어쓴다. None 이면 그 봉은 없다."""
    bars = {}
    for m, b in panel.items():
        d = {}
        for x in minutes:
            p = CL.close_at(b, x // 15 - 1) if x % 15 == 0 else CL.close_at(b, x // 15)
            d[x] = [p, p, p, p]
        bars[m] = d
    for (m, x), v in (overrides or {}).items():
        if v is None:
            bars[m].pop(x, None)
        else:
            bars[m][x] = v
    return CL.FiveMin(bars)


def world():
    """BTC 가 A 봉에서 −2%(신호). ETH 는 같은 봉에서 −7%(동반·조합), XRP 는 2봉 뒤 −5%(후행), IQ 는 A+100 봉에서 −5%(알트만)."""
    panel = {"KRW-BTC": series(drops={A: -0.02}), "KRW-ETH": series(drops={A: -0.07}), "KRW-XRP": series(drops={A + 2: -0.05}),
             "KRW-IQ": series(drops={A + 100: -0.05})}
    minutes = range((Q0 + 100) * 15, (Q0 + 400) * 15, 5)
    return panel, minutes


class Signals(unittest.TestCase):
    def test_an_alt_crash_is_a_three_percent_close_to_close_drop_with_a_sixty_minute_cooldown(self):
        b = series(drops={Q0 + 150: -0.03, Q0 + 153: -0.05, Q0 + 157: -0.05, Q0 + 300: -0.0299, Q0 + 50: -0.10})
        self.assertEqual([b.ts[i] for i in CL.alt_signals(b)], [Q0 + 150, Q0 + 157])                   # 153 은 60분 안, 300 은 −3% 에 못 미침, 50 은 앞 자료 96봉이 없다
        b = series(drops={Q0 + 150: -0.05, Q0 + 154: -0.05})
        self.assertEqual([b.ts[i] for i in CL.alt_signals(b)], [Q0 + 150, Q0 + 154])                   # 딱 4봉 뒤는 다시 센다
        gap = series(drops={Q0 + 150: -0.05}, skip=(Q0 + 149,))
        self.assertEqual(CL.alt_signals(gap), [])                                                      # 직전 봉이 바로 앞 15분이 아니면 판단하지 않는다

    def test_a_btc_signal_is_the_first_bar_whose_one_hour_return_crosses_the_line(self):
        b = series(drops={A: -0.02})
        self.assertEqual(CL.btc_signals(b), [A])                                                       # 그 뒤 3봉도 −2% 지만 처음 내려간 봉만
        self.assertEqual(CL.btc_signals(series(drops={A: -0.018})), [])                                # −1.8% 는 −1.821% 에 못 미친다
        two = series(drops={A: -0.02, A + 1: 0.005, A + 2: -0.006})                                    # 선 위로 올라왔다가 다시 내려가도 60분 안이면 같은 사건
        self.assertEqual(CL.btc_signals(two), [A])
        falling = series(drops={A + k: (-0.02 if k == 0 else -0.01) for k in range(8)})                # 선 아래에 머무는 동안은 60분이 지나도 새 신호가 아니다
        self.assertEqual(CL.btc_signals(falling), [A])
        later = series(drops={A: -0.02, A + 10: -0.02})
        self.assertEqual(CL.btc_signals(later), [A, A + 10])


class Rows(unittest.TestCase):
    def setUp(self):
        self.panel, self.minutes = world()
        self.rows = CL.rows(self.panel, five_flat(self.panel, self.minutes), Q0, Q0 + 400)
        self.by = {r["사건 ID"].split("_")[0]: r for r in self.rows}

    def test_every_event_has_the_research_columns_and_the_condition_values(self):
        self.assertEqual(sorted(self.by), ["BTC", "ETH", "IQ", "XRP"])
        for r in self.rows:
            self.assertEqual(set(r), set(CL.COLUMNS))
        eth, xrp, iq, btc = self.by["ETH"], self.by["XRP"], self.by["IQ"], self.by["BTC"]
        self.assertEqual((eth["구분"], eth["BTC 신호 뒤 경과 분"]), ("동반", 0))                             # 같은 봉에서 BTC 1시간 −2%
        self.assertEqual((xrp["구분"], xrp["BTC 신호 뒤 경과 분"]), ("후행", 30))                            # BTC 신호 2봉 뒤
        self.assertEqual((iq["구분"], iq["BTC 신호 뒤 경과 분"]), ("알트만", None))
        self.assertEqual((btc["구분"], btc["종목"]), ("BTC 직접", "BTC"))
        self.assertAlmostEqual(eth["15분 수익"], -0.07)
        self.assertAlmostEqual(eth["거래대금 배수"], 5.0)                                                 # 직전 96봉 중앙값의 5배
        self.assertAlmostEqual(eth["같은 봉 BTC 15분 수익"], -0.02)
        self.assertAlmostEqual(eth["알트−BTC 15분 차"], -0.05)
        self.assertAlmostEqual(eth["알트 4시간"], -0.07)
        self.assertAlmostEqual(eth["BTC 24시간"], -0.02)
        self.assertEqual((eth["4시간 조건"], eth["BTC24 조건"], eth["조합"], eth["큰 5종목"]), (1, 1, 1, 1))  # −7% ≤ −5.92%, −2% ≤ −1.73%, ETH 는 큰 5종목
        self.assertEqual((xrp["4시간 조건"], xrp["BTC24 조건"], xrp["조합"]), (0, 1, 0))                    # 4시간 −5% 는 못 미친다
        self.assertEqual((iq["4시간 조건"], iq["BTC24 조건"], iq["큰 5종목"]), (0, 0, 0))                    # A+100 봉의 96봉 전은 BTC 급락 뒤라 BTC 24시간 수익이 0 이다
        self.assertAlmostEqual(iq["BTC 24시간"], 0.0)
        self.assertEqual((btc["4시간 조건"], btc["조합"]), (0, 0))                                         # BTC 직접 사건에는 조합을 매기지 않는다
        self.assertEqual(eth["신호 시각(UTC)"][-5:], "%02d:%02d" % (((A + 1) * 15 // 60) % 24, ((A + 1) * 15) % 60))
        self.assertEqual(eth["사건 ID"], CL.event_id("KRW-ETH", (A + 1) * 15))

    def test_same_time_btc_and_eth_are_recorded_and_an_event_on_eth_uses_its_own_path(self):
        eth, xrp = self.by["ETH"], self.by["XRP"]
        self.assertEqual(eth["ETH 60분"], eth["① 60분"])
        self.assertAlmostEqual(xrp["BTC 60분"], CL.net("KRW-BTC", 0.0))                                 # 평평하면 비용만 빠진다 (BTC 0.07%)
        self.assertAlmostEqual(xrp["① 60분"], CL.net("KRW-XRP", 0.0))                                   # 그 밖은 0.10%
        self.assertAlmostEqual(CL.net("KRW-XRP", 0.0), (1 - 0.001) / (1 + 0.001) - 1)
        self.assertAlmostEqual(CL.net("KRW-ETH", 0.01), 1.01 * (1 - 0.0007) / (1 + 0.0007) - 1)

    def test_events_need_the_full_hour_and_the_five_minute_bars_and_are_not_written_twice(self):
        five = five_flat(self.panel, self.minutes)
        self.assertEqual([r["종목"] for r in CL.rows(self.panel, five, Q0, A + 5)], ["BTC", "ETH"])       # XRP(A+2)는 60분이 아직 안 지났다
        self.assertEqual([r["종목"] for r in CL.rows(self.panel, five, Q0, A + 7)], ["BTC", "ETH", "XRP"])
        self.assertEqual([r["종목"] for r in CL.rows(self.panel, five, A + 1, Q0 + 400)], ["XRP", "IQ"])  # 시작 봉 앞의 사건은 적지 않는다
        known = {self.by["ETH"]["사건 ID"]}
        self.assertNotIn("ETH", [r["종목"] for r in CL.rows(self.panel, five, Q0, Q0 + 400, known)])
        t = (A + 101) * 15
        holes = five_flat(self.panel, self.minutes, {("KRW-IQ", t + 5 * j): None for j in range(-7, 12)})   # 그 종목의 5분봉이 통째로 없으면 적지 않는다
        self.assertNotIn("IQ", [r["종목"] for r in CL.rows(self.panel, holes, Q0, Q0 + 400)])


class Path60(unittest.TestCase):
    T = 3000000                                                # 신호 시각(분)

    def five(self, closes, opens=None, pre=100.0, missing=()):
        d = {self.T - 5: [pre, pre, pre, pre]}
        prev = pre
        for j, c in enumerate(closes):
            o = opens[j] if opens else prev
            if j not in missing:
                d[self.T + 5 * j] = [o, max(o, c), min(o, c), c]
            prev = c
        return CL.FiveMin({"KRW-XRP": d})

    def test_fixed_points_extremes_their_times_and_the_pullback(self):
        closes = [99, 97, 98, 101, 104, 103, 102, 101, 100, 100, 101, 102]
        got = CL.path("KRW-XRP", self.T, self.five(closes))
        self.assertEqual(got["① 진입가"], 100.0)
        for key, c in (("① 5분", 99), ("① 15분", 98), ("① 30분", 103), ("① 60분", 102)):
            self.assertAlmostEqual(got[key], CL.net("KRW-XRP", c / 100.0 - 1), msg=key)
        self.assertAlmostEqual(got["최대 반등"], 0.04)
        self.assertEqual((got["고점까지 분"], got["저점까지 분"], got["순서"]), (25, 10, "저점 먼저"))
        self.assertAlmostEqual(got["최대 추가 하락"], -0.03)
        self.assertAlmostEqual(got["고점 뒤 되밀림"], 100.0 / 104.0 - 1)                                 # 고점 뒤 가장 낮았던 값
        one = CL.path("KRW-XRP", self.T, self.five([105, 95] + [96] * 10))
        self.assertEqual((one["순서"], one["고점까지 분"], one["저점까지 분"]), ("고점 먼저", 5, 10))
        last = CL.path("KRW-XRP", self.T, self.five([99] * 11 + [108]))
        self.assertIsNone(last["고점 뒤 되밀림"])                                                        # 마지막 봉이 고점이면 되밀림을 잴 봉이 없다
        same = CL.path("KRW-XRP", self.T, self.five([100] * 12))
        self.assertEqual(same["순서"], "미상")                                                           # 고점과 저점이 같은 봉이면 순서를 알 수 없다
        self.assertIsNone(CL.path("KRW-XRP", self.T, self.five([100] * 12, missing=range(12))))          # 5분봉이 없으면 값을 내지 않는다

    def test_confirmation_entry_waits_for_a_higher_close_up_to_forty_five_minutes(self):
        got = CL.path("KRW-XRP", self.T, self.five([99, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108]))
        self.assertEqual((got["② 진입"], got["② 기다린 분"], got["② 진입가"]), (1, 15, 99))               # 셋째 봉이 처음으로 앞 봉보다 높게 마감 → 넷째 봉 시가
        self.assertAlmostEqual(got["② 60분(진입 건)"], CL.net("KRW-XRP", 108 / 99.0 - 1))                # 청산은 ① 과 같은 시각
        self.assertAlmostEqual(got["② 신호당(미진입 0)"], got["② 60분(진입 건)"])
        self.assertAlmostEqual(got["② 진입가 ÷ ① 진입가"], -0.01)
        down = [100 - k for k in range(1, 10)] + [95, 96, 97]                                             # 9봉(45분) 내내 내려가다 그 뒤에 오른다
        none = CL.path("KRW-XRP", self.T, self.five(down))
        self.assertEqual((none["② 진입"], none["② 기다린 분"], none["② 60분(진입 건)"], none["② 신호당(미진입 0)"]), (0, None, None, 0.0))
        late = CL.path("KRW-XRP", self.T, self.five([100 - k for k in range(1, 9)] + [93, 94, 95, 96]))    # 아홉째 봉(45분째)에 확인
        self.assertEqual((late["② 진입"], late["② 기다린 분"]), (1, 45))
        self.assertEqual(CL.WAIT_BARS, 9)
        flat = CL.path("KRW-XRP", self.T, self.five([100] * 12))                                          # 종가가 같으면 반등 확인이 아니다
        self.assertEqual((flat["② 진입"], flat["② 신호당(미진입 0)"]), (0, 0.0))

    def test_entry_is_the_first_trade_of_the_next_fifteen_minutes_and_missing_bars_carry_the_last_close(self):
        got = CL.path("KRW-XRP", self.T, self.five([100, 98, 99] + [99] * 9, missing=(0, 5)))             # 첫 5분에 거래가 없었다
        self.assertEqual(got["① 진입가"], 100.0)                                                         # 둘째 봉의 시가(= 직전 종가)
        self.assertAlmostEqual(got["① 5분"], CL.net("KRW-XRP", 0.0))                                     # 빈 봉은 직전 종가 그대로
        self.assertIsNone(CL.path("KRW-XRP", self.T, self.five([100] * 12, missing=(0, 1, 2))))           # 다음 15분 내내 거래가 없으면 사건이 아니다


class Storage(unittest.TestCase):
    def test_rows_are_only_ever_appended_and_an_event_is_written_once(self):
        panel, minutes = world()
        rows = CL.rows(panel, five_flat(panel, minutes), Q0, Q0 + 400)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "conditions.csv")
            self.assertEqual(CL.load(path), [])
            self.assertEqual(CL.append(path, rows[:2]), 2)
            first = open(path, encoding="utf-8").read()
            self.assertEqual(first.splitlines()[0].split(","), CL.COLUMNS)
            changed = [dict(rows[0], **{"① 60분": 9.99})] + rows[2:]                                    # 이미 적은 사건은 값이 달라도 다시 적지 않는다
            self.assertEqual(CL.append(path, changed + rows[2:]), len(rows) - 2)                         # 같은 묶음 안의 중복도 한 번만
            second = open(path, encoding="utf-8").read()
            self.assertTrue(second.startswith(first))                                                   # 앞에 적은 줄은 그대로
            self.assertEqual(second.count("\n"), 1 + len(rows))
            self.assertEqual(CL.append(path, rows), 0)
            self.assertEqual(open(path, encoding="utf-8").read(), second)
            got = CL.load(path)
            self.assertEqual([r["사건 ID"] for r in got], [r["사건 ID"] for r in rows])
            self.assertEqual(got[0]["① 60분"], CL.text(rows[0]["① 60분"]))
            self.assertEqual(CL.text(None), "")


class Table(unittest.TestCase):
    def row(self, k, kind="알트만", coin="IQ", net60=0.01, **kw):
        day = "2026-12-%02d" % (1 + k % 28)
        base = {c: "" for c in CL.COLUMNS}
        base.update({"사건 ID": "%s_%d" % (coin, k), "종목": coin, "날": day, "신호 시각(UTC)": "%s %02d:00" % (day, k % 24), "구분": kind,
                     "① 5분": "0.001", "① 15분": "0.002", "① 30분": "0.003", "① 60분": CL.text(net60), "최대 반등": "0.03", "최대 추가 하락": "-0.02",
                     "BTC 1시간": "0.0", "BTC 24시간": "-0.01", "거래대금 배수": "4.0", "알트−BTC 15분 차": "-0.035", "4시간 조건": "0", "BTC24 조건": "0",
                     "② 진입": "1", "② 신호당(미진입 0)": "0.004"})
        base.update({k_: CL.text(v) if not isinstance(v, str) else v for k_, v in kw.items()})
        return base

    def cell(self, rows, key):
        return {c["key"]: c for c in CL.table(rows)}[key]

    def test_cells_use_fixed_boundaries_and_small_samples_show_no_values(self):
        rows = [self.row(k) for k in range(12)]                                                        # 알트만, BTC 1시간 0%, 배수 4, 차 −3.5%
        table = CL.table(rows)
        self.assertEqual([c["key"] for c in table], list(CL.KEYS))
        c4 = self.cell(rows, "4")
        self.assertEqual((c4["n"], c4["days"], c4["ok"]), (12, 12, True))
        self.assertAlmostEqual(c4["means"][3], 0.01)
        self.assertEqual([round(v, 6) for v in c4["means"][:3]], [0.001, 0.002, 0.003])
        self.assertEqual((c4["up"], c4["down"]), (0.03, -0.02))
        self.assertEqual(self.cell(rows, "2")["n"], 0)                                                 # BTC 1시간이 −1.821% 위면 2번이 아니다
        self.assertEqual([self.cell(rows, k)["n"] for k in ("6-4-lo", "6-4-mid", "6-4-hi")], [0, 12, 0])  # −4.0% < −3.5% ≤ −3.1%
        self.assertEqual([self.cell(rows, k)["n"] for k in ("7-4-a", "7-4-b", "7-4-c", "7-4-d")], [0, 0, 12, 0])   # 3~6배
        self.assertEqual((self.cell(rows, "4x3")["n"], self.cell(rows, "8-4-dn")["n"], self.cell(rows, "8-4-up")["n"]), (12, 12, 0))
        self.assertEqual((self.cell(rows, "rest14")["n"], self.cell(rows, "big5")["n"], self.cell(rows, "neither")["n"]), (12, 0, 12))
        few = [self.row(k) for k in range(9)]                                                          # 9일뿐이면 값을 싣지 않는다
        c = self.cell(few, "4")
        self.assertEqual((c["n"], c["days"], c["ok"], c["means"], c["up"]), (9, 9, False, [None] * 4, None))
        self.assertEqual(CL.MIN_DAYS, 10)
        self.assertEqual((CL.REL_CUTS, CL.MULT_CUTS), ({"2번": (-0.029, -0.018), "4번": (-0.040, -0.031)}, (1.5, 3.0, 6.0)))

    def test_days_weigh_the_same_and_the_other_scenarios_are_separated(self):
        rows = [self.row(k, net60=0.01) for k in range(12)] + [self.row(0, coin="GMT", net60=0.13), self.row(100, coin="KAVA", net60=0.05)]
        rows[-1]["날"], rows[-1]["신호 시각(UTC)"] = rows[0]["날"], rows[0]["날"] + " 05:00"              # 같은 날의 다른 시각
        c = self.cell(rows, "4")
        self.assertEqual((c["n"], c["days"]), (14, 12))
        self.assertAlmostEqual(c["means"][3], (0.01 * 11 + ((0.01 + 0.13) / 2 + 0.05) / 2) / 12)        # 같은 시각끼리 먼저 평균 → 날 평균 → 날들의 평균
        mix = ([self.row(k, kind="동반", coin="ETH", **{"BTC 1시간": -0.02, "알트−BTC 15분 차": -0.01, "4시간 조건": "1", "BTC24 조건": "1", "BTC 24시간": 0.01}) for k in range(10)]
               + [self.row(k, kind="후행", coin="XRP", **{"BTC 1시간": -0.03, "② 진입": "0", "② 신호당(미진입 0)": "0.000000"}) for k in range(10, 20)]
               + [self.row(k, kind="BTC 직접", coin="BTC") for k in range(20, 30)])
        got = {c["key"]: c for c in CL.table(mix)}
        self.assertEqual([got[k]["n"] for k in ("1", "2", "3", "4", "co", "lag", "alone")], [10, 20, 10, 0, 10, 10, 0])
        self.assertEqual([got[k]["n"] for k in ("big5", "combo", "combo-big5", "8-2-up", "8-2-dn", "6-2-hi", "6-2-lo")], [20, 10, 10, 10, 10, 10, 10])
        self.assertEqual(got["5a"]["n"], 20)                                                           # BTC 직접은 알트 급락 표에 넣지 않는다
        self.assertIn("미진입 10건", got["5b"]["label"])
        self.assertAlmostEqual(got["5b"]["means"][3], 0.002)                                           # 미진입은 0 으로 넣은 신호당 값
        self.assertEqual(got["5b"]["means"][:3], [None, None, None])


if __name__ == "__main__":
    unittest.main()
