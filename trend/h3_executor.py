#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""h3_executor — H3 동결 규칙을 하루 1회 실행한다. 기본 SHADOW(주문 없음), H3_MODE=LIVE 일 때만 실주문.
표준 라이브러리만 사용 (requests·PyJWT 불필요). bot.py 를 import 하지 않는다(별도 프로세스·별도 상태).

규칙 (scalp/research/trend_paper.py 의 함수를 그대로 import — 복제 없음, 변경 금지):
  매일 09:00 KST(=00:00 UTC) 직후 1회. BTC·ETH 각각, 직전 완료 일봉 종가 > 직전 50개 일봉 종가 평균 → 보유, 아니면 현금.
  배정 자금을 반씩 나눈 독립 슬리브. 리밸런싱 없음. 신호를 사람이 뒤집지 않는다.

모드
  SHADOW (기본)  주문 없음. 그 시각 실제 호가를 걷어 체결가를 계산해 가상 슬리브에 기록하고 텔레그램 한 줄. 일봉 시가 대비
                 미끄러짐(bp)을 남겨 페이퍼의 편도 0.15% 가정이 맞는지 본다.
  LIVE           H3_MODE=LIVE 그리고 H3_CAPITAL_KRW>0 그리고 API 키가 있을 때만. 시장가 주문(매수 ord_type=price, 매도 market),
                 체결은 주문 조회(trades[] VWAP)로 확인한 값만 기록한다. SHADOW 와 LIVE 는 상태 파일이 따로다.

안전장치
  1) 하루 1회·재시작 안전: 종목별 last_day 로 같은 날 재실행은 아무것도 하지 않는다. 동시 실행은 파일 잠금으로 막는다.
  2) 중복 주문 방지: 주문 전에 상태 파일에 pending(결정일·방향·identifier)을 먼저 쓴다. identifier 는 날짜·종목·방향으로 정해져
     같은 주문을 두 번 낼 수 없다(거래소가 중복 identifier 를 거부). 죽었다 살아나면 identifier 로 주문을 조회해 체결을 반영한다.
     주문 POST 는 재시도하지 않는다.
  3) 단타 봇과 분리: bot.py 는 자기가 사지 않은 잔고를 '유령 포지션'으로 입양해 판다. bot.py 가 H3_RESERVED_MARKETS 가드와 함께
     떠 있다는 마커(data/h3_reserved.json 의 pid 가 실제 bot.py 프로세스)가 확인될 때만 LIVE 매수한다. 봇이 옛 코드로 돌고 있으면
     매수를 거부하고 알린다. 매도는 막지 않는다.
  4) 잔고 확인: 매수 전 KRW 잔고, 매도 전 코인 잔고를 계좌에서 확인한다. 슬리브 수량보다 잔고가 적으면 있는 만큼만 팔고 알린다.
  5) 자료 확인: 어제 일봉이 아직 없으면(거래소 지연) 아무것도 하지 않고 다음 실행에 맡긴다.
  6) 지연 실행: 09:00 KST 에서 H3_MAX_LATE_MIN(기본 30분) 넘게 늦은 실행의 매수는 보류하고 알린다(--allow-late 로만 실행).
     매도는 늦어도 실행한다 — 규칙상 나와야 할 포지션을 들고 있는 쪽이 더 위험하다.
  7) 가격 확인: 실주문 직전 실제 호가로 예상 체결가를 계산한다. 중간가보다 H3_MAX_BUY_SLIP_BP(기본 30bp) 넘게 불리하거나
     현재가가 어제 종가에서 H3_MAX_GAP_PCT(기본 20%) 넘게 벗어나면 매수 거부. 매도는 H3_MAX_SELL_SLIP_BP(기본 150bp)까지 허용,
     그걸 넘으면(호가 고장) 보류하고 🚨 로 알린다. 청산 실패는 항상 🚨.
  8) 한 종목의 실패가 메모리에 남긴 변경은 버리고 디스크 상태로 되돌린다. 체결 반영은 저장을 마친 뒤에야 장부(jsonl)에 쓰고,
     장부 쓰기 실패는 거래를 반복시키지 않는다.
  ※ 단타 봇의 LIVE_ENTRY_DISABLED=1 은 이 실행기와 무관하다. 이 실행기의 실주문은 H3_MODE 로만 켜고 끈다.

환경변수
  H3_MODE=SHADOW|LIVE (기본 SHADOW)    H3_CAPITAL_KRW=배정 원화 (LIVE 필수, SHADOW 기본 1,000,000)
  H3_STATE_DIR=상태 폴더 (기본 ~/h3_state, 저장소 밖)    H3_ENV_FILE=추가로 읽을 .env (기본 /home/ubuntu/bot/.env)
  위 H3_* 는 ~/h3_state/h3.env 에 적는다(타이머와 수동 실행이 같은 파일을 읽는다). 이미 설정된 환경변수가 파일보다 우선.
  H3_BOT_MARKER=bot.py 마커 (기본 /home/ubuntu/bot/data/h3_reserved.json)
  UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY (LIVE)    TELEGRAM_TOKEN|TG_TOKEN, TG_CHATS|TELEGRAM_CHAT_ID|TG_CHAT (선택)

