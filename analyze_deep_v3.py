# -*- coding: utf-8 -*-
"""
딥 분석 스크립트 v3
- 1분봉 + 5분봉 동시 분석
- RSI, MACD, 볼린저밴드, 스토캐스틱 등 기술적 지표
- 성공/실패 케이스 상세 비교
"""
import requests
import time
from datetime import datetime, timedelta
import statistics
import math

# 분석할 케이스들 (종목, 날짜, 시간, 성공여부)
CASES = [
    # === 성공 케이스 ===
    ("TOSHI", "2026-01-06", "10:09", True),
    ("BORA", "2026-01-06", "09:05", True),
    ("PLUME", "2026-01-06", "10:29", True),
    ("QTUM", "2026-01-06", "09:02", True),
    ("DOOD", "2026-01-06", "10:11", True),
    ("SUI", "2026-01-06", "09:00", True),
    ("ONT", "2026-01-06", "09:03", True),
    ("VIRTUAL", "2026-01-05", "10:24", True),
    ("BSV", "2026-01-05", "09:51", True),
    ("PEPE", "2026-01-04", "17:08", True),
    ("BTT", "2026-01-06", "09:01", True),
    ("SHIB", "2026-01-06", "01:10", True),
    ("STORJ", "2026-01-05", "21:32", True),
    ("XRP", "2026-01-05", "23:29", True),
    ("BTC", "2026-01-05", "08:59", True),
    ("ETH", "2026-01-05", "08:59", True),
    ("VIRTUAL", "2026-01-03", "12:30", True),
    ("ORCA", "2026-01-05", "09:01", True),
    ("GRS", "2026-01-03", "14:42", True),
    ("MMT", "2026-01-05", "19:52", True),
    ("BOUNTY", "2026-01-07", "09:06", True),
    ("MOC", "2026-01-07", "09:08", True),
    ("FCT2", "2026-01-07", "09:07", True),
    ("BOUNTY", "2026-01-07", "16:23", True),
    ("ZKP", "2026-01-07", "19:39", True),
    # === 1/8 성공 케이스 ===
    ("STRAX", "2026-01-08", "09:00", True),
    ("BREV", "2026-01-08", "09:06", True),
    ("ELF", "2026-01-08", "10:35", True),
    ("MED", "2026-01-08", "11:21", True),
    # === 실패 케이스 ===
    ("ETH", "2026-01-07", "11:05", False),
    ("SUI", "2026-01-07", "11:05", False),
    ("SUI", "2026-01-07", "10:46", False),
    ("ADA", "2026-01-07", "10:25", False),
    ("GAS", "2026-01-07", "10:21", False),
    ("GAS", "2026-01-07", "09:46", False),
    ("SUI", "2026-01-07", "09:30", False),
    ("ETH", "2026-01-07", "21:05", False),
    ("BTC", "2026-01-07", "20:43", False),
    # === 1/8 실패 케이스 (21건) ===
    ("KAITO", "2026-01-08", "07:34", False),
    ("SOL", "2026-01-08", "07:32", False),
    ("ETH", "2026-01-08", "07:34", False),
    ("SUI", "2026-01-08", "07:32", False),
    ("BREV", "2026-01-08", "07:20", False),
    ("BREV", "2026-01-08", "06:48", False),
    ("ETH", "2026-01-08", "06:31", False),
    ("BREV", "2026-01-08", "06:02", False),
    ("BREV", "2026-01-08", "05:52", False),
    ("BREV", "2026-01-08", "05:43", False),
    ("BOUNTY", "2026-01-08", "05:42", False),
    ("BOUNTY", "2026-01-08", "05:38", False),
    ("BREV", "2026-01-08", "05:38", False),
    ("ETH", "2026-01-08", "05:37", False),
    ("XRP", "2026-01-08", "05:01", False),
    ("XRP", "2026-01-08", "03:20", False),
    ("BREV", "2026-01-08", "02:08", False),
    ("XRP", "2026-01-08", "01:32", False),
    ("BREV", "2026-01-08", "01:27", False),
    ("PEPE", "2026-01-08", "01:18", False),
    ("CVC", "2026-01-08", "00:35", False),
    # === 1/8 오후 실패 케이스 (11건) - 조건 완화 후 ===
    ("IP", "2026-01-08", "17:45", False),
    ("VIRTUAL", "2026-01-08", "17:43", False),
    ("VIRTUAL", "2026-01-08", "17:41", False),
    ("VIRTUAL", "2026-01-08", "17:39", False),
    ("SUI", "2026-01-08", "17:36", False),
    ("BTC", "2026-01-08", "17:34", False),
    ("IP", "2026-01-08", "17:32", False),
    ("SUI", "2026-01-08", "17:27", False),
    ("VIRTUAL", "2026-01-08", "17:25", False),
    ("ONDO", "2026-01-08", "17:18", False),
    ("SOL", "2026-01-08", "17:16", False),
    # === 1/8 오후 성공 케이스 (1건) ===
    ("VIRTUAL", "2026-01-08", "17:18", True),
    # === 1/8 저녁 실패 케이스 (13건) ===
    ("BREV", "2026-01-08", "23:46", False),
    ("G", "2026-01-08", "22:19", False),
    ("AERGO", "2026-01-08", "20:55", False),
    ("BTC", "2026-01-08", "19:43", False),
    ("ETH", "2026-01-08", "19:36", False),
    ("VIRTUAL", "2026-01-08", "19:26", False),
    ("CVC", "2026-01-08", "19:21", False),
    ("BREV", "2026-01-08", "18:41", False),
    ("IP", "2026-01-08", "18:29", False),
    ("IP", "2026-01-08", "18:25", False),
    ("IP", "2026-01-08", "18:24", False),
    ("BREV", "2026-01-08", "18:18", False),
    ("IP", "2026-01-08", "18:18", False),
    # === 1/8 저녁 성공 케이스 (2건) ===
    ("ARDR", "2026-01-08", "19:40", True),
    ("IP", "2026-01-08", "18:20", True),
    # === 1/9 실패 케이스 ===
    ("BREV", "2026-01-09", "08:52", False),
    ("BREV", "2026-01-09", "08:51", False),
    ("VIRTUAL", "2026-01-09", "07:11", False),
    ("SOL", "2026-01-09", "07:00", False),
    ("IP", "2026-01-09", "02:50", False),
    ("XRP", "2026-01-09", "02:03", False),
    ("XRP", "2026-01-09", "01:13", False),
    ("VIRTUAL", "2026-01-09", "01:09", False),
    ("KAITO", "2026-01-09", "01:02", False),
    # === 1/9 성공 케이스 ===
    ("G", "2026-01-09", "04:23", True),
    ("AQT", "2026-01-09", "10:45", True),
    ("BOUNTY", "2026-01-09", "09:46", True),
    # === 1/9 추가 실패 ===
    ("AQT", "2026-01-09", "10:48", False),
    ("BOUNTY", "2026-01-09", "11:06", False),
    ("BOUNTY", "2026-01-09", "11:05", False),
    ("BOUNTY", "2026-01-09", "10:46", False),
    ("BOUNTY", "2026-01-09", "10:07", False),
    ("DEEP", "2026-01-09", "12:51", False),
    ("DEEP", "2026-01-09", "13:00", False),
    ("PEPE", "2026-01-09", "13:00", False),
    ("BREV", "2026-01-09", "14:12", False),
]

