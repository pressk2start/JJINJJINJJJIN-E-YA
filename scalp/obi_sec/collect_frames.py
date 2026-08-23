"""collect_frames.py — forward 프레임 수집기 (오더북 스냅샷 + 구간 체결).

왜 forward 수집인가:
  Upbit 공개 API는 **과거 호가창을 제공하지 않는다**. 지금 이 순간의 스냅샷뿐.
  따라서 OBI 축의 데이터는 직접 쌓는 것 외에 방법이 없다.
  이 스크립트가 유일한 데이터 소스이며, 여기서 쌓인 구간 밖의 백테스트는 존재할 수 없다.

수집 방식:
  interval초마다: /orderbook (마켓 배치 1콜) + 마켓별 /trades/ticks (직전 interval 구간)
  → build_frame() → JSONL.gz append

레이트 리밋:
  공개 quotation = 초당 10회. 마켓 M개면 사이클당 1 + M 콜.
  기본 M=8 · interval=2s → 4.5 req/s. 마켓을 늘리면 interval도 함께 늘릴 것.

사용:
  python3 collect_frames.py --markets KRW-BTC,KRW-ETH --minutes 60 --interval 2
  python3 collect_frames.py --top 8 --minutes 240          # 거래대금 상위 자동 선정
  (출력: scalp/data/frames_YYYYmmdd_HHMMSS.jsonl.gz)

⚠ 성과 계산 없음 = discovery-performance contamination 아님. 봉인 전 자유 실행 가능.
"""
import os, sys, time, json, gzip, argparse, datetime

sys.path.insert(0, os.path.dirname(__file__) or ".")
import scalp as SC

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")


def pick_top_markets(n=8):
    """24h 누적 거래대금 상위 n개 KRW 마켓. 스켈핑은 유동성이 전제조건."""
    mk = SC.markets_krw()
    rows = []
    for i in range(0, len(mk), 100):
        js = SC._get("/ticker", {"markets": ",".join(mk[i:i + 100])}) or []
        rows += js
        time.sleep(SC.REQ_DELAY)
    rows.sort(key=lambda t: float(t.get("acc_trade_price_24h", 0)), reverse=True)
    return [t["market"] for t in rows[:n]]


def collect(markets, minutes, interval, out_path, depth=5, verbose=True):
    """지정 시간 동안 프레임을 모아 JSONL.gz로 append 저장. 반환: 저장 프레임 수."""
    os.makedirs(DATA_DIR, exist_ok=True)
    deadline = time.time() + minutes * 60.0
    last_tick_ts = {m: 0.0 for m in markets}   # 마켓별 마지막 반영 체결 timestamp(ms)
    n_written = 0
    fh = gzip.open(out_path, "at", encoding="utf-8")
    try:
        while time.time() < deadline:
            cycle_start = time.time()
            obs = SC.orderbook(markets)
            for m in markets:
                raw = obs.get(m)
                if not raw:
                    continue
                ticks = SC.trades_ticks(m, count=200)
                # 직전 사이클 이후 체결만 사용 (중복 계상 방지). timestamp = ms epoch.
                cut = last_tick_ts.get(m, 0.0)
                fresh = [t for t in ticks if float(t.get("timestamp", 0)) > cut]
                if ticks:
                    last_tick_ts[m] = max(float(t.get("timestamp", 0)) for t in ticks)
                f = SC.build_frame(cycle_start, m, raw, fresh, depth=depth)
                fh.write(json.dumps(f, separators=(",", ":")) + "\n")
                n_written += 1
                time.sleep(SC.REQ_DELAY)
            fh.flush()
            if verbose:
                left = int(deadline - time.time())
                print(f"\r[collect] frames={n_written} 남은시간={left}s", end="", flush=True)
            sleep = interval - (time.time() - cycle_start)
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        print("\n[collect] 사용자 중단 — 여기까지 저장됨", flush=True)
    finally:
        fh.close()
    if verbose:
        print(f"\n[collect] 완료: {n_written} frames → {out_path}", flush=True)
    return n_written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="", help="쉼표 구분 마켓 코드. 미지정 시 --top 사용")
    ap.add_argument("--top", type=int, default=8, help="거래대금 상위 N개 자동 선정")
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=2.0, help="사이클 주기(초)")
    ap.add_argument("--depth", type=int, default=5)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    markets = [s.strip() for s in a.markets.split(",") if s.strip()] or pick_top_markets(a.top)
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out = a.out or os.path.join(DATA_DIR, f"frames_{stamp}.jsonl.gz")

    cps = 1 + len(markets)                       # 사이클당 API 콜
    print(f"[collect] markets={markets}")
    print(f"[collect] interval={a.interval}s · 사이클당 {cps} calls ≈ {cps / a.interval:.1f} req/s "
          f"(공개 제한 10 req/s)")
    if cps / a.interval > 8:
        print("[collect] ⚠ 레이트리밋 위험 — 마켓 수를 줄이거나 interval을 늘리세요.")
    print(f"[collect] out={out}")
    collect(markets, a.minutes, a.interval, out, depth=a.depth)


if __name__ == "__main__":
    main()
