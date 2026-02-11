# 브랜치 `claude/compare-code-branches-clL0Q` 전체 코드 점검 리포트

**점검일**: 2026-02-11
**대상 파일**: `251130-MADEIT에서 여러수정보완한 쪠쪠쪠이야야` (8,335줄)
**커밋 수**: 45개 (main 대비)

---

## 1. 전체 구조 요약

| 영역 | 라인(대략) | 설명 |
|------|-----------|------|
| 임포트/설정/전역변수 | 1~500 | 설정값, 수수료, 프로파일, 락 시스템 |
| 업비트 Private API | 500~900 | 인증, 주문, 잔고조회, 하이브리드매수 |
| 유령 포지션 동기화 | 900~1200 | orphan 탐지, 재모니터링 등록 |
| 자동 매수/포지션 오픈 | 1200~1800 | open_auto_position, 임팩트캡, 체결 재검증 |
| 청산/close_auto_position | 1800~2400 | 전량/부분 청산, 찌꺼기 청소, 순손익 계산 |
| safe_partial_sell | 2400~2700 | 부분청산 공용함수, 브레이크이븐 |
| remonitor_until_close | 2700~3000 | 재모니터링, 타임스탑, 컨텍스트 청산 |
| 설정 상수 (트레이드) | 3000~3200 | 손절, ATR, GATE, 봇감지 파라미터 |
| 자동학습/통계 | 3200~3800 | trade_log CSV, 승패분석, 가중치 조절 |
| 텔레그램 알림 | 3800~4100 | tg_send, 실패큐, 중간알림, 헬스체크 |
| HTTP 세션/스로틀 | 4100~4300 | 토큰버킷, 세션 리프레시, 워치독 |
| 틱/캔들 분석 | 4300~5200 | micro_tape, 점화감지, ATR, 봇감지 |
| stage1_gate (진입필터) | 5200~6200 | 스코어 가중점수제, 스캘프/러너 분류 |
| monitor_position | 6200~7500 | 트레일링, 디바운스, 부분익절, 추매 |
| main 스캔루프 | 7500~8335 | 메인루프, 신호발송, 자동매수, 모니터 스레드 |

---

## 2. 양호한 점 (잘 되어 있는 부분)

### 2-1. 락/동시성 설계
- **이중 락 시스템** (파일락 + 메모리락): 프로세스 간/스레드 간 중복진입 방지가 체계적
- `_CLOSING_MARKETS` set으로 중복 청산 방지
- `mark_position_closed()` 원자적 상태 마킹
- `_STREAK_LOCK`으로 streak 카운터 스레드 안전
- `entry_lock` 컨텍스트 매니저로 안전한 락 해제
- `reentrant=True` 모드에서 소유자(thread ident) 추적

### 2-2. API 안정성
- 429/5xx 자동 재시도 (지수 백오프, 최대 3회)
- ConnectionError 별도 처리
- 토큰 버킷 레이트 리미터 (`_throttle`)
- 세션 자동 리프레시 (`_refresh_session`)

### 2-3. 수수료/슬리피지 처리
- entry/exit 슬리피지 분리 추적 (`_ENTRY_SLIP_HISTORY`, `_EXIT_SLIP_HISTORY`)
- trimmed mean으로 이상치 제거
- 왕복/편도 수수료 명확 구분 (`FEE_RATE_ROUNDTRIP`, `FEE_RATE_ONEWAY`)
- net 기준 통일 (gross/net 혼용 해결)

### 2-4. 에러 핸들링
- 체결 0일 때 잔고 재검증 (8회 재시도)
- `get_balance_with_locked` -1 반환으로 조회실패와 잔고0 구분
- 모니터 스레드 예외 시 잔고 확인 후 조건부 청산
- 찌꺼기(dust) 방지: 잔량 < 6000원 시 전량청산 전환

### 2-5. 자동학습
- 베이지안 스무딩으로 샘플 수 기반 블렌딩 강도 조절
- 래칫(ratchet) 방식으로 임계치 큰 변동 방지
- 연패 게이트/진입 중지 시스템

---

## 3. 발견된 문제점

### 3-1. 크리티컬 (운영 장애 가능)

