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
1. **원자료 불변.** 수신한 JSON을 그대로 저장하고 `recv_ts`(로컬 수신 epoch ms)와
   `_seq`(세션 내 수신 순번, 동일 ms 이벤트의 결정적 정렬용)만 덧붙인다.
   특징량(OBI/microprice/ΔOBI/queue depletion/signed flow…)은 **후처리에서** 계산한다.
   지금 집계해서 저장하면 나중에 다른 정의로 다시 못 만든다. 오더북은 소급 불가다.

   ⚠ `--depth N` (N>0) 은 이 원칙의 **유일한 예외이며 되돌릴 수 없는 손실**이다.
     N단만 남기고 나머지는 영구히 사라진다. `units_total` 로 자른 사실은 남지만 데이터는 안 남는다.
     그래서 **기본값은 0(30단 전량 보존)** 이다. discovery 기간에는 절대 자르지 말 것.
     용량이 문제일 때만 명시적으로 지정한다 (20종목 기준 30단 ≈ 1.2GB/일, 15단 ≈ 0.9GB/일).
2. **손실 구간을 명시.** 재접속·끊김·구독실패를 `_meta` 이벤트로 같은 스트림에 기록한다.
   후처리는 이 구간을 반드시 제외해야 한다. 조용히 이어붙이면 없는 연속성을 만들어낸다.
3. **두 시각을 모두 보존.** `timestamp`(거래소) 와 `recv_ts`(수신) 를 함께 남긴다.
   둘의 차이가 곧 latency 실측치다 — 지금까지 3bp로 **가정**하던 값을 대체한다.
4. **주문 코드 없음.** 읽기 전용 공개 스트림. 인증·API 키·주문 함수 일절 없음.

구독 파라미터 — depth 와 level 은 다른 것이다 (실측 확인)
  · depth = 몇 호가를 저장하느냐 (개수)
  · level = 거래소가 호가를 **묶어서** 보내느냐 (가격 집계 단위)
  구독 코드에 `.{n}` 을 붙이면 **개수**가 잘린다 (KRW-BTC.15 → units=15).
  level 은 그 경우에도 0 으로 유지되고 가격 간격도 tick 그대로다 = 뭉개지지는 않는다.
  이 레코더는 **접미사를 쓰지 않는다** → level=0 · 30단 전량 수신.
  연구 원자료로는 이게 가장 깨끗하다: level 0 · 무절단(--depth 0, 기본값).

trade 이벤트가 touch를 함께 싣는다 (실측 확인)
  best_bid_price/size · best_ask_price/size 가 체결 메시지에 포함된다.
  → 체결 시점의 스프레드·microprice를 별도 조인 없이 정확히 복원할 수 있다.
  → ask_bid 필드가 테이커 방향(BID=테이커 매수)을 직접 준다.

출력: data/ws/{YYYY-MM-DD}/{HH}.jsonl.gz  (UTC 시간별 로테이션)

사용 (한국 VPS 권장):
  nohup python3 ws_recorder.py --top 20 > ws_recorder.log 2>&1 &
  nohup python3 ws_recorder.py --markets KRW-XRP,KRW-ETH,KRW-SOL,KRW-TRUMP --depth 30 &
