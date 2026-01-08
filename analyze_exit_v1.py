# -*- coding: utf-8 -*-
"""
진입 후 가격 움직임 분석 스크립트 v1
- 진입 시점 이후 봉들 분석
- 최대 상승폭, 최대 하락폭, 도달 시간 등
- 성공/실패별 패턴 비교 → 트레일링 최적화 기초 데이터
"""
import requests
import time
from datetime import datetime, timedelta
import statistics

# 분석할 케이스들 (analyze_deep_v3.py와 동일)
CASES = [
    # === 성공 케이스 (29건) ===
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
    # === 실패 케이스 (30건) ===
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
    # === 1/8 오후 실패 케이스 (11건) - 조건 완화 후 ===
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
    # === 1/8 오후 성공 케이스 (1건) ===
    ("VIRTUAL", "2026-01-08", "17:18", True),
]

def get_candles(market, to_time, count=50, unit=1):
    """캔들 조회"""
    url = f"https://api.upbit.com/v1/candles/minutes/{unit}"
    params = {"market": f"KRW-{market}", "to": to_time, "count": count}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return []
    except:
        return []

def analyze_post_entry(ticker, date_str, time_str, candles_after=30):
    """
    진입 후 가격 움직임 분석

    Returns:
        dict: {
            max_gain: 최대 상승률 (%)
            max_drawdown: 최대 하락률 (%)
            time_to_peak: 고점 도달 시간 (분)
            final_gain: 30분 후 수익률 (%)
            peak_then_drop: 고점 이후 하락폭 (%)
            candle_gains: 각 분봉별 수익률 리스트
        }
    """
    # 진입 시점 + 30분 후까지 캔들 조회
    dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    to_time = (dt + timedelta(minutes=candles_after + 5)).strftime("%Y-%m-%dT%H:%M:%S") + "+09:00"

    candles = get_candles(ticker, to_time, candles_after + 5, unit=1)
    time.sleep(0.12)

    if not candles or len(candles) < 5:
        return None

    # 캔들은 최신순 → 역순 정렬 (오래된 것부터)
    candles = list(reversed(candles))

    # 진입 시점 캔들 찾기
    entry_idx = None
    for i, c in enumerate(candles):
        candle_time = c["candle_date_time_kst"][:16]  # "2026-01-06T10:09"
        target_time = f"{date_str}T{time_str}"
        if candle_time == target_time:
            entry_idx = i
            break

    if entry_idx is None:
        # 못 찾으면 첫 캔들 기준
        entry_idx = 0

    entry_price = candles[entry_idx]["trade_price"]

    # 진입 이후 캔들 분석
    post_candles = candles[entry_idx:]
    if len(post_candles) < 3:
        return None

    # 각 분봉별 수익률 계산
    candle_gains = []
    max_gain = 0
    max_drawdown = 0
    peak_price = entry_price
    time_to_peak = 0

    for i, c in enumerate(post_candles):
        high = c["high_price"]
        low = c["low_price"]
        close = c["trade_price"]

        # 고점/저점 대비 수익률
        gain_high = (high / entry_price - 1) * 100
        gain_low = (low / entry_price - 1) * 100
        gain_close = (close / entry_price - 1) * 100

        candle_gains.append({
            "minute": i,
            "high": gain_high,
            "low": gain_low,
            "close": gain_close
        })

        # 최대 상승 갱신
        if gain_high > max_gain:
            max_gain = gain_high
            time_to_peak = i
            peak_price = high

        # 최대 하락 (진입가 대비)
        if gain_low < max_drawdown:
            max_drawdown = gain_low

    # 고점 이후 하락폭
    if peak_price > entry_price:
        # 고점 이후 최저가 찾기
        lowest_after_peak = min(c["low_price"] for c in post_candles[time_to_peak:])
        peak_then_drop = (peak_price - lowest_after_peak) / peak_price * 100
    else:
        peak_then_drop = 0

    # 마지막 캔들 수익률 (30분 후)
    final_gain = (post_candles[-1]["trade_price"] / entry_price - 1) * 100

    return {
        "max_gain": max_gain,
        "max_drawdown": max_drawdown,
        "time_to_peak": time_to_peak,
        "final_gain": final_gain,
        "peak_then_drop": peak_then_drop,
        "candle_gains": candle_gains
    }

