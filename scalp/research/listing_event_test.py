#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""listing_event_test — H16 업비트 신규 상장 첫날 이벤트 (사전 고정 2026-10-24 · 처음 H12 로 등록했으나 다른 세션의 H12 프로필 교차와 번호가 겹쳐 H16 으로 변경). 공개 API만, 주문·키 없음.

왜: 알트 횡단면 순위(1일·1주·4주, H1/H4/H5)는 어느 방향도 EW 를 못 이겼다. 남은 것은 '가격이 아닌 이벤트'다.
업비트 신규 상장은 첫날 거래대금이 수천억으로 튀고 '상장빔 후 되돌림' 이 통설이지만 비용 후 수치로 본 적은 없다.

이벤트 정의
  업비트 일봉 캐시(daily_factor_test 의 df_cache, 800일)에서 첫 일봉이 2024-09-01 이후인 KRW 종목 = 신규 상장.
  (캐시 가장 오래된 날이 2024-08-16 이므로 그 이후 첫 캔들은 진짜 상장. 16일 여유를 둠)
  제외: 페그 자산 — 이름 목록(USDT·USDC·USD1·USDE·USDS·RLUSD·USDG·EURC·JPYC·PYUSD·XAUT) 또는
        상장 후 30 일봉 종가 max/min < 1.10. (자산 종류 분류이며 수익률로 고르는 것이 아님)
  한계: 이미 BTC 마켓에 있던 코인의 KRW 추가상장과 완전 신규 상장을 구분하지 못함 (공개 과거 데이터 없음).

규칙 (결과 보기 전 고정)
  진입 E5  : 첫 체결 1분봉 시각 + 5분 이후 첫 캔들 시가 (첫 5분 가격발견 구간은 실제로 못 잡는다고 가정)
  진입 E30 : 첫 체결 + 30분 이후 첫 캔들 시가 (초기 급등락이 끝난 뒤 진입하면 다른지)
  보유     : 30분 · 60분 · 240분 · 다음날 09:00 KST(= UTC 00:00 일봉 시가). 청산 = 목표 시각 이후 첫 캔들 시가.
             목표 시각 후 60분 안에 캔들이 없으면 그 이벤트는 해당 보유에서 제외(개수 보고).
  비용     : 기본 수수료 0.05% + 불리한 가격 0.10% (편도) · 스트레스 0.05% + 0.25% (상장 직후 스프레드가 넓음)
  구간     : 상장일 < 2026-04-25 TRAIN / 이후 HOLDOUT (다른 가설과 같은 경계)
  합격(HOLDOUT, 기본 비용): n ≥ 20 · 평균 > 0 · 중앙값 > 0 · 부트스트랩 90% CI 하한 > 0 · 최고 1건 제외 평균 > 0
    (상장 수익률은 꼬리가 두꺼워 평균 하나로는 판단하지 않음)
  서술(판정 아님): 첫 캔들 시가 → 첫 5분 고가 → 다음날 시가 경로. 되돌림이 일관되면 '상장 당일 매수 금지' 근거로만 쓴다
    (업비트는 공매도 불가 → 반대 방향은 전략이 아님).

결과 H16 (2026-10-24 실행, 170 상장, TRAIN 119 / HOLDOUT 51, 캔들 누락 0):
  롱은 전부 FAIL. HOLDOUT 기본비용: E5 30m -3.3% · 60m -5.7% · 240m -10.6% · 다음날 시가 -14.8% (승률 10~29%)
  E30 도 같은 방향(30m -2.5% … 다음날 -12.4%). TRAIN 은 더 나쁨(E5 다음날 -17.1%). 비용 스트레스는 0.3%p 차이로 무관.
  서술: 첫 5분 고점 → 다음날 시가 = TRAIN -24.9% / HOLDOUT -18.5%, 94% 가 고점 아래. 첫 시가 → 다음날 시가는
  TRAIN +28.5%(중앙 +11.5%) 이나 HOLDOUT +6.1%(중앙 -3.9%) 로 '첫 체결가' 조차 최근엔 못 지킴.
  결론: 상장 당일은 어느 시점에 사도 하루 안에 두 자릿수 손실이 기대값. 전 캠페인에서 가장 일관된 신호지만 방향이
  '매도' 라 업비트 현물로는 전략이 못 됨. 실무 의미 = LIVE 유니버스에서 상장 당일(및 익일) 종목 제외 근거.