"""
import os, sys, json, gzip, time, signal, asyncio, argparse, datetime, ssl, shutil
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect

URL = "wss://api.upbit.com/websocket/v1"
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ws")
CA = "/root/.ccr/ca-bundle.crt"          # 프록시 환경용. 없으면 시스템 기본.

# ⚠ 디스크 안전장치 — 이 레코더는 **라이브 봇과 같은 디스크**를 쓸 수 있다.
#   연구용 수집이 디스크를 채워 bot.py 를 죽이는 일은 절대 없어야 한다.
#   실측(2026-08-23): 20종목 depth0 = 0.96GB/일 (종목당 0.048GB/일).
#   대상 서버가 Lightsail nano(20GB 디스크, 여유 8.2GB)면 20종목은 8.5일이면 가득 찬다.
MIN_FREE_GB = 2.0      # 여유가 이보다 적으면 **수집을 중단**한다 (디스크를 채우지 않는다)
SETUP_FAIL_LIMIT = 5   # 한 번도 못 붙은 채 같은 오류가 이만큼 반복되면 중단
RETAIN_DAYS = 14       # 이보다 오래된 날짜 디렉터리는 시간 로테이션 때 자동 삭제

_stop = False


def _sig(*_):
    global _stop
    _stop = True


def _request_stop():
    """함수 안에서 global 선언 없이 중단을 요청한다 (디스크 가드 등)."""
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
        self.rotated = False
        self.cur_path = None
        self.comp_closed = 0        # 닫힌 파일들의 압축 후 바이트 합
        self.t0 = time.time()

    def _cur_size(self):
        try:
            return os.path.getsize(self.cur_path) if self.cur_path else 0
        except OSError:
            return 0

    def gb_per_day(self):
        """압축 후 실측 증가율. 시작 배너의 추정치를 대체한다.
        표본이 짧으면 신뢰할 수 없으므로 10분 미만이면 None 을 준다."""
        el = time.time() - self.t0
        if el < 600:
            return None
        return (self.comp_closed + self._cur_size()) / el * 86400 / 1e9

    def free_gb(self):
        return shutil.disk_usage(self.root).free / 1e9

    def purge_old(self, retain_days):
        """오래된 날짜 디렉터리 삭제. 삭제한 목록을 반환(호출측이 메타로 남긴다)."""
        if not retain_days or not os.path.isdir(self.root):
            return []
        cutoff = (datetime.datetime.utcnow()
                  - datetime.timedelta(days=retain_days)).strftime("%Y-%m-%d")
        gone = []
        for d in sorted(os.listdir(self.root)):
            full = os.path.join(self.root, d)
            if os.path.isdir(full) and len(d) == 10 and d < cutoff:
                try:
                    shutil.rmtree(full); gone.append(d)
                except OSError:
                    pass
        return gone

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
                self.comp_closed += self._cur_size()
            self.cur_path = self._path(now)
            # level 9 는 이 파이프라인에서 CPU 를 지배한다. 실측(2026-08-24, 실데이터
            # 2.3MB): lvl9 대비 lvl6 은 2.8배 빠르고 크기는 17% 크다. lvl1 은 5.3배
            # 빠르지만 2.4배 커진다 — 여기서는 디스크가 병목이라 6 이 균형점이다.
            self.fh = gzip.open(self.cur_path, "at", encoding="utf-8",
                                compresslevel=6)
            self.key = key
            self.rotated = True          # 호출측이 시간마다 디스크 점검/정리하도록
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


async def run(markets, depth, writer, stats, retain_days=RETAIN_DAYS,
              min_free_gb=MIN_FREE_GB):
    """한 번의 연결 수명. 정상/비정상 종료 모두 _meta 로 남긴다."""
    import websockets
    # CA 번들이 있으면 그걸 쓰고, 없으면 시스템 기본 컨텍스트를 만든다.
    # ssl=None 을 그대로 넘기면 최신 websockets 가 wss:// 에서 ValueError 를 낸다.
    ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) \
        else ssl.create_default_context()
    sub = [{"ticket": f"scalp-rec-{int(time.time())}"},
           # level=0 = 모아보기 없음(원본 격자). depth(단수)와 level(가격 집계)은 별개다.
           # level>0 이면 거래소가 호가를 묶어 보내고 그 안의 ΔOBI·depletion 이 사라진다.
           # 기본값이 0이더라도 연구 원칙(원본 격자 보존)을 코드에 박아 애매함을 없앤다.
           {"type": "orderbook", "codes": markets, "level": 0},
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
                      "depth_note": "0=30단 전량 보존, N>0=N단만 남기고 영구 손실",
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
            stats["recv_seq"] += 1
            m["_seq"] = stats["recv_seq"]  # 수신 순번 — event_ts 동률 시 secondary key
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

            # ---- 디스크 가드: 시간 로테이션마다 정리하고, 여유가 없으면 중단 ----
            if writer.rotated:
                writer.rotated = False
                gone = writer.purge_old(retain_days)
                free = writer.free_gb()
                if gone:
                    writer.write({"_meta": "purge", "recv_ts": int(time.time() * 1000),
                                  "removed_days": gone, "free_gb": round(free, 2)})
                if free < min_free_gb:
                    writer.write({"_meta": "disk_stop", "recv_ts": int(time.time() * 1000),
                                  "free_gb": round(free, 2), "min_free_gb": min_free_gb,
                                  "note": "디스크 여유 부족 → 수집 중단. 라이브 봇을 지키기 위함."})
                    print(f"[ws] ⚠ 디스크 여유 {free:.2f}GB < {min_free_gb}GB → 수집 중단",
                          flush=True)
                    _request_stop()
                    return

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
                              "written": writer.n, "bytes": writer.bytes,
                              "gb_per_day": writer.gb_per_day()})
                gbd = writer.gb_per_day()
                cap = (f" · 실측 {gbd:.2f}GB/일 → {retain_days}일 보관 시 "
                       f"{gbd*retain_days:.1f}GB (여유 {writer.free_gb():.1f}GB)"
                       if gbd else "")
                print(f"[ws] up={el/60:.1f}분 ob={stats['ob']:,} tr={stats['tr']:,} "
                      f"lat={lat_avg:.0f}ms 기록={writer.n:,}행 "
                      f"{writer.bytes/1024/1024:.1f}MB(비압축){cap}", flush=True)
                last_hb = now


async def main_async(a):
    stats = {"ob": 0, "tr": 0, "connects": 0, "seq_anomaly": 0, "recv_seq": 0,
             "last_seq": {}, "last_disconnect": None, "lat_sum": 0.0, "lat_n": 0}
    writer = Writer(DIR)
    markets = ([s.strip() for s in a.markets.split(",") if s.strip()]
               or [m for m, _ in collect.krw_markets_by_value()[:a.top]])
    os.makedirs(DIR, exist_ok=True)
    free0 = writer.free_gb()
    # 20종목 평균(0.96GB/일 ÷ 20). 거래대금 상위만 고르면 종목당 2배까지 간다
    # — 실측 2026-08-24: XRP/TRUMP/ETH/SOL/BTC 5종목이 0.50GB/일(종목당 0.10).
    # 그래서 이 값은 하한으로만 읽고, 10분 뒤부터 하트비트의 "실측"을 믿는다.
    est = 0.048 * len(markets)
    print(f"[ws] {len(markets)} markets · depth={a.depth} · 출력 {DIR}", flush=True)
    print(f"[ws] 디스크 여유 {free0:.1f}GB · 예상(하한) {est:.2f}GB/일 "
          f"→ 보관 {a.retain_days}일이면 정상상태 약 {est*a.retain_days:.1f}GB "
          f"· 여유 {a.min_free_gb}GB 미만이면 자동 중단", flush=True)
    if free0 < a.min_free_gb * 2:
        print(f"[ws] ⚠ 디스크 여유가 빠듯하다 ({free0:.1f}GB). "
              f"--markets 로 종목을 줄이거나 --retain-days 를 낮출 것.", flush=True)
    print(f"[ws] markets: {','.join(markets)}", flush=True)
    backoff = 1.0
    first_error, first_error_n = None, 0
    try:
        while not _stop:
            try:
                await run(markets, a.depth, writer, stats, a.retain_days, a.min_free_gb)
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
                # 한 번도 붙은 적이 없는데 같은 예외가 반복되면 네트워크가 아니라 설정
                # 문제다. 무한 재시도로 로그만 채우지 말고 끊어서 눈에 띄게 만든다.
                if stats["connects"] == 0:
                    sig = f"{type(e).__name__}: {str(e)[:200]}"
                    if sig == first_error:
                        first_error_n += 1
                    else:
                        first_error, first_error_n = sig, 1
                    if first_error_n >= SETUP_FAIL_LIMIT:
                        writer.write({"_meta": "setup_failed", "error": sig,
                                      "recv_ts": int(time.time() * 1000),
                                      "attempts": first_error_n})
                        print(f"[ws] ✗ 접속에 한 번도 성공하지 못했고 같은 오류가 "
                              f"{first_error_n}회 반복됐다 — 설정 문제로 보고 중단한다.\n"
                              f"    {sig}", flush=True)
                        _request_stop()
                        break
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
    ap.add_argument("--depth", type=int, default=0,
                    help="저장할 호가 단수. **0(기본)=자르지 않음 = 30단 전량 보존.** "
                         "N>0 은 되돌릴 수 없는 손실이므로 discovery 기간에는 쓰지 말 것.")
    ap.add_argument("--hours", type=float, default=0, help="0=무한")
    ap.add_argument("--retain-days", type=int, default=RETAIN_DAYS,
                    help="이보다 오래된 날짜 디렉터리 자동 삭제 (0=삭제 안 함)")
    ap.add_argument("--min-free-gb", type=float, default=MIN_FREE_GB,
                    help="디스크 여유가 이보다 적으면 수집 중단 (라이브 봇 보호)")
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
