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
           latency_ms=100, init_cash=1e12, init_asset=1e12):
    """run_one 을 쓰지 않고 Sim 을 직접 돌려 내부 상태까지 본다."""
    s = Q.Sim(cancel_credit, order_krw, cap_krw, latency_ms, 30,
              init_cash, init_asset)
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
    """관측창 **안**에서의 전량 소멸은 부분 취소와 **같은 경로**를 타야 한다.

    예전 버전은 여기서 queue_ahead 를 0 으로 순간이동시켰다. 그러면
    부분취소 → credit=0, 전량취소 → credit=1.0 이 되어 credit 함수가 불연속이
    되고 cc=0 이 하한이 아니게 된다. 지금은 cc 를 그대로 탄다."""
    ev = [ob(1000, [(100.0, 500.0), (99.0, 300.0)], [(101.0, 500.0)]),
          ob(1200, [(99.0, 300.0)], [(101.0, 500.0)])]      # 100 이 관측창 안에서 소멸
    q = {}
    for cc in (0.0, 1.0):
        s = sim_of(ev, cancel_credit=cc, latency_ms=10_000)
        o = s.orders[1]
        q[cc] = (o.price if o else None, o.queue_ahead if o else None)
    ok = (q[0.0] == (100.0, 500.0)) and (q[1.0] == (100.0, 0.0))
    check("6. 전량 소멸도 cancel_credit 을 탄다 (불연속 제거)", ok,
          f"cc=0 → {q[0.0]} (기대 (100.0, 500.0)) · cc=1 → {q[1.0]} (기대 (100.0, 0.0))")


def t6c_window_exit():
    """관측창 **밖**(매수에서 더 낮은 가격)으로 밀리면 큐를 알 수 없다.
    cc=1.0 이어도 전진시키면 안 된다 — '안 보이니 앞이 비었을 것'은 낙관이다."""
    ev = [ob(1000, [(100.0, 500.0), (99.0, 300.0)], [(101.0, 500.0)]),
          # 시장이 올라 관측창이 [105,106] 로 이동 — 내 100 은 더 깊은 쪽이라 안 보임
          ob(1200, [(106.0, 400.0), (105.0, 400.0)], [(107.0, 400.0)])]
    s = sim_of(ev, cancel_credit=1.0, latency_ms=10_000)
    o = s.orders[1]
    ok = (o is not None and o.q_unknown and abs(o.queue_ahead - 500.0) < 1e-9
          and s.stat["window_exit"] >= 1)
    check("6c. 관측창 밖 → 큐 판단 불가, credit 정지", ok,
          f"q_unknown={o.q_unknown if o else None} · "
          f"queue_ahead={o.queue_ahead if o else None} (기대 500 유지) · "
          f"window_exit={int(s.stat['window_exit'])}")


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
    s = Q.Sim(0.0, 10000, 1e9, 100, 30, 1e12, 1e12)
    s.bk.bid = {100.0: 500.0}
    s.bk.ask = {110.0: 500.0}
    s.bk.ts = 1000
    s.mid0 = 105.0
    s.target = 0.0
    s.asset = 10.0                              # target 대비 편차 +10
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
    s = sim_of(ev, order_krw=1000, latency_ms=10, init_cash=1e6, init_asset=1e6)
    s.liquidate(1400)
    eq0 = s.init_cash + s.init_asset_krw
    net = s.equity(s.bk.mid()) - eq0
    resid = net - (s.stat["spread"] + s.stat["inventory"] + s.stat["market"]
                   - s.stat["fees"])
    scale = max(abs(net), abs(s.stat["spread"]), 1.0)
    check("11. 손익 분해 항등식 (잔차 ≈ 0)", abs(resid) < scale * 1e-6,
          f"순={net:.4f} = 스프레드 {s.stat['spread']:.4f} + 재고 "
          f"{s.stat['inventory']:.4f} + 시장 {s.stat['market']:.4f} "
          f"− 수수료 {s.stat['fees']:.4f} · 잔차 {resid:.2e}")


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


