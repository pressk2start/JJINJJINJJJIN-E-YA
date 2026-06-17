#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLM (과열감지) Phase 2 - Label Collection Module
=================================================

목적: 각 포지션에 dd_30s / dd_60s / MFE_30s / MFE_60s 추적 후
      사후(post-hoc) A/B/C cohort 분류 -> clm_labels.csv 저장
      Phase 3 A-predictor 학습용 데이터셋 축적

Phase 흐름:
    Phase 1: RSI 과열 감지 -> 진입/청산 (이미 운영 중)
    Phase 2: 라벨 수집 (이 모듈) -> 데이터셋 축적
    Phase 3: 수집된 라벨로 A-predictor 학습 (다음 단계)

Critical Notes:
    1. Phase 2는 진입 결정에 영향 주지 않음 (관측만 함)
    2. A/B/C 라벨은 사후(POST-HOC)에만 부여
    3. Phase 3 학습 모델은 이 모듈에 포함되지 않음
    4. 30s/60s 윈도우는 외부 시스템 기준 권장값
    5. 임계값 (0.001, 0.002, 0.0005, 0.004) 은 educated guess, 추후 조정 가능
"""

from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional


# =============================================================================
# 1. Cohort 분류 규칙 (사후 라벨링)
# =============================================================================

DD_30S_A_MAX = 0.001       # A: 30s 내 drawdown <= 0.1%
MFE_60S_A_MIN = 0.002      # A: 60s MFE >= 0.2%
DD_30S_C_MIN = 0.004       # C: 30s 내 drawdown >= 0.4%
MFE_60S_C_MAX = 0.0005     # C: 60s MFE <= 0.05%


def assign_cohort(trade: Dict[str, Any]) -> str:
    """A/B/C 분류 (사후 라벨링).

    A: 진입 후 30s 안에 dd 적고 MFE 60s 양호 + 최종 양수
    C: 진입 후 30s 안에 dd 크거나 60s MFE 미미
    B: 나머지
    """
    if (trade['dd_30s'] <= DD_30S_A_MAX and
        trade['mfe_60s'] >= MFE_60S_A_MIN and
        trade['net_pnl'] > 0):
        return 'A'
    if (trade['dd_30s'] >= DD_30S_C_MIN or
        trade['mfe_60s'] <= MFE_60S_C_MAX):
        return 'C'
    return 'B'


# =============================================================================
# 2. 포지션 추적 헬퍼 (manage_positions 안에서 호출)
# =============================================================================

def update_window_tracking(pos: Dict[str, Any], current_price: float, now_ts: float) -> None:
    """포지션의 30s/60s 윈도우 내 max/min 가격 추적.

    manage_positions 루프에서 매 tick 마다 호출.
    포지션 dict에 다음 키를 추가/갱신:
        max_30s, min_30s, mfe_30s, dd_30s, tracking_30s
        max_60s, min_60s, mfe_60s, dd_60s, tracking_60s
    """
    hold_sec = now_ts - pos["entry_time"]
    entry_price = pos["entry_price"]

    # ---- 30초 윈도우 ----
    if hold_sec <= 30:
        if "max_30s" not in pos:
            pos["max_30s"] = current_price
            pos["min_30s"] = current_price
        else:
            pos["max_30s"] = max(pos["max_30s"], current_price)
            pos["min_30s"] = min(pos["min_30s"], current_price)
    elif "tracking_30s" not in pos:
        pos["tracking_30s"] = True
        pos["mfe_30s"] = (pos.get("max_30s", entry_price) - entry_price) / entry_price
        pos["dd_30s"] = (entry_price - pos.get("min_30s", entry_price)) / entry_price

    # ---- 60초 윈도우 ----
    if hold_sec <= 60:
        if "max_60s" not in pos:
            pos["max_60s"] = current_price
            pos["min_60s"] = current_price
        else:
            pos["max_60s"] = max(pos["max_60s"], current_price)
            pos["min_60s"] = min(pos["min_60s"], current_price)
    elif "tracking_60s" not in pos:
        pos["tracking_60s"] = True
        pos["mfe_60s"] = (pos.get("max_60s", entry_price) - entry_price) / entry_price
        pos["dd_60s"] = (entry_price - pos.get("min_60s", entry_price)) / entry_price


def finalize_window_metrics(pos: Dict[str, Any]) -> None:
    """청산 시점에 아직 윈도우가 안 닫혔으면 보유시간 기준으로 강제 마감."""
    entry_price = pos["entry_price"]
    if "mfe_30s" not in pos:
        pos["mfe_30s"] = (pos.get("max_30s", entry_price) - entry_price) / entry_price
        pos["dd_30s"] = (entry_price - pos.get("min_30s", entry_price)) / entry_price
    if "mfe_60s" not in pos:
        pos["mfe_60s"] = (pos.get("max_60s", entry_price) - entry_price) / entry_price
        pos["dd_60s"] = (entry_price - pos.get("min_60s", entry_price)) / entry_price


# =============================================================================
# 3. CSV 저장 (clm_labels.csv)
# =============================================================================

CSV_FIELDS: List[str] = [
    "time", "market", "entry_price", "exit_price", "net_pnl", "hold_sec", "reason",
    "rsi_5m", "rsi_15m", "ema_spread_pct",
    "dd_30s", "dd_60s", "mfe_30s", "mfe_60s",
    "peak_pnl", "mae", "cohort",
]


def append_label_row(csv_path: str, trade: Dict[str, Any]) -> None:
    """청산된 trade dict 를 clm_labels.csv 에 한 줄 append."""
    trade = dict(trade)
    trade["cohort"] = assign_cohort(trade)

    write_header = not os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow({k: trade.get(k, "") for k in CSV_FIELDS})


# =============================================================================
# 4. 라벨 집계 / 리포팅
# =============================================================================

def summarize_labels(label_csv_path: str) -> Dict[str, Dict[str, float]]:
    """A/B/C 코호트별 통계 출력 + dict 반환."""
    buckets: Dict[str, List[float]] = {"A": [], "B": [], "C": []}
    if not os.path.exists(label_csv_path):
        print(f"[summarize_labels] file not found: {label_csv_path}")
        return {}

    with open(label_csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cohort = row.get("cohort", "")
            if cohort not in buckets:
                continue
            try:
                buckets[cohort].append(float(row["net_pnl"]))
            except (ValueError, KeyError):
                continue

    summary: Dict[str, Dict[str, float]] = {}
    for cohort in ("A", "B", "C"):
        pnls = buckets[cohort]
        n = len(pnls)
        if n == 0:
            summary[cohort] = {"n": 0, "wr": 0.0, "avg": 0.0}
            print(f"Cohort {cohort}: n=0")
            continue
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100.0
        avg = sum(pnls) / n * 100.0
        summary[cohort] = {"n": n, "wr": wr, "avg": avg}
        sign = "+" if avg >= 0 else ""
        print(f"Cohort {cohort}: n={n}, WR {wr:.1f}%, avg {sign}{avg:.2f}%")

    if summary.get("A", {}).get("n", 0) and summary.get("C", {}).get("n", 0):
        lift = summary["A"]["avg"] - summary["C"]["avg"]
        sign = "+" if lift >= 0 else ""
        print(f"Lift A vs C: {sign}{lift:.2f}%")
    return summary


# =============================================================================
# 5. 통합 가이드 (bot 유지보수자용)
# =============================================================================
#
# manage_positions() 안에 아래 패턴으로 삽입:
#
# ──────────────────────────────────────────────────────────────────────
# from clm_phase2_labels import (
#     update_window_tracking, finalize_window_metrics, append_label_row,
# )
#
# def manage_positions(...):
#     for market, pos in list(positions.items()):
#         current_price = get_current_price(market)
#         now_ts = time.time()
#
#         # [Phase 2] 30s / 60s 윈도우 추적
#         update_window_tracking(pos, current_price, now_ts)
#
#         # ... 기존 entry/exit 로직 그대로 ...
#         if should_exit(pos, current_price):
#             # [Phase 2] 청산 직전 윈도우 강제 마감
#             finalize_window_metrics(pos)
#
#             trade = {
#                 "time": now_ts,
#                 "market": market,
#                 "entry_price": pos["entry_price"],
#                 "exit_price": current_price,
#                 "net_pnl": (current_price - pos["entry_price"]) / pos["entry_price"],
#                 "hold_sec": now_ts - pos["entry_time"],
#                 "reason": exit_reason,
#                 "rsi_5m": pos.get("rsi_5m"),
#                 "rsi_15m": pos.get("rsi_15m"),
#                 "ema_spread_pct": pos.get("ema_spread_pct"),
#                 "dd_30s": pos["dd_30s"],
#                 "dd_60s": pos["dd_60s"],
#                 "mfe_30s": pos["mfe_30s"],
#                 "mfe_60s": pos["mfe_60s"],
#                 "peak_pnl": pos.get("peak_pnl", 0),
#                 "mae": pos.get("mae", 0),
#             }
#             # [Phase 2] 라벨 한 줄 저장 (cohort 자동 부여)
#             append_label_row("clm_labels.csv", trade)
#
#             # ... 기존 청산 처리 ...
# ──────────────────────────────────────────────────────────────────────
#
# 중요: 위 코드는 모두 "관측/기록"만 함. 진입/청산 결정에 절대 끼어들지 않음.


# =============================================================================
# 6. Self-test
# =============================================================================

def _mock_trades() -> List[Dict[str, Any]]:
    return [
        {"dd_30s": 0.0005, "mfe_60s": 0.0030, "net_pnl": 0.0025},   # A
        {"dd_30s": 0.0050, "mfe_60s": 0.0040, "net_pnl": -0.0020},  # C (dd)
        {"dd_30s": 0.0010, "mfe_60s": 0.0002, "net_pnl": -0.0005},  # C (mfe)
        {"dd_30s": 0.0020, "mfe_60s": 0.0015, "net_pnl": 0.0010},   # B
    ]


def _self_test() -> None:
    print("=== Phase 2 self-test ===")

    trades = _mock_trades()
    expected = ["A", "C", "C", "B"]
    for t, exp in zip(trades, expected):
        got = assign_cohort(t)
        assert got == exp, f"cohort mismatch: got {got}, expected {exp} for {t}"
        print(f"  trade {t} -> {got} (OK)")

    print("\nCSV header:")
    print(",".join(CSV_FIELDS))

    demo_path = "_clm_labels_demo.csv"
    if os.path.exists(demo_path):
        os.remove(demo_path)
    base = {
        "time": 0, "market": "KRW-BTC", "entry_price": 100.0, "exit_price": 100.0,
        "hold_sec": 65, "reason": "rsi_exit",
        "rsi_5m": 75, "rsi_15m": 70, "ema_spread_pct": 0.5,
        "peak_pnl": 0.003, "mae": 0.001,
    }
    for t in trades:
        row = {**base, **t}
        append_label_row(demo_path, row)

    print("\nsummarize_labels output:")
    summarize_labels(demo_path)

    os.remove(demo_path)
    print("\n=== self-test passed ===")


if __name__ == "__main__":
    _self_test()
