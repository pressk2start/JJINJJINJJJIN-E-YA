"""tests.py — scalp.py 오프라인 유닛 테스트 러너.

전부 result-independent: 합성 데이터로 **로직**만 검증하고 성과 수치는 출력하지 않는다.
(성과 수치는 run_forward.py 전용 — 봉인 후 1회.)

사용:
  python3 tests.py          # 오프라인 4스위트 (F/E/G/IO)
  python3 tests.py smoke    # 네트워크 smoke 추가 (API 응답 스키마만 확인)
"""
import os, sys, math, tempfile

sys.path.insert(0, os.path.dirname(__file__) or ".")
import scalp as SC


def _ob(bid_sizes, ask_sizes, bid0=1000.0, tick=1.0):
    """합성 호가창 raw dict. bid0 = 최우선 매수호가, 매도는 bid0+tick 부터."""
    units = []
    for i in range(max(len(bid_sizes), len(ask_sizes))):
        units.append({
            "bid_price": bid0 - i * tick, "bid_size": bid_sizes[i] if i < len(bid_sizes) else 0.0,
            "ask_price": bid0 + tick + i * tick, "ask_size": ask_sizes[i] if i < len(ask_sizes) else 0.0,
        })
    return {"orderbook_units": units}


def _tick(price, vol, side):
    return {"trade_price": price, "trade_volume": vol, "ask_bid": side}


def _frame(ts, obi=0.5, vp=200.0, spread=5.0, n_tick=20, val=1e7, bid=1000.0, ask=1000.5,
           deep=True):
    """합성 프레임. deep=True면 호가가 두꺼워 VWAP == 최우선호가 (비용 테스트 격리용)."""
    sz = 1e6 if deep else 1e-9          # 레벨당 원화 환산: 1e6 * 1000원 = 10억
    bids = [(bid - i, sz) for i in range(5)]
    asks = [(ask + i, sz) for i in range(5)]
    return {"ts": float(ts), "market": "KRW-TEST", "bid": bid, "ask": ask,
            "mid": (bid + ask) / 2.0, "bids": bids, "asks": asks, "obi": obi,
            "micro": (bid + ask) / 2, "spread_bp": spread, "vp": vp,
            "n_tick": n_tick, "tick_value": val, "net_value": val * 0.5}


def _p(label, cond):
    print(f"  [{label}] → {'PASS' if cond else 'FAIL'}")
    return cond


# ============================================================
# F — 특징량 (OBI · microprice · spread · 체결강도)
# ============================================================
def suite_f():
    ok = True
    b, a = SC.ob_levels(_ob([10, 10, 10], [1, 1, 1]))
    ok &= _p("F:obi 매수우위 → +", SC.obi(b, a) > 0.5)
    b2, a2 = SC.ob_levels(_ob([1, 1, 1], [10, 10, 10]))
    ok &= _p("F:obi 매도우위 → -", SC.obi(b2, a2) < -0.5)
    b3, a3 = SC.ob_levels(_ob([5, 5, 5], [5, 5, 5]))
    # 사이즈가 같아도 매도호가가 더 비싸므로(금액 기준) 아주 약한 음수가 정상.
    ok &= _p("F:obi 균형 → ~0", abs(SC.obi(b3, a3)) < 0.005)
    ok &= _p("F:obi 범위 [-1,1]", all(-1.0 <= SC.obi(*SC.ob_levels(_ob(x, y))) <= 1.0
                                     for x, y in [([1e9], [1e-9]), ([1e-9], [1e9]), ([0], [0])]))
    # 가중치: 같은 총량이라도 최우선호가에 실린 쪽이 더 강해야 한다
    front = SC.obi(*SC.ob_levels(_ob([30, 0, 0], [10, 10, 10])))
    back = SC.obi(*SC.ob_levels(_ob([0, 0, 30], [10, 10, 10])))
    ok &= _p("F:obi 최우선호가 가중", front > back)

    mp = SC.microprice(b, a)
    ok &= _p("F:micro ∈ (bid,ask)", b[0][0] <= mp <= a[0][0])
    ok &= _p("F:micro 매수벽 → ask쪽", SC.microprice(b, a) > SC.microprice(b3, a3))
    ok &= _p("F:spread_bp 계산", abs(SC.spread_bp(b, a) - (1.0 / 1000.5 * 1e4)) < 1e-6)
    ok &= _p("F:spread 빈호가 → inf", SC.spread_bp([], []) == float("inf"))

    ok &= _p("F:vp 균형=100", abs(SC.volume_power(
        [_tick(100, 1, "BID"), _tick(100, 1, "ASK")]) - 100.0) < 1e-9)
    ok &= _p("F:vp 매수우위>100", SC.volume_power(
        [_tick(100, 3, "BID"), _tick(100, 1, "ASK")]) > 100.0)
    ok &= _p("F:vp 매도0 클립", SC.volume_power([_tick(100, 1, "BID")]) == 999.0)
    ok &= _p("F:vp 체결없음=100", SC.volume_power([]) == 100.0)
    ok &= _p("F:vp 대금가중(수량X)", SC.volume_power([_tick(1000, 1, "BID"), _tick(1, 100, "ASK")]) > 100.0)

    f = SC.build_frame(1.0, "KRW-TEST", _ob([10, 10], [1, 1]), [_tick(1000, 5, "BID")])
    ok &= _p("F:frame 조립", f["bid"] == 1000.0 and f["ask"] == 1001.0
             and f["obi"] > 0 and f["n_tick"] == 1)
    return "F(특징량)", bool(ok)


