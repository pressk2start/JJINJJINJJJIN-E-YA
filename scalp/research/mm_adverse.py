#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
지정가(패시브) 유동성 공급의 역선택 측정 — 잠긴 스프레드를 먹을 수 있는가.

배경
----
업비트 KRW 287종목 중 274종목이 스프레드 10bp(수수료 왕복)를 넘는다.
그런데 KRW-DOGE(122원) 같은 종목은 스프레드가 **정확히 1틱**이다. 1원 틱이
82bp 라서, 유동성이 없어서가 아니라 **호가단위가 더 좁은 호가를 금지**해서
넓다. DOGE 는 24h 224억, 최우선호가에만 5.2억원이 쌓여 있다.

그래서 질문이 바뀐다. "방향을 맞출 수 있나"(2,193셀에서 실패)가 아니라
"구조적으로 잠긴 스프레드를 지정가로 먹을 수 있나"가 된다.

측정 원리
---------
스프레드가 1틱으로 잠겨 있으므로 체결가만으로 중간가를 복원할 수 있다.
  · 공격적 매수(BID) → 매도호가에서 체결 → mid = P - tick/2
  · 공격적 매도(ASK) → 매수호가에서 체결 → mid = P + tick/2
즉 mid_t = P_t - side_taker * tick/2   (side_taker: 매수 +1, 매도 -1)

마켓메이커는 그 반대편이다. 테이커가 살 때 MM 이 팔고, 테이커가 팔 때 MM 이 산다.
표준 분해를 쓴다:
  유효 반스프레드(수입)   = side_taker * (P_t - mid_t)          = tick/2
  가격충격(역선택 비용)   = side_taker * (mid_{t+H} - mid_t)
  **실현 반스프레드(순)** = side_taker * (P_t - mid_{t+H})       = 유효 - 충격

실현 반스프레드가 MM 이 실제로 남기는 것이다. 왕복이면 2배를 벌고 수수료
10bp 를 낸다.

이 측정이 **과대평가**하는 것 (반드시 같이 읽을 것)
--------------------------------------------------
1. **체결률 100% 가정.** 모든 체결에서 MM 이 반대편을 잡았다고 본다. 실제로는
   호가 대기열 뒤에 있으면 안 채워진다. DOGE 최우선호가 5.2억원 뒤에 서면
   체결되는 건 그 앞이 다 소진된 뒤다.
2. **재고 무시.** 매도가 몰리면 MM 은 계속 사서 재고가 쌓인다. 실현 반스프레드는
   '중간가에 청산 가능'을 가정한다.
3. **호가 갱신 무시.** 실제 MM 은 가격이 움직이면 호가를 물린다. 여기선 안 물린다.

