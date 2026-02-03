"""
핵심 함수 단위 테스트
- 센티넬 값 처리 (None/0.0)
- 에러 케이스 (빈 데이터, 데이터 부족)
- 경계값 테스트
"""
import time
import sys
import os

# bot 패키지에서 분리된 함수 임포트
sys.path.insert(0, os.path.dirname(__file__))
from bot.utils import _pct
from bot.indicators import (
    inter_arrival_stats,
    price_band_std,
    micro_tape_stats as micro_tape_stats_from_ticks,
)


# ============================================================
# 헬퍼: 테스트용 틱 데이터 생성
# ============================================================

def _make_ticks(n, base_ts=None, interval_ms=500, base_price=1000.0, ask_bid="BID"):
    """n개의 테스트 틱 생성"""
    if base_ts is None:
        base_ts = int(time.time() * 1000)
    ticks = []
    for i in range(n):
        ticks.append({
            "timestamp": base_ts - i * interval_ms,
            "trade_price": base_price + (n - i) * 0.1,
            "trade_volume": 0.01,
            "ask_bid": ask_bid,
        })
    return ticks


# ============================================================
# 테스트 케이스
# ============================================================

class TestResults:
    passed = 0
    failed = 0
    errors = []


def assert_eq(name, actual, expected):
    if actual == expected:
        TestResults.passed += 1
    else:
        TestResults.failed += 1
        TestResults.errors.append(f"  FAIL {name}: expected {expected}, got {actual}")


def assert_true(name, condition):
    if condition:
        TestResults.passed += 1
    else:
        TestResults.failed += 1
        TestResults.errors.append(f"  FAIL {name}: condition is False")


def assert_none(name, value):
    if value is None:
        TestResults.passed += 1
    else:
        TestResults.failed += 1
        TestResults.errors.append(f"  FAIL {name}: expected None, got {value}")


def assert_not_none(name, value):
    if value is not None:
        TestResults.passed += 1
    else:
        TestResults.failed += 1
        TestResults.errors.append(f"  FAIL {name}: expected not None, got None")


# ---- _pct 테스트 ----

def test_pct_normal():
    """정상 케이스: 10% 차이"""
    result = _pct(110, 100)
    assert_true("_pct(110,100) == 0.1", abs(result - 0.1) < 0.001)

def test_pct_same():
    """동일값"""
    assert_eq("_pct(100,100)", _pct(100, 100), 0.0)

def test_pct_zero_denominator():
    """b=0이면 9.9 반환 (탐지 무력화 방지)"""
    assert_eq("_pct(100,0)", _pct(100, 0), 9.9)

def test_pct_both_zero():
    """둘 다 0이면 9.9"""
    assert_eq("_pct(0,0)", _pct(0, 0), 9.9)


# ---- inter_arrival_stats 테스트 ----

def test_ia_empty():
    """빈 틱 → cv=None"""
    result = inter_arrival_stats([])
    assert_none("ia_empty cv", result["cv"])
    assert_eq("ia_empty count", result["count"], 0)

def test_ia_insufficient():
    """3개 이하 틱 → cv=None"""
    ticks = _make_ticks(3)
    result = inter_arrival_stats(ticks, sec=60)
    assert_none("ia_3ticks cv", result["cv"])
    assert_eq("ia_3ticks count", result["count"], 3)

def test_ia_normal():
    """충분한 틱 → cv는 숫자"""
    ticks = _make_ticks(20, interval_ms=500)
    result = inter_arrival_stats(ticks, sec=60)
    assert_not_none("ia_20ticks cv", result["cv"])
    assert_true("ia_20ticks cv >= 0", result["cv"] >= 0)
    assert_eq("ia_20ticks count", result["count"], 20)

def test_ia_no_timestamp_key():
    """timestamp 키 없는 틱 → 예외 처리 → cv=None"""
    ticks = [{"ts": 1000}, {"ts": 2000}]
    result = inter_arrival_stats(ticks, sec=60)
    assert_none("ia_no_ts cv", result["cv"])


# ---- price_band_std 테스트 ----

def test_pbs_empty():
    """빈 틱 → None"""
    assert_none("pbs_empty", price_band_std([]))

def test_pbs_insufficient():
    """2개 틱 → None"""
    ticks = _make_ticks(2)
    assert_none("pbs_2ticks", price_band_std(ticks, sec=60))

def test_pbs_normal():
    """충분한 틱 → 숫자"""
    ticks = _make_ticks(10)
    result = price_band_std(ticks, sec=60)
    assert_not_none("pbs_10ticks", result)
    assert_true("pbs_10ticks >= 0", result >= 0)

