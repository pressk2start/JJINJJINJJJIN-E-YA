# F3_v1 — Order-flow Persistence Discovery Contract (PRE-REGISTRATION)

```
status:         DRAFT · pre-registration · §12 placeholder 미결 · FREEZE 전
implementation: HOLD
authored:       2026-10-03
scope:          offline measurement only · NOT a trading strategy
touches:        no running code · no LIVE / gate / exit parameters
```

본 문서는 F2 와 **독립 사전등록 계약** · 경제적 가설 명시 · F2 와 cohort/feature/
결과 섞지 않음. TRAIN 데이터 보기 전에 feature/threshold/horizon/cost/event
definition 전부 확정 · OOS 열린 후 수정 금지.

**정보원 독립성** (advisor 2): F2 = selection density / small-net reproducibility ·
F3 = **microstructure 지속성** · 서로 다른 경제적 가설.

---

## 0. Purpose — Order-flow Persistence Hypothesis

### 경제적 가설

가격 변화는 **aggressive order flow (public market taker)** 에 반응하지만,
flow 자체는 **단기 지속성 (clustering)** 을 가진다. 매수 공격이 몰리면 (depth
depletion + microprice 상승) 다음 수십초 동안도 매수 공격이 이어지고, 그
방향으로 가격이 잠깐 더 간다 — 특히 **spread 가 좁고 depth 가 두터운
state 에서** 단기 continuation (30-120s) 를 예측할 수 있다.

### 왜 이 정보가 미래 가격에 영향 주는가 (advisor 2 요구 · 사전 설명)

- Taker aggression 은 **informed trading** 또는 **urgent flow** 신호 · 시장
  참여자들이 가격 조정 전에 매수/매도 완료 원함.
- Depth depletion (top-N bid 가 빠르게 소진) + 느린 refill = 공급 부족 신호.
- Microprice vs mid 괴리 = quote-weighted imbalance · 체결가격 bias 선행.
- 이 3가지는 **학술적으로 입증된 단기 continuation 메커니즘** (Cont-Larrard 등
  limit order book literature).

**단**: 학술 증거 ≠ Upbit KRW 2026 regime 에서 재현. 그래서 F3 가 **중립 검증**.

---

## 1. Base universe + event dedup

- Scan 대상: **유동성 충분 · 스프레드 좁은** Upbit KRW market
  - F2c §9 와 동일 liquidity filter (사전 freeze · §12):
    - Top-N bid depth ≥ `MIN_DEPTH_KRW`
    - Spread ≤ `MAX_SPREAD_PCT`
- **Climax trigger 와 무관** (F2a cohort 밖 · 완전 분리)
- Event dedup (F2 §1 동일 메커니즘):
  - `EVENT_WINDOW` 안에서 동일 coin 연속 observation 하나로 묶음
  - Window 사전 freeze (§12)

---

## 2. Candidate Feature Families (사전 등록 · 최대 4개)

**각 feature 는 경제적 가설 명시 필수** · 명시 안 된 feature 추가 금지.

### F3-α — Aggressive Flow Imbalance (short-term)

**가설**: 최근 N초 체결 중 매수 taker 비중이 매도 taker 비중을 유의하게 초과하면
다음 30-60초 가격은 continuation.

- 입력: 최근 N초 (예: 10s · 30s) 체결 데이터
- 측정: `(buy_taker_vol - sell_taker_vol) / total_vol` (dimensionless frac)
- N 사전 freeze (§12)

### F3-β — Depth Depletion / Refill Rate

**가설**: Top-N bid depth 가 급격히 소진된 후 refill 느리면 → 공급 부족 · 상승 압력
지속.

- 입력: 최근 M초 top-N bid depth 시계열
- 측정: `depletion_rate = (depth(t-M) - depth(t)) / depth(t-M)` ·
       `refill_rate` = depth(t+K) - depth(t) 변화율
- M · N · K 사전 freeze (§12)

### F3-γ — Microprice Displacement

**가설**: Microprice (quote-weighted mid: `(bid*ask_vol + ask*bid_vol)/(bid_vol+ask_vol)`)
가 mid 대비 상승 bias 를 가지면 → 체결가격 다음 수십초 mid 보다 상승 선행.

- 입력: snapshot t 의 orderbook
- 측정: `(microprice - mid) / mid` (dimensionless frac)

### F3-δ — Trade Clustering (bursty)

**가설**: 매수 taker burst (단위 시간당 trade count 급증) 는 informed/urgent flow
신호 · 다음 수십초 continuation.

