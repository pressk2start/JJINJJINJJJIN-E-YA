#!/usr/bin/env python3
"""Exploratory taker-only replay. No orders/network. Python standard library only.
All input dates are EXPLORATORY, never an untouched OOS. Not a strategy verdict.
Quotes at/after simulated order arrival; same purchased quantity liquidated.
"""
import argparse
import collections
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics


def buy(units, notional):
    remaining, quantity = notional, 0.0
    for u in sorted(units, key=lambda x: float(x["ask_price"])):
        p, size = float(u["ask_price"]), float(u["ask_size"])
        if p <= 0 or size < 0 or not math.isfinite(p + size):
            raise ValueError("invalid ask")
        spent = min(remaining, p * size)
        quantity += spent / p
        remaining -= spent
        if remaining <= notional * 1e-10:
            return quantity
    return None


def sell(units, quantity):
    remaining, proceeds = quantity, 0.0
    for u in sorted(units, key=lambda x: float(x["bid_price"]), reverse=True):
        p, size = float(u["bid_price"]), float(u["bid_size"])
        if p <= 0 or size < 0 or not math.isfinite(p + size):
            raise ValueError("invalid bid")
        volume = min(remaining, size)
        proceeds += volume * p
        remaining -= volume
        if remaining <= quantity * 1e-10:
            return proceeds
    return None


def quantile(values, q):
    s = sorted(values)
    if not s:
        return None
    x = (len(s) - 1) * q
    lo, hi = int(x), math.ceil(x)
    return s[lo] + (s[hi] - s[lo]) * (x - lo)


def summary(rows, stress_bp):
    vals = [r["net_return_frac"] for r in rows]
    if not vals:
        return {"n": 0}
    ranked = sorted(vals, reverse=True)
    grouped = {}
    for field in ("market", "date"):
        groups = collections.defaultdict(list)
        for r in rows:
            groups[r[field]].append(r["net_return_frac"])
        grouped[field] = {k: {"n": len(v), "mean_net_pct": statistics.mean(v) * 100}
                          for k, v in sorted(groups.items())}
    positive_sum = sum(max(v, 0) for v in vals)
    return {
        "n": len(vals), "mean_net_pct": statistics.mean(vals) * 100,
        "median_net_pct": statistics.median(vals) * 100,
        "positive_rate": sum(v > 0 for v in vals) / len(vals),
        "p10_net_pct": quantile(vals, .1) * 100,
        "p90_net_pct": quantile(vals, .9) * 100,
        "worst_net_pct": min(vals) * 100,
        "mean_stress_net_pct": (statistics.mean(vals) - stress_bp / 10000) * 100,
        "mean_without_top1_pct": statistics.mean(ranked[1:]) * 100 if len(vals) > 1 else None,
        "mean_without_top5_pct": statistics.mean(ranked[5:]) * 100 if len(vals) > 5 else None,
        "top5_share_of_positive_profits": sum(max(v, 0) for v in ranked[:5]) / positive_sum
                                        if positive_sum else None,
        "by_group": grouped,
    }


