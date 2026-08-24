#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
주문흐름 스크리닝 1단계 — 사건 전(前) 분포 비교.

"급등했다"가 아니라 "급등 **전에** 어떤 값이 있었나"를 역으로 본다.
forward 수익률 상·하위 사건을 정의하고, 그 직전 특징값 분포가 전체 분포와
얼마나 떨어져 있는지를 Cohen's d 로 잰다.

왜 **양측** gap 인가
--------------------
변동성 대리변수는 상승 사건 전에도 하락 사건 전에도 똑같이 커진다.
d_up 만 보면 "이 특징이 급등을 예고한다"고 착각한다. 방향성 신호라면
d_up 과 d_dn 이 **반대 부호**여야 하므로 gap = d_up - d_dn 으로 본다.
  · |gap| 크고 |common| 작다 → 방향성 신호 후보
  · d_up ≈ d_dn (같은 부호)   → 변동성 대리변수. 방향을 못 맞춘다.
실제로 KRW-SOL 의 ar_10s 가 d_up=2.31 / d_dn=1.68 (common 4.00, gap 0.63) 로
잡혔다. 한쪽만 봤으면 '도착률 급증 = 급등 예고'로 읽었을 값이다.

이 단계는 **발견**이지 채택이 아니다. 여기 숫자로 전략을 만들면 안 된다.
통과한 것만 임계치 sweep 과 OOS 로 넘긴다.

