#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vol_breakout_test — 변동성·거래량 동반 돌파 가설 (사전 고정 2026-10-20). 공개 캔들만, 주문·키 없음.

사전 고정 스펙 (데이터 보기 전 결정 · 숫자 탐색 금지):
  대상  : 업비트 KRW 종목 중 직전 7일 거래대금 중앙값 상위 50 (UTC 날짜마다 그 전날까지 자료로만 재선정)
  봉    : 15분봉, 기준 구간 = 직전 96봉(24h)
  신호  : ① 이번 봉 거래량 ≥ 기준 중앙값 ×3  ② (고-저)/종가 ≥ 기준 중앙값 ×2  ③ 종가 > 기준 구간 최고가
  중복  : 같은 종목 신호 후 24h 무시
  진입  : 다음 봉 시가 (시장가 가정)
  보유  : 15분 / 1시간 / 4시간 / 24시간 고정, 손절 없음. 청산 = 목표 시각 이후 첫 봉 시가
  비용  : 수수료 0.05% + 불리한 가격 0.10% (편도). 스트레스 0.20%
  구간  : ~9/30 TRAIN(탐색) · 10/1~10/19 VALIDATION(1회) · 10/20~ FINAL(유일한 판정 구간)
  합격  : FINAL 에서 모두 충족 — 신호 ≥60 · 비용후 평균 >0 · 부트스트랩 90% CI 하한 >0 ·
          최대 1건 제외 평균 >0 · 동시간 BTC 대비 초과수익 >0 · 수익 난 종목 ≥5 · 수익 난 날짜 ≥5
  출력  : 월 신호 수 × 건당 원화(주문 6,000원) = 월 기대 원화
제약: 캔들 시가 가정이라 실제 체결가·스프레드는 비용 가정으로만 반영. LIVE 규칙 아님.
결측(다음 봉 없음, 목표 시각 이후 30분 내 봉 없음, 데이터 끝)은 0 이 아니라 unknown 으로 따로 센다.

실행 (PC, 표준 라이브러리만):
  py vol_breakout_test.py --out vb_result
  10/20 이후 데이터가 쌓이면 같은 명령을 다시 실행 (캐시에 이어 받음) → FINAL 행만 판정에 사용
