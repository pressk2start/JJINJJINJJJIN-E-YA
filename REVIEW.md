# bot.py 코드 리뷰 (v3.2.7+Score)

**파일**: `bot.py` (12,017 lines)
**리뷰 일자**: 2026-02-25
**리뷰어**: Claude Code Review

---

## 1. 아키텍처 요약

### 1.1 전체 흐름

```
main() → while True 루프
  ├── 헬스체크/하트비트
  ├── pending 타임아웃 정리
  ├── 유령포지션 동기화 (sync_orphan_positions)
  ├── 리테스트 워치리스트 체크
  ├── 동그라미 워치리스트 체크
  ├── 박스권 매매 체크
  └── 메인 스캔 루프 (shard별)
       ├── detect_leader_stock()  → 1단계 필터 + stage1_gate
       ├── final_check_leader()   → 리스크 스코어 + entry_mode 결정
       ├── postcheck_6s()         → 6초 확인
       ├── open_auto_position()   → 매수 실행
       └── monitor_position()     → 별도 스레드에서 포지션 관리
```

### 1.2 진입 시스템 (3단계)

| 단계 | 함수 | 위치 | 역할 |
|------|------|------|------|
| Pre-filter | `detect_leader_stock()` | ~L8100-8642 | 캔들/볼륨/트렌드/RSI 하드컷 |
| Gate | `stage1_gate()` | L7642-7979 | 3-Phase 통합 게이트 |
| Score | `final_check_leader()` | L8644-8833 | 리스크 스코어 → entry_mode |

### 1.3 진입 경로 (9가지 signal_type)

| Signal | 경로 | R:R 배수 |
|--------|------|----------|
| 🔥점화 | 독립 (gate_score 무관) | 1.8x |
| 강돌파 (EMA↑+고점↑) | 독립 | 1.5x |
| 🕯️캔들돌파 | 독립 | 기본 |
| EMA↑ | gate_score ≥ 75 | 1.4x |
| 고점↑ | gate_score ≥ 75 | 1.4x |
| 거래량↑ | gate_score ≥ 75 | 1.35x |
| 기본 | gate_score ≥ 75 | 1.35x |
| 리테스트 | 워치리스트 기반 | - |
| 동그라미 | 워치리스트 기반 | - |

### 1.4 엑시트 시스템

| 구성요소 | 위치 | 역할 |
|----------|------|------|
| `dynamic_stop_loss()` | L9206-9273 | ATR 기반 동적 SL (2.0~3.5%) |
| `monitor_position()` | L9554-10502 | 메인 포지션 감시 루프 |
| Hard Stop | L9814 | SL × 1.5 초과 시 즉시 청산 |
| ATR 손절 | L9821-9919 | 디바운스 + 컨텍스트 체크 |
| Trail Stop | L10022-10218 | 체크포인트 기반 트레일링 |
| MFE TP | L10281-10409 | 스캘프/러너별 목표수익 청산 |
| 시간만료 | L10418-10500 | 호라이즌 만료 시 손익 기반 처리 |

---

## 2. 긍정적 요소

### 2.1 데이터 기반 의사결정
- 180신호분석, 172샘플 다중타임프레임 분석 등 실제 트레이딩 데이터를 기반으로 임계치를 조정하고 있음
- RSI × 거래량 조합별 승률 통계가 V7 차트분석 로직에 반영 (L8403-8489)
- GATE 상수마다 데이터 근거가 주석으로 잘 문서화됨 (L3700-3748)

### 2.2 방어적 프로그래밍
- 18개 이상의 전용 Lock 객체로 스레드 안전성 확보
- `_POSITION_LOCK` 내부에서만 `OPEN_POSITIONS` 조작하는 패턴 일관 유지
- 유령 포지션 동기화 (`sync_orphan_positions`), pending 타임아웃 등 엣지케이스 처리
- 파일 락 + 메모리 락 이중 구조로 프로세스 간 중복 진입 방지

### 2.3 꼼꼼한 버그 수정 이력
- 코드 전반에 `🔧 FIX:` 주석으로 수정 이유와 사례가 상세히 기록됨
- 과거 버그의 원인 코인명(AZTEC, HOLO, STEEM 등)과 구체적 수치가 함께 문서화

