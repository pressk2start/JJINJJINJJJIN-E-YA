# Phase 2 Extraction — Reference-0 Audit (grep 실측 근거)

```
status:     AUDIT (실측 근거만 · 분리 commit 전 전수 체크용)
authored:   2026-10-03
scope:      Phase 2 reporting extraction 후보 함수의 매매 로직 참조 검증
touches:    no code change · grep 근거만
```

**목적** (advisor 1+2 요구): Phase 2 reporting extraction 전 "매매 로직 참조 0"
을 **grep 실측** 으로 입증. 분리 commit 는 이 audit 통과 후만 진행.

**advisor 2 핵심 지적 수용**:
- "파일 이동만으로 runtime 에서 퇴역하지 않습니다 · 계속 import·호출하면 계산은
  그대로 남습니다"
- "줄 수 감소와 '실매매 영향 0'은 **변경 후 검증할 결과**이지 사전 보장이 아님"

→ 본 audit 는 "매매 함수 직접 참조 0" 까지만 입증 · 전체 runtime 비용 감소는
**분리 후 shadow report equivalence diff + startup/import test** 로 검증.

---

## 1. 검증 방법

```bash
# 각 reporting 함수가 매매 로직 함수들 내부에서 호출되는지 grep
for mfunc in open_auto_position close_auto_position hybrid_buy monitor_position:
    for rfunc in [reporting functions]:
        check if rfunc appears in body of mfunc
```

### 검증 대상 매매 로직 함수 (bot.py)

- `open_auto_position` (body 43,953 chars)
- `close_auto_position` (body 23,975 chars)
- `hybrid_buy` (body 6,656 chars)
- `monitor_position` (body 64,558 chars)

**추가 확인 필요** (이번 audit scope 밖 · 후속):
- `check_fn` family (climax detection)
- `detect_leader` loop
- `scan_markets` cycle
- Signal pipeline (`post_signal_*`)

---

## 2. 실측 결과 (grep 2026-10-03)

### 2a. 매매 로직 직접 참조 = **0** (전부 분리 안전 입증)

| Reporting Function | open_auto | close_auto | hybrid_buy | monitor | 결과 |
|---|---|---|---|---|---|
| `_a2_audit_summary` | 0 | 0 | 0 | 0 | ✅ 분리 안전 |
| `_common_cohort_paired_summary` | 0 | 0 | 0 | 0 | ✅ 분리 안전 |
| `_shadow_contamination_check` | 0 | 0 | 0 | 0 | ✅ 분리 안전 |
| `_shadow_exit_engine_config_summary` | 0 | 0 | 0 | 0 | ✅ 분리 안전 |
| `_pass_entry_funnel_summary` | 0 | 0 | 0 | 0 | ✅ 분리 안전 |
| `_v4_shadow_score_compact` | 0 | 0 | 0 | 0 | ✅ 분리 안전 |
| `_shadow_route_flow_summary` | 0 | 0 | 0 | 0 | ✅ 분리 안전 |
| `_detect_gate_format_summary` | 0 | 0 | 0 | 0 | ✅ 분리 안전 |
| `_SURVIVAL_SCORING_CACHE` | 0 | 0 | 0 | 0 | **단 ⚠ 아래 §3 확인 필요** |

### 2b. 전체 참조 수 (함수 정의 + 호출)

| Function | 전체 참조 | 호출 위치 |
|---|---|---|
| `_a2_audit_summary` | 2 | bot.py:2855 (리포트 조립) |
| `_common_cohort_paired_summary` | 3 | bot.py:2859 (리포트 조립) · +1 (추가 호출) |
| `_shadow_contamination_check` | 2 | bot.py:2851 |
| `_shadow_exit_engine_config_summary` | 2 | bot.py:2847 |
| `_pass_entry_funnel_summary` | 2 | bot.py:2839 |
| `_v4_shadow_score_compact` | 2 | bot.py:3405 (Tier 3 SCORE) |
| `_shadow_route_flow_summary` | 2 | bot.py:3317 (리포트 조립) |
| `_detect_gate_format_summary` | 2 | bot.py:3309 (리포트 조립) |

**패턴**: 각 함수 = **1 정의 + 1 리포트 조립 호출** (가끔 추가 1개)
**결론**: 분리 시 리포트 조립 function 의 **import 경로만 수정** 하면 됨.

