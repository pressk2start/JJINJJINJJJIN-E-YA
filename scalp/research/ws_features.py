# -*- coding: utf-8 -*-
"""ws_features.py — ws_recorder 원자료 → 특징량 프레임 (event-time 재생).

이 파일이 피하려는 세 가지 함정
================================
병렬 세션의 동명 파일에 아래 셋이 모두 있었다. 같은 실수를 반복하지 않기 위해
설계 시점에 명시적으로 막는다.

**(1) look-ahead — 이벤트를 상태에 반영한 뒤 과거 grid를 emit하면 안 된다.**
  잘못된 순서:
      apply(event at T)  →  while g <= T: emit(g)
  마지막 frame이 12:00:00이고 다음 이벤트가 12:00:01.23이면
  01.23의 정보가 01.00 frame에 들어간다. 이벤트 간격이 5초면 그 하나가
  그 사이 모든 1초 frame에 역으로 새어든다.
  올바른 순서 (이 파일):
      while next_emit < T: emit(next_emit)   ← T 이전 grid를 **기존 상태로** 먼저 내보낸다
      apply(event at T)
  경계 규약: **event_ts ≤ grid_ts 인 이벤트만 그 grid frame에 포함된다.**
  (그래서 emit 조건이 `<` 이고 `<=` 가 아니다. event_ts == g 인 이벤트는
   다음 이벤트가 g를 넘어설 때 g frame에 포함된 채로 나간다.)

**(2) receive-time이 아니라 exchange-time이 연구 시계다.**
  `_rx` 로 정렬하면 네트워크 jitter가 시장 순서를 바꾼다:
      거래소  100.000 OB → 100.010 trade
      수신    100.090 trade → 100.105 OB     ← 순서가 뒤집힘
  이 파일의 primary clock:
      orderbook → `timestamp`
      trade     → `trade_timestamp` (체결 시각. `timestamp` 와 실측 49ms 차이)
  동일 ms 동률은 `_seq`(수신 순번)로 결정적 정렬. `_rx` 는 **latency/QC 전용**이다.

**(3) depletion은 가격별로 추적해야 한다.**
  최우선호가 잔량 감소를 통째로 누적해놓고 "현재 best price의 체결량"만 빼면,
  그 사이 호가가 100 → 99 → 100 으로 움직인 경우 다른 가격의 감소분이
  체결로 설명되지 못해 취소로 잡힌다 → cancel_ratio가 1 쪽으로 편향된다.
  이 파일은 `depl[price] += Δqty` / `exec[price] += volume` 으로 **가격별 매칭**한 뒤
  `Σ max(depl[p] − exec[p], 0)` 을 쓴다.

  그리고 이름을 `cancel_ratio` 가 아니라 **`unexplained_depl_ratio`** 로 둔다.
  오더북 스냅샷만으로는 취소와 중간 replenishment(감소 후 재보충)를 식별할 수 없다.
  "설명되지 않은 감소"까지가 이 데이터로 말할 수 있는 전부다.

출력: 마켓별 고정 격자(기본 1초) 프레임 JSONL.gz
  라벨(`fwd_*_bp`)의 기준가는 **mid** 다 — 진입 가능한 가격이 아니다.
  체결가 가정(테이커면 ask 매수/bid 매도)은 비용모델에서 따로 적용한다.
  둘을 섞으면 신호가 없는 건지 비용이 큰 건지 구분할 수 없다.

사용:
  python3 ws_features.py "data/ws/2026-08-23/*.jsonl.gz" --grid 1.0 --labels 30,60,120
  python3 ws_features.py "data/ws/**/*.jsonl.gz" --market KRW-XRP --out feat_xrp.jsonl.gz
"""
import os, sys, json, gzip, glob, math, heapq, argparse
from collections import defaultdict, deque

MAX_STALE_SEC = 60.0        # 호가가 이보다 낡은 프레임은 만들지 않는다 (아래 설명)
GAP_RESET_SEC = 60.0     # 이만큼 이벤트가 없으면 녹화 공백으로 보고 상태를 끊는다
LATENESS_MS = 2000
LAST_QC = {}                # 직전 replay() 의 품질 지표 (지각 폐기 수 등)          # 수신 지연 jitter 흡수용 워터마크 (실측 p99 443ms의 여유배)
OB_HIST_SEC = 60.0
TR_HIST_SEC = 60.0
TAKER_BUY = "BID"           # 테이커가 매도호가를 침 = 매수 체결 → ask 큐 소진
TAKER_SELL = "ASK"          # 테이커가 매수호가를 침 = 매도 체결 → bid 큐 소진


