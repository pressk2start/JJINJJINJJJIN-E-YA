# -*- coding: utf-8 -*-
"""ws_recorder.py — Upbit public WebSocket 이벤트 레코더 (장기 전방 수집의 기준).

왜 REST 폴링이 아니라 WebSocket인가
-----------------------------------
REST 방식은 사이클마다 `orderbook 스냅샷 → 종목별 trades/ticks 호출`을 돈다.
20종목이면 사이클당 21콜이고, 이 컨테이너에서 실측한 사이클 주기는 6.4초였다.
그 결과 **OBI와 체결흐름이 같은 시점의 시장 상태가 아니게 된다.**
스켈핑에서 2~6초 어긋남은 치명적이다 — 측정하려는 현상보다 오차가 크다.

WebSocket이면 orderbook과 trade가 같은 연결로 각자의 발생 시각을 달고 들어온다.
    ORDERBOOK event ─┐
                     ├→ event-time 정렬 → feature frame
    TRADE event ─────┘
따라서 OBI(t) · microprice(t) · signed trade flow(t−1s:t) 를 실제로 맞춰볼 수 있다.

실측 (2026-08-23, 상위 20종목 30초):
  · orderbook 73.1건/s (종목당 3.7/s, 갱신간격 p50 100ms) · 평균 2,892B (30호가)
  · trade 17.2건/s · 평균 457B
  · 원본 214KB/s = 18.9GB/일  → depth 15로 자르면 약 절반, gzip 후 다시 1/5~1/8
  · 수신지연 recv−exchange_ts: p50 88ms (이 컨테이너. 한국 VPS는 훨씬 작다)

설계 원칙
---------
1. **원자료 불변.** 수신한 JSON을 그대로 저장하고 `recv_ts`(로컬 수신 epoch ms)만 덧붙인다.
   특징량(OBI/microprice/ΔOBI/queue depletion/signed flow…)은 **후처리에서** 계산한다.
   지금 집계해서 저장하면 나중에 다른 정의로 다시 못 만든다. 오더북은 소급 불가다.
2. **손실 구간을 명시.** 재접속·끊김·구독실패를 `_meta` 이벤트로 같은 스트림에 기록한다.
   후처리는 이 구간을 반드시 제외해야 한다. 조용히 이어붙이면 없는 연속성을 만들어낸다.
3. **두 시각을 모두 보존.** `timestamp`(거래소) 와 `recv_ts`(수신) 를 함께 남긴다.
   둘의 차이가 곧 latency 실측치다 — 지금까지 3bp로 **가정**하던 값을 대체한다.
4. **주문 코드 없음.** 읽기 전용 공개 스트림. 인증·API 키·주문 함수 일절 없음.

trade 이벤트가 touch를 함께 싣는다 (실측 확인)
  best_bid_price/size · best_ask_price/size 가 체결 메시지에 포함된다.
  → 체결 시점의 스프레드·microprice를 별도 조인 없이 정확히 복원할 수 있다.
  → ask_bid 필드가 테이커 방향(BID=테이커 매수)을 직접 준다.

출력: data/ws/{YYYY-MM-DD}/{HH}.jsonl.gz  (UTC 시간별 로테이션)

사용 (한국 VPS 권장):
  nohup python3 ws_recorder.py --top 20 > ws_recorder.log 2>&1 &
  nohup python3 ws_recorder.py --markets KRW-XRP,KRW-ETH,KRW-SOL,KRW-TRUMP --depth 30 &
"""
import os, sys, json, gzip, time, signal, asyncio, argparse, datetime, ssl
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect

URL = "wss://api.upbit.com/websocket/v1"
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ws")
CA = "/root/.ccr/ca-bundle.crt"          # 프록시 환경용. 없으면 시스템 기본.

_stop = False


def _sig(*_):
    global _stop
    _stop = True
    print("[ws] 종료 신호 — 파일 닫는 중", flush=True)


class Writer:
    """UTC 시간별 gzip JSONL 로테이션. 원자료 1행 = 1이벤트."""

    def __init__(self, root):
        self.root = root
        self.key = None
        self.fh = None
        self.n = 0
        self.bytes = 0

    def _path(self, now):
        d = now.strftime("%Y-%m-%d")
        os.makedirs(os.path.join(self.root, d), exist_ok=True)
        return os.path.join(self.root, d, now.strftime("%H") + ".jsonl.gz")

    def write(self, obj):
        now = datetime.datetime.utcnow()
        key = now.strftime("%Y%m%d%H")
        if key != self.key:
            if self.fh:
                self.fh.close()
            self.fh = gzip.open(self._path(now), "at", encoding="utf-8")
            self.key = key
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        self.fh.write(line + "\n")
        self.n += 1
        self.bytes += len(line) + 1

    def flush(self):
        if self.fh:
            self.fh.flush()

    def close(self):
        if self.fh:
            self.fh.close()
            self.fh = None


