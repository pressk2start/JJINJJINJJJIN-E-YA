#!/usr/bin/env python3
"""Public-only Binance/Upbit paired recorder. No keys, orders, or bot imports."""
import argparse
import asyncio
import base64
import datetime as dt
import gzip
import hashlib
import inspect
import json
import math
import os
import shutil
import signal
import sys
import time
import unittest
import uuid
from collections import Counter
from pathlib import Path

UPBIT = "wss://api.upbit.com/websocket/v1"
BINANCE = ("wss://data-stream.binance.vision/stream?streams="
           "btcusdt@bookTicker/ethusdt@bookTicker/btcusdt@trade/ethusdt@trade")
UP_MARKETS = ("KRW-BTC", "KRW-ETH")
BN_SYMBOLS = ("BTCUSDT", "ETHUSDT")
STREAM_KEYS = ("upbit:KRW-BTC:book", "upbit:KRW-ETH:book",
               "upbit:KRW-BTC:trade", "upbit:KRW-ETH:trade",
               "binance:BTCUSDT:book", "binance:ETHUSDT:book",
               "binance:BTCUSDT:trade", "binance:ETHUSDT:trade")
SCHEMA = 1


def upbit_subscription(ticket):
    """Public books and actual taker trades on the same connection."""
    return [{"ticket": ticket},
            {"type": "orderbook", "codes": list(UP_MARKETS), "level": 0},
            {"type": "trade", "codes": list(UP_MARKETS)},
            {"format": "DEFAULT"}]


def positive(value):
    if isinstance(value, bool):
        raise ValueError("boolean_price_or_size")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("nonpositive_or_nonfinite")
    return value


def integer(value):
    if isinstance(value, bool):
        raise ValueError("boolean_timestamp_or_id")
    value = int(value)
    if value < 0:
        raise ValueError("negative_timestamp_or_id")
    return value


def parse_public(source, raw):
    """Validate without changing the received object; raw text is always retained."""
    item = json.loads(raw)
    if not isinstance(item, dict):
        raise ValueError("not_object")
    if source == "upbit":
        symbol = item.get("code")
        if symbol not in UP_MARKETS:
            raise ValueError("unexpected_upbit_market")
        if item.get("type") == "trade":
            required = ("trade_price", "trade_volume", "ask_bid",
                        "trade_timestamp", "timestamp", "sequential_id")
            if not all(key in item for key in required):
                raise ValueError("missing_upbit_trade_field")
            if item["ask_bid"] not in ("ASK", "BID"):
                raise ValueError("invalid_upbit_taker_side")
            positive(item["trade_price"])
            positive(item["trade_volume"])
            return {"symbol": symbol, "kind": "trade",
                    "exchange_ms": integer(item["trade_timestamp"]),
                    "event_ms": integer(item["timestamp"]),
                    "native_id": integer(item["sequential_id"]),
                    "side": item["ask_bid"]}
        if item.get("type") != "orderbook":
            raise ValueError("unexpected_upbit_message")
        units = item.get("orderbook_units")
        if not isinstance(units, list) or not units:
            raise ValueError("empty_book")
        bids, asks = [], []
        for u in units:
            bids.append(positive(u["bid_price"]))
            asks.append(positive(u["ask_price"]))
            positive(u["bid_size"])
            positive(u["ask_size"])
        if bids != sorted(bids, reverse=True) or asks != sorted(asks):
            raise ValueError("unordered_book")
        if bids[0] > asks[0]:
            raise ValueError("crossed_book")
        return {"symbol": symbol, "kind": "book",
                "exchange_ms": integer(item["timestamp"]), "native_id": None}
    if source != "binance":
        raise ValueError("unknown_source")
    data = item.get("data")
    stream = item.get("stream")
    if not isinstance(data, dict) or not isinstance(stream, str):
        raise ValueError("missing_combined_envelope")
    symbol = data.get("s")
    if symbol not in BN_SYMBOLS or not stream.startswith(symbol.lower() + "@"):
        raise ValueError("unexpected_binance_symbol")
    if stream.endswith("@bookTicker"):
        bid, ask = positive(data["b"]), positive(data["a"])
        positive(data["B"])
        positive(data["A"])
        if bid > ask:
            raise ValueError("crossed_book")
        return {"symbol": symbol, "kind": "book",
                "exchange_ms": integer(data["E"]) if "E" in data else None,
                "native_id": integer(data["u"])}
    if stream.endswith("@trade") and data.get("e") == "trade":
        positive(data["p"])
        positive(data["q"])
        return {"symbol": symbol, "kind": "trade",
                "exchange_ms": integer(data["T"]),
                "event_ms": integer(data["E"]), "native_id": integer(data["t"])}
    raise ValueError("unexpected_binance_message")