def event_ts_ms(m):
    """primary clock = 거래소 시각. trade 는 체결시각을 쓴다."""
    if m.get("type") == "trade":
        return m.get("trade_timestamp") or m.get("timestamp")
    return m.get("timestamp")


# ------------------------------------------------------------------
def obi(units, k):
    """상위 k호가 **금액가중** 불균형 ∈ [-1,+1]. 수량가중은 종목 간 비교 불가."""
    u = units[:k]
    b = sum(x["bid_price"] * x["bid_size"] for x in u)
    a = sum(x["ask_price"] * x["ask_size"] for x in u)
    t = b + a
    return (b - a) / t if t > 0 else 0.0


def microprice(u0):
    bp, bs = u0["bid_price"], u0["bid_size"]
    ap, asz = u0["ask_price"], u0["ask_size"]
    t = bs + asz
    return (bp + ap) / 2.0 if t <= 0 else (bp * asz + ap * bs) / t


class Book:
    """호가 상태. 감소분은 **스냅샷 transition 단위**로 반환한다 (프레임 단위 누적 아님).

    왜 transition 단위인가 — 프레임 전체로 뭉치면 앞선 체결이 나중 감소를 잘못 설명한다:
        0.10s  bid 100 = 50
        0.20s  100 에서 매도체결 20
        0.30s  bid 100 = 50      ← 즉시 재보충. 순감소 없음
        0.80s  bid 100 = 30      ← 이후 취소 20
        1.00s  frame
      프레임 단위로 합치면 depl@100=20 / exec@100=20 → unexplained 0 이 된다.
      그러나 그 체결은 이미 재보충으로 흡수됐고, 나중 감소는 체결과 무관하다. 정답은 20.
      → 각 transition (prev_ob_ts, cur_ob_ts] 마다 그 구간의 같은 가격 체결로만 상계한다.
    """

    def __init__(self):
        self.units = None
        self.ts = None                      # 마지막 orderbook event_ts (초)
        self.pb = {}                        # price -> size (직전 스냅샷, 매수)
        self.pa = {}
        self.obi_hist = deque()             # (ts, obi5)

    def apply(self, m, ts):
        """스냅샷 반영. 반환: (depl_bid, depl_ask, top_b, top_a, t0, t1)
        depl_* = {price: 감소수량} — **이번 transition 에서만**. 없으면 빈 dict."""
        u = m.get("orderbook_units") or []
        if not u:
            return {}, {}, None, None, None, None
        nb = {x["bid_price"]: x["bid_size"] for x in u if x["bid_price"] > 0}
        na = {x["ask_price"]: x["ask_size"] for x in u if x["ask_price"] > 0}
        d_b, d_a = {}, {}
        top_b = max(self.pb) if self.pb else None
        top_a = min(self.pa) if self.pa else None
        t0 = self.ts
        # 관측 창 밖으로 밀려난 가격은 세지 않는다 — 사라진 게 아니라 안 보이는 것이다.
        floor_b = min(nb) if nb else None
        ceil_a = max(na) if na else None
        if self.pb and nb:
            for pr, sz in self.pb.items():
                if floor_b is None or pr < floor_b:
                    continue
                d = sz - nb.get(pr, 0.0)
                if d > 0:
                    d_b[pr] = d
        if self.pa and na:
            for pr, sz in self.pa.items():
                if ceil_a is None or pr > ceil_a:
                    continue
                d = sz - na.get(pr, 0.0)
                if d > 0:
                    d_a[pr] = d
        self.pb, self.pa = nb, na
        self.units, self.ts = u, ts
        self.obi_hist.append((ts, obi(u, 5)))
        while self.obi_hist and ts - self.obi_hist[0][0] > OB_HIST_SEC:
            self.obi_hist.popleft()
        return d_b, d_a, top_b, top_a, t0, ts

    def d_obi(self, g, win):
        """g 시점 기준 win초 전 대비 OBI 변화. 그 시점 관측이 없으면 None."""
        if not self.obi_hist:
            return None
        cur = self.obi_hist[-1][1]
        past = None
        for t, v in self.obi_hist:
            if t <= g - win:
                past = v
            else:
                break
        return None if past is None else cur - past


