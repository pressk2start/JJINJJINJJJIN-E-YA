#!/usr/bin/env python3
"""
업비트 실시간 모멘텀 스캐너 — Paper Trading + Telegram
- 1초 간격 거래량 상위/하위 각 15개 (30개) 모니터
- 평소 변동성 대비 이상 급등 감지 (z-score) + 거래대금 delta z-score 동시 확인
- 목표 +0.3% / 트레일링 -0.25% / 타임아웃 180초
- 기존 봇 .env 파일에서 텔레그램 설정 자동 로드
- JSON+CSV 자동 저장

[2026-05 패치]
  A. 재분류 시 포지션 고아화 수정 (호가 우선 청산관리 + 보유종목 항상 fetch)
  B. stale-history 오신호 차단 (틱 간격 갭 감지 시 이력 리셋)
  C. 진입 spread 강제 제한 (MAX_SPREAD_PCT)
  D. 실시간 거래대금 delta z-score 추가 (가격+거래대금 동시 확인)
실행: python3 momentum_scanner.py &
종료: kill %1 또는 Ctrl+C (결과 자동 저장)
"""
import requests
import time
import json
import csv
import os
import sys
from datetime import datetime, timezone, timedelta
from collections import deque, defaultdict

KST = timezone(timedelta(hours=9))

# ─── .env 파일 로드 (기존 봇 폴더에서 자동 읽기) ───
def load_dotenv(path):
    """간단한 .env 파서 (python-dotenv 없이 동작)"""
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

# 1) 같은 폴더의 .env
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
# 2) 기존 봇 폴더 .env
load_dotenv("/home/ubuntu/bot/.env")

# ─── 설정 ───
SCAN_INTERVAL = 1.0           # 초
LOOKBACK_TICKS = 20           # 최근 N틱 기준 변동성 측정 (~20초)
ANOMALY_THRESHOLD = 3.0       # 평소 변동성 대비 X배 이상이면 감지
TRAILING_STOP_PCT = 0.25      # 트레일링 스탑 (고점 대비 -X% 하락 시 청산)
TARGET_PROFIT_PCT = 0.3       # 목표 수익률 도달 시 즉시 청산
MAX_HOLD_SEC = 180            # 최대 보유 시간 (초)
COOLDOWN_SEC = 120            # 같은 종목 재진입 대기
FEE_PCT = 0.05                # 업비트 수수료 편도 0.05% (왕복 0.1%)
MIN_ASK_KRW = 1_000_000       # 매도1호가 depth 최소 100만원 (진입)
MIN_BID_KRW = 2_000_000       # 매수1호가 depth 최소 200만원 (청산)
TOP_N = 30                    # 거래량 상위 N개만 모니터 (하위 그룹 제거)

# ─── 패치 설정 ───
MAX_SPREAD_PCT = 0.15         # [C] 진입 최대 스프레드 (수수료 고려 net-positive 유지)
STALE_GAP_SEC = 5.0           # [B] 직전 틱과 간격이 이보다 크면 이력 리셋 (stale 오신호 차단)
VOLUME_Z_THRESHOLD = 1.5      # [D] 거래대금 delta z-score 최소 (0이면 게이트 해제·로깅만)
MIN_ABS_MOVE = 0.15           # [E] 진입 최소 순간등락 — 목표(TARGET)와 분리 (작게 보고 크게 잡는다)
REVIEW_INTERVAL_SEC = 3600    # [F] 누적 데이터 회고+개선점 추천 주기 (1시간) — 0이면 비활성
REVIEW_MIN_TRADES = 50        # [F] 회고 분석 최소 표본 (통계적 유의성 위해 50건 이상)
BREAKOUT_REQUIRED = True      # [G] 진입 시 직전 N틱 고점 돌파 요구 (fake bounce / mean-revert 제거)
BREAKOUT_WINDOW = 10          # [G] 돌파 비교 윈도 (틱 수)
VOLUME_RATIO_THRESHOLD = 0.0  # [H] 거래대금 delta ratio 게이트 (0=비활성·로깅만 / 활성 시 2~3 권장)

# ─── 저장 경로 ───
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LOG_DIR, "momentum_live.log")
REVIEW_LOG = os.path.join(LOG_DIR, "momentum_review.log")  # [F] 회고 기록 누적
SAVE_DIR = LOG_DIR