### 2.4 다중 안전장치
- 하드스톱(SL×1.5), 디바운스(N회+시간), 컨텍스트 체크(수급확인) 3단계 손절
- VWAP 추격 차단, EMA 이격 차단, 연속양봉 과열 등 꼭대기 진입 방지 레이어 다수

---

## 3. 식별된 이슈

### 3.1 [HIGH] 단일 파일 12,000줄 — 유지보수 리스크

**현상**: 모든 로직(API, 진입, 엑시트, 텔레그램, 학습, 유틸리티)이 `bot.py` 한 파일에 존재
- 관련 함수 간 거리가 수천 줄 (예: GATE 상수 L3700 ↔ stage1_gate L7642)
- 코드 변경 시 의도치 않은 사이드이펙트 발견 어려움
- IDE 탐색 및 검색 성능 저하

**권장**: 모듈 분리 (config.py, entry.py, exit.py, api.py, telegram.py, utils.py)

### 3.2 [HIGH] 전역 변수 과다 사용

**현상**: `OPEN_POSITIONS`, `_ACTIVE_MONITORS`, `last_signal_at`, `last_price_at_alert`, `_COIN_LOSS_HISTORY`, `TRADE_HISTORY`, `_lose_streak`, `_win_streak`, `_ENTRY_SUSPEND_UNTIL` 등 20개 이상의 전역 가변 상태

**위험**:
- 스레드 간 공유 상태가 많아 Lock 네스팅 실수 가능성 증가
- 테스트 작성 시 전역 상태 초기화 필요 → 단위테스트 사실상 불가
- 함수의 순수성이 낮아 디버깅 어려움

**권장**: 상태를 클래스로 캡슐화 (예: `TradingState`, `PositionManager`)

### 3.3 [MEDIUM] calc_consecutive_buys 중복 호출

**현상**: 동일 tick 데이터에 대해 `calc_consecutive_buys()`가 여러 위치에서 반복 호출됨
- `detect_leader_stock()` L8248
- `stage1_gate()` 내부 전달
- `main()` 루프 L11568 (killer 조건)
- `main()` 루프 L11736 (텔레그램 메시지)
- `snapshot_row()` L8883

**영향**: 동일 데이터를 최소 3~5회 반복 계산 (CPU 낭비)
**권장**: `detect_leader_stock()` 결과를 `pre` dict에 캐시하여 재사용

### 3.4 [MEDIUM] Lock 순서 미명시 — 데드락 가능성

**현상**: `_POSITION_LOCK`, `_MONITOR_LOCK`, `_CIRCLE_LOCK`, `_RETEST_LOCK` 등이 코드 위치에 따라 다른 순서로 중첩 가능

**예시**:
- L11068: `_POSITION_LOCK` → 내부에서 `_release_entry_lock` 호출 가능
- L11200: `_CIRCLE_LOCK` 후 L11210에서 `_POSITION_LOCK`
- L11101: `_POSITION_LOCK` 후 다른 로직

**위험**: 두 스레드가 서로 다른 순서로 락을 잡으면 데드락 발생 가능
**권장**: 락 순서 문서화 및 강제 (예: POSITION → MONITOR → CIRCLE → RETEST)

### 3.5 [MEDIUM] record_trade() 단위 자동 변환 로직

**위치**: L624-628

```python
if abs(pnl_pct) > 10.0:
    pnl_pct = pnl_pct / 100.0  # 자동 변환
elif abs(pnl_pct) > 1.0:
    print(f"... 100%+ 수익? 호출부 단위 재확인")
```

**위험**: 자동 단위 변환은 실제 100%+ 수익과 % 단위 오류를 구분하지 못함. 10.0 임계치도 여전히 모호 (10% 수익 vs 10.0% 단위 오류).
**권장**: 호출부에서 단위를 통일하고 자동 변환 제거. 명시적 `pnl_decimal` / `pnl_percent` 파라미터 사용.

### 3.6 [MEDIUM] `except Exception: pass` 패턴

