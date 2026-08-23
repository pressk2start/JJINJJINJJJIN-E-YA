"""calibrate.py — 수집된 프레임으로 임계치를 **실측**에서 도출한다.

왜 필요한가:
  obi_min=0.25 · vp_min=130 같은 숫자를 손으로 정하면 그건 근거 없는 추측이다.
  이 스크립트는 실제 호가·체결 분포를 재고, 조건부 forward return을 붙여서
  "이 임계 위에서 실제로 뭐가 달라지는가"를 숫자로 보여준다.

출력 3부:
  [1] 비용 바닥   — 즉시 왕복 비용(스프레드+호가소진+수수료+지연). 이걸 못 넘으면 전략 자체가 성립 안 함.
  [2] 분포        — spread/obi/vp/depth/틱 분위수. 임계 후보의 사전(prior).
  [3] 조건부 성과 — OBI·VP 버킷별 forward return(비용 차감 후). 임계치 제안의 유일한 근거.

⚠ 이 결과는 **탐색(discovery)** 용이다. 여기서 고른 임계치로 같은 데이터의 성과를 주장하면
   in-sample 과적합이다. 봉인 후 새로 수집한 forward 구간에서만 성과를 말할 것.

사용:
  python3 calibrate.py data/calib_run1.jsonl.gz
  python3 calibrate.py data/*.jsonl.gz --notional 300000 --horizons 30,60,120
"""
import os, sys, glob, json, math, argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__) or ".")
import scalp as SC


def q(xs, p):
    """분위수 (선형보간 없음 — 표본이 작을 때 오해를 만들지 않도록 nearest-rank)."""
    if not xs:
        return float("nan")
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[i]


def fmt(x, n=1):
    return "n/a" if (x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))) else f"{x:,.{n}f}"


def immediate_roundtrip_bp(f, notional, fee_bp, latency_bp):
    """지금 사서 지금 파는 왕복 비용(bp, 양수=손실). 알파가 이 값을 넘어야 이익이 난다."""
    mid = f.get("mid") or 0.0
    if mid <= 0:
        return float("inf")
    bv, _, bf = SC.vwap_fill(f.get("asks") or [], notional, mid)
    sv, _, sf = SC.vwap_fill(f.get("bids") or [], notional, mid)
    if bv <= 0 or sv <= 0 or bf < notional * 0.999 or sf < notional * 0.999:
        return float("inf")
    buy = bv * (1 + latency_bp / 1e4)
    sell = sv * (1 - latency_bp / 1e4)
    return -((sell / buy - 1.0) * 1e4 - 2.0 * fee_bp)


def forward_net_bp(frames, i, horizon, notional, fee_bp, latency_bp):
    """i번째 프레임에서 진입해 horizon초 뒤 첫 프레임에서 청산했을 때 순손익(bp).
    horizon 안에 프레임이 더 없으면 None (마지막 구간을 0으로 채우면 결과가 왜곡된다)."""
    f0 = frames[i]
    t_target = f0["ts"] + horizon
    j = None
    for k in range(i + 1, len(frames)):
        if frames[k]["ts"] >= t_target:
            j = k
            break
    if j is None:
        return None
    cfg = {"notional_krw": notional, "fee_bp": fee_bp, "latency_bp": latency_bp}
    entry = SC.fill_buy(f0, cfg)
    exit_ = SC.fill_sell(frames[j], cfg)
    if entry <= 0 or exit_ <= 0:
        return None
    return SC.net_bp(entry, exit_, cfg)


