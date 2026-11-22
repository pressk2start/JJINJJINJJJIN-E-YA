# -*- coding: utf-8 -*-
"""1분 급등 보완(검토 의견 2026-11-20 ②): 처음 받을 때 저가를 저장하지 않아, 신호가 걸친 200분 덩어리만 다시 받아 저가를 채운다.
결과: DATA/lows_<종목>.jsonl 에 {"b": 덩어리, "bars": {분: [시가, 고가, 저가, 종가]}} 한 줄씩. 다시 돌리면 받은 덩어리는 건너뛴다."""
import datetime as dt
import importlib.util
import json
import pickle
import sys
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

R = Path(__file__).resolve().parents[3]                   # scalp/research
spec = importlib.util.spec_from_file_location("auto_monitor", str(R / "auto_monitor.py"))
AM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AM)

DATA = Path(sys.argv[1])
BLOCK, SPAN = 200, 13                      # 신호 분부터 진입(다음 거래 봉) + 10분 창이 들어가도록 13분을 덮는다
sig = pickle.loads((DATA / "signals.pkl").read_bytes())
need = {}
for s in sig:
    for t in (s[2], s[2] + SPAN):
        need.setdefault(s[1], set()).add(t // BLOCK)

lock = threading.Lock()
done = {}
for m in need:
    f = DATA / ("lows_%s.jsonl" % m)
    got = set()
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                got.add(json.loads(line)["b"])
            except ValueError:
                continue
    done[m] = got

jobs = [(m, b) for m in sorted(need) for b in sorted(need[m]) if b not in done[m]]
print("받을 덩어리 %d (전체 %d)" % (len(jobs), sum(len(v) for v in need.values())), flush=True)


def one(job):
    m, b = job
    to = dt.datetime.fromtimestamp((b + 1) * BLOCK * 60, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = AM.http_get("%s/v1/candles/minutes/1?market=%s&count=200&to=%s" % (AM.UPBIT, m, urllib.parse.quote(to)))
    bars = {}
    for r in rows:
        t = int(AM.parse_utc(r["candle_date_time_utc"]).timestamp() // 60)
        if b * BLOCK <= t < (b + 1) * BLOCK:
            bars[t] = [float(r["opening_price"]), float(r["high_price"]), float(r["low_price"]), float(r["trade_price"])]
    with lock:
        with open(DATA / ("lows_%s.jsonl" % m), "a") as f:
            f.write(json.dumps({"b": b, "bars": bars}) + "\n")


n = 0
with ThreadPoolExecutor(4) as ex:
    for _ in ex.map(one, jobs):
        n += 1
        if n % 500 == 0:
            print("  %d / %d" % (n, len(jobs)), flush=True)
print("끝", flush=True)