따라서 이 측정에서 **음수가 나오면 확실히 죽은 것**이고, 양수가 나와도
채택이 아니라 다음 단계(체결률·재고 모형)로 가는 조건이다.
"""
import os, gzip, json, argparse
import numpy as np

R = os.path.dirname(os.path.abspath(__file__))
TICKS = os.path.join(R, "data", "ticks")
FEE_ROUNDTRIP_BP = 10.0
HORIZONS = (5, 15, 30, 60, 120)


def load(market):
    d = os.path.join(TICKS, market)
    seen, ts, px, buy = set(), [], [], []
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
                ts.append(r["timestamp"]); px.append(r["trade_price"])
                buy.append(r["ask_bid"] == "BID")
    ts = np.asarray(ts, dtype=np.int64)
    o = np.argsort(ts, kind="stable")
    return ts[o], np.asarray(px)[o], np.asarray(buy)[o]


def infer_tick(px):
    """관측된 최소 양수 가격 간격. 1틱 스프레드 가정의 근거를 데이터로 잡는다."""
    u = np.unique(px)
    g = np.diff(u)
    g = g[g > 0]
    return float(np.min(g)) if len(g) else float("nan")


def analyse(market, horizons):
    ts, px, buy = load(market)
    tick = infer_tick(px)
    side = np.where(buy, 1.0, -1.0)          # 테이커 방향
    mid = px - side * tick / 2.0             # 1틱 스프레드에서 중간가 복원

    # 1틱 가정 검증: 가격이 바뀔 때 대부분 1틱씩 움직여야 한다
    d = np.abs(np.diff(px))
    d = d[d > 0]
    one_tick_frac = float(np.mean(np.isclose(d, tick))) if len(d) else float("nan")

    med_mid = float(np.median(mid))
    eff_half_bp = tick / 2.0 / med_mid * 1e4      # 유효 반스프레드 (상수)

    out = dict(market=market, n=int(len(ts)), tick=tick, price=med_mid,
               one_tick_frac=one_tick_frac, eff_half_bp=eff_half_bp,
               taker_buy_frac=float(buy.mean()), h={})
    for H in horizons:
        j = np.searchsorted(ts, ts + H * 1000, side="right") - 1
        ok = (j >= 0) & (j < len(ts)) & (ts + H * 1000 <= ts[-1])
        mid_f = np.where(ok, mid[np.clip(j, 0, len(ts) - 1)], np.nan)
        # 실현 반스프레드 = side * (P_t - mid_{t+H})
        rea = side * (px - mid_f) / med_mid * 1e4
        rea = rea[np.isfinite(rea)]
        if len(rea) < 100:
            continue
        imp = eff_half_bp - rea
        rt = 2 * rea - FEE_ROUNDTRIP_BP        # 왕복 순손익
        out["h"][H] = dict(n=int(len(rea)),
                           realized_half_bp=float(rea.mean()),
                           impact_bp=float(imp.mean()),
                           roundtrip_net_bp=float(rt.mean()),
                           rt_median=float(np.median(rt)),
                           win=float((rt > 0).mean()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="")
    ap.add_argument("--horizon", default=",".join(str(h) for h in HORIZONS))
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    mks = ([s.strip() for s in a.markets.split(",") if s.strip()]
           or sorted(os.listdir(TICKS)))
    hs = [int(s) for s in a.horizon.split(",")]

    res = []
    for mk in mks:
        try:
            res.append(analyse(mk, hs))
        except Exception as e:
            print(f"  ! {mk}: {type(e).__name__}: {e}")

    print("=" * 100)
    print("시장 상태 — 1틱 가정이 성립하는가")
    print("-" * 100)
    print(f"{'종목':<12}{'체결수':>10}{'틱':>10}{'가격':>11}{'1틱이동비율':>12}"
          f"{'유효반스프bp':>13}{'테이커매수비':>12}")
    for r in res:
        print(f"{r['market']:<12}{r['n']:>10,}{r['tick']:>10g}{r['price']:>11,.4g}"
              f"{r['one_tick_frac']:>12.3f}{r['eff_half_bp']:>13.2f}"
              f"{r['taker_buy_frac']:>12.3f}")

    print("\n" + "=" * 100)
    print("역선택 분해 — 실현 = 유효 − 가격충격. 왕복순 = 2×실현 − 수수료 10bp")
    print("체결률 100%·재고 무시 가정이므로 이 값은 **상한**이다. 음수면 확실히 죽은 것.")
    print("-" * 100)
    print(f"{'종목':<12}{'지평':>5}{'유효반':>9}{'충격':>9}{'실현반':>9}"
          f"{'왕복순bp':>10}{'중앙값':>9}{'승률':>8}")
    for r in res:
        for H, v in sorted(r["h"].items()):
            flag = "" if v["roundtrip_net_bp"] > 0 else "  ✗"
            print(f"{r['market']:<12}{H:>4}s{r['eff_half_bp']:>9.2f}"
                  f"{v['impact_bp']:>9.2f}{v['realized_half_bp']:>9.2f}"
                  f"{v['roundtrip_net_bp']:>10.2f}{v['rt_median']:>9.2f}"
                  f"{v['win']:>8.3f}{flag}")

    if a.save:
        p = os.path.join(R, "results", "mm_adverse.json")
        json.dump(res, open(p, "w"), ensure_ascii=False, indent=1)
        print(f"\n저장 {p}")


if __name__ == "__main__":
    main()