- 입력: 최근 N초 trade timestamp 분포
- 측정: `trade_rate_zscore = (recent_rate - baseline_rate) / baseline_std`

**candidate budget**: 4개 feature × horizons × threshold grid · 하지만
**같은 경제적 아이디어의 threshold 20개 ≠ 20 hypothesis** (§6 F2 동일).

---

## 3. Outcome = Executable Return (F2 §3 동일)

- Horizons fixed: **30 / 60 / 120 / 180s** (post-hoc pick 금지)
- `NetReturn_frac_h = (sell_proceeds - buy_cost - fees) / buy_cost`
- Preferred: **depth-aware VWAP** at fixed `FIXED_NOTIONAL_KRW` (§12)
- Fallback: `small-fill TOB executable proxy` 라벨 한정 (ask1→bid1)
- Maker 가정 시: non-fill · partial fill · adverse drift 반영 의무

**금지**: Mid return 을 executable 로 둔갑 X · suffix 없는 pnl/return X (§4).

---

## 4. Unit Contract (F2 §4 동일 · 100× 버그 가드)

- Suffix 필수: `*_frac` / `*_pct` / `*_bp`
- 값 크기 heuristic 금지

---

## 5. TRAIN / OOS + Leakage Control (F2 §5 동일)

- 시간순 split · boundary freeze
- Temporal/event purge (`EVENT_WINDOW` 이상)
- OOS 열면 feature/threshold/horizon/cost/event-def frozen · 수정 시 F3_v2

---

## 6. Candidate Budget (F2 §6 동일)

- Family 4개 사전 freeze
- Threshold TRAIN only · OOS cap 고정
- OOS 재선택 금지

---

## 7. Success Criteria (F2 §7 동일 · repeatability as part of outcome)

### SURVIVE (전부 만족 · conjunctive · F2 와 동일 criteria)

1. `mean_net_pct > 0` after cost
2. `cost_stress_net > 0` (fee × 1.5)
3. `TRAIN → OOS direction consistency` (walk-forward ≥ 2/3)
4. Concentration guards (전부 §12 freeze):
   - top_1_event_share 상한
   - top_5_event_share 상한
   - top_coin_share 상한
   - top_hour_share 상한
5. **각 feature 별 독립 verdict** (feature 간 혼합 verdict 금지)

### 함께 보고 (판단 보조 · 필수 임계 아님)

- Median · positive-event rate · 손실 꼬리 분포

### 자본당 일일 net (F2 §7 동일)

- 포트폴리오 규칙 없으면 건당 + 빈도만 보고 (단순 합산 금지)

---

## 8. 결과 taxonomy (F2 §10 동일)

**per feature × horizon 조합마다 독립 verdict**:
- **ZERO** · **CANDIDATE** · **SURVIVE**
- SURVIVE 도 forward shadow 검증 필요 · LIVE 승격 아님

**F3 전체 verdict 금지** · 4개 feature × 4 horizon = 16 조합 각각 독립.

---

## 9. F2 와의 관계 (명시적 분리)

- F3 는 F2 의 sub-case 아님 · **독립 cohort · 독립 feature · 독립 verdict**.
- F2 Q1/Q2/Q3 결과가 어떻든 F3 결과와 섞지 않음.
- F3 SURVIVE 가 F2c SURVIVE 를 보장 X (다른 universe · 다른 feature).
- 공통점: §3-§7 methodology 공유 (contract-first · unit protocol · temporal
  purge · repeatability · 등)

---

## 10. Guardrails (F2 §11 동일 · 명시적 반복)

- **LIVE / gate / exit / TP / SL / trail / sizing 전면 무변경.**
- A = REJECTED_FINAL/CLOSED · A2 = TERMINATED_INFEASIBLE · C1 closed · 봉인 유지.
- F3 verdict 는 봉인된 결정 **재판정 근거 아님**.
- Post-hoc filter 금지 · 새 CLM threshold / exit tweak / rescue 추가 X.
- Implementation 은 §12 placeholder freeze + 본 문서 **FROZEN / F3_v1** 승격
  이후에만 시작.
- 사용자 아이디어 중립 검증 · F3 는 F2c 와 다른 각도에서 "작은 edge 가 반복되는
  state" 를 microstructure 측면에서 탐색.

---

## 11. 데이터 요구사항

### 필요한 데이터

- **Orderbook snapshots** (최소 top-5 bid/ask depth · 가능하면 top-20)
- **Trade stream** (체결 timestamp · 가격 · volume · 매수/매도 taker 구분)
- **Tick-level 또는 1초 bar** 데이터 (microprice 계산용)

