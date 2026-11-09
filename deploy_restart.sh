#!/bin/bash
# deploy_restart.sh — bot.py + momentum_scanner 배포 + systemd 재시작
set -e

echo "[deploy] 시작 — $(date)"

cd /home/ubuntu/bot

# 변경 전 bot.py 해시 저장
OLD_BOT_HASH=$(md5sum bot.py 2>/dev/null | awk '{print $1}')

# 최신 코드 가져오기
echo "[deploy] git fetch + reset ..."
git fetch origin main
git reset --hard origin/main
echo "[deploy] 코드 업데이트 완료: $(git log --oneline -1)"

# bot.py가 실제로 변경됐을 때만 재시작
NEW_BOT_HASH=$(md5sum bot.py | awk '{print $1}')
if [ "$OLD_BOT_HASH" != "$NEW_BOT_HASH" ]; then
    echo "[deploy] bot.py 변경됨 → systemctl restart upbit-bot ..."
    sudo systemctl restart upbit-bot
else
    echo "[deploy] bot.py 변경 없음 → 메인봇 재시작 스킵"
fi

# 페이퍼 스캐너 (momentum-scanner / momentum-clm)
# 2026-10-30 CPU 부족(스틸 80~96%, 버스트 잔량 0)으로 정지·비활성화했다. 배포가 이를 되살리면 안 된다.
# 이전: 비활성이면 설치·enable 하고 무조건 restart → 10/30 결정을 조용히 되돌림.
# 정정: 이미 enable 된 경우에만 restart. 다시 켜는 것은 사람이 명시적으로 systemctl enable 할 때만.
for svc in momentum-scanner momentum-clm; do
    if systemctl is-enabled "$svc" &>/dev/null; then
        echo "[deploy] $svc enabled → restart ..."
        sudo systemctl restart "$svc"
    else
        echo "[deploy] $svc disabled (2026-10-30 CPU 결정) → 건드리지 않음"
    fi
done

echo "[deploy] 완료 — $(date)"
sudo systemctl status upbit-bot --no-pager
for svc in momentum-scanner momentum-clm; do
    systemctl is-enabled "$svc" &>/dev/null && sudo systemctl status "$svc" --no-pager
done
true

# ── Research: sweep_defense 안전 조건부 실행 (2026-07-12 조언자 스펙 강화) ──
# 이전 문제: bot.py route registry 변경 시 자동 sweep으로 CPU 포화 (병목 재발)
# 현재 스펙:
#   1) 커밋 메시지에 [sweep] 태그 있을 때만 실행 (opt-in, 기본은 skip)
#   2) AUTO_TRADE=1 상태에서는 실행 거부 (LIVE 중 리소스 경합 방지)
#   3) /tmp/sweep_defense.lock flock으로 중복 실행 방지
#   4) 이미 sweep_defense.py 프로세스 있으면 신규 시작 안 함
#   5) research/*.py 변경 자체는 트리거 조건이 아님 (조언자 명확화)
echo "[deploy] sweep_defense 조건 검사..."

LATEST_COMMIT_MSG=$(git log -1 --pretty=%B 2>/dev/null || echo "")
NEED_SWEEP=0
SKIP_REASON=""

if echo "$LATEST_COMMIT_MSG" | grep -qF "[sweep]"; then
    echo "[deploy] 커밋 메시지에 [sweep] 태그 감지 → sweep 후보"
    NEED_SWEEP=1
else
    SKIP_REASON="커밋 메시지에 [sweep] 태그 없음"
fi

# AUTO_TRADE=1 상태 확인 (실 매매 중이면 리소스 경합 방지)
CURRENT_AUTO_TRADE="0"
if [ -f /home/ubuntu/bot/.env ]; then
    CURRENT_AUTO_TRADE=$(grep -E "^AUTO_TRADE=" /home/ubuntu/bot/.env | tail -1 | cut -d= -f2 | tr -d '"' | tr -d ' ')
fi
if [ "$NEED_SWEEP" = "1" ] && [ "$CURRENT_AUTO_TRADE" = "1" ]; then
    NEED_SWEEP=0
    SKIP_REASON="AUTO_TRADE=1 (LIVE 실 매매 중 리소스 경합 방지)"
    echo "[deploy] $SKIP_REASON"
fi

# 이미 실행 중인 sweep_defense 프로세스 확인
if [ "$NEED_SWEEP" = "1" ] && pgrep -f "research/sweep_defense.py" >/dev/null 2>&1; then
    NEED_SWEEP=0
    SKIP_REASON="sweep_defense.py 프로세스 이미 실행 중 (중복 방지)"
    echo "[deploy] $SKIP_REASON"
fi

if [ "$NEED_SWEEP" = "1" ]; then
    echo "[deploy] sweep_defense 실행 조건 통과 → flock 시도"
    if ls /home/ubuntu/bot/research/data/*.parquet >/dev/null 2>&1; then
        SWEEP_ARGS="--skip-download --stage all"
    else
        SWEEP_ARGS="--top-markets 30 --days 90 --stage all"
    fi
    if [ -f /home/ubuntu/bot/.env ]; then
        set -a
        # shellcheck disable=SC1091
        source /home/ubuntu/bot/.env
        set +a
        echo "[deploy] .env 로드 완료 (TG_TOKEN=${TELEGRAM_TOKEN:0:15}...)"
    fi
    # flock으로 lock 획득 후 백그라운드 실행. 이미 lock 잡혀 있으면 즉시 skip.
    (
        flock -n 9 || {
            echo "[deploy] flock 획득 실패 → 다른 sweep 세션이 lock 보유 중, skip"
            exit 0
        }
        nohup python3 -u /home/ubuntu/bot/research/sweep_defense.py $SWEEP_ARGS \
            > /tmp/sweep_defense.log 2>&1 &
        echo "[deploy] sweep_defense PID=$! args='$SWEEP_ARGS' log=/tmp/sweep_defense.log"
        # 백그라운드로 넘긴 후 lock은 이 subshell 종료 시 해제됨.
        # sweep 프로세스 자체는 nohup으로 독립 실행.
    ) 9>/tmp/sweep_defense.lock
else
    echo "[deploy] sweep 스킵 (사유: $SKIP_REASON)"
fi
