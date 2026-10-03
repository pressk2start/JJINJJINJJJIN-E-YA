# F4_v1 — Cross-sectional Relative Strength Discovery Contract (PRE-REGISTRATION)

```
status:         DRAFT · pre-registration · §12 placeholder 미결 · FREEZE 전
implementation: HOLD
authored:       2026-10-03
scope:          offline measurement only · NOT a trading strategy
touches:        no running code · no LIVE / gate / exit parameters
```

본 문서는 F2 · F3 와 **독립 사전등록 계약**. F3 (microstructure) 와 다른 정보원 ·
**횡단면 (cross-sectional)** 신호. TRAIN 보기 전 전부 freeze · OOS 열린 후 수정
금지. 수정 시 F4_v2 + fresh untouched slice.

**정보원 독립성** (advisor 2 반복): F2 = selection density · F3 = microstructure
지속성 · **F4 = 횡단면 상대 강세**. 서로 다른 경제적 가설.

---

## 0. Purpose — Cross-sectional Relative Strength Hypothesis

### 경제적 가설

가격 변화에는 **시장 공통 요인** (BTC 상승 → 알트 동반) 과 **종목 특이 요인**
(informed buying · sector rotation · 뉴스 선반영) 이 섞여 있다. 단순 "이 코인이
오르고 있다" 는 신호는 BTC 가 같이 오를 때 reliable 하지 않다. 하지만 **시장
공통 효과를 제거한 뒤에도 유의하게 강한 종목** 은 **informed demand** 또는
**지속적 매수 압력** 신호일 가능성이 있고, 이것이 다음 수십초~수분의 continuation
을 예측할 수 있다.

### 핵심 질문 (advisor 1 명시 수용)

> "시장 전체가 같이 오르는 효과를 제거한 뒤에도, 동시간대 KRW universe 대비
> 상대적으로 강한 종목의 초과강도가 이후 executable return 을 예측하는가?"

### 왜 이 정보가 미래 가격에 영향 주는가 (advisor 2 요구 · 사전 설명)

- **Informed demand**: 특정 종목에 집중된 매수는 뉴스/루머/내부 정보 선반영.
- **Sector rotation**: 자본이 BTC → 알트 → 특정 섹터로 이동 · 상대 강세는 그
  섹터로의 자본 유입 신호.
- **Cross-asset flow**: KRW market 전체 상대적으로 강세인 종목은 USDT/USD 시장
  에서도 유사 flow 가능성 · lead-lag.
- **학술 근거**: cross-sectional momentum literature (Jegadeesh-Titman 1993 ·
  short-horizon reversal 과 long-horizon momentum 공존).

**단**: 학술 증거 ≠ Upbit KRW 2026 단기 regime 재현. F4 가 중립 검증.

**F3 와의 차이**: F3 는 종목 자체의 microstructure flow · F4 는 시장 cross-section
에서 상대적 위치. 둘은 정보원 독립 (하지만 §9 통계 독립성 cross-check 필요).

---

## 1. Base universe + event dedup

- **Base universe**: Upbit KRW 전 종목 동시 scan · F2 climax trigger 와 무관
- **Liquidity filter** (사전 freeze · §12 · F2c · F3 와 동일 기준 가능):
  - Top-N bid depth ≥ `MIN_DEPTH_KRW`
  - Spread ≤ `MAX_SPREAD_PCT`
- **Event dedup**: F2 · F3 §1 동일 메커니즘 (`EVENT_WINDOW` · §12)

---

## 2. Candidate Feature Families (사전 등록 · 최대 3개 · F4 는 범위 좁게)

**각 feature 는 경제적 가설 명시 필수** · 명시 안 된 feature 추가 금지.

### F4-α — BTC-adjusted Return

**가설**: BTC 가격 변화를 제거한 종목 return 이 유의하게 양수면 → informed demand
또는 섹터 rotation · 이후 continuation.

- 입력: 최근 N 분 종목 return · BTC return (같은 N 분 window)
- 측정: `coin_return - β × BTC_return` (β = 사전 freeze 또는 rolling · §12)
- Horizon window N 사전 freeze (§12 · 예: 5분)

### F4-β — Breadth-adjusted Z-score

**가설**: 동시간대 KRW universe 전체의 return 분포 대비 **표준화된 excess strength**
는 상대 강세 신호. BTC β 보다 더 robust 할 수 있음 (BTC 자체도 cross-sectional
한 요소).

- 입력: 현재 시점 KRW universe 전 종목의 N분 return
- 측정: `(coin_return - universe_median) / universe_mad` (MAD-based z-score)
- Universe cap 사전 freeze (예: top-N by liquidity · §12)

### F4-γ — Volume-weighted Relative Strength

**가설**: 거래량 가중 상대 강세. "작은 거래량으로 올라간 소형주 급등" 과 "큰
거래량으로 올라간 종목" 은 서로 다른 edge · 거래량 가중이 informed flow 반영.

- 입력: coin return · coin volume · universe 평균 volume
- 측정: `(coin_return) × log(coin_volume / median(universe_volume))`
- 또는 유사 weighted scheme (사전 freeze · §12)