실행
  python3 h3_executor.py            하루치 실행(이미 했으면 아무것도 안 함)
  python3 h3_executor.py --status   슬리브·마지막 결정 출력
  python3 h3_executor.py --dry-run  결정만 계산해 출력 (상태·주문·텔레그램 없음)
  python3 h3_executor.py --smoke-test   LIVE 전용. BTC 7,000원을 시장가로 사서 바로 판다(수수료 약 7원). 첫 실제 신호가
                                    오기 전에 인증·주문·identifier 조회·체결 집계·봇 분리 확인이 실거래소에서 도는지 본다.
                                    슬리브 장부는 건드리지 않는다.
서버 설치는 trend/h3-executor.service·timer 를 사람이 직접 enable 한다. deploy_restart.sh 는 이것을 자동으로 켜지 않는다.
"""
import argparse
import base64
import datetime as dt
import hashlib
import hmac
import importlib.util
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

try:
    import fcntl
except ImportError:                                   # Windows 개발 PC
    fcntl = None

API = "https://api.upbit.com"
FEE = 0.0005
MIN_ORDER_KRW = 5000
DEFAULT_SHADOW_CAPITAL = 1000000.0


def load_rule():
    path = Path(__file__).resolve().parents[1] / "scalp" / "research" / "trend_paper.py"
    spec = importlib.util.spec_from_file_location("trend_paper", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TP = load_rule()
ASSETS = TP.ASSETS


class H3Error(Exception):
    pass


class NoFill(H3Error):
    """주문이 끝났는데(done/cancel) 체결이 0. 같은 identifier 는 다시 쓸 수 없으므로 시도 번호를 올려 다시 낸다."""


def order_identifier(sleeve, day, market, side):
    attempt = sleeve.get("attempt") or {}
    n = attempt.get("n", 0) if attempt.get("day") == day else 0
    base = "h3-%s-%s-%s" % (day.replace("-", ""), market, side)
    return base if n == 0 else "%s-r%d" % (base, n)


def bump_attempt(sleeve, day):
    attempt = sleeve.get("attempt") or {}
    sleeve["attempt"] = {"day": day, "n": (attempt.get("n", 0) if attempt.get("day") == day else 0) + 1}


# ----------------------------------------------------------------------------------------------------------------------
# 설정
def load_env_file(path, environ=os.environ):
    """KEY=VALUE 줄을 읽어 아직 없는 변수만 채운다. 따옴표는 벗긴다. 파일이 없으면 조용히 지나간다."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in environ:
                    environ[key] = value
    except OSError:
        pass


class Config(object):
    def __init__(self, environ=os.environ):
        self.mode = (environ.get("H3_MODE") or "SHADOW").strip().upper()
        if self.mode not in ("SHADOW", "LIVE"):
            raise H3Error("H3_MODE must be SHADOW or LIVE, got %r" % self.mode)
        raw = environ.get("H3_CAPITAL_KRW", "").strip().replace(",", "")
        self.capital = float(raw) if raw else (DEFAULT_SHADOW_CAPITAL if self.mode == "SHADOW" else 0.0)
        self.state_dir = Path(environ.get("H3_STATE_DIR") or (Path.home() / "h3_state"))
        self.bot_marker = Path(environ.get("H3_BOT_MARKER") or "/home/ubuntu/bot/data/h3_reserved.json")
        self.access = environ.get("UPBIT_ACCESS_KEY", "")
        self.secret = environ.get("UPBIT_SECRET_KEY", "")
        self.tg_token = environ.get("TELEGRAM_TOKEN") or environ.get("TG_TOKEN") or ""
        chats = environ.get("TG_CHATS") or environ.get("TELEGRAM_CHAT_ID") or environ.get("TG_CHAT") or ""
        self.tg_chats = [c.strip() for c in chats.split(",") if c.strip()]
        self.max_late_min = float(environ.get("H3_MAX_LATE_MIN") or 30)          # 이보다 늦은 실행의 매수는 보류
        self.max_buy_slip_bp = float(environ.get("H3_MAX_BUY_SLIP_BP") or 30)    # 예상 체결가가 중간가보다 이만큼 넘게 불리하면 매수 거부
        self.max_sell_slip_bp = float(environ.get("H3_MAX_SELL_SLIP_BP") or 150)  # 매도는 넉넉히: 이걸 넘으면 호가가 고장난 것
        self.max_gap_pct = float(environ.get("H3_MAX_GAP_PCT") or 20)            # 현재가가 어제 종가에서 이만큼 벗어나면 매수 거부
        if self.mode == "LIVE":
            if not (self.capital > 0 and math.isfinite(self.capital)):
                raise H3Error("LIVE needs H3_CAPITAL_KRW > 0")
            if self.capital / len(ASSETS) < MIN_ORDER_KRW * 2:
                raise H3Error("H3_CAPITAL_KRW too small for %d sleeves" % len(ASSETS))
            if not (self.access and self.secret):
                raise H3Error("LIVE needs UPBIT_ACCESS_KEY and UPBIT_SECRET_KEY")

    @property
    def state_path(self):
        return self.state_dir / ("h3_state_%s.json" % self.mode.lower())

    @property
    def ledger_path(self):
        return self.state_dir / ("h3_ledger_%s.jsonl" % self.mode.lower())

    @property
    def smoke_path(self):
        return self.state_dir / "h3_smoke.json"


