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
    args = sys.argv[1:] if argv is None else argv
    if "--opportunity-audit" in args:
        return opportunity_main(args)
    root = (args or ["leadlag_data"])[0]
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


# Optional reference-gap audit. This does NOT estimate ETH-long trade returns.
import argparse
import math
import re

GAP_KEYS = ("upbit:KRW-BTC", "upbit:KRW-ETH", "binance:BTCUSDT", "binance:ETHUSDT")
GAP_CASH, GAP_THRESHOLD = 6000.0, 0.0010
GAP_FRESH_NS, GAP_WINDOW_NS, GAP_SAMPLE_NS = 500_000_000, 300_000_000_000, 1_000_000_000


def _gap_number(value, zero=False):
    if isinstance(value, bool):
        raise ValueError("boolean price/size")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError("invalid price/size")
    return value


def gap_book(record):
    """Return (stream, bids, asks), or None for valid non-book messages."""
    source = record.get("source")
    raw = json.loads(record["raw"])
    if not isinstance(raw, dict):
        raise ValueError("raw message is not an object")
    if source == "upbit":
        if raw.get("type") != "orderbook":
            return None
        symbol = raw.get("code")
        if symbol not in ("KRW-BTC", "KRW-ETH"):
            return None
        units = raw["orderbook_units"]
        bids = [(_gap_number(u["bid_price"]), _gap_number(u["bid_size"], True)) for u in units]
        asks = [(_gap_number(u["ask_price"]), _gap_number(u["ask_size"], True)) for u in units]
    elif source == "binance":
        data, stream = raw.get("data", {}), raw.get("stream", "")
        if not isinstance(data, dict) or not isinstance(stream, str):
            raise ValueError("invalid Binance envelope")
        if not stream.endswith("@bookTicker"):
            return None
        symbol = data.get("s")
        if symbol not in ("BTCUSDT", "ETHUSDT") or stream != symbol.lower() + "@bookTicker":
            raise ValueError("unexpected book symbol")
        bids = [(_gap_number(data["b"]), _gap_number(data["B"], True))]
        asks = [(_gap_number(data["a"]), _gap_number(data["A"], True))]
    else:
        raise ValueError("unexpected source")
    if not bids or not asks or bids[0][0] > asks[0][0]:
        raise ValueError("empty/crossed book")
    if [x[0] for x in bids] != sorted((x[0] for x in bids), reverse=True) or [x[0] for x in asks] != sorted(x[0] for x in asks):
        raise ValueError("unordered book")
    if bids[0][1] <= 0 or asks[0][1] <= 0:
        raise ValueError("empty touch")
    return source + ":" + symbol, bids, asks


def modeled_reference_gap(books, baseline):
    """Hypothetical reversion gap, NOT predicted or realized profit."""
    mid = lambda key: books[key][1][0][0] * 0.5 + books[key][2][0][0] * 0.5
    ub, ue, bb, be = [mid(k) for k in GAP_KEYS]
    ratio = _gap_number((ue / be) / (ub / bb))
    fair = _gap_number(be * (ub / bb) * _gap_number(baseline))
    remaining, qty = GAP_CASH / (1 + FEE), 0.0
    for price, size in books["upbit:KRW-ETH"][2]:
        take = min(remaining, price * size)
        qty += take / price
        remaining -= take
        if remaining <= 1e-7:
            break
    if remaining > 1e-7 or books["upbit:KRW-ETH"][1][0][1] < qty:
        return None
    candidate_bid = fair * (books["upbit:KRW-ETH"][1][0][0] / ue)
    net = candidate_bid * qty * (1 - FEE) / GAP_CASH - 1
    if not math.isfinite(net):
        raise ValueError("nonfinite reference gap")
    return {"ratio": ratio, "modeled_net_frac": net}


