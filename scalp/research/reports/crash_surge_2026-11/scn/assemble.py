# -*- coding: utf-8 -*-
"""보고서 묶기: 대조표 + 급등(A~D) + 급락(E~H) + 조합의 이익 집중도 + 접을 것/남길 것 + 재현 방법."""
import hashlib, math, pickle, statistics as st, subprocess, sys
from pathlib import Path
S = Path(sys.argv[1])                                    # 작업 폴더 (scn/out, long/report15.md.pkl 이 있는 곳)
HERE = Path(__file__).resolve().parent
CODE = HERE.parent
O = S / "scn" / "out"
A = pickle.load(open(S / "long" / "report15.md.pkl", "rb"))["A"]
pct = lambda v: "-" if v is None else "%+.2f%%" % (100 * v)
mean = lambda v: sum(v) / len(v) if v else None
f4 = lambda s: s["f"].get("4시간 수익") is not None and s["f"]["4시간 수익"] <= -0.0592
fb = lambda s: s["f"].get("BTC 24시간 수익") is not None and s["f"]["BTC 24시간 수익"] <= -0.0173
def days(sigs):
    ev = {}
    for s in sigs: ev.setdefault((s["day"], s["q"]), []).append(s["net"][4])
    d = {}
    for (day, _q), v in ev.items(): d.setdefault(day, []).append(mean(v))
    return {k: mean(v) for k, v in d.items()}
X = ["### 조합(둘 다)의 이익이 몇 날에 몰려 있나 (2017~, 15분봉)", "",
     "| 기간 | 신호 / 일수 | 날짜별 평균 | 플러스인 날 비율 | 가장 좋은 하루를 뺀 날짜별 평균 | 가장 좋은 5일을 뺀 날짜별 평균 | 신호 합계 기준: 이익 상위 1일의 비중 | 가장 몰린 종목(신호 비중) |", "|---|---|---|---|---|---|---|---|"]
for name, a, b in (("2017~22", "0000", "2023"), ("2023–24", "2023", "2025"), ("2025–26", "2025", "9999")):
    part = [s for s in A if f4(s) and fb(s) and a <= s["day"][:4] < b]
    d = days(part); v = sorted(d.values())
    tot = {}
    cnt = {}
    for s in part:
        tot[s["day"]] = tot.get(s["day"], 0.0) + s["net"][4]; cnt[s["m"]] = cnt.get(s["m"], 0) + 1
    ssum = sum(tot.values()); top = max(tot.items(), key=lambda x: x[1]); cm = max(cnt.items(), key=lambda x: x[1])
    X.append("| %s | %d / %d | %s | %.0f%% | %s | %s | %s %s | %s %.0f%% |" % (name, len(part), len(d), pct(mean(v)), 100.0 * sum(1 for x in v if x > 0) / len(v), pct(mean(v[:-1])), pct(mean(v[:-5])),
             top[0], ("%.0f%%" % (100 * top[1] / ssum)) if ssum > 0 else "(합계가 마이너스)", cm[0][4:], 100.0 * cm[1] / len(part)))
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]
commit = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
head = open(HERE / "head.md", encoding="utf-8").read()
tail = open(HERE / "tail.md", encoding="utf-8").read()
files = [("scn/surge8.py", "급등 여덟 조합·재현"), ("scn/crash3.py", "급락 진입 방식 비교·조합 재현"), ("scn/assemble.py", "보고서 묶기"), ("long/fetch.py", "시세 수집"),
         ("long/study.py", "15분봉 9년 지표 검토(조합 재현에 쓰는 신호 파일을 만든다)"), ("rebound/study.py", "2023–26 급락 신호와 5분봉 수집"),
         ("scn/out/surge_signals.csv.gz", "급등 신호별 원자료"), ("scn/out/crash_events.csv.gz", "급락 사건별 원자료")]
rep = ["## 재현 방법과 코드 버전", "", "저장소 커밋 %s 위에서 돌렸다. 코드는 이 보고서와 같은 커밋의 scalp/research/reports/crash_surge_2026-11 에 있다(아래 경로는 그 폴더 기준). 파이썬 %s, 추가 패키지 없음." % (commit, sys.version.split()[0]), "",
       "```", "python3 -I long/fetch.py long/data 15 2017-09-25 2026-11-20 8      # 15분봉 9년 (약 1시간)", "python3 -I long/fetch.py long/data 1 2025-11-20 2026-11-20 8        # 1분봉 1년 (약 1시간 20분)",
       "python3 -I rebound/study.py rebound/data                            # 2023–26 급락 신호 + 주변 5분봉", "python3 -I long/study.py long/data long/report15.md                 # 15분봉 신호·지표 (report15.md.pkl)",
       "python3 -I scn/surge8.py long/data scn/out                          # 급등 A~D, surge_signals.csv.gz", "python3 -I scn/crash3.py rebound/data long/report15.md.pkl scn/out  # 급락 E~H, crash_events.csv.gz",
       "python3 -I scn/assemble.py .                                        # 이 보고서", "```", "", "| 파일 | 내용 | sha256 앞 16자 |", "|---|---|---|"]
rep += ["| %s | %s | %s |" % (p, d, sha(CODE / p if (CODE / p).exists() else S / p)) for p, d in files]
body = [head, open(O / "surge_report.md", encoding="utf-8").read(), open(O / "crash_report.md", encoding="utf-8").read(), "\n".join(X), "", tail, "\n".join(rep), ""]
(O / "report.md").write_text("\n".join(body), encoding="utf-8")
print("\n".join(X))