# ----------------------------------------------------------------------------------------------------------------------
# 업비트 REST (표준 라이브러리)
def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def jwt_hs256(payload, secret):
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), head + b"." + body, hashlib.sha256).digest())
    return (head + b"." + body + b"." + sig).decode()


def auth_header(access, secret, query=None):
    payload = {"access_key": access, "nonce": str(uuid.uuid4())}
    if query:
        payload["query_hash"] = hashlib.sha512(urllib.parse.urlencode(query).encode()).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    return {"Authorization": "Bearer " + jwt_hs256(payload, secret)}


class Upbit(object):
    """실거래소 클라이언트. 테스트는 같은 메서드를 가진 가짜로 바꿔 끼운다."""

    def __init__(self, access="", secret="", timeout=10):
        self.access, self.secret, self.timeout = access, secret, timeout

    def _call(self, method, path, query=None, body=None, private=False):
        url = API + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        headers = {"Accept": "application/json"}
        data = None
        if private:
            headers.update(auth_header(self.access, self.secret, body if body is not None else query))
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")[:300]
            try:
                name = json.loads(text).get("error", {}).get("name", "")
            except ValueError:
                name = ""
            err = H3Error("upbit %s %s -> %s %s" % (method, path, exc.code, name or text))
            err.code, err.name = exc.code, str(name)
            raise err

    def orderbook(self, market):
        rows = self._call("GET", "/v1/orderbook", {"markets": market})
        units = rows[0]["orderbook_units"]
        asks = sorted((float(u["ask_price"]), float(u["ask_size"])) for u in units)
        bids = sorted(((float(u["bid_price"]), float(u["bid_size"])) for u in units), reverse=True)
        return asks, bids

    def balances(self):
        out = {}
        for acc in self._call("GET", "/v1/accounts", private=True):
            out[acc["currency"]] = float(acc.get("balance") or 0)
        return out

    def place(self, market, side, identifier, krw=None, volume=None):
        body = {"market": market, "side": side, "identifier": identifier}
        if side == "bid":
            body.update(ord_type="price", price=str(int(krw)))
        else:
            body.update(ord_type="market", volume=fmt_volume(volume))
        return self._call("POST", "/v1/orders", body=body, private=True)

    def order(self, identifier):
        try:
            return self._call("GET", "/v1/order", {"identifier": identifier}, private=True)
        except H3Error as exc:
            if getattr(exc, "code", None) == 404 or "not_found" in getattr(exc, "name", ""):
                return None
            raise


def fmt_volume(volume):
    return str(Decimal(repr(float(volume))).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN))


def walk(levels, krw=None, qty=None):
    """호가를 걷어 체결을 계산 → (수량, 대금) 또는 None(잔량 부족). krw 는 쓸 원화, qty 는 팔 수량."""
    got_qty = got_krw = 0.0
    for price, size in levels:
        if krw is not None:
            take = min(size, (krw - got_krw) / price)
        else:
            take = min(size, qty - got_qty)
        got_qty += take
        got_krw += take * price
        if (krw is not None and krw - got_krw <= 1e-6) or (qty is not None and qty - got_qty <= 1e-12):
            return got_qty, got_krw
    return None


def parse_fill(order):
    """주문 조회 응답 → dict(volume, funds, fee, avg, state, uuid). trades[] 가 기준(최상위 executed_funds 는 0 일 수 있다)."""
    volume = funds = 0.0
    for tr in order.get("trades") or []:
        price, vol = float(tr["price"]), float(tr["volume"])
        if not (math.isfinite(price) and math.isfinite(vol) and price > 0 and vol > 0):
            raise H3Error("invalid trade in order %s" % order.get("uuid"))
        volume += vol
        funds += float(tr["funds"]) if tr.get("funds") is not None else price * vol
    fee = float(order.get("paid_fee") or 0)
    if not (math.isfinite(fee) and fee >= 0):
        raise H3Error("invalid paid_fee in order %s" % order.get("uuid"))
    return {"volume": volume, "funds": funds, "fee": fee, "avg": funds / volume if volume > 0 else None,
            "state": order.get("state"), "uuid": order.get("uuid")}


# ----------------------------------------------------------------------------------------------------------------------
# 상태
def new_state(cfg):
    per = cfg.capital / len(ASSETS)
    return {"version": 1, "mode": cfg.mode, "capital": cfg.capital, "created": utc_now().isoformat(),
            "sleeves": {m: {"cash": per, "coin": 0.0, "initial": per, "last_day": None, "pending": None} for m in ASSETS}}


