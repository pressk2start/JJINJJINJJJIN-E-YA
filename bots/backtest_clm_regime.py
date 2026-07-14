#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_clm_regime.py
======================
CLM 전략 분석 엔진 (Train/Val 분리, Top-N 자동 탐색, Feature Importance)

핵심 원칙:
- 하드코딩 가설 제로 (전부 탐색)
- Session 단위 Train/Val 분리 (regime leakage 방지)
- Regime은 절대 threshold (분위수 금지)
- Stability Score 필수
- Feature Importance 3방법 앙상블 (variance/MI/permutation)
- MIN_SAMPLE=10 (탐색부터 적용)

사용:
    python3 backtest_clm_regime.py bots/momentum_clm_*.csv
    python3 backtest_clm_regime.py bots/momentum_clm_*.csv --min-sample 15
"""

import argparse
import csv
import glob
import os
import random
import sys
from collections import defaultdict
from itertools import product
from math import log, sqrt
from statistics import mean, pstdev

# ═══════════════════════════════════════════════
# 상수
# ═══════════════════════════════════════════════
MIN_SAMPLE = 10
TRAIN_RATIO = 0.70
STABILITY_MIN_SESSIONS = 3
STABILITY_THRESHOLD = 0.7
RANDOM_SEED = 42

EMA_BINS = [(0.4, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 1.2), (1.2, 1.5), (1.5, 2.0), (2.0, 3.0)]
RSI_BINS = [(60, 65), (65, 68), (68, 70), (70, 72), (72, 75), (75, 80), (80, 90)]
BREADTH_BINS = [(0, 30), (30, 50), (50, 100)]
BTC_RET_BINS = [(-999, -1), (-1, 0), (0, 1), (1, 999)]
TURNOVER_BINS = [(0, 0.5e12), (0.5e12, 1e12), (1e12, 1e18)]

EMA_THRESHOLDS = [0.6, 0.8, 1.0, 1.2, 1.4, 1.5, 1.8]
RSI_THRESHOLDS = [65, 68, 70, 71, 72, 73, 75]
BREADTH_THRESHOLDS = [25, 30, 33, 40, 50]
BTC_RET_THRESHOLDS = [-1.0, -0.5, 0.0, 0.5, 1.0]

# ═══════════════════════════════════════════════
# CSV Loader
# ═══════════════════════════════════════════════
def _to_float(v, default=None):
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default

def load_csv_files(patterns):
    files = []
    for pat in patterns:
        files.extend(sorted(glob.glob(pat)))
    files = sorted(set(files))
    if not files:
        print(f"⚠ 매칭된 CSV 없음: {patterns}", file=sys.stderr)
        sys.exit(1)
    rows = []
    for path in files:
        session_id = os.path.basename(path).replace(".csv", "")
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                r = _parse_row(raw, session_id, mtime)
                if r is not None:
                    rows.append(r)
    return rows, files

def _parse_row(raw, session_id, mtime):
    net_pnl = _to_float(raw.get("net_pnl")) or _to_float(raw.get("pnl"))
    if net_pnl is None:
        return None
    return {
        "session_id": session_id,
        "session_ts": mtime,
        "time": (raw.get("time") or "").strip(),
        "market": (raw.get("market") or "").strip().upper(),
        "net_pnl": net_pnl,
        "mae": _to_float(raw.get("mae"), 0),
        "peak_pnl": _to_float(raw.get("peak_pnl"), 0),
        "hold_sec": _to_float(raw.get("hold_sec"), 0),
        "reason": (raw.get("reason") or "").strip().lower(),
        "rsi_5m": _to_float(raw.get("rsi_5m"), 0),
        "rsi_15m": _to_float(raw.get("rsi_15m"), 0),
        "ema_spread_pct": _to_float(raw.get("ema_spread_pct"), 0),
        "spread_pct": _to_float(raw.get("spread_pct"), 0),
        "ask_krw": _to_float(raw.get("ask_krw"), 0),
        "bid_krw": _to_float(raw.get("bid_krw"), 0),
        "rg_btc_ret": _to_float(raw.get("rg_btc_ret"), 0),
        "rg_turnover": _to_float(raw.get("rg_turnover"), 0),
        "rg_breadth": _to_float(raw.get("rg_breadth"), 0),
        "rg_btc_dom": _to_float(raw.get("rg_btc_dom"), 0),
        "rg_top5": _to_float(raw.get("rg_top5"), 0),
        "has_regime": "rg_btc_ret" in raw and raw.get("rg_btc_ret") not in (None, ""),
    }

def split_train_val(rows):
    """세션 단위 시간순 70/30 분할."""
    sessions = sorted({(r["session_ts"], r["session_id"]) for r in rows})
    n_train = max(1, int(len(sessions) * TRAIN_RATIO))
    train_ids = {s[1] for s in sessions[:n_train]}
    val_ids = {s[1] for s in sessions[n_train:]}
    train = [r for r in rows if r["session_id"] in train_ids]
    val = [r for r in rows if r["session_id"] in val_ids]
    return train, val, train_ids, val_ids

# ═══════════════════════════════════════════════
# 통계
# ═══════════════════════════════════════════════
def stats(rows):
    n = len(rows)
    if n == 0:
        return {"n": 0, "wr": 0, "pf": 0, "ev": 0, "mfe_avg": 0, "mfe_l": 0, "sessions": 0}
    pnls = [r["net_pnl"] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    pf = gp / gl if gl > 1e-9 else float("inf") if gp > 0 else 0
    mfe_all = [r["peak_pnl"] for r in rows]
    mfe_l_rows = [r["peak_pnl"] for r in rows if r["net_pnl"] <= 0]
    return {
        "n": n,
        "wr": len(wins) / n * 100,
        "pf": pf,
        "ev": mean(pnls),
        "mfe_avg": mean(mfe_all) if mfe_all else 0,
        "mfe_l": mean(mfe_l_rows) if mfe_l_rows else 0,
        "sessions": len({r["session_id"] for r in rows}),
    }

def stability_score(rows):
    """
    Stability Score — regime 분산 측정 전용.

    ⚠ 주의: 이건 '전략 성과'가 아니라 '세션 간 성과 편차' 지표.
    - 세션별 EV 평균의 CV (coefficient of variation)
    - 1 / (1 + CV) 로 정규화, 범위 [0, 1]
    - 1에 가까울수록 세션마다 성과가 균일
    - 낮을수록 regime 의존도가 큼

    ⚠ 전략 통합 PF는 stats() 에서 gross_profit/gross_loss로 별도 계산.
    이 함수의 값을 전략 PF 대체로 쓰면 안 됨.
    """
    by_sess = defaultdict(list)
    for r in rows:
        by_sess[r["session_id"]].append(r["net_pnl"])
    session_evs = [mean(v) for v in by_sess.values() if len(v) >= 3]
    if len(session_evs) < 2:
        return 0.0
    m = mean(session_evs)
    if abs(m) < 1e-9:
        return 0.0
    sd = pstdev(session_evs)
    cv = abs(sd / m)
    return 1.0 / (1.0 + cv)

# ═══════════════════════════════════════════════
# Grid 분석
# ═══════════════════════════════════════════════
def grid_analysis(rows, key, bins):
    """단일 feature grid."""
    out = []
    for lo, hi in bins:
        sub = [r for r in rows if lo <= r[key] < hi]
        s = stats(sub)
        s["bin"] = f"{lo}~{hi}"
        out.append(s)
    return out

def heatmap_ema_rsi(rows):
    """EMA × RSI15 2D heatmap (n/PF/EV 셀)."""
    grid = []
    for rsi_lo, rsi_hi in RSI_BINS:
        row = []
        for ema_lo, ema_hi in EMA_BINS:
            sub = [r for r in rows if ema_lo <= r["ema_spread_pct"] < ema_hi
                   and rsi_lo <= r["rsi_15m"] < rsi_hi]
            s = stats(sub)
            row.append({
                "ema": f"{ema_lo}~{ema_hi}",
                "rsi": f"{rsi_lo}~{rsi_hi}",
                "n": s["n"], "pf": s["pf"], "ev": s["ev"],
            })
        grid.append(row)
    return grid

# ═══════════════════════════════════════════════
# Regime Threshold 분석 (절대 기준)
# ═══════════════════════════════════════════════
def regime_split(rows):
    out = {"btc_ret": [], "breadth": [], "turnover": []}
    for lo, hi in BTC_RET_BINS:
        sub = [r for r in rows if lo <= r["rg_btc_ret"] < hi]
        s = stats(sub)
        s["bin"] = f"{lo}~{hi}%"
        out["btc_ret"].append(s)
    for lo, hi in BREADTH_BINS:
        sub = [r for r in rows if lo <= r["rg_breadth"] * 100 < hi]
        s = stats(sub)
        s["bin"] = f"{lo}~{hi}%"
        out["breadth"].append(s)
    for lo, hi in TURNOVER_BINS:
        sub = [r for r in rows if lo <= r["rg_turnover"] < hi]
        s = stats(sub)
        s["bin"] = f"{lo/1e12:.1f}~{hi/1e12:.1f}조"
        out["turnover"].append(s)
    return out

# ═══════════════════════════════════════════════
# Top-N 자동 탐색 (Primary hypothesis 발견)
# ═══════════════════════════════════════════════
def _cond_desc(ema_th, rsi_th, breadth_th, btc_th):
    parts = []
    if ema_th is not None:
        parts.append(f"EMA≥{ema_th}")
    if rsi_th is not None:
        parts.append(f"RSI15≥{rsi_th}")
    if breadth_th is not None:
        parts.append(f"breadth≤{breadth_th}")
    if btc_th is not None:
        parts.append(f"BTC24h≤{btc_th}")
    return " ∧ ".join(parts) if parts else "ALL"

def _cond_match(r, ema_th, rsi_th, breadth_th, btc_th):
    if ema_th is not None and r["ema_spread_pct"] < ema_th:
        return False
    if rsi_th is not None and r["rsi_15m"] < rsi_th:
        return False
    if breadth_th is not None and r["rg_breadth"] * 100 > breadth_th:
        return False
    if btc_th is not None and r["rg_btc_ret"] > btc_th:
        return False
    return True

def enumerate_conditions():
    """단일 + 조합 임계값 탐색 공간."""
    conds = []
    # 단일 (EMA, RSI, breadth, BTC)
    for e in EMA_THRESHOLDS:
        conds.append((e, None, None, None))
    for r in RSI_THRESHOLDS:
        conds.append((None, r, None, None))
    for b in BREADTH_THRESHOLDS:
        conds.append((None, None, b, None))
    for bt in BTC_RET_THRESHOLDS:
        conds.append((None, None, None, bt))
    # 2조합
    for e, r in product(EMA_THRESHOLDS, RSI_THRESHOLDS):
        conds.append((e, r, None, None))
    for e, b in product(EMA_THRESHOLDS, BREADTH_THRESHOLDS):
        conds.append((e, None, b, None))
    for r, b in product(RSI_THRESHOLDS, BREADTH_THRESHOLDS):
        conds.append((None, r, b, None))
    for e, bt in product(EMA_THRESHOLDS, BTC_RET_THRESHOLDS):
        conds.append((e, None, None, bt))
    # 3조합 (EMA × RSI × breadth 만 — 폭발 방지)
    for e, r, b in product(EMA_THRESHOLDS, RSI_THRESHOLDS, BREADTH_THRESHOLDS):
        conds.append((e, r, b, None))
    return conds

def search_top_conditions(train_rows, val_rows, min_sample=MIN_SAMPLE):
    """Train에서 조건 탐색, Val에서 재평가, Stability까지 산출."""
    results = []
    for ema_th, rsi_th, breadth_th, btc_th in enumerate_conditions():
        tr_sub = [r for r in train_rows if _cond_match(r, ema_th, rsi_th, breadth_th, btc_th)]
        if len(tr_sub) < min_sample:
            continue
        tr_s = stats(tr_sub)
        val_sub = [r for r in val_rows if _cond_match(r, ema_th, rsi_th, breadth_th, btc_th)]
        val_s = stats(val_sub)
        stab = stability_score(tr_sub + val_sub)
        if val_s["n"] == 0:
            d_pf = float("-inf")
        elif val_s["pf"] == float("inf") and tr_s["pf"] == float("inf"):
            d_pf = 0.0
        elif val_s["pf"] == float("inf"):
            d_pf = float("inf")
        elif tr_s["pf"] == float("inf"):
            d_pf = float("-inf")
        else:
            d_pf = val_s["pf"] - tr_s["pf"]
        results.append({
            "condition": _cond_desc(ema_th, rsi_th, breadth_th, btc_th),
            "ema_th": ema_th, "rsi_th": rsi_th, "breadth_th": breadth_th, "btc_th": btc_th,
            "tr_n": tr_s["n"], "tr_pf": tr_s["pf"], "tr_ev": tr_s["ev"], "tr_mfe_l": tr_s["mfe_l"],
            "val_n": val_s["n"], "val_pf": val_s["pf"], "val_ev": val_s["ev"], "val_mfe_l": val_s["mfe_l"],
            "d_pf": d_pf,
            "sessions": tr_s["sessions"] + val_s["sessions"],
            "stability": stab,
            "tag": _classify_tag(tr_s, val_s, stab),
        })
    results.sort(key=lambda x: (x["tr_ev"], x["tr_pf"]), reverse=True)
    return results

def _classify_tag(tr_s, val_s, stab):
    """등급: STRONG / SURVIVED / WATCH / FAILED."""
    if val_s["n"] == 0:
        return "NO_VAL"
    is_survived = val_s["pf"] >= 1.0 and val_s["ev"] > 0
    is_strong = tr_s["pf"] > 0 and val_s["pf"] >= 0.9 * tr_s["pf"] and is_survived
    if is_strong:
        return "STRONG"
    if is_survived:
        return "SURVIVED"
    if val_s["pf"] >= 0.8 or abs(val_s["ev"]) < 0.03:
        return "WATCH"
    return "FAILED"

# ═══════════════════════════════════════════════
# MFE L / 즉사율 분석
# ═══════════════════════════════════════════════
def instant_death_analysis(rows):
    """MFE L=0 (진입 후 상승 전무) 비율 — 즉사 조합 탐색."""
    total = len(rows)
    if total == 0:
        return {"overall": 0, "combos": []}
    instant = sum(1 for r in rows if r["peak_pnl"] <= 0.001)
    combos = []
    for rsi_lo, rsi_hi in RSI_BINS:
        for ema_lo, ema_hi in EMA_BINS:
            sub = [r for r in rows if rsi_lo <= r["rsi_15m"] < rsi_hi
                   and ema_lo <= r["ema_spread_pct"] < ema_hi]
            if len(sub) < 5:
                continue
            death = sum(1 for r in sub if r["peak_pnl"] <= 0.001)
            combos.append({
                "rsi": f"{rsi_lo}~{rsi_hi}",
                "ema": f"{ema_lo}~{ema_hi}",
                "n": len(sub),
                "death_rate": death / len(sub) * 100,
            })
    combos.sort(key=lambda x: x["death_rate"], reverse=True)
    return {
        "overall": instant / total * 100,
        "combos": combos[:10],
    }

# ═══════════════════════════════════════════════
# Feature Importance (3방법)
# ═══════════════════════════════════════════════
FEATURE_KEYS = ["ema_spread_pct", "rsi_15m", "rsi_5m", "rg_breadth", "rg_btc_ret",
                "rg_turnover", "rg_btc_dom", "rg_top5", "spread_pct"]

def _bin_index(val, edges):
    for i, e in enumerate(edges):
        if val < e:
            return i
    return len(edges)

def _feature_edges(rows, key, n_bins=5):
    vals = sorted(r[key] for r in rows if r[key] is not None)
    if len(vals) < n_bins + 1:
        return []
    step = len(vals) // n_bins
    return [vals[i * step] for i in range(1, n_bins)]

def importance_variance(rows):
    """각 feature bin의 EV 분산 (전체 EV 분산 대비 설명력)."""
    if not rows:
        return {}
    all_ev = [r["net_pnl"] for r in rows]
    total_var = pstdev(all_ev) ** 2 if len(all_ev) > 1 else 1e-9
    out = {}
    for k in FEATURE_KEYS:
        edges = _feature_edges(rows, k)
        if not edges:
            out[k] = 0.0
            continue
        by_bin = defaultdict(list)
        for r in rows:
            by_bin[_bin_index(r[k], edges)].append(r["net_pnl"])
        bin_means = [mean(v) for v in by_bin.values() if len(v) > 0]
        if len(bin_means) < 2:
            out[k] = 0.0
            continue
        between_var = pstdev(bin_means) ** 2
        out[k] = between_var / total_var if total_var > 1e-9 else 0.0
    return out

def importance_mi(rows):
    """이진화된 outcome (win/loss)에 대한 mutual information (근사)."""
    if not rows:
        return {}
    labels = [1 if r["net_pnl"] > 0 else 0 for r in rows]
    p1 = sum(labels) / len(labels)
    p0 = 1 - p1
    def _entropy(p):
        return -sum(x * log(x + 1e-12) for x in p if x > 0)
    h_y = _entropy([p0, p1])
    out = {}
    for k in FEATURE_KEYS:
        edges = _feature_edges(rows, k)
        if not edges:
            out[k] = 0.0
            continue
        by_bin = defaultdict(lambda: [0, 0])
        for r, lb in zip(rows, labels):
            by_bin[_bin_index(r[k], edges)][lb] += 1
        h_y_given_x = 0
        n = len(rows)
        for bin_counts in by_bin.values():
            b_total = sum(bin_counts)
            if b_total == 0:
                continue
            p_bin = b_total / n
            probs = [c / b_total for c in bin_counts]
            h_y_given_x += p_bin * _entropy(probs)
        mi = h_y - h_y_given_x
        out[k] = max(0.0, mi)
    return out

def importance_permutation(train_rows, val_rows, n_iter=20):
    """Permutation: feature 셔플 후 조건 탐색 성능 변화. 간이 구현."""
    random.seed(RANDOM_SEED)
    if not val_rows:
        return {k: 0.0 for k in FEATURE_KEYS}
    baseline_ev = mean(r["net_pnl"] for r in val_rows) if val_rows else 0
    baseline_pf = stats(val_rows)["pf"]
    out = {}
    for k in FEATURE_KEYS:
        drops = []
        for _ in range(n_iter):
            shuffled_vals = [r[k] for r in val_rows]
            random.shuffle(shuffled_vals)
            perturbed = [dict(r, **{k: v}) for r, v in zip(val_rows, shuffled_vals)]
            good = [r for r in perturbed if r["ema_spread_pct"] >= 1.0 and r["rsi_15m"] >= 70]
            if not good:
                continue
            new_ev = mean(r["net_pnl"] for r in good)
            drops.append(abs(baseline_ev - new_ev))
        out[k] = mean(drops) if drops else 0.0
    return out

def combined_importance(v_scores, m_scores, p_scores):
    def _norm(d):
        s = sum(d.values())
        return {k: (v / s if s > 1e-9 else 0) for k, v in d.items()}
    v_n, m_n, p_n = _norm(v_scores), _norm(m_scores), _norm(p_scores)
    keys = set(v_n) | set(m_n) | set(p_n)
    combined = {k: (v_n.get(k, 0) + m_n.get(k, 0) + p_n.get(k, 0)) / 3 for k in keys}
    return dict(sorted(combined.items(), key=lambda x: x[1], reverse=True))

# ═══════════════════════════════════════════════
# 출력 (Text + CSV)
# ═══════════════════════════════════════════════
def fmt_pf(pf):
    if pf == float("inf"):
        return "∞"
    if pf != pf:  # NaN
        return "nan"
    return f"{pf:.2f}"

def fmt_dpf(d):
    if d == float("inf"):
        return "+∞"
    if d == float("-inf"):
        return "-∞"
    if d != d:
        return "nan"
    return f"{d:+.2f}"

def build_report(all_rows, train, val, results_dir):
    lines = []
    lines.append("=" * 70)
    lines.append("CLM Regime Backtest — 전략 분석 엔진")
    lines.append("=" * 70)
    all_sess = {r["session_id"] for r in all_rows}
    with_reg = [r for r in all_rows if r["has_regime"]]
    lines.append(f"총 거래: {len(all_rows)}건 / 세션: {len(all_sess)}")
    lines.append(f"regime feature 있음: {len(with_reg)}건")
    lines.append(f"Train: {len(train)}건 / Val: {len(val)}건 (세션 단위 {int(TRAIN_RATIO*100)}/{int(100-TRAIN_RATIO*100)} 분할)")
    lines.append("")
    lines.append("※ 지표 정의:")
    lines.append("   PF        = 전체 거래의 gross_profit / gross_loss (통합 계산)")
    lines.append("   Stability = 세션별 EV 평균의 CV → 1/(1+CV). regime 분산 측정 전용.")
    lines.append("               전략 성과 대체 지표가 아님. 낮으면 regime 의존도 큼.")
    lines.append("")

    lines.append("─" * 70)
    lines.append("[2] EMA spread Grid (Train)")
    lines.append("─" * 70)
    for row in grid_analysis(train, "ema_spread_pct", EMA_BINS):
        lines.append(f"  {row['bin']:>10s}: n={row['n']:3d} wr{row['wr']:.0f}% "
                     f"PF={fmt_pf(row['pf']):>5s} EV{row['ev']:+.3f}% MFE_L{row['mfe_l']:+.3f}%")
    lines.append("")

    lines.append("─" * 70)
    lines.append("[3] RSI15 Grid (Train)")
    lines.append("─" * 70)
    for row in grid_analysis(train, "rsi_15m", RSI_BINS):
        lines.append(f"  {row['bin']:>10s}: n={row['n']:3d} wr{row['wr']:.0f}% "
                     f"PF={fmt_pf(row['pf']):>5s} EV{row['ev']:+.3f}% MFE_L{row['mfe_l']:+.3f}%")
    lines.append("")

    lines.append("─" * 70)
    lines.append("[4] EMA × RSI15 2D Heatmap (Train, n/PF/EV)")
    lines.append("─" * 70)
    hm = heatmap_ema_rsi(train)
    header = "RSI15 \\ EMA".ljust(14) + "".join(f"{b[0]}~{b[1]}".center(13) for b in EMA_BINS)
    lines.append(header)
    for row in hm:
        rsi_lbl = row[0]["rsi"]
        cells = []
        for c in row:
            if c["n"] < MIN_SAMPLE:
                cells.append("     --      ")
            else:
                cells.append(f"n{c['n']:>2d} PF{fmt_pf(c['pf']):>4s}".center(13))
        lines.append(rsi_lbl.ljust(14) + "".join(cells))
    lines.append("")

    lines.append("─" * 70)
    lines.append("[5] Regime Threshold 분석 (Train, 절대 기준)")
    lines.append("─" * 70)
    reg = regime_split(train)
    for name, rows_ in [("BTC 24h", reg["btc_ret"]), ("Breadth", reg["breadth"]), ("Turnover", reg["turnover"])]:
        lines.append(f"  [{name}]")
        for r in rows_:
            lines.append(f"    {r['bin']:>12s}: n={r['n']:3d} wr{r['wr']:.0f}% "
                         f"PF={fmt_pf(r['pf']):>5s} EV{r['ev']:+.3f}%")
    lines.append("")

    lines.append("─" * 70)
    lines.append("[6~8] Top-N 조건 탐색 + Val 검증 + Stability (Train→Val)")
    lines.append(f"      MIN_SAMPLE={MIN_SAMPLE}, 판정: STRONG/SURVIVED/WATCH/FAILED")
    lines.append("      stab = 세션 간 EV 분산 지표 (regime 안정성, [0,1])")
    lines.append("             ⚠ 전략 성과 아님. PF/EV와 별개 축.")
    lines.append("─" * 70)
    tops = search_top_conditions(train, val, MIN_SAMPLE)
    lines.append(f"{'rank':<4}{'condition':<40}{'tr_n':>5}{'tr_PF':>7}{'tr_EV':>8}"
                 f"{'val_n':>6}{'val_PF':>7}{'val_EV':>8}{'ΔPF':>7}{'sess':>5}{'stab':>6}  tag")
    for i, r in enumerate(tops[:20], 1):
        lines.append(
            f"{i:<4}{r['condition']:<40}{r['tr_n']:>5}{fmt_pf(r['tr_pf']):>7}"
            f"{r['tr_ev']:>+7.3f}%{r['val_n']:>6}{fmt_pf(r['val_pf']):>7}"
            f"{r['val_ev']:>+7.3f}%{fmt_dpf(r['d_pf']):>7}"
            f"{r['sessions']:>5}{r['stability']:>6.2f}  {r['tag']}"
        )
    lines.append("")

    lines.append("─" * 70)
    lines.append("[9] Timeout 부분 시뮬레이션")
    lines.append("     ⚠ tick-level 재현 불가 — post_exit_tracks 없이는 완전 시뮬 불가능")
    lines.append("     현재 데이터는 종료 후 30/60/120/180초 tracking 미보유")
    lines.append("─" * 70)
    lines.append("     (backtest CSV에는 exit 시점 스냅샷만 저장됨. 별도 tick 로그 필요)")
    lines.append("")

    lines.append("─" * 70)
    lines.append("[10] MFE L / 즉사율 분석")
    lines.append("─" * 70)
    ida = instant_death_analysis(train)
    lines.append(f"  전체 즉사(MFE≤0.001%) 비율: {ida['overall']:.1f}%")
    lines.append(f"  즉사 조합 Top 10 (RSI15 × EMA):")
    for c in ida["combos"]:
        lines.append(f"    RSI{c['rsi']:>8s} × EMA{c['ema']:>10s}: n={c['n']:3d} death{c['death_rate']:5.0f}%")
    lines.append("")

    lines.append("─" * 70)
    lines.append("[11] Feature Importance (variance / MI / permutation 앙상블)")
    lines.append("─" * 70)
    v_imp = importance_variance(train)
    m_imp = importance_mi(train)
    p_imp = importance_permutation(train, val)
    combined = combined_importance(v_imp, m_imp, p_imp)
    lines.append(f"  {'feature':<20}{'variance':>10}{'MI':>10}{'perm':>10}{'combined':>10}")
    for k, v in combined.items():
        lines.append(f"  {k:<20}{v_imp.get(k, 0):>10.3f}{m_imp.get(k, 0):>10.3f}"
                     f"{p_imp.get(k, 0):>10.3f}{v:>10.3f}")
    lines.append("")

    lines.append("─" * 70)
    lines.append("[12] 최종 추천 조건 (FINAL_RECOMMEND 조건 통과)")
    lines.append("     Val PF≥1.0, Val EV>0, Sessions≥3, Stability≥0.7, n≥MIN_SAMPLE")
    lines.append("─" * 70)
    final = [r for r in tops
             if r["val_pf"] >= 1.0 and r["val_ev"] > 0
             and r["sessions"] >= STABILITY_MIN_SESSIONS
             and r["stability"] >= STABILITY_THRESHOLD
             and r["val_n"] >= MIN_SAMPLE]
    if not final:
        lines.append("  ⚠ 조건 통과 없음 — 더 많은 세션/거래 필요, 또는 진짜 alpha 부재")
    else:
        for i, r in enumerate(final[:10], 1):
            lines.append(
                f"  #{i} {r['condition']:<38} "
                f"tr(n{r['tr_n']} PF{fmt_pf(r['tr_pf'])} EV{r['tr_ev']:+.3f}%) "
                f"val(n{r['val_n']} PF{fmt_pf(r['val_pf'])} EV{r['val_ev']:+.3f}%) "
                f"stab{r['stability']:.2f} tag:{r['tag']}"
            )
    lines.append("")
    lines.append("=" * 70)

    # ── CSV 저장 ──
    os.makedirs(results_dir, exist_ok=True)
    _write_csv(os.path.join(results_dir, "heatmap.csv"),
               ["rsi_bin", "ema_bin", "n", "pf", "ev"],
               [{"rsi_bin": c["rsi"], "ema_bin": c["ema"], "n": c["n"],
                 "pf": c["pf"], "ev": c["ev"]}
                for row in hm for c in row])
    reg_flat = []
    for name, rows_ in [("btc_ret", reg["btc_ret"]), ("breadth", reg["breadth"]), ("turnover", reg["turnover"])]:
        for r in rows_:
            reg_flat.append({"feature": name, "bin": r["bin"], "n": r["n"],
                             "wr": r["wr"], "pf": r["pf"], "ev": r["ev"]})
    _write_csv(os.path.join(results_dir, "regime_split.csv"),
               ["feature", "bin", "n", "wr", "pf", "ev"], reg_flat)
    _write_csv(os.path.join(results_dir, "top_conditions.csv"),
               ["rank", "condition", "tr_n", "tr_pf", "tr_ev", "tr_mfe_l",
                "val_n", "val_pf", "val_ev", "val_mfe_l", "d_pf", "sessions", "stability", "tag"],
               [dict(r, rank=i) for i, r in enumerate(tops, 1)])
    _write_csv(os.path.join(results_dir, "final_recommendations.csv"),
               ["rank", "condition", "tr_n", "tr_pf", "tr_ev", "val_n", "val_pf", "val_ev",
                "sessions", "stability", "tag"],
               [dict(r, rank=i) for i, r in enumerate(final[:10], 1)])
    _write_csv(os.path.join(results_dir, "importance_variance.csv"),
               ["feature", "importance"],
               [{"feature": k, "importance": v} for k, v in sorted(v_imp.items(), key=lambda x: -x[1])])
    _write_csv(os.path.join(results_dir, "importance_mi.csv"),
               ["feature", "importance"],
               [{"feature": k, "importance": v} for k, v in sorted(m_imp.items(), key=lambda x: -x[1])])
    _write_csv(os.path.join(results_dir, "importance_perm.csv"),
               ["feature", "importance"],
               [{"feature": k, "importance": v} for k, v in sorted(p_imp.items(), key=lambda x: -x[1])])
    _write_csv(os.path.join(results_dir, "feature_rank.csv"),
               ["rank", "feature", "importance"],
               [{"rank": i, "feature": k, "importance": v}
                for i, (k, v) in enumerate(combined.items(), 1)])

    return "\n".join(lines)

def _write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

# ═══════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════
def main():
    global MIN_SAMPLE
    ap = argparse.ArgumentParser(description="CLM Regime Backtest 분석 엔진")
    ap.add_argument("patterns", nargs="+", help="CSV glob 패턴 (예: bots/momentum_clm_*.csv)")
    ap.add_argument("--min-sample", type=int, default=MIN_SAMPLE, help=f"조건 최소 표본 (기본 {MIN_SAMPLE})")
    ap.add_argument("--results-dir", default="bots/results", help="CSV/txt 출력 폴더")
    args = ap.parse_args()
    MIN_SAMPLE = args.min_sample

    rows, files = load_csv_files(args.patterns)
    print(f"로드: {len(files)}개 파일, {len(rows)}건", file=sys.stderr)
    train, val, train_ids, val_ids = split_train_val(rows)
    print(f"Train 세션: {len(train_ids)} / Val 세션: {len(val_ids)}", file=sys.stderr)

    report = build_report(rows, train, val, args.results_dir)
    txt_path = os.path.join(args.results_dir, "report.txt")
    os.makedirs(args.results_dir, exist_ok=True)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"\n결과 저장: {args.results_dir}/", file=sys.stderr)

if __name__ == "__main__":
    main()
