#!/bin/bash
# deploy_restart.sh — bot.py 배포 + 안전 재시작
# 이미 실행 중이면 완료될 때까지 기다림 (kill 안 함)
set -e

cd /home/ubuntu/bots

# git pull
git fetch origin main
git reset --hard origin/main

# bot.py가 이미 실행 중인지 확인
RUNNING_FILE=".study_running"
DONE_FILE=".study_done"
BOT_PID=""

if [ -f "$RUNNING_FILE" ]; then
    BOT_PID=$(head -1 "$RUNNING_FILE" 2>/dev/null || true)
    if [ -n "$BOT_PID" ] && kill -0 "$BOT_PID" 2>/dev/null; then
        echo "[deploy] bot.py PID $BOT_PID 실행 중 — kill 하지 않고 완료 대기 (최대 10분)"
        # 최대 10분 대기
        for i in $(seq 1 60); do
            if ! kill -0 "$BOT_PID" 2>/dev/null; then
                echo "[deploy] PID $BOT_PID 종료됨. 새 코드로 시작."
                break
            fi
            sleep 10
        done
        # 10분 후에도 살아있으면 SIGTERM (SIGKILL 아님)
        if kill -0 "$BOT_PID" 2>/dev/null; then
            echo "[deploy] 10분 초과. SIGTERM 전송."
            kill "$BOT_PID" 2>/dev/null || true
            sleep 5
        fi
    else
        echo "[deploy] 이전 프로세스 PID $BOT_PID 이미 종료됨."
        rm -f "$RUNNING_FILE"
    fi
fi

# 최근 완료 확인 — 완료 직후면 바로 시작할 필요 없음
if [ -f "$DONE_FILE" ]; then
    DONE_TS=$(head -1 "$DONE_FILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    AGE=$((NOW - ${DONE_TS%.*}))
    if [ "$AGE" -lt 3600 ]; then
        echo "[deploy] 최근 완료 (${AGE}초 전). 코드 업데이트만 완료, 즉시 재실행 불필요."
        exit 0
    fi
fi

# pip install (필요시)
pip install -q requests 2>/dev/null || true

# bot.py 시작 (nohup + 백그라운드)
echo "[deploy] bot.py 시작"
nohup python3 bot.py > /tmp/bot_stdout.log 2>&1 &
echo "[deploy] PID: $!"
