#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
오더북 기반 대기열 시뮬레이터 — mm_sim.py 의 큐 대리변수를 실제 모형으로 교체.

왜 필요한가
-----------
mm_sim.py 는 KRW-DOGE 에서 +11.8bp 를 냈지만 대기열을 '체결규모 상위 20%'
라는 **대리변수**로 근사했다. 그게 그 결과의 유일한 결정적 구멍이다.
여기서는 실제 호가 잔량을 추적한다.

핵심 난점 — 스냅샷은 내 앞의 취소를 개별로 보여주지 않는다
--------------------------------------------------------
두 스냅샷 사이에 어떤 가격의 잔량이 S → S' 로 줄었을 때, 그 감소가
  · 체결(내 앞이 소진 → 나를 앞으로 밀어줌)
  · 취소(내 **앞** 취소면 밀어주고, 내 **뒤** 취소면 아무 의미 없음)
  · 위 둘의 혼합
중 무엇인지 확정할 수 없다. ws_features.py 의 `unexplained_depl_ratio` 와
정확히 같은 모호성이다.

그래서 3시나리오는 임의 선택이 아니라 **이 모호성의 상하한**이다.

  보수(conservative) : 체결로 설명된 감소만 나를 전진시킨다. 취소는 0% 인정.
  중립(proportional) : 미설명 감소 × (내 앞 잔량 / 레벨 총잔량). 레벨 내
                       균등 위치 가정. 취소가 큐 전체에 고르게 분포한다고 본다.
  낙관(optimistic)   : 미설명 감소 전부를 내 앞에서 나간 것으로 인정. 100%.

**판정 규칙**: 세 시나리오의 부호가 갈리면 결론은 **"확정 불가"**.
낙관만 양수면 그건 큐 모형이 만들어낸 값이지 시장이 준 값이 아니다.
캔들 반전 신호를 체결가정으로 죽인 것과 같은 규율이다.

모형에 반드시 들어간 것
-----------------------
1. **큐 위치 초기화** — 최우선호가가 밀리거나 내 레벨이 소멸하면 재호가하고
   **새 큐의 맨 뒤**로 간다. 이걸 빼면 큐 위치가 영원히 유지되어 낙관 편향이 된다.
2. **재호가 지연** — 가격이 움직여도 즉시 못 뺀다. latency 만큼 낡은 호가가
   노출되고 그 사이 얻어맞는다(stale fill). 실전 최대 손실원이다.
3. **체결 = 정보 도착** — 내 주문이 체결되는 순간이 정보 있는 흐름이 온 순간이다.
   체결 시점 mid 와 t+H mid 를 같이 기록해 adverse selection 을 명시적으로 낸다.
4. **부분 체결** — 내 앞이 다 소진되고 남은 물량만큼만 채워진다.
5. **재고 상한 + 강제청산은 테이커가로** — 청산은 반대편 호가를 친다.
6. **대조군** — KRW-XRP · KRW-BTC. **거기서 음수가 안 나오면 큐 모형이 틀린 것이다.**