def main():
    print("=" * 80)
    print("진입 후 가격 움직임 분석 v1")
    print("=" * 80)

    success_results = []
    fail_results = []

    for ticker, date_str, time_str, is_success in CASES:
        result = analyze_post_entry(ticker, date_str, time_str, candles_after=30)
        if result:
            tag = "성공" if is_success else "실패"
            print(f"[{tag}] {ticker} {date_str} {time_str}: "
                  f"최대+{result['max_gain']:.2f}% 최대-{abs(result['max_drawdown']):.2f}% "
                  f"고점{result['time_to_peak']}분 30분후{result['final_gain']:+.2f}%")

            if is_success:
                success_results.append(result)
            else:
                fail_results.append(result)

    print("\n" + "=" * 80)
    print("📊 성공 케이스 통계 (N=%d)" % len(success_results))
    print("=" * 80)

    if success_results:
        max_gains = [r["max_gain"] for r in success_results]
        max_dds = [r["max_drawdown"] for r in success_results]
        time_peaks = [r["time_to_peak"] for r in success_results]
        final_gains = [r["final_gain"] for r in success_results]
        peak_drops = [r["peak_then_drop"] for r in success_results]

        print(f"최대 상승: 평균 {statistics.mean(max_gains):.2f}%, 중앙값 {statistics.median(max_gains):.2f}%, "
              f"최소 {min(max_gains):.2f}%, 최대 {max(max_gains):.2f}%")
        print(f"최대 하락: 평균 {statistics.mean(max_dds):.2f}%, 중앙값 {statistics.median(max_dds):.2f}%")
        print(f"고점 도달: 평균 {statistics.mean(time_peaks):.1f}분, 중앙값 {statistics.median(time_peaks):.0f}분")
        print(f"30분 후: 평균 {statistics.mean(final_gains):+.2f}%, 중앙값 {statistics.median(final_gains):+.2f}%")
        print(f"고점→하락: 평균 {statistics.mean(peak_drops):.2f}%, 중앙값 {statistics.median(peak_drops):.2f}%")

        # 분포 분석
        print("\n[상승폭 분포]")
        bins = [(0, 0.3), (0.3, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, 100)]
        for low, high in bins:
            count = sum(1 for g in max_gains if low <= g < high)
            pct = count / len(max_gains) * 100
            print(f"  +{low:.1f}% ~ +{high:.1f}%: {count}건 ({pct:.0f}%)")

    print("\n" + "=" * 80)
    print("📊 실패 케이스 통계 (N=%d)" % len(fail_results))
    print("=" * 80)

    if fail_results:
        max_gains = [r["max_gain"] for r in fail_results]
        max_dds = [r["max_drawdown"] for r in fail_results]
        time_peaks = [r["time_to_peak"] for r in fail_results]
        final_gains = [r["final_gain"] for r in fail_results]

        print(f"최대 상승: 평균 {statistics.mean(max_gains):.2f}%, 중앙값 {statistics.median(max_gains):.2f}%")
        print(f"최대 하락: 평균 {statistics.mean(max_dds):.2f}%, 중앙값 {statistics.median(max_dds):.2f}%")
        print(f"고점 도달: 평균 {statistics.mean(time_peaks):.1f}분, 중앙값 {statistics.median(time_peaks):.0f}분")
        print(f"30분 후: 평균 {statistics.mean(final_gains):+.2f}%, 중앙값 {statistics.median(final_gains):+.2f}%")

    # === 분봉별 상세 분석 ===
    print("\n" + "=" * 80)
    print("📈 분봉별 수익률 추이 (중앙값)")
    print("=" * 80)

    print(f"\n{'분':>4} | {'성공(고점)':>10} | {'성공(종가)':>10} | {'실패(고점)':>10} | {'실패(종가)':>10}")
    print("-" * 60)

    for minute in range(0, 20):
        s_highs = [r["candle_gains"][minute]["high"] for r in success_results if len(r["candle_gains"]) > minute]
        s_closes = [r["candle_gains"][minute]["close"] for r in success_results if len(r["candle_gains"]) > minute]
        f_highs = [r["candle_gains"][minute]["high"] for r in fail_results if len(r["candle_gains"]) > minute]
        f_closes = [r["candle_gains"][minute]["close"] for r in fail_results if len(r["candle_gains"]) > minute]

        if s_highs and f_highs:
            print(f"{minute:>4} | {statistics.median(s_highs):>+9.2f}% | {statistics.median(s_closes):>+9.2f}% | "
                  f"{statistics.median(f_highs):>+9.2f}% | {statistics.median(f_closes):>+9.2f}%")

    # === 트레일링 최적화 제안 ===
    print("\n" + "=" * 80)
    print("🎯 트레일링 최적화 제안")
    print("=" * 80)

    if success_results:
        # 성공 케이스에서 고점 후 하락폭 분석
        peak_drops = [r["peak_then_drop"] for r in success_results]

        print(f"\n[성공 케이스 고점→하락 분포]")
        print(f"  - 평균: {statistics.mean(peak_drops):.2f}%")
        print(f"  - 중앙값: {statistics.median(peak_drops):.2f}%")
        print(f"  - 25백분위: {sorted(peak_drops)[len(peak_drops)//4]:.2f}%")
        print(f"  - 75백분위: {sorted(peak_drops)[len(peak_drops)*3//4]:.2f}%")

        # 고점 대비 하락폭별 분포
        drop_bins = [(0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 1.0), (1.0, 100)]
        print(f"\n[고점→하락폭 분포]")
        for low, high in drop_bins:
            count = sum(1 for d in peak_drops if low <= d < high)
            pct = count / len(peak_drops) * 100
            print(f"  {low:.1f}% ~ {high:.1f}%: {count}건 ({pct:.0f}%)")

        # 최적 트레일링 추천
        print(f"\n[추천 트레일링 폭]")
        med_drop = statistics.median(peak_drops)
        print(f"  - 현재 구간별: 0.06%~0.3%")
        print(f"  - 제안: 성공 케이스 고점하락 중앙값({med_drop:.2f}%) 기준 조정 필요")

if __name__ == "__main__":
    main()
