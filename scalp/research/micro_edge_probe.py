#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""micro_edge_probe.py — 사용자 "박리다매" 가설 1차 탐색 (F2c-exploration · advisor 1+2 승인).

목적
----
ws_recorder.py 가 수집한 Upbit WebSocket raw event (orderbook + trade) 를 읽어서
**"초단위 매수압력이 강한 순간일수록 5~30초 뒤 비용 차감 후 순수익이 단조롭게
좋아지는가"** 를 데이터로 묻는다.

이것은 **탐색 모드** 다 — 정식 TRAIN/OOS 없이 전체 14일 전수 측정 ·
"뭔가 비용 넘는 현상이 보이면 → F2c 정식 사전등록으로 넘어감 · 어떤 상태에서도
못 넘으면 → 싼 NO 결론 · 돈 안 쓰고 끝".

주의 (advisor 1+2 교정 수용):
- **Taker benchmark 만** (첫 버전 · maker 는 다음 단계)
- 수수료 왕복 0.1% **만** 차감 (ask1/bid1 가격에 spread 이미 반영 · **이중 차감 금지**)
- MFE 는 predictor 로 사용 X · forward return 측정에만
- "박리다매 불가능" 단정 X · "이 수집 · 이 event 정의 · 이 execution 에서 못 찾음" 까지만
- 매매 봇 코드 완전 분리 · 이 스크립트는 bot.py / 실매매 로직에 영향 0

입력: {data-dir}/{YYYY-MM-DD}/{HH}.jsonl.gz
출력: 콘솔 표 + /tmp/micro_edge_probe_result.json

추가 비교 모드: --execution-compare (주문 금액·깊이·지연·양방향 수수료·시간 분리).
기존 탐색 결과와 구분하며 과거 holdout을 fresh forward 검증으로 부르지 않는다.

사용:
    cd /home/ubuntu/scalp/research
    python3 micro_edge_probe.py \\
        --data-dir /home/ubuntu/scalp/research/data/ws \\
        --days 14

성립 조건 (저위험 플래그):
- 매매 봇과 독립 process · 데이터는 read-only
- 추가 네트워크/디스크 쓰기 없음 (출력 JSON 외)
- RAM: 14일치 incremental 로딩 · 종목별 sliding window 만 유지
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import math
import datetime as dt
import gzip
import json
import os
import statistics
import sys
import time
from pathlib import Path

# ========== 사전 등록 상수 (분석 전 freeze · 결과 보고 수정 금지) ==========
HORIZONS_SEC = [5, 10, 15, 30]              # forward return horizons
PRESSURE_WINDOW_SEC = 10                     # 최근 N초 trade 를 pressure 계산에 사용
MIN_TRADES_FOR_PRESSURE = 3                  # pressure 계산에 필요한 최소 trade 수
EVENT_DEDUP_SEC = 30                         # 같은 종목 같은 pressure state 재사용 금지 window
FEE_ROUNDTRIP_PCT = 0.10                     # 왕복 수수료 (percent-point · Upbit KRW)

# Pressure 분위 (percentile) · 상위 → 더 선택적
PRESSURE_PERCENTILES = [100, 80, 90, 95]     # 100=전체 · 80=상위 20% · 90=상위 10% · 95=상위 5%

# ========================================================================


def parse_args():
    ap = argparse.ArgumentParser(
        description="F2c micro-edge exploration probe (Taker benchmark only)")
    ap.add_argument("data_dir_pos", nargs="?", default=None,
                    help="ws_recorder output root (positional · overrides --data-dir)")
    ap.add_argument("--data-dir", default="/home/ubuntu/scalp/research/data/ws",
                    help="ws_recorder output root (contains YYYY-MM-DD/HH.jsonl.gz)")
    ap.add_argument("--days", type=int, default=14,
                    help="recent N days to analyze (default 14 = full retain)")
    ap.add_argument("--out", default="/tmp/micro_edge_probe_result.json")
    ap.add_argument("--sample-only", action="store_true",
                    help="only 2 days for quick smoke test")
    ap.add_argument("--execution-compare", action="store_true",
                    help="Depth/latency/fee comparison with fixed signals and chronological holdout")
    ap.add_argument("--order-krw", type=float, default=6000.0)
    ap.add_argument("--latency-ms", type=int, default=250)
    ap.add_argument("--fee-rate", type=float, default=.0005, help="Per-side fee fraction")
    ap.add_argument("--validation-from", default=None,
                    help="UTC-aware ISO boundary; otherwise derive from input day inventory only")
    args = ap.parse_args()
    if args.data_dir_pos:
        args.data_dir = args.data_dir_pos
    return args


def iter_event_files(root: str, days: int):
    """Return sorted list of (date_str, hour_str, path) for recent `days`."""
    rp = Path(root)
    if not rp.is_dir():
        raise FileNotFoundError(f"data-dir not found: {root}")
    date_dirs = sorted([d for d in rp.iterdir() if d.is_dir()], reverse=True)[:days]
    files = []
    for d in sorted(date_dirs, key=lambda x: x.name):
        for f in sorted(d.glob("*.jsonl.gz")):
            files.append((d.name, f.stem, f))
    return files


def stream_events(files):
    """Yield parsed events in time order (per-file).
    Each file is already time-ordered by _seq. Across files we process sequentially.
    """
    for date_str, hour_str, path in files:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    yield date_str, hour_str, ev
        except Exception as e:
            print(f"[probe] skip {path}: {e}", file=sys.stderr)
            continue


