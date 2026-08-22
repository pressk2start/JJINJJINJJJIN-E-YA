# Edge Discovery Plan · Offline 연구 실행 스펙

**작성일**: 2026-08-20  
**배경**: advisor 3자 수렴 (maintenance mode → edge discovery mode 전환)  
**목표**: 지금 쌓인 데이터로 frozen hypothesis 1-3개 배출 · live paired 50 무한 대기 종료

---

## 왜 지금인가

몇 주간 계측 60+ commit 은 "결과를 믿을 수 있게 만드는 전제조건" 이었다 · 그 전제조건이 이제 거의 다 닫혔다 (task #60 A2_AUDIT key 버그가 마지막 큰 갭이었음). 이제 계측 추가 금지 · 후보 죽이고 살리는 단계.

**정직**: 지금 데이터에 검증된 양수 전략 **없음**. CS40_VR3 shadow n=217 +0.03% = breakeven ± noise. A_CLEAN 60건 +0.19% 도 paired ΔA=+0.000%p (CONTROL 대비 개선 미확인). A×A2 +0.88% = 신기루 (paired 5건 identical).

이 상태에서 계속 계측만 하면 몇 개월 더 갈 수 있다. **후보 발굴 정공법**: 이미 쌓인 데이터에 offline 엔진 실행 → 새 frozen hypothesis 배출.

---

## 실행 절차 (write-session)

### Step 1: 데이터 export

```bash
# bot.py 는 이미 export_trade_records() 함수 정의 · 자동 실행됨
# 최신 shadow_stats 강제 export 원할 시 telegram command 또는:
python3 -c "from bot import export_trade_records; export_trade_records('/tmp/clm_trades.json')"

# 다른 컬럼 필요 시 (indicators 포함) — 이미 자동 export 대상
ls -la /tmp/clm_trades.json
```

### Step 2: CSV 변환 (feature_screen 입력용)

```python
# /tmp/clm_trades.json → /tmp/clm_trades.csv 변환 스크립트 예시
import json, csv
with open('/tmp/clm_trades.json') as f:
    data = json.load(f)
# 각 route 별로 별도 CSV 생성 (route 간 오염 방지)
for route in ['CS40_VR3_TR180_bp30_240', 'CLM_A_CLEAN_bp30', 'CLM_A_x_A2_bp30']:
    rows = [t for t in data.get(route, [])]
    if not rows:
        continue
    # inds dict flatten
    for r in rows:
        for k, v in r.get('inds', {}).items():
            r[k] = v
    with open(f'/tmp/{route}_trades.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}))
        writer.writeheader()
        writer.writerows(rows)
```

### Step 3: Feature Screen 실행

```bash
# 1차: CS40_VR3 (LIVE 대응 route · n≥217)
python3 research/feature_screen.py /tmp/CS40_VR3_TR180_bp30_240_trades.csv \
    --oos-split 0.7 --wf-windows 4 --effect-min 0.20 --paired-ci-pos

# 2차: A_CLEAN
python3 research/feature_screen.py /tmp/CLM_A_CLEAN_bp30_trades.csv \
    --oos-split 0.7 --wf-windows 3 --effect-min 0.20

# 3차: 합산 (n 확대)
python3 research/feature_screen.py /tmp/all_climax_trades.csv \
    --oos-split 0.7 --wf-windows 4 --effect-min 0.20
```

### Step 4: 결과 판정 (advisor 3자 규율)

Feature Screen 출력에서 **살아남은 후보**만 다음 단계로:
- 3중 방어 전부 통과 (매칭 delta + OOS + walk-forward)
- Cohen's d ≥ 0.20
- Look-ahead 자동 배제됨 (`_LOOKAHEAD_FEATURES` 필터)
- kst_hour 자동 배제 (matched sim 반증)

**threshold 결정 규율** (advisor 2 정정):
- ❌ 관찰된 cutpoint (예: adx_60 > 47.9) 를 그대로 동결 = 사후최적화
- ✅ train window 에서 quantile 규칙 (예: P70 이상) → forward window 에 고정
- ✅ 미리 정의된 임계 (연구 스펙에 사전등록) → forward 에 적용

### Step 5: Loss Attribution 병행

```bash
python3 research/loss_attribution.py /tmp/CS40_VR3_TR180_bp30_240_trades.csv --arm-sec 180 --trail-bp 30
```

출력 카테고리:
- BREAKEVEN_DAMAGE (BE 이동 후 손실)
- PROFIT_GIVEBACK (수익 반납)
- BASE_SL_BEFORE_ARM (A arm 이전 SL)
- TIMEOUT_DECAY (시간 소진 후 손실)
- EARLY_DUMP (초기 하락)
- FAR_STOP (큰 손실)

각 카테고리별 **lever 매칭** 자동 표시 · 어떤 lever 가 어떤 유형에 개입 가능한지 근거.

### Step 6: Counterfactual Replay

```bash
python3 research/live_cohort_resim.py /tmp/all_climax_trades.csv
```

3-arm (CONTROL / A / A×A2) 병렬 재시뮬 · 5조건 사전등록 판정 · **live paired 대신 backtest paired 로 조기 판정**.

---

## 배출 목표 (1-2주 내)

**최소 1개 · 최대 3개** 의 새 frozen HYPOTHESIS 후보:
- 진입시각 확정 feature (look-ahead 아님)
- 3중 방어 통과
- Cohen's d ≥ 0.20
- Threshold 는 quantile rule 또는 pre-registered

**나올 수 있는 결론 (advisor 3자 명시)**:
- (a) 새 후보 발견 → SHADOW 승격 → forward window 관측
- (b) 후보 없음 = **"이 CLM 계열은 이 파라미터에서 엣지 없음 · 다른 진입신호 설계 필요"** 유효한 결론

---

## 데드라인 (advisor 2 프레이밍 · 거래 수 기반)

**A 실험**: `_common_cohort_paired_summary` 자동 판정 (task #63 · bot.py 배선 완료)
- common_n=30 도달 시 강제 판정
- |ΔA| < 0.05%p → **폐기 후보** (엣지 미확인 · 새 후보로 자원 전환)
- ΔA ≥ +0.10%p → **연장** (50 도달까지 검증)
- 경계 → 50까지 방향 확정 대기

**A2 실험**: `_a2_audit_summary` 자동 판정 (task #63 · bot.py 배선 완료)
- eligible ≥ 100 · 실 VP < 5 → **EXPERIMENT_INFEASIBLE 확정** (종료)
- eligible ≥ 50 · 실 VP < 3 → 조기 경보

**새 후보 발굴 데드라인**: 1-2주 내 최소 2개 shadow 승격.

---

## 규율 (불변)

- **cutoff 3.5 FROZEN · 하한 3.0 유지 · arm/bp/gate/adx/rsi/EarlyCut 전부 무변경**
- 성과 판정: paired common_n=30 (첫판정) · 50 (확인) · 100 (승격)
- 룩어헤드 자동 배제 (mfe_*, dd_*, mae_*, hold, exit_reason, kst_hour)
- Threshold 사후최적화 금지 · quantile rule OR pre-registered

## 무변경 (advisor 3자 합의)

- max_positions 확대 X (paired 축적 안 당김 · 돈 위험만 증가)
- daily 한도 완화 X (검증 안 된 breakeven 전략 손실 방지 가드)
- 계측 추가 X (bug fix 만)
- LIVE Research Top3 극소액 승격 X (offline 검증 통과 후에만)

---

## 참조

- research/feature_screen.py (엔진 · advisor 3자 스펙)
- research/loss_attribution.py (rule-based 손실 분류)
- research/live_cohort_resim.py (3-arm counterfactual replay)
- research/PR2_LEVER_A_SPEC.md (A/A2 실험 계약)
- bot.py:2408- (COMMON_COHORT 데드라인 · task #63)
- bot.py:2334- (A2 EXPERIMENT_INFEASIBLE · task #63 강화)
