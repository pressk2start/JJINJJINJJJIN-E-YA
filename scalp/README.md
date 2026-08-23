# scalp/ — 스켈핑 연구 (탐색 단계)

> **현재 상태: 임계치 미확정. 전략 코드 없음. 라이브 코드 없음.**
> 이 폴더는 "임계치를 데이터에서 찾는" 탐색 단계 산출물이다.
> bot.py 등 기존 파일 무접촉 (additive-only).

## 왜 전략부터 안 짜는가

초안에서는 swing/ 패턴대로 모멘텀 브레이크아웃 7 trial을 사전등록 격자로 박았다.
그 숫자들(z≥2.0, stop 1.5×ATR, hold 30분)은 **근거 없는 감**이었다.
사전등록은 다중검정 보정을 가능하게 해줄 뿐, 감으로 고른 값을 정당화하지 않는다.
스켈핑은 임계치 하나로 결과가 뒤집히므로 순서를 뒤집었다:

```
데이터 탐색 → 임계치 발견 → OOS/robustness → 전략 코드 → paper → live
```

## API 소급 가능 범위 (2026-08-23 직접 측정)

| 데이터 | 소급 범위 | 확인 방법 |
|---|---|---|
| 1분봉 | 365일+ | `to=-365d` 정상 응답 |
| 초봉 | ~90일 (120일부터 `[]`) | `/candles/seconds` |
| 체결틱 (`ask_bid` → 체결강도·매수비) | **7일** | `daysAgo=8` → HTTP 400 |
| 오더북 (imbalance·spread·호가소진) | **소급 불가** | 현재 스냅샷 전용 |

⇒ `imbalance > 0.65` 류 임계치는 과거 데이터로 탐색 **불가능**. 전방 수집만이 유일한 경로.
⇒ 그래서 `ob_recorder.py`를 먼저 만들었다. 늦게 시작한 만큼은 영구 손실.

## 파일

| 파일 | 역할 |
|---|---|
| `research/collect.py` | 1분봉 수집 (컬럼형 gzip, 무체결 분을 결측으로 보존) |
| `research/features.py` | 분 격자 피처/결과 행렬 (룩어헤드 차단, 진입=t+1 open) |
| `research/sweep.py` | 임계치 스윕 + 양방향 대조 + OOS + walk-forward + 브래킷 |
| `research/seconds.py` | 초봉 이벤트 수집·라벨링 (TP/SL 순서 확정) |
| `research/ob_recorder.py` | 오더북/체결 **무조건부** 레코더 (전방 수집 전용, 읽기만) |
| `backtest_engine.py` | 체결·비용·갭 모델 (전략 아님, 임계치 없음) |

## 기존 레포와의 관계

`research/`(CLM 계열)의 규율을 그대로 따른다 — 관찰 cutpoint 동결 금지,
임계치는 train 분위수 규칙, OOS+walk-forward, n≥300, 수수료 0.05%×2 + 슬리피지.
`data_loader`/`seconds_loader`/`feature_screen` 과 목적이 겹치지 않는다:
기존은 **CLM 신호 조건부** 표본, 여기는 **전 분봉 무조건부** 표본(대조군 확보)이다.

## 실행

```bash
cd scalp/research
python3 collect.py --top 30 --days 90     # 1분봉 수집 (~85분)
python3 features.py                       # 피처/결과 행렬
python3 sweep.py                          # 임계치 탐색 + 3중 방어
python3 seconds.py --events 40            # 초봉 정밀화 (1단계 생존 후보 대상)

# 서버에서 (오더북은 지금부터 쌓아야 나옴)
nohup python3 ob_recorder.py --top 20 --interval 10 > ob_recorder.log 2>&1 &
```