class Flow:
    """체결 흐름 + **가격별** 체결량 누적 (프레임 단위)."""

    def __init__(self):
        self.tr = deque()                   # (ts, krw, side, vol, price)

    def apply(self, m, ts):
        px = float(m["trade_price"]); vol = float(m["trade_volume"])
        side = m.get("ask_bid")
        self.tr.append((ts, px * vol, side, vol, px))
        while self.tr and ts - self.tr[0][0] > TR_HIST_SEC:
            self.tr.popleft()

    def win(self, g, w):
        return [x for x in self.tr if g - w < x[0] <= g]

    def exec_in(self, t0, t1, side):
        """(t0, t1] 구간의 가격별 체결 수량.
        side='bid' = 매수호가를 친 체결(테이커 매도, ask_bid="ASK") → bid 큐를 줄인다.
        t0 None 이면 첫 스냅샷이라 직전 구간이 없다는 뜻 → 빈 결과."""
        if t0 is None:
            return {}
        want = TAKER_SELL if side == "bid" else TAKER_BUY
        out = defaultdict(float)
        for ts, krw, sd, qty, px in self.tr:
            if t0 < ts <= t1 and sd == want:
                out[px] += qty
        return out

    def imbalance(self, g, w):
        s = self.win(g, w)
        b = sum(x[1] for x in s if x[2] == TAKER_BUY)
        a = sum(x[1] for x in s if x[2] == TAKER_SELL)
        t = b + a
        return ((b - a) / t if t > 0 else None), b, a, len(s)

    def large_frac(self, g, w):
        s = self.win(g, w)
        tot = sum(x[1] for x in s)
        return (max(x[1] for x in s) / tot) if (s and tot > 0) else None


class DeplAcc:
    """프레임 단위 누산기. **transition 마다 이미 상계된 값**을 더한다.

    핵심: 상계(가격별 max(depl−exec,0))는 각 스냅샷 transition 안에서 끝낸다.
    프레임에는 그 결과만 누적한다. 프레임 전체로 depl 과 exec 를 각각 뭉쳐서
    마지막에 상계하면, 앞선 체결이 나중 감소를 설명해버린다 (Book 독스트링 §참조)."""

    def __init__(self):
        self.depl = 0.0; self.unexp = 0.0
        self.depl_top = 0.0; self.unexp_top = 0.0

    def add(self, depl_map, exec_map, top_price):
        for pr, q in depl_map.items():
            un = max(q - exec_map.get(pr, 0.0), 0.0)
            self.depl += q; self.unexp += un
            if top_price is not None and pr == top_price:
                self.depl_top += q; self.unexp_top += un

    def ratio(self):
        r = (self.unexp / self.depl) if self.depl > 0 else None
        rt = (self.unexp_top / self.depl_top) if self.depl_top > 0 else None
        return r, self.depl, rt, self.depl_top


def frame(code, g, bk, fl, cnt, accb, acca):
    if not bk.units:
        return None
    u0 = bk.units[0]
    bid, ask = u0["bid_price"], u0["ask_price"]
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    r = {"ts": round(g, 3), "market": code, "bid": bid, "ask": ask, "mid": mid,
         "n_ob": cnt[0], "n_trade": cnt[1],
         "book_age_ms": round((g - bk.ts) * 1000.0, 1) if bk.ts else None}
    r["obi1"] = obi(bk.units, 1); r["obi5"] = obi(bk.units, 5); r["obi15"] = obi(bk.units, 15)
    r["spread_bp"] = (ask - bid) / mid * 1e4
    r["micro_dev_bp"] = (microprice(u0) - mid) / mid * 1e4
    r["depth_bid_krw"] = sum(x["bid_price"] * x["bid_size"] for x in bk.units[:5])
    r["depth_ask_krw"] = sum(x["ask_price"] * x["ask_size"] for x in bk.units[:5])
    r["d_obi_1s"] = bk.d_obi(g, 1.0); r["d_obi_5s"] = bk.d_obi(g, 5.0)

    rb, tb, rbt, tbt = accb.ratio()
    ra, ta, rat, tat = acca.ratio()
    r["unexplained_depl_ratio_bid"] = rb
    r["unexplained_depl_ratio_ask"] = ra
    r["depl_qty_bid"] = tb; r["depl_qty_ask"] = ta
    # 최우선호가 한정 — 벽의 진위 판별에 쓸 값은 이쪽이다
    # (전 호가단 합산은 깊은 단의 일상 갱신 때문에 구조적으로 1 에 붙는다)
    r["unexplained_depl_ratio_bid_top"] = rbt
    r["unexplained_depl_ratio_ask_top"] = rat
    r["depl_qty_bid_top"] = tbt; r["depl_qty_ask_top"] = tat

    for w in (1, 3, 5, 10):
        imb, b, a, n = fl.imbalance(g, float(w))
        r[f"ti_{w}s"] = imb
        if w in (1, 5):
            r[f"signed_krw_{w}s"] = b - a
            r[f"arr_{w}s"] = n / float(w)
        if w == 5:
            r["aggr_buy_ratio_5s"] = (b / (b + a)) if (b + a) > 0 else None
    a1, a5 = r.get("arr_1s"), r.get("arr_5s")
    r["arr_accel"] = (a1 - a5) if (a1 is not None and a5 is not None) else None
    r["large_frac_5s"] = fl.large_frac(g, 5.0)
    return r


