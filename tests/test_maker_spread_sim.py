# -*- coding: utf-8 -*-
"""maker_spread_sim(H22) 테스트 — 보수적 큐 체결 모형·재호가·시간초과·공백·선택/판정 규칙을 합성 이벤트로 검증."""
import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mss", ROOT / "scalp" / "research" / "maker_spread_sim.py")
MS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MS)
CODE = "KRW-DOGE"


def book(bid=250.0, ask=251.0, bid_size=1000.0, ask_size=1000.0, levels=5):
    bids = {bid - i: bid_size for i in range(levels)}
    asks = {ask + i: ask_size for i in range(levels)}
    return bids, asks, bid, ask


def feed_book(m, t, **kw):
    bids, asks, bb, ba = book(**kw)
    m.on_book(t, CODE, bids, asks, bb, ba)


def new(L=1000, rq="track", T=600):
    m = MS.Maker(L, rq, T)
    m.day = "2026-10-22"
    return m


class TestEngine(unittest.TestCase):
    def test_round_trip_maker_both_sides(self):
        m = new()
        feed_book(m, 0)                                   # IDLE → bid 250 pending(active 1000), qty 400
        s = m.st[CODE]
        self.assertEqual((s.phase, s.price), ("BID_PENDING", 250.0))
        self.assertAlmostEqual(s.qty, 400.0)
        feed_book(m, 1000)                                # 활성: 앞 잔량 1000
        self.assertEqual((s.phase, s.queue), ("BID_WORKING", 1000.0))
        m.on_trade(1500, CODE, 250.0, 1200.0, "ASK")       # 앞 잔량 소진 후 남는 200 < 400 → 미체결
        self.assertEqual(s.phase, "BID_WORKING")
        m.on_trade(1500, CODE, 250.0, 1200.0, "BID")       # 매수 체결은 내 매수호가를 깎지 않음
        self.assertEqual(s.phase, "BID_WORKING")
        m.on_trade(1600, CODE, 250.0, 300.0, "ASK")        # 누적 500 ≥ 400 → 매수 체결, 매도 251 대기
        self.assertEqual((s.phase, s.price, s.buy), ("ASK_PENDING", 251.0, 250.0))
        m.on_trade(2000, CODE, 251.0, 5000.0, "BID")       # 활성 전 체결은 무시
        self.assertEqual(s.phase, "ASK_PENDING")
        feed_book(m, 2600)
        self.assertEqual((s.phase, s.queue), ("ASK_WORKING", 1000.0))
        m.on_trade(3000, CODE, 251.0, 1500.0, "BID")       # 매도 체결
        self.assertEqual(len(m.trips), 1)
        tr = m.trips[0]
        self.assertEqual((tr["buy"], tr["sell"], tr["exit"]), (250.0, 251.0, "maker"))
        self.assertAlmostEqual(tr["net"], MS.net_pct(250.0, 251.0))
        self.assertGreater(tr["net"], 0.29)               # 1틱 0.4% − 수수료 0.1%
        feed_book(m, 4000)                                # 바로 다음 호가에서 재진입
        self.assertEqual(s.phase, "BID_PENDING")
        m.end_day(5000)
        self.assertEqual(len(m.trips), 1)

    def test_timeout_exits_taker_at_best_bid(self):
        m = new(T=120)
        feed_book(m, 0)
        feed_book(m, 1000)
        m.on_trade(1600, CODE, 250.0, 2000.0, "ASK")       # buy 250 at 1600
        feed_book(m, 2600)
        feed_book(m, 121599)                              # 아직 T 미만
        self.assertEqual(m.st[CODE].phase, "ASK_WORKING")
        feed_book(m, 121600)                              # 120s → 최우선 매수호가 250 에 시장가 청산
        self.assertEqual(m.trips[0]["exit"], "timeout")
        self.assertAlmostEqual(m.trips[0]["net"], MS.net_pct(250.0, 250.0))
        self.assertLess(m.trips[0]["net"], -0.09)         # 수수료 왕복 ≈ −0.1%

    def test_trade_through_fills_fully(self):
        m = new()
        feed_book(m, 0)
        feed_book(m, 1000)
        m.on_trade(1100, CODE, 249.0, 1.0, "ASK")          # 내 가격 아래 체결 = 전량 체결
        self.assertEqual((m.st[CODE].phase, m.st[CODE].buy), ("ASK_PENDING", 250.0))

    def test_requote_track_vs_up_only(self):
        tr, up = new(rq="track"), new(rq="up_only")
        for m in (tr, up):
            feed_book(m, 0)
            feed_book(m, 1000)
            feed_book(m, 1200, bid=249.0, ask=251.0)      # 시장이 내 가격 아래로
        self.assertEqual((tr.st[CODE].phase, tr.st[CODE].price), ("BID_PENDING", 249.0))
        self.assertEqual((up.st[CODE].phase, up.st[CODE].price), ("BID_WORKING", 250.0))
        for m in (tr, up):
            m.on_trade(1300, CODE, 250.0, 2000.0, "ASK")   # 250 에서 매도 체결 발생
        self.assertEqual(up.st[CODE].buy, 250.0)          # up_only: 위에 남아 있던 호가가 맞음(역선택 감수)
        self.assertIsNone(tr.st[CODE].buy)                # track: 249 로 재호가 중(대기) → 무시
        feed_book(tr, 1500, bid=251.0, ask=252.0)         # 시장이 위로. 대기 중(활성 2200 전)인 주문은 그대로
        self.assertEqual((tr.st[CODE].phase, tr.st[CODE].price), ("BID_PENDING", 249.0))
        feed_book(tr, 2200, bid=251.0, ask=252.0)         # 활성 → 최우선가 251 > 249 → 즉시 재호가(또 L 지연)
        self.assertEqual((tr.st[CODE].phase, tr.st[CODE].price, tr.st[CODE].active_at), ("BID_PENDING", 251.0, 3200))

    def test_crossed_activation_cancels_and_redecides(self):
        m = new()
        feed_book(m, 0)                                   # bid 250 pending
        feed_book(m, 1000, bid=249.0, ask=250.0)          # 활성 시점에 매수 250 ≥ 매도 250 → 취소, 249 로
        self.assertEqual((m.st[CODE].phase, m.st[CODE].price), ("BID_PENDING", 249.0))

    def test_ask_follows_down_only_to_floor_and_cross_closes(self):
        m = new()
        feed_book(m, 0)
        feed_book(m, 1000)
        m.on_trade(1600, CODE, 250.0, 2000.0, "ASK")       # buy 250 → ask 251 pending
        feed_book(m, 2600, bid=250.0, ask=253.0, ask_size=50.0)  # 최우선 매도 253 > 내 251: 그대로(내가 최우선)
        self.assertEqual((m.st[CODE].phase, m.st[CODE].price), ("ASK_WORKING", 251.0))
        feed_book(m, 2700, bid=249.0, ask=250.0)          # 매도가 하한(251) 아래로 내려옴 → 251 유지
        self.assertEqual(m.st[CODE].price, 251.0)
        feed_book(m, 2800, bid=252.0, ask=253.0)          # 시장이 내 매도가 위로 → 활성 상태라 큐로 체결 기다림
        m.on_trade(2900, CODE, 252.0, 1.0, "BID")          # 내 가격 위 체결 = 전량 체결
        self.assertEqual((m.trips[0]["exit"], m.trips[0]["sell"]), ("maker", 251.0))

    def test_gap_cancels_bids_and_force_exits_longs(self):
        m = new()
        feed_book(m, 0)
        feed_book(m, 1000)
        m.on_trade(1600, CODE, 250.0, 2000.0, "ASK")
        m.on_gap(9000)
        self.assertTrue(m.st[CODE].force_exit)
        feed_book(m, 9100, bid=240.0, ask=241.0)          # 재개 직후 최우선 매수호가에 청산
        self.assertEqual((m.trips[0]["exit"], m.trips[0]["sell"]), ("gap", 240.0))
        m2 = new()
        feed_book(m2, 0)
        m2.on_gap(8000)
        self.assertEqual(m2.st[CODE].phase, "IDLE")

    def test_eod_closes_open_long(self):
        m = new()
        feed_book(m, 0)
        feed_book(m, 1000)
        m.on_trade(1600, CODE, 250.0, 2000.0, "ASK")
        m.end_day(5000)
        self.assertEqual(m.trips[0]["exit"], "eod")
        self.assertEqual(m.st[CODE].phase, "IDLE")


