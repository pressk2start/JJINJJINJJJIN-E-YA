#!/usr/bin/env python3
"""H3 BTC/ETH SMA50 PAPER ONLY. Public daily candles; no keys or orders.

Frozen rule: completed UTC daily close > its 50-calendar-day SMA -> hold
from the next daily open; otherwise cash. START=2026-10-24 means 09:00 KST.
Each asset has one half of INITIAL capital, in an independent cash/coin
sleeve. No cross-asset daily rebalancing. Fee 0.05% and adverse price 0.10%
per executed side. The benchmark uses the same entry/exit assumptions.

This is a reconstruction using daily OPEN PRICE PROXIES, not an executable
profit test or a record of actual timely decisions. Latest valuation is
the latest UTC daily open, after that open's hypothetical transactions.
Unfinished daily CLOSE values never enter a decision.
Usage on a personal PC: py trend_paper.py
"""
import datetime as dt
import json
import math
import time
import urllib.parse
import urllib.request

ASSETS = ("KRW-BTC", "KRW-ETH")
N = 50
START = "2026-10-24"
BUY = (1 + 0.0010) * (1 + 0.0005)
SELL = (1 - 0.0010) * (1 - 0.0005)


def _shift(day, amount):
    return (dt.date.fromisoformat(day) + dt.timedelta(days=amount)).isoformat()


def _price(value):
    if isinstance(value, bool):
        raise ValueError("boolean price")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("nonfinite or nonpositive price")
    return number


def signal_for_open(data, day, n=N):
    """Only the n COMPLETED calendar closes strictly before day are used."""
    if type(n) is not int or n < 1:
        raise ValueError("invalid SMA window")
    closes = []
    for offset in range(n, 0, -1):
        previous = _shift(day, -offset)
        if previous not in data:
            raise ValueError("missing completed close: " + previous)
        closes.append(_price(data[previous][1]))
    average = sum(closes) / n
    return {"on": closes[-1] > average, "close": closes[-1],
            "average": average, "decision_day": _shift(day, -1)}


def simulate_leg(data, start, end, n=N):
    """Mark old holdings to each open, then execute that open's decision."""
    dt.date.fromisoformat(start)
    dt.date.fromisoformat(end)
    if start > end:
        return {"status": "NOT_STARTED", "equity": 1.0, "position": False,
                "trades": [], "latest_action": "WAIT_FOR_START",
                "benchmark_equity": 1.0, "liquidation_equity": 1.0,
                "benchmark_liquidation_equity": 1.0, "mark_day": None}

    equity, position, trades = 1.0, False, []
    previous_open, first_open = None, None
    day = start
    while day <= end:
        if day not in data:
            raise ValueError("missing open: " + day)
        opening = _price(data[day][0])
        decision = signal_for_open(data, day, n)
        if previous_open is not None and position:
            equity *= opening / previous_open
        if first_open is None:
            first_open = opening

        want = decision["on"]
        if want and not position:
            equity /= BUY
            action = "BUY"
            trades.append({"day": day, "action": action, "open": opening})
        elif position and not want:
            equity *= SELL
            action = "SELL"
            trades.append({"day": day, "action": action, "open": opening})
        else:
            action = "KEEP_HOLDING" if position else "STAY_IN_CASH"
        position = want
        previous_open = opening
        day = _shift(day, 1)

    benchmark = opening / first_open / BUY
    return {"status": "OPEN_PRICE_PROXY", "equity": equity,
            "position": position, "trades": trades, "latest_action": action,
            "benchmark_equity": benchmark,
            "liquidation_equity": equity * SELL if position else equity,
            "benchmark_liquidation_equity": benchmark * SELL,
            "mark_day": end}


def portfolio_result(legs):
    """Two equal INITIAL sleeves, not an average of unequal live allocations."""
    if set(legs) != set(ASSETS):
        raise ValueError("both fixed assets are required")
    statuses = {leg["status"] for leg in legs.values()}
    if statuses == {"NOT_STARTED"}:
        return {"status": "NOT_STARTED"}
    if statuses != {"OPEN_PRICE_PROXY"}:
        raise ValueError("incomplete portfolio")
    if len({leg["mark_day"] for leg in legs.values()}) != 1:
        raise ValueError("different valuation dates")
    return {"status": "OPEN_PRICE_PROXY", **{
        field: sum(legs[m][field] for m in ASSETS) / 2
        for field in ("equity", "benchmark_equity", "liquidation_equity",
                      "benchmark_liquidation_equity")}}


