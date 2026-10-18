#!/usr/bin/env python3
"""Read-only cost-aware train/holdout evaluation of micro_edge_probe replay outputs.

Opportunities (including unfilled/expired) set thresholds and completion rates.
Only information known at decision-time selects events. Exit returns are labels.
This is a hypothetical taker execution benchmark, NOT an account return or LIVE rule.
"""
import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

PRIMARY_HORIZON = 10
QUANTILES = (0.80, 0.90, 0.95)
EXTRA_COST_PCT = 0.05  # incremental fee/slippage stress, in percentage points


def _finite(v, name):
    try:
        result = float(v)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name}: {v!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"nonfinite {name}")
    return result


def _load_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _quantile(vals, q):
    vals = sorted(vals)
    if not vals:
        raise ValueError("empty threshold population")
    idx = (len(vals) - 1) * q
    lo = int(idx)
    hi = min(len(vals) - 1, lo + 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (idx - lo)


def load(opportunities, fills):
    opp = {}
    for row in _load_csv(opportunities):
        ident = row.get("event_id")
        if not ident or ident in opp:
            raise ValueError("missing or duplicate opportunity event_id")
        day = row.get("date", "")
        if len(day) != 10 or day[4] != "-" or day[7] != "-":
            raise ValueError("invalid opportunity date")
        opp[ident] = {
            "id": ident,
            "date": day,
            "market": row["market"],
            "decision_recv_ms": int(row["decision_recv_ms"]),
            "buy_imbalance": _finite(row["buy_imbalance"], "buy_imbalance"),
        }
        if not -1 <= opp[ident]["buy_imbalance"] <= 1:
            raise ValueError("imbalance outside [-1,1]")
    if not opp:
        raise ValueError("no opportunities")
    outcomes = {}
    for row in _load_csv(fills):
        ident = row.get("event_id")
        if ident not in opp:
            raise ValueError("fill without recorded opportunity")
        h = int(row["horizon_s"])
        key = (ident, h)
        if key in outcomes:
            raise ValueError("duplicate fill for event and horizon")
        if row.get("market") != opp[ident]["market"] or row.get("date") != opp[ident]["date"]:
            raise ValueError("outcome opportunity metadata conflict")
        val = _finite(row["net_return_frac"], "net_return_frac")
        if val < -1:
            raise ValueError("impossible net return")
        if int(row["entry_fill_ms"]) < opp[ident]["decision_recv_ms"]:
            raise ValueError("future/early fill used for decision")
        if int(row["exit_fill_ms"]) < int(row["entry_fill_ms"]) + h * 1000:
            raise ValueError("exit before prescribed hold time")
        outcomes[key] = val * 100  # fractional net return -> percent, once only
    return opp, outcomes


def _stats(ids, opp, outcomes, h):
    values = [(i, outcomes[(i, h)]) for i in ids if (i, h) in outcomes]
    vals = [v for _, v in values]
    n = len(vals)
    if not n:
        return {"opportunities": len(ids), "fills": 0, "fill_coverage": 0,
                "mean_net_pct": None, "mean_stress_pct": None}
    mean = statistics.mean(vals)
    v_sorted = sorted(vals, reverse=True)
    day_vals, market_vals = defaultdict(list), defaultdict(list)
    for i, v in values:
        day_vals[opp[i]["date"]].append(v)
        market_vals[opp[i]["market"]].append(v)
    good = sum(max(0, v) for v in vals)
    coin_profit = {k: sum(max(0, x) for x in xs) for k, xs in market_vals.items()}
    return {
        "opportunities": len(ids), "fills": n, "fill_coverage": n / len(ids) if ids else 0,
        "mean_net_pct": mean, "mean_stress_pct": mean - EXTRA_COST_PCT,
        "median_net_pct": statistics.median(vals),
        "positive_rate": sum(v > 0 for v in vals) / n,
        "p10_net_pct": sorted(vals)[int((n - 1) * .1)],
        "worst_net_pct": min(vals),
        "mean_without_top1_pct": statistics.mean(v_sorted[1:]) if n > 1 else None,
        "mean_without_top5_pct": statistics.mean(v_sorted[5:]) if n > 5 else None,
        "top_coin_positive_profit_share": max(coin_profit.values()) / good if good else None,
        "daily_mean_net_pct": {k: statistics.mean(v) for k, v in sorted(day_vals.items())},
        "markets": len(market_vals),
    }


def evaluate(opportunities, outcomes, *, train_fraction=.7, min_train=30,
             min_test=100, min_coverage=.85, test_min_days=3):
    days = sorted({o["date"] for o in opportunities.values()})
    if len(days) < 4:
        raise ValueError("need at least 4 separate recorded dates for time split")
    cut = max(1, min(len(days) - 1, int(len(days) * train_fraction)))
    training_days, test_days = set(days[:cut]), set(days[cut:])
    train = {k for k, v in opportunities.items() if v["date"] in training_days}
    test = {k for k, v in opportunities.items() if v["date"] in test_days}
    # Train-only thresholds use all eligible opportunities, NEVER outcome survivors.
    cuts = {str(int(q * 100)): _quantile([opportunities[i]["buy_imbalance"] for i in train], q)
            for q in QUANTILES}
    cohorts = {"all": lambda x: True}
    for p, threshold in cuts.items():
        cohorts["q" + p] = (lambda x, th=threshold: x >= th)

    def group(index, predicate):
        return [i for i in index if predicate(opportunities[i]["buy_imbalance"])]

    train_results = {k: _stats(group(train, f), opportunities, outcomes, PRIMARY_HORIZON)
                     for k, f in cohorts.items()}
    # Select ONCE using training data only. Baseline is not a tradable candidate.
    eligible = [(key, x) for key, x in train_results.items()
                if key != "all" and x["fills"] >= min_train
                and x["fill_coverage"] >= min_coverage
                and x["mean_stress_pct"] is not None and x["mean_stress_pct"] > 0
                and x["mean_without_top5_pct"] is not None and x["mean_without_top5_pct"] > 0]
    selected = max(eligible, key=lambda kv: kv[1]["mean_stress_pct"])[0] if eligible else None
    test_baseline = _stats(group(test, cohorts["all"]), opportunities, outcomes, PRIMARY_HORIZON)
    test_selected = _stats(group(test, cohorts[selected]), opportunities, outcomes, PRIMARY_HORIZON) if selected else None
    verdict = "NO_TRAIN_CANDIDATE" if selected is None else "INSUFFICIENT"
    if selected:
        s = test_selected
        if (s["fills"] >= min_test and s["fill_coverage"] >= min_coverage
                and len(s.get("daily_mean_net_pct", {})) >= test_min_days):
            days_positive = sum(v > 0 for v in s["daily_mean_net_pct"].values())
            robust = (s["mean_stress_pct"] > 0 and s["mean_without_top5_pct"] > 0
                      and s["positive_rate"] > .5
                      and s["top_coin_positive_profit_share"] is not None
                      and s["top_coin_positive_profit_share"] <= .5
                      and days_positive > len(s["daily_mean_net_pct"]) / 2)
            verdict = "HISTORICAL_CANDIDATE" if robust else "HISTORICAL_REJECTED"
    return {
        "status": verdict,
        "warnings": [
            "NOT real LIVE fills, account return, or guaranteed profit.",
            "One historical split only; raw dates may have been viewed before. Historical candidate needs new untouched forward data.",
            "Only the TRAIN set chose the threshold; test performance must not be used to retune.",
            "Net return already includes spread via depth fills and both fees. Stress is only ADDITIONAL cost.",
            "Missing/partial orderbook frames can bias completed-fill results; inspect coverage and quality report.",
            "No opportunity cost of capital, simultaneous inventory, or portfolio risk simulated.",
        ],
        "primary_horizon_seconds": PRIMARY_HORIZON,
        "additional_cost_stress_pct_points": EXTRA_COST_PCT,
        "train_dates": sorted(training_days), "test_dates": sorted(test_days),
        "n_train_opportunities": len(train), "n_test_opportunities": len(test),
        "train_only_quantile_thresholds": cuts,
        "train_cohort_metrics": train_results,
        "selected_by_train": selected,
        "test_all_events_baseline": test_baseline,
        "test_selected_metrics": test_selected,
        "do_not_rescore_oos": True,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--opportunities", required=True)
    p.add_argument("--events", required=True)
    p.add_argument("--output", default="/tmp/net-edge-oos.json")
    p.add_argument("--train-fraction", type=float, default=.7)
    args = p.parse_args()
    if not .1 <= args.train_fraction <= .9:
        p.error("train-fraction must be between .1 and .9")
    opp, fills = load(args.opportunities, args.events)
    report = evaluate(opp, fills, train_fraction=args.train_fraction)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    s = report["test_selected_metrics"]
    print("STATUS", report["status"], "TRAIN_SELECTED", report["selected_by_train"])
    print("TRAIN_ONLY_THRESHOLDS", report["train_only_quantile_thresholds"])
    print("TEST_BASELINE", report["test_all_events_baseline"])
    print("TEST_SELECTED", s if s is not None else "NO_TRAIN_CANDIDATE")
    print("REPORT", str(dest))


if __name__ == "__main__":
    main()
