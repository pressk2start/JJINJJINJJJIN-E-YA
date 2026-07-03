# Research Engine

**목표**: 30일 안에 CLM 전략의 계속 여부(A/B/C) 결론.

## 왜 필요한가

지난 6개월간 LIVE + shadow 방식으로 CLM 파라미터 튜닝을 반복했으나 결론 없음.

문제:
- Live-shadow는 하루 4~10건, 유의미한 판정까지 몇 주
- 얇은 알파(+0.01%)를 튜닝해도 노이즈에 묻힘
- "질문 자체"가 잘못됨: "EC30이 좋나?" 대신 "CLM 이론 상한이 얼마?"

## 30일 로드맵

### 1주차 — 데이터 확보 + Ceiling 최종 판정
- `01_collect.py`: Upbit API로 3~6개월치 1초/10초/1분 캔들 다운로드
- `02_label.py`: 진입 후 10/20/30/60/120/180/300s pnl/mfe/mae 라벨링
- `04_ceiling.py`: Perfect Exit/Entry/Both 확정
- **결과**: 이론 상한 확정 → 계속 여부 1차 판단

### 2주차 — Exit 백테스트
- `05_backtest_exit.py`: 진입 고정, 청산 조합 sweep
- 테스트: AT / LIVEEXIT / PP20/30/40 / EC_A / EC30_T1/T2/T3 / EC30+AT / EC30+PP30
- **답**: 청산만 바꿔서 +0.10% 이상 개선 가능한가?

### 3주차 — Entry 필터 테스트
- `06_backtest_entry.py`: body_pct / wick / close_strength / RSI / ATR / spread sweep
- Walk-forward 3분할 검증 (표본 n≥300, 2개 이상 양수만 통과)
- Decision Tree depth=3로 규칙 자동 추출
- **답**: 진입 필터가 실제 edge 만드는가?

### 4주차 — 결론 A/B/C 강제
- A: CLM 계속 (backtest + shadow 모두 양수)
- B: Exit만 개선 (entry edge 약하나 EC/PP로 방어)
- C: CLM 폐기 (MFE 자체 약함, 새 전략 탐색)

## 파일 구조

```
research/
├── README.md              — 이 문서
├── 01_collect.py          — Upbit 캔들 다운로드
├── 02_label.py            — 진입 이벤트 라벨링
├── 03_feature.py          — 200+ feature 생성
├── 04_ceiling.py          — Perfect Exit/Entry/Both 계산
├── 05_backtest_exit.py    — Exit sweep
├── 06_backtest_entry.py   — Entry filter sweep + Decision Tree
├── 07_report.py           — 결과 리포트 생성
└── data/                  — 캔들 데이터 (gitignore)
    ├── candles_1s/
    ├── candles_10s/
    └── candles_1m/
```

## 원칙

1. **LIVE bot.py 동결** — 새 route/threshold/필터 추가 금지 (4주 내내)
2. **재현 가능성** — 모든 스크립트 seed 고정, 동일 입력 = 동일 출력
3. **표본 최소 n≥300** — 그 이하는 결론 안 냄
4. **Walk-forward** — Train/Test 분할, 단순 in-sample 최적화 금지
5. **수수료/슬리피지 반영** — Upbit 실전 수수료 0.05% × 2 + 슬리피지 0.05% 최소

## 진행 상황

- [x] 폴더 스켈레톤 생성
- [ ] 01_collect.py 완성
- [ ] 02_label.py 완성
- [ ] 04_ceiling.py 완성 (Ceiling 최종 판정)
- [ ] 1주차 결과 리포트
