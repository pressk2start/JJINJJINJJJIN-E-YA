#!/usr/bin/env python3
"""
경로 B: Upbit 다운로드 → CLM 탐지 → 라벨링 → Ceiling.

전체 파이프라인 실행. Top-N 거래량 마켓 대상.

사용법:
    python3 research/run_pipeline.py --days 90 --markets 30
    python3 research/run_pipeline.py --days 30 --markets 10  # 빠른 테스트
"""
import argparse
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import get_krw_markets, quick_volume_rank, download_candles, load_candles
from clm_detector import detect_clm
from event_labeler import label_events
from ceiling import ceiling_analysis, format_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="다운로드 일수")
    parser.add_argument("--markets", type=int, default=30, help="Top-N 거래량 마켓")
    parser.add_argument("--force", action="store_true", help="캐시 무시 재다운로드")
    args = parser.parse_args()

    print(f"1) KRW 마켓 조회...")
    krw_markets = get_krw_markets()
    print(f"   전체 {len(krw_markets)}개 KRW 마켓")

    print(f"\n2) 거래량 Top {args.markets} 선별 (최근 5분봉 기준)...")
    top_markets = quick_volume_rank(krw_markets, top_n=args.markets)
    print(f"   {top_markets}")

    print(f"\n3) 캔들 다운로드 (days={args.days})...")
    all_events = []
    for i, market in enumerate(top_markets, 1):
        t0 = time.time()
        fpath = download_candles(market, days_back=args.days, unit=1, force=args.force)
        if not fpath:
            print(f"   [{i}/{len(top_markets)}] {market}: skip (no data)")
            continue
        df = load_candles(market, unit=1)
        if df is None or len(df) < 100:
            print(f"   [{i}/{len(top_markets)}] {market}: skip (short {len(df) if df is not None else 0})")
            continue

        # CLM 탐지
        signals = detect_clm(df)
        if signals.empty:
            print(f"   [{i}/{len(top_markets)}] {market}: {len(df)}건 → CLM 0개")
            continue

        signals["market"] = market

        # 라벨링
        events = label_events(df, signals)
        if not events.empty:
            all_events.append(events)

        dt = time.time() - t0
        print(f"   [{i}/{len(top_markets)}] {market}: {len(df):>6}건 → CLM {len(signals):>3} → 라벨 {len(events):>3} ({dt:.1f}s)")

    if not all_events:
        print("\n❌ CLM 이벤트 없음")
        return

    combined = pd.concat(all_events, ignore_index=True)
    print(f"\n4) 통합 이벤트: {len(combined):,}건")

    # 저장
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "clm_events.parquet")
    combined.to_parquet(out_path, index=False)
    print(f"   저장: {out_path}")

    # Ceiling
    print(f"\n5) Ceiling Analysis...")
    result = ceiling_analysis(combined)
    print(format_report(result))


if __name__ == "__main__":
    main()
