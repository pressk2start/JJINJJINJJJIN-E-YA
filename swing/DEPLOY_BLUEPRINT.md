# Swing 배포 blueprint (설계 문서만, 미활성화)

> ⚠ 이 문서는 v1.7 protocol 잠금 상태에서 **인프라 설계**만 기술함.
> 실제 서버 활성화(swing.service 설치·enable·start, live 실주문)는
> **hash → historical robustness 1회 PASS → prospective shadow → prospective PASS**
> 순차 통과 후 사용자 손 별도 결정 사항.
>
> ⚠ 서버 봇 5개 안전확인 (`bot.py enabled=False`, `AUTO_TRADE=0`, kill switch, `systemctl mask momentum-*`)
> 은 인프라 만지기 전 필수 선행 (사용자 손).

## 격리 원칙

| 자원 | live bot (bot.py) | swing bot | 격리 방식 |
|---|---|---|---|
| Repo | JJINJJINJJJIN-E-YA (main) | 별도 새 repo 권장 | 물리적 분리 |
| 디렉터리 | `/home/ubuntu/bot/` | `/home/ubuntu/swing/` | 다른 경로 |
| systemd 유닛 | `upbit-bot.service` / `momentum-scanner.service` / `momentum-clm.service` | `swing.service` / `swing.timer` | 별도 unit |
| GitHub Actions | `deploy.yml` (main push → SSH) | `swing-ci.yml` (모든 branch → 오프라인 유닛 테스트만) | 배포 workflow 분리 |
| 배포 스크립트 | `deploy_restart.sh` (bot.py + momentum-*) | `swing_deploy.sh` (swing.service만) | 실행 경로 분리 |
| `.env` | `/home/ubuntu/bot/.env` (Upbit key A, AUTO_TRADE) | `/home/ubuntu/swing/.env` (별도 Upbit key 권장, TELEGRAM_TOKEN) | 파일 분리 |

## 실주문 4중 안전 게이트 (live_swing.py 내장)

`SafetyBlocked` 예외로 하드락 — 4개 모두 통과해야 실주문 함수 진입:

1. **환경변수 `AUTO_TRADE=1`** — 기본값 0 (kill switch)
2. **`.sealed_spec_marker` 파일 존재** — hash 봉인 anchor
3. **`.prospective_pass_marker` 파일 존재** — 검증 통과 anchor
4. **`--arm-live` CLI 플래그** — 매 실행 명시적 동의

하나라도 실패 = paper 모드 강제 + telegram `SAFETY BLOCK` 알림.

## 텔레그램 알림

`telegram_notify.py`가 표준 알림 인터페이스 제공:

- `notify_startup(mode, extra)` — 시작 시 mode(PAPER/LIVE) + 게이트 상태
- `notify_paper_trade(action, market, price, size, reason)` — paper 시뮬 트레이드
- `notify_live_trade(action, market, price, size, order_id)` — 실주문 (게이트 통과 시만)
- `notify_error(where, err)` — 예외
- `notify_safety_block(reason)` — 게이트 차단 이벤트

환경변수:
- `TELEGRAM_TOKEN` — botfather 발급
- `TELEGRAM_CHAT_ID` — 사용자·그룹 ID

둘 다 없으면 stdout fallback (봇 실행 계속).

## 배포 순서 (모두 사용자 손, 각 단계 별도 결정)

### Phase A — pre-hash (지금 상태)
- 코드/문서/CI만 반영. 서버 무접촉.
- vsfec 브랜치에 파일 배치 완료.
- swing-ci.yml이 push마다 오프라인 유닛 테스트 실행 (서버 무접촉).

### Phase B — hash 봉인 (사용자 손)
- 서버 봇 5개 안전확인 + `systemctl mask momentum-*` 완료
- 6개 audit fix 반영 확인 (완료됨, README 참조) + `tests.py` ALL PASS
- 연구 artifact 7개만 별도 새 repo에 배치 (live/telegram/deploy 제외 = 감사범위 축소)
- 봉인 commit + `git tag -a v1.7-sealed`

### Phase C — historical robustness 1회 (사용자 손)
- tag된 커밋에서 `python3 swing/run_robustness.py` 단 1회
- PASS/FAIL 판정

### Phase D — prospective shadow (사용자 손, robustness PASS 시)
- swing.service · swing.timer 서버 설치
- 서버 `/home/ubuntu/swing/` 디렉터리 준비
- `swing_deploy.sh` 서버에서 실행 (bot.py 무접촉)
- paper 모드로 12/24/36 look α-spending 진행 (PSR ≥ 0.9833)

### Phase E — live arm (사용자 손, prospective PASS 시)
- `.sealed_spec_marker` + `.prospective_pass_marker` 생성
- systemd override: `AUTO_TRADE=1` + `ExecStart --arm-live`
- 텔레그램으로 라이브 트레이드 알림 수신 확인
- 실주문 시작

## 절대 하지 말 것

- push to main → 기존 deploy.yml 발동 → momentum-scanner/clm restart
- swing.service를 `upbit-bot` 사용자·경로에 겹치게 설치
- `.env` 파일에 실제 Upbit key를 git 커밋 (`.gitignore` 확인)
- `--arm-live` CLI를 systemd 기본에 포함
- audit blocker 해결 전 hash tag
