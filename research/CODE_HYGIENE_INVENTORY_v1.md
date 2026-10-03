# Code Hygiene Inventory v1 — Dead-code Map (지우기 전 지도)

```
status:     INVENTORY (map only · 삭제 금지)
authored:   2026-10-03
scope:      bot.py 분류 · 매매 로직 무변경 · 실제 변경은 별도 commit
touches:    no code change · 문서만
```

**목적** (advisor 2 명시): "지우기 전에 지도부터". bot.py 23,662줄에서 **(a) 매매 핵심
runtime** / **(b) 측정 활성 필요** / **(c) archive-only (제거 안전)** / **(d) 분리 가능
(파일로 격리)** 로 분류. 이번 inventory 는 **문서만** · 코드 변경 0.

Related PR: #546 (`codex/cleanup-and-alpha-workplan`) · workplan 문서 중복 피함.

---

## 1. Shadow Route Registry 실측 (bot.py:13550-14000 영역)

### 1a. 실 LIVE 활성 (`enabled=True`) — **1개**
| Route | 상태 | 조치 |
|---|---|---|
| `CS40_VR3_TR180_bp30_240` | LIVE · tiny 상한 10만원 | **절대 유지** (매매 핵심) |

### 1b. Shadow 활성 (`shadow_enabled=True` 명시) — **4개**
| Route | 사유 (코드 주석) | 조치 |
|---|---|---|
| `CLM_CS40_TR180_15_240` | 구 LIVE 기준선 (조언자 관심) | **유지** (archival 비교 baseline) |
| `CS40_TR180_bp30_240` | VR 효과 확인 기준선 | **유지** (A/B comparison) |
| `CLM_A_CLEAN_bp30` | A 봉인 but shadow 계속 | **유지** (P0-B classifier 데이터) |
| `CLM_A_x_A2_bp30` | A×A2 봉인 but shadow 계속 | **유지** (A×A2 cohort audit) |

### 1c. `enabled=False` + `shadow_enabled` 미명시 — **24+개** (대부분 자동 shadow)
| Route | 리포트 상태 (오늘) | 분류 제안 |
|---|---|---|
| `SVE1` | n=290 pnl-0.15% wr45% BLOCK 4/7 | **격리 후보** (ENABLE 미달 수개월) |
| `SVE1_ASK1` | n=256 pnl-0.09% BLOCK 0/7 | **격리 후보** |
| `RET` | n=2550 pnl-0.10% wr34% BLOCK 0/7 | **격리 후보** (수개월 안정 음수) |
| `LTRP` | n=508 pnl-0.28% wr48% BLOCK 5/7 | **격리 후보** |
| `CLM` | n=223 pnl-0.07% BLOCK 5/7 | **격리 후보** (CLM 원형) |
| `CLM_A` | n=20 pnl+0.02% BLOCK | **archive** (A 봉인) |
| `CLM_A2` | n=38 pnl-0.00% BLOCK | **archive** (A2 TERMINATED) |
| `CLM_B50/B55/B60/B65` | n=161~222 pnl-0.01~-0.07% BLOCK ⚠조기붕괴 | **격리 후보** (body ladder · 수개월 확인) |
| `CLM_B60_LE` · `CLM_LE` | n=205/220 pnl-0.04/-0.08% BLOCK ⚠조기붕괴 | **격리 후보** (LE 실험 · 결론 음수) |
| `CLM_B60_PP30` | n=196 pnl-0.02% BLOCK | **격리 후보** (PP 조합) |
| `CLM_PP20/PP30/PP40` | n=220 pnl-0.04~-0.05% BLOCK | **격리 후보** (PP 레벨 · 동일 결론) |
| `CLM_EC_A` | n=219 pnl-0.05% BLOCK | **격리 후보** (EarlyCut A) |
| `CLM_TR120_15_180` | n=210 pnl-0.09% BLOCK ⚠조기붕괴 | **격리 후보** (TR 변형) |
| `CLM_TR180_15_240` · `CLM_TR180_15_300` | n=215/209 pnl-0.11% BLOCK ⚠조기붕괴 | **격리 후보** |
| `CS40_TR180_bp50/70/100_240` | n=48 pnl-0.01~-0.05% BLOCK | **격리 후보** (bp variants · no-VR) |
| `CS40_VR3_TR180_bp50_240` | n=24 pnl+0.27% weak | **보류** (Research Top-3 · 아직 n 적음) |
| `CS40_VR3_TR180_bp70_240` | n=24 pnl+0.25% weak | **보류** (Research Top-3) |
| `CS40_VR3_TR180_bp100_240` | n=390 pnl+0.08% BLOCK | **격리 후보** (bp100 결론) |
| `B45_CS40_VR3_TR180_bp30/50/70/100_240` | n=6~245 collecting~ENABLE | **보류** (survival 변동 · 아직 관찰 중) |
| `B45_CS40_VR35_TR180_bp30/50/70/100_240` | n=1~204 collecting~SHADOW | **보류** (B45 variant 관찰 중) |
| `CLM_OBSLIP` | n=17 pnl+0.24% weak | **보류** (Research Top-3) |
| `PBR` · `PBR_STRICT` · `PBR_MOMO` | n=10~123 pnl-0.03~-0.06% BLOCK ⏳skew | **격리 후보** (PBR family · skew 유지) |

