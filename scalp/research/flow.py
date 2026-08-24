#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
7일 틱 기반 주문흐름(order flow) 스크리닝 — 특징 행렬 구축.

연구축 분리
-----------
이 파일은 **체결(tick) 로만 만들 수 있는 것만** 다룬다.
  · trade imbalance (1/3/5/10초)
  · signed notional
  · arrival rate / acceleration
  · large-trade fraction
  · aggressive buy ratio
OBI·spread·depletion 은 **여기에 섞지 않는다.** 그것들은 오더북이 필요하고,
오더북은 과거를 못 받아서 ws_recorder 가 지금부터 쌓는 중이다. 두 축을
섞으면 표본 기간이 다른 특징을 한 표에 놓게 되고, 어느 쪽이 성과를 낸
것인지 분리할 수 없게 된다.

부호 규약 (실측으로 확정, 2026-08-24)
------------------------------------
Upbit `ask_bid` 필드는 **공격자(taker) 방향**이다: BID = 공격적 매수.
가정하지 않고 두 가지로 검증했다.
  1) 다음 '가격이 바뀐' 체결까지의 변화: BID 뒤 하락, ASK 뒤 상승.
     → 이건 규약 근거가 **아니다**. 한 틱짜리 호가 튐일 뿐이다
       (XRP 중앙값 ±7.067bp = 1418원의 1원 = 정확히 1틱).
  2) 60초 창의 signed notional(BID=+) 과 같은 창 수익률의 상관:
     XRP +0.254 · BTC +0.343 · ETH +0.222 · TRUMP +0.219 · SOL +0.112
     한 틱 튐은 창 안에서 상쇄되므로 이 검정은 튐에 오염되지 않는다.
     5종목 전부 양(+) → BID=매수 확정.
  ※ 이 상관은 **동시점**이라 기계적 관계다. 예측력이 아니다.
     예측력은 forward 수익률로 따로 재야 한다 (screen.py).

