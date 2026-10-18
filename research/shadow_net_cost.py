#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shadow_stats.json 읽기 전용 분석 — 비용 차감 후 순기대값이 양수인 route 가 있는가.

배경 (2026-10-18):
  bot.py 의 shadow 가상매매 pnl 은 gross 다. 진입가 = 신호 시점 가격, 청산가 = 당시 현재가,
  수수료·스프레드·슬리피지 전부 미반영 (bot.py `pnl = (cur_price - entry_price) / entry_price`).
  LIVE 는 왕복 수수료 0.1% + 매수 시 ask 크로스 + 매도 시 bid 크로스를 실제로 낸다.
  그래서 "shadow +0.02%/건" 과 "LIVE -0.47%/건" 의 차이 대부분이 비용이다.
  다행히 shadow trade_records 의 inds 에 진입 시점 호가 비용 프록시가 같이 저장돼 있다:
    ob_spread_pct            (ask1-bid1)/mid %
    ob_slip_buy_{100..10000}k 시장가 매수 시 mid 대비 VWAP 불리폭 % (half-spread 포함)
    ob_slip_sell_*k           시장가 매도 시 mid 대비 VWAP 불리폭 %
    ob_exit_slip_sell_{100,300,500}k 청산 시점 호가 기준 매도 비용 (있을 때만)

사전 등록 질문 (코드 쓰기 전 고정):
  Q1. route 별 gross 평균에서 [수수료 0.10% + 매수 슬리피지 + 매도 슬리피지] 를 빼면 평균이 양수인가.
  Q2. 그 결과가 표본 전반부/후반부(청산시각 순) 모두에서 같은 부호인가 (안정성).
  Q3. 매수를 maker(bid 대기, 스프레드 미지불) 로 바꿨을 때의 상한은 얼마인가
      (체결률 100% 가정의 낙관 상한. 실행 가능성은 별도 검증 필요).
판정 규칙:
  - n < 100 이면 판정 보류 (INSUFFICIENT).
  - net_taker 평균 > 0 이고 전/후반 모두 > 0 → CANDIDATE_TAKER
  - 위 미달이지만 net_maker 평균 > 0 이고 전/후반 모두 > 0 → CANDIDATE_MAKER_ONLY
  - 그 외 → NEGATIVE_AFTER_COST
  이 스크립트는 판정 후보를 '나열'만 한다. LIVE 전환 근거가 아니며, 독립 forward 표본 검증이 필요.

주의:
  - 매수 비용 프록시는 주문 금액 6,000원 기준 가장 가까운 버킷(100k)을 쓴다 → 소액 주문엔 보수적(과대).
  - 청산 비용은 ob_exit_slip_sell_100k 가 있으면 그것, 없으면 진입 시점 ob_slip_sell_100k 로 대체.
  - 비용 필드가 없는 trade 는 full-net 계산에서 제외하고 coverage 로 보고한다.
  - 거래소 API 호출 없음. 파일 쓰기는 --output 지정 시 JSON 1개뿐.

실행 (서버):
  python3 research/shadow_net_cost.py shadow_stats.json
  python3 research/shadow_net_cost.py shadow_stats.json --live data/live_trades.jsonl --output /tmp/shadow_net.json
