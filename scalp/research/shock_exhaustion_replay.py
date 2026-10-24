#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shock_exhaustion_replay — H17 초 단위 매도 충격 → 소진 → 반등 (사전 고정 2026-10-24). ws_recorder 원자료 재생.
주문·키 없음, 네트워크 없음, 표준 라이브러리만. 거래 서버에서 돌리지 말 것 (PC 전용).

왜 이것인가 (중복 아님을 먼저 확인)
  ws 아카이브(15일, orderbook+trade 둘 다 기록됨 — ws_recorder.py 186~187행) 에서 이미 시험한 것:
    매수 압력(테이커 BID 비중) → 5~30초 forward: micro_edge_probe / net_edge_oos → 비용 못 넘음
    체결 불균형·도착률·대형체결 비중 1/3/5/10초 sweep: flow/screen/sweep_flow → 보수 체결가정에서 사망
    maker bid1 join, 분위수 taker replay, 스프레드 필터, 고정 보유, 바이낸스 선행 → 전부 음수
  시험하지 않은 것: '매도 충격 뒤 매도세 소진' 을 **실제 테이커 방향·실제 호가 깊이** 로 본 것. sweep_flow 는 하락
  신호를 회피 신호로만 두고 역방향 진입을 재지 않았고, H14 는 5분봉 근사였다(비용 전 +0.10% < 왕복 0.14%).
  이 스크립트는 그 하나를 초 단위 원자료로 묻는다. 결과가 음수면 '매도 충격 반등' 계열은 캔들·초 단위 모두 종결.

규칙 (결과 보기 전 고정)
  충격   : 종목별 직전 10초 체결 창에서 (현재가/창 첫 체결가 − 1) ≤ θ[m] 이고 창 안 ASK(테이커 매도) 체결대금 비중 ≥ 70%.
           θ[m] = TRAIN 구간 그 종목 10초 수익률의 하위 0.5% 분위수, 단 −0.3% 보다 높으면 −0.3% (왕복 비용 0.1%+스프레드를
           넘을 수 없는 '충격' 은 제외). 종목별 쿨다운 60초 (같은 사건 중복 집계 금지).
  진입 A : 충격 감지 체결 시각 + 300ms 이후 첫 호가 스냅샷에서 ask 쪽 10만원 VWAP 매수 (즉시 진입 대조군)
  진입 B : 충격 뒤 '소진 확인' — 최근 2초 체결의 ASK 비중 < 50% 이고 충격 이후 최저가가 2초 이상 갱신되지 않음 → 그 시각
           + 300ms 이후 첫 호가에서 매수. 10초 안에 확인 안 되면 미진입(개수 보고). ★ 1차 가설
  진입 C : B 와 같되, 진입 호가의 상위 5호가 매수 잔량(원) ≥ 충격 전 60초 호가 샘플의 중앙값 (유동성 회복 확인)
  청산   : 진입 + 5/15/30/60초 이후 첫 호가에서 같은 수량 bid 쪽 VWAP 매도. 수수료 0.05%/편도. 깊이 부족·수신 끊김은 미해결로 제외·보고.
  1차 판정 셀 = (B, 15초). 나머지 11셀은 2차 (통과해도 '2차 PASS → 전방 확인 필요').
  구간   : 날짜 정렬 후 앞 10일 TRAIN(θ 산출·탐색) / 나머지 HOLDOUT(판정). 이 아카이브는 여러 연구에 쓰였으므로 HOLDOUT PASS 도
           '후보' 이며 전방 수집으로 다시 본다.
  합격(HOLDOUT 셀): n ≥ 50 · 평균 > 0 · 날짜 블록 부트스트랩 90% CI 하한 > 0 · 상위 5건 제외 평균 > 0 · 양수 날 ≥ 80%.
  보고   : 셀별 n·평균·중앙값·승률·CI·ex-top5·양수날, 비용 전 mid 변화(반등 크기 자체), 기회·확인·타임아웃·깊이거절·미해결 개수.

