# -*- coding: utf-8 -*-
"""h3_executor 무작위 장애 검사 — 여러 날에 걸쳐 주문 경로를 끊고 실패시키면서 지켜야 할 것이 지켜지는지 본다.

2026-11-14 독립 검토에서 만든 검사를 저장소에 보존한 것이다(제3세션 요청). 같은 seed 는 언제나 같은 결과를 낸다.

넣는 장애
  · 프로세스 강제 종료: 상태 파일을 쓰기 전·후, 장부 기록 전, 주문 전송 전·후, 조회·잔고 호출 도중 (kills=False 로 끌 수 있다)
  · 주문 전송: 닿기 전에 통신 오류 / 거래소의 5xx(결과 불명) / 거래소의 4xx 거절(주문이 만들어지지 않음) / 응답만 못 받음
  · 체결: 체결 0, 매도 일부 체결
  · 조회 지연: 들어간 주문이 조회에 안 보이는 시간 3분 ~ 25시간, 그리고 3일·5일 (날짜를 넘겨 미해결로 남는 주문)
  · 체결 내역 지연: 끝난 주문인데 trades 가 한동안 비어 있음
  · 조회·잔고·호가 호출의 시간 초과, 장부 기록 파일 쓰기 실패

지켜야 할 것 (어기면 problems 에 남는다)
  1) 같은 주문 번호를 두 번 보내지 않는다.
  2) 번호를 상태 파일(pending·sent)에 적기 전에 보내지 않는다.
  3) 한 종목에 하루 두 번 매수를 보내지 않는다.
  4) 계좌의 코인이 장부보다 많은데(최소 주문 금액 이상) 매수를 보내지 않는다.
  5) 결과를 모르는 번호가 남아 있는데 새 매수를 보내지 않는다. 결과를 모르는 매도 번호가 남아 있는데 새 매도를 보내지 않는다.
     (날짜가 바뀌어도 마찬가지 — 고문 판정 2026-11-14 두 번째)
  6) 팔아야 할 포지션이 남아 있는 날에는 그 종목을 말하는 알림이 적어도 한 번 나간다.
  7) 장애를 끄고 며칠 조용히 돌린 뒤에는 장부가 거래소에서 실제로 일어난 일과 같다(현금·코인), 미처리 주문과
     결과를 모르는 번호가 남지 않는다, 같은 체결이 장부 기록에 두 번 있지 않다.
  8) 실행이 예상하지 못한 예외로 죽지 않는다.

쓰는 법
  python3 -B -X utf8 tests/fuzz_h3_executor.py [첫 seed] [개수] [--no-kills]
  단위 테스트(tests/test_h3_fuzz.py)는 정해 둔 seed 몇 개만 돈다.
"""
import datetime as dt
import importlib.util
import json
import random
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("h3_executor_tests", ROOT / "tests" / "test_h3_executor.py")
T = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(T)
HX, FakeExchange, candles_for = T.HX, T.FakeExchange, T.candles_for
BTC, ETH, PRICE = T.BTC, T.ETH, T.PRICE

LAGS_MIN = [0] * 14 + [3, 8, 15, 15, 300, 25 * 60, 3 * 24 * 60, 5 * 24 * 60]   # 들어간 주문이 조회에 안 보이는 시간(분)
DAYS, QUIET_DAYS = 6, 7                                                        # 장애를 넣는 날 수, 그 뒤 조용히 돌리는 날 수(가장 긴 지연보다 길게)
SCHEDULE = [(1, "daily"), (6, "retry"), (11, "daily")] + [(m, "retry") for m in (16, 26, 36, 46, 56, 66, 126, 726, 1436)]


class Kill(BaseException):
    """프로세스가 죽는 것을 흉내 낸다. Exception 이 아니라서 프로그램의 어떤 except 도 잡지 못한다."""