**candidate budget**: 3 feature × horizons × threshold grid · 하지만 같은 경제적
아이디어의 threshold 20개 ≠ 20 hypothesis (F2 §6 동일).

---

## 3. Outcome = Executable Return (F2 §3 동일)

- Horizons fixed: **30 / 60 / 120 / 180s** (post-hoc pick 금지)
- `NetReturn_frac_h = (sell_proceeds - buy_cost - fees) / buy_cost`
- Preferred: **depth-aware VWAP** at `FIXED_NOTIONAL_KRW`
- Fallback: `small-fill TOB executable proxy` 라벨 한정
- Maker 가정 시: non-fill · partial fill · adverse drift 반영 의무

**금지**: Mid return 을 executable 로 둔갑 X · suffix 없는 pnl/return X.

---

## 4. Unit Contract (F2 §4 동일 · 100× 버그 가드)

- Suffix 필수: `*_frac` / `*_pct` / `*_bp`
- 값 크기 heuristic 금지
- **β · z-score 등 dimensionless 지표도 명시** (예: `beta_dim` · `zscore_dim`)

---

## 5. TRAIN / OOS + Leakage Control (F2 §5 동일)

- 시간순 split · boundary freeze
- **Temporal purge + cross-sectional purge** (중요):
  - Temporal: F2 · F3 동일 · adjacent-event leakage 방지
  - **Cross-sectional**: 같은 시점에 다른 종목들이 동시 상관 (BTC 공통 움직임) ·
    purge 안 하면 "같은 사건 양쪽에서 본 것" 유사 효과 발생 가능
  - 조치: TRAIN/OOS split 때 시점 기준 strict · 종목 cross-split 금지

### OOS 열린 후 금지 (strict)

- Feature / threshold / horizon / cost / event-def / universe 정의 **frozen**
- 수정 시 F4_v2 + fresh untouched slice

---

## 6. Candidate Budget (multiple-comparison guard · F4 특화)

- Family 3개 사전 freeze
- Threshold TRAIN only · OOS cap 고정
- OOS 재선택 금지

### F4 특화 주의 (advisor 2 지적 수용)

> "F3/F4 는 서로 다른 가설이지만, **같은 시장·기간을 쓰면 통계적으로 독립된
> 증거는 아니다**. 공유 OOS 와 전체 탐색 횟수도 기록해야 함."

- F4 가 F3 와 **같은 데이터** (orderbook snapshot · trade stream) 를 쓰면 **공유
  OOS slice** 명시 필요
- Family-wise error rate 계산 시 F3 candidate 수 + F4 candidate 수 합산
- 공동 Bonferroni 또는 FDR 보정 (사전 등록 · §12)
- 전체 탐색 횟수 (F2 · F3 · F4 합산) 리포트 포함

---

## 7. Success Criteria (F2 §7 동일 · repeatability + cross-sectional 추가)

### SURVIVE 조건 (전부 만족 · conjunctive)

1. `mean_net_pct > 0` after cost
2. `cost_stress_net > 0` (fee × 1.5)
3. `TRAIN → OOS direction consistency` (walk-forward ≥ 2/3)
4. Concentration guards (전부 §12 freeze):
   - top_1_event_share 상한
   - top_5_event_share 상한
   - top_coin_share 상한
   - top_hour_share 상한
5. **각 feature × horizon 조합마다 독립 verdict** (F4 전체 verdict 금지)
6. **F4 특화**: 다른 cross-sectional feature 와 상관성 확인 (collinearity 보고)

### 함께 보고 (판단 보조)

- Median · positive rate · 손실 꼬리
- F3 candidate 와의 overlap (같은 event 가 F3·F4 둘 다에서 pass 하는 비율)
- BTC 가격 이동 regime 별 performance (strong/weak/sideways BTC)

### 자본당 일일 net (F2 §7 동일)

- 포트폴리오 규칙 없으면 건당 + 빈도만 보고

---

## 8. 결과 taxonomy (F2 §10 동일)

**per feature × horizon 조합마다 독립 verdict**: ZERO · CANDIDATE · SURVIVE

**F4 전체 verdict 금지** · 3 feature × 4 horizon = 12 조합 각각 독립.

---

## 9. F2 · F3 와의 관계 (명시적 분리)

| | F2 | F3 | F4 |
|---|---|---|---|
| **정보원** | selection density · small-net reproducibility | microstructure 지속성 | 횡단면 상대 강세 |
| **Universe** | climax trigger (F2a) · 넓은 liquid (F2c) | 넓은 liquid | 전 KRW universe 동시 scan |
| **데이터** | orderbook + trade + flow | orderbook + trade taker | 전 종목 return + BTC + universe stats |
| **공유** | — | F3-F4 공유 OOS slice 가능 | §6 공동 보정 |

- 세 실험 verdict 섞지 않음.
- F4 SURVIVE 가 F3/F2c SURVIVE 를 보장 X.
- 공유 데이터 쓰면 §6 공동 보정 (전체 탐색 횟수 합산).

---

## 10. Guardrails (F2 §11 동일 · 명시적 반복)

