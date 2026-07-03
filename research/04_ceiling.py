"""Ceiling Analysis — CLM 진입 알파의 이론적 상한.

30일 로드맵의 방향 결정에 결정적. 이 스크립트 결과가 A/B/C 결론의 기준.

계산 대상:
1. Perfect Exit: 각 진입 후 최대 MFE 시점 청산
2. Perfect Entry: 최종 PnL 상위 30%만 진입
3. Perfect Both: 상위 30% 진입 + MFE 청산
4. Exit Loss / Entry Loss 분리
5. 시간 Ceiling: MFE peak 도달 시점 분포

판정:
    Perfect Exit ≥ +0.50%  → Exit 개선 여력 큼 (EC30/PP 계속)
    Perfect Exit +0.15~0.30% → 애매, 백테스트 sweep 필요
    Perfect Exit ≤ +0.10%  → CLM 알파 자체 얇음, 새 전략 필요

TODO:
- [ ] event_labeler로 만든 parquet 로드
- [ ] Perfect Exit/Entry/Both 계산
- [ ] 시간 Ceiling 출력
- [ ] Walk-forward 3분할 (train/val/test)
- [ ] 각 분할에서 동일 결과 나오는지 확인
"""

import argparse
from pathlib import Path


DATA_DIR = Path(__file__).parent / "data"


def load_events():
    """02_label.py가 만든 이벤트 parquet 로드."""
    raise NotImplementedError("02_label.py 완성 후 구현")


def ceiling(events):
    """Ceiling 계산.

    events: dict list, 각 dict는:
        entry_time, entry_price,
        pnl_10s, pnl_20s, pnl_30s, pnl_60s, pnl_120s, pnl_180s, pnl_300s,
        mfe_30s, mfe_60s, mfe_180s,
        mae_30s, mae_60s, mae_180s,
        final_pnl, max_mfe, max_mae, mfe_peak_sec
    """
    n = len(events)
    if n < 100:
        raise ValueError(f"표본 부족 (n={n}). n≥100 필요")

    realized = sum(e["final_pnl"] for e in events) / n * 100
    perfect_exit = sum(e["max_mfe"] for e in events) / n * 100

    # Perfect Entry: 상위 30% 진입
    top30 = sorted(events, key=lambda e: e["final_pnl"], reverse=True)[: int(n * 0.3)]
    perfect_entry = sum(e["final_pnl"] for e in top30) / len(top30) * 100
    perfect_both = sum(e["max_mfe"] for e in top30) / len(top30) * 100

    exit_loss = perfect_exit - realized
    entry_loss = perfect_entry - realized

    # 시간 Ceiling: MFE peak 도달 시점
    peak_secs = [e["mfe_peak_sec"] for e in events if e.get("mfe_peak_sec") is not None]

    return {
        "n": n,
        "realized": realized,
        "perfect_exit": perfect_exit,
        "perfect_entry": perfect_entry,
        "perfect_both": perfect_both,
        "exit_loss": exit_loss,
        "entry_loss": entry_loss,
        "recovery_rate": realized / perfect_exit * 100 if perfect_exit > 0 else 0,
        "peak_secs": peak_secs,
    }


def print_report(r):
    print(f"\n========== CLM Ceiling ==========")
    print(f"n = {r['n']}\n")
    print(f"실제 PnL:      {r['realized']:+.3f}%")
    print(f"Perfect Exit:  {r['perfect_exit']:+.3f}%")
    print(f"Perfect Entry: {r['perfect_entry']:+.3f}%")
    print(f"Perfect Both:  {r['perfect_both']:+.3f}%\n")
    print(f"Exit Loss:  {r['exit_loss']:+.3f}%p")
    print(f"Entry Loss: {r['entry_loss']:+.3f}%p")
    print(f"회수율:     {r['recovery_rate']:.1f}%\n")

    # 시간 Ceiling
    peak_secs = r["peak_secs"]
    if peak_secs:
        buckets = [(0, 30), (30, 60), (60, 120), (120, 180), (180, 9999)]
        print(f"시간 Ceiling (MFE peak 시점 n={len(peak_secs)}):")
        for lo, hi in buckets:
            cnt = sum(1 for s in peak_secs if lo <= s < hi)
            pct = cnt / len(peak_secs) * 100
            lbl = f"{lo}~{hi}s" if hi < 9999 else f"{lo}s+"
            print(f"  {lbl:>10}: {cnt:>4}건 ({pct:5.1f}%)")

    # 판정
    print(f"\n========== 판정 ==========")
    pe = r["perfect_exit"]
    if pe >= 0.5:
        print(f"Perfect Exit +{pe:.2f}%: Exit 개선 여력 큼")
        print(f"  → EC30/PP/AT 튜닝 계속할 가치 있음")
    elif pe >= 0.15:
        print(f"Perfect Exit +{pe:.2f}%: 애매")
        print(f"  → 백테스트 sweep으로 최적 청산 찾기")
    else:
        print(f"Perfect Exit +{pe:.2f}%: CLM 알파 자체 얇음")
        print(f"  → CLM 폐기, 새 전략 카테고리 탐색 필요")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default=str(DATA_DIR / "clm_events.parquet"))
    args = parser.parse_args()
    events = load_events()
    r = ceiling(events)
    print_report(r)