### 봇 데이터 가용성 확인 결과 (grep 실측 2026-10-03) · DATA_INSUFFICIENT 확정

**실측 결과** (bot.py grep):
- WebSocket **없음** (REST polling 만 · grep 0건)
- Orderbook snapshot **영구 저장 X** (매 scan 메모리 fetch · 디스크 X)
- Trade stream (taker side) **저장 X**
- 유일 저장: `data/live_trades.jsonl` (실 체결 결과만 · schema_v=2)
- Scan 해상도: 평균 ~7초 · p95 ~12초

→ **F3 전체 features 전부 `DATA_INSUFFICIENT`** · 현 저장 체계로 불가능

### 선결 조건 (별도 티켓 · 사용자 승인 필요)

**WebSocket 틱 수집기 신규 구축** 이 F3 선결 인프라:
- Scope: Upbit WebSocket API (orderbook + trade stream) subscribe
- 저장: tick/orderbook snapshot (예: 1초 또는 더 정밀) · 로컬 디스크 또는 DB
- Universe: F3-α/β/γ/δ 대상 종목 (유동성 filter 통과)
- 실매매 로직 **완전 분리** (별도 process · 매매 봇 영향 0)
- 수집 기간: F3 TRAIN+OOS 커버 (최소 수주)
- 주문 지연 (REST API latency) 도 함께 계측 · 감지 빠름 ≠ 주문 빠름

**이 수집기 없이는 F3 implementation 자체 불가능** · F3 §12 freeze 전제 조건.

---

## 12. 미확정 플래그 (FROZEN 전 결정 필요)

**현재 DRAFT** · 아래 값 결정 후 FROZEN / F3_v1 승격:

| 변수 | 결정 방식 |
|---|---|
| **데이터 가용성** (orderbook depth · trade taker · 저장 기간) | 봇 코드 확인 · 데이터 디렉토리 샘플링 |
| `EVENT_WINDOW` | 봇 scan 주기 · 종목별 재점검 간격 (F2 와 동일 값 가능) |
| `FIXED_NOTIONAL_KRW` | 실 tiny-LIVE 주문금액 (F2 와 동일 가능) |
| `MIN_DEPTH_KRW` · `MAX_SPREAD_PCT` | 사전 가설 (유동성 기준) · 데이터 분포 기반 X |
| F3-α 체결 lookback `N` | 사전 freeze (예: 10s · 30s · 60s 중 1개) |
| F3-β depth lookback `M`·`N`·refill `K` | 사전 freeze |
| F3-γ microprice formula 변형 | 사전 freeze (standard: `(bid*ask_vol + ask*bid_vol)/(bid_vol+ask_vol)`) |
| F3-δ baseline window | 사전 freeze (예: 300s rolling) |
| TRAIN/OOS 비율 + purge window | 데이터 길이 보고 결정 |
| SURVIVE concentration 수치 | 연구자 위험선호 사전 freeze |
| Walk-forward 창 수 | 데이터 길이 보고 결정 |
| 포트폴리오 규칙 (자본당 일일 net 계산 시) | 있으면 명시 · 없으면 건당+빈도만 |

---

## 13. Deliverables (FROZEN 후 implementation 단계)

1. **F3 데이터 가용성 report**: orderbook depth · trade taker · 저장 기간 실측
2. **각 feature (α · β · γ · δ) 별 OOS verdict** × 4 horizons = 최대 16 조합
3. **Concentration · temporal consistency · cost stress** 분석
4. **F2 와의 공통 cohort 분석** (혹시 F3 edge 가 F2 cohort 와 겹치는지)
5. **Unit protocol compliance test** (전수)

---

## 14. Audit trail

- 본 문서의 **FROZEN** commit SHA = pre-registration proof
- DRAFT 수정 (freeze 전 · placeholder 채우기) 은 자유 · 각 수정은 commit
- OOS 열린 후 수정 시 **F3_v2 + fresh untouched slice** · 이전 결과 archival

---

## Status

```
status:        DRAFT · pre-registration · §12 placeholder 미결
next step:     데이터 가용성 확인 → §12 placeholder 결정 → FROZEN / F3_v1 승격
implementor:   별도 커밋 · 별도 PR · 본 문서는 pre-registration 전용
code change:   없음 (bot.py 무변경)
```

**본 문서는 사전등록 계약 DRAFT · 코드 변경 없음.**
LIVE / gate / exit / 파라미터 / A 봉인 전부 그대로 유지.
F3 implementation 은 FROZEN 승격 + 사용자/advisor 승인 후에만 별도 커밋.