# ─── 텔레그램 설정 ───
TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN") or ""
_raw_chats = (
    os.getenv("TG_CHATS")
    or os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TG_CHAT") or "")
CHAT_IDS = []
for part in _raw_chats.split(","):
    part = part.strip()
    if not part:
        continue
    try:
        CHAT_IDS.append(int(part))
    except Exception:
        pass
TG_ENABLED = bool(TG_TOKEN and CHAT_IDS)
_tg_session = requests.Session() if TG_ENABLED else None

# ─── 상태 ───
price_history = defaultdict(lambda: deque(maxlen=LOOKBACK_TICKS + 5))
volume_history = defaultdict(lambda: deque(maxlen=LOOKBACK_TICKS + 5))  # [D] 거래대금 누적값 이력
positions = {}
closed_trades = []
cooldowns = {}
scan_count = 0
start_time = None
_ob_cache = {}  # market -> {"ts": float, "data": dict}
OB_CACHE_TTL = 1.5  # 초
log_fh = None

# ═══════════════════════════════════════════════
# 텔레그램
# ═══════════════════════════════════════════════
def tg_send(text, retry=2):
    if not TG_ENABLED:
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for cid in CHAT_IDS:
        for attempt in range(retry + 1):
            try:
                resp = _tg_session.post(url, json={
                    "chat_id": cid,
                    "text": text,
                    "disable_web_page_preview": True,
                }, timeout=5)
                if resp.status_code == 429:
                    wait = resp.json().get("parameters", {}).get("retry_after", 3)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    log(f"⚠ TG {resp.status_code}: {resp.text[:120]}")
                break
            except Exception as e:
                if attempt == retry:
                    log(f"⚠ TG 전송 실패: {e}")
                time.sleep(1)

# ═══════════════════════════════════════════════
# 로그
# ═══════════════════════════════════════════════
def log(msg):
    ts = datetime.now(KST).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if log_fh:
        log_fh.write(line + "\n")
        log_fh.flush()

# ═══════════════════════════════════════════════
# 업비트 API
# ═══════════════════════════════════════════════
def get_all_krw_markets():
    url = "https://api.upbit.com/v1/market/all?is_details=true"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    return [m["market"] for m in resp.json() if m["market"].startswith("KRW-")]

def get_tickers(markets):
    url = "https://api.upbit.com/v1/ticker"
    results = []
    for i in range(0, len(markets), 100):
        batch = markets[i:i+100]
        params = {"markets": ",".join(batch)}
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        results.extend(resp.json())
        if i + 100 < len(markets):
            time.sleep(0.15)
    return results

def get_orderbook(market):
    """단일 종목 호가 조회 — 캐시 적용. monitor_list와 무관하게 항상 조회 가능."""
    now = time.time()
    cached = _ob_cache.get(market)
    if cached and now - cached["ts"] < OB_CACHE_TTL:
        return cached["data"]
    try:
        url = "https://api.upbit.com/v1/orderbook"
        resp = requests.get(url, params={"markets": market}, timeout=3)
        resp.raise_for_status()
        data = resp.json()[0]
        units = data.get("orderbook_units", [])
        if not units:
            return None
        best = units[0]
        result = {
            "ask": best["ask_price"],
            "bid": best["bid_price"],
            "ask_size": best["ask_size"],
            "bid_size": best["bid_size"],
        }
        _ob_cache[market] = {"ts": now, "data": result}
        return result
    except Exception:
        return cached["data"] if cached else None

def classify_markets(tickers):
    sorted_by_vol = sorted(tickers, key=lambda t: t.get("acc_trade_price_24h", 0))
    top = [t["market"] for t in sorted_by_vol[-TOP_N:]]
    return set(top), set()

# ═══════════════════════════════════════════════
# 감지
# ═══════════════════════════════════════════════
def detect_anomaly(market, current_price, now_ts):
    history = price_history[market]
    if history and now_ts - history[-1][0] > STALE_GAP_SEC:
        history.clear()
    history.append((now_ts, current_price))
    if len(history) < LOOKBACK_TICKS:
        return None
    prices = list(history)
    returns = []
    for i in range(1, len(prices)):
        r = (prices[i][1] - prices[i-1][1]) / prices[i-1][1] * 100
        returns.append(r)
    if len(returns) < 5:
        return None
    recent_avg = sum(returns[-3:]) / 3
    base_returns = returns[:-3]
    if len(base_returns) < 3:
        return None
    mean_r = sum(base_returns) / len(base_returns)
    variance = sum((r - mean_r) ** 2 for r in base_returns) / len(base_returns)
    std_r = max(variance ** 0.5, 0.01)
    z_score = (recent_avg - mean_r) / std_r
    base_price = prices[-4][1]
    abs_move = (current_price - base_price) / base_price * 100
    if z_score >= ANOMALY_THRESHOLD and abs_move > 0:
        if BREAKOUT_REQUIRED:
            win = min(BREAKOUT_WINDOW + 1, len(prices))
            prev_high = max(p[1] for p in prices[-win:-1])
            if current_price <= prev_high:
                return None
        return {
            "z_score": round(z_score, 2),
            "abs_move": round(abs_move, 3),
        }
    return None

def volume_stats(market, acc_price, now_ts):
    """[D/H] 거래대금 delta z-score와 ratio(recent/mean) 동시 반환."""
    h = volume_history[market]
    if h and now_ts - h[-1][0] > STALE_GAP_SEC:
        h.clear()
    h.append((now_ts, acc_price))
    if len(h) < LOOKBACK_TICKS:
        return None, None
    vals = [x[1] for x in h]
    deltas = [max(vals[i] - vals[i-1], 0.0) for i in range(1, len(vals))]
    if len(deltas) < 5:
        return None, None
    recent = sum(deltas[-3:]) / 3
    base = deltas[:-3]
    if len(base) < 3:
        return None, None
    mean_d = sum(base) / len(base)
    var = sum((d - mean_d) ** 2 for d in base) / len(base)
    std_d = max(var ** 0.5, mean_d * 0.5, 1.0)
    z = (recent - mean_d) / std_d
    z_clamped = round(max(min(z, 99.0), -99.0), 2)
    ratio = round(min(recent / max(mean_d, 1.0), 9999.0), 2)
    return z_clamped, ratio

# ═══════════════════════════════════════════════
# 포지션 관리
# ═══════════════════════════════════════════════
def manage_positions(tickers_dict, now_ts):
    to_close = []
    for market, pos in positions.items():
        ob = get_orderbook(market)
        if ob:
            exit_price = ob["bid"]
        else:
            ticker = tickers_dict.get(market)
            if not ticker:
                continue
            exit_price = ticker["trade_price"]
        hold_sec = now_ts - pos["entry_time"]
        if exit_price > pos["peak_price"]:
            pos["peak_price"] = exit_price
        if exit_price < pos["worst_price"]:
            pos["worst_price"] = exit_price
        drawdown = (pos["peak_price"] - exit_price) / pos["peak_price"] * 100
        pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
        trail_pct = TRAILING_STOP_PCT
        if pos["spread_pct"] <= 0.12 and pos["slip_buy"] <= 0.10:
            trail_pct = 0.35
        elif pos["spread_pct"] >= 0.22 or pos["slip_buy"] >= 0.18:
            trail_pct = 0.18
        reason = None
        if pnl_pct >= TARGET_PROFIT_PCT:
            reason = f"target({pnl_pct:+.2f}%)"
        elif drawdown >= trail_pct:
            reason = f"trail({drawdown:.2f}%/{trail_pct:.2f})"
        elif hold_sec >= MAX_HOLD_SEC:
            reason = f"timeout({hold_sec:.0f}s)"
        if reason:
            net_pnl = round(pnl_pct - FEE_PCT * 2, 4)
            mae_pct = round((pos["worst_price"] - pos["entry_price"]) / pos["entry_price"] * 100, 4)
            to_close.append({
                "market": market,
                "group": pos["group"],
                "entry_price": pos["entry_price"],
                "exit_price": exit_price,
                "pnl": round(pnl_pct, 4),
                "net_pnl": net_pnl,
                "mae": mae_pct,
                "hold_sec": round(hold_sec, 1),
                "reason": reason,
                "peak_pnl": round((pos["peak_price"] - pos["entry_price"]) / pos["entry_price"] * 100, 4),
                "time": datetime.now(KST).strftime("%H:%M:%S"),
                "z_score": pos["z_score"],
                "vol_z": pos["vol_z"],
                "vol_ratio": pos["vol_ratio"],
                "abs_move": pos["abs_move"],
                "spread_pct": pos["spread_pct"],
                "ask_krw": pos["ask_krw"],
                "bid_krw": pos["bid_krw"],
                "slip_buy": pos["slip_buy"],
            })
    for trade in to_close:
        market = trade["market"]
        del positions[market]
        cooldowns[market] = now_ts
        closed_trades.append(trade)
        emoji = "🟢" if trade["net_pnl"] > 0 else "🔴"
        coin = market.replace("KRW-", "")
        price_diff = trade["exit_price"] - trade["entry_price"]
        peak_price = trade["entry_price"] * (1 + trade["peak_pnl"] / 100)
        n_total = len(closed_trades)
        n_wins = sum(1 for t in closed_trades if t["net_pnl"] > 0)
        sum_pnl = sum(t["net_pnl"] for t in closed_trades)
        wr = n_wins / n_total * 100 if n_total else 0
        msg = (
            f"{emoji} {trade['net_pnl']:+.3f}% ({price_diff:+,.0f}원) {coin} [{trade['group']}]\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{trade['entry_price']:,.0f} → {trade['exit_price']:,.0f}원\n"
            f"손익 {trade['pnl']:+.3f}% 수수료후 {trade['net_pnl']:+.3f}%\n"
            f"보유 {trade['hold_sec']:.0f}초 | {trade['reason']} | MFE{trade['peak_pnl']:+.3f}% MAE{trade['mae']:+.3f}%\n"
            f"spread:{trade['spread_pct']:.3f}% z:{trade['z_score']} vz:{trade['vol_z']} vr:{trade['vol_ratio']}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"누적: {n_total}전 {n_wins}승 wr{wr:.0f}% sum{sum_pnl:+.3f}%"
        )
        log(msg)
        tg_send(f"📊모멘텀\n{msg}")

# ═══════════════════════════════════════════════
# 요약
# ═══════════════════════════════════════════════
def get_summary_text():
    elapsed = time.time() - start_time
    n = len(closed_trades)
    if n == 0:
        open_str = ""
        if positions:
            open_str = "\nOPEN: " + ", ".join(
                f"{m.replace('KRW-','')}({p['group'][0]})" for m, p in positions.items()
            )
        return f"📊 모멘텀 {elapsed/60:.0f}분 | 거래 없음{open_str}"
    wins = sum(1 for t in closed_trades if t["net_pnl"] > 0)
    wr = wins / n * 100
    avg_pnl = sum(t["net_pnl"] for t in closed_trades) / n
    total_pnl = sum(t["net_pnl"] for t in closed_trades)
    top_trades = [t for t in closed_trades if t["group"] == "상위"]
    bot_trades = [t for t in closed_trades if t["group"] == "하위"]
    def gs(trades):
        if not trades:
            return "0전"
        nn = len(trades)
        w = sum(1 for t in trades if t["net_pnl"] > 0)
        a = sum(t["net_pnl"] for t in trades) / nn
        return f"{nn}전{w}승 wr{w/nn*100:.0f}% avg{a:+.3f}%"
    open_str = ""
    if positions:
        open_str = "\nOPEN: " + ", ".join(
            f"{m.replace('KRW-','')}({p['group'][0]})" for m, p in positions.items()
        )
    reasons = defaultdict(int)
    for t in closed_trades:
        r = t["reason"].split("(")[0]
        reasons[r] += 1
    lines = [
        f"📊 모멘텀 {elapsed/60:.0f}분",
        f"총 {n}전{wins}승 wr{wr:.0f}% avg{avg_pnl:+.3f}% sum{total_pnl:+.2f}%",
        f"상위: {gs(top_trades)}",
        f"하위: {gs(bot_trades)}",
        "청산: " + " ".join(f"{k}:{v}" for k, v in reasons.items()),
    ]
    if open_str:
        lines.append(open_str)
    return "\n".join(lines)

def print_summary():
    summary = get_summary_text()
    print(f"\n{summary}")
    if log_fh:
        log_fh.write(f"{summary}\n")
        log_fh.flush()

def save_results(tag=""):
    if not closed_trades:
        return None
    ts_str = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    prefix = f"momentum_{tag}_{ts_str}" if tag else f"momentum_{ts_str}"
    json_path = os.path.join(SAVE_DIR, f"{prefix}.json")
    with open(json_path, "w") as f:
        json.dump({
            "config": {
                "anomaly_threshold": ANOMALY_THRESHOLD,
                "trailing_stop_pct": TRAILING_STOP_PCT,
                "target_profit_pct": TARGET_PROFIT_PCT,
                "fee_pct": FEE_PCT,
                "max_hold_sec": MAX_HOLD_SEC,
                "top_n": TOP_N,
                "scan_interval": SCAN_INTERVAL,
                "max_spread_pct": MAX_SPREAD_PCT,
                "volume_z_threshold": VOLUME_Z_THRESHOLD,
                "stale_gap_sec": STALE_GAP_SEC,
                "min_abs_move": MIN_ABS_MOVE,
                "review_interval_sec": REVIEW_INTERVAL_SEC,
                "review_min_trades": REVIEW_MIN_TRADES,
                "breakout_required": BREAKOUT_REQUIRED,
                "breakout_window": BREAKOUT_WINDOW,
                "volume_ratio_threshold": VOLUME_RATIO_THRESHOLD,
            },
            "trades": closed_trades,
            "elapsed_sec": round(time.time() - start_time),
            "scan_count": scan_count,
        }, f, ensure_ascii=False, indent=2)
    csv_path = os.path.join(SAVE_DIR, f"{prefix}.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "time", "market", "group", "entry_price", "exit_price",
            "pnl", "net_pnl", "mae", "peak_pnl", "hold_sec", "reason",
            "z_score", "vol_z", "vol_ratio", "abs_move", "spread_pct", "ask_krw", "bid_krw", "slip_buy"
        ])
        writer.writeheader()
        writer.writerows(closed_trades)
    log(f"저장: {json_path}")
    return json_path

