# -*- coding: utf-8 -*-
"""
loss_attribution — 손실 원인 자동 분류 (advisor 3자 수렴 지시).

배경 (2026-08-01):
  "쌓이는 데이터로 손실방지 기여하는 걸 찾자" 지시 · advisor 1 정정 반영:
  "SL 7건을 A가 구했을 거"는 arm180 무시한 착시 · 거래별 counterfactual 필요.

목적:
  per-trade 데이터에서 손실 원인을 규칙 기반으로 자동 분류.
  어느 lever (A/A2/실행품질) 가 어느 유형에 실제 개입 가능한지 판정 근거.

⚠ 등급 규율:
  이 스크립트는 OBSERVED / CLASSIFIED 결과만 산출.
  실제 회수 확정은 counterfactual replay (live_cohort_resim.py) 로만.

Loss category (규칙 기반):
  1. BASE_SL_BEFORE_ARM  : hold < 180s + exit_reason=손절SL → A arm 이전 종료, A 미개입 불가
  2. PROFIT_GIVEBACK     : mfe ≥ 0.5% + pnl < 0 (또는 pnl < 0.5*mfe) → 수익 반납, A 트레일 후보
  3. EARLY_DUMP          : mfe < 0.2% + hold < 120s + pnl < 0 → 초기 하락, A2 진입 필터 후보
  4. TIMEOUT_DECAY       : exit_reason=AT타임아웃 + pnl < 0 → 시간 소진 후 손실, A hold 확장 후보
  5. BREAKEVEN_DAMAGE    : exit_reason=AT본절 + pnl < 0 → 본절 이동 후 손실, A 본절 OFF 후보
  6. FAR_STOP            : pnl < -1.5% → far stop 발동, 큰 손실
  7. UNCLASSIFIED        : 위 어느 것도 아님

Lever 매칭:
  - A (클린 트레일 arm180/bp30/hold240 · BE OFF · SL OFF)
    → PROFIT_GIVEBACK, BREAKEVEN_DAMAGE 커버
    → BASE_SL_BEFORE_ARM 은 A arm 이전이라 커버 불가
    → EARLY_DUMP 는 A2 담당
  - A2 (vr5-cap 진입 필터)
    → EARLY_DUMP 진입 자체 회피
    → 나머지는 A 담당
  - 실행품질 (latency/slippage 개선)
    → 유형 무관 · 전체 진입가 개선

입력 CSV (per-trade summary):
  필수: pnl (decimal, e.g. -0.017 for -1.7%), mfe, mae, hold, exit_reason
  권장: market, entry_ts_utc, vr5 (A2 실행 시뮬용)

사용:
  python research/loss_attribution.py trades.csv [--arm-sec 180] [--trail-bp 30]
"""
import sys, csv, argparse
from datetime import datetime
from collections import defaultdict


def classify_loss(trade, arm_sec=180, trail_bp=30):
    """단일 거래 손실 유형 분류. pnl >= 0 이면 WIN."""
    pnl = trade.get("pnl", 0)
    if pnl >= 0:
        return "WIN"
    mfe = trade.get("mfe", 0) or 0
    mae = trade.get("mae", 0) or 0
    hold = trade.get("hold", 0) or 0
    exit_reason = (trade.get("exit_reason") or "").strip()
    # 우선순위 순서 중요 (specific → general)
    # 1. 본절 이동 후 손실 (advisor 확정: capture 킬러)
    if exit_reason == "AT본절":
        return "BREAKEVEN_DAMAGE"
    # 2. arm 이전 SL (A 미개입 불가)
    if exit_reason == "손절SL" and hold < arm_sec:
        return "BASE_SL_BEFORE_ARM"
    # 3. 시간 소진 후 손실
    if exit_reason == "AT타임아웃":
        return "TIMEOUT_DECAY"
    # 4. far stop (큰 손실)
    if pnl < -0.015:  # -1.5%
        return "FAR_STOP"
    # 5. 수익 반납 (mfe 있었으나 반납)
    if mfe >= 0.005 and pnl < mfe * 0.3:  # mfe 30% 도 못 지킴
        return "PROFIT_GIVEBACK"
    # 6. 초기 덤프 (mfe 작음 + 짧은 hold)
    if mfe < 0.002 and hold < 120:
        return "EARLY_DUMP"
    return "UNCLASSIFIED"


