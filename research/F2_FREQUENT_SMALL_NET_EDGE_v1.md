# F2_v1 — Frequent Small Net Edge Discovery Contract (PRE-REGISTRATION)

```
status:         FROZEN-ON-APPROVAL
implementation: HOLD
authored:       2026-10-03
scope:          offline measurement only · NOT a trading strategy
touches:        no running code · no LIVE / gate / exit parameters
```

본 문서는 사전등록 계약(pre-registration). TRAIN 데이터를 보기 전에
feature / threshold / horizon / cost / event definition 을 전부 확정하고,
OOS 열린 후 수정 금지. 수정 시 F2_v2 + fresh untouched slice 로 재시작.

---

## 0. Purpose — 3 independent questions

이 문서는 하나의 결론을 내지 않음. 세 개의 분리 가능한 질문을 각각 답함.
세 질문을 섞어서 하나의 "F2 성공/실패" 로 묶으면, 결과가 나빠도 **진입 신호 /
필터 / 청산 / 비용** 중 무엇이 원인인지 분리 불가 (advisor 2).

- **Q1 Filter-value.** CLM 필터 stack 이 강화될수록 (BASE → … → FINAL),
  **out-of-sample executable expectancy per event** 가 실제로 증가하는가,
  아니면 selectivity 가 event 개수만 줄이는가? 봇의 가장 오래된 미검증 전제
  (`강한 필터링 ⇒ 더 좋은 거래`) 를 처음으로 데이터로 검증.
- **Q2 Early-TP counterfactual.** 같은 진입에서 **작은 익절**이 **큰 승자 감소분
  까지 포함해** 총 순수익을 개선하는가? MFE 는 사후 정보이므로 "작은 move 가
  존재했다" ≠ "실시간으로 수확 가능." Q2 는 수확 가능성을 직접 테스트.
- **Q3 Frequent-edge.** **유동성이 충분하고 스프레드가 좁은 시장 상태**에서,
  **반복 가능한 작은 가격 움직임을 실제 비용 이후 양수로** 거래할 수 있는 영역이
  존재하는가? 현재 flagship 과 **완전히 분리된** 더 넓은 universe 를 대상.

Q1 · Q2 · Q3 각각 {YES, NO} 조합 전부가 유효 결과. 어떤 verdict 도
A/A2/C1/C2 재판정 · flagship 변경 근거 아님. 결과는 A_CLEAN VALID 가
A verdict 를 되살리지 않는 것과 같은 원리로 봉인된 결정과 독립.

---

## 1. Base universe + event de-duplication  (load-bearing)

- Raw unit = decision-time snapshot. **독립 trade 아님.** 같은 코인을 ~10초
  간격으로 관측하면 하나의 price event 가 수십 번 복제됨.
- **Event dedup (TRAIN 보기 전 freeze):** 동일 coin 의 연속 candidate observation
  을 `EVENT_WINDOW` 안에서 **하나의 opportunity event 로 묶음**.
  - `EVENT_WINDOW = 180s` (한 번 freeze · tuning 금지).
  - 분석 단위 = **independent-ish opportunity event** · snapshot 아님.
- Dedup 없이 150만 scan 을 150만 n 으로 세면 n / CI / p-value / concentration
  이 전부 가짜로 좋아짐 (가장 흔한 분석 실수).

---

## 2. Q1 — Nested Filter Ladder (same base event · strict nesting)

```
BASE → V4 → BODY → WICK → VR → CLOSE_STRENGTH → CS40 → buy → fresh → sprd → FINAL
```

**중요**: 실제 코드의 evaluation order 가 다르면 **코드 순서를 그대로 contract 에
기록** · 연구 편의로 재배열 금지. 각 stage 는 이전 stage 의 **strict subset**
(동일 기간 · 동일 종목 · 동일 outcome/cost 정의). 리포트의 non-matched
`*_fail +0.05%` cohort 는 `[효과 식별 불가]` 로 **evidence 로 사용 금지**.

### 단계별 측정 (per stage)

- `n` (dedup 후 event 수)
- `retention%` (BASE 대비)
- 각 horizon 별 **executable net return** (§3 참조)
- `positive-event rate`
- `median` · `p25` · `p75`
- `top-1 / top-5 event contribution` (총 수익 중 상위 n건 비중)
- `coin concentration` (단일 종목 비중)
- `hour / time concentration`

### Deliverable

**단일 표**: 각 stage 별 `Δ executable EV` vs `Δ selectivity`.

### Q1 YES/NO 판정

