# -*- coding: utf-8 -*-
"""
업비트 1분봉 전용 수집기
========================
메인 스크립트(upbit_signal_study)에서 1분봉을 함께 수집하면
API 호출량이 과다하여 멈추는 문제를 해결하기 위해
1분봉만 별도로 수집하는 스크립트.

사용법:
  python collect_1m.py                  # 기본 7일, 상위 30코인
  python collect_1m.py --days 3         # 3일치
  python collect_1m.py --coins 10       # 상위 10코인만
  python collect_1m.py --coins 10 --resume  # 이미 수집된 코인 건너뛰기
"""

import requests, time, json, os, sys, gc, argparse, atexit, signal as sig_mod
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
BASE = "https://api.upbit.com/v1"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "candle_data")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".collect_1m_lock")

# ── 중복 실행 방지 ──
def _acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            print(f"[잠금] 이미 실행중 (PID {old_pid}). 종료.")
            sys.exit(0)
        except (ProcessLookupError, ValueError, OSError):
            pass
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

def _release_lock(*a):
    try:
        os.remove(LOCK_FILE)
    except:
        pass

atexit.register(_release_lock)
for _s in (sig_mod.SIGTERM, sig_mod.SIGINT):
    sig_mod.signal(_s, lambda s, f: (_release_lock(), sys.exit(0)))

# ── 텔레그램 ──
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN") or ""
_raw = os.getenv("TG_CHATS") or os.getenv("TELEGRAM_CHAT_ID") or ""
CHAT_IDS = []
for p in _raw.split(","):
    p = p.strip()
    if p:
        try:
            CHAT_IDS.append(int(p))
        except:
            pass

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
                    json={"chat_id": cid, "text": chunk, "disable_web_page_preview": True},
                    timeout=10)
                if r.status_code != 200:
                    print(f"[TG경고] {r.status_code}: {r.text[:200]}")
            except Exception as e:
                print(f"[TG에러] {e}")

def safe_get(url, params=None, retries=3, timeout=10):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 1.5 + i * 1.5
                print(f"  [429 Rate Limit] {wait:.1f}초 대기...")
                time.sleep(wait)
                continue
            time.sleep(0.5)
        except requests.exceptions.Timeout:
            print(f"  [Timeout] 재시도 {i+1}/{retries}")
            time.sleep(1)
        except Exception as e:
            print(f"  [요청에러] {e}")
            time.sleep(0.5)
    return None

# ── 종목 조회 ──
def get_top_markets(n=30):
    tickers = safe_get(f"{BASE}/market/all", {"isDetails": "true"})
    if not tickers:
        return []
    krw = [t["market"] for t in tickers if t["market"].startswith("KRW-")]
    stable = {"USDT", "USDC", "DAI", "TUSD", "BUSD"}
    krw = [m for m in krw if m.split("-")[1] not in stable]
    all_t = []
    for i in range(0, len(krw), 100):
        b = safe_get(f"{BASE}/ticker", {"markets": ",".join(krw[i:i+100])})
        if b:
            all_t.extend(b)
        time.sleep(0.15)
    all_t.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
    return [t["market"] for t in all_t[:n]]

# ── 1분봉 수집 (느린 페이싱) ──
def fetch_1m_candles(market, total_count, max_time=300):
    """1분봉 수집. 느린 페이싱으로 API 부하 최소화.
    - 페이지당 200개
    - 요청 간 0.3초 대기 (429 방지)
    - max_time초 초과 시 지금까지 수집한 것만 반환
    """
    result = []
    to = None
    t0 = time.time()
    fails = 0
    total_pages = (total_count + 199) // 200

    for page in range(total_pages):
        elapsed = time.time() - t0
        if elapsed > max_time:
            print(f"      시간초과 {max_time}초, {len(result)}개까지 수집")
            break

        remaining = total_count - len(result)
        if remaining <= 0:
            break

        params = {"market": market, "count": min(200, remaining)}
        if to:
            params["to"] = to

        data = safe_get(f"{BASE}/candles/minutes/1", params, retries=3, timeout=10)

        if not data:
            fails += 1
            if fails >= 5:
                print(f"      연속 {fails}회 실패, 중단")
                break
            time.sleep(1)
            continue

        fails = 0
        result.extend(data)

        if len(data) < 200:
            break

        to = data[-1]["candle_date_time_utc"] + "Z"

        # 1분봉은 API 부하가 크므로 넉넉하게 대기
        time.sleep(0.3)

        # 진행률 표시 (10페이지마다)
        if (page + 1) % 10 == 0:
            pct = len(result) / total_count * 100
            print(f"      {len(result)}/{total_count} ({pct:.0f}%) - {elapsed:.0f}초")

    result.sort(key=lambda x: x["candle_date_time_kst"])
    return result

