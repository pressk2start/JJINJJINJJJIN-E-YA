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
MAX_EVENT_GAP_MS = 60_000          # 이만큼 이벤트가 없으면 녹화 공백 → 세션 단절
MAX_BOOK_STALE_MS = 30_000         # 호가가 이보다 낡으면 그 체결로 채우지 않는다
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
        self.win = {1: (None, None), -1: (None, None)}   # 관측창 [floor, ceil]

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
        inwin = {1: set(), -1: set()}
        for side, old, new in ((1, self.bid, nb), (-1, self.ask, na)):
            floor = min(new) if new else None
            ceil = max(new) if new else None
            self.win[side] = (floor, ceil)
            if not old:
                continue
            for p, sz in old.items():
                # 관측창 판정은 **방향을 따진다.** 매수호가에서 '창 밖'은 관측
                # 범위보다 **더 깊은(낮은)** 가격이다. 반대로 최우선매수가보다
                # 높은 가격은 창 밖이 아니라 시장보다 좋은 **낡은 호가**이고,
                # 거기엔 남이 없으므로 관측된 것으로 다뤄야 한다.
                if floor is None or (side > 0 and p < floor) or (side < 0 and p > ceil):
                    continue                      # 관측창 **밖** — 판단 보류
                inwin[side].add(p)
                d = sz - new.get(p, 0.0)
                if d > 0:
                    drop[side][p] = d             # 전량 소멸도 여기 포함된다
        self.bid, self.ask, self.ts = nb, na, ts
        return drop, inwin

    def in_window(self, side, price):
        """방향을 따진 관측 여부. 매수는 floor 아래, 매도는 ceil 위가 '안 보임'.
        반대쪽(시장보다 좋은 가격)은 남이 없다는 뜻이라 관측된 것으로 본다."""
        lo, hi = self.win[side]
        if lo is None:
            return False
        return price >= lo if side > 0 else price <= hi


class MyOrder:
    """내 지정가 주문 하나. queue_ahead 는 **내 앞의 잔량**이다."""

    __slots__ = ("side", "price", "qty", "queue_ahead", "placed_ts", "level_size0",
                 "q0", "adv_exec", "adv_credit", "q_unknown")

    def __init__(self, side, price, qty, queue_ahead, ts, level_size0):
        self.side = side                 # +1 매수, -1 매도
        self.price = price
        self.qty = qty
        self.queue_ahead = queue_ahead
        self.placed_ts = ts
        self.level_size0 = level_size0   # 배치 시점 레벨 총잔량
        self.q0 = queue_ahead            # 최초 내 앞 잔량 — 체결 사유 분해용
        self.adv_exec = 0.0              # 체결로 전진한 몫
        self.adv_credit = 0.0            # 취소 credit 으로 전진한 몫
        self.q_unknown = False           # 관측창 밖 — 큐 상태 판단 불가


# ────────────────────────────────────────────────────────────── 시뮬