**격리 vs 보류 기준**:
- **격리 후보** = BLOCK 상태 + 수개월 음수 + 승격 조건 지속 미달 (리포트 기반 분류)
- **보류** = 아직 관찰 가치 (Research Top-3 · survival 변동 · n 미달)

**⚠ 중요 정정 (advisor 2 지적 · 실측 확인 결과)**:
- "격리 후보" 분류는 **리포트 상태 기반 추측** · 각 route 가 **shadow 신규 생성
  활성 여부는 실측 미확정**
- advisor 2 (PR #546 writer): "**EC_A · PP30 · PP40 · OBSLIP 등 일부는 이미
  신규 평가에서 제외되는 설정**" 명시 · 하지만 내 grep 으로 명확한 skip 조건
  확정 불가 · 다른 분기/변수 가능성
- grep 실측 결과:
  - `_V0_EXIT_PARAMS_CLM_PP30/PP40/EC_A` 전부 bot.py:11829-11836 정의
  - `_STRATEGY_REGISTRY` 에 route config 등록 (bot.py:13678-13695)
  - 리포트 집계에 참조 (`_bladder` 16575 · `_pp_rm_routes` 16598 ·
    `_pp_er_routes` 16628 · `_eas.get("route") != "CLM_EC_A"` 16788)
  - 즉 **registry 등록 + 리포트 참조 상태** · 실제 shadow 생성 여부는 scan loop
    쪽에서 확인 필요

**조치 (advisor 2 요구 수용)**:
- 각 격리 후보 route 는 **"격리 전 참조 그래프 확인 필수"** · 바로 delete 금지
- PR #546 inventory (codex/cleanup-and-alpha-workplan · 37줄 압축 version) 와
  **cross-check 필요** · 두 inventory 의 분류 차이 확인
- Advisor 2 명시 "A/A2 신규 shadow 생성 중단" 을 **첫 코드 변경 scope 로 좁힘**
  (내가 inventory v1 에서 "~20개 격리 후보" 로 넓힌 것 과잉)

**총계 (하향 조정)**: 
- LIVE 1개 (확정) · shadow_enabled=True 명시 4개 (확정)
- 격리 후보 ~20개 **(리포트 기반 추측 · 실측 확인 필요)** · 보류 ~10개

---

## 2. Reporting / Diagnostic 함수 (분리 가능 · bot.py 밖으로)

**advisor 2 지적**: "bot.py 가 reporting/research 코드로 길어졌음". 매매 핵심과
분리 가능한 함수들:

### 2a. 봉인된 실험 전용 reporting (archive-only · 매매 무관)
- `_a2_audit_summary()` (bot.py:2204- · A2 TERMINATED 전용 archival)
- `_common_cohort_paired_summary()` (A/A×A2 봉인 cohort archival · 재판정 X)
- A verdict archival drift 라인 (post-final ΔA · [ARCHIVAL ONLY])
- `/tmp/paired_cohort_snapshot.json` audit (archival integrity)

### 2b. 활성 분석 reporting (유지 · research 분리 가능)
- `_shadow_contamination_check()` (bot.py:2068- · A_CLEAN purity · 활성)
- `_shadow_exit_engine_config_summary()` (실효 청산 설정)
- `_v4_shadow_score_compact()` (compact score · 활성)
- `_pass_entry_funnel_summary()` (PASS→ENTRY · P1 lifecycle 포함 · 활성)

### 2c. Research-only (분리 1순위 후보 · **단 참조 그래프 사전 확인 필수**)
- Survival Analysis — **⚠ 정정 (advisor 2 지적 · grep 실측)**:
  - `_SURVIVAL_SCORING_CACHE` (bot.py:14045) 가 route 별 rules 저장
  - bot.py:17171: `_SURVIVAL_SCORING_CACHE[route] = list(rules)`
  - bot.py:17200 명시: `"""캐시된 survival rules로 HI/LO 예측. 순수 로깅용, 진입 차단 없음."""`
  - **매매 결정 영향 X** (진입 차단 없음) · 하지만 **route↔cache 참조 존재** ·
    출력 부분만 분리 시 로깅 재배선 필요
  - 조치: 분리 가능하되 "research-only 1순위" 단정 X · 참조 재배선 commit 포함 필요
- EarlyCut 분류 (CLM 60s 시점) · 사후 분석 (**grep 확인 전 분리 판단 보류**)
- 필터검증 fail-WR 집계 (`[효과 식별 불가]` 다수)
- B-ladder + LE + PP 비교 · 격리 후보 route 전용 (**단 §1c cross-check 필요**)
- PP r/m 비교 · 격리 후보 전용 (**단 §1c cross-check 필요**)

**예상 효과 (하향 조정)**: `_report_builders.py` 로 분리 시 bot.py에서 감소 가능
하되 **정확한 줄 수는 실측 필요** (1,500-2,500 는 추측). 매매 로직 무영향은 유지 ·
하지만 **참조 재배선 동반 필요** (Survival cache 등).

**⚠ 추측 vs 실측 구분**:
- grep 실측된 사실: 참조 존재 여부
- 추측 (실측 필요): 격리 후보 route 가 "shadow 신규 생성 여부" (advisor 2: "EC_A·
  PP30/40·OBSLIP 등 이미 신규 평가 제외" 라고 명시했으나 내 grep 으로 미확정)
- PR #546 inventory (`codex/cleanup-and-alpha-workplan`) 와 cross-check 필요

---

## 3. 유지 필수 (삭제·분리 금지)

### 3a. 매매 핵심 (돈 직결 · 절대 불가침)
- 주문 함수: `place_limit_buy` · `place_market_buy` · `cancel_order`
- `hybrid_buy()` (bot.py:5083)
- `open_auto_position()` (bot.py:5807)
- `close_auto_position()` (bot.py:6963 · AUTO_TRADE gate 포함)
- Exit 엔진 (bot.py:14883-14895 · `_eff_sl` · "손절SL" emitter)
- Factory 함수 (`_make_clm_trail_clean` bot.py:11778 등)
- `_STRATEGY_REGISTRY` 선언 (route config · line 13550+)
- Position monitor · OPEN_POSITIONS 관리

### 3b. 활성 측정 · classifier (고치면 데이터 깨짐)
- `_live_trade_log_entry` / `_live_trade_log_exit` (bot.py:990 · 1057 · P1-a · schema_v=2)
- `_a_clean_purity.py` (classifier · pytest 커버)
- `_shadow_perf_stats` · `trade_records` cap=300 (bot.py:14882)
- `_pipeline_inc` · `_PIPELINE_COUNTERS` (P1 lifecycle)
- `shadow_ref_id` 생성 로직 (P1-a · pair 조인 키)

### 3c. 봉인 라벨 (재판정 방지 가드)
- `A_STATUS = REJECTED_FINAL / CLOSED` 라인 (bot.py:2739-2744)
- `A2 TERMINATED_INFEASIBLE / CLOSED`
- `post-final ΔA [ARCHIVAL ONLY] · verdict reevaluation: DISABLED`
- `[ARCHIVAL ONLY · A CLOSED]` 태그
- Straggler 방지 주석 (`100 승격 대기` 관련)

### 3d. Append-only cohort (삭제 시 PAIRED AUDIT lost=0 깨짐)
- `trade_records` deque (각 route 별)
- `/tmp/paired_cohort_snapshot.json`
- `/tmp/a_kill_archive_latest.json`
- `data/live_trades.jsonl` (schema_v=1 legacy + schema_v=2 신규)

---

## 4. 안전한 정리 순서 (advisor 1 명시 · 단계적)

**Phase 1 — Inventory & 격리 (지금 단계 · 코드 변경 X)**:
- 본 문서 작성 (완료 · 이 commit)
- PR #546 workplan 문서와 중복 피함 (내용 상호 보완)

**Phase 2 — Archive-only reporting 분리 (저위험 · 매매 로직 X)**:
- `_report_builders.py` 신규 파일로 이동 (**단 Survival cache 참조 재배선 포함**)
- bot.py 는 import + orchestration 만
- 각 분리 commit 별 regression + startup/import test + **참조 그래프 확인**
- 예상: 실측 필요 (추측 숫자 삭제)

**Phase 3 — 격리 후보 route (⚠ advisor 2 요구 수용 · scope 좁힘)**:
- **첫 scope = "A/A2 신규 shadow 생성 중단"** (advisor 2 명시 · 좁게)
  - A/A2 는 봉인 (REJECTED_FINAL/TERMINATED) 상태 · shadow 신규 생성만 중단
  - 기존 cohort/trade_records 전부 보존 (archival · 재현용)
  - 봉인 라벨/post-final ΔA 라인 전부 유지
- 그 다음 scope = 다른 격리 후보 route (§1c · 각각 참조 그래프 확인 후)
- **"~20개 일괄 격리" 금지** (내 inventory v1 과잉 scope)
- 각 route 격리 전:
  - PAIRED AUDIT · COMMON_COHORT 참조 grep
  - trade_records 참조 grep
  - 리포트 집계 참조 grep (_bladder/_pp_rm_routes/_pp_er_routes 등)
  - Survival cache 참조 grep (`_SURVIVAL_SCORING_CACHE`)
  - 참조 있으면 isolate (분리 모듈로 이동) · 삭제 X
  - 참조 0 확인된 것만 나중에 제거

**Phase 4 — 주문·포지션·청산 core 리팩토링 (최후 · P1 Layer A/B/C 안정 후)**:
- advisor 1 명시: "주문·포지션·청산 core 는 마지막에 건드립니다"
- P1 completed data 축적 + Layer B/C acceptance 통과 후만
- 지금 X

---

## 5. 체크리스트 (각 분리/정리 commit 전 필수)

- [ ] `python3 -c "import ast; ast.parse(open('bot.py').read())"` → SYNTAX_OK
- [ ] `python3 tests/test_a_clean_purity.py` → 18/18 pass
- [ ] 삭제/격리 대상 route · 함수가 다음에 참조되는지 grep:
  - PAIRED AUDIT 계산 경로
  - COMMON_COHORT 계산 경로
  - `trade_records` 접근 경로
  - 봉인 라벨 생성 경로
- [ ] 봉인 문구 (REJECTED_FINAL · TERMINATED · ARCHIVAL ONLY · verdict reevaluation
      DISABLED) 보존 확인
- [ ] startup/import test (bot.py 가 clean import 되는지)
- [ ] shadow report equivalence (분리 전후 리포트 동일성)

---

## 6. 규율 (변화 없음)

- 삭제 X · **분리/격리** O (production runtime 퇴역 · 재현용 보존)
- 매매 로직 · 전략 param · bp · OBSLIP · SVE2 · A/A2 · C1 · C2 실데이터 전면 동결
- Post-hoc filter · gate 완화 · TP 추가 금지 (전면)
- Measurement contract 가 alpha 코드보다 먼저 테스트 통과
- 각 단계 작은 commit · 매매 영향 0 확인 후 다음

---

## Status

```
status:          INVENTORY (map only · 삭제 금지)
next step:       Phase 2 (archive-only reporting 분리) · 사용자/advisor 승인 후
code change:     없음 (이 commit · bot.py 무변경)
related PR:      #546 (workplan 문서 · 상호 보완)
```
