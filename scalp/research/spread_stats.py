#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스프레드 실측 — 비용 모형의 가장 약한 가정을 데이터로 대체한다.

지금까지 모든 비용 계산은 **2026-08-23 에 한 번 찍은 스프레드 스냅샷**에
걸려 있었다. FLOW_FINDINGS.md 에 한계로 명시해 둔 그 값이다. 레코더가
실제 오더북을 쌓았으므로 이제 분포로 바꿀 수 있다.

왜 결론이 바뀔 수 있나
----------------------
틱 연구의 최고 원시 기대값은 KRW-XRP 11.99bp, 비용 하한은 15.06bp 였다.
차이가 3bp 다. 실제 스프레드가 스냅샷보다 3bp 좁기만 해도 판정이 뒤집힌다.
그리고 스프레드가 좁아지는 **시간대**가 따로 있다면 "그 시간대만 거래한다"는
규칙이 성립할 수도 있다. 그래서 전체 분포와 함께 시간대별로도 낸다.

읽는 법
-------
· 스프레드는 시간가중이 아니라 **이벤트 가중**이다. 호가가 자주 바뀌는
  구간이 더 많이 반영된다. 실제로 주문을 내는 순간도 그런 구간이므로
  거래 관점에서는 이쪽이 맞다. 다만 "하루 평균 스프레드"와는 다른 값이다.
· 교차(bid >= ask)와 한쪽이 빈 프레임은 제외한다. 스냅샷 경계에서 드물게
  나오는데, 포함하면 분포 왼쪽 꼬리가 오염된다.
· p50 이 아니라 **p25 도 같이 본다.** 진입 시점을 고를 수 있다면 중앙값이
  아니라 좁은 쪽에서 체결될 수 있기 때문이다. 다만 그건 '고를 수 있다'는
  가정이 필요하고, 그 가정 자체는 여기서 검증되지 않는다.