class Sim:
    def __init__(self, cancel_credit, order_krw, cap_krw, latency_ms, horizon_s,
                 init_cash_krw=None, init_asset_krw=None):
        """현물 자금제약 모형.

        업비트 KRW 현물에는 **공매도가 없다.** 매도 지정가를 걸려면 그만큼의
        코인을 이미 들고 있어야 하고, 매수 지정가를 걸려면 그만큼의 원화가 있어야
        한다. 이전 버전은 pos 를 0 에서 시작해 자유롭게 음수로 보냈다 — 보유하지
        않은 DOGE 를 판 것으로 계산했다는 뜻이고, 그러면 **필요자본을 모르는
        가상 long/short MM 의 수익률**이 나온다.

        여기서는 KRW 잔고(cash)와 코인 잔고(asset)를 따로 들고, 양방향 호가를
        걸 수 있게 초기 재고(target)를 준다. 재고 상한은 **target 대비 편차**에
        건다 — target 자체는 전략이 아니라 시장 노출이다.
        """
        self.cc = cancel_credit
        self.order_krw = order_krw
        self.cap_krw = cap_krw
        self.latency = latency_ms
        self.H = horizon_s * 1000
        # 초기 자본. 기본값은 '상한만큼의 원화 + 상한만큼의 코인' —
        # 양방향으로 상한까지 갈 수 있는 최소 구성이다.
        self.init_cash = init_cash_krw if init_cash_krw is not None else cap_krw
        self.init_asset_krw = (init_asset_krw if init_asset_krw is not None
                               else cap_krw)
        self.cash = self.init_cash
        self.asset = None            # 첫 mid 에서 수량으로 환산
        self.target = None
        self.mid0 = None
        self.n_reject_cash = 0
        self.n_reject_asset = 0
        self.n_clip_cap = 0
        self.bk = Book()
        self.orders = {1: None, -1: None}
        self.pending = {1: None, -1: None}     # 재호가 예정 시각
        self.last_mid = None
        self.inv_t = 0.0          # 시간가중 재고 적분 (원·ms)
        self.cap_t = 0.0          # 상한 99% 이상 체류 시간 (ms)
        self.span_t = 0.0
        self.last_t = None
        self.mkt_turn = 0.0       # 시장 전체 체결대금 — 내 흐름 점유율 계산용
        self.fills = []                        # (ts, side, price, qty, mid_at_fill, stale)
        self.n_stale = 0
        self.exec_acc = defaultdict(float)     # (side, price) -> 직전 transition 이후 체결량
        self.stat = defaultdict(float)
        self.fill_log = []                     # 사유 분해 · drift 계측용
        self.last_ev = None
        self.seg = 0                           # 녹화 세션 번호 — 라벨이 공백을 못 넘게

    @property
    def pos(self):
        """target 대비 재고 편차. 상한은 이 값에 건다."""
        if self.asset is None or self.target is None:
            return 0.0
        return self.asset - self.target

    def _init_balances(self, mid):
        if self.asset is None and mid:
            self.mid0 = mid
            self.target = self.init_asset_krw / mid
            self.asset = self.target

    def equity(self, mid):
        if self.asset is None or not mid:
            return self.cash
        return self.cash + self.asset * mid

    # ---- 큐 전진

    def _advance(self, side, price, executed, observed_drop):
        """내 앞 큐를 줄인다. 반환: 나에게 돌아온 체결량."""
        o = self.orders[side]
        if o is None or o.price != price:
            return 0.0
        # ① 체결로 설명된 감소는 FIFO 로 내 앞부터 소진 — 모든 시나리오 공통
        take = min(executed, o.queue_ahead)
        o.queue_ahead -= take
        o.adv_exec += take
        mine = max(0.0, executed - take)        # 내 앞이 비면 나에게 온다
        # ② 미설명 감소(취소 또는 관측 오차)의 처리 = 시나리오 차이
        unexp = max(0.0, observed_drop - executed)
        # 관측창 밖으로 밀린 주문은 큐 상태를 알 수 없다. 보수적으로 전진시키지
        # 않는다 — 여기서 credit 을 주면 '안 보이니 앞이 비었을 것'이라는 낙관이다.
        if unexp > 0 and o.queue_ahead > 0 and not o.q_unknown:
            credit = unexp * self.cc
            credit = min(credit, o.queue_ahead)
            o.queue_ahead -= credit
            o.adv_credit += credit
            self.stat["queue_credit"] += credit
        return min(mine, o.qty)

    # ---- 체결 기록

    def _mark(self, mid):
        """직전 mark 이후의 재고 손익을 확정한다. 이게 adverse selection 이 들어오는 통로다."""
        if mid is None:
            return
        if self.last_mid is not None and self.asset is not None:
            d = mid - self.last_mid
            self.stat["inventory"] += self.pos * d          # 전략 재고 편차 손익
            self.stat["market"] += self.target * d          # 기초재고의 시장 노출
        self.last_mid = mid

    def _cap_fill(self, side, price, qty):
        """체결 시점에도 제약을 건다. 호가 배치 때만 보면 이미 걸린 주문이
        나중에 체결되며 상한을 넘어간다(예: 재고 195만 + 예전 30만 주문 → 225만).
        상한은 배치 게이트가 아니라 **상태 제약**이다."""
        if qty <= 0:
            return 0.0
        # ① 재고 상한 (target 대비 편차)
        if self.cap_krw:
            room = self.cap_krw / price - side * self.pos
            if room < qty:
                self.n_clip_cap += 1
                qty = max(0.0, room)
        # ② 현물 자금제약 — 공매도 없음, 원화 초과지출 없음
        if side > 0:
            afford = self.cash / (price * (1 + FEE))
            if afford < qty:
                self.n_reject_cash += 1
                qty = max(0.0, afford)
        else:
            if self.asset is not None and self.asset < qty:
                self.n_reject_asset += 1
                qty = max(0.0, self.asset)
        return qty

    def _fill(self, ts, side, price, qty, stale):
        mid = self.bk.mid()
        self._init_balances(mid)
        qty = self._cap_fill(side, price, qty)
        if qty <= 0:
            return
        self._mark(mid)                      # 체결 **직전**까지의 재고 손익을 먼저 확정
        self.cash -= side * qty * price + qty * price * FEE
        self.asset += side * qty
        o0 = self.orders[side]
        cshare = 0.0
        if o0 is not None and o0.q0 > 0:
            cshare = o0.adv_credit / o0.q0
        self.fills.append((ts, side, price, qty, mid, stale))
        self.fill_log.append(dict(ts=ts, side=side, price=price, qty=qty, mid=mid,
                                  stale=stale, credit_share=cshare, seg=self.seg,
                                  q_unknown=bool(o0.q_unknown) if o0 else False))
        if stale:
            self.n_stale += 1
            if mid is not None:
                # 낡은 호가로 맞은 체결의 즉시 손실 = 중간가 대비 불리한 몫
                self.stat["stale_loss"] += max(0.0, -side * (mid - price) * qty)
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
        """상한·현물 잔고 어느 하나라도 못 채우면 호가를 걸지 않는다."""
        px = self.bk.best(side)
        if px is None:
            return False
        q = self.order_krw / px
        if self.cap_krw and abs((self.pos + side * q) * px) > self.cap_krw:
            return False
        if side > 0:
            return self.cash >= self.order_krw * (1 + FEE)
        return self.asset is not None and self.asset >= q

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

    def _gap_guard(self, ts):
        """녹화 공백을 넘어 큐 상태를 이어가면 안 된다.

        7일 수집에서 재접속은 반드시 발생한다. 공백 전 주문·큐·체결 누적을 공백
        후로 끌고 가면 '몇 시간 전에 걸어둔 호가가 아직 큐 맨 앞'이 되어버린다.
        ws_features 에 넣은 GAP_RESET 과 같은 규율을 여기에도 독립적으로 건다.
        """
        if self.last_ev is not None and (ts - self.last_ev) > MAX_EVENT_GAP_MS:
            self.bk = Book()
            self.orders = {1: None, -1: None}
            self.pending = {1: None, -1: None}
            self.exec_acc.clear()
            self.last_mid = None
            self.last_t = None
            self.seg += 1
            self.stat["gap_resets"] += 1
        self.last_ev = ts

    def _tick_clock(self, ts):
        """재고 궤적을 시간가중으로 적분한다. bp 만 보면 규모 정합성을 놓친다."""
        if self.last_t is not None:
            dt = ts - self.last_t
            if 0 <= dt < 3_600_000:                 # 녹화 공백은 적분하지 않는다
                px = self.bk.mid() or 0.0
                self.inv_t += abs(self.pos) * px * dt
                self.span_t += dt
                if self.cap_krw and abs(self.pos) * px >= self.cap_krw * 0.99:
                    self.cap_t += dt
        self.last_t = ts

    def on_trade(self, ts, m):
        p = m.get("trade_price")
        v = m.get("trade_volume")
        if not p or not v:
            return
        self._gap_guard(ts)
        self._tick_clock(ts)
        self.mkt_turn += p * v
        # ask_bid = 테이커 방향. BID=공격적 매수 → 매도호가 소진 → MM 의 매도(-1) 체결
        taker_buy = (m.get("ask_bid") == "BID")
        hit_side = -1 if taker_buy else 1        # 소진되는 호가 쪽 = MM 이 걸어둔 쪽
        self.exec_acc[(hit_side, p)] += v
        o = self.orders[hit_side]
        if o is not None and o.price == p:
            if self.bk.ts is None or (ts - self.bk.ts) > MAX_BOOK_STALE_MS:
                # 호가가 너무 낡았다. 이 시점의 큐 상태를 신뢰할 수 없으므로
                # 체결시키지 않고 센다. 채우면 근거 없는 수익이 된다.
                self.stat["skip_book_stale"] += 1
                return
            best = self.bk.best(hit_side)
            stale = (best is not None and best != p)   # 낡은 호가로 맞은 체결
            got = self._advance(hit_side, p, v, v)     # 체결분은 전부 설명됨
            if got > 0:
                self._fill(ts, hit_side, p, got, stale)

    def on_book(self, ts, m):
        self._gap_guard(ts)
        self._tick_clock(ts)
        drop, _inwin = self.bk.apply(m, ts)
        self._init_balances(self.bk.mid())
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
            # ⚠ 예전 버전은 여기서 queue_ahead 를 0 으로 순간이동시켰다. 그러면
            #   부분취소 → credit=0, 전량취소 → credit=1.0 이 되어 credit 함수가
            #   **불연속**이 되고 cc=0 이 더 이상 하한이 아니게 된다. 하필 그
            #   순간이동이 호가가 얇아진 순간에만 일어나므로 낙관 편향이 크다.
            #   제거했다 — 관측창 **안**에서의 전량 소멸은 Book.apply 가 이미
            #   `d = 전량` 으로 정상 drop 경로에 실어 보내므로 부분취소와 똑같이
            #   cancel_credit 을 탄다. 연속이 되고 cc=0 이 진짜 하한이 된다.
            #   관측창 **밖**으로 밀린 경우만 따로 표시한다(판단 불가).
            if not self.bk.in_window(side, o.price):
                if not o.q_unknown:
                    o.q_unknown = True
                    self.stat["window_exit"] += 1
            else:
                if o.q_unknown:
                    o.q_unknown = False
                if o.price not in (self.bk.bid if side > 0 else self.bk.ask):
                    self.stat["level_gone"] += 1
        self.exec_acc.clear()
        self._requote_check(ts)

    # ---- 마감

    def liquidate(self, ts):
        """잔여 재고를 **테이커가로** 청산한다. 반대편 호가를 쳐야 한다."""
        if self.asset is None or abs(self.pos) < 1e-12:
            return
        # 롱이면 매수호가(bid)에 던지고, 숏이면 매도호가(ask)를 친다. 테이커가다.
        px = self.bk.best(1) if self.pos > 0 else self.bk.best(-1)
        if px is None:
            px = self.bk.mid()
        if px is None:
            return
        q = min(abs(self.pos), self.asset if self.pos > 0 else float("inf"))
        if q <= 1e-12:
            return
        sgn = 1 if self.pos > 0 else -1       # +1 = 롱 편차 청산(매도)
        mid = self.bk.mid()
        self._mark(mid)                       # 청산 직전까지 재고 손익 확정
        self.cash += sgn * q * px - q * px * FEE
        self.asset -= sgn * q
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


