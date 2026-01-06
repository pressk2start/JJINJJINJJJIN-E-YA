# -*- coding: utf-8 -*-
"""
상승 차트 분석 스크립트
- 상승 시작점 기준 전후 캔들 데이터 수집
- 공통 패턴 분석
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

def get_candles(market, to_time, count=30):
    """1분봉 캔들 조회 (to_time 기준 이전 count개)"""
    url = "https://api.upbit.com/v1/candles/minutes/1"
    params = {
        "market": f"KRW-{market}",
        "to": to_time,
        "count": count
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"  [ERR] {market}: {resp.status_code}")
            return []
    except Exception as e:
        print(f"  [ERR] {market}: {e}")
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

def analyze_case(ticker, date_str, time_str):
    """개별 케이스 분석"""
    # 시간 파싱 (KST)
    dt_str = f"{date_str}T{time_str}:00+09:00"
    dt = datetime.fromisoformat(dt_str)

    # API용 UTC 변환
    to_time = dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 상승 시점 기준 이전 30개 + 이후 10개 캔들
    candles_before = get_candles(ticker, to_time, 30)

    # 이후 캔들 (10분 뒤 기준)
    dt_after = dt + timedelta(minutes=15)
    to_time_after = dt_after.strftime("%Y-%m-%dT%H:%M:%S")
    candles_after = get_candles(ticker, to_time_after, 15)

    if not candles_before:
        return None

    # 캔들은 최신순이므로 역순 정렬
    candles_before = list(reversed(candles_before))
    candles_after = list(reversed(candles_after)) if candles_after else []

    # 상승 시점 캔들 (마지막)
    entry_candle = candles_before[-1]

    # 직전 20개 캔들로 지표 계산
    prev_candles = candles_before[:-1] if len(candles_before) > 1 else candles_before

    # 종가 리스트
    closes = [c["trade_price"] for c in prev_candles]
    volumes = [c["candle_acc_trade_volume"] for c in prev_candles]

    # 지표 계산
    result = {
        "ticker": ticker,
        "time": f"{date_str} {time_str}",
        "entry_price": entry_candle["trade_price"],
        "entry_volume": entry_candle["candle_acc_trade_volume"],
    }

    # 1. EMA20 대비 위치
    if len(closes) >= 20:
        ema20 = calc_ema(closes, 20)
        result["vs_ema20"] = (entry_candle["trade_price"] / ema20 - 1) * 100 if ema20 else 0
        result["above_ema20"] = entry_candle["trade_price"] > ema20 if ema20 else False
    else:
        result["vs_ema20"] = 0
        result["above_ema20"] = None

    # 2. 직전 고점 대비
    if closes:
        recent_high = max(closes[-10:]) if len(closes) >= 10 else max(closes)
        result["vs_recent_high"] = (entry_candle["trade_price"] / recent_high - 1) * 100
        result["breaking_high"] = entry_candle["trade_price"] > recent_high

    # 3. 거래량 vs 평균
    if volumes:
        avg_vol = statistics.mean(volumes[-20:]) if len(volumes) >= 20 else statistics.mean(volumes)
        result["vol_vs_avg"] = entry_candle["candle_acc_trade_volume"] / avg_vol if avg_vol > 0 else 0

    # 4. 직전 N봉 연속 양봉 수
    bullish_streak = 0
    for c in reversed(prev_candles[-5:]):
        if c["trade_price"] > c["opening_price"]:
            bullish_streak += 1
        else:
            break
    result["bullish_streak"] = bullish_streak

    # 5. 직전 5봉 가격 변화율
    if len(closes) >= 5:
        result["price_chg_5m"] = (entry_candle["trade_price"] / closes[-5] - 1) * 100
    else:
        result["price_chg_5m"] = 0

    # 6. 직전 5봉 거래량 증가 추세
    if len(volumes) >= 5:
        vol_early = statistics.mean(volumes[-10:-5]) if len(volumes) >= 10 else volumes[-5]
        vol_late = statistics.mean(volumes[-5:])
        result["vol_trend"] = vol_late / vol_early if vol_early > 0 else 1
    else:
        result["vol_trend"] = 1

    # 7. 상승 후 10분간 최고 수익률
    if candles_after:
        max_price_after = max(c["high_price"] for c in candles_after)
        result["max_gain_10m"] = (max_price_after / entry_candle["trade_price"] - 1) * 100
    else:
        result["max_gain_10m"] = 0

    # 8. 캔들 크기 (시가 대비 종가)
    result["candle_body"] = (entry_candle["trade_price"] / entry_candle["opening_price"] - 1) * 100

    # 9. 윗꼬리 / 아랫꼬리 비율
    body = abs(entry_candle["trade_price"] - entry_candle["opening_price"])
    upper_wick = entry_candle["high_price"] - max(entry_candle["trade_price"], entry_candle["opening_price"])
    lower_wick = min(entry_candle["trade_price"], entry_candle["opening_price"]) - entry_candle["low_price"]
    total_range = entry_candle["high_price"] - entry_candle["low_price"]

    result["body_ratio"] = body / total_range * 100 if total_range > 0 else 0
    result["upper_wick_ratio"] = upper_wick / total_range * 100 if total_range > 0 else 0
    result["lower_wick_ratio"] = lower_wick / total_range * 100 if total_range > 0 else 0

    return result

def main():
    print("=" * 60)
    print("상승 차트 분석 시작 (20개 케이스)")
    print("=" * 60)

    results = []

    for ticker, date_str, time_str in CASES:
        print(f"\n분석 중: {ticker} @ {date_str} {time_str}")
        result = analyze_case(ticker, date_str, time_str)
        if result:
            results.append(result)
            print(f"  ✓ EMA20 대비: {result['vs_ema20']:.2f}% | 고점돌파: {result.get('breaking_high', '?')}")
            print(f"  ✓ 거래량 배수: {result['vol_vs_avg']:.1f}x | 5분 변화: {result['price_chg_5m']:.2f}%")
            print(f"  ✓ 10분후 최대수익: {result['max_gain_10m']:.2f}%")
        time.sleep(0.15)  # API 레이트 리밋

    # 통계 요약
    print("\n")
    print("=" * 60)
    print("📊 공통 패턴 분석 결과")
    print("=" * 60)

    if not results:
        print("분석 결과 없음")
        return

    # 각 지표별 통계
    metrics = {
        "vs_ema20": "EMA20 대비 (%)",
        "vs_recent_high": "최근고점 대비 (%)",
        "vol_vs_avg": "거래량 배수 (x)",
        "bullish_streak": "연속 양봉 수",
        "price_chg_5m": "5분간 가격변화 (%)",
        "vol_trend": "거래량 증가 추세",
        "max_gain_10m": "10분후 최대수익 (%)",
        "candle_body": "진입봉 몸통 (%)",
        "body_ratio": "몸통 비율 (%)",
    }

    print("\n[지표별 통계]")
    print("-" * 50)

    summary = {}
    for key, label in metrics.items():
        values = [r[key] for r in results if key in r and r[key] is not None]
        if values:
            avg = statistics.mean(values)
            med = statistics.median(values)
            min_v = min(values)
            max_v = max(values)
            summary[key] = {"avg": avg, "med": med, "min": min_v, "max": max_v}
            print(f"{label:20s}: 평균 {avg:7.2f} | 중앙값 {med:7.2f} | 범위 [{min_v:.2f} ~ {max_v:.2f}]")

    # 불리언 지표
    print("\n[조건 충족 비율]")
    print("-" * 50)

    above_ema = sum(1 for r in results if r.get("above_ema20") == True)
    breaking = sum(1 for r in results if r.get("breaking_high") == True)
    vol_surge = sum(1 for r in results if r.get("vol_vs_avg", 0) >= 1.5)
    bullish = sum(1 for r in results if r.get("bullish_streak", 0) >= 2)

    total = len(results)
    print(f"EMA20 위에서 진입:    {above_ema}/{total} ({above_ema/total*100:.0f}%)")
    print(f"최근고점 돌파:        {breaking}/{total} ({breaking/total*100:.0f}%)")
    print(f"거래량 1.5배 이상:    {vol_surge}/{total} ({vol_surge/total*100:.0f}%)")
    print(f"연속양봉 2개 이상:    {bullish}/{total} ({bullish/total*100:.0f}%)")

    # 핵심 인사이트
    print("\n")
    print("=" * 60)
    print("💡 핵심 인사이트")
    print("=" * 60)

    if summary.get("vs_ema20", {}).get("avg", 0) > 0:
        print("✓ 상승 시작점: 평균적으로 EMA20 위에서 진입")
    else:
        print("✓ 상승 시작점: EMA20 근처 또는 아래에서 시작")

    if summary.get("vol_vs_avg", {}).get("avg", 0) > 1.5:
        print(f"✓ 거래량: 평균 {summary['vol_vs_avg']['avg']:.1f}배로 확실한 거래량 동반")
    else:
        print(f"✓ 거래량: 평균 {summary.get('vol_vs_avg', {}).get('avg', 0):.1f}배 (크지 않음)")

    if summary.get("price_chg_5m", {}).get("avg", 0) > 0.5:
        print(f"✓ 모멘텀: 진입 전 5분간 이미 {summary['price_chg_5m']['avg']:.2f}% 상승 중")

    if breaking / total > 0.6:
        print("✓ 고점 돌파: 대부분 최근 고점 돌파 시점에서 진입")

    print("\n[개별 결과 상세]")
    print("-" * 80)
    for r in results:
        print(f"{r['ticker']:8s} | EMA20: {r['vs_ema20']:+5.1f}% | 고점돌파: {'Y' if r.get('breaking_high') else 'N'} | "
              f"거래량: {r['vol_vs_avg']:4.1f}x | 10분수익: {r['max_gain_10m']:+5.2f}%")

if __name__ == "__main__":
    main()
