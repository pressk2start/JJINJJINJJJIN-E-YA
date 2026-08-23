"""scalp.py — 스켈핑 전략 연구 라이브러리 (오더북 불균형 OBI + 체결강도 VP).

설계 축:
  진입 = 호가창 매수우위(OBI) + 테이커 매수 우위(체결강도 VP)가 동시에 임계를 넘고
         스프레드가 좁을 때만. 청산 = 트레일/하드스탑/타임캡/OBI 반전 중 최초 발생.

⚠ 데이터 정직성 (이 전략의 최대 제약):
  - 오더북(호가)은 Upbit 공개 API에 **과거 이력이 없다**. 스냅샷만 제공.
    → OBI 기반 백테스트는 forward-recording(collect_frames.py)으로 직접 쌓은 구간만 가능.
    → "과거 6개월 백테스트"는 이 축에서 원천적으로 불가능. 하지 말 것.
  - 체결(trades/ticks)은 daysAgo 1~7 범위만 소급 가능 → VP만 단독 소급 연구 가능.
  - 따라서 v1의 검증 순서는: (1) forward 수집 → (2) 봉인 → (3) out-of-sample forward 재수집.

섹션:
  [FEED]    Upbit 공개 API (orderbook · trades/ticks · seconds candles)
  [FEATURE] OBI · microprice · spread_bp · 체결강도(VP) · Frame 조립
  [ENGINE]  진입 판정 · 청산 규칙 · 체결비용 모델 · simulate()
  [STATS]   result-independent 지표 계산기 (승률/PF/기대값/MDD)

공식 성과 산출은 run_forward.py 로 분리 (봉인 후 1회 전용).
"""
import os, json, math, time, gzip, datetime
from collections import deque

import requests

UPBIT_BASE = "https://api.upbit.com/v1"
REQ_DELAY = 0.12                     # ~8 req/s (공개 quotation 초당 10회 제한 대비 여유)
FMT = "%Y-%m-%dT%H:%M:%S"

# 비용 상수 — 저장소 기존 규약과 일치시킨다 (config.py / bot.py 참조).
#   config.FEE_RATE_ROUNDTRIP = 0.001  → 왕복 10bp, 편도 5bp
#   bot.py _get_trimmed_mean 기본값     → 진입 슬립 5bp · 청산 슬립 8bp (라이브 실측 트림평균)
#   config.PROFIT_CHECKPOINT_MIN_ALPHA = 0.0003 → 최소 알파 3bp
# 단, 호가 소진분(book walk)은 vwap_fill()이 직접 계산하므로 여기서 중복 계상하지 않는다.
# LATENCY_BP = 스냅샷~체결 사이(수백 ms) 가격 이동분만. 캘리브레이션으로 재측정 대상.
DEFAULT_FEE_BP = 5.0                 # 편도
DEFAULT_LATENCY_BP = 3.0             # 편도, 실측 전 잠정치 (CALIBRATION.md에서 갱신)
MIN_ALPHA_BP = 3.0
DEFAULT_NOTIONAL_KRW = 300_000.0     # 시뮬 기준 1회 주문 금액. 슬리피지는 이 금액에 종속.


