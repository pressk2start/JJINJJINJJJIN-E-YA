#!/usr/bin/env python3
"""
CLM_RAW Phase 1 — Multi-TF RSI 과열감지 Paper Trading
=====================================================
v2.1 SVE1 DNA를 대체하는 CLM 전략의 Phase 1 검증 봇.
v2.1과 동시 운영 가능 (별도 로그/별도 프로세스).

진입: clm_detector.detect_overheat() (5m+15m RSI + EMA spread)
청산: v2.1 그대로 (target/trail/timeout/early_dd)
코호트 A 예측: OFF (Phase 3 deferred)

Phase 1 목표: CLM 신호 자체가 v2.1 SVE1보다 나은지 50건 paper 비교
"""
import requests
import time
import json
import csv
import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from collections import deque, defaultdict

from clm_detector import detect_overheat, VOL_Z_LOOSE_MIN

KST = timezone(timedelta(hours=9))

def _get_git_info():
    try:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sha = subprocess.check_output(
            ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL).decode().strip()
        msg = subprocess.check_output(
            ["git", "-C", repo, "log", "-1", "--format=%s"],
            stderr=subprocess.DEVNULL).decode().strip()[:40]
        return sha, msg
    except Exception:
        return "unknown", ""

_GIT_SHA, _GIT_MSG = _get_git_info()
_BOOT_TIME = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

# ─── .env ───
def load_dotenv(path):
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

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
load_dotenv("/home/ubuntu/bot/.env")

# ─── 설정 (청산/유니버스는 v2.1 동일) ───
SCAN_INTERVAL = 1.0
CLM_SIGNAL_INTERVAL = 30.0
TRAILING_STOP_PCT = 0.25
TARGET_PROFIT_PCT = 0.25
MAX_HOLD_SEC = 180
COOLDOWN_SEC = 600
FEE_PCT = 0.05
MIN_ASK_KRW = 1_000_000
MIN_BID_KRW = 2_000_000
TOP_N = 30
MAX_SPREAD_PCT = 0.10
EARLY_EXIT_SEC = 30
EARLY_EXIT_ENTRY_PCT = 0.20
MAX_ENTRIES_PER_COIN = 3      # [J] 세션당 동일 종목 최대 진입 횟수 (SOL 6/13 집중 방지)
REVIEW_INTERVAL_SEC = 600
REVIEW_MIN_TRADES = 10
VIRTUAL_POSITION_KRW = 1_000_000
UNIVERSE_BLACKLIST = {
    "KRW-SUI", "KRW-NEAR", "KRW-BCH",
}

# ─── CLM 전용: Phase 1에서는 vol_z를 느슨한 sanity check으로만 사용 ───
# detect_overheat 내부에서 RSI+EMA 체크 → 여기서는 vol_z sanity만 추가
# VOL_Z_LOOSE_MIN은 clm_detector에서 import (1.0)

# ─── 저장 경로 (v2.1과 분리) ───
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LOG_DIR, "momentum_clm_live.log")
REVIEW_LOG = os.path.join(LOG_DIR, "momentum_clm_review.log")
SAVE_DIR = LOG_DIR

# ─── 텔레그램 ───
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
positions = {}
closed_trades = []
cooldowns = {}
post_exit_tracks = []
POST_EXIT_CHECKPOINTS = [30, 60, 120, 180]
blocked_signals = []
BLOCKED_MAX = 200
cooldown_block_count = 0
cooldown_episodes = 0
_active_cooldown_markets = set()
scan_count = 0
start_time = None
_ob_cache = {}
OB_CACHE_TTL = 1.5
OB_STALE_MAX_SEC = 5.0   # [M3 fix] API 실패 시 stale 호가 폴백 허용 상한(초)

# ─── [K] Regime feature snapshot (계측 전용, 전략 무영향) ───
regime_snapshot = {
    "btc_ret_24h": 0.0,
    "market_turnover": 0.0,
    "breadth_pos": 0.0,
    "btc_dominance": 0.0,
    "top5_conc": 0.0,
    "ts": 0.0,
}

def update_regime_snapshot(all_tickers):
    try:
        by_vol = sorted(all_tickers, key=lambda t: t.get("acc_trade_price_24h", 0), reverse=True)
        top30 = by_vol[:30]
        if not top30:
            return
        total_turnover = sum(t.get("acc_trade_price_24h", 0) for t in top30)
        top5_turnover = sum(t.get("acc_trade_price_24h", 0) for t in top30[:5])
        btc = next((t for t in all_tickers if t.get("market") == "KRW-BTC"), None)
        btc_turnover = btc.get("acc_trade_price_24h", 0) if btc else 0
        btc_ret = (btc.get("signed_change_rate", 0) * 100) if btc else 0
        n_pos = sum(1 for t in top30 if t.get("signed_change_rate", 0) > 0)
        regime_snapshot.update({
            "btc_ret_24h": round(btc_ret, 3),
            "market_turnover": round(total_turnover),
            "breadth_pos": round(n_pos / len(top30), 3),
            "btc_dominance": round(btc_turnover / total_turnover, 3) if total_turnover else 0,
            "top5_conc": round(top5_turnover / total_turnover, 3) if total_turnover else 0,
            "ts": time.time(),
        })
    except Exception as e:
        log(f"⚠ regime snapshot 실패: {e}")
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
# 업비트 API (v2.1 동일)
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

