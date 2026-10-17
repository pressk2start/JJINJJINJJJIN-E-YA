#!/usr/bin/env python3
"""Read-only P1 ledger audit. Stdlib only; never imports bot or calls an exchange."""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

VERSION = "1.0"


def number(value, positive=False, nonnegative=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite():
        return None
    if positive and result <= 0:
        return None
    if nonnegative and result < 0:
        return None
    return result


def plain(value):
    return float(value) if value is not None else None


def matches(a, b):
    return abs(a - b) <= max(Decimal("1e-12"), max(abs(a), abs(b)) * Decimal("1e-8"))


def strict_json(text):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError("duplicate JSON key: " + key)
            obj[key] = value
        return obj
    def invalid_constant(value):
        raise ValueError("non-finite JSON constant: " + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def read_ledger(path):
    rows = []
    invalid = []
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for line_no, raw in enumerate(source, 1):
            digest.update(raw)
            if not raw.strip():
                continue
            try:
                row = strict_json(raw)
                if not isinstance(row, dict):
                    raise ValueError("not an object")
            except (ValueError, UnicodeError):
                invalid.append(line_no)
                continue
            rows.append(row)
    return rows, invalid, digest.hexdigest()


def read_orders(path):
    """Accept an offline JSON order object/list or JSONL; no authentication required."""
    if path is None:
        return [], []
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = strict_json(raw)
        data = data if isinstance(data, list) else [data]
    except ValueError:
        data = [strict_json(line) for line in raw.splitlines() if line.strip()]
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("orders must contain JSON objects")
    return data, []


def order_summary(order, side, market):
    """Require full trade detail, quantity agreement, final state and explicit fee."""
    if not market.startswith("KRW-"):
        return None, "non_krw_market_unsupported"
    if order.get("market") != market or order.get("side") != side:
        return None, "order_market_or_side_mismatch"
    if order.get("state") not in ("done", "cancel"):
        return None, "order_not_final"
    if order.get("state") == "done" and order.get("remaining_volume") is not None:
        if number(order["remaining_volume"], nonnegative=True) != Decimal(0):
            return None, "completed_order_has_remaining_volume"
    qty = number(order.get("executed_volume"), positive=True)
    fee = number(order.get("paid_fee"), nonnegative=True)
    if qty is None or fee is None:
        return None, "order_quantity_or_fee_unknown"
    trades = order.get("trades")
    if not isinstance(trades, list) or not trades:
        return None, "order_trade_details_missing"
    count = order.get("trades_count")
    if count is not None and number(count) != len(trades):
        return None, "order_trade_count_mismatch"
    total_qty, total_funds = Decimal(0), Decimal(0)
    trade_ids = set()
    for trade in trades:
        if not isinstance(trade, dict):
            return None, "invalid_order_trade"
        price = number(trade.get("price"), positive=True)
        volume = number(trade.get("volume"), positive=True)
        if price is None or volume is None:
            return None, "invalid_order_trade"
        if trade.get("market") is not None and trade["market"] != market:
            return None, "trade_market_conflict"
        if trade.get("side") is not None and trade["side"] != side:
            return None, "trade_side_conflict"
        if trade.get("uuid") is not None:
            if not isinstance(trade["uuid"], str):
                return None, "invalid_trade_uuid"
            if trade["uuid"] in trade_ids:
                return None, "duplicate_exchange_trade"
            trade_ids.add(trade["uuid"])
        calculated = price * volume
        funds = calculated
        if trade.get("funds") is not None:
            funds = number(trade["funds"], positive=True)
            if funds is None or abs(funds - calculated) > max(
                Decimal("0.000001"), calculated * Decimal("0.000001")
            ):
                return None, "order_funds_mismatch"
        total_qty += volume
        total_funds += funds
    if not matches(qty, total_qty):
        return None, "order_volume_mismatch"
    if not total_funds.is_finite() or total_funds <= 0:
        return None, "order_funds_invalid"
    if order.get("executed_funds") is not None:
        stated = number(order["executed_funds"], positive=True)
        if stated is None or abs(stated - total_funds) > max(
            Decimal("0.000001"), total_funds * Decimal("0.000001")
        ):
            return None, "order_total_funds_mismatch"
    return {"qty": qty, "funds": total_funds, "fee": fee}, None


def reported_stats(rows):
    values = [number(row["reported_net_pct"]) for row in rows
              if row["reported_net_pct"] is not None]
    if not values:
        return {"n": 0, "wins": 0, "positive_rate": None,
                "mean_reported_pct": None, "sum_reported_pp": None}
    return {
        "n": len(values), "wins": sum(x > 0 for x in values),
        "positive_rate": sum(x > 0 for x in values) / len(values),
        "mean_reported_pct": plain(sum(values) / len(values)),
        "sum_reported_pp": plain(sum(values)),
    }


def analyze(rows, orders=(), since=None, until=None):
    grouped = defaultdict(list)
    quality = Counter()
    order_index = defaultdict(list)
    for order in orders:
        if order.get("uuid"):
            order_index[str(order["uuid"])].append(order)
        else:
            quality["exchange_order_missing_uuid"] += 1
    for row in rows:
        if not row.get("trade_id"):
            # ORDER_SUBMITTED occurs before a trade_id exists by design.
            # Keep it visible as an unlinked submission, not a malformed trade event.
            if row.get("event_type") == "ORDER_SUBMITTED":
                quality["order_submitted_without_trade_id"] += 1
            else:
                quality["event_missing_trade_id"] += 1
            continue
        grouped[str(row["trade_id"])].append(row)
    # Build UUID ownership before date filtering, so a hidden leg cannot make a
    # reused order look unique in a selected slice.
    all_uuid_owners = defaultdict(set)
    for owner, owner_events in grouped.items():
        for event in owner_events:
            for field in ("entry_order_uuid", "exit_order_uuid"):
                if event.get(field):
                    all_uuid_owners[str(event[field])].add(owner)
    output = []
    for trade_id, events in sorted(grouped.items()):
        kinds = defaultdict(list)
        for event in events:
            kinds[event.get("event_type", "UNKNOWN")].append(event)
        entries, exits, closes = (
            kinds["ENTRY_FILLED"], kinds["EXIT_FILLED"], kinds["TRADE_CLOSED"]
        )
        entry = entries[0] if entries else {}
        exit_row = exits[0] if exits else {}
        closed = closes[0] if closes else {}
        reconciles = kinds["ENTRY_RECONCILED"]
        latest_reconcile = reconciles[-1] if reconciles else {}
        # Date filters use the ledger's entry_time date, with no invented timezone.
        when = str(entry.get("entry_time") or "")
        day = when[:10]
        if since or until:
            try:
                date.fromisoformat(day)
            except ValueError:
                quality["date_filter_unresolved_trade"] += 1
                continue
            if (since and day < since) or (until and day > until):
                continue
        flags = []
        for name, group in (("entry", entries), ("exit", exits), ("close", closes)):
            if len(group) != 1:
                flags.append(name + ("_missing" if not group else "_duplicate"))
        market_values = [x.get("market") for x in events if x.get("market")]
        markets = {x for x in market_values if isinstance(x, str)}
        market = next(iter(markets)) if len(markets) == 1 else ""
        if len(markets) != 1 or len(markets) != len(set(str(x) for x in market_values)):
            flags.append("market_missing_or_conflict")
        versions = {str(x.get("schema_v", 1)) for x in events}
        schema = next(iter(versions)) if len(versions) == 1 else "mixed"
        if schema != "2":
            flags.append("legacy_or_mixed_schema")
        live_values = [x.get("is_live") for x in events if "is_live" in x]
        live = "true" if any(x is True for x in live_values) else "unknown"
        if any(x is False for x in live_values):
            live = "conflict" if live == "true" else "false"
            flags.append("live_false_or_conflict")
        if live == "unknown":
            flags.append("live_unmarked")
        buy_price = number(entry.get("filled_price"), positive=True)
        buy_qty = number(entry.get("filled_volume"), positive=True)
        buy_cost = number(entry.get("entry_krw"), positive=True)
        buy_fee = number(entry.get("entry_fee_krw"), nonnegative=True)
        sell_price = number(exit_row.get("exit_price"), positive=True)
        sell_qty = number(exit_row.get("executed_exit_volume"), positive=True)
        sell_fee = number(exit_row.get("exit_fee_krw"), nonnegative=True)
        remaining = number(closed.get("remaining_volume"), nonnegative=True)
        buy_uuid, sell_uuid = entry.get("entry_order_uuid"), exit_row.get("exit_order_uuid")
        # Reconcile records may update the in-memory position after an initial partial fill.
        # They do NOT prove that all buy orders and fees were exported. Explicitly
        # reject single-buy/single-sell matching for an updated multi-leg entry.
        final_rec_qty = number(latest_reconcile.get("final_volume"), positive=True)
        final_rec_price = number(latest_reconcile.get("final_avg_price"), positive=True)
        final_rec_cost = number(latest_reconcile.get("final_entry_krw"), positive=True)
        if any(r.get("reconcile_mode") == "updated_pending" for r in reconciles):
            flags.append("entry_reconciled_after_initial_fill")
        if latest_reconcile and (final_rec_qty is None or final_rec_price is None or final_rec_cost is None):
            flags.append("entry_reconcile_incomplete")
        if buy_price is None or buy_qty is None or buy_cost is None:
            flags.append("entry_cashflow_unknown")
        elif abs(buy_cost - buy_price * buy_qty) > max(
            Decimal("0.02"), buy_cost * Decimal("1e-8")
        ):
            flags.append("entry_notional_mismatch")
        if buy_fee is None:
            flags.append("entry_fee_unknown")
        if not buy_uuid:
            flags.append("entry_order_uuid_missing")
        if not sell_uuid:
            flags.append("exit_order_uuid_missing")
        if sell_price is None or sell_qty is None:
            flags.append("exit_cashflow_unknown")
        if sell_fee is None:
            flags.append("exit_fee_unknown")
        if buy_qty is not None and sell_qty is not None and not matches(buy_qty, sell_qty):
            flags.append("logged_volume_mismatch")
        if closed.get("completed") is not True or remaining != Decimal(0):
            flags.append("closure_not_confirmed")
        start, end = number(entry.get("entry_ts")), number(exit_row.get("exit_ts"))
        if start is not None and end is not None and end < start:
            flags.append("time_reversal")
        reported = number(closed.get("net_pnl_pct"))
        # A sum of reported returns is never account performance or verified cashflow.
        item = {
            "trade_id": trade_id, "market": market, "entry_time": when,
            "exit_time": exit_row.get("exit_time"), "schema": schema, "live": live,
            "entry_events": len(entries), "exit_events": len(exits), "close_events": len(closes),
            "entry_order_uuid": buy_uuid, "exit_order_uuid": sell_uuid,
            "entry_price": plain(buy_price), "entry_qty": plain(buy_qty),
            "entry_krw": plain(buy_cost), "entry_fee_krw": plain(buy_fee),
            "entry_reconcile_events": len(reconciles),
            "reconciled_final_qty": plain(final_rec_qty),
            "reconciled_final_avg_price": plain(final_rec_price),
            "reconciled_final_entry_krw": plain(final_rec_cost),
            "exit_price": plain(sell_price), "exit_qty": plain(sell_qty),
            "exit_fee_krw": plain(sell_fee), "exit_reason": closed.get("exit_reason"),
            "reported_net_pct": plain(reported), "reported_net_krw": plain(number(closed.get("net_pnl_krw"))),
            "exchange_cashflow_net_krw": None, "exchange_cashflow_net_pct": None,
            "reported_minus_exchange_pp": None,
            "reconciliation": "UNVERIFIED", "flags": flags,
        }
        # Without exact exported order responses, no record is marked reconciled.
        blockers = any(x in flags for x in (
            "entry_missing", "entry_duplicate", "exit_missing", "exit_duplicate",
            "close_missing", "close_duplicate", "market_missing_or_conflict",
            "live_false_or_conflict", "live_unmarked", "closure_not_confirmed", "time_reversal",
            "entry_reconciled_after_initial_fill", "entry_reconcile_incomplete",
        ))
        if orders and not blockers and buy_uuid and sell_uuid:
            buys, sells = order_index[str(buy_uuid)], order_index[str(sell_uuid)]
            if len(buys) != 1 or len(sells) != 1 or buy_uuid == sell_uuid:
                flags.append("exchange_order_missing_or_duplicate")
            else:
                time_error = False
                if buys[0].get("created_at") is not None and sells[0].get("created_at") is not None:
                    try:
                        buy_time = datetime.fromisoformat(str(buys[0]["created_at"]).replace("Z", "+00:00"))
                        sell_time = datetime.fromisoformat(str(sells[0]["created_at"]).replace("Z", "+00:00"))
                        time_error = sell_time < buy_time
                    except (ValueError, TypeError):
                        time_error = True
                buy, error = order_summary(buys[0], "bid", market)
                sell, error2 = order_summary(sells[0], "ask", market)
                if time_error:
                    flags.append("exchange_order_time_invalid")
                elif error or error2:
                    flags.extend(x for x in (error, error2) if x)
                elif not matches(buy["qty"], sell["qty"]):
                    flags.append("exchange_roundtrip_volume_mismatch")
                elif (buy_qty is None or sell_qty is None
                      or not matches(buy["qty"], buy_qty)
                      or not matches(sell["qty"], sell_qty)):
                    flags.append("exchange_vs_ledger_volume_mismatch")
                else:
                    cost = buy["funds"] + buy["fee"]
                    net = sell["funds"] - sell["fee"] - cost
                    pct = net / cost * 100
                    item["exchange_cashflow_net_krw"] = plain(net)
                    item["exchange_cashflow_net_pct"] = plain(pct)
                    item["reported_minus_exchange_pp"] = plain(reported - pct) if reported is not None else None
                    item["reconciliation"] = "MATCHED_EXPORTED_ORDER_PAIR"
        output.append(item)
        quality.update(set(flags))
    # Shared UUIDs across trade IDs require a portfolio/cost-allocation model; reject here.
    uuid_owners = defaultdict(set)
    for item in output:
        for field in ("entry_order_uuid", "exit_order_uuid"):
            if item[field]:
                uuid_owners[str(item[field])].add(item["trade_id"])
    shared = {key for key, owners in all_uuid_owners.items() if len(owners) > 1}
    for item in output:
        if any(str(item[field]) in shared for field in ("entry_order_uuid", "exit_order_uuid") if item[field]):
            item["flags"].append("order_uuid_reused_across_trades")
            quality["order_uuid_reused_across_trades"] += 1
            item["reconciliation"] = "UNVERIFIED"
            for field in ("exchange_cashflow_net_krw", "exchange_cashflow_net_pct", "reported_minus_exchange_pp"):
                item[field] = None
    eligible = [x for x in output if x["schema"] == "2" and x["live"] == "true"
                and x["entry_events"] == x["close_events"] == 1
                and "market_missing_or_conflict" not in x["flags"]]
    by_market, by_day, by_reason = {}, {}, {}
    for name, field in ((by_market, "market"), (by_day, "entry_time"), (by_reason, "exit_reason")):
        temp = defaultdict(list)
        for item in eligible:
            key = str(item[field] or "UNKNOWN")
            if field == "entry_time":
                key = key[:10] or "UNKNOWN"
            elif field == "exit_reason":
                parts = key.split("|")[0].strip().split()
                key = " ".join(parts[:2] if parts and parts[0] == "시간만료" else parts[:1]) or "UNKNOWN"
            temp[key].append(item)
        name.update({key: reported_stats(value) for key, value in sorted(temp.items())})
    reconciled = [x for x in output if x["reconciliation"] == "MATCHED_EXPORTED_ORDER_PAIR"]
    return {
        "version": VERSION,
        "status": "AUDIT_ONLY_NOT_STRATEGY_VERDICT",
        "warnings": [
            "Reported pct is not verified PnL; legacy/mixed schema excluded from reported summaries.",
            "Reported sum has units percentage points, not account return.",
            "Exported order pairs verify only matched quantities/cashflows, not exchange authenticity or complete history.",
            "No inferred fills, fee-rate estimates, fuzzy shadow matching, portfolio returns, or exit-causality claims.",
            "Dates use recorded entry_time; no timezone inference.",
        ],
        "event_counts": dict(Counter(str(x.get("event_type", "UNKNOWN")) for x in rows)),
        "schema_counts": dict(Counter(str(x.get("schema_v", 1)) for x in rows)),
        "unique_trade_ids": len(grouped), "selected_trade_ids": len(output),
        "quality_trade_counts": dict(sorted(quality.items())),
        "reported_v2_live": reported_stats(eligible),
        "reported_by_market": by_market, "reported_by_entry_date": by_day,
        "reported_by_exit_family": by_reason,
        "matched_exported_order_pairs": len(reconciled),
        "matched_pair_net_krw_sum": sum(x["exchange_cashflow_net_krw"] for x in reconciled) if reconciled else None,
        "trades": output,
    }


def save_report(report, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    trades = report["trades"]
    if trades:
        with (output / "trades.csv").open("w", newline="", encoding="utf-8-sig") as target:
            writer = csv.DictWriter(target, fieldnames=list(trades[0]))
            writer.writeheader()
            for row in trades:
                writer.writerow({**row, "flags": ";".join(row["flags"])})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", help="Existing live_trades.jsonl (read only)")
    parser.add_argument("--orders", help="Optional exported Upbit order JSON/JSONL; no API calls")
    parser.add_argument("--output", default="/tmp/p1-audit")
    parser.add_argument("--since", type=date.fromisoformat, help="Inclusive recorded entry date YYYY-MM-DD")
    parser.add_argument("--until", type=date.fromisoformat, help="Inclusive recorded entry date YYYY-MM-DD")
    args = parser.parse_args(argv)
    if args.since and args.until and args.since > args.until:
        parser.error("--since must be <= --until")
    ledger = Path(args.ledger).resolve()
    out = Path(args.output).resolve()
    orders_path = Path(args.orders).resolve() if args.orders else None
    targets = {out / "report.json", out / "trades.csv"}
    if ledger in targets or orders_path in targets:
        parser.error("output would overwrite an input file")
    rows, invalid, sha = read_ledger(ledger)
    orders, _ = read_orders(orders_path)
    report = analyze(rows, orders,
                     args.since.isoformat() if args.since else None,
                     args.until.isoformat() if args.until else None)
    report["input_sha256"] = sha
    report["orders_export_sha256"] = hashlib.sha256(orders_path.read_bytes()).hexdigest() if orders_path else None
    report["invalid_json_lines"] = invalid
    report["date_filter"] = {"since": str(args.since) if args.since else None,
                             "until": str(args.until) if args.until else None}
    save_report(report, out)
    stats = report["reported_v2_live"]
    print("P1 AUDIT — stored returns are not verified account performance")
    print("events=%d trade_ids=%d invalid_lines=%d" % (len(rows), report["selected_trade_ids"], len(invalid)))
    print("v2 LIVE reported: n=%d wins=%d mean_pct=%s sum_pp=%s" % (
        stats["n"], stats["wins"], stats["mean_reported_pct"], stats["sum_reported_pp"]))
    print("matched exported order pairs=%d net_krw_sum=%s" % (
        report["matched_exported_order_pairs"], report["matched_pair_net_krw_sum"]))
    for flag, count in report["quality_trade_counts"].items():
        print("  %s: %d" % (flag, count))
    print("Outputs: %s/report.json %s/trades.csv" % (out, out))


if __name__ == "__main__":
    main()
