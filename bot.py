#!/usr/bin/env python3
"""
=============================================================================
MASSIVE UPBIT BACKTEST v2.0
=============================================================================
Comprehensive backtesting for the Upbit momentum trading bot.

Tests across:
- Top 30 KRW coins by 24h volume
- 4 timeframes: 1min, 3min, 5min, 15min
- Parameter grid search (2-phase optimization)
- Realistic fees + slippage

Strategy matches bot.py entry/exit logic.
=============================================================================
"""

import os
import requests
import time
import json
import sys
import math
import warnings
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from itertools import product

warnings.filterwarnings("ignore")

try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────
TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN") or ""
_raw = os.getenv("TG_CHATS") or os.getenv("TELEGRAM_CHAT_ID") or ""
CHAT_IDS = []
for p in _raw.split(","):
    p = p.strip()
    if p:
        try: CHAT_IDS.append(int(p))
        except: pass

_tg_last_send = 0.0

def tg(msg):
    global _tg_last_send
    print(msg)
    if not TG_TOKEN or not CHAT_IDS: return
    now = time.time()
    gap = now - _tg_last_send
    if gap < 1.0:
        time.sleep(1.0 - gap)
    chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)] if len(msg) > 4000 else [msg]
    for cid in CHAT_IDS:
        for chunk in chunks:
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={"chat_id": cid, "text": chunk, "disable_web_page_preview": True},
                    timeout=10)
                if r.status_code == 429:
                    retry = int(r.headers.get("Retry-After", 3))
                    time.sleep(retry)
                elif r.status_code != 200:
                    print(f"[TG경고] {r.status_code}: {r.text[:200]}")
            except Exception as e:
                print(f"[TG에러] {e}")
    _tg_last_send = time.time()

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
UPBIT_BASE = "https://api.upbit.com/v1"
RATE_LIMIT_SLEEP = 0.12          # 10 req/sec max
FEE_PER_SIDE = 0.0005            # 0.05%
SLIPPAGE_PER_SIDE = 0.0005       # 0.05%
TOTAL_COST_RT = (FEE_PER_SIDE + SLIPPAGE_PER_SIDE) * 2  # 0.2% roundtrip

CANDLES_PER_REQUEST = 200
TARGET_CANDLES = 2000
PAGES_NEEDED = TARGET_CANDLES // CANDLES_PER_REQUEST  # 10

# Timeframes and their max hold periods (in candles)
TIMEFRAMES = {
    "minutes/1":  {"label": "1min",  "max_hold": 60, "minutes": 1},
    "minutes/3":  {"label": "3min",  "max_hold": 30, "minutes": 3},
    "minutes/5":  {"label": "5min",  "max_hold": 20, "minutes": 5},
    "minutes/15": {"label": "15min", "max_hold": 10, "minutes": 15},
}

# KST timezone (UTC+9)
KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────────────────────────────────────────
# DATA COLLECTION
# ─────────────────────────────────────────────────────────────────────────────

