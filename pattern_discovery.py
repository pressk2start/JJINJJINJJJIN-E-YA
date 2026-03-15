# -*- coding: utf-8 -*-
"""
패턴 발굴 분석기 v1.0
======================
목적: 이미 수집된 60일 1분봉 데이터에서 "어떤 모양일 때 5분 내 1% 오르는가" 규칙 발굴

설계:
1. 1분봉 전체 시점 대상 (사전 필터 없음)
2. 레이블: MFE(5분) >= 1%  (진입 후 5분 내 고점이 +1% 이상)
3. 체결가: 다음 봉 시가 + 0.2% (슬리피지)
4. 연속 성공 구간 태깅 (run_id, is_first)
5. 피처: 직전 3분/15분/60분 다중 시간축 요약
6. 비교: 기존 12개 시나리오 베이스라인
7. 검증: 워크포워드
8. 최종 산출물: 명확한 규칙 (rule)
"""

import json, os, sys, gzip, time, math
from datetime import datetime, timedelta, timezone
from collections import defaultdict

KST = timezone(timedelta(hours=9))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "candle_data")
OUT_DIR = os.path.join(SCRIPT_DIR, "discovery_output")

# ── 설정 ──
SLIPPAGE = 0.002       # 0.2% 슬리피지
MFE_THRESHOLD = 0.01   # 1% MFE 기준
MFE_WINDOW = 5         # 5분 (5개 1분봉)
DAYS = 60              # 분석 대상 일수

# ================================================================
# PART 1: 데이터 로드 (bot.py 로직 재사용)
# ================================================================

