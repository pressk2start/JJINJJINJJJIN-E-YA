#!/bin/bash
# deploy_restart.sh — 서버 코드를 main 의 한 커밋으로 맞추고, 돌고 있던 봇을 새 코드로 다시 띄운다.
# 2026-11-14 전체 검토 반영:
#   · DEPLOY_SHA(check 에서 본, 승인한 커밋 번호)가 지금의 main 일 때만 배포한다(check 뒤에 main 이 바뀌었으면 멈춘다).
#   · 꺼져 있는 봇은 켜지 않는다(사람이 일부러 꺼 둔 봇을 배포가 되살리지 않게). 켜는 것은 사람이 직접 한다.
#   · 봇이 불러 쓰는 config.py · _a_clean_purity.py 가 바뀌어도 재시작한다.
#   · 재시작한 봇이 새 코드로 떴는지 확인되지 않으면 끝에서 실패(종료 코드 1)로 끝난다.
# 2026-11-16 제3세션 검토·고문 판정 반영:
#   · 승인한 커밋 번호(DEPLOY_SHA) 없이는 코드를 바꾸지 않는다. 어떤 경로에서도 그 번호와 대조하기 전에 git reset 을 하지 않는다.
#   · 배포 워크플로는 서버에 있던 옛 스크립트가 아니라 '승인한 커밋의 이 스크립트' 를 임시 파일로 꺼내 DEPLOY_TARGET 과 함께 돌린다.
#     그래서 첫 배포부터 이 규칙이 적용된다(옛 스크립트가 먼저 그 순간의 main 을 받아 버리는 일이 없다).
#   · 올릴 커밋(DEPLOY_TARGET)이 어디서 왔든 승인 번호와 대조하고, 지금의 main 이 그 커밋이 아니면(그 사이 main 이 바뀌었으면)
#     아무것도 바꾸지 않고 종료 코드 3 으로 끝난다.
#   · 배포가 연구 스크립트(sweep_defense)를 띄우던 블록을 지웠다. 이 스크립트는 .env 를 읽지 않는다.
#   · 봇 파일의 '변경 전' 해시는 최초 실행에서 잰 값을 끝까지 쓴다. 이 스크립트가 자기 자신을 새 버전으로 다시 실행할 때 그 값을
#     넘겨준다(넘기지 않으면 다시 실행된 쪽이 바뀐 뒤의 파일로 재서, 스크립트와 config.py 가 함께 바뀐 배포에서 재시작이 빠졌다).
set -e
DEPLOY_FAILED=0
RUN_HASH=$(md5sum "$0" 2>/dev/null | awk '{print $1}')   # 지금 실제로 돌고 있는 스크립트의 해시 (cd 전에 잰다. 못 재면 빈 값)

echo "[deploy] 시작 — $(date)"

cd /home/ubuntu/bot

bot_files_hash() { cat bot.py config.py _a_clean_purity.py 2>/dev/null | md5sum | awk '{print $1}'; }
SELF_PATH="/home/ubuntu/bot/deploy_restart.sh"
OLD_SELF_HASH=$(md5sum "$SELF_PATH" 2>/dev/null | awk '{print $1}')

if [ "$DEPLOY_STAGE" = "reexec" ]; then
    # 이 스크립트가 자기 자신을 새 버전으로 다시 실행한 경우: 코드는 이미 승인한 커밋으로 바뀌어 있다.
    # 최초 실행이 정한 커밋과, 코드를 바꾸기 '전' 에 잰 해시를 그대로 쓴다 (지금 재면 바뀐 뒤의 값이다).
    OLD_BOT_HASH="$DEPLOY_OLD_BOT_HASH"
    OLD_FILES_HASH="$DEPLOY_OLD_FILES_HASH"
