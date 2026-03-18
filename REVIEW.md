# bot.py 코드 리뷰 (v9 — 리포트 기반 임계값 완화)

**파일**: `bot.py` (13,824 lines, ~650KB)
**리뷰 일자**: 2026-03-18
**리뷰어**: Claude Code Review
**이전 리뷰**: 2026-02-25 (v3.2.7+Score, 12,017줄)

---

## 0. v3.2.7 이후 주요 변경 요약 (v7 → v8 → v9)

| 버전 | 핵심 추가사항 |
|------|--------------|
| **v7** | MFE 시계열 추적, 시그널별 성과 통계, MFE→Exit 자동 피드백, 진입 컨텍스트 스냅샷, 상태 영속화, 전략 모듈화 (strategy_v4) |
| **v8** | 섀도우 멀티루트 계측 시스템 (10개 루트 독립 로깅), 가상매매 추적, 파이프라인 계측 (30+ 메트릭), 섀도우 시그널 노이즈 필터링 |
| **v9** | 리포트 기반 임계값 완화, 섀도우 가상매매에 실제 청산 로직(TRAIL) 시뮬레이션 적용, 섀도우 시그널 가상매매 승률/수익률 누적 분석 |

**라인 수 변화**: 12,017줄 → 13,824줄 (+1,807줄, +15%)

---

## 1. 아키텍처 요약

### 1.1 전체 흐름

```
main() (L12812) → while True 루프
  ├── 헬스체크/하트비트 (L12851)
  ├── 상태 영속화 (L12859) ← [v7 신규]
  ├── 섀도우 가상매매 평가 (L12862) ← [v8 신규]
  ├── 텔레그램 실패큐 재시도 (L12865)
  ├── 유령포지션 동기화 (sync_orphan_positions, L1977)
  ├── 리테스트 워치리스트 체크 (L8573)
  ├── 동그라미 워치리스트 체크 (L8976)
  ├── 박스권 매매 체크 (L9522)
  └── 메인 스캔 루프 (shard별)
       ├── detect_leader_stock() (L10178) → 1단계 필터 + stage1_gate
       ├── v4_evaluate_entry() (L8273) ← [v7 신규: 전략 레지스트리]
       ├── postcheck_6s() (L10718) → 6초 확인
       ├── open_auto_position() → 매수 실행
       └── monitor_position() (L11271) → 별도 스레드에서 포지션 관리
```

### 1.2 전략 레지스트리 (v7 신규)

v4 전략 시스템이 도입되어 11+ 전략이 레지스트리로 관리됨:

| 전략 | 함수 | 라인 |
|------|------|------|
| 거래량3배 | `_v4_check_volume_3x()` | L7343 |
| 15m 눌림반전 | `_v4_check_15m_pullback_reversal()` | L7402 |
| 20봉 고점돌파 | `_v4_check_20bar_breakout()` | L7430 |
| EMA 정렬 | `_v4_check_ema_alignment()` | L7487 |
| 15m MACD+1h EMA | `_v4_check_15m_macd_1h_ema()` | L7571 |
| 모멘텀 스캘프 | `_v4_check_momentum_scalp()` | L7586 |
| 60m 장악형 | `_v4_check_60m_engulfing()` | L7628 |
| 15m VR 폭발 | `_v4_check_15m_vr_explosion()` | L7670 |
| 상위TF 정렬 | `_v4_check_upper_tf_aligned()` | L7709 |
| 과매도 반등 | `_v4_check_oversold_bounce()` | L7760 |
| ADX 추세 | `_v4_check_adx_trend()` | L7797 |

**섀도우 전용 전략** (v8):
- `_v4_shadow_check_ema_alignment()` (L7493) — 본전략보다 완화된 조건
- `_v4_shadow_check_volume_relaxed()` (L7529) — VR5 범위 1.5~2.5

### 1.3 엑시트 시스템 (v9 기준 업데이트)