class CoinState:
    """Per-coin rolling state: latest orderbook top-of-book + trade window."""
    __slots__ = ("code", "best_ask", "best_bid",
                 "ask_size_tob", "bid_size_tob",
                 "trades", "last_event_ts", "last_probe_ts")

    def __init__(self, code):
        self.code = code
        self.best_ask = None    # top-of-book ask price
        self.best_bid = None
        self.ask_size_tob = 0.0
        self.bid_size_tob = 0.0
        self.trades = collections.deque()  # (ts_ms, price, volume, is_buy_taker)
        self.last_event_ts = 0
        self.last_probe_ts = 0  # for dedup

    def update_orderbook(self, ev, ts_ms):
        units = ev.get("orderbook_units") or []
        if not units:
            return
        u0 = units[0]
        # upbit: unit[0] = top-of-book (ask·bid 양쪽)
        self.best_ask = float(u0.get("ask_price", 0)) or self.best_ask
        self.best_bid = float(u0.get("bid_price", 0)) or self.best_bid
        self.ask_size_tob = float(u0.get("ask_size", 0))
        self.bid_size_tob = float(u0.get("bid_size", 0))
        self.last_event_ts = ts_ms

    def update_trade(self, ev, ts_ms):
        price = float(ev.get("trade_price") or 0)
        volume = float(ev.get("trade_volume") or 0)
        if price <= 0 or volume <= 0:
            return
        # ask_bid: 'BID' = 테이커 매수 (someone hit the ask) · 'ASK' = 테이커 매도
        is_buy_taker = ev.get("ask_bid") == "BID"
        # trade event 는 best_bid/ask 를 포함 (touch) → orderbook snapshot 없이도 TOB 갱신 가능
        bb = ev.get("best_bid_price")
        ba = ev.get("best_ask_price")
        if bb:
            self.best_bid = float(bb)
        if ba:
            self.best_ask = float(ba)
        self.trades.append((ts_ms, price, volume, is_buy_taker))
        self.last_event_ts = ts_ms

    def prune_trades(self, now_ms, window_sec):
        cutoff = now_ms - window_sec * 1000
        while self.trades and self.trades[0][0] < cutoff:
            self.trades.popleft()

    def buy_pressure(self, now_ms):
        """Returns pressure score ∈ [-1, +1] over PRESSURE_WINDOW_SEC.
        None if insufficient trades.
        """
        self.prune_trades(now_ms, PRESSURE_WINDOW_SEC)
        if len(self.trades) < MIN_TRADES_FOR_PRESSURE:
            return None
        buy_vol = sum(v for _, _, v, is_buy in self.trades if is_buy)
        sell_vol = sum(v for _, _, v, is_buy in self.trades if not is_buy)
        tot = buy_vol + sell_vol
        if tot <= 0:
            return None
        return (buy_vol - sell_vol) / tot


class ForwardTracker:
    """진입 event 를 추적해서 각 horizon 에서 forward price 측정."""
    def __init__(self):
        self.pending = []  # list of (code, entry_ts_ms, entry_ask, pressure, by_horizon_dict)

    def add(self, code, ts_ms, entry_ask, pressure, latency_ms=None):
        if entry_ask is None or entry_ask <= 0:
            return
        self.pending.append({
            "code": code,
            "entry_ts": ts_ms,
            "entry_ask": entry_ask,
            "pressure": pressure,
            "latency_ms": latency_ms,  # recv_ts - exchange_ts at entry
            "forward": {h: None for h in HORIZONS_SEC},  # bid price at t+h
        })

    def update(self, code, ts_ms, best_bid):
        """Check each pending entry for this code · fill forward bid if horizon reached."""
        if best_bid is None or best_bid <= 0:
            return []
        completed = []
        for p in self.pending:
            if p["code"] != code:
                continue
            for h in HORIZONS_SEC:
                target_ts = p["entry_ts"] + h * 1000
                if p["forward"][h] is None and ts_ms >= target_ts:
                    p["forward"][h] = best_bid
            # all horizons filled?
            if all(v is not None for v in p["forward"].values()):
                completed.append(p)
        # remove completed
        self.pending = [p for p in self.pending if not all(v is not None for v in p["forward"].values())]
        return completed

    def expire_stale(self, now_ms, max_horizon_sec):
        """Drop entries older than max_horizon_sec * 2 (data gap · can't fill)."""
        cutoff = now_ms - max_horizon_sec * 2000
        dropped = len([p for p in self.pending if p["entry_ts"] < cutoff])
        self.pending = [p for p in self.pending if p["entry_ts"] >= cutoff]
        return dropped


def compute_net_return(entry_ask, exit_bid):
    """Executable Taker net return (percent) after fee.
    ask1 매수 → bid1 매도 자체가 spread 반영 · 수수료만 추가 차감 (advisor 1+2 교정).
    """
    if entry_ask is None or entry_ask <= 0 or exit_bid is None or exit_bid <= 0:
        return None
    gross_pct = (exit_bid - entry_ask) / entry_ask * 100.0
    net_pct = gross_pct - FEE_ROUNDTRIP_PCT
    return net_pct


