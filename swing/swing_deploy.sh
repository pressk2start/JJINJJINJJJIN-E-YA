#!/bin/bash
# swing_deploy.sh — swing 봇 전용 배포 스크립트 (템플릿, 서버 미배치).
# ⚠ 기존 bot.py / momentum-scanner / momentum-clm 서비스 절대 안 만짐.
# ⚠ 이 스크립트는 hash + robustness PASS + prospective 설정 완료 이후에만 서버에서 사용.
#
# 사용 (사용자 손, hash 후):
#   ssh ubuntu@<server> "cd /home/ubuntu/swing && bash swing_deploy.sh"
#
# 선행조건 (서버에서 미리 완료):
#   1. sudo systemctl mask momentum-scanner momentum-clm     # 부활 완전 차단
#   2. mkdir -p /home/ubuntu/swing                            # 격리 디렉터리
#   3. git clone <swing-only-repo> /home/ubuntu/swing         # bot.py repo와 물리 분리
#   4. cp swing/swing.service /etc/systemd/system/
#   5. cp swing/swing.timer /etc/systemd/system/
#   6. sudo systemctl daemon-reload && sudo systemctl enable swing.timer

set -e

echo "[swing-deploy] 시작 $(date)"

cd /home/ubuntu/swing || { echo "[swing-deploy] /home/ubuntu/swing 없음"; exit 1; }

# --- 안전 재확인 ---
if [ -f /home/ubuntu/bot/bot.py ]; then
    echo "[swing-deploy] ⚠ /home/ubuntu/bot/bot.py 존재 (live bot 서버) — 이 스크립트는 그것과 완전 분리 경로. 계속."
fi

# --- 코드 업데이트 ---
echo "[swing-deploy] git pull..."
git pull --ff-only

# --- 유닛 테스트 (실행 시 fail이면 배포 중단) ---
echo "[swing-deploy] python3 swing/tests.py..."
python3 swing/tests.py || {
    echo "[swing-deploy] ❌ 테스트 실패 → 서비스 restart 안 함"; exit 1; }

# --- 서비스 재시작 (swing.service만) ---
if systemctl is-enabled swing.timer &>/dev/null; then
    echo "[swing-deploy] swing.timer restart..."
    sudo systemctl restart swing.timer
    sudo systemctl status swing.timer --no-pager | head -10
else
    echo "[swing-deploy] swing.timer 미활성 — 사전 enable 필요 (설명은 상단 주석 참조)"
fi

# --- 다른 서비스 무접촉 확인 ---
echo "[swing-deploy] 다른 서비스 상태 (touch 안 함, 정보만):"
systemctl is-active upbit-bot 2>&1 || true
systemctl is-active momentum-scanner 2>&1 || true
systemctl is-active momentum-clm 2>&1 || true

echo "[swing-deploy] 완료 $(date)"