- **YES**: stage 가 추가될수록 **중앙값·positive rate** 가 유의하게 개선되고,
  retention 감소 대비 EV 증가가 monotone 또는 모노토닉에 가까움.
- **NO**: EV 가 평탄하거나 하락 · stage 는 event 수만 줄임.

### 특히 조사할 gate 3개 (이번 리포트에서 29/34 pass 가 여기서 탈락)

- `buy` gate (BLOCK 집계)
- `fresh` gate
- `sprd` gate

**금지**: 이 세 gate 를 "풀면 더 낫다" 로 결론 X (동일 비용·청산 기준
비교 전에는 판단 불가 · advisor 2 명시).

---

## 3. Outcome = Executable Return (not mid)

**Horizons fixed in advance**: 30 / 60 / 120 / 180s **전부** (post-hoc pick 금지).

### 정의

```
Entry        = executable BUY at t0
Exit_h       = executable SELL at t0 + h
NetReturn_h  = sell_proceeds − buy_cost − fees
```

### Price basis (우선순위)

1. **Preferred**: **depth-aware VWAP** at **fixed KRW notional** (예: 현 tiny-LIVE
   정합 6,000 KRW).
   - Buy VWAP(t0, notional) → Sell VWAP(t0+h, notional)
   - Spread + depth/slippage 둘 다 outcome 에 포함.
2. **Fallback**: top-of-book 만 사용 가능 시 → **label 을 `TOB executable proxy`
   로 한정** · 실 execution 과 동일 주장 X (advisor 2 명시).
   - `ask1(t0) → bid1(t0+h)` 는 소액 체결 근사일 뿐 · depth-VWAP 과 동등 X.
3. **Limit-order 가정 금지** (별도 contract 로): 지정가 가정하면 **미체결 + 체결
   후 불리한 움직임** 둘 다 포함해야 함 · 이번 F2_v1 scope 밖.

### Fee treatment

- 현재 Upbit KRW 수수료 schedule freeze (왕복 명시 · 변경 시 F2_v2).
- Maker/taker 차등 없음 (ARCHIVE 기록).

### 금지

- `pnl` / `return` suffix 없는 field 사용 금지.
- Mid-price return 을 executable 로 둔갑시키지 않음.

---

## 4. Q2 — Early-TP Counterfactual (secondary diagnostic)

**질문**: 같은 거래 universe 에서, 사전고정 익절 (예: +0.10% / +0.15% / +0.20%)
이 **큰 승자를 조기 커팅 하는 손해까지 포함해** 총 순수익을 개선하는가?

### 설계

- **동일 cohort** (Q1 의 FINAL stage · 또는 지정 subset) 에서 각 TP 레벨 별
  재시뮬.
- MFE 는 **candidate predictor 로 사용 금지** (사후 정보 · advisor 1 명시).
- MFE 는 diagnostic (A/B 판정 보조) 로만 사용 가능.
- 각 TP 레벨 별: mean / median net · positive rate · top-event share ·
  "큰 승자 손실분" (counterfactual loss).

### Q2 YES/NO 판정

- **A (NO)**: 모든 TP 레벨에서 조기 익절이 총 순수익을 개선 못함 → 작은 MFE 는
  수확 불가 · frequent-small-edge 구조는 현 cohort 에서 작동 안 함.
- **B (YES)**: 일부 TP 레벨이 큰 승자 손실 포함 후에도 순수익 개선 →
  decision-time/early-path state 가 작은 move 를 수확 가능 · Q3 와 연결됨.

**주의**: Q2 는 Q1/Q3 와 섞지 않음. 현 flagship cohort 의 harvestability 만 봄.

---

## 5. Q3 — Frequent-Edge Discovery (wider universe)

**질문**: Q1 cohort (climax-triggered) **밖**에서, **유동성 충분 · 스프레드
좁은 시장 상태** 에 **반복 가능한 작은 net+ expectancy 영역**이 존재하는가?

### Base universe (Q1 와 완전 분리)

- Scan 대상: Upbit KRW market full (Q1 climax trigger 와 무관)
- **Liquidity filter** (사전 freeze):
  - Top-10 bid depth ≥ `MIN_DEPTH_KRW` (예: 50백만 KRW)
  - Spread ≤ `MAX_SPREAD_PCT` (예: 0.15%)
  - 두 임계는 TRAIN 보기 전에 **데이터 분포 기반 아닌 사전 가설 기반**으로 고정.
- Event dedup §1 동일 적용 (`EVENT_WINDOW=180s`).

### Candidate feature families (사전 등록 · 4개 이하)