| 구성요소 | 위치 | 역할 |
|----------|------|------|
| `dynamic_stop_loss()` | L10955 | ATR 기반 동적 SL, WF 오버라이드 |
| `context_exit_score()` | L10995 | 수급 기반 엑시트 강도 (0~5+점) |
| `monitor_position()` | L11271 | 메인 포지션 감시 루프 (800+줄) |
| Hard Stop | 내부 | SL×1.5 초과 → 즉시 청산 |
| SL 디바운스 | 내부 | 2회 이상 + 2초 이상 확인 |
| Supply-check 감량 | 내부 | [v8] 1/3 시그널 생존 시 50% 부분청산 + 20초 관찰 |
| Trail Stop | 내부 | 체크포인트 기반, ATR 스케일링, 래칫 잠금 |
| MFE 피드백 TP | L1443 | [v7] 시그널별 MFE 피크 기반 자동 청산 조정 |
| Plateau 감지 | 내부 | 50% 이상 수익 정체 시 부분청산 |
| 시간만료 | 내부 | 오후 20분 / 기타 30분, 수익/손실 기반 처리 |
| HOLD 전략 | L11999 | [v7] _v4_hold_bars 완료 시 엑시트 |

### 1.4 섀도우 계측 시스템 (v8/v9 신규)

```
v4_evaluate_entry() → 전략 히트
  ├── 실제 진입 경로 → open_auto_position() → monitor_position()
  └── 섀도우 경로 (v8) → _v4_shadow_test_all_routes() (L8165)
       ├── 가상매매 생성 → _SHADOW_VIRTUAL_POSITIONS (L7960)
       ├── 실제 청산 로직 시뮬레이션 → _shadow_sim_exit() (L8047) [v9]
       ├── 성과 기록 → _shadow_record_result() (L8002)
       └── 리포트 생성 → _v4_shadow_report_lines() (L8208)
```

---

## 2. 긍정적 요소

### 2.1 데이터 기반 의사결정 (v3 → v9 강화)
- 180신호분석, 172샘플 다중타임프레임 분석 기반 임계치 조정
- RSI × 거래량 조합별 승률 통계 반영
- GATE 상수마다 데이터 근거 주석 문서화
- **[v7 신규]** MFE 시계열 추적으로 시그널별 최적 청산 시점 데이터 수집
- **[v8 신규]** 섀도우 가상매매로 미진입 시그널의 잠재 성과까지 추적
- **[v9 신규]** 가상매매 승률/수익률 누적 분석 → 임계값 데이터 기반 완화

### 2.2 방어적 프로그래밍 (대폭 개선)
- **20개 이상**의 전용 Lock 객체로 스레드 안전성 확보 (v3: 18개 → v9: 20+)
- **Lock 순서 문서화** (L922-940): MEMORY → POSITION → MONITOR → ORPHAN → RECENT_BUY → STREAK/COIN_LOSS → RETEST/CIRCLE/BOX → CSV/LOG → TG
- `_POSITION_LOCK` 내부에서만 `OPEN_POSITIONS` 조작하는 패턴 일관 유지
- **[v7]** `_RECENT_BUY_LOCK` 추가 (기존 리뷰 3.8 이슈 해결)
- **[v7]** Entry lock reentrant 구조 (owner tracking + TTL, L757-883)
- **[v7]** `_STREAK_LOCK`으로 streak 카운터 보호 (기존 리뷰 관련)
- 유령 포지션 동기화, pending 타임아웃, stale lock 자동 정리

### 2.3 꼼꼼한 버그 수정 이력
- `🔧 FIX:` 주석으로 수정 이유와 사례 상세 기록
- 명시적 FIX 태그: FIX H1(streak race), H2(429 cascade), H3(deque alias), H5(API fail vs no balance), C1(네트워크 retry), M3(jitter), M4(slip 분리), HYBRID_VOL_FIX(부분주문 추적)

### 2.4 다중 안전장치 (v9 기준)
- 하드스톱 → SL 디바운스 → Supply-check 감량 → 컨텍스트 엑시트 4단계 손절
- VWAP 추격 차단, EMA 이격 차단, 연속양봉 과열, 스테이블코인 필터
- **[v8]** Supply-check: 수급 1/3 이상 생존 시 50% 부분청산 + 20초 관찰 (휩소 방지)
- **[v7]** 3연패 → half 모드 전환, 4연패 → 10분 정지, 5연패 → 30분 정지

### 2.5 [신규] 파이프라인 계측 (v8)
- **30+ 메트릭** 추적: scan_markets → c1_ok → detect_called → gate_pass → send_success
- 전략별/시간별/코인별 통과율 기록
- near-miss 추적 (임계값 20% 이내 근접 실패)
- 값 분포 히스토그램 (500건 캐시) — 임계값 튜닝 근거 제공
- 스캔 레이턴시 추적 (200건 deque)

