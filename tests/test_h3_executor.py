# -*- coding: utf-8 -*-
"""h3_executor 테스트 — 가짜 거래소로 SHADOW/LIVE 흐름, 하루 1회·재시작 안전, 중복 주문 방지, 단타 봇 분리 확인, 잔고 확인."""
import base64
import datetime as dt
import hashlib
import hmac
import importlib.util
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("h3x", ROOT / "trend" / "h3_executor.py")
HX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HX)
TP = HX.TP
BTC, ETH = "KRW-BTC", "KRW-ETH"
PRICE = {BTC: 100000000.0, ETH: 3000000.0}


def candles_for(market, day, last_close_ratio):
    """day 직전 60일은 종가 100% 고정, 마지막 완료 일봉(day-1)만 ratio 배. ratio>1 이면 신호 ON."""
    base = PRICE[market]
    data = {TP._shift(day, -k): (base, base) for k in range(1, 61)}
    data[TP._shift(day, -1)] = (base, base * last_close_ratio)
    data[day] = (base * last_close_ratio, base * last_close_ratio)
    return data


class FakeExchange(object):
    def __init__(self, krw=10_000_000.0):
        self.bal = {"KRW": krw}
        self.orders = {}                 # identifier -> order dict
        self.placed = []                 # identifiers in order of placement
        self.mid = dict(PRICE)
        self.fail_after_accept = False   # place 가 주문은 넣고 예외(시간 초과 흉내)
        self.fail_before_accept = False  # place 가 주문을 못 넣고 예외
        self.no_fill = False             # 체결 0 으로 cancel
        self.level_size = 1000.0         # 호가 한 단의 수량
        self.tick_ratio = 0.0001         # 호가 간격 (가격 대비)
        self.fail_side = None            # 이 방향 주문은 넣기 전에 실패

    def orderbook(self, market):
        p = self.mid[market]
        tick = p * self.tick_ratio
        asks = [(p + tick * (k + 1), self.level_size) for k in range(10)]
        bids = [(p - tick * (k + 1), self.level_size) for k in range(10)]
        return asks, bids

    def balances(self):
        return dict(self.bal)

    def place(self, market, side, identifier, krw=None, volume=None):
        if self.fail_before_accept or self.fail_side == side:
            raise HX.H3Error("network down")
        if identifier in self.orders:
            raise HX.H3Error("identifier already exists")
        cur = market.split("-")[1]
        p = self.mid[market]
        trades = []
        if not self.no_fill:
            if side == "bid":
                price = p * 1.0001
                vol = krw / price
                trades = [{"price": str(price), "volume": str(vol), "funds": str(krw)}]
                fee = krw * HX.FEE
                self.bal["KRW"] -= krw + fee
                self.bal[cur] = self.bal.get(cur, 0.0) + vol
            else:
                price = p * 0.9999
                funds = volume * price
                trades = [{"price": str(price), "volume": str(volume), "funds": str(funds)}]
                fee = funds * HX.FEE
                self.bal["KRW"] += funds - fee
                self.bal[cur] = self.bal.get(cur, 0.0) - volume
        else:
            fee = 0.0
        self.orders[identifier] = {"uuid": "u-" + identifier, "state": "cancel" if side == "bid" else "done",
                                   "executed_funds": "0", "paid_fee": str(fee), "trades": trades}
        self.placed.append(identifier)
        if self.fail_after_accept:
            raise TimeoutError("read timed out")
        return self.orders[identifier]

    def order(self, identifier):
        return self.orders.get(identifier)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ratio = {BTC: 1.0, ETH: 1.0}     # 마지막 종가 배율 (신호 제어)
        self.clock = dt.datetime(2026, 11, 10, 0, 1, 0, tzinfo=dt.timezone.utc)
        self.sent = []

    def cfg(self, mode="SHADOW", capital="2000000", **extra):
        env = {"H3_MODE": mode, "H3_CAPITAL_KRW": capital, "H3_STATE_DIR": self.tmp.name,
               "H3_BOT_MARKER": str(Path(self.tmp.name, "marker.json")),
               "UPBIT_ACCESS_KEY": "ak", "UPBIT_SECRET_KEY": "sk"}
        env.update(extra)
        return HX.Config(env)

    def executor(self, cfg, ex, bot_pids=(), allow_late=False):
        return HX.Executor(cfg, ex, fetch_candles=lambda m, day: candles_for(m, day, self.ratio[m]),
                           notify=self.sent.append, now=lambda: self.clock, bot_pids=list(bot_pids), sleep=lambda s: None,
                           allow_late=allow_late)

    def next_day(self):
        self.clock += dt.timedelta(days=1)


