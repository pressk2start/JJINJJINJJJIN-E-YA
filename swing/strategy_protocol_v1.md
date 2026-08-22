# 스윙 전략 연구 프로토콜 (Strategy Research Protocol) v1.7 — hash-ready 후보

> **상태: 값·코드사양·연장규칙·α-spending 확정 + 6개 audit fix 반영 완료.**
> **hash 봉인 = 사용자 손 (별도 새 repo에 이 문서 + swing.py + tests.py + run_robustness.py + fetch_snapshot.py + snapshot.json.gz + README.md 배치 → commit + annotated tag).**
> v1.6→v1.7 substantive: MinTRL 종료조건 삭제(prospective는 PSR이 통계 gate) / §12 재구조 / stop=fill 기준 / §9 옵션(i) α-spending / DSR·PSR 역할 분리·코드사양 봉인.
> v1.7 audit fixes: open/close map 분리 · full_calendar · dynamic eligibility · skew/kurt central-moment · snapshot pre-hash provenance · entry-pending gap → InvalidRun.
> **⚠ 서버 봇 5개 안전확인 + `systemctl mask momentum-*`은 연구와 별개 최우선.**
> **앵커링 고지:** 작성자는 추세추종 discovery(+6% MFE)를 봤다 → A는 절차상 후보 중 하나, 규칙=문헌 표준, 게이트=일반 관행.
> `[확정]`/`[✓승인]` = 봉인값.

---

## 1. 목적함수 [확정]
현금(0%)보다 유리한 순수익, 손실 위험 함께 평가. BTC = 비교지표(report only).

## 2. 지표 & 산출 기준 [확정]
**Net Return / MDD / Calmar.** 모든 통계는 **일별 mark-to-market 포트폴리오 equity curve 기준.** trade-level은 보조.

## 3. 위험 제약 (MDD) [확정]
Hard fail **> 20%** / 선호 ≤ 15% / 회색 15~20%.

## 4. 합격 = 순차 게이트

### 4.1 Gate 1 생존 [확정] — MDD ≤ 20%.

### 4.2 Gate 2 유의한 절대수익 [확정]
두 조건 동시:
1. **cost-stress 최악(§7)에서도 연환산 Net > 0.**
2. **DSR probability ≥ [✓승인: 95%]** — robustness/model-selection(7 trial 선택편향 보정). prospective 최종검정은 §9 PSR로 분리(이중과세 방지).
   - PSR = 관측 Sharpe가 benchmark Sharpe를 초과할 확률 (트랙길이·skew·kurtosis 반영, Bailey-LdP).
   - DSR = benchmark를 raw trial N과 trial Sharpe 분산으로부터 추정되는 기대최대 Sharpe(SR\*_DSR)로 상향한 뒤의 PSR.
   - **⚠ SR\*_DSR = trial registry의 N과 trial Sharpe 분포로부터 사전 고정 공식 계산.** SR=0은 경제적/null 기준이며 DSR benchmark 자체로 직접 고정하지 않음.
   - **Trial count [확정]: raw trial N만 사용. effective-N 금지.** 결과 나쁘다고 갈아타지 않음.
   - **Trial registry [확정]:** 모든 가설족·파라미터 사전등록, 실패 포함 전부 보존. git commit + hash 봉인.
   - **구현 사양 [✓봉인]:** 일별 MTM portfolio returns / annualization 365일 / Sharpe = mean/std×√365 / skew = sample skewness, kurtosis = non-excess (⚠ audit item 4: pure Fisher-Pearson central-moment로 재작성 필요) / PSR = Bailey-LdP / SR\*_DSR = raw N=7 trial Sharpe 평균·분산의 expected max / effective-N 금지 / DSR threshold 0.95(robustness) / prospective = PSR ≥ 0.9833(§9 sequential) / 결측일 0 미충전 / risk-free=0 / 코드와 protocol 동일 commit hash.
   - 다중검정 역할 분리: DSR 하나. 최종 독립 가설족 보고 family-wise만 필요시 Holm/Šidák.

### 4.3 Gate 3 위험조정 [✓승인] — Calmar ≥ 0.5, robustness ≥ 2년 구간에서만 판정.

### 4.4 Gate 4 강건성 [확정]
- **Concentration [abs 기반]:** per-coin notional ≤ 20% · top1 abs-PnL share ≤ 40% · HHI ≤ 0.35. share_i = |pnl_i|/Σ|pnl_j|, HHI = Σshare². top1 positive share는 진단값.
- **LOO:** 최대 양(+) PnL 기여 종목(star winner) 제거 후 Net > 0, MDD ≤ 20%, Calmar ≥ 원Calmar의 70%. LOO별 DSR은 진단값.
- **Cost stress:** §7 전 수준 통과.