**현상**: 다수의 예외 처리에서 `pass`로 무시
- L164, L375, L398, L420, L437, L968 등

**위험**: 실제 오류를 묵인하여 디버깅 어려움 증가
**권장**: 최소한 로깅 추가 (`print` 또는 `logging.warning`)

### 3.7 [HIGH] 보호되지 않은 공유 Dict 접근 — 레이스 컨디션

**위치**: L11714-11716, L11646-11649, L11655-11657

**현상**: `last_signal_at`, `recent_alerts`, `last_price_at_alert`, `last_reason` 딕셔너리가 Lock 없이 여러 스레드에서 읽기/쓰기됨
- L11714: `_POSITION_LOCK` 안에서 쓰기 수행하지만, 정리(pop)는 L11657/11695에서 락 밖에서 수행
- 모니터 스레드와 스캔 루프가 동시에 접근 가능

**위험**: 메모리 누수 또는 중복 진입 허용
**권장**: 이 딕셔너리들도 `_POSITION_LOCK` 또는 전용 Lock으로 보호

### 3.8 [HIGH] `_RECENT_BUY_TS` Lock 미보호

**위치**: L585 (정의), L2384 (쓰기), L1284 (읽기)

**현상**: `_RECENT_BUY_TS = {}` 에 전용 Lock이 없음
- `open_auto_position()`에서 쓰기 (L2384)
- `sync_orphan_positions()`에서 읽기 (L1284)
- 서로 다른 스레드에서 동시 접근

**위험**: 유령 포지션 오탐지 가능 (매수 직후 잔고=0을 API 지연으로 오판)
**권장**: `_POSITION_LOCK` 안에서 접근하거나 전용 Lock 추가

### 3.9 [MEDIUM] `CUT_COUNTER` Lock 미보호

**위치**: L1531 (increment), L11471 (reset)

**현상**: `CUT_COUNTER[reason] = CUT_COUNTER.get(reason, 0) + 1` 이 Lock 없이 수행되고, 메인 루프에서 `CUT_COUNTER[k] = 0` 리셋도 Lock 없음

**위험**: TOCTOU 레이스 — 카운터가 리셋 중 증분되면 값 손실
**영향**: 통계 정확도만 영향 (기능적 영향 없음)

### 3.10 [MEDIUM] 오더북 캐시 30초+ 스테일

**위치**: L11504

**현상**: `fetch_orderbook_cache(shard)` 호출 후 shard 내 30개+ 마켓 처리 동안 오더북이 갱신되지 않음
- 각 마켓 처리에 ~1초 소요 시 마지막 마켓은 30초+ 된 오더북으로 판단

**위험**: 유동성 오판, 잘못된 depth_krw 기반 진입/회전율 계산
**권장**: 진입 직전에 개별 마켓 오더북 재조회 또는 캐시 TTL 적용

### 3.11 [LOW] 점화 감지 window의 고정 시간 (10초)

**위치**: `ignition_detected()` L6016-6125

**현상**: TPS burst, consecutive buys, price impulse, volume burst 모두 10초 윈도우 고정
**관찰**: 알트코인 특성상 유동성에 따라 10초가 너무 짧거나 길 수 있음
**권장**: 메이저/알트 구분 또는 유동성 기반 동적 윈도우 검토

### 3.12 [LOW] `rnd = _jitter` alias (L27)

**현상**: `_jitter` 함수의 alias `rnd`가 정의되어 있으나 사용처 불명
**권장**: 사용되지 않으면 제거

### 3.13 [LOW] `import traceback` 인라인 중복

**위치**: L545, L11926 등

**현상**: 파일 상단에서 이미 `import traceback` (L2)를 수행했으나, 함수 내부에서 다시 `import traceback` 수행
**영향**: 성능 영향은 없으나 (캐시됨) 코드 정리 필요

---

## 4. 아키텍처 관찰 사항

### 4.1 진입 필터 누적 현상

