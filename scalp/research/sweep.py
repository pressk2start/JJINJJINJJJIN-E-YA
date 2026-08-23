# -*- coding: utf-8 -*-
"""sweep.py — 임계치 탐색 + 3중 방어 검증 (탐색 단계 핵심 엔진).

무엇을 하는가
-------------
"급등했다"를 정의하고 그 전을 보는 게 아니라, 모든 분봉을 표본으로 놓고
  (1) 무조건부 base rate 와 비용 허들을 먼저 세우고
  (2) 결과(forward return) 기준으로 사전조건 분포를 역으로 비교(Cohen's d)하고
  (3) 각 피처 임계치를 train 분위수 규칙으로 잡아 조건부 성과를 스윕하고
  (4) 살아남은 것만 OOS + walk-forward 로 재검증한다.

레포 기존 규율 준수 (research/EDGE_DISCOVERY_PLAN.md)
  · 관찰된 cutpoint 동결 금지 → 임계치는 **train window 분위수 규칙**으로만 정의
  · OOS 시간분할 (train 70 / test 30) + walk-forward
  · 표본 최소 n≥300
  · 수수료/슬리피지 반영 (Upbit 0.05%×2 + 슬리피지 최소 0.05%)

통계 함정 대응 (스켈핑에서 특히 치명적)
  · **표본 중복**: 인접 분의 forward 창이 겹침 → 같은 사건을 수백 번 세는 착시.
    → 마켓별 cooldown = horizon 만큼 간격 강제 (겹치지 않는 표본만 채택)
  · **자기상관**: 분 단위 수익률은 독립이 아님 → t 통계량을 **일자 클러스터**로 계산
    (일별 평균 순수익의 표본분포. 일 = 블록)
  · **다중검정**: 피처×임계치×horizon 조합이 수백 개 → OOS·WF 동시통과만 후보로 인정
"""
import os, sys, json, math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features as FT

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "feat")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# 비용 시나리오 (왕복, %). Upbit 현물 수수료 0.05%×2 = 0.10 고정 + 슬리피지 가정.
COSTS = {"base": 0.20, "expected": 0.217, "stress": 0.30}
# base 0.20 = backtest_engine 기본값과 정합 (수수료 왕복 0.10 + 편도 슬리피지 5bp × 2).
# expected 0.217 = 호가 사다리 실측(스프레드+소진 ~5.7bp) + 수수료 공시 10bp
#                  + **가정** latency 6bp. 전부 실측이 아니다 — latency는 미측정 가정치.
# ⚠ 비용을 0.15 → 0.20 으로 올려도 결론은 불변이다 (생존 셀이 0개였으므로 a fortiori).
MIN_N = 300                      # 레포 규율: 이하는 결론 안 냄
QUANTILES = [0.90, 0.95, 0.99, 0.995, 0.999]


