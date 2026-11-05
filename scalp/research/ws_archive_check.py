#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ws_archive_check — ws_recorder 아카이브 사본의 품질 검사 (읽기 전용, 표준 라이브러리, Python 3.7+).

왜: 파일이 있다는 것과 누락 없이 기록됐다는 것은 다르다. PC 로 복사한 녹화를 분석에 쓰기 전에 날짜별로
    (1) 시간별 파일 24개가 다 있는지, (2) 잘린 gzip 이 있는지, (3) 수신 공백(5초 초과)이 몇 번·얼마나 긴지,
    (4) 수신 지연(recv_ts − 거래소 timestamp) p50/p99 와 500ms 초과 비율, (5) 종목이 기대한 5개인지,
    (6) 체결이 늦게 오는 순간에 호가 메시지도 줄어드는지(ob@late) 를 한 표로 본다.

(6) 이 핵심 진단이다. 레코더는 호가·체결을 한 소켓으로 받아 FIFO 로 처리하므로(ws_recorder.py), 서버 CPU 가 밀려
    메시지가 소켓에 쌓이면 호가·체결이 같은 비율로 늦어져야 한다. 그런데 실측은 호가 지연 >500ms 가 1~2% 인데 체결은 14~20%.
    설명은 둘 중 하나: (A) 거래소 체결 피드 자체가 폭주 때 늦게 발행된다(호가는 완전, 체결만 늦음) 또는
    (B) 느린 클라이언트에 대해 거래소가 호가는 최신 스냅샷으로 압축(conflate)하고 체결은 큐에 쌓아 보낸다(호가 갱신 누락 = 품질 저하).
    구분법: 체결 지연 >2s 인 초의 호가 메시지 수 / 하루 중앙값 = ob@late. (A) 면 ≈1 이상(변동 때 호가도 많아짐), (B) 면 ≪1.
    판정 규칙(사전 기재): ob@late < 0.5 → 'conflate?' 표시, 그 날은 ΔOBI·depletion 류 피처에 쓰지 말 것.
    결과 2026-11-05 (10/22~11/05 전 15일): ob@late 0.67~3.55, 새 5종목 전 일자 1.14~2.79 → (A). 체결이 늦는 순간 호가는 오히려
    평소의 1.1~3.5배로 들어오고(변동 구간) 호가 지연은 그대로 작다. 호가 압축 없음, 호가는 완전. 10/29·30 의 체결 p99 11.7·14.9s 도
    ob@late 1.61·1.14 라 서버 CPU 가 아니라 거래소 체결 피드 지연(체결 최다일)으로 해석. 이전 'CPU 부족' 귀인은 철회.
    분석 시 체결은 recv_ts 를 as-of 시각으로 써야 한다(trade_timestamp 기준이면 p99 2s, 10/29·30 은 12~15s 의 미래 참조).

판정 기준(10/22~11/04 사본으로 보정): 체결 500ms 초과 비율은 새 5종목(BTC·ETH·XRP·SOL·NEAR) 정상일에도 14~18% 로 일정해서
    10% 기준은 매일 울린다 → 체결은 p99 > 5s(10/29·30 의 11.7·14.9s 만 해당) 또는 >500ms 비율 30% 초과만 알린다.
    호가는 5% 기준 유지(실측 0.1~2.2%). 공백은 10분 초과만.

실측 2026-11-05 (10/22~11/05 사본, PC): 구 5종목(10/22~27) 호가 p50 55~70ms·p99 0.3~0.4s, 체결 p50 80~88ms·p99 0.5~6.1s, 체결 stale 1~9%.
    새 5종목(10/29~) 메시지 2.5~3배(호가 170~256만/일, 체결 21~51만/일), 호가 p50 76~86ms·p99 0.5~0.7s·stale 0.9~2.2%,
    체결 p50 137~169ms·p99 1.7~2.3s·stale 14~18%; 10/29·30 만 체결 p99 11.7·14.9s(stale 20.5·16.8%) — 스캐너 가동일이자 체결 최다일.
    11/03 869s 공백 = 레코더 정지 테스트. 10/28 codes=9 = 종목 교체일. 마지막 날 마지막 시간 파일 잘림 = 복사 중 기록 중이던 파일(정상).

실행 (PC, 복사한 폴더를 위치 인자로):
  py -3 -X utf8 ws_archive_check.py ws_tight
  py -3 -X utf8 ws_archive_check.py ws_tight -d 2026-11-05     (특정 날짜만)