Stage1 Gate만 해도 다음 레이어가 순차 적용됨:
1. Pre-filters (detect_leader_stock 내부): VOL_PEAK_OUT, FAKE_FLOW_HARD, VOL_SURGE_LOW, TREND_DOWN, BODY_TOO_SMALL, NO_WICK, WEAK_SIGNAL, GREEN_OVERHEAT, SIDEWAYS_BLOCK, NIGHT_BLOCK, COIN_LOSS_CD
2. Stage1 Gate (9 하드컷 + 4-카테고리 점수 + 3 독립경로)
3. Post-gate: BTC_HEADWIND, VWAP_CHASE
4. Final Check: SCORE_CUT, IMB_CUT + 6가지 다운그레이드
5. Killer 조건 (6/6 체크 → 가산점)
6. 6초 포스트체크
7. 가격 가드 (final_price_guard)

총 **40개 이상**의 필터/조건이 진입 전에 적용됨. 이는 높은 정밀도를 제공하지만:
- 각 필터 간 상호작용 예측이 어려움
- 하나의 필터 변경이 전체 진입률에 미치는 영향을 시뮬레이션하기 어려움
- 진입 기회 극도로 제한될 수 있음

### 4.2 스캘프/러너 결정 타이밍

진입 시점에 `trade_type` (scalp vs runner)을 결정하지만 (L8792-8813), 실제로는 포지션 관리 중에 스캘프→러너 전환이 발생할 수 있음 (L10310-10326 scalp TP 시점에서 모멘텀 확인 후 runner 전환). 이 이중 결정 구조가 의도된 것인지 확인 필요.

### 4.3 V7 차트분석의 entry_mode 오버라이드 전달 경로

V7 분석 결과 (`_entry_mode_override`)가 다음 경로를 거침:
1. `detect_leader_stock()` L8415-8489 → `pre["_entry_mode_override"]` 설정
2. `final_check_leader()` L8715-8721 → `entry_mode` 조건부 변경
3. `main()` L11632 → `pre["entry_mode"]` 최종 복사

이 3단계 전달에서 중간에 다른 다운그레이드(FLAT, DECAY, 강돌파 등)가 V7 오버라이드를 다시 덮어쓸 수 있음. 우선순위 명확화 필요.

---

## 5. 성능 관련

### 5.1 API 호출 최적화 (이미 잘 됨)
- BTC 캔들 캐시 (L11804-11808): shard당 1회만 조회
- ThreadPoolExecutor로 캔들 병렬 조회 (L11508-11517)
- 오더북 캐시 (`fetch_orderbook_cache`)
- RSI 계산 시 기존 c1 재사용 (L8432)

### 5.2 개선 가능 영역
- `micro_tape_stats_from_ticks()`가 동일 ticks에 대해 여러 윈도우(15/45초)로 반복 호출
- `calc_orderbook_imbalance()`도 여러 위치에서 동일 ob에 대해 재호출
- `dynamic_stop_loss()` 내부에서 5분봉 API 호출 (L9218-9237)이 모니터링 루프에서 반복

---

## 6. 결론

### 강점
- **데이터 기반 튜닝**: 임계치마다 실거래 데이터 근거가 명확
- **방어적 설계**: 다중 Lock, 유령포지션 동기화, pending 타임아웃 등 로버스트한 에러 핸들링
- **단계적 진입 필터**: 하드컷 → 가중점수 → 독립경로 3단계 구조가 체계적

### 개선 필요 (우선순위 순)
1. **스레드 안전성**: `last_signal_at`, `recent_alerts`, `_RECENT_BUY_TS` 등 Lock 미보호 공유 Dict 보호 (레이스 컨디션 제거)
2. **파일 분리**: 12,000줄 단일 파일은 유지보수 한계 근접
3. **전역 상태 캡슐화**: 20+ 전역 변수를 클래스로 관리
4. **Lock 순서 명시**: 데드락 방지를 위한 문서화/강제
5. **중복 계산 제거**: tick 분석 결과 캐싱
6. **오더북 TTL**: 스캔 루프 중 30초+ 된 오더북으로 판단하는 문제 해결
7. **테스트 인프라**: 현재 단위테스트 없음 → 회귀 방지 어려움

---

*이 리뷰는 코드 읽기 기반 정적 분석입니다. 실제 거래 성과에 대한 평가는 포함하지 않습니다.*
