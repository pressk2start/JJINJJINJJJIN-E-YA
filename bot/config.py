# -*- coding: utf-8 -*-
"""정적 상수 (런타임에 변경되지 않는 값만)"""

# =========================
# 기본 설정
# =========================
TOP_N = 60
SCAN_INTERVAL = 6
COOLDOWN = 480
PARALLEL_WORKERS = 12

# =========================
# 수수료
# =========================
FEE_RATE_ROUNDTRIP = 0.001       # 왕복 0.1%
FEE_RATE_ONEWAY = FEE_RATE_ROUNDTRIP / 2
FEE_RATE = FEE_RATE_ROUNDTRIP    # 하위 호환

# =========================
# MFE 부분익절
# =========================
MFE_PARTIAL_TARGETS = {
    "🔥점화": 0.006,
    "강돌파 (EMA↑+고점↑)": 0.005,
    "EMA↑": 0.005,
    "고점↑": 0.005,
    "거래량↑": 0.005,
    "기본": 0.005,
}
MFE_PARTIAL_RATIO = 0.5

# =========================
# 1단계 게이트 임계치 (완화됨)
# =========================
GATE_TURN_MIN = 1.0              # 2.0 → 1.0 완화
GATE_TURN_MAX = 50.0             # 30.0 → 50.0 완화
GATE_SPREAD_MAX = 2.50           # 1.50 → 2.50 완화
GATE_ACCEL_MIN = 0.1             # 0.3 → 0.1 완화
GATE_ACCEL_MAX = 8.0             # 5.0 → 8.0 완화
GATE_BUY_RATIO_MIN = 0.52        # 0.58 → 0.52 완화
GATE_SURGE_MAX = 150.0           # 100.0 → 150.0 완화
GATE_OVERHEAT_MAX = 30.0         # 20.0 → 30.0 완화
GATE_IMBALANCE_MIN = 0.40        # 0.50 → 0.40 완화
GATE_CONSEC_MIN = 0
GATE_CONSEC_MAX = 20             # 15 → 20 완화
GATE_STRONGBREAK_OFF = False
GATE_STRONGBREAK_CONSEC_MIN = 4  # 6 → 4 완화
GATE_STRONGBREAK_TURN_MAX = 35.0 # 25.0 → 35.0 완화
GATE_STRONGBREAK_ACCEL_MAX = 5.0 # 3.5 → 5.0 완화
GATE_CV_MAX = 8.0                # 6.0 → 8.0 완화
GATE_FRESH_AGE_MAX = 8.0         # 5.0 → 8.0 완화
GATE_PSTD_MAX = 0.12             # 0.08 → 0.12 완화
GATE_PSTD_STRONGBREAK_MAX = 0.10 # 0.06 → 0.10 완화

# pstd 가격대별 배율
PSTD_TIER_MULT_LOW = 2.5
PSTD_TIER_MULT_MID = 1.5
PSTD_TIER_MULT_HIGH = 1.0

# 과회전 상한 (완화됨)
GATE_TURN_MAX_MAJOR = 500.0      # 400.0 → 500.0 완화
GATE_TURN_MAX_ALT = 200.0        # 150.0 → 200.0 완화
GATE_TURN_MAX_ALT_PROBE = 180.0  # 120.0 → 180.0 완화
GATE_CONSEC_BUY_MIN_QUALITY = 5  # 8 → 5 완화
GATE_VOL_MIN = 5_000_000         # 10M → 5M 완화
GATE_SURGE_MIN = 0.2             # 0.4 → 0.2 완화
GATE_VOL_VS_MA_MIN = 0.15        # 0.3 → 0.15 완화
GATE_PRICE_MIN = 0.0001          # 0.0005 → 0.0001 완화

# 동적 임계치 하한 (완화됨)
GATE_RELAX_SURGE_FLOOR = 0.05    # 0.1 → 0.05 완화
GATE_RELAX_VOL_MA_FLOOR = 0.10   # 0.2 → 0.10 완화
GATE_RELAX_TURN_FLOOR = 0.3      # 0.5 → 0.3 완화
GATE_RELAX_BUY_FLOOR = 0.40      # 0.45 → 0.40 완화
GATE_RELAX_IMB_FLOOR = 0.25      # 0.35 → 0.25 완화
GATE_RELAX_ACCEL_FLOOR = 0.05    # 0.1 → 0.05 완화

# 스프레드 가격대별 스케일링
SPREAD_SCALE_LOW = 1.0
SPREAD_CAP_LOW = 1.20
SPREAD_SCALE_MID = 0.40
SPREAD_CAP_MID = 0.55
SPREAD_SCALE_HIGH = 0.17
SPREAD_CAP_HIGH = 0.25

# 조건부 스프레드 임계치 (% 단위, 0.06 = 0.06%)
# 🔧 FIX: 하드코딩된 매직넘버를 상수로 추출
SPREAD_CONDITIONAL_MAX_PCT = 0.06   # 조건부 스프레드 체크 임계치
PSTD_CONDITIONAL_MAX = 0.06         # 조건부 pstd 체크 임계치 (소수 단위)

# Ignition 내부 임계치 (완화됨)
IGN_TPS_MULTIPLIER = 3           # 4 → 3 완화
IGN_TPS_MIN_TICKS = 10           # 15 → 10 완화
IGN_CONSEC_BUY_MIN = 5           # 7 → 5 완화
IGN_PRICE_IMPULSE_MIN = 0.003    # 0.005 → 0.003 완화
IGN_UP_COUNT_MIN = 3             # 4 → 3 완화
IGN_VOL_BURST_RATIO = 0.30       # 0.40 → 0.30 완화
IGN_SPREAD_MAX = 0.60            # 0.40 → 0.60 완화

