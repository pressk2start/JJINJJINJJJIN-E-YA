# -*- coding: utf-8 -*-
"""h3_executor 테스트 — 가짜 거래소로 SHADOW/LIVE 흐름, 하루 1회·재시작 안전, 중복 주문 방지, 단타 봇 분리 확인, 잔고 확인."""
import base64
import contextlib
import datetime as dt
import hashlib
import hmac
import importlib.util
import io
import json
import os
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
        self.sell_ratio = 1.0            # 매도 주문이 체결되는 비율 (1 미만이면 일부만 체결되고 cancel)
        self.hide_trades = False         # 끝난 주문인데 체결 내역(trades)이 아직 안 붙은 응답을 흉내
        self.lookups = None              # [True, False, …] 를 주면 order() 가 False 차례에 "없음" 을 돌려준다(흔들리는 조회)
        self.locked = {}                 # 통화 -> 다른 주문에 묶인 수량
        self.fail_markets = set()        # 이 종목의 주문은 넣기 전에 실패 (통신 오류)
        self.reject_code = None          # 숫자를 주면 place 가 그 HTTP 코드로 거절된다 (주문이 만들어지지 않음)

    def orderbook(self, market):
        p = self.mid[market]
        tick = p * self.tick_ratio
        asks = [(p + tick * (k + 1), self.level_size) for k in range(10)]
        bids = [(p - tick * (k + 1), self.level_size) for k in range(10)]
        return asks, bids

    def balances(self):
        return dict(self.bal)

    def account(self, currency):
        return self.bal.get(currency, 0.0), self.locked.get(currency, 0.0)

    def place(self, market, side, identifier, krw=None, volume=None):
        if self.fail_before_accept or self.fail_side == side or market in self.fail_markets:
            raise HX.H3Error("network down")
        if self.reject_code or identifier in self.orders:       # 거래소의 거절: HTTP 4xx 와 오류 이름이 온다
            err = HX.H3Error("upbit POST /v1/orders -> %d %s" % (self.reject_code or 400, "identifier already exists" if identifier in self.orders else "rejected"))
            err.code, err.name = self.reject_code or 400, "validation_error"
            raise err
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
                sold = volume * self.sell_ratio
                funds = sold * price
                trades = [{"price": str(price), "volume": str(sold), "funds": str(funds)}]
                fee = funds * HX.FEE
                self.bal["KRW"] += funds - fee
                self.bal[cur] = self.bal.get(cur, 0.0) - sold
        else:
            fee = 0.0
        self.orders[identifier] = {"uuid": "u-" + identifier,
                                   "state": "cancel" if (side == "bid" or self.sell_ratio < 1) else "done",
                                   "executed_funds": "0", "paid_fee": str(fee), "trades": trades,
                                   "executed_volume": str(sum(float(tr["volume"]) for tr in trades)), "trades_count": len(trades)}
        self.placed.append(identifier)
        if self.fail_after_accept:
            raise TimeoutError("read timed out")
        return self.orders[identifier]

    def order(self, identifier):
        if self.lookups:
            if not self.lookups.pop(0):
                return None
        found = self.orders.get(identifier)
        if found is not None and self.hide_trades:
            return dict(found, trades=[])
        return found


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

    def run_main(self, argv, **env):
        sent, saved = [], dict(os.environ)
        saved_to = HX.telegram_to
        HX.telegram_to = lambda token, chats: (lambda text: sent.append((token, tuple(chats), text)))
        os.environ.update({"H3_STATE_DIR": self.tmp.name, "H3_ENV_FILE": str(Path(self.tmp.name, "no.env")),
                           "TELEGRAM_TOKEN": "tok", "TG_CHATS": "1,2"})
        os.environ.update(env)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = HX.main(argv)
            return code, buf.getvalue(), sent
        finally:
            HX.telegram_to = saved_to
            os.environ.clear()
            os.environ.update(saved)

    def holding(self, cfg, ex):
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.executor(cfg, ex, bot_pids=[]).run()                                     # 11/10 매수
        self.assertGreater(HX.load_state(cfg)["sleeves"][BTC]["coin"], 0)
        self.next_day()
        self.ratio = {BTC: 0.95, ETH: 0.95}                                           # 11/11 신호 꺼짐 → 매도해야 한다


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

    def test_a_buy_recorded_but_never_seen_at_the_exchange_is_not_sent_again_that_day(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        state = HX.new_state(cfg)
        state["sleeves"][BTC]["pending"] = {"day": "2026-11-10", "side": "bid", "identifier": "h3-20261110-KRW-BTC-bid",
                                            "krw": 999500, "signal": {"close": 1.05e8, "average": 1.0e8, "on": True}}
        HX.save_state(cfg, state)                              # pending 만 쓰고 죽음 — 주문이 나갔는지 알 수 없다
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertIn("매수 건너뜀", errors[0])                # 매수는 하루 한 번만 보낸다 → 오늘은 포기
        self.assertEqual(ex.placed, [])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["pending"], s["last_day"]), (None, None))
        self.assertEqual([(r["id"], r["status"]) for r in s["sent"]], [("h3-20261110-KRW-BTC-bid", "open")])
        self.next_day()
        rows, errors = self.executor(cfg, ex).run()            # 다음 날은 새로 판단해 새 번호로 산다
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261111-KRW-BTC-bid"])

    def test_a_buy_that_was_not_placed_is_given_up_for_the_day(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        ex.fail_before_accept = True
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(len(errors), 1)
        self.assertIn("not placed", errors[0])
        self.assertIsNone(HX.load_state(cfg)["sleeves"][BTC]["last_day"])     # 오늘이 닫히지 않음
        ex.fail_before_accept = False
        rows, errors = self.executor(cfg, ex).run()            # 두 번째 타이머 실행: 들어갔는지 모르는 매수를 다시 보내지 않는다
        self.assertIn("매수 건너뜀", errors[0])
        self.assertEqual(ex.placed, [])
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], 0.0)
        self.next_day()
        rows, errors = self.executor(cfg, ex).run()            # 다음 날 다시 판단
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261111-KRW-BTC-bid"])
        self.assertGreater(HX.load_state(cfg)["sleeves"][BTC]["coin"], 0)

    def test_a_zero_fill_buy_is_not_sent_again_that_day(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        ex.no_fill = True
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(len(errors), 1)
        self.assertIn("no fill", errors[0])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["pending"], s["last_day"]), (None, None))
        self.assertEqual([(r["id"], r["side"], r["status"]) for r in s["sent"]], [("h3-20261110-KRW-BTC-bid", "bid", "nofill")])
        ex.no_fill = False
        rows, errors = self.executor(cfg, ex).run()
        self.assertIn("매수 건너뜀", errors[0])
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid"])                # 쓴 번호도, 새 번호도 다시 보내지 않았다
        self.next_day()
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid", "h3-20261111-KRW-BTC-bid"])

    def test_a_zero_fill_sell_is_sold_again_under_a_new_number(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        self.holding(cfg, ex)                                  # 11/11, 신호 꺼짐
        ex.no_fill = True
        rows, errors = self.executor(cfg, ex).run()
        self.assertIn("SELL FAILED", errors[0])
        self.assertIn("no fill", errors[0])
        ex.no_fill = False
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed[-2:], ["h3-20261111-KRW-BTC-ask", "h3-20261111-KRW-BTC-ask-r1"])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual([(r["id"], r["status"]) for r in s["sent"]],
                         [("h3-20261111-KRW-BTC-ask", "nofill"), ("h3-20261111-KRW-BTC-ask-r1", "applied")])
        self.assertLess(s["coin"], 1e-8)

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
        self.assertIn("계좌에 있던", self.sent[-1])             # 있는 만큼만 팔고 알린다
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

    def test_shortfall_sell_leaves_the_missing_coin_on_the_book_until_a_human_writes_it_off(self):
        cfg = self.cfg("LIVE")
        ex = FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.executor(cfg, ex).run()
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        ex.bal["BTC"] *= 0.5                                   # 절반이 계좌에서 사라짐
        self.next_day()
        self.ratio[BTC] = 0.9
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertAlmostEqual(s["coin"], book * 0.5, places=7)                 # 팔린 만큼만 줄었다 — 자동으로 지우지 않는다
        self.assertEqual((s["last_day"], s["sell_blocked"]), ("2026-11-10", "2026-11-11"))
        self.assertIn("🚨 BTC: 계좌에 있던", self.sent[-1])
        self.assertIn("--writeoff KRW-BTC", self.sent[-1])
        self.assertEqual([r["note"] for r in rows if r["market"] == BTC], ["account held less than the book — remainder left on the book"])
        placed, sent = list(ex.placed), len(self.sent)
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.executor(cfg, ex).run()            # 같은 날 다시 돌아도 주문도 알림도 없다
        self.assertEqual((rows, errors, ex.placed, len(self.sent)), ([], [], placed, sent))
        changed, lines = HX.writeoff(cfg, ex, BTC, True, now=lambda: self.clock)      # 사람이 계좌를 확인하고 장부를 맞춘다
        self.assertTrue(changed)
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
        self.assertEqual(ex.placed, ["h3-smoke-20261110000100-bid", "h3-smoke-20261110010100-ask"])   # 새 매수 없음. 매도는 새 번호로

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


