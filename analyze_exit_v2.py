# -*- coding: utf-8 -*-
"""
트레일링 분석 v2 - 수정본
- 중간 눌림/회복 패턴 정확히 추적
- 트레일링 폭별 조기 청산 시뮬레이션
"""
import requests
import time
from datetime import datetime, timedelta
import statistics

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

def analyze_price_path(ticker, date_str, time_str, minutes=30):
    """진입 후 가격 경로 분석"""
    dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    to_time = (dt + timedelta(minutes=minutes + 5)).strftime("%Y-%m-%dT%H:%M:%S") + "+09:00"

    candles = get_candles(ticker, to_time, minutes + 5, unit=1)
    time.sleep(0.12)

    if not candles or len(candles) < 5:
        return None

    candles = list(reversed(candles))

    # 진입 시점 찾기
    entry_idx = 0
    for i, c in enumerate(candles):
        candle_time = c["candle_date_time_kst"][:16]
        target_time = f"{date_str}T{time_str}"
        if candle_time == target_time:
            entry_idx = i
            break

    entry_price = candles[entry_idx]["trade_price"]
    post_candles = candles[entry_idx:]

    if len(post_candles) < 3:
        return None

    # === 가격 경로 추적 ===
    prices = []  # 각 분봉의 (고점, 저점, 종가) 수익률
    running_high = entry_price
    max_gain = 0
    max_drawdown = 0

    # 모든 눌림(고점 대비 하락) 기록
    all_drawdowns_from_peak = []  # 고점 대비 하락폭 (%)

    for i, c in enumerate(post_candles):
        high = c["high_price"]
        low = c["low_price"]
        close = c["trade_price"]

        # 수익률 계산
        gain_high = (high / entry_price - 1) * 100
        gain_low = (low / entry_price - 1) * 100
        gain_close = (close / entry_price - 1) * 100

        prices.append({
            "minute": i,
            "high": gain_high,
            "low": gain_low,
            "close": gain_close
        })

        # 최대 수익/손실
        max_gain = max(max_gain, gain_high)
        max_drawdown = min(max_drawdown, gain_low)

        # 고점 갱신
        if high > running_high:
            running_high = high

        # 현재 고점 대비 하락폭 기록
        if running_high > entry_price:
            drop_from_peak = (running_high - low) / running_high * 100
            if drop_from_peak > 0.05:  # 0.05% 이상 하락만 기록
                all_drawdowns_from_peak.append(drop_from_peak)

    # 최종 고점 대비 최종 가격 하락폭
    final_price = post_candles[-1]["trade_price"]
    final_drop_from_peak = (running_high - final_price) / running_high * 100 if running_high > entry_price else 0

    return {
        "max_gain": max_gain,
        "max_drawdown": max_drawdown,
        "final_gain": (final_price / entry_price - 1) * 100,
        "peak_price": running_high,
        "entry_price": entry_price,
        "final_drop_from_peak": final_drop_from_peak,
        "all_drawdowns": all_drawdowns_from_peak,
        "prices": prices,
    }

def simulate_trailing(result, trail_pct):
    """특정 트레일링 폭으로 시뮬레이션"""
    entry_price = result["entry_price"]
    prices = result["prices"]

    running_high = entry_price
    trail_stop = 0

    for p in prices:
        # 고점에서의 실제 가격 계산
        high_price = entry_price * (1 + p["high"] / 100)
        low_price = entry_price * (1 + p["low"] / 100)

        # 고점 갱신
        if high_price > running_high:
            running_high = high_price
            trail_stop = running_high * (1 - trail_pct / 100)

        # 트레일링 스탑 트리거
        if trail_stop > 0 and low_price <= trail_stop:
            exit_gain = (trail_stop / entry_price - 1) * 100
            return {
                "triggered": True,
                "minute": p["minute"],
                "exit_gain": exit_gain,
                "missed_gain": result["max_gain"] - exit_gain
            }

    return {
        "triggered": False,
        "minute": None,
        "exit_gain": result["final_gain"],
        "missed_gain": result["max_gain"] - result["final_gain"]
    }

