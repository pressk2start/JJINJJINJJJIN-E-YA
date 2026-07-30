# 레버 A · A2 — PR2 wired-shadow 스펙 ("본절 OFF 클린 트레일 × vr5-cap 진입")

## 한 문장
LIVE shadow의 capture 4~12% 노이즈 진동은 **트레일 위에 얹힌 본절정지 + 조기SL 레이어(A 표적)** 와 **저품질 진입 (고vr5 급등, A2 표적)** 이 원인이다. PR2는 두 레이어를 각각 끈 **A (순수 peak-trail)** 과 **A × A2 (vr5-cap 진입 + 클린 트레일)** 을 동일 코호트에서 병렬 검증한다.

## A2 지위 (3자 수렴 확정, 정직 라벨)
- **리서치 코호트(409)**: 5단계 검증 통과, per-trade +0.63%, MDD 절반 **(리서치 결과, 라이브 외부 재현 미완)**
- **라이브 코호트(113)**: 외부 재현 검증 대기 (본 PR2 목적)
- ⚠ "5검증 완주" 를 확정처럼 프레이밍 금지 — 리서치 vs 라이브 데이터셋 명확 구분

## 근거 (매칭 코호트 n=409, `lever_a_verify.py` 재현)
| 청산구조 | net/건 | WR | MDD | worst | 손실꼬리 ≤-1% |
|---|---|---|---|---|---|
| **클린 트레일 arm180/bp30/h240** | **+0.37%** | 53% | **4.9%p** | -1.07% | ~0% |
| + 본절(0.5%트리거) + SL2% | -0.10% | 15% | — | — | — |
| 라이브 실청산(-2%flat+타임아웃+래칫) | -0.56% | 28% | 234%p* | -3.20% | 42% |

- 폭: bp30 최적, bp50/70/100 단조 열위 → **폭 확대 금지**(라이브 'bp50 우세'는 코호트 착시).
- 프리셋 규칙(+0.10%p AND MDD ≤+2.0%p 악화): 클린 트레일은 수익 +0.93%p **AND** MDD 개선 → 통과.
- `*` 라이브 234%p는 equal-weight 누적의 방향지표(계좌 실MDD 아님).

## 대조 설계 (공통 코호트, 동일 진입 신호에 청산만 분기)

**⚠ 코호트 확정 (2026-07-21, shadow 동결 관측 반영)**
- CS40_VR3 shadow 라우트는 3창 연속 무증가로 표본 기아 위험
- 게다가 shadow=vr3 게이트, 라이브 실 진입=vr2 (LIVE_ROUTE_SPEC v2) → CONTROL-라이브 정합 어긋남
- → **코호트 = vr2 CLM 라이브-실진입** 으로 확정
  - 후향 백테스트(CS40+vr2, n=409)와 정확 동일
  - 비-VR3 라우트는 실측 증가 (CS40_TR180_bp30_240 +2 등)
  - 라이브 실체와 정합

## 대조군 (3-arm 병렬)
- **CONTROL** = 현행 라이브 청산 (본절 ON + -2%/-3% + v7 타임아웃 + 래칫). 라이브 실진입 vr2 코호트.
- **A (WIRED_A_CLEAN)** = 클린 트레일: arm180 / bp30 / hold240, **본절정지 OFF · 조기 시간티어SL OFF**, 유일 백스톱 = **far -3% 하드스톱만 유지**(꼬리보호용, 거의 미발동).
- **A × A2 (WIRED_A_CLEAN + vr5-cap)** = A 청산 + **vr5-cap 진입 필터** (사전등록 vr5 percentile 60 · 실행 전 결정, lookahead 금지).
- 세 arm 모두 같은 (market, signal_ts) 진입에 대해 병렬 shadow 청산 → 동일 코호트에서 net/WR/capture/MDD 직접 비교.

## A2 5조건 사전등록 (advisor 강한 증거 요건)
1. 최근 손실군 vr5 > 과거군
2. cutoff 사전 고정 (실행 전 결정, 결과 보고 변경 금지)
3. cutoff 적용 시 A2 통과군 WR > 전체 WR (선택적 손실 제거)
4. 비용·표본 감소 후에도 A2 net > A net
5. 최근 20건 A2 효과 > 전체 A2 효과 (국면 적응력)
→ 4/5 이상 통과 · 테스트 가능 4/5 이상 이면 **STRONG_EVIDENCE**