# ─────────────────────────────────────────────────────── 13
def _staged(cap=1e9, cash=1e9, asset_qty=100.0, pos_qty=0.0, price=100.0):
    """제약이 **체결 시점에** 작동하는지 보려면 배치 게이트를 우회해야 한다.
    게이트에서 먼저 막히면 그 경로가 아예 실행되지 않는다."""
    s = Q.Sim(0.0, 10_000, cap, 100, 30, cash, asset_qty * price)
    s.bk.bid = {price: 0.0}
    s.bk.ask = {price + 1: 0.0}
    s.bk.ts = 1000
    s._init_balances(s.bk.mid())
    # 환산은 mid 로 이뤄지므로 실제 보유량 기준으로 target 을 잡는다
    s.target = s.asset - pos_qty            # pos = asset - target
    return s


def t13_no_naked_short():
    """현물은 공매도가 없다. 보유량을 넘는 매도는 체결 시점에 깎여야 한다."""
    s = _staged(asset_qty=5.0, cap=1e9)     # 약 5개 보유
    held = s.asset
    got = s._cap_fill(-1, 100.0, 50.0)      # 50개 매도 시도
    check("13. 공매도 금지 — 보유량 초과 매도가 체결 시점에 깎인다",
          abs(got - held) < 1e-9 and s.n_reject_asset >= 1,
          f"체결 허용량 {got:.6f} (기대 보유량 {held:.6f}) · "
          f"코인부족 거절 {s.n_reject_asset}회")


def t13b_no_overspend():
    """보유 원화를 넘는 매수도 체결 시점에 깎여야 한다."""
    s = _staged(cash=1000.0)                # 1,000원 보유
    got = s._cap_fill(1, 100.0, 50.0)       # 5,000원어치 매수 시도
    afford = 1000.0 / (100.0 * (1 + Q.FEE))
    check("13b. 원화 초과지출 불가", abs(got - afford) < 1e-9 and s.n_reject_cash >= 1,
          f"체결 허용량 {got:.4f} (기대 {afford:.4f}) · 현금부족 거절 {s.n_reject_cash}회")


# ─────────────────────────────────────────────────────── 14
def t14_fill_time_cap():
    """상한은 배치 게이트가 아니라 **상태 제약**이다.
    재고 195만인데 예전에 걸어둔 30만 주문이 체결되면 225만이 되면 안 된다."""
    # 상한 200만, 현재 재고 편차 +19,500개 × 100원 = 195만원
    s = _staged(cap=2_000_000.0, asset_qty=100_000.0, pos_qty=19_500.0)
    room = 2_000_000.0 / 100.0 - s.pos      # 상한까지 남은 수량
    got = s._cap_fill(1, 100.0, 3_000.0)    # 30만원어치 추가 매수
    ok = abs(got - room) < 1e-6 and s.n_clip_cap >= 1 and got < 3_000.0
    check("14. 체결 시점 재고 상한", ok,
          f"체결 허용량 {got:.1f}개 (기대 잔여 {room:.1f}) · "
          f"요청 3,000 에서 깎임 · 상한클립 {s.n_clip_cap}회")


def t14b_cap_allows_reducing():
    """상한에 걸려 있어도 재고를 **줄이는** 방향은 막으면 안 된다."""
    s = _staged(cap=2_000_000.0, asset_qty=100_000.0, pos_qty=20_000.0)  # 상한 정확히
    got = s._cap_fill(-1, 100.0, 3_000.0)   # 매도 = 편차 감소
    check("14b. 상한에서도 재고 감소는 허용", abs(got - 3_000.0) < 1e-6,
          f"체결 허용량 {got:.1f}개 (기대 3,000 전량)")


def t14c_asset_never_negative():
    """통합 검정 — 어떤 경로로도 보유량이 음수가 되지 않는다."""
    ev = [ob(1000, [(100.0, 0.0)], [(101.0, 0.0)]),
          tr(1100, 101.0, 5000.0, True),
          ob(1200, [(100.0, 0.0)], [(101.0, 0.0)]),
          tr(1300, 101.0, 5000.0, True)]
    s = sim_of(ev, order_krw=1000, cap_krw=1e9, init_cash=1e9, init_asset=2000.0)
    check("14c. 보유량이 음수가 되지 않는다",
          s.asset is not None and s.asset >= -1e-9,
          f"최종 보유 {s.asset:.6f}개")