- `order_flow_imbalance_short` (최근 N초 매수/매도 체결 imbalance · N 고정)
- `depth_persistence` (depth refill/depletion rate · 측정 구간 고정)
- `microprice_displacement` (microprice vs mid 괴리)
- `cross_sectional_relative_strength` (시장 breadth 대비 상대 강세)

**이것들은 후보** · "좋다" 주장 아님 · 각각 독립 falsification 대상.

### Outcome

§3 동일 (executable net return @ 30/60/120/180s · depth-VWAP 선호).

### Q3 YES/NO 판정

§7 반복성 조건 충족 필요 (아래).

---

## 6. Unit Contract (mandatory · 100× bug guard)

내부 표준 **하나만**. Field 는 suffix 필수:

```
return_frac  = 0.002   (소수)
return_pct   = 0.2     (퍼센트 포인트)
return_bp    = 20      (베이시스 포인트)
```

### 금지

- Suffix 없는 `pnl` / `return` / `net`.
- **값 크기로 unit 추론 heuristic** (`abs<1` 같은 자동 변환 · efea7a4 교훈).

### Legacy conversion

- 기존 shadow 통계 (`total_pnl` 등) 를 F2 input 으로 쓸 때 **원 unit 명시 ·
  변환 한 번만 · 변환 함수 테스트 커버**.

---

## 7. Success Criteria — Repeatability as part of outcome

**Mean net > 0 만으로 SURVIVE 금지** (advisor 1 명시).

예: 1,000 event 중 999 × −0.02% + 1 × +30% → mean > 0 이지만 **F2 철학상 실패**
(1 runner 가 캐리 · "작은 EV 반복" 아님 · fat-tail 의존).

### SURVIVE 조건 (전부 만족 필요 · conjunctive)

1. `mean_net_pct > 0` AND `median_net_pct > 0` (분포 비대칭 확인)
2. `positive_event_rate > 0.5` (사전 freeze · 50% 이상 양수)
3. `top_1_event_share < 10%` AND `top_5_event_share < 30%` (fat-tail 의존 X)
4. `top_coin_share < 20%` (단일 종목 캐리 X)
5. `top_hour_share < 25%` (시간대 캐리 X)
6. `cost_stress_net > 0` (수수료 +50% stress 후에도 양수)
7. `TRAIN→OOS direction consistency` (walk-forward ≥ 2/3 양수)
8. **자본당 일일 net > 0** (advisor 2 · "하루 자본 대비 순수익" · 기회비용 반영)

### 보조 지표 (성공 조건 아님)

- 거래 횟수 · 승률 (advisor 2: "자주 이기는 화면 ≠ 계좌에 남는 구조")

### Advisor 2 핵심 문장 (lock)

> "자주 이기는 화면보다, **작은 이익들이 큰 손실과 비용을 덮고 계좌에 남는 구조**."

---

## 8. TRAIN / OOS + Leakage Control

### Split

- 시간순 · boundary 1회 freeze.
- 비율: TRAIN 60% / OOS 40% (C1 동일 규율).

### Temporal / event purge (필수)

- Split boundary 주변 **purge window** (= `EVENT_WINDOW` 와 동일 또는 그 이상).
- 같은 급등 episode 의 10:01 TRAIN · 10:03 OOS = 같은 사건 양쪽 본 것 → 금지.
- `trade_cluster_id` 또는 `episode_id` 기반 group split 사용 가능 (freeze 전 결정).

### OOS 열린 후 금지 (strict)

- Feature 수정
- Threshold 수정
- Horizon 수정
- Cost treatment 수정
- Event definition 수정

→ 어느 하나 수정 시 **F2_v2 + fresh untouched slice** 로 재시작.

---

## 9. Candidate Budget (multiple-comparison guard)

- Candidate family 수 **사전 freeze** (Q3: 4개 · Q1: filter stage 수 = ~10 ·
  Q2: TP level ≤ 5).
- Threshold discovery TRAIN only · OOS candidate cap 고정.
- 같은 경제적 아이디어의 threshold 20개 ≠ 20 hypothesis (하나로 셈).
- **OOS 최고 결과 다시 골라내는 행위 금지** (p-hacking · 가장 흔한 실수).

---

## 10. Result Taxonomy (frozen · narrow)

### Per question (Q1 · Q2 · Q3 각각 독립 verdict)

- **ZERO** — 사전등록 universe/event/cost/discovery procedure 에서 반복 가능한
  after-cost 양수 candidate 발견 못함.
  - **NOT**: "frequent-small-edge 전략은 불가능하다."
  - **IS**: "이 contract 의 frozen scope 에서 못 찾았다."