def load_state(cfg):
    path = cfg.state_path
    if not path.exists():
        return new_state(cfg)
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("mode") != cfg.mode or set(state.get("sleeves", {})) != set(ASSETS):
        raise H3Error("state file %s does not match mode/assets" % path)
    return state


def save_state(cfg, state):
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.state_path.with_suffix(".tmp")
    with open(str(tmp), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(cfg.state_path))


def append_ledger(cfg, row):
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(str(cfg.ledger_path), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


# ----------------------------------------------------------------------------------------------------------------------
# 단타 봇과의 분리 확인
def running_bot_pids(proc="/proc"):
    """bot.py 를 실행 중인 프로세스 pid 목록 (리눅스 /proc). 다른 OS 에서는 빈 목록."""
    pids = []
    try:
        names = os.listdir(proc)
    except OSError:
        return pids
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "cmdline"), "rb") as fh:
                args = fh.read().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"/bot.py") or a == b"bot.py" for a in args):
            pids.append(int(name))
    return pids


def bot_guard_ok(cfg, market, pids=None):
    """LIVE 매수 전 확인 → (ok, 사유). 봇이 안 돌면 입양할 주체가 없으니 통과. 돌고 있으면 마커의 pid·마켓이 맞아야 한다."""
    pids = running_bot_pids() if pids is None else pids
    if not pids:
        return True, "no scalper bot running"
    try:
        marker = json.loads(cfg.bot_marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "scalper bot is running (pid %s) but marker %s is missing — it would adopt and sell H3 holdings" % (
            pids, cfg.bot_marker)
    if marker.get("pid") not in pids:
        return False, "marker pid %s is not the running bot %s — bot restarted with code that lacks the guard?" % (
            marker.get("pid"), pids)
    if market not in (marker.get("markets") or []):
        return False, "%s is not in the bot's reserved markets %s" % (market, marker.get("markets"))
    if not marker.get("order_block"):
        return False, "the running bot has no order-layer block for reserved markets — deploy the newer bot.py"
    return True, "bot pid %s reserves %s" % (marker.get("pid"), market)


# ----------------------------------------------------------------------------------------------------------------------
# 하루치 실행
class Executor(object):
    def __init__(self, cfg, exchange, fetch_candles=None, notify=None, now=None, bot_pids=None, sleep=time.sleep,
                 allow_late=False):
        self.cfg, self.ex = cfg, exchange
        self.allow_late = allow_late
        self.fetch_candles = fetch_candles or (lambda market, day: TP.candles(market, through=day, start=day))
        self.notify = notify or (lambda text: None)
        self.now = now or utc_now
        self.bot_pids = bot_pids
        self.sleep = sleep
        self.alerts = []

    # --- 결정 ---
    def decide(self, market, day):
        data = self.fetch_candles(market, day)
        yesterday = TP._shift(day, -1)
        if yesterday not in data:
            raise H3Error("%s: completed candle %s not available yet" % (market, yesterday))
        sig = TP.signal_for_open(data, day)
        if sig["decision_day"] != yesterday:
            raise H3Error("%s: decision day mismatch" % market)
        open_proxy = data[day][0] if day in data else None
        return sig, open_proxy

    # --- 체결 (SHADOW) ---
    def shadow_fill(self, market, side, sleeve):
        asks, bids = self.ex.orderbook(market)
        if side == "bid":
            spend = sleeve["cash"] / (1 + FEE)
            got = walk(asks, krw=spend)
        else:
            got = walk(bids, qty=sleeve["coin"])
        if got is None:
            raise H3Error("%s: order book too thin for shadow fill" % market)
        volume, funds = got
        return {"volume": volume, "funds": funds, "fee": funds * FEE, "avg": funds / volume, "state": "shadow", "uuid": None}

    # --- 체결 (LIVE) ---
    def wait_order(self, identifier, tries=30):
        last = None
        for _ in range(tries):
            last = self.ex.order(identifier)
            if last is not None and last.get("state") in ("done", "cancel"):
                return last
            self.sleep(1.0)
        return last

    def live_fill(self, market, side, sleeve, pending):
        """pending 이 이미 저장된 상태에서 호출. 주문이 없으면 내고, 있으면 조회만 한다."""
        identifier = pending["identifier"]
        order = self.ex.order(identifier)
        if order is None:
            try:
                if side == "bid":
                    self.ex.place(market, "bid", identifier, krw=pending["krw"])
                else:
                    self.ex.place(market, "ask", identifier, volume=pending["volume"])
            except Exception as exc:                            # 시간 초과 등: 들어갔는지 모른다 → 조회로 확인, 재전송 금지
                if self.ex.order(identifier) is None:          # 실제로 안 들어갔다 → pending 은 남기고 다음 실행이 다시 시도
                    raise H3Error("%s %s order not placed: %s" % (market, side, exc))
        order = self.wait_order(identifier)
        if order is None or order.get("state") not in ("done", "cancel"):
            raise H3Error("%s %s order %s not final yet (state=%s) — will reconcile on next run" % (
                market, side, identifier, None if order is None else order.get("state")))
        fill = parse_fill(order)
        if fill["volume"] <= 0:
            raise NoFill("%s %s order %s finished with no fill" % (market, side, identifier))
        return fill

    def apply_fill(self, sleeve, side, fill):
        if side == "bid":
            sleeve["cash"] = max(0.0, sleeve["cash"] - fill["funds"] - fill["fee"])
            sleeve["coin"] += fill["volume"]
        else:
            sleeve["cash"] += fill["funds"] - fill["fee"]
            sleeve["coin"] = max(0.0, sleeve["coin"] - fill["volume"])
            if fill.get("writeoff"):                            # 계좌에 없던 수량은 장부에서도 지운다(없는 코인을 계속 들고 있지 않게)
                sleeve["coin"] = 0.0

    # --- 종목 하나 ---
    def run_market(self, state, market, day, late_min):
        sleeve = state["sleeves"][market]
        pending = sleeve.get("pending")
        if pending and self.cfg.mode == "LIVE":                 # 지난 실행이 주문 도중 끊겼다 → 먼저 정리
            fill = None
            if self.ex.order(pending["identifier"]) is not None:
                try:
                    fill = self.live_fill(market, pending["side"], sleeve, pending)
                    fill["writeoff"] = pending.get("writeoff")
                except NoFill:
                    bump_attempt(sleeve, pending["day"])        # 체결 0 으로 끝난 주문 → 다음 주문은 새 identifier
            if fill is not None:
                self.apply_fill(sleeve, pending["side"], fill)
                sleeve["last_day"] = pending["day"]
            sleeve["pending"] = None
            save_state(self.cfg, state)                         # 체결 반영과 pending 해제를 한 번에 저장한 뒤에야 장부를 쓴다
            if fill is not None:
                self.safe_record(state, market, pending["day"], "BUY" if pending["side"] == "bid" else "SELL", fill,
                                 pending.get("signal"), None, late_min, note="reconciled after restart")
        if sleeve.get("last_day") == day:
            return None                                         # 오늘 이미 처리
        sig, open_proxy = self.decide(market, day)
        mark = sig["close"]
        holding = sleeve["coin"] * mark >= MIN_ORDER_KRW
        action = "BUY" if (sig["on"] and not holding) else "SELL" if (holding and not sig["on"]) else (
            "KEEP_HOLDING" if holding else "STAY_IN_CASH")
        late = late_min > self.cfg.max_late_min
        if action == "BUY" and late and not self.allow_late:
            raise H3Error("%s BUY held: this run is %.0f min after 09:00 KST (limit %.0f). Check the price, then run with "
                          "--allow-late, or leave it and tomorrow's run decides again" % (market, late_min, self.cfg.max_late_min))
        if action == "SELL" and late:                           # 매도는 늦어도 실행한다(규칙상 나와야 할 포지션) — 알리기만
            self.alerts.append("%s: SELL executed %.0f min late" % (market, late_min))
        fill = None
        if action in ("BUY", "SELL"):
            side = "bid" if action == "BUY" else "ask"
            if self.cfg.mode == "SHADOW":
                if side == "bid" and sleeve["cash"] < MIN_ORDER_KRW:
                    raise H3Error("%s: sleeve cash %.0f below minimum order" % (market, sleeve["cash"]))
                fill = self.shadow_fill(market, side, sleeve)
            else:
                try:
                    fill = self.live_order(state, market, side, day, sig)
                except Exception as exc:
                    if side == "ask":                           # 청산 실패는 따로 크게 알린다
                        raise H3Error("🚨 %s SELL FAILED — position is still open: %s" % (market, exc))
                    raise
            self.apply_fill(sleeve, side, fill)
        sleeve["last_day"] = day
        sleeve["pending"] = None
        save_state(self.cfg, state)
        return self.safe_record(state, market, day, action, fill, sig, open_proxy, late_min)

    def pretrade_check(self, market, side, sig, krw=None, volume=None):
        """실주문 직전 호가 점검 → dict(mid, est_avg, est_slip_bp, gap_pct). 이상하면 H3Error (주문을 내지 않는다)."""
        asks, bids = self.ex.orderbook(market)
        if not asks or not bids or asks[0][0] <= bids[0][0]:
            raise H3Error("%s: empty or crossed order book" % market)
        mid = (asks[0][0] + bids[0][0]) / 2.0
        got = walk(asks, krw=krw) if side == "bid" else walk(bids, qty=volume)
        if got is None:
            raise H3Error("%s: order book too thin for this order" % market)
        est = got[1] / got[0]
        slip = 1e4 * (est / mid - 1) * (1 if side == "bid" else -1)
        gap = 100.0 * abs(mid / sig["close"] - 1)
        if side == "bid" and gap > self.cfg.max_gap_pct:
            raise H3Error("%s BUY refused: price %.0f is %.1f%% away from yesterday's close %.0f (limit %.0f%%)" % (
                market, mid, gap, sig["close"], self.cfg.max_gap_pct))
        limit = self.cfg.max_buy_slip_bp if side == "bid" else self.cfg.max_sell_slip_bp
        if slip > limit:
            raise H3Error("%s %s refused: estimated fill is %.0fbp worse than mid (limit %.0fbp)" % (
                market, "BUY" if side == "bid" else "SELL", slip, limit))
        return {"mid": mid, "est_avg": est, "est_slip_bp": slip, "gap_pct": gap}

    def live_order(self, state, market, side, day, sig):
        sleeve = state["sleeves"][market]
        balances = self.ex.balances()
        currency = market.split("-", 1)[1]
        pending = {"day": day, "side": side, "identifier": order_identifier(sleeve, day, market, side),
                   "signal": {"close": sig["close"], "average": sig["average"], "on": sig["on"]}}
        if side == "bid":
            ok, why = bot_guard_ok(self.cfg, market, self.bot_pids)
            if not ok:
                raise H3Error("%s BUY refused: %s" % (market, why))
            krw = int(sleeve["cash"] / (1 + FEE))
            if krw < MIN_ORDER_KRW:
                raise H3Error("%s: sleeve cash %.0f below minimum order" % (market, sleeve["cash"]))
            if balances.get("KRW", 0.0) < krw * (1 + FEE):
                raise H3Error("%s BUY refused: account KRW %.0f < needed %.0f" % (market, balances.get("KRW", 0.0), krw * (1 + FEE)))
            pending["krw"] = krw
            pending["pretrade"] = self.pretrade_check(market, side, sig, krw=krw)
        else:
            have = balances.get(currency, 0.0)
            volume = min(sleeve["coin"], have)
            if volume < sleeve["coin"] * (1 - 1e-6):
                pending["writeoff"] = sleeve["coin"] - volume
                self.alerts.append("%s: account holds %.8f but the book says %.8f — selling what is there and writing off %.8f" % (
                    market, have, sleeve["coin"], pending["writeoff"]))
            if volume <= 0:
                raise H3Error("%s SELL impossible: no %s in the account (sold elsewhere?)" % (market, currency))
            pending["volume"] = float(fmt_volume(volume))
            pending["pretrade"] = self.pretrade_check(market, side, sig, volume=pending["volume"])
        sleeve["pending"] = pending
        save_state(self.cfg, state)                              # 주문보다 먼저 기록 → 재시작해도 같은 identifier 로 조회
        try:
            fill = self.live_fill(market, side, sleeve, pending)
        except NoFill:
            bump_attempt(sleeve, day)
            sleeve["pending"] = None
            save_state(self.cfg, state)
            raise
        fill["writeoff"] = pending.get("writeoff")
        fill["pretrade"] = pending.get("pretrade")
        return fill

    def record(self, state, market, day, action, fill, sig, open_proxy, late_min, note=None):
        sleeve = state["sleeves"][market]
        mark = sig["close"] if sig else None
        equity = sleeve["cash"] + sleeve["coin"] * (fill["avg"] if fill and fill.get("avg") else (mark or 0.0))
        slip_bp = None
        if fill and fill.get("avg") and open_proxy:
            sign = 1 if action in ("BUY", "bid") else -1
            slip_bp = sign * 1e4 * (fill["avg"] / open_proxy - 1)       # +는 불리(매수 비싸게/매도 싸게)
        row = {"ts": self.now().isoformat(), "mode": self.cfg.mode, "day": day, "market": market, "action": action,
               "close": sig["close"] if sig else None, "sma": sig["average"] if sig else None,
               "on": sig["on"] if sig else None, "open_proxy": open_proxy, "fill": fill, "slip_vs_open_bp": slip_bp,
               "cash": sleeve["cash"], "coin": sleeve["coin"], "equity": equity, "initial": sleeve["initial"],
               "late_min": late_min, "note": note}
        append_ledger(self.cfg, row)
        return row

    def safe_record(self, *args, **kwargs):
        """장부(jsonl) 쓰기 실패가 이미 저장된 상태를 되돌리거나 재실행을 유발하지 않게 한다. 실패는 알림으로만."""
        try:
            return self.record(*args, **kwargs)
        except Exception as exc:
            self.alerts.append("ledger write failed (state is saved, trade is NOT repeated): %s" % exc)
            state, market, day, action, fill, sig = args[0], args[1], args[2], args[3], args[4], args[5]
            sleeve = state["sleeves"][market]
            return {"day": day, "market": market, "action": action, "fill": fill, "close": sig["close"] if sig else None,
                    "sma": sig["average"] if sig else None, "on": sig["on"] if sig else None, "slip_vs_open_bp": None,
                    "equity": sleeve["cash"] + sleeve["coin"] * ((fill or {}).get("avg") or (sig["close"] if sig else 0.0))}

    # --- 전체 ---
    def run(self, dry_run=False):
        now = self.now()
        day = now.date().isoformat()
        late_min = round((now - dt.datetime.combine(now.date(), dt.time(), tzinfo=dt.timezone.utc)).total_seconds() / 60, 1)
        if dry_run:
            rows = []
            for market in ASSETS:
                sig, open_proxy = self.decide(market, day)
                rows.append({"market": market, "day": day, "close": sig["close"], "sma": sig["average"], "on": sig["on"],
                             "open_proxy": open_proxy})
            return rows, []
        state = load_state(self.cfg)
        if abs(state["capital"] - self.cfg.capital) > 1:
            self.alerts.append("H3_CAPITAL_KRW=%.0f differs from the state's %.0f — the state value is used" % (
                self.cfg.capital, state["capital"]))
        if self.cfg.mode == "LIVE" and self.cfg.smoke_path.exists():
            self.alerts.append("an unfinished smoke test exists — run --smoke-test once to sell what it bought")
        rows, errors = [], []
        for market in ASSETS:
            try:
                row = self.run_market(state, market, day, late_min)
                if row is not None:
                    rows.append(row)
            except Exception as exc:                            # 한 종목의 실패가 다른 종목을 막지 않는다
                errors.append("%s: %s" % (market, exc))
                # 실패한 종목이 메모리에만 남긴 변경(체결 반영 등)이 다른 종목의 저장에 섞여 들어가면 다음 실행에서
                # 같은 체결을 두 번 더하게 된다 → 디스크에 저장된 상태로 되돌린다. 저장할 것은 각 단계가 직접 저장한다.
                state = load_state(self.cfg)
        if rows or errors or self.alerts:
            self.notify(summary_line(self.cfg, state, day, rows, errors + self.alerts))
        return rows, errors


SMOKE_KRW = 7000
SMOKE_DUST_KRW = 100


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(str(tmp), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))


