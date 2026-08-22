# Swing Research (Protocol v1.7) — WIP

> ⚠ **HASH READY = NO.** 이 폴더는 개발 진행 상태(WIP)로 vsfec 브랜치에 배치됨.
> hash 봉인은 아래 blocker 5개 + entry-gap unit test 해결 후 별도 timing에 사용자 손으로.

## 파일 구성

- `swing.py` — 스윙 전략 라이브러리 ([STATS] · [DATA] · [ENGINE] · [UNIVERSE])
- `tests.py` — 오프라인 유닛 테스트 러너 (A/B/C · engine · preflight)
- `run_robustness.py` — 공식 robustness 실행 (hash 후 1회 전용, 성과 수치 출력)
- `live_swing.py` — 라이브 봇 skeleton (4중 안전 게이트, 현재 paper 강제)
- `telegram_notify.py` — 텔레그램 알림 (환경변수 없으면 stdout fallback)
- `swing.service` · `swing.timer` — systemd 유닛 템플릿 (미설치)
- `swing_deploy.sh` — 배포 스크립트 템플릿 (서버 미실행)
- `DEPLOY_BLUEPRINT.md` — 인프라 격리 설계 문서
- `strategy_protocol_v1.md` — Protocol v1.7 원문 (별도 파일)

## 실행

```bash
# 오프라인 유닛 테스트 (result-independent, 성과 수치 미출력)
python3 tests.py

# smoke는 네트워크 필요 (2018 throwaway mechanical)
python3 tests.py smoke

# live skeleton (paper 모드 강제 · 4중 게이트 미통과)
python3 live_swing.py

# 텔레그램 알림 테스트 (환경변수 TELEGRAM_TOKEN + TELEGRAM_CHAT_ID 설정)
TELEGRAM_TOKEN=xxx TELEGRAM_CHAT_ID=yyy python3 telegram_notify.py "테스트"
```

## Outstanding audit blockers (hash 전 필수 해결)

### 1. open sizing map ↔ close MTM map 분리 ❌
현재 `simulate()`의 `eq_now = book.equity(price_of)`와 `Book.open()`의 `room_gross` 모두
`price_of`가 today's close 반환 → t+1 open 체결 시점에 today's close 참조 = look-ahead 1 bar.
**Fix:** `sizing_price_of(open)` / `mtm_price_of(close)` 두 함수 분리.

### 2. full calendar timeline ❌
현재 `run_robustness.master_dates()`가 coin dates union.
전체 coin이 결측인 날 = dates에서 누락 → simulate loop 미방문 → held position stealth carry-forward.
**Fix:** `SW.full_calendar(start, end)` 로 START~END 전체 달력일 방문.

### 3. dynamic point-in-time eligibility + §5 30d turnover + halt ❌
현재 `build_universe()`는 static filter (전체 기간 in/out).
Protocol §5는 각 날짜 t에서 `listing_age(t) ≥ 180 AND 30d turnover ≥ threshold AND not halted`.
**Fix:** 매일 재계산. 30d turnover threshold 값은 사용자 결정 필요.

### 4. skew/kurt central-moment 정확 고정 ⚠
현재 hybrid (z분모 ddof=1 + 집계 /n).
**Fix:** pure Fisher-Pearson `m_k = Σ(x-μ)^k / n`, `skew = m3/m2^1.5`, `kurt = m4/m2²`.
Protocol §4.2 문구도 정확 공식으로 명시.

### 5. snapshot pre-hash vs post-hash provenance ❌
현재 snapshot이 `run_robustness.py` 실행 시 생성 = post-hash artifact.
Protocol "hash commit에 동봉" 문구와 불일치.
**Fix (A):** pre-hash fetch 별도 명령, snapshot을 봉인 commit에 포함.
**Fix (B):** protocol 문구 수정하여 post-hash artifact 인정.

### + entry-gap unit test
`InvalidRun`이 held-coin gap에 대해 raise되는지 검증하는 unit test가 아직 tests.py에 없음.
현재 코드는 `for m in book.pos: if coins[m]["idx"].get(d) is None: raise InvalidRun` 있으나
entry-pending 결측(silent skip)에 대해서는 raise 안 함.
**Fix:** entry-pending 결측도 raise + unit test 추가.

## Live 실주문 하드락 (4중 게이트)

`live_swing.py`의 `place_upbit_order()`는 `SafetyBlocked` raise로 하드락:

1. `AUTO_TRADE=1` 환경변수
2. `.sealed_spec_marker` 파일 존재
3. `.prospective_pass_marker` 파일 존재
4. `--arm-live` CLI 플래그

하나라도 실패 = paper 모드 강제 + 텔레그램 `SAFETY BLOCK` 알림.
현재 상태에서 라이브 실행 시도해도 실주문 절대 불가.

## Protocol v1.7 순서 요약

1. 서버 봇 5개 안전확인 + `systemctl mask momentum-*` (사용자 손)
2. 위 blocker 5개 + entry-gap test 해결 → `python3 tests.py` PASS 확인
3. hash 봉인 (사용자 손, 별도 새 repo 권장)
4. `python3 run_robustness.py` 단 1회 (tag된 커밋에서)
5. PASS → prospective shadow (paper, 12/24/36 look α-spending PSR ≥ 0.9833)
6. prospective PASS → live arm 별도 결정