H16b 상장 다음날 이후 (사전 고정 2026-10-24 · H16 결과를 본 뒤 추가 — 되돌림이 하루로 끝나는지, 이어지는지)
  진입 = 상장 다음날(D+1) UTC 00:00 일봉 시가 (= H16 'nextopen' 가격). 청산 = D+2 · D+4 · D+8 시가 (1·3·7일 보유).
  데이터 = df_cache 일봉(추가 수집 없음). 비교 = 같은 기간 BTC 보유. 비용 기본 0.15%/편도.
  합격(HOLDOUT): H16 와 같은 5조건 + 평균 > 같은 기간 BTC 평균. 기대(기록용): 상장 2~3일째도 매도 압력이 이어져 음수.
  결과 (2026-10-24): 전부 FAIL. HOLDOUT D+1 매수 → 1일 -5.5% · 3일 -12.2% · 7일 -16.1% (승률 16~18%, 같은 기간 BTC
  -0.2~-1.1%). TRAIN 도 -5.3/-8.0/-13.0%. 되돌림은 하루로 끝나지 않고 최소 1주 이어진다.
  실무 결론: 상장 후 8일(D0~D+7) 동안은 어느 시점에 사도 기대값이 두 자릿수 음수. LIVE 유니버스(get_top_krw_by_24h
  = 24h 거래대금 상위) 는 상장일 종목이 반드시 상위에 들어오고 시나리오는 1분봉 7~25개만 요구하므로 노출돼 있음.
  → 제안(LIVE 변경 아님, 별도 승인 필요): 첫 일봉이 8일 미만인 종목 유니버스 제외 (가드 추가, 전략 변경 아님).

실행 (PC, daily_factor_test 를 먼저 돌려 df_cache 가 있어야 함):
  py -u listing_event_test.py --daily-cache df_cache --cache le_cache --out le_result