def sequence_flag(previous, key, value):
    """IDs need not be consecutive. A nonconsecutive ID is not called data loss."""
    if value is None:
        return None
    old = previous.get(key)
    previous[key] = value if old is None else max(old, value)
    if old is not None and value <= old:
        return "duplicate_or_backward_native_id"
    return None


def simulated_net_krw(asks, bids, cash=6000.0, fee=0.0005):
    """Fee-inclusive total cash; both book sides already include their spread."""
    if not math.isfinite(fee) or not 0 <= fee < 1:
        raise ValueError("bad_fee")
    remaining, qty = positive(cash) / (1 + fee), 0.0
    for price, size in asks:
        p, s = positive(price), positive(size)
        spent = min(remaining, p * s)
        qty += spent / p
        remaining -= spent
        if remaining <= 1e-9:
            break
    if remaining > 1e-9:
        return None
    remaining, proceeds = qty, 0.0
    for price, size in bids:
        p, s = positive(price), positive(size)
        sold = min(remaining, s)
        proceeds += sold * p
        remaining -= sold
        if remaining <= 1e-12:
            break
    return None if remaining > 1e-12 else proceeds * (1 - fee) - cash


class Writer:
    def __init__(self, root):
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = now + "_" + uuid.uuid4().hex[:10]
        self.root = Path(root) / self.run_id
        self.root.mkdir(parents=True, exist_ok=False)
        self.handle, self.hour, self.part = None, None, 0
        self.events, self.raw_bytes = 0, 0

    def write(self, record):
        record = dict(record)
        record.setdefault("wall_ns", time.time_ns())
        record.setdefault("mono_ns", time.monotonic_ns())
        record["schema"] = SCHEMA
        record["run_id"] = self.run_id
        hour = dt.datetime.fromtimestamp(record["wall_ns"] / 1e9,
                                        dt.timezone.utc).strftime("%Y%m%dT%H")
        if hour != self.hour:
            self.close()
            path = self.root / (hour + "_" + str(self.part) + ".jsonl.gz")
            self.part += 1
            self.handle = gzip.open(path, "xt", encoding="utf-8", compresslevel=1)
            self.hour = hour
        text = json.dumps(record, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")) + "\n"
        self.handle.write(text)
        self.events += 1
        self.raw_bytes += len(text.encode("utf-8"))

    def status(self, record):
        self.flush()
        temp = self.root / "status.json.tmp"
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2,
                                   allow_nan=False), encoding="utf-8")
        os.replace(temp, self.root / "status.json")

    def flush(self):
        if self.handle:
            self.handle.flush()

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None