class TestAdvisorFollowUps(Base):
    """고문 검토에서 나온 보완 4건 (2026-11-11~13): 설정 오류 알림, 복구 체결 알림, 점검 주문 복구 순서, 매도 실패 뒤 재시도."""

    # ---- 1) 설정 오류: 한 줄로 끝내고 알린다
    def test_a_bad_number_is_a_config_error_and_the_value_is_not_echoed(self):
        for key in ("H3_CAPITAL_KRW", "H3_MAX_LATE_MIN", "H3_MAX_BUY_SLIP_BP", "H3_MAX_SELL_SLIP_BP", "H3_MAX_GAP_PCT"):
            with self.assertRaises(HX.H3Error) as ctx:
                HX.Config({key: "0xSECRETSECRET"})
            self.assertIn(key, str(ctx.exception))
            self.assertNotIn("SECRET", str(ctx.exception))                       # 잘못 붙여 넣은 값이 새지 않는다
            self.assertIn("14 characters", str(ctx.exception))
        with self.assertRaises(HX.H3Error):
            HX.Config({"H3_CAPITAL_KRW": "AMOUNT"})                              # 2026-11-11 실제로 있었던 입력
        with self.assertRaises(HX.H3Error):
            HX.Config({"H3_CAPITAL_KRW": "inf"})
        self.assertEqual(HX.Config({"H3_CAPITAL_KRW": " 1,500,000 "}).capital, 1500000.0)
        self.assertEqual(HX.Config({"H3_MAX_LATE_MIN": "0"}).max_late_min, 0.0)      # 0 은 값이다 (기본값으로 바뀌지 않는다)
        self.assertEqual(HX.Config({}).max_sell_slip_bp, 150.0)

    def test_a_config_error_ends_in_one_line_and_is_sent_to_telegram(self):
        code, out, sent = self.run_main([], H3_MODE="LIVE", H3_CAPITAL_KRW="AMOUNT")
        self.assertEqual(code, 2)
        self.assertEqual(out.strip().splitlines(), ["[h3] config error: H3_CAPITAL_KRW must be a number (got 6 characters that are not a number)"])
        self.assertEqual(sent, [("tok", ("1", "2"), "H3 설정 오류 — 실행하지 않음: H3_CAPITAL_KRW must be a number (got 6 characters that are not a number)")])
        self.assertFalse(Path(self.tmp.name, "h3_state_live.json").exists())         # 아무것도 하지 않았다
        code, out, sent = self.run_main([], H3_MODE="LIVE", H3_CAPITAL_KRW="1000000", UPBIT_ACCESS_KEY="", UPBIT_SECRET_KEY="")
        self.assertEqual((code, len(sent)), (2, 1))                                  # 키가 없는 것도 같은 길로 알린다
        self.assertIn("LIVE needs UPBIT_ACCESS_KEY", sent[0][2])

    def test_looking_only_does_not_send_a_message(self):
        for argv in (["--status"], ["--dry-run"]):
            code, out, sent = self.run_main(argv, H3_CAPITAL_KRW="AMOUNT")
            self.assertEqual((code, sent), (2, []))
            self.assertIn("config error", out)

    # ---- 2) 재시작 복구로 반영한 체결을 알린다
    def pending_buy(self, cfg, ex, day="2026-11-10", placed=True):
        state = HX.new_state(cfg)
        ident = "h3-%s-KRW-BTC-bid" % day.replace("-", "")
        state["sleeves"][BTC]["pending"] = {"day": day, "side": "bid", "identifier": ident, "krw": 999500,
                                            "signal": {"close": 1.05e8, "average": 1.0e8, "on": True}}
        HX.save_state(cfg, state)
        if placed:
            ex.place(BTC, "bid", ident, krw=999500)                                   # 죽기 직전에 이미 체결된 주문
            ex.placed.clear()
        return ident

    def test_a_fill_applied_on_recovery_is_reported_in_the_daily_line(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.pending_buy(cfg, ex)
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual((errors, ex.placed), ([], []))
        self.assertEqual([(r["market"], r["action"]) for r in rows], [(BTC, "BUY"), (ETH, "STAY_IN_CASH")])   # 오늘 것이면 요약에도 실린다
        line = self.sent[-1]
        self.assertIn("BTC 매수", line)
        self.assertIn("⚠ BTC: 중단됐던 실행의 매수 체결을 반영함 (2026-11-10, ", line)
        self.assertIn("평가 ", line)

    def test_a_fill_from_an_earlier_day_is_reported_before_todays_decision(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.pending_buy(cfg, ex, day="2026-11-09")
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual((errors, ex.placed), ([], []))                               # 어제 산 것을 들고 있으니 오늘은 보유 유지
        self.assertEqual([(r["market"], r["action"], r["day"]) for r in rows], [(BTC, "KEEP_HOLDING", "2026-11-10"), (ETH, "STAY_IN_CASH", "2026-11-10")])
        self.assertIn("⚠ BTC: 중단됐던 실행의 매수 체결을 반영함 (2026-11-09, ", self.sent[-1])

    def test_an_interrupted_buy_that_ended_unfilled_is_reported_and_not_sent_again_that_day(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        ex.no_fill = True
        self.pending_buy(cfg, ex)
        ex.no_fill = False
        self.ratio = {BTC: 1.05, ETH: 0.95}
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()
        self.assertIn("매수 건너뜀", errors[0])                                        # 매수는 하루 한 번만 보낸다
        self.assertEqual(ex.placed, [])
        self.assertIn("BTC: 중단됐던 매수 주문(2026-11-10)이 체결 없이 끝났음 — 다시 판단함", self.sent[-1])
        self.next_day()
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()
        self.assertEqual((errors, ex.placed), ([], ["h3-20261111-KRW-BTC-bid"]))       # 다음 날 새 번호로 산다

    # ---- 3) 점검 주문 복구는 매도 주문부터 조회한다
    def finished_smoke_with_leftover_file(self, cfg, ex):
        res = HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock, sleep=lambda s: None)
        self.assertFalse(cfg.smoke_path.exists())
        HX._write_json(cfg.smoke_path, {"market": BTC, "krw": HX.SMOKE_KRW, "buy_id": "h3-smoke-20261110000100-bid",
                                        "sell_id": "h3-smoke-20261110000100-ask"})   # 매도까지 끝났는데 파일을 지우기 전에 죽었다
        return res

    def test_smoke_recovery_when_the_sell_had_already_gone_through(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        first = self.finished_smoke_with_leftover_file(cfg, ex)
        placed = list(ex.placed)
        self.assertLess(abs(ex.bal["BTC"]), 1e-8)                                     # 계좌에는 팔 수 있는 수량이 없다 (먼지뿐)
        res = HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock + dt.timedelta(hours=1), sleep=lambda s: None)
        self.assertTrue(res["recovered"])
        self.assertEqual(ex.placed, placed)                                           # 새 주문 없이 지난 주문의 체결만 확인했다
        self.assertAlmostEqual(res["krw_cost"], first["krw_cost"])
        self.assertLess(res["coin_left"], 1e-8)
        self.assertFalse(cfg.smoke_path.exists())

    def test_smoke_recovery_does_not_touch_what_h3_itself_holds(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.finished_smoke_with_leftover_file(cfg, ex)
        placed = list(ex.placed)
        ex.bal["BTC"] = 0.5                                                           # H3 가 같은 코인을 들고 있다
        res = HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock + dt.timedelta(hours=1), sleep=lambda s: None)
        self.assertEqual(ex.placed, placed)
        self.assertEqual(ex.bal["BTC"], 0.5)                                          # 그 잔고는 그대로다
        self.assertFalse(cfg.smoke_path.exists())
        self.assertTrue(res["recovered"])

    # ---- 4) 매도 실패 뒤: 그날 두 번째 실행과 다음 날 실행이 다시 판다
    def test_a_failed_sell_is_retried_by_the_second_timer_run(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        ex.fail_side = "ask"
        rows, errors = self.executor(cfg, ex).run()                                   # 00:01 실행
        self.assertEqual(len(errors), 1)
        self.assertIn("🚨 KRW-BTC SELL FAILED — position is still open", errors[0])
        self.assertIn("🚨", self.sent[-1])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual(s["last_day"], "2026-11-10")                                 # 오늘이 닫히지 않았다
        self.assertGreater(s["coin"], 0)
        ex.fail_side = None
        self.clock += dt.timedelta(minutes=10)                                        # 00:11 실행
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual([(r["market"], r["action"]) for r in rows], [(BTC, "SELL")])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual(s["last_day"], "2026-11-11")
        self.assertLess(s["coin"], 1e-8)                                              # 소수 8자리 아래 먼지만 남는다
        self.assertEqual(ex.placed[-1], "h3-20261111-KRW-BTC-ask-r1")                 # 한 번 쓴 번호는 다시 쓰지 않는다
        self.assertNotIn("h3-20261111-KRW-BTC-ask", ex.placed)                        # 첫 주문은 거래소에 들어가지 않았다

    def test_a_sell_that_failed_all_day_is_tried_again_the_next_day(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        ex.fail_side = "ask"
        for minutes in (0, 10):                                                       # 그날 두 번 다 실패
            self.clock += dt.timedelta(minutes=minutes)
            rows, errors = self.executor(cfg, ex).run()
            self.assertIn("SELL FAILED", errors[0])
        ex.fail_side = None
        self.clock = dt.datetime(2026, 11, 12, 0, 1, 0, tzinfo=dt.timezone.utc)
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])
        self.assertEqual([(r["market"], r["action"], r["day"]) for r in rows], [(BTC, "SELL", "2026-11-12"), (ETH, "STAY_IN_CASH", "2026-11-12")])
        self.assertLess(HX.load_state(cfg)["sleeves"][BTC]["coin"], 1e-8)


class Untouchable(object):
    """아무 메서드나 부르면 실패하는 가짜 — '조회도 하지 않는다' 를 확인하는 데 쓴다."""
    def __getattr__(self, name):
        raise AssertionError("must not be called: %s" % name)


class TestSellRetry(Base):
    """매도만 다시 시도하는 실행 (--sell-retry, 고문 결정 2026-11-13): 기존 주문 조회가 먼저, 매수는 하지 않음,
    매도가 끝나면 더 돌지 않음, 할 일이 없으면 알림 없음."""

    def retry(self, cfg, ex, fetch=None):
        runner = HX.Executor(cfg, ex, fetch_candles=fetch or (lambda m, day: candles_for(m, day, self.ratio[m])),
                             notify=self.sent.append, now=lambda: self.clock, bot_pids=[], sleep=lambda s: None)
        return runner.run_sell_retry()

    def no_lookup(self, market, day):
        raise AssertionError("must not fetch candles")

    def ledger(self, cfg):
        return [json.loads(x) for x in cfg.ledger_path.read_text(encoding="utf-8").splitlines()] if cfg.ledger_path.exists() else []

    def test_nothing_to_do_means_no_lookups_no_orders_no_message(self):
        cfg = self.cfg("LIVE")
        self.assertEqual(self.retry(cfg, Untouchable(), self.no_lookup), ([], []))          # 현금뿐 → 시세도 거래소도 조회하지 않는다
        self.assertFalse(cfg.state_path.exists())
        ex = FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.executor(cfg, ex, bot_pids=[]).run()                                            # 11/10 매수, 오늘 처리 끝
        placed, sent = list(ex.placed), len(self.sent)
        self.clock += dt.timedelta(minutes=5)
        self.assertEqual(self.retry(cfg, Untouchable(), self.no_lookup), ([], []))          # 보유 중이어도 오늘 처리됐으면 조회하지 않는다
        self.assertEqual((ex.placed, len(self.sent)), (placed, sent))
        self.assertEqual(self.retry(self.cfg("SHADOW"), Untouchable(), self.no_lookup), ([], []))   # SHADOW 에서는 아무것도 하지 않는다

    def test_retry_sells_what_the_daily_run_could_not_and_then_stops(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)                                                                # 11/11, 신호 꺼짐
        ex.fail_side = "ask"
        rows, errors = self.executor(cfg, ex).run()                                          # 00:01 하루 실행: 매도 실패
        self.assertIn("SELL FAILED", errors[0])
        ex.fail_side = None
        self.clock += dt.timedelta(minutes=5)                                                # 00:06 재시도
        rows, errors = self.retry(cfg, ex)
        self.assertEqual(errors, [])
        self.assertEqual([(r["market"], r["action"]) for r in rows], [(BTC, "SELL")])
        self.assertEqual(ex.placed[-1], "h3-20261111-KRW-BTC-ask-r1")                        # 새 번호로 다시 냈다
        self.assertEqual([x for x in ex.placed if "ask" in x], ["h3-20261111-KRW-BTC-ask-r1"])   # 매도 주문은 한 번만 들어갔다
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual(s["last_day"], "2026-11-11")
        self.assertLess(s["coin"], 1e-8)
        self.assertIn("BTC 매도", self.sent[-1])
        self.assertIn("BTC: 재시도 실행에서 매도함", self.sent[-1])
        self.assertEqual(self.ledger(cfg)[-1]["action"], "SELL")
        placed, sent = list(ex.placed), len(self.sent)
        self.clock += dt.timedelta(minutes=10)
        self.assertEqual(self.retry(cfg, Untouchable(), self.no_lookup), ([], []))          # 매도가 끝났으면 더 돌지 않는다
        self.assertEqual((ex.placed, len(self.sent)), (placed, sent))
        rows, errors = self.executor(cfg, ex).run()                                          # 00:11 하루 실행의 두 번째: BTC 는 이미 끝났다
        self.assertEqual((errors, ex.placed), ([], placed))

    def test_retry_never_buys_and_leaves_the_day_open_for_the_daily_run(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        state = HX.new_state(cfg)
        state["sleeves"][BTC]["pending"] = {"day": "2026-11-10", "side": "bid", "identifier": "h3-20261110-KRW-BTC-bid",
                                            "krw": 999500, "signal": {"close": 1.05e8, "average": 1.0e8, "on": True}}
        HX.save_state(cfg, state)                                                            # 매수 주문을 내기 전에 끊긴 하루 실행
        self.ratio = {BTC: 1.05, ETH: 1.05}                                                  # 둘 다 신호 켜짐 → 하루 실행이라면 매수
        self.assertEqual(self.retry(cfg, ex), ([], []))
        self.assertEqual(ex.placed, [])                                                      # 재시도는 매수 주문을 내지 않는다
        s = HX.load_state(cfg)["sleeves"]
        self.assertEqual((s[BTC]["pending"], s[BTC]["last_day"], s[ETH]["last_day"]), (None, None, None))   # 오늘은 닫히지 않았다
        self.assertEqual((self.ledger(cfg), self.sent), ([], []))
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()                             # 매수는 하루 실행이 한다
        self.assertEqual(ex.placed, ["h3-20261110-KRW-ETH-bid"])
        self.assertEqual(len(errors), 1)                                                     # BTC 는 나갔는지 모르는 매수가 있어 오늘은 보내지 않는다
        self.assertIn("KRW-BTC: BTC 매수 건너뜀", errors[0])

    def test_retry_does_not_record_holding_or_cash_decisions(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.executor(cfg, ex, bot_pids=[]).run()                                            # 11/10 BTC 매수
        self.next_day()                                                                      # 11/11, 신호 그대로 켜짐. 하루 실행은 아직
        before, sent, placed = self.ledger(cfg), len(self.sent), list(ex.placed)
        self.assertEqual(self.retry(cfg, ex), ([], []))
        self.assertEqual((self.ledger(cfg), len(self.sent), ex.placed), (before, sent, placed))
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["last_day"], "2026-11-10")       # 보유 유지 기록은 하루 실행의 일
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual([(r["market"], r["action"]) for r in rows], [(BTC, "KEEP_HOLDING"), (ETH, "STAY_IN_CASH")])

    def test_retry_checks_the_open_order_before_anything_else(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)                                                                # 11/11, 신호 꺼짐
        state = HX.load_state(cfg)
        volume = float(HX.fmt_volume(state["sleeves"][BTC]["coin"]))
        ident = "h3-20261111-KRW-BTC-ask"
        state["sleeves"][BTC]["pending"] = {"day": "2026-11-11", "side": "ask", "identifier": ident, "volume": volume,
                                            "signal": {"close": 0.95e8, "average": 1.0e8, "on": False}}
        HX.save_state(cfg, state)
        ex.place(BTC, "ask", ident, volume=volume)                                           # 주문은 들어가 체결됐는데 반영 전에 끊겼다
        placed = list(ex.placed)
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.retry(cfg, ex, self.no_lookup)                                   # 판단을 다시 하지 않고도 끝난다
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, placed)                                                  # 새 주문 없음 — 같은 것을 두 번 팔지 않는다
        self.assertEqual([(r["action"], r["note"]) for r in rows], [("SELL", "reconciled after restart")])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["pending"], s["last_day"]), (None, "2026-11-11"))
        self.assertLess(s["coin"], 1e-8)
        self.assertIn("BTC: 중단됐던 실행의 매도 체결을 반영함", self.sent[-1])
        self.assertNotIn("재시도 실행에서 매도함", self.sent[-1])

    def test_repeated_failures_alert_once_an_hour_and_success_always_reports(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        ex.fail_side = "ask"
        self.sent.clear()
        self.clock += dt.timedelta(minutes=5)                                                # 00:06
        for k in range(6):                                                                   # 00:06 ~ 00:56 여섯 번 실패
            rows, errors = self.retry(cfg, ex)
            self.assertIn("SELL FAILED", errors[0])
            self.clock += dt.timedelta(minutes=10)
        self.assertEqual(len(self.sent), 1)                                                  # 알림은 한 번
        self.assertIn("🚨", self.sent[0])
        self.assertNotIn("late", self.sent[0])                                               # 팔지 못했는데 '늦게 팔았다' 고 하지 않는다
        self.retry(cfg, ex)                                                                  # 01:06 — 한 시간이 지났다
        self.assertEqual(len(self.sent), 2)
        self.assertGreater(HX.load_state(cfg)["sleeves"][BTC]["coin"], 0)                    # 포지션은 그대로다
        ex.fail_side = None
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.retry(cfg, ex)
        self.assertEqual(([r["action"] for r in rows], errors), (["SELL"], []))
        self.assertEqual(len(self.sent), 3)                                                  # 성공은 바로 알린다
        self.assertIn("late", self.sent[-1])                                                 # 늦게 판 것도 같이 알린다
        self.assertEqual(HX.load_state(cfg).get("retry_alerts"), {})

    def test_without_yesterdays_candle_the_retry_stays_silent(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        placed, sent = list(ex.placed), len(self.sent)

        def no_data(market, day):
            raise HX.H3Error("completed candle not available yet")
        self.assertEqual(self.retry(cfg, ex, no_data), ([], []))
        self.assertEqual((ex.placed, len(self.sent)), (placed, sent))

    def test_command_line_is_quiet_when_there_is_nothing_to_do(self):
        code, out, sent = self.run_main(["--sell-retry"])                                    # SHADOW(기본) → 바로 끝난다
        self.assertEqual((code, out, sent), (0, "", []))
        self.assertFalse(Path(self.tmp.name, "h3.lock").exists())
        code, out, sent = self.run_main(["--sell-retry"], H3_MODE="LIVE", H3_CAPITAL_KRW="AMOUNT")
        self.assertEqual((code, sent), (2, []))                                              # 설정 오류 알림은 하루 실행이 한다 (10분마다 보내지 않는다)
        code, out, sent = self.run_main(["--sell-retry"], H3_MODE="LIVE", H3_CAPITAL_KRW="2000000",
                                        UPBIT_ACCESS_KEY="ak", UPBIT_SECRET_KEY="sk")
        self.assertEqual((code, out, sent), (0, "", []))                                     # LIVE 라도 보유가 없으면 조용하다

    def test_timer_units(self):
        trend = ROOT / "trend"
        service = (trend / "h3-sell-retry.service").read_text(encoding="utf-8")
        timer = (trend / "h3-sell-retry.timer").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/bin/python3 -u /home/ubuntu/bot/trend/h3_executor.py --sell-retry", service)
        self.assertIn("EnvironmentFile=-/home/ubuntu/h3_state/h3.env", service)
        self.assertIn("OnCalendar=*-*-* *:06/10:00 UTC", timer)                              # 10분마다, 하루 실행(01·11분)과 다른 분
        self.assertNotIn("Persistent", timer)
        daily = (trend / "h3-executor.timer").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* 00:01:00 UTC", daily)
        self.assertIn("OnCalendar=*-*-* 00:11:00 UTC", daily)
        self.assertNotIn("--sell-retry", (trend / "h3-executor.service").read_text(encoding="utf-8"))


class TestFullReviewFixes(Base):
    """2026-11-14 전체 검토에서 나온 것: 체결 판독, 정리는 조회만, 일부 체결, --dry-run 조합, 알림 제한, 모드 되돌아감."""

    def retry(self, cfg, ex):
        runner = HX.Executor(cfg, ex, fetch_candles=lambda m, day: candles_for(m, day, self.ratio[m]),
                             notify=self.sent.append, now=lambda: self.clock, bot_pids=[], sleep=lambda s: None)
        return runner.run_sell_retry()

    # ---- 체결 내역이 늦게 붙는 응답을 '체결 없음' 으로 보지 않는다
    def test_a_final_order_without_listed_trades_is_not_treated_as_unfilled(self):
        self.assertEqual(HX.parse_fill({"uuid": "u", "state": "cancel", "trades": [], "executed_volume": "0", "paid_fee": "0"})["volume"], 0.0)
        for extra in ({"executed_volume": "0.01"}, {"trades_count": 2}):
            with self.assertRaises(HX.H3Error) as ctx:
                HX.parse_fill(dict({"uuid": "u", "state": "done", "trades": [], "paid_fee": "5"}, **extra))
            self.assertNotIsInstance(ctx.exception, HX.NoFill)

    def test_a_buy_whose_trades_arrive_late_is_not_ordered_twice(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        ex.hide_trades = True
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()                      # 00:01: 주문은 체결됐는데 내역이 아직 안 보인다
        self.assertIn("trades are not listed yet", errors[0])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["pending"]["identifier"], s["last_day"]), ("h3-20261110-KRW-BTC-bid", None))
        self.assertEqual([(r["id"], r["status"]) for r in s["sent"]], [("h3-20261110-KRW-BTC-bid", "open")])
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()                      # 00:11: 여전히 안 보인다 → 그대로 기다린다
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid"])                      # 새 번호(-r1)로 다시 사지 않는다
        ex.hide_trades = False
        self.next_day()
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()                      # 다음 날: 내역이 보인다 → 한 번만 반영
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid"])
        self.assertAlmostEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], ex.bal["BTC"], places=9)

    # ---- 미처리 주문 정리는 조회만 한다
    def pending_buy(self, cfg, ex):
        state = HX.new_state(cfg)
        ident = "h3-20261110-KRW-BTC-bid"
        state["sleeves"][BTC]["pending"] = {"day": "2026-11-10", "side": "bid", "identifier": ident, "krw": 999500,
                                            "signal": {"close": 1.05e8, "average": 1.0e8, "on": True}}
        HX.save_state(cfg, state)
        ex.place(BTC, "bid", ident, krw=999500)
        ex.placed.clear()
        return ident

    def test_reconciling_never_places_an_order_even_when_the_lookup_flaps(self):
        for runner_kind in ("retry", "daily"):
            with self.subTest(runner_kind):
                self.tmp.cleanup()
                self.tmp = tempfile.TemporaryDirectory()
                cfg, ex = self.cfg("LIVE"), FakeExchange()
                self.pending_buy(cfg, ex)
                self.ratio = {BTC: 1.05, ETH: 0.95}
                ex.lookups = [True] + [False] * 40                                    # 첫 조회에는 있고, 그 뒤로는 "없음" 만 온다
                if runner_kind == "retry":
                    rows, errors = self.retry(cfg, ex)
                else:
                    rows, errors = self.executor(cfg, ex, bot_pids=[]).run()
                self.assertEqual([p for p in ex.placed if "BTC" in p], [])            # 정리 단계에서는 어떤 주문도 나가지 않는다
                self.assertTrue(any("not final yet" in e for e in errors))
                self.assertIsNotNone(HX.load_state(cfg)["sleeves"][BTC]["pending"])   # 미처리 주문 기록은 남는다

    def test_smoke_recovery_does_not_buy_again_when_the_lookup_flaps(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        ex.fail_side = "ask"
        with self.assertRaises(HX.H3Error):
            HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock, sleep=lambda s: None)
        ex.fail_side = None
        placed = list(ex.placed)
        ex.lookups = [True] + [False] * 40
        with self.assertRaises(HX.H3Error):
            HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock, sleep=lambda s: None)
        self.assertEqual(ex.placed, placed)                                           # 매수를 다시 내지 않았다

    # ---- 매도가 일부만 체결되면 그날을 닫지 않는다
    def test_a_partly_filled_sell_keeps_the_day_open_and_the_rest_is_sold_under_a_new_id(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)                                                         # 11/11, 신호 꺼짐
        before = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        ex.sell_ratio = 0.4
        rows, errors = self.executor(cfg, ex).run()                                   # 00:01: 40%만 팔리고 끝났다
        self.assertEqual(errors, [])
        row = next(r for r in rows if r["market"] == BTC)
        self.assertEqual((row["action"], row["note"]), ("SELL", "partial fill — remainder still held"))
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["last_day"], s["pending"]), ("2026-11-10", None))
        self.assertEqual([(r["id"], r["status"]) for r in s["sent"]], [("h3-20261111-KRW-BTC-ask", "applied")])
        self.assertAlmostEqual(s["coin"], before * 0.6, places=8)
        self.assertIn("🚨 BTC: 매도가 일부만 체결됨", self.sent[-1])
        ex.sell_ratio = 1.0
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.retry(cfg, ex)                                            # 00:06 재시도가 남은 것을 판다
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed[-2:], ["h3-20261111-KRW-BTC-ask", "h3-20261111-KRW-BTC-ask-r1"])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual(s["last_day"], "2026-11-11")
        self.assertLess(s["coin"], 1e-8)

    def test_nothing_is_written_off_automatically_after_partial_fills_and_a_short_account(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        ex.bal["BTC"] = book * 0.5                                                    # 계좌에는 장부의 절반만 있다
        ex.sell_ratio = 0.4
        self.executor(cfg, ex).run()
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertAlmostEqual(s["coin"], book * 0.8, places=7)                       # 팔린 만큼(절반의 40%)만 줄었다
        self.assertAlmostEqual(ex.bal["BTC"], book * 0.5 * 0.6, places=8)             # 계좌에 안 팔린 코인이 남아 있다
        self.assertIsNone(s.get("sell_blocked"))                                      # 일부 체결 → 다시 판다
        ex.sell_ratio = 1.0
        self.clock += dt.timedelta(minutes=5)
        self.retry(cfg, ex)
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertLess(ex.bal["BTC"], 1e-8)                                          # 계좌에 있던 것은 다 팔았다
        self.assertAlmostEqual(s["coin"], book * 0.5, places=7)                       # 계좌에 없던 절반은 장부에 그대로 — 사람이 정리한다
        self.assertEqual((s["last_day"], s["sell_blocked"]), ("2026-11-10", "2026-11-11"))
        self.assertIn("--writeoff KRW-BTC", self.sent[-1])

    def test_a_recovered_partial_sell_also_keeps_the_day_open(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        state = HX.load_state(cfg)
        volume = float(HX.fmt_volume(state["sleeves"][BTC]["coin"]))
        ident = "h3-20261111-KRW-BTC-ask"
        state["sleeves"][BTC]["pending"] = {"day": "2026-11-11", "side": "ask", "identifier": ident, "volume": volume,
                                            "signal": {"close": 0.95e8, "average": 1.0e8, "on": False}}
        HX.save_state(cfg, state)
        ex.sell_ratio = 0.4
        ex.place(BTC, "ask", ident, volume=volume)                                    # 40%만 체결된 채 끊겼다
        ex.sell_ratio = 1.0
        placed = list(ex.placed)
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.retry(cfg, ex)                                            # 00:06: 지난 주문의 체결만 반영한다
        self.assertEqual((errors, ex.placed), ([], placed))                           # 반영한 실행에서는 새 주문을 내지 않는다
        self.assertEqual([(r["action"], r["note"]) for r in rows], [("SELL", "reconciled after restart — partial fill — remainder still held")])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["last_day"], s["pending"]), ("2026-11-10", None))         # 그날은 닫히지 않았다
        self.assertIn("일부만 체결됨", self.sent[-1])
        self.assertIn("이번 실행에서는 새 매도 주문을 내지 않음", self.sent[-1])
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.retry(cfg, ex)                                            # 00:16: 남은 것을 새 번호로 판다
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed[-1], "h3-20261111-KRW-BTC-ask-r1")
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual(s["last_day"], "2026-11-11")
        self.assertLess(s["coin"], 1e-8)

    # ---- --dry-run 은 실주문 명령과 같이 쓸 수 없다
    def test_dry_run_refuses_to_combine_with_commands_that_place_orders(self):
        for argv in (["--sell-retry", "--dry-run"], ["--smoke-test", "--dry-run"], ["--writeoff", "KRW-BTC", "--yes", "--dry-run"]):
            code, out, sent = self.run_main(argv, H3_MODE="LIVE", H3_CAPITAL_KRW="2000000", UPBIT_ACCESS_KEY="ak", UPBIT_SECRET_KEY="sk")
            self.assertEqual((code, sent), (2, []))
            self.assertIn("--dry-run cannot be combined", out)
            self.assertFalse(Path(self.tmp.name, "h3.lock").exists())                 # 잠금도 잡기 전에 끝난다

    # ---- 재시도 알림 제한은 종목·원인별
    def test_a_different_cause_or_market_alerts_at_once(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 1.05}
        self.executor(cfg, ex, bot_pids=[]).run()                                     # 11/10 둘 다 매수
        self.next_day()
        self.ratio = {BTC: 0.95, ETH: 1.05}                                           # 11/11 BTC 만 매도해야 한다
        ex.fail_side = "ask"
        self.sent.clear()
        self.clock += dt.timedelta(minutes=5)
        self.retry(cfg, ex)                                                           # 00:06 BTC 실패 → 알림 1
        self.clock += dt.timedelta(minutes=10)
        self.retry(cfg, ex)                                                           # 00:16 같은 실패 → 조용
        self.assertEqual(len(self.sent), 1)
        ex.fail_side = None
        ex.level_size = 0.0000001                                                     # 호가가 비었다 → 다른 원인
        self.clock += dt.timedelta(minutes=10)
        self.retry(cfg, ex)                                                           # 00:26 원인이 바뀌었다 → 바로 알림
        self.assertEqual(len(self.sent), 2)
        ex.level_size = 1000.0
        ex.fail_side = "ask"
        self.ratio = {BTC: 0.95, ETH: 0.95}                                           # ETH 도 매도 대상이 됐다
        self.clock += dt.timedelta(minutes=10)
        self.retry(cfg, ex)                                                           # 00:36 ETH 는 처음 실패 → 바로 알림
        self.assertEqual(len(self.sent), 3)
        self.assertIn("KRW-ETH", self.sent[-1])
        self.assertEqual(HX.retry_signature("slip 163bp worse than mid (limit 150bp)"), HX.retry_signature("slip 171bp worse than mid (limit 150bp)"))

    # ---- SHADOW 로 되돌아갔는데 LIVE 장부에 보유가 있으면 알린다
    def test_shadow_mode_reports_what_is_left_in_the_live_book(self):
        live, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.executor(live, ex, bot_pids=[]).run()                                    # LIVE 로 BTC 를 샀다
        self.next_day()
        self.sent.clear()
        shadow = self.cfg("SHADOW")                                                   # 설정 파일이 사라져 SHADOW 로 떨어진 다음 날
        self.executor(shadow, ex).run()
        self.assertIn("🚨 SHADOW 로 실행 중인데 LIVE 장부에 남은 것이 있음: BTC 0.", self.sent[-1])
        self.assertEqual(HX.live_book_holdings(self.cfg("SHADOW", H3_STATE_DIR=str(Path(self.tmp.name, "empty")))), [])


