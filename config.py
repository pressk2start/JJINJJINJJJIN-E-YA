# -*- coding: utf-8 -*-
"""
봇 전역 설정값 (config.py)
- 정적 상수 + 프로파일 기본값
- 런타임 상태(lock, deque, dict 등)는 bot.py에 유지
- auto-learn / exit profile로 수정되는 값은 ★ 표시
"""
import os

# ============================================================
# 1. 스캔 & 모니터링
# ============================================================
TOP_N = 60
SCAN_INTERVAL = 6
COOLDOWN = 240
PARALLEL_WORKERS = 12

# ============================================================
# 2. 청산 제어 (anti-whipsaw)
# ============================================================
# ★ _apply_exit_profile()에서 프로파일별 덮어쓰기
WARMUP_SEC = 5
HARD_STOP_DD = 0.010
EXIT_DEBOUNCE_SEC = 10
EXIT_DEBOUNCE_N = 3

# ★ auto-learn에서 조정 가능
DYN_SL_MIN = 0.010
DYN_SL_MAX = 0.015

PROFIT_CHECKPOINT_BASE = 0.005
PROFIT_CHECKPOINT_MIN_ALPHA = 0.0003

CHART_OPTIMAL_EXIT_SEC = 900

# MFE 비율
MFE_RR_MULTIPLIERS = {
    "거래량3배": 1.0,
    "20봉_고점돌파": 1.0,
    "15m_눌림반전": 1.0,
    "기본": 1.0,
}
SCALP_TO_RUNNER_MIN_BUY = 0.52
SCALP_TO_RUNNER_MIN_ACCEL = 0.4

# 트레일링 손절 ★ _apply_exit_profile()에서 덮어쓰기
TRAIL_ATR_MULT = 1.0
TRAIL_DISTANCE_MIN_BASE = 0.003

# ============================================================
# 3. 수수료
# ============================================================
FEE_RATE_ROUNDTRIP = 0.001
FEE_RATE_ONEWAY = FEE_RATE_ROUNDTRIP / 2
FEE_RATE = FEE_RATE_ROUNDTRIP

# ============================================================
# 4. 하이브리드 모드 & 피처 플래그
# ============================================================
USE_5M_CONTEXT = True
POSTCHECK_ENABLED = False
EARLY_FLOW_MIN_KRWPSEC = 15_000

# ============================================================
# 5. 청산 프로파일 (gentle / balanced / strict)
# ============================================================
EXIT_PROFILE = os.getenv("EXIT_PROFILE", "balanced").lower()

# ★ _apply_exit_profile()에서 프로파일별 덮어쓰기 (기본값 = balanced)
SPIKE_RECOVERY_WINDOW = 3
SPIKE_RECOVERY_MIN_BUY = 0.58
CTX_EXIT_THRESHOLD = 3

EXIT_PROFILES = {
    "gentle": {
        "WARMUP_SEC": 7,
        "HARD_STOP_DD": 0.012,
        "EXIT_DEBOUNCE_SEC": 15,
        "EXIT_DEBOUNCE_N": 4,
        "TRAIL_ATR_MULT": 1.2,
        "TRAIL_DISTANCE_MIN_BASE": 0.004,
        "SPIKE_RECOVERY_WINDOW": 4,
        "SPIKE_RECOVERY_MIN_BUY": 0.56,
        "CTX_EXIT_THRESHOLD": 4,
    },
    "balanced": {
        "WARMUP_SEC": 5,
        "HARD_STOP_DD": 0.010,
        "EXIT_DEBOUNCE_SEC": 10,
        "EXIT_DEBOUNCE_N": 3,
        "TRAIL_ATR_MULT": 1.0,
        "TRAIL_DISTANCE_MIN_BASE": 0.003,
        "SPIKE_RECOVERY_WINDOW": 3,
        "SPIKE_RECOVERY_MIN_BUY": 0.58,
        "CTX_EXIT_THRESHOLD": 3,
    },
    "strict": {
        "WARMUP_SEC": 3,
        "HARD_STOP_DD": 0.008,
        "EXIT_DEBOUNCE_SEC": 8,
        "EXIT_DEBOUNCE_N": 2,
        "TRAIL_ATR_MULT": 0.8,
        "TRAIL_DISTANCE_MIN_BASE": 0.002,
        "SPIKE_RECOVERY_WINDOW": 2,
        "SPIKE_RECOVERY_MIN_BUY": 0.65,
        "CTX_EXIT_THRESHOLD": 2,
    },
}