def analyze_results(completed):
    """Group by pressure percentile · compute stats per horizon.
    Returns: dict with per-group stats.
    """
    if not completed:
        return {}
    pressures = [c["pressure"] for c in completed if c["pressure"] is not None]
    if not pressures:
        return {}
    results = {}

    def group_stats(subset):
        n = len(subset)
        g = {"n": n, "horizons": {}}
        for h in HORIZONS_SEC:
            nets = [compute_net_return(c["entry_ask"], c["forward"][h]) for c in subset]
            nets = [v for v in nets if v is not None]
            if not nets:
                continue
            nets_sorted = sorted(nets)
            n_h = len(nets)
            avg = sum(nets) / n_h
            med = nets_sorted[n_h // 2]
            pos_rate = sum(1 for v in nets if v > 0) / n_h
            p10 = nets_sorted[max(0, n_h * 10 // 100)]
            p90 = nets_sorted[min(n_h - 1, n_h * 90 // 100)]
            # top-1 · top-5 removed
            nets_no_top1 = sorted(nets)[:-1] if n_h > 1 else []
            nets_no_top5 = sorted(nets)[:-5] if n_h > 5 else []
            avg_no1 = (sum(nets_no_top1) / len(nets_no_top1)) if nets_no_top1 else None
            avg_no5 = (sum(nets_no_top5) / len(nets_no_top5)) if nets_no_top5 else None
            g["horizons"][str(h)] = {
                "n": n_h,
                "avg_net_pct": round(avg, 5),
                "median_net_pct": round(med, 5),
                "positive_rate": round(pos_rate, 4),
                "p10": round(p10, 5),
                "p90": round(p90, 5),
                "avg_net_pct_no_top1": round(avg_no1, 5) if avg_no1 is not None else None,
                "avg_net_pct_no_top5": round(avg_no5, 5) if avg_no5 is not None else None,
            }
        # coin / hour concentration
        coin_counter = collections.Counter(c["code"] for c in subset)
        g["top_coin"] = coin_counter.most_common(10)
        hour_counter = collections.Counter(
            dt.datetime.utcfromtimestamp(c["entry_ts"] / 1000).hour for c in subset
        )
        g["top_hour"] = hour_counter.most_common(5)
        # per-day net (10s horizon basis · representative) · 특정 하루 의존성 확인
        by_day = collections.defaultdict(list)
        for c in subset:
            d = dt.datetime.utcfromtimestamp(c["entry_ts"] / 1000).strftime("%Y-%m-%d")
            net = compute_net_return(c["entry_ask"], c["forward"].get(10))
            if net is not None:
                by_day[d].append(net)
        g["per_day_net_10s"] = {d: round(sum(v) / len(v), 5) for d, v in sorted(by_day.items())}
        g["per_day_n"] = {d: len(v) for d, v in sorted(by_day.items())}
        # per-coin net (10s · 종목 의존성)
        by_coin = collections.defaultdict(list)
        for c in subset:
            net = compute_net_return(c["entry_ask"], c["forward"].get(10))
            if net is not None:
                by_coin[c["code"]].append(net)
        g["per_coin_net_10s"] = {k: round(sum(v) / len(v), 5) for k, v in by_coin.items()}
        g["per_coin_n"] = {k: len(v) for k, v in by_coin.items()}
        # entry latency (recv_ts - exchange_ts) distribution
        lats = [c["latency_ms"] for c in subset if c.get("latency_ms") is not None]
        if lats:
            lats_sorted = sorted(lats)
            g["latency_ms"] = {
                "n": len(lats),
                "p50": lats_sorted[len(lats) // 2],
                "p95": lats_sorted[min(len(lats) - 1, int(len(lats) * 0.95))],
                "max": max(lats),
            }
        return g

    # Compute percentile thresholds (unconditional on pressure > 0 for group assignment)
    pressures_sorted = sorted(pressures)
    results["ALL"] = group_stats(completed)
    for pct in PRESSURE_PERCENTILES:
        if pct == 100:
            continue
        thr_idx = int(len(pressures_sorted) * pct / 100.0)
        if thr_idx >= len(pressures_sorted):
            continue
        thr = pressures_sorted[thr_idx]
        subset = [c for c in completed if c["pressure"] is not None and c["pressure"] >= thr]
        key = f"BUY_PRESSURE_P{pct}+"
        results[key] = group_stats(subset)
        results[key]["pressure_threshold"] = round(thr, 5)
    return results



# Execution-aware comparison stays inside this offline probe. No orders/network.
COMPARISON_SIGNALS = ("ALL", "FLOW_PERSISTENCE", "FLOW_PLUS_BOOK")
GAP_META = {"connect", "disconnect", "recv_timeout", "shutdown", "disk_stop",
            "setup_failed", "input_error"}


def finite(value, positive=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


def depth_snapshot(event):
    """One complete received ladder; never combine trade touch with old sizes."""
    units = event.get("orderbook_units")
    if not isinstance(units, list) or not units or event.get("level", 0) != 0:
        return None
    asks, bids = [], []
    for unit in units:
        if not isinstance(unit, dict):
            return None
        ap, bp = finite(unit.get("ask_price"), True), finite(unit.get("bid_price"), True)
        aq, bq = finite(unit.get("ask_size")), finite(unit.get("bid_size"))
        if (None in (ap, bp, aq, bq) or aq < 0 or bq < 0
                or not math.isfinite(ap * aq) or not math.isfinite(bp * bq)):
            return None
        asks.append((ap, aq)); bids.append((bp, bq))
    if (asks[0][0] <= bids[0][0]
            or any(asks[i][0] >= asks[i + 1][0] or bids[i][0] <= bids[i + 1][0]
                   for i in range(len(asks) - 1))):
        return None
    if not all(math.isfinite(sum(p * q for p, q in side)) for side in (asks, bids)):
        return None
    return tuple(asks), tuple(bids)


def depth_buy(asks, notional, adverse_bp=0.0):
    remaining, quantity = notional, 0.0
    for raw_price, size in asks:
        price = raw_price * (1 + adverse_bp / 10000)
        take = min(size, remaining / price)
        quantity += take
        remaining -= take * price
        if remaining <= max(1e-8, notional * 1e-10):
            return quantity
    return None  # insufficient observed capacity; no extrapolation/assumed partial fill


def depth_sell(bids, quantity):
    remaining, funds = quantity, 0.0
    for price, size in bids:
        take = min(size, remaining)
        funds += take * price
        remaining -= take
        if remaining <= max(1e-10, quantity * 1e-10):
            return funds
    return None


def exact_net(notional, sell_funds, fee_rate, adverse_bp):
    # Extra adverse execution is an assumption, separate from depth/spread.
    # Same received ladder is adjusted once per side; spread is already in prices.
    # Entry quantity was walked at adversely adjusted ask prices for this budget.
    exit_funds = sell_funds * (1 - adverse_bp / 10000)
    cash_cost = notional * (1 + fee_rate)
    net = exit_funds * (1 - fee_rate) - cash_cost
    return net, net / cash_cost * 100


class CompareStats:
    def __init__(self):
        self.signals = self.entries = self.missing = self.skipped = 0
        self.values, self.krw_sum = [], 0.0
        self.stress_sum = self.delta_sum = self.opportunity_sum = 0.0
        self.common = 0
        self.reasons = collections.Counter()
        self.by_coin = collections.defaultdict(lambda: [0, 0.0])
        self.by_day = collections.defaultdict(lambda: [0, 0.0])

    def result(self):
        vals = sorted(self.values)
        n = len(vals)
        tail = vals[:max(1, math.ceil(n * .05))]
        no_top5 = vals[:-5] if n > 5 else []
        return {
            "selected_signals": self.signals, "snapshot_entry_capacity_ok": self.entries,
            "completed_roundtrips": n, "selected_unresolved": self.missing,
            "signals_skipped": self.skipped, "missing_reasons": dict(self.reasons),
            "mean_net_pct": sum(vals) / n if n else None,
            "positive_rate": sum(x > 0 for x in vals) / n if n else None,
            "worst_5pct_mean_net_pct": sum(tail) / len(tail) if tail else None,
            "mean_net_pct_without_top5": sum(no_top5) / len(no_top5) if no_top5 else None,
            "stress_mean_net_pct": self.stress_sum / n if n else None,
            "mean_event_net_krw": self.krw_sum / n if n else None,
            "paired_complete_opportunities": self.common,
            "net_pct_per_complete_opportunity_including_skips":
                self.opportunity_sum / self.common if self.common else None,
            "delta_vs_all_pct_per_complete_opportunity":
                self.delta_sum / self.common if self.common else None,
            "per_coin": {k: {"n": v[0], "mean_net_pct": v[1] / v[0]}
                         for k, v in sorted(self.by_coin.items())},
            "per_day": {k: {"n": v[0], "mean_net_pct": v[1] / v[0]}
                        for k, v in sorted(self.by_day.items())},
        }


class ExecutionComparison:
    """Receive-time observation -> delayed snapshot capacity -> fixed-horizon exits.

    This is an offline taker model, not proof of live order acceptance or fills.
    """
    def __init__(self, notional=6000.0, latency_ms=250, fee_rate=.0005,
                 validation_ms=None, max_fill_lag_ms=1000, quote_age_ms=2000):
        if (finite(notional, True) is None or notional < 5000
                or finite(latency_ms) is None or latency_ms < 0
                or finite(fee_rate) is None or not 0 <= fee_rate < .1
                or finite(max_fill_lag_ms) is None or max_fill_lag_ms <= 0
                or finite(quote_age_ms) is None or quote_age_ms <= 0):
            raise ValueError("invalid comparison execution assumptions")
        self.notional, self.latency_ms, self.fee_rate = notional, latency_ms, fee_rate
        self.validation_ms = validation_ms
        self.max_lag, self.quote_age = max_fill_lag_ms, quote_age_ms
        self.purge_ms = (max(HORIZONS_SEC) * 1000 + 2 * latency_ms
                         + 2 * max_fill_lag_ms + PRESSURE_WINDOW_SEC * 1000)
        self.states, self.pending, self.groups = {}, [], {}
        self.qc = collections.Counter()
        self.last_ts = self.first_ts = None
        self.last_sequence = None
        self.eligible = collections.Counter()

    def stats(self, phase, signal, horizon):
        return self.groups.setdefault((phase, signal, horizon), CompareStats())

    def phase(self, timestamp):
        if self.validation_ms is None:
            return "EXPLORATION"
        if timestamp < self.validation_ms - self.purge_ms:
            return "TRAIN"
        if timestamp >= self.validation_ms + PRESSURE_WINDOW_SEC * 1000:
            return "HISTORICAL_HOLDOUT"
        return None

    def finish(self, pending, horizon, reason=None, sell_funds=None, stress_sell_funds=None):
        if horizon in pending["finished"]:
            return
        pending["finished"].add(horizon)
        base = stress = None
        if reason is None:
            base = exact_net(self.notional, sell_funds, self.fee_rate, 1.0)
            stress = exact_net(self.notional, stress_sell_funds, self.fee_rate * 1.5, 2.0)
        for signal, selected in pending["signals"].items():
            stat = self.stats(pending["phase"], signal, horizon)
            if selected:
                if reason:
                    stat.missing += 1
                    stat.reasons[reason] += 1
                else:
                    stat.values.append(base[1]); stat.krw_sum += base[0]
                    stat.stress_sum += stress[1]
                    stat.by_coin[pending["code"]][0] += 1
                    stat.by_coin[pending["code"]][1] += base[1]
                    day = dt.datetime.fromtimestamp(
                        pending["decision_ts"] / 1000, dt.timezone.utc).date().isoformat()
                    stat.by_day[day][0] += 1; stat.by_day[day][1] += base[1]
            # Rejected signals contribute zero only to complete matched opportunities.
            # Unknown execution outcomes never become zero-return observations.
            if base is not None:
                chosen = base[1] if selected else 0.0
                stat.common += 1
                stat.opportunity_sum += chosen
                stat.delta_sum += chosen - base[1]

    def reset(self, reason):
        for pending in self.pending:
            for h in HORIZONS_SEC:
                self.finish(pending, h, reason=reason)
        self.pending.clear()
        self.states.clear()
        self.last_sequence = None
        self.qc[reason] += 1

    def advance(self, now, code=None, book=None):
        for pending in self.pending:
            if pending["quantity"] is None:
                target = pending["decision_ts"] + self.latency_ms
                if now > target + self.max_lag:
                    for h in HORIZONS_SEC:
                        self.finish(pending, h, reason="entry_quote_late_or_missing")
                    continue
                if book is not None and code == pending["code"] and now >= target:
                    quantity = depth_buy(book[0], self.notional, adverse_bp=1.0)
                    if quantity is None:
                        for h in HORIZONS_SEC:
                            self.finish(pending, h, reason="entry_depth_insufficient")
                        continue
                    pending["quantity"], pending["entry_ts"] = quantity, now
                    pending["stress_quantity"] = depth_buy(book[0], self.notional, adverse_bp=2.0)
                    for signal, selected in pending["signals"].items():
                        if selected:
                            for h in HORIZONS_SEC:
                                self.stats(pending["phase"], signal, h).entries += 1
            if pending["quantity"] is None:
                continue
            for h in HORIZONS_SEC:
                if h in pending["finished"]:
                    continue
                target = pending["entry_ts"] + h * 1000 + self.latency_ms
                if now > target + self.max_lag:
                    self.finish(pending, h, reason="exit_quote_late_or_missing")
                elif book is not None and code == pending["code"] and now >= target:
                    funds = depth_sell(book[1], pending["quantity"])
                    stress_funds = depth_sell(book[1], pending["stress_quantity"])
                    self.finish(pending, h, reason="exit_depth_insufficient" if funds is None else None,
                                sell_funds=funds, stress_sell_funds=stress_funds)
        self.pending = [p for p in self.pending if len(p["finished"]) < len(HORIZONS_SEC)]

    def step(self, event):
        now = finite(event.get("recv_ts"), True)
        meta = event.get("_meta")
        if meta is not None:
            if not isinstance(meta, str) or meta in GAP_META:
                self.reset("session_or_input_gap")
                if now is not None:
                    self.last_ts = now
            return
        if now is None:
            self.reset("invalid_receive_timestamp")
            return
        if self.last_ts is not None:
            if now < self.last_ts:
                self.reset("receive_clock_reversed")
                self.last_ts = now
                return
            if now - self.last_ts > 5000:
                self.reset("unmarked_receive_gap")
        sequence = event.get("_seq")
        if sequence is not None:
            if (not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0
                    or (self.last_sequence is not None and sequence <= self.last_sequence)):
                self.qc["invalid_or_duplicate_receive_sequence"] += 1
                return
            self.last_sequence = sequence
        self.first_ts = now if self.first_ts is None else self.first_ts
        self.last_ts = now
        self.advance(now)
        code = event.get("code")
        if not isinstance(code, str) or not code:
            self.qc["invalid_market"] += 1
            return
        state = self.states.setdefault(
            code, {"trades": collections.deque(), "book": None, "book_ts": None,
                   "last_signal": None, "last_trade_id": None, "first_seen_ts": now})
        if event.get("type") == "orderbook":
            exchange_ts = finite(event.get("timestamp"), True)
            book = depth_snapshot(event)
            if (book is None or exchange_ts is None
                    or not 0 <= now - exchange_ts <= self.quote_age
                    or (state["book_ts"] is not None and exchange_ts < state["book_ts"])):
                state["book"] = state["book_ts"] = None
                self.qc["invalid_or_delayed_book"] += 1
                return
            state["book"], state["book_ts"] = book, exchange_ts
            self.advance(now, code, book)
            return
        if event.get("type") != "trade":
            return
        px, qty = finite(event.get("trade_price"), True), finite(event.get("trade_volume"), True)
        side = event.get("ask_bid")
        exchange_ts = finite(event.get("trade_timestamp", event.get("timestamp")), True)
        trade_id = event.get("sequential_id")
        if trade_id is not None:
            if (isinstance(trade_id, bool) or not isinstance(trade_id, (int, str))
                    or not str(trade_id).isdigit() or int(trade_id) <= 0):
                self.qc["invalid_trade_sequence"] += 1
                return
            trade_id = int(trade_id)  # preserve integer identity beyond IEEE-754 exact range
        if (event.get("_seq_anomaly") or px is None or qty is None or side not in ("ASK", "BID")
                or exchange_ts is None or not 0 <= now - exchange_ts <= self.quote_age
                or (trade_id is not None and state["last_trade_id"] is not None
                    and trade_id <= state["last_trade_id"])):
            self.qc["invalid_duplicate_or_delayed_trade"] += 1
            return
        if trade_id is not None:
            state["last_trade_id"] = trade_id
        amount = px * qty
        if not math.isfinite(amount):
            self.qc["invalid_trade_notional"] += 1
            return
        state["trades"].append((now, amount if side == "BID" else -amount, amount))
        while state["trades"] and state["trades"][0][0] <= now - 10000:
            state["trades"].popleft()
        if now - state["first_seen_ts"] < PRESSURE_WINDOW_SEC * 1000:
            return
        if state["last_signal"] is not None and now - state["last_signal"] < EVENT_DEDUP_SEC * 1000:
            return
        phase = self.phase(now)
        if phase is None:
            self.qc["split_boundary_purged"] += 1
            return
        if state["book"] is None or now - state["book_ts"] > self.quote_age:
            self.qc["decision_book_unavailable"] += 1
            return
        old = [t for t in state["trades"] if t[0] <= now - 5000]
        new = [t for t in state["trades"] if t[0] > now - 5000]
        if len(old) < MIN_TRADES_FOR_PRESSURE or len(new) < MIN_TRADES_FOR_PRESSURE:
            return
        if len(state["book"][0]) < 5:
            self.qc["decision_depth_under_five_levels"] += 1
            return
        totals = [sum(t[1] for t in old), sum(t[2] for t in old),
                  sum(t[1] for t in new), sum(t[2] for t in new)]
        if not all(math.isfinite(v) for v in totals) or min(totals[1], totals[3]) <= 0:
            self.qc["invalid_flow_totals"] += 1
            return
        pressure_old, pressure_new = totals[0] / totals[1], totals[2] / totals[3]
        ask_depth = sum(p * q for p, q in state["book"][0][:5])
        bid_depth = sum(p * q for p, q in state["book"][1][:5])
        if ask_depth + bid_depth <= 0:
            return
        imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
        persistence = pressure_old > 0 and pressure_new > 0
        signals = {"ALL": True, "FLOW_PERSISTENCE": persistence,
                   "FLOW_PLUS_BOOK": persistence and imbalance > 0}
        state["last_signal"] = now
        self.eligible[phase] += 1
        for signal, selected in signals.items():
            for h in HORIZONS_SEC:
                stat = self.stats(phase, signal, h)
                stat.signals += int(selected)
                stat.skipped += int(not selected)
        self.pending.append({"code": code, "decision_ts": now, "phase": phase,
                             "signals": signals, "quantity": None, "stress_quantity": None, "entry_ts": None,
                             "finished": set()})

    def result(self):
        for pending in self.pending:
            for h in HORIZONS_SEC:
                self.finish(pending, h, reason="end_of_input")
        self.pending.clear()
        return {
            "mode": "SNAPSHOT_DEPTH_TAKER_HISTORICAL_COMPARISON",
            "assumptions": {
                "order_notional_krw_excluding_fee": self.notional,
                "fee_rate_per_side": self.fee_rate, "order_latency_ms_each_side": self.latency_ms,
                "max_fill_quote_lag_ms": self.max_lag, "max_exchange_quote_age_ms": self.quote_age,
                "adverse_slip_bp_each_side": 1.0, "stress_fee_multiplier": 1.5,
                "stress_adverse_slip_bp_each_side": 2.0,
                "holding_horizons_sec_from_entry_snapshot": HORIZONS_SEC,
                "validation_boundary_recv_ms_utc": self.validation_ms,
                "train_end_purge_ms": self.purge_ms, "validation_warmup_ms": 10000,
                "flow_windows_sec": [5, 5], "flow_cut_frac": 0.0, "book_imbalance_cut_frac": 0.0,
                "candidate_selection": "FIXED_RULES_NO_FITTING_OR_HOLDOUT_SELECTION",
            },
            "eligible_opportunities": dict(self.eligible), "quality_counts": dict(self.qc),
            "observed_receive_span_ms": (self.last_ts - self.first_ts)
                if self.first_ts is not None and self.last_ts is not None else None,
            "results": [
                {"phase": phase, "signal": signal, "hold_sec": horizon, **stat.result()}
                for (phase, signal, horizon), stat in sorted(self.groups.items())],
            "notes": [
                "Historical split is not fresh forward evidence; previously inspected dates remain exploratory.",
                "Snapshots estimate taker capacity, not order acceptance, actual fills or guaranteed profit.",
                "No maker fill assumptions. No LIVE changes. No automatic strategy promotion.",
                "Spread is already in depth prices. Fees use both buy and sell funds.",
                "Each horizon is independent. Gaps, late quotes and insufficient depth remain unresolved.",
                "Skipped signals count as zero only on common complete baseline opportunities.",
                "KRW and percent are event metrics; no account MDD, portfolio return or hourly profit is inferred.",
                "Remaining dataset exclusions can bias results; inspect coverage and per-coin/day results.",
            ],
        }


def comparison_stream(files, manifest):
    """Streaming read, logical-byte hashes, and explicit corrupt/changed-file gaps."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError("nonfinite JSON constant: " + value)
    for date_str, hour_str, path in files:
        before = path.stat()
        digest = hashlib.sha256()
        entry = {"path": str(path), "size_bytes": before.st_size,
                 "sha256_uncompressed": None, "complete": False, "invalid_lines": 0}
        manifest.append(entry)
        try:
            with gzip.open(path, "rb") as stream:
                for line in stream:
                    digest.update(line)
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line, object_pairs_hook=unique, parse_constant=invalid_constant)
                        if not isinstance(event, dict):
                            raise ValueError("event must be an object")
                    except (ValueError, UnicodeError):
                        entry["invalid_lines"] += 1
                        yield {"_meta": "input_error"}
                        continue
                    yield event
            entry["complete"] = True
        except (OSError, EOFError) as exc:
            entry["read_error"] = type(exc).__name__
            yield {"_meta": "input_error"}
        finally:
            entry["sha256_uncompressed"] = digest.hexdigest()
            try:
                after = path.stat()
                entry["changed_while_reading"] = (
                    before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
                    or before.st_ino != after.st_ino)
            except OSError:
                entry["changed_while_reading"] = True
            if entry["changed_while_reading"]:
                yield {"_meta": "input_error"}


def run_execution_comparison(args):
    files = iter_event_files(args.data_dir, args.days)
    if not files:
        raise ValueError("no WS input files")
    if args.validation_from:
        boundary = dt.datetime.fromisoformat(args.validation_from.replace("Z", "+00:00"))
        if boundary.tzinfo is None:
            raise ValueError("--validation-from requires an explicit timezone, e.g. 2026-10-14T00:00:00Z")
        validation_ms = boundary.timestamp() * 1000
    else:
        days = sorted({day for day, _, _ in files})
        # Boundary derives only from file calendar inventory, never outcomes.
        validation_ms = (dt.datetime.fromisoformat(days[max(1, int(len(days) * .7))])
                         .replace(tzinfo=dt.timezone.utc).timestamp() * 1000) if len(days) >= 2 else None
    output = Path(args.out).resolve()
    if output.suffix.lower() != ".json":
        raise ValueError("--out must be a .json path; comparison CSV uses the same stem")
    csv_output = output.with_suffix(".csv")
    for target in (Path(args.out), Path(args.out).with_suffix(".csv")):
        if target.is_symlink() or any(
                target.resolve() == path.resolve()
                or (target.exists() and target.samefile(path)) for _, _, path in files):
            raise ValueError("refusing to overwrite/link an input file")
    engine = ExecutionComparison(args.order_krw, args.latency_ms, args.fee_rate,
                                 validation_ms=validation_ms)
    manifest = []
    for event in comparison_stream(files, manifest):
        engine.step(event)
    report = engine.result()
    report["input_manifest"] = manifest
    report["generated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["input_mutation_or_read_error"] = any(
        not item["complete"] or item["changed_while_reading"] or item["invalid_lines"]
        for item in manifest)
    report["source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    columns = ["phase", "signal", "hold_sec", "selected_signals", "snapshot_entry_capacity_ok",
               "completed_roundtrips", "selected_unresolved", "signals_skipped", "mean_net_pct",
               "positive_rate", "stress_mean_net_pct", "mean_net_pct_without_top5",
               "worst_5pct_mean_net_pct", "paired_complete_opportunities",
               "net_pct_per_complete_opportunity_including_skips",
               "delta_vs_all_pct_per_complete_opportunity"]
    with csv_output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader(); writer.writerows(report["results"])
    print("OFFLINE COST-AFTER COMPARISON — simulated snapshot capacity, not LIVE profit")
    for row in report["results"]:
        print("%s | %s | %ss | selected=%s complete=%s unknown=%s | net_pct=%s stress=%s" % (
            row["phase"], row["signal"], row["hold_sec"], row["selected_signals"],
            row["completed_roundtrips"], row["selected_unresolved"],
            row["mean_net_pct"], row["stress_mean_net_pct"]))
    print("Results: %s %s" % (output, csv_output))
    return report


def main():
    args = parse_args()
    if args.sample_only:
        args.days = 2
    if args.execution_compare:
        try:
            run_execution_comparison(args)
        except (ValueError, OSError) as exc:
            print(f"[probe] comparison error: {exc}", file=sys.stderr)
            sys.exit(2)
        return

    print(f"[probe] data-dir: {args.data_dir}")
    print(f"[probe] horizons: {HORIZONS_SEC}s · pressure window: {PRESSURE_WINDOW_SEC}s "
          f"· fee: {FEE_ROUNDTRIP_PCT}% roundtrip")
    print(f"[probe] loading recent {args.days} days...")

    files = iter_event_files(args.data_dir, args.days)
    print(f"[probe] files: {len(files)}")
    if not files:
        print("[probe] ERROR: no files found")
        sys.exit(2)

    states = {}  # code -> CoinState
    tracker = ForwardTracker()
    completed_all = []

    event_count = 0
    skipped_meta = 0
    skipped_gap = 0
    t_start = time.time()
    last_log_ts = 0

    for date_str, hour_str, ev in stream_events(files):
        event_count += 1

        # _meta events = session boundaries · skip (and could invalidate pending if gap)
        if "_meta" in ev:
            skipped_meta += 1
            # On reconnect, drop pending entries that are stale
            if ev.get("_meta") in ("recv_timeout", "connect"):
                dropped = tracker.expire_stale(ev.get("recv_ts", 0), max(HORIZONS_SEC))
                skipped_gap += dropped
            continue

        recv_ts = ev.get("recv_ts")
        code = ev.get("code")
        ev_type = ev.get("type")
        if not recv_ts or not code or not ev_type:
            continue

        if code not in states:
            states[code] = CoinState(code)
        st = states[code]

        if ev_type == "orderbook":
            st.update_orderbook(ev, recv_ts)
            # try to fill forward entries whose horizon reached
            done = tracker.update(code, recv_ts, st.best_bid)
            completed_all.extend(done)

        elif ev_type == "trade":
            st.update_trade(ev, recv_ts)
            # trade also updates TOB (best_bid/ask fields) · try to fill
            done = tracker.update(code, recv_ts, st.best_bid)
            completed_all.extend(done)

            # consider this trade event as a probe trigger (buy pressure state)
            # dedup: at least EVENT_DEDUP_SEC since last probe for this code
            if recv_ts - st.last_probe_ts < EVENT_DEDUP_SEC * 1000:
                continue
            pressure = st.buy_pressure(recv_ts)
            if pressure is None:
                continue
            if st.best_ask is None:
                continue
            # Entry latency: recv_ts - exchange timestamp (if present)
            ex_ts = ev.get("timestamp") or ev.get("trade_timestamp")
            latency = (recv_ts - int(ex_ts)) if ex_ts else None
            # Create new forward entry
            tracker.add(code, recv_ts, st.best_ask, pressure, latency_ms=latency)
            st.last_probe_ts = recv_ts

        # progress log every 500k events
        if event_count % 500000 == 0:
            now = time.time()
            if now - last_log_ts > 5:
                elapsed = now - t_start
                print(f"[probe] events={event_count:,} completed={len(completed_all):,} "
                      f"pending={len(tracker.pending)} elapsed={elapsed:.1f}s")
                last_log_ts = now

    elapsed = time.time() - t_start
    print(f"[probe] DONE events={event_count:,} completed={len(completed_all):,} "
          f"skipped_meta={skipped_meta} skipped_gap={skipped_gap} elapsed={elapsed:.1f}s")
    print(f"[probe] coins observed: {len(states)}: {sorted(states.keys())}")
    print(f"[probe] completed by coin: {collections.Counter(c['code'] for c in completed_all).most_common()}")

    # Data coverage summary (date range + file count · 추측 X · 실측만)
    if files:
        date_set = sorted(set(dt for dt, hr, pt in files))
        print(f"[probe] data coverage: {len(files)} files · dates {date_set[0]}~{date_set[-1]} ({len(date_set)} days)")
    # Events/day 추정
    if completed_all:
        completed_days = len(set(dt.datetime.utcfromtimestamp(c["entry_ts"] / 1000).strftime("%Y-%m-%d")
                                  for c in completed_all))
        if completed_days > 0:
            print(f"[probe] completed events/day avg: {len(completed_all) / completed_days:.1f} "
                  f"(across {completed_days} days)")

    results = analyze_results(completed_all)

    # ===== 콘솔 표 =====
    print()
    print("=" * 100)
    print("MICRO-EDGE PROBE · Taker benchmark · fee %.2f%% roundtrip only · spread 이미 가격 반영 (이중 차감 X)"
          % FEE_ROUNDTRIP_PCT)
    print("=" * 100)
    headers = ["Group", "n_events"] + [f"{h}s_net%" for h in HORIZONS_SEC] + \
              [f"{h}s_pos%" for h in HORIZONS_SEC] + [f"{h}s_no_top5%" for h in HORIZONS_SEC]
    print("  ".join(h.rjust(12) for h in headers))
    print("-" * 100)
    for grp_name in ["ALL", "BUY_PRESSURE_P80+", "BUY_PRESSURE_P90+", "BUY_PRESSURE_P95+"]:
        if grp_name not in results:
            continue
        g = results[grp_name]
        row = [grp_name, str(g.get("n", 0))]
        for h in HORIZONS_SEC:
            v = g["horizons"].get(str(h), {}).get("avg_net_pct")
            row.append(f"{v:+.4f}" if v is not None else "-")
        for h in HORIZONS_SEC:
            v = g["horizons"].get(str(h), {}).get("positive_rate")
            row.append(f"{v*100:.1f}%" if v is not None else "-")
        for h in HORIZONS_SEC:
            v = g["horizons"].get(str(h), {}).get("avg_net_pct_no_top5")
            row.append(f"{v:+.4f}" if v is not None else "-")
        print("  ".join(c.rjust(12) for c in row))
    print("=" * 100)
    print()
    print("판정 가이드 (advisor 1+2 지시):")
    print("- Pressure 상위로 갈수록 net 이 monotone 하게 좋아지면 → F2c 정식 사전등록 TRAIN/OOS")
    print("- 어느 group 에서도 net ≤ 0 또는 top-5 제거 후 음수 → 이 scope 에서 NO (업비트 전체 아님)")
    print("- Positive rate · top-5 제거 비교로 fat-tail 의존도 판정")
    print()

    # ===== JSON 저장 =====
    out_data = {
        "metadata": {
            "contract": "F2c-exploration-v1",
            "generated_at": dt.datetime.utcnow().isoformat() + "Z",
            "data_dir": args.data_dir,
            "days": args.days,
            "n_files": len(files),
            "n_events_processed": event_count,
            "n_completed_entries": len(completed_all),
            "coins_observed": sorted(states.keys()),
            "constants": {
                "HORIZONS_SEC": HORIZONS_SEC,
                "PRESSURE_WINDOW_SEC": PRESSURE_WINDOW_SEC,
                "MIN_TRADES_FOR_PRESSURE": MIN_TRADES_FOR_PRESSURE,
                "EVENT_DEDUP_SEC": EVENT_DEDUP_SEC,
                "FEE_ROUNDTRIP_PCT": FEE_ROUNDTRIP_PCT,
            },
            "notes": [
                "exploration mode · NOT formal TRAIN/OOS",
                "Taker only · maker variant not implemented",
                "ask1→bid1 includes spread · fee only subtracted (NO double-count)",
                "if any pressure group shows monotone improving net → promote to F2c pre-registration",
            ],
        },
        "results": results,
    }
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2)
        print(f"[probe] JSON output: {args.out}")
    except Exception as e:
        print(f"[probe] WARN: output write failed: {e}")


if __name__ == "__main__":
    main()