def compress(c):
    return {
        "t": c["candle_date_time_kst"],
        "o": c["opening_price"],
        "h": c["high_price"],
        "l": c["low_price"],
        "c": c["trade_price"],
        "v": round(c.get("candle_acc_trade_volume", 0), 6),
    }

def save_coin(coin, candles):
    os.makedirs(DATA_DIR, exist_ok=True)
    comp = [compress(c) for c in candles]
    fpath = os.path.join(DATA_DIR, f"{coin}_1m.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump({
            "coin": coin,
            "unit": 1,
            "count": len(comp),
            "from": comp[0]["t"] if comp else "",
            "to": comp[-1]["t"] if comp else "",
            "candles": comp,
        }, f, ensure_ascii=False)
    return fpath

def is_collected(coin, min_count=100):
    fpath = os.path.join(DATA_DIR, f"{coin}_1m.json")
    if not os.path.exists(fpath):
        return False
    try:
        with open(fpath) as f:
            data = json.load(f)
        return data.get("count", 0) >= min_count
    except:
        return False

# ── 메인 ──
def main():
    _acquire_lock()

    parser = argparse.ArgumentParser(description="업비트 1분봉 전용 수집기")
    parser.add_argument("--days", type=int, default=7,
                        help="수집 일수 (기본: 7, 최대 권장: 7)")
    parser.add_argument("--coins", type=int, default=30,
                        help="상위 N코인 (기본: 30)")
    parser.add_argument("--resume", action="store_true",
                        help="이미 수집된 코인 건너뛰기")
    parser.add_argument("--max-time-per-coin", type=int, default=300,
                        help="코인당 최대 수집 시간(초) (기본: 300)")
    args = parser.parse_args()

    # 7일 초과 시 경고
    if args.days > 7:
        tg(f"[경고] 1분봉 {args.days}일은 API 호출이 매우 많습니다. 7일로 제한합니다.")
        args.days = 7

    total_per_day = 1440  # 1분봉: 하루 1440개
    total_count = total_per_day * args.days

    tg(f"[1분봉 수집] 시작: {args.days}일, {args.coins}코인, 코인당 ~{total_count}개")

    markets = get_top_markets(args.coins)
    if not markets:
        tg("[오류] 종목 조회 실패")
        return

    coins = [m.split("-")[1] for m in markets]
    tg(f"종목({len(coins)}): {', '.join(coins)}")

    t0 = time.time()
    collected = 0
    skipped = 0
    failed = 0

    for i, (market, coin) in enumerate(zip(markets, coins)):
        # 이미 수집된 코인 건너뛰기
        if args.resume and is_collected(coin, total_count // 2):
            skipped += 1
            print(f"  [{i+1}/{len(coins)}] {coin} 스킵 (이미 수집됨)")
            continue

        print(f"  [{i+1}/{len(coins)}] {coin} 수집 시작...")
        try:
            candles = fetch_1m_candles(market, total_count,
                                       max_time=args.max_time_per_coin)
            if candles and len(candles) >= 100:
                fpath = save_coin(coin, candles)
                tg(f"  [{i+1}/{len(coins)}] {coin}: {len(candles)}개 저장")
                collected += 1
            else:
                tg(f"  [{i+1}/{len(coins)}] {coin}: 데이터 부족 ({len(candles) if candles else 0}개)")
                failed += 1

            del candles
            gc.collect()

        except Exception as e:
            tg(f"  [{i+1}/{len(coins)}] {coin} 에러: {e}")
            failed += 1

        # 코인 간 대기 (API 부하 분산)
        if i < len(coins) - 1:
            time.sleep(1)

        # 5코인마다 진행 상황 보고
        if (i + 1) % 5 == 0:
            elapsed = time.time() - t0
            remaining = len(coins) - i - 1
            eta = elapsed / (i + 1) * remaining if i > 0 else 0
            tg(f"  진행: {i+1}/{len(coins)} (수집:{collected} 스킵:{skipped} 실패:{failed}) "
               f"{elapsed/60:.1f}분 경과, ~{eta/60:.1f}분 남음")

    elapsed = time.time() - t0
    tg(f"\n[1분봉 수집 완료] {elapsed/60:.1f}분 소요")
    tg(f"  수집: {collected}코인 | 스킵: {skipped}코인 | 실패: {failed}코인")

    # 수집 결과 요약
    if os.path.exists(DATA_DIR):
        files_1m = [f for f in os.listdir(DATA_DIR) if f.endswith("_1m.json")]
        total_candles = 0
        for f in files_1m:
            try:
                with open(os.path.join(DATA_DIR, f)) as fp:
                    total_candles += json.load(fp).get("count", 0)
            except:
                pass
        tg(f"  1분봉 파일: {len(files_1m)}개 | 총 캔들: {total_candles:,}개")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _release_lock()
        tg("\n[중단] 사용자 취소")
    except Exception as e:
        import traceback
        _release_lock()
        tg(f"[에러] {e}\n{traceback.format_exc()[-500:]}")
