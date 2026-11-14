#!/bin/bash
# deploy_restart.sh — 서버 코드를 main 의 한 커밋으로 맞추고, 돌고 있던 봇을 새 코드로 다시 띄운다.
# 2026-11-14 전체 검토 반영:
#   · DEPLOY_SHA 를 주면 그 커밋이 지금의 main 일 때만 배포한다(check 뒤에 main 이 바뀌었으면 멈춘다).
#   · 꺼져 있는 봇은 켜지 않는다(사람이 일부러 꺼 둔 봇을 배포가 되살리지 않게). 켜는 것은 사람이 직접 한다.
#   · 봇이 불러 쓰는 config.py · _a_clean_purity.py 가 바뀌어도 재시작한다.
#   · 재시작한 봇이 새 코드로 떴는지 확인되지 않으면 끝에서 실패(종료 코드 1)로 끝난다.
set -e
DEPLOY_FAILED=0

echo "[deploy] 시작 — $(date)"

cd /home/ubuntu/bot

# 변경 전 bot.py 해시 저장 (봇이 불러 쓰는 파일들의 묶음 해시도 같이)
bot_files_hash() { cat bot.py config.py _a_clean_purity.py 2>/dev/null | md5sum | awk '{print $1}'; }
OLD_BOT_HASH=$(md5sum bot.py 2>/dev/null | awk '{print $1}')
OLD_FILES_HASH=$(bot_files_hash)
# 이 스크립트 자신의 해시도 저장 (아래 재실행 판단용)
SELF_PATH="/home/ubuntu/bot/deploy_restart.sh"
OLD_SELF_HASH=$(md5sum "$SELF_PATH" 2>/dev/null | awk '{print $1}')

# 코드 가져오기. 재실행된 경우(DEPLOY_TARGET 있음)에는 첫 실행이 정한 커밋을 그대로 쓴다.
if [ -z "$DEPLOY_TARGET" ]; then
    echo "[deploy] git fetch ..."
    git fetch origin main
    DEPLOY_TARGET=$(git rev-parse origin/main)
    if [ -n "$DEPLOY_SHA" ]; then                       # check 에서 본 커밋과 지금의 main 이 같아야 한다
        case "$DEPLOY_TARGET" in
            "$DEPLOY_SHA"*) ;;
            *) echo "[deploy] ⛔ main 이 바뀌었다: 승인한 커밋 $DEPLOY_SHA, 지금 main $DEPLOY_TARGET — 배포하지 않는다. check 부터 다시 할 것"
               exit 3 ;;
        esac
    fi
    export DEPLOY_TARGET
fi
git reset --hard "$DEPLOY_TARGET"
echo "[deploy] 코드 업데이트 완료: $(git log --oneline -1)"

# 2026-11-09 사고 재발 방지: reset 이 이 스크립트 자체를 바꿨으면 새 버전으로 다시 실행한다.
# bash 는 이미 연 옛 파일을 끝까지 실행하므로, 실행 도중 main 이 바뀌면 "새 코드 위에서 옛 배포 로직" 이 돈다.
# 그날 옛 로직이 새 bot.py 를 올리면서 10/30 에 끈 페이퍼 스캐너를 다시 켰다. DEPLOY_REEXEC 는 무한 재실행 방지.
NEW_SELF_HASH=$(md5sum "$SELF_PATH" | awk '{print $1}')
if [ "$OLD_SELF_HASH" != "$NEW_SELF_HASH" ] && [ -z "$DEPLOY_REEXEC" ]; then
    echo "[deploy] deploy_restart.sh 자체가 갱신됨 → 새 버전으로 재실행"
    DEPLOY_REEXEC=1 exec bash "$SELF_PATH" "$@"
fi

