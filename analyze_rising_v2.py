# -*- coding: utf-8 -*-
"""
상승 차트 분석 스크립트 v2
- 상승 시작 전 30개 캔들 패턴 분석
- 진입 신호가 될 수 있는 선행 지표 탐색
"""
import requests
import time
from datetime import datetime, timedelta
import statistics

# 분석할 케이스들 (종목, 날짜, 시간)
CASES = [
    ("TOSHI", "2026-01-06", "10:09"),
    ("BORA", "2026-01-06", "09:05"),
    ("PLUME", "2026-01-06", "10:29"),
    ("QTUM", "2026-01-06", "09:02"),
    ("DOOD", "2026-01-06", "10:11"),
    ("SUI", "2026-01-06", "09:00"),
    ("ONT", "2026-01-06", "09:03"),
    ("VIRTUAL", "2026-01-05", "10:24"),
    ("BSV", "2026-01-05", "09:51"),
    ("PEPE", "2026-01-04", "17:08"),
    ("BTT", "2026-01-06", "09:01"),
    ("SHIB", "2026-01-06", "01:10"),
    ("STORJ", "2026-01-05", "21:32"),
    ("XRP", "2026-01-05", "23:29"),
    ("BTC", "2026-01-05", "08:59"),
    ("ETH", "2026-01-05", "08:59"),
    ("VIRTUAL", "2026-01-03", "12:30"),
    ("ORCA", "2026-01-05", "09:01"),
    ("GRS", "2026-01-03", "14:42"),
    ("MMT", "2026-01-05", "19:52"),
]

def get_candles(market, to_time, count=50):
    """1분봉 캔들 조회"""
    url = "https://api.upbit.com/v1/candles/minutes/1"
    params = {"market": f"KRW-{market}", "to": to_time, "count": count}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return []
    except:
        return []

