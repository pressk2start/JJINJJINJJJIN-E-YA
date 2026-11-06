#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""book_winner_study — H23 사전 등록: 진입 직전 호가 구조만으로 수익 거래와 손실 거래를 구분할 수 있는가, 그 차이가 비용 후에도 남는가.
(읽기 전용, 표준 라이브러리, Python 3.7+. 고문 조건부 승인 2026-11-05: 호가 피처만, 비용 후 순손익 라벨, TRAIN/HOLDOUT 고정,
 미래정보 차단, 통과 기준 사전 고정, 10/29·30 은 날짜 통째 제외하지 않음(민감도만), H19·H20 에 쓴 데이터는 HOLDOUT 으로 재사용 금지.)

질문: 5종목(BTC·ETH·XRP·SOL·NEAR)에서 5초마다 "지금 시장가로 사서 h초 뒤 시장가로 판다"를 가정했을 때, 그 순간의 호가 구조(불균형·
스프레드·마이크로프라이스·직전 수익률·깊이 변화·갱신 빈도)로 비용 후 승자를 골라낼 수 있는가. 이것이 안 되면 승자/패자 학습은 종료한다.

사전 등록 (이 커밋이 결과보다 먼저다. 아래 숫자는 결과를 보고 바꾸지 않는다):
  데이터     ws_tight.  TRAIN 10/29~11/04 (7일).  HOLDOUT 11/05~11/11 (11/12 2차 복사 후에만 존재 — H19·H20 이 보지 않은 새 데이터).
             10/28 은 종목 교체일로 제외. 10/29·30 은 포함; 민감도로 '10/29·30 제외 TRAIN' 의 상위 규칙과 겹침 수만 보고한다.
  결정 격자  5초(epoch 정렬). 결정 시점 g 에 쓰는 호가 = recv≤g 인 가장 최근 호가 중 지연(recv−timestamp) 0~500ms 이고 g−recv≤1000ms 인 것.
             없으면 결정 없음(no_book). 체결 데이터는 피처에 쓰지 않는다(체결 피드 지연 구조가 확인됐으므로 — ws_valid_windows 참조).
  진입       g+300ms 이후 첫 유효 호가의 매도호가를 100,000 KRW 명목으로 걷어 올린 평균가(잔량 부족이면 thin → 제외). 2초 안에 유효 호가가
             없으면 제외(no_entry).  보유시간 h ∈ {30, 120, 300}s 각각 독립 라벨.
  청산       진입 시각+h 이후 첫 유효 호가의 매수호가를 보유 수량만큼 걷어 내린 평균가. 5초 안에 없으면 제외(no_exit).
  비용       0.05%×2.  net% = 100·( sell·(1−f) / (buy·(1+f)) − 1 ).  승자 = net>0.
  공백       수신 공백 >5s: 이력 초기화, 대기 중 결정·미청산 포지션 제외(gap). 일자 경계는 연속 스트림으로 본다.
  피처(11)   결정 호가에서만, 미래 정보 없음:
             obi1=(bs1−as1)/(bs1+as1) · obi5 · obi10 (5·10단 누적 수량 불균형) · spread_ticks=(a1−b1)/틱 · micro_bp=1e4·(마이크로프라이스−중간가)/중간가
             ret5_bp·ret30_bp·ret120_bp = 1e4·(중간가/과거 중간가 −1), 과거 호가는 g−Δ 이전 가장 최근 유효 호가(허용 오차 2s, 없으면 결측)
             dask5·dbid5 = 5단 매도·매수 KRW 깊이의 5초 변화율 · upd5 = 직전 5초 수신 유효 호가 수.
  규칙 탐색  TRAIN 만. 피처별 TRAIN 분위 10·20·80·90% 를 문턱으로 한 단일 규칙(≤q10, ≤q20, ≥q80, ≥q90)과 서로 다른 피처 두 개의 AND.
             h 마다 44 단일 + 946 쌍. 전 종목 합산 n≥200 인 규칙 중 평균 net 상위 10개를 잠근다(JSON, 절대 문턱값). 기계적 선택.
  판정       HOLDOUT 에서 잠긴 10개만 평가. 규칙 PASS = n≥100 · 평균 net 의 부트스트랩 하한(단측 99.5% = 0.05/10 보정) >0 ·
             종목별 평균 net 이 5종목 중 3종목 이상 양수.  하나라도 PASS 면 H23 PASS, 전부 n<100 이면 INSUFFICIENT, 아니면 FAIL.
             PASS 여도 LIVE 변경 없음 → SHADOW 로만. FAIL 이면 승자/패자 학습 종료.
  범위       이 5종목·이 14일·시장가 체결 모형(300ms 지연, 호가 걷기)·이 피처군 안에서만. 내 주문이 호가를 바꾸지 않는다고 가정.
  기대       기저 평균 net 은 −(스프레드+0.1%) 근처, 승률 30~45%. 규칙이 TRAIN 에서 양수여도 HOLDOUT 에서 무너지는 것이 기본 가설이다.