async def wait_or_stop(stop, seconds):
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def collect(args):
    try:
        import websockets
    except ImportError:
        raise SystemExit('Install first: py -m pip install "websockets>=12,<17"')
    stop = asyncio.Event()
    writer = Writer(args.dir)
    counts, notices, latest, stale = Counter(), Counter(), {}, set()
    connected = set()
    started = time.monotonic()
    clock_offset = [time.time_ns() - time.monotonic_ns()]
    failures = []
    ws_options = dict(open_timeout=15, close_timeout=2, ping_interval=20,
                      ping_timeout=20, max_size=2 ** 22, max_queue=128)
    if "proxy" in inspect.signature(websockets.connect).parameters:
        ws_options["proxy"] = None

    def emit(kind, **fields):
        writer.write(dict(record_type=kind, **fields))

    manifest = {"record_type": "manifest", "public_only": True,
                "markets": list(UP_MARKETS), "binance_symbols": list(BN_SYMBOLS),
                "sample_ms": args.sample_ms, "raw_sampling": False,
                "requested_streams": list(STREAM_KEYS),
                "duration_hours": args.duration_hours,
                "websockets_version": getattr(websockets, "__version__", "unknown"),
                "proxy_policy": "disabled_when_supported; older_clients_direct",
                "python": sys.version.split()[0],
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "purpose": "data_quality_only; not a profit test"}
    writer.write(manifest)
    print("PUBLIC_ONLY no keys, no orders. Raw messages are NOT sampled.", flush=True)
    print("OUTPUT: " + str(writer.root.resolve()), flush=True)

    async def receiver(source, url):
        retry = 1.0
        while not stop.is_set():
            session, sequence, native = uuid.uuid4().hex, 0, {}
            try:
                async with websockets.connect(url, **ws_options) as ws:
                    connected.add(source)
                    emit("connect", source=source, session_id=session)
                    notices[source + "_connects"] += 1
                    if source == "upbit":
                        await ws.send(json.dumps(upbit_subscription(writer.run_id)))
                    retry = 1.0
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        wall, mono = time.time_ns(), time.monotonic_ns()
                        sequence += 1
                        flags = []
                        offset = wall - mono
                        if abs(offset - clock_offset[0]) > 250_000_000:
                            emit("clock_jump", shift_ns=offset - clock_offset[0])
                            notices["clock_jump"] += 1
                            flags.append("clock_jump")
                        clock_offset[0] = offset
                        rec = {"record_type": "message", "source": source,
                               "session_id": session, "connection_seq": sequence,
                               "wall_ns": wall, "mono_ns": mono}
                        if isinstance(raw, bytes):
                            try:
                                raw = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                rec["raw_base64"] = base64.b64encode(raw).decode("ascii")
                                raw = None
                        if raw is not None:
                            rec["raw"] = raw
                        try:
                            if raw is None:
                                raise ValueError("invalid_utf8")
                            parsed = parse_public(source, raw)
                            rec.update(parsed)
                            key = source + ":" + parsed["symbol"] + ":" + parsed["kind"]
                            flag = sequence_flag(native, key, parsed["native_id"])
                            if flag:
                                flags.append(flag)
                                notices[flag] += 1
                            counts[key] += 1
                            if parsed["kind"] == "book":
                                latest[key] = mono
                        except (ValueError, TypeError, KeyError, OverflowError) as exc:
                            flags.append("invalid_payload:" + str(exc)[:100])
                            notices["invalid_payload"] += 1
                        rec["flags"] = flags
                        writer.write(rec)
            except asyncio.CancelledError:
                emit("disconnect", source=source, session_id=session, reason="shutdown")
                raise
            except Exception as exc:
                emit("disconnect", source=source, session_id=session,
                     reason=type(exc).__name__ + ":" + str(exc)[:200])
                notices[source + "_disconnects"] += 1
                print("RECONNECT " + source + " " + type(exc).__name__, flush=True)
            finally:
                connected.discard(source)
                for key in list(latest):
                    if key.startswith(source + ":"):
                        latest.pop(key, None)
            if not stop.is_set():
                await wait_or_stop(stop, retry)
                retry = min(retry * 2, 30)

    expected = STREAM_KEYS

    def snapshot(final=False):
        elapsed = time.monotonic() - started
        age_ms = {key: round((time.monotonic_ns() - latest[key]) / 1e6, 1)
                  if key in latest else None for key in expected if key.endswith(":book")}
        return {"run_id": writer.run_id, "elapsed_seconds": round(elapsed, 3),
                "final": final, "counts": dict(counts), "notices": dict(notices),
                "missing_streams": [key for key in expected if counts[key] == 0],
                "connected_sources": sorted(connected), "book_age_ms": age_ms,
                "stale_streams": sorted(stale),
                "events": writer.events, "uncompressed_bytes": writer.raw_bytes,
                "failures": failures, "profit_verdict": "NOT_EVALUATED"}

    async def heartbeat():
        last_display = 0.0
        while not stop.is_set():
            now = time.monotonic()
            if args.duration_hours and now - started >= args.duration_hours * 3600:
                stop.set()
                break
            free = shutil.disk_usage(writer.root).free
            if free < args.min_free_mb * 1024 ** 2:
                failures.append("disk_free_below_limit")
                emit("disk_stop", free_bytes=free)
                stop.set()
                break
            for key in expected:
                if not key.endswith(":book"):
                    continue
                when = latest.get(key)
                old = when is None or time.monotonic_ns() - when > 5_000_000_000
                if old and key not in stale:
                    emit("stale_book_start", stream=key)
                    stale.add(key)
                    notices["stale_book_start"] += 1
                elif not old and key in stale:
                    emit("stale_book_end", stream=key)
                    stale.remove(key)
            writer.status(snapshot())
            if now - last_display >= 30:
                last_display = now
                print("HEALTH seconds=" + str(round(now - started)) +
                      " valid_messages=" + str(sum(counts.values())) +
                      " invalid=" + str(notices["invalid_payload"]), flush=True)
            await wait_or_stop(stop, args.sample_ms / 1000)

    old_handlers = {}
    def request_stop(*_):
        stop.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            old_handlers[sig] = signal.signal(sig, request_stop)
        except (ValueError, OSError):
            pass
    tasks = []
    def finished(task):
        if not task.cancelled() and task.exception() is not None:
            failures.append(type(task.exception()).__name__ + ":" + str(task.exception())[:200])
            stop.set()
    try:
        tasks = [asyncio.create_task(receiver("upbit", UPBIT)),
                 asyncio.create_task(receiver("binance", BINANCE)),
                 asyncio.create_task(heartbeat())]
        for task in tasks:
            task.add_done_callback(finished)
        await stop.wait()
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=5)
            for task in pending:
                task.cancel()
                failures.append("shutdown_task_timeout")
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        result = snapshot(final=True)
        result["status"] = ("COLLECTION_INCOMPLETE" if failures or result["missing_streams"]
                            else "COLLECTION_RECORDED_CHECK_QUALITY_MARKERS")
        try:
            emit("shutdown", **result)
            writer.status(result)
        finally:
            writer.close()
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
        print("COLLECTION_STATUS: " + result["status"], flush=True)
        print("PROFIT_VERDICT: NOT_EVALUATED", flush=True)
        print("STATUS_FILE: " + str((writer.root / "status.json").resolve()), flush=True)
    return 1 if failures or result["missing_streams"] else 0