"""
import argparse
import datetime as dt
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_factor_test as dft  # noqa: E402  (_get, cached 재사용)

UPBIT = dft.UPBIT


def _mean(v):
    """statistics.fmean 대체 (PC 의 Python 3.7 호환)."""
    v = list(v)
    return sum(v) / len(v)
FEE, SLIP, SLIP_STRESS = 0.0005, 0.0010, 0.0025
LIST_AFTER = "2024-09-01"
ENTRY_DELAYS = (5, 30)
HOLDS = (("30m", 30), ("60m", 60), ("240m", 240), ("nextopen", None))
EXIT_GRACE_MIN = 60
PEGGED = {"USDT", "USDC", "USD1", "USDE", "USDS", "RLUSD", "USDG", "EURC", "JPYC", "PYUSD", "XAUT"}
PEG_RANGE, PEG_DAYS = 1.10, 30
HOLDOUT_START = "2026-04-25"
MIN_EVENTS = 20


def _ts(s):
    return dt.datetime.fromisoformat(s[:19]).replace(tzinfo=dt.timezone.utc)


# ───────── events from daily cache ─────────
def listing_markets(daily_cache):
    """[(market, first_date)] — 첫 일봉이 LIST_AFTER 이후이고 페그가 아닌 종목."""
    out = []
    for p in sorted(Path(daily_cache).glob("up_KRW-*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or not d:
            continue
        market = p.name[3:-5]
        days = sorted(d)
        if days[0] <= LIST_AFTER or is_pegged(market, [d[k][1] for k in days[:PEG_DAYS]]):
            continue
        out.append((market, days[0]))
    return out


def is_pegged(market, closes):
    if market.split("-")[-1] in PEGGED:
        return True
    closes = [c for c in closes if c and c > 0]
    return len(closes) >= 5 and max(closes) / min(closes) < PEG_RANGE


# ───────── 1-min candles ─────────
def fetch_first_day_minutes(market, first_date):
    """상장일 첫 체결 ~ 다음날 01:00 UTC 까지의 1분봉, 시간 오름차순 [[utc, open, high, low, close, value], ...]."""
    end = dt.date.fromisoformat(first_date) + dt.timedelta(days=1)
    to = f"{end.isoformat()}T01:00:00Z"
    rows, seen = [], set()
    for _ in range(12):
        page = dft._get(f"{UPBIT}/candles/minutes/1?market={market}&count=200&to={to}")
        if not page:
            break
        for r in page:
            u = r["candle_date_time_utc"][:19]
            if u in seen:
                continue
            seen.add(u)
            rows.append([u, float(r["opening_price"]), float(r["high_price"]), float(r["low_price"]),
                         float(r["trade_price"]), float(r["candle_acc_trade_price"])])
        oldest = min(r["candle_date_time_utc"][:19] for r in page)
        if oldest[:10] < first_date or len(page) < 200:
            break
        to = oldest + "Z"
    rows = [r for r in rows if r[0][:10] >= first_date]
    rows.sort()
    return rows


# ───────── pure logic ─────────
def net(entry, exit_, slip=SLIP):
    return exit_ * (1 - slip) * (1 - FEE) / (entry * (1 + slip) * (1 + FEE)) - 1


def first_open_at_or_after(candles, t, grace_min=EXIT_GRACE_MIN):
    """t 이후 첫 캔들 (grace 안에 없으면 None)."""
    for c in candles:
        ct = _ts(c[0])
        if ct >= t:
            return c if (ct - t) <= dt.timedelta(minutes=grace_min) else None
    return None


def event_returns(candles, first_date):
    """한 이벤트의 진입/청산 가격과 서술 경로. 캔들이 비면 None."""
    if not candles:
        return None
    t0 = _ts(candles[0][0])
    next_open_t = dt.datetime.combine(dt.date.fromisoformat(first_date) + dt.timedelta(days=1), dt.time(),
                                      tzinfo=dt.timezone.utc)
    legs = {}
    for delay in ENTRY_DELAYS:
        ent = first_open_at_or_after(candles, t0 + dt.timedelta(minutes=delay))
        if ent is None:
            continue
        et = _ts(ent[0])
        for name, hold in HOLDS:
            target = next_open_t if hold is None else et + dt.timedelta(minutes=hold)
            if target <= et:
                continue
            ex = first_open_at_or_after(candles, target)
            if ex is None:
                continue
            legs[f"E{delay}_{name}"] = (ent[1], ex[1])
    first5 = [c for c in candles if _ts(c[0]) < t0 + dt.timedelta(minutes=5)]
    nxt = first_open_at_or_after(candles, next_open_t)
    desc = {"first_open": candles[0][1], "peak5": max(c[2] for c in first5),
            "next_open": nxt[1] if nxt else None,
            "day_value_krw": sum(c[5] for c in candles if _ts(c[0]) < next_open_t)}
    return {"legs": legs, "desc": desc}


def ci_low(v, n=2000, seed=5):
    if len(v) < 2:
        return float("nan")
    rnd = random.Random(seed)
    means = sorted(_mean(rnd.choices(v, k=len(v))) for _ in range(n))
    return means[int(0.05 * n)]


def summarize(v):
    if not v:
        return {"n": 0}
    s = sorted(v)
    return {"n": len(v), "mean": _mean(v), "median": statistics.median(v),
            "win": sum(x > 0 for x in v) / len(v), "ci_low": ci_low(v),
            "ex_top1": _mean(s[:-1]) if len(s) > 1 else float("nan")}


def verdict(s):
    if s["n"] < MIN_EVENTS:
        return "INSUFFICIENT"
    ok = s["mean"] > 0 and s["median"] > 0 and s["ci_low"] > 0 and s["ex_top1"] > 0
    return "PASS" if ok else "FAIL"


def analyze(events):
    """events: [{market, first_date, legs, desc}] → 표 행."""
    rows = []
    for phase in ("TRAIN", "HOLDOUT"):
        ev = [e for e in events if (e["first_date"] >= HOLDOUT_START) == (phase == "HOLDOUT")]
        for delay in ENTRY_DELAYS:
            for name, _ in HOLDS:
                key = f"E{delay}_{name}"
                for cost, slip in (("base", SLIP), ("stress", SLIP_STRESS)):
                    v = [net(*e["legs"][key], slip) for e in ev if key in e["legs"]]
                    s = summarize(v)
                    s.update(phase=phase, leg=key, cost=cost, events=len(ev),
                             verdict=verdict(s) if (phase == "HOLDOUT" and cost == "base") else "(explore)")
                    rows.append(s)
    return rows


def describe(events):
    """서술: 첫 시가→5분 고점→다음날 시가. 판정 아님."""
    out = {}
    for phase in ("TRAIN", "HOLDOUT"):
        ev = [e for e in events if (e["first_date"] >= HOLDOUT_START) == (phase == "HOLDOUT")]
        pk = [e["desc"]["peak5"] / e["desc"]["first_open"] - 1 for e in ev if e["desc"]["first_open"] > 0]
        fade = [e["desc"]["next_open"] / e["desc"]["peak5"] - 1 for e in ev
                if e["desc"]["next_open"] and e["desc"]["peak5"] > 0]
        full = [e["desc"]["next_open"] / e["desc"]["first_open"] - 1 for e in ev
                if e["desc"]["next_open"] and e["desc"]["first_open"] > 0]
        out[phase] = {"n": len(ev), "peak5_vs_first_open": summarize(pk), "next_open_vs_peak5": summarize(fade),
                      "next_open_vs_first_open": summarize(full)}
    return out


# ───────── H16b: 상장 다음날 이후 (일봉) ─────────
POST_HOLDS = (1, 3, 7)


def _shift(d, k):
    return (dt.date.fromisoformat(d) + dt.timedelta(days=k)).isoformat()


def _daily(daily_cache, market):
    p = Path(daily_cache, f"up_{market}.json")
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def post_listing_returns(daily_cache, listings):
    """[(market, first_date)] → [{market, first_date, legs: {'D1_1d': (entry, exit, btc_ret)}}]. D+1 시가 매수."""
    btc = _daily(daily_cache, "KRW-BTC")
    out = []
    for m, d0 in listings:
        d = _daily(daily_cache, m)
        d1 = _shift(d0, 1)
        if d1 not in d or d1 not in btc:
            continue
        legs = {}
        for h in POST_HOLDS:
            dx = _shift(d1, h)
            if dx in d and dx in btc and d[d1][0] > 0 and btc[d1][0] > 0:
                legs[f"D1_{h}d"] = (d[d1][0], d[dx][0], btc[dx][0] / btc[d1][0] - 1)
        out.append({"market": m, "first_date": d0, "legs": legs})
    return out


def analyze_post(events):
    rows = []
    for phase in ("TRAIN", "HOLDOUT"):
        ev = [e for e in events if (e["first_date"] >= HOLDOUT_START) == (phase == "HOLDOUT")]
        for h in POST_HOLDS:
            key = f"D1_{h}d"
            legs = [e["legs"][key] for e in ev if key in e["legs"]]
            s = summarize([net(a, b) for a, b, _ in legs])
            s["btc_mean"] = _mean(c for _, _, c in legs) if legs else float("nan")
            v = verdict(s) if phase == "HOLDOUT" else "(explore)"
            if v == "PASS" and not s["mean"] > s["btc_mean"]:
                v = "FAIL"
            s.update(phase=phase, leg=key, cost="base", events=len(ev), verdict=v)
            rows.append(s)
    return rows


def _fmt(s):
    if s["n"] == 0:
        return f"{s['n']:>4} {'n/a':>7} {'n/a':>7} {'n/a':>5} {'n/a':>7} {'n/a':>7}"
    return (f"{s['n']:>4} {s['mean']*100:>+7.2f} {s['median']*100:>+7.2f} {s['win']*100:>5.0f} "
            f"{s['ci_low']*100:>+7.2f} {s['ex_top1']*100:>+7.2f}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-cache", default="df_cache")
    ap.add_argument("--cache", default="le_cache")
    ap.add_argument("--out", default="le_result")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args(argv)

    markets = listing_markets(a.daily_cache)
    print(f"LISTING EVENTS (pre-registered 2026-10-24) | {len(markets)} new KRW listings after {LIST_AFTER} | "
          f"holdout from {HOLDOUT_START}")
    events, missing = [], []
    for i, (m, d0) in enumerate(markets, 1):
        c = dft.cached(f"{a.cache}/{m}_{d0}.json", lambda: fetch_first_day_minutes(m, d0), a.refresh)
        r = event_returns(c or [], d0)
        if r is None:
            missing.append(m)
            continue
        events.append({"market": m, "first_date": d0, "first_ts": c[0][0], **r})
        if i % 25 == 0:
            print(f"[upbit 1m] {i}/{len(markets)}", flush=True)
    print(f"\nevents {len(events)} | no candles {len(missing)} {missing[:5]}")

    rows = analyze(events)
    print(f"\n{'phase':<8} {'leg':<14} {'cost':<7} {'n':>4} {'mean%':>7} {'med%':>7} {'win%':>5} {'ciLow%':>7} "
          f"{'exTop1%':>7}  verdict")
    for s in rows:
        print(f"{s['phase']:<8} {s['leg']:<14} {s['cost']:<7} {_fmt(s)}  {s['verdict']}")
    desc = describe(events)
    print("\ndescriptive (no costs, not a verdict):")
    for phase, d in desc.items():
        for k in ("peak5_vs_first_open", "next_open_vs_peak5", "next_open_vs_first_open"):
            print(f"  {phase:<8} {k:<24} {_fmt(d[k])}")
    post = post_listing_returns(a.daily_cache, markets)
    post_rows = analyze_post(post)
    print(f"\nH12b post-listing (D+1 open entry, daily cache, base cost):\n{'phase':<8} {'leg':<14} {'n':>4} {'mean%':>7} "
          f"{'med%':>7} {'win%':>5} {'ciLow%':>7} {'exTop1%':>7} {'BTC%':>7}  verdict")
    for s in post_rows:
        print(f"{s['phase']:<8} {s['leg']:<14} {_fmt(s)} {s['btc_mean']*100:>+7.2f}  {s['verdict']}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"rows": rows, "describe": desc, "missing": missing,
                                                 "post_rows": post_rows}, indent=1))
    (out / "events.json").write_text(json.dumps(events, indent=1))
    print(f"\nResults: {out}/report.json {out}/events.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
