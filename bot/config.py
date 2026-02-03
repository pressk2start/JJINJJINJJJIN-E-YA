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
# 1단계 게이트 임계치
# =========================
GATE_TURN_MIN = 2.0
GATE_TURN_MAX = 30.0
GATE_SPREAD_MAX = 1.50
GATE_ACCEL_MIN = 0.3
GATE_ACCEL_MAX = 5.0
GATE_BUY_RATIO_MIN = 0.58
GATE_SURGE_MAX = 100.0
GATE_OVERHEAT_MAX = 20.0
GATE_IMBALANCE_MIN = 0.50
GATE_CONSEC_MIN = 0
GATE_CONSEC_MAX = 15
GATE_STRONGBREAK_OFF = False
GATE_STRONGBREAK_CONSEC_MIN = 6
GATE_STRONGBREAK_TURN_MAX = 25.0
GATE_STRONGBREAK_ACCEL_MAX = 3.5
GATE_CV_MAX = 6.0
GATE_FRESH_AGE_MAX = 5.0
GATE_PSTD_MAX = 0.08
GATE_PSTD_STRONGBREAK_MAX = 0.06

# pstd 가격대별 배율
PSTD_TIER_MULT_LOW = 2.5
PSTD_TIER_MULT_MID = 1.5
PSTD_TIER_MULT_HIGH = 1.0

# 과회전 상한
GATE_TURN_MAX_MAJOR = 400.0
GATE_TURN_MAX_ALT = 150.0
GATE_TURN_MAX_ALT_PROBE = 120.0
GATE_CONSEC_BUY_MIN_QUALITY = 8
GATE_VOL_MIN = 10_000_000
GATE_SURGE_MIN = 0.4
GATE_VOL_VS_MA_MIN = 0.3
GATE_PRICE_MIN = 0.0005

# 동적 임계치 하한
GATE_RELAX_SURGE_FLOOR = 0.1
GATE_RELAX_VOL_MA_FLOOR = 0.2
GATE_RELAX_TURN_FLOOR = 0.5
GATE_RELAX_BUY_FLOOR = 0.45
GATE_RELAX_IMB_FLOOR = 0.35
GATE_RELAX_ACCEL_FLOOR = 0.1

# 스프레드 가격대별 스케일링
SPREAD_SCALE_LOW = 1.0
SPREAD_CAP_LOW = 1.20
SPREAD_SCALE_MID = 0.40
SPREAD_CAP_MID = 0.55
SPREAD_SCALE_HIGH = 0.17
SPREAD_CAP_HIGH = 0.25

# Ignition 내부 임계치
IGN_TPS_MULTIPLIER = 4
IGN_TPS_MIN_TICKS = 15
IGN_CONSEC_BUY_MIN = 7
IGN_PRICE_IMPULSE_MIN = 0.005
IGN_UP_COUNT_MIN = 4
IGN_VOL_BURST_RATIO = 0.40
IGN_SPREAD_MAX = 0.40

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
# 틱/체결/위험 관리
# =========================
MIN_RELATIVE_KRW_X = 1.08
MIN_TICKS_COUNT = 3
MIN_TURNOVER = 0.018
TICKS_BUY_RATIO = 0.56
MIN_DEPTH_KRW = 6_000_000
MIN_REAL_TRADES = 10
STOP_LOSS_PCT = 0.008
RECHECK_SEC = 5

# Bot-aware
BOT_PINGPONG_MAX_BAND = 0.0015
BOT_PINGPONG_MIN_ALT = 0.90
BOT_WASH_REPEAT_VOL_N = 5
BOT_TWAP_MAX_CV = 0.48
BOT_TWAP_MAX_PSTD = 0.0018
BOT_ACCUM_MIN_BUY = 0.54
BOT_ACCUM_MAX_BUY = 0.68

# Ignition
IGN_BREAK_LOOKBACK = 12
IGN_MIN_BODY = 0.006
IGN_MIN_BUY = 0.60

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

# 포스트체크
POSTCHECK_WINDOW_SEC = 3
POSTCHECK_MIN_BUY = 0.46
POSTCHECK_MIN_RATE = 0.16
POSTCHECK_MAX_PSTD = 0.0028
POSTCHECK_MAX_CV = 0.72
POSTCHECK_MAX_DD = 0.018

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