"""
import argparse
import csv
import datetime as dt
import json
import math
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.upbit.com/v1"
M15 = 15 * 60 * 1000
H24 = 24 * 3600 * 1000
HORIZONS_MIN = (15, 60, 240, 1440)
BASE_BARS = 96
MIN_BASE_BARS = 90          # 24h 중 최소 90봉 존재해야 기준 구간 인정
VOL_MULT, RANGE_MULT = 3.0, 2.0
TOP_N, UNIVERSE_DAYS = 50, 7
FEE = 0.0005
SLIP_BASE, SLIP_STRESS = 0.0010, 0.0020
NOTIONAL_KRW = 6000
UTC = dt.timezone.utc


# ───────────────────────── network (PC only) ─────────────────────────
def _get(path, params, retries=6):
    url = API + path + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                time.sleep(0.12)
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < retries - 1:
                time.sleep(1.0 + i)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < retries - 1:
                time.sleep(1.0 + i)
                continue
            raise


def _ms(s):
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


def _iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_krw_markets():
    return sorted(m["market"] for m in _get("/market/all", {"isDetails": "false"}) if m["market"].startswith("KRW-"))


def fetch_daily_value(market, count=200):
    """{utc_date: acc_trade_price}"""
    rows = _get("/candles/days", {"market": market, "count": count})
    return {r["candle_date_time_utc"][:10]: float(r["candle_acc_trade_price"]) for r in rows}


def fetch_m15(market, start_ms, end_ms):
    """[ts_ms, o, h, l, c, v] ascending, ts in [start_ms, end_ms)."""
    out, to = {}, end_ms
    while to > start_ms:
        rows = _get("/candles/minutes/15", {"market": market, "to": _iso(to), "count": 200})
        if not rows:
            break
        for r in rows:
            t = _ms(r["candle_date_time_utc"])
            if start_ms <= t < end_ms:
                out[t] = [t, float(r["opening_price"]), float(r["high_price"]), float(r["low_price"]),
                          float(r["trade_price"]), float(r["candle_acc_trade_volume"])]
        oldest = min(_ms(r["candle_date_time_utc"]) for r in rows)
        if oldest >= to:
            break
        to = oldest
    return [out[k] for k in sorted(out)]


def load_m15_cached(market, start_ms, end_ms, cache_dir):
    p = Path(cache_dir) / f"{market}_m15.json"
    bars = {}
    if p.exists():
        try:
            bars = {b[0]: b for b in json.loads(p.read_text())}
        except ValueError:
            bars = {}
    if bars:
        lo, hi = min(bars), max(bars)
        ranges = []
        if start_ms < lo:
            ranges.append((start_ms, lo))
        if hi + M15 < end_ms:
            ranges.append((hi + M15, end_ms))
    else:
        ranges = [(start_ms, end_ms)]
    for a, b in ranges:
        for bar in fetch_m15(market, a, b):
            bars[bar[0]] = bar
    out = [bars[k] for k in sorted(bars)]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out))
    return [b for b in out if start_ms <= b[0] < end_ms]


# ───────────────────────── pure logic (tested) ─────────────────────────
def build_universe(daily, dates, top_n=TOP_N, lookback=UNIVERSE_DAYS):
    """daily: {market: {date: value}}. 반환 {date: set(markets)} — date 당일 자료는 쓰지 않음."""
    uni = {}
    for d in dates:
        dd = dt.date.fromisoformat(d)
        prior = [(dd - dt.timedelta(days=k)).isoformat() for k in range(1, lookback + 1)]
        scored = []
        for m, vals in daily.items():
            v = [vals[p] for p in prior if p in vals]
            if len(v) >= lookback - 2:
                scored.append((statistics.median(v), m))
        scored.sort(reverse=True)
        uni[d] = {m for _, m in scored[:top_n]}
    return uni


def detect_signals(market, bars, universe):
    """bars: [ts,o,h,l,c,v] ascending. 반환 [(signal_ts, info)] — 봉이 닫힌 뒤 판단."""
    sigs, last, j = [], None, 0
    for i, (t, o, h, l, c, v) in enumerate(bars):
        while j < i and bars[j][0] < t - BASE_BARS * M15:
            j += 1
        base = bars[j:i]
        if len(base) < MIN_BASE_BARS or c <= 0:
            continue
        d = dt.datetime.fromtimestamp(t / 1000, UTC).date().isoformat()
        if market not in universe.get(d, ()):
            continue
        if last is not None and t - last < H24:
            continue
        med_v = statistics.median(b[5] for b in base)
        med_r = statistics.median((b[2] - b[3]) / b[4] for b in base if b[4] > 0)
        hi = max(b[2] for b in base)
        if med_v <= 0 or med_r <= 0:
            continue
        if v >= VOL_MULT * med_v and (h - l) / c >= RANGE_MULT * med_r and c > hi:
            sigs.append((t, {"vol_x": v / med_v, "range_x": ((h - l) / c) / med_r}))
            last = t
    return sigs


def _price_at(idx, target, tol_bars=2):
    for k in range(tol_bars + 1):
        b = idx.get(target + k * M15)
        if b:
            return b[1]
    return None


def net_return(entry, exit_, slip, fee=FEE):
    return (exit_ * (1 - slip) * (1 - fee)) / (entry * (1 + slip) * (1 + fee)) - 1


def evaluate(market, sig_ts, idx, btc_idx, data_end_ms):
    rows = []
    entry_t = sig_ts + M15
    entry = idx.get(entry_t, [None, None])[1]
    for hmin in HORIZONS_MIN:
        target = entry_t + hmin * 60 * 1000
        r = {"market": market, "signal_ts": sig_ts, "hold_min": hmin}
        if entry is None:
            r["unknown"] = "entry_bar_missing"
        elif target + 2 * M15 >= data_end_ms:
            r["unknown"] = "beyond_data"
        else:
            ex = _price_at(idx, target)
            if ex is None:
                r["unknown"] = "exit_bar_missing"
            else:
                r["gross_pct"] = (ex / entry - 1) * 100
                r["net_pct"] = net_return(entry, ex, SLIP_BASE) * 100
                r["stress_pct"] = net_return(entry, ex, SLIP_STRESS) * 100
                be, bx = btc_idx.get(entry_t, [None, None])[1], _price_at(btc_idx, target)
                r["btc_pct"] = (bx / be - 1) * 100 if be and bx else None
        rows.append(r)
    return rows


def bootstrap_ci(vals, n=2000, seed=7, lo=0.05, hi=0.95):
    if len(vals) < 2:
        return None, None
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(vals, k=len(vals))) for _ in range(n))
    return means[int(lo * n)], means[int(hi * n) - 1]


def summarize(rows, days_in_phase):
    known = [r for r in rows if "net_pct" in r]
    out = {"signals": len(rows), "known": len(known), "unknown": len(rows) - len(known)}
    if not known:
        return out
    v = sorted(r["net_pct"] for r in known)
    ex = [r["net_pct"] - r["btc_pct"] for r in known if r.get("btc_pct") is not None]
    ci = bootstrap_ci(v)
    mean = statistics.mean(v)
    rate = len(known) / max(days_in_phase, 1) * 30
    out.update({
        "mean_net_pct": mean, "median_net_pct": statistics.median(v),
        "mean_stress_pct": statistics.mean(r["stress_pct"] for r in known),
        "win_rate": sum(x > 0 for x in v) / len(v),
        "ci90_low": ci[0], "ci90_high": ci[1],
        "mean_ex_top1_pct": statistics.mean(v[:-1]) if len(v) > 1 else None,
        "mean_excess_vs_btc_pct": statistics.mean(ex) if ex else None,
        "pos_markets": len({r["market"] for r in known if r["net_pct"] > 0}),
        "pos_days": len({dt.datetime.fromtimestamp(r["signal_ts"] / 1000, UTC).date() for r in known if r["net_pct"] > 0}),
        "signals_per_month": rate,
        "monthly_krw": rate * mean / 100 * NOTIONAL_KRW,
    })
    return out


def verdict(s):
    need = ("mean_net_pct", "ci90_low", "mean_ex_top1_pct", "mean_excess_vs_btc_pct")
    if s.get("known", 0) < 60 or any(s.get(k) is None for k in need):
        return "INSUFFICIENT"
    ok = (s["mean_net_pct"] > 0 and s["ci90_low"] > 0 and s["mean_ex_top1_pct"] > 0
          and s["mean_excess_vs_btc_pct"] > 0 and s["pos_markets"] >= 5 and s["pos_days"] >= 5)
    return "PASS" if ok else "FAIL"


def phase_of(ts, train_end, val_end):
    d = dt.datetime.fromtimestamp(ts / 1000, UTC).date()
    return "TRAIN" if d <= train_end else ("VALIDATION" if d <= val_end else "FINAL")


# ───────────────────────── main ─────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2026-08-01")
    ap.add_argument("--train-end", default="2026-09-30")
    ap.add_argument("--val-end", default="2026-10-19")
    ap.add_argument("--cache", default="vb_cache")
    ap.add_argument("--out", default="vb_result")
    a = ap.parse_args(argv)
    start = dt.date.fromisoformat(a.start)
    train_end, val_end = dt.date.fromisoformat(a.train_end), dt.date.fromisoformat(a.val_end)
    now_ms = int(time.time() * 1000) // M15 * M15
    today = dt.datetime.fromtimestamp(now_ms / 1000, UTC).date()
    dates = [(start + dt.timedelta(days=k)).isoformat() for k in range((today - start).days + 1)]

    print(f"VOL-BREAKOUT (pre-registered 2026-10-20) | {a.start} .. {today} | public candles only")
    markets = fetch_krw_markets()
    daily = {}
    for k, m in enumerate(markets, 1):
        try:
            daily[m] = fetch_daily_value(m)
        except Exception as e:
            print(f"[daily] skip {m}: {e}")
        if k % 50 == 0:
            print(f"[daily] {k}/{len(markets)}")
    universe = build_universe(daily, dates)
    members = sorted(set().union(*universe.values()))
    print(f"universe: {len(members)} markets entered top-{TOP_N} at least once")

    fetch_start = int(dt.datetime.combine(start, dt.time(), UTC).timestamp() * 1000) - 2 * H24
    btc = load_m15_cached("KRW-BTC", fetch_start, now_ms, a.cache)
    btc_idx = {b[0]: b for b in btc}
    rows, fails = [], {}
    for k, m in enumerate(members, 1):
        try:
            bars = btc if m == "KRW-BTC" else load_m15_cached(m, fetch_start, now_ms, a.cache)
        except Exception as e:
            fails[m] = str(e)
            continue
        idx = {b[0]: b for b in bars}
        for ts, info in detect_signals(m, bars, universe):
            if ts < fetch_start + 2 * H24:
                continue
            for r in evaluate(m, ts, idx, btc_idx, now_ms):
                r.update(info)
                r["phase"] = phase_of(ts, train_end, val_end)
                rows.append(r)
        if k % 10 == 0:
            print(f"[m15] {k}/{len(members)} rows={len(rows)}")

    spans = {"TRAIN": (start, train_end), "VALIDATION": (train_end + dt.timedelta(days=1), val_end),
             "FINAL": (val_end + dt.timedelta(days=1), today)}
    result = {"spec": "see module docstring", "params": vars(a), "fetch_failures": fails, "groups": []}
    print("\nphase       hold   n  unk  mean%  median%  stress%  win%  ci90_low  exTop1%  exBTC%  posMkt posDay  sig/mo  krw/mo  verdict")
    for ph in ("TRAIN", "VALIDATION", "FINAL"):
        days = max((spans[ph][1] - spans[ph][0]).days + 1, 0)
        for h in HORIZONS_MIN:
            s = summarize([r for r in rows if r["phase"] == ph and r["hold_min"] == h], days)
            s.update(phase=ph, hold_min=h, verdict=verdict(s) if ph == "FINAL" else "(explore)")
            result["groups"].append(s)
            f = lambda x, w=7, p=3: (f"{x:{w}.{p}f}" if isinstance(x, (int, float)) else " " * (w - 3) + "n/a")
            print(f"{ph:10} {h:5} {s['known']:4} {s['unknown']:4} {f(s.get('mean_net_pct'))} {f(s.get('median_net_pct'))} "
                  f"{f(s.get('mean_stress_pct'))} {f((s.get('win_rate') or 0)*100,5,1)} {f(s.get('ci90_low'),8)} "
                  f"{f(s.get('mean_ex_top1_pct'))} {f(s.get('mean_excess_vs_btc_pct'))} {s.get('pos_markets', 0):6} "
                  f"{s.get('pos_days', 0):6} {f(s.get('signals_per_month'),6,1)} {f(s.get('monthly_krw'),7,0)}  {s['verdict']}")
    print("\nONLY FINAL rows are a verdict. TRAIN/VALIDATION are exploration. Not LIVE profit.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    keys = sorted({k for r in rows for k in r})
    with (out / "events.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"Results: {out / 'report.json'} {out / 'events.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