async def run(markets, depth, writer, stats):
    """한 번의 연결 수명. 정상/비정상 종료 모두 _meta 로 남긴다."""
    import websockets
    ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else None
    sub = [{"ticket": f"scalp-rec-{int(time.time())}"},
           {"type": "orderbook", "codes": markets},
           {"type": "trade", "codes": markets},
           {"format": "DEFAULT"}]
    connected_at = None
    async with websockets.connect(URL, ssl=ctx, open_timeout=20, max_size=None,
                                  ping_interval=20, ping_timeout=20) as ws:
        connected_at = time.time()
        gap_ms = None
        if stats["last_disconnect"]:
            gap_ms = int((connected_at - stats["last_disconnect"]) * 1000)
        writer.write({"_meta": "connect", "recv_ts": int(connected_at * 1000),
                      "markets": markets, "depth": depth,
                      "gap_since_last_disconnect_ms": gap_ms,
                      "note": "이 이벤트 이전 gap 구간은 데이터 없음 — 후처리에서 제외할 것"})
        await ws.send(json.dumps(sub))
        stats["connects"] += 1
        last_hb = time.time()
        while not _stop:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                # 30초 무수신 = 비정상. 끊고 재접속시켜 손실 구간을 명시적으로 남긴다.
                writer.write({"_meta": "recv_timeout", "recv_ts": int(time.time() * 1000)})
                raise ConnectionError("30s no message")
            recv_ts = int(time.time() * 1000)
            try:
                m = json.loads(raw if isinstance(raw, str) else raw.decode())
            except Exception:
                continue
            t = m.get("type")
            if t is None:                       # PING 응답 등 상태 메시지
                writer.write({"_meta": "status", "recv_ts": recv_ts, "raw": m})
                continue
            m["recv_ts"] = recv_ts
            code = m.get("code")
            if t == "orderbook":
                u = m.get("orderbook_units") or []
                m["units_total"] = len(u)       # 자른 사실을 명시 (조용한 절단 금지)
                if depth and len(u) > depth:
                    m["orderbook_units"] = u[:depth]
                stats["ob"] += 1
            elif t == "trade":
                seq = m.get("sequential_id")
                prev = stats["last_seq"].get(code)
                if prev is not None and seq is not None and seq <= prev:
                    stats["seq_anomaly"] += 1
                    m["_seq_anomaly"] = True    # 역행/중복 = 후처리에서 판단하도록 표시만
                if seq is not None:
                    stats["last_seq"][code] = seq
                stats["tr"] += 1
                lat = recv_ts - m.get("timestamp", recv_ts)
                stats["lat_sum"] += lat
                stats["lat_n"] += 1
            writer.write(m)

            now = time.time()
            if now - last_hb >= 60:
                writer.flush()
                el = now - connected_at
                lat_avg = stats["lat_sum"] / max(stats["lat_n"], 1)
                writer.write({"_meta": "heartbeat", "recv_ts": int(now * 1000),
                              "uptime_sec": round(el, 1),
                              "n_orderbook": stats["ob"], "n_trade": stats["tr"],
                              "seq_anomaly": stats["seq_anomaly"],
                              "latency_ms_avg": round(lat_avg, 1),
                              "written": writer.n, "bytes": writer.bytes})
                print(f"[ws] up={el/60:.1f}분 ob={stats['ob']:,} tr={stats['tr']:,} "
                      f"lat={lat_avg:.0f}ms 기록={writer.n:,}행 "
                      f"{writer.bytes/1024/1024:.1f}MB(비압축)", flush=True)
                last_hb = now


async def main_async(a):
    stats = {"ob": 0, "tr": 0, "connects": 0, "seq_anomaly": 0,
             "last_seq": {}, "last_disconnect": None, "lat_sum": 0.0, "lat_n": 0}
    writer = Writer(DIR)
    markets = ([s.strip() for s in a.markets.split(",") if s.strip()]
               or [m for m, _ in collect.krw_markets_by_value()[:a.top]])
    print(f"[ws] {len(markets)} markets · depth={a.depth} · 출력 {DIR}", flush=True)
    print(f"[ws] markets: {','.join(markets)}", flush=True)
    backoff = 1.0
    try:
        while not _stop:
            try:
                await run(markets, a.depth, writer, stats)
                backoff = 1.0
            except Exception as e:
                if _stop:
                    break
                stats["last_disconnect"] = time.time()
                writer.write({"_meta": "disconnect", "recv_ts": int(time.time() * 1000),
                              "error": f"{type(e).__name__}: {str(e)[:200]}",
                              "retry_in_sec": backoff})
                writer.flush()
                print(f"[ws] 끊김 ({type(e).__name__}: {str(e)[:80]}) · {backoff:.0f}초 후 재접속",
                      flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
    finally:
        writer.write({"_meta": "shutdown", "recv_ts": int(time.time() * 1000),
                      "n_orderbook": stats["ob"], "n_trade": stats["tr"],
                      "connects": stats["connects"], "seq_anomaly": stats["seq_anomaly"]})
        writer.close()
        print(f"[ws] 종료 · orderbook={stats['ob']:,} trade={stats['tr']:,} "
              f"연결={stats['connects']}회 seq이상={stats['seq_anomaly']}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--markets", default="")
    ap.add_argument("--depth", type=int, default=15,
                    help="저장할 호가 단수 (Upbit는 30단 전송). 0=자르지 않음. "
                         "queue depletion은 상위 5단이 핵심이라 15면 충분하다.")
    ap.add_argument("--hours", type=float, default=0, help="0=무한")
    a = ap.parse_args()
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    if a.hours:
        async def bounded():
            task = asyncio.create_task(main_async(a))
            await asyncio.sleep(a.hours * 3600)
            _sig()
            await task
        asyncio.run(bounded())
    else:
        asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
