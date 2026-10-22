#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""leadlag_test — 바이낸스 선행 → 업비트 후행 가설 (사전 고정 2026-10-22). 주문·키 없음, 표준 라이브러리만.

질문: 바이낸스에서 먼저 오른 뒤, 그걸 보고 업비트에서 사서 비용을 내고도 남는가.
데이터 (둘 다 이미 있거나 무료):
  업비트: ws_recorder 가 모은 WS 원자료 (orderbook 스냅샷) — PC 의 ws_closed 폴더
  바이낸스: data.binance.vision 공개 아카이브의 1초봉 (KRW-XXX → XXXUSDT)
사전 고정 규칙 (숫자 탐색 금지):
  신호   : 바이낸스 1초봉 종가가 직전 10초 대비 +0.15% 이상 상승 (그 1초봉이 닫힌 순간에만 판단 가능)
           (임계값은 손익을 보기 전, 실제 1일치 신호 '빈도'만 보고 고정: DOGE 10/10 기준 0.30%=0건/일,
            0.15%=4건/일, 0.10%=19건/일. 0.30% 는 표본이 안 나와 판정 불가라 0.15% 로 정함)
  중복   : 같은 종목 신호 후 60초 무시. 매수만 (업비트 현물은 공매도 불가)
  진입   : 신호 + 지연 L 이후 첫 업비트 호가에서 매도호가 깊이로 6,000원 시장가 매수
           L = 300ms (기본) / 1000ms (스트레스). 그 호가가 목표보다 1초 넘게 늦으면 unknown
  청산   : 진입 + H 초 이후 첫 업비트 호가에서 매수호가 깊이로 전량 시장가 매도, H = 5 / 10 / 30 / 60초
  비용   : 수수료 편도 0.05%. 스프레드·호가 깊이는 매수/매도 가격에 이미 반영 (이중 차감 안 함)
  구간   : 업비트 날짜 앞 70% TRAIN(탐색) / 뒤 30% HOLDOUT(판정)
  합격   : HOLDOUT, L=300ms 에서 — n ≥ 100 · 비용 후 평균 > 0 · 부트스트랩 90% CI 하한 > 0 ·
           최대 1건 제외 평균 > 0 · 같은 H 의 L=1000ms 평균도 > 0
진단 출력 (판정과 별개):
  mid%     : 진입~청산 사이 업비트 중간가 변화 (비용 0). 양수면 '따라가긴 한다', 음수면 '따라가지도 않는다'
  pre_up%  : 신호 순간까지 업비트가 이미 직전 10초 동안 오른 폭. 바이낸스만큼 이미 올랐으면 시차가 없는 것
제약: 거래소 시계(서로 다른 서버)의 오차, 실제 네트워크 지연은 L 가정으로만 반영. LIVE 규칙 아님.

실행 (PC):
  py leadlag_test.py ws_closed --out ll_result
"""
import argparse
import collections
import csv
import datetime as dt
import gzip
import io
import json
import math
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

THETA = 0.0015
WINDOW_S = 10
COOLDOWN_MS = 60_000
LATENCIES_MS = (300, 1000)
HORIZONS_S = (5, 10, 30, 60)
FEE = 0.0005
NOTIONAL = 6000.0
MAX_QUOTE_LATE_MS = 1000
MAX_EXIT_LATE_MS = 2000
MAX_GAP_MS = 5000
TRAIN_FRAC = 0.7
BN_URL = "https://data.binance.vision/data/spot/daily/klines/{s}/1s/{s}-1s-{d}.zip"


# ───────── 바이낸스 1초봉 ─────────
def _norm_ms(t):
    t = int(float(t))
    return t // 1000 if t > 10 ** 14 else t          # 2025년 이후 spot 아카이브는 마이크로초


def parse_kline_csv(text):
    """[(open_ms, close)] — 헤더/잘못된 줄은 건너뜀."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 5:
            continue
        try:
            out.append((_norm_ms(row[0]), float(row[4])))
        except ValueError:
            continue
    return out