def bucket_report(rows, key, edges, horizon_label):
    """rows = [(key_value, net_bp)] → 구간별 n / 승률 / 평균 / 중앙값."""
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = [r for kv, r in rows if lo <= kv < hi]
        if not sel:
            out.append((f"[{fmt(lo,2)}, {fmt(hi,2)})", 0, None, None, None))
            continue
        wins = sum(1 for x in sel if x > 0)
        out.append((f"[{fmt(lo,2)}, {fmt(hi,2)})", len(sel), wins / len(sel),
                    sum(sel) / len(sel), q(sel, 0.5)))
    print(f"\n  -- {key} 버킷별 {horizon_label} 순손익(bp, 비용차감후) --")
    print(f"     {'구간':>18} {'n':>6} {'승률':>7} {'평균bp':>9} {'중앙bp':>9}")
    for name, n, wr, mean, med in out:
        print(f"     {name:>18} {n:>6} {fmt(wr,3) if wr is not None else '-':>7} "
              f"{fmt(mean,2) if mean is not None else '-':>9} {fmt(med,2) if med is not None else '-':>9}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--notional", type=float, default=SC.DEFAULT_NOTIONAL_KRW)
    ap.add_argument("--fee-bp", type=float, default=SC.DEFAULT_FEE_BP)
    ap.add_argument("--latency-bp", type=float, default=SC.DEFAULT_LATENCY_BP)
    ap.add_argument("--horizons", default="30,60,120")
    a = ap.parse_args()

    files = []
    for p in a.paths:
        files += sorted(glob.glob(p))
    frames = []
    for p in files:
        frames += SC.load_frames(p)
    if not frames:
        print("프레임 없음 — 먼저 collect_frames.py 로 수집하세요."); sys.exit(1)

    by_m = SC.split_by_market(frames)
    span = max(f["ts"] for f in frames) - min(f["ts"] for f in frames)
    gaps = []
    for m, fs in by_m.items():
        gaps += [fs[i]["ts"] - fs[i - 1]["ts"] for i in range(1, len(fs))]
    print("=" * 78)
    print(f"CALIBRATION · frames={len(frames)} · markets={len(by_m)} · 구간={span/60:.1f}분 "
          f"· 유효해상도(중앙 프레임간격)={fmt(q(gaps,0.5),1)}s")
    print(f"기준 주문금액={a.notional:,.0f}원 · 수수료 편도 {a.fee_bp}bp · 지연 편도 {a.latency_bp}bp")
    print("=" * 78)

    # ---------- [1] 비용 바닥 ----------
    print("\n[1] 즉시 왕복 비용 (지금 사서 지금 팔 때 잃는 bp) — 알파가 이걸 넘어야 함")
    print(f"    {'market':<12} {'n':>5} {'p10':>8} {'p50':>8} {'p90':>8} {'전액체결가능':>10}")
    pooled_cost = []
    for m, fs in sorted(by_m.items()):
        cs = [immediate_roundtrip_bp(f, a.notional, a.fee_bp, a.latency_bp) for f in fs]
        fin = [c for c in cs if math.isfinite(c)]
        pooled_cost += fin
        dr = len(fin) / len(cs) if cs else 0.0
        print(f"    {m:<12} {len(fs):>5} {fmt(q(fin,0.1),1):>8} {fmt(q(fin,0.5),1):>8} "
              f"{fmt(q(fin,0.9),1):>8} {fmt(dr,2):>10}")
    print(f"    {'POOLED':<12} {len(pooled_cost):>5} {fmt(q(pooled_cost,0.1),1):>8} "
          f"{fmt(q(pooled_cost,0.5),1):>8} {fmt(q(pooled_cost,0.9),1):>8}")

    # ---------- [2] 분포 ----------
    print("\n[2] 특징량 분포 (POOLED)")
    feats = {
        "spread_bp": [f["spread_bp"] for f in frames if math.isfinite(f["spread_bp"])],
        "obi": [f["obi"] for f in frames],
        "vp": [f["vp"] for f in frames],
        "n_tick": [f["n_tick"] for f in frames],
        "tick_value": [f["tick_value"] for f in frames],
    }
    print(f"    {'feature':<12} {'p05':>10} {'p25':>10} {'p50':>10} {'p75':>10} {'p95':>10}")
    for k, v in feats.items():
        nd = 0 if k in ("n_tick", "tick_value") else 2
        print(f"    {k:<12} {fmt(q(v,0.05),nd):>10} {fmt(q(v,0.25),nd):>10} {fmt(q(v,0.5),nd):>10} "
              f"{fmt(q(v,0.75),nd):>10} {fmt(q(v,0.95),nd):>10}")

    # ---------- [3] 조건부 성과 ----------
    horizons = [float(h) for h in a.horizons.split(",") if h.strip()]
    suggestions = {}
    for H in horizons:
        rows_obi = []; rows_vp = []; rows_sp = []
        for m, fs in by_m.items():
            for i, f in enumerate(fs):
                nb = forward_net_bp(fs, i, H, a.notional, a.fee_bp, a.latency_bp)
                if nb is None:
                    continue
                rows_obi.append((f["obi"], nb))
                rows_vp.append((min(f["vp"], 400.0), nb))
                if math.isfinite(f["spread_bp"]):
                    rows_sp.append((f["spread_bp"], nb))
        if not rows_obi:
            print(f"\n[3] horizon {H:.0f}s — 표본 없음 (수집 구간이 짧음)")
            continue
        allbp = [x for _, x in rows_obi]
        print(f"\n[3] horizon {H:.0f}s · 표본={len(allbp)} · 무조건 평균={fmt(sum(allbp)/len(allbp),2)}bp "
              f"(= 아무 때나 진입했을 때의 기준선)")
        bo = bucket_report(rows_obi, "OBI", [-1.01, -0.3, -0.1, 0.1, 0.3, 0.6, 1.01], f"{H:.0f}s")
        bv = bucket_report(rows_vp, "VP(체결강도)", [0, 80, 100, 130, 180, 260, 401], f"{H:.0f}s")
        bs = bucket_report(rows_sp, "spread_bp", [0, 3, 6, 10, 20, 50, 1e9], f"{H:.0f}s")
        best_o = max((r for r in bo if r[1] >= 20), key=lambda r: r[3], default=None)
        best_v = max((r for r in bv if r[1] >= 20), key=lambda r: r[3], default=None)
        suggestions[H] = (best_o, best_v)

    # ---------- 요약 ----------
    print("\n" + "=" * 78)
    print("요약 / 임계치 제안 (표본 20 이상 버킷 중 평균 최대. **탐색용**, 성과 주장 근거 아님)")
    print("=" * 78)
    med_cost = q(pooled_cost, 0.5)
    print(f"  · 즉시 왕복 비용 중앙값 = {fmt(med_cost,1)}bp → tp_bp 는 최소 이 값보다 커야 함")
    for H, (bo, bv) in suggestions.items():
        so = f"OBI {bo[0]} (n={bo[1]}, 평균 {fmt(bo[3],2)}bp)" if bo else "OBI 표본부족"
        sv = f"VP {bv[0]} (n={bv[1]}, 평균 {fmt(bv[3],2)}bp)" if bv else "VP 표본부족"
        print(f"  · horizon {H:.0f}s → {so} / {sv}")
    print("\n  ⚠ 어떤 버킷의 평균도 비용을 넘지 못하면, 이 데이터에서 해당 축의 엣지는 '없다'가 결론이다.")
    print("     그 경우 임계치를 더 조이는 게 아니라 전략 축 자체를 재검토할 것.")


if __name__ == "__main__":
    main()
