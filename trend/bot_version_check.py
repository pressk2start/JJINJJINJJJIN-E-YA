#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bot_version_check — 실행 중인 단타 봇이 디스크의 bot.py 와 같은 코드로 떠 있는지 확인한다 (표준 라이브러리).

왜: 배포 명령이 `git reset` 을 먼저 하고 deploy_restart.sh 를 부르면, 스크립트가 재는 '변경 전/후 해시' 가 같아져
    새 bot.py 가 디스크에만 있고 옛 프로세스가 계속 도는 일이 생긴다(2026-11-10 검토에서 확인). 파일이 바뀌었는지가 아니라
    '지금 도는 프로세스가 어떤 파일에서 시작됐는지' 로 판단해야 한다. bot.py 는 기동할 때 data/h3_reserved.json 에
    자기 pid 와 자기 소스의 md5 를 적는다. 이 스크립트는 그 값과 현재 파일·현재 pid 를 비교한다.

종료코드 0 = 같은 코드로 실행 중,  1 = 재시작 필요(사유를 출력).
  python3 bot_version_check.py --marker data/h3_reserved.json --bot-file bot.py --pid <MainPID>
"""
import argparse
import hashlib
import json
import sys


def needs_restart(marker_path, bot_file, pid):
    """→ (재시작 필요 여부, 사유)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        return True, "bot is not running"
    try:
        with open(bot_file, "rb") as fh:
            file_md5 = hashlib.md5(fh.read()).hexdigest()
    except OSError as exc:
        return True, "cannot read %s: %s" % (bot_file, exc)
    try:
        with open(marker_path, encoding="utf-8") as fh:
            marker = json.load(fh)
    except (OSError, ValueError):
        return True, "no marker — the running bot predates the H3 guard"
    if marker.get("pid") != pid:
        return True, "marker pid %s is not the running pid %s — the bot restarted from code that does not write the marker" % (
            marker.get("pid"), pid)
    if marker.get("bot_md5") != file_md5:
        return True, "the running bot was started from a different bot.py (running %s, on disk %s)" % (
            str(marker.get("bot_md5"))[:8], file_md5[:8])
    if not marker.get("order_block"):
        return True, "the running bot lacks the order-layer block for H3 markets"
    return False, "running bot pid %d was started from the bot.py on disk (md5 %s)" % (pid, file_md5[:8])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--marker", required=True)
    ap.add_argument("--bot-file", required=True)
    ap.add_argument("--pid", required=True)
    a = ap.parse_args(argv)
    restart, why = needs_restart(a.marker, a.bot_file, a.pid)
    print(why)
    return 1 if restart else 0


if __name__ == "__main__":
    sys.exit(main())