def fmt_krw_g(v):
    if v >= 100_000_000: return f"{v/100_000_000:.1f}억"
    return f"{v/10_000:.0f}만"

def analyze_buckets():
    """bucket별 통계 분석"""
    if len(closed_trades) < 3:
        return "데이터 부족 (3건 미만)"
    def bucket_stats(trades, key, boundaries):
        lines = []
        for i, (lo, hi, label) in enumerate(boundaries):
            subset = [t for t in trades if lo <= t.get(key, 0) < hi]
            if not subset:
                lines.append(f"  {label}: -")
                continue
            n = len(subset)
            w = sum(1 for t in subset if t["net_pnl"] > 0)
            avg = sum(t["net_pnl"] for t in subset) / n
            avg_mae = sum(t["mae"] for t in subset) / n
            lines.append(f"  {label}: {n}건 wr{w/n*100:.0f}% avg{avg:+.3f}% mae{avg_mae:+.3f}%")
        return "\n".join(lines)
    sections = []
    sections.append("▶ spread별:")
    sections.append(bucket_stats(closed_trades, "spread_pct", [
        (0, 0.1, "0~0.1%"), (0.1, 0.2, "0.1~0.2%"), (0.2, 0.5, "0.2~0.5%"), (0.5, 99, "0.5%+"),
    ]))
    sections.append("▶ bid depth별:")
    sections.append(bucket_stats(closed_trades, "bid_krw", [
        (0, 5_000_000, "<500만"), (5_000_000, 10_000_000, "500~1000만"),
        (10_000_000, 50_000_000, "1000~5000만"), (50_000_000, 9e18, "5000만+"),
    ]))
    sections.append("▶ z-score별:")
    sections.append(bucket_stats(closed_trades, "z_score", [
        (3, 5, "3~5"), (5, 10, "5~10"), (10, 20, "10~20"), (20, 9e18, "20+"),
    ]))
    sections.append("▶ 거래대금z별:")
    sections.append(bucket_stats(closed_trades, "vol_z", [
        (-9e18, 2, "<2"), (2, 5, "2~5"), (5, 10, "5~10"), (10, 9e18, "10+"),
    ]))
    sections.append("▶ 거래대금ratio별:")
    sections.append(bucket_stats(closed_trades, "vol_ratio", [
        (0, 2, "<2"), (2, 5, "2~5"), (5, 10, "5~10"), (10, 9e18, "10+"),
    ]))
    sections.append("▶ 그룹별:")
    for grp in ["상위", "하위"]:
        subset = [t for t in closed_trades if t["group"] == grp]
        if not subset:
            sections.append(f"  {grp}: -")
            continue
        n = len(subset)
        w = sum(1 for t in subset if t["net_pnl"] > 0)
        avg = sum(t["net_pnl"] for t in subset) / n
        avg_mae = sum(t["mae"] for t in subset) / n
        sections.append(f"  {grp}: {n}건 wr{w/n*100:.0f}% avg{avg:+.3f}% mae{avg_mae:+.3f}%")
    return "\n".join(sections)

