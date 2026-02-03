# -*- coding: utf-8 -*-
"""기술 지표 순수 함수 (글로벌 상태 의존 없음)"""
import time


def inter_arrival_stats(ticks, sec=30):
    """틱 간 도착 시간 통계 (CV, count)"""
    if not ticks:
        return {"cv": None, "count": 0}
    try:
        newest_ts = max(t["timestamp"] for t in ticks)
    except Exception:
        return {"cv": None, "count": 0}
    cutoff = newest_ts - sec * 1000
    ts = sorted(x["timestamp"] for x in ticks if x["timestamp"] >= cutoff)
    if len(ts) < 4:
        return {"cv": None, "count": len(ts)}
    gaps = [(b - a) / 1000.0 for a, b in zip(ts, ts[1:])]
    mu = sum(gaps) / len(gaps)
    if mu <= 0:
        return {"cv": None, "count": len(ts)}
    var = sum((g - mu) ** 2 for g in gaps) / len(gaps)
    cv = (var ** 0.5) / mu
    return {"cv": cv, "count": len(ts)}


def price_band_std(ticks, sec=30):
    """가격대 표준편차 (정규화)"""
    if not ticks:
        return None
    try:
        newest_ts = max(t["timestamp"] for t in ticks)
    except Exception:
        return None
    cutoff = newest_ts - sec * 1000
    ps = [x["trade_price"] for x in ticks if x["timestamp"] >= cutoff]
    if len(ps) < 3:
        return None
    m = sum(ps) / len(ps)
    var = sum((p - m) ** 2 for p in ps) / len(ps)
    std = (var ** 0.5) / max(m, 1)
    return std


def micro_tape_stats(ticks, sec):
    """틱 테이프 통계 (거래대금, 매수비, 속도 등)"""
    empty = {"krw": 0, "n": 0, "buy_ratio": 0, "age": 999, "rate": 0, "krw_per_sec": 0}
    if not ticks:
        return empty
    try:
        newest_ts = max(t["timestamp"] for t in ticks)
        cutoff = newest_ts - sec * 1000
    except Exception:
        return empty

    n = 0
    krw = 0.0
    buys = 0
    oldest_ts = newest_ts
    for x in ticks:
        ts = x.get("timestamp", 0)
        if ts < cutoff:
            continue
        p = x.get("trade_price", 0.0)
        v = x.get("trade_volume", 0.0)
        krw += p * v
        n += 1
        if x.get("ask_bid") == "BID":
            buys += 1
        if ts < oldest_ts:
            oldest_ts = ts

    if n == 0:
        return empty

    now_ms = int(time.time() * 1000)
    age = (now_ms - newest_ts) / 1000.0 if newest_ts else 999
    duration = max((newest_ts - (oldest_ts or newest_ts)) / 1000.0, 1.0)
    return {
        "krw": krw,
        "n": n,
        "buy_ratio": buys / n,
        "age": age,
        "rate": n / duration,
        "krw_per_sec": krw / duration,
    }


def calc_consecutive_buys(ticks, sec=15):
    """최근 N초 내 연속 매수 체결 최대 횟수"""
    if not ticks:
        return 0
    try:
        newest_ts = max(t["timestamp"] for t in ticks)
        cutoff = newest_ts - sec * 1000
    except Exception:
        return 0
    max_streak = 0
    current_streak = 0
    for x in ticks:
        if x.get("timestamp", 0) < cutoff:
            continue
        if x.get("ask_bid") == "BID":
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def calc_avg_krw_per_tick(t_stats):
    """틱당 평균금액"""
    if not t_stats or t_stats.get("n", 0) == 0:
        return 0
    return t_stats["krw"] / t_stats["n"]


def calc_flow_acceleration(ticks):
    """체결 가속도: t5s / t15s 비율"""
    if not ticks:
        return 1.0
    t5s = micro_tape_stats(ticks, 5)
    t15s = micro_tape_stats(ticks, 15)
    if t15s["krw_per_sec"] <= 0:
        return 1.0
    return t5s["krw_per_sec"] / t15s["krw_per_sec"]