def lever_can_help(category, has_a2=True):
    """각 카테고리를 어느 lever 가 실제 개입 가능한지."""
    mapping = {
        "BREAKEVEN_DAMAGE": ["A (본절 OFF)"],
        "PROFIT_GIVEBACK": ["A (클린 트레일)"],
        "BASE_SL_BEFORE_ARM": ["실행품질 (진입 개선)"],  # A arm 이전이라 A 커버 불가
        "TIMEOUT_DECAY": ["A hold 확장 검토"],
        "EARLY_DUMP": ["A2 (vr5-cap 진입 필터)"] if has_a2 else [],
        "FAR_STOP": ["A far -3% 백스톱 유지"],
        "UNCLASSIFIED": [],
        "WIN": [],
    }
    return mapping.get(category, [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="per-trade CSV (pnl, mfe, mae, hold, exit_reason)")
    ap.add_argument("--arm-sec", type=int, default=180, dest="arm_sec",
                    help="A 트레일 arm 초 (기본 180 · A eligible 판정)")
    ap.add_argument("--trail-bp", type=int, default=30, dest="trail_bp",
                    help="A 트레일 폭 bp (기본 30 · 0.3%)")
    args = ap.parse_args()

    # CSV 로드
    trades = []
    with open(args.csv) as f:
        for r in csv.DictReader(f):
            trade = {}
            for k, v in r.items():
                if not v or not v.strip():
                    continue
                if k in ("market", "entry_ts_utc", "signal_id", "exit_reason"):
                    trade[k] = v.strip()
                else:
                    try:
                        trade[k] = float(v)
                    except (ValueError, TypeError):
                        pass
            trades.append(trade)

    if not trades:
        print("입력 거래 0건"); return

    print(f"입력 거래 {len(trades)}건")
    print(f"A 파라미터: arm={args.arm_sec}s / trail={args.trail_bp}bp / hold=240s\n")

    # 분류
    cat_counts = defaultdict(list)
    for t in trades:
        cat = classify_loss(t, arm_sec=args.arm_sec, trail_bp=args.trail_bp)
        cat_counts[cat].append(t)

    # 전체 요약
    total = len(trades)
    wins = len(cat_counts["WIN"])
    losses = total - wins
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    loss_pnl = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) < 0)

    print(f"=== 전체 요약 ===")
    print(f"  n={total}  wins={wins} ({wins/total*100:.0f}%)  losses={losses}")
    print(f"  total PnL: {total_pnl*100:+.2f}%")
    print(f"  loss PnL total: {loss_pnl*100:+.2f}%")

    # 카테고리별 breakdown
    print(f"\n=== 손실 카테고리 분류 ===")
    _order = ["BREAKEVEN_DAMAGE", "PROFIT_GIVEBACK", "BASE_SL_BEFORE_ARM",
              "TIMEOUT_DECAY", "EARLY_DUMP", "FAR_STOP", "UNCLASSIFIED"]
    for cat in _order:
        items = cat_counts.get(cat, [])
        if not items:
            continue
        cat_pnl = sum(t.get("pnl", 0) for t in items)
        cat_avg = cat_pnl / len(items) if items else 0
        share = cat_pnl / loss_pnl * 100 if loss_pnl < 0 else 0
        levers = lever_can_help(cat)
        levers_str = " / ".join(levers) if levers else "-"
        print(f"  {cat:22s} n={len(items):3d} "
              f"avg={cat_avg*100:+.2f}% total={cat_pnl*100:+.2f}%p "
              f"(loss 지분 {share:.0f}%) → {levers_str}")

    # Lever 별 회수 가능성 (per-category 합산)
    print(f"\n=== Lever 별 개입 가능 손실 지분 (OBSERVED) ===")
    print(f"  ⚠ 상한 표시 · 실 회수는 counterfactual (live_cohort_resim.py) 로만 확정")
    lever_shares = defaultdict(float)
    for cat, items in cat_counts.items():
        if cat == "WIN" or cat == "UNCLASSIFIED":
            continue
        cat_pnl = sum(t.get("pnl", 0) for t in items)
        levers = lever_can_help(cat)
        for lv in levers:
            # 각 lever 에 categorical loss share 할당 (겹치면 중복 계상)
            lever_shares[lv] += cat_pnl
    for lv, share_pnl in sorted(lever_shares.items(), key=lambda x: x[1]):
        share_pct = share_pnl / loss_pnl * 100 if loss_pnl < 0 else 0
        print(f"  {lv:35s} 최대 회수 상한 {share_pnl*100:+.2f}%p ({share_pct:.0f}% of loss)")

    # BASE_SL_BEFORE_ARM 특별 강조 (advisor 정정 반영)
    if _pre_arm := cat_counts.get("BASE_SL_BEFORE_ARM"):
        print(f"\n⚠ BASE_SL_BEFORE_ARM {len(_pre_arm)}건: A arm={args.arm_sec}s 이전 종료 "
              f"→ A 트레일 미무장 · 이 손실은 A 로 못 잡음 (advisor 정정)")
        avg_hold = sum(t.get("hold", 0) for t in _pre_arm) / len(_pre_arm)
        avg_pnl = sum(t.get("pnl", 0) for t in _pre_arm) / len(_pre_arm)
        print(f"  평균 hold: {avg_hold:.0f}s · 평균 pnl: {avg_pnl*100:+.2f}%")
        print(f"  이 유형은 A2 (진입 필터) 또는 실행품질 개선 담당")

    print(f"\n=== 다음 단계 ===")
    print(f"  1. live_cohort_resim.py 로 CONTROL vs A vs A×A2 per-trade counterfactual")
    print(f"  2. 각 손실 카테고리별 실 회수 확인 (상한 아니라 실측)")
    print(f"  3. 회수율 낮은 카테고리는 다른 lever 후보 발굴")


if __name__ == "__main__":
    main()