def smoke_test(cfg, exchange, bot_pids=None, now=None, sleep=time.sleep, market="KRW-BTC"):
    """아주 작은 실주문 왕복으로 LIVE 경로를 검증 → 결과 dict. 실패하면 H3Error.
    매수 전에 h3_smoke.json 을 먼저 쓴다. 매수 뒤 끊기면 그 파일이 남고, 다음 --smoke-test 가 산 수량을 팔아 정리한다."""
    if cfg.mode != "LIVE":
        raise H3Error("smoke test needs H3_MODE=LIVE (it places a real %d KRW order)" % SMOKE_KRW)
    runner = Executor(cfg, exchange, now=now, bot_pids=bot_pids, sleep=sleep)
    recovered = cfg.smoke_path.exists()
    if recovered:
        rec = json.loads(cfg.smoke_path.read_text(encoding="utf-8"))
        guard = "recovering an unfinished smoke test"
    else:
        ok, guard = bot_guard_ok(cfg, market, bot_pids)
        if not ok:
            raise H3Error("smoke test refused: %s" % guard)
        if exchange.balances().get("KRW", 0.0) < SMOKE_KRW * (1 + FEE):
            raise H3Error("smoke test needs %d KRW in the account" % SMOKE_KRW)
        stamp = (now or utc_now)().strftime("%Y%m%d%H%M%S")
        rec = {"market": market, "krw": SMOKE_KRW, "buy_id": "h3-smoke-%s-bid" % stamp, "sell_id": "h3-smoke-%s-ask" % stamp}
        _write_json(cfg.smoke_path, rec)                        # 주문보다 먼저 기록
    market = rec["market"]
    currency = market.split("-", 1)[1]
    if recovered and exchange.order(rec["buy_id"]) is None:     # 매수 주문이 아예 안 들어갔던 경우 → 정리할 것 없음
        cfg.smoke_path.unlink()
        return {"market": market, "guard": guard, "recovered": True, "buy": None, "sell": None, "krw_cost": 0.0, "coin_left": 0.0}
    try:
        buy = runner.live_fill(market, "bid", None, {"identifier": rec["buy_id"], "krw": rec["krw"]})
    except NoFill:
        cfg.smoke_path.unlink()                                 # 체결이 없었으니 남은 코인도 없다
        raise H3Error("smoke test: the buy order finished with no fill — nothing was bought")
    volume = float(fmt_volume(min(buy["volume"], exchange.balances().get(currency, 0.0))))
    if volume <= 0:
        raise H3Error("smoke test: bought %.8f %s but the account has none — check the account, then delete %s" % (
            buy["volume"], currency, cfg.smoke_path))
    sell = runner.live_fill(market, "ask", None, {"identifier": rec["sell_id"], "volume": volume})
    left = max(0.0, buy["volume"] - sell["volume"])
    if left * sell["avg"] > SMOKE_DUST_KRW:
        raise H3Error("smoke test: %.8f %s (about %.0f KRW) is still unsold — sell it in the app, then delete %s" % (
            left, currency, left * sell["avg"], cfg.smoke_path))
    cfg.smoke_path.unlink()
    return {"market": market, "guard": guard, "recovered": recovered, "buy": buy, "sell": sell,
            "krw_cost": (buy["funds"] + buy["fee"]) - (sell["funds"] - sell["fee"]), "coin_left": left}


