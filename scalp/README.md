# scalp/ — 스켈핑 탐색 (research-only)

> **라이브 주문 코드 없음.** systemd 유닛도, 배포 스크립트도, 주문 함수도 없다.
> 순서는 `데이터 탐색 → 임계치 발견 → OOS/robustness → 코드 → paper → live`이고 현재 3단계에서 멈췄다.
> `bot.py` 등 기존 파일 무접촉 · 순수 additive.

## 결론

**채택 가능한 신호 없음.** 두 개의 독립 파이프라인이 서로 다른 데이터·해상도·코드로
합계 **693셀**을 검정해 전부 기각했다.

| 문서 | 내용 |
|---|---|
| **[obi_sec/FINDINGS.md](obi_sec/FINDINGS.md)** | 초봉 축 결과 — 비용 실측, 구조적 상한, 모멘텀 단조악화, 반전 기각 |
| **[CROSS_CHECK.md](CROSS_CHECK.md)** | 1분봉 축의 독립 재검정 — 재현·강화·정정 |

핵심만:

- 왕복 비용 실측 **0.217%** (30만원·상위 6종목·호가 사다리 VWAP). 기존 0.20% 가정은 타당.
- 5분 창 평균 최대유리변동이 **+0.195%** — 비용과 같은 자릿수. 평균적인 분엔 먹을 게 없다.
- 모멘텀 추격: 두 축 모두 **양수 셀 0개**, 임계를 조일수록 단조 악화.
- 반전 신호: 통계는 통과하지만 **체결 가정을 현실화하면 소멸**(호가 바운스 착시).
  초봉 축에선 DOGE 76~89% 집중이었으나, **1분봉 축은 최다종목 0.08~0.35인데도 동일하게 소멸** →
  단일종목 현상이 아니라 일반적 결론.
- 메이커 진입: **업비트 KRW는 메이커/테이커 수수료 차등이 없다.** 비용 21.7→16.8bp(−23%)뿐이고
  승률 상한은 6.7%→9.2%. 국면 전환이 아니다. (CROSS_CHECK §3)

## 데이터 제약 (설계가 아니라 API 한계 · 직접 측정)

| 축 | 소급 가능 | 확인 방법 |
|---|---|---|
| 1분봉 | 365일+ | `to=-365d` 정상 응답 |
| 초봉 | ~90일 | `-90d` OK, `-120d` → `[]` |
| 체결 틱 (`ask_bid` → 체결강도·매수비) | **7일** | `daysAgo=8` → HTTP 400 |
| 오더북 (imbalance·spread·호가소진) | **0일** | 현재 스냅샷 전용, `to=` 무시 |

"과거 6개월 오더북 백테스트"는 존재할 수 없다. 우회로도 없다.
`imbalance > 0.65` 류 임계치는 **지금부터 쌓아야만** 나온다.

## 두 축

### `obi_sec/` — 오더북 + 초봉 축

| 파일 | 역할 |
|---|---|
| `scalp.py` | OBI · microprice · spread · 체결강도 · VWAP 비용모델 · 프레임 리플레이 엔진 |
| `collect_frames.py` | 오더북 + 체결 forward 수집기 (호가 사다리 전체 보존 → 임의 주문금액 재계산 가능) |
| `calibrate.py` | 비용 실측 · 특징 분포 · OBI/VP 버킷별 조건부 성과 |
| `sec_collect.py` | 초봉 90일 무작위 표본 수집기 (seed 42) |
| `sec_study.py` | 이벤트 스터디 · 182셀 격자 · OOS · 체결가정/종목집중도 검정 |
| `tests.py` | 오프라인 유닛 테스트 (F/E/G/IO 4스위트, 성과 수치 미출력) |

### `research/` — 1분봉 전수 축

