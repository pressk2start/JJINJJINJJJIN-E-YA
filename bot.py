# -*- coding: utf-8 -*-
# v18e-tune2: G RSI74.55 + 60s조기탈출 + K gap제거 (2026-04-06)
import os, time, math, requests, statistics, traceback, threading, csv, sys, json, random, copy, re, atexit, signal
from datetime import datetime, timedelta, timezone
from collections import deque, OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import *  # 전역 설정값 (config.py)
# 🔧 FIX: _로 시작하는 config 변수는 import * 에서 제외됨 → 명시 import
import config as _cfg
_TICKS_TTL = getattr(_cfg, "_TICKS_TTL", 2.0)
from urllib.parse import urlencode

import uuid
import hashlib
import jwt

# 🔧 WF 데이터 기반 전략 모듈 (bot.py에 인라인 통합)
# strategy_v4 함수들은 아래 "# ============ strategy_v4 통합 ============" 섹션에 정의

# 🔧 PyJWT 패키지 검증 (동명이인 패키지 혼동 방지)
try:
    _jwt_ver = getattr(jwt, "__version__", "unknown")
    assert hasattr(jwt, "encode") and callable(jwt.encode), "jwt.encode 없음"
    print(f"[JWT] PyJWT v{_jwt_ver} 로드됨")
except Exception as e:
    print(f"[JWT_ERR] PyJWT 패키지 문제: {e}")
    print("[JWT_ERR] pip install PyJWT 로 설치 필요")
    sys.exit(1)


def _jitter():
    """FIX [M3]: 스로틀/백오프 지터용 난수 (0.0~1.0)"""
    return random.random()

rnd = _jitter  # 하위호환 alias

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

# =========================
# 설정 (24시간 단타 최적화 + Bot-aware, 3.3.0-sigmoid+vwap+btcRegime+cascadeDecay+rsiDCB+smartTrail+bayesML)
# =========================
# 스캔 파라미터 — config.py에서 정의됨 (TOP_N, SCAN_INTERVAL, COOLDOWN, PARALLEL_WORKERS)

# ==== Exit Control — config.py에서 정의됨 ====
# ★ WARMUP_SEC, HARD_STOP_DD, EXIT_DEBOUNCE_*, DYN_SL_*, PROFIT_CHECKPOINT_*
# ★ _apply_exit_profile()에서 프로파일별 덮어쓰기
# 🔧 FIX: entry/exit 슬립 분리 (TP에서 exit만 정확히 반영)
_ENTRY_SLIP_HISTORY = deque(maxlen=50)  # 진입 슬리피지
_EXIT_SLIP_HISTORY = deque(maxlen=50)   # 청산 슬리피지
# FIX [M4]: _SLIP_HISTORY 제거됨 (읽는 곳 없음, entry/exit 분리로 대체 완료)

def _get_trimmed_mean(slip_deque, default=0.0008):
    """슬립 deque에서 trimmed mean 계산 (상하위 10% 제거)"""
    if len(slip_deque) >= 10:
        sorted_slip = sorted(slip_deque)
        trim_n = max(1, len(sorted_slip) // 10)
        trimmed = sorted_slip[trim_n:-trim_n] if trim_n > 0 else sorted_slip
        return statistics.mean(trimmed) if trimmed else default
    elif len(slip_deque) >= 5:
        return statistics.median(slip_deque)
    return default

def get_dynamic_checkpoint():
    """🔧 체크포인트 = max(비용바닥, PROFIT_CHECKPOINT_BASE)
    비용바닥: 수수료 + 왕복슬립 + 최소알파 (≈0.26%)
    BASE: 0.25% (데이터 최적)
    → 실질 비용을 커버하면서 빠른 trail 무장
    """
    fee = FEE_RATE
    avg_entry_slip = _get_trimmed_mean(_ENTRY_SLIP_HISTORY, 0.0005)
    avg_exit_slip = _get_trimmed_mean(_EXIT_SLIP_HISTORY, 0.0008)
    est_roundtrip_slip = max(0.0005, avg_entry_slip) + max(0.0005, avg_exit_slip)
    # 비용 기반 바닥 = 수수료 + 슬립 + 최소알파
    cost_floor = fee + est_roundtrip_slip + PROFIT_CHECKPOINT_MIN_ALPHA
    # 🔧 FIX: sl_linked 제거 → PROFIT_CHECKPOINT_BASE 직접 사용
    # 기존: sl_linked = DYN_SL_MIN * 0.15 = 0.003 고정 → BASE 변경이 무효화됨
    return max(cost_floor, PROFIT_CHECKPOINT_BASE)

def get_expected_exit_slip_pct():
    """TP 판단용 예상 청산 슬립 (exit만 사용, %)"""
    return _get_trimmed_mean(_EXIT_SLIP_HISTORY, 0.001) * 100.0

# (PROFIT_CHECKPOINT 제거됨 — PROFIT_CHECKPOINT_BASE 직접 사용)

# 차트 최적 청산 — config.py에서 정의됨 (CHART_OPTIMAL_EXIT_SEC)

# SIDEWAYS_TIMEOUT, SCRATCH_TIMEOUT_SEC, SCRATCH_MIN_GAIN 제거 (비활성화됨 — 코드 주석처리 완료)

# MFE 비율 — config.py에서 정의됨 (MFE_RR_MULTIPLIERS)
MFE_PARTIAL_TARGETS = {k: DYN_SL_MIN * v for k, v in MFE_RR_MULTIPLIERS.items()}

def refresh_mfe_targets():
    """DYN_SL_MIN 변경 시 MFE 타겟 재계산"""
    global MFE_PARTIAL_TARGETS
    MFE_PARTIAL_TARGETS = {k: DYN_SL_MIN * v for k, v in MFE_RR_MULTIPLIERS.items()}
# MFE_PARTIAL_RATIO 제거 (미사용 — 실제 비율은 하드코딩됨)
# 스캘프→러너, 트레일링 손절 — config.py에서 정의됨
# (SCALP_TO_RUNNER_*, TRAIL_ATR_MULT, TRAIL_DISTANCE_MIN_BASE)

def get_trail_distance_min():
    """🔧 WF데이터: Trail 0.3% 고정"""
    return TRAIL_DISTANCE_MIN_BASE

# 하위 호환용
# TRAIL_DISTANCE_MIN 제거 (미사용 — 런타임에서 get_trail_distance_min() 사용)

# 수수료 / 하이브리드 모드 — config.py에서 정의됨
# (FEE_RATE_*, USE_5M_CONTEXT, POSTCHECK_ENABLED, EARLY_FLOW_MIN_KRWPSEC)

# --- 환경변수(.env 지원) ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ==== Exit Profile — config.py에서 정의됨 ====
# EXIT_PROFILE, SPIKE_RECOVERY_*, CTX_EXIT_THRESHOLD, EXIT_PROFILES dict


def _apply_exit_profile():
    """
    프로파일별 청산 민감도 세팅
    - gentle  : 느슨(휩쏘 내성↑, 수익 최대화 지향)
    - balanced: 기본값(현재 네 세팅 기준)
    - strict  : 엄격(보수적, 손실축소 지향)
    """
    global WARMUP_SEC, HARD_STOP_DD, EXIT_DEBOUNCE_SEC, EXIT_DEBOUNCE_N
    global TRAIL_ATR_MULT, TRAIL_DISTANCE_MIN_BASE
    global SPIKE_RECOVERY_WINDOW, SPIKE_RECOVERY_MIN_BUY
    global CTX_EXIT_THRESHOLD

    prof = EXIT_PROFILE

    if prof == "gentle":
        WARMUP_SEC = 7
        HARD_STOP_DD = 0.012
        EXIT_DEBOUNCE_SEC = 8
        EXIT_DEBOUNCE_N = 3
        TRAIL_ATR_MULT = 1.2
        TRAIL_DISTANCE_MIN_BASE = 0.003  # 🔧 WF데이터: Trail 0.3% (gentle)
        SPIKE_RECOVERY_WINDOW = 4
        SPIKE_RECOVERY_MIN_BUY = 0.56
        CTX_EXIT_THRESHOLD = 4

    elif prof == "strict":
        WARMUP_SEC = 3
        HARD_STOP_DD = 0.008
        EXIT_DEBOUNCE_SEC = 6
        EXIT_DEBOUNCE_N = 3
        TRAIL_ATR_MULT = 0.90
        TRAIL_DISTANCE_MIN_BASE = 0.002  # 🔧 WF데이터: Trail 0.2% (strict — 15m_눌림반전 기준)
        SPIKE_RECOVERY_WINDOW = 2
        SPIKE_RECOVERY_MIN_BUY = 0.65
        CTX_EXIT_THRESHOLD = 2

    else:  # balanced
        WARMUP_SEC = 5
        HARD_STOP_DD = 0.010
        EXIT_DEBOUNCE_SEC = 10
        EXIT_DEBOUNCE_N = 3
        TRAIL_ATR_MULT = 1.0
        TRAIL_DISTANCE_MIN_BASE = 0.003  # 🔧 WF데이터: Trail 0.3%
        SPIKE_RECOVERY_WINDOW = 3
        SPIKE_RECOVERY_MIN_BUY = 0.58
        CTX_EXIT_THRESHOLD = 3


_apply_exit_profile()

TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN") or ""

# 📌 여러 명에게 보내기용 chat_id 목록
_raw_chats = (
    os.getenv("TG_CHATS")  # 새로 쓸 추천 키: "id1,id2,-100xxx"
    or os.getenv("TELEGRAM_CHAT_ID")  # 예전 단일 키도 호환
    or os.getenv("TG_CHAT") or "")

CHAT_IDS = []
for part in _raw_chats.split(","):
    part = part.strip()
    if not part:
        continue
    try:
        CHAT_IDS.append(int(part))
    except Exception:
        print(f"[WARN] 잘못된 chat_id 값 무시됨: {part}")

if os.getenv("DEBUG_BOT"):
    print("[DEBUG] CHAT_IDS =", CHAT_IDS)

# =========================
# 🔥 점화 감지 (Ignition Detection) 전역 변수
# =========================
_IGNITION_LAST_SIGNAL = {}  # {market: timestamp_ms} 마지막 점화 신호 시각
_IGNITION_BASELINE_TPS = {}  # {market: tps} 종목별 평시 틱/초
_IGNITION_LOCK = threading.Lock()

# =========================
# 🎯 리테스트 진입 모드 — config.py에서 정의됨 (RETEST_*)

# 리테스트 워치리스트: {market: {"peak_price", "peak_ts", "pullback_low", "state", "pre", ...}}
# state: "watching" → "pullback" → "bounce" → "ready"
_RETEST_WATCHLIST = {}
_RETEST_LOCK = threading.Lock()

# (DCB 데드캣바운스 전략 제거됨 — 비활성 상태였으며 코드 정리)

# =========================
# ⭕ 동그라미 엔트리 V1 (Circle Entry - 눌림 재돌파 전용 엔진)
# =========================
# 패턴: Ignition → 1~6봉 첫 눌림 → 리클레임 → 재돌파
# 기존 retest와 독립 운영, 동시 감시 가능
# ⭕ 동그라미 엔트리 — config.py에서 정의됨 (CIRCLE_*)

# 동그라미 워치리스트
# state: "armed" → "pullback" → "reclaim" → "ready"
_CIRCLE_WATCHLIST = {}
_CIRCLE_LOCK = threading.Lock()

# =========================
# 📦 박스권 매매 (Box Range Trading)
# =========================
# 전략: 횡보장에서 박스 하단 매수 → 상단 매도 반복
# 돌파 전략과 독립 운영 (별도 워치리스트 + 모니터)
# 📦 박스권 매매 — config.py에서 정의됨 (BOX_*)

# 박스 워치리스트: { market: { box_high, box_low, ... } }
_BOX_WATCHLIST = {}
_BOX_LOCK = threading.Lock()
_BOX_LAST_EXIT = {}                    # 쿨다운 추적: { market: timestamp }
_BOX_LAST_SCAN_TS = 0                  # 마지막 스캔 시각

# =========================
# 📊 라이브 파이프라인 계측 (Pipeline Instrumentation)
# =========================
# 스캔 사이클마다 누적, 10분마다 텔레그램+콘솔 리포트
_PIPELINE_COUNTERS_LOCK = threading.Lock()
_PIPELINE_COUNTERS = {
    "scan_markets": 0,          # 스캔한 마켓 수
    "c1_ok": 0,                 # 1m 캔들 수집 성공 마켓
    "detect_called": 0,         # detect_leader_stock 호출 수
    "v4_called": 0,             # v4_evaluate_entry 호출 수
    "v4_raw_hit": 0,            # v4 원시 신호 발생 수
    "v4_time_block": 0,         # 시간대 필터 차단
    # -- A 거래량폭발 --
    "vol_burst_enter": 0,
    "vol_burst_vr5_fail": 0,
    "vol_burst_bull_fail": 0,
    "vol_burst_macd_hist_fail": 0,
    "vol_burst_pass": 0,
    # -- B 가격돌파 --
    "breakout_enter": 0,
    "breakout_price_fail": 0,
    "breakout_bull_fail": 0,
    "breakout_vr5_15m_fail": 0,
    "breakout_pass": 0,
    # -- D 고점근접 --
    "near_high_enter": 0,
    "near_high_gap_fail": 0,
    "near_high_over_fail": 0,
    "near_high_bull_fail": 0,
    "near_high_pass": 0,
    # -- C 패턴반전_15m --
    "reversal_15m_enter": 0,
    "reversal_15m_prev_fail": 0,
    "reversal_15m_cur_fail": 0,
    "reversal_15m_recovery_fail": 0,
    "reversal_15m_1m_fail": 0,
    "reversal_15m_vr5_15m_fail": 0,
    "reversal_15m_gap20_fail": 0,
    "reversal_15m_pass": 0,
    # -- H 패턴반전_60m --
    "reversal_60m_enter": 0,
    "reversal_60m_prev_fail": 0,
    "reversal_60m_cur_fail": 0,
    "reversal_60m_recovery_fail": 0,
    "reversal_60m_1m_fail": 0,
    "reversal_60m_gap20_fail": 0,
    "reversal_60m_pass": 0,
    # -- F 추세정배열_15m --
    "ema_align_15m_enter": 0,
    "ema_align_15m_ema_fail": 0,
    "ema_align_15m_1m_fail": 0,
    "ema_align_15m_pass": 0,
    # -- J 추세정배열_60m --
    "ema_align_60m_enter": 0,
    "ema_align_60m_ema_fail": 0,
    "ema_align_60m_1m_fail": 0,
    "ema_align_60m_pass": 0,
    # -- G 모멘텀 --
    "momentum_enter": 0,
    "momentum_rsi5_fail": 0,
    "momentum_1m_fail": 0,
    "momentum_vr5_over_fail": 0,
    "momentum_vr5_15m_fail": 0,
    "momentum_pass": 0,
    # -- L 추세강도 --
    "adx_trend_enter": 0,
    "adx_trend_15_fail": 0,
    "adx_trend_1m_fail": 0,
    "adx_trend_vr5_15m_fail": 0,
    "adx_trend_pass": 0,
    # -- K 역추세반등 --
    "oversold_enter": 0,
    "oversold_rsi_fail": 0,
    "oversold_5m_bull_fail": 0,
    "oversold_5m_prev_fail": 0,
    "oversold_1m_fail": 0,
    "oversold_engulf_fail": 0,
    "oversold_gap20_fail": 0,
    "oversold_pass": 0,
    # -- gate 필터 --
    "gate_fail_stablecoin": 0, # 스테이블코인 제외
    "gate_fail_position": 0,   # 이미 보유
    "gate_fail_no_ticks": 0,   # 틱 없음
    "gate_fail_fake_flow": 0,  # 스푸핑
    "gate_fail_no_v4": 0,      # v4 신호 없음 (총)
    "gate_fail_coin_cd": 0,    # 코인별 연패 쿨다운
    "gate_fail_fresh": 0,      # 틱 신선도 부족
    "gate_fail_spread": 0,     # 스프레드 과다
    "gate_fail_vol_min": 0,    # 거래대금 부족
    "gate_fail_buy_ratio": 0,  # 매수비 부족
    "gate_fail_accel": 0,      # 가속 과다
    "gate_fail_early_flow": 0, # 거래속도 부족
    "gate_pass": 0,            # gate 통과 (pre 반환)
    "cooldown_block": 0,       # 쿨다운 탈락
    "position_block": 0,       # 포지션/recent_alerts 탈락
    "postcheck_block": 0,      # postcheck 탈락
    "lock_block": 0,           # 락 획득 실패
    "suspend_block": 0,        # 연패 중지
    "send_attempt": 0,         # 최종 진입 시도 수
    "send_success": 0,         # 진입 성공 수
}
_PIPELINE_LAST_REPORT_TS = 0
_PIPELINE_REPORT_INTERVAL = 600  # 10분
_PIPELINE_START_TS = time.time()  # 누적 계측 시작 시각

# ======================================================================
# 📊 확장 파이프라인 계측 — delta, conversion, 코인별, 시간대별, 레이턴시
# ======================================================================
_PIPELINE_PREV_SNAPSHOT = {}          # 이전 리포트 시점 카운터 스냅샷 (delta 계산용)
_PIPELINE_PREV_SNAPSHOT_TS = time.time()

# 코인별 탈락 히트맵: {market: {reason: count}}
_PIPELINE_COIN_HITS = {}
_PIPELINE_COIN_HITS_LOCK = threading.Lock()

# 전략별 통과 추적: {"거래량3배": count, "20봉_고점돌파": count}
_PIPELINE_STRATEGY_PASS = {"거래량3배": 0, "20봉_고점돌파": 0}
_PIPELINE_STRATEGY_LOCK = threading.Lock()

# 시간대별 신호 분포 (0~23시)
_PIPELINE_HOURLY_SIGNALS = [0] * 24    # raw_hit
_PIPELINE_HOURLY_GATE_PASS = [0] * 24  # gate_pass
_PIPELINE_HOURLY_SUCCESS = [0] * 24    # send_success
_PIPELINE_HOURLY_LOCK = threading.Lock()

# 스캔 사이클 레이턴시 추적
_PIPELINE_SCAN_LATENCIES = deque(maxlen=200)  # 최근 200 사이클 (ms)
_PIPELINE_SCAN_LAT_LOCK = threading.Lock()

# 마켓별 스캔 간격 추적 — 한 코인이 얼마나 자주 재스캔되는지
# (사이클 80s ÷ 마켓수가 아니라, 같은 코인이 재방문되는 실 간격)
_MARKET_LAST_SCAN_TS = {}  # market -> last detect_called ts
_MARKET_SCAN_INTERVALS = deque(maxlen=2000)  # 최근 2000건 (ms)
_MARKET_SCAN_LOCK = threading.Lock()

# 단계별 레이턴시 추적 — 사이클 내 각 단계의 elapsed 독립 측정
# (전체 사이클 레이턴시가 이상치로 오염돼도 단계별로 어디가 병목인지 파악 가능)
# scan은 fetch(네트워크 I/O)와 detect(CPU 루프)로 분리, report는 full/mini 분리
_PIPELINE_STAGE_LATENCIES = {
    "health_check": deque(maxlen=200),
    "save_state":   deque(maxlen=200),
    "shadow_eval":  deque(maxlen=200),
    "tg_flush":     deque(maxlen=200),
    "report_full":  deque(maxlen=200),  # _pipeline_report (10분 텔레그램)
    "report_mini":  deque(maxlen=200),  # _pipeline_mini_report (1분 콘솔)
    "scan_fetch":   deque(maxlen=200),  # orderbook + candles 네트워크 fetch
    "scan_detect":  deque(maxlen=200),  # detect_leader_stock 루프 + 진입 판정
    # detect 루프 내부 세분화 (마켓별 누적 → 사이클 단위로 합산되어 측정)
    "detect_leader": deque(maxlen=200),  # detect_leader_stock 한 사이클 누적 시간
    "universal_ind": deque(maxlen=200),  # _collect_universal_indicators 한 사이클 누적
    "detect_v4":     deque(maxlen=200),  # v4_evaluate_entry 누적 (universal_ind + check_fn 포함)
    # 참고: timeframe별 fetch는 _TAGGED_FETCH_HISTORY(call-site tag 기반)로 측정
    # → get_minutes_candles 함수 레벨 wrap + thread-local tag → 모든 호출 경로 추적
}
_PIPELINE_STAGE_LOCK = threading.Lock()
_PIPELINE_LATENCY_SANITY_CAP_MS = 600000  # 10분 초과는 이상치로 버림 (호스트 hibernation 등)

# check_fn 캐시 hit/miss 직접 계측 — per-function 분리
# 캐시가 실제로 작동하는지 간접 증거(momentum_enter 카운터 비율)와 별개로 직접 검증
_CHECK_FN_CACHE_HITS = {}   # fn_name -> hit count
_CHECK_FN_CACHE_MISSES = {}  # fn_name -> miss count
_CHECK_FN_CACHE_LOCK = threading.Lock()

# check_fn 실행 시간 직접 계측 — 진짜 병목 함수 특정용
# 캐시 hit 시점은 측정 안 함 (조회 자체는 O(1)); miss 시점만 실제 실행 시간 측정
# eval_all_mode 재실행 시간도 합산 (전체 실행 비용 = 1차 호출 + 재호출)
_CHECK_FN_EXEC_TOTAL_MS = {}  # fn_name -> total accumulated ms
_CHECK_FN_EXEC_CALLS = {}     # fn_name -> miss call count (실제 실행 횟수)
_CHECK_FN_EXEC_LOCK = threading.Lock()

# 사이클 단위 detect 루프 내부 세분화 측정용 누적기
# - detect_leader_stock: 480회 호출 누적 → 사이클당 1회 record_stage
# - universal_ind: _collect_universal_indicators 469회 호출 누적
# - detect_v4: v4_evaluate_entry 호출 누적
_CYCLE_DETECT_LEADER_MS = 0.0
_CYCLE_UNIVERSAL_IND_MS = 0.0
_CYCLE_DETECT_V4_MS = 0.0
_CYCLE_TIMING_LOCK = threading.Lock()

# call-site tag 기반 fetch 측정 (thread-local tag + 함수 레벨 wrap)
# get_minutes_candles(tf, ...) 호출 시점에 현재 tag를 읽어 key=f"c{tf}_{tag}"로 분류
# → 모든 호출 경로 자동 추적 (detect_leader / orphan / box / circle / other 등)
_FETCH_TAG_LOCAL = threading.local()
_CYCLE_FETCH_BY_TAG = {}  # key -> [ms_sum, calls]
_TAGGED_FETCH_HISTORY = {}  # key -> deque(maxlen=200) of (ms, calls)
_TAGGED_FETCH_LOCK = threading.Lock()


class _FetchTagContext:
    """with _fetch_tag('detect_leader'): ... 블록 안의 get_minutes_candles 호출은 'detect_leader' tag로 분류"""
    def __init__(self, tag):
        self.tag = tag
        self.prev = None
    def __enter__(self):
        self.prev = getattr(_FETCH_TAG_LOCAL, 'tag', None)
        _FETCH_TAG_LOCAL.tag = self.tag
        return self
    def __exit__(self, *args):
        _FETCH_TAG_LOCAL.tag = self.prev


def _fetch_tag(tag):
    return _FetchTagContext(tag)


def _record_tagged_fetch(tf, elapsed_ms):
    """get_minutes_candles 내부에서 호출 — 현재 thread-local tag + timeframe으로 누적"""
    if elapsed_ms < 0 or elapsed_ms > _PIPELINE_LATENCY_SANITY_CAP_MS:
        return
    tag = getattr(_FETCH_TAG_LOCAL, 'tag', None) or 'other'
    key = f"c{tf}_{tag}"
    with _TAGGED_FETCH_LOCK:
        cur = _CYCLE_FETCH_BY_TAG.get(key)
        if cur is None:
            cur = [0.0, 0]
            _CYCLE_FETCH_BY_TAG[key] = cur
        cur[0] += elapsed_ms
        cur[1] += 1


def _add_cycle_detect_leader_ms(ms):
    """detect_leader_stock 한 번 호출 시간 누적"""
    global _CYCLE_DETECT_LEADER_MS
    with _CYCLE_TIMING_LOCK:
        _CYCLE_DETECT_LEADER_MS += ms


def _add_cycle_universal_ind_ms(ms):
    """_collect_universal_indicators 한 번 호출 시간 누적"""
    global _CYCLE_UNIVERSAL_IND_MS
    with _CYCLE_TIMING_LOCK:
        _CYCLE_UNIVERSAL_IND_MS += ms


def _add_cycle_detect_v4_ms(ms):
    """v4_evaluate_entry 호출 누적"""
    global _CYCLE_DETECT_V4_MS
    with _CYCLE_TIMING_LOCK:
        _CYCLE_DETECT_V4_MS += ms


def _flush_cycle_internal_timing():
    """사이클 끝에서 호출 — 누적값을 stage 측정으로 flush + 리셋"""
    global _CYCLE_DETECT_LEADER_MS, _CYCLE_UNIVERSAL_IND_MS, _CYCLE_DETECT_V4_MS
    with _CYCLE_TIMING_LOCK:
        _dl = _CYCLE_DETECT_LEADER_MS
        _ui = _CYCLE_UNIVERSAL_IND_MS
        _dv = _CYCLE_DETECT_V4_MS
        _CYCLE_DETECT_LEADER_MS = 0.0
        _CYCLE_UNIVERSAL_IND_MS = 0.0
        _CYCLE_DETECT_V4_MS = 0.0
    if _dl > 0:
        _pipeline_record_stage("detect_leader", _dl)
    if _ui > 0:
        _pipeline_record_stage("universal_ind", _ui)
    if _dv > 0:
        _pipeline_record_stage("detect_v4", _dv)
    # tag 기반 fetch 누적 flush
    with _TAGGED_FETCH_LOCK:
        tag_snap = {k: list(v) for k, v in _CYCLE_FETCH_BY_TAG.items()}
        _CYCLE_FETCH_BY_TAG.clear()
    for _key, (_ms, _calls) in tag_snap.items():
        if _ms > 0 or _calls > 0:
            if _key not in _TAGGED_FETCH_HISTORY:
                _TAGGED_FETCH_HISTORY[_key] = deque(maxlen=200)
            _TAGGED_FETCH_HISTORY[_key].append((_ms, _calls))

# gate 통과 시 핵심 지표 스냅샷 (품질 분석용)
_PIPELINE_PASS_METRICS = deque(maxlen=100)  # 최근 100개 통과 신호의 지표
_PIPELINE_PASS_METRICS_LOCK = threading.Lock()

# 근접 탈락 (near-miss) 추적: gate 직전 단계에서 탈락한 건
_PIPELINE_NEAR_MISS = deque(maxlen=50)
_PIPELINE_NEAR_MISS_LOCK = threading.Lock()


def _pipeline_coin_hit(market, reason):
    """코인별 탈락 사유 기록"""
    coin = market.split("-")[-1] if "-" in market else market
    with _PIPELINE_COIN_HITS_LOCK:
        if coin not in _PIPELINE_COIN_HITS:
            # 🔧 FIX: 무한 증가 방지 — 최대 120개 코인만 추적
            if len(_PIPELINE_COIN_HITS) >= 120:
                _PIPELINE_COIN_HITS.clear()
            _PIPELINE_COIN_HITS[coin] = {}
        _PIPELINE_COIN_HITS[coin][reason] = _PIPELINE_COIN_HITS[coin].get(reason, 0) + 1


def _pipeline_strategy_pass(strategy_name):
    """전략별 통과 카운트 기록"""
    with _PIPELINE_STRATEGY_LOCK:
        _PIPELINE_STRATEGY_PASS[strategy_name] = _PIPELINE_STRATEGY_PASS.get(strategy_name, 0) + 1


def _pipeline_hourly_inc(stage):
    """시간대별 신호 카운트 증가 (stage: 'raw_hit' | 'gate_pass' | 'success')"""
    try:
        h = datetime.now(timezone(timedelta(hours=9))).hour
    except Exception:
        h = 0
    with _PIPELINE_HOURLY_LOCK:
        if stage == "raw_hit":
            _PIPELINE_HOURLY_SIGNALS[h] += 1
        elif stage == "gate_pass":
            _PIPELINE_HOURLY_GATE_PASS[h] += 1
        elif stage == "success":
            _PIPELINE_HOURLY_SUCCESS[h] += 1


def _pipeline_record_scan_latency(elapsed_ms):
    """스캔 사이클 소요시간 기록 (ms) — sanity cap 적용"""
    # 이상치 방어: 10분 초과는 host hibernation/clock jump 가능성 → 버림
    if elapsed_ms < 0 or elapsed_ms > _PIPELINE_LATENCY_SANITY_CAP_MS:
        return
    with _PIPELINE_SCAN_LAT_LOCK:
        _PIPELINE_SCAN_LATENCIES.append(elapsed_ms)


def _pipeline_record_stage(name, elapsed_ms):
    """단계별 레이턴시 기록 — sanity cap 적용"""
    if elapsed_ms < 0 or elapsed_ms > _PIPELINE_LATENCY_SANITY_CAP_MS:
        return
    with _PIPELINE_STAGE_LOCK:
        d = _PIPELINE_STAGE_LATENCIES.get(name)
        if d is not None:
            d.append(elapsed_ms)


def _pipeline_record_market_scan(market):
    """마켓별 재스캔 간격 기록 (ms) — 같은 코인이 다음에 스캔되기까지 걸리는 시간"""
    now = time.time()
    with _MARKET_SCAN_LOCK:
        prev = _MARKET_LAST_SCAN_TS.get(market)
        _MARKET_LAST_SCAN_TS[market] = now
        if prev is not None:
            delta_ms = (now - prev) * 1000
            # 10분 초과 갭은 무시 (재시작/일시정지 보정)
            if 0 < delta_ms < 600000:
                _MARKET_SCAN_INTERVALS.append(delta_ms)


def _pipeline_record_pass_metrics(market, metrics_dict):
    """gate 통과 시 핵심 지표 스냅샷 저장"""
    with _PIPELINE_PASS_METRICS_LOCK:
        _PIPELINE_PASS_METRICS.append({
            "ts": time.time(),
            "market": market,
            **metrics_dict
        })


def _pipeline_record_near_miss(market, reason, metrics_str):
    """gate 직전 근접 탈락 기록"""
    with _PIPELINE_NEAR_MISS_LOCK:
        _PIPELINE_NEAR_MISS.append({
            "ts": time.time(),
            "market": market,
            "reason": reason,
            "metrics": metrics_str
        })


# ======================================================================
# 📈 값 분포 추적기 — "얼마나 아깝게 탈락했는지" 실측값 기록
# ======================================================================
_PIPELINE_VALUE_TRACKER_LOCK = threading.Lock()
_PIPELINE_VALUE_TRACKER = {
    # 각 키: {"values": deque(maxlen=500), "near_miss": count (기준 근처 탈락)}
    "m3_pct":       {"values": deque(maxlen=500), "threshold": 0.0, "near_miss": 0},
    "vr5":          {"values": deque(maxlen=500), "threshold": 2.5, "near_miss": 0},
    "atr_pct":      {"values": deque(maxlen=500), "threshold": 0.5, "near_miss": 0},
    "adx_15_vol3x": {"values": deque(maxlen=500), "threshold": 20.0, "near_miss": 0},
    "adx_15_20bar": {"values": deque(maxlen=500), "threshold": 20.0, "near_miss": 0},
    "20bar_gap_pct": {"values": deque(maxlen=500), "threshold": 0.0, "near_miss": 0},
    "gate_spread":  {"values": deque(maxlen=500), "threshold": 0.0, "near_miss": 0},
    "gate_buy_ratio": {"values": deque(maxlen=500), "threshold": 0.0, "near_miss": 0},
    "gate_vol_krw": {"values": deque(maxlen=500), "threshold": 0.0, "near_miss": 0},
    "gate_accel":   {"values": deque(maxlen=500), "threshold": 0.0, "near_miss": 0},
    "gate_flow_kps": {"values": deque(maxlen=500), "threshold": 0.0, "near_miss": 0},
}

# 시그널 발생 종목 기록 (최근 50개)
_PIPELINE_SIGNAL_COINS = deque(maxlen=50)
_PIPELINE_SIGNAL_COINS_LOCK = threading.Lock()


def _pipeline_track_value(key, value, market=None, passed=False):
    """지표 실측값 기록 + 근접 탈락(near-miss) 자동 판정

    near-miss: 탈락했지만 기준값과 20% 이내 차이인 경우
    """
    if value is None:
        return
    with _PIPELINE_VALUE_TRACKER_LOCK:
        tracker = _PIPELINE_VALUE_TRACKER.get(key)
        if not tracker:
            return
        tracker["values"].append(value)
        thr = tracker["threshold"]
        if not passed and thr != 0:
            # 기준 대비 20% 이내면 near-miss
            margin = abs(thr) * 0.2
            if abs(value - thr) <= margin:
                tracker["near_miss"] += 1
                # 근접 탈락 상세 기록
                _pipeline_record_near_miss(
                    market or "?", key,
                    f"{key}={value:.4f} thr={thr:.4f} gap={value-thr:+.4f}")


def _pipeline_record_signal_coin(market, strategy):
    """시그널 발생 종목 기록"""
    with _PIPELINE_SIGNAL_COINS_LOCK:
        _PIPELINE_SIGNAL_COINS.append({
            "ts": time.time(),
            "market": market,
            "strategy": strategy
        })


def _pipeline_value_summary():
    """값 분포 통계 요약 반환 (리포트용)"""
    result = {}
    with _PIPELINE_VALUE_TRACKER_LOCK:
        for key, tracker in _PIPELINE_VALUE_TRACKER.items():
            vals = list(tracker["values"])
            if not vals:
                result[key] = {"max": 0, "avg": 0, "min": 0, "n": 0,
                               "near_miss": tracker["near_miss"], "thr": tracker["threshold"]}
                continue
            result[key] = {
                "max": max(vals),
                "avg": sum(vals) / len(vals),
                "min": min(vals),
                "n": len(vals),
                "near_miss": tracker["near_miss"],
                "thr": tracker["threshold"],
            }
    return result


def _pipeline_gauge_csv_write():
    """pipeline_gauge.csv에 현재 값 분포 + 카운터 스냅샷 1줄 기록"""
    gauge_path = os.path.join(os.getcwd(), "pipeline_gauge.csv")
    try:
        vs = _pipeline_value_summary()
        with _PIPELINE_COUNTERS_LOCK:
            c = dict(_PIPELINE_COUNTERS)
        write_header = not os.path.exists(gauge_path)
        with open(gauge_path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                header = ["timestamp",
                          "v4_called", "v4_raw_hit", "gate_pass", "send_success",
                          "m3_avg", "m3_max", "m3_nm",
                          "vr5_avg", "vr5_max", "vr5_nm",
                          "atr_avg", "atr_max", "atr_nm",
                          "adx_vol3x_avg", "adx_vol3x_max", "adx_vol3x_nm",
                          "adx_20bar_avg", "adx_20bar_max", "adx_20bar_nm",
                          "20bar_gap_avg", "20bar_gap_max", "20bar_gap_nm",
                          "spread_avg", "buy_ratio_avg", "vol_krw_avg",
                          "accel_avg", "flow_kps_avg"]
                w.writerow(header)
            def _g(k, f_name):
                return round(vs.get(k, {}).get(f_name, 0), 6)
            w.writerow([
                datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S"),
                c.get("v4_called", 0), c.get("v4_raw_hit", 0),
                c.get("gate_pass", 0), c.get("send_success", 0),
                _g("m3_pct", "avg"), _g("m3_pct", "max"), _g("m3_pct", "near_miss"),
                _g("vr5", "avg"), _g("vr5", "max"), _g("vr5", "near_miss"),
                _g("atr_pct", "avg"), _g("atr_pct", "max"), _g("atr_pct", "near_miss"),
                _g("adx_15_vol3x", "avg"), _g("adx_15_vol3x", "max"), _g("adx_15_vol3x", "near_miss"),
                _g("adx_15_20bar", "avg"), _g("adx_15_20bar", "max"), _g("adx_15_20bar", "near_miss"),
                _g("20bar_gap_pct", "avg"), _g("20bar_gap_pct", "max"), _g("20bar_gap_pct", "near_miss"),
                _g("gate_spread", "avg"), _g("gate_buy_ratio", "avg"), _g("gate_vol_krw", "avg"),
                _g("gate_accel", "avg"), _g("gate_flow_kps", "avg"),
            ])
    except Exception as e:
        print(f"[GAUGE_CSV_ERR] {e}")

# 섀도우 모드 CSV 로깅 (raw signal별 한 줄)
_SHADOW_LOG_PATH = os.path.join(os.getcwd(), "pipeline_shadow.csv")
_SHADOW_LOG_LOCK = threading.Lock()
_SHADOW_LOG_INITIALIZED = False


_BLOCKED_THREAD_LOCAL = threading.local()  # 차단 건 가상 추적용 thread-local


def _pipeline_inc(key, n=1, value=None, threshold=None, direction=None):
    """파이프라인 카운터 증가 + 차단 필터명/값/임계치 thread-local 기록.
    Returns True(=caller should return None) in normal mode,
    False(=caller should continue) in eval_all mode.
    eval_all mode: 카운터 미증가, all_fails 리스트에 누적 → 실패 필터 전수 추적"""
    _eval_all = getattr(_BLOCKED_THREAD_LOCAL, '_eval_all_mode', False)
    if not _eval_all:
        with _PIPELINE_COUNTERS_LOCK:
            _PIPELINE_COUNTERS[key] = _PIPELINE_COUNTERS.get(key, 0) + n
    if key.endswith("_fail"):
        _BLOCKED_THREAD_LOCAL.last_fail = key
        _BLOCKED_THREAD_LOCAL.last_fail_value = value
        _BLOCKED_THREAD_LOCAL.last_fail_threshold = threshold
        _BLOCKED_THREAD_LOCAL.last_fail_direction = direction
        # v0: eval_all 시 모든 실패 필터를 리스트에 누적
        if _eval_all:
            if not hasattr(_BLOCKED_THREAD_LOCAL, 'all_fails'):
                _BLOCKED_THREAD_LOCAL.all_fails = []
            _BLOCKED_THREAD_LOCAL.all_fails.append({
                "filter": key, "value": value,
                "threshold": threshold, "direction": direction,
            })
    # eval_all에서도 데이터검증(value=None)은 반드시 return해야 크래시 방지
    if _eval_all and value is None:
        return True
    return not _eval_all


def _pipeline_report(force=False):
    """10분마다 파이프라인 카운터 리포트 전송 (확장판: delta, 전환율, 값분포, 니어미스, Top-N)"""
    global _PIPELINE_LAST_REPORT_TS, _PIPELINE_PREV_SNAPSHOT, _PIPELINE_PREV_SNAPSHOT_TS
    now = time.time()
    if not force and (now - _PIPELINE_LAST_REPORT_TS) < _PIPELINE_REPORT_INTERVAL:
        return
    _PIPELINE_LAST_REPORT_TS = now
    with _PIPELINE_COUNTERS_LOCK:
        c = dict(_PIPELINE_COUNTERS)
    elapsed_min = (now - _PIPELINE_START_TS) / 60
    # v15: 첫 리포트에 데이터가 전혀 없으면 "수집 중" 한 줄만 보내고 스킵
    if c.get("scan_markets", 0) == 0 and c.get("detect_called", 0) == 0:
        tg_send("📊 파이프라인 계측: 데이터 수집 중... (다음 리포트부터 표시)")
        return
    delta_min = (now - _PIPELINE_PREV_SNAPSHOT_TS) / 60 if _PIPELINE_PREV_SNAPSHOT else elapsed_min

    # delta 계산 (이번 구간 변화량)
    prev = _PIPELINE_PREV_SNAPSHOT or {}
    def d(key):
        return c.get(key, 0) - prev.get(key, 0)
    def pct(num, denom):
        return f"{num/max(denom,1)*100:.1f}%" if denom else "N/A"

    # 전환율 계산
    _v4 = c["v4_called"]
    _raw = c["v4_raw_hit"]
    _gp = c["gate_pass"]
    _succ = c["send_success"]
    _det = c["detect_called"]

    lines = [
        f"📊 <b>파이프라인 계측 (누적 {elapsed_min:.0f}분 | Δ{delta_min:.0f}분)</b>",
        f"━━━━━━━━━━━━━━━━",
        f"🔍 스캔: {c['scan_markets']}마켓 | c1성공: {c['c1_ok']}",
        f"🔬 detect: {_det}(Δ{d('detect_called')})",
        f"📡 v4: {_v4}(Δ{d('v4_called')})",
        f"🎯 raw_hit: {_raw}(Δ{d('v4_raw_hit')}) | 시간차단: {c.get('v4_time_block',0)}",
        f"━ v0 전략 세부 ━",
        f"  [A거래량] ⛔비활성(v18e: 944건27%-0.05%)",
        f"  [B돌파] 종가돌파(20봉고점)+양봉+15mVR≧1.5 [SL1.0/90바]",
        f"    진입{c.get('breakout_enter',0)}"
        f" → 종가(20봉고점미달):{c.get('breakout_price_fail',0)}"
        f" 음봉:{c.get('breakout_bull_fail',0)}"
        f" 15mVR(1.5미만):{c.get('breakout_vr5_15m_fail',0)}"
        f" ✅통과:{c.get('breakout_pass',0)}",
        f"  [C반전15] 15m음→양+종가회복+1m양봉+15mVR≧1.0+고점근접 [SL0.4/빠른익절]",
        f"    진입{c.get('reversal_15m_enter',0)}"
        f" → 전봉양봉:{c.get('reversal_15m_prev_fail',0)}"
        f" 현봉음봉:{c.get('reversal_15m_cur_fail',0)}"
        f" 종가(전봉시가미달):{c.get('reversal_15m_recovery_fail',0)}"
        f" 회복약({c.get('reversal_15m_recovery_weak_fail',0)})"
        f" 1m음봉:{c.get('reversal_15m_1m_fail',0)}"
        f" 15mVR(1.0미만):{c.get('reversal_15m_vr5_15m_fail',0)}"
        f" 고점이격(1.5%초과):{c.get('reversal_15m_gap20_fail',0)}"
        f" ✅통과:{c.get('reversal_15m_pass',0)}",
        f"  [H반전60] 60m음→양+종가회복+1m양봉 [90바]",
        f"    진입{c.get('reversal_60m_enter',0)}"
        f" → 전봉양봉:{c.get('reversal_60m_prev_fail',0)}"
        f" 현봉음봉:{c.get('reversal_60m_cur_fail',0)}"
        f" 종가(전봉시가미달):{c.get('reversal_60m_recovery_fail',0)}"
        f" 1m음봉:{c.get('reversal_60m_1m_fail',0)}"
        f" ✅통과:{c.get('reversal_60m_pass',0)}",
        f"  [FEMA15] ⛔비활성(v18e: 1905건27%-0.10%)",
        f"  [G모멘텀] 5mRSI≧73.0+1m양봉+VR5≦3.2+15mVR≧2.0 [G-v2:SL1.0/minH120s/max180s]",
        f"    진입{c.get('momentum_enter',0)}"
        f" → 5mRSI(73.0미만):{c.get('momentum_rsi5_fail',0)}"
        f" 1m음봉:{c.get('momentum_1m_fail',0)}"
        f" VR5과열(3.2초과):{c.get('momentum_vr5_over_fail',0)}"
        f" 15mVR(2.0미만):{c.get('momentum_vr5_15m_fail',0)}"
        f" ✅통과:{c.get('momentum_pass',0)}",
        f"  [LADX] 15mADX≧28.5+1m양봉+15mVR≧0.8 [90바]",
        f"    진입{c.get('adx_trend_enter',0)}"
        f" → 15mADX(28.5미만):{c.get('adx_trend_15_fail',0)}"
        f" 1m음봉:{c.get('adx_trend_1m_fail',0)}"
        f" 15mVR(0.8미만):{c.get('adx_trend_vr5_15m_fail',0)}"
        f" ✅통과:{c.get('adx_trend_pass',0)}",
        f"  [K역추세] ⛔비활성(전구간 마이너스, 시간축 무반응)",
        f"━━━━━━━━━━━━━━━━",
        f"🚫 gate탈락:",
        f"  v4없음: {c['gate_fail_no_v4']} | 코인CD: {c['gate_fail_coin_cd']}",
        f"  신선도: {c['gate_fail_fresh']} | 스프레드: {c['gate_fail_spread']}",
        f"  거래대금: {c['gate_fail_vol_min']} | 매수비: {c['gate_fail_buy_ratio']}",
        f"  가속: {c['gate_fail_accel']} | 거래속도: {c['gate_fail_early_flow']}",
        f"✅ gate통과: {_gp}(Δ{d('gate_pass')})",
        f"━━━━━━━━━━━━━━━━",
        f"🧊 쿨다운: {c['cooldown_block']} | 포지션: {c['position_block']}",
        f"📋 postcheck: {c['postcheck_block']} | 락: {c['lock_block']}",
        f"⛔ 연패중지: {c['suspend_block']}",
        f"🚀 진입: {c['send_attempt']}(Δ{d('send_attempt')}) | 성공: {_succ}(Δ{d('send_success')})",
        f"━━━━━━━━━━━━━━━━",
        f"📈 <b>퍼널 전환율</b>",
        f"  detect→v4: {pct(_v4, _det)} | v4→raw: {pct(_raw, _v4)}",
        f"  raw→gate: {pct(_gp, _raw)} | gate→성공: {pct(_succ, _gp)}",
        f"  <b>전체: detect→성공 {pct(_succ, _det)}</b>",
    ]

    # 📈 값 분포 + 니어미스
    vs = _pipeline_value_summary()
    val_lines = ["━━━━━━━━━━━━━━━━", "📉 <b>값 분포 (max/avg) + 니어미스</b>"]
    _m3 = vs.get("m3_pct", {})
    if _m3.get("n", 0) > 0:
        val_lines.append(f"  m3%: max={_m3['max']:.3f} avg={_m3['avg']:.3f} thr={_m3['thr']:.3f} 🎯NM={_m3['near_miss']}")
    _vr5 = vs.get("vr5", {})
    if _vr5.get("n", 0) > 0:
        val_lines.append(f"  VR5: max={_vr5['max']:.1f} avg={_vr5['avg']:.1f} thr=3.2 🎯NM={_vr5['near_miss']}")
    _atr = vs.get("atr_pct", {})
    if _atr.get("n", 0) > 0:
        val_lines.append(f"  ATR%: max={_atr['max']:.2f} avg={_atr['avg']:.2f} thr=0.7 🎯NM={_atr['near_miss']}")
    _adx_v = vs.get("adx_15_vol3x", {})
    _adx_b = vs.get("adx_15_20bar", {})
    if _adx_v.get("n", 0) > 0:
        val_lines.append(f"  ADX(vol3x): max={_adx_v['max']:.1f} avg={_adx_v['avg']:.1f} thr=20 🎯NM={_adx_v['near_miss']}")
    if _adx_b.get("n", 0) > 0:
        val_lines.append(f"  ADX(20bar): max={_adx_b['max']:.1f} avg={_adx_b['avg']:.1f} thr=25 🎯NM={_adx_b['near_miss']}")
    _gap = vs.get("20bar_gap_pct", {})
    if _gap.get("n", 0) > 0:
        val_lines.append(f"  20봉gap%: max={_gap['max']:.2f} avg={_gap['avg']:.2f} 🎯NM={_gap['near_miss']}")
    lines.extend(val_lines)

    # 📊 gate 지표 평균
    _gs = vs.get("gate_spread", {})
    _gb = vs.get("gate_buy_ratio", {})
    _gv = vs.get("gate_vol_krw", {})
    _ga = vs.get("gate_accel", {})
    _gf = vs.get("gate_flow_kps", {})
    if any(v.get("n", 0) > 0 for v in [_gs, _gb, _gv]):
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("📊 <b>gate 진입 지표 평균</b>")
        if _gs.get("n"): lines.append(f"  스프레드: avg={_gs['avg']:.2f}% max={_gs['max']:.2f}%")
        if _gb.get("n"): lines.append(f"  매수비: avg={_gb['avg']:.0%} min={_gb['min']:.0%}")
        if _gv.get("n"): lines.append(f"  거래대금: avg={_gv['avg']:.0f}M max={_gv['max']:.0f}M")
        if _ga.get("n"): lines.append(f"  가속: avg={_ga['avg']:.1f}x max={_ga['max']:.1f}x")
        if _gf.get("n"): lines.append(f"  거래속도: avg={_gf['avg']:.0f}K/s max={_gf['max']:.0f}K/s")

    # 🏆 전략별 통과 비율
    with _PIPELINE_STRATEGY_LOCK:
        strat = dict(_PIPELINE_STRATEGY_PASS)
    strat_total = sum(strat.values())
    if strat_total > 0:
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("🏆 <b>전략별 시그널</b>")
        for sname, scount in sorted(strat.items(), key=lambda x: -x[1]):
            lines.append(f"  {sname}: {scount} ({scount/strat_total*100:.0f}%)")

    # 🪙 Top-5 탈락 코인
    with _PIPELINE_COIN_HITS_LOCK:
        coin_totals = {coin: sum(reasons.values()) for coin, reasons in _PIPELINE_COIN_HITS.items()}
    if coin_totals:
        top_coins = sorted(coin_totals.items(), key=lambda x: -x[1])[:5]
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("🪙 <b>Top-5 탈락 코인</b>")
        for coin, cnt in top_coins:
            reasons = _PIPELINE_COIN_HITS.get(coin, {})
            top_reason = max(reasons, key=reasons.get) if reasons else "?"
            lines.append(f"  {coin}: {cnt}회 (주사유: {top_reason})")

    # 💡 최근 시그널 종목
    with _PIPELINE_SIGNAL_COINS_LOCK:
        recent_signals = list(_PIPELINE_SIGNAL_COINS)
    if recent_signals:
        # 최근 5개
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("💡 <b>최근 시그널 종목</b>")
        for sig in recent_signals[-5:]:
            _sig_market = sig["market"].split("-")[-1] if "-" in sig["market"] else sig["market"]
            lines.append(f"  {_sig_market} [{sig['strategy']}]")

    # 🎯 가장 아까운 근접 탈락 (최근 3개)
    with _PIPELINE_NEAR_MISS_LOCK:
        nm_list = list(_PIPELINE_NEAR_MISS)
    if nm_list:
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("🎯 <b>근접 탈락 (아깝게 놓친 것)</b>")
        for nm in nm_list[-3:]:
            _nm_market = nm["market"].split("-")[-1] if "-" in nm["market"] else nm["market"]
            lines.append(f"  {_nm_market}: {nm['metrics']}")

    # ⏱️ 스캔 레이턴시
    with _PIPELINE_SCAN_LAT_LOCK:
        lat_list = list(_PIPELINE_SCAN_LATENCIES)
    if lat_list:
        lat_avg = sum(lat_list) / len(lat_list)
        lat_max = max(lat_list)
        lat_p95 = sorted(lat_list)[min(int(len(lat_list) * 0.95), len(lat_list) - 1)] if len(lat_list) >= 5 else lat_max
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append(f"⏱️ 스캔 레이턴시(사이클): avg={lat_avg:.0f}ms p95={lat_p95:.0f}ms max={lat_max:.0f}ms ({len(lat_list)}cycle)")

    # 🔁 마켓별 재스캔 간격 — 한 코인이 다음에 다시 스캔되는 실 간격
    # (사이클 80s ÷ 마켓수 ≠ 코인별 재스캔 간격. 진짜 alpha decay 척도)
    with _MARKET_SCAN_LOCK:
        ms_list = list(_MARKET_SCAN_INTERVALS)
    if ms_list and len(ms_list) >= 10:
        ms_sorted = sorted(ms_list)
        ms_avg = sum(ms_list) / len(ms_list)
        ms_p50 = ms_sorted[len(ms_sorted) // 2]
        ms_p95 = ms_sorted[min(int(len(ms_sorted) * 0.95), len(ms_sorted) - 1)]
        ms_max = ms_sorted[-1]
        lines.append(f"🔁 마켓별 재스캔간격: avg={ms_avg/1000:.1f}s p50={ms_p50/1000:.1f}s p95={ms_p95/1000:.1f}s max={ms_max/1000:.1f}s ({len(ms_list)}건)")

    # 🔍 단계별 레이턴시 — 사이클 내 각 단계 elapsed (병목 위치 파악)
    # scan_fetch (네트워크 I/O) vs scan_detect (CPU 루프) 분리로 병목 특정
    # sample count(n=) 포함 — 호출 빈도가 낮은 단계의 outlier 영향 파악
    with _PIPELINE_STAGE_LOCK:
        stage_snapshot = {k: list(v) for k, v in _PIPELINE_STAGE_LATENCIES.items()}
    _stage_order = (
        "scan_fetch", "scan_detect",
        "detect_leader",                   # detect 루프 총합
        "detect_v4", "universal_ind",      # detect_leader 내부 (fetch는 tag 기반 별도 섹션)
        "shadow_eval",
        "save_state", "report_full", "report_mini",
        "tg_flush", "health_check",
    )
    _stage_rows = []
    for _stage_name in _stage_order:
        _lst = stage_snapshot.get(_stage_name, [])
        _n = len(_lst)
        if _n < 5:
            continue
        _lst_sorted = sorted(_lst)
        _s_avg = sum(_lst) / _n
        _s_p95 = _lst_sorted[min(int(_n * 0.95), _n - 1)]
        _s_max = _lst_sorted[-1]
        _stage_rows.append(
            f"   {_stage_name:14} n={_n:3} avg={_s_avg:6.1f}ms p95={_s_p95:7.1f}ms max={_s_max:8.1f}ms"
        )
    if _stage_rows:
        lines.append("🔍 단계별 레이턴시 (p95/max에 튀는 단계 = 병목)")
        lines.extend(_stage_rows)

    # 🔎 fetch by call-site tag — 어느 경로에서 얼마나 호출되는지 분해
    # key 예: c5_detect_leader, c15_detect_leader, c1_other(ThreadPool/기타)
    with _TAGGED_FETCH_LOCK:
        _tag_snap = {k: list(v) for k, v in _TAGGED_FETCH_HISTORY.items()}
    if _tag_snap:
        _tag_rows = []
        for _key in sorted(_tag_snap.keys()):
            _hist = _tag_snap[_key]
            if len(_hist) < 5:
                continue
            _n_cyc = len(_hist)
            _total_ms = sum(ms for ms, _c in _hist)
            _total_calls = sum(_c for _m, _c in _hist)
            if _total_calls == 0:
                continue
            _avg_ms_cyc = _total_ms / _n_cyc
            _avg_calls_cyc = _total_calls / _n_cyc
            _per_call = _total_ms / _total_calls
            _tag_rows.append(
                (_avg_ms_cyc,
                 f"   {_key:28} n={_n_cyc:3} cyc_avg={_avg_ms_cyc:7.1f}ms "
                 f"calls/cyc={_avg_calls_cyc:6.1f} per_call={_per_call:5.2f}ms")
            )
        _tag_rows.sort(key=lambda x: -x[0])
        if _tag_rows:
            lines.append("🔎 fetch call-site별 분해 (tag=호출 위치)")
            for _, _row in _tag_rows:
                lines.append(_row)

    # 🧠 check_fn 캐시 hit/miss 직접 계측 — 캐시가 진짜 작동하는지 확인
    with _CHECK_FN_CACHE_LOCK:
        _cache_hits_snap = dict(_CHECK_FN_CACHE_HITS)
        _cache_misses_snap = dict(_CHECK_FN_CACHE_MISSES)
    if _cache_hits_snap or _cache_misses_snap:
        _total_hits = sum(_cache_hits_snap.values())
        _total_misses = sum(_cache_misses_snap.values())
        _total_calls = _total_hits + _total_misses
        _hit_rate = (_total_hits / _total_calls * 100) if _total_calls > 0 else 0
        lines.append(f"🧠 check_fn 캐시: hit={_total_hits} miss={_total_misses} hit_rate={_hit_rate:.1f}%")
        # Phase2: 함수별 상세 생략 (인프라 안정 확인 완료)

    # ⏱️ check_fn 실행 시간 — 진짜 병목 함수 특정 (cache miss 시점만 측정)
    with _CHECK_FN_EXEC_LOCK:
        _exec_total_snap = dict(_CHECK_FN_EXEC_TOTAL_MS)
        _exec_calls_snap = dict(_CHECK_FN_EXEC_CALLS)
    if _exec_calls_snap:
        _total_all_ms = sum(_exec_total_snap.values())
        _total_all_calls = sum(_exec_calls_snap.values())
        lines.append(f"⏱️ check_fn 실행 시간 (cache miss만, 함수별 누적): 총 {_total_all_ms/1000:.1f}s / {_total_all_calls}회")
        # Phase2: 함수별 상세 생략 (인프라 안정 확인 완료)
        _exec_rows = []
        _SHOW_FN_DETAIL = False
        for _fn in _exec_total_snap.keys():
            if not _SHOW_FN_DETAIL:
                break
            _total_ms = _exec_total_snap.get(_fn, 0.0)
            _n = _exec_calls_snap.get(_fn, 0)
            if _n < 10:
                continue
            _avg_ms = _total_ms / _n if _n > 0 else 0
            _share = (_total_ms / _total_all_ms * 100) if _total_all_ms > 0 else 0
            _short = _fn.replace("_v0_check_", "")
            _exec_rows.append((_total_ms, f"   {_short:28} n={_n:6} total={_total_ms/1000:7.2f}s avg={_avg_ms:5.2f}ms ({_share:4.1f}%)"))
        _exec_rows.sort(key=lambda x: -x[0])
        for _, _row in _exec_rows:
            lines.append(_row)

    # 🕐 시간대별 신호 (non-zero만)
    with _PIPELINE_HOURLY_LOCK:
        h_sig = list(_PIPELINE_HOURLY_SIGNALS)
        h_gp = list(_PIPELINE_HOURLY_GATE_PASS)
        h_succ = list(_PIPELINE_HOURLY_SUCCESS)
    _hourly_parts = []
    for h in range(24):
        if h_sig[h] > 0 or h_gp[h] > 0 or h_succ[h] > 0:
            _hourly_parts.append(f"{h}시:{h_sig[h]}/{h_gp[h]}/{h_succ[h]}")
    if _hourly_parts:
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("🕐 <b>시간대별 (raw/gate/성공)</b>")
        # 6개씩 한 줄
        for i in range(0, len(_hourly_parts), 6):
            lines.append("  " + " | ".join(_hourly_parts[i:i+6]))

    # 📡 섀도우 루트 계측 (v8)
    shadow_lines = _v4_shadow_report_lines()
    if shadow_lines:
        lines.append("━━━━━━━━━━━━━━━━")
        lines.extend(shadow_lines)

    # 스냅샷 갱신 (다음 리포트의 delta 계산용)
    _PIPELINE_PREV_SNAPSHOT = dict(c)
    _PIPELINE_PREV_SNAPSHOT_TS = now

    # 📊 pipeline_gauge.csv 자동 기록
    try:
        _pipeline_gauge_csv_write()
    except Exception:
        pass

    msg = "\n".join(lines)
    print(msg)
    tg_send(msg)

    # 섀도우 통계 저장 (누적 — 초기화하지 않음)
    try:
        _save_shadow_stats()
    except Exception:
        pass


_PIPELINE_MINI_LAST_TS = 0
_PIPELINE_MINI_INTERVAL = 60  # 1분
_PIPELINE_MINI_PREV = {}  # delta 계산용 이전 스냅샷


def _pipeline_mini_report():
    """1분마다 v4 전략 퍼널 미니 리포트 (콘솔만, 텔레그램 X)"""
    global _PIPELINE_MINI_LAST_TS, _PIPELINE_MINI_PREV
    now = time.time()
    if (now - _PIPELINE_MINI_LAST_TS) < _PIPELINE_MINI_INTERVAL:
        return
    _PIPELINE_MINI_LAST_TS = now
    with _PIPELINE_COUNTERS_LOCK:
        c = dict(_PIPELINE_COUNTERS)
    v4 = c.get("v4_called", 0)
    if v4 == 0:
        return  # 스캔 없으면 skip
    prev = _PIPELINE_MINI_PREV
    def d(k):
        return c.get(k, 0) - prev.get(k, 0)
    _gt_enter = c.get('momentum_enter', 0)
    _gt_rsi = c.get('momentum_rsi5_fail', 0)
    _gt_1m = c.get('momentum_1m_fail', 0)
    _gt_vr1 = c.get('momentum_vr5_over_fail', 0)
    _gt_vr15 = c.get('momentum_vr5_15m_fail', 0)
    _gt_pass = c.get('momentum_pass', 0)
    # delta (이번 1분 구간)
    _d_enter = d('momentum_enter')
    _d_rsi = d('momentum_rsi5_fail')
    _d_1m = d('momentum_1m_fail')
    _d_vr1 = d('momentum_vr5_over_fail')
    _d_vr15 = d('momentum_vr5_15m_fail')
    _d_pass = d('momentum_pass')
    lines = [
        f"[V0_FUNNEL] {now_kst_str()} | v4호출={v4}(Δ{d('v4_called')})",
        f"  [GT] 호출={_gt_enter}(Δ{_d_enter}) RSI탈락={_gt_rsi}(Δ{_d_rsi}) "
        f"1m음봉={_gt_1m}(Δ{_d_1m}) VR5과열={_gt_vr1}(Δ{_d_vr1}) "
        f"15mVR부족={_gt_vr15}(Δ{_d_vr15}) ✅통과={_gt_pass}(Δ{_d_pass})",
        f"  [C15] 통과={c.get('reversal_15m_pass',0)}(Δ{d('reversal_15m_pass')})",
        f"  raw={c.get('v4_raw_hit',0)}(Δ{d('v4_raw_hit')}) "
        f"gate={c.get('gate_pass',0)}(Δ{d('gate_pass')}) "
        f"→ 포지션={c.get('position_block',0)}(Δ{d('position_block')}) "
        f"포스트={c.get('postcheck_block',0)}(Δ{d('postcheck_block')}) "
        f"쿨다운={c.get('cooldown_block',0)}(Δ{d('cooldown_block')}) "
        f"락={c.get('lock_block',0)}(Δ{d('lock_block')}) "
        f"연패={c.get('suspend_block',0)}(Δ{d('suspend_block')})",
        f"  → 진입={c.get('send_attempt',0)}(Δ{d('send_attempt')}) "
        f"성공={c.get('send_success',0)}(Δ{d('send_success')})",
    ]
    with _STREAK_LOCK:
        _strat_streak_str = " ".join(f"{k}:{v}패" for k, v in _STRAT_LOSE_STREAK.items() if v > 0)
    if _strat_streak_str:
        lines.append(f"  [연패] {_strat_streak_str}")
    print("\n".join(lines))
    _PIPELINE_MINI_PREV = c


def _shadow_log_write(timestamp, market, strategy, raw_signal, block_reason, final_alert,
                      extra_info=""):
    """섀도우 모드 CSV 로그 1줄 기록"""
    global _SHADOW_LOG_INITIALIZED
    with _SHADOW_LOG_LOCK:
        write_header = not _SHADOW_LOG_INITIALIZED
        try:
            with open(_SHADOW_LOG_PATH, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow(["timestamp", "market", "strategy", "raw_signal",
                                "block_reason", "final_alert", "extra_info"])
                    _SHADOW_LOG_INITIALIZED = True
                w.writerow([timestamp, market, strategy, raw_signal,
                            block_reason, final_alert, extra_info])
        except Exception as e:
            print(f"[SHADOW_LOG_ERR] {e}")


# =========================
# 🔐 프로세스 간 중복 진입 방지 (파일락 + 메모리락)
# =========================
# 🔧 FIX: 락에 소유자(스레드 ID) 추적 추가 - reentrant 버그 수정
# 형식: { market: (timestamp, owner_thread_ident) }
_MEMORY_ENTRY_LOCKS = {}  # 메모리 기반 락 (스레드 간)
_MEMORY_LOCK = threading.Lock()  # 메모리 락 보호용

def _entry_lock_path(market: str) -> str:
    return f"/tmp/bot_entry_{market.replace('-', '_')}.lock"

def _try_acquire_entry_lock(market: str, ttl_sec: int = 300, reentrant: bool = False) -> bool:
    """락 획득 시도. 성공하면 True, 이미 락 있으면 False

    🔧 FIX: 원자적 파일 생성 (O_CREAT | O_EXCL) + 메모리 락 이중 보호
    🔧 FIX: reentrant=True는 **같은 스레드**에서만 재진입 허용 (소유자 추적)
    """
    current_owner = threading.current_thread().ident

    # 1️⃣ 메모리 락 먼저 체크 (같은 프로세스 내 스레드 간)
    with _MEMORY_LOCK:
        if market in _MEMORY_ENTRY_LOCKS:
            lock_ts, lock_owner = _MEMORY_ENTRY_LOCKS[market]
            if time.time() - lock_ts < ttl_sec:
                # 🔧 FIX: reentrant 모드는 **같은 스레드(소유자)**일 때만 True
                # 🔧 FIX: TTL 갱신 (재진입 시 타임스탬프 리셋 → 장기 루틴에서 TTL 만료 방지)
                if reentrant and lock_owner == current_owner:
                    _MEMORY_ENTRY_LOCKS[market] = (time.time(), current_owner)
                    try:
                        os.utime(_entry_lock_path(market), None)
                    except Exception:
                        pass
                    return True
                return False
        # 🔧 FIX: (타임스탬프, 소유자) 튜플로 저장
        _MEMORY_ENTRY_LOCKS[market] = (time.time(), current_owner)

    # 2️⃣ 파일 락 (프로세스 간)
    # 🔧 FIX: TOCTOU 방지 — O_CREAT|O_EXCL 먼저 시도, 실패 시 TTL 체크 후 재시도
    path = _entry_lock_path(market)
    for _fl_attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(time.time()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            if _fl_attempt == 0:
                # 첫 실패: TTL 만료 여부 확인 후 제거 → 재시도
                try:
                    if (time.time() - os.path.getmtime(path)) >= ttl_sec:
                        os.remove(path)
                        continue  # 재시도
                except Exception:
                    pass
            # TTL 미만이거나 제거 실패 → 락 획득 실패
            with _MEMORY_LOCK:
                _MEMORY_ENTRY_LOCKS.pop(market, None)
            return False
        except Exception:
            with _MEMORY_LOCK:
                _MEMORY_ENTRY_LOCKS.pop(market, None)
            return False

def _release_entry_lock(market: str):
    """락 해제 (파일 삭제 + 메모리 락 해제)"""
    # 메모리 락 해제
    with _MEMORY_LOCK:
        _MEMORY_ENTRY_LOCKS.pop(market, None)

    # 파일 락 해제
    path = _entry_lock_path(market)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def cleanup_stale_entry_locks(max_age_sec=300):
    """오래된 엔트리 락파일 정리 (기본 5분)"""
    try:
        nowt = time.time()
        cleaned = 0
        for name in os.listdir("/tmp"):
            if not name.startswith("bot_entry_"):
                continue
            path = f"/tmp/{name}"
            try:
                if nowt - os.path.getmtime(path) > max_age_sec:
                    os.remove(path)
                    cleaned += 1
            except Exception:
                pass
        if cleaned > 0:
            print(f"[LOCK_CLEAN] {cleaned}개 오래된 락파일 정리됨")
    except Exception as e:
        print(f"[LOCK_CLEAN_ERR] {e}")


from contextlib import contextmanager

@contextmanager
def entry_lock(market: str, ttl_sec: int = 300, reentrant: bool = False):
    """엔트리 락 컨텍스트 매니저 - 안전한 락 획득/해제

    🔧 reentrant=True: 재진입 모드
      - 동일 스레드 재진입(기존 락 재사용) → 해제 안 함 (원래 획득한 곳에서 해제)
      - 신규 획득(상위에서 락 없었음) → 해제함 (락 누수 방지)
    """
    # 🔧 FIX: reentrant 모드에서 "기존 락 재사용인지 / 신규 획득인지" 판별
    was_already_held = False
    if reentrant:
        current_tid = threading.current_thread().ident
        with _MEMORY_LOCK:
            existing = _MEMORY_ENTRY_LOCKS.get(market)
            if existing:
                _, lock_owner = existing
                was_already_held = (lock_owner == current_tid)

    got = _try_acquire_entry_lock(market, ttl_sec=ttl_sec, reentrant=reentrant)
    try:
        yield got
    finally:
        # 🔧 FIX: reentrant 모드라도 "신규 획득"이면 반드시 해제 (락 누수 방지)
        # - reentrant=False → 항상 해제
        # - reentrant=True + 기존 재사용(was_already_held) → 해제 안 함
        # - reentrant=True + 신규 획득(!was_already_held) → 해제함
        if got and not (reentrant and was_already_held):
            _release_entry_lock(market)


def get_available_krw(accounts) -> float:
    """KRW 가용잔고 계산 (locked 반영)"""
    for a in accounts:
        if a.get("currency") == "KRW":
            bal = float(a.get("balance", "0") or 0)
            locked = float(a.get("locked", "0") or 0)
            return max(0.0, bal - locked)
    return 0.0


# =========================
# 🔥 업비트 Private API (주문/잔고/포지션 관리)
# =========================
# import uuid, hashlib, jwt  # 상단(8-11줄)에서 이미 import됨
# from urllib.parse import urlencode  # 상단(6줄)에서 이미 import됨

UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY", "")
UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY", "")

# 🔧 보안: 키 길이 로깅은 디버그 모드에서만
if os.getenv("DEBUG_KEYS") == "1":
    print(
        "[UPBIT_KEYS] access_len=",
        len(UPBIT_ACCESS_KEY),
        "secret_len=",
        len(UPBIT_SECRET_KEY),
    )

# 리스크 관리 — config.py에서 정의됨 (RISK_PER_TRADE, AGGRESSIVE_MODE, USE_PYRAMIDING,
#   SEED_RISK_FRACTION, ADD_RISK_FRACTION, PYRAMID_ADD_*)
AUTO_TRADE = os.getenv("AUTO_TRADE", "0") == "1"
print(f"[BOT_MODE] AUTO_TRADE={AUTO_TRADE}, RISK_PER_TRADE={RISK_PER_TRADE}")
# PYRAMID_ADD_COOLDOWN_SEC — config.py에서 정의됨


# ============================================================
# 🔒 LOCK ORDERING RULE (데드락 방지)
# ============================================================
# 여러 lock을 동시에 획득할 때 반드시 아래 순서를 따를 것.
# 역순 획득 금지! Nested lock 금지 — 항상 선 release 후 후 acquire.
#
# 순서 (번호가 작을수록 먼저 획득):
#   1) _MEMORY_LOCK          (진입 락 메모리)
#   2) _POSITION_LOCK        (포지션 + _CLOSING_MARKETS)
#   3) _MONITOR_LOCK         (모니터 스레드 레지스트리)
#   4) _ORPHAN_LOCK          (유령 포지션 감지)
#   5) _RECENT_BUY_LOCK      (최근 매수 타임스탬프)
#   6) _STREAK_LOCK, _COIN_LOSS_LOCK  (거래 통계)
#   7) _RETEST_LOCK, _CIRCLE_LOCK, _BOX_LOCK  (전략별 워치리스트)
#   8) _trade_log_lock, _CSV_LOCK  (로깅)
#   9) _TG_SESSION_LOCK, _req_lock  (네트워크)
#
# 위반 방지: nested `with lock:` 사용 금지.
#   잘못된 예: with _POSITION_LOCK: ... with _ORPHAN_LOCK: ...
#   올바른 예: with _POSITION_LOCK: ... (release) → with _ORPHAN_LOCK: ...
# ============================================================

# 현재 열린 포지션 기록용
OPEN_POSITIONS = {}
_POSITION_LOCK = threading.Lock()  # [LOCK_ORDER: 2] 포지션 접근 락
_CLOSING_MARKETS = set()  # 중복 청산 방지용


def _pop_position_tracked(market, caller="unknown"):
    """🔧 FIX: 포지션 제거 시 호출자 + 상태 로깅 (유령포지션 원인 추적용)
    반드시 _POSITION_LOCK 내부에서 호출할 것."""
    pos = OPEN_POSITIONS.get(market)
    if pos:
        state = pos.get("state", "?")
        strategy = pos.get("strategy", "?")
        age = time.time() - pos.get("entry_ts", time.time())
        print(f"[POS_REMOVE] {market} state={state} strategy={strategy} age={age:.0f}s caller={caller}")
        traceback.print_stack(limit=6)
    return OPEN_POSITIONS.pop(market, None)


def mark_position_closed(market, reason=""):
    """
    🔧 FIX: 포지션 청산 완료 마킹 (중복 청산 방지 핵심)
    - state='closed' 마킹 후 OPEN_POSITIONS에서 제거
    - 이미 closed면 False 반환 (중복 호출 방지)
    """
    with _POSITION_LOCK:
        pos = OPEN_POSITIONS.get(market)
        if not pos:
            return False
        # 이미 closed면 재처리 방지
        if pos.get("state") == "closed":
            return False
        pos["state"] = "closed"
        pos["closed_at"] = time.time()
        pos["closed_reason"] = reason
        _pop_position_tracked(market, f"mark_closed:{reason}")
    return True

# MAX_POSITIONS — config.py에서 정의됨

# 🔧 FIX: 모니터링 스레드 중복 방지
# 🔧 FIX: ident 대신 스레드 객체 저장 (is_alive() 체크 가능)
_ACTIVE_MONITORS = {}  # { "KRW-BTC": threading.Thread }
_MONITOR_LOCK = threading.Lock()

# 🔧 손실 후 쿨다운 추적 (상단 선언으로 초기화 순서 보장)
last_trade_was_loss = {}
_TRADE_LOSS_LOCK = threading.Lock()  # 🔧 FIX: last_trade_was_loss 스레드 안전 보호

# 🔧 유령 포지션 동기화
_LAST_ORPHAN_SYNC = 0
# ORPHAN_SYNC_INTERVAL — config.py에서 정의됨
_ORPHAN_HANDLED = set()    # 이미 처리한 유령 포지션 (세션 내 중복 알림 방지)
_ORPHAN_LOCK = threading.Lock()  # 🔧 FIX: _ORPHAN_HANDLED 스레드 안전 보호
_PREV_SYNC_MARKETS = set() # 이전 동기화에서 발견된 마켓 (신규 매수 오탐 방지)
_ORPHAN_FIRST_SYNC = True  # 🔧 FIX: 봇 시작 후 첫 sync 표시 (재시작 시 전체 유령 즉시 처리)
_RECENT_BUY_TS = {}        # 🔧 최근 매수 시간 추적 (유령 오탐 방지)
_RECENT_BUY_LOCK = threading.Lock()  # 🔧 FIX: _RECENT_BUY_TS 스레드 안전 보호 (모니터/스캔 동시접근)

# 🔔 재모니터링 알림 쿨타임 (비매매 알림용)

# =========================
# 📈 최근 승률 기반 리스크 튜닝
# =========================
# FIX [H3]: 불필요한 deque alias 제거 (상단에서 이미 import됨)
TRADE_HISTORY = deque(maxlen=30)  # 최근 30개 거래 기록

# 🔧 크리티컬 핫픽스: streak 전역변수 상단 선언 (NameError 방지)
_lose_streak = 0              # 연속 패배 수 (글로벌 — 로그/표시용)
_win_streak = 0               # 연속 승리 수
_STREAK_LOCK = threading.Lock()  # 🔧 FIX H1: streak 카운터 스레드 안전 보장

# 🔧 전략별 연패 분리 — C 손실이 GT를 차단하지 않도록
_STRAT_LOSE_STREAK = {}       # {"GT": 0, "C": 0, ...}
_STRAT_SUSPEND_UNTIL = {}     # {"GT": 0.0, "C": 0.0, ...}
_STRAT_MAX_MODE = {}          # {"GT": None, "C": None, ...}

def _strat_group_from_signal(signal_type: str) -> str:
    if "GT" in signal_type or "모멘텀" in signal_type:
        return "GT"
    elif "반전" in signal_type or signal_type == "C":
        return "C"
    return signal_type

# 🔧 승률개선: 코인별 연패 추적 (같은 코인 반복 손절 방지)
_COIN_LOSS_HISTORY = {}  # { "KRW-XXX": [loss_ts1, loss_ts2, ...] }
_COIN_LOSS_LOCK = threading.Lock()
# COIN_LOSS_MAX, COIN_LOSS_COOLDOWN — config.py에서 정의됨
# 🔧 FIX: 연패 게이트 전역변수 상단 선언 (record_trade()에서 사용, 선언 순서 보장)
_ENTRY_SUSPEND_UNTIL = 0.0     # 연패 시 전체 진입 중지 타임스탬프
_ENTRY_MAX_MODE = None         # 연패 시 entry_mode 상한 (None=제한없음, "half"=half만 허용)


def record_trade(market: str, pnl_pct: float, signal_type: str = "기본"):
    """
    거래 결과 기록
    🔧 FIX: 소수 단위로 통일 (예: +0.023 = +2.3%)
    - pnl_pct: 소수 단위 수익률 (예: +0.023, -0.015)
    - signal_type: 진입 신호 타입 (점화/강돌파/EMA↑/고점↑/거래량↑/기본/리테스트/동그라미/박스)
    - update_trade_result()와 동일한 단위 사용
    🔧 FIX: streak도 여기서 일원화 (update_trade_result 누락/중복 스킵 영향 제거)
    """
    global _lose_streak, _win_streak, _ENTRY_SUSPEND_UNTIL, _ENTRY_MAX_MODE
    # 🔧 FIX: 단위 자동 정규화 — % 단위(예: 2.3)가 들어오면 소수(0.023)로 변환
    # 소수점 비율(0.02 = 2%)이 정상 범위, 10 이상이면 확실히 % 단위
    # 🔧 FIX: 기존 1.0 기준은 실제 100%+ 수익을 잘못 축소 — 10.0으로 상향
    # 🔧 FIX: 1~10 범위 경고 (호출부 단위 불일치 조기 발견)
    if abs(pnl_pct) > 10.0:
        print(f"[RECORD_TRADE_WARN] {market} pnl_pct={pnl_pct:.4f} > 10.0 → 자동 /100 변환 (호출부 단위 확인 필요)")
        pnl_pct = pnl_pct / 100.0
    elif abs(pnl_pct) > 1.0:
        print(f"[RECORD_TRADE_WARN] {market} pnl_pct={pnl_pct:.4f} — 100%+ 수익? 호출부 단위 재확인")
    is_win = pnl_pct > 0

    TRADE_HISTORY.append({
        "market": market,
        "pnl": pnl_pct,
        "win": is_win,
        "time": time.time(),
        "signal": signal_type,  # 🔧 수익개선: 전략별 승률 추적용
    })

    # 🔧 수익개선: 전략별 승률 로깅 (어떤 전략이 돈을 까먹는지 파악)
    _sig_trades = [t for t in TRADE_HISTORY if t.get("signal") == signal_type]
    if len(_sig_trades) >= 5:
        _sig_wins = sum(1 for t in _sig_trades if t.get("win"))
        _sig_wr = _sig_wins / len(_sig_trades) * 100
        _sig_avg_pnl = statistics.mean([t["pnl"] for t in _sig_trades]) * 100
        print(f"[STRATEGY_STAT] {signal_type}: {len(_sig_trades)}건 승률 {_sig_wr:.0f}% 평균PnL {_sig_avg_pnl:+.2f}%")

    # 🔧 승률개선: 코인별 손실 기록 (같은 코인 반복 손절 방지)
    now_ts = time.time()
    with _COIN_LOSS_LOCK:
        if is_win:
            # 승리하면 해당 코인 손실 기록 초기화
            _COIN_LOSS_HISTORY.pop(market, None)
        else:
            # 패배 기록 추가 (최근 COIN_LOSS_COOLDOWN 이내만 유지)
            if market not in _COIN_LOSS_HISTORY:
                _COIN_LOSS_HISTORY[market] = []
            _COIN_LOSS_HISTORY[market].append(now_ts)
            # 오래된 기록 정리
            _COIN_LOSS_HISTORY[market] = [
                ts for ts in _COIN_LOSS_HISTORY[market]
                if now_ts - ts < COIN_LOSS_COOLDOWN
            ]

    # 🔧 전략별 연패 분리 — signal_type으로 그룹 결정
    _sg = _strat_group_from_signal(signal_type)

    with _STREAK_LOCK:
        # 글로벌 streak (로그/표시용)
        if is_win:
            _lose_streak = 0
            _win_streak += 1
            _ENTRY_MAX_MODE = None
        else:
            _lose_streak += 1
            _win_streak = 0

        # 전략별 streak (실제 차단용)
        if is_win:
            _STRAT_LOSE_STREAK[_sg] = 0
            _STRAT_MAX_MODE[_sg] = None
        else:
            _STRAT_LOSE_STREAK[_sg] = _STRAT_LOSE_STREAK.get(_sg, 0) + 1
            _sl = _STRAT_LOSE_STREAK[_sg]
            if _sl >= 5:
                _STRAT_SUSPEND_UNTIL[_sg] = time.time() + 1800
                _STRAT_MAX_MODE[_sg] = "half"
                print(f"[LOSE_GATE] [{_sg}] 연속 {_sl}패 → 30분 {_sg} 진입 금지")
            elif _sl >= 4:
                _STRAT_SUSPEND_UNTIL[_sg] = time.time() + 600
                _STRAT_MAX_MODE[_sg] = "half"
                print(f"[LOSE_GATE] [{_sg}] 연속 {_sl}패 → 10분 {_sg} 진입 금지")
            elif _sl >= 3:
                _STRAT_MAX_MODE[_sg] = "half"
                print(f"[LOSE_GATE] [{_sg}] 연속 {_sl}패 → {_sg} half만 허용")

    # 💾 거래 발생 시 즉시 상태 저장 (재시작 시 손실 방지)
    global _LAST_STATE_PERSIST_TS
    _LAST_STATE_PERSIST_TS = 0  # 강제 저장 트리거
    _save_bot_state()


def is_coin_loss_cooldown(market: str) -> bool:
    """🔧 승률개선: 코인별 연패 쿨다운 체크
    최근 COIN_LOSS_COOLDOWN(30분) 내 COIN_LOSS_MAX(2)회 이상 손절한 코인이면 True
    """
    now_ts = time.time()
    with _COIN_LOSS_LOCK:
        losses = _COIN_LOSS_HISTORY.get(market, [])
        # 쿨다운 기간 내 손실만 카운트
        recent = [ts for ts in losses if now_ts - ts < COIN_LOSS_COOLDOWN]
        return len(recent) >= COIN_LOSS_MAX


# ============================================================
# 📊 시그널별 성과 통계 + MFE 시계열 추적 시스템
# ============================================================
# 메모리 누적 통계: { signal_tag: { trades, wins, total_pnl, mfe_timeline: {10: [...], 30: [...], ...} } }
_SIGNAL_STATS = {}
_SIGNAL_STATS_LOCK = threading.Lock()


def _load_signal_stats():
    """봇 시작 시 저장된 시그널 통계 로드"""
    global _SIGNAL_STATS
    try:
        if os.path.exists(SIGNAL_STATS_PATH):
            with open(SIGNAL_STATS_PATH, "r", encoding="utf-8") as f:
                _SIGNAL_STATS = json.load(f)
            print(f"[SIGNAL_STATS] 로드 완료: {len(_SIGNAL_STATS)}개 시그널 타입")
    except Exception as e:
        print(f"[SIGNAL_STATS] 로드 실패: {e}")
        _SIGNAL_STATS = {}


def _save_signal_stats():
    """시그널 통계를 파일에 저장 (주기적 호출)"""
    with _SIGNAL_STATS_LOCK:
        data = copy.deepcopy(_SIGNAL_STATS)
    try:
        with open(SIGNAL_STATS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SIGNAL_STATS] 저장 실패: {e}")


def update_signal_stats(signal_tag: str, pnl_pct: float, mfe_snapshots: dict,
                        entry_mode: str = "confirm", exit_reason: str = "",
                        hold_sec: float = 0, mfe_pct: float = 0, mae_pct: float = 0):
    """
    시그널별 성과 통계 업데이트
    - signal_tag: 진입 시그널 태그 (거래량3배, 20봉_고점돌파, 점화, 강돌파, 기본 등)
    - pnl_pct: 최종 수익률 (소수 단위)
    - mfe_snapshots: {10: 0.23, 30: 0.45, 60: 0.67, ...} 각 시점 MFE% (소수 단위)
    - mfe_pct/mae_pct: 최종 MFE/MAE (소수 단위)
    """
    is_win = pnl_pct > 0
    with _SIGNAL_STATS_LOCK:
        if signal_tag not in _SIGNAL_STATS:
            _SIGNAL_STATS[signal_tag] = {
                "trades": 0, "wins": 0, "total_pnl": 0.0,
                "mfe_timeline": {str(t): [] for t in MFE_SNAPSHOT_TIMES},
                "avg_mfe": 0.0, "avg_mae": 0.0,
                "best_pnl": 0.0, "worst_pnl": 0.0,
                "exit_reasons": {},
                "entry_modes": {"confirm": 0, "half": 0},
                "mfe_peaks": [],  # 최고 MFE 도달 시점(초) 리스트
                "hold_secs": [],  # 보유 시간 리스트
            }
        s = _SIGNAL_STATS[signal_tag]
        s["trades"] += 1
        if is_win:
            s["wins"] += 1
        s["total_pnl"] += pnl_pct

        # MFE/MAE 이동평균 업데이트
        n = s["trades"]
        s["avg_mfe"] = s["avg_mfe"] * (n - 1) / n + mfe_pct / n
        s["avg_mae"] = s["avg_mae"] * (n - 1) / n + mae_pct / n
        s["best_pnl"] = max(s["best_pnl"], pnl_pct)
        s["worst_pnl"] = min(s["worst_pnl"], pnl_pct)

        # MFE 시계열 스냅샷 저장 (최근 100건만 유지)
        for t_sec, mfe_val in mfe_snapshots.items():
            t_key = str(t_sec)
            if t_key not in s["mfe_timeline"]:
                s["mfe_timeline"][t_key] = []
            s["mfe_timeline"][t_key].append(round(mfe_val, 5))
            if len(s["mfe_timeline"][t_key]) > 100:
                s["mfe_timeline"][t_key] = s["mfe_timeline"][t_key][-100:]

        # 청산 사유 카운트
        reason_cat = _categorize_exit_reason(exit_reason)
        s["exit_reasons"][reason_cat] = s["exit_reasons"].get(reason_cat, 0) + 1

        # 진입 모드 카운트
        if entry_mode in s["entry_modes"]:
            s["entry_modes"][entry_mode] += 1

        # MFE 피크 시점 기록 (최고점이 몇 초에 나왔는지)
        if mfe_snapshots:
            peak_time = max(mfe_snapshots, key=lambda k: mfe_snapshots[k])
            s["mfe_peaks"].append(int(peak_time))
            if len(s["mfe_peaks"]) > 100:
                s["mfe_peaks"] = s["mfe_peaks"][-100:]

        # 보유시간 기록
        if hold_sec > 0:
            s["hold_secs"].append(round(hold_sec, 1))
            if len(s["hold_secs"]) > 100:
                s["hold_secs"] = s["hold_secs"][-100:]

    # 통계 파일 저장 (10건마다)
    if n % 10 == 0:
        _save_signal_stats()


def _categorize_exit_reason(reason: str) -> str:
    """청산 사유를 카테고리로 분류"""
    r = str(reason).strip()
    if "트레일" in r:
        return "트레일링"
    if "본절" in r or "base_stop" in r:
        return "본절SL"
    if "하드스톱" in r:
        return "하드스톱"
    if "감량" in r or "수급확인" in r:
        return "수급확인SL"
    if "타임아웃" in r or "시간" in r or "만료" in r:
        return "타임아웃"
    if "손절" in r or "ATR" in r:
        return "ATR손절"
    if "폭발" in r:
        return "폭발청산"
    if "Plateau" in r or "고원" in r:
        return "고원청산"
    return r[:10] if r else "미분류"


def get_signal_stats_summary(signal_tag: str) -> dict:
    """특정 시그널의 성과 요약 반환 (청산 전략 최적화용)"""
    with _SIGNAL_STATS_LOCK:
        s = _SIGNAL_STATS.get(signal_tag)
        if not s or s["trades"] < SIGNAL_STATS_MIN_TRADES:
            return {}
        wr = s["wins"] / s["trades"] * 100 if s["trades"] > 0 else 0
        avg_pnl = s["total_pnl"] / s["trades"] if s["trades"] > 0 else 0

        # MFE 시계열 평균 계산
        avg_mfe_timeline = {}
        for t_key, vals in s["mfe_timeline"].items():
            if vals:
                avg_mfe_timeline[t_key] = round(statistics.mean(vals), 5)

        # MFE 피크 시점 중앙값
        median_peak_sec = 0
        if s["mfe_peaks"]:
            sorted_peaks = sorted(s["mfe_peaks"])
            median_peak_sec = sorted_peaks[len(sorted_peaks) // 2]

        return {
            "trades": s["trades"],
            "win_rate": round(wr, 1),
            "avg_pnl": round(avg_pnl, 5),
            "avg_mfe": round(s["avg_mfe"], 5),
            "avg_mae": round(s["avg_mae"], 5),
            "avg_mfe_timeline": avg_mfe_timeline,
            "median_peak_sec": median_peak_sec,
            "best_pnl": s["best_pnl"],
            "worst_pnl": s["worst_pnl"],
            "top_exit_reason": max(s["exit_reasons"], key=s["exit_reasons"].get) if s["exit_reasons"] else "N/A",
        }


def get_all_signal_stats_report() -> str:
    """전체 시그널 성과 리포트 텍스트 생성 (텔레그램 발송용)"""
    with _SIGNAL_STATS_LOCK:
        if not _SIGNAL_STATS:
            return ""
        stats_list = []
        for tag, s in _SIGNAL_STATS.items():
            if s["trades"] < SIGNAL_STATS_MIN_TRADES:
                continue
            wr = s["wins"] / s["trades"] * 100
            avg_pnl = s["total_pnl"] / s["trades"] * 100  # % 단위
            avg_mfe = s["avg_mfe"] * 100
            avg_mae = s["avg_mae"] * 100
            # MFE 시계열: 1분 시점 평균
            mfe_60 = 0
            if "60" in s["mfe_timeline"] and s["mfe_timeline"]["60"]:
                mfe_60 = statistics.mean(s["mfe_timeline"]["60"]) * 100
            # 피크 시점 중앙값
            peak_sec = 0
            if s["mfe_peaks"]:
                sorted_p = sorted(s["mfe_peaks"])
                peak_sec = sorted_p[len(sorted_p) // 2]
            stats_list.append((tag, s["trades"], wr, avg_pnl, avg_mfe, avg_mae, mfe_60, peak_sec))

    if not stats_list:
        return ""

    stats_list.sort(key=lambda x: x[3], reverse=True)  # PnL 순
    lines = [
        f"{'─' * 32}",
        "<b>🎯 시그널별 MFE 분석:</b>",
    ]
    for tag, cnt, wr, avg_pnl, avg_mfe, avg_mae, mfe_60, peak_sec in stats_list:
        emoji = "🟢" if avg_pnl > 0 else "🔴"
        lines.append(
            f"  {emoji} <b>{tag}</b> ({cnt}건 승률{wr:.0f}%)"
        )
        lines.append(
            f"    PnL{avg_pnl:+.2f}% MFE+{avg_mfe:.2f}% MAE{avg_mae:.2f}%"
        )
        lines.append(
            f"    1분MFE+{mfe_60:.2f}% 피크{peak_sec}초"
        )
    return "\n".join(lines)


# 봇 시작 시 통계 로드 (모듈 임포트 시 실행)
_load_signal_stats()


# ============================================================
# 💾 상태 영속화 (서버 재시작 시에도 TRADE_HISTORY, streak, 코인별 손실 누적 유지)
# ============================================================
_LAST_STATE_PERSIST_TS = 0


def _save_bot_state(force=False):
    """봇 상태를 JSON 파일에 저장 (주기적 호출, force=True면 쿨다운 무시)"""
    global _LAST_STATE_PERSIST_TS
    now = time.time()
    if not force and now - _LAST_STATE_PERSIST_TS < STATE_PERSIST_INTERVAL:
        return
    _LAST_STATE_PERSIST_TS = now
    try:
        # TRADE_HISTORY 수집
        trade_hist = list(TRADE_HISTORY)

        # 코인별 손실 기록 수집
        with _COIN_LOSS_LOCK:
            coin_loss = copy.deepcopy(_COIN_LOSS_HISTORY)

        # streak 수집
        with _STREAK_LOCK:
            streaks = {"lose": _lose_streak, "win": _win_streak,
                       "suspend_until": _ENTRY_SUSPEND_UNTIL,
                       "strat_lose": dict(_STRAT_LOSE_STREAK),
                       "strat_suspend": dict(_STRAT_SUSPEND_UNTIL)}

        # OPEN_POSITIONS 수집 (재시작 시 유령포지션 복구용)
        with _POSITION_LOCK:
            positions = {}
            for m, p in OPEN_POSITIONS.items():
                if p.get("state") == "open":
                    positions[m] = {
                        k: v for k, v in p.items()
                        if isinstance(v, (str, int, float, bool, type(None)))
                    }

        state = {
            "saved_at": now,
            "trade_history": trade_hist,
            "coin_loss_history": coin_loss,
            "streaks": streaks,
            "open_positions": positions,
        }
        tmp_path = STATE_PERSIST_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATE_PERSIST_PATH)
    except Exception as e:
        print(f"[STATE_PERSIST] 저장 실패: {e}")


def _shutdown_save_all():
    """종료 시 모든 통계 강제 저장 (데이터 유실 방지)"""
    print("[SHUTDOWN] 종료 감지 — 통계 강제 저장 중...")
    try:
        _save_bot_state(force=True)
    except Exception as e:
        print(f"[SHUTDOWN] bot_state 저장 실패: {e}")
    try:
        _save_signal_stats()
    except Exception as e:
        print(f"[SHUTDOWN] signal_stats 저장 실패: {e}")
    try:
        _save_shadow_stats()
    except Exception as e:
        print(f"[SHUTDOWN] shadow_stats 저장 실패: {e}")
    print("[SHUTDOWN] 강제 저장 완료")


atexit.register(_shutdown_save_all)


def _signal_handler(signum, frame):
    """SIGTERM/SIGINT 수신 시 저장 후 종료"""
    print(f"[SHUTDOWN] 시그널 {signum} 수신")
    _shutdown_save_all()
    sys.exit(0)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def _load_bot_state():
    """봇 시작 시 저장된 상태 복원"""
    global _lose_streak, _win_streak, _ENTRY_SUSPEND_UNTIL
    try:
        if not os.path.exists(STATE_PERSIST_PATH):
            print("[STATE_PERSIST] 저장된 상태 없음 — 초기 상태로 시작")
            return
        with open(STATE_PERSIST_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)

        saved_at = state.get("saved_at", 0)
        age_min = (time.time() - saved_at) / 60
        print(f"[STATE_PERSIST] 상태 파일 발견 (저장 시점: {age_min:.1f}분 전)")

        # TRADE_HISTORY 복원
        hist = state.get("trade_history", [])
        if hist:
            TRADE_HISTORY.clear()
            for t in hist:
                TRADE_HISTORY.append(t)
            print(f"[STATE_PERSIST] TRADE_HISTORY 복원: {len(hist)}건")

        # 코인별 손실 기록 복원 (쿨다운 만료된 것은 제외)
        coin_loss = state.get("coin_loss_history", {})
        if coin_loss:
            now_ts = time.time()
            restored = 0
            with _COIN_LOSS_LOCK:
                for market, losses in coin_loss.items():
                    recent = [ts for ts in losses if now_ts - ts < COIN_LOSS_COOLDOWN]
                    if recent:
                        _COIN_LOSS_HISTORY[market] = recent
                        restored += 1
            print(f"[STATE_PERSIST] COIN_LOSS_HISTORY 복원: {restored}개 코인")

        # streak 복원
        streaks = state.get("streaks", {})
        if streaks:
            with _STREAK_LOCK:
                _lose_streak = streaks.get("lose", 0)
                _win_streak = streaks.get("win", 0)
                sus = streaks.get("suspend_until", 0)
                if sus > time.time():
                    _ENTRY_SUSPEND_UNTIL = sus
                    print(f"[STATE_PERSIST] 진입 금지 복원: {(sus - time.time()):.0f}초 남음")
                # 전략별 streak 복원
                _strat_lose = streaks.get("strat_lose", {})
                _strat_sus = streaks.get("strat_suspend", {})
                for k, v in _strat_lose.items():
                    _STRAT_LOSE_STREAK[k] = v
                for k, v in _strat_sus.items():
                    if v > time.time():
                        _STRAT_SUSPEND_UNTIL[k] = v
            _strat_info = " ".join(f"{k}={v}" for k, v in _STRAT_LOSE_STREAK.items())
            print(f"[STATE_PERSIST] streak 복원: 연패={_lose_streak} 연승={_win_streak} 전략별=[{_strat_info}]")

        # OPEN_POSITIONS 복원 (참고 정보 — 실제 잔고는 orphan_sync가 처리)
        positions = state.get("open_positions", {})
        if positions:
            with _POSITION_LOCK:
                for m, p in positions.items():
                    if m not in OPEN_POSITIONS:
                        p["restored"] = True  # 복원된 포지션 표시
                        OPEN_POSITIONS[m] = p
            print(f"[STATE_PERSIST] OPEN_POSITIONS 복원: {len(positions)}개 (orphan_sync에서 검증)")

        print(f"[STATE_PERSIST] ✅ 상태 복원 완료")
    except Exception as e:
        print(f"[STATE_PERSIST] 복원 실패: {e}")


# ============================================================
# 🔧 v7: MFE→Exit 자동 피드백 시스템
# 시그널별 평균 MFE 피크 시점/크기를 기반으로 trail activation, TP 자동 조정
# ============================================================
def mfe_feedback_exit_params(signal_tag: str, base_params: dict) -> dict:
    """
    시그널별 누적 MFE 통계를 기반으로 청산 파라미터 자동 조정
    - avg_peak_mfe × 0.6 → activation_pct (체크포인트)
    - avg_peak_mfe × 0.35 → trail_pct (트레일 간격)
    - median_peak_sec → max_bars 조정 (피크 이후 과도한 홀딩 방지)
    Returns: 조정된 exit_params (base_params 변형)
    """
    if not MFE_FEEDBACK_ENABLED:
        return base_params

    summary = get_signal_stats_summary(signal_tag)
    if not summary or summary.get("trades", 0) < MFE_FEEDBACK_MIN_TRADES:
        return base_params

    avg_mfe = summary.get("avg_mfe", 0)  # 소수 단위 (0.005 = 0.5%)
    if avg_mfe <= 0:
        return base_params

    # MFE 시계열에서 피크 MFE 값 찾기
    mfe_timeline = summary.get("avg_mfe_timeline", {})
    if mfe_timeline:
        peak_mfe_val = max(mfe_timeline.values())
        peak_mfe_sec = int(max(mfe_timeline, key=lambda k: mfe_timeline[k]))
    else:
        peak_mfe_val = avg_mfe
        peak_mfe_sec = summary.get("median_peak_sec", 60)

    # 피드백 파라미터 계산
    fb_activation = peak_mfe_val * MFE_FEEDBACK_ACTIVATION_RATIO
    fb_trail = peak_mfe_val * MFE_FEEDBACK_TRAIL_RATIO

    # 상하한 클램프
    fb_activation = max(MFE_FEEDBACK_MIN_ACTIVATION, min(MFE_FEEDBACK_MAX_ACTIVATION, fb_activation))
    fb_trail = max(MFE_FEEDBACK_MIN_TRAIL, min(MFE_FEEDBACK_MAX_TRAIL, fb_trail))

    # max_bars: 피크 시점 + 여유 (피크 후 60초 추가)
    fb_max_bars = max(30, min(120, (peak_mfe_sec + 60) // 60 * 60 // RECHECK_SEC))

    adjusted = base_params.copy()
    adjusted["activation_pct"] = round(fb_activation, 5)
    adjusted["trail_pct"] = round(fb_trail, 5)
    adjusted["max_bars"] = fb_max_bars
    adjusted["_mfe_feedback"] = True  # 피드백 적용 마커
    adjusted["_fb_peak_mfe"] = round(peak_mfe_val * 100, 3)  # 디버그용 (%)
    adjusted["_fb_peak_sec"] = peak_mfe_sec

    return adjusted


def get_adaptive_risk() -> float:
    """
    최근 승률 + streak 기반 RISK_PER_TRADE 가변 조정
    - 히스토리 10건 미만: 기본값 (streak만 적용)
    - winrate < 30% : 리스크 0.5배
    - winrate >= 50%: 리스크 1.2배
    - 🔧 연패 3회 이상: 리스크 0.85배 (방어적)
    - 🔧 연승 3회 이상: 리스크 1.15배 (공격적)
    """
    global _lose_streak, _win_streak  # 🔧 전역 참조 명시
    base_risk = RISK_PER_TRADE

    # 승률 기반 조정
    if len(TRADE_HISTORY) >= 10:
        wins = sum(1 for t in TRADE_HISTORY if t.get("win"))
        win_rate = wins / len(TRADE_HISTORY)

        if win_rate < 0.30:
            base_risk = RISK_PER_TRADE * 0.5
        elif win_rate < 0.38:
            base_risk = RISK_PER_TRADE * 0.7  # 🔧 수익개선: 30~38% 구간 리스크 축소 (손실 누적 주범 구간)
        elif win_rate >= 0.50:
            base_risk = RISK_PER_TRADE * 1.2

    # 🔧 FIX H1: streak 읽기도 락으로 보호 (스레드 안전 읽기)
    with _STREAK_LOCK:
        _ls = _lose_streak
        _ws = _win_streak
    # 🔧 FIX: streak 기반 추가 조정 (연패 시 줄이고, 연승 시 늘림)
    if _ls >= 3:
        base_risk *= 0.85  # 연패 3회 → 리스크 15% 감소
    elif _ws >= 3:
        base_risk *= 1.15  # 연승 3회 → 리스크 15% 증가

    # 🔧 FIX: 리스크 상한 (연승 시에도 과도한 리스크 방지)
    # - 최대 기본값의 1.5배 또는 절대값 5% 중 작은 값
    MAX_RISK_MULTIPLIER = 1.5
    MAX_RISK_ABSOLUTE = 0.05  # 5%
    base_risk = min(base_risk, RISK_PER_TRADE * MAX_RISK_MULTIPLIER, MAX_RISK_ABSOLUTE)

    return base_risk

def _make_auth_headers(query: dict = None):
    payload = {
        'access_key': UPBIT_ACCESS_KEY,
        'nonce': str(uuid.uuid4()),
    }
    if query:
        q = urlencode(query).encode()
        m = hashlib.sha512()
        m.update(q)
        payload['query_hash'] = m.hexdigest()
        payload['query_hash_alg'] = 'SHA512'
    # 🔧 FIX: 알고리즘 지정 + PyJWT v1/v2 호환 디코딩
    jwt_token = jwt.encode(payload, UPBIT_SECRET_KEY, algorithm="HS256")
    if isinstance(jwt_token, bytes):  # PyJWT v1 대비
        jwt_token = jwt_token.decode("utf-8")
    return {"Authorization": f"Bearer {jwt_token}"}


def upbit_private_get(path, params=None, timeout=7):
    """🔧 FIX C1: 429/500 재시도 추가 (최대 3회, 지수 백오프)"""
    url = f"https://api.upbit.com{path}"
    _max_retries = 3
    for _attempt in range(_max_retries + 1):
        headers = _make_auth_headers(params or {})
        _throttle()
        try:
            with _SESSION_REFRESH_LOCK:
                sess = SESSION  # 🔧 FIX: 락으로 보호 (세션 리프레시 레이스 방지)
            r = sess.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503) and _attempt < _max_retries:
                _wait = 0.5 * (2 ** _attempt)  # 0.5s, 1s, 2s
                print(f"[API_RETRY] GET {path} → {r.status_code}, {_wait:.1f}초 후 재시도 ({_attempt+1}/{_max_retries})")
                time.sleep(_wait)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError, ValueError) as e:
            # 🔧 FIX: HTTPError 추가 (400/401/403 등에서 크래시 방지)
            if _attempt < _max_retries:
                _wait = 0.5 * (2 ** _attempt)
                print(f"[API_RETRY] GET {path} → {type(e).__name__}, {_wait:.1f}초 후 재시도 ({_attempt+1}/{_max_retries})")
                time.sleep(_wait)
                continue
            raise


def upbit_private_post(path, body=None, timeout=7):
    """🔧 FIX C1: 429/500 재시도 추가 (최대 3회, 지수 백오프) — 매도 실패 = 돈 잃음 방지"""
    url = f"https://api.upbit.com{path}"
    body = body or {}
    _max_retries = 3
    # 🔧 FIX: 주문 POST는 500/502/503 재시도 금지 (멱등성 없음 → 중복 주문 위험)
    _is_order = (path == "/v1/orders")
    for _attempt in range(_max_retries + 1):
        headers = _make_auth_headers(body)
        _throttle()
        try:
            with _SESSION_REFRESH_LOCK:
                sess = SESSION  # 🔧 FIX: 락으로 보호 (세션 리프레시 레이스 방지)
            r = sess.post(url, headers=headers, json=body, timeout=timeout)
            # 429: 항상 재시도 (rate limit = 미처리 보장)
            # 500/502/503: 주문이면 재시도 금지 (이미 처리됐을 수 있음)
            _retry_codes = (429,) if _is_order else (429, 500, 502, 503)
            if r.status_code in _retry_codes and _attempt < _max_retries:
                _wait = 0.5 * (2 ** _attempt)
                print(f"[API_RETRY] POST {path} → {r.status_code}, {_wait:.1f}초 후 재시도 ({_attempt+1}/{_max_retries})")
                time.sleep(_wait)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError, ValueError) as e:
            # 🔧 FIX: HTTPError 추가 (400/401/403 등에서 크래시 방지)
            if _attempt < _max_retries:
                _wait = 0.5 * (2 ** _attempt)
                print(f"[API_RETRY] POST {path} → {type(e).__name__}, {_wait:.1f}초 후 재시도 ({_attempt+1}/{_max_retries})")
                time.sleep(_wait)
                continue
            raise


def get_order_result(uuid_str, timeout_sec=10.0):
    """
    주문 uuid 로 최종 체결 결과 조회
    - done / cancel 상태가 되거나 timeout 될 때까지 polling
    🔧 FIX: wait에서 종료하면 체결 전에 끊김 → done/cancel만 종료
    """
    deadline = time.time() + timeout_sec
    last = None
    while time.time() < deadline:
        try:
            od = upbit_private_get("/v1/order", {"uuid": uuid_str})
            last = od
            state = od.get("state")
            # 🔧 FIX: done/cancel에서만 종료, wait는 계속 대기
            if state in ("done", "cancel"):
                break
        except Exception as e:
            last = None
        time.sleep(0.25)
    return last


def get_account_info():
    """업비트 계좌(잔고) 조회"""
    try:
        return upbit_private_get("/v1/accounts")
    except Exception as e:
        print("[AUTO] 계좌 조회 실패:", e)
        return []


def calc_position_size(entry_price, stop_price, total_equity, risk_pct):
    """
    손절가 기준으로 포지션 크기 계산
    - total_equity * risk_pct 만큼만 최대 손실 허용
    """
    # 🔧 CRITICAL: 비정상 entry_price 가드 (분모 0 방지)
    if entry_price is None or entry_price <= 0:
        return 0.0

    risk_krw = total_equity * risk_pct
    # 🔧 FIX: DYN_SL_MIN과 동기화 (과위험 방지)
    min_sl_pct = DYN_SL_MIN  # 전역 손절폭과 일치

    # 🔧 FIX: stop_price가 None이거나 entry_price 이상이면 보정
    if stop_price is None or stop_price <= 0 or stop_price >= entry_price:
        stop_price = entry_price * (1 - min_sl_pct)

    per_unit_loss = max(entry_price - stop_price,
                        entry_price * min_sl_pct)

    # 🔧 CRITICAL: 분모 안전 가드
    if per_unit_loss <= 0:
        return 0.0

    qty = risk_krw / per_unit_loss
    return max(qty, 0.0)


def place_market_buy(market, krw_amount):
    """KRW 기준 시장가 매수 (ord_type=price)"""
    krw_amount = int(krw_amount)
    # 🔧 FIX: 최소주문금액 가드 (400 에러 방지)
    if krw_amount < 5000:
        print(f"[BUY_ERR] {market} 최소주문금액 미달: {krw_amount}원 < 5000원")
        return None
    body = {
        "market": market,
        "side": "bid",
        "ord_type": "price",
        "price": str(krw_amount)
    }
    return upbit_private_post("/v1/orders", body)


def upbit_private_delete(path, params=None, timeout=7):
    """업비트 DELETE API (주문 취소용) — 재시도 포함"""
    url = f"https://api.upbit.com{path}"
    params = params or {}
    _max_retries = 3
    for _attempt in range(_max_retries + 1):
        headers = _make_auth_headers(params)
        _throttle()
        try:
            with _SESSION_REFRESH_LOCK:
                sess = SESSION  # 🔧 FIX: 락으로 보호 (세션 리프레시 레이스 방지)
            r = sess.delete(url, headers=headers, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503) and _attempt < _max_retries:
                _wait = 0.5 * (2 ** _attempt)
                print(f"[API_RETRY] DELETE {path} → {r.status_code}, {_wait:.1f}초 후 재시도 ({_attempt+1}/{_max_retries})")
                time.sleep(_wait)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError, ValueError) as e:
            # 🔧 FIX: HTTPError 추가 (400/401/403 등에서 크래시 방지)
            if _attempt < _max_retries:
                _wait = 0.5 * (2 ** _attempt)
                print(f"[API_RETRY] DELETE {path} → {type(e).__name__}, {_wait:.1f}초 후 재시도 ({_attempt+1}/{_max_retries})")
                time.sleep(_wait)
                continue
            raise


def cancel_order(uuid_str):
    """미체결 주문 취소 (업비트 DELETE /v1/order)"""
    try:
        return upbit_private_delete("/v1/order", {"uuid": uuid_str})
    except Exception as e:
        print(f"[CANCEL_ERR] {uuid_str}: {e}")
        return None


def place_limit_buy(market, price, volume):
    """지정가 매수 (ord_type=limit)"""
    volume = round(float(volume), 8)
    price = float(price)
    if price <= 0 or volume <= 0:
        print(f"[LIMIT_BUY_ERR] {market} 가격/수량 무효: price={price}, vol={volume}")
        return None
    if price * volume < 5000:
        print(f"[LIMIT_BUY_ERR] {market} 최소주문금액 미달: {price*volume:.0f}원")
        return None
    # 🔧 FIX: 호가 단위에 맞춘 가격 포맷 (int() 절삭 → 저가코인 오류 방지)
    _tick = upbit_tick_size(price)
    _rounded = round(round(price / _tick) * _tick, 8)  # 호가 단위로 반올림
    _price_str = f"{_rounded:.8f}".rstrip('0').rstrip('.') if _tick < 1 else str(int(_rounded))
    body = {
        "market": market,
        "side": "bid",
        "ord_type": "limit",
        "price": _price_str,
        "volume": f"{volume:.8f}",
    }
    return upbit_private_post("/v1/orders", body)


def hybrid_buy(market, krw_amount, ob_data=None, timeout_sec=1.2):
    """
    하이브리드 매수: 지정가 → 타임아웃 → 시장가 전환 (슬리피지 절감)
    1) 최우선 매도호가(ask1) 지정가로 주문
    2) timeout_sec 동안 체결 대기
    3) 미체결/부분체결 시 → 취소 → 잔여분 시장가 매수
    """
    ask1_price = None
    bid1_price = None
    try:
        if ob_data:
            units = ob_data.get("raw", {}).get("orderbook_units", [])
            if units:
                ask1_price = float(units[0].get("ask_price", 0))
                bid1_price = float(units[0].get("bid_price", 0))
    except Exception:
        pass

    if not ask1_price or ask1_price <= 0:
        print(f"[HYBRID] {market} 호가 정보 없음 → 시장가 폴백")
        return place_market_buy(market, krw_amount)

    buy_volume = krw_amount / ask1_price
    buy_volume = round(buy_volume, 8)

    if buy_volume <= 0 or ask1_price * buy_volume < 5000:
        print(f"[HYBRID] {market} 주문금액 부족 → 시장가 폴백")
        return place_market_buy(market, krw_amount)

    try:
        limit_res = place_limit_buy(market, ask1_price, buy_volume)
        if not limit_res or not isinstance(limit_res, dict):
            print(f"[HYBRID] {market} 지정가 주문 실패 → 시장가 폴백")
            return place_market_buy(market, krw_amount)

        order_uuid = limit_res.get("uuid")
        if not order_uuid:
            print(f"[HYBRID] {market} 지정가 UUID 없음 → 시장가 폴백")
            return place_market_buy(market, krw_amount)

        print(f"[HYBRID] {market} 지정가 매수 @ {ask1_price:,.0f}원 × {buy_volume:.6f} | 대기 {timeout_sec}초")

    except Exception as e:
        print(f"[HYBRID] {market} 지정가 예외: {e} → 시장가 폴백")
        return place_market_buy(market, krw_amount)

    deadline = time.time() + timeout_sec
    od = None
    while time.time() < deadline:
        time.sleep(0.3)
        try:
            od = upbit_private_get("/v1/order", {"uuid": order_uuid})
            state = od.get("state", "")
            if state == "done":
                print(f"[HYBRID] {market} 지정가 전량체결!")
                return limit_res
            if state == "cancel":
                break
        except Exception as _poll_e:
            print(f"[HYBRID] {market} 주문조회 실패: {_poll_e}")

    cancel_order(order_uuid)  # 🔧 FIX: 취소 먼저 → 체결량 확정 후 잔여 계산 (레이스 방지)

    # 🔧 FIX: 취소 후 최종 체결량 재조회 (취소 전 od는 stale → 과잉매수 위험)
    executed_vol = 0.0
    try:
        # 🔧 FIX: upbit_private_get 직접 호출 (get_order 미존재 NameError 수정)
        od_final = upbit_private_get("/v1/order", {"uuid": order_uuid})
        if od_final:
            executed_vol = float(od_final.get("executed_volume") or "0")
        elif od:
            executed_vol = float(od.get("executed_volume") or "0")  # fallback
    except Exception as _vol_e:
        print(f"[HYBRID] {market} 체결량 파싱 실패: {_vol_e}")
        if od:
            try:
                executed_vol = float(od.get("executed_volume") or "0")
            except Exception:
                pass

    remaining_vol = buy_volume - executed_vol
    remaining_krw = int(remaining_vol * ask1_price)

    if executed_vol > 0:
        print(f"[HYBRID] {market} 부분체결 {executed_vol:.6f} / 잔여 {remaining_vol:.6f}")

    if remaining_krw >= 5000:
        # 🔧 수익개선: 시장가 전환 전 슬리피지 가드 — 동적 스프레드 기반 임계값
        try:
            _cur_js = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": market})
            _cur_price = _cur_js[0].get("trade_price", 0) if _cur_js else 0
            # 🔧 업비트 실데이터 기반 동적 슬리피지: 평균 스프레드 0.26%, 중위 0.22%
            # BTC/ETH=0.01~0.05%, 중형=0.1~0.3%, 소형=0.3~0.8% → 범위 0.15%~0.5%
            _slip_threshold = 1.0
            if ask1_price and bid1_price and ask1_price > 0:
                _spread_pct = (ask1_price - bid1_price) / ask1_price
                _dyn_threshold = max(0.0015, min(0.005, _spread_pct * 2))
                _slip_threshold = 1.0 + _dyn_threshold
            if _cur_price and ask1_price and _cur_price > ask1_price * _slip_threshold:
                _slip = (_cur_price / ask1_price - 1) * 100
                print(f"[HYBRID] {market} 슬리피지 가드 발동: 현재가 {_cur_price:,.0f} > ask1 {ask1_price:,.0f} (+{_slip:.2f}%) → 시장가 포기")
                if executed_vol > 0:
                    return limit_res
                return None
        except Exception:
            pass  # 조회 실패 시 기존 로직 진행
        print(f"[HYBRID] {market} 잔여분 시장가 매수 {remaining_krw:,}원")
        try:
            place_market_buy(market, remaining_krw)
            return limit_res
        except Exception as e:
            print(f"[HYBRID] {market} 잔여분 시장가 실패: {e}")
            if executed_vol > 0:
                return limit_res
            return None
    else:
        if executed_vol > 0:
            print(f"[HYBRID] {market} 잔여분 소액({remaining_krw}원) → 부분체결만 유지")
            return limit_res
        print(f"[HYBRID] {market} 체결 0 + 잔여 소액 → 실패")
        return None


def place_market_sell(market, volume, price_hint=None):
    """
    수량 기준 시장가 매도
    🔧 FIX: 수량 정밀도 보정 + 최소주문금액 항상 체크
    """
    # 수량 정밀도 보정 (8자리까지, 업비트 표준)
    volume = round(float(volume), 8)

    # 🔧 FIX: price_hint 없으면 현재가 조회 (최소금액 체크 우회 방지)
    if price_hint is None:
        try:
            cur_js = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": market})
            price_hint = cur_js[0].get("trade_price", 0) if cur_js else 0
        except Exception:
            price_hint = 0

    # 최소 주문금액 체크 (5,000원)
    # 🔧 FIX: 4500원 이상이면 시도 허용 (price_hint 지연/오차 감안, 거래소가 최종 판단)
    if price_hint and price_hint > 0:
        est_value = volume * price_hint
        if est_value < 4500:
            raise ValueError(f"최소주문금액 미달: {est_value:.0f}원 < 5000원")
        elif est_value < 5000:
            print(f"[SELL_BORDERLINE] {volume:.8f} × {price_hint:,.0f} = {est_value:,.0f}원 (5000원 미만이지만 시도)")

    body = {
        "market": market,
        "side": "ask",
        "ord_type": "market",
        "volume": f"{volume:.8f}"  # 소수점 8자리 고정
    }
    return upbit_private_post("/v1/orders", body)

def get_actual_balance(market):
    """실제 매도 가능량 조회 (balance만, locked 제외)"""
    try:
        currency = market.replace("KRW-", "")
        accounts = get_account_info()
        if not accounts:
            return -1.0  # API 실패
        for a in accounts:
            if a.get("currency") == currency:
                return float(a.get("balance", "0"))
        return 0.0  # 목록에 없음 = 진짜 0
    except Exception:
        return -1.0  # 조회 실패

def get_balance_with_locked(market, retries=2):
    """
    실제 보유량 + 주문 대기량 조회 (청산 완료 판정용)
    🔧 FIX: API 오류 시 재시도 (단발 조회로 0 오판 → 유령 오탐 방지)
    - retries: 재시도 횟수 (기본 2회 = 총 3회 시도)
    """
    currency = market.replace("KRW-", "")
    last_err = None
    for attempt in range(retries + 1):
        try:
            accounts = get_account_info()
            if accounts:
                for a in accounts:
                    if a.get("currency") == currency:
                        balance = float(a.get("balance", "0"))
                        locked = float(a.get("locked", "0"))
                        return balance + locked
                # 계정 목록에서 찾지 못함 = 진짜 0
                return 0.0
            # accounts가 비어있으면 API 오류일 수 있음 → 재시도
            last_err = "accounts empty"
        except Exception as e:
            last_err = str(e)
        # 재시도 전 짧은 대기
        if attempt < retries:
            time.sleep(0.5)
    # 모든 재시도 실패 → -1 반환 (0과 구분, 호출부에서 처리)
    print(f"[BALANCE_ERR] {market} 잔고 조회 실패 ({retries+1}회 시도): {last_err}")
    return -1.0  # 🔧 -1 = 조회 실패 (0 = 진짜 없음과 구분)

def sell_all(market):
    """실제 보유량 전량 매도 (1원 찌꺼기 방지). 성공 시 주문결과 dict, 실패 시 None 반환."""
    actual = get_actual_balance(market)
    # FIX [H5]: -1(API 실패)과 0(잔고 없음) 모두 방어
    if actual < 0:
        print(f"[SELL_ALL] {market} 잔고 조회 실패 (API 오류) → 매도 보류")
        return None
    if actual <= 0:
        print(f"[SELL_ALL] {market} 보유량 없음")
        return None
    # 🔧 현재가 조회
    try:
        cur_js = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": market})
        cur_price = cur_js[0].get("trade_price", 0) if cur_js and len(cur_js) > 0 else 0  # 🔧 FIX: 빈 배열 방어
    except Exception:
        cur_price = None
    print(f"[SELL_ALL] {market} 실제 보유량 {actual:.8f} 전량 매도")
    # 🔧 FIX: 매도 실패 시 예외 처리 (최소주문금액 미만 등)
    try:
        return place_market_sell(market, actual, price_hint=cur_price)
    except Exception as e:
        print(f"[SELL_ALL_ERR] {market}: {e}")
        # 최소주문금액 미만이면 소액 잔여로 간주
        if "최소주문금액" in str(e) or "5000" in str(e):
            print(f"[SELL_ALL] {market} 최소주문금액 미만 → 소액 잔여 보유")
        return None


def sync_orphan_positions():
    """
    🔧 유령 포지션 동기화
    - 업비트에 잔고가 있지만 OPEN_POSITIONS에 없는 포지션 감지
    - 감지된 포지션을 OPEN_POSITIONS에 추가하고 모니터링 시작
    - 세션 내 1회만 처리 (반복 알림 방지)
    """
    global _LAST_ORPHAN_SYNC, _PREV_SYNC_MARKETS, _ORPHAN_FIRST_SYNC

    now = time.time()
    if now - _LAST_ORPHAN_SYNC < ORPHAN_SYNC_INTERVAL:
        return  # 아직 동기화 시간 안됨
    _LAST_ORPHAN_SYNC = now

    try:
        accounts = get_account_info()
        if not accounts:
            print("[ORPHAN_SYNC] 계좌 조회 실패 또는 비어있음")
            return

        # 🔧 현재 잔고 있는 마켓 수집 (청산된 건 _ORPHAN_HANDLED에서 제거)
        current_markets = set()

        for acc in accounts:
            currency = acc.get("currency", "")
            if currency == "KRW":
                continue

            balance = float(acc.get("balance", "0"))
            avg_buy_price = float(acc.get("avg_buy_price", "0"))

            # 최소 금액 이상만 (찌꺼기 제외)
            if balance * avg_buy_price < 5000:
                continue

            market = f"KRW-{currency}"
            current_markets.add(market)

            # 이미 처리한 유령 포지션이면 스킵 (반복 알림 방지)
            if market in _ORPHAN_HANDLED:
                continue

            with _POSITION_LOCK:
                if market in OPEN_POSITIONS:
                    continue  # 이미 추적 중

            # 🔧 FIX: 모니터 스레드가 살아있으면 유령 아님 (정상 매수 후 모니터링 중)
            with _MONITOR_LOCK:
                _mon_thread = _ACTIVE_MONITORS.get(market)
                if _mon_thread is not None and isinstance(_mon_thread, threading.Thread) and _mon_thread.is_alive():
                    print(f"[ORPHAN] {market} 모니터 스레드 활성 → 유령 아님, 스킵")
                    continue

            # 🔧 FIX: _CLOSING_MARKETS에 있으면 청산 진행 중 → 유령 아님
            with _POSITION_LOCK:
                if market in _CLOSING_MARKETS:
                    print(f"[ORPHAN] {market} 청산 진행 중 → 유령 아님, 스킵")
                    continue

            # 🔧 FIX: 이전 동기화에 없던 마켓은 스킵 (신규 매수 오탐 방지)
            # 단, 봇 시작 직후 첫 sync에서는 대기 없이 바로 처리
            # (재시작이므로 기존 포지션이 전부 유령 — 오탐 가능성 없음)
            if not _ORPHAN_FIRST_SYNC and market not in _PREV_SYNC_MARKETS:
                print(f"[ORPHAN] {market} 신규 발견 → 다음 사이클까지 대기 (오탐 방지)")
                continue

            # 🔧 FIX: 최근 10분 내 매수 주문이 있으면 스킵 (다중 프로세스 오탐 방지)
            # - 한 프로세스에서 매수, 다른 프로세스에서 sync 시 오탐 발생 가능
            # - 단, 봇 재시작 첫 sync에서는 건너뜀 (기존 포지션 = 당연히 매수 이력 있음)
            skip_recent_buy = False
            if not _ORPHAN_FIRST_SYNC:
                try:
                    recent_orders = upbit_private_get("/v1/orders", {
                        "market": market,
                        "state": "done",
                        "limit": 5
                    })
                    if recent_orders:
                        for order in recent_orders:
                            if order.get("side") == "bid":  # 매수 주문
                                created_str = order.get("created_at", "")
                                if created_str:
                                    try:
                                        order_time = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                                        now_utc = datetime.now(timezone.utc)
                                        age_sec = (now_utc - order_time).total_seconds()
                                        if age_sec < 600:  # 10분 이내 매수
                                            print(f"[ORPHAN] {market} 최근 매수 주문 발견 ({age_sec:.0f}초 전) → 스킵")
                                            skip_recent_buy = True
                                            break
                                    except Exception as parse_err:
                                        print(f"[ORPHAN] {market} 주문시간 파싱 에러: {parse_err}")
                except Exception as orders_err:
                    print(f"[ORPHAN] {market} 주문내역 조회 에러: {orders_err}")

            if skip_recent_buy:
                continue  # 🔧 다음 마켓으로 (유령 감지 스킵)

            # 🔧 FIX: 봇 내부 최근 매수 체크 (600초 내 매수면 유령 아님)
            # 300초 → 600초로 증가: 매수 후 모니터→청산→잔고지연까지 충분한 보호
            with _RECENT_BUY_LOCK:
                last_buy_ts = _RECENT_BUY_TS.get(market, 0)
            if now - last_buy_ts < 600:
                print(f"[ORPHAN] {market} 최근 매수 ({now - last_buy_ts:.0f}초 전) → 유령 아님, 스킵")
                continue

            # 🔥 유령 포지션 발견! (2사이클 연속 존재 + OPEN_POSITIONS에 없음)
            print(f"[ORPHAN] {market} 유령 포지션 발견! 잔고={balance:.4f} 평단={avg_buy_price:.2f}")

            # 🔧 FIX: 개별 orphan 처리를 try/except로 격리 (한 종목 에러가 나머지 종목 처리를 막지 않게)
            try:
                # 현재가 조회
                try:
                    cur_js = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": market})
                    cur_price = cur_js[0].get("trade_price", avg_buy_price) if cur_js and len(cur_js) > 0 else avg_buy_price  # 🔧 FIX: 빈 배열 방어
                except Exception:
                    cur_price = avg_buy_price

                # 수익률 계산
                pnl_pct = ((cur_price / avg_buy_price) - 1.0) * 100 if avg_buy_price > 0 else 0

                # 🔧 FIX: OPEN_POSITIONS에 추가 전 한번 더 확인 (race condition 방지)
                with _POSITION_LOCK:
                    if market in OPEN_POSITIONS:
                        print(f"[ORPHAN] {market} 이미 OPEN_POSITIONS에 있음 (race 방지) → 스킵")
                        continue

                # 🔧 FIX: API/계산을 락 밖에서 수행 (데드락 방지 — 락 안 네트워크 호출 금지)
                _orphan_c1 = get_minutes_candles(1, market, 20)
                _orphan_stop, _orphan_sl_pct_val, _ = dynamic_stop_loss(avg_buy_price, _orphan_c1, market=market)
                _orphan_atr = atr14_from_candles(_orphan_c1, 14) if _orphan_c1 else None
                _orphan_atr_pct = (_orphan_atr / avg_buy_price * 100) if (_orphan_atr and avg_buy_price > 0) else 0.0

                with _POSITION_LOCK:
                    if market in OPEN_POSITIONS:
                        print(f"[ORPHAN] {market} 계산 중 다른 곳에서 추가됨 → 스킵")
                        continue
                    OPEN_POSITIONS[market] = {
                        "state": "open",
                        "entry_price": avg_buy_price,
                        "volume": balance,
                        "stop": _orphan_stop,
                        "sl_pct": _orphan_sl_pct_val,
                        "entry_atr_pct": round(_orphan_atr_pct, 4),
                        "entry_mode": "orphan",
                        "ts": now,
                        "entry_ts": now,
                        "orphan_detected": True,
                        # 🔧 FIX: 튜닝 데이터 필드 초기화 (청산 알람 0값 방지)
                        "entry_pstd": 0.0,
                        "mfe_pct": 0.0,
                        "mae_pct": 0.0,
                        "mfe_sec": 0,
                        "trail_dist": 0.0,
                        "trail_stop_pct": 0.0,
                    }

                # 텔레그램 알림
                tg_send(
                    f"👻 유령 포지션 감지!\n"
                    f"• {market}\n"
                    f"• 평단: {fmt6(avg_buy_price)}원\n"
                    f"• 현재가: {fmt6(cur_price)}원 ({pnl_pct:+.2f}%)\n"
                    f"• 수량: {balance:.6f}\n"
                    f"• SL: {_orphan_sl_pct_val*100:.2f}% | ATR: {_orphan_atr_pct:.3f}%\n"
                    f"→ 모니터링 시작 (ATR 손절 적용)"
                )

                # 🔧 처리 완료 표시 (반복 알림 방지) - 먼저 표시
                with _ORPHAN_LOCK:
                    _ORPHAN_HANDLED.add(market)

                # 🔧 FIX: orphan 즉시 손절 → DYN_SL_MIN 연동 (0.6% 하드코딩 제거)
                # - 기존: -0.6% 고정 → 정상 눌림도 강제청산 (승률 하락)
                # - 수정: DYN_SL_MIN(1.0%) 기준으로 판단 (본전략 SL과 일관)
                _orphan_sl_pct = DYN_SL_MIN * 100  # 1.0%
                if pnl_pct <= -_orphan_sl_pct:
                    print(f"[ORPHAN] {market} 이미 손절선 이하 ({pnl_pct:.2f}%) → 즉시 청산")
                    # 🔧 FIX: 즉시 손절을 별도 스레드에서 실행 (블로킹 방지 → 다른 종목 처리 지연 제거)
                    def _orphan_close_now(_m=market, _pnl=pnl_pct):
                        try:
                            close_auto_position(_m, f"유령포지션 손절 | 감지 즉시 {_pnl:.2f}%")
                        except Exception as e:
                            print(f"[ORPHAN_CLOSE_ERR] {_m}: {e}")
                            with _POSITION_LOCK:
                                OPEN_POSITIONS.pop(_m, None)
                            with _ORPHAN_LOCK:
                                _ORPHAN_HANDLED.discard(_m)
                    threading.Thread(target=_orphan_close_now, daemon=True).start()
                else:
                    # 🔧 FIX: 모니터링 스레드 중복 방지 + 죽은 스레드 감지
                    with _MONITOR_LOCK:
                        existing_thread = _ACTIVE_MONITORS.get(market)
                        if existing_thread is not None:
                            # 🔧 FIX: 스레드가 살아있는지 확인 (is_alive)
                            if isinstance(existing_thread, threading.Thread) and existing_thread.is_alive():
                                print(f"[ORPHAN_SKIP] {market} 이미 모니터링 중 → 스레드 생성 스킵")
                                continue
                            # 죽은 스레드면 정리하고 새로 시작
                            print(f"[ORPHAN_CLEANUP] {market} 죽은 모니터 스레드 정리")
                            _ACTIVE_MONITORS.pop(market, None)

                    # 🔧 FIX: 박스 포지션인지 감지 (박스모니터 복구용)
                    _is_box_orphan = False
                    _box_orphan_info = None
                    with _BOX_LOCK:
                        # 1) _BOX_WATCHLIST에 아직 있는 경우 (워치리스트는 남아있지만 모니터 죽은 경우)
                        _bw = _BOX_WATCHLIST.get(market)
                        if _bw and _bw.get("state") == "holding":
                            _is_box_orphan = True
                            _box_orphan_info = {
                                "box_high": _bw.get("box_high", 0),
                                "box_low": _bw.get("box_low", 0),
                                "box_tp": _bw.get("box_high", 0),  # TP = 상단
                                "box_stop": _bw.get("box_low", 0) * 0.995,  # SL = 하단 -0.5%
                                "range_pct": _bw.get("range_pct", 0),
                            }
                        # 2) _BOX_LAST_EXIT에 최근 기록 (1800초 이내) → 박스 매도 실패로 유령화
                        elif market in _BOX_LAST_EXIT and (now - _BOX_LAST_EXIT[market]) < 1800:
                            _is_box_orphan = True
                    # 3) 박스 정보 없으면 실시간 박스 감지 시도 (BOX_LAST_EXIT 이력만 있는 경우 포함)
                    if not _box_orphan_info:
                        try:
                            _orphan_c1_box = get_minutes_candles(1, market, 60)
                            if _orphan_c1_box:
                                _box_is, _box_det = detect_box_range(_orphan_c1_box)
                                if _box_is and _box_det:
                                    _is_box_orphan = True
                                    _box_orphan_info = {
                                        "box_high": _box_det["box_high"],
                                        "box_low": _box_det["box_low"],
                                        "box_tp": _box_det["box_high"],
                                        "box_stop": _box_det["box_low"] * 0.995,
                                        "range_pct": _box_det.get("range_pct", 0),
                                    }
                        except Exception:
                            pass

                    # 모니터링 스레드 시작
                    def _orphan_monitor(m, entry_price, _is_box=_is_box_orphan, _box_info=_box_orphan_info):
                        try:
                            # 🔧 FIX: 박스 유령 포지션 → box_monitor_position으로 복구 (시간만료 없음)
                            if _is_box and _box_info:
                                with _POSITION_LOCK:
                                    _opos = OPEN_POSITIONS.get(m, {})
                                    _opos["strategy"] = "box"
                                    if m in OPEN_POSITIONS:
                                        OPEN_POSITIONS[m] = _opos
                                _orphan_vol = _opos.get("volume", 0)
                                if _orphan_vol <= 0:
                                    _orphan_vol = get_balance_with_locked(m)
                                print(f"[ORPHAN] 📦 {m} 박스 포지션 복구 → box_monitor_position 시작")
                                tg_send(f"📦 {m} 유령 → 박스 모니터 복구\n• 박스: {fmt6(_box_info['box_low'])}~{fmt6(_box_info['box_high'])}")
                                box_monitor_position(m, entry_price, _orphan_vol, _box_info)
                                return

                            # 일반 유령 포지션 → 기존 로직
                            # 🔧 FIX: dummy_pre를 실제 데이터로 보강 (기존: 모든 파라미터 0 → 모니터링 무력화)
                            # OPEN_POSITIONS에 저장된 원본 데이터 복원 시도
                            with _POSITION_LOCK:
                                _orphan_pos = OPEN_POSITIONS.get(m, {})
                            _orphan_signal_type = _orphan_pos.get("signal_type", "normal")
                            _orphan_trade_type = _orphan_pos.get("trade_type", "scalp")
                            _orphan_signal_tag = _orphan_pos.get("signal_tag", "유령복구")
                            # 실시간 호가/틱 데이터 조회
                            _orphan_ticks = get_recent_ticks(m, 100) or []
                            _orphan_t15 = micro_tape_stats_from_ticks(_orphan_ticks, 15) if _orphan_ticks else {
                                "buy_ratio": 0.5, "krw": 0, "n": 0, "krw_per_sec": 0
                            }
                            _orphan_ob_raw = safe_upbit_get("https://api.upbit.com/v1/orderbook", {"markets": m})
                            _orphan_ob = {"depth_krw": 10_000_000}
                            if _orphan_ob_raw and len(_orphan_ob_raw) > 0:
                                try:
                                    _units = _orphan_ob_raw[0].get("orderbook_units", [])
                                    _depth = sum(u.get("ask_size", 0) * u.get("ask_price", 0) + u.get("bid_size", 0) * u.get("bid_price", 0) for u in _units[:5])
                                    _orphan_ob = {"depth_krw": _depth, "raw": _orphan_ob_raw[0]}
                                except Exception:
                                    pass
                            dummy_pre = {
                                "price": entry_price,
                                "ob": _orphan_ob,
                                "tape": _orphan_t15,
                                "ticks": _orphan_ticks,
                                "signal_type": _orphan_signal_type,
                                "trade_type": _orphan_trade_type,
                                "signal_tag": _orphan_signal_tag,
                            }
                            remonitor_until_close(m, entry_price, dummy_pre, tight_mode=False)
                        except Exception as e:
                            print(f"[ORPHAN_ERR] {m} 모니터링 에러: {e}")
                            # 🔧 FIX: 예외 발생 시 알람 + 잔고 확인 후 정리
                            try:
                                actual = get_balance_with_locked(m)
                                # 🔧 FIX: -1 = 조회 실패 → 포지션 유지 (오탐 방지)
                                if actual < 0:
                                    tg_send(f"⚠️ {m} 유령포지션 오류 (잔고 조회 실패)\n• 예외: {e}\n• 포지션 유지")
                                elif actual <= 1e-12:
                                    tg_send(f"⚠️ {m} 유령포지션 오류 (이미 청산됨)\n• 예외: {e}")
                                    with _POSITION_LOCK:
                                        OPEN_POSITIONS.pop(m, None)
                                    with _ORPHAN_LOCK:
                                        _ORPHAN_HANDLED.discard(m)
                                else:
                                    tg_send(f"🚨 {m} 유령포지션 오류 → 청산 시도\n• 예외: {e}")
                                    close_auto_position(m, f"유령모니터링예외 | {e}")
                            except Exception:
                                tg_send(f"🚨 {m} 유령포지션 오류\n• 예외: {e}")
                        finally:
                            # 🔧 FIX: 모니터링 종료 시 활성 목록에서 제거
                            with _MONITOR_LOCK:
                                _ACTIVE_MONITORS.pop(m, None)

                    t = threading.Thread(target=_orphan_monitor, args=(market, avg_buy_price), daemon=True)
                    t.start()
                    # 🔧 FIX: 스레드 객체 저장 (ident 대신)
                    with _MONITOR_LOCK:
                        _ACTIVE_MONITORS[market] = t
                    print(f"[ORPHAN] {market} 모니터링 스레드 시작")

            except Exception as _orphan_err:
                # 🔧 FIX: 개별 orphan 처리 에러 격리 (한 종목 에러 → 나머지 종목 계속 처리)
                print(f"[ORPHAN_PROCESS_ERR] {market} 개별 처리 실패: {_orphan_err}")
                # 에러 발생 시 해당 종목의 pending 상태 정리
                with _POSITION_LOCK:
                    _err_pos = OPEN_POSITIONS.get(market)
                    if _err_pos and _err_pos.get("orphan_detected"):
                        OPEN_POSITIONS.pop(market, None)
                with _ORPHAN_LOCK:
                    _ORPHAN_HANDLED.discard(market)

        # 🔧 청산된 포지션은 _ORPHAN_HANDLED에서 제거 (재매수 시 다시 감지 가능)
        # 🔧 FIX: 락 보호 (compound set 연산 원자성 보장)
        with _ORPHAN_LOCK:
            closed_markets = _ORPHAN_HANDLED - current_markets
            for m in closed_markets:
                _ORPHAN_HANDLED.discard(m)

        # 🔧 FIX: _RECENT_BUY_TS 오래된 항목 정리 (메모리 누수 방지)
        _now_cleanup = time.time()
        with _RECENT_BUY_LOCK:
            _stale_keys = [k for k, v in list(_RECENT_BUY_TS.items()) if _now_cleanup - v > 600]
            for k in _stale_keys:
                _RECENT_BUY_TS.pop(k, None)

        # 🔧 다음 사이클을 위해 현재 마켓 저장 (신규 매수 오탐 방지)
        with _ORPHAN_LOCK:
            _PREV_SYNC_MARKETS = current_markets.copy()

        # 🔧 FIX: 첫 sync 완료 → 이후부터는 2사이클 확인 복원
        if _ORPHAN_FIRST_SYNC:
            _ORPHAN_FIRST_SYNC = False
            print(f"[ORPHAN] 첫 동기화 완료 (발견: {len(current_markets)}개 마켓, 이후 2사이클 확인 복원)")

    except Exception as e:
        print(f"[ORPHAN_SYNC_ERR] {e}")


# =========================
# 컷 로깅 (위치 이동: final_price_guard에서 사용)
# =========================
# DEBUG_CUT, DEBUG_NEAR_MISS — config.py에서 정의됨
CUT_COUNTER = {
    k: 0
    for k in [
        "SURGE_LOW", "VOL_LOW", "SPREAD_HIGH", "PRICE_LOW", "ZSC_LOW",
        "VWAP_GAP_LOW", "UPTICK_FAIL", "FAKE_PUMP", "TICKS_LOW", "TURN_LOW",
        "BUY_WEAK", "BUY_WEAK_MANYT", "FRESH_FAIL", "BIDASK_WEAK",
        "IGNITION_OK", "BOT_PINGPONG", "BOT_WASH", "BOTACC_OK", "WICK_SPIKE",
        "ATR_OVERSHOOT", "EMA15M_DOWN", "BUY_DECAY", "EARLY_OK",
        "EARLY_LIGHT_FAIL", "PEAK_CHASE", "POSTCHECK_DROP", "MEGA_PASS",
        "SCORE_LOW", "SPREAD_EXTREME", "PROBE_TICK", "NO_SIGNAL",
        "STAGE1_GATE", "IGNITION_FAIL", "ENTRY_LOCK_FAIL", "PRICE_GUARD_FAIL"
    ]
}


def cut(reason, detail, near_miss=False):
    CUT_COUNTER[reason] = CUT_COUNTER.get(reason, 0) + 1
    # DEBUG_CUT: 전체 로그, DEBUG_NEAR_MISS: 초입 신호 후 컷만
    if DEBUG_CUT or (DEBUG_NEAR_MISS and near_miss):
        now_str = now_kst().strftime("%H:%M:%S")
        print(f"[FILTER][{now_str}] {reason:<16} | {detail}")


def cut_summary():
    parts = [
        f"{k}:{v}" for k, v in sorted(
            CUT_COUNTER.items(), key=lambda x: x[1], reverse=True) if v > 0
    ]
    if parts:
        print(f"[CUT_SUMMARY] {' , '.join(parts)}")


def final_price_guard(m, initial_price, max_drift=None, ticks=None, is_circle=False):
    """
    주문 직전 가격 재확인 (동적 임계치)
    - initial_price: 신호 발생 시 기준 가격 (pre['price'])
    - max_drift: 신호가 대비 허용 상승률 (None이면 동적 계산)
    - ticks: 변동성 계산용 틱 데이터
    - is_circle: 동그라미 재돌파 진입 여부 (True면 threshold 완화)
    - AGGRESSIVE_MODE=True 인 경우, max_drift~max_drift+1.2% 구간은
      '추격 진입'으로 소액/피라미딩 기반 진입 허용
    """
    try:
        js = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": m})
        if not js:
            print(f"[GUARD] {m} 티커 조회 실패 → 가드 스킵")
            return True, initial_price, False

        current_price = js[0].get("trade_price", initial_price)
        # 🔧 FIX: initial_price=0 방어 (ZeroDivisionError 방지)
        if not initial_price or initial_price <= 0:
            return True, current_price or initial_price, False
        drift = (current_price / initial_price - 1.0)

        # 🔧 동적 임계치: 변동성 + 장세 반영 (신호→주문 짧은 구간)
        if max_drift is None:
            pstd = price_band_std(ticks or [], sec=10) if ticks else None
            pstd = pstd if pstd is not None else 0.0  # None 센티넬 처리
            r = relax_knob()  # 0~1.5
            # 🔧 0.5% 기준 (짧은 순간 0.5% 움직임이면 충분)
            base = 0.005 if not AGGRESSIVE_MODE else 0.006
            # 🔧 야간(0~6시) 추격 허용폭 +0.1% 완화
            hour = now_kst().hour
            if 0 <= hour < 6:
                base += 0.001
            # 🔧 FIX: pstd 기여도 축소 (서지 중 고변동성 → 가드 넓어짐 → 꼭대기 체결)
            # 기존: min(0.004, pstd*0.5) → 서지 시 최대 +0.4%
            # 변경: min(0.002, pstd*0.3) → 서지 시 최대 +0.2% (변동성 클수록 조심)
            dyn = base + min(0.002, pstd * 0.3) + r * 0.002
            # 🔧 FIX: 동그라미(재돌파)는 이미 눌림 검증 완료 → threshold +0.3% 완화
            if is_circle:
                dyn += 0.003
            thr = dyn
        else:
            thr = max_drift

        if drift > thr:
            # 🔧 추격진입 예외 완전 제거 (pullback 엔트리가 있으므로 추격 불필요)
            return False, current_price, False

        # 🔧 FIX: 하방 급락 컷 (페이크 브레이크 방지)
        down_thr = max(0.005, thr * 0.8)  # 🔧 0.5% 또는 상단의 80%
        if drift < -down_thr:
            return False, current_price, False

        # 🔧 FIX: 추격성 진입 구분 (B안 — drift가 thr의 70%↑이면 chase 마킹)
        # chase=True → 후속에서 강제 half + spread 0.25% + depth 15M
        is_chase = (drift >= thr * 0.7)
        return True, current_price, is_chase

    except Exception as e:
        print(f"[GUARD_ERR] {m}: {e} → 1회 재시도")
        # 🔧 FIX: API 실패 시 1회 재시도 (네트워크 순간 장애로 기회손실 방지)
        # - 기존: 무조건 차단 → 좋은 신호도 날릴 수 있음
        # - 개선: 0.3초 후 1회 재시도, 그래도 실패면 차단
        try:
            time.sleep(0.3)
            js_retry = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": m})
            if js_retry:
                current_price = js_retry[0].get("trade_price", initial_price)
                drift = (current_price / initial_price - 1.0)
                # 재시도 성공 시 간단히 상승률만 체크 (동적 임계치는 기본값 사용)
                if drift <= 0.006:  # 0.6% 이하면 통과
                    print(f"[GUARD_RETRY_OK] {m} 재시도 성공 (drift={drift*100:.2f}%)")
                    return True, current_price, False
                else:
                    print(f"[GUARD_RETRY_FAIL] {m} 재시도 성공했으나 급등 (drift={drift*100:.2f}%)")
                    return False, current_price, False
        except Exception as e2:
            print(f"[GUARD_RETRY_ERR] {m}: {e2}")
        return False, initial_price, False

# =========================
# 🔥 자동 매수 진입
# =========================
def open_auto_position(m, pre, dyn_stop, eff_sl_pct):
    """
    초입·공격모드 대응 자동 매수 진입
    """
    # 🔍 DEBUG: 자동매수 진입 시작 로그
    print(f"[AUTO_ENTRY] {m} 시작 (AUTO_TRADE={AUTO_TRADE})")

    def signal_skip(reason):
        """초입신호 후 매수 스킵 로그 (near_miss 출력용)"""
        if DEBUG_NEAR_MISS:
            now_str = now_kst().strftime("%H:%M:%S")
            print(f"[SIGNAL_SKIP][{now_str}] {m} | {reason}")

    if not AUTO_TRADE:
        signal_skip("AUTO_TRADE=False (환경변수 AUTO_TRADE=1 필요)")
        tg_send_mid(f"⚠️ {m} 자동매수 비활성 (AUTO_TRADE=0)")
        return

    if not UPBIT_ACCESS_KEY or not UPBIT_SECRET_KEY:
        signal_skip("API 키 미설정")
        return

    # 🔧 FIX: pending 고착 방지 — open 전환 실패 시 pending 강제 제거
    _entered_open = False

    # 🔐 프로세스 간 중복 진입 방지 (파일락+메모리락 컨텍스트)
    # 🔧 FIX: reentrant=True (스캔 루프가 이미 락 보유 → TTL 갱신만, 해제는 모니터 finally에서)
    with entry_lock(m, ttl_sec=90, reentrant=True) as got_lock:
        if not got_lock:
            signal_skip("entry_lock 획득 실패")
            return

        # 🔧 pending 상태 원자화 (락 안에서만 조작)
        _max_pos_blocked = False
        with _POSITION_LOCK:
            existing = OPEN_POSITIONS.get(m)
            if existing:
                if not existing.get("pre_signal"):
                    signal_skip("이미 포지션 보유중")
                    return
            active_count = sum(1 for p in OPEN_POSITIONS.values() if p.get("state") == "open")
            if active_count >= MAX_POSITIONS:
                signal_skip(f"최대 포지션 {MAX_POSITIONS}개 도달")
                # 🔧 FIX: pending 상태인 경우에만 제거 (다른 상태 보호)
                if existing and existing.get("state") == "pending":
                    OPEN_POSITIONS.pop(m, None)
                _max_pos_blocked = True
            if not _max_pos_blocked and not existing:
                OPEN_POSITIONS[m] = {"state": "pending", "pre_signal": True, "pending_ts": time.time()}
        # 🔧 FIX: tg_send_mid를 락 밖으로 이동 — 네트워크 I/O가 _POSITION_LOCK 차단 방지
        if _max_pos_blocked:
            tg_send_mid(f"⚠️ {m} 신규 진입 대기 (최대 {MAX_POSITIONS}개 포지션 보유 중)")
            return

        signal_price = pre.get("price")
        if not signal_price:
            signal_skip("pre['price'] 없음")
            with _POSITION_LOCK:
                OPEN_POSITIONS.pop(m, None)
            return

        # === 이진 진입모드 반영 (probe 폐지) ===
        entry_mode = pre.get("entry_mode", "confirm")

        # 🔧 특단조치: probe 폐지 → half(50%) or confirm(100%) only
        if entry_mode == "half":
            entry_fraction = 0.50   # 50% 사이즈
            mode_emoji = "⚡"
        else:  # confirm or any
            entry_mode = "confirm"
            entry_fraction = 1.0    # 전체 금액 (확정 진입)
            mode_emoji = "🔥"

        # ========================================
        # 🚀 Pre-break 전용 2초 포스트체크
        # ========================================
        filter_type = pre.get("filter_type", "")
        if filter_type == "prebreak" and PREBREAK_POSTCHECK_SEC > 0:
            print(f"[PREBREAK] {m} → {PREBREAK_POSTCHECK_SEC}초 포스트체크 시작")
            time.sleep(PREBREAK_POSTCHECK_SEC)

            # 틱 재조회
            ticks_recheck = get_recent_ticks(m, 100)
            if not ticks_recheck:
                signal_skip("PREBREAK 포스트체크: 틱 없음")
                with _POSITION_LOCK:
                    OPEN_POSITIONS.pop(m, None)
                return

            t15_recheck = micro_tape_stats_from_ticks(ticks_recheck, 15)

            # 매수비/거래속도 재확인
            if t15_recheck["buy_ratio"] < PREBREAK_BUY_MIN * 0.9:  # 10% 여유
                signal_skip(f"PREBREAK 포스트체크: 매수비 하락 ({t15_recheck['buy_ratio']:.0%})")
                with _POSITION_LOCK:
                    OPEN_POSITIONS.pop(m, None)
                return

            if t15_recheck["krw_per_sec"] < PREBREAK_KRW_PER_SEC_MIN * 0.7:  # 30% 여유
                signal_skip(f"PREBREAK 포스트체크: 거래속도 하락 ({t15_recheck['krw_per_sec']/1000:.0f}K)")
                with _POSITION_LOCK:
                    OPEN_POSITIONS.pop(m, None)
                return

            print(f"[PREBREAK] {m} 포스트체크 통과 → 진입 진행")

        # ★ 동적 가격 가드 (변동성 + 장세 반영)
        # ticks 전달로 동적 임계치 계산
        # 🔧 Phase2: GT 모멘텀은 drift 임계 1.2%로 완화 (기본 0.5~1.0%)
        # GT는 강모멘텀이라 시그널→주문 사이 0.5%+ drift가 정상 → 기존 임계에서 과도 차단
        _gt_drift = 0.012 if "GT" in pre.get("signal_tag", "") else None
        ok_guard, current_price, is_chase = final_price_guard(m, signal_price, max_drift=_gt_drift, ticks=pre.get("ticks"), is_circle=pre.get("is_circle", False))

        # 🔧 FIX: VWAP gap + drift 복합 체크 (가드 통과해도 총 괴리 과대 → 꼭대기 진입 차단)
        # 예: VWAP+1.7% 신호 + 0.95% drift = 2.65% → 실질 VWAP+2.65% 진입은 과도
        _vwap_gap_pct = pre.get("vwap_gap", 0)  # % 단위 (1.7 = 1.7%)
        _guard_drift_pct = (current_price / signal_price - 1.0) * 100 if signal_price > 0 else 0
        _total_gap = _vwap_gap_pct + max(0, _guard_drift_pct)
        # 🔧 Phase2: GT 모멘텀 전용 VWAP+DRIFT 완화 (2.0% → 3.0%)
        # GT는 RSI≥74.55 강모멘텀이라 시그널 시점에 이미 VWAP 대비 높은 위치
        # → 기존 2.0% 임계에서 구조적으로 100% 차단됨 → GT만 3.0%로 완화
        _is_gt = "GT" in pre.get("signal_tag", "")
        _vwap_drift_limit = 3.5 if _is_gt else 2.0
        if _total_gap > _vwap_drift_limit and not pre.get("is_circle"):
            ok_guard = False
            print(f"[VWAP+DRIFT] {m} VWAP gap {_vwap_gap_pct:.1f}% + drift {_guard_drift_pct:+.2f}% "
                  f"= 총 {_total_gap:.1f}% > {_vwap_drift_limit:.1f}% → 꼭대기 진입 차단")

        if not ok_guard:
            drift_pct = (current_price / signal_price - 1) * 100
            signal_skip(f"가격가드 실패 (신호가→현재가 {drift_pct:+.2f}%)")
            tg_send(
                f"⚠️ <b>진입 취소</b> {m}\n"
                f"• 신호가: {fmt6(signal_price)}원\n"
                f"• 현재가: {fmt6(current_price)}원\n"
                f"• 상승률: {drift_pct:.2f}%\n"
                f"• 사유: 가격 급등 (초입 추격 위험)"
            )
            with _POSITION_LOCK:
                OPEN_POSITIONS.pop(m, None)
            return

        # 🔧 추격진입 시 사이즈 다운: half 강제 (probe 폐지)
        if is_chase and entry_mode == "confirm":
            print(f"[CHASE_SIZE_DOWN] {m} 추격진입 감지 → entry_mode=confirm→half (50%)")
            entry_mode = "half"
            entry_fraction = 0.50
            mode_emoji = "⚡"

        # ============================================================
        # ★★★ 구조 변경 1: 풀백 진입 (Pullback Entry)
        # 🔧 손익분기개선: 20초→5초 대폭 축소 (역선택 제거)
        #   기존 문제: 강한 모멘텀은 풀백 안 옴→20초 후 더 높게 매수
        #              약한 모멘텀만 풀백 잡힘→역선택 구조
        #   개선: 5초 이내 빠른 눌림만 잡고, 없으면 즉시 진입
        # 🔧 FIX: 동그라미(재돌파)는 이미 눌림→리클레임 거쳤으므로 대기 축소
        # ============================================================
        _is_circle_entry = pre.get("is_circle", False)
        _is_ignition = "점화" in pre.get("signal_tag", "")
        # 🔧 점화 신호: 풀백 대기 0.5초 (모멘텀 확실 → 지체 = 꼭대기 진입)
        # 동그라미: 1.0초 / 일반: 2.0초
        if _is_ignition:
            PULLBACK_WAIT_SEC = 0.5
        elif _is_circle_entry:
            PULLBACK_WAIT_SEC = 1.0
        else:
            PULLBACK_WAIT_SEC = 2.0
        PULLBACK_MIN_DIP = 0.001    # 🔧 0.15→0.1% (미세 눌림도 인정)
        # 🔧 FIX: PULLBACK_MAX_DIP을 SL 기반 연동 (SL보다 먼저 기회를 버리지 않게)
        # 기존: 고정 1.2/1.5% → SL(1.8%)보다 먼저 컷 → 좋은 신호 버림
        # 변경: min(0.020, SL * 0.8) → SL이 넓을수록 정상 눌림도 넓게 허용
        _pb_sl_ref = pre.get("box_sl_pct", DYN_SL_MIN)
        _pb_base_dip = min(0.020, _pb_sl_ref * 0.8)
        if _is_circle_entry:
            PULLBACK_MAX_DIP = max(_pb_base_dip, 0.015)   # 동그라미: 최소 1.5%
        else:
            PULLBACK_MAX_DIP = max(_pb_base_dip, 0.012)   # 일반: 최소 1.2%
        PULLBACK_BOUNCE_TICKS = 2   # 🔧 3→2틱 (빠른 확인)

        _pb_peak = current_price
        _pb_trough = current_price
        _pb_bounce_cnt = 0
        _pb_dipped = False
        _pb_entry_price = current_price  # 풀백 없으면 원래 가격 사용
        _pb_start = time.time()
        _pb_last_tick_ts = 0  # 🔧 FIX: 캐시 틱 중복 바운스 방지용

        while time.time() - _pb_start < PULLBACK_WAIT_SEC:
            _pb_ticks = get_recent_ticks(m, 30, allow_network=True)
            if not _pb_ticks:
                time.sleep(0.5)
                continue
            # 🔧 FIX: 최신 틱 타임스탬프 비교로 캐시 중복 카운트 방지
            _pb_latest_tick = max(_pb_ticks, key=tick_ts_ms)
            _pb_latest_ts = tick_ts_ms(_pb_latest_tick)
            _pb_now = _pb_latest_tick.get("trade_price", current_price)
            if _pb_latest_ts == _pb_last_tick_ts:
                time.sleep(0.5)
                continue  # 같은 틱이면 스킵 (캐시 중복 방지)
            _pb_last_tick_ts = _pb_latest_ts

            # 피크 갱신
            if _pb_now > _pb_peak:
                _pb_peak = _pb_now
                _pb_trough = _pb_now
                _pb_bounce_cnt = 0

            # 트로프 갱신
            if _pb_now < _pb_trough:
                _pb_trough = _pb_now
                _pb_bounce_cnt = 0

            # 풀백 깊이 체크
            _pb_dip_pct = (_pb_peak - _pb_trough) / _pb_peak if _pb_peak > 0 else 0

            # 너무 깊은 하락 = 모멘텀 소멸 → 진입 포기
            if _pb_dip_pct >= PULLBACK_MAX_DIP:
                signal_skip(f"풀백 과대 ({_pb_dip_pct*100:.2f}% 하락, 모멘텀 소멸)")
                tg_send_mid(f"⚠️ {m} 매수 스킵: 풀백 과대 ({_pb_dip_pct*100:.2f}% 하락)")
                with _POSITION_LOCK:
                    OPEN_POSITIONS.pop(m, None)
                return

            # 최소 풀백 달성 여부
            if _pb_dip_pct >= PULLBACK_MIN_DIP:
                _pb_dipped = True

            # 풀백 후 바운스 감지 (지지 확인)
            if _pb_dipped and _pb_now > _pb_trough:
                _pb_bounce_cnt += 1
                if _pb_bounce_cnt >= PULLBACK_BOUNCE_TICKS:
                    # ★ 풀백 진입 성공! 트로프 근처에서 매수
                    _pb_entry_price = _pb_now
                    _pb_saved = (current_price - _pb_entry_price) / current_price * 100
                    print(f"[PULLBACK_ENTRY] {m} 풀백 진입! "
                          f"피크{fmt6(_pb_peak)}→저점{fmt6(_pb_trough)}→현재{fmt6(_pb_now)} "
                          f"(절약 {_pb_saved:.2f}%)")
                    break
            else:
                _pb_bounce_cnt = max(0, _pb_bounce_cnt - 1)

            time.sleep(0.5)
        else:
            # 타임아웃: 풀백 없이 계속 상승 → 원래 가격으로 진입 (강한 모멘텀)
            _pb_ticks_final = get_recent_ticks(m, 10, allow_network=True)
            if _pb_ticks_final:
                _pb_entry_price = max(_pb_ticks_final, key=tick_ts_ms).get("trade_price", current_price)
            # 모멘텀 재확인: 계속 오르고 있으면 진입, 꺾이면 포기
            _pb_t10 = micro_tape_stats_from_ticks(_pb_ticks_final or [], 10)
            if _pb_t10.get("buy_ratio", 0) < 0.42:
                signal_skip(f"풀백 타임아웃 + 매수세 약화 ({_pb_t10.get('buy_ratio',0):.0%})")
                tg_send_mid(f"⚠️ {m} 매수 스킵: 풀백 후 매수세 약화 ({_pb_t10.get('buy_ratio',0):.0%})")
                with _POSITION_LOCK:
                    OPEN_POSITIONS.pop(m, None)
                return
            print(f"[PULLBACK_TIMEOUT] {m} 풀백 없이 상승 지속 → 현재가 {fmt6(_pb_entry_price)} 진입")

        # 풀백 이후 현재가를 진입가로 갱신
        current_price = _pb_entry_price

        # 🔧 FIX: 풀백 후 가격가드 완화 — 급등/급락만 차단 (1.2% 상한)
        # - 기존: final_price_guard 재호출 (0.5% 임계치) → 풀백 자체가 가격 이동이라 대부분 실패
        # - 수정: 단순 상하한만 체크 (이미 1차 가드 통과한 신호이므로 2차는 느슨하게)
        try:
            _pb_ticker = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": m})
            if _pb_ticker:
                cur_price2 = _pb_ticker[0].get("trade_price", current_price)
                _drift2 = (cur_price2 / current_price - 1.0)
                if _drift2 > 0.012:  # 1.2% 이상 급등 → 추격 위험
                    signal_skip(f"풀백 후 급등 ({_drift2*100:+.2f}%)")
                    tg_send(
                        f"⚠️ <b>진입 취소</b> {m}\n"
                        f"• 풀백 후 현재가: {fmt6(cur_price2)}원\n"
                        f"• 사유: 풀백 대기 중 {_drift2*100:.2f}% 급등"
                    )
                    with _POSITION_LOCK:
                        OPEN_POSITIONS.pop(m, None)
                    return
                if _drift2 < -0.010:  # 1.0% 이상 급락 → 모멘텀 소멸
                    signal_skip(f"풀백 후 급락 ({_drift2*100:+.2f}%)")
                    tg_send_mid(f"⚠️ {m} 매수 스킵: 풀백 후 급락 ({_drift2*100:.2f}%)")
                    with _POSITION_LOCK:
                        OPEN_POSITIONS.pop(m, None)
                    return
                current_price = cur_price2  # 최신 가격으로 갱신
        except Exception as _pb_guard_err:
            print(f"[PB_GUARD] {m} 풀백 후 가격 체크 실패 (진행): {_pb_guard_err}")

        accounts = get_account_info()
        if not accounts:
            signal_skip("계좌 조회 실패")
            with _POSITION_LOCK:
                OPEN_POSITIONS.pop(m, None)
            return

        # 🔧 FIX: locked 반영한 가용잔고 계산
        krw_bal = get_available_krw(accounts)

        if krw_bal < 6000:
            signal_skip(f"KRW 부족 ({krw_bal:,.0f}원)")
            tg_send_mid(f"⚠️ {m} 매수 스킵: KRW 잔고 부족 ({krw_bal:,.0f}원)")
            with _POSITION_LOCK:
                OPEN_POSITIONS.pop(m, None)
            return

        entry_price = current_price

        # 🔧 FIX (A): 현재가 기준으로 SL 재계산 (신호가와 현재가 차이 보정)
        # - 기존: dyn_stop은 신호 시점 가격 기준 → 현재가로 진입 시 손절폭 불일치
        # - 개선: final_price_guard 통과 후 현재가 기준으로 dynamic_stop_loss 재계산
        c1_for_sl = pre.get("c1")
        # 🔧 FIX: signal_type 파생 (pre에 명시적 signal_type 있으면 우선 사용)
        signal_type = pre.get("signal_type") or (
            "ign" if pre.get("ign_ok") else
            "mega" if pre.get("mega_ok") else
            "early" if pre.get("early_ok") else
            "normal"
        )
        # 📦 박스 전략은 고정 SL/TP 사용 (dynamic_stop_loss 재계산 금지)
        if pre.get("is_box"):
            stop_price = pre["box_stop"]
            eff_sl_pct = pre["box_sl_pct"]
            print(f"[SL_BOX] {m} 박스 전용 SL: {eff_sl_pct*100:.2f}% (stop={fmt6(stop_price)})")
        elif c1_for_sl:
            new_stop, new_sl_pct, sl_info = dynamic_stop_loss(entry_price, c1_for_sl, signal_type, entry_price, market=m)
            stop_price = new_stop
            eff_sl_pct = new_sl_pct
            print(f"[SL_RECALC] {m} 현재가 기준 SL 재계산: {eff_sl_pct*100:.2f}% (stop={fmt6(stop_price)})")
        else:
            # c1 없으면 기존 dyn_stop 사용 (폴백)
            stop_price = dyn_stop
            print(f"[SL_RECALC] {m} c1 없음 → 기존 SL 사용")

        # ✅ 최근 승률 기반 동적 리스크
        adaptive_risk = get_adaptive_risk()

        # === 하이브리드 진입 구조 ===
        risk_to_use = adaptive_risk * SEED_RISK_FRACTION if USE_PYRAMIDING else adaptive_risk
        risk_to_use *= entry_fraction  # probe는 리스크 축소 반영

        base_qty = calc_position_size(
            entry_price,
            stop_price,
            krw_bal,
            risk_to_use,
        )

        krw_to_use = base_qty * entry_price

        # 🔧 FIX: 가용잔고 초과 방지 (before1 기준)
        # 소액계좌(3만원 미만)는 비율 완화하여 최소주문 가능하게
        min_order_krw = 6000
        if krw_bal < 30000:
            MAX_POSITION_RATIO = 0.60  # 소액: 60%까지 허용 (최소주문 확보)
        else:
            MAX_POSITION_RATIO = 0.30  # 일반: 30% (과집중 방지)
        if krw_to_use > krw_bal * MAX_POSITION_RATIO:
            print(f"[SIZE_CAP] {m} 주문 {krw_to_use:,.0f} > 가용잔고 {krw_bal:,.0f}의 {int(MAX_POSITION_RATIO*100)}% → 캡")
            krw_to_use = krw_bal * MAX_POSITION_RATIO

        # 🔧 FIX: 최소 진입금액 6000원 (매도최소 5000원 + 버퍼 1000원)
        if krw_to_use < min_order_krw:
            # 소액계좌 지원: 잔고가 최소주문의 1.5배 이상이면 상향
            if krw_bal >= min_order_krw * 1.5:
                print(f"[SIZE_BUMP] {m} 리스크계산 {krw_to_use:,.0f}원 < 최소주문 {min_order_krw:,}원 → {min_order_krw:,}원 상향 (⚠️ 리스크모델 초과)")
                krw_to_use = min_order_krw
            else:
                signal_skip(f"주문금액 부족 ({krw_to_use:,.0f}원 < {min_order_krw:,}원)")
                tg_send_mid(f"⚠️ {m} 매수 스킵: 주문금액 부족 ({krw_to_use:,.0f}원 < {min_order_krw:,}원)")
                with _POSITION_LOCK:
                    OPEN_POSITIONS.pop(m, None)
                return

        # 🔧 체결충격(impact) 기반 사이징 댐퍼
        # 상위 3호가 합계의 15% 초과 사용 시 과도 → 캡 (슬리피지 방지)
        try:
            units = pre.get("ob", {}).get("raw", {}).get("orderbook_units", [])[:3]
            top3_ask_krw = sum(float(u["ask_price"]) * float(u["ask_size"]) for u in units)
        except Exception:
            top3_ask_krw = 0.0

        if top3_ask_krw > 0:
            cap = top3_ask_krw * 0.15  # 상위 3호가 합계의 15%
            if krw_to_use > cap:
                print(f"[IMPACT_CAP] {m} 주문 {krw_to_use:,.0f} > 15% of top3-ask {cap:,.0f} → 캡")
                krw_to_use = int(cap)
                # 🔧 FIX: confirm이면 entry_mode를 half로 변경 (포지션 추적에 반영)
                # - 기존: entry_fraction만 변경 → 이미 사이징 완료라 무효
                # - 변경: entry_mode 자체를 바꿔서 OPEN_POSITIONS에 정확히 기록
                if entry_mode == "confirm":
                    entry_mode = "half"
                    print(f"[IMPACT_CAP] {m} confirm → half 전환 (임팩트캡 적용)")

        krw_to_use = int(krw_to_use)

        # 🔧 임팩트캡 후 최소주문금액 재검증
        if krw_to_use < min_order_krw:
            if krw_bal >= min_order_krw * 2:
                print(f"[SIZE_BUMP] {m} 임팩트캡 후 {krw_to_use:,.0f}원 < {min_order_krw:,}원 → {min_order_krw:,}원 상향")
                krw_to_use = min_order_krw
            else:
                signal_skip(f"임팩트캡 후 주문금액 부족 ({krw_to_use:,.0f}원)")
                tg_send_mid(f"⚠️ {m} 매수 스킵: 임팩트캡 후 주문금액 부족 ({krw_to_use:,.0f}원)")
                with _POSITION_LOCK:
                    OPEN_POSITIONS.pop(m, None)
                return

        # === 매수 ===
        # 🔧 FIX: 매수 전 보유량 저장 (체결 재검증용)
        # - od가 None/timeout/executed 0이어도 실제 체결됐을 수 있음
        # - prev_balance 대비 증가 시에만 포지션 생성 → 오탐 방지
        coin = m.replace("KRW-", "")
        prev_balance = 0.0
        try:
            for acc in accounts:
                if acc.get("currency") == coin:
                    prev_balance = float(acc.get("balance") or "0")
                    break
        except Exception:
            prev_balance = 0.0

        # 🔧 FIX: 매수 직전 스프레드/깊이 재체크 (가격가드→주문 사이 호가 변동 방어)
        try:
            _ob_recheck = fetch_orderbook_cache([m]).get(m, {})
            _spread_now = _ob_recheck.get("spread", 0)
            _depth_now = _ob_recheck.get("depth_krw", 0)
            _spread_limit = 0.25 if is_chase else 0.40
            _depth_min = 15_000_000 if is_chase else 5_000_000
            if _spread_now > _spread_limit:
                signal_skip(f"매수직전 스프레드 악화 {_spread_now:.2f}%>{_spread_limit:.2f}%")
                tg_send_mid(f"⚠️ {m} 매수 스킵: 스프레드 악화 ({_spread_now:.2f}%>{_spread_limit:.2f}%)")
                with _POSITION_LOCK:
                    OPEN_POSITIONS.pop(m, None)
                return
            if _depth_now > 0 and _depth_now < _depth_min:
                signal_skip(f"매수직전 호가깊이 부족 {_depth_now/1e6:.1f}M<{_depth_min/1e6:.0f}M")
                tg_send_mid(f"⚠️ {m} 매수 스킵: 호가깊이 부족 ({_depth_now/1e6:.1f}M)")
                with _POSITION_LOCK:
                    OPEN_POSITIONS.pop(m, None)
                return
        except Exception as _ob_err:
            print(f"[OB_RECHECK] {m} 호가 재체크 실패 (진행): {_ob_err}")

        avg_price = None  # FIX [C3]: 명시적 초기화 (locals() 의존 제거)
        try:
            # 🔧 FIX: 매수 주문 전에 _RECENT_BUY_TS 선제 기록 (유령 오탐 방지)
            # - 주문~체결 사이에 sync_orphan이 돌면 잔고 발견 → 유령으로 오판
            # - 주문 전에 기록해두면 sync에서 300초 보호에 걸려 스킵됨
            with _RECENT_BUY_LOCK:
                _RECENT_BUY_TS[m] = time.time()
            # 하이브리드 매수: 지정가(ask1) → 대기 → 미체결 시 시장가 전환
            # 🔧 FIX: 강돌파는 하이브리드 타임아웃 0.6초로 단축 (빠른 진입 = 꼭대기 방지)
            _ob_for_hybrid = pre.get("ob")
            _is_strongbreak_entry = "강돌파" in pre.get("signal_tag", "")
            _hybrid_timeout = 0.6 if _is_strongbreak_entry else 1.2
            res = hybrid_buy(m, krw_to_use, ob_data=_ob_for_hybrid, timeout_sec=_hybrid_timeout)
            if os.getenv("DEBUG_HYBRID_BUY"):
                print("[AUTO_BUY_RES]", json.dumps(res, ensure_ascii=False))
            oid = res.get("uuid") if isinstance(res, dict) else None
            od = get_order_result(oid, timeout_sec=12) if oid else None
            if os.getenv("DEBUG_HYBRID_BUY"):
                print("[AUTO_BUY_ORDER]", json.dumps(od, ensure_ascii=False))

            if od:
                volume_filled = float(od.get("executed_volume") or "0")
            else:
                volume_filled = 0.0

            # 🔧 FIX: 체결 0이면 잔고 재검증 (prev_balance 대비 증가 시에만 복구)
            # - od가 None/timeout/executed 0이어도 실제 체결됐을 수 있음
            # - 전면 잔고 fallback 부활 X → prev_balance 대비 증가분만 인정 (오탐 방지)
            if volume_filled <= 0:
                # 🔧 FIX: 잔고 재검증 1초 간격 최대 8회 (업비트 잔고 반영 지연 대응)
                # - 기존: 0.5초 1회 → 유령 포지션의 주요 원인
                # - 변경: 1초 간격 최대 8회 재시도 → 체결 반영 지연 충분히 커버
                verified = False
                try:
                    for retry_i in range(8):
                        time.sleep(1.0)  # 1초 간격 대기
                        accounts_retry = get_account_info()
                        new_balance = 0.0
                        avg_buy_price_from_acc = entry_price
                        for acc in (accounts_retry or []):
                            if acc.get("currency") == coin:
                                new_balance = float(acc.get("balance") or "0")
                                avg_buy_price_from_acc = float(acc.get("avg_buy_price") or "0")
                                break

                        balance_diff = new_balance - prev_balance
                        if balance_diff > 1e-8:  # 증가분 존재 → 실제 체결됨
                            volume_filled = balance_diff
                            avg_price = avg_buy_price_from_acc if avg_buy_price_from_acc > 0 else entry_price
                            print(f"[BUY_VERIFY] {m} 잔고 증가 확인 (시도 {retry_i+1}/8): {prev_balance:.8f} → {new_balance:.8f} (체결량: {volume_filled:.8f})")
                            tg_send(f"🔄 {m} 체결 재검증 성공 (시도 {retry_i+1}/8)\n• 잔고 변화: {prev_balance:.6f} → {new_balance:.6f}\n• 체결량: {volume_filled:.6f}")
                            verified = True
                            break
                        if retry_i < 7:
                            print(f"[BUY_VERIFY] {m} 잔고 변화 없음 (시도 {retry_i+1}/8) → 재시도")

                    if not verified:
                        # 8회 모두 증가 없음 → 진짜 실패
                        signal_skip("체결 0 (잔고 재검증 8회 실패)")
                        tg_send(f"⚠️ {m} 자동매수 체결 0 (잔고 변화 없음, 8회 재검증) → 포지션 생성 안 함")
                        with _POSITION_LOCK:
                            OPEN_POSITIONS.pop(m, None)
                        return
                except Exception as ve:
                    print(f"[BUY_VERIFY_ERR] {m} 재검증 실패: {ve}")
                    signal_skip(f"체결 0 (재검증 예외)")
                    tg_send(f"⚠️ {m} 자동매수 체결 0 (재검증 실패) → 포지션 생성 안 함")
                    with _POSITION_LOCK:
                        OPEN_POSITIONS.pop(m, None)
                    return

            # 🔧 FIX C2: hybrid_buy 잔여분 시장가 체결분 반영
            # - hybrid_buy는 limit_res(지정가 UUID)만 반환 → 시장가 추가분 누락
            # - 잔고 차이로 실제 총 체결량 보정 (volume_filled 과소 방지)
            if volume_filled > 0:
                try:
                    time.sleep(0.3)
                    _post_accounts = get_account_info()
                    _post_balance = 0.0
                    _post_avg_price = 0.0
                    for _acc in (_post_accounts or []):
                        if _acc.get("currency") == coin:
                            _post_balance = float(_acc.get("balance") or "0")
                            _post_avg_price = float(_acc.get("avg_buy_price") or "0")
                            break
                    _balance_diff = _post_balance - prev_balance
                    if _balance_diff > volume_filled + 1e-12:  # 잔고 증가분이 조금이라도 크면 항상 보정
                        print(f"[HYBRID_VOL_FIX] {m} od.executed={volume_filled:.8f} < 잔고증가={_balance_diff:.8f} → 보정")
                        volume_filled = _balance_diff
                        if _post_avg_price > 0:
                            avg_price = _post_avg_price
                except Exception as _vf_err:
                    print(f"[HYBRID_VOL_FIX_ERR] {m}: {_vf_err}")

            # 평균가 계산 (체결 정보가 있으면)
            if avg_price is None:
                trades = (od.get("trades") or []) if od else []
                if trades:
                    total_krw = sum(float(tr["price"]) * float(tr["volume"]) for tr in trades)
                    total_vol = sum(float(tr["volume"]) for tr in trades)
                    avg_price = total_krw / total_vol if total_vol > 0 else entry_price
                else:
                    avg_price = float(entry_price)

        except Exception as e:
            print("[AUTO BUY ERR]", e)
            # 🔧 FIX: 예외 시 실패 처리 (잔고 fallback 제거 - 이전 잔고 오판 방지)
            signal_skip(f"매수 예외 ({e})")
            tg_send(f"⚠️ 매수 실패 {m}\n{e}")
            with _POSITION_LOCK:
                OPEN_POSITIONS.pop(m, None)
            return

        # === 포지션 저장 ===
        # 🔧 FIX: avg_price 기준으로 stop 재계산 (체결 슬립 반영)
        # - 기존: dyn_stop은 신호 시점 가격(signal_price) 기준 → 체결가와 괴리 발생 가능
        # - 변경: 실제 체결가(avg_price) 기준으로 손절가 재계산
        adjusted_stop = avg_price * (1 - eff_sl_pct)
        # 🔧 FIX: signal_type 재사용 (위 1657에서 이미 파생됨 → 중복 로직 통합)
        _derived_signal_type = signal_type
        # 🔧 데이터수집: 진입시점 ATR/pstd 저장 (나중에 손절폭/트레일 간격 최적화용)
        _entry_ticks = pre.get("ticks", [])
        _entry_pstd = price_band_std(_entry_ticks, sec=10) if _entry_ticks else 0.0
        _entry_pstd = _entry_pstd if _entry_pstd is not None else 0.0
        _entry_c1 = get_minutes_candles(1, m, 20)
        _entry_atr = atr14_from_candles(_entry_c1, 14) if _entry_c1 else None
        _entry_atr_pct = (_entry_atr / avg_price * 100) if (_entry_atr and avg_price > 0) else 0.0

        # 🔧 v7: 진입 컨텍스트 스냅샷 — 핵심 지표값 기록 (사후 분석/피드백용)
        _ctx_rsi = _v4_rsi_from_candles(_entry_c1, 14) if _entry_c1 and len(_entry_c1) >= 15 else None
        _ctx_vr5 = _v4_volume_ratio_5(_entry_c1) if _entry_c1 and len(_entry_c1) >= 6 else 0.0
        _ctx_buy_ratio = pre.get("buy_ratio", 0)
        _ctx_spread = pre.get("spread", 0)
        _ctx_accel = pre.get("flow_accel", 0)

        with _POSITION_LOCK:
            OPEN_POSITIONS[m] = {
                "entry_price": avg_price,
                "volume": volume_filled,
                "stop": adjusted_stop,  # 🔧 avg_price 기준
                "sl_pct": eff_sl_pct,
                "state": "open",
                "last_add_ts": 0.0,
                "entry_mode": entry_mode,  # 🔧 FIX: IMPACT_CAP 전환 반영 (pre 대신 로컬 변수)
                "entry_ts": time.time(),  # 🧠 진입 시각 (학습용)
                "signal_tag": pre.get("signal_tag", "기본"),  # 🔧 MFE 익절 경로용
                "signal_type": _derived_signal_type,  # 🔧 FIX: SL 신호별 완화용
                "trade_type": pre.get("trade_type", "scalp"),  # 🔧 특단조치: 스캘프/러너 진입 시 결정
                # 🔧 데이터수집: 손절폭/트레일 간격 튜닝용 메트릭
                "entry_atr_pct": round(_entry_atr_pct, 4),     # 진입시 ATR% (변동성 크기) — % 단위
                "entry_pstd": round(_entry_pstd * 100, 4),     # 진입시 가격표준편차 (10초) — % 단위 (pstd 통일)
                "entry_spread": round(pre.get("spread", 0), 4),  # 진입시 스프레드
                "entry_consec": pre.get("consecutive_buys", 0),  # 진입시 연속매수
                # 🔧 v7: 진입 컨텍스트 스냅샷 (사후 분석/MFE 피드백용)
                "entry_rsi": round(_ctx_rsi, 2) if _ctx_rsi is not None else None,  # 1m RSI(14)
                "entry_vr5": round(_ctx_vr5, 2),                 # VR5 (현재봉/직전5봉 거래량비)
                "entry_buy_ratio": round(_ctx_buy_ratio, 4),     # 매수비율
                "entry_accel": round(_ctx_accel, 3),             # 유입가속도
                # 📦 전략 태그 (박스/돌파/동그라미 구분)
                "entry_hour": now_kst().hour,  # 🔧 v7: 시간대별 청산 타임아웃 차별화용
                "strategy": "box" if pre.get("is_box") else
                            "circle" if pre.get("is_circle") else "breakout",
                # 📦 박스 전용 TP/SL (모니터에서 우선 적용)
                "box_tp": pre.get("box_tp"),
                "box_stop": pre.get("box_stop"),
                "box_high": pre.get("box_high"),
                "box_low": pre.get("box_low"),
                # 🔧 FIX: 튜닝 데이터 필드 초기화 (monitor가 갱신 전 청산 시 0값 방지)
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
                "mfe_sec": 0,
                "trail_dist": 0.0,
                "trail_stop_pct": 0.0,
            }
            _entered_open = True  # 🔧 FIX: pending→open 전환 성공 마킹

        slip_pct = (avg_price / signal_price - 1.0) if signal_price else 0.0
        # 🔧 FIX: 불리한 슬리피지만 기록 (abs()는 유리한 체결도 비용으로 취급 → 체크포인트 과대)
        # 유리한 체결(음수) → 0, 불리한 체결(양수) → 그대로, 1% 캡
        slip_cost = min(0.01, max(0.0, slip_pct))  # 불리한 것만 반영
        _ENTRY_SLIP_HISTORY.append(slip_cost)  # 🔧 FIX: entry 전용
        # FIX [M4]: _SLIP_HISTORY 제거됨 (entry/exit 분리로 대체)

        # 진입 사유 한 줄 생성 (pre dict에서 직접 추출)
        signal_tag = pre.get("signal_tag", "기본")
        vol_b = pre.get("current_volume", 0)
        vol_s = pre.get("volume_surge", 1.0)
        _tape = pre.get("tape", {})
        buy_r = _tape.get("buy_ratio", pre.get("buy_ratio", 0))
        turn_r = pre.get("turn_pct", 0) / 100  # % → decimal
        imb = pre.get("imbalance", 0)
        cons = pre.get("consecutive_buys", 0)

        # 🔧 대금/배수 표시 포맷
        if vol_b >= 1e8:
            vol_str = f"{vol_b/1e8:.1f}억"
        elif vol_b >= 1e6:
            vol_str = f"{vol_b/1e6:.0f}백만"
        else:
            vol_str = f"{vol_b/1e4:.0f}만"
        surge_str = f"{vol_s:.1f}x" if vol_s >= 1.0 else f"{vol_s:.2f}x"

        detail_str = (f"대금{vol_str} 서지{surge_str} "
                      f"매수{buy_r:.0%} 회전{turn_r:.0%} "
                      f"임밸{imb:.2f} 연속{cons}회")

        # 🔧 이진 진입모드 라벨 (half/confirm only, probe 폐지)
        if entry_mode == "half":
            mode_label = "중간진입"      # 스코어 55~72, 50% 사이즈
        else:  # confirm
            mode_label = "확정진입"      # 스코어 >= 72, 풀 사이즈
        entry_reason = f"{signal_tag} ({detail_str})"

        # 🔧 실제 비율 계산 (최소금액 적용 시 entry_fraction과 다를 수 있음)
        actual_pct = (krw_to_use / krw_bal * 100) if krw_bal > 0 else 0

        # 🔥 경로 표시: signal_tag 하나로 간소화
        filter_type = pre.get("filter_type", "stage1_gate")
        if filter_type == "prebreak":
            path_str = "🚀선행진입"
        else:
            path_str = pre.get("signal_tag", "기본")

        # ✅ 손절가 None 방지
        safe_stop_str = fmt6(stop_price) if isinstance(stop_price, (int, float)) and stop_price > 0 else "계산중"

        # 🔧 VWAP 표시
        # 🔧 FIX: vwap_gap=0 도 유효값 → falsy 체크 대신 None 체크
        _vwap_gap_str = f" VWAP{pre.get('vwap_gap', 0):+.1f}%" if pre.get('vwap_gap') is not None else ""

        # 전략 설명 매핑
        _buy_group = pre.get("v4_logic_group", "?")
        _buy_filters_str = " ".join(pre.get("v4_filters_hit", []))
        _buy_desc_map = {
            "GT": "5분RSI≥73 과매수 + 1분양봉 + 거래량폭증 → 강모멘텀 추격 (180초 시간청산)",
            "C": "15분 음→양 반전 + 종가회복0.1%↑ + 1분양봉 + 거래량동반 → 초입반전 포착 (빠른익절)",
        }
        _buy_strat_desc = _buy_desc_map.get(_buy_group, _buy_filters_str)

        # 🔧 FIX: 박스 진입은 박스 코드에서 별도 알람 발송 → 여기서 중복 발송 방지
        if not pre.get("is_box"):
            tg_send(
                f"{mode_emoji} <b>[{mode_label}] 자동매수</b> {m}\n"
                f"• 전략: {signal_tag} [{_buy_group}] | {_buy_filters_str}\n"
                f"• 신호가: {fmt6(signal_price)}원 → 체결가: {fmt6(avg_price)}원 ({slip_pct*100:+.2f}%)\n"
                f"• 주문: {krw_to_use:,.0f}원 ({actual_pct:.1f}%) | 수량: {volume_filled:.6f}\n"
                f"• 손절: {safe_stop_str}원 (SL {eff_sl_pct*100:.2f}%){_vwap_gap_str}\n"
                f"{link_for(m)}"
            )

        # 🔧 FIX: 최근 매수 시간 기록 + 유령감지 방지 (레이스컨디션 대비)
        with _RECENT_BUY_LOCK:
            _RECENT_BUY_TS[m] = time.time()
        with _ORPHAN_LOCK:
            _ORPHAN_HANDLED.add(m)

        # === 🧠 피처 로깅 (거래 데이터 기록 + 배치/경로 리포트용) ===
        # 🔧 FIX: AUTO_LEARN_ENABLED 무관하게 항상 실행 (배치 리포트가 CSV 의존)
        try:
            ob = pre.get("ob", {})
            t = pre.get("tape", {})
            ticks = pre.get("ticks", [])
            imbalance = calc_orderbook_imbalance(ob) if ob else 0
            turn = t.get("krw", 0) / max(ob.get("depth_krw", 1), 1) if ob else 0

            # 🔥 새 지표 계산
            cons_buys = calc_consecutive_buys(ticks, 15)
            t15_stats = micro_tape_stats_from_ticks(ticks, 15)
            avg_krw = calc_avg_krw_per_tick(t15_stats)
            flow_accel = calc_flow_acceleration(ticks)

            # 🚀 초단기 미세필터 지표 계산
            ia_stats = inter_arrival_stats(ticks, 30) if ticks else {"cv": 0.0}
            cv = ia_stats.get("cv") or 0.0
            pstd = price_band_std(ticks, sec=10) if ticks else None
            pstd = pstd if pstd is not None else 0.0
            overheat = flow_accel * float(pre.get("volume_surge", 1.0))
            # 틱 신선도
            fresh_age = 0.0
            if ticks:
                now_ms = int(time.time() * 1000)
                last_tick_ts = max(tick_ts_ms(t) for t in ticks)
                if last_tick_ts == 0: last_tick_ts = now_ms
                fresh_age = (now_ms - last_tick_ts) / 1000.0
            # 베스트호가 깊이
            try:
                u0 = ob.get("raw", {}).get("orderbook_units", [])[0]
                best_ask_krw = float(u0["ask_price"]) * float(u0["ask_size"])
            except Exception:
                best_ask_krw = 0.0

            # 🔍 경로 정보: signal_tag 하나로 통일
            log_trade_features({
                "ts": now_kst_str(),
                "market": m,
                "entry_price": avg_price,
                "buy_ratio": t.get("buy_ratio", 0),
                "spread": ob.get("spread", 0),
                "turn": turn,
                "imbalance": imbalance,
                "volume_surge": pre.get("volume_surge", 1.0),
                "fresh": 1 if last_two_ticks_fresh(ticks) else 0,
                "score": pre.get("ignition_score", 0),
                "entry_mode": entry_mode,
                "signal_tag": pre.get("signal_tag", "기본"),
                "filter_type": pre.get("filter_type", "stage1_gate"),
                # 🔥 새 지표
                "consecutive_buys": cons_buys,
                "avg_krw_per_tick": round(avg_krw, 0),
                "flow_acceleration": round(flow_accel, 2),
                # 🚀 초단기 미세필터 지표
                "overheat": round(overheat, 2),
                "fresh_age": round(fresh_age, 2),
                "cv": round(cv, 2),
                "pstd": round(pstd * 100, 4),
                "best_ask_krw": int(best_ask_krw),
                "shadow_flags": pre.get("shadow_flags", ""),
                "would_cut": 1 if pre.get("would_cut", False) else 0,
                "is_prebreak": 1 if pre.get("is_prebreak", False) else 0,
                # 🔧 데이터수집: 손절폭/트레일 간격 튜닝용 (진입시 기록)
                "entry_atr_pct": round(_entry_atr_pct, 4),
                "entry_pstd": round(_entry_pstd * 100, 4),
                "entry_spread": round(ob.get("spread", 0), 4),
                "entry_consec": cons_buys,
                # 🔧 v7: 진입 컨텍스트 스냅샷
                "entry_rsi": round(_ctx_rsi, 2) if _ctx_rsi is not None else None,
                "entry_vr5": round(_ctx_vr5, 2),
                "entry_buy_ratio": round(_ctx_buy_ratio, 4),
                "entry_accel": round(_ctx_accel, 3),
            })
        except Exception as e:
            print(f"[FEATURE_LOG_ERR] {e}")

        # 🔐 컨텍스트 종료 시 entry_lock 자동 해제

    # 🔧 FIX: pending 고착 방지 — with 블록 종료 후 open 미전환 시 pending 강제 제거
    # - 정상 return(각 분기에서 pop)은 이미 처리됨
    # - 예외 발생 시 caller(main loop)에서 처리 + 여기서 이중 방어
    if not _entered_open:
        with _POSITION_LOCK:
            _pos_check = OPEN_POSITIONS.get(m)
            if _pos_check and _pos_check.get("state") == "pending":
                OPEN_POSITIONS.pop(m, None)
                print(f"[PENDING_CLEANUP] {m} open 미전환 → pending 제거")

def _reset_pos_after_reprice(pos: dict, new_entry: float, curp: float):
    """
    평단(entry_price) 변경 후 OPEN_POSITIONS dict의 추적 상태를 완전 리셋.
    - 로컬 변수 리셋은 monitor_position 호출부에서 별도 수행
    - 여기서는 dict에 저장되는 공유 상태만 처리
    """
    now = time.time()
    # MFE/MAE
    pos["mfe_pct"] = 0.0
    pos["mae_pct"] = 0.0
    # 컨텍스트 청산 카운트 (누적 방지)
    pos["ctx_close_count"] = 0
    # 부분익절 상태 (이전 평단 기준 partial 잔상 제거)
    pos["partial_state"] = None
    pos.pop("partial_ts", None)
    pos.pop("partial_price", None)
    pos.pop("partial_type", None)
    # 본절 플래그 (새 평단 기준 재판정)
    pos["breakeven_set"] = False
    return pos


def add_auto_position(m, cur_price, reason=""):
    """
    이미 seed 포지션이 있을 때, 강한 추세에서 1회 추매(add) 수행
    - ADD_RISK_FRACTION 비율만큼 RISK_PER_TRADE를 다시 사용
    - 평균단가 재계산
    """
    if not AUTO_TRADE:
        return False, None

    if not UPBIT_ACCESS_KEY or not UPBIT_SECRET_KEY:
        return False, None

    # 🔧 FIX: pos dict를 락 안에서 필요한 값 모두 복사 (락 밖 읽기 레이스 방지)
    with _POSITION_LOCK:
        pos = OPEN_POSITIONS.get(m)
        if not pos or pos.get("volume", 0) <= 0:
            return False, None
        if pos.get("added"):
            # 이미 한 번 추매한 포지션
            return False, None
        last_add_ts = pos.get("last_add_ts", 0.0)
        entry_price_old = pos.get("entry_price", 0)
        stop_price = pos.get("stop", 0)

    now = time.time()
    if (now - last_add_ts) < PYRAMID_ADD_COOLDOWN_SEC:
        return False, None

    accounts = get_account_info()
    if not accounts:
        print("[AUTO_ADD] 계좌 조회 실패")
        return False, None

    # 🔧 FIX: locked 반영한 가용잔고 계산
    krw_bal = get_available_krw(accounts)

    if krw_bal < 6000:
        print(f"[AUTO_ADD] KRW 부족({krw_bal:,.0f}) → 추매 스킵")
        return False, None

    # 추매도 적응형 리스크 적용 (연패/연승 반영)
    # 🔧 FIX: 추매에는 리스크 캡 적용 (연승 중 과도한 리스크 확대 방지)
    # - seed는 streak 보너스 100% 적용
    # - add는 캡으로 제한 (연승 보너스가 추매까지 타면 DD 확대 위험)
    adaptive = max(0.0001, get_adaptive_risk())
    add_risk_pct = max(0.0001, adaptive * ADD_RISK_FRACTION)
    add_risk_pct = min(add_risk_pct, RISK_PER_TRADE * 0.6)  # 캡: 기본 리스크의 60%
    qty_theoretical = calc_position_size(cur_price, stop_price, krw_bal, add_risk_pct)
    krw_to_use = qty_theoretical * cur_price

    if krw_to_use < 5000:
        print(f"[AUTO_ADD] 주문 금액 {krw_to_use:,.0f}원 < 5,000원 → 스킵")
        return False, None

    krw_to_use = int(krw_to_use)

    print(
        f"[AUTO_ADD] {m} 추매 시도: {krw_to_use:,.0f} KRW "
        f"(이론수량≈{qty_theoretical:.6f}, 현재가 {cur_price:,.0f})"
    )

    volume_filled = 0.0
    avg_price_add = cur_price

    # 🔧 FIX: hybrid_buy 잔여 시장가 체결분 보정용 사전 잔고 (open_auto_position과 동일)
    coin = m.split("-")[1] if "-" in m else m
    _prev_bal_add = 0.0
    for _a in (accounts or []):
        if _a.get("currency") == coin:
            _prev_bal_add = float(_a.get("balance") or "0")
            break

    try:
        # 추매도 하이브리드 매수 적용
        _ob_add = fetch_orderbook_cache([m]).get(m) if m else None
        res = hybrid_buy(m, krw_to_use, ob_data=_ob_add, timeout_sec=1.0)
        order_uuid = res.get("uuid") if isinstance(res, dict) else None
        od = get_order_result(order_uuid, timeout_sec=12.0) if order_uuid else None

        if od:
            try:
                volume_filled = float(od.get("executed_volume") or "0")
            except Exception:
                volume_filled = 0.0

            trades = od.get("trades") or []
            if trades and volume_filled > 0:
                total_krw = 0.0
                total_vol = 0.0
                for tr in trades:
                    p = float(tr.get("price", "0"))
                    v = float(tr.get("volume", "0"))
                    total_krw += p * v
                    total_vol += v
                if total_vol > 0:
                    avg_price_add = total_krw / total_vol
            else:
                if volume_filled > 0:
                    avg_price_add = krw_to_use / volume_filled

        # 🔧 FIX: hybrid_buy 잔여 시장가 체결분 보정 (open_auto_position HYBRID_VOL_FIX와 동일)
        # - hybrid_buy는 limit_res UUID만 반환 → 시장가 추가분 누락 가능
        # - 잔고 차이로 실제 총 체결량/평단 보정
        if volume_filled > 0:
            try:
                time.sleep(0.3)
                _post_acc_add = get_account_info()
                _post_bal_add = 0.0
                _post_avg_add = 0.0
                for _a in (_post_acc_add or []):
                    if _a.get("currency") == coin:
                        _post_bal_add = float(_a.get("balance") or "0")
                        _post_avg_add = float(_a.get("avg_buy_price") or "0")
                        break
                _bal_diff_add = _post_bal_add - _prev_bal_add
                if _bal_diff_add > volume_filled + 1e-12:  # 잔고 증가분이 조금이라도 크면 항상 보정
                    print(f"[ADD_VOL_FIX] {m} od.executed={volume_filled:.8f} < 잔고증가={_bal_diff_add:.8f} → 보정")
                    volume_filled = _bal_diff_add
                    if _post_avg_add > 0:
                        avg_price_add = _post_avg_add
            except Exception as _avf_err:
                print(f"[ADD_VOL_FIX_ERR] {m}: {_avf_err}")

        if volume_filled <= 0:
            msg = f"[AUTO_ADD] {m} 추매 체결 0 → 무시"
            print(msg)
            tg_send(f"⚠️ {msg}")
            return False, None

    except Exception as e:
        print("[AUTO_ADD ERR]", e)
        tg_send(f"⚠️ <b>추매 실패</b> {m}\n사유: {e}")
        return False, None

    # 🔧 FIX: 네트워크 I/O를 락 바깥에서 수행 (_POSITION_LOCK 장기 점유 방지)
    c1_for_sl = get_minutes_candles(1, m, 20)

    # 평균단가 갱신
    with _POSITION_LOCK:
        pos = OPEN_POSITIONS.get(m)
        if not pos:
            return False, None
        old_vol = pos.get("volume", 0.0)
        new_vol = old_vol + volume_filled
        if new_vol <= 0:
            return False, None
        # 🔧 FIX: entry_price를 같은 락 내에서 재읽기 (초기 읽기와 여기 사이 변경 가능)
        _ep_old = pos.get("entry_price", entry_price_old)
        new_entry_price = (_ep_old * old_vol + avg_price_add * volume_filled) / new_vol

        pos["entry_price"] = new_entry_price
        pos["volume"] = new_vol
        pos["added"] = True
        pos["last_add_ts"] = time.time()
        # 🔧 FIX: 원래 entry_mode 보존 (추매했다고 confirm으로 덮어쓰면 추적 부정확)
        if pos.get("entry_mode") == "probe":
            pos["entry_mode"] = "probe_added"  # probe에서 추매 = 별도 추적

        # 🔧 FIX: 평단 변경 시 dict 추적 상태 완전 리셋
        # (mfe/mae + ctx_close_count + partial_state + breakeven_set)
        _reset_pos_after_reprice(pos, new_entry_price, cur_price)

        # 🔧 FIX: 추매 후 손절가 재계산 (평단이 바뀌었으므로)
        try:
            _sig_type_for_sl = pos.get("signal_type", "normal")
            new_stop, new_sl_pct, _ = dynamic_stop_loss(new_entry_price, c1_for_sl, _sig_type_for_sl, current_price=cur_price, trade_type=pos.get("trade_type"), market=m)
            pos["stop"] = new_stop
            pos["sl_pct"] = new_sl_pct
        except Exception as e:
            print(f"[PYRA_STOP_ERR] 추매 후 손절가 갱신 실패: {e}")

    gain_from_old = (avg_price_add / entry_price_old - 1) * 100 if entry_price_old > 0 else 0
    tg_send(
        f"📈 <b>추매 체결</b> {m}\n"
        f"• 사유: {reason or '추세강화'}\n"
        f"• 기존평단: {fmt6(entry_price_old)}원 → 신규평단: {fmt6(new_entry_price)}원\n"
        f"• 추가 체결가: {fmt6(avg_price_add)}원 (평단대비 {gain_from_old:+.2f}%)\n"
        f"• 추가 수량: {volume_filled:.6f} / 총 수량: {new_vol:.6f}\n"
        f"{link_for(m)}"
    )

    return True, new_entry_price

# =========================
# 🔥 자동 청산
# =========================
def close_auto_position(m, reason=""):
    """
    손절/청산 시 자동 매도 (찌꺼기 방지 포함)
    """
    if not AUTO_TRADE:
        print(f"[AUTO] AUTO_TRADE=0 → 청산 스킵 ({m}, reason={reason})")
        return

    # 🔧 FIX: 중복 청산 방지 락 (동시 청산 시도 시 한 쪽만 실행)
    # 🔧 FIX (B): 락 안에서 복사본 생성 → 락 해제 후 레이스 방지
    with _POSITION_LOCK:
        if m in _CLOSING_MARKETS:
            print(f"[AUTO] {m} 이미 청산 진행 중 → 스킵 (reason={reason})")
            return
        _CLOSING_MARKETS.add(m)
        pos_raw = OPEN_POSITIONS.get(m)
        # 🔧 FIX: deepcopy를 try 안으로 이동 — deepcopy 예외 시 _CLOSING_MARKETS 영구 잠김 방지
        try:
            pos = copy.deepcopy(pos_raw) if pos_raw else None
        except Exception:
            _CLOSING_MARKETS.discard(m)
            raise

    try:
        if not pos:
            print(f"[AUTO] OPEN_POSITIONS에 {m} 포지션 없음 → 청산 스킵 (reason={reason})")
            # 🔧 FIX: _CLOSING_MARKETS.discard(m) → finally 블록에서 일괄 처리
            tg_send_mid(f"⚠️ {m} 청산 스킵 (포지션 없음)\n• 사유: {reason}")
            return

        # 🔧 FIX: 실제 잔고 먼저 체크 (race condition 방지 - 동시 모니터링 시 중복 청산 방지)
        # 🔧 FIX: 매도 시에는 실제 매도 가능량(balance만) 사용 — locked는 매도 불가
        actual_vol = get_actual_balance(m)
        # 🔧 FIX: -1 = 조회 실패 → 청산 진행하지 않음 (오탐 방지)
        if actual_vol < 0:
            print(f"[AUTO] {m} 잔고 조회 실패 → 청산 스킵 (reason={reason})")
            # 🔧 FIX: _CLOSING_MARKETS.discard(m) → finally 블록에서 일괄 처리
            tg_send_mid(f"⚠️ {m} 청산 스킵 (잔고 조회 실패)\n• 사유: {reason}")
            return
        if actual_vol <= 1e-12:
            print(f"[AUTO] {m} 실잔고=0 → 이미 청산됨, 스킵 (reason={reason})")
            # 🔧 FIX: mark_position_closed로 state 마킹 후 정리
            mark_position_closed(m, f"already_zero:{reason}")
            # 🔧 FIX: _CLOSING_MARKETS.discard(m) → finally 블록에서 일괄 처리
            tg_send(f"🧹 {m} 이미 청산됨 (잔고=0 확인)\n• 사유: {reason}")
            return

        # 실제 보유량 사용
        vol = actual_vol

        # 🔧 청산 시도 알람 (실잔고 확인 후에만 발송)
        tg_send(f"💣 {m} 청산시도\n• 사유: {reason}\n• 수량: {vol:.4f}")

        if vol <= 0:
            print(f"[AUTO] {m} volume<=0 ({vol}) → 포지션 제거만 수행")
            with _POSITION_LOCK:
                OPEN_POSITIONS.pop(m, None)
            # 🔧 FIX: volume 0이어도 알람 + 리포트 카운트 증가
            tg_send(f"⚠️ {m} 청산 완료 (수량 0 확인)\n• 사유: {reason}\n• 외부 청산 또는 이미 정리됨")
            # 🔧 FIX: AUTO_LEARN_ENABLED 무관하게 항상 호출 (배치 리포트 카운터 증가)
            try:
                update_trade_result(m, 0, 0, 0, exit_reason=reason or "잔고0_외부청산")
            except Exception:
                pass
            return

        entry_price = pos.get("entry_price", 0)

        # 현재가(청산 전 기준) 조회 - ✅ 퍼블릭 API 사용
        # 🔧 FIX: IndexError 방어 (빈 리스트 체크)
        try:
            cur_js = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": m}, retries=2)  # 🔧 FIX: 청산 경로 retries 강화
            cur_price = cur_js[0].get("trade_price", entry_price) if cur_js and len(cur_js) > 0 else entry_price
        except (IndexError, Exception):
            cur_price = entry_price

        # 선 계산(대략)
        ret_pct = (cur_price / entry_price -
                   1.0) * 100.0 if entry_price > 0 else 0.0
        est_entry_value = entry_price * vol
        est_exit_value = cur_price * vol
        pl_value = est_exit_value - est_entry_value

        print(
            f"[AUTO] {m} 청산 시도: volume={vol}, reason={reason}, PnL(선계산)={ret_pct:+.2f}%"
        )

        exit_price_used = cur_price  # 실제 체결가 성공 시 교체

        try:
            res = place_market_sell(m, vol, price_hint=cur_price)  # 🔧 가격 힌트 전달
            order_uuid = res.get("uuid") if isinstance(res, dict) else None
            if order_uuid:
                od = get_order_result(order_uuid, timeout_sec=20.0)  # 🔧 12→20초로 증가
            else:
                od = None

            # 실제 체결량/체결가 계산 (🔧 FIX: trades 없을 때 executed_volume 대체 사용)
            executed = 0.0
            if od:
                trades = od.get("trades") or []
                total_krw = 0.0
                total_vol = 0.0
                for tr in trades:
                    try:
                        p = float(tr.get("price", "0"))
                        v = float(tr.get("volume", "0"))
                    except Exception:
                        continue
                    total_krw += p * v
                    total_vol += v
                executed = total_vol
                if total_vol > 0:
                    exit_price_used = total_krw / total_vol
                # 🔧 FIX: trades 배열 없어도 executed_volume이 있으면 체결된 것으로 간주
                if executed <= 0:
                    try:
                        executed = float(od.get("executed_volume") or "0")
                        if executed > 0:
                            # 평균 체결가 계산 (가능하면)
                            try:
                                avg_price = float(od.get("avg_price") or "0")
                                if avg_price > 0:
                                    exit_price_used = avg_price
                            except Exception:
                                pass
                            print(f"[AUTO] {m} trades 없음, executed_volume={executed:.6f}로 대체")
                    except Exception:
                        pass

            # 🔧 FIX #1: 미체결 시 잔고 재확인 후 처리 (지연 체결 대응 강화)
            if executed <= 0:
                # 🔧 PATCH: 최대 30초까지 잔고+locked=0 재확인 (지연 체결 대비)
                for _retry in range(15):  # 15회 x 2초 = 30초
                    time.sleep(2.0)
                    actual_after = get_balance_with_locked(m)  # 🔧 locked 포함
                    if actual_after < 0:
                        continue  # 🔧 FIX: API 실패(-1)를 잔고 0으로 오판 방지
                    if actual_after <= 1e-12:
                        # 실잔고+locked 0 = 체결된 것으로 간주
                        with _POSITION_LOCK:
                            OPEN_POSITIONS.pop(m, None)

                        # 🔧 FIX: 지연청산 시 실제 체결가 조회 시도 (학습 데이터 정확도 개선)
                        if order_uuid:
                            try:
                                od_delayed = get_order_result(order_uuid, timeout_sec=8.0)
                                if od_delayed:
                                    delayed_avg = float(od_delayed.get("avg_price") or "0")
                                    if delayed_avg > 0:
                                        exit_price_used = delayed_avg
                                        ret_pct = (exit_price_used / entry_price - 1.0) * 100.0 if entry_price > 0 else 0.0
                                        print(f"[DELAYED] {m} 실제 체결가 조회 성공: {delayed_avg:.0f}원 → ret={ret_pct:+.2f}%")
                            except Exception as _delayed_err:
                                print(f"[DELAYED_PRICE_ERR] {m} 체결가 조회 실패 (추정값 사용): {_delayed_err}")

                        tg_send(f"🧹 <b>자동청산 완료(지연확인)</b> {m}\n• 주문응답 지연으로 잔고=0 확인 후 완료 처리\n• 사유: {reason}")
                        # 🔧 FIX: 지연청산에서도 record_trade 기록 (승률 기반 리스크 조정에 필수)
                        # 🔧 FIX: 수수료 반영한 순수익률 사용
                        net_ret_delayed = ret_pct - (FEE_RATE_ROUNDTRIP * 100.0)
                        try:
                            record_trade(m, net_ret_delayed / 100.0, pos.get("signal_type", "기본"))  # 🔧 수수료 반영
                        except Exception as _e:
                            print("[DELAYED_TRADE_RECORD_ERR]", _e)
                        # 🔧 FIX: AUTO_LEARN_ENABLED 무관하게 항상 호출 (배치 리포트 카운터)
                        try:
                            hold_sec = time.time() - pos.get("entry_ts", time.time())
                            mfe = pos.get("mfe_pct", 0.0)
                            mae = pos.get("mae_pct", 0.0)
                            update_trade_result(m, exit_price_used, net_ret_delayed/100.0 if entry_price else 0, hold_sec,
                                                added=pos.get('added', False), exit_reason=reason,
                                                mfe_pct=mfe, mae_pct=mae,
                                                entry_ts=pos.get("entry_ts"))
                        except Exception as _e:
                            print("[DELAYED_CLOSE_LOG_ERR]", _e)
                        return

                # 30초 후에도 잔고 있으면 → 후속 워커로 추가 감시
                print(f"[AUTO] {m} 청산 미체결 → 후속 감시 시작")
                tg_send(f"⚠️ <b>자동청산 미체결</b> {m}\n사유: 체결 지연 / 후속 감시 진행")

                def _followup_check():
                    try:
                        _fup_exit_price = cur_price  # 🔧 FIX: 초기값은 closure의 cur_price
                        for _ in range(120):  # 추가 4분 감시 (120회 x 2초)
                            time.sleep(2.0)
                            _fup_bal = get_balance_with_locked(m)  # 🔧 locked 포함
                            if _fup_bal < 0:
                                continue  # 🔧 FIX: API 실패(-1)를 잔고 0으로 오판 방지
                            if _fup_bal <= 1e-12:
                                # 🔧 FIX: 실제 체결가 조회 (stale cur_price 사용 방지)
                                if order_uuid:
                                    try:
                                        _od_fup = get_order_result(order_uuid, timeout_sec=5.0)
                                        if _od_fup:
                                            _fup_avg = float(_od_fup.get("avg_price") or "0")
                                            if _fup_avg > 0:
                                                _fup_exit_price = _fup_avg
                                    except Exception:
                                        pass
                                with _POSITION_LOCK:
                                    OPEN_POSITIONS.pop(m, None)
                                tg_send(f"🧹 <b>자동청산 완료(후속확인)</b> {m}\n• 사유: {reason}")
                                # 🔧 FIX: 후속확인 청산에서도 record_trade + trade result 기록 (누락 방지)
                                try:
                                    _net_ret = (_fup_exit_price / entry_price - 1.0 - FEE_RATE_ROUNDTRIP) if entry_price > 0 else 0
                                    record_trade(m, _net_ret, pos.get("signal_type", "기본"))  # 🔧 FIX: 승률/연패 추적 누락 방지
                                except Exception:
                                    pass
                                # 🔧 FIX: AUTO_LEARN_ENABLED 무관하게 항상 호출 (배치 리포트 카운터)
                                try:
                                    _hold = time.time() - pos.get("entry_ts", time.time())
                                    _mfe = pos.get("mfe_pct", 0.0)
                                    _mae = pos.get("mae_pct", 0.0)
                                    update_trade_result(m, _fup_exit_price, _net_ret, _hold,
                                                        added=pos.get('added', False),
                                                        exit_reason=reason or "후속확인_청산",
                                                        mfe_pct=_mfe, mae_pct=_mae,
                                                        entry_ts=pos.get("entry_ts"))
                                except Exception as _e:
                                    print(f"[FOLLOWUP_TRADE_LOG_ERR] {_e}")
                                return
                        # 🔧 4분 후에도 미체결 → 경고 알림
                        tg_send(f"🚨 <b>{m} 청산 미완료</b>\n• 4분 후속감시 종료, 수동 확인 필요\n• 사유: {reason}")
                    except Exception as e:
                        print("[FOLLOWUP_ERR]", e)
                        # 🔧 FIX: 예외 발생해도 알람 발송
                        tg_send(f"🚨 <b>{m} 후속감시 오류</b>\n• 예외: {e}\n• 수동 확인 필요")
                threading.Thread(target=_followup_check, daemon=True).start()
                return

            # 🔧 FIX #1: 부분체결 시 잔여량으로 업데이트
            if executed < vol - 1e-10:
                remaining = max(vol - executed, 0.0)
                remaining_krw = exit_price_used * remaining
                # ✅ 잔여가 최소주문금액(5000원) 미만이면 dust 처리
                is_dust = remaining_krw < 5000 and remaining > 1e-12
                with _POSITION_LOCK:
                    pos2 = OPEN_POSITIONS.get(m)
                    _need_orphan_add = False
                    if pos2:
                        if remaining <= 1e-12:
                            # 잔여 없음 → 포지션 제거
                            OPEN_POSITIONS.pop(m, None)
                        elif is_dust:
                            # 🔧 FIX: dust 잔여는 포지션 제거 + _ORPHAN_HANDLED에 등록
                            # → 유령으로 감지되지 않음 (부분청산 후 손절 방지)
                            OPEN_POSITIONS.pop(m, None)
                            _need_orphan_add = True  # 🔧 LOCK_ORDER: _POSITION_LOCK 밖에서 _ORPHAN_LOCK 획득
                        else:
                            pos2["volume"] = remaining
                            pos2["last_exit_ts"] = time.time()
                            OPEN_POSITIONS[m] = pos2
                # 🔧 LOCK_ORDER FIX: _ORPHAN_LOCK은 _POSITION_LOCK 밖에서 획득 (nested lock 제거)
                if _need_orphan_add:
                    with _ORPHAN_LOCK:
                        _ORPHAN_HANDLED.add(m)
                    print(f"[AUTO] {m} dust 잔여 ({remaining_krw:.0f}원) → _ORPHAN_HANDLED 등록")
                    # 🔧 FIX: 10분 후 자동 해제 — 코인 가격 급등 시 재감지 허용
                    def _dust_expire(_m=m):
                        time.sleep(600)
                        with _ORPHAN_LOCK:
                            _ORPHAN_HANDLED.discard(_m)
                        print(f"[DUST_EXPIRE] {_m} _ORPHAN_HANDLED 만료 → 재감지 허용")
                    threading.Thread(target=_dust_expire, daemon=True).start()
                print(f"[AUTO] {m} 부분체결: {executed:.6f}/{vol:.6f} → 잔여 {remaining:.6f} ({remaining_krw:.0f}원)")
                if remaining > 1e-12 and not is_dust:
                    tg_send(f"⚠️ <b>부분체결</b> {m}\n체결: {executed:.6f} / 잔여: {remaining:.6f}")
                elif is_dust:
                    tg_send(f"⚠️ <b>부분체결</b> {m}\n체결: {executed:.6f} / 잔여 {remaining:.6f} (dust, 유령감지 제외)")
                else:
                    tg_send(f"⚠️ <b>부분체결</b> {m}\n체결: {executed:.6f} / 잔여 미달 → 정리완료")
                # 부분체결도 손익 계산은 함 (executed 기준)
                vol = executed  # 아래 손익 계산용

            # 🔧 FIX #1: 전량체결 시에만 포지션 제거
            else:
                # 🔧 FIX: mark_position_closed로 state 마킹 후 정리
                mark_position_closed(m, f"full_close:{reason}")

                # 🔧 찌꺼기 청소: 전량 매도 후에도 소수점 잔량이 남을 수 있음
                try:
                    time.sleep(0.5)
                    dust_bal = get_actual_balance(m)
                    if dust_bal > 1e-12:
                        dust_krw = dust_bal * exit_price_used
                        if dust_krw >= 5000:
                            # 5000원 이상이면 추가 매도 시도
                            place_market_sell(m, dust_bal, price_hint=exit_price_used)
                            print(f"[DUST_CLEAN] {m} 잔여 {dust_bal:.8f} ({dust_krw:.0f}원) 추가 매도")
                        else:
                            # 5000원 미만이면 매도 불가 → 유령감지 제외만
                            with _ORPHAN_LOCK:
                                _ORPHAN_HANDLED.add(m)
                            print(f"[DUST_CLEAN] {m} 잔여 {dust_bal:.8f} ({dust_krw:.0f}원) → 최소금액 미달, 유령제외")
                except Exception as _dust_err:
                    print(f"[DUST_CLEAN_ERR] {m}: {_dust_err}")

            # 🔧 FIX: 실제 체결량(vol=executed) 기준으로 재계산
            est_entry_value = entry_price * vol  # ✅ executed 기준으로 재계산
            est_exit_value = exit_price_used * vol
            pl_value = est_exit_value - est_entry_value
            gross_ret_pct = (exit_price_used / entry_price -
                             1.0) * 100.0 if entry_price > 0 else 0.0
            # 🔧 FIX: 수수료 반영한 순수익률 계산 (승률/리스크 튜닝 정확도 개선)
            net_ret_pct = gross_ret_pct - (FEE_RATE_ROUNDTRIP * 100.0)  # 왕복 0.1% 차감
            ret_pct = gross_ret_pct  # 알람용 (기존 유지)

            # 🔧 FIX: 청산 슬립 기록 (진입 슬립만 기록하면 과소추정됨)
            # 🔧 FIX: 상한 캡 추가 (0.5%) - 가격이동이 슬립으로 섞이면 TP 과도 상승
            # - cur_price는 주문 직전 티커값 → 체결까지 시장 이동분 포함될 수 있음
            # - 이상치 제외로 expected_exit_slip_pct 정확도 개선
            if cur_price > 0 and exit_price_used > 0:
                # 🔧 FIX: 슬립 캡 0.5% → 0.25% (시장이동이 슬립으로 오염 → TP 과도 지연 방지)
                exit_slip = min(0.0025, abs(exit_price_used / cur_price - 1.0))  # 0.25% 캡
                _EXIT_SLIP_HISTORY.append(exit_slip)  # 🔧 FIX: exit 전용
                # FIX [M4]: _SLIP_HISTORY 제거됨 (entry/exit 분리로 대체)

            # ✅ 거래 결과 기록 (승률 기반 리스크 튜닝에 사용)
            # 🔧 FIX: net_ret_pct 사용 — gross/net 혼용 제거 (지연청산/DCB와 통일)
            # (방어코드가 record_trade 내부에도 있지만, 호출부에서도 정확히 넣기)
            try:
                record_trade(m, net_ret_pct / 100.0, pos.get("signal_type", "기본"))
            except Exception as _e:
                print("[TRADE_RECORD_ERR]", _e)

            # 🔧 FIX: AUTO_LEARN_ENABLED 무관하게 항상 호출 (배치 리포트 카운터)
            try:
                hold_sec = time.time() - pos.get("entry_ts", time.time())
                was_added = pos.get("added", False)
                mfe = pos.get("mfe_pct", 0.0)
                mae = pos.get("mae_pct", 0.0)
                update_trade_result(m, exit_price_used, net_ret_pct / 100.0, hold_sec,
                                    added=was_added, exit_reason=reason,
                                    mfe_pct=mfe, mae_pct=mae,
                                    entry_ts=pos.get("entry_ts"),
                                    pos_snapshot=dict(pos))
            except Exception as _e:
                print(f"[FEATURE_UPDATE_ERR] {_e}")

            # 🔧 FIX: net_ret_pct 기준 판정 (gross 기준 시 수수료 미반영으로 마이너스인데 🟢 표기 버그)
            result_emoji = "🟢" if net_ret_pct > 0 else "🔴"
            fee_total = (est_entry_value + est_exit_value) * FEE_RATE_ONEWAY  # 🔧 FIX: 편도요율×양쪽 = 왕복수수료
            # 🔧 FIX: 순손익을 실제 net(수수료 차감)으로 표시 (gross→net 왜곡 방지)
            net_pl_value = pl_value - fee_total

            # 🔧 DEBUG: 청산 알람 발송 직전 로그 (손실 거래만 출력)
            if net_ret_pct <= 0:
                print(f"[CLOSE_DEBUG] {m} 청산알람 발송 직전 | ret={net_ret_pct:.2f}%(net) vol={vol:.6f} exit_price={exit_price_used}")

            # 🔧 FIX: 청산 알람용 튜닝 데이터는 LIVE 포지션에서 재읽기 (deepcopy는 시작 시점 스냅샷 → 모니터 갱신값 누락)
            # mark_position_closed() 이후라 OPEN_POSITIONS에서 이미 제거됐을 수 있으므로, deepcopy를 fallback으로 사용
            with _POSITION_LOCK:
                _live_pos = OPEN_POSITIONS.get(m) or {}
            _pos_data = {}
            # 먼저 deepcopy(pos)의 값으로 초기화, 그 위에 live 값 덮어쓰기 (최신값 우선)
            if pos:
                _pos_data.update(pos)
            if _live_pos:
                for _tkey in ("mfe_pct", "mae_pct", "mfe_sec", "trail_dist", "trail_stop_pct", "entry_atr_pct", "entry_pstd"):
                    if _tkey in _live_pos:
                        _pos_data[_tkey] = _live_pos[_tkey]
            _hold_sec = time.time() - _pos_data.get("entry_ts", time.time())
            _mfe_val = _pos_data.get("mfe_pct", 0.0)
            _mae_val = _pos_data.get("mae_pct", 0.0)
            _mfe_sec_val = _pos_data.get("mfe_sec", 0)
            _entry_atr_val = _pos_data.get("entry_atr_pct", 0)
            _entry_pstd_val = _pos_data.get("entry_pstd", 0)
            _trail_dist_val = _pos_data.get("trail_dist", 0)
            _trail_stop_val = _pos_data.get("trail_stop_pct", 0)
            # 피크→청산 드롭 (트레일이 얼마나 줬는지)
            _peak_drop = _mfe_val - (net_ret_pct if net_ret_pct else 0)

            # 전략 정보 추출
            _exit_sig_tag = _pos_data.get('signal_tag', '?')
            _exit_group = "GT" if "GT" in _exit_sig_tag else ("C" if "반전_15m" in _exit_sig_tag else "?")
            _exit_desc_map = {
                "GT": "강모멘텀 추격 (5분RSI≥73, 180초 시간청산)",
                "C": "초입반전 포착 (15분 음→양, 빠른익절)",
            }
            _exit_strat_desc = _exit_desc_map.get(_exit_group, "")

            tg_send(
                f"====================================\n"
                f"{result_emoji} <b>자동청산 완료</b> {m}\n"
                f"====================================\n"
                f"💰 순손익: {net_pl_value:+,.0f}원 (gross:{ret_pct:+.2f}% / net:{net_ret_pct:+.2f}%)\n"
                f"📊 매매차익: {pl_value:+,.0f}원 → 수수료 {fee_total:,.0f}원 차감 → 실현손익 {net_pl_value:+,.0f}원\n\n"
                f"• 전략: {_exit_sig_tag} [{_exit_group}]{' — ' + _exit_strat_desc if _exit_strat_desc else ''}\n"
                f"• 사유: {reason}\n"
                f"• 매수평단: {fmt6(entry_price)}원\n"
                f"• 실매도가: {fmt6(exit_price_used)}원\n"
                f"• 체결수량: {vol:.6f}\n"
                f"• 매수금액: {est_entry_value:,.0f}원\n"
                f"• 청산금액: {est_exit_value:,.0f}원\n"
                f"• 수수료: {fee_total:,.0f}원 (매수 {est_entry_value * FEE_RATE_ONEWAY:,.0f} + 매도 {est_exit_value * FEE_RATE_ONEWAY:,.0f})\n"
                f"-------- 튜닝 데이터 --------\n"
                f"• 보유: {_hold_sec:.0f}초 | MFE: +{_mfe_val:.2f}% ({_mfe_sec_val:.0f}초) | MAE: {_mae_val:.2f}%\n"
                f"• 피크드롭: {_peak_drop:.2f}% | 트레일: {_trail_dist_val:.3f}% (잠금 {_trail_stop_val:+.3f}%)\n"
                f"• 진입ATR: {_entry_atr_val:.3f}% | pstd: {_entry_pstd_val:.4f}% | SL: {_pos_data.get('sl_pct', 0)*100:.2f}%\n"
                f"• 모드: {_pos_data.get('entry_mode', '?')} | 타입: {_pos_data.get('signal_type', '?')}\n"
                f"• MFE시계열: {' → '.join(f'{k}초:+{v*100:.2f}%' for k, v in sorted(_pos_data.get('mfe_snapshots', {}).items(), key=lambda x: int(x[0])))}\n"
                f"====================================\n"
                f"{link_for(m)}"
            )

        except Exception as e:
            print("[AUTO SELL ERR]", e)
            traceback.print_exc()  # 🔧 DEBUG: 상세 에러 출력
            tg_send(f"⚠️ <b>자동청산 실패</b> {m}\n사유: {e}")

            # 🔧 FIX: 최소주문금액 미만 → 매도 불가 찌꺼기, 메모리 포지션만 정리
            # 🔧 FIX: _ORPHAN_HANDLED에 등록하여 유령으로 감지되지 않게 함
            if "최소주문금액" in str(e) or "5000" in str(e):
                with _POSITION_LOCK:
                    OPEN_POSITIONS.pop(m, None)
                with _ORPHAN_LOCK:
                    _ORPHAN_HANDLED.add(m)
                tg_send(f"🧹 {m} 청산 완료 (최소주문금액 미달 dust)\n• 소량 잔여는 거래소에 보유 (유령감지 제외)")
                # 🔧 FIX: AUTO_LEARN_ENABLED 무관하게 항상 호출 (배치 리포트 카운터)
                try:
                    update_trade_result(m, 0, 0, 0, exit_reason=reason or "최소주문금액_dust")
                except Exception:
                    pass
                return

            # 🔧 FIX: 400 에러 시 실제 잔고 확인 → 0이면 좀비 포지션 제거
            # 🔧 FIX: get_actual_balance → get_balance_with_locked (locked 포함, 유령 오탐 방지)
            if "400" in str(e) or "Bad Request" in str(e):
                actual_check = get_balance_with_locked(m)
                if actual_check < 0:
                    return  # 🔧 FIX: API 실패(-1)를 잔고0으로 오판하여 실제 포지션 삭제 방지
                if actual_check <= 1e-12:
                    print(f"[AUTO] {m} 잔고 0 확인 → 좀비 포지션 제거")
                    tg_send(f"🗑️ {m} 포지션 정리 완료 (실제 잔고 0 확인)")
                    with _POSITION_LOCK:
                        OPEN_POSITIONS.pop(m, None)
                    # 🔧 FIX: AUTO_LEARN_ENABLED 무관하게 항상 호출 (배치 리포트 카운터)
                    try:
                        update_trade_result(m, 0, 0, 0, exit_reason=reason or "좀비포지션_잔고0")
                    except Exception:
                        pass
            return
    finally:
        # 🔧 FIX: 중복 청산 방지 락 해제 (성공/실패 상관없이)
        with _POSITION_LOCK:
            _CLOSING_MARKETS.discard(m)


# 포지션 관리 — config.py에서 정의됨 (PARTIAL_PENDING_TIMEOUT, DUST_PREVENT_KRW)

def safe_partial_sell(m, sell_ratio=0.5, reason=""):
    """
    부분 청산 공용 함수
    - sell_ratio: 0.5 → 50%, 0.3 → 30% 등
    - partial_state + partial_ts로 1회만 부분청산/부분익절 허용 (크래시 후 봉인 방지)
      - None: 미수행
      - "pending": 진행 중 (타임아웃 시 자동 해제)
      - "done": 완료
    반환: (성공여부:bool, 메시지:str, 체결량:float)
    """
    with _POSITION_LOCK:
        # 🔧 FIX: 전량청산과 동일하게 _CLOSING_MARKETS 체크 (레이스 방지)
        if m in _CLOSING_MARKETS:
            msg = f"[REMONITOR] {m} 이미 청산 진행 중 → 부분청산 스킵"
            print(msg)
            return False, msg, 0.0
        pos = OPEN_POSITIONS.get(m)
        if not pos or pos.get("volume", 0) <= 0:
            msg = f"[REMONITOR] {m} 부분청산 실패: 포지션 없음/수량 0"
            print(msg)
            return False, msg, 0.0

        # 🔧 FIX: partial_state 기반 체크 (크래시 후 봉인 방지)
        # - "done": 이미 완료 → 스킵
        # - "pending": 진행 중이지만, 타임아웃(30초) 초과 시 자동 해제
        partial_state = pos.get("partial_state")
        partial_ts = pos.get("partial_ts", 0)

        if partial_state == "done":
            msg = f"[REMONITOR] {m} 부분청산 이미 수행됨 → 스킵"
            print(msg)
            return False, msg, 0.0
        elif partial_state == "pending":
            elapsed = time.time() - partial_ts
            if elapsed < PARTIAL_PENDING_TIMEOUT:
                msg = f"[REMONITOR] {m} 부분청산 진행 중 ({elapsed:.1f}초 경과) → 스킵"
                print(msg)
                return False, msg, 0.0
            else:
                # 타임아웃: pending 자동 해제 (크래시 후 복구 가능)
                print(f"[PARTIAL_TIMEOUT] {m} pending 상태 {elapsed:.1f}초 → 자동 해제 (재시도 허용)")
                pos["partial_state"] = None
                pos.pop("partial_ts", None)

        # 🔧 FIX: 부분청산도 _CLOSING_MARKETS에 등록 (전량청산과 동시 실행 방지)
        _CLOSING_MARKETS.add(m)
        # 🔧 FIX: TOCTOU 레이스 방지 - 락 해제 전 partial_state=pending 선점
        # 주문 실패 시 아래에서 None으로 롤백
        pos["partial_state"] = "pending"
        pos["partial_ts"] = time.time()
        current_volume = pos["volume"]
        entry_price = pos.get("entry_price", 0)

    # 🔧 FIX: _CLOSING_MARKETS 등록 후 전체를 try/except로 감싸기
    # — lock 밖 ~ 내부 try 사이, 내부 try 안 모든 예외 시 cleanup 보장
    try:
        # 🔧 FIX: 실잔고 기반 매도 수량 계산 (pos.volume 불일치 방어)
        # 기존: current_volume(pos) * sell_ratio → 잔고 지연/레이스 시 주문 실패
        # 변경: actual balance 우선, 실패 시 pos 기반 폴백
        actual_bal = get_actual_balance(m)
        if actual_bal > 0:
            sell_volume = actual_bal * sell_ratio
            if abs(actual_bal - current_volume) / max(current_volume, 1e-10) > 0.01:
                print(f"[PARTIAL_BAL_DIFF] {m} 실잔고={actual_bal:.6f} vs pos={current_volume:.6f} → 실잔고 기준 사용")
            current_volume = actual_bal  # 이후 remaining 계산도 실잔고 기준
        elif actual_bal == 0:
            msg = f"[REMONITOR] {m} 부분청산: 실잔고=0 → 이미 청산됨"
            print(msg)
            mark_position_closed(m, "partial_sell_actual_zero")
            with _POSITION_LOCK:
                _CLOSING_MARKETS.discard(m)
            return True, msg, 0.0
        else:
            # API 실패(-1) → pos 기반 폴백
            print(f"[PARTIAL_BAL_WARN] {m} 잔고API 실패 → pos.volume({current_volume:.6f}) 폴백")
            sell_volume = current_volume * sell_ratio

        if sell_volume <= 0:
            msg = f"[REMONITOR] {m} 부분청산 실패: sell_volume<=0"
            print(msg)
            # 🔧 FIX: 실패 시 partial_state 롤백
            with _POSITION_LOCK:
                pos2 = OPEN_POSITIONS.get(m)
                if pos2:
                    pos2["partial_state"] = None
                    pos2.pop("partial_ts", None)
                _CLOSING_MARKETS.discard(m)  # 🔧 FIX: 청산 락 해제
            return False, msg, 0.0

        # 🔧 현재가 조회 (최소주문금액 체크용)
        try:
            cur_js = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": m})
            cur_price = cur_js[0].get("trade_price", entry_price) if cur_js else entry_price
        except Exception:
            cur_price = entry_price

        # 🔧 FIX: 최소주문금액(5000원) 사전검증
        MIN_ORDER_KRW = 5000
        sell_krw = sell_volume * cur_price
        remaining_volume = current_volume - sell_volume
        remaining_krw = remaining_volume * cur_price

        # 매도금액이 5000원 미만 또는 잔여금액이 DUST_PREVENT_KRW 미만이면 → 전량청산으로 전환
        # 🔧 FIX: 잔여 기준을 DUST_PREVENT_KRW(6000원)로 상향 → 가격 변동 시 찌꺼기 방지
        # 🔧 FIX: was_full 플래그로 전량청산 시도 여부 추적 (부분체결 시 재시도용)
        was_full = False
        if sell_krw < MIN_ORDER_KRW or remaining_krw < DUST_PREVENT_KRW:
            print(f"[PARTIAL→FULL] {m} 찌꺼기방지 (매도:{sell_krw:.0f}원, 잔여:{remaining_krw:.0f}원 < {DUST_PREVENT_KRW}원) → 전량청산")
            # 🔧 FIX 7차: get_actual_balance() 리턴값 -1(API 실패) 방어
            # 기존: actual_bal > 0 else current_volume → -1 시 current_volume(stale) 사용
            # 변경: -1(API 실패) 시 current_volume 폴백 + 경고 로그
            actual_bal = get_actual_balance(m)
            if actual_bal < 0:
                print(f"[PARTIAL→FULL_WARN] {m} 잔고 조회 실패(API) → OPEN_POSITIONS 수량({current_volume:.6f}) 폴백")
                sell_volume = current_volume
            elif actual_bal > 0:
                sell_volume = actual_bal
            else:
                print(f"[PARTIAL→FULL] {m} 실잔고=0 → 이미 청산됨")
                mark_position_closed(m, "partial_to_full_already_zero")
                return True, "이미 청산됨(잔고=0)", 0.0
            was_full = True  # 🔧 전량청산 시도 표시
            # 🔧 FIX: 전량청산 모드로 전환 → partial_state 해제 (부분체결 시 재시도 가능하게)
            with _POSITION_LOCK:
                pos2 = OPEN_POSITIONS.get(m)
                if pos2:
                    pos2["partial_state"] = None
                    pos2.pop("partial_ts", None)
        res = place_market_sell(m, sell_volume, price_hint=cur_price)
        order_uuid = res.get("uuid") if isinstance(res, dict) else None
        od = get_order_result(order_uuid,
                              timeout_sec=12.0) if order_uuid else None

        executed = 0.0
        if od:
            try:
                executed = float(od.get("executed_volume") or "0")
            except Exception:
                executed = 0.0

        if executed <= 0:
            msg = f"[PARTIAL_SELL_ERR] {m}: executed_volume=0 (요청 {sell_volume:.6f})"
            print(msg)
            tg_send_mid(f"⚠️ <b>부분청산 주문 실패</b> {m}\n"
                        f"• 요청 비율: {sell_ratio*100:.0f}%\n"
                        f"• 요청 수량: {sell_volume:.6f}\n"
                        f"• 체결 수량: 0 (실패)")
            # 🔧 FIX: 체결 실패 시 partial_state 롤백
            with _POSITION_LOCK:
                pos2 = OPEN_POSITIONS.get(m)
                if pos2:
                    pos2["partial_state"] = None
                    pos2.pop("partial_ts", None)
                _CLOSING_MARKETS.discard(m)  # 🔧 FIX: 청산 락 해제
            return False, msg, 0.0

        remaining_volume = max(current_volume - executed, 0.0)

        # 실제 체결가 계산
        exit_price_used = 0.0
        if od:
            trades = od.get("trades") or []
            total_krw = 0.0
            total_vol = 0.0
            for tr in trades:
                try:
                    p = float(tr.get("price", "0"))
                    v = float(tr.get("volume", "0"))
                except Exception:
                    continue
                total_krw += p * v
                total_vol += v
            if total_vol > 0:
                exit_price_used = total_krw / total_vol

        # 🔧 FIX: exit_price_used=0 fallback (리포트/학습 품질 보장)
        exit_price_used = exit_price_used if exit_price_used > 0 else cur_price

        # 🔧 FIX: 청산 슬립 기록 (진입 슬립만 기록하면 과소추정됨)
        # 🔧 FIX: 상한 캡 추가 (0.5%) - 가격이동이 슬립으로 섞이면 TP 과도 상승
        if cur_price > 0 and exit_price_used > 0:
            exit_slip = min(0.0025, abs(exit_price_used / cur_price - 1.0))  # 🔧 FIX: 0.5→0.25% 캡 (close_auto_position과 통일)
            _EXIT_SLIP_HISTORY.append(exit_slip)  # 🔧 FIX: exit 전용

        # 찌꺼기 방지: 체결 후 잔여금액 < MIN_ORDER_KRW 이면 매도 불가 → orphan 등록
        # - pre-check에서 잔여 < DUST_PREVENT_KRW(6000원)은 이미 전량청산 전환됨
        # - 여기는 체결 중 가격변동으로 잔여가 최소주문금액 미만이 된 경우만 처리
        remaining_price_ref = exit_price_used if exit_price_used > 0 else cur_price
        remaining_krw_post = remaining_volume * remaining_price_ref
        if remaining_volume > 1e-10 and remaining_krw_post < MIN_ORDER_KRW and not was_full:
            with _ORPHAN_LOCK:
                _ORPHAN_HANDLED.add(m)
            remaining_volume = 0.0
            print(f"[DUST_PREVENT] {m} 잔여 {remaining_krw_post:.0f}원 < {MIN_ORDER_KRW}원 매도불가 → orphan 등록")

        # 손익 계산
        est_entry_value = entry_price * executed
        est_exit_value = exit_price_used * executed
        pl_value = est_exit_value - est_entry_value
        ret_pct = (exit_price_used / entry_price - 1.0) * 100.0 if entry_price > 0 else 0.0
        fee_total = (est_entry_value + est_exit_value) * FEE_RATE_ONEWAY  # 🔧 FIX: 편도요율×양쪽 = 왕복수수료

        # 🔧 FIX: net 기준으로 통일 (수수료 반영) - 모든 분기에서 사용
        net_ret_pct = ret_pct - (FEE_RATE_ROUNDTRIP * 100.0)

        # 💥 크리티컬 핫픽스: 잔여 0이면 포지션 제거 (좀비 방지)
        # 🔧 FIX: pop 전에 entry_ts, added 백업 (hold_sec 0 버그 방지)
        # 🔧 FIX 7차: 백업 읽기를 락 안에서 수행 (TOCTOU 레이스 방지)
        # 기존: pos_backup 읽기가 락 밖에서 시작 → 다른 스레드 삭제 시 데이터 손상
        # 변경: 모든 백업 + 상태 변경을 단일 락 블록 내에서 수행
        backup_entry_ts = None
        backup_added = False
        backup_pos_snapshot = {}
        if remaining_volume <= 1e-10:
            with _POSITION_LOCK:
                pos_backup = OPEN_POSITIONS.get(m, {})
                backup_entry_ts = pos_backup.get("entry_ts")
                backup_added = pos_backup.get("added", False)
                backup_pos_snapshot = dict(pos_backup) if pos_backup else {}
                # 🔧 FIX: state='closed' 마킹 후 pop (mark_position_closed와 일관성)
                if pos_backup:
                    pos_backup["state"] = "closed"
                    pos_backup["closed_at"] = time.time()
                    pos_backup["closed_reason"] = reason or "partial_sell_full_close"
                OPEN_POSITIONS.pop(m, None)
            print(f"[PARTIAL→FULL_DONE] {m} 전량청산 완료 → 포지션 제거 (net:{net_ret_pct:+.2f}%)")
        else:
            with _POSITION_LOCK:
                pos2 = OPEN_POSITIONS.get(m)
                if pos2:
                    pos2["volume"] = remaining_volume
                    # 🔧 FIX: 전량청산 시도 후 부분체결이면 partial_state=None (재시도 가능)
                    if was_full:
                        pos2["partial_state"] = None  # 전량 시도했는데 잔량 남음 → 재시도 허용
                        pos2.pop("partial_ts", None)
                        print(f"[PARTIAL_RETRY] {m} 전량청산 시도 후 부분체결 → 재시도 허용")
                    else:
                        pos2["partial_state"] = "done"  # 일반 부분청산 → 1회만
                        pos2["partial_ts"] = time.time()  # 🔧 FIX: done 시점 기록 (타임스탑 기준점)
                    pos2["partial_price"] = exit_price_used if exit_price_used > 0 else cur_price
                    pos2["partial_type"] = "profit" if net_ret_pct > 0 else "loss"  # 🔧 net 기준

                    # 🔧 브레이크이븐 (본절): 부분익절 후 손절가를 진입가로 이동
                    # - "먹고 뱉는" 거래 방지
                    # - 🔧 FIX: 비교 기준을 alpha만으로 단순화 (slip은 gate/impact_cap으로 이미 관리)
                    # - ret_net에 slip 없으니 cp에서도 slip 제거 → 일관된 비교
                    # 🔧 본절 = entry_price + 수수료 + 여유 (수수료 미포함 시 net 손실 발생)
                    # 🔧 FIX: 본절 + 트레일 합성 스탑 (큰 수익 꼬리 살리기)
                    if net_ret_pct > 0 and not pos2.get("breakeven_set"):  # 🔧 FIX: gross→net (수수료 차감 후 실제 수익 기준)
                        old_stop = pos2.get("stop", 0)
                        be_price = entry_price * (1 + FEE_RATE_ROUNDTRIP + 0.0005)  # 수수료(0.1%) + 여유(0.05%) = +0.15%
                        # 🔧 FIX: 현재가 기준 트레일 최소폭과 합성 (상승 중엔 트레일 유지)
                        _trail_min = get_trail_distance_min()
                        _trail_stop = cur_price * (1.0 - _trail_min) if cur_price > 0 else 0
                        # 본절 보호 + 상승 중 트레일 중 높은 값
                        combined_stop = max(be_price, _trail_stop)
                        # 🔧 FIX C5: old_stop과 비교하여 손절가 하향 방지 (기존 스탑이 더 높으면 유지)
                        combined_stop = max(combined_stop, old_stop)
                        pos2["stop"] = combined_stop
                        pos2["breakeven_set"] = True
                        print(f"[BREAKEVEN] {m} 부분익절 후 합성스탑: 손절 {old_stop:.0f} → {combined_stop:.0f} (본절{be_price:.0f} 트레일{_trail_stop:.0f})")

        msg = (f"[REMONITOR] {m} 부분 청산 실행 {sell_ratio*100:.0f}% "
               f"(체결 {executed:.6f}) 잔여 {remaining_volume:.6f}")
        print(msg)

        # 🔧 FIX: net 기준으로 판정 (수수료 반영)
        result_emoji = "🟢" if net_ret_pct > 0 else "🔴"
        # 전략 정보 추출
        _partial_sig_tag = backup_pos_snapshot.get("signal_tag") if backup_pos_snapshot else None
        if not _partial_sig_tag:
            with _POSITION_LOCK:
                _pos_ref = OPEN_POSITIONS.get(m)
                if _pos_ref:
                    _partial_sig_tag = _pos_ref.get("signal_tag", "?")
        _partial_sig_tag = _partial_sig_tag or "?"
        _partial_group = "GT" if "GT" in _partial_sig_tag else ("C" if "반전_15m" in _partial_sig_tag else "?")
        _partial_desc_map = {
            "GT": "강모멘텀 추격 (5분RSI≥73, 180초 시간청산)",
            "C": "초입반전 포착 (15분 음→양, 빠른익절)",
        }
        _partial_strat_desc = _partial_desc_map.get(_partial_group, "")
        tg_send(
            f"====================================\n"
            f"{result_emoji} <b>부분 청산</b> {m}\n"
            f"====================================\n"
            f"💰 순손익: {pl_value - fee_total:+,.0f}원 (gross:{ret_pct:+.2f}% / net:{net_ret_pct:+.2f}%)\n"
            f"📊 매매차익: {pl_value:+,.0f}원 → 수수료 {fee_total:,.0f}원 차감 → 실현손익 {pl_value - fee_total:+,.0f}원\n\n"
            f"• 전략: {_partial_sig_tag} [{_partial_group}]{' — ' + _partial_strat_desc if _partial_strat_desc else ''}\n"
            f"• 사유: {reason or '부분청산'}\n"
            f"• 매수평단: {fmt6(entry_price)}원\n"
            f"• 실매도가: {fmt6(exit_price_used)}원\n"
            f"• 체결수량: {executed:.6f}\n"
            f"• 매수금액: {est_entry_value:,.0f}원\n"
            f"• 청산금액: {est_exit_value:,.0f}원\n"
            f"• 수수료: {fee_total:,.0f}원 (매수 {est_entry_value * FEE_RATE_ONEWAY:,.0f} + 매도 {est_exit_value * FEE_RATE_ONEWAY:,.0f})\n"
            f"• 잔여수량: {remaining_volume:.6f}\n"
            f"===================================="
        )

        # 🔧 FIX: 전량청산 시 record_trade(net) + update_trade_result(net) 호출
        # - 기존: update_trade_result(gross)만 호출 → TRADE_HISTORY/streak 누락
        # - 변경: net 기준으로 통일, record_trade도 호출
        if remaining_volume <= 1e-10:
            # 🔧 FIX: 백업된 entry_ts, added 사용 (pop 후라 OPEN_POSITIONS에 없음)
            try:
                if backup_entry_ts is not None:
                    hold_sec = time.time() - backup_entry_ts
                else:
                    hold_sec = 0
                # 🔧 FIX: record_trade(net) 호출 - TRADE_HISTORY/streak 업데이트
                record_trade(m, net_ret_pct / 100.0, backup_pos_snapshot.get("signal_type", "기본"))
                # 🔧 FIX: AUTO_LEARN_ENABLED 무관하게 항상 호출 (배치 리포트 카운터)
                update_trade_result(m, exit_price_used, net_ret_pct / 100.0, hold_sec,
                                        added=backup_added, exit_reason=reason or "부분청산",
                                        entry_ts=backup_entry_ts,
                                        pos_snapshot=backup_pos_snapshot)  # 🔧 FIX: 튜닝 메트릭 전달
            except Exception as _e:
                print(f"[PARTIAL_TRADE_LOG_ERR] {_e}")

        # 🔧 FIX: 청산 락 해제 (성공)
        with _POSITION_LOCK:
            _CLOSING_MARKETS.discard(m)
        return True, msg, executed

    except Exception as e:
        msg = f"[PARTIAL_SELL_ERR] {m}: {e}"
        print(msg)
        # 🔧 FIX: 부분청산 실패 시 포지션 유지 (orphan 방지)
        # - 최소주문금액 미만이면 부분매도 불가 → 전량청산 로직에 맡김
        # - 포지션을 pop하면 잔고는 남아있는데 OPEN_POSITIONS에 없어서 orphan 됨!
        if "최소주문금액" in str(e) or "5000" in str(e):
            tg_send(f"⚠️ {m} 부분청산 스킵 (최소주문금액 미만)\n• 포지션 유지, 전량청산 대기")
        # 🔧 FIX: 예외 발생 시 partial_state 롤백
        with _POSITION_LOCK:
            pos2 = OPEN_POSITIONS.get(m)
            if pos2:
                pos2["partial_state"] = None
                pos2.pop("partial_ts", None)
            _CLOSING_MARKETS.discard(m)  # 🔧 FIX: 청산 락 해제
        return False, msg, 0.0


def remonitor_until_close(m, entry_price, pre, tight_mode=False):
    """
    끝알람 이후 자동청산 신호가 나올 때까지 반복 모니터링
    🔧 FIX: 장기 보유 타임아웃 추가 (부분청산 후 정체 방지)
    """
    # 🐱 DCB 포지션은 자체 모니터가 관리 → 재모니터링 스킵
    with _POSITION_LOCK:
        if OPEN_POSITIONS.get(m, {}).get("strategy") == "dcb":
            print(f"[REMONITOR] {m} DCB 포지션 → 자체 모니터 관리, 스킵")
            return False

    # 🔧 FIX: 진입 전 가드 - 이미 청산 중/완료된 포지션은 즉시 리턴
    with _POSITION_LOCK:
        if m in _CLOSING_MARKETS:
            print(f"[REMONITOR] {m} 이미 청산 진행 중(_CLOSING_MARKETS) → 스킵")
            return False
        pos_check = OPEN_POSITIONS.get(m)
        if pos_check and pos_check.get("state") == "closed":
            print(f"[REMONITOR] {m} 이미 청산 완료(state=closed) → 스킵")
            return False

    # 🔧 FIX: 잔고 0이면 즉시 리턴 (청산 완료 확인)
    # 🔧 FIX: 매수 직후 300초 내에는 잔고=0이어도 API 지연 가능 → 포지션 유지
    bal_check = get_balance_with_locked(m)
    with _RECENT_BUY_LOCK:
        buy_age = time.time() - _RECENT_BUY_TS.get(m, 0)
    if bal_check >= 0 and bal_check <= 1e-12:
        if buy_age < 120:  # 🔧 FIX: 300→120초 (5분은 너무 길어 고스트 포지션 위험)
            print(f"[REMONITOR] {m} 진입 전 잔고=0이지만 매수 {buy_age:.0f}초 전 → API 지연 가능, 계속 진행")
        else:
            print(f"[REMONITOR] {m} 진입 전 잔고=0 확인 → 스킵")
            mark_position_closed(m, "remonitor entry - balance zero")
            return False

    CYCLE_SEC = 60  # 🔧 승률개선: 300→60초 (빠른 하락 대응, 5분 방치→1분 반응)
    MAX_REMONITOR_CYCLES = 60  # 🔧 FIX: 무한루프 방지 (최대 60회 × 60초 = 60분)
    cycle = 0

    # 🔧 FIX: 루프 밖으로 이동 (매 반복 재생성 방지)
    _ALREADY_CLOSED_VERDICTS = {
        "ATR손절", "TRAIL_STOP", "부분청산→전량청산",
        "스캘프_TP",                                # 스캘프 익절
        "시간만료_손실컷", "시간만료_본절컷",       # 시간만료 손실/본절 즉시청산
        "연장_TRAIL_STOP", "연장_RATCHET_STOP",    # 연장 중 트레일/래칫 컷
        "연장_ATR_STOP",                            # 연장 ATR 손절
        "연장중_전량청산",                          # 연장 중 외부 청산
        "유효하지 않은 entry_price",                # 🔧 FIX: 잘못된 진입가 → 포지션 제거 후 verdict
        "하드스톱",                                  # 🔧 BUG FIX: SL×1.5 초과 즉시컷 (중복청산 방지)
        "본절SL",                                    # 🔧 BUG FIX: 래칫 본절 손절 (중복청산 방지)
        "수급감량_DUST",                            # 🔧 BUG FIX: 수급확인 감량 후 dust 정리 (중복청산 방지)
        "잔여청산",                                  # 🔧 BUG FIX: 감량 후 잔여 청산 (중복청산 방지)
        "관망만료",                                  # 🔧 BUG FIX: 수급확인 관망 후 청산 (중복청산 방지)
        "V7_TIMEOUT_LOSS",                          # 🔧 BUG FIX: v7 시간대 타임아웃 손실청산 (중복청산 방지)
        "V7_SURGE_PEAK_EXIT",                       # 🔧 BUG FIX: v7 폭발 피크아웃 익절 (중복청산 방지)
        "V7_SURGE_FAIL",                            # 🔧 BUG FIX: v7 폭발 15분 미수익 청산 (중복청산 방지)
        "스캘프_TP_DUST",                            # 🔧 BUG FIX: 스캘프 TP dust 전량청산 (중복청산 방지)
        "러너_TP_DUST",                              # 🔧 BUG FIX: 러너 TP dust 전량청산 (중복청산 방지)
    }

    while True:
        cycle += 1
        # 🔧 FIX: 무한루프 방지 — 최대 사이클 초과 시 강제 청산
        if cycle > MAX_REMONITOR_CYCLES:
            print(f"[REMONITOR] {m} 최대 {MAX_REMONITOR_CYCLES}회 초과 → 강제 청산")
            try:
                close_auto_position(m, f"remonitor 최대사이클({MAX_REMONITOR_CYCLES}회) 초과")
            except Exception as _rmc_err:
                print(f"[REMONITOR_FORCE_CLOSE_ERR] {m}: {_rmc_err}")
            return True
        print(f"[REMONITOR] {m} {cycle}회차 재모니터링 시작")

        # 🔧 유령 포지션 탈출: 실잔고 확인
        # 🔧 FIX (B): 락 안에서 복사본 생성 → 락 해제 후 레이스 방지
        with _POSITION_LOCK:
            pos_raw = OPEN_POSITIONS.get(m)
            pos = copy.deepcopy(pos_raw) if pos_raw else None  # 🔧 FIX: 깊은 복사 (nested dict 레이스 방지)
        # 🔧 FIX: 피라미딩(추가매수) 후 entry_price 갱신 — 함수 인자 값은 최초 진입가
        if pos and pos.get("entry_price"):
            entry_price = pos["entry_price"]
        if not pos:
            print(f"[REMONITOR] {m} OPEN_POSITIONS에 없음 → 루프 종료")
            # 🔧 FIX: 포지션 없음 알람 추가
            tg_send(f"⚠️ {m} 포지션 정리됨 (OPEN_POSITIONS에서 제거됨)")
            return False

        # 실제 거래소 잔고 확인 (🔧 FIX: balance + locked 모두 체크)
        actual = get_balance_with_locked(m)
        # 🔧 FIX: -1 = 조회 실패 → 포지션 삭제하지 않고 다음 사이클 대기
        if actual < 0:
            print(f"[REMONITOR] {m} 잔고 조회 실패 → 포지션 유지, 다음 사이클 대기")
            time.sleep(5)
            continue
        if actual <= 1e-12:
            # 🔧 FIX: 매수 직후 300초 내에는 잔고=0이어도 API 지연 가능 → 다음 사이클 대기
            with _RECENT_BUY_LOCK:
                buy_age_loop = time.time() - _RECENT_BUY_TS.get(m, 0)
            if buy_age_loop < 300:
                print(f"[REMONITOR] {m} 잔고=0이지만 매수 {buy_age_loop:.0f}초 전 → API 지연 가능, 다음 사이클 대기")
                time.sleep(5)
                continue
            print(f"[REMONITOR] {m} 실잔고+locked=0 → 유령 포지션 정리 후 루프 종료")
            # 🔧 FIX: 청산 알람 추가 (외부 정리 또는 체결 누락 감지)
            entry_price_for_msg = pos.get("entry_price", 0)
            tg_send(f"⚠️ {m} 포지션 정리 (잔고+locked=0 확인)\n• 매수가: {fmt6(entry_price_for_msg)}원\n• 외부 청산 또는 이미 정리됨")
            # 🔧 FIX: mark_position_closed로 state 마킹 후 정리
            mark_position_closed(m, "remonitor_zero_balance")
            return False

        # 🔧 부분청산 후 추가 하락 체크
        if pos and pos.get("partial_state") == "done":
            partial_price = pos.get("partial_price", 0)
            partial_type = pos.get("partial_type", "loss")
            partial_ts = pos.get("partial_ts", 0)  # 🔧 FIX: 타임스탑용 타임스탬프

            # 현재가 조회
            # 🔧 FIX 7차: cur_price=0 방어 (네트워크 장애 시 수익률 -100% 오판 → 잘못된 강제 청산 방지)
            # 기존: except → cur_price=0 (로그 없이) → 후속 로직에서 수익률 -100%로 계산
            # 변경: 실패 시 entry_price 폴백 + 로그 출력
            try:
                cur_js = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": m})
                cur_price = cur_js[0].get("trade_price", 0) if cur_js and len(cur_js) > 0 else 0  # 🔧 FIX: 빈 배열 방어
            except Exception as _cp_err:
                print(f"[PARTIAL_POSTCHECK] {m} 현재가 조회 실패: {_cp_err}")
                cur_price = 0

            # 🔧 FIX 7차: cur_price=0이면 entry_price로 폴백 (0원 기준 청산 판단 방지)
            if cur_price <= 0:
                cur_price = entry_price if entry_price > 0 else partial_price

            if cur_price > 0 and partial_price > 0:
                # 🔧 FIX: 드롭 기준을 변동성(trail_distance_min) 연동
                # - 고정값이면 변동성 큰 코인에서 정상 흔들림에도 잔량 정리됨
                trail_min = get_trail_distance_min()
                profit_drop_thr = max(PARTIAL_EXIT_PROFIT_DROP, trail_min * 1.2)
                loss_drop_thr = max(PARTIAL_EXIT_LOSS_DROP, trail_min * 0.8)

                if partial_type == "profit":
                    # 익절 부분청산 후: 추가 하락 OR 진입가 "확실히" 이하 → 청산
                    # 🔧 FIX: Division by Zero 방어
                    drop_from_partial = (partial_price - cur_price) / partial_price if partial_price > 0 else 0
                    # 🔧 FIX: breakeven box 적용 - entry 살짝 찍는 정상 변동성 허용
                    # - 기존: entry 찍으면 바로 잔량 청산 → 크게 가는 거래 꼬리 잘림
                    # - 변경: entry*(1-box) 이하일 때만 청산 (box = trail_min * 0.5)
                    breakeven_box = trail_min * 0.5
                    if entry_price > 0 and (cur_price <= entry_price * (1 - breakeven_box) or drop_from_partial >= profit_drop_thr):
                        reason = f"부분익절 후 추가하락 -{drop_from_partial*100:.2f}% (thr:{profit_drop_thr*100:.2f}%) 잔량청산"
                        print(f"[REMONITOR] {m} {reason} → 나머지 청산")
                        close_auto_position(m, reason)
                        return True

                    # 🔧 부분익절 후 타임스탑: 3분간 횡보 시 잔량 정리
                    # - 부분익절 후 추가 상승 없이 시간만 경과 → 기회비용 낭비 방지
                    partial_elapsed = time.time() - partial_ts
                    if partial_elapsed >= 900 and entry_price > 0:  # before1에 없음, 180→900 대폭 완화 (15분)
                        ret_from_entry = (cur_price / entry_price - 1.0)
                        # 진입가 대비 +0.3% 이하면 더 이상 기대 어려움 → 잔량 정리
                        if ret_from_entry <= 0.003:
                            reason = (f"부분익절 후 타임스탑 {partial_elapsed:.0f}초 경과, "
                                      f"수익률 {ret_from_entry*100:.2f}% ≤ 0.3% → 잔량청산")
                            print(f"[REMONITOR][TIMESTOP] {m} {reason}")
                            close_auto_position(m, reason)
                            return True
                else:
                    # 손절 부분청산 후: 추가 하락 → 청산
                    # 🔧 FIX: Division by Zero 방어
                    drop_from_partial = (partial_price - cur_price) / partial_price if partial_price > 0 else 0
                    if drop_from_partial >= loss_drop_thr:
                        reason = f"부분손절 후 추가하락 -{drop_from_partial*100:.2f}% (thr:{loss_drop_thr*100:.2f}%) 잔량청산"
                        print(f"[REMONITOR] {m} {reason} → 나머지 청산")
                        close_auto_position(m, reason)
                        return True

        verdict, action, rationale, ret_pct, last_price, maxrun, maxdd = \
            monitor_position(
                m, entry_price, pre,
                tight_mode=tight_mode,
                horizon=CYCLE_SEC,
                reentry=True
            )

        should_close = False
        reason = verdict or action or "모니터링 종료"

        # 🔧 FIX C2: monitor_position 내부에서 이미 청산 완료된 경우 중복 청산 방지
        if verdict in _ALREADY_CLOSED_VERDICTS or (verdict and "스크래치" in verdict):
            # 내부에서 close_auto_position 이미 호출됨 → remonitor 종료
            print(f"[REMONITOR] {m} 내부청산 완료 ({verdict}) → remonitor 종료")
            return True

        # 🔧 FIX: 분기 순서 수정 - 위험신호/청산권고를 TP보다 먼저 처리
        # (기존: ret_pct 블록이 먼저 실행되어 청산권고/부분청산권고 도달 불가)

        # 🔧 FIX: 컨텍스트 청산 권고가 아니면 연속 카운트 리셋 (간헐 경고 누적 방지)
        # - 기존: 리셋 없이 누적 → "청산 권고 1번씩 뜨다 말다"도 결국 3회 도달
        # - 개선: N회 "연속"을 제대로 판정하려면 비청산권고 시 리셋 필수
        if not (verdict and verdict.startswith("청산 권고(")):
            with _POSITION_LOCK:
                pos_reset = OPEN_POSITIONS.get(m, {})
                if pos_reset and pos_reset.get("ctx_close_count", 0) > 0:
                    pos_reset["ctx_close_count"] = 0
                    print(f"[REMONITOR] {m} 컨텍스트 청산 권고 해제 → ctx_close_count 리셋")

        # 1) 급락 / 손절 권고 → 즉시 청산
        if verdict and ("급락" in verdict or "손절" in verdict):
            should_close = True

        # 2) 전량 청산 권고 (action 기반) → 즉시 청산
        elif action and "전량 청산 권고" in action:
            should_close = True

        # 3) 컨텍스트 청산 권고 → N회 연속 시 청산 (나쁜 포지션 오래 끌림 방지)
        elif verdict and verdict.startswith("청산 권고("):
            # 🔧 FIX: 컨텍스트 청산 권고가 계속 반복되면 손실을 시간으로 키움
            # - 3회 연속이면 전량 청산으로 승격
            # 🔧 FIX(0-2): entry_mode별 컨텍스트 청산 재활성화
            # probe/half은 약신호이므로 나쁜 흐름에서 빠르게 청산
            # 🔧 FIX: 단일 락 블록으로 통합 (TOCTOU 방지 — 읽기~증가 사이 변경 가능성 제거)
            with _POSITION_LOCK:
                pos_ctx = OPEN_POSITIONS.get(m, {})
                _ctx_em = pos_ctx.get("entry_mode", "confirm")
                if _ctx_em == "probe":
                    CONTEXT_CLOSE_THRESHOLD = 2   # probe: 2회 연속이면 청산
                elif _ctx_em == "half":
                    CONTEXT_CLOSE_THRESHOLD = 3   # half: 3회 연속이면 청산
                else:
                    CONTEXT_CLOSE_THRESHOLD = 5   # confirm: 5회 연속이면 청산
                ctx_count = pos_ctx.get("ctx_close_count", 0) + 1
                if pos_ctx:
                    pos_ctx["ctx_close_count"] = ctx_count

            if ctx_count >= CONTEXT_CLOSE_THRESHOLD:
                print(f"[REMONITOR] {m} 컨텍스트 청산 권고 {ctx_count}회 연속 → 전량 청산")
                should_close = True
                reason = f"컨텍스트 청산 권고 {ctx_count}회 연속"
            else:
                print(f"[REMONITOR] {m} 컨텍스트 청산 권고 {ctx_count}/{CONTEXT_CLOSE_THRESHOLD} → 계속 모니터링")
                continue

        # 4) 부분 청산(50%) 권고 → 실제 50% 매도 후 계속 재모니터링
        elif action == "부분 청산(50%) 권고":
            ok, msg, executed = safe_partial_sell(m,
                                                  sell_ratio=0.5,
                                                  reason="모니터 권고 부분청산")
            if ok and executed > 0:
                print(msg)
            continue

        # 5) 🔧 v4: 트레일링 기반 청산 — EXIT_PARAMS에서 TP 목표 제거
        #    v4 전략은 트레일링 스톱이 기본이므로, 고정 TP 대신 트레일링에 위임
        #    단, 극단적 수익(3%+)에서만 전량 청산으로 수익 확정
        elif ret_pct is not None:
            # 현재 포지션의 signal_tag 및 v4 청산 파라미터 가져오기
            with _POSITION_LOCK:
                pos_for_tag = OPEN_POSITIONS.get(m) or {}
            tag = pos_for_tag.get("signal_tag", "기본")
            _v4_ep = v4_get_exit_params(tag)

            _fee_pct = FEE_RATE_ROUNDTRIP * 100.0
            _exit_slip_pct = get_expected_exit_slip_pct()
            _exit_slip_capped = min(_exit_slip_pct, 0.20)
            net_ret_for_tp = ret_pct - _fee_pct - _exit_slip_capped

            # 극단적 수익 (3%+) → 전량 청산
            if net_ret_for_tp >= 3.0:
                should_close = True
                reason = f"{tag} 극단수익 net{net_ret_for_tp:.2f}% ≥ 3.0% 전량청산"
            # A그룹(추세형) 1.5%+ → 50% 부분익절
            elif net_ret_for_tp >= 1.5 and _v4_ep.get("strategy") == "TRAIL":
                ok, msg, executed = safe_partial_sell(
                    m, sell_ratio=0.5, reason=f"{tag} net{net_ret_for_tp:.2f}% ≥ 1.5% 50% 부분익절")
                if ok and executed > 0:
                    print(msg)
                continue

        # 6) 유지 권고 → 계속 재모니터링
        elif action == "유지 권고":
            continue

        # 7) 시간/연장 만료 → 다시 한 번 사이클
        # 🔧 FIX: "연장만료(모니터링 종료)" 누락으로 should_close=False 무한루프 방지
        elif verdict in ("연장만료(모니터링 종료)", "데이터 수신 실패"):  # 🔧 FIX: "시간 만료(모니터링 종료)" 제거 (실제 설정되지 않는 verdict)
            continue

        if not should_close:
            # 🔧 FIX: 인식 불가 verdict → tight loop 방지 (fallback sleep)
            print(f"[REMONITOR] {m} 미인식 verdict={verdict}, action={action} → 다음 사이클")
            time.sleep(5)
            continue

        if should_close:
            print(f"[REMONITOR] {m} 자동청산 조건 충족 → 청산 ({reason})")
            close_auto_position(m, reason)
            # 🔧 FIX: 부분체결 시 잔여 포지션이 남아있으면 remonitor 계속 (방치 방지)
            with _POSITION_LOCK:
                _still_open = m in OPEN_POSITIONS and OPEN_POSITIONS[m].get("state") == "open"
            if _still_open:
                print(f"[REMONITOR] {m} 부분체결 잔여 포지션 감지 → 재모니터링 계속")
                continue
            return True


# =========================
# 얼럿 정책
# =========================
# SILENT_MIDDLE_ALERTS — config.py에서 정의됨


def tg_send_mid(t):
    if not SILENT_MIDDLE_ALERTS:
        return tg_send(t)
    else:
        print("[SILENT]", t)
        return True

# 틱/체결 기반
# MIN_TURNOVER, TICKS_BUY_RATIO — config.py에서 정의됨

# 위험 관리

# ★★★ 1단계 게이트 임계치 — config.py에서 정의됨 (GATE_*, SPREAD_*)
# ★ auto-learn에서 런타임 조정 가능 (global 선언으로 덮어쓰기)

# ========================================
# 🔧 Ignition 내부 임계치
# ========================================
# 🔧 Ignition 내부 임계치 — config.py에서 정의됨 (IGN_*)

# ========================================
# 🚀 Pre-break Probe 설정 (선행 진입)
# ========================================
# 🚀 Pre-break — config.py에서 정의됨 (PREBREAK_*)

# 손절/모니터링 — config.py에서 정의됨 (STOP_LOSS_PCT, RECHECK_SEC)

# (IGN_BREAK_LOOKBACK, IGN_MIN_BODY, IGN_MIN_BUY, ABS_SURGE_KRW, RELAXED_X 삭제 — 미사용 상수)

# 쿨다운 히스테리시스 — config.py에서 정의됨 (REARM_*)

# 포스트체크(허수 2차) - 상단에서 정의됨, 이중선언 제거
# POSTCHECK_ENABLED: 상단(L148)에서 정의됨
# 포스트체크 — config.py에서 정의됨 (POSTCHECK_*)

# 동적 손절(ATR) - 단일 스탑 (틱스탑 제거)
# 🔧 구조개선: SL 넓히기 — 0.4% SL은 1분봉 노이즈(0.3~0.5%)에 걸림
#   → 정상 눌림에서 손절 → 반등 패턴이 승률 최대 훼손 원인
#   → 0.6% 최소로 올려 노이즈 손절 -50%, R:R은 TP 연동으로 보전
# 동적 손절 ATR — config.py에서 정의됨 (ATR_PERIOD, ATR_MULT)
# 🔧 FIX: DYN_SL_MIN/MAX → 상단(line 59-60)에서 단일 선언 (중복 제거, 한쪽만 바꾸는 사고 방지)

# =========================
# 🎯 틱 기반 트레일링 스탑 (비활성화 - ATR 단일 스탑 사용)
# =========================
                                 # (틱기반 트레일링 관련 상수 제거됨 — ATR 단일 스탑 사용)
# 🔧 HARD_STOP_DD는 상단 프로파일에서만 설정 (이중정의 제거)

# 메가 브레이크아웃 — config.py에서 정의됨 (ULTRA_RELAX_ON_MEGA, MEGA_*)

# =========================

def calc_orderbook_imbalance(ob):
    """
    1~3호가 가중 평균 임밸런스 계산
    - 1호가 가중치 3, 2호가 2, 3호가 1
    - 반환값: -1.0 ~ +1.0 (양수=매수우세)
    """
    try:
        units = ob["raw"]["orderbook_units"][:3]
        # 🔧 FIX: float 캐스팅 (업비트 응답이 string으로 올 경우 곱셈 TypeError 방지)
        bid_weighted = sum(float(u.get("bid_size", 0) or 0) * float(u.get("bid_price", 0) or 0) * (3-i) for i, u in enumerate(units))
        ask_weighted = sum(float(u.get("ask_size", 0) or 0) * float(u.get("ask_price", 0) or 0) * (3-i) for i, u in enumerate(units))
        total = bid_weighted + ask_weighted
        if total <= 0:
            return 0.0
        imbalance = (bid_weighted - ask_weighted) / total
        return max(-1.0, min(1.0, imbalance))
    except Exception:
        return 0.0

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


# =========================
# 🧠 자동 가중치 학습 시스템
# =========================
TRADE_LOG_PATH = os.path.join(os.getcwd(), "trade_features.csv")
WEIGHTS_PATH = os.path.join(os.getcwd(), "learned_weights.json")
# 자동학습 — config.py에서 정의됨 (AUTO_LEARN_*)
_trade_log_lock = threading.Lock()
_trade_count_since_learn = 0  # 마지막 학습 이후 거래 수
_path_report_count = 0  # 🔍 경로 리포트용 카운터
_reported_trades = set()  # 🔧 중복 카운트 방지용 (market, entry_ts) 세트 — 유일 선언
# PATH_REPORT_INTERVAL — config.py에서 정의됨
# 🔧 _lose_streak, _win_streak는 상단(라인 203-204)에서 선언됨

# =========================
# 📊 배치 거래 리포트 (30건마다 텔레그램 종합 발송)
# =========================
# BATCH_REPORT_INTERVAL — config.py에서 정의됨
BATCH_LOG_PATH = os.path.join(os.getcwd(), "batch_reports.csv")  # 배치 요약 CSV

def _restore_batch_count() -> int:
    """봇 재시작 시 마지막 배치 리포트 이후 거래 수 복원"""
    try:
        if not os.path.exists(BATCH_LOG_PATH) or not os.path.exists(TRADE_LOG_PATH):
            # 배치 리포트가 한 번도 없었으면 전체 청산 건수 카운트
            if os.path.exists(TRADE_LOG_PATH):
                import csv as _csv
                with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
                    reader = _csv.DictReader(f)
                    count = sum(1 for row in reader if row.get("result") in ("win", "lose"))
                restored = count % BATCH_REPORT_INTERVAL
                print(f"[BATCH_REPORT] 카운터 복원: {restored}/{BATCH_REPORT_INTERVAL} (배치기록 없음, 전체 {count}건)")
                return restored
            return 0

        # 마지막 배치 리포트 시각 가져오기
        last_batch_ts = ""
        with open(BATCH_LOG_PATH, "r", encoding="utf-8") as f:
            import csv as _csv
            for row in _csv.DictReader(f):
                last_batch_ts = row.get("ts", "")

        if not last_batch_ts:
            return 0

        # 마지막 배치 리포트 이후 청산된 거래 수 카운트
        import csv as _csv
        count = 0
        with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                if row.get("result") in ("win", "lose") and row.get("ts", "") > last_batch_ts:
                    count += 1

        print(f"[BATCH_REPORT] 카운터 복원: {count}/{BATCH_REPORT_INTERVAL} (마지막 배치: {last_batch_ts})")
        return count
    except Exception as e:
        print(f"[BATCH_REPORT] 카운터 복원 실패: {e}")
        return 0

_batch_report_count = _restore_batch_count()

FEATURE_FIELDS = [
    "ts", "market", "entry_price", "exit_price",
    "buy_ratio", "spread", "turn", "imbalance", "volume_surge",
    "fresh", "score", "entry_mode",
    "signal_tag", "filter_type",  # 🔍 경로 분석용 (signal_tag 하나로 통일)
    "consecutive_buys", "avg_krw_per_tick", "flow_acceleration",  # 🔥 새 지표
    # 🔥 GATE 핵심 + 초단기 미세필터 지표
    "overheat", "fresh_age", "cv", "pstd", "best_ask_krw",
    # 🔍 섀도우 모드용 (거래는 그대로, 나중에 분석용)
    "shadow_flags", "would_cut", "is_prebreak",
    # 🔍 리포트 상세: 추매여부 + 청산사유
    "added", "exit_reason",
    # 🔧 MFE/MAE (최고점/최저점 수익률) - 익절/손절 튜닝용
    "mfe_pct", "mae_pct",
    "pnl_pct", "result", "hold_sec",
    # 🔧 데이터수집: 손절폭/트레일 간격 튜닝용 신규 필드
    "entry_atr_pct", "entry_pstd", "entry_spread", "entry_consec",
    "mfe_sec", "trail_dist", "trail_stop_pct", "peak_drop",
    # 🔧 v7: 진입 컨텍스트 스냅샷 필드
    "entry_rsi", "entry_vr5", "entry_buy_ratio", "entry_accel",
]

def log_trade_features(entry_data: dict, exit_data: dict = None):
    """
    거래 피처 로깅 (진입 시 호출, 청산 시 업데이트)
    """
    with _trade_log_lock:
        new_file = not os.path.exists(TRADE_LOG_PATH)
        with open(TRADE_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FEATURE_FIELDS)
            if new_file:
                w.writeheader()
            row = {k: entry_data.get(k, "") for k in FEATURE_FIELDS}
            if exit_data:
                row.update(exit_data)
            w.writerow(row)


def update_trade_result(market: str, exit_price: float, pnl_pct: float, hold_sec: float,
                        added: bool = False, exit_reason: str = "",
                        mfe_pct: float = 0.0, mae_pct: float = 0.0,
                        entry_ts: float = None, pos_snapshot: dict = None):
    """
    청산 시 결과 업데이트 + 건수 기반 학습 트리거 + 경로 리포트
    - added: 추매 여부 (probe → confirm 승격 시 True)
    - exit_reason: 청산 사유 (예: ATR손절, 트레일링, 얇은수익, 시간종료 등)
    - mfe_pct: 최고 수익률 (Maximum Favorable Excursion)
    - mae_pct: 최저 수익률 (Maximum Adverse Excursion)
    - entry_ts: 진입 시각 (중복 방지용 - 동일 거래 식별)
    """
    global _trade_count_since_learn, _lose_streak, _win_streak, _path_report_count, _batch_report_count

    # last_trade_was_loss는 모듈 레벨에서 정의됨 (L466) — 직접 참조
    global last_trade_was_loss

    # 🔧 FIX: (market, entry_ts) 기반 중복 방지 - 동일 거래 2회 기록 방지
    # entry_ts가 있으면 거래 ID로 사용, 없으면 market+현재시각 기준 (호환성)
    # 🔧 FIX: _reported_trades 접근을 _trade_log_lock으로 보호 (멀티스레드 중복 방지)
    with _trade_log_lock:
        if entry_ts is not None:
            # 🔧 FIX: ms 단위로 변경 (1초 내 재진입 시 중복 판정 방지)
            trade_id = (market, int(entry_ts * 1000))  # ms 단위
            if trade_id in _reported_trades:
                print(f"[UPDATE_TRADE] {market} 동일 거래 중복 스킵 (entry_ts={entry_ts:.0f})")
                return
            _reported_trades.add(trade_id)
            # 오래된 항목 정리 (1시간 이상 지난 거래)
            # 🔧 FIX: discard()는 단일 원소만 제거 → difference_update() 사용
            now = time.time()
            _old_trades = {t for t in _reported_trades if now - t[1] / 1000 > 3600}  # ms→sec 변환
            _reported_trades.difference_update(_old_trades)
        else:
            # 🔧 호환성: entry_ts 없으면 기존 방식 (market + 30초)
            # 🔧 FIX: ms 단위로 통일 (cleanup 로직과 단위 일치)
            now_ts = time.time()
            now_ms = int(now_ts * 1000)
            recent = [t for t in _reported_trades if t[0] == market and now_ts - t[1] / 1000 < 30]
            if recent:
                print(f"[UPDATE_TRADE] {market} 중복 호출 스킵 (30초 내)")
                return
            _reported_trades.add((market, now_ms))

    print(f"[UPDATE_TRADE] {market} 청산 기록 시작 (pnl: {pnl_pct:.2%})")

    is_win = pnl_pct > 0
    csv_exists = os.path.exists(TRADE_LOG_PATH)

    # 🔧 손실 후 동일 종목 쿨다운 2배 적용용 플래그 설정
    with _TRADE_LOSS_LOCK:
        last_trade_was_loss[market] = not is_win

    if not csv_exists:
        print(f"[UPDATE_TRADE] {TRADE_LOG_PATH} 파일 없음 (CSV 업데이트 스킵, 리포트는 계속)")
    else:
        with _trade_log_lock:
            try:
                rows = []
                with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)

                # 마지막 해당 마켓 찾아서 업데이트
                for i in range(len(rows) - 1, -1, -1):
                    if rows[i]["market"] == market and not rows[i].get("exit_price"):
                        rows[i]["exit_price"] = str(exit_price)
                        rows[i]["pnl_pct"] = f"{pnl_pct:.4f}"
                        rows[i]["result"] = "win" if is_win else "lose"
                        rows[i]["hold_sec"] = str(int(hold_sec))
                        # 🔍 리포트 상세: 추매여부 + 청산사유
                        rows[i]["added"] = "1" if added else "0"
                        rows[i]["exit_reason"] = exit_reason
                        # 🔧 MFE/MAE 기록 (익절/손절 튜닝용)
                        rows[i]["mfe_pct"] = f"{mfe_pct:.4f}"
                        rows[i]["mae_pct"] = f"{mae_pct:.4f}"
                        # 🔧 데이터수집: 포지션에서 튜닝 메트릭 가져와서 CSV에 기록
                        # 🔧 FIX: pos_snapshot 우선 사용 (close 후 OPEN_POSITIONS에서 제거됨)
                        # 🔧 FIX: pos_snapshot 우선 사용 (close 후 OPEN_POSITIONS에서 이미 제거됨)
                        with _POSITION_LOCK:
                            _pos_for_csv = pos_snapshot if pos_snapshot else dict(OPEN_POSITIONS.get(market, {}))
                        rows[i]["mfe_sec"] = str(_pos_for_csv.get("mfe_sec", ""))
                        rows[i]["trail_dist"] = str(_pos_for_csv.get("trail_dist", ""))
                        rows[i]["trail_stop_pct"] = str(_pos_for_csv.get("trail_stop_pct", ""))
                        _mfe_f = float(rows[i].get("mfe_pct", 0) or 0)
                        rows[i]["peak_drop"] = f"{_mfe_f - pnl_pct * 100:.4f}" if _mfe_f else ""
                        break

                # 🔧 FIX: 원자적 쓰기 (임시파일 → rename, 크래시 시 원본 보존)
                import tempfile
                _dir = os.path.dirname(TRADE_LOG_PATH)
                with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8",
                                                  dir=_dir, suffix=".tmp", delete=False) as tf:
                    w = csv.DictWriter(tf, fieldnames=FEATURE_FIELDS)
                    w.writeheader()
                    w.writerows(rows)
                    _tmp_path = tf.name
                os.replace(_tmp_path, TRADE_LOG_PATH)
            except Exception as e:
                print(f"[TRADE_LOG_UPDATE_ERR] {e}")

    # 📊 시그널별 성과 통계 + MFE 시계열 업데이트
    try:
        _pos_data = pos_snapshot or {}
        _sig_tag = _pos_data.get("signal_tag", "기본")
        _entry_mode = _pos_data.get("entry_mode", "confirm")
        _mfe_snaps = _pos_data.get("mfe_snapshots", {})
        # mfe_pct/mae_pct를 소수 단위로 변환 (% → 소수: 0.5% → 0.005)
        _mfe_dec = mfe_pct / 100.0 if abs(mfe_pct) > 0.1 else mfe_pct
        _mae_dec = mae_pct / 100.0 if abs(mae_pct) > 0.1 else mae_pct
        update_signal_stats(
            signal_tag=_sig_tag,
            pnl_pct=pnl_pct,
            mfe_snapshots=_mfe_snaps,
            entry_mode=_entry_mode,
            exit_reason=exit_reason,
            hold_sec=hold_sec,
            mfe_pct=_mfe_dec,
            mae_pct=_mae_dec,
        )
    except Exception as _ss_err:
        print(f"[SIGNAL_STATS_ERR] {market}: {_ss_err}")

    # 🔧 건수 기반 학습 트리거 (매수만 학습)
    if AUTO_LEARN_ENABLED:
        with _trade_log_lock:
            _trade_count_since_learn += 1

        # 🔧 FIX: streak 업데이트는 record_trade()에서만 (중복 방지)
        # - 여기서 또 업데이트하면 거래 1번에 streak 2번 올라감
        # - record_trade()가 streak의 단일 진실 공급원(SSOT)

        # 🔧 FIX: _lose_streak 읽기를 _STREAK_LOCK 아래서 수행 (스레드 안전 읽기)
        with _STREAK_LOCK:
            _ls_snap = _lose_streak

        # 학습 조건: 10건마다 OR 연속 3패
        with _trade_log_lock:
            should_learn = (
                _trade_count_since_learn >= AUTO_LEARN_INTERVAL or
                _ls_snap >= AUTO_LEARN_STREAK_TRIGGER
            )

        if should_learn:
            with _trade_log_lock:
                trigger_reason = f"연속 {_ls_snap}패" if _ls_snap >= AUTO_LEARN_STREAK_TRIGGER else f"{_trade_count_since_learn}건 도달"
                _trade_count_since_learn = 0  # 리셋
            print(f"[AUTO_LEARN] 학습 트리거: {trigger_reason}")

            try:
                learn_result = analyze_and_update_weights()
                if learn_result:
                    thr = learn_result.get("thresholds", {})
                    chg = learn_result.get("changes", {})

                    # analyze_and_update_weights() 안에서 이미 GATE_* 전역을 갱신함
                    change_detail = " | ".join(
                        f"{k}:{v:+g}" for k, v in chg.items() if v != 0
                    ) or "변화없음"

                    tg_send(
                        f"🧠 <b>자동학습 완료</b> ({trigger_reason})\n"
                        f"📊 승률: {learn_result['win_rate']}% ({learn_result['wins']}승/{learn_result['loses']}패)\n"
                        f"📈 샘플: {learn_result.get('sample_size', 0)}건\n"
                        f"🧱 게이트 변화: {change_detail}\n"
                        f"🎯 현재 임계치: "
                        f"매수비≥{thr.get('GATE_BUY_RATIO_MIN', GATE_BUY_RATIO_MIN):.0%} "
                        f"스프레드≤{thr.get('GATE_SPREAD_MAX', GATE_SPREAD_MAX):.2f}% "
                        f"임밸≥{thr.get('GATE_IMBALANCE_MIN', GATE_IMBALANCE_MIN):.2f} "
                        f"급등≤{thr.get('GATE_SURGE_MAX', GATE_SURGE_MAX):.1f}x "
                        f"가속≥{thr.get('GATE_ACCEL_MIN', GATE_ACCEL_MIN):.2f}x"
                    )
                else:
                    tg_send_mid(f"🧠 자동학습 시도 ({trigger_reason}) - 데이터 부족으로 스킵")

                # 🧠 SL/트레일 자동학습 (게이트 학습과 동시 실행)
                exit_learn_result = auto_learn_exit_params()
                if exit_learn_result:
                    ep = exit_learn_result.get("exit_params", {})
                    ec = exit_learn_result.get("changes", {})
                    exit_change_detail = " | ".join(
                        f"{k}:{v:+.3f}" for k, v in ec.items() if v != 0
                    ) or "변화없음"
                    tg_send(
                        f"🎚 <b>SL/트레일 자동조정</b>\n"
                        f"📉 변화: {exit_change_detail}\n"
                        f"🧯 현재: SL {ep.get('DYN_SL_MIN',0)*100:.2f}~{ep.get('DYN_SL_MAX',0)*100:.2f}% "
                        f"| 트레일 {ep.get('TRAIL_DISTANCE_MIN_BASE',0)*100:.2f}% "
                        f"| 비상 {ep.get('HARD_STOP_DD',0)*100:.1f}%"
                    )
            except Exception as e:
                print(f"[AUTO_LEARN_ERR] {e}")

    # 🔍 경로 리포트 (20건마다 자동 발송)
    # 🔧 FIX: threshold 체크+리셋을 같은 lock 안에서 수행 (중복 리포트 방지)
    with _trade_log_lock:
        _path_report_count += 1
        _current_report_count = _path_report_count
        _should_send_report = _current_report_count >= PATH_REPORT_INTERVAL
        if _should_send_report:
            _path_report_count = 0
    print(f"[PATH_REPORT] 카운트: {_current_report_count}/{PATH_REPORT_INTERVAL}")
    if _should_send_report:
        try:
            # 경로 통계 + 상세 거래 목록 합쳐서 발송
            path_report = get_path_statistics(50)  # 최근 50건 경로 분석
            detail_report = get_recent_trades_detail(10)  # 최근 10건 상세
            combined = path_report + detail_report
            print(f"[PATH_REPORT] 리포트 발송 시도")
            tg_send(combined)
        except Exception as e:
            print(f"[PATH_REPORT_ERR] {e}")

    # 📊 배치 종합 리포트 (30건마다 자동 발송)
    with _trade_log_lock:
        _batch_report_count += 1
        _current_batch_count = _batch_report_count
        _should_send_batch = _current_batch_count >= BATCH_REPORT_INTERVAL
        if _should_send_batch:
            _batch_report_count = 0
    if _should_send_batch:
        try:
            print(f"[BATCH_REPORT] {BATCH_REPORT_INTERVAL}건 도달 → 배치 리포트 발송")
            send_batch_trade_report()
        except Exception as e:
            print(f"[BATCH_REPORT_ERR] {e}")

def get_path_statistics(last_n: int = 100) -> str:
    """
    🔍 경로별 승률 통계 생성 (텔레그램 리포트용)
    """
    if not os.path.exists(TRADE_LOG_PATH):
        return "📊 거래 기록 없음"

    try:
        import pandas as pd
        df = pd.read_csv(TRADE_LOG_PATH)

        # result 컬럼이 있고 값이 있는 행만 (청산 완료된 거래)
        if "result" not in df.columns:
            return "📊 청산 기록 없음"

        df = df[df["result"].isin(["win", "lose"])].tail(last_n)

        if len(df) < 5:
            return f"📊 데이터 부족 ({len(df)}건, 최소 5건 필요)"

        total = len(df)
        wins = len(df[df["result"] == "win"])
        overall_wr = wins / total * 100 if total > 0 else 0

        lines = [
            f"📊 <b>경로별 승률 리포트</b> (최근 {total}건)",
            f"📈 전체 승률: {overall_wr:.1f}% ({wins}승/{total-wins}패)",
            "─" * 20,
        ]

        # 🔥 진입 경로별 승률 (signal_tag 기준)
        if "signal_tag" in df.columns:
            lines.append("<b>📍 진입 경로:</b>")
            tag_stats = {}
            for _, row in df.iterrows():
                tag = str(row.get("signal_tag", "기본"))
                if tag not in tag_stats:
                    tag_stats[tag] = {"win": 0, "total": 0}
                tag_stats[tag]["total"] += 1
                if row["result"] == "win":
                    tag_stats[tag]["win"] += 1

            # 승률 순 정렬
            sorted_tags = sorted(tag_stats.items(),
                                 key=lambda x: (x[1]["win"]/max(x[1]["total"],1), x[1]["total"]),
                                 reverse=True)
            for tag, stats in sorted_tags:
                cnt = stats["total"]
                w = stats["win"]
                wr = w / cnt * 100 if cnt > 0 else 0
                star = " ✅" if wr >= overall_wr + 10 else (" ⚠️" if wr <= overall_wr - 10 else "")
                lines.append(f"  {tag}: {cnt}건 ({wr:.0f}%){star}")

            # 추천: 가장 나쁜 경로 찾기
            lines.append("─" * 20)
            worst = None
            worst_wr = 100
            for tag, stats in sorted_tags:
                cnt = stats["total"]
                if cnt >= 3:
                    wr = stats["win"] / cnt * 100
                    if wr < worst_wr:
                        worst_wr = wr
                        worst = tag

            if worst and worst_wr < overall_wr - 5:
                lines.append(f"💡 검토 필요: {worst} ({worst_wr:.0f}%)")
            else:
                lines.append("💡 특별히 나쁜 경로 없음")

        # 🔧 핵심 파라미터 현황 (실시간 값 리포팅)
        lines.append("")
        lines.append("─" * 20)
        lines.append("<b>⚙️ 핵심 파라미터:</b>")
        dyn_cp = get_dynamic_checkpoint() * 100
        trail_min = get_trail_distance_min() * 100
        lines.append(f"  체크포인트: {dyn_cp:.2f}%")
        lines.append(f"  트레일최소: {trail_min:.2f}%")
        lines.append(f"  MFE타겟: {MFE_PARTIAL_TARGETS.get('기본', 0.005)*100:.1f}%")
        lines.append(f"  ATR배수: {TRAIL_ATR_MULT}")
        lines.append(f"  SL범위: {DYN_SL_MIN*100:.1f}~{DYN_SL_MAX*100:.1f}%")
        lines.append(f"  프로파일: {EXIT_PROFILE}")

        # 📊 entry_mode 분포
        if "entry_mode" in df.columns:
            lines.append("<b>📦 진입모드 분포:</b>")
            for mode in ["probe", "half", "confirm"]:
                mode_df = df[df["entry_mode"] == mode]
                if len(mode_df) > 0:
                    mode_wins = len(mode_df[mode_df["result"] == "win"])
                    mode_wr = mode_wins / len(mode_df) * 100
                    lines.append(f"  {mode}: {len(mode_df)}건 ({mode_wr:.0f}%)")

        # 📊 평균 MFE/MAE
        if "mfe_pct" in df.columns and "mae_pct" in df.columns:
            avg_mfe = pd.to_numeric(df["mfe_pct"], errors="coerce").mean()
            avg_mae = pd.to_numeric(df["mae_pct"], errors="coerce").mean()
            lines.append(f"  평균MFE: +{avg_mfe:.2f}% / 평균MAE: {avg_mae:.2f}%")

        # 📊 손절폭/트레일 튜닝 요약 (데이터 있을 때만)
        lines.append("")
        lines.append("─" * 20)
        lines.append("<b>🎚 SL/트레일 튜닝 데이터:</b>")

        _tuning_cols = {
            "entry_atr_pct": ("진입ATR", "%", 3),
            "entry_pstd": ("진입pstd", "%", 4),
            "mfe_sec": ("MFE도달", "초", 0),
            "hold_sec": ("보유시간", "초", 0),
            "peak_drop": ("피크드롭", "%", 2),
            "trail_dist": ("트레일간격", "%", 3),
            "trail_stop_pct": ("트레일잠금", "%", 3),
        }
        _has_tuning = False
        for col, (label, unit, dec) in _tuning_cols.items():
            if col in df.columns:
                vals = pd.to_numeric(df[col], errors="coerce").dropna()
                if len(vals) >= 2:
                    _has_tuning = True
                    fmt = f"{{:.{dec}f}}"
                    lines.append(f"  {label}: 평균{fmt.format(vals.mean())}{unit} (범위 {fmt.format(vals.min())}~{fmt.format(vals.max())}{unit})")

        if not _has_tuning:
            lines.append("  (아직 데이터 부족 — 거래 쌓이면 표시됩니다)")

        # 📊 MFE 대비 실현 비율 (얼마나 잘 먹고 나왔나)
        if "mfe_pct" in df.columns and "pnl_pct" in df.columns:
            _mfe_s = pd.to_numeric(df["mfe_pct"], errors="coerce")
            _pnl_s = pd.to_numeric(df["pnl_pct"], errors="coerce") * 100
            _valid = (_mfe_s > 0) & _pnl_s.notna()
            if _valid.sum() >= 2:
                _capture = (_pnl_s[_valid] / _mfe_s[_valid]).mean() * 100
                lines.append(f"  MFE캡처율: {_capture:.0f}% (100%=최고점 익절)")
                if _capture < 40:
                    lines.append(f"  💡 캡처율 낮음 → 트레일 간격 줄이기 검토")
                elif _capture > 80:
                    lines.append(f"  💡 캡처율 높음 → 현재 트레일 설정 양호")

        # 📊 손절 분석 (MAE vs SL)
        if "mae_pct" in df.columns:
            _mae_s = pd.to_numeric(df["mae_pct"], errors="coerce").dropna()
            _losses = df[df["result"] == "lose"]
            if len(_losses) >= 2 and "mae_pct" in _losses.columns:
                _loss_mae = pd.to_numeric(_losses["mae_pct"], errors="coerce").dropna()
                if len(_loss_mae) >= 2:
                    _avg_loss_mae = _loss_mae.mean()
                    _sl_min_pct = DYN_SL_MIN * 100  # 소수→% 변환 (0.012→1.2%)
                    _sl_max_pct = DYN_SL_MAX * 100
                    lines.append(f"  패배MAE: 평균{_avg_loss_mae:.2f}% (SL {_sl_min_pct:.1f}~{_sl_max_pct:.1f}%)")
                    if abs(_avg_loss_mae) < _sl_min_pct * 0.5:  # MAE가 SL 하한의 50% 미만 (단위 통일: 둘 다 %)
                        lines.append(f"  💡 손절이 SL 하한 전에 발생 → SL 줄여도 될 수 있음")

        return "\n".join(lines)

    except ImportError:
        return "📊 pandas 미설치 - 통계 불가"
    except Exception as e:
        return f"📊 통계 오류: {e}"

def get_recent_trades_detail(last_n: int = 10) -> str:
    """
    🔍 최근 거래 상세 목록 (임계치 분석용)
    """
    if not os.path.exists(TRADE_LOG_PATH):
        return ""

    try:
        import pandas as pd
        df = pd.read_csv(TRADE_LOG_PATH)

        if "result" not in df.columns:
            return ""

        # 청산 완료된 거래만
        df = df[df["result"].isin(["win", "lose"])].tail(last_n)

        if len(df) == 0:
            return ""

        lines = [
            "",
            f"📋 <b>최근 {len(df)}건 상세</b>",
            "─" * 20,
        ]

        for idx, row in df.iterrows():
            # 기본 정보
            market = str(row.get("market", "?"))[-6:]  # KRW-XXX에서 XXX만
            result = row.get("result", "?")
            pnl_raw = row.get("pnl_pct", 0) or 0
            pnl = float(pnl_raw) * 100  # 🔧 FIX: 소수 → 퍼센트 환산
            icon = "✅" if result == "win" else "❌"
            pnl_str = f"+{pnl:.2f}%" if pnl > 0 else f"{pnl:.2f}%"

            # 경로: signal_tag 하나로 간소화
            signal_tag = str(row.get("signal_tag", "기본"))

            # 지표들 (소수점 정리)
            buy_r = row.get("buy_ratio", 0) or 0
            turn = row.get("turn", 0) or 0
            imbal = row.get("imbalance", 0) or 0
            spread = row.get("spread", 0) or 0
            vol_surge = row.get("volume_surge", 0) or 0
            hold = row.get("hold_sec", 0) or 0

            # 🔥 새 지표
            cons_buys = row.get("consecutive_buys", 0) or 0
            avg_krw = row.get("avg_krw_per_tick", 0) or 0
            flow_accel = row.get("flow_acceleration", 1.0) or 1.0

            # 🔥 GATE 핵심 지표
            overheat = row.get("overheat", 0) or 0
            fresh_age = row.get("fresh_age", 0) or 0

            # 🚀 초단기 미세필터 지표
            cv = row.get("cv", 0) or 0
            pstd = row.get("pstd", 0) or 0
            best_ask_krw = row.get("best_ask_krw", 0) or 0
            is_prebreak = row.get("is_prebreak", 0) or 0

            # 🔧 튜닝 데이터 (손절폭/트레일 간격 최적화용)
            mfe_pct_val = float(row.get("mfe_pct", 0) or 0)
            mae_pct_val = float(row.get("mae_pct", 0) or 0)
            entry_atr = float(row.get("entry_atr_pct", 0) or 0)
            entry_pstd_val = float(row.get("entry_pstd", 0) or 0)
            entry_spread_val = float(row.get("entry_spread", 0) or 0)
            entry_consec_val = float(row.get("entry_consec", 0) or 0)
            mfe_sec_val = float(row.get("mfe_sec", 0) or 0)
            trail_dist_val = float(row.get("trail_dist", 0) or 0)
            trail_stop_val = float(row.get("trail_stop_pct", 0) or 0)
            peak_drop_val = float(row.get("peak_drop", 0) or 0)

            # 시간 (ts에서 시:분만 추출)
            ts = str(row.get("ts", ""))
            time_str = ts[11:16] if len(ts) >= 16 else "?"

            # 가속도 이모지
            accel_emoji = "🚀" if flow_accel >= 1.5 else ("📉" if flow_accel <= 0.7 else "")

            # CV 이모지
            cv_emoji = "🤖" if cv <= 0.45 else ("🔥" if cv >= 1.2 else "")
            # Pre-break 마크
            pb_mark = "⚡PB" if is_prebreak else ""

            # 🔍 진입모드 + 추매 + 청산사유
            entry_mode = str(row.get("entry_mode", "confirm"))
            added_val = str(row.get("added", "0"))
            was_added = added_val == "1"
            exit_reason = str(row.get("exit_reason", "")).strip() or "미기록"

            # 진입모드 이모지 (probe+추매 = 승격)
            if entry_mode == "probe" and was_added:
                mode_str = "🔬→✅승격"  # probe에서 추매로 confirm 승격
            elif entry_mode == "probe":
                mode_str = "🔬탐색"  # probe 진입, 추매 없이 청산
            else:
                mode_str = "✅확정"  # 처음부터 confirm 진입

            # 🔧 MFE/MAE 이모지
            mfe_emoji = "🎯" if mfe_pct_val >= 1.5 else ""
            drop_emoji = "⚠️" if peak_drop_val >= 1.0 else ""

            lines.append(
                f"{icon} {market} {time_str} {pnl_str} {pb_mark}\n"
                f"   {mode_str} 경로:{signal_tag}\n"
                f"   ⏹청산:{exit_reason} ({hold:.0f}초)\n"
                f"   매수{buy_r:.0%} 회전{turn:.1%} 임밸{imbal:.2f} 스프{spread:.2f}%\n"
                f"   배수{vol_surge:.1f}x 연속{cons_buys:.0f} 가속{flow_accel:.1f}x{accel_emoji}\n"
                f"   📈CV{cv:.2f}{cv_emoji} pstd{pstd:.2f}%\n"
                f"   📐MFE+{mfe_pct_val:.2f}%({mfe_sec_val:.0f}초){mfe_emoji} MAE{mae_pct_val:.2f}% 드롭{peak_drop_val:.2f}%{drop_emoji}\n"
                f"   🎚ATR{entry_atr:.3f}% 트레일{trail_dist_val:.3f}% 잠금{trail_stop_val:+.3f}%"
            )

        # 임계치 힌트 (승리/패배 평균 비교)
        wins = df[df["result"] == "win"]
        loses = df[df["result"] == "lose"]

        if len(wins) >= 2 and len(loses) >= 2:
            lines.append("─" * 20)
            lines.append("<b>💡 승/패 평균 비교:</b>")

            for col, name in [("buy_ratio", "매수비"), ("turn", "회전"), ("imbalance", "임밸"),
                              ("spread", "스프레드"), ("volume_surge", "배수"),
                              ("consecutive_buys", "연속매수"), ("flow_acceleration", "가속도"),
                              ("overheat", "과열"), ("fresh_age", "틱나이"),
                              ("cv", "CV"), ("pstd", "pstd"),
                              # 🔧 튜닝 메트릭 승/패 비교
                              ("entry_atr_pct", "진입ATR"), ("mfe_pct", "MFE"),
                              ("mae_pct", "MAE"), ("peak_drop", "피크드롭"),
                              ("mfe_sec", "MFE시간"), ("trail_dist", "트레일간격"),
                              ("hold_sec", "보유시간")]:
                if col in df.columns:
                    w_avg = wins[col].mean() if col in wins.columns else 0
                    l_avg = loses[col].mean() if col in loses.columns else 0
                    if pd.notna(w_avg) and pd.notna(l_avg):
                        diff = w_avg - l_avg
                        if col == "buy_ratio":
                            lines.append(f"  {name}: 승{w_avg:.0%} / 패{l_avg:.0%} (차이 {diff:+.0%})")
                        elif col == "turn":
                            lines.append(f"  {name}: 승{w_avg:.1%} / 패{l_avg:.1%} (차이 {diff:+.1%})")
                        elif col == "spread":
                            # spread는 이미 % 단위로 저장됨 (0.15 = 0.15%)
                            lines.append(f"  {name}: 승{w_avg:.2f}% / 패{l_avg:.2f}% (차이 {diff:+.2f}%)")
                        elif col in ("entry_atr_pct", "entry_pstd", "mfe_pct", "mae_pct",
                                     "peak_drop", "trail_dist", "pstd"):
                            # % 단위 컬럼들
                            lines.append(f"  {name}: 승{w_avg:.2f}% / 패{l_avg:.2f}% (차이 {diff:+.2f}%)")
                        elif col in ("hold_sec", "mfe_sec"):
                            # 초 단위 컬럼들
                            lines.append(f"  {name}: 승{w_avg:.0f}초 / 패{l_avg:.0f}초 (차이 {diff:+.0f}초)")
                        else:
                            lines.append(f"  {name}: 승{w_avg:.2f} / 패{l_avg:.2f} (차이 {diff:+.2f})")

        return "\n".join(lines)

    except ImportError:
        return "📋 pandas 미설치 - 상세 불가"
    except Exception as e:
        return f"📋 상세 오류: {e}"


def send_batch_trade_report():
    """
    📊 50건 배치 종합 리포트 — 텔레그램 발송 + CSV 기록
    핵심 성과 지표 + 진입경로/시간대/코인/청산사유/진입모드별 분석
    """
    if not os.path.exists(TRADE_LOG_PATH):
        tg_send("📊 배치 리포트: 거래 기록 없음")
        return

    try:
        import pandas as pd
        import numpy as np
        df = pd.read_csv(TRADE_LOG_PATH)

        if "result" not in df.columns:
            tg_send("📊 배치 리포트: 청산 기록 없음")
            return

        df = df[df["result"].isin(["win", "lose"])].tail(BATCH_REPORT_INTERVAL)

        if len(df) < BATCH_REPORT_INTERVAL:
            tg_send(f"📊 배치 리포트: 데이터 부족 ({len(df)}/{BATCH_REPORT_INTERVAL}건)")
            return

        total = len(df)
        wins = len(df[df["result"] == "win"])
        losses = total - wins
        wr = wins / total * 100 if total > 0 else 0

        # PnL 계산 (소수 단위 → % 변환)
        pnl_series = pd.to_numeric(df["pnl_pct"], errors="coerce").fillna(0) * 100
        total_pnl = pnl_series.sum()
        avg_pnl = pnl_series.mean()
        win_pnls = pnl_series[df["result"] == "win"]
        lose_pnls = pnl_series[df["result"] == "lose"]
        avg_win_pnl = win_pnls.mean() if wins > 0 else 0
        avg_loss_pnl = lose_pnls.mean() if losses > 0 else 0
        best_trade = pnl_series.max()
        worst_trade = pnl_series.min()

        # 🔑 핵심 성과 지표
        # Profit Factor = 총이익 / 총손실 (>1 이면 수익 구조)
        gross_profit = win_pnls.sum() if wins > 0 else 0
        gross_loss = abs(lose_pnls.sum()) if losses > 0 else 0.001
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Payoff Ratio = 평균이익 / 평균손실 (리워드:리스크)
        payoff_ratio = abs(avg_win_pnl / avg_loss_pnl) if avg_loss_pnl != 0 else float('inf')

        # 기대값 = 승률×평균이익 + 패률×평균손실
        expectancy = (wr / 100 * avg_win_pnl) + ((100 - wr) / 100 * avg_loss_pnl)

        # 최대 연승/연패
        streak_list = (df["result"] == "win").astype(int).tolist()
        max_win_streak = max_lose_streak = cur_win = cur_lose = 0
        for s in streak_list:
            if s == 1:
                cur_win += 1
                cur_lose = 0
            else:
                cur_lose += 1
                cur_win = 0
            max_win_streak = max(max_win_streak, cur_win)
            max_lose_streak = max(max_lose_streak, cur_lose)

        # 최대 낙폭 (equity curve drawdown)
        equity = pnl_series.cumsum()
        running_max = equity.cummax()
        drawdown = equity - running_max
        max_dd = drawdown.min()

        # MFE/MAE 통계
        mfe_vals = pd.to_numeric(df.get("mfe_pct", pd.Series(dtype=float)), errors="coerce").dropna()
        mae_vals = pd.to_numeric(df.get("mae_pct", pd.Series(dtype=float)), errors="coerce").dropna()
        avg_mfe = mfe_vals.mean() if len(mfe_vals) > 0 else 0
        avg_mae = mae_vals.mean() if len(mae_vals) > 0 else 0

        # MFE 활용도 = PnL / MFE (고점 대비 얼마나 챙겼는지)
        mfe_util = 0
        if "mfe_pct" in df.columns and len(mfe_vals) > 0:
            _mfe_aligned = pd.to_numeric(df["mfe_pct"], errors="coerce").fillna(0)
            _mfe_nonzero = _mfe_aligned.replace(0, np.nan)
            _util = (pnl_series / (_mfe_nonzero * 100)).dropna()
            mfe_util = _util.mean() * 100 if len(_util) > 0 else 0

        # MAE 역전률 = MAE -0.3% 이하였는데 결국 이긴 비율
        mae_reversal = 0
        if "mae_pct" in df.columns:
            deep_mae = df[pd.to_numeric(df["mae_pct"], errors="coerce").fillna(0) < -0.3]
            if len(deep_mae) > 0:
                mae_reversal = len(deep_mae[deep_mae["result"] == "win"]) / len(deep_mae) * 100

        # 보유시간 통계
        hold_vals = pd.to_numeric(df.get("hold_sec", pd.Series(dtype=float)), errors="coerce").dropna()
        avg_hold = hold_vals.mean() if len(hold_vals) > 0 else 0
        med_hold = hold_vals.median() if len(hold_vals) > 0 else 0

        # PnL 표준편차 (변동성)
        pnl_std = pnl_series.std() if len(pnl_series) > 1 else 0

        # ═══════════════════════════════════════
        # 리포트 조립
        # ═══════════════════════════════════════

        # ─── 1. 헤더 + 핵심 지표 ───
        lines = [
            f"{'=' * 32}",
            f"📊 <b>배치 종합 리포트</b> (최근 {total}건)",
            f"{'=' * 32}",
            f"📈 승률: {wr:.1f}% ({wins}승 / {losses}패)",
            f"💰 누적PnL: {total_pnl:+.2f}% | 건당기대값: {expectancy:+.3f}%",
            f"📉 평균: {avg_pnl:+.2f}% (승 {avg_win_pnl:+.2f}% / 패 {avg_loss_pnl:+.2f}%)",
            f"🏆 최고: {best_trade:+.2f}% | 최저: {worst_trade:+.2f}%",
            "",
            f"<b>🔑 핵심 지표:</b>",
            f"  PF(Profit Factor): {profit_factor:.2f}" + (" ✅" if profit_factor > 1 else " ❌"),
            f"  Payoff(보상비): {payoff_ratio:.2f} (평균이익/평균손실)",
            f"  최대낙폭: {max_dd:+.2f}% | PnL표준편차: {pnl_std:.3f}%",
            f"  연승max: {max_win_streak} | 연패max: {max_lose_streak}",
        ]

        # ─── 2. MFE/MAE 분석 ───
        lines.append("")
        lines.append(f"{'─' * 32}")
        lines.append("<b>📐 MFE/MAE 분석:</b>")
        lines.append(f"  평균MFE: +{avg_mfe:.2f}% | 평균MAE: {avg_mae:.2f}%")
        lines.append(f"  MFE활용도: {mfe_util:.0f}% (고점 대비 수익 실현율)")
        if mae_reversal > 0 or ("mae_pct" in df.columns):
            lines.append(f"  MAE역전률: {mae_reversal:.0f}% (MAE<-0.3% 후 이긴 비율)")

        # ─── 3. 보유시간 ───
        lines.append(f"  보유시간: 평균{avg_hold:.0f}초 / 중앙값{med_hold:.0f}초")

        # 보유시간 구간별 성과
        if len(hold_vals) > 0:
            lines.append("")
            lines.append(f"{'─' * 32}")
            lines.append("<b>⏱ 보유시간대별:</b>")
            hold_col = pd.to_numeric(df["hold_sec"], errors="coerce").fillna(0) if "hold_sec" in df.columns else pd.Series([0]*total)
            for label, lo, hi in [("~60초", 0, 60), ("60~180초", 60, 180), ("180~600초", 180, 600), ("600초~", 600, 999999)]:
                mask = (hold_col >= lo) & (hold_col < hi)
                sub = df[mask]
                if len(sub) > 0:
                    sw = len(sub[sub["result"] == "win"])
                    s_wr = sw / len(sub) * 100
                    s_pnl = pd.to_numeric(sub["pnl_pct"], errors="coerce").fillna(0).sum() * 100
                    emoji = "🟢" if s_pnl > 0 else "🔴"
                    lines.append(f"  {emoji} {label}: {len(sub)}건 승률{s_wr:.0f}% PnL{s_pnl:+.2f}%")

        # ─── 4. 진입 경로별 통계 ───
        if "signal_tag" in df.columns:
            lines.append("")
            lines.append(f"{'─' * 32}")
            lines.append("<b>📍 경로별 성과:</b>")
            tag_groups = df.groupby(df["signal_tag"].fillna("기본"))
            tag_stats = []
            for tag, grp in tag_groups:
                cnt = len(grp)
                w = len(grp[grp["result"] == "win"])
                t_wr = w / cnt * 100 if cnt > 0 else 0
                t_pnl = pd.to_numeric(grp["pnl_pct"], errors="coerce").fillna(0).sum() * 100
                t_avg = t_pnl / cnt
                tag_stats.append((tag, cnt, w, t_wr, t_pnl, t_avg))
            tag_stats.sort(key=lambda x: x[4], reverse=True)
            for tag, cnt, w, t_wr, t_pnl, t_avg in tag_stats:
                emoji = "🟢" if t_pnl > 0 else "🔴"
                lines.append(f"  {emoji} {tag}: {cnt}건 승률{t_wr:.0f}% PnL{t_pnl:+.2f}% (건당{t_avg:+.2f}%)")

        # ─── 5. 청산 사유별 통계 (카테고리 집계) ───
        if "exit_reason" in df.columns:
            lines.append("")
            lines.append(f"{'─' * 32}")
            lines.append("<b>⏹ 청산사유별:</b>")
            # 청산 사유를 카테고리로 그룹핑
            def _categorize_reason(r):
                r = str(r).strip()
                if "트레일" in r: return "트레일링SL"
                if "본절" in r or "base_stop" in r: return "본절SL"
                if "시간만료" in r: return "시간만료"
                if "손절" in r or "SL" in r.upper(): return "손절SL"
                if "목표가" in r or "TP" in r: return "목표가TP"
                if "좀비" in r or "잔고0" in r or "dust" in r: return "포지션정리"
                return r[:15] if len(r) > 15 else (r or "미기록")
            df["_reason_cat"] = df["exit_reason"].fillna("미기록").apply(_categorize_reason)
            reason_groups = df.groupby("_reason_cat")
            reason_stats = []
            for reason, grp in reason_groups:
                cnt = len(grp)
                w = len(grp[grp["result"] == "win"])
                r_wr = w / cnt * 100 if cnt > 0 else 0
                r_pnl = pd.to_numeric(grp["pnl_pct"], errors="coerce").fillna(0).sum() * 100
                reason_stats.append((reason, cnt, w, r_wr, r_pnl))
            reason_stats.sort(key=lambda x: x[1], reverse=True)
            for reason, cnt, w, r_wr, r_pnl in reason_stats[:8]:
                emoji = "🟢" if r_pnl > 0 else "🔴"
                pct = cnt / total * 100
                lines.append(f"  {emoji} {reason}: {cnt}건({pct:.0f}%) 승률{r_wr:.0f}% PnL{r_pnl:+.2f}%")

        # ─── 6. 코인별 통계 (상위5 + 하위3) ───
        if "market" in df.columns:
            lines.append("")
            lines.append(f"{'─' * 32}")
            lines.append("<b>🪙 코인별 (상위5/하위3):</b>")
            coin_groups = df.groupby("market")
            coin_stats = []
            for market, grp in coin_groups:
                cnt = len(grp)
                w = len(grp[grp["result"] == "win"])
                c_wr = w / cnt * 100 if cnt > 0 else 0
                c_pnl = pd.to_numeric(grp["pnl_pct"], errors="coerce").fillna(0).sum() * 100
                coin_name = str(market).replace("KRW-", "")
                coin_stats.append((coin_name, cnt, w, c_wr, c_pnl))
            coin_stats.sort(key=lambda x: x[4], reverse=True)
            for coin, cnt, w, c_wr, c_pnl in coin_stats[:5]:
                lines.append(f"  🟢 {coin}: {cnt}건 승률{c_wr:.0f}% PnL{c_pnl:+.2f}%")
            if len(coin_stats) > 5:
                lines.append("  ···")
                for coin, cnt, w, c_wr, c_pnl in coin_stats[-3:]:
                    lines.append(f"  🔴 {coin}: {cnt}건 승률{c_wr:.0f}% PnL{c_pnl:+.2f}%")

        # ─── 7. 시간대별 통계 ───
        if "ts" in df.columns:
            lines.append("")
            lines.append(f"{'─' * 32}")
            lines.append("<b>🕐 시간대별:</b>")
            hours = {}
            for _, row in df.iterrows():
                ts = str(row.get("ts", ""))
                if len(ts) >= 13:
                    try:
                        h = int(ts[11:13])
                        bucket = f"{(h // 3) * 3:02d}~{(h // 3) * 3 + 2:02d}시"
                        if bucket not in hours:
                            hours[bucket] = {"win": 0, "total": 0, "pnl": 0.0}
                        hours[bucket]["total"] += 1
                        if row.get("result") == "win":
                            hours[bucket]["win"] += 1
                        pnl_val = float(row.get("pnl_pct", 0) or 0) * 100
                        hours[bucket]["pnl"] += pnl_val
                    except (ValueError, IndexError):
                        pass
            for bucket in sorted(hours.keys()):
                s = hours[bucket]
                h_wr = s["win"] / s["total"] * 100 if s["total"] > 0 else 0
                emoji = "🟢" if s["pnl"] > 0 else "🔴"
                lines.append(f"  {emoji} {bucket}: {s['total']}건 승률{h_wr:.0f}% PnL{s['pnl']:+.2f}%")

        # ─── 8. 진입모드별 통계 ───
        if "entry_mode" in df.columns:
            lines.append("")
            lines.append(f"{'─' * 32}")
            lines.append("<b>📦 진입모드별:</b>")
            for mode in ["probe", "half", "confirm"]:
                mode_df = df[df["entry_mode"] == mode]
                if len(mode_df) > 0:
                    m_wins = len(mode_df[mode_df["result"] == "win"])
                    m_wr = m_wins / len(mode_df) * 100
                    m_pnl = pd.to_numeric(mode_df["pnl_pct"], errors="coerce").fillna(0).sum() * 100
                    emoji = "🟢" if m_pnl > 0 else "🔴"
                    lines.append(f"  {emoji} {mode}: {len(mode_df)}건 승률{m_wr:.0f}% PnL{m_pnl:+.2f}%")

        # ─── 9. 시그널별 MFE 시계열 분석 (메모리 통계) ───
        _sig_report = get_all_signal_stats_report()
        if _sig_report:
            lines.append("")
            lines.append(_sig_report)

        # ─── 10. 진단 요약 (자동 인사이트) ───
        lines.append("")
        lines.append(f"{'─' * 32}")
        lines.append("<b>🧠 자동 진단:</b>")
        insights = []
        if profit_factor < 1:
            insights.append(f"  ⚠️ PF {profit_factor:.2f} < 1 → 현재 구조적 손실 (총손실 > 총이익)")
        if payoff_ratio < 1 and wr < 60:
            insights.append(f"  ⚠️ 보상비 {payoff_ratio:.2f} + 승률 {wr:.0f}% → 둘 다 부족")
        elif payoff_ratio < 1:
            insights.append(f"  ℹ️ 보상비 {payoff_ratio:.2f} < 1 → 승률({wr:.0f}%)로 보완 중")
        if mfe_util < 40 and mfe_util > 0:
            insights.append(f"  ⚠️ MFE활용 {mfe_util:.0f}% → 고점 대비 수익 실현 부족 (익절 개선 필요)")
        if max_lose_streak >= 5:
            insights.append(f"  ⚠️ 최대연패 {max_lose_streak}연패 → 쿨다운/필터 강화 검토")
        if avg_hold > 0 and med_hold > 0 and avg_hold > med_hold * 2:
            insights.append(f"  ℹ️ 평균보유({avg_hold:.0f}초) >> 중앙값({med_hold:.0f}초) → 일부 장기 보유가 평균 왜곡")
        if max_dd < -3:
            insights.append(f"  ⚠️ 최대낙폭 {max_dd:.2f}% → 연속손실 구간 주의")
        if not insights:
            insights.append("  ✅ 특이사항 없음")
        lines.extend(insights)

        lines.append(f"{'=' * 32}")

        report_text = "\n".join(lines)
        tg_send(report_text)
        print(f"[BATCH_REPORT] 배치 리포트 발송 완료 ({total}건)")

        # ─── CSV 기록 ───
        try:
            batch_exists = os.path.exists(BATCH_LOG_PATH)
            with open(BATCH_LOG_PATH, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "ts", "total", "wins", "losses", "win_rate",
                    "total_pnl", "avg_pnl", "avg_win_pnl", "avg_loss_pnl",
                    "profit_factor", "payoff_ratio", "expectancy",
                    "max_dd", "max_win_streak", "max_lose_streak",
                    "mfe_util", "mae_reversal",
                ])
                if not batch_exists:
                    writer.writeheader()
                writer.writerow({
                    "ts": now_kst_str(),
                    "total": total,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": f"{wr:.1f}",
                    "total_pnl": f"{total_pnl:.4f}",
                    "avg_pnl": f"{avg_pnl:.4f}",
                    "avg_win_pnl": f"{avg_win_pnl:.4f}" if wins > 0 else "",
                    "avg_loss_pnl": f"{avg_loss_pnl:.4f}" if losses > 0 else "",
                    "profit_factor": f"{profit_factor:.3f}",
                    "payoff_ratio": f"{payoff_ratio:.3f}",
                    "expectancy": f"{expectancy:.4f}",
                    "max_dd": f"{max_dd:.4f}",
                    "max_win_streak": max_win_streak,
                    "max_lose_streak": max_lose_streak,
                    "mfe_util": f"{mfe_util:.1f}",
                    "mae_reversal": f"{mae_reversal:.1f}",
                })
        except Exception as e:
            print(f"[BATCH_REPORT_CSV_ERR] {e}")

    except ImportError:
        tg_send("📊 배치 리포트: pandas 미설치")
    except Exception as e:
        print(f"[BATCH_REPORT_ERR] {e}")
        traceback.print_exc()


def analyze_and_update_weights():
    """
    🔥 1단계 게이트 임계치 자동 조절
    - 승리/패배 그룹 간 피처 분포 분석
    - GATE_* 전역 변수 조절
    """
    global GATE_TURN_MAX, GATE_SPREAD_MAX
    global GATE_ACCEL_MIN, GATE_ACCEL_MAX, GATE_BUY_RATIO_MIN
    global GATE_SURGE_MAX, GATE_IMBALANCE_MIN, GATE_OVERHEAT_MAX, GATE_FRESH_AGE_MAX
    global GATE_VOL_MIN, GATE_VOL_VS_MA_MIN

    if not os.path.exists(TRADE_LOG_PATH):
        print("[AUTO_LEARN] 거래 로그 없음")
        return None

    try:
        import pandas as pd
        df = pd.read_csv(TRADE_LOG_PATH)

        # 결과가 있는 것만
        df = df[df["result"].isin(["win", "lose"])]

        if len(df) < AUTO_LEARN_MIN_TRADES:
            print(f"[AUTO_LEARN] 데이터 부족 ({len(df)}/{AUTO_LEARN_MIN_TRADES})")
            return None

        wins = df[df["result"] == "win"]
        loses = df[df["result"] == "lose"]

        if len(wins) < 5 or len(loses) < 5:
            print(f"[AUTO_LEARN] 승/패 샘플 부족 (승:{len(wins)}, 패:{len(loses)})")
            return None

        win_rate = round(len(wins) / len(df) * 100, 1)

        # 승/패 평균 계산
        stats = {}
        for col in ["buy_ratio", "spread", "turn", "imbalance", "volume_surge", "flow_acceleration", "overheat", "fresh_age"]:
            try:
                w_avg = pd.to_numeric(wins[col], errors='coerce').mean()
                l_avg = pd.to_numeric(loses[col], errors='coerce').mean()
                # 🔧 FIX: NaN 방어 (json.dump 크래시 → 학습 결과 손실 방지)
                if math.isnan(w_avg): w_avg = 0.0
                if math.isnan(l_avg): l_avg = 0.0
                stats[col] = {"win": w_avg, "lose": l_avg}
            except Exception:
                pass

        # 조절 로직 (신뢰도 기반 동적 블렌딩)
        # 🔧 베이지안 스무딩: 샘플 수에 따라 블렌딩 강도 조절
        # 100건 미만: 매우 보수적 (5%), 100~300건: 표준 (10%), 300건 이상: 적극적 (15%)
        _n_total = len(df)
        if _n_total < 150:
            BLEND = 0.05
        elif _n_total < 300:
            BLEND = 0.10
        else:
            BLEND = 0.15
        # 승/패 샘플 불균형 보정: 소수 그룹이 전체의 20% 미만이면 블렌딩 절반
        _minority_ratio = min(len(wins), len(loses)) / max(_n_total, 1)
        if _minority_ratio < 0.20:
            BLEND *= 0.5
            print(f"[AUTO_LEARN] 승/패 불균형 ({_minority_ratio*100:.0f}% minority) → 블렌딩 절반 적용")
        old_values = {
            "GATE_BUY_RATIO_MIN": GATE_BUY_RATIO_MIN,
            "GATE_SPREAD_MAX": GATE_SPREAD_MAX,
            "GATE_IMBALANCE_MIN": GATE_IMBALANCE_MIN,
            "GATE_SURGE_MAX": GATE_SURGE_MAX,
            "GATE_ACCEL_MIN": GATE_ACCEL_MIN,
            "GATE_OVERHEAT_MAX": GATE_OVERHEAT_MAX,
            "GATE_FRESH_AGE_MAX": GATE_FRESH_AGE_MAX,
        }
        changes = {}

        # 🔧 그림자 모드: 변경값 계산은 하되, AUTO_LEARN_APPLY=True일 때만 실제 적용
        # 매수비: 승자 > 패자면 상향 (더 엄격)
        if "buy_ratio" in stats:
            w, l = stats["buy_ratio"]["win"], stats["buy_ratio"]["lose"]
            if w > l:  # 승자가 더 높은 매수비
                target = min(0.80, w * 0.95)  # 승자 평균의 95%
                new_val = round(GATE_BUY_RATIO_MIN * (1 - BLEND) + target * BLEND, 3)
                new_val = max(0.55, min(0.80, new_val))  # 바운드
                changes["GATE_BUY_RATIO_MIN"] = round(new_val - GATE_BUY_RATIO_MIN, 3)
                if AUTO_LEARN_APPLY:
                    GATE_BUY_RATIO_MIN = new_val

        # 스프레드: 승자 < 패자면 하향 (더 엄격)
        if "spread" in stats:
            w, l = stats["spread"]["win"], stats["spread"]["lose"]
            if w < l:  # 승자가 더 낮은 스프레드
                target = max(0.08, w * 1.2)  # 승자 평균의 120%
                new_val = round(GATE_SPREAD_MAX * (1 - BLEND) + target * BLEND, 3)
                new_val = max(0.08, min(0.35, new_val))  # 바운드
                changes["GATE_SPREAD_MAX"] = round(new_val - GATE_SPREAD_MAX, 3)
                if AUTO_LEARN_APPLY:
                    GATE_SPREAD_MAX = new_val

        # 임밸런스: 승자 > 패자면 상향
        if "imbalance" in stats:
            w, l = stats["imbalance"]["win"], stats["imbalance"]["lose"]
            if w > l:
                target = max(0.20, w * 0.9)
                new_val = round(GATE_IMBALANCE_MIN * (1 - BLEND) + target * BLEND, 3)
                new_val = max(0.15, min(0.50, new_val))
                changes["GATE_IMBALANCE_MIN"] = round(new_val - GATE_IMBALANCE_MIN, 3)
                if AUTO_LEARN_APPLY:
                    GATE_IMBALANCE_MIN = new_val

        # 급등: 승자 < 패자면 하향 (더 엄격)
        if "volume_surge" in stats:
            w, l = stats["volume_surge"]["win"], stats["volume_surge"]["lose"]
            if w < l:
                target = max(1.5, w * 1.3)
                new_val = round(GATE_SURGE_MAX * (1 - BLEND) + target * BLEND, 2)
                new_val = max(1.5, min(6.0, new_val))
                changes["GATE_SURGE_MAX"] = round(new_val - GATE_SURGE_MAX, 2)
                if AUTO_LEARN_APPLY:
                    GATE_SURGE_MAX = new_val

        # 가속도: 승자 > 패자면 상향
        if "flow_acceleration" in stats:
            w, l = stats["flow_acceleration"]["win"], stats["flow_acceleration"]["lose"]
            if w > l:
                target = max(0.3, w * 0.7)
                new_val = round(GATE_ACCEL_MIN * (1 - BLEND) + target * BLEND, 2)
                new_val = max(0.3, min(1.5, new_val))
                changes["GATE_ACCEL_MIN"] = round(new_val - GATE_ACCEL_MIN, 2)
                if AUTO_LEARN_APPLY:
                    GATE_ACCEL_MIN = new_val

        # 과열(overheat): 승자 < 패자면 하향 (더 엄격)
        if "overheat" in stats:
            w, l = stats["overheat"]["win"], stats["overheat"]["lose"]
            if w < l:  # 승자가 낮은 과열
                target = max(2.0, w * 1.3)  # 승자 평균의 130%
                new_val = round(GATE_OVERHEAT_MAX * (1 - BLEND) + target * BLEND, 2)
                new_val = max(2.0, min(8.0, new_val))  # 바운드
                changes["GATE_OVERHEAT_MAX"] = round(new_val - GATE_OVERHEAT_MAX, 2)
                if AUTO_LEARN_APPLY:
                    GATE_OVERHEAT_MAX = new_val

        # 틱나이(fresh_age): 승자 < 패자면 하향 (더 엄격)
        if "fresh_age" in stats:
            w, l = stats["fresh_age"]["win"], stats["fresh_age"]["lose"]
            if w < l:  # 승자가 낮은 틱나이 (더 신선)
                target = max(1.0, w * 1.5)  # 승자 평균의 150%
                new_val = round(GATE_FRESH_AGE_MAX * (1 - BLEND) + target * BLEND, 2)
                new_val = max(1.0, min(6.0, new_val))  # 바운드
                changes["GATE_FRESH_AGE_MAX"] = round(new_val - GATE_FRESH_AGE_MAX, 2)
                if AUTO_LEARN_APPLY:
                    GATE_FRESH_AGE_MAX = new_val

        new_values = {
            "GATE_BUY_RATIO_MIN": GATE_BUY_RATIO_MIN,
            "GATE_SPREAD_MAX": GATE_SPREAD_MAX,
            "GATE_IMBALANCE_MIN": GATE_IMBALANCE_MIN,
            "GATE_SURGE_MAX": GATE_SURGE_MAX,
            "GATE_ACCEL_MIN": GATE_ACCEL_MIN,
            "GATE_OVERHEAT_MAX": GATE_OVERHEAT_MAX,
            "GATE_FRESH_AGE_MAX": GATE_FRESH_AGE_MAX,
            "GATE_VOL_MIN": GATE_VOL_MIN,
        }

        # 🔧 FIX: 원자적 쓰기 (크래시 시 학습 데이터 손실 방지)
        import tempfile
        _wdir = os.path.dirname(WEIGHTS_PATH) or "."
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                          dir=_wdir, suffix=".tmp", delete=False) as _wf:
            json.dump({
                "gate_thresholds": new_values,
                "updated_at": now_kst_str(),
                "sample_size": len(df),
                "win_rate": win_rate,
                "feature_stats": {k: {"win": round(v["win"], 3), "lose": round(v["lose"], 3)} for k, v in stats.items()}
            }, _wf, ensure_ascii=False, indent=2)
            _wf_path = _wf.name
        os.replace(_wf_path, WEIGHTS_PATH)

        print(f"[AUTO_LEARN] 게이트 임계치 업데이트: {new_values}")
        print(f"[AUTO_LEARN] 승률: {len(wins)}/{len(df)} = {win_rate}%")

        return {
            "thresholds": new_values,
            "old_values": old_values,
            "changes": changes,
            "win_rate": win_rate,
            "wins": len(wins),
            "loses": len(loses),
            "sample_size": len(df)
        }

    except ImportError:
        print("[AUTO_LEARN] pandas 미설치 - 수동 분석 필요")
        return None
    except Exception as e:
        print(f"[AUTO_LEARN_ERR] {e}")
        traceback.print_exc()
        return None

# =========================
# 🧠 매도 파라미터 자동 튜닝
# =========================
EXIT_PARAMS_PATH = os.path.join(os.getcwd(), "learned_exit_params.json")

# 🔧 hard_stop 제거 → 동적손절(ATR)로 대체 (DYN_SL_MIN~DYN_SL_MAX)
DYNAMIC_EXIT_PARAMS = {}

# =========================
# 🧠 SL/트레일 자동학습 (데이터 기반 동적 조정)
# =========================
def auto_learn_exit_params():
    """
    📊 trade_features.csv의 MAE/MFE/트레일 데이터를 분석하여
    DYN_SL_MIN, DYN_SL_MAX, TRAIL_DISTANCE_MIN_BASE 등을 자동 조정

    분석 항목:
    1) 패배 MAE → SL 적정선 판단 (너무 넓으면 줄이고, 너무 좁으면 넓힘)
    2) MFE 캡처율 → 트레일 간격 조정 (캡처율 낮으면 트레일 좁히기)
    3) 승리 peak_drop → 트레일 거리 적정선

    바운드:
    - DYN_SL_MIN: 0.008 ~ 0.020 (0.8% ~ 2.0%)
    - DYN_SL_MAX: 0.018 ~ 0.035 (1.8% ~ 3.5%)
    - TRAIL_DISTANCE_MIN_BASE: 0.001 ~ 0.002 (0.1% ~ 0.2%)
    """
    global DYN_SL_MIN, DYN_SL_MAX, TRAIL_DISTANCE_MIN_BASE, HARD_STOP_DD

    if not os.path.exists(TRADE_LOG_PATH):
        print("[EXIT_LEARN] 거래 로그 없음")
        return None

    try:
        import pandas as pd
        df = pd.read_csv(TRADE_LOG_PATH)

        df = df[df["result"].isin(["win", "lose"])]
        if len(df) < AUTO_LEARN_MIN_TRADES:
            print(f"[EXIT_LEARN] 데이터 부족 ({len(df)}/{AUTO_LEARN_MIN_TRADES})")
            return None

        wins = df[df["result"] == "win"]
        loses = df[df["result"] == "lose"]
        if len(wins) < 5 or len(loses) < 5:
            print(f"[EXIT_LEARN] 승/패 샘플 부족 (승:{len(wins)}, 패:{len(loses)})")
            return None

        # 🔧 베이지안 블렌딩 (샘플 수 기반)
        _n = len(df)
        BLEND = 0.08 if _n < 150 else (0.12 if _n < 300 else 0.18)
        _minority = min(len(wins), len(loses)) / max(_n, 1)
        if _minority < 0.20:
            BLEND *= 0.5

        old_sl_min = DYN_SL_MIN
        old_sl_max = DYN_SL_MAX
        old_trail = TRAIL_DISTANCE_MIN_BASE
        old_hard = HARD_STOP_DD
        changes = {}

        # =====================================================
        # 1) 패배 MAE 분석 → DYN_SL_MIN 조정
        # =====================================================
        # MAE = 해당 거래의 최대 역행폭 (얼마나 빠졌다가 손절됐는지)
        # - 패배 MAE 평균이 SL보다 훨씬 작으면 → 다른 원인으로 손절 (SL은 적정)
        # - 패배 MAE 평균이 SL 근처면 → SL에 맞고 나간 것 (노이즈 가능 → SL 넓히기)
        # - 패배 MAE 평균이 SL보다 크면 → SL 이후 더 빠짐 (SL 적정 or 좁혀도 됨)
        if "mae_pct" in df.columns:
            loss_mae = pd.to_numeric(loses["mae_pct"], errors="coerce").dropna()
            if len(loss_mae) >= 5:
                avg_loss_mae = abs(loss_mae.mean())  # % 단위 (예: 1.2)
                avg_loss_mae_dec = avg_loss_mae / 100  # 소수 단위 (예: 0.012)

                current_sl_pct = DYN_SL_MIN * 100  # % 단위

                # 패배 MAE가 SL의 80~120% 범위 = SL 경계에서 손절 (노이즈 가능 → 넓히기)
                if avg_loss_mae >= current_sl_pct * 0.80:
                    # SL 경계 손절 → SL을 패배MAE의 120%로 타겟
                    target_sl = avg_loss_mae_dec * 1.20
                    new_sl = DYN_SL_MIN * (1 - BLEND) + target_sl * BLEND
                    new_sl = max(0.003, min(0.007, round(new_sl, 4)))
                    changes["DYN_SL_MIN"] = round(new_sl - DYN_SL_MIN, 4)
                    if AUTO_LEARN_APPLY:
                        DYN_SL_MIN = new_sl
                        refresh_mfe_targets()
                        print(f"[EXIT_LEARN] SL 넓힘: {old_sl_min*100:.2f}%→{new_sl*100:.2f}% (패배MAE={avg_loss_mae:.2f}%, SL경계 손절)")

                # 패배 MAE가 SL의 50% 미만 = SL 전에 다른 원인으로 청산 (SL 좁혀도 됨)
                elif avg_loss_mae < current_sl_pct * 0.50:
                    target_sl = avg_loss_mae_dec * 1.50  # MAE의 150% 정도로 축소
                    new_sl = DYN_SL_MIN * (1 - BLEND) + target_sl * BLEND
                    new_sl = max(0.003, min(0.007, round(new_sl, 4)))
                    changes["DYN_SL_MIN"] = round(new_sl - DYN_SL_MIN, 4)
                    if AUTO_LEARN_APPLY:
                        DYN_SL_MIN = new_sl
                        refresh_mfe_targets()
                        print(f"[EXIT_LEARN] SL 좁힘: {old_sl_min*100:.2f}%→{new_sl*100:.2f}% (패배MAE={avg_loss_mae:.2f}%, SL전 청산)")

        # =====================================================
        # 2) DYN_SL_MAX = DYN_SL_MIN × 2.0 연동 (바운드: 0.7~1.5%)
        # =====================================================
        new_sl_max = round(DYN_SL_MIN * 2.0, 4)
        new_sl_max = max(0.007, min(0.015, new_sl_max))
        if abs(new_sl_max - old_sl_max) > 0.0005:
            changes["DYN_SL_MAX"] = round(new_sl_max - old_sl_max, 4)
            if AUTO_LEARN_APPLY:
                DYN_SL_MAX = new_sl_max

        # =====================================================
        # 3) HARD_STOP_DD = DYN_SL_MIN × 2.0 연동 (바운드: 0.7~1.5%)
        # =====================================================
        new_hard = round(DYN_SL_MIN * 2.0, 4)
        new_hard = max(0.007, min(0.015, new_hard))
        if abs(new_hard - old_hard) > 0.001:
            changes["HARD_STOP_DD"] = round(new_hard - old_hard, 4)
            if AUTO_LEARN_APPLY:
                HARD_STOP_DD = new_hard

        # =====================================================
        # 4) 트레일 간격 조정 (MFE 캡처율 + 승리 peak_drop 기반)
        # =====================================================
        _trail_adjusted = False
        if "mfe_pct" in df.columns and "pnl_pct" in df.columns:
            mfe_s = pd.to_numeric(wins["mfe_pct"], errors="coerce")
            pnl_s = pd.to_numeric(wins["pnl_pct"], errors="coerce") * 100  # % 변환
            valid = (mfe_s > 0) & pnl_s.notna()
            if valid.sum() >= 5:
                capture_rate = (pnl_s[valid] / mfe_s[valid]).mean()  # 0~1 비율

                # 캡처율 40% 미만 → 트레일이 넓어서 수익 흘림 → 좁히기
                if capture_rate < 0.40:
                    target_trail = TRAIL_DISTANCE_MIN_BASE * 0.85  # 15% 축소 방향
                    new_trail = TRAIL_DISTANCE_MIN_BASE * (1 - BLEND) + target_trail * BLEND
                    new_trail = max(0.001, min(0.002, round(new_trail, 4)))
                    changes["TRAIL_DISTANCE_MIN_BASE"] = round(new_trail - old_trail, 4)
                    if AUTO_LEARN_APPLY:
                        TRAIL_DISTANCE_MIN_BASE = new_trail
                        _trail_adjusted = True
                        print(f"[EXIT_LEARN] 트레일 좁힘: {old_trail*100:.2f}%→{new_trail*100:.2f}% (캡처율={capture_rate*100:.0f}%)")

                # 캡처율 70% 이상 → 트레일 적정 or 살짝 넓혀도 됨 (눌림 허용)
                elif capture_rate > 0.70:
                    target_trail = TRAIL_DISTANCE_MIN_BASE * 1.10  # 10% 확대 방향
                    new_trail = TRAIL_DISTANCE_MIN_BASE * (1 - BLEND) + target_trail * BLEND
                    new_trail = max(0.001, min(0.002, round(new_trail, 4)))
                    changes["TRAIL_DISTANCE_MIN_BASE"] = round(new_trail - old_trail, 4)
                    if AUTO_LEARN_APPLY:
                        TRAIL_DISTANCE_MIN_BASE = new_trail
                        _trail_adjusted = True
                        print(f"[EXIT_LEARN] 트레일 넓힘: {old_trail*100:.2f}%→{new_trail*100:.2f}% (캡처율={capture_rate*100:.0f}%)")

        # 트레일 미조정 시: 승리 peak_drop으로 보조 조정
        if not _trail_adjusted and "peak_drop" in df.columns:
            win_drops = pd.to_numeric(wins["peak_drop"], errors="coerce").dropna()
            if len(win_drops) >= 5:
                avg_drop = abs(win_drops.mean()) / 100  # % → 소수
                # 승리 시 평균 피크드롭의 80%를 트레일 간격으로
                target_trail = max(0.001, avg_drop * 0.80)
                new_trail = TRAIL_DISTANCE_MIN_BASE * (1 - BLEND) + target_trail * BLEND
                new_trail = max(0.001, min(0.002, round(new_trail, 4)))
                if abs(new_trail - old_trail) > 0.0005:
                    changes["TRAIL_DISTANCE_MIN_BASE"] = round(new_trail - old_trail, 4)
                    if AUTO_LEARN_APPLY:
                        TRAIL_DISTANCE_MIN_BASE = new_trail
                        print(f"[EXIT_LEARN] 트레일(피크드롭): {old_trail*100:.2f}%→{new_trail*100:.2f}% (승리avg_drop={avg_drop*100:.2f}%)")

        # =====================================================
        # 5) 결과 저장
        # =====================================================
        result_data = {
            "DYN_SL_MIN": DYN_SL_MIN,
            "DYN_SL_MAX": DYN_SL_MAX,
            "HARD_STOP_DD": HARD_STOP_DD,
            "TRAIL_DISTANCE_MIN_BASE": TRAIL_DISTANCE_MIN_BASE,
        }

        import tempfile
        _wdir = os.path.dirname(EXIT_PARAMS_PATH) or "."
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                          dir=_wdir, suffix=".tmp", delete=False) as _wf:
            json.dump({
                "exit_params": result_data,
                "updated_at": now_kst_str(),
                "sample_size": len(df),
                "win_rate": round(len(wins) / len(df) * 100, 1),
            }, _wf, ensure_ascii=False, indent=2)
            _wf_path = _wf.name
        os.replace(_wf_path, EXIT_PARAMS_PATH)

        win_rate = round(len(wins) / len(df) * 100, 1)
        change_detail = " | ".join(
            f"{k}:{v:+.4f}" for k, v in changes.items() if v != 0
        ) or "변화없음"

        print(f"[EXIT_LEARN] 완료: {result_data} | 변화: {change_detail}")
        return {
            "exit_params": result_data,
            "changes": changes,
            "win_rate": win_rate,
            "sample_size": len(df),
        }

    except ImportError:
        print("[EXIT_LEARN] pandas 미설치")
        return None
    except Exception as e:
        print(f"[EXIT_LEARN_ERR] {e}")
        traceback.print_exc()
        return None

# 부분청산/본절가드 — config.py에서 정의됨
# (PARTIAL_EXIT_*, BREAKEVEN_BOX, SOFT_GUARD_SEC)

def is_strong_momentum(ticks, ob):
    """
    체크포인트 도달 시점에 강세 여부 판단 (점수 기반 + 가속도)
    - 약세 점수 3점 이상이면 약세 → 즉시 매도
    - 약세 점수 2점 이하면 강세 → 홀딩 (트레일링 모드)
    - 🔧 NEW: 가속도 추가, BTC 레짐 참조
    """
    try:
        t15 = micro_tape_stats_from_ticks(ticks or [], 15)
        t5 = micro_tape_stats_from_ticks(ticks or [], 5)
        imb = calc_orderbook_imbalance(ob) if ob else 0.0
        fresh = uptick_streak_from_ticks(ticks, need=2) if ticks else False

        # 약세 점수 계산 (0~6점)
        weak_score = 0

        # 1) 매수비 낮음
        if t15["buy_ratio"] < 0.55:
            weak_score += 1

        # 2) 거래속도 둔화
        if t15["krw_per_sec"] < 15000:
            weak_score += 1

        # 3) 호가 매도우세 (imbalance < -0.1)
        if imb < -0.1:
            weak_score += 1

        # 4) 상승틱 끊김
        if not fresh:
            weak_score += 1

        # 🔧 NEW: 5) 최근 5초가 15초 대비 급감 (모멘텀 소진)
        if (t5["krw_per_sec"] > 0 and t15["krw_per_sec"] > 0
                and t5["krw_per_sec"] < t15["krw_per_sec"] * 0.40):
            weak_score += 1

        # 🔧 NEW: 강세 보너스 (매수비 + 임밸런스 동시 강하면 약세 점수 감산)
        if t5["buy_ratio"] >= 0.65 and imb >= 0.30:
            weak_score = max(0, weak_score - 1)

        # 약세 점수 3점 이상이면 약세 판정 → False 반환
        return weak_score < 3
    except Exception:
        return False

def load_exit_params():
    """저장된 매도 파라미터 로드"""
    global DYNAMIC_EXIT_PARAMS
    if os.path.exists(EXIT_PARAMS_PATH):
        try:
            with open(EXIT_PARAMS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "exit_params" in data:
                DYNAMIC_EXIT_PARAMS.update(data["exit_params"])
                print(f"[EXIT_PARAMS] 로드 완료: 승률 {data.get('win_rate')}%, 평균손익 {data.get('avg_pnl')}%")
        except Exception as e:
            print(f"[EXIT_PARAMS_LOAD_ERR] {e}")

def load_learned_weights():
    """
    저장된 학습 파일 로드
    - 과거 버전 호환: "weights" 키는 무시 (스코어 시스템 폐지)
    - 현행: "gate_thresholds"가 있으면 GATE_* 임계치 복원
    """
    global GATE_TURN_MAX, GATE_SPREAD_MAX
    global GATE_ACCEL_MIN, GATE_ACCEL_MAX, GATE_BUY_RATIO_MIN
    global GATE_SURGE_MAX, GATE_OVERHEAT_MAX, GATE_IMBALANCE_MIN, GATE_FRESH_AGE_MAX
    global GATE_VOL_MIN, GATE_VOL_VS_MA_MIN
    global DYN_SL_MIN, DYN_SL_MAX, HARD_STOP_DD, TRAIL_DISTANCE_MIN_BASE

    if not os.path.exists(WEIGHTS_PATH):
        print("[WEIGHTS] 학습된 파일 없음 - 기본값 사용")
        return

    try:
        with open(WEIGHTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        # (현행) 게이트 임계치 복원
        thr = data.get("gate_thresholds")
        if isinstance(thr, dict):
            GATE_TURN_MAX      = thr.get("GATE_TURN_MAX",      GATE_TURN_MAX)
            GATE_SPREAD_MAX    = thr.get("GATE_SPREAD_MAX",    GATE_SPREAD_MAX)
            GATE_ACCEL_MIN     = thr.get("GATE_ACCEL_MIN",     GATE_ACCEL_MIN)
            GATE_ACCEL_MAX     = thr.get("GATE_ACCEL_MAX",     GATE_ACCEL_MAX)
            GATE_BUY_RATIO_MIN = thr.get("GATE_BUY_RATIO_MIN", GATE_BUY_RATIO_MIN)
            GATE_SURGE_MAX     = thr.get("GATE_SURGE_MAX",     GATE_SURGE_MAX)
            GATE_OVERHEAT_MAX  = thr.get("GATE_OVERHEAT_MAX",  GATE_OVERHEAT_MAX)
            GATE_IMBALANCE_MIN = thr.get("GATE_IMBALANCE_MIN", GATE_IMBALANCE_MIN)
            print(f"[WEIGHTS] 게이트 임계치 로드: {thr}")

        print(f"[WEIGHTS] 업데이트: {data.get('updated_at', '?')}, 샘플: {data.get('sample_size', '?')}, 승률: {data.get('win_rate', '?')}%")
    except Exception as e:
        print(f"[WEIGHTS_LOAD_ERR] {e}")

    # 🔧 WF데이터: SL/트레일 학습 결과 로딩 비활성화 (WF 고정값만 사용)
    # 기존 학습 파일이 WF 데이터 기반 파라미터를 덮어쓰는 것 방지
    # if os.path.exists(EXIT_PARAMS_PATH):
    #     ... (비활성화됨)
    print("[WEIGHTS] SL/트레일 = WF 데이터 고정값 사용 (학습 로딩 비활성화)")

# =========================
# 세션/요청(네트워크 안정화)
# =========================
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter


def _new_session():
    s = requests.Session()
    # 🔧 urllib3 버전 호환성 (1.26+ = allowed_methods, 구버전 = method_whitelist)
    # 🔧 FIX: POST는 자동재시도 제외 (중복 주문 방지)
    retry_kwargs = dict(
        total=3,
        backoff_factor=0.3,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    try:
        retry = Retry(allowed_methods=frozenset(["GET"]), **retry_kwargs)  # POST 제거
    except TypeError:
        # urllib3 < 1.26 fallback
        retry = Retry(method_whitelist=frozenset(["GET"]), **retry_kwargs)  # POST 제거
    adapter = HTTPAdapter(pool_connections=256,
                          pool_maxsize=256,
                          max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "Connection": "keep-alive",
        "User-Agent": "UpbitSniper/3.2.7-hh+...+netRetry"
    })
    return s


SESSION = _new_session()
# 🔧 FIX: 텔레그램 전용 세션 분리 (SESSION 리프레시 중 청산알림 유실 방지)
# - SESSION은 업비트 API + 텔레그램 공유 → _refresh_session() 시 close→재생성 gap에서 tg_send 실패
# - _TG_SESSION은 텔레그램 전용, 별도 라이프사이클 → API 세션 리프레시 영향 없음
_TG_SESSION = _new_session()
_TG_SESSION_LOCK = threading.Lock()
KST = timezone(timedelta(hours=9))

def now_kst():
    return datetime.now(KST)

def now_kst_str():
    return now_kst().strftime("%Y-%m-%d %H:%M:%S KST")

# =========================
# 🔥 시간대별 스캔 간격
# =========================
def get_scan_interval():
    """
    스캔 주기: 전 시간대 3초 통일
    🔧 기존 3~6초 가변 → 3초 고정 (진입 지연 최소화)
    """
    return 3

def link_for(m):
    return f"https://upbit.com/exchange?code=CRIX.UPBIT.{m}"


# 토큰버킷
_BUCKET = {"tokens": 6.0, "last": time.time(), "cap": 6.0, "rate": 4.5}
_req_lock = threading.Lock()
REQ_STATS = {"ok": 0, "http429": 0, "http5xx": 0, "errors": 0, "conn_err": 0}
_CONSEC_CONN_ERR = 0


def _throttle():
    while True:
        with _req_lock:
            now = time.time()
            delta = now - _BUCKET["last"]
            _BUCKET["last"] = now
            rate = max(float(_BUCKET.get("rate", 0.0)), 0.1)
            cap = max(float(_BUCKET.get("cap", 1.0)), 1.0)
            tokens = min(cap, max(0.0, _BUCKET["tokens"] + delta * rate))
            if tokens >= 1.0:
                _BUCKET["tokens"] = tokens - 1.0
                return
            _BUCKET["tokens"] = tokens
            need = 1.0 - tokens
        base_wait = need / rate
        time.sleep(min(1.2, max(0.05, base_wait)) * (1.0 + 0.2 * rnd()))


_SESSION_REFRESH_LOCK = threading.Lock()

def _refresh_session():
    global SESSION, _CONSEC_CONN_ERR
    # 🔧 FIX 7차: close 전에 새 세션 먼저 생성 (gap 제거)
    # 기존: close() → _new_session() 사이에 다른 스레드가 닫힌 SESSION 사용 → ConnectionError
    # 변경: 새 세션 생성 → SESSION 교체 → 구 세션 close (무중단)
    with _SESSION_REFRESH_LOCK:
        old_session = SESSION
        SESSION = _new_session()
        _CONSEC_CONN_ERR = 0
        print("[NET] session refreshed")
    # 구 세션은 락 밖에서 close (진행 중인 요청 완료 대기 불필요 — requests는 스레드세이프)
    try:
        old_session.close()
    except Exception:
        pass


def upbit_get(url, params=None, timeout=7):
    global _CONSEC_CONN_ERR
    for attempt in range(3):  # 4 -> 3 유지
        try:
            _throttle()
            # 🔧 FIX 7차: SESSION 참조를 락으로 보호하여 캐시 (교체 중 닫힌 세션 사용 방지)
            with _SESSION_REFRESH_LOCK:
                _s = SESSION
            r = _s.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                REQ_STATS["http429"] += 1
                # 지수적 백오프 + 버킷 속도 하향(보다 공격적으로)
                backoff = min(1.2 * (2**attempt), 6.0)
                time.sleep(backoff)
                # 🔧 FIX H2: 락 보호 (멀티스레드 429 캐스케이드 방지)
                with _req_lock:
                    _BUCKET["rate"] = max(3.0, _BUCKET["rate"] - 0.4)
                    _BUCKET["cap"] = max(4.0, _BUCKET["cap"] - 0.5)
                continue
            if 500 <= r.status_code < 600:
                REQ_STATS["http5xx"] += 1
                time.sleep(0.35 * (2**attempt))
                continue
            r.raise_for_status()
            REQ_STATS["ok"] += 1
            with _SESSION_REFRESH_LOCK:
                _CONSEC_CONN_ERR = 0
            # ✅ 429 후 점진 회복 (장기 성능 저하 방지)
            # 🔧 FIX H2: 락 보호 (멀티스레드 동시 수정 방지)
            with _req_lock:
                _BUCKET["rate"] = min(4.5, float(_BUCKET.get("rate", 3.0)) + 0.10)
                _BUCKET["cap"]  = min(6.0, float(_BUCKET.get("cap", 4.0)) + 0.10)
            return r.json()
        except requests.exceptions.Timeout:
            if attempt == 2: return None
            time.sleep(0.35 * (2**attempt))
        except requests.exceptions.ConnectionError:
            REQ_STATS["errors"] += 1
            REQ_STATS["conn_err"] += 1
            # 🔧 FIX: 락 보호 (read-modify-write 레이스 방지)
            with _SESSION_REFRESH_LOCK:
                _CONSEC_CONN_ERR += 1
                _should_refresh = _CONSEC_CONN_ERR >= 3
            if _should_refresh:
                _refresh_session()
                time.sleep(0.6)
            else:
                time.sleep(0.4 * (2**attempt))
            if attempt == 2: return None
        except Exception:
            REQ_STATS["errors"] += 1
            if attempt == 2: return None
            time.sleep(0.2 * (2**attempt))
    return None

# =========================================================
# 🧩 안전 네트워크 요청 래퍼 (자동 재시도 + 백오프)
# =========================================================
def safe_upbit_get(url, params=None, timeout=6, retries=1, backoff=1.5):
    """
    업비트 API 요청용 안전 래퍼
    - upbit_get() 자체가 3회 재시도하므로 여기선 1회만 (중복 재시도 방지)
    - 🔧 FIX: retries 3→1 (기존 3×3=9회 → 3+1=4회로 축소, 펌핑 지연 방지)
    """
    for i in range(retries):
        try:
            js = upbit_get(url, params, timeout=timeout)
            if js:
                return js
        except Exception as e:
            print(f"[SAFE_GET] {url.split('/')[-1]} 실패 ({e}) → 재시도 {i+1}/{retries}")
        time.sleep(backoff * (i + 1))
    print(f"[SAFE_GET_FAIL] {url.split('/')[-1]} 최종 실패")
    return None

def req_summary():
    print(
        f"[REQ] ok:{REQ_STATS['ok']}  429:{REQ_STATS['http429']}  5xx:{REQ_STATS['http5xx']}  err:{REQ_STATS['errors']}"
    )


def aligned_sleep(interval):
    t = time.time()
    nxt = math.ceil(t / interval) * interval
    time.sleep(max(0, nxt - t))


# =========================
# 지표 유틸
# =========================
def vwap_from_candles_1m(c1, n=20):
    seg = c1[-n:] if len(c1) >= n else c1[:]
    pv = sum(x["trade_price"] * x["candle_acc_trade_volume"] for x in seg)
    vol = sum(x["candle_acc_trade_volume"] for x in seg)
    return pv / max(vol, 1e-12)


def zscore_krw_1m(c1, win=30):
    seg = c1[-win:] if len(c1) >= win else c1[:]
    arr = [x["candle_acc_trade_price"] for x in seg]
    if len(arr) < 3: return 0.0
    m = sum(arr) / len(arr)
    sd = (sum((a - m)**2 for a in arr) / max(len(arr) - 1, 1))**0.5
    return (arr[-1] - m) / max(sd, 1e-9)


def uptick_streak_from_ticks(ticks, need=2):
    if not ticks:  # 🔧 FIX: None/빈리스트 방어
        return False
    t = sorted(ticks[:need + 4], key=tick_ts_ms)
    return sum(1 for a, b in zip(t, t[1:])
               if b.get("trade_price", 0) > a.get("trade_price", 0)) >= need


def last_two_ticks_fresh(ticks, max_age=None, return_age=False):
    """
    틱 신선도 체크 - GATE_FRESH_AGE_MAX 전역 변수 사용

    return_age=True: (bool, max_tick_age, effective_max_age) 반환
    return_age=False: bool만 반환 (기존 호환)
    """
    if max_age is None:
        max_age = GATE_FRESH_AGE_MAX
    if len(ticks) < 2:
        if return_age:
            return False, 999.0, max_age
        return False
    # 🔧 시간대별 신선도 동적 완화 (장중 엄격, 야간 완화)
    h = now_kst().hour
    if 0 <= h < 6:
        max_age = max(max_age, 5.0)   # 야간: 최소 5초
    elif 6 <= h < 9:
        max_age = max(max_age, 4.0)   # 새벽~장전: 최소 4초
    # 9~24시(장중): GATE_FRESH_AGE_MAX 그대로 (3초)
    now = int(time.time() * 1000)
    # 최근 2틱 중 가장 오래된 틱의 나이 계산
    tick_ages = [(now - tick_ts_ms(x)) / 1000.0 for x in ticks[:2]]
    max_tick_age = max(tick_ages) if tick_ages else 999.0
    is_fresh = all(age <= max_age for age in tick_ages)

    if return_age:
        return is_fresh, max_tick_age, max_age
    return is_fresh


def body_ratio(c):
    try:
        return max((c["trade_price"] - c["opening_price"]) /
                   max(c["opening_price"], 1), 0)
    except Exception:
        return 0


# ---- 5분 컨텍스트: LRU 캐시 ----
class LRUCache:

    def __init__(self, maxsize=100):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def set(self, key, value):
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)

    def clear(self):
        with self.lock:
            self.cache.clear()

    def purge_older_than(self, max_age_sec=3.0):
        cutoff = int(time.time() * 1000) - int(max_age_sec * 1000)
        with self.lock:
            drop = [
                k for k, v in self.cache.items()
                if isinstance(v, dict) and v.get("ts", 0) < cutoff
            ]
            for k in drop:
                self.cache.pop(k, None)


_TICKS_CACHE = LRUCache(maxsize=100)
# _TICKS_TTL — L10에서 명시 import 완료 (import * 가 언더스코어 제외하므로)
_C5_CACHE = LRUCache(maxsize=300)
# v18f: c60 전역 캐시 (scan wall-clock 최적화)
# 60분봉은 3600s 주기 갱신 → TTL 300s도 실질 왜곡 거의 없음
# 대상: detect_leader 경로의 get_minutes_candles(60, m, 30) — 58 calls/cycle × 221ms ≈ 12.9s/cycle 절감 예상
_C60_CACHE = LRUCache(maxsize=300)
_C60_CACHE_TTL_MS = 300_000  # 300s


def _get_c60_cached(m, count=30):
    """c60 캐시 조회 (TTL 300s). detect_leader 경로 전용.
    miss 시 get_minutes_candles(60, m, count) 호출 후 캐시."""
    hit = _C60_CACHE.get(m)
    _now_ms = int(time.time() * 1000)
    if hit and (_now_ms - hit.get("ts", 0) <= _C60_CACHE_TTL_MS) and hit.get("count", 0) >= count:
        _pipeline_inc("c60_cache_hit")
        cached = hit["c"]
        # count가 더 많이 캐시된 경우 뒤쪽 count만 반환 (최신 캔들 포함)
        return cached[-count:] if len(cached) > count else cached
    _pipeline_inc("c60_cache_miss")
    c60 = get_minutes_candles(60, m, count) or []
    if c60:
        _C60_CACHE.set(m, {"ts": _now_ms, "c": c60, "count": count})
    return c60


# v18f: c15 전역 캐시 (scan wall-clock Phase B)
# 15분봉은 900s 주기 갱신 → TTL 60s면 주기 대비 7% 창 (매우 안전)
# 대상: detect_leader 경로의 get_minutes_candles(15, m, 50) — 59 calls/cycle × 224ms ≈ 13.0s/cycle 절감 예상
_C15_CACHE = LRUCache(maxsize=300)
_C15_CACHE_TTL_MS = 60_000  # 60s


def _get_c15_cached(m, count=50):
    """c15 캐시 조회 (TTL 60s). detect_leader 경로 전용."""
    hit = _C15_CACHE.get(m)
    _now_ms = int(time.time() * 1000)
    if hit and (_now_ms - hit.get("ts", 0) <= _C15_CACHE_TTL_MS) and hit.get("count", 0) >= count:
        _pipeline_inc("c15_cache_hit")
        cached = hit["c"]
        return cached[-count:] if len(cached) > count else cached
    _pipeline_inc("c15_cache_miss")
    c15 = get_minutes_candles(15, m, count) or []
    if c15:
        _C15_CACHE.set(m, {"ts": _now_ms, "c": c15, "count": count})
    return c15


def five_min_context_ok(m):
    if not USE_5M_CONTEXT:
        return True
    hit = _C5_CACHE.get(m)
    # 🔧 FIX: ms 단위 통일 (purge_older_than이 ms 기준 → 기존 초 단위면 매번 전부 삭제됨)
    _now_ms = int(time.time() * 1000)
    if hit and (_now_ms - hit.get("ts", 0) <= 3000):
        c5 = hit["c"]
    else:
        c5 = get_minutes_candles(5, m, 6)
        _C5_CACHE.set(m, {"ts": _now_ms, "c": c5})
    if len(c5) < 4:
        return True
    try:
        close = [c["trade_price"] for c in c5]
        slope3 = close[-1] - close[-3]
        recent_break = c5[-1]["high_price"] > max(x["high_price"]
                                                  for x in c5[-4:-1])
        return (slope3 > 0) or recent_break
    except Exception:
        return True

def get_dynamic_thresholds():
    h = now_kst().hour
    if 0 <= h < 6:
        return {
            "zscore": 0.90,   # 기존 0.95
            "vwap_gap": 0.0008,
            "uptick": 2,
            "min_change": 0.0004,  # 기존 0.0005
            "bidask_min": 1.06
        }
    elif 6 <= h < 12:
        return {
            "zscore": 0.95,   # 기존 1.0
            "vwap_gap": 0.0009,
            "uptick": 2,
            "min_change": 0.0006,  # 기존 0.0007
            "bidask_min": 1.07
        }
    elif 12 <= h < 18:
        return {
            "zscore": 0.95,   # 기존 1.0
            "vwap_gap": 0.0009,    # 기존 0.0010
            "uptick": 2,
            "min_change": 0.0008,  # 기존 0.0010
            "bidask_min": 1.075    # 살짝 완화
        }
    else:
        return {
            "zscore": 0.92,   # 기존 0.95
            "vwap_gap": 0.0009,
            "uptick": 2,
            "min_change": 0.0007,
            "bidask_min": 1.07
        }

# =========================
# ★ 장세/야간 완화 노브
# =========================
def relax_knob():
    """
    0.0 ~ 1.5 스케일.
    + BTC 5분 수익 > 0.6%면 +1.0, > 0.3%면 +0.5
    + 야간(00~06h)면 +0.5
    """
    try:
        b5 = btc_5m_change()
    except Exception:
        b5 = 0.0
    h = now_kst().hour
    f = 0.0
    if b5 >= 0.006: f += 1.0
    elif b5 >= 0.003: f += 0.5
    if 0 <= h < 6: f += 0.5
    return min(1.5, f)


# =========================
# 데이터 수집/캐시
# =========================
# MKTS_CACHE_TTL — config.py에서 정의됨
_MKTS_CACHE = {"ts": 0.0, "mkts": []}
_MKTS_CACHE_LOCK = threading.Lock()  # 🔧 FIX: TOCTOU 방어


def get_top_krw_by_24h(n=TOP_N):
    now = time.time()
    with _MKTS_CACHE_LOCK:
        if _MKTS_CACHE["mkts"] and (now - _MKTS_CACHE["ts"] <= MKTS_CACHE_TTL):
            return list(_MKTS_CACHE["mkts"][:n])  # 🔧 FIX: 복사본 반환 (락 밖 변경 방지)
    # 캐시 미스 → API 호출 (락 밖에서 실행 — 블로킹 방지)
    _raw_mkts = upbit_get("https://api.upbit.com/v1/market/all")
    allm = [
        d.get("market", "")  # 🔧 FIX: .get() 방어
        for d in (_raw_mkts if isinstance(_raw_mkts, list) else [])
        if d.get("market", "").startswith("KRW-")
    ]
    acc = []
    for i in range(0, len(allm), 50):
        info = upbit_get("https://api.upbit.com/v1/ticker",
                         {"markets": ",".join(allm[i:i + 50])})
        if not info: continue
        for t in info:
            v = t.get("acc_trade_price_24h", 0)
            if v > 0: acc.append((t["market"], v))
    acc.sort(key=lambda x: x[1], reverse=True)
    mkts = [m for m, _ in acc]
    with _MKTS_CACHE_LOCK:
        _MKTS_CACHE["mkts"] = mkts
        _MKTS_CACHE["ts"] = time.time()  # 🔧 FIX: API 완료 시점 기준
    return mkts[:n]


def get_minutes_candles(u, m, c):
    _t_gmc = time.time()
    js = upbit_get(f"https://api.upbit.com/v1/candles/minutes/{u}", {
        "market": m,
        "count": c
    },
                   timeout=6)
    _record_tagged_fetch(u, (time.time() - _t_gmc) * 1000)
    return list(reversed(js)) if js else []

def tick_ts_ms(t):
    """틱 타임스탬프 추출 (ms 단위, timestamp/ts 키 통일 + 초→ms 방어)"""
    ts = t.get("timestamp")
    if ts is None:
        ts = t.get("ts", 0)
    if ts and ts < 10_000_000_000:  # 10자리 = 초 단위 → ms 변환
        ts *= 1000
    return int(ts or 0)


def get_recent_ticks(m, c=100, allow_network=True):
    _MAX_TICKS = 100  # 🔧 FIX: 항상 최대치로 요청, 캐시에 최대치 저장
    now_ms = int(time.time() * 1000)
    hit = _TICKS_CACHE.get(m)
    if hit and (now_ms - hit["ts"] <= _TICKS_TTL * 1000):
        return hit["ticks"][:c]  # 🔧 요청 수만큼 slice 반환
    if not allow_network:
        return hit["ticks"][:c] if hit else []

    # ✅ 안전 래퍼로 변경 — 항상 최대치 요청 (캐시 재활용 극대화)
    js = safe_upbit_get("https://api.upbit.com/v1/trades/ticks", {
        "market": m,
        "count": _MAX_TICKS
    },
                        timeout=6)

    if not js or not isinstance(js, list):
        return hit["ticks"][:c] if hit else []
    # 🔧 FIX: tick_ts_ms 통일 (timestamp/ts 키 혼재 + 초/ms 방어)
    js_sorted = sorted(js, key=tick_ts_ms, reverse=True)
    _TICKS_CACHE.set(m, {"ts": now_ms, "ticks": js_sorted})
    return js_sorted[:c]  # 🔧 요청 수만큼 slice 반환

def micro_tape_stats_from_ticks(ticks, sec):
    if not ticks:
        return {
            "krw": 0,
            "n": 0,
            "buy_ratio": 0,
            "age": 999,
            "rate": 0,
            "krw_per_sec": 0
        }
    try:
        # 🔧 FIX: tick_ts_ms 헬퍼로 통일 (timestamp/ts 키 + 초→ms 방어)
        newest_ts = max(tick_ts_ms(t) for t in ticks)
        cutoff = newest_ts - sec * 1000
    except Exception:
        return {
            "krw": 0,
            "n": 0,
            "buy_ratio": 0,
            "age": 999,
            "rate": 0,
            "krw_per_sec": 0
        }

    n = 0
    krw = 0.0
    buys = 0
    oldest_ts = newest_ts
    for x in ticks:
        ts = tick_ts_ms(x)
        if ts < cutoff:
            continue
        p = x.get("trade_price", 0.0)
        v = x.get("trade_volume", 0.0)
        krw += p * v
        n += 1
        if x.get("ask_bid") == "BID": buys += 1
        if ts < oldest_ts: oldest_ts = ts

    if n == 0:
        return {
            "krw": 0,
            "n": 0,
            "buy_ratio": 0,
            "age": 999,
            "rate": 0,
            "krw_per_sec": 0
        }

    now_ms = int(time.time() * 1000)
    age = (now_ms - newest_ts) / 1000.0 if newest_ts else 999
    # 🔧 FIX: duration을 윈도우 크기(sec)로 사용 — 기존 틱 간 시간차 기반은 버스트 시 폭등
    # tick_span은 실제 틱 존재 구간, sec는 요청한 윈도우 크기
    tick_span = max((newest_ts - (oldest_ts or newest_ts)) / 1000.0, 1.0)
    duration = max(float(sec), tick_span)  # 윈도우 크기와 틱 span 중 큰 값 사용
    rate = n / duration
    krw_per_sec = krw / duration
    return {
        "krw": krw,
        "n": n,
        "buy_ratio": buys / n,
        "age": age,
        "rate": rate,
        "krw_per_sec": krw_per_sec
    }


def calc_consecutive_buys(ticks, sec=15):
    """
    체결강도: 최근 N초 내 연속 매수 체결 최대 횟수
    → 5개 이상 연속 매수 = 강한 신호
    """
    if not ticks:
        return 0
    try:
        newest_ts = max(tick_ts_ms(t) for t in ticks)
        cutoff = newest_ts - sec * 1000
    except Exception:
        return 0

    # 🔧 FIX: 윈도우 필터 + 시간순 정렬 (틱 순서 보장 → 연속 의미 정확)
    window = [t for t in ticks if tick_ts_ms(t) >= cutoff]
    window.sort(key=tick_ts_ms)

    max_streak = 0
    current_streak = 0
    for x in window:
        if x.get("ask_bid") == "BID":
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def calc_avg_krw_per_tick(t_stats):
    """
    틱당 평균금액: 총 거래대금 / 틱수
    → 높을수록 대형 체결 (고래 가능성)
    """
    if not t_stats or t_stats.get("n", 0) == 0:
        return 0
    return t_stats["krw"] / t_stats["n"]


def calc_flow_acceleration(ticks):
    """
    체결 가속도: (최근5초 raw krw) / (최근15초 raw krw의 5/15 비례분)
    → 1.5 이상 = 가속 중, 0.7 이하 = 감속 중
    🔧 FIX: krw_per_sec 사용 시 duration floor 비대칭(5 vs 15)으로 버스트 때 3x 편향
    → raw krw를 동일 비율로 비교하여 편향 제거
    """
    if not ticks:
        return 1.0
    t5s = micro_tape_stats_from_ticks(ticks, 5)
    t15s = micro_tape_stats_from_ticks(ticks, 15)

    # raw krw 비교: t5 구간이 t15의 1/3이면 비율 1.0이 기준
    krw_15 = t15s["krw"]
    if krw_15 <= 0:
        return 1.0
    # t5_krw / (t15_krw * 5/15) = t5_krw * 3 / t15_krw
    return (t5s["krw"] * 3.0) / krw_15


# ========================================
# 🚀 러닝 1분봉 (Running 1m Bar) - 종가 확정 전 실시간 계산
# ========================================
def running_1m_bar(ticks, last_candle=None):
    """
    틱 데이터로 현재 진행 중인 1분봉을 실시간 계산
    - 종가 확정 전에도 현재 가격/거래량/변동폭 파악 가능
    - last_candle이 있으면 이전 봉 기준으로 변동률 계산

    Returns: {
        'open': 시가,
        'high': 고가,
        'low': 저가,
        'close': 현재가 (진행 중),
        'volume_krw': 거래대금,
        'tick_count': 틱 수,
        'buy_ratio': 매수 비율,
        'change_from_prev': 이전봉 대비 변동률,
        'range_pct': 진행 중 봉의 변동폭 (high-low)/low
    }
    """
    if not ticks:
        return None

    # 🔧 FIX: tick_ts_ms 헬퍼로 통일 (서버 시간 드리프트 방지 + 키/단위 방어)
    newest_ts = max(tick_ts_ms(t) for t in ticks)
    if newest_ts == 0:
        newest_ts = int(time.time() * 1000)
    minute_start = (newest_ts // 60000) * 60000  # 최신 틱 기준 분의 시작 시점

    # 현재 분 내의 틱만 필터
    current_ticks = [t for t in ticks if tick_ts_ms(t) >= minute_start]

    if not current_ticks:
        # 현재 분 틱이 없으면 최근 10초 틱으로 대체
        fallback_cutoff = newest_ts - 10000
        current_ticks = [t for t in ticks if tick_ts_ms(t) >= fallback_cutoff]
        if not current_ticks:
            return None

    # 🔧 FIX: 정렬 전제 제거 — timestamp 기준 명시 정렬
    current_ticks = sorted(current_ticks, key=tick_ts_ms)
    prices = [t.get("trade_price", 0) for t in current_ticks if t.get("trade_price", 0) > 0]
    if not prices:
        return None

    # OHLC 계산 (timestamp 정렬 완료 → 첫=시가, 끝=종가)
    open_price = prices[0]
    high_price = max(prices)
    low_price = min(prices)
    close_price = prices[-1]  # 가장 최근 가격

    # 거래대금/매수비
    volume_krw = sum(t.get("trade_price", 0) * t.get("trade_volume", 0) for t in current_ticks)
    buys = sum(1 for t in current_ticks if t.get("ask_bid") == "BID")
    buy_ratio = buys / len(current_ticks) if current_ticks else 0

    # 이전봉 대비 변동률
    change_from_prev = 0.0
    if last_candle and last_candle.get("trade_price", 0) > 0:
        change_from_prev = close_price / last_candle["trade_price"] - 1

    # 진행 중 봉의 변동폭
    range_pct = (high_price - low_price) / low_price if low_price > 0 else 0

    return {
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume_krw": volume_krw,
        "tick_count": len(current_ticks),
        "buy_ratio": buy_ratio,
        "change_from_prev": change_from_prev,
        "range_pct": range_pct,
    }


# ========================================
# 🚀 Pre-break 동적 대역 (변동성 기반)
# ========================================
# (_PREBREAK_SUSPEND_UNTIL 제거됨 — PREBREAK_ENABLED=False이며 체크 코드 없었음)
# 🔧 FIX: _ENTRY_SUSPEND_UNTIL / _ENTRY_MAX_MODE → 상단(line ~527)으로 이동 (단일 선언)

def dynamic_prebreak_band(ticks):
    """
    분위기(가격밴드 표준편차, 야간 완화)에 따라 고점 근접 허용폭 자동 조절
    - 급등/휩쏘 장면에선 더 타이트
    - 조용하면 살짝 관대
    """
    # 10초 가격밴드 표준편차
    pstd = price_band_std(ticks, sec=10) if ticks else None
    pstd = pstd if pstd is not None else 0.0  # None 센티넬 처리
    # 기본 0.20% ± pstd*40% (상한 0.35%)
    base = PREBREAK_HIGH_PCT  # 0.002
    band = min(0.0035, base + pstd * 0.40)
    # 야간 살짝 완화
    if 0 <= now_kst().hour < 6:
        band = min(0.0038, band + 0.0004)
    return band


# ========================================
# 🚀 Pre-break Probe 체크 (고점 근처 선행 진입)
# ========================================
def _pct(a, b):
    try:
        return abs(a / b - 1.0)
    except Exception:
        return 9.9


def inter_arrival_stats(ticks, sec=30):
    """틱 도착간격 CV. 데이터 부족시 cv=None 반환 (센티넬 9.9 제거)"""
    if not ticks: return {"cv": None, "count": 0}
    try:
        # 🔧 FIX: tick_ts_ms 헬퍼로 통일
        newest_ts = max(tick_ts_ms(t) for t in ticks)
    except Exception:
        return {"cv": None, "count": 0}
    cutoff = newest_ts - sec * 1000
    ts = [tick_ts_ms(x) for x in ticks if tick_ts_ms(x) >= cutoff]
    ts = sorted(ts)
    if len(ts) < 4: return {"cv": None, "count": len(ts)}
    gaps = [(b - a) / 1000.0 for a, b in zip(ts, ts[1:])]
    mu = sum(gaps) / len(gaps)
    if mu <= 0: return {"cv": None, "count": len(ts)}
    var = sum((g - mu)**2 for g in gaps) / len(gaps)
    cv = (var**0.5) / mu
    return {"cv": cv, "count": len(ts)}


def price_band_std(ticks, sec=30):
    """가격밴드 표준편차. 데이터 부족시 None 반환 (센티넬 9.9 제거)"""
    if not ticks: return None
    try:
        # 🔧 FIX: tick_ts_ms 헬퍼로 통일
        newest_ts = max(tick_ts_ms(t) for t in ticks)
    except Exception:
        return None
    cutoff = newest_ts - sec * 1000
    ps = [x.get("trade_price", 0) for x in ticks if tick_ts_ms(x) >= cutoff]
    ps = [p for p in ps if p > 0]
    if len(ps) < 3: return None
    m = sum(ps) / len(ps)
    var = sum((p - m)**2 for p in ps) / len(ps)
    std = (var**0.5) / max(m, 1)
    return std


def _win_stats(ticks, start_s, end_s):
    if not ticks:
        return {"n": 0, "buy_ratio": 0.0, "rate": 0.0, "krw_per_sec": 0.0}
    try:
        # 🔧 FIX: tick_ts_ms 헬퍼로 통일
        newest_ts = max(tick_ts_ms(t) for t in ticks)
    except Exception:
        return {"n": 0, "buy_ratio": 0.0, "rate": 0.0, "krw_per_sec": 0.0}
    lo = newest_ts - end_s * 1000
    hi = newest_ts - start_s * 1000
    win = [x for x in ticks if lo <= tick_ts_ms(x) <= hi]
    if len(win) < 2:
        return {
            "n": len(win),
            "buy_ratio": 0.0,
            "rate": 0.0,
            "krw_per_sec": 0.0
        }
    win = sorted(win, key=tick_ts_ms)
    # 🔧 FIX: micro_tape_stats와 동일 — 윈도우 크기(end_s - start_s)도 고려
    tick_span = max((tick_ts_ms(win[-1]) - tick_ts_ms(win[0])) / 1000.0, 1.0)
    window_size = max(end_s - start_s, 1.0)
    dur = max(window_size, tick_span)
    buys = sum(1 for x in win if x.get("ask_bid") == "BID")
    krw = sum(x.get("trade_price", 0) * x.get("trade_volume", 0) for x in win)
    return {
        "n": len(win),
        "buy_ratio": buys / max(len(win), 1),
        "rate": len(win) / dur,
        "krw_per_sec": krw / dur
    }


def buy_decay_flag(ticks):
    """
    3단계 모멘텀 감쇄 감지 (개선):
    - Window 1 (20~30초 전): 초기 모멘텀
    - Window 2 (5~15초 전): 중간 상태
    - Window 3 (0~5초): 현재 상태
    → 3단계 연속 하락 패턴 감지 (단순 2-window 대비 정확도 향상)
    """
    w1 = _win_stats(ticks, start_s=20, end_s=30)  # 초기
    w2 = _win_stats(ticks, start_s=5, end_s=15)   # 중간
    w3 = _win_stats(ticks, start_s=0, end_s=5)    # 현재

    # 기존 호환 변수
    early = _win_stats(ticks, start_s=10, end_s=20)
    now = w3

    if early["n"] < 4 or now["n"] < 2:
        return False, {"early": early, "now": now}

    drop_buy = early["buy_ratio"] - now["buy_ratio"]

    # 기존 조건 (2-window)
    basic_decay = (drop_buy >= 0.12
                   and now["rate"] <= early["rate"] * 0.80
                   and now["krw_per_sec"] <= early["krw_per_sec"] * 0.70)

    # 3단계 연속 감쇄 (더 정확한 추세 역전 감지)
    cascade_decay = False
    if w1["n"] >= 3 and w2["n"] >= 3 and w3["n"] >= 2:
        # 매수비 연속 하락: w1 > w2 > w3
        buy_cascade = (w1["buy_ratio"] > w2["buy_ratio"] > w3["buy_ratio"])
        # 거래속도 연속 감소: w1 > w2 > w3
        flow_cascade = (w1["krw_per_sec"] > w2["krw_per_sec"] * 1.1
                        and w2["krw_per_sec"] > w3["krw_per_sec"] * 1.1)
        # 매수비 하락폭이 유의미 (총 10% 이상)
        total_drop = w1["buy_ratio"] - w3["buy_ratio"]
        cascade_decay = (buy_cascade and flow_cascade and total_drop >= 0.10)

    cond = basic_decay or cascade_decay
    return cond, {
        "early": early, "now": now, "drop_buy": drop_buy,
        "cascade": cascade_decay,
        "w1_buy": round(w1["buy_ratio"], 2) if w1["n"] > 0 else 0,
        "w2_buy": round(w2["buy_ratio"], 2) if w2["n"] > 0 else 0,
        "w3_buy": round(w3["buy_ratio"], 2) if w3["n"] > 0 else 0,
    }


# =========================
# 시장 필터
# =========================
def btc_5m_change():
    c = get_minutes_candles(5, "KRW-BTC", 3)
    if len(c) < 2: return 0.0
    return c[-1]["trade_price"] / max(c[-2]["trade_price"], 1) - 1

# === BTC 변동성 레짐 감지 (진입 품질 향상) ===
_BTC_REGIME_CACHE = {"regime": "normal", "ts": 0, "atr_pct": 0.0}
_BTC_REGIME_LOCK = threading.Lock()  # 🔧 FIX: 캐시 TOCTOU 방지

def btc_volatility_regime():
    """
    BTC 1분봉 ATR 기반 변동성 레짐 판단:
    - "calm"   : ATR < 0.08% → 낮은 변동성, 알트코인 모멘텀 유리 (공격적 진입)
    - "normal" : 0.08~0.25% → 표준 상태
    - "storm"  : ATR > 0.25% → 높은 변동성, 알트 연쇄 청산 위험 (보수적 진입)

    10초 캐시로 API 절약.
    Returns: (regime: str, atr_pct: float)
    """
    now = time.time()
    with _BTC_REGIME_LOCK:
        if now - _BTC_REGIME_CACHE["ts"] < 10:
            return _BTC_REGIME_CACHE["regime"], _BTC_REGIME_CACHE["atr_pct"]

    try:
        c1_btc = get_minutes_candles(1, "KRW-BTC", 20)
        if not c1_btc or len(c1_btc) < 15:
            return "normal", 0.0

        atr = atr14_from_candles(c1_btc, 14)
        if not atr or atr <= 0:
            return "normal", 0.0

        btc_price = c1_btc[-1].get("trade_price", 1)
        atr_pct = (atr / max(btc_price, 1)) * 100  # %로 환산

        if atr_pct < 0.08:
            regime = "calm"
        elif atr_pct > 0.25:
            regime = "storm"
        else:
            regime = "normal"

        with _BTC_REGIME_LOCK:
            # 🔧 FIX: time.time() 사용 (now는 API 호출 전에 캡처됨 → 캐시 만료 오차 방지)
            _BTC_REGIME_CACHE.update({"regime": regime, "ts": time.time(), "atr_pct": atr_pct})
        return regime, atr_pct

    except Exception:
        return "normal", 0.0

# =========================
# 보조: 캔들/ATR/EMA
# =========================
def ema_series(vals, period):
    if not vals: return []
    k = 2 / (period + 1)
    out = []
    ema = vals[0]
    for v in vals:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def ema_last(vals, period):
    if len(vals) == 0: return None
    return ema_series(vals, period)[-1]


# ============================================================
# ============ strategy_v4 통합 (WF 데이터 기반 진입 시그널) ============
# ============================================================
# 핵심 원칙:
# ============================================================
# 🔧 v6 리포트 기반 전략 (2026-03-15 signal_v4 2차 백테스트 결과)
#
# 핵심 발견 (60일 WF 7폴드 검증):
# - 거래량3배: WF PASS avgTE=+0.0919%, 양수폴드 71% (5/7)
# - 20봉_고점돌파: WF PASS avgTE=+0.0322%, 양수폴드 71% (5/7)
# - 15m_눌림반전: 단독 WF FAIL → 15m_MACD골든+1h_EMA정배열 필터 시 BOTH EV>0
# - EMA정배열진입: WF FAIL (43%), 복합필터도 TR/TE 일관성 없음 → 비활성화
# - 60m RSI 70+ = 과매수가 아니라 모멘텀 시작 (상한 제거 유지)
#
# 전략 구조 (v6.1 — GATE 신호별 분리):
# [SIGNAL] 진입 (OR — 먼저 매칭):
#   1순위: 거래량3배 — VR5>3.0 AND ATR%>0.7% AND 직전봉양봉
#     단독 WF PASS → GATE 불필요
#     WF: avgTE=+0.0919%, 71%, TRAIL SL0.7/A0.3/T0.2
#   2순위: 20봉_고점돌파 — 1m 20봉 고점 돌파
#     자체 복합필터: 5m_MACD골든 + 15m_ADX>25
#     WF: avgTE=+0.0322%, 71%, TRAIL SL0.7/A0.3/T0.2
#   3순위: 15m_눌림반전 — 15m 직전음봉+현재양봉+종가회복
#     단독 WF FAIL → GATE 필수 (15m_MACD골든+1h_EMA정배열+60m_RSI≥45)
#     복합: TR+0.11% TE+0.10% BOTH>0, TRAIL SL0.7/A0.3/T0.2
#
# [EXIT] 청산 (모두 TRAIL — 데이터 최적):
#   거래량3배: TRAIL SL0.7%/A0.3%/T0.2%
#   20봉_고점돌파: TRAIL SL0.7%/A0.3%/T0.2%
#   15m_눌림반전: TRAIL SL0.7%/A0.3%/T0.2% (HOLD→TRAIL 변경, 데이터 기반)
#
# 비활성 (WF FAIL / 데이터 미지지):
#   EMA정배열진입(WF43%), 5m_양봉, 15m_눌림+돌파, RSI과매도반등,
#   MACD골든(단독), 쌍바닥, 망치형반전, BB하단반등, 5m_큰양봉
# ============================================================

def _v4_calc_rsi(closes, period=14):
    """RSI 계산 (Wilder's smoothing)"""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _v4_ema(vals, period):
    """EMA 계산 — 마지막 값 반환"""
    if not vals or len(vals) < period:
        return None
    k = 2.0 / (period + 1)
    ema_val = vals[0]
    for v in vals[1:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val


def _v4_macd(closes, fast=12, slow=26, signal=9):
    """MACD 계산 — (macd_line, signal_line, histogram) 반환"""
    if len(closes) < slow + signal:
        return None, None, None
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)
    ef = closes[0]
    es = closes[0]
    macd_line_series = []
    for v in closes[1:]:
        ef = v * k_fast + ef * (1 - k_fast)
        es = v * k_slow + es * (1 - k_slow)
        macd_line_series.append(ef - es)
    if len(macd_line_series) < signal:
        return None, None, None
    sig = _v4_ema(macd_line_series[-signal * 2:], signal) if len(macd_line_series) >= signal * 2 else _v4_ema(macd_line_series, signal)
    macd_val = macd_line_series[-1]
    hist = macd_val - sig if sig is not None else None
    return macd_val, sig, hist


def _v4_adx(highs, lows, closes, period=14):
    """ADX 계산 — 단일 ADX 값 반환 (0~100)"""
    n = len(closes)
    if n < period * 2 + 1:
        return None
    # True Range, +DM, -DM
    tr_list = []
    plus_dm_list = []
    minus_dm_list = []
    for i in range(1, n):
        h = highs[i]
        l = lows[i]
        c_prev = closes[i - 1]
        tr_list.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm_list.append(up if up > dn and up > 0 else 0.0)
        minus_dm_list.append(dn if dn > up and dn > 0 else 0.0)
    # Wilder smoothing
    atr = sum(tr_list[:period])
    plus_dm_sum = sum(plus_dm_list[:period])
    minus_dm_sum = sum(minus_dm_list[:period])
    dx_list = []
    for i in range(period, len(tr_list)):
        atr = atr - atr / period + tr_list[i]
        plus_dm_sum = plus_dm_sum - plus_dm_sum / period + plus_dm_list[i]
        minus_dm_sum = minus_dm_sum - minus_dm_sum / period + minus_dm_list[i]
        if atr == 0:
            continue
        plus_di = 100 * plus_dm_sum / atr
        minus_di = 100 * minus_dm_sum / atr
        di_sum = plus_di + minus_di
        if di_sum == 0:
            continue
        dx_list.append(100 * abs(plus_di - minus_di) / di_sum)
    if len(dx_list) < period:
        return None
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period
    return adx


def _v4_volume_ratio_5(candles):
    """VR5: 현재봉 거래량 / 직전5봉 평균 거래량"""
    if len(candles) < 6:
        return 0.0
    cur_vol = candles[-1].get("candle_acc_trade_price", 0)
    past_vols = [c.get("candle_acc_trade_price", 0) for c in candles[-6:-1]]
    avg = sum(past_vols) / max(len(past_vols), 1)
    if avg <= 0:
        return 0.0
    return cur_vol / avg


def _v4_atr_pct(candles, period=14):
    """ATR% = ATR / 현재가 × 100"""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high_price"]
        l = candles[i]["low_price"]
        pc = candles[i - 1]["trade_price"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atr = sum(trs[-period:]) / period if len(trs) >= period else 0
    price = candles[-1]["trade_price"]
    if price <= 0:
        return 0.0
    return (atr / price) * 100


def _v4_is_bullish(candle):
    """양봉 여부"""
    return candle["trade_price"] > candle["opening_price"]


def _v4_rsi_from_candles(candles, period=14):
    """캔들 리스트에서 RSI 계산"""
    closes = [c["trade_price"] for c in candles]
    return _v4_calc_rsi(closes, period)


def _v4_ema_from_candles(candles, period):
    """캔들 리스트에서 EMA 마지막 값"""
    closes = [c["trade_price"] for c in candles]
    return _v4_ema(closes, period)


# === 공통 지표 수집 (모든 전략 교차분석용) ===
def _collect_universal_indicators(c1, c5, c15, c30, c60, market=None):
    """모든 전략 진입 시점에 호출 — 전체 지표를 한번에 수집.
    각 전략의 고유 indicators에 병합하여 W/L 교차분석 가능하게 함."""
    ui = {}
    # --- 1m 지표 ---
    if c1 and len(c1) >= 7:
        ui["vr5"] = round(_v4_volume_ratio_5(c1), 2)
        ui["atr_pct"] = round(_v4_atr_pct(c1, 14), 4)
    # --- 5m 지표 ---
    if c5 and len(c5) >= 15:
        rsi_5m = _v4_rsi_from_candles(c5, 14)
        if rsi_5m is not None:
            ui["rsi_5m"] = round(rsi_5m, 2)
        if len(c5) >= 35:
            closes_5m = [c["trade_price"] for c in c5]
            macd_5m, sig_5m, hist_5m = _v4_macd(closes_5m)
            price_5m = closes_5m[-1] if closes_5m[-1] > 0 else 1
            # MACD를 가격대비 bps(만분율)로 정규화 → 코인간 비교 가능
            if macd_5m is not None:
                ui["macd_5m_bps"] = round(macd_5m / price_5m * 10000, 2)
            if hist_5m is not None:
                ui["macd_hist_5m_bps"] = round(hist_5m / price_5m * 10000, 2)
    # --- 15m 지표 ---
    if c15 and len(c15) >= 30:
        highs_15 = [c["high_price"] for c in c15]
        lows_15 = [c["low_price"] for c in c15]
        closes_15 = [c["trade_price"] for c in c15]
        adx_15 = _v4_adx(highs_15, lows_15, closes_15, 14)
        if adx_15 is not None:
            ui["adx_15"] = round(adx_15, 2)
        rsi_15m = _v4_rsi_from_candles(c15, 14)
        if rsi_15m is not None:
            ui["rsi_15m"] = round(rsi_15m, 2)
        if len(c15) >= 35:
            macd_15, sig_15, hist_15 = _v4_macd(closes_15)
            price_15 = closes_15[-1] if closes_15[-1] > 0 else 1
            # MACD를 가격대비 bps(만분율)로 정규화
            if macd_15 is not None:
                ui["macd_15_bps"] = round(macd_15 / price_15 * 10000, 2)
            if hist_15 is not None:
                ui["macd_hist_15_bps"] = round(hist_15 / price_15 * 10000, 2)
            ema5_15 = _v4_ema_from_candles(c15, 5)
            ema10_15 = _v4_ema_from_candles(c15, 10)
            ema20_15 = _v4_ema_from_candles(c15, 20)
            if ema5_15 is not None and ema10_15 is not None and ema20_15 is not None:
                # EMA 정배열 정도: (ema5-ema20)/ema20 * 100 → 양수=정배열
                ui["ema_spread_15"] = round((ema5_15 - ema20_15) / max(ema20_15, 1) * 100, 4)
        # 15m VR5
        cur_vol_15 = c15[-1].get("candle_acc_trade_price", 0)
        past_vols_15 = [c.get("candle_acc_trade_price", 0) for c in c15[-6:-1]]
        avg_vol_15 = sum(past_vols_15) / max(len(past_vols_15), 1)
        if avg_vol_15 > 0:
            ui["vr5_15m"] = round(cur_vol_15 / avg_vol_15, 2)
    # --- 60m 지표 ---
    if c60 and len(c60) >= 15:
        rsi_60m = _v4_rsi_from_candles(c60, 14)
        if rsi_60m is not None:
            ui["rsi_60m"] = round(rsi_60m, 2)
        if len(c60) >= 20:
            ema5_60 = _v4_ema_from_candles(c60, 5)
            ema10_60 = _v4_ema_from_candles(c60, 10)
            ema20_60 = _v4_ema_from_candles(c60, 20)
            if ema5_60 is not None and ema10_60 is not None and ema20_60 is not None:
                ui["ema_spread_60"] = round((ema5_60 - ema20_60) / max(ema20_60, 1) * 100, 4)
        if len(c60) >= 30:
            highs_60 = [c["high_price"] for c in c60]
            lows_60 = [c["low_price"] for c in c60]
            closes_60 = [c["trade_price"] for c in c60]
            adx_60 = _v4_adx(highs_60, lows_60, closes_60, 14)
            if adx_60 is not None:
                ui["adx_60"] = round(adx_60, 2)
        # 60m 3봉 모멘텀
        if len(c60) >= 4:
            closes_60 = [c["trade_price"] for c in c60]
            m3 = (closes_60[-1] - closes_60[-4]) / max(closes_60[-4], 1)
            ui["m3_60m"] = round(m3 * 100, 4)
        # 60m 감싸기 비율
        if len(c60) >= 2:
            prev_60 = c60[-2]
            cur_60 = c60[-1]
            prev_body = abs(prev_60["opening_price"] - prev_60["trade_price"])
            cur_body = abs(cur_60["opening_price"] - cur_60["trade_price"])
            if prev_body > 0:
                ui["engulf_ratio_60"] = round(cur_body / prev_body, 2)
    # --- v18e: 진입 시점 스프레드/거래대금 ---
    if c1 and len(c1) >= 2:
        _cur = c1[-1]
        # 스프레드 추정: (고가-저가)/종가 (%)
        _hi = _cur.get("high_price", 0)
        _lo = _cur.get("low_price", 0)
        _cl = _cur.get("trade_price", 1)
        if _cl > 0 and _hi > _lo:
            ui["entry_spread_pct"] = round((_hi - _lo) / _cl * 100, 4)
        # 거래대금 (백만원)
        _vol_krw = _cur.get("candle_acc_trade_price", 0)
        ui["entry_vol_krw_m"] = round(_vol_krw / 1_000_000, 2)
    # --- 1m 추가: 20봉 gap ---
    if c1 and len(c1) >= 21:
        cur_close = c1[-1]["trade_price"]
        high_20 = max(c["high_price"] for c in c1[-21:-1])
        ui["gap_20bar"] = round(((cur_close / max(high_20, 1)) - 1.0) * 100, 4)
    # --- v18e: 틱 기반 지표 (초봉 대용) ---
    if market:
        try:
            # TTL 무관하게 캐시된 틱 직접 참조 (detect_leader_stock에서 이미 fetch 완료)
            _tick_hit = _TICKS_CACHE.get(market)
            _ticks = _tick_hit["ticks"][:200] if _tick_hit and _tick_hit.get("ticks") else []
            if _ticks and len(_ticks) >= 5:
                # 10초 테이프: 매수비, 체결속도, 거래대금/초
                t10 = micro_tape_stats_from_ticks(_ticks, 10)
                ui["tick_buy_10s"] = round(t10["buy_ratio"], 3)
                ui["tick_rate_10s"] = round(t10["rate"], 2)
                ui["tick_krw_ps_10s"] = round(t10["krw_per_sec"] / 1_000_000, 2)  # 백만원/초
                # 30초 테이프
                t30 = micro_tape_stats_from_ticks(_ticks, 30)
                ui["tick_buy_30s"] = round(t30["buy_ratio"], 3)
                ui["tick_rate_30s"] = round(t30["rate"], 2)
                ui["tick_krw_ps_30s"] = round(t30["krw_per_sec"] / 1_000_000, 2)
                # 60초 테이프
                t60 = micro_tape_stats_from_ticks(_ticks, 60)
                ui["tick_buy_60s"] = round(t60["buy_ratio"], 3)
                ui["tick_rate_60s"] = round(t60["rate"], 2)
                # 가격 모멘텀: 최근 10초/30초 가격 변화율
                newest_ts = max(tick_ts_ms(t) for t in _ticks)
                # 최신가: newest_ts 기준 틱 (순서 무관하게 안전)
                _p_now = max(_ticks, key=lambda t: tick_ts_ms(t)).get("trade_price", 0)
                for _sec, _label in [(10, "10s"), (30, "30s"), (60, "60s")]:
                    _cutoff = newest_ts - _sec * 1000
                    _old_ticks = [t for t in _ticks if tick_ts_ms(t) <= _cutoff]
                    if _old_ticks and _p_now > 0:
                        _p_old = max(_old_ticks, key=lambda t: tick_ts_ms(t)).get("trade_price", _p_now)
                        if _p_old > 0:
                            ui[f"tick_mom_{_label}"] = round((_p_now / _p_old - 1) * 100, 4)
                # 체결강도: 매수체결금액 / 매도체결금액 (30초)
                _buy_krw = 0
                _sell_krw = 0
                _cutoff_30 = newest_ts - 30_000
                for t in _ticks:
                    if tick_ts_ms(t) < _cutoff_30:
                        continue
                    _v = t.get("trade_price", 0) * t.get("trade_volume", 0)
                    if t.get("ask_bid") == "BID":
                        _buy_krw += _v
                    else:
                        _sell_krw += _v
                if _sell_krw > 0:
                    ui["tick_strength_30s"] = round(min(_buy_krw / _sell_krw, 50.0), 2)  # clamp: 매도0 근처 시 폭발 방지
                # 연속 매수 틱 수
                _consec = 0
                _max_consec = 0
                for t in reversed(_ticks):
                    if t.get("ask_bid") == "BID":
                        _consec += 1
                        _max_consec = max(_max_consec, _consec)
                    else:
                        _consec = 0
                ui["tick_consec_buy"] = _max_consec
                # 데이터 신선도 (초)
                ui["tick_age"] = round(t10["age"], 1)
        except Exception:
            pass
    return ui


# --- 60분봉 레짐 필터 (전 시그널 공통) ---
# 🔧 v5: RSI 상한 제거 (데이터: RSI 70+ 구간이 최고 수익)
# 거래량3배+15m_RSI70-100: EV +1.30%, PF 7.58
# 15m_눌림반전+60m_RSI70-100: EV +0.19%
# → RSI 70 = 과매수가 아닌 모멘텀 시작
def _v4_regime_filter_60m(c60):
    """60m RSI(14) ≥ 45 레짐 필터 (상한 제거)"""
    if not c60 or len(c60) < 15:
        return False, None
    rsi = _v4_rsi_from_candles(c60, 14)
    if rsi is None:
        return False, None
    return (rsi >= 45.0), rsi


# --- 60m 3봉 모멘텀 레짐 필터 (전 시그널 공통 사전필터) ---
# WF 섹션4 자동탐색: 60m_m3_상위33% TEST EV +0.24% (가장 강한 독립 피처)
# "상위 TF 모멘텀이 상승 중일 때만 진입" = 추세 방향 확인
def _v4_momentum_3bar_filter(c60, top_pct=0.33):
    """60m 3봉 모멘텀 상위 백분위 필터
    m3 = (close[i] - close[i-3]) / close[i-3]
    최근 12개 m3 값 중 상위 top_pct(33%) 이상이면 통과
    Returns: (pass_bool, current_m3, threshold)"""
    if not c60 or len(c60) < 7:
        return False, 0.0, 0.0
    closes = [c["trade_price"] for c in c60]
    # 사용 가능한 모든 윈도우에서 m3 계산
    m3_values = []
    for i in range(3, len(closes)):
        m3 = (closes[i] - closes[i - 3]) / closes[i - 3]
        m3_values.append(m3)
    if len(m3_values) < 4:
        return False, 0.0, 0.0
    current_m3 = m3_values[-1]
    # 상위 33% 임계값 결정 (rolling percentile)
    sorted_m3 = sorted(m3_values)
    threshold_idx = int(len(sorted_m3) * (1 - top_pct))
    threshold_idx = min(threshold_idx, len(sorted_m3) - 1)
    threshold = sorted_m3[threshold_idx]
    return current_m3 >= threshold, current_m3, threshold


# --- 공통 환경필터 GATE (15m_눌림반전 필수) ---
def _v4_gate_filter(c15, c60):
    """공통 환경필터: 60m RSI≥45 AND 1h EMA정배열 AND 15m MACD골든"""
    # 1) 60m RSI ≥ 45 (필수)
    regime_ok, rsi_60 = _v4_regime_filter_60m(c60)
    if not regime_ok:
        return False, None, "60m_RSI_low"

    # 2) 1h EMA 정배열 (EMA5 > EMA10 > EMA20)
    ema_aligned = False
    ema5_60 = ema10_60 = ema20_60 = None
    if c60 and len(c60) >= 20:
        ema5_60 = _v4_ema_from_candles(c60, 5)
        ema10_60 = _v4_ema_from_candles(c60, 10)
        ema20_60 = _v4_ema_from_candles(c60, 20)
        if ema5_60 is not None and ema10_60 is not None and ema20_60 is not None:
            ema_aligned = (ema5_60 > ema10_60 > ema20_60)

    # 3) 15m MACD 골든크로스 상태 (MACD > Signal)
    macd_golden = False
    macd_val = sig_val = None
    if c15 and len(c15) >= 35:
        closes_15 = [c["trade_price"] for c in c15]
        macd_val, sig_val, _ = _v4_macd(closes_15)
        if macd_val is not None and sig_val is not None:
            macd_golden = (macd_val > sig_val)

    # AND: EMA정배열 + MACD골든 둘 다 필요
    if not ema_aligned or not macd_golden:
        return False, None, "GATE_fail_EMA_or_MACD"

    _ema_str = f"1hEMA={ema5_60:.0f}>{ema10_60:.0f}>{ema20_60:.0f}" if ema_aligned else "1hEMA=X"
    _macd_str = f"15mMACD={macd_val:.4f}>{sig_val:.4f}" if macd_golden else "15mMACD=X"
    gate_info = f"60mRSI={rsi_60:.1f},{_ema_str},{_macd_str}"
    return True, gate_info, None


# --- 청산 파라미터 (v0: _V4_EXIT_PARAMS 제거 — 레지스트리 exit_params 사용) ---
_V4_EXIT_PARAMS = {}  # v0: 빈 dict — v4_get_exit_params fallback용

_V4_DEFAULT_EXIT = {
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 0.005,
    "trail_pct": 0.003,
    "hold_bars": 0,
    "max_bars": 60,
    "description": "DEFAULT_TRAIL_SL1.0/A0.5/T0.3",
}

# ========================================================================
# v0 초경량 시나리오 (v18 리셋)
# 각 전략: 핵심 트리거 2개 + 옵션 타이밍
# 튜닝 필터 0개 — 섀도우 데이터로 검증 후 하나씩 추가
# ========================================================================

_V0_EXIT_PARAMS = {
    "strategy": "TRAIL",
    "sl_pct": 0.007,
    "activation_pct": 0.003,
    "trail_pct": 0.002,
    "hold_bars": 0,
    "max_bars": 60,
    "description": "TRAIL_SL0.7/A0.3/T0.2",
}

_V0_EXIT_PARAMS_REVERSAL = {
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 0.005,
    "trail_pct": 0.003,
    "hold_bars": 0,
    "max_bars": 60,
    "description": "TRAIL_SL1.0/A0.5/T0.3",
}

# v18c: 시나리오별 엑시트 분리
_V0_EXIT_PARAMS_C = {  # C: 30s부터 양수 → 빠르게 잠그기
    "strategy": "TRAIL",
    "sl_pct": 0.004,
    "activation_pct": 0.002,
    "trail_pct": 0.0015,
    "hold_bars": 0,
    "max_bars": 60,
    "description": "C_TRAIL_SL0.4/A0.2/T0.15",
}

_V0_EXIT_PARAMS_MOMENTUM = {  # G-v2-A: min_hold 120s + 기존 trail + max 180s
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 0.003,
    "trail_pct": 0.002,
    "hold_bars": 0,
    "max_bars": 60,  # 60bars × 3s = 180s
    "description": "G-v2A_SL1.0/A0.3/T0.2/minH120s/max180s",
}

_V0_EXIT_PARAMS_MOMENTUM_B = {  # G-v2-B→G3: 중간값 trail (MFE +0.38%에서 0.8%는 과도, 0.5%로 조정)
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 0.005,  # 0.5% (0.3%=너무 빠름, 0.8%=도달불가, 중간)
    "trail_pct": 0.003,       # 0.3%
    "hold_bars": 0,
    "max_bars": 60,
    "description": "G3_SL1.0/A0.5/T0.3/minH120s/max180s",
}

_V0_EXIT_PARAMS_MOMENTUM_G4 = {  # G4: activation 0.4%, trail 0.25%
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 0.004,  # 0.4%
    "trail_pct": 0.0025,      # 0.25%
    "hold_bars": 0,
    "max_bars": 60,
    "description": "G4_SL1.0/A0.4/T0.25/minH120s/max180s",
}

_V0_EXIT_PARAMS_MOMENTUM_G6 = {  # G6: activation 0.6%, trail 0.35%
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 0.006,  # 0.6%
    "trail_pct": 0.0035,      # 0.35%
    "hold_bars": 0,
    "max_bars": 60,
    "description": "G6_SL1.0/A0.6/T0.35/minH120s/max180s",
}

_V0_EXIT_PARAMS_MOMENTUM_G7 = {  # G7: activation 0.7%, trail 0.4%
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 0.007,  # 0.7%
    "trail_pct": 0.004,       # 0.4%
    "hold_bars": 0,
    "max_bars": 60,
    "description": "G7_SL1.0/A0.7/T0.4/minH120s/max180s",
}

# GT 불변조건 (반드시 G와 동일):
#   entry: _v0_check_momentum_rsi (RSI≥74.55, 동일 함수)
#   sl_pct: 0.010 (G와 동일)
#   max_bars: 60 (180s, G와 동일)
#   min_hold: 120s (_is_g 분기로 G와 동일하게 적용)
# 변수 (GT 유일한 차이):
#   disable_trail: True → 트레일/본절 모두 우회, SL+타임아웃만 작동
#   activation_pct: 1.0 → 라이브 monitor의 dyn_checkpoint = max(_cost_floor, 1.0) = 1.0
#                         → trail_armed가 100% 수익에서만 활성 = 사실상 영구 비활성
#                         → 라이브 청산에서도 SL/max_bars만 작동 (shadow와 일치)
# 가설: trail이 alpha를 죽이고 있다 (180s 강제보유시 +0.31%p / +0.26%p 개선)
_V0_EXIT_PARAMS_MOMENTUM_GT = {
    "strategy": "TRAIL",
    "sl_pct": 0.010,           # G와 동일
    "activation_pct": 1.0,     # ⭐ 100% — 라이브 trail 영구 비활성 (shadow는 disable_trail 사용)
    "trail_pct": 0.005,        # 무관 (trail_armed 안 됨)
    "hold_bars": 0,
    "max_bars": 60,            # 180s, G와 동일
    "disable_trail": True,     # ⭐ shadow_sim_exit 분기용 (라이브는 activation 1.0으로 우회)
    "description": "GT_SL1.0/no-trail/max180s",
}

_V0_EXIT_PARAMS_MOMENTUM_GT_SL07 = {
    "strategy": "TRAIL",
    "sl_pct": 0.007,           # GT 1.0% → 0.7% (MAE -0.68% 기반, 빠른 손절이 나은지)
    "activation_pct": 1.0,
    "trail_pct": 0.005,
    "hold_bars": 0,
    "max_bars": 60,
    "disable_trail": True,
    "description": "GT_SL07_SL0.7/no-trail/max180s",
}

_V0_EXIT_PARAMS_MOMENTUM_GT_SL15 = {
    "strategy": "TRAIL",
    "sl_pct": 0.015,           # GT 1.0% → 1.5% (SL 히트 후 회복 여부 검증)
    "activation_pct": 1.0,
    "trail_pct": 0.005,
    "hold_bars": 0,
    "max_bars": 60,
    "disable_trail": True,
    "description": "GT_SL15_SL1.5/no-trail/max180s",
}

_V0_EXIT_PARAMS_MOMENTUM_GT_300s = {
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 1.0,
    "trail_pct": 0.005,
    "hold_bars": 0,
    "max_bars": 100,           # GT 60 (180s) → 100 (300s) — 180s 이후 alpha 지속 여부
    "disable_trail": True,
    "description": "GT_300s_SL1.0/no-trail/max300s",
}

_V0_EXIT_PARAMS_H_T180 = {
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 1.0,
    "trail_pct": 0.005,
    "hold_bars": 0,
    "max_bars": 60,
    "disable_trail": True,
    "description": "HT180_SL1.0/no-trail/max180s",
}

_V0_EXIT_PARAMS_SLOW = {  # L/B: 후반 양전 → 시간만 더
    "strategy": "TRAIL",
    "sl_pct": 0.007,
    "activation_pct": 0.003,
    "trail_pct": 0.002,
    "hold_bars": 0,
    "max_bars": 90,
    "description": "SLOW_TRAIL_SL0.7/A0.3/T0.2/90bar",
}

_V0_EXIT_PARAMS_K = {  # v18d: K 150s +0.09% 활용, 맥스바 90
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 0.005,
    "trail_pct": 0.003,
    "hold_bars": 0,
    "max_bars": 90,
    "description": "K_TRAIL_SL1.0/A0.5/T0.3/90bar",
}

_V0_EXIT_PARAMS_BREAKOUT = {  # B: V자 패턴, 되돌림 버텨야
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 0.004,
    "trail_pct": 0.003,
    "hold_bars": 0,
    "max_bars": 90,
    "description": "B_TRAIL_SL1.0/A0.4/T0.3/90bar",
}


def _v0_check_volume_burst(c1, c5, c15, c30, c60, gate_info=None):
    """A 거래량폭발: VR5≥2.5 + 양봉"""
    _pipeline_inc("vol_burst_enter")
    if not c1 or len(c1) < 7:
        return None
    vr5 = _v4_volume_ratio_5(c1)
    if vr5 < 2.5:
        if _pipeline_inc("vol_burst_vr5_fail", value=vr5, threshold=2.5, direction="gte"): return None
    # 양봉 체크
    _body_pct_a = ((c1[-1]["trade_price"] - c1[-1]["opening_price"]) / max(c1[-1]["opening_price"], 1)) * 100
    if not _v4_is_bullish(c1[-1]):
        if _pipeline_inc("vol_burst_bull_fail", value=round(_body_pct_a, 2), threshold=0, direction="gt"): return None
    # v18c: MACD 필터 제거 — 차단건 41% > 통과건 23%, 역효과 확정
    _pipeline_inc("vol_burst_pass")
    return {
        "signal_tag": "거래량폭발",
        "entry_mode": "confirm",
        "logic_group": "A",
        "filters_hit": [f"VR5={vr5:.1f}"],
        "exit_params": _V0_EXIT_PARAMS.copy(),
        "indicators": {"vr5": round(vr5, 2)},
    }


def _v0_check_price_breakout(c1, c5, c15, c30, c60, gate_info=None):
    """B 가격돌파: 종가>20봉고점 + 양봉"""
    _pipeline_inc("breakout_enter")
    if not c1 or len(c1) < 21:
        return None
    cur_close = c1[-1]["trade_price"]
    high_20 = max(c["high_price"] for c in c1[-21:-1])
    if cur_close <= high_20:
        gap_pct = ((cur_close / max(high_20, 1)) - 1.0) * 100
        if _pipeline_inc("breakout_price_fail", value=round(gap_pct, 2), threshold=0, direction="gt"): return None
    _body_pct_b = ((c1[-1]["trade_price"] - c1[-1]["opening_price"]) / max(c1[-1]["opening_price"], 1)) * 100
    if not _v4_is_bullish(c1[-1]):
        if _pipeline_inc("breakout_bull_fail", value=round(_body_pct_b, 2), threshold=0, direction="gt"): return None
    # v18: 15분봉 거래량 동반 체크 (W3.20 vs L0.77 → 4.2배 변별력)
    _vr5_15m_b = None
    if c15 and len(c15) >= 6:
        cur_vol_15 = c15[-1].get("candle_acc_trade_price", 0)
        past_vols_15 = [c.get("candle_acc_trade_price", 0) for c in c15[-6:-1]]
        avg_vol_15 = sum(past_vols_15) / max(len(past_vols_15), 1)
        if avg_vol_15 > 0:
            _vr5_15m_b = round(cur_vol_15 / avg_vol_15, 2)
            if _vr5_15m_b < 1.5:
                if _pipeline_inc("breakout_vr5_15m_fail", value=_vr5_15m_b, threshold=1.5, direction="gte"): return None
    _pipeline_inc("breakout_pass")
    gap_pct = ((cur_close / max(high_20, 1)) - 1.0) * 100
    return {
        "signal_tag": "가격돌파",
        "entry_mode": "confirm",
        "logic_group": "B",
        "filters_hit": [f"돌파={cur_close:.0f}>{high_20:.0f}", f"VR15={_vr5_15m_b}"],
        "exit_params": _V0_EXIT_PARAMS_BREAKOUT.copy(),
        "indicators": {"gap_20bar": round(gap_pct, 4)},
    }


def _v0_check_price_breakout_bullpass(c1, c5, c15, c30, c60, gate_info=None):
    """B2 가격돌파 (양봉 필터 제거): 종가>20봉고점 + 15mVR≥1.5"""
    _pipeline_inc("breakout_bp_enter")
    if not c1 or len(c1) < 21:
        return None
    cur_close = c1[-1]["trade_price"]
    high_20 = max(c["high_price"] for c in c1[-21:-1])
    if cur_close <= high_20:
        gap_pct = ((cur_close / max(high_20, 1)) - 1.0) * 100
        if _pipeline_inc("breakout_bp_price_fail", value=round(gap_pct, 2), threshold=0, direction="gt"): return None
    _vr5_15m_b = None
    if c15 and len(c15) >= 6:
        cur_vol_15 = c15[-1].get("candle_acc_trade_price", 0)
        past_vols_15 = [c.get("candle_acc_trade_price", 0) for c in c15[-6:-1]]
        avg_vol_15 = sum(past_vols_15) / max(len(past_vols_15), 1)
        if avg_vol_15 > 0:
            _vr5_15m_b = round(cur_vol_15 / avg_vol_15, 2)
            if _vr5_15m_b < 1.5:
                if _pipeline_inc("breakout_bp_vr5_15m_fail", value=_vr5_15m_b, threshold=1.5, direction="gte"): return None
    _pipeline_inc("breakout_bp_pass")
    gap_pct = ((cur_close / max(high_20, 1)) - 1.0) * 100
    return {
        "signal_tag": "가격돌파BP",
        "entry_mode": "confirm",
        "logic_group": "B2",
        "filters_hit": [f"돌파={cur_close:.0f}>{high_20:.0f}", f"VR15={_vr5_15m_b}"],
        "exit_params": _V0_EXIT_PARAMS_BREAKOUT.copy(),
        "indicators": {"gap_20bar": round(gap_pct, 4)},
    }


def _v0_check_pattern_reversal(c1, c5, c15, c30, c60, gate_info=None, tf="15m"):
    """C/H 패턴반전: {tf} 음→양 + 종가회복"""
    tag = "패턴반전_15m" if tf == "15m" else "패턴반전_60m"
    route = "C" if tf == "15m" else "H"
    pkey = f"reversal_{tf}"
    candles = c15 if tf == "15m" else c60
    _pipeline_inc(f"{pkey}_enter")
    if not candles or len(candles) < 3:
        return None
    prev, cur = candles[-2], candles[-1]
    # 직전봉 음봉 체크 — 몸통비율(%) 전달
    # v18e-tune2: C만 전봉양봉 허용 (고점이격 1.2% 이내 시)
    #   기존: 전봉양봉 → 무조건 탈락 (746/1044=71% 컷 → C 완전 비활성)
    #   수정: 전봉양봉이어도 고점 근접(1.2%이내)이면 "초반 반전" 허용
    prev_body_pct = ((prev["trade_price"] - prev["opening_price"]) / max(prev["opening_price"], 1)) * 100
    if prev["trade_price"] >= prev["opening_price"]:
        if route == "C" and c1 and len(c1) >= 21:
            _cur_close = c1[-1]["trade_price"]
            _high_20 = max(c["high_price"] for c in c1[-21:-1])
            _gap_from_high = ((_cur_close / max(_high_20, 1)) - 1.0) * 100
            if _gap_from_high < -1.2:
                # 고점 대비 1.2%+ 하락 → 약한 회복 → 전봉양봉 차단 유지
                if _pipeline_inc(f"{pkey}_prev_fail", value=round(prev_body_pct, 2), threshold=0, direction="lt"): return None
            # else: 고점 근접(-1.2%이내) → 전봉양봉이어도 허용 (상승 전환 초기)
        else:
            if _pipeline_inc(f"{pkey}_prev_fail", value=round(prev_body_pct, 2), threshold=0, direction="lt"): return None
    # 현재봉 양봉 체크 — 몸통비율(%) 전달
    cur_body_pct = ((cur["trade_price"] - cur["opening_price"]) / max(cur["opening_price"], 1)) * 100
    if cur["trade_price"] <= cur["opening_price"]:
        if _pipeline_inc(f"{pkey}_cur_fail", value=round(cur_body_pct, 2), threshold=0, direction="gt"): return None
    # 종가회복 — 회복gap(%) 전달
    recovery_gap = ((cur["trade_price"] / max(prev["opening_price"], 1)) - 1.0) * 100
    if cur["trade_price"] <= prev["opening_price"]:
        if _pipeline_inc(f"{pkey}_recovery_fail", value=round(recovery_gap, 2), threshold=0, direction="gt"): return None
    # v18e-tune2: C만 최소 회복 강도 요구 (한계 회복 제거)
    #   recovery_gap > 0이면 통과했지만, 0.001%~0.09% 같은 미미한 회복은 노이즈
    if route == "C" and recovery_gap < 0.1:
        if _pipeline_inc(f"{pkey}_recovery_weak_fail", value=round(recovery_gap, 2), threshold=0.1, direction="gte"): return None
    # (옵션) 1m 양봉 타이밍
    if c1 and len(c1) >= 1:
        _bp_rev = ((c1[-1]["trade_price"] - c1[-1]["opening_price"]) / max(c1[-1]["opening_price"], 1)) * 100
        if not _v4_is_bullish(c1[-1]):
            if _pipeline_inc(f"{pkey}_1m_fail", value=round(_bp_rev, 2), threshold=0, direction="gt"): return None
    # v18d: engulf 제거 (W3.35 vs L3.66 역전, 차단건 42% > 통과건 32% = 과적합)
    # → vr5_15m >= 1.0 추가 (W4.83 vs L1.31, 3.7배 변별력)
    if route == "C" and c15 and len(c15) >= 6:
        cur_vol_15 = c15[-1].get("candle_acc_trade_price", 0)
        past_vols_15 = [c.get("candle_acc_trade_price", 0) for c in c15[-6:-1]]
        avg_vol_15 = sum(past_vols_15) / max(len(past_vols_15), 1)
        if avg_vol_15 > 0:
            _vr5_15m_c = round(cur_vol_15 / avg_vol_15, 2)
            if _vr5_15m_c < 1.0:
                if _pipeline_inc(f"{pkey}_vr5_15m_fail", value=_vr5_15m_c, threshold=1.0, direction="gte"): return None
    # v18c: gap20 필터 — C만 적용, H는 제거 (H 차단건 46%+0.07% > 통과건 28%, 역효과)
    _gap20_rev = None
    if route == "C" and c1 and len(c1) >= 21:
        _cur_close_rev = c1[-1]["trade_price"]
        _high_20_rev = max(c["high_price"] for c in c1[-21:-1])
        _gap20_rev = round(((_cur_close_rev / max(_high_20_rev, 1)) - 1.0) * 100, 4)
        if _gap20_rev < -1.5:
            if _pipeline_inc(f"{pkey}_gap20_fail", value=_gap20_rev, threshold=-1.5, direction="gte"): return None
    _pipeline_inc(f"{pkey}_pass")
    return {
        "signal_tag": tag,
        "entry_mode": "confirm",
        "logic_group": route,
        "filters_hit": [f"{tf}음→양", f"회복{recovery_gap:+.2f}%", f"Gap20={_gap20_rev}"],
        "exit_params": (_V0_EXIT_PARAMS_C if route == "C" else _V0_EXIT_PARAMS_SLOW).copy(),
        "indicators": {"recovery_gap": round(recovery_gap, 2)},
    }


def _v0_check_reversal_15m(c1, c5, c15, c30, c60, gate_info=None):
    return _v0_check_pattern_reversal(c1, c5, c15, c30, c60, gate_info, tf="15m")


def _v0_check_reversal_60m(c1, c5, c15, c30, c60, gate_info=None):
    return _v0_check_pattern_reversal(c1, c5, c15, c30, c60, gate_info, tf="60m")


def _v0_check_ema_aligned(c1, c5, c15, c30, c60, gate_info=None, tf="15m"):
    """F/J 추세정배열: {tf} EMA5>10>20 + 양봉"""
    tag = "추세정배열_15m" if tf == "15m" else "추세정배열_60m"
    route = "F" if tf == "15m" else "J"
    pkey = f"ema_align_{tf}"
    candles = c15 if tf == "15m" else c60
    _pipeline_inc(f"{pkey}_enter")
    if not candles or len(candles) < 20:
        return None
    if not c1 or len(c1) < 1:
        return None
    ema5 = _v4_ema_from_candles(candles, 5)
    ema10 = _v4_ema_from_candles(candles, 10)
    ema20 = _v4_ema_from_candles(candles, 20)
    if not all(v is not None for v in [ema5, ema10, ema20]):
        return None
    # EMA 스프레드(%) — 정배열이면 양수, 역배열이면 음수
    spread = round((ema5 - ema20) / max(ema20, 1) * 100, 4)
    if not (ema5 > ema10 > ema20):
        if _pipeline_inc(f"{pkey}_ema_fail", value=spread, threshold=0, direction="gt"): return None
    _bp_ema = ((c1[-1]["trade_price"] - c1[-1]["opening_price"]) / max(c1[-1]["opening_price"], 1)) * 100
    if not _v4_is_bullish(c1[-1]):
        if _pipeline_inc(f"{pkey}_1m_fail", value=round(_bp_ema, 2), threshold=0, direction="gt"): return None
    _pipeline_inc(f"{pkey}_pass")
    return {
        "signal_tag": tag,
        "entry_mode": "confirm",
        "logic_group": route,
        "filters_hit": [f"{tf}EMA={ema5:.0f}>{ema10:.0f}>{ema20:.0f}"],
        "exit_params": _V0_EXIT_PARAMS.copy(),
        "indicators": {f"ema_spread_{tf}": spread},
    }


def _v0_check_ema_15m(c1, c5, c15, c30, c60, gate_info=None):
    return _v0_check_ema_aligned(c1, c5, c15, c30, c60, gate_info, tf="15m")


def _v0_check_ema_60m(c1, c5, c15, c30, c60, gate_info=None):
    return _v0_check_ema_aligned(c1, c5, c15, c30, c60, gate_info, tf="60m")


def _v0_check_momentum_rsi_strict(c1, c5, c15, c30, c60, gate_info=None):
    """GR 모멘텀(strict): G와 모든 조건 동일하나 5m RSI 임계치만 74.55 → 78
    - 자동탐지 추천(78.28에서 G/G2 모두 +8%p) 반영
    - exit는 _V0_EXIT_PARAMS_MOMENTUM (G와 동일) 고정
    - 별도 pipeline_key("momentum_gr_*")로 카운터 분리
    - shadow-only, route="GR" → stats 키 "GR:모멘텀GR"로 G/G2/GT와 완전 분리"""
    _pipeline_inc("momentum_gr_enter")
    if not c5 or len(c5) < 15:
        return None
    rsi_5m = _v4_rsi_from_candles(c5, 14)
    # GR: RSI 임계치 74.55 → 78 (G의 유일한 차이점)
    if rsi_5m is None or rsi_5m < 78.0:
        if _pipeline_inc("momentum_gr_rsi5_fail", value=rsi_5m, threshold=78.0, direction="gte"): return None
    if not c1 or not _v4_is_bullish(c1[-1]):
        _bp_gr = ((c1[-1]["trade_price"] - c1[-1]["opening_price"]) / max(c1[-1]["opening_price"], 1)) * 100 if c1 else 0
        if _pipeline_inc("momentum_gr_1m_fail", value=round(_bp_gr, 2), threshold=0, direction="gt"): return None
    _vr5_1m_gr = _v4_volume_ratio_5(c1) if c1 and len(c1) >= 7 else None
    if _vr5_1m_gr is not None and _vr5_1m_gr > 3.2:
        if _pipeline_inc("momentum_gr_vr5_over_fail", value=round(_vr5_1m_gr, 2), threshold=3.2, direction="lte"): return None
    _vr5_15m_gr = None
    if c15 and len(c15) >= 6:
        cur_vol_15 = c15[-1].get("candle_acc_trade_price", 0)
        past_vols_15 = [c.get("candle_acc_trade_price", 0) for c in c15[-6:-1]]
        avg_vol_15 = sum(past_vols_15) / max(len(past_vols_15), 1)
        if avg_vol_15 > 0:
            _vr5_15m_gr = round(cur_vol_15 / avg_vol_15, 2)
            if _vr5_15m_gr < 2.0:
                if _pipeline_inc("momentum_gr_vr5_15m_fail", value=_vr5_15m_gr, threshold=2.0, direction="gte"): return None
    _pipeline_inc("momentum_gr_pass")
    return {
        "signal_tag": "모멘텀GR",
        "entry_mode": "confirm",
        "logic_group": "GR",
        "filters_hit": [f"5mRSI={rsi_5m:.1f}(strict)", f"VR5={_vr5_1m_gr}", f"VR15={_vr5_15m_gr}"],
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM.copy(),
        "indicators": {"rsi_5m": round(rsi_5m, 1)},
    }


def _v0_check_momentum_rsi(c1, c5, c15, c30, c60, gate_info=None):
    """G 모멘텀: 5m RSI 71~100 + 양봉 + 1mVR≤3.0"""
    _pipeline_inc("momentum_enter")
    if not c5 or len(c5) < 15:
        return None
    rsi_5m = _v4_rsi_from_candles(c5, 14)
    # v18d: RSI 68→71로 조이기 (71.4에서 89건 29% +4%p)
    # v18e-tune: RSI 71→74.55 (136건 46% +0.11%, 234건 38% -0.05% 대비 +0.16%p)
    # v18e-tune2: RSI 74.55→73.0 (100% 차단 해소 — 시스템 idle 방지)
    if rsi_5m is None or rsi_5m < 73.0:
        if _pipeline_inc("momentum_rsi5_fail", value=rsi_5m, threshold=73.0, direction="gte"): return None
    if not c1 or not _v4_is_bullish(c1[-1]):
        _bp_g = ((c1[-1]["trade_price"] - c1[-1]["opening_price"]) / max(c1[-1]["opening_price"], 1)) * 100 if c1 else 0
        if _pipeline_inc("momentum_1m_fail", value=round(_bp_g, 2), threshold=0, direction="gt"): return None
    # v18c: 1분봉 VR5 상한 (W1.31 vs L4.67 — L이 3.6배! 과열 추격 차단)
    _vr5_1m_g = _v4_volume_ratio_5(c1) if c1 and len(c1) >= 7 else None
    # v18e: VR5 상한 3.0→3.2 (차단 3건=100% Win +1.51%, 리스크 최소)
    if _vr5_1m_g is not None and _vr5_1m_g > 3.2:
        if _pipeline_inc("momentum_vr5_over_fail", value=round(_vr5_1m_g, 2), threshold=3.2, direction="lte"): return None
    # v18: 15분봉 거래량 동반 체크 (W4.42 vs L1.51 → 2.9배 변별력)
    _vr5_15m_g = None
    if c15 and len(c15) >= 6:
        cur_vol_15 = c15[-1].get("candle_acc_trade_price", 0)
        past_vols_15 = [c.get("candle_acc_trade_price", 0) for c in c15[-6:-1]]
        avg_vol_15 = sum(past_vols_15) / max(len(past_vols_15), 1)
        if avg_vol_15 > 0:
            _vr5_15m_g = round(cur_vol_15 / avg_vol_15, 2)
            if _vr5_15m_g < 2.0:
                if _pipeline_inc("momentum_vr5_15m_fail", value=_vr5_15m_g, threshold=2.0, direction="gte"): return None
    _pipeline_inc("momentum_pass")
    return {
        "signal_tag": "모멘텀",
        "entry_mode": "confirm",
        "logic_group": "G",
        "filters_hit": [f"5mRSI={rsi_5m:.1f}", f"VR5={_vr5_1m_g}", f"VR15={_vr5_15m_g}"],
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM.copy(),
        "indicators": {"rsi_5m": round(rsi_5m, 1)},
    }


def _make_momentum_rsi_variant(rsi_min, pkey):
    """GT shadow variant factory — RSI 임계값만 바꿔서 shadow 비교용"""
    def _check(c1, c5, c15, c30, c60, gate_info=None):
        _pipeline_inc(f"{pkey}_enter")
        if not c5 or len(c5) < 15:
            return None
        rsi_5m = _v4_rsi_from_candles(c5, 14)
        if rsi_5m is None or rsi_5m < rsi_min:
            if _pipeline_inc(f"{pkey}_rsi5_fail", value=rsi_5m, threshold=rsi_min, direction="gte"): return None
        if not c1 or not _v4_is_bullish(c1[-1]):
            _bp = ((c1[-1]["trade_price"] - c1[-1]["opening_price"]) / max(c1[-1]["opening_price"], 1)) * 100 if c1 else 0
            if _pipeline_inc(f"{pkey}_1m_fail", value=round(_bp, 2), threshold=0, direction="gt"): return None
        _vr5 = _v4_volume_ratio_5(c1) if c1 and len(c1) >= 7 else None
        if _vr5 is not None and _vr5 > 3.2:
            if _pipeline_inc(f"{pkey}_vr5_over_fail", value=round(_vr5, 2), threshold=3.2, direction="lte"): return None
        _vr15 = None
        if c15 and len(c15) >= 6:
            cur_vol = c15[-1].get("candle_acc_trade_price", 0)
            past = [c.get("candle_acc_trade_price", 0) for c in c15[-6:-1]]
            avg = sum(past) / max(len(past), 1)
            if avg > 0:
                _vr15 = round(cur_vol / avg, 2)
                if _vr15 < 2.0:
                    if _pipeline_inc(f"{pkey}_vr5_15m_fail", value=_vr15, threshold=2.0, direction="gte"): return None
        _pipeline_inc(f"{pkey}_pass")
        return {
            "signal_tag": "모멘텀",
            "entry_mode": "confirm",
            "logic_group": "G",
            "filters_hit": [f"5mRSI={rsi_5m:.1f}", f"VR5={_vr5}", f"VR15={_vr15}"],
            "exit_params": _V0_EXIT_PARAMS_MOMENTUM.copy(),
            "indicators": {"rsi_5m": round(rsi_5m, 1)},
        }
    return _check

_v0_check_momentum_rsi_70 = _make_momentum_rsi_variant(70.0, "momentum_70")
_v0_check_momentum_rsi_68 = _make_momentum_rsi_variant(68.0, "momentum_68")


def _v0_check_trend_strength(c1, c5, c15, c30, c60, gate_info=None):
    """L 추세강도: 15m ADX≥28.5 + 양봉"""
    _pipeline_inc("adx_trend_enter")
    if not c15 or len(c15) < 30:
        return None
    highs = [c["high_price"] for c in c15]
    lows = [c["low_price"] for c in c15]
    closes = [c["trade_price"] for c in c15]
    adx_15 = _v4_adx(highs, lows, closes, 14)
    # v18c: ADX 30→28.5로 풀기 (신규20건 45% 승률, 전체30%보다 좋음)
    if adx_15 is None or adx_15 < 28.5:
        if _pipeline_inc("adx_trend_15_fail", value=adx_15, threshold=28.5, direction="gte"): return None
    if not c1 or not _v4_is_bullish(c1[-1]):
        _bp_l = ((c1[-1]["trade_price"] - c1[-1]["opening_price"]) / max(c1[-1]["opening_price"], 1)) * 100 if c1 else 0
        if _pipeline_inc("adx_trend_1m_fail", value=round(_bp_l, 2), threshold=0, direction="gt"): return None
    # v18: 15분봉 거래량 동반 체크 (W1.44 vs L0.66 → 2.2배 변별력)
    _vr5_15m_l = None
    if c15 and len(c15) >= 6:
        cur_vol_15 = c15[-1].get("candle_acc_trade_price", 0)
        past_vols_15 = [c.get("candle_acc_trade_price", 0) for c in c15[-6:-1]]
        avg_vol_15 = sum(past_vols_15) / max(len(past_vols_15), 1)
        if avg_vol_15 > 0:
            _vr5_15m_l = round(cur_vol_15 / avg_vol_15, 2)
            if _vr5_15m_l < 0.8:
                if _pipeline_inc("adx_trend_vr5_15m_fail", value=_vr5_15m_l, threshold=0.8, direction="gte"): return None
    _pipeline_inc("adx_trend_pass")
    return {
        "signal_tag": "추세강도",
        "entry_mode": "confirm",
        "logic_group": "L",
        "filters_hit": [f"15mADX={adx_15:.1f}", f"VR15={_vr5_15m_l}"],
        "exit_params": _V0_EXIT_PARAMS_SLOW.copy(),
        "indicators": {"adx_15": round(adx_15, 1)},
    }


def _v0_check_oversold_bounce(c1, c5, c15, c30, c60, gate_info=None):
    """K 역추세반등: 5m RSI≤35 + 음→양"""
    _pipeline_inc("oversold_enter")
    if not c5 or len(c5) < 15:
        return None
    rsi_5m = _v4_rsi_from_candles(c5, 14)
    if rsi_5m is None or rsi_5m > 35:
        if _pipeline_inc("oversold_rsi_fail", value=rsi_5m, threshold=35, direction="lte"): return None
    # 5m 현재봉 양봉
    _bp_k5 = ((c5[-1]["trade_price"] - c5[-1]["opening_price"]) / max(c5[-1]["opening_price"], 1)) * 100
    if not _v4_is_bullish(c5[-1]):
        if _pipeline_inc("oversold_5m_bull_fail", value=round(_bp_k5, 2), threshold=0, direction="gt"): return None
    # 5m 직전봉 음봉
    if len(c5) >= 2:
        _bp_k5p = ((c5[-2]["trade_price"] - c5[-2]["opening_price"]) / max(c5[-2]["opening_price"], 1)) * 100
        if _v4_is_bullish(c5[-2]):
            if _pipeline_inc("oversold_5m_prev_fail", value=round(_bp_k5p, 2), threshold=0, direction="lt"): return None
    # (옵션) 1m 양봉
    _bp_k1 = ((c1[-1]["trade_price"] - c1[-1]["opening_price"]) / max(c1[-1]["opening_price"], 1)) * 100 if c1 and len(c1) >= 1 else 0
    if c1 and len(c1) >= 1 and not _v4_is_bullish(c1[-1]):
        if _pipeline_inc("oversold_1m_fail", value=round(_bp_k1, 2), threshold=0, direction="gt"): return None
    # v18: 60분봉 감싸기 비율 체크 (W2.96 vs L1.67 → 1.8배 변별력)
    _engulf_k = None
    if c60 and len(c60) >= 2:
        prev_body_k = abs(c60[-2]["opening_price"] - c60[-2]["trade_price"])
        cur_body_k = abs(c60[-1]["opening_price"] - c60[-1]["trade_price"])
        if prev_body_k > 0:
            _engulf_k = round(cur_body_k / prev_body_k, 2)
            # v18d: engulf 2.0→2.1 조이기 (62건 44% +3%p)
            if _engulf_k < 2.1:
                if _pipeline_inc("oversold_engulf_fail", value=_engulf_k, threshold=2.1, direction="gte"): return None
    # v18e: gap_20bar 필터 제거 — 차단 39건 44% +0.12%, 역효과 확인
    # if c1 and len(c1) >= 21:
    #     ...if _gap_20_k < -3.0: return None
    _pipeline_inc("oversold_pass")
    return {
        "signal_tag": "역추세반등",
        "entry_mode": "confirm",
        "logic_group": "K",
        "filters_hit": [f"5mRSI={rsi_5m:.1f}", "5m음→양", f"Engulf={_engulf_k}"],
        "exit_params": _V0_EXIT_PARAMS_K.copy(),
        "indicators": {"rsi_5m": round(rsi_5m, 1)},
    }


def _v0_check_near_high(c1, c5, c15, c30, c60, gate_info=None):
    """D 고점근접: 20봉고점 대비 -1% 이내 + 양봉"""
    _pipeline_inc("near_high_enter")
    if not c1 or len(c1) < 21:
        return None
    cur_close = c1[-1]["trade_price"]
    high_20 = max(c["high_price"] for c in c1[-21:-1])
    gap_pct = ((cur_close / max(high_20, 1)) - 1.0) * 100
    # gap_20bar >= -1.0 (고점 대비 1% 이내)
    if gap_pct < -1.0:
        if _pipeline_inc("near_high_gap_fail", value=round(gap_pct, 4), threshold=-1.0, direction="gte"): return None
    # 이미 돌파한 건 B가 담당 → 여기선 제외 (gap > 0)
    if gap_pct > 0:
        if _pipeline_inc("near_high_over_fail"): return None
    # 양봉 체크
    _body_pct_d = ((c1[-1]["trade_price"] - c1[-1]["opening_price"]) / max(c1[-1]["opening_price"], 1)) * 100
    if not _v4_is_bullish(c1[-1]):
        if _pipeline_inc("near_high_bull_fail", value=round(_body_pct_d, 2), threshold=0, direction="gt"): return None
    _pipeline_inc("near_high_pass")
    return {
        "signal_tag": "고점근접",
        "entry_mode": "confirm",
        "logic_group": "D",
        "filters_hit": [f"Gap20={gap_pct:+.2f}%"],
        "exit_params": _V0_EXIT_PARAMS.copy(),
        "indicators": {"gap_20bar": round(gap_pct, 4)},
    }


# --- v0 전략 레지스트리 (초경량 — 섀도우 전용) ---
# 우선순위: B > D > A > C > H > F > G > L > K
_STRATEGY_REGISTRY = {
    "가격돌파": {
        "check_fn": _v0_check_price_breakout,
        "exit_params": _V0_EXIT_PARAMS_BREAKOUT,
        "priority": 1,
        "enabled": False,
        "pipeline_key": "breakout",
        "route": "B",
        "description": "종가>20봉고점 + 양봉",
    },
    # v18c: D(고점근접) 비활성화 — 5782건 24%, W/L 변별력 전무, MFE 0.09%
    # "고점근접": {
    #     "check_fn": _v0_check_near_high,
    #     "exit_params": _V0_EXIT_PARAMS,
    #     "priority": 2,
    #     "enabled": False,
    #     "pipeline_key": "near_high",
    #     "route": "D",
    #     "description": "20봉고점 -1%이내 + 양봉",
    # },
    # v18e: A(거래량폭발) 비활성화 — 944건 27% PnL-0.05%, MFE+0.19%, W/L 분포 완전 겹침, 개선 불가
    # "거래량폭발": {
    #     "check_fn": _v0_check_volume_burst,
    #     "exit_params": _V0_EXIT_PARAMS,
    #     "priority": 2,
    #     "enabled": False,
    #     "pipeline_key": "vol_burst",
    #     "route": "A",
    #     "description": "VR5≥2.5 + 양봉",
    # },
    "패턴반전_15m": {
        "check_fn": _v0_check_reversal_15m,
        "exit_params": _V0_EXIT_PARAMS_C,
        "priority": 3,
        "enabled": False,  # ⬇ 라이브 비활성 (shadow -0.04%, 33% WR → edge 없음. GT 집중)
        "pipeline_key": "reversal_15m",
        "route": "C",
        "description": "15m 음→양 + 종가회복",
    },
    "패턴반전_60m": {
        "check_fn": _v0_check_reversal_60m,
        "exit_params": _V0_EXIT_PARAMS_SLOW,
        "priority": 4,
        "enabled": False,
        "pipeline_key": "reversal_60m",
        "route": "H",
        "description": "60m 음→양 + 종가회복",
    },
    # v18e: F(추세정배열_15m) 비활성화 — 1905건 27% PnL-0.10%, MFE+0.12%, 전체 최악. 리소스 낭비
    # "추세정배열_15m": {
    #     "check_fn": _v0_check_ema_15m,
    #     "exit_params": _V0_EXIT_PARAMS,
    #     "priority": 5,
    #     "enabled": False,
    #     "pipeline_key": "ema_align_15m",
    #     "route": "F",
    #     "description": "15m EMA5>10>20 + 양봉",
    # },
    # v18: J(추세정배열_60m) 제거 — 4432건 31% PnL-0.10% 최악, 환경필터 전환 검토
    # "추세정배열_60m": {
    #     "check_fn": _v0_check_ema_60m,
    #     "exit_params": _V0_EXIT_PARAMS,
    #     "priority": 6,
    #     "enabled": False,
    #     "pipeline_key": "ema_align_60m",
    #     "route": "J",
    #     "description": "60m EMA5>10>20 + 양봉",
    # },
    "모멘텀": {
        "check_fn": _v0_check_momentum_rsi,
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM,
        "priority": 7,
        "enabled": False,  # ⬇ shadow only — GT 메인 승격 (B-1)으로 인해 비활성화
        "pipeline_key": "momentum",
        "route": "G",
        "description": "5mRSI≥74.55 + 양봉 + VR5≤3.2 [G-v2:minH120s] (shadow)",
    },
    "모멘텀B": {  # G2→G3: activation 0.5%, trail 0.3% (0.8%는 MFE 대비 과도 확인)
        "check_fn": _v0_check_momentum_rsi,
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM_B,
        "priority": 7,
        "enabled": False,  # shadow-only
        "pipeline_key": "momentum",
        "route": "G2",
        "description": "5mRSI≥74.55 [G3:A0.5/T0.3/minH120s]",
    },
    "모멘텀G4": {  # G4: activation 0.4%, trail 0.25% (G~G2 중간)
        "check_fn": _v0_check_momentum_rsi,
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM_G4,
        "priority": 7,
        "enabled": False,  # shadow-only
        "pipeline_key": "momentum",
        "route": "G4",
        "description": "5mRSI≥74.55 [G4:A0.4/T0.25/minH120s]",
    },
    "모멘텀G6": {  # G6: activation 0.6%, trail 0.35%
        "check_fn": _v0_check_momentum_rsi,
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM_G6,
        "priority": 7,
        "enabled": False,  # shadow-only
        "pipeline_key": "momentum",
        "route": "G6",
        "description": "5mRSI≥74.55 [G6:A0.6/T0.35/minH120s]",
    },
    "모멘텀G7": {  # G7: activation 0.7%, trail 0.4%
        "check_fn": _v0_check_momentum_rsi,
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM_G7,
        "priority": 7,
        "enabled": False,  # shadow-only
        "pipeline_key": "momentum",
        "route": "G7",
        "description": "5mRSI≥74.55 [G7:A0.7/T0.4/minH120s]",
    },
    "모멘텀GT": {  # GT: trail 제거 — entry/SL/hold 모두 G와 동일, trail만 OFF
        "check_fn": _v0_check_momentum_rsi,  # ⭐ G와 완전 동일한 entry
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM_GT,
        "priority": 7,
        "enabled": True,  # ⬆ B-1 메인 승격 (GT 71건 +0.11% vs G4/G6/G7 +0.02~0.04%)
        "pipeline_key": "momentum",
        "route": "GT",
        "description": "5mRSI≥73.0 [GT:no-trail/max180s] (LIVE)",
    },
    "모멘텀GR": {  # GR: entry 강화 — RSI 74.55 → 78, exit는 G와 동일 고정
        "check_fn": _v0_check_momentum_rsi_strict,  # ⭐ rsi 78 별도 함수 (G 영향 없음)
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM,    # ⭐ G와 동일 exit (변수는 entry만)
        "priority": 7,
        "enabled": False,  # shadow-only
        "pipeline_key": "momentum_gr",
        "route": "GR",
        "description": "5mRSI≥78 [GR:G와 동일 exit, entry만 강화]",
    },
    "모멘텀GT70": {
        "check_fn": _v0_check_momentum_rsi_70,
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM_GT,
        "priority": 7,
        "enabled": False,  # shadow-only
        "pipeline_key": "momentum_70",
        "route": "GT70",
        "description": "5mRSI≥70 [GT70:no-trail/max180s] (shadow — 진입 앞당김 실험)",
    },
    "모멘텀GT68": {
        "check_fn": _v0_check_momentum_rsi_68,
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM_GT,
        "priority": 7,
        "enabled": False,  # shadow-only
        "pipeline_key": "momentum_68",
        "route": "GT68",
        "description": "5mRSI≥68 [GT68:no-trail/max180s] (shadow — 진입 앞당김 실험)",
    },
    "모멘텀GT_SL07": {
        "check_fn": _v0_check_momentum_rsi,
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM_GT_SL07,
        "priority": 7,
        "enabled": False,  # shadow-only (SL 1.0→0.7 타이트화 실험)
        "pipeline_key": "momentum",
        "route": "GT_SL07",
        "description": "5mRSI≥73.0 [GT_SL07:SL0.7/no-trail/max180s] (shadow — SL 타이트)",
    },
    "모멘텀GT_SL15": {
        "check_fn": _v0_check_momentum_rsi,
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM_GT_SL15,
        "priority": 7,
        "enabled": False,  # shadow-only (SL 1.0→1.5 완화 실험)
        "pipeline_key": "momentum",
        "route": "GT_SL15",
        "description": "5mRSI≥73.0 [GT_SL15:SL1.5/no-trail/max180s] (shadow — SL 완화)",
    },
    "모멘텀GT_300s": {
        "check_fn": _v0_check_momentum_rsi,
        "exit_params": _V0_EXIT_PARAMS_MOMENTUM_GT_300s,
        "priority": 7,
        "enabled": False,  # shadow-only (max 180→300s 연장 실험)
        "pipeline_key": "momentum",
        "route": "GT_300s",
        "description": "5mRSI≥73.0 [GT_300s:SL1.0/no-trail/max300s] (shadow — 시간 연장)",
    },
    "돌파B2": {
        "check_fn": _v0_check_price_breakout_bullpass,
        "exit_params": _V0_EXIT_PARAMS_BREAKOUT,
        "priority": 1,
        "enabled": False,  # shadow-only (양봉 필터 제거 실험)
        "pipeline_key": "breakout_bp",
        "route": "B2",
        "description": "종가>20봉고점 + 15mVR≥1.5 (양봉 필터 제거, shadow)",
    },
    "반전60m_T180": {
        "check_fn": _v0_check_reversal_60m,
        "exit_params": _V0_EXIT_PARAMS_H_T180,
        "priority": 4,
        "enabled": False,  # shadow-only (GT식 exit 적용 실험)
        "pipeline_key": "reversal_60m",
        "route": "HT",
        "description": "60m 음→양 + 종가회복 [HT:GT식 no-trail/max180s] (shadow)",
    },
    "추세강도": {
        "check_fn": _v0_check_trend_strength,
        "exit_params": _V0_EXIT_PARAMS_SLOW,
        "priority": 8,
        "enabled": False,
        "pipeline_key": "adx_trend",
        "route": "L",
        "description": "15mADX≥28.5 + 양봉",
    },
    # v18e: K(역추세반등) 비활성화 — 전구간 마이너스, 시간축으로도 안 살아남음
    # "역추세반등": {
    #     "check_fn": _v0_check_oversold_bounce,
    #     "exit_params": _V0_EXIT_PARAMS_K,
    #     "priority": 9,
    #     "enabled": True,
    #     "pipeline_key": "oversold",
    #     "route": "K",
    #     "description": "5mRSI≤35 + 음→양",
    # },
}

# === v9: 섀도우 가상매매 + 실제 청산 로직 시뮬레이션 ===
_SHADOW_LOCK = threading.Lock()
# 가상포지션: { route, strat, market, entry_price, entry_ts,
#               best_price, trail_armed, trail_stop, exit_params, bars, exit_reason }
_SHADOW_VIRTUAL_POSITIONS = []
_SHADOW_DEDUP = {}  # { "route_market": last_entry_ts }
_SHADOW_PNL_SNAP_SECS = [5, 10, 15, 20, 25, 30, 60, 90, 120, 150, 180, 240, 300]  # v18e: 초반 5초 단위 추가, +240/300 (GT_300s 검증)

# 섀도우 전용 check_fn 매핑 (v0: 불필요 — 모든 전략이 직접 로직 보유)
_SHADOW_CHECK_OVERRIDES = {}

# 누적 성과 통계
_SHADOW_PERF_STATS = {}
_SHADOW_PERF_LOCK = threading.Lock()
_SHADOW_TRADE_COUNT = 0

# 차단 건 가상 추적 (counterfactual tracking)
# 필터에 걸려 차단된 시그널도 가상 포지션으로 추적 → 필터 효과 검증
_SHADOW_BLOCKED_POSITIONS = []
_SHADOW_BLOCKED_DEDUP = {}  # { "route_market": last_entry_ts }
_SHADOW_BLOCKED_STATS = {}
_SHADOW_BLOCKED_TRADE_COUNT = 0


def _load_shadow_stats():
    """봇 시작 시 저장된 섀도우 성과 통계 로드"""
    global _SHADOW_PERF_STATS, _SHADOW_TRADE_COUNT
    try:
        if os.path.exists(SHADOW_STATS_PATH):
            with open(SHADOW_STATS_PATH, "r", encoding="utf-8") as f:
                _SHADOW_PERF_STATS = json.load(f)
            # 🔧 마이그레이션: 누락 필드 보완 (이전 버전 호환)
            for key, s in _SHADOW_PERF_STATS.items():
                if "mfes" not in s:
                    s["mfes"] = []
                if "hold_secs" not in s:
                    s["hold_secs"] = []
                if "coins" not in s:
                    s["coins"] = []
                if "exit_reasons" not in s:
                    s["exit_reasons"] = {}
                if "pnls" not in s:
                    s["pnls"] = []
                # 마이그레이션: 불필요 필드 정리
                for _old_key in ("avg_mfe", "avg_hold", "mfe_n",
                                 "win_avg_mfe", "win_avg_hold", "win_avg_pnl",
                                 "loss_avg_mfe", "loss_avg_hold", "loss_avg_pnl"):
                    s.pop(_old_key, None)
                if "win_ind_avg" not in s:
                    old_win = s.pop("win_indicators", [])
                    s["win_ind_avg"], s["win_ind_cnt"] = _calc_ind_avg(old_win)
                if "loss_ind_avg" not in s:
                    old_loss = s.pop("loss_indicators", [])
                    s["loss_ind_avg"], s["loss_ind_cnt"] = _calc_ind_avg(old_loss)
                # v13 마이그레이션: Welford 분산 + MAE + PnL 곡선 필드 보장
                for _f, _d in (("win_ind_m2", {}), ("loss_ind_m2", {}),
                               ("mae_sum", 0.0), ("mae_cnt", 0),
                               ("pnl_curve_sum", {}), ("pnl_curve_cnt", {})):
                    if _f not in s:
                        s[_f] = _d
            # v12 1회성 리셋: F/H/I에 W/L 임계치 필터 추가로 기존 데이터 무효
            # F: ema_spread_60>=1.0 (W1.36/L0.82, 1887건), H: macd_15_bps>=10 (W18.5/L1.3, 137건), I: macd_15_bps>=15 (W23.3/L6.8, 125건)
            _v12_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v12_filters_reset_done")
            if not os.path.exists(_v12_marker):
                print("[SHADOW_STATS] v12 1회 초기화: F(ema_spread_60≥1.0) H(macd_15_bps≥10) I(macd_15_bps≥15) 필터 추가 → 전체 리셋")
                print("[SHADOW_STATS] 근거:")
                print("[SHADOW_STATS]   F:EMA정배열 1887건 — ema_spread_60 W1.36(493건)/L0.82(1394건) +66% → 60m추세 확실할때만 진입")
                print("[SHADOW_STATS]   H:60m감싸기 137건 — macd_15_bps W18.47(35건)/L1.31(102건) 14배 → 모멘텀 동반 감싸기만 진입")
                print("[SHADOW_STATS]   I:15mVR폭발 125건 — macd_15_bps W23.29(34건)/L6.84(91건) 3.4배 → 모멘텀 동반 VR폭발만 진입")
                _SHADOW_PERF_STATS = {}
                try:
                    with open(_v12_marker, "w") as f:
                        f.write("v12 filters reset done\n")
                except Exception:
                    pass
            # v13 1회성 리셋: J/I/D에 W/L 임계치 필터 추가로 기존 데이터 무효
            # J: vr5_15m<=6 (W3.52/L5.24, 146건), I: engulf_ratio_60>=3.0 (W4.21/L1.98, 98건), D: vr5_15m>=1.5 (W2.85/L0.82, 87건)
            _v13_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v13_filters_reset_done")
            if not os.path.exists(_v13_marker):
                print("[SHADOW_STATS] v13 1회 초기화: J(vr5_15m≤6) I(engulf_ratio_60≥3.0) D(vr5_15m≥1.5) 필터 추가 → 전체 리셋")
                print("[SHADOW_STATS] 근거:")
                print("[SHADOW_STATS]   J:상위TF정배열 146건 — vr5_15m W3.52/L5.24 (3.3x) → 과열VR 제외")
                print("[SHADOW_STATS]   I:15mVR폭발 98건 — engulf_ratio_60 W4.21/L1.98 (2.1x) → 60m감싸기 동반만")
                print("[SHADOW_STATS]   D:거래량완화 87건 — vr5_15m W2.85/L0.82 (3.5x) → 15m거래량 뒷받침 필수")
                _SHADOW_PERF_STATS = {}
                try:
                    with open(_v13_marker, "w") as f:
                        f.write("v13 filters reset done\n")
                except Exception:
                    pass
            # v14 1회성 리셋: 차단 건 가상 추적(counterfactual) 도입 → 기존 데이터 전체 초기화
            _v14_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v14_blocked_tracking_reset_done")
            if not os.path.exists(_v14_marker):
                print("[SHADOW_STATS] v14 초기화: 차단 건 가상 추적 도입 → 섀도우 + 차단 통계 전체 리셋")
                _SHADOW_PERF_STATS = {}
                try:
                    # 차단 통계 파일도 초기화
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v14_marker, "w") as f:
                        f.write("v14 blocked tracking reset done\n")
                except Exception:
                    pass
            # v15 1회성 리셋: 재시작 알림 연결 + 차단 PnL curve 수집 보강 → 전체 초기화
            _v15_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v15_alerts_reset_done")
            if not os.path.exists(_v15_marker):
                print("[SHADOW_STATS] v15 초기화: 재시작 알림 연결 + 차단 PnL curve 보강 → 섀도우 + 차단 통계 전체 리셋")
                _SHADOW_PERF_STATS = {}
                try:
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v15_marker, "w") as f:
                        f.write("v15 alerts + blocked pnl_curve reset done\n")
                except Exception:
                    pass
            # v16 1회성 리셋: threshold sweep (fail_values) 필드 추가 → 차단 통계 초기화
            _v16_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v16_threshold_sweep_reset_done")
            if not os.path.exists(_v16_marker):
                print("[SHADOW_STATS] v16 초기화: threshold sweep 필드 추가 → 차단 통계 리셋")
                try:
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v16_marker, "w") as f:
                        f.write("v16 threshold sweep reset done\n")
                except Exception:
                    pass
            # v0 1회성 리셋: 전체 전략 초경량 리셋 → 기존 통계 전부 초기화
            _v0_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v0_full_reset_done")
            if not os.path.exists(_v0_marker):
                print("[SHADOW_STATS] v0 전체 리셋: 초경량 시나리오 전환 → 모든 통계 초기화")
                try:
                    _SHADOW_PERF_STATS = {}
                    if os.path.exists(SHADOW_STATS_PATH):
                        os.remove(SHADOW_STATS_PATH)
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v0_marker, "w") as f:
                        f.write("v0 full strategy reset done\n")
                except Exception:
                    pass
            # v18 전체 리셋: 7개 필터 추가 + trade_records + recovery_gap + J 제거
            _v18_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18_filters_reset_done")
            if not os.path.exists(_v18_marker):
                print("[SHADOW_STATS] v18 전체 리셋: 필터 추가(A/B/C/G/H/K/L) + J 제거 + trade_records")
                try:
                    _SHADOW_PERF_STATS = {}
                    if os.path.exists(SHADOW_STATS_PATH):
                        os.remove(SHADOW_STATS_PATH)
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v18_marker, "w") as f:
                        f.write("v18 full reset: new filters + J removal + trade_records\n")
                except Exception:
                    pass
            # v18b 강제 리셋: 💡sweep 전체건 합산 반영
            _v18b_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18b_sweep_reset_done")
            if not os.path.exists(_v18b_marker):
                print("[SHADOW_STATS] v18b 강제 리셋: sweep 전체건 합산 + 포맷 변경 반영")
                try:
                    _SHADOW_PERF_STATS = {}
                    if os.path.exists(SHADOW_STATS_PATH):
                        os.remove(SHADOW_STATS_PATH)
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v18b_marker, "w") as f:
                        f.write("v18b sweep reset done\n")
                except Exception:
                    pass
            # v18c 강제 리셋: D비활성+A MACD제거+H gap20제거+C engulf+G상한/RSI68+L ADX28.5+엑시트분리
            _v18c_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18c_exit_split_reset_done")
            if not os.path.exists(_v18c_marker):
                print("[SHADOW_STATS] v18c 전체 리셋: 엑시트 분리 + 필터 재조정 + D비활성화")
                try:
                    _SHADOW_PERF_STATS = {}
                    if os.path.exists(SHADOW_STATS_PATH):
                        os.remove(SHADOW_STATS_PATH)
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v18c_marker, "w") as f:
                        f.write("v18c exit split + filter readjust reset done\n")
                except Exception:
                    pass
            # v18e-tune: G SL1.0 + K gap20 + tick_strength clamp → G/K/L만 초기화
            _v18e_tune_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18e_tune_gkl_reset_done")
            if not os.path.exists(_v18e_tune_marker):
                print("[SHADOW_STATS] v18e-tune: G SL1.0 + K gap20 필터 → G/K/L 통계 초기화")
                try:
                    for _prefix in ("G:", "K:", "L:"):
                        _del_keys = [k for k in _SHADOW_PERF_STATS if k.startswith(_prefix)]
                        for k in _del_keys:
                            del _SHADOW_PERF_STATS[k]
                    with open(_v18e_tune_marker, "w") as f:
                        f.write("v18e tune: G SL1.0, K gap20>=3, tick_strength clamp50\n")
                    _save_shadow_stats()
                except Exception:
                    pass
            # v18e-tune2: G RSI74.55 + G 조기탈출60s + K gap제거 → G/K만 초기화
            _v18e_tune2_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18e_tune2_gk_reset_done")
            if not os.path.exists(_v18e_tune2_marker):
                print("[SHADOW_STATS] v18e-tune2: G RSI74.55+조기탈출 + K gap제거 → G/K 초기화")
                try:
                    for _prefix in ("G:", "K:"):
                        _del_keys = [k for k in _SHADOW_PERF_STATS if k.startswith(_prefix)]
                        for k in _del_keys:
                            del _SHADOW_PERF_STATS[k]
                    with open(_v18e_tune2_marker, "w") as f:
                        f.write("v18e tune2: G RSI74.55+early_exit60s, K gap20 removed\n")
                    _save_shadow_stats()
                except Exception:
                    pass
            # G-v2: G exit 재설계 + K 비활성화 → G만 초기화 (K는 레지스트리에서 제거됨)
            _gv2_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".gv2_exit_redesign_done")
            if not os.path.exists(_gv2_marker):
                print("[SHADOW_STATS] G-v2: exit 재설계(min_hold120s+max180s) + K 비활성화 → G 초기화")
                try:
                    _del_keys = [k for k in _SHADOW_PERF_STATS if k.startswith("G:")]
                    for k in _del_keys:
                        del _SHADOW_PERF_STATS[k]
                    with open(_gv2_marker, "w") as f:
                        f.write("G-v2: min_hold 120s, trail after 120s, max 180s, K disabled\n")
                    _save_shadow_stats()
                except Exception:
                    pass
            # G3: G2 activation 0.8→0.5 교체 → G2 통계만 초기화
            _g3_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".g3_activation_adjust_done")
            if not os.path.exists(_g3_marker):
                print("[SHADOW_STATS] G3: G2 activation 0.8→0.5 교체 → G2 초기화")
                try:
                    _del_keys = [k for k in _SHADOW_PERF_STATS if k.startswith("G2:")]
                    for k in _del_keys:
                        del _SHADOW_PERF_STATS[k]
                    with open(_g3_marker, "w") as f:
                        f.write("G3: activation 0.5, trail 0.3 (was 0.8/0.4)\n")
                    _save_shadow_stats()
                except Exception:
                    pass
            # K-cleanup: 비활성 K 잔존 stats 제거 (registry에서 제거됐는데 stats에 잔존)
            _k_cleanup_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".k_stats_cleanup_done")
            if not os.path.exists(_k_cleanup_marker):
                print("[SHADOW_STATS] K-cleanup: 비활성 K 잔존 stats 제거")
                try:
                    _del_keys = [k for k in _SHADOW_PERF_STATS if k.startswith("K:")]
                    for k in _del_keys:
                        del _SHADOW_PERF_STATS[k]
                    with open(_k_cleanup_marker, "w") as f:
                        f.write("K stats cleanup: removed stale K entries\n")
                    _save_shadow_stats()
                except Exception:
                    pass
            # v18e-final: ATR동적 비활성 + K/G 라이브 + 고정값 복원 → 전체 초기화
            _v18e_final_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18e_final_fixed_exit_reset_done")
            if not os.path.exists(_v18e_final_marker):
                print("[SHADOW_STATS] v18e-final: ATR동적 비활성 + K/G 라이브 → 전체 초기화")
                try:
                    _SHADOW_PERF_STATS = {}
                    if os.path.exists(SHADOW_STATS_PATH):
                        os.remove(SHADOW_STATS_PATH)
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v18e_final_marker, "w") as f:
                        f.write("v18e final: ATR off, K/G live, fixed exit params\n")
                except Exception:
                    pass
            # v18e-fix: G pullback 제거 → 전체 통계 초기화 (깔끔하게 재시작)
            _v18e_g_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18e_g_pullback_remove_v2_done")
            _v18e_g_need_blocked_cleanup = False
            if not os.path.exists(_v18e_g_marker):
                print("[SHADOW_STATS] v18e-fix: G pullback 제거 → 전체 통계 초기화")
                try:
                    _SHADOW_PERF_STATS = {}
                    if os.path.exists(SHADOW_STATS_PATH):
                        os.remove(SHADOW_STATS_PATH)
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v18e_g_marker, "w") as f:
                        f.write("v18e G pullback removed, full stats reset\n")
                except Exception:
                    pass
            # v18e: ATR 동적 엑시트 + B/G pullback entry + 분포 겹침 경고
            _v18e_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18e_atr_pullback_reset_done")
            if not os.path.exists(_v18e_marker):
                print("[SHADOW_STATS] v18e 리셋: ATR 동적 엑시트 + B/G pullback + 분포 겹침 경고")
                try:
                    _SHADOW_PERF_STATS = {}
                    if os.path.exists(SHADOW_STATS_PATH):
                        os.remove(SHADOW_STATS_PATH)
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v18e_marker, "w") as f:
                        f.write("v18e atr dynamic exit + B/G pullback + overlap warning + A/F off + G vr5 3.2 reset done\n")
                except Exception:
                    pass
            # v18d: C engulf→vr5_15m + G RSI71/SL원복 + K engulf2.1/90바 + H 90바
            _v18d_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18d_filter_tune_reset_done")
            if not os.path.exists(_v18d_marker):
                print("[SHADOW_STATS] v18d 리셋: C vr5_15m + G RSI71/SL원복 + K engulf2.1 + H/K 90바")
                try:
                    _SHADOW_PERF_STATS = {}
                    if os.path.exists(SHADOW_STATS_PATH):
                        os.remove(SHADOW_STATS_PATH)
                    if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
                        os.remove(SHADOW_BLOCKED_STATS_PATH)
                    with open(_v18d_marker, "w") as f:
                        f.write("v18d filter tune reset done\n")
                except Exception:
                    pass
            _SHADOW_TRADE_COUNT = sum(s.get("signals", 0) for s in _SHADOW_PERF_STATS.values())
            print(f"[SHADOW_STATS] 로드 완료: {len(_SHADOW_PERF_STATS)}개 루트, 총 {_SHADOW_TRADE_COUNT}건")
    except Exception as e:
        print(f"[SHADOW_STATS] 로드 실패: {e}")
        _SHADOW_PERF_STATS = {}
    # 차단 건 통계 로드
    _load_blocked_stats()
    # v18e-tune: blocked에서도 G/K/L 제거 (blocked 로드 후, 1회성)
    _v18e_tune_blocked_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18e_tune_gkl_blocked_done")
    if not os.path.exists(_v18e_tune_blocked_marker):
        try:
            for _prefix in ("G:", "K:", "L:"):
                _del_keys = [k for k in _SHADOW_BLOCKED_STATS if k.startswith(_prefix)]
                for k in _del_keys:
                    del _SHADOW_BLOCKED_STATS[k]
            with open(_v18e_tune_blocked_marker, "w") as f:
                f.write("v18e tune blocked G/K/L reset done\n")
            _save_shadow_stats()
            print("[SHADOW_STATS] v18e-tune: G/K/L blocked 통계 정리 완료")
        except Exception:
            pass
    # v18e-tune2: blocked에서도 G/K 제거
    _v18e_tune2_blocked_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".v18e_tune2_gk_blocked_done")
    if not os.path.exists(_v18e_tune2_blocked_marker):
        try:
            for _prefix in ("G:", "K:"):
                _del_keys = [k for k in _SHADOW_BLOCKED_STATS if k.startswith(_prefix)]
                for k in _del_keys:
                    del _SHADOW_BLOCKED_STATS[k]
            with open(_v18e_tune2_blocked_marker, "w") as f:
                f.write("v18e tune2 blocked G/K reset done\n")
            _save_shadow_stats()
            print("[SHADOW_STATS] v18e-tune2: G/K blocked 통계 정리 완료")
        except Exception:
            pass
    # G-v2: blocked에서도 G 정리
    _gv2_blocked_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".gv2_blocked_done")
    if not os.path.exists(_gv2_blocked_marker):
        try:
            _del_keys = [k for k in _SHADOW_BLOCKED_STATS if k.startswith("G:")]
            for k in _del_keys:
                del _SHADOW_BLOCKED_STATS[k]
            with open(_gv2_blocked_marker, "w") as f:
                f.write("G-v2 blocked reset done\n")
            _save_shadow_stats()
            print("[SHADOW_STATS] G-v2: G blocked 통계 정리 완료")
        except Exception:
            pass
    # G3: blocked에서 G2 정리
    _g3_blocked_marker = os.path.join(os.path.dirname(SHADOW_STATS_PATH), ".g3_blocked_done")
    if not os.path.exists(_g3_blocked_marker):
        try:
            _del_keys = [k for k in _SHADOW_BLOCKED_STATS if k.startswith("G2:")]
            for k in _del_keys:
                del _SHADOW_BLOCKED_STATS[k]
            with open(_g3_blocked_marker, "w") as f:
                f.write("G3 blocked G2 reset done\n")
            _save_shadow_stats()
            print("[SHADOW_STATS] G3: G2 blocked 통계 정리 완료")
        except Exception:
            pass


def _save_shadow_stats():
    """섀도우 성과 통계 파일 저장 (일반 + 차단 건 동시 저장)"""
    with _SHADOW_PERF_LOCK:
        data = copy.deepcopy(_SHADOW_PERF_STATS)
    try:
        tmp = SHADOW_STATS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SHADOW_STATS_PATH)
    except Exception as e:
        print(f"[SHADOW_STATS] 저장 실패: {e}")
    _save_blocked_stats()


def _load_blocked_stats():
    """봇 시작 시 저장된 차단 건 가상 추적 통계 로드"""
    global _SHADOW_BLOCKED_STATS, _SHADOW_BLOCKED_TRADE_COUNT
    try:
        if os.path.exists(SHADOW_BLOCKED_STATS_PATH):
            with open(SHADOW_BLOCKED_STATS_PATH, "r", encoding="utf-8") as f:
                _SHADOW_BLOCKED_STATS = json.load(f)
            _SHADOW_BLOCKED_TRADE_COUNT = sum(
                s.get("signals", 0) for s in _SHADOW_BLOCKED_STATS.values())
            print(f"[BLOCKED_STATS] 로드 완료: {len(_SHADOW_BLOCKED_STATS)}개 필터, "
                  f"총 {_SHADOW_BLOCKED_TRADE_COUNT}건")
    except Exception as e:
        print(f"[BLOCKED_STATS] 로드 실패: {e}")
        _SHADOW_BLOCKED_STATS = {}
    # 로드된 누적 통계로 즉시 필터 유효성 판정 알림
    if _SHADOW_BLOCKED_STATS:
        _check_blocked_filter_alerts_on_load()


def _check_blocked_filter_alerts_on_load():
    """봇 시작 시 로드된 차단 통계로 즉시 필터 유효성 알림 전송.
    재시작 후에도 누적 데이터 기반 '재검토 필요' 판정이 이어지도록 함."""
    MIN_SAMPLES = 20  # 최소 샘플 수 — 20건 미만은 통계적으로 무의미
    review_alerts = []  # ⚠ 재검토 필요 (승률 55%+ → 필터가 좋은 매매 막고 있음)
    valid_cnt = 0       # ✅ 유효 건수 (상세 안 보냄)
    with _SHADOW_PERF_LOCK:
        for bkey, bs in _SHADOW_BLOCKED_STATS.items():
            bn = bs.get("signals", 0)
            if bn < MIN_SAMPLES:
                continue
            bw = bs.get("wins", 0)
            bwr = bw / bn * 100
            bavg = bs.get("total_pnl", 0) / bn * 100
            broute = bs.get("route", "?")
            bfilter = bs.get("filter", bkey)
            if bwr >= 55:
                review_alerts.append(
                    f"  ⚠{broute}:{bfilter} {bn}건 승률{bwr:.0f}%"
                    f" PnL{bavg:+.2f}% → 재검토 필요"
                )
            elif bwr <= 45:
                valid_cnt += 1
    if review_alerts or valid_cnt > 0:
        parts = []
        if review_alerts:
            parts.extend(review_alerts[:15])  # 최대 15개
            if len(review_alerts) > 15:
                parts.append(f"  ... 외 {len(review_alerts)-15}건")
        if valid_cnt > 0:
            parts.append(f"  ✅ 유효 필터: {valid_cnt}개 (정상 작동 중)")
        msg = "🔄 [재시작] 차단 필터 누적 판정:\n" + "\n".join(parts)
        try:
            tg_send(msg)
        except Exception as e:
            print(f"[BLOCKED_ALERT] 전송 실패: {e}")
        print(msg)


def _save_blocked_stats():
    """차단 건 통계 파일 저장 (atomic write)"""
    with _SHADOW_PERF_LOCK:
        data = copy.deepcopy(_SHADOW_BLOCKED_STATS)
    try:
        tmp = SHADOW_BLOCKED_STATS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SHADOW_BLOCKED_STATS_PATH)
    except Exception as e:
        print(f"[BLOCKED_STATS] 저장 실패: {e}")


def _shadow_record_blocked_result(route, strat_name, market, pnl_pct, mfe_pct,
                                   exit_reason, hold_sec, blocked_by="",
                                   mae=None, fail_value=None, fail_threshold=None,
                                   fail_direction=None):
    """차단 건 가상 추적 결과 기록 — 시나리오:필터별 W/L, PnL, MFE, MAE, 보유시간, 임계치값 누적"""
    global _SHADOW_BLOCKED_TRADE_COUNT
    # v15: route 포함 → 시나리오별 분리 (같은 필터라도 시나리오마다 별도 통계)
    key = f"{route}:{blocked_by}" if blocked_by else f"{route}:{strat_name}"
    is_win = pnl_pct > 0
    with _SHADOW_PERF_LOCK:
        if key not in _SHADOW_BLOCKED_STATS:
            _SHADOW_BLOCKED_STATS[key] = {
                "filter": blocked_by, "route": route, "strat": strat_name,
                "signals": 0, "wins": 0, "losses": 0,
                "total_pnl": 0.0, "pnls": [],
                "mfes": [], "hold_secs": [],
                "mae_sum": 0.0, "mae_cnt": 0,
                "exit_reasons": {},
                "fail_values": [],  # v16: 차단 시점 실제 지표 값
                "fail_threshold": fail_threshold,
                "fail_direction": fail_direction,
            }
        s = _SHADOW_BLOCKED_STATS[key]
        # 마이그레이션: 기존 데이터에 누락 필드 보충
        if "mfes" not in s:
            s["mfes"] = []
        if "hold_secs" not in s:
            s["hold_secs"] = []
        if "mae_sum" not in s:
            s["mae_sum"] = 0.0
        if "mae_cnt" not in s:
            s["mae_cnt"] = 0
        if "fail_values" not in s:
            s["fail_values"] = []
        if "fail_threshold" not in s and fail_threshold is not None:
            s["fail_threshold"] = fail_threshold
        if "fail_direction" not in s and fail_direction is not None:
            s["fail_direction"] = fail_direction
        s["signals"] += 1
        if is_win:
            s["wins"] += 1
        else:
            s["losses"] += 1
        s["total_pnl"] = round(s["total_pnl"] + pnl_pct, 6)
        s["pnls"].append(round(pnl_pct, 5))
        if len(s["pnls"]) > 200:
            s["pnls"] = s["pnls"][-200:]
        s["mfes"].append(round(mfe_pct, 5))
        if len(s["mfes"]) > 200:
            s["mfes"] = s["mfes"][-200:]
        s["hold_secs"].append(round(hold_sec, 1))
        if len(s["hold_secs"]) > 200:
            s["hold_secs"] = s["hold_secs"][-200:]
        if mae is not None:
            s["mae_sum"] = round(s["mae_sum"] + mae, 6)
            s["mae_cnt"] += 1
        if fail_value is not None:
            s["fail_values"].append({"v": round(fail_value, 4), "pnl": round(pnl_pct, 5)})
            if len(s["fail_values"]) > 200:
                s["fail_values"] = s["fail_values"][-200:]
        s["exit_reasons"][exit_reason] = s["exit_reasons"].get(exit_reason, 0) + 1
        _SHADOW_BLOCKED_TRADE_COUNT += 1
        _should_save = (_SHADOW_BLOCKED_TRADE_COUNT % SHADOW_STATS_SAVE_INTERVAL == 0)
    if _should_save:
        _save_blocked_stats()


def _calc_ind_avg(ind_list):
    """indicators 리스트 → (키별 평균 dict, 키별 건수 dict)"""
    if not ind_list:
        return {}, {}
    totals = {}
    counts = {}
    for d in ind_list:
        for k, v in d.items():
            if isinstance(v, (int, float)):
                totals[k] = totals.get(k, 0.0) + v
                counts[k] = counts.get(k, 0) + 1
    avg = {k: round(totals[k] / counts[k], 6) for k in totals}
    return avg, counts


def _shadow_auto_analyze_indicators(min_samples=10, effect_threshold=0.8):
    """W/L 지표 자동 분석 — 통계적으로 유의미한 차이를 가진 지표 탐지.

    Welford M2 기반 분산으로 effect size (Cohen's d) 계산:
      d = |mean_W - mean_L| / pooled_std
    d ≥ threshold이고 양측 샘플 ≥ min_samples일 때 필터 후보로 추천.

    Returns: { "route:strat": [ {ind, effect, direction, w_avg, l_avg, threshold_suggest}, ... ] }
    """
    import math
    results = {}
    with _SHADOW_PERF_LOCK:
        for key, s in _SHADOW_PERF_STATS.items():
            w_avg = s.get("win_ind_avg", {})
            w_cnt = s.get("win_ind_cnt", {})
            w_m2 = s.get("win_ind_m2", {})
            l_avg = s.get("loss_ind_avg", {})
            l_cnt = s.get("loss_ind_cnt", {})
            l_m2 = s.get("loss_ind_m2", {})
            if not w_avg and not l_avg:
                continue
            findings = []
            all_keys = set(w_avg.keys()) & set(l_avg.keys())
            for ik in sorted(all_keys):
                nw = w_cnt.get(ik, 0)
                nl = l_cnt.get(ik, 0)
                if nw < min_samples or nl < min_samples:
                    continue
                mw = w_avg[ik]
                ml = l_avg[ik]
                diff = abs(mw - ml)
                if diff < 1e-9:
                    continue
                # Welford variance: var = M2 / n
                var_w = w_m2.get(ik, 0) / nw if nw > 1 else 0
                var_l = l_m2.get(ik, 0) / nl if nl > 1 else 0
                pooled_var = (var_w * nw + var_l * nl) / (nw + nl)
                if pooled_var <= 0:
                    continue
                pooled_std = math.sqrt(pooled_var)
                effect = diff / pooled_std
                if effect < effect_threshold:
                    continue
                # 방향: W > L이면 "높을수록 좋다" → ≥ 기준 추천
                if mw > ml:
                    direction = "≥"
                    threshold_suggest = round((mw + ml) / 2, 3)
                else:
                    direction = "≤"
                    threshold_suggest = round((mw + ml) / 2, 3)
                # 승률 개선 추정 (정규분포 가정, 간이)
                # 해당 threshold 적용 시 필터링되는 L 비율 추정
                findings.append({
                    "ind": ik,
                    "effect": round(effect, 2),
                    "direction": direction,
                    "w_avg": round(mw, 3),
                    "l_avg": round(ml, 3),
                    "threshold": threshold_suggest,
                    "n_w": nw, "n_l": nl,
                })
            if findings:
                findings.sort(key=lambda x: x["effect"], reverse=True)
                results[key] = findings[:5]  # 상위 5개만
    return results


def _shadow_record_result(route, strat_name, market, pnl_pct, mfe_pct, exit_reason, hold_sec,
                          indicators=None, mae=None, pnl_curve=None):
    """섀도우 가상매매 결과를 누적 통계에 기록 (점진적 평균 + Welford 분산)"""
    global _SHADOW_TRADE_COUNT
    key = f"{route}:{strat_name}"
    is_win = pnl_pct > 0
    with _SHADOW_PERF_LOCK:
        if key not in _SHADOW_PERF_STATS:
            _SHADOW_PERF_STATS[key] = {
                "route": route, "strat": strat_name,
                "signals": 0, "wins": 0, "losses": 0,
                "total_pnl": 0.0, "pnls": [], "mfes": [],
                "exit_reasons": {}, "hold_secs": [],
                "coins": [],
                "win_ind_avg": {}, "win_ind_cnt": {},
                "loss_ind_avg": {}, "loss_ind_cnt": {},
                "win_ind_m2": {}, "loss_ind_m2": {},
                "mae_sum": 0.0, "mae_cnt": 0,
                "pnl_curve_sum": {}, "pnl_curve_cnt": {},
                "sl_hit_secs": [],           # v18d: SL 히트 시점 (초)
                "coin_wl": {},               # v18d: 코인별 {coin: [wins, losses]}
                "_v11_filters_reset": True,
            }
        s = _SHADOW_PERF_STATS[key]
        # v11 이후: 구 마이그레이션 불필요 (전체 리셋 완료)
        # 필드 보장만 수행
        for _field, _default in (("win_ind_avg", {}), ("win_ind_cnt", {}),
                                  ("loss_ind_avg", {}), ("loss_ind_cnt", {}),
                                  ("win_ind_m2", {}), ("loss_ind_m2", {}),
                                  ("mae_sum", 0.0), ("mae_cnt", 0),
                                  ("pnl_curve_sum", {}), ("pnl_curve_cnt", {}),
                                  ("sl_hit_secs", []), ("coin_wl", {})):
            if _field not in s:
                s[_field] = _default
        s["signals"] += 1
        if is_win:
            s["wins"] += 1
        else:
            s["losses"] += 1
        s["total_pnl"] = round(s["total_pnl"] + pnl_pct, 6)
        s["pnls"].append(round(pnl_pct, 5))
        if len(s["pnls"]) > 200:
            s["pnls"] = s["pnls"][-200:]
        s["mfes"].append(round(mfe_pct, 5))
        if len(s["mfes"]) > 200:
            s["mfes"] = s["mfes"][-200:]
        # 청산 사유별 카운트
        s["exit_reasons"][exit_reason] = s["exit_reasons"].get(exit_reason, 0) + 1
        # 보유 시간
        s["hold_secs"].append(round(hold_sec, 1))
        if len(s["hold_secs"]) > 200:
            s["hold_secs"] = s["hold_secs"][-200:]
        # 코인 종류
        coin = market.split("-")[-1] if "-" in market else market
        if coin not in s["coins"]:
            s["coins"].append(coin)
            if len(s["coins"]) > 50:
                s["coins"] = s["coins"][-50:]
        # v18d: SL 히트 시점 기록
        if exit_reason == "손절SL":
            sl_secs = s.get("sl_hit_secs", [])
            sl_secs.append(round(hold_sec, 1))
            if len(sl_secs) > 200:
                sl_secs = sl_secs[-200:]
            s["sl_hit_secs"] = sl_secs
        # v18d: 코인별 W/L 기록
        coin_wl = s.get("coin_wl", {})
        if coin not in coin_wl:
            coin_wl[coin] = [0, 0]
        if is_win:
            coin_wl[coin][0] += 1
        else:
            coin_wl[coin][1] += 1
        s["coin_wl"] = coin_wl
        # 🔬 진입 지표값 승/패 점진적 평균 + Welford 분산 업데이트
        if indicators:
            if is_win:
                avg_key, cnt_key, m2_key = "win_ind_avg", "win_ind_cnt", "win_ind_m2"
            else:
                avg_key, cnt_key, m2_key = "loss_ind_avg", "loss_ind_cnt", "loss_ind_m2"
            avg = s.get(avg_key, {})
            cnt = s.get(cnt_key, {})
            m2 = s.get(m2_key, {})
            for k, v in indicators.items():
                if isinstance(v, (int, float)):
                    cnt[k] = cnt.get(k, 0) + 1
                    cn = cnt[k]
                    if cn == 1:
                        avg[k] = round(v, 6)
                        m2[k] = 0.0
                    else:
                        old_avg = avg[k]
                        avg[k] = round(old_avg + (v - old_avg) / cn, 6)
                        # Welford: M2 += (x - old_mean) * (x - new_mean)
                        m2[k] = round(m2[k] + (v - old_avg) * (v - avg[k]), 6)
            s[avg_key] = avg
            s[cnt_key] = cnt
            s[m2_key] = m2
        # v18: per-trade 기록 저장 → 임계치 sweep 전체건 비교용
        if indicators:
            if "trade_records" not in s:
                s["trade_records"] = []
            _tr = {
                "pnl": round(pnl_pct, 5),
                "inds": {k: round(v, 4) for k, v in indicators.items() if isinstance(v, (int, float))}
            }
            # v18e: 개별 건 pnl_curve 저장 → 조기 탈출 분석용
            if pnl_curve:
                _tr["curve"] = {k: round(v, 5) for k, v in pnl_curve.items()}
            s["trade_records"].append(_tr)
            if len(s["trade_records"]) > 300:
                s["trade_records"] = s["trade_records"][-300:]
        # MAE 누적
        if mae is not None:
            s["mae_sum"] = round(s.get("mae_sum", 0.0) + mae, 6)
            s["mae_cnt"] = s.get("mae_cnt", 0) + 1
        # PnL 곡선 스냅샷 누적
        if pnl_curve:
            cs = s.get("pnl_curve_sum", {})
            cc = s.get("pnl_curve_cnt", {})
            for sec_key, pval in pnl_curve.items():
                sk = str(sec_key)
                cs[sk] = round(cs.get(sk, 0.0) + pval, 6)
                cc[sk] = cc.get(sk, 0) + 1
            s["pnl_curve_sum"] = cs
            s["pnl_curve_cnt"] = cc
        _SHADOW_TRADE_COUNT += 1
        _should_save = (_SHADOW_TRADE_COUNT % SHADOW_STATS_SAVE_INTERVAL == 0)  # 🔧 FIX: 락 안에서 체크

    if _should_save:
        _save_shadow_stats()


def _shadow_sim_exit(vp, cur_price):
    """가상포지션에 실제 청산 로직(TRAIL) 시뮬레이션 적용.
    Returns: (closed: bool, exit_reason: str)

    청산 조건 (실제 monitor_position과 동일):
    1. 손절 (SL): 현재가 ≤ entry × (1 - sl_pct)
    2. 체크포인트 도달 → 트레일링 활성화: 최고가 대비 trail_pct 이상 하락 시 청산
    3. 타임아웃: max_bars × RECHECK_SEC 초과 보유 시 청산
    """
    entry_price = vp["entry_price"]
    ep = vp["exit_params"]
    sl_pct = ep.get("sl_pct", 0.007)
    activation_pct = ep.get("activation_pct", 0.003)
    trail_pct = ep.get("trail_pct", 0.002)
    max_bars = ep.get("max_bars", 60)

    # v18e: ATR 동적 스케일링 — 비활성화 (v18d 고정값으로 복원, 근거 확보 후 재도입)
    # _REF_ATR = 0.25
    # atr_pct = vp.get("indicators", {}).get("atr_pct")
    # if atr_pct and atr_pct > 0:
    #     atr_scale = max(0.5, min(2.5, atr_pct / _REF_ATR))
    #     sl_pct = sl_pct * atr_scale
    #     activation_pct = activation_pct * atr_scale
    #     trail_pct = trail_pct * atr_scale

    now = time.time()
    hold_sec = now - vp["entry_ts"]
    pnl = (cur_price - entry_price) / entry_price

    # 최고가 갱신 + MFE 도달 시점 기록
    if cur_price > vp["best_price"]:
        vp["best_price"] = cur_price
        vp["_mfe_sec"] = hold_sec  # MFE가 몇 초에 찍혔는지

    mfe = (vp["best_price"] - entry_price) / entry_price

    # 1) 손절 (SL) — 항상 작동
    if pnl <= -sl_pct:
        return True, "손절SL"

    # G-v2: min_hold 120초 + 조기탈출 제거 + 트레일 지연
    # 120초 이상 건: +1.03%, 미만: -0.95%. 빨리 건드리면 죽고 버티면 산다.
    _is_g = vp.get("route", "").startswith("G")  # G/G2/G4/G6/G7/GT 모두 포함
    _g_min_hold = 120  # G-variants 최소보유시간 120초
    _disable_trail = ep.get("disable_trail", False)  # GT: trail/본절 완전 우회

    # 2) 체크포인트 도달 → 트레일링 (G는 120초 이후에만, GT는 영구 비활성)
    cost_floor = FEE_RATE + 0.001 + PROFIT_CHECKPOINT_MIN_ALPHA
    checkpoint = max(cost_floor, activation_pct)
    _trail_allowed = (not _disable_trail) and (not _is_g or hold_sec >= _g_min_hold)
    if _trail_allowed and mfe >= checkpoint:
        if not vp["trail_armed"]:
            vp["trail_armed"] = True
            vp["trail_stop"] = vp["best_price"] * (1 - trail_pct)
        else:
            # 트레일 스톱 갱신 (최고가 따라 올림)
            new_stop = vp["best_price"] * (1 - trail_pct)
            if new_stop > vp["trail_stop"]:
                vp["trail_stop"] = new_stop

    # 트레일 스톱 히트 (G는 120초 이후에만)
    if _trail_allowed and vp["trail_armed"] and cur_price <= vp["trail_stop"]:
        trail_pnl = (vp["trail_stop"] - entry_price) / entry_price
        if trail_pnl > 0:
            return True, "트레일익절"
        else:
            return True, "트레일본절"

    # 3) 본절 스톱 (G는 120초 이후에만)
    if _trail_allowed and mfe >= checkpoint and pnl <= 0:
        return True, "본절SL"

    # 4) 타임아웃 (max_bars × RECHECK_SEC)
    if hold_sec >= max_bars * RECHECK_SEC:
        return True, "타임아웃"

    vp["bars"] += 1
    return False, ""


def _shadow_evaluate_positions():
    """섀도우 가상포지션에 청산 로직 적용 — 메인 루프 매 사이클 호출
    일반 가상 포지션 + 차단 건 가상 추적 모두 처리"""
    now = time.time()
    with _SHADOW_LOCK:
        if not _SHADOW_VIRTUAL_POSITIONS and not _SHADOW_BLOCKED_POSITIONS:
            return
        all_vps = _SHADOW_VIRTUAL_POSITIONS + _SHADOW_BLOCKED_POSITIONS
        markets = list(set(vp["market"] for vp in all_vps))

    # 시세 일괄 조회 (API 호출 절약)
    price_map = {}
    for i in range(0, len(markets), 10):
        batch = markets[i:i+10]
        try:
            js = safe_upbit_get("https://api.upbit.com/v1/ticker",
                                {"markets": ",".join(batch)}, retries=1)
            if js:
                for t in js:
                    price_map[t["market"]] = t.get("trade_price", 0)
        except Exception:
            pass

    closed_results = []
    blocked_closed = []
    with _SHADOW_LOCK:
        # --- 일반 가상 포지션 평가 ---
        remaining = []
        for vp in _SHADOW_VIRTUAL_POSITIONS:
            cur_price = price_map.get(vp["market"], 0)
            if cur_price <= 0:
                remaining.append(vp)
                continue
            if cur_price < vp.get("worst_price", vp["entry_price"]):
                vp["worst_price"] = cur_price
            hold_sec_now = now - vp["entry_ts"]
            # v18e: pullback delay 처리 — 대기 중 최저가 추적, 완료 시 entry_price 갱신
            _pb_delay = vp.get("_pullback_delay_sec", 0)
            if _pb_delay > 0 and hold_sec_now < _pb_delay:
                if cur_price < vp.get("_pullback_best_price", vp["entry_price"]):
                    vp["_pullback_best_price"] = cur_price
                remaining.append(vp)
                continue
            elif _pb_delay > 0 and not vp.get("_pullback_applied"):
                vp["_pullback_applied"] = True
                pb_price = vp.get("_pullback_best_price", vp["entry_price"])
                vp["entry_price"] = pb_price
                vp["best_price"] = max(pb_price, cur_price)
                vp["worst_price"] = min(pb_price, cur_price)
                vp["entry_ts"] = now  # 실질 진입시점 리셋
            for snap_s in _SHADOW_PNL_SNAP_SECS:
                sk = str(snap_s)
                if sk not in vp.get("pnl_curve", {}) and hold_sec_now >= snap_s:
                    vp.setdefault("pnl_curve", {})[sk] = round(
                        (cur_price - vp["entry_price"]) / vp["entry_price"], 6)
            closed, reason = _shadow_sim_exit(vp, cur_price)
            if closed:
                entry_price = vp["entry_price"]
                pnl = (cur_price - entry_price) / entry_price
                mfe = (vp["best_price"] - entry_price) / entry_price
                mae = (vp.get("worst_price", entry_price) - entry_price) / entry_price
                hold = now - vp["entry_ts"]
                # v18e: 청산 시점 지표 추가 (MFE 도달 시점)
                _close_ind = dict(vp.get("indicators", {}))
                if "_mfe_sec" in vp:
                    _close_ind["mfe_peak_sec"] = round(vp["_mfe_sec"], 0)
                closed_results.append((vp, pnl, mfe, mae, reason, hold,
                                       _close_ind, vp.get("pnl_curve", {})))
            else:
                remaining.append(vp)
        _SHADOW_VIRTUAL_POSITIONS[:] = remaining

        # --- 차단 건 가상 포지션 평가 (동일 청산 로직 + PnL curve) ---
        blocked_remaining = []
        for vp in _SHADOW_BLOCKED_POSITIONS:
            cur_price = price_map.get(vp["market"], 0)
            if cur_price <= 0:
                blocked_remaining.append(vp)
                continue
            if cur_price < vp.get("worst_price", vp["entry_price"]):
                vp["worst_price"] = cur_price
            # v15: PnL 시간곡선 수집 (일반 포지션과 동일)
            hold_sec_now = now - vp["entry_ts"]
            for snap_s in _SHADOW_PNL_SNAP_SECS:
                sk = str(snap_s)
                if sk not in vp.get("pnl_curve", {}) and hold_sec_now >= snap_s:
                    vp.setdefault("pnl_curve", {})[sk] = round(
                        (cur_price - vp["entry_price"]) / vp["entry_price"], 6)
            closed, reason = _shadow_sim_exit(vp, cur_price)
            if closed:
                entry_price = vp["entry_price"]
                pnl = (cur_price - entry_price) / entry_price
                mfe = (vp["best_price"] - entry_price) / entry_price
                mae = (vp.get("worst_price", entry_price) - entry_price) / entry_price
                hold = now - vp["entry_ts"]
                blocked_closed.append((vp, pnl, mfe, mae, reason, hold))
            else:
                blocked_remaining.append(vp)
        _SHADOW_BLOCKED_POSITIONS[:] = blocked_remaining

    # 일반 결과 기록
    for vp, pnl, mfe, mae, reason, hold, indicators, pnl_curve in closed_results:
        _shadow_record_result(vp["route"], vp["strat"], vp["market"],
                              pnl, mfe, reason, hold, indicators,
                              mae=mae, pnl_curve=pnl_curve)

    # 차단 건 결과 기록
    for vp, pnl, mfe, mae, reason, hold in blocked_closed:
        _shadow_record_blocked_result(
            vp["route"], vp["strat"], vp["market"],
            pnl, mfe, reason, hold,
            blocked_by=vp.get("_blocked_by", ""),
            mae=mae,
            fail_value=vp.get("_fail_value"),
            fail_threshold=vp.get("_fail_threshold"),
            fail_direction=vp.get("_fail_direction"))

    # 중복 방지 캐시 정리
    with _SHADOW_LOCK:
        stale = [k for k, v in _SHADOW_DEDUP.items() if now - v > SHADOW_DEDUP_CD_SEC * 2]
        for k in stale:
            _SHADOW_DEDUP.pop(k, None)
        stale_b = [k for k, v in _SHADOW_BLOCKED_DEDUP.items() if now - v > SHADOW_DEDUP_CD_SEC * 2]
        for k in stale_b:
            _SHADOW_BLOCKED_DEDUP.pop(k, None)


def _v4_shadow_test_all_routes(market, c1, c5, c15, c30, c60, m3_info):
    """섀도우 테스트: 비활성 전략에 시그널 발생 시 가상 포지션 등록.
    실매매 안 함 — 가상 진입 → 실제 청산 로직 시뮬레이션 → 승률/수익률 누적.
    v10: 공통 지표 수집 후 각 전략 고유 지표와 병합 (자기 지표 우선, 타 지표 추가)

    ⭐ check_fn 캐시 최적화:
    - G/G2/G4/G6/G7/GT 6개 variants가 동일한 _v0_check_momentum_rsi를 호출하므로
      동일 (check_fn, candles) 입력에 대해 결과가 완전히 동일
    - Pure function이므로 첫 호출만 실행하고 이후는 캐시된 (sig, all_fails) 재사용
    - _pipeline_inc 부작용도 1회만 발생 → 카운터 정확도 개선 (이전 6× 부풀려짐)
    - _BLOCKED_THREAD_LOCAL 상태도 첫 호출에서만 수집
    - scan_detect 예상 감소: 65s → 20~30s
    """
    results = {}
    now_ts = time.time()
    entry_price = c1[-1]["trade_price"] if c1 else 0

    # 공통 지표 한 번만 계산 (모든 전략에 공유)
    _t_uni = time.time()
    try:
        universal_ind = _collect_universal_indicators(c1, c5, c15, c30, c60, market=market)
    except Exception as e:
        print(f"[SHADOW] universal_ind 수집 실패: {e}")
        universal_ind = {}
    _add_cycle_universal_ind_ms((time.time() - _t_uni) * 1000)

    # ⭐ Per-call check_fn 캐시 — 같은 함수는 1회만 호출하고 재사용
    # 키: check_fn 객체 식별자 / 값: (sig, all_fails) 튜플
    _check_fn_cache = {}
    # hit/miss 로컬 집계 (함수 종료 시 글로벌로 flush → lock 한 번만)
    _local_hits = {}
    _local_misses = {}
    # 실행 시간 로컬 집계 (cache miss 시 실제 실행 시간)
    _local_exec_ms = {}
    _local_exec_calls = {}

    for strat_name, strat in _STRATEGY_REGISTRY.items():
        route = strat.get("route", "?")
        check_fn = _SHADOW_CHECK_OVERRIDES.get(strat_name, strat["check_fn"])
        _fn_name = getattr(check_fn, "__name__", str(check_fn))

        # 🟢 캐시 조회 — 같은 check_fn 이미 호출됐으면 결과 재사용 (부작용 없음)
        if check_fn in _check_fn_cache:
            _local_hits[_fn_name] = _local_hits.get(_fn_name, 0) + 1
            sig, all_fails = _check_fn_cache[check_fn]
        else:
            _local_misses[_fn_name] = _local_misses.get(_fn_name, 0) + 1
            # 🔴 캐시 미스 — 실제 호출 (부작용: _pipeline_inc, _BLOCKED_THREAD_LOCAL)
            # ⏱️ 실행 시간 측정 시작 (1차 호출 + eval_all 재호출 합산)
            _t_check = time.time()
            # 차단 플래그 초기화 후 전략 호출
            _BLOCKED_THREAD_LOCAL.last_fail = None
            _BLOCKED_THREAD_LOCAL.last_fail_value = None
            _BLOCKED_THREAD_LOCAL.last_fail_threshold = None
            _BLOCKED_THREAD_LOCAL.last_fail_direction = None
            try:
                sig = check_fn(c1, c5, c15, c30, c60, gate_info=m3_info)
            except Exception:
                sig = None
            all_fails = []
            if sig is None and getattr(_BLOCKED_THREAD_LOCAL, "last_fail", None):
                # eval_all 모드로 재실행 → 모든 실패 필터 수집
                _BLOCKED_THREAD_LOCAL.last_fail = None
                _BLOCKED_THREAD_LOCAL.last_fail_value = None
                _BLOCKED_THREAD_LOCAL.last_fail_threshold = None
                _BLOCKED_THREAD_LOCAL.last_fail_direction = None
                _BLOCKED_THREAD_LOCAL.all_fails = []
                _BLOCKED_THREAD_LOCAL._eval_all_mode = True
                try:
                    check_fn(c1, c5, c15, c30, c60, gate_info=m3_info)
                except Exception:
                    pass
                finally:
                    _BLOCKED_THREAD_LOCAL._eval_all_mode = False
                all_fails = list(getattr(_BLOCKED_THREAD_LOCAL, "all_fails", []))
            # ⏱️ 실행 시간 측정 종료 — sanity cap (>10초는 outlier)
            _check_elapsed_ms = (time.time() - _t_check) * 1000
            if 0 <= _check_elapsed_ms < 10000:
                _local_exec_ms[_fn_name] = _local_exec_ms.get(_fn_name, 0.0) + _check_elapsed_ms
                _local_exec_calls[_fn_name] = _local_exec_calls.get(_fn_name, 0) + 1
            # 캐시 저장 — 이후 같은 check_fn 호출 시 재사용
            _check_fn_cache[check_fn] = (sig, all_fails)

        hit = sig is not None
        results[route] = hit

        if entry_price <= 0:
            continue

        if hit:
            # 정상 시그널 → 기존 가상 포지션 등록
            dedup_key = f"{route}_{market}"
            ep = strat.get("exit_params", _V4_DEFAULT_EXIT).copy()
            merged_ind = dict(universal_ind)
            own_ind = sig.get("indicators", {})
            merged_ind.update(own_ind)
            with _SHADOW_LOCK:
                last_entry = _SHADOW_DEDUP.get(dedup_key, 0)
                if now_ts - last_entry < SHADOW_DEDUP_CD_SEC:
                    continue
                if len(_SHADOW_VIRTUAL_POSITIONS) >= SHADOW_MAX_VIRTUAL_POS:
                    continue
                _SHADOW_DEDUP[dedup_key] = now_ts
                # v18e: B pullback entry — 30초 대기 후 최저가로 진입 (G는 30s부터 양수라 제외)
                _pb_delay = 30 if route in ("B", "B2") else 0
                _SHADOW_VIRTUAL_POSITIONS.append({
                    "route": route, "strat": strat_name,
                    "market": market, "entry_price": entry_price,
                    "entry_ts": now_ts, "best_price": entry_price,
                    "worst_price": entry_price,
                    "trail_armed": False, "trail_stop": 0.0,
                    "exit_params": ep, "bars": 0,
                    "indicators": merged_ind, "pnl_curve": {},
                    "_pullback_delay_sec": _pb_delay,
                    "_pullback_best_price": entry_price,
                    "_pullback_orig_price": entry_price,
                })
        elif all_fails:
            # v0: 실패한 필터 각각에 대해 차단건 가상 추적
            dedup_key = f"{route}_{market}"
            ep = strat.get("exit_params", _V4_DEFAULT_EXIT).copy()
            with _SHADOW_LOCK:
                last_entry = _SHADOW_BLOCKED_DEDUP.get(dedup_key, 0)
                if now_ts - last_entry < SHADOW_DEDUP_CD_SEC:
                    continue
                _SHADOW_BLOCKED_DEDUP[dedup_key] = now_ts
                for fail_info in all_fails:
                    if len(_SHADOW_BLOCKED_POSITIONS) >= SHADOW_MAX_BLOCKED_POS:
                        break
                    _SHADOW_BLOCKED_POSITIONS.append({
                        "route": route, "strat": strat_name,
                        "market": market, "entry_price": entry_price,
                        "entry_ts": now_ts, "best_price": entry_price,
                        "worst_price": entry_price,
                        "trail_armed": False, "trail_stop": 0.0,
                        "exit_params": ep.copy(), "bars": 0,
                        "indicators": dict(universal_ind), "pnl_curve": {},
                        "_blocked_by": fail_info["filter"],
                        "_fail_value": fail_info["value"],
                        "_fail_threshold": fail_info["threshold"],
                        "_fail_direction": fail_info["direction"],
                    })
    # 캐시 hit/miss 로컬 집계 → 글로벌 flush (lock 한 번만)
    if _local_hits or _local_misses:
        with _CHECK_FN_CACHE_LOCK:
            for _k, _v in _local_hits.items():
                _CHECK_FN_CACHE_HITS[_k] = _CHECK_FN_CACHE_HITS.get(_k, 0) + _v
            for _k, _v in _local_misses.items():
                _CHECK_FN_CACHE_MISSES[_k] = _CHECK_FN_CACHE_MISSES.get(_k, 0) + _v
    # check_fn 실행 시간 로컬 집계 → 글로벌 flush (lock 한 번만)
    if _local_exec_calls:
        with _CHECK_FN_EXEC_LOCK:
            for _k, _v in _local_exec_ms.items():
                _CHECK_FN_EXEC_TOTAL_MS[_k] = _CHECK_FN_EXEC_TOTAL_MS.get(_k, 0.0) + _v
            for _k, _v in _local_exec_calls.items():
                _CHECK_FN_EXEC_CALLS[_k] = _CHECK_FN_EXEC_CALLS.get(_k, 0) + _v
    return results


def _threshold_sweep(fail_values_list, current_threshold, direction,
                     trade_records=None, ind_key=None):
    """v18: 차단건 브루트포스 탐색 + 통과건 합산 전체 비교.
    direction: 'gte'/'gt' → 값이 threshold 이상이면 통과 (하한)
               'lt'/'lte' → 값이 threshold 미만이면 통과 (상한)
    Returns: dict with best alternative threshold or None"""
    if not fail_values_list or len(fail_values_list) < 5:
        return None
    fail_data = [fv for fv in fail_values_list if fv.get("v") is not None]
    if len(fail_data) < 5:
        return None

    def _passes(v, th, d):
        if d == "gte": return v >= th
        if d == "gt": return v > th
        if d == "lte": return v <= th
        if d == "lt": return v < th
        return False

    # 통과건 데이터 준비
    pass_data = []
    if trade_records and ind_key:
        for tr in trade_records:
            v = tr.get("inds", {}).get(ind_key)
            if v is not None:
                pass_data.append({"v": v, "pnl": tr["pnl"]})

    best = None
    candidates = sorted(set(fv["v"] for fv in fail_data),
                        reverse=(direction in ("gte", "gt")))
    for c in candidates:
        # 차단건 중 새 임계치로 통과할 건
        new_passed = [fv for fv in fail_data if _passes(fv["v"], c, direction)]
        if len(new_passed) < 3:
            continue
        # 차단건만 기준
        fail_n = len(new_passed)
        fail_avg_pnl = sum(fv["pnl"] for fv in new_passed) / fail_n
        fail_wr = sum(1 for fv in new_passed if fv["pnl"] > 0) / fail_n * 100
        if fail_avg_pnl <= 0:
            continue
        # 전체건 합산 (통과건 + 새로 통과할 차단건)
        total_n = len(pass_data) + fail_n
        total_wins = (sum(1 for p in pass_data if p["pnl"] > 0)
                      + sum(1 for fv in new_passed if fv["pnl"] > 0))
        total_pnl = (sum(p["pnl"] for p in pass_data)
                     + sum(fv["pnl"] for fv in new_passed))
        total_wr = total_wins / total_n * 100 if total_n > 0 else 0
        total_avg_pnl = total_pnl / total_n * 100 if total_n > 0 else 0
        if best is None or fail_avg_pnl > best["fail_avg_pnl"]:
            best = {
                "new_th": round(c, 4),
                # 차단건 기준
                "fail_n": fail_n,
                "fail_avg_pnl": round(fail_avg_pnl * 100, 2),
                "fail_wr": round(fail_wr, 1),
                # 전체건 기준
                "total_n": total_n,
                "total_wr": round(total_wr, 1),
                "total_avg_pnl": round(total_avg_pnl, 2),
            }
    return best


# v18: 필터명 → 인디케이터키 매핑 (임계치 sweep 전체건 비교용)
_SWEEP_FILTER_TO_IND = {
    "vol_burst_vr5_fail": "vr5",
    "breakout_vr5_15m_fail": "vr5_15m",
    "momentum_rsi5_fail": "rsi_5m",
    "momentum_vr5_over_fail": "vr5",          # v18c: G VR5 상한
    "momentum_vr5_15m_fail": "vr5_15m",
    "oversold_rsi_fail": "rsi_5m",
    "oversold_engulf_fail": "engulf_ratio_60",
    "adx_trend_15_fail": "adx_15",
    "adx_trend_vr5_15m_fail": "vr5_15m",
    "reversal_15m_vr5_15m_fail": "vr5_15m",    # v18d: C engulf→vr5_15m 교체
    "reversal_15m_gap20_fail": "gap_20bar",
    # v18c: H gap20 제거, D 비활성화 → 매핑 불필요
}


def _threshold_sweep_table(trade_records, fail_values_list, current_threshold, direction, ind_key):
    """v18: 통과건(trade_records) + 차단건(fail_values) 합산 ±5% 전체 비교.
    - 조이기: 전체 W/L 승률 (통과건 중 더 조인 임계치 충족분만)
    - 풀기: 전체 W/L + 신규 추가건 W/L (차단건 중 새로 통과할 분)
    Returns: list of dicts or []"""
    if current_threshold == 0:
        return []
    # 통과건에서 해당 인디케이터 값 + PnL 추출
    pass_trades = []
    for tr in (trade_records or []):
        v = tr.get("inds", {}).get(ind_key)
        if v is not None:
            pass_trades.append({"v": v, "pnl": tr["pnl"], "src": "pass"})
    # 차단건에서 값 + PnL 추출
    fail_trades = []
    for fv in (fail_values_list or []):
        if fv.get("v") is not None:
            fail_trades.append({"v": fv["v"], "pnl": fv["pnl"], "src": "fail"})
    all_trades = pass_trades + fail_trades
    if len(all_trades) < 5:
        return []

    def _passes(v, th, d):
        if d == "gte": return v >= th
        if d == "gt": return v > th
        if d == "lte": return v <= th
        if d == "lt": return v < th
        return False

    # 풀기/조이기 방향 판별 (음수 임계치도 올바르게 처리)
    delta = abs(current_threshold) * 0.05
    if delta == 0:
        delta = 0.5  # threshold=0일 때 fallback
    if direction in ("gte", "gt"):
        loosen_step = round(current_threshold - delta, 4)  # 낮추면 더 많이 통과
        tighten_step = round(current_threshold + delta, 4)  # 높이면 더 적게 통과
    else:  # lt, lte
        loosen_step = round(current_threshold + delta, 4)  # 높이면 더 많이 통과
        tighten_step = round(current_threshold - delta, 4)  # 낮추면 더 적게 통과

    steps = [loosen_step, round(current_threshold, 4), tighten_step]
    rows = []
    for th in steps:
        is_current = (th == round(current_threshold, 4))
        matched = [t for t in all_trades if _passes(t["v"], th, direction)]
        n = len(matched)
        if n == 0:
            rows.append({"th": th, "n": 0, "wins": 0, "losses": 0,
                         "wr": 0, "avg_pnl": 0, "is_current": is_current,
                         "is_loosen": (th == loosen_step),
                         "new_n": 0, "new_wins": 0, "new_losses": 0,
                         "new_wr": 0, "new_avg_pnl": 0})
            continue
        wins = sum(1 for t in matched if t["pnl"] > 0)
        losses = n - wins
        wr = wins / n * 100
        avg_pnl = sum(t["pnl"] for t in matched) / n * 100
        # 신규 추가건 (차단→통과 전환분) — 풀기 방향에서만 의미
        new_trades = [t for t in matched if t["src"] == "fail"]
        new_n = len(new_trades)
        new_wins = sum(1 for t in new_trades if t["pnl"] > 0)
        new_losses = new_n - new_wins
        new_wr = new_wins / new_n * 100 if new_n > 0 else 0
        new_avg_pnl = sum(t["pnl"] for t in new_trades) / new_n * 100 if new_n > 0 else 0
        rows.append({
            "th": th, "n": n, "wins": wins, "losses": losses,
            "wr": round(wr, 1), "avg_pnl": round(avg_pnl, 2),
            "is_current": is_current,
            "is_loosen": (th == loosen_step),
            "new_n": new_n, "new_wins": new_wins, "new_losses": new_losses,
            "new_wr": round(new_wr, 1), "new_avg_pnl": round(new_avg_pnl, 2),
        })
    return rows


def _v4_shadow_report_lines():
    """전 시나리오 가상매매 성과 리포트 (10분 텔레그램 리포트용)
    루트별 시그널수, 승률, 평균수익률, MFE, 청산사유 분포 + 유니버설 지표 W/L 표시
    v11: 라이브(A,B) + 섀도우(C~L) 전체 11개 시나리오 지표 수집"""
    lines = []
    with _SHADOW_PERF_LOCK:
        if not _SHADOW_PERF_STATS:
            return []
        lines.append("📡 전 시나리오 가상매매 지표 W/L:")
        sorted_stats = sorted(_SHADOW_PERF_STATS.items(),
                              key=lambda x: x[1].get("signals", 0), reverse=True)
        # Phase2: GT만 상세, 나머지 전부 1줄 요약
        _DETAIL_ROUTES = {"GT"}  # GT만 풀 상세
        _SUMMARY_ROUTES = {"G", "G2", "G4", "G6", "G7", "GR", "GT70", "GT68", "GT_SL07", "GT_SL15", "GT_300s", "HT", "B2", "L", "H", "B", "C"}  # 전부 1줄
        _cluster_data = {}  # G-cluster 요약용 데이터 수집
        _shadow_cand_data = {}  # 🧪 Shadow후보(B2/HT/GT70/GT68) 요약용 수집
        for key, s in sorted_stats:
            n = s.get("signals", 0)
            if n < 1:
                continue
            wins = s.get("wins", 0)
            wr = wins / n * 100
            avg_pnl = s.get("total_pnl", 0) / n * 100
            coins = len(s.get("coins", []))
            # 승률 기반 이모지
            if wr >= 55:
                tag = "🟢"
            elif wr >= 45:
                tag = "🟡"
            else:
                tag = "🔴"
            route = s.get("route", "?")
            strat = s.get("strat", "?")
            # MFE / MAE 평균
            avg_mfe = sum(s.get("mfes", [])) / max(len(s.get("mfes", [])), 1) * 100
            mae_cnt = s.get("mae_cnt", 0)
            avg_mae_str = ""
            if mae_cnt > 0:
                avg_mae = s.get("mae_sum", 0) / mae_cnt * 100
                avg_mae_str = f" MAE{avg_mae:+.2f}%"
            lines.append(
                f"  {tag}{route}:{strat} {n}건 승률{wr:.0f}%"
                f" PnL{avg_pnl:+.2f}% MFE{avg_mfe:+.2f}%{avg_mae_str} ({coins}코인)"
            )
            # Phase2: G-cluster 데이터 수집 + 1줄 요약만
            if route in {"GT", "G", "G2", "G4", "G6", "G7", "GR"}:
                _cluster_data[route] = {"pnl": avg_pnl, "wr": wr, "n": n}
            # 🧪 Shadow후보 데이터 수집 (GT_SL07/SL15/300s 우선, 그다음 B2/HT/GT70/GT68)
            if route in {"GT_SL07", "GT_SL15", "GT_300s", "B2", "HT", "GT70", "GT68"}:
                _cs_cand = s.get("pnl_curve_sum", {})
                _cc_cand = s.get("pnl_curve_cnt", {})
                _p180 = None
                if "180" in _cs_cand and _cc_cand.get("180", 0) > 0:
                    _p180 = _cs_cand["180"] / _cc_cand["180"] * 100
                _p300 = None
                if "300" in _cs_cand and _cc_cand.get("300", 0) > 0:
                    _p300 = _cs_cand["300"] / _cc_cand["300"] * 100
                _shadow_cand_data[route] = {"pnl": avg_pnl, "wr": wr, "n": n, "p180": _p180, "p300": _p300}
            if route in _SUMMARY_ROUTES:
                continue  # 1줄 요약(위)만 출력하고 상세(아래) 전부 스킵
            # 청산 사유 분포 (상위 3개)
            reasons = s.get("exit_reasons", {})
            if reasons:
                top_reasons = sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:3]
                reason_str = " ".join(f"{r}:{c}" for r, c in top_reasons)
                lines.append(f"    └ {reason_str}")
            # PnL 시간곡선
            cs = s.get("pnl_curve_sum", {})
            cc = s.get("pnl_curve_cnt", {})
            if cs:
                curve_parts = []
                for snap_s in _SHADOW_PNL_SNAP_SECS:
                    sk = str(snap_s)
                    if sk in cs and cc.get(sk, 0) > 0:
                        avg_snap = cs[sk] / cc[sk] * 100
                        curve_parts.append(f"{snap_s}s:{avg_snap:+.2f}%")
                if curve_parts:
                    lines.append(f"    ⏱️ {' → '.join(curve_parts)}")
            # v18d: SL 히트 시점 분포
            sl_secs = s.get("sl_hit_secs", [])
            if sl_secs and len(sl_secs) >= 3:
                avg_sl = sum(sl_secs) / len(sl_secs)
                # 구간별 분포: 0-30s, 30-60s, 60-120s, 120s+
                b1 = sum(1 for x in sl_secs if x <= 30)
                b2 = sum(1 for x in sl_secs if 30 < x <= 60)
                b3 = sum(1 for x in sl_secs if 60 < x <= 120)
                b4 = sum(1 for x in sl_secs if x > 120)
                lines.append(
                    f"    🛑 SL평균{avg_sl:.0f}초"
                    f" (0-30s:{b1} 30-60s:{b2} 60-120s:{b3} 120s+:{b4})"
                )
            # v18d: 시간 기반 청산 PnL 비교 (트레일 vs 시간청산)
            if cs and n >= 20:
                # 현재 실제 PnL vs 각 시점 고정 청산 PnL
                time_exit_parts = []
                for snap_s in _SHADOW_PNL_SNAP_SECS:
                    sk = str(snap_s)
                    if sk in cs and cc.get(sk, 0) > 0:
                        time_pnl = cs[sk] / cc[sk] * 100
                        diff = time_pnl - avg_pnl
                        if abs(diff) >= 0.01:
                            sign = "+" if diff >= 0 else ""
                            time_exit_parts.append(f"{snap_s}s→{sign}{diff:.2f}%p")
                if time_exit_parts:
                    lines.append(f"    📐 시간청산시 차이: {' '.join(time_exit_parts)}")
            # v18d: 코인별 승률 (상위/하위 3개)
            coin_wl = s.get("coin_wl", {})
            if coin_wl and len(coin_wl) >= 5:
                coin_stats = []
                for c_name, (cw, cl) in coin_wl.items():
                    ct = cw + cl
                    if ct >= 3:  # 최소 3건
                        coin_stats.append((c_name, cw, cl, ct, cw / ct * 100))
                if len(coin_stats) >= 3:
                    coin_stats.sort(key=lambda x: x[4], reverse=True)
                    top3 = coin_stats[:3]
                    bot3 = coin_stats[-3:]
                    top_str = " ".join(f"{c}({w}W{l}L {wr:.0f}%)" for c, w, l, _, wr in top3)
                    bot_str = " ".join(f"{c}({w}W{l}L {wr:.0f}%)" for c, w, l, _, wr in bot3)
                    lines.append(f"    🏆 상위: {top_str}")
                    lines.append(f"    💀 하위: {bot_str}")
            # 🔬 진입지표 승/패 비교 — Phase2: GT/GR만 mfe_peak_sec 표시 (나머지 생략)
            _show_indicators = route in ("GT", "GR")
            if _show_indicators:
                w_ind = s.get("win_ind_avg", {})
                w_cnt = s.get("win_ind_cnt", {})
                w_m2 = s.get("win_ind_m2", {})
                l_ind = s.get("loss_ind_avg", {})
                l_cnt = s.get("loss_ind_cnt", {})
                l_m2 = s.get("loss_ind_m2", {})
                if w_ind or l_ind:
                    # mfe_peak_sec만 표시 (핵심 분리 지표)
                    for ik in ["mfe_peak_sec", "tick_age"]:
                        if ik not in w_ind and ik not in l_ind:
                            continue
                        _fmt = ".3f" if ik.startswith("tick_") else ".2f"
                        w_str = f"W{w_ind[ik]:{_fmt}}({w_cnt.get(ik,0)})" if ik in w_ind else "W:-"
                        l_str = f"L{l_ind[ik]:{_fmt}}({l_cnt.get(ik,0)})" if ik in l_ind else "L:-"
                        lines.append(f"    📊{ik}: {w_str} / {l_str}")
    # 📌 Phase2: G-cluster 요약 + GT 판정 상태
    if _cluster_data:
        _gc_parts = []
        for _r in ["GT", "G7", "G6", "G4"]:
            if _r in _cluster_data:
                _d = _cluster_data[_r]
                _gc_parts.append(f"{_r}({_d['pnl']:+.2f}%)")
        if _gc_parts:
            lines.append(f"📌 G-cluster: {' ≈ '.join(_gc_parts)}")
        # GT 판정 상태
        if "GT" in _cluster_data:
            _gt = _cluster_data["GT"]
            # 180s PnL 가져오기
            _gt_180s = ""
            for _k, _s in sorted_stats:
                if _s.get("route") == "GT":
                    _cs = _s.get("pnl_curve_sum", {})
                    _cc = _s.get("pnl_curve_cnt", {})
                    if "180" in _cs and _cc.get("180", 0) > 0:
                        _gt_180s = f" | 180s {_cs['180']/_cc['180']*100:+.2f}%"
                    break
            lines.append(f"🎯 GT 판정: PnL {_gt['pnl']:+.2f}% | 승률 {_gt['wr']:.0f}% | n={_gt['n']}{_gt_180s}")
        # GR 상태 (있으면)
        if "GR" in _cluster_data:
            _gr = _cluster_data["GR"]
            lines.append(f"🎯 GR 상태: PnL {_gr['pnl']:+.2f}% | 승률 {_gr['wr']:.0f}% | n={_gr['n']} (실전 부적합)")
    # 🧪 Shadow후보 (GT_SL07/SL15/300s 우선, B2/HT/GT70/GT68) 한줄 요약
    if _shadow_cand_data:
        lines.append("🧪 Shadow후보:")
        for _r in ["GT_SL07", "GT_SL15", "GT_300s", "B2", "HT", "GT70", "GT68"]:
            if _r in _shadow_cand_data:
                _d = _shadow_cand_data[_r]
                _p180_str = f" | 180s {_d['p180']:+.2f}%" if _d["p180"] is not None else ""
                _p300_str = f" | 300s {_d['p300']:+.2f}%" if _d["p300"] is not None else ""
                lines.append(f"  [{_r}] n={_d['n']} 승률{_d['wr']:.0f}% PnL{_d['pnl']:+.2f}%{_p180_str}{_p300_str}")
    # v18e: 조기 탈출 분석 — 시점별 PnL 임계치에 따른 최종 결과
    with _SHADOW_PERF_LOCK:
        _early_exit_lines = []
        for key, s in sorted_stats:
            tr_list = s.get("trade_records", [])
            _trades_with_curve = [t for t in tr_list if t.get("curve")]
            if len(_trades_with_curve) < 20:
                continue
            route = s.get("route", "?")
            # Phase2: GT만 조기탈출 상세 표시
            if route not in ("GT", "GR"):
                continue
            _ea_parts = []
            for check_sec in [60, 90, 120]:
                sk = str(check_sec)
                # 해당 시점에서 -0.1% 이하인 건 vs 아닌 건
                bad = [t for t in _trades_with_curve if t["curve"].get(sk) is not None and t["curve"][sk] < -0.001]
                good = [t for t in _trades_with_curve if t["curve"].get(sk) is not None and t["curve"][sk] >= -0.001]
                if len(bad) >= 5 and len(good) >= 5:
                    bad_avg = sum(t["pnl"] for t in bad) / len(bad) * 100
                    good_avg = sum(t["pnl"] for t in good) / len(good) * 100
                    _ea_parts.append(f"{check_sec}s미만({len(bad)}건)→{bad_avg:+.2f}% vs 이상({len(good)}건)→{good_avg:+.2f}%")
            if _ea_parts:
                _early_exit_lines.append(f"  [{route}] " + " | ".join(_ea_parts))
        if _early_exit_lines:
            lines.append("🔪 조기탈출 분석 (시점별 PnL<-0.1% 건의 최종결과):")
            lines.extend(_early_exit_lines)
    # 현재 추적 중인 가상포지션 수
    with _SHADOW_LOCK:
        active = len(_SHADOW_VIRTUAL_POSITIONS)
    if active > 0:
        lines.append(f"  ⏳ 추적 중: {active}건")
    # 🔬 W/L 자동 분석 — 필터 후보 임계치 추천
    try:
        analysis = _shadow_auto_analyze_indicators()
        if analysis:
            lines.append("🔍 필터 후보 자동 탐지:")
            # Phase2: GT만 표시
            _filter_routes = {"GT"}
            for akey, findings in analysis.items():
                _aroute = akey.split(":")[0] if ":" in akey else akey
                if _aroute not in _filter_routes:
                    continue
                lines.append(f"  [{akey}]")
                for f in findings[:3]:
                    star = "★" if f["effect"] >= 1.5 else "☆"
                    lines.append(
                        f"    {star}{f['ind']}: W={f['w_avg']} L={f['l_avg']}"
                        f" d={f['effect']} → {f['direction']}{f['threshold']} 추천"
                    )
    except Exception:
        pass
    # 🔍 차단 건 가상 추적 리포트 (counterfactual)
    # v15: 요약 + 상세 2줄 구조, ⚠재검토 우선, 전체 표시
    with _SHADOW_PERF_LOCK:
        if _SHADOW_BLOCKED_STATS:
            lines.append("🚫 필터 효과 검증 (차단 안했으면?):")
            # 재검토(승률 높은 것) 우선 → 시그널 수 내림차순
            sorted_blocked = sorted(
                _SHADOW_BLOCKED_STATS.items(),
                key=lambda x: (-1 if x[1].get("wins", 0) / max(x[1].get("signals", 1), 1) >= 0.55 else 0,
                               -x[1].get("signals", 0)))
            _blocked_shown = 0
            _blocked_pending = 0  # 샘플 부족 건수
            _BLOCKED_MAX_SHOW = 15  # Phase2: 상위 15개만 표시
            for bkey, bs in sorted_blocked:
                if _blocked_shown >= _BLOCKED_MAX_SHOW:
                    break
                bn = bs.get("signals", 0)
                if bn < 1:
                    continue
                if bn < 10:
                    _blocked_pending += 1
                    continue  # 10건 미만은 판정 보류
                bw = bs.get("wins", 0)
                bl = bs.get("losses", 0)
                bwr = bw / bn * 100
                bavg = bs.get("total_pnl", 0) / bn * 100
                broute = bs.get("route", "?")
                bfilter = bs.get("filter", bkey)
                # Phase2: G-variant 중복 제거 — GT만 표시 (G/G2/G4/G6/G7/GR 스킵)
                if broute in ("G", "G2", "G4", "G6", "G7", "GR"):
                    continue
                # 필터 효과 판정
                if bwr <= 45:
                    verdict = "✅유효"
                elif bwr >= 55:
                    verdict = "⚠재검토!"
                else:
                    verdict = "🔸관찰중"
                # 1줄: 요약 (읽기 쉬운 판정)
                lines.append(
                    f"{verdict} {broute}:{bfilter} — {bn}건 중 {bw}승 ({bwr:.0f}%) {bavg:+.2f}%"
                )
                # 2줄: 상세 수치 (가로 구분)
                mfe_list = bs.get("mfes", [])
                avg_mfe = sum(mfe_list) / max(len(mfe_list), 1) * 100
                hold_list = bs.get("hold_secs", [])
                avg_hold = sum(hold_list) / max(len(hold_list), 1)
                # 보유시간 읽기 쉽게
                if avg_hold >= 60:
                    hold_str = f"{avg_hold/60:.1f}m"
                else:
                    hold_str = f"{avg_hold:.0f}s"
                mae_part = ""
                if bs.get("mae_cnt", 0) > 0:
                    avg_mae = bs.get("mae_sum", 0.0) / bs.get("mae_cnt", 1) * 100
                    mae_part = f" | 최대손실{avg_mae:+.2f}%"
                _blocked_shown += 1
                lines.append(
                    f" └ 최고{avg_mfe:+.2f}%{mae_part} | 평균보유 {hold_str}"
                )
                # v18: threshold sweep — 임계치 최적화 제안 + ±5% 전체건 비교
                fv_list = bs.get("fail_values", [])
                f_th = bs.get("fail_threshold")
                f_dir = bs.get("fail_direction")
                if fv_list and f_th is not None and f_dir:
                    bfilter_name = bs.get("filter", "")
                    ind_key = _SWEEP_FILTER_TO_IND.get(bfilter_name)
                    # 해당 route의 perf stats에서 trade_records 가져오기
                    tr_list = []
                    if ind_key:
                        for pk, ps in _SHADOW_PERF_STATS.items():
                            if ps.get("route") == broute:
                                tr_list = ps.get("trade_records", [])
                                break
                    # 💡 최적 임계치 탐색 (차단건 + 전체건 합산)
                    sweep = _threshold_sweep(fv_list, f_th, f_dir,
                                             trade_records=tr_list, ind_key=ind_key)
                    if sweep:
                        lines.append(
                            f" └ 💡임계치 {f_th}→{sweep['new_th']} 시"
                            f" 차단건+{sweep['fail_n']}건 승률{sweep['fail_wr']:.0f}%"
                            f" PnL{sweep['fail_avg_pnl']:+.02f}%"
                            f" → 전체{sweep['total_n']}건"
                            f" 승률{sweep['total_wr']:.0f}%"
                            f" PnL{sweep['total_avg_pnl']:+.02f}%"
                        )
                    # ±5% 전체건 비교 테이블
                    if ind_key:
                        table = _threshold_sweep_table(tr_list, fv_list, f_th, f_dir, ind_key)
                        if table:
                            # 현재 기준값 추출 (변화량 표시용)
                            cur_row = next((r for r in table if r["is_current"]), None)
                            for row in table:
                                diff_n = row["n"] - cur_row["n"] if cur_row else 0
                                diff_wr = row["wr"] - cur_row["wr"] if cur_row else 0
                                diff_sign = "+" if diff_n >= 0 else ""
                                wr_sign = "+" if diff_wr >= 0 else ""
                                if row["is_current"]:
                                    lines.append(
                                        f"      ● {row['th']:>8g}(현재):"
                                        f" {row['n']}건 W{row['wins']}/L{row['losses']}"
                                        f" 승률{row['wr']:.0f}% PnL{row['avg_pnl']:+.02f}%"
                                    )
                                elif row.get("is_loosen"):
                                    new_part = ""
                                    if row["new_n"] > 0:
                                        new_part = (f" | 신규+{row['new_n']}건"
                                                    f" W{row['new_wins']}/L{row['new_losses']}"
                                                    f" 승률{row['new_wr']:.0f}%")
                                    lines.append(
                                        f"      ▼ {row['th']:>8g}(풀림):"
                                        f" {row['n']}건({diff_sign}{diff_n})"
                                        f" W{row['wins']}/L{row['losses']}"
                                        f" 승률{row['wr']:.0f}%({wr_sign}{diff_wr:.0f}%p)"
                                        f" PnL{row['avg_pnl']:+.02f}%"
                                        f"{new_part}"
                                    )
                                else:
                                    lines.append(
                                        f"      ▲ {row['th']:>8g}(조임):"
                                        f" {row['n']}건({diff_sign}{diff_n})"
                                        f" W{row['wins']}/L{row['losses']}"
                                        f" 승률{row['wr']:.0f}%({wr_sign}{diff_wr:.0f}%p)"
                                        f" PnL{row['avg_pnl']:+.02f}%"
                                    )
            if _blocked_pending > 0:
                lines.append(f"  📎 {_blocked_pending}개 필터 수집 중 (10건 미만)")
    with _SHADOW_LOCK:
        active_b = len(_SHADOW_BLOCKED_POSITIONS)
    if active_b > 0:
        lines.append(f"  ⏳ 차단건 추적 중: {active_b}건")
    return lines


def _v4_shadow_reset_counters():
    """(비활성) 섀도우 통계는 누적 유지 — 초기화하지 않음"""
    pass


def v4_get_strategy_registry():
    """전략 레지스트리 조회 (외부 모듈/텔레그램 커맨드용)"""
    return {k: {kk: vv for kk, vv in v.items() if kk != "check_fn"}
            for k, v in _STRATEGY_REGISTRY.items()}


# --- 공개 API (v6 signal_v4 60일 WF 데이터 기반) ---

def v4_evaluate_entry(market, c5, c15, c30, c60, c1=None):
    """
    통합 진입 판정 — detect_leader_stock()에서 호출

    v0 리셋: m3 게이트 제거, 전략별 핵심 트리거 2개만
    [SHADOW] 9개 시나리오 전수 테스트 (게이트 없음)
    [LIVE] 레지스트리 우선순위 순, enabled만 실행
    """
    _pipeline_inc("v4_called")
    if not c1:
        return None

    # === v0: 섀도우 테스트 — 게이트 없이 전 시나리오 실행 ===
    try:
        _v4_shadow_test_all_routes(market, c1, c5, c15, c30, c60, None)
    except Exception as e:
        print(f"[SHADOW] 섀도우 테스트 오류: {e}")

    # === 라이브 전략 순회 (priority 순, enabled만) ===
    sorted_strategies = sorted(_STRATEGY_REGISTRY.items(), key=lambda x: x[1]["priority"])
    for strat_name, strat in sorted_strategies:
        if not strat["enabled"]:
            continue
        check_fn = strat["check_fn"]
        sig = check_fn(c1, c5, c15, c30, c60, gate_info=None)
        if sig:
            # ⭐ strat 정보로 override (같은 check_fn을 공유하는 strategy 분리 보장)
            #   예: G와 GT가 _v0_check_momentum_rsi 공유 → check_fn 반환값은 항상 G용
            #       → strat_name/exit_params/route를 등록 키 기준으로 덮어써야 GT exit 적용됨
            sig["signal_tag"] = strat_name  # 예: "모멘텀GT"
            sig["exit_params"] = strat.get("exit_params", sig.get("exit_params", _V4_DEFAULT_EXIT)).copy()
            sig["logic_group"] = strat.get("route", sig.get("logic_group", "?"))
            _pipeline_inc("v4_raw_hit")
            _pipeline_hourly_inc("raw_hit")
            _pipeline_strategy_pass(strat_name)
            _pipeline_record_signal_coin(market, strat_name)
            _shadow_log_write(now_kst_str(), market, strat_name, 1, "", 1, "")
            return sig
        _pipeline_inc(f"{strat['pipeline_key']}_fail")

    return None


def v4_get_exit_params(signal_tag):
    """시그널 태그별 청산 파라미터 반환 (v0: 레지스트리 우선 → _V4_EXIT_PARAMS fallback)"""
    # v0: 레지스트리에서 exit_params 직접 참조
    if signal_tag in _STRATEGY_REGISTRY:
        base = _STRATEGY_REGISTRY[signal_tag]["exit_params"].copy()
    else:
        base = _V4_EXIT_PARAMS.get(signal_tag, _V4_DEFAULT_EXIT).copy()
    return mfe_feedback_exit_params(signal_tag, base)


def v4_is_favorable_hour(hour, signal_tag):
    """시간대 필터 — WF 데이터에 시간대별 결과 없음 → 전 시간 허용"""
    return True


# ============ strategy_v4 통합 끝 ============


def calc_vwap_from_candles(candles, lookback=20):
    """
    캔들 기반 VWAP 계산 (Volume Weighted Average Price)
    - 최근 N봉의 거래량 가중 평균가
    - VWAP 위: 강세 편향 / VWAP 아래: 약세 편향
    Returns: vwap_price (float) or None
    """
    target = candles[-lookback:] if len(candles) >= lookback else candles
    if not target:
        return None
    total_vp = 0.0
    total_vol = 0.0
    for c in target:
        # 대표가 = (고+저+종)/3
        typical = (c.get("high_price", 0) + c.get("low_price", 0) + c.get("trade_price", 0)) / 3
        # 🔧 FIX: VWAP은 코인수량(trade_volume) 가중 — 기존 trade_price(원화거래대금)은 price² 가중
        vol = c.get("candle_acc_trade_volume", 0)
        total_vp += typical * vol
        total_vol += vol
    return total_vp / max(total_vol, 1) if total_vol > 0 else None


def vol_ma_from_candles(candles, period=20):
    """최근 N봉 거래량 평균 (거래대금 기준)"""
    if len(candles) < period:
        return 0
    vols = [c.get("candle_acc_trade_price", 0) for c in candles[-period:]]
    return sum(vols) / len(vols) if vols else 0


def prev_high_from_candles(candles, lookback=12, skip_recent=1):
    """최근 N봉 중 고점 (최근 skip_recent봉 제외)"""
    if len(candles) < lookback + skip_recent:
        return 0
    subset = candles[-(lookback + skip_recent):-skip_recent] if skip_recent > 0 else candles[-lookback:]
    if not subset:
        return 0
    return max(c.get("high_price", 0) for c in subset)


# ========================================
# 🔥 점화 감지 (Ignition Detection) - 급등 초입 0~30초 내 감지
# ========================================
def update_baseline_tps(market: str, ticks, window_sec: int = 300):
    """
    평시 틱/초 (baseline TPS) 업데이트
    - 최근 5분간 틱 데이터로 평균 ticks-per-second 계산
    - 점화 감지의 상대 임계치 기준으로 사용
    """
    if not ticks or len(ticks) < 10:
        return

    # 🔧 FIX: tick_ts_ms 헬퍼로 통일 (timestamp/ts 키 + 초/ms 방어)
    now_ts = max(tick_ts_ms(t) for t in ticks)
    cutoff = now_ts - (window_sec * 1000)

    # window_sec 내의 틱만 필터
    window_ticks = [t for t in ticks if tick_ts_ms(t) >= cutoff]

    if len(window_ticks) < 5:
        return

    # 🔧 FIX: 분모를 window_sec로 고정 (last-first 사용 시 틱 몰림→TPS 과대추정→점화 누락)
    ts_list = [tick_ts_ms(t) for t in window_ticks]
    first_ts = min(ts_list)
    last_ts = max(ts_list)
    coverage = (last_ts - first_ts) / 1000.0

    # 🔧 FIX: coverage 체크 — 데이터가 윈도우의 40% 미만이면 업데이트 스킵 (오염 방지)
    if coverage < window_sec * 0.4:
        return

    tps = len(window_ticks) / max(window_sec, 1)

    with _IGNITION_LOCK:
        # 지수이동평균으로 부드럽게 업데이트
        old_tps = _IGNITION_BASELINE_TPS.get(market, tps)
        new_tps = old_tps * 0.8 + tps * 0.2
        # 🔧 FIX: 바운드 제한 (점화 이벤트 시 baseline 과도 오염 방지)
        _IGNITION_BASELINE_TPS[market] = max(0.1, min(new_tps, 50.0))


def ignition_detected(
    market: str,
    ticks,
    avg_candle_volume: float,
    ob=None,
    cooldown_ms: int = 10000
) -> tuple:
    """
    점화 감지: 급등 시작 0~30초 내 감지

    4요건 중 3개 충족 시 점화 (폭발적 급등 감지):
    1. 틱 폭주: 최근 10초 틱수 >= 평시의 4배 (강화: 3→4배)
    2. 연속 매수: 10초 내 7회 이상 연속 매수 (강화: 5→7회)
    3. 가격 임펄스: 0.5% 이상 상승 + 최근 6틱 단조증가 (강화: 0.3→0.5%)
    4. 거래량 폭발: 10초 거래량 >= 1분평균의 40% (강화: 25→40%)

    추가 필터:
    - 스프레드 안정성 (평시 2배 이하)
    - 쿨다운 (15초간 재점화 금지)

    Returns: (is_ignition, reason, score)
    """
    if not ticks or len(ticks) < 10:
        return False, "틱부족", 0

    # 🔧 FIX: tick_ts_ms 헬퍼로 통일 (timestamp/ts 키 + 초/ms 방어)
    now_ts = max(tick_ts_ms(t) for t in ticks)
    if now_ts == 0:
        now_ts = int(time.time() * 1000)

    # ---- 쿨다운 체크 ----
    with _IGNITION_LOCK:
        last_signal = _IGNITION_LAST_SIGNAL.get(market, 0)
        if (now_ts - last_signal) < cooldown_ms:
            return False, f"쿨다운({(cooldown_ms - (now_ts - last_signal)) / 1000:.1f}초)", 0

    # ---- 최근 10초 윈도우 추출 ----
    cutoff_10s = now_ts - 10000
    window = [t for t in ticks if tick_ts_ms(t) >= cutoff_10s]

    if len(window) < 6:
        return False, "10초윈도우부족", 0

    # ---- 1) 틱 폭주 (상대 임계치) ----
    with _IGNITION_LOCK:
        baseline_tps = _IGNITION_BASELINE_TPS.get(market, 0.5)  # 기본값 0.5 tps

    t10 = micro_tape_stats_from_ticks(window, 10)  # 🔧 FIX: ticks→window (10초 윈도우 단일 기준)
    # 🔧 강화: 평시의 4배 이상, 최소 15틱 (폭발적 급등 감지)
    tps_threshold = max(IGN_TPS_MIN_TICKS, IGN_TPS_MULTIPLIER * baseline_tps * 10)
    tps_burst = t10["n"] >= tps_threshold

    # ---- 2) 연속 매수 (10초 윈도우) ----
    # 🔧 강화: 5회 → 7회 (폭발적 매수세만 감지)
    consec_buys = calc_consecutive_buys(window, 10) >= IGN_CONSEC_BUY_MIN

    # ---- 3) 가격 임펄스 (수익률 + 대부분 상승) ----
    # 🔧 FIX: 명시적 시간순 정렬 (API 순서 의존 제거 → 오탐/누락 방지)
    sorted_window = sorted(window, key=tick_ts_ms)
    prices = [t.get("trade_price", 0) for t in sorted_window]  # 오래된 → 최신
    prices = [p for p in prices if p > 0]
    if len(prices) >= 6:
        ret = (prices[-1] / prices[0]) - 1 if prices[0] > 0 else 0
        # 🔧 완화: 5틱 중 4틱 이상 상승 (기존: 6틱 모두 상승)
        up_count = sum(1 for a, b in zip(prices[-6:-1], prices[-5:]) if b > a)
        mostly_up = up_count >= IGN_UP_COUNT_MIN
        price_impulse = (ret >= IGN_PRICE_IMPULSE_MIN) and mostly_up
    else:
        ret = 0
        price_impulse = False

    # ---- 4) 거래량 폭발 (10초 거래량 >= 1분평균의 40% AND 절대금액 >= 3M원) ----
    # 🔧 강화: 25% → 40% (폭발적 거래량만 감지)
    # 🔧 FIX: 절대 거래대금 하한 추가 (저거래량 코인 노이즈 신호 차단)
    #   - 기존: 상대적 증가만 체크 → 1분평균 500K인 코인이 5.6배=2.8M에도 점화
    #   - 추가: 10초간 최소 3M원 이상 실거래 필요 (절대 유동성 보장)
    _vol_relative = t10["krw"] >= IGN_VOL_BURST_RATIO * avg_candle_volume if avg_candle_volume > 0 else False
    _vol_absolute = t10["krw"] >= IGN_MIN_ABS_KRW_10S
    vol_burst = _vol_relative and _vol_absolute

    # ---- 스프레드 안정성 필터 (옵션) ----
    spread_ok = True
    if ob and ob.get("spread", 0) > 0:
        # 🔧 0.5% → 0.40% 강화 (점화 구간은 슬립 커지므로 더 엄격히)
        spread_ok = ob["spread"] <= IGN_SPREAD_MAX

    # ---- 점수 계산 ----
    score = sum([tps_burst, consec_buys, price_impulse, vol_burst])

    # ---- 점화 판정: 4요건 중 3개 이상 + 스프레드 양호 ----
    # 🔧 4/4 → 3/4로 완화 (개별 조건이 강화됐으므로, 폭발적 급등 유연하게 감지)
    is_ignition = (score >= 3) and spread_ok

    if is_ignition:
        # 마지막 신호 시각 기록
        with _IGNITION_LOCK:
            _IGNITION_LAST_SIGNAL[market] = now_ts

    # 상세 reason 생성
    details = []
    details.append(f"틱{'✓' if tps_burst else '✗'}({t10['n']:.0f}>={tps_threshold:.0f})")
    details.append(f"연매{'✓' if consec_buys else '✗'}")
    details.append(f"가격{'✓' if price_impulse else '✗'}({ret*100:.2f}%)")
    details.append(f"거래량{'✓' if vol_burst else '✗'}")
    if not spread_ok:
        details.append("스프레드✗")

    reason = ",".join(details)

    return is_ignition, reason, score


def atr14_from_candles(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high_price"]
        l = candles[i]["low_price"]
        pc = candles[i - 1]["trade_price"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs[-period:]) / period if len(trs) >= period else None


# =========================
# ★ 그라인드(계단식 상승) 예외
# =========================
# === PATCH: grind detector ===
def is_mega_breakout(c1):
    if not ULTRA_RELAX_ON_MEGA or len(c1) < 6:
        return False
    cur = c1[-1]
    prev_high = max(x["high_price"] for x in c1[-6:-1])
    gap = cur["high_price"] / max(prev_high, 1) - 1
    chg_1m = cur["trade_price"] / max(c1[-2]["trade_price"], 1) - 1 if len(
        c1) >= 2 else 0
    z = zscore_krw_1m(c1, 30)
    abs_krw = cur.get("candle_acc_trade_price", 0)
    return (gap >= MEGA_BREAK_MIN_GAP) and (chg_1m >= MEGA_MIN_1M_CHG) and (
        (z >= MEGA_VOL_Z) or (abs_krw >= MEGA_ABS_KRW))


# =========================
# 🎯 리테스트 진입 함수들
# =========================
def add_to_retest_watchlist(m, peak_price, pre):
    """
    첫 급등 감지 시 워치리스트에 등록
    🔧 강화: 첫 파동 품질 검증 + 5분 EMA 추세 필수
    """
    if not RETEST_MODE_ENABLED:
        return

    # --- 첫 파동 품질 검증: ignition_score≥3 OR (turn/imb/buy_ratio 중 2개 이상 강함) ---
    ign_score = pre.get("ignition_score", 0)
    _br = pre.get("buy_ratio", 0)
    _imb = pre.get("imbalance", 0)
    _turn = pre.get("turn_pct", 0)

    strong_count = 0
    if _br >= 0.60:
        strong_count += 1
    if _imb >= 0.40:
        strong_count += 1
    if _turn >= 5.0:
        strong_count += 1

    first_wave_real = (ign_score >= 3) or (strong_count >= 2)
    if not first_wave_real:
        print(f"[RETEST] {m} 첫 파동 품질 미달 (ign={ign_score}, br={_br:.2f}, imb={_imb:.2f}, turn={_turn:.1f}) → 등록 거부")
        return

    # --- 5분 EMA 추세 정렬 확인: EMA5 > EMA20 + gap ≥ RETEST_EMA_GAP_MIN ---
    try:
        c5 = get_minutes_candles(5, m, 25)
        if c5 and len(c5) >= 20:
            closes_5m = [x["trade_price"] for x in c5]  # oldest→newest (get_minutes_candles가 이미 reversed)
            ema5_val = ema_last(closes_5m, 5)
            ema20_val = ema_last(closes_5m, 20)
            if ema5_val and ema20_val and ema20_val > 0:
                ema_gap = (ema5_val - ema20_val) / ema20_val
                if ema_gap < RETEST_EMA_GAP_MIN:
                    print(f"[RETEST] {m} 5분 EMA 정렬 미달 (EMA5-EMA20 gap={ema_gap*100:.2f}% < {RETEST_EMA_GAP_MIN*100:.1f}%) → 등록 거부")
                    return
            else:
                print(f"[RETEST] {m} 5분 EMA 계산 실패 → 등록 거부")
                return
        else:
            print(f"[RETEST] {m} 5분 캔들 부족 → 등록 거부")
            return
    except Exception as e:
        print(f"[RETEST] {m} 5분 EMA 조회 실패: {e} → 등록 거부")
        return

    with _RETEST_LOCK:
        if m in _RETEST_WATCHLIST:
            return  # 이미 등록됨
        _RETEST_WATCHLIST[m] = {
            "peak_price": peak_price,
            "peak_ts": time.time(),
            "pullback_low": peak_price,  # 되돌림 저점 추적
            "state": "watching",  # watching → pullback → bounce → ready
            "pre": pre,
            "entry_price": pre.get("price", peak_price),  # 원래 신호 가격
            # 🔧 첫 파동 메타데이터 (로그/디버그용)
            "reg_ign_score": ign_score,
            "reg_buy_ratio": _br,
            "reg_imbalance": _imb,
            "reg_turn_pct": _turn,
        }
        print(f"[RETEST] {m} 워치리스트 등록 | 고점 {peak_price:,.0f}원 | ign={ign_score} br={_br:.2f} imb={_imb:.2f} turn={_turn:.1f} | 리테스트 대기")


def check_retest_entry(m):
    """
    리테스트 조건 체크 → 진입 가능하면 pre 반환, 아니면 None
    🔧 강화: 5분 EMA 추세 이탈 폐기 / 거래량 사망 폐기 / 재돌파 확인형 진입
    """
    if not RETEST_MODE_ENABLED:
        return None

    if is_coin_loss_cooldown(m):
        return None

    with _RETEST_LOCK:
        watch = _RETEST_WATCHLIST.get(m)
        if not watch:
            return None

        # 타임아웃 체크
        elapsed = time.time() - watch["peak_ts"]
        if elapsed > RETEST_TIMEOUT_SEC:
            print(f"[RETEST] {m} 타임아웃 ({elapsed:.0f}초) → 워치리스트 제거")
            _RETEST_WATCHLIST.pop(m, None)
            return None

        peak_price = watch["peak_price"]
        entry_price = watch["entry_price"]
        state = watch["state"]

    # 현재가 조회
    try:
        cur_js = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": m})
        if not cur_js or len(cur_js) == 0:
            return None
        cur_price = cur_js[0].get("trade_price", 0)
    except Exception:
        return None

    if cur_price <= 0:
        return None

    # 되돌림 저점 업데이트
    with _RETEST_LOCK:
        watch = _RETEST_WATCHLIST.get(m)
        if not watch:
            return None
        if cur_price < watch["pullback_low"]:
            watch["pullback_low"] = cur_price
        pullback_low = watch["pullback_low"]

    # 고점 대비 하락률
    pullback_pct = (peak_price - pullback_low) / peak_price if peak_price > 0 else 0
    # 저점 대비 반등률
    bounce_pct = (cur_price - pullback_low) / pullback_low if pullback_low > 0 else 0

    # =====================================================
    # 🔧 공통 안전 필터: 5분 EMA 추세 이탈 → 즉시 폐기
    # (눌림 중이든 반등 중이든, 5분 추세가 깨지면 알파 소멸)
    # =====================================================
    if state in ("pullback", "bounce"):
        try:
            c5 = get_minutes_candles(5, m, 25)
            if c5 and len(c5) >= 20:
                closes_5m = [x["trade_price"] for x in c5]  # oldest→newest (get_minutes_candles가 이미 reversed)
                ema5_val = ema_last(closes_5m, 5)
                ema20_val = ema_last(closes_5m, 20)
                if ema5_val and ema20_val and ema5_val < ema20_val:
                    with _RETEST_LOCK:
                        _RETEST_WATCHLIST.pop(m, None)
                    print(f"[RETEST] {m} 5분 EMA 추세 이탈 (EMA5 {ema5_val:,.0f} < EMA20 {ema20_val:,.0f}) → 폐기")
                    return None
        except Exception:
            pass  # API 에러 시 다음 사이클에서 재확인

    # =====================================================
    # 🔧 눌림 중 거래량 사망 체크 → 폐기
    # (krw_per_sec 바닥이면 관심 소멸 = 재상승 기대 불가)
    # =====================================================
    if state in ("pullback", "bounce"):
        try:
            ticks = get_recent_ticks(m, 100)
            if ticks and len(ticks) >= 5:
                t15_stats = micro_tape_stats_from_ticks(ticks, 15)
                if t15_stats["krw_per_sec"] < RETEST_KRW_PER_SEC_DEAD:
                    with _RETEST_LOCK:
                        _RETEST_WATCHLIST.pop(m, None)
                    print(f"[RETEST] {m} 거래량 사망 (krw/s={t15_stats['krw_per_sec']:,.0f} < {RETEST_KRW_PER_SEC_DEAD:,}) → 폐기")
                    return None
        except Exception:
            pass

    # 상태 전이 로직
    with _RETEST_LOCK:
        watch = _RETEST_WATCHLIST.get(m)
        if not watch:
            return None
        # 🔧 FIX: 상태 전이 직전에 state 재읽기 (안전필터에서 다른 스레드가 변경 가능)
        state = watch["state"]

        if state == "watching":
            # 충분히 되돌림이 왔는지 체크
            if pullback_pct >= RETEST_PULLBACK_MIN:
                watch["state"] = "pullback"
                print(f"[RETEST] {m} 되돌림 감지 | -{pullback_pct*100:.2f}% | state→pullback")

        elif state == "pullback":
            # 너무 많이 빠졌으면 제거
            if pullback_pct > RETEST_PULLBACK_MAX:
                print(f"[RETEST] {m} 과도한 되돌림 -{pullback_pct*100:.2f}% > {RETEST_PULLBACK_MAX*100:.1f}% → 제거")
                _RETEST_WATCHLIST.pop(m, None)
                return None

            # 반등 시작 체크
            if bounce_pct >= RETEST_BOUNCE_MIN:
                watch["state"] = "bounce"
                print(f"[RETEST] {m} 반등 감지 | +{bounce_pct*100:.2f}% | state→bounce")

        elif state == "bounce":
            # 🔧 FIX: API 호출은 락 바깥에서 수행 (아래에서 처리)
            pass

        elif state == "ready":
            # 진입 조건 충족!
            # 🔧 FIX: shallow copy 후 수정 (원본 pre dict 오염 방지 — circle_check_entry와 동일 패턴)
            pre = dict(watch.get("pre", {}))
            pre["retest_entry"] = True  # 리테스트 진입 마킹
            pre["price"] = cur_price  # 현재가로 업데이트
            pre["entry_mode"] = "half"  # 🔧 리테스트 진입은 half 강제 (burst와 리스크-리턴 구조 다름)
            pre["signal_type"] = "retest"  # 🔧 FIX: SL 완화용 (circle과 동일 구조)
            _RETEST_WATCHLIST.pop(m, None)  # 워치리스트에서 제거
            print(f"[RETEST] {m} 🎯 리테스트 진입 신호! | 고점 {peak_price:,.0f} → 저점 {pullback_low:,.0f} → 현재 {cur_price:,.0f} | half 강제")
            return pre

    # -----------------------------------------------
    # 🔧 FIX: bounce → ready API 호출을 락 바깥에서 수행 (네트워크 지연 시 블로킹 방지)
    # -----------------------------------------------
    if state == "bounce":
        support_ok = cur_price >= entry_price * 0.995
        if support_ok and bounce_pct >= RETEST_BOUNCE_MIN:
            reentry_ok = True
            reject_reasons = []

            try:
                ticks = get_recent_ticks(m, 100)
                ob_raw = safe_upbit_get("https://api.upbit.com/v1/orderbook", {"markets": m})
                ob = None
                if ob_raw and len(ob_raw) > 0:
                    _units = ob_raw[0].get("orderbook_units", [])
                    if _units:
                        ob = {
                            "spread": ((_units[0]["ask_price"] - _units[0]["bid_price"]) /
                                       ((_units[0]["ask_price"] + _units[0]["bid_price"]) / 2) * 100)
                                      if _units[0]["ask_price"] > 0 else 999,
                            "raw": ob_raw[0],
                        }

                if ticks and len(ticks) >= 5:
                    t10_stats = micro_tape_stats_from_ticks(ticks, 10)

                    if t10_stats["buy_ratio"] < RETEST_BUY_RATIO_MIN:
                        reentry_ok = False
                        reject_reasons.append(f"br={t10_stats['buy_ratio']:.2f}<{RETEST_BUY_RATIO_MIN}")

                    fresh_ok_rt = last_two_ticks_fresh(ticks)
                    if not fresh_ok_rt:
                        reentry_ok = False
                        reject_reasons.append("fresh_fail")

                    if not uptick_streak_from_ticks(ticks, need=2):
                        reentry_ok = False
                        reject_reasons.append("no_uptick")
                else:
                    reentry_ok = False
                    reject_reasons.append("ticks_insufficient")

                if ob and ob.get("raw"):
                    imb_rt = calc_orderbook_imbalance(ob)
                    if imb_rt < RETEST_IMBALANCE_MIN:
                        reentry_ok = False
                        reject_reasons.append(f"imb={imb_rt:.2f}<{RETEST_IMBALANCE_MIN}")

                if ob and ob.get("spread", 999) > RETEST_SPREAD_MAX:
                    reentry_ok = False
                    reject_reasons.append(f"spread={ob['spread']:.2f}>{RETEST_SPREAD_MAX}")

            except Exception as e:
                reentry_ok = False
                reject_reasons.append(f"api_err:{e}")

            # 🔧 FIX: 상태 전이는 락 안에서 (API 후 상태 재검증)
            with _RETEST_LOCK:
                watch = _RETEST_WATCHLIST.get(m)
                if watch and watch["state"] == "bounce":
                    if reentry_ok:
                        watch["state"] = "ready"
                        print(f"[RETEST] {m} 재돌파 확인 ✓ | 현재가 {cur_price:,.0f} | br/imb/fresh/uptick/spread 모두 통과 | state→ready")
                    else:
                        print(f"[RETEST] {m} 재진입 품질 미달 [{', '.join(reject_reasons)}] | bounce 유지")

    return None


def cleanup_retest_watchlist():
    """타임아웃된 항목 정리"""
    if not RETEST_MODE_ENABLED:
        return
    with _RETEST_LOCK:
        now = time.time()
        expired = [m for m, w in _RETEST_WATCHLIST.items()
                   if now - w["peak_ts"] > RETEST_TIMEOUT_SEC]
        for m in expired:
            print(f"[RETEST] {m} 타임아웃 → 워치리스트 제거")
            _RETEST_WATCHLIST.pop(m, None)


def is_morning_session():
    """장초 시간대인지 확인 (08:00~10:00)"""
    try:
        cur_hour = now_kst().hour
        return RETEST_MORNING_HOURS[0] <= cur_hour < RETEST_MORNING_HOURS[1]
    except Exception:
        return False


# =====================================================
# ⭕ 동그라미 엔트리 V1 (Circle Entry Engine)
# =====================================================
# 패턴: Ignition → 1~6봉 첫 눌림 → 리클레임 → 재돌파
# 상태: armed → pullback → reclaim → ready
# 기존 로직과 완전 독립 — 별도 워치리스트/상태머신
# =====================================================

def circle_register(m, pre, c1):
    """
    ⭕ 동그라미 워치리스트 등록
    점화 감지 후 호출 — 눌림→리클레임→재돌파 감시 시작

    등록 조건:
    - ignition_score >= CIRCLE_MIN_IGN_SCORE
    - 1분봉 데이터 충분 (최소 3개)
    - 5분 EMA 추세 정렬 (EMA5 > EMA20)
    """
    if not CIRCLE_ENTRY_ENABLED:
        return

    ign_score = pre.get("ignition_score", 0)
    if ign_score < CIRCLE_MIN_IGN_SCORE:
        return

    if not c1 or len(c1) < 3:
        return

    # 이미 등록되어 있으면 스킵
    with _CIRCLE_LOCK:
        if m in _CIRCLE_WATCHLIST:
            return

    # 5분 EMA 추세 정렬 확인
    try:
        c5 = get_minutes_candles(5, m, 25)
        if c5 and len(c5) >= 20:
            closes_5m = [x["trade_price"] for x in c5]  # oldest→newest (get_minutes_candles가 이미 reversed)
            ema5_val = ema_last(closes_5m, 5)
            ema20_val = ema_last(closes_5m, 20)
            if ema5_val and ema20_val and ema5_val < ema20_val:
                print(f"[CIRCLE] {m} 5분 EMA 역배열 → 등록 거부")
                return
        else:
            print(f"[CIRCLE] {m} 5분 캔들 부족 → 등록 거부")
            return
    except Exception as e:
        print(f"[CIRCLE] {m} 5분 EMA 조회 실패: {e} → 등록 거부")
        return

    # 🔧 WF데이터 Phase3: 60m RSI 35~70 레짐 필터 (과매도/과매수 구간 제외)
    try:
        _c60_circle = get_minutes_candles(60, m, 15) or []
        _regime_ok, _rsi_60 = _v4_regime_filter_60m(_c60_circle)
        if not _regime_ok:
            print(f"[CIRCLE] {m} 60m RSI={_rsi_60} 레짐 필터 미충족 (35~70 필요) → 등록 거부")
            return
    except Exception as e:
        print(f"[CIRCLE] {m} 60m 레짐 필터 오류: {e} → 등록 거부")
        return

    # 🔧 WF데이터 Phase3: VR5 > 3.0 필수 (거래량 동반 없는 점화 = 노이즈)
    if c1 and len(c1) >= 6:
        _vr5_circle = _v4_volume_ratio_5(c1)
        if _vr5_circle <= 3.0:
            print(f"[CIRCLE] {m} VR5={_vr5_circle:.1f} ≤ 3.0 → 등록 거부")
            return

    # 점화 캔들 정보 추출 (현재 캔들 = 점화 캔들)
    ign_candle = c1[-1]
    ign_high = ign_candle["high_price"]
    ign_low = ign_candle["low_price"]
    ign_open = ign_candle["opening_price"]
    ign_close = ign_candle["trade_price"]
    ign_body_top = max(ign_open, ign_close)
    ign_body_bot = min(ign_open, ign_close)
    ign_body_mid = (ign_body_top + ign_body_bot) / 2

    with _CIRCLE_LOCK:
        if m in _CIRCLE_WATCHLIST:
            return
        _CIRCLE_WATCHLIST[m] = {
            "state": "armed",
            "reg_ts": time.time(),
            "state_ts": time.time(),     # 현재 상태 진입 시각 (최소 체류 시간 체크용)
            "candle_count": 0,           # 점화 후 경과 봉 수
            "last_candle_ts": ign_candle.get("candle_date_time_kst", ""),
            # 점화 캔들 레벨 (핵심 기준선)
            "ign_high": ign_high,
            "ign_low": ign_low,
            "ign_body_top": ign_body_top,
            "ign_body_bot": ign_body_bot,
            "ign_body_mid": ign_body_mid,
            # 추적 변수
            "peak_after_ign": ign_high,   # 점화 이후 최고점
            "pullback_low": ign_high,     # 눌림 저점
            "reclaim_price": 0,           # 리클레임 확인 가격
            "was_below_reclaim": False,   # pullback 중 body_mid 아래 경험 여부
            # 원본 pre (진입 시 재사용)
            "pre": pre,
            # 메타데이터 (디버그용)
            "reg_ign_score": ign_score,
            "reg_buy_ratio": pre.get("buy_ratio", 0),
            "reg_volume_surge": pre.get("volume_surge", 0),
        }
        print(
            f"[CIRCLE] ⭕ {m} 워치리스트 등록 | ign={ign_score} "
            f"| 고점={ign_high:,.0f} 몸통중심={ign_body_mid:,.0f} 저점={ign_low:,.0f} "
            f"| br={pre.get('buy_ratio',0):.2f} surge={pre.get('volume_surge',0):.1f}x "
            f"| 눌림→리클레임→재돌파 감시 시작"
        )


def circle_check_entry(m):
    """
    ⭕ 동그라미 상태 전이 체크 → 진입 가능하면 pre 반환, 아니면 None

    상태 전이:
    armed    → pullback : 고점 대비 CIRCLE_PULLBACK_MIN_PCT 이상 하락
    pullback → reclaim  : 점화몸통중심 위로 회복 + 매수세 확인
    reclaim  → ready    : 점화고점 재돌파 + 플로우 품질 확인
    ready    → (진입)   : pre dict 반환

    안전 필터 (모든 상태에서):
    - 6봉 초과 시 폐기
    - 과도한 눌림 (CIRCLE_PULLBACK_MAX_PCT 초과) 시 폐기
    - 거래량 사망 시 폐기
    """
    if not CIRCLE_ENTRY_ENABLED:
        return None

    if is_coin_loss_cooldown(m):
        return None

    with _CIRCLE_LOCK:
        watch = _CIRCLE_WATCHLIST.get(m)
        if not watch:
            return None

        elapsed = time.time() - watch["reg_ts"]
        if elapsed > CIRCLE_TIMEOUT_SEC:
            print(f"[CIRCLE] {m} 타임아웃 ({elapsed:.0f}초) → 폐기")
            _CIRCLE_WATCHLIST.pop(m, None)
            return None

        state = watch["state"]
        ign_high = watch["ign_high"]
        ign_body_mid = watch["ign_body_mid"]
        ign_body_bot = watch["ign_body_bot"]

    # 현재 1분봉 조회 (봉 수 카운트 + 현재가)
    try:
        c1 = get_minutes_candles(1, m, 10)
        if not c1 or len(c1) < 2:
            return None
    except Exception:
        return None

    cur_candle = c1[-1]
    cur_price = cur_candle["trade_price"]
    cur_high = cur_candle["high_price"]
    cur_low = cur_candle["low_price"]

    if cur_price <= 0:
        return None

    # 봉 수 카운트 업데이트 (새 캔들이면 +1)
    cur_candle_ts = cur_candle.get("candle_date_time_kst", "")
    with _CIRCLE_LOCK:
        watch = _CIRCLE_WATCHLIST.get(m)
        if not watch:
            return None
        if cur_candle_ts and cur_candle_ts != watch["last_candle_ts"]:
            watch["candle_count"] += 1
            watch["last_candle_ts"] = cur_candle_ts

        candle_count = watch["candle_count"]

        # 최고점/최저점 업데이트
        if cur_high > watch["peak_after_ign"]:
            watch["peak_after_ign"] = cur_high
        if cur_low < watch["pullback_low"]:
            watch["pullback_low"] = cur_low

        peak = watch["peak_after_ign"]
        pullback_low = watch["pullback_low"]

    # --- 봉 수 초과 체크 ---
    if candle_count > CIRCLE_MAX_CANDLES:
        with _CIRCLE_LOCK:
            _CIRCLE_WATCHLIST.pop(m, None)
        print(f"[CIRCLE] {m} {candle_count}봉 초과 (최대 {CIRCLE_MAX_CANDLES}) → 폐기")
        return None

    # --- 눌림 퍼센트 계산 ---
    # pullback_pct_hist: 역대 최저점 기준 (구조 훼손 판정용 — MAX 체크, peak 기준)
    # pullback_pct_now:  현재가 기준 (실제 눌림 상태 판정용 — MIN 체크, 상태전이)
    # 🔧 FIX: MIN 눌림은 ign_high 기준 (peak_after_ign이면 추가 상승 후 얕은 눌림도 통과 위험)
    pullback_pct_hist = (peak - pullback_low) / peak if peak > 0 else 0
    pullback_pct_now = (ign_high - cur_price) / ign_high if ign_high > 0 else 0

    # --- was_below_reclaim 추적 (pullback 상태에서 body_mid 아래 경험 기록) ---
    with _CIRCLE_LOCK:
        watch = _CIRCLE_WATCHLIST.get(m)
        if watch and watch["state"] == "pullback" and cur_price < ign_body_mid:
            watch["was_below_reclaim"] = True

    # --- 과도한 눌림 체크 (역대 저점 기준 — 한번이라도 깊이 빠졌으면 구조 훼손) ---
    if pullback_pct_hist > CIRCLE_PULLBACK_MAX_PCT:
        with _CIRCLE_LOCK:
            _CIRCLE_WATCHLIST.pop(m, None)
        print(f"[CIRCLE] {m} 과도한 눌림 -{pullback_pct_hist*100:.2f}% > {CIRCLE_PULLBACK_MAX_PCT*100:.1f}% → 폐기")
        return None

    # --- 거래량 사망 체크 (pullback/reclaim 상태에서) ---
    if state in ("pullback", "reclaim"):
        try:
            ticks = get_recent_ticks(m, 100)
            if ticks and len(ticks) >= 5:
                t15_stats = micro_tape_stats_from_ticks(ticks, 15)
                if t15_stats["krw_per_sec"] < CIRCLE_REBREAK_KRW_PER_SEC_MIN * 0.5:
                    with _CIRCLE_LOCK:
                        _CIRCLE_WATCHLIST.pop(m, None)
                    print(f"[CIRCLE] {m} 거래량 사망 (krw/s={t15_stats['krw_per_sec']:,.0f}) → 폐기")
                    return None
        except Exception:
            pass

    # --- 5분 EMA 추세 이탈 체크 (pullback/reclaim 상태에서) ---
    # 🔧 FIX: 진행 단계에서는 gap 완충 적용 (EMA5 ≈ EMA20 구간에서 불필요 폐기 방지)
    if state in ("pullback", "reclaim"):
        try:
            c5 = get_minutes_candles(5, m, 25)
            if c5 and len(c5) >= 20:
                closes_5m = [x["trade_price"] for x in c5]  # oldest→newest (get_minutes_candles가 이미 reversed)
                ema5_val = ema_last(closes_5m, 5)
                ema20_val = ema_last(closes_5m, 20)
                if ema5_val and ema20_val:
                    ema_gap_pct = (ema5_val - ema20_val) / ema20_val if ema20_val > 0 else 0
                    # 등록 단계(circle_register)는 gap<0이면 즉시 거부
                    # 진행 단계에서는 -0.2% 이하일 때만 폐기 (노이즈 완충)
                    if ema_gap_pct < -0.002:
                        with _CIRCLE_LOCK:
                            _CIRCLE_WATCHLIST.pop(m, None)
                        print(f"[CIRCLE] {m} 5분 EMA 추세 이탈 (gap={ema_gap_pct*100:.2f}%) → 폐기")
                        return None
        except Exception:
            pass

    # =====================================================
    # 상태 전이 로직
    # =====================================================
    with _CIRCLE_LOCK:
        watch = _CIRCLE_WATCHLIST.get(m)
        if not watch:
            return None
        # 🔧 FIX: 상태 전이 직전에 state 재읽기 (안전필터에서 변경 가능성)
        state = watch["state"]

        # 상태 체류 시간 체크 (순간통과 방지)
        state_dwell = time.time() - watch.get("state_ts", 0)

        if state == "armed":
            # -----------------------------------------------
            # armed → pullback: 현재가 기준 고점 대비 충분한 눌림
            # pullback_pct_now 사용 (현재가 기준) — 윅 한번으로 통과 방지
            # -----------------------------------------------
            if state_dwell >= CIRCLE_STATE_MIN_DWELL_SEC and pullback_pct_now >= CIRCLE_PULLBACK_MIN_PCT:
                watch["state"] = "pullback"
                watch["state_ts"] = time.time()
                print(
                    f"[CIRCLE] ⭕ {m} 눌림 감지 | 현재가 기준 -{pullback_pct_now*100:.2f}% "
                    f"| 고점 {peak:,.0f} → 현재 {cur_price:,.0f} (저점 {pullback_low:,.0f}) "
                    f"| {candle_count}봉째 | state→pullback"
                )

        elif state == "pullback":
            # -----------------------------------------------
            # pullback → reclaim: 점화 몸통 하단 위로 회복
            # 🔧 FIX: was_below_reclaim 제거 — pullback 상태 진입 자체가 이미
            # 0.4% 눌림을 경험한 증거. body_mid 아래 요구는 과도 (진입 불가 원인)
            # reclaim 기준도 body_mid → body_bot 완화 (회복 확인만 하면 충분)
            # -----------------------------------------------
            reclaim_level = ign_body_bot  # 🔧 완화: body_mid → body_bot (몸통 하단)
            if CIRCLE_RECLAIM_LEVEL == "body_mid":
                reclaim_level = ign_body_mid  # 설정으로 되돌릴 수 있음

            if (state_dwell >= CIRCLE_STATE_MIN_DWELL_SEC
                    and cur_price >= reclaim_level):
                watch["state"] = "reclaim"
                watch["state_ts"] = time.time()
                watch["reclaim_price"] = cur_price
                print(
                    f"[CIRCLE] ⭕ {m} 리클레임 ✓ | 현재 {cur_price:,.0f} ≥ 기준선 {reclaim_level:,.0f} "
                    f"| 아래 체류 경험 ✓ | {candle_count}봉째 | state→reclaim"
                )

        elif state == "reclaim":
            # -----------------------------------------------
            # reclaim → ready: 조건 충족 여부만 확인 (API 호출은 락 바깥)
            # -----------------------------------------------
            pass  # API 호출이 필요하므로 아래에서 락 해제 후 처리

        elif state == "ready":
            # -----------------------------------------------
            # ready → 진입 신호 발생!
            # ⚠️ 여기서 pop 하지 않음 — 메인루프에서 진입 성공 후에만 pop
            # (OPEN_POSITIONS 차단 시 다음 사이클에서 재시도 가능)
            # -----------------------------------------------
            original_pre = watch.get("pre", {})
            entry_pre = dict(original_pre)
            entry_pre["circle_entry"] = True
            entry_pre["is_circle"] = True  # 🔧 FIX: final_price_guard/pullback 분기용 플래그
            entry_pre["is_surge_circle"] = watch.get("is_surge_circle", False)  # 🔧 차트분석: 폭발진입 플래그
            entry_pre["circle_state_path"] = "armed→pullback→reclaim→rebreak"
            entry_pre["circle_candles"] = candle_count
            entry_pre["circle_pullback_pct"] = pullback_pct_hist
            entry_pre["circle_ign_high"] = ign_high
            entry_pre["circle_reclaim_price"] = watch.get("reclaim_price", 0)
            entry_pre["price"] = cur_price
            entry_pre["entry_mode"] = "full" if watch.get("is_surge_circle") else CIRCLE_ENTRY_MODE  # 🔧 차트분석: 폭발진입은 full size
            # 🔧 FIX: 동그라미 전용 메타데이터 (TP/SL/매도 로직 분기용)
            entry_pre["signal_tag"] = "⭕동그라미"
            entry_pre["trade_type"] = "runner"   # 재돌파는 추세연장 성향
            entry_pre["signal_type"] = "circle"  # dynamic_stop_loss 완화 분기용

            # pop은 메인루프 circle_confirm_entry()에서 수행
            return entry_pre

    # -----------------------------------------------
    # 🔧 FIX: reclaim → ready API 호출을 락 바깥에서 수행 (네트워크 지연 시 블로킹 방지)
    # -----------------------------------------------
    # 🔧 FIX: 재돌파 기준 ign_high → ign_body_top (위꼬리 고점까지 넘기는 건 과도)
    # 점화 캔들의 몸통 상단만 넘기면 구조적 재돌파로 충분
    with _CIRCLE_LOCK:
        _w = _CIRCLE_WATCHLIST.get(m)
        ign_body_top = _w["ign_body_top"] if _w else ign_high
    rebreak_level = ign_body_top  # 몸통 상단 기준 재돌파
    # 🔧 FIX: 재돌파 시 현재 캔들이 양봉이어야 함 (음봉 윗꼬리 돌파 = 페이크)
    cur_candle_green = (cur_candle["trade_price"] > cur_candle["opening_price"])
    if state == "reclaim" and state_dwell >= CIRCLE_STATE_MIN_DWELL_SEC and cur_price >= rebreak_level and cur_candle_green:
        # === 🔧 차트분석: 폭발 종목 감지 (HOLO/STEEM형 9시 급등) ===
        # 실측: vol 45~1570x, 직전 1분 vol 2~5x 선행, 피크까지 2-4봉(10-20분), 피크 +6.6~13.3%
        # 동그라미 rebreak 시 "폭발"이면 품질점수 무시하고 즉시 진입
        _is_surge_circle = False
        try:
            _sc_c1 = get_minutes_candles(1, m, 5)
            if _sc_c1 and len(_sc_c1) >= 3:
                _sc_cur_vol = _sc_c1[-1].get("candle_acc_trade_price", 0)
                _sc_prev_avg = sum(c.get("candle_acc_trade_price", 0) for c in _sc_c1[:-1]) / max(len(_sc_c1)-1, 1)
                _sc_vol_spike = _sc_cur_vol / max(_sc_prev_avg, 1)
                _sc_body = abs(_sc_c1[-1].get("trade_price",0) - _sc_c1[-1].get("opening_price",0)) / max(_sc_c1[-1].get("opening_price",1), 1) * 100

                # 폭발 조건: vol 20x+ AND body 2%+ (HOLO: 1570x+8.96%, STEEM: 45x+8.75%)
                if _sc_vol_spike >= 20 and _sc_body >= 2.0:
                    _is_surge_circle = True
                    print(f"[CIRCLE_SURGE] {m} 폭발감지! vol {_sc_vol_spike:.0f}x body {_sc_body:.2f}% → 품질점수 무시 즉시진입")
                    # 폭발 시 full 사이즈 (일반 동그라미는 half)
        except Exception:
            pass

        rebreak_score = 0
        rebreak_details = []

        try:
            ticks = get_recent_ticks(m, 100)
            ob_raw = safe_upbit_get("https://api.upbit.com/v1/orderbook", {"markets": m})
            ob = None
            if ob_raw and len(ob_raw) > 0:
                _units = ob_raw[0].get("orderbook_units", [])
                if _units:
                    ob = {
                        "spread": ((_units[0]["ask_price"] - _units[0]["bid_price"]) /
                                   ((_units[0]["ask_price"] + _units[0]["bid_price"]) / 2) * 100)
                                  if _units[0]["ask_price"] > 0 else 999,
                        "raw": ob_raw[0],
                    }

            if ticks and len(ticks) >= 5:
                t10_stats = micro_tape_stats_from_ticks(ticks, 10)

                # (1) 매수비
                if t10_stats["buy_ratio"] >= CIRCLE_REBREAK_BUY_RATIO_MIN:
                    rebreak_score += 1
                    rebreak_details.append(f"br={t10_stats['buy_ratio']:.2f}✓")
                else:
                    rebreak_details.append(f"br={t10_stats['buy_ratio']:.2f}✗")

                # (2) 체결강도
                if t10_stats["krw_per_sec"] >= CIRCLE_REBREAK_KRW_PER_SEC_MIN:
                    rebreak_score += 1
                    rebreak_details.append(f"krw/s={t10_stats['krw_per_sec']:,.0f}✓")
                else:
                    rebreak_details.append(f"krw/s={t10_stats['krw_per_sec']:,.0f}✗")

                # (3) uptick 모멘텀
                if uptick_streak_from_ticks(ticks, need=2):
                    rebreak_score += 1
                    rebreak_details.append("uptick✓")
                else:
                    rebreak_details.append("uptick✗")
            else:
                rebreak_details.append("ticks_insufficient")

            # (4) 임밸런스
            if ob and ob.get("raw"):
                imb_val = calc_orderbook_imbalance(ob)
                if imb_val >= CIRCLE_REBREAK_IMBALANCE_MIN:
                    rebreak_score += 1
                    rebreak_details.append(f"imb={imb_val:.2f}✓")
                else:
                    rebreak_details.append(f"imb={imb_val:.2f}✗")

            # (5) 스프레드
            if ob and ob.get("spread", 999) <= CIRCLE_REBREAK_SPREAD_MAX:
                rebreak_score += 1
                rebreak_details.append(f"sp={ob['spread']:.2f}✓")
            elif ob:
                rebreak_details.append(f"sp={ob.get('spread',0):.2f}✗")

        except Exception as e:
            rebreak_details.append(f"api_err:{e}")

        # 🔧 재돌파 VWAP/EMA5 하드필터 (추격 제거)
        # 재돌파 = 추세연장인데 VWAP/EMA5 밑이면 되돌림 페이크
        _circle_vwap_ok = True
        try:
            _c1_circle = get_minutes_candles(1, m, 30)
            if _c1_circle and len(_c1_circle) >= 10:
                _vwap_circle = calc_vwap_from_candles(_c1_circle, 20)
                # EMA5 계산 (종가 기반)
                _closes = [c.get("trade_price", 0) for c in _c1_circle if c.get("trade_price", 0) > 0]
                _ema5 = None
                if len(_closes) >= 5:
                    _ema5 = _closes[-5]
                    _k = 2.0 / (5 + 1)
                    for _cp in _closes[-4:]:
                        _ema5 = _cp * _k + _ema5 * (1 - _k)

                if _vwap_circle and cur_price < _vwap_circle:
                    _circle_vwap_ok = False
                    rebreak_details.append(f"VWAP하회({cur_price:,.0f}<{_vwap_circle:,.0f})✗")
                elif _ema5 and cur_price < _ema5:
                    _circle_vwap_ok = False
                    rebreak_details.append(f"EMA5하회({cur_price:,.0f}<{_ema5:,.0f})✗")
                elif _vwap_circle and _vwap_circle > 0:
                    _vgap_circle = (cur_price / _vwap_circle - 1.0) * 100
                    if _vgap_circle > 1.0:
                        _circle_vwap_ok = False
                        rebreak_details.append(f"VWAP추격({_vgap_circle:.1f}%>1.0%)✗")
                    else:
                        rebreak_details.append(f"VWAP+EMA5✓({_vgap_circle:.1f}%)")
        except Exception:
            pass  # API 실패 시 필터 비활성 (기존 로직 유지)

        # 🔧 NEW: 노이즈 방어 — ATR 바닥 + 임밸런스 하드플로어
        _circle_noise_ok = True
        try:
            _c1_atr = get_minutes_candles(1, m, 20) or []
            if _c1_atr and len(_c1_atr) >= 15:
                _atr_raw = atr14_from_candles(_c1_atr, 14)
                if _atr_raw and cur_price > 0:
                    _atr_pct = _atr_raw / cur_price
                    if _atr_pct < CIRCLE_ATR_FLOOR:
                        _circle_noise_ok = False
                        rebreak_details.append(f"ATR{_atr_pct*100:.2f}%<{CIRCLE_ATR_FLOOR*100:.1f}%✗")
            # 임밸런스 하드플로어 (스코어 통과와 무관하게 차단)
            if ob and ob.get("raw"):
                _imb_hard = calc_orderbook_imbalance(ob)
                if _imb_hard < CIRCLE_IMB_HARD_FLOOR:
                    _circle_noise_ok = False
                    rebreak_details.append(f"imb_hard={_imb_hard:.2f}<{CIRCLE_IMB_HARD_FLOOR}✗")
        except Exception:
            pass

        # 🔧 FIX: 상태 전이는 락 안에서 (API 후 상태 재검증)
        with _CIRCLE_LOCK:
            watch = _CIRCLE_WATCHLIST.get(m)
            if watch and watch["state"] == "reclaim":
                if (_is_surge_circle or rebreak_score >= CIRCLE_REBREAK_MIN_SCORE) and _circle_vwap_ok and _circle_noise_ok:
                    watch["state"] = "ready"
                    watch["state_ts"] = time.time()
                    watch["is_surge_circle"] = _is_surge_circle  # 🔧 차트분석: 폭발진입 플래그 저장
                    print(
                        f"[CIRCLE] ⭕ {m} 재돌파 확인 ✓ | 현재 {cur_price:,.0f} ≥ 몸통상단 {rebreak_level:,.0f} "
                        f"| 품질 {rebreak_score}/5 ({','.join(rebreak_details)}) "
                        f"| {candle_count}봉째 | state→ready"
                    )
                else:
                    _reasons = []
                    if rebreak_score < CIRCLE_REBREAK_MIN_SCORE:
                        _reasons.append(f"품질{rebreak_score}/5<{CIRCLE_REBREAK_MIN_SCORE}")
                    if not _circle_vwap_ok:
                        _reasons.append("VWAP/EMA5필터")
                    if not _circle_noise_ok:
                        _reasons.append("노이즈필터(ATR/임밸)")
                    _fail_reason = ",".join(_reasons) or "알수없음"
                    print(
                        f"[CIRCLE] {m} 재돌파 미달 ({_fail_reason}) "
                        f"[{','.join(rebreak_details)}] | reclaim 유지"
                    )

    return None


def circle_cleanup():
    """⭕ 타임아웃/만료된 동그라미 워치리스트 정리"""
    if not CIRCLE_ENTRY_ENABLED:
        return
    with _CIRCLE_LOCK:
        now = time.time()
        expired = [m for m, w in _CIRCLE_WATCHLIST.items()
                   if now - w["reg_ts"] > CIRCLE_TIMEOUT_SEC]
        for m in expired:
            print(f"[CIRCLE] {m} 타임아웃 → 워치리스트 제거")
            _CIRCLE_WATCHLIST.pop(m, None)


def circle_confirm_entry(m):
    """⭕ 동그라미 진입 확정 시 워치리스트에서 제거 (메인루프에서 호출)"""
    with _CIRCLE_LOCK:
        _CIRCLE_WATCHLIST.pop(m, None)


# =========================
# 📦 박스권 매매 엔진 (Box Range Trading Engine)
# =========================

def detect_box_range(c1, lookback=None):
    """
    📦 박스권 감지: N봉 캔들에서 박스 상단/하단 식별 (엄격 검증)

    핵심: 단순 범위 체크가 아닌, 실제 횡보 패턴인지 다중 검증
    1. 범위 + BB폭 기본 체크
    2. 비연속 터치 (같은 영역 연속 체류는 1회로)
    3. 중간선 교차 횟수 (진짜 왕복 = 횡보 확인)
    4. 종가 선형회귀 기울기 (추세 있으면 박스 아님)
    5. 종가 집중도 (중앙 60%에 종가 80%+ 밀집)

    Returns: (is_box, box_info)
    """
    lookback = lookback or BOX_LOOKBACK
    if not c1 or len(c1) < lookback:
        return False, {}

    candles = c1[-lookback:]
    highs = [c["high_price"] for c in candles]
    lows = [c["low_price"] for c in candles]
    closes = [c["trade_price"] for c in candles]
    volumes = [c.get("candle_acc_trade_price", 0) for c in candles]

    box_high = max(highs)
    box_low = min(lows)
    if box_low <= 0:
        return False, {}

    range_pct = (box_high - box_low) / box_low
    box_range = box_high - box_low

    # 범위 체크
    if range_pct < BOX_MIN_RANGE_PCT or range_pct > BOX_MAX_RANGE_PCT:
        return False, {}

    # 볼린저밴드 폭 체크
    bb_width = 0.0
    if len(closes) >= 20:
        sma20 = sum(closes[-20:]) / 20
        if sma20 > 0:
            variance = sum((c - sma20) ** 2 for c in closes[-20:]) / 20
            std20 = variance ** 0.5
            bb_width = (4 * std20) / sma20
    if not math.isfinite(bb_width) or bb_width < BOX_MIN_BB_WIDTH or bb_width > BOX_MAX_BB_WIDTH:
        return False, {}

    # ===== 🔧 강화1: 비연속 터치 (연속 봉이 같은 영역이면 1회로 카운트) =====
    touch_zone = box_range * BOX_TOUCH_ZONE_PCT
    top_zone = box_high - touch_zone
    bot_zone = box_low + touch_zone

    # 비연속 터치 카운트: 이전 봉이 해당 영역 밖이었을 때만 새 터치
    top_touches = 0
    bot_touches = 0
    prev_in_top = False
    prev_in_bot = False
    for i in range(len(highs)):
        in_top = (highs[i] >= top_zone)
        in_bot = (lows[i] <= bot_zone)
        if in_top and not prev_in_top:
            top_touches += 1
        if in_bot and not prev_in_bot:
            bot_touches += 1
        prev_in_top = in_top
        prev_in_bot = in_bot

    if top_touches < BOX_MIN_TOUCHES or bot_touches < BOX_MIN_TOUCHES:
        return False, {}

    # ===== 🔧 강화2: 중간선 교차 횟수 (진짜 왕복 확인) =====
    mid_price = (box_high + box_low) / 2
    mid_crosses = 0
    prev_above = (closes[0] >= mid_price) if closes else True
    for c in closes[1:]:
        cur_above = (c >= mid_price)
        if cur_above != prev_above:
            mid_crosses += 1
        prev_above = cur_above

    if mid_crosses < BOX_MIN_MIDCROSS:
        return False, {}

    # ===== 🔧 강화3: 종가 선형회귀 기울기 (추세 필터) =====
    # 추세가 있으면 박스가 아님 — 기울기가 박스 범위의 0.3% 이하여야
    n = len(closes)
    if n >= 10:
        x_mean = (n - 1) / 2.0
        y_mean = sum(closes) / n
        numerator = sum((i - x_mean) * (closes[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator > 0 and y_mean > 0:
            slope = numerator / denominator
            # 기울기를 %/봉 단위로 변환, 전체 구간 기울기
            total_slope_pct = abs(slope * n / y_mean)
            if total_slope_pct > BOX_MAX_TREND_SLOPE:
                return False, {}

    # ===== 🔧 강화4: 종가 집중도 (중앙 60%에 80%+ 밀집) =====
    inner_top = box_high - box_range * 0.20
    inner_bot = box_low + box_range * 0.20
    closes_in_inner = sum(1 for c in closes if inner_bot <= c <= inner_top)
    close_ratio = closes_in_inner / max(len(closes), 1)
    if close_ratio < BOX_MIN_CLOSE_IN_RANGE:
        return False, {}

    # 거래대금 체크
    total_vol = sum(volumes)
    avg_vol = total_vol / max(len(volumes), 1)
    if total_vol < BOX_MIN_VOL_KRW:
        return False, {}

    # 현재가가 박스 안에 있는지
    cur_price = closes[-1]
    if cur_price < box_low or cur_price > box_high:
        return False, {}

    return True, {
        "box_high": box_high,
        "box_low": box_low,
        "range_pct": range_pct,
        "top_touches": top_touches,
        "bot_touches": bot_touches,
        "mid_crosses": mid_crosses,
        "close_ratio": close_ratio,
        "avg_vol": avg_vol,
        "total_vol": total_vol,
        "bb_width": bb_width,
        "cur_price": cur_price,
    }


def box_scan_markets(c1_cache):
    """
    📦 박스권 종목 스캔 — 메인루프에서 주기적 호출
    박스가 감지된 종목을 워치리스트에 등록
    """
    global _BOX_LAST_SCAN_TS
    if not BOX_ENABLED:
        return

    now = time.time()
    if now - _BOX_LAST_SCAN_TS < BOX_SCAN_INTERVAL:
        return
    _BOX_LAST_SCAN_TS = now

    for m, c1 in c1_cache.items():
        if not c1:
            continue

        # 이미 돌파 포지션 보유 중이면 스킵
        with _POSITION_LOCK:
            if m in OPEN_POSITIONS:
                continue

        # 이미 박스 워치리스트에 있으면 스킵
        with _BOX_LOCK:
            if m in _BOX_WATCHLIST:
                continue

        # 쿨다운 체크
        with _BOX_LOCK:
            last_exit = _BOX_LAST_EXIT.get(m, 0)  # 🔧 FIX: _BOX_LOCK 안에서 읽기
        if now - last_exit < BOX_COOLDOWN_SEC:
            continue

        # 스테이블코인 제외
        ticker = m.upper().split("-")[-1] if "-" in m else m.upper()
        if ticker in {"USDT", "USDC", "DAI", "TUSD", "BUSD"}:
            continue

        # 🔧 FIX: 5분봉 기반 박스 감지 (1분봉 노이즈 제거, 뚜렷한 패턴만)
        if BOX_USE_5MIN:
            try:
                c5 = get_minutes_candles(5, m, BOX_LOOKBACK)
                if not c5 or len(c5) < BOX_LOOKBACK:
                    continue
                box_candles = c5
            except Exception:
                continue
        else:
            if len(c1) < BOX_LOOKBACK:
                continue
            box_candles = c1

        is_box, box_info = detect_box_range(box_candles)
        if not is_box:
            continue

        # 박스 워치리스트 등록
        with _BOX_LOCK:
            if m in _BOX_WATCHLIST:
                continue

            # 박스 포지션 수 체크
            box_pos_count = sum(1 for w in _BOX_WATCHLIST.values() if w.get("state") == "holding")
            if box_pos_count >= BOX_MAX_POSITIONS:
                continue

            _BOX_WATCHLIST[m] = {
                "state": "watching",  # watching → ready → holding
                "reg_ts": now,
                "box_high": box_info["box_high"],
                "box_low": box_info["box_low"],
                "range_pct": box_info["range_pct"],
                "bb_width": box_info["bb_width"],
                "avg_vol": box_info["avg_vol"],
                "top_touches": box_info["top_touches"],
                "bot_touches": box_info["bot_touches"],
            }
            print(
                f"[BOX] 📦 {m} 박스 감지 | "
                f"상단 {box_info['box_high']:,.0f} 하단 {box_info['box_low']:,.0f} "
                f"({box_info['range_pct']*100:.1f}%) | "
                f"터치 상{box_info['top_touches']}회 하{box_info['bot_touches']}회 | "
                f"중간교차 {box_info.get('mid_crosses', 0)}회 | "
                f"종가집중 {box_info.get('close_ratio', 0)*100:.0f}% | "
                f"BB폭 {box_info['bb_width']*100:.1f}%"
            )


def box_check_entry(m):
    """
    📦 박스 하단 진입 체크 → 진입 가능하면 pre dict 반환

    조건:
    1. 현재가가 박스 하단 25% 영역 이내
    2. 매수세 확인 (반등 시작)
    3. 박스가 여전히 유효 (이탈 안 함)
    """
    if not BOX_ENABLED:
        return None

    if is_coin_loss_cooldown(m):
        return None

    with _BOX_LOCK:
        watch = _BOX_WATCHLIST.get(m)
        if not watch or watch["state"] != "watching":
            return None

        box_high = watch["box_high"]
        box_low = watch["box_low"]

    # 현재가 조회
    try:
        c1 = get_minutes_candles(1, m, 5)
        if not c1 or len(c1) < 2:
            return None
    except Exception:
        return None

    cur_price = c1[-1]["trade_price"]
    cur_low = c1[-1]["low_price"]
    if cur_price <= 0:
        return None

    box_range = box_high - box_low
    if box_range <= 0:
        return None

    # 박스 이탈 체크 (하방 돌파 → 폐기)
    if cur_price < box_low * (1 - BOX_SL_BUFFER_PCT):
        with _BOX_LOCK:
            _BOX_WATCHLIST.pop(m, None)
        print(f"[BOX] {m} 박스 하방 이탈 {cur_price:,.0f} < {box_low:,.0f} → 폐기")
        return None

    # 박스 상방 돌파 → 폐기 (돌파 전략이 처리)
    if cur_price > box_high * 1.003:
        with _BOX_LOCK:
            _BOX_WATCHLIST.pop(m, None)
        print(f"[BOX] {m} 박스 상방 돌파 {cur_price:,.0f} > {box_high:,.0f} → 폐기 (돌파전략으로)")
        return None

    # 🔧 WF데이터 Phase3: 60m RSI 35~70 레짐 필터
    try:
        _c60_box = get_minutes_candles(60, m, 15) or []
        _box_regime_ok, _box_rsi_60 = _v4_regime_filter_60m(_c60_box)
        if not _box_regime_ok:
            return None
    except Exception:
        return None

    # 🔧 WF데이터 Phase3: VR5 > 3.0 (거래량 동반 필수)
    if c1 and len(c1) >= 6:
        _vr5_box = _v4_volume_ratio_5(c1)
        if _vr5_box <= 3.0:
            return None

    # 진입 영역 체크: 박스 하단 25% 이내
    entry_ceiling = box_low + box_range * BOX_ENTRY_ZONE_PCT
    if cur_price > entry_ceiling:
        # 하단 영역 밖 → 체류 시간 초기화
        with _BOX_LOCK:
            w = _BOX_WATCHLIST.get(m)
            if w:
                w.pop("in_zone_since", None)
        return None  # 아직 하단 근처 아님

    # 🔧 하단 체류 확인 (BOX_CONFIRM_SEC초 연속 하단 영역 유지)
    with _BOX_LOCK:
        w = _BOX_WATCHLIST.get(m)
        if w:
            if "in_zone_since" not in w:
                w["in_zone_since"] = time.time()
                return None  # 첫 진입 — 체류 시간 누적 시작
            dwell = time.time() - w["in_zone_since"]
            if dwell < BOX_CONFIRM_SEC:
                return None  # 아직 체류 시간 미달

    # 매수세 확인 (반등 징후) — 🔧 캔들 기반 반등 + 틱 보조
    # 🔧 FIX: 캔들 기반 반등 1차 체크 (저점 갱신 실패 + 양봉 = mean-reversion 시그널)
    # 틱이 얇은 종목에서도 작동하고, 펌프 오진입도 방지
    candle_bounce = False
    try:
        if c1 and len(c1) >= 3:
            c_prev = c1[-2]
            c_cur = c1[-1]
            # 직전봉 대비 저점이 높아지고(저점 갱신 실패) + 현재봉 양봉
            no_lower_low = (c_cur["low_price"] >= c_prev["low_price"])
            cur_bullish = (c_cur["trade_price"] > c_cur["opening_price"])
            candle_bounce = (no_lower_low and cur_bullish)
    except Exception:
        pass

    try:
        ticks = get_recent_ticks(m, 100)
        if not ticks or len(ticks) < 8:
            # 🔧 FIX: 틱 부족해도 캔들 반등 확인되면 진입 허용
            if not candle_bounce:
                return None
        else:
            t10 = micro_tape_stats_from_ticks(ticks, 10)
            t30 = micro_tape_stats_from_ticks(ticks, 30)
            tick_count = len(ticks)

            # 🔧 FIX: 틱 수에 따라 매수비 기준 보정 (틱 적으면 왜곡 가능)
            buy_ratio_thr = 0.53 if tick_count >= 30 else 0.58
            if t10["buy_ratio"] < buy_ratio_thr:
                if not candle_bounce:  # 캔들 반등 확인되면 틱 조건 완화
                    return None
            if t10["krw_per_sec"] < 5000:
                if not candle_bounce:
                    return None

            # 반등 가속도 확인
            flow_accel = calc_flow_acceleration(ticks)
            if flow_accel < 1.0:
                if not candle_bounce:
                    return None

            # 연속매수 확인
            cons_buys = calc_consecutive_buys(ticks, 10)
            if cons_buys < 3:
                if not candle_bounce:
                    return None

            # 30초 매수비 확인
            if t30["buy_ratio"] < 0.48:
                if not candle_bounce:
                    return None
    except Exception:
        if not candle_bounce:
            return None

    # 호가 확인 (스프레드)
    try:
        ob_raw = safe_upbit_get("https://api.upbit.com/v1/orderbook", {"markets": m})
        if not ob_raw or len(ob_raw) == 0:
            return None
        units = ob_raw[0].get("orderbook_units", [])
        if not units:
            return None
        spread = ((units[0]["ask_price"] - units[0]["bid_price"]) /
                  ((units[0]["ask_price"] + units[0]["bid_price"]) / 2) * 100)
        if spread > 0.25:  # 🔧 0.4→0.25% (박스 범위 대비 스프레드 부담 축소)
            return None
    except Exception:
        return None

    # 진입 준비 완료
    # 손절가: 박스 하단 -0.3%
    box_stop = box_low * (1 - BOX_SL_BUFFER_PCT)
    # 익절가: 박스 상단 근처 (상위 20% 영역 시작점)
    box_tp = box_high - box_range * BOX_EXIT_ZONE_PCT

    sl_pct = (cur_price - box_stop) / cur_price  # 손절 퍼센트

    entry_pre = {
        "price": cur_price,
        "signal_tag": "📦박스하단",
        "signal_type": "box",
        "trade_type": "box",
        "entry_mode": BOX_ENTRY_MODE,
        "is_box": True,
        "box_high": box_high,
        "box_low": box_low,
        "box_stop": box_stop,
        "box_tp": box_tp,
        "box_sl_pct": sl_pct,
        "box_range_pct": watch.get("range_pct", 0),
        "buy_ratio": t10["buy_ratio"],
        "volume_surge": 1.0,
        "spread": spread,
        "tape": t10,
        "ticks": ticks,
        "ob": {"spread": spread, "depth_krw": 0, "raw": ob_raw[0] if ob_raw else {}},
        "imbalance": 0,
        "turn_pct": 0,
        "current_volume": 0,
        "filter_type": "box_range",
    }

    with _BOX_LOCK:
        watch = _BOX_WATCHLIST.get(m)
        if watch:
            watch["state"] = "ready"
            watch["ready_ts"] = time.time()
            watch["entry_price"] = cur_price

    print(
        f"[BOX] 📦 {m} 하단 진입 신호! | "
        f"현재 {cur_price:,.0f} (하단 {box_low:,.0f}~{entry_ceiling:,.0f}) | "
        f"TP {box_tp:,.0f} SL {box_stop:,.0f} | "
        f"매수비 {t10['buy_ratio']:.0%}"
    )

    return entry_pre


def box_monitor_position(m, entry_price, volume, box_info):
    """
    📦 박스 포지션 모니터: 상단 익절 / 하단 손절 / 박스 이탈 감시

    기존 monitor_position과 독립 — 박스 전용 간단 로직
    """
    box_high = box_info["box_high"]
    box_low = box_info["box_low"]
    box_tp = box_info["box_tp"]
    box_stop = box_info["box_stop"]
    box_range = box_high - box_low

    start_ts = time.time()
    # 🔧 시간만료 제거: 박스매매는 박스 유지되는 한 시간 제한 없음 (추세 이탈만 청산)
    # 대신 박스 유효성 주기적 체크로 대체

    print(f"[BOX_MON] 📦 {m} 모니터 시작 | 진입 {entry_price:,.0f} | TP {box_tp:,.0f} SL {box_stop:,.0f}")

    sell_reason = ""
    partial_sold = False      # 부분 익절 여부
    remaining_vol = volume    # 남은 수량
    breakout_trail = False    # 돌파 트레일 모드
    trail_peak = 0            # 트레일 최고점
    # 🔧 FIX: 부분익절 실현손익 누적 (최종 손익 계산 정확도 보장)
    realized_krw = 0.0        # 부분매도 실현 금액 누적
    realized_vol = 0.0        # 부분매도 체결 수량 누적

    while True:
        time.sleep(1.5)

        try:
            c1 = get_minutes_candles(1, m, 3)
            if not c1:
                continue
            cur_price = c1[-1]["trade_price"]
        except Exception:
            continue

        # 🔧 포지션 상태 체크 (외부에서 이미 청산된 경우)
        # 🔧 FIX: API 호출을 락 밖으로 이동 (데드락 방지)
        _box_pos_missing = False
        with _POSITION_LOCK:
            if m not in OPEN_POSITIONS:
                _box_pos_missing = True
        if _box_pos_missing:
            _actual_bal = get_balance_with_locked(m)
            if _actual_bal is not None and _actual_bal > 0:
                remaining_vol = _actual_bal
                sell_reason = "📦 포지션 이탈 감지 (잔고 존재→청산)"
                with _POSITION_LOCK:
                    OPEN_POSITIONS[m] = {
                        "state": "open", "entry_price": entry_price,
                        "volume": _actual_bal, "strategy": "box",
                    }
                print(f"[BOX_MON] {m} OPEN_POSITIONS 이탈 but 잔고 {_actual_bal:.6f} → 청산 진행")
            elif _actual_bal is not None and _actual_bal < 0:
                # 🔧 FIX: API 실패 → 다음 루프에서 재확인
                print(f"[BOX_MON] {m} 잔고 조회 실패 → 다음 루프 대기")
                continue
            else:
                sell_reason = "📦 외부 청산 감지"
                remaining_vol = 0
            break

        cur_gain = (cur_price / entry_price - 1) if entry_price > 0 else 0

        # === 돌파 트레일 모드 ===
        if breakout_trail:
            if cur_price > trail_peak:
                trail_peak = cur_price
            trail_drop = (trail_peak - cur_price) / trail_peak if trail_peak > 0 else 0
            # 고점 대비 0.5% 하락하면 나머지 익절
            if trail_drop >= 0.005:
                sell_reason = f"📦 돌파 트레일 익절 (고점 {trail_peak:,.0f} → {cur_price:,.0f})"
                break
            continue

        # 1) 익절: 박스 상단 영역 도달 → 70% 부분익절
        if cur_price >= box_tp and not partial_sold:
            partial_vol = remaining_vol * 0.70
            try:
                _partial_res = place_market_sell(m, partial_vol)
                remaining_vol -= partial_vol
                partial_sold = True
                # 🔧 FIX: 부분매도 실현금액 누적 (정확한 손익 계산)
                realized_krw += cur_price * partial_vol  # 체결가 근사치
                realized_vol += partial_vol
                # 🔧 FIX: OPEN_POSITIONS volume 동기화 (크래시 복구 시 이중매도 방지)
                with _POSITION_LOCK:
                    _bp = OPEN_POSITIONS.get(m)
                    if _bp:
                        _bp["volume"] = remaining_vol
                print(f"[BOX_MON] 📦 {m} 상단 부분익절 70% | 실현 {realized_krw:,.0f}원 | 나머지 {remaining_vol:.6f}")
                _partial_gain = (cur_price / entry_price - 1) * 100 if entry_price > 0 else 0
                tg_send(
                    f"💰 <b>부분익절 70%</b> {m}\n"
                    f"• 현재가: {fmt6(cur_price)}원 ({_partial_gain:+.2f}%)\n"
                    f"• 나머지 30% 돌파 대기\n"
                    f"{link_for(m)}"
                )
            except Exception as pe:
                print(f"[BOX_MON] 부분매도 실패: {pe}")
                sell_reason = f"📦 박스 상단 익절 (부분매도실패→전량)"
                break
            continue

        # 2) 부분익절 후 박스 돌파 → 트레일 모드
        if partial_sold and cur_price > box_high * 1.002:
            breakout_trail = True
            trail_peak = cur_price
            print(f"[BOX_MON] 📦 {m} 돌파! 트레일 시작 | 고점 {cur_price:,.0f}")
            continue

        # 3) 부분익절 후 다시 하락 → 나머지도 청산
        if partial_sold and cur_price < box_tp - box_range * 0.15:
            sell_reason = f"📦 부분익절 후 하락 → 나머지 청산"
            break

        # 4) 손절: 박스 하단 이탈
        if cur_price <= box_stop:
            sell_reason = f"📦 박스 하단 이탈 (SL {box_stop:,.0f})"
            break

    # 나머지 수량 매도
    try:
        if remaining_vol > 0:
            sell_result = place_market_sell(m, remaining_vol)
        else:
            sell_result = {"uuid": ""}  # 이미 전량 부분매도됨
        time.sleep(0.5)

        # 매도가 조회 (Private API — get_order_result 사용)
        try:
            order_id = sell_result.get("uuid", "")
            if order_id:
                od = get_order_result(order_id, timeout_sec=10.0)
                if od and od.get("avg_price"):
                    sell_price = float(od["avg_price"])
                elif od and od.get("trades"):
                    trades = od["trades"]
                    total_krw = sum(float(tr["price"]) * float(tr["volume"]) for tr in trades)
                    total_vol = sum(float(tr["volume"]) for tr in trades)
                    sell_price = total_krw / total_vol if total_vol > 0 else cur_price
                else:
                    sell_price = cur_price
            else:
                sell_price = cur_price
        except Exception:
            sell_price = cur_price

        # 🔧 FIX: 부분익절 실현금액을 합산한 정확한 손익 계산
        # 기존: sell_price * volume (마지막 매도가로 전체 계산 → 부분익절 무시)
        # 변경: realized_krw(부분매도 누적) + sell_price * remaining_vol(나머지) = 실제 총 매도금액
        hold_sec = time.time() - start_ts
        est_entry_value = entry_price * volume
        final_sell_krw = sell_price * remaining_vol if remaining_vol > 0 else 0.0
        est_exit_value = realized_krw + final_sell_krw  # 부분+나머지 합산
        pl_value = est_exit_value - est_entry_value
        # 가중평균 매도가 (기록/학습용)
        avg_exit_price = est_exit_value / volume if volume > 0 else sell_price
        gross_ret_pct = (avg_exit_price / entry_price - 1.0) * 100.0 if entry_price > 0 else 0.0
        net_ret_pct = gross_ret_pct - (FEE_RATE_ROUNDTRIP * 100.0)
        fee_total = (est_entry_value + est_exit_value) * FEE_RATE_ONEWAY
        net_pl_value = pl_value - fee_total
        result_emoji = "🟢" if net_ret_pct > 0 else "🔴"

        # 🔧 거래 결과 기록 (승률 기반 리스크 튜닝)
        try:
            record_trade(m, net_ret_pct / 100.0, "박스")
        except Exception as _e:
            print(f"[BOX_TRADE_RECORD_ERR] {_e}")

        # 🔧 FIX: AUTO_LEARN_ENABLED 무관하게 항상 호출 (배치 리포트 카운터)
        try:
            update_trade_result(m, sell_price, net_ret_pct / 100.0, hold_sec,
                                exit_reason=sell_reason)
        except Exception as _e:
            print(f"[BOX_FEATURE_UPDATE_ERR] {_e}")

        # 🔧 FIX: 일반 매매와 동일한 헤더 형식
        tg_send(
            f"====================================\n"
            f"{result_emoji} <b>자동청산 완료</b> {m}\n"
            f"====================================\n"
            f"💰 순손익: {net_pl_value:+,.0f}원 (gross:{gross_ret_pct:+.2f}% / net:{net_ret_pct:+.2f}%)\n"
            f"📊 매매차익: {pl_value:+,.0f}원 → 수수료 {fee_total:,.0f}원 차감 → 실현손익 {net_pl_value:+,.0f}원\n\n"
            f"• 사유: {sell_reason}\n"
            f"• 매수평단: {fmt6(entry_price)}원\n"
            f"• 실매도가: {fmt6(sell_price)}원\n"
            f"• 체결수량: {volume:.6f}\n"
            f"• 매수금액: {est_entry_value:,.0f}원\n"
            f"• 청산금액: {est_exit_value:,.0f}원\n"
            f"• 수수료: {fee_total:,.0f}원 (매수 {est_entry_value * FEE_RATE_ONEWAY:,.0f} + 매도 {est_exit_value * FEE_RATE_ONEWAY:,.0f})\n"
            f"• 보유시간: {hold_sec:.0f}초\n"
            f"• 박스: {fmt6(box_low)}~{fmt6(box_high)} ({box_info.get('range_pct', 0)*100:.1f}%)\n"
            f"====================================\n"
            f"{link_for(m)}"
        )

        print(f"[BOX_MON] 📦 {m} 매도 완료 | {sell_reason} | PnL net:{net_ret_pct:+.2f}% | {hold_sec:.0f}초")

    except Exception as e:
        print(f"[BOX_MON] 📦 {m} 매도 실패: {e}")
        tg_send(f"⚠️ <b>자동청산 실패</b> {m}\n사유: {e}")
        # 🔧 FIX: 매도 실패 시 잔고 확인 → 코인 남아있으면 OPEN_POSITIONS 유지 (유령 방지)
        try:
            _fail_bal = get_balance_with_locked(m)
            if _fail_bal is not None and _fail_bal > 0:
                print(f"[BOX_MON] 📦 {m} 매도 실패 but 잔고 {_fail_bal:.6f} 존재 → 포지션 유지 (유령 전환 방지)")
                tg_send(f"⚠️ {m} 매도 실패 → 포지션 유지 중 (다음 동기화에서 재시도)")
                with _BOX_LOCK:
                    _BOX_WATCHLIST.pop(m, None)
                return  # mark_position_closed 호출하지 않음 → OPEN_POSITIONS 유지
        except Exception:
            pass

    # 정리 (매도 성공 시에만 도달)
    with _BOX_LOCK:
        _BOX_WATCHLIST.pop(m, None)
        _BOX_LAST_EXIT[m] = time.time()  # 🔧 FIX: _BOX_LOCK 안에서 쓰기 (레이스컨디션 방지)

    mark_position_closed(m, f"box_close:{sell_reason}")

    # 🔧 FIX: 매도 완료 후 _ORPHAN_HANDLED 해제 (다음 진입 시 재감지 가능)
    with _ORPHAN_LOCK:
        _ORPHAN_HANDLED.discard(m)


def box_confirm_entry(m):
    """📦 박스 진입 확정 → state를 holding으로 변경"""
    with _BOX_LOCK:
        watch = _BOX_WATCHLIST.get(m)
        if watch:
            watch["state"] = "holding"


def box_cleanup():
    """📦 오래된 박스 워치리스트 정리 (30분 이상 watching인 것)"""
    if not BOX_ENABLED:
        return
    with _BOX_LOCK:
        now = time.time()
        expired = [m for m, w in _BOX_WATCHLIST.items()
                   if w.get("state") == "watching" and now - w.get("reg_ts", 0) > 1800]
        for m in expired:
            print(f"[BOX] {m} 30분 초과 → 워치리스트 제거")
            _BOX_WATCHLIST.pop(m, None)


# =========================
# 허수 방어 / 점화 / 조기 브레이크
# =========================
# =========================
# 🔧 레짐 필터 (횡보장 진입 차단) — 현재 미사용, 참조용 유지
# =========================
def is_sideways_regime(c1, lookback=20):
    """
    횡보장 판정: 최근 N봉의 고저 범위 + 볼린저밴드 폭 복합 판정
    - 변동폭이 좁으면 횡보
    - 볼린저밴드 폭(BB width) < 1.0% = 횡보 (XRP 0.7% 같은 케이스 포착)
    - 횡보장에서 돌파 신호는 페이크 확률 높음
    """
    if len(c1) < lookback:
        return False, 0.0

    candles = c1[-lookback:]
    highs = [c["high_price"] for c in candles]
    lows = [c["low_price"] for c in candles]
    closes = [c["trade_price"] for c in candles]

    box_high = max(highs)
    box_low = min(lows)

    if box_low <= 0:
        return False, 0.0

    range_pct = (box_high - box_low) / box_low

    # 🔧 가격대별 횡보 판정 (고정 임계값)
    cur_price = candles[-1].get("trade_price", 0)
    if cur_price < 1000:
        sideways_thr = 0.008   # 1000원 미만: 0.8%
    else:
        sideways_thr = 0.005   # 1000원 이상: 0.5%

    # 🔧 NEW: 볼린저밴드 폭 기반 횡보 판정 (range만으론 XRP 횡보 못 잡음)
    # BB width = (upper - lower) / middle × 100
    # 좁은 BB = 저변동 = 횡보 (돌파 신호는 페이크)
    bb_sideways = False
    if len(closes) >= 20:
        sma20 = sum(closes[-20:]) / 20
        if sma20 > 0:
            variance = sum((c - sma20) ** 2 for c in closes[-20:]) / 20
            std20 = variance ** 0.5
            bb_width_pct = (2 * std20 * 2) / sma20  # (upper - lower) / middle
            if bb_width_pct < 0.010:  # BB 폭 1.0% 미만 = 횡보
                bb_sideways = True

    is_sideways = range_pct < sideways_thr or bb_sideways

    return is_sideways, range_pct


def calc_ema_slope(c1, period=20, lookback=5):
    """
    EMA 기울기 계산: 평평하면 횡보
    - 기울기 0.1% 미만 = 횡보
    """
    if len(c1) < period + lookback:
        return 0.0

    closes = [c["trade_price"] for c in c1]

    # 🔧 FIX: 전체 순회 1회로 ema_now + ema_prev(lookback 전 시점) 동시 추출
    mult = 2 / (period + 1)
    ema = closes[0]
    ema_prev = None
    prev_idx = len(closes) - 1 - lookback  # lookback 전 인덱스

    for i, p in enumerate(closes[1:], start=1):
        ema = p * mult + ema * (1 - mult)
        if i == prev_idx:
            ema_prev = ema

    if not ema_prev or ema_prev <= 0:
        return 0.0

    slope = (ema - ema_prev) / ema_prev
    return slope


def regime_filter(m, c1, cur_price):
    """
    통합 레짐 필터: 횡보장/박스상단이면 진입 차단
    Returns: (pass: bool, reason: str)
    """
    # 1) 횡보 판정 → 전면 차단 대신 "SIDEWAYS" 힌트 반환
    # 호출부(detect_leader_stock)에서 점화/강돌파 예외 판단
    is_sw, range_pct = is_sideways_regime(c1, lookback=20)
    if is_sw:
        return True, f"SIDEWAYS({range_pct*100:.1f}%)"  # 🔧 차단→힌트 (예외 통과 가능)

    # 2) 박스 상단 근처 판정 - 🔧 비활성화 (돌파 전략에서 고점 진입은 정상)
    # near_box, box_pos = near_box_boundary(cur_price, c1, lookback=20)
    # if near_box and box_pos == "BOX_TOP":
    #     return False, "BOX_TOP"

    # 3) EMA 기울기 판정 → 평평하면 entry_mode 다운그레이드
    slope = calc_ema_slope(c1, period=20, lookback=5)
    if abs(slope) < 0.001:  # 0.1% 미만 → 기울기 거의 없음
        # 🔧 FIX: no-op 제거 → "FLAT_SLOPE" 힌트 반환 (caller에서 half로 다운그레이드)
        return True, "FLAT_SLOPE"

    return True, "OK"


# === 🔧 틱버스트 허용 판단 (비활성화됨) ===
def detect_leader_stock(m, obc, c1, tight_mode=False):
    """
    하이브리드 진입 탐지 엔진:
      - Probe(소액): 완화된 early 흐름 감지 → 초기 염탐 진입
      - Confirm(추세): 강한 점화/매집/돌파 → 확정 진입
    """
    if len(c1) < 3:
        return None

    # === 🔧 스테이블코인 차단 (USDT, USDC 등 가격변동 없는 코인) ===
    _coin_ticker = m.upper().split("-")[-1] if "-" in m else m.upper()
    if _coin_ticker in {"USDT", "USDC", "DAI", "TUSD", "BUSD"}:  # 🔧 FIX: 정확매치 (부분문자열 오탐 방지)
        cut("STABLECOIN", f"{m} 스테이블코인 제외")
        _pipeline_inc("gate_fail_stablecoin")
        return None

    # === 동일 종목 중복 진입 방지 (포지션 보유 시 스킵) ===
    with _POSITION_LOCK:
        pos = OPEN_POSITIONS.get(m)
        if pos:
            # 🔧 FIX: 락 내부에서 체크해야 race condition 방지
            _pipeline_inc("gate_fail_position")
            return None

    # === 틱 기반 초봉(10초) 선행 진입 시그널 ===
    # 🔧 비활성화: tick_burst 경로 제거 (normal 경로로 통합)
    # - probe 진입 후 본진입 전략이 실제로 효과 없음
    # - tick_burst vs normal 임계치가 거의 동일해서 분리 의미 없음
    # ticks_now = get_recent_ticks(m, 80)
    # if ticks_now: ... (전체 tick_burst 로직 비활성화)

    # (이하 기존 detect_leader_stock 코드 계속)

    ob = obc.get(m)
    if not ob or not isinstance(ob.get("raw"), dict):
        return None
    if not ob.get("raw", {}).get("orderbook_units"):
        return None
    if ob.get("depth_krw", 0) <= 0:
        return None

    # 🔧 WF데이터: mega breakout 비활성화 (데이터에 없는 로직 — 거래대금 최소조건 바이패스 제거)
    mega = False  # is_mega_breakout(c1)
    cur, prev = c1[-1], c1[-2]
    # 🔧 FIX: 고가 기준 펌프 감지 추가 (종가만 보면 윗꼬리 펌프 놓침)
    price_change_close = (cur["trade_price"] / max(prev["trade_price"], 1) - 1)
    cur_high = cur.get("high_price", cur["trade_price"])
    pump_move = (cur_high / max(prev["trade_price"], 1) - 1)
    # 가중 합산: 고가 기준 70% 반영 (펌프 초반 감지)
    price_change = max(price_change_close, pump_move * 0.7)

    # 🔧 SPREAD_HIGH, VOL_LOW, SURGE_LOW, PRICE_LOW → stage1_gate로 이동
    # 거래량 데이터 (stage1_gate에서 사용)
    current_volume = cur.get("candle_acc_trade_price", 0)
    past_volumes = [c["candle_acc_trade_price"] for c in c1[-7:-2] if c["candle_acc_trade_price"] > 0]

    # 틱 확보
    ticks = get_recent_ticks(m, 100)
    if not ticks:
        cut("TICKS_LOW", f"{m} no ticks")
        _pipeline_inc("gate_fail_no_ticks")
        return None

    # 🔧 진입지연개선: 실시간 러닝바로 price_change 보강 (캔들 확정 전 조기 감지)
    _running = running_1m_bar(ticks, prev)
    if _running and _running.get("change_from_prev", 0) > price_change:
        _running_pc = _running["change_from_prev"]
        # 🔧 FIX: 스푸핑 방지 — 러닝바 가격변동이 비정상(5%초과)이면 무시
        if 0 < _running_pc <= 0.05:
            price_change = max(price_change, _running_pc * 0.9)
            # 러닝바 거래대금으로 current_volume도 보강
            _running_vol = _running.get("volume_krw", 0)
            # 🔧 FIX: 거래대금도 이전 평균의 10배 이내만 허용
            _vol_cap = max(current_volume, sum(past_volumes) / max(len(past_volumes), 1)) * 10
            if 0 < _running_vol <= _vol_cap and _running_vol > current_volume:
                current_volume = max(current_volume, _running_vol * 0.85)

    # 🔥 평시 TPS 업데이트 (점화 감지용)
    update_baseline_tps(m, ticks)

    # === 테이프 지표 (stage1_gate용) ===
    t15 = micro_tape_stats_from_ticks(ticks, 15)
    t45 = micro_tape_stats_from_ticks(ticks, 45)
    twin = t15 if t15["krw_per_sec"] >= t45["krw_per_sec"] else t45
    turn = twin["krw"] / max(ob["depth_krw"], 1)

    # 🔥 1단계 게이트 적용 (단일 통합 필터)
    # 🔧 FIX: SMA → EMA 기반 vol_surge (펌프 초반 더 빠른 반응)
    if past_volumes and len(past_volumes) >= 3:
        vol_ema = ema_last(past_volumes, min(len(past_volumes), 10))
        vol_surge_ema = current_volume / max(vol_ema, 1) if vol_ema else 1.0
        # 3분 누적 비교 추가 (c1[-3:] 최근 3분 vs 과거 평균)
        if len(c1) >= 6:
            sum_3 = sum(c["candle_acc_trade_price"] for c in c1[-3:])
            past_sums = []
            for i in range(max(0, len(c1)-15), len(c1)-3):
                if i >= 2:
                    s = sum(c["candle_acc_trade_price"] for c in c1[i-2:i+1])
                    past_sums.append(s)
            # 🔧 FIX: 표본 3개 미만이면 mean 신뢰도 부족 → EMA 폴백 (노이즈성 vol_surge 방지)
            vol_surge_3m = (sum_3 / max(statistics.mean(past_sums), 1)) if len(past_sums) >= 3 else vol_surge_ema
            vol_surge = max(vol_surge_ema, vol_surge_3m * 0.8)
        else:
            vol_surge = vol_surge_ema
    else:
        # 🔧 FIX: 데이터 부족 시 중립값 (8.0은 무조건 통과 티켓 → 오진입 유발)
        vol_surge = 1.0
    accel = calc_flow_acceleration(ticks)
    turn_pct = turn * 100  # decimal → %
    imbalance = calc_orderbook_imbalance(ob)
    fresh_ok, fresh_age, fresh_max_age = last_two_ticks_fresh(ticks, return_age=True)

    # 🔥 섀도우 모드용 지표 미리 계산
    ia = inter_arrival_stats(ticks, 60)  # 60초 윈도우 (샘플 충분해야 CV 안정)
    cv = ia["cv"]
    pstd10 = price_band_std(ticks, 10)  # 10초 윈도우 (현재 순간 흔들림 감지)
    # 🔧 FIX: None이면 None 유지 → gate에서 데이터 부족 시 pstd 체크 스킵
    cons_buys = calc_consecutive_buys(ticks, 15)
    overheat = accel * vol_surge
    spread = ob.get("spread", 9.9)

    # 🕯️ 캔들 모멘텀 지표 (stage1_gate 캔들 보너스용)
    candle_body_pct = (cur["trade_price"] / max(cur["opening_price"], 1) - 1)  # 종가-시가 %
    _green_streak = 0
    for _gc in reversed(c1):
        if _gc["trade_price"] > _gc["opening_price"]:
            _green_streak += 1
        else:
            break
    green_streak = _green_streak

    # 📊 윗꼬리 비율 계산 (180신호분석: uw<10% wr21.9%, 10-30% wr50.9% 최적)
    _uw_high = cur.get("high_price", cur["trade_price"])
    _uw_low = cur.get("low_price", cur["trade_price"])
    _uw_close = cur["trade_price"]
    _uw_open = cur["opening_price"]
    _uw_range = _uw_high - _uw_low
    if _uw_range > 0:
        upper_wick_ratio = (_uw_high - max(_uw_close, _uw_open)) / _uw_range
    else:
        upper_wick_ratio = 0.0

    # 🛑 하드 컷: 극단 스푸핑 패턴 (확신 구간만 차단)
    # buy_ratio >= 0.98 AND pstd <= 0.001 AND CV >= 2.5
    if twin["buy_ratio"] >= 0.98 and pstd10 is not None and pstd10 <= 0.001 and cv is not None and cv >= 2.5:
        cut("FAKE_FLOW_HARD", f"{m} buy{twin['buy_ratio']:.2f} pstd{pstd10:.4f} cv{cv:.2f}")
        _pipeline_inc("gate_fail_fake_flow")
        return None

    # 🚀 신규 조건 계산: EMA20 돌파, 고점 돌파, 거래량 MA 대비
    cur_price = cur["trade_price"]
    cur_high = cur.get("high_price", cur_price)  # 🔧 현재봉 고가
    closes = [x["trade_price"] for x in c1]
    ema20 = ema_last(closes, 20) if len(closes) >= 20 else None
    ema20_breakout = (ema20 is not None and cur_price > ema20)

    # 🔧 FIX: 고점 돌파 - 윗꼬리 오탐 방지 (점화 아닐 때는 종가 확인)
    prev_high = prev_high_from_candles(c1, lookback=12, skip_recent=1)
    high_breakout_wick = (prev_high > 0 and cur_high > prev_high)  # 고가 기준 (윅 포함)
    high_breakout_close = (prev_high > 0 and cur_price > prev_high * 1.0005)  # 종가 기준 (0.05% 버퍼)

    vol_ma20 = vol_ma_from_candles(c1, period=20)
    vol_vs_ma = current_volume / max(vol_ma20, 1) if vol_ma20 > 0 else 0.0

    # 🔥 점화 감지 점수 계산 (stage1_gate에 전달)
    _, ignition_reason, ignition_score = ignition_detected(
        market=m,
        ticks=ticks,
        avg_candle_volume=vol_ma20,
        ob=ob,
        cooldown_ms=15000
    )

    # 🔧 WF데이터: 20봉_고점돌파 비활성화됨 — high_breakout 판정 불필요
    high_breakout = False  # WF FAIL 시그널

    _ign_candidate = (ignition_score >= 3)

    # ============================================================
    # 🔧 WF 데이터 기반: strategy_v4.evaluate_entry() 통합 진입 판정
    # 활성: 거래량3배, 15m_눌림반전, EMA정배열진입 (GATE: 1h_EMA정배열+15m_MACD골든+60mRSI≥45)
    # ============================================================
    _entry_mode_override = None
    _v4_signal = None  # strategy_v4 반환값

    try:
        # 멀티TF 캔들 데이터 페칭 — 순차 3-fetch (병렬화 폐기 + c30 dead fetch 제거)
        # 이전 실험:
        #   - bc48961 (4-worker 병렬): per_call 220→545ms (rate limit), scan_detect 변화 없음
        #   - dd4385e (2-worker): per_call 334~428ms, scan_detect 65.4s 그대로
        #   - 결론: 병렬화의 rate limit 페널티가 동시 실행 이득을 상쇄
        # c30 제거 근거:
        #   - 모든 check_fn body에서 c30 사용 0회
        #   - _collect_universal_indicators body에서 c30 사용 0회
        #   - 100% dead fetch 확정 → 12.8s/cycle 절감
        # signature 호환성: _v4_shadow_test_all_routes(c30=...) 인자는 빈 list로 전달
        with _fetch_tag('detect_leader'):
            _c5  = get_minutes_candles(5,  m, 50) or []
            _c15 = _get_c15_cached(m, count=50)  # v18f Phase B: 전역 캐시 (TTL 60s)
            _c60 = _get_c60_cached(m, count=30)  # v18f: 전역 캐시 (TTL 300s)
        _c30 = []  # dead fetch 제거 (signature 호환성만 유지)

        # 📊 캔들 데이터 정합성 진단 (NaN/부족 원인 파악용)
        _tf_diag = (f"c1={len(c1)} c5={len(_c5)} c15={len(_c15)} "
                    f"c30={len(_c30)} c60={len(_c60)}")
        # MACD(26+9=35), ADX(14*2+1=29), EMA50(50), m3(7봉) 최소 길이 체크
        _tf_short = []
        if len(_c5) < 35:
            _tf_short.append(f"c5<35({len(_c5)})")
        if len(_c15) < 30:
            _tf_short.append(f"c15<30({len(_c15)})")
        if len(_c60) < 7:
            _tf_short.append(f"c60<7({len(_c60)})")
        if _tf_short:
            _shadow_log_write(now_kst_str(), m, "CANDLE_DIAG", 0,
                              "TF_SHORT:" + ",".join(_tf_short), 0, _tf_diag)

        # 시간대 필터 (불리한 시간 차단)
        _cur_hour = now_kst().hour

        # strategy_v4 통합 진입 판정 (c1 전달 — VR5/ATR% 계산용)
        _t_dv = time.time()
        _v4_signal = v4_evaluate_entry(m, _c5, _c15, _c30, _c60, c1=c1)
        _add_cycle_detect_v4_ms((time.time() - _t_dv) * 1000)

        if _v4_signal:
            # 시간대 필터 적용
            _time_ok = v4_is_favorable_hour(_cur_hour, _v4_signal["signal_tag"])
            if _time_ok is False:  # False=불리, None=중립, True=유리
                print(f"[V4_TIME_BAD] {m} {_v4_signal['signal_tag']} hour={_cur_hour} → 불리한 시간대")
                _pipeline_inc("v4_time_block")
                _v4_signal = None
    except Exception as _v4_err:
        print(f"[V4_ERR] {m} strategy_v4 오류: {_v4_err}")
        _v4_signal = None

    # v4 신호 없으면 → 진입 차단
    if not _v4_signal:
        cut("NO_V4_SIGNAL", f"{m} v4 진입 조건 미충족")
        _pipeline_inc("gate_fail_no_v4")
        _pipeline_coin_hit(m, "no_v4")
        return None

    _15m_signal = _v4_signal["signal_tag"]
    _entry_mode_override = _v4_signal.get("entry_mode")  # "confirm" or "half"
    _v4_exit_params = _v4_signal["exit_params"]

    print(f"[V4_SIGNAL] {m} {_15m_signal} (그룹={_v4_signal['logic_group']}) "
          f"필터={_v4_signal['filters_hit']} 청산={_v4_exit_params['description']}")

    # === 🔧 승률개선: 코인별 연패 쿨다운 ===
    # 같은 코인에서 연속 2회 이상 손절 → 30분 쿨다운
    if is_coin_loss_cooldown(m):
        cut("COIN_LOSS_CD", f"{m} 코인별연패쿨다운 (최근 30분 내 {COIN_LOSS_MAX}패 → 재진입 차단)", near_miss=False)
        _pipeline_inc("gate_fail_coin_cd")
        return None

    # 🔧 (제거됨) BUY_FADE: final_check DECAY 다운그레이드가 매수세 둔화 감지 → 중복 제거

    # === 매수비 계산 (스푸핑 방지: 비점화는 가중평균) ===
    _gate_buy_ratio = twin["buy_ratio"] if ignition_score >= 3 else (t15["buy_ratio"] * 0.7 + t45["buy_ratio"] * 0.3)

    # ============================================================
    # 하드컷 — 이 조건 실패 시 어떤 스코어든 위험한 진입
    # ============================================================
    _metrics = (f"점화={ignition_score} surge={vol_surge:.2f}x 매수비={_gate_buy_ratio:.0%} "
                f"스프레드={spread:.2f}% 가속={accel:.1f}x")

    # 📈 gate 진입 지표 값 기록 (통과/탈락 무관하게 모든 v4 시그널에 대해)
    _pipeline_track_value("gate_spread", spread, m, passed=True)  # 일단 기록 (탈락 시 아래서 재기록)
    _pipeline_track_value("gate_buy_ratio", _gate_buy_ratio, m, passed=True)
    _pipeline_track_value("gate_vol_krw", current_volume / 1e6, m, passed=True)
    _pipeline_track_value("gate_accel", accel, m, passed=True)
    _pipeline_track_value("gate_flow_kps", t15.get("krw_per_sec", 0) / 1000, m, passed=True)

    # 1) 틱 신선도
    if not fresh_ok:
        cut("FRESH", f"{m} 틱신선도부족 {fresh_age:.1f}초>{fresh_max_age:.1f}초 | {_metrics}", near_miss=False)
        _pipeline_inc("gate_fail_fresh")
        _pipeline_coin_hit(m, "fresh")
        return None

    # 2) 스프레드 (가격대별 동적 상한)
    if cur_price > 0 and cur_price < 100:
        eff_spread_max = min(GATE_SPREAD_MAX * SPREAD_SCALE_LOW, SPREAD_CAP_LOW)
    elif cur_price >= 100 and cur_price < 1000:
        eff_spread_max = min(GATE_SPREAD_MAX * SPREAD_SCALE_MID, SPREAD_CAP_MID)
    else:
        eff_spread_max = min(GATE_SPREAD_MAX * SPREAD_SCALE_HIGH, SPREAD_CAP_HIGH)
    if spread > eff_spread_max:
        cut("SPREAD", f"{m} 스프레드과다 {spread:.2f}%>{eff_spread_max:.2f}% | {_metrics}", near_miss=False)
        _pipeline_inc("gate_fail_spread")
        _pipeline_coin_hit(m, "spread")
        _pipeline_track_value("gate_spread", spread, m, passed=False)
        return None

    # 3) 최소 거래대금
    if current_volume < GATE_VOL_MIN and not mega:
        cut("VOL_MIN", f"{m} 거래대금부족 {current_volume/1e6:.0f}M<{GATE_VOL_MIN/1e6:.0f}M | {_metrics}", near_miss=False)
        _pipeline_inc("gate_fail_vol_min")
        _pipeline_coin_hit(m, "vol_min")
        _pipeline_track_value("gate_vol_krw", current_volume / 1e6, m, passed=False)
        return None

    # 4) 매수비 100% 스푸핑
    if abs(_gate_buy_ratio - 1.0) < 1e-6:
        cut("SPOOF100", f"{m} 매수비100%(스푸핑) | {_metrics}", near_miss=False)
        _pipeline_coin_hit(m, "spoof")
        return None

    # 4-1) 매수비 하한 — 🔧 데드코드→실구현 (0.50, 공포장 대응)
    if _gate_buy_ratio < GATE_BUY_RATIO_MIN:
        cut("BUY_RATIO", f"{m} 매수비부족 {_gate_buy_ratio:.2f}<{GATE_BUY_RATIO_MIN} | {_metrics}", near_miss=True)
        _pipeline_inc("gate_fail_buy_ratio")
        _pipeline_coin_hit(m, "buy_ratio")
        _pipeline_track_value("gate_buy_ratio", _gate_buy_ratio, m, passed=False)
        return None

    # 5) 가속도 과다
    if accel > GATE_ACCEL_MAX:
        cut("ACCEL_MAX", f"{m} 가속과다 {accel:.1f}x>{GATE_ACCEL_MAX}x | {_metrics}", near_miss=False)
        _pipeline_inc("gate_fail_accel")
        _pipeline_coin_hit(m, "accel")
        _pipeline_track_value("gate_accel", accel, m, passed=False)
        return None

    # 5-1) 초기 거래속도 하한 — 🔧 데드코드→실구현 (15K원/초)
    if t15.get("krw_per_sec", 0) < EARLY_FLOW_MIN_KRWPSEC:
        cut("EARLY_FLOW", f"{m} 거래속도부족 {t15.get('krw_per_sec',0)/1000:.0f}K<{EARLY_FLOW_MIN_KRWPSEC/1000:.0f}K | {_metrics}", near_miss=True)
        _pipeline_inc("gate_fail_early_flow")
        _pipeline_coin_hit(m, "early_flow")
        _pipeline_track_value("gate_flow_kps", t15.get("krw_per_sec", 0) / 1000, m, passed=False)
        return None

    # 🔧 WF데이터: 기존 캔들 바디/윗꼬리/WEAK_SIGNAL 필터 비활성화
    # 15분봉 눌림반전 신호가 이미 진입 품질을 보장하므로 1분봉 캔들 형태 필터 불필요

    # ============================================================
    # 신호 태깅 (WF데이터: v4 시그널 기반)
    # ============================================================
    signal_tag = _15m_signal if _15m_signal else "기본"

    # === VWAP gap 계산 (사이즈 조절/표시용) ===
    vwap = calc_vwap_from_candles(c1, 20)
    vwap_gap = ((cur_price / vwap - 1.0) * 100) if vwap and cur_price > 0 else 0.0

    # 🔧 WF데이터: entry_mode는 strategy_v4에서 결정 (데이터 기반 전부 confirm)
    _entry_mode = _entry_mode_override if _entry_mode_override else "confirm"
    _is_precision = False

    # 🔧 WF데이터: 1파/2파 판정 비활성화 (데이터에 없는 로직)
    _now_ts = time.time()
    _spike_wave = 1  # 항상 1파 (비활성화)
    _is_first_wave = True

    # 🔧 WF데이터: 2파/body/green_streak 오버라이드 비활성화
    # strategy_v4 시그널이 이미 진입 품질을 보장

    # === 결과 패키징 (v4 통합) ===
    pre = {
        "price": cur["trade_price"],
        "change": price_change,
        "current_volume": current_volume,
        "volume_surge": vol_surge,
        "ob": ob,
        "tape": twin,
        "ticks": ticks,
        "flow_accel": accel,
        "imbalance": imbalance,
        "turn_pct": turn_pct,
        "spread": spread,
        "buy_ratio": _gate_buy_ratio,
        "buy_ratio_conservative": min(t15["buy_ratio"], t45["buy_ratio"]),
        "fresh_ok": fresh_ok,
        "mega": mega,
        "filter_type": "stage1_gate",
        "ignition_score": ignition_score,
        "signal_tag": signal_tag,
        "cv": cv,
        "pstd": pstd10,
        "consecutive_buys": cons_buys,
        "overheat": overheat,
        "ign_ok": _ign_candidate,
        "mega_ok": mega,
        "candle_body_pct": candle_body_pct,
        "upper_wick_ratio": upper_wick_ratio,
        "green_streak": green_streak,
        "vwap_gap": round(vwap_gap, 2),
        "entry_mode": _entry_mode,
        "is_precision_pocket": _is_precision,
        "spike_wave": _spike_wave,
        # 🔧 v4 추가 필드
        "v4_signal": _v4_signal,
        "v4_exit_params": _v4_exit_params,
        "v4_logic_group": _v4_signal.get("logic_group", "A") if _v4_signal else "A",
        "v4_filters_hit": _v4_signal.get("filters_hit", []) if _v4_signal else [],
    }

    # 🔧 WF데이터: spike tracker 갱신 비활성화 (1파/2파 비활성화됨)

    _pipeline_inc("gate_pass")
    _pipeline_hourly_inc("gate_pass")
    _pipeline_record_pass_metrics(m, {
        "signal_tag": signal_tag,
        "spread": spread,
        "buy_ratio": _gate_buy_ratio,
        "vol_surge": vol_surge,
        "accel": accel,
        "ignition": ignition_score,
        "vwap_gap": vwap_gap,
        "flow_kps": t15.get("krw_per_sec", 0) / 1000,
    })
    _shadow_log_write(now_kst_str(), m, signal_tag, 1, "", 0,
                      f"gate_pass|mode={_entry_mode}|vwap_gap={vwap_gap:.2f}")

    return pre



# =========================
# === [DL LOGGING]
# =========================
LOG_PATH = os.path.join(os.getcwd(), os.getenv("DL_LOG_PATH",
                                               "signals_log.csv"))
_CSV_LOCK = threading.Lock()

DL_FIELDS = [
    "ts", "market", "entry_price", "chg_1m", "chg_5m", "chg_15m", "zscore_1m",
    "vwap_gap", "t15_buy", "t15_n", "t15_rate", "t15_krw", "turn", "spread",
    "depth_krw", "bidask_ratio", "volume_surge", "btc_1m", "btc_5m", "hour",
    "dow", "two_green_break", "ignition_ok", "early_ok", "uptick_ok",
    # 🔥 새 지표 추가
    "consecutive_buys", "avg_krw_per_tick", "flow_acceleration",
    # 🔥 GATE 핵심 지표 추가
    "imbalance", "overheat", "fresh_age",
    # 🚀 초단기 미세필터 지표 추가
    "cv", "pstd", "best_ask_krw", "prebreak_band", "is_prebreak",
    "ret_3m", "ret_10m", "ret_15m", "maxdd_10m", "maxrun_10m", "label_win10",
    "label_fail10"
]


def append_csv(row: dict):
    with _CSV_LOCK:
        new = not os.path.exists(LOG_PATH)
        with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=DL_FIELDS)
            if new: w.writeheader()
            padded = dict(row)  # 🔧 FIX: 복사본에 패딩 (caller dict 오염 방지)
            for k in DL_FIELDS:
                if k not in padded: padded[k] = ""
            w.writerow(padded)


def snapshot_row(m, entry_price, pre, c1, ob, t15, btc1m, btc5m,
                 flags):
    try:
        raw_ob = ob["raw"]["orderbook_units"][:3]
        bid_sum = sum(u["bid_size"] * u["bid_price"] for u in raw_ob)
        ask_sum = sum(u["ask_size"] * u["ask_price"] for u in raw_ob)
        bidask_ratio = bid_sum / max(ask_sum, 1)
    except Exception:
        bidask_ratio = 0.0

    # 🔥 새 지표 계산
    ticks = pre.get("ticks", [])
    cons_buys = calc_consecutive_buys(ticks, 15)
    avg_krw = calc_avg_krw_per_tick(t15)
    flow_accel = calc_flow_acceleration(ticks)

    # 🔥 GATE 핵심 지표
    imbalance = pre.get("imbalance", 0.0)
    overheat = flow_accel * float(pre.get("volume_surge", 1.0))  # accel * surge
    # 틱 신선도: 마지막 틱 나이 (초)
    fresh_age = 0.0
    if ticks:
        now_ms = int(time.time() * 1000)
        # 🔧 FIX: tick_ts_ms 헬퍼로 통일
        last_tick_ts = max(tick_ts_ms(t) for t in ticks)
        if last_tick_ts == 0: last_tick_ts = now_ms
        fresh_age = (now_ms - last_tick_ts) / 1000.0

    # 🚀 초단기 미세필터 지표
    ia_stats = inter_arrival_stats(ticks, 60) if ticks else {"cv": 0.0}  # 60초 (detect와 동일)
    cv = ia_stats.get("cv") or 0.0  # 🔧 FIX: None→0.0 (round(None) TypeError 방지)
    pstd = price_band_std(ticks, sec=10) if ticks else None  # 10초 (detect와 동일)
    pstd = pstd if pstd is not None else 0.0  # None 센티넬 처리
    prebreak_band = dynamic_prebreak_band(ticks) if ticks else PREBREAK_HIGH_PCT
    is_prebreak = 1 if pre.get("filter_type") == "prebreak" else 0
    # 베스트호가 깊이
    try:
        u0 = ob.get("raw", {}).get("orderbook_units", [])[0]
        best_ask_krw = float(u0["ask_price"]) * float(u0["ask_size"])
    except Exception:
        best_ask_krw = 0.0

    row = {
        "ts": now_kst_str(),
        "market": m,
        "entry_price": int(entry_price),
        "chg_1m": round(flags.get("chg_1m", 0.0), 4),
        "chg_5m": flags.get("chg_5m", ""),
        "chg_15m": flags.get("chg_15m", ""),
        "zscore_1m": flags.get("zscore", ""),
        "vwap_gap": flags.get("vwap_gap", ""),
        "t15_buy": round(t15.get("buy_ratio", 0.0), 4),
        "t15_n": t15.get("n", 0),
        "t15_rate": round(t15.get("rate", 0.0), 4),
        "t15_krw": int(t15.get("krw", 0)),
        "turn": flags.get("turn", ""),
        "spread": pre.get("spread", ""),
        "depth_krw": ob.get("depth_krw", ""),
        "bidask_ratio": round(bidask_ratio, 3),
        "volume_surge": pre.get("volume_surge", ""),
        "btc_1m": btc1m,
        "btc_5m": btc5m,
        "hour": now_kst().hour,
        "dow": now_kst().weekday(),
        "two_green_break": 1 if flags.get("two_green_break") else 0,
        "ignition_ok": 1 if flags.get("ign_ok") else 0,
        "early_ok": 1 if flags.get("early_ok") else 0,
        "uptick_ok": 1 if flags.get("uptick_ok") else 0,
        # 🔥 새 지표
        "consecutive_buys": cons_buys,
        "avg_krw_per_tick": round(avg_krw, 0),
        "flow_acceleration": round(flow_accel, 2),
        # 🚀 초단기 미세필터 지표
        "cv": round(cv, 2),
        "pstd": round(pstd * 100, 4),  # % 단위
        "best_ask_krw": int(best_ask_krw),
        "prebreak_band": round(prebreak_band * 100, 2),  # % 단위
        "is_prebreak": is_prebreak,
        # 🔥 GATE 핵심 지표
        "imbalance": round(imbalance, 3),
        "overheat": round(overheat, 2),
        "fresh_age": round(fresh_age, 2),
        "ret_3m": "",
        "ret_10m": "",
        "ret_15m": "",
        "maxdd_10m": "",
        "maxrun_10m": "",
        "label_win10": "",
        "label_fail10": ""
    }
    return row


# =========================
# 포스트체크 6초
# =========================


def postcheck_6s(m, pre):
    # 🔥 점화 진입은 포스트체크 바이패스 (signal_tag에 "점화" 포함 시)
    is_ignition = "점화" in pre.get("signal_tag", "")

    if not POSTCHECK_ENABLED:
        return True, "SKIP"
    # 🔧 FIX(I4): 점화도 최소 스프레드/매수비 체크 (완전 바이패스 금지)
    # 점화=가장 위험한 순간(급등+스프레드확장+되돌림)이므로 안전장치 유지
    if is_ignition:
        _ign_spread = pre.get("spread", 0)
        _ign_buy = pre.get("buy_ratio", 0)
        # 🔧 FIX: 하드코딩 0.40% → 가격대별 동적 상한 (stage1_gate와 동일 로직)
        _ign_price = pre.get("price", 0)
        if _ign_price > 0 and _ign_price < 100:
            _ign_spread_max = min(GATE_SPREAD_MAX * SPREAD_SCALE_LOW, SPREAD_CAP_LOW)
        elif _ign_price >= 100 and _ign_price < 1000:
            _ign_spread_max = min(GATE_SPREAD_MAX * SPREAD_SCALE_MID, SPREAD_CAP_MID)
        else:
            _ign_spread_max = min(GATE_SPREAD_MAX * SPREAD_SCALE_HIGH, SPREAD_CAP_HIGH)
        if _ign_spread > _ign_spread_max:
            return False, f"IGN_SPREAD_HIGH({_ign_spread:.2f}%>{_ign_spread_max:.2f}%)"
        if _ign_buy < 0.48:
            return False, f"IGN_BUY_LOW({_ign_buy:.2f})"
        # 🔧 꼭대기방지: 캔들 위치 기반 적응적 타이밍
        # - 캔들 초입(body < 0.5%): 빠른 진입 (0.1초만 확인)
        # - 캔들 중반(0.5~1.5%): 기존 0.3초 확인
        _ign_body = pre.get("candle_body_pct", 0)
        if _ign_body < 0.005:  # 캔들 초입 → 즉시 진입 (0.1초만)
            time.sleep(0.1)
            print(f"[IGN_FAST] {m} 캔들초입 body={_ign_body*100:.2f}% → 0.1초 퀵체크")
        else:
            time.sleep(0.3)
        _ign_ticks = get_recent_ticks(m, 50, allow_network=True)
        if _ign_ticks:
            _ign_curp = max(_ign_ticks, key=tick_ts_ms).get("trade_price", pre["price"])
            if pre["price"] > 0:
                _ign_dd = _ign_curp / pre["price"] - 1.0
                if _ign_dd < -0.008:
                    return False, f"IGN_DD({_ign_dd*100:.2f}%)"
            _ign_t5 = micro_tape_stats_from_ticks(_ign_ticks, 5)
            if _ign_t5.get("buy_ratio", 0) < 0.45:
                return False, f"IGN_BUY_FADE({_ign_t5.get('buy_ratio',0):.2f})"
        return True, "BYPASS_IGNITION_CHECKED"
    if pre.get("ign_ok") or pre.get("two_green_break") or pre.get(
            "mega_ok", False):
        return True, "BYPASS_STRONG_BREAK"
    # 🔧 강돌파 빠른 진입: 점화 수준의 초고속 체크 (3초 풀체크 → 0.5초 퀵체크)
    # - 강돌파는 EMA+고점 동시 돌파 = 확실한 모멘텀 → 늦게 들어가면 꼭대기
    # - 최소 안전장치(스프레드/급락/매수비 급감)만 확인 후 즉시 진입
    is_strongbreak = "강돌파" in pre.get("signal_tag", "")
    if is_strongbreak:
        sb_imb = pre.get("imbalance", 0)
        sb_br = pre.get("buy_ratio", 0)
        sb_spread = pre.get("spread", 0)
        _sb_body = pre.get("candle_body_pct", 0)
        # 스프레드 과다면 풀체크로 전환
        if sb_spread > 0.30:
            pass  # 풀 postcheck 진행
        else:
            # 🔧 꼭대기방지: 캔들 위치 기반 적응적 타이밍
            # - 캔들 초입(body < 0.4%): 즉시 진입 (0.1초 최소 체크)
            # - 캔들 중반(0.4~1.0%): 0.3초 퀵체크
            if _sb_body < 0.004:  # 캔들 초입 → 빠른 진입
                time.sleep(0.1)
                print(f"[SB_FAST] {m} 캔들초입 body={_sb_body*100:.2f}% → 0.1초 퀵체크")
            else:
                time.sleep(0.3)  # 🔧 0.5→0.3초 단축
            _sb_ticks = get_recent_ticks(m, 50, allow_network=True)
            if _sb_ticks:
                _sb_curp = max(_sb_ticks, key=tick_ts_ms).get("trade_price", pre["price"])
                if pre["price"] > 0:
                    _sb_dd = _sb_curp / pre["price"] - 1.0
                    if _sb_dd < -0.006:
                        return False, f"SB_DD({_sb_dd*100:.2f}%)"
                _sb_t5 = micro_tape_stats_from_ticks(_sb_ticks, 5)
                if _sb_t5.get("buy_ratio", 0) < 0.42:
                    return False, f"SB_BUY_FADE({_sb_t5.get('buy_ratio',0):.2f})"
            return True, "FAST_STRONGBREAK"

    # ★★★ 장세/야간 완화 노브
    r = relax_knob()
    pc_min_buy = max(0.46, POSTCHECK_MIN_BUY - 0.05 * r)   # 0.48 -> 0.46, 완화폭 살짝↑
    pc_min_rate = max(0.14, POSTCHECK_MIN_RATE - 0.09 * r) # 0.16 -> 0.14
    pc_max_pstd = POSTCHECK_MAX_PSTD + 0.0005 * r          # 살짝 더 관대
    pc_max_cv = POSTCHECK_MAX_CV + 0.18 * r
    pc_max_dd = POSTCHECK_MAX_DD + 0.005 * r

    window = POSTCHECK_WINDOW_SEC
    start = time.time()
    acc = deque(maxlen=400)  # 누수 방지
    seen = set()  # 중복 차단

    last_fetch = 0.0
    net_calls = 0  # ★ 이번 postcheck에서 실제 네트워크 호출 횟수
    # 🔧 조기진입: fetch 간격 통합 0.8초 (새벽도 주간과 동일 — 진입 지연 최소화)
    fetch_interval = 0.8
    ok_streak = 0

    base_price = pre["price"]
    peak = base_price
    trough_after_peak = base_price  # 피크 이후 최저가 추적
    # 🔧 FIX: surge 기준을 첫 틱 가격으로 리베이스 (관측 지연 처벌 제거)
    surge_base_set = False

    # ★★★ postcheck 중 최대 허용 급등 (1.5%)
    MAX_SURGE = 0.015

    while True:
        now = time.time()
        if now - start > window:
            break

        # 캐시 재사용 + 네트워크 호출 횟수 제한
        if (now - last_fetch >= fetch_interval) and (net_calls < 2):
            # 최대 2번까지만 실제 API 호출
            ticks = get_recent_ticks(m, 100, allow_network=True)
            last_fetch = now
            net_calls += 1
        else:
            # 나머지는 캐시만 사용
            ticks = get_recent_ticks(m, 100, allow_network=False)

        if not ticks:
            time.sleep(0.45)
            continue

        # acc에 최신 틱만 중복없이 축적
        # 🔧 FIX: appendleft 사용 - 새 틱을 왼쪽에 추가해야 acc[0]이 최신 유지
        for x in reversed(ticks[:12]):  # 과거→최신 순으로 반복
            key = (tick_ts_ms(x), x.get("trade_price"),
                   x.get("trade_volume"))
            if key in seen:
                continue
            seen.add(key)
            acc.appendleft(x)  # ✅ 최신이 index 0 유지

        curp = max(ticks, key=tick_ts_ms).get("trade_price", base_price)

        # 🔧 FIX: 첫 틱 가격으로 surge base 리베이스 (API지연→가격변동을 surge로 오인 방지)
        if not surge_base_set and curp > 0:
            base_price = curp
            surge_base_set = True

        # 🔧 승률개선: 급등 필터 강화 (55%/15K → 58%/18K + 되돌림 체크)
        # 55%는 가짜 돌파도 통과 → 매수비+거래속도+되돌림 3중 확인
        if base_price > 0:
            surge = (curp / base_price - 1.0)
            if surge >= MAX_SURGE:
                t_surge = micro_tape_stats_from_ticks(list(acc), 10) if len(acc) >= 3 else {}
                momentum_ok = (
                    t_surge.get("buy_ratio", 0) >= 0.58
                    and t_surge.get("krw_per_sec", 0) >= 18000
                    and (peak - trough_after_peak) / max(peak, 1) < 0.004  # 되돌림 0.4% 미만
                )
                if not momentum_ok:
                    return False, f"SURGE_IN_POST({surge*100:.2f}%)"
                # 모멘텀 확인 → half 모드로 다운그레이드 (리스크 제한)
                pre["_surge_probe"] = True
                print(f"[POSTCHECK] {surge*100:.1f}% 급등 + 모멘텀 확인 → half 다운그레이드")

        if curp > peak:
            peak = curp
            trough_after_peak = curp  # 새 피크가 생기면 트로프 리셋
        else:
            trough_after_peak = min(trough_after_peak, curp)

        # ✔ DD는 피크 대비 하락률(음수)로 체크
        dd = (curp / peak - 1.0)

        acc_list = list(acc)
        t10 = micro_tape_stats_from_ticks(acc_list, 10)
        ia = inter_arrival_stats(acc_list, 20)
        pstd = price_band_std(acc_list, 20)
        pstd = pstd if pstd is not None else 0.0  # None 센티넬 처리

        # ★ 가변 임계치 적용
        pass_now = (t10["buy_ratio"] >= pc_min_buy
                    and t10["rate"] >= pc_min_rate and pstd <= pc_max_pstd
                    and (ia["cv"] is None or ia["cv"] <= pc_max_cv) and dd >= -pc_max_dd)

        if pass_now:
            ok_streak += 1
            if ok_streak >= 2:  # 🔧 꼭대기방지: 1→2회 (연속 2회 OK 확인 후 통과 — 허수 급등 추가 필터)
                return True, "OK_EARLY"
        else:
            ok_streak = 0

        # 🔧 조기진입: 슬립 축소 (0.6/1.0→0.4/0.7초, 루프 1회당 지연 감소)
        time.sleep(0.4 if t10["rate"] >= 0.6 else 0.7)

    if not acc:
        return False, "POST_NO_TICKS"

    # 종료 시점 재평가 (acc 기반으로 피크/트로프 계산)
    prices = [x.get("trade_price", base_price) for x in list(acc)]
    if prices:
        peak2 = max(prices + [base_price])
        curp2 = prices[0]
        dd2 = (curp2 / peak2 - 1.0)
    else:
        dd2 = 0.0

    t10 = micro_tape_stats_from_ticks(list(acc), 10)
    ia = inter_arrival_stats(list(acc), 20)
    pstd = price_band_std(list(acc), 20)
    pstd = pstd if pstd is not None else 0.0  # None 센티넬 처리

    # ★ 최종 판정도 가변 임계치로
    if t10["buy_ratio"] < pc_min_buy:
        return False, f"BUY_LOW({t10['buy_ratio']:.2f})"
    if t10["rate"] < pc_min_rate: return False, f"RATE_LOW({t10['rate']:.2f})"
    if pstd > pc_max_pstd: return False, f"PSTD_HIGH({pstd:.4f})"
    if ia["cv"] is not None and ia["cv"] > pc_max_cv: return False, f"CV_HIGH({ia['cv']:.2f})"
    if dd2 < -pc_max_dd: return False, f"DD_TOO_DEEP({dd2:.4f})"
    return True, "OK"

# =========================
# 🎯 틱 기반 손절 헬퍼 함수
# =========================
def upbit_tick_size(price: float) -> float:
    """업비트 KRW 마켓 호가 단위 (보수적: 100~1000원 구간은 1원)"""
    p = float(price)
    if p >= 2_000_000: return 1000.0
    if p >= 1_000_000: return 500.0
    if p >=   500_000: return 100.0
    if p >=   100_000: return 50.0
    if p >=    10_000: return 10.0
    if p >=     1_000: return 5.0
    if p >=       100: return 1.0    # 보수적 (일부 종목 0.1원이지만 1원으로)
    if p >=        10: return 0.1
    if p >=         1: return 0.01
    return 0.001

# 🔧 BUG FIX: 5분봉 ATR 캐시 (60초 TTL) — 모니터링 루프에서 매번 API 호출하던 문제 수정
_ATR5_CACHE = {}  # {market: {"atr5": float, "ts": float}}
_ATR5_CACHE_TTL = 60  # 초

def dynamic_stop_loss(entry_price, c1, signal_type=None, current_price=None, trade_type=None, market=None):
    atr = atr14_from_candles(c1, ATR_PERIOD)
    if not atr or atr <= 0:
        return entry_price * (1 - DYN_SL_MIN), DYN_SL_MIN, None

    # 🔧 ATR 바닥값: 너무 작으면 휩쏘에 털림 방지 (최소 0.05% 또는 호가단위)
    atr = max(atr, entry_price * 0.0005, upbit_tick_size(entry_price))

    # 🔧 수익개선(실데이터): 5분봉 ATR 교차참조 — 고변동 코인 SL 자동 확장
    # STEEM ATR: 1분=0.40% 5분=0.85% → 1분봉 기준 SL 2.0%는 정상 눌림에 손절됨
    # 🔧 WF데이터: 5분봉 ATR cross-check 비활성화 (데이터에 없는 로직)
    # SL은 strategy_v4 exit_params에서 직접 결정
    _atr5_adjusted_min = DYN_SL_MIN

    base_pct = (atr / max(entry_price, 1)) * ATR_MULT

    # 🔧 WF데이터: SL 1.0% 통일 (strategy_v4 exit_params가 오버라이드)
    _time_sl_min = DYN_SL_MIN

    pct = min(max(base_pct, max(_time_sl_min, _atr5_adjusted_min)), DYN_SL_MAX)

    _sl_signal_mult = 1.0
    _sl_profit_mult = 1.0

    # 🔧 WF데이터: 신호유형별 SL 완화 비활성화 (데이터에 없는 로직)
    # SL은 strategy_v4 exit_params에서 시그널별로 직접 설정

    # 🔧 WF데이터: 수익구간 SL 완화 비활성화 (데이터에 없는 로직)
    # SL은 WF exit_params 고정값만 사용

    _sl_mult = 1.0
    max_sl = DYN_SL_MAX
    pct = min(max(pct, _time_sl_min), max_sl)

    atr_info = f"ATR {atr:.2f}원×{ATR_MULT}배"
    return entry_price * (1 - pct), pct, atr_info

# =========================
# 컨텍스트 기반 청산 점수
# =========================
def context_exit_score(m, ticks, ob_depth_krw, entry_price, last_price, c1):
    """
    휘핑 방지형 컨텍스트 점수 (개선):
    - 단일 신호로 청산 유도 금지 (복합 조건 누적)
    - 추세 역전(EMA5/VWAP 이탈 + uptick 부재) 쪽에 가중치
    - 🔧 NEW: 볼륨 확인 + 호가 임밸런스 교차검증 + 3단계 감쇄
    """
    score = 0
    reasons = []

    # 테이프 변화
    w_now = _win_stats(ticks, 0, 10)
    w_early = _win_stats(ticks, 10, 30)
    decay, decay_info = buy_decay_flag(ticks)

    if w_now["krw_per_sec"] < w_early["krw_per_sec"] * 0.60:
        score += 1
        reasons.append("FLOW_DROP")
    if not uptick_streak_from_ticks(ticks, need=2):
        score += 1
        reasons.append("NO_UPTICK")
    if w_now["rate"] < 0.25:  # 너무 느리면 1점
        score += 1
        reasons.append("RATE_SLOW")

    # 🔧 NEW: 3단계 cascade 감쇄 감지 시 추가 +1점 (더 확실한 추세 역전 신호)
    if decay_info.get("cascade", False):
        score += 1
        reasons.append("CASCADE_DECAY")

    # 🔧 NEW: 호가 임밸런스 교차검증 (매도 우세 + 흐름 감소 = 강한 청산 신호)
    try:
        _ctx_ob = fetch_orderbook_cache([m]).get(m)
        if _ctx_ob:
            _ctx_imb = calc_orderbook_imbalance(_ctx_ob)
            if _ctx_imb < -0.20:  # 매도 압도적 우세
                score += 1
                reasons.append(f"OB_SELL_HEAVY({_ctx_imb:.2f})")
            elif _ctx_imb >= 0.40 and w_now.get("buy_ratio", 0) >= 0.60:
                # 호가도 매수우세 + 테이프도 매수 → 청산 억제
                score = max(0, score - 1)
                reasons.append("OB_BUY_SUPPORT")
    except Exception:
        pass

    # 가격/컨텍스트
    vwap = vwap_from_candles_1m(c1, 20) if c1 else 0
    ema5 = ema_last([x["trade_price"] for x in c1], 5) if c1 else 0
    if vwap and last_price < vwap:
        score += 1
        reasons.append("VWAP_LOSS")
    if ema5 and last_price < ema5:
        score += 1
        reasons.append("EMA5_LOSS")

    # 💎 약상승/횡보 시 청산 점수 완화
    gain_now = (last_price / entry_price - 1.0)
    # 🔧 before1 복원: 약상승/횡보 시 청산 점수 완화 (범위 확대)
    if -0.004 <= gain_now <= 0.008:
        score = max(0, score - 1)
        reasons.append("MILD_GAIN_RELAX")

    # 🔧 NEW: 수익 구간(+1% 이상)에서는 청산 문턱 상향 (수익을 더 키우기)
    if gain_now >= 0.010:
        score = max(0, score - 1)
        reasons.append("PROFIT_HOLD_RELAX")

    # 수익 구간에서의 급감
    if last_price > entry_price * 1.008 and decay:
        score += 1
        reasons.append("DECAY_AFTER_GAIN")

    # 🔧 NEW: 거래량 확인 (볼륨 급감 시 청산 신호 강화)
    if w_now["n"] >= 2 and w_early["n"] >= 3:
        _vol_ratio = w_now["krw_per_sec"] / max(w_early["krw_per_sec"], 1)
        if _vol_ratio < 0.30:  # 거래량 70% 이상 급감
            score += 1
            reasons.append(f"VOL_CRASH({_vol_ratio:.1%})")

    return score, reasons

# =========================
# ★ 모니터링 시간 결정 (신규 추가)
# =========================
def decide_monitor_secs(pre: dict, tight_mode: bool = False) -> int:
    """
    포지션 모니터링 총 시간(초)을 상황별로 결정.
    - early_ok: 비교적 짧게 추세 확인
    - ignition_ok / mega_ok: 상대적으로 길게 (추세 이어질 가능성)
    - 시장 모드(TIGHT), 야간, BTC 모멘텀, 오더북 깊이 등에 따라 가/감
    """
    try:
        r = relax_knob()  # 0.0 ~ 1.5
    except Exception:
        r = 0.0

    base = 240  # 🔧 승률개선: 150→240초 (2.5분은 너무 짧음 → 4분 기본으로 추세 확인 여유)

    # 신호 유형 가중
    if pre.get("mega_ok"):
        base = 360  # 🔧 승률개선: 300→360초 (메가는 6분)
    elif pre.get("ign_ok"):
        base = 300  # 🔧 승률개선: 240→300초 (점화는 5분)
    elif pre.get("botacc_ok"):
        base = 270  # 🔧 승률개선: 210→270초
    elif pre.get("early_ok"):
        base = 240  # 🔧 승률개선: 180→240초
    elif pre.get("two_green_break"):
        base = 270  # 🔧 승률개선: 210→270초

    # 오더북 깊이 기반 (깊으면 여유 있게)
    ob_depth = 0
    try:
        ob_depth = pre.get("ob", {}).get("depth_krw", 0) or 0
    except Exception:
        pass
    if ob_depth >= 30_000_000:
        base += 30
    elif ob_depth <= 6_000_000:
        base -= 30

    # BTC 5분 모멘텀
    try:
        b5 = btc_5m_change()
    except Exception:
        b5 = 0.0
    if b5 >= 0.006:
        base += 30
    elif b5 <= -0.008:
        base -= 30

    # 야간(00~06 KST)엔 흔들림 대비 약간 단축
    h = now_kst().hour
    if 0 <= h < 6:
        base -= 15

    # 장세 완화 노브 반영
    base += int(10 * r)

    # 타이트 모드(급락 방어)면 단축
    if tight_mode:
        base -= 30

    # 하한/상한 클램프
    base = max(120, min(base, 480))  # 🔧 승률개선: 90~360 → 120~480 (최소 2분, 최대 8분)
    return int(base)


# =========================
# 끝알람 권고 생성 (END RECO)
# =========================
def _end_reco(m, entry_price, last_price, c1, ticks, ob_depth_krw, ctx_thr=3):
    """
    끝알람용 권고 생성:
      - 수익/손실, 컨텍스트, 테이프 흐름 종합으로
        👉 유지 / 부분청산 / 전량청산 세 가지 액션 제안
    """
    try:
        ret_pct = ((last_price / entry_price - 1.0) - FEE_RATE) * 100.0
    except Exception:
        ret_pct = 0.0

    # 컨텍스트 스코어(추세역전 신호들)
    try:
        ctx_score, ctx_reasons = context_exit_score(
            m,
            ticks or [],
            ob_depth_krw or 10_000_000,
            entry_price,
            last_price,
            c1 or [],
        )
    except Exception:
        ctx_score, ctx_reasons = (0, [])

    # 테이프(최근 15s)
    t15 = micro_tape_stats_from_ticks(ticks or [], 15)
    buy = t15.get("buy_ratio", 0.0)
    n = t15.get("n", 0)

    # 컨텍스트(EMA5 / VWAP)
    vwap = vwap_from_candles_1m(c1 or [], 20) if c1 else 0
    ema5 = ema_last([x["trade_price"] for x in (c1 or [])], 5) if c1 else 0
    vwap_ok = bool(vwap and last_price >= vwap)
    ema_ok = bool(ema5 and last_price >= ema5)

    # 💎 거래 둔화 + 약상승 → 본절 익절 유도
    if -0.2 <= ret_pct <= 0.4 and t15.get("krw_per_sec", 0) < 12000 and ctx_score <= ctx_thr:
        action = "부분 청산(본절)"
        rationale = f"거래둔화 구간 본절 익절 ({ret_pct:+.2f}%)"
        return action, rationale

    # -----------------------------
    # 1) 전량 청산 권고 조건 (강한 청산)
    # -----------------------------
    full_exit = False
    why_full = []

    # (1) 손실이 많이 커졌을 때
    if ret_pct <= -2.0:
        full_exit = True
        why_full.append(f"손실 {ret_pct:+.2f}%")

    # (2) 컨텍스트 스코어가 임계치보다 많이 높고, VWAP/EMA도 깨져 있을 때
    if ctx_score >= (ctx_thr + 1) and not vwap_ok and not ema_ok:
        full_exit = True
        why_full.append(f"컨텍스트 {ctx_score}/{ctx_thr}")

    # (3) 약손실 상태에서 매수세·테이프가 많이 죽은 경우
    if ret_pct < -0.8 and buy < 0.50 and n >= 4:
        full_exit = True
        why_full.append(f"매수비 {buy*100:.1f}% / 틱 {n}")

    if full_exit:
        action = "전량 청산 권고"
        rationale = " · ".join(why_full) if why_full else "리스크 우위"
        return action, rationale

    # -----------------------------
    # 2) 부분 청산 권고 (애매/경고 구간)
    # -----------------------------
    partial_exit = False
    why_partial = []

    # 수익이 크지 않은 구간
    if -0.8 < ret_pct < 0.8:
        partial_exit = True
        why_partial.append(f"수익 {ret_pct:+.2f}%")

    # 컨텍스트 경고 레벨
    if ctx_score == ctx_thr:
        partial_exit = True
        why_partial.append(f"컨텍스트 경고 {ctx_score}/{ctx_thr}")

    # 매수비 약하고 틱은 많은 경우
    if buy < 0.55 and n >= 6:
        partial_exit = True
        why_partial.append(f"매수비 {buy*100:.1f}% / 틱 {n}")

    # 🚀 거래둔화 시 자동 부분익절 권고
    _kps = t15.get("krw_per_sec", 0)
    if ret_pct >= 1.5 and _kps < 15000:
        partial_exit = True
        why_partial.append(f"거래속도 둔화 {_kps:.0f} KRW/s")

    # VWAP/EMA 둘 다 하방일 때
    if not vwap_ok and not ema_ok:
        partial_exit = True
        why_partial.append("VWAP·EMA5 하방")

    if partial_exit:
        action = "부분 청산(50%) 권고"
        rationale = " · ".join(why_partial) if why_partial else "불확실 구간"
        return action, rationale

    # -----------------------------
    # 3) 유지 권고 (추세 유지)
    # -----------------------------
    why_keep = [f"수익 {ret_pct:+.2f}%"]
    if vwap_ok:
        why_keep.append("VWAP 상방")
    if ema_ok:
        why_keep.append("EMA5 상방")
    if buy >= 0.60 and n >= 4:
        why_keep.append(f"매수비 {buy*100:.1f}% / 틱 {n}")

    rationale = " · ".join(why_keep)
    return "유지 권고", rationale


# (DCB monitor_dead_cat 함수 제거됨 — 비활성 전략 코드 정리)


# =========================
# 모니터링(최종형)
# =========================
def monitor_position(m,
                     entry_price,
                     pre,
                     tight_mode=False,
                     horizon=None,
                     reentry=False):
    # 🔧 FIX: entry_price 유효성 검증 (Division by Zero 방지)
    if not entry_price or entry_price <= 0:
        print(f"[MONITOR_ERR] {m} entry_price 무효 ({entry_price}) → 모니터링 중단")
        # 🔧 FIX: 알림 발송 + 포지션 정리 (무알림 방치 방지)
        tg_send(f"🚨 {m} entry_price 무효 ({entry_price}) → 모니터링 중단\n• 잔고 확인 필요")
        with _POSITION_LOCK:
            OPEN_POSITIONS.pop(m, None)
        return "유효하지 않은 entry_price", None, "", None, 0, 0, 0

    # 🔧 FIX: c1 초기화 (is_box 경로에서 미할당 → finally 참조 시 UnboundLocalError 방지)
    c1 = []
    # 🔧 FIX: 박스 포지션은 고정 SL/TP 사용 (dynamic_stop_loss 덮어쓰기 방지)
    if pre.get("is_box"):
        base_stop = pre.get("box_stop", entry_price * (1 - DYN_SL_MIN))
        eff_sl_pct = pre.get("box_sl_pct", DYN_SL_MIN)
        atr_info = "box_fixed"
    else:
        c1 = get_minutes_candles(1, m, 20)
        # 🔧 FIX: 초기 SL에도 signal_type 전달 (래칫 max()로 인해 초기값이 영구 지배 → ign/circle 완화 무효화 방지)
        base_stop, eff_sl_pct, atr_info = dynamic_stop_loss(entry_price, c1, signal_type=pre.get("signal_type", "normal"), market=m)

    # 🔧 FIX: remonitor 시 래칫된 stop 복원 (본절잠금/트레일잠금이 ATR 재계산으로 상실 방지)
    with _POSITION_LOCK:
        _pos_stop = OPEN_POSITIONS.get(m)
        if _pos_stop:
            _persisted_stop = _pos_stop.get("stop", 0)
            if _persisted_stop > base_stop:
                base_stop = _persisted_stop
                print(f"[REMONITOR_SL] {m} 래칫 stop 복원: {base_stop:,.0f} (ATR보다 높음)")

    # horizon이 안 들어오면 자동 결정, 들어오면 그 값 사용
    if horizon is None:
        horizon = decide_monitor_secs(pre, tight_mode=tight_mode)
    start_ts = time.time()
    # MAX_RUNTIME 제거 (미사용 — while 조건에서 horizon 직접 사용)


    # 디바운스/트레일 상태
    # 손절 디바운스용
    stop_first_seen_ts = 0.0
    stop_hits = 0
    # 🔧 수급확인 손절: 감량 후 관망모드 상태
    _sl_reduced = False          # 감량(50%) 매도 완료 여부
    _sl_reduced_ts = 0.0         # 감량 시각
    # 🔧 FIX: SL 확장에 캡 적용 (eff_sl_pct에 이미 1.8x 적용 가능 → 1.35x 스태킹 시 7.78% 가능)
    _sl_extended_pct = min(eff_sl_pct * 1.35, DYN_SL_MAX * 1.5)  # 최대 4.8%
    # 트레일 디바운스용
    trail_db_first_ts = 0.0
    trail_db_hits = 0

    trail_armed = False
    trail_stop = 0.0
    trail_dist = 0.0  # 🔧 FIX: 초기화 (checkpoint 전 참조 시 NameError 방지)
    _already_closed = False  # 🔧 FIX: 내부 청산 완료 플래그 (중복 청산 방지)
    in_soft_guard = True  # 🔧 FIX: 초기화 (첫 루프에서 SL 디바운스 참조 시 NameError 방지)

    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 10

    ob = pre.get("ob")

    last_price = entry_price
    curp = entry_price  # 🔧 FIX: 초기화 (틱 전부 실패 시 NameError 방지)
    best = entry_price
    worst = entry_price

    verdict = None

    # === 📊 MFE 시계열 스냅샷 추적 ===
    _mfe_snapshots = {}          # {10: 최고MFE%, 30: 최고MFE%, ...} 각 시점까지의 최고 수익률
    _mfe_snap_best = {}          # {10: best_price, 30: best_price, ...} 각 시점까지의 최고가
    _mfe_snap_done = set()       # 이미 기록 완료된 시점 (중복 방지)
    _mfe_snap_cur_prices = {}    # {10: 현재가, 30: 현재가, ...} 각 시점의 현재가 (MFE뿐 아니라 실시간 수익률도)

    # === 포지션 모드 (half / confirm) + 트레이드 유형 (scalp / runner) ===
    with _POSITION_LOCK:
        pos = OPEN_POSITIONS.get(m, {})
    entry_mode = pos.get("entry_mode", "confirm")
    trade_type = pos.get("trade_type", "scalp")  # 🔧 특단조치: 진입 시 결정된 스캘프/러너
    signal_tag = pos.get("signal_tag", "기본")  # 🔧 MFE 익절 경로용

    # === 🎯 WF데이터: EXIT_PARAMS 기반 트레일 파라미터 ===
    _v4_ep = pre.get("v4_exit_params") or v4_get_exit_params(signal_tag)
    checkpoint_reached = False
    # 🔧 WF: v4_ep activation 우선 적용 (비용바닥만 보장, 글로벌 BASE 무시)
    # 거래량3배/눌림반전: 0.3%, EMA정배열/MACD골든: 0.5%
    _v4_activation = _v4_ep.get("activation_pct")
    if _v4_activation is not None:
        _cost_floor = FEE_RATE + 0.001 + PROFIT_CHECKPOINT_MIN_ALPHA  # ≈0.23% (수수료+슬립+α)
        dyn_checkpoint = max(_cost_floor, _v4_activation)
    else:
        dyn_checkpoint = get_dynamic_checkpoint()
    # 🔧 WF: v4_ep trail 직접 적용 (글로벌 TRAIL_DISTANCE_MIN_BASE 무시)
    # 거래량3배/눌림반전: 0.2%, EMA정배열/MACD골든: 0.3%
    _v4_trail = _v4_ep.get("trail_pct")
    trail_dist_min = _v4_trail if _v4_trail is not None else get_trail_distance_min()
    # 🔧 WF: hold_bars (현재 모든 시그널 0)
    _v4_hold_bars = _v4_ep.get("hold_bars", 0)
    _v4_max_bars = _v4_ep.get("max_bars", 60)
    _v4_strategy = _v4_ep.get("strategy", "TRAIL")
    # 🔧 v4: SL 오버라이드 — WF 최적값 우선 적용 (글로벌 DYN_SL_MIN 무시)
    # 거래량3배/20봉돌파/눌림반전: SL 0.7% (WF PASS), 기본: SL 1.0%
    if _v4_ep.get("sl_pct"):
        eff_sl_pct = _v4_ep["sl_pct"]
        base_stop = entry_price * (1 - eff_sl_pct)

    # === 🔥 Plateau 감지용 상태 ===
    last_peak_ts = time.time()   # 마지막 고점 갱신 시간
    plateau_partial_done = False # Plateau 부분익절 완료 여부

    # === 🔧 청산 이벤트 쿨다운 (MFE/Plateau/Checkpoint 겹침 방지) ===
    last_exit_event_ts = 0.0
    EXIT_EVENT_COOLDOWN_SEC = 6.0  # 부분익절 후 6초간 다른 청산 트리거 무시


    # === 🔧 MFE 기반 부분익절 상태 ===
    mfe_partial_done = False     # MFE 부분익절 완료 여부


    # 🔧 FIX: signal_type 로드 (dynamic_stop_loss 신호별 완화용 — signal_tag와 별도)
    signal_type_for_sl = pos.get("signal_type", "normal")

    # === 🔧 1분봉 캐시 (10초 스로틀 — 루프 내 다중 호출 방지) ===
    _c1_cache = None
    _c1_cache_ts = 0.0
    # 🔧 FIX: SL 주기적 갱신용 타임스탬프 (수익 중 손절 완화 반영)
    _last_sl_refresh_ts = 0.0

    def _get_c1_cached():
        nonlocal _c1_cache, _c1_cache_ts
        now = time.time()
        if _c1_cache is None or (now - _c1_cache_ts) >= 5:  # 🔧 손익분기개선: 10→5초 (stale 데이터로 판단 방지)
            _c1_cache = get_minutes_candles(1, m, 20)
            _c1_cache_ts = now
        return _c1_cache

    # 🔧 FIX: ticker/오더북 throttle을 로컬 변수로 관리 (함수 속성 race condition 방지)
    _local_ticker_ts = 0.0
    _local_ob_snap_ts = 0.0
    _local_ob_snap_cache = pre.get("ob", {})

    try:
        while time.time() - start_ts <= horizon:  # 🔧 before1 복원 (MAX_RUNTIME→horizon)
            time.sleep(RECHECK_SEC)

            # 🔧 찌꺼기 방지: 부분청산→전량청산 전환 시 루프 조기 종료
            # 🔧 FIX: 잔고 확인 후 판단 (OPEN_POSITIONS 이탈만으로 청산 단정 → 유령포지션 원인)
            # 🔧 FIX: API 호출을 락 밖으로 이동 (데드락 방지 — 락 안 네트워크 호출 금지)
            _pos_missing = False
            with _POSITION_LOCK:
                if m not in OPEN_POSITIONS:
                    _pos_missing = True
            if _pos_missing:
                _actual_bal_check = get_balance_with_locked(m)
                if _actual_bal_check is not None and _actual_bal_check > 1e-12:
                    # 잔고 있는데 OPEN_POSITIONS에서 사라짐 → 재등록 후 계속 모니터링
                    print(f"[MON_GUARD] {m} OPEN_POSITIONS 이탈 but 잔고 {_actual_bal_check:.6f} → 재등록")
                    with _POSITION_LOCK:
                        OPEN_POSITIONS[m] = {
                            "state": "open", "entry_price": entry_price,
                            "volume": _actual_bal_check, "stop": base_stop,
                            "sl_pct": eff_sl_pct, "entry_ts": start_ts,
                            "strategy": pre.get("strategy", "breakout"),
                            "signal_type": pre.get("signal_type", "normal"),
                            "signal_tag": pre.get("signal_tag", "복구"),
                            "trade_type": pre.get("trade_type", "scalp"),
                        }
                elif _actual_bal_check is not None and _actual_bal_check < 0:
                    # 🔧 FIX: API 실패(-1) → 포지션 유지, 다음 루프에서 재확인
                    print(f"[MON_GUARD] {m} 잔고 조회 실패 → 포지션 유지, 다음 루프 대기")
                else:
                    verdict = "부분청산→전량청산"
                    _already_closed = True
                    break

            ticks = get_recent_ticks(m, 100)
            if not ticks or len(ticks) < 3:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    time.sleep(3)
                    ticks = get_recent_ticks(m, 100)
                    if not ticks:
                        verdict = "데이터 수신 실패"
                        break
                    consecutive_failures = 0
                continue
            consecutive_failures = 0

            # 현재가 — 🔧 FIX: ticker API throttle (6초마다만 호출, 나머지는 ticks에서 추출)
            # 🔧 FIX: 함수 속성 대신 로컬 변수 사용 (스레드 간 race condition 방지)
            _ticker_age = time.time() - _local_ticker_ts
            if _ticker_age >= 6:
                cur_js = safe_upbit_get("https://api.upbit.com/v1/ticker", {"markets": m})
                if cur_js and len(cur_js) > 0:
                    curp = cur_js[0].get("trade_price", last_price)
                    _local_ticker_ts = time.time()
                else:
                    # ticker 실패 시 ticks에서 최신 가격 추출
                    curp = max(ticks, key=tick_ts_ms).get("trade_price", last_price)
            else:
                # throttle 중: ticks에서 최신 가격 추출 (API 절약)
                curp = max(ticks, key=tick_ts_ms).get("trade_price", last_price)
            last_price = curp

            # 🔧 FIX: 오더북 스냅샷 (루프당 1회, 10초 throttle — API 429 방지)
            # 🔧 FIX: 함수 속성 대신 로컬 변수 사용 (스레드 간 race condition 방지)
            _ob_snap_age = time.time() - _local_ob_snap_ts
            if _ob_snap_age >= 10:
                try:
                    _ob_snap_raw = fetch_orderbook_cache([m])
                    _local_ob_snap_cache = _ob_snap_raw.get(m, pre.get("ob", {}))
                    _local_ob_snap_ts = time.time()
                except Exception as _ob_err:
                    print(f"[OB_SNAP] {m} 오더북 갱신 실패: {_ob_err}")  # 🔧 FIX: silent exception → 로깅
            ob_snap = _local_ob_snap_cache

            # ✅ 트레일링 래칫 버그 수정: 신고점 판정 먼저
            new_high = curp > best
            if new_high:
                last_peak_ts = time.time()  # 🔥 Plateau 감지용 고점 시간 갱신
            best = max(best, curp)
            worst = min(worst, curp)

            # 📊 MFE 시계열 스냅샷: 각 시점까지의 최고 수익률 기록
            for snap_sec in MFE_SNAPSHOT_TIMES:
                if snap_sec not in _mfe_snap_done and alive_sec >= snap_sec:
                    _mfe_snap_best[snap_sec] = best
                    _mfe_snap_cur_prices[snap_sec] = curp
                    _mfe_snapshots[snap_sec] = (best / entry_price - 1.0) if entry_price > 0 else 0
                    _mfe_snap_done.add(snap_sec)

            # 🔧 before1 복원: Probe/Half/Confirm 스크래치 비활성화
            # 조기 탈출은 정상 눌림→반등 기회를 박탈하여 승률 하락 원인
            # ATR 기반 동적 손절이 충분히 보호하므로 추가 스크래치 불필요
            # (before1에서는 스크래치 규칙이 비활성화 상태였음)

            # 🔧 MFE/MAE 실시간 저장 (청산 시 로깅용)
            mfe_pct = (best / entry_price - 1.0) * 100 if entry_price > 0 else 0
            mae_pct = (worst / entry_price - 1.0) * 100 if entry_price > 0 else 0
            # 🔧 데이터수집: MFE 도달 시간 기록 (트레일 간격 최적화용)
            _mfe_sec = time.time() - start_ts if new_high else None
            with _POSITION_LOCK:
                pos_now = OPEN_POSITIONS.get(m)
                if pos_now:
                    pos_now["mfe_pct"] = mfe_pct
                    pos_now["mae_pct"] = mae_pct
                    if _mfe_sec is not None:
                        pos_now["mfe_sec"] = round(_mfe_sec, 1)  # 최고점 도달까지 걸린 시간
                    # 🔧 FIX: trail_dist/trail_stop_pct를 항상 저장 (trail 미무장 시에도 기본값 기록 → 청산 알람 0값 방지)
                    if trail_armed:
                        pos_now["trail_dist"] = round((trail_dist if trail_dist > 0 else trail_dist_min) * 100, 3)  # % 단위
                        pos_now["trail_stop_pct"] = round((trail_stop / entry_price - 1.0) * 100, 3) if trail_stop > 0 and entry_price > 0 else 0
                    else:
                        # 트레일 미무장이라도 잠재 트레일 거리 기록 (튜닝 참고용)
                        pos_now["trail_dist"] = round(trail_dist_min * 100, 3)
                        pos_now["trail_stop_pct"] = 0.0
                    # 📊 MFE 시계열 스냅샷 저장 (청산 시 record_trade에서 활용)
                    pos_now["mfe_snapshots"] = dict(_mfe_snapshots)
                    pos_now["mfe_snap_prices"] = dict(_mfe_snap_cur_prices)
                    OPEN_POSITIONS[m] = pos_now

            # 🔧 FIX: SL 주기적 갱신 — 수익 중 손절 완화(current_price) 반영
            # - 박스 포지션은 고정 SL 유지 (refresh 스킵)
            if not pre.get("is_box") and time.time() - _last_sl_refresh_ts >= 5:
                _c1_for_sl_refresh = _get_c1_cached()
                _new_stop, _new_sl_pct, _new_atr_info = dynamic_stop_loss(
                    entry_price, _c1_for_sl_refresh, signal_type=signal_type_for_sl, current_price=curp, trade_type=trade_type, market=m
                )
                # base_stop은 래칫/본절잠금과 충돌하니 max로만 갱신 (하향 방지)
                base_stop = max(base_stop, _new_stop)
                eff_sl_pct = _new_sl_pct
                atr_info = _new_atr_info
                _last_sl_refresh_ts = time.time()

            # === 1) ATR 기반 동적 손절 (웜업 제거, 체결 직후부터 적용) ===
            alive_sec = time.time() - start_ts
            cur_gain = (curp / entry_price - 1.0)

            # 🔧 before1 복원: 손절 = eff_sl_pct 직접 비교 (fee margin 없음)
            # + base_stop 가격 기반 SL (부분익절 후 본절 상향 반영)
            # 🔧 수급확인감량 후: 확장 SL% 사용 (원래 SL의 135%)
            _active_sl_pct = _sl_extended_pct if _sl_reduced else eff_sl_pct
            hit_pct_sl = cur_gain <= -_active_sl_pct
            hit_base_stop = (base_stop > 0 and curp <= base_stop) if not _sl_reduced else False  # 감량 후 본절SL 비활성
            if hit_pct_sl or hit_base_stop:
                # 🔧 FIX: SL 디바운스 — 틱 1~2번 휩쏘에 즉시 손절 방지
                # 연속 2회 이상 또는 2초 이상 유지 시에만 실제 청산
                if stop_first_seen_ts == 0.0:
                    stop_first_seen_ts = time.time()
                    stop_hits = 1
                else:
                    stop_hits += 1
                _sl_duration = time.time() - stop_first_seen_ts
                # HARD_STOP: 급락(-1.5%)은 즉시 컷 (디바운스 미적용)
                _is_hard_stop = cur_gain <= -(eff_sl_pct * 1.5)
                # 🔧 FIX: EXIT_DEBOUNCE_N/SEC 상수 활용 + 웜업/소프트가드 중 더 둔하게
                _db_n = EXIT_DEBOUNCE_N + (1 if alive_sec < WARMUP_SEC else 0) + (1 if in_soft_guard else 0)
                _db_sec = EXIT_DEBOUNCE_SEC + (2 if alive_sec < WARMUP_SEC else 0) + (1 if in_soft_guard else 0)
                if not _is_hard_stop and stop_hits < _db_n and _sl_duration < _db_sec:
                    continue  # 디바운스 대기

                # ================================================================
                # 🔧 수급확인 손절 (Context-Aware Stop)
                # 디바운스 통과 후 즉시 청산 대신, 수급을 보고 판단:
                # - 추세 죽음 → 전량 청산 (기존과 동일)
                # - 추세 살아있음 → 50% 감량 + 확장 SL로 20초 관찰
                # - 하드스톱/본절SL은 수급확인 없이 즉시 청산
                # ================================================================

                # 하드스톱(SL×1.5)이면 수급확인 없이 즉시 전량 청산
                if _is_hard_stop:
                    sl_reason = f"하드스톱 | -{abs(cur_gain)*100:.2f}% (SL×1.5 초과, 즉시컷)"
                    close_auto_position(m, sl_reason)
                    _already_closed = True
                    verdict = "하드스톱"
                    break

                # 본절SL(래칫)은 이미 수익 구간을 거쳤으므로 즉시 청산
                if hit_base_stop and not hit_pct_sl:
                    sl_reason = f"본절SL | 현재 {curp:,.0f}원 ≤ base_stop {base_stop:,.0f}원 ({atr_info})"
                    close_auto_position(m, sl_reason)
                    _already_closed = True
                    verdict = "본절SL"
                    break

                # === 수급 스캔: 추세가 살아있는지 확인 ===
                _sl_ticks = get_recent_ticks(m, 80, allow_network=True)
                _sl_t10 = micro_tape_stats_from_ticks(_sl_ticks, 10) if _sl_ticks and len(_sl_ticks) >= 3 else {}
                _sl_ob = None
                try:
                    _sl_ob_raw = safe_upbit_get("https://api.upbit.com/v1/orderbook", {"markets": m})
                    if _sl_ob_raw and len(_sl_ob_raw) > 0:
                        _sl_ob = _sl_ob_raw[0]
                except Exception:
                    pass
                _sl_imb = calc_orderbook_imbalance({"raw": _sl_ob}) if _sl_ob else -1.0

                _sl_alive_signals = 0
                _sl_buy_r = _sl_t10.get("buy_ratio", 0)
                _sl_krw_s = _sl_t10.get("krw_per_sec", 0)
                # 🔧 손절완화: 수급 생존 기준 하향 (더 쉽게 감량 기회 부여)
                if _sl_buy_r >= 0.44:      # 🔧 0.48→0.44 (매수비 44%면 아직 살아있음)
                    _sl_alive_signals += 1
                if _sl_krw_s >= 5000:      # 🔧 8000→5000 (소형코인도 거래속도 인정)
                    _sl_alive_signals += 1
                if _sl_imb >= -0.15:       # 🔧 -0.10→-0.15 (약간의 매도벽도 허용)
                    _sl_alive_signals += 1

                # 🔧 손절완화: 추세 죽음 기준 완화 (1개 이하→0개만 즉시 전량 청산)
                # 기존: 1개 이하 = 전량청산 → 매수비만 살아있어도 전량컷 (너무 빡빡)
                # 변경: 0개일 때만 전량청산, 1개라도 통과하면 감량 기회 부여
                if _sl_alive_signals == 0:
                    sl_reason = (f"ATR손절(수급확인) | -{abs(cur_gain)*100:.2f}% "
                                 f"매수비{_sl_buy_r:.0%} 체결{_sl_krw_s:,.0f}/s 임밸{_sl_imb:.2f} "
                                 f"→ 추세사망({_sl_alive_signals}/3) 전량청산 ({atr_info})")
                    close_auto_position(m, sl_reason)
                    _already_closed = True
                    verdict = "ATR손절"
                    tg_send_mid(f"🛑 {m} 수급확인손절 | -{abs(cur_gain)*100:.2f}% | 추세사망({_sl_alive_signals}/3)")
                    break

                # 추세 살아있음 (3개 중 2개 이상 통과) → 50% 감량 + 확장 관찰
                if not _sl_reduced:
                    _reduce_ok, _reduce_msg, _reduce_sold = safe_partial_sell(
                        m, 0.50,
                        f"수급확인감량 | -{abs(cur_gain)*100:.2f}% 매수비{_sl_buy_r:.0%} "
                        f"체결{_sl_krw_s:,.0f}/s 임밸{_sl_imb:.2f} → 추세생존({_sl_alive_signals}/3)"
                    )
                    if _reduce_ok:
                        # 감량 후 포지션 존재 확인 (dust 전량청산 가능)
                        with _POSITION_LOCK:
                            _pos_chk = OPEN_POSITIONS.get(m)
                        if not _pos_chk:
                            _already_closed = True
                            verdict = "수급감량_DUST"
                            break
                        _sl_reduced = True
                        _sl_reduced_ts = time.time()
                        _sl_extended_pct = min(eff_sl_pct * 1.35, DYN_SL_MAX * 1.5)  # 🔧 FIX: 캡 적용 (최대 4.8%)
                        # 디바운스 리셋 (새 기준으로 관찰 시작)
                        stop_first_seen_ts = 0.0
                        stop_hits = 0
                        tg_send_mid(
                            f"🔄 {m} 수급확인감량 50% | -{abs(cur_gain)*100:.2f}% "
                            f"| 추세생존({_sl_alive_signals}/3) "
                            f"| 20초 관찰 (SL -{_sl_extended_pct*100:.1f}%)"
                        )
                    else:
                        # 감량 실패 → 전량 청산
                        sl_reason = f"ATR손절(감량실패) | -{abs(cur_gain)*100:.2f}% ({atr_info})"
                        close_auto_position(m, sl_reason)
                        _already_closed = True
                        verdict = "ATR손절"
                        break
                else:
                    # 이미 감량된 상태에서 또 SL 터치 → 잔여분 전량 청산
                    sl_reason = (f"잔여청산 | 감량 후 재하락 -{abs(cur_gain)*100:.2f}% "
                                 f"매수비{_sl_buy_r:.0%} ({atr_info})")
                    close_auto_position(m, sl_reason)
                    _already_closed = True
                    verdict = "잔여청산"
                    tg_send_mid(f"🛑 {m} 잔여청산 | 감량 후 재하락 -{abs(cur_gain)*100:.2f}%")
                    break
            else:
                # 🔧 FIX C4: SL 디바운스도 partial decay 적용 (풀 리셋 방지)
                _sl_recovery = -cur_gain / eff_sl_pct if eff_sl_pct > 0 else 0  # 항상 계산
                if stop_first_seen_ts > 0:
                    if _sl_recovery < 0.5:  # SL선의 50% 이내로 회복 = 진짜 반등
                        stop_first_seen_ts = 0.0
                        stop_hits = 0
                    else:
                        stop_hits = max(0, stop_hits - 1)

                # 🔧 수급확인감량 후 관망 결과 처리
                if _sl_reduced:
                    _sl_observe_elapsed = time.time() - _sl_reduced_ts
                    # 감량 후 20초 내 가격 회복 → 잔여 포지션 생존 (휩쏘 방어 성공)
                    if _sl_recovery < 0.3 and _sl_observe_elapsed >= 5.0:
                        # SL선에서 충분히 멀어짐(손실 30% 미만) + 5초 이상 유지
                        print(f"[수급확인] {m} 감량 후 회복 확인 | {cur_gain*100:.2f}% | 잔여 포지션 유지")
                        tg_send_mid(f"✅ {m} 휩쏘 방어 성공 | 감량50% 후 회복 | 잔여 트레일 전환")
                        _sl_reduced = False  # 관망 종료, 일반 모드 복귀
                    # 🔧 수익성패치: 관망 30→20초 (SL 근처 30초는 추세반전 확정, 추가손실 방지)
                    elif _sl_observe_elapsed >= 20.0:
                        _sl_final_gain = (curp / entry_price - 1.0) if entry_price > 0 else 0
                        if _sl_final_gain <= -eff_sl_pct * 0.8:
                            # 20초 지나도 SL 70% 이상 손실 유지 → 추세 반전 확정
                            close_auto_position(m, f"관망만료청산 | 20초 후 미회복 -{abs(_sl_final_gain)*100:.2f}%")
                            _already_closed = True
                            verdict = "관망만료"
                            tg_send_mid(f"🛑 {m} 관망 만료 | 20초 미회복 -{abs(_sl_final_gain)*100:.2f}% → 잔여 청산")
                            break
                        else:
                            # 20초 지나고 약간 회복 → 생존 (일반 모드 복귀)
                            print(f"[수급확인] {m} 관망 만료 | 약간 회복 {_sl_final_gain*100:.2f}% → 잔여 유지")
                            _sl_reduced = False

            # === 🔧 v7 차트분석: 시간대 적응형 타임아웃 (청산 로직) ===
            # 📊 172샘플: 오전 wr59% → 30분 유지, 오후 wr28% → 20분으로 조기청산
            # 수익 중: 트레일 타이트닝 / 손실 중: 스크래치 아웃
            _entry_hour = pos.get("entry_hour", 12)
            _timeout_sec = 1200 if 12 <= _entry_hour < 18 else 1800  # 오후 20분, 그외 30분
            if alive_sec >= _timeout_sec:
                if cur_gain > 0:
                    # 수익 중: 트레일링 스톱 타이트닝 (정상의 50%)
                    if trail_armed and trail_dist > 0:
                        _trail_dist_tight = trail_dist * 0.5
                        trail_stop = max(trail_stop, curp * (1.0 - _trail_dist_tight), base_stop)
                        print(f"[V7_TIMEOUT] {m} {_timeout_sec//60}분경과 수익중({cur_gain*100:.2f}%) → 트레일 타이트닝")
                        tg_send_mid(f"⏰ {m} {_timeout_sec//60}분 경과 | +{cur_gain*100:.2f}% | 트레일 타이트닝 50%")
                else:
                    # 손실 중: 스크래치 아웃 (즉시 청산)
                    close_auto_position(m, f"{_timeout_sec//60}분타임아웃 손실청산 | -{abs(cur_gain)*100:.2f}% | 스크래치아웃")
                    _already_closed = True
                    verdict = "V7_TIMEOUT_LOSS"
                    print(f"[V7_TIMEOUT] {m} {_timeout_sec//60}분경과 손실중({cur_gain*100:.2f}%) → 스크래치아웃")
                    tg_send_mid(f"⏰ {m} {_timeout_sec//60}분 경과 | {cur_gain*100:.2f}% 손실 → 강제 청산")
                    break

            # === 🔥 실패 브레이크아웃 즉시 탈출 ===
            # +0.15% 돌파 후 5초 내 진입가 이하로 복귀 → 가짜 돌파, 즉시 청산
            # 🔧 실패돌파/스크래치/횡보탈출/고점미갱신: before1 비활성화 상태 유지
            # (향후 필요시 git history 참고)

            # === 🔧 v7 차트분석: 폭발진입 적응형 청산 ===
            # 📊 1분봉 서지 분석: 피크 2-4바(10-20분), 이후 급락 -4.6~6.4%
            # vol20x+ avg+2.259% → 5분 홀드 후 볼륨감소 or 10분 무조건 트레일 타이트닝
            if pos.get("is_surge_circle"):
                if alive_sec >= 180 and cur_gain > 0.015:  # 3분+1.5% 이상
                    # 볼륨 감소 감지 (피크아웃) → 즉시 익절
                    try:
                        _sc_recent = get_recent_ticks(m, 30)
                        if _sc_recent and len(_sc_recent) >= 10:
                            _sc_rv = sum(t.get("trade_price",0) * t.get("trade_volume",0) for t in _sc_recent[:5])
                            _sc_pv = sum(t.get("trade_price",0) * t.get("trade_volume",0) for t in _sc_recent[5:10])
                            if _sc_pv > 0 and _sc_rv < _sc_pv * 0.5:  # 거래량 50% 감소
                                print(f"[V7_SURGE_EXIT] {m} 폭발익절 {cur_gain*100:.2f}% (vol감소 {_sc_rv/_sc_pv:.1f}x)")
                                close_auto_position(m, f"폭발익절 | +{cur_gain*100:.2f}% | 볼륨감소(피크아웃)")
                                _already_closed = True
                                verdict = "V7_SURGE_PEAK_EXIT"
                                break
                    except Exception:
                        pass
                # 10분 경과 시 트레일 강제 타이트닝 (피크 구간 벗어남)
                if alive_sec >= 600 and trail_armed and trail_dist > 0:
                    _surge_tight = trail_dist * 0.3  # 70% 축소
                    trail_stop = max(trail_stop, curp * (1.0 - _surge_tight), base_stop)
                    if alive_sec < 605:  # 로그 1회만
                        print(f"[V7_SURGE_TIGHT] {m} 폭발 10분경과 → 트레일 {trail_dist*100:.2f}%→{_surge_tight*100:.2f}%")
                # 15분 경과 + 손실 → 스크래치아웃 (서지 실패)
                if alive_sec >= 900 and cur_gain <= 0:
                    close_auto_position(m, f"폭발실패 | 15분 미수익 {cur_gain*100:.2f}%")
                    _already_closed = True
                    verdict = "V7_SURGE_FAIL"
                    print(f"[V7_SURGE_FAIL] {m} 폭발 15분 미수익 → 청산")
                    break

            # === 2) 트레일링 손절: 이익이 나야만 무장
            gain_from_entry = (curp / entry_price - 1.0) if entry_price > 0 else 0

            # 🔧 특단조치: PROBE→CONFIRM 전환 로직 제거 (probe 폐지 → 불필요)

            # 🔧 before1 복원: 독립 trail_armed 블록 (단순 체크포인트 기반 무장)
            if (not trail_armed) and gain_from_entry >= dyn_checkpoint:
                trail_armed = True
                # 🔧 FIX: trail_stop 최소보장 = entry × (1 + CP×0.5)
                # 기존: curp × 0.9985 → CP 직후 반락 시 +0.15% 청산 → 수수료 후 손실
                # 수정: 최소 CP의 50%는 확보 (실질 수익 보장)
                _trail_raw = curp * (1.0 - trail_dist_min)
                _trail_min_floor = entry_price * (1.0 + dyn_checkpoint * 0.5)
                trail_stop = max(_trail_raw, _trail_min_floor)
                print(f"[TRAIL_ARM] {m} +{gain_from_entry*100:.2f}% ≥ CP {dyn_checkpoint*100:.2f}% → 트레일 무장 (floor +{dyn_checkpoint*50:.2f}%)")

            # === 🔧 매도구조개선: 래칫 완화 — 트레일에 주역할 위임 ===
            # 3단계: CP(~0.3%)→본절, +3.5%→+1.8%, +5.0%→+3.0%
            # 트레일 0.15%가 메인 보호 → 래칫은 큰 수익 최저보장만 담당
            if trail_armed and gain_from_entry > 0:
                _ratchet_lock = 0
                if gain_from_entry >= 0.050:      # +5.0% → 최소 +3.0% 확보 (60%)
                    _ratchet_lock = entry_price * (1.0 + 0.030)
                elif gain_from_entry >= 0.035:    # +3.5% → 최소 +1.8% 확보 (51%)
                    _ratchet_lock = entry_price * (1.0 + 0.018)
                elif gain_from_entry >= dyn_checkpoint:  # 체크포인트(~0.25%) → 실질수익 보호
                    # 🔧 FIX: CP×0.5 = 0.125% 확보 (수수료+슬립 커버)
                    _ratchet_lock = entry_price * (1.0 + dyn_checkpoint * 0.5)
                if _ratchet_lock > base_stop:
                    base_stop = _ratchet_lock

            # === 2-1) 🔧 변경안D: 피라미딩(추매) — 눌림→재돌파 기반 + best/worst 보존 ===
            # 기존: gain + flow + EMA 기반 → 고점 추격 추매 위험
            # 변경: 반드시 0.4% 이상 눌림 후 고점 근처 재탈환에서만 추매
            #       best/worst 리셋하지 않아 MAE/MFE 왜곡 방지
            if USE_PYRAMIDING and AUTO_TRADE:
                t15_now = micro_tape_stats_from_ticks(ticks, 15)

                # 🔧 FIX: 루프 상단 오더북 스냅샷 재사용 (API 중복 호출 제거)
                ob_pyr = ob_snap
                imb_pyr = calc_orderbook_imbalance(ob_pyr) if ob_pyr else 0.0

                # 🔧 변경안D: 눌림→재돌파 조건
                # (1) 고점 대비 0.4% 이상 눌림이 있었어야 함
                _pyr_drop = (best - curp) / best if best > 0 else 0
                _pyr_peak_drop = (best - worst) / best if best > 0 else 0
                add_cond_pullback = _pyr_peak_drop >= 0.004  # 진입 후 0.4% 이상 눌림 경험

                # (2) 고점 근처 재탈환 (고점의 99.5% 이상 회복)
                add_cond_rebreak = curp >= best * 0.995

                # (3) 최소 수익 기준
                add_cond_price = gain_from_entry >= max(0.005, PYRAMID_ADD_MIN_GAIN * 0.8)

                # (4) 매수세 + 호가 임밸런스 확인
                add_cond_flow = (
                    t15_now["buy_ratio"] >= 0.55 and
                    t15_now["krw_per_sec"] >= PYRAMID_ADD_FLOW_MIN_KRWPSEC * 0.8 and
                    last_two_ticks_fresh(ticks) and
                    imb_pyr >= 0.40
                )

                # 🔧 MAE/MFE 게이트: 흔들린 포지션엔 추매 금지
                mae_now = (worst / entry_price - 1.0) if entry_price > 0 else -1
                mfe_now_add = (best / entry_price - 1.0) if entry_price > 0 else 0
                add_cond_mfe = mae_now > -0.005 and mfe_now_add > 0.007  # 🔧 수익성패치: MAE>-0.5%(정상 눌림 허용), MFE>0.7%

                # 🔧 피라미딩 BTC 역풍 차단: BTC -0.3% 이하 + 수급 미달이면 추매 금지
                _btc5_pyr = btc_5m_change()
                add_cond_btc = True
                if _btc5_pyr <= -0.003:
                    # 초강한 수급(매수비 63%+ AND 임밸 55%+)이면 예외 허용
                    if not (t15_now["buy_ratio"] >= 0.63 and imb_pyr >= 0.55):
                        add_cond_btc = False

                if add_cond_price and add_cond_flow and add_cond_pullback and add_cond_rebreak and add_cond_mfe and add_cond_btc:
                    with _POSITION_LOCK:
                        pos = OPEN_POSITIONS.get(m)
                        already_added = pos.get("added") if pos else True
                        last_add_ts = pos.get("last_add_ts", 0.0) if pos else 0.0

                    cooldown_ok = (time.time() - last_add_ts) >= (PYRAMID_ADD_COOLDOWN_SEC * 0.6)

                    if pos and (not already_added) and cooldown_ok:
                        add_reason = f"눌림재돌파 (수익+{gain_from_entry*100:.1f}% 눌림{_pyr_peak_drop*100:.1f}%→재탈환 매수비{t15_now['buy_ratio']:.0%})"
                        ok_add, new_entry = add_auto_position(m, curp, add_reason)
                        if ok_add and new_entry:
                            entry_price = new_entry
                            # 🔧 변경안D: best/worst 보존 (MAE/MFE 왜곡 방지)
                            # 기존: best=worst=curp 리셋 → 리스크 추적 초기화로 왜곡
                            # 변경: entry_price만 새 평단으로, best/worst는 전체 트레이드 기준 유지
                            # best, worst 리셋하지 않음

                            # 추매 후 부분 상태만 리셋 (트레일/체크포인트는 유지)
                            # 🔧 수익성패치: trail_armed면 CP 리셋 스킵 (래칫 덮어쓰기 방지)
                            if not trail_armed:
                                checkpoint_reached = False  # 새 평단 기준 체크포인트 재평가
                            mfe_partial_done = False    # 새 기회 허용
                            plateau_partial_done = False
                            last_peak_ts = time.time()
                            stop_first_seen_ts = 0.0
                            stop_hits = 0
                            trail_db_first_ts = 0.0
                            trail_db_hits = 0
                            _c1_cache = None; _c1_cache_ts = 0.0
                            c1_for_sl = _get_c1_cached()
                            _new_stop, eff_sl_pct, atr_info = dynamic_stop_loss(entry_price, c1_for_sl, signal_type=signal_type_for_sl, current_price=curp, trade_type=trade_type, market=m)  # 🔧 FIX: signal_type/current_price/trade_type 전달
                            base_stop = max(base_stop, _new_stop)  # 🔧 FIX: 래칫 보호 (추매 후 SL 하향 방지)
                            # trail은 유지 (이미 무장된 상태면 새 평단 기준으로 계속)
                            tg_send_mid(f"🔧 {m} 추매(눌림재돌파) 평단→{fmt6(new_entry)} | best/worst 보존")

            # 🔧 변경안C: 모멘텀 기반 동적 트레일 업데이트 + 러너 모드
            # 러너(max_gain >= 2*CP): 약세에도 트레일 넓게 유지 (추세 중간 눌림 허용)
            # 비러너: 기존 강세→넓게, 약세→좁게
            if trail_armed and new_high:
                atr = atr14_from_candles(_get_c1_cached(), 14)
                if atr and atr > 0:
                    base_trail = max(trail_dist_min,
                                     (atr / max(curp, 1)) * TRAIL_ATR_MULT)
                else:
                    base_trail = trail_dist_min

                # 러너 판정: MFE가 체크포인트의 2배 이상
                _trail_max_gain = (best / entry_price - 1.0) if entry_price > 0 else 0
                _is_trail_runner = _trail_max_gain >= (dyn_checkpoint * 2.0)

                # 현재 모멘텀 강도 측정 (매수비 + 거래속도)
                _trail_t10 = micro_tape_stats_from_ticks(ticks, 10)
                _trail_momentum = 1.0

                if _is_trail_runner:
                    # 🔧 러너 모드: 약세에도 트레일 최소 1.0x 유지 (추세 눌림 허용)
                    # 강세면 1.4x 확대, 약세여도 1.0x (축소 안 함)
                    if _trail_t10["buy_ratio"] >= 0.65 and _trail_t10["krw_per_sec"] >= 25000:
                        _trail_momentum = 1.4
                    else:
                        _trail_momentum = 1.0  # 러너: 약세에도 축소 없이 기본 유지
                    # 🔧 수익성패치: 러너 래칫 MFE 구간별 차등 (큰 수익일수록 더 많이 잠금)
                    if _trail_max_gain >= 0.05:    # +5% 이상: 55% 잠금
                        _ratchet_pct = 0.55
                    elif _trail_max_gain >= 0.03:  # +3% 이상: 50% 잠금
                        _ratchet_pct = 0.50
                    else:                          # 기본: 40% 잠금
                        _ratchet_pct = 0.40
                    _runner_lock = entry_price * (1.0 + max(FEE_RATE + 0.001, _trail_max_gain * _ratchet_pct))
                    base_stop = max(base_stop, _runner_lock)
                    # 🔧 FIX: 러너 래칫을 OPEN_POSITIONS에 저장
                    with _POSITION_LOCK:
                        _p_ratchet = OPEN_POSITIONS.get(m)
                        if _p_ratchet:
                            _p_ratchet["stop"] = base_stop
                else:
                    # 비러너: 기존 로직 (강세 확대, 약세 축소)
                    if _trail_t10["buy_ratio"] >= 0.65 and _trail_t10["krw_per_sec"] >= 25000:
                        _trail_momentum = 1.4  # 강세: 트레일 40% 확대
                    elif _trail_t10["buy_ratio"] < 0.50 or _trail_t10["krw_per_sec"] < 10000:
                        _trail_momentum = 0.90  # 🔧 익절극대화: 0.8→0.90 (약세 시 트레일 축소 최소화, 숨고르기 허용)

                trail_dist = base_trail * _trail_momentum
                trail_stop = max(trail_stop, curp * (1.0 - trail_dist))
                # 🔧 FIX: trail_stop이 base_stop 아래로 내려가지 않도록 바닥 보장
                trail_stop = max(trail_stop, base_stop)

            # 🔧 트레일링 손절 실제 청산 트리거 (디바운스 적용)
            if trail_armed and curp < trail_stop:
                # 디바운스: 연속 N회 또는 T초 유지 시에만 실제 청산
                if trail_db_first_ts == 0.0:
                    trail_db_first_ts = time.time()
                    trail_db_hits = 1
                else:
                    trail_db_hits += 1
                _trail_dur = time.time() - trail_db_first_ts
                # 🔧 FIX: 트레일 디바운스를 trade_type별로 차등
                # 기존: SL+2회 +5초 고정 → scalp에서 큰 수익 되돌림 허용
                # 변경: scalp는 빠른 확정(1회/2초), runner는 기존대로 강하게(꼬리 살리기)
                if trade_type == "scalp":
                    _tdb_n = 1  # scalp는 1회 hit으로 즉시 청산
                    _tdb_sec = EXIT_DEBOUNCE_SEC + (2 if alive_sec < WARMUP_SEC else 0)  # 시간 조건은 유지
                else:
                    # 🔧 수익성패치: +2/+5 → +1/+3 (러너도 반응 10초 단축, 되돌림 손실 감소)
                    _tdb_n = EXIT_DEBOUNCE_N + 1 + (1 if alive_sec < WARMUP_SEC else 0)
                    _tdb_sec = EXIT_DEBOUNCE_SEC + 3 + (2 if alive_sec < WARMUP_SEC else 0)
                if trail_db_hits >= _tdb_n or _trail_dur >= _tdb_sec:
                    # 디바운스 통과 → 실제 청산
                    # 🔧 FIX: Division by Zero 방어 (entry_price, best는 항상 양수여야 함)
                    trail_gain = (curp / entry_price - 1.0) if entry_price > 0 else 0
                    peak_gain = (best / entry_price - 1.0) if entry_price > 0 else 0
                    drop_pct = (best - curp) / best * 100 if best > 0 else 0
                    close_auto_position(m, f"트레일링손절 +{trail_gain*100:.2f}% (고점+{peak_gain*100:.2f}%에서 -{drop_pct:.2f}% 하락, 디바운스 {trail_db_hits}회/{_trail_dur:.1f}초)")
                    _already_closed = True
                    verdict = "TRAIL_STOP"
                    break
                # else: 디바운스 대기 중 → 다음 체크로 진행
            elif trail_armed:
                # 🔧 FIX C4: 트레일 디바운스 partial decay (풀 리셋 → 점진 감소)
                # - 기존: 가격 회복 시 즉시 0으로 리셋 → 노이즈 진동 시 영원히 미발동
                # - 개선: 0.2% 이상 회복 시에만 풀 리셋, 그 외엔 hits -1 (최소 0)
                if trail_db_first_ts > 0:
                    _recovery_margin = curp / trail_stop - 1.0 if trail_stop > 0 else 0
                    if _recovery_margin >= 0.002:  # 0.2% 이상 회복 = 진짜 반등
                        trail_db_first_ts = 0.0
                        trail_db_hits = 0
                    else:
                        # 미세 회복: 점진 감소 (노이즈 진동에 강건)
                        trail_db_hits = max(0, trail_db_hits - 1)


            # === 🔥 심플 체크포인트 매도 로직 ===
            cur_gain = (curp / entry_price - 1.0)
            # 🔧 FIX: 루프 상단 오더북 스냅샷 재사용 (API 중복 호출 제거)
            ob_now = ob_snap
            # === 🎯 ATR 기반 동적 손절 (틱스탑 제거됨) ===
            # 이미 line 4876에서 웜업 없이 즉시 ATR 손절 적용 중
            # 여기는 트레일링 손절 이후 추가 손절 판정용

            # ② 체크포인트 도달 시 강세/약세 판단
            # 🔧 FIX: 체크포인트 재평가 - 가격이 50% 아래로 떨어지면 리셋 (0.3→0.5: 상태진동 방지)
            # 🔧 수익성패치: trail_armed 상태에서는 CP 리셋 스킵 (래칫 보호 일관성)
            if checkpoint_reached and cur_gain < (dyn_checkpoint * 0.5) and not trail_armed:
                checkpoint_reached = False  # 체크포인트 아래로 떨어짐 → 재평가 허용

            # 🔧 소프트 가드: 초기 30초간 손절/트레일 디바운스 강화 (false breakout 방어)
            in_soft_guard = alive_sec < SOFT_GUARD_SEC

            # 🔧 v5: HOLD 전략 봉 카운팅 (1분봉 기준)
            # 데이터: 15m_눌림반전 HOLD_12봉 = 1분봉 12봉(12분) 보유 후 현재가 청산
            _elapsed_bars_1m = int(alive_sec / 60)  # 1분봉 기준 경과 봉 수
            _hold_complete = (_v4_hold_bars == 0) or (_elapsed_bars_1m >= _v4_hold_bars)

            # 🔧 v5: HOLD 전략 — 봉 완료 시 현재가로 즉시 청산 (트레일 없음)
            if _v4_strategy == "HOLD" and _v4_hold_bars > 0 and _elapsed_bars_1m >= _v4_hold_bars:
                _hold_gain = (curp / entry_price - 1.0) if entry_price > 0 else 0
                close_auto_position(m, f"HOLD_{_v4_hold_bars}봉 완료 ({_elapsed_bars_1m}분) | {'+' if _hold_gain >= 0 else ''}{_hold_gain*100:.2f}%")
                _already_closed = True
                verdict = "HOLD_COMPLETE"
                break

            # v4 max_bars 초과 시 강제 청산 (1분봉 기준)
            if _v4_max_bars > 0 and _elapsed_bars_1m >= _v4_max_bars and cur_gain > 0:
                close_auto_position(m, f"v4 max_bars({_v4_max_bars}) 초과 | +{cur_gain*100:.2f}%")
                _already_closed = True
                verdict = "v4_max_bars"
                break

            if not checkpoint_reached and cur_gain >= dyn_checkpoint and _hold_complete:
                checkpoint_reached = True
                # 🔧 v5: HOLD 전략은 트레일 비활성화 (봉 완료 시 즉시 청산)
                if _v4_strategy == "HOLD":
                    # HOLD 전략: 트레일 없음, 봉 완료까지 SL만 유지
                    tg_send_mid(f"⏳ {m} +{cur_gain*100:.2f}% HOLD_{_v4_hold_bars}봉 대기중 ({_elapsed_bars_1m}/{_v4_hold_bars}분)")
                else:
                    trail_armed = True
                    atr = atr14_from_candles(_get_c1_cached(), 14)
                    if atr and atr > 0:
                        trail_dist = max(trail_dist_min, (atr / max(curp, 1)) * TRAIL_ATR_MULT)
                    else:
                        trail_dist = trail_dist_min
                    trail_stop = max(trail_stop, curp * (1.0 - trail_dist), base_stop)
                    # 본절 확보 (래칫 기본)
                    be_stop = entry_price * (1.0 + FEE_RATE + 0.0005)
                    base_stop = max(base_stop, be_stop)
                    # 래칫 stop을 OPEN_POSITIONS에 저장 (remonitor 복원용)
                    with _POSITION_LOCK:
                        _p_ratchet = OPEN_POSITIONS.get(m)
                        if _p_ratchet:
                            _p_ratchet["stop"] = base_stop
                    if trade_type == "runner":
                        tg_send_mid(f"🏃 {m} +{cur_gain*100:.2f}% 러너 CP도달 → 트레일 무장 (dist={trail_dist*100:.2f}%, 부분익절 없음)")
                    else:
                        tg_send_mid(f"⚡ {m} +{cur_gain*100:.2f}% 스캘프 CP도달 → 트레일 무장 (TP 대기중)")

            # 🔧 [제거됨] 강세모드 동적 트레일링 → 일반 트레일링(0.2% 간격)으로 대체

            # 🔧 [제거됨] Giveback Cap / Peak Giveback → 트레일링으로 대체
            # 트레일링 간격 0.25%로 타이트화하여 동일 효과 달성
            max_gain = (best / entry_price - 1.0) if entry_price > 0 else 0  # MFE 수익률 (다른 곳에서 사용)
            cur_gain_now = (curp / entry_price - 1.0) if entry_price > 0 else 0  # 현재 수익률

            # ============================================================
            # 🔧 매도구조개선: 매수세감쇄 익절 제거
            # 이유: SL 1.0%까지 참겠다고 해놓고 +0.5%에서 자르면 R:R 0.5:1.0
            #       SL이 보호해주는데 +0.5%에서 또 보호할 필요 없음
            #       트레일링 + SL이 알아서 처리 → 중간 청산 제거로 큰 수익 보존
            # ============================================================
            # (매수세감쇄 익절 비활성화 — 트레일링/SL에 위임)

            # ============================================================
            # 🔧 특단조치: trade_type 기반 청산 (스캘프 vs 러너 이진분류)
            # 스캘프: MFE 타겟 도달 시 100% 전량 익절 (깔끔한 수익 확정)
            # 러너: MFE 타겟 도달 시 30%만 익절, 나머지 70% 넓은 트레일
            # → 기존 MFE 50% + Plateau 50% = 25% 남는 문제 해결
            # ============================================================
            if trade_type == "scalp":
                # === 스캘프 모드: 빠른 전량 익절 ===
                # 🔧 구조개선: R:R 연동 + 코인별 변동성 맞춤 MFE
                # 핵심: 코인의 실제 ATR%를 기준 ATR(0.5%) 대비 비율로 MFE 스케일링
                # - 저변동 코인(ATR 0.3%): vol_factor=0.7 → 타겟 축소 → 도달 가능한 목표
                # - 고변동 코인(ATR 1.0%): vol_factor=1.8 → 타겟 확대 → 큰 수익 포착
                _rr_mult = MFE_RR_MULTIPLIERS.get(signal_tag, 2.0)  # fallback 2.0 (테이블 미매칭 시 보수적)
                mfe_base = max(eff_sl_pct * _rr_mult, MFE_PARTIAL_TARGETS.get(signal_tag, 0.020))  # SL 2.0%×2.0=4.0%
                try:
                    c1_mfe = _get_c1_cached()
                    atr_raw = atr14_from_candles(c1_mfe) if c1_mfe and len(c1_mfe) >= 15 else None
                    if atr_raw and curp > 0:
                        atr_pct = atr_raw / curp
                        # 🔧 코인별 변동성 팩터: 기준 ATR 0.5% 대비 비율
                        _vol_factor = max(0.7, min(1.8, atr_pct / 0.005))
                        mfe_target = max(mfe_base * _vol_factor, dyn_checkpoint + 0.002)
                    else:
                        mfe_target = mfe_base
                except Exception:
                    mfe_target = mfe_base
                # 🔧 WF데이터: BTC 방향 조정 비활성화 (데이터에 없는 로직)
                # btc5_now = btc_5m_change()
                # if btc5_now <= -0.004:
                #     mfe_target *= 0.80
                # elif btc5_now >= 0.004:
                #     mfe_target *= 1.30
                # ★ 개선: 스캘프 MFE 도달 시 모멘텀 확인 → 러너 전환 or 전량익절
                if not mfe_partial_done and max_gain >= mfe_target and (time.time() - last_exit_event_ts) >= EXIT_EVENT_COOLDOWN_SEC:
                    # 현재 모멘텀 체크: 매수비+가속도 충분하면 러너로 전환
                    _tp_t10 = micro_tape_stats_from_ticks(ticks, 10)
                    _tp_buy_r = _tp_t10.get("buy_ratio", 0)
                    _tp_accel = calc_flow_acceleration(ticks)
                    _momentum_alive = (_tp_buy_r >= SCALP_TO_RUNNER_MIN_BUY and _tp_accel >= SCALP_TO_RUNNER_MIN_ACCEL)

                    if _momentum_alive:
                        # ★ 스캘프→러너 자동전환: 50% 익절 + 50% 러너 트레일
                        ok, msg, sold = safe_partial_sell(
                            m, 0.50,
                            reason=f"스캘프→러너전환 +{cur_gain_now*100:.2f}% (매수{_tp_buy_r:.0%} 가속{_tp_accel:.1f}x)")
                        if ok and sold > 0:
                            # 🔧 FIX: dust방지로 전량청산됐는지 확인 (오해 알림 방지)
                            with _POSITION_LOCK:
                                _pos_up = OPEN_POSITIONS.get(m)
                            if not _pos_up:
                                _already_closed = True
                                verdict = "스캘프_TP_DUST"
                                break
                            mfe_partial_done = True
                            last_exit_event_ts = time.time()
                            trade_type = "runner"  # ★ 러너로 승격
                            # 🔧 FIX: OPEN_POSITIONS에도 반영 — remonitor 재호출 시 trade_type 유지
                            with _POSITION_LOCK:
                                _pos_up2 = OPEN_POSITIONS.get(m)
                                if _pos_up2:
                                    _pos_up2["trade_type"] = "runner"
                            tg_send_mid(f"🚀 {m} 스캘프→러너 전환! 50% 익절 +{cur_gain_now*100:.2f}% | 나머지 트레일링")
                    else:
                        # 모멘텀 소진 → 전량 익절
                        close_auto_position(m, f"스캘프TP +{cur_gain_now*100:.2f}% (MFE+{max_gain*100:.2f}% 타겟{mfe_target*100:.2f}%)")
                        _already_closed = True
                        verdict = "스캘프_TP"
                        tg_send_mid(f"💰 {m} 스캘프 전량익절 +{cur_gain_now*100:.2f}% | MFE+{max_gain*100:.2f}%")
                        break
                # 🔧 매도구조개선: Plateau 전량익절 제거
                # 이유: 횡보는 거래의 자연스러운 과정 (숨고르기)
                #       +1.5%에서 90초 쉬다가 +3.0% 가는 거래를 +1.5%에서 잘라먹음
                #       트레일링이 알아서 하락 시 처리 → 횡보 자체는 문제 아님
                # (Plateau 비활성화 — 트레일링에 위임)

            else:
                # === 러너 모드: 30% 익절 + 70% 넓은 트레일 ===
                # 🔧 구조개선: R:R 연동 + 코인별 변동성 맞춤 MFE (러너는 더 넓게)
                _rr_mult = MFE_RR_MULTIPLIERS.get(signal_tag, 2.0) + 0.3  # fallback 2.3 (러너 +0.3 보너스)
                mfe_base = max(eff_sl_pct * _rr_mult, MFE_PARTIAL_TARGETS.get(signal_tag, 0.020))  # SL 2.0%×2.3=4.6%
                try:
                    c1_mfe = _get_c1_cached()
                    atr_raw = atr14_from_candles(c1_mfe) if c1_mfe and len(c1_mfe) >= 15 else None
                    if atr_raw and curp > 0:
                        atr_pct = atr_raw / curp
                        # 🔧 코인별 변동성 팩터: 러너는 0.8~2.0 (스캘프보다 넓은 범위)
                        _vol_factor = max(0.8, min(2.0, atr_pct / 0.005))
                        mfe_target = max(mfe_base * _vol_factor, dyn_checkpoint + 0.003)
                    else:
                        mfe_target = mfe_base
                except Exception:
                    mfe_target = mfe_base
                btc5_now = btc_5m_change()
                if btc5_now <= -0.004:
                    mfe_target *= 0.80
                elif btc5_now >= 0.004:
                    mfe_target *= 1.30
                # 러너: MFE 도달 시 모멘텀 체크 후 익절 비율 결정
                if not mfe_partial_done and max_gain >= mfe_target and (time.time() - last_exit_event_ts) >= EXIT_EVENT_COOLDOWN_SEC:
                    # 🔧 모멘텀 체크: 죽었으면 40%, 살아있으면 25%
                    _rn_t10 = micro_tape_stats_from_ticks(ticks, 10)
                    _rn_momentum = (
                        _rn_t10.get("buy_ratio", 0) >= 0.55 and
                        _rn_t10.get("krw_per_sec", 0) >= 12000 and
                        uptick_streak_from_ticks(ticks, need=2)
                    )
                    _rn_sell_ratio = 0.25 if _rn_momentum else 0.40
                    _rn_label = f"러너{int(_rn_sell_ratio*100)}%익절"
                    ok, msg, sold = safe_partial_sell(
                        m, _rn_sell_ratio,
                        f"{_rn_label} +{max_gain*100:.2f}% (경로:{signal_tag}, 타겟:{mfe_target*100:.2f}%, 모멘텀:{'O' if _rn_momentum else 'X'})"
                    )
                    if ok:
                        # 🔧 FIX: dust방지로 전량청산됐는지 확인 (오해 알림 방지)
                        with _POSITION_LOCK:
                            _pos_runner = OPEN_POSITIONS.get(m)
                        if not _pos_runner:
                            _already_closed = True
                            verdict = "러너_TP_DUST"
                            break
                        mfe_partial_done = True
                        last_exit_event_ts = time.time()
                        # 🔧 수익성패치: 래칫 70→55% (러너 추세연장 여유 확보, 이미 25~40% 익절함)
                        mfe_lock_pct = max(FEE_RATE + 0.001, max_gain * 0.55)
                        be_stop = entry_price * (1.0 + mfe_lock_pct)
                        base_stop = max(base_stop, be_stop)
                        # 🔧 FIX: MFE 래칫을 OPEN_POSITIONS에 저장
                        with _POSITION_LOCK:
                            _p_ratchet = OPEN_POSITIONS.get(m)
                            if _p_ratchet:
                                _p_ratchet["stop"] = base_stop
                        _rn_remain = int((1 - _rn_sell_ratio) * 100)
                        tg_send_mid(f"🏃 {m} {_rn_label} | +{max_gain*100:.2f}% | {_rn_remain}% 트레일중 | 손절→+{mfe_lock_pct*100:.2f}%")
                # 러너: Plateau에서는 부분익절 안함 (트레일과 래칫에 맡김)

            # ④ 눌림 후 재상승 감지 → 트레일 강화 (재진입 대신)
            drop_from_high = (best - curp) / best if best > 0 else 0
            if drop_from_high >= 0.005 and uptick_streak_from_ticks(ticks, need=3):
                if trail_armed:
                    # 재진입 대신 트레일 간격 타이트하게 강화 (🔧 손절폭 연동)
                    trail_stop = max(trail_stop, curp * (1.0 - max(trail_dist_min * 0.8, 0.0025)))
                    tg_send_mid(f"🔧 {m} 재상승 감지 → 트레일 강화")
        
        # === 🔧 변경안E: 시간 만료 2단계화 ===
        # 손실 중 → 즉시 청산 (추가 손실 방지)
        # 수익/본절 중 → 90초 연장하여 러너 트레일로 추가 수익 기회
        if verdict is None:
            _final_gain = (curp / entry_price - 1.0) if entry_price > 0 else 0
            if _final_gain <= -FEE_RATE:
                # 손실 상태 → 즉시 청산
                close_auto_position(m, f"시간만료 손실컷 {_final_gain*100:.2f}%")
                _already_closed = True
                verdict = "시간만료_손실컷"
            elif trail_armed and _final_gain > FEE_RATE:
                # 🔧 수익성패치: 스캘프/러너 연장시간 차등 (스캘프는 빠른 확정)
                _ext_horizon = 90 if trade_type == "runner" else 30
                tg_send_mid(f"⏰ {m} 시간만료 but 수익중 +{_final_gain*100:.2f}% → {_ext_horizon}초 연장 ({trade_type})")
                _ext_start = time.time()
                _ext_trail_hits = 0  # 🔧 FIX: 트레일 디바운스 (1틱 노이즈 방지)
                while time.time() - _ext_start <= _ext_horizon:
                    time.sleep(RECHECK_SEC)
                    # 포지션 존재 확인
                    with _POSITION_LOCK:
                        if m not in OPEN_POSITIONS:
                            _already_closed = True
                            verdict = "연장중_전량청산"
                            break
                    ticks = get_recent_ticks(m, 100)
                    if not ticks:
                        continue
                    curp = max(ticks, key=tick_ts_ms).get("trade_price", curp)
                    last_price = curp

                    # 🔧 FIX: 신고점 판정을 best 갱신 전에 수행 (기존: best 먼저 갱신 → 조건 항상 False)
                    # - 기존 순서: best=max(best,curp) → if curp>best: (절대 True 불가)
                    # - 수정: 신고점 판정 → trail 갱신 → best 갱신
                    if curp > best:
                        # 🔧 FIX: ATR 기반 trail_dist 사용 (trail_dist_min 고정 → 본루프와 동일 ATR 반영)
                        _ext_atr = atr14_from_candles(_get_c1_cached(), 14)
                        if _ext_atr and _ext_atr > 0:
                            _ext_trail_dist = max(trail_dist_min, (_ext_atr / max(curp, 1)) * TRAIL_ATR_MULT)
                        else:
                            _ext_trail_dist = trail_dist_min
                        trail_stop = max(trail_stop, curp * (1.0 - _ext_trail_dist))
                    best = max(best, curp)
                    worst = min(worst, curp)  # 🔧 FIX: 연장루프에서도 MAE 추적 (maxdd 정확도)

                    # 트레일 체크 (🔧 FIX: 2회 디바운스 — 메인루프와 동일 패턴)
                    if curp < trail_stop:
                        _ext_trail_hits += 1
                        if _ext_trail_hits >= 2:
                            _ext_gain = (curp / entry_price - 1.0) if entry_price > 0 else 0
                            close_auto_position(m, f"연장트레일컷 +{_ext_gain*100:.2f}%")
                            _already_closed = True
                            verdict = "연장_TRAIL_STOP"
                            break
                    else:
                        _ext_trail_hits = 0
                    # base_stop 체크
                    if base_stop > 0 and curp <= base_stop:
                        _ext_gain = (curp / entry_price - 1.0) if entry_price > 0 else 0
                        close_auto_position(m, f"연장래칫컷 +{_ext_gain*100:.2f}%")
                        _already_closed = True
                        verdict = "연장_RATCHET_STOP"
                        break
                    # 🔧 FIX: ATR 동적 손절 체크 (연장루프에서도 가격 폭락 방어)
                    _ext_sl_price, _ext_sl_pct, _ = dynamic_stop_loss(entry_price, _get_c1_cached(), signal_type=signal_type_for_sl, current_price=curp, trade_type=trade_type, market=m)  # 🔧 FIX: trade_type 전달 (scalp/runner SL 분리)
                    if _ext_sl_price > 0 and curp <= _ext_sl_price:
                        _ext_gain = (curp / entry_price - 1.0) if entry_price > 0 else 0
                        close_auto_position(m, f"연장ATR손절 {_ext_gain*100:.2f}% (SL {_ext_sl_pct*100:.2f}%)")
                        _already_closed = True
                        verdict = "연장_ATR_STOP"
                        break

                if verdict is None:
                    verdict = "연장만료(모니터링 종료)"
            else:
                # 🔧 FIX: reentry(리모니터 사이클)에서 수익 중이면 다음 사이클로 이월
                # — 기존: 60초마다 본절컷 → 상승 추세 중 +0.12%에서 조기 청산 (ZRO 사례)
                # — 수정: reentry + 수익 → 다음 사이클에서 계속 감시 (트레일 무장 기회 부여)
                if reentry and _final_gain > 0:
                    verdict = "연장만료(모니터링 종료)"  # non-closing → remonitor 다음 사이클
                else:
                    close_auto_position(m, f"시간만료 본절컷 {_final_gain*100:+.2f}%")
                    _already_closed = True
                    verdict = "시간만료_본절컷"

    finally:
        # ================================
        # 1) 최신 상태 / 수익률 계산
        # ================================
        ticks = get_recent_ticks(m, 100)
        t15 = micro_tape_stats_from_ticks(ticks, 15) if ticks else {
            "buy_ratio": 0,
            "krw": 0,
            "n": 0,
            "krw_per_sec": 0
        }

        ob = pre.get("ob") or {}
        ob_depth_krw = ob.get("depth_krw", 10_000_000)

        # 🔧 FIX: Division by Zero 방어
        if entry_price > 0:
            try:
                ret_pct = ((last_price / entry_price - 1.0) - FEE_RATE) * 100.0
            except Exception:
                ret_pct = 0.0
            maxrun = (best / entry_price - 1.0) * 100.0
            maxdd = (worst / entry_price - 1.0) * 100.0
        else:
            ret_pct = 0.0
            maxrun = 0.0
            maxdd = 0.0

        # ================================
        # 2) 끝알람 문구 생성
        # ================================
        # 🔧 FIX: 루프 종료 후 c1 갱신 (stale 데이터로 끝알람/ctx 판단 왜곡 방지)
        c1 = _get_c1_cached() or c1
        # 🔧 BUG FIX: _end_reco 예외 시 finally 블록 중단 → remonitor 미호출 방지
        try:
            action, rationale = _end_reco(m,
                                          entry_price,
                                          last_price,
                                          c1,
                                          ticks,
                                          ob_depth_krw,
                                          ctx_thr=CTX_EXIT_THRESHOLD)
        except Exception as _reco_err:
            print(f"[END_RECO_ERR] {m}: {_reco_err}")
            action, rationale = None, f"끝알람 생성 오류: {_reco_err}"

        # ===========================================
        # 🔧 FIX: 청산 없이 모니터 종료 → 재모니터 전환 알림
        #  (진입 알림만 오고 청산 알림이 안 오는 문제 해결)
        # ===========================================
        if not _already_closed and not reentry and verdict in (
            "연장만료(모니터링 종료)",  # 🔧 FIX: "시간 만료(모니터링 종료)" 제거 (미사용 verdict)
            "데이터 수신 실패",
        ):
            _g = (last_price / entry_price - 1.0) * 100 if entry_price > 0 else 0
            tg_send(f"🔄 {m} {verdict} → 재모니터 전환\n"
                    f"• 현재가 {last_price:,.0f} ({_g:+.2f}%)\n"
                    f"• 자동 청산까지 계속 감시합니다")

        # ===========================================
        # 재모니터링 루프 시작
        #  - 최초 모니터링에서만 호출
        #  - remonitor_until_close()에서 재호출된 경우(reentry=True)는 다시 안 들어감
        # ===========================================
        # ✅ 재모니터링 알림 비활성화 (불필요한 반복 메시지 방지)
        # (실제 로직은 유지하지만, 알림 발송만 차단)
        if AUTO_TRADE and m in OPEN_POSITIONS and not reentry and not _already_closed:
            remonitor_until_close(m, entry_price, pre, tight_mode)

        # 🔧 특단조치: probe 손절 후 재진입 로직 제거 (probe 폐지 → 불필요)

        # 👇 이 return 은 if 바깥에서 항상 실행되게
        return (
            verdict,
            action,
            rationale,
            ret_pct,
            last_price,
            maxrun,
            maxdd,
        )


# =========================
# 알림
# =========================
def _tg_split_message(text, max_len=4000):
    """긴 메시지를 구분선(━━━) 기준으로 분할, 각 파트 max_len 이내
    🔧 FIX: 구분선 재결합 시 중복 방지, 줄바꿈 보존"""
    sep = "━━━━━━━━━━━━━━━━"
    # 구분선 기준 분할 후 각 섹션에 구분선 포함하여 재조립
    parts = text.split(sep)
    # 첫 파트 제외, 나머지는 구분선 앞에 붙여줌
    sections = [parts[0]]
    for p in parts[1:]:
        sections.append(sep + p)

    chunks = []
    current = ""
    for sec in sections:
        if not current:
            current = sec
        elif len(current) + len(sec) <= max_len:
            current += sec
        else:
            if current.strip():
                chunks.append(current.rstrip())
            current = sec.lstrip("\n")
    if current.strip():
        chunks.append(current.rstrip())
    # 안전장치: 단일 섹션이 max_len 초과 시 줄바꿈 기준 강제 분할
    final = []
    for chunk in chunks:
        while len(chunk) > max_len:
            cut = chunk[:max_len].rfind("\n")
            if cut < max_len // 2:
                cut = max_len
            final.append(chunk[:cut])
            chunk = chunk[cut:].lstrip("\n")
        if chunk.strip():
            final.append(chunk)
    return final if final else [text[:max_len]]


def tg_send(t, retry=3):
    """텔레그램 메시지 전송 (429 rate-limit 처리 + 지수 백오프 + 실패큐)
    🔧 FIX: _TG_SESSION 전용 세션 사용 (SESSION 리프레시 시 청산알림 유실 방지)
    🔧 FIX: 4096자 초과 메시지 자동 분할 (Telegram API 제한)
    """
    # TG_TOKEN 없거나 CHAT_IDS가 비어 있으면 콘솔에만 출력
    if not TG_TOKEN or not CHAT_IDS:
        print(t)
        return True

    # 🔧 FIX: Telegram 4096자 제한 → 초과 시 분할 전송 (잘림 방지)
    if len(t) > 4000:
        chunks = _tg_split_message(t, max_len=4000)
        ok_all = True
        for i, chunk in enumerate(chunks):
            if i > 0:
                time.sleep(0.3)  # rate-limit 방지
            if not tg_send(chunk, retry=retry):
                ok_all = False
        return ok_all

    def _tg_post(payload):
        """_TG_SESSION으로 전송, 실패 시 새 세션 시도
        🔧 FIX 7차: 락 안에서 전송까지 완료 (세션 교체 레이스 컨디션 수정)
        기존: 락 밖에서 sess.post() → 다른 스레드가 세션 교체 시 ConnectionError
        변경: 락 안에서 sess.post()까지 수행 → 세션 일관성 보장
        """
        global _TG_SESSION
        try:
            with _TG_SESSION_LOCK:
                return _TG_SESSION.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json=payload, timeout=10,
                )
        except Exception:
            # 세션 문제 → 새 세션 생성 후 재시도
            try:
                with _TG_SESSION_LOCK:
                    _TG_SESSION = _new_session()
                    return _TG_SESSION.post(
                        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                        json=payload, timeout=10,
                    )
            except Exception as e2:
                print(f"[TG] _tg_post 재시도도 실패: {e2}")
                return None

    ok_any = False
    for cid in CHAT_IDS:
        payload = {
            "chat_id": cid,
            "text": t,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        sent = False
        for attempt in range(retry + 1):
            try:
                r = _tg_post(payload)
                if r is None:
                    raise ConnectionError("_tg_post returned None")
                if r.status_code == 200:
                    js = r.json()
                    if js.get("ok") is True:
                        ok_any = True
                        sent = True
                        break
                # 🔧 429 Too Many Requests → Retry-After 만큼 대기
                elif r.status_code == 429:
                    try:
                        retry_after = r.json().get("parameters", {}).get("retry_after", 1)
                    except Exception:
                        retry_after = 1
                    print(f"[TG][{cid}] 429 rate-limit → {retry_after}초 대기 (시도 {attempt+1}/{retry+1})")
                    time.sleep(retry_after + 0.1)
                    continue  # 백오프 sleep 건너뜀 (이미 대기함)
                else:
                    # 디버깅용
                    print(f"[TG][{cid}] status={r.status_code} body={r.text[:200]}")
                    # 🔧 FIX: HTML 파싱 실패 시 plain text로 재시도
                    if r.status_code == 400 and "parse" in r.text.lower():
                        print(f"[TG][{cid}] HTML 파싱 실패 → plain text 재시도")
                        payload_plain = {
                            "chat_id": cid,
                            "text": re.sub(r"<[^>]+>", "", t),
                            "disable_web_page_preview": True,
                        }
                        try:
                            r2 = _tg_post(payload_plain)
                            if r2 and r2.status_code == 200 and r2.json().get("ok"):
                                ok_any = True
                                sent = True
                                break
                        except Exception:
                            pass
            except Exception as e:
                print(f"[TG][{cid}] exception: {e}")
            # 🔧 지수 백오프: 0.5s → 1s → 2s → 4s
            backoff = min(4, 0.5 * (2 ** attempt)) + rnd() * 0.3
            time.sleep(backoff)
        # 🔧 FIX: 모든 시도 실패 시 마지막으로 plain text 시도
        if not sent:
            try:
                payload_plain = {
                    "chat_id": cid,
                    "text": re.sub(r"<[^>]+>", "", t),
                    "disable_web_page_preview": True,
                }
                r3 = _tg_post(payload_plain)
                if r3 and r3.status_code == 200 and r3.json().get("ok"):
                    ok_any = True
                else:
                    # 🔧 최종 실패 → 큐에 저장
                    _tg_fail_queue.append((time.time(), cid, t))
                    print(f"[TG][{cid}] 전송 실패 → 큐 저장 (큐 크기: {len(_tg_fail_queue)})")
            except Exception as e:
                print(f"[TG][{cid}] final plain fallback failed: {e}")
                _tg_fail_queue.append((time.time(), cid, t))
    return ok_any


# 🔧 실패 메시지 재전송 큐
_tg_fail_queue = deque(maxlen=50)  # 최대 50개 보관
_tg_flush_lock = threading.Lock()


def tg_flush_failed():
    """실패한 메시지 재전송 시도 (메인 루프에서 주기적 호출)"""
    if not _tg_fail_queue:
        return
    with _tg_flush_lock:
        retried = 0
        while _tg_fail_queue and retried < 5:  # 한 번에 최대 5개
            ts, cid, msg = _tg_fail_queue[0]
            # 10분 이상 된 메시지는 버림
            if time.time() - ts > 600:
                _tg_fail_queue.popleft()
                continue
            try:
                payload = {
                    "chat_id": cid,
                    "text": f"[지연] {re.sub(r'<[^>]+>', '', msg)}",
                    "disable_web_page_preview": True,
                }
                # 🔧 FIX: 세션 사용도 락 안에서 (use-after-release 방지)
                with _TG_SESSION_LOCK:
                    r = _TG_SESSION.post(
                        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                        json=payload,
                        timeout=8,
                    )
                if r.status_code == 200 and r.json().get("ok"):
                    _tg_fail_queue.popleft()
                    retried += 1
                    print(f"[TG_FLUSH] 재전송 성공 ({retried}건)")
                elif r.status_code == 429:
                    break  # rate-limit면 다음 기회에
                else:
                    _tg_fail_queue.popleft()  # 복구 불가 오류면 버림
            except Exception:
                break  # 네트워크 오류면 다음 기회에
            time.sleep(0.3)


# =========================
# 메인 루프 준비
# =========================
last_signal_at = {}
recent_alerts = {}
last_price_at_alert = {}
last_reason = {}
# last_trade_was_loss → 상단(line 458)에서 초기화됨
# ALERT_TTL — config.py에서 정의됨

# === 🔧 1파/2파 추적 (데이터 기반: 1파 SL38% vs 2파+ SL85%) ===
# {market: {"ts": first_spike_time, "count": spike_count}}
# 30분 내 같은 코인 재급등 → 2파로 판정
_SPIKE_TRACKER = {}
_SPIKE_TRACKER_LOCK = threading.Lock()  # 🔧 FIX: 멀티스레드 경쟁 방지
# _SPIKE_WAVE_WINDOW — config.py에서 정의됨

def _cleanup_spike_tracker():
    """🔧 FIX: 만료된 _SPIKE_TRACKER 항목 제거 (메모리 누수 방지)"""
    _now = time.time()
    with _SPIKE_TRACKER_LOCK:
        expired = [m for m, v in _SPIKE_TRACKER.items()
                   if (_now - v["ts"]) >= _SPIKE_WAVE_WINDOW]
        for m in expired:
            del _SPIKE_TRACKER[m]

# =========================
# 시간대별 쿨다운 설정
# =========================
def get_cooldown_sec(market: str) -> int:
    """
    같은 종목 재진입 대기 시간(초)
    - 09시대: 3분
    - 10~14시: 5분
    - 그 외: 기본 COOLDOWN(8분)
    - 🔧 손실 후: 쿨다운 2배
    """
    h = now_kst().hour

    if h == 9:
        base = 180  # 3분
    elif 10 <= h <= 14:
        base = 300  # 5분
    else:
        base = COOLDOWN  # 전역 기본값(480)

    # 🔧 손실 후 동일 종목 재진입 쿨다운 (시간대별 차등)
    # - 9시대: 1.5배 (장초반 급등 기회 보호, 6분→4.5분)
    # - 그 외: 2배 (기존 유지)
    with _TRADE_LOSS_LOCK:
        _was_loss = last_trade_was_loss.get(market, False)
    if _was_loss:
        loss_mult = 1.5 if h == 9 else 2
        return int(base * loss_mult)

    return base

def cooldown_ok(market, price=None, reason=None):
    now = time.time()
    last = last_signal_at.get(market, 0)

    # ✅ 시간대별 동적 쿨다운 적용
    cooldown = get_cooldown_sec(market)

    # 기본 쿨다운 조건
    if (now - last) >= cooldown:
        return True

    # 히스테리시스(재돌파/되돌림 재진입 허용)는 기존 로직 유지
    if (now - last) >= REARM_MIN_SEC:
        lp = last_price_at_alert.get(market)
        rebreak = (price and lp and (price >= lp * (1.0 + REARM_PRICE_GAP)))
        reason_changed = (last_reason.get(market) != reason)
        rebreak_small = (price and lp
                         and (price >= lp * (1.0 + REARM_REBREAK_MIN))
                         and not reason_changed)
        pullback = (price and lp
                    and (price <= lp * (1.0 - REARM_PULLBACK_MAX)))
        if rebreak or rebreak_small or (pullback and reason_changed):
            return True
    return False

def cleanup_expired(dic, ttl):
    now = time.time()
    drop = [k for k, v in dic.items() if now - v >= ttl]
    for k in drop:
        dic.pop(k, None)


# =========================
# 설정 검증
# =========================
def validate_config():
    errors = []
    warnings = []
    if TOP_N > 200: errors.append(f"TOP_N={TOP_N} 너무 큼 (≤200 권장)")
    if STOP_LOSS_PCT >= 0.05:
        warnings.append(f"STOP_LOSS_PCT={STOP_LOSS_PCT*100:.1f}% 큼 (<5%)")
    if PARALLEL_WORKERS > 30:
        warnings.append(f"PARALLEL_WORKERS={PARALLEL_WORKERS} 과다")
    if MIN_TURNOVER <= 0 or MIN_TURNOVER >= 1:
        errors.append(f"MIN_TURNOVER={MIN_TURNOVER} 범위 오류 (0~1)")
    if TICKS_BUY_RATIO < 0.5 or TICKS_BUY_RATIO > 1:
        errors.append(f"TICKS_BUY_RATIO={TICKS_BUY_RATIO} 범위 오류 (0.5~1)")
    if not TG_TOKEN or not CHAT_IDS: warnings.append("텔레그램 미설정 - 콘솔 출력만 사용")
    if _BUCKET.get("rate", 0) <= 0: warnings.append("토큰버킷 rate<=0 → 0.1로 클램프")
    if _BUCKET.get("cap", 0) <= 0: warnings.append("토큰버킷 cap<=0 → 1.0로 클램프")
    if warnings:
        print("[CONFIG_WARNING]")
        for w in warnings:
            print("  ⚠️", w)
    if errors:
        print("[CONFIG_ERROR]")
        for e in errors:
            print("  ❌", e)
        sys.exit(1)
    print("✅ 설정 검증 완료")


# =========================
# 헬스체크 서버(옵션)
# =========================
from http.server import HTTPServer, BaseHTTPRequestHandler

bot_start_time = 0


class HealthHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            status = {
                "status":
                "running",
                "version":
                "3.2.7-hh+peakcut+perf+fix-final2+patch+postcheck6s+dynSL+ctxExit+netRetry",
                "uptime_sec":
                int(time.time() - bot_start_time),
                "uptime_str":
                str(timedelta(seconds=int(time.time() - bot_start_time))),
                "last_scan":
                now_kst_str(),
                "req_stats":
                dict(REQ_STATS),  # 🔧 FIX: 스냅샷 (직렬화 중 concurrent mutation 방지)
                "alerts_count":
                len(last_signal_at),
                "cache_size":
                len(_TICKS_CACHE.cache)
                if hasattr(_TICKS_CACHE, 'cache') else 0,
                "config": {
                    "top_n": TOP_N,
                    "scan_interval": SCAN_INTERVAL,
                    "stop_loss_pct": STOP_LOSS_PCT
                }
            }
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                json.dumps(status, ensure_ascii=False).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

def start_health_server(port=8080):
    for p in range(port, port + 5):
        try:
            server = HTTPServer(("127.0.0.1", p), HealthHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            print(f"🏥 Health check server: http://localhost:{p}/health")
            return
        except OSError:
            continue
    print("[HEALTH_ERR] 포트 사용 불가 (8080~8084)")


def start_watchdogs():
    """워치독 스레드들 시작: 헬스비트, 세션 리프레시, 락 청소"""

    def heartbeat():
        """5분마다 상태 로깅 + 모니터 watchdog (죽은 모니터 감지 → failsafe 청산)"""
        while True:
            try:
                time.sleep(300)  # 5분
                req_summary()     # 429/5xx/에러 카운트
                cut_summary()     # 필터 컷 카운트 요약
                print(f"[HB] {now_kst_str()} open={len(OPEN_POSITIONS)} "
                      f"rate={_BUCKET.get('rate', 0):.2f} cap={_BUCKET.get('cap', 0):.2f}")

                # === 모니터 watchdog: 포지션 있는데 모니터 죽은 경우 failsafe ===
                with _POSITION_LOCK:
                    open_markets = list(OPEN_POSITIONS.keys())
                for mk in open_markets:
                    with _MONITOR_LOCK:
                        mon = _ACTIVE_MONITORS.get(mk)
                    if mon is not None and mon.is_alive():
                        continue  # 모니터 정상
                    # 모니터가 없거나 죽은 경우
                    with _POSITION_LOCK:
                        pos = OPEN_POSITIONS.get(mk)
                    if not pos:
                        continue
                    pos_age = time.time() - pos.get("entry_ts", time.time())
                    if pos_age < 30:
                        continue  # 방금 진입 — 모니터 시작 중일 수 있음
                    # 🚨 모니터 죽은 포지션 발견 → failsafe 시장가 청산
                    print(f"[WATCHDOG] 🚨 {mk} 모니터 죽음 감지 (age={pos_age:.0f}s) → failsafe 청산")
                    tg_send(f"🚨 <b>WATCHDOG FAILSAFE</b> {mk}\n"
                            f"• 모니터 스레드 죽음 감지\n"
                            f"• 포지션 age: {pos_age:.0f}초\n"
                            f"• 즉시 시장가 청산 시도")
                    try:
                        close_auto_position(mk, f"watchdog_failsafe|monitor_dead|age={pos_age:.0f}s")
                    except Exception as wde:
                        print(f"[WATCHDOG_CLOSE_ERR] {mk}: {wde}")
                        tg_send(f"🚨 WATCHDOG 청산 실패 {mk}: {wde}")
            except Exception as e:
                print(f"[HB_ERR] {e}")

    def session_refresher():
        """10분마다 세션 리프레시 (커넥션 풀 정리)"""
        while True:
            time.sleep(600)  # 10분
            try:
                _refresh_session()
            except Exception as e:
                print(f"[SESSION_REFRESH_ERR] {e}")

    def lock_cleaner():
        """10분마다 오래된 락파일 정리"""
        while True:
            time.sleep(600)  # 10분
            try:
                cleanup_stale_entry_locks(900)  # 🔧 FIX: 300→900초 (모니터 최대 540초+여유 — 실행 중 파일락 오삭제 방지)
            except Exception as e:
                print(f"[LOCK_CLEANER_ERR] {e}")

    threading.Thread(target=heartbeat, daemon=True, name="Heartbeat").start()
    threading.Thread(target=session_refresher, daemon=True, name="SessionRefresh").start()
    threading.Thread(target=lock_cleaner, daemon=True, name="LockCleaner").start()
    print("🐕 워치독 시작됨 (헬스비트 5분, 세션리프레시 10분, 락청소 10분)")


# ===== 오더북 캐시 =====
def fetch_orderbook_cache(mkts):
    cache = {}
    for i in range(0, len(mkts), 15):
        js = safe_upbit_get("https://api.upbit.com/v1/orderbook",
            {"markets": ",".join(mkts[i:i + 15])},
            timeout=6)
        if not js: continue
        for ob in js:
            try:
                units = ob["orderbook_units"][:3]
                ask, bid = units[0]["ask_price"], units[0]["bid_price"]
                spread = (ask - bid) / max((ask + bid) / 2, 1) * 100
                askv = sum(u["ask_price"] * u["ask_size"] for u in units)
                bidv = sum(u["bid_price"] * u["bid_size"] for u in units)
                # 🔧 FIX: best_ask_krw 포함 (detect_leader_stock→stage1_gate에서 참조)
                best_ask_krw = units[0]["ask_price"] * units[0]["ask_size"]
                cache[ob["market"]] = {
                    "spread": spread,
                    "depth_krw": askv + bidv,
                    "best_ask_krw": best_ask_krw,
                    "raw": ob
                }
            except Exception as _ob_err:
                # 🔧 FIX H4: 오더북 파싱 실패 로깅 (silent 무시 → 누락 데이터 가시성 확보)
                _ob_market = ob.get("market", "?") if isinstance(ob, dict) else "?"
                print(f"[OB_PARSE_ERR] {_ob_market}: {_ob_err}")
    return cache


# =========================
# 메인
# =========================
SHARD_SIZE = TOP_N
_cursor = 0
_cursor_lock = threading.Lock()  # 🔧 FIX: _cursor 레이스 컨디션 방지


def main():
    global _cursor

    # 🧠 시작 시 학습된 가중치 & 매도 파라미터 로드
    if AUTO_LEARN_ENABLED:
        load_learned_weights()
        load_exit_params()

    # 💾 이전 세션 상태 복원 (TRADE_HISTORY, streak, 코인 손실 등)
    _load_bot_state()

    # 📡 섀도우 가상매매 누적 통계 로드
    _load_shadow_stats()

    tg_send(
        f"🚀 대장초입 헌터 v3.2.7+Score (자동학습+동적매도) 시작\n"
        f"📊 TOP {TOP_N} | 학습: {AUTO_LEARN_MIN_TRADES}건~ | {now_kst_str()}"
    )

    # 🔧 시작 시 유령 포지션 즉시 동기화
    global _LAST_ORPHAN_SYNC
    _LAST_ORPHAN_SYNC = 0  # 강제 리셋
    sync_orphan_positions()

    # 🔧 FIX: ThreadPoolExecutor를 루프 밖에서 1회 생성 (매 루프 생성/소멸 오버헤드 제거)
    _candle_executor = ThreadPoolExecutor(max_workers=PARALLEL_WORKERS)

    # 🔧 주기적 헬스체크 텔레그램 알림 (10분마다)
    _last_heartbeat_ts = time.time()
    _HEARTBEAT_INTERVAL = 600  # 10분

    # 🔧 FIX: c1_cache 초기화 (첫 반복에서 box_scan_markets에 NameError 방지)
    c1_cache = {}

    # 🔧 FIX: _scan_cycle_start 초기화 (첫 반복에서 레이턴시 기록 시 NameError 방지)
    _scan_cycle_start = time.time()

    while True:
        try:
            # 🔧 Health check - watchdog용 파일 업데이트
            _t_stage = time.time()
            try:
                with open(os.path.join(os.getcwd(), "health.log"), "w") as hf:  # 🔧 fix: 하드코딩→동적 경로
                    hf.write(f"{time.time()}\n")
            except Exception:
                pass
            _pipeline_record_stage("health_check", (time.time() - _t_stage) * 1000)

            # 💾 상태 영속화 (주기적 저장)
            _t_stage = time.time()
            _save_bot_state()
            _pipeline_record_stage("save_state", (time.time() - _t_stage) * 1000)

            # 📡 섀도우 가상포지션 평가 (만료된 것 → 승률/수익률 누적)
            _t_stage = time.time()
            _shadow_evaluate_positions()
            _pipeline_record_stage("shadow_eval", (time.time() - _t_stage) * 1000)

            # 🔧 실패 메시지 큐 재전송
            _t_stage = time.time()
            tg_flush_failed()
            _pipeline_record_stage("tg_flush", (time.time() - _t_stage) * 1000)

            # 📊 스캔 사이클 레이턴시 기록 (sanity cap 적용: >10분 outlier 제외)
            _scan_cycle_elapsed = (time.time() - _scan_cycle_start) * 1000
            _pipeline_record_scan_latency(_scan_cycle_elapsed)

            # 📊 파이프라인 계측 리포트 (10분마다 텔레그램, 1분마다 콘솔)
            _t_stage = time.time()
            _pipeline_report()
            _pipeline_record_stage("report_full", (time.time() - _t_stage) * 1000)
            _t_stage = time.time()
            _pipeline_mini_report()
            _pipeline_record_stage("report_mini", (time.time() - _t_stage) * 1000)

            # 🔧 30분마다 텔레그램 헬스체크 알림
            if time.time() - _last_heartbeat_ts >= _HEARTBEAT_INTERVAL:
                _last_heartbeat_ts = time.time()
                with _POSITION_LOCK:
                    pos_count = len([p for p in OPEN_POSITIONS.values() if p.get("state") == "open"])
                tg_send(
                    f"💓 봇 생존 확인 | {now_kst_str()}\n"
                    f"📊 보유 {pos_count}개 | 큐 {len(_tg_fail_queue)}건"
                )

            # BTC_guard 제거 — 항상 기본 모드로 실행
            tight_mode = False

            # 🔧 FIX H3: pending 상태 타임아웃 세이프가드 (60초 초과 시 자동 제거)
            # - 진입 중 예외 발생 시 pending 마킹만 남아 해당 코인 영구 차단되는 버그 방지
            # - 🔧 FIX: pending_ts 기반으로 변경 (last_signal_at은 리테스트/동그라미에서 미세팅)
            _PENDING_TIMEOUT_SEC = 60
            with _POSITION_LOCK:
                _stale_pending = []
                for _pm, _pv in list(OPEN_POSITIONS.items()):
                    if _pv.get("state") == "pending" and _pv.get("pre_signal"):
                        # pending_ts 우선, 없으면 last_signal_at 폴백
                        _sig_ts = _pv.get("pending_ts") or last_signal_at.get(_pm, 0)
                        if _sig_ts > 0 and (time.time() - _sig_ts) > _PENDING_TIMEOUT_SEC:
                            _stale_pending.append(_pm)
                for _sp in _stale_pending:
                    OPEN_POSITIONS.pop(_sp, None)
                    print(f"[PENDING_TIMEOUT] {_sp} pending 상태 {_PENDING_TIMEOUT_SEC}초 초과 → 자동 제거")

            # 🔧 유령 포지션 동기화 (업비트 잔고 vs OPEN_POSITIONS)
            sync_orphan_positions()

            # 🔧 FIX: 스파이크 트래커 만료 항목 정리 (메모리 누수 방지)
            _cleanup_spike_tracker()

            # 🎯 리테스트 워치리스트 체크 (장초 2차 기회 진입)
            if RETEST_MODE_ENABLED:
                cleanup_retest_watchlist()  # 타임아웃 정리
                with _RETEST_LOCK:
                    watch_markets = list(_RETEST_WATCHLIST.keys())
                for wm in watch_markets:
                    try:
                        retest_pre = check_retest_entry(wm)
                        if retest_pre:
                            # 🔐 락 획득 (메인/DCB 경로와 동일 패턴)
                            if not _try_acquire_entry_lock(wm):
                                print(f"[RETEST] {wm} already locked → skip")
                                continue
                            with _POSITION_LOCK:
                                if wm in OPEN_POSITIONS:
                                    _release_entry_lock(wm)
                                    continue
                                # 🔧 FIX: MAX_POSITIONS 체크 (리테스트도 포지션 한도 준수)
                                _retest_active = sum(1 for p in OPEN_POSITIONS.values() if p.get("state") == "open")
                                if _retest_active >= MAX_POSITIONS:
                                    _release_entry_lock(wm)
                                    continue
                                OPEN_POSITIONS[wm] = {"state": "pending", "pre_signal": True, "pending_ts": time.time()}

                            # 리테스트 조건 충족 → 진입 (half 강제)
                            retest_pre["entry_mode"] = "half"  # 🔧 이중 보장: 리테스트 = half 강제
                            print(f"[RETEST] {wm} 🎯 리테스트 진입 시작! (half 강제)")
                            c1 = get_minutes_candles(1, wm, 20)
                            dyn_stop, eff_sl_pct, _ = dynamic_stop_loss(retest_pre["price"], c1, signal_type=retest_pre.get("signal_type", "normal"), market=wm)  # 🔧 FIX: signal_type 전달
                            tg_send(f"🎯 <b>리테스트 진입</b> {wm} ⚡HALF\n"
                                    f"• 첫 급등 후 되돌림 → 재돌파 확인\n"
                                    f"• 현재가: {retest_pre['price']:,.0f}원\n"
                                    f"• 모드: half (리스크 제한)")
                            try:
                                open_auto_position(wm, retest_pre, dyn_stop, eff_sl_pct)
                            except Exception as e2:
                                print(f"[RETEST_OPEN_ERR] {wm}: {e2}")
                                with _POSITION_LOCK:
                                    pos = OPEN_POSITIONS.get(wm)
                                    if pos and pos.get("pre_signal"):
                                        OPEN_POSITIONS.pop(wm, None)
                                _release_entry_lock(wm)
                                continue

                            # 🔧 FIX: 모니터 스레드 시작 (기존엔 누락 → 포지션 방치)
                            # 🔧 FIX: open 후 state 확인 (매수 실패 시 모니터 생성 방지)
                            with _POSITION_LOCK:
                                _retest_pos = OPEN_POSITIONS.get(wm)
                                if not _retest_pos or _retest_pos.get("state") != "open":
                                    # 매수 실패 또는 pre_signal 상태 → 정리 후 스킵
                                    if _retest_pos and _retest_pos.get("pre_signal"):
                                        OPEN_POSITIONS.pop(wm, None)
                                    _release_entry_lock(wm)
                                    continue
                                actual_entry = _retest_pos.get("entry_price", retest_pre["price"])
                            retest_pre_copy = dict(retest_pre)
                            def _run_retest_monitor(market, entry, pre_data):
                                try:
                                    monitor_position(market, entry, pre_data, tight_mode=True)
                                    # 🔧 FIX: 모니터 종료 후 포지션 잔존 시 remonitor (방치 방지)
                                    with _POSITION_LOCK:
                                        _pos_after = OPEN_POSITIONS.get(market)
                                    if _pos_after and _pos_after.get("state") == "open":
                                        remonitor_until_close(market, entry, pre_data, tight_mode=True)
                                except Exception as e3:
                                    print(f"[RETEST_MON_ERR] {market}: {e3}")
                                    traceback.print_exc()
                                    # 🔧 FIX: 리테스트 모니터 예외 시 알림 + 잔고 확인 후 정리
                                    try:
                                        actual = get_balance_with_locked(market)
                                        if actual < 0:
                                            tg_send(f"⚠️ {market} 리테스트 모니터 오류 (잔고 조회 실패)\n• 예외: {e3}\n• 포지션 유지")
                                        elif actual <= 1e-12:
                                            tg_send(f"⚠️ {market} 리테스트 모니터 오류 (잔고=0, 이미 청산)\n• 예외: {e3}")
                                            with _POSITION_LOCK:
                                                OPEN_POSITIONS.pop(market, None)
                                        else:
                                            tg_send(f"🚨 {market} 리테스트 모니터 오류 → 청산 시도\n• 예외: {e3}")
                                            close_auto_position(market, f"리테스트모니터예외 | {e3}")
                                    except Exception as _cleanup_err:
                                        tg_send(f"🚨 {market} 리테스트 모니터 오류 (청산 시도 실패)\n• 예외: {e3}")
                                finally:
                                    _release_entry_lock(market)
                                    with _MONITOR_LOCK:
                                        _ACTIVE_MONITORS.pop(market, None)
                            rt_thread = threading.Thread(
                                target=_run_retest_monitor,
                                args=(wm, actual_entry, retest_pre_copy),
                                daemon=True
                            )
                            # 🔧 FIX: 스레드 start 실패 시 락/pending orphan 방지 (일반 진입과 동일 패턴)
                            try:
                                rt_thread.start()
                            except Exception as rt_thread_err:
                                print(f"[RT_THREAD_ERR] {wm} 리테스트 모니터 스레드 생성 실패: {rt_thread_err}")
                                _release_entry_lock(wm)
                                with _POSITION_LOCK:
                                    OPEN_POSITIONS.pop(wm, None)
                                continue
                            with _MONITOR_LOCK:
                                _ACTIVE_MONITORS[wm] = rt_thread
                    except Exception as e:
                        print(f"[RETEST_ERR] {wm}: {e}")
                        # 🔧 FIX: 리테스트 진입 오류 시 알림 발송 (무알림 포지션 유실 방지)
                        tg_send(f"🚨 {wm} 리테스트 진입 오류\n• 예외: {e}\n• 포지션 정리됨")
                        with _POSITION_LOCK:
                            OPEN_POSITIONS.pop(wm, None)
                        _release_entry_lock(wm)

            # ⭕ 동그라미 워치리스트 체크 (눌림→리클레임→재돌파 진입)
            if CIRCLE_ENTRY_ENABLED:
                circle_cleanup()
                with _CIRCLE_LOCK:
                    circle_markets = list(_CIRCLE_WATCHLIST.keys())
                for cm in circle_markets:
                    try:
                        circle_pre = circle_check_entry(cm)
                        if not circle_pre:
                            continue

                        # ⚠️ OPEN_POSITIONS 차단 시 watchlist 유지 (다음 사이클 재시도)
                        # → 리테스트가 죽었던 원인: ready에서 pop 해버려서 재시도 불가
                        with _POSITION_LOCK:
                            if cm in OPEN_POSITIONS:
                                # 이미 포지션 보유 중 → watchlist 유지, 다음 사이클에 재확인
                                continue
                            # 🔧 FIX: MAX_POSITIONS 체크 (동그라미도 포지션 한도 준수)
                            _circle_active = sum(1 for p in OPEN_POSITIONS.values() if p.get("state") == "open")
                            if _circle_active >= MAX_POSITIONS:
                                continue

                        # 락 획득
                        if not _try_acquire_entry_lock(cm):
                            print(f"[CIRCLE] {cm} already locked → 다음 사이클 재시도")
                            continue

                        with _POSITION_LOCK:
                            if cm in OPEN_POSITIONS:
                                _release_entry_lock(cm)
                                continue
                            OPEN_POSITIONS[cm] = {"state": "pending", "pre_signal": True, "pending_ts": time.time()}

                        # 🔧 FIX: ready 재시도 쿨다운 (텔레그램 스팸 방지)
                        # NOTE: _CIRCLE_LOCK 안에서 읽기만, 정리는 밖에서 (락 네스팅 방지)
                        _circle_in_cooldown = False
                        with _CIRCLE_LOCK:
                            _cw = _CIRCLE_WATCHLIST.get(cm)
                            if _cw:
                                _last_try = _cw.get("last_try_ts", 0)
                                if time.time() - _last_try < CIRCLE_RETRY_COOLDOWN_SEC:
                                    _circle_in_cooldown = True
                                else:
                                    _cw["last_try_ts"] = time.time()
                        if _circle_in_cooldown:
                            _release_entry_lock(cm)
                            with _POSITION_LOCK:
                                _pp = OPEN_POSITIONS.get(cm)
                                if _pp and _pp.get("pre_signal"):
                                    OPEN_POSITIONS.pop(cm, None)
                            continue

                        # 🔧 FIX: setdefault — circle_check_entry에서 이미 설정된 경우 덮어쓰지 않음
                        circle_pre.setdefault("entry_mode", CIRCLE_ENTRY_MODE)
                        c1_circle = get_minutes_candles(1, cm, 20)
                        # 🔧 FIX: 동그라미 signal_type 전달 (circle SL 완화 적용)
                        dyn_stop_c, eff_sl_pct_c, _ = dynamic_stop_loss(circle_pre["price"], c1_circle, signal_type=circle_pre.get("signal_type"), market=cm)

                        try:
                            open_auto_position(cm, circle_pre, dyn_stop_c, eff_sl_pct_c)
                        except Exception as ce:
                            print(f"[CIRCLE_OPEN_ERR] {cm}: {ce}")
                            with _POSITION_LOCK:
                                pos = OPEN_POSITIONS.get(cm)
                                if pos and pos.get("pre_signal"):
                                    OPEN_POSITIONS.pop(cm, None)
                            _release_entry_lock(cm)
                            continue

                        # 🔧 FIX: 진입 성공 확인 (예외 없음 ≠ 성공)
                        # open_auto_position이 예외 없이 return해도 실제 포지션이 안 열렸을 수 있음
                        # (잔고부족, MAX_POSITIONS, API키 미설정 등)
                        with _POSITION_LOCK:
                            _circle_pos = OPEN_POSITIONS.get(cm, {})
                            _circle_opened = (_circle_pos.get("state") == "open")

                        if _circle_opened:
                            # ✅ 진입 성공 → 워치리스트에서 확정 제거 + 텔레그램 알림
                            circle_confirm_entry(cm)
                            _c_candles = circle_pre.get('circle_candles', '?')
                            _c_ign_high = circle_pre.get('circle_ign_high', 0)
                            _c_pb_pct = circle_pre.get('circle_pullback_pct', 0)
                            _c_reclaim = circle_pre.get('circle_reclaim_price', 0)
                            tg_send(
                                f"⭕ <b>동그라미 진입 성공</b> {cm} ⚡{CIRCLE_ENTRY_MODE.upper()}\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"🔥 점화고점: {fmt6(_c_ign_high)}원\n"
                                f"📉 눌림: -{_c_pb_pct*100:.2f}% ({_c_candles}봉)\n"
                                f"📈 리클레임: {fmt6(_c_reclaim)}원\n"
                                f"🚀 재돌파: {fmt6(circle_pre['price'])}원\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"🧯 손절: {fmt6(dyn_stop_c)} (SL {eff_sl_pct_c*100:.2f}%)\n"
                                f"📊 경로: 점화→{_c_candles}봉눌림→리클레임→재돌파\n"
                                f"💰 모드: {CIRCLE_ENTRY_MODE} (리스크 제한)\n"
                                f"{link_for(cm)}"
                            )
                        else:
                            # ⚠️ 진입 실패 (예외 없이 return) → 워치리스트 유지, 쿨다운 적용
                            print(f"[CIRCLE] {cm} open_auto_position 완료했으나 state!=open → 워치리스트 유지 (쿨다운 {CIRCLE_RETRY_COOLDOWN_SEC}초)")
                            with _POSITION_LOCK:
                                _pp = OPEN_POSITIONS.get(cm)
                                if _pp and _pp.get("pre_signal"):
                                    OPEN_POSITIONS.pop(cm, None)
                            _release_entry_lock(cm)
                            continue

                        # 모니터 스레드 시작
                        with _POSITION_LOCK:
                            actual_entry_c = OPEN_POSITIONS.get(cm, {}).get("entry_price", circle_pre["price"])
                        circle_pre_copy = dict(circle_pre)
                        def _run_circle_monitor(market, entry, pre_data):
                            try:
                                monitor_position(market, entry, pre_data, tight_mode=True)
                                with _POSITION_LOCK:
                                    _pos_after = OPEN_POSITIONS.get(market)
                                if _pos_after and _pos_after.get("state") == "open":
                                    remonitor_until_close(market, entry, pre_data, tight_mode=True)
                            except Exception as ce2:
                                print(f"[CIRCLE_MON_ERR] {market}: {ce2}")
                                traceback.print_exc()
                                try:
                                    actual = get_balance_with_locked(market)
                                    if actual < 0:
                                        tg_send(f"⚠️ {market} 동그라미 모니터 오류 (잔고 조회 실패)\n• 예외: {ce2}")
                                    elif actual <= 1e-12:
                                        tg_send(f"⚠️ {market} 동그라미 모니터 오류 (잔고=0)\n• 예외: {ce2}")
                                        with _POSITION_LOCK:
                                            OPEN_POSITIONS.pop(market, None)
                                    else:
                                        tg_send(f"🚨 {market} 동그라미 모니터 오류 → 청산 시도\n• 예외: {ce2}")
                                        close_auto_position(market, f"동그라미모니터예외 | {ce2}")
                                except Exception as _cleanup_err:
                                    tg_send(f"🚨 {market} 동그라미 모니터 오류 (청산 실패)\n• 예외: {ce2}")
                            finally:
                                _release_entry_lock(market)
                                with _MONITOR_LOCK:
                                    _ACTIVE_MONITORS.pop(market, None)
                        ct_thread = threading.Thread(
                            target=_run_circle_monitor,
                            args=(cm, actual_entry_c, circle_pre_copy),
                            daemon=True
                        )
                        try:
                            ct_thread.start()
                        except Exception as ct_thread_err:
                            print(f"[CIRCLE_THREAD_ERR] {cm} 동그라미 모니터 스레드 생성 실패: {ct_thread_err}")
                            _release_entry_lock(cm)
                            with _POSITION_LOCK:
                                OPEN_POSITIONS.pop(cm, None)
                            continue
                        with _MONITOR_LOCK:
                            _ACTIVE_MONITORS[cm] = ct_thread
                    except Exception as ce:
                        print(f"[CIRCLE_ERR] {cm}: {ce}")
                        tg_send(f"🚨 {cm} 동그라미 진입 오류\n• 예외: {ce}")
                        with _POSITION_LOCK:
                            if OPEN_POSITIONS.get(cm, {}).get("pre_signal"):
                                OPEN_POSITIONS.pop(cm, None)
                        _release_entry_lock(cm)

            # 📦 박스권 매매: 스캔 + 진입 체크
            # 🔧 FIX: c1_cache 비어있으면 스킵 (첫 반복에서 빈 캐시로 스캔 방지)
            if BOX_ENABLED and c1_cache:
                try:
                    box_cleanup()
                    box_scan_markets(c1_cache)

                    with _BOX_LOCK:
                        box_markets = [bm for bm, bw in _BOX_WATCHLIST.items()
                                       if bw.get("state") == "watching"]
                    for bm in box_markets:
                        try:
                            box_pre = box_check_entry(bm)
                            if not box_pre:
                                continue

                            with _POSITION_LOCK:
                                if bm in OPEN_POSITIONS:
                                    continue

                            if not _try_acquire_entry_lock(bm):
                                continue

                            with _POSITION_LOCK:
                                if bm in OPEN_POSITIONS:
                                    _release_entry_lock(bm)
                                    continue
                                active_count = sum(1 for p in OPEN_POSITIONS.values() if p.get("state") == "open")
                                if active_count >= MAX_POSITIONS:
                                    _release_entry_lock(bm)
                                    continue
                                OPEN_POSITIONS[bm] = {"state": "pending", "pre_signal": True, "pending_ts": time.time()}

                            # 박스 전용 SL/TP
                            box_stop = box_pre["box_stop"]
                            box_sl_pct = box_pre["box_sl_pct"]

                            try:
                                open_auto_position(bm, box_pre, box_stop, box_sl_pct)
                            except Exception as be:
                                print(f"[BOX_OPEN_ERR] {bm}: {be}")
                                with _POSITION_LOCK:
                                    pos = OPEN_POSITIONS.get(bm)
                                    if pos and pos.get("pre_signal"):
                                        OPEN_POSITIONS.pop(bm, None)
                                _release_entry_lock(bm)
                                continue

                            with _POSITION_LOCK:
                                _box_pos = OPEN_POSITIONS.get(bm, {})
                                _box_opened = (_box_pos.get("state") == "open")

                            if _box_opened:
                                box_confirm_entry(bm)
                                # 🔧 FIX: 박스 진입 즉시 유령포지션 감지 방지
                                # - 레이스컨디션: 박스 모니터 시작 전 sync_orphan이 잔고 발견 → 유령 오탐
                                # - _ORPHAN_HANDLED에 등록하여 ghost detection 원천 차단
                                # - _RECENT_BUY_TS도 갱신 (box_monitor_position 안에서 600초 보호)
                                with _ORPHAN_LOCK:
                                    _ORPHAN_HANDLED.add(bm)
                                with _RECENT_BUY_LOCK:
                                    _RECENT_BUY_TS[bm] = time.time()
                                with _POSITION_LOCK:
                                    actual_entry_b = OPEN_POSITIONS.get(bm, {}).get("entry_price", box_pre["price"])
                                    actual_vol_b = OPEN_POSITIONS.get(bm, {}).get("volume", 0)

                                _box_info = {
                                    "box_high": box_pre["box_high"],
                                    "box_low": box_pre["box_low"],
                                    "box_tp": box_pre["box_tp"],
                                    "box_stop": box_pre["box_stop"],
                                    "range_pct": box_pre.get("box_range_pct", 0),
                                }

                                # 🔧 일반 매매와 동일한 매수 알림 포맷
                                _box_signal_price = box_pre.get("price", 0)
                                _box_slip_pct = (actual_entry_b / _box_signal_price - 1.0) * 100 if _box_signal_price > 0 else 0
                                _box_krw_used = actual_entry_b * actual_vol_b
                                _box_buy_r = box_pre.get("buy_ratio", 0)
                                _box_spread = box_pre.get("spread", 0)
                                _box_sl_display = fmt6(_box_info['box_stop'])

                                # 🔧 FIX: 일반 매수와 동일한 헤더 형식 (내용은 박스 전용 유지)
                                tg_send(
                                    f"⚡ <b>[중간진입] 자동매수</b> {bm}\n"
                                    f"• 신호: 📦박스하단 | 박스 {fmt6(_box_info['box_low'])}~{fmt6(_box_info['box_high'])} ({_box_info['range_pct']*100:.1f}%)\n"
                                    f"• 지표: 매수{_box_buy_r:.0%} 스프레드{_box_spread:.2f}%\n"
                                    f"• 신호가: {fmt6(_box_signal_price)}원 → 체결가: {fmt6(actual_entry_b)}원 ({_box_slip_pct:+.2f}%)\n"
                                    f"• 주문: {_box_krw_used:,.0f}원 | 수량: {actual_vol_b:.6f}\n"
                                    f"• 손절: {_box_sl_display}원 (SL {box_sl_pct*100:.2f}%) | 목표: {fmt6(_box_info['box_tp'])}원\n"
                                    f"{link_for(bm)}"
                                )

                                # 박스 전용 모니터 스레드
                                def _run_box_monitor(market, entry, vol, binfo):
                                    try:
                                        box_monitor_position(market, entry, vol, binfo)
                                    except Exception as bme:
                                        print(f"[BOX_MON_ERR] {market}: {bme}")
                                        traceback.print_exc()
                                        try:
                                            close_auto_position(market, f"박스모니터예외 | {bme}")
                                        except Exception:
                                            pass
                                    finally:
                                        _release_entry_lock(market)
                                        with _MONITOR_LOCK:
                                            _ACTIVE_MONITORS.pop(market, None)

                                bt = threading.Thread(
                                    target=_run_box_monitor,
                                    args=(bm, actual_entry_b, actual_vol_b, _box_info),
                                    daemon=True
                                )
                                try:
                                    bt.start()
                                    with _MONITOR_LOCK:
                                        _ACTIVE_MONITORS[bm] = bt
                                except Exception as _bt_err:
                                    print(f"[BOX_THREAD_ERR] {bm} 스레드 시작 실패: {_bt_err}")
                                    with _POSITION_LOCK:
                                        OPEN_POSITIONS.pop(bm, None)
                                    _release_entry_lock(bm)
                                    # 🔧 FIX: watchlist도 정리 (무한 재시도 방지)
                                    with _BOX_LOCK:
                                        _BOX_WATCHLIST.pop(bm, None)
                            else:
                                with _POSITION_LOCK:
                                    _pp = OPEN_POSITIONS.get(bm)
                                    if _pp and _pp.get("pre_signal"):
                                        OPEN_POSITIONS.pop(bm, None)
                                _release_entry_lock(bm)
                                with _BOX_LOCK:
                                    _BOX_WATCHLIST.pop(bm, None)

                        except Exception as be:
                            print(f"[BOX_ERR] {bm}: {be}")
                            _release_entry_lock(bm)
                            # 🔧 FIX: 예외 시 watchlist 정리 (무한 재시도 방지)
                            with _BOX_LOCK:
                                _BOX_WATCHLIST.pop(bm, None)
                except Exception as box_scan_err:
                    print(f"[BOX_SCAN_ERR] {box_scan_err}")

            # 🔧 학습은 update_trade_result에서 건수 기반으로 자동 트리거됨
            # (10건마다 또는 연속 3패 시 즉시 학습)
            # 매도 파라미터는 고정 (매수만 학습)

            for k in list(CUT_COUNTER.keys()):
                CUT_COUNTER[k] = 0

            cleanup_expired(recent_alerts, ALERT_TTL)
            # 🔧 FIX H2: 동적 쿨다운 최대값(COOLDOWN*2=960초) 사용
            # 기존: 고정 COOLDOWN(480) → 시간대별 180~960초와 불일치 → 조기 삭제
            cleanup_expired(last_signal_at, COOLDOWN * 2 + 60)  # 🔧 FIX: 여유 60초 추가 (쿨다운 경계 jitter로 인한 조기삭제 방지)
            # 🔧 FIX: last_price_at_alert / last_reason도 정리 (메모리 누수 방지)
            # — 타임스탬프가 아니라 가격/문자열이므로 last_signal_at 키 기준으로 정리
            _valid_signal_keys = set(last_signal_at.keys())
            for _stale_k in list(last_price_at_alert.keys()):
                if _stale_k not in _valid_signal_keys:
                    last_price_at_alert.pop(_stale_k, None)
            for _stale_k in list(last_reason.keys()):
                if _stale_k not in _valid_signal_keys:
                    last_reason.pop(_stale_k, None)
            _TICKS_CACHE.purge_older_than(max_age_sec=2.5)
            _C5_CACHE.purge_older_than(max_age_sec=2.5)
            _C60_CACHE.purge_older_than(max_age_sec=300)  # v18f: TTL과 동일
            _C15_CACHE.purge_older_than(max_age_sec=60)   # v18f Phase B: TTL과 동일

            mkts_all = get_top_krw_by_24h(TOP_N)
            if not mkts_all:
                aligned_sleep(SCAN_INTERVAL)
                continue

            # 🔧 잔고 부족 시 스캔 스킵 (주문금액 부족 로그 폭주 방지)
            # 리스크계산+최소주문+임팩트캡 등으로 실제 필요 금액은 6000원보다 훨씬 높음
            _MIN_SCAN_KRW = 9000  # 최소주문 6000 × 1.5 = 9000원
            try:
                _pre_accounts = get_account_info()
                _pre_krw = get_available_krw(_pre_accounts) if _pre_accounts else 0
                if _pre_krw < _MIN_SCAN_KRW:
                    _has_positions = bool(OPEN_POSITIONS)
                    if not _has_positions:
                        aligned_sleep(SCAN_INTERVAL)
                        continue
            except Exception:
                pass

            # 🔧 FIX: _cursor 읽기/쓰기를 락으로 보호 (레이스 컨디션 방지)
            with _cursor_lock:
                start = _cursor
                end = _cursor + SHARD_SIZE
                _cursor = (end) % len(mkts_all)
            shard = mkts_all[start:end]
            if len(shard) < SHARD_SIZE:
                shard += mkts_all[:(SHARD_SIZE - len(shard))]
            # 🔧 FIX: shard 중복 제거 (wrap-around 시 중복 방지)
            shard = list(dict.fromkeys(shard))

            _scan_cycle_start = time.time()
            _t_fetch = _scan_cycle_start  # scan_fetch 단계 시작 (네트워크 I/O)

            obc = fetch_orderbook_cache(shard)

            _pipeline_inc("scan_markets", len(shard))

            c1_cache = {}
            # 🔧 FIX: 20→30 캔들 (BOX_LOOKBACK=30 요구 충족 — 돌파 감지는 20개만 슬라이싱해서 사용)
            futures = {
                _candle_executor.submit(get_minutes_candles, 1, m, 30): m
                for m in shard
            }
            for f in as_completed(futures):
                m = futures[f]
                try:
                    c1_cache[m] = f.result() or []
                except Exception:
                    c1_cache[m] = []

            _pipeline_inc("c1_ok", sum(1 for v in c1_cache.values() if v))

            # scan_fetch 단계 종료 (orderbook + candles 네트워크 fetch)
            _pipeline_record_stage("scan_fetch", (time.time() - _t_fetch) * 1000)
            _t_detect = time.time()  # scan_detect 단계 시작 (CPU 판정 루프)

            # 🔧 FIX: BTC 캔들 캐시 (shard 루프 밖에서 1회만 조회 → API 절약)
            _btc_c1_cache = None
            _btc_c5_cache = None

            found = 0
            for m in shard:
              _lock_held = False  # 🔧 FIX: 락 획득 여부 추적 (미획득 상태에서 해제 방지)
              try:  # 🔧 심볼별 예외 격리 (한 심볼 에러가 전체 스캔 중단 방지)
                c1 = c1_cache.get(m, [])
                if not c1: continue

                _pipeline_inc("detect_called")
                _pipeline_record_market_scan(m)
                _t_dl = time.time()
                pre = detect_leader_stock(m, obc, c1, tight_mode=tight_mode)
                _add_cycle_detect_leader_ms((time.time() - _t_dl) * 1000)
                if not pre:
                    continue

                # ⭕ 동그라미 워치리스트 등록 (점화 감지 시)
                # 즉시 진입과 별개로, 눌림→리클레임→재돌파 패턴 감시 시작
                if CIRCLE_ENTRY_ENABLED and pre.get("ign_ok"):
                    try:
                        circle_register(m, pre, c1)
                    except Exception as _cr_err:
                        print(f"[CIRCLE_REG_ERR] {m}: {_cr_err}")

                # === 🔧 WF데이터: 진입 모드는 strategy_v4에서 결정 ===
                # A그룹(거래량3배) → confirm, B그룹(눌림반전/EMA정배열) → confirm (GATE 필수)
                _v4_group = pre.get("v4_logic_group", "A")
                _v4_filters = pre.get("v4_filters_hit", [])

                # v4 진입모드 그대로 유지 (detect_leader_stock에서 이미 설정)
                print(f"[V4_ENTRY] {m} {pre.get('signal_tag', '?')} 그룹={_v4_group} "
                      f"모드={pre.get('entry_mode', 'confirm')} 필터={_v4_filters}")

                # 🔧 FIX: postcheck 전 중복 체크 + 즉시 마킹 (6초 동안 다른 스캔 차단)
                with _POSITION_LOCK:
                    if m in OPEN_POSITIONS:
                        _pipeline_inc("position_block")
                        continue
                    # 🔧 FIX: recent_alerts도 락 안에서 체크 (10초 이내만 차단 - postcheck 동안만)
                    if m in recent_alerts and time.time() - recent_alerts[m] < 10:
                        _pipeline_inc("position_block")
                        continue
                    # 🔧 FIX: postcheck 전에 미리 마킹 (다른 스캔 차단)
                    recent_alerts[m] = time.time()

                # === 6초 포스트체크 ===
                ok_post, post_reason = postcheck_6s(m, pre)
                if not ok_post:
                    cut("POSTCHECK_DROP", f"{m} postcheck fail: {post_reason}")
                    _pipeline_inc("postcheck_block")
                    _shadow_log_write(now_kst_str(), m, pre.get("signal_tag", "?"), 1,
                                      f"postcheck:{post_reason}", 0)
                    # 🔧 FIX: postcheck 실패 시 recent_alerts 제거 (다음 스캔에서 재시도 가능)
                    with _POSITION_LOCK:
                        recent_alerts.pop(m, None)
                    continue

                # 🔧 postcheck 통과 후 vwap_gap 추격 체크 (추격매수 제거)
                _post_vwap_gap = pre.get("vwap_gap", 0)
                if _post_vwap_gap > 1.0:
                    pre["entry_mode"] = "half"
                    print(f"[VWAP_GAP] {m} vwap_gap {_post_vwap_gap:.1f}%>1.0% → half 강제 (추격 제한)")

                # 🔧 승률개선: 급등 허용 시 half 강제 (리스크 제한)
                if pre.get("_surge_probe"):
                    pre["entry_mode"] = "half"
                # 🔧 FIX: postcheck 후 재확인 제거 (이미 위에서 마킹됨)

                # 🔧 3929건시뮬: 야간 half 0-7시만 (0-9시는 9시간 → 과도)
                # 7-8시: 3847건 데이터에서 승률차이 미미 → half 불필요
                _night_h = now_kst().hour
                if 0 <= _night_h < 7 and pre.get("entry_mode") == "confirm":
                    pre["entry_mode"] = "half"
                    print(f"[NIGHT] {m} 야간({_night_h}시) → half 강제 (유동성 부족 완화)")

                # 🔧 전략별 연패 게이트 — 해당 전략만 차단 (C 연패가 GT 차단하지 않음)
                _cur_strat_group = pre.get("v4_logic_group", "?")
                _sg_key = _strat_group_from_signal(pre.get("signal_tag", ""))
                with _STREAK_LOCK:
                    _suspend_ts = _STRAT_SUSPEND_UNTIL.get(_sg_key, 0)
                    _max_mode = _STRAT_MAX_MODE.get(_sg_key)
                if _suspend_ts > time.time():
                    _remain = int(_suspend_ts - time.time())
                    cut("LOSE_SUSPEND", f"{m} [{_sg_key}] 연패 진입중지 (잔여 {_remain}초)")
                    _pipeline_inc("suspend_block")
                    continue
                if _max_mode == "half" and pre.get("entry_mode") == "confirm":
                    pre["entry_mode"] = "half"
                    print(f"[LOSE_GATE] {m} [{_sg_key}] 연패 모드제한 → half 강제")
                    print(f"[LOSE_GATE] {m} 연패 모드제한 → half 강제 (probe 폐지)")

                reason = "ign" if pre.get("ign_ok") else (
                    "early" if pre.get("early_ok") else
                    ("mega" if pre.get("mega_ok") else "normal"))
                if not cooldown_ok(m, pre['price'], reason=reason):
                    _pipeline_inc("cooldown_block")
                    _shadow_log_write(now_kst_str(), m, pre.get("signal_tag", "?"), 1,
                                      "cooldown", 0)
                    # 🔧 FIX: cooldown 실패 시 recent_alerts 정리 (10초 재탐지 블록 방지)
                    with _POSITION_LOCK:
                        recent_alerts.pop(m, None)
                    continue

                # 🔧 FIX: 초입 신호 발송 전 중복 진입 차단 (race condition 방지)
                # 🔐 파일락 획득 시도 (프로세스 간 공유)
                if not _try_acquire_entry_lock(m):
                    print(f"[LOCK] {m} already locked → skip")
                    _pipeline_inc("lock_block")
                    continue
                _lock_held = True

                # 🔧 FIX: 파일락 획득 후 OPEN_POSITIONS만 재확인 (recent_alerts는 이미 위에서 마킹됨)
                with _POSITION_LOCK:
                    if m in OPEN_POSITIONS:
                        print(f"[SCAN] {m} 이미 포지션/pending 존재 → 스킵")
                        _release_entry_lock(m)
                        continue
                    # 미리 pending 마킹 (다른 스레드 차단)
                    OPEN_POSITIONS[m] = {"state": "pending", "pre_signal": True, "pending_ts": time.time()}
                    # recent_alerts는 postcheck 전에 이미 설정됨 (line 5684)
                    last_signal_at[m] = time.time()
                    last_price_at_alert[m] = pre['price']
                    last_reason[m] = reason

                # 동적 손절가
                dyn_stop, eff_sl_pct, _ = dynamic_stop_loss(pre['price'], c1, market=m)

                # 임밸런스 표시
                imb_str = f"임밸 {pre.get('imbalance', 0):.2f}"
                pocket_mark = "🎯" if pre.get("is_precision_pocket") else ""

                # 🔥 경로 표시: signal_tag + 핵심 조건 요약
                filter_type = pre.get("filter_type", "stage1_gate")
                _sig_tag = pre.get("signal_tag", "기본")
                _v4_filters_str = " ".join(pre.get("v4_filters_hit", []))
                _v4_group_str = pre.get("v4_logic_group", "?")
                if filter_type == "prebreak":
                    path_str = "🚀선행진입"
                else:
                    path_str = f"{_sig_tag} [{_v4_group_str}]"
                # 전략별 한 줄 설명
                _strat_desc_map = {
                    "GT": "5분봉RSI 73이상 과매수 돌입 + 1분봉양봉 + 거래량 폭증 확인 → 강한 상승 모멘텀 추격 진입 (트레일링 없이 180초 시간청산)",
                    "C": "15분봉이 음봉→양봉 전환 + 종가가 전봉시가 회복(0.1%이상) + 1분봉 양봉 + 15분거래량 증가 → 단기 반전 초입 포착 (빠른 익절 전략)",
                }
                _strat_desc = _strat_desc_map.get(_v4_group_str, _v4_filters_str)

                # 🔥 새 지표 계산: 체결강도, 틱당금액, 가속도
                ticks_for_metrics = pre.get("ticks", [])
                t15_for_avg = micro_tape_stats_from_ticks(ticks_for_metrics, 15)
                cons_buys = calc_consecutive_buys(ticks_for_metrics, 15)
                avg_krw = calc_avg_krw_per_tick(t15_for_avg)
                flow_accel = calc_flow_acceleration(ticks_for_metrics)

                # 가속도 이모지
                accel_emoji = "🚀" if flow_accel >= 1.5 else ("📉" if flow_accel <= 0.7 else "➡️")

                # 🔥 GATE 핵심 지표
                overheat = flow_accel * float(pre.get("volume_surge", 1.0))
                fresh_age = 0.0
                if ticks_for_metrics:
                    now_ms = int(time.time() * 1000)
                    # 🔧 FIX: tick_ts_ms 헬퍼로 통일
                    last_tick_ts = max(tick_ts_ms(t) for t in ticks_for_metrics)
                    if last_tick_ts == 0:
                        last_tick_ts = now_ms
                    fresh_age = (now_ms - last_tick_ts) / 1000.0

                # 🚀 초단기 미세필터 지표 계산
                ia_stats = inter_arrival_stats(ticks_for_metrics, 30) if ticks_for_metrics else {"cv": 0.0}
                cv_val = ia_stats.get("cv")
                if cv_val is None:
                    cv_val = 0.0  # 🔧 FIX: inter_arrival_stats가 cv=None 반환 시 TypeError 방지
                pstd_val = price_band_std(ticks_for_metrics, sec=10) if ticks_for_metrics else 0.0
                if pstd_val is None:
                    pstd_val = 0.0  # 🔧 FIX: price_band_std가 None 반환 시 TypeError 방지
                prebreak_band_val = dynamic_prebreak_band(ticks_for_metrics) if ticks_for_metrics else PREBREAK_HIGH_PCT
                is_prebreak = pre.get("filter_type") == "prebreak"
                # 베스트호가 깊이
                try:
                    u0 = pre.get("ob", {}).get("raw", {}).get("orderbook_units", [])[0]
                    best_ask_krw = float(u0["ask_price"]) * float(u0["ask_size"])
                except Exception:
                    best_ask_krw = 0.0

                # CV 이모지 (봇/사람 판단)
                cv_emoji = "🤖" if cv_val <= 0.45 else ("⚔️" if cv_val >= 1.2 else "")

                txt = (
                    f"⚡ <b>초입 신호</b> {m} <code>#{reason}</code>{pocket_mark}\n"
                    f"💵 현재가 {fmt6(pre['price'])}원\n"
                    f"📊 등락 {round(pre.get('change', 0) * 100, 2)}% | 거래증가 {round(pre.get('volume_surge', 0), 2)}배 | 회전 {round(pre.get('turn_pct', 0), 2)}%\n"
                    f"🔸매수 {round(pre.get('buy_ratio', 0) * 100, 1)}% | 틱 {pre['tape']['n']} | 스프레드 {round(pre.get('spread', 0), 2)}% | {imb_str}\n"
                    f"🔥 연속매수 {cons_buys}회 | 틱당 {avg_krw/1000:.0f}K | 가속 {flow_accel:.1f}x {accel_emoji}\n"
                    f"🌡️ 과열 {overheat:.1f} | 틱나이 {fresh_age:.1f}초\n"
                    f"📈 CV {cv_val:.2f}{cv_emoji} | pstd {pstd_val*100:.3f}% | 호가 {best_ask_krw/1000:.0f}K\n"
                    f"🧯 손절가: {fmt6(dyn_stop)} (동적SL {eff_sl_pct*100:.2f}%)\n"
                    f"🔍 {path_str} | {_v4_filters_str}\n"
                    f"{link_for(m)}")

                sent = tg_send(txt, retry=2)

                if sent:
                    found += 1
                    # --- 로그 CSV 기록 (기존 그대로) ---
                    try:
                        c5 = get_minutes_candles(5, m, 2) or []
                        c15 = get_minutes_candles(15, m, 2) or []
                        chg_1m = (c1[-1]["trade_price"] /
                                  max(c1[-2]["trade_price"], 1) -
                                  1) if len(c1) >= 2 else 0.0
                        chg_5m = (c5[-1]["trade_price"] /
                                  max(c5[-2]["trade_price"], 1) -
                                  1) if len(c5) >= 2 else ""
                        chg_15m = (c15[-1]["trade_price"] /
                                   max(c15[-2]["trade_price"], 1) -
                                   1) if len(c15) >= 2 else ""

                        # 🔧 FIX: BTC 캔들은 shard 루프당 1회만 조회 (lazy 캐시)
                        if _btc_c1_cache is None:
                            _btc_c1_cache = get_minutes_candles(1, "KRW-BTC", 2) or []
                        if _btc_c5_cache is None:
                            _btc_c5_cache = get_minutes_candles(5, "KRW-BTC", 2) or []
                        cbtc1 = _btc_c1_cache
                        btc1m = (cbtc1[-1]["trade_price"] /
                                 max(cbtc1[-2]["trade_price"], 1) -
                                 1) if len(cbtc1) >= 2 else 0.0
                        cbtc5 = _btc_c5_cache
                        btc5m = (cbtc5[-1]["trade_price"] /
                                 max(cbtc5[-2]["trade_price"], 1) -
                                 1) if len(cbtc5) >= 2 else 0.0

                        t15_now = micro_tape_stats_from_ticks(pre["ticks"], 15)
                        ob = pre.get("ob") or {}  # 🔧 FIX: None 방어 (orderbook 실패 시 TypeError 방지)
                        flags = {
                            "chg_1m":
                            chg_1m,
                            "chg_5m":
                            chg_5m,
                            "chg_15m":
                            chg_15m,
                            "zscore":
                            zscore_krw_1m(c1, 30),
                            "vwap_gap": (c1[-1]["trade_price"] /
                                         max(vwap_from_candles_1m(c1, 20), 1) -
                                         1) if len(c1) >= 1 else 0.0,
                            "turn":
                            round((t15_now.get("krw", 0) / max(ob.get("depth_krw", 0), 1)) *
                                  100, 2),
                            "two_green_break":
                            pre.get("two_green_break", False),
                            "ignition_ok":
                            pre.get("ign_ok", False),
                            "early_ok":
                            pre.get("early_ok", False),
                            "uptick_ok":
                            True
                        }
                        row = snapshot_row(m, pre["price"], pre, c1,
                                           ob, t15_now, btc1m, btc5m, flags)
                        append_csv(row)
                    except Exception as e:
                        print("[LOG_ERR]", e)

                    # --- 🔥 자동매수 진입 ---
                    # 🎯 리테스트 모드: 장초 첫 양봉은 워치리스트에만 등록
                    if RETEST_MODE_ENABLED and is_morning_session():
                        # 급등률 체크 (신호가 대비 현재가)
                        cur_price = pre.get("price", 0)
                        # 🔧 FIX: entry_price가 없으면 c1 시가 사용 (cur_price 폴백 시 gain=0 되어 무의미)
                        entry_price_base = pre.get("entry_price") or (c1[-2]["trade_price"] if len(c1) >= 2 else cur_price)
                        gain_pct = (cur_price / entry_price_base - 1.0) if entry_price_base > 0 else 0

                        if gain_pct >= RETEST_PEAK_MIN_GAIN:
                            # 첫 급등 → 워치리스트 등록 시도 (내부에서 품질 검증)
                            add_to_retest_watchlist(m, cur_price, pre)
                            print(f"[RETEST] {m} 장초 첫 급등 +{gain_pct*100:.2f}% | ign={pre.get('ignition_score',0)} → 워치리스트 검토 완료")
                            # 🔧 FIX: pending 마킹 정리 (안 하면 ghost 포지션으로 남아 진입 차단)
                            # 🔧 FIX: signal dict도 _POSITION_LOCK 안에서 정리 (일관성)
                            with _POSITION_LOCK:
                                OPEN_POSITIONS.pop(m, None)
                                # 🔧 FIX: 워치리스트만 등록하고 진입 안 한 경우 cooldown 되돌리기
                                # (진입 안 했는데 cooldown 걸리면 리테스트/재탐지 기회 손실)
                                last_signal_at.pop(m, None)
                                last_price_at_alert.pop(m, None)
                                last_reason.pop(m, None)
                            # recent_alerts는 유지 (10초 이내 동일 종목 중복 신호 방지)
                            _release_entry_lock(m)
                            _lock_held = False
                            continue  # 바로 진입하지 않고 다음 종목으로

                    # 🔧 FIX: 스캔 루프 락을 유지한 채 매수 진행 (gap 제거 → 중복진입 방지)
                    # open_auto_position이 reentrant=True로 재진입, 모니터 finally에서 최종 해제
                    _pipeline_inc("send_attempt")
                    _shadow_log_write(now_kst_str(), m, pre.get("signal_tag", "?"), 1,
                                      "", 1, f"ENTRY_ATTEMPT|mode={pre.get('entry_mode')}")
                    try:
                        open_auto_position(m, pre, dyn_stop, eff_sl_pct)
                    except Exception as e:
                        print("[AUTO_OPEN_ERR]", e)
                        # 🔧 FIX: 자동매수 실패 시 pre_signal pending 정리
                        with _POSITION_LOCK:
                            pos = OPEN_POSITIONS.get(m)
                            if pos and pos.get("pre_signal"):
                                OPEN_POSITIONS.pop(m, None)
                        # 🔧 FIX: 모니터 미생성이므로 락 직접 해제 (안 풀면 90초간 진입 차단)
                        _release_entry_lock(m)
                        _lock_held = False
                        continue  # 🔧 FIX: 매수 실패 시 모니터 생성 방지 (fall-through → 유령 모니터 방지)

                    # --- 포지션 모니터링 (손절/청산 시 자동청산까지 이어짐) ---
                    # 🔧 FIX: 신호가가 아닌 실제 체결가 사용
                    with _POSITION_LOCK:
                        actual_entry = OPEN_POSITIONS.get(m, {}).get("entry_price", pre["price"])
                        _entry_opened = OPEN_POSITIONS.get(m, {}).get("state") == "open"
                    if _entry_opened:
                        _pipeline_inc("send_success")
                        _pipeline_hourly_inc("success")

                    # 🔧 FIX: 별도 스레드에서 모니터링 실행 (메인 스캔 루프 블로킹 방지)
                    # 🔧 FIX: 모니터링 스레드 중복 방지 + 죽은 스레드 감지
                    with _MONITOR_LOCK:
                        existing_thread = _ACTIVE_MONITORS.get(m)
                        if existing_thread is not None:
                            # 🔧 FIX: 스레드가 살아있는지 확인 (is_alive)
                            if isinstance(existing_thread, threading.Thread) and existing_thread.is_alive():
                                print(f"[MON_SKIP] {m} 이미 모니터링 중 → 스레드 생성 스킵")
                                # 🔧 FIX: 새로 획득한 락 해제 (기존 모니터가 포지션 관리)
                                _release_entry_lock(m)
                                _lock_held = False
                                continue
                            # 죽은 스레드면 정리하고 새로 시작
                            print(f"[MON_CLEANUP] {m} 죽은 모니터 스레드 정리")
                            _ACTIVE_MONITORS.pop(m, None)

                    pre_copy = dict(pre)  # 클로저 문제 방지
                    def _run_monitor(market, entry, pre_data, tight):
                        try:
                            monitor_position(market, entry, pre_data, tight_mode=tight)
                            # 🔧 FIX: monitor_position 종료 후 포지션 잔존 시 remonitor 연결
                            # 기존: orphan_sync 30초 후에야 감지 → 무감시 갭 존재
                            # 변경: 리테스트 모니터와 동일하게 즉시 remonitor 연결
                            with _POSITION_LOCK:
                                _pos_after = OPEN_POSITIONS.get(market)
                            if _pos_after and _pos_after.get("state") == "open":
                                remonitor_until_close(market, entry, pre_data, tight_mode=tight)
                        except Exception as e:
                            print(f"[MON_ERR] {market}: {e}")
                            traceback.print_exc()
                            # 🔧 FIX: 예외 발생 시 알람 + 잔고 확인 후 정리
                            try:
                                actual = get_balance_with_locked(market)
                                # 🔧 FIX: -1 = 조회 실패 → 포지션 유지 (오탐 방지)
                                if actual < 0:
                                    tg_send(f"⚠️ {market} 모니터링 오류 (잔고 조회 실패)\n• 예외: {e}\n• 포지션 유지")
                                elif actual <= 1e-12:
                                    # 🔧 FIX: 매수 직후 300초 내 잔고=0은 API 지연일 수 있음 → 포지션 유지
                                    with _RECENT_BUY_LOCK:
                                        buy_age = time.time() - _RECENT_BUY_TS.get(market, 0)
                                    if buy_age < 300:
                                        tg_send(f"⚠️ {market} 모니터링 오류 (매수 {buy_age:.0f}초 전, 잔고=0 but 포지션 유지)\n• 예외: {e}")
                                    else:
                                        # 잔고 0이면 이미 청산됨 → 알람만 발송
                                        tg_send(f"⚠️ {market} 모니터링 오류 (이미 청산됨)\n• 예외: {e}")
                                        with _POSITION_LOCK:
                                            OPEN_POSITIONS.pop(market, None)
                                else:
                                    # 잔고 있으면 청산 시도
                                    tg_send(f"🚨 {market} 모니터링 오류 → 청산 시도\n• 예외: {e}")
                                    close_auto_position(market, f"모니터링예외 | {e}")
                            except Exception as cleanup_err:
                                print(f"[MON_CLEANUP_ERR] {market}: {cleanup_err}")
                                tg_send(f"🚨 {market} 모니터링 오류 (청산 시도 실패)\n• 예외: {e}")
                        finally:
                            _release_entry_lock(market)
                            # 🔧 FIX: 모니터링 종료 시 활성 목록에서 제거
                            with _MONITOR_LOCK:
                                _ACTIVE_MONITORS.pop(market, None)

                    mon_thread = threading.Thread(
                        target=_run_monitor,
                        args=(m, actual_entry, pre_copy, tight_mode),
                        daemon=True
                    )
                    # 🔧 FIX(0-1): 스레드 spawn 실패 시 락 orphan 방지
                    try:
                        mon_thread.start()
                    except Exception as thread_err:
                        print(f"[THREAD_ERR] {m} 모니터 스레드 생성 실패: {thread_err}")
                        _release_entry_lock(m)
                        _lock_held = False
                        with _POSITION_LOCK:
                            OPEN_POSITIONS.pop(m, None)
                        continue
                    # 🔧 FIX: 스레드 객체 저장 (ident 대신)
                    with _MONITOR_LOCK:
                        _ACTIVE_MONITORS[m] = mon_thread
                    _lock_held = False  # 모니터 스레드가 락 소유
                else:
                    # 🔧 FIX: 신호 발송 실패 시 pre_signal pending 정리 + 락 해제
                    with _POSITION_LOCK:
                        pos = OPEN_POSITIONS.get(m)
                        if pos and pos.get("pre_signal"):
                            OPEN_POSITIONS.pop(m, None)
                    _release_entry_lock(m)
                    _lock_held = False  # 🔧 FIX: 이중 해제 방지 (except 블록과 일관성)

              except Exception as e:
                # 🔧 심볼별 예외 처리: 락/펜딩 정리 후 다음 심볼 진행
                print(f"[SYMBOL_ERR][{m}] {e}")
                traceback.print_exc()
                # 🔧 FIX: 락 획득한 경우에만 해제 (미획득 시 모니터 스레드 락 삭제 방지)
                if _lock_held:
                    _release_entry_lock(m)
                with _POSITION_LOCK:
                    if OPEN_POSITIONS.get(m, {}).get("state") == "pending":
                        OPEN_POSITIONS.pop(m, None)

            cut_summary()
            if found == 0:
                req_summary()
            # scan_detect 단계 종료 (detect_leader_stock 루프 + 진입 판정 전체)
            _pipeline_record_stage("scan_detect", (time.time() - _t_detect) * 1000)
            # 사이클 내부 누적 측정 flush (detect_leader, universal_ind 각각)
            _flush_cycle_internal_timing()
            # 시간대별 동적 스캔 간격 적용
            aligned_sleep(get_scan_interval())

        except KeyboardInterrupt:
            print("Stopped by user.")
            # 🔧 FIX: 그레이스풀 셧다운 (상태 저장 + 스레드 정리)
            try:
                _save_bot_state()
                print("[SHUTDOWN] 봇 상태 저장 완료")
            except Exception as _se:
                print(f"[SHUTDOWN] 상태 저장 실패: {_se}")
            try:
                _candle_executor.shutdown(wait=False)
                print("[SHUTDOWN] ThreadPoolExecutor 종료")
            except Exception:
                pass
            try:
                tg_send("🛑 봇 수동 종료 (KeyboardInterrupt)")
            except Exception:
                pass
            break
        except Exception as e:
            print("[MAIN_ERR]", e)
            traceback.print_exc()
            print("[MAIN] 5초 후 재시작...")
            time.sleep(5)
            continue  # 💡 다시 루프 시작

if __name__ == "__main__":
    validate_config()
    bot_start_time = time.time()
    start_health_server()
    start_watchdogs()  # 🐕 워치독 시작 (헬스비트/세션리프레시/락청소)
    main()