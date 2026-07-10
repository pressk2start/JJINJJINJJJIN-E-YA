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

# 모멘텀 스캐너 서비스 (설치 안 됐으면 설치 후 시작)
if ! systemctl is-enabled momentum-scanner &>/dev/null; then
    echo "[deploy] momentum-scanner 서비스 최초 설치 ..."
    sudo cp /home/ubuntu/bot/bots/momentum-scanner.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable momentum-scanner
fi
echo "[deploy] systemctl restart momentum-scanner ..."
sudo systemctl restart momentum-scanner

# CLM Phase 1 서비스 (설치 안 됐으면 설치 후 시작)
if ! systemctl is-enabled momentum-clm &>/dev/null; then
    echo "[deploy] momentum-clm 서비스 최초 설치 ..."
    sudo cp /home/ubuntu/bot/bots/momentum-clm.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable momentum-clm
fi
echo "[deploy] systemctl restart momentum-clm ..."
sudo systemctl restart momentum-clm

echo "[deploy] 완료 — $(date)"
sudo systemctl status upbit-bot --no-pager
sudo systemctl status momentum-scanner --no-pager
sudo systemctl status momentum-clm --no-pager

# ── Research: sweep_defense 백그라운드 실행 (실패해도 배포엔 영향 없음) ──
echo "[deploy] sweep_defense 백그라운드 실행 시작..."
if ls /home/ubuntu/bot/research/data/*.parquet &>/dev/null; then
    SWEEP_ARGS="--skip-download --stage all"
else
    SWEEP_ARGS="--top-markets 30 --days 90 --stage all"
fi
# .env 로드 (TELEGRAM_TOKEN, TG_CHATS 등) — nohup 자식 프로세스에 상속시키기 위해
if [ -f /home/ubuntu/bot/.env ]; then
    set -a
    source /home/ubuntu/bot/.env
    set +a
    echo "[deploy] .env 로드 완료 (TG_TOKEN=${TELEGRAM_TOKEN:0:15}...)"
fi
# python3 -u: unbuffered stdout → 로그가 실시간으로 파일에 쓰임
nohup python3 -u /home/ubuntu/bot/research/sweep_defense.py $SWEEP_ARGS \
    > /tmp/sweep_defense.log 2>&1 &
echo "[deploy] sweep_defense PID=$! args='$SWEEP_ARGS' log=/tmp/sweep_defense.log"
