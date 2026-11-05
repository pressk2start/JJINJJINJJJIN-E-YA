#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""maker_spread_sim — H22 사전 등록: 1원 틱 종목에서 지정가로 스프레드를 '받는' 구조가 비용 후 양수인가 (읽기 전용, 표준 라이브러리, 3.7+).

지금까지의 가설(H1~H20)은 전부 시장가로 들어가 스프레드를 '내는' 구조였고, 틱 1원 종목(ADA·DOGE·XLM·XPL, 가격 100~1000원)은 그
스프레드가 0.1~1% 라 모든 신호가 비용에 묻혔다. H22 는 반대편이다: 최우선 매수호가에 지정가로 서서, 체결되면 최우선 매도호가에
지정가로 되판다. 수수료는 메이커도 0.05% 라 왕복 0.1%, 받는 스프레드는 틱 1개 = 0.1~1%.
위험: 역선택(내 호가가 맞는 순간 가격이 그쪽으로 움직임), 재고(매수만 체결되고 매도가 안 됨 → 시간 초과 시 시장가 청산으로
스프레드+수수료 손실), 체결률 자체가 낮을 가능성. 이 셋이 스프레드를 다 먹으면 FAIL 이고, 그것도 답이다.

사전 등록 (이 커밋이 결과보다 먼저다. 아래 숫자는 결과를 보고 바꾸지 않는다):
  데이터   ws_tight 10/22~10/27 (구 5종목 녹화). 종목 ADA·DOGE·XLM·XPL. XRP 는 틱이 가격의 0.03% 라 제외.
  TRAIN    10/22·23·24    HOLDOUT 10/25·26·27.    판정은 HOLDOUT 만.
  주문     명목 100,000 KRW, 종목당 동시 1건. 틱은 그날 호가 격자(인접 호가 차이의 최솟값)에서 추정.
  체결모형 (보수적) 내 주문은 가격·시간 우선순위의 맨 뒤. 앞 잔량 = 주문 활성 시점 그 가격의 표시 잔량. 앞 잔량은 체결로만 줄고
           (남의 취소는 무시), 내 가격에서 반대편 시장가 체결(내 매수호가엔 ask_bid=ASK, 내 매도호가엔 BID)이 앞 잔량을 다 소진한 뒤
           남는 누적량이 내 수량 이상일 때 체결. 내 가격을 뚫고 간 체결(내 매수가 아래 / 내 매도가 위의 체결가)은 전량 체결로 친다.
           주문 활성 시점에 이미 교차돼 있으면(매수호가≥최우선 매도가 등) 그 주문은 취소하고 다시 결정한다. 매도 주문이 활성 시점에
           교차돼 있으면 내 가격에 체결된 것으로 친다(실제론 더 좋은 가격 → 보수적).
  지연 L   결정 → 주문 활성 1000ms 또는 3000ms. 서버 주문 왕복은 실측이 없어 둘 다 본다.
  재호가   track: 최우선가가 내 가격과 달라지면 취소 후 새 최우선가로(L 지연).  up_only: 최우선가가 내 가격보다 좋아질 때만 재호가,
           시장이 내 가격 아래로 내려가면 그대로 둔다(체결을 기다림 = 역선택 감수).
  매도     매수 체결 직후 매도호가 = max(최우선 매도호가, 매수가+1틱). 최우선 매도가가 내려오면 이 하한까지만 따라간다.
  시간초과 T  매수 체결 후 T 초(120 또는 600) 안에 매도 체결이 없으면 최우선 매수호가에 시장가 청산.
  공백     수신 공백 >5s 또는 일자 경계: 대기 주문 취소, 보유 중이면 재개 직후 최우선 매수호가에 청산(exit=gap/eod).
  비용     0.05%×2.  net = sell·(1−f) / (buy·(1+f)) − 1.  건당 net 은 명목 대비 %.
  선택     TRAIN 에서 8조합(L×재호가×T) 중 왕복 n≥20 인 것 가운데 총 net 최대 1개. 기계적 선택, 사람 개입 없음. 후보 없으면 중단.
  판정     고른 조합의 HOLDOUT: 왕복 n<30 → INSUFFICIENT; 총 net>0 이고 건당 net 부트스트랩 95% 하한>0 → PASS; 아니면 FAIL.
           PASS 여도 LIVE 변경 없음(LIVE 는 시장가 구조). 다음 단계는 SHADOW 호가 시뮬이다.
  범위     이 4종목·6일·이 체결 모형 안에서만 결론. 내 주문이 남의 행동을 바꾸지 않는다고 가정(실제보다 유리한 쪽).
           체결 인식은 recv_ts 순서(봇이 보는 순서)이므로 실제보다 늦게 인식 = 보수적. 100,000 KRW 이 호가 잔량보다 크면 체결이 더 어렵다.

