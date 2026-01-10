# /analyze_entry_v3.py
# -*- coding: utf-8 -*-
"""
진입 조건 분석 v3 (리팩터링)
- 미래 데이터 누수 방지: 진입 시점 이후 캔들 제외
- Upbit API 호출 안정화: Session/Retry/Rate-limit
- RSI 계산 안정화: Wilder RSI
- 통계/출력 안정화: 빈 데이터 보호
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
import statistics


CASES: List[Tuple[str, str, str, bool]] = [
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
    ("AQT", "2026-01-09", "10:45", True),
    ("BOUNTY", "2026-01-09", "09:46", True),
    # 1/9 추가 실패
    ("AQT", "2026-01-09", "10:48", False),
    ("BOUNTY", "2026-01-09", "11:06", False),
    ("BOUNTY", "2026-01-09", "11:05", False),
    ("BOUNTY", "2026-01-09", "10:46", False),
    ("BOUNTY", "2026-01-09", "10:07", False),
    ("DEEP", "2026-01-09", "12:51", False),
    ("DEEP", "2026-01-09", "13:00", False),
    ("PEPE", "2026-01-09", "13:00", False),
    ("BREV", "2026-01-09", "14:12", False),
]


KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Case:
    ticker: str
    dt_kst: datetime
    is_success: bool

    @staticmethod
    def from_tuple(ticker: str, date_str: str, time_str: str, is_success: bool) -> "Case":
        dt = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=KST)
        return Case(ticker=ticker, dt_kst=dt, is_success=is_success)


@dataclass(frozen=True)
class Features:
    rsi_5m: float
    trend_5m: float
    price_vs_ema20: float
    hour: int


class UpbitClient:
    """
    Minimal Upbit candle client with retry/backoff + rate limiting.
    """

    BASE_URL = "https://api.upbit.com/v1/candles/minutes"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout_sec: float = 10.0,
        min_interval_sec: float = 0.12,
        max_retries: int = 5,
        backoff_base_sec: float = 0.25,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout_sec
        self._min_interval = min_interval_sec
        self._max_retries = max_retries
        self._backoff_base = backoff_base_sec
        self._last_call_ts = 0.0

    def get_candles_minutes(
        self,
        market: str,
        to_time_iso: str,
        unit: int,
        count: int,
    ) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/{unit}"
        params = {"market": f"KRW-{market}", "to": to_time_iso, "count": count}

        self._rate_limit()

        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return data
                    return []

                if resp.status_code in (429, 500, 502, 503, 504):
                    self._sleep_backoff(attempt)
                    continue

                return []
            except requests.RequestException:
                self._sleep_backoff(attempt)

        return []

    def _rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call_ts
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call_ts = time.monotonic()

    def _sleep_backoff(self, attempt: int) -> None:
        time.sleep(self._backoff_base * (2 ** attempt))


def calc_ema(prices: Sequence[float], period: int) -> Optional[float]:
    if period <= 0 or len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calc_rsi_wilder(prices: Sequence[float], period: int = 14) -> Optional[float]:
    """
    Wilder RSI (more stable).
    Requires len(prices) >= period + 1
    """
    if period <= 0 or len(prices) < period + 1:
        return None

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def safe_stats(values: Sequence[float]) -> Optional[Tuple[float, float, float, float]]:
    if not values:
        return None
    return (statistics.mean(values), statistics.median(values), min(values), max(values))


def find_optimal_threshold(
    success_vals: Sequence[float],
    fail_vals: Sequence[float],
    target_success_rate: float = 0.75,
) -> Optional[Tuple[float, float, float]]:
    """
    returns (threshold, fail_pass_rate, success_pass_rate)
    - Higher is "better" direction (>= threshold).
    """
    if not success_vals or not fail_vals:
        return None

    all_vals = sorted(set(success_vals) | set(fail_vals))
    best: Optional[Tuple[float, float, float]] = None

    for thresh in all_vals:
        s_pass = sum(1 for v in success_vals if v >= thresh)
        f_pass = sum(1 for v in fail_vals if v >= thresh)

        s_rate = s_pass / len(success_vals)
        f_rate = f_pass / len(fail_vals)

        if s_rate >= target_success_rate:
            if best is None or f_rate < best[1]:
                best = (thresh, f_rate, s_rate)

    return best


def to_upbit_iso_kst(dt_kst: datetime) -> str:
    # Upbit expects format like "YYYY-MM-DDTHH:MM:SS+09:00"
    return dt_kst.strftime("%Y-%m-%dT%H:%M:%S") + "+09:00"


def parse_upbit_candle_dt_kst(candle: Dict[str, Any]) -> Optional[datetime]:
    """
    candle_date_time_kst example: "2026-01-06T10:10:00"
    """
    s = candle.get("candle_date_time_kst")
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=KST)
    except ValueError:
        return None


def analyze_case(client: UpbitClient, case: Case) -> Optional[Features]:
    """
    미래 데이터 누수 방지:
    - to_time: dt + 1초 (dt 이전 캔들까지)
    - candle_date_time_kst 기준으로 dt 이전 데이터만 사용
    """
    to_time = to_upbit_iso_kst(case.dt_kst + timedelta(seconds=1))

    candles = client.get_candles_minutes(case.ticker, to_time, unit=5, count=60)
    if len(candles) < 25:
        return None

    # Upbit returns newest first; convert to chronological and filter
    candles = list(reversed(candles))
    filtered: List[Dict[str, Any]] = []
    for c in candles:
        cdt = parse_upbit_candle_dt_kst(c)
        if cdt is None:
            continue
        if cdt <= case.dt_kst:
            filtered.append(c)

    if len(filtered) < 25:
        return None

    closes = [float(c["trade_price"]) for c in filtered]
    closes = closes[-30:]  # enough for RSI14 + EMA20 + trend windows

    rsi = calc_rsi_wilder(closes, 14)
    rsi_val = float(rsi) if rsi is not None else 50.0

    if len(closes) >= 10:
        recent_avg = sum(closes[-5:]) / 5
        prev_avg = sum(closes[-10:-5]) / 5
        trend = (recent_avg / prev_avg - 1.0) * 100.0 if prev_avg > 0 else 0.0
    else:
        trend = 0.0

    ema20 = calc_ema(closes, 20)
    price_vs_ema20 = (closes[-1] / ema20 - 1.0) * 100.0 if ema20 else 0.0

    return Features(
        rsi_5m=rsi_val,
        trend_5m=trend,
        price_vs_ema20=price_vs_ema20,
        hour=case.dt_kst.hour,
    )


def print_bucket_distribution(
    title: str,
    s_vals: Sequence[float],
    f_vals: Sequence[float],
    buckets: Sequence[Tuple[float, float]],
    fmt_label: str,
) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

    s_stats = safe_stats(s_vals)
    f_stats = safe_stats(f_vals)

    if s_stats:
        m, med, lo, hi = s_stats
        print(f"성공: 평균 {m:.2f}, 중앙값 {med:.2f}, 최소 {lo:.2f}, 최대 {hi:.2f}")
    else:
        print("성공: 데이터 없음")

    if f_stats:
        m, med, lo, hi = f_stats
        print(f"실패: 평균 {m:.2f}, 중앙값 {med:.2f}, 최소 {lo:.2f}, 최대 {hi:.2f}")
    else:
        print("실패: 데이터 없음")

    if not s_vals or not f_vals:
        return

    print("\n[구간별 분포]")
    for low, high in buckets:
        s_cnt = sum(1 for v in s_vals if low <= v < high)
        f_cnt = sum(1 for v in f_vals if low <= v < high)
        s_pct = s_cnt / len(s_vals) * 100
        f_pct = f_cnt / len(f_vals) * 100
        label = fmt_label.format(low=low, high=high)
        print(f"  {label}: 성공 {s_cnt:>3}건 ({s_pct:>4.0f}%), 실패 {f_cnt:>3}건 ({f_pct:>4.0f}%)")


def main() -> None:
    print("=" * 80)
    print("진입 조건 분석 v3 - 리팩터링(미래데이터 방지/Wilder RSI)")
    print("=" * 80)

    client = UpbitClient(min_interval_sec=0.12)

    print("\n데이터 수집 중...")
    success_data: List[Features] = []
    fail_data: List[Features] = []

    for ticker, date_str, time_str, is_success in CASES:
        case = Case.from_tuple(ticker, date_str, time_str, is_success)
        feats = analyze_case(client, case)
        if feats is None:
            continue

        (success_data if is_success else fail_data).append(feats)
        tag = "성공" if is_success else "실패"
        print(
            f"  [{tag}] {ticker} {case.dt_kst.strftime('%H:%M')}: "
            f"RSI={feats.rsi_5m:.0f} 추세={feats.trend_5m:.2f}% EMA20대비={feats.price_vs_ema20:.2f}%"
        )

    print(f"\n수집 완료: 성공 {len(success_data)}건, 실패 {len(fail_data)}건")
    if not success_data or not fail_data:
        print("데이터가 부족해서 임계치 탐색을 진행할 수 없습니다.")
        return

    s_rsi = [d.rsi_5m for d in success_data]
    f_rsi = [d.rsi_5m for d in fail_data]
    s_trend = [d.trend_5m for d in success_data]
    f_trend = [d.trend_5m for d in fail_data]

    print_bucket_distribution(
        "📊 RSI(5분봉) 분포",
        s_rsi,
        f_rsi,
        buckets=[(0, 50), (50, 60), (60, 70), (70, 80), (80, 101)],
        fmt_label="{low:>2.0f}-{high:<3.0f}",
    )

    print_bucket_distribution(
        "📊 5분봉 추세(%) 분포",
        s_trend,
        f_trend,
        buckets=[(-10, 0), (0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 10)],
        fmt_label="{low:>4.1f}~{high:<4.1f}%",
    )

    print("\n" + "=" * 80)
    print("🎯 최적 임계치 탐색 (목표: 성공 75% 유지)")
    print("=" * 80)

    rsi_opt = find_optimal_threshold(s_rsi, f_rsi, 0.75)
    trend_opt = find_optimal_threshold(s_trend, f_trend, 0.75)

    if rsi_opt:
        thresh, f_rate, s_rate = rsi_opt
        print(f"\nRSI 최적 임계치: >= {thresh:.0f}")
        print(f"  성공 통과 {s_rate*100:.0f}%, 실패 통과 {f_rate*100:.0f}%")
    else:
        print("\nRSI 최적 임계치: 계산 불가")

    if trend_opt:
        thresh, f_rate, s_rate = trend_opt
        print(f"\n추세 최적 임계치: >= {thresh:.2f}%")
        print(f"  성공 통과 {s_rate*100:.0f}%, 실패 통과 {f_rate*100:.0f}%")
    else:
        print("\n추세 최적 임계치: 계산 불가")

    print("\n" + "=" * 80)
    print("🔬 RSI + 추세 조합 테스트")
    print("=" * 80)

    print(f"\n{'RSI':>5} | {'추세':>6} | {'성공통과':>14} | {'실패통과':>14} | {'순효과':>7} | {'Precision':>9}")
    print("-" * 75)

    for rsi_min in [55, 60, 65, 68, 70, 75]:
        for trend_min in [0.3, 0.5, 0.8, 1.0, 1.2]:
            s_pass = sum(1 for d in success_data if d.rsi_5m >= rsi_min and d.trend_5m >= trend_min)
            f_pass = sum(1 for d in fail_data if d.rsi_5m >= rsi_min and d.trend_5m >= trend_min)
            s_rate = s_pass / len(success_data) * 100
            f_rate = f_pass / len(fail_data) * 100
            net = s_rate - f_rate

            # Precision = 성공통과 / (성공통과 + 실패통과)
            total_pass = s_pass + f_pass
            precision = (s_pass / total_pass * 100) if total_pass > 0 else 0

            if s_rate >= 60:  # 성공 60% 이상만 표시
                print(
                    f"{rsi_min:>5} | {trend_min:>5.1f}% | "
                    f"{s_pass:>3}/{len(success_data):<3} ({s_rate:>4.0f}%) | "
                    f"{f_pass:>3}/{len(fail_data):<3} ({f_rate:>4.0f}%) | "
                    f"{net:>+5.0f}% | {precision:>7.1f}%"
                )

    print("\n" + "=" * 80)
    print("💡 권장 설정 (Precision 기준)")
    print("=" * 80)

    best_precision = 0
    best_config: Optional[Tuple[int, float, float, float, float]] = None

    for rsi_min in range(55, 85):
        for trend_i in range(0, 25):
            trend_min = trend_i / 10.0
            s_pass = sum(1 for d in success_data if d.rsi_5m >= rsi_min and d.trend_5m >= trend_min)
            f_pass = sum(1 for d in fail_data if d.rsi_5m >= rsi_min and d.trend_5m >= trend_min)
            s_rate = s_pass / len(success_data) * 100
            f_rate = f_pass / len(fail_data) * 100

            total_pass = s_pass + f_pass
            precision = (s_pass / total_pass * 100) if total_pass > 0 else 0

            # 성공 50% 이상 유지하면서 Precision 최대화
            if s_rate >= 50 and precision > best_precision:
                best_precision = precision
                best_config = (rsi_min, trend_min, s_rate, f_rate, precision)

    if best_config:
        rsi_min, trend_min, s_rate, f_rate, precision = best_config
        print(f"\n최적 조합 (Precision 기준): RSI >= {rsi_min}, 추세 >= {trend_min:.1f}%")
        print(f"  성공 통과: {s_rate:.0f}%")
        print(f"  실패 통과: {f_rate:.0f}%")
        print(f"  Precision: {precision:.1f}% (진입 시 성공 확률)")
    else:
        print("\n조건을 만족하는 조합을 찾지 못했습니다.")

    # 시간대별 분석
    print("\n" + "=" * 80)
    print("🕐 시간대별 성공률")
    print("=" * 80)

    time_buckets = [
        ("아침 (8-10시)", lambda h: 8 <= h < 10),
        ("오전 (10-12시)", lambda h: 10 <= h < 12),
        ("오후 (12-18시)", lambda h: 12 <= h < 18),
        ("저녁 (18-22시)", lambda h: 18 <= h < 22),
        ("밤 (22-08시)", lambda h: h >= 22 or h < 8),
    ]

    for name, cond in time_buckets:
        s_cnt = sum(1 for d in success_data if cond(d.hour))
        f_cnt = sum(1 for d in fail_data if cond(d.hour))
        total = s_cnt + f_cnt
        rate = (s_cnt / total * 100) if total > 0 else 0
        print(f"  {name}: 성공 {s_cnt} / 실패 {f_cnt} = {rate:.0f}%")


if __name__ == "__main__":
    main()
