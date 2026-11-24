# -*- coding: utf-8 -*-
"""전진 조건 기록 코드(scalp/research/crash_log.py)가 9년 연구 파일과 같은 값을 내는지 대조한다.
같은 15분봉·5분봉 자료에 crash_log.rows() 를 돌려 crash9/crash_events_9y.csv.gz 와 사건 ID·열마다 비교한다.
사용: check_log.py REPO DATA15(c15_*.jsonl.gz) M5(m5_*.jsonl) crash_events_9y.csv.gz"""
import csv, gzip, importlib.util, json, sys
from pathlib import Path
REPO, D15, M5, REF = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
spec = importlib.util.spec_from_file_location("crash_log", str(REPO / "scalp/research/crash_log.py"))
CL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CL)
UNI = ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-WAVES", "KRW-AXS", "KRW-ETC", "KRW-CHZ", "KRW-SAND", "KRW-IQ", "KRW-APT",
       "KRW-ADA", "KRW-GMT", "KRW-KAVA", "KRW-POL", "KRW-TRX", "KRW-CELO", "KRW-ARK", "KRW-ALGO")


class Bars(object):
    def __init__(self, rows):
        qs = sorted(rows)
        self.ts, self.o, self.c, self.v = qs, [rows[q][0] for q in qs], [rows[q][1] for q in qs], [rows[q][2] for q in qs]


panel, five = {}, {}
for m in UNI:
    rows = {}
    with gzip.open(D15 / ("c15_%s.jsonl.gz" % m), "rt") as g:
        for line in g:
            try:
                page = json.loads(line)
            except ValueError:
                continue
            for r in page:
                rows[r[0] // 15] = (float(r[1]), float(r[4]), float(r[6]))
    panel[m] = Bars(rows)
    d = {}
    for line in (M5 / ("m5_%s.jsonl" % m)).read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        d.update({int(k): [float(x) for x in v] for k, v in rec["bars"].items()})
    five[m] = d
last = min(b.ts[-1] for b in panel.values())
got = {r["사건 ID"]: r for r in CL.rows(panel, CL.FiveMin(five), 0, last - 11)}
ref = {r["사건 ID"]: r for r in csv.DictReader(gzip.open(REF, "rt", encoding="utf-8"))}
alt = {k: v for k, v in got.items() if v["구분"] != "BTC 직접"}
print("연구 파일 %d건, 새 코드 알트 급락 %d건 (BTC 직접 %d건은 연구 파일에 없는 종류)" % (len(ref), len(alt), len(got) - len(alt)))
print("연구에만: %d, 새 코드에만: %d" % (len(set(ref) - set(alt)), len(set(alt) - set(ref))))
cols = [c for c in next(iter(ref.values())) if c in CL.COLUMNS]
bad = {}
for k in set(ref) & set(alt):
    for c in cols:
        a, b = ref[k][c], CL.text(alt[k].get(c))
        if a != b:
            try:
                same = abs(float(a) - float(b)) <= 2e-6
            except ValueError:
                same = False
            if not same:
                bad.setdefault(c, []).append((k, a, b))
print("겹치는 열 %d개, 겹치는 사건 %d건, 값이 다른 열: %s" % (len(cols), len(set(ref) & set(alt)), {c: len(v) for c, v in bad.items()} or "없음"))
for c, v in bad.items():
    print("  ", c, v[:3])
for name in ("연구에만", "새 코드에만"):
    ks = sorted((set(ref) - set(alt)) if name == "연구에만" else (set(alt) - set(ref)))[:5]
    if ks:
        print("  %s 예:" % name, ks)
if len(sys.argv) > 5:                                           # 2023–26 원자료(scn/crash_events.csv.gz)의 구분·배수·되밀림과도 대조
    scn = {r["사건 ID"]: r for r in csv.DictReader(gzip.open(sys.argv[5], "rt", encoding="utf-8"))}
    both = set(scn) & set(alt)
    kind = sum(1 for k in both if scn[k]["구분"] != alt[k]["구분"])
    mult = sum(1 for k in both if abs(float(scn[k]["거래대금 배수"]) - alt[k]["거래대금 배수"]) > 1e-4 * max(1.0, alt[k]["거래대금 배수"]))
    pull = sum(1 for k in both if (scn[k]["고점 뒤 되밀림"] or "") != CL.text(alt[k]["고점 뒤 되밀림"]) and abs(float(scn[k]["고점 뒤 되밀림"] or 0) - (alt[k]["고점 뒤 되밀림"] or 0)) > 2e-6)
    print("2023–26 원자료 %d건 중 겹침 %d건 — 구분이 다른 것 %d, 거래대금 배수가 다른 것 %d, 고점 뒤 되밀림이 다른 것 %d" % (len(scn), len(both), kind, mult, pull))
    d = [(k, scn[k]["구분"], alt[k]["구분"]) for k in sorted(both) if scn[k]["구분"] != alt[k]["구분"]][:5]
    if d:
        print("  ", d)