class TestRules(unittest.TestCase):
    def test_choose_and_verdict(self):
        def fake(n, net):
            m = MS.Maker(1000, "track", 120)
            m.trips = [{"net": net, "exit": "maker", "code": CODE, "day": "d"} for _ in range(n)]
            return m
        sims = {c: fake(5, 1.0) for c in MS.COMBOS}
        self.assertIsNone(MS.choose(sims))                # 전부 n<20 → 후보 없음
        sims[(3000, "up_only", 600)] = fake(25, 0.1)
        sims[(1000, "track", 600)] = fake(40, 0.05)       # 총 net 2.0 < 2.5
        self.assertEqual(MS.choose(sims), (3000, "up_only", 600))
        self.assertEqual(MS.verdict(MS.summarize([{"net": 0.3, "exit": "maker"}] * 10, 1.0)), "INSUFFICIENT")
        good = [{"net": 0.3 + 0.01 * (i % 5), "exit": "maker"} for i in range(40)]
        self.assertEqual(MS.verdict(MS.summarize(good, 1.0)), "PASS")
        mixed = [{"net": 0.3 if i % 2 else -0.4, "exit": "maker" if i % 2 else "timeout"} for i in range(40)]
        self.assertEqual(MS.verdict(MS.summarize(mixed, 1.0)), "FAIL")
        self.assertAlmostEqual(MS.net_pct(100.0, 101.0), 100 * (101 * 0.9995 / (100 * 1.0005) - 1))
        self.assertEqual(MS.infer_tick({250.0: 1, 251.0: 1, 253.0: 1}, None), 1.0)
        self.assertEqual(MS.infer_tick({250.5: 1, 251.0: 1}, 1.0), 0.5)


