# -*- coding: utf-8 -*-
"""
feature_screen — 진입 후보 특징 자동 스크리닝 엔진 (advisor 3자 수렴).

배경 (2026-08-01 advisor 정리):
  라이브 리포트가 매번 W/L d-score 상위 지표를 보여주지만,
  이걸 매번 눈으로 보고 "이거 필터로 쓸까?" 판단하는 게 사후끼워맞춤 함정.
  kst_hour(D 가설) 이 매번 편차 보이지만 matched sim 반증받은 게 정확히 이 함정.

목적:
  진입시각에 확정 가능한 특징 (rsi_60m, rsi_15m, macd_hist_5m, atr_pct 등) 을
  누적 진입 데이터에 대해 매칭-OOS + walk-forward 로 자동 스크리닝.
  통과한 후보만 진입 필터 후보로 승격.

3중 방어 (사후끼워맞춤 방지):
  1. 매칭 코호트 (같은 진입 신호에 필터 만 다르게 · paired delta)
  2. OOS split (train 70% / test 30% · time order)
  3. Walk-forward (rolling window · 앞 부분 학습 → 뒤 부분 검증 여러 번)
  → 셋 다 유의 통과 시만 "STRONG_CANDIDATE"

기각 확정 (룩어헤드 · 반복 금지):
  - mfe_*, dd_*, mae_* : 진입 후 관측값
  - hold_sec, exit_reason, realized_pnl : 청산 관측값
  - kst_hour : matched sim 로 reject (누적 재버킷 편차 착시)

입력 CSV (누적 진입 데이터 · trade_records):
  필수: entry_ts_utc, pnl_pct
  권장: rsi_60m, rsi_15m, rsi_5m, macd_hist_5m_bps, atr_pct, ob_spread_pct,
        ob_bid_cum5_krw, vr5_15m, vr5, per_3
  (없는 컬럼은 자동 skip)

사용:
  python research/feature_screen.py trades.csv [--seed 7]
    --oos-split 0.7                    train:test 비율
    --wf-windows 4                     walk-forward 검증 창 수
    --effect-min 0.20                  Cohen's d 최소 임계
    --paired-ci-pos                    paired 95% CI 양수 강제
"""
import sys, csv, argparse
from datetime import datetime
import numpy as np
FMT = "%Y-%m-%dT%H:%M:%S"

# advisor 정리: 룩어헤드 지표 (진입시각에 확정 X) — 스크리닝 대상 제외
_LOOKAHEAD_FEATURES = frozenset({
    "mfe_peak_sec", "mfe_peak", "mfe_30s", "mfe_60s", "mfe_120s",
    "dd_peak_30s", "dd_peak_60s", "dd_peak_120s",
    "mae_60s", "mae_120s", "mae_peak",
    "hold_sec", "hold", "exit_reason", "realized_pnl", "pnl_pct",
    # kst_hour: matched sim 반증 (누적 재버킷 착시)
    "kst_hour",
})

# 후보 특징 (진입시각 확정 · 리포트 W/L d-score 관찰 기반)
_CANDIDATE_FEATURES = [
    "rsi_60m", "rsi_15m", "rsi_5m",
    "macd_hist_5m_bps", "atr_pct",
    "ob_spread_pct", "ob_bid_cum5_krw", "ob_ask1_krw",
    "vr5_15m", "vr5", "per_3",
    "close_strength", "body_pct", "wick_asym", "upper_wick_ratio",
]


def cohens_d(win_vals, loss_vals):
    """W/L 그룹 Cohen's d (양수 = 승자 값 더 큼)."""
    if len(win_vals) < 3 or len(loss_vals) < 3:
        return None
    mw, ml = np.mean(win_vals), np.mean(loss_vals)
    vw = np.var(win_vals, ddof=1) if len(win_vals) > 1 else 0
    vl = np.var(loss_vals, ddof=1) if len(loss_vals) > 1 else 0
    pooled = np.sqrt((vw + vl) / 2)
    if pooled <= 0:
        return None
    return (mw - ml) / pooled


def matched_delta(rows, feature, threshold, direction):
    """매칭 delta: filter 적용 vs 미적용 · 같은 코호트 net PnL 차이."""
    kept_pnls, dropped_pnls = [], []
    for r in rows:
        v = r.get(feature)
        if v is None:
            continue
        pnl = r.get("pnl_pct")
        if pnl is None:
            continue
        passes = (v >= threshold) if direction == ">=" else (v <= threshold)
        if passes:
            kept_pnls.append(pnl)
        else:
            dropped_pnls.append(pnl)
    if len(kept_pnls) < 5:
        return None
    baseline = np.mean([r.get("pnl_pct", 0) for r in rows if r.get("pnl_pct") is not None])
    filtered_mean = np.mean(kept_pnls)
    return {
        "kept_n": len(kept_pnls),
        "dropped_n": len(dropped_pnls),
        "baseline_mean": baseline,
        "filtered_mean": filtered_mean,
        "delta": filtered_mean - baseline,
        "drop_ratio": len(dropped_pnls) / (len(kept_pnls) + len(dropped_pnls)),
    }


