# F2_v1 — Frequent Small Net Edge Discovery Contract (PRE-REGISTRATION)

```
status:         DRAFT · pre-registration · §12 placeholder 5개 미결 · FREEZE 전
implementation: HOLD
authored:       2026-10-03
revision:       v1-draft2 (advisor 1 v2 네이밍 + advisor 2 "FROZEN 금지" 수용)
scope:          offline measurement only · NOT a trading strategy
touches:        no running code · no LIVE / gate / exit parameters
```

본 문서는 사전등록 계약(pre-registration). TRAIN 데이터를 보기 전에
feature / threshold / horizon / cost / event definition 을 전부 확정하고,
OOS 열린 후 수정 금지. 수정 시 F2_v2 + fresh untouched slice 로 재시작.

**중요**: 현재 상태는 `DRAFT` · §12 placeholder 5개가 미결이라 **FROZEN 표시
금지** (advisor 2). 그 5개 값이 코드/데이터 근거로 결정된 뒤 **FROZEN / F2_v1** 로
전환하고 그 commit SHA 가 pre-registration proof.

---

## 0. Purpose — 세 개의 분리된 실험 (NEVER merged)

Merging 하면 bad result 가 **un-diagnosable** (signal? filter? exit? cost?).
각 실험은 독립 verdict · 어느 조합도 ({YES,NO} × 3) 유효 결과.
어떤 verdict 도 A/A2/C1 재판정 · flagship / LIVE / gate / exit 변경 근거 아님
(A_CLEAN VALID 가 A verdict 를 되살리지 않는 것과 동일 원리로 독립).

### F2a — Filter Value (기존 CLM 구조 검증)

같은 base event 에서 CLM stack (BASE → … → FINAL) 이 강화될수록 **out-of-sample
executable expectancy per event 가 실제로 증가하는가**, 아니면 selectivity 가
event 개수만 줄이는가? 봇의 가장 오래된 미검증 전제 (`강한 필터링 ⇒ 더 좋은
거래`) 를 처음으로 데이터로 검증. (§2 상세)

### F2b — Early-exit Harvestability (DIAGNOSTIC · not a strategy)

같은 entry 에서 **pre-fixed 짧은 horizon exit** 가 **큰 승자 포기 비용까지
포함해** 총 net 을 현재 exit 대비 개선하는가? Flagship MFE 수수께끼 조사.

**핵심 규율**: **MFE 는 어떤 경우에도 predictor/input 금지** · diagnostic
outcome 에만 사용 (advisor 1 명시 · hindsight 변수).

이것은 **counterfactual DIAGNOSTIC** · deployable rule 아님. (§8 상세)

### F2c — Frequent Small Net Edge (사용자 아이디어 중립 검증 · primary alpha)

**유동성 충분 · 스프레드 좁은 market state** 에서, **반복 가능한 작은 after-cost
positive expectancy** 가 존재하는가? 사용자의 "작은 수익 자주 쌓기" 아이디어를
"작은 익절" 이 아니라 **"작은 net EV 가 반복되는 state 발견"** 으로 중립
검증.

**명시**: 사용자 아이디어는 **anti-pattern 이 아님** (advisor 2). 검증 없이
현재 LIVE 에 작은 TP 를 추가하는 것이 금지인 것이고, small-harvest 자체는
F2b/F2c 에서 검증할 **정당한 가설**. (Outcome §3 · Success §7)

---

## 1. Base universe + event de-duplication  (load-bearing)

- Raw unit = decision-time snapshot. 독립 trade 아님. 같은 코인을 ~10초 간격으로
  관측하면 하나의 price event 가 수십 번 복제.
- **Event dedup (TRAIN 보기 전 freeze):** 동일 coin 의 연속 candidate observation
  을 `EVENT_WINDOW` 안에서 **하나의 opportunity event 로 묶음** (연속 관측으로
  event 가 무한 연장되지 않는 종료 규칙 포함 · §12 에서 freeze).
  - 분석 단위 = **independent-ish opportunity event** · snapshot 아님.
- Dedup 없이 150만 scan 을 150만 n 으로 세면 n / CI / p-value / concentration
  이 전부 가짜로 좋아짐 (가장 흔한 분석 실수).

---

## 2. F2a Spec — Nested Filter Ladder (same base event · strict nesting)

```
BASE → V4 → BODY → WICK → VR → CLOSE_STRENGTH → CS40 → buy → fresh → sprd → FINAL
```

