# Swing Research Bot (Protocol v1.7)

Upbit 스윙 전략 연구·실행 인프라. 상세는 `swing/README.md`.

## 구조

```
swing/                 ← 스윙 전략 코드·문서·live skeleton (Protocol v1.7)
.github/workflows/
  ├── deploy.yml       ← main push → SSH → deploy_restart.sh
  └── swing-ci.yml     ← swing/tests.py 오프라인 러너 (모든 branch)
deploy_restart.sh      ← 서버 배포 스크립트 (swing 전용)
auto_deploy.sh         ← 서버 crontab 매분 poller (redundant, 안전)
```

## Deploy 흐름

Push to main → GitHub Actions → SSH → `deploy_restart.sh`:
1. `git fetch + reset --hard origin/main`
2. `python3 swing/tests.py` (FAIL 시 배포 중단)
3. `swing.service` + `swing.timer` 설치 (최초 1회)
4. `swing.timer` restart (UTC 00:05 daily trigger)
5. `swing.service` 즉시 1회 실행 (paper 모드 확인)

## Live 실주문 4중 하드락 (`swing/live_swing.py`)

`SafetyBlocked` 예외로 하드락:
1. `AUTO_TRADE=1` 환경변수
2. `.sealed_spec_marker` 파일 존재
3. `.prospective_pass_marker` 파일 존재
4. `--arm-live` CLI 플래그

하나라도 실패 = paper 모드 강제 + telegram `SAFETY BLOCK` 알림.

## ⚠ 서버 정리 (사용자 손, main 병합 전 필수)

기존 bot.py / momentum-scanner / momentum-clm 서비스 완전 종료:

```bash
# 1. Upbit 오픈 포지션 수동 청산 · 미체결 취소 (계정에서 직접)

# 2. 서비스 중단·비활성·마스크
sudo systemctl stop upbit-bot momentum-scanner momentum-clm
sudo systemctl disable upbit-bot momentum-scanner momentum-clm
sudo systemctl mask upbit-bot momentum-scanner momentum-clm

# 3. .env AUTO_TRADE=0 확인 (레거시 참조라도 안전 마진)
grep AUTO_TRADE /home/ubuntu/bot/.env
```

이 순서 안 지키면 main 병합 → auto-deploy → `git reset --hard` → bot.py 삭제 → systemd 재시작 실패 → 오픈 포지션 orphan 위험.

## 텔레그램 알림

`swing/telegram_notify.py` — 환경변수 설정:
```bash
# /home/ubuntu/bot/.env
TELEGRAM_TOKEN=<botfather 발급>
TELEGRAM_CHAT_ID=<사용자·그룹 ID>
```

시작·paper 트레이드·live 트레이드·에러·safety block 이벤트 알림.

## Swing 코드 상태

**HASH READY = NO.** `swing/README.md`의 audit blocker 5개 + entry-gap test 미해결.
현재 배포되어도 paper 모드로만 실행 (실주문 4중 게이트에 모두 걸림).

## Live arm 순서 (한참 후)

1. audit blocker 5개 + entry-gap test 해결
2. 새 repo에서 hash tag (또는 이 repo에서 별도 tag)
3. Historical robustness 1회 (`python3 swing/run_robustness.py`)
4. PASS → prospective shadow 12/24/36 look (α-spending PSR ≥ 0.9833)
5. Prospective PASS → `.sealed_spec_marker` + `.prospective_pass_marker` 생성 + systemd override로 `--arm-live` 추가 + `AUTO_TRADE=1`
6. 텔레그램으로 라이브 트레이드 알림 수신 확인