def mid_at(track, ts, max_slip_ms, seg):
    """(ts 이하 마지막 mid) 를 준다. 단 **같은 세그먼트**이고 목표 시각을
    max_slip 이상 넘지 않을 때만. 안 그러면 공백 건너편의 오래된 mid 를
    30초 뒤 값으로 쓰게 된다 — ws_features.add_labels 에서 잡았던 그 버그다."""
    import bisect
    tss = track["ts"]
    i = bisect.bisect_right(tss, ts) - 1
    if i < 0:
        return None
    if track["seg"][i] != seg:
        return None
    if ts - tss[i] > max_slip_ms:
        return None
    return track["mid"][i]


def drift_report(fill_log, track, horizons_ms, max_slip_ms):
    """체결 후 mid 이동을 **체결 사유별로** 낸다.

    레벨이 전량 취소로 비는 순간은 '다들 뭔가 봤다'는 뜻이고, 그때 나만 남아
    체결되는 건 MM 이 당하는 전형적 형태다. 즉 credit 으로 큐를 통과해 체결된
    군의 drift 가 유의하게 불리하지 않다면 adverse selection 이 계측되지
    않고 있다는 신호다. 그래서 두 군을 나눠 본다.
      · FIFO   : 순전히 체결로 큐가 소진되어 체결 (credit_share == 0)
      · CREDIT : 취소 credit 으로 큐를 통과해 체결 (credit_share > 0)
    """
    out = {}
    for grp, pred in (("FIFO", lambda f: f["credit_share"] <= 1e-12),
                      ("CREDIT", lambda f: f["credit_share"] > 1e-12)):
        sel = [f for f in fill_log if pred(f) and f["mid"]]
        g = {"n": len(sel), "krw": sum(f["qty"] * f["price"] for f in sel)}
        for h in horizons_ms:
            num = den = 0.0
            n = 0
            for f in sel:
                m1 = mid_at(track, f["ts"] + h, max_slip_ms, f["seg"])
                if not m1:
                    continue
                # MM 관점: 산 뒤 오르면 이득, 판 뒤 내리면 이득
                num += f["side"] * (m1 - f["mid"]) / f["mid"] * 1e4 * f["qty"] * f["price"]
                den += f["qty"] * f["price"]
                n += 1
            g[f"drift_{h//1000}s_bp"] = (num / den) if den > 0 else float("nan")
            g[f"n_{h//1000}s"] = n
        out[grp] = g
    return out


