#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ws_valid_windows — ws_recorder 아카이브에서 종목별 '분석 가능 시간'과 제외 구간을 산출 (읽기 전용, 표준 라이브러리, Python 3.7+).

왜: ws_archive_check 는 날짜 단위 품질이다. 전략 재검증은 개별 시점에 유효한 호가가 있었는지로 결정되므로,
    H17(shock_exhaustion_replay.py) 의 입력 규칙(MAX_GAP_MS=5000, MAX_AGE_MS=500)을 그대로 적용해 종목별·날짜별로
    (1) ready%  : 1초 창마다 유효 호가(수신 지연 0~500ms)가 하나 이상 들어온 초의 비율. 결정 시점에 500ms 안에 쓸 호가가 있었는가.
    (2) ob_rej% : 거부되는 호가 비율(지연 <0 또는 >500ms).  tr_rej%: 거부되는 체결 비율(timestamp·trade_timestamp 둘 다 0~500ms 이어야 통과).
    (3) 제외 구간: 수신 공백(>5s), 체결 거부가 있던 초(5초 이내 병합 = '체결 지연 구간'), 호가 거부가 있던 초.
    표(<root>_valid.txt)와 JSON(<root>_valid.json) 으로 낸다. JSON 은 이후 분석에서 그대로 필터로 쓴다 — 분석 결과를 보고 바꾸지 않는다.

읽는 법: ready% 가 낮은데 ob_rej% 도 높으면 수신 품질 문제. ready% 만 낮고 ob_rej% 는 0 근처면 호가 변동이 드문 종목(1초 안에
    갱신이 없었을 뿐) 이다. ob_ia p50/p99 (호가 수신 간격 ms) 로 둘을 가른다. late_s 는 체결 거부가 있던 초의 수 — H17 류
    체결 기반 신호는 그 구간에서 판정 불가가 된다.

이 도구는 데이터 선별이며 전략 판정이 아니다. 어떤 종목·날짜가 '좋다'는 말을 하지 않고, 어느 초가 규칙상 쓸 수 있는지만 센다.

실측 2026-11-05 (10/28~11/05 사본, -m 5종목, -s 4):
  ready%  BTC 96~99 · ETH 90~98 · XRP 85~98 · NEAR 66~89 · SOL 52~89. SOL·NEAR 가 낮은 것은 ob_rej%(1~3%) 탓이 아니라 갱신 없는
          초가 많아서다(ia_p99 2~7s, 주말 10/31·11/01 최저 52~54%). 호가 변동 피처는 그만큼 표본이 줄고, 스냅샷 피처는 영향 없음.
  ob_rej% 구 XRP 일 0.1~0.3, 새 종목 0.8~3.0 (11/05 2h 구간 3.7~4.6). ia_p50 BTC 82ms(거래소 전송 간격 하한으로 보임).
  tr_rej% BTC 22~31 · ETH 17~26 · XRP 8~19 · SOL 11~22 · NEAR 2~12. 그런데 late_s 는 하루의 0.9~3.1% 에 불과 → 거부 체결은 소수의
          폭주 초에 몰려 있다(BTC 10/29: 하루 2.5% 의 초 안에 체결의 28%). 체결 기반 충격 신호는 바로 그 순간 판정 불가가 된다 —
          H17 의 UNKNOWN 비율이 높았던 구조적 이유와 일치. 체결 피처는 recv_ts 기준 as-of 로만, 그 구간은 플래그로 남길 것.
  10/28 은 종목 교체일(새 종목 부분 기록)로 제외. 11/03 869s 공백·11/02·10/30 의 수십 초 공백은 JSON gaps 에 들어 있다.

실행 (PC):
  py -3 -X utf8 ws_valid_windows.py ws_tight                 (전체)
  py -3 -X utf8 ws_valid_windows.py ws_tight -d 2026-11-04   (하루)
  -s 4 : 호가 수신 간격 분위수만 4줄 중 1줄 표본(그 외 전수).  -m KRW-BTC,KRW-ETH : 종목 제한.