### 2.6 [신규] 상태 영속화 (v7)
- `_save_bot_state()` / `_load_bot_state()`: TRADE_HISTORY, COIN_LOSS, streaks, OPEN_POSITIONS를 JSON으로 주기적 저장
- 원자적 쓰기 (`.tmp` → replace)
- 재시작 시 만료된 cooldown 자동 필터링

---

## 3. 식별된 이슈

### 이전 리뷰(v3.2.7) 이슈 상태

| ID | 이슈 | 상태 | 비고 |
|----|------|------|------|
| 3.1 | 단일 파일 12,000줄 | **악화** | 13,824줄로 증가 (+15%) |
| 3.2 | 전역 변수 과다 | **악화** | 20개 → 30+개 (계측 변수 추가) |
| 3.3 | calc_consecutive_buys 중복 | 미해결 | 여전히 다수 위치에서 반복 호출 |
| 3.4 | Lock 순서 미명시 | **해결** | L922-940에 문서화됨 |
| 3.5 | record_trade() 단위 변환 | 미해결 | 여전히 자동 변환 로직 존재 |
| 3.6 | except Exception: pass | 미해결 | 여전히 다수 존재 |
| 3.7 | 공유 Dict 레이스 컨디션 | **부분해결** | 일부 Lock 추가, 일부 미보호 |
| 3.8 | _RECENT_BUY_TS Lock 미보호 | **해결** | `_RECENT_BUY_LOCK` 추가 (L999) |
| 3.9 | CUT_COUNTER Lock 미보호 | 미해결 | 통계 전용이라 기능 영향 없음 |
| 3.10 | 오더북 캐시 스테일 | 미해결 | 구조적 변경 없음 |
| 3.11 | 점화 윈도우 고정 | 미해결 | 10초 고정 유지 |
| 3.12 | rnd alias 미사용 | 미확인 | 경미한 이슈 |
| 3.13 | import traceback 중복 | 미해결 | 경미한 이슈 |

---

### 3.1 [CRITICAL] 단일 파일 13,824줄 — 유지보수 한계 초과

**현상**: 이전 리뷰에서 12,017줄이었던 bot.py가 13,824줄(+15%)로 증가. 모든 로직이 한 파일에 존재:
- API 래퍼 (L1538-1947)
- 전략 레지스트리 (L7336-7851)
- 섀도우 계측 (L7957-8260)
- 엔트리 탐지 (L8273-10581)
- 포지션 모니터링 (L11271-12017)
- 텔레그램 (L12373-12527)
- 메인 루프 (L12812-13824)
- 파이프라인 계측 (L248-510)

**위험**:
- IDE 탐색/검색 성능 저하 심화
- 관련 함수 간 거리가 수천 줄 (예: 전략 정의 L7343 ↔ 전략 사용 L12849)
- config.py 분리는 시작했으나 런타임 상태는 여전히 모두 bot.py

**권장**: 최소 모듈 분리안:

| 모듈 | 대상 | 예상 라인 |
|------|------|-----------|
| `api.py` | Upbit API 래퍼, 인증, 주문 | ~500 |
| `strategy_v4.py` | 전략 레지스트리, v4 체크 함수 | ~600 |
| `shadow.py` | 섀도우 계측, 가상매매 | ~350 |
| `entry.py` | detect_leader_stock, stage1_gate, postcheck | ~800 |
| `exit.py` | monitor_position, dynamic_stop_loss, trail | ~900 |
| `telegram_bot.py` | tg_send, flush, handlers | ~200 |
| `pipeline.py` | 파이프라인 메트릭, 리포트 | ~300 |
| `state.py` | 상태 영속화, 시그널 통계 | ~200 |

### 3.2 [HIGH] 전역 가변 상태 30+개 — 테스트 불가

**현상**: v7/v8/v9 추가로 전역 상태가 대폭 증가:

**기존 (20개)**:
- `OPEN_POSITIONS`, `_ACTIVE_MONITORS`, `TRADE_HISTORY`, `_lose_streak`, `_win_streak`
- `_COIN_LOSS_HISTORY`, `_ENTRY_SUSPEND_UNTIL`, `_RECENT_BUY_TS`, `_ORPHAN_HANDLED`
- `last_signal_at`, `recent_alerts`, `last_price_at_alert`, `last_reason`

