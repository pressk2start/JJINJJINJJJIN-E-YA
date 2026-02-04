# -*- coding: utf-8 -*-
"""bot 패키지 - 트레이딩 봇 모듈 분리

Modules:
    - config: 설정 상수
    - indicators: 기술지표 함수
    - utils: 유틸리티 함수
    - gate_checks: stage1_gate 개별 검증 함수들
    - ignition_checks: ignition_detected 개별 조건 함수들
    - monitor_checks: monitor_position 개별 체크 함수들
    - signal_detection: detect_leader_stock 단계별 함수들

Main Functions (통합):
    - ignition_detected(): 점화(급등 시작) 감지
    - stage1_gate(): 1단계 게이트 검증
    - detect_leader_stock(): 리더 종목 신호 감지
    - monitor_position(): 포지션 모니터링
"""

import time
import logging
from typing import Tuple, Optional, Dict, Any, List

# 기존 모듈
from bot.config import *
from bot.indicators import *
from bot.utils import *

# 새로 분리된 함수 모듈들
from bot import gate_checks
from bot import ignition_checks
from bot import monitor_checks
from bot import signal_detection


# =============================================================================
# 메인 함수 1: ignition_detected - 점화(급등 시작) 감지
# =============================================================================
def ignition_detected(
    market: str,
    ticks: List[Dict],
    candles: List[Dict],
    ob: Optional[Dict] = None,
    baseline_tps: float = 1.0,
    last_signal_ts: int = 0,
    cooldown_ms: int = 60000,
) -> Tuple[bool, int, str]:
    """점화(급등 시작) 감지

    4가지 조건을 체크하여 점화 여부 판정:
    1. 틱 폭주 (TPS burst)
    2. 연속 매수
    3. 가격 임펄스
    4. 거래량 폭발

    Args:
        market: 마켓 코드
        ticks: 틱 데이터 리스트
        candles: 캔들 데이터 리스트
        ob: 오더북 데이터
        baseline_tps: 평시 틱/초
        last_signal_ts: 마지막 점화 신호 타임스탬프 (ms)
        cooldown_ms: 쿨다운 시간 (ms)

    Returns:
        (is_ignition, score, reason): 점화 여부, 점화 점수 (0~4), 상세 사유
    """
    # 쿨다운 체크
    now_ts = ignition_checks.get_latest_timestamp(ticks)
    cooldown_ok, remaining = ignition_checks.check_ignition_cooldown(
        market, now_ts, last_signal_ts, cooldown_ms
    )
    if not cooldown_ok:
        return False, 0, f"쿨다운중 ({remaining:.0f}초 남음)"

    # 최근 10초 틱 윈도우 추출
    window_ticks = ignition_checks.extract_tick_window(ticks, window_sec=10)
    if len(window_ticks) < 3:
        return False, 0, "틱 데이터 부족"

    tick_count_10s = len(window_ticks)

    # 조건 1: 틱 폭주 (TPS burst)
    tps_burst, tps_threshold = ignition_checks.check_tps_burst(
        tick_count_10s,
        baseline_tps,
        IGN_TPS_MIN_TICKS,
        IGN_TPS_MULTIPLIER
    )

    # 조건 2: 연속 매수
    consec_buys, max_streak = ignition_checks.check_consecutive_buys(
        window_ticks,
        window_sec=10,
        ign_consec_buy_min=IGN_CONSEC_BUY_MIN
    )

    # 조건 3: 가격 임펄스
    prices = ignition_checks.get_prices_from_ticks(window_ticks)
    price_impulse, return_pct, up_count = ignition_checks.check_price_impulse(
        prices,
        IGN_PRICE_IMPULSE_MIN,
        IGN_UP_COUNT_MIN
    )

    # 조건 4: 거래량 폭발
    volume_10s = sum(
        t.get("trade_price", 0) * t.get("trade_volume", 0)
        for t in window_ticks
    )
    avg_candle_vol = (
        sum(c.get("candle_acc_trade_price", 0) for c in candles[-5:]) / 5
        if len(candles) >= 5 else 0
    )
    vol_burst, vol_ratio = ignition_checks.check_volume_burst(
        volume_10s,
        avg_candle_vol,
        IGN_VOL_BURST_RATIO
    )

    # 스프레드 안정성 체크
    spread_ok, spread_val = ignition_checks.check_spread_stability(
        ob, IGN_SPREAD_MAX
    )

    # 점화 점수 계산
    score = ignition_checks.calc_ignition_score(
        tps_burst, consec_buys, price_impulse, vol_burst
    )

    # 점화 여부 판정
    is_ignition = ignition_checks.is_ignition_triggered(score, spread_ok)

    # 상세 사유 생성
    reason = ignition_checks.build_ignition_reason(
        tps_burst, consec_buys, price_impulse, vol_burst,
        spread_ok, tick_count_10s, tps_threshold, return_pct
    )

    return is_ignition, score, reason