# ============================================================
# E — 엔진 (게이트 AND · look-ahead 차단 · 청산 우선순위 · 비용)
# ============================================================
def suite_e():
    ok = True
    cfg = dict(SC.DEFAULT_CFG)
    ok &= _p("E:gate 통과", SC.entry_ok(_frame(0), cfg))
    ok &= _p("E:gate obi 미달 차단", not SC.entry_ok(_frame(0, obi=0.0), cfg))
    ok &= _p("E:gate vp 미달 차단", not SC.entry_ok(_frame(0, vp=100.0), cfg))
    ok &= _p("E:gate 스프레드 초과 차단", not SC.entry_ok(_frame(0, spread=99.0), cfg))
    ok &= _p("E:gate 얇은 틱 차단", not SC.entry_ok(_frame(0, n_tick=1), cfg))
    ok &= _p("E:gate 소액 차단", not SC.entry_ok(_frame(0, val=1.0), cfg))

    # look-ahead 차단: confirm_frames=2 → f0,f1 통과 후 f2에서 체결. 진입가 = f2의 ask.
    c2 = dict(cfg, confirm_frames=2, hold_sec=1e9, tp_bp=1e9, stop_bp=1e9,
              trail_bp=1e9, exit_obi=-1.0, latency_bp=0.0)
    fr = [_frame(0), _frame(1), _frame(2, ask=1234.0, bid=1233.0), _frame(3)]
    tr, _ = SC.simulate(fr, c2)
    ok &= _p("E:진입은 확인 다음 프레임 체결(look-ahead 차단)",
             len(tr) == 1 and abs(tr[0]["entry_px"] - 1234.0) < 1e-9 and tr[0]["entry_ts"] == 2.0)

    # 1프레임만 통과하면 confirm 미달 → 진입 없음
    fr1 = [_frame(0), _frame(1, obi=0.0), _frame(2, obi=0.0), _frame(3, obi=0.0)]
    tr1, _ = SC.simulate(fr1, c2)
    ok &= _p("E:confirm 미달 시 미진입", len(tr1) == 0)

    # 청산 우선순위 — 같은 프레임이 스탑·익절 동시 충족처럼 보이면 스탑 우선(보수)
    pos = {"entry_ts": 0.0, "entry_px": 1000.0, "peak": 1100.0}
    ok &= _p("E:exit stop 최우선",
             SC.exit_reason(_frame(1, bid=900.0), pos, cfg) == "stop")
    ok &= _p("E:exit tp", SC.exit_reason(_frame(1, bid=1010.0), pos, cfg) == "tp")
    ok &= _p("E:exit trail(arm 후)",
             SC.exit_reason(_frame(99, bid=1000.5), pos, cfg) == "trail")
    ok &= _p("E:exit trail arm 전 미발동",
             SC.exit_reason(_frame(1, bid=1000.5), pos, cfg) != "trail")
    ok &= _p("E:exit obi 반전",
             SC.exit_reason(_frame(1, bid=1000.2, obi=-0.9), pos, cfg) == "obi_flip")
    pos2 = {"entry_ts": 0.0, "entry_px": 1000.0, "peak": 1000.0}
    ok &= _p("E:exit 타임캡",
             SC.exit_reason(_frame(cfg["hold_sec"] + 1, bid=1000.1), pos2, cfg) == "timecap")
    ok &= _p("E:exit 미충족 → None",
             SC.exit_reason(_frame(1, bid=1000.1), pos2, cfg) is None)

    # 비용: 가격 무변동 왕복 = 수수료 왕복만큼 손실
    ok &= _p("E:비용 왕복 fee 반영",
             abs(SC.net_bp(1000.0, 1000.0, cfg) - (-2 * cfg["fee_bp"])) < 1e-9)
    # 스프레드 비용: 호가 고정이면 ask 매수 → bid 매도는 항상 마이너스
    flat = [_frame(i, bid=1000.0, ask=1000.5) for i in range(6)]
    tr2, _ = SC.simulate(flat, dict(cfg, confirm_frames=1, hold_sec=2.0, stop_bp=1e9,
                                    tp_bp=1e9, trail_bp=1e9, exit_obi=-1.0))
    ok &= _p("E:호가 고정 구간은 순손실(스프레드+수수료)",
             len(tr2) >= 1 and all(t["net_bp"] < 0 for t in tr2))

    # 정렬 위반은 조용히 건너뛰지 않고 예외
    try:
        SC.simulate([_frame(5), _frame(1)], cfg); bad = False
    except ValueError:
        bad = True
    ok &= _p("E:ts 역순 → ValueError", bad)

    # 잔여 포지션 강제청산
    c3 = dict(cfg, confirm_frames=1, hold_sec=1e9, tp_bp=1e9, stop_bp=1e9,
              trail_bp=1e9, exit_obi=-1.0)
    tr3, _ = SC.simulate([_frame(i) for i in range(5)], c3)
    ok &= _p("E:마지막 잔여포지션 eod 청산",
             len(tr3) == 1 and tr3[0]["reason"] == "eod")
    return "E(엔진)", bool(ok)