"""
import argparse
import json
import math
import statistics
import sys

FEE_RT_PCT_DEFAULT = 0.10  # Upbit 0.05% x 2
MIN_N = 100


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def trade_costs(inds, size_k=100):
    """(buy_slip_pct, sell_slip_pct, spread_pct) — 없으면 None."""
    inds = inds or {}
    buy = _f(inds.get(f"ob_slip_buy_{size_k}k"))
    sell = _f(inds.get(f"ob_exit_slip_sell_{size_k}k"))
    if sell is None:
        sell = _f(inds.get(f"ob_slip_sell_{size_k}k"))
    spread = _f(inds.get("ob_spread_pct"))
    return buy, sell, spread


def analyze_route(records, fee_pct=FEE_RT_PCT_DEFAULT, pnl_unit="fraction", size_k=100):
    scale = 100.0 if pnl_unit == "fraction" else 1.0
    rows = []
    for r in records:
        g = _f(r.get("pnl"))
        if g is None:
            continue
        g *= scale
        buy, sell, spread = trade_costs(r.get("inds"), size_k)
        net_fee = g - fee_pct
        net_taker = (g - fee_pct - buy - sell) if (buy is not None and sell is not None) else None
        net_maker = (g - fee_pct - sell) if sell is not None else None  # 진입 스프레드 미지불 상한
        rows.append({
            "gross": g, "net_fee": net_fee, "net_taker": net_taker, "net_maker": net_maker,
            "buy_slip": buy, "sell_slip": sell, "spread": spread,
            "exit_ts": _f(r.get("exit_ts")) or 0.0,
            "exit_reason": r.get("exit_reason"),
        })
    rows.sort(key=lambda x: x["exit_ts"])
    n = len(rows)
    out = {"n": n, "fee_pct": fee_pct, "size_k": size_k}
    if n == 0:
        out["verdict"] = "EMPTY"
        return out
    half = n // 2
    first, second = rows[:half], rows[half:]

    def block(rs):
        return {
            "n": len(rs),
            "gross_mean": _mean([x["gross"] for x in rs]),
            "net_fee_mean": _mean([x["net_fee"] for x in rs]),
            "net_taker_mean": _mean([x["net_taker"] for x in rs]),
            "net_maker_mean": _mean([x["net_maker"] for x in rs]),
            "net_taker_n": sum(1 for x in rs if x["net_taker"] is not None),
            "win_rate_gross": (sum(1 for x in rs if x["gross"] > 0) / len(rs)) if rs else None,
            "win_rate_net_taker": (
                (sum(1 for x in rs if x["net_taker"] is not None and x["net_taker"] > 0)
                 / max(1, sum(1 for x in rs if x["net_taker"] is not None)))
                if any(x["net_taker"] is not None for x in rs) else None),
        }

    out["all"] = block(rows)
    out["first_half"] = block(first)
    out["second_half"] = block(second)
    out["cost_coverage"] = out["all"]["net_taker_n"] / n
    out["median_spread_pct"] = _median([x["spread"] for x in rows])
    out["median_buy_slip_pct"] = _median([x["buy_slip"] for x in rows])
    out["median_sell_slip_pct"] = _median([x["sell_slip"] for x in rows])
    # 스프레드 3분위별 net_taker (비용이 결과를 좌우하는지)
    sp = sorted([x for x in rows if x["spread"] is not None and x["net_taker"] is not None],
                key=lambda x: x["spread"])
    if len(sp) >= 30:
        t = len(sp) // 3
        out["by_spread_tercile"] = []
        for lo, hi, label in ((0, t, "tight"), (t, 2 * t, "mid"), (2 * t, len(sp), "wide")):
            seg = sp[lo:hi]
            out["by_spread_tercile"].append({
                "bucket": label, "n": len(seg),
                "spread_range": [seg[0]["spread"], seg[-1]["spread"]],
                "gross_mean": _mean([x["gross"] for x in seg]),
                "net_taker_mean": _mean([x["net_taker"] for x in seg]),
            })
    # 판정
    a, f1, f2 = out["all"], out["first_half"], out["second_half"]
    if n < MIN_N or out["cost_coverage"] < 0.8:
        out["verdict"] = "INSUFFICIENT"
    elif (a["net_taker_mean"] is not None and a["net_taker_mean"] > 0
          and (f1["net_taker_mean"] or 0) > 0 and (f2["net_taker_mean"] or 0) > 0):
        out["verdict"] = "CANDIDATE_TAKER"
    elif (a["net_maker_mean"] is not None and a["net_maker_mean"] > 0
          and (f1["net_maker_mean"] or 0) > 0 and (f2["net_maker_mean"] or 0) > 0):
        out["verdict"] = "CANDIDATE_MAKER_ONLY"
    else:
        out["verdict"] = "NEGATIVE_AFTER_COST"
    return out


def analyze_horizons(records, fee_pct=FEE_RT_PCT_DEFAULT, pnl_unit="fraction", size_k=100,
                     horizons=(30, 60, 120, 180, 240, 300)):
    """고정 보유 가설 (생존편향 분리). trade_records[i].curve["<sec>"] 는 그 시점에 아직 보유 중일 때만 기록됨.
    survivor_mean : curve 값 있는 건만 평균 (봇 리포트의 '240s:+0.59' 가 이것 → 생존편향)
    all_mean      : 전건 평균. H 이전에 청산된 건은 실제 청산 pnl 사용 (= '기존 청산규칙 OR H 보유' 의 실측 하한)
    all_net_taker : all_mean − fee − buy_slip − sell_slip (비용 프록시 있는 건만)
    주의: all 은 '순수 고정보유' 가 아니라 '현 청산규칙 ∪ H 보유' 하이브리드의 실측. 순수 고정보유(청산규칙 OFF)
    는 전 건의 H 시점 MTM 이 필요해 shadow_stats 로는 계산 불가 (WS 리플레이 영역).
    판정(verdict, route verdict 와 같은 기준): n_all ≥ MIN_N · 비용 coverage ≥ 0.8 · all_net > 0 ·
    청산시각 순 전/후반 all_net 모두 > 0 → HYBRID_CANDIDATE. surv 만 양수면 SURVIVOR_ONLY(폐기).
    """
    scale = 100.0 if pnl_unit == "fraction" else 1.0
    out = {}
    recs = sorted([r for r in records if _f(r.get("pnl")) is not None], key=lambda r: _f(r.get("exit_ts")) or 0.0)
    half = len(recs) // 2
    for h in horizons:
        k = str(h)
        surv, allv, allnet = [], [], []
        net_first, net_second = [], []
        for idx, r in enumerate(recs):
            g = _f(r.get("pnl"))
            if g is None:
                continue
            g *= scale
            cv = (r.get("curve") or {}).get(k)
            cvf = _f(cv)
            val = cvf * scale if cvf is not None else g
            if cvf is not None:
                surv.append(cvf * scale)
            allv.append(val)
            buy, sell, _ = trade_costs(r.get("inds"), size_k)
            if buy is not None and sell is not None:
                nv = val - fee_pct - buy - sell
                allnet.append(nv)
                (net_first if idx < half else net_second).append(nv)
        cov = (len(allnet) / len(allv)) if allv else 0.0
        a_net, f1, f2 = _mean(allnet), _mean(net_first), _mean(net_second)
        if len(allv) < MIN_N or cov < 0.8:
            verdict = "INSUFFICIENT"
        elif a_net is not None and a_net > 0 and (f1 or 0) > 0 and (f2 or 0) > 0:
            verdict = "HYBRID_CANDIDATE"
        elif (_mean(surv) or 0) > 0 and (_mean(allv) or 0) <= 0:
            verdict = "SURVIVOR_ONLY"
        else:
            verdict = "NEGATIVE_AFTER_COST"
        out[k] = {"n_all": len(allv), "n_survivor": len(surv),
                  "survivor_share": (len(surv) / len(allv)) if allv else None,
                  "survivor_mean": _mean(surv), "all_mean": _mean(allv),
                  "all_net_taker_mean": a_net, "all_net_taker_n": len(allnet), "cost_coverage": cov,
                  "first_half_net": f1, "second_half_net": f2, "verdict": verdict}
    return out


def analyze_stats(stats, **kw):
    res = {}
    for key, s in (stats or {}).items():
        recs = (s or {}).get("trade_records") or []
        if not recs:
            continue
        res[key] = analyze_route(recs, **kw)
        res[key]["horizons"] = analyze_horizons(recs, **kw)
    return res


def live_summary(path):
    """live_trades.jsonl v2 TRADE_CLOSED 의 실현 net_pnl_pct 평균 (비교용)."""
    n, tot, wins = 0, 0.0, 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("event_type") != "TRADE_CLOSED" or e.get("schema_v") != 2 or not e.get("is_live"):
                    continue
                p = _f(e.get("net_pnl_pct"))
                if p is None:
                    continue
                n += 1
                tot += p
                wins += 1 if p > 0 else 0
    except OSError:
        return None
    return {"n": n, "net_mean_pct": (tot / n) if n else None, "win_rate": (wins / n) if n else None}


def _fmt(x, w=7):
    return f"{x:+{w}.3f}" if isinstance(x, (int, float)) else " " * (w - 4) + "n/a "


def print_report(res, live=None, min_n=20):
    print("SHADOW NET-COST — gross shadow pnl minus fee + orderbook cost proxies (read-only)")
    print(f"  fee_rt={FEE_RT_PCT_DEFAULT}%  buy cost=ob_slip_buy_100k  sell cost=ob_exit_slip_sell_100k|ob_slip_sell_100k")
    print(f"{'route:strat':48} {'n':>5} {'gross':>8} {'net_fee':>8} {'net_tak':>8} {'net_mak':>8} {'cov':>5} {'sprd':>6} {'1st':>7} {'2nd':>7}  verdict")
    order = sorted(res.items(), key=lambda kv: -(kv[1].get("all", {}).get("net_taker_mean") or -9e9))
    for key, r in order:
        if r.get("n", 0) < min_n:
            continue
        a = r["all"]
        print(f"{key[:48]:48} {r['n']:>5} {_fmt(a['gross_mean'],8)} {_fmt(a['net_fee_mean'],8)} "
              f"{_fmt(a['net_taker_mean'],8)} {_fmt(a['net_maker_mean'],8)} {r['cost_coverage']:>5.2f} "
              f"{(r['median_spread_pct'] if r['median_spread_pct'] is not None else float('nan')):>6.3f} "
              f"{_fmt(r['first_half']['net_taker_mean'])} {_fmt(r['second_half']['net_taker_mean'])}  {r['verdict']}")
    print("\nFIXED-HOLD (survivorship check): survivor=curve-only mean (biased), all=exited-before-H uses exit pnl")
    for key, r in order:
        if r.get("n", 0) < MIN_N or not r.get("horizons"):
            continue
        cells = []
        for h, v in r["horizons"].items():
            cells.append(f"{h}s surv{_fmt(v['survivor_mean'],6)}({(v['survivor_share'] or 0):.0%}) all{_fmt(v['all_mean'],6)} "
                         f"net{_fmt(v['all_net_taker_mean'],6)} 1st{_fmt(v['first_half_net'],6)} 2nd{_fmt(v['second_half_net'],6)}  {v['verdict']}")
        print(f"  {key[:40]}")
        for c in cells:
            print(f"      {c}")
    cands = [k for k, r in res.items() if r.get("verdict", "").startswith("CANDIDATE")]
    print(f"\ncandidates (n>={MIN_N}, cost coverage>=0.8, both halves same sign): {cands or 'NONE'}")
    if live:
        print(f"LIVE v2 realized: n={live['n']} net_mean={_fmt(live['net_mean_pct'])}% "
              f"win_rate={live['win_rate'] if live['win_rate'] is None else round(live['win_rate'], 3)}")
    print("NOTE: shadow entries fill at signal price with 100% fill assumption; 'net_mak' assumes maker entry "
          "always fills — an upper bound, not a forecast. Not a LIVE change recommendation.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stats", help="shadow_stats.json (read-only)")
    ap.add_argument("--live", help="data/live_trades.jsonl for realized comparison")
    ap.add_argument("--fee-pct", type=float, default=FEE_RT_PCT_DEFAULT)
    ap.add_argument("--pnl-unit", choices=("fraction", "percent"), default="fraction",
                    help="trade_records.pnl unit (bot.py stores fraction: 0.0012 = 0.12%%)")
    ap.add_argument("--size-k", type=int, default=100, help="orderbook slip bucket in KRW thousands")
    ap.add_argument("--min-n", type=int, default=20, help="rows with fewer trades are hidden in the table")
    ap.add_argument("--output", help="write full JSON here (new file only; never touches inputs)")
    a = ap.parse_args(argv)
    with open(a.stats, encoding="utf-8") as f:
        stats = json.load(f)
    res = analyze_stats(stats, fee_pct=a.fee_pct, pnl_unit=a.pnl_unit, size_k=a.size_k)
    live = live_summary(a.live) if a.live else None
    print_report(res, live, min_n=a.min_n)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            json.dump({"routes": res, "live": live, "params": vars(a)}, f, ensure_ascii=False, indent=1)
        print(f"written: {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