## 5. Universe & 실행비용 [✓승인, 2층 분리]
**(i) Universe liquidity eligibility [✓]:** trailing 거래대금(30일 평균) + 상장경과(≥180일) + 거래중단 여부. **참여율 상한: 주문 ≤ 30일평균 일거래대금의 0.5%** (eligibility proxy만). survivorship caveat 항상 명시.
⚠ audit item 3: 현 코드가 static filter. dynamic point-in-time + 30d turnover + halt 판정 미구현.
**(ii) 역사 구간 execution cost [확정]:** 고정 stress 0.20 / 0.35 / 0.50%.
**(iii) k/거래대금 impact 모델 = 사용 금지** (별도 execution-calibration dataset 생기기 전까지).

## 6. 가설족 [✓승인 = A+B+C, A=(b)]
3개, 절차상 동등 후보. 규칙=문헌 표준, 파라미터=사전등록.

| 족 | 개요 | discovery exposure |
|---|---|---|
| A 추세추종 (simplified Turtle-derived) | §6.1 A1/A2 | strong YES |
| B 평균회귀 (Connors RSI-2 + risk overlay) | §6.1 B | weak YES |
| C 횡단면 상대강도 (J-T-inspired) | §6.1 C | weak YES |

- A 처리 = (b): 파라미터 §6.1 문헌 표준값 완전 고정.
- Selection metric [✓]: Gate 1~4 통과 조합 중 robustness Calmar 최고, tie-break 낮은 MDD.
- Grid → 최종 spec: 사전등록 grid 전체 평가, 봉인 selection rule 하나로 1개 선택. DSR raw-N에 grid 전체 산입.

### 6.1 확정 규칙·grid [✓승인]
공통: long-only, 일봉. **stop·sizing 기준 [확정]:** ATR = ATR_t(신호일 완성봉). fill = t+1 일봉 open. initial stop = fill − mult×ATR_t. position size = equity×1% / (fill − initial stop).
**ATR = Wilder ATR:** seed = 첫 period개 TR 평균, 이후 ATR_t = ((period−1)·ATR_{t−1} + TR_t)/period. (A=ATR20, B/C=ATR14.)

**A. simplified Turtle-derived** (long-only crypto 적응, pyramiding·S1 skip·short 제외):
- A1: 진입 = 20일 신고가 돌파 / 청산 = 10일 저점 돌파 / 초기 stop = 2N(=2×ATR20).
- A2: 진입 = 55일 신고가 돌파 / 청산 = 20일 저점 돌파 / 초기 stop = 2N.
- grid = 2 systems.

**B. Connors RSI-2 signal + protocol risk overlay:**
- 신호(literature): 종가 > SMA200; RSI(2) < threshold ∈ {5,10}; 청산 = 종가 > SMA5.
- risk overlay(우리 것): 보호 stop = 2×ATR(14) + 시간 cap 10일.
- grid = 2 combos.

**C. Jegadeesh-Titman-inspired 횡단면 momentum:**
- 신호(literature-inspired): eligible universe를 formation 수익률로 랭크, formation ∈ {3, 6, 12개월}, 월간 리밸런싱, 문헌 원형 = top decile.
- 포트폴리오 제약(우리 것): 실제 보유 = top-N (= §11 N = 5). §11 N 변경 시 top-N 자동 재도출.
- risk overlay(우리 것): 보호 stop = 2×ATR(14).
- **eligible < top-N 처리 [✓ = min(E,N)]:** 실제 보유 수 = min(E, N). E<N이면 적격 종목만, 남는 gross = 현금. E=0이면 전액 현금. 부적격 강제 충원 금지. 결과 독립 execution semantics (variant 아님, raw-N 불변).
- grid = 3 combos.

**총 raw-N = 2+2+3 = 7 trials** → DSR benchmark(SR\*_DSR) 이만큼 상향.

### 6.2 실행 semantics [결과 독립 default — ✓승인]
- 신호 판정: 완성봉에만 계산.
- 진입: 신호일 t 완성봉 → t+1 일봉 open.
- 정상 청산: t 완성봉 → t+1 open.
- 보호 stop: 장중 low가 stop 침범 시 체결, gap 불연속 시 min(stop_price, next executable) 등 불리한 가격 사용.
- N=5 초과 동시신호: 가설 내부 사전정의 ranking. 없으면 lexical tie-break.
- equity/sizing: 매일 MTM equity 먼저 계산 → 신규주문 size = equity×1%/stop-distance, 20% notional cap, gross 100% cap. 기존 포지션 매일 재조정 안 함.
  ⚠ audit item 1: sizing map(open) vs MTM map(close) 분리 필요.
