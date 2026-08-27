#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
queue_sim.py 회귀 테스트 — 합성 시나리오로 큐 역학을 검증한다.

나이브하게 짜면 위양성이 나온다는 게 이 시뮬레이터의 최대 위험이다.
각 테스트는 **틀렸을 때 반드시 깨지도록** 설계했다.
"""
import os, sys, gzip, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_sim as Q

MK = "KRW-TEST"
PASS, FAIL = [], []


def ob(ts, bids, asks, seq=0):
    u = []
    for i in range(max(len(bids), len(asks))):
        b = bids[i] if i < len(bids) else (0, 0)
        a = asks[i] if i < len(asks) else (0, 0)
        u.append({"bid_price": b[0], "bid_size": b[1],
                  "ask_price": a[0], "ask_size": a[1]})
    return {"type": "orderbook", "code": MK, "timestamp": ts,
            "orderbook_units": u, "_seq": seq}


def tr(ts, price, vol, taker_buy, seq=0):
    return {"type": "trade", "code": MK, "timestamp": ts, "trade_timestamp": ts,
            "trade_price": price, "trade_volume": vol,
            "ask_bid": "BID" if taker_buy else "ASK", "_seq": seq}


def write(events):
    fd, path = tempfile.mkstemp(suffix=".jsonl.gz")
    os.close(fd)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def sim_of(events, cancel_credit=0.0, order_krw=10000, cap_krw=1e9,
           latency_ms=100):
    """run_one 을 쓰지 않고 Sim 을 직접 돌려 내부 상태까지 본다."""
    s = Q.Sim(cancel_credit, order_krw, cap_krw, latency_ms, 30)
    path = write(events)
    try:
        for ts, m in Q.stream([path], MK):
            if m["type"] == "orderbook":
                s.on_book(ts, m)
            else:
                s.on_trade(ts, m)
    finally:
        os.remove(path)
    return s


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ─────────────────────────────────────────────────────── 1
def t1_queue_back():
    """새로 걸면 큐 맨 뒤여야 한다. 앞에 있던 잔량 전부가 내 앞이다."""
    ev = [ob(1000, [(100.0, 500.0)], [(101.0, 500.0)])]
    s = sim_of(ev)
    o = s.orders[1]
    check("1. 신규 주문은 큐 맨 뒤", o is not None and abs(o.queue_ahead - 500.0) < 1e-9,
          f"queue_ahead={o.queue_ahead if o else None} (기대 500)")


# ─────────────────────────────────────────────────────── 2
def t2_fifo():
    """체결은 FIFO — 내 앞이 다 소진돼야 나에게 온다. 앞이 남으면 체결 0."""
    ev = [ob(1000, [(100.0, 500.0)], [(101.0, 500.0)]),
          tr(1100, 100.0, 300.0, False)]          # 내 앞 500 중 300 만 소진
    s = sim_of(ev)
    o = s.orders[1]
    check("2. FIFO — 내 앞이 남으면 체결 없음",
          len(s.fills) == 0 and o is not None and abs(o.queue_ahead - 200.0) < 1e-9,
          f"체결 {len(s.fills)}건 · queue_ahead={o.queue_ahead if o else None} (기대 200)")


# ─────────────────────────────────────────────────────── 3
def t3_partial():
    """내 앞이 비고 남은 물량만큼만 부분 체결된다."""
    ev = [ob(1000, [(100.0, 500.0)], [(101.0, 500.0)]),
          tr(1100, 100.0, 550.0, False)]     # 앞 500 소진 + 나에게 50
    s = sim_of(ev, order_krw=10000)          # 내 수량 = 10000/100 = 100
    got = sum(f[3] for f in s.fills)
    o = s.orders[1]
    check("3. 부분 체결", abs(got - 50.0) < 1e-9 and o is not None and abs(o.qty - 50.0) < 1e-9,
          f"체결량={got} (기대 50) · 잔량={o.qty if o else None} (기대 50)")


# ─────────────────────────────────────────────────────── 4
def t4_scenario_spread():
    """미설명 감소(취소)에서 보수/중립/낙관이 **반드시 갈려야** 한다.
    안 갈리면 시나리오가 아무 일도 안 하는 것이고, 그건 이 시뮬의 존재 이유가 없다는 뜻."""
    ev = [ob(1000, [(100.0, 500.0)], [(101.0, 500.0)]),
          # 체결 없이 잔량만 500 → 100 (=400 취소). 내 앞이 얼마나 줄었나?
          ob(1200, [(100.0, 100.0)], [(101.0, 500.0)]),
          tr(1300, 100.0, 120.0, False)]     # 그 뒤 120 체결
    q = {}
    for sc in (0.0, 0.5, 1.0):
        s = sim_of(ev, cancel_credit=sc)
        o = s.orders[1]
        q[sc] = (o.queue_ahead if o else 0.0, sum(f[3] for f in s.fills))
    ok = q[0.0][0] > q[0.5][0] > q[1.0][0]
    check("4. cancel_credit 이 큐 전진을 실제로 가른다 (0 > 0.5 > 1.0)", ok,
          " · ".join(f"{k}: 앞={v[0]:.1f} 체결={v[1]:.1f}" for k, v in q.items()))


# ─────────────────────────────────────────────────────── 5
def t5_conservative_no_credit():
    """보수 시나리오는 취소를 **0% 인정**해야 한다 — 큐가 그대로여야 한다."""
    ev = [ob(1000, [(100.0, 500.0)], [(101.0, 500.0)]),
          ob(1200, [(100.0, 100.0)], [(101.0, 500.0)])]
    s = sim_of(ev, cancel_credit=0.0)
    o = s.orders[1]
    check("5. 보수 = 취소 0% 인정", o is not None and abs(o.queue_ahead - 500.0) < 1e-9,
          f"queue_ahead={o.queue_ahead if o else None} (기대 500 유지)")


# ─────────────────────────────────────────────────────── 6
def t6_level_gone():
    """관측 레벨이 비어도 내 주문은 **살아남고 큐 맨 앞**이 된다.
    큐 초기화는 레벨 소멸이 아니라 재호가 시점에 일어난다."""
    ev = [ob(1000, [(100.0, 500.0)], [(101.0, 500.0)]),
          ob(1200, [(99.0, 800.0)], [(101.0, 500.0)])]      # 100 레벨이 관측창에서 소멸
    s = sim_of(ev, latency_ms=10_000)                        # 지연을 길게 — 아직 재호가 전
    o = s.orders[1]
    ok = (o is not None and o.price == 100.0
          and abs(o.queue_ahead) < 1e-9 and s.stat["level_gone"] >= 1)
    check("6. 레벨 소멸 → 주문 생존 · 큐 맨 앞", ok,
          f"price={o.price if o else None} (기대 100) · "
          f"queue_ahead={o.queue_ahead if o else None} (기대 0)")


def t6b_requote_to_back():
    """재호가는 **새 큐 맨 뒤**로 간다. 이게 빠지면 큐 위치가 영원히 유지된다."""
    ev = [ob(1000, [(100.0, 500.0)], [(101.0, 500.0)]),
          ob(1200, [(99.0, 800.0)], [(101.0, 500.0)]),
          ob(1400, [(99.0, 800.0)], [(101.0, 500.0)]),      # latency 경과 → 철회
          ob(1600, [(99.0, 800.0)], [(101.0, 500.0)])]      # 재배치
    s = sim_of(ev, latency_ms=100)
    o = s.orders[1]
    ok = (o is not None and o.price == 99.0
          and abs(o.queue_ahead - 800.0) < 1e-9 and s.stat["requotes"] >= 1)
    check("6b. 재호가는 새 큐 맨 뒤", ok,
          f"price={o.price if o else None} (기대 99) · "
          f"queue_ahead={o.queue_ahead if o else None} (기대 800) · "
          f"requotes={int(s.stat['requotes'])}")


# ─────────────────────────────────────────────────────── 7
def t7_stale_fill():
    """가격이 움직인 뒤 latency 안에 들어온 체결은 **낡은 호가 체결**로 표시돼야 한다."""
    ev = [ob(1000, [(100.0, 0.0)], [(101.0, 500.0)]),        # 내 앞 0 — 바로 최전선
          ob(1100, [(99.0, 500.0)], [(100.0, 500.0)]),       # 최우선매수가 99 로 하락
          tr(1150, 100.0, 50.0, False)]                      # 낡은 100 호가가 맞음
    s = sim_of(ev, latency_ms=500)
    stale = [f for f in s.fills if f[5]]
    check("7. 지연 중 낡은 호가 체결이 표시된다", len(stale) >= 1,
          f"체결 {len(s.fills)}건 중 낡은체결 {len(stale)}건")


def t7b_no_stale_after_requote():
    """latency 가 짧으면 철회가 끝나 낡은 체결이 없어야 한다."""
    ev = [ob(1000, [(100.0, 0.0)], [(101.0, 500.0)]),
          ob(1100, [(99.0, 500.0)], [(100.0, 500.0)]),
          ob(1200, [(99.0, 500.0)], [(100.0, 500.0)]),       # latency 10ms → 이미 철회
          tr(1300, 100.0, 50.0, False)]
    s = sim_of(ev, latency_ms=10)
    stale = [f for f in s.fills if f[5]]
    check("7b. 지연이 짧으면 낡은 체결이 없다", len(stale) == 0,
          f"낡은체결 {len(stale)}건 (기대 0)")


# ─────────────────────────────────────────────────────── 8
def t8_inventory_cap():
    """재고 상한을 넘기는 방향은 호가를 걸지 않아야 한다."""
    ev = [ob(1000, [(100.0, 0.0)], [(101.0, 0.0)]),
          tr(1100, 100.0, 1000.0, False),                    # 대량 매수 체결 → 롱 누적
          ob(1200, [(100.0, 0.0)], [(101.0, 0.0)])]
    s = sim_of(ev, order_krw=10000, cap_krw=5000)            # 상한 5,000원
    ok = abs(s.pos * 100.0) <= 5000 * 1.5 or s.orders[1] is None
    check("8. 재고 상한이 매수 호가를 막는다", ok,
          f"재고 {s.pos*100:.0f}원 · 매수호가 {'있음' if s.orders[1] else '없음'}")


# ─────────────────────────────────────────────────────── 9
def t9_liquidation_taker():
    """청산은 테이커가로 — 롱이면 **매수호가(bid)** 에 던져야 한다."""
    s = Q.Sim(0.0, 10000, 1e9, 100, 30)
    s.bk.bid = {100.0: 500.0}
    s.bk.ask = {110.0: 500.0}
    s.bk.ts = 1000
    s.pos = 10.0
    s.cash = 0.0
    s.liquidate(2000)
    expect = 10.0 * 100.0 * (1 - Q.FEE)         # bid 에 팔고 수수료
    ok = abs(s.cash - expect) < 1e-6 and abs(s.pos) < 1e-12
    check("9. 청산은 테이커가(롱→bid)", ok,
          f"현금={s.cash:.2f} (기대 {expect:.2f}) · 잔여재고={s.pos}")


# ─────────────────────────────────────────────────────── 10
def t10_no_lookahead():
    """미래 이벤트가 현재 판단에 못 들어간다 — 뒤 이벤트를 지워도 앞 결과가 같아야 한다."""
    base = [ob(1000, [(100.0, 500.0)], [(101.0, 500.0)]),
            tr(1100, 100.0, 300.0, False)]
    extra = base + [tr(9000, 100.0, 9999.0, False),
                    ob(9100, [(100.0, 1.0)], [(101.0, 1.0)])]
    a = sim_of(base)
    b = sim_of(extra)
    # 앞 구간(ts<=1100)의 체결은 동일해야 한다
    fa = [f for f in a.fills if f[0] <= 1100]
    fb = [f for f in b.fills if f[0] <= 1100]
    check("10. look-ahead 차단", fa == fb,
          f"앞구간 체결 {len(fa)} vs {len(fb)}")


# ─────────────────────────────────────────────────────── 11
def t11_pnl_identity():
    """손익 분해가 **항등식**이어야 한다: 순손익 = 스프레드포획 + 재고손익 − 수수료.
    잔차가 0 이 아니면 분해가 거짓말을 하고 있는 것이고, 그러면 '청산이 수익
    대부분을 먹었나' 같은 판정을 할 수 없다."""
    ev = [ob(1000, [(100.0, 0.0)], [(102.0, 0.0)]),
          tr(1100, 100.0, 5.0, False),                 # MM 매수
          ob(1200, [(101.0, 0.0)], [(103.0, 0.0)]),    # 가격 상승
          tr(1300, 103.0, 3.0, True),                  # MM 매도
          ob(1400, [(104.0, 0.0)], [(106.0, 0.0)])]
    s = sim_of(ev, order_krw=1000, latency_ms=10)
    s.liquidate(1400)
    resid = s.cash - (s.stat["spread"] + s.stat["inventory"] - s.stat["fees"])
    scale = max(abs(s.cash), abs(s.stat["spread"]), 1.0)
    check("11. 손익 분해 항등식 (잔차 ≈ 0)", abs(resid) < scale * 1e-6,
          f"순={s.cash:.4f} = 스프레드 {s.stat['spread']:.4f} + 재고 "
          f"{s.stat['inventory']:.4f} − 수수료 {s.stat['fees']:.4f} · 잔차 {resid:.2e}")