## basis 라벨 규칙 (advisor 정정 확정 · 산술 반사실 금지)
| 종류 | 표기 | 예시 |
|---|---|---|
| 매칭 sim (paired replay) | `+0.37% (매칭 sim, n=409, 동일 진입 paired)` | 본절 OFF 효과 |
| 관측 shadow | `+0.06% (관측, n=113 라이브)` | 현 shadow cap 9% |
| counterfactual estimate | `+0.29% (counterfactual estimate, 라이브 MFE 기반 모형)` | 상한 추정 |
| **금지** | ~~`73 × 0.48%p → +0.15~+0.25%/건 상한`~~ | 순진한 산술 반사실 |

## 3구간 × 3축 검증 매트릭스 (advisor 지적)
- **3구간 분해**: 최근 5 / 20 / 50 / 전체 → 국면 적응력
- **3축 분포**: vr5 × MFE × MAE → A2 설계 근거 (고vr5 + 낮은MFE + 높은MAE 조합)
- A2 통과/차단별 PnL · WR · MFE · MAE · vr5 중앙값 표

## 승격 게이트 (판정 4 + 표본 CI)
- capture ≥ 25% (**단일 시점 통과만으로 승격 금지 · paired CI 병렬 판단**)
- net > CONTROL
- MDD ≤ CONTROL + 2.0%p
- 최근 20 · 50건 방향 유지

## 계측(신호 페어링 필수 필드)
`signal_id`, `entry_ts`, `entry_price_basis`, `market`, `control_exit`, `clean_exit`

## 결과 페어링 (동일 이벤트 페어 기준)
`matched_n`, `control_net`, `clean_net`, **`delta_net`**, `control_capture`, `clean_capture`, **`delta_capture`**, `control_avg_win`, `clean_avg_win`, `control_avg_loss`, `clean_avg_loss`, `control_mdd`, `clean_mdd`

## 청산 이벤트별 필수 기록
signal_ts, entry, exit_reason ∈ {TRAIL_HIT, HOLD_CAP, HARD_STOP}, realized_pnl, mfe, capture=realized/mfe, hold_sec.
- CONTROL의 exit_reason은 기존대로(AT익절/AT본절/AT타임아웃/손절SL).
- **불변식**: 모든 청산이 정확히 한 exit_reason에 귀속(미분류 0).
- **오염 방지**: 오염 bp30 vs 클린 bp50 착시 재발 방지 위해 반드시 **동일 signal_id 페어** 기준 비교.

## POST accounting 보존식 (조언자 세션 스펙, 이번 턴 확정)
`enter = blocked + live_pass + shadow_only + error`
- **shadow_only** = AUTO_TRADE=False 정상 terminal state (실주문 gate 이전 shadow 분기 return)
  - unclassified 로 취급 금지 · 별도 카운터
  - AUTO_TRADE=True 로 전환하면 자연히 0 으로 수렴
- **live_pass** = 실주문 게이트 통과 (기존 post_signal_pass)
- **blocked** = 명시적 gate 차단 (기존 post_signal_blocked, 11+1곳)
- **error** = try/except 로 잡힌 예외 (기존 post_signal_error)
- **unclassified > 0** 은 실 accounting 누락 신호 (shadow_only 로 재분류하면 해결되는지 확인)

## 판정 게이트 (전향 shadow, n 충분히 쌓인 뒤)
1. TREATMENT capture ≥ 25% (CONTROL 4% 대비 유의 상승) — 1차 관문.
2. TREATMENT net > CONTROL net, 부호 양수.
3. TREATMENT MDD ≤ CONTROL MDD + 2.0%p (실측은 개선 예상).
4. 감쇠 감시: 최근 20/50건에서 우위 유지.
- 4개 통과 → 극소액 LIVE 후보. 하나라도 실패 → 후향-전향 괴리 원인 규명 후 재설계.

