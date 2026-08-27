#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
지정가 유동성 공급 시뮬레이션 — 재고 통제 · 큐 위치 · 수수료 포함.

왜 이 축인가
------------
방향성 신호는 두 파이프라인 2,193셀에서 전부 비용에 막혔다(1분봉 693셀,
틱 주문흐름 1,500셀). 실패는 매번 **신호 부재가 아니라 비용 초과**였다.
그러면 레버는 신호가 아니라 비용 쪽에 있다.

업비트 KRW 는 maker/taker 수수료 구분이 **없어서**(왕복 10bp 고정) '지정가로
수수료를 아낀다'는 레버는 존재하지 않는다. 남는 레버는 하나뿐이다 —
**스프레드를 내는 대신 버는 것.** 그러려면 스프레드가 10bp 를 넘어야 한다.

전 KRW 287종목 조사 결과 274종목이 10bp 를 넘는다. 그중 KRW-DOGE 같은
종목은 스프레드가 **정확히 1틱**이다(122원에 1원 틱 = 82bp). 유동성이
없어서가 아니라 호가단위가 더 좁은 호가를 **금지**해서 넓다. 24h 224억,
최우선호가에만 5.2억원이 쌓여 있다. 구조적으로 잠긴 스프레드다.

이 시뮬레이터가 검증하는 것
---------------------------
1. **재고 통제.** 상한 없이 모든 체결의 반대편을 잡으면 그건 마켓메이킹이
   아니라 방향성 베팅이다. 실제로 테이커 매수비가 0.38~0.42 라 MM 은 계속
   사기만 하고, 관측 3일간 세 종목 다 -543~-584bp 떨어져서 무제한 재고는
   XLM -9.4bp / ADA -24.6bp 로 죽었다. 상한을 걸면 방향성이 잘려나가고
   순수 스프레드 포획만 남는다.
2. **큐 위치.** 최우선호가에 5.2억이 쌓인 시장에서 소액 참여자는 대기열
   뒤에 선다. 뒤에 선 주문은 **큐가 쓸릴 때만** 체결되고 그건 정보가 있는
   거래일 때다. 체결 규모 상위 20% 만 잡히는 것으로 이 조건을 근사한다.
3. **수수료.** 편도 0.05% 를 체결마다, 최종 청산에도 부과한다.

여전히 검증되지 않은 것 (결과를 읽을 때 반드시 같이 볼 것)
---------------------------------------------------------
· **큐 모형이 거칠다.** '체결 규모 상위 20%'는 큐 소진의 대리변수일 뿐
  실제 대기열 시뮬레이션이 아니다. 진짜 검증은 오더북이 필요하다.
· **호가 갱신·지연이 없다.** 실제 MM 은 가격이 움직이면 호가를 물려야 하고,
  느리면 그 사이에 얻어맞는다. 여기선 호가가 항상 제자리에 있다고 본다.
· **호가 경쟁이 없다.** 내가 걸면 대기열이 그만큼 길어진다.
· **표본이 짧고 한 국면이다.** 관측 구간에서 세 종목이 모두 하락했다.
  상승 국면에서도 같은지는 이 데이터로 답할 수 없다.
