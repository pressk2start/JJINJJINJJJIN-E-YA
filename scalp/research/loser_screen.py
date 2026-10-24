#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""loser_screen — '어제 많이 떨어진 코인' 중 되오르는 것을 미리 가르는 특징이 있는가 (사전 고정 2026-10-24).

공개 업비트 일봉만 사용. 주문·키 없음. 표준 라이브러리만.
사건  : 매일 d, 유동성 상위 50(직전 30일 거래대금 중앙값, 상장 60일+) 중 어제(d-1) 수익률 하위 10개
        (-25% 미만 급락은 상폐 공지 편향으로 제외). 오늘 시가 매수 → 내일 시가 매도, 비용 편도 0.15%.
특징  : 전부 d 시가 이전에 확정된 값만
  ret1      어제 하락률                      ret7     그제까지 1주 수익률 (하락 전 추세)
  ret30     그제까지 1달 수익률               volx     어제 거래대금 / 직전 30일 중앙값 (패닉 거래량)
  sma20     어제 종가 / 20일 평균 - 1          closepos (종가-저가)/(고가-저가) 어제 (0=저가 마감, 1=회복 마감)
  rel       어제 수익률 - 유니버스 평균 수익률  (개별 악재 vs 시장 전체 하락)
  btc1      BTC 어제 수익률                   gap      어제 시가 / 그제 종가 - 1 (09:00 갭하락 여부)
절차 (결과 보기 전에 고정):
  1) TRAIN(~2026-04-24) 사건으로 특징별 3분위(경계는 TRAIN 에서만) → 분위별 다음날 순수익 평균·n
  2) TRAIN 에서 n≥200 인 분위 중 평균이 가장 높은 '단일 조건' 1개, 그리고 상위 2개 특징의 최고 분위 AND 조합 1개 선택
  3) 선택된 조건을 그대로 HOLDOUT(2026-04-25~) 에 적용. 판정은 HOLDOUT 만:
     조건부 평균 순수익 > 0 · 조건부 평균 > 무조건(하위10 전체) 평균 · 부트스트랩 90% CI 하한 > 0 · n ≥ 100
  9개 특징 × 3분위 = 27칸 중 최고를 고르는 것이라 TRAIN 숫자는 우연으로 좋게 나온다. HOLDOUT 이 유일한 판정.