**중요**: 실제 코드의 evaluation order 가 다르면 **코드 순서를 그대로 contract
에 기록** · 연구 편의로 재배열 금지. 각 stage 는 이전 stage 의 **strict
subset** (동일 기간 · 동일 종목 · 동일 outcome/cost 정의).

리포트의 non-matched `*_fail +0.05%` cohort 는 `[효과 식별 불가]` 로 **evidence
로 사용 금지**.

### 단계별 측정 (per stage)

- `n` (dedup 후 event 수) · `retention%` (BASE 대비)
- 각 horizon (30/60/120/180s) 별 **executable net return** (§3 참조)
- `positive-event rate` · `median` · `p25` · `p75`
- `top-1 / top-5 event contribution` · `coin concentration` · `hour concentration`

### Deliverable

**단일 표**: 각 stage 별 `Δ executable EV` vs `Δ selectivity`.
필터의 조건부 평균이 올라가도 **기회 수가 크게 줄면 자본당 수익은 나빠질 수
있으므로**, 선별 효과와 경제적 가치를 **함께** 봄 (advisor 2 지적).

### F2a YES/NO 판정

- **YES**: stage 추가 시 **EV 가 monotone 또는 모노토닉에 가까움** · retention
  감소 대비 EV 증가가 유의.
- **NO**: EV 평탄/하락 · stage 는 event 수만 줄임.

**특히 조사**: `buy / fresh / sprd` gate (이번 리포트에서 29/34 pass 가 여기서
탈락). **금지**: "풀면 더 낫다" 결론 X (동일 비용·청산 기준 비교 전엔 판단
불가).

---

## 3. Outcome = Executable Return (not mid)

**Horizons fixed in advance** (**advisor 1+2 요구 수용 · 2026-10-03 추가** ·
사용자 "초단위 상승기류" 프레이밍 반영):

- **초단위 지평**: 5 / 10 / 15 s (**신규** · 짧은 상승기류 수확 가능성 측정)
- **분단위 지평**: 30 / 60 / 120 / 180 s (기존)
- 총 7개 horizon · 전부 post-hoc pick 금지

**Horizon selection rule (FREEZE 전 확정 필수 · advisor 2 2026-10-03 교정)**:
- OOS 열린 후 7개 중 "제일 좋은 시간" 고르는 방식 **금지** (multiple testing)
- Freeze 시 다음 중 하나 사전 선택 (사용자/advisor 결정):
  - (a) 7개 전부 **각각 독립 verdict** (per feature × horizon · 공동 보정 포함)
  - (b) **사전 지정 1개 horizon 만 primary** · 나머지는 diagnostic
  - (c) 초단위 (5/10/15) 중 1개 + 분단위 (30/60/120/180) 중 1개 **사전 선택**
- Freeze 이후 지평 추가 금지 (`F2_v2` + fresh untouched slice 필요)

### 정의

```
Entry        = executable BUY at t0
Exit_h       = executable SELL at t0 + h
NetReturn_h  = sell_proceeds − buy_cost − fees
```

### Price basis (두 execution variant 병행 보고 · advisor 2 요구 수용)

**이유** (advisor 2 명시 수용): "박리다매의 진짜 집은 모멘텀 추격이 아니라
마켓메이킹/유동성 제공. Upbit KRW maker rebate 없지만 **스프레드를 내는 대신
아끼는 구조로 비용 확 줄 수 있음**."

### Variant 1 — Taker (시장가 추격)

- Entry: `ask1(t0)` 또는 top-N depth-VWAP buy
- Exit: `bid1(t0+h)` 또는 top-N depth-VWAP sell
- 비용: **왕복 수수료 + fallback slippage 만 추가** (왕복 지연 효과 포함)
- ⚠ **비용 이중차감 금지** (advisor 1+2 2026-10-03 교정):
  - `ask1→bid1` 자체가 이미 **spread 반영** · depth-VWAP 도 호가 깊이 가격 불리함 포함
  - **"+ full spread" 추가 차감 금지** (이중 차감 · 이전 draft 오류 수정)
  - 추가 차감은 **그 가격 모델에 포함 안 된 것만** (수수료 · fallback 전환 지연 slippage)

### Variant 2 — Maker (지정가 · execution scenario upper-bound 성격)

- ⚠ **"실 실행" 주장 금지** (advisor 1+2 2026-10-03 교정):
  - `bid1 매수 → ask1 매도` 가정은 **execution scenario / upper-bound** 성격
  - 두 주문 **실제 체결 여부**는 queue position 없이는 모름
  - "가격이 찍혔다 = 내 주문이 체결됐다" X
  - 체결 가능성 재현 데이터 (L2 orderbook + 체결 time-series) 확보 후에만 강한 결론
