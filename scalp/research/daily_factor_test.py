#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_factor_test — 일·주 단위 두 가설 (사전 고정 2026-10-22). 공개 API만, 주문·키 없음, 표준 라이브러리만.

왜 일·주 단위인가: 지금까지 초~시간 단위 7개 계열은 왕복 비용(0.3~0.6%)을 못 넘었다. 주 단위 코인 움직임은
±10% 수준이라 같은 비용이 상대적으로 작다. 아래 두 현상은 학술 연구에서 반복 보고됐지만 아직 시험하지 않았다.

H1 코인 간 모멘텀 (주간)
  매주 월요일(UTC) 시가에 리밸런싱. 유니버스 = 직전 30일 거래대금 중앙값 상위 50 KRW 종목 (상장 60일 이상)
  신호 = 직전 28일 수익률 (일요일 종가까지). 상위 5개를 같은 비중으로 매수, 다음 월요일 시가에 전량 매도
H2 김치 프리미엄 되돌림 (3일)
  프리미엄 = 업비트 종가 / (바이낸스 USDT 종가 × 업비트 KRW-USDT 종가) − 1
  이탈 = 오늘 프리미엄 − 직전 30일 프리미엄 중앙값. 3일마다, 이탈이 가장 낮은(평소보다 업비트가 싼) 5개를
  다음날 시가에 매수, 3일 뒤 시가에 매도. 이탈 < 0 인 종목만
H4 알트 단기 되돌림 (사전 고정 2026-10-24 · H1 이 주당 -2.7%p 로 크게 진 것을 보고 '반대 방향' 가설로 추가)
  매일: 유니버스(위와 동일 상위 50) 중 어제 하루 수익률(어제 종가/그제 종가)이 가장 낮은 5개를 오늘 시가 매수,
  내일 시가 매도 (1일 보유). 어제 수익률 < -25% 인 종목은 제외 (상장폐지 공지 급락이 보통 이 범위라 그 편향을 줄임.
  이 수치는 결과를 보기 전에 정함). 비교·비용·구간·합격 기준은 공통과 같음.
  의도: 하루 5건 ≈ 월 150건의 '다매매' 구조에서 건당 기대값이 비용(왕복 0.3%)을 넘는지 직접 본다.
공통
  비용: 수수료 0.05% + 불리한 가격 0.10% (편도). 매 리밸런싱 전량 교체 가정 (보수적)
  비교: 같은 시점·같은 유니버스 전 종목 동일비중(EW) 보유, BTC 단순 보유
  구간: ~2026-04-24 TRAIN(탐색) / 2026-04-25~ HOLDOUT(판정)
  합격(HOLDOUT): 전략 비용 후 평균 > 0 · EW 대비 초과수익 평균 > 0 · 초과수익 부트스트랩 90% CI 하한 > 0 ·
                 누적수익 > BTC 보유 누적수익
한계 (판정 전에 미리 적어 둠):
  - 생존편향: 업비트 API 는 현재 상장 종목만 준다. 상장폐지된 종목이 빠져 EW·전략 모두 실제보다 좋게 나온다.
    EW 대비 초과수익으로 판정하는 이유가 이것 (같은 편향이 양쪽에 들어감)
  - HOLDOUT 이 약 26주라 검정력이 낮다. PASS 여도 '후보'이고 이후 forward 기간으로 다시 본다
  - 일봉 시가 가정. 실제 체결은 비용 가정으로만 반영. LIVE 규칙 아님
실행 (PC):
  py daily_factor_test.py --out df_result