# ============================================================
# G — 리스크 가드 (쿨다운 · 일일 손실 중단 · 거래수 상한)
# ============================================================
def suite_g():
    ok = True
    base = dict(SC.DEFAULT_CFG, confirm_frames=1, hold_sec=1.0, stop_bp=1e9,
                tp_bp=1e9, trail_bp=1e9, exit_obi=-1.0)
    fr = [_frame(i * 1.0) for i in range(60)]

    no_cd, _ = SC.simulate(fr, dict(base, cooldown_sec=0.0))
    with_cd, _ = SC.simulate(fr, dict(base, cooldown_sec=20.0))
    ok &= _p("G:쿨다운이 재진입 억제", len(with_cd) < len(no_cd) and len(no_cd) > 1)
    ok &= _p("G:쿨다운 간격 준수",
             all(with_cd[i + 1]["entry_ts"] - with_cd[i]["exit_ts"] >= 20.0
                 for i in range(len(with_cd) - 1)))

    _, s_halt = SC.simulate(fr, dict(base, cooldown_sec=0.0, daily_stop_bp=-20.0))
    ok &= _p("G:일일 손실 한도 도달 시 halt", s_halt.get("halted") is True)
    _, s_cap = SC.simulate(fr, dict(base, cooldown_sec=0.0, max_trades=3, daily_stop_bp=-1e9))
    ok &= _p("G:거래수 상한 halt", s_cap.get("halted") is True and s_cap["n"] == 3)

    s0 = SC.summarize([])
    ok &= _p("G:무거래 요약 안전", s0["n"] == 0)
    s1 = SC.summarize([{"net_bp": 10.0, "reason": "tp", "held_sec": 5.0},
                       {"net_bp": -5.0, "reason": "stop", "held_sec": 3.0}])
    ok &= _p("G:요약 필드 정합",
             s1["n"] == 2 and abs(s1["win_rate"] - 0.5) < 1e-9
             and abs(s1["profit_factor"] - 2.0) < 1e-9 and s1["max_dd_bp"] <= 0)
    return "G(리스크 가드)", bool(ok)


# ============================================================
# IO — 프레임 저장/로드 왕복
# ============================================================
def suite_io():
    ok = True
    frames = [_frame(2), _frame(1)]
    frames[0]["market"] = "KRW-A"; frames[1]["market"] = "KRW-B"
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.jsonl.gz")
        SC.save_frames(p, frames)
        back = SC.load_frames(p)
        ok &= _p("IO:왕복 보존", len(back) == 2)
        ok &= _p("IO:로드 시 ts 정렬", back[0]["ts"] <= back[1]["ts"])
        ok &= _p("IO:마켓 필터", len(SC.load_frames(p, market="KRW-A")) == 1)
        sp = SC.split_by_market(back)
        ok &= _p("IO:마켓 분할", set(sp) == {"KRW-A", "KRW-B"})
    return "IO(저장/로드)", bool(ok)


# ============================================================
# smoke — 네트워크. 스키마만 확인, 성과 계산 없음.
# ============================================================
def suite_smoke():
    ok = True
    mk = SC.markets_krw()
    ok &= _p("S:KRW 마켓 조회", len(mk) > 10)
    m = "KRW-BTC" if "KRW-BTC" in mk else (mk[0] if mk else None)
    if not m:
        return "S(smoke)", False
    obs = SC.orderbook([m])
    ok &= _p("S:orderbook 스키마", m in obs and len(obs[m].get("orderbook_units", [])) >= 5)
    tk = SC.trades_ticks(m, count=50)
    ok &= _p("S:trades 스키마",
             len(tk) > 0 and {"trade_price", "trade_volume", "ask_bid"} <= set(tk[0]))
    f = SC.build_frame(0.0, m, obs[m], tk)
    ok &= _p("S:실데이터 프레임 정상",
             f["ask"] > f["bid"] > 0 and -1 <= f["obi"] <= 1 and f["spread_bp"] >= 0)
    return "S(smoke)", bool(ok)


def main():
    smoke = "smoke" in sys.argv
    suites = [suite_f, suite_e, suite_g, suite_io] + ([suite_smoke] if smoke else [])
    results = []
    for s in suites:
        name = s.__name__
        print(f"\n=== {name} ===")
        try:
            results.append(s())
        except Exception as e:
            print(f"  EXCEPTION: {type(e).__name__}: {e}")
            results.append((name, False))
    print("\n" + "=" * 46)
    allok = True
    for name, ok in results:
        allok &= ok
        print(f"  {name:22} {'PASS' if ok else 'FAIL'}")
    print("=" * 46)
    print("ALL PASS" if allok else "FAIL 있음")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