- 모델링 필수 (전부 사전 freeze · §12):
  - Timeout window 내 체결 확률 가정 (실측 전엔 상한)
  - 미체결 시 **잔여 재고 손익** + 종료 방식 (강제 taker 전환 vs 다음 window 재시도)
  - Partial fill 처리
  - 체결 직후 adverse drift
- **임의 "non-fill 손실 cost" 상수 추가 금지** · 재고 처리 명시적 모델만

### 두 variant 비교 리포트 필수

각 feature × horizon 조합마다 **Taker net vs Maker net** 나란히 보고.
- Taker 로 음수 · Maker 로 양수 → 전략은 **메이커 구조 전용** · 모멘텀 추격 X
- 둘 다 양수 → 가장 robust (하지만 흔치 않음)
- 둘 다 음수 → 그 state 는 edge 없음

### Price basis 세부 (TOB proxy fallback 라벨 유지)

- **Preferred**: depth-aware VWAP at `FIXED_NOTIONAL_KRW` (§12 freeze)
- **Fallback**: top-of-book 만 가용 시 → `small-fill TOB executable proxy` 라벨
  한정 · 실 execution 동일 주장 X
- `ask1(t0) → bid1(t0+h)` 는 소액 체결 근사 · depth-VWAP 아님

### Fee treatment

- Upbit KRW 수수료 schedule freeze (왕복 명시)
- Maker/taker 차등 없음 (ARCHIVE 기록 · **maker rebate 가정 금지**)
- 메이커 체결 · 테이커 체결 동일 수수료 · 다만 **스프레드 지불 vs 아낌 차이** 만 반영

### 결측 처리

- Depth history 결측 구간 · tick 결측 구간은 해당 event drop 또는 TOB proxy 로
  명시 처리 (freeze 전 결정 · §12).

### 금지

- Mid-price return 을 executable 로 둔갑 X.
- Suffix 없는 `pnl` / `return` field 사용 X (§4).

---

## 4. Unit Contract (mandatory · 100× bug guard)

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

## 5. TRAIN / OOS + Leakage Control

### Split

- 시간순 · boundary 1회 freeze.
- 비율 §12 에서 결정 (C1 동일 60/40 가 baseline · 데이터 길이 보고 결정).

### Temporal / event purge (필수)

- Split boundary 주변 **purge window** (≥ `EVENT_WINDOW` · §12 에서 길이 확정).
- 같은 급등 episode 의 10:01 TRAIN · 10:03 OOS = 같은 사건 양쪽 본 것 → 금지.
- `episode_id` 또는 `cluster_id` 기반 group split 사용 가능 (freeze 전 결정).

### OOS 열린 후 금지 (strict)

- Feature / threshold / horizon / cost / event definition 수정 X.
- 수정 시 **F2_v2 + fresh untouched slice** 로 재시작.

---

## 6. Candidate Budget (multiple-comparison guard)

- Candidate family 수 **사전 freeze** (§12 에서 결정 · 예: 4개 이하).
- Threshold discovery TRAIN only · OOS candidate cap 고정.
- 같은 경제적 아이디어의 threshold 20개 ≠ 20 hypothesis (하나로 셈).
- **OOS 최고 결과 다시 골라내는 행위 금지** (p-hacking 가장 흔한 실수).

---

## 7. Success Criteria — Repeatability + Portfolio Realism

**Mean net > 0 만으로 SURVIVE 금지** (fat-tail 의존 구조는 F2c 철학 불일치).

### SURVIVE 조건 (전부 만족 · conjunctive)

필수:
1. `mean_net_pct > 0` after cost (양수 기대값 자체는 필수).
2. `cost_stress_net > 0` (수수료 +50% stress 후에도 양수 · §12 freeze).
3. `TRAIN → OOS direction consistency` (walk-forward ≥ 2/3 양수 · §12).
4. Concentration guards (전부 사전 freeze · §12):
   - `top_1_event_share` 상한
   - `top_5_event_share` 상한
   - `top_coin_share` 상한
   - `top_hour_share` 상한

### 함께 보고 (판단에 사용 · 필수 임계 아님)

- `median_net_pct` (분포 비대칭 확인 · advisor 2: 작은 수익 전략에 median > 0 이
  필수조건은 아님 · 사용자 선호 조건으로 추가 가능)
