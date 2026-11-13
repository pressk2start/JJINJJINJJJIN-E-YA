#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tg_send — 워크플로에서 텔레그램으로 글 한 편을 보내고, 실제로 전달됐는지 확인한다 (표준 라이브러리).

왜 (2026-11-13 제3세션 지적): 전에는 응답을 버려서, 토큰이나 채팅 번호가 틀려 거절돼도 워크플로가 성공으로 끝났다.
    여기서는 채팅마다 HTTP 상태와 응답의 "ok": true 를 확인하고, 하나라도 전달되지 않으면 종료 코드 1 로 끝난다.

환경 변수  TG_TOKEN, TG_CHATS (쉼표로 구분한 채팅 번호).
           둘 중 하나라도 없으면 아무것도 보내지 않고 0 으로 끝난다 — 알림을 아직 설정하지 않은 상태이지 실패가 아니다.
토큰이 든 주소와 토큰 자체는 어디에도 찍지 않는다. 채팅 번호는 끝 두 자리만 찍는다.

사용
  python3 scalp/research/tg_send.py 글파일
  python3 scalp/research/tg_send.py --text "한 줄"
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

LIMIT = 3900          # 텔레그램 한 통은 4096자까지
TIMEOUT = 20
TRIES = 2


def _post(url, data, timeout):
    """→ (HTTP 상태, 본문 글자). 거절(4xx·5xx)도 예외가 아니라 상태로 돌려준다."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def send(text, token, chats, post=None):
    """채팅마다 보내고 → 전달되지 않은 것의 사유 목록(전부 전달됐으면 빈 목록)."""
    post = post or _post
    failures = []
    for chat in chats:
        why = "?"
        for _ in range(TRIES):
            try:
                status, body = post("https://api.telegram.org/bot%s/sendMessage" % token,
                                    urllib.parse.urlencode({"chat_id": chat, "text": text[:LIMIT], "disable_web_page_preview": "true"}).encode(),
                                    TIMEOUT)
            except Exception as exc:                                  # 시간 초과·연결 실패. 주소(토큰 포함)는 찍지 않는다
                why = type(exc).__name__
                continue
            try:
                reply = json.loads(body)
            except ValueError:
                reply = {}
            if status == 200 and reply.get("ok") is True:
                why = None
                break
            why = "HTTP %s %s" % (status, str(reply.get("description") or "")[:80])
            if status not in (429, 500, 502, 503, 504):               # 토큰·채팅 번호가 틀린 것은 다시 보내도 같다
                break
        if why:
            failures.append("채팅 …%s: %s" % (str(chat)[-2:], why.strip()))
    return failures


def main(argv=None, post=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?")
    ap.add_argument("--text")
    a = ap.parse_args(argv)
    token = (os.environ.get("TG_TOKEN") or "").strip()
    chats = [c.strip() for c in (os.environ.get("TG_CHATS") or "").split(",") if c.strip()]
    if not token or not chats:
        print("telegram not configured")
        return 0
    if a.text is not None:
        text = a.text
    elif a.file:
        with open(a.file, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("telegram: 보낼 글이 비어 있음")
        return 1
    failures = send(text, token, chats, post)
    for f in failures:
        print("telegram 전달 실패 — " + f)
    if not failures:
        print("telegram %d곳 전달 확인" % len(chats))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