실행 (PC, ws 아카이브 폴더를 위치 인자로):
  py -X utf8 -u shock_exhaustion_replay.py ws_server\ws -o se_result
  (메신저로 명령을 옮기면 '--' 가 '—' 로 바뀌므로 '--root' 대신 위치 인자 또는 -r 을 쓴다)
  (전체 15일 ≈ 1~3시간. --days 5 로 뒤 5일만 먼저 돌려 파이프라인 확인 가능 — 단 그 경우 TRAIN 이 없어 판정 없음)
"""
import argparse
import collections
import gzip
import json
import random
import re
import statistics
import sys
from pathlib import Path

FEE = 0.0005
NOTIONAL = 100_000
LAT_MS = 300


def _mean(v):
    """statistics.fmean 대체 (PC 의 Python 3.7 호환)."""
    v = list(v)
    return sum(v) / len(v)
SHOCK_WIN_MS, SELL_SHARE_MIN, MIN_TRADES = 10_000, 0.70, 5
EXH_WIN_MS, EXH_MAX_WAIT_MS = 2_000, 10_000
DEPTH_BASE_MS, DEPTH_LEVELS, DEPTH_SAMPLE_MS = 60_000, 5, 1_000
HORIZONS_S = (5, 15, 30, 60)
COOLDOWN_MS = 60_000
DROP_Q, DROP_FLOOR = 0.005, -0.003
TRAIN_DAYS, MIN_EVENTS = 10, 50
PRIMARY = ("B_exhaust", 15)
MAX_GAP_MS = 5_000
VARIANTS = ("A_immediate", "B_exhaust", "C_exhaust_depth")

_TYPE_RE = re.compile(rb'"type":\s*"(trade|orderbook)"')
_CODE_RE = re.compile(rb'"code":\s*"([A-Z0-9-]+)"')
_RECV_RE = re.compile(rb'"recv_ts":\s*(\d+)')


# ───────── pure logic ─────────
def buy_vwap(units, krw):
    """asks 로 krw 만큼 매수 → 수량 (깊이 부족 None)."""
    remain, qty = krw, 0.0
    for u in sorted(units, key=lambda x: float(x["ask_price"])):
        p, s = float(u["ask_price"]), float(u["ask_size"])
        if p * s >= remain:
            return qty + remain / p
        qty += s
        remain -= p * s
    return None


def sell_vwap(units, qty):
    remain, krw = qty, 0.0
    for u in sorted(units, key=lambda x: -float(x["bid_price"])):
        p, s = float(u["bid_price"]), float(u["bid_size"])
        if s >= remain:
            return krw + remain * p
        krw += s * p
        remain -= s
    return None


def bid_depth_krw(units, levels=DEPTH_LEVELS):
    return sum(float(u["bid_price"]) * float(u["bid_size"]) for u in
               sorted(units, key=lambda x: -float(x["bid_price"]))[:levels])


def net_return(cost_krw, proceeds_krw):
    return proceeds_krw * (1 - FEE) / (cost_krw * (1 + FEE)) - 1


def window_stats(win):
    """win: deque[(ts, price, notional, is_sell)] → (ret, sell_share)."""
    tot = sum(n for _, _, n, _ in win)
    sell = sum(n for _, _, n, s in win if s)
    return win[-1][1] / win[0][1] - 1, (sell / tot if tot > 0 else 0.0)


def threshold(returns, q=DROP_Q, floor=DROP_FLOOR):
    """TRAIN 10초 수익률 → θ = min(하위 q 분위수, floor)."""
    if not returns:
        return floor
    s = sorted(returns)
    return min(s[int(q * (len(s) - 1))], floor)


def exhausted(trades_since_shock, now, low_ts):
    """최근 2초 ASK 비중 < 50% 이고 충격 후 최저가가 2초 이상 미갱신."""
    recent = [t for t in trades_since_shock if t[0] > now - EXH_WIN_MS]
    tot = sum(n for _, _, n, _ in recent)
    if tot <= 0:
        return False
    sell = sum(n for _, _, n, s in recent if s)
    return sell / tot < 0.5 and now - low_ts >= EXH_WIN_MS


def ci_low_days(rows, n=2000, seed=5):
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r["net"])
    days = list(by_day.values())
    if len(days) < 2:
        return float("nan")
    rnd = random.Random(seed)
    means = []
    for _ in range(n):
        pool = [x for d in rnd.choices(days, k=len(days)) for x in d]
        means.append(_mean(pool))
    means.sort()
    return means[int(0.05 * n)]


def summarize(rows):
    v = [r["net"] for r in rows]
    if not v:
        return {"n": 0}
    s = sorted(v)
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r["net"])
    day_means = [_mean(x) for x in by_day.values()]
    return {"n": len(v), "mean": _mean(v), "median": statistics.median(v),
            "win": sum(x > 0 for x in v) / len(v), "ci_low": ci_low_days(rows),
            "ex_top5": _mean(s[:-5]) if len(s) > 5 else float("nan"),
            "days": len(day_means), "days_pos": sum(m > 0 for m in day_means),
            "mid_ret": _mean(r["mid_ret"] for r in rows)}


def verdict(s):
    if s["n"] < MIN_EVENTS:
        return "INSUFFICIENT"
    ok = (s["mean"] > 0 and s["ci_low"] > 0 and s["ex_top5"] > 0 and s["days_pos"] >= 0.8 * s["days"])
    return "PASS" if ok else "FAIL"


# ───────── archive streaming ─────────
def archive_files(root, days=None):
    files = sorted(Path(root).rglob("*.jsonl.gz"))
    if not files:
        raise ValueError("no .jsonl.gz under " + str(root))
    dates = sorted({p.parent.name for p in files})
    if days:
        dates = dates[-days:]
        files = [p for p in files if p.parent.name in dates]
    return files, dates


def iter_lines(files):
    """(date, raw_bytes) — 원자료 1행."""
    for p in files:
        with gzip.open(p, "rb") as fh:
            for raw in fh:
                yield p.parent.name, raw


def _ts(m):
    return int(m.get("trade_timestamp") or m["timestamp"]) if m.get("type") == "trade" else int(m["timestamp"])


def pass_thresholds(files):
    """TRAIN: 종목별 10초 수익률 분포 → θ[m]. trade 행만 파싱."""
    win, rets = {}, collections.defaultdict(list)
    for _, raw in iter_lines(files):
        tm = _TYPE_RE.search(raw)
        if not tm or tm.group(1) != b"trade":
            continue
        m = json.loads(raw)
        if m.get("_seq_anomaly"):
            continue
        code, ts, p = m["code"], _ts(m), float(m["trade_price"])
        q = win.setdefault(code, collections.deque())
        while q and q[0][0] < ts - SHOCK_WIN_MS:
            q.popleft()
        q.append((ts, p))
        if len(q) >= MIN_TRADES:
            r = p / q[0][1] - 1
            if r <= -0.0005:                           # 꼬리만 저장 (분위수 0.5% 는 이 안에 있음; 없으면 floor 적용)
                rets[code].append(r)
    return {c: threshold(v) for c, v in rets.items()}


def replay(files, theta, counters, default_theta=None):
    """→ rows [{variant, horizon, market, date, net, mid_ret}]. default_theta: TRAIN 없는 탐색 실행에서만 floor 사용."""
    rows = []
    win = collections.defaultdict(collections.deque)            # market → (ts, price, notional, is_sell)
    depth = collections.defaultdict(collections.deque)          # market → (ts, bid5_krw)
    last_depth_sample = collections.defaultdict(lambda: -10**18)
    last_opp = collections.defaultdict(lambda: -10**18)
    opps = collections.defaultdict(list)                        # market → open opportunities
    last_recv = None

    def invalidate(reason):
        for lst in opps.values():
            for o in lst:
                counters["unresolved_" + reason] += 1 + len(o["legs"])
        opps.clear(); win.clear(); depth.clear()

    for date, raw in iter_lines(files):
        rm = _RECV_RE.search(raw)
        if rm is None:
            continue
        recv = int(rm.group(1))
        if last_recv is not None and recv - last_recv > MAX_GAP_MS:
            invalidate("gap")
        last_recv = recv
        if b'"_meta"' in raw:
            invalidate("session")
            continue
        tm, cm = _TYPE_RE.search(raw), _CODE_RE.search(raw)
        if not tm or not cm:
            continue
        kind, market = tm.group(1), cm.group(1).decode()
        if kind == b"orderbook":
            need = bool(opps[market]) or (recv - last_depth_sample[market] >= DEPTH_SAMPLE_MS)
            if not need:
                continue
            m = json.loads(raw)
            ts, units = int(m["timestamp"]), m.get("orderbook_units") or []
            if not units:
                continue
            if recv - last_depth_sample[market] >= DEPTH_SAMPLE_MS:
                last_depth_sample[market] = recv
                dq = depth[market]
                dq.append((ts, bid_depth_krw(units)))
                while dq and dq[0][0] < ts - DEPTH_BASE_MS:
                    dq.popleft()
            for o in opps[market]:
                # entries
                for var, tgt in list(o["enter"].items()):
                    if ts < tgt:
                        continue
                    del o["enter"][var]
                    if var == "C_exhaust_depth" and not (o["depth_base"] is not None and
                                                         bid_depth_krw(units) >= o["depth_base"]):
                        counters["C_depth_reject"] += 1
                        continue
                    qty = buy_vwap(units, NOTIONAL)
                    if qty is None:
                        counters["entry_depth_insufficient"] += 1
                        continue
                    counters["entries_" + var] += 1
                    mid0 = (float(units[0]["ask_price"]) + float(units[0]["bid_price"])) / 2
                    for h in HORIZONS_S:
                        o["legs"].append({"var": var, "h": h, "qty": qty, "cost": NOTIONAL, "mid0": mid0,
                                          "exit_ts": ts + h * 1000})
                # exits
                for leg in list(o["legs"]):
                    if ts < leg["exit_ts"]:
                        continue
                    o["legs"].remove(leg)
                    proceeds = sell_vwap(units, leg["qty"])
                    if proceeds is None:
                        counters["exit_depth_insufficient"] += 1
                        continue
                    mid = (float(units[0]["ask_price"]) + float(units[0]["bid_price"])) / 2
                    rows.append({"variant": leg["var"], "horizon": leg["h"], "market": market, "date": date,
                                 "net": net_return(leg["cost"], proceeds), "mid_ret": mid / leg["mid0"] - 1})
            opps[market] = [o for o in opps[market] if o["enter"] or o["legs"] or o["wait"]]
            continue
        # trade
        m = json.loads(raw)
        if m.get("_seq_anomaly"):
            continue
        ts, p, vol = _ts(m), float(m["trade_price"]), float(m["trade_volume"])
        is_sell = m.get("ask_bid") == "ASK"
        q = win[market]
        while q and q[0][0] < ts - SHOCK_WIN_MS:
            q.popleft()
        q.append((ts, p, p * vol, is_sell))
        # exhaustion checks for waiting opportunities
        for o in opps[market]:
            if not o["wait"]:
                continue
            o["since"].append((ts, p, p * vol, is_sell))
            if p < o["low"]:
                o["low"], o["low_ts"] = p, ts
            if ts - o["t_shock"] > EXH_MAX_WAIT_MS:
                o["wait"] = False
                counters["B_timeout"] += 1
            elif exhausted(o["since"], ts, o["low_ts"]):
                o["wait"] = False
                counters["B_confirmed"] += 1
                o["enter"]["B_exhaust"] = ts + LAT_MS
                o["enter"]["C_exhaust_depth"] = ts + LAT_MS
        # new shock?
        th = theta.get(market, default_theta)
        if th is None or len(q) < MIN_TRADES or ts - last_opp[market] < COOLDOWN_MS:
            continue
        ret, share = window_stats(q)
        if ret <= th and share >= SELL_SHARE_MIN:
            last_opp[market] = ts
            counters["opportunities"] += 1
            base = [d for _, d in depth[market]]
            opps[market].append({"t_shock": ts, "low": min(x[1] for x in q), "low_ts": ts, "wait": True,
                                 "since": [(ts, p, p * vol, is_sell)], "legs": [],
                                 "enter": {"A_immediate": ts + LAT_MS},
                                 "depth_base": statistics.median(base) if base else None})
    invalidate("end")
    return rows


def analyze(rows, train_dates):
    out = []
    train = set(train_dates)
    for phase in ("TRAIN", "HOLDOUT"):
        for var in VARIANTS:
            for h in HORIZONS_S:
                sel = [r for r in rows if r["variant"] == var and r["horizon"] == h and ((r["date"] in train) == (phase == "TRAIN"))]
                s = summarize(sel)
                s.update(phase=phase, variant=var, horizon=h,
                         verdict=(verdict(s) if phase == "HOLDOUT" else "(explore)"),
                         primary=(var, h) == PRIMARY)
                out.append(s)
    return out


def _fmt(s):
    if s["n"] == 0:
        return f"{0:>5} {'n/a':>7} {'n/a':>7} {'n/a':>5} {'n/a':>7} {'n/a':>7} {'n/a':>6} {'n/a':>7}"
    return (f"{s['n']:>5} {s['mean']*100:>+7.3f} {s['median']*100:>+7.3f} {s['win']*100:>5.0f} {s['ci_low']*100:>+7.3f} "
            f"{s['ex_top5']*100:>+7.3f} {s['days_pos']:>2}/{s['days']:<3} {s['mid_ret']*100:>+7.3f}")


def main(argv=None):
    # 메신저 경유 복사 시 '--' 가 '—' 로 바뀌는 문제 → 위치 인자와 한 글자 옵션(-r/-o/-d/-t)도 받는다
    ap = argparse.ArgumentParser()
    ap.add_argument("root_pos", nargs="?", help="ws 아카이브 폴더 (위치 인자)")
    ap.add_argument("-r", "--root", dest="root_opt")
    ap.add_argument("-o", "--out", default="se_result")
    ap.add_argument("-d", "--days", type=int, default=0, help="뒤 N일만 (파이프라인 확인용)")
    ap.add_argument("-t", "--train-days", type=int, default=TRAIN_DAYS)
    a = ap.parse_args(argv)
    root = a.root_opt or a.root_pos
    if not root:
        ap.error("아카이브 폴더를 지정하세요: shock_exhaustion_replay.py ws_server\\ws  (또는 -r ws_server\\ws)")
    files, dates = archive_files(root, a.days or None)
    train_dates = dates[:a.train_days] if len(dates) > a.train_days else []
    print(f"SHOCK→EXHAUSTION REPLAY (pre-registered 2026-10-24) | {len(files)} files | dates {dates[0]}..{dates[-1]} "
          f"({len(dates)}) | TRAIN {len(train_dates)} days | HOLDOUT {len(dates) - len(train_dates)} days", flush=True)
    if not train_dates:
        print("no TRAIN days → θ 산출 불가 → 판정 없음 (floor -0.3% 로 탐색만)")
    theta = pass_thresholds([p for p in files if p.parent.name in train_dates]) if train_dates else {}
    print("θ by market: " + (" ".join(f"{m}={v*100:.2f}%" for m, v in sorted(theta.items())[:25]) if theta
                             else f"floor {DROP_FLOOR*100:.1f}% (no TRAIN)"), flush=True)
    counters = collections.Counter()
    rows = replay(files, theta, counters, default_theta=None if train_dates else DROP_FLOOR)
    print("\ncounters: " + " ".join(f"{k}={v}" for k, v in sorted(counters.items())))
    res = analyze(rows, train_dates)
    print(f"\n{'phase':<8} {'variant':<16} {'h':>3} {'n':>5} {'mean%':>7} {'med%':>7} {'win%':>5} {'ciLow%':>7} {'exTop5%':>7} "
          f"{'days+':>6} {'mid%':>7}  verdict")
    for s in res:
        print(f"{s['phase']:<8} {s['variant']:<16} {s['horizon']:>3} {_fmt(s)}  {s['verdict']}{' ★primary' if s['primary'] else ''}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"dates": dates, "train_dates": train_dates, "theta": theta,
                                                 "counters": dict(counters), "cells": res}, indent=1))
    (out / "rows.json").write_text(json.dumps(rows))
    print(f"\nResults: {out}/report.json {out}/rows.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