호가단위
--------
규칙표를 손으로 유지하지 않는다. 실제로 틀렸다: 100,000~500,000원 구간을
50원으로 적은 표가 있었지만 공식값은 100원이고 7일 체결가의 최소 간격도
100원이었다(KRW-SOL). 그래서 **공식 조회값(results/tick_size.json)** 을 쓰고,
그 값이 이 7일 구간에도 유효했는지 관측 간격으로 검증한다. 역추정이 아니라
검증이다.
"""
import os, json, argparse
import numpy as np

R = os.path.dirname(os.path.abspath(__file__))
FLOW = os.path.join(R, "data", "flow")
OUT = os.path.join(R, "results")

FEE_ROUNDTRIP_BP = 10.0          # 업비트 KRW 0.05% × 2 (maker/taker 구분 없음)
OFFICIAL = {}                    # main() 에서 채운다


def load(market):
    z = np.load(os.path.join(FLOW, f"{market}.npz"), allow_pickle=False)
    feat = {k[2:]: z[k] for k in z.files if k.startswith("f_")}
    ret = {k[2:]: z[k] for k in z.files if k.startswith("r_")}
    return dict(grid=z["grid"], day=z["day"].astype(str), p_now=z["p_now"],
                age_ms=z["age_ms"], feat=feat, ret=ret)


def official_ticks():
    p = os.path.join(OUT, "tick_size.json")
    if not os.path.exists(p):
        return {}
    return {r["market"]: r for r in
            json.load(open(p, encoding="utf-8")).get("rows", [])}


def observed_tick(p_now):
    """이 구간 체결가의 최소 양수 간격. 역추정이 아니라 공식값 검증용이다."""
    u = np.unique(p_now[np.isfinite(p_now)])
    if len(u) < 3:
        return None
    g = np.diff(u)
    g = g[g > 0]
    return float(np.min(g)) if len(g) else None


def cost_model(market, p_now):
    """비용 하한을 **두 가지로 따로** 낸다. 하나로 합치면 무엇을 가정했는지 숨는다.

      floor_tick   = 수수료 + 틱bp        — 어떤 경우에도 이보다 좁을 수 없는 절대 하한
      floor_spread = 수수료 + max(스프레드, 틱)bp — 더 현실적이나 **스냅샷 1회** 관측이다

    BTC 는 틱 1000원(0.10bp)인데 스냅샷 스프레드가 5.46bp다. 틱만 보면 비용을
    50배 과소평가한다. 이 7일 구간의 실제 스프레드 시계열은 틱 데이터만으로는
    알 수 없다 — ws_recorder 가 쌓는 오더북이 그 답을 준다. 그때까지는 범위로 둔다.
    """
    med_p = float(np.nanmedian(p_now))
    row = OFFICIAL.get(market)
    obs = observed_tick(p_now)
    tick = float(row["tick"]) if row else None
    if tick is None:
        note = "공식값없음"
    elif obs is None:
        note = "관측불가"
    elif abs(obs - tick) < 1e-9:
        note = "일치"
    else:
        note = f"불일치 공식{tick:g}/관측{obs:g}"
        tick = max(tick, obs)          # 어긋나면 보수적으로 큰 쪽
    tick_bp = tick / med_p * 1e4 if tick else float("nan")
    spr_bp = float(row["spread_bp_now"]) if row else float("nan")
    return dict(tick=tick, tick_obs=obs, tick_note=note, med_price=med_p,
                tick_bp=tick_bp, spread_bp_snapshot=spr_bp,
                floor_tick_bp=FEE_ROUNDTRIP_BP + tick_bp,
                floor_spread_bp=FEE_ROUNDTRIP_BP + max(spr_bp, tick_bp))


def cohens_d(x_sub, mu, sd):
    if len(x_sub) < 30 or not np.isfinite(sd) or sd <= 0:
        return np.nan
    return (float(np.nanmean(x_sub)) - mu) / sd


def screen_market(market, horizon, ev_q, max_stale_ms, min_n):
    b = load(market)
    p, age = b["p_now"], b["age_ms"]

    # 가격이 오래 묵은 격자점은 제외한다. 안 걸면 거래가 없는 구간이
    # '변화 0'으로 대량 유입돼 모든 통계를 0 쪽으로 끌어당긴다.
    fresh = np.isfinite(age) & (age <= max_stale_ms) & np.isfinite(p)
    kept = float(fresh.mean())
    cm = cost_model(market, p[fresh])

    rows, ev_up, ev_dn = [], {}, {}
    for h in horizon:
        r = b["ret"][f"ret_{h}s"]
        m = fresh & np.isfinite(r)
        if m.sum() < min_n:
            continue
        rv = r[m]
        hi_cut = float(np.quantile(rv, 1 - ev_q))
        lo_cut = float(np.quantile(rv, ev_q))
        up, dn = rv >= hi_cut, rv <= lo_cut
        # 사건의 크기. 비용 하한과 나란히 놓아야 의미가 생긴다.
        ev_up[h] = dict(cut=hi_cut, mean=float(rv[up].mean()))
        ev_dn[h] = dict(cut=lo_cut, mean=float(rv[dn].mean()))
        for name, fv in b["feat"].items():
            x = fv[m]
            good = np.isfinite(x)
            if good.sum() < min_n:
                continue
            mu, sd = float(np.nanmean(x[good])), float(np.nanstd(x[good]))
            d_up = cohens_d(x[up & good], mu, sd)
            d_dn = cohens_d(x[dn & good], mu, sd)
            if not (np.isfinite(d_up) and np.isfinite(d_dn)):
                continue
            rows.append(dict(market=market, horizon=h, feature=name,
                             d_up=d_up, d_dn=d_dn, gap=d_up - d_dn,
                             common=d_up + d_dn, n=int(m.sum())))
    return rows, dict(market=market, kept_frac=kept, n_grid=int(len(p)),
                      ev_up_bp=ev_up, ev_dn_bp=ev_dn, **cm)


def main():
    global OFFICIAL
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="")
    ap.add_argument("--horizon", default="30,60,120")
    ap.add_argument("--event-q", type=float, default=0.01)
    ap.add_argument("--max-stale-sec", type=float, default=5.0)
    ap.add_argument("--min-n", type=int, default=5000)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    OFFICIAL = official_ticks()
    mks = ([s.strip() for s in a.markets.split(",") if s.strip()]
           or sorted(f[:-4] for f in os.listdir(FLOW) if f.endswith(".npz")))
    hs = [int(s) for s in a.horizon.split(",")]

    all_rows, meta = [], []
    for mk in mks:
        rows, mt = screen_market(mk, hs, a.event_q, a.max_stale_sec * 1000, a.min_n)
        all_rows += rows
        meta.append(mt)

    print("=" * 104)
    print("종목별 표본 상태 · 비용 하한 두 가지 (수수료 왕복 10bp 포함)")
    print("  floor_tick = 절대 하한(틱 1개) · floor_spr = 스냅샷 스프레드 기준(더 현실적)")
    print("-" * 104)
    print(f"{'종목':<11}{'신선격자':>9}{'틱':>10}{'틱bp':>8}{'스프레드bp':>11}"
          f"{'floor_tick':>11}{'floor_spr':>10}{'틱검증':>16}")
    for m in meta:
        print(f"{m['market']:<11}{m['kept_frac']*100:>8.1f}%{m['tick']:>10g}"
              f"{m['tick_bp']:>8.2f}{m['spread_bp_snapshot']:>11.2f}"
              f"{m['floor_tick_bp']:>11.2f}{m['floor_spread_bp']:>10.2f}"
              f"{m['tick_note']:>16}")

    print("\n" + "=" * 104)
    print("사건 크기 vs 비용 — 신호가 완벽해도 이 크기를 못 넘으면 채택 대상이 아니다")
    print("-" * 104)
    print(f"{'종목':<11}{'지평':>5}{'상위1%평균bp':>14}{'하위1%평균bp':>14}"
          f"{'floor_spr':>11}{'상위-비용':>11}")
    for m in meta:
        for h in sorted(m["ev_up_bp"]):
            u = m["ev_up_bp"][h]["mean"]
            d = m["ev_dn_bp"][h]["mean"]
            fl = m["floor_spread_bp"]
            print(f"{m['market']:<11}{h:>4}s{u:>14.2f}{d:>14.2f}{fl:>11.2f}{u-fl:>+11.2f}")

    print("\n" + "=" * 104)
    print(f"양측 gap 상위 (사건 = forward 수익률 상·하위 {a.event_q*100:.0f}%)")
    print("gap = d_up - d_dn (방향성) · common = d_up + d_dn (변동성 대리 성분)")
    print("-" * 104)
    print(f"{'종목':<11}{'지평':>5}{'특징':>12}{'d_up':>8}{'d_dn':>8}"
          f"{'gap':>8}{'common':>9}{'n':>10}")
    for r in sorted(all_rows, key=lambda x: -abs(x["gap"]))[:25]:
        print(f"{r['market']:<11}{r['horizon']:>4}s{r['feature']:>12}"
              f"{r['d_up']:>8.3f}{r['d_dn']:>8.3f}{r['gap']:>8.3f}"
              f"{r['common']:>9.3f}{r['n']:>10,}")

    if a.save:
        os.makedirs(OUT, exist_ok=True)
        p = os.path.join(OUT, "flow_screen.json")
        json.dump(dict(params=vars(a), meta=meta, rows=all_rows),
                  open(p, "w"), ensure_ascii=False, indent=1)
        print(f"\n저장 {p}")


if __name__ == "__main__":
    main()