def load_binance_day(sym, day, cache):
    p = Path(cache) / f"{sym}-1s-{day}.json"
    if p.exists():
        return [tuple(x) for x in json.loads(p.read_text())]
    url = BN_URL.format(s=sym, d=day)
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as r:
            blob = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        rows = parse_kline_csv(z.read(z.namelist()[0]).decode("utf-8", "replace"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows))
    return rows


def binance_signals(bars, theta=THETA, window_s=WINDOW_S, cooldown_ms=COOLDOWN_MS):
    """bars: [(open_ms, close)] 오름차순. 반환 [(signal_ms, ret)] — signal_ms = 그 1초봉이 닫힌 시각."""
    sigs, last_sig = [], None
    hist = collections.deque()                         # (open_ms, close)
    w = window_s * 1000
    for t, c in bars:
        hist.append((t, c))
        # hist[0] = window 이전 시점에서 마지막으로 알려진 종가 (forward-fill 기준가)
        while len(hist) >= 2 and hist[1][0] <= t - w:
            hist.popleft()
        if hist[0][0] > t - w or hist[0][1] <= 0:
            continue
        ref = hist[0][1]
        ret = c / ref - 1
        sig_ms = t + 1000
        if ret >= theta and (last_sig is None or sig_ms - last_sig >= cooldown_ms):
            sigs.append((sig_ms, ret))
            last_sig = sig_ms
    return sigs


# ───────── 업비트 체결 시뮬레이션 ─────────
def _buy(units, krw):
    remaining, qty = krw, 0.0
    for u in sorted(units, key=lambda x: float(x["ask_price"])):
        p, s = float(u["ask_price"]), float(u["ask_size"])
        take = min(remaining, p * s)
        qty += take / p
        remaining -= take
        if remaining <= 1e-9:
            return qty
    return None


def _sell(units, qty):
    remaining, krw = qty, 0.0
    for u in sorted(units, key=lambda x: -float(x["bid_price"])):
        p, s = float(u["bid_price"]), float(u["bid_size"])
        take = min(remaining, s)
        krw += take * p
        remaining -= take
        if remaining <= 1e-12:
            return krw
    return None


def _mid(units):
    a, b = float(units[0]["ask_price"]), float(units[0]["bid_price"])
    return (a + b) / 2 if a > 0 and b > 0 and a >= b else None