시간 규약
---------
ws_features.py 와 동일하게 **거래소 타임스탬프**를 1차 시계로 쓴다.
수신 시각(_rx)이 아니다. 이벤트는 event-time 으로 병합 처리한다.
"""
import os, sys, gzip, json, glob, heapq, argparse, datetime
from collections import defaultdict

R = os.path.dirname(os.path.abspath(__file__))
FEE = 0.0005                       # 편도 0.05% (업비트 KRW, maker/taker 동일)
# 미설명 감소(취소)를 내 앞에서 나간 것으로 얼마나 인정할지. 이 축만 바꾸고
# **이벤트 집합은 고정한다.** 부분집합을 바꾸면 큐 효과와 국면 효과가 섞여
# 비교 자체가 성립하지 않는다 (mm_sim.py 의 3층이 정확히 그 오류였다).
CANCEL_CREDITS = (0.0, 0.5, 1.0)


# ────────────────────────────────────────────────────────────── 입력

def event_ts_ms(m):
    if m.get("type") == "trade":
        return m.get("trade_timestamp") or m.get("timestamp")
    return m.get("timestamp")


def stream(paths, market):
    """event-time 순으로 정렬해 흘린다. 지각 이벤트는 워터마크로 흡수."""
    files = []
    for p in paths:
        files += sorted(glob.glob(p))
    if not files:
        sys.exit("입력 파일 없음")
    heap, hi, push_n = [], 0, 0
    LATE = 2000
    for f in files:
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if m.get("type") not in ("orderbook", "trade"):
                    continue
                if m.get("code") != market:
                    continue
                ets = event_ts_ms(m)
                if ets is None:
                    continue
                push_n += 1
                heapq.heappush(heap, (ets, m.get("_seq", 0), push_n, m))
                hi = max(hi, ets)
                while heap and hi - heap[0][0] > LATE:
                    t, _, _, mm = heapq.heappop(heap)
                    yield t, mm
    while heap:
        t, _, _, mm = heapq.heappop(heap)
        yield t, mm


# ────────────────────────────────────────────────────────────── 상태

class Book:
    """가격→잔량. 스냅샷 전체 교체 방식(업비트 WS 는 델타가 아니라 스냅샷)."""

    def __init__(self):
        self.bid = {}
        self.ask = {}
        self.ts = None

    def best(self, side):
        d = self.bid if side > 0 else self.ask
        if not d:
            return None
        return max(d) if side > 0 else min(d)

    def mid(self):
        b, a = self.best(1), self.best(-1)
        if b is None or a is None:
            return None
        return (b + a) / 2.0

    def apply(self, m, ts):
        """새 스냅샷 적용. 가격별 **감소분**을 반환한다(증가는 무시 — 내 뒤에 붙는다)."""
        nb, na = {}, {}
        for u in m.get("orderbook_units") or []:
            bp, bs = u.get("bid_price"), u.get("bid_size")
            ap, asz = u.get("ask_price"), u.get("ask_size")
            if bp:
                nb[bp] = bs or 0.0
            if ap:
                na[ap] = asz or 0.0
        drop = {1: {}, -1: {}}
        for side, old, new in ((1, self.bid, nb), (-1, self.ask, na)):
            if not old:
                continue
            # 관측 창 밖으로 나간 레벨은 '소멸'이 아니라 '안 보임'이다.
            # 창 안에 남아 있는 레벨만 감소로 인정한다.
            floor = min(new) if new else None
            ceil = max(new) if new else None
            for p, s in old.items():
                if floor is None or p < floor or p > ceil:
                    continue                      # 창 밖 — 판단 보류
                d = s - new.get(p, 0.0)
                if d > 0:
                    drop[side][p] = d
        self.bid, self.ask, self.ts = nb, na, ts
        return drop


class MyOrder:
    """내 지정가 주문 하나. queue_ahead 는 **내 앞의 잔량**이다."""

    __slots__ = ("side", "price", "qty", "queue_ahead", "placed_ts", "level_size0")

    def __init__(self, side, price, qty, queue_ahead, ts, level_size0):
        self.side = side                 # +1 매수, -1 매도
        self.price = price
        self.qty = qty
        self.queue_ahead = queue_ahead
        self.placed_ts = ts
        self.level_size0 = level_size0   # 배치 시점 레벨 총잔량 (중립 시나리오용)


# ────────────────────────────────────────────────────────────── 시뮬

class Sim:
    def __init__(self, cancel_credit, order_krw, cap_krw, latency_ms, horizon_s):
        self.cc = cancel_credit
        self.order_krw = order_krw
        self.cap_krw = cap_krw
        self.latency = latency_ms
        self.H = horizon_s * 1000
        self.bk = Book()
        self.orders = {1: None, -1: None}
        self.pending = {1: None, -1: None}     # 재호가 예정 시각
        self.pos = 0.0
        self.cash = 0.0
        self.last_mid = None
        self.fills = []                        # (ts, side, price, qty, mid_at_fill, stale)
        self.n_stale = 0
        self.exec_acc = defaultdict(float)     # (side, price) -> 직전 transition 이후 체결량
        self.stat = defaultdict(float)

    # ---- 큐 전진

    def _advance(self, side, price, executed, observed_drop):
        """내 앞 큐를 줄인다. 반환: 나에게 돌아온 체결량."""
        o = self.orders[side]
        if o is None or o.price != price:
            return 0.0
        # ① 체결로 설명된 감소는 FIFO 로 내 앞부터 소진 — 모든 시나리오 공통
        take = min(executed, o.queue_ahead)
        o.queue_ahead -= take
        mine = max(0.0, executed - take)        # 내 앞이 비면 나에게 온다
        # ② 미설명 감소(취소 또는 관측 오차)의 처리 = 시나리오 차이
        unexp = max(0.0, observed_drop - executed)
        if unexp > 0 and o.queue_ahead > 0:
            credit = unexp * self.cc
            o.queue_ahead = max(0.0, o.queue_ahead - credit)
            self.stat["queue_credit"] += min(credit, unexp)
        return min(mine, o.qty)

    # ---- 체결 기록

    def _mark(self, mid):
        """직전 mark 이후의 재고 손익을 확정한다. 이게 adverse selection 이 들어오는 통로다."""
        if mid is None:
            return
        if self.last_mid is not None and abs(self.pos) > 0:
            self.stat["inventory"] += self.pos * (mid - self.last_mid)
        self.last_mid = mid

    def _fill(self, ts, side, price, qty, stale):
        if qty <= 0:
            return
        mid = self.bk.mid()
        self._mark(mid)                      # 체결 **직전**까지의 재고 손익을 먼저 확정
        self.cash -= side * qty * price + qty * price * FEE
        self.pos += side * qty
        self.fills.append((ts, side, price, qty, mid, stale))
        if stale:
            self.n_stale += 1
        # 스프레드 포획 = 중간가 대비 유리하게 체결된 몫. 매수는 mid 아래, 매도는 mid 위.
        if mid is not None:
            self.stat["spread"] += side * (mid - price) * qty
        self.stat["fees"] += qty * price * FEE
        self.stat["turn"] += qty * price
        o = self.orders[side]
        if o is not None:
            o.qty -= qty
            if o.qty <= 1e-12:
                self.orders[side] = None
                self.pending[side] = ts + self.latency     # 재호가는 지연 후

    # ---- 호가 배치/철회

    def _want(self, side):
        """재고 상한을 넘기는 방향이면 호가를 걸지 않는다."""
        px = self.bk.best(side)
        if px is None:
            return False
        return abs((self.pos + side * (self.order_krw / px)) * px) <= self.cap_krw

    def _place(self, side, ts):
        px = self.bk.best(side)
        if px is None:
            return
        lvl = (self.bk.bid if side > 0 else self.bk.ask).get(px, 0.0)
        qty = self.order_krw / px
        # 새로 걸면 **큐 맨 뒤**다. 이미 쌓인 잔량 전부가 내 앞이다.
        self.orders[side] = MyOrder(side, px, qty, lvl, ts, lvl + qty)
        self.pending[side] = None

    def _requote_check(self, ts):
        """최우선호가가 바뀌면 내 호가는 낡았다. latency 뒤에 새 큐 뒷줄로 재배치."""
        for side in (1, -1):
            o = self.orders[side]
            best = self.bk.best(side)
            if o is None:
                p = self.pending[side]
                if (p is None or ts >= p) and self._want(side):
                    self._place(side, ts)
                continue
            if best is not None and o.price != best:
                # 낡은 호가다. **즉시 못 뺀다** — latency 동안 그대로 노출되고
                # 그 사이 들어온 체결에 얻어맞는다(stale fill). 실전 최대 손실원.
                if self.pending[side] is None:
                    self.pending[side] = ts + self.latency
                elif ts >= self.pending[side]:
                    self.orders[side] = None
                    self.pending[side] = ts + self.latency
                    self.stat["requotes"] += 1
            elif best is not None and o.price == best:
                self.pending[side] = None        # 다시 최우선이면 철회 예약 취소

    # ---- 이벤트 처리

    def on_trade(self, ts, m):
        p = m.get("trade_price")
        v = m.get("trade_volume")
        if not p or not v:
            return
        # ask_bid = 테이커 방향. BID=공격적 매수 → 매도호가 소진 → MM 의 매도(-1) 체결
        taker_buy = (m.get("ask_bid") == "BID")
        hit_side = -1 if taker_buy else 1        # 소진되는 호가 쪽 = MM 이 걸어둔 쪽
        self.exec_acc[(hit_side, p)] += v
        o = self.orders[hit_side]
        if o is not None and o.price == p:
            best = self.bk.best(hit_side)
            stale = (best is not None and best != p)   # 낡은 호가로 맞은 체결
            got = self._advance(hit_side, p, v, v)     # 체결분은 전부 설명됨
            if got > 0:
                self._fill(ts, hit_side, p, got, stale)

    def on_book(self, ts, m):
        drop = self.bk.apply(m, ts)
        for side in (1, -1):
            o = self.orders[side]
            if o is None:
                continue
            d = drop[side].get(o.price)
            if d:
                ex = self.exec_acc.get((side, o.price), 0.0)
                # 체결분은 on_trade 에서 이미 반영했다. 여기선 **미설명분만** 처리.
                got = self._advance(side, o.price, 0.0, max(0.0, d - ex))
                if got > 0:
                    self._fill(ts, side, o.price, got, False)
            # 관측 호가창에서 내 레벨이 사라져도 **내 주문은 살아 있다.**
            # 레코더 스냅샷에는 내 주문이 안 들어가므로, 그 레벨이 비었다는 건
            # 남들이 다 빠졌다는 뜻이고 나는 그 가격의 유일한 주문이 된다.
            # 여기서 주문을 지우면 '낡은 호가로 얻어맞는' 실전 최대 손실원을
            # 모형에서 통째로 지우게 된다. 큐 초기화는 레벨 소멸이 아니라
            # **재호가 시점**(_requote_check)에 일어나야 맞다.
            book = self.bk.bid if side > 0 else self.bk.ask
            if o.price not in book and o.queue_ahead > 0:
                o.queue_ahead = 0.0
                self.stat["level_gone"] += 1
        self.exec_acc.clear()
        self._requote_check(ts)

    # ---- 마감

    def liquidate(self, ts):
        """잔여 재고를 **테이커가로** 청산한다. 반대편 호가를 쳐야 한다."""
        if abs(self.pos) < 1e-12:
            return
        # 롱이면 매수호가(bid)에 던지고, 숏이면 매도호가(ask)를 친다. 테이커가다.
        px = self.bk.best(1) if self.pos > 0 else self.bk.best(-1)
        if px is None:
            px = self.bk.mid()
        if px is None:
            return
        q = abs(self.pos)
        sgn = 1 if self.pos > 0 else -1       # +1 = 롱 청산(매도)
        mid = self.bk.mid()
        self._mark(mid)                       # 청산 직전까지 재고 손익 확정
        self.cash += sgn * q * px - q * px * FEE
        # 청산도 하나의 체결이다. 분해 항등식에 들어가야 한다 —
        # 안 넣으면 '청산이 수익 대부분을 먹었나' 판정 자체가 불가능해진다.
        # 청산 방향은 보유의 반대(-sgn)이므로 스프레드 기여는 (-sgn)*(mid-px)*q,
        # 즉 반대편 호가를 쳐서 잃은 몫만큼 **음수**다.
        if mid is not None:
            self.stat["spread"] += (-sgn) * (mid - px) * q
            self.stat["liq_cost"] = q * abs(mid - px) + q * px * FEE
        self.stat["fees"] += q * px * FEE
        self.stat["liq_krw"] = q * px
        self.stat["liq_qty"] = q
        self.pos = 0.0


def adverse_selection(fills, book_ts_mid, horizon_ms):
    """체결 시점 mid 대비 t+H mid. 체결이 곧 정보 도착인지 명시적으로 낸다."""
    if not fills or not book_ts_mid:
        return float("nan"), 0
    tss = [t for t, _ in book_ts_mid]
    mids = [m for _, m in book_ts_mid]
    import bisect
    tot, n = 0.0, 0
    for ts, side, price, qty, mid0, _ in fills:
        if mid0 is None:
            continue
        i = bisect.bisect_right(tss, ts + horizon_ms) - 1
        if i < 0:
            continue
        m1 = mids[i]
        if not m1:
            continue
        # MM 관점 손익 방향: 산 뒤 오르면 이득, 판 뒤 내리면 이득
        tot += side * (m1 - mid0) / mid0 * 1e4 * qty * price
        n += 1
    return (tot, n)


def run_one(paths, market, cancel_credit, order_krw, cap_krw, latency_ms, horizon_s):
    sim = Sim(cancel_credit, order_krw, cap_krw, latency_ms, horizon_s)
    mid_track = []
    last_ts = None
    for ts, m in stream(paths, market):
        last_ts = ts
        if m["type"] == "orderbook":
            sim.on_book(ts, m)
            mm = sim.bk.mid()
            if mm:
                sim._mark(mm)                 # 재고 손익을 시각마다 확정
                mid_track.append((ts, mm))
        else:
            sim.on_trade(ts, m)
    if last_ts is not None:
        sim.liquidate(last_ts)

    adv, nadv = adverse_selection(sim.fills, mid_track, horizon_s * 1000)
    turn = sim.stat["turn"]
    spread = sim.stat["spread"]
    fees = sim.stat["fees"]
    inv = sim.stat["inventory"]
    liq = sim.stat.get("liq_cost", 0.0)
    net = sim.cash

    # 분해 항등식 검증: net ≈ 스프레드포획 + 재고손익 − 수수료
    # (강제청산 비용은 재고손익·수수료 안에 이미 들어 있으므로 별도 진단값이다)
    resid = net - (spread + inv - fees)
    return dict(market=market, cancel_credit=cancel_credit, latency_ms=latency_ms,
                order_krw=order_krw, cap_krw=cap_krw,
                n_fills=len(sim.fills), n_stale=sim.n_stale,
                stale_frac=(sim.n_stale / len(sim.fills)) if sim.fills else float("nan"),
                requotes=int(sim.stat["requotes"]),
                level_gone=int(sim.stat["level_gone"]),
                queue_credit=sim.stat["queue_credit"],
                turn_krw=turn,
                spread_krw=spread, fees_krw=fees, inventory_krw=inv,
                liq_cost_krw=liq, liq_krw=sim.stat.get("liq_krw", 0.0),
                adverse_bp_krw=adv, n_adverse=nadv,
                net_krw=net, resid_krw=resid,
                bp=(net / turn * 1e4) if turn > 0 else float("nan"))


MIN_FILLS_FOR_MONO = 200      # 이보다 체결이 적으면 단조성 판정을 하지 않는다


def verdict_of(vals, n_fills=None):
    """세 cancel_credit 의 부호가 갈리면 **확정 불가**.

    단조성에 대해 — credit 이 크면 큐를 빨리 통과해 덜 정보적인 체결을 먹으므로
    기대값이 올라갈 것으로 **예상**되지만, 동시에 체결 수와 재고도 늘어난다.
    따라서 bp 단조성은 **정리가 아니라 진단 지표**다. 표본이 작으면 잡음으로도
    쉽게 깨지므로 체결이 MIN_FILLS_FOR_MONO 미만이면 판정하지 않는다('-').
    큰 표본에서 깨지면 큐 모형을 의심할 근거가 된다.
    """
    import math
    if any(math.isnan(v) for v in vals):
        return "체결없음", "-"
    if all(v > 0 for v in vals):
        v = "전부 양수"
    elif all(v < 0 for v in vals):
        v = "전부 음수"
    else:
        v = "확정 불가"
    if n_fills is not None and n_fills < MIN_FILLS_FOR_MONO:
        return v, "-"
    mono = (vals[0] <= vals[1] + 1e-9) and (vals[1] <= vals[2] + 1e-9)
    return v, ("OK" if mono else "X")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--markets", required=True)
    ap.add_argument("--order-krw", default="100000,300000,500000,1000000")
    ap.add_argument("--cap-krw", default="500000,1000000,2000000,4000000")
    ap.add_argument("--latency-ms", default="20,50,100,250,500")
    ap.add_argument("--horizon-sec", type=int, default=30)
    ap.add_argument("--save", default="")
    a = ap.parse_args()

    mks = [s.strip() for s in a.markets.split(",") if s.strip()]
    orders = [float(s) for s in a.order_krw.split(",")]
    caps = [float(s) for s in a.cap_krw.split(",")]
    lats = [int(s) for s in a.latency_ms.split(",")]
    total = len(mks) * len(orders) * len(caps) * len(lats) * len(CANCEL_CREDITS)
    print(f"[queue] {len(mks)}종목 × 주문{len(orders)} × 상한{len(caps)} × "
          f"지연{len(lats)} × credit{len(CANCEL_CREDITS)} = {total}회", flush=True)

    rows, done = [], 0
    for mk in mks:
        for oq in orders:
            for cap in caps:
                for lat in lats:
                    for cc in CANCEL_CREDITS:
                        try:
                            rows.append(run_one(a.paths, mk, cc, oq, cap, lat,
                                                a.horizon_sec))
                        except SystemExit:
                            raise
                        except Exception as e:
                            print(f"  ! {mk} cc={cc} {oq:.0f} {cap:.0f} {lat}ms: "
                                  f"{type(e).__name__}: {e}")
                        done += 1
                        if done % 25 == 0:
                            print(f"  ... {done}/{total}", flush=True)

    print("\n" + "=" * 118)
    print(f"대기열 시뮬레이션 · 지평 {a.horizon_sec}초 · 수수료 편도 {FEE*100:.3f}%")
    print("**이벤트 집합은 고정이고 큐 전진 규칙(cancel_credit)만 바뀐다.**")
    print("판정: 세 credit 의 부호가 갈리면 확정 불가.")
    print(f"단조: 진단 지표다(정리가 아니다). 체결 {MIN_FILLS_FOR_MONO}건 미만이면 판정하지 않는다.")
    print("-" * 118)
    hdr = "".join(f"{'cc='+str(c):>10}" for c in CANCEL_CREDITS)
    print(f"{'종목':<10}{'주문':>7}{'상한':>7}{'지연':>7}{hdr}"
          f"{'폭':>8}{'체결':>8}{'낡은':>7}{'재호가':>7}{'판정':>12}{'단조':>6}")
    for mk in mks:
        for oq in orders:
            for cap in caps:
                for lat in lats:
                    g = {r["cancel_credit"]: r for r in rows
                         if r["market"] == mk and r["order_krw"] == oq
                         and r["cap_krw"] == cap and r["latency_ms"] == lat}
                    if len(g) != len(CANCEL_CREDITS):
                        continue
                    vals = [g[c]["bp"] for c in CANCEL_CREDITS]
                    b = g[CANCEL_CREDITS[0]]
                    v, mono = verdict_of(vals, b['n_fills'])
                    print(f"{mk:<10}{oq/1e4:>5.0f}만{cap/1e4:>5.0f}만{lat:>5}ms"
                          + "".join(f"{x:>+10.2f}" for x in vals)
                          + f"{vals[-1]-vals[0]:>8.2f}{b['n_fills']:>8,}"
                          + f"{b['stale_frac']*100:>6.1f}%{b['requotes']:>7,}"
                          + f"{v:>12}{mono:>6}")

    # 손익 분해 — cancel_credit=0(보수)만
    print("\n" + "=" * 118)
    print("손익 분해 (cancel_credit=0, 원) — 강제청산이 수익 대부분을 먹는지 본다")
    print("-" * 118)
    print(f"{'종목':<10}{'주문':>7}{'상한':>7}{'지연':>7}{'스프레드':>12}{'수수료':>11}"
          f"{'재고손익':>12}{'청산비용':>11}{'순손익':>12}{'잔차':>9}{'bp':>8}")
    for r in rows:
        if r["cancel_credit"] != 0.0:
            continue
        print(f"{r['market']:<10}{r['order_krw']/1e4:>5.0f}만{r['cap_krw']/1e4:>5.0f}만"
              f"{r['latency_ms']:>5}ms{r['spread_krw']:>+12,.0f}{-r['fees_krw']:>+11,.0f}"
              f"{r['inventory_krw']:>+12,.0f}{-r['liq_cost_krw']:>+11,.0f}"
              f"{r['net_krw']:>+12,.0f}{r['resid_krw']:>+9,.0f}{r['bp']:>+8.2f}")

    if a.save:
        os.makedirs(os.path.dirname(a.save) or ".", exist_ok=True)
        json.dump(dict(params=vars(a), fee=FEE, cancel_credits=list(CANCEL_CREDITS),
                       rows=rows), open(a.save, "w"),
                  ensure_ascii=False, indent=1, default=float)
        print(f"\n저장 {a.save}")


if __name__ == "__main__":
    main()
