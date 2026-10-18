#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maker_fill_replay — bid1 지정가(maker) 진입 가설의 체결률·역선택 리플레이. 읽기 전용, 표준 라이브러리만.

질문 (사전 등록 · 2026-10-18 어드바이저 스펙):
  "스프레드를 안 내는 maker 진입이면 비용 후 양수가 되는가" 를, 체결 안 된 주문까지 포함해 묻는다.
필수 출력 4개 (하나라도 없으면 불채택):
  1. 체결률 (기회 대비 maker 체결 비율)
  2. 미체결=0 포함한 기회당 net (체결건만 평균하면 역선택이 숨는다)
  3. 체결군 vs 미체결군의 이후 mid 수익 비교 (역선택 직접 측정)
  4. 날짜 전/후반 분리
체결 규칙 (보수적): 주문 가격 = 의사결정 시점 bid1, 체결 판정은 설정 지연 이후부터 주문 만료 직전까지. 그 뒤 체결(trade) 중 **가격이 내 호가보다 낮은** 체결
  (= 가격이 내 호가를 뚫고 지나간 경우) 의 누적 수량이 내 수량 이상일 때만 체결. 같은 가격 체결은 큐 순서를
  모르므로 체결로 치지 않는다 (--same-price-fill 로 완화 가능 · 낙관 상한).
청산: 체결 시각 + H 초 뒤 첫 호가에서 bid 측 깊이 VWAP 로 taker 매도. 수수료 양쪽 0.05%.
기회 정의: 종목별 --sample-every-ms 간격의 호가 갱신 시점 전부 (신호 없음 = 무조건부 기준선).
  --signal-quantile q 를 주면 5초 매수불균형이 전체 분포 상위 q 이상인 시점만 (탐색용 · OOS 아님).
스프레드 버킷: 기회 시점 (ask1-bid1)/mid 로 --spread-buckets 경계에 따라 분할.
제약: 호가 20단 스냅샷 + 체결 스트림만으로 큐 위치·숨은 유동성은 알 수 없다. 결과는 상한/하한 사이의
  탐색용 추정이며 LIVE 규칙이 아니다. 부분체결 재고·불완전 경로는 손익 미확인으로 남긴다.
  세션 경계·수신 공백·시퀀스 불연속에서 보류 주문 전부 무효화.
