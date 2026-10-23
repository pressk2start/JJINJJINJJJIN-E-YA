#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""leadlag_peek — leadlag.py collect 로 모은 BTC·ETH 데이터를 읽어 '바이낸스 선행' 여부를 요약. 읽기 전용.

수집 데이터가 수십 분~수 시간뿐이라 판정이 아니라 진단이다 (사건 수가 적으면 숫자가 흔들린다).
모든 시각은 PC 수신 시각(wall_ns) 기준 → 실제로 우리가 '본' 순서와 지연이 그대로 들어간다.
사건  : 바이낸스 체결가가 10초 전보다 X% 이상 상승 (X = 0.05 / 0.10 / 0.15, 종목별 60초 쿨다운)
진입  : 사건 + 300ms 이후 첫 업비트 호가의 매도1호가로 매수 (6,000원은 BTC·ETH 1호가 잔량보다 훨씬 작음)
청산  : 진입 + H초(1/5/10/30) 이후 첫 업비트 호가의 매수1호가로 매도. 수수료 편도 0.05%
출력  : 사건 수, 바이낸스 상승폭, 사건 순간 업비트가 이미 오른 폭(pre), 진입 후 업비트 중간가 변화(follow), 비용 후(net)
실행 (PC): py -X utf8 leadlag_peek.py leadlag_data
"""
import bisect
import collections
import gzip
import json
import statistics
import sys
from pathlib import Path

PAIRS = {"BTCUSDT": "KRW-BTC", "ETHUSDT": "KRW-ETH"}
THRESHOLDS = (0.0005, 0.0010, 0.0015)
HOLDS_S = (1, 5, 10, 30)
LATENCY_MS, WINDOW_MS, COOLDOWN_MS, FEE = 300, 10_000, 60_000, 0.0005


def load(root):
    bn = collections.defaultdict(list)     # symbol -> [(ms, price)]
    up = collections.defaultdict(list)     # market -> [(ms, bid, ask)]
    for f in sorted(Path(root).glob("*/*.jsonl.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("record_type") != "message" or "raw" not in r:
                    continue
                if any(str(x).startswith("invalid_payload") for x in r.get("flags", [])):
                    continue
                ms = r["wall_ns"] // 1_000_000
                try:
                    m = json.loads(r["raw"])
                except ValueError:
                    continue
                if r.get("source") == "binance":
                    d = m.get("data", {})
                    if d.get("e") == "trade" and d.get("s") in PAIRS:
                        bn[d["s"]].append((ms, float(d["p"])))
                elif r.get("source") == "upbit" and m.get("type") == "orderbook":
                    u = (m.get("orderbook_units") or [{}])[0]
                    if m.get("code") in PAIRS.values() and u.get("bid_price") and u.get("ask_price"):
                        up[m["code"]].append((ms, float(u["bid_price"]), float(u["ask_price"])))
    for v in list(bn.values()) + list(up.values()):
        v.sort()
    return bn, up


def events(trades, theta):
    out, last, j = [], None, 0
    for i, (t, p) in enumerate(trades):
        while j + 1 < i and trades[j + 1][0] <= t - WINDOW_MS:
            j += 1
        if trades[j][0] > t - WINDOW_MS:
            continue
        ret = p / trades[j][1] - 1
        if ret >= theta and (last is None or t - last >= COOLDOWN_MS):
            out.append((t, ret))
            last = t
    return out


def _at_or_after(book, times, t, max_late=2000):
    k = bisect.bisect_left(times, t)
    return book[k] if k < len(book) and book[k][0] - t <= max_late else None


def _at_or_before(book, times, t):
    k = bisect.bisect_right(times, t) - 1
    return book[k] if k >= 0 else None


def evaluate(evs, book):
    times = [b[0] for b in book]
    rows = []
    for t, ret in evs:
        now, past = _at_or_before(book, times, t), _at_or_before(book, times, t - WINDOW_MS)
        ent = _at_or_after(book, times, t + LATENCY_MS)
        if not (now and past and ent):
            continue
        mid = lambda b: (b[1] + b[2]) / 2
        r = {"bn": ret * 100, "pre": (mid(now) / mid(past) - 1) * 100}
        for h in HOLDS_S:
            ex = _at_or_after(book, times, ent[0] + h * 1000)
            if ex:
                r[f"follow_{h}"] = (mid(ex) / mid(ent) - 1) * 100
                r[f"net_{h}"] = (ex[1] * (1 - FEE) / (ent[2] * (1 + FEE)) - 1) * 100
        rows.append(r)
    return rows


def main(argv=None):
    root = (argv or sys.argv[1:] or ["leadlag_data"])[0]
    bn, up = load(root)
    span = {k: (v[-1][0] - v[0][0]) / 3_600_000 for k, v in up.items() if v}
    print("LEADLAG PEEK (diagnostic, not a verdict) | hours of Upbit data:",
          {k: round(h, 2) for k, h in span.items()}, "| binance trades:", {k: len(v) for k, v in bn.items()})
    print("symbol   X%    n   bn%    pre%   " + "  ".join(f"follow{h}s net{h}s " for h in HOLDS_S))
    f = lambda v: f"{statistics.mean(v):+.3f}" if v else "  n/a"
    for sym, mk in PAIRS.items():
        for th in THRESHOLDS:
            rows = evaluate(events(bn.get(sym, []), th), up.get(mk, []))
            cells = "  ".join(f"{f([r[f'follow_{h}'] for r in rows if f'follow_{h}' in r]):>8} "
                              f"{f([r[f'net_{h}'] for r in rows if f'net_{h}' in r]):>6}" for h in HOLDS_S)
            print(f"{mk:8} {th*100:4.2f} {len(rows):4} {f([r['bn'] for r in rows]):>6} {f([r['pre'] for r in rows]):>6}  {cells}")
    print("pre% close to bn% = Upbit already moved (no lag). follow% must exceed ~0.1% (fees) to be worth anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
