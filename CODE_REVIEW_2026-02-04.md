# 최신 코드 종합 리뷰 보고서

**프로젝트**: JJINJJINJJJIN-E-YA (암호화폐 자동 트레이딩 봇)
**리뷰어**: Claude Code (Opus 4.5)
**날짜**: 2026-02-04
**대상**: 리팩토링된 최신 코드 (`bot/` 패키지)

---

## 1. 프로젝트 개요

암호화폐(알트코인) 급등 신호 감지 및 자동 포지션 관리 시스템

### 핵심 기능
1. **점화(Ignition) 감지**: 급등 시작점 4가지 조건 분석
2. **1단계 게이트**: 19개 품질 필터로 신호 검증
3. **리더 종목 탐지**: 진입 신호 생성 및 모드 결정
4. **포지션 모니터링**: 13개 청산 + 2개 부분익절 조건

### 파일 구조
```
bot/
├── __init__.py     # 1947줄 - 메인 로직 (4개 메인 함수 + 80+ 헬퍼)
├── config.py       # 210줄 - 설정 상수 (40+ 임계치)
├── indicators.py   # 240줄 - 기술 지표 순수 함수
└── utils.py        # 59줄 - 유틸리티 함수
test_core_functions.py  # 275줄 - 단위 테스트
```

---

## 2. 코드 품질 평가

### 2.1 강점 (Strengths) ✅

| 항목 | 평가 | 설명 |
|------|------|------|
| **모듈화** | 우수 | config/indicators/utils로 관심사 분리 |
| **순수 함수** | 우수 | 헬퍼 함수들이 글로벌 상태 의존 최소화 |
| **타입 힌트** | 양호 | 메인 함수 서명에 `List`, `Dict`, `Optional`, `Tuple` 사용 |
| **설정 중앙화** | 우수 | 모든 임계치가 config.py에서 관리됨 |
| **테스트 코드** | 양호 | 19개 테스트 케이스, 센티넬 값 처리 검증 |
| **문서화** | 양호 | docstring과 섹션별 주석 포함 |
| **에러 방어** | 양호 | None 체크, 기본값 설정 적용 |

### 2.2 메인 함수 분석

#### `ignition_detected()` (1270~1372줄) - **품질: 우수**
```python
def ignition_detected(
    market: str,
    ticks: List[Dict],
    candles: List[Dict],
    ob: Optional[Dict] = None,
    ...
) -> Tuple[bool, int, str]:
```
- ✅ 명확한 입출력 타입 힌트
- ✅ 4가지 조건을 개별 헬퍼 함수로 분리
- ✅ 점수 기반 판정 로직 명확
- ✅ 쿨다운 체크 포함

#### `stage1_gate()` (1377~1611줄) - **품질: 우수**
```python
def stage1_gate(
    market: str,
    cur_price: float,
    ...
) -> Tuple[bool, str, Dict[str, Any]]:
```
- ✅ 19개 체크를 개별 함수로 분리
- ✅ 동적 임계치 계산 로직 명확
- ✅ 메트릭 딕셔너리 반환으로 추적 용이
- ✅ 실패 사유 상세 제공

#### `detect_leader_stock()` (1616~1760줄) - **품질: 양호**
```python
def detect_leader_stock(
    market: str,
    ticks: List[Dict],
    candles: List[Dict],
    ...
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
```
- ✅ 단계별 필터링 로직 명확
- ✅ 신호 데이터 빌더 함수 사용
- ⚠️ 일부 지표 계산이 간략화됨 (주석 참고)

#### `monitor_position()` (1765~1936줄) - **품질: 양호**
```python
def monitor_position(
    position: Dict[str, Any],
    cur_price: float,
    ticks: List[Dict],
    ob: Optional[Dict] = None,
) -> Tuple[Optional[str], str, Dict[str, Any]]:
```
- ✅ 청산/부분익절 조건을 개별 함수로 분리
- ✅ 포지션 상태 업데이트 명확
- ⚠️ 일부 하드코딩된 값 존재 (아래 상세)

---

## 3. 발견된 이슈 및 개선 권장

### 3.1 HIGH - 파일 크기

**위치**: `bot/__init__.py` (1947줄)

**현황**: 메인 함수 4개 + 헬퍼 80개가 단일 파일에 통합

**권장**: 기능별 모듈 분리
```
bot/
├── __init__.py       # 패키지 진입점 (re-export만)
├── ignition.py       # 점화 감지 로직
├── gate.py           # 게이트 검증 로직
├── signal.py         # 신호 감지 로직
├── monitor.py        # 포지션 모니터링 로직
├── config.py         # (현재 유지)
├── indicators.py     # (현재 유지)
└── utils.py          # (현재 유지)
```

---

### 3.2 MEDIUM - 하드코딩된 값

**위치**: `bot/__init__.py:1847, 1877, 1893` 등