def compute_pump_1m(market, entry_ts_epoch):
    """
    [L] 진입 순간 직전 완성 1분봉 상승률(%) — 계측 전용, 전략 무영향.

    정의: A/B 스크립트와 동일.
      entry_ts_epoch 이전 1분(완성된 봉) 의 (close - open) / open * 100
      → 미래정보 없음. 진입 순간 이미 확정된 1분봉만 사용.

    실패 시 None. 봇 로직에는 영향 없음 (계측만).
    """
    try:
        entry_dt = datetime.fromtimestamp(entry_ts_epoch, tz=timezone.utc).replace(second=0, microsecond=0)
        target = entry_dt - timedelta(minutes=1)
        to_iso = entry_dt.strftime("%Y-%m-%dT%H:%M:%S")
        resp = requests.get(
            "https://api.upbit.com/v1/candles/minutes/1",
            params={"market": market, "count": 3, "to": to_iso},
            timeout=3,
        )
        resp.raise_for_status()
        candles = resp.json()
        target_iso = target.strftime("%Y-%m-%dT%H:%M:%S")
        match = next((c for c in candles if c.get("candle_date_time_utc") == target_iso), None)
        if not match:
            older = [c for c in candles if c.get("candle_date_time_utc", "") <= target_iso]
            match = older[0] if older else None
        if not match:
            return None
        op = float(match.get("opening_price", 0))
        cl = float(match.get("trade_price", 0))
        if op <= 0:
            return None
        return round((cl - op) / op * 100, 3)
    except Exception:
        return None

def get_orderbook(market):
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
        # [M3 fix] 무기한 stale 호가 반환 금지 — 상한 이내만 재사용, 초과 시 None.
        if cached and now - cached["ts"] < OB_STALE_MAX_SEC:
            return cached["data"]
        return None

def classify_markets(tickers):
    tickers = [t for t in tickers if t["market"] not in UNIVERSE_BLACKLIST]
    sorted_by_vol = sorted(tickers, key=lambda t: t.get("acc_trade_price_24h", 0))
    top = [t["market"] for t in sorted_by_vol[-TOP_N:]]
    return set(top), set()