class MarketState:
    """⚠ 낡은 호가 프레임 문제
      격자는 이벤트가 올 때마다 그 이전 grid 를 채운다. 그런데 재접속·수집 중단·
      한산 구간처럼 오랫동안 호가 갱신이 없으면, **같은 스냅샷의 복사본**이 초당 하나씩
      계속 찍힌다. 실측: 두 스모크 세션(116초+30초) 사이 공백 때문에 전체 61,985 프레임 중
      **96%가 낡은 복사본**이었고 book_age 중앙값이 2.6시간이었다.
      그대로 분석에 넣으면 표본 수가 허구로 부풀고 자기상관이 극단적으로 커진다.
      → book_age > max_stale_sec 인 프레임은 **만들지 않고 카운트만 한다.**
        (book_age_ms 는 남은 프레임에도 기록되므로 후처리에서 더 좁게 거를 수 있다.)"""

    def __init__(self, code, grid, max_stale=MAX_STALE_SEC):
        self.code = code; self.grid = grid; self.max_stale = max_stale
        self.n_stale = 0
        self.bk = Book(); self.fl = Flow()
        self.last_ev = None                 # 직전 이벤트 시각 — 공백 감지용
        self.n_reset = 0
        self.cnt = [0, 0]
        self.next_emit = None
        self.rows = []
        self.last_ts = None
        self.n_late = 0            # 워터마크 허용을 넘어 늦게 도착한 이벤트 수
        self.accb = DeplAcc(); self.acca = DeplAcc()

    def step(self, m, ts):
        """이벤트 1건 처리. **emit 먼저, apply 나중** — look-ahead 차단.

        ts 가 직전 처리분보다 과거면 워터마크 허용(lateness_ms)을 넘어선 지각 도착이다.
        그대로 반영하면 상태가 시간을 거슬러 오염되므로 **버리고 카운트**한다.
        조용히 처리하는 것이 진짜 위험이다 — 버린 건 QC 로 보고된다."""
        if self.last_ts is not None and ts < self.last_ts:
            self.n_late += 1
            return
        self.last_ts = ts
        # 녹화가 끊겼다 이어지면 이전 상태를 그대로 쓰면 안 된다.
        # Book 은 업비트가 스냅샷을 보내 자동 교체되고 Flow 는 TR_HIST_SEC 프루닝으로
        # 자가 치유되지만, 그건 **우연히 그렇게 되는 것**이지 코드가 보장한 게 아니다.
        # 공백을 만나면 명시적으로 끊는다 — 공백 전 체결이 공백 후 감소에 귀속되거나
        # 낡은 호가가 첫 프레임에 들어가는 일을 막는다.
        if self.last_ev is not None and (ts - self.last_ev) > GAP_RESET_SEC:
            self.bk = Book(); self.fl = Flow()
            self.next_emit = None
            self.n_reset += 1
        self.last_ev = ts
        if self.next_emit is None:
            self.next_emit = math.floor(ts / self.grid) * self.grid + self.grid
        else:
            while self.next_emit < ts:                    # `<` : event_ts == g 는 g에 포함
                stale = (self.bk.ts is not None
                         and (self.next_emit - self.bk.ts) > self.max_stale)
                row = None if stale else frame(self.code, self.next_emit, self.bk,
                                               self.fl, self.cnt, self.accb, self.acca)
                if stale:
                    self.n_stale += 1
                elif row:
                    self.rows.append(row)
                self.cnt = [0, 0]
                self.accb = DeplAcc(); self.acca = DeplAcc()
                self.next_emit += self.grid
        if m["type"] == "orderbook":
            d_b, d_a, top_b, top_a, t0, t1 = self.bk.apply(m, ts)
            # transition (t0, t1] 의 같은 가격 체결로만 상계한다.
            # 이 구간의 체결은 event-time 순서상 이미 Flow 에 들어와 있다.
            if d_b:
                self.accb.add(d_b, self.fl.exec_in(t0, t1, "bid"), top_b)
            if d_a:
                self.acca.add(d_a, self.fl.exec_in(t0, t1, "ask"), top_a)
            self.cnt[0] += 1
        else:
            self.fl.apply(m, ts); self.cnt[1] += 1


