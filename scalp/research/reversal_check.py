# -*- coding: utf-8 -*-
"""reversal_check.py — 반전 신호 독립 검증 (다른 세션의 초봉 결론을 1분봉에서 재검정).

배경
----
초봉(1초) 연구에서 "하락 직후 되돌림" 셀 21개가 OOS를 통과했으나
체결가정을 현실화하자 전부 소멸했다 (호가 바운스 착시 + DOGE 76~89% 집중).

1분봉 전수 스윕(29종목·191만 표본)에서도 같은 방향이 나왔다:
  ret1 d_up=-0.183 / d_dn=+0.187 (gap -0.370), ret3·ret5·body 동일 패턴.

**해상도가 다르면 호가 바운스의 영향도 다르다.** 1초 종가는 직전 체결 1건이라
매수호가/매도호가 사이를 그대로 튀지만, 1분 종가는 그 분의 마지막 체결이고
진입은 다음 분의 시가다 = 바운스가 훨씬 희석된다.
따라서 1분봉에서도 죽으면 "바운스 착시"가 아니라 **반전 자체가 없다**는 더 강한 결론이고,
1분봉에서 살면 초봉 결론이 해상도 artifact였다는 뜻이다. 어느 쪽이든 정보가 있다.

세 가지 체결 가정 (초봉 연구와 같은 축)
  · 낙관 opt : 매수 = 신호봉 종가        / 매도 = 종료봉 종가
  · 중간 mid : 매수 = 다음 봉 시가        / 매도 = 종료봉 종가   ← sweep.py 기본값
  · 보수 con : 매수 = 다음 봉 **고가**    / 매도 = 종료봉 **저가**

추가 검정: 종목 집중도 (최다 기여 종목 제외 후 생존하는가) — ARCHIVE.md:76 패턴
"""
import os, sys, json, math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect, features as FT

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
COSTS = {"base": 0.15, "measured": 0.217, "stress": 0.30}   # 0.217 = 초봉 연구 실측 왕복
HORIZONS = [1, 2, 5, 10]
# 반전 임계 후보 = train 하위 분위수 규칙 (관찰 cutpoint 동결 아님)
QUANTILES = [0.10, 0.05, 0.01, 0.005, 0.001]
FEATS = ["ret1", "ret3", "ret5", "ret15"]
MIN_N = 300


def build(market):
    """분 격자 → 반전 검정에 필요한 배열만."""
    rec = collect.load(market)
    if rec is None or len(rec["t"]) < 5000:
        return None
    o, h, l, c, v, present, base = FT._grid(rec)
    n = len(c)
    eps = 1e-12
    F = {}
    for k in (1, 3, 5, 15):
        r = np.full(n, np.nan)
        r[k:] = c[k:] / np.maximum(c[:-k], eps) - 1.0
        F[f"ret{k}"] = r
    return {"o": o, "h": h, "l": l, "c": c, "present": present, "n": n, "F": F,
            "t0": base}


def outcomes(D, hz):
    """세 체결가정별 총수익률(%) 배열. 진입 시점 = 신호봉 t."""
    o, h, l, c, n = D["o"], D["h"], D["l"], D["c"], D["n"]
    eps = 1e-12
    opt = np.full(n, np.nan); mid = np.full(n, np.nan); con = np.full(n, np.nan)
    m = n - hz - 1
    if m <= 0:
        return opt, mid, con
    opt[:m] = c[1 + hz:1 + hz + m] / np.maximum(c[:m], eps) - 1.0        # 종가→종가
    mid[:m] = c[1 + hz:1 + hz + m] / np.maximum(o[1:1 + m], eps) - 1.0   # 다음시가→종가
    con[:m] = l[1 + hz:1 + hz + m] / np.maximum(h[1:1 + m], eps) - 1.0   # 다음고가→종료저가
    return opt * 100.0, mid * 100.0, con * 100.0


def dedupe_mask(idx, hz):
    """겹침 제거: 직전 채택 이후 hz분 이내 트리거 버림 (단일 마켓, idx 오름차순)."""
    keep = []; last = -10**9
    for i in idx:
        if i - last >= hz:
            keep.append(i); last = i
    return np.array(keep, dtype=np.int64)


