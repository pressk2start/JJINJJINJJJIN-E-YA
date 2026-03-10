# -*- coding: utf-8 -*-
"""
업비트 신호 연구 v2.0 — 정량적 백테스트 프레임워크
====================================================
핵심 변경 (v1 → v2):
  - 진입가 = 다음 캔들 시가 (현실적 진입)
  - 수수료 0.05%×2 + 슬리피지 0.08% = 총 0.18% 반영
  - EV, PF, MDD, 손익비 중심 평가 (WR은 참고용)
  - Train/Test 분리 (70/30)
  - BTC 시장 레짐별 성과 분리
  - 최소 100거래 이상 필터
  - 다중 청산전략 비교 (고정/트레일/N봉/시간스탑)
  - MFE/MAE 분포 분석
  - 미래 데이터 누출 없음

사용법:
  python upbit_signal_study.py                  # 기본 30일 30코인
  python upbit_signal_study.py --days 60        # 60일치
  python upbit_signal_study.py --coins 50       # 50코인
  python upbit_signal_study.py --skip-collect   # 수집 스킵
  python upbit_signal_study.py --include-1m     # 1분봉 포함
"""
import requests, time, json, os, sys, gc, argparse, math
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ================================================================
# 설정
# ================================================================
KST = timezone(timedelta(hours=9))
BASE = "https://api.upbit.com/v1"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "candle_data")
OUT_DIR = os.path.join(SCRIPT_DIR, "analysis_output")

# 백테스트 설정
FEE_RATE = 0.0005       # 편도 0.05%
SLIPPAGE = 0.0008       # 슬리피지 0.08%
TOTAL_COST = FEE_RATE * 2 + SLIPPAGE  # 총 0.18%
MIN_TRADES = 100         # 패턴 최소 거래 수
TRAIN_RATIO = 0.7        # 학습 구간 비율
MAX_HOLD_BARS = 60       # 최대 보유 5분봉 수 (5시간)

# 텔레그램
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass
TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN") or ""
_raw = os.getenv("TG_CHATS") or os.getenv("TELEGRAM_CHAT_ID") or ""
CHAT_IDS = []
for p in _raw.split(","):
    p = p.strip()
    if p:
        try: CHAT_IDS.append(int(p))
        except: pass


def tg(msg):
    print(msg)
    if not TG_TOKEN or not CHAT_IDS:
        return
    for cid in CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": cid, "text": msg[:4000], "disable_web_page_preview": True},
                timeout=10
            )
        except:
            pass


def safe_get(url, params=None, retries=4, timeout=10):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(2 + i * 2)
                continue
            time.sleep(0.5 + i)
        except:
            time.sleep(1 + i)
    return None


# ================================================================
# PART 1: 데이터 수집 (변경 없음)
# ================================================================
def get_top_markets(n=30):
    tickers = safe_get(f"{BASE}/market/all", {"isDetails": "true"})
    if not tickers:
        return []
    krw = [t["market"] for t in tickers if t["market"].startswith("KRW-")]
    stable = {"USDT", "USDC", "DAI", "TUSD", "BUSD"}
    krw = [m for m in krw if m.split("-")[1] not in stable]
    all_t = []
    for i in range(0, len(krw), 100):
        b = safe_get(f"{BASE}/ticker", {"markets": ",".join(krw[i:i+100])})
        if b:
            all_t.extend(b)
        time.sleep(0.15)
    all_t.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
    return [t["market"] for t in all_t[:n]]


def fetch_candles(unit, market, total_count):
    result, to = [], None
    pages = (total_count + 199) // 200
    for page in range(pages):
        params = {"market": market, "count": min(200, total_count - len(result))}
        if to:
            params["to"] = to
        data = safe_get(f"{BASE}/candles/minutes/{unit}", params)
        if not data:
            break
        result.extend(data)
        if len(data) < 200:
            break
        to = data[-1]["candle_date_time_utc"] + "Z"
        if (page + 1) % 20 == 0:
            print(f"    {len(result)}/{total_count}")
        time.sleep(0.12)
    result.sort(key=lambda x: x["candle_date_time_kst"])
    return result


def compress(c):
    return {
        "t": c["candle_date_time_kst"],
        "o": c["opening_price"],
        "h": c["high_price"],
        "l": c["low_price"],
        "c": c["trade_price"],
        "v": round(c.get("candle_acc_trade_volume", 0), 6),
        "vk": round(c.get("candle_acc_trade_price", 0)),
    }