# ============================================================
# [FEED] — 공개 API. 인증 불필요. 실패는 예외 대신 None/[] 반환.
# ============================================================
def _get(path, params, retries=3, timeout=7):
    for a in range(retries):
        try:
            r = requests.get(f"{UPBIT_BASE}{path}", params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(0.5 * (a + 1)); continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException:
            time.sleep(0.4 * (a + 1))
    return None


def markets_krw():
    """현재 상장된 KRW 마켓 코드 리스트. ⚠ survivorship: 상장폐지 종목 미포함."""
    js = _get("/market/all", {"isDetails": "false"}) or []
    return [m["market"] for m in js if m.get("market", "").startswith("KRW-")]


def orderbook(markets):
    """호가 스냅샷. markets = 리스트(최대 ~15개 권장). 반환: {market: raw_dict}."""
    if isinstance(markets, str):
        markets = [markets]
    js = _get("/orderbook", {"markets": ",".join(markets)}) or []
    return {o["market"]: o for o in js}


def trades_ticks(market, count=200, to=None, days_ago=None):
    """최근 체결 내역 (newest-first). count<=500.
    to='HH:mm:ss' · days_ago=1~7 로 소급 가능 (그 이상은 공개 API 미제공).
    각 원소: trade_price · trade_volume · ask_bid("BID"=테이커 매수 / "ASK"=테이커 매도)."""
    p = {"market": market, "count": min(int(count), 500)}
    if to: p["to"] = to
    if days_ago: p["daysAgo"] = int(days_ago)
    return _get("/trades/ticks", p) or []


def seconds_candles(market, count=200, to=None):
    """초봉 (newest-first). 거래 발생한 초에만 캔들 존재 → 갭 정상."""
    p = {"market": market, "count": min(int(count), 200)}
    if to: p["to"] = to
    return _get("/candles/seconds", p) or []


# ============================================================
# [FEATURE] — 원자료 → 특징량. 모두 순수함수 (네트워크 X).
# ============================================================
def ob_levels(raw, depth=5):
    """raw orderbook → (bids, asks). 각각 [(price, size), ...] 최우선호가부터 depth개."""
    units = raw.get("orderbook_units", [])[:depth]
    bids = [(float(u["bid_price"]), float(u["bid_size"])) for u in units]
    asks = [(float(u["ask_price"]), float(u["ask_size"])) for u in units]
    return bids, asks


def obi(bids, asks, depth=5, weighted=True):
    """Order Book Imbalance ∈ [-1, +1]. +1 = 매수벽 일방, -1 = 매도벽 일방.

    weighted=True 면 레벨 i 가중치 1/(i+1) — 최우선호가일수록 체결 확률이 높다는 사전.
    호가 금액(price*size) 기준: 코인마다 수량 단위가 달라 수량 합산은 비교 불가.
    """
    b = bids[:depth]; a = asks[:depth]
    if not b or not a:
        return 0.0
    bv = sum(p * s * (1.0 / (i + 1) if weighted else 1.0) for i, (p, s) in enumerate(b))
    av = sum(p * s * (1.0 / (i + 1) if weighted else 1.0) for i, (p, s) in enumerate(a))
    tot = bv + av
    return 0.0 if tot <= 0 else (bv - av) / tot


def microprice(bids, asks):
    """최우선호가 사이즈 가중 중간가. 사이즈가 큰 쪽에서 멀어진다(= 체결 예상 방향)."""
    if not bids or not asks:
        return 0.0
    bp, bs = bids[0]; ap, as_ = asks[0]
    tot = bs + as_
    return (bp + ap) / 2.0 if tot <= 0 else (bp * as_ + ap * bs) / tot


def spread_bp(bids, asks):
    """최우선 스프레드 (bp). 스켈핑 손익의 1차 결정변수 — 넓으면 진입 자체가 손해."""
    if not bids or not asks:
        return float("inf")
    bp, _ = bids[0]; ap, _ = asks[0]
    mid = (bp + ap) / 2.0
    return float("inf") if mid <= 0 else (ap - bp) / mid * 1e4


def volume_power(ticks):
    """체결강도 VP = 테이커매수대금 / 테이커매도대금 × 100.

    100 = 균형, >100 = 매수 우위. 매도 체결이 0이면 상한 999.0 으로 클립
    (∞ 방지 · 임계 비교에서 얇은 구간이 항상 통과하는 것을 막기 위해 tick 수 게이트와 병용).
    """
    buy = sell = 0.0
    for t in ticks:
        v = float(t.get("trade_price", 0)) * float(t.get("trade_volume", 0))
        if t.get("ask_bid") == "BID":
            buy += v
        else:
            sell += v
    if sell <= 0:
        return 999.0 if buy > 0 else 100.0
    return min(buy / sell * 100.0, 999.0)


def tick_stats(ticks):
    """체결 흐름 요약: 건수 · 총대금 · 순매수대금(테이커 매수 - 매도)."""
    n = len(ticks); val = 0.0; net = 0.0
    for t in ticks:
        v = float(t.get("trade_price", 0)) * float(t.get("trade_volume", 0))
        val += v
        net += v if t.get("ask_bid") == "BID" else -v
    return {"n": n, "value": val, "net": net}


def vwap_fill(levels, krw_amount, mid):
    """호가 사다리를 krw_amount만큼 걷어낼 때의 체결 VWAP과 슬리피지(bp).

    bot.py `_calc_vwap_slip`과 동일한 정의(mid 대비, 양수=불리)를 bp 단위로 옮긴 것.
    반환 (vwap, slip_bp, filled_krw). 호가가 얕아 전액 체결이 안 되면 filled_krw < krw_amount
    → 호출 측에서 insufficient_depth 로 진입 거부해야 한다 (부분체결을 성공으로 위장 금지).
    """
    if not levels or krw_amount <= 0 or mid <= 0:
        return 0.0, float("inf"), 0.0
    remain = float(krw_amount); qty = 0.0; cost = 0.0
    for p, s in levels:
        if p <= 0 or s <= 0:
            continue
        take = min(remain, p * s)
        cost += take; qty += take / p; remain -= take
        if remain <= 0:
            break
    if qty <= 0:
        return 0.0, float("inf"), 0.0
    vwap = cost / qty
    return vwap, abs(vwap - mid) / mid * 1e4, cost


def build_frame(ts, market, raw_ob, ticks, depth=5, notional=DEFAULT_NOTIONAL_KRW):
    """호가 스냅샷 + 직전 구간 체결 → 1 프레임(= 시뮬레이터 입력 1행).

    호가 사다리(bids/asks)를 그대로 보존한다 — 슬리피지는 주문 금액에 종속하므로
    최우선호가만 저장하면 나중에 어떤 금액으로도 정확히 재계산할 수 없다.
    """
    bids, asks = ob_levels(raw_ob, depth)
    ts_ = tick_stats(ticks)
    bp = bids[0][0] if bids else 0.0
    ap = asks[0][0] if asks else 0.0
    mid = (bp + ap) / 2.0 if (bp > 0 and ap > 0) else 0.0
    _, buy_slip, buy_fill = vwap_fill(asks, notional, mid)
    _, sell_slip, sell_fill = vwap_fill(bids, notional, mid)
    return {
        "ts": float(ts), "market": market,
        "bid": bp, "ask": ap, "mid": mid,
        "bids": bids, "asks": asks,
        "obi": obi(bids, asks, depth),
        "micro": microprice(bids, asks),
        "spread_bp": spread_bp(bids, asks),
        "vp": volume_power(ticks),
        "n_tick": ts_["n"], "tick_value": ts_["value"], "net_value": ts_["net"],
        # 참고용 사전계산 (notional 기준). 다른 금액이면 vwap_fill로 재계산할 것.
        "buy_slip_bp": buy_slip, "sell_slip_bp": sell_slip,
        "depth_ok": buy_fill >= notional * 0.999 and sell_fill >= notional * 0.999,
    }


# ============================================================
# [ENGINE] — 진입/청산 규칙 + 체결비용 + 시뮬레이터
# ============================================================
# ⚠⚠ 아래 임계값은 **검증되지 않은 자리표시자**다. 데이터에서 도출된 값이 아니다.
#    FINDINGS.md 결론: 이 축(테이커 30~120초 스켈핑)에서 채택 가능한 임계치는 발견되지 않았다.
#    이 CFG로 실거래하지 말 것. simulate()는 리플레이·회귀테스트 도구로만 쓴다.
DEFAULT_CFG = {
    "depth": 5,
    # --- 진입 게이트 (전부 AND) ---
    "obi_min": 0.25,         # 호가 매수우위
    "vp_min": 130.0,         # 체결강도 (테이커 매수 우위)
    "spread_max_bp": 12.0,   # 스프레드 상한 — 넘으면 진입 금지
    "min_tick": 8,           # 구간 체결 건수 하한 (얇은 구간 배제)
    "min_tick_value": 3e6,   # 구간 체결 대금 하한 (원)
    "confirm_frames": 2,     # 연속 N 프레임 게이트 유지 시에만 진입 (1틱 노이즈 제거)
    # --- 청산 규칙 ---
    "tp_bp": 45.0,           # 익절
    "stop_bp": 25.0,         # 하드 스탑
    "trail_bp": 18.0,        # 고점 대비 트레일
    "arm_sec": 5.0,          # 트레일 무장 지연
    "hold_sec": 120.0,       # 타임캡
    "exit_obi": -0.15,       # OBI 반전 즉시 청산
    # --- 비용 (편도 bp) ---
    "fee_bp": DEFAULT_FEE_BP,
    "latency_bp": DEFAULT_LATENCY_BP,
    "notional_krw": DEFAULT_NOTIONAL_KRW,
    "require_depth": True,   # notional 전액 체결 불가한 호가면 진입 거부
    # --- 리스크 가드 ---
    "cooldown_sec": 30.0,    # 청산 후 동일 마켓 재진입 금지 구간
    "max_trades": 10_000,    # 세션 상한 (시뮬 폭주 방지)
    "daily_stop_bp": -300.0, # 누적 순손익이 이 아래면 세션 중단
}


def cost_floor_bp(cfg):
    """왕복 고정비용 하한 (bp) = 수수료 왕복 + 지연 왕복 + 최소 알파.
    호가 소진(book walk) 분은 금액·시점 종속이라 여기 포함하지 않는다 — vwap_fill이 계산.
    이 값보다 작은 tp_bp는 구조적으로 기대값이 음수 (config.PROFIT_CHECKPOINT 논리와 동일).
    """
    return 2.0 * cfg["fee_bp"] + 2.0 * cfg["latency_bp"] + MIN_ALPHA_BP


def depth_ok(f, cfg):
    """설정 주문금액을 양방향 모두 전액 체결할 수 있는 호가인가."""
    if not cfg.get("require_depth", True):
        return True
    n = cfg["notional_krw"]
    if f.get("mid", 0) <= 0:
        return False
    _, _, bf = vwap_fill(f.get("asks") or [], n, f["mid"])
    _, _, sf = vwap_fill(f.get("bids") or [], n, f["mid"])
    return bf >= n * 0.999 and sf >= n * 0.999


def entry_ok(f, cfg):
    """단일 프레임이 진입 게이트를 통과하는가. 순수 판정 (상태 없음)."""
    return (f["obi"] >= cfg["obi_min"]
            and f["vp"] >= cfg["vp_min"]
            and f["spread_bp"] <= cfg["spread_max_bp"]
            and f["n_tick"] >= cfg["min_tick"]
            and f["tick_value"] >= cfg["min_tick_value"]
            and f["bid"] > 0 and f["ask"] > 0
            and depth_ok(f, cfg))


def fill_buy(f, cfg):
    """테이커 매수 체결가 = 매도호가 사다리 VWAP + 지연 슬립.
    최우선호가만 쓰면 주문금액이 커질수록 체결가를 낙관하게 된다 → 사다리를 실제로 걷는다."""
    mid = f.get("mid") or ((f["bid"] + f["ask"]) / 2.0)
    vwap, _, filled = vwap_fill(f.get("asks") or [(f["ask"], 1e18)], cfg["notional_krw"], mid)
    if vwap <= 0:
        vwap = f["ask"]
    return vwap * (1.0 + cfg["latency_bp"] / 1e4)


def fill_sell(f, cfg):
    """테이커 매도 체결가 = 매수호가 사다리 VWAP - 지연 슬립."""
    mid = f.get("mid") or ((f["bid"] + f["ask"]) / 2.0)
    vwap, _, filled = vwap_fill(f.get("bids") or [(f["bid"], 1e18)], cfg["notional_krw"], mid)
    if vwap <= 0:
        vwap = f["bid"]
    return vwap * (1.0 - cfg["latency_bp"] / 1e4)


def net_bp(entry_px, exit_px, cfg):
    """왕복 순손익 (bp). 수수료 편도 fee_bp × 2 차감.
    슬리피지(호가 소진 + 지연)는 체결가에 이미 반영되어 있으므로 중복 차감하지 않는다."""
    if entry_px <= 0:
        return 0.0
    return (exit_px / entry_px - 1.0) * 1e4 - 2.0 * cfg["fee_bp"]


def exit_reason(f, pos, cfg):
    """청산 사유 판정. 우선순위: 하드스탑 > 익절 > 트레일 > OBI반전 > 타임캡.

    스탑을 최우선에 두는 이유 = 같은 프레임에서 고가·저가가 모두 트리거된 것처럼 보일 때
    최악을 택하는 보수 가정 (프레임 내부 경로는 알 수 없음).
    """
    px = f["bid"]
    if px <= 0:
        return None
    held = f["ts"] - pos["entry_ts"]
    if px <= pos["entry_px"] * (1.0 - cfg["stop_bp"] / 1e4):
        return "stop"
    if px >= pos["entry_px"] * (1.0 + cfg["tp_bp"] / 1e4):
        return "tp"
    if held >= cfg["arm_sec"] and px <= pos["peak"] * (1.0 - cfg["trail_bp"] / 1e4):
        return "trail"
    if f["obi"] <= cfg["exit_obi"]:
        return "obi_flip"
    if held >= cfg["hold_sec"]:
        return "timecap"
    return None


def simulate(frames, cfg=None):
    """단일 마켓 프레임 시퀀스 리플레이 → (trades, summary).

    규칙:
      - 프레임은 ts 오름차순이어야 한다 (아니면 ValueError — silent skip 금지).
      - 동시에 최대 1 포지션. 진입은 confirm_frames 연속 통과 후 '다음' 프레임에서 체결
        (같은 프레임 내 신호·체결 = look-ahead 이므로 금지).
      - 마지막 프레임에 포지션이 남으면 강제 청산 후 reason='eod'.
    """
    cfg = dict(DEFAULT_CFG, **(cfg or {}))
    for i in range(1, len(frames)):
        if frames[i]["ts"] < frames[i - 1]["ts"]:
            raise ValueError(f"frames not sorted by ts at index {i}")

    trades = []; pos = None; streak = 0; armed = False
    cum_bp = 0.0; cooldown_until = -1e18; halted = False

    for f in frames:
        if pos is not None:
            pos["peak"] = max(pos["peak"], f["bid"])
            r = exit_reason(f, pos, cfg)
            if r:
                px = fill_sell(f, cfg)
                bp = net_bp(pos["entry_px"], px, cfg)
                cum_bp += bp
                trades.append({"market": f["market"], "entry_ts": pos["entry_ts"], "exit_ts": f["ts"],
                               "entry_px": pos["entry_px"], "exit_px": px, "net_bp": bp,
                               "reason": r, "held_sec": f["ts"] - pos["entry_ts"]})
                pos = None; streak = 0; armed = False
                cooldown_until = f["ts"] + cfg["cooldown_sec"]
                if cum_bp <= cfg["daily_stop_bp"] or len(trades) >= cfg["max_trades"]:
                    halted = True
                    break
            continue

        if halted or f["ts"] < cooldown_until:
            streak = 0; armed = False
            continue

        if armed:                                   # 직전 프레임까지 확인 완료 → 이번 프레임 체결
            px = fill_buy(f, cfg)
            if px > 0:
                pos = {"entry_ts": f["ts"], "entry_px": px, "peak": f["bid"]}
            armed = False; streak = 0
            continue

        streak = streak + 1 if entry_ok(f, cfg) else 0
        if streak >= cfg["confirm_frames"]:
            armed = True

    if pos is not None and frames:
        f = frames[-1]
        px = fill_sell(f, cfg)
        bp = net_bp(pos["entry_px"], px, cfg)
        trades.append({"market": f["market"], "entry_ts": pos["entry_ts"], "exit_ts": f["ts"],
                       "entry_px": pos["entry_px"], "exit_px": px, "net_bp": bp,
                       "reason": "eod", "held_sec": f["ts"] - pos["entry_ts"]})

    return trades, summarize(trades, halted=halted, n_frames=len(frames))


# ============================================================
# [STATS] — 지표 계산기. 계산만 한다 (출력·판정은 호출 측).
# ============================================================
def summarize(trades, halted=False, n_frames=0):
    n = len(trades)
    if n == 0:
        return {"n": 0, "halted": halted, "n_frames": n_frames}
    xs = [t["net_bp"] for t in trades]
    wins = [x for x in xs if x > 0]; losses = [x for x in xs if x <= 0]
    gp = sum(wins); gl = -sum(losses)
    eq = 0.0; peak = 0.0; mdd = 0.0
    for x in xs:
        eq += x; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {
        "n": n, "halted": halted, "n_frames": n_frames,
        "win_rate": len(wins) / n,
        "expectancy_bp": sum(xs) / n,
        "total_bp": sum(xs),
        "profit_factor": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0),
        "max_dd_bp": mdd,
        "avg_hold_sec": sum(t["held_sec"] for t in trades) / n,
        "reasons": reasons,
    }


# ============================================================
# [IO] — 프레임 저장/로드 (JSONL.gz). 수집기와 시뮬레이터의 유일한 접점.
# ============================================================
def save_frames(path, frames):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for f in frames:
            fh.write(json.dumps(f, separators=(",", ":")) + "\n")
    os.replace(tmp, path)
    return path


def load_frames(path, market=None):
    out = []
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            f = json.loads(line)
            if market is None or f.get("market") == market:
                out.append(f)
    out.sort(key=lambda x: x["ts"])
    return out


def split_by_market(frames):
    d = {}
    for f in frames:
        d.setdefault(f["market"], []).append(f)
    for m in d:
        d[m].sort(key=lambda x: x["ts"])
    return d