"""
import argparse
import collections
import csv
import gzip
import json
import math
import statistics
from pathlib import Path


def _mid(units):
    a, b = float(units[0]["ask_price"]), float(units[0]["bid_price"])
    if a <= 0 or b <= 0 or a < b:
        raise ValueError("crossed/invalid book")
    return a, b, (a + b) / 2


def sell_vwap(units, quantity):
    """bid 측 깊이로 quantity 매도 시 수취 KRW. 깊이 부족이면 None."""
    remaining, proceeds = quantity, 0.0
    for u in sorted(units, key=lambda x: -float(x["bid_price"])):
        p, size = float(u["bid_price"]), float(u["bid_size"])
        if p <= 0 or size < 0:
            raise ValueError("invalid bid")
        take = min(remaining, size)
        proceeds += take * p
        remaining -= take
        if remaining <= 1e-12:
            return proceeds
    return None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def summarize(rows, label):
    """Keep every opportunity; unknown PnL is never an unfilled zero."""
    n = len(rows)
    if n == 0:
        return {"label": label, "n_opps": 0}
    filled = [r for r in rows if r["filled"] is True]
    unfilled = [r for r in rows if r["filled"] is False]
    resolved = [r for r in rows if r["net_frac"] is not None]
    n_unknown = n - len(resolved)
    fills_known = all(r["filled"] is not None for r in rows)
    filled_pnl_known = all(r["net_frac"] is not None for r in filled)

    def pct_mean(values):
        value = _mean(values)
        return value * 100 if value is not None else None

    filled_mid = (_mean([r["mid_ret_h"] for r in filled])
                  if all(r["mid_ret_h"] is not None for r in filled) else None)
    unfilled_mid = (_mean([r["mid_ret_h"] for r in unfilled])
                    if all(r["mid_ret_h"] is not None for r in unfilled) else None)
    return {
        "label": label, "n_opps": n, "n_filled": len(filled),
        "n_unfilled": len(unfilled), "n_unknown": n_unknown,
        "resolved_fraction": len(resolved) / n,
        "fill_rate": len(filled) / n if fills_known else None,
        "net_per_opp_pct": pct_mean([r["net_frac"] for r in rows]) if not n_unknown else None,
        "net_per_resolved_opp_pct": pct_mean([r["net_frac"] for r in resolved]),
        "net_per_fill_pct": pct_mean([r["net_frac"] for r in filled]) if filled_pnl_known else None,
        "mid_ret_h_filled_pct": filled_mid * 100 if filled_mid is not None else None,
        "mid_ret_h_unfilled_pct": unfilled_mid * 100 if unfilled_mid is not None else None,
        "adverse_selection_pct": (
            (filled_mid - unfilled_mid) * 100
            if not n_unknown and filled_mid is not None and unfilled_mid is not None else None),
        "positive_rate_filled": (
            sum(r["net_frac"] > 0 for r in filled) / len(filled)
            if filled and filled_pnl_known else None),
    }


def replay(args):
    files = sorted(Path(args.root).rglob("*.jsonl.gz"))
    if not files:
        raise ValueError("no .jsonl.gz files under root")
    dates = sorted({p.parent.name for p in files})
    if args.days:
        keep = set(dates[-args.days:])
        files = [p for p in files if p.parent.name in keep]
        dates = sorted(keep)
    fee = args.fee_oneway_frac
    counters = collections.Counter()
    rows = []
    # per-market state
    ticks = {}            # market -> deque[(recv_ms, value, is_buy)]
    pending = {}          # market -> list[order]
    next_opp = {}
    seen_trade = {}
    last_recv, seq = None, None
    session = 0
    imb_values = []       # for optional signal quantile (two-pass avoided: exploratory, collect then filter rows)

    def unresolved(o, t, reason):
        # A gap, partial entry, or missing exit is neither a profitable fill nor a zero-cost nontrade.
        rows.append(dict(o["base"], horizon_s=o["h"],
                         filled=True if o["filled"] else None,
                         fill_ms=o["fill_ms"], fill_wait_ms=None, exit_ms=t,
                         net_frac=None, mid_ret_h=None, end=reason))

    def invalidate(reason):
        for lst in pending.values():
            for o in lst:
                counters["invalidated_" + reason] += 1
                unresolved(o, last_recv, reason)
        pending.clear(); ticks.clear(); seen_trade.clear()

    def finalize(o, t, units, reason):
        """exit or expire. Records one row per opportunity×horizon."""
        a, b, mid = _mid(units)
        mid_ret = (mid - o["mid0"]) / o["mid0"]
        if o["filled"]:
            proceeds = sell_vwap(units, o["qty"])
            if proceeds is None:
                counters["insufficient_exit_depth"] += 1
                unresolved(o, t, "insufficient_exit_depth")
                return
            cost = o["qty"] * o["px"] * (1 + fee)
            net = proceeds * (1 - fee) - cost
            rows.append(dict(o["base"], horizon_s=o["h"], filled=True, fill_ms=o["fill_ms"],
                             fill_wait_ms=o["fill_ms"] - o["place_ms"], exit_ms=t,
                             net_frac=net / cost, mid_ret_h=mid_ret, end=reason))
        elif o["cum"] > 0:
            counters["unmodeled_partial_entry"] += 1
            unresolved(o, t, "unmodeled_partial_entry")
        else:
            rows.append(dict(o["base"], horizon_s=o["h"], filled=False, fill_ms=None, fill_wait_ms=None,
                             exit_ms=t, net_frac=0.0, mid_ret_h=mid_ret, end=reason))

    for path in files:
        counters["files"] += 1
        with gzip.open(path, "rb") as stream:
            for raw in stream:
                try:
                    m = json.loads(raw); t = int(m["recv_ts"])
                except (ValueError, KeyError, TypeError):
                    raise ValueError("invalid JSON/recv_ts in " + str(path))
                if last_recv is not None and t < last_recv:
                    raise ValueError("receive time regressed")
                if last_recv is not None and t - last_recv > args.max_gap_ms:
                    invalidate("receive_gap")
                last_recv = t
                if "_meta" in m:
                    if m["_meta"] in ("connect", "disconnect", "recv_timeout", "disk_stop"):
                        invalidate("session_boundary"); session += 1; seq = None
                    continue
                nseq = m.get("_seq")
                if nseq is not None:
                    nseq = int(nseq)
                    if seq is not None and nseq != seq + 1:
                        invalidate("sequence_discontinuity")
                    seq = nseq
                market, kind = m.get("code"), m.get("type")
                if not market or kind not in ("trade", "orderbook"):
                    continue
                ex = int(m["timestamp"])
                q = ticks.setdefault(market, collections.deque())
                while q and q[0][0] < t - args.feature_window_ms:
                    q.popleft()
                if kind == "trade":
                    sid = m.get("sequential_id")
                    if sid is None or m.get("_seq_anomaly"):
                        counters["invalid_trade_id"] += 1; continue
                    prev = seen_trade.get(market)
                    if prev is not None and int(sid) <= prev:
                        counters["duplicate_or_backward_trade"] += 1; continue
                    seen_trade[market] = int(sid)
                    price, vol = float(m["trade_price"]), float(m["trade_volume"])
                    if not (math.isfinite(price) and math.isfinite(vol) and price > 0 and vol > 0):
                        raise ValueError("invalid trade")
                    q.append((t, price * vol, m.get("ask_bid") == "BID"))
                    # maker fill check: trades strictly below my bid (price traded through), after placement
                    for o in pending.get(market, []):
                        if o["filled"] or t < o["place_ms"] or t >= o["expire_ms"]:
                            continue
                        through = price < o["px"] or (args.same_price_fill and price <= o["px"] and m.get("ask_bid") == "ASK")
                        if through:
                            o["cum"] += vol
                            if o["cum"] >= o["qty"]:
                                o["filled"] = True; o["fill_ms"] = t
                                o["exit_due_ms"] = t + o["h"] * 1000 + args.latency_ms
                    continue
                units = m.get("orderbook_units", [])
                if not units:
                    counters["empty_book"] += 1; continue
                if t - ex > args.max_quote_age_ms:
                    counters["stale_book"] += 1; continue
                a, b, mid = _mid(units)
                # resolve pending: exits (filled) and expiries (unfilled TTL)
                active = []
                for o in pending.get(market, []):
                    if o["filled"]:
                        if t >= o["exit_due_ms"]:
                            if t - o["exit_due_ms"] > args.max_lateness_ms:
                                counters["invalidated_late_exit"] += 1
                                unresolved(o, t, "late_exit")
                            else:
                                finalize(o, t, units, "exit")
                            continue
                    else:
                        if t >= o["expire_ms"]:
                            # unfilled: measure mid return at place + H for adverse-selection comparison
                            if t - o["expire_ms"] > args.max_lateness_ms:
                                counters["invalidated_late_expire"] += 1
                                unresolved(o, t, "late_expire")
                            else:
                                finalize(o, t, units, "unfilled_ttl")
                            continue
                    active.append(o)
                pending[market] = active
                # opportunity sampling
                if t < next_opp.get(market, 0):
                    continue
                next_opp[market] = t + args.sample_every_ms
                total = sum(v for _, v, _ in q)
                if total <= 0:
                    counters["opp_no_recent_trade"] += 1; continue
                imb = (2 * sum(v for _, v, is_b in q if is_b) - total) / total
                imb_values.append(imb)
                spread = (a - b) / mid
                px = b                                   # bid1 join
                qty = args.notional_krw / px
                base = {"session": session, "market": market, "date": path.parent.name, "opp_ms": t,
                        "spread_frac": spread, "buy_imbalance": imb, "bid1": b, "ask1": a}
                counters["opportunities"] += 1
                for h in args.horizons:
                    pending.setdefault(market, []).append({
                        "base": base, "h": h, "px": px, "qty": qty, "cum": 0.0, "filled": False,
                        "place_ms": t + args.latency_ms, "mid0": mid,
                        "expire_ms": t + args.latency_ms + h * 1000,   # TTL = H (미체결이면 같은 H 시점 mid 비교)
                        "fill_ms": None, "exit_due_ms": None,
                    })
    counters["pending_at_end"] = sum(len(v) for v in pending.values())
    for lst in pending.values():
        for o in lst:
            unresolved(o, last_recv, "pending_at_end")
    return rows, counters, dates, imb_values


def _quantile(vals, q):
    s = sorted(vals)
    if not s:
        return None
    k = (len(s) - 1) * q
    f = math.floor(k); c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def report(rows, counters, dates, imb_values, args):
    out = {"status": "EXPLORATORY_ONLY", "params": vars(args), "counters": dict(counters),
           "dates": dates, "groups": []}
    if args.signal_quantile is not None and imb_values:
        thr = _quantile(imb_values, args.signal_quantile)
        out["signal_threshold_buy_imbalance"] = thr
        rows = [r for r in rows if r["buy_imbalance"] >= thr]
    half = len(dates) // 2
    first_dates = set(dates[:half])
    edges = args.spread_buckets
    def bucket(sp):
        for i, e in enumerate(edges):
            if sp * 100 < e:
                return f"spread<{e}%"
        return f"spread>={edges[-1]}%" if edges else "all"
    for h in args.horizons:
        hr = [r for r in rows if r["horizon_s"] == h]
        out["groups"].append(summarize(hr, f"H={h}s all"))
        out["groups"].append(summarize([r for r in hr if r["date"] in first_dates], f"H={h}s first-half"))
        out["groups"].append(summarize([r for r in hr if r["date"] not in first_dates], f"H={h}s second-half"))
        for bname in sorted({bucket(r["spread_frac"]) for r in hr}):
            out["groups"].append(summarize([r for r in hr if bucket(r["spread_frac"]) == bname], f"H={h}s {bname}"))
    return out, rows


def print_report(rep):
    print("MAKER FILL REPLAY — bid1 join, fill only when price trades THROUGH my bid (conservative)")
    print(f"dates={rep['dates'][0]}..{rep['dates'][-1]} ({len(rep['dates'])}d)  opportunities={rep['counters'].get('opportunities', 0)}"
          + (f"  signal_thr={rep['signal_threshold_buy_imbalance']:.3f}" if 'signal_threshold_buy_imbalance' in rep else ""))
    print(f"{'group':28} {'n_opp':>7} {'unknown':>7} {'fill%':>6} {'net/opp':>8} {'net/fill':>8} {'midH_fill':>9} {'midH_unf':>9} {'advsel':>7}")
    for g in rep["groups"]:
        if g["n_opps"] == 0:
            continue
        f = lambda v, w=8: (f"{v:+{w}.3f}" if isinstance(v, (int, float)) else " " * (w - 3) + "n/a")
        fill_pct = g["fill_rate"] * 100 if g["fill_rate"] is not None else None
        print(f"{g['label']:28} {g['n_opps']:>7} {g['n_unknown']:>7} {f(fill_pct,6)} {f(g['net_per_opp_pct'])} {f(g['net_per_fill_pct'])} "
              f"{f(g['mid_ret_h_filled_pct'],9)} {f(g['mid_ret_h_unfilled_pct'],9)} {f(g['adverse_selection_pct'],7)}")
    print("net/opp = 확인된 미체결 0 포함 기회당 비용 후 수익(%). 미확인 경로가 있으면 n/a.")
    print("unknown = 부분체결·누락 경로 등. advsel은 완결 표본에서만 표시하며 이 결과는 탐색용이다.")
    print("Not a LIVE rule. Conservative fill (through-price only); --same-price-fill gives the optimistic bound.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("root")
    p.add_argument("--days", type=int, default=0)
    p.add_argument("--output", default="/tmp/maker_fill")
    p.add_argument("--notional-krw", type=float, default=6000)
    p.add_argument("--fee-oneway-frac", type=float, default=0.0005)
    p.add_argument("--horizons", type=int, nargs="+", default=[30, 60, 180, 300])
    p.add_argument("--latency-ms", type=int, default=250)
    p.add_argument("--sample-every-ms", type=int, default=10000)
    p.add_argument("--feature-window-ms", type=int, default=5000)
    p.add_argument("--signal-quantile", type=float, default=None, help="buy-imbalance upper quantile filter (exploratory)")
    p.add_argument("--spread-buckets", type=float, nargs="*", default=[0.2, 0.36])
    p.add_argument("--same-price-fill", action="store_true", help="optimistic: count ASK-side trades at my price as fills")
    p.add_argument("--max-quote-age-ms", type=int, default=1000)
    p.add_argument("--max-lateness-ms", type=int, default=2000)
    p.add_argument("--max-gap-ms", type=int, default=5000)
    args = p.parse_args(argv)
    rows, counters, dates, imb = replay(args)
    rep, used = report(rows, counters, dates, imb, args)
    print_report(rep)
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    with (out / "report.json").open("w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    if used:
        keys = [k for k in used[0].keys()]
        with (out / "rows.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(used)
    print("Outputs:", out / "report.json", out / "rows.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
