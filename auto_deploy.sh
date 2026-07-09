#!/bin/bash
# auto_deploy.sh — 1분마다 main 변경 확인 → 변경 시 자동 배포
# crontab: * * * * * /home/ubuntu/bot/auto_deploy.sh >> /tmp/auto_deploy.log 2>&1

cd /home/ubuntu/bot || exit 1

LOCAL=$(git rev-parse HEAD)
git fetch origin main -q 2>/dev/null || exit 0
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

echo "[auto-deploy] $(date) — 변경 감지: $LOCAL → $REMOTE"
bash ./deploy_restart.sh
echo "[auto-deploy] 완료 — $(date)"
