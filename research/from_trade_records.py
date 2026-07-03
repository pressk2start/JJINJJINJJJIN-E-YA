#!/usr/bin/env python3
"""
경로 A: bot.py의 기존 trade_records에서 직접 Ceiling Analysis.

Upbit API 다운로드 없이, 이미 수집된 LIVE 데이터로 즉시 분석.

사용법:
  1. bot.py에 export 함수 추가 (아래 코드 참고)
  2. export 실행 → clm_trades.json 생성
  3. python3 research/from_trade_records.py clm_trades.json

bot.py에 추가할 export 코드:
─────────────────────────────────────
def export_trade_records(filepath="clm_trades.json"):
    import json
    records = []
    for key, stats in _SHADOW_PERF_STATS.items():
        route, strat = key.split(":", 1) if ":" in key else (key, "")
        for tr in stats.get("trade_records", []):
            rec = {
                "route": route,
                "strat": strat,
                "pnl": tr.get("pnl", 0),
                "mfe": tr.get("mfe", 0),
                "hold": tr.get("hold", 0),
                "exit_reason": tr.get("exit_reason", ""),
            }
            # indicators (mfe_60s, dd_peak_60s 등)
            for k, v in tr.get("inds", {}).items():
                rec[f"ind_{k}"] = v
            records.append(rec)
    with open(filepath, "w") as f:
        json.dump(records, f, indent=2)
    print(f"Exported {len(records)} records to {filepath}")
─────────────────────────────────────
"""
import sys
import os
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ceiling import ceiling_analysis, format_report
try:
    from tg_notify import send as tg_send
except Exception:
    tg_send = None


def load_trade_records(filepath):
    """
    JSON 파일에서 trade records 로드.
    bot.py export 또는 수동 생성 모두 지원.
    """
    with open(filepath) as f:
        records = json.load(f)

    df = pd.DataFrame(records)
    print(f"총 {len(df)}건 로드")

    # route별 건수
    if "route" in df.columns:
        for route, grp in df.groupby("route"):
            print(f"  {route}: {len(grp)}건")

    return df


def build_events_from_records(df, route_filter=None):
    """
    trade_records → ceiling analysis 입력 형식 변환.

    trade_records 형식:
      pnl, mfe, hold, exit_reason, ind_mfe_60s, ind_dd_peak_60s, ...

    ceiling analysis 형식:
      final_pnl, max_mfe, max_mae, mfe_60, mfe_120, ... (가능한 것만)
    """
    if route_filter:
        df = df[df["route"] == route_filter].copy()
        print(f"\n[{route_filter}] {len(df)}건 분석")

    events = pd.DataFrame()
    events["final_pnl"] = df["pnl"].values * 100  # 소수→퍼센트
    events["max_mfe"] = df["mfe"].values * 100

    # max_mae는 trade_records에 없을 수 있음 → 0으로 대체
    if "ind_mae_60s" in df.columns:
        events["max_mae"] = df["ind_mae_60s"].values * 100
    else:
        events["max_mae"] = 0.0

    # 시간별 MFE/PnL (가용한 것만)
    time_mapping = {
        "ind_mfe_30s": "mfe_30",
        "ind_mfe_60s": "mfe_60",
        "ind_dd_peak_30s": "dd_30",
        "ind_dd_peak_60s": "dd_60",
    }
    for src, dst in time_mapping.items():
        if src in df.columns:
            events[dst] = df[src].values * 100

    # pnl_curve에서 시간별 PnL 추출 (있으면)
    if "pnl_curve" in df.columns:
        for sec in [30, 60, 120, 180, 300]:
            key = str(sec)
            vals = df["pnl_curve"].apply(lambda x: x.get(key, np.nan) if isinstance(x, dict) else np.nan)
            if vals.notna().any():
                events[f"pnl_{sec}"] = vals * 100

    # hold time
    if "hold" in df.columns:
        events["hold_sec"] = df["hold"].values

    # exit reason
    if "exit_reason" in df.columns:
        events["exit_reason"] = df["exit_reason"].values

    return events


def enhanced_report(events, label=""):
    """ceiling analysis + 추가 분석. 결과는 콘솔+텔레그램 병행 출력."""
    from io import StringIO
    result = ceiling_analysis(events)
    report = format_report(result)
    print(f"\n{report}")

    # 텔레그램용 캡처 버퍼
    tg_buf = StringIO()
    tg_buf.write(report + "\n")

    # 추가: exit reason 분포
    if "exit_reason" in events.columns:
        s = "\n[Exit Reason 분포]\n"
        for reason, cnt in events["exit_reason"].value_counts().items():
            sub = events[events["exit_reason"] == reason]
            avg = sub["final_pnl"].mean()
            s += f"  {reason:<16} {cnt:>4}건  avg={avg:+.4f}%\n"
        print(s)
        tg_buf.write(s)

    # 추가: hold time 분포
    if "hold_sec" in events.columns:
        hs = events["hold_sec"]
        s = f"\n[Hold Time 분포]\n  평균: {hs.mean():.0f}s  중앙값: {hs.median():.0f}s  p25: {np.percentile(hs, 25):.0f}s  p75: {np.percentile(hs, 75):.0f}s\n"
        print(s)
        tg_buf.write(s)

    # 추가: 30s/60s MFE 가용 시 EC 관련 분석
    if "mfe_60" in events.columns:
        mfe60 = events["mfe_60"]
        s = f"\n[EC 관련 — 60초 MFE]\n"
        s += f"  MFE60 < 0.10%: {(mfe60 < 0.10).sum()}건 ({(mfe60 < 0.10).mean()*100:.0f}%)\n"
        s += f"  MFE60 < 0.15%: {(mfe60 < 0.15).sum()}건 ({(mfe60 < 0.15).mean()*100:.0f}%)\n"
        s += f"  MFE60 < 0.20%: {(mfe60 < 0.20).sum()}건 ({(mfe60 < 0.20).mean()*100:.0f}%)\n"
        dead = events[mfe60 < 0.10]
        if len(dead) > 0:
            s += f"  Dead(MFE60<0.10%) 최종PnL: avg={dead['final_pnl'].mean():+.4f}% n={len(dead)}\n"
        print(s)
        tg_buf.write(s)

    # 텔레그램 전송
    if tg_send:
        title = f"🔬 Ceiling: {label or 'ALL'}"
        tg_send(title, tg_buf.getvalue())

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 research/from_trade_records.py <trade_records.json> [route_filter]")
        print("\nbot.py에서 export:")
        print("  export_trade_records('clm_trades.json')")
        return

    filepath = sys.argv[1]
    route_filter = sys.argv[2] if len(sys.argv) > 2 else None

    df = load_trade_records(filepath)
    if df.empty:
        print("데이터 없음")
        return

    # route_filter 지정 시 해당 route만
    if route_filter:
        events = build_events_from_records(df, route_filter)
        enhanced_report(events, route_filter)
    else:
        # 전체 분석
        events = build_events_from_records(df)
        enhanced_report(events, "전체")

        # route별 분석
        if "route" in df.columns:
            for route in df["route"].unique():
                sub = df[df["route"] == route]
                if len(sub) >= 10:
                    events_r = build_events_from_records(df, route)
                    enhanced_report(events_r, route)


if __name__ == "__main__":
    main()