def main():
    os.makedirs(OUT, exist_ok=True)
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "min1")
    markets = sorted(f[:-8] for f in os.listdir(src) if f.endswith(".json.gz"))
    print(f"[rev] {len(markets)} markets 로드 중...", flush=True)
    D = {}
    for m in markets:
        d = build(m)
        if d is not None:
            D[m] = d
    print(f"[rev] {len(D)} markets · 반전 그리드 {len(FEATS)}피처 × {len(QUANTILES)}분위 × "
          f"{len(HORIZONS)}horizon = {len(FEATS)*len(QUANTILES)*len(HORIZONS)}셀", flush=True)

    # train/test 시간분할 (각 마켓 격자의 앞 70% / 뒤 30%)
    report = []
    print(f"\n{'조건':<18} {'h':>3} {'n_tr':>7} {'n_te':>7} "
          f"{'낙관_te':>9} {'중간_te':>9} {'보수_te':>9} {'승률_보수':>9} "
          f"{'최다종목':>12} {'비중':>6} {'제외후_중간':>11} {'판정':>8}")
    print("-" * 130)
    for feat in FEATS:
        for qq in QUANTILES:
            # train 전 마켓 pooled 분위수 (마켓별로 스케일이 달라 pooled가 타당)
            pool = []
            for m, d in D.items():
                col = d["F"][feat]; cut = int(d["n"] * 0.70)
                s = col[:cut][np.isfinite(col[:cut])]
                pool.append(s)
            allv = np.concatenate(pool)
            thr = float(np.quantile(allv, qq))
            for hz in HORIZONS:
                rows = {"opt": [], "mid": [], "con": []}; mk_of = []
                n_tr = 0
                for m, d in D.items():
                    col = d["F"][feat]
                    opt, mid, con = outcomes(d, hz)
                    ok = (np.isfinite(col) & np.isfinite(opt) & np.isfinite(mid)
                          & np.isfinite(con) & d["present"])
                    ok[1:] &= d["present"][:-1]      # 다음 봉 체결 존재 (t+1 진입 가능)
                    hit = np.nonzero(ok & (col <= thr))[0]
                    if len(hit) == 0:
                        continue
                    sel = dedupe_mask(hit, hz)
                    cut = int(d["n"] * 0.70)
                    n_tr += int((sel < cut).sum())
                    te = sel[sel >= cut]
                    if len(te) == 0:
                        continue
                    rows["opt"].append(opt[te]); rows["mid"].append(mid[te])
                    rows["con"].append(con[te]); mk_of += [m] * len(te)
                if not rows["mid"]:
                    continue
                R = {k: np.concatenate(v) for k, v in rows.items()}
                mk = np.array(mk_of)
                n_te = len(R["mid"])
                if n_te < MIN_N or n_tr < MIN_N:
                    continue
                cost = COSTS["base"]
                net = {k: R[k] - cost for k in R}
                # 종목 집중도
                uniq, cnt = np.unique(mk, return_counts=True)
                top = uniq[cnt.argmax()]; share = cnt.max() / n_te
                excl = net["mid"][mk != top]
                excl_mean = float(excl.mean()) if len(excl) >= 30 else float("nan")
                verdict = ("생존" if (net["con"].mean() > 0 and excl_mean > 0)
                           else ("불확정" if net["mid"].mean() > 0 else "소멸"))
                rec = {"feature": feat, "q": qq, "thr": thr, "h": hz,
                       "n_train": n_tr, "n_test": n_te,
                       "opt": float(net["opt"].mean()), "mid": float(net["mid"].mean()),
                       "con": float(net["con"].mean()),
                       "win_con": float((net["con"] > 0).mean()),
                       "top_market": str(top), "top_share": float(share),
                       "excl_mid": excl_mean, "verdict": verdict}
                report.append(rec)
                print(f"{feat+'<=P'+str(qq*100):<18} {hz:>3} {n_tr:>7,} {n_te:>7,} "
                      f"{rec['opt']:>+9.4f} {rec['mid']:>+9.4f} {rec['con']:>+9.4f} "
                      f"{rec['win_con']*100:>8.1f}% {top:>12} {share:>6.2f} "
                      f"{excl_mean:>+11.4f} {verdict:>8}")

    alive = [r for r in report if r["verdict"] == "생존"]
    mid_pos = [r for r in report if r["mid"] > 0]
    print("\n" + "=" * 130)
    print(f"검정 셀 {len(report)}개 · 중간가정 양수 {len(mid_pos)}개 · "
          f"보수가정+종목분산 동시통과(생존) {len(alive)}개")
    if not alive:
        print("  ✗ 생존 0 — 1분봉 해상도에서도 반전 신호는 채택 불가.")
        print("    초봉 연구의 '호가 바운스 착시' 판정과 독립적으로 일치.")
    else:
        for r in sorted(alive, key=lambda x: -x["con"])[:10]:
            print(f"  · {r['feature']}<=P{r['q']*100} h={r['h']} 보수 {r['con']:+.4f}%p "
                  f"(n={r['n_test']:,}, 최다 {r['top_market']} {r['top_share']:.2f})")
    json.dump(report, open(os.path.join(OUT, "reversal_check.json"), "w"), indent=1)
    print(f"\n→ {OUT}/reversal_check.json")


if __name__ == "__main__":
    main()