def summary_line(cfg, state, day, rows, problems):
    parts = ["H3 %s %s" % (cfg.mode, day[5:].replace("-", "/"))]
    names = {"BUY": "매수", "SELL": "매도", "KEEP_HOLDING": "보유유지", "STAY_IN_CASH": "현금유지"}
    for row in rows:
        gap = 100 * (row["close"] / row["sma"] - 1) if row.get("close") and row.get("sma") else None
        text = "%s %s" % (row["market"][4:], names.get(row["action"], row["action"]))
        if gap is not None:
            text += " (종가 %s SMA50 대비 %+.1f%%)" % ("위" if row["on"] else "아래", gap)
        if row.get("fill") and row["fill"].get("avg"):
            text += " 체결 %s" % format(row["fill"]["avg"], ",.0f")
            if row.get("slip_vs_open_bp") is not None:
                text += " 시가대비 %+.1fbp" % row["slip_vs_open_bp"]
        parts.append(text)
    total = sum(r["equity"] for r in rows) if len(rows) == len(ASSETS) else None
    if total is not None:
        parts.append("평가 %s원 (%+.2f%%)" % (format(total, ",.0f"), 100 * (total / state["capital"] - 1)))
    if problems:
        parts.append("⚠ " + " / ".join(problems))
    return " | ".join(parts)