def simulate(files, signals_by_market):
    """files: 업비트 jsonl.gz 경로(시간순). signals_by_market: {KRW-XXX: [(sig_ms, ret)] 오름차순}."""
    rows, counters = [], collections.Counter()
    queues = {m: list(s) for m, s in signals_by_market.items()}
    qpos = {m: 0 for m in queues}
    mids = collections.defaultdict(collections.deque)   # market -> (ex_ms, mid)
    pending = collections.defaultdict(list)
    last_recv = None

    def invalidate(reason):
        for lst in pending.values():
            for p in lst:
                for h in HORIZONS_S:
                    if h not in p["done"]:
                        rows.append(dict(p["base"], latency_ms=p["L"], hold_s=h, unknown=reason))
        pending.clear()
        mids.clear()

    for path in files:
        date = Path(path).parent.name
        with gzip.open(path, "rb") as fh:
            for raw in fh:
                try:
                    m = json.loads(raw)
                    recv = int(m["recv_ts"])
                except (ValueError, KeyError, TypeError):
                    counters["bad_line"] += 1
                    continue
                if last_recv is not None and recv - last_recv > MAX_GAP_MS:
                    invalidate("receive_gap")
                last_recv = recv
                if "_meta" in m:
                    if m["_meta"] in ("connect", "disconnect", "recv_timeout", "disk_stop"):
                        invalidate("session_boundary")
                    continue
                mk = m.get("code")
                if m.get("type") != "orderbook" or mk not in queues:
                    continue
                units = m.get("orderbook_units") or []
                if not units:
                    continue
                T = int(m.get("timestamp", recv))
                mid = _mid(units)
                if mid is None:
                    continue
                dq = mids[mk]
                dq.append((T, mid))
                while dq and dq[0][0] < T - 20_000:
                    dq.popleft()
                # 신호 활성화 (신호 시각 ≤ 현재 호가 시각)
                q, i = queues[mk], qpos[mk]
                while i < len(q) and q[i][0] <= T:
                    sig_ms, bret = q[i]
                    i += 1
                    if T - sig_ms > 60_000:                 # 그 시간대 업비트 기록 없음
                        counters["signal_without_upbit_data"] += 1
                        continue
                    now_mid = next((x[1] for x in reversed(dq) if x[0] <= sig_ms), None)
                    pre_mid = next((x[1] for x in reversed(dq) if x[0] <= sig_ms - WINDOW_S * 1000), None)
                    base = {"market": mk, "date": date, "signal_ms": sig_ms, "bn_ret_pct": bret * 100,
                            "pre_up_pct": (now_mid / pre_mid - 1) * 100 if now_mid and pre_mid else None}
                    counters["signals_activated"] += 1
                    for L in LATENCIES_MS:
                        pending[mk].append({"base": base, "L": L, "entry_due": sig_ms + L,
                                            "qty": None, "done": set()})
                qpos[mk] = i
                # 진입·청산 처리
                keep = []
                for p in pending[mk]:
                    if p["qty"] is None:
                        if T < p["entry_due"]:
                            keep.append(p)
                            continue
                        if T - p["entry_due"] > MAX_QUOTE_LATE_MS:
                            for h in HORIZONS_S:
                                rows.append(dict(p["base"], latency_ms=p["L"], hold_s=h, unknown="entry_quote_late"))
                            continue
                        qty = _buy(units, NOTIONAL)
                        if qty is None:
                            for h in HORIZONS_S:
                                rows.append(dict(p["base"], latency_ms=p["L"], hold_s=h, unknown="entry_depth"))
                            continue
                        p.update(qty=qty, entry_ts=T, entry_mid=mid)
                    for h in HORIZONS_S:
                        if h in p["done"] or T < p["entry_ts"] + h * 1000:
                            continue
                        p["done"].add(h)
                        r = dict(p["base"], latency_ms=p["L"], hold_s=h)
                        if T - (p["entry_ts"] + h * 1000) > MAX_EXIT_LATE_MS:
                            r["unknown"] = "exit_quote_late"
                        else:
                            krw = _sell(units, p["qty"])
                            if krw is None:
                                r["unknown"] = "exit_depth"
                            else:
                                cost = NOTIONAL * (1 + FEE)
                                r["net_pct"] = (krw * (1 - FEE) / cost - 1) * 100
                                r["mid_pct"] = (mid / p["entry_mid"] - 1) * 100
                        rows.append(r)
                    if len(p["done"]) < len(HORIZONS_S):
                        keep.append(p)
                pending[mk] = keep
    invalidate("end_of_input")
    return rows, counters


# ───────── 통계·판정 ─────────
def bootstrap_ci(v, n=2000, seed=11):
    if len(v) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(v, k=len(v))) for _ in range(n))
    return means[int(0.05 * n)]


def summarize(rows, days):
    known = [r for r in rows if "net_pct" in r]
    s = {"n": len(known), "unknown": len(rows) - len(known)}
    if not known:
        return s
    v = sorted(r["net_pct"] for r in known)
    pre = [r["pre_up_pct"] for r in known if r.get("pre_up_pct") is not None]
    mean = statistics.mean(v)
    s.update(mean=mean, median=statistics.median(v), win=sum(x > 0 for x in v) / len(v),
             ci_low=bootstrap_ci(v), ex_top1=statistics.mean(v[:-1]) if len(v) > 1 else None,
             mid=statistics.mean(r["mid_pct"] for r in known),
             pre_up=statistics.mean(pre) if pre else None,
             bn_ret=statistics.mean(r["bn_ret_pct"] for r in known),
             per_day=len(known) / max(days, 1),
             krw_month=len(known) / max(days, 1) * 30 * mean / 100 * NOTIONAL)
    return s