# ═══════════════════════════════════════════════
# 포지션 관리 (v2.1 청산로직 그대로)
# ═══════════════════════════════════════════════
def manage_positions(tickers_dict, now_ts):
    to_close = []
    for market, pos in positions.items():
        ob = get_orderbook(market)
        if not ob:
            # [M4 fix] 호가 없으면 마지막 체결가(≥bid)로 대체하지 않고 이 tick 스킵.
            #   못 팔 가격(trade_price)에 target/trail 청산이 잘못 기록되는 것 방지.
            continue
        exit_price = ob["bid"]
        hold_sec = now_ts - pos["entry_time"]
        if exit_price > pos["peak_price"]:
            pos["peak_price"] = exit_price
        if exit_price < pos["worst_price"]:
            pos["worst_price"] = exit_price
        drawdown = (pos["peak_price"] - exit_price) / pos["peak_price"] * 100
        pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100

        # [M1 fix] 진입 게이트 spread<=0.10 이라 기존 0.12/0.22 분기는 죽은 코드.
        #   → 게이트 안(0~0.10)으로 임계 재보정: clean 0.05 / dirty 0.08.
        trail_pct = TRAILING_STOP_PCT
        if pos["spread_pct"] <= 0.05 and pos["slip_buy"] <= 0.10:
            trail_pct = 0.35          # clean → loose
        elif pos["spread_pct"] >= 0.08 or pos["slip_buy"] >= 0.18:
            trail_pct = 0.18          # dirty → tight

        reason = None
        if pnl_pct >= TARGET_PROFIT_PCT:
            reason = f"target({pnl_pct:+.2f}%)"
        elif hold_sec <= EARLY_EXIT_SEC and pnl_pct <= -EARLY_EXIT_ENTRY_PCT:
            reason = f"early_dd({pnl_pct:+.2f}%@{hold_sec:.0f}s)"
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
                "rsi_5m": pos["rsi_5m"],
                "rsi_15m": pos["rsi_15m"],
                "ema_spread_pct": pos["ema_spread_pct"],
                "spread_pct": pos["spread_pct"],
                "ask_krw": pos["ask_krw"],
                "bid_krw": pos["bid_krw"],
                "slip_buy": pos["slip_buy"],
                "rg_btc_ret": pos.get("rg_btc_ret", 0),
                "rg_turnover": pos.get("rg_turnover", 0),
                "rg_breadth": pos.get("rg_breadth", 0),
                "rg_btc_dom": pos.get("rg_btc_dom", 0),
                "rg_top5": pos.get("rg_top5", 0),
                "pump_1m": pos.get("pump_1m"),
            })

    for trade in to_close:
        market = trade["market"]
        del positions[market]
        cooldowns[market] = now_ts
        closed_trades.append(trade)
        reason_key = trade["reason"].split("(")[0]
        if reason_key in ("trail", "timeout", "early_dd"):
            post_exit_tracks.append({
                "market": market,
                "exit_time": now_ts,
                "exit_price": trade["exit_price"],
                "entry_price": trade["entry_price"],
                "reason": reason_key,
                "target_price": trade["entry_price"] * (1 + TARGET_PROFIT_PCT / 100),
                "prices": {},
                "done": False,
            })
        emoji = "🟢" if trade["net_pnl"] > 0 else "🔴"
        coin = market.replace("KRW-", "")
        price_diff = trade["exit_price"] - trade["entry_price"]
        fee_pct_total = FEE_PCT * 2
        virtual_pnl = round(VIRTUAL_POSITION_KRW * trade["net_pnl"] / 100)
        virtual_fee = round(VIRTUAL_POSITION_KRW * fee_pct_total / 100)
        n_total = len(closed_trades)
        n_wins = sum(1 for t in closed_trades if t["net_pnl"] > 0)
        sum_pnl = sum(t["net_pnl"] for t in closed_trades)
        sum_krw = round(VIRTUAL_POSITION_KRW * sum_pnl / 100)
        wr = n_wins / n_total * 100 if n_total else 0
        msg = (
            f"{emoji} CLM EXIT {coin} [{trade['group']}]\n"
            f"━━━━━━━━━━━━━━━\n"
            f"진입  {trade['entry_price']:,.0f}원\n"
            f"청산  {trade['exit_price']:,.0f}원 ({price_diff:+,.0f})\n"
            f"손익  {trade['pnl']:+.3f}%\n"
            f"수수료 -{fee_pct_total:.3f}% ({virtual_fee:,}원)\n"
            f"순익  {trade['net_pnl']:+.3f}% ({virtual_pnl:+,}원/100만)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"보유 {trade['hold_sec']:.0f}초 | {trade['reason']}\n"
            f"고점{trade['peak_pnl']:+.3f}% 저점{trade['mae']:+.3f}%\n"
            f"rsi5m:{trade['rsi_5m']:.1f} rsi15m:{trade['rsi_15m']:.1f} ema_sp:{trade['ema_spread_pct']:.3f}%\n"
            f"━━━━━━━━━━━━━━━\n"
            f"누적 {n_total}전 {n_wins}승 wr{wr:.0f}% sum{sum_pnl:+.3f}% ({sum_krw:+,}원)"
        )
        log(msg)
        tg_send(f"📊CLM\n{msg}")

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
        return f"📊 CLM {elapsed/60:.0f}분 | 거래 없음{open_str}"
    wins = sum(1 for t in closed_trades if t["net_pnl"] > 0)
    wr = wins / n * 100
    avg_pnl = sum(t["net_pnl"] for t in closed_trades) / n
    total_pnl = sum(t["net_pnl"] for t in closed_trades)
    top_trades = [t for t in closed_trades if t["group"] == "상위"]
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
        f"📊 CLM {elapsed/60:.0f}분",
        f"총 {n}전{wins}승 wr{wr:.0f}% avg{avg_pnl:+.3f}% sum{total_pnl:+.2f}%",
        f"상위: {gs(top_trades)}",
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
    prefix = f"momentum_clm_{tag}_{ts_str}" if tag else f"momentum_clm_{ts_str}"
    json_path = os.path.join(SAVE_DIR, f"{prefix}.json")
    with open(json_path, "w") as f:
        json.dump({
            "strategy": "CLM_RAW_Phase1",
            "config": {
                "trailing_stop_pct": TRAILING_STOP_PCT,
                "target_profit_pct": TARGET_PROFIT_PCT,
                "fee_pct": FEE_PCT,
                "max_hold_sec": MAX_HOLD_SEC,
                "top_n": TOP_N,
                "scan_interval": SCAN_INTERVAL,
                "max_spread_pct": MAX_SPREAD_PCT,
                "early_exit_sec": EARLY_EXIT_SEC,
                "early_exit_entry_pct": EARLY_EXIT_ENTRY_PCT,
            },
            "trades": closed_trades,
            "elapsed_sec": round(time.time() - start_time),
            "scan_count": scan_count,
        }, f, ensure_ascii=False, indent=2)
    csv_path = os.path.join(SAVE_DIR, f"{prefix}.csv")
    csv_fields = [
        "time", "market", "group", "entry_price", "exit_price",
        "pnl", "net_pnl", "mae", "peak_pnl", "hold_sec", "reason",
        "rsi_5m", "rsi_15m", "ema_spread_pct",
        "spread_pct", "ask_krw", "bid_krw", "slip_buy",
        "rg_btc_ret", "rg_turnover", "rg_breadth", "rg_btc_dom", "rg_top5",
        "pump_1m",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for t in closed_trades:
            writer.writerow(t)
    log(f"저장: {json_path}")
    return json_path

def fmt_krw_g(v):
    if v >= 100_000_000:
        return f"{v/100_000_000:.1f}억"
    return f"{v/10_000:.0f}만"

# ═══════════════════════════════════════════════
# Bucket 분석 (CLM 적응)
# ═══════════════════════════════════════════════
def analyze_buckets():
    if len(closed_trades) < 3:
        return "데이터 부족 (3건 미만)"
    def bucket_stats(trades, key, boundaries):
        lines = []
        for lo, hi, label in boundaries:
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
    sections.append("▶ rsi_5m별:")
    sections.append(bucket_stats(closed_trades, "rsi_5m", [
        (70, 75, "70~75"), (75, 80, "75~80"), (80, 85, "80~85"), (85, 100, "85+"),
    ]))
    sections.append("▶ rsi_15m별:")
    sections.append(bucket_stats(closed_trades, "rsi_15m", [
        (65, 70, "65~70"), (70, 75, "70~75"), (75, 80, "75~80"), (80, 100, "80+"),
    ]))
    sections.append("▶ ema_spread별:")
    sections.append(bucket_stats(closed_trades, "ema_spread_pct", [
        (0.6, 1.0, "0.6~1.0%"), (1.0, 1.5, "1.0~1.5%"), (1.5, 2.0, "1.5~2.0%"),
        (2.0, 3.0, "2.0~3.0%"),
    ]))
    sections.append("▶ 보유시간별:")
    sections.append(bucket_stats(closed_trades, "hold_sec", [
        (0, 30, "<30s"), (30, 60, "30~60s"), (60, 120, "60~120s"),
        (120, 180, "120~180s"), (180, 9e18, "180s+"),
    ]))
    return "\n".join(sections)

def _analyze_post_exit():
    done = [t for t in post_exit_tracks if t["done"] and len(t["prices"]) >= 2]
    if len(done) < 3:
        return None
    by_reason = defaultdict(list)
    for t in done:
        by_reason[t["reason"]].append(t)
    lines = ["▶ 청산후 추적:"]
    for reason in ["trail", "early_dd", "timeout"]:
        tracks = by_reason.get(reason, [])
        if len(tracks) < 3:
            continue
        lines.append(f"  [{reason}] {len(tracks)}건")
        for cp in POST_EXIT_CHECKPOINTS:
            subset = [t for t in tracks if cp in t["prices"]]
            if not subset:
                continue
            changes = []
            target_hits = 0
            for t in subset:
                chg = (t["prices"][cp] - t["exit_price"]) / t["exit_price"] * 100
                changes.append(chg)
                if t["prices"][cp] >= t["target_price"]:
                    target_hits += 1
            avg_chg = sum(changes) / len(changes)
            pos_cnt = sum(1 for c in changes if c > 0)
            lines.append(
                f"    {cp:3d}초후 avg{avg_chg:+.3f}% "
                f"(↑{pos_cnt}/{len(subset)}) "
                f"target도달:{target_hits}/{len(subset)}"
            )
    return "\n".join(lines)

def _analyze_mfe_distribution():
    if len(closed_trades) < 5:
        return None
    n = len(closed_trades)
    mfes = [t["peak_pnl"] for t in closed_trades]
    thresholds = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    lines = ["▶ MFE 도달 분포:"]
    for th in thresholds:
        cnt = sum(1 for m in mfes if m >= th)
        lines.append(f"  +{th:.2f}% 도달: {cnt}/{n}건 ({cnt/n*100:.0f}%)")
    avg_mfe = sum(mfes) / n
    med_mfe = sorted(mfes)[n // 2]
    lines.append(f"  avg{avg_mfe:+.3f}% med{med_mfe:+.3f}%")
    return "\n".join(lines)

def _analyze_pump_1m_buckets():
    """[L] pump_1m 버킷별 성과 분해 — CLM 진입이 어느 급등 세기에 몰리는지."""
    trades_with_pump = [t for t in closed_trades if t.get("pump_1m") is not None]
    if len(trades_with_pump) < 3:
        return None
    buckets = [
        (-999, 1.0, "<1%"),
        (1.0, 2.0, "1~2%"),
        (2.0, 3.0, "2~3%"),
        (3.0, 4.0, "3~4%"),
        (4.0, 5.0, "4~5%"),
        (5.0, 999, "5%+"),
    ]
    lines = ["▶ pump_1m 버킷별 성과 (진입 직전 1분봉 상승률):"]
    for lo, hi, label in buckets:
        sub = [t for t in trades_with_pump if lo <= t["pump_1m"] < hi]
        if not sub:
            continue
        n = len(sub)
        wins = sum(1 for t in sub if t["net_pnl"] > 0)
        avg = sum(t["net_pnl"] for t in sub) / n
        lines.append(f"  {label:>6}: n={n:3d} wr{wins/n*100:.0f}% avg{avg:+.3f}%")
    pumps = [t["pump_1m"] for t in trades_with_pump]
    n = len(pumps)
    med = sorted(pumps)[n // 2]
    ge2 = sum(1 for p in pumps if p >= 2)
    ge3 = sum(1 for p in pumps if p >= 3)
    ge4 = sum(1 for p in pumps if p >= 4)
    lines.append(f"  분포: n={n} med{med:+.2f}% ≥2%:{ge2/n*100:.0f}% ≥3%:{ge3/n*100:.0f}% ≥4%:{ge4/n*100:.0f}%")
    return "\n".join(lines)

def _analyze_regime_features():
    """[K] 세션 regime feature 요약 + W/L 분리 (계측 전용)."""
    trades_with_rg = [t for t in closed_trades if "rg_btc_ret" in t]
    if len(trades_with_rg) < 3:
        return None
    def _avg(rows, key):
        vals = [r.get(key, 0) for r in rows]
        return sum(vals) / len(vals) if vals else 0
    win = [t for t in trades_with_rg if t["net_pnl"] > 0]
    loss = [t for t in trades_with_rg if t["net_pnl"] <= 0]
    lines = ["▶ Regime feature (진입시점 스냅샷 평균):"]
    lines.append(
        f"  BTC24h {_avg(trades_with_rg,'rg_btc_ret'):+.2f}% / "
        f"turnover {_avg(trades_with_rg,'rg_turnover')/1e12:.2f}조 / "
        f"breadth {_avg(trades_with_rg,'rg_breadth')*100:.0f}% / "
        f"BTC_dom {_avg(trades_with_rg,'rg_btc_dom')*100:.0f}% / "
        f"top5 {_avg(trades_with_rg,'rg_top5')*100:.0f}%"
    )
    if win and loss:
        lines.append(
            f"  W: BTC{_avg(win,'rg_btc_ret'):+.2f}% breadth{_avg(win,'rg_breadth')*100:.0f}% "
            f"dom{_avg(win,'rg_btc_dom')*100:.0f}% top5{_avg(win,'rg_top5')*100:.0f}%"
        )
        lines.append(
            f"  L: BTC{_avg(loss,'rg_btc_ret'):+.2f}% breadth{_avg(loss,'rg_breadth')*100:.0f}% "
            f"dom{_avg(loss,'rg_btc_dom')*100:.0f}% top5{_avg(loss,'rg_top5')*100:.0f}%"
        )
    return "\n".join(lines)

def _analyze_realized_mfe_buckets():
    """MFE 구간별 realized/MFE — 어디서 수익을 흘리는지 진단."""
    mfe_pos = [t for t in closed_trades if t["peak_pnl"] > 0]
    if len(mfe_pos) < 3:
        return None
    buckets = [
        (0.10, 0.30, "0.1~0.3%"),
        (0.30, 0.50, "0.3~0.5%"),
        (0.50, 1.00, "0.5~1%"),
        (1.00, 2.00, "1~2%"),
        (2.00, 100.0, "2%+"),
    ]
    total_pnl = sum(t["net_pnl"] for t in mfe_pos)
    total_mfe = sum(t["peak_pnl"] for t in mfe_pos)
    overall_rm = (total_pnl / total_mfe * 100) if total_mfe else 0
    parts = []
    for lo, hi, label in buckets:
        sub = [t for t in mfe_pos if lo <= t["peak_pnl"] < hi]
        if not sub:
            continue
        s_pnl = sum(t["net_pnl"] for t in sub)
        s_mfe = sum(t["peak_pnl"] for t in sub)
        rm = (s_pnl / s_mfe * 100) if s_mfe else 0
        parts.append(f"{label}:{len(sub)}건 r/m{rm:.0f}%")
    lines = [f"▶ realized/MFE: {overall_rm:.0f}%"]
    if parts:
        lines.append(f"▶ r/m구간: {' | '.join(parts)}")
    return "\n".join(lines)

def _analyze_blocked_signals():
    done = [b for b in blocked_signals if b["done"] and 180 in b.get("prices", {})]
    if len(done) < 3:
        return ""
    lines = [f"▶ 차단 신호 사후추적 ({len(done)}건, cooldown차단 {cooldown_block_count}회, 신호에피소드 {cooldown_episodes}회):"]
    blocker_stats = defaultdict(lambda: {"n": 0, "wins": 0, "pnl_sum": 0.0, "mfe_sum": 0.0})
    all_rsi5, all_rsi15, all_ema_sp = [], [], []
    for b in done:
        ask = b["ask"]
        peak = b.get("peak", ask)
        p180 = b["prices"][180]
        hypo_pnl = (p180 - ask) / ask * 100 - FEE_PCT * 2
        hypo_mfe = (peak - ask) / ask * 100
        all_rsi5.append(b.get("rsi_5m", 0))
        all_rsi15.append(b.get("rsi_15m", 0))
        all_ema_sp.append(b.get("ema_spread_pct", 0))
        for blocker in b["blockers"]:
            s = blocker_stats[blocker]
            s["n"] += 1
            s["pnl_sum"] += hypo_pnl
            s["mfe_sum"] += hypo_mfe
            if hypo_pnl > 0:
                s["wins"] += 1
    for blocker, s in sorted(blocker_stats.items(), key=lambda x: -x[1]["n"]):
        n = s["n"]
        wr = s["wins"] / n * 100
        avg_pnl = s["pnl_sum"] / n
        avg_mfe = s["mfe_sum"] / n
        verdict = "✅유효" if avg_pnl < -0.05 else "⚠재검토" if avg_pnl < 0.05 else "❌과잉차단"
        lines.append(f"  {blocker:10s} {n:3d}건 wr{wr:.0f}% avg{avg_pnl:+.3f}% mfe{avg_mfe:+.3f}% {verdict}")
    if all_rsi5:
        avg_r5 = sum(all_rsi5) / len(all_rsi5)
        avg_r15 = sum(all_rsi15) / len(all_rsi15)
        avg_sp = sum(all_ema_sp) / len(all_ema_sp)
        lines.append(f"  [차단 신호 품질] rsi5:{avg_r5:.1f} rsi15:{avg_r15:.1f} ema_sp:{avg_sp:.3f}%")
        if closed_trades:
            e_r5 = sum(t["rsi_5m"] for t in closed_trades) / len(closed_trades)
            e_r15 = sum(t["rsi_15m"] for t in closed_trades) / len(closed_trades)
            e_sp = sum(t["ema_spread_pct"] for t in closed_trades) / len(closed_trades)
            lines.append(f"  [진입 신호 품질] rsi5:{e_r5:.1f} rsi15:{e_r15:.1f} ema_sp:{e_sp:.3f}%")
    return "\n".join(lines)

# ═══════════════════════════════════════════════
# 회고 (CLM 적응)
# ═══════════════════════════════════════════════
def generate_review():
    n = len(closed_trades)
    if n < REVIEW_MIN_TRADES:
        return f"📋 CLM 회고 (스킵): 거래 {n}건, 최소 {REVIEW_MIN_TRADES}건 필요"

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

    mfes = [t["peak_pnl"] for t in closed_trades]
    maes = [t["mae"] for t in closed_trades]
    holds = [t["hold_sec"] for t in closed_trades]

    by_market = defaultdict(lambda: [0, 0.0])
    for t in closed_trades:
        by_market[t["market"]][0] += 1
        by_market[t["market"]][1] += t["net_pnl"]
    sorted_mkts = sorted(by_market.items(), key=lambda x: x[1][1], reverse=True)
    top3 = sorted_mkts[:3]
    worst3 = sorted_mkts[-3:][::-1] if len(sorted_mkts) > 3 else []

    win_trades = [t for t in closed_trades if t["net_pnl"] > 0]
    loss_trades = [t for t in closed_trades if t["net_pnl"] <= 0]
    win_mfes = [t["peak_pnl"] for t in win_trades]
    loss_mfes = [t["peak_pnl"] for t in loss_trades]

    elapsed_hr = (time.time() - start_time) / 3600
    pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"
    lines = [
        f"📋 CLM 회고 ({elapsed_hr:.1f}h, scan{scan_count})",
        f"━━━━━━━━━━━━━━━",
        f"[전략] CLM_RAW Phase 1 (multi-TF RSI + EMA spread)",
        f"[승패] {n}전 {wins}승 {losses}패 {even}브레이크 wr{wr:.0f}% PF{pf_str}",
        f"[손익] avg{avg:+.3f}% sum{total:+.2f}% best{best_t['net_pnl']:+.3f}% worst{worst_t['net_pnl']:+.3f}%",
        f"        best {best_t['market'].replace('KRW-','')} / worst {worst_t['market'].replace('KRW-','')}",
        f"",
        f"[청산사유]",
    ]
    for r in ["target", "trail", "early_dd", "timeout"]:
        c, s = reason_stats.get(r, [0, 0.0])
        if c > 0:
            lines.append(f"  {r:8s} {c:3d}회 ({c/n*100:3.0f}%) avg{s/c:+.3f}%")
    lines += [
        "",
        "[리스크]",
        f"  MFE avg{sum(mfes)/n:+.3f}% / max{max(mfes):+.3f}%",
    ]
    if win_mfes and loss_mfes:
        lines.append(
            f"  MFE W{sum(win_mfes)/len(win_mfes):+.3f}%({len(win_mfes)}건) "
            f"/ L{sum(loss_mfes)/len(loss_mfes):+.3f}%({len(loss_mfes)}건)")
    lines += [
        f"  MAE avg{sum(maes)/n:+.3f}% / min{min(maes):+.3f}%",
        f"  보유 avg{sum(holds)/n:.0f}s / max{max(holds):.0f}s",
    ]

    avg_rsi5 = sum(t["rsi_5m"] for t in closed_trades) / n
    avg_rsi15 = sum(t["rsi_15m"] for t in closed_trades) / n
    avg_ema_sp = sum(t["ema_spread_pct"] for t in closed_trades) / n
    lines += [
        "",
        "[신호 품질]",
        f"  RSI5   avg {avg_rsi5:.1f}",
        f"  RSI15  avg {avg_rsi15:.1f}",
        f"  EMA_SP avg {avg_ema_sp:.2f}%",
    ]

    if win_trades and loss_trades:
        w_rsi5 = sum(t["rsi_5m"] for t in win_trades) / len(win_trades)
        l_rsi5 = sum(t["rsi_5m"] for t in loss_trades) / len(loss_trades)
        w_rsi15 = sum(t["rsi_15m"] for t in win_trades) / len(win_trades)
        l_rsi15 = sum(t["rsi_15m"] for t in loss_trades) / len(loss_trades)
        w_sp = sum(t["ema_spread_pct"] for t in win_trades) / len(win_trades)
        l_sp = sum(t["ema_spread_pct"] for t in loss_trades) / len(loss_trades)
        lines += [
            "",
            "[진입품질 W/L]",
            f"  rsi5m  W{w_rsi5:.1f} / L{l_rsi5:.1f}",
            f"  rsi15m W{w_rsi15:.1f} / L{l_rsi15:.1f}",
            f"  ema_sp W{w_sp:.3f}% / L{l_sp:.3f}%",
        ]

    lines += ["", "[종목 TOP3]"]
    for m, (c, s) in top3:
        lines.append(f"  {m.replace('KRW-',''):10s} {c}회 sum{s:+.3f}% avg{s/c:+.3f}%")
    if worst3 and worst3[0][1][1] < 0:
        lines.append("[종목 WORST3]")
        for m, (c, s) in worst3:
            mt = [t for t in closed_trades if t["market"] == m]
            avg_rsi5 = sum(t["rsi_5m"] for t in mt) / len(mt)
            avg_sp = sum(t["ema_spread_pct"] for t in mt) / len(mt)
            lines.append(f"  {m.replace('KRW-',''):10s} {c}회 sum{s:+.3f}% avg{s/c:+.3f}% rsi5m:{avg_rsi5:.0f} sp:{avg_sp:.2f}%")

    most_traded = max(by_market.items(), key=lambda x: x[1][0]) if by_market else None
    if most_traded and most_traded[1][0] >= 3:
        mc, (mc_n, mc_pnl) = most_traded
        lines.append(f"[집중도] {mc.replace('KRW-','')} {mc_n}/{n}건 ({mc_n/n*100:.0f}%) sum{mc_pnl:+.3f}%")

    lines += [
        "",
        "━━━━━━━━━━━━━━━",
        "▶ Bucket 상세:",
        analyze_buckets(),
    ]
    pet = _analyze_post_exit()
    if pet:
        lines += ["", "━━━━━━━━━━━━━━━", pet]
    mfe_dist = _analyze_mfe_distribution()
    if mfe_dist:
        lines += ["", "━━━━━━━━━━━━━━━", mfe_dist]
    rm_buckets = _analyze_realized_mfe_buckets()
    if rm_buckets:
        lines += ["", rm_buckets]
    pump_buckets = _analyze_pump_1m_buckets()
    if pump_buckets:
        lines += ["", "━━━━━━━━━━━━━━━", pump_buckets]
    rg_features = _analyze_regime_features()
    if rg_features:
        lines += ["", "━━━━━━━━━━━━━━━", rg_features]
    blocked_analysis = _analyze_blocked_signals()
    if blocked_analysis:
        lines += ["", "━━━━━━━━━━━━━━━", blocked_analysis]
    return "\n".join(lines)

# ═══════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════
def main():
    global scan_count, start_time, log_fh, cooldown_block_count, cooldown_episodes
    start_time = time.time()
    log_fh = open(LOG_FILE, "a")

    from clm_detector import (
        RSI_5M_THRESHOLD, RSI_15M_THRESHOLD,
        EMA_SPREAD_MIN_PCT, EMA_SPREAD_MAX_PCT,
    )

    print("=" * 60)
    print("CLM_RAW Phase 1 — Multi-TF RSI 과열감지 [PAPER ONLY]")
    print(f"진입: rsi5m≥{RSI_5M_THRESHOLD} & rsi15m≥{RSI_15M_THRESHOLD} & "
          f"ema_spread∈[{EMA_SPREAD_MIN_PCT},{EMA_SPREAD_MAX_PCT}]%")
    print(f"청산: target +{TARGET_PROFIT_PCT}% / trail -{TRAILING_STOP_PCT}% / "
          f"early_dd -{EARLY_EXIT_ENTRY_PCT}%({EARLY_EXIT_SEC}s내) / timeout {MAX_HOLD_SEC}s")
    print(f"필터: spread≤{MAX_SPREAD_PCT}% / ask≥{MIN_ASK_KRW:,} / bid≥{MIN_BID_KRW:,} / 종목당max{MAX_ENTRIES_PER_COIN}회")
    print(f"신호검사: {CLM_SIGNAL_INTERVAL:.0f}초 간격 (포지션관리: {SCAN_INTERVAL:.0f}초)")
    print(f"코호트 A 예측: OFF (Phase 3 deferred)")
    print(f"모드: PAPER ONLY (실주문 없음)")
    print(f"모니터: 상위 {TOP_N}개")
    print(f"텔레그램: {'ON' if TG_ENABLED else 'OFF'} ({len(CHAT_IDS)}채널)")
    print("=" * 60)

    markets = get_all_krw_markets()
    log(f"전체 KRW 마켓: {len(markets)}개")
    tickers = get_tickers(markets)
    update_regime_snapshot(tickers)
    top_markets, _ = classify_markets(tickers)
    monitor_list = sorted(top_markets)
    log(f"모니터: {len(monitor_list)}개")
    log(f"종목: {', '.join(m.replace('KRW-','') for m in monitor_list)}")

    if TG_ENABLED:
        tg_send(
            f"🚀 CLM_RAW Phase 1 시작 [PAPER ONLY]\n"
            f"━━━━━━━━━━━━━━━\n"
            f"버전: {_GIT_SHA} ({_GIT_MSG})\n"
            f"시각: {_BOOT_TIME}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"진입: rsi5m≥{RSI_5M_THRESHOLD} & rsi15m≥{RSI_15M_THRESHOLD}\n"
            f"ema_spread∈[{EMA_SPREAD_MIN_PCT},{EMA_SPREAD_MAX_PCT}]%\n"
            f"코호트: OFF (Phase 1)\n"
            f"모드: paper only\n"
            f"━━━━━━━━━━━━━━━\n"
            f"청산: target +{TARGET_PROFIT_PCT}% / trail -{TRAILING_STOP_PCT}% / "
            f"early_dd -{EARLY_EXIT_ENTRY_PCT}%({EARLY_EXIT_SEC}s) / timeout {MAX_HOLD_SEC}s"
        )

    last_reclassify = time.time()
    last_summary_tg = time.time()
    last_autosave = time.time()
    last_review = time.time()
    last_clm_check = 0
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

            # 청산후 추적
            for trk in post_exit_tracks:
                if trk["done"]:
                    continue
                elapsed = now_ts - trk["exit_time"]
                if elapsed > max(POST_EXIT_CHECKPOINTS) + 10:
                    trk["done"] = True
                    continue
                ticker_t = tickers_dict.get(trk["market"])
                if not ticker_t:
                    continue
                price_now = ticker_t["trade_price"]
                for cp in POST_EXIT_CHECKPOINTS:
                    if cp not in trk["prices"] and elapsed >= cp:
                        trk["prices"][cp] = price_now

            # 차단 신호 추적
            for blk in blocked_signals:
                if blk["done"]:
                    continue
                elapsed = now_ts - blk["time"]
                if elapsed > MAX_HOLD_SEC + 10:
                    blk["done"] = True
                    continue
                ticker_b = tickers_dict.get(blk["market"])
                if not ticker_b:
                    continue
                bp = ticker_b["trade_price"]
                if "peak" not in blk:
                    blk["peak"] = bp
                elif bp > blk["peak"]:
                    blk["peak"] = bp
                for cp in [30, 60, 120, 180]:
                    if cp not in blk["prices"] and elapsed >= cp:
                        blk["prices"][cp] = bp

            # ─── CLM 진입 스캔 (RSI는 상태형이므로 30초 간격으로 충분) ───
            clm_scan_due = now_ts - last_clm_check >= CLM_SIGNAL_INTERVAL
            if clm_scan_due:
                last_clm_check = now_ts

            for ticker in tickers:
                market = ticker["market"]
                if not clm_scan_due:
                    continue
                if market not in top_markets:
                    continue
                if market in positions:
                    continue
                if market in cooldowns and now_ts - cooldowns[market] < COOLDOWN_SEC:
                    cooldown_block_count += 1
                    continue

                if MAX_ENTRIES_PER_COIN > 0:
                    coin_entries = sum(1 for t in closed_trades if t["market"] == market)
                    if market in positions:
                        coin_entries += 1
                    if coin_entries >= MAX_ENTRIES_PER_COIN:
                        continue

                signal = detect_overheat(market)
                if signal is None:
                    if market in _active_cooldown_markets:
                        _active_cooldown_markets.discard(market)
                    continue
                if market not in _active_cooldown_markets:
                    _active_cooldown_markets.add(market)
                    cooldown_episodes += 1

                ob = get_orderbook(market)
                if not ob:
                    continue
                ask = ob["ask"]
                bid = ob["bid"]
                ask_krw = ask * ob["ask_size"]
                bid_krw = bid * ob["bid_size"]
                spread_pct = (ask - bid) / bid * 100

                cond_spread = spread_pct <= MAX_SPREAD_PCT
                cond_depth = ask_krw >= MIN_ASK_KRW and bid_krw >= MIN_BID_KRW
                all_pass = cond_spread and cond_depth

                if not all_pass:
                    blockers = []
                    if not cond_spread:
                        blockers.append("spread")
                    if not cond_depth:
                        blockers.append("depth")
                    if len(blocked_signals) >= BLOCKED_MAX:
                        blocked_signals.pop(0)
                    blocked_signals.append({
                        "market": market,
                        "time": now_ts,
                        "ask": ask,
                        "blockers": blockers,
                        "rsi_5m": signal["rsi_5m"],
                        "rsi_15m": signal["rsi_15m"],
                        "ema_spread_pct": signal["ema_spread_pct"],
                        "spread_pct": spread_pct,
                        "ask_krw": ask_krw,
                        "bid_krw": bid_krw,
                        "prices": {},
                        "done": False,
                    })
                    continue

                entry_price = ask
                slip_buy = (ask - ticker["trade_price"]) / ticker["trade_price"] * 100
                positions[market] = {
                    "entry_price": entry_price,
                    "entry_time": now_ts,
                    "peak_price": entry_price,
                    "worst_price": entry_price,
                    "group": "상위",
                    "rsi_5m": round(signal["rsi_5m"], 2),
                    "rsi_15m": round(signal["rsi_15m"], 2),
                    "ema_spread_pct": round(signal["ema_spread_pct"], 4),
                    "spread_pct": round(spread_pct, 4),
                    "ask_krw": round(ask_krw),
                    "bid_krw": round(bid_krw),
                    "slip_buy": round(slip_buy, 4),
                    "rg_btc_ret": regime_snapshot["btc_ret_24h"],
                    "rg_turnover": regime_snapshot["market_turnover"],
                    "rg_breadth": regime_snapshot["breadth_pos"],
                    "rg_btc_dom": regime_snapshot["btc_dominance"],
                    "rg_top5": regime_snapshot["top5_conc"],
                    "pump_1m": compute_pump_1m(market, now_ts),
                }
                coin = market.replace("KRW-", "")
                target_price = entry_price * (1 + TARGET_PROFIT_PCT / 100)
                msg = (
                    f"🔥 CLM ENTRY {coin}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"rsi5m:{signal['rsi_5m']:.1f} rsi15m:{signal['rsi_15m']:.1f}\n"
                    f"ema_spread:{signal['ema_spread_pct']:.3f}%\n"
                    f"spread:{spread_pct:.3f}%\n"
                    f"ask:{ask:,.0f} ({fmt_krw_g(ask_krw)}) bid:{bid:,.0f} ({fmt_krw_g(bid_krw)})\n"
                    f"목표: {target_price:,.0f}원 (+{TARGET_PROFIT_PCT}%)\n"
                    f"스탑: trail -{TRAILING_STOP_PCT}% / {MAX_HOLD_SEC}s"
                )
                log(msg)
                tg_send(f"📊CLM\n{msg}")

            # 주기 태스크
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
                                parts.append(cur)
                                cur = ln
                            else:
                                cur = (cur + "\n" + ln) if cur else ln
                        if cur:
                            parts.append(cur)
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
                    update_regime_snapshot(all_tickers)
                    top_markets, _ = classify_markets(all_tickers)
                    monitor_list = sorted(top_markets)
                    last_reclassify = now_ts
                except Exception:
                    pass

            time.sleep(SCAN_INTERVAL)

    except (KeyboardInterrupt, SystemExit):
        log("CLM 스캐너 종료")
        print("\n" + "=" * 60)
        print("CLM 최종 결과")
        print("=" * 60)
        print_summary()
        analysis = analyze_buckets()
        print(f"\n{analysis}")
        save_results("final")
        if TG_ENABLED:
            tg_send(f"📊 CLM 스캐너 종료\n{get_summary_text()}")
            if len(closed_trades) >= 3:
                tg_send(f"📊 CLM Bucket 분석\n{analysis}")
        if log_fh:
            log_fh.close()

if __name__ == "__main__":
    main()