- `positive_event_rate`
- 손실 꼬리 분포 (worst-K / p1 / p5)
- 승률 · 거래 횟수 (**보조지표** · "자주 이기는 화면 ≠ 계좌에 남는 구조")

### 자본당 일일 net (advisor 2 핵심 지적)

- **단순 event 합산 금지** · 동시 포지션 · 중복 신호 · 사용 가능 자본 · 보유
  시간 반영한 **별도 포트폴리오 규칙** 필요.
- 포트폴리오 규칙 사전 freeze 안 됐으면 **건당 수익 + 이벤트 빈도만 보고** ·
  "자본당 일일 net" 주장 X.

### 핵심 문장 (advisor 2 · lock)

> "자주 이기는 화면보다, **작은 이익들이 큰 손실과 비용을 덮고 계좌에 남는
> 구조**."

### 박리다매 판정 (사용자 프레이밍 · diagnostic 성격)

사용자 아이디어 ("초단위 상승기류 수없이 매매 · 소액 수익 누적") 성립 여부
diagnostic (**SURVIVE 필수 조건 아님 · advisor 1+2 2026-10-03 교정**):

**필수 (SURVIVE gate)**:
- §7 본체 criteria (mean executable net > 0 · cost stress · concentration ·
  temporal consistency) 가 SURVIVE 결정자

**함께 보고 (박리다매 profile 판정 · diagnostic)**:
- **event frequency per day** (TRAIN · OOS 각각)
- **average executable net per event** (Taker + Maker 각각 · Maker 는 upper-bound 성격)
- **자본 대비 최대 손실 · 낙폭** (daily_net 기반 X · 자본 대비 로 측정)
- **상위 손실 기여도** (worst-K 분포)
- **time-of-day stability** · **cross-coin stability**
- (**옵션**) 포트폴리오 규칙 (동시 포지션 cap · 중복 신호 처리) **사전 freeze 된
  경우에만** calculated daily net 보고

**실패 명확화** (좁게 · "불가능" 단정 X):
- Taker/Maker variant 모두 §7 SURVIVE 미달 = 이 universe/horizon/procedure 에서
  후보 발견 못함 (**NOT**: 업비트 박리다매 전체 불가능)
- Taker 미달 · Maker 양수 = **upper-bound 로만 양수** · 실제 체결 모델링 전엔
  "메이커 전용 전략" 결론 금지 · 추가 queue position 검증 필수
- 자본 대비 큰 손실 1건이 수개월 수익 지움 = 박리다매 risk profile 불일치 ·
  하지만 edge 자체 존재 가능 (별도 리스크 관리 설계)

**금지**: `worst_single_loss < daily_net × K` 를 generic SURVIVE gate 로 사용 X ·
daily_net 이 0/음수일 때 불안정 · 자본·동시 포지션 규칙 사전 freeze 전엔
diagnostic 로만 사용 (advisor 2 명시 교정).

**실패 명확화** (NOT "불가능" · F2 §10 정신 유지):
- Taker 음수 + Maker 음수: 이 universe/horizon 에서 박리다매 **구조적 불가능**
- Taker 음수 + Maker 양수: **메이커 전용** · 모멘텀 추격 X · limit-only 구조
- 하루 1건 손실이 하루 수익 전부 지움: **단일 손실 리스크 과대** · 박리다매 아닌
  "분산된 소량 캐리" 구조

---

## 8. F2b Spec — Early-exit Harvestability (diagnostic)

**질문**: 같은 entry universe (예: 현재 flagship cohort) 에서, **pre-fixed
exit** 가 현재 exit 대비 총 net 을 개선하는가 · **큰 승자 포기 비용을 포함해**?

### 설계 (사전 freeze)

- **동일 cohort 재시뮬** (Q1 FINAL stage 또는 지정 subset).
- Pre-fixed exit variants (전부 사전 등록 · §12 에서 레벨 freeze):
  - Fixed-horizon exit: 30/60/120/180s 각각
  - Small-TP + paired SL + timeout: TP level × SL level 조합 (사전 등록)
- 각 variant 별 측정: mean / cost-stressed net · concentration · worst-K ·
  **"큰 승자 손실분"** (counterfactual loss · 현재 exit 대비).

### MFE 규율 (hard rule · advisor 1)

- MFE 및 **모든 post-entry peak 는 input/predictor 금지** · hindsight 변수.
- Diagnostic outcome 에만 사용 가능 (예: "TP X 가 MFE Y 를 몇 % 수확했나" 분석).

### F2b YES/NO 판정