class TestPieces(unittest.TestCase):
    def test_jwt_and_query_hash(self):
        token = HX.jwt_hs256({"access_key": "ak", "nonce": "n"}, "secret")
        head, body, sig = token.split(".")
        pad = lambda x: x + "=" * (-len(x) % 4)
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(head))), {"alg": "HS256", "typ": "JWT"})
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(body)))["access_key"], "ak")
        want = base64.urlsafe_b64encode(hmac.new(b"secret", (head + "." + body).encode(), hashlib.sha256).digest()).rstrip(b"=")
        self.assertEqual(sig.encode(), want)
        q = {"market": BTC, "side": "bid"}
        payload = json.loads(base64.urlsafe_b64decode(pad(HX.auth_header("ak", "s", q)["Authorization"].split()[1].split(".")[1])))
        self.assertEqual(payload["query_hash"], hashlib.sha512(urllib.parse.urlencode(q).encode()).hexdigest())
        self.assertEqual(payload["query_hash_alg"], "SHA512")
        self.assertNotIn("query_hash", json.loads(base64.urlsafe_b64decode(pad(
            HX.auth_header("ak", "s")["Authorization"].split()[1].split(".")[1]))))

    def test_walk_volume_format_and_fill_parsing(self):
        asks = [(100.0, 1.0), (101.0, 1.0), (102.0, 1.0)]
        qty, krw = HX.walk(asks, krw=150.0)
        self.assertAlmostEqual(krw, 150.0)
        self.assertAlmostEqual(qty, 1.0 + 50.0 / 101.0)
        self.assertIsNone(HX.walk(asks, krw=1000.0))
        self.assertEqual(HX.walk([(99.0, 0.5), (98.0, 5.0)], qty=1.0), (1.0, 99.0 * 0.5 + 98.0 * 0.5))
        self.assertEqual(HX.fmt_volume(0.123456789), "0.12345678")       # 내림 (올리면 잔고 초과)
        self.assertEqual(HX.fmt_volume(1.0), "1.00000000")
        fill = HX.parse_fill({"uuid": "u", "state": "cancel", "executed_funds": "0", "paid_fee": "50",
                              "trades": [{"price": "100", "volume": "2", "funds": "200"}, {"price": "110", "volume": "1"}]})
        self.assertEqual((fill["volume"], fill["funds"], fill["fee"]), (3.0, 310.0, 50.0))
        self.assertAlmostEqual(fill["avg"], 310.0 / 3)
        with self.assertRaises(HX.H3Error):
            HX.parse_fill({"trades": [{"price": "0", "volume": "1"}]})

    def test_config_and_env_file(self):
        with self.assertRaises(HX.H3Error):
            HX.Config({"H3_MODE": "REAL"})
        with self.assertRaises(HX.H3Error):
            HX.Config({"H3_MODE": "LIVE", "UPBIT_ACCESS_KEY": "a", "UPBIT_SECRET_KEY": "b"})       # 자금 없음
        with self.assertRaises(HX.H3Error):
            HX.Config({"H3_MODE": "LIVE", "H3_CAPITAL_KRW": "1000000"})                             # 키 없음
        self.assertEqual(HX.Config({}).mode, "SHADOW")
        self.assertEqual(HX.Config({}).capital, HX.DEFAULT_SHADOW_CAPITAL)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, ".env")
            p.write_text('# c\nUPBIT_ACCESS_KEY="from_file"\nexport TG_TOKEN=tok\nH3_MODE=LIVE\n', encoding="utf-8")
            env = {"H3_MODE": "SHADOW"}
            HX.load_env_file(str(p), env)
            self.assertEqual(env, {"H3_MODE": "SHADOW", "UPBIT_ACCESS_KEY": "from_file", "TG_TOKEN": "tok"})

    def test_bot_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = HX.Config({"H3_BOT_MARKER": str(Path(tmp, "m.json")), "H3_STATE_DIR": tmp})
            self.assertTrue(HX.bot_guard_ok(cfg, BTC, pids=[])[0])                 # 봇이 없으면 입양할 주체가 없다
            self.assertFalse(HX.bot_guard_ok(cfg, BTC, pids=[77])[0])              # 봇은 도는데 마커 없음
            Path(tmp, "m.json").write_text(json.dumps({"pid": 5, "markets": [BTC, ETH]}))
            self.assertFalse(HX.bot_guard_ok(cfg, BTC, pids=[77])[0])              # 마커가 다른(옛) 프로세스 것
            Path(tmp, "m.json").write_text(json.dumps({"pid": 77, "markets": [ETH]}))
            self.assertFalse(HX.bot_guard_ok(cfg, BTC, pids=[77])[0])              # 이 마켓이 예약 목록에 없음
            Path(tmp, "m.json").write_text(json.dumps({"pid": 77, "markets": [BTC, ETH]}))
            self.assertFalse(HX.bot_guard_ok(cfg, BTC, pids=[77])[0])              # 주문 차단이 없는 #585 시점의 봇
            Path(tmp, "m.json").write_text(json.dumps({"pid": 77, "markets": [BTC, ETH], "order_block": True}))
            self.assertTrue(HX.bot_guard_ok(cfg, BTC, pids=[77])[0])

    def test_scalper_bot_has_the_reserved_market_guard(self):
        src = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("H3_RESERVED_MARKETS", "KRW-BTC,KRW-ETH")', src)
        orphan = src[src.index("def sync_orphan_positions"):src.index("def sync_orphan_positions") + 3000]
        self.assertIn("if market in H3_RESERVED_MARKETS:\n                continue", orphan)
        entry = src[src.index("def open_auto_position"):src.index("def open_auto_position") + 6000]
        self.assertIn("if m in H3_RESERVED_MARKETS:", entry)
        self.assertLess(entry.index("if m in H3_RESERVED_MARKETS:"), entry.index("if LIVE_ENTRY_DISABLED:"))
        self.assertIn("_write_h3_reserved_marker()", src)
        # 주문이 나가는 함수는 하나뿐이고, 거기에 예약 마켓 차단이 있다 (진입·청산·유령·수동 모든 경로의 최종 방어)
        post = src[src.index("def upbit_private_post"):src.index("def get_order_result")]
        self.assertIn('if _is_order and str(body.get("market", "")).upper() in H3_RESERVED_MARKETS:', post)
        self.assertLess(post.index("H3_RESERVED_MARKETS"), post.index("sess.post("))       # 전송보다 먼저
        import re
        calls = [ln for ln in src.splitlines() if re.search(r"\bsess\.post\(", ln) and not ln.lstrip().startswith(("#", "기존", "변경"))]
        self.assertEqual(len(calls), 1, calls)                  # 업비트로 POST 하는 곳은 upbit_private_post 한 줄뿐
        self.assertEqual(src.count("requests.post("), 0)
        for fn in ("def place_market_buy", "def place_limit_buy", "def place_market_sell"):
            body = src[src.index(fn):src.index(fn) + 3000]
            self.assertIn('upbit_private_post("/v1/orders", body)', body)
        self.assertIn('"order_block": True', src)
        self.assertIn('"bot_md5": _bot_md5', src)


