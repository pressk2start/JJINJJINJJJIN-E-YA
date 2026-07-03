"""
Research 결과 텔레그램 전송 유틸 — curl subprocess 방식.

bot.py와 동일한 환경변수 사용:
  TELEGRAM_TOKEN (or TG_TOKEN)
  TG_CHATS (or TELEGRAM_CHAT_ID or TG_CHAT)  — 콤마 구분

curl 사용 이유:
  Python requests가 일부 환경에서 IPv6 시도 후 hang됨.
  curl은 자동 IPv4 fallback + 안정적. -4 옵션으로 IPv4 강제.

사용:
    from tg_notify import send
    send("Ceiling 결과", body_text)
"""
import os
import subprocess
import time


TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN") or ""
_raw = os.getenv("TG_CHATS") or os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TG_CHAT") or ""
CHAT_IDS = []
for part in _raw.split(","):
    part = part.strip()
    if part:
        try:
            CHAT_IDS.append(int(part))
        except Exception:
            pass


def _split(text, max_len=4000):
    """Telegram 4096자 제한 → 4000자 단위 분할 (줄바꿈 우선)"""
    if len(text) <= max_len:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > max_len:
        cut = remaining.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def send(title, body, code_block=True):
    """
    title: 짧은 헤더 (예: "🔬 Ceiling Analysis")
    body: 본문 (터미널 출력 그대로. code_block=True면 monospace로 감쌈)

    curl subprocess 사용 — Python requests의 IPv6 hang 회피.
    Returns: True (전부 성공) / False (일부라도 실패)
    """
    if not TG_TOKEN or not CHAT_IDS:
        print("[tg_notify] TELEGRAM_TOKEN 또는 TG_CHATS 없음. 콘솔 출력만.")
        print(f"\n{title}\n{'='*len(title)}\n{body}")
        return False

    if code_block:
        text = f"*{title}*\n```\n{body}\n```"
    else:
        text = f"*{title}*\n\n{body}"

    chunks = _split(text)
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    ok = True

    for chat_id in CHAT_IDS:
        for i, chunk in enumerate(chunks):
            if i > 0:
                time.sleep(0.3)  # rate-limit 방지
            try:
                result = subprocess.run(
                    [
                        "curl", "-sS", "-4",  # -4: IPv4 강제
                        "--max-time", "10",
                        "-X", "POST", url,
                        "-d", f"chat_id={chat_id}",
                        "-d", "parse_mode=Markdown",
                        "-d", "disable_web_page_preview=true",
                        "--data-urlencode", f"text={chunk}",
                    ],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode != 0 or '"ok":true' not in result.stdout:
                    print(f"[tg_notify] chat_id={chat_id} 실패: out={result.stdout[:200]} err={result.stderr[:200]}")
                    ok = False
            except subprocess.TimeoutExpired:
                print(f"[tg_notify] chat_id={chat_id} 타임아웃")
                ok = False
            except Exception as e:
                print(f"[tg_notify] chat_id={chat_id} 예외: {e}")
                ok = False
    return ok


if __name__ == "__main__":
    # 자체 테스트
    result = send(
        "🧪 Research Notify Test",
        "이 메시지가 보이면 정상입니다.\n\nTG_TOKEN, TG_CHATS, curl 확인 완료.",
    )
    print(f"send() 결과: {result}")
