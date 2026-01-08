# -*- coding: utf-8 -*-
"""
딥 분석 스크립트 v3
- 1분봉 + 5분봉 동시 분석
- RSI, MACD, 볼린저밴드, 스토캐스틱 등 기술적 지표
- 성공/실패 케이스 상세 비교
"""
import requests
import time
from datetime import datetime, timedelta
import statistics
import math

# 분석할 케이스들 (종목, 날짜, 시간, 성공여부)
CASES = [
    # === 성공 케이스 ===
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
    # === 1/8 성공 케이스 ===
    ("STRAX", "2026-01-08", "09:00", True),
    ("BREV", "2026-01-08", "09:06", True),
    # === 실패 케이스 ===
    ("ETH", "2026-01-07", "11:05", False),
    ("SUI", "2026-01-07", "11:05", False),
    ("SUI", "2026-01-07", "10:46", False),
    ("ADA", "2026-01-07", "10:25", False),
    ("GAS", "2026-01-07", "10:21", False),
    ("GAS", "2026-01-07", "09:46", False),
    ("SUI", "2026-01-07", "09:30", False),
    ("ETH", "2026-01-07", "21:05", False),
    ("BTC", "2026-01-07", "20:43", False),
    # === 1/8 실패 케이스 (21건) ===
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
]

def get_candles(market, to_time, count=50, unit=1):
    """캔들 조회 (unit: 1=1분봉, 5=5분봉)"""
    url = f"https://api.upbit.com/v1/candles/minutes/{unit}"
    params = {"market": f"KRW-{market}", "to": to_time, "count": count}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return []
    except:
        return []