class _OpportunityRun:
    def __init__(self, window_ns=GAP_WINDOW_NS, min_samples=240):
        self.window, self.min_samples = window_ns, min_samples
        self.hist, self.books, self.sessions = collections.deque(), {}, {}
        self.stats, self.quality = collections.Counter(), collections.Counter()
        self.last_t = self.valid_until = self.ready_until = self.warm = None
        self.last_sample = self.median = self.episode = None
        self.first_wall = self.last_wall = self.offset = None
        self.clock_bad = False

    def _end(self, t, censored):
        if self.episode is None:
            return
        duration = max(0, t - self.episode) / 1e6
        kind = "censored" if censored else "complete"
        self.stats[kind + "_episodes"] += 1
        self.stats[kind + "_duration_ms_sum"] += duration
        self.stats[kind + "_duration_ms_max"] = max(self.stats[kind + "_duration_ms_max"], duration)
        for h in (300, 1000):
            key = "survive_" if duration >= h else ("unknown_" if censored else "below_")
            self.stats[key + str(h) + "ms"] += 1
        self.episode = None

    def reset(self, t, reason):
        self._end(t, True)
        self.quality[reason] += 1
        self.books.clear()
        self.hist.clear()
        self.last_sample = self.median = self.valid_until = self.ready_until = None
        self.warm = None

    def feed(self, record):
        t, wall = record["mono_ns"], record["wall_ns"]
        if type(t) is not int or type(wall) is not int or t < 0 or wall < 0:
            raise ValueError("missing/invalid receive clocks")
        self.first_wall = wall if self.first_wall is None else min(self.first_wall, wall)
        self.last_wall = wall if self.last_wall is None else max(self.last_wall, wall)
        self.stats["records"] += 1
        if self.last_t is not None:
            for bound, field in ((self.valid_until, "valid_quote_ns"), (self.ready_until, "evaluated_quote_ns")):
                if bound is not None:
                    self.stats[field] += max(0, min(t, bound) - self.last_t)
            if self.valid_until is not None and t > self.valid_until:
                self._end(self.valid_until, True)
                self.valid_until = self.ready_until = None
            if t < self.last_t:
                self.clock_bad = True
            if t < self.last_t or t - self.last_t > 5_000_000_000:
                self.reset(t, "monotonic_backward_or_receive_gap")
        self.last_t = t
        offset = wall - t
        if self.offset is not None and abs(offset - self.offset) > 250_000_000:
            self.clock_bad = True
            self.reset(t, "clock_offset_jump")
        self.offset = offset
        kind = record.get("record_type")
        if kind in ("connect", "disconnect", "recv_timeout", "clock_jump", "stale_book_start", "disk_stop"):
            if kind == "clock_jump":
                self.clock_bad = True
            self.reset(t, kind)
            if kind == "connect":
                self.sessions[record.get("source")] = record.get("session_id")
            return
        if kind != "message":
            return
        flags = [str(x) for x in record.get("flags", [])]
        if any(x.startswith("invalid_payload") or "backward" in x or "clock_jump" in x for x in flags):
            self.clock_bad = self.clock_bad or any("clock_jump" in x for x in flags)
            self.reset(t, "flagged_payload")
            return
        source, session = record.get("source"), record.get("session_id")
        if not session:
            self.reset(t, "missing_session")
            return
        if source in self.sessions and self.sessions[source] != session:
            self.reset(t, "session_change")
        self.sessions[source] = session
        try:
            parsed = gap_book(record)
        except (ValueError, KeyError, TypeError, IndexError):
            self.reset(t, "invalid_book")
            return
        if parsed:
            key, bids, asks = parsed
            self.books[key] = (t, bids, asks)
        if len(self.books) != 4 or any(t - b[0] > GAP_FRESH_NS for b in self.books.values()):
            self._end(t, True)
            self.valid_until = self.ready_until = None
            return
        self.valid_until = min(b[0] for b in self.books.values()) + GAP_FRESH_NS
        if self.warm is None:
            self.warm = t
        while self.hist and self.hist[0][0] < t - self.window:
            self.hist.popleft()
            self.median = None
        ready = t - self.warm >= self.window and len(self.hist) >= self.min_samples
        if ready:
            if self.median is None:
                self.median = statistics.median(x[1] for x in self.hist)
            try:
                model = modeled_reference_gap(self.books, self.median)
            except (ValueError, ZeroDivisionError, OverflowError):
                self.reset(t, "invalid_model")
                return
            if model is not None:
                self.ready_until = self.valid_until
                self.stats["model_evaluations"] += 1
                if model["modeled_net_frac"] >= GAP_THRESHOLD:
                    if self.episode is None:
                        self.episode = t
                        self.stats["episode_starts"] += 1
                else:
                    self._end(t, False)
            else:
                self.quality["insufficient_depth"] += 1
                self._end(t, True)
                self.ready_until = None
        else:
            self.ready_until = None
            self._end(t, True)
        # Current observation is inserted AFTER evaluating against the prior baseline.
        if self.last_sample is None or t - self.last_sample >= GAP_SAMPLE_NS:
            mid = lambda k: self.books[k][1][0][0] * 0.5 + self.books[k][2][0][0] * 0.5
            try:
                ratio = _gap_number((mid(GAP_KEYS[1]) / mid(GAP_KEYS[3])) / (mid(GAP_KEYS[0]) / mid(GAP_KEYS[2])))
            except (ValueError, ZeroDivisionError, OverflowError):
                self.reset(t, "invalid_model")
                return
            self.hist.append((t, ratio))
            self.last_sample, self.median = t, None

    def finish(self):
        if self.last_t is not None:
            self._end(self.last_t, True)
        return {"statistics": dict(self.stats), "quality": dict(self.quality),
                "valid_quote_seconds": self.stats["valid_quote_ns"] / 1e9,
                "evaluated_quote_seconds": self.stats["evaluated_quote_ns"] / 1e9,
                "wall_interval_ns": [self.first_wall, self.last_wall], "clock_jump": self.clock_bad}