따라서 여기서 음수면 확실히 죽은 것이고, 양수라도 채택이 아니라
오더북 기반 큐 시뮬레이션으로 넘어가는 조건이다.
"""
import os, gzip, json, datetime, argparse
import numpy as np

R = os.path.dirname(os.path.abspath(__file__))
TICKS = os.path.join(R, "data", "ticks")
FEE = 0.0005                 # 편도 0.05% (업비트 KRW, maker/taker 동일)


def load(market):
    d = os.path.join(TICKS, market)
    seen, rows = set(), []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".gz"):
            continue
        with gzip.open(os.path.join(d, fn), "rt") as f:
            for ln in f:
                r = json.loads(ln)
                sid = r.get("sequential_id")
                if sid is not None:
                    if sid in seen:
                        continue
                    seen.add(sid)
                rows.append((r["timestamp"], r["trade_price"],
                             r["trade_volume"], r["ask_bid"] == "BID"))
    rows.sort()
    return (np.array([r[0] for r in rows], dtype=np.int64),
            np.array([r[1] for r in rows], float),
            np.array([r[2] for r in rows], float),
            np.array([r[3] for r in rows], bool))


def infer_tick(px):
    u = np.unique(px)
    g = np.diff(u)
    g = g[g > 0]
    return float(np.min(g)) if len(g) else float("nan")


def simulate(ts, px, vol, buy, tick, part, cap_krw, mask):
    """MM 은 테이커의 반대편을 잡는다. 재고를 **키우는** 방향이고 상한을
    넘으면 체결하지 않는다 (그쪽 호가를 뺀 것과 같다)."""
    mm = -np.where(buy, 1.0, -1.0)
    pos = cash = turn = gross = 0.0
    filled = skipped = 0
    peak = 0.0
    for i in range(len(ts)):
        if not mask[i]:
            continue
        q, p, d = vol[i] * part, px[i], mm[i]
        new = pos + d * q
        if cap_krw and abs(new) > abs(pos) and abs(new * p) > cap_krw:
            skipped += 1
            continue
        cash -= d * q * p + q * p * FEE
        pos = new
        turn += q * p
        gross += q * p * (tick / 2 / p)      # 스프레드 수입(반스프레드)
        filled += 1
        peak = max(peak, abs(pos * p))
    mid_last = px[-1] - (1.0 if buy[-1] else -1.0) * tick / 2
    cash += pos * mid_last - abs(pos) * mid_last * FEE      # 잔여 재고 청산
    return dict(pnl=cash, turn=turn, gross=gross, fees=turn * FEE,
                filled=filled, skipped=skipped, peak=peak,
                bp=(cash / turn * 1e4) if turn else float("nan"))


def run(market, part, cap, big_q):
    ts, px, vol, buy = load(market)
    tick = infer_tick(px)
    d = np.abs(np.diff(px))
    d = d[d > 0]
    one_tick = float(np.mean(np.isclose(d, tick))) if len(d) else float("nan")
    med = float(np.median(px))
    cut = np.quantile(vol, big_q)
    masks = {"맨앞": np.ones(len(ts), bool),
             "중간": vol < cut,
             "뒷줄": vol >= cut}
    out = dict(market=market, n=len(ts), tick=tick, price=med,
               spread_bp=tick / med * 1e4, one_tick_frac=one_tick,
               taker_buy=float(buy.mean()),
               drift_bp=(px[-1] - px[0]) / px[0] * 1e4, fills={})
    for k, m in masks.items():
        out["fills"][k] = simulate(ts, px, vol, buy, tick, part, cap, m)
    # 일별 안정성 — 뒷줄(비관) 모형으로만 본다
    days = np.array([datetime.datetime.utcfromtimestamp(t / 1000).strftime("%m-%d")
                     for t in ts])
    daily = {}
    for dd in sorted(set(days)):
        m = (days == dd) & (vol >= cut)
        if m.sum() < 300:
            continue
        daily[dd] = simulate(ts, px, vol, buy, tick, part, cap, m)["bp"]
    out["daily_bp"] = daily
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="")
    ap.add_argument("--participation", type=float, default=0.05)
    ap.add_argument("--cap-krw", type=float, default=200e4)
    ap.add_argument("--big-quantile", type=float, default=0.80)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    mks = ([s.strip() for s in a.markets.split(",") if s.strip()]
           or sorted(os.listdir(TICKS)))

    res = []
    for mk in mks:
        try:
            res.append(run(mk, a.participation, a.cap_krw, a.big_quantile))
        except Exception as e:
            print(f"  ! {mk}: {type(e).__name__}: {e}")
    res.sort(key=lambda r: -r["fills"]["뒷줄"]["bp"])

    print("=" * 104)
    print(f"지정가 유동성 공급 · 재고상한 {a.cap_krw/1e4:,.0f}만원 · "
          f"참여율 {a.participation*100:.0f}% · 수수료 왕복 10bp 포함")
    print("채택 기준은 **뒷줄**(큐가 쓸릴 때만 체결)이다. 소액 참여자의 조건이다.")
    print("-" * 104)
    print(f"{'종목':<12}{'체결수':>9}{'스프bp':>8}{'1틱비율':>8}{'기간등락':>9}"
          f"{'테이커매수':>10}{'맨앞':>8}{'중간':>8}{'뒷줄':>8}{'일별(뒷줄)':>22}")
    for r in res:
        dl = " ".join(f"{v:+.0f}" for v in r["daily_bp"].values())
        print(f"{r['market']:<12}{r['n']:>9,}{r['spread_bp']:>8.1f}"
              f"{r['one_tick_frac']:>8.3f}{r['drift_bp']:>+9.0f}"
              f"{r['taker_buy']:>10.3f}{r['fills']['맨앞']['bp']:>+8.1f}"
              f"{r['fills']['중간']['bp']:>+8.1f}{r['fills']['뒷줄']['bp']:>+8.1f}"
              f"{dl:>22}")

    ok = [r for r in res if r["fills"]["뒷줄"]["bp"] > 0
          and r["daily_bp"] and min(r["daily_bp"].values()) > 0]
    print("\n" + "-" * 104)
    print(f"뒷줄 양수 + 전 일자 양수: {len(ok)}/{len(res)}종목"
          + (" → " + ", ".join(r["market"] for r in ok) if ok else ""))
    print("※ 통과해도 채택이 아니다. 큐 모형이 거칠고(체결규모 대리변수),")
    print("  호가 갱신·지연·경쟁이 없으며, 표본 구간이 한 국면이다.")

    if a.save:
        p = os.path.join(R, "results", "mm_sim.json")
        json.dump(dict(params=vars(a), rows=res), open(p, "w"),
                  ensure_ascii=False, indent=1, default=float)
        print(f"\n저장 {p}")


if __name__ == "__main__":
    main()