def run_one(paths, market, cancel_credit, order_krw, cap_krw, latency_ms, horizon_s,
            init_cash_krw=None, init_asset_krw=None, drift_h=(1, 5, 30)):
    sim = Sim(cancel_credit, order_krw, cap_krw, latency_ms, horizon_s,
              init_cash_krw, init_asset_krw)
    track = {"ts": [], "mid": [], "seg": []}
    max_inv = 0.0
    daily = defaultdict(lambda: {"equity0": None, "equity1": None})
    last_ts = None
    for ts, m in stream(paths, market):
        last_ts = ts
        if m["type"] == "orderbook":
            sim.on_book(ts, m)
            mm = sim.bk.mid()
            if mm:
                sim._init_balances(mm)
                sim._mark(mm)
                track["ts"].append(ts)
                track["mid"].append(mm)
                track["seg"].append(sim.seg)
                max_inv = max(max_inv, abs(sim.pos) * mm)
                d = datetime.datetime.utcfromtimestamp(ts / 1000).strftime("%m-%d")
                e = sim.equity(mm)
                if daily[d]["equity0"] is None:
                    daily[d]["equity0"] = e
                daily[d]["equity1"] = e
        else:
            sim.on_trade(ts, m)
    if last_ts is not None:
        sim.liquidate(last_ts)

    mid_end = sim.bk.mid() or sim.mid0
    turn = sim.stat["turn"]
    spread, fees = sim.stat["spread"], sim.stat["fees"]
    inv, mkt = sim.stat["inventory"], sim.stat["market"]
    liq = sim.stat.get("liq_cost", 0.0)

    req_cap = sim.init_cash + sim.init_asset_krw
    net = sim.equity(mid_end) - req_cap        # 기초재고의 시장 이동까지 포함한 실제 손익
    net_ex_mkt = net - mkt                     # 시장 노출을 뺀 전략 손익
    resid = net - (spread + inv + mkt - fees)

    drift = drift_report(sim.fill_log, track, [h * 1000 for h in drift_h],
                         max_slip_ms=max(5000, horizon_s * 500))
    n_credit = sum(1 for f in sim.fill_log if f["credit_share"] > 1e-12)
    dj = {d: v["equity1"] - v["equity0"] for d, v in daily.items()
          if v["equity0"] is not None}

    return dict(market=market, cancel_credit=cancel_credit, latency_ms=latency_ms,
                order_krw=order_krw, cap_krw=cap_krw,
                n_fills=len(sim.fills), n_stale=sim.n_stale,
                stale_frac=(sim.n_stale / len(sim.fills)) if sim.fills else float("nan"),
                requotes=int(sim.stat["requotes"]),
                level_gone=int(sim.stat["level_gone"]),
                window_exit=int(sim.stat["window_exit"]),
                gap_resets=int(sim.stat["gap_resets"]),
                skip_book_stale=int(sim.stat["skip_book_stale"]),
                clip_cap=sim.n_clip_cap, reject_cash=sim.n_reject_cash,
                reject_asset=sim.n_reject_asset,
                queue_credit=sim.stat["queue_credit"], n_fills_credit=n_credit,
                credit_fill_frac=(n_credit / len(sim.fill_log)) if sim.fill_log
                else float("nan"),
                turn_krw=turn, spread_krw=spread, fees_krw=fees,
                inventory_krw=inv, market_krw=mkt,
                stale_loss_krw=sim.stat["stale_loss"],
                liq_cost_krw=liq, liq_krw=sim.stat.get("liq_krw", 0.0),
                # ── 자본·규모 정합성 (bp 만 보면 안 된다) ──────────────
                required_capital_krw=req_cap,
                net_krw=net, net_ex_market_krw=net_ex_mkt,
                ret_on_capital=(net / req_cap) if req_cap else float("nan"),
                ret_on_capital_ex_mkt=(net_ex_mkt / req_cap) if req_cap else float("nan"),
                max_inv_krw=max_inv, mkt_turn_krw=sim.mkt_turn,
                flow_share=(turn / sim.mkt_turn) if sim.mkt_turn > 0 else float("nan"),
                avg_inv_krw=(sim.inv_t / sim.span_t) if sim.span_t > 0 else 0.0,
                at_cap_frac=(sim.cap_t / sim.span_t) if sim.span_t > 0 else 0.0,
                daily_krw=dj, drift=drift, resid_krw=resid,
                bp=(net_ex_mkt / turn * 1e4) if turn > 0 else float("nan"))


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
    ap.add_argument("--init-cash-krw", type=float, default=None,
                    help="초기 원화. 기본 = 재고상한")
    ap.add_argument("--init-asset-krw", type=float, default=None,
                    help="초기 코인(원화 환산). 기본 = 재고상한. "
                         "현물은 공매도가 없어 이게 있어야 매도 호가를 걸 수 있다")
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
                                                a.horizon_sec, a.init_cash_krw,
                                                a.init_asset_krw))
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

    print("\n" + "=" * 126)
    print("자본·규모 정합성 (cancel_credit=0) — bp 가 아니라 이쪽이 판정 기준이다")
    print("필요자본 = 초기 원화 + 초기 코인. 현물은 공매도가 없어 양방향 호가에 둘 다 필요하다.")
    print("-" * 126)
    print(f"{'종목':<9}{'주문':>6}{'상한':>6}{'지연':>6}{'순손익':>12}{'시장제외':>12}"
          f"{'필요자본':>11}{'자본수익':>9}{'최대재고':>11}{'상한체류':>8}"
          f"{'낡은손실':>10}{'청산손실':>10}{'흐름점유':>9}")
    for r in rows:
        if r["cancel_credit"] != 0.0:
            continue
        print(f"{r['market']:<9}{r['order_krw']/1e4:>4.0f}만{r['cap_krw']/1e4:>4.0f}만"
              f"{r['latency_ms']:>4}ms{r['net_krw']:>+12,.0f}{r['net_ex_market_krw']:>+12,.0f}"
              f"{r['required_capital_krw']/1e4:>9,.0f}만{r['ret_on_capital_ex_mkt']*100:>+8.2f}%"
              f"{r['max_inv_krw']:>11,.0f}{r['at_cap_frac']*100:>7.1f}%"
              f"{-r['stale_loss_krw']:>+10,.0f}{-r['liq_cost_krw']:>+10,.0f}"
              f"{r['flow_share']*100:>8.3f}%")

    print("\n" + "=" * 126)
    print("체결 사유 분해 · 체결 후 mid drift (cancel_credit=0)")
    print("CREDIT 군의 drift 가 유의하게 불리하지 않으면 adverse selection 이 계측되지 않는 것이다.")
    print("-" * 126)
    print(f"{'종목':<9}{'주문':>6}{'지연':>6}{'군':>8}{'체결':>9}{'대금':>11}"
          f"{'drift1s':>10}{'drift5s':>10}{'drift30s':>10}")
    for r in rows:
        if r["cancel_credit"] != 0.0:
            continue
        for g in ("FIFO", "CREDIT"):
            d = r["drift"].get(g, {})
            if not d.get("n"):
                continue
            print(f"{r['market']:<9}{r['order_krw']/1e4:>4.0f}만{r['latency_ms']:>4}ms"
                  f"{g:>8}{d['n']:>9,}{d['krw']/1e4:>9,.0f}만"
                  f"{d.get('drift_1s_bp', float('nan')):>+10.2f}"
                  f"{d.get('drift_5s_bp', float('nan')):>+10.2f}"
                  f"{d.get('drift_30s_bp', float('nan')):>+10.2f}")

    print("\n" + "=" * 126)
    print("모형 계기 (cancel_credit=0) — 이 값들이 크면 결과보다 먼저 이걸 봐야 한다")
    print("-" * 126)
    print(f"{'종목':<9}{'주문':>6}{'지연':>6}{'창밖이탈':>9}{'레벨소멸':>9}{'공백리셋':>9}"
          f"{'낡은호가스킵':>13}{'상한클립':>9}{'현금부족':>9}{'코인부족':>9}{'credit체결비':>13}")
    for r in rows:
        if r["cancel_credit"] != 0.0:
            continue
        print(f"{r['market']:<9}{r['order_krw']/1e4:>4.0f}만{r['latency_ms']:>4}ms"
              f"{r['window_exit']:>9,}{r['level_gone']:>9,}{r['gap_resets']:>9,}"
              f"{r['skip_book_stale']:>13,}{r['clip_cap']:>9,}{r['reject_cash']:>9,}"
              f"{r['reject_asset']:>9,}{r['credit_fill_frac']*100:>12.1f}%")

    if a.save:
        os.makedirs(os.path.dirname(a.save) or ".", exist_ok=True)
        json.dump(dict(params=vars(a), fee=FEE, cancel_credits=list(CANCEL_CREDITS),
                       rows=rows), open(a.save, "w"),
                  ensure_ascii=False, indent=1, default=float)
        print(f"\n저장 {a.save}")


if __name__ == "__main__":
    main()