# ─────────────────────────────────────────────────────── 15
def t15_gap_reset():
    """녹화 공백을 넘어 큐 상태를 이어가면 안 된다."""
    ev = [ob(1000, [(100.0, 500.0)], [(101.0, 500.0)]),
          ob(1200, [(100.0, 500.0)], [(101.0, 500.0)]),
          # 2시간 공백
          ob(7_201_000, [(100.0, 500.0)], [(101.0, 500.0)])]
    s = sim_of(ev, latency_ms=10)
    check("15. 공백 → 세션 단절", s.stat["gap_resets"] >= 1 and s.seg >= 1,
          f"gap_resets={int(s.stat['gap_resets'])} · seg={s.seg}")


# ─────────────────────────────────────────────────────── 16
def t16_book_stale_skip():
    """호가가 너무 낡으면 그 체결로 채우지 않는다 (근거 없는 수익 방지)."""
    ev = [ob(1000, [(100.0, 0.0)], [(101.0, 0.0)]),
          # 공백 리셋(60초)에는 안 걸리고 호가 신선도(30초)에만 걸리도록 40초 뒤
          tr(41_000, 100.0, 50.0, False)]
    s = sim_of(ev, latency_ms=10_000)
    check("16. 낡은 호가로는 체결시키지 않는다",
          s.stat["skip_book_stale"] >= 1 and len(s.fills) == 0,
          f"스킵 {int(s.stat['skip_book_stale'])}회 · 체결 {len(s.fills)}건 (기대 0)")


# ─────────────────────────────────────────────────────── 17
def t17_drift_freshness():
    """adverse drift 라벨도 공백을 넘으면 안 된다."""
    track = {"ts": [0, 1000, 2000], "mid": [100.0, 101.0, 102.0], "seg": [0, 0, 0]}
    a = Q.mid_at(track, 1500, max_slip_ms=5000, seg=0)          # 정상
    b = Q.mid_at(track, 900_000, max_slip_ms=5000, seg=0)       # 목표를 크게 초과
    track2 = {"ts": [0, 1000], "mid": [100.0, 101.0], "seg": [0, 1]}
    c = Q.mid_at(track2, 1000, max_slip_ms=5000, seg=0)         # 세그먼트 불일치
    check("17. drift 라벨 freshness · 세그먼트 가드",
          a == 101.0 and b is None and c is None,
          f"정상={a} (기대 101.0) · 초과={b} (기대 None) · 세그먼트불일치={c} (기대 None)")


# ─────────────────────────────────────────────────────── 18
def t18_level_gone_axis():
    """소멸 override 를 **축**으로 둔다. cc=0 이면서 lgc=1 이면 전량 소멸에서만
    순간이동한다. 두 값의 차이가 곧 override 의 기여분이다."""
    ev = [ob(1000, [(100.0, 500.0), (99.0, 300.0)], [(101.0, 500.0)]),
          ob(1200, [(99.0, 300.0)], [(101.0, 500.0)])]      # 100 이 창 안에서 소멸
    strict = Q.Sim(0.0, 10000, 1e9, 10_000, 30, 1e12, 1e12, 0.0)
    lg1    = Q.Sim(0.0, 10000, 1e9, 10_000, 30, 1e12, 1e12, 1.0)
    import tempfile, gzip as gz, json as js
    for sim in (strict, lg1):
        path = write(ev)
        try:
            for ts, m in Q.stream([path], MK):
                (sim.on_book if m["type"] == "orderbook" else sim.on_trade)(ts, m)
        finally:
            os.remove(path)
    a = strict.orders[1].queue_ahead if strict.orders[1] else None
    b = lg1.orders[1].queue_ahead if lg1.orders[1] else None
    check("18. 소멸 override 축이 실제로 갈린다",
          a == 500.0 and b == 0.0,
          f"cc0-strict 앞={a} (기대 500) · cc0+소멸 앞={b} (기대 0)")


def t19_beta_alpha_split():
    """베타(기초재고를 그냥 들고만 있었을 때)와 알파(MM 기여분)가 분리돼야 한다."""
    s = Q.Sim(0.0, 10000, 1e9, 100, 30, 100_000.0, 100_000.0)
    s.bk.bid = {100.0: 10.0}; s.bk.ask = {101.0: 10.0}; s.bk.ts = 1000
    s._init_balances(s.bk.mid())
    s._mark(s.bk.mid())
    s.bk.bid = {200.0: 10.0}; s.bk.ask = {201.0: 10.0}     # 가격 약 2배
    s._mark(s.bk.mid())
    beta = s.stat["market"]
    expect = s.target * (s.bk.mid() - 100.5)
    check("19. 베타 = 기초재고 × mid 이동", abs(beta - expect) < 1e-6,
          f"베타 {beta:,.1f}원 (기대 {expect:,.1f}) · 전략재고 편차 손익 "
          f"{s.stat['inventory']:,.1f} (기대 0)")