"""
import argparse
import datetime as dt
import json
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UPBIT = "https://api.upbit.com/v1"
BINANCE = ("https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines")
FEE, SLIP = 0.0005, 0.0010
TOP_N, LIQ_DAYS, MIN_HIST = 50, 30, 60
MOM_DAYS, MOM_PICK = 28, 5
KP_DAYS, KP_PICK, KP_HOLD = 30, 5, 3
REV_PICK, REV_HOLD, REV_MIN_RET = 5, 1, -0.25
HOLDOUT_START = "2026-04-25"
FORWARD_START = "2026-10-23"   # 이 날짜 이후 = 결과를 보기 전 미래 구간 (H2 PASS 이후 추가, 판정 기준 동일)


def _get(url, retries=6):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=20) as r:
                time.sleep(0.12)
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < retries - 1:
                time.sleep(1 + i)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < retries - 1:
                time.sleep(1 + i)
                continue
            raise


def fetch_upbit_daily(market, days):
    """{date: (open, close, krw_value)}"""
    out, to = {}, None
    while len(out) < days:
        url = f"{UPBIT}/candles/days?market={market}&count=200" + (f"&to={to}" if to else "")
        rows = _get(url)
        if not rows:
            break
        for r in rows:
            out[r["candle_date_time_utc"][:10]] = (float(r["opening_price"]), float(r["trade_price"]),
                                                   float(r["candle_acc_trade_price"]))
        oldest = min(r["candle_date_time_utc"] for r in rows)
        if len(rows) < 200:
            break
        to = oldest[:19] + "Z"
    return out


def fetch_binance_daily(sym, limit=1000):
    """{date: close} or None"""
    for base in BINANCE:
        try:
            rows = _get(f"{base}?symbol={sym}&interval=1d&limit={limit}")
            return {dt.datetime.fromtimestamp(int(r[0]) / 1000, dt.timezone.utc).date().isoformat(): float(r[4]) for r in rows}
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return None
            continue
        except Exception:
            continue
    return None


def cached(path, fn, refresh=False):
    """refresh=True 면 새로 받아 기존 캐시에 합친다. 새로 못 받으면(상장폐지 등) 기존 캐시를 그대로 쓴다
    → 앞으로 상폐되는 종목도 상폐 직전까지의 가격이 남아 forward 구간의 생존편향을 줄인다."""
    p = Path(path)
    old = json.loads(p.read_text()) if p.exists() else None
    if old is not None and not refresh:
        return old
    try:
        v = fn()
    except Exception:
        v = None
    if v is None:
        return old
    if isinstance(old, dict):
        old.update(v)
        v = old
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(v))
    return v


# ───────── pure logic ─────────
def net(entry, exit_):
    return exit_ * (1 - SLIP) * (1 - FEE) / (entry * (1 + SLIP) * (1 + FEE)) - 1


def _shift(d, k):
    return (dt.date.fromisoformat(d) + dt.timedelta(days=k)).isoformat()


def universe(data, d):
    """d 당일 자료는 쓰지 않음."""
    scored = []
    for m, s in data.items():
        if m == "KRW-USDT":
            continue
        hist = [s[_shift(d, -k)] for k in range(1, MIN_HIST + 1) if _shift(d, -k) in s]
        if len(hist) < MIN_HIST - 5:
            continue
        vals = [s[_shift(d, -k)][2] for k in range(1, LIQ_DAYS + 1) if _shift(d, -k) in s]
        if len(vals) >= LIQ_DAYS - 3:
            scored.append((statistics.median(vals), m))
    scored.sort(reverse=True)
    return [m for _, m in scored[:TOP_N]]


def hold_returns(data, coins, d_in, d_out):
    """d_in 시가 매수, d_out 시가 매도. 매도일 자료가 없으면 그 전 마지막 종가로 청산(상폐 근사)."""
    out = {}
    for m in coins:
        s = data[m]
        if d_in not in s:
            continue
        if d_out in s:
            ex = s[d_out][0]
        else:
            prior = [k for k in s if d_in <= k < d_out]
            if not prior:
                continue
            ex = s[max(prior)][1]
        out[m] = net(s[d_in][0], ex)
    return out


def momentum_periods(data, start, end):
    rows, d = [], start
    while dt.date.fromisoformat(d).weekday() != 0:
        d = _shift(d, 1)
    while _shift(d, 7) <= end:
        uni = universe(data, d)
        mom = {}
        for m in uni:
            s = data[m]
            a, b = _shift(d, -1 - MOM_DAYS), _shift(d, -1)
            if a in s and b in s and s[a][1] > 0:
                mom[m] = s[b][1] / s[a][1] - 1
        picks = [m for m, _ in sorted(mom.items(), key=lambda x: -x[1])[:MOM_PICK]]
        rs = hold_returns(data, picks, d, _shift(d, 7))
        ew = hold_returns(data, uni, d, _shift(d, 7))
        btc = data.get("KRW-BTC", {})
        if rs and ew and d in btc and _shift(d, 7) in btc:
            rows.append({"date": d, "picks": picks, "strat": statistics.mean(rs.values()),
                         "ew": statistics.mean(ew.values()), "btc": btc[_shift(d, 7)][0] / btc[d][0] - 1})
        d = _shift(d, 7)
    return rows


def reversal_periods(data, start, end):
    """H4: 어제(d-1) 종가/그제(d-2) 종가 기준 하위 REV_PICK 종목을 d 시가 매수, d+REV_HOLD 시가 매도."""
    rows, d = [], start
    btc = data.get("KRW-BTC", {})
    while _shift(d, REV_HOLD) <= end:
        uni = universe(data, d)
        ret = {}
        for m in uni:
            s = data[m]
            a, b = _shift(d, -2), _shift(d, -1)
            if a in s and b in s and s[a][1] > 0:
                ret[m] = s[b][1] / s[a][1] - 1
        cands = sorted((v, m) for m, v in ret.items() if REV_MIN_RET <= v < 0)
        picks = [m for _, m in cands[:REV_PICK]]
        d_out = _shift(d, REV_HOLD)
        rs = hold_returns(data, picks, d, d_out)
        ew = hold_returns(data, list(ret), d, d_out)
        if rs and ew and d in btc and d_out in btc:
            rows.append({"date": d, "picks": picks, "ret_pct": [round(ret[m] * 100, 2) for m in picks],
                         "strat": statistics.mean(rs.values()), "ew": statistics.mean(ew.values()),
                         "btc": btc[d_out][0] / btc[d][0] - 1})
        d = _shift(d, REV_HOLD)
    return rows


def kimchi_periods(data, bn, start, end):
    rows, d = [], start
    fx = data.get("KRW-USDT", {})
    btc = data.get("KRW-BTC", {})

    def prem(m, day):
        s, b = data[m], bn.get(m)
        if not b or day not in s or day not in b or day not in fx or b[day] <= 0:
            return None
        return s[day][1] / (b[day] * fx[day][1]) - 1

    while _shift(d, 1 + KP_HOLD) <= end:
        uni = [m for m in universe(data, _shift(d, 1)) if m in bn]
        dev = {}
        for m in uni:
            p = prem(m, d)
            hist = [prem(m, _shift(d, -k)) for k in range(1, KP_DAYS + 1)]
            hist = [h for h in hist if h is not None]
            if p is not None and len(hist) >= KP_DAYS - 5:
                dev[m] = p - statistics.median(hist)
        picks = [m for m, v in sorted(dev.items(), key=lambda x: x[1]) if v < 0][:KP_PICK]
        d_in, d_out = _shift(d, 1), _shift(d, 1 + KP_HOLD)
        rs = hold_returns(data, picks, d_in, d_out)
        ew = hold_returns(data, list(dev), d_in, d_out)
        if rs and ew and d_in in btc and d_out in btc:
            rows.append({"date": d_in, "picks": picks, "dev_pct": [round(dev[m] * 100, 3) for m in picks],
                         "strat": statistics.mean(rs.values()), "ew": statistics.mean(ew.values()),
                         "btc": btc[d_out][0] / btc[d_in][0] - 1})
        d = _shift(d, KP_HOLD)
    return rows


def ci_low(v, n=2000, seed=5):
    if len(v) < 2:
        return None
    rng = random.Random(seed)
    m = sorted(statistics.mean(rng.choices(v, k=len(v))) for _ in range(n))
    return m[int(0.05 * n)]


def summarize(rows):
    if not rows:
        return {"periods": 0}
    s, e, b = [r["strat"] for r in rows], [r["ew"] for r in rows], [r["btc"] for r in rows]
    ex = [x - y for x, y in zip(s, e)]

    def cum(v):
        c = 1.0
        for x in v:
            c *= 1 + x
        return c - 1

    def mdd(v):
        c, peak, worst = 1.0, 1.0, 0.0
        for x in v:
            c *= 1 + x
            peak = max(peak, c)
            worst = min(worst, c / peak - 1)
        return worst

    return {"periods": len(rows), "mean_pct": statistics.mean(s) * 100, "ew_mean_pct": statistics.mean(e) * 100,
            "excess_pct": statistics.mean(ex) * 100, "excess_ci_low_pct": (ci_low(ex) or 0) * 100,
            "win_vs_ew": sum(x > 0 for x in ex) / len(ex), "cum_pct": cum(s) * 100, "ew_cum_pct": cum(e) * 100,
            "btc_cum_pct": cum(b) * 100, "mdd_pct": mdd(s) * 100}


def verdict(s, min_periods=10):
    if s.get("periods", 0) < min_periods:
        return "INSUFFICIENT"
    ok = s["mean_pct"] > 0 and s["excess_pct"] > 0 and s["excess_ci_low_pct"] > 0 and s["cum_pct"] > s["btc_cum_pct"]
    return "PASS" if ok else "FAIL"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=760)
    ap.add_argument("--cache", default="df_cache")
    ap.add_argument("--out", default="df_result")
    ap.add_argument("--refresh", action="store_true", help="최신 일봉을 받아 캐시에 합침 (forward 재평가용)")
    ap.add_argument("--only", nargs="*", default=None, help="실행할 가설만 (예: --only H4). 생략 시 전부")
    a = ap.parse_args(argv)
    markets = {m["market"] for m in _get(f"{UPBIT}/market/all") if m["market"].startswith("KRW-")}
    # 캐시에만 남은 종목(이후 상장폐지) 도 포함 → forward 구간 생존편향 완화
    markets |= {q.stem[3:] for q in Path(a.cache).glob("up_KRW-*.json")}
    markets = sorted(markets)
    print(f"DAILY FACTORS (pre-registered 2026-10-22) | {len(markets)} KRW markets | holdout from {HOLDOUT_START}")
    data, bn = {}, {}
    for k, m in enumerate(markets, 1):
        try:
            data[m] = {d: tuple(v) for d, v in (cached(f"{a.cache}/up_{m}.json", lambda: fetch_upbit_daily(m, a.days), a.refresh) or {}).items()}
        except Exception as e:
            print(f"[upbit] skip {m}: {e}")
        if k % 50 == 0:
            print(f"[upbit] {k}/{len(markets)}")
    need_bn = a.only is None or "H2" in a.only
    for k, m in enumerate(markets, 1):
        if not need_bn or m in ("KRW-USDT",):
            continue
        sym = m.split("-", 1)[1] + "USDT"
        try:
            v = cached(f"{a.cache}/bn_{sym}.json", lambda: fetch_binance_daily(sym), a.refresh)
        except Exception:
            v = None
        if v:
            bn[m] = v
        if k % 50 == 0:
            print(f"[binance] {k}/{len(markets)} matched={len(bn)}")
    all_days = sorted({d for s in data.values() for d in s})
    start, end = _shift(all_days[0], MIN_HIST + 5), all_days[-1]
    runners = {"H1_momentum": lambda: momentum_periods(data, start, end),
               "H2_kimchi": lambda: kimchi_periods(data, bn, start, end),
               "H4_reversal": lambda: reversal_periods(data, start, end)}
    res = {k: f() for k, f in runners.items() if a.only is None or k.split("_")[0] in a.only}
    print(f"\ndata {all_days[0]}..{end} | binance matched {len(bn)} | KRW-USDT days {len(data.get('KRW-USDT', {}))}")
    print("\nhypothesis    phase     periods  mean%   EW%    excess%  ciLow%  win%   cum%     EWcum%   BTCcum%  MDD%    verdict")
    report = {"groups": [], "params": vars(a)}
    for name, rows in res.items():
        for ph in ("TRAIN", "HOLDOUT", "FORWARD"):
            lo, hi = {"TRAIN": ("", HOLDOUT_START), "HOLDOUT": (HOLDOUT_START, FORWARD_START),
                      "FORWARD": (FORWARD_START, "9999")}[ph]
            sel = [r for r in rows if lo <= r["date"] < hi]
            s = summarize(sel)
            vd = {"TRAIN": "(explore)", "HOLDOUT": verdict(s), "FORWARD": verdict(s, 20)}[ph]
            s.update(hypothesis=name, phase=ph, verdict=vd)
            report["groups"].append(s)
            f = lambda x, w=7, p=2: (f"{x:{w}.{p}f}" if isinstance(x, (int, float)) else " " * (w - 3) + "n/a")
            print(f"{name:13} {ph:8} {s['periods']:7} {f(s.get('mean_pct'))} {f(s.get('ew_mean_pct'),6)} {f(s.get('excess_pct'),8)} "
                  f"{f(s.get('excess_ci_low_pct'))} {f((s.get('win_vs_ew') or 0)*100,5,0)} {f(s.get('cum_pct'),8,1)} "
                  f"{f(s.get('ew_cum_pct'),8,1)} {f(s.get('btc_cum_pct'),8,1)} {f(s.get('mdd_pct'),7,1)}  {vd}")
    print("\nmean% = per period (H1 week, H2 3 days) after costs. Survivorship bias inflates mean% and EW%; judge by excess%.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "periods.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(f"Results: {out / 'report.json'} {out / 'periods.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