def _load_day_file(fpath):
    rows = []
    try:
        with gzip.open(fpath, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception:
        pass
    return rows


def load_coin_1m(coin, days=DAYS):
    """코인의 일별 파일들을 합쳐서 시간순 캔들 리스트 반환"""
    d = os.path.join(DATA_DIR, coin)
    if not os.path.exists(d):
        return []
    files = sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz"))
    if not files:
        return []
    cutoff = (datetime.now(KST) - timedelta(days=days + 1)).strftime("%Y-%m-%d")
    all_candles = []
    for fname in files:
        if fname[:10] < cutoff:
            continue
        fpath = os.path.join(d, fname)
        all_candles.extend(_load_day_file(fpath))
    all_candles.sort(key=lambda x: x["t"])
    # 중복 제거
    seen = set()
    deduped = []
    for c in all_candles:
        if c["t"] not in seen:
            seen.add(c["t"])
            deduped.append(c)
    return deduped


def get_available_coins():
    """candle_data에 있는 코인 목록"""
    if not os.path.exists(DATA_DIR):
        return []
    coins = []
    for name in os.listdir(DATA_DIR):
        d = os.path.join(DATA_DIR, name)
        if os.path.isdir(d) and not name.startswith("."):
            if any(f.endswith(".jsonl.gz") for f in os.listdir(d)):
                coins.append(name)
    return sorted(coins)


# ================================================================
# PART 2: 지표 계산 함수들 (bot.py에서 가져옴)
# ================================================================

def _rsi(cl, p=14):
    if len(cl) < p + 1:
        return 50.0
    g, l = 0.0, 0.0
    for i in range(len(cl) - p, len(cl)):
        d = cl[i] - cl[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    return 100.0 - 100.0 / (1.0 + g / l) if l else 100.0


def _bb(cl, p=20):
    if len(cl) < p:
        return 50.0, 0.0
    s = cl[-p:]
    m = sum(s) / p
    std = (sum((x - m) ** 2 for x in s) / p) ** 0.5
    if std == 0 or m == 0:
        return 50.0, 0.0
    u, lo = m + 2 * std, m - 2 * std
    return (cl[-1] - lo) / (u - lo) * 100, (u - lo) / m * 100


def _ema(cl, p):
    if not cl:
        return 0.0
    if len(cl) < p:
        return cl[-1]
    k = 2.0 / (p + 1)
    e = sum(cl[:p]) / p
    for v in cl[p:]:
        e = v * k + e * (1 - k)
    return e


def _stoch(hi, lo, cl, p=14):
    if len(cl) < p:
        return 50.0
    hh, ll = max(hi[-p:]), min(lo[-p:])
    return (cl[-1] - ll) / (hh - ll) * 100 if hh != ll else 50.0


def _atr(hi, lo, cl, p=14):
    if len(cl) < p + 1:
        return 0.0
    return sum(max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
               for i in range(len(cl) - p, len(cl))) / p


def _adx(hi, lo, cl, p=14):
    if len(cl) < p + 2:
        return 25.0
    dp, dm, tr = 0.0, 0.0, 0.0
    for i in range(len(cl) - p, len(cl)):
        u, d = hi[i] - hi[i - 1], lo[i - 1] - lo[i]
        dp += (u if u > d and u > 0 else 0)
        dm += (d if d > u and d > 0 else 0)
        tr += max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
    if tr == 0:
        return 25.0
    dip, dim = dp / tr * 100, dm / tr * 100
    return abs(dip - dim) / (dip + dim) * 100 if dip + dim > 0 else 0


def _cci(hi, lo, cl, p=20):
    if len(cl) < p:
        return 0.0
    tps = [(hi[i] + lo[i] + cl[i]) / 3 for i in range(len(cl) - p, len(cl))]
    m = sum(tps) / p
    md = sum(abs(t - m) for t in tps) / p
    return (tps[-1] - m) / (0.015 * md) if md else 0.0


def _mfi(hi, lo, cl, vol, p=14):
    if len(cl) < p + 1:
        return 50.0
    pf, nf = 0.0, 0.0
    for i in range(len(cl) - p, len(cl)):
        tp = (hi[i] + lo[i] + cl[i]) / 3
        tp0 = (hi[i - 1] + lo[i - 1] + cl[i - 1]) / 3
        mf = tp * vol[i]
        if tp > tp0:
            pf += mf
        else:
            nf += mf
    return 100.0 - 100.0 / (1.0 + pf / nf) if nf else 100.0


def _ema_align(cl):
    if len(cl) < 50:
        return 0
    e5, e10, e20, e50 = _ema(cl, 5), _ema(cl, 10), _ema(cl, 20), _ema(cl, 50)
    s = 0
    if e5 > e10:
        s += 1
    else:
        s -= 1
    if e10 > e20:
        s += 1
    else:
        s -= 1
    if e20 > e50:
        s += 1
    else:
        s -= 1
    if cl[-1] > e5:
        s += 1
    else:
        s -= 1
    return s


def _vol(cl, p=20):
    if len(cl) < p:
        return 0.0
    s = cl[-p:]
    m = sum(s) / p
    return (sum((x - m) ** 2 for x in s) / p) ** 0.5 / m * 100 if m else 0.0


def _macd_hist(cl):
    if len(cl) < 35:
        return 0.0
    ef = _ema(cl, 12)
    es = _ema(cl, 26)
    macd = ef - es
    price = cl[-1] if cl[-1] > 0 else 1
    return round(macd / price * 100, 4)


# ================================================================
# PART 3: 레이블 생성 — MFE(5분) >= 1%
# ================================================================

def compute_labels(candles):
    """
    각 시점 t에 대해:
    - entry_price = candles[t+1].open * 1.002  (다음 봉 시가 + 0.2% 슬리피지)
    - MFE_5m = max(candles[t+1..t+5].high) / entry_price - 1
    - success = MFE_5m >= 0.01

    추가 저장: mfe_5m, mae_5m, ret_close_5m (원시 성과값)
    """
    n = len(candles)
    labels = []

    for t in range(n):
        # t+1 봉이 있어야 진입 가능, t+6까지 있어야 5분 관찰 가능
        if t + 1 + MFE_WINDOW >= n:
            labels.append(None)
            continue

        entry_price = candles[t + 1]["o"] * (1 + SLIPPAGE)
        if entry_price <= 0:
            labels.append(None)
            continue

        # t+1봉 시가에 진입 → t+1 ~ t+5 (5개 봉)의 고가/저가로 MFE/MAE 계산
        # 5분 후 종가 = t+5봉의 종가
        future_highs = [candles[t + 1 + j]["h"] for j in range(MFE_WINDOW)]
        future_lows = [candles[t + 1 + j]["l"] for j in range(MFE_WINDOW)]
        close_5m = candles[t + MFE_WINDOW]["c"]  # = candles[t+5]["c"]

        mfe = max(future_highs) / entry_price - 1
        mae = min(future_lows) / entry_price - 1  # 음수
        ret_close = close_5m / entry_price - 1

        labels.append({
            "entry_price": round(entry_price, 2),
            "mfe_5m": round(mfe * 100, 4),
            "mae_5m": round(mae * 100, 4),
            "ret_close_5m": round(ret_close * 100, 4),
            "success": 1 if mfe >= MFE_THRESHOLD else 0,
        })

    return labels


# ================================================================
# PART 4: 연속 성공 구간 태깅
# ================================================================

def tag_runs(labels):
    """연속 성공 구간에 run_id, is_first 태깅"""
    run_id = 0
    in_run = False
    current_pos = 0

    for i, lb in enumerate(labels):
        if lb is None:
            in_run = False
            continue
        if lb["success"] == 1:
            if not in_run:
                run_id += 1
                in_run = True
                current_pos = 0
                lb["run_id"] = run_id
                lb["is_first"] = 1
                lb["run_pos"] = 0
            else:
                current_pos += 1
                lb["run_id"] = run_id
                lb["is_first"] = 0
                lb["run_pos"] = current_pos
        else:
            in_run = False
            lb["run_id"] = 0
            lb["is_first"] = 0
            lb["run_pos"] = 0


# ================================================================
# PART 5: 피처 생성 — 직전 3분/15분/60분
# ================================================================

def extract_features(candles, t):
    """
    시점 t의 직전 데이터로 피처 생성
    - 3분: 초단기 미세구조
    - 15분: 단기 맥락
    - 60분: 상위 맥락
    """
    if t < 60:
        return None

    feat = {}
    price = candles[t]["c"]
    if price <= 0:
        return None

    # 공통 배열 추출
    cl_60 = [candles[t - 60 + j]["c"] for j in range(61)]  # t-60 ~ t
    hi_60 = [candles[t - 60 + j]["h"] for j in range(61)]
    lo_60 = [candles[t - 60 + j]["l"] for j in range(61)]
    vo_60 = [candles[t - 60 + j]["v"] for j in range(61)]
    op_60 = [candles[t - 60 + j]["o"] for j in range(61)]

    # ── 3분 피처 (직전 3개 봉) ──
    cl3 = cl_60[-4:]  # 마지막 4개 (변화율 계산용)
    hi3 = hi_60[-3:]
    lo3 = lo_60[-3:]
    vo3 = vo_60[-3:]

    # 3분 수익률
    feat["m3_ret"] = round((cl3[-1] - cl3[-4]) / cl3[-4] * 100, 4) if cl3[-4] > 0 else 0
    # 양봉 비율
    green3 = sum(1 for i in range(-3, 0) if cl_60[i] > op_60[i])
    feat["m3_green_ratio"] = round(green3 / 3, 2)
    # 고점 상승 여부 (최근 3분 고점이 순차 상승)
    feat["m3_higher_high"] = 1 if hi3[-1] > hi3[-2] > hi3[-3] else 0
    # 저점 상승 여부
    feat["m3_higher_low"] = 1 if lo3[-1] > lo3[-2] > lo3[-3] else 0
    # 거래량 합 vs 이전 3분 거래량 합
    v3_now = sum(vo3)
    v3_prev = sum(vo_60[-6:-3]) if len(vo_60) >= 6 else v3_now
    feat["m3_vol_ratio"] = round(v3_now / v3_prev, 2) if v3_prev > 0 else 1.0
    # 현재 봉 크기 (body)
    body = abs(candles[t]["c"] - candles[t]["o"])
    rng = candles[t]["h"] - candles[t]["l"]
    feat["m3_body_pct"] = round(body / price * 100, 4) if price > 0 else 0
    # 아래꼬리 비율
    feat["m3_lw_pct"] = round((min(candles[t]["o"], candles[t]["c"]) - candles[t]["l"]) / rng * 100, 1) if rng > 0 else 0
    # 윗꼬리 비율
    feat["m3_uw_pct"] = round((candles[t]["h"] - max(candles[t]["o"], candles[t]["c"])) / rng * 100, 1) if rng > 0 else 0

    # ── 15분 피처 ──
    cl15 = cl_60[-16:]  # 마지막 16개
    hi15 = hi_60[-15:]
    lo15 = lo_60[-15:]
    vo15 = vo_60[-15:]
    op15 = op_60[-15:]

    # 수익률
    feat["m15_ret"] = round((cl15[-1] - cl15[-16]) / cl15[-16] * 100, 4) if cl15[-16] > 0 else 0
    # 변동성
    feat["m15_volatility"] = round(_vol(cl15, min(15, len(cl15))), 3)
    # RSI 14
    feat["m15_rsi14"] = round(_rsi(cl15, 14), 1)
    # BB 위치
    bb_pos, bb_width = _bb(cl15, min(15, len(cl15)))
    feat["m15_bb_pos"] = round(bb_pos, 1)
    feat["m15_bb_width"] = round(bb_width, 3)
    # 거래량 z-score (현재 vs 15분 평균)
    v15_avg = sum(vo15) / 15
    v15_std = (sum((v - v15_avg) ** 2 for v in vo15) / 15) ** 0.5
    feat["m15_vol_zscore"] = round((vo15[-1] - v15_avg) / v15_std, 2) if v15_std > 0 else 0
    # 양봉 비율
    green15 = sum(1 for i in range(15) if cl_60[-15 + i] > op_60[-15 + i])
    feat["m15_green_ratio"] = round(green15 / 15, 2)
    # 고점 대비 현재 위치
    hh15 = max(hi15)
    ll15 = min(lo15)
    feat["m15_pos"] = round((price - ll15) / (hh15 - ll15) * 100, 1) if hh15 != ll15 else 50.0
    # 최근 저점 대비 회복률
    recent_low = min(lo_60[-5:])
    feat["m15_recovery"] = round((price - recent_low) / recent_low * 100, 3) if recent_low > 0 else 0

    # ── 60분 피처 ──
    # 수익률
    feat["m60_ret"] = round((cl_60[-1] - cl_60[0]) / cl_60[0] * 100, 4) if cl_60[0] > 0 else 0
    # 변동성
    feat["m60_volatility"] = round(_vol(cl_60, 20), 3)
    # RSI
    feat["m60_rsi14"] = round(_rsi(cl_60, 14), 1)
    # EMA 정배열
    feat["m60_ema_align"] = _ema_align(cl_60)
    # MACD histogram
    feat["m60_macd_h"] = _macd_hist(cl_60)
    # BB 위치
    bb60_pos, bb60_width = _bb(cl_60, 20)
    feat["m60_bb_pos"] = round(bb60_pos, 1)
    feat["m60_bb_width"] = round(bb60_width, 3)
    # ADX
    feat["m60_adx"] = round(_adx(hi_60, lo_60, cl_60), 1)
    # CCI
    feat["m60_cci"] = round(_cci(hi_60, lo_60, cl_60), 1)
    # MFI
    feat["m60_mfi"] = round(_mfi(hi_60, lo_60, cl_60, vo_60), 1)
    # 거래량 분위기 (최근 15분 vs 이전 45분)
    v_recent = sum(vo_60[-15:]) / 15
    v_old_sum = sum(vo_60[:45])
    v_old = v_old_sum / 45 if v_old_sum > 0 else 1
    feat["m60_vol_trend"] = round(v_recent / v_old, 2) if v_old > 0 else 1.0
    # 고점/저점 위치
    hh60 = max(hi_60)
    ll60 = min(lo_60)
    feat["m60_pos"] = round((price - ll60) / (hh60 - ll60) * 100, 1) if hh60 != ll60 else 50.0
    # EMA 괴리율
    ema20 = _ema(cl_60, 20)
    feat["m60_ema20_gap"] = round((price - ema20) / ema20 * 100, 3) if ema20 > 0 else 0

    # 모멘텀 피처 (여러 기간)
    for p in [5, 10, 20]:
        if len(cl_60) > p:
            feat[f"mom_{p}"] = round((cl_60[-1] - cl_60[-(p + 1)]) / cl_60[-(p + 1)] * 100, 4) if cl_60[-(p + 1)] > 0 else 0

    return feat


# ================================================================
# PART 6: 코인별 분석 실행
# ================================================================

def analyze_coin(coin):
    """단일 코인 분석: 데이터 로드 → 레이블 → 피처 → 결과 반환"""
    candles = load_coin_1m(coin, DAYS)
    if len(candles) < 200:
        print(f"  {coin}: 데이터 부족 ({len(candles)}개), 스킵")
        return None

    print(f"  {coin}: {len(candles)}개 1분봉 로드")

    # 레이블 생성
    labels = compute_labels(candles)
    tag_runs(labels)

    # 피처 생성 + 결합
    samples = []
    for t in range(len(candles)):
        lb = labels[t]
        if lb is None:
            continue

        feat = extract_features(candles, t)
        if feat is None:
            continue

        sample = {
            "coin": coin,
            "time": candles[t]["t"],
            **feat,
            **lb,
        }
        samples.append(sample)

    success_count = sum(1 for s in samples if s["success"] == 1)
    total = len(samples)
    rate = success_count / total * 100 if total > 0 else 0
    print(f"  {coin}: {total}개 샘플, 성공 {success_count}개 ({rate:.1f}%)")

    return samples


# ================================================================
# PART 7: 규칙 발굴 — 피처별 성공률 비교
# ================================================================

def discover_rules(all_samples):
    """
    성공 vs 실패 그룹의 피처 평균 차이 비교.
    차이가 큰 피처를 찾아 규칙 후보 도출.
    """
    if not all_samples:
        return {}

    # 피처 키 추출
    feat_keys = [k for k in all_samples[0].keys()
                 if k not in ("coin", "time", "entry_price", "mfe_5m", "mae_5m",
                              "ret_close_5m", "success", "run_id", "is_first", "run_pos")]

    success = [s for s in all_samples if s["success"] == 1]
    failure = [s for s in all_samples if s["success"] == 0]

    if not success or not failure:
        return {}

    results = {}
    for key in feat_keys:
        s_vals = [s[key] for s in success if key in s and s[key] is not None]
        f_vals = [s[key] for s in failure if key in s and s[key] is not None]

        if not s_vals or not f_vals:
            continue

        s_mean = sum(s_vals) / len(s_vals)
        f_mean = sum(f_vals) / len(f_vals)
        diff = s_mean - f_mean

        # 표준편차 (풀링)
        all_vals = s_vals + f_vals
        all_mean = sum(all_vals) / len(all_vals)
        std = (sum((v - all_mean) ** 2 for v in all_vals) / len(all_vals)) ** 0.5

        # effect size (Cohen's d 비슷)
        effect = diff / std if std > 0 else 0

        results[key] = {
            "success_mean": round(s_mean, 4),
            "failure_mean": round(f_mean, 4),
            "diff": round(diff, 4),
            "effect_size": round(effect, 4),
            "n_success": len(s_vals),
            "n_failure": len(f_vals),
        }

    # effect size 절대값으로 정렬
    results = dict(sorted(results.items(), key=lambda x: abs(x[1]["effect_size"]), reverse=True))
    return results


def find_best_rules(all_samples, top_features, n_rules=10):
    """
    상위 피처들의 임계값 기반 규칙 탐색.
    각 피처에 대해 최적 임계값과 그때의 성공률을 찾음.
    """
    rules = []

    for feat_name, feat_info in list(top_features.items())[:20]:
        vals_with_label = [(s[feat_name], s["success"])
                          for s in all_samples
                          if feat_name in s and s[feat_name] is not None]
        if len(vals_with_label) < 100:
            continue

        # 정렬 후 분위수별 성공률 (정렬된 배열 슬라이싱으로 O(n) 처리)
        vals_with_label.sort(key=lambda x: x[0])
        n = len(vals_with_label)
        total_success = sum(v[1] for v in vals_with_label)

        # 누적합 미리 계산 (위/아래 성공 수를 O(1)로 구하기 위해)
        cum_success = [0] * (n + 1)
        for i in range(n):
            cum_success[i + 1] = cum_success[i] + vals_with_label[i][1]

        best_rule = None
        base_rate = total_success / n

        for pct in [10, 20, 25, 30, 70, 75, 80, 90]:
            idx = int(n * pct / 100)
            threshold = vals_with_label[idx][0]

            # 상위 (>=threshold): idx부터 끝까지
            n_above = n - idx
            if n_above >= 50:
                success_above = total_success - cum_success[idx]
                rate_above = success_above / n_above
                lift = rate_above / base_rate if base_rate > 0 else 0
                if lift > 1.3:
                    if best_rule is None or lift > best_rule["lift"]:
                        best_rule = {
                            "feature": feat_name,
                            "direction": ">=",
                            "threshold": round(threshold, 4),
                            "success_rate": round(rate_above * 100, 2),
                            "base_rate": round(base_rate * 100, 2),
                            "lift": round(lift, 2),
                            "n_samples": n_above,
                        }

            # 하위 (<=threshold): 처음부터 idx까지
            n_below = idx + 1
            if n_below >= 50:
                success_below = cum_success[idx + 1]
                rate_below = success_below / n_below
                lift = rate_below / base_rate if base_rate > 0 else 0
                if lift > 1.3:
                    if best_rule is None or lift > best_rule["lift"]:
                        best_rule = {
                            "feature": feat_name,
                            "direction": "<=",
                            "threshold": round(threshold, 4),
                            "success_rate": round(rate_below * 100, 2),
                            "base_rate": round(base_rate * 100, 2),
                            "lift": round(lift, 2),
                            "n_samples": n_below,
                        }

        if best_rule:
            rules.append(best_rule)

    rules.sort(key=lambda x: x["lift"], reverse=True)
    return rules[:n_rules]


# ================================================================
# PART 8: 워크포워드 검증
# ================================================================

def walk_forward_validate(all_samples, rules, train_ratio=0.67):
    """
    앞 67% 학습, 뒤 33% 검증.
    규칙이 학습 구간에서만 좋은지, 검증 구간에서도 유효한지 확인.
    """
    if not all_samples or not rules:
        return []

    # 시간순 정렬
    all_samples.sort(key=lambda x: x["time"])
    split = int(len(all_samples) * train_ratio)
    train = all_samples[:split]
    test = all_samples[split:]

    results = []
    base_rate_train = sum(s["success"] for s in train) / len(train) if train else 0
    base_rate_test = sum(s["success"] for s in test) / len(test) if test else 0

    for rule in rules:
        feat = rule["feature"]
        direction = rule["direction"]
        threshold = rule["threshold"]

        # 학습 구간 성능
        if direction == ">=":
            train_hit = [s for s in train if feat in s and s[feat] >= threshold]
            test_hit = [s for s in test if feat in s and s[feat] >= threshold]
        else:
            train_hit = [s for s in train if feat in s and s[feat] <= threshold]
            test_hit = [s for s in test if feat in s and s[feat] <= threshold]

        train_rate = sum(s["success"] for s in train_hit) / len(train_hit) if train_hit else 0
        test_rate = sum(s["success"] for s in test_hit) / len(test_hit) if test_hit else 0

        results.append({
            **rule,
            "train_rate": round(train_rate * 100, 2),
            "test_rate": round(test_rate * 100, 2),
            "train_n": len(train_hit),
            "test_n": len(test_hit),
            "base_train": round(base_rate_train * 100, 2),
            "base_test": round(base_rate_test * 100, 2),
            "test_lift": round(test_rate / base_rate_test, 2) if base_rate_test > 0 else 0,
            "survived": test_rate > base_rate_test * 1.2,  # 검증에서도 20% 이상 리프트면 생존
        })

    return results


# ================================================================
# PART 9: 리포트 생성
# ================================================================

def generate_report(all_samples, feature_analysis, rules, wf_results):
    """분석 결과 리포트 생성"""
    os.makedirs(OUT_DIR, exist_ok=True)

    report = []
    report.append("=" * 70)
    report.append("패턴 발굴 분석 리포트")
    report.append(f"생성 시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    report.append("=" * 70)

    # 기본 통계
    total = len(all_samples)
    success = sum(s["success"] for s in all_samples)
    rate = success / total * 100 if total > 0 else 0

    report.append(f"\n총 샘플: {total:,}")
    report.append(f"성공 샘플: {success:,} ({rate:.2f}%)")
    report.append(f"실패 샘플: {total - success:,} ({100 - rate:.2f}%)")

    # 코인별 통계
    coin_stats = defaultdict(lambda: {"total": 0, "success": 0})
    for s in all_samples:
        coin_stats[s["coin"]]["total"] += 1
        coin_stats[s["coin"]]["success"] += s["success"]

    report.append(f"\n── 코인별 통계 ──")
    report.append(f"{'코인':<10} {'총샘플':>8} {'성공':>6} {'성공률':>8}")
    report.append("-" * 35)
    for coin in sorted(coin_stats.keys()):
        st = coin_stats[coin]
        r = st["success"] / st["total"] * 100 if st["total"] > 0 else 0
        report.append(f"{coin:<10} {st['total']:>8,} {st['success']:>6,} {r:>7.2f}%")

    # 연속 성공 구간 통계
    run_lengths = defaultdict(int)  # (coin, run_id) → length
    for s in all_samples:
        rid = s.get("run_id", 0)
        if rid > 0:
            key = (s["coin"], rid)
            run_lengths[key] += 1
    runs = defaultdict(int)
    for length in run_lengths.values():
        runs[length] += 1

    report.append(f"\n── 연속 성공 구간 분포 ──")
    for length in sorted(runs.keys()):
        report.append(f"  {length}분 연속: {runs[length]}회")

    # 피처 분석 상위 15개
    report.append(f"\n── 피처별 성공/실패 차이 (상위 15개) ──")
    report.append(f"{'피처':<25} {'성공평균':>10} {'실패평균':>10} {'차이':>8} {'효과크기':>8}")
    report.append("-" * 65)
    for i, (key, info) in enumerate(feature_analysis.items()):
        if i >= 15:
            break
        report.append(f"{key:<25} {info['success_mean']:>10.4f} {info['failure_mean']:>10.4f} "
                      f"{info['diff']:>8.4f} {info['effect_size']:>8.4f}")

    # 발굴 규칙
    report.append(f"\n── 발굴된 규칙 후보 ──")
    for i, rule in enumerate(rules):
        report.append(f"\n규칙 {i + 1}: {rule['feature']} {rule['direction']} {rule['threshold']}")
        report.append(f"  성공률: {rule['success_rate']}% (기본: {rule['base_rate']}%)")
        report.append(f"  리프트: {rule['lift']}x  |  샘플 수: {rule['n_samples']}")

    # 워크포워드 결과
    report.append(f"\n── 워크포워드 검증 결과 ──")
    report.append(f"{'규칙':<30} {'학습성공률':>10} {'검증성공률':>10} {'검증리프트':>10} {'생존':>6}")
    report.append("-" * 70)
    for wf in wf_results:
        survived = "O" if wf["survived"] else "X"
        name = f"{wf['feature']} {wf['direction']} {wf['threshold']}"
        report.append(f"{name:<30} {wf['train_rate']:>9.2f}% {wf['test_rate']:>9.2f}% "
                      f"{wf['test_lift']:>9.2f}x {survived:>6}")

    # 생존 규칙만 요약
    survived_rules = [wf for wf in wf_results if wf["survived"]]
    report.append(f"\n── 워크포워드 생존 규칙: {len(survived_rules)}개 ──")
    for wf in survived_rules:
        report.append(f"  {wf['feature']} {wf['direction']} {wf['threshold']}  "
                      f"(검증 성공률: {wf['test_rate']}%, 리프트: {wf['test_lift']}x)")

    report_text = "\n".join(report)

    # 파일 저장
    report_path = os.path.join(OUT_DIR, "discovery_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    # 원시 데이터도 저장
    data_path = os.path.join(OUT_DIR, "discovery_data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump({
            "feature_analysis": feature_analysis,
            "rules": rules,
            "walk_forward": wf_results,
            "stats": {
                "total_samples": total,
                "success_count": success,
                "success_rate": round(rate, 2),
            }
        }, f, ensure_ascii=False, indent=2)

    return report_text, report_path


# ================================================================
# MAIN
# ================================================================

def main():
    print("=" * 60)
    print("패턴 발굴 분석기 v1.0")
    print("=" * 60)

    coins = get_available_coins()
    if not coins:
        print("candle_data 디렉토리에 데이터가 없습니다.")
        print("먼저 collect_1m.py 또는 bot.py로 데이터를 수집하세요.")
        return

    print(f"\n수집된 코인: {len(coins)}개")
    print(f"분석 대상: {DAYS}일")
    print(f"레이블: MFE({MFE_WINDOW}분) >= {MFE_THRESHOLD * 100}%")
    print(f"슬리피지: {SLIPPAGE * 100}%")
    print()

    # 코인별 분석
    all_samples = []
    for i, coin in enumerate(coins):
        print(f"[{i + 1}/{len(coins)}] {coin}")
        samples = analyze_coin(coin)
        if samples:
            all_samples.extend(samples)

    if not all_samples:
        print("\n분석 가능한 샘플이 없습니다.")
        return

    total = len(all_samples)
    success = sum(s["success"] for s in all_samples)
    print(f"\n{'=' * 60}")
    print(f"전체 샘플: {total:,}")
    print(f"성공: {success:,} ({success / total * 100:.2f}%)")
    print(f"{'=' * 60}")

    # 피처 분석
    print("\n피처 분석 중...")
    feature_analysis = discover_rules(all_samples)

    # 규칙 발굴
    print("규칙 발굴 중...")
    rules = find_best_rules(all_samples, feature_analysis)

    # 워크포워드 검증
    print("워크포워드 검증 중...")
    wf_results = walk_forward_validate(all_samples, rules)

    # 리포트 생성
    print("리포트 생성 중...")
    report_text, report_path = generate_report(all_samples, feature_analysis, rules, wf_results)

    print(f"\n{report_text}")
    print(f"\n리포트 저장: {report_path}")
    print("완료!")


if __name__ == "__main__":
    main()
