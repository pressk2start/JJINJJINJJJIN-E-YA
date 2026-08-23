# -*- coding: utf-8 -*-
"""tick_size.py — 호가단위 **공식 조회** + universe 스크리닝.

왜 체결가 역추정을 쓰지 않는가
------------------------------
업비트는 `GET /v1/orderbook/instruments` 로 종목별 `tick_size` 를 직접 제공한다.
따라서 "최근 체결가 집합의 최소 양수 간격"으로 역추정할 이유가 없다.

역추정은 실제로 틀린다. 병렬 세션이 그 방식으로 KRW-DOGE 를 0.1원(9.4bp)으로 측정했는데,
공식 API 는 **1원**을 반환한다 (125원 기준 **80bp**). 8배 차이다.
역추정은 관측된 체결가가 우연히 2틱·3틱 간격으로만 찍히면 과대추정되고,
반대로 과거 표본(가격대가 달랐던 시점)이 섞이면 과소추정된다.
DOGE 처럼 가격이 구간 경계(100원)를 넘나든 종목에서 정확히 그 일이 일어났다.

공식 API 는 **현재 적용 중인** tick_size 를 반환하므로 구간 전환도 자동으로 따라간다.
→ 구간표 하드코딩도, 체결가 역추정도 하지 않는다. 조회만 한다.

응답 예 (실측):
  {"market":"KRW-DOGE","quote_currency":"KRW","tick_size":"1",
   "supported_levels":["0","1","10","100"]}
  · tick_size 는 **문자열**로 온다 → float 변환 필요
  · supported_levels = WS orderbook 호가 묶음 단위 (0=기본)
  · markets 파라미터 필수 (생략 시 HTTP 400)

왜 tick_bp 가 스켈핑을 양쪽에서 막는가
--------------------------------------
tick_bp = tick / price × 10000. 스켈핑이 노리는 건 1~2틱이므로 이 값이 전부다.

  · tick_bp 가 너무 작으면 (BTC 0.1bp) 왕복비용까지 200틱 이상 = 가격이 그만큼 안 움직인다
  · tick_bp 가 너무 크면 (DOGE 80bp) **스프레드 자체가 비용을 초과**한다

두 번째가 중요하다. 스프레드가 최소 1틱이므로 왕복으로 스프레드를 한 번 넘으면
그것만으로 tick_bp 만큼 잃는다. 즉 **종목별 비용 하한**은 고정 21.7bp 가 아니라

    cost_floor_bp ≈ 수수료 왕복 10bp + max(관측 스프레드, tick_bp)

이다. DOGE 라면 10 + 80 = 90bp 로, 전 종목 공통 가정(21.7bp)의 4배다.
비용을 전 종목 공통으로 놓고 스윕하면 이런 종목에서 결과가 통째로 낙관 편향된다.

참여율
------
주문금액 / 분당 거래대금. 제약은 중앙값이 아니라 **p10(한산한 분)** 에서 온다.
스켈핑은 시간대를 고르지 않으므로 한산한 구간에서도 체결돼야 한다.

사용:
  python3 tick_size.py --top 30
  python3 tick_size.py --markets KRW-XRP,KRW-ETH --notional 300000
  python3 tick_size.py --all --save
"""
import os, sys, json, time, argparse, statistics as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

FEE_ROUNDTRIP_BP = 10.0     # 업비트 KRW 0.05% × 2 (메이커/테이커 차등 없음)
# 스테이블코인은 비용/유동성 기준을 통과해도 스켈핑 대상이 아니다 (가격이 움직이지 않는다).
# 실측: KRW-USDT 의 h=5분 비용초과확률 0.1% — 비용이 아니라 변동성이 없어서 불가능하다.
STABLE = {"KRW-USDT", "KRW-USDC", "KRW-DAI", "KRW-TUSD", "KRW-BUSD"}
PARTICIPATION_MAX = 1.0     # 주문금액 / 분당 거래대금 중앙값 (%)
PARTICIPATION_QUIET_MAX = 5.0   # 한산한 분(p10) 기준 상한


def instruments(markets):
    """공식 tick_size 조회. markets 파라미터 필수, 100개씩 배치."""
    out = {}
    for i in range(0, len(markets), 100):
        chunk = markets[i:i + 100]
        for r in collect.get("/orderbook/instruments", {"markets": ",".join(chunk)}):
            out[r["market"]] = {"tick": float(r["tick_size"]),
                                "levels": r.get("supported_levels", [])}
    return out


def spread_now(markets):
    """현재 호가 스냅샷의 최우선 스프레드(bp). 1회 관측이라 노이즈가 있다 —
    tick_bp 가 이 값의 하한이므로 둘을 함께 본다."""
    out = {}
    for i in range(0, len(markets), 10):
        for o in collect.get("/orderbook", {"markets": ",".join(markets[i:i + 10])}):
            u = (o.get("orderbook_units") or [None])[0]
            if not u:
                continue
            b, a = u["bid_price"], u["ask_price"]
            mid = (b + a) / 2.0
            if mid > 0:
                out[o["market"]] = (a - b) / mid * 1e4
    return out


