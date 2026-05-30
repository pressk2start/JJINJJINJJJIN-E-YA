#!/bin/bash
# deploy_restart.sh — bot.py + momentum_scanner 배포 + systemd 재시작
set -e

echo "[deploy] 시작 — $(date)"

cd /home/ubuntu/bot

# 최신 코드 가져오기
echo "[deploy] git fetch + reset ..."
git fetch origin main
git reset --hard origin/main
echo "[deploy] 코드 업데이트 완료: $(git log --oneline -1)"

# systemd로 메인봇 재시작
echo "[deploy] systemctl restart upbit-bot ..."
sudo systemctl restart upbit-bot

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
