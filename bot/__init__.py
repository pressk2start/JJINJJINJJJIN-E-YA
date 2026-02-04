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
"""

# 기존 모듈
from bot.config import *
from bot.indicators import *
from bot.utils import *

# 새로 분리된 함수 모듈들
from bot import gate_checks
from bot import ignition_checks
from bot import monitor_checks
from bot import signal_detection