# ============================================================
# 6. 리테스트 진입 (비활성)
# ============================================================
RETEST_MODE_ENABLED = False
RETEST_PEAK_MIN_GAIN = 0.015
RETEST_PULLBACK_MIN = 0.006
RETEST_PULLBACK_MAX = 0.020
RETEST_BOUNCE_MIN = 0.002
RETEST_TIMEOUT_SEC = 900
RETEST_MORNING_HOURS = (8, 10)
RETEST_BUY_RATIO_MIN = 0.55
RETEST_IMBALANCE_MIN = 0.35
RETEST_SPREAD_MAX = 0.40
RETEST_KRW_PER_SEC_DEAD = 5000
RETEST_EMA_GAP_MIN = 0.001

# ============================================================
# 7. 동그라미 엔트리 (비활성)
# ============================================================
CIRCLE_ENTRY_ENABLED = False
CIRCLE_MAX_CANDLES = 10
CIRCLE_TIMEOUT_SEC = 600
CIRCLE_PULLBACK_MIN_PCT = 0.007
CIRCLE_PULLBACK_MAX_PCT = 0.025
CIRCLE_RECLAIM_LEVEL = "body_low"
CIRCLE_MIN_IGN_SCORE = 3
CIRCLE_ENTRY_MODE = "half"
CIRCLE_RETRY_COOLDOWN_SEC = 15
CIRCLE_STATE_MIN_DWELL_SEC = 8
CIRCLE_REBREAK_BUY_RATIO_MIN = 0.50
CIRCLE_REBREAK_IMBALANCE_MIN = 0.20
CIRCLE_REBREAK_SPREAD_MAX = 0.35
CIRCLE_REBREAK_MIN_SCORE = 3
CIRCLE_REBREAK_KRW_PER_SEC_MIN = 8000
CIRCLE_ATR_FLOOR = 0.003
CIRCLE_IMB_HARD_FLOOR = 0.05

# ============================================================
# 8. 박스권 매매
# ============================================================
BOX_ENABLED = True
BOX_LOOKBACK = 30
BOX_USE_5MIN = True
BOX_MIN_RANGE_PCT = 0.020
BOX_MAX_RANGE_PCT = 0.035
BOX_MIN_TOUCHES = 3
BOX_TOUCH_ZONE_PCT = 0.20
BOX_ENTRY_ZONE_PCT = 0.12
BOX_EXIT_ZONE_PCT = 0.20
BOX_SL_BUFFER_PCT = 0.007
BOX_MIN_VOL_KRW = 80_000_000
BOX_ENTRY_MODE = "full"
BOX_MAX_POSITIONS = 2
BOX_COOLDOWN_SEC = 300
BOX_SCAN_INTERVAL = 60
BOX_MIN_BB_WIDTH = 0.012
BOX_MAX_BB_WIDTH = 0.028
BOX_CONFIRM_SEC = 10
BOX_MIN_MIDCROSS = 3
BOX_MAX_TREND_SLOPE = 0.003
BOX_MIN_CLOSE_IN_RANGE = 0.70

# ============================================================
# 9. 틱/체결 기반 임계치
# ============================================================
MIN_TURNOVER = 0.018
TICKS_BUY_RATIO = 0.56