def save_coin(coin, unit, candles):
    os.makedirs(DATA_DIR, exist_ok=True)
    compressed = [compress(c) for c in candles]
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump({
            "coin": coin, "unit": unit, "count": len(compressed),
            "from": compressed[0]["t"] if compressed else "",
            "to": compressed[-1]["t"] if compressed else "",
            "collected_at": datetime.now(KST).isoformat(),
            "candles": compressed
        }, f, ensure_ascii=False)
    kb = os.path.getsize(fpath) / 1024
    print(f"    {coin}_{unit}m: {len(compressed)}개, {kb:.0f}KB")


def is_collected(coin, unit, min_count=100):
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath):
        return False
    try:
        with open(fpath) as f:
            return json.load(f).get("count", 0) >= min_count
    except:
        return False


def collect(days=30, top_n=30, include_1m=False):
    tg(f"[수집] {days}일, {top_n}코인 시작")
    markets = get_top_markets(top_n)
    if not markets:
        tg("[오류] 종목 가져오기 실패")
        return []
    coins = [m.split("-")[1] for m in markets]
    tg(f"종목: {', '.join(coins)}")

    timeframes = [("5", 288), ("15", 96)]
    if include_1m:
        timeframes.insert(0, ("1", 1440))

    t0 = time.time()
    for i, market in enumerate(markets):
        coin = market.split("-")[1]
        print(f"\n[{i+1}/{top_n}] {coin}")
        for tf, per_day in timeframes:
            total = per_day * days
            if is_collected(coin, int(tf), total // 2):
                print(f"    {tf}분봉 있음, 스킵")
                continue
            print(f"    {tf}분봉 {total}개 수집...")
            candles = fetch_candles(int(tf), market, total)
            if candles:
                save_coin(coin, int(tf), candles)
                del candles
                gc.collect()
            else:
                print(f"    실패")
            time.sleep(0.3)
        elapsed = time.time() - t0
        eta = elapsed / (i + 1) * (top_n - i - 1)
        print(f"    {elapsed/60:.1f}분 경과, ~{eta/60:.1f}분 남음")

    tg(f"[수집 완료] {(time.time()-t0)/60:.1f}분")
    return coins


# ================================================================
# PART 2: 분석 엔진
# ================================================================
def load_coin_data(coin, unit):
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath):
        return []
    with open(fpath, encoding="utf-8") as f:
        return json.load(f).get("candles", [])


def get_saved_coins():
    if not os.path.exists(DATA_DIR):
        return []
    return sorted(set(
        f.replace("_5m.json", "")
        for f in os.listdir(DATA_DIR) if f.endswith("_5m.json")
    ))


def make_15m(c5):
    """5분봉 3개 → 15분봉 1개 합성"""
    out = []
    for i in range(0, len(c5) - 2, 3):
        a, b, c = c5[i], c5[i+1], c5[i+2]
        out.append({
            "t": a["t"], "o": a["o"], "c": c["c"],
            "h": max(a["h"], b["h"], c["h"]),
            "l": min(a["l"], b["l"], c["l"]),
            "v": a["v"] + b["v"] + c["v"],
            "_si": i  # 5분봉 인덱스 매핑
        })
    return out


# --- 지표 (과거 데이터만 사용, 미래 누출 없음) ---
def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = 0, 0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i-1]
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def bb_pos(closes, period=20):
    if len(closes) < period:
        return 50
    s = closes[-period:]
    m = sum(s) / period
    std = (sum((x - m)**2 for x in s) / period) ** 0.5
    if std == 0:
        return 50
    upper = m + 2 * std
    lower = m - 2 * std
    return (closes[-1] - lower) / (upper - lower) * 100


def ema_val(closes, period):
    if len(closes) < period:
        return closes[-1] if closes else 0
    k = 2 / (period + 1)
    e = sum(closes[:period]) / period
    for v in closes[period:]:
        e = v * k + e * (1 - k)
    return e


# --- BTC 레짐 분류 ---
def classify_btc_regime(btc_5m):
    """
    BTC 5분봉 전체를 시간 → 레짐 매핑으로 변환.
    60봉(5시간) 기준 수익률:
      > +0.5% → 상승
      < -0.5% → 하락
      else → 횡보
    반환: dict { "2026-03-08T14:00" → "상승" | "횡보" | "하락" }
    """
    regime = {}
    for i in range(60, len(btc_5m)):
        ret = (btc_5m[i]["c"] - btc_5m[i-60]["c"]) / btc_5m[i-60]["c"] * 100
        t = btc_5m[i]["t"][:16]  # "2026-03-08T14:05" → "2026-03-08T14:0"
        if ret > 0.5:
            regime[t] = "상승"
        elif ret < -0.5:
            regime[t] = "하락"
        else:
            regime[t] = "횡보"
    return regime


