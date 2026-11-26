# -*- coding: utf-8 -*-
"""조건별 표의 과거 값(conditions_past.json)을 이미 만들어 둔 과거 9년 사건 파일에서 다시 낸다 — 표에 칸을 더했을 때 쓴다.
사건을 다시 찾지 않고(시세를 받지 않는다) 같은 줄들을 지금의 crash_log.table 로 묶기만 한다.
사용: past_table.py REPO conditions_9y.csv.gz OUT.json [옛 OUT.json — 주면 이름이 같은 칸의 값이 그대로인지 확인한다]"""
import csv, gzip, importlib.util, io, json, sys
from pathlib import Path
REPO, SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
spec = importlib.util.spec_from_file_location("crash_log", str(REPO / "scalp/research/crash_log.py"))
CL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CL)
with gzip.open(SRC, "rb") as g:
    rows = list(csv.DictReader(io.TextIOWrapper(g, encoding="utf-8", newline="")))
table = CL.table(rows)
if len(sys.argv) > 4:
    old = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
    was = {c["key"]: c for c in old["table"]}
    now = {c["key"]: c for c in table}
    diff = [k for k in was if was[k] != now.get(k)]
    if diff or old["events"] != len(rows):
        sys.exit("옛 값과 다른 칸: %s (사건 %d → %d)" % (", ".join(diff) or "없음", old["events"], len(rows)))
    print("옛 칸 %d개 모두 같은 값, 새 칸 %d개: %s" % (len(was), len(now) - len(was), ", ".join(k for k in now if k not in was)))
OUT.write_text(json.dumps({"period": "2017-09 ~ 2026-11-19", "events": len(rows), "table": table}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
for c in table:
    print("%-52s %5d %5d  %s" % (c["label"][:52], c["n"], c["days"], " ".join("%+.2f" % (100 * v) if v is not None else "  -  " for v in c["means"] + [c["up"], c["down"]])))
