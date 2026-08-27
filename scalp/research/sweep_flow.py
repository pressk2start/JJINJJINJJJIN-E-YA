#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
주문흐름 스크리닝 2단계 — 임계치 sweep · OOS · 체결가정.

1단계(screen.py)의 Cohen's d gap 은 **양 꼬리 사이의 거리**일 뿐이다.
그 값이 크다고 비용을 넘는 기대값이 있다는 뜻이 전혀 아니다. 여기서 실제로
규칙을 만들어 넣고, 비용을 빼고, 표본 밖에서 다시 재본다.

규율
----
1. 임계치는 **train 구간의 분위수**로 정한다. 관측된 최적 절단점을 그대로
   쓰면 그 절단점 자체가 과최적화다. 분위수 규칙은 OOS 에서도 같은 방식으로
   다시 계산되므로 규칙이 이동해도 정의가 유지된다.
2. **중복 진입 제거.** 지평 H 짜리 forward 수익률은 H 초 동안 겹친다.
   쿨다운 없이 세면 같은 사건을 수백 번 세고 t 값이 부풀려진다.
3. **일 클러스터 t.** 초 단위 관측은 강하게 자기상관한다. 관측 수로 계산한
   t 는 무의미하다. 일 단위로 묶어 일별 평균의 분산으로 t 를 낸다.
4. **체결가정 3종.** 틱 데이터만으로는 진입가를 알 수 없다. 낙관/중간/보수를
   따로 내고, 보수 가정에서 죽으면 채택하지 않는다.
5. 롱 전용. 업비트 KRW 현물은 공매도가 없다. 하락 신호는 진입이 아니라
   회피 신호이므로 여기서 수익으로 환산하지 않는다.