def write_day(day_dir, cycles=25, t0=1761091200000):
    """사이클마다: 호가 → 1초 뒤 호가(활성) → ASK 체결로 매수 → 호가(매도 활성) → BID 체결로 매도. L=1000 조합만 사이클당 1왕복."""
    day_dir.mkdir(parents=True)
    bids = lambda: [{"ask_price": 251.0 + i, "bid_price": 250.0 - i, "ask_size": 1000.0, "bid_size": 1000.0} for i in range(5)]
    with gzip.open(day_dir / "00.jsonl.gz", "wt", encoding="utf-8") as fh:
        def w(o):
            fh.write(json.dumps(o) + "\n")
        w({"_meta": "connect", "recv_ts": t0 - 10})
        for i in range(cycles):
            t = t0 + i * 10000
            w({"type": "orderbook", "code": CODE, "recv_ts": t, "timestamp": t - 80, "orderbook_units": bids()})
            w({"type": "orderbook", "code": "KRW-XRP", "recv_ts": t + 500, "timestamp": t + 420,
               "orderbook_units": [{"ask_price": 3001.0, "bid_price": 3000.0, "ask_size": 1.0, "bid_size": 1.0}]})
            w({"type": "orderbook", "code": CODE, "recv_ts": t + 1000, "timestamp": t + 920, "orderbook_units": bids()})
            w({"type": "trade", "code": CODE, "recv_ts": t + 1500, "timestamp": t + 1400, "trade_price": 250.0,
               "trade_volume": 2000.0, "ask_bid": "ASK", "sequential_id": i})
            w({"type": "orderbook", "code": CODE, "recv_ts": t + 3000, "timestamp": t + 2920, "orderbook_units": bids()})
            w({"type": "trade", "code": CODE, "recv_ts": t + 3500, "timestamp": t + 3400, "trade_price": 251.0,
               "trade_volume": 2000.0, "ask_bid": "BID", "sequential_id": 100 + i})
            w({"type": "orderbook", "code": CODE, "recv_ts": t + 4000, "timestamp": t + 3920, "orderbook_units": bids()})


class TestEndToEnd(unittest.TestCase):
    def test_iter_events_and_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "2026-10-22")
            write_day(d, cycles=2)
            ev = list(MS.iter_events(d, MS.MARKETS))
            kinds = [e[0] for e in ev]
            self.assertEqual(kinds.count("trade"), 4)
            self.assertEqual(kinds.count("book"), 8)       # XRP 호가는 종목 외 → 제외
            self.assertEqual(kinds.count("gap"), 1)        # 사이클 사이 6초 공백(4000→10000)
            b = [e for e in ev if e[0] == "book"][0]
            self.assertEqual((b[5], b[6]), (250.0, 251.0))

    def test_main_train_then_holdout_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "ws_tight")
            write_day(root / "2026-10-22", cycles=25)
            self.assertEqual(MS.main([str(root)]), 0)
            txt = Path(tmp, "ws_tight_maker_train.txt").read_text(encoding="utf-8")
            self.assertIn("SELECTION: L=1000ms requote=track T=120s", txt)   # 동률이면 첫 조합
            self.assertIn("KRW-DOGE", txt)
            self.assertEqual(MS.main([str(root), "--holdout"]), 0)
            hold = Path(tmp, "ws_tight_maker_holdout.txt").read_text(encoding="utf-8")
            self.assertIn("-> INSUFFICIENT", hold)         # HOLDOUT 폴더 없음 → n=0
            sims = MS.run_days(root, ["2026-10-22"])
            n_fast = len(sims[(1000, "track", 120)].trips)
            n_slow = len(sims[(3000, "track", 120)].trips)
            self.assertEqual(n_fast, 25)
            self.assertLess(n_slow, 20)                    # 3초 지연은 같은 데이터에서 체결 기회를 절반 놓침
            self.assertTrue(all(abs(x["net"] - MS.net_pct(250.0, 251.0)) < 1e-9 for x in sims[(1000, "track", 120)].trips))


if __name__ == "__main__":
    unittest.main(verbosity=2)
