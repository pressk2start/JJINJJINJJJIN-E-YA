"""
Ceiling Analysis: CLM 전략의 이론적 상한.

핵심 질문에 답함:
  1. Perfect Exit: MFE 정점 청산 시 최대 수익은?
  2. Perfect Entry: 하위 N% 제거 시 얼마나 개선?
  3. Perfect Both: 최적 진입 + 최적 청산의 이론적 상한

이 숫자가 전략의 방향을 결정함:
  - Perfect Exit < +0.15% → 알파 자체가 없음 → 새 전략 필요
  - Perfect Exit +0.15~0.30% → 얇지만 존재 → EC/PP로 방어 가능
  - Perfect Exit > +0.30% → 청산 최적화 여력 큼
"""
import numpy as np
import pandas as pd


def ceiling_analysis(events_df):
    """Ceiling 분석 결과 dict 반환"""
    n = len(events_df)
    if n == 0:
        return {"error": "no events", "n_events": 0}

    mfe = events_df["max_mfe"].values
    mae = events_df["max_mae"].values
    final = events_df["final_pnl"].values

    # --- 1. 현재 성과 (AT 없이 300초 후 청산 기준) ---
    cur = {
        "avg_pnl": np.mean(final),
        "median_pnl": np.median(final),
        "winrate": np.mean(final > 0) * 100,
        "std_pnl": np.std(final),
    }

    # --- 2. Perfect Exit: MFE 정점 청산 ---
    pe = {
        "avg_mfe": np.mean(mfe),
        "median_mfe": np.median(mfe),
        "p25_mfe": np.percentile(mfe, 25),
        "p75_mfe": np.percentile(mfe, 75),
        "rm_ratio": np.mean(final) / np.mean(mfe) * 100 if np.mean(mfe) > 0 else 0,
    }

    # --- 3. Perfect Entry: 하위 N% 제거 ---
    pi = {}
    for pct in [10, 20, 30, 40, 50]:
        thr = np.percentile(final, pct)
        filt = final[final >= thr]
        mfe_filt = mfe[final >= thr]
        pi[pct] = {
            "n": len(filt),
            "avg_pnl": np.mean(filt),
            "median_pnl": np.median(filt),
            "winrate": np.mean(filt > 0) * 100,
            "avg_mfe": np.mean(mfe_filt),
        }

    # --- 4. Perfect Both: 하위 30% 제거 + MFE 청산 ---
    mask70 = final >= np.percentile(final, 30)
    pb = {
        "avg_mfe_top70": np.mean(mfe[mask70]),
        "median_mfe_top70": np.median(mfe[mask70]),
    }

    # --- 5. MFE 분포 ---
    mfe_dist = {f"p{p}": np.percentile(mfe, p) for p in [10, 25, 50, 75, 90]}

    # --- 6. MAE 분포 ---
    mae_dist = {f"p{p}": np.percentile(mae, p) for p in [10, 25, 50, 75, 90]}

    # --- 7. 시간별 MFE/PnL (숫자 기준 정렬) ---
    time_cols = {}
    for prefix in ["mfe", "pnl", "mae"]:
        cols = [c for c in events_df.columns if c.startswith(f"{prefix}_") and c[len(prefix)+1:].isdigit()]
        cols = sorted(cols, key=lambda x: int(x.split("_")[1]))
        if cols:
            time_cols[prefix] = {c: events_df[c].mean() for c in cols}

    # --- 8. 승/패 분리 ---
    winners = final[final > 0]
    losers = final[final <= 0]
    wl = {
        "win_n": len(winners),
        "loss_n": len(losers),
        "win_avg": np.mean(winners) if len(winners) > 0 else 0,
        "loss_avg": np.mean(losers) if len(losers) > 0 else 0,
        "win_avg_mfe": np.mean(mfe[final > 0]) if len(winners) > 0 else 0,
        "loss_avg_mfe": np.mean(mfe[final <= 0]) if len(losers) > 0 else 0,
    }

    return {
        "n_events": n,
        "current": cur,
        "perfect_exit": pe,
        "perfect_entry": pi,
        "perfect_both": pb,
        "mfe_dist": mfe_dist,
        "mae_dist": mae_dist,
        "time_series": time_cols,
        "win_loss": wl,
    }