class TestOrderNumberPolicy(Base):
    """2026-11-14 고문 방침: 보낼 때마다 새 번호를 먼저 적는다 · 결과를 모르는 번호는 전부 조회해 있으면 그것만 반영한다 ·
    매수는 하루 한 번 · 매수 전 계좌와 장부 대조 · 팔 잔고가 없으면 하루 한 번 알리고 장부는 사람이 정리한다(--writeoff)."""

    def retry(self, cfg, ex, fetch=None):
        runner = HX.Executor(cfg, ex, fetch_candles=fetch or (lambda m, day: candles_for(m, day, self.ratio[m])),
                             notify=self.sent.append, now=lambda: self.clock, bot_pids=[], sleep=lambda s: None)
        return runner.run_sell_retry()

    def ledger(self, cfg):
        return [json.loads(x) for x in cfg.ledger_path.read_text(encoding="utf-8").splitlines()] if cfg.ledger_path.exists() else []

    def accepted_but_invisible(self, ex):
        """다음 주문 하나: 거래소에는 들어가 체결되는데 응답도 못 받고, 직후 조회에도 안 보인다."""
        ex.fail_after_accept = True
        ex.lookups = [True, False]                              # 보내기 전 조회 1번, 보낸 직후 조회 1번("없음")

    # ---- 번호는 보내기 전에 기록된다
    def test_the_number_is_on_disk_before_the_order_goes_out(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        real, seen = ex.place, []

        def spy(market, side, identifier, **kw):
            s = HX.load_state(cfg)["sleeves"][market]
            seen.append((identifier, s["pending"]["identifier"], [(r["id"], r["day"], r["side"], r["status"]) for r in s["sent"]]))
            return real(market, side, identifier, **kw)
        ex.place = spy
        self.executor(cfg, ex, bot_pids=[]).run()
        self.assertEqual(seen, [("h3-20261110-KRW-BTC-bid", "h3-20261110-KRW-BTC-bid",
                                 [("h3-20261110-KRW-BTC-bid", "2026-11-10", "bid", "open")])])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual([(r["id"], r["status"]) for r in s["sent"]], [("h3-20261110-KRW-BTC-bid", "applied")])

    # ---- 안 들어간 줄 알았던 매도가 들어가 있었다
    def test_a_sell_thought_lost_is_found_by_lookup_and_never_sold_twice(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)                                                         # 11/11, 신호 꺼짐
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        self.accepted_but_invisible(ex)
        rows, errors = self.executor(cfg, ex).run()                                   # 00:01: 주문은 체결됐는데 "안 들어감" 으로 보인다
        self.assertIn("not placed", errors[0])
        ex.fail_after_accept = False
        self.assertLess(ex.bal["BTC"], 1e-8)                                          # 실제로는 다 팔렸다
        placed = list(ex.placed)
        ex.lookups = [False, False]                                                   # 00:06: 여전히 조회에 안 보인다
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.retry(cfg, ex)
        self.assertEqual(ex.placed, placed)                                           # 팔 잔고가 없으니 새 주문은 나가지 않는다
        self.assertIn("🚨 BTC 매도 불가", errors[0])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertAlmostEqual(s["coin"], book, places=9)                             # 장부는 지우지 않았다
        self.assertEqual((s["pending"], s["sell_blocked"]), (None, "2026-11-11"))
        self.assertEqual([(r["id"], r["status"]) for r in s["sent"]], [("h3-20261111-KRW-BTC-ask", "open")])
        cash = s["cash"]
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.executor(cfg, ex).run()                                   # 00:11: 이제 조회에 보인다 → 그 체결만 반영
        self.assertEqual((errors, ex.placed), ([], placed))
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertLess(s["coin"], 1e-8)
        self.assertGreater(s["cash"], cash + 900000)                                  # 매도 대금이 한 번만 들어왔다
        self.assertEqual([(r["id"], r["status"]) for r in s["sent"]], [("h3-20261111-KRW-BTC-ask", "applied")])
        self.assertIn("🚨 BTC: 안 들어간 줄 알았던 매도 주문(h3-20261111-KRW-BTC-ask)이 체결돼 있었음", self.sent[-1])
        self.assertEqual([(r["action"], r["note"]) for r in self.ledger(cfg)][-2:],
                         [("SELL", "late fill found by lookup (h3-20261111-KRW-BTC-ask)"), ("STAY_IN_CASH", None)])
        again = s["cash"]
        self.next_day()
        self.executor(cfg, ex).run()                                                  # 다음 날 다시 돌아도 같은 체결을 또 더하지 않는다
        self.assertEqual((HX.load_state(cfg)["sleeves"][BTC]["cash"], ex.placed), (again, placed))

    def test_a_run_that_reflects_an_earlier_fill_sends_no_new_order(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)                                                         # 11/11, 신호 꺼짐
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        ex.sell_ratio = 0.4
        self.accepted_but_invisible(ex)
        rows, errors = self.executor(cfg, ex).run()                                   # 00:01: 40%가 팔렸는데 "안 들어감" 으로 보인다
        self.assertIn("not placed", errors[0])
        ex.fail_after_accept, ex.sell_ratio = False, 1.0
        placed = list(ex.placed)
        ex.lookups = [False]                                                          # 00:06: 미처리 주문 조회에는 안 보이고, 번호 목록 조회에는 보인다
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.retry(cfg, ex)
        self.assertEqual((rows, errors, ex.placed), ([], [], placed))                 # 그 체결만 반영하고 새 주문은 내지 않았다
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertAlmostEqual(s["coin"], book * 0.6, places=7)
        self.assertEqual(s["last_day"], "2026-11-10")
        self.assertIn("안 들어간 줄 알았던 매도 주문", self.sent[-1])
        self.assertIn("BTC: 지난 주문의 체결을 반영했으므로 이번 실행에서는 새 매도 주문을 내지 않음", self.sent[-1])
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.retry(cfg, ex)                                            # 00:16: 남은 60%를 새 번호로 판다
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed, placed + ["h3-20261111-KRW-BTC-ask-r1"])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertLess(s["coin"], 1e-8)
        self.assertAlmostEqual(s["coin"], ex.bal["BTC"], places=7)                    # 장부 = 계좌

    # ---- 안 들어간 줄 알았던 매수가 들어가 있었다
    def test_a_buy_thought_lost_is_found_the_next_day_and_never_bought_twice(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.accepted_but_invisible(ex)
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()                      # 00:01: 매수는 체결됐는데 "안 들어감" 으로 보인다
        self.assertIn("not placed", errors[0])
        ex.fail_after_accept = False
        self.assertGreater(ex.bal["BTC"], 0)
        ex.lookups = [False, False]                                                   # 00:11: 여전히 안 보인다
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()
        self.assertIn("매수 건너뜀", errors[0])                                       # 매수는 하루 한 번만 보낸다
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid"])
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], 0.0)
        self.next_day()                                                               # 다음 날: 조회에 보인다
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()
        self.assertEqual((errors, ex.placed), ([], ["h3-20261110-KRW-BTC-bid"]))      # 새로 사지 않고 그 체결만 반영했다
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertAlmostEqual(s["coin"], ex.bal["BTC"], places=9)
        self.assertLess(s["cash"], 2.0)
        self.assertEqual([(r["market"], r["action"]) for r in rows], [(BTC, "KEEP_HOLDING"), (ETH, "STAY_IN_CASH")])
        self.assertIn("🚨 BTC: 안 들어간 줄 알았던 매수 주문(h3-20261110-KRW-BTC-bid)이 체결돼 있었음", self.sent[-1])
        self.assertEqual(s["sent"], [])                                               # 결과가 정해진 지난 번호는 목록에서 빠진다

    def test_a_buy_is_refused_when_the_account_holds_more_than_the_book(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        ex.bal["BTC"] = 0.001                                                         # 장부에는 없는 코인 약 10만 원어치
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()
        self.assertEqual(ex.placed, [])
        self.assertIn("BTC 매수 거부: 계좌에는 0.00100000개가 있는데 장부에는 0.00000000개", errors[0])
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC].get("sent") or [], [])    # 보내지 않았으니 번호도 쓰지 않았다
        ex.bal["BTC"] = 0.00000050                                                    # 먼지(최소 주문 금액 미만)는 막지 않는다
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()
        self.assertEqual((errors, ex.placed), ([], ["h3-20261110-KRW-BTC-bid"]))

    # ---- 팔 수 있는 잔고가 없다
    def test_no_sellable_balance_alerts_once_a_day_and_keeps_the_book(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)                                                         # 11/11, 신호 꺼짐
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        ex.locked["BTC"], ex.bal["BTC"] = ex.bal["BTC"], 0.0                          # 전부 다른 주문에 묶였다
        placed = list(ex.placed)
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(len(errors), 1)
        self.assertIn("🚨 BTC 매도 불가: 장부에는 %.8f개가 있는데 계좌에서 팔 수 있는 수량은 0.00000000개 (다른 주문에 묶인 수량 %.8f개)" % (book, book), errors[0])
        self.assertNotIn("SELL FAILED", errors[0])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["coin"], s["pending"], s["last_day"], s["sell_blocked"]), (book, None, "2026-11-10", "2026-11-11"))
        self.assertEqual([r for r in s.get("sent") or [] if r["side"] == "ask"], [])  # 주문을 내지 않았으니 번호도 쓰지 않았다
        sent = len(self.sent)
        self.clock += dt.timedelta(minutes=5)
        self.assertEqual(self.retry(cfg, Untouchable(), lambda m, d: self.fail("must not fetch")), ([], []))    # 재시도는 조회도 하지 않는다
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.executor(cfg, ex).run()                                   # 00:11 실행도 조용하다
        self.assertEqual((rows, errors, ex.placed, len(self.sent)), ([], [], placed, sent))
        self.next_day()
        rows, errors = self.executor(cfg, ex).run()                                   # 다음 날 다시 한 번 알린다
        self.assertIn("🚨 BTC 매도 불가", errors[0])
        self.assertEqual((ex.placed, len(self.sent)), (placed, sent + 1))
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], book)
        ex.bal["BTC"], ex.locked["BTC"] = book, 0.0                                   # 묶였던 주문이 풀렸다
        self.next_day()
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(errors, [])                                                  # 그대로 판다
        self.assertLess(HX.load_state(cfg)["sleeves"][BTC]["coin"], 1e-8)

    # ---- 장부 정리 명령
    def test_writeoff_shows_first_and_changes_only_when_confirmed(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        before = len(self.ledger(cfg))
        changed, lines = HX.writeoff(cfg, ex, BTC, False)
        self.assertEqual((changed, lines[-1]), (False, "차이가 없습니다. 바꿀 것이 없습니다."))
        ex.bal["BTC"] = book * 0.25                                                   # 계좌에 4분의 1만 남았다
        changed, lines = HX.writeoff(cfg, ex, BTC, False)                             # 보여 주기만
        self.assertFalse(changed)
        self.assertIn("장부에서 지울 수량 %.8f BTC" % (book * 0.75), lines[1])
        self.assertIn("--yes", lines[-1])
        self.assertEqual((HX.load_state(cfg)["sleeves"][BTC]["coin"], len(self.ledger(cfg))), (book, before))
        ex.locked["BTC"] = book * 0.75                                                # 사라진 게 아니라 다른 주문에 묶여 있다 → 거부
        with self.assertRaises(HX.H3Error) as ctx:
            HX.writeoff(cfg, ex, BTC, True)
        self.assertIn("locked in an open order", str(ctx.exception))
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], book)
        ex.locked["BTC"] = 0.0
        state = HX.load_state(cfg)
        state["sleeves"][BTC]["pending"] = {"day": "2026-11-11", "side": "ask", "identifier": "x", "volume": 1.0}
        HX.save_state(cfg, state)
        with self.assertRaises(HX.H3Error):                                           # 미처리 주문이 있으면 그것부터
            HX.writeoff(cfg, ex, BTC, True)
        state["sleeves"][BTC]["pending"] = None
        state["sleeves"][BTC]["sell_blocked"] = "2026-11-11"
        HX.save_state(cfg, state)
        changed, lines = HX.writeoff(cfg, ex, BTC, True, now=lambda: self.clock)
        self.assertTrue(changed)
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["coin"], s["sell_blocked"]), (book * 0.25, None))
        row = self.ledger(cfg)[-1]
        self.assertEqual((row["action"], row["coin_before"], row["coin"], row["market"]), ("WRITEOFF", book, book * 0.25, BTC))
        self.assertEqual(ex.placed, ["h3-20261110-KRW-BTC-bid"])                      # 주문은 내지 않는다
        with self.assertRaises(HX.H3Error):
            HX.writeoff(self.cfg("SHADOW"), ex, BTC, True)                            # LIVE 장부 전용
        with self.assertRaises(HX.H3Error):
            HX.writeoff(cfg, ex, "KRW-XRP", True)

    def test_writeoff_command_line(self):
        ex = FakeExchange()
        live = dict(H3_MODE="LIVE", H3_CAPITAL_KRW="2000000", UPBIT_ACCESS_KEY="ak", UPBIT_SECRET_KEY="sk")
        cfg = self.cfg("LIVE")
        self.holding(cfg, ex)
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        ex.bal["BTC"] = 0.0
        real = HX.Upbit
        HX.Upbit = lambda access, secret: ex
        try:
            code, out, sent = self.run_main(["--writeoff", "krw-btc"], **live)
            self.assertEqual((code, sent), (0, []))                                   # 보여 주기만 — 알림 없음
            self.assertIn("장부에서 지울 수량", out)
            self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], book)
            code, out, sent = self.run_main(["--writeoff", "KRW-BTC", "--yes"], **live)
            self.assertEqual(code, 0)
            self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], 0.0)
            self.assertEqual(len(sent), 1)
            self.assertIn("H3 LIVE 장부 정리(수동): 장부에서 지울 수량", sent[0][2])
            code, out, sent = self.run_main(["--writeoff", "KRW-BTC", "--yes"])       # SHADOW(기본) 에서는 거부
            self.assertEqual((code, sent), (1, []))
            self.assertIn("writeoff refused", out)
        finally:
            HX.Upbit = real

    # ---- 끝내 안 보이는 번호는 며칠 뒤 목록에서 지운다
    def test_a_number_that_never_shows_up_is_dropped_after_a_few_days(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        ex.fail_before_accept = True
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.executor(cfg, ex, bot_pids=[]).run()                                     # 11/10 매수가 안 들어감
        ex.fail_before_accept = False
        self.ratio = {BTC: 0.95, ETH: 0.95}                                           # 그 뒤로는 신호 꺼짐 → 주문 없음
        for k in range(HX.SENT_KEEP_DAYS):
            self.next_day()
            self.executor(cfg, ex, bot_pids=[]).run()
            self.assertEqual([r["id"] for r in HX.load_state(cfg)["sleeves"][BTC]["sent"]], ["h3-20261110-KRW-BTC-bid"])
        self.next_day()
        self.executor(cfg, ex, bot_pids=[]).run()
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["sent"], [])
        self.assertEqual(ex.placed, [])

    def test_status_shows_unknown_numbers_and_the_blocked_day(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        ex.fail_before_accept = True
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.executor(cfg, ex, bot_pids=[]).run()
        text = HX.status_text(cfg)
        self.assertIn("KRW-BTC cash=1,000,000 coin=0.00000000 last_day=None pending=yes unknown_orders=1 sell_blocked=no", text)
        self.assertIn("KRW-ETH cash=1,000,000 coin=0.00000000 last_day=2026-11-10 pending=no unknown_orders=0 sell_blocked=no", text)


class TestIndependentReview(Base):
    """2026-11-14 독립 검토(#598)에서 나온 것: 옛 상태 파일의 번호, 이미 있던 번호, 거절된 주문, 알림 전에 적힌 표시,
    묻히는 알림, 결과를 모르는 번호가 있을 때의 장부 정리, 일부만 붙은 체결 내역, 보유 중 계좌 대조, 점검 주문 복구."""

    def runner(self, cfg, ex, notify=None):
        return HX.Executor(cfg, ex, fetch_candles=lambda m, day: candles_for(m, day, self.ratio[m]),
                           notify=notify or self.sent.append, now=lambda: self.clock, bot_pids=[], sleep=lambda s: None)

    def retry(self, cfg, ex, notify=None):
        return self.runner(cfg, ex, notify).run_sell_retry()

    def sent_of(self, cfg, market=BTC):
        return [(r["id"], r["status"]) for r in HX.load_state(cfg)["sleeves"][market].get("sent") or []]

    # ---- 1) 옛 버전의 상태 파일(attempt 카운터)이 그날 것이어도 번호를 되풀이하지 않는다
    def test_an_old_state_file_with_todays_attempt_counter_never_repeats_a_number(self):
        day = "2026-11-11"
        self.assertEqual(HX.order_identifier({"attempt": {"day": day, "n": 2}}, day, BTC, "bid"), "h3-20261111-KRW-BTC-bid-r3")
        self.assertEqual(HX.order_identifier({"attempt": {"day": "2026-11-10", "n": 2}}, day, BTC, "bid"), "h3-20261111-KRW-BTC-bid")
        taken = {"sent": [{"id": "h3-20261111-KRW-BTC-ask-r1", "day": "2026-11-10", "side": "ask", "status": "open"}]}
        taken["sent"].append({"id": "h3-20261111-KRW-BTC-ask", "day": day, "side": "ask", "status": "nofill"})
        self.assertEqual(HX.order_identifier(taken, day, BTC, "ask"), "h3-20261111-KRW-BTC-ask-r2")     # 적어 둔 번호와 겹치면 건너뛴다
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)                                                         # 11/11, 신호 꺼짐
        state = HX.load_state(cfg)
        s = state["sleeves"][BTC]
        book, volume = s["coin"], float(HX.fmt_volume(s["coin"]))
        s.pop("sent", None)                                                           # 옛 버전이 쓴 파일: sent 없음, attempt 와 pending(-r1)
        s["attempt"] = {"day": day, "n": 1}
        s["pending"] = {"day": day, "side": "ask", "identifier": "h3-20261111-KRW-BTC-ask-r1", "volume": volume, "writeoff": None,
                        "signal": {"close": 0.95e8, "average": 1.0e8, "on": False}}
        HX.save_state(cfg, state)
        ex.sell_ratio = 0.4
        ex.place(BTC, "ask", "h3-20261111-KRW-BTC-ask-r1", volume=volume)             # 옛 버전이 낸 주문: 40%만 체결
        ex.sell_ratio = 1.0
        rows, errors = self.executor(cfg, ex).run()                                   # 새 버전의 첫 실행: 그 체결을 한 번 반영한다
        self.assertEqual(errors, [])
        self.assertAlmostEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], book * 0.6, places=7)
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.executor(cfg, ex).run()                                   # 남은 것을 한 번도 쓰지 않은 번호로 판다
        self.assertEqual(errors, [])
        self.assertEqual(ex.placed[-1], "h3-20261111-KRW-BTC-ask-r3")
        self.assertEqual(len(set(ex.placed)), len(ex.placed))
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertLess(s["coin"], 1e-8)
        self.assertAlmostEqual(s["coin"], ex.bal["BTC"], places=7)                    # 같은 체결을 두 번 더하지 않았다(장부 = 계좌)

    # ---- 1) 보내기 전에 이미 있던 번호의 주문은 내 것으로 받아들이지 않는다
    def test_an_order_that_already_exists_under_the_new_number_is_not_adopted(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        cash = HX.load_state(cfg)["sleeves"][BTC]["cash"]
        ex.sell_ratio = 0.5
        ex.place(BTC, "ask", "h3-20261111-KRW-BTC-ask", volume=float(HX.fmt_volume(book)))   # 장부가 모르는 주문이 그 번호로 이미 있다
        ex.sell_ratio = 1.0
        placed = list(ex.placed)
        rows, errors = self.executor(cfg, ex).run()
        self.assertIn("already exists at the exchange before sending", errors[0])
        self.assertEqual(ex.placed, placed)                                           # 보내지 않았다
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["coin"], s["cash"], s["pending"]), (book, cash, None))    # 그 주문의 체결을 장부에 넣지 않았다
        self.assertEqual(self.sent_of(cfg), [("h3-20261111-KRW-BTC-ask", "conflict")])
        self.clock += dt.timedelta(minutes=10)
        self.executor(cfg, ex).run()
        self.assertEqual(ex.placed[-1], "h3-20261111-KRW-BTC-ask-r1")                 # 다음에는 다른 번호를 쓴다
        self.assertEqual(self.sent_of(cfg)[0], ("h3-20261111-KRW-BTC-ask", "conflict"))   # 겹친 번호는 뒤에도 조회해 반영하지 않는다

    def test_a_rejected_post_is_final_and_a_lookup_cannot_turn_it_into_a_fill(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        ex.reject_code = 400                                                          # 거래소가 요청을 거절했다 → 주문은 만들어지지 않았다
        rows, errors = self.executor(cfg, ex).run()
        self.assertIn("SELL FAILED", errors[0])
        self.assertIn("not placed", errors[0])
        self.assertEqual(self.sent_of(cfg), [("h3-20261111-KRW-BTC-ask", "rejected")])
        self.assertIsNone(HX.load_state(cfg)["sleeves"][BTC]["pending"])
        ex.reject_code = None
        real, asked = ex.order, []
        ex.order = lambda ident: (asked.append(ident), real(ident))[1]
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual((errors, ex.placed[-1]), ([], "h3-20261111-KRW-BTC-ask-r1"))
        self.assertNotIn("h3-20261111-KRW-BTC-ask", asked)                            # 거절된 번호는 다시 조회하지 않는다 (목록이 불어나지 않는다)
        ex.order = real
        # 번호가 겹쳐 거절됐는데 조회에는 그 번호의 옛 주문이 보이는 경우 (보내기 전 조회가 놓쳤다)
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = dt.datetime(2026, 11, 10, 0, 1, 0, tzinfo=dt.timezone.utc)
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        ex.sell_ratio = 0.5
        ex.place(BTC, "ask", "h3-20261111-KRW-BTC-ask", volume=float(HX.fmt_volume(book)))
        ex.sell_ratio = 1.0
        ex.lookups = [False]                                                          # 보내기 전 조회에는 "없음"
        rows, errors = self.executor(cfg, ex).run()
        self.assertIn("not placed", errors[0])
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], book)            # 옛 주문의 체결을 받아들이지 않았다
        self.assertEqual(self.sent_of(cfg), [("h3-20261111-KRW-BTC-ask", "rejected")])

    def test_a_failed_lookup_before_sending_does_not_use_up_the_days_buy(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        real = ex.order

        def down(identifier):
            raise OSError("timed out")                                                # 보내기 전 조회가 실패한다 → 주문은 나가지 않았다
        ex.order = down
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()
        self.assertEqual((len(errors), ex.placed), (1, []))
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s.get("pending"), s.get("sent") or []), (None, []))         # 보낸 것이 없으니 번호도 적지 않았다
        ex.order = real
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()                      # 다음 실행이 그날의 매수를 한다
        self.assertEqual((errors, ex.placed), ([], ["h3-20261110-KRW-BTC-bid"]))

    def test_a_rejected_buy_is_still_the_one_buy_of_the_day(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        ex.reject_code = 400
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()
        self.assertIn("not placed", errors[0])
        ex.reject_code = None
        rows, errors = self.executor(cfg, ex, bot_pids=[]).run()
        self.assertIn("매수 건너뜀", errors[0])
        self.assertEqual(ex.placed, [])

    # ---- 2)·9) '오늘 알렸음' 은 알림이 실제로 나간 뒤에 적는다
    def test_the_blocked_day_is_recorded_only_after_the_alert_went_out(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        ex.bal["BTC"] = 0.0
        delivered = [False]
        notify = lambda text: (self.sent.append(text), delivered[0])[1]
        sent = len(self.sent)
        rows, errors = self.runner(cfg, ex, notify).run()                             # 00:01: 텔레그램 전송 실패
        self.assertIn("매도 불가", errors[0])
        self.assertIsNone(HX.load_state(cfg)["sleeves"][BTC].get("sell_blocked"))     # 알림이 안 나갔으니 '알렸음' 도 없다
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.retry(cfg, ex, notify)                                    # 00:06: 다시 알리려 한다
        self.assertIn("매도 불가", errors[0])
        self.assertEqual(len(self.sent), sent + 2)
        delivered[0] = True
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.retry(cfg, ex, notify)                                    # 00:16: 같은 문구라도 한 시간 제한에 묻히지 않는다
        self.assertEqual(len(self.sent), sent + 3)
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["sell_blocked"], "2026-11-11")
        self.clock += dt.timedelta(minutes=10)
        self.assertEqual(self.retry(cfg, Untouchable(), notify), ([], []))            # 알린 뒤에는 조용하다
        self.assertEqual(len(self.sent), sent + 3)

    def test_a_run_killed_before_its_alert_does_not_silence_the_day(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 1.05}
        self.executor(cfg, ex, bot_pids=[]).run()                                     # 11/10 둘 다 매수
        self.next_day()
        self.ratio = {BTC: 0.95, ETH: 0.95}
        ex.bal["BTC"] = 0.0                                                           # BTC 는 팔 잔고가 없다
        real = ex.place

        def killed(market, side, identifier, **kw):
            raise KeyboardInterrupt()                                                 # ETH 주문 도중 프로세스가 죽는다 (알림을 보내기 전)
        ex.place = killed
        sent = len(self.sent)
        with self.assertRaises(KeyboardInterrupt):
            self.executor(cfg, ex).run()
        self.assertEqual(len(self.sent), sent)
        self.assertIsNone(HX.load_state(cfg)["sleeves"][BTC].get("sell_blocked"))     # 알리지 못했으니 '알렸음' 도 없다
        ex.place = real
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.retry(cfg, ex)                                            # 다음 실행이 알린다
        self.assertTrue(any("BTC 매도 불가" in e for e in errors))
        self.assertEqual(len(self.sent), sent + 1)

    def test_the_retry_still_looks_for_an_unknown_sell_after_reporting_nothing_sellable(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        ex.fail_after_accept, ex.lookups = True, [True, False]                        # 매도가 다 체결됐는데 "안 들어감" 으로 보인다
        self.executor(cfg, ex).run()                                                  # 00:01
        ex.fail_after_accept = False
        ex.lookups = [False, False]
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.retry(cfg, ex)                                            # 00:06: 여전히 안 보이고 팔 잔고는 없다 → 매도 불가 알림
        self.assertIn("매도 불가", errors[0])
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["sell_blocked"], "2026-11-11")
        placed, sent = list(ex.placed), len(self.sent)
        ex.lookups = [False]
        self.clock += dt.timedelta(minutes=10)
        self.assertEqual(self.retry(cfg, ex), ([], []))                               # 00:16: 아직 안 보인다 → 주문도 알림도 없다
        self.assertEqual((ex.placed, len(self.sent)), (placed, sent))
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.retry(cfg, ex)                                            # 00:26: 보인다 → 그 체결을 반영하고 알린다
        self.assertEqual((errors, ex.placed), ([], placed))
        self.assertIn("안 들어간 줄 알았던 매도 주문", self.sent[-1])
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertLess(s["coin"], 1e-8)
        self.assertGreater(s["cash"], 900000)

    def test_a_long_lookup_of_unknown_numbers_stops_with_an_alert_instead_of_a_timeout(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        state = HX.load_state(cfg)
        state["sleeves"][BTC]["sent"] = [{"id": "h3-20261111-KRW-BTC-ask-r%d" % k, "day": "2026-11-11", "side": "ask", "status": "open"}
                                         for k in range(1, 6)]
        HX.save_state(cfg, state)
        real, ticks = HX.time.monotonic, [0.0]

        def slow():
            ticks[0] += 40.0                                                          # 조회 한 번에 40초씩 걸린다
            return ticks[0]
        HX.time.monotonic = slow
        try:
            placed = list(ex.placed)
            rows, errors = self.executor(cfg, ex).run()
        finally:
            HX.time.monotonic = real
        self.assertTrue(any("unknown order numbers took more than" in e for e in errors))
        self.assertEqual(ex.placed, placed)                                           # 다 조회하지 못했으면 새 주문을 내지 않는다
        self.assertIn("KRW-BTC: KRW-BTC: looking up 5 unknown order numbers", self.sent[-1])    # 조용히 죽지 않고 알린다

    def test_the_hourly_limit_does_not_swallow_the_next_days_blocked_alert(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        ex.bal["BTC"] = 0.0
        sent = len(self.sent)
        self.clock = dt.datetime(2026, 11, 11, 23, 16, 0, tzinfo=dt.timezone.utc)
        self.retry(cfg, ex)                                                           # 23:16 재시도가 알렸다
        self.assertEqual((len(self.sent), HX.load_state(cfg)["sleeves"][BTC]["sell_blocked"]), (sent + 1, "2026-11-11"))
        self.clock = dt.datetime(2026, 11, 12, 0, 6, 0, tzinfo=dt.timezone.utc)
        rows, errors = self.retry(cfg, ex)                                            # 다음 날 00:06, 하루 실행보다 먼저 닿았다 (50분 뒤, 같은 문구)
        self.assertIn("매도 불가", errors[0])
        self.assertEqual((len(self.sent), HX.load_state(cfg)["sleeves"][BTC]["sell_blocked"]), (sent + 2, "2026-11-12"))

    def test_a_short_sale_is_alerted_again_if_the_first_alert_did_not_go_out(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        ex.bal["BTC"] *= 0.5
        delivered = [False]
        notify = lambda text: (self.sent.append(text), delivered[0])[1]
        rows, errors = self.runner(cfg, ex, notify).run()                             # 있는 만큼 팔았는데 알림 전송이 실패했다
        self.assertEqual(errors, [])
        self.assertIsNone(HX.load_state(cfg)["sleeves"][BTC].get("sell_blocked"))
        delivered[0] = True
        self.clock += dt.timedelta(minutes=5)
        rows, errors = self.retry(cfg, ex, notify)                                    # 다음 실행이 '매도 불가' 로 다시 알린다
        self.assertIn("매도 불가", errors[0])
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["sell_blocked"], "2026-11-11")

    # ---- 4) 한 번만 나오는 알림은 다른 종목의 실패가 조용한 시간이어도 보낸다
    def test_a_one_time_alert_is_sent_even_while_another_markets_error_is_quiet(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 1.05}
        self.executor(cfg, ex, bot_pids=[]).run()                                     # 11/10 둘 다 매수
        self.next_day()
        self.ratio = {BTC: 0.95, ETH: 0.95}                                           # 11/11 둘 다 매도해야 한다
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        ex.fail_markets = {ETH}
        ex.sell_ratio, ex.fail_after_accept, ex.lookups = 0.5, True, [True, False]    # BTC: 절반이 팔렸는데 "안 들어감" 으로 보인다
        self.sent.clear()
        self.executor(cfg, ex).run()                                                  # 00:01
        ex.sell_ratio, ex.fail_after_accept = 1.0, False
        ex.fail_markets, ex.lookups = {BTC, ETH}, [False, False]
        self.clock += dt.timedelta(minutes=5)
        self.retry(cfg, ex)                                                           # 00:06: BTC 새 번호도 실패, ETH 실패 → 알림
        self.assertEqual(len(self.sent), 2)
        ex.fail_markets = {ETH}
        self.clock += dt.timedelta(minutes=10)
        rows, errors = self.retry(cfg, ex)                                            # 00:16: BTC 의 첫 주문이 조회에 보인다. ETH 는 같은 실패(조용한 시간)
        self.assertEqual((rows, len(errors)), ([], 1))
        self.assertEqual(len(self.sent), 3)                                           # 그래도 BTC 의 알림은 나간다
        self.assertIn("안 들어간 줄 알았던 매도 주문(h3-20261111-KRW-BTC-ask)이 체결돼 있었음", self.sent[-1])
        self.assertAlmostEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], book * 0.5, places=7)
        self.clock += dt.timedelta(minutes=10)
        self.retry(cfg, ex)                                                           # 00:26: BTC 는 나머지를 판다
        self.assertLess(HX.load_state(cfg)["sleeves"][BTC]["coin"], 1e-8)

    # ---- 3)·10) 장부 정리
    def test_writeoff_waits_until_unknown_numbers_are_resolved(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.holding(cfg, ex)
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        ex.sell_ratio, ex.fail_after_accept, ex.lookups = 0.6, True, [True, False]    # 60%가 팔렸는데 "안 들어감" 으로 보인다
        self.executor(cfg, ex).run()
        ex.sell_ratio, ex.fail_after_accept = 1.0, False
        ex.lookups = [False] * 4                                                      # 조회는 계속 "없음"
        ex.no_fill = True
        self.clock += dt.timedelta(minutes=5)
        self.retry(cfg, ex)                                                           # 새 번호는 체결 없이 끝났다 → 미처리 주문은 없다
        ex.no_fill = False
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertEqual((s["pending"], s["coin"]), (None, book))
        self.assertIn(("h3-20261111-KRW-BTC-ask", "open"), self.sent_of(cfg))
        ex.lookups = None
        changed, lines = HX.writeoff(cfg, ex, BTC, False)                             # 보여 주기는 한다
        self.assertFalse(changed)
        self.assertTrue(any("결과를 모르는 주문 번호 1개" in ln for ln in lines))
        with self.assertRaises(HX.H3Error) as ctx:                                    # 바꾸는 것은 거부한다
            HX.writeoff(cfg, ex, BTC, True)
        self.assertIn("still unknown", str(ctx.exception))
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], book)
        self.clock += dt.timedelta(minutes=5)
        self.executor(cfg, ex).run()                                                  # 00:11: 첫 주문이 조회에 보인다 → 그 체결을 반영
        s = HX.load_state(cfg)["sleeves"][BTC]
        self.assertAlmostEqual(s["coin"], book * 0.4, places=7)
        self.assertAlmostEqual(s["coin"], ex.bal["BTC"], places=7)                    # 장부 = 계좌. 안 팔린 40%가 장부에서 사라지지 않았다

    def test_writeoff_does_not_say_refused_after_it_changed_the_book(self):
        ex = FakeExchange()
        cfg = self.cfg("LIVE")
        self.holding(cfg, ex)
        ex.bal["BTC"] = 0.0
        real_ledger, real_upbit = HX.append_ledger, HX.Upbit

        def full(c, row):
            raise OSError("No space left on device")
        HX.append_ledger, HX.Upbit = full, (lambda access, secret: ex)
        try:
            code, out, sent = self.run_main(["--writeoff", "KRW-BTC", "--yes"], H3_MODE="LIVE", H3_CAPITAL_KRW="2000000",
                                            UPBIT_ACCESS_KEY="ak", UPBIT_SECRET_KEY="sk")
        finally:
            HX.append_ledger, HX.Upbit = real_ledger, real_upbit
        self.assertEqual(code, 0)
        self.assertNotIn("refused", out)
        self.assertIn("장부를 고쳤습니다", out)
        self.assertIn("남기지 못했습니다", out)
        self.assertEqual(len(sent), 1)                                                # 바뀐 사실은 알린다
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], 0.0)

    # ---- 8) 체결 내역이 일부만 붙은 응답
    def test_a_trades_list_shorter_than_the_executed_volume_is_not_final(self):
        trade = {"price": "100000000", "volume": "0.01", "funds": "1000000"}
        base = {"uuid": "u", "state": "done", "trades": [trade], "paid_fee": "500"}
        with self.assertRaises(HX.H3Error) as ctx:
            HX.parse_fill(dict(base, executed_volume="0.02"))
        self.assertNotIsInstance(ctx.exception, HX.NoFill)
        self.assertIn("trades are not listed yet", str(ctx.exception))
        self.assertEqual(HX.parse_fill(dict(base, executed_volume="0.01"))["volume"], 0.01)
        self.assertEqual(HX.parse_fill(dict(base, executed_volume="0.0100000001"))["volume"], 0.01)   # 표기 차이는 넘어간다
        self.assertEqual(HX.parse_fill(base)["volume"], 0.01)                         # 그 칸이 없으면 내역대로

    # ---- 7) 보유 유지인 날에도 계좌를 한 번 본다
    def test_coins_missing_from_the_account_are_reported_on_a_keep_holding_day(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        self.ratio = {BTC: 1.05, ETH: 0.95}
        self.executor(cfg, ex, bot_pids=[]).run()                                     # 11/10 매수
        book = HX.load_state(cfg)["sleeves"][BTC]["coin"]
        self.next_day()
        self.sent.clear()
        rows, errors = self.executor(cfg, ex).run()                                   # 11/11 보유 유지, 계좌와 같다
        self.assertFalse(any("🚨" in line for line in self.sent))
        self.next_day()
        ex.locked["BTC"], ex.bal["BTC"] = book, 0.0                                   # 11/12 누군가 전부 지정가 매도에 걸어 두었다
        rows, errors = self.executor(cfg, ex).run()
        self.assertEqual(([(r["market"], r["action"]) for r in rows], errors), ([(BTC, "KEEP_HOLDING"), (ETH, "STAY_IN_CASH")], []))
        self.assertIn("🚨 BTC: 장부에는 %.8f개를 들고 있는데 계좌에서 팔 수 있는 수량은 0.00000000개 (다른 주문에 묶인 수량 %.8f개)" % (book, book), self.sent[-1])
        self.assertEqual(HX.load_state(cfg)["sleeves"][BTC]["coin"], book)            # 장부는 바꾸지 않는다
        sent = len(self.sent)
        self.executor(cfg, ex).run()                                                  # 같은 날 다시 돌면 조용하다
        self.assertEqual(len(self.sent), sent)
        self.next_day()

        def down(currency):
            raise HX.H3Error("upbit GET /v1/accounts -> 500")
        ex.account = down
        rows, errors = self.executor(cfg, ex).run()                                   # 계좌를 못 봐도 그날 판단은 기록한다
        self.assertEqual(([r["action"] for r in rows if r["market"] == BTC], errors), (["KEEP_HOLDING"], []))
        self.assertIn("BTC: 계좌 대조를 하지 못함", self.sent[-1])

    # ---- 5) 점검 주문 복구
    def test_smoke_recovery_sells_under_a_new_number_that_is_saved_first(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        ex.fail_side = "ask"
        with self.assertRaises(HX.H3Error):
            HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock, sleep=lambda s: None)
        ex.fail_side = None
        real, seen = ex.place, []

        def spy(market, side, identifier, **kw):
            seen.append((identifier, json.loads(cfg.smoke_path.read_text(encoding="utf-8")).get("sell_ids")))
            return real(market, side, identifier, **kw)
        ex.place = spy
        later = self.clock + dt.timedelta(minutes=30)
        res = HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: later, sleep=lambda s: None)
        self.assertEqual(seen, [("h3-smoke-20261110003100-ask", ["h3-smoke-20261110000100-ask", "h3-smoke-20261110003100-ask"])])
        self.assertTrue(res["recovered"])
        self.assertLess(abs(ex.bal["BTC"]), 1e-8)
        self.assertFalse(cfg.smoke_path.exists())

    def test_smoke_recovery_keeps_its_record_while_the_buy_may_only_be_invisible(self):
        cfg, ex = self.cfg("LIVE"), FakeExchange()
        ex.fail_after_accept, ex.lookups = True, [True, False]                        # 매수는 체결됐는데 "안 들어감" 으로 보인다
        with self.assertRaises(HX.H3Error):
            HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock, sleep=lambda s: None)
        ex.fail_after_accept = False
        self.assertGreater(ex.bal["BTC"], 0)
        ex.lookups = [False]                                                          # 복구 때도 매수 주문이 조회에 안 보인다
        with self.assertRaises(HX.H3Error) as ctx:
            HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: self.clock, sleep=lambda s: None)
        self.assertIn("not visible", str(ctx.exception))
        self.assertTrue(cfg.smoke_path.exists())                                      # 기록을 지우지 않았다
        later = self.clock + dt.timedelta(minutes=10)
        res = HX.smoke_test(cfg, ex, bot_pids=[], now=lambda: later, sleep=lambda s: None)    # 보이게 되면 판다
        self.assertTrue(res["recovered"])
        self.assertLess(abs(ex.bal["BTC"]), 1e-8)
        self.assertFalse(cfg.smoke_path.exists())

    # ---- 텔레그램 전송 결과
    def test_the_sender_reports_whether_anything_was_delivered(self):
        real = HX.urllib.request.urlopen
        calls = []

        class Ok(object):
            def read(self):
                return b"{}"

        def flaky(req, timeout=None):
            calls.append(1)
            if len(calls) % 2:
                raise TimeoutError("https://api.telegram.org/botSECRET/sendMessage timed out")
            return Ok()

        def dead(req, timeout=None):
            raise TimeoutError("https://api.telegram.org/botSECRET/sendMessage timed out")
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertTrue(HX.telegram_to("", [])("x"))                          # 보낼 곳이 없으면 실패가 아니다
                HX.urllib.request.urlopen = flaky
                self.assertTrue(HX.telegram_to("SECRET", ["1", "2"])("x"))            # 한 곳이라도 받으면 전달
                HX.urllib.request.urlopen = dead
                self.assertFalse(HX.telegram_to("SECRET", ["1", "2"])("x"))
            self.assertNotIn("SECRET", buf.getvalue())                                # 토큰이 든 주소는 찍지 않는다
        finally:
            HX.urllib.request.urlopen = real


if __name__ == "__main__":
    unittest.main(verbosity=2)