# 봇 재시작 판단 (2026-11-10 검토 반영).
# 이전: 이 스크립트가 잰 '변경 전/후 bot.py 해시' 만 비교 → 배포 명령이 git reset 을 먼저 하면 둘이 같아져
#       새 코드는 디스크에만 있고 옛 프로세스가 계속 돌았다(H3 가드 없이).
# 정정: '실행 중인 프로세스가 지금 디스크의 bot.py 에서 시작됐는가' 로 판단한다. bot.py 가 기동 때 남기는 마커
#       (pid + 자기 소스 md5)를 trend/bot_version_check.py 로 대조. 재시작 뒤에는 같은 검사로 새 프로세스를 확인한다.
NEW_BOT_HASH=$(md5sum bot.py | awk '{print $1}')
NEW_FILES_HASH=$(bot_files_hash)
BOT_MARKER=/home/ubuntu/bot/data/h3_reserved.json
bot_pid() { systemctl show -p MainPID --value upbit-bot 2>/dev/null || echo 0; }
BOT_PID_NOW=$(bot_pid)
NEED_RESTART=0
RESTART_REASON=""
if [ -z "$BOT_PID_NOW" ] || [ "$BOT_PID_NOW" = "0" ]; then
    # 꺼져 있는 봇은 켜지 않는다. (전에는 '실행 중이 아님' 을 재시작 사유로 봐서, 일부러 꺼 둔 봇도 배포가 켰다.)
    echo "[deploy] upbit-bot 이 실행 중이 아니다 → 건드리지 않는다. 켜려면 사람이 직접: sudo systemctl start upbit-bot"
elif [ "$OLD_BOT_HASH" != "$NEW_BOT_HASH" ]; then
    NEED_RESTART=1
    RESTART_REASON="bot.py changed during this run"
elif [ "$OLD_FILES_HASH" != "$NEW_FILES_HASH" ]; then
    NEED_RESTART=1
    RESTART_REASON="config.py or _a_clean_purity.py changed during this run"
elif ! RESTART_REASON=$(python3 trend/bot_version_check.py --marker "$BOT_MARKER" --bot-file bot.py --pid "$BOT_PID_NOW" 2>&1); then
    NEED_RESTART=1
fi
if [ "$NEED_RESTART" = "1" ]; then
    echo "[deploy] 봇 재시작: $RESTART_REASON"
    sudo systemctl restart upbit-bot
    BOT_VERIFIED=0
    for _i in $(seq 1 40); do                      # 느린 서버에서 import 가 오래 걸린다 → 최대 2분 대기
        sleep 3
        if VERIFY_MSG=$(python3 trend/bot_version_check.py --marker "$BOT_MARKER" --bot-file bot.py --pid "$(bot_pid)" 2>&1); then
            BOT_VERIFIED=1
            break
        fi
    done
    if [ "$BOT_VERIFIED" = "1" ]; then
        echo "[deploy] 검증 OK: $VERIFY_MSG"
    else
        echo "[deploy] ⚠⚠ 검증 실패: 봇이 새 코드로 떴는지 확인되지 않음 — $VERIFY_MSG"
        echo "[deploy] ⚠⚠ H3 실행기는 이 상태에서 매수를 거부한다. journalctl -u upbit-bot -n 30 으로 확인할 것."
        DEPLOY_FAILED=1                                 # 나머지 단계는 마저 하되, 끝에서 실패로 끝낸다
    fi
elif [ -n "$BOT_PID_NOW" ] && [ "$BOT_PID_NOW" != "0" ]; then
    echo "[deploy] 재시작 불필요: $RESTART_REASON"
fi

# 페이퍼 스캐너 (momentum-scanner / momentum-clm)
# 2026-10-30 CPU 부족(스틸 80~96%, 버스트 잔량 0)으로 정지·비활성화했다. 배포가 이를 되살리면 안 된다.
# 이전: 비활성이면 설치·enable 하고 무조건 restart → 10/30 결정을 조용히 되돌림.
# 정정: 이미 enable 된 경우에만 restart. 다시 켜는 것은 사람이 명시적으로 systemctl enable 할 때만.
for svc in momentum-scanner momentum-clm; do
    if systemctl is-enabled "$svc" &>/dev/null; then
        echo "[deploy] $svc enabled → restart ..."
        sudo systemctl restart "$svc"
    else
        echo "[deploy] $svc disabled (2026-10-30 CPU 결정) → 건드리지 않음"
    fi
done

echo "[deploy] 완료 — $(date)"
# 상태만 보여 준다. -n 0 = 서비스의 최근 로그 줄은 찍지 않는다 — 이 출력은 공개 저장소의 배포 기록에 남는데,
# 봇의 로그 줄에는 통신 오류 문구처럼 비밀값이 섞인 줄이 있을 수 있다 (2026-11-14 검토). 로그는 서버에서 journalctl 로 본다.
sudo systemctl status upbit-bot --no-pager -n 0 || true      # 꺼져 있으면 0 이 아닌 값을 돌려준다 — 그것만으로 배포를 실패시키지 않는다
for svc in momentum-scanner momentum-clm; do
    systemctl is-enabled "$svc" &>/dev/null && sudo systemctl status "$svc" --no-pager -n 0
done
true

