#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tri_arb_sample — G군 업비트 내부 삼각 차익 측정 (사전 고정 2026-10-24). 공개 호가만, 주문·키 없음, 표준 라이브러리만.

질문: KRW → BTC(또는 USDT) → 알트 → KRW 세 다리를 실측 호가 깊이로 체결했을 때, 모든 다리의 수수료를 뺀 뒤
      원화가 늘어나는 순간이 존재하는가. 존재하면 얼마나 자주·얼마나 오래(연속 샘플) 지속되는가.
왜 측정으로 끝나는가: 과거 동시 호가가 없어 TRAIN/HOLDOUT 이 불가능하다. 이 스크립트는 '지금부터' 표본을 모으는
      forward 측정이며, 양수가 나와도 전략 채택이 아니라 '더 긴 수집 가치 있음' 까지만 뜻한다.

규칙 (결과 보기 전 고정)
  유니버스  : KRW·BTC 마켓 양쪽에 있는 알트 중 KRW 24h 거래대금 상위 N(기본 30). USDT 마켓이 있으면 USDT 경로도 본다.
  경로      : via-BTC 정방향 KRW→BTC→X→KRW · 역방향 KRW→X→BTC→KRW · via-USDT 같은 두 경로 (총 최대 4)
  체결      : 매 샘플에서 한 번의 /v1/orderbook 응답(복수 마켓) 으로 세 마켓 호가를 동시에 받아 30호가 깊이 VWAP 로
              가상 체결. 깊이가 부족하면 그 크기는 '체결 불가'. 마켓별 timestamp 차이(skew) 기록.
  수수료    : KRW 마켓 0.05% · BTC/USDT 마켓 0.25% (업비트 공시 기준 예시) — 각 다리에서 곱셈 적용. 지연·가격변동 비용 0 가정
              (즉 아래 수치는 '상한'. 실제 세 다리는 순차 주문이라 1초 이상 걸리고 그 사이 가격이 움직인다)
  크기      : 6천 · 10만 · 100만원. (BTC/USDT 마켓 최소주문이 6천원보다 클 수 있어 6천은 참고치)
  판정(측정): 수수료 후 net > 0 인 샘플 비율, 최대값, 99분위, 가장 긴 연속 양수 샘플 수. 양수 샘플이 전체의 1% 미만이거나
              연속 지속이 1샘플(≈3초) 이하면 '실행 불가 → 종결'.

사전 점검 (2026-10-24, 30초 10샘플 스모크): 네 경로 모두 전부 음수. 최고 -0.31% (= 수수료 0.35% 가 비용 전 +0.04% 를 삼킴).
  BTC/USDT 마켓 알트 호가는 중앙값 -2~-37% 로 사실상 유동성 없음. 본 측정 60분은 이 뒤에 실행.
실행 (PC 또는 연구용 머신. 거래 서버 금지):
  py -u tri_arb_sample.py --minutes 60 --interval 3 --out tri_result
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

UPBIT = "https://api.upbit.com/v1"
FEE = {"KRW": 0.0005, "BTC": 0.0025, "USDT": 0.0025}
SIZES = (6_000, 100_000, 1_000_000)
TOP_N = 30


def _get(url, retries=4):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                        timeout=20) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1 + i)


# ───────── pure logic ─────────
def fill_buy(asks, quote_amount):
    """quote 통화 quote_amount 를 써서 asks([(price, size)...] 오름차순) 를 먹는다 → 받은 base 수량 (깊이 부족 None)."""
    remain, got = quote_amount, 0.0
    for p, s in asks:
        cost = p * s
        if cost >= remain:
            got += remain / p
            return got
        got += s
        remain -= cost
    return None


def fill_sell(bids, base_amount):
    """base base_amount 를 bids(내림차순) 에 판다 → 받은 quote (깊이 부족 None)."""
    remain, got = base_amount, 0.0
    for p, s in bids:
        if s >= remain:
            got += remain * p
            return got
        got += s * p
        remain -= s
    return None


def path_net(books, x, via, forward, krw):
    """KRW krw 로 시작해 via(BTC/USDT) 를 거쳐 x 를 사고팔아 KRW 로 돌아왔을 때의 순수익률. 불가 None.
    books[market] = {"asks": [(p, s)...], "bids": [(p, s)...]}"""
    k_via, via_x, k_x = f"KRW-{via}", f"{via}-{x}", f"KRW-{x}"
    if k_via not in books or via_x not in books or k_x not in books:
        return None
    if forward:                                   # KRW → via → X → KRW
        v = fill_buy(books[k_via]["asks"], krw)
        if v is None:
            return None
        v *= 1 - FEE["KRW"]
        q = fill_buy(books[via_x]["asks"], v)
        if q is None:
            return None
        q *= 1 - FEE[via]
        out = fill_sell(books[k_x]["bids"], q)
    else:                                         # KRW → X → via → KRW
        q = fill_buy(books[k_x]["asks"], krw)
        if q is None:
            return None
        q *= 1 - FEE["KRW"]
        v = fill_sell(books[via_x]["bids"], q)
        if v is None:
            return None
        v *= 1 - FEE[via]
        out = fill_sell(books[k_via]["bids"], v)
    if out is None:
        return None
    return out * (1 - FEE["KRW"]) / krw - 1


