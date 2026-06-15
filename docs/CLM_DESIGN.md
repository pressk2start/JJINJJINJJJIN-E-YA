# momentum_scanner v3.0 CLM 설계 문서

## Multi-TF RSI 과열감지 + 코호트 등급화 (CLM_A 채택)

상태: **설계 단계** — 외부 시스템 통계로부터 역공학된 가설
대상: v2.1 SVE1 DNA의 구조적 손실 문제, 그리고 직전 V3 PBR 설계의 한계 동시 해결

> ⚠ **중요 경고 (읽고 시작할 것)**
>
> 1. 이 설계는 **외부 멀티전략 시스템의 출력 통계만 보고 역공학한 가설**입니다.
>    실제 CLM 코드를 본 게 아니라 d-top feature importance와 코호트 PnL만 보고 추정한 것.
> 2. **RSI 임계치·EMA spread 컷·코호트 분류 규칙은 모두 educated guess**입니다.
>    초기 paper run에서 캘리브레이션 필수. 그 전에 실거래 금지.
> 3. **v2.1은 v3 CLM이 검증되기 전까지 계속 운영**합니다.
>    별도 파일/별도 프로세스로 paper run, 둘 다 50건 누적 후 비교.
> 4. 직전 V3 PBR(눌림 재돌파) 설계는 외부 데이터 기준 PnL +0.01% (n=457)로
>    "거의 0". 그래서 같은 V3 슬롯을 CLM에 할당 — PBR 코드는 폐기 또는 보류.

---

## 1. 동기 (왜 CLM인가, 왜 지금)

### 1.1 v2.1 (SVE1 DNA) 21건 회고 — 구조적 한계 확정

| 지표 | 값 | 의미 |
|---|---|---|
| PF | 0.16 | 구조적 손실 |
| WR | 14% | 7건 중 1건만 승리 |
| MFE 승리 평균 | +0.314% | 이긴 거래는 평균 0.3% 이상 감 |
| MFE 패배 평균 | +0.057% | 진 거래는 진입 후 거의 못 감 |
| **MFE 갭 비율** | **5.5x** | **진입 품질 자체가 구조적으로 갈림** |
| vol_z 게이트 | 3회 regime flip | 한 regime의 신호가 다음 regime에서 노이즈 |
| Gate 거절 거래 WR | 27~42% | **게이트가 통과시킨 거래(WR 14~21%)보다 거절한 게 더 좋음** |
| 유니버스 가지치기 | SUI→SOL 이동 | whack-a-mole 확정 |
| z 3~5 vs 5~7 | 25% vs 8% (3회 재현) | 낮은 z가 나음 — 그래도 적자 |

핵심 해석: v2.1은 **신호 검출 시점이 추세의 끝**(SVE1 시그니처). 어느 종목을 빼도, 어떤 게이트를 조여도 동일 — **신호 정의 자체를 바꿔야** 함.

### 1.2 외부 멀티전략 시스템 데이터

| 전략 | n | PnL/trade | 추세 | 우리 매핑 |
|---|---|---|---|---|
| SVE1 (모멘텀GT) | 1,868 | -0.05% | ↘ decay | v2.1과 동일 DNA |
| PBR (눌림재돌파) | 457 | +0.01% | flat | 직전 V3 설계 (사실상 무에지) |
| **CLM_A (과열감지 A코호트)** | **292** | **+0.05%** | **↗ cont** | **본 문서 채택** |

### 1.3 CLM 코호트 생존 분석 (A vs C)

| 코호트 | WR | PnL/trade | 비고 |
|---|---|---|---|
| **A 코호트** | **38%** | **+0.18%** | 진입 직후 추가 모멘텀 동반 |
| C 코호트 | 15% | -0.49% | 진입 직후 식어버린 케이스 |
| Lift (A−C) | +23%p WR / +0.67% PnL | — | **코호트 분리 자체가 알파** |

→ "과열을 감지한다"가 핵심이 아님. **"과열 직후 어떤 코호트로 진입할지"가 핵심**.

### 1.4 외부 시스템 d-top features for CLM

| feature | 중요도 (정규화) |
|---|---|
| rsi_15m | 1.02 |
| rsi_5m | 0.94 |
| ema_spread_15 | 0.74 |

해석:
- RSI **두 개 TF가 동시에** 과열 — 단일 TF 노이즈 배제
- EMA spread (단/장기 이격) 동반 — 추세 가속도 확인
- 즉 CLM = **multi-TF 모멘텀의 동조 과열**, v2.1의 1초 z-score와 본질적으로 다른 시간 스케일

---

## 2. 핵심 아이디어