# =========================
# 리테스트 설정
# =========================
RETEST_MODE_ENABLED = True
RETEST_PEAK_MIN_GAIN = 0.015
RETEST_PULLBACK_MIN = 0.003
RETEST_PULLBACK_MAX = 0.020
RETEST_BOUNCE_MIN = 0.002
RETEST_TIMEOUT_SEC = 180
RETEST_SUPPORT_EMA = 20
RETEST_MORNING_HOURS = (8, 10)

# =========================
# 틱/체결/위험 관리 (완화됨)
# =========================
MIN_RELATIVE_KRW_X = 1.03        # 1.08 → 1.03 완화
MIN_TICKS_COUNT = 2              # 3 → 2 완화
MIN_TURNOVER = 0.010             # 0.018 → 0.010 완화
TICKS_BUY_RATIO = 0.50           # 0.56 → 0.50 완화
MIN_DEPTH_KRW = 3_000_000        # 6M → 3M 완화
MIN_REAL_TRADES = 5              # 10 → 5 완화
STOP_LOSS_PCT = 0.008
RECHECK_SEC = 5

# Bot-aware (완화됨)
BOT_PINGPONG_MAX_BAND = 0.0025   # 0.0015 → 0.0025 완화
BOT_PINGPONG_MIN_ALT = 0.85      # 0.90 → 0.85 완화
BOT_WASH_REPEAT_VOL_N = 7        # 5 → 7 완화
BOT_TWAP_MAX_CV = 0.60           # 0.48 → 0.60 완화
BOT_TWAP_MAX_PSTD = 0.003        # 0.0018 → 0.003 완화
BOT_ACCUM_MIN_BUY = 0.48         # 0.54 → 0.48 완화
BOT_ACCUM_MAX_BUY = 0.75         # 0.68 → 0.75 완화

# Ignition (완화됨)
IGN_BREAK_LOOKBACK = 15          # 12 → 15 완화 (더 넓은 범위)
IGN_MIN_BODY = 0.004             # 0.006 → 0.004 완화
IGN_MIN_BUY = 0.52               # 0.60 → 0.52 완화

# 틱 기반 조기 브레이크
USE_TICK_BREAK = True
TICK_BREAK_GAP = 0.0020

# 적응식 볼륨 서지
ABS_SURGE_KRW = 2_200_000
RELAXED_X = 1.08

# 쿨다운 히스테리시스
REARM_MIN_SEC = 45
REARM_PRICE_GAP = 0.009
REARM_PULLBACK_MAX = 0.004
REARM_REBREAK_MIN = 0.0028

# 포스트체크 (완화됨)
POSTCHECK_WINDOW_SEC = 3
POSTCHECK_MIN_BUY = 0.40         # 0.46 → 0.40 완화
POSTCHECK_MIN_RATE = 0.10        # 0.16 → 0.10 완화
POSTCHECK_MAX_PSTD = 0.005       # 0.0028 → 0.005 완화
POSTCHECK_MAX_CV = 1.0           # 0.72 → 1.0 완화
POSTCHECK_MAX_DD = 0.025         # 0.018 → 0.025 완화

# 동적 손절(ATR)
ATR_PERIOD = 14
ATR_MULT = 0.35
DYN_SL_MIN = 0.004
DYN_SL_MAX = 0.006

# 메가 브레이크아웃
ULTRA_RELAX_ON_MEGA = True
MEGA_BREAK_MIN_GAP = 0.012
MEGA_MIN_1M_CHG = 0.012
MEGA_VOL_Z = 2.8
MEGA_ABS_KRW = 4_000_000

# Pre-break Probe
PREBREAK_ENABLED = False
PREBREAK_HIGH_PCT = 0.002
PREBREAK_POSTCHECK_SEC = 2
PREBREAK_BUY_MIN = 0.60
PREBREAK_KRW_PER_SEC_MIN = 20_000
PREBREAK_IMBALANCE_MIN = 0.55

# =========================
# 리스크 스코어 가중치
# =========================
SCORE_WEIGHTS = {
    "buy_ratio": 28,
    "spread": 15,
    "turn": 22,
    "imbalance": 18,
    "fresh": 7,
    "volume_surge": 10,
}

# =========================
# 자동 학습 설정
# =========================
AUTO_LEARN_ENABLED = True
AUTO_LEARN_APPLY = False
AUTO_LEARN_MIN_TRADES = 100
AUTO_LEARN_INTERVAL = 10
AUTO_LEARN_STREAK_TRIGGER = 3
PATH_REPORT_INTERVAL = 10

FEATURE_FIELDS = [
    "ts", "market", "entry_price", "exit_price",
    "buy_ratio", "spread", "turn", "imbalance", "volume_surge",
    "fresh", "score", "entry_mode",
    "signal_tag", "filter_type",
    "consecutive_buys", "avg_krw_per_tick", "flow_acceleration",
    "overheat", "fresh_age", "cv", "pstd", "best_ask_krw",
    "shadow_flags", "would_cut", "is_prebreak",
    "added", "exit_reason",
    "mfe_pct", "mae_pct",
    "pnl_pct", "result", "hold_sec",
]