## Wired-Shadow 계측 5축 (advisor 3자 수렴, A2 운영 검증)
A2 의 본질 = "슬롯·일일가드 제약 하 고품질 선별" → 운영 제약에서 실 이득 검증 필수.
기존 per-trade (net/WR/MDD/capture) 외에 A×A2 배선 시 병행 계측:
1. **거래 수** — A2 차단 후 실제 진입 수 (드롭률 관측)
2. **MDD** — 코호트 equal-weight (계좌 실MDD 아님, 방향지표)
3. **평균 동시 포지션 수** — A2 차단이 포지션 완화에 기여하는지
4. **슬롯 점유율** — 최대 동시 포지션 / MAX_POSITIONS
5. **신호 드롭률** — A2 차단 신호 / 전체 신호

## Adaptive Exit — A로 흡수 (별도 후보 삭제, advisor 정정)
"AT익절 +0.13% vs MFE +0.97% → 익절 부족" 관찰 = **A 가 이미 다루는 청산 병목**.
클린 트레일 (arm180/bp30, 본절·조기SL OFF) 이 capture 복원 → "승자를 더 오래 태우는" 처방과 동일.
- ⚠ 이중계상 위험: Adaptive Exit 이 A 의 재발견일 소지, 별도 후보 세우면 이득 중복 계산
- ⚠ 룩어헤드 위험: "관측 MFE 크니 TP 높이자" = conditioning-on-outcome (진입시각 미확정 특징 사용)
- 새 청산 아이디어는 "**진입시각 확정 특징만 사용**" 증명 후 재도입

## cap 서사 표현 규율 (advisor 정정)
- 데이터 규율: **표본 소량 (Δ+9건) 에서 cap 1%p 변동 = 노이즈**
- ❌ 금지 표현: "cap 12→8→7 계속 감소, 청산이 계속 나빠지고 있다"
- ✅ 정확 표현: "약한 밴드에서 표류, 추세 판정 불가 (n 부족)"
- ✅ PnL +0.05% 유지가 더 중요한 신호 (미세 표류는 자연 변동)
- 감쇠 판정: **wired-shadow 최근 20/50건 게이트로만** 판정 (누적 shadow cap 아님)

## 확정 vs 추론 vs 다음단계 규율 (advisor 정정)
| 축 | 표현 |
|---|---|
| ✅ 확정 (로그 뒷받침) | "v4_meta 단계에서 예외가 반복 발생한다" |
| 💡 강한 추론 | "동일 예외일 가능성 높음 (하나의 KeyError 인지 여러 종류인지 미확정)" |
| ▶ 다음 단계 | "POST_ERROR_SAMPLE 로 타입 확인 후 예외 타입별 방어 코드 적용" |

## defensive 방어 처방 (예외 타입별, advisor 정정)
`.get()` 하나로 성급 확정 금지 — POST_ERROR_SAMPLE 로 타입 확정 후 매칭:
| 예외 타입 | 처방 |
|---|---|
| KeyError | `.get(key, default)` 로 fallback |
| AttributeError | `if obj is None:` 명시 체크 |
| TypeError | `isinstance()` 타입 방어 |
| IndexError | `len(seq) > idx` 길이 체크 |
| ValueError | `try` 격리 + default 반환 |

## bp width 착시 검정 명시적 승격 (advisor 정정)
라이브 shadow `bp30(+) → bp50/70(peak, n=24) → bp100(-, n=116)` **비단조** 패턴 관찰.
"폭 확대 이득" 이 아니라 **표집 노이즈 지문** — 정점이 하필 소표본 셀.
- bp50/70 양수 셀이 **thin-book 버킷과 동조** (bp50: `ob_slip_sell 0.18~0.34 → wr62% +1.00%` · bp70: `ob_bid_cum5 하위 → wr62% +0.89%`)
- ⚠ 라벨 규율: **"thin-book 효과가 섞였을 가능성"** (강한 표현 "새어나왔다" 금지 — 코호트 고정 검정 필요)
- 검정 방법: `live_cohort_resim.py` 로 코호트 고정 후 A · A×A2 + **bp width sweep 동시 검정**
- 판정 유지: **bp30 고정, 폭 확대 금지** (재기각)

