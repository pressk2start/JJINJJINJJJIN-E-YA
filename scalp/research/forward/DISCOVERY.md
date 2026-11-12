# 후보 탐색 기록

자동으로 만들어지는 파일입니다. 마지막 실행 2026-11-12 (UTC).
이 첫 기록은 틀이 초안인 상태에서 전체 범위로 한 번 돌려 본 결과입니다(등록 잠김).

```
DISCOVER 2026-11-12 | 틀 1 (초안 — 등록 잠김) | 30종목 | 2023-11-13 ~ 2026-11-11 | TRAIN 앞 60%
  받은 봉: ADA 27542, ARB 27532, ARK 27446, AVAX 27538, AXS 27534, BCH 27540, BTC 27545, DOGE 27541, ETH 27546, G 20398, HBAR 27536, ID 24145, IOST 27470, LINK 27537, LSK 27454, NEAR 27538, ONDO 21085, ONG 27482, SAND 27535, SEI 27535, SHIB 27540, SOL 27543, STEEM 27452, STX 27535, SUI 27536, T 27482, UNI 17973, XEC 27525, XLM 27536, XRP 27545
거래 46315건, TRAIN < 2025-08-31 <= HOLDOUT
== BREAKOUT/1 | TRAIN n=4398 days=578 net -0.272% t -5.52 | HOLDOUT n=2509 net -0.204% t -3.34
   TRAIN 최상: btc_ret_24h >= 0.0206  n=1473 net -0.159% t -1.54
   -> TRAIN 기준을 넘은 조건 없음
== BREAKOUT/4 | TRAIN n=3910 days=576 net -0.220% t -2.67 | HOLDOUT n=2256 net -0.210% t -1.98
   TRAIN 최상: btc_ret_24h >= 0.02035  n=1314 net +0.035% t +0.21
   -> TRAIN 기준을 넘은 조건 없음
== BREAKOUT/24 | TRAIN n=3131 days=568 net +0.190% t +0.97 | HOLDOUT n=1861 net -0.015% t -0.06
   TRAIN 최상: btc_ret_24h >= 0.01921  n=1045 net +1.000% t +2.94
   HOLDOUT 재현: n=548 days=98 net -0.222% t -0.45 => 재현 안 됨
== CRASH/1 | TRAIN n=3688 days=452 net +0.192% t +0.83 | HOLDOUT n=1675 net -0.054% t -0.35
   TRAIN 최상: compress <= 1.157  n=1223 net +0.155% t +1.13
   -> TRAIN 기준을 넘은 조건 없음
== CRASH/4 | TRAIN n=3281 days=451 net +0.438% t +1.24 | HOLDOUT n=1471 net -0.458% t -1.73
   TRAIN 최상: btc_trend >= 0.1346  n=1096 net +0.684% t +1.97
   -> TRAIN 기준을 넘은 조건 없음
== CRASH/24 | TRAIN n=2279 days=423 net +1.608% t +2.22 | HOLDOUT n=1045 net -0.323% t -0.66
   TRAIN 최상: compress <= 1.083  n=754 net +1.457% t +2.35
   -> TRAIN 기준을 넘은 조건 없음
== SURGE/1 | TRAIN n=4165 days=562 net -0.164% t -2.25 | HOLDOUT n=1893 net -0.321% t -2.99
   TRAIN 최상: mkt_ret_1h >= 0.01899  n=1389 net +0.125% t +0.66
   -> TRAIN 기준을 넘은 조건 없음
== SURGE/4 | TRAIN n=3612 days=561 net +0.074% t +0.54 | HOLDOUT n=1629 net -0.476% t -2.78
   TRAIN 최상: mkt_ret_1h >= 0.01994  n=1205 net +0.754% t +2.51
   HOLDOUT 재현: n=425 days=44 net -0.352% t -1.00 => 재현 안 됨
== SURGE/24 | TRAIN n=2374 days=546 net +0.968% t +3.09 | HOLDOUT n=1138 net +0.025% t +0.06
   TRAIN 최상: btc_ret_24h <= -0.005979  n=791 net +2.428% t +3.83
   HOLDOUT 재현: n=379 days=145 net +0.288% t +0.37 => 재현 안 됨
훑은 조합 213개 → 발견 0개
새로 등록할 후보 없음
```
