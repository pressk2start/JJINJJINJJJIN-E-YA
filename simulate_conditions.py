# -*- coding: utf-8 -*-
"""
조건 시뮬레이션 - 다양한 임계치 조합의 성공/실패 통과율 계산
"""
import requests
import time
from datetime import datetime, timedelta
import math

# 분석할 케이스들
CASES = [
    # === 성공 케이스 (30건) ===
    ("TOSHI", "2026-01-06", "10:09", True),
    ("BORA", "2026-01-06", "09:05", True),
    ("PLUME", "2026-01-06", "10:29", True),
    ("QTUM", "2026-01-06", "09:02", True),
    ("DOOD", "2026-01-06", "10:11", True),
    ("SUI", "2026-01-06", "09:00", True),
    ("ONT", "2026-01-06", "09:03", True),
    ("VIRTUAL", "2026-01-05", "10:24", True),
    ("BSV", "2026-01-05", "09:51", True),
    ("PEPE", "2026-01-04", "17:08", True),
    ("BTT", "2026-01-06", "09:01", True),
    ("SHIB", "2026-01-06", "01:10", True),
    ("STORJ", "2026-01-05", "21:32", True),
    ("XRP", "2026-01-05", "23:29", True),
    ("BTC", "2026-01-05", "08:59", True),
    ("ETH", "2026-01-05", "08:59", True),
    ("VIRTUAL", "2026-01-03", "12:30", True),
    ("ORCA", "2026-01-05", "09:01", True),
    ("GRS", "2026-01-03", "14:42", True),
    ("MMT", "2026-01-05", "19:52", True),
    ("BOUNTY", "2026-01-07", "09:06", True),
    ("MOC", "2026-01-07", "09:08", True),
    ("FCT2", "2026-01-07", "09:07", True),
    ("BOUNTY", "2026-01-07", "16:23", True),
    ("ZKP", "2026-01-07", "19:39", True),
    ("STRAX", "2026-01-08", "09:00", True),
    ("BREV", "2026-01-08", "09:06", True),
    ("ELF", "2026-01-08", "10:35", True),
    ("MED", "2026-01-08", "11:21", True),
    ("VIRTUAL", "2026-01-08", "17:18", True),
    # === 실패 케이스 (41건) ===
    ("ETH", "2026-01-07", "11:05", False),
    ("SUI", "2026-01-07", "11:05", False),
    ("SUI", "2026-01-07", "10:46", False),
    ("ADA", "2026-01-07", "10:25", False),
    ("GAS", "2026-01-07", "10:21", False),
    ("GAS", "2026-01-07", "09:46", False),
    ("SUI", "2026-01-07", "09:30", False),
    ("ETH", "2026-01-07", "21:05", False),
    ("BTC", "2026-01-07", "20:43", False),
    ("KAITO", "2026-01-08", "07:34", False),
    ("SOL", "2026-01-08", "07:32", False),
    ("ETH", "2026-01-08", "07:34", False),
    ("SUI", "2026-01-08", "07:32", False),
    ("BREV", "2026-01-08", "07:20", False),
    ("BREV", "2026-01-08", "06:48", False),
    ("ETH", "2026-01-08", "06:31", False),
    ("BREV", "2026-01-08", "06:02", False),
    ("BREV", "2026-01-08", "05:52", False),
    ("BREV", "2026-01-08", "05:43", False),
    ("BOUNTY", "2026-01-08", "05:42", False),
    ("BOUNTY", "2026-01-08", "05:38", False),
    ("BREV", "2026-01-08", "05:38", False),
    ("ETH", "2026-01-08", "05:37", False),
    ("XRP", "2026-01-08", "05:01", False),
    ("XRP", "2026-01-08", "03:20", False),
    ("BREV", "2026-01-08", "02:08", False),
    ("XRP", "2026-01-08", "01:32", False),
    ("BREV", "2026-01-08", "01:27", False),
    ("PEPE", "2026-01-08", "01:18", False),
    ("CVC", "2026-01-08", "00:35", False),
    ("IP", "2026-01-08", "17:45", False),
    ("VIRTUAL", "2026-01-08", "17:43", False),
    ("VIRTUAL", "2026-01-08", "17:41", False),
    ("VIRTUAL", "2026-01-08", "17:39", False),
    ("SUI", "2026-01-08", "17:36", False),
    ("BTC", "2026-01-08", "17:34", False),
    ("IP", "2026-01-08", "17:32", False),
    ("SUI", "2026-01-08", "17:27", False),
    ("VIRTUAL", "2026-01-08", "17:25", False),
    ("ONDO", "2026-01-08", "17:18", False),
    ("SOL", "2026-01-08", "17:16", False),
]