def minute_value(market, count=200):
    """최근 count분 거래대금 (p50, p10). p10 = 한산한 분 = 진짜 제약."""
    js = collect.get("/candles/minutes/1", {"market": market, "count": count})
    if not isinstance(js, list) or len(js) < 30:
        return None, None
    v = sorted(float(c["candle_acc_trade_price"]) for c in js)
    return st.median(v), v[int(0.10 * (len(v) - 1))]


def screen(markets, notional):
    inst = instruments(markets)
    spr = spread_now(markets)
    px = {}
    for i in range(0, len(markets), 100):
        for t in collect.get("/ticker", {"markets": ",".join(markets[i:i + 100])}):
            px[t["market"]] = float(t["trade_price"])
    rows = []
    for m in markets:
        info = inst.get(m)
        p = px.get(m)
        if not info or not p:
            continue
        p50, p10 = minute_value(m)
        if not p50:
            continue
        tick_bp = info["tick"] / p * 1e4
        sp = spr.get(m)
        floor = FEE_ROUNDTRIP_BP + max(sp if sp is not None else 0.0, tick_bp)
        part = notional / p50 * 100.0
        part_q = notional / p10 * 100.0 if p10 and p10 > 0 else float("inf")
        rows.append({"market": m, "price": p, "tick": info["tick"], "tick_bp": tick_bp,
                     "spread_bp_now": sp, "cost_floor_bp": floor,
                     "min_krw_p50": p50, "min_krw_p10": p10,
                     "participation_pct": part, "participation_quiet_pct": part_q,
                     "levels": info["levels"]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--markets", default="")
    ap.add_argument("--notional", type=float, default=300_000.0)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.markets:
        mks = [s.strip() for s in a.markets.split(",") if s.strip()]
    else:
        ranked = [m for m, _ in collect.krw_markets_by_value() if m not in STABLE]
        mks = ranked if a.all else ranked[:a.top]

    rows = screen(mks, a.notional)
    rows.sort(key=lambda r: -r["min_krw_p50"])

    print(f"주문금액 {a.notional:,.0f}원 · 수수료 왕복 {FEE_ROUNDTRIP_BP:.0f}bp")
    print(f"cost_floor = 수수료 + max(현재 스프레드, tick_bp)  ← 종목별로 다르다\n")
    print(f"{'market':<12}{'price':>13}{'tick':>9}{'tick_bp':>9}{'spread':>8}"
          f"{'cost_floor':>11}{'참여율':>8}{'한산분':>9}  판정")
    for r in rows:
        ok = (r["market"] not in STABLE
              and r["participation_pct"] <= PARTICIPATION_MAX
              and r["participation_quiet_pct"] <= PARTICIPATION_QUIET_MAX
              and r["cost_floor_bp"] <= 25.0)
        why = ("적격" if ok else
               "스테이블(변동성 없음)" if r["market"] in STABLE else
               "비용바닥 과대" if r["cost_floor_bp"] > 25.0 else "유동성 부족")
        r["eligible"] = ok; r["reason"] = why
        sp = f"{r['spread_bp_now']:.1f}" if r["spread_bp_now"] is not None else "n/a"
        print(f"{r['market']:<12}{r['price']:>13,.6g}{r['tick']:>9g}{r['tick_bp']:>9.2f}"
              f"{sp:>8}{r['cost_floor_bp']:>11.1f}{r['participation_pct']:>7.2f}%"
              f"{r['participation_quiet_pct']:>8.1f}%  {why}")

    elig = [r for r in rows if r["eligible"]]
    print(f"\n적격 {len(elig)}/{len(rows)}: {', '.join(r['market'] for r in elig) or '없음'}")
    print("\n* tick_size 는 GET /v1/orderbook/instruments 공식 조회값이다 (체결가 역추정 아님).")
    print("* spread 는 1회 스냅샷이라 노이즈가 있다. tick_bp 가 그 하한이다.")
    print("* cost_floor 를 전 종목 공통 21.7bp 로 놓고 스윕하면 고틱 종목에서 낙관 편향된다.")
    print("* 참여율 제약은 중앙값이 아니라 한산분(p10)에서 온다.")
    print("* 적격 기준(참여율 1%/5%, cost_floor 25bp)은 **설계 판단**이지 데이터에서 나온 값이 아니다.")

    if a.save:
        os.makedirs(OUT, exist_ok=True)
        f = os.path.join(OUT, "tick_size.json")
        json.dump({"measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "notional": a.notional, "source": "/v1/orderbook/instruments",
                   "rows": rows}, open(f, "w"), indent=1)
        print(f"\n→ {f}")


if __name__ == "__main__":
    main()