### 2c. Survival 재검증 (advisor 2 지적 · 2026-10-03 재실측)

**추가 grep 발견** (내 이전 "매매 참조 0" claim 수정):
```
bot.py:14045: _SURVIVAL_SCORING_CACHE = {}
bot.py:17171: _SURVIVAL_SCORING_CACHE[route] = list(rules)
bot.py:17200: """캐시된 survival rules로 HI/LO 예측. 순수 로깅용, 진입 차단 없음."""
bot.py:17202: rules = _SURVIVAL_SCORING_CACHE.get(route)
bot.py:19791: pre["survival_score"] = _sv_score                        # 쓰기 (예측 시점)
bot.py:6613:  "survival_score": pre.get("survival_score", 0)            # trade record 저장 (open path body)
```

**정정 (advisor 2 지적 수용)**:
- 매매 로직 함수 body 내부에서 `pre["survival_score"]` **쓰기 경로 존재**
  (bot.py:6613 · trade record 기록)
- 즉 "매매 참조 0" 은 **과장** · 판단에는 쓰이지 X ("진입 차단 없음" 유지) ·
  **저장 의존성은 있음**
- 분리 시: survival_score 쓰기 경로 **재배선 필요** · 단순 cache 분리 X

**advisor 2 핵심 지적 재확인**:
> "'진입 차단 없음' 주석만으로 매매 영향이 없다고 확정할 수 없습니다. 반환값과
> `pre["survival_score"]`의 모든 소비처를 확인해야 합니다."

- **Phase 2 scope 에서 Survival 제외 유지** · 별도 Phase (cache + score 쓰기
  경로 전체 정리 후) 로 격리
- 분리 후보 분류: **RED (분리 전 추가 작업 필수)** 유지 · 등급 변화 없음

---

## 3. 분리 안전 분류 (실측 반영)

### 3a. **GREEN** (매매 참조 0 · 리포트 조립만 import 수정 · 분리 즉시 가능)

- `_a2_audit_summary` (archive-only · A2 TERMINATED 전용)
- `_common_cohort_paired_summary` (A/A2 봉인 cohort · archival drift)
- `_shadow_exit_engine_config_summary` (실효 청산 설정 요약)
- `_detect_gate_format_summary` (gate 통계)
- `_shadow_route_flow_summary` (route delta 통계)

**조치**: `_report_builders.py` 로 이동 · bot.py 리포트 조립 함수 import 추가 ·
regression + shadow report equivalence diff 로 검증.

### 3b. **YELLOW** (매매 참조 0 but 활성 측정 코드 · 분리 신중)

- `_shadow_contamination_check` (A_CLEAN purity 활성 분석 · classifier 와 연결)
- `_pass_entry_funnel_summary` (P1 lifecycle exch[] 포함 · 활성)
- `_v4_shadow_score_compact` (compact score · CURRENT_LIVE_ROUTE 라벨 포함)

**조치**: 분리 가능하되 **매매 함수 외 측정 함수 (`_live_trade_log_entry` 등)
에서 간접 참조되는지 추가 grep 필요**. Phase 2 **2순위** 로 분리.

### 3c. **RED** (분리 전 추가 작업 필수)

- `_SURVIVAL_SCORING_CACHE` + 관련 함수
  - 매매 직접 참조 0 · 하지만 HI/LO 예측 호출 체인 재확인 필요
  - **Phase 2 제외** · 별도 Phase (Survival cache 의존 정리 후)

---

## 4. 분리 commit 체크리스트 (각 함수 분리 시 반드시)

1. [ ] 대상 함수의 매매 로직 참조 0 재확인 (open_auto · close_auto · hybrid_buy
   · monitor_position + 추가 scan loop · check_fn)
2. [ ] 대상 함수 내부에서 호출하는 **다른 함수들**이 매매 경로에 있는지 2차 grep
3. [ ] 대상 함수가 접근하는 **global state** (예: `_SHADOW_PERF_STATS` ·
   `_PIPELINE_COUNTERS`) 가 매매 함수와 공유되는지 확인