- **MTM 결측일 [✓ = INVALID]:** 전일부터 보유 중인 종목의 당일 봉 결측 → InvalidRun raise → run/dataset INVALID. carry-forward 금지 (모델 리스크 회피). entry pending 결측도 동일 처리 필요 (audit outstanding).
- 동일봉 stop+정상exit 동시: stop 우선.
- 비용: 진입·청산 각 stress의 절반 → 왕복 0.20/0.35/0.50%.
- C 월간 리밸런싱: 월말 완성봉 ranking 확정 → t+1 open 교체. 실보유 = min(E, top-N). 부족 슬롯 = 현금.
- variant 규율: variant 추가 시 raw-N 즉시 증가. 진단용 variant도 registry 삭제 금지.

## 7. 비용 stress [✓승인]
왕복 0.20 / 0.35 / 0.50%. 모든 Gate 최악에서도 통과.

## 8. 데이터 분할 (Z) [✓승인]
- Historical robustness: 2019-01-01 00:00 ~ 2023-07-31 23:59 KST (한 번만, survivorship caveat).
- Discovery: 2023-08-01 00:00 ~ 2026-08-22.
- True sealed holdout: 봉인 완료 시각 이후 prospective.
- Discovery exposure 목록: 추세추종 Phase 0 · 시장 스캔 층4 · 층1·2 · 인벤토리.
- 대화 exposure caveat: 이 세션 전체가 discovery context (range Phase 1 실패 · 청산 8규칙 · OFI 예측력 0 · CLM 라이브 통계 · AQR·BTOP50 참고 등).

## 9. Prospective shadow 종료 [확정]
두 조건 충족 시 첫 판정. 사전등록. 조기종료 금지 (연장은 사전등록 스케줄만).
**⚠ 성과 blind [확정]:** 두 조건 충족 전까지 성과지표 전부 blind. 중간점검 = 운영·데이터 품질만.
1. calendar ≥ [✓: 12개월]
2. coverage 충분조건 [✓]: ≥ 10 distinct 종목 AND 각 기여종목 ≥ 2 trades AND 단일종목 ≤ 30% of trades.

**종료 = 1 AND 2 충족 시 첫 판정.** 종료 시점 누적 prospective 데이터로 §4 게이트 계산.
**⚠ prospective 통계 gate = PSR (단일 최종 spec, benchmark SR=0), DSR 아님** — DSR의 7-trial 보정은 robustness 단계, prospective는 고정 spec을 새 시험지에서 = 7-trial 페널티 재부과 이중과세. DSR은 prospective에선 진단값 병기만.

**⚠ 검정력 실장 [명시]:** 12개월 일별로는 modest Sharpe를 prospective PSR 기준으로 확증하기엔 검정력 낮음 → 첫 판정 미확증 가능성 높음. modest 리테일 엣지는 존재해도 실용 기간 내 노이즈와 구별 안 될 수 있음 — 이 한계 봉인.

**연장 규칙 [✓ = 옵션 (i), 사전등록·자동]:** 첫 판정 = 12개월 AND coverage. PSR<임계(98.33%)면 자동 24개월 → 24개월 미확증이면 자동 36개월 → 36개월에도 미달이면 종료.

**⚠ sequential 다중검정 [확정]:** PSR을 12/24/36 3회 체크 → type-I inflation. **α-spending: family α=5% Bonferroni /3 → 각 look PSR ≥ 98.33%.** 시간축 look 보정 — robustness의 7-trial DSR과 완전 별개 단계.

**Hash-time vs shadow-start gap 허용 [✓]:** hash 시각 = 방법론 seal 기준점. shadow 프로세스 activation은 robustness PASS 이후여도 됨. 단 hash-time과 shadow-start 사이에 시장 데이터를 human observation of performance/spec-selection에 사용하지 않으면 봉인 무결성 유지.