def format_report(result):
    """텍스트 리포트 생성"""
    if "error" in result:
        return f"❌ {result['error']}"

    def r(v, d=4):
        return round(v, d)

    lines = []
    n = result["n_events"]
    c = result["current"]
    pe = result["perfect_exit"]
    pb = result["perfect_both"]
    wl = result["win_loss"]

    lines.append(f"{'='*55}")
    lines.append(f"  CLM Ceiling Analysis  (n={n:,})")
    lines.append(f"{'='*55}")
    lines.append("")

    # 현재 성과
    lines.append(f"[현재 성과] (300초 후 청산 기준)")
    lines.append(f"  평균 PnL:  {c['avg_pnl']:+.4f}%")
    lines.append(f"  중앙값:    {c['median_pnl']:+.4f}%")
    lines.append(f"  승률:      {c['winrate']:.1f}%")
    lines.append(f"  표준편차:  {c['std_pnl']:.4f}%")
    lines.append(f"  승: {wl['win_n']}건 avg {wl['win_avg']:+.4f}%  |  패: {wl['loss_n']}건 avg {wl['loss_avg']:+.4f}%")
    lines.append("")

    # Perfect Exit
    lines.append(f"[Perfect Exit] MFE 정점 청산")
    lines.append(f"  평균 MFE:  {pe['avg_mfe']:+.4f}%")
    lines.append(f"  중앙값:    {pe['median_mfe']:+.4f}%")
    lines.append(f"  p25~p75:   {pe['p25_mfe']:+.4f}% ~ {pe['p75_mfe']:+.4f}%")
    lines.append(f"  r/m ratio: {pe['rm_ratio']:.1f}%")
    lines.append(f"  승 MFE avg: {wl['win_avg_mfe']:+.4f}%  |  패 MFE avg: {wl['loss_avg_mfe']:+.4f}%")
    lines.append("")

    # Perfect Entry
    lines.append(f"[Perfect Entry] 하위 제거 시")
    for pct, v in sorted(result["perfect_entry"].items()):
        lines.append(f"  하위{pct:>2}% 제거: n={v['n']:>5}  avg={v['avg_pnl']:+.4f}%  wr={v['winrate']:.1f}%  mfe={v['avg_mfe']:+.4f}%")
    lines.append("")

    # Perfect Both
    lines.append(f"[Perfect Both] 하위30% 제거 + MFE 청산")
    lines.append(f"  평균 MFE:  {pb['avg_mfe_top70']:+.4f}%")
    lines.append(f"  중앙값:    {pb['median_mfe_top70']:+.4f}%")
    lines.append("")

    # MFE/MAE 분포
    mfe_str = "  ".join(f"{k}={v:+.4f}%" for k, v in result["mfe_dist"].items())
    lines.append(f"[MFE 분포]  {mfe_str}")
    mae_str = "  ".join(f"{k}={v:+.4f}%" for k, v in result["mae_dist"].items())
    lines.append(f"[MAE 분포]  {mae_str}")
    lines.append("")

    # 시간별
    ts = result.get("time_series", {})
    if "mfe" in ts:
        lines.append(f"[시간별 평균 MFE]")
        for col, val in ts["mfe"].items():
            sec = col.split("_")[1]
            lines.append(f"  {sec:>3}s: {val:+.4f}%")
    if "pnl" in ts:
        lines.append(f"[시간별 평균 PnL]")
        for col, val in ts["pnl"].items():
            sec = col.split("_")[1]
            lines.append(f"  {sec:>3}s: {val:+.4f}%")
    lines.append("")

    # 결론
    lines.append(f"{'─'*55}")
    avg_mfe = pe["avg_mfe"]
    if avg_mfe < 0.15:
        lines.append(f"⚠️  Perfect Exit {avg_mfe:+.4f}% (<0.15%)")
        lines.append(f"    → CLM 알파 자체가 매우 얇음. 청산 최적화 무의미.")
        lines.append(f"    → 새 진입 로직 필요.")
    elif avg_mfe < 0.30:
        lines.append(f"🔸 Perfect Exit {avg_mfe:+.4f}% (0.15~0.30%)")
        lines.append(f"    → 알파 존재하나 얇음. EC/PP 수준 개선으로 손익분기 돌파 가능.")
    elif avg_mfe < 0.50:
        lines.append(f"🟢 Perfect Exit {avg_mfe:+.4f}% (0.30~0.50%)")
        lines.append(f"    → 청산 최적화 여력 있음. r/m {pe['rm_ratio']:.0f}%→50% 목표.")
    else:
        lines.append(f"✅ Perfect Exit {avg_mfe:+.4f}% (>0.50%)")
        lines.append(f"    → 충분한 알파. r/m {pe['rm_ratio']:.0f}%→40~50% 달성 시 큰 개선.")
    lines.append(f"{'─'*55}")

    return "\n".join(lines)
