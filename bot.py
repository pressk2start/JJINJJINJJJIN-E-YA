# -*- coding: utf-8 -*-
"""
업비트 24시간 종합 캔들 분석 v3
================================
- 거래량 상위 50종목
- 1분/5분/15분/1시간/일봉 + 틱(3000건+) + 호가창
- 캔들패턴 + BTC상관관계 + 거래량프로파일 + 모든지표
- 분석 각 섹션마다 텔레그램 즉시 전송 (크래시 방지)
- 재시작 방지 (완료 후 대기모드)
"""
import requests, time, statistics, json, os, sys, traceback, math
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ================================================================
# 환경변수 (bot.py와 동일)
# ================================================================
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN") or ""
_raw = os.getenv("TG_CHATS") or os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TG_CHAT") or ""
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
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": cid, "text": msg[:4000], "disable_web_page_preview": True}, timeout=10)
        except: pass

# ================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_JSON = os.path.join(SCRIPT_DIR, "analysis_results.json")
RESULT_TXT  = os.path.join(SCRIPT_DIR, "analysis_results.txt")

KST = timezone(timedelta(hours=9))
BASE = "https://api.upbit.com/v1"
TOP_N = 50
FEE = 0.0005; SLIP = 0.0008

def safe_get(url, params=None, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200: return r.json()
            if r.status_code == 429: time.sleep(1+i); continue
            time.sleep(0.3)
        except: time.sleep(0.5)
    return None

# ================================================================
# 1) 종목 선정
# ================================================================
def get_top_markets(n=50):
    tickers = safe_get(f"{BASE}/market/all", {"isDetails":"true"})
    if not tickers: return []
    krw = [t["market"] for t in tickers if t["market"].startswith("KRW-")]
    stable = {"USDT","USDC","DAI","TUSD","BUSD"}
    krw = [m for m in krw if m.split("-")[1] not in stable]
    all_t = []
    for i in range(0, len(krw), 100):
        b = safe_get(f"{BASE}/ticker", {"markets":",".join(krw[i:i+100])})
        if b: all_t.extend(b)
        time.sleep(0.12)
    all_t.sort(key=lambda x: x.get("acc_trade_price_24h",0), reverse=True)
    return [t["market"] for t in all_t[:n]]

# ================================================================
# 2) 데이터 수집
# ================================================================
def get_all_candles(unit, market, total):
    result, to = [], None
    while len(result) < total:
        p = {"market": market, "count": min(200, total-len(result))}
        if to: p["to"] = to
        b = safe_get(f"{BASE}/candles/minutes/{unit}", p)
        if not b: break
        result.extend(b)
        to = b[-1]["candle_date_time_utc"] + "Z"
        time.sleep(0.12)
    result.sort(key=lambda x: x["candle_date_time_kst"])
    return result[:total]

def get_daily_candles(market, count=7):
    """일봉 (매크로 추세용)"""
    return safe_get(f"{BASE}/candles/days", {"market": market, "count": count}) or []

def get_ticks_paginated(market, target=3000):
    """틱 대량수집 - cursor 페이징으로 3000건+"""
    all_ticks = []
    cursor = None
    for _ in range(8):  # 최대 8페이지 (500*8=4000)
        p = {"market": market, "count": 500}
        if cursor: p["cursor"] = cursor
        batch = safe_get(f"{BASE}/trades/ticks", p)
        if not batch: break
        all_ticks.extend(batch)
        if len(batch) < 500: break
        cursor = str(batch[-1].get("sequential_id", ""))
        if not cursor: break
        time.sleep(0.12)
    return all_ticks

def get_orderbook(market):
    r = safe_get(f"{BASE}/orderbook", {"markets": market})
    return r[0] if r and len(r) > 0 else None

# ================================================================
# 3) 지표 계산
# ================================================================
def ema(data, period):
    if len(data) < period: return None
    m = 2/(period+1); e = data[0]
    for v in data[1:]: e = v*m + e*(1-m)
    return e

def sma(data, period):
    if len(data) < period: return None
    return sum(data[-period:]) / period

def rsi(closes, period=14):
    if len(closes) < period+1: return 50.0
    g, l = 0, 0
    for i in range(1, period+1):
        d = closes[-period-1+i] - closes[-period-1+i-1]
        if d > 0: g += d
        else: l -= d
    return 100 - 100/(1 + g/max(l, 0.001))

def atr(candles, period=14):
    if len(candles) < period+1: return None
    trs = []
    for i in range(1, len(candles)):
        h,l,pc = candles[i]["high_price"], candles[i]["low_price"], candles[i-1]["trade_price"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[-period:])/period if len(trs) >= period else None

def vwap(candles, period=20):
    if len(candles) < period: return None
    tv, tpv = 0, 0
    for c in candles[-period:]:
        tp = (c["high_price"]+c["low_price"]+c["trade_price"])/3
        v = c.get("candle_acc_trade_volume",0)
        tpv += tp*v; tv += v
    return tpv/tv if tv > 0 else None

def bb(closes, period=20):
    if len(closes) < period: return None, None, None, None
    s = sum(closes[-period:])/period
    if s <= 0: return None, None, None, None
    var = sum((c-s)**2 for c in closes[-period:])/period
    std = var**0.5
    return round((4*std)/s*100, 3), s+2*std, s-2*std, s

def macd(closes, fast=12, slow=26, sig=9):
    if len(closes) < slow+sig: return None, None, None
    mf, ms = 2/(fast+1), 2/(slow+1)
    ef2, es2 = closes[0], closes[0]
    macd_vals = []
    for v in closes[1:]:
        ef2 = v*mf + ef2*(1-mf)
        es2 = v*ms + es2*(1-ms)
        macd_vals.append(ef2 - es2)
    if len(macd_vals) < sig: return macd_vals[-1] if macd_vals else None, None, None
    signal = ema(macd_vals[-sig*2:], sig)
    ml = macd_vals[-1]
    return ml, signal, ml-signal if signal else None

def stoch_rsi(closes, rsi_period=14, stoch_period=14, k_period=3):
    if len(closes) < rsi_period + stoch_period + 5: return None, None
    rsi_vals = []
    for i in range(rsi_period+1, len(closes)+1):
        rsi_vals.append(rsi(closes[:i], rsi_period))
    if len(rsi_vals) < stoch_period: return None, None
    recent = rsi_vals[-stoch_period:]
    mn, mx = min(recent), max(recent)
    if mx == mn: return 50, 50
    k = (rsi_vals[-1] - mn) / (mx - mn) * 100
    k_vals = []
    for i in range(k_period, len(rsi_vals)+1):
        rr = rsi_vals[max(0,i-stoch_period):i]
        if rr:
            mn2, mx2 = min(rr), max(rr)
            k_vals.append((rr[-1]-mn2)/(mx2-mn2)*100 if mx2!=mn2 else 50)
    d = sum(k_vals[-k_period:])/k_period if len(k_vals) >= k_period else k
    return round(k, 1), round(d, 1)

def obv_trend(candles, period=10):
    if len(candles) < period+1: return 0
    obv = 0; obvs = []
    for i in range(1, len(candles)):
        if candles[i]["trade_price"] > candles[i-1]["trade_price"]:
            obv += candles[i].get("candle_acc_trade_volume", 0)
        elif candles[i]["trade_price"] < candles[i-1]["trade_price"]:
            obv -= candles[i].get("candle_acc_trade_volume", 0)
        obvs.append(obv)
    if len(obvs) < period: return 0
    return (obvs[-1] - obvs[-period]) / max(abs(obvs[-period]), 1)

def adx_approx(candles, period=14):
    if len(candles) < period+2: return None
    plus_dm, minus_dm, tr_list = [], [], []
    for i in range(1, len(candles)):
        h, l = candles[i]["high_price"], candles[i]["low_price"]
        ph, pl = candles[i-1]["high_price"], candles[i-1]["low_price"]
        pc = candles[i-1]["trade_price"]
        up = h - ph; down = pl - l
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        tr_list.append(max(h-l, abs(h-pc), abs(l-pc)))
    if len(tr_list) < period: return None
    atr_v = sum(tr_list[-period:])/period
    if atr_v <= 0: return None
    pdi = sum(plus_dm[-period:])/period / atr_v * 100
    mdi = sum(minus_dm[-period:])/period / atr_v * 100
    dx = abs(pdi-mdi) / max(pdi+mdi, 0.001) * 100
    return round(dx, 1)

# ================================================================
# 4) 캔들 패턴 감지
# ================================================================
def detect_candle_pattern(candles, idx):
    """캔들 패턴 감지 → 패턴명 또는 'none'"""
    if idx < 2 or idx >= len(candles): return "none"
    c = candles[idx]; p = candles[idx-1]; pp = candles[idx-2]
    o, h, l, cl = c["opening_price"], c["high_price"], c["low_price"], c["trade_price"]
    rng = h - l
    if rng <= 0: return "none"
    body = abs(cl - o)
    body_r = body / rng

    # 도지: 몸통 < 10%
    if body_r < 0.1: return "doji"

    upper_w = h - max(o, cl)
    lower_w = min(o, cl) - l

    # 해머: 아래꼬리 길고 몸통 위쪽 (하락 후)
    if lower_w > body * 2 and upper_w < body * 0.5:
        if p["trade_price"] < p["opening_price"]: return "hammer"

    # 슈팅스타: 위꼬리 길고 몸통 아래쪽 (상승 후)
    if upper_w > body * 2 and lower_w < body * 0.5:
        if p["trade_price"] > p["opening_price"]: return "shooting_star"

    # 장악형(Engulfing)
    p_body = abs(p["trade_price"] - p["opening_price"])
    if body > p_body * 1.3:
        if cl > o and p["trade_price"] < p["opening_price"]: return "bull_engulf"
        if cl < o and p["trade_price"] > p["opening_price"]: return "bear_engulf"

    # 십자가: 위아래 꼬리 비슷, 몸통 작음
    if body_r < 0.2 and abs(upper_w - lower_w) < rng * 0.2: return "cross"

    # 마루보즈: 꼬리 거의 없음
    if body_r > 0.85:
        return "bull_marubozu" if cl > o else "bear_marubozu"

    return "none"

# ================================================================
# 5) 틱 심층 분석
# ================================================================
def analyze_ticks_deep(trades):
    """틱 대량 심층분석"""
    if not trades or len(trades) < 50: return {}
    total = len(trades)
    buys = sum(1 for t in trades if t.get("ask_bid")=="BID")
    prices = [t["trade_price"] for t in trades if t.get("trade_price",0) > 0]
    if not prices: return {}
    mn = statistics.mean(prices)
    std = statistics.stdev(prices) if len(prices) >= 2 else 0

    # 연속 매수 최대
    consec, mx_consec = 0, 0
    for t in trades:
        if t.get("ask_bid")=="BID": consec += 1; mx_consec = max(mx_consec, consec)
        else: consec = 0

    # 대량체결 감지 (상위 5% 거래대금)
    trade_vals = [t.get("trade_price",0)*t.get("trade_volume",0) for t in trades]
    if trade_vals:
        big_threshold = sorted(trade_vals, reverse=True)[max(0, len(trade_vals)//20)]
        big_count = sum(1 for v in trade_vals if v >= big_threshold)
        big_buy = sum(1 for i, t in enumerate(trades) if trade_vals[i] >= big_threshold and t.get("ask_bid")=="BID")
        big_buy_ratio = big_buy / max(big_count, 1)
    else:
        big_count, big_buy_ratio = 0, 0.5

    # 체결 속도 (초당 체결수 추정)
    timestamps = []
    for t in trades:
        ts = t.get("timestamp")
        if ts: timestamps.append(ts)
    if len(timestamps) >= 2:
        time_span = (max(timestamps) - min(timestamps)) / 1000  # ms → sec
        tps = len(trades) / max(time_span, 1)  # trades per second
    else:
        tps = 0

    # 구간별 매수비 (최근 100/500/전체)
    r100 = trades[:100]
    buy100 = sum(1 for t in r100 if t.get("ask_bid")=="BID") / max(len(r100),1)
    r500 = trades[:500]
    buy500 = sum(1 for t in r500 if t.get("ask_bid")=="BID") / max(len(r500),1)

    # 가격 방향성 (최근 vs 과거)
    if len(prices) >= 100:
        recent_avg = statistics.mean(prices[:50])
        older_avg = statistics.mean(prices[50:100])
        price_momentum = (recent_avg / max(older_avg,1) - 1) * 100
    else:
        price_momentum = 0

    # 총 거래대금
    total_krw = sum(trade_vals[:500])

    return {
        "buy_ratio": round(buys/total, 3),
        "buy_ratio_100": round(buy100, 3),
        "buy_ratio_500": round(buy500, 3),
        "pstd": round(std/mn*100, 4) if mn > 0 else 0,
        "consec_buy": mx_consec,
        "big_count": big_count,
        "big_buy_ratio": round(big_buy_ratio, 3),
        "tps": round(tps, 2),
        "price_momentum": round(price_momentum, 4),
        "total_krw": round(total_krw),
        "n": total
    }

def analyze_orderbook(ob):
    if not ob or not ob.get("orderbook_units"): return {}
    units = ob["orderbook_units"]
    bid_sum = sum(u["bid_size"]*u["bid_price"] for u in units[:5])
    ask_sum = sum(u["ask_size"]*u["ask_price"] for u in units[:5])
    total = bid_sum + ask_sum
    imbalance = (bid_sum - ask_sum) / total if total > 0 else 0
    spread = 0
    if units and units[0]["ask_price"] > 0 and units[0]["bid_price"] > 0:
        spread = (units[0]["ask_price"] - units[0]["bid_price"]) / units[0]["bid_price"] * 100

    # 호가벽 감지: 가장 큰 매수/매도 호가 vs 평균
    bid_sizes = [u["bid_size"]*u["bid_price"] for u in units[:10]]
    ask_sizes = [u["ask_size"]*u["ask_price"] for u in units[:10]]
    avg_bid = statistics.mean(bid_sizes) if bid_sizes else 1
    avg_ask = statistics.mean(ask_sizes) if ask_sizes else 1
    max_bid_wall = max(bid_sizes) / max(avg_bid, 1)
    max_ask_wall = max(ask_sizes) / max(avg_ask, 1)

    depth = ob.get("total_ask_size",0) + ob.get("total_bid_size",0)
    return {
        "imbalance": round(imbalance, 3),
        "spread": round(spread, 4),
        "bid_krw": round(bid_sum),
        "ask_krw": round(ask_sum),
        "depth": round(depth, 2),
        "bid_wall": round(max_bid_wall, 2),
        "ask_wall": round(max_ask_wall, 2)
    }

# ================================================================
# 6) 거래량 프로파일
# ================================================================
def volume_profile(candles, n_bins=10):
    """가격대별 거래량 분포 → 지지/저항 수준"""
    if len(candles) < 20: return {}
    prices = [(c["high_price"]+c["low_price"]+c["trade_price"])/3 for c in candles]
    vols = [c.get("candle_acc_trade_volume",0) for c in candles]
    mn_p, mx_p = min(prices), max(prices)
    if mx_p <= mn_p: return {}
    bin_size = (mx_p - mn_p) / n_bins
    bins = [0.0] * n_bins
    for p, v in zip(prices, vols):
        idx = min(int((p - mn_p) / bin_size), n_bins - 1)
        bins[idx] += v
    total_v = sum(bins) or 1
    # POC (Point of Control) - 가장 거래량 많은 가격대
    poc_idx = bins.index(max(bins))
    poc_price = mn_p + (poc_idx + 0.5) * bin_size
    # 현재가 기준 위치
    cur = candles[-1]["trade_price"]
    cur_vs_poc = (cur / poc_price - 1) * 100 if poc_price > 0 else 0
    # 상위/하위 거래량 비율
    mid = n_bins // 2
    upper_vol = sum(bins[mid:]) / total_v
    lower_vol = sum(bins[:mid]) / total_v
    return {
        "poc_price": round(poc_price),
        "cur_vs_poc": round(cur_vs_poc, 3),
        "upper_vol_ratio": round(upper_vol, 3),
        "lower_vol_ratio": round(lower_vol, 3),
        "poc_concentration": round(max(bins)/total_v, 3)
    }

# ================================================================
# 7) BTC 상관관계
# ================================================================
def calc_btc_corr(coin_candles, btc_candles):
    """코인 vs BTC 수익률 상관계수"""
    if len(coin_candles) < 20 or len(btc_candles) < 20: return 0
    # 시간 매칭
    btc_map = {}
    for c in btc_candles:
        btc_map[c["candle_date_time_kst"]] = c["trade_price"]
    coin_rets, btc_rets = [], []
    for i in range(1, len(coin_candles)):
        t = coin_candles[i]["candle_date_time_kst"]
        t_prev = coin_candles[i-1]["candle_date_time_kst"]
        if t in btc_map and t_prev in btc_map and btc_map[t_prev] > 0 and coin_candles[i-1]["trade_price"] > 0:
            coin_rets.append(coin_candles[i]["trade_price"] / coin_candles[i-1]["trade_price"] - 1)
            btc_rets.append(btc_map[t] / btc_map[t_prev] - 1)
    if len(coin_rets) < 10: return 0
    try:
        mn_c = statistics.mean(coin_rets)
        mn_b = statistics.mean(btc_rets)
        cov = sum((c-mn_c)*(b-mn_b) for c,b in zip(coin_rets, btc_rets)) / len(coin_rets)
        std_c = statistics.stdev(coin_rets)
        std_b = statistics.stdev(btc_rets)
        if std_c > 0 and std_b > 0:
            return round(cov / (std_c * std_b), 3)
    except: pass
    return 0

# ================================================================
# 헬퍼
# ================================================================
def body_pct(c):
    return (c["trade_price"] - c["opening_price"]) / max(c["opening_price"], 1)

def wick_ratio(c):
    rng = c["high_price"] - c["low_price"]
    if rng <= 0: return 0, 0
    upper = (c["high_price"] - max(c["trade_price"], c["opening_price"])) / rng
    lower = (min(c["trade_price"], c["opening_price"]) - c["low_price"]) / rng
    return upper, lower

def green_streak_at(candles, idx):
    n = 0
    for i in range(idx, -1, -1):
        if candles[i]["trade_price"] > candles[i]["opening_price"]: n += 1
        else: break
    return n

def vol_surge_at(candles, idx, lb=5):
    if idx < lb+1: return 1.0
    cur = candles[idx].get("candle_acc_trade_price", 0)
    past = [candles[i].get("candle_acc_trade_price",0) for i in range(idx-lb, idx)]
    return cur / max(sum(past)/len(past), 1) if past else 1.0

# ================================================================
# 8) 시뮬레이션
# ================================================================
def simulate(c1, c5, c15, c60, daily, market, btc_c5, tick_data):
    results = []
    if len(c1) < 40: return results

    # 일봉 매크로 추세
    daily_trend = 0
    if daily and len(daily) >= 3:
        try:
            daily.sort(key=lambda x: x["candle_date_time_kst"])
            d_closes = [d["trade_price"] for d in daily[-3:]]
            if len(d_closes) >= 3 and d_closes[0] > 0:
                daily_trend = (d_closes[-1] / d_closes[0] - 1) * 100
        except: pass

    # BTC 상관관계
    btc_corr = calc_btc_corr(c5, btc_c5) if btc_c5 else 0

    # 거래량 프로파일
    vp = volume_profile(c1[-200:] if len(c1)>=200 else c1)

    # 틱 요약
    tick_info = analyze_ticks_deep(tick_data) if tick_data else {}

    def nearest(candles, t):
        for i in range(len(candles)-1, -1, -1):
            if candles[i]["candle_date_time_kst"] <= t: return i
        return 0

    for idx in range(30, len(c1) - 16):
        c = c1[idx]; prev = c1[idx-1]
        close = c["trade_price"]; op = c["opening_price"]
        if close <= 0 or op <= 0: continue

        closes = [c1[i]["trade_price"] for i in range(max(0,idx-29), idx+1)]

        # ===== 1분봉 지표 =====
        bp = body_pct(c)
        uwr, lwr = wick_ratio(c)
        gs = green_streak_at(c1, idx)
        vs = vol_surge_at(c1, idx, 5)
        vs_10 = vol_surge_at(c1, idx, 10)
        pattern = detect_candle_pattern(c1, idx)

        ema5_v  = ema(closes, 5)
        ema10_v = ema(closes, 10)
        ema20_v = ema(closes, 20)

        ema_brk = 1 if (ema20_v and close > ema20_v) else 0
        ema_gap_v = (close/ema20_v - 1)*100 if ema20_v and ema20_v > 0 else 0
        ema5_slope = (ema5_v/ema(closes[:-1],5)-1)*100 if ema5_v and len(closes)>6 and ema(closes[:-1],5) else 0
        # EMA 배열 (5>10>20 = 정배열)
        ema_aligned = 1 if (ema5_v and ema10_v and ema20_v and ema5_v > ema10_v > ema20_v) else 0

        rsi_v = rsi(closes, 14)
        atr_v = atr(c1[max(0,idx-15):idx+1], 14)
        atr_pct_v = atr_v/close*100 if atr_v and close > 0 else 0.5
        vwap_v = vwap(c1[max(0,idx-20):idx+1], min(20, idx+1))
        vwap_gap_v = (close/vwap_v-1)*100 if vwap_v and vwap_v > 0 else 0
        bb_w, bb_u, bb_l, bb_m = bb(closes, 20)
        bb_pos = (close-bb_l)/(bb_u-bb_l)*100 if bb_u and bb_l and bb_u != bb_l else 50

        macd_l, macd_s, macd_h = macd(closes, 12, 26, 9)
        stoch_k, stoch_d = stoch_rsi(closes, 14, 14, 3)
        obv_t = obv_trend(c1[max(0,idx-20):idx+1], 10)
        adx_v = adx_approx(c1[max(0,idx-15):idx+1], 14)

        prev_highs = [c1[i]["high_price"] for i in range(max(0,idx-12), idx)]
        ph = max(prev_highs) if prev_highs else 0
        high_brk = 1 if c["high_price"] > ph > 0 else 0

        vol_v = c.get("candle_acc_trade_price", 0)
        vol_ma_v = [c1[i].get("candle_acc_trade_price",0) for i in range(max(0,idx-20), idx)]
        vol_ma20 = sum(vol_ma_v)/len(vol_ma_v) if vol_ma_v else 1
        vol_vs_ma = vol_v / max(vol_ma20, 1)

        price_chg = (close/max(prev["trade_price"],1)-1)
        mom5_v = (close - c1[idx-5]["trade_price"])/max(c1[idx-5]["trade_price"],1) if idx >= 5 else 0
        mom10_v = (close - c1[idx-10]["trade_price"])/max(c1[idx-10]["trade_price"],1) if idx >= 10 else 0

        # 거래량 프로파일 위치
        vp_poc = vp.get("cur_vs_poc", 0)
        vp_conc = vp.get("poc_concentration", 0)

        try: hour = int(c["candle_date_time_kst"][11:13])
        except: hour = 12

        # ===== 다중 타임프레임 =====
        tkst = c["candle_date_time_kst"]

        i5 = nearest(c5, tkst)
        rsi5=50; atr5p=0; vs5=1
        if i5 >= 15 and len(c5) > i5:
            cl5 = [c5[j]["trade_price"] for j in range(max(0,i5-15), i5+1)]
            rsi5 = rsi(cl5, 14)
            a5 = atr(c5[max(0,i5-15):i5+1], 14)
            atr5p = a5/close*100 if a5 and close > 0 else 0
            vs5 = vol_surge_at(c5, i5, 3)

        i15 = nearest(c15, tkst)
        rsi15=50; atr15p=0
        if i15 >= 15 and len(c15) > i15:
            cl15 = [c15[j]["trade_price"] for j in range(max(0,i15-15), i15+1)]
            rsi15 = rsi(cl15, 14)
            a15 = atr(c15[max(0,i15-15):i15+1], 14)
            atr15p = a15/close*100 if a15 and close > 0 else 0

        i60 = nearest(c60, tkst)
        rsi1h=50; trend1h=0
        if i60 >= 6 and len(c60) > i60:
            cl60 = [c60[j]["trade_price"] for j in range(max(0,i60-10), i60+1)]
            rsi1h = rsi(cl60, 14) if len(cl60) >= 15 else 50
            e1 = ema(cl60[-6:], 5); e0 = ema(cl60[-7:-1], 5) if len(cl60) >= 7 else None
            trend1h = (e1/e0-1)*100 if e1 and e0 and e0 > 0 else 0

        # ===== 시뮬레이션 (15분) =====
        ep = close * (1 + SLIP + FEE)
        mfe, mae, mfe_bar = 0, 0, 0
        exit_p, exit_r, exit_b = None, None, 0
        sl = max(0.010, min(0.016, atr_pct_v/100*0.85))
        td = max(0.004, atr_pct_v/100*0.5)
        cp = 0.0035
        armed, ts = False, 0

        for fwd in range(1, min(16, len(c1)-idx)):
            fc = c1[idx+fwd]
            fh, fl, fcl = fc["high_price"], fc["low_price"], fc["trade_price"]
            cm = fh/ep-1; ca = fl/ep-1
            if cm > mfe: mfe = cm; mfe_bar = fwd
            if ca < mae: mae = ca
            g = fcl/ep-1
            if ca <= -sl: exit_p = ep*(1-sl); exit_r = "SL"; exit_b = fwd; break
            if ca <= -(sl*1.6): exit_p = ep*(1-sl*1.6); exit_r = "HARD"; exit_b = fwd; break
            if not armed and g >= cp:
                armed = True; ts = max(ep*(1+FEE), fcl*(1-td))
            if armed:
                ts = max(ts, fh*(1-td))
                if fl <= ts: exit_p = ts; exit_r = "TRAIL"; exit_b = fwd; break

        if exit_p is None:
            exit_p = c1[min(idx+15, len(c1)-1)]["trade_price"]
            exit_r = "TIMEOUT"; exit_b = 15
        pnl = (exit_p/ep-1) - FEE - SLIP

        results.append({
            "market": market, "hour": hour,
            "open": op, "high": c["high_price"], "low": c["low_price"], "close": close,
            "volume": round(vol_v),
            # 캔들 지표
            "body_pct": round(bp*100,3), "uwr": round(uwr,3), "lwr": round(lwr,3),
            "green_streak": gs, "pattern": pattern,
            "vol_surge_5": round(vs,2), "vol_surge_10": round(vs_10,2), "vol_vs_ma20": round(vol_vs_ma,2),
            "ema_break": ema_brk, "ema_gap": round(ema_gap_v,3), "ema5_slope": round(ema5_slope,3),
            "ema_aligned": ema_aligned,
            "high_break": high_brk,
            "rsi_1m": round(rsi_v,1), "atr_pct": round(atr_pct_v,3),
            "vwap_gap": round(vwap_gap_v,2),
            "bb_width": bb_w, "bb_pos": round(bb_pos,1) if bb_pos else None,
            "macd_hist": round(macd_h,4) if macd_h else None,
            "stoch_k": stoch_k, "stoch_d": stoch_d,
            "obv_trend": round(obv_t,3), "adx": adx_v,
            "price_chg": round(price_chg*100,3),
            "mom5": round(mom5_v*100,3), "mom10": round(mom10_v*100,3),
            # 다중TF
            "rsi_5m": round(rsi5,1), "atr_5m_pct": round(atr5p,3), "vol_surge_5m": round(vs5,2),
            "rsi_15m": round(rsi15,1), "atr_15m_pct": round(atr15p,3),
            "rsi_1h": round(rsi1h,1), "trend_1h": round(trend1h,3),
            # 매크로
            "daily_trend": round(daily_trend, 3),
            "btc_corr": btc_corr,
            "vp_poc_gap": round(vp_poc, 3),
            "vp_concentration": round(vp_conc, 3),
            # 틱 데이터
            "tick_buy_ratio": tick_info.get("buy_ratio", 0.5),
            "tick_buy_100": tick_info.get("buy_ratio_100", 0.5),
            "tick_big_buy": tick_info.get("big_buy_ratio", 0.5),
            "tick_tps": tick_info.get("tps", 0),
            "tick_momentum": tick_info.get("price_momentum", 0),
            # 시뮬 결과
            "sl_pct": round(sl*100,2), "mfe": round(mfe*100,3), "mae": round(mae*100,3),
            "mfe_bar": mfe_bar, "pnl": round(pnl*100,3),
            "exit_reason": exit_r, "exit_bar": exit_b,
            "win": 1 if pnl > 0 else 0,
        })
    return results

# ================================================================
# 9) 분석 (섹션별 try/except + 즉시 TG 전송)
# ================================================================
def safe_mean(lst):
    return statistics.mean(lst) if lst else 0

def run_analysis(all_results, tick_all, ob_all):
    """분석 실행 — 각 섹션마다 즉시 텔레그램 전송"""
    out_lines = []
    try:
        wr = sum(r["win"] for r in all_results)/len(all_results)*100
        pnl = safe_mean([r["pnl"] for r in all_results])
        header = f"전체: {len(all_results)}건 WR={wr:.1f}% PnL={pnl:+.3f}%"
        out_lines.append(header)
        tg(f"📊 {header}")
    except Exception as e:
        tg(f"❌ 기본통계 에러: {e}"); wr = 50

    # 팩터 중요도
    imp = []
    try:
        factors = [
            "body_pct","vol_surge_5","vol_surge_10","vol_vs_ma20","ema_gap","ema5_slope","ema_aligned",
            "rsi_1m","rsi_5m","rsi_15m","rsi_1h","trend_1h","daily_trend",
            "atr_pct","atr_5m_pct","atr_15m_pct","price_chg","vwap_gap","mom5","mom10",
            "uwr","lwr","green_streak","bb_width","bb_pos",
            "macd_hist","stoch_k","stoch_d","obv_trend","adx",
            "vol_surge_5m","btc_corr","vp_poc_gap","vp_concentration",
            "tick_buy_ratio","tick_buy_100","tick_big_buy","tick_tps","tick_momentum"
        ]
        for f in factors:
            vs = sorted([r for r in all_results if r.get(f) is not None], key=lambda x: x[f])
            if len(vs) < 40: continue
            n = len(vs); q1 = vs[:n//4]; q4 = vs[3*n//4:]
            w1 = sum(r["win"] for r in q1)/len(q1)*100
            w4 = sum(r["win"] for r in q4)/len(q4)*100
            p1 = safe_mean([r["pnl"] for r in q1])
            p4 = safe_mean([r["pnl"] for r in q4])
            imp.append({"factor":f, "wr_diff":round(w4-w1,1), "pnl_diff":round(p4-p1,3),
                         "wr_q1":round(w1,1), "wr_q4":round(w4,1)})
        imp.sort(key=lambda x: abs(x["wr_diff"]), reverse=True)
        msg = "🏆 [FACTOR IMPORTANCE]\n"
        for x in imp:
            d = "↑" if x["wr_diff"] > 0 else "↓"
            line = f"  {x['factor']:17s}: {x['wr_diff']:+.1f}% (하위={x['wr_q1']:.1f}% 상위={x['wr_q4']:.1f}%) {d}"
            out_lines.append(line)
            msg += line + "\n"
        tg(msg[:3900])
    except Exception as e:
        tg(f"❌ 팩터 중요도 에러: {e}")

    # 캔들패턴별 성과
    try:
        patterns = defaultdict(list)
        for r in all_results:
            if r.get("pattern") and r["pattern"] != "none":
                patterns[r["pattern"]].append(r)
        if patterns:
            msg = "🕯️ [CANDLE PATTERNS]\n"
            out_lines.append("\n[CANDLE PATTERNS]")
            for p, rs in sorted(patterns.items(), key=lambda x: -len(x[1])):
                if len(rs) < 10: continue
                w = sum(r["win"] for r in rs)/len(rs)*100
                pv = safe_mean([r["pnl"] for r in rs])
                line = f"  {p:15s}: n={len(rs):5d} WR={w:5.1f}% PnL={pv:+.3f}%"
                out_lines.append(line); msg += line + "\n"
            tg(msg[:3900])
    except Exception as e:
        tg(f"❌ 캔들패턴 에러: {e}")

    # 팩터 구간 상세
    try:
        bins_map = {
            "body_pct":[-999,0,0.3,0.5,1,2,999], "vol_surge_5":[-999,0.5,1,2,5,10,999],
            "rsi_1m":[-999,30,50,60,70,80,999], "rsi_5m":[-999,30,50,60,70,80,999],
            "rsi_1h":[-999,30,50,60,70,80,999], "trend_1h":[-999,-0.3,0,0.1,0.3,0.5,999],
            "ema_gap":[-999,-0.5,0,0.3,0.5,1,2,999], "vwap_gap":[-999,-1,-0.3,0,0.3,1,2,999],
            "green_streak":[0,1,2,3,4,5,999], "atr_pct":[0,0.2,0.3,0.5,0.8,1,999],
            "bb_pos":[-999,20,40,60,80,100,999], "stoch_k":[-999,20,40,60,80,100,999],
            "adx":[-999,15,25,40,60,999], "daily_trend":[-999,-2,-0.5,0,0.5,2,999],
            "tick_buy_ratio":[0,0.3,0.4,0.5,0.6,0.7,1], "tick_big_buy":[0,0.3,0.5,0.6,0.7,0.8,1],
            "btc_corr":[-1,-0.3,0,0.3,0.6,0.8,1],
        }
        msg = "📈 [FACTOR DETAIL]\n"
        out_lines.append("\n[FACTOR DETAIL]")
        for fn, bins in bins_map.items():
            vs = [r[fn] for r in all_results if r.get(fn) is not None]
            if len(vs) < 20: continue
            out_lines.append(f"\n  [{fn}]"); msg += f"\n[{fn}]\n"
            for i in range(len(bins)-1):
                lo, hi = bins[i], bins[i+1]
                sub = [r for r in all_results if r.get(fn) is not None and lo <= r[fn] < hi]
                if len(sub) < 5: continue
                w = sum(r["win"] for r in sub)/len(sub)*100
                p = safe_mean([r["pnl"] for r in sub])
                star = " ★" if w >= wr+5 else (" ✗" if w <= wr-5 else "")
                line = f"  [{lo:7.2f},{hi:7.2f}): n={len(sub):4d} WR={w:5.1f}% PnL={p:+.3f}%{star}"
                out_lines.append("  " + line); msg += line + "\n"
            if len(msg) > 3500:
                tg(msg[:3900]); msg = "📈 [FACTOR DETAIL 계속]\n"
        if msg.strip(): tg(msg[:3900])
    except Exception as e:
        tg(f"❌ 팩터 구간 에러: {e}")

    # 콤보
    try:
        combos = [
            ("vol_surge_5",3,"rsi_1m",55), ("vol_surge_5",5,"ema_break",1),
            ("body_pct",0.3,"vol_surge_5",2), ("rsi_5m",60,"vol_vs_ma20",2),
            ("mom5",0.5,"vol_surge_5",3), ("high_break",1,"vol_surge_5",2),
            ("rsi_1m",60,"rsi_5m",60), ("rsi_1m",60,"rsi_1h",55),
            ("trend_1h",0.1,"vol_surge_5",2), ("stoch_k",70,"rsi_1m",60),
            ("adx",25,"vol_surge_5",2), ("bb_pos",80,"vol_surge_5",2),
            ("ema_aligned",1,"vol_surge_5",2), ("ema_aligned",1,"rsi_1m",55),
            ("tick_buy_ratio",0.6,"vol_surge_5",2), ("tick_big_buy",0.6,"rsi_1m",55),
            ("daily_trend",0.5,"vol_surge_5",2), ("btc_corr",0.5,"trend_1h",0.1),
        ]
        msg = "🔗 [COMBOS]\n"; out_lines.append("\n[COMBOS]")
        for f1,t1,f2,t2 in combos:
            both = [r for r in all_results if r.get(f1) is not None and r.get(f2) is not None and r[f1]>=t1 and r[f2]>=t2]
            if len(both) >= 5:
                w = sum(r["win"] for r in both)/len(both)*100
                p = safe_mean([r["pnl"] for r in both])
                line = f"  {f1}>={t1} & {f2}>={t2}: n={len(both):4d} WR={w:5.1f}% PnL={p:+.3f}%"
                out_lines.append(line); msg += line + "\n"
        tg(msg[:3900])
    except Exception as e:
        tg(f"❌ 콤보 에러: {e}")

    # Exit 파라미터
    try:
        msg = "🔚 [EXIT PARAMS]\n"; out_lines.append("\n[EXIT PARAMS]")
        mfes = sorted([r["mfe"] for r in all_results if r["mfe"]>0])
        maes = sorted([r["mae"] for r in all_results])
        if mfes:
            line = "[MFE] " + " ".join(f"P{p}:+{mfes[int(len(mfes)*p/100)]:.3f}%" for p in [25,50,75,90])
            out_lines.append(line); msg += line + "\n"
        if maes:
            line = "[MAE] " + " ".join(f"P{p}:{maes[int(len(maes)*p/100)]:.3f}%" for p in [10,25,50,75])
            out_lines.append(line); msg += line + "\n"
        for sl_v in [0.6,0.8,1.0,1.2,1.5,1.8,2.0]:
            st = sum(1 for r in all_results if r["mae"]<=-sl_v)
            ns = sum(1 for r in all_results if r["mae"]<=-sl_v and r["mfe"]>sl_v*0.5)
            line = f"  SL {sl_v:.1f}%: 피격 {st}/{len(all_results)} ({st/len(all_results)*100:.1f}%) 노이즈={ns}"
            out_lines.append(line); msg += line + "\n"
        for td_v in [0.20,0.25,0.30,0.35,0.40,0.50]:
            cpok = [r for r in all_results if r["mfe"]>=0.35]
            cap = [min(r["pnl"],r["mfe"]-td_v) for r in cpok]
            if cap:
                line = f"  Trail {td_v:.2f}%: 포착 {safe_mean(cap):.3f}% (n={len(cpok)})"
                out_lines.append(line); msg += line + "\n"
        tg(msg[:3900])
    except Exception as e:
        tg(f"❌ Exit 에러: {e}")

    # 시간대별
    try:
        msg = "🕐 [HOURLY]\n"; out_lines.append("\n[HOURLY]")
        bh = defaultdict(list)
        for r in all_results: bh[r["hour"]].append(r)
        for h in sorted(bh):
            s = bh[h]
            if len(s) < 5: continue
            line = f"  {h:02d}시: n={len(s):5d} WR={sum(r['win'] for r in s)/len(s)*100:5.1f}% PnL={safe_mean([r['pnl'] for r in s]):+.3f}%"
            out_lines.append(line); msg += line + "\n"
        tg(msg[:3900])
    except Exception as e:
        tg(f"❌ 시간대 에러: {e}")

    # 틱 요약
    try:
        if tick_all:
            msg = "📊 [TICK SUMMARY]\n"; out_lines.append("\n[TICK SUMMARY]")
            for m, td in sorted(tick_all.items(), key=lambda x:-x[1].get("buy_ratio",0)):
                line = f"  {m.split('-')[1]:8s}: 매수비={td['buy_ratio']:.0%} 대량매수비={td.get('big_buy_ratio',0):.0%} 초당={td.get('tps',0):.1f} n={td['n']}"
                out_lines.append(line); msg += line + "\n"
            tg(msg[:3900])
    except Exception as e:
        tg(f"❌ 틱 에러: {e}")

    # 호가 요약
    try:
        if ob_all:
            msg = "📊 [ORDERBOOK SUMMARY]\n"; out_lines.append("\n[ORDERBOOK SUMMARY]")
            for m, od in sorted(ob_all.items(), key=lambda x:-x[1].get("imbalance",0)):
                line = f"  {m.split('-')[1]:8s}: 임밸={od['imbalance']:+.3f} 스프={od['spread']:.4f}% 매수벽={od.get('bid_wall',0):.1f}x 매도벽={od.get('ask_wall',0):.1f}x"
                out_lines.append(line); msg += line + "\n"
            tg(msg[:3900])
    except Exception as e:
        tg(f"❌ 호가 에러: {e}")

    return out_lines, imp

# ================================================================
# 메인
# ================================================================
def main():
    t0 = time.time()
    tg("🔍 [캔들분석v3] 시작\nTOP50 × (1m/5m/15m/1h/일봉 + tick3000 + orderbook)\n캔들패턴 + BTC상관 + 거래량프로파일 + 틱심층")

    markets = get_top_markets(TOP_N)
    if not markets:
        tg("❌ 종목 가져오기 실패!"); return
    tg(f"📋 TOP{TOP_N}: {', '.join(m.split('-')[1] for m in markets)}")

    # BTC 5분봉 먼저 수집 (상관관계용)
    btc_c5 = []
    try:
        btc_c5 = get_all_candles(5, "KRW-BTC", 288)
        tg(f"📈 BTC 5분봉 {len(btc_c5)}개 수집 완료")
    except: tg("⚠️ BTC 데이터 수집 실패")

    all_results = []
    tick_all, ob_all = {}, {}

    for i, m in enumerate(markets):
        coin = m.split("-")[1]
        if i % 10 == 0:
            tg(f"📊 진행: {i}/{TOP_N} | {(time.time()-t0)/60:.1f}분 | {len(all_results)}건")
        try:
            print(f"[{i+1}/{TOP_N}] {coin}...")
            c1 = get_all_candles(1, m, 1440); time.sleep(0.1)
            c5 = get_all_candles(5, m, 288);  time.sleep(0.1)
            c15= get_all_candles(15, m, 96);  time.sleep(0.1)
            c60= get_all_candles(60, m, 24);  time.sleep(0.1)
            daily = get_daily_candles(m, 7);  time.sleep(0.1)
            ticks = get_ticks_paginated(m, 3000); time.sleep(0.1)
            ob = get_orderbook(m);            time.sleep(0.1)

            print(f"  1m:{len(c1)} 5m:{len(c5)} 15m:{len(c15)} 1h:{len(c60)} d:{len(daily)} tick:{len(ticks)} ob:{'Y' if ob else 'N'}")
            if len(c1) < 100: continue

            ts = analyze_ticks_deep(ticks)
            if ts: tick_all[m] = ts
            obs = analyze_orderbook(ob)
            if obs: ob_all[m] = obs

            res = simulate(c1, c5, c15, c60, daily, m, btc_c5, ticks)
            all_results.extend(res)
            print(f"  시뮬: {len(res)}건 WR={sum(r['win'] for r in res)/max(len(res),1)*100:.1f}%")
        except Exception as e:
            print(f"  ❌ {coin}: {e}"); continue

    elapsed = (time.time()-t0)/60
    tg(f"📊 수집 완료: {len(all_results)}건 | {elapsed:.1f}분\n분석 시작...")

    if not all_results:
        tg("❌ 데이터 없음!"); return

    # 분석 (섹션별 안전 실행 + 즉시 전송)
    out_lines, imp = run_analysis(all_results, tick_all, ob_all)

    # 파일 저장
    try:
        txt = "\n".join(out_lines)
        with open(RESULT_TXT, "w", encoding="utf-8") as f: f.write(txt)
        with open(RESULT_JSON, "w", encoding="utf-8") as f:
            json.dump({"timestamp": datetime.now(KST).isoformat(), "total": len(all_results),
                        "elapsed_min": round(elapsed,1), "importance": imp,
                        "tick": tick_all, "orderbook": ob_all, "results": all_results},
                       f, ensure_ascii=False, indent=2)
        tg(f"💾 저장 완료: {RESULT_TXT}")
    except Exception as e:
        tg(f"⚠️ 파일 저장 에러 (분석은 완료): {e}")

    tg(f"✅ [캔들분석v3] 전체 완료! | {len(all_results)}건 | {elapsed:.1f}분")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        err = traceback.format_exc(); print(err)
        tg(f"❌ 크래시!\n{str(e)}\n{err[-500:]}")
    # ===== 재시작 방지 =====
    tg("💤 분석 완료. 대기모드 (bot.py 복구해주세요)")
    while True:
        time.sleep(3600)
