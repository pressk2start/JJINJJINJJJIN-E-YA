#!/bin/bash
set -e
cd /home/ubuntu/bots

# 1. 기존 프로세스 확실히 종료
pkill -f "python.*bot.py" || true
pkill -f "python.*collect_1m.py" || true
sleep 2

# 2. 혹시 남았으면 강제 종료
pkill -9 -f "python.*bot.py" || true
pkill -9 -f "python.*collect_1m.py" || true

# 3. 락 파일 정리
rm -f .study_lock .collect_1m_lock .study_done

# 4. 코드 업데이트
git pull origin main

# 5. 새로 시작 (하나만)
nohup python3 bot.py > bot.log 2>&1 &
echo "Started bot.py (PID: $!)"