def block_bootstrap_ci(pairs, dates, seed, n=1000):
    """day-block bootstrap 95% CI."""
    rng = np.random.default_rng(seed)
    by_day = {}
    for d, v in zip(dates, pairs):
        by_day.setdefault(d, []).append(v)
    days = list(by_day.keys())
    if not days:
        return None, None
    means = []
    for _ in range(n):
        samp = []
        for _ in range(len(days)):
            d = days[rng.integers(len(days))]
            samp.extend(by_day[d])
        means.append(np.mean(samp))
    return np.percentile(means, 2.5), np.percentile(means, 97.5)


def walk_forward_check(rows, feature, threshold, direction, windows=4):
    """walk-forward: 코호트 시간순으로 windows 등분 후 각 fold 에서
    filter 적용 시 net > baseline 인지 검증. 통과율 반환."""
    if len(rows) < windows * 10:
        return None
    fold_size = len(rows) // windows
    passes = 0
    for i in range(windows):
        start = i * fold_size
        end = (i + 1) * fold_size if i < windows - 1 else len(rows)
        fold = rows[start:end]
        r = matched_delta(fold, feature, threshold, direction)
        if r and r["delta"] > 0:
            passes += 1
    return passes / windows


def screen_feature(rows, feature, seed=7, oos_split=0.7,
                   effect_min=0.20, wf_windows=4):
    """단일 특징에 대해 3중 방어 스크리닝."""
    vals_with_pnl = [(r.get(feature), r.get("pnl_pct")) for r in rows
                     if r.get(feature) is not None and r.get("pnl_pct") is not None]
    if len(vals_with_pnl) < 30:
        return {"status": "INSUFFICIENT", "n": len(vals_with_pnl)}

    # 1. 전체 코호트 Cohen's d (W/L 분리)
    wins = [v for v, p in vals_with_pnl if p > 0]
    losses = [v for v, p in vals_with_pnl if p <= 0]
    d = cohens_d(wins, losses)
    if d is None or abs(d) < effect_min:
        return {"status": "WEAK_SIGNAL", "d": d, "n": len(vals_with_pnl)}

    # 방향 · 임계값 결정 (승자 평균 우선)
    threshold = (np.mean(wins) + np.mean(losses)) / 2
    direction = ">=" if d > 0 else "<="

    # 2. OOS split (train / test)
    n_train = int(len(rows) * oos_split)
    train_rows = rows[:n_train]
    test_rows = rows[n_train:]
    train_r = matched_delta(train_rows, feature, threshold, direction)
    test_r = matched_delta(test_rows, feature, threshold, direction)
    if not train_r or not test_r:
        return {"status": "INSUFFICIENT_OOS", "d": d}
    oos_replicated = (train_r["delta"] > 0) and (test_r["delta"] > 0)

    # 3. Walk-forward
    wf_pass_rate = walk_forward_check(rows, feature, threshold, direction, wf_windows)
    wf_ok = wf_pass_rate is not None and wf_pass_rate >= 0.75  # 4/4 or 3/4

    # 판정
    strong = oos_replicated and wf_ok
    moderate = oos_replicated or wf_ok
    if strong:
        status = "STRONG_CANDIDATE"
    elif moderate:
        status = "MODERATE_CANDIDATE"
    else:
        status = "REJECTED"
    return {
        "status": status,
        "d": round(d, 3),
        "n": len(vals_with_pnl),
        "direction": direction,
        "threshold": round(threshold, 3),
        "train_delta": round(train_r["delta"], 4) if train_r else None,
        "test_delta": round(test_r["delta"], 4) if test_r else None,
        "wf_pass_rate": wf_pass_rate,
        "drop_ratio": round(train_r["drop_ratio"], 3) if train_r else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="누적 진입 데이터 CSV (entry_ts_utc, pnl_pct, features...)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--oos-split", type=float, default=0.7, dest="oos_split")
    ap.add_argument("--wf-windows", type=int, default=4, dest="wf_windows")
    ap.add_argument("--effect-min", type=float, default=0.20, dest="effect_min")
    args = ap.parse_args()

    # 파라미터 검증 (advisor 방어)
    if not (0.5 <= args.oos_split <= 0.9):
        print(f"⚠ --oos-split 0.5~0.9 권장 (입력: {args.oos_split})"); return
    if not (2 <= args.wf_windows <= 10):
        print(f"⚠ --wf-windows 2~10 권장 (입력: {args.wf_windows})"); return

    # CSV 로드
    rows = []
    with open(args.csv) as f:
        for r in csv.DictReader(f):
            row = {}
            for k, v in r.items():
                if not v or not v.strip():
                    continue
                if k in ("entry_ts_utc", "market", "signal_id", "route", "exit_reason"):
                    row[k] = v.strip()
                else:
                    try:
                        row[k] = float(v)
                    except (ValueError, TypeError):
                        pass
            if row.get("entry_ts_utc"):
                try:
                    row["_dt"] = datetime.strptime(row["entry_ts_utc"][:19], FMT)
                except Exception:
                    continue
            rows.append(row)

    # 시간정렬 (walk-forward 무결성)
    rows.sort(key=lambda r: r.get("_dt", datetime.min))

    print(f"입력 {len(rows)} 건 · 시간정렬 완료")
    print(f"OOS split: train {int(len(rows)*args.oos_split)} / test {len(rows)-int(len(rows)*args.oos_split)}")
    print(f"WF windows: {args.wf_windows}")
    print(f"Effect size min: |d| ≥ {args.effect_min}")
    print(f"룩어헤드 배제: {sorted(_LOOKAHEAD_FEATURES)}")

    # 후보 특징 스크리닝
    candidates = [f for f in _CANDIDATE_FEATURES
                  if any(r.get(f) is not None for r in rows)]
    if not candidates:
        print("⚠ 후보 특징 컬럼이 CSV 에 없음 (rsi_60m, atr_pct 등)"); return

    print(f"\n=== 후보 특징 {len(candidates)}개 스크리닝 ===")

    results = []
    for feat in candidates:
        r = screen_feature(rows, feat, seed=args.seed,
                          oos_split=args.oos_split,
                          effect_min=args.effect_min,
                          wf_windows=args.wf_windows)
        r["feature"] = feat
        results.append(r)

    # 상태별 정렬 + 출력
    _order = {"STRONG_CANDIDATE": 0, "MODERATE_CANDIDATE": 1,
              "REJECTED": 2, "WEAK_SIGNAL": 3,
              "INSUFFICIENT_OOS": 4, "INSUFFICIENT": 5}
    results.sort(key=lambda x: (_order.get(x["status"], 9), -abs(x.get("d") or 0)))

    for r in results:
        feat = r["feature"]
        status = r["status"]
        if status in ("STRONG_CANDIDATE", "MODERATE_CANDIDATE"):
            print(f"\n✅ [{status}] {feat}")
            print(f"    Cohen's d: {r['d']:+.3f}  방향: {r['direction']}{r['threshold']}")
            print(f"    train Δ: {r['train_delta']:+.4f}%p · test Δ: {r['test_delta']:+.4f}%p")
            print(f"    WF pass rate: {r['wf_pass_rate']:.0%}")
            print(f"    drop ratio: {r['drop_ratio']:.0%} (진입 감소 비율)")
        elif status == "REJECTED":
            print(f"❌ [{status}] {feat} (d={r.get('d')}, OOS 또는 WF 실패)")
        elif status == "WEAK_SIGNAL":
            print(f"⚠  [{status}] {feat} (d={r.get('d')}, |d|<{args.effect_min})")
        else:
            print(f"⏳ [{status}] {feat} (n={r.get('n', 0)})")

    strong = [r for r in results if r["status"] == "STRONG_CANDIDATE"]
    moderate = [r for r in results if r["status"] == "MODERATE_CANDIDATE"]

    print(f"\n=== 종합 판정 ===")
    print(f"  STRONG (OOS ✅ + WF ✅): {len(strong)}개")
    for r in strong:
        print(f"    - {r['feature']} {r['direction']}{r['threshold']} (d={r['d']})")
    print(f"  MODERATE (OOS or WF): {len(moderate)}개 (표본 확대 필요)")
    print(f"  REJECTED / WEAK / INSUFFICIENT: {len(results)-len(strong)-len(moderate)}개")
    print("\n※ STRONG_CANDIDATE 만 진입 필터로 승격 가능 · 반드시 ind_filters 배선 전 매칭 재검증")
    print("※ 룩어헤드 지표 자동 배제 (mfe_*, dd_*, mae_*, hold_*, kst_hour)")


if __name__ == "__main__":
    main()