```
v2.1 SVE1: 1초 tick z-score 폭발 → 즉시 진입 (끝물 잡음)
V3 PBR:    1초 tick 신호 → 눌림 대기 → 재돌파 (외부 PnL ~0)
V3 CLM:    5m + 15m RSI 동시 과열 + EMA spread + 코호트 A 예측 → 진입
```

근거:
- 단일 1초 z-score는 "지금 튀고 있다"만 알려줌 → 끝물 위험 큼
- 15m / 5m RSI 동시 과열 = **여러 시간 스케일이 합의한 모멘텀**
- 진입 시점에 코호트 A를 미리 가려내는 게 최대 알파 (+0.67 lift)
- 우리는 "과열이 시작된 직후"를 잡되, "C로 갈 것 같은 케이스를 사전 거절"

**한 줄 요약**: "튀는 종목" 잡지 말고 **"여러 TF가 합의한 과열 + A 코호트 후보"** 잡기.

---

## 3. CLM 진입 조건 (multi-TF 과열 감지)

종목이 다음 조건을 **모두** 만족할 때 진입 의도 발생.

### 3.1 RSI 동시 과열 (2 TF)

| 조건 | 초기 추정 | 근거 |
|---|---|---|
| `rsi_5m` | **≥ 70** | 단기 과열, 통상 RSI 과매수 컷 |
| `rsi_15m` | **≥ 65** | 중기에서도 과열 진입, 추세 동조 |
| `rsi_5m - rsi_15m` | **0 ~ +15** | 단기가 중기보다 살짝 위 (가속), 둘 다 같으면 정체, 단기가 너무 아래면 식는 중 |

> 외부 d-top: rsi_15m(1.02) > rsi_5m(0.94) — 15m이 약간 더 중요. 초기 컷은 15m 65 고정, 5m은 70 시도 후 데이터로 65~75 범위 캘리브레이션.

### 3.2 EMA spread (이격도)

| 조건 | 초기 추정 | 의미 |
|---|---|---|
| `ema_spread_15` = (price − ema_15m_20) / ema_15m_20 × 100 | **≥ 0.6%** | 15분 EMA20 대비 가격이 0.6% 이상 위 → 단/중기 이격 충분 |
| `ema_spread_15` 상한 | **≤ 3.0%** | 너무 이격되면 평균 회귀 위험 (RSI 80+의 끝물) |

밴드형 컷이라는 점이 중요. 단순 ">" 가 아닌 [0.6, 3.0] 범위.

### 3.3 거래대금 동조 (보조)

v2.1 vol_z는 regime flip 문제 있었음 → CLM에서는 **메인 게이트 아님**, 단순 sanity check로만 사용.

| 조건 | 초기 추정 |
|---|---|
| `vol_z_5m` (5분 캔들 거래대금 z) | ≥ 1.0 (느슨) |
| `vol_ratio_5m` (직전 5분 vs 그 전 30분 평균) | ≥ 2.0 |

### 3.4 코호트 A 사전 예측 (가장 중요한 게이트)

A vs C lift가 +0.67이므로 **이 분류가 사실상 진입의 핵심**. 자세한 정의는 §4.

### 3.5 종합 진입 게이트

```
ENTRY_CLM 7 조건 (ENABLE=7, 즉 모두 만족 요구):

1. rsi_5m  ∈ [RSI_5_MIN,  RSI_5_MAX]   (초기 70~85)
2. rsi_15m ∈ [RSI_15_MIN, RSI_15_MAX]  (초기 65~80)
3. rsi_5m  >= rsi_15m                   (단기 가속)
4. ema_spread_15 ∈ [EMA_SPREAD_MIN, EMA_SPREAD_MAX]  (초기 0.6~3.0)
5. vol_z_5m  >= VOL_Z_5M_MIN  (초기 1.0)
6. vol_ratio_5m >= VOL_RATIO_5M_MIN  (초기 2.0)
7. predicted_cohort == "A"  (§4)
```

> **Phase 분리 (핵심 설계 결정)**:
> - **Phase 1 (CLM_RAW)**: 조건 1~6만 사용 (코호트 예측 OFF). RSI+EMA 신호 자체의 알파를 먼저 검증.
> - **Phase 2 (라벨링)**: Phase 1 데이터 50건으로 실제 A/B/C 사후 라벨링.
> - **Phase 3 (A 예측기)**: 실제 라벨 기반으로 assign_cohort() 작성, 조건 7 활성화.
>
> 근거: CLM 신호와 A 예측기를 동시에 켜면 실패 원인을 분리할 수 없음.
> A 예측기가 틀리면 CLM 자체 성능 평가가 불가능해짐. **신호 먼저, 예측기 나중에.**

