# -*- coding: utf-8 -*-
"""ticks_collect.py — 체결 틱 7일 소급 수집 (order-flow 축의 유일한 과거 데이터).

무엇을 할 수 있고 무엇을 할 수 없는가
------------------------------------
`/v1/trades/ticks` 는 `daysAgo` 1~7 로 **최근 7일**만 소급된다 (8일부터 HTTP 400, 실측).
이 데이터로 계산 **가능**한 것과 **불가능**한 것을 먼저 구분해야 한다:

  가능 (체결만으로 계산됨)
    · trade imbalance 1s/3s/5s/10s   (매수체결금액−매도체결금액)/합
    · signed notional                부호 있는 체결대금
    · trade arrival rate · 가속       체결 도착률과 그 변화
    · large-trade fraction           대형체결 비중
    · aggressive buy ratio           공격적 매수 비율

  불가능 (호가가 있어야 함 → ws_recorder 가 쌓여야 나옴)
    · OBI level · ΔOBI
    · best-bid / best-ask depletion · cancel ratio
    · microprice − mid (연속) · spread 시계열

즉 이 수집기는 **order-flow 축의 절반**만 채운다. 나머지 절반은 전방 수집뿐이다.
두 축이 겹치지 않으므로 병행해도 중복이 아니다.

⚠ REST `/trades/ticks` 응답에는 `best_bid_price/size` 가 **없다**
   (WebSocket trade 이벤트에는 있다 — 실측 확인).
   따라서 "체결 시점의 스프레드"는 7일 틱으로 복원할 수 없다. WS 수집 이후에만 가능하다.

요청량 (실측)
  500건/요청 상한. 종목별 하루 요청 수 ≈ 일일체결수/500.
    KRW-XRP 약 346k건/일 → 691회/일 → 7일 4,837회 ≈ 10분 (8 req/s)
    KRW-BTC 약 158k건/일 → 315회/일
    KRW-SOL 약  43k건/일 →  85회/일
  대상을 적격 유니버스(tick_size.py 결과)로 좁히면 1시간 이내에 끝난다.

페이지네이션 (실측 확인)
  `daysAgo=N` + `to=HH:MM:SS` 로 시작점을 잡고, 응답 마지막 항목의 `sequential_id` 를
  `cursor` 로 넘기면 그보다 과거로 이어진다. count 는 500이 상한(1000 요청해도 500).

저장: data/ticks/{market}/{YYYY-MM-DD}.jsonl.gz — 응답 레코드 원본 그대로 (가공 없음)
  파생 지표는 후처리에서 계산한다. 지금 집계하면 정의를 바꿀 수 없다.

사용:
  python3 ticks_collect.py --markets KRW-XRP,KRW-ETH,KRW-SOL,KRW-TRUMP,KRW-BTC
  python3 ticks_collect.py --eligible          # tick_size.py 결과의 적격 종목만
  python3 ticks_collect.py --top 6 --days 7
"""
import os, sys, json, gzip, time, argparse, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ticks")
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def fetch_day(market, days_ago, max_req=4000):
    """daysAgo 기준 하루치 전량 (최신→과거). 반환: 레코드 리스트(시간 오름차순)."""
    rows = []
    seen = set()
    cursor = None
    params = {"market": market, "count": 500, "daysAgo": days_ago, "to": "23:59:59"}
    for i in range(max_req):
        p = dict(params)
        if cursor is not None:
            p["cursor"] = cursor
            p.pop("to", None)
        js = collect.get("/trades/ticks", p)
        if not js:
            break
        new = 0
        for r in js:
            sid = r.get("sequential_id")
            if sid in seen:
                continue
            seen.add(sid)
            rows.append(r)
            new += 1
        if new == 0:
            break
        cursor = min(r["sequential_id"] for r in js)
        if len(js) < 500:
            break
    rows.sort(key=lambda r: r["sequential_id"])
    return rows


def save(market, date_str, rows):
    d = os.path.join(DIR, market)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{date_str}.jsonl.gz")
    tmp = p + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n")
    os.replace(tmp, p)
    return p, os.path.getsize(p)


def eligible_markets():
    f = os.path.join(RESULTS, "tick_size.json")
    if not os.path.exists(f):
        sys.exit("results/tick_size.json 없음 — 먼저 python3 tick_size.py --save 실행")
    rows = json.load(open(f))["rows"]
    return [r["market"] for r in rows if r.get("eligible")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="")
    ap.add_argument("--eligible", action="store_true", help="tick_size.py 적격 종목만")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--days", type=int, default=7, help="1~7 (공개 API 상한)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.days > 7:
        sys.exit("daysAgo 상한은 7이다 (8부터 HTTP 400).")
    if a.markets:
        mks = [s.strip() for s in a.markets.split(",") if s.strip()]
    elif a.eligible:
        mks = eligible_markets()
    else:
        skip = {"KRW-USDT", "KRW-USDC", "KRW-DAI", "KRW-TUSD"}
        mks = [m for m, _ in collect.krw_markets_by_value() if m not in skip][:a.top]

    today = datetime.datetime.utcnow().date()
    print(f"[ticks] {len(mks)} markets × {a.days}일 · {DIR}", flush=True)
    print(f"[ticks] markets: {','.join(mks)}", flush=True)
    t0 = time.time()
    tot_rows = tot_bytes = 0
    for m in mks:
        for d in range(1, a.days + 1):
            date_str = (today - datetime.timedelta(days=d)).isoformat()
            out = os.path.join(DIR, m, f"{date_str}.jsonl.gz")
            if os.path.exists(out) and not a.force:
                print(f"  {m:12} {date_str} skip (존재)", flush=True)
                continue
            try:
                rows = fetch_day(m, d)
            except Exception as e:
                print(f"  {m:12} {date_str} FAIL {e}", flush=True)
                continue
            if not rows:
                print(f"  {m:12} {date_str} 0건", flush=True)
                continue
            _, sz = save(m, date_str, rows)
            tot_rows += len(rows); tot_bytes += sz
            el = time.time() - t0
            print(f"  {m:12} {date_str} {len(rows):>8,}건 {sz/1024/1024:>6.1f}MB "
                  f"({el/60:.1f}분 경과)", flush=True)
    print(f"[ticks] 완료 {tot_rows:,}건 · {tot_bytes/1024/1024:.0f}MB · "
          f"{(time.time()-t0)/60:.1f}분", flush=True)


if __name__ == "__main__":
    main()
