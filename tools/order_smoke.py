#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
주문 배관 점검 — 소액 실주문으로 매수→매도 왕복을 돌려보고 텔레그램까지 확인한다.

무엇을 확인하는가
-----------------
전략이 아니라 **배관**이다.
  API 키 인증 → 시장가 매수 → 체결 조회 → 시장가 매도 → 텔레그램 알림
이게 다 도는지 한 번에 본다. LIVE 전략과 무관하고 bot.py 를 건드리지 않는다.

왜 별도 스크립트인가
--------------------
bot.py 에는 수동 주문 경로가 없고 텔레그램 명령 수신부도 없다(발신 전용).
bot.py 를 import 하면 봇이 통째로 기동되므로 쓸 수 없다. 그래서 서명·주문을
최소한으로 다시 구현한다.

⚠ 실제 돈이 나간다
------------------
기본 5,000원(업비트 최소 주문금액). 왕복 수수료 0.1% + 스프레드로 대략
10~30원 손실이 난다. 이건 배관 점검 비용이지 전략 손익이 아니다.
--yes 없이는 아무 주문도 내지 않는다.
"""
import os, sys, json, time, uuid, hashlib, argparse, subprocess
from urllib.parse import urlencode
import urllib.request

API = "https://api.upbit.com"
# ⚠ 업비트 최소 주문금액은 매수·매도 **둘 다** 5,000원인데 기준이 다르다.
#   매수: 지정한 KRW 금액 그대로
#   매도: 수량 × **매수 1호가(bid)**
#   5,000원어치를 ask 에 사면 bid 로 환산했을 때 스프레드만큼 모자라 매도가 거부된다.
#   실제로 KRW-XRP 5,000원 왕복이 under_min_total_market_ask 로 두 번 실패했다.
#   그래서 매수 최소를 6,000원으로 올린다 — 스프레드 100bp 까지 견딘다.
MIN_KRW = 6000
MAX_KRW = 20000          # 배관 점검에 이보다 큰 돈을 쓸 이유가 없다
MIN_SELL_KRW = 5000


def load_env(path):
    """systemd EnvironmentFile 과 같은 형식을 읽는다."""
    if not os.path.exists(path):
        return
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _jwt(payload, secret):
    try:
        import jwt as pyjwt
    except ImportError:
        sys.exit("PyJWT 없음: pip3 install PyJWT")
    t = pyjwt.encode(payload, secret, algorithm="HS256")
    return t.decode("utf-8") if isinstance(t, bytes) else t


def _headers(body=None):
    ak = os.getenv("UPBIT_ACCESS_KEY") or os.getenv("ACCESS_KEY")
    sk = os.getenv("UPBIT_SECRET_KEY") or os.getenv("SECRET_KEY")
    if not ak or not sk:
        sys.exit("UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 를 .env 에서 못 찾았다")
    p = {"access_key": ak, "nonce": str(uuid.uuid4())}
    if body:
        q = urlencode(body).encode()
        p["query_hash"] = hashlib.sha512(q).hexdigest()
        p["query_hash_alg"] = "SHA512"
    return {"Authorization": f"Bearer {_jwt(p, sk)}",
            "Content-Type": "application/json"}


def req(method, path, body=None, params=None, timeout=10):
    url = API + path
    data = None
    sig = params if params else body
    if params:
        url += "?" + urlencode(params)
    if body:
        data = json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method,
                               headers=_headers(sig))
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f:
            return json.load(f)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        sys.exit(f"[{method} {path}] HTTP {e.code}: {detail}")


def best_bid(market):
    with urllib.request.urlopen(
            f"{API}/v1/orderbook?markets={market}", timeout=10) as f:
        ob = json.load(f)
    return float(ob[0]["orderbook_units"][0]["bid_price"])


def tg(title, body):
    """봇과 같은 경로(curl)로 보낸다. 실패해도 스크립트는 계속 간다."""
    tok = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN")
    raw = (os.getenv("TG_CHATS") or os.getenv("TELEGRAM_CHAT_ID")
           or os.getenv("TG_CHAT") or "")
    chats = [c.strip() for c in raw.split(",") if c.strip()]
    text = f"[{title}]\n{body}"
    print(f"\n--- 텔레그램 ---\n{text}\n----------------")
    if not tok or not chats:
        print("  (토큰/챗ID 없음 — 콘솔 출력만)")
        return False
    ok = True
    for c in chats:
        r = subprocess.run(["curl", "-sS", "-4", "--max-time", "10",
                            f"https://api.telegram.org/bot{tok}/sendMessage",
                            "-d", f"chat_id={c}", "--data-urlencode", f"text={text}"],
                           capture_output=True, text=True)
        good = '"ok":true' in (r.stdout or "")
        print(f"  → {c}: {'전송됨' if good else '실패 ' + (r.stdout or r.stderr)[:120]}")
        ok &= good
    return ok


def wait_fill(oid, timeout=15):
    for _ in range(int(timeout / 0.5)):
        o = req("GET", "/v1/order", params={"uuid": oid})
        if o.get("state") in ("done", "cancel"):
            return o
        time.sleep(0.5)
    return req("GET", "/v1/order", params={"uuid": oid})


def filled_of(o):
    """체결 수량·평균가·수수료. trades 를 합산한다."""
    vol = float(o.get("executed_volume") or 0)
    fee = float(o.get("paid_fee") or 0)
    funds = sum(float(t["funds"]) for t in (o.get("trades") or []))
    avg = (funds / vol) if vol else 0.0
    return vol, avg, funds, fee


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KRW-XRP", help="유동성 좋은 종목 권장")
    ap.add_argument("--krw", type=int, default=MIN_KRW)
    ap.add_argument("--env", default="/home/ubuntu/bot/.env")
    ap.add_argument("--yes", action="store_true", help="이게 없으면 주문하지 않는다")
    ap.add_argument("--hold-sec", type=float, default=2.0)
    ap.add_argument("--sell-all", action="store_true",
                    help="이번에 산 것뿐 아니라 보유 전량을 판다 (잔량 정리용)")
    ap.add_argument("--sell-only", action="store_true",
                    help="매수 없이 보유분만 시장가 매도 (실패한 점검 뒷정리)")
    a = ap.parse_args()

    load_env(a.env)
    if not a.sell_only and not (MIN_KRW <= a.krw <= MAX_KRW):
        sys.exit(f"--krw 는 {MIN_KRW}~{MAX_KRW} 범위여야 한다 (배관 점검용)")

    # ── 1. 인증 확인 (읽기만) ────────────────────────────────────────
    accts = req("GET", "/v1/accounts")
    krw = next((float(x["balance"]) for x in accts if x["currency"] == "KRW"), 0.0)
    cur = a.market.replace("KRW-", "")
    have = next((float(x["balance"]) for x in accts if x["currency"] == cur), 0.0)
    print(f"[1] 인증 OK · KRW 잔고 {krw:,.0f}원 · {cur} 보유 {have:.8f}")
    if krw < a.krw * 1.1:
        sys.exit(f"KRW 잔고 부족: {krw:,.0f} < {a.krw:,}")

    if a.sell_only:
        bid = best_bid(a.market)
        est = have * bid
        print(f"[정리] 보유 {have:.8f} {cur} · bid {bid:,.4f} → 약 {est:,.0f}원")
        if have <= 0:
            print("  보유 없음 — 할 일 없다.")
            return
        if est < MIN_SELL_KRW:
            sys.exit(f"매도 최소금액 미달: {est:,.0f}원 < {MIN_SELL_KRW:,}원. "
                     f"조금 더 매수해서 합쳐야 팔 수 있다.")
        if not a.yes:
            print("  실제 매도는 --yes 필요.")
            return
        s2 = req("POST", "/v1/orders", body={"market": a.market, "side": "ask",
                                             "ord_type": "market",
                                             "volume": f"{have:.8f}"})
        so2 = wait_fill(s2["uuid"])
        v, avg, funds, fee = filled_of(so2)
        print(f"  매도 체결 {v:.8f} @ {avg:,.4f} · {funds:,.0f}원 · 수수료 {fee:,.2f}원")
        tg("🧹 잔량 정리 완료",
           f"{a.market}\n매도 {v:.8f} @ {avg:,.4f}\n회수 {funds - fee:,.0f}원")
        return

    if not a.yes:
        print(f"\n[중단] 실제 주문은 --yes 를 붙여야 나간다.")
        print(f"       예정: {a.market} 시장가 매수 {a.krw:,}원 → 즉시 전량 시장가 매도")
        print(f"       예상 비용: 수수료 왕복 약 {a.krw*0.001:,.0f}원 + 스프레드")
        tg("🔧 주문 배관 점검 (드라이런)",
           f"인증 OK\nKRW 잔고 {krw:,.0f}원\n\n실주문은 --yes 필요")
        return

    # ── 2. 시장가 매수 ───────────────────────────────────────────────
    print(f"\n[2] 시장가 매수 {a.krw:,}원 …")
    b = req("POST", "/v1/orders", body={"market": a.market, "side": "bid",
                                        "ord_type": "price", "price": str(a.krw)})
    bo = wait_fill(b["uuid"])
    bvol, bavg, bfunds, bfee = filled_of(bo)
    print(f"    체결 {bvol:.8f} {cur} @ {bavg:,.4f} · 대금 {bfunds:,.0f}원 · 수수료 {bfee:,.2f}원")
    if bvol <= 0:
        tg("🔴 주문 배관 점검 실패", f"매수 미체결\nstate={bo.get('state')}")
        sys.exit("매수 미체결 — 매도 단계로 넘어가지 않는다")

    time.sleep(a.hold_sec)

    # ── 3. 전량 시장가 매도 (수수료 차감분까지 실제 잔고로 확인) ──────
    accts = req("GET", "/v1/accounts")
    bal = next((float(x["balance"]) for x in accts if x["currency"] == cur), 0.0)
    sell_qty = bal if a.sell_all else min(bal, bvol)   # 기본은 이번에 산 것만
    bid = best_bid(a.market)
    est = sell_qty * bid
    print(f"\n[3] 시장가 매도 {sell_qty:.8f} {cur} (bid {bid:,.4f} 기준 {est:,.0f}원) …")
    if est < MIN_SELL_KRW:
        tg("⚠ 주문 배관 점검 — 매도 불가, 잔량 남음",
           f"{a.market}\n매수는 됐으나 매도 최소금액 미달\n"
           f"보유 {bal:.8f} {cur} · bid {bid:,.4f} → {est:,.0f}원 < {MIN_SELL_KRW:,}원\n\n"
           f"수동 정리: python3 ~/tools/order_smoke.py --sell-only --sell-all "
           f"--market {a.market}")
        sys.exit(f"매도 최소금액 미달 ({est:,.0f}원 < {MIN_SELL_KRW:,}원). "
                 f"--sell-all 로 전량 정리하거나 --krw 를 올려라.")
    s = req("POST", "/v1/orders", body={"market": a.market, "side": "ask",
                                        "ord_type": "market",
                                        "volume": f"{sell_qty:.8f}"})
    so = wait_fill(s["uuid"])
    svol, savg, sfunds, sfee = filled_of(so)
    print(f"    체결 {svol:.8f} {cur} @ {savg:,.4f} · 대금 {sfunds:,.0f}원 · 수수료 {sfee:,.2f}원")

    # ── 4. 결과 ─────────────────────────────────────────────────────
    net = (sfunds - sfee) - (bfunds + bfee)
    bp = (net / bfunds * 1e4) if bfunds else 0.0
    left = svol < bvol - 1e-12
    msg = (f"{a.market}\n"
           f"매수 {bvol:.8f} @ {bavg:,.4f}  ({bfunds:,.0f}원, 수수료 {bfee:,.0f})\n"
           f"매도 {svol:.8f} @ {savg:,.4f}  ({sfunds:,.0f}원, 수수료 {sfee:,.0f})\n"
           f"왕복 순손익 {net:+,.0f}원 ({bp:+.1f}bp)\n"
           + ("⚠ 잔량 남음 — 수동 확인 필요\n" if left else "")
           + "\n※ 배관 점검이다. 전략 성과가 아니다.")
    print(f"\n[4] 왕복 순손익 {net:+,.0f}원 ({bp:+.1f}bp)")
    tg("✅ 주문 배관 점검 완료" if not left else "⚠ 주문 배관 점검 (잔량)", msg)


if __name__ == "__main__":
    main()