class World(object):
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ratio = {BTC: 1.0, ETH: 1.0}
        self.clock = dt.datetime(2026, 11, 10, 0, 1, 0, tzinfo=dt.timezone.utc)
        self.sent = []
        env = {"H3_MODE": "LIVE", "H3_CAPITAL_KRW": "2000000", "H3_STATE_DIR": self.tmp.name,
               "H3_BOT_MARKER": str(Path(self.tmp.name, "marker.json")), "UPBIT_ACCESS_KEY": "ak", "UPBIT_SECRET_KEY": "sk"}
        self.cfg = HX.Config(env)
        self.ex = None

    def runner(self):
        return HX.Executor(self.cfg, self.ex, fetch_candles=lambda m, day: candles_for(m, day, self.ratio[m]),
                           notify=self.sent.append, now=lambda: self.clock, bot_pids=[], sleep=lambda s: None)

    def sleeve(self, market):
        return HX.load_state(self.cfg)["sleeves"][market]

    def ledger(self):
        p = self.cfg.ledger_path
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def brief(s):
    return {"cash": round(s["cash"]), "coin": round(s["coin"], 8), "last_day": s["last_day"],
            "pending": (s.get("pending") or {}).get("identifier"), "sell_blocked": s.get("sell_blocked"),
            "sent": [(r["id"], r["status"], r.get("misses")) for r in s.get("sent") or []]}


class FuzzExchange(FakeExchange):
    def __init__(self, world, rnd, kills=True):
        super().__init__()
        self.w, self.rnd, self.kills = world, rnd, kills
        self.faults = True
        self.posted = []            # place() 에 넘어온 모든 번호
        self.visible_at = {}        # 번호 -> 이 시각부터 조회에 보인다
        self.hide_until = {}        # 번호 -> 이 시각까지는 체결 내역 없이 보인다
        self.buy_posts = Counter()  # (종목, 날짜) -> 매수 전송 횟수
        self.violations = []

    def chance(self, p):
        return self.faults and self.rnd.random() < p

    def net(self, what, p=0.03):
        if self.kills and self.chance(0.004):
            raise Kill(what)
        if self.chance(p):
            raise OSError("%s: timed out" % what)

    def orderbook(self, market):
        self.net("orderbook", 0.02)
        return super().orderbook(market)

    def balances(self):
        self.net("balances", 0.02)
        return super().balances()

    def account(self, currency):
        self.net("account", 0.02)
        return super().account(currency)

    def order(self, identifier):
        self.net("order", 0.03)
        found = self.orders.get(identifier)
        if found is None or self.w.clock < self.visible_at.get(identifier, self.w.clock):
            return None
        if self.w.clock < self.hide_until.get(identifier, self.w.clock):
            return dict(found, trades=[])
        return found

    def place(self, market, side, identifier, krw=None, volume=None):
        day = self.w.clock.date().isoformat()
        cur = market.split("-")[1]
        if identifier in self.posted:
            self.violations.append("identifier POSTed twice: %s" % identifier)
        disk = HX.load_state(self.w.cfg)["sleeves"][market]
        others = [r for r in disk.get("sent") or [] if r["status"] == "open" and r["id"] != identifier]
        if side == "bid":
            self.buy_posts[(market, day)] += 1
            if self.buy_posts[(market, day)] > 1:
                self.violations.append("second buy POST for %s on %s: %s" % (market, day, identifier))
            if (self.bal.get(cur, 0.0) + self.locked.get(cur, 0.0) - disk["coin"]) * PRICE[market] >= HX.MIN_ORDER_KRW:
                self.violations.append("buy POST while the account holds more than the book: %s (acct %.8f book %.8f)" % (
                    identifier, self.bal.get(cur, 0.0), disk["coin"]))
            if others:
                self.violations.append("buy POST while an earlier order is unresolved: %s (unresolved %s)" % (
                    identifier, [r["id"] for r in others]))
        elif any(r["side"] == "ask" for r in others):
            self.violations.append("sell POST while an earlier sell is unresolved: %s (unresolved %s)" % (
                identifier, [r["id"] for r in others if r["side"] == "ask"]))
        if (disk.get("pending") or {}).get("identifier") != identifier or not any(
                r["id"] == identifier and r["status"] == "open" for r in disk.get("sent") or []):
            self.violations.append("POST of %s before it was written to the state file" % identifier)
        self.posted.append(identifier)
        r = self.rnd.random() if self.faults else 1.0
        if self.kills and r < 0.02:
            raise Kill("place before send")
        if r < 0.07:
            raise OSError("network down")                       # 거래소에 닿지 못했다
        if r < 0.10:
            raise HX.H3Error("upbit POST /v1/orders -> 500 ")   # 닿았는데 결과를 모른다(주문은 만들어지지 않았다)
        if r < 0.13:
            err = HX.H3Error("upbit POST /v1/orders -> 400 validation_error")     # 거래소가 거절했다
            err.code, err.name = 400, "validation_error"
            raise err
        self.no_fill = 0.13 <= r < 0.19
        self.sell_ratio = self.rnd.choice([0.3, 0.6, 0.9]) if (0.19 <= r < 0.29 and side == "ask") else 1.0
        self.fail_after_accept = False
        try:
            super().place(market, side, identifier, krw=krw, volume=volume)
        finally:
            self.no_fill, self.sell_ratio = False, 1.0
        lag = 0
        if self.faults:
            lag = self.rnd.choice(LAGS_MIN)
            if self.rnd.random() < 0.08:
                self.hide_until[identifier] = self.w.clock + dt.timedelta(minutes=self.rnd.choice([7, 12, 700]))
        self.visible_at[identifier] = self.w.clock + dt.timedelta(minutes=lag)
        if self.kills and 0.29 <= r < 0.32:
            raise Kill("place after accept")
        if 0.32 <= r < 0.44 or lag:
            raise TimeoutError("read timed out")
        return self.orders[identifier]


