#!/bin/bash
# deploy_restart.sh — bot.py 배포 + systemd 재시작
set -e

cd /home/ubuntu/bot

# 최신 코드 가져오기
git fetch origin main
git reset --hard origin/main

# systemd로 봇 재시작
sudo systemctl restart upbit-bot

echo "[deploy] 완료 — $(date)"
sudo systemctl status upbit-bot --no-pager