def calc_ema(prices, period):
    """EMA 계산"""
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calc_sma(prices, period):
    """SMA 계산"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def calc_rsi(prices, period=14):
    """RSI 계산"""
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    if len(gains) < period:
        return None

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_macd(prices, fast=12, slow=26, signal=9):
    """MACD 계산"""
    if len(prices) < slow:
        return None, None, None

    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)

    if ema_fast is None or ema_slow is None:
        return None, None, None

    macd_line = ema_fast - ema_slow
    # 간단히 현재 MACD만 반환
    return macd_line, None, None

def calc_bollinger(prices, period=20, std_dev=2):
    """볼린저밴드 계산"""
    if len(prices) < period:
        return None, None, None

    sma = sum(prices[-period:]) / period
    variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
    std = math.sqrt(variance)

    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)

    return upper, sma, lower

def calc_stochastic(highs, lows, closes, k_period=14, d_period=3):
    """스토캐스틱 계산"""
    if len(closes) < k_period:
        return None, None

    highest_high = max(highs[-k_period:])
    lowest_low = min(lows[-k_period:])

    if highest_high == lowest_low:
        return 50, 50

    k = ((closes[-1] - lowest_low) / (highest_high - lowest_low)) * 100
    return k, None  # D는 K의 이동평균이라 간단히 K만 반환

def calc_atr(highs, lows, closes, period=14):
    """ATR(Average True Range) 계산"""
    if len(closes) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    return sum(true_ranges[-period:]) / period

def calc_obv_trend(closes, volumes, period=10):
    """OBV 추세 계산"""
    if len(closes) < period + 1:
        return None

    obv = 0
    obv_values = []
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv += volumes[i]
        elif closes[i] < closes[i-1]:
            obv -= volumes[i]
        obv_values.append(obv)

    if len(obv_values) < period:
        return None

    # OBV 기울기 (추세)
    recent_obv = obv_values[-period:]
    if recent_obv[-1] > recent_obv[0]:
        return (recent_obv[-1] - recent_obv[0]) / abs(recent_obv[0]) if recent_obv[0] != 0 else 1
    return (recent_obv[-1] - recent_obv[0]) / abs(recent_obv[0]) if recent_obv[0] != 0 else -1

def analyze_deep(ticker, date_str, time_str):
    """딥 분석"""
    dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    to_time = (dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S") + "+09:00"

    # 1분봉 50개
    candles_1m = get_candles(ticker, to_time, 50, unit=1)
    time.sleep(0.1)

    # 5분봉 30개
    candles_5m = get_candles(ticker, to_time, 30, unit=5)

    if not candles_1m or len(candles_1m) < 35:
        return None

    candles_1m = list(reversed(candles_1m))
    if candles_5m:
        candles_5m = list(reversed(candles_5m))

    # 진입 시점 찾기
    target_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    entry_idx = None
    min_diff = 999999
    for i, c in enumerate(candles_1m):
        c_time_str = c["candle_date_time_kst"][:19]
        c_dt = datetime.fromisoformat(c_time_str)
        diff = abs((c_dt - target_dt).total_seconds())
        if diff < min_diff:
            min_diff = diff
            entry_idx = i

    if entry_idx is None or entry_idx < 30 or min_diff > 120:
        return None

    # 데이터 추출
    pre_candles = candles_1m[entry_idx-30:entry_idx]
    entry_candle = candles_1m[entry_idx]

    closes = [c["trade_price"] for c in pre_candles]
    highs = [c["high_price"] for c in pre_candles]
    lows = [c["low_price"] for c in pre_candles]
    volumes = [c["candle_acc_trade_volume"] for c in pre_candles]

    result = {"ticker": ticker, "time": f"{date_str} {time_str}"}

    # ==========================================
    # 1분봉 기본 지표
    # ==========================================

    # 가격 관련
    entry_price = entry_candle["trade_price"]
    result["entry_price"] = entry_price

    # 5봉 범위
    last5_highs = highs[-5:]
    last5_lows = lows[-5:]
    result["range_5"] = (max(last5_highs) - min(last5_lows)) / min(last5_lows) * 100

    # 10봉 범위
    last10_highs = highs[-10:]
    last10_lows = lows[-10:]
    result["range_10"] = (max(last10_highs) - min(last10_lows)) / min(last10_lows) * 100

    # 30봉 고저 대비 위치
    high_30 = max(highs)
    low_30 = min(lows)
    result["pos_30"] = (entry_price - low_30) / (high_30 - low_30) * 100 if high_30 > low_30 else 50

    # 저점/고점 상승 횟수
    higher_lows = sum(1 for i in range(1, 5) if lows[-5+i] >= lows[-5+i-1])
    higher_highs = sum(1 for i in range(1, 5) if highs[-5+i] >= highs[-5+i-1])
    result["higher_lows"] = higher_lows
    result["higher_highs"] = higher_highs

    # 양봉/음봉 비율 (최근 5봉)
    bullish_5 = sum(1 for c in pre_candles[-5:] if c["trade_price"] > c["opening_price"])
    result["bullish_ratio_5"] = bullish_5 / 5 * 100

    # 진입봉 특성
    result["entry_bullish"] = entry_candle["trade_price"] > entry_candle["opening_price"]
    entry_body = abs(entry_candle["trade_price"] - entry_candle["opening_price"]) / entry_candle["opening_price"] * 100
    result["entry_body_pct"] = entry_body

    # 거래량
    avg_vol = sum(volumes) / len(volumes) if volumes else 1
    entry_vol = entry_candle["candle_acc_trade_volume"]
    result["vol_ratio"] = entry_vol / avg_vol if avg_vol > 0 else 0

    # 최근 5봉 거래량 vs 이전 25봉
    recent_vol = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
    prev_vol = sum(volumes[:-5]) / 25 if len(volumes) >= 30 else 0
    result["vol_trend"] = recent_vol / prev_vol if prev_vol > 0 else 1

    # ==========================================
    # 기술적 지표 (1분봉)
    # ==========================================

    # RSI
    rsi = calc_rsi(closes, 14)
    result["rsi_14"] = rsi if rsi else 50

    # RSI 6 (단기)
    rsi_6 = calc_rsi(closes, 6)
    result["rsi_6"] = rsi_6 if rsi_6 else 50

    # MACD
    macd, _, _ = calc_macd(closes)
    result["macd"] = macd if macd else 0

    # 볼린저밴드
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes, 20)
    if bb_upper and bb_lower:
        bb_width = (bb_upper - bb_lower) / bb_mid * 100 if bb_mid else 0
        bb_pos = (entry_price - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper > bb_lower else 50
        result["bb_width"] = bb_width
        result["bb_pos"] = bb_pos  # 0=하단, 100=상단
    else:
        result["bb_width"] = 0
        result["bb_pos"] = 50

    # 스토캐스틱
    stoch_k, _ = calc_stochastic(highs, lows, closes)
    result["stoch_k"] = stoch_k if stoch_k else 50

    # ATR (변동성)
    atr = calc_atr(highs, lows, closes)
    result["atr"] = atr / entry_price * 100 if atr and entry_price > 0 else 0

    # OBV 추세
    obv_trend = calc_obv_trend(closes, volumes)
    result["obv_trend"] = obv_trend if obv_trend else 0

    # EMA 관계
    ema_5 = calc_ema(closes, 5)
    ema_10 = calc_ema(closes, 10)
    ema_20 = calc_ema(closes, 20)

    if ema_5 and ema_10 and ema_20:
        result["ema_5_10"] = (ema_5 / ema_10 - 1) * 100  # EMA5 vs EMA10
        result["ema_10_20"] = (ema_10 / ema_20 - 1) * 100  # EMA10 vs EMA20
        result["price_vs_ema20"] = (entry_price / ema_20 - 1) * 100
    else:
        result["ema_5_10"] = 0
        result["ema_10_20"] = 0
        result["price_vs_ema20"] = 0

    # ==========================================
    # 5분봉 지표
    # ==========================================
    if candles_5m and len(candles_5m) >= 10:
        closes_5m = [c["trade_price"] for c in candles_5m]
        highs_5m = [c["high_price"] for c in candles_5m]
        lows_5m = [c["low_price"] for c in candles_5m]

        # 5분봉 RSI
        rsi_5m = calc_rsi(closes_5m, 14)
        result["rsi_5m"] = rsi_5m if rsi_5m else 50

        # 5분봉 추세 (최근 5개 vs 이전 5개)
        if len(closes_5m) >= 10:
            recent_avg = sum(closes_5m[-5:]) / 5
            prev_avg = sum(closes_5m[-10:-5]) / 5
            result["trend_5m"] = (recent_avg / prev_avg - 1) * 100 if prev_avg > 0 else 0
        else:
            result["trend_5m"] = 0

        # 5분봉 볼린저밴드
        bb_upper_5m, bb_mid_5m, bb_lower_5m = calc_bollinger(closes_5m, 20)
        if bb_upper_5m and bb_lower_5m:
            result["bb_pos_5m"] = (entry_price - bb_lower_5m) / (bb_upper_5m - bb_lower_5m) * 100
        else:
            result["bb_pos_5m"] = 50
    else:
        result["rsi_5m"] = 50
        result["trend_5m"] = 0
        result["bb_pos_5m"] = 50

    # ==========================================
    # 시간대 분석
    # ==========================================
    hour = int(time_str.split(":")[0])
    result["hour"] = hour
    result["is_morning"] = 8 <= hour <= 10  # 장 초반
    result["is_afternoon"] = 13 <= hour <= 16  # 오후
    result["is_night"] = hour >= 20 or hour <= 6  # 밤

    return result

def main():
    print("=" * 80)
    print("🔬 딥 분석 v3 - 성공/실패 완전 비교")
    print("=" * 80)

    success_results = []
    fail_results = []

    for ticker, date_str, time_str, is_success in CASES:
        label = "✅" if is_success else "❌"
        print(f"분석 중: {label} {ticker} @ {date_str} {time_str}...", end=" ")
        result = analyze_deep(ticker, date_str, time_str)
        if result:
            result["is_success"] = is_success
            if is_success:
                success_results.append(result)
            else:
                fail_results.append(result)
            print("✓")
        else:
            print("✗")
        time.sleep(0.2)

    if not success_results or not fail_results:
        print("\n성공/실패 케이스 모두 필요합니다.")
        return

    # ==========================================
    # 성공 vs 실패 비교
    # ==========================================
    print("\n")
    print("=" * 80)
    print("⚖️ 성공 vs 실패 완전 비교")
    print("=" * 80)

    all_metrics = [
        ("range_5", "5봉 범위(%)"),
        ("range_10", "10봉 범위(%)"),
        ("pos_30", "30봉 내 위치(%)"),
        ("higher_lows", "저점상승 횟수"),
        ("higher_highs", "고점상승 횟수"),
        ("bullish_ratio_5", "양봉비율(%)"),
        ("entry_body_pct", "진입봉 몸통(%)"),
        ("vol_ratio", "진입봉 거래량배수"),
        ("vol_trend", "거래량 추세"),
        ("rsi_14", "RSI(14)"),
        ("rsi_6", "RSI(6)"),
        ("macd", "MACD"),
        ("bb_width", "BB폭(%)"),
        ("bb_pos", "BB위치(%)"),
        ("stoch_k", "스토캐스틱K"),
        ("atr", "ATR(%)"),
        ("obv_trend", "OBV추세"),
        ("ema_5_10", "EMA5/10(%)"),
        ("ema_10_20", "EMA10/20(%)"),
        ("price_vs_ema20", "가격/EMA20(%)"),
        ("rsi_5m", "RSI(5분봉)"),
        ("trend_5m", "5분봉추세(%)"),
        ("bb_pos_5m", "BB위치(5분봉)"),
    ]

    print(f"\n{'지표':<20} | {'성공 평균':>12} | {'실패 평균':>12} | {'차이':>10} | {'판별력':>8}")
    print("-" * 75)

    discriminators = []  # 판별력 있는 지표 저장

    for key, label in all_metrics:
        s_vals = [r[key] for r in success_results if key in r and r[key] is not None]
        f_vals = [r[key] for r in fail_results if key in r and r[key] is not None]

        if s_vals and f_vals:
            s_avg = statistics.mean(s_vals)
            f_avg = statistics.mean(f_vals)
            diff = s_avg - f_avg

            # 판별력 계산 (차이 / 표준편차)
            try:
                all_vals = s_vals + f_vals
                std = statistics.stdev(all_vals) if len(all_vals) > 1 else 1
                discriminant = abs(diff) / std if std > 0 else 0
            except:
                discriminant = 0

            diff_str = f"+{diff:.2f}" if diff > 0 else f"{diff:.2f}"
            disc_str = f"{discriminant:.2f}"

            # 판별력 0.5 이상이면 ★ 표시
            star = "★" if discriminant >= 0.5 else ""
            print(f"{label:<20} | {s_avg:>12.2f} | {f_avg:>12.2f} | {diff_str:>10} | {disc_str:>6} {star}")

            if discriminant >= 0.5:
                discriminators.append((label, s_avg, f_avg, diff, discriminant))

    # ==========================================
    # 핵심 판별 지표
    # ==========================================
    print("\n")
    print("=" * 80)
    print("🎯 핵심 판별 지표 (판별력 0.5 이상)")
    print("=" * 80)

    discriminators.sort(key=lambda x: x[4], reverse=True)

    for label, s_avg, f_avg, diff, disc in discriminators:
        direction = "성공이 높음" if diff > 0 else "실패가 높음"
        print(f"  ★ {label}: 성공 {s_avg:.2f} vs 실패 {f_avg:.2f} ({direction}, 판별력 {disc:.2f})")

    # ==========================================
    # 시간대별 분석
    # ==========================================
    print("\n")
    print("=" * 80)
    print("🕐 시간대별 성공률")
    print("=" * 80)

    # 아침 (8-10시)
    s_morning = sum(1 for r in success_results if r.get("is_morning"))
    f_morning = sum(1 for r in fail_results if r.get("is_morning"))

    # 오후 (13-16시)
    s_afternoon = sum(1 for r in success_results if r.get("is_afternoon"))
    f_afternoon = sum(1 for r in fail_results if r.get("is_afternoon"))

    # 밤 (20시-6시)
    s_night = sum(1 for r in success_results if r.get("is_night"))
    f_night = sum(1 for r in fail_results if r.get("is_night"))

    print(f"  아침(8-10시): 성공 {s_morning} / 실패 {f_morning}")
    print(f"  오후(13-16시): 성공 {s_afternoon} / 실패 {f_afternoon}")
    print(f"  밤(20-06시): 성공 {s_night} / 실패 {f_night}")

    # ==========================================
    # 권장 임계치
    # ==========================================
    print("\n")
    print("=" * 80)
    print("💡 권장 진입 조건")
    print("=" * 80)

    if discriminators:
        print("\n핵심 판별 지표 기반 조건:")
        for label, s_avg, f_avg, diff, disc in discriminators[:5]:  # 상위 5개
            # 성공과 실패의 중간값을 임계치로 제안
            threshold = (s_avg + f_avg) / 2
            if diff > 0:
                print(f"  - {label} >= {threshold:.2f} (성공 평균 {s_avg:.2f})")
            else:
                print(f"  - {label} <= {threshold:.2f} (성공 평균 {s_avg:.2f})")

if __name__ == "__main__":
    main()