def get_candles(market, to_time, count=50, unit=1):
    url = f"https://api.upbit.com/v1/candles/minutes/{unit}"
    params = {"market": f"KRW-{market}", "to": to_time, "count": count}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return []
    except:
        return []

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(diff if diff > 0 else 0)
        losses.append(-diff if diff < 0 else 0)
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_ema(prices, period):
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def analyze_case(ticker, date_str, time_str):
    """케이스의 지표 값 계산"""
    dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    to_time = (dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S") + "+09:00"

    # 5분봉 조회
    candles_5m = get_candles(ticker, to_time, 25, unit=5)
    time.sleep(0.12)

    if not candles_5m or len(candles_5m) < 20:
        return None

    candles_5m = list(reversed(candles_5m))
    closes_5m = [c["trade_price"] for c in candles_5m[-20:]]

    result = {}

    # RSI(5분봉)
    rsi_5m = calc_rsi(closes_5m, 14)
    result["rsi_5m"] = rsi_5m if rsi_5m else 50

    # 5분봉추세
    if len(closes_5m) >= 10:
        recent_avg = sum(closes_5m[-5:]) / 5
        prev_avg = sum(closes_5m[-10:-5]) / 5
        result["trend_5m"] = (recent_avg / prev_avg - 1) * 100 if prev_avg > 0 else 0
    else:
        result["trend_5m"] = 0

    # 가격/EMA20
    ema20 = calc_ema(closes_5m, 20) if len(closes_5m) >= 20 else None
    if ema20:
        result["price_vs_ema20"] = (closes_5m[-1] / ema20 - 1) * 100
    else:
        result["price_vs_ema20"] = 0

    # EMA10/20
    ema10 = calc_ema(closes_5m, 10) if len(closes_5m) >= 10 else None
    if ema10 and ema20:
        result["ema10_20"] = (ema10 / ema20 - 1) * 100
    else:
        result["ema10_20"] = 0

    return result

def simulate_conditions(data, rsi_min, trend_min, secondary_type=None, secondary_val=None):
    """조건 시뮬레이션"""
    success_pass = 0
    success_total = 0
    fail_pass = 0
    fail_total = 0

    for item in data:
        if item is None:
            continue

        is_success = item["is_success"]

        # 필수 조건
        rsi_ok = item["rsi_5m"] >= rsi_min
        trend_ok = item["trend_5m"] >= trend_min

        # 보조 조건
        if secondary_type == "ema20":
            secondary_ok = item["price_vs_ema20"] >= secondary_val
        elif secondary_type == "ema10_20":
            secondary_ok = item["ema10_20"] >= secondary_val
        else:
            secondary_ok = True  # 보조 조건 없음

        passed = rsi_ok and trend_ok and secondary_ok

        if is_success:
            success_total += 1
            if passed:
                success_pass += 1
        else:
            fail_total += 1
            if passed:
                fail_pass += 1

    return {
        "success_pass": success_pass,
        "success_total": success_total,
        "success_rate": success_pass / success_total * 100 if success_total > 0 else 0,
        "fail_pass": fail_pass,
        "fail_total": fail_total,
        "fail_rate": fail_pass / fail_total * 100 if fail_total > 0 else 0,
    }

def main():
    print("=" * 80)
    print("조건 시뮬레이션 - 성공/실패 통과율 계산")
    print("=" * 80)

    # 데이터 수집
    print("\n데이터 수집 중...")
    data = []
    for ticker, date_str, time_str, is_success in CASES:
        result = analyze_case(ticker, date_str, time_str)
        if result:
            result["is_success"] = is_success
            data.append(result)
            tag = "성공" if is_success else "실패"
            print(f"  [{tag}] {ticker} {time_str}: RSI={result['rsi_5m']:.0f} 추세={result['trend_5m']:.2f}%")
        else:
            print(f"  [에러] {ticker} {time_str}: 데이터 없음")

    print(f"\n수집 완료: {len(data)}건")

    # 다양한 조건 조합 시뮬레이션
    print("\n" + "=" * 80)
    print("📊 조건별 시뮬레이션 결과")
    print("=" * 80)

    conditions = [
        # (RSI, 추세, 보조타입, 보조값, 설명)
        (52, 0.1, None, None, "현재 조건"),
        (60, 0.5, None, None, "약간 강화"),
        (65, 0.8, None, None, "제안 v1"),
        (68, 1.0, None, None, "제안 v2 (엄격)"),
        (65, 0.8, "ema20", 0.5, "v1 + EMA20>=0.5%"),
        (60, 0.5, "ema20", 0.3, "완화 + EMA20>=0.3%"),
    ]

    print(f"\n{'조건':<25} | {'성공 통과':>12} | {'실패 통과':>12} | {'순효과':>8}")
    print("-" * 70)

    for rsi_min, trend_min, sec_type, sec_val, desc in conditions:
        result = simulate_conditions(data, rsi_min, trend_min, sec_type, sec_val)

        s_rate = result["success_rate"]
        f_rate = result["fail_rate"]
        net = s_rate - f_rate  # 성공통과율 - 실패통과율 (높을수록 좋음)

        s_str = f"{result['success_pass']}/{result['success_total']} ({s_rate:.0f}%)"
        f_str = f"{result['fail_pass']}/{result['fail_total']} ({f_rate:.0f}%)"

        print(f"{desc:<25} | {s_str:>12} | {f_str:>12} | {net:>+6.0f}%")

    print("\n" + "=" * 80)
    print("💡 해석: 성공 통과율은 높고, 실패 통과율은 낮을수록 좋음")
    print("   순효과 = 성공통과율 - 실패통과율 (클수록 좋은 조건)")
    print("=" * 80)

if __name__ == "__main__":
    main()