# ═══════════════════════════════════════════════
# [F] 주기적 회고 + 개선점 추천
# ═══════════════════════════════════════════════
def generate_review():
    """누적 closed_trades 전체 통계 + 파라미터 조정 추천."""
    n = len(closed_trades)
    if n < REVIEW_MIN_TRADES:
        return f"📋 모멘텀 회고 (스킵): 거래 {n}건, 최소 {REVIEW_MIN_TRADES}건 필요"

    wins = sum(1 for t in closed_trades if t["net_pnl"] > 0)
    losses = sum(1 for t in closed_trades if t["net_pnl"] < 0)
    even = n - wins - losses
    wr = wins / n * 100
    avg = sum(t["net_pnl"] for t in closed_trades) / n
    total = sum(t["net_pnl"] for t in closed_trades)
    best_t = max(closed_trades, key=lambda t: t["net_pnl"])
    worst_t = min(closed_trades, key=lambda t: t["net_pnl"])
    pf_win = sum(t["net_pnl"] for t in closed_trades if t["net_pnl"] > 0)
    pf_loss = -sum(t["net_pnl"] for t in closed_trades if t["net_pnl"] < 0)
    pf = (pf_win / pf_loss) if pf_loss > 0 else float('inf') if pf_win > 0 else 0

    reason_stats = defaultdict(lambda: [0, 0.0])
    for t in closed_trades:
        r = t["reason"].split("(")[0]
        reason_stats[r][0] += 1
        reason_stats[r][1] += t["net_pnl"]
    target_r = reason_stats["target"][0] / n
    trail_r = reason_stats["trail"][0] / n
    timeout_r = reason_stats["timeout"][0] / n

    mfes = [t["peak_pnl"] for t in closed_trades]
    maes = [t["mae"] for t in closed_trades]
    holds = [t["hold_sec"] for t in closed_trades]

    grp_stats = {}
    for grp in ["상위", "하위"]:
        sub = [t for t in closed_trades if t["group"] == grp]
        if sub:
            ns = len(sub)
            ws = sum(1 for t in sub if t["net_pnl"] > 0)
            grp_stats[grp] = (ns, ws, sum(t["net_pnl"] for t in sub) / ns)

    by_market = defaultdict(lambda: [0, 0.0])
    for t in closed_trades:
        by_market[t["market"]][0] += 1
        by_market[t["market"]][1] += t["net_pnl"]
    sorted_mkts = sorted(by_market.items(), key=lambda x: x[1][1], reverse=True)
    top3 = sorted_mkts[:3]
    worst3 = sorted_mkts[-3:][::-1] if len(sorted_mkts) > 3 else []

    suggestions = []
    if timeout_r > 0.5:
        suggestions.append(f"timeout {timeout_r:.0%} 과다 → 신호가 모멘텀 아닐 가능성. "
                           f"ANOMALY_THRESHOLD 또는 VOLUME_Z_THRESHOLD 상향 검토")
    if trail_r > 0.5 and avg < 0:
        suggestions.append(f"trail {trail_r:.0%} 과다·avg 음수 → 신호 즉시 retrace. "
                           f"MIN_ABS_MOVE 0.15→0.20 또는 MAX_SPREAD_PCT 더 조이기 검토")
    if target_r >= 0.35 and avg > 0:
        suggestions.append(f"target {target_r:.0%} 우세·avg 양수 → 에지 확인됨. "
                           f"TARGET_PROFIT_PCT 상향 시험 또는 진입 빈도 늘리기 가능")

    def split_check(key, name, buckets, hint):
        stats = []
        for lo, hi, label in buckets:
            sub = [t for t in closed_trades if lo <= t.get(key, 0) < hi]
            if len(sub) >= 10:
                a = sum(t["net_pnl"] for t in sub) / len(sub)
                stats.append((label, len(sub), a))
        loss = [(l, n_, a) for l, n_, a in stats if a < -0.05]
        win = [(l, n_, a) for l, n_, a in stats if a > +0.05]
        if loss and win:
            ls = ", ".join(f"{l}({a:+.2f}%)" for l, _, a in loss)
            ws = ", ".join(f"{l}({a:+.2f}%)" for l, _, a in win)
            return f"{name}: 손실[{ls}] vs 수익[{ws}] → {hint}"
        return None

    for s in [
        split_check("spread_pct", "spread",
            [(0,0.05,"0~0.05"),(0.05,0.10,"0.05~0.10"),(0.10,0.20,"0.10~0.20")],
            "MAX_SPREAD_PCT 조정"),
        split_check("z_score", "price z",
            [(3,5,"3~5"),(5,10,"5~10"),(10,99,"10+")],
            "ANOMALY_THRESHOLD 조정"),
        split_check("vol_z", "vol z",
            [(-99,2,"<2"),(2,5,"2~5"),(5,99,"5+")],
            "VOLUME_Z_THRESHOLD 조정"),
        split_check("vol_ratio", "vol ratio",
            [(0,2,"<2"),(2,5,"2~5"),(5,99,"5+")],
            "VOLUME_RATIO_THRESHOLD 활성 검토"),
        split_check("bid_krw", "bid depth",
            [(0,5e6,"<500만"),(5e6,2e7,"500~2000만"),(2e7,9e18,"2000만+")],
            "MIN_BID_KRW 조정"),
    ]:
        if s: suggestions.append(s)
    for grp, (ns, ws, sub_avg) in grp_stats.items():
        if ns >= 20:
            sub_wr = ws / ns * 100
            if sub_avg < -0.05:
                suggestions.append(f"{grp} 그룹 {ns}건 avg{sub_avg:+.3f}% wr{sub_wr:.0f}% → 제외 검토")
            elif sub_avg > +0.10:
                suggestions.append(f"{grp} 그룹 {ns}건 avg{sub_avg:+.3f}% wr{sub_wr:.0f}% → 집중 검토")

    elapsed_hr = (time.time() - start_time) / 3600
    pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"
    lines = [
        f"📋 모멘텀 회고 ({elapsed_hr:.1f}h, scan{scan_count})",
        f"━━━━━━━━━━━━━━━",
        f"[승패] {n}전 {wins}승 {losses}패 {even}브레이크 wr{wr:.0f}% PF{pf_str}",
        f"[손익] avg{avg:+.3f}% sum{total:+.2f}% best{best_t['net_pnl']:+.3f}% worst{worst_t['net_pnl']:+.3f}%",
        f"        best {best_t['market'].replace('KRW-','')} / worst {worst_t['market'].replace('KRW-','')}",
        f"",
        f"[청산사유]",
    ]
    for r in ["target", "trail", "timeout"]:
        c, s = reason_stats.get(r, [0, 0.0])
        if c > 0:
            lines.append(f"  {r:8s} {c:3d}회 ({c/n*100:3.0f}%) avg{s/c:+.3f}%")
    lines.append("")
    lines.append("[그룹]")
    for grp in ["상위", "하위"]:
        if grp in grp_stats:
            ns, ws, ag = grp_stats[grp]
            lines.append(f"  {grp} {ns:3d}전 {ws:3d}승 wr{ws/ns*100:3.0f}% avg{ag:+.3f}%")
    lines += [
        "",
        "[리스크]",
        f"  MFE avg{sum(mfes)/n:+.3f}% / max{max(mfes):+.3f}%",
        f"  MAE avg{sum(maes)/n:+.3f}% / min{min(maes):+.3f}%",
        f"  보유 avg{sum(holds)/n:.0f}s / max{max(holds):.0f}s",
        "",
        "[종목 TOP3]",
    ]
    for m, (c, s) in top3:
        lines.append(f"  {m.replace('KRW-',''):10s} {c}회 sum{s:+.3f}% avg{s/c:+.3f}%")
    if worst3 and worst3[0][1][1] < 0:
        lines.append("[종목 WORST3]")
        for m, (c, s) in worst3:
            lines.append(f"  {m.replace('KRW-',''):10s} {c}회 sum{s:+.3f}% avg{s/c:+.3f}%")
    lines += [
        "",
        "━━━━━━━━━━━━━━━",
        "▶ 추천:",
    ]
    if suggestions:
        for s in suggestions:
            lines.append(f"• {s}")
    else:
        lines.append("• 명확한 개선점 없음 (현 설정 유지·데이터 추가 수집)")
    lines += [
        "",
        "━━━━━━━━━━━━━━━",
        "▶ Bucket 상세:",
        analyze_buckets(),
    ]
    return "\n".join(lines)

