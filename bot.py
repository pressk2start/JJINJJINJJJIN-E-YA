# -*- coding: utf-8 -*-
"""
업비트 장기 데이터 수집기 v1.0
================================
- 코인별로 수집 → 즉시 파일 저장 → 메모리 해제
- 5분봉/15분봉 30일치 (1분봉은 옵션)
- 중단 후 재개 가능 (이미 저장된 코인 스킵)
- 저장 위치: ./candle_data/ 폴더

사용법:
  python upbit_collector.py              # 5분봉+15분봉 30일
  python upbit_collector.py --days 60    # 60일치
  python upbit_collector.py --include-1m # 1분봉도 포함
"""
import requests, time, json, os, sys, argparse
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
BASE = "https://api.upbit.com/v1"
SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "candle_data")

# ================================================================
# API 호출
# ================================================================
def safe_get(url, params=None, retries=4, timeout=10):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 2 + i * 2
                print(f"  ⏳ 429 rate limit, {wait}s 대기...")
                time.sleep(wait)
                continue
            print(f"  ⚠️ HTTP {r.status_code}, 재시도 {i+1}/{retries}")
            time.sleep(0.5 + i)
        except Exception as e:
            print(f"  ⚠️ 요청 실패: {e}, 재시도 {i+1}/{retries}")
            time.sleep(1 + i)
    return None

# ================================================================
# 거래량 상위 종목
# ================================================================
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
        time.sleep(0.15)

    all_t.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
    return [t["market"] for t in all_t[:n]]

# ================================================================
# 캔들 수집 (페이징)
# ================================================================
def fetch_candles(unit, market, total_count):
    """
    unit: 1, 5, 15, 60, 240
    한 번에 200개씩 과거로 페이징
    반환: 시간순 정렬 (과거→현재)
    """
    result = []
    to = None
    pages = (total_count + 199) // 200

    for page in range(pages):
        params = {"market": market, "count": min(200, total_count - len(result))}
        if to:
            params["to"] = to

        data = safe_get(f"{BASE}/candles/minutes/{unit}", params)
        if not data:
            print(f"    ⚠️ 페이지 {page+1} 실패, 현재까지 {len(result)}개")
            break

        result.extend(data)
        if len(data) < 200:
            break

        # 다음 페이지: 마지막 캔들의 시간 이전으로
        to = data[-1]["candle_date_time_utc"] + "Z"

        # 진행 표시
        if (page + 1) % 10 == 0:
            print(f"    📥 {len(result)}/{total_count} ({len(result)*100//total_count}%)")

        time.sleep(0.12)  # rate limit 방지

    # 시간순 정렬 (과거→현재)
    result.sort(key=lambda x: x["candle_date_time_kst"])
    return result

# ================================================================
# 캔들 → 경량 포맷으로 변환 (저장 용량 절감)
# ================================================================
def compress_candle(c):
    """원본 ~300바이트 → ~80바이트"""
    return {
        "t": c["candle_date_time_kst"],
        "o": c["opening_price"],
        "h": c["high_price"],
        "l": c["low_price"],
        "c": c["trade_price"],
        "v": round(c.get("candle_acc_trade_volume", 0), 6),
        "vk": round(c.get("candle_acc_trade_price", 0))  # KRW 거래대금
    }

# ================================================================
# 저장/로드
# ================================================================
def save_coin_data(coin, unit, candles):
    os.makedirs(SAVE_DIR, exist_ok=True)
    fname = f"{coin}_{unit}m.json"
    fpath = os.path.join(SAVE_DIR, fname)

    compressed = [compress_candle(c) for c in candles]

    with open(fpath, "w", encoding="utf-8") as f:
        json.dump({
            "coin": coin,
            "unit": unit,
            "count": len(compressed),
            "from": compressed[0]["t"] if compressed else "",
            "to": compressed[-1]["t"] if compressed else "",
            "collected_at": datetime.now(KST).isoformat(),
            "candles": compressed
        }, f, ensure_ascii=False)

    size_kb = os.path.getsize(fpath) / 1024
    print(f"    💾 {fname}: {len(compressed)}개, {size_kb:.0f}KB")
    return fpath

def already_collected(coin, unit):
    fpath = os.path.join(SAVE_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath):
        return False
    try:
        with open(fpath, "r") as f:
            data = json.load(f)
        return data.get("count", 0) > 100  # 100개 이상이면 유효
    except:
        return False

# ================================================================
# 메인 수집 루프
# ================================================================
def collect_all(days=30, top_n=30, include_1m=False):
    print(f"🔍 업비트 데이터 수집 시작")
    print(f"   기간: {days}일, 종목: 상위 {top_n}개")
    print(f"   타임프레임: 5분, 15분" + (", 1분" if include_1m else ""))
    print(f"   저장 위치: {SAVE_DIR}")
    print()

    # 필요 캔들 수 계산
    candles_per_day = {"1": 1440, "5": 288, "15": 96}
    timeframes = ["5", "15"]
    if include_1m:
        timeframes.insert(0, "1")

    for tf in timeframes:
        total = candles_per_day[tf] * days
        print(f"   {tf}분봉: {total}캔들/코인 × {top_n}코인 = {total*top_n:,}개")
    print()

    # 종목 가져오기
    markets = get_top_markets(top_n)
    if not markets:
        print("❌ 종목 가져오기 실패!")
        return
    print(f"📋 종목: {', '.join(m.split('-')[1] for m in markets)}\n")

    t0 = time.time()
    total_saved = 0

    for i, market in enumerate(markets):
        coin = market.split("-")[1]
        print(f"[{i+1}/{top_n}] {coin}")

        for tf in timeframes:
            tf_int = int(tf)
            total_candles = candles_per_day[tf] * days

            # 이미 수집된 건 스킵
            if already_collected(coin, tf_int):
                print(f"    ✅ {tf}분봉 이미 수집됨, 스킵")
                continue

            print(f"    📥 {tf}분봉 {total_candles}개 수집 중...")
            candles = fetch_candles(tf_int, market, total_candles)

            if candles:
                save_coin_data(coin, tf_int, candles)
                total_saved += len(candles)
                # 메모리 해제
                del candles
            else:
                print(f"    ❌ {tf}분봉 수집 실패")

            time.sleep(0.3)  # 코인 간 쿨다운

        elapsed = time.time() - t0
        remaining = elapsed / (i + 1) * (top_n - i - 1)
        print(f"    ⏱️ 경과: {elapsed/60:.1f}분, 예상 잔여: {remaining/60:.1f}분\n")

    elapsed = time.time() - t0
    print(f"\n✅ 수집 완료!")
    print(f"   총 캔들: {total_saved:,}개")
    print(f"   소요 시간: {elapsed/60:.1f}분")
    print(f"   저장 위치: {SAVE_DIR}")

    # 저장된 파일 목록
    files = sorted(os.listdir(SAVE_DIR))
    total_size = sum(os.path.getsize(os.path.join(SAVE_DIR, f)) for f in files)
    print(f"   총 파일: {len(files)}개, {total_size/1024/1024:.1f}MB")

# ================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="업비트 캔들 데이터 수집기")
    parser.add_argument("--days", type=int, default=30, help="수집 기간 (일)")
    parser.add_argument("--coins", type=int, default=30, help="상위 N개 코인")
    parser.add_argument("--include-1m", action="store_true", help="1분봉도 수집")
    args = parser.parse_args()

    collect_all(days=args.days, top_n=args.coins, include_1m=args.include_1m)
