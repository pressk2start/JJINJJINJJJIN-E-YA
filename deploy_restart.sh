#!/bin/bash
# deploy_restart.sh — bot.py 배포 + systemd 재시작
set -e

echo "[deploy] 시작 — $(date)"

cd /home/ubuntu/bot

# 최신 코드 가져오기
echo "[deploy] git fetch + reset ..."
git fetch origin main
git reset --hard origin/main
echo "[deploy] 코드 업데이트 완료: $(git log --oneline -1)"

# systemd로 봇 재시작
echo "[deploy] systemctl restart upbit-bot ..."
sudo systemctl restart upbit-bot

echo "[deploy] 완료 — $(date)"
sudo systemctl status upbit-bot --no-pager