"""
import os, sys, gzip, json, glob, argparse, datetime
from collections import defaultdict

R = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(R, "data", "ws")
FEE_ROUNDTRIP_BP = 10.0


def quantiles(vals, qs=(0.05, 0.25, 0.50, 0.75, 0.95)):
    if not vals:
        return {q: float("nan") for q in qs}
    vals = sorted(vals)
    n = len(vals)
    out = {}
    for q in qs:
        i = min(n - 1, max(0, int(round(q * (n - 1)))))
        out[q] = vals[i]
    return out


def scan(paths, markets, sample):
    """오더북 프레임을 흘려 읽으며 스프레드를 모은다. 메모리에 원본을 안 쌓는다."""
    per = defaultdict(list)          # market -> [spread_bp]
    per_hour = defaultdict(list)     # (market, hour) -> [spread_bp]
    tick_gap = defaultdict(set)      # market -> 관측된 호가 간격 (틱 검증용)
    n_seen = n_bad = 0
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if sample > 1 and (i % sample):
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if m.get("type") != "orderbook":
                    continue
                code = m.get("code")
                if markets and code not in markets:
                    continue
                u = m.get("orderbook_units") or []
                if not u:
                    continue
                b = u[0].get("bid_price")
                a = u[0].get("ask_price")
                if not b or not a:
                    continue
                n_seen += 1
                if a <= b:                      # 교차/이상 프레임은 버린다
                    n_bad += 1
                    continue
                mid = (a + b) / 2.0
                sp = (a - b) / mid * 1e4
                per[code].append(sp)
                ts = m.get("timestamp")
                if ts:
                    h = datetime.datetime.utcfromtimestamp(ts / 1000).hour
                    per_hour[(code, h)].append(sp)
                # 같은 스냅샷 안의 인접 호가 간격 = 그 시점의 실제 틱
                for j in range(min(4, len(u) - 1)):
                    try:
                        g = round(u[j]["bid_price"] - u[j + 1]["bid_price"], 8)
                        if g > 0:
                            tick_gap[code].add(g)
                    except Exception:
                        pass
    return per, per_hour, tick_gap, n_seen, n_bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", default=[])
    ap.add_argument("--markets", default="")
    ap.add_argument("--sample", type=int, default=1,
                    help="N 줄마다 1개만 본다. 1 vCPU 서버에서 훑을 때 쓴다")
    ap.add_argument("--days", type=int, default=0,
                    help="최근 N 일치 자동 선택 (paths 를 안 줄 때)")
    ap.add_argument("--hourly", action="store_true", help="시간대별 표까지 낸다")
    ap.add_argument("--save", default="")
    a = ap.parse_args()

    paths = []
    for p in a.paths:
        paths += sorted(glob.glob(p))
    if not paths:
        days = sorted(os.listdir(WS)) if os.path.isdir(WS) else []
        if a.days:
            days = days[-a.days:]
        for d in days:
            paths += sorted(glob.glob(os.path.join(WS, d, "*.jsonl.gz")))
    if not paths:
        sys.exit("입력 파일 없음")

    mks = set(s.strip() for s in a.markets.split(",") if s.strip())
    print(f"[spread] 파일 {len(paths)}개 · 샘플링 1/{a.sample}", flush=True)
    per, per_hour, tick_gap, n_seen, n_bad = scan(paths, mks, a.sample)

    print(f"[spread] 오더북 프레임 {n_seen:,} · 교차/이상 제외 {n_bad:,}\n")
    print("=" * 96)
    print("스프레드 분포 (bp) — 이벤트 가중. 비용 = 수수료 왕복 10bp + 스프레드")
    print("-" * 96)
    print(f"{'종목':<11}{'n':>10}{'p05':>8}{'p25':>8}{'p50':>8}{'p75':>8}{'p95':>8}"
          f"{'비용p50':>9}{'비용p25':>9}{'관측틱':>18}")
    rows = {}
    for mk in sorted(per):
        q = quantiles(per[mk])
        ticks = sorted(tick_gap.get(mk, []))[:3]
        tstr = "/".join(f"{t:g}" for t in ticks) if ticks else "-"
        rows[mk] = {str(k): v for k, v in q.items()}
        print(f"{mk:<11}{len(per[mk]):>10,}{q[0.05]:>8.2f}{q[0.25]:>8.2f}"
              f"{q[0.50]:>8.2f}{q[0.75]:>8.2f}{q[0.95]:>8.2f}"
              f"{FEE_ROUNDTRIP_BP+q[0.50]:>9.2f}{FEE_ROUNDTRIP_BP+q[0.25]:>9.2f}"
              f"{tstr:>18}")

    if a.hourly:
        print("\n" + "=" * 96)
        print("시간대별 스프레드 중앙값 (UTC · KST = +9) — 좁아지는 시간대가 있는가")
        print("-" * 96)
        hdr = "".join(f"{h:>4}" for h in range(24))
        print(f"{'종목':<11}{hdr}")
        for mk in sorted(per):
            cells = []
            for h in range(24):
                v = per_hour.get((mk, h))
                cells.append(f"{quantiles(v)[0.50]:>4.0f}" if v else "   -")
            print(f"{mk:<11}" + "".join(cells))
        print("\n※ 값은 bp. 특정 시간대만 뚜렷이 낮으면 그 시간대만 거래하는 규칙이")
        print("  성립할 수 있다. 다만 그 시간대에 신호도 있는지는 별개 문제다.")

    print("\n" + "=" * 96)
    print("스냅샷 가정과의 차이 — 기존 결론이 뒤집히는지 확인할 것")
    print("-" * 96)
    snap = os.path.join(R, "results", "tick_size.json")
    if os.path.exists(snap):
        off = {r["market"]: r for r in
               json.load(open(snap, encoding="utf-8")).get("rows", [])}
        print(f"{'종목':<11}{'스냅샷':>9}{'실측p50':>9}{'차이':>8}"
              f"{'기존비용':>9}{'새비용':>8}{'판정':>22}")
        for mk in sorted(per):
            if mk not in off:
                continue
            s0 = float(off[mk]["spread_bp_now"])
            s1 = quantiles(per[mk])[0.50]
            c0, c1 = FEE_ROUNDTRIP_BP + s0, FEE_ROUNDTRIP_BP + s1
            # 틱 연구의 전 셀 최고 원시 기대값 (results/flow_sweep.json 기준)
            verdict = "변화 없음" if abs(c1 - c0) < 0.5 else (
                "비용 낮아짐 — 재검토" if c1 < c0 else "비용 높아짐")
            print(f"{mk:<11}{s0:>9.2f}{s1:>9.2f}{s1-s0:>+8.2f}"
                  f"{c0:>9.2f}{c1:>8.2f}{verdict:>22}")
    else:
        print("results/tick_size.json 이 없어 비교 생략")

    if a.save:
        json.dump(dict(n_frames=n_seen, n_bad=n_bad, files=len(paths),
                       spread_bp=rows), open(a.save, "w"),
                  ensure_ascii=False, indent=1)
        print(f"\n저장 {a.save}")


if __name__ == "__main__":
    main()