결과 2026-11-06 (TRAIN, PC):
  기저   n≈50.8만, 건당 −0.17% (보유시간 무관) · 승률 0.8/4.4/10.3% (30/120/300s). BTC −0.13 · ETH −0.16 · XRP −0.17 · SOL −0.20 · NEAR −0.24.
         보유시간과 무관한 상수 = 수수료 0.10% + 평균 스프레드(BTC 약 0.03%). 5분 안에 그 이상 오르는 경우가 10% 뿐이다(사전 기대 30~45% 는 틀렸다).
  규칙   잠긴 10개의 TRAIN 평균이 전부 음수(−0.107~−0.119%). 단일 44 + 쌍 946, ×3 보유시간 중 표본 안 최적조차 비용을 못 넘는다.
         상위 규칙은 대부분 BTC·ETH 만 남기는 효과(BTC 기저 −0.129 대비 +0.02%p).
  해석   HOLDOUT PASS 는 표본 밖 성과가 표본 안 최적보다 0.1%p 이상 좋아야 가능 = 사실상 불가.
         답: 이 5종목·5분 이하에서 진입 직전 호가 구조는 기대수익을 많아야 0.02~0.07%p 움직이고, 왕복 비용 0.13~0.24% 를 넘기지 못한다.
  상태   2026-11-06: TRAIN 단계에서 비용 후 양수 후보 없음. 고문 합의로 사전 등록대로 11/12 형식 HOLDOUT 을 한 번 돌려 닫는다.
         HOLDOUT 을 돌리지 않고 닫는다면 기록은 TERMINATED_EARLY(TRAIN 양수 후보 없음)이지 HOLDOUT FAIL 이 아니다.
         HOLDOUT 이 우연히 PASS 여도 LIVE 투입 없음, SHADOW 로만.
  종료   2026-11-06: 서버 ws-recorder 를 정지·비활성화했다(녹화를 쓰는 연구가 모두 닫힘). HOLDOUT 11/05~11/11 은 11/05 와 11/06 일부만
         존재 → 거래일 3일 미만이라 개정 기준상 판정 불가. H23 = TERMINATED_EARLY (TRAIN 단계 비용 후 양수 후보 없음). HOLDOUT FAIL 아님.
  개정   2026-11-06, HOLDOUT 데이터를 열기 전 (기준을 더 엄격하게만 바꿈 → 사전 등록 위반 아님):
         5초 격자 진입은 같은 종목에서 보유 구간이 최대 60개 겹쳐 독립 표본이 아니므로 건 단위 부트스트랩 하한이 너무 높게 나온다.
         규칙 PASS 에 조건 하나 추가: 종목별로 보유 중 새 진입을 버린 '겹침 제거' 거래로 일별 평균을 내고(HOLDOUT 최대 7일),
         그 평균의 단측 99.5% t 하한(0.05/10 보정, 자유도 = 일수−1)이 0 초과. 거래가 있는 날이 3일 미만이면 PASS 아님.
         참고: 잠긴 #1·#3·#8 은 TRAIN 에서 BTC·ETH 두 종목에서만 나와 '5종목 중 3종목 양수' 를 구조적으로 못 넘는다(판정 대상은 사실상 7개).
  정정   SENSITIVITY 의 0/10 은 숫자 문턱까지 비교한 탓에 과장됐다. 10/29·30 을 빼면 분위 문턱이 다시 계산돼(같은 "obi10 ≥ q90" 도 숫자가 조금 달라짐)
         같은 규칙도 다른 규칙으로 셌다. 구조 키(h, 피처, 방향, 분위)로 비교하도록 고침. '잡음 적합의 강한 증거' 라는 앞선 해석은 철회.
         TRAIN 을 다시 돌리면 고친 값이 나온다(선택·rules.json 불변). 핵심 결론(표본 안 최적도 음수)은 이것과 무관.
  정정   10분위 진단표가 동률을 net 순으로 정렬해 이산 피처(upd5·spread_ticks)의 rho 를 부풀렸다(0.98 등). 피처값으로만 안정 정렬하도록
         고침. 진단 전용 표이며 규칙 탐색·판정 로직은 무변경, 이미 저장된 rules.json 은 그대로 유효.