실행 (PC):
  py -3 -X utf8 maker_spread_sim.py ws_tight               TRAIN 8조합 표 + 선택 조합 → ws_tight_maker_train.txt
  py -3 -X utf8 maker_spread_sim.py ws_tight --holdout     TRAIN 으로 조합을 고른 뒤 HOLDOUT 판정 → ws_tight_maker_holdout.txt
"""
import argparse
import gzip
import json
import random
import re
import sys
import zlib
from pathlib import Path

FEE = 0.0005
NOTIONAL = 100000.0
MAX_GAP_MS = 5000
MARKETS = ("KRW-ADA", "KRW-DOGE", "KRW-XLM", "KRW-XPL")
TRAIN = ("2026-10-22", "2026-10-23", "2026-10-24")
HOLDOUT = ("2026-10-25", "2026-10-26", "2026-10-27")
COMBOS = [(L, rq, T) for L in (1000, 3000) for rq in ("track", "up_only") for T in (120, 600)]
MIN_TRAIN_N, MIN_HOLDOUT_N = 20, 30
_CODE = re.compile(rb'"code":\s*"(KRW-[A-Z0-9]+)"')
_RECV = re.compile(rb'"recv_ts":\s*(\d+)')


def net_pct(buy, sell):
    return 100.0 * (sell * (1 - FEE) / (buy * (1 + FEE)) - 1)


def _mean(v):
    return sum(v) / len(v) if v else None


def bootstrap_low(v, q=0.025, n=1000, seed=7):
    """건당 net 평균의 부트스트랩 하한 (seed 고정 → 재현 가능)."""
    if len(v) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(_mean([rng.choice(v) for _ in v]) for _ in range(n))
    return means[int(q * (n - 1))]


# ----------------------------------------------------------------------------------------------------------------------
# 이벤트 읽기: recv 순서 그대로. 종목 외 줄은 regex 로 걸러 json 파싱을 줄인다.
def iter_events(day_dir, markets):
    """yield ("book", t, code, bids{price:size}, asks{price:size}, best_bid, best_ask) | ("trade", t, code, price, vol, side)
    | ("gap", t, None) — 수신 공백 >5s 직후, 그 다음 이벤트보다 먼저."""
    files = sorted(p for p in day_dir.iterdir() if p.name.endswith(".jsonl.gz"))
    want = set(m.encode() for m in markets)
    prev = None
    for p in files:
        try:
            with gzip.open(str(p), "rb") as fh:
                for raw in fh:
                    c = _CODE.search(raw)
                    if not c or c.group(1) not in want:
                        r = _RECV.search(raw)              # 다른 종목/메타 줄도 공백 판정에는 쓴다 (수신 시각만)
                        if r:
                            t = int(r.group(1))
                            if prev is not None and t - prev > MAX_GAP_MS:
                                yield ("gap", t, None)
                            prev = t
                        continue
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    t = m.get("recv_ts")
                    if t is None:
                        continue
                    if prev is not None and t - prev > MAX_GAP_MS:
                        yield ("gap", t, None)
                    prev = t
                    kind = m.get("type")
                    code = m.get("code")
                    if kind == "orderbook":
                        units = m.get("orderbook_units") or []
                        if not units:
                            continue
                        bids = {float(u["bid_price"]): float(u["bid_size"]) for u in units}
                        asks = {float(u["ask_price"]): float(u["ask_size"]) for u in units}
                        yield ("book", t, code, bids, asks, max(bids), min(asks))
                    elif kind == "trade":
                        side = m.get("ask_bid")
                        if side not in ("ASK", "BID"):
                            continue
                        yield ("trade", t, code, float(m["trade_price"]), float(m["trade_volume"]), side)
        except (EOFError, OSError, zlib.error):
            yield ("gap", prev or 0, None)      # 잘린 파일 = 그 뒤는 모름


def infer_tick(asks, cur):
    """인접 매도호가 차이의 최솟값. cur 는 지금까지의 추정(None 가능)."""
    ps = sorted(asks)
    best = cur
    for a, b in zip(ps, ps[1:]):
        d = round(b - a, 6)
        if d > 0 and (best is None or d < best):
            best = d
    return best


# ----------------------------------------------------------------------------------------------------------------------
class _St(object):
    __slots__ = ("phase", "price", "qty", "queue", "active_at", "buy", "buy_t", "best_bid", "best_ask", "bids", "asks",
                 "tick", "nbook", "force_exit", "quoting_since", "quote_ms")

    def __init__(self):
        self.phase = "IDLE"            # IDLE | BID_PENDING | BID_WORKING | ASK_PENDING | ASK_WORKING
        self.price = self.qty = self.queue = self.active_at = self.buy = self.buy_t = None
        self.best_bid = self.best_ask = None
        self.bids = self.asks = None
        self.tick = None
        self.nbook = 0
        self.force_exit = False
        self.quoting_since = None
        self.quote_ms = 0


class Maker(object):
    """한 파라미터 조합의 시뮬레이터. 종목별 상태 1개. trips: dict(code, day, buy_t, sell_t, buy, sell, exit, net)."""

    def __init__(self, latency_ms, requote, timeout_s):
        self.L, self.requote, self.T = latency_ms, requote, timeout_s * 1000
        self.st = {}
        self.trips = []
        self.day = None

    def _s(self, code):
        s = self.st.get(code)
        if s is None:
            s = self.st[code] = _St()
        return s

    # --- 주문 결정 ---
    def _decide_bid(self, s, t):
        s.phase, s.price, s.active_at = "BID_PENDING", s.best_bid, t + self.L
        s.qty = NOTIONAL / s.best_bid
        if s.quoting_since is None:
            s.quoting_since = t

    def _decide_ask(self, s, t):
        floor = s.buy + s.tick
        s.phase, s.price, s.active_at = "ASK_PENDING", max(s.best_ask, floor), t + self.L

    def _close(self, s, t, sell, how, code):
        self.trips.append({"code": code, "day": self.day, "buy_t": s.buy_t, "sell_t": t, "buy": s.buy, "sell": sell,
                           "exit": how, "net": net_pct(s.buy, sell)})
        s.phase, s.buy, s.buy_t, s.force_exit = "IDLE", None, None, False

    # --- 이벤트 ---
    def on_gap(self, t):
        for s in self.st.values():
            if s.phase in ("BID_PENDING", "BID_WORKING"):
                s.phase = "IDLE"
            elif s.phase in ("ASK_PENDING", "ASK_WORKING"):
                s.force_exit = True
            if s.quoting_since is not None:
                s.quote_ms += max(0, t - s.quoting_since)
                s.quoting_since = None

    def end_day(self, t):
        for code, s in self.st.items():
            if s.phase in ("ASK_PENDING", "ASK_WORKING") and s.best_bid:
                self._close(s, t, s.best_bid, "eod", code)
            s.phase = "IDLE"
            if s.quoting_since is not None:
                s.quote_ms += max(0, t - s.quoting_since)
                s.quoting_since = None

    def on_book(self, t, code, bids, asks, best_bid, best_ask):
        s = self._s(code)
        s.bids, s.asks, s.best_bid, s.best_ask = bids, asks, best_bid, best_ask
        if s.nbook < 200:
            s.tick = infer_tick(asks, s.tick)
        s.nbook += 1
        if s.tick is None:
            return
        if s.quoting_since is None:
            s.quoting_since = t
        if s.force_exit:                                   # 공백 후 재개: 보유분 즉시 청산
            self._close(s, t, best_bid, "gap", code)
            return
        if s.phase in ("ASK_PENDING", "ASK_WORKING") and t - s.buy_t >= self.T:
            self._close(s, t, best_bid, "timeout", code)
            return
        if s.phase == "IDLE":
            self._decide_bid(s, t)
            return
        if s.phase == "BID_PENDING" and t >= s.active_at:
            if s.price >= best_ask:                        # 활성 시점에 교차 → 취소, 다시 결정
                self._decide_bid(s, t)
                return
            s.queue = bids.get(s.price, 0.0)
            s.phase = "BID_WORKING"
        if s.phase == "BID_WORKING":
            if best_bid > s.price or (self.requote == "track" and best_bid < s.price):
                self._decide_bid(s, t)                     # 취소 후 재호가 (큐 맨 뒤로)
            return
        if s.phase == "ASK_PENDING" and t >= s.active_at:
            if best_bid >= s.price:                        # 교차: 내 가격에 체결로 침 (실제는 더 유리)
                self._close(s, t, s.price, "cross", code)
                return
            s.queue = asks.get(s.price, 0.0)
            s.phase = "ASK_WORKING"
        if s.phase == "ASK_WORKING":
            floor = s.buy + s.tick
            target = max(best_ask, floor)
            if target < s.price - 1e-9:                    # 최우선 매도가가 내려옴 → 하한까지 따라감
                self._decide_ask(s, t)

    def on_trade(self, t, code, price, vol, side):
        s = self._s(code)
        if s.phase == "BID_WORKING":
            if price < s.price - 1e-9 or (abs(price - s.price) < 1e-9 and side == "ASK"):
                if price < s.price - 1e-9:
                    s.queue = -s.qty                       # 뚫고 지나감 = 전량 체결
                else:
                    s.queue -= vol
                if -s.queue >= s.qty - 1e-12:
                    s.buy, s.buy_t = s.price, t
                    self._decide_ask(s, t)
        elif s.phase == "ASK_WORKING":
            if price > s.price + 1e-9 or (abs(price - s.price) < 1e-9 and side == "BID"):
                if price > s.price + 1e-9:
                    s.queue = -s.qty
                else:
                    s.queue -= vol
                if -s.queue >= s.qty - 1e-12:
                    self._close(s, t, s.price, "maker", code)


# ----------------------------------------------------------------------------------------------------------------------
def run_days(root, days, markets=MARKETS, combos=COMBOS, log=None):
    """모든 조합을 한 번의 파싱으로 돌린다 → {combo: Maker}."""
    sims = {c: Maker(*c) for c in combos}
    for day in days:
        d = Path(root, day)
        if not d.is_dir():
            if log:
                log("missing day folder: %s" % d)
            continue
        for m in sims.values():
            m.day = day
        last_t = None
        for ev in iter_events(d, markets):
            kind, t = ev[0], ev[1]
            last_t = t
            if kind == "gap":
                for m in sims.values():
                    m.on_gap(t)
            elif kind == "book":
                for m in sims.values():
                    m.on_book(t, ev[2], ev[3], ev[4], ev[5], ev[6])
            else:
                for m in sims.values():
                    m.on_trade(t, ev[2], ev[3], ev[4], ev[5])
        if last_t is not None:
            for m in sims.values():
                m.end_day(last_t)
        if log:
            log("  scanned %s" % day)
    return sims


def summarize(trips, quote_hours):
    n = len(trips)
    nets = [x["net"] for x in trips]
    maker = sum(1 for x in trips if x["exit"] == "maker")
    return {"n": n, "maker_pct": 100.0 * maker / n if n else None, "mean": _mean(nets),
            "total": sum(nets), "low95": bootstrap_low(nets), "worst": min(nets) if nets else None,
            "trips_per_h": n / quote_hours if quote_hours else None,
            "exits": {k: sum(1 for x in trips if x["exit"] == k) for k in ("maker", "timeout", "cross", "gap", "eod")}}


def quote_hours(m):
    return sum(s.quote_ms for s in m.st.values()) / 3600000.0


def choose(sims):
    """TRAIN 선택 규칙: n>=MIN_TRAIN_N 중 총 net 최대. 없으면 None."""
    best, best_total = None, None
    for c in COMBOS:
        m = sims[c]
        if len(m.trips) >= MIN_TRAIN_N:
            tot = sum(x["net"] for x in m.trips)
            if best is None or tot > best_total:
                best, best_total = c, tot
    return best


def verdict(summary):
    if summary["n"] < MIN_HOLDOUT_N:
        return "INSUFFICIENT"
    if summary["total"] > 0 and summary["low95"] is not None and summary["low95"] > 0:
        return "PASS"
    return "FAIL"


def _f(v, spec="{:.3f}"):
    return "n/a" if v is None else spec.format(v)


def combo_table(sims, title):
    lines = [title, "{:<6} {:<8} {:>5} {:>5} {:>7} {:>8} {:>8} {:>8} {:>7} {:>6}  exits".format(
        "L_ms", "requote", "T_s", "n", "maker%", "mean%", "total%", "low95%", "worst%", "trip/h")]
    for c in COMBOS:
        m = sims[c]
        s = summarize(m.trips, quote_hours(m))
        lines.append("{:<6} {:<8} {:>5} {:>5} {:>7} {:>8} {:>8} {:>8} {:>7} {:>6}  {}".format(
            c[0], c[1], c[2], s["n"], _f(s["maker_pct"], "{:.0f}"), _f(s["mean"]), _f(s["total"], "{:.2f}"),
            _f(s["low95"]), _f(s["worst"], "{:.2f}"), _f(s["trips_per_h"], "{:.1f}"),
            " ".join("%s=%d" % kv for kv in s["exits"].items() if kv[1])))
    return lines


def market_table(m):
    lines = ["  per market: {:<9} {:>5} {:>7} {:>8} {:>8} {:>7}".format("market", "n", "maker%", "mean%", "total%", "worst%")]
    for code in sorted(set(x["code"] for x in m.trips)):
        tr = [x for x in m.trips if x["code"] == code]
        s = summarize(tr, None)
        lines.append("              {:<9} {:>5} {:>7} {:>8} {:>8} {:>7}  tick={}".format(
            code, s["n"], _f(s["maker_pct"], "{:.0f}"), _f(s["mean"]), _f(s["total"], "{:.2f}"), _f(s["worst"], "{:.2f}"),
            m.st[code].tick if code in m.st else "n/a"))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--holdout", action="store_true", help="TRAIN 으로 조합을 고른 뒤 HOLDOUT 판정")
    a = ap.parse_args(argv)
    root = Path(a.root)
    out = []

    def log(s):
        print(s, flush=True)
        out.append(s)

    log("H22 MAKER SPREAD SIM | root=%s | markets=%s | notional=%d KRW | fee=%.2f%%x2 | queue model: conservative"
        % (root, ",".join(MARKETS), NOTIONAL, FEE * 100))
    log("TRAIN %s" % ", ".join(TRAIN))
    train = run_days(root, TRAIN, log=log)
    for ln in combo_table(train, "TRAIN combos (selection = max total%% with n>=%d):" % MIN_TRAIN_N):
        log(ln)
    chosen = choose(train)
    if chosen is None:
        log("SELECTION: none (no combo reached n>=%d round trips on TRAIN) -> STOP. H22 cannot be evaluated with this data."
            % MIN_TRAIN_N)
        Path(str(root).rstrip("\\/") + "_maker_train.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
        return 0
    log("SELECTION: L=%dms requote=%s T=%ds" % chosen)
    for ln in market_table(train[chosen]):
        log(ln)
    if not a.holdout:
        p = Path(str(root).rstrip("\\/") + "_maker_train.txt")
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
        print("saved:", p)
        return 0
    log("HOLDOUT %s" % ", ".join(HOLDOUT))
    hold = run_days(root, HOLDOUT, log=log)
    s = summarize(hold[chosen].trips, quote_hours(hold[chosen]))
    v = verdict(s)
    log("HOLDOUT chosen L=%dms requote=%s T=%ds: n=%d maker%%=%s mean=%s%% total=%s%% low95=%s%% worst=%s%% -> %s"
        % (chosen[0], chosen[1], chosen[2], s["n"], _f(s["maker_pct"], "{:.0f}"), _f(s["mean"]), _f(s["total"], "{:.2f}"),
           _f(s["low95"]), _f(s["worst"], "{:.2f}"), v))
    for ln in market_table(hold[chosen]):
        log(ln)
    for ln in combo_table(hold, "HOLDOUT all combos (reference only — NOT pre-selected, no verdict):"):
        log(ln)
    log("scope: these 4 markets, 10/22-27, this fill model (orders do not change others' behaviour; cancels ignored). "
        "PASS -> SHADOW quote sim next, no LIVE change.")
    p = Path(str(root).rstrip("\\/") + "_maker_holdout.txt")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("saved:", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