def _read_json(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def candles(m, through=None, start=START, reader=None):
    """Fetch through START-50, rather than silently retaining only 400 bars."""
    through = through or dt.datetime.now(dt.timezone.utc).date().isoformat()
    needed = _shift(min(start, through), -N)
    read = reader or _read_json
    out, cursor = {}, None
    for _ in range(100):
        params = {"market": m, "count": 200}
        if cursor is not None:
            params["to"] = cursor + "T00:00:00Z"
        rows = read("https://api.upbit.com/v1/candles/days?" +
                    urllib.parse.urlencode(params))
        if reader is None:
            time.sleep(0.15)
        if not isinstance(rows, list) or not rows:
            raise ValueError("daily history unavailable before " + needed)
        page_dates = []
        for row in rows:
            stamp = dt.datetime.fromisoformat(row["candle_date_time_utc"])
            if stamp.tzinfo is not None or stamp.time() != dt.time():
                raise ValueError("unexpected daily candle boundary")
            day = stamp.date().isoformat()
            if row.get("market", m) != m or day > through:
                raise ValueError("unexpected candle market/date")
            prices = (_price(row["opening_price"]), _price(row["trade_price"]))
            if day in out and out[day] != prices:
                raise ValueError("conflicting duplicate candle: " + day)
            out[day] = prices
            page_dates.append(day)
        oldest = min(page_dates)
        if cursor is not None and oldest >= cursor:
            raise ValueError("candle pagination made no progress")
        if oldest <= needed:
            return out
        cursor = oldest
    raise ValueError("history safety limit reached; no partial result calculated")


def main():
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    print(f"H3 TREND PAPER ONLY | UTC {now:%Y-%m-%d %H:%M:%S} | START {START} 00:00 UTC / 09:00 KST")
    print("OPEN PRICE PROXY; timely decision and execution NOT verified.")
    print("50% initial capital per asset; no cross-asset rebalancing.")
    legs = {}
    for market in ASSETS:
        try:
            data = candles(market, through=today)
            result = simulate_leg(data, START, today)
            legs[market] = result
            if result["status"] == "NOT_STARTED":
                print(f"{market}: NOT_STARTED; paper position=CASH; no performance yet")
                continue
            decision = signal_for_open(data, today)
            print(f"{market}: previous close {decision['decision_day']}={decision['close']:,.0f}"
                  f" SMA50={decision['average']:,.0f}")
            print(f"  hypothetical action at {today} 09:00 KST: {result['latest_action']}"
                  f" | paper position={'HOLD' if result['position'] else 'CASH'}")
            print(f"  mark-to-open strategy={(result['equity']-1)*100:+.4f}%"
                  f" benchmark={(result['benchmark_equity']-1)*100:+.4f}%"
                  f" | transactions={len(result['trades'])}")
            for trade in result["trades"][-5:]:
                print(f"    {trade['day']} {trade['action']} @ open proxy {trade['open']:,.0f}")
        except (ValueError, TypeError, KeyError, IndexError, OSError) as error:
            print(f"{market}: UNKNOWN ({type(error).__name__}: {error}); not zero")
    if set(legs) != set(ASSETS):
        print("TOTAL: UNKNOWN; incomplete input, no portfolio return")
        return 1
    total = portfolio_result(legs)
    if total["status"] == "NOT_STARTED":
        print("TOTAL: NOT_STARTED. First scheduled paper open: 2026-10-24 09:00 KST.")
        return 0
    print(f"TOTAL mark-to-open: strategy={(total['equity']-1)*100:+.4f}%"
          f" benchmark={(total['benchmark_equity']-1)*100:+.4f}%")
    print(f"TOTAL hypothetical liquidation at the same open:"
          f" strategy={(total['liquidation_equity']-1)*100:+.4f}%"
          f" benchmark={(total['benchmark_liquidation_equity']-1)*100:+.4f}%")
    print("Mark-to-open includes executed-side model costs; liquidation additionally models closing held coins.")
    print("Recomputed public-price simulation, not actual orders or guaranteed profit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