else
    case "$DEPLOY_SHA" in
        ""|*[!0-9a-f]*) echo "[deploy] ⛔ 승인한 커밋 번호(DEPLOY_SHA)가 없다 — 아무것도 바꾸지 않는다. 배포는 Actions 의 Deploy to Lightsail 로 한다"
                        exit 2 ;;
    esac
    if [ "${#DEPLOY_SHA}" -lt 7 ]; then echo "[deploy] ⛔ DEPLOY_SHA 는 7자 이상이어야 한다"; exit 2; fi
    # 변경 전 bot.py 해시 저장 (봇이 불러 쓰는 파일들의 묶음 해시도 같이)
    OLD_BOT_HASH=$(md5sum bot.py 2>/dev/null | awk '{print $1}')
    OLD_FILES_HASH=$(bot_files_hash)
    echo "[deploy] git fetch ..."
    git fetch origin main
    MAIN_NOW=$(git rev-parse origin/main)
    TARGET_FROM="배포 워크플로가 정한 커밋"
    if [ -z "$DEPLOY_TARGET" ]; then DEPLOY_TARGET="$MAIN_NOW"; TARGET_FROM="지금 main"; fi
    case "$DEPLOY_TARGET" in                              # 올릴 커밋은 승인한 커밋이어야 한다 (대상이 어디서 왔든 대조한다)
        "$DEPLOY_SHA"*) ;;
        *) echo "[deploy] ⛔ main 이 바뀌었다: 승인한 커밋 $DEPLOY_SHA, $TARGET_FROM $DEPLOY_TARGET — 아무것도 바꾸지 않는다. check 부터 다시 할 것"
           exit 3 ;;
    esac
    if [ "$MAIN_NOW" != "$DEPLOY_TARGET" ]; then          # 승인한 뒤 main 이 바뀌었다 → 올리지 않는다
        echo "[deploy] ⛔ main 이 바뀌었다: 승인한 커밋 $DEPLOY_TARGET, 지금 main $MAIN_NOW — 아무것도 바꾸지 않는다. check 부터 다시 할 것"
        exit 3
    fi
    export DEPLOY_TARGET
fi
git reset --hard "$DEPLOY_TARGET"
echo "[deploy] 코드 업데이트 완료: $(git log --oneline -1)"

# 2026-11-09 사고 재발 방지: reset 이 이 스크립트 자체를 바꿨으면 새 버전으로 다시 실행한다.
# bash 는 이미 연 옛 파일을 끝까지 실행하므로, 실행 도중 main 이 바뀌면 "새 코드 위에서 옛 배포 로직" 이 돈다.
# 그날 옛 로직이 새 bot.py 를 올리면서 10/30 에 끈 페이퍼 스캐너를 다시 켰다. DEPLOY_REEXEC 는 무한 재실행 방지.
# 2026-11-16: '지금 돌고 있는 스크립트' 가 새 버전과 같으면 다시 실행하지 않는다. 배포 워크플로는 승인한 커밋의 스크립트를 꺼내
#   DEPLOY_REEXEC=1 로 돌리므로 그 경로에서는 다시 실행이 없다. 다시 실행할 때는 변경 전 해시와 대상 커밋을 넘긴다.
NEW_SELF_HASH=$(md5sum "$SELF_PATH" | awk '{print $1}')
if [ "${RUN_HASH:-$OLD_SELF_HASH}" != "$NEW_SELF_HASH" ] && [ -z "$DEPLOY_REEXEC" ]; then
    echo "[deploy] deploy_restart.sh 자체가 갱신됨 → 새 버전으로 재실행"
    export DEPLOY_OLD_BOT_HASH="$OLD_BOT_HASH" DEPLOY_OLD_FILES_HASH="$OLD_FILES_HASH"
    DEPLOY_STAGE=reexec DEPLOY_REEXEC=1 exec bash "$SELF_PATH" "$@"
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

# (2026-11-16 고문 판정 ③) 여기 있던 sweep_defense 블록을 지웠다. 커밋 메시지에 [sweep] 이 있으면 거래 서버에서 연구 스크립트를
# 띄우고 그때 .env 를 통째로 읽던 블록이다. 거래 서버에서는 연구를 돌리지 않는다. 배포는 코드를 맞추고 봇을 다시 띄우는 일만 한다.

if [ "$DEPLOY_FAILED" = "1" ]; then
    echo "[deploy] ⛔ 봇이 새 코드로 떴는지 확인되지 않았다 — 이 배포는 실패로 끝난다"
    exit 1
fi