def replay(events, grid=1.0, lateness_ms=LATENESS_MS, max_stale=MAX_STALE_SEC):
    """event-time 재생. events = 원자료 dict 이터러블 (수신 순서, 순서 무관).

    워터마크: 마켓별 힙에 담아두고 (최신 event_ts − 힙 최소) > lateness 일 때만 꺼낸다.
    수신 지연 jitter 로 인한 국소 역순을 흡수한다.
    """
    heaps = defaultdict(list)
    hi = defaultdict(lambda: -1)
    st = {}
    push_n = 0          # 전역 push 순번 — (event_ts, _seq) 동률에도 전순서를 보장한다.
                        # 이게 없으면 힙이 dict 를 비교하려다 TypeError 로 죽는다
                        # (구 데이터처럼 _seq 가 전부 0인 경우 실제로 발생).
    for m in events:
        if "_meta" in m or m.get("type") not in ("orderbook", "trade"):
            continue
        code = m.get("code")
        ets = event_ts_ms(m)
        if code is None or ets is None:
            continue
        push_n += 1
        heapq.heappush(heaps[code], (ets, m.get("_seq", 0), push_n, m))
        if ets > hi[code]:
            hi[code] = ets
        h = heaps[code]
        while h and hi[code] - h[0][0] > lateness_ms:
            ets0, _, _, m0 = heapq.heappop(h)
            st.setdefault(code, MarketState(code, grid, max_stale)).step(m0, ets0 / 1000.0)
    for code, h in heaps.items():                          # 잔여 flush
        while h:
            ets0, _, _, m0 = heapq.heappop(h)
            st.setdefault(code, MarketState(code, grid, max_stale)).step(m0, ets0 / 1000.0)
    out = []; late = {}; stale = 0
    for code, s in st.items():
        out += s.rows
        stale += s.n_stale
        if s.n_late:
            late[code] = s.n_late
    out.sort(key=lambda r: (r["market"], r["ts"]))
    global LAST_QC
    LAST_QC = {"n_frames": len(out), "late_dropped": late, "lateness_ms": lateness_ms,
               "stale_skipped": stale, "max_stale_sec": max_stale,
               "note": "late_dropped>0 = 워터마크 초과 지각. --lateness-ms 늘려 재처리할 것."}
    return out


def add_labels(rows, horizons, max_slip=None):
    """mid 기준 forward bp.

    ⚠ 녹화 공백을 넘어 라벨링하지 않는다.
      `ts >= target` 인 첫 행을 그냥 집으면, 공백 뒤 몇 시간 후의 행을 집어놓고
      '30초 forward 수익률'이라고 붙이게 된다. 그 값은 지평이 다른 다른 관측이다.
      목표 시각을 max_slip 이상 넘어선 행은 라벨을 만들지 않고 None 으로 둔다.
      기본 max_slip = 지평의 50% 와 5초 중 큰 값 — 격자 간격보다는 넉넉하되
      공백은 확실히 걸러내는 폭이다.

    커버리지 부족은 0 으로 채우지 않고 None 으로 남긴다.
    """
    by = defaultdict(list)
    for r in rows:
        by[r["market"]].append(r)
    n_slip = 0
    for rs in by.values():
        rs.sort(key=lambda r: r["ts"])
        ts = [r["ts"] for r in rs]
        for i, r in enumerate(rs):
            for h in horizons:
                target = r["ts"] + h
                slip = max_slip if max_slip is not None else max(h * 0.5, 5.0)
                j = None
                for k in range(i + 1, len(rs)):
                    if ts[k] >= target:
                        j = k
                        break
                key = f"fwd_{int(h)}_bp"
                if j is None or r.get("mid") in (None, 0) or rs[j].get("mid") in (None, 0):
                    r[key] = None
                elif ts[j] - target > slip:          # 공백을 건너뛴 것 — 라벨 무효
                    r[key] = None
                    n_slip += 1
                else:
                    r[key] = (rs[j]["mid"] / r["mid"] - 1.0) * 1e4
    if n_slip:
        print(f"  라벨 무효(공백 초과) {n_slip:,}건 — 0 으로 채우지 않고 None 으로 둔다",
              flush=True)
    return rows