## 라벨링 규율 — "지지 vs 방향 일치" 구분 (advisor 정정)
필터검증 라이브 관찰 예시:
```
climax_vr3_fail 28건 wr46% +0.17%  (vr3 못 넘은 저vr 신호가 통과 route 보다 우수)
CLM_A2:climax_body_hi_fail 13건 wr54% +0.12%
```
- ❌ 금지 표현: "A2 방증 확보", "A2 지지"
- ✅ 정확 표현: **"A2 가설과 방향성 일치 관찰"** · **"A2 가설과 모순되지 않음"**
- 근거: 이 필터는 **A2 자체를 테스트한 것이 아님** (관련 특성만 관찰). 리서치 코호트 검증과 라이브 파이프라인 재현은 별개.
- A2 라이브 승격은 여전히 `live_cohort_resim.py` A × A2 3-arm + 5조건 사전등록 통과 후에만.

## 착수 선행조건 (강화, 재배포 34시간 정체 반영)
1. **SHA 3중 확인** (신구 프로세스 이벤트 섞임 방지)
   - `GIT_HEAD` = f976e86 (또는 그 이상)
   - `PROCESS_START_TS` = 재배포 이후
   - 리포트 헤더 `deploy` = 같은 SHA
   - `[LIVE_EFFECTIVE_CONFIG].code_build_id` = 같은 SHA
2. `[LIVE_EFFECTIVE_CONFIG]`에서 `effective_vr_min=2.0 / live_entry_path_uses_route_vr=false` 확인(문서 v2 대조).
3. delta enter>0 창에서 POST 보존식 check=OK · coverage=100% · unclassified=0(관측 결손 종결).
4. `SHADOW_ROUTE_FLOW`에서 대상 route (vr2 CLM 계열) candidate 증가·opened 증가 확인 (파이프 stuck 아님 검증).
→ 이후 A 단독 wired-shadow 착수.

## 기각 확정 가설 (진입필터 후보 영구 제외, 2026-07-25)
- **가설 D · kst_hour 시간대 필터** — 라이브 105건 시간대 편차 관찰 (오전 강 · 저녁 약)
  이 세션 조언자 matched 종료 테스트로 최종 기각:
  ```
  409 코호트, 두 청산 (CLEAN + CONTROL), 야간(18~23) 회피 효과:
    CLEAN net    Δ -0.015%p (손해)
    CONTROL net  Δ -0.035%p (손해)
  ```
  두 청산 구조 모두 야간 회피가 net 을 깎음 → exit 구조 교락 배제
  → REJECTED_FINAL · 진입필터 후보 영구 제외 · 실무 비활성화
  → 재현 5회는 같은 누적 코호트 재버킷 (독립 표본 아님, 규율 위반)

- **가설 B · 60초 EarlyCut** — walk-forward 실행형 −0.20%p · 사후 조건 착시
- **가설 J · mfe_60s 필터** — 룩어헤드 (진입 후 관측값)
- **폭 확대 bp50/bp70** — 매칭 코호트 반증

## 명시적 금지
- 청산 전략값(활성화 threshold/trail width/timeout/BE trigger) — 승격 전 무변경.
- **cutoff 사후 최적 선택 금지** (사전등록값만 primary 판정)
- 폭 확대 금지(bp30 고정), 조기컷(가설 B) 미포함, 호가벽(가설 C) 미포함.
- **A2 진입 필터**는 라이브 진입 로직 변경 없이 shadow-only 배선으로 검증 (승격 후 별도 PR)
- 산술 반사실 표현 금지 (`73×0.48` 형태) — paired sim 만 사용

## 참조 (advisor 3자 수렴, 2026-07-27)
- 3-arm 병렬 · 3구간 × 3축 · 5조건 사전등록: `research/live_cohort_resim.py` v3
- A2 단독 sweep · 국면 적응력: `research/a2_vr5_filter.py` (신규)
- 관측 트랙 (POST_ERROR_STAGE · shadow_only=0 강제 · lookahead 자동 차단): `bot.py`
