# -*- coding: utf-8 -*-
"""
업비트 신호 연구 v3.0 — 다중 타임프레임 + 대량 지표
====================================================
v2 → v3 변경:
  - 수집: 1m, 3m, 5m, 15m, 30m, 1h (6개 타임프레임)
  - 지표: 타임프레임별 50+개 피처 추출
    RSI(7,14,21), BB(10,20), MACD, Stoch, ATR, ADX, OBV변화율,
    CCI, Williams%R, MFI, 이격도, EMA(5,10,20,50) 정배열,
    캔들패턴(망치/도지/감싸기/연속양음봉), 꼬리비율, 변동성 등
  - 진입: 다음 캔들 시가 (미래 누출 없음)
  - 수수료 0.18% 반영
  - EV/PF/MDD/손익비 중심 평가
  - Train/Test 70/30 분리
  - BTC 레짐별 분석

사용법:
  python upbit_signal_study.py                  # 기본 30일 30코인
  python upbit_signal_study.py --days 60        # 60일
  python upbit_signal_study.py --coins 50       # 50코인
  python upbit_signal_study.py --skip-collect   # 수집 스킵
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

FEE_RATE = 0.0005
SLIPPAGE = 0.0008
TOTAL_COST = FEE_RATE * 2 + SLIPPAGE  # 0.18%
MIN_TRADES = 100
TRAIN_RATIO = 0.7
MAX_HOLD_BARS = 60  # 5분봉 기준

# 수집할 타임프레임: (분, 하루당 봉수)
TIMEFRAMES = [
    (1, 1440),
    (3, 480),
    (5, 288),
    (15, 96),
    (30, 48),
    (60, 24),
]

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
# PART 1: 데이터 수집
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
        if (page + 1) % 30 == 0:
            print(f"      {len(result)}/{total_count}")
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
    print(f"      {coin}_{unit}m: {len(compressed)}개, {kb:.0f}KB")


def is_collected(coin, unit, min_count=100):
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath):
        return False
    try:
        with open(fpath) as f:
            return json.load(f).get("count", 0) >= min_count
    except:
        return False


def collect(days=30, top_n=30):
    tg(f"[수집] {days}일, {top_n}코인, {len(TIMEFRAMES)}개 타임프레임")
    markets = get_top_markets(top_n)
    if not markets:
        tg("[오류] 종목 가져오기 실패")
        return []
    coins = [m.split("-")[1] for m in markets]
    tg(f"종목: {', '.join(coins)}")
    tg(f"타임프레임: {', '.join(f'{tf}m' for tf,_ in TIMEFRAMES)}")

    t0 = time.time()
    for i, market in enumerate(markets):
        coin = market.split("-")[1]
        print(f"\n[{i+1}/{top_n}] {coin}")
        try:
            for tf, per_day in TIMEFRAMES:
                total = per_day * days
                # 1분봉은 데이터량이 크므로 최대 7일만
                if tf == 1 and days > 7:
                    total = 1440 * 7
                if is_collected(coin, tf, total // 2):
                    print(f"    {tf}m 있음, 스킵")
                    continue
                print(f"    {tf}m {total}개 수집...")
                candles = fetch_candles(tf, market, total)
                if candles:
                    save_coin(coin, tf, candles)
                    del candles
                    gc.collect()
                else:
                    print(f"    실패")
                time.sleep(0.3)
        except Exception as e:
            import traceback
            tg(f"[수집 에러] {coin}: {e}\n{traceback.format_exc()[-300:]}")
            continue
        elapsed = time.time() - t0
        eta = elapsed / (i + 1) * (top_n - i - 1)
        if (i + 1) % 5 == 0:
            tg(f"  수집 진행 {i+1}/{top_n} ({elapsed/60:.1f}분, ~{eta/60:.1f}분 남음)")

    tg(f"[수집 완료] {(time.time()-t0)/60:.1f}분")
    return coins


# ================================================================
# PART 2: 지표 계산 함수 (모두 과거 데이터만 사용)
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


def make_nmin(c_small, ratio):
    """작은 봉 ratio개 → 큰 봉 1개 합성"""
    out = []
    for i in range(0, len(c_small) - ratio + 1, ratio):
        batch = c_small[i:i+ratio]
        out.append({
            "t": batch[0]["t"],
            "o": batch[0]["o"],
            "c": batch[-1]["c"],
            "h": max(b["h"] for b in batch),
            "l": min(b["l"] for b in batch),
            "v": sum(b["v"] for b in batch),
            "_si": i
        })
    return out


# --- 기본 지표 ---
def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i-1]
        if d > 0: gains += d
        else: losses -= d
    if losses == 0: return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _bb(closes, period=20):
    """(bb_pos%, bb_width%) 반환"""
    if len(closes) < period:
        return 50.0, 0.0
    s = closes[-period:]
    m = sum(s) / period
    std = (sum((x - m)**2 for x in s) / period) ** 0.5
    if std == 0 or m == 0:
        return 50.0, 0.0
    upper = m + 2 * std
    lower = m - 2 * std
    pos = (closes[-1] - lower) / (upper - lower) * 100
    width = (upper - lower) / m * 100
    return pos, width


def _ema(closes, period):
    if not closes:
        return 0.0
    if len(closes) < period:
        return closes[-1]
    k = 2.0 / (period + 1)
    e = sum(closes[:period]) / period
    for v in closes[period:]:
        e = v * k + e * (1.0 - k)
    return e


def _sma(values, period):
    if len(values) < period:
        return values[-1] if values else 0.0
    return sum(values[-period:]) / period


def _stochastic(highs, lows, closes, k_period=14, d_period=3):
    """%K, %D 반환"""
    if len(closes) < k_period:
        return 50.0, 50.0
    hh = max(highs[-k_period:])
    ll = min(lows[-k_period:])
    if hh == ll:
        return 50.0, 50.0
    k_val = (closes[-1] - ll) / (hh - ll) * 100
    # 간이 %D (최근 3개 %K 평균으로 근사)
    k_vals = []
    for offset in range(min(d_period, len(closes) - k_period + 1)):
        idx = len(closes) - offset
        h_slice = highs[max(0, idx-k_period):idx]
        l_slice = lows[max(0, idx-k_period):idx]
        c_slice = closes[max(0, idx-k_period):idx]
        if not h_slice:
            continue
        hh2 = max(h_slice)
        ll2 = min(l_slice)
        if hh2 != ll2:
            k_vals.append((c_slice[-1] - ll2) / (hh2 - ll2) * 100)
    d_val = sum(k_vals) / len(k_vals) if k_vals else k_val
    return k_val, d_val


def _macd(closes, fast=12, slow=26, signal=9):
    """MACD line, Signal line, Histogram 반환"""
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = ema_fast - ema_slow
    # 간이 Signal: 최근 signal개 MACD의 EMA
    macd_vals = []
    for offset in range(signal):
        idx = len(closes) - offset
        if idx < slow:
            break
        ef = _ema(closes[:idx], fast)
        es = _ema(closes[:idx], slow)
        macd_vals.append(ef - es)
    macd_vals.reverse()
    sig_line = _ema(macd_vals, signal) if len(macd_vals) >= signal else macd_line
    hist = macd_line - sig_line
    return macd_line, sig_line, hist


def _atr(highs, lows, closes, period=14):
    """Average True Range"""
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(len(closes) - period, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    return sum(trs) / period


def _adx(highs, lows, closes, period=14):
    """간이 ADX (DI+ - DI- 방향성)"""
    if len(closes) < period + 2:
        return 25.0  # 중립
    dp_sum, dm_sum, tr_sum = 0.0, 0.0, 0.0
    for i in range(len(closes) - period, len(closes)):
        up = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        dp = up if (up > down and up > 0) else 0
        dm = down if (down > up and down > 0) else 0
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        dp_sum += dp
        dm_sum += dm
        tr_sum += tr
    if tr_sum == 0:
        return 25.0
    di_p = dp_sum / tr_sum * 100
    di_m = dm_sum / tr_sum * 100
    dx = abs(di_p - di_m) / (di_p + di_m) * 100 if (di_p + di_m) > 0 else 0
    return dx


def _cci(highs, lows, closes, period=20):
    """Commodity Channel Index"""
    if len(closes) < period:
        return 0.0
    tps = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes) - period, len(closes))]
    tp_mean = sum(tps) / period
    md = sum(abs(tp - tp_mean) for tp in tps) / period
    if md == 0:
        return 0.0
    return (tps[-1] - tp_mean) / (0.015 * md)


def _williams_r(highs, lows, closes, period=14):
    """Williams %R"""
    if len(closes) < period:
        return -50.0
    hh = max(highs[-period:])
    ll = min(lows[-period:])
    if hh == ll:
        return -50.0
    return (hh - closes[-1]) / (hh - ll) * -100


def _mfi(highs, lows, closes, volumes, period=14):
    """Money Flow Index"""
    if len(closes) < period + 1:
        return 50.0
    pos_flow, neg_flow = 0.0, 0.0
    for i in range(len(closes) - period, len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        tp_prev = (highs[i-1] + lows[i-1] + closes[i-1]) / 3
        mf = tp * volumes[i]
        if tp > tp_prev:
            pos_flow += mf
        else:
            neg_flow += mf
    if neg_flow == 0:
        return 100.0
    mfr = pos_flow / neg_flow
    return 100.0 - 100.0 / (1.0 + mfr)


def _obv_change(closes, volumes, period=10):
    """OBV 변화율 (최근 period봉)"""
    if len(closes) < period + 1:
        return 0.0
    obv = 0.0
    obv_start = None
    for i in range(len(closes) - period - 1, len(closes)):
        if i < 1:
            continue
        if closes[i] > closes[i-1]:
            obv += volumes[i]
        elif closes[i] < closes[i-1]:
            obv -= volumes[i]
        if obv_start is None:
            obv_start = obv
    if obv_start is None or obv_start == 0:
        return 0.0 if obv == 0 else (1.0 if obv > 0 else -1.0)
    return (obv - obv_start) / abs(obv_start) * 100


def _disparity(closes, period=20):
    """이격도: 현재가 / MA × 100"""
    if len(closes) < period:
        return 100.0
    ma = sum(closes[-period:]) / period
    if ma == 0:
        return 100.0
    return closes[-1] / ma * 100


def _ema_alignment(closes):
    """EMA 정배열 점수 (-4 ~ +4). 양수=정배열(상승), 음수=역배열(하락)"""
    if len(closes) < 50:
        return 0
    e5 = _ema(closes, 5)
    e10 = _ema(closes, 10)
    e20 = _ema(closes, 20)
    e50 = _ema(closes, 50)
    score = 0
    if e5 > e10: score += 1
    else: score -= 1
    if e10 > e20: score += 1
    else: score -= 1
    if e20 > e50: score += 1
    else: score -= 1
    if closes[-1] > e5: score += 1
    else: score -= 1
    return score


def _volatility(closes, period=20):
    """변동성 (표준편차 / 평균 × 100)"""
    if len(closes) < period:
        return 0.0
    s = closes[-period:]
    m = sum(s) / period
    if m == 0:
        return 0.0
    std = (sum((x - m)**2 for x in s) / period) ** 0.5
    return std / m * 100


# --- 캔들 패턴 ---
def _candle_patterns(candles, idx):
    """
    현재 캔들 + 이전 캔들 기반 패턴 감지
    반환: dict of (패턴명 → 0 or 1)
    """
    patterns = {}
    if idx < 2 or idx >= len(candles):
        return {
            "hammer": 0, "inv_hammer": 0, "doji": 0, "engulf_bull": 0,
            "engulf_bear": 0, "consec_green": 0, "consec_red": 0,
            "morning_star": 0, "upper_wick_pct": 0, "lower_wick_pct": 0,
            "body_pct": 0,
        }

    c = candles[idx]
    p1 = candles[idx - 1]
    p2 = candles[idx - 2]

    o, h, l, cl = c["o"], c["h"], c["l"], c["c"]
    rng = h - l if h != l else 0.0001
    body = abs(cl - o)
    upper_wick = h - max(o, cl)
    lower_wick = min(o, cl) - l

    # 망치형 (Hammer): 아래꼬리 길고, 몸통 작고, 하단
    patterns["hammer"] = 1 if (lower_wick > body * 2 and upper_wick < body * 0.5 and body > 0) else 0

    # 역망치형 (Inverted Hammer)
    patterns["inv_hammer"] = 1 if (upper_wick > body * 2 and lower_wick < body * 0.5 and body > 0) else 0

    # 도지 (Doji): 몸통이 전체 범위의 10% 이하
    patterns["doji"] = 1 if (body / rng < 0.1) else 0

    # 상승 감싸기 (Bullish Engulfing)
    p1_body = abs(p1["c"] - p1["o"])
    patterns["engulf_bull"] = 1 if (
        p1["c"] < p1["o"] and  # 이전봉 음봉
        cl > o and             # 현재봉 양봉
        body > p1_body and     # 몸통 더 큼
        o <= p1["c"] and cl >= p1["o"]  # 감싸기
    ) else 0

    # 하락 감싸기
    patterns["engulf_bear"] = 1 if (
        p1["c"] > p1["o"] and
        cl < o and
        body > p1_body and
        o >= p1["c"] and cl <= p1["o"]
    ) else 0

    # 연속 양봉 수 (최대 5봉 체크)
    consec_green = 0
    for k in range(idx, max(idx - 5, -1), -1):
        if candles[k]["c"] > candles[k]["o"]:
            consec_green += 1
        else:
            break
    patterns["consec_green"] = consec_green

    # 연속 음봉 수
    consec_red = 0
    for k in range(idx - 1, max(idx - 6, -1), -1):
        if candles[k]["c"] < candles[k]["o"]:
            consec_red += 1
        else:
            break
    patterns["consec_red"] = consec_red

    # 샛별형 (Morning Star) 간이
    patterns["morning_star"] = 1 if (
        p2["c"] < p2["o"] and                       # 2봉전 음봉
        abs(p1["c"] - p1["o"]) / rng < 0.2 and      # 1봉전 작은 몸통
        cl > o and body / rng > 0.3                  # 현재 양봉
    ) else 0

    # 꼬리 비율
    patterns["upper_wick_pct"] = round(upper_wick / rng * 100, 1)
    patterns["lower_wick_pct"] = round(lower_wick / rng * 100, 1)
    patterns["body_pct"] = round(body / rng * 100, 1)

    return patterns


# --- 다중 타임프레임 피처 추출 ---
def extract_features_multi_tf(coin, candles_by_tf, sig_time, sig_bar_i5):
    """
    모든 타임프레임에서 해당 시점의 지표를 추출.
    candles_by_tf: {1: [...], 3: [...], 5: [...], 15: [...], 30: [...], 60: [...]}
    sig_time: 신호 캔들의 시간 문자열
    sig_bar_i5: 5분봉 인덱스
    반환: flat dict of features
    """
    feat = {}

    for tf, candles in candles_by_tf.items():
        if not candles or len(candles) < 60:
            continue

        # 해당 시점 인덱스 찾기
        if tf == 5:
            idx = sig_bar_i5
        else:
            # 시간 기반 매칭: sig_time 이하인 가장 마지막 캔들
            idx = _find_candle_idx(candles, sig_time)

        if idx is None or idx < 55:
            continue

        pfx = f"tf{tf}_"  # prefix: tf1_, tf3_, tf5_ ...

        # 종가/고가/저가/거래량 배열 (최근 55봉)
        start = max(0, idx - 54)
        closes = [candles[j]["c"] for j in range(start, idx + 1)]
        highs = [candles[j]["h"] for j in range(start, idx + 1)]
        lows = [candles[j]["l"] for j in range(start, idx + 1)]
        volumes = [candles[j]["v"] for j in range(start, idx + 1)]

        if len(closes) < 30:
            continue

        # === RSI ===
        feat[pfx + "rsi7"] = round(_rsi(closes, 7), 1)
        feat[pfx + "rsi14"] = round(_rsi(closes, 14), 1)
        feat[pfx + "rsi21"] = round(_rsi(closes, 21), 1)

        # === Bollinger Bands ===
        bb10_pos, bb10_w = _bb(closes, 10)
        bb20_pos, bb20_w = _bb(closes, 20)
        feat[pfx + "bb10_pos"] = round(bb10_pos, 1)
        feat[pfx + "bb10_width"] = round(bb10_w, 3)
        feat[pfx + "bb20_pos"] = round(bb20_pos, 1)
        feat[pfx + "bb20_width"] = round(bb20_w, 3)

        # === MACD ===
        macd_l, macd_s, macd_h = _macd(closes)
        price = closes[-1] if closes[-1] != 0 else 1
        feat[pfx + "macd_hist"] = round(macd_h / price * 10000, 2)  # 정규화 (만분율)
        feat[pfx + "macd_cross"] = 1 if macd_l > macd_s else 0  # 골든크로스 여부

        # === Stochastic ===
        stoch_k, stoch_d = _stochastic(highs, lows, closes)
        feat[pfx + "stoch_k"] = round(stoch_k, 1)
        feat[pfx + "stoch_d"] = round(stoch_d, 1)

        # === ATR ===
        atr = _atr(highs, lows, closes, 14)
        feat[pfx + "atr_pct"] = round(atr / price * 100, 3) if price > 0 else 0

        # === ADX ===
        feat[pfx + "adx"] = round(_adx(highs, lows, closes, 14), 1)

        # === CCI ===
        feat[pfx + "cci"] = round(_cci(highs, lows, closes, 20), 1)

        # === Williams %R ===
        feat[pfx + "willr"] = round(_williams_r(highs, lows, closes, 14), 1)

        # === MFI ===
        feat[pfx + "mfi"] = round(_mfi(highs, lows, closes, volumes, 14), 1)

        # === OBV 변화율 ===
        feat[pfx + "obv_chg"] = round(_obv_change(closes, volumes, 10), 2)

        # === 이격도 ===
        feat[pfx + "disp10"] = round(_disparity(closes, 10), 2)
        feat[pfx + "disp20"] = round(_disparity(closes, 20), 2)

        # === EMA 정배열 점수 ===
        feat[pfx + "ema_align"] = _ema_alignment(closes)

        # === EMA 개별값 (현재가 대비 %) ===
        for ep in [5, 10, 20, 50]:
            ev = _ema(closes, ep)
            feat[pfx + f"ema{ep}_dist"] = round((closes[-1] - ev) / ev * 100, 3) if ev > 0 else 0

        # === 변동성 ===
        feat[pfx + "vol10"] = round(_volatility(closes, 10), 3)
        feat[pfx + "vol20"] = round(_volatility(closes, 20), 3)

        # === 거래량 관련 ===
        avg_v5 = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else 1
        feat[pfx + "vr"] = round(volumes[-1] / avg_v5, 2) if avg_v5 > 0 else 1
        avg_v20 = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else 1
        feat[pfx + "vr20"] = round(volumes[-1] / avg_v20, 2) if avg_v20 > 0 else 1

        # === 모멘텀 ===
        for mp in [3, 5, 10, 20]:
            if len(closes) > mp and closes[-(mp+1)] > 0:
                feat[pfx + f"mom{mp}"] = round(
                    (closes[-1] - closes[-(mp+1)]) / closes[-(mp+1)] * 100, 3
                )
            else:
                feat[pfx + f"mom{mp}"] = 0.0

        # === 최근 N봉 고저 대비 위치 ===
        for np in [10, 20]:
            if len(closes) >= np:
                hh = max(closes[-np:])
                ll = min(closes[-np:])
                feat[pfx + f"pos{np}"] = round(
                    (closes[-1] - ll) / (hh - ll) * 100, 1
                ) if hh != ll else 50.0
            else:
                feat[pfx + f"pos{np}"] = 50.0

        # === 캔들 패턴 ===
        cp = _candle_patterns(candles, idx)
        for k, v in cp.items():
            feat[pfx + k] = v

        # === 봉 크기 (현재 봉의 range / ATR) ===
        cur_range = candles[idx]["h"] - candles[idx]["l"]
        feat[pfx + "range_atr"] = round(cur_range / atr, 2) if atr > 0 else 1.0

        # === 가격 변화 방향 ===
        feat[pfx + "is_green"] = 1 if candles[idx]["c"] > candles[idx]["o"] else 0

    return feat


def _find_candle_idx(candles, sig_time):
    """sig_time 이하인 가장 마지막 캔들 인덱스 (이진 탐색)"""
    lo, hi = 0, len(candles) - 1
    result = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if candles[mid]["t"] <= sig_time:
            result = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return result


# --- BTC 레짐 분류 ---
def classify_btc_regime(btc_5m):
    regime = {}
    for i in range(60, len(btc_5m)):
        ret = (btc_5m[i]["c"] - btc_5m[i-60]["c"]) / btc_5m[i-60]["c"] * 100
        t = btc_5m[i]["t"][:16]
        if ret > 0.5:
            regime[t] = "상승"
        elif ret < -0.5:
            regime[t] = "하락"
        else:
            regime[t] = "횡보"
    return regime


# ================================================================
# PART 3: 신호 감지 + 시뮬레이션
# ================================================================
def process_coin(coin, btc_regime):
    """코인 하나 처리: 데이터 로드 → 신호 감지 → 다중TF 피처 추출 → 청산 시뮬"""

    # 모든 타임프레임 로드
    candles_by_tf = {}
    for tf, _ in TIMEFRAMES:
        data = load_coin_data(coin, tf)
        if data and len(data) >= 60:
            candles_by_tf[tf] = data

    c5 = candles_by_tf.get(5, [])
    if len(c5) < 200:
        return {}

    # 15분봉 (없으면 합성)
    c15 = candles_by_tf.get(15, [])
    if not c15 or len(c15) < 60:
        c15 = make_nmin(c5, 3)
        candles_by_tf[15] = c15

    # 30분봉 (없으면 합성)
    if 30 not in candles_by_tf or len(candles_by_tf[30]) < 60:
        candles_by_tf[30] = make_nmin(c5, 6)

    # 1시간봉 (없으면 합성)
    if 60 not in candles_by_tf or len(candles_by_tf[60]) < 60:
        candles_by_tf[60] = make_nmin(c5, 12)

    results = {}

    results["15m_눌림반전"] = _scan_15m_reversal(coin, c5, c15, candles_by_tf, btc_regime, require_break=False)
    results["15m_눌림+돌파"] = _scan_15m_reversal(coin, c5, c15, candles_by_tf, btc_regime, require_break=True)
    results["5m_양봉"] = _scan_5m_green(coin, c5, c15, candles_by_tf, btc_regime)
    results["20봉_고점돌파"] = _scan_breakout(coin, c5, c15, candles_by_tf, btc_regime)
    results["거래량3배"] = _scan_vol_spike(coin, c5, c15, candles_by_tf, btc_regime)

    # 메모리 해제
    del candles_by_tf, c5, c15
    gc.collect()
    return results


def _scan_15m_reversal(coin, c5, c15, candles_by_tf, btc_regime, require_break=False):
    sigs = []
    last_i5 = -20
    for i in range(14, len(c15)):
        bar, prev = c15[i], c15[i-1]
        if prev["c"] >= prev["o"]: continue
        if bar["c"] <= bar["o"]: continue
        body_pct = (bar["c"] - bar["o"]) / bar["o"] * 100
        if body_pct < 0.1: continue
        if require_break and bar["c"] <= prev["h"]: continue

        si = bar.get("_si", i * 3) + 2
        entry_idx = si + 1
        if entry_idx - last_i5 < 10: continue
        if entry_idx >= len(c5) - MAX_HOLD_BARS: continue
        last_i5 = entry_idx

        sig = _build_signal(coin, c5, candles_by_tf, si, entry_idx, bar, btc_regime)
        if sig:
            sigs.append(sig)
    return sigs


def _scan_5m_green(coin, c5, c15, candles_by_tf, btc_regime):
    sigs = []
    last = -20
    for i in range(30, len(c5) - MAX_HOLD_BARS - 1):
        bar = c5[i]
        if bar["c"] <= bar["o"]: continue
        if (bar["c"] - bar["o"]) / bar["o"] * 100 < 0.1: continue
        entry_idx = i + 1
        if entry_idx - last < 10: continue
        last = entry_idx
        sig = _build_signal(coin, c5, candles_by_tf, i, entry_idx, bar, btc_regime)
        if sig:
            sigs.append(sig)
    return sigs


def _scan_breakout(coin, c5, c15, candles_by_tf, btc_regime):
    sigs = []
    last = -20
    for i in range(30, len(c5) - MAX_HOLD_BARS - 1):
        bar = c5[i]
        if bar["c"] <= bar["o"]: continue
        prev_high = max(c5[j]["h"] for j in range(max(0, i-20), i))
        if bar["h"] <= prev_high: continue
        entry_idx = i + 1
        if entry_idx - last < 10: continue
        last = entry_idx
        sig = _build_signal(coin, c5, candles_by_tf, i, entry_idx, bar, btc_regime)
        if sig:
            sigs.append(sig)
    return sigs


def _scan_vol_spike(coin, c5, c15, candles_by_tf, btc_regime):
    sigs = []
    last = -20
    for i in range(30, len(c5) - MAX_HOLD_BARS - 1):
        bar = c5[i]
        if bar["c"] <= bar["o"]: continue
        vols = [c5[j]["v"] for j in range(max(0, i-5), i)]
        avg_vol = sum(vols) / max(len(vols), 1)
        if avg_vol == 0 or bar["v"] < avg_vol * 3: continue
        entry_idx = i + 1
        if entry_idx - last < 10: continue
        last = entry_idx
        sig = _build_signal(coin, c5, candles_by_tf, i, entry_idx, bar, btc_regime)
        if sig:
            sigs.append(sig)
    return sigs


def _build_signal(coin, c5, candles_by_tf, sig_bar_i5, entry_idx, sig_bar, btc_regime):
    entry_price = c5[entry_idx]["o"]
    if entry_price <= 0:
        return None

    try:
        hour = int(sig_bar["t"][11:13])
    except:
        hour = 12

    time_key = sig_bar["t"][:16]
    regime = btc_regime.get(time_key, "횡보")

    # --- 다중 타임프레임 피처 추출 ---
    feat = extract_features_multi_tf(coin, candles_by_tf, sig_bar["t"], sig_bar_i5)

    # --- 기본 패턴 (하위 호환) ---
    dip3 = 1 if (sig_bar_i5 >= 3 and c5[sig_bar_i5 - 3]["c"] > c5[sig_bar_i5 - 1]["c"]) else 0
    brk = 1 if (sig_bar_i5 >= 1 and sig_bar["c"] > c5[sig_bar_i5 - 1]["h"]) else 0

    # --- MFE/MAE ---
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

    # --- 다중 청산전략 ---
    exits = {}
    cost_pct = TOTAL_COST * 100

    for tp in [0.3, 0.5, 0.7, 1.0, 1.5]:
        for sl in [0.3, 0.5, 0.7, 1.0]:
            exits[f"FIX_TP{tp}/SL{sl}"] = _sim_fixed_tpsl(c5, entry_idx, entry_price, tp, sl, max_bars, cost_pct)

    for sl in [0.5, 0.7, 1.0]:
        for arm in [0.2, 0.3, 0.5]:
            for trail in [0.1, 0.15, 0.2, 0.3]:
                if trail > arm: continue
                exits[f"TRAIL_SL{sl}/A{arm}/T{trail}"] = _sim_trail(c5, entry_idx, entry_price, sl, arm, trail, max_bars, cost_pct)

    for n in [6, 12, 18, 24, 36]:
        if n <= max_bars:
            exit_close = c5[entry_idx + n]["c"]
            raw = (exit_close - entry_price) / entry_price * 100
            exits[f"HOLD_{n}봉"] = round(raw - cost_pct, 4)

    for n in [12, 24]:
        for sl in [0.7, 1.0]:
            exits[f"TIME{n}_SL{sl}"] = _sim_timestop(c5, entry_idx, entry_price, sl, n, max_bars, cost_pct)

    result = {
        "coin": coin, "time": sig_bar["t"], "hour": hour,
        "entry": entry_price, "regime": regime,
        "dip3": dip3, "brk": brk,
        "mfe": round(mfe, 4), "mae": round(mae, 4), "mfe_bar": mfe_bar,
        "exits": exits,
    }
    # 다중 TF 피처 병합
    result.update(feat)
    return result


# --- 청산 시뮬레이터 ---
def _sim_fixed_tpsl(c5, entry_idx, entry, tp, sl, max_bars, cost):
    for j in range(1, max_bars + 1):
        fc = c5[entry_idx + j]
        lo_pct = (fc["l"] - entry) / entry * 100
        hi_pct = (fc["h"] - entry) / entry * 100
        if lo_pct <= -sl:
            return round(-sl - cost, 4)
        if hi_pct >= tp:
            return round(tp - cost, 4)
    exit_c = c5[entry_idx + max_bars]["c"]
    return round((exit_c - entry) / entry * 100 - cost, 4)


def _sim_trail(c5, entry_idx, entry, sl, arm, trail, max_bars, cost):
    max_pct = 0
    armed = False
    for j in range(1, max_bars + 1):
        fc = c5[entry_idx + j]
        hi_pct = (fc["h"] - entry) / entry * 100
        lo_pct = (fc["l"] - entry) / entry * 100
        if hi_pct > max_pct: max_pct = hi_pct
        if lo_pct <= -sl:
            return round(-sl - cost, 4)
        if max_pct >= arm: armed = True
        if armed and (max_pct - lo_pct) >= trail:
            return round(max(max_pct - trail, -sl) - cost, 4)
    exit_c = c5[entry_idx + max_bars]["c"]
    return round((exit_c - entry) / entry * 100 - cost, 4)


def _sim_timestop(c5, entry_idx, entry, sl, n_bars, max_bars, cost):
    hold = min(n_bars, max_bars)
    for j in range(1, hold + 1):
        fc = c5[entry_idx + j]
        if (fc["l"] - entry) / entry * 100 <= -sl:
            return round(-sl - cost, 4)
    exit_c = c5[entry_idx + hold]["c"]
    return round((exit_c - entry) / entry * 100 - cost, 4)


# ================================================================
# PART 4: 통계 + 리포트
# ================================================================
def calc_metrics(pnls):
    if not pnls:
        return None
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins) / n * 100
    ev = sum(pnls) / n
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    rr = avg_win / avg_loss if avg_loss > 0 else float('inf')
    total_gain = sum(wins)
    total_loss = abs(sum(losses))
    pf = total_gain / total_loss if total_loss > 0 else float('inf')

    cumulative = peak = mdd = 0
    for p in pnls:
        cumulative += p
        if cumulative > peak: peak = cumulative
        dd = peak - cumulative
        if dd > mdd: mdd = dd

    return {
        "n": n, "wr": wr, "ev": ev, "pf": pf, "mdd": mdd, "rr": rr,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "total_pnl": sum(pnls), "total_gain": total_gain, "total_loss": total_loss,
    }


def fmt_metrics(m, indent=""):
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


def generate_report(all_results):
    lines = []
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    lines.append(f"업비트 신호 연구 v3.0 리포트 ({ts})")
    lines.append(f"타임프레임: {', '.join(f'{tf}m' for tf,_ in TIMEFRAMES)}")
    lines.append(f"수수료: {TOTAL_COST*100:.2f}% | 진입: 다음캔들시가 | 최대보유: {MAX_HOLD_BARS}봉")
    lines.append(f"최소 거래수: {MIN_TRADES} | Train/Test: {TRAIN_RATIO*100:.0f}/{(1-TRAIN_RATIO)*100:.0f}")
    lines.append("=" * 70)

    # 피처 목록 파악
    sample_sig = None
    for sigs in all_results.values():
        if sigs:
            sample_sig = sigs[0]
            break
    if sample_sig:
        tf_feats = [k for k in sample_sig.keys() if k.startswith("tf")]
        lines.append(f"\n추출된 피처 수: {len(tf_feats)}개 (다중 타임프레임)")
        # 타임프레임별 피처 수
        for tf, _ in TIMEFRAMES:
            cnt = sum(1 for k in tf_feats if k.startswith(f"tf{tf}_"))
            if cnt > 0:
                lines.append(f"  {tf}분봉: {cnt}개")

    default_exits = ["TRAIL_SL0.7/A0.3/T0.2", "FIX_TP0.7/SL0.7", "HOLD_12봉"]

    # [1] 신호 유형별 비교
    lines.append(f"\n[1] 신호 유형별 비교")
    lines.append("-" * 70)
    for de in default_exits:
        lines.append(f"\n  전략: {de}")
        ranked = []
        for stype, sigs in all_results.items():
            if not sigs: continue
            pnls = [s["exits"].get(de, 0) for s in sigs]
            m = calc_metrics(pnls)
            if m:
                ranked.append((stype, m))
        ranked.sort(key=lambda x: -x[1]["ev"])
        for stype, m in ranked:
            star = " ***" if m["ev"] > 0 and m["n"] >= MIN_TRADES else ""
            lines.append(f"    {stype:<18s} {fmt_metrics(m)}{star}")

    # [2] 각 신호별 상세
    for stype, sigs in all_results.items():
        if len(sigs) < 30:
            continue
        lines.append(f"\n{'='*70}")
        lines.append(f"[2] {stype} 상세 (전체 n={len(sigs)})")
        lines.append(f"{'='*70}")

        split_idx = int(len(sigs) * TRAIN_RATIO)
        train_sigs = sigs[:split_idx]
        test_sigs = sigs[split_idx:]

        for subset_name, subset in [("TRAIN", train_sigs), ("TEST", test_sigs), ("전체", sigs)]:
            if len(subset) < 10: continue
            lines.append(f"\n  --- {subset_name} (n={len(subset)}) ---")

            # 최적 청산전략 TOP10
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

            if subset_name != "전체":
                continue

            # MFE/MAE 분포
            mfes = sorted([s["mfe"] for s in subset])
            maes = sorted([s["mae"] for s in subset])
            lines.append(f"\n  [MFE 분포]")
            for pct in [10, 25, 50, 75, 90]:
                idx = min(int(len(mfes) * pct / 100), len(mfes) - 1)
                lines.append(f"    P{pct:2d}: {mfes[idx]:>+.3f}%")
            lines.append(f"\n  [MAE 분포]")
            for pct in [10, 25, 50, 75, 90]:
                idx = min(int(len(maes) * pct / 100), len(maes) - 1)
                lines.append(f"    P{pct:2d}: {maes[idx]:>+.3f}%")

        # --- 조건별 EV 분석 (다중 타임프레임 피처 활용) ---
        dk = "TRAIL_SL0.7/A0.3/T0.2"

        def cond_ev(name, fn, sig_list=sigs):
            sub = [s for s in sig_list if fn(s)]
            if len(sub) < 10: return
            pnls = [s["exits"].get(dk, 0) for s in sub]
            m = calc_metrics(pnls)
            if m:
                star = " ***" if m["ev"] > 0 and m["n"] >= MIN_TRADES else ""
                lines.append(f"    {name:35s} {fmt_metrics(m, '')}{star}")

        # 각 타임프레임별 RSI/BB/Stoch/MACD 분석
        for tf, _ in TIMEFRAMES:
            pfx = f"tf{tf}_"
            rsi_key = pfx + "rsi14"
            bb_key = pfx + "bb20_pos"
            stoch_key = pfx + "stoch_k"
            macd_key = pfx + "macd_cross"
            adx_key = pfx + "adx"
            mfi_key = pfx + "mfi"
            cci_key = pfx + "cci"
            ema_key = pfx + "ema_align"

            # RSI가 있는 신호만 (해당 TF 데이터가 있는 경우)
            has_tf = [s for s in sigs if rsi_key in s]
            if len(has_tf) < 30:
                continue

            lines.append(f"\n    [{tf}분봉 지표]")

            # RSI 구간
            for lo, hi in [(0, 30), (30, 50), (50, 70), (70, 100)]:
                cond_ev(f"{tf}m RSI14 {lo}-{hi}",
                    lambda s, lo=lo, hi=hi, k=rsi_key: k in s and lo <= s[k] < hi)

            # BB 위치
            for lo, hi in [(0, 20), (20, 50), (50, 80), (80, 100)]:
                cond_ev(f"{tf}m BB20 {lo}-{hi}",
                    lambda s, lo=lo, hi=hi, k=bb_key: k in s and lo <= s[k] < hi)

            # Stochastic 과매도/과매수
            cond_ev(f"{tf}m Stoch<20 (과매도)",
                lambda s, k=stoch_key: k in s and s[k] < 20)
            cond_ev(f"{tf}m Stoch>80 (과매수)",
                lambda s, k=stoch_key: k in s and s[k] > 80)

            # MACD 골든크로스
            cond_ev(f"{tf}m MACD 골든크로스",
                lambda s, k=macd_key: k in s and s[k] == 1)

            # ADX 추세 강도
            cond_ev(f"{tf}m ADX>25 (추세중)",
                lambda s, k=adx_key: k in s and s[k] > 25)

            # MFI
            cond_ev(f"{tf}m MFI<20 (자금유출)",
                lambda s, k=mfi_key: k in s and s[k] < 20)
            cond_ev(f"{tf}m MFI>80 (자금유입)",
                lambda s, k=mfi_key: k in s and s[k] > 80)

            # EMA 정배열
            cond_ev(f"{tf}m EMA 정배열(3+)",
                lambda s, k=ema_key: k in s and s[k] >= 3)
            cond_ev(f"{tf}m EMA 역배열(-3이하)",
                lambda s, k=ema_key: k in s and s[k] <= -3)

        # BTC 레짐
        lines.append(f"\n    [BTC 시장 레짐]")
        for reg in ["상승", "횡보", "하락"]:
            cond_ev(f"BTC_{reg}", lambda s, r=reg: s["regime"] == r)

        # 시간대
        lines.append(f"\n    [시간대]")
        for h in range(24):
            cond_ev(f"{h:02d}시", lambda s, h=h: s["hour"] == h)

        # 진입 패턴
        lines.append(f"\n    [진입 패턴]")
        cond_ev("눌림3봉(dip3)", lambda s: s.get("dip3") == 1)
        cond_ev("이전봉돌파(brk)", lambda s: s.get("brk") == 1)
        cond_ev("눌림+돌파", lambda s: s.get("dip3") == 1 and s.get("brk") == 1)

        # 캔들 패턴 (5분봉 기준)
        lines.append(f"\n    [캔들 패턴 (5분봉)]")
        for pat in ["hammer", "inv_hammer", "doji", "engulf_bull", "morning_star"]:
            cond_ev(f"5m_{pat}", lambda s, p="tf5_"+pat: s.get(p) == 1)

        # ★ 복합 필터 (다중 TF 조합)
        lines.append(f"\n    [복합 필터 — 다중 타임프레임 조합]")
        multi_combos = [
            ("5m_RSI<50+15m_BB<40",
                lambda s: s.get("tf5_rsi14", 50) < 50 and s.get("tf15_bb20_pos", 50) < 40),
            ("5m_RSI<50+15m_BB<30",
                lambda s: s.get("tf5_rsi14", 50) < 50 and s.get("tf15_bb20_pos", 50) < 30),
            ("15m_RSI<40+30m_RSI<40",
                lambda s: s.get("tf15_rsi14", 50) < 40 and s.get("tf30_rsi14", 50) < 40),
            ("5m_Stoch<20+15m_BB<40",
                lambda s: s.get("tf5_stoch_k", 50) < 20 and s.get("tf15_bb20_pos", 50) < 40),
            ("15m_MACD골든+1h_EMA정배열",
                lambda s: s.get("tf15_macd_cross") == 1 and s.get("tf60_ema_align", 0) >= 3),
            ("5m_망치형+15m_BB<30",
                lambda s: s.get("tf5_hammer") == 1 and s.get("tf15_bb20_pos", 50) < 30),
            ("15m_BB<20+30m_RSI<40+BTC상승",
                lambda s: s.get("tf15_bb20_pos", 50) < 20 and s.get("tf30_rsi14", 50) < 40 and s["regime"] == "상승"),
            ("5m_감싸기+15m_RSI<50+1h_정배열",
                lambda s: s.get("tf5_engulf_bull") == 1 and s.get("tf15_rsi14", 50) < 50 and s.get("tf60_ema_align", 0) >= 2),
            ("15m_MFI<20+30m_BB<30",
                lambda s: s.get("tf15_mfi", 50) < 20 and s.get("tf30_bb20_pos", 50) < 30),
            ("1m_RSI<30+5m_RSI<40+15m_BB<40",
                lambda s: s.get("tf1_rsi14", 50) < 30 and s.get("tf5_rsi14", 50) < 40 and s.get("tf15_bb20_pos", 50) < 40),
            ("3m_Stoch<20+15m_MACD골든",
                lambda s: s.get("tf3_stoch_k", 50) < 20 and s.get("tf15_macd_cross") == 1),
            ("5m_RSI<40+눌림+돌파+BTC상승",
                lambda s: s.get("tf5_rsi14", 50) < 40 and s.get("dip3") == 1 and s.get("brk") == 1 and s["regime"] == "상승"),
        ]
        for name, fn in multi_combos:
            cond_ev(name, fn)

        # Train/Test 복합필터 비교
        lines.append(f"\n  [복합 필터 × 최적 전략 — Train vs Test]")
        for fname, ffn in multi_combos:
            train_sub = [s for s in train_sigs if ffn(s)]
            test_sub = [s for s in test_sigs if ffn(s)]
            if len(train_sub) < 15 or len(test_sub) < 5:
                continue
            lines.append(f"\n    필터: {fname}")
            best_ek, best_ev = None, -999
            for ek in exit_keys:
                pnls = [s["exits"].get(ek, 0) for s in train_sub]
                m = calc_metrics(pnls)
                if m and m["ev"] > best_ev:
                    best_ev = m["ev"]
                    best_ek = ek
            if best_ek:
                tr_m = calc_metrics([s["exits"].get(best_ek, 0) for s in train_sub])
                te_m = calc_metrics([s["exits"].get(best_ek, 0) for s in test_sub])
                lines.append(f"      전략: {best_ek}")
                lines.append(f"      TRAIN: {fmt_metrics(tr_m)}")
                lines.append(f"      TEST:  {fmt_metrics(te_m)}")
                if tr_m and te_m and tr_m["ev"] > 0 and te_m["ev"] > 0:
                    lines.append(f"      >>> Train+Test 모두 EV>0 <<<")

    # [3] 최종 결론
    lines.append(f"\n{'='*70}")
    lines.append("[3] 최종 결론 — EV>0 & n>=100 (TEST 구간)")
    lines.append("=" * 70)
    lines.append(f"  우선순위: EV > MDD > PF > 거래수 > WR")
    lines.append(f"  수수료: {TOTAL_COST*100:.2f}%/거래")

    found = False
    for stype, sigs in all_results.items():
        if len(sigs) < MIN_TRADES: continue
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
        lines.append("  → 신호 정의 또는 필터 재설계 필요")

    return lines


# ================================================================
# 메인
# ================================================================
def main():
    parser = argparse.ArgumentParser(description="업비트 신호 연구 v3.0")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--coins", type=int, default=30)
    parser.add_argument("--skip-collect", action="store_true")
    args = parser.parse_args()

    t0 = time.time()

    if not args.skip_collect:
        collect(days=args.days, top_n=args.coins)

    coins = get_saved_coins()
    if not coins:
        tg(f"[오류] 데이터 없음: {DATA_DIR}")
        return

    tg(f"\n[분석] {len(coins)}개 코인, {len(TIMEFRAMES)}개 타임프레임")

    # BTC 레짐
    btc_5m = load_coin_data("BTC", 5)
    btc_regime = classify_btc_regime(btc_5m) if btc_5m else {}
    if btc_regime:
        tg(f"  BTC 레짐: {len(btc_regime)}시점")
    del btc_5m

    all_results = defaultdict(list)

    for i, coin in enumerate(coins):
        try:
            coin_results = process_coin(coin, btc_regime)
            for stype, sigs in coin_results.items():
                all_results[stype].extend(sigs)
        except Exception as e:
            import traceback
            tg(f"[분석 에러] {coin}: {e}\n{traceback.format_exc()[-300:]}")
            continue
        if (i + 1) % 5 == 0:
            counts = {k: len(v) for k, v in all_results.items()}
            tg(f"  분석 진행 {i+1}/{len(coins)}: {counts}")

    for stype in all_results:
        all_results[stype].sort(key=lambda s: s["time"])

    tg(f"\n[신호 감지 완료]")
    for stype, sigs in all_results.items():
        tg(f"  {stype}: {len(sigs)}건")

    # 피처 수 보고
    for stype, sigs in all_results.items():
        if sigs:
            tf_feats = [k for k in sigs[0].keys() if k.startswith("tf")]
            tg(f"  피처 수: {len(tf_feats)}개 (다중 타임프레임)")
            break

    tg(f"[리포트 생성 시작]")
    try:
        lines = generate_report(dict(all_results))
    except Exception as e:
        import traceback
        tg(f"[리포트 생성 에러] {e}\n{traceback.format_exc()[-500:]}")
        lines = [f"리포트 생성 실패: {e}"]

    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now(KST).strftime("%Y%m%d_%H%M")
    fpath = os.path.join(OUT_DIR, f"signal_study_v3_{ts}.txt")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    elapsed = (time.time() - t0) / 60
    tg(f"\n[완료] {elapsed:.1f}분 소요")
    tg(f"리포트: {fpath}")

    # 리포트 분할 전송 (텔레그램 4000자 제한)
    full_report = "\n".join(lines)
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > 3800:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    for ci, chunk in enumerate(chunks):
        tg(f"[리포트 {ci+1}/{len(chunks)}]\n{chunk}")

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
