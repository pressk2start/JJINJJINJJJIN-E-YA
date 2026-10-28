#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spread_survey — 레코더 종목 선정용 실시간 호가 비용 실측 (공개 REST만, 주문·키 없음, 표준 라이브러리, Python 3.7+).

왜: 15일 아카이브 5종목 중 4종목(DOGE·ADA·XLM·XPL)은 1원 호가 단위 구간이라 최우선 스프레드가 30~90bp 였고,
    캐시 감사(2026-10-27)에서 종목별 15초 순손익이 '가격 그대로 왕복 비용(flatCost)' 과 거의 같았다
    (DOGE -0.956% vs 95.7bp, XRP -0.159% vs 15.8bp). 초 단위 연구의 병목은 지표가 아니라 시장 선택이다.
    다음 녹화 종목은 추정이 아니라 여러 시간대의 실측으로 고른다.

측정 (매 표본, 종목별): 최우선 스프레드(bp), 10만원·100만원 시장가 매수/매도 가격충격(bp, 최우선 대비 VWAP),
    최우선 잔량(작은 쪽, 원), 호가 timestamp 나이(ms, 조회 시각 기준).
요약: 종목별 스프레드 중앙값·p90, 충격 중앙값, 잔량 중앙값, KST 시간대별 스프레드 중앙값.
    기준(조언자 초안, 수집 대상 선별용이며 수익성 기준 아님): 중앙값 <= 5bp · p90 <= 10bp · 10만원 충격 <= 2bp.
    ETH·XRP·SOL 처럼 스프레드가 항상 1틱인 종목은 5bp 를 구조적으로 못 넘으므로 '1틱 고정' 여부를 함께 표시한다.

실행 (PC, upbit-research 폴더에서. PC 절전 끄기):
  py -3 -X utf8 -u spread_survey.py -H 8 -s 120      (8시간, 2분 간격. Ctrl+C 로 중간에 멈춰도 요약은 만든다)
  py -3 -X utf8 spread_survey.py -S                    (이미 쌓인 표본으로 요약만 다시)
