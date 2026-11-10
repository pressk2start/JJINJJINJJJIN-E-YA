#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""conditional_salvage — H26 사전 등록: 버려진 일·주 단위 시나리오를 '지표 1개 조건'으로 되살릴 수 있는가.
(공개 일봉만, 키·주문 없음, 표준 라이브러리. daily_factor_test 의 유니버스·비용·보유수익 함수를 그대로 import.)

사용자 지적(2026-11-10): 지금까지는 시나리오를 통째로 돌려 "전체 평균이 음수 → 폐기" 로 끝냈다. 시나리오가 만드는 거래 전체를
    모수로 놓고, 그 안에서 승패를 가르는 지표 1개를 찾아 붙였을 때 음에서 양으로 바뀌는지를 봐야 한다. 아무리 번갈아 봐도
    없으면 그때 버린다. 이 스크립트가 그 절차를 한 번에, 같은 기준으로 한다.

위험과 통제: 지표를 여러 개 번갈아 보면 우연히 좋아 보이는 조건이 반드시 나온다. 그래서 조건 탐색은 TRAIN 에서만 하고,
    거기서 고른 조건만 HOLDOUT(탐색에 쓰지 않은 뒤쪽 기간)에서 판정한다. 아래 목록·문턱·기준은 자료를 보기 전에 고정한다.

사전 등록 (이 커밋이 결과보다 먼저다):
  자료      업비트 KRW 전 종목 일봉. 결정일 2023-01-01 ~ 최신. TRAIN ≤ 2025-06-30, HOLDOUT ≥ 2025-07-01.
  유니버스  daily_factor_test.universe: 직전 30일 거래대금 중앙값 상위 50, 상장 60일 이상, 결정일 자료 미사용.
  비용      편도 수수료 0.05% + 불리한 가격 0.10% (daily_factor_test.net). 시가 매수·시가 매도.
  기본 시나리오 5개 (예전 가설을 그대로):
    D_REV1  매일, 어제 수익률 하위 5개(−25% ≤ r < 0) 매수, 1일 보유            (H4·H9: '가장 많이 떨어진 5종목')
    D_MOM1  매일, 어제 수익률 상위 5개(r > 0) 매수, 1일 보유                   (그 거울상)
    W_MOM28 매주 월요일, 직전 28일 수익률 상위 5개, 1주 보유                   (H1)
    W_REV28 매주 월요일, 직전 28일 수익률 하위 5개(≥ −40%), 1주 보유           (H5)
    W_REV7  매주 월요일, 직전 7일 수익률 하위 5개(≥ −40%), 1주 보유            (H5b)
  지표 15개 (모두 결정일 전날까지의 종가·거래대금만 사용):
    시장  btc_trend(BTC 종가/50일 평균−1) · btc_ret7 · breadth20(유니버스 중 20일 평균 위 비율) · mkt_ret1(유니버스 어제 수익률 중앙값)
          · btc_vol14(BTC 일수익률 14일 표준편차) · weekend(일 단위 시나리오만: 결정일이 토·일)
    종목  coin_trend20 · coin_trend50(종가/평균−1) · vol_ratio(어제 거래대금/직전 20일 중앙값) · idio_ret1(어제 수익률−시장 중앙값)
          · ret7 · ret28 · dd30(30일 고점 대비) · atr14(14일 평균 절대수익률) · liq_rank(유니버스 내 거래대금 순위)
  조건      지표마다 2개: TRAIN 3분위의 아래 1/3('low')과 위 1/3('high'). 단 btc_trend 는 0 기준(위/아래 = H3 신호),
            weekend 는 예/아니오. 조건은 지표 1개짜리만(쌍 없음). 시나리오당 약 30개, 전체 약 146개.
  집계 단위 기간(결정일) 하나 = 그 날 조건을 통과한 거래들의 평균. 통과 거래가 없는 날은 현금(집계 제외). 초과수익 = 기간 수익 − 같은 날
            유니버스 전 종목 동일비중(EW) 수익. 거래 건이 아니라 기간을 표본으로 t 를 계산한다(같은 날 거래는 서로 독립이 아님).
  선택      TRAIN 에서 기간 수 ≥ 40(일)·20(주), 평균 순수익 > 0, 평균 초과수익 > 0 인 조건 중 순수익 t 가 큰 순서로
            시나리오당 최대 2개, t ≥ 2.0 인 것만 잠근다. 없으면 그 시나리오는 '되살릴 조건 없음'.
  판정      잠긴 조건만 HOLDOUT 에서: 기간 수 ≥ 20, 평균 순수익의 단측 하한(유의수준 0.05/잠긴 조건 수) > 0, 평균 초과수익 > 0 → PASS.
            PASS 는 '후보'다 — H2 처럼 앞으로의 기간에서 다시 본 뒤에야 실거래를 논한다. 전부 FAIL 이면 이 시나리오들은 닫는다.
  보조 진단 TRAIN 에서 조건을 통과한(순수익>0·초과>0·t≥2) 것들 중 HOLDOUT 에서도 순수익이 양수인 비율을 함께 낸다
            — '조건을 붙여 살리는' 방법 자체가 얼마나 재현되는지를 보는 수치(선택·판정에는 쓰지 않음).
  한계      생존편향(현재 상장 종목만) → 특히 급락 종목을 사는 REV 계열이 실제보다 좋게 나온다. 초과수익 조건이 일부만 상쇄.
            HOLDOUT 일부(2025-07~2026-10)는 기본 시나리오의 무조건 결과를 이미 본 구간이다(조건별 결과는 본 적 없음).
            시가 체결 가정. 조건을 붙이면 거래가 줄어 자금이 노는 날이 생긴다 — 기간 평균은 '거래한 날' 기준이다.

