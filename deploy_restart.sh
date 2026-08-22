#!/bin/bash
# deploy_restart.sh — swing bot 배포 (Protocol v1.7).
# 기존 bot.py / momentum-scanner / momentum-clm 서비스는 폐기.
# ⚠ 서버측 선행 (사용자 손, deploy 전 필수):
#     sudo systemctl stop upbit-bot momentum-scanner momentum-clm
#     sudo systemctl disable upbit-bot momentum-scanner momentum-clm
#     sudo systemctl mask upbit-bot momentum-scanner momentum-clm
#     # Upbit 오픈 포지션 수동 청산 · 미체결 취소
set -e

echo "[deploy] 시작 — $(date)"

cd /home/ubuntu/bot

# 최신 코드 가져오기
echo "[deploy] git fetch + reset ..."
git fetch origin main
git reset --hard origin/main
echo "[deploy] 코드 업데이트 완료: $(git log --oneline -1)"

# --- Swing bot (Protocol v1.7) ---
if [ -d /home/ubuntu/bot/swing ]; then
    echo "[deploy] swing 유닛 테스트 (오프라인, result-independent)..."
    if ! python3 /home/ubuntu/bot/swing/tests.py; then
        echo "[deploy] ❌ swing 테스트 FAIL → 배포 중단"
        exit 1
    fi

    if ! systemctl is-enabled swing.timer &>/dev/null; then
        echo "[deploy] swing 최초 설치 ..."
        sudo cp /home/ubuntu/bot/swing/swing.service /etc/systemd/system/
        sudo cp /home/ubuntu/bot/swing/swing.timer /etc/systemd/system/
        sudo systemctl daemon-reload
        sudo systemctl enable swing.timer
    fi

    echo "[deploy] swing.timer restart ..."
    sudo systemctl restart swing.timer
    sudo systemctl status swing.timer --no-pager | head -8

    # 즉시 1회 실행 (paper 모드, live_swing.py 내부 4중 게이트로 실주문 하드락)
    echo "[deploy] swing.service 즉시 1회 실행 (paper 확인) ..."
    sudo systemctl start swing.service || echo "[deploy] swing.service start 실패 (계속)"
else
    echo "[deploy] swing/ 없음 → 배포할 게 없음"
fi

echo "[deploy] 완료 — $(date)"
