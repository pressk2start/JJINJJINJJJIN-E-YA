"""telegram_notify.py — swing 봇 상태·트레이드 알림.
사용:
  from telegram_notify import send
  send("메시지")

환경변수:
  TELEGRAM_TOKEN=<bot_token>
  TELEGRAM_CHAT_ID=<chat_id>
둘 다 없으면 stdout 로그만.
"""
import os, time, requests

def send(text, silent=False):
    """텔레그램 전송. 실패 시 stdout fallback. 봇 실행에 blocking 안 됨."""
    tok=os.environ.get("TELEGRAM_TOKEN","").strip()
    chat=os.environ.get("TELEGRAM_CHAT_ID","").strip()
    ts=time.strftime("%Y-%m-%d %H:%M:%S")
    msg=f"[swing {ts}] {text}"
    if not tok or not chat:
        print(f"[TG-fallback] {msg}", flush=True)
        return False
    try:
        r=requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id":chat,"text":msg,"disable_notification":silent},
            timeout=10)
        if r.status_code==200:
            print(f"[TG-ok] {text[:80]}", flush=True); return True
        print(f"[TG-err {r.status_code}] {r.text[:120]}", flush=True); return False
    except Exception as e:
        print(f"[TG-exc] {e}", flush=True); return False

def notify_startup(mode, extra=""):
    send(f"🚀 swing 시작 · mode={mode} · {extra}")

def notify_paper_trade(action, market, price, size, reason=""):
    send(f"📝 PAPER {action} {market} @ {price:.4f} size={size:.6f} {reason}")

def notify_live_trade(action, market, price, size, order_id=""):
    send(f"💰 LIVE {action} {market} @ {price:.4f} size={size:.6f} order={order_id}")

def notify_error(where, err):
    send(f"❌ ERROR @ {where}: {str(err)[:200]}")

def notify_safety_block(reason):
    send(f"🛑 SAFETY BLOCK: {reason}")

if __name__=="__main__":
    import sys
    m=" ".join(sys.argv[1:]) or "테스트 메시지"
    ok=send(m)
    print(f"result={ok}")