| 파일 | 역할 |
|---|---|
| `collect.py` | 1분봉 수집 (컬럼형 gzip · 무체결 분을 결측으로 보존) |
| `features.py` | 분 격자 피처/결과 행렬 (룩어헤드 차단 · 진입 = t+1 open) |
| `sweep.py` | 임계치 스윕 · 상승/하락 **양방향 대조** · OOS · walk-forward · 브래킷 |
| `reversal_check.py` | 반전 그리드 3중 체결가정 + 종목집중도 검정 (초봉 결론 독립 재검정) |
| `seconds.py` | 초봉 이벤트 수집·라벨링 |
| `ob_recorder.py` | 오더북/체결 **무조건부** 레코더 — 신호와 무관하게 균일 샘플링(대조군 확보) |

`backtest_engine.py`는 1분봉 체결·비용·갭 모델이다. **임계치 하드코딩이 없다** —
초안에 있던 사전등록 7 trial 격자는 근거 없는 감이라 제거했다.

수집기가 두 개인 것은 중복이 아니다: `collect_frames.py`는 호가 사다리를 보존해
비용을 정확히 재계산하고, `ob_recorder.py`는 신호 조건 없이 균일 샘플링해 대조군을 만든다.

## 실행

```bash
# 오프라인 유닛 테스트
cd obi_sec && python3 tests.py

# 초봉 축 (~90일 소급)
python3 sec_collect.py --top 8 --anchors 40 --pages 4
python3 sec_study.py "data/sec_*.jsonl.gz" --horizon 60

# 1분봉 축 (365일+ 소급)
cd ../research
python3 collect.py --top 30 --days 90     # ~25분
python3 features.py
python3 sweep.py
python3 reversal_check.py

# 오더북 축 — forward 전용. 서버에서 상시 실행할 것 (늦게 시작한 만큼 영구 손실)
python3 ob_recorder.py --top 20 --interval 10
cd ../obi_sec && python3 collect_frames.py --top 6 --minutes 60 --interval 2
python3 calibrate.py "data/frames_*.jsonl.gz"
```

원자료는 `.gitignore` 처리 — 커밋하지 않는다 (`research/` 관행과 동일).

## 지킨 규율 (전부 기존 저장소 문서에서 가져옴)

- 비용은 **%p 뺄셈**. 곱셈 haircut 금지 — H2 버그가 부호를 뒤집은 전례 (`ARCHIVE.md:72`)
- 봉/프레임 내부 순서 가정 금지. 동시 트리거는 **최악 선택** (H3, `sweep_full.py:120`)
- 신호와 체결을 같은 봉/프레임에서 처리하지 않음 (look-ahead 차단, 유닛 테스트로 강제)
- 시간순 train/test 분할. 임계치는 train에서만 선택 (`backtest_exit.py:372`)
- 관찰 cutpoint 동결 금지 → **train 분위수 규칙** (`EDGE_DISCOVERY_PLAN.md`)
- 셀 최소 n≥100~300 (`backtest_exit.py:564`)
- 승률 46~54%는 base-rate 노이즈로 표기 (`PR2_LEVER_A_SPEC.md:163`)
- **검정한 셀 개수를 항상 출력** (다중검정 노출 공개)
- 부분체결을 성공으로 위장 금지
- 표본 추출은 균등 무작위 + seed 고정 (`research/README.md:56`)
- 인접 표본 중복 제거(cooldown) + 자기상관 대응(**일자 클러스터 t 통계량**)

## 주의

`obi_sec/scalp.py`의 `DEFAULT_CFG` 임계값은 **검증되지 않은 자리표시자**다.
데이터에서 도출된 값이 아니며 그 값으로 실거래하면 안 된다. 엔진은 리플레이 도구로만 쓴다.

## 출처

`obi_sec/`는 별도 세션(`claude/scalp-v1`)의 산출물이다. 그 세션은 저장소 push 권한이
없어 패치로만 남았고, 여기서 파일을 복원해 합류시켰다.
복원 무결성 확인: 줄 수 일치(scalp.py 451 / tests.py 250 / sec_study.py 497 / calibrate.py 193 /
collect_frames.py 110 / sec_collect.py 126), `tests.py` 4스위트 ALL PASS,
그리고 복원된 격자가 원 보고서의 셀 수를 정확히 재현(GRID 31 + PAIRS 83 = 114, REV 68, 합 182).
`FINDINGS.md` 본문은 원본 그대로이며, 추가한 것은 상단 4줄 상호참조뿐이다.