**v7/v8 추가 (~15개)**:
- `_SIGNAL_STATS` (시그널 통계)
- `_PIPELINE_COUNTERS`, `_PIPELINE_COIN_HITS`, `_PIPELINE_STRATEGY_PASS` (계측)
- `_PIPELINE_HOURLY_SIGNALS/GATE_PASS/SUCCESS` (시간별 통계)
- `_PIPELINE_SCAN_LATENCIES`, `_PIPELINE_PASS_METRICS`, `_PIPELINE_NEAR_MISS` (분석)
- `_PIPELINE_VALUE_TRACKER`, `_PIPELINE_SIGNAL_COINS` (값 분포)
- `_SHADOW_VIRTUAL_POSITIONS`, `_SHADOW_DEDUP`, `_SHADOW_PERF_STATS` (섀도우)
- `_ENTRY_SLIP_HISTORY`, `_EXIT_SLIP_HISTORY` (슬리피지)

**위험**:
- 단위 테스트 작성 시 30+개 전역 상태 초기화 필요
- 함수 시그니처만으로 의존성 파악 불가
- 전역 dict 증가 → Lock 객체도 비례 증가 → 데드락 표면적 확대

**권장**: 최소한 3개 클래스로 캡슐화:
- `TradingState`: OPEN_POSITIONS, TRADE_HISTORY, streaks, suspend
- `PipelineMetrics`: 모든 _PIPELINE_* 변수
- `ShadowTracker`: 모든 _SHADOW_* 변수

### 3.3 [HIGH] monitor_position() 800+줄 단일 함수

**위치**: L11271-12017

**현상**: 포지션 라이프사이클 전체를 하나의 함수에서 관리:
- 진입 검증 (postcheck)
- MFE/MAE 추적
- SL 디바운스 (하드스톱, 베이스스톱, 일반SL)
- Supply-check 감량 (v8)
- Trail 활성화 및 래칫 잠금
- 부분 익절 (다단계)
- Plateau 감지
- 피라미딩/추가매수
- 동적 트레일 모멘텀
- HOLD 전략 만료
- 시간만료 엑시트
- 텔레그램 알림

**위험**:
- 6+ 레벨 중첩 (nesting depth)
- 변수 재초기화 블록 다수 (L11856-11878)
- 상태 머신 로직이 암묵적 (명시적 state enum 없음)
- 테스트/디버깅이 사실상 불가능한 크기

**권장**: 상태 머신 패턴으로 분리:
```python
class PositionMonitor:
    def warmup(self): ...
    def check_hard_stop(self): ...
    def check_sl_debounce(self): ...
    def check_trail(self): ...
    def check_timeout(self): ...
    def tick(self):  # 메인 루프
        for check in self.checks:
            result = check()
            if result.should_exit: ...
```

### 3.4 [HIGH] 섀도우 시스템의 Lock 중첩 복잡도

**현상**: v8/v9 섀도우 시스템이 추가하는 Lock:
- `_SHADOW_LOCK` (L7957): 가상 포지션 관리
- `_SHADOW_PERF_LOCK` (L7971): 성과 통계
- `_SHADOW_LOG_LOCK` (L509): CSV 로깅
- `_PIPELINE_COUNTERS_LOCK` (L248): 파이프라인 메트릭

**위험**: 섀도우 평가(`_shadow_evaluate_positions`)가 메인 스캔 루프와 동시 실행 시:
- 섀도우 스레드: `_SHADOW_LOCK` → 가격 조회 → `_SHADOW_PERF_LOCK`
- 메인 스레드: 스캔 → `_v4_shadow_test_all_routes` → `_SHADOW_LOCK`
- 교차 접근 시 데드락 가능성은 낮으나 Lock 대기 시간 증가

**권장**: 섀도우 연산을 별도 큐 기반으로 분리 (생산자-소비자 패턴)

### 3.5 [MEDIUM] record_trade() 단위 자동 변환 — 미해결

v3.2.7 리뷰에서 지적한 `pnl_pct > 10.0` 자동 변환 로직이 여전히 존재. 호출부 단위 통일이 근본 해결책.

### 3.6 [MEDIUM] except Exception: pass 패턴 — 미해결

다수의 예외 처리에서 `pass`로 무시하는 패턴 여전히 존재. 최소한 로깅 추가 필요.

### 3.7 [MEDIUM] monitor_position 내부 매직 넘버