# ============================================================
# 10. 1단계 게이트 (Stage1 Gate) — ★ auto-learn 대상
# ============================================================
GATE_TURN_MAX = 40.0
GATE_SPREAD_MAX = 0.40
GATE_ACCEL_MIN = 0.3
GATE_ACCEL_MAX = 6.0
GATE_BUY_RATIO_MIN = 0.50
GATE_SURGE_MAX = 50.0
GATE_OVERHEAT_MAX = 25.0
GATE_IMBALANCE_MIN = 0.50
GATE_CONSEC_MIN = 2
GATE_STRONGBREAK_OFF = False
GATE_STRONGBREAK_TURN_MAX = 25.0
GATE_STRONGBREAK_ACCEL_MAX = 3.5
GATE_STRONGBREAK_BODY_MAX = 1.0
GATE_IGNITION_BODY_MAX = 1.5
GATE_EMA_CHASE_MAX = 1.0
GATE_IGNITION_ACCEL_MIN = 1.1
GATE_FRESH_AGE_MAX = 10.0
GATE_PSTD_MAX = 0.20
GATE_PSTD_STRONGBREAK_MAX = 0.12
GATE_TURN_MAX_MAJOR = 400.0
GATE_TURN_MAX_ALT = 80.0
GATE_VOL_MIN = 1_000_000
GATE_VOL_VS_MA_MIN = 0.5
GATE_BODY_MIN = 0.003
GATE_UW_RATIO_MIN = 0.05
GATE_GREEN_STREAK_MAX = 5
GATE_RELAX_VOL_MA_FLOOR = 0.2

# 스프레드 가격대별 스케일링
SPREAD_SCALE_LOW = 1.0
SPREAD_CAP_LOW = 0.80
SPREAD_SCALE_MID = 1.0
SPREAD_CAP_MID = 0.45
SPREAD_SCALE_HIGH = 0.80
SPREAD_CAP_HIGH = 0.35

# ============================================================
# 11. 점화 (Ignition) 내부 임계치
# ============================================================
IGN_TPS_MULTIPLIER = 3
IGN_TPS_MIN_TICKS = 15
IGN_CONSEC_BUY_MIN = 7
IGN_PRICE_IMPULSE_MIN = 0.005
IGN_UP_COUNT_MIN = 4
IGN_VOL_BURST_RATIO = 0.40
IGN_MIN_ABS_KRW_10S = 3_000_000
IGN_SPREAD_MAX = 0.40

# ============================================================
# 12. Pre-break (비활성)
# ============================================================
PREBREAK_ENABLED = False
PREBREAK_HIGH_PCT = 0.002
PREBREAK_POSTCHECK_SEC = 1
PREBREAK_BUY_MIN = 0.60
PREBREAK_KRW_PER_SEC_MIN = 20_000
PREBREAK_IMBALANCE_MIN = 0.55

# ============================================================
# 13. 손절 / 모니터링 / 쿨다운
# ============================================================
STOP_LOSS_PCT = 0.005
RECHECK_SEC = 3
REARM_MIN_SEC = 45
REARM_PRICE_GAP = 0.009
REARM_PULLBACK_MAX = 0.004
REARM_REBREAK_MIN = 0.0028

# 포스트체크
POSTCHECK_WINDOW_SEC = 3.0
POSTCHECK_MIN_BUY = 0.52
POSTCHECK_MIN_RATE = 0.16
POSTCHECK_MAX_PSTD = 0.0028
POSTCHECK_MAX_CV = 0.72
POSTCHECK_MAX_DD = 0.010

# 동적 손절 (ATR)
ATR_PERIOD = 14
ATR_MULT = 0.85

# 메가 브레이크아웃
ULTRA_RELAX_ON_MEGA = True
MEGA_BREAK_MIN_GAP = 0.012
MEGA_MIN_1M_CHG = 0.012
MEGA_VOL_Z = 2.8
MEGA_ABS_KRW = 4_000_000

# ============================================================
# 14. 포지션 관리
# ============================================================
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "5"))
PARTIAL_PENDING_TIMEOUT = 30.0
DUST_PREVENT_KRW = 6000

# 부분청산
PARTIAL_EXIT_PROFIT_DROP = 0.002
PARTIAL_EXIT_LOSS_DROP = 0.001
BREAKEVEN_BOX = 0.0015
SOFT_GUARD_SEC = 30

# ============================================================
# 15. 자동학습 & 리포트
# ============================================================
AUTO_LEARN_ENABLED = False
AUTO_LEARN_APPLY = False
AUTO_LEARN_MIN_TRADES = 100
AUTO_LEARN_INTERVAL = 10
AUTO_LEARN_STREAK_TRIGGER = 3
PATH_REPORT_INTERVAL = 10
BATCH_REPORT_INTERVAL = 50

# ============================================================
# 16. 코인 손실 관리
# ============================================================
COIN_LOSS_MAX = 2
COIN_LOSS_COOLDOWN = 900
ORPHAN_SYNC_INTERVAL = 30

