#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ws_archive_check — ws_recorder 아카이브 사본의 품질 검사 (읽기 전용, 표준 라이브러리, Python 3.7+).

왜: 파일이 있다는 것과 누락 없이 기록됐다는 것은 다르다. PC 로 복사한 녹화를 분석에 쓰기 전에 날짜별로
    (1) 시간별 파일 24개가 다 있는지, (2) 잘린 gzip 이 있는지, (3) 수신 공백(5초 초과)이 몇 번·얼마나 긴지,
    (4) 수신 지연(recv_ts − 거래소 timestamp) p50/p99 가 얼마인지, (5) 종목이 기대한 5개인지를 한 표로 본다.
    2026-10-29 실측 기준: 호가 p99 539ms · 체결 p99 696ms · 공백은 재접속/레코더 재시작 때만. 이보다 크게 나쁘면 알린다.

결과 2026-11-05 (10/22~11/05 사본, PC): 호가 p50 55~86ms·p99 0.3~0.7s 전 기간 안정. 체결 p99 는 CPU 부족 기간(10/27~10/30
    스캐너 가동)에 11~15s 까지 밀렸다가 스캐너 정지 후 ~2s. 호가는 최신분만 오고 체결은 밀려 오는 형태 → 그 이틀은 호가 갱신
    누락 가능성이 있어 '품질 낮음' 표시. 11/03 869s 공백 = 레코더 정지 테스트. 10/28 codes=9 = 종목 교체일.

실행 (PC, 복사한 폴더를 위치 인자로):
  py -3 -X utf8 ws_archive_check.py ws_tight
  py -3 -X utf8 ws_archive_check.py ws_tight -d 2026-11-05     (특정 날짜만)