**현상**: 하드코딩된 임계값이 다수 존재:
- `0.4%` (풀백 요건), `1.35` (배수), `0.995` (피크 비율), `600` (초)
- `0.58` (매수비율), `18000` (KRW/s), `0.3` (가속도)
- `50%` (래칫 잠금), `40-55%` (MFE 체크포인트별)

**위험**: config.py에 정의된 상수와 monitor_position 내부 하드코딩이 혼재 → 튜닝 시 어디를 수정해야 하는지 불명확

**권장**: 모든 매직 넘버를 config.py 상수로 승격

### 3.8 [MEDIUM] 캔들 데이터 중복 조회

**현상**: `detect_leader_stock()` (L10365-10368)에서 c5/c15/c30/c60 캔들을 매번 조회하지만 shard 내 공유 캐시 없음. `monitor_position()`의 c1 캐시는 5초 TTL로 관리되나 (L11405-11411), detect 단계의 다중 TF 캔들은 캐싱 없음.

**영향**: API 호출 수 증가 → rate limit 소진 가속
**권장**: `_C5_CACHE` (L6385, LRU 300) 패턴을 c15/c30/c60에도 적용

### 3.9 [MEDIUM] monitor_position의 MFE 스냅샷 O(n) 루프

**위치**: L11502 부근

**현상**: MFE 스냅샷 업데이트마다 `MFE_SNAPSHOT_TIMES` (12개) 전체를 순회. 포지션당 3초 간격으로 반복 호출됨.

**영향**: MAX_POSITIONS=5 시 초당 ~20회 리스트 순회 (경미하나 불필요)
**권장**: 다음 스냅샷 시점만 추적하는 인덱스 변수 사용

### 3.10 [LOW] 오더북 캐시 스테일 — 미해결

shard 처리 동안 30초+ 오래된 오더북으로 판단하는 문제 여전히 존재. 진입 직전 개별 마켓 오더북 재조회 또는 TTL 적용 권장.

### 3.11 [LOW] 점화 윈도우 고정 (10초) — 미해결

유동성에 따른 동적 윈도우 미적용.

### 3.12 [LOW] Unused 변수 잔재

**현상**: `dynamic_stop_loss()`에서 사용되지 않는 multiplier 변수 (`_sl_signal_mult`, `_sl_profit_mult` 등)가 WF 오버라이드 도입 후에도 남아있음. v4 전략이 SL을 직접 제공하면서 기존 배수 로직이 사실상 비활성화.

---

## 4. 아키텍처 관찰 사항

### 4.1 진입 필터 누적 현상 (v9 기준 확대)

v3.2.7에서 40개 이상이던 필터가 v4 전략 레지스트리 추가로 더 확대:

1. **Pre-filters** (detect_leader_stock 내부): 스테이블코인, 포지션 중복, VOL_PEAK_OUT, FAKE_FLOW_HARD 등 11개+
2. **v4 Gate Filter** (`_v4_gate_filter`, L7194): RSI/EMA/MACD/ADX/VR5/ATR 기반 9개 하드컷
3. **Stage1 Gate** (L10441): spread/vol/buy_ratio/accel/early_flow 5개 하드컷 + 4-카테고리 점수
4. **3 독립경로**: 점화/강돌파/캔들돌파 (gate_score 무관)
5. **Post-gate**: BTC_HEADWIND, VWAP_CHASE
6. **Final Check**: SCORE_CUT, IMB_CUT + 다운그레이드
7. **Killer 조건** (6/6 체크)
8. **6초 포스트체크** (L10718)
9. **가격 가드**
10. **v4 전략별 추가 조건** (각 전략 함수 내부)

총 **50개 이상**의 필터/조건. 섀도우 시스템(v8)이 이 문제를 부분적으로 해결 — 차단된 시그널의 잠재 성과를 추적하여 과도한 필터 식별 가능.

### 4.2 v4 전략과 기존 시스템의 이중 경로

**현상**: v7에서 `_STRATEGY_REGISTRY` (L7851)를 도입했으나, 기존 `detect_leader_stock()`의 하드코딩된 진입 로직도 여전히 활성. 두 시스템이 공존:
- **레거시 경로**: detect_leader_stock → stage1_gate → final_check → 진입
- **v4 경로**: v4_evaluate_entry → 전략별 체크 → exit_params 반환

**위험**: 동일 시그널이 두 경로를 모두 통과하는 경우의 우선순위가 불명확
**권장**: v4 전략이 충분히 검증되면 레거시 경로를 점진적으로 폐기

### 4.3 MFE 피드백 루프의 자기강화

