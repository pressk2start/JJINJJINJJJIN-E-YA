# -*- coding: utf-8 -*-
"""순수 유틸리티 함수 (글로벌 상태 의존 없음)"""
import math
import random
import statistics


def rnd():
    """random.random() 래퍼"""
    return random.random()


def fmt6(x):
    """
    숫자를 보기 좋게 표시:
    - 정수는 소수점 없이
    - 소수점이 있는 경우 최대 6자리까지 표시
    """
    if isinstance(x, (int, float)):
        if abs(x - int(x)) < 1e-6:
            return f"{int(x):,}"
        else:
            s = f"{x:,.6f}".rstrip('0').rstrip('.')
            return s
    return str(x)


def _pct(a, b):
    """두 값의 퍼센트 차이. b=0이면 9.9 반환"""
    try:
        return abs(a / b - 1.0)
    except Exception:
        return 9.9


def _safe_float(x, default=0.0):
    """NaN/inf 방지용 안전 변환"""
    try:
        if x is None:
            return default
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def get_trimmed_mean(slip_deque, default=0.0008):
    """슬립 deque에서 trimmed mean 계산 (상하위 10% 제거)"""
    if len(slip_deque) >= 10:
        sorted_slip = sorted(slip_deque)
        trim_n = max(1, len(sorted_slip) // 10)
        trimmed = sorted_slip[trim_n:-trim_n] if trim_n > 0 else sorted_slip
        return statistics.mean(trimmed) if trimmed else default
    elif len(slip_deque) >= 5:
        return statistics.median(slip_deque)
    return default
