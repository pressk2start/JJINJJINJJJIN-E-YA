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
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/home/ubuntu/scalp/research/data/ws",
                    help="ws_recorder output root (contains YYYY-MM-DD/HH.jsonl.gz)")
    ap.add_argument("--days", type=int, default=14,
                    help="recent N days to analyze (default 14 = full retain)")
    ap.add_argument("--out", default="/tmp/micro_edge_probe_result.json")
    ap.add_argument("--sample-only", action="store_true",
                    help="only 2 days for quick smoke test")
    return ap.parse_args()


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

    def add(self, code, ts_ms, entry_ask, pressure):
        if entry_ask is None or entry_ask <= 0:
            return
        self.pending.append({
            "code": code,
            "entry_ts": ts_ms,
            "entry_ask": entry_ask,
            "pressure": pressure,
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
        g["top_coin"] = coin_counter.most_common(5)
        hour_counter = collections.Counter(
            dt.datetime.utcfromtimestamp(c["entry_ts"] / 1000).hour for c in subset
        )
        g["top_hour"] = hour_counter.most_common(5)
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


def main():
    args = parse_args()
    if args.sample_only:
        args.days = 2

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
            # Create new forward entry
            tracker.add(code, recv_ts, st.best_ask, pressure)
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