실행 (PC):
  py -3 -X utf8 book_winner_study.py ws_tight             TRAIN: 기저율·피처 분위표·잠근 규칙 → ws_tight_h23_train.txt, ws_tight_h23_rules.json
  py -3 -X utf8 book_winner_study.py ws_tight --holdout   잠근 규칙을 HOLDOUT 에 적용 → ws_tight_h23_holdout.txt   (11/12 복사 후)
"""
import argparse
import gzip
import heapq
import json
import random
import re
import sys
import zlib
from bisect import bisect_left, bisect_right
from pathlib import Path

NOTIONAL, FEE = 100000.0, 0.0005
GRID_MS, MAX_AGE_MS, BOOK_STALE_MS = 5000, 500, 1000
ENTRY_DELAY_MS, ENTRY_WAIT_MS, EXIT_WAIT_MS, MAX_GAP_MS = 300, 2000, 5000, 5000
HORIZONS = (30, 120, 300)
MARKETS = ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-NEAR")
TRAIN_DAYS = ("2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04")
HOLDOUT_DAYS = ("2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08", "2026-11-09", "2026-11-10", "2026-11-11")
SENS_DROP = ("2026-10-29", "2026-10-30")
FEATURES = ("obi1", "obi5", "obi10", "spread_ticks", "micro_bp", "ret5_bp", "ret30_bp", "ret120_bp", "dask5", "dbid5", "upd5")
QUANTS = ((0.10, "<="), (0.20, "<="), (0.80, ">="), (0.90, ">="))
MIN_RULE_N, TOP_K, MIN_HOLD_N, ALPHA = 200, 10, 100, 0.05 / 10
_CODE = re.compile(rb'"code":\s*"(KRW-[A-Z0-9]+)"')
_RECV = re.compile(rb'"recv_ts":\s*(\d+)')
_OB = re.compile(rb'"type":\s*"orderbook"')


def _mean(v):
    return sum(v) / len(v) if v else None


def _q(sorted_vals, q):
    return sorted_vals[min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1)))]


def bootstrap_low(v, alpha, n=2000, seed=11):
    if len(v) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(_mean([rng.choice(v) for _ in v]) for _ in range(n))
    return means[int(alpha * (n - 1))]


def net_pct(buy, sell):
    return 100.0 * (sell * (1 - FEE) / (buy * (1 + FEE)) - 1)


# ----------------------------------------------------------------------------------------------------------------------
def iter_books(day_dir, markets):
    """yield ("book", recv, code, ts, asks[(p,s)...best first], bids[...]) | ("gap", recv). 체결 줄은 공백 판정에만 쓴다."""
    files = sorted(p for p in day_dir.iterdir() if p.name.endswith(".jsonl.gz"))
    want = set(m.encode() for m in markets)
    prev = None
    for p in files:
        try:
            with gzip.open(str(p), "rb") as fh:
                for raw in fh:
                    r = _RECV.search(raw)
                    if not r:
                        continue
                    recv = int(r.group(1))
                    if prev is not None and recv - prev > MAX_GAP_MS:
                        yield ("gap", recv)
                    prev = recv
                    c = _CODE.search(raw)
                    if not c or c.group(1) not in want or not _OB.search(raw):
                        continue
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    units = m.get("orderbook_units") or []
                    ts = m.get("timestamp")
                    if not units or ts is None:
                        continue
                    asks = sorted((float(u["ask_price"]), float(u["ask_size"])) for u in units)
                    bids = sorted(((float(u["bid_price"]), float(u["bid_size"])) for u in units), reverse=True)
                    yield ("book", recv, c.group(1).decode(), int(ts), asks, bids)
        except (EOFError, OSError, zlib.error):
            yield ("gap", prev or 0)


def walk_buy(asks, notional):
    """매도호가를 걷어 올려 notional KRW 매수 → (평균가, 수량) 또는 None(잔량 부족)."""
    left, cost, qty = notional, 0.0, 0.0
    for p, s in asks:
        take = min(s, left / p)
        cost += take * p
        qty += take
        left -= take * p
        if left <= 1e-9:
            return cost / qty, qty
    return None


def walk_sell(bids, qty):
    """매수호가를 걷어 내려 qty 매도 → 평균가 또는 None."""
    left, proceeds = qty, 0.0
    for p, s in bids:
        take = min(s, left)
        proceeds += take * p
        left -= take
        if left <= 1e-12:
            return proceeds / qty
    return None


def infer_tick(asks, cur):
    best = cur
    for (a, _), (b, _) in zip(asks, asks[1:]):
        d = round(b - a, 6)
        if d > 0 and (best is None or d < best):
            best = d
    return best


def features(book, ht, hv, g, tick):
    """book=(recv, asks, bids). ht=수신시각 리스트(정렬), hv=(mid, ask5_krw, bid5_krw) 리스트. 결정 시점 g. 미래 정보 없음."""
    _, asks, bids = book
    a1, as1 = asks[0]
    b1, bs1 = bids[0]
    mid = (a1 + b1) / 2.0
    f = {"obi1": (bs1 - as1) / (bs1 + as1) if bs1 + as1 > 0 else None}
    for n in (5, 10):
        sa = sum(s for _, s in asks[:n])
        sb = sum(s for _, s in bids[:n])
        f["obi%d" % n] = (sb - sa) / (sb + sa) if sb + sa > 0 else None
    f["spread_ticks"] = (a1 - b1) / tick if tick else None
    micro = (a1 * bs1 + b1 * as1) / (bs1 + as1) if bs1 + as1 > 0 else mid
    f["micro_bp"] = 1e4 * (micro - mid) / mid
    ask5 = sum(p * s for p, s in asks[:5])
    bid5 = sum(p * s for p, s in bids[:5])

    def past(delta_ms):
        lo, hi = g - delta_ms - 2000, g - delta_ms
        i = bisect_right(ht, hi) - 1
        return hv[i] if i >= 0 and ht[i] >= lo else None
    for name, d in (("ret5_bp", 5000), ("ret30_bp", 30000), ("ret120_bp", 120000)):
        p = past(d)
        f[name] = 1e4 * (mid / p[0] - 1) if p and p[0] > 0 else None
    p5 = past(5000)
    f["dask5"] = ask5 / p5[1] - 1 if p5 and p5[1] > 0 else None
    f["dbid5"] = bid5 / p5[2] - 1 if p5 and p5[2] > 0 else None
    f["upd5"] = len(ht) - bisect_right(ht, g - 5000)
    return f, mid, ask5, bid5


class _M(object):
    __slots__ = ("ht", "hv", "last", "tick", "nbook", "next_grid", "pending", "open")

    def __init__(self):
        self.ht, self.hv = [], []   # 유효 호가 이력: 수신시각 / (mid, ask5_krw, bid5_krw), 125초 보관
        self.last = None            # (recv, asks, bids) 마지막 유효 호가
        self.tick = None
        self.nbook = 0
        self.next_grid = None
        self.pending = []           # 진입 대기 결정
        self.open = []              # 청산 대기 heap: (due_ms, seq, h, rec)


def run_days(root, days, markets=MARKETS, log=None):
    """결정 레코드 목록. 각 레코드: day, code, g, f{피처}, buy, qty, net{h: net% 또는 None}, drop(사유) ."""
    st = {m: _M() for m in markets}
    records, counters = [], {"decisions": 0, "no_book": 0, "no_entry": 0, "thin": 0, "no_exit": 0, "gap": 0}

    seq = [0]

    def on_gap():
        for s in st.values():
            del s.ht[:], s.hv[:]
            s.last = None
            s.next_grid = None
            counters["gap"] += len(s.pending) + len(s.open)
            for _, _, h, rec in s.open:
                rec["net"][h] = None
            s.pending, s.open = [], []

    for day in days:
        d = Path(root, day)
        if not d.is_dir():
            if log:
                log("missing day folder: %s" % d)
            continue
        for ev in iter_books(d, markets):
            if ev[0] == "gap":
                on_gap()
                continue
            _, recv, code, ts, asks, bids = ev
            s = st[code]
            if s.nbook < 200:
                s.tick = infer_tick(asks, s.tick)
            s.nbook += 1
            fresh = 0 <= recv - ts <= MAX_AGE_MS
            # 1) 이 호가 이전의 격자 시점들을 결정 (recv ≤ g 인 마지막 유효 호가 = s.last)
            if s.next_grid is None:
                s.next_grid = (recv // GRID_MS + 1) * GRID_MS
            while s.next_grid <= recv:
                g = s.next_grid
                s.next_grid += GRID_MS
                if s.last is None or g - s.last[0] > BOOK_STALE_MS or s.tick is None:
                    counters["no_book"] += 1
                    continue
                f, _, _, _ = features(s.last, s.ht, s.hv, g, s.tick)
                rec = {"day": day, "code": code, "g": g, "f": f, "buy": None, "qty": None, "net": {}}
                counters["decisions"] += 1
                s.pending.append(rec)
            # 2) 진입 대기 처리
            if s.pending:
                keep = []
                for rec in s.pending:
                    if recv >= rec["g"] + ENTRY_DELAY_MS + ENTRY_WAIT_MS and not fresh:
                        counters["no_entry"] += 1
                        continue
                    if fresh and recv >= rec["g"] + ENTRY_DELAY_MS:
                        if recv > rec["g"] + ENTRY_DELAY_MS + ENTRY_WAIT_MS:
                            counters["no_entry"] += 1
                            continue
                        w = walk_buy(asks, NOTIONAL)
                        if w is None:
                            counters["thin"] += 1
                            continue
                        rec["buy"], rec["qty"], rec["t_in"] = w[0], w[1], recv
                        records.append(rec)
                        for h in HORIZONS:
                            seq[0] += 1
                            heapq.heappush(s.open, (recv + h * 1000, seq[0], h, rec))
                    else:
                        keep.append(rec)
                s.pending = keep
            # 3) 청산 대기 처리: 만기 지난 것부터. 유효 호가만 체결하고, 대기 한도를 넘긴 것은 결측
            while s.open and s.open[0][0] <= recv:
                due, _, h, rec = s.open[0]
                if recv > due + EXIT_WAIT_MS:
                    heapq.heappop(s.open)
                    rec["net"][h] = None
                    counters["no_exit"] += 1
                    continue
                if not fresh:
                    break
                heapq.heappop(s.open)
                sell = walk_sell(bids, rec["qty"])
                rec["net"][h] = net_pct(rec["buy"], sell) if sell is not None else None
                if sell is None:
                    counters["thin"] += 1
            # 4) 상태 갱신 (유효 호가만 결정·이력에 쓴다)
            if fresh:
                s.last = (recv, asks, bids)
                a5 = sum(p * q for p, q in asks[:5])
                b5 = sum(p * q for p, q in bids[:5])
                s.ht.append(recv)
                s.hv.append(((asks[0][0] + bids[0][0]) / 2.0, a5, b5))
                if s.ht[0] < recv - 185000:                   # 125초만 남기고 앞부분을 묶어서 버림
                    k = bisect_left(s.ht, recv - 125000)
                    del s.ht[:k], s.hv[:k]
        if log:
            log("  scanned %s (records so far %d)" % (day, len(records)))
    for s in st.values():                                  # 끝까지 청산 못한 것은 결측
        counters["gap"] += len(s.pending)
    for rec in records:
        for h in HORIZONS:
            rec["net"].setdefault(h, None)
    return records, counters


# ----------------------------------------------------------------------------------------------------------------------
def base_rates(records):
    out = {}
    for h in HORIZONS:
        v = [r["net"][h] for r in records if r["net"][h] is not None]
        out[h] = {"n": len(v), "mean": _mean(v), "wr": 100.0 * sum(1 for x in v if x > 0) / len(v) if v else None,
                  "by_market": {m: _mean([r["net"][h] for r in records if r["code"] == m and r["net"][h] is not None])
                                for m in sorted(set(r["code"] for r in records))}}
    return out


def thresholds(records):
    """피처별 TRAIN 분위 문턱 (None 제외)."""
    th = {}
    for f in FEATURES:
        vals = sorted(r["f"][f] for r in records if r["f"][f] is not None)
        th[f] = {q: _q(vals, q) for q, _ in QUANTS} if vals else None
    return th


def match(rec, terms):
    for f, op, thr in terms:
        v = rec["f"].get(f)
        if v is None or (op == "<=" and v > thr) or (op == ">=" and v < thr):
            return False
    return True


def search(records, th):
    """TRAIN 규칙 탐색 → 전 h 합산 후보 리스트 (n≥MIN_RULE_N), 평균 net 내림차순."""
    rules = []
    for h in HORIZONS:
        idx = [i for i, r in enumerate(records) if r["net"][h] is not None]
        nets = {i: records[i]["net"][h] for i in idx}
        singles, seen = [], set()
        for f in FEATURES:
            if th[f] is None:
                continue
            for q, op in QUANTS:
                thr = th[f][q]
                if (f, op, thr) in seen:                      # q10==q20 같은 중복 문턱
                    continue
                seen.add((f, op, thr))
                s = set(i for i in idx if match(records[i], [(f, op, thr)]))
                if len(s) < MIN_RULE_N or len(s) == len(idx):  # 너무 작거나 전부 맞는(무의미한) 조건은 제외
                    continue
                singles.append(((f, op, thr), s))

        def stat(terms, s):
            n = len(s)
            if n < MIN_RULE_N:
                return None
            vals = [nets[i] for i in s]
            per = {}
            for i in s:
                per.setdefault(records[i]["code"], []).append(nets[i])
            return {"h": h, "terms": [list(t) for t in terms], "n": n, "mean": _mean(vals),
                    "wr": 100.0 * sum(1 for x in vals if x > 0) / n, "per_market": {m: _mean(v) for m, v in per.items()}}
        for t, s in singles:
            r = stat([t], s)
            if r:
                rules.append(r)
        for i in range(len(singles)):
            for j in range(i + 1, len(singles)):
                if singles[i][0][0] == singles[j][0][0]:
                    continue
                r = stat([singles[i][0], singles[j][0]], singles[i][1] & singles[j][1])
                if r:
                    rules.append(r)
    rules.sort(key=lambda r: (-r["mean"], len(r["terms"])))
    out, sig = [], set()
    for r in rules:                                          # 같은 표본(n·평균 동일)을 고르는 규칙은 가장 단순한 것 하나만
        k = (r["h"], r["n"], round(r["mean"], 9))
        if k in sig:
            continue
        sig.add(k)
        out.append(r)
    return out


def evaluate(records, rule, alpha=ALPHA):
    h = rule["h"]
    vals, per = [], {}
    for r in records:
        if r["net"][h] is not None and match(r, rule["terms"]):
            vals.append(r["net"][h])
            per.setdefault(r["code"], []).append(r["net"][h])
    n = len(vals)
    pm = {m: _mean(v) for m, v in per.items()}
    pos = sum(1 for v in pm.values() if v is not None and v > 0)
    low = bootstrap_low(vals, alpha)
    nov = non_overlap(records, rule)
    days = {}
    for r in nov:
        days.setdefault(r["day"], []).append(r["net"][h])
    daily = [_mean(v) for _, v in sorted(days.items())]
    tlow = t_low(daily)
    ok = (n >= MIN_HOLD_N and low is not None and low > 0 and pos >= 3
          and tlow is not None and tlow > 0)                        # 2026-11-06 개정: 겹침 제거 일별 평균 t 하한
    return {"n": n, "mean": _mean(vals), "wr": 100.0 * sum(1 for x in vals if x > 0) / n if n else None,
            "low": low, "per_market": pm, "markets_pos": pos, "n_nov": len(nov), "days": len(daily),
            "daily_mean": _mean(daily), "tlow": tlow, "pass": ok}


def non_overlap(records, rule):
    """규칙이 고른 거래를 종목별 시간순으로 훑으며, 직전 채택 거래의 만기(진입+h) 이전 진입은 버린다 = 종목당 동시 1포지션."""
    h = rule["h"]
    out, busy = [], {}
    for r in sorted((r for r in records if r["net"][h] is not None and match(r, rule["terms"])),
                    key=lambda r: (r["code"], r["t_in"])):
        if r["t_in"] < busy.get(r["code"], -1):
            continue
        busy[r["code"]] = r["t_in"] + h * 1000
        out.append(r)
    return out


# 단측 99.5% (= 0.05/10 보정) t 임계값, 자유도 2..6 (HOLDOUT 7일 → 최대 df 6). 표준 라이브러리에 t 분포가 없어 표로 고정.
T_CRIT = {2: 9.925, 3: 5.841, 4: 4.604, 5: 4.032, 6: 3.707}


def t_low(daily):
    """일별 평균들의 평균 − t·표준오차. 거래가 있는 날이 3일 미만이면 None(판정 불가 = PASS 아님)."""
    k = len(daily)
    if k < 3:
        return None
    m = _mean(daily)
    sd = (sum((x - m) ** 2 for x in daily) / (k - 1)) ** 0.5
    return m - T_CRIT[min(k - 1, 6)] * sd / k ** 0.5


def rule_key(rule, th):
    """규칙의 구조 키 = (h, {(피처, 방향, 분위)}). 숫자 문턱은 표본마다 다시 계산되므로 비교에 쓰지 않는다."""
    terms = []
    for f, op, thr in rule["terms"]:
        q = next((q for q, o in QUANTS if o == op and th.get(f) and th[f][q] == thr), None)
        terms.append((f, op, q))
    return rule["h"], tuple(sorted(terms))


def rule_str(rule):
    return "h=%ds %s" % (rule["h"], " AND ".join("%s %s %.4g" % (f, op, thr) for f, op, thr in rule["terms"]))


def _f(v, spec="{:.3f}"):
    return "n/a" if v is None else spec.format(v)


def report_train(records, counters, th, rules, sens_rules, log, sens_th=None):
    log("decisions=%d entered=%d | skipped: no_book=%d no_entry=%d thin=%d no_exit=%d gap=%d" % (
        counters["decisions"], len(records), counters["no_book"], counters["no_entry"], counters["thin"],
        counters["no_exit"], counters["gap"]))
    br = base_rates(records)
    for h in HORIZONS:
        b = br[h]
        log("BASE h=%3ds n=%d mean=%s%% wr=%s%% | %s" % (h, b["n"], _f(b["mean"]), _f(b["wr"], "{:.1f}"),
            " ".join("%s=%s" % (m[4:], _f(v)) for m, v in b["by_market"].items())))
    log("FEATURE DECILES (h=120s): bottom10% mean / top10% mean / spearman(decile, mean)  — 단조성 진단, 선택엔 안 씀")
    h = 120
    for f in FEATURES:
        pairs = sorted(((r["f"][f], r["net"][h]) for r in records if r["f"][f] is not None and r["net"][h] is not None),
                       key=lambda x: x[0])                    # 동률은 시간순 유지 (net 으로 정렬하면 이산 피처 rho 가 부풀려짐)
        if len(pairs) < 100:
            log("  %-12s n/a" % f)
            continue
        k = len(pairs) // 10
        dec = [_mean([x[1] for x in pairs[i * k:(i + 1) * k]]) for i in range(10)]
        rho = _spearman(list(range(10)), dec)
        log("  %-12s bottom=%s top=%s rho=%s n=%d" % (f, _f(dec[0]), _f(dec[-1]), _f(rho, "{:.2f}"), len(pairs)))
    log("LOCKED RULES (TRAIN top %d by mean net, n>=%d; thresholds absolute):" % (TOP_K, MIN_RULE_N))
    for i, r in enumerate(rules[:TOP_K]):
        log("  #%-2d n=%-6d mean=%s%% wr=%s%% | %s | %s" % (i + 1, r["n"], _f(r["mean"]), _f(r["wr"], "{:.1f}"), rule_str(r),
            " ".join("%s=%s" % (m[4:], _f(v)) for m, v in sorted(r["per_market"].items()))))
    if not rules:
        log("  none reached n>=%d" % MIN_RULE_N)
    if sens_rules is not None:
        a = set(rule_key(r, th) for r in rules[:TOP_K])
        b = set(rule_key(r, sens_th or th) for r in sens_rules[:TOP_K])
        log("SENSITIVITY (TRAIN without %s): top-%d overlap with locked = %d/%d by structure (h, feature, side, quantile) "
            "— 정보용, 선택 불변" % ("/".join(d[5:] for d in SENS_DROP), TOP_K, len(a & b), min(TOP_K, len(a))))


def _spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        rk = [0.0] * len(v)
        for pos, i in enumerate(order):
            rk[i] = float(pos)
        return rk
    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--holdout", action="store_true", help="잠긴 규칙(JSON)을 HOLDOUT 에 적용해 판정")
    a = ap.parse_args(argv)
    root = Path(a.root)
    base = str(root).rstrip("\\/")
    out = []

    def log(s):
        print(s, flush=True)
        out.append(s)

    if not a.holdout:
        log("H23 BOOK WINNER STUDY | TRAIN %s..%s | markets=%s | grid=%ds | notional=%d | fee=%.2f%%x2 | h=%s"
            % (TRAIN_DAYS[0], TRAIN_DAYS[-1], ",".join(MARKETS), GRID_MS // 1000, NOTIONAL, FEE * 100, HORIZONS))
        records, counters = run_days(root, TRAIN_DAYS, log=log)
        if not records:
            log("no TRAIN records")
            Path(base + "_h23_train.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
            return 1
        th = thresholds(records)
        rules = search(records, th)
        sens = [r for r in records if r["day"] not in SENS_DROP]
        sens_th = thresholds(sens) if sens else None
        sens_rules = search(sens, sens_th) if sens else None
        report_train(records, counters, th, rules, sens_rules, log, sens_th)
        locked = {"rules": rules[:TOP_K], "thresholds": th, "train_days": TRAIN_DAYS, "holdout_days": HOLDOUT_DAYS,
                  "criteria": {"min_hold_n": MIN_HOLD_N, "alpha_one_sided": ALPHA, "markets_pos_min": 3},
                  "base": {str(h): base_rates(records)[h] for h in HORIZONS}}
        with open(base + "_h23_rules.json", "w", encoding="utf-8") as fh:
            json.dump(locked, fh, ensure_ascii=False, indent=1)
        Path(base + "_h23_train.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
        print("saved:", base + "_h23_train.txt", base + "_h23_rules.json")
        return 0
    with open(base + "_h23_rules.json", encoding="utf-8") as fh:
        locked = json.load(fh)
    rules = locked["rules"]
    log("H23 HOLDOUT %s..%s | %d locked rules | rule PASS = n>=%d & bootstrap low(%.3f one-sided)>0 & >=3/5 markets positive"
        " & non-overlapping daily-mean t low(%.3f one-sided)>0 with >=3 days" % (
            HOLDOUT_DAYS[0], HOLDOUT_DAYS[-1], len(rules), MIN_HOLD_N, ALPHA, ALPHA))
    records, counters = run_days(root, HOLDOUT_DAYS, log=log)
    log("decisions=%d entered=%d | skipped: no_book=%d no_entry=%d thin=%d no_exit=%d gap=%d" % (
        counters["decisions"], len(records), counters["no_book"], counters["no_entry"], counters["thin"],
        counters["no_exit"], counters["gap"]))
    br = base_rates(records)
    for h in HORIZONS:
        log("BASE h=%3ds n=%d mean=%s%% wr=%s%%" % (h, br[h]["n"], _f(br[h]["mean"]), _f(br[h]["wr"], "{:.1f}")))
    any_pass, any_enough = False, False
    for i, r in enumerate(rules):
        e = evaluate(records, r)
        any_pass |= e["pass"]
        any_enough |= e["n"] >= MIN_HOLD_N
        log("  #%-2d TRAIN mean=%s%% -> HOLDOUT n=%d mean=%s%% low=%s%% wr=%s%% markets+=%d/5 | nov n=%d days=%d "
            "daily=%s%% tlow=%s%% %s | %s" % (
                i + 1, _f(r["mean"]), e["n"], _f(e["mean"]), _f(e["low"]), _f(e["wr"], "{:.1f}"), e["markets_pos"],
                e["n_nov"], e["days"], _f(e["daily_mean"]), _f(e["tlow"]), "PASS" if e["pass"] else "fail", rule_str(r)))
    v = "PASS" if any_pass else ("FAIL" if any_enough else "INSUFFICIENT")
    log("H23 VERDICT: %s | scope: 5 markets, %s..%s, taker fill model (300ms, book walk), 11 book features. "
        "PASS -> SHADOW only; FAIL -> winner/loser learning closed." % (v, TRAIN_DAYS[0], HOLDOUT_DAYS[-1]))
    Path(base + "_h23_holdout.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("saved:", base + "_h23_holdout.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