# ── Research: sweep_defense 안전 조건부 실행 (2026-07-12 조언자 스펙 강화) ──
# 이전 문제: bot.py route registry 변경 시 자동 sweep으로 CPU 포화 (병목 재발)
# 현재 스펙:
#   1) 커밋 메시지에 [sweep] 태그 있을 때만 실행 (opt-in, 기본은 skip)
#   2) AUTO_TRADE=1 상태에서는 실행 거부 (LIVE 중 리소스 경합 방지)
#   3) /tmp/sweep_defense.lock flock으로 중복 실행 방지
#   4) 이미 sweep_defense.py 프로세스 있으면 신규 시작 안 함
#   5) research/*.py 변경 자체는 트리거 조건이 아님 (조언자 명확화)
echo "[deploy] sweep_defense 조건 검사..."

LATEST_COMMIT_MSG=$(git log -1 --pretty=%B 2>/dev/null || echo "")
NEED_SWEEP=0
SKIP_REASON=""

if echo "$LATEST_COMMIT_MSG" | grep -qF "[sweep]"; then
    echo "[deploy] 커밋 메시지에 [sweep] 태그 감지 → sweep 후보"
    NEED_SWEEP=1
else
    SKIP_REASON="커밋 메시지에 [sweep] 태그 없음"
fi

# AUTO_TRADE=1 상태 확인 (실 매매 중이면 리소스 경합 방지)
CURRENT_AUTO_TRADE="0"
if [ -f /home/ubuntu/bot/.env ]; then
    CURRENT_AUTO_TRADE=$(grep -E "^AUTO_TRADE=" /home/ubuntu/bot/.env | tail -1 | cut -d= -f2 | tr -d '"' | tr -d ' ')
fi
if [ "$NEED_SWEEP" = "1" ] && [ "$CURRENT_AUTO_TRADE" = "1" ]; then
    NEED_SWEEP=0
    SKIP_REASON="AUTO_TRADE=1 (LIVE 실 매매 중 리소스 경합 방지)"
    echo "[deploy] $SKIP_REASON"
fi

# 이미 실행 중인 sweep_defense 프로세스 확인
if [ "$NEED_SWEEP" = "1" ] && pgrep -f "research/sweep_defense.py" >/dev/null 2>&1; then
    NEED_SWEEP=0
    SKIP_REASON="sweep_defense.py 프로세스 이미 실행 중 (중복 방지)"
    echo "[deploy] $SKIP_REASON"
fi

if [ "$NEED_SWEEP" = "1" ]; then
    echo "[deploy] sweep_defense 실행 조건 통과 → flock 시도"
    if ls /home/ubuntu/bot/research/data/*.parquet >/dev/null 2>&1; then
        SWEEP_ARGS="--skip-download --stage all"
    else
        SWEEP_ARGS="--top-markets 30 --days 90 --stage all"
    fi
    if [ -f /home/ubuntu/bot/.env ]; then
        set -a
        # shellcheck disable=SC1091
        source /home/ubuntu/bot/.env
        set +a
        # 배포 로그는 GitHub Actions 에 남는다(공개 저장소면 누구나 열람) → 토큰은 앞부분도 찍지 않는다.
        if [ -n "$TELEGRAM_TOKEN" ]; then TG_STATE="설정됨"; else TG_STATE="없음"; fi
        echo "[deploy] .env 로드 완료 (TELEGRAM_TOKEN: $TG_STATE)"
    fi
    # flock으로 lock 획득 후 백그라운드 실행. 이미 lock 잡혀 있으면 즉시 skip.
    (
        flock -n 9 || {
            echo "[deploy] flock 획득 실패 → 다른 sweep 세션이 lock 보유 중, skip"
            exit 0
        }
        nohup python3 -u /home/ubuntu/bot/research/sweep_defense.py $SWEEP_ARGS \
            > /tmp/sweep_defense.log 2>&1 &
        echo "[deploy] sweep_defense PID=$! args='$SWEEP_ARGS' log=/tmp/sweep_defense.log"
        # 백그라운드로 넘긴 후 lock은 이 subshell 종료 시 해제됨.
        # sweep 프로세스 자체는 nohup으로 독립 실행.
    ) 9>/tmp/sweep_defense.lock
else
    echo "[deploy] sweep 스킵 (사유: $SKIP_REASON)"
fi

if [ "$DEPLOY_FAILED" = "1" ]; then
    echo "[deploy] ⛔ 봇이 새 코드로 떴는지 확인되지 않았다 — 이 배포는 실패로 끝난다"
    exit 1
fi