def safe_request(url, params=None, retries=3):
    """Make API request with retry logic."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                tg(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                tg(f"  HTTP {resp.status_code} for {url}, attempt {attempt+1}")
                time.sleep(1)
        except Exception as e:
            tg(f"  Request error: {e}, attempt {attempt+1}")
            time.sleep(2 ** attempt)
    return None


def get_top_coins(n=30):
    """Get top N KRW market coins by 24h volume."""
    tg("📊 Fetching all KRW markets...")
    markets = safe_request(f"{UPBIT_BASE}/market/all")
    time.sleep(RATE_LIMIT_SLEEP)
    if not markets:
        tg("ERROR: Cannot fetch markets")
        return []

    krw_markets = [m["market"] for m in markets if m["market"].startswith("KRW-")]
    tg(f"  Found {len(krw_markets)} KRW markets")

    # Fetch tickers in batches of 30 (Upbit limit for ticker endpoint)
    all_tickers = []
    for i in range(0, len(krw_markets), 30):
        batch = krw_markets[i:i+30]
        market_str = ",".join(batch)
        tickers = safe_request(f"{UPBIT_BASE}/ticker", {"markets": market_str})
        time.sleep(RATE_LIMIT_SLEEP)
        if tickers:
            all_tickers.extend(tickers)

    # Sort by 24h accumulated trade price (KRW volume)
    all_tickers.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)

    top = []
    lines = []
    for t in all_tickers[:n]:
        vol_billion = t["acc_trade_price_24h"] / 1_000_000_000
        top.append({
            "market": t["market"],
            "price": t["trade_price"],
            "volume_24h": t["acc_trade_price_24h"],
            "change_rate": t.get("signed_change_rate", 0),
        })
        lines.append(f"{t['market']:12s} {t['trade_price']:>12,.0f}원 {vol_billion:>6.1f}B")

    tg(f"📊 Top {len(top)} coins:\n" + "\n".join(lines))
    return top


def fetch_candles(market, timeframe, target_count=2000):
    """Fetch candles with pagination. Returns list oldest→newest."""
    tf_info = TIMEFRAMES[timeframe]
    all_candles = []
    to_param = None

    pages = math.ceil(target_count / CANDLES_PER_REQUEST)

    for page in range(pages):
        params = {"market": market, "count": CANDLES_PER_REQUEST}
        if to_param:
            params["to"] = to_param

        url = f"{UPBIT_BASE}/candles/{timeframe}"
        data = safe_request(url, params)
        time.sleep(RATE_LIMIT_SLEEP)

        if not data or len(data) == 0:
            break

        all_candles.extend(data)
        # Set 'to' parameter for next page (oldest candle's timestamp)
        to_param = data[-1]["candle_date_time_utc"] + "Z"

        if len(data) < CANDLES_PER_REQUEST:
            break

    # Reverse to chronological order (oldest first)
    all_candles.reverse()

    # Remove duplicates by timestamp
    seen = set()
    unique = []
    for c in all_candles:
        key = c["candle_date_time_utc"]
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


def fetch_orderbook(markets):
    """Fetch current orderbook snapshots for spread analysis."""
    tg("\n📋 Fetching orderbook snapshots for spread analysis...")
    orderbooks = {}

    for i in range(0, len(markets), 10):
        batch = markets[i:i+10]
        market_str = ",".join(batch)
        data = safe_request(f"{UPBIT_BASE}/orderbook", {"markets": market_str})
        time.sleep(RATE_LIMIT_SLEEP)
        if data:
            for ob in data:
                m = ob["market"]
                units = ob.get("orderbook_units", [])
                if units:
                    best_ask = units[0]["ask_price"]
                    best_bid = units[0]["bid_price"]
                    ask_size = sum(u["ask_size"] for u in units[:5])
                    bid_size = sum(u["bid_size"] for u in units[:5])
                    spread = (best_ask - best_bid) / best_bid * 100
                    imbalance = (bid_size - ask_size) / (bid_size + ask_size) if (bid_size + ask_size) > 0 else 0
                    orderbooks[m] = {
                        "spread_pct": spread,
                        "imbalance": imbalance,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "bid_depth_5": bid_size,
                        "ask_depth_5": ask_size,
                    }

    return orderbooks


# ─────────────────────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────────────────────

def calc_ema(data, period):
    """Calculate EMA series."""
    if len(data) < period:
        return [None] * len(data)
    ema = [None] * (period - 1)
    sma = sum(data[:period]) / period
    ema.append(sma)
    multiplier = 2 / (period + 1)
    for i in range(period, len(data)):
        val = (data[i] - ema[-1]) * multiplier + ema[-1]
        ema.append(val)
    return ema


def calc_sma(data, period):
    """Calculate SMA series."""
    sma = [None] * (period - 1)
    for i in range(period - 1, len(data)):
        sma.append(sum(data[i - period + 1:i + 1]) / period)
    return sma


def calc_rsi(closes, period=14):
    """Calculate RSI series."""
    if len(closes) < period + 1:
        return [None] * len(closes)

    rsi = [None] * period
    gains = []
    losses = []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        rsi.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi.append(100 - 100 / (1 + rs))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi.append(100 - 100 / (1 + rs))

    return rsi


def calc_atr(highs, lows, closes, period=14):
    """Calculate ATR series."""
    if len(closes) < 2:
        return [None] * len(closes)

    tr_list = [highs[0] - lows[0]]  # first candle
    for i in range(1, len(closes)):
        h_l = highs[i] - lows[i]
        h_pc = abs(highs[i] - closes[i - 1])
        l_pc = abs(lows[i] - closes[i - 1])
        tr_list.append(max(h_l, h_pc, l_pc))

    atr = [None] * (period - 1)
    atr.append(sum(tr_list[:period]) / period)

    for i in range(period, len(tr_list)):
        val = (atr[-1] * (period - 1) + tr_list[i]) / period
        atr.append(val)

    return atr


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

def prepare_indicators(candles):
    """Pre-compute all indicators for a candle series."""
    closes = [c["trade_price"] for c in candles]
    highs = [c["high_price"] for c in candles]
    lows = [c["low_price"] for c in candles]
    volumes = [c["candle_acc_trade_volume"] for c in candles]
    opens = [c["opening_price"] for c in candles]

    ema20 = calc_ema(closes, 20)
    vol_ma20 = calc_sma(volumes, 20)
    rsi14 = calc_rsi(closes, 14)
    atr14 = calc_atr(highs, lows, closes, 14)

    indicators = []
    for i in range(len(candles)):
        indicators.append({
            "open": opens[i],
            "high": highs[i],
            "low": lows[i],
            "close": closes[i],
            "volume": volumes[i],
            "ema20": ema20[i],
            "vol_ma20": vol_ma20[i],
            "rsi": rsi14[i],
            "atr": atr14[i],
            "timestamp": candles[i]["candle_date_time_kst"],
        })

    return indicators


def check_entry(ind, prev_ind):
    """Check if entry conditions are met at candle index.
    Returns: 'momentum' | 'pullback' | False
    """
    if any(v is None for v in [ind["ema20"], ind["vol_ma20"], ind["rsi"], ind["atr"]]):
        return False

    # ── Momentum entry (기존 로직) ──
    is_momentum = True

    # 1. Price > EMA20
    if ind["close"] <= ind["ema20"]:
        is_momentum = False

    # 2. Volume > Volume_MA20 * 1.5
    if ind["vol_ma20"] <= 0 or ind["volume"] <= ind["vol_ma20"] * 1.5:
        is_momentum = False

    # 3. RSI between 40-70
    if is_momentum and (ind["rsi"] < 40 or ind["rsi"] > 70):
        is_momentum = False

    # 4. Positive candle body (close > open)
    if is_momentum and ind["close"] <= ind["open"]:
        is_momentum = False

    # 5. Previous candle was NOT a big red candle (close < open by >1%)
    if is_momentum and prev_ind and prev_ind["open"] > 0:
        prev_change = (prev_ind["close"] - prev_ind["open"]) / prev_ind["open"]
        if prev_change < -0.01:
            is_momentum = False

    if is_momentum:
        return "momentum"

    # ── Pullback entry (눌림목 매수) ──
    # 조건: EMA20 근처/아래 + 최근 상승추세(EMA20 기울기 상승) + RSI 과매도 아님
    ema20 = ind["ema20"]
    close = ind["close"]
    if ema20 is None or ema20 <= 0:
        return False

    ema_gap = (close - ema20) / ema20  # 음수 = EMA 아래

    # 가격이 EMA20 ± 0.5% 이내 (눌림 범위)
    if ema_gap > 0.005 or ema_gap < -0.015:
        return False

    # RSI 30-55 (과매도 아닌 눌림 구간)
    if ind["rsi"] < 30 or ind["rsi"] > 55:
        return False

    # 볼륨 최소 기준 (평균 이상)
    if ind["vol_ma20"] <= 0 or ind["volume"] <= ind["vol_ma20"] * 0.8:
        return False

    return "pullback"


def simulate_trade(indicators, entry_idx, max_hold, checkpoint_pct, trail_pct, sl_min_pct, sl_max_pct):
    """
    Simulate a single trade from entry_idx.
    Returns dict with trade result.
    """
    entry = indicators[entry_idx]
    entry_price = entry["close"]
    atr = entry["atr"]

    if entry_price <= 0 or atr is None or atr <= 0:
        return None

    # Dynamic stop-loss based on ATR
    atr_sl = (atr * 0.85 / entry_price)
    sl_pct = max(sl_min_pct, min(sl_max_pct, atr_sl))

    stop_loss_price = entry_price * (1 - sl_pct)
    checkpoint_price = entry_price * (1 + checkpoint_pct)

    trail_armed = False
    trail_high = entry_price
    trail_stop = 0

    best_price = entry_price
    worst_price = entry_price

    exit_reason = "timeout"
    exit_price = None
    exit_idx = None

    for j in range(entry_idx + 1, min(entry_idx + max_hold + 1, len(indicators))):
        bar = indicators[j]
        high = bar["high"]
        low = bar["low"]
        close = bar["close"]

        best_price = max(best_price, high)
        worst_price = min(worst_price, low)

        # Check stop-loss (using low of candle)
        if low <= stop_loss_price:
            exit_price = stop_loss_price
            exit_reason = "stop_loss"
            exit_idx = j
            break

        # Check checkpoint → arm trailing stop
        if high >= checkpoint_price and not trail_armed:
            trail_armed = True
            trail_high = high
            trail_stop = trail_high * (1 - trail_pct)

        # Update trailing stop
        if trail_armed:
            if high > trail_high:
                trail_high = high
                trail_stop = trail_high * (1 - trail_pct)

            # Check trailing stop hit
            if low <= trail_stop:
                exit_price = trail_stop
                exit_reason = "trail_stop"
                exit_idx = j
                break

    # Timeout exit
    if exit_price is None:
        last_idx = min(entry_idx + max_hold, len(indicators) - 1)
        exit_price = indicators[last_idx]["close"]
        exit_reason = "timeout"
        exit_idx = last_idx

    # Calculate PnL — proper round-trip cost model
    # gross = pure price movement (no costs)
    gross_pnl_pct = (exit_price / entry_price) - 1.0
    # net = slippage-adjusted execution + fees
    entry_exec = entry_price * (1 + SLIPPAGE_PER_SIDE)
    exit_exec = exit_price * (1 - SLIPPAGE_PER_SIDE)
    net_pnl_pct = (exit_exec / entry_exec) - 1.0 - (2 * FEE_PER_SIDE)
    mfe_pct = (best_price - entry_price) / entry_price
    mae_pct = (worst_price - entry_price) / entry_price

    # Extract hour (KST) for time analysis
    try:
        ts = entry["timestamp"]
        hour_kst = int(ts[11:13])
    except:
        hour_kst = -1

    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_pnl_pct": gross_pnl_pct,
        "net_pnl_pct": net_pnl_pct,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "exit_reason": exit_reason,
        "hold_candles": exit_idx - entry_idx if exit_idx else 0,
        "sl_pct": sl_pct,
        "trail_armed": trail_armed,
        "hour_kst": hour_kst,
        "entry_idx": entry_idx,
        "exit_idx": exit_idx,
    }


def run_backtest(all_data, checkpoint_pct, trail_pct, sl_min_pct, sl_max_pct):
    """
    Run backtest across all coins and timeframes.
    Returns aggregated results.
    """
    all_trades = []
    per_coin = defaultdict(list)
    per_tf = defaultdict(list)
    per_hour = defaultdict(list)

    for (market, tf_key), indicators in all_data.items():
        tf_info = TIMEFRAMES[tf_key]
        max_hold = tf_info["max_hold"]

        i = 21  # Start after indicators are valid (EMA20 needs 20 bars)
        while i < len(indicators) - max_hold:
            entry_type = check_entry(indicators[i], indicators[i - 1] if i > 0 else None)
            if entry_type:
                trade = simulate_trade(
                    indicators, i, max_hold,
                    checkpoint_pct, trail_pct, sl_min_pct, sl_max_pct
                )
                if trade:
                    trade["market"] = market
                    trade["timeframe"] = tf_info["label"]
                    trade["entry_type"] = entry_type
                    all_trades.append(trade)
                    per_coin[market].append(trade)
                    per_tf[tf_info["label"]].append(trade)
                    per_hour[trade["hour_kst"]].append(trade)

                    # Skip past exit candle (no overlapping trades)
                    i = trade["exit_idx"] + 1 if trade["exit_idx"] else i + max_hold
                    continue
            i += 1

    return all_trades, per_coin, per_tf, per_hour


def calc_stats(trades):
    """Calculate comprehensive statistics for a list of trades."""
    empty = {
        "count": 0, "win_rate": 0, "avg_pnl": 0, "total_pnl": 0,
        "avg_gross_pnl": 0, "total_gross_pnl": 0,
        "max_dd": 0, "profit_factor": 0, "avg_mfe": 0, "avg_mae": 0,
        "avg_hold": 0, "sl_exits": 0, "trail_exits": 0, "timeout_exits": 0,
        "exit_breakdown": {},
    }
    if not trades:
        return empty

    wins = [t for t in trades if t["net_pnl_pct"] > 0]
    losses = [t for t in trades if t["net_pnl_pct"] <= 0]

    total_pnl = sum(t["net_pnl_pct"] for t in trades)
    total_gross = sum(t["gross_pnl_pct"] for t in trades)
    gross_profit = sum(t["net_pnl_pct"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["net_pnl_pct"] for t in losses)) if losses else 0.001

    # Max drawdown (sequential)
    equity = 0
    peak = 0
    max_dd = 0
    for t in trades:
        equity += t["net_pnl_pct"]
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)

    # Per exit-reason breakdown
    exit_reasons = defaultdict(list)
    for t in trades:
        exit_reasons[t["exit_reason"]].append(t)

    exit_breakdown = {}
    for reason, rtrades in exit_reasons.items():
        r_net = sum(t["net_pnl_pct"] for t in rtrades)
        r_gross = sum(t["gross_pnl_pct"] for t in rtrades)
        exit_breakdown[reason] = {
            "count": len(rtrades),
            "pct": len(rtrades) / len(trades) * 100,
            "avg_net": r_net / len(rtrades) * 100,
            "total_net": r_net * 100,
            "avg_gross": r_gross / len(rtrades) * 100,
            "total_gross": r_gross * 100,
        }

    sl_exits = len(exit_reasons.get("stop_loss", []))
    trail_exits = len(exit_reasons.get("trail_stop", []))
    timeout_exits = len(exit_reasons.get("timeout", []))

    return {
        "count": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "avg_pnl": total_pnl / len(trades) * 100,
        "total_pnl": total_pnl * 100,
        "avg_gross_pnl": total_gross / len(trades) * 100,
        "total_gross_pnl": total_gross * 100,
        "max_dd": max_dd * 100,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 999,
        "avg_mfe": sum(t["mfe_pct"] for t in trades) / len(trades) * 100,
        "avg_mae": sum(t["mae_pct"] for t in trades) / len(trades) * 100,
        "avg_hold": sum(t["hold_candles"] for t in trades) / len(trades),
        "sl_exits": sl_exits,
        "trail_exits": trail_exits,
        "timeout_exits": timeout_exits,
        "exit_breakdown": exit_breakdown,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DISPLAY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def print_separator(char="=", width=100):
    tg(char * width)


def print_header(title):
    tg("")
    print_separator()
    tg(f"  {title}")
    print_separator()


def print_results_table(results, sort_key="total_pnl", top_n=None):
    """Print a formatted results table."""
    sorted_results = sorted(results, key=lambda x: x.get(sort_key, 0), reverse=True)
    if top_n:
        sorted_results = sorted_results[:top_n]

    lines = []
    lines.append(f"{'CP%':>6} {'Trail%':>7} {'SL_min%':>8} {'SL_max%':>8} | {'Trades':>7} {'WinR%':>7} {'AvgPnL%':>8} {'TotPnL%':>9} {'MaxDD%':>7} {'PF':>6}")
    lines.append("-" * 80)

    for r in sorted_results:
        s = r["stats"]
        lines.append(
            f"{r['cp']*100:>6.2f} {r['trail']*100:>7.2f} {r['sl_min']*100:>8.2f} {r['sl_max']*100:>8.2f} | "
            f"{s['count']:>7d} {s['win_rate']:>7.1f} {s['avg_pnl']:>8.3f} {s['total_pnl']:>9.2f} "
            f"{s['max_dd']:>7.2f} {s['profit_factor']:>6.2f}"
        )
    tg("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    start_time = time.time()

    tg(f"🚀 백테스트 시작\n{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}\n수수료 {FEE_PER_SIDE*100:.2f}%/편도 | 슬리피지 {SLIPPAGE_PER_SIDE*100:.2f}%/편도 | 왕복비용 {TOTAL_COST_RT*100:.2f}%")

    # ── Step 1: Get top coins ──
    top_coins = get_top_coins(30)
    if not top_coins:
        tg("❌ 코인 목록 조회 실패, 종료")
        sys.exit(1)

    markets = [c["market"] for c in top_coins]

    orderbooks = fetch_orderbook(markets)

    tg("📥 데이터 수집 시작...")

    all_data = {}
    total_candles = 0
    total_requests = 0

    coin_done = 0
    coin_total = len(top_coins) * len(TIMEFRAMES)
    for tf_key, tf_info in TIMEFRAMES.items():
        tg(f"\n⏰ Timeframe: {tf_info['label']}")
        for coin in top_coins:
            market = coin["market"]
            coin_done += 1

            candles = fetch_candles(market, tf_key, TARGET_CANDLES)
            total_requests += PAGES_NEEDED

            if candles and len(candles) >= 50:
                indicators = prepare_indicators(candles)
                all_data[(market, tf_key)] = indicators
                total_candles += len(candles)
                # 5개마다 진행 알림
                if coin_done % 5 == 0 or coin_done == coin_total:
                    elapsed = time.time() - start_time
                    tg(f"[수집 {coin_done}/{coin_total}] {market} {tf_info['label']} {len(candles)}개 | {elapsed:.0f}초 경과")
            else:
                tg(f"  SKIP {market} (only {len(candles) if candles else 0} candles)")

    elapsed_data = time.time() - start_time
    tg(f"✅ 수집 완료: {total_candles:,}개 캔들 | {len(all_data)}개 시리즈 | {elapsed_data:.0f}초 소요")

    tg("⚙️ Phase1: 체크포인트×트레일 최적화 시작 (SL 1.5%/2.5% 고정)")

    checkpoints = [0.0025, 0.0040, 0.0050, 0.0060, 0.0080, 0.0100]
    trails = [0.0015, 0.0025, 0.0035, 0.0050, 0.0070]
    fixed_sl_min = 0.015
    fixed_sl_max = 0.025

    phase1_results = []
    total_combos = len(checkpoints) * len(trails)
    combo_num = 0

    for cp in checkpoints:
        for tr in trails:
            combo_num += 1

            trades, _, _, _ = run_backtest(all_data, cp, tr, fixed_sl_min, fixed_sl_max)
            stats = calc_stats(trades)

            phase1_results.append({
                "cp": cp, "trail": tr,
                "sl_min": fixed_sl_min, "sl_max": fixed_sl_max,
                "stats": stats,
                "total_pnl": stats["total_pnl"],
            })
            if combo_num % 10 == 0 or combo_num == total_combos:
                tg(f"[Phase1] {combo_num}/{total_combos} 진행중...")

    tg(f"[Phase1] {total_combos}개 조합 완료")
    print_results_table(phase1_results, sort_key="total_pnl")

    # Find best CP/Trail
    best_p1 = max(phase1_results, key=lambda x: x["total_pnl"])
    best_cp = best_p1["cp"]
    best_trail = best_p1["trail"]

    tg(f"✅ Phase1 최적: CP={best_cp*100:.2f}% Trail={best_trail*100:.2f}%\n순익 {best_p1['stats']['total_pnl']:.2f}% | 승률 {best_p1['stats']['win_rate']:.1f}% | {best_p1['stats']['count']}건")

    tg(f"⚙️ Phase2: 손절 최적화 시작 (CP={best_cp*100:.2f}% Trail={best_trail*100:.2f}% 고정)")

    sl_mins = [0.008, 0.010, 0.012, 0.015, 0.020]
    sl_maxs = [0.015, 0.020, 0.025, 0.030, 0.035]

    phase2_results = []
    total_combos2 = len(sl_mins) * len(sl_maxs)
    combo_num = 0

    for sl_min in sl_mins:
        for sl_max in sl_maxs:
            if sl_max <= sl_min:
                continue
            combo_num += 1

            trades, _, _, _ = run_backtest(all_data, best_cp, best_trail, sl_min, sl_max)
            stats = calc_stats(trades)

            phase2_results.append({
                "cp": best_cp, "trail": best_trail,
                "sl_min": sl_min, "sl_max": sl_max,
                "stats": stats,
                "total_pnl": stats["total_pnl"],
            })
            if combo_num % 5 == 0:
                tg(f"[Phase2] {combo_num}/{total_combos2} 진행중...")

    tg(f"[Phase2] {combo_num}개 조합 완료")
    print_results_table(phase2_results, sort_key="total_pnl")

    # Find best overall
    best_p2 = max(phase2_results, key=lambda x: x["total_pnl"])
    best_sl_min = best_p2["sl_min"]
    best_sl_max = best_p2["sl_max"]

    # ── 최종 결과 ──
    final_trades, per_coin, per_tf, per_hour = run_backtest(
        all_data, best_cp, best_trail, best_sl_min, best_sl_max
    )
    final_stats = calc_stats(final_trades)

    # 청산 사유 분석
    eb = final_stats.get("exit_breakdown", {})
    exit_lines = []
    reason_kr = {"stop_loss": "손절", "trail_stop": "트레일", "timeout": "시간초과"}
    for reason in ["stop_loss", "trail_stop", "timeout"]:
        if reason in eb:
            r = eb[reason]
            exit_lines.append(f"  {reason_kr.get(reason, reason)}: {r['count']}건({r['pct']:.0f}%) 평균{r['avg_net']:+.4f}%")

    diagnosis = ""
    if final_stats["total_gross_pnl"] > 0 and final_stats["total_pnl"] < 0:
        diagnosis = "\n⚠️ 비용 제외시 수익이나 실질 손실 → 진입 빈도↓ 또는 수익폭↑ 필요"
    elif final_stats["total_gross_pnl"] < 0:
        diagnosis = "\n❌ 비용 제외해도 손실 → 전략 자체 재설계 필요"

    tg(f"""📊 최종 결과
