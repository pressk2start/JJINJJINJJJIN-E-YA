# -*- coding: utf-8 -*-
"""
설정 변경 분석 및 승률 영향 진단

기존 설정 (승률 좋았던 시기) vs 현재 설정 비교
"""

# =========================
# 📊 설정 비교 테이블
# =========================

# 이전 설정 (251130-MADEIT... 파일 기준)
OLD_CONFIG = {
    # 기본 설정
    "TOP_N": 60,
    "SCAN_INTERVAL": 6,

    # 트레일링 손절 (핵심!)
    "TRAIL_ARM_GAIN": 0.012,        # +1.2% 이익 시 트레일링 활성화
    "TRAIL_DISTANCE_MIN": 0.010,   # 최소 트레일 간격 1.0%

    # 포스트체크
    "POSTCHECK_ENABLED": False,    # 비활성화

    # 초기 거래속도
    "EARLY_FLOW_MIN_KRWPSEC": 22_000,

    # 추매 조건
    "PYRAMID_ADD_MIN_GAIN": 0.008,  # +0.8% 이상에서 추매
    "PYRAMID_ADD_FLOW_MIN_BUY": 0.60,
    "PYRAMID_ADD_FLOW_MIN_KRWPSEC": 35_000,

    # final_price_guard
    "MAX_DRIFT_NORMAL": 0.012,
    "MAX_DRIFT_AGGRESSIVE": 0.025,
}

# 새 설정 (사용자가 제공한 코드)
NEW_CONFIG = {
    # 기본 설정
    "TOP_N": 120,                  # 60 → 120 (2배 확장!)
    "SCAN_INTERVAL": 6,

    # 트레일링 손절 (핵심 변경!)
    "TRAIL_ARM_GAIN": 0.003,       # +0.3% 이익 시 트레일링 활성화 (0.012 → 0.003, 4배 빠름!)
    "TRAIL_DISTANCE_MIN": 0.005,   # 최소 트레일 간격 0.5% (0.010 → 0.005, 2배 타이트!)

    # 포스트체크
    "POSTCHECK_ENABLED": True,     # 활성화!

    # 초기 거래속도
    "EARLY_FLOW_MIN_KRWPSEC": 18_000,  # 22k → 18k 완화

    # 추매 조건 (엄격화!)
    "ADD1_MIN_GAIN": 0.004,        # +0.4% 이상 (신규)
    "ADD1_MIN_BUY": 0.60,          # 매수비 60%+ (신규)
    "ADD1_MIN_KRWPSEC": 20_000,    # 절대속도 20k+/s (신규)
    "ADD1_MIN_ACCEL": 1.25,        # 상대가속 t15 ≥ 1.25×t30 (신규)

    # final_price_guard
    "MAX_DRIFT_NORMAL": 0.018,     # 0.012 → 0.018 완화
    "MAX_DRIFT_AGGRESSIVE": 0.030, # 0.025 → 0.030 완화

    # 새로 추가된 필터들
    "BB_EXCEED_FILTER": True,      # BB 상단 초과 차단
    "VOLUME_DECLINING_FILTER": True,  # 거래량 둔화 차단
    "LATE_ENTRY_GATE": True,       # 끝자락 추격 차단
    "EXTREME_OVERBOUGHT_FILTER": True,  # 극단 과매수 차단
    "PROBE_COUNTERTREND_FILTER": True,  # 역추세 probe 차단
}