def load_all():
    """모든 마켓 npz → (market, 절대분) 정렬된 단일 행렬."""
    files = sorted(f for f in os.listdir(DIR) if f.endswith(".npz"))
    Xs, Ys, Ts, Ms = [], [], [], []
    import datetime
    for i, f in enumerate(files):
        z = np.load(os.path.join(DIR, f))
        t0 = str(z["t0"])
        base = int(datetime.datetime.strptime(t0, FT.FMT).timestamp() // 60)
        Xs.append(z["X"]); Ys.append(z["Y"])
        Ts.append(z["tmin"].astype(np.int64) + base)
        Ms.append(np.full(len(z["X"]), i, np.int32))
    X = np.concatenate(Xs); Y = np.concatenate(Ys)
    T = np.concatenate(Ts); M = np.concatenate(Ms)
    order = np.lexsort((T, M))                       # 마켓별 시간순
    return X[order], Y[order], T[order], M[order], [f[:-4] for f in files]


def dedupe(idx, M, T, k):
    """겹치는 표본 제거: 같은 마켓에서 직전 채택 이후 k분 이내 트리거는 버림.
    idx는 (market, time) 정렬 순서여야 함."""
    keep = np.empty(len(idx), bool); last = {}
    for j, i in enumerate(idx):
        m = M[i]; t = T[i]
        p = last.get(m)
        if p is None or t - p >= k:
            keep[j] = True; last[m] = t
        else:
            keep[j] = False
    return idx[keep]


def stats(sel, Y, T, ycol, mfecol, maecol, cost_pct):
    """선택된 표본의 성과 요약. 순수익 = forward return − 왕복비용. 일자 클러스터 t 통계량."""
    if len(sel) == 0:
        return None
    r = Y[sel, ycol] * 100.0 - cost_pct
    mfe = Y[sel, mfecol] * 100.0
    mae = Y[sel, maecol] * 100.0
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    day = T[sel] // 1440
    ud = np.unique(day)
    dmeans = np.array([r[day == d].mean() for d in ud])
    nd = len(ud)
    tstat = (dmeans.mean() / (dmeans.std(ddof=1) / math.sqrt(nd))) if nd > 2 and dmeans.std(ddof=1) > 0 else 0.0
    return {"n": int(len(sel)), "n_days": int(nd),
            "win_rate": float((r > 0).mean()),
            "mean": float(r.mean()), "median": float(np.median(r)),
            "mfe": float(mfe.mean()), "mae": float(mae.mean()),
            "pf": float(gp / gl) if gl > 0 else float("inf"),
            "t_day": float(tstat)}


def cohens_d(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2: return 0.0
    va, vb = a.var(ddof=1), b.var(ddof=1)
    sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / max(na + nb - 2, 1))
    return float((a.mean() - b.mean()) / sp) if sp > 0 else 0.0


def main():
    os.makedirs(OUT, exist_ok=True)
    X, Y, T, M, markets = load_all()
    F = {n: i for i, n in enumerate(FT.FEATURES)}
    O = {n: i for i, n in enumerate(FT.OUTCOMES)}
    N = len(X)
    t_lo, t_hi = T.min(), T.max()
    cut = t_lo + int((t_hi - t_lo) * 0.70)
    train = T <= cut; test = T > cut
    print(f"[data] 표본 {N:,} · 마켓 {len(markets)} · "
          f"{(t_hi-t_lo)/1440:.1f}일 · train {train.sum():,} / test {test.sum():,}")

    report = {"n_samples": int(N), "markets": markets,
              "train_days": float((cut - t_lo) / 1440), "test_days": float((t_hi - cut) / 1440),
              "costs": COSTS}

    # ---------- 0) 비용 허들 · 무조건부 base rate ----------
    print("\n=== 0) 무조건부 base rate (겹침 제거 후) · 비용 허들 ===")
    base = {}
    for k in FT.HORIZONS:
        idx = dedupe(np.nonzero(np.ones(N, bool))[0], M, T, k)
        s = stats(idx, Y, T, O[f"f{k}"], O[f"mfe{k}"], O[f"mae{k}"], 0.0)
        sc = stats(idx, Y, T, O[f"f{k}"], O[f"mfe{k}"], O[f"mae{k}"], COSTS["base"])
        base[k] = {"gross": s, "net_base": sc}
        print(f"  h={k:2}분  n={s['n']:8,}  총수익평균={s['mean']:+.4f}%  "
              f"|이동|중앙={abs(s['median']):.4f}%  MFE={s['mfe']:+.3f}% MAE={s['mae']:+.3f}%  "
              f"→ 비용 {COSTS['base']}% 차감 시 평균 {sc['mean']:+.4f}%")
    report["baseline"] = base

    # ---------- 1) 역방향 조사: 결과가 좋았던 표본의 사전조건 ----------
    #  ⚠ 함정: "5분 뒤 +0.5% 이상" 하나만 보면 변동성 피처가 무조건 1등으로 뜬다.
    #     변동성이 높으면 +0.5%도 -0.5%도 같이 늘기 때문 = 동어반복이지 방향성 엣지가 아님.
    #  → 상승 이벤트 d 와 하락 이벤트 d 를 **둘 다** 구하고, 그 차이(d_up − d_dn)로 판정한다.
    #     d_up≈d_dn 이면 그 피처는 변동성 대리변수일 뿐. d_up−d_dn 이 커야 방향성 정보.
    print("\n=== 1) 역방향: 사전조건 비교 (train만) — 상승/하락 이벤트 양방향 대조 ===")
    up = (Y[:, O["f5"]] * 100.0 >= 0.5) & train
    dn = (Y[:, O["f5"]] * 100.0 <= -0.5) & train
    neu = train & ~up & ~dn
    print(f"  상승(+0.5%↑) {up.sum():,} · 하락(−0.5%↓) {dn.sum():,} · 그외 {neu.sum():,} "
          f"(train {train.sum():,}) → 상승률 {up.sum()/train.sum()*100:.3f}% / 하락률 {dn.sum()/train.sum()*100:.3f}%")
    print(f"    {'feature':10} {'d_up':>8} {'d_dn':>8} {'d_up−d_dn':>10}   판정")
    ds = []
    for f in FT.FEATURES:
        d_up = cohens_d(X[up, F[f]], X[neu, F[f]])
        d_dn = cohens_d(X[dn, F[f]], X[neu, F[f]])
        ds.append((abs(d_up - d_dn), d_up, d_dn, f))
    ds.sort(reverse=True)
    for gap, d_up, d_dn, f in ds:
        if gap >= 0.20: verdict = "방향성 후보"
        elif abs(d_up) >= 0.20: verdict = "변동성 대리변수(양방향 동반)"
        else: verdict = "-"
        print(f"    {f:10} {d_up:+8.3f} {d_dn:+8.3f} {d_up-d_dn:+10.3f}   {verdict}")
    report["reverse_lookup_d"] = [{"feature": f, "d_up": u, "d_dn": v, "gap": u - v}
                                  for _, u, v, f in ds]

    # ---------- 2) 단변량 임계치 스윕 (train 분위수 규칙) ----------
    print("\n=== 2) 단변량 임계치 스윕 — train 분위수 규칙 (관찰 cutpoint 동결 아님) ===")
    print(f"    {'feature':10} {'q':6} {'임계값':>12} {'h':>3} {'n':>7} {'승률':>6} "
          f"{'평균순수익':>10} {'중앙':>8} {'MFE':>7} {'MAE':>7} {'PF':>6} {'t(일)':>7}")
    cand = []
    for f in FT.FEATURES:
        col = X[:, F[f]]
        for q in QUANTILES:
            thr = float(np.quantile(col[train], q))
            mask = col >= thr
            for k in FT.HORIZONS:
                idx = dedupe(np.nonzero(mask & train)[0], M, T, k)
                s = stats(idx, Y, T, O[f"f{k}"], O[f"mfe{k}"], O[f"mae{k}"], COSTS["base"])
                if s is None or s["n"] < MIN_N: continue
                rec = {"feature": f, "q": q, "thr": thr, "h": k, "train": s}
                cand.append(rec)
                if s["mean"] > 0 and s["t_day"] > 1.0:
                    print(f"    {f:10} P{q*100:<5.1f} {thr:12.5f} {k:3} {s['n']:7,} "
                          f"{s['win_rate']*100:5.1f}% {s['mean']:+10.4f} {s['median']:+8.4f} "
                          f"{s['mfe']:+7.3f} {s['mae']:+7.3f} {s['pf']:6.2f} {s['t_day']:7.2f}")
    pos = [c for c in cand if c["train"]["mean"] > 0 and c["train"]["t_day"] > 1.0]
    print(f"\n  train 에서 평균 순수익>0 & t(일)>1.0 인 규칙: {len(pos)} / 전체 {len(cand)}")
    report["univariate"] = cand

    # ---------- 3) OOS 검증 ----------
    print("\n=== 3) OOS 검증 (train 에서 정한 임계치를 test 구간에 그대로 적용) ===")
    survivors = []
    for c in sorted(pos, key=lambda x: -x["train"]["mean"])[:40]:
        col = X[:, F[c["feature"]]]
        idx = dedupe(np.nonzero((col >= c["thr"]) & test)[0], M, T, c["h"])
        s = stats(idx, Y, T, O[f"f{c['h']}"], O[f"mfe{c['h']}"], O[f"mae{c['h']}"], COSTS["base"])
        c["test"] = s
        if s is None: continue
        ok = s["n"] >= MIN_N and s["mean"] > 0
        if ok: survivors.append(c)
        print(f"    {c['feature']:10} P{c['q']*100:<5.1f} h={c['h']:2} │ "
              f"train n={c['train']['n']:6,} {c['train']['mean']:+.4f}% t={c['train']['t_day']:5.2f} │ "
              f"test n={s['n']:6,} {s['mean']:+.4f}% t={s['t_day']:5.2f} │ {'생존' if ok else '탈락'}")
    print(f"\n  OOS 생존: {len(survivors)}")
    report["oos_survivors"] = survivors

    # ---------- 4) walk-forward ----------
    print("\n=== 4) Walk-forward (구간 6분할 · 앞 구간 분위수 → 다음 구간 적용) ===")
    NF = 6
    edges = np.linspace(t_lo, t_hi, NF + 1).astype(np.int64)
    wf_out = []
    for c in survivors[:15]:
        col = X[:, F[c["feature"]]]
        folds = []
        for i in range(NF - 1):
            tr = (T >= edges[i]) & (T < edges[i + 1])
            te = (T >= edges[i + 1]) & (T < edges[i + 2])
            if tr.sum() < 1000: continue
            thr = float(np.quantile(col[tr], c["q"]))
            idx = dedupe(np.nonzero((col >= thr) & te)[0], M, T, c["h"])
            s = stats(idx, Y, T, O[f"f{c['h']}"], O[f"mfe{c['h']}"], O[f"mae{c['h']}"], COSTS["base"])
            folds.append(s)
        good = [s for s in folds if s and s["n"] >= 30 and s["mean"] > 0]
        c["wf"] = {"folds": folds, "n_pos": len(good), "n_folds": len([s for s in folds if s])}
        wf_out.append(c)
        seq = " ".join(f"{s['mean']:+.3f}" if s else "  n/a " for s in folds)
        print(f"    {c['feature']:10} P{c['q']*100:<5.1f} h={c['h']:2} │ fold 평균순수익: {seq} │ "
              f"양수 {c['wf']['n_pos']}/{c['wf']['n_folds']}")
    report["walk_forward"] = wf_out

    # ---------- 5) 비용 민감도 ----------
    print("\n=== 5) 비용 민감도 (생존 규칙, test 구간) ===")
    if not survivors:
        print("    (생존 규칙 없음 — 생략)")
    for c in survivors[:15]:
        col = X[:, F[c["feature"]]]
        idx = dedupe(np.nonzero((col >= c["thr"]) & test)[0], M, T, c["h"])
        line = []
        for nm, cost in COSTS.items():
            s = stats(idx, Y, T, O[f"f{c['h']}"], O[f"mfe{c['h']}"], O[f"mae{c['h']}"], cost)
            line.append(f"{nm}({cost}%)={s['mean']:+.4f}%")
        c["cost_sensitivity"] = line
        print(f"    {c['feature']:10} P{c['q']*100:<5.1f} h={c['h']:2} │ " + "  ".join(line))

    # ---------- 6) 브래킷(TP/SL) 청산 ----------
    #  고정 horizon 청산은 스켈핑 현실과 다름. MFE/MAE로 브래킷 결과를 **구간추정**한다.
    #   · 보수(하한): 창 안에서 SL과 TP가 둘 다 닿았으면 SL이 먼저 닿았다고 본다
    #   · 낙관(상한): 반대로 TP가 먼저
    #  진실은 두 값 사이. 하한이 양수여야 실제 엣지로 인정한다.
    print("\n=== 6) 브래킷 청산 TP/SL 격자 (h=10분 창, train/test 각각, 비용 base) ===")
    def bracket(sel, k, tp, sl, cost):
        if len(sel) == 0: return None
        mfe = Y[sel, O[f"mfe{k}"]] * 100.0
        mae = Y[sel, O[f"mae{k}"]] * 100.0
        fin = Y[sel, O[f"f{k}"]] * 100.0
        lo = np.where(mae <= -sl, -sl, np.where(mfe >= tp, tp, fin)) - cost
        hi = np.where(mfe >= tp, tp, np.where(mae <= -sl, -sl, fin)) - cost
        day = T[sel] // 1440; ud = np.unique(day)
        dm = np.array([lo[day == d].mean() for d in ud])
        ts = (dm.mean() / (dm.std(ddof=1) / math.sqrt(len(ud)))) if len(ud) > 2 and dm.std(ddof=1) > 0 else 0.0
        return {"n": int(len(sel)), "lo_mean": float(lo.mean()), "hi_mean": float(hi.mean()),
                "lo_win": float((lo > 0).mean()), "t_day_lo": float(ts)}
    K = 10
    grid = []
    print(f"    {'rule':26} {'TP':>5} {'SL':>5} {'n':>7} {'보수평균':>10} {'낙관평균':>10} {'승률':>6} {'t(일)':>7}")
    rules = [("(무조건부)", None, None)] + [(f"{c['feature']}≥P{c['q']*100:.1f}", c["feature"], c["thr"])
                                            for c in sorted(cand, key=lambda x: -x["train"]["mean"])[:6]]
    for name, feat, thr in rules:
        mask = np.ones(N, bool) if feat is None else (X[:, F[feat]] >= thr)
        idx = dedupe(np.nonzero(mask & test)[0], M, T, K)
        for tp in (0.3, 0.5, 0.8, 1.2):
            for sl in (0.3, 0.5):
                b = bracket(idx, K, tp, sl, COSTS["base"])
                if b is None or b["n"] < MIN_N: continue
                grid.append({"rule": name, "tp": tp, "sl": sl, **b})
                if b["lo_mean"] > 0 or b["hi_mean"] > 0:
                    print(f"    {name:26} {tp:5.1f} {sl:5.1f} {b['n']:7,} "
                          f"{b['lo_mean']:+10.4f} {b['hi_mean']:+10.4f} {b['lo_win']*100:5.1f}% {b['t_day_lo']:7.2f}")
    pos_grid = [g for g in grid if g["lo_mean"] > 0]
    print(f"\n  보수(하한) 기준 양수인 TP/SL 조합: {len(pos_grid)} / {len(grid)}")
    report["bracket_grid"] = grid

    json.dump(report, open(os.path.join(OUT, "sweep_report.json"), "w"),
              indent=1, default=float)
    print(f"\n→ {OUT}/sweep_report.json")


if __name__ == "__main__":
    main()
