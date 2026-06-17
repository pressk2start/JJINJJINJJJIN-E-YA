#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_filters.py
====================
크립토 모멘텀 스캐너 봇 (또는 CLM 전략) 의 누적 CSV 로그를 입력 받아
"만약 이런 필터를 켰다면 PF/WR 이 어떻게 바뀌었을까?" 를 회고적으로 검증한다.

사용 예:
    python3 backtest_filters.py /home/ubuntu/bot/momentum_*.csv
    python3 backtest_filters.py /home/ubuntu/bot/momentum_clm_*.csv --clm

설계 원칙:
    - python 표준 라이브러리만 사용 (csv, glob, argparse, sys, os, statistics)
    - 단일 세션 over-fitting 을 피하기 위해 여러 CSV 를 합쳐서 평가
    - 잔여 표본 n < 30 은 노이즈로 간주, n < 50 은 'low conf' 마킹
    - ANSI 컬러로 개선/악화를 시각화 (green = 개선, red = 악화)
"""

import argparse
import csv
import glob
import os
import sys
from collections import defaultdict
from statistics import mean

# ─────────────────────────────────────────────────────────────────────────────
# ANSI 컬러 코드
# ─────────────────────────────────────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[32m"
RED     = "\033[31m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
MAGENTA = "\033[35m"
BLUE    = "\033[34m"


def color_delta(delta: float, fmt: str = "{:+.2f}", neutral_zero: bool = True) -> str:
    if neutral_zero and abs(delta) < 1e-9:
        return f"{DIM}{fmt.format(delta)}{RESET}"
    if delta > 0:
        return f"{GREEN}{fmt.format(delta)}{RESET}"
    return f"{RED}{fmt.format(delta)}{RESET}"


# ─────────────────────────────────────────────────────────────────────────────
# CSV 로딩
# ─────────────────────────────────────────────────────────────────────────────
def _to_float(v, default=None):
    if v is None:
        return default
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return default
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def load_csvs(paths, is_clm: bool):
    rows = []
    file_stats = []
    for p in paths:
        if not os.path.isfile(p):
            print(f"{YELLOW}[경고] 파일이 존재하지 않음: {p}{RESET}", file=sys.stderr)
            continue
        n_before = len(rows)
        try:
            with open(p, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for raw in reader:
                    row = _parse_row(raw, is_clm=is_clm, source_file=os.path.basename(p))
                    if row is not None:
                        rows.append(row)
        except Exception as e:
            print(f"{RED}[에러] {p} 읽기 실패: {e}{RESET}", file=sys.stderr)
            continue
        file_stats.append((os.path.basename(p), len(rows) - n_before))
    return rows, file_stats


def _parse_row(raw: dict, is_clm: bool, source_file: str):
    net_pnl = _to_float(raw.get("net_pnl"))
    if net_pnl is None:
        net_pnl = _to_float(raw.get("pnl"))
    if net_pnl is None:
        return None

    row = {
        "time":        (raw.get("time") or "").strip(),
        "market":      (raw.get("market") or "").strip().upper(),
        "group":       (raw.get("group") or "").strip(),
        "entry_price": _to_float(raw.get("entry_price")),
        "exit_price":  _to_float(raw.get("exit_price")),
        "pnl":         _to_float(raw.get("pnl"), default=net_pnl),
        "net_pnl":     net_pnl,
        "mae":         _to_float(raw.get("mae")),
        "peak_pnl":    _to_float(raw.get("peak_pnl")),
        "hold_sec":    _to_float(raw.get("hold_sec")),
        "reason":      (raw.get("reason") or "").strip().lower(),
        "abs_move":    _to_float(raw.get("abs_move")),
        "spread_pct":  _to_float(raw.get("spread_pct")),
        "ask_krw":     _to_float(raw.get("ask_krw")),
        "bid_krw":     _to_float(raw.get("bid_krw")),
        "slip_buy":    _to_float(raw.get("slip_buy")),
        "_source":     source_file,
    }

    if not is_clm:
        row["z_score"]   = _to_float(raw.get("z_score"))
        row["vol_z"]     = _to_float(raw.get("vol_z"))
        row["vol_ratio"] = _to_float(raw.get("vol_ratio"))
    else:
        row["rsi_5m"]          = _to_float(raw.get("rsi_5m"))
        row["rsi_15m"]         = _to_float(raw.get("rsi_15m"))
        row["ema_spread_pct"]  = _to_float(raw.get("ema_spread_pct"))

    ask = row["ask_krw"] or 0.0
    bid = row["bid_krw"] or 0.0
    row["depth_krw"] = ask + bid
    row["depth_man"] = row["depth_krw"] / 10000.0 if row["depth_krw"] else 0.0

    return row


# ─────────────────────────────────────────────────────────────────────────────
# 통계 계산
# ─────────────────────────────────────────────────────────────────────────────
def compute_stats(rows: list) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}

    pnls = [r["net_pnl"] for r in rows]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    wr = (len(wins) / n) * 100.0
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 1e-9 else float("inf")

    avg_pnl = mean(pnls)
    sum_pnl = sum(pnls)

    reasons = defaultdict(int)
    for r in rows:
        reasons[r["reason"] or "unknown"] += 1

    pct_target  = (reasons.get("target", 0)  / n) * 100.0
    pct_trail   = (reasons.get("trail", 0)   / n) * 100.0
    pct_timeout = (reasons.get("timeout", 0) / n) * 100.0

    peak_vals = [r["peak_pnl"] for r in rows if r["peak_pnl"] is not None]
    win_rows  = [r for r in rows if r["net_pnl"] > 0]
    loss_rows = [r for r in rows if r["net_pnl"] <= 0]
    mfe_avg = mean(peak_vals) if peak_vals else 0.0
    mfe_w   = mean([r["peak_pnl"] for r in win_rows  if r["peak_pnl"] is not None]) if win_rows  else 0.0
    mfe_l   = mean([r["peak_pnl"] for r in loss_rows if r["peak_pnl"] is not None]) if loss_rows else 0.0
    mfe_gap = (mfe_w / mfe_l) if abs(mfe_l) > 1e-9 else float("inf")

    return {
        "n": n,
        "wr": wr,
        "pf": pf,
        "avg_pnl": avg_pnl,
        "sum_pnl": sum_pnl,
        "pct_target": pct_target,
        "pct_trail": pct_trail,
        "pct_timeout": pct_timeout,
        "mfe_avg": mfe_avg,
        "mfe_w": mfe_w,
        "mfe_l": mfe_l,
        "mfe_gap": mfe_gap,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    }


def fmt_pf(pf: float) -> str:
    if pf == float("inf"):
        return "inf"
    return f"{pf:.2f}"


def print_stats(label: str, s: dict, color=CYAN):
    if s["n"] == 0:
        print(f"  {RED}샘플 0개 — 건너뜀{RESET}")
        return
    print(f"{color}{BOLD}{label} (n={s['n']}):{RESET}")
    print(f"  WR {s['wr']:.1f}%, PF {fmt_pf(s['pf'])}, "
          f"avg pnl {s['avg_pnl']:+.2f}%, sum {s['sum_pnl']:+.2f}%")
    print(f"  target {s['pct_target']:.0f}%, "
          f"trail {s['pct_trail']:.0f}%, "
          f"timeout {s['pct_timeout']:.0f}%")
    gap_str = "inf" if s["mfe_gap"] == float("inf") else f"{s['mfe_gap']:.1f}x"
    print(f"  MFE avg {s['mfe_avg']:+.2f}% / W{s['mfe_w']:+.2f}% L{s['mfe_l']:+.2f}% (gap {gap_str})")


# ─────────────────────────────────────────────────────────────────────────────
# 필터 정의
# ─────────────────────────────────────────────────────────────────────────────
EXCLUDE_COINS = ["SUI", "NEAR", "BCH", "SOL", "JTO", "TAO"]


def _ge(field, threshold):
    def f(r):
        v = r.get(field)
        return v is not None and v >= threshold
    return f


def _le(field, threshold):
    def f(r):
        v = r.get(field)
        return v is not None and v <= threshold
    return f


def _between(field, lo, hi):
    def f(r):
        v = r.get(field)
        return v is not None and lo <= v <= hi
    return f


def _coin_in_market(coin: str):
    coin_u = coin.upper()
    def f(r):
        m = r.get("market") or ""
        if "-" in m:
            return m.split("-")[-1].upper() == coin_u
        return coin_u in m.upper()
    return f


def _exclude_coins(coins):
    matchers = [_coin_in_market(c) for c in coins]
    def f(r):
        return not any(m(r) for m in matchers)
    return f


def build_momentum_filters():
    return [
        ("depth >= 500만",       _ge("depth_man", 500)),
        ("depth >= 1000만",      _ge("depth_man", 1000)),
        ("depth >= 5000만",      _ge("depth_man", 5000)),
        ("spread <= 0.05%",      _le("spread_pct", 0.05)),
        ("spread <= 0.08%",      _le("spread_pct", 0.08)),
        ("spread <= 0.10%",      _le("spread_pct", 0.10)),
        ("vol_z >= 50",          _ge("vol_z", 50)),
        ("vol_z >= 10",          _ge("vol_z", 10)),
        ("vol_z <= 50",          _le("vol_z", 50)),
        ("vol_z 5-10",           _between("vol_z", 5, 10)),
        ("vol_ratio >= 200",     _ge("vol_ratio", 200)),
        ("z_score 3-5",          _between("z_score", 3, 5)),
        ("z_score 5-7",          _between("z_score", 5, 7)),
        ("z_score 3-7",          _between("z_score", 3, 7)),
        ("abs_move >= 0.25",     _ge("abs_move", 0.25)),
        (f"exclude {','.join(EXCLUDE_COINS)}", _exclude_coins(EXCLUDE_COINS)),
    ]


def build_clm_filters():
    return [
        ("depth >= 500만",        _ge("depth_man", 500)),
        ("depth >= 1000만",       _ge("depth_man", 1000)),
        ("spread <= 0.05%",       _le("spread_pct", 0.05)),
        ("spread <= 0.10%",       _le("spread_pct", 0.10)),
        ("rsi_5m 70-75",          _between("rsi_5m", 70, 75)),
        ("rsi_5m <= 75",          _le("rsi_5m", 75)),
        ("rsi_15m 65-70",         _between("rsi_15m", 65, 70)),
        ("ema_spread 0.6-1.0",    _between("ema_spread_pct", 0.6, 1.0)),
        ("ema_spread 1.0-1.5",    _between("ema_spread_pct", 1.0, 1.5)),
        ("ema_spread >= 1.0",     _ge("ema_spread_pct", 1.0)),
        (f"exclude {','.join(EXCLUDE_COINS)}", _exclude_coins(EXCLUDE_COINS)),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 필터 적용 / 리포트
# ─────────────────────────────────────────────────────────────────────────────
MIN_N_REPORT = 30
LOW_CONF_N   = 50


def apply_filter(rows: list, predicate) -> list:
    return [r for r in rows if predicate(r)]


def evaluate_filter(name: str, predicate, rows: list, baseline: dict) -> dict:
    kept = apply_filter(rows, predicate)
    total = len(rows)
    n_keep = len(kept)
    n_excl = total - n_keep
    excl_pct = (n_excl / total * 100.0) if total else 0.0

    stats = compute_stats(kept)
    if stats["n"] == 0:
        return {
            "name": name, "kept": kept, "n_keep": n_keep, "n_excl": n_excl,
            "excl_pct": excl_pct, "stats": stats, "skip": True,
        }

    d_wr  = stats["wr"]  - baseline["wr"]
    d_pf  = (stats["pf"] - baseline["pf"]) if (
        stats["pf"] != float("inf") and baseline["pf"] != float("inf")
    ) else None
    d_avg = stats["avg_pnl"] - baseline["avg_pnl"]

    return {
        "name": name,
        "kept": kept,
        "n_keep": n_keep,
        "n_excl": n_excl,
        "excl_pct": excl_pct,
        "stats": stats,
        "d_wr": d_wr,
        "d_pf": d_pf,
        "d_avg": d_avg,
        "skip": False,
    }


def print_filter_result(res: dict):
    name = res["name"]
    if res["n_keep"] < MIN_N_REPORT:
        print(f"{DIM}FILTER: {name}{RESET}")
        print(f"  {DIM}잔여 {res['n_keep']} 개 < {MIN_N_REPORT} — 노이즈로 간주, 건너뜀{RESET}\n")
        return

    s = res["stats"]
    low_conf = res["n_keep"] < LOW_CONF_N
    conf_tag = f" {YELLOW}[low conf]{RESET}" if low_conf else ""

    print(f"{BOLD}{CYAN}FILTER:{RESET} {BOLD}{name}{RESET}{conf_tag}")
    print(f"  Excluded: {res['n_excl']}/{res['n_excl']+res['n_keep']} ({res['excl_pct']:.0f}%)")
    gap_str = "inf" if s["mfe_gap"] == float("inf") else f"{s['mfe_gap']:.1f}x"
    print(f"  Remaining: WR {s['wr']:.1f}%, PF {fmt_pf(s['pf'])}, "
          f"avg {s['avg_pnl']:+.2f}%, MFE W/L {gap_str}")

    d_wr_s  = color_delta(res["d_wr"], "{:+.1f}%p")
    d_avg_s = color_delta(res["d_avg"], "{:+.2f}%p")
    if res["d_pf"] is None:
        d_pf_s = f"{DIM}n/a{RESET}"
    else:
        d_pf_s = color_delta(res["d_pf"], "{:+.2f}")
    print(f"  Δ vs baseline: WR {d_wr_s}, PF {d_pf_s}, avg {d_avg_s}\n")


# ─────────────────────────────────────────────────────────────────────────────
# 코인별 / 시간대별 브레이크다운
# ─────────────────────────────────────────────────────────────────────────────
def per_coin_breakdown(rows: list, top_n: int = 8):
    by_coin = defaultdict(list)
    for r in rows:
        m = r["market"]
        coin = m.split("-")[-1] if "-" in m else m
        by_coin[coin].append(r)

    items = []
    for coin, lst in by_coin.items():
        s = compute_stats(lst)
        items.append((coin, s))

    items.sort(key=lambda x: x[1]["sum_pnl"], reverse=True)

    print(f"{BOLD}{MAGENTA}[코인별 기여도 — 상위 {top_n}]{RESET}")
    print(f"  {'코인':<10}{'n':>5}  {'WR':>6}  {'PF':>6}  {'avg':>8}  {'sum':>8}")
    for coin, s in items[:top_n]:
        pf_s = fmt_pf(s["pf"])
        print(f"  {coin:<10}{s['n']:>5}  {s['wr']:>5.1f}%  {pf_s:>6}  "
              f"{s['avg_pnl']:>+7.2f}%  {s['sum_pnl']:>+7.2f}%")
    print()

    losers = [it for it in items if it[1]["sum_pnl"] < 0]
    losers.sort(key=lambda x: x[1]["sum_pnl"])
    if losers:
        print(f"{BOLD}{RED}[손실 기여 큰 코인 — 하위 5]{RESET}")
        print(f"  {'코인':<10}{'n':>5}  {'WR':>6}  {'PF':>6}  {'avg':>8}  {'sum':>8}")
        for coin, s in losers[:5]:
            pf_s = fmt_pf(s["pf"])
            print(f"  {coin:<10}{s['n']:>5}  {s['wr']:>5.1f}%  {pf_s:>6}  "
                  f"{s['avg_pnl']:>+7.2f}%  {s['sum_pnl']:>+7.2f}%")
        print()


def hour_of_day_breakdown(rows: list):
    by_hour = defaultdict(list)
    parsed_any = False
    for r in rows:
        t = r["time"]
        if not t:
            continue
        h = None
        for sep in (" ", "T"):
            if sep in t:
                parts = t.split(sep)
                if len(parts) >= 2 and ":" in parts[1]:
                    try:
                        h = int(parts[1].split(":")[0])
                        break
                    except ValueError:
                        pass
        if h is None:
            try:
                h = int(t.split(":")[0])
            except (ValueError, IndexError):
                pass
        if h is None:
            continue
        parsed_any = True
        by_hour[h].append(r)

    if not parsed_any:
        return

    print(f"{BOLD}{BLUE}[시간대별 (KST 추정)]{RESET}")
    print(f"  {'hour':>4}  {'n':>5}  {'WR':>6}  {'PF':>6}  {'avg':>8}")
    for h in sorted(by_hour.keys()):
        s = compute_stats(by_hour[h])
        if s["n"] < 5:
            continue
        pf_s = fmt_pf(s["pf"])
        print(f"  {h:>4d}  {s['n']:>5}  {s['wr']:>5.1f}%  {pf_s:>6}  {s['avg_pnl']:>+7.2f}%")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────
def expand_paths(patterns):
    out = []
    for p in patterns:
        if any(c in p for c in "*?[]"):
            matched = sorted(glob.glob(p))
            if not matched:
                print(f"{YELLOW}[경고] glob 매칭 결과 없음: {p}{RESET}", file=sys.stderr)
            out.extend(matched)
        else:
            out.append(p)
    return out


def main():
    parser = argparse.ArgumentParser(
        description="크립토 봇 CSV 누적 데이터에 대한 필터 백테스트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="+",
                        help="CSV 파일 경로 또는 glob 패턴")
    parser.add_argument("--clm", action="store_true",
                        help="CLM 전략용 필터 세트 사용")
    parser.add_argument("--no-color", action="store_true",
                        help="ANSI 컬러 비활성화")
    args = parser.parse_args()

    if args.no_color:
        g = globals()
        for k in ("RESET","BOLD","DIM","GREEN","RED","YELLOW","CYAN","MAGENTA","BLUE"):
            g[k] = ""

    paths = expand_paths(args.paths)
    if not paths:
        print(f"{RED}[에러] 처리할 CSV 파일이 없습니다.{RESET}", file=sys.stderr)
        sys.exit(1)

    mode = "CLM" if args.clm else "MOMENTUM v2.1"
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}백테스트 모드: {CYAN}{mode}{RESET}")
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"입력 파일 {len(paths)} 개:")
    for p in paths:
        print(f"  - {p}")
    print()

    rows, file_stats = load_csvs(paths, is_clm=args.clm)
    if not rows:
        print(f"{RED}[에러] 유효한 행이 0 개입니다.{RESET}", file=sys.stderr)
        sys.exit(1)

    print(f"{DIM}파일별 행 수:{RESET}")
    for fn, n in file_stats:
        print(f"  {DIM}{fn}: {n} 행{RESET}")
    print(f"{DIM}총 합산: {len(rows)} 행{RESET}\n")

    # ─── Baseline ─────────────────────────────────────────────────────────
    baseline = compute_stats(rows)
    print_stats("Baseline", baseline, color=CYAN)
    print()

    # ─── 필터 평가 ────────────────────────────────────────────────────────
    filters = build_clm_filters() if args.clm else build_momentum_filters()
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}개별 필터 평가 ({len(filters)} 개){RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    results = []
    for name, pred in filters:
        res = evaluate_filter(name, pred, rows, baseline)
        results.append(res)
        print_filter_result(res)

    # ─── 랭킹: ΔPF 내림차순 ────────────────────────────────────────────
    rankable = [
        r for r in results
        if not r["skip"]
        and r["n_keep"] >= MIN_N_REPORT
        and r["d_pf"] is not None
    ]
    rankable.sort(key=lambda r: r["d_pf"], reverse=True)

    print(f"{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}TOP 10 필터 (ΔPF 내림차순){RESET}")
    print(f"{BOLD}{'='*70}{RESET}")
    if not rankable:
        print(f"{YELLOW}랭킹할 필터가 없습니다 (모두 n<{MIN_N_REPORT} 또는 PF=inf).{RESET}\n")
    else:
        for i, r in enumerate(rankable[:10], 1):
            s = r["stats"]
            tag = f" {YELLOW}[low conf]{RESET}" if r["n_keep"] < LOW_CONF_N else ""
            pf_arrow = f"{fmt_pf(baseline['pf'])} -> {fmt_pf(s['pf'])}"
            d_pf_s = color_delta(r["d_pf"], "D{:+.2f}")
            print(f"  #{i:<2} {r['name']:<28} PF {pf_arrow:<14} "
                  f"({d_pf_s}, n={r['n_keep']}/{len(rows)}){tag}")
        print()

    if len(rankable) > 5:
        print(f"{BOLD}{RED}BOTTOM 5 (PF 악화){RESET}")
        for r in rankable[-5:]:
            s = r["stats"]
            pf_arrow = f"{fmt_pf(baseline['pf'])} -> {fmt_pf(s['pf'])}"
            d_pf_s = color_delta(r["d_pf"], "D{:+.2f}")
            print(f"  {r['name']:<28} PF {pf_arrow:<14} "
                  f"({d_pf_s}, n={r['n_keep']}/{len(rows)})")
        print()

    # ─── 조합 테스트 ──────────────────────────────────────────────────────
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}조합 테스트 — 상위 필터 AND 결합{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    top3 = rankable[:3]
    if len(top3) >= 2:
        pred_map = {name: pred for name, pred in filters}

        combo2_names = [r["name"] for r in top3[:2]]
        def combo2(r):
            return all(pred_map[n](r) for n in combo2_names)
        kept2 = [r for r in rows if combo2(r)]
        s2 = compute_stats(kept2)
        print(f"{BOLD}조합 [{' AND '.join(combo2_names)}]{RESET}")
        if s2["n"] < MIN_N_REPORT:
            print(f"  {YELLOW}잔여 {s2['n']} < {MIN_N_REPORT} — 의미 없음{RESET}\n")
        else:
            d_pf = (s2["pf"] - baseline["pf"]) if s2["pf"] != float("inf") else None
            d_wr = s2["wr"] - baseline["wr"]
            tag = f" {YELLOW}[low conf]{RESET}" if s2["n"] < LOW_CONF_N else ""
            print(f"  Kept: {s2['n']}/{len(rows)}{tag}")
            print(f"  WR {s2['wr']:.1f}% ({color_delta(d_wr, '{:+.1f}%p')}), "
                  f"PF {fmt_pf(s2['pf'])} ({color_delta(d_pf, '{:+.2f}') if d_pf is not None else 'n/a'}), "
                  f"avg {s2['avg_pnl']:+.2f}%\n")

    if len(top3) >= 3:
        combo3_names = [r["name"] for r in top3]
        pred_map = {name: pred for name, pred in filters}
        def combo3(r):
            return all(pred_map[n](r) for n in combo3_names)
        kept3 = [r for r in rows if combo3(r)]
        s3 = compute_stats(kept3)
        print(f"{BOLD}조합 [{' AND '.join(combo3_names)}]{RESET}")
        if s3["n"] < MIN_N_REPORT:
            print(f"  {YELLOW}잔여 {s3['n']} < {MIN_N_REPORT} — 의미 없음{RESET}\n")
        else:
            d_pf = (s3["pf"] - baseline["pf"]) if s3["pf"] != float("inf") else None
            d_wr = s3["wr"] - baseline["wr"]
            tag = f" {YELLOW}[low conf]{RESET}" if s3["n"] < LOW_CONF_N else ""
            print(f"  Kept: {s3['n']}/{len(rows)}{tag}")
            print(f"  WR {s3['wr']:.1f}% ({color_delta(d_wr, '{:+.1f}%p')}), "
                  f"PF {fmt_pf(s3['pf'])} ({color_delta(d_pf, '{:+.2f}') if d_pf is not None else 'n/a'}), "
                  f"avg {s3['avg_pnl']:+.2f}%\n")

    # ─── 브레이크다운 ────────────────────────────────────────────────────
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}브레이크다운 (Baseline 데이터 전체 기준){RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")
    per_coin_breakdown(rows, top_n=8)
    hour_of_day_breakdown(rows)

    if rankable:
        best = rankable[0]
        if best["n_keep"] >= MIN_N_REPORT:
            print(f"{BOLD}{'='*70}{RESET}")
            print(f"{BOLD}참고 — 최상위 필터 '{best['name']}' 적용 후 코인별 분포{RESET}")
            print(f"{BOLD}{'='*70}{RESET}\n")
            per_coin_breakdown(best["kept"], top_n=8)

    print(f"{DIM}--- 끝 ---{RESET}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}사용자 중단{RESET}", file=sys.stderr)
        sys.exit(130)