---

## 4. 코호트 A/B/C 분류

### 4.1 개념

진입 직후 첫 30초 행동을 기준으로 사후 분류 → 그 분류를 **진입 직전 feature로 예측**.

| 코호트 | 첫 30s MFE | 첫 30s MAE | 후속 PnL 경향 |
|---|---|---|---|
| **A** | ≥ +0.15% | > −0.10% | 추가 모멘텀, A 라벨 |
| B | +0.05 ~ +0.15% | −0.10 ~ −0.20% | 횡보, 중립 |
| **C** | < +0.05% | ≤ −0.20% | 즉시 식음, C 라벨 |

이 임계치도 추정. 데이터 누적 후 quantile split (상위 30% A, 하위 30% C 등)로 재정의.

### 4.2 진입 직전 A 예측 feature (가설)

진입 시점에 우리가 볼 수 있는 정보로 A 가능성 추정:

| feature | A 신호 방향 | 비고 |
|---|---|---|
| `rsi_5m_slope_3` (직전 3개 5m 캔들 RSI 기울기) | 양(+)이면 A 우호 | 식는 중이면 C |
| `ema_spread_15_slope` | 양(+)이면 A | 평균회귀 시작이면 C |
| `vol_z_5m_lastN` (최근 5분 거래대금 평균 z) | 높을수록 A | 거래 유입 지속 |
| `spread_pct_now` (호가 스프레드) | 낮을수록 A | 유동성 좋음 |
| `bid_depth / ask_depth` | 1.0 초과면 A | 매수벽 우세 |
| 직전 10분 신고가까지 거리 | 가까울수록 A | 추세 살아있음 |

### 4.3 구현 순서: 신호 먼저, 예측기 나중에

> ⚠ **Phase 1 (CLM_RAW)에서는 코호트 예측 OFF.**
> 모든 overheat 신호를 A로 간주하고 진입 → 사후 라벨링으로 실제 A/B/C 비율 측정.
> 예측기는 Phase 3에서 실데이터 기반으로 작성.

**Phase 1**: `assign_cohort()` 호출 안 함. detect_overheat 통과 = 진입.
모든 진입에 대해 30s/60s/180s MFE/MAE 기록 → 사후 코호트 라벨 자동 부여.

**Phase 2** (50건 누적 후): 사후 라벨 분석.
- A/B/C 비율은 어떤가
- A와 C의 PnL 차이가 외부 lift(+0.67)에 근접하는가
- 어떤 진입 시점 feature가 A/C를 분리하는가

**Phase 3** (라벨 분석 후): 간이 룰 예측기 작성.

```python
# Phase 3에서만 활성화 — Phase 1/2에서는 이 코드 사용 안 함
score = 0
if rsi_5m_slope_3 > 0:                score += 1
if ema_spread_15_slope > 0:           score += 1
if vol_z_5m_last3 >= 1.5:             score += 1
if spread_pct_now <= 0.08:            score += 1
if bid_depth_top5 >= ask_depth_top5:  score += 1
if dist_from_10m_high_pct <= 0.15:    score += 1

predicted_cohort = "A" if score >= COHORT_A_SCORE_MIN else "C"
# COHORT_A_SCORE_MIN은 Phase 2 데이터로 결정 (초기 추정 4)
```

feature 목록과 가중치는 Phase 2 데이터가 결정. 위 6개는 가설일 뿐.
데이터 100건 누적 후 logistic regression 또는 단순 의사결정 트리로 재학습 검토.

---

## 5. 청산 (v2.1 호환)

**중요**: 청산 인프라는 v2.1 그대로 유지. 신호 정의만 바뀜.

| 파라미터 | v2.1 값 | CLM v3 값 | 비고 |
|---|---|---|---|
| `TARGET` | 0.25% | **0.25%** | 동일 |
| `TRAIL` | 0.25% | **0.25%** | 동일 |
| `TIMEOUT` | 180s | **180s** | 동일 |
| `EARLY_DD` | 0.35% (진입가 기준) | **0.35%** | 동일 |
| `MAX_HOLD` | 180s | 180s | 동일 |
| `FEE` | v2.1 그대로 | 그대로 | — |
| `MAX_SPREAD`, `MIN_*_KRW` | 그대로 | 그대로 | 진입 시점 게이트 |
| `TOP_N`, `COOLDOWN` | 그대로 | 그대로 | — |

