# -*- coding: utf-8 -*-
"""
트레일링 분석 v2 - 중간 하락/회복 패턴 분석
- 고점→최종하락 뿐 아니라 중간 눌림/회복 패턴 분석
- 어느 트레일링 폭에서 조기 청산되는지 확인
- 최적 트레일링 폭 제안
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
    """
    진입 후 가격 경로 상세 분석
    - 각 분봉에서 고점 대비 하락폭 추적
    - 중간 눌림 후 회복 패턴 분석
    """
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

    # === 가격 경로 분석 ===
    running_high = entry_price  # 현재까지 최고가
    max_gain = 0  # 최대 수익률
    max_drawdown = 0  # 최대 손실률 (진입가 대비)

    # 중간 눌림 기록: (고점수익률, 눌림폭, 회복여부)
    pullbacks = []
    current_pullback_start = None
    current_pullback_high = None

    # 트레일링 시뮬레이션: 각 폭에서 청산 시점
    trail_stops = {}
    for trail_pct in [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]:
        trail_stops[trail_pct] = {"triggered": False, "minute": None, "gain": None}

    for i, c in enumerate(post_candles):
        high = c["high_price"]
        low = c["low_price"]
        close = c["trade_price"]

        # 고점 갱신
        if high > running_high:
            # 눌림에서 회복
            if current_pullback_start is not None:
                pullback_depth = (current_pullback_high - running_high) / current_pullback_high * 100
                pullbacks.append({
                    "start_minute": current_pullback_start,
                    "end_minute": i,
                    "peak_gain": (current_pullback_high / entry_price - 1) * 100,
                    "pullback_depth": pullback_depth,
                    "recovered": True
                })
                current_pullback_start = None

            running_high = high

        # 현재 수익률
        gain_high = (high / entry_price - 1) * 100
        gain_low = (low / entry_price - 1) * 100

        max_gain = max(max_gain, gain_high)
        max_drawdown = min(max_drawdown, gain_low)

        # 고점 대비 하락 (눌림 시작)
        drop_from_high = (running_high - low) / running_high * 100
        if drop_from_high > 0.1 and current_pullback_start is None:
            current_pullback_start = i
            current_pullback_high = running_high

        # 트레일링 시뮬레이션
        for trail_pct, status in trail_stops.items():
            if not status["triggered"]:
                trail_price = running_high * (1 - trail_pct / 100)
                if low <= trail_price:
                    status["triggered"] = True
                    status["minute"] = i
                    # 트레일링 청산 시점의 수익률 (진입가 대비)
                    status["gain"] = (trail_price / entry_price - 1) * 100

    # 마지막 눌림 (회복 안 됨)
    if current_pullback_start is not None:
        pullback_depth = (running_high - post_candles[-1]["low_price"]) / running_high * 100
        pullbacks.append({
            "start_minute": current_pullback_start,
            "end_minute": len(post_candles) - 1,
            "peak_gain": (running_high / entry_price - 1) * 100,
            "pullback_depth": pullback_depth,
            "recovered": False
        })

    return {
        "max_gain": max_gain,
        "max_drawdown": max_drawdown,
        "final_gain": (post_candles[-1]["trade_price"] / entry_price - 1) * 100,
        "pullbacks": pullbacks,
        "trail_stops": trail_stops,
    }

def main():
    print("=" * 80)
    print("트레일링 분석 v2 - 중간 눌림/회복 패턴")
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
            pullback_cnt = len(result["pullbacks"])
            print(f"  [{tag}] {ticker} {time_str}: 최대+{result['max_gain']:.2f}% "
                  f"눌림{pullback_cnt}회")

    print(f"\n수집 완료: 성공 {len(success_results)}건, 실패 {len(fail_results)}건")

    # === 성공 케이스 눌림 분석 ===
    print("\n" + "=" * 80)
    print("📊 성공 케이스 - 중간 눌림 분석 (회복한 것들)")
    print("=" * 80)

    all_recovered_pullbacks = []
    for r in success_results:
        for pb in r["pullbacks"]:
            if pb["recovered"]:
                all_recovered_pullbacks.append(pb["pullback_depth"])

    if all_recovered_pullbacks:
        print(f"\n회복한 눌림 총 {len(all_recovered_pullbacks)}회")
        print(f"  평균: {statistics.mean(all_recovered_pullbacks):.2f}%")
        print(f"  중앙값: {statistics.median(all_recovered_pullbacks):.2f}%")
        print(f"  최대: {max(all_recovered_pullbacks):.2f}%")

        print("\n[눌림 깊이 분포 - 이후 회복됨]")
        for low, high in [(0, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, 2.0), (2.0, 10)]:
            cnt = sum(1 for v in all_recovered_pullbacks if low <= v < high)
            pct = cnt / len(all_recovered_pullbacks) * 100
            bar = "█" * int(pct / 5)
            print(f"  {low:.1f}%~{high:.1f}%: {cnt:>3}회 ({pct:>4.0f}%) {bar}")

    # === 트레일링 시뮬레이션 결과 ===
    print("\n" + "=" * 80)
    print("🎯 트레일링 폭별 조기 청산 분석 (성공 케이스)")
    print("=" * 80)

    print(f"\n{'트레일링':>8} | {'청산됨':>8} | {'평균수익':>10} | {'수익놓침':>10} | 판정")
    print("-" * 70)

    for trail_pct in [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]:
        triggered_gains = []
        missed_gains = []

        for r in success_results:
            ts = r["trail_stops"][trail_pct]
            if ts["triggered"]:
                triggered_gains.append(ts["gain"])
                missed_gains.append(r["max_gain"] - ts["gain"])

        triggered_cnt = len(triggered_gains)
        triggered_pct = triggered_cnt / len(success_results) * 100

        if triggered_gains:
            avg_gain = statistics.mean(triggered_gains)
            avg_missed = statistics.mean(missed_gains)
        else:
            avg_gain = 0
            avg_missed = 0

        # 판정
        if triggered_pct >= 80:
            verdict = "❌ 너무 타이트"
        elif triggered_pct >= 50:
            verdict = "⚠️  주의"
        elif triggered_pct >= 30:
            verdict = "✅ 적절"
        else:
            verdict = "🔵 느슨"

        print(f"{trail_pct:>7.1f}% | {triggered_cnt:>3}/{len(success_results)} ({triggered_pct:>3.0f}%) | "
              f"{avg_gain:>+8.2f}% | {avg_missed:>+8.2f}% | {verdict}")

    # === 구간별 권장 트레일링 ===
    print("\n" + "=" * 80)
    print("💡 권장 트레일링 설정")
    print("=" * 80)

    # 회복한 눌림의 75백분위 = 이 이상 눌리면 회복 안 될 가능성 높음
    if all_recovered_pullbacks:
        p75 = sorted(all_recovered_pullbacks)[int(len(all_recovered_pullbacks) * 0.75)]
        p90 = sorted(all_recovered_pullbacks)[int(len(all_recovered_pullbacks) * 0.90)]

        print(f"\n성공 케이스 회복한 눌림:")
        print(f"  75백분위: {p75:.2f}% (이 이상 눌리면 25%는 회복 못함)")
        print(f"  90백분위: {p90:.2f}% (이 이상 눌리면 10%는 회복 못함)")

        print(f"\n권장: 트레일링 폭 {p75:.1f}% ~ {p90:.1f}% 사이")
        print(f"  → 대부분의 정상 눌림은 버티고, 진짜 하락은 청산")

if __name__ == "__main__":
    main()