# ============================================================
# 17. 디버그 & 알림
# ============================================================
DEBUG_CUT = True
DEBUG_NEAR_MISS = True
SILENT_MIDDLE_ALERTS = False
ALERT_TTL = 1800
_SPIKE_WAVE_WINDOW = 1800

# ============================================================
# 18. 리스크 관리 (env 기본값)
# ============================================================
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.002"))
AGGRESSIVE_MODE = os.getenv("AGGRESSIVE_MODE", "1") == "1"
USE_PYRAMIDING = False
SEED_RISK_FRACTION = float(os.getenv("SEED_RISK_FRACTION", "0.55"))
ADD_RISK_FRACTION = float(os.getenv("ADD_RISK_FRACTION", "0.55"))
PYRAMID_ADD_MIN_GAIN = float(os.getenv("PYRAMID_ADD_MIN_GAIN", "0.010"))
PYRAMID_ADD_FLOW_MIN_BUY = float(os.getenv("PYRAMID_ADD_FLOW_MIN_BUY", "0.60"))
PYRAMID_ADD_FLOW_MIN_KRWPSEC = float(os.getenv("PYRAMID_ADD_FLOW_MIN_KRWPSEC", "25000"))
PYRAMID_ADD_COOLDOWN_SEC = int(os.getenv("PYRAMID_ADD_COOLDOWN_SEC", "12"))

# ============================================================
# 19. 캐시 TTL
# ============================================================
_TICKS_TTL = 2.0
MKTS_CACHE_TTL = 90

# ============================================================
# 20. MFE 시계열 추적 (진입 후 시간대별 최고수익률 기록)
# ============================================================
# 스냅샷 시점 (초) — 진입 후 각 시점에서의 MFE/현재가 기록
# 🔧 v7: 첫 2분 내 5초 간격 세분화 (초기 MFE 피크 정밀 추적)
MFE_SNAPSHOT_TIMES = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600]
# 시그널별 성과 통계 최소 샘플 수 (이 이상 모여야 리포트/적응형 청산에 활용)
SIGNAL_STATS_MIN_TRADES = 5
# 시그널별 통계 파일 경로
SIGNAL_STATS_PATH = "signal_stats.json"

# ============================================================
# 21. MFE→Exit 자동 피드백 (시그널별 MFE 피크 기반 청산 파라미터 자동 조정)
# ============================================================
MFE_FEEDBACK_ENABLED = True
MFE_FEEDBACK_MIN_TRADES = 20       # 최소 거래 수 (이 이상 모여야 피드백 적용)
MFE_FEEDBACK_ACTIVATION_RATIO = 0.6  # activation = avg_peak_mfe × ratio (60%)
MFE_FEEDBACK_TRAIL_RATIO = 0.35      # trail_pct = avg_peak_mfe × ratio (35%)
MFE_FEEDBACK_MAX_ACTIVATION = 0.010  # activation 상한 1.0%
MFE_FEEDBACK_MAX_TRAIL = 0.006       # trail 상한 0.6%
MFE_FEEDBACK_MIN_ACTIVATION = 0.002  # activation 하한 0.2%
MFE_FEEDBACK_MIN_TRAIL = 0.001       # trail 하한 0.1%

# ============================================================
# 22. 상태 영속화 (서버 재시작 시에도 누적 데이터 유지)
# ============================================================
STATE_PERSIST_PATH = os.path.join(os.getcwd(), "bot_state.json")
STATE_PERSIST_INTERVAL = 30  # 30초마다 자동 저장

# ============================================================
# 23. 섀도우 시그널 필터링 (유의미한 통계만 수집)
# ============================================================
SHADOW_MIN_SIGNAL_RATE = 3.0       # 시그널 발생률 최소 % (이하면 노이즈로 간주)
SHADOW_MIN_COINS = 2               # 최소 코인 수 (1코인에서만 뜨면 무시)
SHADOW_ALERT_ENABLED = True        # 텔레그램 섀도우 알림 on/off
SHADOW_LOG_MIN_RATE = 1.0          # CSV 로깅 최소 시그널률 % (이하면 로그도 안 남김)
SHADOW_ALERT_CD_SEC = 600          # 섀도우 알림 쿨다운 (기본 5분→10분)