**EARLY_DD는 진입가 기준**(평단가 −0.35%)으로 유지. CLM A 코호트 정의가 "30s 내 MAE > −0.10%"이므로 EARLY_DD 0.35는 충분한 여유. 단 진짜 A가 아닌데 진입한 케이스를 빠르게 잘라내는 안전판.

---

## 6. 데이터 구조

### 6.1 신규 글로벌

```python
candles_5m  = {}   # market → deque[Candle] (maxlen=40, 5m × 40 = 약 3.3시간)
candles_15m = {}   # market → deque[Candle] (maxlen=40, 15m × 40 = 약 10시간)
rsi_history = {}   # market → {"5m": deque[float], "15m": deque[float]} (maxlen=20)
ema_history = {}   # market → {"15m_20": deque[float]} (maxlen=20)
candle_last_fetch = {}  # market → timestamp (다음 fetch 스로틀)
```

### 6.2 Candle dict 구조

```python
{
    "ts": 1717225200,         # 캔들 시작 시각 (epoch sec, 정렬된 5m/15m boundary)
    "open": 99100.0,
    "high": 99850.0,
    "low":  99050.0,
    "close": 99780.0,
    "volume_krw": 1.23e9,     # 거래대금 (KRW)
}
```

### 6.3 메모리 영향

- 종목당 5m 40개 + 15m 40개 ≈ 80 캔들 × ~80B = ~6.4KB
- RSI/EMA history ≈ 1KB
- 30종목 모니터: ~7.4KB × 30 = **~220KB** — 무시 가능

---

## 7. 신규 함수 (pseudocode)

### 7.1 멀티 TF 캔들 fetch

```python
def fetch_candles_multi_tf(market, now_ts) -> bool:
    """업비트 캔들 API에서 5m, 15m을 가져와 캐시 업데이트.
       성공 시 True, rate limit/error 시 False."""
    last = candle_last_fetch.get(market, 0)
    if now_ts - last < CANDLE_FETCH_INTERVAL_SEC:  # 초기 30s
        return True  # 캐시 신선

    try:
        c5  = api_get_candles(market, unit=5,  count=40)
        c15 = api_get_candles(market, unit=15, count=40)
    except RateLimitError:
        return False

    candles_5m[market]  = deque(c5,  maxlen=40)
    candles_15m[market] = deque(c15, maxlen=40)
    candle_last_fetch[market] = now_ts
    return True
```

> 업비트 캔들 API는 분당 호출 한도 있음 → §8.2 rate limit 처리.

### 7.2 RSI / EMA 계산

```python
def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_ema(closes: list[float], period: int = 20) -> float | None:
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period  # SMA seed
    for px in closes[period:]:
        ema = px * k + ema * (1 - k)
    return ema
```

### 7.3 과열 감지

```python
def detect_overheat(market, ticker, now_ts) -> dict | None:
    if not fetch_candles_multi_tf(market, now_ts):
        return None  # rate limit, 다음 tick 재시도

    c5  = candles_5m.get(market)
    c15 = candles_15m.get(market)
    if not c5 or not c15 or len(c5) < 15 or len(c15) < 21:
        return None  # warmup

    closes_5  = [c["close"] for c in c5]
    closes_15 = [c["close"] for c in c15]

    rsi_5  = calc_rsi(closes_5,  14)
    rsi_15 = calc_rsi(closes_15, 14)
    ema_15_20 = calc_ema(closes_15, 20)
    if rsi_5 is None or rsi_15 is None or ema_15_20 is None:
        return None

    price = ticker["trade_price"]
    ema_spread = (price - ema_15_20) / ema_15_20 * 100

    vol_z_5m, vol_ratio_5m = candle_volume_stats(market, c5)

    # 조건 (§3.5)
    if not (RSI_5_MIN  <= rsi_5  <= RSI_5_MAX):  return None
    if not (RSI_15_MIN <= rsi_15 <= RSI_15_MAX): return None
    if rsi_5 < rsi_15:                            return None
    if not (EMA_SPREAD_MIN <= ema_spread <= EMA_SPREAD_MAX): return None
    if vol_z_5m  is None or vol_z_5m  < VOL_Z_5M_MIN:  return None
    if vol_ratio_5m is None or vol_ratio_5m < VOL_RATIO_5M_MIN: return None

    return {
        "rsi_5": rsi_5,
        "rsi_15": rsi_15,
        "ema_spread_15": ema_spread,
        "vol_z_5m": vol_z_5m,
        "vol_ratio_5m": vol_ratio_5m,
        "closes_5": closes_5,
        "closes_15": closes_15,
    }
```

### 7.4 코호트 A 예측