def replay(args):
    files = sorted(Path(args.root).rglob("*.jsonl.gz"))
    if not files:
        raise ValueError("no .jsonl.gz files under root")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    counters = collections.Counter()
    market_counts = collections.defaultdict(collections.Counter)
    ticks, quotes, first, last_event = {}, {}, {}, {}
    next_event, pending, seen_trade = {}, {}, {}
    rows, manifest, events = [], [], {}
    last_recv, seq, session = None, None, 0
    digest = hashlib.sha256()
    latency = collections.Counter()

    def invalidate(reason):
        counters["invalidated_" + reason] += sum(len(v) for v in pending.values())
        pending.clear()
        ticks.clear()
        quotes.clear()
        first.clear()
        next_event.clear()
        seen_trade.clear()
        last_event.clear()

    for path in files:
        counters["files"] += 1
        item = {"path": str(path), "bytes": path.stat().st_size, "records": 0}
        manifest.append(item)
        with gzip.open(path, "rb") as stream:
            for raw in stream:
                digest.update(raw)
                item["records"] += 1
                try:
                    m = json.loads(raw)
                    t = int(m["recv_ts"])
                except (ValueError, KeyError, TypeError):
                    raise ValueError("invalid JSON/recv_ts in " + str(path))
                if last_recv is not None and t < last_recv:
                    raise ValueError("receive time regressed; split sessions explicitly")
                if last_recv is not None and t - last_recv > args.max_gap_ms:
                    invalidate("receive_gap")
                last_recv = t
                if "_meta" in m:
                    counters["meta_" + str(m["_meta"])] += 1
                    if m["_meta"] in ("connect", "disconnect", "recv_timeout", "disk_stop"):
                        invalidate("session_boundary")
                        session += 1
                        seq = None
                    continue
                nseq = m.get("_seq")
                if nseq is not None:
                    nseq = int(nseq)
                    if seq is not None and nseq != seq + 1:
                        invalidate("sequence_discontinuity")
                    seq = nseq
                market, kind = m.get("code"), m.get("type")
                if not market or kind not in ("trade", "orderbook"):
                    counters["unknown_record"] += 1
                    continue
                ex = int(m["timestamp"])
                if ex > t + args.clock_tolerance_ms:
                    raise ValueError("exchange timestamp ahead of receive clock")
                delay = t - ex
                latency["negative" if delay < 0 else
                        "0_100ms" if delay <= 100 else
                        "100_500ms" if delay <= 500 else "over500ms"] += 1
                market_counts[market][kind] += 1
                market_counts[market]["first_recv_ms"] = min(
                    market_counts[market].get("first_recv_ms", t), t)
                market_counts[market]["last_recv_ms"] = t
                q = ticks.setdefault(market, collections.deque())
                while q and q[0][0] < t - args.feature_window_ms:
                    q.popleft()
                if kind == "trade":
                    sid = m.get("sequential_id")
                    if sid is None or m.get("_seq_anomaly"):
                        counters["invalid_trade_id"] += 1
                        continue
                    previous = seen_trade.get(market)
                    if previous is not None and int(sid) <= previous:
                        counters["duplicate_or_backward_trade"] += 1
                        continue
                    seen_trade[market] = int(sid)
                    if m.get("ask_bid") not in ("BID", "ASK"):
                        counters["unknown_trade_side"] += 1
                        continue
                    value = float(m["trade_price"]) * float(m["trade_volume"])
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError("invalid trade price/volume")
                    q.append((t, value, m["ask_bid"] == "BID"))
                    continue
                units = m.get("orderbook_units", [])
                if not units:
                    counters["empty_book"] += 1
                    continue
                if float(m.get("level", 0)) != 0:
                    raise ValueError("aggregated orderbook level unsupported")
                if delay > args.max_quote_age_ms:
                    counters["stale_book"] += 1
                    continue
                # Arrival uses a received quote at/after target, never a future quote before target.
                active = []
                for p in pending.get(market, []):
                    target = p["entry_due_ms"] if p["quantity"] is None else p["exit_due_ms"]
                    if t < target:
                        active.append(p)
                        continue
                    if t - target > args.max_lateness_ms:
                        counters["invalidated_late_target"] += 1
                        continue
                    if p["quantity"] is None:
                        quantity = buy(units, args.notional_krw)
                        if quantity is None:
                            counters["insufficient_entry_depth"] += 1
                            continue
                        p["quantity"] = quantity
                        p["entry_fill_ms"] = t
                        p["entry_quote_exchange_ms"] = ex
                        p["exit_due_ms"] = t + p["horizon_s"] * 1000 + args.latency_ms
                        active.append(p)
                    else:
                        proceeds = sell(units, p["quantity"])
                        if proceeds is None:
                            counters["insufficient_exit_depth"] += 1
                            continue
                        cost = args.notional_krw * (1 + args.fee_oneway_frac)
                        net = proceeds * (1 - args.fee_oneway_frac) - cost
                        r = {k: v for k, v in p.items() if k != "quantity"}
                        r.update(exit_fill_ms=t, exit_quote_exchange_ms=ex,
                                 quantity=p["quantity"], proceeds_krw=proceeds,
                                 net_return_frac=net / cost)
                        rows.append(r)
                pending[market] = active
                previous_book = quotes.get(market)
                quotes[market] = (t, units)
                first.setdefault(market, t)
                if t - first[market] < args.feature_window_ms:
                    continue
                if t < next_event.get(market, 0):
                    continue
                next_event[market] = t + args.event_window_ms
                if previous_book is None or t - previous_book[0] > args.max_quote_age_ms:
                    counters["event_missing_previous_quote"] += 1
                    continue
                total = sum(v for _, v, _ in q)
                if not q or total <= 0:
                    counters["event_no_recent_trade"] += 1
                    continue
                buy_value = sum(v for _, v, b in q if b)
                recent = sum(v for ts, v, _ in q if ts >= t - args.feature_window_ms / 2)
                old = total - recent
                old_units = previous_book[1]
                ask_depth = sum(float(u["ask_price"]) * float(u["ask_size"]) for u in units[:5])
                old_ask = sum(float(u["ask_price"]) * float(u["ask_size"]) for u in old_units[:5])
                bid_depth = sum(float(u["bid_price"]) * float(u["bid_size"]) for u in units[:5])
                old_bid = sum(float(u["bid_price"]) * float(u["bid_size"]) for u in old_units[:5])
                a, b = float(units[0]["ask_price"]), float(units[0]["bid_price"])
                if a < b or b <= 0:
                    raise ValueError("crossed/invalid book")
                event_id = "%s:%s:%s" % (session, market, t)
                counters["opportunity_events"] += 1
                events[event_id] = (2 * buy_value - total) / total
                base = {
                    "event_id": event_id, "market": market,
                    "date": path.parent.name, "decision_recv_ms": t,
                    "buy_imbalance": (2 * buy_value - total) / total,
                    "trade_acceleration": recent / old if old > 0 else None,
                    "ask_depletion_frac": (old_ask - ask_depth) / old_ask if old_ask else None,
                    "bid_refill_frac": (bid_depth - old_bid) / old_bid if old_bid else None,
                    "spread_frac": (a - b) / ((a + b) / 2),
                    "entry_due_ms": t + args.latency_ms,
                }
                for horizon in args.horizons:
                    p = dict(base, horizon_s=horizon, quantity=None, exit_due_ms=None)
                    if args.latency_ms == 0:
                        p["quantity"] = buy(units, args.notional_krw)
                        if p["quantity"] is None:
                            counters["insufficient_entry_depth"] += 1
                            continue
                        p["entry_fill_ms"] = t
                        p["entry_quote_exchange_ms"] = ex
                        p["exit_due_ms"] = t + horizon * 1000
                    pending[market].append(p)
    counters["unfinished_at_end"] = sum(len(v) for v in pending.values())
    with (out / "events.csv").open("w", newline="") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    thresholds = {}
    # Quantiles on all declared exploratory inputs, not horizon-specific survivors.
    for percentile in (.8, .9, .95):
        thresholds[str(percentile)] = quantile(list(events.values()), percentile)
    tables = {}
    for horizon in args.horizons:
        cohort = [r for r in rows if r["horizon_s"] == horizon]
        groups = {"all": cohort}
        for p, cut in thresholds.items():
            groups["buy_pressure_q" + p] = [r for r in cohort
                                          if cut is not None and r["buy_imbalance"] >= cut]
        tables[str(horizon)] = {k: summary(v, args.stress_bp) for k, v in groups.items()}
    report = {
        "status": "EXPLORATORY_ONLY" if rows else "DATA_INSUFFICIENT",
        "warning": "Not YES/NO, not SURVIVE; input dates are consumed exploration data. "
                   "No maker fills or portfolio returns. Quotes are hypothetical taker benchmark.",
        "input_sha256_uncompressed_stream": digest.hexdigest(),
        "parameters": vars(args), "input_files": manifest,
        "quality_counts": dict(counters),
        "coverage": {k: dict(v) for k, v in market_counts.items()},
        "receive_latency_bins": dict(latency),
        "quantile_thresholds_exploratory": thresholds, "results": tables,
        "selection_warning": "Thresholds use all admitted opportunities, including missing outcomes. "
                             "Outcome exclusions still may bias averages; inspect quality counts.",
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print("EXPLORATORY TAKER BENCHMARK — not a strategy verdict")
    print("horizon group n mean_net_pct median_net_pct positive_rate")
    for horizon, groups in tables.items():
        for name, s in groups.items():
            print(horizon, name, s["n"], s.get("mean_net_pct"),
                  s.get("median_net_pct"), s.get("positive_rate"))
    print("Outputs:", out / "report.json", out / "events.csv")


def self_test():
    units = [{"ask_price": 100, "ask_size": 1, "bid_price": 99, "bid_size": 1},
             {"ask_price": 101, "ask_size": 2, "bid_price": 98, "bid_size": 2}]
    quantity = buy(units, 201)
    assert abs(quantity - 2) < 1e-10
    assert sell(units, quantity) == 197
    assert buy(units, 1000) is None
    assert sell(units, 10) is None
    # Spread is in fill prices already; fees apply once each.
    cost = 201 * 1.0005
    net = 197 * .9995 - cost
    assert net < -4 and abs(net + 4.199) < 1e-8
    assert quantile([1, 2, 3], .5) == 2
    print("SELF_TEST_OK")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", nargs="?")
    p.add_argument("--output", default="micro_edge_probe_output")
    p.add_argument("--notional-krw", type=float, default=6000)
    p.add_argument("--fee-oneway-frac", type=float, default=.0005)
    p.add_argument("--horizons", type=int, nargs="+", default=[5, 10, 15, 30])
    p.add_argument("--latency-ms", type=int, default=200,
                   help="assumed each-leg order latency, not an empirical estimate")
    p.add_argument("--max-quote-age-ms", type=int, default=1000)
    p.add_argument("--max-lateness-ms", type=int, default=1000)
    p.add_argument("--max-gap-ms", type=int, default=5000)
    p.add_argument("--clock-tolerance-ms", type=int, default=1000)
    p.add_argument("--feature-window-ms", type=int, default=5000)
    p.add_argument("--event-window-ms", type=int, default=30000)
    p.add_argument("--stress-bp", type=float, default=5,
                   help="extra ROUNDTRIP stress, not duplicated spread")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    if a.self_test:
        self_test()
        return
    if not a.root:
        p.error("root required")
    if (a.notional_krw <= 0 or not 0 <= a.fee_oneway_frac < 1 or
            min(a.horizons) <= 0 or a.latency_ms < 0 or
            min(a.max_quote_age_ms, a.max_lateness_ms, a.max_gap_ms,
                a.feature_window_ms, a.event_window_ms) <= 0 or a.stress_bp < 0):
        p.error("invalid positive parameter/fee")
    replay(a)


if __name__ == "__main__":
    main()