**현황**:
```python
# Line 1847
should_exit, reason = _mon_check_hard_stop_drawdown(
    dd_now, hard_stop_dd=0.02, alive_sec=alive_sec, warmup_sec=10  # 하드코딩
)

# Line 1877
should_exit, reason = _mon_check_no_peak_update_timeout(
    alive_sec, peak_update_count, min_peak_updates=2,
    no_peak_timeout_sec=45, cur_gain=cur_gain  # 하드코딩
)

# Line 1893
new_armed, just_armed = _mon_check_trail_arm_condition(
    cur_price, entry_price, dyn_checkpoint=0.008, trail_armed=trail_armed  # 하드코딩
)
```

**권장**: `config.py`로 이동
```python
# config.py에 추가
HARD_STOP_DD = 0.02
WARMUP_SEC = 10
MIN_PEAK_UPDATES = 2
NO_PEAK_TIMEOUT_SEC = 45
DYN_CHECKPOINT = 0.008
```

---

### 3.3 MEDIUM - 포괄적 예외 처리

**위치**: `bot/indicators.py:11-13, 32-34, 53-54, 96-97, 169`

**현황**:
```python
# indicators.py:11-13
try:
    newest_ts = max(t["timestamp"] for t in ticks)
except Exception:
    return {"cv": None, "count": 0}
```

**권장**: 특정 예외만 캐치
```python
try:
    newest_ts = max(t["timestamp"] for t in ticks)
except (KeyError, ValueError, TypeError) as e:
    logging.debug(f"inter_arrival_stats: {e}")
    return {"cv": None, "count": 0}
```

---

### 3.4 LOW - print 대신 logging 사용

**위치**: `bot/__init__.py:1932`

**현황**:
```python
logging.info(f"[모니터] Probe→Confirm 전환: {upgrade_reason}")
```

**상태**: ✅ 이미 logging 사용 중 (양호)

하지만 모듈 최상단에 로거 설정이 없음:
```python
# 권장: __init__.py 상단에 추가
logger = logging.getLogger(__name__)
```

---

### 3.5 LOW - 타입 힌트 불완전

**위치**: 헬퍼 함수 내부 변수

**현황**: 함수 시그니처에는 타입 힌트가 있지만, 내부 변수에는 없음

**권장**: 복잡한 함수의 중간 변수에 타입 주석 추가
```python
def _calc_effective_thresholds(...) -> Dict[str, float]:
    relax: float = _calc_ignition_relax(ignition_score)
    eff_surge_min: float = max(relax_surge_floor, gate_surge_min * (1 - relax))
    ...
```

---

### 3.6 LOW - 테스트 커버리지 확장 필요

**현황**: `test_core_functions.py`가 유틸리티/지표 함수만 테스트

**누락된 테스트**:
- `ignition_detected()` 메인 함수
- `stage1_gate()` 메인 함수
- `detect_leader_stock()` 메인 함수
- `monitor_position()` 메인 함수

**권장**: 메인 함수별 통합 테스트 추가
```python
# test_main_functions.py (새 파일)
def test_ignition_detected_basic():
    ticks = _make_test_ticks(burst=True)
    candles = _make_test_candles()
    is_ign, score, reason = ignition_detected("KRW-BTC", ticks, candles)
    assert score >= 0 and score <= 4
```

---

## 4. 아키텍처 분석

### 4.1 데이터 흐름

```
┌──────────────────────────────────────────────────────┐
│                   입력 데이터                         │
│  • ticks: List[Dict] - 실시간 체결 데이터             │
│  • candles: List[Dict] - 1분봉 캔들                   │
│  • ob: Dict - 오더북 스냅샷                           │
└────────────────────────┬─────────────────────────────┘
                         │
     ┌───────────────────┼───────────────────┐
     ▼                   ▼                   ▼
┌─────────┐      ┌──────────────┐      ┌───────────┐
│ignition │      │ indicators.py│      │ utils.py  │
│detected │      │ - CV 계산    │      │ - _pct()  │
│ 4조건   │      │ - PSTD 계산  │      │ - fmt6()  │
└────┬────┘      │ - micro_tape │      └───────────┘
     │           └──────────────┘
     ▼
┌─────────────────────────────────────────────────────┐
│                  stage1_gate()                       │
│  • 19개 필터 순차 검증                               │
│  • 동적 임계치 계산 (점화 점수 + 가격대 기반)        │
│  • 메트릭 딕셔너리 반환                              │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│              detect_leader_stock()                   │
│  • 스테이블코인/포지션 필터                          │
│  • 레짐 판정 (횡보장 제외)                           │
│  • 진입 모드 결정 (confirm/half/probe)               │
│  • 신호 태그 결정                                    │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│               monitor_position()                     │
│  • 13개 청산 조건 (손절/스크래치/트레일링)           │
│  • 2개 부분익절 조건 (MFE/Plateau)                   │
│  • Probe → Confirm 자동 전환                         │
└─────────────────────────────────────────────────────┘
```

### 4.2 설정 의존성 분석