출력: 콘솔 표 + <root>_check.txt (같은 내용). 느리면 -s 4 로 4줄 중 1줄만 표본(지연 분위수만 영향).
"""
import argparse
import gzip
import json
import re
import sys
import zlib
from pathlib import Path

GAP_MS = 5000
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")
_RECV = re.compile(rb'"recv_ts":\s*(\d+)')
_TS = re.compile(rb'"timestamp":\s*(\d+)')
_TYPE = re.compile(rb'"type":\s*"(trade|orderbook)"')
_CODE = re.compile(rb'"code":\s*"(KRW-[A-Z0-9]+)"')


def _q(v, q):
    if not v:
        return None
    s = sorted(v)
    return s[min(len(s) - 1, int(q * (len(s) - 1)))]


def scan_file(path, sample=1):
    """한 파일 → dict(lines, trades, books, gaps, max_gap_ms, lag_ob, lag_tr, codes, truncated, first_recv, last_recv)."""
    r = {"lines": 0, "trades": 0, "books": 0, "gaps": 0, "max_gap_ms": 0, "lag_ob": [], "lag_tr": [],
         "codes": set(), "truncated": False, "first_recv": None, "last_recv": None, "meta": 0}
    prev = None
    try:
        with gzip.open(str(path), "rb") as fh:
            for raw in fh:
                r["lines"] += 1
                m = _RECV.search(raw)
                if not m:
                    continue
                recv = int(m.group(1))
                if r["first_recv"] is None:
                    r["first_recv"] = recv
                if prev is not None and recv - prev > GAP_MS:
                    r["gaps"] += 1
                    r["max_gap_ms"] = max(r["max_gap_ms"], recv - prev)
                prev = recv
                r["last_recv"] = recv
                if b'"_meta"' in raw:
                    r["meta"] += 1
                    continue
                t = _TYPE.search(raw)
                if not t:
                    continue
                c = _CODE.search(raw)
                if c:
                    r["codes"].add(c.group(1).decode())
                kind = t.group(1)
                if kind == b"trade":
                    r["trades"] += 1
                else:
                    r["books"] += 1
                if sample > 1 and r["lines"] % sample:
                    continue
                ts = _TS.search(raw)
                if ts:
                    lag = recv - int(ts.group(1))
                    (r["lag_tr"] if kind == b"trade" else r["lag_ob"]).append(lag)
    except (EOFError, OSError, zlib.error):
        r["truncated"] = True
    return r


def check_day(day_dir, sample=1):
    files = sorted(p for p in day_dir.iterdir() if p.name.endswith(".jsonl.gz"))
    hours = sorted(int(p.name[:2]) for p in files if p.name[:2].isdigit())
    missing = [h for h in range(24) if h not in hours]
    agg = {"files": len(files), "missing_hours": missing, "lines": 0, "trades": 0, "books": 0, "gaps": 0,
           "max_gap_ms": 0, "lag_ob": [], "lag_tr": [], "codes": set(), "truncated": [], "cross_gaps": 0, "meta": 0}
    last_recv = None
    for p in files:
        r = scan_file(p, sample)
        for k in ("lines", "trades", "books", "gaps", "meta"):
            agg[k] += r[k]
        agg["max_gap_ms"] = max(agg["max_gap_ms"], r["max_gap_ms"])
        agg["lag_ob"] += r["lag_ob"]
        agg["lag_tr"] += r["lag_tr"]
        agg["codes"] |= r["codes"]
        if r["truncated"]:
            agg["truncated"].append(p.name)
        if last_recv is not None and r["first_recv"] is not None and r["first_recv"] - last_recv > GAP_MS:
            agg["cross_gaps"] += 1                               # 파일 경계를 넘는 공백 (재시작 등)
            agg["max_gap_ms"] = max(agg["max_gap_ms"], r["first_recv"] - last_recv)
        if r["last_recv"] is not None:
            last_recv = r["last_recv"]
    agg["gaps"] += agg["cross_gaps"]
    return agg


def stale_share(v, ms=500):
    """분석기 신선도 기준(500ms)보다 늦게 받은 메시지 비율(%). 분석에서 '판정 불가'로 빠지는 몫."""
    return 100.0 * sum(1 for x in v if x > ms) / len(v) if v else None


def verdict(a, expect_codes=5, today=False):
    bad = []
    if a["missing_hours"] and not today:
        bad.append("missing_hours=%s" % a["missing_hours"])
    if a["truncated"]:
        bad.append("truncated=%s" % a["truncated"])
    if len(a["codes"]) != expect_codes:
        bad.append("codes=%d" % len(a["codes"]))
    so, st = stale_share(a["lag_ob"]), stale_share(a["lag_tr"])
    if (so or 0) > 5 or (st or 0) > 10:                       # 호가 5%·체결 10% 넘게 500ms 이후 수신이면 CPU 부족 의심
        bad.append("stale>500ms ob=%.1f%% tr=%.1f%%" % (so or 0, st or 0))
    if a["max_gap_ms"] > 10 * 60 * 1000:
        bad.append("gap>10min")
    return "OK" if not bad else "CHECK " + " ".join(bad)


def fmt_row(day, a, today=False):
    p50o, p99o = _q(a["lag_ob"], 0.5), _q(a["lag_ob"], 0.99)
    p50t, p99t = _q(a["lag_tr"], 0.5), _q(a["lag_tr"], 0.99)
    f = lambda v: "n/a" if v is None else str(v)
    g = lambda v: "n/a" if v is None else "{:.1f}".format(v)
    return ("{:<10} {:>5} {:>9} {:>8} {:>4} {:>8} {:>7}/{:<6} {:>7}/{:<6} {:>5}/{:<5} {:>5}  {}".format(
        day, a["files"], a["books"], a["trades"], a["gaps"], "{:.1f}".format(a["max_gap_ms"] / 1000),
        f(p50o), f(p99o), f(p50t), f(p99t), g(stale_share(a["lag_ob"])), g(stale_share(a["lag_tr"])),
        len(a["codes"]), verdict(a, today=today)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("-d", "--date", default="", help="이 날짜만")
    ap.add_argument("-s", "--sample", type=int, default=1, help="지연 분위수용 표본 간격 (1=전수)")
    ap.add_argument("-c", "--codes", type=int, default=5, help="기대 종목 수")
    a = ap.parse_args(argv)
    root = Path(a.root)
    days = sorted(p for p in root.iterdir() if p.is_dir() and _DATE.match(p.name))
    if a.date:
        days = [p for p in days if p.name == a.date]
    if not days:
        print("no dated folders under", root)
        return 1
    head = "{:<10} {:>5} {:>9} {:>8} {:>4} {:>8} {:>14} {:>14} {:>11} {:>5}  verdict".format(
        "date", "files", "books", "trades", "gaps", "maxgap_s", "ob_lag p50/p99", "tr_lag p50/p99", "stale% ob/tr", "codes")
    lines = ["WS ARCHIVE CHECK | root=%s | gap>%ds | lag=recv_ts-timestamp ms" % (root, GAP_MS // 1000), head]
    last = days[-1].name
    for d in days:
        agg = check_day(d, a.sample)
        lines.append(fmt_row(d.name, agg, today=(d.name == last)))
        print(lines[-1], flush=True)
    lines.append("codes seen: " + ", ".join(sorted(set().union(*[check_day(d, 50)["codes"] for d in days[-1:]]))))
    lines.append("verdict OK = 24 files, no truncation, %d codes, stale(>500ms) ob<=5%% tr<=10%%, max gap<=10min (last day may be partial)" % a.codes)
    lines.append("2026-10-29..30 reference (CPU-starved recorder): trade p99 11-15s; after scanners stopped 10-30: ~2s.")
    out = Path(str(root).rstrip("\\/") + "_check.txt")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:1] + lines[-2:]))
    print("saved:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