- **CANDIDATE** — TRAIN 사전등록 조건 통과 · untouched OOS 평가 대상 지정.
  - **NOT**: edge 입증.
- **SURVIVE** — untouched OOS 에서 §7 전 criteria 통과 · forward shadow 검증 가치.
  - **NOT**: LIVE 승격 (별도 forward shadow + 추가 untouched slice 필요).

### C1 교훈 재적용

- OOS 본 후 threshold rescue **금지**.
- ZERO 수용 · rescue 없음.

---

## 11. Guardrails (명시적 반복)

- **LIVE / gate / exit / TP / SL / trail / sizing 전면 무변경.**
- A = REJECTED_FINAL/CLOSED · A2 = TERMINATED_INFEASIBLE · C1 closed · 봉인 유지.
- F2 verdict 는 봉인된 결정 **재판정 근거 아님**.
- Post-hoc filter 금지 · 새 CLM threshold · exit tweak · rescue 추가 X.
- Measurement contract 가 alpha 코드보다 **먼저 테스트 통과** (efea7a4 교훈).
- Implementation 은 이 문서 **승인 + freeze 이후에만** 시작.

### 사용자 아이디어에 대한 중립 표기 (advisor 2)

사용자의 "작은 수익 자주 쌓기" 아이디어는 **anti-pattern 이 아님** ·
검증 없이 LIVE 에 적용하는 것이 문제 · offline 비교는 **타당한 연구 질문** ·
Q3 가 바로 이 질문을 중립적으로 검증.

---

## 12. 미확정 플래그 (contract freeze 전 사용자 결정 필요)

아래 값은 **구현 시작 전** 반드시 freeze (여기 기재된 것은 **제안값** · 사용자
또는 advisor 승인 후 확정):

| 변수 | 제안값 | 비고 |
|---|---|---|
| `EVENT_WINDOW` | 180s | 동일 코인 observation dedup window |
| Fixed notional | 6,000 KRW | 현 tiny-LIVE 정합 |
| Depth source | depth history | 가능 여부 확인 필요 · 없으면 TOB proxy |
| Q3 `MIN_DEPTH_KRW` | 50백만 | 유동성 하한 |
| Q3 `MAX_SPREAD_PCT` | 0.15% | 스프레드 상한 |
| Q3 feature 수 | 4 (사전 등록) | order-flow · depth · microprice · cross-sectional |
| Q2 TP levels | 0.10 / 0.15 / 0.20% | 3개 사전 등록 |
| TRAIN/OOS 비율 | 60/40 | 시간순 |
| Purge window | 180s (= EVENT_WINDOW) | 또는 그 이상 |
| Positive rate 기준 | > 0.5 | 사전 freeze |
| Top-1 event share | < 10% | repeatability guard |
| Top-5 event share | < 30% | repeatability guard |
| Top-coin share | < 20% | diversification guard |
| Top-hour share | < 25% | temporal guard |
| Cost stress | fee × 1.5 | stress multiplier |
| Walk-forward 창 | 3 (TRAIN 내부) | direction consistency ≥ 2/3 |

---

## 13. Deliverables (contract freeze 후 implementation 단계)

1. **Q1 report**: filter ladder 표 + 각 stage 별 §2 지표 전부.
2. **Q2 report**: TP level × cohort 매트릭스 + counterfactual loss 분해.
3. **Q3 report**: feature family 별 OOS §7 전 criteria 결과 + verdict.
4. **각 question 별 verdict**: `{ZERO, CANDIDATE, SURVIVE}` × 3.
5. **Unit protocol compliance test** (문서화된 unit 변환이 전수 테스트 통과).

---

## 14. Audit trail

- 본 문서 git commit SHA = freeze 시점 pre-registration proof.
- 이후 수정 (OOS 열기 전) 시 **문서 재발행** (`F2_v1.1`) · 변경 이유 기록.
- OOS 열린 후 수정 시 **`F2_v2` + fresh untouched slice** · 이전 결과는 archival.

---

## Status

```
status:       FROZEN-ON-APPROVAL
next step:    사용자 (또는 advisor) 승인 → §12 미확정 값 freeze → implementation GO
implementor:  별도 커밋 (별도 PR) · 본 문서는 pre-registration 전용
```

**본 문서는 사전등록 계약이며, 코드 변경 없음.**
LIVE / gate / exit / 파라미터 / A 봉인 전부 그대로 유지.