출력: spread_survey\samples.csv (누적), spread_survey\summary.txt
"""
import argparse
import csv
import datetime as dt
import json
import math
import statistics
import sys
import time
import urllib.request
from pathlib import Path

UPBIT = "https://api.upbit.com/v1"
MARKETS = ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-NEAR", "KRW-SUI", "KRW-BCH",
           "KRW-DOGE", "KRW-ADA", "KRW-XLM")
KST = dt.timezone(dt.timedelta(hours=9))
FIELDS = ("kst", "market", "spread_bp", "spread_ticks", "imp_buy_100k", "imp_sell_100k", "imp_buy_1m", "imp_sell_1m",
          "touch_krw", "book_age_ms")


def _get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


# ───────── pure logic ─────────
def impact_bp(units, krw, side):
    """최우선 대비 시장가 VWAP 불리폭(bp). 30호가로 부족하면 None."""
    if side == "buy":
        levels = sorted(((float(u["ask_price"]), float(u["ask_size"])) for u in units), key=lambda x: x[0])
    else:
        levels = sorted(((float(u["bid_price"]), float(u["bid_size"])) for u in units), key=lambda x: -x[0])
    if not levels:
        return None
    remain, qty = float(krw), 0.0
    for p, s in levels:
        if p * s >= remain:
            qty += remain / p
            remain = 0.0
            break
        qty += s
        remain -= p * s
    if remain > 0 or qty <= 0:
        return None
    return abs((krw / qty) / levels[0][0] - 1) * 1e4


def tick_of(units):
    """최우선 호가 바로 옆 간격으로 추정한 호가 단위 = min(ask2-ask1, bid1-bid2).
    30호가 전체를 쓰면 100원·1,000원 같은 단위 경계를 넘는 깊은 호가 때문에 틀린다 (DOGE 104원 → 99.9원 구간 0.1원)."""
    asks = sorted(float(u["ask_price"]) for u in units)
    bids = sorted((float(u["bid_price"]) for u in units), reverse=True)
    gaps = [g for g in ((asks[1] - asks[0]) if len(asks) > 1 else None,
                        (bids[0] - bids[1]) if len(bids) > 1 else None) if g and g > 0]
    return round(min(gaps), 10) if gaps else None


def book_row(book, now_ms, kst_label):
    u = book.get("orderbook_units") or []
    if not u:
        return None
    a, b = float(u[0]["ask_price"]), float(u[0]["bid_price"])
    if a <= b or b <= 0:
        return None
    tick = tick_of(u)
    return {"kst": kst_label, "market": book["market"], "spread_bp": (a - b) / ((a + b) / 2) * 1e4,
            "spread_ticks": (a - b) / tick if tick else None,
            "imp_buy_100k": impact_bp(u, 100_000, "buy"), "imp_sell_100k": impact_bp(u, 100_000, "sell"),
            "imp_buy_1m": impact_bp(u, 1_000_000, "buy"), "imp_sell_1m": impact_bp(u, 1_000_000, "sell"),
            "touch_krw": min(float(u[0]["ask_size"]) * a, float(u[0]["bid_size"]) * b),
            "book_age_ms": max(0, now_ms - int(book.get("timestamp") or now_ms))}


def _q(v, q):
    """올림 분위수 — 표본이 적어도 p90 이 중앙값보다 작게 나오지 않게."""
    s = sorted(v)
    return s[min(len(s) - 1, int(math.ceil(q * (len(s) - 1))))] if s else None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def summarize(rows, med_max=5.0, p90_max=10.0, imp_max=2.0):
    """rows: dict 들(csv 에서 읽은 문자열도 허용) → (종목별 결과 list, 출력 줄 list)."""
    by = {}
    for r in rows:
        by.setdefault(r["market"], []).append(r)
    res, lines = [], []
    lines.append("market     n  spr_med  spr_p90  1tick  imp10만(b/s)  imp100만(b/s)  touch_med(만원)  verdict  | KST hour: spread_med_bp")
    for m in sorted(by, key=lambda k: statistics.median(_f(x["spread_bp"]) for x in by[k])):
        g = by[m]
        sp = [_f(x["spread_bp"]) for x in g]
        sp = [x for x in sp if x is not None]
        if not sp:
            continue
        med, p90 = statistics.median(sp), _q(sp, 0.9)
        ticks = [_f(x.get("spread_ticks")) for x in g if _f(x.get("spread_ticks")) is not None]
        one_tick = bool(ticks) and statistics.median(ticks) <= 1.05     # 스프레드가 한 틱 = 더 좁아질 수 없음
        def mid(key):
            v = [_f(x[key]) for x in g if _f(x[key]) is not None]
            return statistics.median(v) if v else None
        ib, isl, ib1, is1, touch = mid("imp_buy_100k"), mid("imp_sell_100k"), mid("imp_buy_1m"), mid("imp_sell_1m"), mid("touch_krw")
        ok = (med <= med_max and p90 <= p90_max and ib is not None and isl is not None and ib <= imp_max and isl <= imp_max)
        verdict = "PASS" if ok else ("1TICK" if one_tick and p90 <= p90_max and ib is not None and ib <= imp_max else "fail")
        hours = {}
        for x in g:
            hours.setdefault(str(x["kst"])[6:8], []).append(_f(x["spread_bp"]))
        hour_txt = " ".join("{}:{:.0f}".format(h, statistics.median([v for v in vs if v is not None]))
                            for h, vs in sorted(hours.items()))
        res.append({"market": m, "n": len(g), "spread_med": med, "spread_p90": p90, "one_tick": one_tick,
                    "imp_buy_100k": ib, "imp_sell_100k": isl, "imp_buy_1m": ib1, "imp_sell_1m": is1,
                    "touch_med_krw": touch, "verdict": verdict})
        fmt = lambda v: "n/a" if v is None else "{:.1f}".format(v)
        lines.append("{:<9} {:>3} {:>8.1f} {:>8.1f} {:>6} {:>6}/{:<6} {:>6}/{:<6} {:>15} {:>8}  | {}".format(
            m.replace("KRW-", ""), len(g), med, p90, "Y" if one_tick else "-", fmt(ib), fmt(isl), fmt(ib1), fmt(is1),
            "n/a" if touch is None else "{:,.0f}".format(touch / 1e4), verdict, hour_txt))
    lines.append("criteria: med<={} p90<={} imp10만<={}bp | 1TICK = 스프레드 중앙값이 한 틱(호가 단위가 하한), 그 외 기준 충족".format(
        med_max, p90_max, imp_max))
    lines.append("왕복 비용 하한 = 스프레드 + 수수료 10bp(0.05%x2). 수집 대상 선별용이며 수익성 판정 아님.")
    return res, lines


def write_summary(out_dir, rows):
    res, lines = summarize(rows)
    span = "{}..{}".format(rows[0]["kst"], rows[-1]["kst"]) if rows else "no data"
    lines.insert(0, "SPREAD SURVEY | samples={} | KST {}".format(len(rows), span))
    (out_dir / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(lines), flush=True)


def read_rows(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-H", "--hours", type=float, default=8.0)
    ap.add_argument("-s", "--every", type=float, default=120.0, help="표본 간격(초)")
    ap.add_argument("-m", "--markets", default=",".join(MARKETS))
    ap.add_argument("-o", "--out", default="spread_survey")
    ap.add_argument("-S", "--summary-only", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "samples.csv"
    if a.summary_only:
        write_summary(out, read_rows(path))
        return 0
    markets = [m.strip() for m in a.markets.split(",") if m.strip()]
    if a.every < 10:
        ap.error("간격은 10초 이상 (공개 API 요청 제한)")
    new = not path.exists()
    fh = path.open("a", newline="", encoding="utf-8")
    w = csv.DictWriter(fh, fieldnames=FIELDS)
    if new:
        w.writeheader()
    end, n, errors = time.time() + a.hours * 3600, 0, 0
    print("SPREAD SURVEY start | {} markets | every {}s for {}h | Ctrl+C = stop + summary".format(
        len(markets), a.every, a.hours), flush=True)
    try:
        while time.time() < end:
            t0 = time.time()
            try:
                books = _get(UPBIT + "/orderbook?markets=" + ",".join(markets))
                now_ms = int(time.time() * 1000)
                label = dt.datetime.now(KST).strftime("%m-%d %H:%M")
                for b in books:
                    r = book_row(b, now_ms, label)
                    if r:
                        w.writerow(r)
                fh.flush()
                n += 1
                if n % 15 == 0:
                    print("[{}] rounds={} errors={}".format(label, n, errors), flush=True)
            except Exception as e:   # 네트워크 오류는 그 회차만 건너뛴다
                errors += 1
                if errors <= 3:
                    print("[warn] {}: {}".format(type(e).__name__, str(e)[:120]), flush=True)
            time.sleep(max(0.0, a.every - (time.time() - t0)))
    except KeyboardInterrupt:
        print("stopped by user", flush=True)
    finally:
        fh.close()
    write_summary(out, read_rows(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