def parse_books(rows, now_ms=None):
    """→ (books, ages) · ages = {quote: [호가 나이 ms...]} (timestamp 는 마지막 호가 변경 시각 → 유동성 없는 마켓은 오래됨)."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    books, ages = {}, {}
    for r in rows:
        u = r.get("orderbook_units") or []
        books[r["market"]] = {"asks": [(float(l["ask_price"]), float(l["ask_size"])) for l in u],
                              "bids": [(float(l["bid_price"]), float(l["bid_size"])) for l in u]}
        ages.setdefault(r["market"].split("-", 1)[0], []).append(max(0, now_ms - int(r.get("timestamp", 0))))
    return books, ages


def evaluate(books, universe, vias):
    """{(x, path, size): net or None}"""
    out = {}
    for x in universe:
        for via in vias:
            for forward in (True, False):
                path = f"{via}-{'fwd' if forward else 'rev'}"
                for krw in SIZES:
                    out[(x, path, krw)] = path_net(books, x, via, forward, krw)
    return out


def summarize(samples, keys):
    """samples: [dict key→net]. 경로·크기별 집계 + (x, path, size) 상위."""
    by_ps, by_key = {}, {}
    for s in samples:
        for k in keys:
            v = s.get(k)
            if v is None:
                continue
            by_key.setdefault(k, []).append(v)
            by_ps.setdefault((k[1], k[2]), []).append(v)
    rows = []
    for (path, size), v in sorted(by_ps.items()):
        v_sorted = sorted(v)
        rows.append({"path": path, "size": size, "n": len(v), "pos": sum(x > 0 for x in v),
                     "max": v_sorted[-1], "p99": v_sorted[int(0.99 * (len(v) - 1))], "median": statistics.median(v)})
    best = sorted(((statistics.fmean(v), k, len(v)) for k, v in by_key.items()), reverse=True)[:8]
    # 가장 긴 연속 양수 (어느 (x, path, size) 든)
    longest = 0
    for k in by_key:
        run = cur = 0
        for s in samples:
            v = s.get(k)
            cur = cur + 1 if (v is not None and v > 0) else 0
            run = max(run, cur)
        longest = max(longest, run)
    return rows, best, longest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--interval", type=float, default=3)
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--out", default="tri_result")
    a = ap.parse_args(argv)

    mk = _get(f"{UPBIT}/market/all")
    by = {}
    for m in mk:
        q, b = m["market"].split("-", 1)
        by.setdefault(b, set()).add(q)
    cands = [b for b, s in by.items() if {"KRW", "BTC"} <= s]
    tick = []
    for i in range(0, len(cands), 100):
        tick += _get(f"{UPBIT}/ticker?markets=" + ",".join(f"KRW-{b}" for b in cands[i:i + 100]))
    tick.sort(key=lambda t: -float(t.get("acc_trade_price_24h", 0)))
    universe = [t["market"][4:] for t in tick[:a.top]]
    vias = ["BTC"] + (["USDT"] if "KRW" in by.get("USDT", set()) else [])
    markets = [f"KRW-{v}" for v in vias]
    for x in universe:
        markets.append(f"KRW-{x}")
        for v in vias:
            if v in by[x]:
                markets.append(f"{v}-{x}")
    print(f"TRI-ARB SAMPLE (pre-registered 2026-10-24) | universe {len(universe)} alts | vias {vias} | "
          f"{len(markets)} markets/sample | fees KRW {FEE['KRW']*100:.2f}% BTC/USDT {FEE['BTC']*100:.2f}%", flush=True)
    keys = [(x, f"{v}-{d}", s) for x in universe for v in vias for d in ("fwd", "rev") for s in SIZES]
    samples, ages, t_end, n_err = [], {}, time.time() + a.minutes * 60, 0
    while time.time() < t_end:
        t0 = time.time()
        try:
            rows = []
            for i in range(0, len(markets), 60):
                rows += _get(f"{UPBIT}/orderbook?markets=" + ",".join(markets[i:i + 60]))
            books, age = parse_books(rows)
            samples.append(evaluate(books, universe, vias))
            for q, v in age.items():
                ages.setdefault(q, []).extend(v)
        except Exception as e:
            n_err += 1
            if n_err <= 3:
                print(f"[warn] {type(e).__name__}: {e}", flush=True)
        if len(samples) % 100 == 0 and samples:
            print(f"[sample] {len(samples)}", flush=True)
        time.sleep(max(0.0, a.interval - (time.time() - t0)))

    rows, best, longest = summarize(samples, keys)
    age_s = {q: statistics.median(v) / 1000 for q, v in ages.items()}
    print(f"\nsamples {len(samples)} | errors {n_err} | orderbook age median (s, last change → fetch): "
          + " ".join(f"{q}-market {s:,.0f}" for q, s in age_s.items()))
    print(f"\n{'path':<10} {'size':>9} {'n':>6} {'pos':>5} {'pos%':>6} {'max%':>8} {'p99%':>8} {'median%':>8}")
    for r in rows:
        print(f"{r['path']:<10} {r['size']:>9,} {r['n']:>6} {r['pos']:>5} {r['pos']/r['n']*100:>6.2f} "
              f"{r['max']*100:>+8.3f} {r['p99']*100:>+8.3f} {r['median']*100:>+8.3f}")
    print("\nbest (x, path, size) by mean net:")
    for m, k, n in best:
        print(f"  {k[0]:<8} {k[1]:<9} {k[2]:>9,}  mean {m*100:+.3f}%  n={n}")
    print(f"\nlongest consecutive net>0 run (any x/path/size): {longest} samples (~{longest*a.interval:.0f}s)")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"universe": universe, "vias": vias, "samples": len(samples),
                                                 "errors": n_err, "skew_ms": skews, "rows": rows,
                                                 "best": [(m, list(k), n) for m, k, n in best],
                                                 "longest_run": longest}, indent=1))
    print(f"Results: {out}/report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