class TestShadow(Base):
    def test_buy_keep_sell_cycle_and_once_per_day(self):
        cfg = self.cfg()
        ex = FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}                    # BTC 신호 ON, ETH OFF
        ex.mid[BTC] = PRICE[BTC] * 1.05                        # 호가도 오늘 시가 근처
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual({r["market"]: r["action"] for r in rows}, {BTC: "BUY", ETH: "STAY_IN_CASH"})
        self.assertEqual(ex.placed, [])                        # SHADOW 는 주문을 내지 않는다
        st = HX.load_state(cfg)
        self.assertGreater(st["sleeves"][BTC]["coin"], 0)
        self.assertLess(st["sleeves"][BTC]["cash"], 1.0)
        self.assertEqual(st["sleeves"][ETH]["cash"], 1000000.0)
        btc = [r for r in rows if r["market"] == BTC][0]
        self.assertAlmostEqual(btc["slip_vs_open_bp"], 1e4 * (btc["fill"]["avg"] / btc["open_proxy"] - 1), places=6)
        self.assertGreater(btc["slip_vs_open_bp"], 0)          # 매도호가를 걷으니 시가보다 비싸게 산다
        self.assertAlmostEqual(btc["equity"], 1000000.0 / (1 + HX.FEE), delta=5)   # 수수료만큼 감소
        self.assertIn("BTC 매수", self.sent[-1])
        self.assertIn("ETH 현금유지", self.sent[-1])
        rows2, _ = self.executor(cfg, ex).run()                # 같은 날 재실행
        self.assertEqual(rows2, [])
        self.assertEqual(len(self.sent), 1)
        self.next_day()
        rows3, _ = self.executor(cfg, ex).run()
        self.assertEqual({r["market"]: r["action"] for r in rows3}, {BTC: "KEEP_HOLDING", ETH: "STAY_IN_CASH"})
        self.next_day()
        self.ratio[BTC] = 0.9                                  # 신호 OFF
        ex.mid[BTC] = PRICE[BTC] * 0.9
        rows4, _ = self.executor(cfg, ex).run()
        btc = [r for r in rows4 if r["market"] == BTC][0]
        self.assertEqual(btc["action"], "SELL")
        st = HX.load_state(cfg)
        self.assertEqual(st["sleeves"][BTC]["coin"], 0.0)
        self.assertAlmostEqual(st["sleeves"][BTC]["cash"], 1000000.0 * 0.9 / 1.05, delta=3000)   # 105 에 사서 90 에 팜
        self.assertGreater(btc["slip_vs_open_bp"], 0)          # 매수호가를 걷으니 시가보다 싸게 판다 = 불리(+)
        ledger = [json.loads(x) for x in cfg.ledger_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(ledger), 6)

    def test_missing_yesterday_candle_does_nothing(self):
        cfg = self.cfg()

        def no_yesterday(market, day):
            data = candles_for(market, day, 1.05)
            del data[TP._shift(day, -1)]
            return data
        ex = HX.Executor(cfg, FakeExchange(), fetch_candles=no_yesterday, notify=self.sent.append,
                         now=lambda: self.clock, bot_pids=[], sleep=lambda s: None)
        rows, errors = ex.run()
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 2)
        self.assertIn("not available yet", errors[0])
        self.assertFalse(cfg.state_path.exists() and HX.load_state(cfg)["sleeves"][BTC]["last_day"])
        self.assertIn("⚠", self.sent[-1])

    def test_dry_run_writes_nothing(self):
        cfg = self.cfg()
        self.ratio = {BTC: 1.05, ETH: 1.05}
        rows, _ = self.executor(cfg, FakeExchange()).run(dry_run=True)
        self.assertEqual([r["on"] for r in rows], [True, True])
        self.assertFalse(cfg.state_path.exists())
        self.assertEqual(self.sent, [])