4. [ ] `_report_builders.py` 로 이동 · bot.py import 추가
5. [ ] `python3 -c "import ast; ast.parse(open('bot.py').read())"` → SYNTAX_OK
6. [ ] `python3 -c "import bot"` → startup/import test 통과
7. [ ] `python3 tests/test_a_clean_purity.py` → 18/18 pass
8. [ ] **Shadow report equivalence diff**: 분리 전/후 동일 입력에 대해 리포트
   문자열이 byte-identical 인지 확인 (최소 1 샘플 run)
9. [ ] 봉인 문구 보존 확인 (REJECTED_FINAL · TERMINATED · ARCHIVAL ONLY ·
   verdict reevaluation DISABLED · CURRENT_LIVE_ROUTE)
10. [ ] Commit 단독 (다른 변경 섞지 X · advisor 1 "report extraction / route
    retirement / contract / runner 각각 별도 commit")

---

## 5. 분리 순서 제안 (GREEN 부터 하나씩)

**Commit 1 (가장 안전)**: `_shadow_exit_engine_config_summary` + `_detect_gate_format_summary`
+ `_shadow_route_flow_summary` → `_report_builders.py` 로 이동

**Commit 2**: `_a2_audit_summary` + `_common_cohort_paired_summary` →
`_report_builders.py` 로 이동 (archive-only · 봉인 라벨 보존 특히 주의)

**Commit 3 (YELLOW · 추가 grep 후)**: `_shadow_contamination_check` ·
`_pass_entry_funnel_summary` · `_v4_shadow_score_compact` → 각각 별도 commit
또는 묶음 (추가 grep 결과에 따라)

**Commit 4+ (RED)**: Survival cache 의존 정리 후 별도 Phase

**각 commit 전 §4 체크리스트 전수 통과 필수.**

---

## 6. Runtime 비용 감소는 분리의 결과 아님 (advisor 2 지적)

- 파일 이동만으로 runtime 계산 비용 감소 X
- 리포트 조립 함수는 **여전히 매 리포트 cycle 마다 모든 reporting 함수 호출**
- **실 runtime 비용 감소** 를 원하면 별도 작업 필요:
  - 리포트 interval 조정 (10분 → 30분 등)
  - 조건부 skip (shadow 데이터 없으면 skip)
  - 각 함수 내부 lazy evaluation
- 이건 **Phase 2 scope 밖** · 분리 완료 후 별도 ticket

**Phase 2 scope** = **"조직화" + "매매 로직과 reporting 분리"** 까지만.
"줄 수 감소" · "실매매 영향 0" 은 각 commit 의 shadow report equivalence 로
증명 (사전 보장 X).

---

## 7. 추가 grep 필요 (Phase 2 commit 1 전)

- Scan loop 함수 (`scan_markets` · 메인 while loop)
- `detect_leader` 와 그 호출 체인
- `check_fn` family (climax · retest · momentum 등)
- `pre_signal_*` · `post_signal_*` pipeline 함수들
- Monitor position 의 extended body (64,558 chars · 재분할 필요 가능)

**이 추가 grep 은 commit 1 전에 완료** · 숨은 참조 가능성 완전 배제.

---

## 8. 작업 큐 (advisor 1 "할까요?" 종료 수용)

| Step | 작업 | Commit |
|---|---|---|
| 1 | F4 contract 작성 | **✅ 완료 (5f4cfe9)** |
| 2 | **이 audit 문서 commit** | **← 지금** |
| 3 | 추가 grep (§7 · scan loop · detect_leader) | 다음 session |
| 4 | Commit 1: GREEN 3개 함수 → `_report_builders.py` | 다음 session · 체크리스트 전수 |
| 5 | Commit 2: GREEN 2개 (archive-only) · 봉인 보존 특히 주의 | 다음 session |
| 6 | Commit 3+: YELLOW (추가 grep 후) | 승인 후 |
| 7 | Commit 4+: RED (Survival 등) · 별도 Phase | 승인 후 |

**각 commit 전 §4 체크리스트 전수 · 각 commit 단독 (advisor 1 "섞지 X").**

---

## Status

```
status:        AUDIT (실측 근거 · 분리 commit 전 체크용)
next step:     §7 추가 grep → Commit 1 (GREEN 3개 함수 분리)
code change:   없음 (본 commit · bot.py 무변경)
```

**본 문서는 참조-0 검증 audit · 코드 변경 없음.**
LIVE / gate / exit / 파라미터 / A 봉인 전부 그대로 유지.
Phase 2 분리 commit 는 §4 체크리스트 전수 통과 후만.