━━━━━━━━━━━━━━━━━━━━
최적 파라미터:
  체크포인트: {best_cp*100:.2f}%
  트레일: {best_trail*100:.2f}%
  손절: {best_sl_min*100:.2f}%~{best_sl_max*100:.2f}%

성과:
  총 {final_stats['count']}건 | 승률 {final_stats['win_rate']:.1f}%
  순익(비용포함): 건당 {final_stats['avg_pnl']:+.4f}% / 합계 {final_stats['total_pnl']:+.2f}%
  총익(비용제외): 건당 {final_stats['avg_gross_pnl']:+.4f}% / 합계 {final_stats['total_gross_pnl']:+.2f}%
  비용영향: 건당 {final_stats['avg_gross_pnl'] - final_stats['avg_pnl']:+.4f}%
  최대낙폭: {final_stats['max_dd']:.2f}% | PF: {final_stats['profit_factor']:.2f}
  평균홀딩: {final_stats['avg_hold']:.1f}봉

청산사유:
{chr(10).join(exit_lines)}{diagnosis}""")

    # ── 진입 타입 비교 ──
    momentum_trades = [t for t in final_trades if t.get("entry_type") == "momentum"]
    pullback_trades = [t for t in final_trades if t.get("entry_type") == "pullback"]

    if momentum_trades or pullback_trades:
        lines = ["📈 진입타입 비교"]
        for label, trades_subset in [("모멘텀", momentum_trades), ("눌림목", pullback_trades)]:
            if trades_subset:
                s = calc_stats(trades_subset)
                lines.append(f"  {label}: {s['count']}건 승률{s['win_rate']:.0f}% 순익{s['avg_pnl']:+.4f}% PF{s['profit_factor']:.2f}")
            else:
                lines.append(f"  {label}: 없음")

        if momentum_trades and pullback_trades:
            m_s = calc_stats(momentum_trades)
            p_s = calc_stats(pullback_trades)
            diff = p_s["avg_gross_pnl"] - m_s["avg_gross_pnl"]
            if diff > 0:
                lines.append(f"→ 눌림목이 모멘텀보다 {diff:+.4f}% 우위")
            else:
                lines.append(f"→ 모멘텀이 눌림목보다 {-diff:+.4f}% 우위")
        tg("\n".join(lines))

    # ── Per-Coin Breakdown ──
    print_header("PER-COIN BREAKDOWN")

    coin_stats = []
    for market, trades in per_coin.items():
        s = calc_stats(trades)
        coin_stats.append((market, s))

    coin_stats.sort(key=lambda x: x[1]["total_pnl"], reverse=True)

    lines = [f"{'Market':>14s} | {'Trades':>7} {'WinR%':>7} {'AvgPnL%':>8} {'TotPnL%':>9} {'PF':>6}", "-" * 65]
    for market, s in coin_stats:
        lines.append(f"{market:>14s} | {s['count']:>7d} {s['win_rate']:>7.1f} {s['avg_pnl']:>8.3f} {s['total_pnl']:>9.2f} {s['profit_factor']:>6.2f}")
    lines.append(f"\n🏆 Top 5:")
    for market, s in coin_stats[:5]:
        lines.append(f"  {market}: PnL {s['total_pnl']:+.2f}% WR {s['win_rate']:.1f}%")
    lines.append(f"\n💀 Bottom 5:")
    for market, s in coin_stats[-5:]:
        lines.append(f"  {market}: PnL {s['total_pnl']:+.2f}% WR {s['win_rate']:.1f}%")
    tg("\n".join(lines))

    # ── Per-Timeframe Breakdown ──
    print_header("PER-TIMEFRAME BREAKDOWN")

    lines = [f"{'TF':>6s} | {'Trades':>7} {'WinR%':>7} {'AvgPnL%':>8} {'TotPnL%':>9} {'PF':>6}", "-" * 55]
    tf_ranking = []
    for tf_label in ["1min", "3min", "5min", "15min"]:
        trades = per_tf.get(tf_label, [])
        s = calc_stats(trades)
        tf_ranking.append((tf_label, s))
        lines.append(f"{tf_label:>6s} | {s['count']:>7d} {s['win_rate']:>7.1f} {s['avg_pnl']:>8.3f} {s['total_pnl']:>9.2f} {s['profit_factor']:>6.2f}")

    best_tf = max(tf_ranking, key=lambda x: x[1]["total_pnl"])
    lines.append(f"\n✅ Best: {best_tf[0]} (PnL: {best_tf[1]['total_pnl']:.2f}%)")
    tg("\n".join(lines))

    # ── Time-of-Day Analysis ──
    print_header("시간대별 분석 (KST)")

    lines = []
    hour_stats = []
    for hour in range(24):
        trades = per_hour.get(hour, [])
        if trades:
            s = calc_stats(trades)
            hour_stats.append((hour, s))
            marker = "🌙" if 0 <= hour < 7 else ("☀️" if 9 <= hour < 18 else "🌆")
            lines.append(f"{hour:02d}시{marker} {s['count']}건 승률{s['win_rate']:.0f}% 평균{s['avg_pnl']:+.3f}%")

    if hour_stats:
        best_hour = max(hour_stats, key=lambda x: x[1]["avg_pnl"])
        worst_hour = min(hour_stats, key=lambda x: x[1]["avg_pnl"])
        lines.append(f"\n✅ 최고: {best_hour[0]:02d}시 (평균 {best_hour[1]['avg_pnl']:+.3f}%)")
        lines.append(f"❌ 최악: {worst_hour[0]:02d}시 (평균 {worst_hour[1]['avg_pnl']:+.3f}%)")
    tg("\n".join(lines))

    # ── Orderbook Spread Analysis ──
    print_header("호가 스프레드 분석")

    if orderbooks:
        lines = []
        ob_items = sorted(orderbooks.items(), key=lambda x: x[1]["spread_pct"])
        for market, ob in ob_items:
            spread = ob["spread_pct"]
            verdict = "⚠️넓음" if spread > 0.30 else ("🟡보통" if spread > 0.15 else "✅좁음")
            lines.append(f"{market:12s} 스프레드{spread:.4f}% {verdict}")

        wide = [m for m, ob in ob_items if ob["spread_pct"] > 0.30]
        if wide:
            lines.append(f"\n⚠️ 스프레드 넓은 코인: {', '.join(wide)}")
            lines.append(f"→ 실매매 시 제외 권장")
        tg("\n".join(lines))

    # ── Holding Time Grid Experiment ──
    print_header("홀딩 시간 실험")

    hold_values = [5, 10, 15, 20, 30, 45, 60]
    lines = []
    hold_results = []
    for mh in hold_values:
        saved_max_holds = {}
        for tf_key in TIMEFRAMES:
            saved_max_holds[tf_key] = TIMEFRAMES[tf_key]["max_hold"]
            TIMEFRAMES[tf_key]["max_hold"] = mh

        h_trades, _, _, _ = run_backtest(all_data, best_cp, best_trail, best_sl_min, best_sl_max)
        h_stats = calc_stats(h_trades)
        hold_results.append((mh, h_stats))

        lines.append(f"{mh:>3d}봉 | {h_stats['count']}건 승률{h_stats['win_rate']:.0f}% 순익{h_stats['total_pnl']:+.1f}% PF{h_stats['profit_factor']:.2f}")

        for tf_key in TIMEFRAMES:
            TIMEFRAMES[tf_key]["max_hold"] = saved_max_holds[tf_key]

    best_hold = max(hold_results, key=lambda x: x[1]["total_pnl"])
    lines.append(f"\n✅ 최적 홀딩: {best_hold[0]}봉 (순익 {best_hold[1]['total_pnl']:+.2f}%)")
    tg("\n".join(lines))

    # ── Optimal Filter Combo Search ──
    print_header("필터 콤보 탐색")

    trade_features = []
    for t in final_trades:
        idx = t.get("entry_idx", 0)
        market = t.get("market", "")
        tf_key = None
        for tk, ti in TIMEFRAMES.items():
            if ti["label"] == t.get("timeframe", ""):
                tf_key = tk
                break
        if tf_key is None or (market, tf_key) not in all_data:
            continue

        inds = all_data[(market, tf_key)]
        if idx >= len(inds):
            continue
        ind = inds[idx]

        close = ind["close"]
        ema20 = ind["ema20"]
        rsi = ind["rsi"]
        vol = ind["volume"]
        vol_ma = ind["vol_ma20"]

        feat = {
            "ema_gap_pct": ((close - ema20) / ema20 * 100) if ema20 and ema20 > 0 else 0,
            "rsi": rsi if rsi else 50,
            "vol_ratio": (vol / vol_ma) if vol_ma and vol_ma > 0 else 1,
            "body_pct": ((close - ind["open"]) / ind["open"] * 100) if ind["open"] > 0 else 0,
            "candle_range_pct": ((ind["high"] - ind["low"]) / ind["low"] * 100) if ind["low"] > 0 else 0,
            "entry_type": t.get("entry_type", "momentum"),
        }
        trade_features.append((feat, t))

    if trade_features:
        filter_defs = {
            "ema_gap<=0.3": lambda f: f["ema_gap_pct"] <= 0.3,
            "ema_gap<=0.5": lambda f: f["ema_gap_pct"] <= 0.5,
            "ema_gap<=1.0": lambda f: f["ema_gap_pct"] <= 1.0,
            "rsi<=50": lambda f: f["rsi"] <= 50,
            "rsi<=55": lambda f: f["rsi"] <= 55,
            "rsi<=60": lambda f: f["rsi"] <= 60,
            "rsi45-55": lambda f: 45 <= f["rsi"] <= 55,
            "vol_ratio>=2.0": lambda f: f["vol_ratio"] >= 2.0,
            "vol_ratio>=2.5": lambda f: f["vol_ratio"] >= 2.5,
            "vol_ratio>=3.0": lambda f: f["vol_ratio"] >= 3.0,
            "body_pct<=0.3": lambda f: f["body_pct"] <= 0.3,
            "body_pct<=0.5": lambda f: f["body_pct"] <= 0.5,
            "range_pct<=1.0": lambda f: f["candle_range_pct"] <= 1.0,
            "is_pullback": lambda f: f.get("entry_type") == "pullback",
            "is_momentum": lambda f: f.get("entry_type") == "momentum",
        }

        # 단일 필터
        lines = ["[단일 필터]"]
        single_results = []
        for fname, ffunc in filter_defs.items():
            passed = [(f, t) for f, t in trade_features if ffunc(f)]
            if len(passed) >= 10:
                ptrades = [t for _, t in passed]
                s = calc_stats(ptrades)
                single_results.append((fname, s, len(passed)))
                lines.append(f"{fname:>20s} | {s['count']}건 승률{s['win_rate']:.0f}% 순익{s['avg_pnl']:+.4f}%")
        tg("\n".join(lines))

        # 2개 조합 필터
        filter_names = list(filter_defs.keys())
        combo_results = []
        for i in range(len(filter_names)):
            for j in range(i + 1, len(filter_names)):
                f1, f2 = filter_names[i], filter_names[j]
                fn1, fn2 = filter_defs[f1], filter_defs[f2]
                passed = [(f, t) for f, t in trade_features if fn1(f) and fn2(f)]
                if len(passed) >= 10:
                    ptrades = [t for _, t in passed]
                    s = calc_stats(ptrades)
                    combo_results.append((f"{f1} & {f2}", s, len(passed)))

        combo_results.sort(key=lambda x: x[1]["avg_gross_pnl"], reverse=True)

        if combo_results:
            lines = ["[Top 15 필터 조합]"]
            for combo_name, s, cnt in combo_results[:15]:
                star = "★" if s["avg_gross_pnl"] > 0 else ""
                lines.append(f"{star}{combo_name} | {s['count']}건 승률{s['win_rate']:.0f}% 순익{s['avg_pnl']:+.4f}%")

            net_profitable = [c for c in combo_results if c[1]["avg_pnl"] > 0]
            lines.append(f"\n순익>0 조합: {len(net_profitable)}/{len(combo_results)}개")
            if net_profitable:
                lines.append("\n✅ 수익나는 조합:")
                for name, s, cnt in net_profitable[:5]:
                    lines.append(f"  {name}: 승률{s['win_rate']:.0f}% 순익{s['avg_pnl']:+.4f}% ({cnt}건)")
            tg("\n".join(lines))
    else:
        tg("필터 분석할 트레이드 없음")

    # ── 최종 요약 ──
    elapsed_total = time.time() - start_time

    n = max(final_stats['count'], 1)
    profit_status = "✅ 순수익" if final_stats['total_pnl'] > 0 else "❌ 순손실"
    pf_status = "양호" if final_stats['profit_factor'] > 1.5 else "개선필요"

    lines = [f"""🏁 백테스트 완료
