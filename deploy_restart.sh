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

echo "[deploy] 완료 — $(date)"
sudo systemctl status upbit-bot --no-pager
sudo systemctl status momentum-scanner --no-pager