#### [C1] `cleanup_stale_entry_locks` TTL 불일치
- **위치**: `cleanup_stale_entry_locks(max_age_sec=300)` 기본 5분
- **문제**: `_try_acquire_entry_lock` 기본 TTL은 60초인데, cleanup은 300초. 정상 작동 중인 락을 삭제할 수 있음
- **영향**: 모니터링 중인 포지션의 락이 삭제되어 중복진입 발생 가능
- **권장**: `_try_acquire_entry_lock`의 TTL을 300초로 통일하거나, cleanup TTL을 락 생성 시 쓴 TTL과 연동

#### [C2] `monitor_position`에서 `_get_c1_cached()` 캐시 stale 위험
- **문제**: `_c1_cache_ts` 기반 캐시인데, 모니터 루프에서 매 반복마다 호출. 캐시 만료 시간이 명확하지 않음
- **영향**: stale 데이터로 잘못된 손절/익절 판단 가능

#### [C3] `open_auto_position`에서 `'avg_price' not in locals()` 체크
- **위치**: 체결 후 평균가 계산 블록
- **문제**: `locals()` 기반 체크는 불안정 (Python 최적화에 따라 동작이 달라질 수 있음)
- **권장**: 명시적 플래그 변수 사용 (예: `avg_price = None` 초기화 후 `if avg_price is None`)

#### [C4] 메인 루프 `except` 들여쓰기 이상
- **위치**: 8200줄 부근 `except Exception as e:` (심볼별 예외처리)
- **관찰**: 들여쓰기가 `for m in sorted_filtered` 블록의 `try`와 맞지 않아 보이는 구간 존재
- **영향**: 특정 예외가 상위 try로 bubbling되어 전체 스캔루프 중단 가능
- **권장**: 실행 테스트로 들여쓰기 정합성 확인 필수

### 3-2. 하이 (기능 오동작 가능)

#### [H1] `sync_orphan_positions`에서 datetime import 중복
- **위치**: 함수 내부에서 `from datetime import datetime, timezone, timedelta` 재임포트
- **문제**: 상단에서 이미 임포트됨. 불필요하며 미세한 성능 저하
- **영향**: 동작에는 문제없지만 코드 정리 필요

#### [H2] `_apply_exit_profile` strict에서 `TRAIL_DISTANCE_MIN` 덮어쓰기
- **위치**: strict 프로파일 `TRAIL_DISTANCE_MIN = 0.002`
- **문제**: `get_trail_distance_min()` 함수는 BASE 기준으로 동작하지만, 전역 `TRAIL_DISTANCE_MIN`도 변경됨. 하위호환용 변수가 프로파일에 의해 변경되면 혼동
- **영향**: `get_trail_distance_min()` 사용처와 직접 `TRAIL_DISTANCE_MIN` 사용처가 다른 값을 참조할 수 있음

#### [H3] `TRADE_HISTORY` deque alias 불필요
- **위치**: `from collections import deque as _deque_for_risk`
- **문제**: 상단에서 이미 `from collections import deque` 임포트. 별도 alias 불필요
- **영향**: 코드 가독성 저하

#### [H4] `partial_state` "done" 후 타임스탑 기준 혼동
- **위치**: `remonitor_until_close` 부분익절 후 타임스탑
- **문제**: `partial_ts`가 "done" 시점으로 기록되는데, 이것이 부분청산 체결 시점인지 주문 시점인지 불명확
- **영향**: 타임스탑 판정이 수초~수십초 어긋날 수 있음

#### [H5] `sell_all` 예외 시 리턴값 None
- **문제**: `sell_all`이 예외 시 None 반환. 호출부에서 None 체크 없이 사용하면 오류 가능

### 3-3. 미디엄 (개선 권장)

#### [M1] 단일 파일 8335줄 — 모듈 분리 필요
- API 통신, 텔레그램, 매매 로직, 분석 등이 한 파일에 있어 유지보수 어려움
- 최소 4개 모듈로 분리 권장: `api.py`, `telegram.py`, `trading.py`, `analysis.py`

#### [M2] 전역 변수 과다 사용
- `OPEN_POSITIONS`, `_MEMORY_ENTRY_LOCKS`, `_CLOSING_MARKETS` 등 가변 전역 상태가 많음
- 클래스로 캡슐화하면 상태 관리가 더 안전해짐