# ================================================================
# 신호 감지 + 시뮬레이션 (다음 캔들 시가 진입)
# ================================================================
def process_coin(coin, btc_regime):
    """
    코인 하나의 모든 신호 감지 → 다음 캔들 시가 진입 → 다중 청산 시뮬
    """
    c5 = load_coin_data(coin, 5)
    if len(c5) < 200:
        return {}

    c15 = load_coin_data(coin, 15)
    if not c15:
        c15 = make_15m(c5)

    results = {}

    # === 신호 1: 15분봉 눌림반전 ===
    results["15m_눌림반전"] = _scan_15m_reversal(coin, c5, c15, btc_regime, require_break=False)

    # === 신호 2: 15분봉 눌림반전 + 이전고가돌파 ===
    results["15m_눌림+돌파"] = _scan_15m_reversal(coin, c5, c15, btc_regime, require_break=True)

    # === 신호 3: 5분봉 양봉 ===
    results["5m_양봉"] = _scan_5m_green(coin, c5, c15, btc_regime)

    # === 신호 4: 20봉 고점돌파 ===
    results["20봉_고점돌파"] = _scan_breakout(coin, c5, c15, btc_regime)

    # === 신호 5: 거래량 3배 + 양봉 ===
    results["거래량3배"] = _scan_vol_spike(coin, c5, c15, btc_regime)

    del c5, c15
    gc.collect()
    return results


def _scan_15m_reversal(coin, c5, c15, btc_regime, require_break=False):
    sigs = []
    last_i5 = -20
    for i in range(14, len(c15)):
        bar, prev = c15[i], c15[i-1]
        # 이전봉 음봉, 현재봉 양봉
        if prev["c"] >= prev["o"]:
            continue
        if bar["c"] <= bar["o"]:
            continue
        body_pct = (bar["c"] - bar["o"]) / bar["o"] * 100
        if body_pct < 0.1:
            continue
        if require_break and bar["c"] <= prev["h"]:
            continue

        # 5분봉 인덱스 (15분봉 마지막 5분봉 다음이 진입 캔들)
        si = bar.get("_si", i * 3) + 2
        entry_idx = si + 1  # ★ 다음 캔들

        if entry_idx - last_i5 < 10:
            continue
        if entry_idx >= len(c5) - MAX_HOLD_BARS:
            continue

        last_i5 = entry_idx
        sig = _build_signal(coin, c5, c15, i, si, entry_idx, bar, btc_regime)
        if sig:
            sigs.append(sig)
    return sigs


