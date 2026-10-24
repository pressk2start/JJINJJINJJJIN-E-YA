#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger_listing_split — LIVE 원장(live_trades.jsonl)의 매수를 '상장 후 나이' 로 나눠 손익을 집계한다. 읽기 전용.

왜: H16(신규 상장 8일 드리프트) 에 따라 LIVE 유니버스가 상장 직후 종목에 노출돼 있음을 확인했다(83건 중 7건).
    이 스크립트는 그 노출의 실제 손익을 원장에서 그대로 읽는다. 주문·네트워크·쓰기 없음. 표준 라이브러리만. Python 3.7+.
입력: ENTRY_FILLED(seed leg) 와 TRADE_CLOSED 를 trade_id 로 조인. 손익 = net_pnl_krw_cashflow (없으면 net_pnl_krw).
상장일: daily_factor_test 의 일봉 캐시에서 첫 일봉 > 2024-09-01 인 179 종목 (2026-10-24 기준 스냅샷, 아래 LISTED).
구간: 0~7일(H16 창) / 8~30일 / 31일+ / 목록에 없음(2024-09-01 이전 상장).
실행 (서버, 읽기 전용):  python3 ledger_listing_split.py [/path/to/live_trades.jsonl]
"""
import datetime as dt
import json
import sys

LISTED = {
    "KRW-0G": "2025-09-22", "KRW-2Z": "2025-10-02", "KRW-AERO": "2025-08-22", "KRW-AGLD": "2024-11-13", "KRW-AI": "2026-06-30", "KRW-ALT": "2025-06-16",
    "KRW-ANIME": "2025-01-24", "KRW-API3": "2025-08-19", "KRW-ARKM": "2025-03-11", "KRW-ARX": "2026-06-23", "KRW-AVNT": "2025-09-15", "KRW-AXIS": "2026-10-21",
    "KRW-AXL": "2025-06-10", "KRW-AZTEC": "2026-02-20", "KRW-B3": "2026-05-07", "KRW-BABY": "2026-06-05", "KRW-BARD": "2025-09-18", "KRW-BERA": "2025-02-06",
    "KRW-BFC": "2026-09-10", "KRW-BIGTIME": "2024-09-05", "KRW-BIO": "2025-10-20", "KRW-BIRB": "2026-02-03", "KRW-BLEND": "2026-04-29", "KRW-BREV": "2026-01-07",
    "KRW-BSB": "2026-08-07", "KRW-CAP": "2026-08-06", "KRW-CARV": "2024-10-15", "KRW-CASHCAT": "2026-09-28", "KRW-CC": "2026-04-15", "KRW-CFG": "2026-02-26",
    "KRW-CFX": "2026-07-31", "KRW-CHIP": "2026-04-21", "KRW-CKB": "2024-09-13", "KRW-COMP": "2025-04-01", "KRW-COW": "2025-02-25", "KRW-CP": "2026-09-08",
    "KRW-CPOOL": "2025-10-22", "KRW-CRV": "2026-08-21", "KRW-CT": "2026-10-19", "KRW-CYBER": "2025-08-12", "KRW-DATA": "2025-08-08", "KRW-DEEP": "2025-04-22",
    "KRW-DOOD": "2025-10-07", "KRW-DOS": "2026-08-11", "KRW-DRV": "2026-07-14", "KRW-EDGE": "2026-03-04", "KRW-ELSA": "2026-01-22", "KRW-ENA": "2025-07-11",
    "KRW-ENSO": "2025-10-29", "KRW-ERA": "2025-07-17", "KRW-ESP": "2026-02-24", "KRW-ETHFI": "2026-03-19", "KRW-EUL": "2026-07-26", "KRW-EURC": "2026-08-28",
    "KRW-F": "2025-10-21", "KRW-FF": "2025-09-29", "KRW-FIL": "2025-04-04", "KRW-FLOCK": "2025-09-09", "KRW-FLUID": "2025-09-24", "KRW-FOLD": "2026-08-23",
    "KRW-GEOD": "2026-07-27", "KRW-GRVT": "2026-08-05", "KRW-GTC": "2026-10-12", "KRW-HOLO": "2025-09-11", "KRW-HOME": "2026-08-04", "KRW-HYPER": "2025-07-10",
    "KRW-ICP": "2026-03-11", "KRW-IN": "2025-10-10", "KRW-INJ": "2024-10-17", "KRW-IO": "2026-05-29", "KRW-IRYS": "2026-05-15", "KRW-JPYC": "2026-09-17",
    "KRW-JTO": "2025-02-21", "KRW-KAIA": "2026-10-09", "KRW-KAITO": "2025-03-05", "KRW-KAT": "2026-03-26", "KRW-KERNEL": "2025-10-28", "KRW-KITE": "2025-11-03",
    "KRW-KMNO": "2026-08-07", "KRW-LA": "2025-09-18", "KRW-LAYER": "2025-02-12", "KRW-LINEA": "2025-09-10", "KRW-LIT": "2026-08-24", "KRW-LPT": "2025-05-30",
    "KRW-MANTRA": "2025-05-21", "KRW-ME": "2024-12-11", "KRW-MEGA": "2026-04-30", "KRW-MET2": "2025-11-18", "KRW-META2": "2026-07-29", "KRW-MEW": "2024-10-21",
    "KRW-MIRA": "2025-09-26", "KRW-MMT": "2025-11-05", "KRW-MOCA": "2024-12-16", "KRW-MON": "2025-11-24", "KRW-MOODENG": "2025-07-03", "KRW-MORPHO": "2026-07-25",
    "KRW-MOVE": "2024-12-10", "KRW-MPLX": "2026-10-20", "KRW-NCT": "2026-08-26", "KRW-NEWT": "2025-06-24", "KRW-NMR": "2026-10-06", "KRW-NXPC": "2025-05-15",
    "KRW-O": "2026-07-23", "KRW-OP": "2025-07-28", "KRW-OPEN": "2025-09-10", "KRW-OPG": "2026-07-07", "KRW-ORCA": "2025-03-21", "KRW-ORDER": "2025-10-24",
    "KRW-PENGU": "2025-05-09", "KRW-PEPE": "2024-11-14", "KRW-PIEVERSE": "2026-04-20", "KRW-PLUME": "2025-11-26", "KRW-POD": "2026-10-02", "KRW-POKT": "2025-05-30",
    "KRW-PONS": "2026-10-08", "KRW-PRL": "2026-04-27", "KRW-PROM": "2026-08-12", "KRW-PROS": "2026-05-08", "KRW-PROVE": "2025-08-06", "KRW-PUFFER": "2026-10-15",
    "KRW-PUMP": "2025-09-11", "KRW-PYUSD": "2026-09-17", "KRW-QUID": "2026-08-04", "KRW-RAY": "2025-06-19", "KRW-RE": "2026-06-19", "KRW-RED": "2025-09-05",
    "KRW-RENDER": "2024-12-05", "KRW-RLUSD": "2026-07-28", "KRW-RSR": "2026-10-12", "KRW-SAFE": "2024-10-24", "KRW-SAHARA": "2025-06-26", "KRW-SENT": "2026-01-29",
    "KRW-SIGN": "2025-04-29", "KRW-SKR": "2026-02-24", "KRW-SKY": "2026-03-31", "KRW-SLX": "2026-06-01", "KRW-SOMI": "2025-10-01", "KRW-SONIC": "2025-01-08",
    "KRW-SOON": "2026-04-17", "KRW-SOPH": "2025-05-28", "KRW-SPK": "2026-04-23", "KRW-SPX": "2026-06-16", "KRW-SUN": "2025-09-22", "KRW-SUPER": "2025-09-29",
    "KRW-SYRUP": "2025-07-25", "KRW-TAO": "2026-02-16", "KRW-TIA": "2025-05-08", "KRW-TOSHI": "2025-09-17", "KRW-TRAC": "2026-05-18", "KRW-TREE": "2025-08-28",
    "KRW-TRUMP": "2025-02-13", "KRW-TRUST": "2025-11-05", "KRW-UNI": "2024-10-22", "KRW-UP2": "2026-05-13", "KRW-USD1": "2025-09-01", "KRW-USDE": "2026-01-14",
    "KRW-USDG": "2026-07-30", "KRW-USDS": "2026-03-31", "KRW-VANA": "2024-12-17", "KRW-VIRTUAL": "2025-01-31", "KRW-VTHO": "2025-01-21", "KRW-VVV": "2026-05-12",
    "KRW-W": "2024-10-02", "KRW-WAL": "2025-03-28", "KRW-WCT": "2025-04-16", "KRW-WET": "2025-12-15", "KRW-WIF": "2026-05-06", "KRW-WLD": "2025-09-09",
    "KRW-WLFI": "2025-09-01", "KRW-XAUT": "2026-01-01", "KRW-XCN": "2026-04-27", "KRW-XPL": "2025-09-25", "KRW-YGG": "2025-10-15", "KRW-ZAMA": "2026-04-14",
    "KRW-ZBT": "2025-10-17", "KRW-ZK": "2026-01-06", "KRW-ZKC": "2025-09-15", "KRW-ZKP": "2025-12-26", "KRW-ZORA": "2025-10-17",
}
BUCKETS = (("D0-7", 0, 7), ("D8-30", 8, 30), ("D31+", 31, 10**6))


def _pnl(c):
    v = c.get("net_pnl_krw_cashflow")
    return c.get("net_pnl_krw") if v is None else v


def split(rows, listed=LISTED):
    """rows: 원장 이벤트 dict 들 → (per_trade list, bucket summary dict)."""
    entries, closed = {}, {}
    for j in rows:
        t = j.get("event_type")
        if t == "ENTRY_FILLED" and j.get("leg", "seed") == "seed" and j.get("trade_id"):
            entries[j["trade_id"]] = j
        elif t == "TRADE_CLOSED" and j.get("trade_id"):
            closed[j["trade_id"]] = j
    per = []
    for tid, e in entries.items():
        c = closed.get(tid)
        m, d = e.get("market"), str(e.get("entry_time", ""))[:10]
        f = listed.get(m)
        age = None
        if f and len(d) == 10:
            try:
                age = (dt.date.fromisoformat(d) - dt.date.fromisoformat(f)).days
            except ValueError:
                age = None
        bucket = "unlisted/old" if age is None else next((b for b, lo, hi in BUCKETS if lo <= age <= hi), "D31+")
        per.append({"market": m, "date": d, "age": age, "bucket": bucket, "closed": c is not None,
                    "pnl_krw": _pnl(c) if c else None, "pnl_pct": c.get("net_pnl_pct") if c else None,
                    "exit_reason": c.get("exit_reason") if c else None})
    summary = {}
    for b in [x[0] for x in BUCKETS] + ["unlisted/old"]:
        sel = [p for p in per if p["bucket"] == b and p["closed"]]
        pn = [p["pnl_krw"] for p in sel if p["pnl_krw"] is not None]
        pc = [p["pnl_pct"] for p in sel if p["pnl_pct"] is not None]
        summary[b] = {"n": len(sel), "sum_krw": round(sum(pn)) if pn else 0,
                      "mean_pct": (sum(pc) / len(pc)) if pc else None,
                      "win": (sum(1 for x in pn if x > 0) / len(pn)) if pn else None}
    return per, summary


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else "/home/ubuntu/bot/data/live_trades.jsonl"
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    per, summary = split(rows)
    print("LEDGER LISTING SPLIT | entries(seed) %d | listed dict %d markets" % (len(per), len(LISTED)))
    for p in sorted((p for p in per if p["bucket"] in ("D0-7", "D8-30")), key=lambda p: (p["age"], p["date"])):
        pk = "%+.0f" % p["pnl_krw"] if p["pnl_krw"] is not None else "?"
        pp = "%+.2f%%" % p["pnl_pct"] if p["pnl_pct"] is not None else "?"
        print("  %-6s %-12s %s age %3sd  pnl %8s %8s  %s" % (p["bucket"], p["market"], p["date"], p["age"], pk, pp,
                                                          p["exit_reason"] or ("OPEN" if not p["closed"] else "")))
    print("%-13s %4s %10s %9s %5s" % ("bucket", "n", "sum_krw", "mean_pct", "win%"))
    for b, s in summary.items():
        mp = "%+.2f" % s["mean_pct"] if s["mean_pct"] is not None else "n/a"
        w = "%.0f" % (s["win"] * 100) if s["win"] is not None else "n/a"
        print("%-13s %4d %10d %9s %5s" % (b, s["n"], s["sum_krw"], mp, w))
    return 0


if __name__ == "__main__":
    sys.exit(main())
