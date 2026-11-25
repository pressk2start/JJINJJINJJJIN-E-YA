# -*- coding: utf-8 -*-
"""study.py 의 기본 칸 신호를 다른 방식(원자료를 한 분씩 도는 단순 계산)으로 다시 세어 맞춰 본다.
사용: check.py DATA OUT_DIR"""
import datetime as dt, gzip, json, statistics, sys
from pathlib import Path
import numpy as np
DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2])
UNI = ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-WAVES", "KRW-AXS", "KRW-ETC", "KRW-CHZ", "KRW-SAND", "KRW-IQ", "KRW-APT",
       "KRW-ADA", "KRW-GMT", "KRW-KAVA", "KRW-POL", "KRW-TRX", "KRW-CELO", "KRW-ARK", "KRW-ALGO")
T0 = int(dt.datetime(2025, 11, 19, tzinfo=dt.timezone.utc).timestamp() // 60)
A, B = 1500, 1500 + 30000                                  # 검산 구간(분)
sig = np.load(OUT / "signals.npz")


def raw(m):
    rows = {}
    with gzip.open(DATA / ("c1_%s.jsonl.gz" % m), "rt") as g:
        for line in g:
            try:
                page = json.loads(line)
            except ValueError:
                continue
            for r in page:
                rows[r[0] - T0] = r
    return rows


def series(rows, n):
    """거래 없는 분은 직전 종가·거래대금 0."""
    o, h, l, c, v, last = [None] * n, [None] * n, [None] * n, [None] * n, [0.0] * n, None
    for i in range(n):
        r = rows.get(i)
        if r:
            o[i], h[i], l[i], c[i], v[i], last = r[1], r[2], r[3], r[4], r[6], r[4]
        elif last is not None:
            o[i] = h[i] = l[i] = c[i] = last
    return o, h, l, c, v


btc = raw("KRW-BTC")
last_btc, bad, run = None, [True] * B, 10 ** 9
halt_until = -1
for i in range(B):
    if i in btc:
        last_btc = i
    if last_btc is None or i - last_btc >= 30:
        halt_until = i + 59
    bad[i] = i < 1500 or i <= halt_until

ok_all = True
for k in (7, 3, 0, 14):                                    # ETC, DOGE, BTC, KAVA
    rows = raw(UNI[k])
    o, h, l, c, v = series(rows, B)
    out, last, prev_mult = [], -10 ** 9, None
    for e in range(A - 1, B):
        win = v[e - 1440:e]
        if c[e - 1440] is None:
            prev_mult = None
            continue
        med = statistics.median(win)
        mult = v[e] / med if med > 0 else None
        if e >= A and mult is not None and mult >= 3 and not (prev_mult is not None and prev_mult >= 3):
            hi, lo = max(h[e - 59:e + 1]), min(l[e - 59:e + 1])
            hi24, lo24 = max(h[e - 1440:e]), min(l[e - 1440:e])
            if (hi - lo) / lo <= 0.01 and lo24 <= c[e] <= hi24 and not bad[e] and e - last >= 15:
                out.append(e)
                last = e
        prev_mult = mult
    mine = [int(x) for x in sig["s1_%d" % k] if A <= x < B]
    same = out == mine
    ok_all &= same
    print("시나리오 1 %s: 단순 계산 %d건, study.py %d건, %s" % (UNI[k][4:], len(out), len(mine), "일치" if same else "불일치 %s" % sorted(set(out) ^ set(mine))[:10]))

closes = {}
for k in range(20):
    closes[k] = series(raw(UNI[k]), B)[3]
for k in (12, 10, 1):                                      # ADA, IQ, ETH
    out, last, prev = [], -10 ** 9, False
    for e in range(A - 1, B):
        cond = False
        if all(closes[j][e - 5] is not None for j in range(20)):
            rt = closes[k][e] / closes[k][e - 5] - 1
            rb = closes[0][e] / closes[0][e - 5] - 1
            peers = [closes[j][e] / closes[j][e - 5] - 1 for j in range(1, 20) if j != k]
            cond = sum(1 for x in peers if x > 0) >= 12 and statistics.median(peers) >= 0.003 and abs(rt) <= 0.002 and abs(rb) <= 0.003 and not bad[e]
        if e >= A and cond and not prev and e - last >= 15:
            out.append(e)
            last = e
        prev = cond
    mine = [int(x) for x in sig["s2_%d" % k] if A <= x < B]
    same = out == mine
    ok_all &= same
    print("시나리오 2 %s: 단순 계산 %d건, study.py %d건, %s" % (UNI[k][4:], len(out), len(mine), "일치" if same else "불일치 %s" % sorted(set(out) ^ set(mine))[:10]))
print("전체:", "일치" if ok_all else "불일치")

# 성적 검산: 원자료에서 진입가·청산가를 직접 찾아 5분 순수익(왕복 0.20%)의 날짜별 평균을 다시 낸다
N = int(dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc).timestamp() // 60) - T0
for name, k in (("s1", 7), ("s2", 12), ("s2", 10)):
    rows = raw(UNI[k])
    days, n = {}, 0
    for e in sig["%s_%d" % (name, k)].tolist():
        if not (1500 <= e < N - 80):
            continue
        ei = next((i for i in range(e + 1, e + 6) if i in rows), None)
        if ei is None or ei + 60 >= N:
            continue
        x = ei + 4
        while x not in rows:
            x -= 1
        g = rows[x][4] / rows[ei][1] - 1
        days.setdefault((T0 + e) // 1440, []).append((1 + g) * 0.999 / 1.001 - 1)
        n += 1
    dm = [sum(v) / len(v) for v in days.values()]
    print("성적 %s %s: 체결 %d건 / %d일, 5분 날짜별 평균 %+.4f%%" % (name, UNI[k][4:], n, len(dm), 100 * sum(dm) / len(dm)))