look-ahead 차단
---------------
시각 t 의 특징은 **t 이하** 체결만 쓴다. forward 수익률은 t 초과만 쓴다.
경계는 `ts <= t` 로 닫고, 진입 가능 시점은 t 이후 첫 체결이다.
"""
import gzip, json, os, argparse, datetime
import numpy as np

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ticks")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flow")

# 되돌아보는 창(초) — 사용자가 지정한 범위. 임의로 늘리지 않는다.
LOOKBACK = (1, 3, 5, 10)
# forward 지평(초)
HORIZON = (30, 60, 120)


def load_ticks(market):
    """한 종목의 7일 체결을 시간순 배열로 만든다.

    반환: ts(ms,int64), price(float64), volume(float64), buy(bool)
    중복 sequential_id 는 제거한다 (페이지네이션 경계에서 겹칠 수 있다).
    """
    d = os.path.join(DIR, market)
    seen = set()
    ts, px, vol, buy = [], [], [], []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".gz"):
            continue
        with gzip.open(os.path.join(d, fn), "rt") as f:
            for ln in f:
                r = json.loads(ln)
                sid = r.get("sequential_id")
                if sid is not None:
                    if sid in seen:
                        continue
                    seen.add(sid)
                ts.append(r["timestamp"])
                px.append(r["trade_price"])
                vol.append(r["trade_volume"])
                buy.append(r["ask_bid"] == "BID")     # BID = 공격적 매수 (위 실측)
    ts = np.asarray(ts, dtype=np.int64)
    o = np.argsort(ts, kind="stable")
    return (ts[o], np.asarray(px)[o], np.asarray(vol)[o], np.asarray(buy)[o])


def _win_sums(ts, vals, grid_ms, win_ms):
    """각 격자점 t 에 대해 (t-win, t] 구간 합. 누적합 + searchsorted 로 O(n log n).

    경계: 오른쪽은 닫고(<= t), 왼쪽은 연다(> t-win). t 시점 체결은 **포함**된다.
    t 시점 체결까지가 '이미 관측된 것'이므로 look-ahead 가 아니다.
    """
    cs = np.concatenate([[0.0], np.cumsum(vals)])
    hi = np.searchsorted(ts, grid_ms, side="right")
    lo = np.searchsorted(ts, grid_ms - win_ms, side="right")
    return cs[hi] - cs[lo], (hi - lo)


def build(market, grid_sec=1, verbose=True):
    ts, px, vol, buy = load_ticks(market)
    if len(ts) < 10_000:
        raise SystemExit(f"{market}: 체결이 너무 적다 ({len(ts):,})")
    notional = px * vol
    signed_n = notional * np.where(buy, 1.0, -1.0)
    signed_v = vol * np.where(buy, 1.0, -1.0)

    g0 = (ts[0] // 1000 + 1) * 1000
    g1 = ts[-1] // 1000 * 1000
    grid = np.arange(g0, g1 + 1, grid_sec * 1000, dtype=np.int64)

    # 대형체결 기준: 이 종목 7일 체결대금의 p95. 절대금액을 박으면 종목마다
    # 의미가 달라지므로 종목 내부 분위수로 정의한다.
    big_cut = float(np.quantile(notional, 0.95))
    big_notional = np.where(notional >= big_cut, notional, 0.0)

    F = {}
    for w in LOOKBACK:
        wm = w * 1000
        sv, n = _win_sums(ts, signed_v, grid, wm)
        av, _ = _win_sums(ts, vol, grid, wm)
        sn, _ = _win_sums(ts, signed_n, grid, wm)
        an, _ = _win_sums(ts, notional, grid, wm)
        bn, _ = _win_sums(ts, big_notional, grid, wm)
        nb, _ = _win_sums(ts, buy.astype(float), grid, wm)

        with np.errstate(invalid="ignore", divide="ignore"):
            F[f"ti_{w}s"] = np.where(av > 0, sv / av, np.nan)          # 체결량 불균형
            F[f"sn_{w}s"] = np.where(an > 0, sn / an, np.nan)          # 대금 불균형
            F[f"abr_{w}s"] = np.where(n > 0, nb / n, np.nan)           # 공격적 매수 비율
            F[f"ltf_{w}s"] = np.where(an > 0, bn / an, np.nan)         # 대형체결 대금비중
        F[f"ar_{w}s"] = n / w                                          # 도착률 (건/초)
        # 절대 규모도 남긴다 — 비율만 보면 '거래가 거의 없는 창'이 극단값을 만든다
        F[f"an_{w}s"] = an

    # 가속: 최근 1초 도착률 / 최근 10초 평균 도착률
    with np.errstate(invalid="ignore", divide="ignore"):
        F["ar_accel"] = np.where(F["ar_10s"] > 0, F["ar_1s"] / F["ar_10s"], np.nan)

    # 격자점의 '현재가' = t 이하 마지막 체결가. t 이후를 보지 않는다.
    idx = np.searchsorted(ts, grid, side="right") - 1
    valid = idx >= 0
    p_now = np.where(valid, px[np.clip(idx, 0, None)], np.nan)
    age_ms = np.where(valid, grid - ts[np.clip(idx, 0, None)], np.nan)

    # forward 수익률: t+H 이하 마지막 체결가 기준
    R = {}
    for h in HORIZON:
        j = np.searchsorted(ts, grid + h * 1000, side="right") - 1
        ok = (j >= 0) & (j < len(ts)) & valid
        pf = np.where(ok, px[np.clip(j, 0, len(ts) - 1)], np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            R[f"ret_{h}s"] = (pf - p_now) / p_now * 1e4                # bp
        # 지평 끝이 데이터 끝을 넘으면 무효
        R[f"ret_{h}s"][grid + h * 1000 > ts[-1]] = np.nan

    day = np.asarray([datetime.datetime.utcfromtimestamp(t / 1000).strftime("%Y-%m-%d")
                      for t in grid])

    if verbose:
        print(f"[flow] {market} 체결 {len(ts):,} · 격자 {len(grid):,} "
              f"({grid_sec}초) · 대형체결 기준 p95={big_cut:,.0f}원 "
              f"· 가격나이 중앙값 {np.nanmedian(age_ms):,.0f}ms")
    return dict(market=market, grid=grid, day=day, p_now=p_now, age_ms=age_ms,
                feat=F, ret=R, big_cut=big_cut, n_ticks=len(ts))


def save(b):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, f"{b['market']}.npz")
    cols = {f"f_{k}": v for k, v in b["feat"].items()}
    cols.update({f"r_{k}": v for k, v in b["ret"].items()})
    np.savez_compressed(p, grid=b["grid"], day=b["day"], p_now=b["p_now"],
                        age_ms=b["age_ms"], **cols)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="")
    ap.add_argument("--grid-sec", type=int, default=1)
    a = ap.parse_args()
    mks = ([s.strip() for s in a.markets.split(",") if s.strip()]
           or sorted(os.listdir(DIR)))
    for mk in mks:
        b = build(mk, a.grid_sec)
        print(f"        저장 {save(b)}")


if __name__ == "__main__":
    main()