def verdict(s300, s1000):
    if s300.get("n", 0) < 100 or s300.get("ci_low") is None:
        return "INSUFFICIENT"
    ok = (s300["mean"] > 0 and s300["ci_low"] > 0 and (s300["ex_top1"] or -1) > 0
          and s1000.get("mean") is not None and s1000["mean"] > 0)
    return "PASS" if ok else "FAIL"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ws_root")
    ap.add_argument("--cache", default="bn_cache")
    ap.add_argument("--out", default="ll_result")
    a = ap.parse_args(argv)
    files = sorted(Path(a.ws_root).rglob("*.jsonl.gz"))
    if not files:
        raise SystemExit("no .jsonl.gz under " + a.ws_root)
    dates = sorted({p.parent.name for p in files})
    split = dates[: max(1, int(len(dates) * TRAIN_FRAC))]
    train_dates = set(split)
    markets = set()
    with gzip.open(files[0], "rb") as fh:
        for k, raw in enumerate(fh):
            if k > 20000:
                break
            try:
                c = json.loads(raw).get("code")
            except ValueError:
                continue
            if c and c.startswith("KRW-"):
                markets.add(c)
    print(f"LEAD-LAG (pre-registered 2026-10-22) | upbit days {dates[0]}..{dates[-1]} ({len(dates)}) | markets {sorted(markets)}")
    d0 = dt.date.fromisoformat(dates[0]) - dt.timedelta(days=1)
    d1 = dt.date.fromisoformat(dates[-1]) + dt.timedelta(days=1)
    bn_days = [(d0 + dt.timedelta(days=k)).isoformat() for k in range((d1 - d0).days + 1)]
    signals, missing = {}, {}
    for mk in sorted(markets):
        sym = mk.split("-", 1)[1] + "USDT"
        bars = []
        for d in bn_days:
            try:
                day = load_binance_day(sym, d, a.cache)
            except Exception as e:
                day = None
                missing.setdefault(sym, []).append(f"{d}:{e}")
            if day is None:
                missing.setdefault(sym, []).append(d)
                continue
            bars.extend(day)
        bars.sort()
        if not bars:
            print(f"[binance] {sym}: no data (not listed?) → skip")
            continue
        signals[mk] = binance_signals(bars)
        print(f"[binance] {sym}: bars={len(bars)} signals={len(signals[mk])}")
    print("[upbit] replaying WS files (this is the slow part) ...")
    rows, counters = simulate(files, signals)
    for r in rows:
        r["phase"] = "TRAIN" if r["date"] in train_dates else "HOLDOUT"
    ndays = {"TRAIN": len(train_dates), "HOLDOUT": len(dates) - len(train_dates)}
    print("\nphase    L_ms  H_s    n  unk  net%    med%    win%  ci_low  exTop1  mid%    pre_up% bn_ret% /day   krw/mo  verdict")
    report = {"counters": dict(counters), "binance_missing": missing, "groups": []}
    for ph in ("TRAIN", "HOLDOUT"):
        for h in HORIZONS_S:
            ss = {}
            for L in LATENCIES_MS:
                ss[L] = summarize([r for r in rows if r["phase"] == ph and r["hold_s"] == h and r["latency_ms"] == L], ndays[ph])
            for L in LATENCIES_MS:
                s = ss[L]
                vd = verdict(ss[300], ss[1000]) if (ph == "HOLDOUT" and L == 300) else "-"
                s.update(phase=ph, latency_ms=L, hold_s=h, verdict=vd)
                report["groups"].append(s)
                f = lambda x, w=7, p=3: (f"{x:{w}.{p}f}" if isinstance(x, (int, float)) else " " * (w - 3) + "n/a")
                print(f"{ph:8} {L:5} {h:4} {s['n']:4} {s['unknown']:4} {f(s.get('mean'))} {f(s.get('median'))} "
                      f"{f((s.get('win') or 0) * 100, 6, 1)} {f(s.get('ci_low'))} {f(s.get('ex_top1'))} {f(s.get('mid'))} "
                      f"{f(s.get('pre_up'))} {f(s.get('bn_ret'))} {f(s.get('per_day'), 5, 1)} {f(s.get('krw_month'), 7, 0)}  {vd}")
    print("\nVerdict uses HOLDOUT, L=300ms only. mid% = Upbit mid move (no cost). pre_up% = Upbit move before signal.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    keys = sorted({k for r in rows for k in r})
    with (out / "rows.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"counters: {dict(counters)}")
    print(f"Results: {out / 'report.json'} {out / 'rows.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