```
config.py
    │
    ├── GATE_* (19개) ──→ stage1_gate()
    │
    ├── IGN_* (7개) ───→ ignition_detected()
    │
    ├── MFE_* (2개) ───→ monitor_position()
    │
    └── DYN_SL_* (3개) ─→ monitor_position()
```

---

## 5. 보안 분석

### 5.1 코드 내 민감 정보 - **없음** ✅
- API 키, 토큰 등이 코드에 하드코딩되어 있지 않음
- 환경변수 또는 외부 설정 파일 사용 추정

### 5.2 입력 검증 - **양호** ✅
- 틱/캔들 데이터의 None 체크
- 가격/볼륨의 0 체크
- `.get()` 메서드로 KeyError 방지

### 5.3 주입 공격 가능성 - **낮음** ✅
- 외부 입력이 거래소 API 데이터로 제한
- SQL/커맨드 인젝션 벡터 없음

---

## 6. 성능 분석

### 6.1 시간 복잡도

| 함수 | 복잡도 | 설명 |
|------|--------|------|
| `ignition_detected` | O(n) | n = 틱 개수 |
| `stage1_gate` | O(1) | 고정 개수 체크 |
| `detect_leader_stock` | O(n + m) | n = 틱, m = 캔들 |
| `monitor_position` | O(1) | 고정 조건 체크 |

### 6.2 메모리 사용

- 틱 윈도우: 최대 10초분 (약 수백 개)
- 캔들: 최대 30개
- 포지션 상태: Dict (수 KB)

**평가**: 실시간 트레이딩에 적합한 경량 구조 ✅

---

## 7. 테스트 실행 결과

```bash
$ python test_core_functions.py

==================================================
Tests: 19 total, 19 passed, 0 failed
==================================================

All tests passed!
```

**테스트 커버리지**:
- `_pct()`: 4 케이스 ✅
- `inter_arrival_stats()`: 4 케이스 ✅
- `price_band_std()`: 4 케이스 ✅
- `micro_tape_stats()`: 4 케이스 ✅
- None 안전성: 3 케이스 ✅

---

## 8. 최근 개발 이력 (Git Log 분석)

```
58fb820 fix: f-string 조건부 포맷팅 구문 오류 수정
552c859 refactor: 분리된 모듈 함수들을 __init__.py에 직접 통합
0144f81 feat: 메인 함수들 통합 — ignition/gate/detect/monitor 메인코드 완성
5c663d3 refactor: 메인 함수들을 독립 모듈로 분리
ad178f5 feat: 메인 코드 전면 임계치 완화 + 버그 방어 추가
```

**트렌드 분석**:
1. 모듈 분리 → 통합 → 최적화 흐름
2. 임계치 완화 추세 (거래 진입 조건 완화)
3. 버그 방어 강화 (None 체크, KeyError 방지)

---

## 9. 종합 평가

### 점수 (10점 만점)

| 항목 | 점수 | 설명 |
|------|------|------|
| **코드 구조** | 8/10 | 모듈화 양호, 파일 크기 개선 여지 |
| **가독성** | 8/10 | 명확한 함수명, docstring 존재 |
| **유지보수성** | 7/10 | 설정 중앙화, 테스트 확장 필요 |
| **안정성** | 8/10 | 에러 방어 양호, 예외 처리 개선 여지 |
| **성능** | 9/10 | 실시간 트레이딩에 적합 |
| **보안** | 9/10 | 민감 정보 노출 없음 |

**종합 점수: 8.2/10** - 양호 (Good)

---

## 10. 권장 액션 (우선순위별)

### 즉시 (Quick Wins)
1. ✅ 테스트 실행 확인 - 완료
2. ⬜ `config.py`에 하드코딩된 값 이동 (3.2절)
3. ⬜ `except Exception` → 특정 예외로 변경 (3.3절)

### 단기 (1-2주)
1. ⬜ 메인 함수 단위 테스트 추가
2. ⬜ 로거 설정 추가 (`logging.getLogger(__name__)`)

### 중기 (1개월)
1. ⬜ `__init__.py` 기능별 분리 (3.1절)
2. ⬜ 타입 힌트 완성 (내부 변수 포함)

---

## 11. 이전 리뷰 대비 개선 현황

| 이전 이슈 | 현재 상태 |
|----------|----------|
| 단일 파일 7,600줄 | ✅ 4개 모듈로 분리 (총 ~2,500줄) |
| 50+ 전역 변수 | ✅ 대부분 제거, config.py로 통합 |
| 함수 과대화 (600줄) | ✅ 헬퍼 함수로 분리 |
| 예외 무시 패턴 | ⚠️ 일부 남아있음 (indicators.py) |
| 매직 넘버 산재 | ✅ config.py로 대부분 통합, 일부 남음 |
| 데드 코드 | ✅ 레거시 파일로 이동 |
| 테스트 부재 | ✅ 단위 테스트 19개 추가 |

---

*Generated by Claude Code (Opus 4.5) - 2026-02-04*