def test_pbs_constant_price():
    """모든 가격 동일 → std = 0"""
    base_ts = int(time.time() * 1000)
    ticks = [{"timestamp": base_ts - i * 500, "trade_price": 1000.0} for i in range(10)]
    result = price_band_std(ticks, sec=60)
    assert_eq("pbs_constant", result, 0.0)


# ---- micro_tape_stats_from_ticks 테스트 ----

def test_mts_empty():
    """빈 틱"""
    result = micro_tape_stats_from_ticks([], 10)
    assert_eq("mts_empty n", result["n"], 0)
    assert_eq("mts_empty age", result["age"], 999)

def test_mts_normal():
    """정상 데이터"""
    ticks = _make_ticks(10, ask_bid="BID")
    result = micro_tape_stats_from_ticks(ticks, 60)
    assert_eq("mts_10 n", result["n"], 10)
    assert_eq("mts_10 buy_ratio", result["buy_ratio"], 1.0)
    assert_true("mts_10 krw > 0", result["krw"] > 0)

def test_mts_mixed_buysell():
    """매수/매도 혼합"""
    base_ts = int(time.time() * 1000)
    ticks = []
    for i in range(10):
        ticks.append({
            "timestamp": base_ts - i * 500,
            "trade_price": 1000.0,
            "trade_volume": 0.01,
            "ask_bid": "BID" if i % 2 == 0 else "ASK",
        })
    result = micro_tape_stats_from_ticks(ticks, 60)
    assert_eq("mts_mixed buy_ratio", result["buy_ratio"], 0.5)

def test_mts_old_ticks_excluded():
    """윈도우 밖 틱 제외"""
    base_ts = int(time.time() * 1000)
    ticks = [
        {"timestamp": base_ts, "trade_price": 1000, "trade_volume": 0.01, "ask_bid": "BID"},
        {"timestamp": base_ts - 1000, "trade_price": 1000, "trade_volume": 0.01, "ask_bid": "BID"},
        {"timestamp": base_ts - 60000, "trade_price": 1000, "trade_volume": 0.01, "ask_bid": "BID"},  # 60초 전
    ]
    result = micro_tape_stats_from_ticks(ticks, 5)  # 5초 윈도우
    assert_eq("mts_window n", result["n"], 2)  # 60초 전 틱 제외


# ---- None 전파 안전성 테스트 ----

def test_cv_none_comparison_safe():
    """cv=None일 때 비교가 TypeError 안 나는지"""
    cv = None
    try:
        # 실제 코드에서 하는 패턴
        result = cv is not None and cv > 6.0
        assert_eq("cv_none_compare", result, False)
    except TypeError:
        TestResults.failed += 1
        TestResults.errors.append("  FAIL cv_none_compare: TypeError raised")

def test_pstd_none_comparison_safe():
    """pstd=None일 때 비교가 TypeError 안 나는지"""
    pstd = None
    try:
        result = pstd is not None and pstd > 0.08
        assert_eq("pstd_none_compare", result, False)
    except TypeError:
        TestResults.failed += 1
        TestResults.errors.append("  FAIL pstd_none_compare: TypeError raised")

def test_pstd_none_multiply_safe():
    """pstd10=None일 때 곱셈 안전성"""
    pstd10 = None
    try:
        pstd_pct = (pstd10 * 100) if pstd10 is not None else None
        assert_none("pstd_none_multiply", pstd_pct)
    except TypeError:
        TestResults.failed += 1
        TestResults.errors.append("  FAIL pstd_none_multiply: TypeError raised")


# ============================================================
# 실행
# ============================================================

def run_all():
    tests = [
        # _pct
        test_pct_normal, test_pct_same, test_pct_zero_denominator, test_pct_both_zero,
        # inter_arrival_stats
        test_ia_empty, test_ia_insufficient, test_ia_normal, test_ia_no_timestamp_key,
        # price_band_std
        test_pbs_empty, test_pbs_insufficient, test_pbs_normal, test_pbs_constant_price,
        # micro_tape_stats
        test_mts_empty, test_mts_normal, test_mts_mixed_buysell, test_mts_old_ticks_excluded,
        # None 안전성
        test_cv_none_comparison_safe, test_pstd_none_comparison_safe, test_pstd_none_multiply_safe,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            TestResults.failed += 1
            TestResults.errors.append(f"  ERROR {t.__name__}: {e}")

    print(f"\n{'='*50}")
    print(f"Tests: {TestResults.passed + TestResults.failed} total, "
          f"{TestResults.passed} passed, {TestResults.failed} failed")
    print(f"{'='*50}")

    if TestResults.errors:
        print("\nFailures:")
        for e in TestResults.errors:
            print(e)
        return 1
    else:
        print("\nAll tests passed!")
        return 0


if __name__ == "__main__":
    sys.exit(run_all())