```python
def assign_cohort(market, overheat_meta, ticker) -> str:
    closes_5 = overheat_meta["closes_5"]
    closes_15 = overheat_meta["closes_15"]

    # 1. RSI slope (최근 3개 5m 캔들에 대한 RSI)
    rsi_5_series = [
        calc_rsi(closes_5[:i+1], 14)
        for i in range(len(closes_5)-3, len(closes_5))
    ]
    rsi_5_series = [r for r in rsi_5_series if r is not None]
    rsi_slope_pos = len(rsi_5_series) >= 2 and rsi_5_series[-1] > rsi_5_series[0]

    # 2. EMA spread slope (대략): 직전 5분 가격 vs 현재 가격의 spread 차
    prev_price = closes_5[-2]
    prev_ema = calc_ema(closes_15[:-1], 20) or closes_15[-2]
    prev_spread = (prev_price - prev_ema) / prev_ema * 100
    ema_spread_slope_pos = overheat_meta["ema_spread_15"] > prev_spread

    # 3. 최근 3개 5m 캔들 평균 vol_z
    vol_z_last3 = overheat_meta["vol_z_5m"]  # 근사: 현재 vol_z를 사용
    vol_ok = vol_z_last3 >= 1.5

    # 4. 호가
    spread_pct = ticker.get("spread_pct", 1.0)
    spread_ok = spread_pct <= 0.08

    # 5. 매수/매도 깊이
    bid_d = ticker.get("bid_depth_top5", 0)
    ask_d = ticker.get("ask_depth_top5", 1)
    depth_ok = bid_d >= ask_d

    # 6. 10분 신고가까지 거리
    recent_high = max(closes_5[-2:])  # 직전 2개 5m 캔들
    dist_pct = (recent_high - ticker["trade_price"]) / recent_high * 100
    near_high = dist_pct <= 0.15

    score = sum([rsi_slope_pos, ema_spread_slope_pos, vol_ok,
                 spread_ok, depth_ok, near_high])
    return "A" if score >= COHORT_A_SCORE_MIN else "C"
```

### 7.5 main loop 변경

```python
for ticker in tickers:
    market = ticker["market"]
    if market in positions: continue
    if in_cooldown(market, now_ts): continue

    overheat = detect_overheat(market, ticker, now_ts)
    if overheat is None:
        continue

    # Phase 1 (CLM_RAW): 코호트 예측 OFF — overheat 통과 = 진입
    # Phase 3 이후 아래 블록 활성화:
    # cohort = assign_cohort(market, overheat, ticker)
    # if cohort != "A":
    #     log_skipped(market, "cohort_C", overheat)
    #     continue

    # 기존 v2.1 진입 로직: ob 재조회 → spread/depth 최종 검사 → positions[] 추가
    enter_position(market, ticker, now_ts, meta={
        "strategy": "CLM_RAW",  # Phase 1. Phase 3 이후 "CLM_A"로 변경
        "overheat": overheat,
    })
```

---

## 8. 엣지케이스

### 8.1 캔들 warmup

**문제**: 봇 부팅 직후 캔들 캐시가 비어 있음.

**대응**: 캔들 부족 (5m < 15개, 15m < 21개) 시 `detect_overheat` None 반환 → 자동 대기. 봇 시작 후 약 15분간은 사실상 진입 불가.

### 8.2 API rate limit (가장 큰 신규 리스크)

**문제**: 업비트 캔들 API는 분당 호출 한도(공개 ~600/min) 있음. 30종목 × 5m + 15m = 60 req → 매 tick fetch면 한도 초과.

**대응**:
- `CANDLE_FETCH_INTERVAL_SEC = 30` (30초마다 같은 종목 재fetch). 5m/15m TF니까 30s 캐시는 충분.
- ticker tick은 1초마다 오지만 캔들은 30초 캐시 → 30종목이라도 분당 60 × 2 = 120 req 수준.
- 그래도 한도 근접 시 fetch 실패 → return None → 자연 backoff.
- 동일 분 안에 같은 market 재요청 안 하도록 `candle_last_fetch` 강제.

### 8.3 multi-TF 경계 (캔들 미닫힘 케이스)

**문제**: 5m 캔들이 아직 닫히지 않은 진행 중 캔들을 마지막에 포함하면 RSI 노이즈.

**대응**: API에서 받은 가장 최근 캔들은 "진행 중"일 수 있음 → 캔들 ts가 `now_ts - (now_ts % 300)`보다 작으면 닫힌 것으로 간주. 진행 중 캔들은 별도 분리해서 RSI는 닫힌 캔들로만 계산, ema_spread는 현재가 사용.

### 8.4 RSI/EMA 경계값

**문제**: avg_loss=0 → RSI 100 (강한 상승만), 코너 케이스.