def vwap_from_candles_1m(c1, n=20):
    """1분봉 기반 VWAP"""
    seg = c1[-n:] if len(c1) >= n else c1[:]
    pv = sum(x["trade_price"] * x["candle_acc_trade_volume"] for x in seg)
    vol = sum(x["candle_acc_trade_volume"] for x in seg)
    return pv / max(vol, 1e-12)


def zscore_krw_1m(c1, win=30):
    """1분봉 거래대금 Z-score"""
    seg = c1[-win:] if len(c1) >= win else c1[:]
    arr = [x["candle_acc_trade_price"] for x in seg]
    if len(arr) < 3:
        return 0.0
    m = sum(arr) / len(arr)
    sd = (sum((a - m) ** 2 for a in arr) / max(len(arr) - 1, 1)) ** 0.5
    return (arr[-1] - m) / max(sd, 1e-9)


def uptick_streak_from_ticks(ticks, need=2):
    """연속 상승틱 체크
    🔧 FIX: 틱 정렬 계약(과거→최신) 반영 — 최근 틱은 끝에서 슬라이싱, 이미 정렬됨
    """
    t = ticks[-(need + 4):]
    return (
        sum(
            1
            for a, b in zip(t, t[1:])
            if b.get("trade_price", 0) > a.get("trade_price", 0)
        )
        >= need
    )


def body_ratio(c):
    """캔들 몸통 비율"""
    try:
        return max(
            (c["trade_price"] - c["opening_price"]) / max(c["opening_price"], 1), 0
        )
    except Exception:
        return 0


def calc_orderbook_imbalance(ob):
    """1~3호가 가중 평균 임밸런스 (-1.0 ~ +1.0)"""
    try:
        units = ob["raw"]["orderbook_units"][:3]
        bid_weighted = sum(
            u["bid_size"] * u["bid_price"] * (3 - i) for i, u in enumerate(units)
        )
        ask_weighted = sum(
            u["ask_size"] * u["ask_price"] * (3 - i) for i, u in enumerate(units)
        )
        total = bid_weighted + ask_weighted
        if total <= 0:
            return 0.0
        imbalance = (bid_weighted - ask_weighted) / total
        return max(-1.0, min(1.0, imbalance))
    except Exception:
        return 0.0


def running_1m_bar(ticks, last_candle=None):
    """틱 데이터로 현재 진행 중인 1분봉을 실시간 계산"""
    if not ticks:
        return None

    now_ms = int(time.time() * 1000)
    minute_start = (now_ms // 60000) * 60000

    current_ticks = [t for t in ticks if t.get("timestamp", 0) >= minute_start]

    if not current_ticks:
        fallback_cutoff = now_ms - 10000
        current_ticks = [t for t in ticks if t.get("timestamp", 0) >= fallback_cutoff]
        if not current_ticks:
            return None

    current_ticks = sorted(current_ticks, key=lambda t: t.get("timestamp", 0))
    prices = [t.get("trade_price", 0) for t in current_ticks if t.get("trade_price", 0) > 0]
    if not prices:
        return None

    o = prices[0]
    h = max(prices)
    lo = min(prices)
    c = prices[-1]
    vol_krw = sum(
        t.get("trade_price", 0) * t.get("trade_volume", 0) for t in current_ticks
    )
    n = len(current_ticks)
    buys = sum(1 for t in current_ticks if t.get("ask_bid") == "BID")

    change_from_prev = 0.0
    if last_candle and last_candle.get("trade_price", 0) > 0:
        change_from_prev = (c / last_candle["trade_price"]) - 1.0

    range_pct = (h - lo) / max(lo, 1) if lo > 0 else 0.0

    return {
        "open": o,
        "high": h,
        "low": lo,
        "close": c,
        "volume_krw": vol_krw,
        "tick_count": n,
        "buy_ratio": buys / n if n > 0 else 0,
        "change_from_prev": change_from_prev,
        "range_pct": range_pct,
    }