- **A**: 모든 사전 등록 variant 가 현재 exit 대비 개선 못함 → 해당 variant 들로
  수확 불가 (advisor 2 정정: "작은 MFE 는 사후정보 · frequent-edge 불가능"으로
  확대하지 않음 · 이 cohort · 이 variant 들로 수확 못 함 까지만).
- **B**: 일부 variant 가 큰 승자 손실 포함 후에도 net 개선 → 수확 가능성 ·
  F2c 와 연결 (하지만 F2c 는 독립 universe · B 가 F2c 성공 보장 X).

### 섞지 X

- F2b 는 flagship cohort 재시뮬 · F2c 는 완전 분리 universe · 두 실험 verdict
  섞지 않음.

---

## 9. F2c Spec — Frequent-Edge Discovery (wider universe)

**질문**: F2a cohort (climax-triggered) **밖**에서, **유동성 충분 · 스프레드
좁은 market state** 에 **반복 가능한 작은 net+ expectancy** 가 존재하는가?

### Base universe (F2a 와 완전 분리)

- Scan 대상: Upbit KRW market full (climax trigger 와 무관).
- **Liquidity filter** (사전 freeze · §12):
  - Top-N bid depth ≥ `MIN_DEPTH_KRW`
  - Spread ≤ `MAX_SPREAD_PCT`
  - 두 임계는 **데이터 분포 기반 아닌 사전 가설 기반**으로 freeze.
- Event dedup §1 동일 적용.

### Candidate feature families (사전 등록 · §12 에서 최대 수 freeze)

후보 (경제적 가설 명시 필수):
- `order_flow_imbalance_short` (최근 N초 체결 매수/매도 imbalance)
- `depth_persistence` (depth refill/depletion rate)
- `microprice_displacement` (microprice vs mid 괴리)
- `cross_sectional_relative_strength` (시장 breadth 대비 상대 강세)

**이것들은 후보** · "좋다" 주장 아님 · 각각 독립 falsification 대상.
**경제적 가설이 명시되지 않은 feature 는 추가 금지** (지표 목록 대규모 탐색 =
과최적화 재현).

### Outcome

§3 동일 (executable net return @ 30/60/120/180s · depth-VWAP 우선).

### F2c YES/NO 판정

§7 전 criteria 통과 필요 (concentration · cost-stress · temporal consistency).

---

## 10. Result Taxonomy (frozen · narrow)

### Per experiment (F2a · F2b · F2c 각각 독립 verdict)

- **ZERO** — 사전등록 universe/event/cost/discovery procedure 에서 반복 가능한
  after-cost 양수 candidate 발견 못함.
  - **NOT**: "frequent-small-edge 전략은 영원히 불가능하다."
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
- F2 verdict 는 봉인된 결정 **재판정 근거 아님** (A_CLEAN VALID 가 A 를 되살리지
  않는 것과 동일).
- Post-hoc filter 금지 · 새 CLM threshold · exit tweak · rescue 추가 X.
- Measurement contract 가 alpha 코드보다 **먼저 테스트 통과** (efea7a4 교훈).
- Implementation 은 §12 placeholder freeze + 본 문서 **FROZEN / F2_v1** 승격
  이후에만 시작.
- 사용자 아이디어 **"anti-pattern 아님"** · 검증 없이 LIVE 적용이 금지 · F2b/F2c
  에서 중립 검증 (advisor 2 명시).

---

## 12. 미확정 플래그 (FROZEN 전 반드시 코드/데이터 근거로 결정)

**현재 DRAFT 상태** · 아래 값이 결정되어야 FROZEN / F2_v1 승격 가능.
Advisor 1 명시: "임의로 지금 수치를 만들어 넣지 않겠음 · 코드 근거로 결정할
수 있는 값은 코드 근거로 결정 · 연구자 위험선호가 필요한 값만 명시적 선택."