실행:
  python3 conditional_salvage.py --cache <일봉 캐시 폴더> --out <결과 폴더>      (처음엔 일봉을 받느라 5~10분)
"""
import argparse
import datetime as dt
import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("daily_factor_test", str(Path(__file__).resolve().parent / "daily_factor_test.py"))
DF = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(DF)
_shift = DF._shift

START, TRAIN_END, HOLD_START = "2023-01-01", "2025-06-30", "2025-07-01"
FETCH_DAYS = 1700
PICK = 5
MIN_PERIODS = {"D": 40, "W": 20}
MIN_HOLD_PERIODS = 20
LOCK_PER_SCENARIO, LOCK_MIN_T = 2, 2.0
SCENARIOS = ("D_REV1", "D_MOM1", "W_MOM28", "W_REV28", "W_REV7")
MARKET_IND = ("btc_trend", "btc_ret7", "breadth20", "mkt_ret1", "btc_vol14")
COIN_IND = ("coin_trend20", "coin_trend50", "vol_ratio", "idio_ret1", "ret7", "ret28", "dd30", "atr14", "liq_rank")


# ----------------------------------------------------------------------------------------------------------------------
def load_data(cache_dir, days=FETCH_DAYS, log=print):
    cache = Path(cache_dir)
    markets = DF.cached(str(cache / "_markets.json"), lambda: [m["market"] for m in DF._get(DF.UPBIT + "/market/all")
                                                                 if m["market"].startswith("KRW-")])
    data = {}
    for i, m in enumerate(markets):
        rows = DF.cached(str(cache / (m + ".json")), lambda m=m: DF.fetch_upbit_daily(m, days))
        if rows:
            data[m] = {d: tuple(v) for d, v in rows.items()}
        if log and (i + 1) % 50 == 0:
            log("  loaded %d/%d markets" % (i + 1, len(markets)))
    return data


def closes(series, day, n):
    """day 전날까지의 종가 n개(오래된 것부터). 하루라도 없으면 None."""
    out = []
    for k in range(n, 0, -1):
        row = series.get(_shift(day, -k))
        if row is None:
            return None
        out.append(row[1])
    return out


def market_state(data, uni, day):
    """시장 지표. 계산 불가 항목은 None."""
    st = dict.fromkeys(MARKET_IND)
    btc = data.get("KRW-BTC", {})
    c51 = closes(btc, day, 51)
    if c51:
        st["btc_trend"] = c51[-1] / (sum(c51[-50:]) / 50.0) - 1
        st["btc_ret7"] = c51[-1] / c51[-8] - 1
        rets = [c51[i] / c51[i - 1] - 1 for i in range(len(c51) - 14, len(c51))]
        st["btc_vol14"] = statistics.pstdev(rets)
    above, total, r1 = 0, 0, []
    for m in uni:
        c21 = closes(data[m], day, 21)
        if c21:
            total += 1
            above += 1 if c21[-1] > sum(c21[-20:]) / 20.0 else 0
            if c21[-2] > 0:
                r1.append(c21[-1] / c21[-2] - 1)
    if total >= 10:
        st["breadth20"] = above / float(total)
    if len(r1) >= 10:
        st["mkt_ret1"] = statistics.median(r1)
    return st


def coin_state(data, market, day, mkt, rank):
    """종목 지표. 계산 불가 항목은 None."""
    st = dict.fromkeys(COIN_IND)
    st["liq_rank"] = rank
    s = data[market]
    c51 = closes(s, day, 51)
    c31 = c51[-31:] if c51 else closes(s, day, 31)
    if c31:
        last = c31[-1]
        st["coin_trend20"] = last / (sum(c31[-20:]) / 20.0) - 1
        st["ret7"] = last / c31[-8] - 1 if c31[-8] > 0 else None
        st["ret28"] = last / c31[-29] - 1 if c31[-29] > 0 else None
        st["dd30"] = last / max(c31[-30:]) - 1
        st["atr14"] = sum(abs(c31[i] / c31[i - 1] - 1) for i in range(len(c31) - 14, len(c31))) / 14.0
        r1 = c31[-1] / c31[-2] - 1 if c31[-2] > 0 else None
        if r1 is not None and mkt.get("mkt_ret1") is not None:
            st["idio_ret1"] = r1 - mkt["mkt_ret1"]
        vals = [s[_shift(day, -k)][2] for k in range(2, 22) if _shift(day, -k) in s]
        y = s.get(_shift(day, -1))
        if len(vals) >= 15 and y is not None:
            med = statistics.median(vals)
            st["vol_ratio"] = y[2] / med if med > 0 else None
    if c51:
        st["coin_trend50"] = c51[-1] / (sum(c51[-50:]) / 50.0) - 1
    return st


def pick_scenarios(data, uni, day):
    """결정일의 시나리오별 종목 선택 → {시나리오: [종목]}. 주 단위는 월요일에만."""
    r1, r7, r28 = {}, {}, {}
    for m in uni:
        c = closes(data[m], day, 29)
        if not c:
            c2 = closes(data[m], day, 2)
            if c2 and c2[0] > 0:
                r1[m] = c2[1] / c2[0] - 1
            continue
        if c[-2] > 0:
            r1[m] = c[-1] / c[-2] - 1
        if c[-8] > 0:
            r7[m] = c[-1] / c[-8] - 1
        if c[0] > 0:
            r28[m] = c[-1] / c[0] - 1
    out = {
        "D_REV1": [m for _, m in sorted((v, m) for m, v in r1.items() if DF.REV_MIN_RET <= v < 0)[:PICK]],
        "D_MOM1": [m for _, m in sorted(((v, m) for m, v in r1.items() if v > 0), reverse=True)[:PICK]],
    }
    if dt.date.fromisoformat(day).weekday() == 0:
        out["W_MOM28"] = [m for _, m in sorted(((v, m) for m, v in r28.items()), reverse=True)[:PICK]]
        out["W_REV28"] = [m for _, m in sorted((v, m) for m, v in r28.items() if v >= DF.WREV_MIN_RET)[:PICK]]
        out["W_REV7"] = [m for _, m in sorted((v, m) for m, v in r7.items() if v >= DF.WREV_MIN_RET)[:PICK]]
    return out


def build_trades(data, start, end, log=None):
    """모든 시나리오의 거래 목록. 거래 = dict(sc, day, m, net, ew, ind{지표})."""
    trades, day = [], start
    while day <= end:
        uni = DF.universe(data, day)
        if len(uni) >= 20:
            mkt = market_state(data, uni, day)
            rank = {m: i for i, m in enumerate(uni)}
            ew_cache = {}
            for sc, picks in pick_scenarios(data, uni, day).items():
                hold = 1 if sc.startswith("D") else 7
                d_out = _shift(day, hold)
                if d_out > end or not picks:
                    continue
                if hold not in ew_cache:
                    ew = DF.hold_returns(data, uni, day, d_out)
                    ew_cache[hold] = statistics.mean(ew.values()) if ew else None
                if ew_cache[hold] is None:
                    continue
                for m, r in DF.hold_returns(data, picks, day, d_out).items():
                    ind = dict(mkt)
                    ind.update(coin_state(data, m, day, mkt, rank[m]))
                    if sc.startswith("D"):
                        ind["weekend"] = 1.0 if dt.date.fromisoformat(day).weekday() >= 5 else 0.0
                    trades.append({"sc": sc, "day": day, "m": m, "net": r, "ew": ew_cache[hold], "ind": ind})
        if log and day.endswith("-01"):
            log("  built through %s (%d trades)" % (day, len(trades)))
        day = _shift(day, 1)
    return trades


# ----------------------------------------------------------------------------------------------------------------------
def period_stats(trades):
    """기간(결정일) 단위 통계 → dict(n, net, t, excess, t_excess, trades). 기간 수익 = 그 날 거래들의 평균."""
    by_day = {}
    for tr in trades:
        by_day.setdefault(tr["day"], []).append(tr)
    nets, exc = [], []
    for day in sorted(by_day):
        rows = by_day[day]
        r = statistics.mean(x["net"] for x in rows)
        nets.append(r)
        exc.append(r - rows[0]["ew"])
    n = len(nets)
    if n < 2:
        return {"n": n, "net": nets[0] if nets else None, "t": None, "excess": exc[0] if exc else None, "t_excess": None,
                "sd": None, "trades": len(trades)}
    sd = statistics.stdev(nets)
    sde = statistics.stdev(exc)
    return {"n": n, "net": statistics.mean(nets), "t": statistics.mean(nets) / (sd / math.sqrt(n)) if sd > 0 else None,
            "excess": statistics.mean(exc), "t_excess": statistics.mean(exc) / (sde / math.sqrt(n)) if sde > 0 else None,
            "sd": sd, "trades": len(trades)}


def make_conditions(train_trades, scenario):
    """TRAIN 자료로 조건 목록을 만든다 → [(이름, 지표, 연산, 문턱)]. 문턱은 이후 HOLDOUT 에 그대로 쓴다."""
    conds = [("btc_trend<0", "btc_trend", "<", 0.0), ("btc_trend>=0", "btc_trend", ">=", 0.0)]
    names = [n for n in MARKET_IND if n != "btc_trend"] + list(COIN_IND)
    for name in names:
        vals = sorted(tr["ind"][name] for tr in train_trades if tr["ind"].get(name) is not None)
        if len(vals) < 30:
            continue
        lo, hi = vals[len(vals) // 3], vals[(2 * len(vals)) // 3]
        if lo == hi:
            continue
        conds.append(("%s low" % name, name, "<=", lo))
        conds.append(("%s high" % name, name, ">=", hi))
    if scenario.startswith("D"):
        conds += [("weekend", "weekend", ">=", 0.5), ("weekday", "weekend", "<", 0.5)]
    return conds


def passes(trade, cond):
    v = trade["ind"].get(cond[1])
    if v is None:
        return False
    return v < cond[3] if cond[2] == "<" else v <= cond[3] if cond[2] == "<=" else v >= cond[3]


def scan(train_trades, scenario):
    """TRAIN 조건 탐색 → 모든 조건의 통계 목록(순수익 t 내림차순)."""
    out = []
    for cond in make_conditions(train_trades, scenario):
        st = period_stats([tr for tr in train_trades if passes(tr, cond)])
        st["cond"] = list(cond)
        st["qualified"] = bool(st["n"] >= MIN_PERIODS[scenario[0]] and st["net"] is not None and st["net"] > 0
                               and st["excess"] > 0 and st["t"] is not None and st["t"] >= LOCK_MIN_T)
        out.append(st)
    out.sort(key=lambda s: -(s["t"] if s["t"] is not None else -99))
    return out


def lower_bound(st, alpha):
    """평균 순수익의 단측 하한 (정규 근사)."""
    if st["n"] < 2 or not st["sd"]:
        return None
    z = statistics.NormalDist().inv_cdf(1 - alpha)
    return st["net"] - z * st["sd"] / math.sqrt(st["n"])


def judge(hold_trades, cond, alpha):
    st = period_stats([tr for tr in hold_trades if passes(tr, cond)])
    st["low"] = lower_bound(st, alpha)
    st["pass"] = bool(st["n"] >= MIN_HOLD_PERIODS and st["low"] is not None and st["low"] > 0 and st["excess"] > 0)
    return st


def _p(v, spec="{:+.3f}"):
    return "n/a" if v is None else spec.format(100 * v)


def _t(v):
    return "n/a" if v is None else "{:+.2f}".format(v)


def run(trades, log=print):
    """탐색(TRAIN) → 잠금 → 판정(HOLDOUT). 반환: dict(locked, verdicts, calibration)."""
    train = [t for t in trades if t["day"] <= TRAIN_END]
    hold = [t for t in trades if t["day"] >= HOLD_START]
    scans, locked = {}, []
    for sc in SCENARIOS:
        tr = [t for t in train if t["sc"] == sc]
        ho = [t for t in hold if t["sc"] == sc]
        base_tr, base_ho = period_stats(tr), period_stats(ho)
        log("== %s | base TRAIN n=%d net %s%% t %s excess %s%% | base HOLDOUT n=%d net %s%% excess %s%% ==" % (
            sc, base_tr["n"], _p(base_tr["net"]), _t(base_tr["t"]), _p(base_tr["excess"]),
            base_ho["n"], _p(base_ho["net"]), _p(base_ho["excess"])))
        scans[sc] = scan(tr, sc)
        for st in scans[sc][:6]:
            log("   TRAIN %-22s n=%-4d net %s%% t %s excess %s%% %s" % (
                st["cond"][0], st["n"], _p(st["net"]), _t(st["t"]), _p(st["excess"]), "QUALIFIED" if st["qualified"] else ""))
        picks = [st for st in scans[sc] if st["qualified"]][:LOCK_PER_SCENARIO]
        if not picks:
            log("   -> no condition qualified on TRAIN: nothing to carry to HOLDOUT")
        for st in picks:
            locked.append({"sc": sc, "cond": st["cond"], "train": {k: st[k] for k in ("n", "net", "t", "excess")}})
    log("LOCKED %d condition(s) on TRAIN. HOLDOUT is opened only for these." % len(locked))
    alpha = 0.05 / max(1, len(locked))
    verdicts = []
    for item in locked:
        ho = [t for t in hold if t["sc"] == item["sc"]]
        st = judge(ho, item["cond"], alpha)
        verdicts.append({"sc": item["sc"], "cond": item["cond"], "train": item["train"],
                         "hold": {k: st[k] for k in ("n", "net", "t", "excess", "low", "pass")}})
        log("  %s + %-22s TRAIN net %s%% t %s -> HOLDOUT n=%d net %s%% t %s excess %s%% low %s%% => %s" % (
            item["sc"], item["cond"][0], _p(item["train"]["net"]), _t(item["train"]["t"]), st["n"], _p(st["net"]),
            _t(st["t"]), _p(st["excess"]), _p(st["low"]), "PASS" if st["pass"] else "FAIL"))
    # 보조 진단: TRAIN 에서 통과한 모든 조건이 HOLDOUT 에서 어땠는가 (선택·판정에는 쓰지 않음)
    q = pos = 0
    for sc in SCENARIOS:
        ho = [t for t in hold if t["sc"] == sc]
        for st in scans[sc]:
            if st["qualified"]:
                h = period_stats([t for t in ho if passes(t, st["cond"])])
                if h["n"] >= MIN_HOLD_PERIODS:
                    q += 1
                    pos += 1 if (h["net"] or 0) > 0 else 0
    log("CALIBRATION: %d TRAIN-qualified conditions had enough HOLDOUT periods; %d of them stayed positive after costs." % (q, pos))
    n_pass = sum(1 for v in verdicts if v["hold"]["pass"])
    log("H26 VERDICT: %s (%d of %d locked conditions passed; alpha per condition %.4f)" % (
        "PASS (candidates for forward test only)" if n_pass else ("FAIL" if locked else "NOTHING TO TEST"), n_pass, len(locked), alpha))
    return {"locked": locked, "verdicts": verdicts, "calibration": {"qualified": q, "positive_on_holdout": pos}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="h26_cache")
    ap.add_argument("--out", default="h26_result")
    a = ap.parse_args(argv)
    out_lines = []

    def log(s):
        print(s, flush=True)
        out_lines.append(s)

    log("H26 CONDITIONAL SALVAGE | decisions %s.. | TRAIN <= %s | HOLDOUT >= %s | cost per side %.2f%%" % (
        START, TRAIN_END, HOLD_START, 100 * (DF.FEE + DF.SLIP)))
    data = load_data(a.cache, log=log)
    end = _shift(max(max(s) for s in data.values()), -1)       # 마지막 날은 진행 중인 일봉일 수 있어 제외
    log("markets %d | data through %s" % (len(data), end))
    trades = build_trades(data, START, end, log=log)
    log("trades %d (%s)" % (len(trades), ", ".join("%s %d" % (sc, sum(1 for t in trades if t["sc"] == sc)) for sc in SCENARIOS)))
    result = run(trades, log=log)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "h26_brief.txt").write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    (out / "h26_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved:", out / "h26_brief.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