#### [M3] `rnd()` 함수 이름 모호
- `random.random()`의 래퍼인데 이름만으로 용도 파악 불가
- 스로틀 지터용으로만 사용됨 → `_jitter()` 등으로 변경 권장

#### [M4] 하위호환 코드 잔재
- `_SLIP_HISTORY`, `FEE_RATE`, `TRAIL_DISTANCE_MIN`, `PROFIT_CHECKPOINT` 등 하위호환용 변수가 실제로 참조되는지 확인 필요
- 참조처가 없으면 제거하여 혼동 방지

#### [M5] CSV 로깅의 동시성
- `_CSV_LOCK`으로 보호되지만, 파일 I/O 에러 시 데이터 유실 가능
- 버퍼링 또는 큐 기반 비동기 로깅 고려

#### [M6] `validate_config()` 내용 미확인
- 호출만 있고 본문을 확인하지 못함 (라인 범위 밖이거나 정의 위치 불명)
- 설정값 범위 검증이 충분한지 확인 필요

### 3-4. 로우 (미관/스타일)

#### [L1] 주석 이모지 과다
- 🔧, 🔥, 🚀, 💥 등 이모지가 수백 개. 검색/grep 시 노이즈
- 카테고리별 prefix (`FIX:`, `FEAT:`, `CRITICAL:`) 텍스트만으로도 충분

#### [L2] 한/영 혼용 변수명
- 대부분 영문이지만 주석은 한국어. 일관성은 괜찮음
- 단, `signal_skip`, `cut` 등 일부 내부 함수가 외부에서 호출 가능한 구조 → docstring 보강 권장

---

## 4. 동시성/스레드 안전성 종합 평가

| 항목 | 상태 | 비고 |
|------|------|------|
| 포지션 접근 (`_POSITION_LOCK`) | 양호 | 일관되게 사용됨 |
| 엔트리 락 (`_MEMORY_LOCK` + 파일락) | 양호 | reentrant 소유자 추적 포함 |
| 모니터 스레드 중복 방지 (`_MONITOR_LOCK`) | 양호 | is_alive() 체크 + 정리 로직 |
| Streak 카운터 (`_STREAK_LOCK`) | 양호 | 읽기/쓰기 모두 보호 |
| 청산 중복 방지 (`_CLOSING_MARKETS`) | 양호 | partial_sell, close 모두 체크 |
| 락 해제 보장 | 주의 | 대부분 finally 사용하나, 일부 경로에서 누락 가능성 (C1 참조) |
| cleanup TTL 불일치 | 문제 | C1 참조 |

---

## 5. 보안 점검

| 항목 | 상태 | 비고 |
|------|------|------|
| API 키 노출 | 양호 | .env + 환경변수, DEBUG_KEYS 조건부 로깅 |
| JWT 토큰 생성 | 양호 | HS256 + PyJWT 호환 |
| 헬스서버 접근 | 미확인 | `start_health_server()` 바인드 주소/포트 확인 필요 |
| 파일락 경로 | 주의 | `/tmp/` 사용 → 다른 사용자가 심링크 공격 가능 (로컬 전용이면 OK) |

---

## 6. 종합 판정

| 카테고리 | 등급 | 설명 |
|----------|------|------|
| 기능 완성도 | **B+** | 진입→모니터→청산→학습 파이프라인 완성. 부분청산, 추매, 리테스트 등 고급 기능 포함 |
| 에러 처리 | **B+** | API 재시도, 잔고 재검증, 찌꺼기 방지 등 실전 경험 기반의 방어 코드 충실 |
| 동시성 안전 | **B** | 전반적으로 락 사용이 체계적이나, TTL 불일치(C1) 해결 필요 |
| 코드 품질 | **C+** | 단일 파일 8300줄, 전역변수 과다. 기능은 동작하나 유지보수 비용 높음 |
| 보안 | **B** | API 키 관리 양호. 헬스서버/파일락 경로 점검 권장 |

### 우선 수정 권장 순서:
1. **[C1]** 락 TTL 불일치 수정 (중복진입 버그 방지)
2. **[C3]** `locals()` 기반 체크를 명시적 변수로 교체
3. **[C4]** 메인 루프 들여쓰기 정합성 검증
4. **[H2]** TRAIL_DISTANCE_MIN 프로파일 덮어쓰기 정리
5. **[M1]** 장기적으로 모듈 분리 계획 수립