**대응**: `calc_rsi`에서 명시 처리(이미 위 코드 포함). RSI=100은 진입 컷 통과시키되, `RSI_5_MAX=85` 컷에 걸려 거절됨.

### 8.5 코호트 분류 데이터 부족

**문제**: 5m 캔들 < 5개이면 RSI slope 계산 불가.

**대응**: slope 항목 점수 0 처리. score 최대치만 낮춰 보수적으로 판정. 캔들 충분해질 때까지 사실상 A 거의 안 나옴 → 자연 안전.

### 8.6 v2.1과 동일 ticker 갭

**문제**: STALE_GAP_SEC 초과 시 캔들과 ticker 불일치.

**대응**: v2.1과 동일하게 종목 skip. 캔들 캐시는 다음 tick에 재fetch 시 갱신.

### 8.7 ENTERED 이후 같은 종목 재신호

**대응**: `if market in positions: continue` 가드. v2.1과 동일.

### 8.8 CLM 코호트 사후 검증 (필수)

**왜**: 우리가 분류한 A가 실제로 +0.18% PnL을 내고 있는지 측정 안 하면 §4의 score 룰이 맞는지 모름.

**구현**: 진입 후 30s, 60s, 180s 시점의 MFE/MAE를 기록 → 실제 코호트(A/B/C) 사후 라벨링 → 진입 시 예측 코호트와 비교 → confusion matrix.

**판단**:
- A 예측 → 실제 A 비율 ≥ 50%면 score 룰 유효
- A 예측이지만 실제 C가 50%↑이면 score 룰 폐기, feature 재설계

이거 없으면 v3 CLM도 검증 불가.

---

## 9. v2.1 → v3 CLM 마이그레이션

1. **별도 파일**: `momentum_scanner_clm.py` (또는 동일 파일 내 `STRATEGY=CLM` 분기).
2. **같은 유니버스, 같은 청산 인프라**: TOP_N, 종목 후보군, exit 로직 모두 그대로.
3. **다른 점**: 진입 트리거만 SVE1 → multi-TF RSI + EMA + 코호트.
4. **병행 paper run**: v2.1 라이브 + CLM paper 둘 다 50건씩.
5. **비교 기준**:
   - PnL/trade
   - MFE 승/패 갭 (v2.1의 5.5x가 줄어드는가)
   - WR (외부 데이터 38% 목표)
   - A 예측 적중률
6. **승격 조건**: CLM PnL/trade > v2.1 AND A 예측 적중률 ≥ 50% → CLM을 메인으로, v2.1 종료.
7. **이전 V3 PBR 코드**: 외부 데이터 기준 +0.01% → 폐기 또는 별도 archive. CLM과 PBR 동시 운영은 안 함 (혼란).

---

## 10. 테스트 계획

### 10.1 단위 테스트

| 대상 | 케이스 |
|---|---|
| `calc_rsi` | 알려진 closes 시퀀스 (TradingView/외부 라이브러리와 일치) |
| `calc_rsi` 경계 | 전부 상승 → 100, 전부 하락 → 0, 동일가 → 50 또는 None |
| `calc_ema` | period보다 짧은 입력 → None, 정확한 SMA seed 검증 |
| `assign_cohort` | mock ticker/overheat dict로 score 0~6 전체 분기 |

### 10.2 시나리오 테스트 (mock detect_overheat)

| 시나리오 | 입력 | 기대 |
|---|---|---|
| 5m RSI 75, 15m RSI 70, spread 1.2% | 모든 vol/depth OK | ENTER (A) |
| 5m RSI 75, 15m RSI 60 | 15m 미달 | 거절 |
| 5m RSI 90, 15m RSI 80, spread 3.5% | 너무 과열 (끝물) | 거절 (spread max) |
| 5m RSI 65, 15m RSI 70 | 5m < 15m (식는 중) | 거절 (조건 3) |
| 모든 RSI/EMA OK, 코호트 score 2 | C 예측 | 거절 (코호트 C) |

### 10.3 캔들 replay 백테스트

- v2.1의 21건 거래 시점 ±5분 캔들 데이터 수집
- CLM 신호가 같은 시점에 발생하는지 / 다른 시점인지 검사
- 다른 시점에 발생한다면 그 시점이 실제 어떻게 됐는지 가격 추적
- 목표: v2.1 21건 중 진입 시점에서 CLM은 **몇 건이나 거절하는가**, 거절한 거래의 실제 PnL은?

### 10.4 회귀 테스트

