#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
filter_tournament.py
=====================
shadow_stats.json의 CLM trade_records를 읽어서
다수 필터 후보를 동일 데이터셋 위에서 비교하는 토너먼트 도구.

사용:
    python3 filter_tournament.py /path/to/shadow_stats.json
    python3 filter_tournament.py /path/to/shadow_stats.json --route CLM --top 10

설계:
    - shadow_stats.json → route별 trade_records (최근 300건) 활용
    - 각 필터를 적용했을 때의 n, WR, PnL, MFE, MAE, cap, 감소율 계산
    - 승격 기준 자동 판정 (n≥100, PnL≥base+0.08%, MAE≤base*0.75 등)
    - python 표준 라이브러리만 사용
"""

import argparse
import json
import os
import sys
from statistics import mean

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"

FILTERS = [
    ("B60", "body_pct", "<=", 0.60, "body_pct≤0.60"),
    # CS 제거: check_fn에서 cs>0.50 이미 차단, ind_filter 중복
    ("RSI5", "rsi_5m", "<=", 51.0, "rsi_5m≤51"),
    ("RSI5_55", "rsi_5m", "<=", 55.0, "rsi_5m≤55"),
    ("RSI5_60", "rsi_5m", "<=", 60.0, "rsi_5m≤60"),
    ("ATR", "atr_pct", "<=", 0.32, "atr_pct≤0.32"),
    ("ATR_40", "atr_pct", "<=", 0.40, "atr_pct≤0.40"),
    ("ATR_50", "atr_pct", "<=", 0.50, "atr_pct≤0.50"),
    ("EMA15", "ema_spread_15", "<=", -0.23, "ema_spread≤-0.23"),
    ("EMA15_0", "ema_spread_15", "<=", 0.0, "ema_spread≤0"),
    ("EMA15_1", "ema_spread_15", "<=", 1.0, "ema_spread≤1.0"),
    ("OBSLIP", "ob_slip_sell_10000k", "<=", 0.15, "ob_slip≤0.15%"),
    ("OBSLIP_20", "ob_slip_sell_10000k", "<=", 0.20, "ob_slip≤0.20%"),
    ("OBSLIP_27", "ob_slip_sell_10000k", "<=", 0.27, "ob_slip≤0.27%"),
    # CS_55/CS_60 제거: check_fn cs>0.50 차단 때문에 0.55/0.60은 의미 없음 (모든 trade가 ≤0.50)
    ("WICK_HI", "wick_ratio", ">=", 0.50, "wick_ratio≥0.50"),
    ("RSI15", "rsi_15m", "<=", 49.3, "rsi_15m≤49.3"),
    ("RSI15_55", "rsi_15m", "<=", 55.0, "rsi_15m≤55"),
]

COMBO_FILTERS = [
    ("B60+R5", [("body_pct", "<=", 0.60), ("rsi_5m", "<=", 51.0)], "body≤0.60 + rsi5≤51"),
    ("B60+ATR", [("body_pct", "<=", 0.60), ("atr_pct", "<=", 0.50)], "body≤0.60 + atr≤0.50"),
    ("B60+EMA", [("body_pct", "<=", 0.60), ("ema_spread_15", "<=", 0.0)], "body≤0.60 + ema≤0"),
    ("R5+ATR", [("rsi_5m", "<=", 51.0), ("atr_pct", "<=", 0.50)], "rsi5≤51 + atr≤0.50"),
    # R5+CS 제거: cs≤0.50은 check_fn에서 이미 강제, 조합 의미 없음
    ("B60+OB", [("body_pct", "<=", 0.60), ("ob_slip_sell_10000k", "<=", 0.27)], "body≤0.60 + obslip≤0.27"),
]


def load_trade_records(path, route="CLM"):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    key = route
    if key not in data:
        for k in data:
            if k.upper() == route.upper() or route in k:
                key = k
                break
    if key not in data:
        print(f"{RED}[오류] route '{route}' 없음. 사용 가능: {list(data.keys())[:20]}{RESET}")
        sys.exit(1)
    records = data[key].get("trade_records", [])
    if not records:
        print(f"{RED}[오류] '{key}'에 trade_records 없음{RESET}")
        sys.exit(1)
    return records, key


def calc_stats(records):
    if not records:
        return {"n": 0, "wr": 0, "pnl": 0, "mfe": 0, "mae": 0, "cap": 0}
    n = len(records)
    wins = sum(1 for r in records if r.get("pnl", 0) > 0)
    pnls = [r.get("pnl", 0) for r in records]
    mfes = [r.get("mfe", 0) for r in records]
    maes = [r.get("mae", r.get("inds", {}).get("mae_60s", 0)) for r in records if r.get("mae") is not None or r.get("inds", {}).get("mae_60s") is not None]
    return {
        "n": n,
        "wr": wins / n * 100 if n else 0,
        "pnl": mean(pnls) * 100 if pnls else 0,
        "mfe": mean(mfes) * 100 if mfes else 0,
        "cap": sum(pnls) * 100 if pnls else 0,
    }


def apply_filter(records, key, op, threshold):
    result = []
    skipped = 0
    for r in records:
        inds = r.get("inds", {})
        val = inds.get(key)
        if val is None:
            skipped += 1
            continue
        if op == "<=" and val <= threshold:
            result.append(r)
        elif op == ">=" and val >= threshold:
            result.append(r)
        elif op == "<" and val < threshold:
            result.append(r)
        elif op == ">" and val > threshold:
            result.append(r)
    return result, skipped


def check_promotion(base_stats, filtered_stats):
    reasons = []
    ok = True
    n = filtered_stats["n"]
    if n < 100:
        reasons.append(f"n={n}<100")
        ok = False
    pnl_thresh = base_stats["pnl"] + 0.08
    if filtered_stats["pnl"] < pnl_thresh:
        reasons.append(f"pnl {filtered_stats['pnl']:.3f}<{pnl_thresh:.3f}")
        ok = False
    if filtered_stats["cap"] <= 0:
        reasons.append(f"cap={filtered_stats['cap']:.1f}%≤0")
        ok = False
    reduction = 1 - filtered_stats["n"] / base_stats["n"] if base_stats["n"] else 0
    if reduction > 0.50:
        reasons.append(f"감소율{reduction:.0%}>50%")
        ok = False
    return ok, reasons, reduction


def main():
    parser = argparse.ArgumentParser(description="CLM 필터 토너먼트")
    parser.add_argument("stats_path", help="shadow_stats.json 경로")
    parser.add_argument("--route", default="CLM", help="기본 route (default: CLM)")
    parser.add_argument("--top", type=int, default=0, help="상위 N개만 표시")
    args = parser.parse_args()

    records, key = load_trade_records(args.stats_path, args.route)
    base = calc_stats(records)
    print(f"\n{'='*70}")
    print(f" CLM 필터 토너먼트 — {key} base: n={base['n']}")
    print(f"{'='*70}")
    print(f" BASE: n={base['n']}  WR={base['wr']:.1f}%  PnL={base['pnl']:+.3f}%  "
          f"MFE={base['mfe']:+.3f}%  cap={base['cap']:+.1f}%")
    print(f"{'─'*70}")

    results = []
    for name, fkey, op, thresh, desc in FILTERS:
        filtered, skipped = apply_filter(records, fkey, op, thresh)
        if not filtered:
            continue
        st = calc_stats(filtered)
        promoted, reasons, reduction = check_promotion(base, st)
        results.append((name, desc, st, promoted, reasons, reduction, skipped))

    # ── 조합 필터 ──
    for name, conditions, desc in COMBO_FILTERS:
        filtered = list(records)
        total_skipped = 0
        for fkey, op, thresh in conditions:
            filtered, skipped = apply_filter(filtered, fkey, op, thresh)
            total_skipped += skipped
            if not filtered:
                break
        if not filtered:
            continue
        st = calc_stats(filtered)
        promoted, reasons, reduction = check_promotion(base, st)
        results.append((name, desc, st, promoted, reasons, reduction, total_skipped))

    results.sort(key=lambda x: -x[2]["pnl"])

    if args.top > 0:
        results = results[:args.top]

    print(f"\n {'필터':<12} {'n':>5} {'감소':>6} {'WR':>6} {'PnL':>8} "
          f"{'MFE':>8} {'cap':>7}  승격")
    print(f" {'─'*12} {'─'*5} {'─'*6} {'─'*6} {'─'*8} {'─'*8} {'─'*7}  {'─'*15}")

    for name, desc, st, promoted, reasons, reduction, skipped in results:
        pnl_delta = st["pnl"] - base["pnl"]
        pnl_color = GREEN if pnl_delta > 0 else RED if pnl_delta < 0 else DIM
        promo_str = f"{GREEN}✅ PASS{RESET}" if promoted else f"{RED}❌ {','.join(reasons[:2])}{RESET}"
        print(f" {name:<12} {st['n']:>5} {reduction:>5.0%} "
              f"{st['wr']:>5.1f}% {pnl_color}{st['pnl']:>+7.3f}%{RESET} "
              f"{st['mfe']:>+7.3f}% {st['cap']:>+6.1f}%  {promo_str}")

    print(f"\n{'─'*70}")
    print(f" 승격 기준: n≥100, PnL≥base+0.08%, cap>0, 감소율≤50%")

    passing = [r for r in results if r[3]]
    if passing:
        print(f"\n {GREEN}✅ 승격 후보: {', '.join(r[0] for r in passing)}{RESET}")
    else:
        print(f"\n {YELLOW}⚠ 승격 기준 충족 후보 없음{RESET}")

    print()


if __name__ == "__main__":
    main()