출력: 콘솔 표 + <root>_check.txt (같은 내용). 느리면 -s 4 로 4줄 중 1줄만 표본(지연 분위수만 영향, ob@late 는 전수).
"""
import argparse
import gzip
import re
import sys
import zlib
from pathlib import Path

GAP_MS = 5000
STALE_MS = 500
LATE_TR_MS = 2000
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
    """한 파일 → dict(lines, trades, books, gaps, max_gap_ms, lag_ob, lag_tr, codes, truncated, first_recv, last_recv,
    sec_ob{초: 호가 수}, sec_trlag{초: 그 초 최대 체결 지연})."""
    r = {"lines": 0, "trades": 0, "books": 0, "gaps": 0, "max_gap_ms": 0, "lag_ob": [], "lag_tr": [],
         "codes": set(), "truncated": False, "first_recv": None, "last_recv": None, "meta": 0,
         "sec_ob": {}, "sec_trlag": {}}
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
                sec = recv // 1000
                ts = _TS.search(raw)
                lag = recv - int(ts.group(1)) if ts else None
                if kind == b"trade":
                    r["trades"] += 1
                    if lag is not None and lag > r["sec_trlag"].get(sec, -1):
                        r["sec_trlag"][sec] = lag
                else:
                    r["books"] += 1
                    r["sec_ob"][sec] = r["sec_ob"].get(sec, 0) + 1
                if sample > 1 and r["lines"] % sample:
                    continue
                if lag is not None:
                    (r["lag_tr"] if kind == b"trade" else r["lag_ob"]).append(lag)
    except (EOFError, OSError, zlib.error):
        r["truncated"] = True
    return r


def ob_late_ratio(sec_ob, sec_trlag, late_ms=LATE_TR_MS):
    """체결 지연 >late_ms 인 초들의 호가 메시지 수 중앙값 / 전체 활동 초의 중앙값 → (ratio, late_secs, ob_per_sec).
    ≪1 이면 느린 클라이언트에 맞춰 거래소가 호가를 압축해 보낸 것(호가 갱신 누락), ≈1 이상이면 체결 피드만 늦은 것."""
    if not sec_ob:
        return None, 0, None
    base = _q(list(sec_ob.values()), 0.5)
    late = [s for s, lag in sec_trlag.items() if lag > late_ms]
    if not late or not base:
        return None, len(late), base
    at_late = _q([sec_ob.get(s, 0) for s in late], 0.5)
    return at_late / base, len(late), base


def check_day(day_dir, sample=1):
    files = sorted(p for p in day_dir.iterdir() if p.name.endswith(".jsonl.gz"))
    hours = sorted(int(p.name[:2]) for p in files if p.name[:2].isdigit())
    missing = [h for h in range(24) if h not in hours]
    agg = {"files": len(files), "missing_hours": missing, "lines": 0, "trades": 0, "books": 0, "gaps": 0,
           "max_gap_ms": 0, "lag_ob": [], "lag_tr": [], "codes": set(), "truncated": [], "cross_gaps": 0, "meta": 0,
           "last_file": files[-1].name if files else None}
    sec_ob, sec_trlag = {}, {}
    last_recv = None
    for p in files:
        r = scan_file(p, sample)
        for k in ("lines", "trades", "books", "gaps", "meta"):
            agg[k] += r[k]
        agg["max_gap_ms"] = max(agg["max_gap_ms"], r["max_gap_ms"])
        agg["lag_ob"] += r["lag_ob"]
        agg["lag_tr"] += r["lag_tr"]
        agg["codes"] |= r["codes"]
        for s, n in r["sec_ob"].items():
            sec_ob[s] = sec_ob.get(s, 0) + n
        for s, lag in r["sec_trlag"].items():
            if lag > sec_trlag.get(s, -1):
                sec_trlag[s] = lag
        if r["truncated"]:
            agg["truncated"].append(p.name)
        if last_recv is not None and r["first_recv"] is not None and r["first_recv"] - last_recv > GAP_MS:
            agg["cross_gaps"] += 1                               # 파일 경계를 넘는 공백 (재시작 등)
            agg["max_gap_ms"] = max(agg["max_gap_ms"], r["first_recv"] - last_recv)
        if r["last_recv"] is not None:
            last_recv = r["last_recv"]
    agg["gaps"] += agg["cross_gaps"]
    agg["ob_late_ratio"], agg["late_secs"], agg["ob_per_sec"] = ob_late_ratio(sec_ob, sec_trlag)
    return agg


def stale_share(v, ms=STALE_MS):
    """분석기 신선도 기준(500ms)보다 늦게 받은 메시지 비율(%). 분석에서 '판정 불가'로 빠지는 몫."""
    return 100.0 * sum(1 for x in v if x > ms) / len(v) if v else None


def verdict(a, expect_codes=5, today=False):
    bad = []
    if a["missing_hours"] and not today:
        bad.append("missing_hours=%s" % a["missing_hours"])
    trunc = a["truncated"]
    if today and trunc == [a.get("last_file")]:
        trunc = []                                                # 복사 시점에 기록 중이던 마지막 파일 = 정상
    if trunc:
        bad.append("truncated=%s" % trunc)
    if len(a["codes"]) != expect_codes:
        bad.append("codes=%d" % len(a["codes"]))
    so, st = stale_share(a["lag_ob"]), stale_share(a["lag_tr"])
    if (so or 0) > 5:                                             # 호가는 한 소켓 FIFO 라 >5% 면 서버 쪽 지연
        bad.append("ob_stale=%.1f%%" % so)
    p99t = _q(a["lag_tr"], 0.99)
    if (p99t or 0) > 5000 or (st or 0) > 30:                      # 체결은 정상일도 14~18% 늦게 옴 → 꼬리만 본다
        bad.append("tr_p99=%.1fs stale=%.1f%%" % ((p99t or 0) / 1000, st or 0))
    if a.get("ob_late_ratio") is not None and a["ob_late_ratio"] < 0.5:
        bad.append("conflate? ob@late=%.2f" % a["ob_late_ratio"])
    if a["max_gap_ms"] > 10 * 60 * 1000:
        bad.append("gap>10min")
    return "OK" if not bad else "CHECK " + " ".join(bad)


def fmt_row(day, a, today=False, expect_codes=5):
    p50o, p99o = _q(a["lag_ob"], 0.5), _q(a["lag_ob"], 0.99)
    p50t, p99t = _q(a["lag_tr"], 0.5), _q(a["lag_tr"], 0.99)
    f = lambda v: "n/a" if v is None else str(v)
    g = lambda v: "n/a" if v is None else "{:.1f}".format(v)
    h = lambda v: "n/a" if v is None else "{:.2f}".format(v)
    return ("{:<10} {:>5} {:>9} {:>8} {:>4} {:>8} {:>7}/{:<6} {:>7}/{:<6} {:>5}/{:<5} {:>4} {:>7} {:>5}  {}".format(
        day, a["files"], a["books"], a["trades"], a["gaps"], "{:.1f}".format(a["max_gap_ms"] / 1000),
        f(p50o), f(p99o), f(p50t), f(p99t), g(stale_share(a["lag_ob"])), g(stale_share(a["lag_tr"])),
        f(a.get("ob_per_sec")), h(a.get("ob_late_ratio")), len(a["codes"]),
        verdict(a, expect_codes=expect_codes, today=today)))


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
    head = "{:<10} {:>5} {:>9} {:>8} {:>4} {:>8} {:>14} {:>14} {:>11} {:>4} {:>7} {:>5}  verdict".format(
        "date", "files", "books", "trades", "gaps", "maxgap_s", "ob_lag p50/p99", "tr_lag p50/p99", "stale% ob/tr",
        "ob/s", "ob@late", "codes")
    lines = ["WS ARCHIVE CHECK | root=%s | gap>%ds | lag=recv_ts-timestamp ms | ob@late = ob msgs/s in seconds with "
             "trade lag>%ds vs day median" % (root, GAP_MS // 1000, LATE_TR_MS // 1000), head]
    last = days[-1].name
    codes = set()
    for d in days:
        agg = check_day(d, a.sample)
        codes = agg["codes"]
        lines.append(fmt_row(d.name, agg, today=(d.name == last), expect_codes=a.codes))
        print(lines[-1], flush=True)
    lines.append("codes seen (last day): " + ", ".join(sorted(codes)))
    lines.append("verdict OK = 24 files, no truncation (last day's last file exempt), %d codes, ob stale(>%dms)<=5%%, "
                 "tr p99<=5s & stale<=30%%, ob@late>=0.5, max gap<=10min" % (a.codes, STALE_MS))
    lines.append("ob@late <0.5 = exchange conflated books for a slow client (book updates lost) -> skip that day for "
                 "dOBI/depletion features; ~1 = only the trade feed was late, books complete.")
    out = Path(str(root).rstrip("\\/") + "_check.txt")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:1] + lines[-3:]))
    print("saved:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
