# -*- coding: utf-8 -*-
"""
업비트 1분봉 전용 수집기
========================
메인 스크립트(upbit_signal_study)에서 1분봉을 함께 수집하면
API 호출량이 과다하여 멈추는 문제를 해결하기 위해
1분봉만 별도로 수집하는 스크립트.

- 기본 30일치, 기존 파일 있으면 자동 스킵
- 청크 단위 수집+디스크 저장 (메모리 안전)
- 부분수집도 저장
- 종목별 진행상황 텔레그램 전송
- 잠금파일 별도 사용

사용법:
  python collect_1m.py                              # 기본 30일, 상위 30코인
  python collect_1m.py --days 7 --coins 10          # 7일치, 10코인
  python collect_1m.py --markets KRW-BTC,KRW-ETH    # 특정 종목만
  python collect_1m.py --force                      # 기존 파일 무시하고 재수집
"""

import os, sys, json, time, math, gc, argparse, atexit, signal as sig_mod
from datetime import datetime, timedelta, timezone
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# =========================================================
# 기본 설정
# =========================================================
KST = timezone(timedelta(hours=9))
BASE = "https://api.upbit.com/v1"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "candle_data")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".collect_1m_lock")

# 청크 단위 수집 설정 (메모리 보호)
CHUNK_SIZE = 5000       # 5000개씩 메모리에 유지 → 디스크에 임시 저장
MEM_LOG_INTERVAL = 10   # 10페이지마다 메모리 상태 로그

TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN") or ""
_raw = os.getenv("TG_CHATS") or os.getenv("TELEGRAM_CHAT_ID") or ""
CHAT_IDS = []
for p in _raw.split(","):
    p = p.strip()
    if p:
        try:
            CHAT_IDS.append(int(p))
        except Exception:
            pass

# =========================================================
# 공통 유틸
# =========================================================
def tg(msg):
    print(msg)
    if not TG_TOKEN or not CHAT_IDS:
        return
    chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)] if len(msg) > 4000 else [msg]
    for cid in CHAT_IDS:
        for chunk in chunks:
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={
                        "chat_id": cid,
                        "text": chunk,
                        "disable_web_page_preview": True,
                    },
                    timeout=10,
                )
                if r.status_code != 200:
                    print(f"[TG경고] {r.status_code}: {r.text[:200]}")
            except Exception as e:
                print(f"[TG에러] {e}")

def _acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            print(f"[잠금] 이미 실행중 (PID {old_pid}). 종료.")
            sys.exit(0)
        except (ProcessLookupError, ValueError, OSError):
            pass
    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

def _release_lock(*_args):
    try:
        os.remove(LOCK_FILE)
    except Exception:
        pass

atexit.register(_release_lock)
for _s in (sig_mod.SIGTERM, sig_mod.SIGINT):
    sig_mod.signal(_s, lambda s, f: (_release_lock(), sys.exit(0)))