def analyze_changes():
    """설정 변경이 승률에 미치는 영향 분석"""

    print("="*70)
    print("🔍 설정 변경 분석 리포트")
    print("="*70)

    # 🚨 핵심 문제점
    print("\n" + "🚨 핵심 문제점 (승률 하락 주요 원인)")
    print("-"*70)

    issues = []

    # 1. 트레일링 너무 빨리 무장
    print("""
1️⃣ TRAIL_ARM_GAIN: 0.012 → 0.003 (4배 빠름!)

   문제: 고작 +0.3% 이익에서 트레일링 무장
   - 정상적인 눌림에도 조기 청산
   - 작은 흔들림에 견디지 못함
   - 큰 수익 기회를 놓침

   권장: 0.008 ~ 0.012 사이로 복원
""")

    # 2. 트레일 간격 너무 타이트
    print("""
2️⃣ TRAIL_DISTANCE_MIN: 0.010 → 0.005 (2배 타이트!)

   문제: 트레일 간격이 0.5%밖에 안 됨
   - 작은 되돌림에도 청산 트리거
   - 변동성이 조금만 커도 손절
   - 추세 타기 불가능

   권장: 0.008 ~ 0.012 사이로 복원
""")

    # 3. 포스트체크 활성화
    print("""
3️⃣ POSTCHECK_ENABLED: False → True

   문제: 6초 포스트체크가 추가됨
   - 초동 신호도 필터링
   - 빠른 급등 놓침
   - 진입 기회 감소

   권장: probe 모드에서는 비활성화
""")

    # 4. 추매 조건 과도하게 엄격
    print("""
4️⃣ 추매(probe→confirm) 조건 신규 추가 및 엄격화

   ADD1_MIN_GAIN: 0.004 (+0.4% 이상)
   ADD1_MIN_BUY: 0.60 (매수비 60%+)
   ADD1_MIN_KRWPSEC: 20,000
   ADD1_MIN_ACCEL: 1.25 (상대가속)

   문제: probe가 confirm으로 전환되기 어려움
   - probe는 소액이라 수익 제한
   - 좋은 추세에서도 추매 못함
   - probe 손절만 늘어남

   권장: ADD1_MIN_GAIN을 0.003으로,
         ADD1_MIN_BUY를 0.56으로 완화
""")

    # 5. 새로 추가된 필터들
    print("""
5️⃣ 새로 추가된 다중 필터 (오버필터링)

   - BB 상단 초과 차단
   - 거래량 둔화 차단
   - 끝자락 추격 차단 (Late Entry Gate)
   - 극단 과매수 차단 (BB %b > 0.95)
   - 역추세 probe 차단

   문제: 필터가 너무 많아서 신호 대부분 걸러짐
   - 강한 추세에서도 "꼭지 추격"으로 차단
   - 정상적인 돌파도 "오버필터링"
   - 진입 기회 급감

   권장: 필터를 점진적으로 켜서 A/B 테스트
""")

    print("\n" + "="*70)
    print("📋 권장 설정 (승률 복구용)")
    print("="*70)

    print("""
# 트레일링 (이전 설정으로 복원)
TRAIL_ARM_GAIN = 0.008        # 0.003 → 0.008 (적당히 여유)
TRAIL_DISTANCE_MIN = 0.009    # 0.005 → 0.009 (적당히 여유)

# 포스트체크 (probe는 제외)
POSTCHECK_ENABLED = True      # 유지하되...
# → postcheck_6s()에서 entry_mode == "probe"면 SKIP 처리 추가

# 추매 조건 (완화)
ADD1_MIN_GAIN = 0.003         # 0.004 → 0.003
ADD1_MIN_BUY = 0.56           # 0.60 → 0.56
ADD1_MIN_KRWPSEC = 15_000     # 20000 → 15000
ADD1_MIN_ACCEL = 1.15         # 1.25 → 1.15

# 필터 (점진적 적용)
# → BB/Late Entry 필터는 꺼서 테스트
# → 승률 회복되면 하나씩 켜서 비교
""")

    print("\n" + "="*70)
    print("🧪 A/B 테스트 추천")
    print("="*70)

    print("""
1단계: 트레일링만 복원 (가장 영향 큼)
   TRAIL_ARM_GAIN = 0.008
   TRAIL_DISTANCE_MIN = 0.009
   → 1~2일 돌려서 승률 비교

2단계: 추매 조건 완화
   ADD1_MIN_GAIN = 0.003
   ADD1_MIN_BUY = 0.56
   → probe→confirm 전환률 확인

3단계: 필터 하나씩 켜기
   먼저 끄고 시작 → 승률 안정화 후 하나씩 활성화
   각 필터의 "컷" 횟수 vs "정상 진입" 비율 추적
""")

    return {
        "critical_issues": [
            "TRAIL_ARM_GAIN 너무 빠름 (0.003)",
            "TRAIL_DISTANCE_MIN 너무 타이트 (0.005)",
            "추매 조건 과도하게 엄격",
            "오버필터링 (새 필터 다수 추가)",
        ],
        "recommended_actions": [
            "TRAIL_ARM_GAIN을 0.008로 복원",
            "TRAIL_DISTANCE_MIN을 0.009로 복원",
            "ADD1_MIN_GAIN을 0.003으로 완화",
            "새 필터들을 일단 비활성화 후 A/B 테스트",
        ]
    }


def compare_side_by_side():
    """핵심 설정 나란히 비교"""

    print("\n" + "="*70)
    print("📊 핵심 설정 비교표")
    print("="*70)

    comparisons = [
        ("TRAIL_ARM_GAIN", 0.012, 0.003, "⚠️ 4배 빠름"),
        ("TRAIL_DISTANCE_MIN", 0.010, 0.005, "⚠️ 2배 타이트"),
        ("POSTCHECK_ENABLED", False, True, "⚠️ 신규 활성화"),
        ("TOP_N", 60, 120, "ℹ️ 2배 확장"),
        ("EARLY_FLOW_MIN_KRWPSEC", 22000, 18000, "✅ 완화"),
        ("MAX_DRIFT (normal)", 0.012, 0.018, "✅ 완화"),
    ]

    print(f"\n{'설정':<25} {'이전':<12} {'현재':<12} {'변화'}")
    print("-"*70)

    for name, old, new, note in comparisons:
        old_str = str(old)
        new_str = str(new)
        print(f"{name:<25} {old_str:<12} {new_str:<12} {note}")

    print("\n" + "-"*70)
    print("새로 추가된 필터:")
    print("   - BB_EXCEED_FILTER (BB 상단 초과 차단)")
    print("   - VOLUME_DECLINING_FILTER (거래량 둔화 차단)")
    print("   - LATE_ENTRY_GATE (끝자락 추격 차단)")
    print("   - EXTREME_OVERBOUGHT_FILTER (극단 과매수 차단)")
    print("   - PROBE_COUNTERTREND_FILTER (역추세 probe 차단)")
    print("   - BREAKEVEN_FLOOR (동적 수익 보호)")
    print("   - TP_EARLY, TP0, TP1, TP2, TP3 (사다리 익절)")


if __name__ == "__main__":
    compare_side_by_side()
    print("\n")
    analyze_changes()
