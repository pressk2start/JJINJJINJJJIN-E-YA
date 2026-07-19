#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
초 단위 CLM 리서치 파이프라인.

흐름:
  1) 1분봉 다운로드(기존 data_loader) → CLM 신호 탐지(기존 clm_detector)
  2) 신호 진입 직후 horizon초 초봉만 타겟 수집(seconds_loader)
  3) 초 단위 라벨링(seconds_labeler) → ceiling / cohort / exit sweep
  4) 초 vs 분 해상도 비교 리포트

Usage:
  python3 research/run_seconds_pipeline.py --top-markets 20 --days 10 --max-events 800
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import get_krw_markets, download_candles, load_candles, quick_volume_rank
from clm_detector import detect_clm
from event_labeler import label_events  # 1분봉 라벨(비교용)
from ceiling import ceiling_analysis, format_report
from seconds_loader import collect_events_seconds_threaded
from seconds_labeler import label_all, INTERVALS_SEC
from cohort import cohort_split, best_cohort, exit_sweep

FMT = "%Y-%m-%dT%H:%M:%S"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--markets", type=str, default="")
    ap.add_argument("--top-markets", type=int, default=20)
    ap.add_argument("--max-events", type=int, default=800, help="초봉 수집 이벤트 상한(시간/데이터 보호)")
    ap.add_argument("--horizon", type=int, default=300)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", type=str, default="research/clm_events_sec.parquet")
    args = ap.parse_args()

    # 1. 마켓
    if args.markets:
        markets = [m.strip() for m in args.markets.split(",")]
    else:
        print("KRW 마켓 거래량 랭킹...", flush=True)
        markets = quick_volume_rank(get_krw_markets(), args.top_markets)
    print(f"대상 {len(markets)}개: {', '.join(markets)}", flush=True)

    # 2. 1분봉 병렬 다운 + CLM 신호
    print(f"\n1분봉 {args.days}일 병렬 다운로드({args.workers} workers)...", flush=True)
    def _dl(m):
        try:
            download_candles(m, days_back=args.days)
            return m
        except Exception as e:
            print(f"  DL ERR {m}: {e}", flush=True)
            return None
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for j, _ in enumerate(ex.map(_dl, markets)):
            if (j + 1) % 10 == 0:
                print(f"  다운로드 {j+1}/{len(markets)}", flush=True)

    print(f"CLM 탐지...", flush=True)
    events_meta = []
    events_1m = []
    for i, m in enumerate(markets):
        df = load_candles(m)
        if df is None or len(df) < 10:
            continue
        df["market"] = m
        sigs = detect_clm(df)
        if sigs.empty:
            continue
        # 1분봉 라벨(비교 baseline)
        ev1m = label_events(df, sigs)
        if not ev1m.empty:
            events_1m.append(ev1m)
        # 초봉용 메타
        for _, s in sigs.iterrows():
            start = datetime.strptime(s["candle_date_time_utc"], FMT)
            events_meta.append({
                "market": m,
                "entry_dt": start + timedelta(seconds=60),  # 신호 1분봉 종료 = 진입시각
                "entry_price": float(s["trade_price"]),
                "entry_time_kst": s.get("candle_date_time_kst", ""),
                "body_pct": float(s["body_pct"]), "wick_ratio": float(s["wick_ratio"]),
                "vr5": float(s["vr5"]), "close_strength": float(s["close_strength"]),
                "wick_asym": float(s["wick_asym"]),
            })
        print(f"  [{i+1}/{len(markets)}] {m:<12} 신호 {len(sigs)}건", flush=True)

    print(f"\n총 CLM 신호 {len(events_meta)}건", flush=True)
    if not events_meta:
        print("신호 없음. 종료.")
        return

    # 최신순으로 잘라 max_events 제한(초봉 90일 한도 내 최신 우선)
    events_meta.sort(key=lambda e: e["entry_dt"], reverse=True)
    if len(events_meta) > args.max_events:
        print(f"  → 최신 {args.max_events}건으로 제한(초봉 수집 보호)", flush=True)
        events_meta = events_meta[:args.max_events]

    # 3. 초봉 타겟 수집
    print(f"\n초봉 타겟 수집(진입 직후 {args.horizon}s)...", flush=True)
    cache_path = os.path.join(os.path.dirname(args.out), "sec_cache.parquet")
    sec_map = collect_events_seconds_threaded(events_meta, horizon=args.horizon,
                                              cache_path=cache_path, workers=args.workers)

    # 4. 초 단위 라벨
    print(f"\n초 단위 라벨링...", flush=True)
    ev_sec = label_all(events_meta, sec_map, INTERVALS_SEC, min_coverage=60)
    if ev_sec.empty:
        print("라벨 0건(초봉 부실). 종료.")
        return
    ev_sec = ev_sec.sort_values("entry_time").reset_index(drop=True)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    ev_sec.to_parquet(args.out, index=False)
    print(f"초 단위 이벤트 {len(ev_sec):,}건 저장: {args.out}", flush=True)

    # ── 리포트 ──
    lines = []
    def P(s=""):
        lines.append(s); print(s, flush=True)

    P("\n" + "=" * 60)
    P(f"  초 단위 CLM 분석  (n={len(ev_sec):,}, horizon={args.horizon}s)")
    P("=" * 60)

    # A) Ceiling (초 해상도)
    res = ceiling_analysis(ev_sec)
    P(format_report(res))

    # B) 초 vs 분 해상도 MFE 비교
    if events_1m:
        ev1m = pd.concat(events_1m, ignore_index=True)
        P("\n[해상도 비교] 평균 MFE — 진입 직후 실제 정점을 얼마나 포착?")
        P(f"  분봉(60s 최소해상도) 평균 max_mfe : {ev1m['max_mfe'].mean():+.4f}%  (n={len(ev1m)})")
        P(f"  초봉(세밀)          평균 max_mfe : {ev_sec['max_mfe'].mean():+.4f}%  (n={len(ev_sec)})")
        P("  ※ 초봉은 분 사이 미세 정점을 더 정확히 잡음 → Perfect Exit 현실화")

    # C) 초기 모멘텀 시계열(A/C 분기 관측)
    P("\n[진입 직후 시계열] 평균 MFE / PnL (초 단위)")
    for t in INTERVALS_SEC:
        P(f"  {t:>3}s   mfe={ev_sec[f'mfe_{t}'].mean():+.4f}%   pnl={ev_sec[f'pnl_{t}'].mean():+.4f}%")

    # D) 코호트 A/C
    P("\n[A/C 코호트] 초기 모멘텀으로 분리 시 downstream lift")
    best = best_cohort(ev_sec)
    if best is not None:
        P(f"  베스트 분리: {best['signal']} ≥ {best['thr']:+.4f}")
        P(f"    A(계속): n={int(best['A_n'])}  pnl={best['A_pnl']:+.4f}%  wr={best['A_wr']:.0f}%")
        P(f"    C(소멸): n={int(best['C_n'])}  pnl={best['C_pnl']:+.4f}%  wr={best['C_wr']:.0f}%")
        P(f"    Lift: pnl {best['lift_pnl']:+.4f}%p,  wr {best['lift_wr']:+.0f}%p")
        if best["lift_pnl"] > 0.05:
            P("    → 코호트 분리가 실질 알파. 초기 모멘텀 게이트 유효.")
        else:
            P("    → 분리력 약함. 초기 모멘텀만으로는 edge 부족.")

    # E) 청산 sweep (비용 차감 net)
    P("\n[청산 sweep] net PnL(수수료+슬립 왕복 0.20% 차감) 상위")
    sw = exit_sweep(ev_sec, INTERVALS_SEC)
    for _, r in sw.head(8).iterrows():
        P(f"  {r['rule']:<12} net={r['net_pnl']:+.4f}%  wr={r['wr']:.0f}%")

    P("\n" + "=" * 60)

    # 리포트 저장
    rpt = os.path.join(os.path.dirname(args.out), "seconds_report.txt")
    with open(rpt, "w") as f:
        f.write("\n".join(lines))
    print(f"\n리포트 저장: {rpt}")
    return ev_sec


if __name__ == "__main__":
    main()