v3 PBR 설계의 회귀 케이스(있다면) 입력 → CLM이 같은 시점에 진입하지 않는지 검증. PBR과 CLM 신호가 겹친다면 한 가지 알파를 다른 이름으로 부르는 것일 뿐.

---

## 11. 미해결 질문

| # | 질문 | 결정에 영향 |
|---|---|---|
| 1 | **RSI 임계치 정확한 값**: 5m 70 vs 65 vs 75 어느 게 +0.05% PnL을 재현? | RSI_5_MIN/MAX 캘리브레이션 |
| 2 | EMA spread 상한 3.0% 적절한가, 2.0% / 4.0% 시 진입 빈도/품질? | EMA_SPREAD_MAX |
| 3 | 코호트 A 예측 feature 6개 중 실제로 분류력 있는 건 몇 개? | score 룰 재설계 |
| 4 | multi-TF 가중치 (rsi_15m이 정말 0.74×rsi_5m보다 중요한가) | weighted score 도입 여부 |
| 5 | **ENABLE 7 조건의 실제 정의**: 외부 시스템의 ENABLE 7이 우리가 추정한 7개와 같은가? 다르면 어떤 7개? | 진입 게이트 구성 |
| 6 | 코호트 분류 임계 (30s MFE +0.15% 등) — quantile split이 더 나은가? | A/B/C 정의 |
| 7 | CLM_A vs CLM_B 진입 — A만 진입할지, A+B 둘 다 진입할지 | 진입 빈도 trade-off |
| 8 | 캔들 fetch 간격 30s가 충분한가, 60s까지 늘려도 신호 손실 없는가? | API 부하 |

1, 5번이 최우선. 나머지는 paper 데이터 누적 후.

---

## 12. 알려진 한계와 위험

### 12.1 REST 1초 폴링은 여전히 유지

CLM도 ticker는 1초 REST, 캔들도 REST 30s 캐시. WebSocket 전환은 별도 작업 (v4 영역).

다만 multi-TF RSI는 본질적으로 분 단위 → 1초 폴링이 v2.1만큼 치명적이지 않음. **CLM은 시간 스케일이 v2.1과 달라 REST 1s가 덜 아픔** — 이게 CLM 채택의 부수적 이점.

### 12.2 외부 데이터 의존 — 추정의 누적

- RSI 임계치: 추정
- EMA spread 컷: 추정
- 코호트 A 예측 feature: 추정
- ENABLE 7 정의: 추정

→ 다 맞다는 보장 없음. paper run에서 외부 통계(WR 38%, PnL +0.18%)가 재현 안 되면 **외부 시스템과 다른 무언가가 있다**는 신호 → 재설계.

### 12.3 코호트 가시성 제한

외부 시스템은 어떻게 A/B/C를 정의했는지 모름 — 우리 정의가 다르면 lift도 다름. 사후 라벨링으로 우리 정의의 lift를 측정 후 외부 lift(+0.67)와 비교 필요.

### 12.4 진입 빈도 큰 폭 감소 예상

multi-TF + 7 조건 + 코호트 A 게이트 → v2.1보다 진입 1/5~1/10 예상.

50건 누적까지 v2.1보다 5~10배 시간. paper만 돌리면 비용 0이지만 검증 지연.

### 12.5 캔들 API 의존 (신규 장애 표면)

v2.1은 ticker API만 의존. CLM은 candle API 추가 의존. 캔들 API 장애 시 CLM은 **완전 정지** (overheat 검출 불가). 자동 fallback 없음. v2.1 병행 운영이 hedge.

### 12.6 코호트 재학습 부재

score 룰은 고정. 시장 regime 바뀌면 A 정의도 바뀜. 50건/100건마다 사후 코호트와 예측 비교 → score 가중치 수동 조정 또는 logistic regression 도입 필요. **자동 학습 파이프라인은 v3.1 이후**.

### 12.7 복구 설계

- 봇 재시작 시 캔들 캐시 비어있음 → 첫 ~15분 진입 불가 (warmup)
- 강제 종료 후 재시작 케이스에 한해 캔들 캐시를 디스크에 직렬화하는 옵션 검토 (선택, v3.1)
- 포지션 보유 중 재시작 → v2.1 복구 로직 그대로 재사용 (positions 직렬화)

---

## 13. 다음 행동 (Phase 분리 적용)

> **핵심 원칙**: CLM 신호와 A 예측기를 분리해서 단계적 검증.
> A 예측기가 틀리면 CLM 자체 성능 평가가 불가능 → 신호 먼저 검증.

### Phase 1: CLM_RAW (신호 검증)

