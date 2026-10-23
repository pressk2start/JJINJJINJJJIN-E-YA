#!/usr/bin/env python3
"""trend_paper.py - H3 BTC/ETH SMA50 trend, PAPER ONLY (no keys, no orders). Public Upbit daily candles.

Rule (frozen 2026-10-23): at each completed daily close (09:00 KST), if close > 50-day average -> hold that coin
from the next open, else cash. 50% of capital per coin. Cost per side 0.15% (fee 0.05% + slippage 0.10%).
Forward record starts 2026-10-24. Results are recomputed from public data every run, so no state file is needed:
run it any day (e.g. once a week) and it shows today's action plus the paper result since the start.

Usage (PC):  py trend_paper.py
"""
import datetime as dt
import json
import time
import urllib.request

ASSETS = ("KRW-BTC", "KRW-ETH")
N = 50
START = "2026-10-24"
BUY = (1 + 0.0010) * (1 + 0.0005)
SELL = (1 - 0.0010) * (1 - 0.0005)


def candles(m, count=200, pages=2):
    out, to = {}, None
    for _ in range(pages):
        url = f"https://api.upbit.com/v1/candles/days?market={m}&count={count}" + (f"&to={to}" if to else "")
        rows = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=20))
        time.sleep(0.15)
        if not rows:
            break
        for r in rows:
            out[r["candle_date_time_utc"][:10]] = (float(r["opening_price"]), float(r["trade_price"]))
        to = min(r["candle_date_time_utc"] for r in rows)[:19] + "Z"
    return out


def main():
    today_utc = dt.datetime.now(dt.timezone.utc).date().isoformat()
    print(f"H3 TREND PAPER (no orders) | now UTC {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} | forward from {START}")
    legs = {}
    for m in ASSETS:
        s = candles(m)
        days = sorted(d for d in s if d < today_utc)          # completed candles only
        sig = {}
        for i in range(N - 1, len(days)):
            avg = sum(s[days[j]][1] for j in range(i - N + 1, i + 1)) / N
            sig[days[i]] = (s[days[i]][1] > avg, s[days[i]][1], avg)
        last = days[-1]
        on, close, avg = sig[last]
        prev_on = sig[days[-2]][0]
        act = ("BUY" if not prev_on else "KEEP HOLDING") if on else ("SELL" if prev_on else "STAY IN CASH")
        print(f"{m}: last close {last} = {close:,.0f}  avg50 = {avg:,.0f}  ({(close/avg-1)*100:+.2f}%)  -> at the 09:00 KST open after that close: {act}")
        # paper record from START
        eq, pos, trades, bh0 = 1.0, False, [], None
        alld = sorted(s)
        for i in range(len(alld) - 1):
            d, nxt = alld[i], alld[i + 1]
            if d < START or alld[i - 1] not in sig:
                continue
            want = sig[alld[i - 1]][0]                      # decided at previous close, executed at open d
            if bh0 is None:
                bh0 = s[d][0]
            if want and not pos:
                eq /= BUY; trades.append((d, "BUY", s[d][0]))
            if not want and pos:
                eq *= SELL; trades.append((d, "SELL", s[d][0]))
            pos = want
            if pos:
                eq *= s[nxt][0] / s[d][0]
        bh = (s[alld[-1]][0] / bh0 - 1) if bh0 else 0.0
        legs[m] = (eq - 1, bh, on, trades)
    print("\nPAPER RESULT since", START, "(each coin = 50% of capital; open-to-open, costs included)")
    tot, tbh = 0.0, 0.0
    for m, (r, bh, pos, trades) in legs.items():
        tot += r / 2; tbh += bh / 2
        print(f"  {m}: strategy {r*100:+.2f}%  buy&hold {bh*100:+.2f}%  position after today's open: {'HOLD' if pos else 'CASH'}  trades {len(trades)}")
        for t in trades[-5:]:
            print(f"      {t[0]} {t[1]} @ {t[2]:,.0f}")
    print(f"  TOTAL: strategy {tot*100:+.2f}%  buy&hold {tbh*100:+.2f}%")
    print("\nPaper only. Not a live order. Past results do not guarantee future results.")


if __name__ == "__main__":
    main()
