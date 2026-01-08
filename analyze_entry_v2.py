# -*- coding: utf-8 -*-
"""
진입 조건 분석 v2 - 데이터 기반 최적 임계치 탐색
- 고정 조건 비교 대신 데이터 분포 분석
- 최적 임계치 자동 계산
- 새 케이스 추가해도 자동 분석
"""
import requests
import time
from datetime import datetime, timedelta
import statistics

# 케이스 목록 (analyze_deep_v3.py와 동기화)
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
    ("STRAX", "2026-01-08", "09:00", True),
    ("BREV", "2026-01-08", "09:06", True),
    ("ELF", "2026-01-08", "10:35", True),
    ("MED", "2026-01-08", "11:21", True),
    ("VIRTUAL", "2026-01-08", "17:18", True),
    ("ARDR", "2026-01-08", "19:40", True),
    ("IP", "2026-01-08", "18:20", True),
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
    ("BREV", "2026-01-08", "23:46", False),
    ("G", "2026-01-08", "22:19", False),
    ("AERGO", "2026-01-08", "20:55", False),
    ("BTC", "2026-01-08", "19:43", False),
    ("ETH", "2026-01-08", "19:36", False),
    ("VIRTUAL", "2026-01-08", "19:26", False),
    ("CVC", "2026-01-08", "19:21", False),
    ("BREV", "2026-01-08", "18:41", False),
    ("IP", "2026-01-08", "18:29", False),
    ("IP", "2026-01-08", "18:25", False),
    ("IP", "2026-01-08", "18:24", False),
    ("BREV", "2026-01-08", "18:18", False),
    ("IP", "2026-01-08", "18:18", False),
    # 1/9 추가
    ("BREV", "2026-01-09", "08:52", False),
    ("BREV", "2026-01-09", "08:51", False),
    ("VIRTUAL", "2026-01-09", "07:11", False),
    ("SOL", "2026-01-09", "07:00", False),
    ("IP", "2026-01-09", "02:50", False),
    ("XRP", "2026-01-09", "02:03", False),
    ("XRP", "2026-01-09", "01:13", False),
    ("VIRTUAL", "2026-01-09", "01:09", False),
    ("KAITO", "2026-01-09", "01:02", False),
    # 1/9 성공
    ("G", "2026-01-09", "04:23", True),
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
    dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    to_time = (dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S") + "+09:00"

    candles_5m = get_candles(ticker, to_time, 25, unit=5)
    time.sleep(0.12)

    if not candles_5m or len(candles_5m) < 20:
        return None

    candles_5m = list(reversed(candles_5m))
    closes_5m = [c["trade_price"] for c in candles_5m[-20:]]

    result = {}
    result["rsi_5m"] = calc_rsi(closes_5m, 14) or 50

    if len(closes_5m) >= 10:
        recent_avg = sum(closes_5m[-5:]) / 5
        prev_avg = sum(closes_5m[-10:-5]) / 5
        result["trend_5m"] = (recent_avg / prev_avg - 1) * 100 if prev_avg > 0 else 0
    else:
        result["trend_5m"] = 0

    ema20 = calc_ema(closes_5m, 20) if len(closes_5m) >= 20 else None
    result["price_vs_ema20"] = (closes_5m[-1] / ema20 - 1) * 100 if ema20 else 0

    # 시간 추출
    result["hour"] = int(time_str.split(":")[0])

    return result

def find_optimal_threshold(success_vals, fail_vals, target_success_rate=0.75):
    """
    목표 성공 통과율을 유지하면서 실패를 최대한 필터링하는 임계치 찾기
    """
    all_vals = sorted(set(success_vals + fail_vals))

    best_threshold = None
    best_fail_rate = 1.0

    for thresh in all_vals:
        s_pass = sum(1 for v in success_vals if v >= thresh)
        s_rate = s_pass / len(success_vals) if success_vals else 0

        f_pass = sum(1 for v in fail_vals if v >= thresh)
        f_rate = f_pass / len(fail_vals) if fail_vals else 0

        if s_rate >= target_success_rate and f_rate < best_fail_rate:
            best_threshold = thresh
            best_fail_rate = f_rate

    return best_threshold, best_fail_rate

def main():
    print("=" * 80)
    print("진입 조건 분석 v2 - 최적 임계치 탐색")
    print("=" * 80)

    # 데이터 수집
    print("\n데이터 수집 중...")
    success_data = []
    fail_data = []

    for ticker, date_str, time_str, is_success in CASES:
        result = analyze_case(ticker, date_str, time_str)
        if result:
            if is_success:
                success_data.append(result)
            else:
                fail_data.append(result)
            tag = "성공" if is_success else "실패"
            print(f"  [{tag}] {ticker} {time_str}: RSI={result['rsi_5m']:.0f} 추세={result['trend_5m']:.2f}%")

    print(f"\n수집 완료: 성공 {len(success_data)}건, 실패 {len(fail_data)}건")

    # === RSI 분포 분석 ===
    print("\n" + "=" * 80)
    print("📊 RSI(5분봉) 분포")
    print("=" * 80)

    s_rsi = [d["rsi_5m"] for d in success_data]
    f_rsi = [d["rsi_5m"] for d in fail_data]

    print(f"성공: 평균 {statistics.mean(s_rsi):.1f}, 중앙값 {statistics.median(s_rsi):.1f}, "
          f"최소 {min(s_rsi):.1f}, 최대 {max(s_rsi):.1f}")
    print(f"실패: 평균 {statistics.mean(f_rsi):.1f}, 중앙값 {statistics.median(f_rsi):.1f}, "
          f"최소 {min(f_rsi):.1f}, 최대 {max(f_rsi):.1f}")

    # RSI 구간별 분포
    print("\n[RSI 구간별 분포]")
    for low, high in [(0, 50), (50, 60), (60, 70), (70, 80), (80, 100)]:
        s_cnt = sum(1 for v in s_rsi if low <= v < high)
        f_cnt = sum(1 for v in f_rsi if low <= v < high)
        print(f"  {low:>2}-{high:<3}: 성공 {s_cnt:>2}건 ({s_cnt/len(s_rsi)*100:>4.0f}%), "
              f"실패 {f_cnt:>2}건 ({f_cnt/len(f_rsi)*100:>4.0f}%)")

    # === 추세 분포 분석 ===
    print("\n" + "=" * 80)
    print("📊 5분봉추세(%) 분포")
    print("=" * 80)

    s_trend = [d["trend_5m"] for d in success_data]
    f_trend = [d["trend_5m"] for d in fail_data]

    print(f"성공: 평균 {statistics.mean(s_trend):.2f}%, 중앙값 {statistics.median(s_trend):.2f}%, "
          f"최소 {min(s_trend):.2f}%, 최대 {max(s_trend):.2f}%")
    print(f"실패: 평균 {statistics.mean(f_trend):.2f}%, 중앙값 {statistics.median(f_trend):.2f}%, "
          f"최소 {min(f_trend):.2f}%, 최대 {max(f_trend):.2f}%")

    # 추세 구간별 분포
    print("\n[추세 구간별 분포]")
    for low, high in [(-10, 0), (0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 10)]:
        s_cnt = sum(1 for v in s_trend if low <= v < high)
        f_cnt = sum(1 for v in f_trend if low <= v < high)
        print(f"  {low:>4.1f}~{high:<4.1f}%: 성공 {s_cnt:>2}건 ({s_cnt/len(s_trend)*100:>4.0f}%), "
              f"실패 {f_cnt:>2}건 ({f_cnt/len(f_trend)*100:>4.0f}%)")

    # === 최적 임계치 탐색 ===
    print("\n" + "=" * 80)
    print("🎯 최적 임계치 탐색 (목표: 성공 75% 유지)")
    print("=" * 80)

    rsi_thresh, rsi_fail = find_optimal_threshold(s_rsi, f_rsi, 0.75)
    trend_thresh, trend_fail = find_optimal_threshold(s_trend, f_trend, 0.75)

    print(f"\nRSI 최적 임계치: >= {rsi_thresh:.0f}")
    print(f"  → 성공 75%+ 유지, 실패 {rsi_fail*100:.0f}% 통과")

    print(f"\n추세 최적 임계치: >= {trend_thresh:.2f}%")
    print(f"  → 성공 75%+ 유지, 실패 {trend_fail*100:.0f}% 통과")

    # === 조합 테스트 ===
    print("\n" + "=" * 80)
    print("🔬 RSI + 추세 조합 테스트")
    print("=" * 80)

    print(f"\n{'RSI':>5} | {'추세':>6} | {'성공통과':>12} | {'실패통과':>12} | {'순효과':>8}")
    print("-" * 60)

    for rsi_min in [60, 65, 68, 70, 75]:
        for trend_min in [0.5, 0.8, 1.0, 1.2]:
            s_pass = sum(1 for d in success_data if d["rsi_5m"] >= rsi_min and d["trend_5m"] >= trend_min)
            f_pass = sum(1 for d in fail_data if d["rsi_5m"] >= rsi_min and d["trend_5m"] >= trend_min)

            s_rate = s_pass / len(success_data) * 100
            f_rate = f_pass / len(fail_data) * 100
            net = s_rate - f_rate

            # 성공 70% 이상만 표시
            if s_rate >= 70:
                s_str = f"{s_pass}/{len(success_data)} ({s_rate:.0f}%)"
                f_str = f"{f_pass}/{len(fail_data)} ({f_rate:.0f}%)"
                print(f"{rsi_min:>5} | {trend_min:>5.1f}% | {s_str:>12} | {f_str:>12} | {net:>+6.0f}%")

    # === 권장 설정 ===
    print("\n" + "=" * 80)
    print("💡 권장 설정")
    print("=" * 80)

    # 최고 순효과 찾기
    best_net = -100
    best_config = None
    for rsi_min in range(55, 80):
        for trend_min in [x/10 for x in range(0, 20)]:
            s_pass = sum(1 for d in success_data if d["rsi_5m"] >= rsi_min and d["trend_5m"] >= trend_min)
            f_pass = sum(1 for d in fail_data if d["rsi_5m"] >= rsi_min and d["trend_5m"] >= trend_min)

            s_rate = s_pass / len(success_data) * 100
            f_rate = f_pass / len(fail_data) * 100
            net = s_rate - f_rate

            if s_rate >= 70 and net > best_net:
                best_net = net
                best_config = (rsi_min, trend_min, s_rate, f_rate)

    if best_config:
        rsi, trend, s_rate, f_rate = best_config
        print(f"\n최적 조합: RSI >= {rsi}, 추세 >= {trend:.1f}%")
        print(f"  성공 통과: {s_rate:.0f}%")
        print(f"  실패 통과: {f_rate:.0f}%")
        print(f"  순효과: +{best_net:.0f}%")

if __name__ == "__main__":
    main()
