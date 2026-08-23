# -*- coding: utf-8 -*-
"""tests_ws.py — ws_features 재생 엔진 합성 테스트 (네트워크 없음, 성과 수치 없음).

검증 대상 3가지 (전부 실제로 발생했던 버그 유형):
  1. 미래 이벤트가 과거 frame에 새어들지 않는가          → look-ahead 차단
  2. 수신 순서가 뒤집혀도 exchange-time 결과가 같은가     → primary clock 정합
  3. 호가 가격이 바뀔 때 depletion↔trade 매칭이 섞이는가  → 가격별 매칭

사용: python3 tests_ws.py
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ws_features as WF


def ob(ts_ms, seq, bid_p, bid_s, ask_p, ask_s, code="KRW-T"):
    return {"type": "orderbook", "code": code, "timestamp": ts_ms, "_seq": seq,
            "recv_ts": ts_ms + 90,
            "orderbook_units": [{"bid_price": bid_p, "bid_size": bid_s,
                                 "ask_price": ask_p, "ask_size": ask_s}]}


def tr(ts_ms, seq, price, vol, side, code="KRW-T"):
    return {"type": "trade", "code": code, "timestamp": ts_ms + 40,
            "trade_timestamp": ts_ms, "_seq": seq,
            "recv_ts": ts_ms + 90, "trade_price": price,
            "trade_volume": vol, "ask_bid": side}


def p(label, cond):
    print(f"  [{label}] → {'PASS' if cond else 'FAIL'}")
    return cond


# ============================================================
# 1. look-ahead 차단
#   t=0 에 매수우위 호가, t=2 에 매도우위로 급반전.
#   t=1 frame 은 **t=0 상태만** 반영해야 한다 (t=2 정보가 새어들면 실패).
# ============================================================
def test_lookahead():
    ok = True
    T0 = 1_700_000_000_000
    evs = [
        ob(T0 + 0,    1, 100.0, 100.0, 101.0, 1.0),     # 강한 매수우위
        tr(T0 + 1500, 2, 100.0, 1.0, "ASK"),
        ob(T0 + 2000, 3, 100.0, 1.0, 101.0, 100.0),     # 강한 매도우위로 반전
        ob(T0 + 3000, 4, 100.0, 1.0, 101.0, 100.0),
    ]
    rows = WF.replay(evs, grid=1.0, lateness_ms=0)
    f1 = [r for r in rows if abs(r["ts"] - (T0 + 1000) / 1000.0) < 1e-6]
    ok &= p("t=1 frame 존재", len(f1) == 1)
    if f1:
        ok &= p("t=1 frame 이 t=0 상태(매수우위) 반영", f1[0]["obi1"] > 0.9)
        ok &= p("t=2 정보(매도우위) 미유입", f1[0]["obi1"] > 0)
    f2 = [r for r in rows if abs(r["ts"] - (T0 + 2000) / 1000.0) < 1e-6]
    if f2:
        ok &= p("t=2 frame 은 t=2 이벤트 포함(경계 event_ts==g)", f2[0]["obi1"] < 0)
    # trade 도 마찬가지: t=1 frame 에는 t=1.5 체결이 없어야 한다
    if f1:
        ok &= p("t=1 frame 에 t=1.5 체결 미포함", f1[0]["n_trade"] == 0)
    return "1. look-ahead 차단", ok


# ============================================================
# 2. 수신 순서 뒤집힘 내성
#   유한 버퍼 재생은 **lateness_ms 이내의 역순만** 흡수할 수 있다 (그게 워터마크의 정의).
#   무제한 셔플에도 동일하길 요구하는 건 유한버퍼 설계에서 불가능한 요구다.
#   따라서 검증할 성질은 둘이다:
#     (a) 허용 범위 내 역순 → 시간순과 **결과 동일**
#     (b) 허용 초과 역순    → **조용히 반영하지 않고 검출·폐기 카운트**
# ============================================================
def test_receive_order():
    ok = True
    T0 = 1_700_000_000_000
    evs = []; seq = 0
    for i in range(30):
        seq += 1
        evs.append(ob(T0 + i * 200, seq, 100.0, 10.0 + i, 101.0, 5.0))
        if i % 3 == 0:
            seq += 1
            evs.append(tr(T0 + i * 200 + 50, seq, 100.0, 0.5, "ASK" if i % 2 else "BID"))
    base = WF.replay(list(evs), grid=1.0, lateness_ms=3000)
    ok &= p("기준 재생 지각 폐기 0", not WF.LAST_QC.get("late_dropped"))

    rnd = random.Random(42)
    for trial in range(5):
        sh = list(evs)
        for i in range(0, len(sh), 6):              # 6개 창(≈1.2초) 안에서만 셔플
            blk = sh[i:i + 6]; rnd.shuffle(blk); sh[i:i + 6] = blk
        got = WF.replay(sh, grid=1.0, lateness_ms=3000)
        same = (len(got) == len(base)
                and all(abs(a["ts"] - b["ts"]) < 1e-9
                        and abs(a["obi1"] - b["obi1"]) < 1e-12
                        and a["n_trade"] == b["n_trade"]
                        for a, b in zip(base, got)))
        ok &= p(f"허용범위 내 역순 #{trial+1} 결과 동일",
                same and not WF.LAST_QC.get("late_dropped"))

    sh = list(evs); rnd.shuffle(sh)
    WF.replay(sh, grid=1.0, lateness_ms=0)
    ok &= p("허용 초과 역순 → 지각 폐기로 검출됨", bool(WF.LAST_QC.get("late_dropped")))
    # _rx 는 일부러 어긋나게 넣어도 무시돼야 한다
    ev2 = [dict(e) for e in evs]
    for e in ev2:
        e["recv_ts"] = T0 + 999999 - e["_seq"]      # 수신 시각을 완전히 역순으로
    got2 = WF.replay(ev2, grid=1.0, lateness_ms=3000)
    ok &= p("recv_ts 역순이어도 결과 불변", len(got2) == len(base)
            and all(abs(a["obi1"] - b["obi1"]) < 1e-12 for a, b in zip(base, got2)))
    return "2. exchange-time primary clock", ok


# ============================================================
# 3. 가격별 depletion ↔ trade 매칭
#   (a) 같은 가격의 감소는 같은 가격 체결로 설명돼야 한다 → unexplained 0
#   (b) 다른 가격 체결로는 설명되면 안 된다 → unexplained 1
#   (c) 가격이 이동해도 이전 가격의 감소가 새 가격 체결과 섞이면 안 된다
# ============================================================
def test_price_matching():
    ok = True
    T0 = 1_700_000_000_000

    # (a) bid 100 이 10→4 (감소 6), 같은 가격에서 매도체결 6 → 전부 설명됨
    a = WF.replay([
        ob(T0 + 0,   1, 100.0, 10.0, 101.0, 5.0),
        tr(T0 + 100, 2, 100.0, 6.0, "ASK"),
        ob(T0 + 200, 3, 100.0, 4.0, 101.0, 5.0),
        ob(T0 + 1500, 4, 100.0, 4.0, 101.0, 5.0),
    ], grid=1.0, lateness_ms=0)
    f = [r for r in a if r["depl_qty_bid"] > 0]
    ok &= p("(a) 동일가격 체결로 감소 설명 → ratio 0",
            bool(f) and abs(f[0]["unexplained_depl_ratio_bid"]) < 1e-9)

    # (b) 감소는 100 에서, 체결은 99 에서 → 설명 안 됨
    b = WF.replay([
        ob(T0 + 0,   1, 100.0, 10.0, 101.0, 5.0),
        tr(T0 + 100, 2, 99.0, 6.0, "ASK"),
        ob(T0 + 200, 3, 100.0, 4.0, 101.0, 5.0),
        ob(T0 + 1500, 4, 100.0, 4.0, 101.0, 5.0),
    ], grid=1.0, lateness_ms=0)
    f = [r for r in b if r["depl_qty_bid"] > 0]
    ok &= p("(b) 다른가격 체결로는 설명 안 됨 → ratio 1",
            bool(f) and abs(f[0]["unexplained_depl_ratio_bid"] - 1.0) < 1e-9)

    # (c) 호가가 100 → 99 → 100 으로 이동. 100에서 6 감소(체결 없음),
    #     99에서 6 감소(99 체결 6). 섞이면 ratio 0.5 가 아니라 0 이 된다.
    c = WF.replay([
        ob(T0 + 0,   1, 100.0, 10.0, 101.0, 5.0),
        ob(T0 + 100, 2, 99.0,  10.0, 101.0, 5.0),   # 100 호가 소멸 = 10 감소
        tr(T0 + 150, 3, 99.0, 6.0, "ASK"),
        ob(T0 + 200, 4, 99.0,  4.0,  101.0, 5.0),   # 99 에서 6 감소 (체결로 설명)
        ob(T0 + 1500, 5, 99.0, 4.0,  101.0, 5.0),
    ], grid=1.0, lateness_ms=0)
    f = [r for r in c if r["depl_qty_bid"] > 0]
    # 총 감소 16 (100에서 10 + 99에서 6), 설명된 것 6 → unexplained 10/16
    ok &= p("(c) 가격 이동 시 교차오염 없음 → 10/16",
            bool(f) and abs(f[0]["unexplained_depl_ratio_bid"] - 10.0 / 16.0) < 1e-9)
    if f:
        print(f"     (참고) depl={f[0]['depl_qty_bid']:.1f} "
              f"ratio={f[0]['unexplained_depl_ratio_bid']:.4f}")
    return "3. 가격별 depletion 매칭", ok


# ============================================================
# 4. 보조: 라벨은 mid 기준이고 커버리지 부족 시 None
# ============================================================
def test_labels():
    ok = True
    T0 = 1_700_000_000_000
    evs = [ob(T0 + i * 500, i + 1, 100.0 + i * 0.1, 10.0, 101.0 + i * 0.1, 10.0)
           for i in range(12)]
    rows = WF.add_labels(WF.replay(evs, grid=1.0, lateness_ms=0), [2.0])
    ok &= p("라벨 계산됨", any(r.get("fwd_2_bp") is not None for r in rows))
    ok &= p("커버리지 부족 구간은 None (0 충전 아님)",
            rows[-1].get("fwd_2_bp") is None)
    r0 = rows[0]
    ok &= p("라벨 기준가는 mid", "mid" in r0 and r0["mid"] > 0)
    return "4. 라벨 규약", ok


# ============================================================
# 5. 회귀: _seq 없는(전부 동률) 이벤트에서도 죽지 않아야 한다
#   실제 수집 데이터(레코더가 _seq 를 넣기 전 버전)에서 힙이 dict 를 비교하려다
#   TypeError 로 죽었다. 합성 테스트는 항상 _seq 를 줘서 못 잡았던 케이스.
# ============================================================
def test_tie_break():
    ok = True
    T0 = 1_700_000_000_000
    evs = []
    for i in range(6):
        e = ob(T0 + 1000, 0, 100.0, 10.0 + i, 101.0, 5.0)   # 전부 같은 ts, _seq=0
        e.pop("_seq")                                        # _seq 자체가 없는 경우
        evs.append(e)
    evs.append(ob(T0 + 3000, 0, 100.0, 1.0, 101.0, 5.0))
    try:
        rows = WF.replay(evs, grid=1.0, lateness_ms=0)
        ok &= p("동률 이벤트에서 예외 없음", True)
        ok &= p("프레임 생성됨", len(rows) >= 1)
    except Exception as e:
        ok &= p(f"동률 이벤트에서 예외 없음 ({type(e).__name__})", False)
    return "5. 동률 tie-break 회귀", ok


# ============================================================
# 6. 관측 창 밖으로 밀려난 레벨을 감소로 세면 안 된다
#   스냅샷은 상위 N단만 담는다. 호가가 올라가면 최하위 레벨이 창 밖으로 나가는데
#   그건 잔량 소멸이 아니라 관측 불가다. 세면 unexplained_ratio 가 1로 편향된다.
# ============================================================
def ob2(ts_ms, seq, bids, asks, code="KRW-T"):
    """다단 호가 스냅샷. bids/asks = [(price,size),...]"""
    n = max(len(bids), len(asks))
    units = []
    for i in range(n):
        bp, bs = bids[i] if i < len(bids) else (0.0, 0.0)
        ap, asz = asks[i] if i < len(asks) else (0.0, 0.0)
        units.append({"bid_price": bp, "bid_size": bs, "ask_price": ap, "ask_size": asz})
    return {"type": "orderbook", "code": code, "timestamp": ts_ms, "_seq": seq,
            "recv_ts": ts_ms + 90, "orderbook_units": units}


def test_window_exit():
    ok = True
    T0 = 1_700_000_000_000
    # 2단만 보이는 창. 매수호가가 100/99 → 101/100 으로 올라가면서 99가 창 밖으로 나간다.
    # 99의 잔량 50은 사라진 게 아니라 안 보이는 것이므로 감소로 세면 안 된다.
    evs = [
        ob2(T0 + 0,    1, [(100.0, 10.0), (99.0, 50.0)], [(101.0, 5.0), (102.0, 5.0)]),
        ob2(T0 + 200,  2, [(101.0, 8.0), (100.0, 10.0)], [(102.0, 5.0), (103.0, 5.0)]),
        ob2(T0 + 1500, 3, [(101.0, 8.0), (100.0, 10.0)], [(102.0, 5.0), (103.0, 5.0)]),
    ]
    rows = WF.replay(evs, grid=1.0, lateness_ms=0)
    f = [r for r in rows if r["depl_qty_bid"] > 0]
    ok &= p("창 밖 레벨(99, 50)을 감소로 세지 않음", not f)
    if f:
        print(f"     (실패 상세) depl_qty_bid={f[0]['depl_qty_bid']}")

    # 대조: 창 안에 남아 있는 가격의 실제 감소는 정상적으로 잡혀야 한다
    evs2 = [
        ob2(T0 + 0,    1, [(100.0, 10.0), (99.0, 50.0)], [(101.0, 5.0), (102.0, 5.0)]),
        ob2(T0 + 200,  2, [(100.0, 4.0),  (99.0, 50.0)], [(101.0, 5.0), (102.0, 5.0)]),
        ob2(T0 + 1500, 3, [(100.0, 4.0),  (99.0, 50.0)], [(101.0, 5.0), (102.0, 5.0)]),
    ]
    rows2 = WF.replay(evs2, grid=1.0, lateness_ms=0)
    f2 = [r for r in rows2 if r["depl_qty_bid"] > 0]
    ok &= p("창 안 실제 감소(100: 10→4)는 정상 계상", bool(f2) and abs(f2[0]["depl_qty_bid"] - 6.0) < 1e-9)
    return "6. 관측 창 경계 처리", ok


def main():
    suites = [test_lookahead, test_receive_order, test_price_matching,
              test_labels, test_tie_break, test_window_exit]
    res = []
    for s in suites:
        print(f"\n=== {s.__name__} ===")
        try:
            res.append(s())
        except Exception as e:
            import traceback; traceback.print_exc()
            res.append((s.__name__, False))
    print("\n" + "=" * 50)
    allok = True
    for name, ok in res:
        allok &= ok
        print(f"  {name:34} {'PASS' if ok else 'FAIL'}")
    print("=" * 50)
    print("ALL PASS" if allok else "FAIL 있음")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