def _get_mem_mb():
    """현재 프로세스 RSS 메모리(MB) 반환. psutil 없으면 /proc 사용."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except ImportError:
        pass
    try:
        with open(f"/proc/{os.getpid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # kB → MB
    except Exception:
        pass
    return -1

def safe_get(url, params=None, retries=6, timeout=12):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = min(5, 1 + i)
                print(f"[429] {url} | {wait}s 대기")
                time.sleep(wait)
                continue
            print(f"[HTTP {r.status_code}] {url} | {str(r.text)[:120]}")
            time.sleep(0.5 + i * 0.5)
        except requests.exceptions.Timeout:
            print(f"[Timeout] {url} | 재시도 {i+1}/{retries}")
            time.sleep(1 + i * 0.5)
        except Exception as e:
            print(f"[GET 에러] {url} | {e}")
            time.sleep(0.7 + i * 0.7)
    return None

# =========================================================
# 마켓 조회
# =========================================================
def get_top_markets(n=30):
    tickers = safe_get(f"{BASE}/market/all", {"isDetails": "true"})
    if not tickers:
        return []
    krw = [t["market"] for t in tickers if t["market"].startswith("KRW-")]
    stable = {"USDT", "USDC", "DAI", "TUSD", "BUSD"}
    krw = [m for m in krw if m.split("-")[1] not in stable]
    all_t = []
    for i in range(0, len(krw), 100):
        batch = safe_get(f"{BASE}/ticker", {"markets": ",".join(krw[i:i+100])})
        if batch:
            all_t.extend(batch)
        time.sleep(0.2)
    all_t.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
    return [t["market"] for t in all_t[:n]]

# =========================================================
# 파일 처리
# =========================================================
def compress(c):
    return {
        "t": c["candle_date_time_kst"],
        "o": c["opening_price"],
        "h": c["high_price"],
        "l": c["low_price"],
        "c": c["trade_price"],
        "v": round(c.get("candle_acc_trade_volume", 0), 6),
    }

def _chunk_path(coin, chunk_idx):
    """임시 청크 파일 경로"""
    return os.path.join(DATA_DIR, f".{coin}_1m_chunk{chunk_idx}.tmp")

def _save_chunk(coin, chunk_idx, chunk_data):
    """청크를 임시 파일에 저장하고 메모리 해제"""
    os.makedirs(DATA_DIR, exist_ok=True)
    fpath = _chunk_path(coin, chunk_idx)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(chunk_data, f, ensure_ascii=False)
    return len(chunk_data)

def _merge_chunks_and_save(coin, num_chunks):
    """임시 청크들을 병합하여 최종 파일 저장 (스트리밍 방식)"""
    all_candles = []
    for ci in range(num_chunks):
        cp = _chunk_path(coin, ci)
        if not os.path.exists(cp):
            continue
        with open(cp, "r", encoding="utf-8") as f:
            chunk = json.load(f)
        all_candles.extend(chunk)
        os.remove(cp)
        del chunk
    gc.collect()

    if not all_candles:
        return 0

    # 시간순 정렬 + 중복 제거
    all_candles.sort(key=lambda x: x["t"])
    seen = set()
    deduped = []
    for c in all_candles:
        if c["t"] not in seen:
            seen.add(c["t"])
            deduped.append(c)
    del all_candles, seen
    gc.collect()

    fpath = os.path.join(DATA_DIR, f"{coin}_1m.json")
    payload = {
        "coin": coin,
        "unit": 1,
        "count": len(deduped),
        "from": deduped[0]["t"] if deduped else "",
        "to": deduped[-1]["t"] if deduped else "",
        "candles": deduped,
        "saved_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    count = len(deduped)
    del deduped, payload
    gc.collect()
    return count

def _cleanup_chunks(coin, max_chunks=100):
    """비정상 종료 시 남은 청크 파일 정리"""
    for ci in range(max_chunks):
        cp = _chunk_path(coin, ci)
        if os.path.exists(cp):
            try:
                os.remove(cp)
            except Exception:
                pass

def get_saved_count_1m(coin):
    fpath = os.path.join(DATA_DIR, f"{coin}_1m.json")
    if not os.path.exists(fpath):
        return 0
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("count", 0))
    except Exception:
        return 0

def is_collected_1m(coin, need_count, margin_ratio=0.95):
    saved = get_saved_count_1m(coin)
    return saved >= int(need_count * margin_ratio)

# =========================================================
# 1분봉 수집 (청크 단위 메모리 관리)
# =========================================================
def fetch_and_save_1m(market, coin, total_count, max_time=600, progress_prefix=""):
    """1분봉 수집 + 청크 저장 (메모리 안전 버전)

    원본 API 응답에서 to 파라미터를 유지하면서
    CHUNK_SIZE마다 디스크에 내려놓는 구조.
    """
    _cleanup_chunks(coin)

    buffer = []
    chunk_idx = 0
    total_fetched = 0
    to = None
    start_ts = time.time()
    fails = 0
    target_pages = math.ceil(total_count / 200)

    while total_fetched < total_count:
        elapsed = time.time() - start_ts
        if elapsed > max_time:
            tg(f"{progress_prefix}시간초과 {int(elapsed)}초 → {total_fetched}개까지 저장")
            break

        remain = total_count - total_fetched
        count = min(200, remain)
        params = {"market": market, "count": count}
        if to:
            params["to"] = to

        data = safe_get(f"{BASE}/candles/minutes/1", params=params, retries=6, timeout=12)

        if not data:
            fails += 1
            if fails >= 5:
                tg(f"{progress_prefix}연속 {fails}회 실패, 중단 ({total_fetched}개까지)")
                break
            time.sleep(1.0 + fails * 0.5)
            continue

        fails = 0
        got = len(data)

        # to 갱신 (다음 페이지용) — 원본 데이터에서 추출
        if got > 0:
            to = data[-1]["candle_date_time_utc"] + "Z"

        # 압축해서 버퍼에 추가
        for c in data:
            buffer.append(compress(c))
        total_fetched += got
        del data

        page_no = math.ceil(total_fetched / 200)
        pct = total_fetched / total_count * 100

        # 주기적 로그
        if page_no % MEM_LOG_INTERVAL == 0 or got < 200:
            mem = _get_mem_mb()
            mem_str = f" | mem={mem:.0f}MB" if mem > 0 else ""
            print(f"{progress_prefix}{total_fetched:5d}/{total_count} ({pct:.0f}%) "
                  f"page {page_no}/{target_pages}{mem_str}")

        # 청크 저장
        if len(buffer) >= CHUNK_SIZE:
            _save_chunk(coin, chunk_idx, buffer)
            saved_n = len(buffer)
            chunk_idx += 1
            buffer.clear()
            gc.collect()
            mem = _get_mem_mb()
            mem_str = f" | mem={mem:.0f}MB" if mem > 0 else ""
            tg(f"{progress_prefix}청크#{chunk_idx-1} 디스크 저장 ({saved_n}개, 누적 {total_fetched}개){mem_str}")

        if got < 200:
            break

        time.sleep(0.35)

    # 나머지 버퍼 저장
    if buffer:
        _save_chunk(coin, chunk_idx, buffer)
        chunk_idx += 1
        buffer.clear()
        gc.collect()

    if chunk_idx == 0:
        return 0

    # 청크 병합 → 최종 파일
    final_count = _merge_chunks_and_save(coin, chunk_idx)
    return final_count


def collect_1m(days=30, top_n=30, force=False, specific_markets=None, max_time_per_coin=600):
    """1분봉만 수집. 30일 이내 권장."""
    if days > 30:
        tg(f"[경고] 1분봉은 30일 초과 요청이 비효율적이라 30일로 제한")
        days = 30

    need_count = 1440 * days

    if specific_markets:
        markets = [m.strip().upper() for m in specific_markets if m.strip()]
    else:
        markets = get_top_markets(top_n)

    if not markets:
        tg("[오류] 대상 마켓 조회 실패")
        return []

    coins = [m.split("-")[1] for m in markets]
    tg(f"[1분봉 수집 시작] days={days}, need={need_count:,}개/코인, coins={len(coins)}")
    tg(f"대상: {', '.join(coins)}")
    mem = _get_mem_mb()
    if mem > 0:
        tg(f"시작 메모리: {mem:.0f}MB")

    started = time.time()
    done = 0
    skipped = 0
    failed = 0

    for idx, market in enumerate(markets, start=1):
        coin = market.split("-")[1]
        prefix = f"[{idx}/{len(markets)}] {coin} | "

        try:
            saved = get_saved_count_1m(coin)
            if (not force) and is_collected_1m(coin, need_count):
                skipped += 1
                tg(f"{prefix}스킵 (기존 {saved:,}개)")
                continue

            tg(f"{prefix}수집 시작 (기존 {saved:,}개, 목표 {need_count:,}개)")
            count = fetch_and_save_1m(
                market=market,
                coin=coin,
                total_count=need_count,
                max_time=max_time_per_coin,
                progress_prefix=f"  {coin}: ",
            )

            if count > 0:
                done += 1
                tg(f"{prefix}저장 완료 ({count:,}개)")
            else:
                failed += 1
                tg(f"{prefix}수집 실패 (0개)")

            gc.collect()
            time.sleep(0.6)

        except Exception as e:
            failed += 1
            tg(f"{prefix}예외: {e}")
            _cleanup_chunks(coin)

        if idx % 5 == 0 or idx == len(markets):
            elapsed = time.time() - started
            avg = elapsed / idx
            remain = avg * (len(markets) - idx)
            mem = _get_mem_mb()
            mem_str = f" | mem={mem:.0f}MB" if mem > 0 else ""
            tg(
                f"[진행] {idx}/{len(markets)} | 완료 {done} | 스킵 {skipped} | 실패 {failed} "
                f"| 경과 {elapsed/60:.1f}분 | 남은 예상 {remain/60:.1f}분{mem_str}"
            )

    total_elapsed = time.time() - started
    tg(
        f"[1분봉 수집 완료] 완료={done}, 스킵={skipped}, 실패={failed}, "
        f"총 {total_elapsed/60:.1f}분"
    )

    # 최종 요약: 전체 1분봉 파일 현황
    if os.path.exists(DATA_DIR):
        files_1m = [f for f in os.listdir(DATA_DIR) if f.endswith("_1m.json")]
        total_candles = 0
        for f in files_1m:
            try:
                with open(os.path.join(DATA_DIR, f)) as fp:
                    total_candles += json.load(fp).get("count", 0)
            except Exception:
                pass
        tg(f"  1분봉 파일: {len(files_1m)}개 | 총 캔들: {total_candles:,}개")

    return coins

# =========================================================
# 메인
# =========================================================
def main():
    _acquire_lock()

    parser = argparse.ArgumentParser(description="업비트 1분봉 전용 수집기")
    parser.add_argument("--days", type=int, default=30,
                        help="수집 일수 (기본: 30)")
    parser.add_argument("--coins", type=int, default=30,
                        help="상위 거래대금 기준 코인 수 (기본: 30)")
    parser.add_argument("--force", action="store_true",
                        help="기존 파일 있어도 강제 재수집")
    parser.add_argument("--markets", type=str, default="",
                        help="직접 지정할 마켓. 예: KRW-BTC,KRW-ETH,KRW-XRP")
    parser.add_argument("--max-time-per-coin", type=int, default=600,
                        help="코인당 최대 수집 시간(초) (기본: 600)")
    args = parser.parse_args()

    tg("[시작] 1분봉 전용 수집기 실행")

    specific_markets = []
    if args.markets.strip():
        specific_markets = [x.strip() for x in args.markets.split(",") if x.strip()]

    collect_1m(
        days=args.days,
        top_n=args.coins,
        force=args.force,
        specific_markets=specific_markets if specific_markets else None,
        max_time_per_coin=args.max_time_per_coin,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _release_lock()
        tg("[중단] 사용자 중단")
    except Exception as e:
        import traceback
        _release_lock()
        tg(f"[에러] {e}\n{traceback.format_exc()[-800:]}")
