#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""H17 shock/exhaustion event study, PUBLIC ARCHIVE ONLY: no network, keys or orders.

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

PRIOR EXPLORATION 2026-10-25 (revision 1b855143, user-run, incomplete input):
  Primary B/15s: TRAIN n=21 mean=-0.498%, HOLDOUT n=9 mean=-0.482%.
  B/15s UNKNOWN across BOTH phases: entry quote deadline 23, stale/future event
  21, exit quote deadline 14. These are terminal outcomes, not actual orders.
  That revision wrongly let a rejected event censor unrelated markets. Do not
  infer how many of the 21 were affected, or that repairing one file cannot
  change the completed sample. No family-wide profitability verdict is established.
  This revision scopes ordinary event rejection to its market; actual file,
  connection and shared clock discontinuities remain global. Trading thresholds,
  latency and freshness/deadline limits are unchanged.

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

MODEL_REVISION = "market-scoped-v3"
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
    unknown = [r for r in rows if r.get("status") == "UNKNOWN"]
    out = {"unknown_reasons": dict(collections.Counter(r.get("reason", "") for r in unknown)),
           "unknown_markets": dict(collections.Counter(r.get("market", "UNKNOWN") for r in unknown)),
           "opportunities": len(rows), "n": len(v),
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


def archive_files(root, days=None, before_date=None):
    # Reject overlapping roots or unrelated gzip trees rather than double-count.
    files = sorted(p for p in Path(root).rglob("*.jsonl.gz")
                   if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.parent.name)
                   and (before_date is None or p.parent.name < before_date))
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
        market = m.get("code")
        try:
            kind, market = m["type"], m["code"]
            event_ts = int(m["timestamp"])
            age = recv - event_ts
            # Clock alignment is an assumption; reject future/stale events.
            if age < 0 or age > MAX_AGE_MS:
                raise ValueError("stale_or_future_event")
            key = market, kind
            stamp = int(m.get("trade_timestamp", event_ts)) if kind == "trade" else event_ts
            if kind == "trade" and not 0 <= recv - stamp <= MAX_AGE_MS:
                raise ValueError("stale_or_future_event")
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
            barrier = {"_barrier": why, "recv_ts": recv, "source_kind": m.get("type")}
            if isinstance(market, str):
                # A bad event does not erase other assets, or lower native high-water marks.
                barrier["market"] = market
                counters["rejected_market:" + market + ":" + why] += 1
            else:
                native.clear()
            yield date, barrier
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
            market = m.get("market")
            if market is None:
                win.clear()
                started.clear()
            else:
                win.pop(market, None)
                started.pop(market, None)
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

    def finish_variant(o, var, status, reason, context=None):
        active = {leg["h"]: leg for leg in o["legs"] if leg["var"] == var}
        for h in HORIZONS_S:
            extra = dict(context or {})
            leg = active.get(h)
            if leg is not None:
                extra.update(entry_recv_ts=leg["entry_recv"],
                             entry_exchange_ts=leg["entry_native"],
                             decision_recv_ts=leg["decision_recv"],
                             quantity=leg["qty"], cost_krw=NOTIONAL * (1 + FEE))
            emit(o, var, h, status, reason, **extra)
        o["enter"].pop(var, None)
        o["legs"] = [leg for leg in o["legs"] if leg["var"] != var]

    def invalidate(reason, status="UNKNOWN", market=None, source_kind=None):
        affected = list(opps) if market is None else [market]
        context = {"barrier_scope": "global" if market is None else "market",
                   "barrier_market": market, "barrier_stream": source_kind}
        for code in affected:
            for o in opps.get(code, ()):
                for var in VARIANTS:
                    finish_variant(o, var, status, reason, context)
            opps.pop(code, None)
        for state in (win, depth, since, books, last_sample):
            if market is None:
                state.clear()
            else:
                state.pop(market, None)
        if market is None:
            last_opp.clear()
        # Market-local rejection does not reset the registered 60s signal cooldown.

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
                             entry_recv_ts=leg["entry_recv"], entry_exchange_ts=leg["entry_native"],
                             decision_recv_ts=leg["decision_recv"])
                        o["legs"].remove(leg)

    for date, m in iter_events(files, counters, markets):
        phase = date in train
        if phase_before is not None and phase != phase_before:
            invalidate("split_boundary", "PURGED")
        phase_before = phase
        if "_barrier" in m:
            invalidate(m["_barrier"], market=m.get("market"), source_kind=m.get("source_kind"))
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
            books[market] = (now, units, int(m["timestamp"]))
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
                                          "decision_recv": target - LAT_MS,
                                          "exit_target": now + h * 1000})
                for leg in list(o["legs"]):
                    if now < leg["exit_target"]:
                        continue
                    o["legs"].remove(leg)
                    proceeds = sell_vwap(units, leg["qty"])
                    extra = dict(decision_recv_ts=leg["decision_recv"], entry_recv_ts=leg["entry_recv"],
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
                        and 0 <= now - book[2] <= MAX_AGE_MS
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


# Outcome-first discovery is opt-in; the frozen H17 event study above is unchanged.
DISCOVERY_REVISION = "outcome-discovery-v1"
DISCOVERY_WINDOWS = (1, 5, 10, 30, 60)


class DiscoveryState:
    def __init__(self, now):
        self.started = now
        self.book = None
        self.history = collections.deque()
        self.flow = {w: [collections.deque(), 0.0, 0.0, 0] for w in DISCOVERY_WINDOWS}

    def apply(self, m):
        now = int(m["recv_ts"])
        if m["type"] == "trade":
            value = float(m["trade_price"]) * float(m["trade_volume"])
            sell = m["ask_bid"] == "ASK"
            for q in self.flow.values():
                q[0].append((now, value, sell))
                q[2 if sell else 1] += value
                q[3] += 1
            self.expire(now)
            return
        units = m["orderbook_units"]
        bids = sorted(units, key=lambda u: -float(u["bid_price"]))
        asks = sorted(units, key=lambda u: float(u["ask_price"]))
        bid, ask = float(bids[0]["bid_price"]), float(asks[0]["ask_price"])
        mid = (bid + ask) / 2
        bd = sum(float(u["bid_price"]) * float(u["bid_size"]) for u in bids[:5])
        ad = sum(float(u["ask_price"]) * float(u["ask_size"]) for u in asks[:5])
        spread = (ask / bid - 1) * 10000
        self.book = (now, int(m["timestamp"]), units)
        self.history.append((now, int(m["timestamp"]), mid, bd, ad, spread))
        self.expire(now)

    def expire(self, now):
        # Keep one historical predecessor for the longest endpoint.
        while len(self.history) > 1 and self.history[1][0] <= now - 60000:
            self.history.popleft()
        for w, q in self.flow.items():
            while q[0] and q[0][0][0] <= now - w * 1000:
                _, value, sell = q[0].popleft()
                q[2 if sell else 1] -= value
                q[3] -= 1

    def features(self, now):
        self.expire(now)
        if (self.book is None or now - self.started < 60000
                or not 0 <= now - self.book[0] <= MAX_AGE_MS
                or not 0 <= now - self.book[1] <= MAX_AGE_MS):
            return None
        history = list(self.history)
        current = history[-1]
        mid, bd, ad, spread = current[2:]
        units = self.book[2]
        bids = sorted(units, key=lambda u: -float(u["bid_price"]))
        asks = sorted(units, key=lambda u: float(u["ask_price"]))
        bp, ap = float(bids[0]["bid_price"]), float(asks[0]["ask_price"])
        bs, ass = float(bids[0]["bid_size"]), float(asks[0]["ask_size"])
        f = {
            "spread_bp": spread,
            "obi1": (bs - ass) / (bs + ass) if bs + ass else 0.0,
            "obi5": (bd - ad) / (bd + ad) if bd + ad else 0.0,
            "bid_depth5_log": math.log1p(bd), "ask_depth5_log": math.log1p(ad),
            "microprice_bp": (((ap * bs + bp * ass) / (bs + ass)) / mid - 1) * 10000
                              if bs + ass else 0.0,
        }
        for w in DISCOVERY_WINDOWS:
            target = now - w * 1000
            prior = next((r for r in reversed(history) if r[0] <= target), None)
            f["ret" + str(w)] = (mid / prior[2] - 1
                                  if prior and 0 <= target - prior[1] <= MAX_AGE_MS else None)
            q = self.flow[w]
            total = max(0.0, q[1] + q[2])
            f["flow" + str(w)] = (q[1] - q[2]) / total if total > 1e-9 else 0.0
            if w in (5, 10, 60):
                f["trades" + str(w)] = float(q[3])
                f["turnover" + str(w) + "_log"] = math.log1p(total)
            if w in (5, 10):
                fresh = prior is not None and 0 <= target - prior[1] <= MAX_AGE_MS
                f["bid_depth_change" + str(w)] = bd / prior[3] - 1 if fresh and prior[3] > 0 else None
                f["spread_change" + str(w)] = spread - prior[5] if fresh else None
        for w in (10, 60):
            seq = [r for r in history if now - w * 1000 <= r[0] <= now]
            f["rv" + str(w)] = math.sqrt(sum(math.log(b[2] / a[2]) ** 2
                                                 for a, b in zip(seq, seq[1:])))
        recent = [r for r in history if r[0] >= now - 5000]
        low10 = min((r[2] for r in history if r[0] >= now - 10000), default=mid)
        f["bounce10"] = mid / low10 - 1
        f["bid_refill5"] = bd / min((r[3] for r in recent if r[3] > 0), default=bd or 1) - 1
        f["book_updates5"] = float(len(recent))
        f["flow_reversal"] = f["flow1"] - f["flow10"]
        total5 = max(0.0, self.flow[5][1] + self.flow[5][2])
        total10 = max(0.0, self.flow[10][1] + self.flow[10][2])
        f["turnover_acceleration"] = total5 / (max(0.0, total10 - total5) + 1.0)
        qty = buy_vwap(units, NOTIONAL)
        f["entry_cost_hint_bp"] = (NOTIONAL / qty / mid * (1 + FEE) - 1) * 10000 if qty else None
        seconds = (now // 1000) % 86400
        f["hour_sin"] = math.sin(2 * math.pi * seconds / 86400)
        f["hour_cos"] = math.cos(2 * math.pi * seconds / 86400)
        return {k: (round(v, 10) if v is not None and math.isfinite(v) else None) for k, v in f.items()}


def build_discovery_panel(files, database, markets, step_ms=5000):
    """One raw pass; independent broad opportunities, no shock/return filter."""
    import sqlite3
    database = Path(database)
    if database.exists():
        raise ValueError("panel exists; reuse it or choose another output folder")
    database.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    if shutil.disk_usage(str(database.parent)).free < 512 * 1024 * 1024:
        raise ValueError("less than 512MB free; choose a disk with room for the panel")
    connection = sqlite3.connect(str(database))
    connection.executescript("""
      CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE samples(id INTEGER PRIMARY KEY, market TEXT, date TEXT,
                           decision_recv_ts INTEGER, features TEXT);
      CREATE TABLE labels(sample_id INTEGER, horizon INTEGER, status TEXT, reason TEXT,
                          entry_recv_ts INTEGER, exit_recv_ts INTEGER, net REAL,
                          quantity REAL, cost_krw REAL,
                          PRIMARY KEY(sample_id,horizon));
      CREATE INDEX sample_time ON samples(decision_recv_ts,id);
    """)
    def meta(key, value):
        connection.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",
                           (key, json.dumps(value, allow_nan=False)))
    signature = {str(p): [p.stat().st_size, p.stat().st_mtime_ns] for p in files}
    config = {"revision": DISCOVERY_REVISION, "grid_ms": step_ms, "markets": list(markets),
              "horizons": list(HORIZONS_S), "principal": NOTIONAL, "fee": FEE,
              "latency_ms": LAT_MS, "max_age_ms": MAX_AGE_MS, "max_wait_ms": MAX_WAIT_MS}
    meta("state", "BUILDING")
    meta("config", config)
    meta("manifest", signature)
    connection.commit()
    states, active = {}, {}
    counts = collections.Counter()
    next_grid, serial, last_now = None, 0, None
    final_books, daily_books = {}, {}

    def finish(sample, h, status, reason="", exit_ts=None, value=None):
        entry = sample.get("entry")
        connection.execute("UPDATE labels SET status=?,reason=?,entry_recv_ts=?,exit_recv_ts=?,"
                           "net=?,quantity=?,cost_krw=? WHERE sample_id=? AND horizon=?",
                           (status, reason, entry["recv"] if entry else None, exit_ts, value,
                            entry["qty"] if entry else None, NOTIONAL * (1 + FEE),
                            sample["id"], h))
        sample["done"].add(h)
        counts["label_" + status] += 1

    def barrier(reason, market=None):
        for code in list(active):
            if market is not None and code != market:
                continue
            for sample in active.pop(code):
                for h in HORIZONS_S:
                    if h not in sample["done"]:
                        finish(sample, h, "UNKNOWN", reason)
        if reason != "end_of_input" and last_now is not None:
            day = dt.datetime.utcfromtimestamp(last_now / 1000).date().isoformat()
            if market is None:
                daily_books.pop(day, None)
            else:
                daily_books.get(day, {}).pop(market, None)
        if market is None:
            states.clear()
            final_books.clear()
        else:
            states.pop(market, None)
            final_books.pop(market, None)

    def expire(now):
        for code in list(active):
            for sample in active[code]:
                for h in HORIZONS_S:
                    if h in sample["done"]:
                        continue
                    entry = sample.get("entry")
                    deadline = (entry["recv"] + h * 1000 if entry else sample["target"]) + MAX_WAIT_MS
                    if now > deadline:
                        finish(sample, h, "UNKNOWN", "exit_quote_late_or_missing" if entry else
                               "entry_quote_late_or_missing")
            active[code] = [s for s in active[code] if len(s["done"]) < len(HORIZONS_S)]

    def grid(g):
        nonlocal serial
        date = dt.datetime.utcfromtimestamp(g / 1000).date().isoformat()
        for code in markets:
            counts["grid_opportunities"] += 1
            state = states.get(code)
            features = state.features(g) if state else None
            if features is None:
                counts["feature_unavailable"] += 1
                continue
            serial += 1
            connection.execute("INSERT INTO samples VALUES (?,?,?,?,?)",
                               (serial, code, date, g, json.dumps(features, allow_nan=False,
                                                                  separators=(",", ":"))))
            connection.executemany("INSERT INTO labels VALUES (?,?,?,?,?,?,?,?,?)",
                                   [(serial, h, "PENDING", "", None, None, None, None,
                                     NOTIONAL * (1 + FEE)) for h in HORIZONS_S])
            active.setdefault(code, []).append({"id": serial, "target": g + LAT_MS,
                                                 "entry": None, "done": set()})
            if serial % 1000 == 0:
                connection.commit()
            if serial % 10000 == 0 and shutil.disk_usage(str(database.parent)).free < 512 * 1024 * 1024:
                raise ValueError("panel stopped before filling disk; cache remains BUILDING")

    try:
        for date, m in iter_events(files, counts, markets):
            now = m.get("recv_ts")
            if now is not None:
                now = int(now)
                if last_now is not None and now < last_now:
                    barrier("receive_clock_rollback")
                    next_grid = ((now + step_ms - 1) // step_ms) * step_ms
                if next_grid is None:
                    next_grid = ((now + step_ms - 1) // step_ms) * step_ms
                # Strictly before this event; all receive-time ties at g are already applied.
                while next_grid < now:
                    expire(next_grid)
                    grid(next_grid)
                    next_grid += step_ms
                last_now = now
            if "_barrier" in m:
                barrier(m["_barrier"], m.get("market"))
                continue
            expire(now)
            code = m["code"]
            state = states.setdefault(code, DiscoveryState(now))
            state.apply(m)
            if m["type"] != "orderbook":
                continue
            units = m["orderbook_units"]
            mark = {"recv_ts": now, "timestamp": int(m["timestamp"]), "units": units}
            final_books[code] = mark
            mark_day = dt.datetime.utcfromtimestamp(now / 1000).date().isoformat()
            daily_books.setdefault(mark_day, {})[code] = mark
            for sample in active.get(code, ()):
                if sample["entry"] is None and now >= sample["target"]:
                    qty = buy_vwap(units, NOTIONAL)
                    if qty is None:
                        for h in HORIZONS_S:
                            if h not in sample["done"]:
                                finish(sample, h, "UNKNOWN", "entry_depth_insufficient")
                        continue
                    sample["entry"] = {"recv": now, "qty": qty}
                entry = sample["entry"]
                if entry is None:
                    continue
                for h in HORIZONS_S:
                    if h in sample["done"] or now < entry["recv"] + h * 1000:
                        continue
                    proceeds = sell_vwap(units, entry["qty"])
                    if proceeds is None:
                        finish(sample, h, "UNKNOWN", "exit_depth_insufficient", exit_ts=now)
                    else:
                        finish(sample, h, "RESOLVED", exit_ts=now,
                               value=net_return(NOTIONAL, proceeds))
        marks_at_end = dict(final_books)
        barrier("end_of_input")
        try:
            unchanged = signature == {str(p): [p.stat().st_size, p.stat().st_mtime_ns] for p in files}
        except OSError:
            unchanged = False
        meta("state", "READY" if unchanged and not counts["input_errors"] else "INCOMPLETE")
        meta("counts", dict(counts))
        meta("last_recv_ts", last_now)
        meta("final_books", marks_at_end)
        meta("daily_books", daily_books)
        meta("dates", sorted({p.parent.name for p in files}))
        meta("sample_count", serial)
        connection.commit()
    finally:
        connection.close()
    return serial


def discovery_metadata(database):
    import sqlite3
    connection = sqlite3.connect(str(database))
    try:
        return {key: json.loads(value) for key, value in connection.execute("SELECT key,value FROM metadata")}
    finally:
        connection.close()


def discovery_rows(database, horizon, dates=None):
    """Full chronological cache stream, including UNKNOWN; bounded reader memory."""
    import sqlite3
    meta = discovery_metadata(database)
    if meta.get("state") not in ("READY", "INCOMPLETE"):
        raise ValueError("interrupted panel cannot be used as a completed dataset")
    allowed = set(dates) if dates is not None else None
    connection = sqlite3.connect(str(database))
    try:
        query = ("SELECT s.id,s.market,s.date,s.decision_recv_ts,s.features,l.status,l.reason,"
                 "l.entry_recv_ts,l.exit_recv_ts,l.net,l.quantity,l.cost_krw FROM samples s "
                 "JOIN labels l ON s.id=l.sample_id WHERE l.horizon=? "
                 "ORDER BY s.decision_recv_ts,s.id")
        for row in connection.execute(query, (horizon,)):
            if allowed is not None and row[2] not in allowed:
                continue
            yield {"id": row[0], "market": row[1], "date": row[2], "decision_recv_ts": row[3],
                   "features": json.loads(row[4]), "status": row[5], "reason": row[6],
                   "entry_recv_ts": row[7], "exit_recv_ts": row[8], "net": row[9],
                   "quantity": row[10], "cost_krw": row[11]}
    finally:
        connection.close()

def _disc_num(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _disc_base(model, features):
    values, missing = [], []
    for name, center, scale in model["features"]:
        value = _disc_num(features.get(name))
        missing.append(float(value is None))
        values.append(max(-5.0, min(5.0, ((center if value is None else value) - center) / scale)))
    return values, missing


def _disc_product(values, columns):
    result = 1.0
    for col in columns:
        result *= values[col]
    return max(-5.0, min(5.0, result))


def _disc_design(model, features):
    values, missing = _disc_base(model, features)
    return values + missing + [_disc_product(values, cols) for cols in model["interactions"]]


def _disc_tree(matrix, target, indices, names, depth=0):
    n = len(indices)
    mean = sum(target[i] for i in indices) / n
    node = {"value": mean, "n": n, "win": sum(target[i] > 0 for i in indices) / n}
    minimum = max(20, min(100, len(target) // 30))
    if depth >= 3 or n < 2 * minimum:
        return node
    total = sum(target[i] for i in indices)
    total_sq = sum(target[i] ** 2 for i in indices)
    parent = total_sq - total * total / n
    best, gain = None, 1e-15
    for col in range(len(names)):
        order = sorted(indices, key=lambda i: matrix[i][col])
        left, square = 0.0, 0.0
        for pos, i in enumerate(order[:-1]):
            left += target[i]
            square += target[i] ** 2
            nl, nr = pos + 1, n - pos - 1
            lo, hi = matrix[i][col], matrix[order[pos + 1]][col]
            if nl < minimum or nr < minimum or lo == hi:
                continue
            error = square - left * left / nl
            error += total_sq - square - (total - left) ** 2 / nr
            if parent - error > gain:
                gain = parent - error
                best = col, (lo + hi) / 2, order[:nl], order[nl:]
    if best is not None:
        col, cut, left, right = best
        node.update(column=col, threshold=cut, feature=names[col],
                    left=_disc_tree(matrix, target, left, names, depth + 1),
                    right=_disc_tree(matrix, target, right, names, depth + 1))
    return node


def fit_discovery_model(rows, family="score", max_train=4000, seed=17):
    """All preprocessing/interaction search is fitted inside this TRAIN call."""
    import itertools
    if family not in ("score", "tree"):
        raise ValueError("unknown discovery family")
    rng, sample, seen = random.Random(seed), [], 0
    for row in rows:
        net = _disc_num(row.get("net"))
        if net is None or row.get("status", "RESOLVED") != "RESOLVED":
            continue
        seen += 1
        if len(sample) < max_train:
            sample.append(row)
        else:
            index = rng.randrange(seen)
            if index < max_train:
                sample[index] = row
    model = {"family": family, "fit_n": len(sample), "eligible_train_n": seen,
             "features": [], "interactions": [], "search_pairs": 0, "search_triples": 0}
    if not sample:
        model["constant"] = .5 if family == "score" else 0.0
        return model
    names = sorted({key for row in sample for key in row.get("features", {})})
    for name in names:
        finite = [_disc_num(r["features"].get(name)) for r in sample]
        finite = [v for v in finite if v is not None]
        if not finite:
            continue
        center = statistics.median(finite)
        scale = statistics.pstdev(finite) if len(finite) > 1 else 0.0
        model["features"].append([name, center, scale if scale > 1e-12 else 1.0])
    target = [float(r["net"]) for r in sample]
    wins = [float(v > 0) for v in target]
    model["training_mean_net"] = _mean(target)
    model["training_win"] = _mean(wins)
    if not model["features"]:
        model["constant"] = _mean(wins) if family == "score" else _mean(target)
        return model
    base = [_disc_base(model, row["features"])[0] for row in sample]
    if family == "tree":
        model["tree"] = _disc_tree(base, target, list(range(len(sample))),
                                   [x[0] for x in model["features"]])
        return model
    prior = (sum(wins) + 1) / (len(wins) + 2)
    if min(wins) == max(wins):
        model["constant"] = prior
        return model
    # Screen ALL pairs and triples, including features with no marginal association.
    # Use an outcome-independent subset of the representative TRAIN reservoir.
    screen_n = min(800, len(sample))
    y = wins[:screen_n]
    ym = _mean(y)
    yss = sum((v - ym) ** 2 for v in y)
    for degree, keep, key in ((2, 24, "search_pairs"), (3, 12, "search_triples")):
        ranked = []
        for cols in itertools.combinations(range(len(model["features"])), degree):
            model[key] += 1
            values = [_disc_product(v, cols) for v in base[:screen_n]]
            xm = _mean(values)
            xss = sum((v - xm) ** 2 for v in values)
            cross = sum((v - xm) * (label - ym) for v, label in zip(values, y))
            association = abs(cross) / math.sqrt(xss * yss) if xss > 1e-12 and yss > 0 else 0.
            if association > 1e-12:
                ranked.append((association, list(cols)))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        model["interactions"].extend(cols for _, cols in ranked[:keep])
    model["interaction_screen_n"] = screen_n
    matrix = [_disc_design(model, r["features"]) for r in sample]
    weights = [0.] * len(matrix[0])
    bias = math.log(prior / (1 - prior))
    for iteration in range(100):
        grad, intercept = [0.] * len(weights), 0.
        for values, label in zip(matrix, wins):
            raw = bias + sum(w * v for w, v in zip(weights, values))
            prob = 1 / (1 + math.exp(-max(-35., min(35., raw))))
            error = prob - label
            intercept += error
            for j, value in enumerate(values):
                grad[j] += error * value
        rate = .8 / (len(weights) + 1) / math.sqrt(1 + iteration / 50)
        bias -= rate * intercept / len(sample)
        for j in range(len(weights)):
            weights[j] -= rate * (grad[j] / len(sample) + .03 * weights[j])
    model.update(weights=weights, bias=bias)
    return model


def predict_discovery_model(model, features):
    if "constant" in model:
        return model["constant"]
    if model["family"] == "tree":
        values, _ = _disc_base(model, features)
        node = model["tree"]
        while "left" in node:
            node = node["left"] if values[node["column"]] <= node["threshold"] else node["right"]
        return node["value"]
    raw = model["bias"] + sum(w * x for w, x in zip(model["weights"], _disc_design(model, features)))
    return 1 / (1 + math.exp(-max(-35., min(35., raw))))


def discovery_profiles(rows):
    """Whole TRAIN stream, both classes; no winner-only fitted win probabilities."""
    totals = collections.Counter()
    values = {}
    for row in rows:
        if row.get("status") != "RESOLVED":
            continue
        label = "win" if row["net"] > 0 else "loss"
        totals[label] += 1
        for name, value in row["features"].items():
            v = _disc_num(value)
            if v is None:
                continue
            stat = values.setdefault(name, {}).setdefault(label, [0, 0., 0.])
            stat[0] += 1
            stat[1] += v
            stat[2] += v * v
    out = {}
    for name, groups in values.items():
        record = {}
        for label in ("win", "loss"):
            n, total, sq = groups.get(label, [0, 0., 0.])
            record[label] = {"n": n, "mean": total / n if n else None,
                             "sd": math.sqrt(max(0., sq / n - (total / n) ** 2)) if n else None}
        out[name] = record
    return {"classes": dict(totals), "features": out}

def explain_discovery_model(model):
    if "constant" in model:
        return ["constant=" + str(model["constant"])]
    if model["family"] == "score":
        names = [f"z({x[0]})" for x in model["features"]]
        names += [f"missing({x[0]})" for x in model["features"]]
        names += ["*".join("z(" + model["features"][i][0] + ")" for i in cols)
                  for cols in model["interactions"]]
        lines = [f"logistic raw score = {model['bias']:.8g} + weighted terms",
                 "z uses TRAIN median/standard deviation and clipping [-5,5]. "
                 "Scores are not calibrated win-rate guarantees."]
        lines += [f"{weight:+.8g} * {name}" for weight, name in
                  sorted(zip(model["weights"], names), key=lambda x: -abs(x[0]))[:30]]
        return lines
    lines = []
    def visit(node, path):
        if "left" not in node:
            lines.append(" AND ".join(path or ["ALL"]) +
                         f" => TRAIN mean_net={node['value']:+.6f} win={node['win']:.3f} n={node['n']}")
            return
        name, center, scale = model["features"][node["column"]]
        cut = center + scale * node["threshold"]
        visit(node["left"], path + [name + "<=" + str(round(cut, 8))])
        visit(node["right"], path + [name + ">" + str(round(cut, 8))])
    visit(model["tree"], [])
    return lines

class DiscoveryAccount:
    """Fee-inclusive cash reserved before inspecting eventual outcome availability."""
    def __init__(self, capital, dates, metadata, cutoff):
        self.capital = float(capital)
        self.cash = float(capital)
        self.dates = list(dates)
        self.metadata = metadata
        self.cutoff = cutoff
        self.positions, self.queue = {}, []
        self.closed = self.wins = self.selected = self.unknown = 0
        self.skipped_cash = self.skipped_overlap = 0
        self.realized = self.gains = self.losses = 0.
        self.worst = None
        self.day_realized = collections.defaultdict(float)
        self.day_closed = collections.Counter()
        self.equity = []
        self.next_day = 0
        self.serial = 0

    def _release(self, now):
        import heapq
        while self.queue and self.queue[0][0] <= now:
            ts, _, code, row = heapq.heappop(self.queue)
            self.positions.pop(code, None)
            profit = row["cost_krw"] * row["net"]
            self.cash += row["cost_krw"] + profit
            self.realized += profit
            self.closed += 1
            self.wins += row["net"] > 0
            self.gains += max(0., profit)
            self.losses += min(0., profit)
            self.worst = row["net"] if self.worst is None else min(self.worst, row["net"])
            day = dt.datetime.utcfromtimestamp(ts / 1000).date().isoformat()
            self.day_realized[day] += profit
            self.day_closed[day] += 1

    def _value(self, day, now):
        total = self.cash
        marks = self.metadata.get("daily_books", {}).get(day, {})
        for code, row in self.positions.items():
            qty = row.get("quantity")
            mark = marks.get(code)
            if (qty is None or mark is None or row.get("entry_recv_ts") is None
                    or row["entry_recv_ts"] > now or mark["recv_ts"] < row["entry_recv_ts"]
                    or not 0 <= now - mark["timestamp"] <= MAX_AGE_MS):
                return None
            proceeds = sell_vwap(mark["units"], qty)
            if proceeds is None:
                return None
            total += proceeds * (1 - FEE)
        return total

    def _days_until(self, now):
        while self.next_day < len(self.dates):
            day = self.dates[self.next_day]
            end = int(dt.datetime.combine(dt.date.fromisoformat(day) + dt.timedelta(days=1),
                                          dt.time(), tzinfo=dt.timezone.utc).timestamp() * 1000)
            end = min(end, self.metadata.get("last_recv_ts") or end)
            if now < end:
                break
            self._release(end)
            self.equity.append((day, self._value(day, end)))
            self.next_day += 1

    def offer(self, row, score):
        import heapq
        now = row["decision_recv_ts"]
        self._days_until(now)
        self._release(now)
        if score < self.cutoff:
            return
        self.selected += 1
        code, cost = row["market"], NOTIONAL * (1 + FEE)
        if code in self.positions:
            self.skipped_overlap += 1
            return
        if self.cash + 1e-9 < cost:
            self.skipped_cash += 1
            return
        # This decision uses only model score, current cash and open positions.
        self.cash -= cost
        self.positions[code] = row
        if (row["status"] == "RESOLVED" and row.get("net") is not None
                and row.get("entry_recv_ts") is not None and row.get("exit_recv_ts") is not None
                and now <= row["entry_recv_ts"] <= row["exit_recv_ts"]):
            self.serial += 1
            heapq.heappush(self.queue, (row["exit_recv_ts"], self.serial, code, row))
        else:
            # Never recycle unknown future exposure as free capital.
            self.unknown += 1

    def summary(self):
        final = min(
            int(dt.datetime.combine(dt.date.fromisoformat(self.dates[-1]) + dt.timedelta(days=1),
                                    dt.time(), tzinfo=dt.timezone.utc).timestamp() * 1000),
            self.metadata.get("last_recv_ts") or 10**18)
        self._days_until(final)
        self._release(final)
        equity = self._value(self.dates[-1], final)
        complete = self.unknown == 0 and equity is not None and all(v is not None for _, v in self.equity)
        peak, mdd, previous = self.capital, 0., self.capital
        day_pnl = []
        for _, value in self.equity:
            if value is not None:
                peak = max(peak, value)
                mdd = min(mdd, value / peak - 1)
                day_pnl.append(value - previous)
                previous = value
        result = {
            "capital": self.capital, "closed": self.closed, "selected": self.selected,
            "unknown_selected": self.unknown, "cash_skips": self.skipped_cash,
            "overlap_skips": self.skipped_overlap, "complete": complete,
            "win_rate": self.wins / self.closed if self.closed else None,
            "mean_net_frac": self.realized / (NOTIONAL * (1 + FEE) * self.closed) if self.closed else None,
            "mean_win_krw": self.gains / self.wins if self.wins else None,
            "mean_loss_krw": self.losses / (self.closed - self.wins) if self.closed > self.wins else None,
            "known_realized_krw": self.realized,
            "net_krw": equity - self.capital if complete else None,
            "daily_close_mdd_frac": mdd if complete else None,
            "worst_closed_trade_frac": self.worst,
            "days": len(self.dates), "traded_days": len(self.day_closed),
            "positive_realized_days": sum(self.day_realized[d] > 0 for d in self.dates),
            "equity": self.equity, "unresolved_positions": len(self.positions),
            "day_net_ci_low_krw": None,
            "closed_per_calendar_day": self.closed / len(self.dates),
            "monthly_linear_krw_proxy": (equity - self.capital) * 30 / len(self.dates) if complete else None,
        }
        if complete and len(day_pnl) >= 2:
            rng = random.Random(41)
            values = sorted(_mean(rng.choices(day_pnl, k=len(day_pnl))) for _ in range(500))
            result["day_net_ci_low_krw"] = values[25]
        return result


def _discovery_reservoir(rows, boundary, limit=4000):
    rng, selected, seen = random.Random(17), [], 0
    for row in rows:
        if (row["status"] != "RESOLVED" or row.get("net") is None
                or row.get("exit_recv_ts") is None or row["exit_recv_ts"] >= boundary):
            continue
        seen += 1
        if len(selected) < limit:
            selected.append(row)
        else:
            index = rng.randrange(seen)
            if index < limit:
                selected[index] = row
    return selected, seen


def discovery_fold_dates(dates):
    """Each fold chooses on past validation dates, then evaluates later dates."""
    return [(dates[:start - 2], dates[start - 2:start], dates[start:start + 2])
            for start in range(5, len(dates), 2)]


def _discovery_boundary(day):
    return int(dt.datetime.combine(dt.date.fromisoformat(day), dt.time(),
                                   tzinfo=dt.timezone.utc).timestamp() * 1000)


def _discovery_evaluate(database, horizon, dates, metadata, policies):
    accounts = [DiscoveryAccount(p.get("capital", 1000000), dates, metadata, p["cutoff"])
                for p in policies]
    for row in discovery_rows(database, horizon, dates):
        scores = {}
        for policy, account in zip(policies, accounts):
            key = policy["model_key"]
            if key not in scores:
                scores[key] = predict_discovery_model(policy["model"], row["features"])
            account.offer(row, scores[key])
    return [account.summary() for account in accounts]


def analyze_discovery(database, out_dir):
    """Outcome-first TRAIN fitting, nested past selection, forward historical audit."""
    metadata = discovery_metadata(database)
    if metadata.get("config", {}).get("revision") != DISCOVERY_REVISION:
        raise ValueError("cache schema/revision mismatch")
    dates = metadata["dates"]
    folds = discovery_fold_dates(dates)
    report = {"revision": DISCOVERY_REVISION, "status": "EXPLORATION_ONLY",
              "input_state": metadata["state"], "sample_count": metadata["sample_count"],
              "dates": dates, "cache_counts": metadata.get("counts", {}), "folds": [],
              "fresh_independent_validation": False, "h3_combined_evaluated": False,
              "notional_principal": NOTIONAL, "capital_scenarios": [1000000, 2000000],
              "limitations": ["historical dates already examined", "displayed-depth fill proxy",
                              "uncalibrated model scores", "finite model/search budget",
                              "daily-close drawdown, not intraday drawdown",
                              "unknown selected exposures keep capital reserved",
                              "no final profitability PASS or actual orders"]}
    latest_profiles = {}
    for fold_number, (train, validation, evaluation) in enumerate(folds, 1):
        print(f"[discovery] fold {fold_number}/{len(folds)} TRAIN={len(train)} "
              f"SELECT={validation} CHECK={evaluation}", flush=True)
        candidates = []
        for horizon in HORIZONS_S:
            fit_rows, eligible = _discovery_reservoir(discovery_rows(database, horizon, train),
                                                       _discovery_boundary(validation[0]))
            if len(fit_rows) < 100:
                continue
            models = {}
            policies = []
            for family in ("score", "tree"):
                model = fit_discovery_model(fit_rows, family)
                model["eligible_train_n"] = eligible
                models[family] = model
                scores = sorted(predict_discovery_model(model, row["features"]) for row in fit_rows)
                for q in (.5, .7, .8, .9, .95):
                    cut = scores[int(q * (len(scores) - 1))]
                    policies.append({"family": family, "quantile": q, "cutoff": cut,
                                     "model_key": family, "model": model, "horizon": horizon})
            summaries = _discovery_evaluate(database, horizon, validation, metadata, policies)
            for policy, summary in zip(policies, summaries):
                candidates.append(dict(policy, validation=summary))
            if horizon == 15 and fold_number == len(folds):
                latest_profiles = discovery_profiles(
                    row for row in discovery_rows(database, horizon, train)
                    if row.get("exit_recv_ts") is not None
                    and row["exit_recv_ts"] < _discovery_boundary(validation[0]))
        supported = [p for p in candidates if p["validation"]["closed"] >= 20
                     and p["validation"]["traded_days"] >= 2]
        by_profit = sorted(supported, key=lambda p: (
            p["validation"]["complete"], p["validation"]["net_krw"]
            if p["validation"]["complete"] else p["validation"]["known_realized_krw"]), reverse=True)
        by_win = sorted(supported, key=lambda p: (p["validation"]["win_rate"],
                                                  p["validation"]["closed"]), reverse=True)
        # Select using validation only. Never rerank these policies on CHECK outcomes.
        picks = []
        for reason, choices in (("validation_net", by_profit[:3]), ("validation_win_rate", by_win[:1])):
            for candidate in choices:
                if any((x["family"], x["quantile"], x["horizon"]) ==
                       (candidate["family"], candidate["quantile"], candidate["horizon"]) for x in picks):
                    continue
                picks.append(dict(candidate, selection_reason=reason))
        fold = {"train_dates": train, "selection_dates": validation, "check_dates": evaluation,
                "candidate_count": len(candidates), "supported_count": len(supported),
                "selected": [], "status": "NO_SUPPORTED_CONDITION" if not picks else "EXPLORATORY_CONDITIONS"}
        for candidate in picks:
            checks = []
            for capital in (1000000, 2000000):
                policy = dict(candidate, capital=capital)
                checks.append(_discovery_evaluate(database, candidate["horizon"], evaluation,
                                                  metadata, [policy])[0])
            fold["selected"].append({k: v for k, v in candidate.items() if k != "model_key"})
            fold["selected"][-1]["check_accounts"] = checks
        # Baseline uses the same grid and cost model with no predictive selection.
        baseline = {"model_key": "constant", "model": {"constant": 1., "family": "score"},
                    "cutoff": 0.}
        fold["baseline_15s"] = _discovery_evaluate(database, 15, evaluation, metadata, [baseline])[0]
        report["folds"].append(fold)
    report["winner_loser_profiles_last_train"] = latest_profiles
    report["selection_rule"] = ("TRAIN-only preprocessing and interactions; past two-date selection; "
                                "next two dates checked; no ranking on CHECK")
    out = Path(out_dir)
    (out / "discovery_report.json").write_text(json.dumps(report, indent=1, allow_nan=False), encoding="utf-8")

    import csv
    columns = ["fold", "family", "horizon", "quantile", "reason", "capital", "closed",
               "win_rate", "mean_net_frac", "mean_win_krw", "mean_loss_krw", "net_krw",
               "unknown_selected", "daily_close_mdd_frac", "closed_per_calendar_day",
               "monthly_linear_krw_proxy", "complete"]
    with open(out / "conditions.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for number, fold in enumerate(report["folds"], 1):
            for candidate in fold["selected"]:
                for account in candidate["check_accounts"]:
                    row = {key: account.get(key) for key in columns}
                    row.update(fold=number, family=candidate["family"], horizon=candidate["horizon"],
                               quantile=candidate["quantile"], reason=candidate["selection_reason"])
                    writer.writerow(row)
    explanations = ["TRAIN-fitted conditions; selection used past dates only. All results remain exploratory."]
    for number, fold in enumerate(report["folds"], 1):
        for candidate in fold["selected"]:
            explanations.append(f"\nFOLD {number} {candidate['family']} h={candidate['horizon']} "
                                f"score>={candidate['cutoff']:.8g}")
            explanations.extend(explain_discovery_model(candidate["model"]))
    (out / "learned_conditions.txt").write_text("\n".join(explanations) + "\n", encoding="utf-8")

    if report["folds"]:
        last = report["folds"][-1]
        profiles = report["winner_loser_profiles_last_train"].get("classes", {})
        lines = ["DISCOVERY_MODEL: " + DISCOVERY_REVISION,
                 f"DATA: {dates[0]}..{dates[-1]} samples={metadata['sample_count']}",
                 "STATUS: EXPLORATION_ONLY | independent future validation pending",
                 "TRAIN_CLASSES: " + json.dumps(profiles, sort_keys=True),
                 f"WALK_FORWARD_FOLDS: {len(folds)} | last supported conditions={last['supported_count']}"]
        for candidate in last["selected"][:2]:
            check = candidate["check_accounts"][0]
            wr = check["win_rate"]
            lines.append(f"{candidate['family']} h={candidate['horizon']} q={candidate['quantile']} "
                         f"CHECK_n={check['closed']} WR={wr:.3f} "
                         f"netKRW={check['net_krw']} unknown={check['unknown_selected']}"
                         if wr is not None else f"{candidate['family']} CHECK_n=0")
        lines.append("Full report includes learned weights/interactions, trees, profiles and account uncertainty.")
    else:
        lines = ["DISCOVERY_MODEL: " + DISCOVERY_REVISION, "STATUS: INSUFFICIENT_DATES",
                 "Need at least six ordered dates for a past-selection/later-check fold."]
    (out / "discovery_brief.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return report


def run_discovery(files, out, markets):
    files = [Path(p).resolve() for p in files]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    database = out / "panel.sqlite"
    if database.exists():
        metadata = discovery_metadata(database)
        signature = {str(p): [p.stat().st_size, p.stat().st_mtime_ns] for p in files}
        if (metadata.get("state") not in ("READY", "INCOMPLETE")
                or metadata.get("manifest") != signature
                or metadata.get("config", {}).get("revision") != DISCOVERY_REVISION
                or metadata.get("config", {}).get("markets") != list(markets)
                or metadata.get("config", {}).get("principal") != NOTIONAL
                or metadata.get("config", {}).get("fee") != FEE
                or metadata.get("config", {}).get("latency_ms") != LAT_MS
                or metadata.get("config", {}).get("max_age_ms") != MAX_AGE_MS
                or metadata.get("config", {}).get("max_wait_ms") != MAX_WAIT_MS):
            raise ValueError("existing cache is incomplete or has different inputs; choose a new output folder")
        print("[discovery] reusing cached feature/label panel; raw archive not replayed", flush=True)
    else:
        print("[discovery] extracting pre-decision features and fee-adjusted labels once", flush=True)
        build_discovery_panel(files, database, markets)
    return analyze_discovery(database, out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root_pos", nargs="?")
    ap.add_argument("-r", "--root", dest="root_opt")
    ap.add_argument("-o", "--out", default="se_result_checked")
    ap.add_argument("-d", "--days", type=int, default=0)
    ap.add_argument("-t", "--train-days", type=int, default=TRAIN_DAYS)
    ap.add_argument("--markets", default=",".join(MARKETS))
    ap.add_argument("-e", "--before-date", help="exclusive UTC date bound; raw files stay unchanged")
    ap.add_argument("-D", "--discover", action="store_true", help="outcome-first feature and interaction discovery; separate cached output")
    a = ap.parse_args(argv)
    root = a.root_opt or a.root_pos
    if not root:
        ap.error("specify one archive root, e.g. ws_server\\ws")
    if a.days < 0 or a.train_days < 1:
        ap.error("--days must be >=0 and --train-days >=1")
    if a.before_date is not None:
        try:
            if dt.date.fromisoformat(a.before_date).isoformat() != a.before_date:
                raise ValueError("noncanonical date")
        except ValueError:
            ap.error("--before-date must be YYYY-MM-DD")
    files, dates = archive_files(root, a.days or None, a.before_date)
    out = Path(a.out)
    if Path(root).resolve() == out.resolve() or Path(root).resolve() in out.resolve().parents:
        ap.error("output must be outside the input archive")
    markets = tuple(x.strip() for x in a.markets.split(",") if x.strip())
    if not markets:
        ap.error("--markets must contain at least one market")
    if a.discover:
        run_discovery(files, out, markets)
        return 0
    manifest = _manifest(files)
    train_dates = dates[:a.train_days] if len(dates) > a.train_days else []
    print(f"H17 EXPLORATION ONLY | model={MODEL_REVISION} | {len(files)} files | {dates[0]}..{dates[-1]} | "
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
    report = {"schema": 3, "model_revision": MODEL_REVISION, "event_rejection_scope": "market",
              "before_date_exclusive": a.before_date, "status": "EXPLORATION_ONLY", "input_quality": quality,
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
    for summary in res:
        if summary["primary"]:
            lines.append("UNKNOWN_REASONS " + summary["phase"] + ": "
                         + json.dumps(summary["unknown_reasons"], sort_keys=True))
    lines.append(f"Results: {out}/report.json {out}/rows.json")
    (out / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    brief = ["H17_MODEL: " + MODEL_REVISION,
             f"DATA: {dates[0]}..{dates[-1]} | dates={len(dates)} files={len(files)}",
             "QUALITY: " + quality + " | PROFIT: NOT_VALIDATED"]
    for summary in res:
        if summary["primary"]:
            brief.append(summary["phase"] + " B15 " + _fmt(summary))
    for summary in res:
        if summary["primary"]:
            brief.append(summary["phase"] + " UNKNOWN: "
                         + json.dumps(summary["unknown_reasons"], sort_keys=True))
    brief.append("SCOPE: exploratory completed outcomes; funded portfolio not evaluated")
    (out / "brief.txt").write_text("\n".join(brief) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