"""
import os, json, argparse
import numpy as np

R = os.path.dirname(os.path.abspath(__file__))
FLOW = os.path.join(R, "data", "flow")
OUT = os.path.join(R, "results")
FEE_ROUNDTRIP_BP = 10.0


def load(market):
    z = np.load(os.path.join(FLOW, f"{market}.npz"), allow_pickle=False)
    return dict(grid=z["grid"], day=z["day"].astype(str), p_now=z["p_now"],
                age_ms=z["age_ms"],
                feat={k[2:]: z[k] for k in z.files if k.startswith("f_")},
                ret={k[2:]: z[k] for k in z.files if k.startswith("r_")})


def day_cluster_t(vals, days):
    """일별 평균의 t. 초 단위 자기상관을 관측 수로 부풀리지 않는다."""
    ud = np.unique(days)
    if len(ud) < 3:
        return float("nan"), len(ud)
    means = np.array([vals[days == d].mean() for d in ud if (days == d).sum() >= 5])
    if len(means) < 3:
        return float("nan"), len(means)
    sd = means.std(ddof=1)
    if sd <= 0:
        return float("nan"), len(means)
    return float(means.mean() / (sd / np.sqrt(len(means)))), len(means)


def cooldown(idx, grid, hold_ms):
    """겹치는 진입 제거. 한 번 들어가면 지평이 끝날 때까지 다시 안 들어간다."""
    keep, last = [], -1
    for i in idx:
        t = grid[i]
        if t >= last:
            keep.append(i)
            last = t + hold_ms
    return np.asarray(keep, dtype=np.int64)


def sweep_market(market, horizons, qs, cost_bp, tick_bp, train_frac, max_stale_ms,
                 min_trades):
    b = load(market)
    grid, day, p, age = b["grid"], b["day"], b["p_now"], b["age_ms"]
    fresh = np.isfinite(age) & (age <= max_stale_ms) & np.isfinite(p)

    ud = np.unique(day)
    n_tr = max(1, int(round(len(ud) * train_frac)))
    tr_days, te_days = set(ud[:n_tr]), set(ud[n_tr:])
    is_tr = np.array([d in tr_days for d in day])
    is_te = np.array([d in te_days for d in day])

    rows = []
    for h in horizons:
        r = b["ret"][f"ret_{h}s"]
        base = fresh & np.isfinite(r)
        for name, fv in b["feat"].items():
            ok = base & np.isfinite(fv)
            if not ok.any():
                continue
            for q in qs:
                # 임계치는 train 구간 분위수로만 정한다 (test 를 보지 않는다)
                m_tr = ok & is_tr
                if m_tr.sum() < 1000:
                    continue
                thr = float(np.quantile(fv[m_tr], q))
                res = {}
                for tag, mask in (("train", m_tr), ("oos", ok & is_te)):
                    sel = np.where(mask & (fv >= thr))[0]
                    if len(sel) == 0:
                        res[tag] = None
                        continue
                    sel = cooldown(sel, grid, h * 1000)
                    if len(sel) < min_trades:
                        res[tag] = None
                        continue
                    raw = r[sel]
                    fills = {
                        "낙관": raw - cost_bp,                    # 체결가 그대로
                        "중간": raw - cost_bp - tick_bp,          # 진입 시 반틱 × 2
                        "보수": raw - cost_bp - 2 * tick_bp,      # 진입·청산 모두 불리
                    }
                    e = {}
                    for fk, v in fills.items():
                        t, nd = day_cluster_t(v, day[sel])
                        e[fk] = dict(mean=float(v.mean()), win=float((v > 0).mean()),
                                     t=t, n_days=nd)
                    res[tag] = dict(n=int(len(sel)), raw_mean=float(raw.mean()), **e)
                if res["train"] and res["oos"]:
                    rows.append(dict(market=market, horizon=h, feature=name, q=q,
                                     thr=thr, train=res["train"], oos=res["oos"]))
    return rows, sorted(tr_days), sorted(te_days)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="KRW-XRP,KRW-BTC,KRW-ETH,KRW-SOL,KRW-TRUMP")
    ap.add_argument("--horizon", default="30,60,120")
    ap.add_argument("--q", default="0.90,0.95,0.99,0.995")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--max-stale-sec", type=float, default=5.0)
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    off = {}
    tp = os.path.join(OUT, "tick_size.json")
    if os.path.exists(tp):
        off = {r["market"]: r for r in
               json.load(open(tp, encoding="utf-8")).get("rows", [])}

    hs = [int(s) for s in a.horizon.split(",")]
    qs = [float(s) for s in a.q.split(",")]
    allrows = []
    for mk in [s.strip() for s in a.markets.split(",") if s.strip()]:
        row = off.get(mk, {})
        tick_bp = float(row.get("tick_bp", 5.0))
        cost = FEE_ROUNDTRIP_BP + max(float(row.get("spread_bp_now", tick_bp)), tick_bp)
        rr, trd, ted = sweep_market(mk, hs, qs, cost, tick_bp, a.train_frac,
                                    a.max_stale_sec * 1000, a.min_trades)
        allrows += rr
        print(f"[sweep] {mk:<10} 셀 {len(rr):>4} · 비용 {cost:5.2f}bp · 틱 {tick_bp:4.2f}bp "
              f"· train {len(trd)}일 / oos {len(ted)}일")

    print("\n" + "=" * 108)
    print("보수 가정 OOS 기대값 상위 — 채택 기준은 '보수 가정에서도 양수'다")
    print("-" * 108)
    print(f"{'종목':<10}{'지평':>5}{'특징':>10}{'q':>7}{'훈련n':>7}{'훈련보수':>9}"
          f"{'OOSn':>6}{'OOS보수':>9}{'OOS_t':>8}{'승률':>7}")
    cand = [r for r in allrows if np.isfinite(r["oos"]["보수"]["t"])]
    for r in sorted(cand, key=lambda x: -x["oos"]["보수"]["mean"])[:20]:
        print(f"{r['market']:<10}{r['horizon']:>4}s{r['feature']:>10}{r['q']:>7.3f}"
              f"{r['train']['n']:>7,}{r['train']['보수']['mean']:>9.2f}"
              f"{r['oos']['n']:>6,}{r['oos']['보수']['mean']:>9.2f}"
              f"{r['oos']['보수']['t']:>8.2f}{r['oos']['보수']['win']:>7.3f}")

    n_pos_tr = sum(1 for r in allrows if r["train"]["보수"]["mean"] > 0)
    n_pos_oos = sum(1 for r in allrows if r["oos"]["보수"]["mean"] > 0)
    n_both = sum(1 for r in allrows
                 if r["train"]["보수"]["mean"] > 0 and r["oos"]["보수"]["mean"] > 0
                 and np.isfinite(r["oos"]["보수"]["t"]) and r["oos"]["보수"]["t"] > 2)
    print("\n" + "-" * 108)
    print(f"전체 셀 {len(allrows):,}")
    print(f"  훈련 보수 양수            {n_pos_tr:,} ({n_pos_tr/max(len(allrows),1)*100:.1f}%)")
    print(f"  OOS  보수 양수            {n_pos_oos:,} ({n_pos_oos/max(len(allrows),1)*100:.1f}%)")
    print(f"  훈련·OOS 모두 양수 & t>2  {n_both:,}")
    # 낙관 가정에서만 살아남는 셀 수 — 체결가정이 얼마나 결정적인지 보여준다
    n_opt = sum(1 for r in allrows if r["oos"]["낙관"]["mean"] > 0)
    print(f"  (참고) OOS 낙관 양수      {n_opt:,} — 낙관 가정은 스프레드를 공짜로 준다")

    if a.save:
        os.makedirs(OUT, exist_ok=True)
        p = os.path.join(OUT, "flow_sweep.json")
        json.dump(dict(params=vars(a), rows=allrows), open(p, "w"),
                  ensure_ascii=False, indent=1)
        print(f"\n저장 {p}")


if __name__ == "__main__":
    main()