# =============================================================================
# 메인 함수 2: stage1_gate - 1단계 게이트 검증
# =============================================================================
def stage1_gate(
    market: str,
    cur_price: float,
    ticks: List[Dict],
    candles: List[Dict],
    ob: Optional[Dict] = None,
    *,
    ignition_score: int = 0,
    ema20_breakout: bool = False,
    high_breakout: bool = False,
    mega: bool = False,
    volume_surge: float = 1.0,
    vol_vs_ma: float = 1.0,
    price_change: float = 0.0,
    turn_pct: float = 0.0,
    buy_ratio: float = 0.5,
    spread: float = 0.0,
    imbalance: float = 0.0,
    accel: float = 1.0,
    consecutive_buys: int = 0,
    pstd: Optional[float] = None,
    cv: Optional[float] = None,
    fresh_ok: bool = True,
    fresh_age: float = 0.0,
) -> Tuple[bool, str, Dict[str, Any]]:
    """1단계 게이트 검증

    진입 신호가 품질 조건을 충족하는지 검증

    Args:
        market: 마켓 코드
        cur_price: 현재가
        ticks: 틱 데이터 리스트
        candles: 캔들 데이터 리스트
        ob: 오더북 데이터
        ignition_score: 점화 점수
        ema20_breakout: EMA20 돌파 여부
        high_breakout: 고점 돌파 여부
        mega: 메가 돌파 여부
        volume_surge: 거래량 급등 배수
        vol_vs_ma: MA 대비 거래량 비율
        price_change: 가격 변동률
        turn_pct: 회전율 (%)
        buy_ratio: 매수비 (0~1)
        spread: 스프레드 (%)
        imbalance: 호가 임밸런스 (-1~1)
        accel: 가속도
        consecutive_buys: 연속 매수 횟수
        pstd: 가격대 표준편차
        cv: 변동계수
        fresh_ok: 틱 신선도 통과 여부
        fresh_age: 틱 나이 (초)

    Returns:
        (pass, reason, metrics): 통과 여부, 실패 사유, 메트릭 정보
    """
    # 후보 경로 결정
    cand_path = gate_checks.determine_candidate_path(
        ignition_score, ema20_breakout, high_breakout
    )
    is_ignition = (ignition_score >= 3)
    breakout_score = int(ema20_breakout) + int(high_breakout)

    # 동적 임계치 계산
    eff_thresholds = gate_checks.calc_effective_thresholds(
        ignition_score=ignition_score,
        cur_price=cur_price,
        gate_surge_min=GATE_SURGE_MIN,
        gate_vol_vs_ma_min=GATE_VOL_VS_MA_MIN,
        gate_price_min=GATE_PRICE_MIN,
        gate_turn_min=GATE_TURN_MIN,
        gate_buy_ratio_min=GATE_BUY_RATIO_MIN,
        gate_imbalance_min=GATE_IMBALANCE_MIN,
        gate_spread_max=GATE_SPREAD_MAX,
        gate_accel_min=GATE_ACCEL_MIN,
        relax_surge_floor=GATE_RELAX_SURGE_FLOOR,
        relax_vol_ma_floor=GATE_RELAX_VOL_MA_FLOOR,
        relax_turn_floor=GATE_RELAX_TURN_FLOOR,
        relax_buy_floor=GATE_RELAX_BUY_FLOOR,
        relax_imb_floor=GATE_RELAX_IMB_FLOOR,
        relax_accel_floor=GATE_RELAX_ACCEL_FLOOR,
        spread_scale_low=SPREAD_SCALE_LOW,
        spread_scale_mid=SPREAD_SCALE_MID,
        spread_scale_high=SPREAD_SCALE_HIGH,
        spread_cap_low=SPREAD_CAP_LOW,
        spread_cap_mid=SPREAD_CAP_MID,
        spread_cap_high=SPREAD_CAP_HIGH,
    )

    # 메트릭 요약 문자열
    metrics_str = gate_checks.build_metrics_summary(
        ignition_score, volume_surge, vol_vs_ma, price_change,
        turn_pct, buy_ratio, spread, imbalance, accel
    )

    # 메트릭 딕셔너리
    metrics = {
        "path": cand_path,
        "ignition_score": ignition_score,
        "volume_surge": volume_surge,
        "vol_vs_ma": vol_vs_ma,
        "price_change": price_change,
        "turn_pct": turn_pct,
        "buy_ratio": buy_ratio,
        "spread": spread,
        "imbalance": imbalance,
        "accel": accel,
        "consecutive_buys": consecutive_buys,
        "pstd": pstd,
        "cv": cv,
        "eff_thresholds": eff_thresholds,
    }

    # === 게이트 검증 시작 ===

    # 1. 틱 신선도
    ok, reason = gate_checks.check_freshness(
        fresh_ok, fresh_age, GATE_FRESH_AGE_MAX, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 2. 거래대금
    current_volume = candles[-1].get("candle_acc_trade_price", 0) if candles else 0
    ok, reason = gate_checks.check_volume(
        current_volume, GATE_VOL_MIN, mega, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 3. 거래량 급등
    ok, reason = gate_checks.check_volume_surge(
        volume_surge, vol_vs_ma,
        eff_thresholds["eff_surge_min"],
        eff_thresholds["eff_vol_vs_ma"],
        mega, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 4. 가격 변동
    ok, reason = gate_checks.check_price_change(
        price_change, eff_thresholds["eff_price_min"], mega, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 5. 회전율 하한
    ok, reason = gate_checks.check_turnover(
        turn_pct, eff_thresholds["eff_turn_min"], metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 6. 과회전 상한
    ok, reason = gate_checks.check_turnover_max(
        turn_pct, market, GATE_TURN_MAX_MAJOR, GATE_TURN_MAX_ALT, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 7. 스프레드
    ok, reason = gate_checks.check_spread(
        spread, eff_thresholds["eff_spread_max"], metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 8. 조건부 스프레드
    ok, reason = gate_checks.check_conditional_spread(
        turn_pct, pstd, spread, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 9. 과열 필터
    ok, reason = gate_checks.check_overheat(
        accel, volume_surge, ignition_score, mega, GATE_OVERHEAT_MAX, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 10. 가속도 하한
    ok, reason = gate_checks.check_accel(
        accel, eff_thresholds["eff_accel_min"], metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 11. 매수비
    ok, reason = gate_checks.check_buy_ratio(
        buy_ratio, eff_thresholds["eff_buy_min"], metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 12. 매수비 스푸핑 체크
    ok, reason = gate_checks.check_buy_ratio_spoofing(buy_ratio, metrics_str)
    if not ok:
        return False, reason, metrics

    # 13. 급등 상한
    ok, reason = gate_checks.check_surge_max(
        volume_surge, GATE_SURGE_MAX, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 14. 호가 임밸런스
    ok, reason = gate_checks.check_imbalance(
        imbalance, eff_thresholds["eff_imb_min"], metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 15. PSTD 체크
    ok, reason = gate_checks.check_pstd(
        pstd, cur_price, ema20_breakout, high_breakout, mega,
        GATE_PSTD_MAX, GATE_PSTD_STRONGBREAK_MAX,
        PSTD_TIER_MULT_LOW, PSTD_TIER_MULT_MID, PSTD_TIER_MULT_HIGH,
        metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 16. 연속매수 품질
    ok, reason = gate_checks.check_consecutive_buys(
        consecutive_buys, is_ignition, GATE_CONSEC_BUY_MIN_QUALITY, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 17. 강돌파 품질 필터
    overheat = accel * volume_surge
    ok, reason = gate_checks.check_strongbreak_quality(
        breakout_score, accel, consecutive_buys, buy_ratio, imbalance,
        turn_pct, cv, overheat,
        GATE_STRONGBREAK_OFF, GATE_STRONGBREAK_ACCEL_MAX,
        GATE_STRONGBREAK_CONSEC_MIN, GATE_STRONGBREAK_TURN_MAX,
        metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 18. 거래량 경로 품질
    ok, reason = gate_checks.check_volume_path_quality(
        cand_path, turn_pct, imbalance, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 19. 진입 신호 조건
    ok, reason = gate_checks.check_entry_signal(
        cand_path, breakout_score, vol_vs_ma, eff_thresholds["eff_vol_vs_ma"],
        ignition_score, ema20_breakout, high_breakout, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 모든 검증 통과
    return True, f"PASS:{cand_path}", metrics


# =============================================================================
# 메인 함수 3: detect_leader_stock - 리더 종목 신호 감지
# =============================================================================
def detect_leader_stock(
    market: str,
    ticks: List[Dict],
    candles: List[Dict],
    ob: Optional[Dict] = None,
    open_positions: Optional[Dict] = None,
    baseline_tps: float = 1.0,
    last_signal_ts: int = 0,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """리더 종목 신호 감지

    진입 가능한 신호를 감지하고 신호 데이터 반환

    Args:
        market: 마켓 코드
        ticks: 틱 데이터 리스트
        candles: 캔들 데이터 리스트
        ob: 오더북 데이터
        open_positions: 열린 포지션 딕셔너리
        baseline_tps: 평시 틱/초
        last_signal_ts: 마지막 신호 타임스탬프

    Returns:
        (has_signal, signal_data, reason): 신호 여부, 신호 데이터, 사유
    """
    open_positions = open_positions or {}

    # 1. 스테이블코인 필터
    is_stable, reason = signal_detection.check_stablecoin(market)
    if is_stable:
        return False, None, reason

    # 2. 포지션 보유 체크
    if signal_detection.check_position_exists(market, open_positions):
        return False, None, f"{market} 이미 포지션 보유"

    # 3. 캔들 데이터 충분성
    if not signal_detection.check_candle_data_sufficient(candles, min_count=3):
        return False, None, "캔들 데이터 부족"

    # 4. 현재가 추출
    cur_price = candles[-1].get("trade_price", 0) if candles else 0
    if cur_price <= 0:
        return False, None, "현재가 없음"

    # 5. 레짐 필터
    regime_ok, regime_reason = signal_detection.check_regime(market, candles, cur_price)
    if not regime_ok:
        return False, None, f"레짐필터: {regime_reason}"
    regime_flat = (regime_reason == "FLAT_SLOPE")

    # 6. 기술적 조건 체크
    ema20_breakout = signal_detection.check_ema20_breakout(cur_price, candles, period=20)
    high_breakout = signal_detection.check_high_breakout(cur_price, candles, lookback=5)

    # 7. 틱 기반 지표 계산
    fresh_ok, fresh_age = signal_detection.check_tick_freshness(ticks, max_age_sec=GATE_FRESH_AGE_MAX)
    buy_ratio = signal_detection.calc_buy_ratio(ticks, window_sec=15)
    volume_surge = signal_detection.calc_volume_surge(ticks, candles, window_sec=10)

    # 8. 캔들 기반 지표
    vol_vs_ma = signal_detection.check_volume_vs_ma(
        candles[-1].get("candle_acc_trade_price", 0) if candles else 0,
        candles, ma_period=20
    )

    # 9. 메가 돌파 체크
    mega_ok = signal_detection.check_mega_breakout(
        candles, MEGA_BREAK_MIN_GAP, MEGA_MIN_1M_CHG, MEGA_VOL_Z, MEGA_ABS_KRW
    )

    # 10. 강한 모멘텀 체크
    strong_momentum = signal_detection.check_strong_momentum(candles, lookback=3)

    # 11. 점화 감지
    ignition_ok, ignition_score, ign_reason = ignition_detected(
        market, ticks, candles, ob, baseline_tps, last_signal_ts
    )

    # 12. 오더북 기반 지표
    spread = ob.get("spread", 0) if ob else 0
    imbalance = ob.get("imbalance", 0) if ob else 0

    # 13. 추가 지표 계산 (간략화)
    price_change = (
        (candles[-1].get("trade_price", 0) / candles[-2].get("trade_price", 1) - 1)
        if len(candles) >= 2 else 0
    )
    turn_pct = candles[-1].get("turnover_pct", 5.0) if candles else 5.0
    accel = 1.0  # 간략화

    # 14. 1단계 게이트 검증
    gate_pass, gate_reason, gate_metrics = stage1_gate(
        market=market,
        cur_price=cur_price,
        ticks=ticks,
        candles=candles,
        ob=ob,
        ignition_score=ignition_score,
        ema20_breakout=ema20_breakout,
        high_breakout=high_breakout,
        mega=mega_ok,
        volume_surge=volume_surge,
        vol_vs_ma=vol_vs_ma,
        price_change=price_change,
        turn_pct=turn_pct,
        buy_ratio=buy_ratio,
        spread=spread,
        imbalance=imbalance,
        accel=accel,
        fresh_ok=fresh_ok,
        fresh_age=fresh_age,
    )

    if not gate_pass:
        return False, None, gate_reason

    # 15. 진입 모드 결정
    entry_mode = signal_detection.determine_entry_mode(
        ignition_ok=ignition_ok,
        mega_ok=mega_ok,
        strong_momentum=strong_momentum,
        regime_flat=regime_flat,
        early_ok=False
    )

    # 16. 신호 태그 결정
    signal_tag = signal_detection.determine_signal_tag(
        ignition_ok=ignition_ok,
        mega_ok=mega_ok,
        ema20_breakout=ema20_breakout,
        high_breakout=high_breakout
    )

    # 17. 신호 데이터 빌드
    signal_data = signal_detection.build_pre_signal(
        market=market,
        cur_price=cur_price,
        entry_mode=entry_mode,
        signal_tag=signal_tag,
        ignition_ok=ignition_ok,
        mega_ok=mega_ok,
        early_ok=False,
        ema20_breakout=ema20_breakout,
        high_breakout=high_breakout,
        volume_surge=volume_surge,
        buy_ratio=buy_ratio,
        imbalance=imbalance,
        spread=spread,
        turn_pct=turn_pct,
        ob=ob,
        ignition_score=ignition_score,
        extra=gate_metrics
    )

    return True, signal_data, f"신호감지: {signal_tag}"


# =============================================================================
# 메인 함수 4: monitor_position - 포지션 모니터링
# =============================================================================
def monitor_position(
    position: Dict[str, Any],
    cur_price: float,
    ticks: List[Dict],
    ob: Optional[Dict] = None,
) -> Tuple[Optional[str], str, Dict[str, Any]]:
    """포지션 모니터링

    포지션의 청산/익절/손절 조건을 체크

    Args:
        position: 포지션 정보 딕셔너리
            - entry_price: 진입가
            - entry_ts: 진입 시점 (Unix timestamp)
            - entry_mode: 진입 모드 (probe/half/confirm)
            - signal_tag: 신호 태그
            - best_price: 최고가
            - worst_price: 최저가
            - base_stop: 손절가
            - trail_armed: 트레일링 무장 여부
            - trail_stop: 트레일 손절가
            - mfe_partial_done: MFE 부분익절 완료 여부
            - plateau_done: Plateau 익절 완료 여부
            - peak_update_count: 고점 갱신 횟수
            - sideways_peak: 횡보 구간 고점
            - is_strong_signal: 강한 신호 여부 (mega/ign)
        cur_price: 현재가
        ticks: 틱 데이터 리스트
        ob: 오더북 데이터

    Returns:
        (action, reason, updated_position): 액션(exit/partial/None), 사유, 업데이트된 포지션
    """
    # 포지션 정보 추출
    entry_price = position.get("entry_price", cur_price)
    entry_ts = position.get("entry_ts", time.time())
    entry_mode = position.get("entry_mode", "half")
    signal_tag = position.get("signal_tag", "기본")
    best_price = position.get("best_price", entry_price)
    worst_price = position.get("worst_price", entry_price)
    base_stop = position.get("base_stop", 0)
    trail_armed = position.get("trail_armed", False)
    trail_stop = position.get("trail_stop", 0)
    mfe_partial_done = position.get("mfe_partial_done", False)
    plateau_done = position.get("plateau_done", False)
    peak_update_count = position.get("peak_update_count", 0)
    sideways_peak = position.get("sideways_peak", entry_price)
    is_strong_signal = position.get("is_strong_signal", False)
    last_peak_ts = position.get("last_peak_ts", entry_ts)

    # 경과 시간
    now = time.time()
    alive_sec = now - entry_ts

    # 현재 수익률, MFE, MAE 계산
    cur_gain = monitor_checks.calc_current_gain(cur_price, entry_price)
    mfe = (best_price / entry_price - 1) if entry_price > 0 else 0
    mae = (worst_price / entry_price - 1) if entry_price > 0 else 0

    # 고점 대비 낙폭
    dd_now = monitor_checks.calc_drawdown_from_peak(cur_price, best_price)

    # 최고가/최저가 갱신
    new_best = max(best_price, cur_price)
    new_worst = min(worst_price, cur_price)
    if new_best > best_price:
        peak_update_count += 1
        last_peak_ts = now

    # 업데이트할 포지션 복사
    updated = position.copy()
    updated["best_price"] = new_best
    updated["worst_price"] = new_worst
    updated["peak_update_count"] = peak_update_count
    updated["last_peak_ts"] = last_peak_ts

    # 동적 손절 비율 (간략화)
    eff_sl_pct = DYN_SL_MAX

    # 오더북 지표
    buy_ratio = ob.get("buy_ratio", 0.5) if ob else 0.5
    imbalance = ob.get("imbalance", 0) if ob else 0

    # 틱 기반 지표 (간략화)
    krw_per_sec = 10000  # 기본값
    has_uptick = cur_price > entry_price

    # === 청산 조건 체크 ===

    # 1. ATR 기반 손절
    should_exit, reason = monitor_checks.check_atr_stop_loss(
        cur_gain, eff_sl_pct, FEE_RATE_ROUNDTRIP
    )
    if should_exit:
        return "exit", reason, updated

    # 2. 본절 손절
    should_exit, reason = monitor_checks.check_base_stop_price(
        cur_price, base_stop, entry_price, eff_sl_pct
    )
    if should_exit:
        return "exit", reason, updated

    # 3. 하드스톱 (급락)
    should_exit, reason = monitor_checks.check_hard_stop_drawdown(
        dd_now, hard_stop_dd=0.02, alive_sec=alive_sec, warmup_sec=10
    )
    if should_exit:
        return "exit", reason, updated

    # 4. Probe 조기 스크래치 (25초)
    should_exit, reason = monitor_checks.check_probe_scratch_phase1(
        alive_sec, mfe, cur_gain, entry_mode
    )
    if should_exit:
        return "exit", reason, updated

    # 5. Probe 스크래치 (60초)
    should_exit, reason = monitor_checks.check_probe_scratch_phase2(
        alive_sec, mfe, cur_gain, entry_mode
    )
    if should_exit:
        return "exit", reason, updated

    # 6. Half/Confirm 스크래치
    should_exit, reason = monitor_checks.check_half_confirm_scratch(
        alive_sec, mfe, cur_gain, entry_mode,
        buy_ratio, krw_per_sec, has_uptick, imbalance, is_strong_signal
    )
    if should_exit:
        return "exit", reason, updated

    # 7. 고점 갱신 없음 타임아웃
    should_exit, reason = monitor_checks.check_no_peak_update_timeout(
        alive_sec, peak_update_count, min_peak_updates=2,
        no_peak_timeout_sec=45, cur_gain=cur_gain
    )
    if should_exit:
        return "exit", reason, updated

    # 8. 횡보 미니트레일
    should_exit, reason, new_sideways_peak = monitor_checks.check_sideways_mini_trail(
        alive_sec, sideways_timeout=120, sideways_peak=sideways_peak,
        cur_price=cur_price, entry_price=entry_price, trail_pct=0.003
    )
    updated["sideways_peak"] = new_sideways_peak
    if should_exit:
        return "exit", reason, updated

    # 9. 트레일링 무장 조건
    new_armed, just_armed = monitor_checks.check_trail_arm_condition(
        cur_price, entry_price, dyn_checkpoint=0.008, trail_armed=trail_armed
    )
    updated["trail_armed"] = new_armed

    # 10. 트레일링 손절
    should_exit, reason, new_trail_stop = monitor_checks.check_trailing_stop(
        new_armed, cur_price, new_best, trail_stop,
        trail_distance_min=0.005, entry_price=entry_price
    )
    updated["trail_stop"] = new_trail_stop
    if should_exit:
        return "exit", reason, updated

    # === 부분 익절 체크 ===

    # 11. MFE 부분익절
    should_partial, reason, _ = monitor_checks.check_mfe_partial_exit(
        mfe, signal_tag, mfe_partial_done, MFE_PARTIAL_TARGETS
    )
    if should_partial:
        updated["mfe_partial_done"] = True
        return "partial", reason, updated

    # 12. Plateau 부분익절
    time_since_peak = now - last_peak_ts
    should_partial, reason = monitor_checks.check_plateau_partial_exit(
        time_since_peak, plateau_timeout=60, mfe=mfe, cur_gain=cur_gain,
        plateau_done=plateau_done, min_mfe_for_plateau=0.004
    )
    if should_partial:
        updated["plateau_done"] = True
        return "partial", reason, updated

    # 13. Probe → Confirm 전환 체크
    should_upgrade, upgrade_reason = monitor_checks.check_probe_to_confirm_upgrade(
        entry_mode, cur_gain, buy_ratio, krw_per_sec, has_uptick, imbalance, mae
    )
    if should_upgrade:
        updated["entry_mode"] = "confirm"
        logging.info(f"[모니터] Probe→Confirm 전환: {upgrade_reason}")

    # 모든 조건 미충족 - 포지션 유지
    return None, "", updated


# =============================================================================
# 모듈 내보내기
# =============================================================================
__all__ = [
    # 메인 함수들
    "ignition_detected",
    "stage1_gate",
    "detect_leader_stock",
    "monitor_position",
    # 서브모듈
    "gate_checks",
    "ignition_checks",
    "monitor_checks",
    "signal_detection",
]
