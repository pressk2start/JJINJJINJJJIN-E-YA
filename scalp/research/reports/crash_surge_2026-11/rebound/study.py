# -*- coding: utf-8 -*-
"""급락 후 반등 검토안 (사용자 설계 2026-11-20) — 관찰만. 매도 규칙 없음, 신호 뒤 60분까지 5분봉으로 본다.
공개 시세만. 고정 20종목, 2023-01-01 ~ 2026-11-19. 받은 시세는 OUT 에 저장해 다시 돌릴 때 이어 쓴다."""
import datetime as dt
import importlib.util
import json
import math
import pickle
import statistics as st
import sys
import urllib.parse
from pathlib import Path

R = Path(__file__).resolve().parents[3]                   # scalp/research


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(R / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = _load("discover")
AM = D.AM
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
UNI = D.REPRO_UNIVERSE
BTC, ETH = "KRW-BTC", "KRW-ETH"
ALTS = tuple(m for m in UNI if m != BTC)
START, CUT, END = "2023-01-01", "2025-01-01", "2026-11-19"
BTC_TH, ALT_TH, VOL_X = -0.01821, -0.03, 3.0
COOL = 4                                      # 같은 종목은 신호 뒤 4봉(60분) 안에 다시 세지 않는다
COST = {BTC: 0.0007, ETH: 0.0007}
UTC = dt.timezone.utc
log = lambda s: print(s, flush=True)


# ---------------------------------------------------------------- 15분봉
def panel15():
    p = OUT / "panel15.pkl"
    if p.exists():
        return pickle.loads(p.read_bytes())
    panel = D.load_panel(D.day_q("2022-12-20"), D.day_q(END), UNI, log=log)
    data = {m: {"ts": list(b.ts), "o": list(b.o), "c": list(b.c), "v": list(b.v)} for m, b in panel.items()}
    p.write_bytes(pickle.dumps(data))
    return data


P = panel15()
IDX = {m: {q: i for i, q in enumerate(P[m]["ts"])} for m in UNI}
log("15분봉: " + ", ".join("%s %d" % (m[4:], len(P[m]["ts"])) for m in UNI))


def ret15(m, q, k):
    i, j = IDX[m].get(q), IDX[m].get(q - k)
    if i is None or j is None or P[m]["c"][j] <= 0:
        return None
    return P[m]["c"][i] / P[m]["c"][j] - 1


q0, q1 = D.day_q(START), D.day_q(END)


def in_range(q):
    return q0 <= q + 1 < q1                   # 신호 시각(= 다음 봉 시가) 이 기간 안


# 1번: BTC 1시간 −1.821% 를 처음 넘은 봉
btc_sig, last = [], -99
for q in P[BTC]["ts"]:
    r, prev = ret15(BTC, q, 4), ret15(BTC, q - 1, 4)
    if r is None or not in_range(q):
        continue
    if r <= BTC_TH and (prev is None or prev > BTC_TH) and q - last >= COOL:
        btc_sig.append({"m": BTC, "q": q, "r1h": r, "btc24": ret15(BTC, q, 96)})
        last = q
log("1번 BTC 신호 %d" % len(btc_sig))

# 알트 15분 −3% (거래대금 조건 없이 잡고 배수를 적는다)
alt_sig = []
for m in ALTS:
    b = P[m]
    bars = D.Bars({q: (b["o"][i], 0.0, 0.0, b["c"][i], b["v"][i]) for i, q in enumerate(b["ts"])})
    last = -99
    for k, events, mult in D.detect(bars, 0.03, 0.0):
        q = bars.ts[k]
        if "CRASH" not in events or not in_range(q) or q - last < COOL:
            continue
        last = q
        alt_sig.append({"m": m, "q": q, "r15": events["CRASH"], "mult": float(mult), "btc1h": ret15(BTC, q, 4), "btc15": ret15(BTC, q, 1),
                        "btc24": ret15(BTC, q, 96)})
log("알트 신호 %d (거래대금 3배 이상 %d)" % (len(alt_sig), sum(1 for s in alt_sig if s["mult"] >= VOL_X)))

# ---------------------------------------------------------------- 5분봉 (필요한 구간만, 1000분 덩어리로 받아 저장)
BLOCK = 1000
CACHE = {}


def cache_file(m):
    return OUT / ("m5_%s.jsonl" % m)


def load_cache(m):
    if m in CACHE:
        return CACHE[m]
    c = {}
    f = cache_file(m)
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:                                   # 중간에 끊겨 잘린 줄 — 그 덩어리는 다시 받는다
                continue
            c[rec["b"]] = {int(k): v for k, v in rec["bars"].items()}
    CACHE[m] = c
    return c


def block(m, b):
    c = load_cache(m)
    if b in c:
        return c[b]
    to = dt.datetime.fromtimestamp((b + 1) * BLOCK * 60, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = AM.http_get("%s/v1/candles/minutes/5?market=%s&count=200&to=%s" % (AM.UPBIT, m, urllib.parse.quote(to)))
    bars = {}
    for r in rows:
        t = int(AM.parse_utc(r["candle_date_time_utc"]).timestamp() // 60)
        if b * BLOCK <= t < (b + 1) * BLOCK:
            bars[t] = [float(r["opening_price"]), float(r["high_price"]), float(r["low_price"]), float(r["trade_price"])]
    c[b] = bars
    with open(cache_file(m), "a") as f:
        f.write(json.dumps({"b": b, "bars": bars}) + "\n")
    return bars


def series(m, t0, t1):
    """[t0, t1) 의 5분봉 → {분: [o,h,l,c]}. 체결이 없어 빠진 봉은 직전 종가로 채운다(그 앞 30분 안에 봉이 있어야 한다)."""
    raw = {}
    for b in range((t0 - 30) // BLOCK, (t1 - 1) // BLOCK + 1):
        raw.update(block(m, b))
    prev = None
    for t in range(t0 - 30, t0, 5):
        if t in raw:
            prev = raw[t][3]
    out = {}
    for t in range(t0, t1, 5):
        if t in raw:
            out[t] = raw[t]
            prev = raw[t][3]
        elif prev is not None:
            out[t] = [prev, prev, prev, prev]
        else:
            return None
    return out, prev


def observe(m, t, s=None):
    """신호 시각 t(분)부터 60분 → 관찰 값. s 는 미리 받은 5분봉."""
    if s is None:
        got = series(m, t, t + 60)
        if got is None:
            return None
        s = got[0]
    bars = [s.get(t + 5 * j) for j in range(12)]
    if any(x is None for x in bars):
        return None
    e = bars[0][0]
    if e <= 0:
        return None
    hi = max(range(12), key=lambda j: bars[j][1])
    lo = min(range(12), key=lambda j: bars[j][2])
    peak = bars[hi][1]
    after = [bars[j][2] for j in range(hi + 1, 12)]
    c = COST.get(m, 0.001)
    r60 = bars[11][3] / e - 1
    return {"r5": bars[0][3] / e - 1, "r15": bars[2][3] / e - 1, "r30": bars[5][3] / e - 1, "r60": r60,
            "net60": (1 + r60) * (1 - c) / (1 + c) - 1, "mfe": peak / e - 1, "t_mfe": 5 * hi + 5, "mae": bars[lo][2] / e - 1, "t_mae": 5 * lo + 5,
            "pullback": (min(after) / peak - 1) if after else 0.0}


def confirmed(m, t):
    """5번: 신호 뒤 60분 안에 5분봉 하나가 직전 봉보다 높게 마감하면 다음 봉 시가부터 60분 관찰 → (기다린 분, 관찰, 놓친 폭) 또는 None."""
    got = series(m, t - 5, t + 125)
    if got is None:
        return None
    s = got[0]
    for j in range(12):
        a, b = s.get(t + 5 * j - 5), s.get(t + 5 * j)
        if a is None or b is None:
            return None
        if b[3] > a[3]:
            t2 = t + 5 * (j + 1)
            o = observe(m, t2, s)
            if o is None:
                return None
            return {"wait": t2 - t, "obs": o, "late": s[t2][0] / s[t][0] - 1}
    return {"wait": None}


def tmin(q):
    return (q + 1) * 15


# ---------------------------------------------------------------- 관찰 모으기
def run_obs(sig, also_pairs=True, confirm=True):
    done = 0
    for s in sig:
        t = tmin(s["q"])
        s["day"] = dt.datetime.fromtimestamp(t * 60, tz=UTC).strftime("%Y-%m-%d")
        s["obs"] = observe(s["m"], t)
        if also_pairs:
            s["btc"] = observe(BTC, t) if s["m"] != BTC else s["obs"]
            s["eth"] = observe(ETH, t) if s["m"] != ETH else s["obs"]
        if confirm:
            s["conf"] = confirmed(s["m"], t)
        done += 1
        if done % 500 == 0:
            log("  관찰 %d / %d" % (done, len(sig)))


log("1번 관찰")
run_obs(btc_sig, also_pairs=True)
log("알트 관찰")
run_obs(alt_sig, also_pairs=True)

# 3번: BTC 신호 뒤 1~4봉 안의 알트 신호
by_q = {}
for s in alt_sig:
    by_q.setdefault(s["q"], []).append(s)
lead = []
for b in btc_sig:
    for d in range(1, COOL + 1):
        for s in by_q.get(b["q"] + d, []):
            lead.append(dict(s, lag=15 * d, btc_q=b["q"]))

(OUT / "signals.pkl").write_bytes(pickle.dumps({"btc": btc_sig, "alt": alt_sig, "lead": lead}))
log("끝: BTC %d, 알트 %d, 3번 %d" % (len(btc_sig), len(alt_sig), len(lead)))