def get_candles(market, to_time, count=50, unit=1):
    """캔들 조회 (unit: 1=1분봉, 5=5분봉)"""
    url = f"https://api.upbit.com/v1/candles/minutes/{unit}"
    params = {"market": f"KRW-{market}", "to": to_time, "count": count}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return []
    except:
        return []

def calc_ema(prices, period):
    """EMA 계산"""
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calc_sma(prices, period):
    """SMA 계산"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def calc_rsi(prices, period=14):
    """RSI 계산"""
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    if len(gains) < period:
        return None

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_macd(prices, fast=12, slow=26, signal=9):
    """MACD 계산"""
    if len(prices) < slow:
        return None, None, None

    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)

    if ema_fast is None or ema_slow is None:
        return None, None, None

    macd_line = ema_fast - ema_slow
    # 간단히 현재 MACD만 반환
    return macd_line, None, None

def calc_bollinger(prices, period=20, std_dev=2):
    """볼린저밴드 계산"""
    if len(prices) < period:
        return None, None, None

    sma = sum(prices[-period:]) / period
    variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
    std = math.sqrt(variance)

    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)

    return upper, sma, lower

def calc_stochastic(highs, lows, closes, k_period=14, d_period=3):
    """스토캐스틱 계산"""
    if len(closes) < k_period:
        return None, None

    highest_high = max(highs[-k_period:])
    lowest_low = min(lows[-k_period:])

    if highest_high == lowest_low:
        return 50, 50

    k = ((closes[-1] - lowest_low) / (highest_high - lowest_low)) * 100
    return k, None  # D는 K의 이동평균이라 간단히 K만 반환

def calc_atr(highs, lows, closes, period=14):
    """ATR(Average True Range) 계산"""
    if len(closes) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    return sum(true_ranges[-period:]) / period

def calc_obv_trend(closes, volumes, period=10):
    """OBV 추세 계산"""
    if len(closes) < period + 1:
        return None

    obv = 0
    obv_values = []
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv += volumes[i]
        elif closes[i] < closes[i-1]:
            obv -= volumes[i]
        obv_values.append(obv)

    if len(obv_values) < period:
        return None

    # OBV 기울기 (추세)
    recent_obv = obv_values[-period:]
    if recent_obv[-1] > recent_obv[0]:
        return (recent_obv[-1] - recent_obv[0]) / abs(recent_obv[0]) if recent_obv[0] != 0 else 1
    return (recent_obv[-1] - recent_obv[0]) / abs(recent_obv[0]) if recent_obv[0] != 0 else -1

def calc_cci(highs, lows, closes, period=20):
    """CCI (Commodity Channel Index) 계산"""
    if len(closes) < period:
        return None

    # Typical Price = (High + Low + Close) / 3
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]

    # SMA of TP
    tp_sma = sum(tp[-period:]) / period

    # Mean Deviation
    mean_dev = sum(abs(tp[-period:][i] - tp_sma) for i in range(period)) / period

    if mean_dev == 0:
        return 0

    # CCI = (TP - SMA) / (0.015 * Mean Deviation)
    cci = (tp[-1] - tp_sma) / (0.015 * mean_dev)
    return cci

def calc_williams_r(highs, lows, closes, period=14):
    """Williams %R 계산"""
    if len(closes) < period:
        return None

    highest_high = max(highs[-period:])
    lowest_low = min(lows[-period:])

    if highest_high == lowest_low:
        return -50

    # Williams %R = (Highest High - Close) / (Highest High - Lowest Low) * -100
    wr = (highest_high - closes[-1]) / (highest_high - lowest_low) * -100
    return wr

def calc_adx(highs, lows, closes, period=14):
    """ADX (Average Directional Index) 계산 - 추세 강도"""
    if len(closes) < period + 1:
        return None

    # True Range, +DM, -DM 계산
    tr_list = []
    plus_dm_list = []
    minus_dm_list = []

    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr_list.append(tr)

        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < period:
        return None

    # Smoothed averages
    atr = sum(tr_list[-period:]) / period
    plus_di = (sum(plus_dm_list[-period:]) / period) / atr * 100 if atr > 0 else 0
    minus_di = (sum(minus_dm_list[-period:]) / period) / atr * 100 if atr > 0 else 0

    # DX
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0

    return dx  # 간단히 DX 반환 (ADX는 DX의 이동평균)

def calc_mfi(highs, lows, closes, volumes, period=14):
    """MFI (Money Flow Index) - 거래량 가중 RSI"""
    if len(closes) < period + 1:
        return None

    # Typical Price
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]

    positive_mf = 0
    negative_mf = 0

    for i in range(-period, 0):
        money_flow = tp[i] * volumes[i]
        if tp[i] > tp[i-1]:
            positive_mf += money_flow
        else:
            negative_mf += money_flow

    if negative_mf == 0:
        return 100

    money_ratio = positive_mf / negative_mf
    mfi = 100 - (100 / (1 + money_ratio))
    return mfi

def calc_momentum(closes, period=10):
    """모멘텀 계산"""
    if len(closes) < period + 1:
        return None

    # Momentum = Current Close - Close n periods ago
    momentum = (closes[-1] - closes[-period-1]) / closes[-period-1] * 100
    return momentum

def calc_roc(closes, period=10):
    """ROC (Rate of Change) 계산"""
    if len(closes) < period + 1:
        return None

    # ROC = ((Current - Previous) / Previous) * 100
    roc = (closes[-1] - closes[-period-1]) / closes[-period-1] * 100
    return roc

def calc_disparity(closes, period=20):
    """이격도 계산 (가격이 이평선에서 얼마나 벗어났는지)"""
    if len(closes) < period:
        return None

    ma = sum(closes[-period:]) / period
    disparity = (closes[-1] / ma) * 100
    return disparity

def detect_candle_pattern(opens, highs, lows, closes):
    """캔들 패턴 감지"""
    if len(closes) < 3:
        return {"doji": False, "hammer": False, "engulfing": False, "three_soldiers": False}

    result = {
        "doji": False,
        "hammer": False,
        "engulfing": False,
        "three_soldiers": False
    }

    # 마지막 캔들 분석
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l
    total_range = h - l

    if total_range > 0:
        # 도지: 몸통이 매우 작음
        if body / total_range < 0.1:
            result["doji"] = True

        # 망치: 긴 아래꼬리, 작은 위꼬리, 작은 몸통 (상승 신호)
        if lower_shadow > body * 2 and upper_shadow < body * 0.5:
            result["hammer"] = True

    # 장악형(Engulfing): 이전 캔들을 완전히 감싸는 큰 양봉
    if len(closes) >= 2:
        prev_o, prev_c = opens[-2], closes[-2]
        curr_o, curr_c = opens[-1], closes[-1]

        # 상승 장악형: 이전 음봉 + 현재 양봉이 이전을 완전히 감쌈
        if prev_c < prev_o and curr_c > curr_o:  # 이전 음봉, 현재 양봉
            if curr_o <= prev_c and curr_c >= prev_o:  # 완전히 감싸기
                result["engulfing"] = True

    # 삼병(Three Soldiers): 연속 3개의 상승 양봉
    if len(closes) >= 3:
        three_bullish = all(
            closes[-i] > opens[-i] and closes[-i] > closes[-i-1]
            for i in range(1, 4)
        )
        result["three_soldiers"] = three_bullish

    return result

def calc_price_acceleration(closes, period=5):
    """가격 가속도 (2차 미분)"""
    if len(closes) < period * 2 + 1:
        return None

    # 1차 미분 (속도)
    v1 = (sum(closes[-period:]) / period) - (sum(closes[-period*2:-period]) / period)
    v0 = (sum(closes[-period*2:-period]) / period) - (sum(closes[-period*3:-period*2]) / period) if len(closes) >= period * 3 else v1

    # 2차 미분 (가속도) - 속도의 변화
    base = sum(closes[-period*2:-period]) / period
    acceleration = (v1 - v0) / base * 100 if base > 0 else 0
    return acceleration

def analyze_deep(ticker, date_str, time_str):
    """딥 분석"""
    dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    to_time = (dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S") + "+09:00"

    # 1분봉 50개
    candles_1m = get_candles(ticker, to_time, 50, unit=1)
    time.sleep(0.1)

    # 5분봉 30개
    candles_5m = get_candles(ticker, to_time, 30, unit=5)

    if not candles_1m or len(candles_1m) < 35:
        return None

    candles_1m = list(reversed(candles_1m))
    if candles_5m:
        candles_5m = list(reversed(candles_5m))

    # 진입 시점 찾기
    target_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    entry_idx = None
    min_diff = 999999
    for i, c in enumerate(candles_1m):
        c_time_str = c["candle_date_time_kst"][:19]
        c_dt = datetime.fromisoformat(c_time_str)
        diff = abs((c_dt - target_dt).total_seconds())
        if diff < min_diff:
            min_diff = diff
            entry_idx = i

    if entry_idx is None or entry_idx < 30 or min_diff > 120:
        return None

    # 데이터 추출
    pre_candles = candles_1m[entry_idx-30:entry_idx]
    entry_candle = candles_1m[entry_idx]

    closes = [c["trade_price"] for c in pre_candles]
    highs = [c["high_price"] for c in pre_candles]
    lows = [c["low_price"] for c in pre_candles]
    volumes = [c["candle_acc_trade_volume"] for c in pre_candles]

    result = {"ticker": ticker, "time": f"{date_str} {time_str}"}

    # ==========================================
    # 1분봉 기본 지표
    # ==========================================

    # 가격 관련
    entry_price = entry_candle["trade_price"]
    result["entry_price"] = entry_price

    # 5봉 범위
    last5_highs = highs[-5:]
    last5_lows = lows[-5:]
    result["range_5"] = (max(last5_highs) - min(last5_lows)) / min(last5_lows) * 100

    # 10봉 범위
    last10_highs = highs[-10:]
    last10_lows = lows[-10:]
    result["range_10"] = (max(last10_highs) - min(last10_lows)) / min(last10_lows) * 100

    # 30봉 고저 대비 위치
    high_30 = max(highs)
    low_30 = min(lows)
    result["pos_30"] = (entry_price - low_30) / (high_30 - low_30) * 100 if high_30 > low_30 else 50

    # 저점/고점 상승 횟수
    higher_lows = sum(1 for i in range(1, 5) if lows[-5+i] >= lows[-5+i-1])
    higher_highs = sum(1 for i in range(1, 5) if highs[-5+i] >= highs[-5+i-1])
    result["higher_lows"] = higher_lows
    result["higher_highs"] = higher_highs

    # 양봉/음봉 비율 (최근 5봉)
    bullish_5 = sum(1 for c in pre_candles[-5:] if c["trade_price"] > c["opening_price"])
    result["bullish_ratio_5"] = bullish_5 / 5 * 100

    # 진입봉 특성
    result["entry_bullish"] = entry_candle["trade_price"] > entry_candle["opening_price"]
    entry_body = abs(entry_candle["trade_price"] - entry_candle["opening_price"]) / entry_candle["opening_price"] * 100
    result["entry_body_pct"] = entry_body

    # 거래량
    avg_vol = sum(volumes) / len(volumes) if volumes else 1
    entry_vol = entry_candle["candle_acc_trade_volume"]
    result["vol_ratio"] = entry_vol / avg_vol if avg_vol > 0 else 0

    # 최근 5봉 거래량 vs 이전 25봉
    recent_vol = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
    prev_vol = sum(volumes[:-5]) / 25 if len(volumes) >= 30 else 0
    result["vol_trend"] = recent_vol / prev_vol if prev_vol > 0 else 1

    # ==========================================
    # 기술적 지표 (1분봉)
    # ==========================================

    # RSI
    rsi = calc_rsi(closes, 14)
    result["rsi_14"] = rsi if rsi else 50

    # RSI 6 (단기)
    rsi_6 = calc_rsi(closes, 6)
    result["rsi_6"] = rsi_6 if rsi_6 else 50

    # MACD
    macd, _, _ = calc_macd(closes)
    result["macd"] = macd if macd else 0

    # 볼린저밴드
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes, 20)
    if bb_upper and bb_lower:
        bb_width = (bb_upper - bb_lower) / bb_mid * 100 if bb_mid else 0
        bb_pos = (entry_price - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper > bb_lower else 50
        result["bb_width"] = bb_width
        result["bb_pos"] = bb_pos  # 0=하단, 100=상단
    else:
        result["bb_width"] = 0
        result["bb_pos"] = 50

    # 스토캐스틱
    stoch_k, _ = calc_stochastic(highs, lows, closes)
    result["stoch_k"] = stoch_k if stoch_k else 50

    # ATR (변동성)
    atr = calc_atr(highs, lows, closes)
    result["atr"] = atr / entry_price * 100 if atr and entry_price > 0 else 0

    # OBV 추세
    obv_trend = calc_obv_trend(closes, volumes)
    result["obv_trend"] = obv_trend if obv_trend else 0

    # ==========================================
    # 추가 기술적 지표 (1분봉)
    # ==========================================

    # CCI (Commodity Channel Index)
    cci = calc_cci(highs, lows, closes)
    result["cci"] = cci if cci else 0

    # Williams %R
    williams_r = calc_williams_r(highs, lows, closes)
    result["williams_r"] = williams_r if williams_r else -50

    # ADX (추세 강도)
    adx = calc_adx(highs, lows, closes)
    result["adx"] = adx if adx else 0

    # MFI (Money Flow Index)
    mfi = calc_mfi(highs, lows, closes, volumes)
    result["mfi"] = mfi if mfi else 50

    # Momentum (10봉)
    momentum = calc_momentum(closes, 10)
    result["momentum_10"] = momentum if momentum else 0

    # ROC (Rate of Change)
    roc = calc_roc(closes, 10)
    result["roc_10"] = roc if roc else 0

    # 이격도 (20봉 이평 대비)
    disparity = calc_disparity(closes, 20)
    result["disparity_20"] = disparity if disparity else 100

    # 가격 가속도
    acceleration = calc_price_acceleration(closes, 5)
    result["price_accel"] = acceleration if acceleration else 0

    # 캔들 패턴
    opens = [c["opening_price"] for c in pre_candles]
    patterns = detect_candle_pattern(opens, highs, lows, closes)
    result["pattern_doji"] = 1 if patterns["doji"] else 0
    result["pattern_hammer"] = 1 if patterns["hammer"] else 0
    result["pattern_engulfing"] = 1 if patterns["engulfing"] else 0
    result["pattern_3soldiers"] = 1 if patterns["three_soldiers"] else 0

    # EMA 관계
    ema_5 = calc_ema(closes, 5)
    ema_10 = calc_ema(closes, 10)
    ema_20 = calc_ema(closes, 20)

    if ema_5 and ema_10 and ema_20:
        result["ema_5_10"] = (ema_5 / ema_10 - 1) * 100  # EMA5 vs EMA10
        result["ema_10_20"] = (ema_10 / ema_20 - 1) * 100  # EMA10 vs EMA20
        result["price_vs_ema20"] = (entry_price / ema_20 - 1) * 100
    else:
        result["ema_5_10"] = 0
        result["ema_10_20"] = 0
        result["price_vs_ema20"] = 0

    # ==========================================
    # 5분봉 지표
    # ==========================================
    if candles_5m and len(candles_5m) >= 10:
        closes_5m = [c["trade_price"] for c in candles_5m]
        highs_5m = [c["high_price"] for c in candles_5m]
        lows_5m = [c["low_price"] for c in candles_5m]

        # 5분봉 RSI
        rsi_5m = calc_rsi(closes_5m, 14)
        result["rsi_5m"] = rsi_5m if rsi_5m else 50

        # 5분봉 추세 (최근 5개 vs 이전 5개)
        if len(closes_5m) >= 10:
            recent_avg = sum(closes_5m[-5:]) / 5
            prev_avg = sum(closes_5m[-10:-5]) / 5
            result["trend_5m"] = (recent_avg / prev_avg - 1) * 100 if prev_avg > 0 else 0
        else:
            result["trend_5m"] = 0

        # 5분봉 볼린저밴드
        bb_upper_5m, bb_mid_5m, bb_lower_5m = calc_bollinger(closes_5m, 20)
        if bb_upper_5m and bb_lower_5m:
            result["bb_pos_5m"] = (entry_price - bb_lower_5m) / (bb_upper_5m - bb_lower_5m) * 100
        else:
            result["bb_pos_5m"] = 50
    else:
        result["rsi_5m"] = 50
        result["trend_5m"] = 0
        result["bb_pos_5m"] = 50

    # ==========================================
    # 시간대 분석
    # ==========================================
    hour = int(time_str.split(":")[0])
    result["hour"] = hour
    result["is_morning"] = 8 <= hour <= 10  # 장 초반
    result["is_afternoon"] = 13 <= hour <= 16  # 오후
    result["is_night"] = hour >= 20 or hour <= 6  # 밤

    return result

def main():
    print("=" * 80)
    print("🔬 딥 분석 v3 - 성공/실패 완전 비교")
    print("=" * 80)

    success_results = []
    fail_results = []

    for ticker, date_str, time_str, is_success in CASES:
        label = "✅" if is_success else "❌"
        print(f"분석 중: {label} {ticker} @ {date_str} {time_str}...", end=" ")
        result = analyze_deep(ticker, date_str, time_str)
        if result:
            result["is_success"] = is_success
            if is_success:
                success_results.append(result)
            else:
                fail_results.append(result)
            print("✓")
        else:
            print("✗")
        time.sleep(0.2)

    if not success_results or not fail_results:
        print("\n성공/실패 케이스 모두 필요합니다.")
        return

    # ==========================================
    # 성공 vs 실패 비교
    # ==========================================
    print("\n")
    print("=" * 80)
    print("⚖️ 성공 vs 실패 완전 비교")
    print("=" * 80)

    all_metrics = [
        ("range_5", "5봉 범위(%)"),
        ("range_10", "10봉 범위(%)"),
        ("pos_30", "30봉 내 위치(%)"),
        ("higher_lows", "저점상승 횟수"),
        ("higher_highs", "고점상승 횟수"),
        ("bullish_ratio_5", "양봉비율(%)"),
        ("entry_body_pct", "진입봉 몸통(%)"),
        ("vol_ratio", "진입봉 거래량배수"),
        ("vol_trend", "거래량 추세"),
        ("rsi_14", "RSI(14)"),
        ("rsi_6", "RSI(6)"),
        ("macd", "MACD"),
        ("bb_width", "BB폭(%)"),
        ("bb_pos", "BB위치(%)"),
        ("stoch_k", "스토캐스틱K"),
        ("atr", "ATR(%)"),
        ("obv_trend", "OBV추세"),
        # 추가 지표
        ("cci", "CCI"),
        ("williams_r", "Williams %R"),
        ("adx", "ADX(추세강도)"),
        ("mfi", "MFI"),
        ("momentum_10", "모멘텀(10)"),
        ("roc_10", "ROC(10)"),
        ("disparity_20", "이격도(20)"),
        ("price_accel", "가격가속도"),
        # 캔들패턴
        ("pattern_doji", "도지패턴"),
        ("pattern_hammer", "망치패턴"),
        ("pattern_engulfing", "장악형패턴"),
        ("pattern_3soldiers", "삼병패턴"),
        # EMA
        ("ema_5_10", "EMA5/10(%)"),
        ("ema_10_20", "EMA10/20(%)"),
        ("price_vs_ema20", "가격/EMA20(%)"),
        # 5분봉
        ("rsi_5m", "RSI(5분봉)"),
        ("trend_5m", "5분봉추세(%)"),
        ("bb_pos_5m", "BB위치(5분봉)"),
    ]

    print(f"\n{'지표':<18} | {'성공(평균/중앙)':>16} | {'실패(평균/중앙)':>16} | {'판별력':>6} | {'신뢰도':>6}")
    print("-" * 90)

    discriminators = []  # 판별력 있는 지표 저장

    for key, label in all_metrics:
        s_vals = [r[key] for r in success_results if key in r and r[key] is not None]
        f_vals = [r[key] for r in fail_results if key in r and r[key] is not None]

        if s_vals and f_vals:
            s_avg = statistics.mean(s_vals)
            f_avg = statistics.mean(f_vals)
            s_med = statistics.median(s_vals)
            f_med = statistics.median(f_vals)
            diff = s_avg - f_avg
            diff_med = s_med - f_med

            # 🔧 신뢰도 계산: |평균-중앙값|/중앙값 (낮을수록 좋음)
            s_reliability = abs(s_avg - s_med) / abs(s_med) * 100 if s_med != 0 else 0
            f_reliability = abs(f_avg - f_med) / abs(f_med) * 100 if f_med != 0 else 0
            avg_reliability = (s_reliability + f_reliability) / 2

            # 판별력 계산 (중앙값 기준으로도 계산)
            try:
                all_vals = s_vals + f_vals
                std = statistics.stdev(all_vals) if len(all_vals) > 1 else 1
                discriminant = abs(diff) / std if std > 0 else 0
                discriminant_med = abs(diff_med) / std if std > 0 else 0
            except:
                discriminant = 0
                discriminant_med = 0

            # 더 보수적인 판별력 사용 (평균과 중앙값 중 낮은 것)
            final_disc = min(discriminant, discriminant_med)

            # 판별력 0.5 이상이면 ★, 신뢰도 15% 이하면 ◆ 표시
            star = "★" if final_disc >= 0.5 else ""
            reliable = "◆" if avg_reliability <= 15 else ""
            print(f"{label:<18} | {s_avg:>6.2f}/{s_med:>6.2f} | {f_avg:>6.2f}/{f_med:>6.2f} | {final_disc:>5.2f}{star} | {avg_reliability:>5.1f}%{reliable}")

            if final_disc >= 0.5:
                discriminators.append((label, s_avg, s_med, f_avg, f_med, diff, final_disc, avg_reliability))

    # ==========================================
    # 핵심 판별 지표
    # ==========================================
    print("\n")
    print("=" * 80)
    print("🎯 핵심 판별 지표 (판별력 0.5 이상)")
    print("=" * 80)

    discriminators.sort(key=lambda x: x[6], reverse=True)

    for item in discriminators:
        label, s_avg, s_med, f_avg, f_med, diff, disc = item[:7]
        reliability = item[7] if len(item) > 7 else 0
        direction = "성공이 높음" if diff > 0 else "실패가 높음"
        reliable_str = "✓신뢰" if reliability <= 15 else "△편차큼"
        print(f"  ★ {label}:")
        print(f"      평균: 성공 {s_avg:.2f} vs 실패 {f_avg:.2f}")
        print(f"      중앙: 성공 {s_med:.2f} vs 실패 {f_med:.2f}")
        print(f"      ({direction}, 판별력 {disc:.2f}, {reliable_str} {reliability:.1f}%)")

    # ==========================================
    # 시간대별 분석
    # ==========================================
    print("\n")
    print("=" * 80)
    print("🕐 시간대별 성공률")
    print("=" * 80)

    # 아침 (8-10시)
    s_morning = sum(1 for r in success_results if r.get("is_morning"))
    f_morning = sum(1 for r in fail_results if r.get("is_morning"))

    # 오후 (13-16시)
    s_afternoon = sum(1 for r in success_results if r.get("is_afternoon"))
    f_afternoon = sum(1 for r in fail_results if r.get("is_afternoon"))

    # 밤 (20시-6시)
    s_night = sum(1 for r in success_results if r.get("is_night"))
    f_night = sum(1 for r in fail_results if r.get("is_night"))

    print(f"  아침(8-10시): 성공 {s_morning} / 실패 {f_morning}")
    print(f"  오후(13-16시): 성공 {s_afternoon} / 실패 {f_afternoon}")
    print(f"  밤(20-06시): 성공 {s_night} / 실패 {f_night}")

    # ==========================================
    # 권장 임계치
    # ==========================================
    print("\n")
    print("=" * 80)
    print("💡 권장 진입 조건")
    print("=" * 80)

    if discriminators:
        print("\n핵심 판별 지표 기반 조건 (중앙값 기준):")
        for label, s_avg, s_med, f_avg, f_med, diff, disc in discriminators[:5]:  # 상위 5개
            # 성공과 실패의 중앙값 중간을 임계치로 제안
            threshold = (s_med + f_med) / 2
            if diff > 0:
                print(f"  - {label} >= {threshold:.2f} (성공 중앙값 {s_med:.2f}, 실패 중앙값 {f_med:.2f})")
            else:
                print(f"  - {label} <= {threshold:.2f} (성공 중앙값 {s_med:.2f}, 실패 중앙값 {f_med:.2f})")

if __name__ == "__main__":
    main()
