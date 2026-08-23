# -*- coding: utf-8 -*-
"""ob_recorder.py — 오더북/체결 REST 폴링 레코더.

⚠⚠ **장기 수집용으로는 폐기. `ws_recorder.py`를 쓸 것.**
   REST는 사이클마다 오더북 1콜 + 종목별 체결 N콜을 돌아서, 실측 사이클 주기가 6.4초였다.
   그러면 OBI와 체결흐름이 같은 시점의 시장 상태가 아니게 된다 — 스켈핑에서 2~6초
   어긋남은 측정하려는 현상보다 오차가 크다. WebSocket은 두 스트림이 각자의 발생 시각을
   달고 같은 연결로 들어오므로 event-time 정렬이 가능하다.
   이 파일은 단발 점검·WS 불가 환경 fallback 용도로만 남긴다.

왜 필요한가 (2026-08-23 API 실측)
  Upbit 오더북은 **소급 조회가 불가능**하다 (현재 스냅샷 전용, to= 파라미터 무시).
  체결틱도 daysAgo≤7 까지만.
  ⇒ imbalance / spread / 호가소진 / 체결강도 임계치는 **과거 데이터로 탐색 자체가 불가**.
    지금부터 쌓아야만 나온다. 시작이 늦어진 만큼은 영구 손실.

bot.py 와 무엇이 다른가
  bot.py 도 imbalance·체결강도를 기록하지만 **진입 시점에만** 남긴다 (신호 조건부 표본).
  임계치를 찾으려면 "신호가 안 난 평범한 분"이라는 대조군이 필요하다.
  이 레코더는 **조건 없이 전 구간을 균일 샘플링**한다. 그게 유일한 차이이자 핵심.

안전
  · 읽기 전용 공개 엔드포인트만 호출. 주문·인증 코드 없음. API 키 사용 안 함.
  · bot.py / 기존 systemd 유닛과 무관한 별도 프로세스.

출력: data/ob/{YYYY-MM-DD}.jsonl.gz  (1행 = 1마켓 1샘플)
  ts, market, mid, spread_bps,
  imb1/imb3/imb5      — 상위 1/3/5호가 (bid−ask)/(bid+ask) 잔량 가중
  bid_krw5, ask_krw5  — 상위 5호가 누적 잔량(원)
  tick_n, tick_buy_n, tick_buy_krw, tick_sell_krw, tick_krw  — 직전 interval 체결 집계
  buy_ratio           — 체결금액 기준 매수비 (= 체결강도)

사용 (서버에서):
  nohup python3 ob_recorder.py --top 20 --interval 10 > ob_recorder.log 2>&1 &
"""
import os, sys, json, gzip, time, argparse, datetime, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ob")


def imbalance(units, k):
    """상위 k호가 잔량 기준 (bid−ask)/(bid+ask). bot.py:8005 정의와 동일 계열."""
    u = units[:k]
    b = sum(x["bid_size"] * x["bid_price"] for x in u)
    a = sum(x["ask_size"] * x["ask_price"] for x in u)
    t = b + a
    return (b - a) / t if t > 0 else 0.0


def sample(markets, since_ms):
    """1회 샘플: 오더북 스냅샷 + 직전 구간 체결틱 집계."""
    rows = []
    obs = {}
    for i in range(0, len(markets), 10):                    # 오더북은 다중 마켓 지원
        for o in collect.get("/orderbook", {"markets": ",".join(markets[i:i + 10])}):
            obs[o["market"]] = o
    now = int(time.time() * 1000)
    for m in markets:
        o = obs.get(m)
        if not o: continue
        u = o["orderbook_units"]
        if not u: continue
        bid1, ask1 = u[0]["bid_price"], u[0]["ask_price"]
        mid = (bid1 + ask1) / 2.0
        try:
            ticks = collect.get("/trades/ticks", {"market": m, "count": 200})
        except Exception:
            ticks = []
        sel = [t for t in ticks if t["timestamp"] > since_ms]
        buy_krw = sum(t["trade_price"] * t["trade_volume"] for t in sel if t["ask_bid"] == "BID")
        sell_krw = sum(t["trade_price"] * t["trade_volume"] for t in sel if t["ask_bid"] == "ASK")
        tot = buy_krw + sell_krw
        rows.append({
            "ts": now, "market": m, "mid": mid,
            "spread_bps": (ask1 - bid1) / mid * 10000.0 if mid > 0 else 0.0,
            "imb1": imbalance(u, 1), "imb3": imbalance(u, 3), "imb5": imbalance(u, 5),
            "bid_krw5": sum(x["bid_size"] * x["bid_price"] for x in u[:5]),
            "ask_krw5": sum(x["ask_size"] * x["ask_price"] for x in u[:5]),
            "tick_n": len(sel), "tick_buy_n": sum(1 for t in sel if t["ask_bid"] == "BID"),
            "tick_buy_krw": buy_krw, "tick_sell_krw": sell_krw, "tick_krw": tot,
            "buy_ratio": (buy_krw / tot) if tot > 0 else None,
            "truncated": len(sel) == len(ticks) and len(ticks) >= 200,   # 200개 상한에 걸림 = 과소집계
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--interval", type=int, default=10, help="샘플 간격(초)")
    ap.add_argument("--markets", default="")
    ap.add_argument("--hours", type=float, default=0, help="0=무한")
    a = ap.parse_args()
    os.makedirs(DIR, exist_ok=True)
    if a.markets:
        mks = a.markets.split(",")
    else:
        mks = [m for m, _ in collect.krw_markets_by_value()[:a.top]]
    print(f"[ob] {len(mks)} markets · {a.interval}초 간격 · 예상 요청 "
          f"{(len(mks)+ (len(mks)+9)//10)/a.interval:.1f} req/s", flush=True)
    print(f"[ob] markets: {','.join(mks)}", flush=True)
    end = time.time() + a.hours * 3600 if a.hours else None
    since = int(time.time() * 1000) - a.interval * 1000
    n = 0
    while end is None or time.time() < end:
        t0 = time.time()
        try:
            rows = sample(mks, since)
            since = int(t0 * 1000)
            day = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            with gzip.open(os.path.join(DIR, f"{day}.jsonl.gz"), "at", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
            n += len(rows)
            if n % (len(mks) * 30) < len(mks):
                print(f"[ob] {datetime.datetime.utcnow().strftime('%H:%M:%S')} 누적 {n:,}행", flush=True)
        except Exception as e:
            print(f"[ob] ERR {e}", flush=True); time.sleep(2)
        d = a.interval - (time.time() - t0)
        if d > 0: time.sleep(d)


if __name__ == "__main__":
    main()
