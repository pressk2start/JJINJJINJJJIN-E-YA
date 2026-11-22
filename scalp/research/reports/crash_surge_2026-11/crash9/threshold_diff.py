# -*- coding: utf-8 -*-
"""조합의 문턱값을 반올림하면서 달라진 사건을 남긴다 (253건 → 243건). 사용: threshold_diff.py SIGNALS.pkl OUT.md"""
import datetime as dt, pickle, sys
A = pickle.load(open(sys.argv[1], "rb"))["A"]
UTC = dt.timezone.utc
base = [s for s in A if "2023-01-01" <= s["day"] < "2025-01-01"]
def cut(name):
    xs = sorted(s["f"][name] for s in base if s["f"].get(name) is not None)
    return xs[len(xs) // 3]
E4, EB, R4, RB = cut("4시간 수익"), cut("BTC 24시간 수익"), -0.0592, -0.0173
sid = lambda s: "%s_%s" % (s["m"][4:], dt.datetime.fromtimestamp((s["q"] + 1) * 900, tz=UTC).strftime("%Y%m%dT%H%M"))
g = lambda s, n: s["f"].get(n)
ex = lambda s: g(s, "4시간 수익") is not None and g(s, "4시간 수익") <= E4 and g(s, "BTC 24시간 수익") is not None and g(s, "BTC 24시간 수익") <= EB
ro = lambda s: g(s, "4시간 수익") is not None and g(s, "4시간 수익") <= R4 and g(s, "BTC 24시간 수익") is not None and g(s, "BTC 24시간 수익") <= RB
L = ["# 조합 문턱값 반올림으로 달라진 사건", "",
     "long/report15.md 의 조합 표는 2023–24 신호의 3분위 값을 그대로(반올림 없이) 문턱으로 썼다. 그 뒤의 표(long/robust.py, scn, crash9)와 등록 후보는 반올림한 값을 쓴다.", "",
     "| 조건 | 3분위 값 그대로 | 반올림해 고정한 값 |", "|---|---|---|",
     "| 그 종목 4시간 수익 | ≤ %.6f%% | ≤ %.2f%% |" % (100 * E4, 100 * R4), "| BTC 24시간 수익 | ≤ %.6f%% | ≤ %.2f%% |" % (100 * EB, 100 * RB), "",
     "| 기간 | 3분위 값 그대로 | 반올림 값 | 그대로일 때만 들어가는 사건 | 반올림일 때만 들어가는 사건 |", "|---|---|---|---|---|"]
rows = []
for name, a, b in (("2017–22", "0000", "2023"), ("2023–24", "2023", "2025"), ("2025–26", "2025", "9999")):
    part = [s for s in A if a <= s["day"][:4] < b]
    only_e = [s for s in part if ex(s) and not ro(s)]
    only_r = [s for s in part if ro(s) and not ex(s)]
    L.append("| %s | %d | %d | %d | %d |" % (name, sum(1 for s in part if ex(s)), sum(1 for s in part if ro(s)), len(only_e), len(only_r)))
    rows += [(name, "그대로일 때만", s) for s in only_e] + [(name, "반올림일 때만", s) for s in only_r]
L += ["", "## 달라진 사건", "", "| 기간 | 구분 | 사건 ID | 그 종목 4시간 수익 | BTC 24시간 수익 | 60분 수익(비용 뒤, 15분봉 기준) |", "|---|---|---|---|---|---|"]
for name, kind, s in rows:
    L.append("| %s | %s | %s | %.4f%% | %.4f%% | %+.2f%% |" % (name, kind, sid(s), 100 * g(s, "4시간 수익"), 100 * g(s, "BTC 24시간 수익"), 100 * s["net"][4]))
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(L) + "\n")
print("\n".join(L[:14])); print(len(rows))