class TestLive(Base):
    def test_buy_then_sell_with_confirmed_fills(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 1.05}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid", "h3-20261110-KRW-ETH-bid"])
        st = HX.load_state(cfg)
        for m in (BTC, ETH):
            s = st["sleeves"][m]
            self.assertIsNone(s["pending"])
            self.assertEqual(s["last_day"], "2026-11-10")
            self.assertAlmostEqual(s["coin"], ex.bal[m.split("-")[1]], places=9)   # 장부 = 계좌
            self.assertLess(s["cash"], 2.0)
        self.assertEqual([r["fill"]["uuid"] for r in rows], ["u-h3-20261110-KRW-BTC-bid", "u-h3-20261110-KRW-ETH-bid"])
        self.executor(cfg, ex).run()                           # 같은 날 재실행 → 주문 추가 없음
        self.assertEqual(len(ex.placed), 2)
        self.next_day()
        self.ratio = {BTC: 0.9, ETH: 1.05}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed[-1], "h3-20261111-KRW-BTC-ask")
        self.assertEqual(len(ex.placed), 3)
        st = HX.load_state(cfg)
        self.assertLess(st["sleeves"][BTC]["coin"], 1e-8)      # 8자리 내림으로 남는 먼지뿐
        self.assertLess(abs(ex.bal["BTC"]), 1e-8)

    def test_timeout_after_exchange_accepted_does_not_double_order(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        ex.fail_after_accept = True                            # 주문은 들어갔는데 응답을 못 받음
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid"])
        self.assertGreater(HX.load_state(cfg)["sleeves"][BTC]["coin"], 0)

    def test_restart_reconciles_pending_order_instead_of_reordering(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        state = HX.new_state(cfg)
        ident = "h3-20261110-KRW-BTC-bid"
        state["sleeves"][BTC]["pending"] = {"day": "2026-11-10", "side": "bid", "identifier": ident, "krw": 999500,
                                            "signal": {"close": 1.05e8, "average": 1.0e8, "on": True}}
        HX.save_state(cfg, state)
        ex.place(BTC, "bid", ident, krw=999500)                # 죽기 직전에 이미 체결된 주문
        ex.placed.clear()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, [])                        # 새 주문 없음
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertIsNone(s["pending"])
        self.assertEqual(s["last_day"], "2026-11-10")
        self.assertAlmostEqual(s["coin"], ex.bal["BTC"], places=9)
        ledger = [json.loads(x) for x in cfg.ledger_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(ledger[0]["note"], "reconciled after restart")
        self.assertEqual(ledger[0]["action"], "BUY")

    def test_pending_without_order_is_placed_once(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        state = HX.new_state(cfg)
        state["sleeves"][BTC]["pending"] = {"day": "2026-11-10", "side": "bid", "identifier": "h3-20261110-KRW-BTC-bid",
                                            "krw": 999500, "signal": {"close": 1.05e8, "average": 1.0e8, "on": True}}
        HX.save_state(cfg, state)                              # pending 만 쓰고 주문 전에 죽음
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid"])

    def test_order_not_placed_keeps_day_open_for_retry(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        ex.fail_before_accept = True
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(len(errors), 1)
        self.assertIn("not placed", errors[0])
        self.assertIsNone(HX.load_state(cfg)["sleeves"][BTC]["last_day"])     # 오늘이 닫히지 않음
        ex.fail_before_accept = False
        rows, errors = self.executor(cfg, ex).run()            # 두 번째 타이머 실행
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid"])
        self.assertGreater(HX.load_state(cfg)["sleeves"][BTC]["coin"], 0)

    def test_zero_fill_uses_a_new_identifier_next_time(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        ex.no_fill = True
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(len(errors), 1)
        self.assertIn("no fill", errors[0])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["pending"], s["last_day"], s["attempt"]), (None, None, {"day": "2026-11-10", "n": 1}))
        ex.no_fill = False
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid", "h3-20261110-KRW-BTC-bid-r1"])

    def test_buy_refused_when_scalper_would_adopt_the_holding(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex, bot_pids=[4242]).run()          # 봇은 도는데 마커 없음
        self.assertEqual(ex.placed, [])
        self.assertIn("BUY refused", errors[0])
        self.assertIsNone(HX.load_state(cfg)["sleeves"][BTC]["last_day"])
        cfg.bot_marker.write_text(json.dumps({"pid": 4242, "markets": [BTC, ETH], "order_block": True}))
        rows, errors = self.executor(cfg, ex, bot_pids=[4242]).run()
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid"])

    def test_smoke_test_round_trip(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        res = HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock, sleep=lambda s: None)
        self.assertEqual(ex.placed, ["h3-smoke-20261110000100-bid", "h3-smoke-20261110000100-ask"])
        self.assertLess(abs(res["coin_left"]), 1e-8)           # 산 만큼 팔았다
        self.assertTrue(0 < res["krw_cost"] < 30)              # 수수료 약 7원 + 호가 차이
        self.assertFalse(cfg.state_path.exists())              # 슬리브 장부는 건드리지 않는다
        with self.assertRaises(HX.H3Error):                    # 봇이 가드 없이 돌면 거부
            HX.smoke_test(cfg, FakeExchange(), bot_pids=[9], now=lambda: self.clock, sleep=lambda s: None)
        with self.assertRaises(HX.H3Error):                    # SHADOW 에서는 실주문을 내지 않는다
            HX.smoke_test(self.cfg("SHADOW"), FakeExchange(), bot_pids=[])

    def test_balance_checks(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange(krw=500000.0)                        # 슬리브 100만 원인데 계좌엔 50만 원
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(ex.placed, [])
        self.assertIn("account KRW", errors[0])
        ex.bal["KRW"] = 5_000_000.0
        self.executor(cfg, ex).run()
        self.assertEqual(len(ex.placed), 1)
        ex.bal["BTC"] *= 0.5                                   # 누군가 절반을 팔았다
        self.next_day()
        self.ratio[BTC] = 0.9
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertIn("selling what is there", self.sent[-1])
        self.assertLess(abs(ex.bal["BTC"]), 1e-8)


class TestReviewFixes(Base):
    """2026-11-10 검토에서 나온 결함의 회귀 테스트."""

    def _pending_btc_with_filled_order(self, cfg, ex):
        state = HX.new_state(cfg)
        ident = "h3-20261110-KRW-BTC-bid"
        state["sleeves"][BTC]["pending"] = {"day": "2026-11-10", "side": "bid", "identifier": ident, "krw": 999500,
                                            "signal": {"close": 1.05e8, "average": 1.0e8, "on": True}}
        HX.save_state(cfg, state)
        ex.place(BTC, "bid", ident, krw=999500)
        ex.placed.clear()

    def test_fill_is_not_applied_twice_when_ledger_write_fails(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        self._pending_btc_with_filled_order(cfg, ex)
        self.ratio = {BTC: 1.05, ETH: 0.95}
        real, calls = HX.append_ledger, []

        def flaky(c, row):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("disk full")
            return real(c, row)
        HX.append_ledger = flaky
        try:
            rows, errors = self.executor(cfg, ex).run()
        finally:
            HX.append_ledger = real
        self.assertEqual(errors, [])
        self.assertIn("ledger write failed", self.sent[-1])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertIsNone(s["pending"])                         # 체결 반영과 pending 해제가 함께 저장됐다
        self.assertAlmostEqual(s["coin"], ex.bal["BTC"], places=9)
        self.next_day()
        self.executor(cfg, ex).run()                           # 다음 실행에서 같은 체결을 다시 더하지 않는다
        self.assertAlmostEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], ex.bal["BTC"], places=9)
        self.assertEqual(ex.placed, [])

    def test_failed_market_does_not_leak_memory_state_into_the_other_markets_save(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        self._pending_btc_with_filled_order(cfg, ex)
        self.ratio = {BTC: 1.05, ETH: 1.05}                    # ETH 는 이번 실행에서 매수 → 상태를 저장한다
        real, calls = HX.save_state, []

        def flaky(c, st):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("disk hiccup")                   # BTC 정리 중 저장 실패 (메모리엔 체결이 이미 더해진 뒤)
            return real(c, st)
        HX.save_state = flaky
        try:
            rows, errors = self.executor(cfg, ex).run()
        finally:
            HX.save_state = real
        self.assertEqual(len(errors), 1)
        st = HX.load_state(cfg)
        self.assertIsNotNone(st["sleeves"][BTC]["pending"])     # BTC 는 디스크에서 그대로 (정리 전)
        self.assertEqual(st["sleeves"][BTC]["coin"], 0.0)       # 메모리의 체결이 ETH 저장에 섞여 들어가지 않았다
        self.assertGreater(st["sleeves"][ETH]["coin"], 0)
        rows, errors = self.executor(cfg, ex).run()            # 다시 실행 → 한 번만 반영
        self.assertEqual(errors, [])
        self.assertAlmostEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], ex.bal["BTC"], places=9)
        self.assertEqual(ex.placed, ["h3-20261110-KRW-ETH-bid"])

    def test_shortfall_sell_writes_off_the_missing_coin(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.executor(cfg, ex).run()
        ex.bal["BTC"] *= 0.5                                   # 절반이 계좌에서 사라짐
        self.next_day()
        self.ratio[BTC] = 0.9
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual(s["coin"], 0.0)                        # 없는 코인을 장부에 남기지 않는다
        self.assertIn("writing off", self.sent[-1])
        self.next_day()
        rows, errors = self.executor(cfg, ex).run()            # 다음 날: 유령 매도 시도가 없어야 한다
        self.assertEqual(errors, [])
        self.assertEqual([r["action"] for r in rows if r["market"] == BTC], ["STAY_IN_CASH"])

    def test_late_run_holds_buys_but_still_sells(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.clock = self.clock.replace(hour=3, minute=0)       # 09:00 KST 에서 3시간 늦음
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(ex.placed, [])
        self.assertIn("BUY held", errors[0])
        self.assertIsNone(HX.load_state(cfg)["sleeves"][BTC]["last_day"])     # 오늘이 닫히지 않음
        rows, errors = self.executor(cfg, ex, allow_late=True).run()          # 사람이 확인 후 허용
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid"])
        self.next_day()
        self.ratio[BTC] = 0.9
        rows, errors = self.executor(cfg, ex).run()            # 여전히 03:00 UTC — 매도는 늦어도 실행
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed[-1], "h3-20261111-KRW-BTC-ask")
        self.assertIn("SELL executed", self.sent[-1])

    def test_price_guards(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        ex.tick_ratio = 0.01                                    # 호가 간격 1% → 예상 체결가가 중간가보다 100bp 불리
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(ex.placed, [])
        self.assertIn("worse than mid", errors[0])
        ex.tick_ratio = 0.0001
        ex.mid[BTC] = PRICE[BTC] * 1.4                          # 어제 종가(105%)에서 33% 벗어난 가격
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(ex.placed, [])
        self.assertIn("away from yesterday", errors[0])
        ex.mid[BTC] = PRICE[BTC] * 1.05
        ex.level_size = 1e-6                                    # 호가 잔량이 주문보다 얇음
        rows, errors = self.executor(cfg, ex).run()
        self.assertIn("too thin", errors[0])
        ex.level_size = 1000.0
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual(len(ex.placed), 1)
        self.assertLess(HX.load_state(cfg) and rows[0]["fill"]["pretrade"]["est_slip_bp"], 30)
        self.next_day()
        self.ratio[BTC] = 0.9
        ex.tick_ratio = 0.05                                    # 매도 쪽 호가가 고장난 수준(500bp)
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(len(ex.placed), 1)                     # 매도 보류
        self.assertIn("🚨", errors[0])
        self.assertIn("SELL FAILED", errors[0])
        ex.tick_ratio = 0.005                                   # 50bp — 매수라면 거부지만 매도는 허용 범위
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed[-1], "h3-20261111-KRW-BTC-ask")

    def test_sell_is_never_blocked_by_the_bot_guard(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.executor(cfg, ex).run()                           # 봇이 없을 때 매수
        self.next_day()
        self.ratio[BTC] = 0.9
        rows, errors = self.executor(cfg, ex, bot_pids=[4242]).run()          # 가드 없는 봇이 도는 상황
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed[-1], "h3-20261111-KRW-BTC-ask")

    def test_smoke_test_recovers_after_dying_between_buy_and_sell(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        ex.fail_side = "ask"                                    # 매수는 되고 매도에서 죽음
        with self.assertRaises(HX.H3Error):
            HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock, sleep=lambda s: None)
        self.assertTrue(cfg.smoke_path.exists())                # 산 것이 기록으로 남아 있다
        self.assertGreater(ex.bal["BTC"], 0)
        self.ratio = {BTC: 0.95, ETH: 0.95}
        self.executor(cfg, ex).run()
        self.assertIn("unfinished smoke test", self.sent[-1])   # 일일 실행이 알려 준다
        ex.fail_side = None
        res = HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock + dt.timedelta(hours=1), sleep=lambda s: None)
        self.assertTrue(res["recovered"])
        self.assertLess(abs(ex.bal["BTC"]), 1e-8)               # 남은 수량을 팔았다
        self.assertFalse(cfg.smoke_path.exists())
        self.assertEqual(ex.placed, ["h3-smoke-20261110000100-bid", "h3-smoke-20261110000100-ask"])   # 새 매수 없음

    def test_smoke_recovery_when_the_buy_never_reached_the_exchange(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        ex.fail_before_accept = True
        with self.assertRaises(HX.H3Error):
            HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock, sleep=lambda s: None)
        self.assertTrue(cfg.smoke_path.exists())
        ex.fail_before_accept = False
        res = HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock, sleep=lambda s: None)
        self.assertIsNone(res["buy"])                           # 정리만 하고 끝 (아무것도 사지 않았으니)
        self.assertFalse(cfg.smoke_path.exists())
        self.assertEqual(ex.placed, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