- **LIVE / gate / exit / TP / SL / trail / sizing 전면 무변경.**
- A = REJECTED_FINAL/CLOSED · A2 = TERMINATED_INFEASIBLE · C1 closed · 봉인 유지.
- F4 verdict 는 봉인된 결정 **재판정 근거 아님**.
- Post-hoc filter 금지 · 새 CLM threshold · exit tweak · rescue 추가 X.
- Implementation 은 §12 placeholder freeze + 본 문서 FROZEN / F4_v1 승격 이후만.

### 운영 규칙 (advisor 1 명시 · lock)

> "research 가 늘수록 bot.py 는 **짧아져야** 한다. 새 가설 분석기 · runner ·
> reporter 는 `research/` 에 두고, production 엔 검증 통과분의 최소 interface 만
> 연결. 새 shadow route 를 bot.py 에 직접 붙이는 것 금지."

- F4 runner 구현 시 `research/f4_runner.py` 로 격리
- bot.py 에 F4 shadow route 추가 금지
- 검증 통과 (SURVIVE + forward shadow) 후에만 production interface

---

## 11. 데이터 요구사항

### 필요한 데이터

- **전 KRW universe 동시 snapshot** (최소 1분 간격 · 가능하면 10초)
- **BTC/KRW 1분 bar** (베타 계산용)
- **종목별 거래량** (volume-weighted strength 용)
- **Universe liquidity rank** (depth · spread 기반)

### 봇 데이터 가용성 확인 필요 (§12)

- 현재 봇이 전 KRW universe 를 어느 해상도로 동시 저장하는지
- BTC/KRW 데이터 저장 여부 (별도 fetch 필요?)
- 저장 기간 (최소 F4 TRAIN+OOS 커버)
- 가용 X 시 → 데이터 수집 phase 선행 (별도 ticket)

---

## 12. 미확정 플래그 (FROZEN 전 결정 필요)

**현재 DRAFT** · 아래 값 결정 후 FROZEN / F4_v1 승격:

| 변수 | 결정 방식 |
|---|---|
| **데이터 가용성** (universe scan · BTC · volume · liquidity) | 봇 코드/데이터 디렉토리 샘플링 |
| `EVENT_WINDOW` | F2 · F3 동일 값 가능 · 봇 scan 주기 기반 |
| `FIXED_NOTIONAL_KRW` | 실 tiny-LIVE 주문금액 (F2 · F3 동일 가능) |
| `MIN_DEPTH_KRW` · `MAX_SPREAD_PCT` | F2c · F3 와 동일 사전 가설 (일관성) |
| F4-α return window `N` | 사전 freeze (예: 5분) |
| F4-α β 계산 방식 | 사전 freeze (고정 β vs rolling vs OLS) |
| F4-β universe cap | 사전 freeze (예: top-50 by liquidity) |
| F4-γ weighting scheme | 사전 freeze (log-weighted vs rank-weighted) |
| TRAIN/OOS 비율 + purge window | 데이터 길이 보고 결정 · F3 와 공유 OOS slice? |
| **공동 보정** (F3 · F4 전체 탐색 수 Bonferroni/FDR) | advisor 2 요구 수용 · 사전 freeze |
| SURVIVE concentration 수치 | 연구자 위험선호 사전 freeze |
| 포트폴리오 규칙 | 있으면 명시 · 없으면 건당+빈도만 |

---

## 13. Deliverables (FROZEN 후 implementation 단계)

1. **F4 데이터 가용성 report**: universe scan · BTC · volume 저장 실측
2. **각 feature (α · β · γ) 별 OOS verdict** × 4 horizons = 최대 12 조합
3. **Concentration · temporal · cross-sectional** 분석
4. **F3 와의 overlap**: 같은 event 가 F3·F4 둘 다 pass 하는 비율
5. **BTC regime 별 performance** (strong/weak/sideways)
6. **공동 보정 report** (F2+F3+F4 전체 탐색 횟수 · Bonferroni/FDR)
7. **Unit protocol compliance test** (전수)

---

## 14. Audit trail

- 본 문서의 FROZEN commit SHA = pre-registration proof
- DRAFT 수정 (freeze 전) 자유 · 각 수정은 commit
- OOS 열린 후 수정 시 F4_v2 + fresh untouched slice

---

## Status

```
status:        DRAFT · pre-registration · §12 placeholder 미결
next step:     데이터 가용성 확인 → §12 placeholder 결정 → FROZEN / F4_v1 승격
implementor:   별도 커밋 · 별도 PR · 본 문서는 pre-registration 전용
code change:   없음 (bot.py 무변경)
```

**본 문서는 사전등록 계약 DRAFT · 코드 변경 없음.**
LIVE / gate / exit / 파라미터 / A 봉인 전부 그대로 유지.
F4 implementation 은 FROZEN 승격 + 사용자/advisor 승인 후에만 별도 커밋.

**PR #546 과 관계**: codex branch 의 `F4_RELATIVE_STRENGTH_DRAFT.md` (28줄) 는
scope 초안 · 본 문서는 methodology 상세. 통합 시 PR #546 version 흡수 또는 둘
다 유지 (상호 보완). 사용자/advisor 결정.