1. **구현**:
   - `calc_rsi`, `calc_ema`, `fetch_candles_multi_tf` 작성 + 단위 테스트
   - `detect_overheat` 작성 (조건 1~6, 코호트 예측 없음)
   - 별도 파일 `momentum_scanner_clm.py`
   - 모든 진입에 30s/60s/180s MFE/MAE 자동 기록 (사후 라벨링용)
2. **paper run**: CLM_RAW로 50건 수집
3. **판정 기준**:
   - CLM_RAW PnL/trade > v2.1 PnL/trade? → 신호 자체에 알파 존재
   - MFE 승/패 갭이 v2.1(5.5x)보다 줄었나? → 진입 품질 개선
   - WR이 v2.1(14~21%)보다 높은가?
   - 진입 빈도는 얼마인가? (너무 적으면 의미 없음)

### Phase 2: 사후 라벨링 (A/B/C 정의)

1. Phase 1의 50건 데이터에서 30s MFE/MAE로 A/B/C 사후 라벨 부여
2. 분석:
   - A/B/C 비율 확인 (외부: A ~30% 추정)
   - A vs C PnL 차이 → 외부 lift +0.67 근접 여부
   - 어떤 진입 시점 feature(RSI slope, EMA slope, vol_z, spread, depth, 신고가 거리)가 A/C 분리력 보유하는지
3. **판정**: A/C lift가 유의미하면 Phase 3 진행. lift 없으면 CLM_RAW 상태로 유지 또는 전략 재검토

### Phase 3: A 예측기 구축

1. Phase 2 라벨 데이터 기반으로 `assign_cohort()` 작성
   - feature 선정: Phase 2에서 분리력 확인된 것만
   - 가중치/임계치: 실데이터 기반 (추정 아님)
2. A 예측기 ON 상태로 50건 추가 수집
3. **판정**: 예측 적중률 ≥ 50% AND CLM_A PnL > CLM_RAW PnL → A 게이트 유지

### Phase 4: 승격 결정

- CLM (RAW 또는 A) > v2.1 확정 시 메인 전환
- 그렇지 않으면 §11 미해결 질문 재검토 후 재튜닝 또는 폐기

---

## 부록 A. v2.1 SVE1 vs V3 PBR vs V3 CLM 비교

```
v2.1 SVE1 (현재 라이브, 손실 중)
─────────────────────────────
1초 tick → z_score + vol_z + abs_move + breakout → 즉시 진입
  └─ 끝물/fake breakout 잡음 (MFE 갭 5.5x, WR ~15%)

V3 PBR (외부 +0.01%, 사실상 폐기)
─────────────────────────────
1초 tick → 신호 → CANDIDATE → PULLBACK → CONFIRM → ENTER
  └─ 시간 스케일은 여전히 초 단위, 알파 미미

V3 CLM Phase 1 (CLM_RAW, 본 문서 채택)
─────────────────────────────
1초 tick → 30s 캔들 캐시:
  5m RSI ≥ 70 AND 15m RSI ≥ 65 AND
  rsi_5m > rsi_15m AND
  ema_spread_15 ∈ [0.6, 3.0] AND
  vol_z_5m / vol_ratio_5m OK
  → ENTER (코호트 예측 OFF, 사후 라벨링)

V3 CLM Phase 3 (CLM_A, 데이터 기반 예측기 추가 후)
─────────────────────────────
  Phase 1 조건 + cohort_predicted == "A"
  → ENTER (외부 기대치 WR 38%, +0.18%)
```

핵심 차이:
- v2.1/PBR: 초 단위 z-score
- CLM: 분 단위 multi-TF RSI + 코호트 등급화
- 청산 인프라는 셋 다 동일

---

## 부록 B. 신규 설정값 요약

```python
# === CLM v3.0 신규 설정값 ===

# RSI 컷
RSI_5_MIN  = 70
RSI_5_MAX  = 85
RSI_15_MIN = 65
RSI_15_MAX = 80

# EMA spread (15분 EMA20 대비 가격 이격 %)
EMA_SPREAD_MIN = 0.6
EMA_SPREAD_MAX = 3.0

# 거래대금 (보조)
VOL_Z_5M_MIN     = 1.0
VOL_RATIO_5M_MIN = 2.0

# 코호트 점수
COHORT_A_SCORE_MIN = 4  # 6개 항목 중

# 캔들 fetch
CANDLE_FETCH_INTERVAL_SEC = 30

# 청산 (v2.1과 동일)
TARGET   = 0.25
TRAIL    = 0.25
TIMEOUT  = 180
EARLY_DD = 0.35
```

**모든 값은 초기 추정**. paper run 데이터로 캘리브레이션 전제.

---

문서 끝.
