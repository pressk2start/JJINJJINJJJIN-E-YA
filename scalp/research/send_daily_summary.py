#!/usr/bin/env python3
"""채점 성공과 알림 전달을 구분한다. 전달 확인 뒤에만 하루 요약 영수증을 적는다."""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("checked_sender", HERE / "tg_send.py")
TG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TG)


def send_once(ledger_path, text, today, token, chats, sender=None):
    path = Path(ledger_path)
    ledger = json.loads(path.read_text(encoding="utf-8"))
    if ledger.get("summary_sent_on") == today:
        print("오늘 요약은 이미 전달됨")
        return 0
    if not token or not chats:
        print("telegram not configured")
        return 0
    failures = (sender or TG.send)(text, token, chats)
    if failures:
        print("telegram 전달 실패: %d곳" % len(failures))
        return 1
    ledger["summary_sent_on"] = today
    ledger["summary_covers_run_day"] = (ledger.get("new") or {}).get("day")
    tmp = path.with_suffix(path.suffix + ".notify.tmp")
    try:
        tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--ledger", default=str(HERE / "forward/ledger.json"))
    ap.add_argument("--today")
    args = ap.parse_args(argv)
    text = Path(args.file).read_text(encoding="utf-8")
    if not text.strip():
        print("보낼 요약이 비어 있음")
        return 1
    token = (os.environ.get("TG_TOKEN") or "").strip()
    chats = [s.strip() for s in (os.environ.get("TG_CHATS") or "").split(",") if s.strip()]
    today = args.today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    return send_once(args.ledger, text, today, token, chats)


if __name__ == "__main__":
    raise SystemExit(main())