| 변수 | 결정 방식 |
|---|---|
| **데이터** 출처 · 수집 기간 · 호가 해상도 · 결측 처리 | 봇 export · scan log · tick/depth history 가용성 확인 |
| **기본 이벤트** 시작 조건 · 대표 snapshot 선택 방식 | 봇의 scan loop · check_fn 기록 기반 |
| **`EVENT_WINDOW`** · 이벤트 무한 연장 방지 종료 규칙 | 봇 데이터 수집 주기 + 종목별 재점검 간격 확인 |
| **`FIXED_NOTIONAL_KRW`** · 매수한 **동일 수량**의 매도 VWAP · 깊이 부족 처리 | 실 tiny-LIVE 주문금액 (6,000 KRW 수준) · depth history 결측률 확인 |
| **TRAIN/OOS 비율** · **purge window** 길이 | 데이터 길이 · event cluster 분포 보고 결정 (baseline 60/40 · purge ≥ EVENT_WINDOW) |
| **Candidate family 수** · OOS candidate cap · TRAIN 선택 절차 | 경제적 가설 수 상한 · multiple-comparison 보정 |
| **SURVIVE concentration 수치** (top_1/top_5/top_coin/top_hour 상한) | 연구자 위험선호 사전 freeze |
| **Cost-stress multiplier** (예: fee × 1.5) | 연구자 사전 freeze |
| **Walk-forward 창 수** · direction consistency 기준 | 데이터 길이 보고 결정 |
| **F2b TP/SL/timeout levels** · 레벨 수 | 사전 등록 · multiple-comparison 보정 |
| **F2c `MIN_DEPTH_KRW`** · **`MAX_SPREAD_PCT`** | 데이터 분포 X · 사전 가설 (사용자 거래 가능 조건) |
| **F2c feature family 최대 수** | multiple-comparison 보정 |
| **포트폴리오 규칙** (자본당 일일 net 계산 시) · 또는 포기 결정 | 동시 포지션 cap · 중복 신호 처리 · 보유시간 분포 보고 결정 · 없으면 건당·빈도만 보고 |
| **초단위 horizons (5/10/15s) 데이터 가용성** (NEW 2026-10-03 · **2차 정정**) | ⚠ **1차 claim** (`93de3b2`): "WebSocket 없음 · DATA_INSUFFICIENT 확정" → **grep 범위 과장** (bot.py 만 봄). **2차 실측**: `scalp/research/ws_recorder.py` 등 **수집기 코드 존재 확인** (recv_ts + exchange_ts 두 시각 · 원자료 불변 · _meta 끊김 기록 · 주문 분리). **정확한 현 상태**: 수집 구현 ✅ 존재 · 서버 실행 여부 ❌ 미확인 · 축적 데이터 ❌ 미확인. **다음**: 신규 구축 X · **서버에서 기존 수집기 실행/데이터 확인 (사용자)** |
| **Maker variant 체결 window** (NEW) · timeout + non-fill 모델 | 사전 freeze · 실 봇의 hybrid_buy timeout 1.2s 참조 가능 |
| **Maker non-fill 손실 가정** (NEW) · 미체결 시 가격 drift cost | 사전 freeze · 보수적 추정 (실측 전엔 upper bound) |
| **박리다매 `MIN_FREQ`** (NEW · "자주" 정의) | 연구자 사전 freeze (예: 하루 50건 · 100건 등) |
| **박리다매 `K` (worst-single-loss 상한 배수)** (NEW) | 연구자 위험선호 사전 freeze |

---

## 13. Deliverables (FROZEN 승격 후 implementation 단계)

1. **F2a report**: filter ladder 표 + 각 stage §2 지표 전부 + `Δ EV vs Δ selectivity`.
2. **F2b report**: variant × cohort 매트릭스 + counterfactual loss 분해 + MFE
   diagnostic (predictor X).
3. **F2c report**: feature family 별 OOS §7 전 criteria 결과 + verdict.
4. **각 experiment 별 verdict**: `{ZERO, CANDIDATE, SURVIVE}` × 3.
5. **Unit protocol compliance test** (문서화된 unit 변환이 전수 테스트 통과).

---

## 14. Audit trail

- 본 문서의 **FROZEN** commit SHA = pre-registration proof.
- DRAFT 수정 (freeze 전 · placeholder 채우기) 은 자유 · 각 수정은 commit.
- OOS 열린 후 수정 시 **`F2_v2` + fresh untouched slice** · 이전 결과는 archival.

---

## Status (재확인)

```
status:        DRAFT · pre-registration · §12 placeholder 미결
next step:     §12 placeholder 를 코드/데이터 근거로 채움 → FROZEN / F2_v1 승격
implementor:   별도 커밋 · 별도 PR · 본 문서는 pre-registration 전용
code change:   없음 (bot.py 무변경 · LIVE/gate/exit/파라미터/A 봉인 전면 유지)
```

**본 문서는 사전등록 계약 DRAFT 이며, 코드 변경 없음.**
LIVE / gate / exit / 파라미터 / A 봉인 전부 그대로 유지.
Implementation 은 FROZEN 승격 + 사용자/advisor 승인 후에만 별도 커밋.
