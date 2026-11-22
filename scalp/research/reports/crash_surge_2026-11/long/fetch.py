# -*- coding: utf-8 -*-
"""업비트 분봉을 긴 기간 받아 저장한다 (공개 시세만). 종목마다 최신 → 과거로 넘기며, 종목 여러 개를 동시에 받는다.
사용: fetch.py OUT 단위(1|5|15) 시작일(YYYY-MM-DD) 끝일 [동시 수]
저장: OUT/c<단위>_<종목>.jsonl.gz — 한 줄 = [[분, 시가, 고가, 저가, 종가, 거래량, 거래대금], ...]. 다시 돌리면 받은 곳부터 잇는다."""
import datetime as dt
import gzip
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UNI = ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-WAVES", "KRW-AXS", "KRW-ETC", "KRW-CHZ", "KRW-SAND", "KRW-IQ", "KRW-APT",
       "KRW-ADA", "KRW-GMT", "KRW-KAVA", "KRW-POL", "KRW-TRX", "KRW-CELO", "KRW-ARK", "KRW-ALGO")
OUT, UNIT = Path(sys.argv[1]), int(sys.argv[2])
UTC = dt.timezone.utc
day = lambda s: dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
T0, T1 = int(day(sys.argv[3]).timestamp() // 60), int(day(sys.argv[4]).timestamp() // 60)
THREADS = int(sys.argv[5]) if len(sys.argv) > 5 else 8
OUT.mkdir(parents=True, exist_ok=True)
gate, nxt, count = threading.Lock(), [0.0], [0]


def get(url):
    last = "?"
    for k in range(10):
        with gate:                                           # 전체 초당 8회 이하
            wait = nxt[0] - time.time()
            if wait > 0:
                time.sleep(wait)
            nxt[0] = max(time.time(), nxt[0]) + 0.125
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "candle-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                body, rem = r.read(), r.headers.get("Remaining-Req", "")
            if "sec=" in rem and int(rem.split("sec=")[1].split(";")[0]) <= 1:      # 한도가 거의 찼으면 잠깐 쉰다
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


def one(m):
    f = OUT / ("c%d_%s.jsonl.gz" % (UNIT, m))
    cur, n = T1, 0
    if f.exists():
        try:
            with gzip.open(f, "rt") as g:
                for line in g:
                    try:
                        rows = json.loads(line)
                    except ValueError:
                        continue
                    if rows:
                        cur, n = min(cur, min(r[0] for r in rows)), n + len(rows)
        except (EOFError, OSError):                          # 끊겨서 잘린 꼬리 — 읽힌 데까지 잇는다
            pass
    while cur > T0:
        to = dt.datetime.fromtimestamp(cur * 60, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        got = get("https://api.upbit.com/v1/candles/minutes/%d?market=%s&count=200&to=%s" % (UNIT, m, urllib.parse.quote(to)))
        rows = []
        for r in got or []:
            t = int(dt.datetime.strptime(r["candle_date_time_utc"][:16], "%Y-%m-%dT%H:%M").replace(tzinfo=UTC).timestamp() // 60)
            if t < cur:
                rows.append([t, r["opening_price"], r["high_price"], r["low_price"], r["trade_price"], r["candle_acc_trade_volume"], r["candle_acc_trade_price"]])
        if not rows:
            break                                            # 상장 전 — 더 없음
        with gzip.open(f, "at") as g:
            g.write(json.dumps(rows) + "\n")
        cur, n = min(r[0] for r in rows), n + len(rows)
        with gate:
            count[0] += 1
            if count[0] % 1000 == 0:
                print("  조회 %d" % count[0], flush=True)
        if len(got) < 200:
            break
    print("%s %d분봉 %d개, 가장 이른 봉 %s" % (m[4:], UNIT, n, dt.datetime.fromtimestamp(cur * 60, tz=UTC).strftime("%Y-%m-%d")), flush=True)


with ThreadPoolExecutor(THREADS) as ex:
    list(ex.map(one, UNI))
print("끝", flush=True)
