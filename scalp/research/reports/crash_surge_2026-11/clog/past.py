# -*- coding: utf-8 -*-
"""조건별 표의 과거 값(2017-09 ~ 2026-11-19)을 만든다 — 전진 조건 기록과 같은 코드(crash_log.py)를 과거 15분봉·5분봉에 돌린다.
BTC 직접 사건에 필요한 5분봉 가운데 없는 덩어리는 받아서 M5 창고에 붙인다. 사용: past.py REPO DATA15 M5 OUT.json [ALL_ROWS.csv.gz]"""
import csv, datetime as dt, gzip, importlib.util, io, json, sys, time, urllib.request
from pathlib import Path
REPO, D15, M5, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
spec = importlib.util.spec_from_file_location("crash_log", str(REPO / "scalp/research/crash_log.py"))
CL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CL)
UNI = ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-WAVES", "KRW-AXS", "KRW-ETC", "KRW-CHZ", "KRW-SAND", "KRW-IQ", "KRW-APT",
       "KRW-ADA", "KRW-GMT", "KRW-KAVA", "KRW-POL", "KRW-TRX", "KRW-CELO", "KRW-ARK", "KRW-ALGO")


class Bars(object):
    def __init__(self, rows):
        qs = sorted(rows)
        self.ts, self.o, self.c, self.v = qs, [rows[q][0] for q in qs], [rows[q][1] for q in qs], [rows[q][2] for q in qs]


panel, five, have = {}, {}, {}
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
    d, blocks = {}, set()
    f = M5 / ("m5_%s.jsonl" % m)
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            blocks.add(rec["b"])
            d.update({int(k): [float(x) for x in v] for k, v in rec["bars"].items()})
    five[m], have[m] = d, blocks


def get(url):
    for k in range(8):
        try:
            time.sleep(0.14)
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "candle-fetch/1.0"}), timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(1.0 + k)
    raise RuntimeError(url)


raw = CL.upbit_fetch5(get, "https://api.upbit.com")
fetched = [0]


def fetch(m, block):
    if block in have[m]:
        return {}
    bars = raw(m, block)
    have[m].add(block)
    fetched[0] += 1
    with open(M5 / ("m5_%s.jsonl" % m), "a") as f:
        f.write(json.dumps({"b": block, "bars": bars}) + "\n")
    return bars


last = min(b.ts[-1] for b in panel.values())
rows = CL.rows(panel, CL.FiveMin(five, fetch), 0, last - 11)
print("사건 %d (BTC 직접 %d), 새로 받은 5분봉 덩어리 %d" % (len(rows), sum(1 for r in rows if r["구분"] == "BTC 직접"), fetched[0]), flush=True)
trows = [{k: CL.text(r.get(k)) for k in CL.COLUMNS} for r in rows]
table = CL.table(trows)
OUT.write_text(json.dumps({"period": "2017-09 ~ 2026-11-19", "events": len(trows), "table": table}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
if len(sys.argv) > 5:
    with open(sys.argv[5], "wb") as rawf:
        with gzip.GzipFile(filename="", mode="wb", fileobj=rawf, mtime=0) as gz:
            w = csv.DictWriter(io.TextIOWrapper(gz, encoding="utf-8", newline="", write_through=True), fieldnames=CL.COLUMNS, lineterminator="\n")
            w.writeheader()
            for r in trows:
                w.writerow(r)
for c in table:
    print("%-52s %5d %5d  %s" % (c["label"][:52], c["n"], c["days"], " ".join("%+.2f" % (100 * v) if v is not None else "  -  " for v in c["means"] + [c["up"], c["down"]])))
