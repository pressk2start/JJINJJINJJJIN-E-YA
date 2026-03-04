# -*- coding: utf-8 -*-
"""
업비트 24시간 캔들 데이터 분석 스크립트
- 거래량 상위 30종목
- 1분봉, 5분봉, 15분봉 24시간 수집
- 진입/청산 시뮬레이션으로 핵심 팩터 도출
"""
import requests, time, statistics, json, math
from datetime import datetime, timedelta, timezone
from collections import defaultdict

KST = timezone(timedelta(hours=9))
BASE = "https://api.upbit.com/v1"
FEE = 0.0005  # 편도 수수료
SLIP = 0.0008  # 편도 슬리피지
ROUNDTRIP_COST = (FEE + SLIP) * 2  # 왕복 ~0.26%

def safe_get(url, params=None, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                time.sleep(1 + i)
                continue
            else:
                time.sleep(0.5)
        except Exception as e:
            time.sleep(0.5)
    return None

# 1) 거래량 상위 30종목 가져오기
def get_top30_markets():
    tickers = safe_get(f"{BASE}/market/all", {"isDetails": "true"})
    if not tickers:
        return []
    krw_markets = [t["market"] for t in tickers if t["market"].startswith("KRW-")]

    # 24h 거래대금 기준 정렬
    ticker_data = safe_get(f"{BASE}/ticker", {"markets": ",".join(krw_markets[:100])})
    if not ticker_data:
        return []

    sorted_tickers = sorted(ticker_data, key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
    top30 = [t["market"] for t in sorted_tickers[:30]]
    print(f"[TOP30] {', '.join([m.split('-')[1] for m in top30])}")
    return top30

# 2) 캔들 수집 (최대 200개씩)
def get_candles(unit, market, count=200, to=None):
    """1분/5분/15분봉 수집"""
    params = {"market": market, "count": min(count, 200)}
    if to:
        params["to"] = to
    data = safe_get(f"{BASE}/candles/minutes/{unit}", params)
    return data if data else []

def get_all_candles(unit, market, total_needed):
    """24시간치 캔들 모두 수집"""
    all_candles = []
    to = None
    while len(all_candles) < total_needed:
        batch = get_candles(unit, market, 200, to)
        if not batch:
            break
        all_candles.extend(batch)
        to = batch[-1]["candle_date_time_utc"] + "Z"
        time.sleep(0.12)  # rate limit
    # 시간순 정렬 (오래된 → 최신)
    all_candles.sort(key=lambda x: x["candle_date_time_kst"])
    return all_candles[:total_needed]

# 3) 기술 지표 계산
def calc_ema(data, period):
    if len(data) < period:
        return None
    mult = 2.0 / (period + 1)
    ema = data[0]
    for v in data[1:]:
        ema = v * mult + ema * (1 - mult)
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = 0, 0
    for i in range(1, period + 1):
        d = closes[-period - 1 + i] - closes[-period - 1 + i - 1]
        if d > 0: gains += d
        else: losses -= d
    rs = gains / max(losses, 0.001)
    return 100 - 100 / (1 + rs)

def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high_price"]
        l = candles[i]["low_price"]
        pc = candles[i-1]["trade_price"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs[-period:]) / period if len(trs) >= period else None

def calc_vwap(candles, period=20):
    if len(candles) < period:
        return None
    total_vol = 0
    total_pv = 0
    for c in candles[-period:]:
        tp = (c["high_price"] + c["low_price"] + c["trade_price"]) / 3
        v = c["candle_acc_trade_volume"]
        total_pv += tp * v
        total_vol += v
    return total_pv / total_vol if total_vol > 0 else None

def calc_bb_width(closes, period=20):
    if len(closes) < period:
        return None
    sma = sum(closes[-period:]) / period
    if sma <= 0:
        return None
    var = sum((c - sma)**2 for c in closes[-period:]) / period
    std = var ** 0.5
    return (4 * std) / sma  # (upper - lower) / middle

def vol_surge(candles, idx, lookback=5):
    if idx < lookback + 1:
        return 1.0
    cur_vol = candles[idx]["candle_acc_trade_price"]
    past = [candles[i]["candle_acc_trade_price"] for i in range(idx-lookback, idx)]
    avg = sum(past) / len(past) if past else 1
    return cur_vol / max(avg, 1)

def body_pct(c):
    op = c["opening_price"]
    cl = c["trade_price"]
    return (cl - op) / max(op, 1)

def upper_wick_ratio(c):
    h = c["high_price"]
    l = c["low_price"]
    cl = c["trade_price"]
    op = c["opening_price"]
    rng = h - l
    if rng <= 0:
        return 0
    return (h - max(cl, op)) / rng

def green_streak(candles, idx):
    count = 0
    for i in range(idx, -1, -1):
        if candles[i]["trade_price"] > candles[i]["opening_price"]:
            count += 1
        else:
            break
    return count

# 4) 시뮬레이션: 각 캔들에서 진입 → 미래 N봉 후 결과 분석
def simulate_entries(candles_1m, candles_5m, candles_15m, market):
    """
    1분봉 기준으로 잠재 진입 신호를 감지하고,
    진입 후 결과(MFE, MAE, PnL)를 기록
    """
    results = []

    if len(candles_1m) < 30:
        return results

    # 5분봉/15분봉 시간 인덱스 매핑 (가장 가까운 봉 찾기)
    def find_nearest(candles, target_time):
        for i in range(len(candles)-1, -1, -1):
            if candles[i]["candle_date_time_kst"] <= target_time:
                return i
        return 0

    for idx in range(25, len(candles_1m) - 15):  # 앞뒤 여유
        c = candles_1m[idx]
        prev = candles_1m[idx-1]

        close = c["trade_price"]
        open_p = c["opening_price"]
        high = c["high_price"]
        low = c["low_price"]

        if close <= 0 or open_p <= 0:
            continue

        # --- 진입 조건 계산 ---
        closes = [candles_1m[i]["trade_price"] for i in range(max(0,idx-24), idx+1)]

        # 기본 지표
        bp = body_pct(c)
        uwr = upper_wick_ratio(c)
        gs = green_streak(candles_1m, idx)
        vs = vol_surge(candles_1m, idx, 5)

        # EMA
        ema5 = calc_ema(closes, 5)
        ema20 = calc_ema(closes, 20)
        ema_breakout = 1 if (ema20 and close > ema20) else 0
        ema_gap = ((close - ema20) / ema20) if ema20 and ema20 > 0 else 0

        # RSI
        rsi = calc_rsi(closes, 14) if len(closes) >= 15 else 50

        # ATR
        atr_val = calc_atr(candles_1m[max(0,idx-15):idx+1], 14)
        atr_pct = (atr_val / close) if atr_val and close > 0 else 0.005

        # 고점 돌파
        prev_highs = [candles_1m[i]["high_price"] for i in range(max(0,idx-12), idx)]
        prev_high = max(prev_highs) if prev_highs else 0
        high_break = 1 if high > prev_high and prev_high > 0 else 0

        # BB width
        bb_w = calc_bb_width(closes, 20)

        # VWAP gap
        vwap = calc_vwap(candles_1m[max(0,idx-20):idx+1], min(20, idx+1))
        vwap_gap = ((close - vwap) / vwap * 100) if vwap and vwap > 0 else 0

        # 거래량
        cur_vol = c["candle_acc_trade_price"]
        vol_ma20_vals = [candles_1m[i]["candle_acc_trade_price"] for i in range(max(0,idx-20), idx)]
        vol_ma20 = sum(vol_ma20_vals) / len(vol_ma20_vals) if vol_ma20_vals else 1
        vol_vs_ma = cur_vol / max(vol_ma20, 1)

        # 5분봉 컨텍스트
        t5m = c["candle_date_time_kst"]
        idx5 = find_nearest(candles_5m, t5m)
        rsi_5m = 50
        if idx5 >= 15 and len(candles_5m) > idx5:
            closes_5m = [candles_5m[i]["trade_price"] for i in range(max(0,idx5-15), idx5+1)]
            rsi_5m = calc_rsi(closes_5m, 14)

        # 15분봉 컨텍스트
        idx15 = find_nearest(candles_15m, t5m)
        rsi_15m = 50
        if idx15 >= 15 and len(candles_15m) > idx15:
            closes_15m = [candles_15m[i]["trade_price"] for i in range(max(0,idx15-15), idx15+1)]
            rsi_15m = calc_rsi(closes_15m, 14)

        # 가격변화율
        price_chg = (close / max(prev["trade_price"], 1) - 1)

        # 시간대
        try:
            hour = int(c["candle_date_time_kst"][11:13])
        except:
            hour = 12

        # 모멘텀 (최근 5봉 변화율)
        if idx >= 5:
            mom5 = (close - candles_1m[idx-5]["trade_price"]) / max(candles_1m[idx-5]["trade_price"], 1)
        else:
            mom5 = 0

        # --- 미래 결과 시뮬레이션 (진입 → 15분 후까지) ---
        entry_price = close * (1 + SLIP + FEE)  # 진입 비용

        mfe = 0  # Maximum Favorable Excursion
        mae = 0  # Maximum Adverse Excursion
        mfe_bar = 0  # MFE 도달 봉
        exit_price = None
        exit_reason = None
        exit_bar = 0

        # SL/TP 시뮬
        sl_pct = max(0.012, min(0.018, atr_pct * 0.85))  # 동적 SL
        trail_dist = max(0.003, atr_pct * 0.5)
        cp = max(0.0026, 0.0045)  # checkpoint

        trail_armed = False
        trail_stop = 0
        best_price = entry_price

        for fwd in range(1, min(16, len(candles_1m) - idx)):
            fc = candles_1m[idx + fwd]
            fh = fc["high_price"]
            fl = fc["low_price"]
            fcl = fc["trade_price"]

            # MFE/MAE 갱신
            cur_mfe = (fh / entry_price - 1)
            cur_mae = (fl / entry_price - 1)
            if cur_mfe > mfe:
                mfe = cur_mfe
                mfe_bar = fwd
            if cur_mae < mae:
                mae = cur_mae

            best_price = max(best_price, fh)
            gain = (fcl / entry_price - 1)

            # SL 체크 (저가 기준)
            if (fl / entry_price - 1) <= -sl_pct:
                exit_price = entry_price * (1 - sl_pct)
                exit_reason = "SL"
                exit_bar = fwd
                break

            # 하드스톱
            if (fl / entry_price - 1) <= -(sl_pct * 1.5):
                exit_price = entry_price * (1 - sl_pct * 1.5)
                exit_reason = "HARD_STOP"
                exit_bar = fwd
                break

            # Trail 무장
            if not trail_armed and gain >= cp:
                trail_armed = True
                trail_stop = max(entry_price * (1 + FEE), fcl * (1 - trail_dist))

            if trail_armed:
                trail_stop = max(trail_stop, fh * (1 - trail_dist))
                if fl <= trail_stop:
                    exit_price = trail_stop
                    exit_reason = "TRAIL"
                    exit_bar = fwd
                    break

        if exit_price is None:
            # 15분 시간만료
            last_c = candles_1m[min(idx + 15, len(candles_1m) - 1)]
            exit_price = last_c["trade_price"]
            exit_reason = "TIMEOUT"
            exit_bar = 15

        pnl = (exit_price / entry_price - 1) - FEE - SLIP  # 청산 비용

        results.append({
            "market": market,
            "hour": hour,
            "body_pct": round(bp * 100, 3),
            "uwr": round(uwr, 3),
            "green_streak": gs,
            "vol_surge": round(vs, 2),
            "vol_vs_ma": round(vol_vs_ma, 2),
            "ema_break": ema_breakout,
            "ema_gap": round(ema_gap * 100, 3),
            "high_break": high_break,
            "rsi_1m": round(rsi, 1),
            "rsi_5m": round(rsi_5m, 1),
            "rsi_15m": round(rsi_15m, 1),
            "atr_pct": round(atr_pct * 100, 3),
            "price_chg": round(price_chg * 100, 3),
            "bb_width": round(bb_w * 100, 2) if bb_w else None,
            "vwap_gap": round(vwap_gap, 2),
            "mom5": round(mom5 * 100, 3),
            "sl_pct": round(sl_pct * 100, 2),
            "mfe": round(mfe * 100, 3),
            "mae": round(mae * 100, 3),
            "mfe_bar": mfe_bar,
            "pnl": round(pnl * 100, 3),
            "exit_reason": exit_reason,
            "exit_bar": exit_bar,
            "win": 1 if pnl > 0 else 0,
        })

    return results

# 5) 팩터별 통계 분석
def analyze_factor(results, factor_name, bins=None, percentiles=None):
    """특정 팩터의 구간별 승률/PnL 분석"""
    if not results:
        return {}

    vals = [r[factor_name] for r in results if r.get(factor_name) is not None]
    if not vals:
        return {}

    if bins is None and percentiles is None:
        # 자동 구간: 4분위
        sorted_vals = sorted(vals)
        n = len(sorted_vals)
        percentiles = [sorted_vals[int(n*0.25)], sorted_vals[int(n*0.5)], sorted_vals[int(n*0.75)]]
        bins = [float('-inf')] + percentiles + [float('inf')]
    elif percentiles:
        bins = [float('-inf')] + list(percentiles) + [float('inf')]

    stats = {}
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i+1]
        subset = [r for r in results if r.get(factor_name) is not None and lo <= r[factor_name] < hi]
        if len(subset) < 5:
            continue
        wins = sum(r["win"] for r in subset)
        wr = wins / len(subset)
        avg_pnl = statistics.mean([r["pnl"] for r in subset])
        avg_mfe = statistics.mean([r["mfe"] for r in subset])
        avg_mae = statistics.mean([r["mae"] for r in subset])

        label = f"[{lo:.2f}, {hi:.2f})" if hi != float('inf') else f"[{lo:.2f}, ∞)"
        if lo == float('-inf'):
            label = f"(-∞, {hi:.2f})"

        stats[label] = {
            "n": len(subset),
            "wr": round(wr * 100, 1),
            "avg_pnl": round(avg_pnl, 3),
            "avg_mfe": round(avg_mfe, 3),
            "avg_mae": round(avg_mae, 3),
        }

    return stats

def analyze_combo(results, f1, f1_thresh, f2, f2_thresh, mode=">="):
    """2팩터 콤보 분석"""
    if mode == ">=":
        subset_both = [r for r in results if r.get(f1) is not None and r.get(f2) is not None
                       and r[f1] >= f1_thresh and r[f2] >= f2_thresh]
        subset_f1 = [r for r in results if r.get(f1) is not None and r[f1] >= f1_thresh]
        subset_f2 = [r for r in results if r.get(f2) is not None and r[f2] >= f2_thresh]
    else:
        subset_both = [r for r in results if r.get(f1) is not None and r.get(f2) is not None
                       and r[f1] < f1_thresh and r[f2] < f2_thresh]
        subset_f1 = [r for r in results if r.get(f1) is not None and r[f1] < f1_thresh]
        subset_f2 = [r for r in results if r.get(f2) is not None and r[f2] < f2_thresh]

    def _stats(s):
        if len(s) < 3:
            return None
        w = sum(r["win"] for r in s)
        return {"n": len(s), "wr": round(w/len(s)*100,1), "pnl": round(statistics.mean([r["pnl"] for r in s]),3)}

    return {
        "both": _stats(subset_both),
        f1+"_only": _stats(subset_f1),
        f2+"_only": _stats(subset_f2),
    }

# 6) SL/Trail 최적화 분석
def analyze_exit_params(results):
    """최적 SL, Trail 파라미터 탐색"""
    print("\n" + "="*60)
    print("📊 EXIT PARAMETER OPTIMIZATION")
    print("="*60)

    # MFE/MAE 분포
    mfes = [r["mfe"] for r in results if r["mfe"] > 0]
    maes = [r["mae"] for r in results]

    if mfes:
        print(f"\n[MFE 분포] (% 단위)")
        for p in [25, 50, 75, 90]:
            val = sorted(mfes)[int(len(mfes)*p/100)]
            print(f"  P{p}: +{val:.3f}%")

    if maes:
        print(f"\n[MAE 분포] (% 단위)")
        for p in [10, 25, 50, 75]:
            val = sorted(maes)[int(len(maes)*p/100)]
            print(f"  P{p}: {val:.3f}%")

    # SL별 결과 시뮬
    print(f"\n[SL 민감도 분석]")
    for sl in [0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5]:
        sl_dec = sl / 100
        stopped = sum(1 for r in results if r["mae"] <= -sl)
        total = len(results)
        # 진짜 하락 vs 노이즈 구분
        noise_stops = sum(1 for r in results if r["mae"] <= -sl and r["mfe"] > sl * 0.5)
        print(f"  SL {sl:.1f}%: 피격 {stopped}/{total} ({stopped/total*100:.1f}%) | 노이즈손절(MFE>{sl*0.5:.1f}%인데SL) {noise_stops}건 ({noise_stops/max(stopped,1)*100:.0f}%)")

    # Trail distance별 결과
    print(f"\n[Trail 거리 민감도 (CP 도달 후)]")
    cp_reached = [r for r in results if r["mfe"] >= 0.45]  # CP=0.45% 도달한 건
    for td in [0.15, 0.20, 0.25, 0.30, 0.40, 0.50]:
        # trail로 잡히는 수익 추정
        captured = []
        for r in cp_reached:
            max_trail_stop_gain = r["mfe"] - td
            actual_gain = min(r["pnl"], max_trail_stop_gain)
            captured.append(max(actual_gain, r["mae"]))  # SL이 먼저 걸렸을 수도
        if captured:
            avg_cap = statistics.mean(captured)
            print(f"  Trail {td:.2f}%: 평균포착 {avg_cap:.3f}% (CP도달 {len(cp_reached)}건)")

    # MFE bar 분포 (최고점까지 걸리는 시간)
    print(f"\n[MFE 도달 시간 분포]")
    mfe_bars = [r["mfe_bar"] for r in results if r["mfe"] > 0.3]
    if mfe_bars:
        for p in [25, 50, 75, 90]:
            val = sorted(mfe_bars)[int(len(mfe_bars)*p/100)]
            print(f"  P{p}: {val}분")

# 7) 시간대별 분석
def analyze_hourly(results):
    print("\n" + "="*60)
    print("📊 HOURLY ANALYSIS")
    print("="*60)

    by_hour = defaultdict(list)
    for r in results:
        by_hour[r["hour"]].append(r)

    for h in sorted(by_hour.keys()):
        subset = by_hour[h]
        if len(subset) < 5:
            continue
        wr = sum(r["win"] for r in subset) / len(subset) * 100
        avg_pnl = statistics.mean([r["pnl"] for r in subset])
        avg_mfe = statistics.mean([r["mfe"] for r in subset])
        print(f"  {h:02d}시: n={len(subset):4d} WR={wr:5.1f}% PnL={avg_pnl:+.3f}% MFE={avg_mfe:.3f}%")

# 8) 종합 팩터 중요도 분석
def factor_importance(results):
    """각 팩터의 승률 예측력 분석 (상위25% vs 하위25% 승률 차이)"""
    print("\n" + "="*60)
    print("📊 FACTOR IMPORTANCE (상위25% vs 하위25% 승률 차이)")
    print("="*60)

    factors = ["body_pct", "vol_surge", "vol_vs_ma", "ema_gap", "rsi_1m", "rsi_5m", "rsi_15m",
               "atr_pct", "price_chg", "vwap_gap", "mom5", "uwr", "green_streak"]

    importance = []
    for f in factors:
        vals = sorted([r for r in results if r.get(f) is not None], key=lambda x: x[f])
        if len(vals) < 20:
            continue
        n = len(vals)
        q1 = vals[:n//4]
        q4 = vals[3*n//4:]

        wr_q1 = sum(r["win"] for r in q1) / len(q1) * 100 if q1 else 0
        wr_q4 = sum(r["win"] for r in q4) / len(q4) * 100 if q4 else 0
        pnl_q1 = statistics.mean([r["pnl"] for r in q1]) if q1 else 0
        pnl_q4 = statistics.mean([r["pnl"] for r in q4]) if q4 else 0

        importance.append({
            "factor": f,
            "wr_diff": round(wr_q4 - wr_q1, 1),
            "pnl_diff": round(pnl_q4 - pnl_q1, 3),
            "wr_q1": round(wr_q1, 1),
            "wr_q4": round(wr_q4, 1),
            "pnl_q1": round(pnl_q1, 3),
            "pnl_q4": round(pnl_q4, 3),
        })

    importance.sort(key=lambda x: abs(x["wr_diff"]), reverse=True)

    for imp in importance:
        direction = "↑높을수록좋음" if imp["wr_diff"] > 0 else "↓낮을수록좋음"
        print(f"  {imp['factor']:15s}: WR차이={imp['wr_diff']:+6.1f}% | 하위25%={imp['wr_q1']:5.1f}% 상위25%={imp['wr_q4']:5.1f}% | PnL차이={imp['pnl_diff']:+.3f}% {direction}")

    return importance

# 메인 실행
def main():
    print("=" * 60)
    print("🔍 업비트 24시간 캔들 데이터 수집 & 분석")
    print("=" * 60)

    # 상위 30종목
    markets = get_top30_markets()
    if not markets:
        print("ERROR: 종목 목록 가져오기 실패")
        return

    all_results = []

    for i, m in enumerate(markets):
        coin = m.split("-")[1]
        print(f"\n[{i+1}/30] {coin} 데이터 수집 중...")

        # 24시간 = 1440분봉, 288 5분봉, 96 15분봉
        c1 = get_all_candles(1, m, 1440)
        time.sleep(0.2)
        c5 = get_all_candles(5, m, 288)
        time.sleep(0.2)
        c15 = get_all_candles(15, m, 96)
        time.sleep(0.2)

        print(f"  1분봉: {len(c1)}개, 5분봉: {len(c5)}개, 15분봉: {len(c15)}개")

        if len(c1) < 100:
            print(f"  ⚠️ 데이터 부족 → 스킵")
            continue

        results = simulate_entries(c1, c5, c15, m)
        all_results.extend(results)
        print(f"  시뮬레이션: {len(results)}건 | 승률: {sum(r['win'] for r in results)/max(len(results),1)*100:.1f}%")

    print(f"\n\n{'='*60}")
    print(f"📊 전체 결과: {len(all_results)}건")
    print(f"{'='*60}")

    if not all_results:
        print("데이터 없음")
        return

    total_wr = sum(r["win"] for r in all_results) / len(all_results) * 100
    total_pnl = statistics.mean([r["pnl"] for r in all_results])
    print(f"전체 승률: {total_wr:.1f}% | 평균 PnL: {total_pnl:.3f}%")

    # 팩터 중요도 분석
    imp = factor_importance(all_results)

    # 핵심 팩터별 상세 분석
    print("\n" + "="*60)
    print("📊 핵심 팩터별 구간 분석")
    print("="*60)

    factor_bins = {
        "body_pct": [-999, 0, 0.3, 0.5, 1.0, 2.0, 999],
        "vol_surge": [-999, 0.5, 1.0, 2.0, 5.0, 10.0, 999],
        "rsi_1m": [-999, 30, 40, 50, 60, 70, 80, 999],
        "rsi_5m": [-999, 30, 40, 50, 60, 70, 80, 999],
        "rsi_15m": [-999, 30, 40, 50, 60, 70, 80, 999],
        "ema_gap": [-999, -0.5, 0, 0.3, 0.5, 1.0, 2.0, 999],
        "vwap_gap": [-999, -1, -0.3, 0, 0.3, 1.0, 2.0, 999],
        "green_streak": [0, 1, 2, 3, 4, 5, 999],
        "atr_pct": [0, 0.2, 0.3, 0.5, 0.8, 1.0, 999],
        "mom5": [-999, -0.5, 0, 0.3, 0.5, 1.0, 2.0, 999],
        "price_chg": [-999, -0.3, 0, 0.3, 0.5, 1.0, 2.0, 999],
        "uwr": [0, 0.05, 0.1, 0.2, 0.3, 0.5, 999],
    }

    for f_name, bins in factor_bins.items():
        stats = analyze_factor(all_results, f_name, bins=bins)
        if stats:
            print(f"\n  [{f_name}]")
            for label, s in stats.items():
                star = " ⭐" if s["wr"] >= total_wr + 5 else (" ❌" if s["wr"] <= total_wr - 5 else "")
                print(f"    {label:25s}: n={s['n']:5d} WR={s['wr']:5.1f}% PnL={s['avg_pnl']:+.3f}% MFE={s['avg_mfe']:.3f}% MAE={s['avg_mae']:.3f}%{star}")

    # 콤보 분석
    print("\n" + "="*60)
    print("📊 핵심 팩터 콤보 분석")
    print("="*60)

    combos = [
        ("vol_surge", 3.0, "rsi_1m", 55),
        ("vol_surge", 5.0, "ema_break", 1),
        ("body_pct", 0.3, "vol_surge", 2.0),
        ("rsi_5m", 60, "vol_vs_ma", 2.0),
        ("mom5", 0.5, "vol_surge", 3.0),
        ("ema_gap", 0.3, "vol_surge", 2.0),
        ("high_break", 1, "vol_surge", 2.0),
        ("rsi_1m", 60, "rsi_5m", 60),
        ("body_pct", 0.5, "uwr", 0.1),
    ]

    for f1, t1, f2, t2 in combos:
        combo = analyze_combo(all_results, f1, t1, f2, t2)
        if combo.get("both"):
            both = combo["both"]
            print(f"  {f1}>={t1} & {f2}>={t2}: n={both['n']:4d} WR={both['wr']:5.1f}% PnL={both['pnl']:+.3f}%")

    # Exit 파라미터 최적화
    analyze_exit_params(all_results)

    # 시간대 분석
    analyze_hourly(all_results)

    # 결과 저장
    with open("/sessions/focused-eloquent-pasteur/analysis_results.json", "w") as f:
        json.dump({
            "total": len(all_results),
            "wr": total_wr,
            "avg_pnl": total_pnl,
            "importance": imp,
            "sample_results": all_results[:100],
        }, f, ensure_ascii=False, indent=2)

    print(f"\n\n✅ 분석 완료! 결과 저장됨")
    return all_results

if __name__ == "__main__":
    results = main()
