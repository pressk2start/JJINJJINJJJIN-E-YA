# -*- coding: utf-8 -*-
"""급락 사건(2017~, 15분봉 신호 파일) 주변의 5분봉을 받아 둔다 — 사건 종목과 BTC·ETH, 신호 30분 전 ~ 60분 뒤. 공개 시세만.
저장: CACHE/m5_<종목>.jsonl 에 {"b": 덩어리(1000분), "bars": {분: [시가, 고가, 저가, 종가]}} 한 줄씩. 받은 덩어리는 건너뛴다.
사용: fetch5.py SIGNALS.pkl CACHE [동시 수]"""
import datetime as dt
import json
import pickle
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SIG, CACHE = pickle.load(open(sys.argv[1], "rb"))["A"], Path(sys.argv[2])
THREADS = int(sys.argv[3]) if len(sys.argv) > 3 else 8
BLOCK, UTC = 1000, dt.timezone.utc
CACHE.mkdir(parents=True, exist_ok=True)
need = {}
for s in SIG:
    t = (s["q"] + 1) * 15
    for m in (s["m"], "KRW-BTC", "KRW-ETH"):
        for b in range((t - 30) // BLOCK, (t + 59) // BLOCK + 1):
            need.setdefault(m, set()).add(b)
have = {}
for m in need:
    f, got = CACHE / ("m5_%s.jsonl" % m), set()
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                got.add(json.loads(line)["b"])
            except ValueError:
                continue
    have[m] = got
jobs = [(m, b) for m in sorted(need) for b in sorted(need[m]) if b not in have[m]]
print("받을 덩어리 %d (필요 %d)" % (len(jobs), sum(len(v) for v in need.values())), flush=True)
gate, nxt, lock = threading.Lock(), [0.0], threading.Lock()


def get(url):
    last = "?"
    for k in range(10):
        with gate:
            wait = nxt[0] - time.time()
            if wait > 0:
                time.sleep(wait)
            nxt[0] = max(time.time(), nxt[0]) + 0.125
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "candle-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                body, rem = r.read(), r.headers.get("Remaining-Req", "")
            if "sec=" in rem and int(rem.split("sec=")[1].split(";")[0]) <= 1:
                with gate:
                    nxt[0] = max(nxt[0], time.time() + 0.6)
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            last = "HTTP %s" % exc.code
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except Exception as exc:
            last = type(exc).__name__
        time.sleep(1.0 + k)
    raise RuntimeError("조회 실패 (%s) %s" % (last, url))


def one(job):
    m, b = job
    to = dt.datetime.fromtimestamp((b + 1) * BLOCK * 60, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    bars = {}
    for r in get("https://api.upbit.com/v1/candles/minutes/5?market=%s&count=200&to=%s" % (m, urllib.parse.quote(to))) or []:
        t = int(dt.datetime.strptime(r["candle_date_time_utc"][:16], "%Y-%m-%dT%H:%M").replace(tzinfo=UTC).timestamp() // 60)
        if b * BLOCK <= t < (b + 1) * BLOCK:
            bars[t] = [r["opening_price"], r["high_price"], r["low_price"], r["trade_price"]]
    with lock:
        with open(CACHE / ("m5_%s.jsonl" % m), "a") as f:
            f.write(json.dumps({"b": b, "bars": bars}) + "\n")


n = 0
with ThreadPoolExecutor(THREADS) as ex:
    for _ in ex.map(one, jobs):
        n += 1
        if n % 1000 == 0:
            print("  %d / %d" % (n, len(jobs)), flush=True)
print("끝", flush=True)