def _gap_file_key(path):
    match = re.fullmatch(r"(\d{8}T\d{2})_(\d+)\.jsonl\.gz", path.name)
    if not match:
        raise ValueError("unrecognized collector filename: " + str(path))
    # Part is monotonically increasing within a run, even if wall time reverses.
    return int(match.group(2)), match.group(1)


def opportunity_audit(root, out):
    root, out = Path(root).resolve(), Path(out).resolve()
    if out == root or root in out.parents:
        raise ValueError("output must be outside the raw input directory")
    runs, errors, manifest = [], [], []
    groups = collections.defaultdict(list)
    for path in root.glob("*/*.jsonl.gz"):
        groups[path.parent].append(path)
    for directory, paths in sorted(groups.items()):
        audit = _OpportunityRun()
        try:
            paths = sorted(paths, key=_gap_file_key)
        except ValueError as error:
            errors.append(str(error))
            continue
        for path in paths:
            before = path.stat()
            manifest.append({"path": str(path), "bytes": before.st_size, "mtime_ns": before.st_mtime_ns})
            try:
                with gzip.open(path, "rt", encoding="utf-8") as stream:
                    for line in stream:
                        audit.feed(json.loads(line))
            except (OSError, EOFError, ValueError, KeyError, TypeError) as error:
                errors.append(str(path) + ": " + type(error).__name__)
                audit.reset(audit.last_t or 0, "input_incomplete")
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                errors.append(str(path) + ": changed_while_reading")
        result = audit.finish()
        result["run"] = directory.name
        runs.append(result)
    intervals = sorted(r["wall_interval_ns"] for r in runs if r["wall_interval_ns"][0] is not None)
    overlap = any(b[0] <= a[1] for a, b in zip(intervals, intervals[1:]))
    stats, quality = collections.Counter(), collections.Counter()
    for run in runs:
        for key, value in run["statistics"].items():
            stats[key] = max(stats[key], value) if key.endswith("_max") else stats[key] + value
        quality.update(run["quality"])
    seconds = sum(r["evaluated_quote_seconds"] for r in runs)
    blocked = bool(errors) or overlap or any(r["clock_jump"] for r in runs)
    rate = None if blocked or seconds <= 0 else stats["episode_starts"] * 3600 / seconds
    report = {"status": "INPUT_INCOMPLETE" if errors or not runs else "EXPLORATION_ONLY",
              "trade_profit": "TRADE_PROFIT_NOT_ESTIMATED", "runs": runs, "input_files": manifest,
              "input_errors": errors, "overlapping_runs": overlap, "statistics": dict(stats),
              "quality": dict(quality), "episode_starts_per_evaluated_hour": rate,
              "assumptions": {"cash_including_buy_fee": GAP_CASH, "fee_oneway": FEE,
                  "freshness_ms": 500, "baseline_seconds": 300, "minimum_prior_samples": 240,
                  "modeled_reference_gap_after_costs_min_frac": GAP_THRESHOLD,
                  "warning": "Reference can move because BTC moves; ETH-long return is NOT estimated.",
                  "duration_note": "300/1000ms survival is descriptive only, not a future entry filter."}}
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    short = [
        "STATUS: " + report["status"] + " | TRADE_PROFIT_NOT_ESTIMATED",
        f"INPUT: runs={len(runs)} files={len(manifest)} read_errors={len(errors)} overlap={overlap}",
        f"COVERAGE: fresh_seconds={sum(r['valid_quote_seconds'] for r in runs):.3f} evaluated_seconds={seconds:.3f}",
        f"EPISODES: starts={stats['episode_starts']} complete={stats['complete_episodes']} censored={stats['censored_episodes']}",
        f"SURVIVAL: 300ms={stats['survive_300ms']} unknown={stats['unknown_300ms']} 1000ms={stats['survive_1000ms']} unknown={stats['unknown_1000ms']}",
        "RATE_PER_EVALUATED_HOUR: " + ("UNKNOWN" if rate is None else f"{rate:.4f}"),
        "CAUTION: BTC-reference confounding; hypothetical gap is NOT ETH-long profit.",
        "OUTPUT: " + str(out / "report.json")]
    (out / "report.txt").write_text("\n".join(short) + "\n", encoding="utf-8")
    print("\n".join(short))
    return 1 if report["status"] == "INPUT_INCOMPLETE" else 0


def opportunity_main(argv):
    parser = argparse.ArgumentParser(description="Exploratory reference-gap opportunity audit; no trading")
    parser.add_argument("root", nargs="?", default="leadlag_data")
    parser.add_argument("--opportunity-audit", action="store_true")
    parser.add_argument("--out", default="leadlag_gap_audit")
    args = parser.parse_args(argv)
    return opportunity_audit(args.root, args.out)


if __name__ == "__main__":
    sys.exit(main())