# ─────────────────────────────────────────────────────── 20
def t20_beta_gap_immune():
    """베타는 공백에 면역이어야 한다. _mark 적분 베타는 공백에서 끊기므로
    그 구간의 가격 이동이 알파로 샌다."""
    ev = [ob(1_000_000, [(100.0, 10.0)], [(101.0, 10.0)]),
          # 2시간 공백 뒤 가격이 200 으로
          ob(8_200_000, [(200.0, 10.0)], [(201.0, 10.0)])]
    path = write(ev)
    try:
        r = Q.run_one([path], MK, ("t", 0.0, 0.0), 10_000, 1e9, 100, 30,
                      init_cash_krw=100_000.0, init_asset_krw=100_000.0)
    finally:
        os.remove(path)
    # 기초재고 = 100,000 / 100.5 개. mid 100.5 → 200.5
    target = 100_000.0 / 100.5
    expect = target * (200.5 - 100.5)
    ok = (abs(r["baseline_beta_krw"] - expect) < 1.0
          and abs(r["gap_beta_unobserved_krw"] - expect) < 1.0
          and abs(r["mm_alpha_krw"]) < 1.0)
    check("20. 베타가 공백에 면역 (알파로 안 샌다)", ok,
          f"베타 {r['baseline_beta_krw']:,.0f} (기대 {expect:,.0f}) · "
          f"공백베타 {r['gap_beta_unobserved_krw']:,.0f} · "
          f"알파 {r['mm_alpha_krw']:,.2f} (기대 0)")


def t21_daily_sum_identity():
    """일별 합이 전체와 닫혀야 한다. 각 날의 (첫→끝) 을 쓰면 밤사이 이동이
    어느 날에도 안 들어가 합이 어긋난다."""
    ev = []
    t0 = 1_756_000_000_000        # 임의 시작
    for i in range(6):
        ts = t0 + i * 6 * 3600 * 1000          # 6시간 간격 → 날짜가 넘어간다
        px = 100.0 + i * 5
        ev.append(ob(ts, [(px, 10.0)], [(px + 1, 10.0)]))
    path = write(ev)
    try:
        r = Q.run_one([path], MK, ("t", 0.0, 0.0), 10_000, 1e9, 100, 30,
                      init_cash_krw=100_000.0, init_asset_krw=100_000.0)
    finally:
        os.remove(path)
    s_net = sum(r["daily_krw"].values())
    s_beta = sum(r["daily_beta_krw"].values())
    s_alpha = sum(r["daily_alpha_krw"].values())
    ok = (abs(s_net - r["net_krw"]) < 1e-6
          and abs(s_beta - r["baseline_beta_krw"]) < 1e-6
          and abs(s_alpha - r["mm_alpha_krw"]) < 1e-6)
    check("21. 일별 합 = 전체 (net/beta/alpha 셋 다)", ok,
          f"Σnet {s_net:,.2f} vs {r['net_krw']:,.2f} · "
          f"Σbeta {s_beta:,.2f} vs {r['baseline_beta_krw']:,.2f} · "
          f"Σalpha {s_alpha:,.2f} vs {r['mm_alpha_krw']:,.2f} · "
          f"일수 {len(r['daily_krw'])}")


def main():
    print("queue_sim.py 회귀 테스트")
    print("=" * 66)
    for f in (t1_queue_back, t2_fifo, t3_partial, t4_scenario_spread,
              t5_conservative_no_credit, t6_level_gone, t6c_window_exit,
              t6b_requote_to_back,
              t7_stale_fill,
              t7b_no_stale_after_requote, t8_inventory_cap,
              t9_liquidation_taker, t10_no_lookahead,
              t11_pnl_identity, t12_credit_monotone,
              t13_no_naked_short, t13b_no_overspend, t14_fill_time_cap,
              t14b_cap_allows_reducing, t14c_asset_never_negative,
              t15_gap_reset, t16_book_stale_skip, t17_drift_freshness,
              t18_level_gone_axis, t19_beta_alpha_split,
              t20_beta_gap_immune, t21_daily_sum_identity):
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