def _scan_5m_green(coin, c5, c15, btc_regime):
    sigs = []
    last = -20
    for i in range(30, len(c5) - MAX_HOLD_BARS - 1):
        bar = c5[i]
        if bar["c"] <= bar["o"]:
            continue
        if (bar["c"] - bar["o"]) / bar["o"] * 100 < 0.1:
            continue
        entry_idx = i + 1  # ★ 다음 캔들
        if entry_idx - last < 10:
            continue
        last = entry_idx
        i15 = min(i // 3, len(c15) - 1)
        sig = _build_signal(coin, c5, c15, i15, i, entry_idx, bar, btc_regime)
        if sig:
            sigs.append(sig)
    return sigs


def _scan_breakout(coin, c5, c15, btc_regime):
    sigs = []
    last = -20
    for i in range(30, len(c5) - MAX_HOLD_BARS - 1):
        bar = c5[i]
        if bar["c"] <= bar["o"]:
            continue
        prev_high = max(c5[j]["h"] for j in range(max(0, i-20), i))
        if bar["h"] <= prev_high:
            continue
        entry_idx = i + 1
        if entry_idx - last < 10:
            continue
        last = entry_idx
        i15 = min(i // 3, len(c15) - 1)
        sig = _build_signal(coin, c5, c15, i15, i, entry_idx, bar, btc_regime)
        if sig:
            sigs.append(sig)
    return sigs


def _scan_vol_spike(coin, c5, c15, btc_regime):
    sigs = []
    last = -20
    for i in range(30, len(c5) - MAX_HOLD_BARS - 1):
        bar = c5[i]
        if bar["c"] <= bar["o"]:
            continue
        vols = [c5[j]["v"] for j in range(max(0, i-5), i)]
        avg_vol = sum(vols) / max(len(vols), 1)
        if avg_vol == 0 or bar["v"] < avg_vol * 3:
            continue
        entry_idx = i + 1
        if entry_idx - last < 10:
            continue
        last = entry_idx
        i15 = min(i // 3, len(c15) - 1)
        sig = _build_signal(coin, c5, c15, i15, i, entry_idx, bar, btc_regime)
        if sig:
            sigs.append(sig)
    return sigs


def _build_signal(coin, c5, c15, i15, sig_bar_i5, entry_idx, sig_bar, btc_regime):
    """
    sig_bar: 신호 발생 캔들
    entry_idx: 진입 캔들 (= sig_bar 다음 봉) → 시가(open) 진입
    """
    entry_price = c5[entry_idx]["o"]  # ★ 다음 캔들 시가 진입
    if entry_price <= 0:
        return None

    try:
        hour = int(sig_bar["t"][11:13])
    except:
        hour = 12

    # 시간 키 (레짐 매핑용)
    time_key = sig_bar["t"][:16]
    regime = btc_regime.get(time_key, "횡보")

    # --- 지표 (신호 캔들까지만 사용, 미래 누출 없음) ---
    cl5 = [c5[j]["c"] for j in range(max(0, sig_bar_i5 - 29), sig_bar_i5 + 1)]
    r5 = rsi(cl5, 14)
    b5 = bb_pos(cl5, 20)

    cl15 = [c15[j]["c"] for j in range(max(0, i15 - 19), i15 + 1)]
    r15 = rsi(cl15, 14)
    b15 = bb_pos(cl15, 20)

    vols = [c5[j]["v"] for j in range(max(0, sig_bar_i5 - 5), sig_bar_i5)]
    avg_vol = sum(vols) / max(len(vols), 1)
    vr = c5[sig_bar_i5]["v"] / avg_vol if avg_vol > 0 else 1

    e20 = ema_val(cl15, 20) if len(cl15) >= 20 else entry_price
    above_ema = 1 if entry_price > e20 else 0

    base5 = c5[max(0, sig_bar_i5 - 5)]["c"]
    mom5 = (sig_bar["c"] - base5) / base5 * 100 if base5 > 0 else 0
    base10 = c5[max(0, sig_bar_i5 - 10)]["c"]
    mom10 = (sig_bar["c"] - base10) / base10 * 100 if base10 > 0 else 0

    dip3 = 1 if (sig_bar_i5 >= 3 and c5[sig_bar_i5 - 3]["c"] > c5[sig_bar_i5 - 1]["c"]) else 0
    brk = 1 if (sig_bar_i5 >= 1 and sig_bar["c"] > c5[sig_bar_i5 - 1]["h"]) else 0
    body = (sig_bar["c"] - sig_bar["o"]) / sig_bar["o"] * 100 if sig_bar["o"] > 0 else 0

    # --- MFE/MAE (진입가 기준, 수수료 미반영 raw) ---
    max_bars = min(MAX_HOLD_BARS, len(c5) - entry_idx - 1)
    mfe, mae, mfe_bar = 0, 0, 0
    for j in range(1, max_bars + 1):
        fc = c5[entry_idx + j]
        hi_pct = (fc["h"] - entry_price) / entry_price * 100
        lo_pct = (fc["l"] - entry_price) / entry_price * 100
        if hi_pct > mfe:
            mfe = hi_pct
            mfe_bar = j
        if lo_pct < mae:
            mae = lo_pct

    # --- 다중 청산전략 시뮬레이션 ---
    exits = {}
    cost_pct = TOTAL_COST * 100  # 0.18%

    # 전략 1: 고정 TP/SL
    for tp in [0.3, 0.5, 0.7, 1.0, 1.5]:
        for sl in [0.3, 0.5, 0.7, 1.0]:
            pnl = _sim_fixed_tpsl(c5, entry_idx, entry_price, tp, sl, max_bars, cost_pct)
            exits[f"FIX_TP{tp}/SL{sl}"] = pnl

    # 전략 2: 트레일링 스탑
    for sl in [0.5, 0.7, 1.0]:
        for arm in [0.2, 0.3, 0.5]:
            for trail in [0.1, 0.15, 0.2, 0.3]:
                if trail > arm:
                    continue
                pnl = _sim_trail(c5, entry_idx, entry_price, sl, arm, trail, max_bars, cost_pct)
                exits[f"TRAIL_SL{sl}/A{arm}/T{trail}"] = pnl

    # 전략 3: N봉 홀드 (시간 기반 청산)
    for n in [6, 12, 18, 24, 36]:
        if n <= max_bars:
            exit_close = c5[entry_idx + n]["c"]
            raw = (exit_close - entry_price) / entry_price * 100
            exits[f"HOLD_{n}봉"] = round(raw - cost_pct, 4)

    # 전략 4: 시간스탑 + SL (N봉 내에 SL 안 걸리면 시간 청산)
    for n in [12, 24]:
        for sl in [0.7, 1.0]:
            pnl = _sim_timestop(c5, entry_idx, entry_price, sl, n, max_bars, cost_pct)
            exits[f"TIME{n}_SL{sl}"] = pnl

    return {
        "coin": coin, "time": sig_bar["t"], "hour": hour,
        "entry": entry_price, "regime": regime,
        "rsi5": round(r5, 1), "rsi15": round(r15, 1),
        "bb5": round(b5, 1), "bb15": round(b15, 1),
        "vr": round(vr, 2), "body": round(body, 3),
        "above_ema": above_ema, "mom5": round(mom5, 3), "mom10": round(mom10, 3),
        "dip3": dip3, "brk": brk,
        "mfe": round(mfe, 4), "mae": round(mae, 4), "mfe_bar": mfe_bar,
        "exits": exits,
    }


# --- 청산 시뮬레이터 ---
def _sim_fixed_tpsl(c5, entry_idx, entry, tp, sl, max_bars, cost):
    """고정 TP/SL. 봉 내 SL 먼저 체크 (보수적)."""
    for j in range(1, max_bars + 1):
        fc = c5[entry_idx + j]
        lo_pct = (fc["l"] - entry) / entry * 100
        hi_pct = (fc["h"] - entry) / entry * 100
        # SL 먼저 (보수적)
        if lo_pct <= -sl:
            return round(-sl - cost, 4)
        if hi_pct >= tp:
            return round(tp - cost, 4)
    # 시간 만료 → 종가 청산
    exit_c = c5[entry_idx + max_bars]["c"]
    raw = (exit_c - entry) / entry * 100
    return round(raw - cost, 4)


def _sim_trail(c5, entry_idx, entry, sl, arm, trail, max_bars, cost):
    """트레일링 스탑. SL → arm 도달 시 trail 활성화."""
    max_pct = 0
    armed = False
    for j in range(1, max_bars + 1):
        fc = c5[entry_idx + j]
        hi_pct = (fc["h"] - entry) / entry * 100
        lo_pct = (fc["l"] - entry) / entry * 100
        if hi_pct > max_pct:
            max_pct = hi_pct
        # SL
        if lo_pct <= -sl:
            return round(-sl - cost, 4)
        # arm
        if max_pct >= arm:
            armed = True
        # trail
        if armed and (max_pct - lo_pct) >= trail:
            pnl = max(max_pct - trail, -sl)
            return round(pnl - cost, 4)
    # 시간 만료
    exit_c = c5[entry_idx + max_bars]["c"]
    raw = (exit_c - entry) / entry * 100
    return round(raw - cost, 4)


def _sim_timestop(c5, entry_idx, entry, sl, n_bars, max_bars, cost):
    """시간스탑: N봉 내에 SL 안 걸리면 시간 청산."""
    hold = min(n_bars, max_bars)
    for j in range(1, hold + 1):
        fc = c5[entry_idx + j]
        lo_pct = (fc["l"] - entry) / entry * 100
        if lo_pct <= -sl:
            return round(-sl - cost, 4)
    exit_c = c5[entry_idx + hold]["c"]
    raw = (exit_c - entry) / entry * 100
    return round(raw - cost, 4)


# ================================================================
# PART 3: 통계 함수
# ================================================================
def calc_metrics(pnls):
    """EV, PF, MDD, 손익비, WR 등 핵심 지표 계산"""
    if not pnls:
        return None
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins) / n * 100
    ev = sum(pnls) / n

    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    rr = avg_win / avg_loss if avg_loss > 0 else float('inf')  # 손익비

    total_gain = sum(wins)
    total_loss = abs(sum(losses))
    pf = total_gain / total_loss if total_loss > 0 else float('inf')  # Profit Factor

    # MDD (누적 수익 기준)
    cumulative = 0
    peak = 0
    mdd = 0
    for p in pnls:
        cumulative += p
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > mdd:
            mdd = dd

    # 총 수익
    total_pnl = sum(pnls)

    return {
        "n": n, "wr": wr, "ev": ev, "pf": pf, "mdd": mdd, "rr": rr,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "total_pnl": total_pnl,
        "total_gain": total_gain, "total_loss": total_loss,
    }


def fmt_metrics(m, indent=""):
    """지표 → 한줄 문자열"""
    if not m:
        return f"{indent}데이터 없음"
    pf_str = f"{m['pf']:.2f}" if m['pf'] < 999 else "INF"
    rr_str = f"{m['rr']:.2f}" if m['rr'] < 999 else "INF"
    return (
        f"{indent}n={m['n']:>5d}  "
        f"EV={m['ev']:>+.4f}%  "
        f"PF={pf_str:>6s}  "
        f"MDD={m['mdd']:>.3f}%  "
        f"WR={m['wr']:>5.1f}%  "
        f"손익비={rr_str:>5s}  "
        f"총PnL={m['total_pnl']:>+.2f}%"
    )


# ================================================================
# PART 4: 리포트 생성
# ================================================================
def generate_report(all_results):
    lines = []
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    lines.append(f"업비트 신호 연구 v2.0 리포트 ({ts})")
    lines.append(f"수수료: {TOTAL_COST*100:.2f}% (편도{FEE_RATE*100:.2f}%×2 + 슬리피지{SLIPPAGE*100:.2f}%)")
    lines.append(f"진입: 다음 캔들 시가 | 최대보유: {MAX_HOLD_BARS}봉({MAX_HOLD_BARS*5}분)")
    lines.append(f"최소 거래수: {MIN_TRADES} | Train/Test: {TRAIN_RATIO*100:.0f}/{(1-TRAIN_RATIO)*100:.0f}")
    lines.append("=" * 70)

    # 대표 전략 키 (리포트에서 기본 비교용)
    default_exits = ["TRAIL_SL0.7/A0.3/T0.2", "FIX_TP0.7/SL0.7", "HOLD_12봉"]

    # ─── 1. 신호 유형별 비교 ───
    lines.append("\n[1] 신호 유형별 비교")
    lines.append("-" * 70)
    for de in default_exits:
        lines.append(f"\n  전략: {de}")
        ranked = []
        for stype, sigs in all_results.items():
            if not sigs:
                continue
            pnls = [s["exits"].get(de, 0) for s in sigs]
            m = calc_metrics(pnls)
            if m:
                ranked.append((stype, m))
        ranked.sort(key=lambda x: -x[1]["ev"])
        for stype, m in ranked:
            star = " ***" if m["ev"] > 0 and m["n"] >= MIN_TRADES else ""
            lines.append(f"    {stype:<18s} {fmt_metrics(m)}{star}")

    # ─── 2. 각 신호별 상세 ───
    for stype, sigs in all_results.items():
        if len(sigs) < 30:
            continue
        lines.append(f"\n{'='*70}")
        lines.append(f"[2] {stype} 상세 (전체 n={len(sigs)})")
        lines.append(f"{'='*70}")

        # Train/Test 분리
        split_idx = int(len(sigs) * TRAIN_RATIO)
        train_sigs = sigs[:split_idx]
        test_sigs = sigs[split_idx:]

        for subset_name, subset in [("TRAIN", train_sigs), ("TEST", test_sigs), ("전체", sigs)]:
            if len(subset) < 10:
                continue
            lines.append(f"\n  --- {subset_name} (n={len(subset)}) ---")

            # 2-1. 최적 청산전략 TOP10 (EV 기준)
            lines.append(f"\n  [최적 청산전략 TOP10 by EV]")
            exit_keys = sorted(set(k for s in subset for k in s["exits"]))
            exit_ranked = []
            for ek in exit_keys:
                pnls = [s["exits"].get(ek, 0) for s in subset]
                m = calc_metrics(pnls)
                if m and m["n"] >= 10:
                    exit_ranked.append((ek, m))
            exit_ranked.sort(key=lambda x: -x[1]["ev"])
            for ek, m in exit_ranked[:10]:
                star = " ***" if m["ev"] > 0 else ""
                lines.append(f"    {ek:28s} {fmt_metrics(m, '')}{star}")

            # 2-2. MFE/MAE 분포
            if subset_name != "전체":
                continue  # MFE/MAE는 전체에서만

            mfes = sorted([s["mfe"] for s in subset])
            maes = sorted([s["mae"] for s in subset])
            lines.append(f"\n  [MFE 분포] (진입 후 최대 상승%)")
            for pct in [10, 25, 50, 75, 90]:
                idx = min(int(len(mfes) * pct / 100), len(mfes) - 1)
                lines.append(f"    P{pct:2d}: {mfes[idx]:>+.3f}%")

            lines.append(f"\n  [MAE 분포] (진입 후 최대 하락%)")
            for pct in [10, 25, 50, 75, 90]:
                idx = min(int(len(maes) * pct / 100), len(maes) - 1)
                lines.append(f"    P{pct:2d}: {maes[idx]:>+.3f}%")

            # 2-3. MFE→MAE 관계 (적정 SL 판단)
            lines.append(f"\n  [적정 SL 판단: MAE가 X% 이상인 거래의 MFE]")
            for mae_thr in [0.3, 0.5, 0.7, 1.0]:
                deep = [s for s in subset if s["mae"] <= -mae_thr]
                if len(deep) >= 5:
                    avg_mfe = sum(s["mfe"] for s in deep) / len(deep)
                    lines.append(f"    MAE<=-{mae_thr}%: n={len(deep)}, avgMFE={avg_mfe:+.3f}%")

        # ─── 조건별 분석 (전체 데이터) ───
        lines.append(f"\n  [조건별 EV 분석] (전략: TRAIL_SL0.7/A0.3/T0.2)")
        dk = "TRAIL_SL0.7/A0.3/T0.2"

        def cond_ev(name, fn, sig_list=sigs):
            sub = [s for s in sig_list if fn(s)]
            if len(sub) < 10:
                return
            pnls = [s["exits"].get(dk, 0) for s in sub]
            m = calc_metrics(pnls)
            if m:
                star = " ***" if m["ev"] > 0 and m["n"] >= MIN_TRADES else ""
                lines.append(f"    {name:30s} {fmt_metrics(m, '')}{star}")

        # RSI
        lines.append(f"\n    [RSI 15분봉]")
        for lo, hi in [(0, 30), (30, 40), (40, 50), (50, 60), (60, 100)]:
            cond_ev(f"RSI15 {lo}-{hi}", lambda s, lo=lo, hi=hi: lo <= s["rsi15"] < hi)

        # BB
        lines.append(f"\n    [BB 15분봉]")
        for lo, hi in [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]:
            cond_ev(f"BB15 {lo}-{hi}", lambda s, lo=lo, hi=hi: lo <= s["bb15"] < hi)

        # 레짐
        lines.append(f"\n    [BTC 시장 레짐]")
        for reg in ["상승", "횡보", "하락"]:
            cond_ev(f"BTC_{reg}", lambda s, r=reg: s["regime"] == r)

        # 시간대
        lines.append(f"\n    [시간대]")
        for h in range(24):
            cond_ev(f"{h:02d}시", lambda s, h=h: s["hour"] == h)

        # 패턴
        lines.append(f"\n    [진입 패턴]")
        cond_ev("눌림3봉(dip3)", lambda s: s["dip3"] == 1)
        cond_ev("이전봉돌파(brk)", lambda s: s["brk"] == 1)
        cond_ev("눌림+돌파", lambda s: s["dip3"] == 1 and s["brk"] == 1)
        cond_ev("EMA 위", lambda s: s["above_ema"] == 1)
        cond_ev("EMA 아래", lambda s: s["above_ema"] == 0)

        # 복합 필터
        lines.append(f"\n    [복합 필터]")
        combos = [
            ("BB15<20", lambda s: s["bb15"] < 20),
            ("BB15<30", lambda s: s["bb15"] < 30),
            ("BB15<40+RSI15<50", lambda s: s["bb15"] < 40 and s["rsi15"] < 50),
            ("BB15<30+RSI15<40", lambda s: s["bb15"] < 30 and s["rsi15"] < 40),
            ("BB15<40+눌림+돌파", lambda s: s["bb15"] < 40 and s["dip3"] == 1 and s["brk"] == 1),
            ("BB15<30+RSI15<50+돌파", lambda s: s["bb15"] < 30 and s["rsi15"] < 50 and s["brk"] == 1),
            ("BB15<40+BTC상승", lambda s: s["bb15"] < 40 and s["regime"] == "상승"),
            ("BB15<40+BTC횡보", lambda s: s["bb15"] < 40 and s["regime"] == "횡보"),
        ]
        for name, fn in combos:
            cond_ev(name, fn)

        # 복합 필터 × 최적 전략 (Train/Test)
        lines.append(f"\n  [복합 필터 × 최적 전략 — Train vs Test 비교]")
        for fname, ffn in combos:
            train_sub = [s for s in train_sigs if ffn(s)]
            test_sub = [s for s in test_sigs if ffn(s)]
            if len(train_sub) < 20 or len(test_sub) < 5:
                continue
            lines.append(f"\n    필터: {fname}")
            # Train에서 최적 전략 찾기
            best_ek, best_ev = None, -999
            for ek in exit_keys:
                pnls = [s["exits"].get(ek, 0) for s in train_sub]
                m = calc_metrics(pnls)
                if m and m["ev"] > best_ev:
                    best_ev = m["ev"]
                    best_ek = ek
            if best_ek:
                # Train 성과
                tr_pnls = [s["exits"].get(best_ek, 0) for s in train_sub]
                tr_m = calc_metrics(tr_pnls)
                lines.append(f"      TRAIN: {best_ek}")
                lines.append(f"        {fmt_metrics(tr_m)}")
                # Test 성과
                te_pnls = [s["exits"].get(best_ek, 0) for s in test_sub]
                te_m = calc_metrics(te_pnls)
                lines.append(f"      TEST:")
                lines.append(f"        {fmt_metrics(te_m)}")
                if tr_m and te_m and tr_m["ev"] > 0 and te_m["ev"] > 0:
                    lines.append(f"      >>> Train+Test 모두 EV>0 <<<")

    # ─── 3. 최종 결론 ───
    lines.append(f"\n{'='*70}")
    lines.append("[3] 최종 결론 — EV > 0 & n >= 100 인 조합")
    lines.append("=" * 70)
    lines.append(f"  평가 우선순위: EV > MDD > PF > 거래횟수 > WR")
    lines.append(f"  수수료 포함: {TOTAL_COST*100:.2f}%/거래")
    lines.append(f"  Train/Test 분리 적용")

    found = False
    for stype, sigs in all_results.items():
        if len(sigs) < MIN_TRADES:
            continue
        split_idx = int(len(sigs) * TRAIN_RATIO)
        test_sigs = sigs[split_idx:]

        exit_keys = sorted(set(k for s in sigs for k in s["exits"]))
        for ek in exit_keys:
            pnls = [s["exits"].get(ek, 0) for s in test_sigs]
            m = calc_metrics(pnls)
            if m and m["ev"] > 0 and m["n"] >= 20:
                lines.append(f"\n  {stype} + {ek}")
                lines.append(f"    TEST: {fmt_metrics(m)}")
                found = True

    if not found:
        lines.append("\n  >>> 수수료 포함 시 TEST에서 EV>0 인 조합 없음 <<<")
        lines.append("  → 현재 신호 정의로는 안정적 수익 불가")
        lines.append("  → 신호 정의 또는 필터 재설계 필요")

    return lines


# ================================================================
# 메인
# ================================================================
def main():
    parser = argparse.ArgumentParser(description="업비트 신호 연구 v2.0")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--coins", type=int, default=30)
    parser.add_argument("--skip-collect", action="store_true", help="수집 스킵, 분석만")
    parser.add_argument("--include-1m", action="store_true")
    args = parser.parse_args()

    t0 = time.time()

    # === 수집 ===
    if not args.skip_collect:
        collect(days=args.days, top_n=args.coins, include_1m=args.include_1m)

    # === 분석 ===
    coins = get_saved_coins()
    if not coins:
        tg(f"[오류] 데이터 없음: {DATA_DIR}")
        return

    tg(f"\n[분석] {len(coins)}개 코인 시작")

    # BTC 레짐 분류
    btc_5m = load_coin_data("BTC", 5)
    if btc_5m:
        btc_regime = classify_btc_regime(btc_5m)
        tg(f"  BTC 레짐: {len(btc_regime)}개 시점 분류됨")
        del btc_5m
    else:
        btc_regime = {}
        tg("  BTC 데이터 없음 → 레짐 분류 스킵")

    all_results = defaultdict(list)

    for i, coin in enumerate(coins):
        coin_results = process_coin(coin, btc_regime)
        for stype, sigs in coin_results.items():
            all_results[stype].extend(sigs)
        if (i + 1) % 10 == 0:
            counts = {k: len(v) for k, v in all_results.items()}
            print(f"  진행 {i+1}/{len(coins)}: {counts}")

    # 시간순 정렬 (Train/Test 분리를 위해)
    for stype in all_results:
        all_results[stype].sort(key=lambda s: s["time"])

    tg(f"\n[신호 감지 완료]")
    for stype, sigs in all_results.items():
        tg(f"  {stype}: {len(sigs)}건")

    # 리포트
    lines = generate_report(dict(all_results))

    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now(KST).strftime("%Y%m%d_%H%M")
    fpath = os.path.join(OUT_DIR, f"signal_study_v2_{ts}.txt")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    elapsed = (time.time() - t0) / 60
    tg(f"\n[완료] {elapsed:.1f}분 소요")
    tg(f"리포트: {fpath}")

    # 텔레그램에 핵심 결과
    summary = "\n".join(lines[:50])
    tg(f"\n{summary}")

    if os.path.exists(DATA_DIR):
        files = os.listdir(DATA_DIR)
        total_mb = sum(os.path.getsize(os.path.join(DATA_DIR, f)) for f in files) / 1024 / 1024
        tg(f"데이터: {len(files)}파일, {total_mb:.1f}MB")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        tg("중단됨 (저장된 데이터 유지)")
    except Exception as e:
        import traceback
        tg(f"[에러] {e}\n{traceback.format_exc()[-500:]}")