def calc_ema(prices, period):
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def analyze_pre_entry_pattern(ticker, date_str, time_str):
    """진입 전 30개 캔들 패턴 분석"""
    dt_str = f"{date_str}T{time_str}:00+09:00"
    dt = datetime.fromisoformat(dt_str)
    to_time = dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 진입 시점 포함 35개 캔들 (앞 30개 + 진입봉 + 뒤 4개)
    candles = get_candles(ticker, (dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S"), 40)
    if not candles or len(candles) < 35:
        return None

    candles = list(reversed(candles))  # 오래된 것부터

    # 진입 시점 찾기 (가장 가까운 시간)
    target_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    entry_idx = None
    min_diff = 999999
    for i, c in enumerate(candles):
        c_time_str = c["candle_date_time_kst"][:19]
        c_dt = datetime.fromisoformat(c_time_str)
        diff = abs((c_dt - target_dt).total_seconds())
        if diff < min_diff:
            min_diff = diff
            entry_idx = i

    if entry_idx is None or entry_idx < 30 or min_diff > 120:
        return None

    # 진입 전 30개 캔들
    pre_candles = candles[entry_idx-30:entry_idx]
    entry_candle = candles[entry_idx]
    post_candles = candles[entry_idx+1:entry_idx+5] if entry_idx+5 <= len(candles) else []

    result = {"ticker": ticker, "time": f"{date_str} {time_str}"}

    # ============================================
    # 1. 변동성 분석 (진입 전 30봉)
    # ============================================
    closes = [c["trade_price"] for c in pre_candles]
    highs = [c["high_price"] for c in pre_candles]
    lows = [c["low_price"] for c in pre_candles]
    volumes = [c["candle_acc_trade_volume"] for c in pre_candles]

    # 가격 변동폭 (%)
    ranges = [(h - l) / l * 100 for h, l in zip(highs, lows) if l > 0]
    result["avg_range_30"] = statistics.mean(ranges) if ranges else 0
    result["avg_range_last10"] = statistics.mean(ranges[-10:]) if len(ranges) >= 10 else 0
    result["avg_range_last5"] = statistics.mean(ranges[-5:]) if len(ranges) >= 5 else 0

    # 변동성 축소 여부 (최근 10봉 vs 이전 20봉)
    if len(ranges) >= 30:
        early_range = statistics.mean(ranges[:20])
        late_range = statistics.mean(ranges[-10:])
        result["volatility_squeeze"] = late_range / early_range if early_range > 0 else 1
    else:
        result["volatility_squeeze"] = 1

    # ============================================
    # 2. 거래량 패턴
    # ============================================
    result["avg_vol_30"] = statistics.mean(volumes)
    result["avg_vol_last10"] = statistics.mean(volumes[-10:]) if len(volumes) >= 10 else 0
    result["avg_vol_last5"] = statistics.mean(volumes[-5:]) if len(volumes) >= 5 else 0

    # 거래량 감소 후 증가 패턴
    if len(volumes) >= 30:
        early_vol = statistics.mean(volumes[:20])
        late_vol = statistics.mean(volumes[-10:])
        result["vol_trend_ratio"] = late_vol / early_vol if early_vol > 0 else 1
    else:
        result["vol_trend_ratio"] = 1

    # ============================================
    # 3. 가격 위치 분석
    # ============================================
    entry_price = entry_candle["trade_price"]

    # 30봉 고/저점 대비 위치
    high_30 = max(highs)
    low_30 = min(lows)
    price_position = (entry_price - low_30) / (high_30 - low_30) * 100 if high_30 > low_30 else 50
    result["price_position_30"] = price_position  # 0=저점, 100=고점

    # EMA20 대비
    if len(closes) >= 20:
        ema20 = calc_ema(closes, 20)
        result["vs_ema20"] = (entry_price / ema20 - 1) * 100 if ema20 else 0
    else:
        result["vs_ema20"] = 0

    # ============================================
    # 4. 캔들 패턴 분석 (직전 5봉)
    # ============================================
    last5 = pre_candles[-5:]

    # 양봉/음봉 비율
    bullish_count = sum(1 for c in last5 if c["trade_price"] > c["opening_price"])
    result["bullish_ratio_5"] = bullish_count / 5 * 100

    # 저점이 높아지는지 (Higher Lows)
    last5_lows = [c["low_price"] for c in last5]
    higher_lows = sum(1 for i in range(1, len(last5_lows)) if last5_lows[i] >= last5_lows[i-1])
    result["higher_lows_count"] = higher_lows

    # 고점이 높아지는지 (Higher Highs)
    last5_highs = [c["high_price"] for c in last5]
    higher_highs = sum(1 for i in range(1, len(last5_highs)) if last5_highs[i] >= last5_highs[i-1])
    result["higher_highs_count"] = higher_highs

    # ============================================
    # 5. 횡보/수렴 패턴
    # ============================================
    # 최근 10봉 가격 범위
    last10_highs = [c["high_price"] for c in pre_candles[-10:]]
    last10_lows = [c["low_price"] for c in pre_candles[-10:]]
    last10_range = (max(last10_highs) - min(last10_lows)) / min(last10_lows) * 100
    result["consolidation_range_10"] = last10_range

    # 최근 5봉 가격 범위
    last5_range = (max(last5_highs) - min(last5_lows)) / min(last5_lows) * 100
    result["consolidation_range_5"] = last5_range

    # 수렴 패턴 (범위 축소)
    result["range_squeeze"] = last5_range / last10_range if last10_range > 0 else 1

    # ============================================
    # 6. 진입봉 특성
    # ============================================
    entry_body = (entry_candle["trade_price"] - entry_candle["opening_price"]) / entry_candle["opening_price"] * 100
    result["entry_body_pct"] = entry_body
    result["entry_is_bullish"] = entry_body > 0

    # 진입봉 거래량 vs 평균
    result["entry_vol_vs_avg"] = entry_candle["candle_acc_trade_volume"] / result["avg_vol_30"] if result["avg_vol_30"] > 0 else 0

    # ============================================
    # 7. 직전 봉 대비 변화 (중요!)
    # ============================================
    prev_candle = pre_candles[-1]
    result["vs_prev_close"] = (entry_price / prev_candle["trade_price"] - 1) * 100
    result["prev_was_bullish"] = prev_candle["trade_price"] > prev_candle["opening_price"]

    # 직전 5봉 평균 종가 대비
    avg_close_5 = statistics.mean([c["trade_price"] for c in pre_candles[-5:]])
    result["vs_avg_close_5"] = (entry_price / avg_close_5 - 1) * 100

    # ============================================
    # 8. 결과 수익률
    # ============================================
    if post_candles:
        max_price = max(c["high_price"] for c in post_candles)
        result["gain_5m"] = (max_price / entry_price - 1) * 100
    else:
        result["gain_5m"] = 0

    return result

def main():
    print("=" * 70)
    print("📊 상승 전 30봉 패턴 분석 (v2)")
    print("=" * 70)

    results = []
    for ticker, date_str, time_str in CASES:
        print(f"분석 중: {ticker} @ {date_str} {time_str}...", end=" ")
        result = analyze_pre_entry_pattern(ticker, date_str, time_str)
        if result:
            results.append(result)
            print("✓")
        else:
            print("✗")
        time.sleep(0.15)

    if not results:
        print("분석 결과 없음")
        return

    print("\n")
    print("=" * 70)
    print("📈 진입 전 30봉 공통 패턴")
    print("=" * 70)

    # 핵심 지표 통계
    metrics = [
        ("volatility_squeeze", "변동성 축소율 (최근10/이전20)", "x"),
        ("range_squeeze", "범위 수렴율 (5봉/10봉)", "x"),
        ("consolidation_range_10", "10봉 횡보 범위", "%"),
        ("consolidation_range_5", "5봉 횡보 범위", "%"),
        ("price_position_30", "30봉 내 가격위치 (0=저점)", "%"),
        ("vs_ema20", "EMA20 대비", "%"),
        ("vol_trend_ratio", "거래량 추세 (최근/이전)", "x"),
        ("bullish_ratio_5", "직전5봉 양봉비율", "%"),
        ("higher_lows_count", "저점상승 횟수 (4개중)", "회"),
        ("higher_highs_count", "고점상승 횟수 (4개중)", "회"),
        ("vs_prev_close", "직전봉 대비 변화", "%"),
        ("entry_vol_vs_avg", "진입봉 거래량/평균", "x"),
    ]

    print("\n[핵심 지표 통계]")
    print("-" * 70)

    for key, label, unit in metrics:
        values = [r[key] for r in results if key in r and r[key] is not None]
        if values:
            avg = statistics.mean(values)
            med = statistics.median(values)
            min_v = min(values)
            max_v = max(values)
            print(f"{label:30s}: 평균 {avg:6.2f}{unit} | 중앙값 {med:6.2f}{unit} | [{min_v:.2f}~{max_v:.2f}]")

    # 패턴 발견
    print("\n")
    print("=" * 70)
    print("💡 발견된 패턴")
    print("=" * 70)

    # 변동성 축소
    squeeze_values = [r["volatility_squeeze"] for r in results]
    squeeze_ratio = sum(1 for v in squeeze_values if v < 0.8) / len(squeeze_values) * 100
    print(f"\n1. 변동성 축소 (최근10봉 < 이전20봉의 80%): {squeeze_ratio:.0f}% 케이스")

    # 횡보 범위
    consol_values = [r["consolidation_range_5"] for r in results]
    tight_ratio = sum(1 for v in consol_values if v < 1.0) / len(consol_values) * 100
    print(f"2. 타이트한 횡보 (5봉 범위 < 1%): {tight_ratio:.0f}% 케이스")

    # 가격 위치
    pos_values = [r["price_position_30"] for r in results]
    mid_low_ratio = sum(1 for v in pos_values if v < 50) / len(pos_values) * 100
    print(f"3. 30봉 범위 중하단 (50% 이하): {mid_low_ratio:.0f}% 케이스")

    # Higher Lows
    hl_values = [r["higher_lows_count"] for r in results]
    hl_ratio = sum(1 for v in hl_values if v >= 2) / len(hl_values) * 100
    print(f"4. 저점 상승 패턴 (2회 이상): {hl_ratio:.0f}% 케이스")

    # 거래량 감소 후
    vol_values = [r["vol_trend_ratio"] for r in results]
    vol_dry_ratio = sum(1 for v in vol_values if v < 0.8) / len(vol_values) * 100
    print(f"5. 거래량 감소 추세 (< 0.8x): {vol_dry_ratio:.0f}% 케이스")

    # 진입봉
    entry_bull = sum(1 for r in results if r["entry_is_bullish"]) / len(results) * 100
    print(f"6. 진입봉 양봉: {entry_bull:.0f}% 케이스")

    print("\n")
    print("=" * 70)
    print("🎯 진입 조건 제안")
    print("=" * 70)

    avg_squeeze = statistics.mean(squeeze_values)
    avg_consol = statistics.mean(consol_values)
    avg_pos = statistics.mean(pos_values)
    avg_hl = statistics.mean(hl_values)

    print(f"""
상승 전 공통 패턴 기반 진입 조건:

1. 변동성 축소: 최근 10봉 변동성 < 이전 20봉의 {avg_squeeze:.0%}
2. 횡보 수렴: 최근 5봉 범위 < {avg_consol:.1f}%
3. 가격 위치: 30봉 고저 범위 중 {avg_pos:.0f}% 부근
4. 저점 상승: 최근 5봉 중 {avg_hl:.0f}회 이상 저점↑
5. 현재봉 양봉 전환
""")

    # 개별 상세
    print("\n[개별 케이스 상세]")
    print("-" * 90)
    print(f"{'종목':8s} | {'변동축소':8s} | {'5봉범위':8s} | {'가격위치':8s} | {'저점↑':6s} | {'진입봉':6s}")
    print("-" * 90)
    for r in results:
        print(f"{r['ticker']:8s} | {r['volatility_squeeze']:7.2f}x | {r['consolidation_range_5']:7.2f}% | "
              f"{r['price_position_30']:7.1f}% | {r['higher_lows_count']:5d}회 | "
              f"{'양봉' if r['entry_is_bullish'] else '음봉':6s}")

if __name__ == "__main__":
    main()