# ─────────────────────────────────────────────────────── 12
def t12_credit_monotone():
    """같은 이벤트 집합에서 cancel_credit 이 클수록 체결이 **줄지 않아야** 한다.
    credit 은 큐를 앞당기기만 하므로 체결 기회가 감소할 수 없다."""
    ev = [ob(1000, [(100.0, 400.0)], [(101.0, 400.0)]),
          ob(1200, [(100.0, 150.0)], [(101.0, 400.0)]),   # 250 미설명 감소
          tr(1300, 100.0, 200.0, False)]
    n = {}
    for cc in (0.0, 0.5, 1.0):
        s = sim_of(ev, cancel_credit=cc)
        n[cc] = sum(f[3] for f in s.fills)
    ok = n[0.0] <= n[0.5] <= n[1.0]
    check("12. credit 증가 → 체결 비감소", ok,
          " · ".join(f"cc={k}: {v:.1f}" for k, v in n.items()))


def main():
    print("queue_sim.py 회귀 테스트")
    print("=" * 66)
    for f in (t1_queue_back, t2_fifo, t3_partial, t4_scenario_spread,
              t5_conservative_no_credit, t6_level_gone, t6b_requote_to_back,
              t7_stale_fill,
              t7b_no_stale_after_requote, t8_inventory_cap,
              t9_liquidation_taker, t10_no_lookahead,
              t11_pnl_identity, t12_credit_monotone):
        try:
            f()
        except Exception as e:
            FAIL.append(f.__name__)
            print(f"  [FAIL] {f.__name__} — 예외 {type(e).__name__}: {e}")
    print("=" * 66)
    print(f"PASS {len(PASS)} · FAIL {len(FAIL)}")
    if FAIL:
        print("실패:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