def telegram_sender(cfg):
    def send(text):
        if not (cfg.tg_token and cfg.tg_chats):
            return
        for chat in cfg.tg_chats:
            try:
                body = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
                urllib.request.urlopen(urllib.request.Request(
                    "https://api.telegram.org/bot%s/sendMessage" % cfg.tg_token, data=body), timeout=10).read()
            except Exception as exc:                            # 알림 실패가 실행을 막지 않는다
                print("[h3] telegram failed: %s" % exc)
    return send


def status_text(cfg):
    state = load_state(cfg)
    lines = ["H3 %s | capital %s | state %s" % (cfg.mode, format(state["capital"], ",.0f"), cfg.state_path)]
    for market in ASSETS:
        s = state["sleeves"][market]
        lines.append("  %s cash=%s coin=%.8f last_day=%s pending=%s" % (
            market, format(s["cash"], ",.0f"), s["coin"], s["last_day"], "yes" if s.get("pending") else "no"))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--smoke-test", action="store_true")
    ap.add_argument("--allow-late", action="store_true", help="09:00 KST 에서 H3_MAX_LATE_MIN 분 넘게 늦은 실행에서도 매수를 허용")
    a = ap.parse_args(argv)
    # 사람이 ssh 로 직접 돌릴 때도 타이머와 같은 설정을 쓰도록: h3.env 먼저(우선), 그다음 봇의 .env (키·텔레그램)
    load_env_file(str(Path(os.environ.get("H3_STATE_DIR") or (Path.home() / "h3_state")) / "h3.env"))
    load_env_file(os.environ.get("H3_ENV_FILE") or "/home/ubuntu/bot/.env")
    try:
        cfg = Config()
    except H3Error as exc:
        print("[h3] config error: %s" % exc)
        return 2
    if a.status:
        print(status_text(cfg))
        return 0
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    lock = open(str(cfg.state_dir / "h3.lock"), "w")
    if fcntl is not None:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("[h3] another run holds the lock — exiting")
            return 0
    notify = telegram_sender(cfg)
    if a.smoke_test:
        try:
            res = smoke_test(cfg, Upbit(cfg.access, cfg.secret))
        except Exception as exc:
            print("[h3] SMOKE TEST FAILED: %s" % exc)
            return 1
        if res["buy"] is None:
            print("[h3] SMOKE TEST: the earlier attempt never bought anything — cleaned up. Run --smoke-test again.")
            return 0
        print("[h3] SMOKE TEST OK%s | %s | buy %.8f @ %s (fee %.2f) | sell @ %s (fee %.2f) | cost %.1f KRW | coin left %.8f" % (
            " (recovered an unfinished test)" if res["recovered"] else "",
            res["guard"], res["buy"]["volume"], format(res["buy"]["avg"], ",.0f"), res["buy"]["fee"],
            format(res["sell"]["avg"], ",.0f"), res["sell"]["fee"], res["krw_cost"], res["coin_left"]))
        notify("H3 LIVE 주문 경로 점검 OK: %s 7,000원 매수·매도 왕복, 비용 %.0f원" % (res["market"], res["krw_cost"]))
        return 0
    ex = Executor(cfg, Upbit(cfg.access, cfg.secret), notify=notify, allow_late=a.allow_late)
    try:
        rows, errors = ex.run(dry_run=a.dry_run)
    except Exception as exc:
        print("[h3] run failed: %s" % exc)
        notify("H3 %s 실행 실패: %s" % (cfg.mode, exc))
        return 1
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    for err in errors:
        print("[h3] ERROR %s" % err)
    if not rows and not errors:
        print("[h3] nothing to do (already ran today)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