def stream(paths, market=None):
    files = []
    for p in paths:
        files += sorted(glob.glob(p, recursive=True))
    if not files:
        sys.exit("입력 파일 없음")
    for f in files:
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if market and m.get("code") != market:
                    continue
                yield m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--grid", type=float, default=1.0)
    ap.add_argument("--market", default="")
    ap.add_argument("--labels", default="")
    ap.add_argument("--max-label-slip-sec", type=float, default=None,
                    help="목표 시각을 이만큼 넘어선 행은 라벨을 만들지 않는다 "
                         "(기본: 지평의 50%% 와 5초 중 큰 값)")
    ap.add_argument("--lateness-ms", type=int, default=LATENESS_MS)
    ap.add_argument("--max-stale-sec", type=float, default=MAX_STALE_SEC,
                    help="호가가 이보다 낡으면 프레임을 만들지 않는다 (재접속·공백 구간 방어)")
    ap.add_argument("--out", default="features.jsonl.gz")
    a = ap.parse_args()

    rows = replay(stream(a.paths, a.market or None), a.grid, a.lateness_ms, a.max_stale_sec)
    if a.labels:
        rows = add_labels(rows, [float(x) for x in a.labels.split(",") if x.strip()],
                          a.max_label_slip_sec)
    print(f"프레임 {len(rows):,} (격자 {a.grid}s · 워터마크 {a.lateness_ms}ms)")
    if LAST_QC.get("late_dropped"):
        print(f"  ⚠ 지각 폐기: {LAST_QC['late_dropped']} — --lateness-ms 를 늘려 재처리할 것")
    else:
        print("  지각 폐기 0건 (워터마크 충분)")
    print(f"  낡은 호가로 생략한 프레임: {LAST_QC.get('stale_skipped',0):,} "
          f"(book_age > {LAST_QC.get('max_stale_sec')}s)")
    if rows:
        mks = sorted({r["market"] for r in rows})
        span = max(r["ts"] for r in rows) - min(r["ts"] for r in rows)
        stale = [r["book_age_ms"] for r in rows if r.get("book_age_ms") is not None]
        stale.sort()
        print(f"마켓 {len(mks)} · 구간 {span/60:.1f}분")
        print(f"호가 신선도 book_age_ms: p50 {stale[len(stale)//2]:.0f} / "
              f"p90 {stale[int(.9*len(stale))]:.0f} / max {stale[-1]:.0f}")
        for key, label in (("unexplained_depl_ratio_bid", "전 호가단"),
                           ("unexplained_depl_ratio_bid_top", "최우선호가만")):
            ur = sorted(r[key] for r in rows if r.get(key) is not None)
            if ur:
                print(f"unexplained_depl_ratio_bid [{label}]: p25 {ur[len(ur)//4]:.3f} / "
                      f"p50 {ur[len(ur)//2]:.3f} / p75 {ur[3*len(ur)//4]:.3f} (n={len(ur):,})")
        print("  ⚠ 취소율이 아니라 '설명되지 않은 감소' 비율이다. 검증 전 인과 해석 금지.")
        print("  ⚠ 전 호가단 값은 깊은 단의 일상적 갱신 때문에 구조적으로 1 에 붙는다.")
        print("     벽의 진위는 **최우선호가만** 보는 쪽으로 판단할 것.")
    with gzip.open(a.out, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"→ {a.out}")


if __name__ == "__main__":
    main()
