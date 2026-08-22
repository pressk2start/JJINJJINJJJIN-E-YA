# Swing Research (Protocol v1.7) — hash-ready 후보

> ✅ **6개 audit fix 반영 완료 · 유닛 테스트 ALL PASS.**
> hash 봉인은 (1) pre-hash snapshot 생성 (2) 사용자 손 commit + tag 순서로.
> 이 폴더는 vsfec 브랜치에 additive-only 배치 (bot.py 등 기존 파일 무접촉).

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

## Audit fix 반영 상태 (전부 ✅)

### 1. open sizing map ↔ close MTM map 분리 ✅
`simulate()`에서 `open_px` / `close_px` 두 함수 분리:
- 사이징·room_gross: `book.equity(open_px)` — t+1 open 체결 시점 known 가격
- 일말 MTM: `book.equity(close_px)`
- look-ahead 1 bar 제거. `[E:sizing=open cap]` unit test PASS.

### 2. full calendar timeline ✅
`SW.full_calendar(start, end)` 함수 신규. `run_robustness.py`에서 `master_dates` 대신 사용.
START~END 전체 달력일 방문 → 결측일도 방문 → held-position stealth carry-forward 차단.
`[E:full_calendar]` unit test PASS.

### 3. dynamic point-in-time eligibility ✅
`build_universe()` 재작성: 각 coin에 `eligible_from = first_valid_candle + 180 days` 부여.
Signal generator에 `_is_eligible(cd, d)` 체크 추가 (`d < eligible_from` → skip).
`min_coverage` 창을 `[max(eligible_from, start), end]`로 재정의.
`[P:상장<180 dynamic 제외] [P:eligible_from set] [P:KRW-OK0 eligible_from]` PASS.

**§5 30d turnover:** 확정 = universe cutoff 아님. participation feasibility report field로만 사용.
run_robustness output JSON에 `participation_feasibility` 필드 포함 (진단값).

**§5 halt:** 확정 = candle 부재 = 신규 signal/entry eligibility 없음 (기존 InvalidRun 규칙으로 covered).

### 4. skew/kurt central-moment ✅
Pure Fisher-Pearson으로 재작성:
```
m_k = Σ(x−μ)^k / n
skew = m3 / m2^1.5
kurt = m4 / m2² (non-excess, 정규=3)
```
Bailey-LdP PSR denom과 일관. 통계 shape 검증됨.

### 5. snapshot pre-hash provenance ✅
`swing/fetch_snapshot.py` 신규 (pre-hash fetch CLI).
`swing/data/snapshot.json.gz` 생성 → 봉인 commit에 포함.
`run_robustness.py`는 snapshot만 읽음 (네트워크 fetch 코드 제거).
snapshot 없이 실행 시 명확한 에러.

### entry-pending gap → InvalidRun ✅
`simulate()`에 카테고리 ② 추가:
- pending exit + target open 결측 → `raise InvalidRun("pending-exit open gap")`
- pending entry + target open 결측 → `raise InvalidRun("pending-entry open gap")`
`[E:entry-gap→InvalidRun]` unit test PASS.

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
