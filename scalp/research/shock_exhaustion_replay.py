#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H17 shock/exhaustion event study, PUBLIC ARCHIVE ONLY: no network, keys or orders.

Rules: 10-second receive-time trade return <= TRAIN lower 0.5% quantile (capped
at -0.3%), >=70% sell notional, >=5 trades, 60-second signal cooldown.
A: shock; B: within 10 seconds, sell share of last 2 seconds <50% and no new
post-shock low for 2 seconds. C: B plus already-observed fresh bid-depth recovery.
Decisions precede a 300ms delay; first fresh book within 500ms of execution target
provides an ask/bid depth proxy. Four independent exit horizons: 5/15/30/60 sec.
C checks depth BEFORE that execution delay, never on the eventual fill book.

First 10 available UTC dates fit theta; subsequent dates are SEEN_HOLDOUT, not
independent validation. TRAIN-crossing outcomes are purged. Heartbeats do not
break a session. Corrupt input creates an explicit barrier and prevents a
positive screen. Unknown outcomes are never zero. No-TRAIN runs have no verdict.
The registered statistical screen is descriptive: no multiple-testing adjustment,
market-impact model, actual orders or funded multi-market portfolio is implied.

PC: py -X utf8 -u shock_exhaustion_replay.py ws_server\ws -o se_result_checked
Requires only Python standard library. Preserve the original archive/results.
"""
import argparse
import collections
import datetime as dt
import gzip
import json
import math
import random
import re
import statistics
import sys
import zlib
from array import array
from pathlib import Path

FEE, NOTIONAL, LAT_MS = 0.0005, 100_000, 300
SHOCK_WIN_MS, SELL_SHARE_MIN, MIN_TRADES = 10_000, 0.70, 5
EXH_WIN_MS, EXH_MAX_WAIT_MS = 2_000, 10_000
DEPTH_BASE_MS, DEPTH_LEVELS, DEPTH_SAMPLE_MS = 60_000, 5, 1_000
HORIZONS_S = (5, 15, 30, 60)
COOLDOWN_MS, DROP_Q, DROP_FLOOR = 60_000, 0.005, -0.003
TRAIN_DAYS, MIN_EVENTS = 10, 50
PRIMARY = ("B_exhaust", 15)
MAX_GAP_MS, MAX_AGE_MS, MAX_WAIT_MS = 5_000, 500, 500
VARIANTS = ("A_immediate", "B_exhaust", "C_exhaust_depth")
MARKETS = ("KRW-XRP", "KRW-DOGE", "KRW-ADA", "KRW-XLM", "KRW-XPL")
BENIGN_META = {"heartbeat", "purge"}
_TYPE_RE = re.compile(rb'"type":\s*"(trade|orderbook)"')
_CODE_RE = re.compile(rb'"code":\s*"([A-Z0-9-]+)"')


def _mean(v):
    v = list(v)
    return sum(v) / len(v)


def buy_vwap(units, krw):
    remain, qty = krw, 0.0
    for u in sorted(units, key=lambda x: float(x["ask_price"])):
        p, s = float(u["ask_price"]), float(u["ask_size"])
        if p * s >= remain:
            return qty + remain / p
        qty += s
        remain -= p * s
    return None


def sell_vwap(units, qty):
    remain, krw = qty, 0.0
    for u in sorted(units, key=lambda x: -float(x["bid_price"])):
        p, s = float(u["bid_price"]), float(u["bid_size"])
        if s >= remain:
            return krw + remain * p
        krw += s * p
        remain -= s
    return None


def bid_depth_krw(units, levels=DEPTH_LEVELS):
    return sum(float(u["bid_price"]) * float(u["bid_size"]) for u in
               sorted(units, key=lambda x: -float(x["bid_price"]))[:levels])


def net_return(cost_krw, proceeds_krw):
    return proceeds_krw * (1 - FEE) / (cost_krw * (1 + FEE)) - 1


def window_stats(win):
    total = sum(n for _, _, n, _ in win)
    sell = sum(n for _, _, n, is_sell in win if is_sell)
    return win[-1][1] / win[0][1] - 1, sell / total if total else 0.0


def threshold(returns, q=DROP_Q, floor=DROP_FLOOR):
    if not returns:
        return floor
    values = sorted(returns)
    return min(values[int(q * (len(values) - 1))], floor)


def exhausted(trades_since_shock, now, low_ts):
    recent = [t for t in trades_since_shock if t[0] > now - EXH_WIN_MS]
    total = sum(n for _, _, n, _ in recent)
    sell = sum(n for _, _, n, is_sell in recent if is_sell)
    return total > 0 and sell / total < 0.5 and now - low_ts >= EXH_WIN_MS


def ci_low_days(rows, n=2000, seed=5):
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r["net"])
    days = list(by_day.values())
    if len(days) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(_mean(x for day in rng.choices(days, k=len(days)) for x in day)
                   for _ in range(n))
    return means[int(0.05 * n)]


def summarize(rows):
    resolved = [r for r in rows if r.get("status", "RESOLVED") == "RESOLVED"
                and r.get("net") is not None]
    v = [r["net"] for r in resolved]
    out = {"opportunities": len(rows), "n": len(v),
           "unknown": sum(r.get("status") == "UNKNOWN" for r in rows),
           "not_entered": sum(r.get("status") == "NOT_ENTERED" for r in rows),
           "purged": sum(r.get("status") == "PURGED" for r in rows)}
    if not v:
        return out
    by_day = collections.defaultdict(list)
    for r in resolved:
        by_day[r["date"]].append(r["net"])
    day_means = [_mean(x) for x in by_day.values()]
    out.update(mean=_mean(v), median=statistics.median(v),
               win=sum(x > 0 for x in v) / len(v), ci_low=ci_low_days(resolved),
               ex_top5=_mean(sorted(v)[:-5]) if len(v) > 5 else None,
               days=len(day_means), days_pos=sum(m > 0 for m in day_means),
               mid_ret=_mean(r["mid_ret"] for r in resolved))
    return out


def verdict(s, has_train=True, input_complete=True):
    if not input_complete:
        return "INPUT_INCOMPLETE"
    if not has_train:
        return "NO_TRAIN"
    if s["n"] < MIN_EVENTS or s.get("days", 0) < 2:
        return "INSUFFICIENT"
    if s.get("unknown", 0):
        return "UNRESOLVED"
    ok = (s["mean"] > 0 and s["ci_low"] is not None and s["ci_low"] > 0
          and s["ex_top5"] is not None and s["ex_top5"] > 0
          and s["days_pos"] >= 0.8 * s["days"])
    return "CANDIDATE_SEEN_DATA" if ok else "FAIL"


def archive_files(root, days=None):
    # Reject overlapping roots or unrelated gzip trees rather than double-count.
    files = sorted(p for p in Path(root).rglob("*.jsonl.gz")
                   if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.parent.name))
    if not files:
        raise ValueError("no dated .jsonl.gz under " + str(root))
    slots = [(p.parent.name, p.name) for p in files]
    if len(slots) != len(set(slots)):
        raise ValueError("duplicate date/hour files; choose one archive root")
    dates = sorted({p.parent.name for p in files})
    if days:
        dates = dates[-days:]
        files = [p for p in files if p.parent.name in dates]
    return files, dates


def iter_lines(files, counters=None):
    """Retain complete lines, then emit a barrier for every damaged file."""
    if counters is None:
        counters = collections.Counter()
    for p in files:
        reason = None
        try:
            before = p.stat()
            with gzip.open(p, "rb") as fh:
                for raw in fh:
                    if not raw.endswith(b"\n"):
                        reason = "partial_line"
                        break
                    yield p.parent.name, raw
            after = p.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                reason = "changed_file"
        except (EOFError, OSError, zlib.error):
            reason = "truncated_or_unreadable"
        if reason:
            counters["input_errors"] += 1
            counters[reason] += 1
            print(f"[warn] input incomplete: {p} ({reason})", flush=True)
            yield p.parent.name, json.dumps({"_meta": "input_gap", "reason": reason}).encode()


def _book_valid(units):
    try:
        if not units:
            return False
        for u in units:
            for key in ("ask_price", "ask_size", "bid_price", "bid_size"):
                x = float(u[key])
                if not math.isfinite(x) or x < 0 or ("price" in key and x == 0):
                    return False
        return min(float(u["ask_price"]) for u in units) > max(float(u["bid_price"]) for u in units)
    except (KeyError, TypeError, ValueError):
        return False


def iter_events(files, counters, markets=None):
    """Archive order is availability order; never sort by exchange timestamp."""
    last_recv, native = None, {}
    selected = set(markets) if markets is not None else None
    for date, raw in iter_lines(files, counters):
        is_meta = b'"_meta"' in raw
        if not is_meta:
            tm, cm = _TYPE_RE.search(raw), _CODE_RE.search(raw)
            if not tm or not cm or (selected is not None and cm.group(1).decode() not in selected):
                continue
        try:
            m = json.loads(raw)
            if not isinstance(m, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError):
            counters["input_errors"] += 1
            counters["invalid_json"] += 1
            native.clear()
            yield date, {"_barrier": "invalid_json"}
            continue
        meta = m.get("_meta")
        if meta == "input_gap":
            native.clear()
            yield date, {"_barrier": "input_gap"}
            continue
        try:
            recv = int(m["recv_ts"])
        except (KeyError, TypeError, ValueError, OverflowError):
            counters["input_errors"] += 1
            counters["missing_receive_time"] += 1
            native.clear()
            yield date, {"_barrier": "missing_receive_time"}
            continue
        reason = None
        if last_recv is not None:
            if recv < last_recv:
                reason = "receive_clock_rollback"
                counters["input_errors"] += 1
            elif recv - last_recv > MAX_GAP_MS:
                reason = "receive_gap"
        last_recv = recv
        if reason:
            native.clear()
            counters[reason] += 1
            yield date, {"_barrier": reason, "recv_ts": recv}
        if meta:
            if meta not in BENIGN_META:
                native.clear()
                yield date, {"_barrier": "session_" + str(meta), "recv_ts": recv}
            continue
        try:
            kind, market = m["type"], m["code"]
            event_ts = int(m["timestamp"])
            age = recv - event_ts
            # Clock alignment is an assumption; reject future/stale events.
            if age < 0 or age > MAX_AGE_MS:
                raise ValueError("stale_or_future_event")
            key = market, kind
            stamp = int(m.get("trade_timestamp", event_ts)) if kind == "trade" else event_ts
            if m.get("_seq_anomaly") or stamp < native.get(key, -1):
                raise ValueError("native_time_or_sequence")
            if kind == "trade":
                price, volume = float(m["trade_price"]), float(m["trade_volume"])
                if (not math.isfinite(price) or not math.isfinite(volume)
                        or price <= 0 or volume <= 0 or m.get("ask_bid") not in ("ASK", "BID")):
                    raise ValueError("invalid_trade")
            elif not _book_valid(m.get("orderbook_units")):
                raise ValueError("invalid_book")
            if kind == "trade" and m.get("sequential_id") is not None:
                seq = int(m["sequential_id"])
                seq_key = (market, "sequential_id")
                if seq <= native.get(seq_key, -1):
                    raise ValueError("native_time_or_sequence")
                native[seq_key] = seq
            native[key] = stamp
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            why = str(exc) if str(exc) in ("stale_or_future_event", "native_time_or_sequence",
                                          "invalid_trade", "invalid_book") else "invalid_event"
            counters["rejected_" + why] += 1
            native.clear()
            yield date, {"_barrier": why, "recv_ts": recv}
            continue
        yield date, m


def pass_thresholds(files, counters=None, markets=None):
    """Quantile of ALL eligible TRAIN returns, including flat/positive returns."""
    if counters is None:
        counters = collections.Counter()
    win, started = {}, {}
    rets = collections.defaultdict(lambda: array("d"))
    for _, m in iter_events(files, counters, markets):
        if "_barrier" in m:
            win.clear()
            started.clear()
            continue
        if m["type"] != "trade":
            continue
        code, now, price = m["code"], int(m["recv_ts"]), float(m["trade_price"])
        started.setdefault(code, now)
        q = win.setdefault(code, collections.deque())
        while q and q[0][0] < now - SHOCK_WIN_MS:
            q.popleft()
        q.append((now, price))
        if len(q) >= MIN_TRADES and now - started[code] >= SHOCK_WIN_MS:
            rets[code].append(price / q[0][1] - 1)
    return {code: threshold(values) for code, values in rets.items()}


def replay(files, theta, counters, default_theta=None, train_dates=None, markets=None):
    """Return one terminal outcome per opportunity/variant/horizon, including unknowns."""
    rows = []
    win, depth, since, books = {}, {}, {}, {}
    last_sample, last_opp = {}, {}
    opps = collections.defaultdict(list)
    train = set(train_dates or [])
    phase_before = None
    serial = 0

    def emit(o, var, h, status, reason="", **extra):
        key = var, h
        if key in o["done"]:
            return
        o["done"].add(key)
        row = {"opportunity_id": o["id"], "variant": var, "horizon": h,
               "market": o["market"], "date": o["date"], "signal_recv_ts": o["recv"],
               "signal_exchange_ts": o["native"], "status": status, "reason": reason,
               "net": None, "mid_ret": None}
        row.update(extra)
        rows.append(row)
        counters["outcome_" + status] += 1

    def finish_variant(o, var, status, reason):
        for h in HORIZONS_S:
            emit(o, var, h, status, reason)
        o["enter"].pop(var, None)
        o["legs"] = [leg for leg in o["legs"] if leg["var"] != var]

    def invalidate(reason, status="UNKNOWN"):
        for active in opps.values():
            for o in active:
                for var in VARIANTS:
                    finish_variant(o, var, status, reason)
        opps.clear()
        win.clear()
        depth.clear()
        since.clear()
        books.clear()
        last_sample.clear()
        last_opp.clear()

    def expire(now):
        for active in opps.values():
            for o in active:
                if o["wait"] and now > o["recv"] + EXH_MAX_WAIT_MS:
                    o["wait"] = False
                    counters["B_timeout"] += 1
                    for var in VARIANTS[1:]:
                        finish_variant(o, var, "NOT_ENTERED", "confirmation_timeout")
                for var, target in list(o["enter"].items()):
                    if now > target + MAX_WAIT_MS:
                        finish_variant(o, var, "UNKNOWN", "entry_quote_late_or_missing")
                for leg in list(o["legs"]):
                    if now > leg["exit_target"] + MAX_WAIT_MS:
                        emit(o, leg["var"], leg["h"], "UNKNOWN", "exit_quote_late_or_missing",
                             entry_recv_ts=leg["entry_recv"], entry_exchange_ts=leg["entry_native"])
                        o["legs"].remove(leg)

    for date, m in iter_events(files, counters, markets):
        phase = date in train
        if phase_before is not None and phase != phase_before:
            invalidate("split_boundary", "PURGED")
        phase_before = phase
        if "_barrier" in m:
            invalidate(m["_barrier"])
            continue
        now, market = int(m["recv_ts"]), m["code"]
        expire(now)
        # Remove fully classified opportunities; never remove unfinished outcomes.
        for code in list(opps):
            opps[code] = [o for o in opps[code] if len(o["done"]) < len(VARIANTS) * len(HORIZONS_S)]
        if m["type"] == "orderbook":
            units = m["orderbook_units"]
            mid = (min(float(u["ask_price"]) for u in units)
                   + max(float(u["bid_price"]) for u in units)) / 2
            books[market] = (now, units)
            dq = depth.setdefault(market, collections.deque())
            if now - last_sample.get(market, -10**18) >= DEPTH_SAMPLE_MS:
                last_sample[market] = now
                dq.append((now, bid_depth_krw(units)))
            while dq and dq[0][0] < now - DEPTH_BASE_MS:
                dq.popleft()
            for o in opps[market]:
                for var, target in list(o["enter"].items()):
                    if now < target:
                        continue
                    del o["enter"][var]
                    qty = buy_vwap(units, NOTIONAL)
                    if qty is None:
                        finish_variant(o, var, "UNKNOWN", "entry_depth_insufficient")
                        continue
                    counters["entries_" + var] += 1
                    for h in HORIZONS_S:
                        busy = any(leg["var"] == var and leg["h"] == h
                                   for other in opps[market] if other is not o for leg in other["legs"])
                        if busy:
                            emit(o, var, h, "NOT_ENTERED", "existing_position")
                            continue
                        o["legs"].append({"var": var, "h": h, "qty": qty, "mid": mid,
                                          "entry_recv": now, "entry_native": int(m["timestamp"]),
                                          "exit_target": now + h * 1000})
                for leg in list(o["legs"]):
                    if now < leg["exit_target"]:
                        continue
                    o["legs"].remove(leg)
                    proceeds = sell_vwap(units, leg["qty"])
                    extra = dict(entry_recv_ts=leg["entry_recv"],
                                 entry_exchange_ts=leg["entry_native"], exit_recv_ts=now,
                                 exit_exchange_ts=int(m["timestamp"]), quantity=leg["qty"],
                                 cost_krw=NOTIONAL * (1 + FEE))
                    if proceeds is None:
                        emit(o, leg["var"], leg["h"], "UNKNOWN", "exit_depth_insufficient", **extra)
                    else:
                        emit(o, leg["var"], leg["h"], "RESOLVED",
                             net=net_return(NOTIONAL, proceeds), mid_ret=mid / leg["mid"] - 1,
                             proceeds_after_fee_krw=proceeds * (1 - FEE), **extra)
            continue
        price, volume = float(m["trade_price"]), float(m["trade_volume"])
        trade = (now, price, price * volume, m["ask_bid"] == "ASK")
        q = win.setdefault(market, collections.deque())
        since.setdefault(market, now)
        while q and q[0][0] < now - SHOCK_WIN_MS:
            q.popleft()
        q.append(trade)
        for o in opps[market]:
            if not o["wait"]:
                continue
            o["recent"].append(trade)
            while o["recent"] and o["recent"][0][0] <= now - EXH_WIN_MS:
                o["recent"].popleft()
            if price < o["low"]:
                o["low"], o["low_ts"] = price, now
            if exhausted(o["recent"], now, o["low_ts"]):
                o["wait"] = False
                counters["B_confirmed"] += 1
                o["enter"]["B_exhaust"] = now + LAT_MS
                book = books.get(market)
                if (o["depth_base"] is not None and book is not None
                        and 0 <= now - book[0] <= MAX_AGE_MS
                        and bid_depth_krw(book[1]) >= o["depth_base"]):
                    o["enter"]["C_exhaust_depth"] = now + LAT_MS
                else:
                    finish_variant(o, "C_exhaust_depth", "NOT_ENTERED", "depth_not_recovered_at_decision")
        th = theta.get(market, default_theta)
        if (th is None or len(q) < MIN_TRADES or now - since[market] < SHOCK_WIN_MS
                or now - last_opp.get(market, -10**18) < COOLDOWN_MS):
            continue
        ret, share = window_stats(q)
        if ret > th or share < SELL_SHARE_MIN:
            continue
        last_opp[market] = now
        serial += 1
        counters["opportunities"] += 1
        dq = depth.get(market, ())
        base = [(t, d) for t, d in dq if now - DEPTH_BASE_MS <= t < now]
        # Require nearly a full past minute and no long sampling holes.
        ready = (len(base) >= 50 and now - base[0][0] >= DEPTH_BASE_MS - DEPTH_SAMPLE_MS
                 and now - base[-1][0] <= 2 * DEPTH_SAMPLE_MS
                 and all(b[0] - a[0] <= 2 * DEPTH_SAMPLE_MS for a, b in zip(base, base[1:])))
        opps[market].append({"id": f"{market}:{now}:{serial}", "market": market,
                             "date": date, "recv": now, "native": int(m["timestamp"]),
                             "low": price, "low_ts": now, "wait": True,
                             "recent": collections.deque([trade]), "legs": [], "done": set(),
                             "enter": {"A_immediate": now + LAT_MS},
                             "depth_base": statistics.median(d for _, d in base) if ready else None})
    invalidate("end_of_input")
    return rows


def analyze(rows, train_dates, input_complete=True):
    out, train = [], set(train_dates)
    phases = ("TRAIN", "HOLDOUT") if train else ("EXPLORATION_NO_TRAIN",)
    for phase in phases:
        for var in VARIANTS:
            for h in HORIZONS_S:
                selected = [r for r in rows if r["variant"] == var and r["horizon"] == h
                            and (not train or ((r["date"] in train) == (phase == "TRAIN")))]
                s = summarize(selected)
                s.update(phase=phase, variant=var, horizon=h, primary=(var, h) == PRIMARY,
                         verdict=verdict(s, bool(train), input_complete) if phase != "TRAIN" else "(explore)")
                out.append(s)
    return out


def _fmt(s):
    def pct(key):
        value = s.get(key)
        return f"{value * 100:+.3f}" if value is not None else "n/a"
    return (f"n={s['n']} unknown={s['unknown']} skipped={s['not_entered']} purged={s['purged']} "
            f"net%={pct('mean')} med%={pct('median')} ciLow%={pct('ci_low')} "
            f"exTop5%={pct('ex_top5')} days+={s.get('days_pos', 0)}/{s.get('days', 0)}")


def _manifest(files):
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in files}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root_pos", nargs="?")
    ap.add_argument("-r", "--root", dest="root_opt")
    ap.add_argument("-o", "--out", default="se_result_checked")
    ap.add_argument("-d", "--days", type=int, default=0)
    ap.add_argument("-t", "--train-days", type=int, default=TRAIN_DAYS)
    ap.add_argument("--markets", default=",".join(MARKETS))
    a = ap.parse_args(argv)
    root = a.root_opt or a.root_pos
    if not root:
        ap.error("specify one archive root, e.g. ws_server\\ws")
    if a.days < 0 or a.train_days < 1:
        ap.error("--days must be >=0 and --train-days >=1")
    files, dates = archive_files(root, a.days or None)
    out = Path(a.out)
    if Path(root).resolve() == out.resolve() or Path(root).resolve() in out.resolve().parents:
        ap.error("output must be outside the input archive")
    markets = tuple(x.strip() for x in a.markets.split(",") if x.strip())
    if not markets:
        ap.error("--markets must contain at least one market")
    manifest = _manifest(files)
    train_dates = dates[:a.train_days] if len(dates) > a.train_days else []
    print(f"H17 EXPLORATION ONLY | {len(files)} files | {dates[0]}..{dates[-1]} | "
          f"TRAIN={len(train_dates)} dates | markets={','.join(markets)}", flush=True)
    print("RECEIVE_TIME | quote age/wait <=500ms | delay=300ms | "
          "100000 KRW principal + entry fee | NO FUNDED PORTFOLIO", flush=True)
    fit_counts, counters = collections.Counter(), collections.Counter()
    theta = pass_thresholds([p for p in files if p.parent.name in train_dates],
                            fit_counts, markets) if train_dates else {}
    print("THETA: " + json.dumps(theta, sort_keys=True), flush=True)
    rows = replay(files, theta, counters, default_theta=None if train_dates else DROP_FLOOR,
                  train_dates=train_dates, markets=markets)
    try:
        unchanged = _manifest(files) == manifest
    except OSError:
        unchanged = False
    complete = unchanged and not fit_counts["input_errors"] and not counters["input_errors"]
    res = analyze(rows, train_dates, complete)
    quality = "RECORDED_WITH_QUALITY_EXCLUSIONS" if complete else "INPUT_INCOMPLETE"
    report = {"schema": 2, "status": "EXPLORATION_ONLY", "input_quality": quality,
              "dates": dates, "train_dates": train_dates, "markets": markets, "theta": theta,
              "params": {"notional_principal_krw": NOTIONAL, "fee_oneway": FEE,
                         "latency_ms": LAT_MS, "max_quote_age_ms": MAX_AGE_MS,
                         "max_execution_wait_ms": MAX_WAIT_MS, "horizons_s": HORIZONS_S},
              "fit_counters": dict(fit_counts), "counters": dict(counters), "cells": res,
              "portfolio_evaluated": False, "fresh_independent_holdout": False,
              "limitations": ["receive wall clock alignment assumed", "displayed depth fill proxy",
                              "no own market impact", "no combined capital/account return",
                              "seen-data screen, no multiple-testing adjustment"]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1, allow_nan=False), encoding="utf-8")
    (out / "rows.json").write_text(json.dumps(rows, allow_nan=False), encoding="utf-8")
    lines = [f"INPUT_QUALITY: {quality}", "PROFIT_VERDICT: NOT_VALIDATED"]
    lines += [f"{s['phase']} {s['variant']} {s['horizon']}s {_fmt(s)} {s['verdict']}" for s in res]
    lines.append(f"Results: {out}/report.json {out}/rows.json")
    (out / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