def selftest():
    class Checks(unittest.TestCase):
        def test_public_parsers_preserve_input(self):
            obj = {"type": "orderbook", "code": "KRW-BTC", "timestamp": 1234,
                   "orderbook_units": [
                       {"bid_price": 100, "ask_price": 101, "bid_size": 2, "ask_size": 3},
                       {"bid_price": 99, "ask_price": 102, "bid_size": 4, "ask_size": 5}]}
            before = json.dumps(obj)
            self.assertEqual(parse_public("upbit", before)["exchange_ms"], 1234)
            self.assertEqual(json.dumps(obj), before)
            obj["orderbook_units"].reverse()
            with self.assertRaises(ValueError):
                parse_public("upbit", json.dumps(obj))
            bn = {"stream": "btcusdt@bookTicker",
                  "data": {"s": "BTCUSDT", "b": "100", "a": "101",
                           "B": "2", "A": "3", "u": 9}}
            self.assertEqual(parse_public("binance", json.dumps(bn))["kind"], "book")
            bn["data"]["a"] = "nan"
            with self.assertRaises(ValueError):
                parse_public("binance", json.dumps(bn))

        def test_sequence_is_not_gap_inference(self):
            prev = {}
            self.assertIsNone(sequence_flag(prev, "x", 10))
            self.assertIsNone(sequence_flag(prev, "x", 20))
            self.assertEqual(sequence_flag(prev, "x", 19),
                             "duplicate_or_backward_native_id")

        def test_cost_uses_depth_and_fees_once(self):
            actual = simulated_net_krw([(100, 100)], [(101, 100)])
            expected = (6000 / 1.0005 / 100) * 101 * 0.9995 - 6000
            self.assertAlmostEqual(actual, expected)
            self.assertIsNone(simulated_net_krw([(100, 0.01)], [(101, 100)]))
            self.assertLess(simulated_net_krw([(100, 100)], [(100, 100)]), 0)

        def test_monotonic_stamp_and_writer(self):
            import tempfile
            with tempfile.TemporaryDirectory() as temp:
                writer = Writer(temp)
                writer.write({"record_type": "test", "raw": "unchanged"})
                writer.status({"ok": True})
                writer.close()
                path = next(writer.root.glob("*.jsonl.gz"))
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    rec = json.loads(fh.readline())
                self.assertIsInstance(rec["mono_ns"], int)
                self.assertIsInstance(rec["wall_ns"], int)
                self.assertEqual(rec["raw"], "unchanged")
                self.assertEqual(json.loads((writer.root / "status.json").read_text())["ok"], True)

        def test_upbit_trade_sides_exchange_time_and_raw_preservation(self):
            for side in ("BID", "ASK"):
                with self.subTest(side=side):
                    item = {"type": "trade", "code": "KRW-ETH",
                            "trade_price": 3000000.0, "trade_volume": 0.002,
                            "ask_bid": side, "trade_timestamp": 1790000000100,
                            "timestamp": 1790000000149,
                            "sequential_id": 9007199254740993,
                            "extra_future_field": {"preserved": True}}
                    raw = json.dumps(item, separators=(",", ":"))
                    parsed = parse_public("upbit", raw)
                    self.assertEqual(parsed["kind"], "trade")
                    self.assertEqual(parsed["symbol"], "KRW-ETH")
                    self.assertEqual(parsed["side"], side)
                    self.assertEqual(parsed["exchange_ms"], 1790000000100)
                    self.assertEqual(parsed["event_ms"], 1790000000149)
                    self.assertEqual(parsed["native_id"], 9007199254740993)
                    self.assertEqual(json.loads(raw), item)

        def test_upbit_trade_requires_price_size_side_and_clocks(self):
            valid = {"type": "trade", "code": "KRW-BTC", "trade_price": 100.0,
                     "trade_volume": 0.1, "ask_bid": "BID",
                     "trade_timestamp": 1000, "timestamp": 1049,
                     "sequential_id": 123}
            for field, value in (("trade_price", 0), ("trade_price", float("nan")),
                                 ("trade_volume", -0.1), ("trade_volume", float("inf")),
                                 ("trade_volume", True), ("ask_bid", "BUY"),
                                 ("ask_bid", "bid"), ("timestamp", True),
                                 ("trade_timestamp", -1), ("sequential_id", False)):
                with self.subTest(field=field, value=value):
                    bad = dict(valid)
                    bad[field] = value
                    with self.assertRaises(ValueError):
                        parse_public("upbit", json.dumps(bad))
            for field in ("trade_price", "trade_volume", "ask_bid", "trade_timestamp",
                          "timestamp", "sequential_id"):
                with self.subTest(missing=field):
                    bad = dict(valid)
                    del bad[field]
                    with self.assertRaises((KeyError, ValueError)):
                        parse_public("upbit", json.dumps(bad))

        def test_upbit_trade_rejects_unsubscribed_market_and_wrong_type(self):
            valid = {"type": "trade", "code": "KRW-BTC", "trade_price": 100.0,
                     "trade_volume": 0.1, "ask_bid": "ASK", "trade_timestamp": 1000,
                     "timestamp": 1049, "sequential_id": 123}
            for patch in ({"code": "KRW-XRP"}, {"code": "BTC-ETH"}, {"type": "ticker"}):
                with self.subTest(patch=patch):
                    bad = dict(valid, **patch)
                    with self.assertRaises(ValueError):
                        parse_public("upbit", json.dumps(bad))

        def test_subscription_and_expected_streams_include_upbit_trades(self):
            subscription = upbit_subscription("offline-ticket")
            self.assertEqual(subscription[0], {"ticket": "offline-ticket"})
            typed = {item["type"]: item for item in subscription if "type" in item}
            self.assertEqual(set(typed), {"orderbook", "trade"})
            self.assertEqual(typed["orderbook"]["codes"], list(UP_MARKETS))
            self.assertEqual(typed["orderbook"]["level"], 0)
            self.assertEqual(typed["trade"]["codes"], list(UP_MARKETS))
            expected = {"upbit:KRW-BTC:book", "upbit:KRW-ETH:book",
                        "upbit:KRW-BTC:trade", "upbit:KRW-ETH:trade",
                        "binance:BTCUSDT:book", "binance:ETHUSDT:book",
                        "binance:BTCUSDT:trade", "binance:ETHUSDT:trade"}
            self.assertEqual(set(STREAM_KEYS), expected)
            self.assertEqual(len(STREAM_KEYS), 8)

        def test_upbit_trade_native_sequence_and_old_book_compatibility(self):
            item = {"type": "trade", "code": "KRW-BTC", "trade_price": 100.0,
                    "trade_volume": 0.1, "ask_bid": "BID", "trade_timestamp": 1000,
                    "timestamp": 1049, "sequential_id": 9007199254740993}
            trade = parse_public("upbit", json.dumps(item))
            previous, key = {}, "upbit:KRW-BTC:trade"
            self.assertIsNone(sequence_flag(previous, key, trade["native_id"]))
            self.assertEqual(sequence_flag(previous, key, trade["native_id"]),
                             "duplicate_or_backward_native_id")
            self.assertIsNone(sequence_flag(previous, key, trade["native_id"] + 2))
            old_book = {"type": "orderbook", "code": "KRW-BTC", "timestamp": 1050,
                        "orderbook_units": [{"bid_price": 99, "ask_price": 101,
                                             "bid_size": 1, "ask_size": 1}]}
            parsed = parse_public("upbit", json.dumps(old_book))
            self.assertEqual(parsed["kind"], "book")
            self.assertEqual(parsed["exchange_ms"], 1050)
            self.assertIsNone(parsed["native_id"])

    result = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    if result.wasSuccessful():
        print("SELFTEST_OK synthetic parser, timestamps, writer, execution-cost calculation")
        return 0
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest", help="synthetic tests only; no WebSocket connection")
    cp = sub.add_parser("collect", help="public-only paired recording; no profit verdict")
    cp.add_argument("--dir", default="leadlag_data")
    cp.add_argument("--sample-ms", type=int, default=500,
                    help="health/status interval; raw messages are not sampled")
    cp.add_argument("--duration-hours", type=float, default=0.5,
                    help="0 means until Ctrl+C")
    cp.add_argument("--min-free-mb", type=float, default=512)
    args = ap.parse_args(argv)
    if args.command == "selftest":
        return selftest()
    if args.sample_ms < 100 or not math.isfinite(args.duration_hours) or args.duration_hours < 0:
        ap.error("sample-ms must be >=100 and duration-hours finite and >=0")
    if not math.isfinite(args.min_free_mb) or args.min_free_mb <= 0:
        ap.error("min-free-mb must be finite and positive")
    return asyncio.run(collect(args))


if __name__ == "__main__":
    raise SystemExit(main())
