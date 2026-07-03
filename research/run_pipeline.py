#!/usr/bin/env python3
"""
CLM Research Pipeline — 1주차 목표: clm_events.parquet

Usage:
  # 빠른 테스트 (상위 5개 마켓, 7일)
  python3 research/run_pipeline.py --top-markets 5 --days 7

  # 표준 실행 (상위 30개, 90일)
  python3 research/run_pipeline.py --top-markets 30 --days 90

  # 특정 마켓
  python3 research/run_pipeline.py --markets KRW-BTC,KRW-ETH,KRW-XRP --days 180

  # 다운로드 건너뛰기 (이미 캐시된 데이터)
  python3 research/run_pipeline.py --skip-download --days 90
"""
import argparse
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import get_krw_markets, download_candles, load_candles, quick_volume_rank, DATA_DIR
from clm_detector import detect_clm
from event_labeler import label_events
from ceiling import ceiling_analysis, format_report


def main():
    parser = argparse.ArgumentParser(description="CLM Research Pipeline")
    parser.add_argument("--days", type=int, default=90, help="다운로드 기간 (일)")
    parser.add_argument("--markets", type=str, default="", help="마켓 코드 (콤마 구분)")
    parser.add_argument("--top-markets", type=int, default=0, help="거래량 상위 N개 마켓 자동 선별")
    parser.add_argument("--all-markets", action="store_true", help="전체 KRW 마켓")
    parser.add_argument("--output", type=str, default="research/clm_events.parquet")
    parser.add_argument("--skip-download", action="store_true", help="다운로드 건너뛰기")
    parser.add_argument("--force", action="store_true", help="캐시 무시하고 재다운로드")
    args = parser.parse_args()

    t0 = time.time()

    # ── 1. 마켓 결정 ──
    if args.markets:
        markets = [m.strip() for m in args.markets.split(",")]
        print(f"마켓: {', '.join(markets)}")
    elif args.top_markets > 0:
        print(f"KRW 마켓 조회 중...")
        all_m = get_krw_markets()
        print(f"  전체 {len(all_m)}개에서 상위 {args.top_markets}개 선별...")
        markets = quick_volume_rank(all_m, args.top_markets)
        print(f"  선택: {', '.join(markets)}")
    elif args.all_markets:
        markets = get_krw_markets()
        print(f"전체 {len(markets)}개 KRW 마켓")
    elif args.skip_download:
        # 캐시된 파일에서 마켓 목록 추출
        if os.path.exists(DATA_DIR):
            files = [f for f in os.listdir(DATA_DIR) if f.endswith("_m1.parquet")]
            markets = [f.replace("_m1.parquet", "") for f in files]
            print(f"캐시된 {len(markets)}개 마켓")
        else:
            print("캐시 없음. --markets 또는 --top-markets 지정 필요")
            return
    else:
        print("--markets, --top-markets, 또는 --all-markets 중 하나 지정 필요")
        return

    # ── 2. 데이터 다운로드 ──
    if not args.skip_download:
        print(f"\n{'─'*40}")
        print(f"다운로드: {len(markets)}개 마켓 x {args.days}일")
        print(f"{'─'*40}")
        for i, m in enumerate(markets):
            print(f"  [{i+1:>3}/{len(markets)}] {m:<12}", end="", flush=True)
            try:
                fpath = download_candles(m, days_back=args.days, force=args.force)
                if fpath:
                    df = load_candles(m)
                    print(f" {len(df):>7,}건 OK")
                else:
                    print(" skip")
            except Exception as e:
                print(f" ERR: {e}")

    # ── 3. CLM 신호 탐지 + 라벨링 ──
    print(f"\n{'─'*40}")
    print(f"CLM 신호 탐지 + 이벤트 라벨링")
    print(f"{'─'*40}")
    all_events = []
    total_signals = 0
    for m in markets:
        df = load_candles(m)
        if df is None or len(df) < 7:
            continue
        df["market"] = m
        signals = detect_clm(df)
        if signals.empty:
            continue
        total_signals += len(signals)
        events = label_events(df, signals)
        if not events.empty:
            all_events.append(events)
            print(f"  {m:<12} 신호 {len(signals):>4}건 → 라벨 {len(events):>4}건")

    if not all_events:
        print("CLM 신호 없음. 다른 마켓이나 기간 시도 필요.")
        return

    combined = pd.concat(all_events, ignore_index=True)
    combined = combined.sort_values("entry_time").reset_index(drop=True)

    # 저장
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    combined.to_parquet(args.output, index=False)
    print(f"\n총 신호 {total_signals:,}건 → 라벨링 {len(combined):,}건")
    print(f"저장: {args.output}")

    # ── 4. Ceiling Analysis ──
    print(f"\n{'='*55}")
    result = ceiling_analysis(combined)
    report = format_report(result)
    print(report)

    # 마켓별 요약
    if len(all_events) > 1:
        print(f"\n[마켓별 요약]")
        by_market = combined.groupby("market").agg(
            n=("final_pnl", "count"),
            avg_pnl=("final_pnl", "mean"),
            avg_mfe=("max_mfe", "mean"),
            winrate=("final_pnl", lambda x: (x > 0).mean() * 100),
        ).sort_values("n", ascending=False)
        for m, row in by_market.iterrows():
            print(f"  {m:<12} n={row['n']:>4}  pnl={row['avg_pnl']:+.4f}%  mfe={row['avg_mfe']:+.4f}%  wr={row['winrate']:.0f}%")

    elapsed = time.time() - t0
    print(f"\n소요시간: {elapsed:.0f}초")

    return combined


if __name__ == "__main__":
    main()