## 10. 데이터 적격성 & 증거 위계 [확정]
- Data-quality preflight (robustness 실행 前):
  - (a) 유효 eligible universe < 20
  - (b) 캔들/API 결측률 > 5% (dataset 집계: Σ_coin(구간 달력일 − 실제 캔들)/Σ_coin(구간 달력일))
  - (c) 가격·거래량 corruption (구조검사: o,h,l,c > 0 · h ≥ max(o,c,l) · l ≤ min(o,c,h) · value ≥ 0 · finite. 실패봉 = 드롭 후 결측 집계)
  - (d) point-in-time 복원 불가 (survivorship: `/market/all`이 현재 상장만 반환 = 과거 편향. 공개 API 한계, 코드 미복원, caveat + 캐시 스냅샷 동봉)
  **미달 = INVALID DATASET, 전략 결과 계산 안 함.** 폭락·폭등·고변동은 적격성 문제 아님 = test condition. **결과를 본 뒤 artifact 예외 선언 금지.**
  ⚠ audit item 3: 현 코드 static filter, dynamic point-in-time + 30d turnover + halt 미구현.
  ⚠ audit item 5: snapshot이 post-hash 생성 = "hash commit 동봉" 문구와 불일치.
- **(a) eligible 최소 커버리지 = 30일** (코드 min_coverage=30 잠금).
- 값(§4~9) 봉인 → protocol hash/commit → robustness 최초 1회.
- 3족 스펙 봉인 후 한 번의 실행에서 동시 평가.
- 증거 위계: robustness PASS → prospective 허용 + 보조증거(provenance 등급 제한) / robustness FAIL → 원칙 중단 / prospective FAIL → 종료.
- 재튜닝 금지, shadow 시작 후 과거 재최적화 금지.

## 11. 포트폴리오 구조 [확정 — 사용자 승인, 결과 독립]
- Risk per position R = 1.0% of equity
- Max concurrent positions N = 5
- Gross exposure ≤ 100% (레버리지 없음)
- Per-coin notional cap = 20% of equity
- Position sizing = equity × 1% / stop-distance
- 남는 자금 = 현금
- 설계상 nominal risk = 5% (N×R). 실제 5개 동시 손절은 갭·슬리피지·동시급락으로 초과 가능.

## 12. 임계값 [구조 파생 vs governance threshold 구분]
**(A) 구조에서 직접 파생 [확정]:** R=1% · N=5 · gross ≤ 100% · per-coin notional ≤ 20%.

**(B) 사전등록 governance threshold [✓승인, 결과 독립 사전값, prospective 결과 나빠도 완화 금지]:**

| 항목 | 값 | 성격 |
|---|---|---|
| §4.4 top1 abs-PnL share cap | ≤ 40% | governance (abs 기반, 손실 지배도 감지) |
| §4.4 HHI cap | ≤ 0.35 | governance (2~3종목 편중 감지) |
| §4.4 LOO Calmar retention | ≥ 70% | governance (degradation 검사) |
| §5 trailing window | 30일 평균 | 3안 중 택1 |
| §5 참여율 상한 | ≤ 일거래대금 0.5% | liquidity eligibility proxy만 |
| §9 distinct 종목 | ≥ 10 | governance |
| §9 per-coin trades | ≥ 2 | governance |
| §9 단일종목 trade share | ≤ 30% | governance |
| §10 preflight 최소 universe | ≥ 20 eligible | governance |
| §10 preflight 결측률 | ≤ 5% | governance |
| MinTRL target Sharpe (참고) | 연 0.5 | §9 검정력 참고값, 종료 gate 아님 |

---

## 봉인 상태

**설계값 [모든 ✓확정]:** governance(DSR 95% robustness · PSR 98.33% prospective · Calmar 0.5 · 12→24→36개월 · cost 0.20/0.35/0.50) · 포트폴리오(R1% · N5 · gross100% · per-coin20%) · 가설족(A+B+C, A=(b), raw-N=7) · §12(B) governance thresholds · §8 경계·exposure · DSR/PSR 코드사양.

**Hash 전 남은 blocker (swing/README.md 참조):**
1. open/close valuation map 분리 (look-ahead)
2. full calendar timeline (stealth carry-forward)
3. dynamic point-in-time eligibility + 30d turnover + halt
4. skew/kurt central-moment 정확 공식
5. snapshot pre-hash vs post-hash provenance
+ entry-gap unit test

**실행 2단계 (사용자 손):**
1. protocol + code 동일 commit/hash (git tag) — 이 시각 = prospective 시작 기준.
2. 서버 봇 5개 안전확인 + `systemctl mask momentum-*` (연구와 별개, 최우선).

*hash 후: preflight → historical robustness 1회(3족 동시) → 통과 spec만 prospective shadow(성과 blind). 봉인 뒤 metric·threshold·문서 변경 금지. registry 변경 시 raw-N 즉시 증가.*