def main():
    print("=" * 80)
    print("트레일링 분석 v2 - 수정본")
    print("=" * 80)

    success_results = []
    fail_results = []

    print("\n데이터 수집 중...")
    for ticker, date_str, time_str, is_success in CASES:
        result = analyze_price_path(ticker, date_str, time_str, 30)
        if result:
            result["is_success"] = is_success
            if is_success:
                success_results.append(result)
            else:
                fail_results.append(result)

            tag = "성공" if is_success else "실패"
            print(f"  [{tag}] {ticker} {time_str}: 최대+{result['max_gain']:.2f}% "
                  f"고점후-{result['final_drop_from_peak']:.2f}%")

    print(f"\n수집 완료: 성공 {len(success_results)}건, 실패 {len(fail_results)}건")

    # === 성공 케이스 기본 통계 ===
    print("\n" + "=" * 80)
    print("📊 성공 케이스 기본 통계")
    print("=" * 80)

    max_gains = [r["max_gain"] for r in success_results]
    final_drops = [r["final_drop_from_peak"] for r in success_results]

    print(f"\n최대 상승률:")
    print(f"  평균: {statistics.mean(max_gains):.2f}%")
    print(f"  중앙값: {statistics.median(max_gains):.2f}%")
    print(f"  최소: {min(max_gains):.2f}%, 최대: {max(max_gains):.2f}%")

    print(f"\n고점 대비 최종 하락:")
    print(f"  평균: {statistics.mean(final_drops):.2f}%")
    print(f"  중앙값: {statistics.median(final_drops):.2f}%")

    # === 모든 눌림 분석 ===
    print("\n" + "=" * 80)
    print("📉 중간 눌림 분석 (고점 대비 하락)")
    print("=" * 80)

    all_drawdowns = []
    for r in success_results:
        all_drawdowns.extend(r["all_drawdowns"])

    if all_drawdowns:
        print(f"\n총 눌림 횟수: {len(all_drawdowns)}회")
        print(f"  평균: {statistics.mean(all_drawdowns):.2f}%")
        print(f"  중앙값: {statistics.median(all_drawdowns):.2f}%")
        print(f"  최대: {max(all_drawdowns):.2f}%")

        # 분포
        print("\n[눌림 깊이 분포]")
        bins = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0),
                (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 100)]
        for low, high in bins:
            cnt = sum(1 for v in all_drawdowns if low <= v < high)
            pct = cnt / len(all_drawdowns) * 100 if all_drawdowns else 0
            bar = "█" * int(pct / 3)
            print(f"  {low:.1f}%~{high:.1f}%: {cnt:>4}회 ({pct:>5.1f}%) {bar}")

    # === 트레일링 시뮬레이션 ===
    print("\n" + "=" * 80)
    print("🎯 트레일링 폭별 시뮬레이션 (성공 케이스)")
    print("=" * 80)

    print(f"\n{'트레일링':>8} | {'청산됨':>12} | {'평균수익':>10} | {'놓친수익':>10} | 판정")
    print("-" * 70)

    best_trail = None
    best_net_gain = -100

    for trail_pct in [0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]:
        results = [simulate_trailing(r, trail_pct) for r in success_results]

        triggered_cnt = sum(1 for r in results if r["triggered"])
        triggered_pct = triggered_cnt / len(results) * 100

        exit_gains = [r["exit_gain"] for r in results]
        missed_gains = [r["missed_gain"] for r in results]

        avg_exit = statistics.mean(exit_gains)
        avg_missed = statistics.mean(missed_gains)

        # 판정
        if triggered_pct >= 80:
            verdict = "❌ 너무 타이트"
        elif triggered_pct >= 60:
            verdict = "⚠️  주의"
        elif triggered_pct >= 40:
            verdict = "✅ 적절"
        else:
            verdict = "🔵 느슨"

        # 최적 찾기 (청산율 40-70%, 평균수익 최대화)
        if 30 <= triggered_pct <= 70 and avg_exit > best_net_gain:
            best_net_gain = avg_exit
            best_trail = trail_pct

        print(f"{trail_pct:>7.1f}% | {triggered_cnt:>3}/{len(results)} ({triggered_pct:>4.0f}%) | "
              f"{avg_exit:>+8.2f}% | {avg_missed:>+8.2f}% | {verdict}")

    # === 권장 설정 ===
    print("\n" + "=" * 80)
    print("💡 권장 트레일링 설정")
    print("=" * 80)

    if all_drawdowns:
        sorted_dd = sorted(all_drawdowns)
        p50 = sorted_dd[int(len(sorted_dd) * 0.50)]
        p75 = sorted_dd[int(len(sorted_dd) * 0.75)]
        p90 = sorted_dd[int(len(sorted_dd) * 0.90)]

        print(f"\n눌림 깊이 백분위:")
        print(f"  50백분위: {p50:.2f}%")
        print(f"  75백분위: {p75:.2f}%")
        print(f"  90백분위: {p90:.2f}%")

        print(f"\n권장: 트레일링 폭 {p75:.1f}% ~ {p90:.1f}%")
        print(f"  → 75%의 정상 눌림은 버티고, 큰 하락만 청산")

    if best_trail:
        print(f"\n시뮬레이션 기반 최적: {best_trail:.1f}%")
        print(f"  → 평균 수익 {best_net_gain:.2f}% 달성")

if __name__ == "__main__":
    main()