"""
import argparse
import datetime as dt
import gzip
import json
import re
import sys
import zlib
from pathlib import Path

MAX_GAP_MS, MAX_AGE_MS = 5000, 500             # shock_exhaustion_replay.py 와 동일
MERGE_S = 5                                    # 체결 지연 초를 구간으로 묶을 때 허용 간격
DAY_S = 86400
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")
_RECV = re.compile(rb'"recv_ts":\s*(\d+)')
_TS = re.compile(rb'"timestamp":\s*(\d+)')
_TTS = re.compile(rb'"trade_timestamp":\s*(\d+)')
_TYPE = re.compile(rb'"type":\s*"(trade|orderbook)"')
_CODE = re.compile(rb'"code":\s*"(KRW-[A-Z0-9]+)"')


def _q(v, q):
    if not v:
        return None
    s = sorted(v)
    return s[min(len(s) - 1, int(q * (len(s) - 1)))]


def day_epoch_s(name):
    return int(dt.datetime.strptime(name, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())


def intervals(flags, merge_s=0):
    """bytearray(초 단위 플래그) → [(start_s, end_s)] 반열림 구간, 인접/merge_s 이내 구간은 병합."""
    out = []
    start = None
    for i in range(len(flags) + 1):
        on = i < len(flags) and flags[i]
        if on and start is None:
            start = i
        elif not on and start is not None:
            if out and start - out[-1][1] <= merge_s:
                out[-1] = (out[-1][0], i)
            else:
                out.append((start, i))
            start = None
    return out


class _Market(object):
    __slots__ = ("ob_n", "ob_rej", "tr_n", "tr_rej", "ready", "ob_bad", "tr_bad", "last_ob", "ia")

    def __init__(self):
        self.ob_n = self.ob_rej = self.tr_n = self.tr_rej = 0
        self.ready = bytearray(DAY_S)          # 그 초에 유효 호가 수신
        self.ob_bad = bytearray(DAY_S)         # 그 초에 거부 호가
        self.tr_bad = bytearray(DAY_S)         # 그 초에 거부 체결
        self.last_ob = None
        self.ia = []


def scan_day(day_dir, sample=1, only=None):
    """하루 폴더 → dict(span, first, last, gaps[(start_ms,end_ms)], truncated[], markets{code: _Market}, lines)."""
    day0 = day_epoch_s(day_dir.name)
    files = sorted(p for p in day_dir.iterdir() if p.name.endswith(".jsonl.gz"))
    r = {"date": day_dir.name, "day0": day0, "first": None, "last": None, "gaps": [], "truncated": [],
         "markets": {}, "lines": 0, "files": len(files)}
    prev = None
    n = 0
    for p in files:
        try:
            with gzip.open(str(p), "rb") as fh:
                for raw in fh:
                    r["lines"] += 1
                    m = _RECV.search(raw)
                    if not m:
                        continue
                    recv = int(m.group(1))
                    if r["first"] is None:
                        r["first"] = recv
                    if prev is not None and recv - prev > MAX_GAP_MS:
                        r["gaps"].append((prev, recv))
                    prev = recv
                    r["last"] = recv
                    if b'"_meta"' in raw:
                        continue
                    t = _TYPE.search(raw)
                    c = _CODE.search(raw)
                    ts = _TS.search(raw)
                    if not (t and c and ts):
                        continue
                    code = c.group(1).decode()
                    if only and code not in only:
                        continue
                    mk = r["markets"].get(code)
                    if mk is None:
                        mk = r["markets"][code] = _Market()
                    sec = recv // 1000 - day0
                    inside = 0 <= sec < DAY_S
                    lag = recv - int(ts.group(1))
                    ok = 0 <= lag <= MAX_AGE_MS
                    if t.group(1) == b"orderbook":
                        mk.ob_n += 1
                        n += 1
                        if mk.last_ob is not None and (sample <= 1 or n % sample == 0):
                            mk.ia.append(recv - mk.last_ob)
                        mk.last_ob = recv
                        if ok:
                            if inside:
                                mk.ready[sec] = 1
                        else:
                            mk.ob_rej += 1
                            if inside:
                                mk.ob_bad[sec] = 1
                    else:
                        mk.tr_n += 1
                        tts = _TTS.search(raw)
                        if tts and not (0 <= recv - int(tts.group(1)) <= MAX_AGE_MS):
                            ok = False
                        if not ok:
                            mk.tr_rej += 1
                            if inside:
                                mk.tr_bad[sec] = 1
        except (EOFError, OSError, zlib.error):
            r["truncated"].append(p.name)
    r["span_s"] = (r["last"] - r["first"]) / 1000.0 if r["first"] is not None else 0.0
    return r


def market_row(r, code, mk):
    span = max(r["span_s"], 1.0)
    ready_s = sum(mk.ready)
    late = intervals(mk.tr_bad, MERGE_S)
    stale = intervals(mk.ob_bad, 0)
    late_s = sum(e - s for s, e in late)
    return {"market": code, "ob_n": mk.ob_n, "ob_rej_pct": 100.0 * mk.ob_rej / mk.ob_n if mk.ob_n else None,
            "ob_ia_p50": _q(mk.ia, 0.5), "ob_ia_p99": _q(mk.ia, 0.99),
            "ready_s": ready_s, "ready_pct": 100.0 * ready_s / span,
            "tr_n": mk.tr_n, "tr_rej_pct": 100.0 * mk.tr_rej / mk.tr_n if mk.tr_n else None,
            "late_s": late_s, "late_pct": 100.0 * late_s / span,
            "trade_late": [[(r["day0"] + s) * 1000, (r["day0"] + e) * 1000] for s, e in late],
            "ob_stale": [[(r["day0"] + s) * 1000, (r["day0"] + e) * 1000] for s, e in stale]}


def fmt(v, spec="{}"):
    return "n/a" if v is None else spec.format(v)


HEAD = "{:<10} {:<9} {:>8} {:>6} {:>5}/{:<5} {:>6} {:>7} {:>6} {:>6} {:>5}".format(
    "date", "market", "ob_n", "ob_rej%", "ia_p50", "p99", "ready%", "tr_n", "tr_rej%", "late_s", "late%")


def fmt_row(date, w):
    return "{:<10} {:<9} {:>8} {:>7} {:>5}/{:<5} {:>6} {:>7} {:>7} {:>6} {:>5}".format(
        date, w["market"], w["ob_n"], fmt(w["ob_rej_pct"], "{:.1f}"), fmt(w["ob_ia_p50"]), fmt(w["ob_ia_p99"]),
        "{:.1f}".format(w["ready_pct"]), w["tr_n"], fmt(w["tr_rej_pct"], "{:.1f}"), w["late_s"],
        "{:.1f}".format(w["late_pct"]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("-d", "--date", default="", help="이 날짜만")
    ap.add_argument("-s", "--sample", type=int, default=1, help="호가 수신 간격 분위수용 표본 간격")
    ap.add_argument("-m", "--markets", default="", help="쉼표 구분 종목 제한")
    a = ap.parse_args(argv)
    root = Path(a.root)
    only = set(x.strip() for x in a.markets.split(",") if x.strip()) or None
    days = sorted(p for p in root.iterdir() if p.is_dir() and _DATE.match(p.name))
    if a.date:
        days = [p for p in days if p.name == a.date]
    if not days:
        print("no dated folders under", root)
        return 1
    lines = ["WS VALID WINDOWS | root=%s | rules: gap>%ds, age 0..%dms (H17) | ready%% = seconds with a fresh book / "
             "recorded span | late_s = seconds with a rejected trade (merged within %ds)"
             % (root, MAX_GAP_MS // 1000, MAX_AGE_MS, MERGE_S), HEAD]
    out = {"rules": {"max_gap_ms": MAX_GAP_MS, "max_age_ms": MAX_AGE_MS, "merge_s": MERGE_S}, "days": {}}
    total = {}
    for d in days:
        r = scan_day(d, a.sample, only)
        gap_s = sum(e - s for s, e in r["gaps"]) / 1000.0
        day = {"files": r["files"], "span_s": round(r["span_s"], 1), "gap_s": round(gap_s, 1),
               "gaps": [[s, e] for s, e in r["gaps"]], "truncated": r["truncated"], "markets": {}}
        for code in sorted(r["markets"]):
            w = market_row(r, code, r["markets"][code])
            day["markets"][code] = w
            lines.append(fmt_row(d.name, w))
            print(lines[-1], flush=True)
            t = total.setdefault(code, {"span": 0.0, "ready": 0, "late": 0, "days": 0})
            t["span"] += r["span_s"]
            t["ready"] += w["ready_s"]
            t["late"] += w["late_s"]
            t["days"] += 1
        note = "{:<10} span={:.1f}h gaps={} ({:.0f}s)".format(d.name, r["span_s"] / 3600, len(r["gaps"]), gap_s)
        if r["truncated"]:
            note += " truncated=%s" % r["truncated"]
        lines.append(note)
        print(note, flush=True)
        out["days"][d.name] = day
    lines.append("per market (all days): market  days  span_h  ready_h  ready%  late_h")
    for code in sorted(total):
        t = total[code]
        lines.append("  {:<9} {:>4} {:>7.1f} {:>8.1f} {:>6.1f} {:>7.2f}".format(
            code, t["days"], t["span"] / 3600, t["ready"] / 3600, 100.0 * t["ready"] / max(t["span"], 1), t["late"] / 3600))
    base = str(root).rstrip("\\/")
    Path(base + "_valid.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with open(base + "_valid.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print("\n".join(lines[:1] + lines[-(len(total) + 1):]))
    print("saved:", base + "_valid.txt", base + "_valid.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