**현상**: v7의 `mfe_feedback_exit_params()` (L1443)이 과거 MFE 데이터로 청산 파라미터를 자동 조정:
- `activation_pct = avg_peak_mfe × 0.6`
- `trail_pct = avg_peak_mfe × 0.35`

**관찰**: 청산 파라미터가 과거 MFE를 따라가면서 자기강화 루프 발생 가능:
- 높은 MFE → 높은 activation → 더 오래 보유 → 평균 MFE 하락 → activation 하락 → ...
- 낮은 MFE → 낮은 activation → 빨리 청산 → MFE 포착 축소 → ...

**완화 요소**: min/max 클램핑 (activation 0.2~1.0%, trail 0.1~0.6%)이 극단 방지
**권장**: 피드백 강도를 제어하는 감쇄 계수(damping factor) 도입 검토

### 4.4 섀도우 vs 실제 청산 시뮬레이션 정합성 (v9)

**현상**: v9에서 `_shadow_sim_exit()` (L8047)이 실제 TRAIL 청산 로직을 시뮬레이션하도록 개선. 그러나:
- 실제 `monitor_position()`은 800+줄의 복잡한 상태 머신
- 섀도우 시뮬레이션은 단순화된 TRAIL만 적용
- Supply-check 감량, plateau 감지, 피라미딩 등은 시뮬레이션에 미포함

**위험**: 섀도우 성과와 실제 성과 간 괴리 → 임계값 완화 근거의 신뢰도 저하
**권장**: 시뮬레이션 충실도 명시 (어떤 로직이 포함/제외되는지 문서화)

---

## 5. Lock 체계 분석 (v9 기준)

### 5.1 문서화된 Lock 순서 (L922-940)

```
1) _MEMORY_LOCK          (진입 락 메모리)
2) _POSITION_LOCK        (포지션 + _CLOSING_MARKETS)
3) _MONITOR_LOCK         (모니터 레지스트리)
4) _ORPHAN_LOCK          (_ORPHAN_HANDLED)
5) _RECENT_BUY_LOCK      (_RECENT_BUY_TS)
6) _STREAK_LOCK          (_lose/_win_streak)
   _COIN_LOSS_LOCK       (_COIN_LOSS_HISTORY)
7) _RETEST_LOCK          (리테스트 워치리스트)
   _CIRCLE_LOCK          (동그라미 워치리스트)
   _BOX_LOCK             (박스 워치리스트)
8) _trade_log_lock       (거래 로그)
   _CSV_LOCK             (CSV 파일)
9) _TG_SESSION_LOCK      (텔레그램)
   _req_lock             (API 요청)
```

### 5.2 계측 전용 Lock (v8 추가, 순서 미지정)

| Lock | 보호 대상 | 위치 |
|------|-----------|------|
| `_PIPELINE_COUNTERS_LOCK` | _PIPELINE_COUNTERS | L248 |
| `_PIPELINE_COIN_HITS_LOCK` | _PIPELINE_COIN_HITS | L306 |
| `_PIPELINE_STRATEGY_LOCK` | _PIPELINE_STRATEGY_PASS | L310 |
| `_PIPELINE_HOURLY_LOCK` | 시간별 통계 3개 | L316 |
| `_PIPELINE_SCAN_LAT_LOCK` | 스캔 레이턴시 | L320 |
| `_PIPELINE_PASS_METRICS_LOCK` | 통과 메트릭 | L324 |
| `_PIPELINE_NEAR_MISS_LOCK` | 근접 실패 | L328 |
| `_PIPELINE_VALUE_TRACKER_LOCK` | 값 분포 | L391 |
| `_PIPELINE_SIGNAL_COINS_LOCK` | 시그널 코인 | L409 |
| `_SHADOW_LOCK` | 가상 포지션 | L7957 |
| `_SHADOW_PERF_LOCK` | 섀도우 성과 | L7971 |
| `_SHADOW_LOG_LOCK` | 섀도우 CSV | L509 |
| `_SIGNAL_STATS_LOCK` | 시그널 통계 | L1123 |
| `_IGNITION_LOCK` | 점화 감지 | L205 |

**관찰**: 계측 Lock들은 메인 트레이딩 Lock과 교차 사용되지 않아 데드락 위험은 낮음. 다만 14개 추가 Lock은 관리 복잡도 증가.

---

## 6. 성능 관련

