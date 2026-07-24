# 레버 A — PR2 wired-shadow 스펙 ("본절 OFF 클린 트레일")

## 한 문장
LIVE shadow의 capture 4%가 낮은 건 트레일 폭·진입 문제가 아니라 **트레일 위에 얹힌 본절정지 + 조기SL 레이어**가 원인이다. PR2는 이 레이어를 끈 **순수 peak-trail**이 전향 실행에서 capture를 40%대로 복원하는지 하나만 검증한다.

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

## 대조군
- **CONTROL** = 현행 라이브 청산 (본절 ON + -2%/-3% + v7 타임아웃 + 래칫). 라이브 실진입 vr2 코호트.
- **TREATMENT** = 클린 트레일: arm180 / bp30 / hold240, **본절정지 OFF · 조기 시간티어SL OFF**, 유일 백스톱 = **far -3% 하드스톱만 유지**(꼬리보호용, 거의 미발동).
- 두 트랙 모두 같은 (market, signal_ts) 진입에 대해 병렬 shadow 청산 → 동일 코호트에서 net/WR/capture/MDD 직접 비교.

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

## 명시적 금지
- 전략값(진입 vr/cs/body/wick) 무변경 — 이 PR은 **청산 배선만**.
- 폭 확대 금지(bp30 고정), 조기컷(가설 B) 미포함, 호가벽(가설 C) 미포함.