# ═══════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════
def main():
    global scan_count, start_time, log_fh
    start_time = time.time()
    log_fh = open(LOG_FILE, "a")
    print("=" * 60)
    print("업비트 모멘텀 스캐너 — Paper Trading")
    print(f"감지: z≥{ANOMALY_THRESHOLD} & 거래대금z≥{VOLUME_Z_THRESHOLD} & 등락≥{MIN_ABS_MOVE}%")
    print(f"청산: target +{TARGET_PROFIT_PCT}% / trail -{TRAILING_STOP_PCT}% / timeout {MAX_HOLD_SEC}s")
    print(f"필터: spread≤{MAX_SPREAD_PCT}% / ask≥{MIN_ASK_KRW:,} / bid≥{MIN_BID_KRW:,}")
    print(f"breakout: {'ON' if BREAKOUT_REQUIRED else 'OFF'} (직전{BREAKOUT_WINDOW}틱 고점 돌파) / vol_ratio gate: {VOLUME_RATIO_THRESHOLD}")
    print(f"모니터: 상위 {TOP_N}개 (하위 그룹 제거)")
    print(f"텔레그램: {'ON' if TG_ENABLED else 'OFF'} ({len(CHAT_IDS)}채널)")
    print("=" * 60)

    markets = get_all_krw_markets()
    log(f"전체 KRW 마켓: {len(markets)}개")
    tickers = get_tickers(markets)
    top_markets, bottom_markets = classify_markets(tickers)
    monitor_markets = top_markets | bottom_markets
    monitor_list = sorted(monitor_markets)
    log(f"모니터: {len(monitor_list)}개 (상위{len(top_markets)}"
        + (f" + 하위{len(bottom_markets)}" if bottom_markets else "") + ")")
    log(f"상위: {', '.join(m.replace('KRW-','') for m in sorted(top_markets))}")
    if bottom_markets:
        log(f"하위: {', '.join(m.replace('KRW-','') for m in sorted(bottom_markets))}")

    if TG_ENABLED:
        tg_send(
            f"📊 모멘텀 스캐너 시작\n"
            f"모니터: 상위{len(top_markets)}개"
            + (f" + 하위{len(bottom_markets)}" if bottom_markets else "") + "\n"
            f"감지: z≥{ANOMALY_THRESHOLD} & 거래대금z≥{VOLUME_Z_THRESHOLD} & 등락≥{MIN_ABS_MOVE}%\n"
            f"필터: spread≤{MAX_SPREAD_PCT}%\n"
            f"청산: +{TARGET_PROFIT_PCT}% / -{TRAILING_STOP_PCT}% / {MAX_HOLD_SEC}s"
        )

    last_reclassify = time.time()
    last_summary_tg = time.time()
    last_autosave = time.time()
    last_review = time.time()
    print("-" * 60)

    try:
        while True:
            scan_count += 1
            now_ts = time.time()
            try:
                fetch_list = sorted(set(monitor_list) | set(positions.keys()))
                tickers = get_tickers(fetch_list)
            except Exception as e:
                log(f"⚠ ticker 실패: {e}")
                time.sleep(2)
                continue
            tickers_dict = {t["market"]: t for t in tickers}

            manage_positions(tickers_dict, now_ts)

            for ticker in tickers:
                market = ticker["market"]
                price = ticker["trade_price"]
                if market in positions:
                    continue
                if market in cooldowns and now_ts - cooldowns[market] < COOLDOWN_SEC:
                    continue

                signal = detect_anomaly(market, price, now_ts)
                vol_z, vol_ratio = volume_stats(market, ticker.get("acc_trade_price_24h", 0), now_ts)
                if not signal:
                    continue
                if VOLUME_Z_THRESHOLD > 0 and (vol_z is None or vol_z < VOLUME_Z_THRESHOLD):
                    continue
                if VOLUME_RATIO_THRESHOLD > 0 and (vol_ratio is None or vol_ratio < VOLUME_RATIO_THRESHOLD):
                    continue
                vol_z_val = vol_z if vol_z is not None else 0
                vol_ratio_val = vol_ratio if vol_ratio is not None else 0

                ob = get_orderbook(market)
                if not ob:
                    continue
                ask = ob["ask"]
                bid = ob["bid"]
                ask_krw = ask * ob["ask_size"]
                bid_krw = bid * ob["bid_size"]
                spread_pct = (ask - bid) / bid * 100
                if ask_krw < MIN_ASK_KRW or bid_krw < MIN_BID_KRW:
                    continue
                if spread_pct > MAX_SPREAD_PCT:
                    continue
                if signal["abs_move"] < MIN_ABS_MOVE:
                    continue

                group = "상위" if market in top_markets else "하위"
                chg_rate = ticker.get("signed_change_rate", 0) * 100
                entry_price = ask
                slip_buy = (ask - price) / price * 100
                positions[market] = {
                    "entry_price": entry_price,
                    "entry_time": now_ts,
                    "peak_price": entry_price,
                    "worst_price": entry_price,
                    "group": group,
                    "z_score": signal["z_score"],
                    "vol_z": vol_z_val,
                    "vol_ratio": vol_ratio_val,
                    "abs_move": signal["abs_move"],
                    "spread_pct": round(spread_pct, 4),
                    "ask_krw": round(ask_krw),
                    "bid_krw": round(bid_krw),
                    "slip_buy": round(slip_buy, 4),
                }
                coin = market.replace("KRW-", "")
                target_price = entry_price * (1 + TARGET_PROFIT_PCT / 100)
                fmt_krw = fmt_krw_g
                msg = (
                    f"🔥 ENTRY {coin} [{group}]\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"현재가: {price:,.0f}원 ({chg_rate:+.2f}%)\n"
                    f"매수(ask): {ask:,.0f}원 ({slip_buy:+.3f}%) depth {fmt_krw(ask_krw)}\n"
                    f"매도(bid): {bid:,.0f}원 depth {fmt_krw(bid_krw)}\n"
                    f"스프레드: {spread_pct:.3f}%\n"
                    f"순간등락: {signal['abs_move']:+.3f}% (평소의 {signal['z_score']}배)\n"
                    f"거래대금z:{vol_z_val} ratio:{vol_ratio_val}\n"
                    f"목표: {target_price:,.0f}원 (+{TARGET_PROFIT_PCT}%)\n"
                    f"스탑: trail -{TRAILING_STOP_PCT}% / {MAX_HOLD_SEC}s"
                )
                log(msg)
                tg_send(f"📊모멘텀\n{msg}")

            if scan_count % 60 == 0:
                print_summary()
            if TG_ENABLED and now_ts - last_summary_tg > 600:
                tg_send(get_summary_text())
                last_summary_tg = now_ts
            if now_ts - last_autosave > 1800:
                save_results("auto")
                last_autosave = now_ts
            if REVIEW_INTERVAL_SEC > 0 and now_ts - last_review > REVIEW_INTERVAL_SEC:
                review = generate_review()
                log(review)
                if TG_ENABLED:
                    if len(review) > 3800:
                        parts, cur = [], ""
                        for ln in review.split("\n"):
                            if len(cur) + len(ln) + 1 > 3800:
                                parts.append(cur); cur = ln
                            else:
                                cur = (cur + "\n" + ln) if cur else ln
                        if cur: parts.append(cur)
                        for p in parts:
                            tg_send(p)
                    else:
                        tg_send(review)
                try:
                    with open(REVIEW_LOG, "a") as rf:
                        rf.write(f"\n=== {datetime.now(KST).isoformat()} "
                                 f"(scan {scan_count}, trades {len(closed_trades)}) ===\n"
                                 f"{review}\n")
                except Exception as e:
                    log(f"⚠ review 저장 실패: {e}")
                last_review = now_ts
            if now_ts - last_reclassify > 300:
                try:
                    all_tickers = get_tickers(markets)
                    top_markets, bottom_markets = classify_markets(all_tickers)
                    monitor_markets = top_markets | bottom_markets
                    monitor_list = sorted(monitor_markets)
                    last_reclassify = now_ts
                except Exception:
                    pass

            time.sleep(SCAN_INTERVAL)
    except (KeyboardInterrupt, SystemExit):
        log("스캐너 종료")
        print("\n" + "=" * 60)
        print("최종 결과")
        print("=" * 60)
        print_summary()
        analysis = analyze_buckets()
        print(f"\n{analysis}")
        save_results("final")
        if TG_ENABLED:
            tg_send(f"📊 모멘텀 스캐너 종료\n{get_summary_text()}")
            if len(closed_trades) >= 3:
                tg_send(f"📊 Bucket 분석\n{analysis}")
        if log_fh:
            log_fh.close()

if __name__ == "__main__":
    main()