### 6.1 잘 된 점
- BTC 캔들 캐시: shard당 1회 조회
- `ThreadPoolExecutor` 캔들 병렬 조회 (L12837)
- 틱 캐시 `_TICKS_CACHE` LRU 100 (L6383)
- 5분봉 캐시 `_C5_CACHE` LRU 300 (L6385)
- 마켓 목록 캐시 `_MKTS_CACHE` 90초 TTL (L6470)
- BTC regime 캐시 (L6908)
- RSI 계산 시 기존 캔들 재사용
- **[v7]** 포스트체크 fast-track: 점화 0.1초, 강돌파 0.3초 (네트워크 I/O 절약)

### 6.2 개선 가능 영역
- `micro_tape_stats_from_ticks()` 동일 ticks 다중 윈도우 반복 호출
- `calc_orderbook_imbalance()` 동일 ob 재호출
- `detect_leader_stock()` c15/c30/c60 캔들 캐싱 없음
- `monitor_position()` 내 Supply-check (L11602): ticks + orderbook 동기 조회 (SL 경로에서 지연)
- 서지 볼륨 분석 (L11747): 30 ticks 재조회
- MFE 스냅샷 O(n) 순회 (경미)
- 오더북 shard 내 30초+ 스테일

---

## 7. 결론

### 강점
- **데이터 기반 진화**: v7~v9에 걸쳐 MFE 피드백, 섀도우 계측, 리포트 기반 임계값 완화 등 체계적인 데이터 수집→분석→적용 파이프라인 구축
- **방어적 설계 강화**: Lock 순서 문서화, _RECENT_BUY_LOCK 추가, reentrant entry lock, streak 보호 등 이전 리뷰 이슈의 절반 이상 해결
- **계측 인프라**: 30+ 메트릭, 시간별/전략별/코인별 통계, near-miss 추적으로 데이터 기반 의사결정 기반 마련
- **점진적 검증**: 섀도우 가상매매로 미진입 시그널의 잠재 성과를 추적하여 필터 과잉/부족 식별 가능

### 개선 필요 (우선순위 순)

| 우선순위 | 이슈 | 영향 | 난이도 |
|----------|------|------|--------|
| 1 | **파일 분리** (13,824줄 → 모듈화) | 유지보수, 협업, IDE 성능 | 높음 |
| 2 | **monitor_position 분리** (800줄 함수) | 테스트, 디버깅, 가독성 | 중간 |
| 3 | **전역 상태 캡슐화** (30+ → 클래스 3~4개) | 테스트, 의존성 관리 | 중간 |
| 4 | **매직 넘버 상수화** (monitor_position 내부) | 튜닝 용이성 | 낮음 |
| 5 | **섀도우 시뮬레이션 충실도** (TRAIL만 → 전체) | 임계값 완화 신뢰도 | 중간 |
| 6 | **캔들 캐시 확대** (c15/c30/c60) | API 효율 | 낮음 |
| 7 | **MFE 피드백 감쇄** (자기강화 방지) | 청산 안정성 | 낮음 |
| 8 | **테스트 인프라** (단위테스트 0건) | 회귀 방지 | 높음 (선행: 1,2,3) |
| 9 | **record_trade() 단위 통일** | 데이터 정확성 | 낮음 |
| 10 | **except pass → 로깅** | 디버깅 | 낮음 |

### 종합 평가

v3.2.7 대비 **구조적 성숙도가 크게 향상**됨:
- Lock 체계 문서화, 상태 영속화, 계측 인프라 등 프로덕션 수준의 운영 기능이 추가됨
- 섀도우 계측으로 데이터 기반 의사결정이 가능해짐
- 주요 스레드 안전성 이슈(3.4, 3.8)가 해결됨

그러나 **아키텍처적 부채**는 증가 추세:
- 13,824줄 단일 파일, 30+ 전역 상태, 800줄 함수는 기능 추가마다 복잡도가 기하급수적으로 증가하는 구조
- 모듈화 없이는 테스트 인프라 구축이 사실상 불가능하며, 이는 v7~v9의 데이터 기반 개선이 회귀 버그로 무효화될 위험을 내포

**가장 시급한 다음 단계**: bot.py 모듈 분리 → 단위테스트 기반 마련 → 리팩토링 안전망 확보

---

*이 리뷰는 코드 읽기 기반 정적 분석입니다. 실제 거래 성과에 대한 평가는 포함하지 않습니다.*