━━━━━━━━━━━━━━━━━━━━
소요시간: {elapsed_total:.0f}초 ({elapsed_total/60:.1f}분)
수집 캔들: {total_candles:,}개 | 시리즈: {len(all_data)}개
시뮬레이션: {final_stats['count']}건

💡 추천사항:
1. 최적 파라미터: CP {best_cp*100:.2f}% / Trail {best_trail*100:.2f}% / SL {best_sl_min*100:.2f}~{best_sl_max*100:.2f}%
2. 최적 타임프레임: {best_tf[0]}
3. 수익성: {profit_status} | PF {final_stats['profit_factor']:.2f} ({pf_status})
4. 청산비율: 손절 {final_stats['sl_exits']/n*100:.0f}% | 트레일 {final_stats['trail_exits']/n*100:.0f}% | 시간초과 {final_stats['timeout_exits']/n*100:.0f}%"""]

    if hour_stats:
        profitable_hours = [h for h, s in hour_stats if s["avg_pnl"] > 0]
        losing_hours = [h for h, s in hour_stats if s["avg_pnl"] < 0]
        if profitable_hours:
            lines.append(f"5. 수익시간대: {', '.join(f'{h:02d}시' for h in sorted(profitable_hours))}")
        if losing_hours:
            lines.append(f"   손실시간대: {', '.join(f'{h:02d}시' for h in sorted(losing_hours))}")

    tg("\n".join(lines))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tg(f"[백테스트 크래시] {e}\n{traceback.format_exc()[-1500:]}")
        sys.exit(1)