한계: 일봉만이라 호가·체결흐름 특징은 없음. 상폐 종목 누락(생존편향)은 하락 종목 매수에 유리하게 작용.
실행: py loser_screen.py   (캐시 lsc_cache/, 첫 실행 5~10분)
"""
import argparse
import datetime as dt
import json
import random
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

UPBIT = "https://api.upbit.com/v1"
FEE, SLIP = 0.0005, 0.0010
TOP_N, LIQ_DAYS, MIN_HIST = 50, 30, 60
BOTTOM_K, MIN_RET = 10, -0.25
HOLDOUT_START = "2026-04-25"
FEATURES = ("ret1", "ret7", "ret30", "volx", "sma20", "closepos", "rel", "btc1", "gap")
MIN_CELL_TRAIN, MIN_CELL_HOLD = 200, 100


def _get(url, retries=6):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=20) as r:
                time.sleep(0.12)
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < retries - 1:
                time.sleep(1 + i)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < retries - 1:
                time.sleep(1 + i)
                continue
            raise


def fetch_daily(market, days=760):
    """{date: [open, high, low, close, krw_value]}"""
    out, to = {}, None
    while len(out) < days:
        rows = _get(f"{UPBIT}/candles/days?market={market}&count=200" + (f"&to={to}" if to else ""))
        if not rows:
            break
        for r in rows:
            out[r["candle_date_time_utc"][:10]] = [float(r["opening_price"]), float(r["high_price"]), float(r["low_price"]),
                                                   float(r["trade_price"]), float(r["candle_acc_trade_price"])]
        if len(rows) < 200:
            break
        to = min(r["candle_date_time_utc"] for r in rows)[:19] + "Z"
    return out


def _shift(d, k):
    return (dt.date.fromisoformat(d) + dt.timedelta(days=k)).isoformat()


def net(entry, exit_):
    return exit_ * (1 - SLIP) * (1 - FEE) / (entry * (1 + SLIP) * (1 + FEE)) - 1


def universe(data, d):
    scored = []
    for m, s in data.items():
        if m == "KRW-USDT":
            continue
        if sum(1 for k in range(1, MIN_HIST + 1) if _shift(d, -k) in s) < MIN_HIST - 5:
            continue
        vals = [s[_shift(d, -k)][4] for k in range(1, LIQ_DAYS + 1) if _shift(d, -k) in s]
        if len(vals) >= LIQ_DAYS - 3:
            scored.append((statistics.median(vals), m))
    scored.sort(reverse=True)
    return [m for _, m in scored[:TOP_N]]


def features_for(s, d, ew_ret1, btc_ret1):
    y, yy = _shift(d, -1), _shift(d, -2)
    if y not in s or yy not in s:
        return None
    o, h, l, c, v = s[y]
    c2 = s[yy][3]
    if c2 <= 0 or c <= 0:
        return None
    try:
        c9, c31 = s[_shift(d, -9)][3], s[_shift(d, -31)][3]
        vols = [s[_shift(d, -k)][4] for k in range(2, 32)]
        closes20 = [s[_shift(d, -k)][3] for k in range(1, 21)]
    except KeyError:
        return None
    medv = statistics.median(vols)
    ret1 = c / c2 - 1
    return {"ret1": ret1, "ret7": c2 / c9 - 1, "ret30": c2 / c31 - 1,
            "volx": (v / medv) if medv > 0 else None, "sma20": c / (sum(closes20) / 20) - 1,
            "closepos": ((c - l) / (h - l)) if h > l else 0.5, "rel": ret1 - ew_ret1, "btc1": btc_ret1,
            "gap": o / c2 - 1}


def build_events(data, start, end):
    events, d = [], start
    btc = data.get("KRW-BTC", {})
    while _shift(d, 1) <= end:
        uni = universe(data, d)
        rets = {}
        for m in uni:
            s = data[m]
            y, yy = _shift(d, -1), _shift(d, -2)
            if y in s and yy in s and s[yy][3] > 0:
                rets[m] = s[y][3] / s[yy][3] - 1
        y, yy = _shift(d, -1), _shift(d, -2)
        if rets and y in btc and yy in btc:
            ew1 = statistics.mean(rets.values())
            b1 = btc[y][3] / btc[yy][3] - 1
            losers = sorted((r, m) for m, r in rets.items() if MIN_RET <= r < 0)[:BOTTOM_K]
            for r, m in losers:
                s = data[m]
                nxt = _shift(d, 1)
                if d not in s or nxt not in s:
                    continue
                f = features_for(s, d, ew1, b1)
                if f is None or any(v is None for v in f.values()):
                    continue
                f.update(date=d, market=m, net=net(s[d][0], s[nxt][0]), ew_next=None)
                events.append(f)
        d = _shift(d, 1)
    return events


def terciles(vals):
    v = sorted(vals)
    return v[len(v) // 3], v[2 * len(v) // 3]


def bucket(x, edges):
    return 0 if x < edges[0] else (1 if x < edges[1] else 2)


def ci_low(v, n=2000, seed=3):
    if len(v) < 2:
        return None
    rng = random.Random(seed)
    m = sorted(statistics.mean(rng.choices(v, k=len(v))) for _ in range(n))
    return m[int(0.05 * n)]


def mean(v):
    return statistics.mean(v) if v else None


def analyze(events):
    tr = [e for e in events if e["date"] < HOLDOUT_START]
    ho = [e for e in events if e["date"] >= HOLDOUT_START]
    out = {"n_train": len(tr), "n_holdout": len(ho),
           "uncond_train": mean([e["net"] for e in tr]), "uncond_holdout": mean([e["net"] for e in ho]), "features": {}}
    cells = []
    for f in FEATURES:
        edges = terciles([e[f] for e in tr])
        row = {"edges": edges, "train": [], "holdout": []}
        for b in range(3):
            t = [e["net"] for e in tr if bucket(e[f], edges) == b]
            h = [e["net"] for e in ho if bucket(e[f], edges) == b]
            row["train"].append({"n": len(t), "mean": mean(t)})
            row["holdout"].append({"n": len(h), "mean": mean(h)})
            if len(t) >= MIN_CELL_TRAIN:
                cells.append((mean(t), f, b, edges))
        out["features"][f] = row
    cells.sort(reverse=True)
    out["selected"] = []
    if cells:
        m1, f1, b1, e1 = cells[0]
        sel = [(f1, b1, e1)]
        # second feature = best cell among features other than f1
        rest = [c for c in cells if c[1] != f1]
        if rest:
            m2, f2, b2, e2 = rest[0]
            sel.append((f2, b2, e2))
        rules = [("single", sel[:1])] + ([("and2", sel)] if len(sel) == 2 else [])
        for name, conds in rules:
            def hit(e):
                return all(bucket(e[f], ed) == b for f, b, ed in conds)
            t = [e["net"] for e in tr if hit(e)]
            h = [e["net"] for e in ho if hit(e)]
            lo = ci_low(h)
            verdict = ("INSUFFICIENT" if len(h) < MIN_CELL_HOLD else
                       "PASS" if (mean(h) or -1) > 0 and (mean(h) or -1) > (out["uncond_holdout"] or 0) and (lo or -1) > 0 else "FAIL")
            out["selected"].append({"rule": name, "conds": [(f, b, [round(x, 4) for x in ed]) for f, b, ed in conds],
                                    "train_n": len(t), "train_mean": mean(t), "holdout_n": len(h), "holdout_mean": mean(h),
                                    "holdout_ci_low": lo, "holdout_win": (sum(x > 0 for x in h) / len(h)) if h else None,
                                    "verdict": verdict})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default="lsc_cache")
    ap.add_argument("--out", default="lsc_result")
    a = ap.parse_args(argv)
    markets = sorted(m["market"] for m in _get(f"{UPBIT}/market/all") if m["market"].startswith("KRW-"))
    print(f"LOSER SCREEN (pre-registered 2026-10-24) | {len(markets)} markets | holdout from {HOLDOUT_START}")
    data = {}
    Path(a.cache).mkdir(parents=True, exist_ok=True)
    for k, m in enumerate(markets, 1):
        p = Path(a.cache) / f"{m}.json"
        try:
            if p.exists():
                data[m] = json.loads(p.read_text())
            else:
                data[m] = fetch_daily(m)
                p.write_text(json.dumps(data[m]))
        except Exception as e:
            print(f"[skip] {m}: {e}")
        if k % 50 == 0:
            print(f"[upbit] {k}/{len(markets)}")
    days = sorted({d for s in data.values() for d in s})
    events = build_events(data, _shift(days[0], MIN_HIST + 5), days[-1])
    res = analyze(events)
    pct = lambda x: f"{x*100:+.3f}" if isinstance(x, (int, float)) else "   n/a"
    print(f"\nevents: train {res['n_train']}  holdout {res['n_holdout']}  (bottom-{BOTTOM_K} losers/day, 1-day hold, costs in)")
    print(f"unconditional loser next-day net: train {pct(res['uncond_train'])}%  holdout {pct(res['uncond_holdout'])}%")
    print("\nfeature   tercile  TRAIN n  TRAIN net%   HOLD n  HOLD net%   (edges from TRAIN)")
    for f, row in res["features"].items():
        for b in range(3):
            t, h = row["train"][b], row["holdout"][b]
            print(f"{f:9} {['low','mid','high'][b]:7} {t['n']:8} {pct(t['mean']):>10}  {h['n']:7} {pct(h['mean']):>10}")
    print("\nSELECTED ON TRAIN → APPLIED TO HOLDOUT (the only verdict)")
    for s in res["selected"]:
        conds = " AND ".join(f"{f}={['low','mid','high'][b]}" for f, b, _ in s["conds"])
        print(f"  {s['rule']:6} {conds:40} train n={s['train_n']} {pct(s['train_mean'])}% | holdout n={s['holdout_n']} "
              f"{pct(s['holdout_mean'])}% ciLow {pct(s['holdout_ci_low'])}% win {((s['holdout_win'] or 0)*100):.0f}%  → {s['verdict']}")
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "report.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (Path(a.out) / "events.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    print(f"Results: {Path(a.out) / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
