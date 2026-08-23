"""sec_collect.py — 초봉 90일 표본 수집기 (스켈핑 탐색용 · 성과 계산 없음).

왜 이 데이터인가:
  기존 research/는 초봉을 "이벤트 **직후** 240초 청산 시뮬"에만 썼다 (seconds_loader.py).
  "이벤트 **직전** 5~30초에 무엇이 있었나"는 이 저장소에서 아무도 본 적이 없다.
  Upbit 초봉은 ~90일 소급 가능 → 오더북(0일)·체결틱(7일)보다 훨씬 긴 탐색 창.

왜 표본 추출인가:
  90일 연속 초봉 = 마켓당 780만 초 → 200개/요청이면 39,000콜. 불가능.
  대신 [90일 전, 1일 전] 구간에서 anchor를 **균등 무작위** 추출하고 각 anchor에서
  연속 구간을 페이지백으로 긁는다. seed 고정(저장소 관행: seed 42) → 재현 가능.

  ⚠ 균등 무작위인 이유: 특정 국면(예: 최근 상승장)만 뽑으면 그 자체가 선택편향이다.
     기존 연구에서 "운 좋은 14일 창"이 부호를 뒤집은 전례가 있다 (ARCHIVE.md:54).

출력: data/sec_<stamp>.jsonl.gz  — {market, dt_utc, ts, o,h,l,c, value}
사용:
  python3 sec_collect.py --top 8 --anchors 40 --pages 4
"""
import os, sys, gzip, json, time, random, argparse, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__) or ".")
import scalp as SC

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
FMT = "%Y-%m-%dT%H:%M:%S"
SEED = 42


def top_markets(n):
    mk = SC.markets_krw()
    rows = []
    for i in range(0, len(mk), 100):
        rows += (SC._get("/ticker", {"markets": ",".join(mk[i:i + 100])}) or [])
        time.sleep(SC.REQ_DELAY)
    rows.sort(key=lambda t: float(t.get("acc_trade_price_24h", 0)), reverse=True)
    # 스테이블코인 제외 — 가격이 안 움직여 스켈핑 대상이 아니다 (collect_1m.py 관행과 동일)
    skip = {"KRW-USDT", "KRW-USDC", "KRW-DAI", "KRW-TUSD", "KRW-BUSD"}
    return [t["market"] for t in rows if t["market"] not in skip][:n]


def fetch_chunk(market, anchor_dt, pages):
    """anchor에서 과거로 pages장(장당 200개) 페이지백. 반환: 오름차순 캔들 리스트."""
    out = []
    to = anchor_dt.strftime(FMT) + "Z"
    for _ in range(pages):
        js = SC.seconds_candles(market, count=200, to=to)
        if not js:
            break
        out += js
        to = js[-1]["candle_date_time_utc"] + "Z"     # 가장 오래된 캔들 시각으로 이어붙이기
        time.sleep(SC.REQ_DELAY)
    rows = []
    for c in out:
        rows.append({
            "market": market,
            "dt": c["candle_date_time_utc"],
            "ts": float(c["timestamp"]) / 1000.0,
            "o": float(c["opening_price"]), "h": float(c["high_price"]),
            "l": float(c["low_price"]), "c": float(c["trade_price"]),
            "value": float(c.get("candle_acc_trade_price", 0.0)),
        })
    rows.sort(key=lambda r: r["ts"])
    # 중복 제거 (페이지 경계에서 같은 캔들이 두 번 올 수 있음)
    ded = []
    seen = set()
    for r in rows:
        if r["ts"] in seen:
            continue
        seen.add(r["ts"]); ded.append(r)
    return ded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--markets", default="")
    ap.add_argument("--anchors", type=int, default=40, help="마켓당 무작위 anchor 수")
    ap.add_argument("--pages", type=int, default=4, help="anchor당 페이지백 횟수 (장당 200캔들)")
    ap.add_argument("--days-back", type=int, default=90)
    ap.add_argument("--days-skip", type=int, default=1, help="최근 N일 제외 (holdout 확보)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    markets = [s.strip() for s in a.markets.split(",") if s.strip()] or top_markets(a.top)
    rnd = random.Random(SEED)
    now = datetime.datetime.utcnow()
    lo = now - datetime.timedelta(days=a.days_back)
    hi = now - datetime.timedelta(days=a.days_skip)
    span = (hi - lo).total_seconds()

    jobs = []
    for m in markets:
        for _ in range(a.anchors):
            jobs.append((m, lo + datetime.timedelta(seconds=rnd.uniform(0, span))))

    stamp = now.strftime("%Y%m%d_%H%M%S")
    out = a.out or os.path.join(DATA_DIR, f"sec_{stamp}.jsonl.gz")
    print(f"[sec] markets={markets}")
    print(f"[sec] anchors={a.anchors}/마켓 · pages={a.pages} · 창=[{lo:%Y-%m-%d} ~ {hi:%Y-%m-%d}] "
          f"· seed={SEED} · 총 {len(jobs)*a.pages} calls")
    print(f"[sec] out={out}")

    n = 0
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(fetch_chunk, m, d, a.pages): (m, d) for m, d in jobs}
            done = 0
            for fu in as_completed(futs):
                done += 1
                try:
                    rows = fu.result()
                except Exception as e:
                    print(f"\n[sec] chunk 실패 {futs[fu][0]}: {e}"); continue
                for r in rows:
                    fh.write(json.dumps(r, separators=(",", ":")) + "\n"); n += 1
                print(f"\r[sec] chunk {done}/{len(jobs)} · rows={n:,}", end="", flush=True)
    print(f"\n[sec] 완료: {n:,} rows → {out}")


if __name__ == "__main__":
    main()