def truth(ex, market, initial):
    """거래소에서 실제로 일어난 일로 계산한 현금과 코인."""
    cash = initial
    for ident, o in ex.orders.items():
        if market not in ident:
            continue
        funds = sum(float(t["funds"]) for t in o["trades"])
        fee = float(o["paid_fee"])
        cash += (funds - fee) if "-ask" in ident else -(funds + fee)
    return cash, ex.bal.get(market.split("-")[1], 0.0)


def one(seed, kills=True, days=DAYS, quiet_days=QUIET_DAYS):
    """seed 하나 → (문제 목록, 실행 기록, 날짜별 알림 수)."""
    rnd = random.Random(seed)
    w = World()
    w.ex = ex = FuzzExchange(w, rnd, kills=kills)
    real_save, real_append = HX.save_state, HX.append_ledger

    def save(cfg, state):
        if kills and ex.chance(0.01):
            raise Kill("before save")
        real_save(cfg, state)
        if kills and ex.chance(0.01):
            raise Kill("after save")

    def append(cfg, row):
        if kills and ex.chance(0.01):
            raise Kill("before ledger")
        if ex.chance(0.01):
            raise OSError("disk full")
        real_append(cfg, row)

    HX.save_state, HX.append_ledger = save, append
    log, tg_per_day, problems = [], Counter(), []
    try:
        on = {BTC: rnd.random() < 0.6, ETH: rnd.random() < 0.6}
        for d in range(days + quiet_days):
            quiet = d >= days
            ex.faults = not quiet
            for m in (BTC, ETH):
                if rnd.random() < 0.45:
                    on[m] = not on[m]
                if quiet:
                    on[m] = False
            w.ratio = {m: (1.05 if on[m] else 0.95) for m in (BTC, ETH)}
            base = dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc) + dt.timedelta(days=d)
            day_start = len(w.sent)
            for minute, kind in SCHEDULE:
                w.clock = base + dt.timedelta(minutes=minute)
                n = len(w.sent)
                try:
                    rows, errors = w.runner().run() if kind == "daily" else w.runner().run_sell_retry()
                    res = "rows=%d errors=%s" % (len(rows), [e[:90] for e in errors])
                except Kill as k:
                    res = "KILLED (%s)" % k
                except Exception as exc:                     # main() 이라면 이것을 알리고 종료 코드 1 로 끝난다
                    res = "RUN FAILED %s: %s" % (type(exc).__name__, exc)
                    if not isinstance(exc, (OSError, HX.H3Error)):
                        problems.append("unexpected %s in %s run: %r" % (type(exc).__name__, kind, exc))
                log.append("%s %-5s %s | tg+%d | BTC %s | ETH %s" % (w.clock.strftime("%m/%d %H:%M"), kind, res, len(w.sent) - n,
                                                                    brief(w.sleeve(BTC)), brief(w.sleeve(ETH))))
            tg_per_day[d] = len(w.sent) - day_start
            if not quiet:                                    # 팔아야 할 포지션이 남았는데 그날 그 종목을 말하는 알림이 하나도 없다
                for m in (BTC, ETH):
                    s = w.sleeve(m)
                    if not on[m] and s["coin"] * PRICE[m] >= HX.MIN_ORDER_KRW and not any(m[4:] in t for t in w.sent[day_start:]):
                        problems.append("day %d: %s should be sold, is still on the book, and no message mentioned it" % (d, m))
        problems.extend(ex.violations)
        for m in (BTC, ETH):
            s = w.sleeve(m)
            cash, coin = truth(ex, m, s["initial"])
            if abs(s["coin"] - coin) > 2e-8 or abs(s["cash"] - cash) > 1.0 or s.get("pending"):
                problems.append("%s book differs from the exchange: book cash=%.2f coin=%.8f pending=%s | real cash=%.2f coin=%.8f | sent=%s" % (
                    m, s["cash"], s["coin"], bool(s.get("pending")), cash, coin, s.get("sent")))
            left = [r["id"] for r in s.get("sent") or [] if r["status"] == "open"]
            if left:
                problems.append("%s: unresolved order numbers remain after the quiet period: %s" % (m, left))
            rows = [r for r in w.ledger() if r["market"] == m and r.get("fill") and r["fill"].get("uuid")]
            dup = [u for u, c in Counter(r["fill"]["uuid"] for r in rows).items() if c > 1]
            if dup:
                problems.append("%s: the same fill is in the ledger twice: %s" % (m, dup))
    finally:
        HX.save_state, HX.append_ledger = real_save, real_append
        w.tmp.cleanup()
    return problems, log, tg_per_day


def kind_of(problem):
    return "book differs from the exchange" if "book differs" in problem else problem.split(":")[0][:60]


def run(first, count, kills=True, show=6, out=print):
    """seed 여러 개 → (문제 종류별 건수, 하루 최대 알림 수와 그 seed)."""
    kinds, worst, shown = Counter(), (0, None), 0
    for seed in range(first, first + count):
        problems, _log, tg = one(seed, kills=kills)
        top = max(tg.values())
        if top > worst[0]:
            worst = (top, seed)
        for p in problems:
            kinds[kind_of(p)] += 1
        if problems and shown < show:
            shown += 1
            out("seed %d:" % seed)
            for p in problems:
                out("    " + p[:400])
    return dict(kinds), worst


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    first = int(args[0]) if args else 0
    count = int(args[1]) if len(args) > 1 else 300
    kills = "--no-kills" not in sys.argv
    kinds, worst = run(first, count, kills=kills)
    print("seeds %d..%d (%s) done. problem kinds: %s" % (first, first + count - 1, "kills on" if kills else "kills off", kinds))
    print("most telegrams in one day: %d (seed %s)" % worst)
    sys.exit(1 if kinds else 0)
