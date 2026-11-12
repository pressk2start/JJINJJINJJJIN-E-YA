#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""discover — '사건이 일어난 사례들 안에서 어떤 조건일 때만 되는가' 를 달마다 자동으로 묻고, 넘은 것만 후보로 등록한다.
(공개 시세만, 키·주문 없음, 표준 라이브러리. GitHub Actions 월 1회 실행용 — 거래 서버에서 돌리지 않는다.)

왜 (사용자 원칙, 2026-11-10): "급등하면 버는가" 가 아니라 "급등한 수많은 사례 중 어떤 조건일 때만 이어지는가" 를 물어야 한다.
    지금까지는 사람이 한 번씩 손으로 돌렸다(H26 일·주, H27 봇 기록, 고문의 15분봉 탐색). 이 파일은 같은 질문을 정해진 틀 안에서
    달마다 다시 묻는다. 찾은 것을 믿을지는 여기서 정하지 않는다 — 등록일 이후의 새 자료로 forward_watch 가 판정한다.

*** 틀은 초안이다 (2026-11-12). 고문 검토 전에는 등록하지 않는다: FRAME_APPROVED 가 False 인 동안 --register 는 '등록했을 것' 만
    보고하고 장부에 쓰지 않는다. 틀을 고치면 ENGINE 번호를 올린다(옛 번호로 등록된 후보는 채점을 멈춘다). ***

틀
  자료    업비트 60분봉, 최근 3년. 앞 60% 기간 = TRAIN, 뒤 40% = HOLDOUT (날짜 경계).
          대상은 실행 시점에 '상장 2년 이상' 인 종목 중 직전 200일 거래대금이 큰 30종목(스테이블코인 제외, BTC 는 항상 포함).
          등록되는 후보에는 그때의 종목 목록이 고정된다.
  사건    봉 하나가 끝난 시점에 판단한다 (봉 h 의 종가까지만 사용).
          SURGE     1시간 수익률 ≥ +3%
          CRASH     1시간 수익률 ≤ −3%
          BREAKOUT  종가가 직전 24봉의 고가를 넘고, 그 봉의 거래대금이 직전 24봉 평균의 3배 이상
  거래    다음 봉 시가에 사서 1·4·24시간 뒤 봉의 시가에 판다. 모집단 = 사건 × 보유 시간 (9개).
          한 종목·한 모집단에서 동시에 1건. 진입·청산 시각의 봉이 없으면(거래소 점검 등) 거래로 세지 않는다.
          비용 편도 BTC·ETH 0.07%, 그 밖 0.10% (고문의 급락 반등과 같은 가정).
  조건    모집단마다 아래 지표 하나를 TRAIN 3분위의 아래 1/3·위 1/3 로 나눠 붙인다 (지표 1개짜리만). 조건 없는 기본형도 후보다.
          시장 상태  btc_ret_1h · btc_ret_24h · btc_trend(BTC 종가/50일 평균−1) · mkt_ret_1h(다른 종목 1시간 수익률 중앙값)
                     · breadth_24h(24시간 수익률이 양수인 종목 비율) · session(KST 0~8시=0, 8~16시=1, 16~24시=2) · weekend
          종목       size(사건 봉의 |1시간 수익률|) · rel_24h(종목 24시간 수익률 − BTC) · vol_ratio(거래대금/직전 24봉 평균)
                     · compress(최근 24봉 평균 변동/최근 168봉 평균 변동) · dist_7d(종가/7일 평균−1)
          갈림선이 실제로 나온 적이 있는 쪽이 시장 상태라(급락 + BTC 동반 하락) 그쪽을 더 많이 넣었다.
  선택    TRAIN: 거래 ≥ 80건·≥ 30일, 평균 순수익 > 0, 일 단위 군집 t ≥ 2.5 인 것 중 t 가 가장 큰 1개(모집단마다).
          HOLDOUT 재현: 그 1개가 뒤 구간에서 거래 ≥ 40건·≥ 15일, 평균 순수익 > 0, t ≥ 2.0. 둘 다 넘어야 '발견'.
          한 달에 훑는 조합이 약 220개다. TRAIN 기준만 쓰면 우연으로 1~2개가 넘는다 — HOLDOUT 재현까지 넘을 우연은 달에 0.03개쯤.
  등록    발견한 것을 HOLDOUT t 순으로 달에 최대 3개. 이미 추적 중인 것과 같은 (사건, 보유, 지표, 방향)은 다시 등록하지 않는다.
          등록 내용(사건 정의·문턱·비용·종목)은 장부(forward/registry.json)에 고정된다.
  판정    forward_watch 가 등록일 이후 거래로만 채점한다. 30건·10일이 처음 채워지는 거래까지로 한 번만: 평균 순수익 > 0 이고
          날짜 가중 평균 > 0 이면 PASS, 아니면 FAIL (급락 반등과 같은 기준). 180일 안에 못 채우면 EXPIRED.
          PASS 는 '후보' 다 — 봉 기준이고, 실제 호가 체결은 확인하지 않은 값이다.
  표본    처음엔 4%·큰 종목 12개로 잡았는데, 실제 시세로 돌려 보니(5종목 240일) 급등·급락이 13건씩뿐이라 조건을 가릴 수 없었다.
          그래서 결과를 보기 전 틀 단계에서 3%·상위 30종목으로 넓혔다 — 수익이 아니라 사건 수를 보고 정한 것이다.
          '직전 30일 거래대금 상위' 로 뽑아 보니 최근에 급등해 거래가 몰린 신규 상장 코인이 대부분이었다(최근에 올랐기 때문에
          뽑힌 종목으로 과거를 훑으면 급등이 좋아 보인다). 그래서 상장 2년 이상·200일 거래대금으로 바꿨다.
  한계    탐색에 쓰는 거래는 '기본 모집단에서 조건을 통과한 거래' 다(조건 없는 거래가 뒤 신호를 막을 수 있다) — 등록 후 채점도 같은
          방식이라 서로 일치한다. 60분봉이라 봉 안에서 일어난 급등·급락의 정확한 시점은 보지 못한다(정각에 끊은 한 시간만 본다).
          대상 30종목은 '지금까지 살아남아 거래가 많은' 종목이라 과거 구간에는 생존 편향이 남는다 — 등록 뒤의 전진 채점에는 없다.
          초·분 단위와 호가는 다루지 않는다.

실행
  python3 scalp/research/discover.py                       탐색만 하고 보고 (등록 안 함)
  python3 scalp/research/discover.py --register            발견한 것을 등록 (FRAME_APPROVED 가 True 일 때만 실제로 쓴다)
  python3 scalp/research/discover.py --markets KRW-BTC,KRW-ETH --days 120     작은 범위로 점검 (등록·기록 안 함)
"""
import argparse
import datetime as dt
import importlib.util
import json
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(HERE / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AM = _load("auto_monitor")          # 조회 함수(http_get)만 빌려 쓴다
SS = _load("shadow_salvage")        # 군집 통계·3분위 조건 만들기 (H27 에서 쓴 것 그대로)

ENGINE = "1"
FRAME_APPROVED = False              # 고문이 틀을 확인하면 True 로. 그 전에는 등록하지 않는다
UTC = dt.timezone.utc
UNIVERSE_N, LIQ_DAYS, MIN_LISTED_DAYS = 30, 200, 730
STABLE = ("KRW-USDT", "KRW-USDC", "KRW-USD1", "KRW-USDE", "KRW-DAI", "KRW-TUSD")
HISTORY_DAYS, TRAIN_FRAC = 1095, 0.60
MOVE, VOL_X, HOLDS = 0.03, 3.0, (1, 4, 24)
COST, COST_OTHER = {"KRW-BTC": 0.0007, "KRW-ETH": 0.0007}, 0.0010
TRAIN_MIN_N, TRAIN_MIN_DAYS, TRAIN_MIN_T = 80, 30, 2.5
HOLD_MIN_N, HOLD_MIN_DAYS, HOLD_MIN_T = 40, 15, 2.0
MAX_NEW = 3
FWD_MIN_N, FWD_MIN_DAYS, FWD_EXPIRE_DAYS = 30, 10, 180
WARMUP_H = 55 * 24                  # 지표 계산에 필요한 앞쪽 자료 (50일 평균)
WORKERS = 6                         # 동시에 받는 종목 수. 전체 호출 간격은 auto_monitor.http_get 이 초당 8회 아래로 묶는다
OUT_DIR = HERE / "forward"


# ---------------------------------------------------------------------------------------------------------------------
def to_hour(t):
    return int(t.timestamp() // 3600)


def day_hour(day):
    """'YYYY-MM-DD' 00:00 UTC 의 시(epoch hour)."""
    return to_hour(dt.datetime.fromisoformat(day).replace(tzinfo=UTC))


def hour_day(h):
    return dt.datetime.fromtimestamp(h * 3600, tz=UTC).strftime("%Y-%m-%d")


def fetch_hours(market, start_h, end_h, get=None):
    """[start_h, end_h) 의 60분봉 → {시: (시가, 고가, 저가, 종가, 거래대금)}. end_h 는 포함하지 않으므로 진행 중인 봉은 들어오지 않는다."""
    get = get or AM.http_get
    out, to = {}, end_h
    while True:
        stamp = dt.datetime.fromtimestamp(to * 3600, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = get("%s/v1/candles/minutes/60?market=%s&count=200&to=%s" % (AM.UPBIT, market, urllib.parse.quote(stamp)))
        if not rows:
            break
        hours = []
        for r in rows:
            h = to_hour(AM.parse_utc(r["candle_date_time_utc"]))
            hours.append(h)
            out[h] = (float(r["opening_price"]), float(r["high_price"]), float(r["low_price"]), float(r["trade_price"]),
                      float(r["candle_acc_trade_price"]))
        oldest = min(hours)
        if oldest <= start_h or len(rows) < 200 or oldest >= to:
            break
        to = oldest
    return {h: v for h, v in out.items() if start_h <= h < end_h}


def pick_universe(today, n=UNIVERSE_N, list_markets=None, daily_value=None, listed_before=None):
    """상장 MIN_LISTED_DAYS 일 이상인 종목 중 직전 LIQ_DAYS 일(오늘 제외) 거래대금 합이 큰 n 종목.
    스테이블코인은 빼고 BTC 는 항상 넣는다."""
    get = AM.http_get
    list_markets = list_markets or (lambda: [m["market"] for m in get(AM.UPBIT + "/v1/market/all") if m["market"].startswith("KRW-")])
    old_day = (dt.date.fromisoformat(today) - dt.timedelta(days=MIN_LISTED_DAYS)).isoformat()

    def default_value(m):
        rows = get("%s/v1/candles/days?market=%s&count=%d" % (AM.UPBIT, m, LIQ_DAYS))
        return sum(float(r["candle_acc_trade_price"]) for r in rows if r["candle_date_time_utc"][:10] < today)

    def default_listed(m):                                           # 그 날짜 이전의 일봉이 하나라도 있으면 그때 이미 상장돼 있었다
        return bool(get("%s/v1/candles/days?market=%s&count=1&to=%s" % (AM.UPBIT, m, urllib.parse.quote(old_day + "T00:00:00Z"))))

    daily_value, listed_before = daily_value or default_value, listed_before or default_listed
    names = [m for m in list_markets() if m not in STABLE]
    with ThreadPoolExecutor(WORKERS) as pool:
        scored = sorted(zip(pool.map(daily_value, names), names), reverse=True)
    top = []
    for _, m in scored:                                              # 거래대금 순으로 내려가며 상장 기간을 확인한다
        if len(top) >= n:
            break
        if listed_before(m):
            top.append(m)
    if "KRW-BTC" not in top:
        top = ["KRW-BTC"] + top[:n - 1]
    return tuple(sorted(top))


def load_panel(start_h, end_h, markets, fetch=None, log=None):
    """{종목: {시: 봉}}. 한 종목이라도 못 받으면 예외 — 빠진 종목이 시장 지표를 바꾸는 것을 막는다."""
    fetch = fetch or fetch_hours
    with ThreadPoolExecutor(WORKERS) as pool:                       # 하나라도 실패하면 여기서 예외가 올라온다
        panel = dict(zip(markets, pool.map(lambda m: fetch(m, start_h, end_h), markets)))
    if log:
        log("  받은 봉: " + ", ".join("%s %d" % (m[4:], len(panel[m])) for m in markets))
    return panel


# ---------------------------------------------------------------------------------------------------------------------
def bar_events(bars, h, move=MOVE, vol_x=VOL_X):
    """봉 h 가 끝났을 때 일어난 사건 → {이름: 그 봉의 1시간 수익률}. 직전 봉이 없으면 판단하지 않는다."""
    cur, prev = bars.get(h), bars.get(h - 1)
    if not cur or not prev or prev[3] <= 0:
        return {}
    ret, out = cur[3] / prev[3] - 1, {}
    if ret >= move:
        out["SURGE"] = ret
    if ret <= -move:
        out["CRASH"] = ret
    past = [bars.get(h - k) for k in range(1, 25)]
    if all(past):
        avg = sum(p[4] for p in past) / 24.0
        if cur[3] > max(p[1] for p in past) and avg > 0 and cur[4] >= vol_x * avg:
            out["BREAKOUT"] = ret
    return out


def _ret(series, h, k):
    a, z = series.get(h), series.get(h - k)
    return a[3] / z[3] - 1 if a and z and z[3] > 0 else None


def indicators(panel, market, h):
    """봉 h 가 끝난 시점에 알 수 있는 값만으로 만든 지표. 계산할 수 없는 항목은 넣지 않는다."""
    bars, btc, ind = panel[market], panel.get("KRW-BTC", {}), {}

    def put(name, value):
        if value is not None:
            ind[name] = float(value)

    own1, own24, btc24 = _ret(bars, h, 1), _ret(bars, h, 24), _ret(btc, h, 24)
    put("size", abs(own1) if own1 is not None else None)
    put("btc_ret_1h", _ret(btc, h, 1))
    put("btc_ret_24h", btc24)
    if h in btc:
        closes = [btc[t][3] for t in range(h - 1199, h + 1) if t in btc]
        if len(closes) >= 1100:
            put("btc_trend", btc[h][3] / mean(closes) - 1)
    others = [r for r in (_ret(panel[m], h, 1) for m in panel if m != market) if r is not None]
    if len(others) >= max(2, (len(panel) - 1) // 2):
        put("mkt_ret_1h", median(others))
    day_rets = [r for r in (_ret(panel[m], h, 24) for m in panel) if r is not None]
    if len(day_rets) >= max(2, len(panel) // 2):
        put("breadth_24h", sum(1 for r in day_rets if r > 0) / float(len(day_rets)))
    if own24 is not None and btc24 is not None:
        put("rel_24h", own24 - btc24)
    vols = [bars[t][4] for t in range(h - 24, h) if t in bars]
    if len(vols) >= 20 and mean(vols) > 0:
        put("vol_ratio", bars[h][4] / mean(vols))

    def moves(n):
        return [abs(bars[t][3] / bars[t - 1][3] - 1) for t in range(h - n + 1, h + 1) if t in bars and t - 1 in bars and bars[t - 1][3] > 0]

    short, long_ = moves(24), moves(168)
    if len(short) >= 20 and len(long_) >= 140 and mean(long_) > 0:
        put("compress", mean(short) / mean(long_))
    week = [bars[t][3] for t in range(h - 167, h + 1) if t in bars]
    if len(week) >= 140:
        put("dist_7d", bars[h][3] / mean(week) - 1)
    entry_kst = dt.datetime.fromtimestamp((h + 1) * 3600, tz=UTC) + dt.timedelta(hours=9)
    put("session", entry_kst.hour // 8)
    put("weekend", 1.0 if entry_kst.weekday() >= 5 else 0.0)
    return ind


def build_trades(panel, start_h, end_h, move=MOVE, vol_x=VOL_X, cost=None, cost_other=COST_OTHER, only=None):
    """사건 봉이 [start_h, end_h) 인 모든 모집단의 끝난 거래 → [{pop, market, entry_h, exit_h, day, gross, net, ind}].
    only=(사건, 보유) 를 주면 그 모집단만. 한 종목·한 모집단에서 동시에 1건(앞 거래의 청산 봉까지 새 신호를 받지 않는다)."""
    cost = COST if cost is None else cost
    trades = []
    for market in sorted(panel):
        bars, busy, c = panel[market], {}, cost.get(market, cost_other)
        for h in range(start_h, end_h):
            if h not in bars:
                continue
            events = bar_events(bars, h, move, vol_x)
            if not events or h + 1 not in bars:
                continue
            ind = None
            for name in sorted(events):
                for hold in HOLDS:
                    if only and (name, hold) != tuple(only):
                        continue
                    pop = "%s/%d" % (name, hold)
                    if h <= busy.get(pop, -1) or h + 1 + hold not in bars:
                        continue
                    if ind is None:
                        ind = indicators(panel, market, h)
                    gross = bars[h + 1 + hold][0] / bars[h + 1][0] - 1
                    trades.append({"pop": pop, "market": market, "entry_h": h + 1, "exit_h": h + 1 + hold, "day": hour_day(h + 1),
                                   "gross": gross, "net": (1 + gross) * (1 - c) / (1 + c) - 1, "move": events[name], "ind": ind})
                    busy[pop] = h + hold
    trades.sort(key=lambda t: (t["entry_h"], t["market"], t["pop"]))
    return trades


# ---------------------------------------------------------------------------------------------------------------------
def _ok(st, n, days, t):
    return bool(st["n"] >= n and st["days"] >= days and st["net"] is not None and st["net"] > 0 and st["t"] is not None and st["t"] >= t)


def _slim(st):
    return {k: st[k] for k in ("n", "days", "net", "t", "daymean")}


def _pct(v):
    return "n/a" if v is None else "%+.3f%%" % (100 * v)


def _tt(v):
    return "n/a" if v is None else "%+.2f" % v


def search(trades, cut_day, log=print):
    """모집단마다 TRAIN 에서 조건 1개를 고르고 HOLDOUT 에서 재현되는지 본다 → (발견 목록, 훑은 조합 수)."""
    found, tests = [], 0
    for pop in sorted({t["pop"] for t in trades}, key=lambda p: (p.split("/")[0], int(p.split("/")[1]))):
        train = [t for t in trades if t["pop"] == pop and t["day"] < cut_day]
        hold = [t for t in trades if t["pop"] == pop and t["day"] >= cut_day]
        bt, bh = SS.cluster_stats(train), SS.cluster_stats(hold)
        log("== %s | TRAIN n=%d days=%d net %s t %s | HOLDOUT n=%d net %s t %s" % (
            pop, bt["n"], bt["days"], _pct(bt["net"]), _tt(bt["t"]), bh["n"], _pct(bh["net"]), _tt(bh["t"])))
        options = [(None, bt)] + [(c, SS.cluster_stats([t for t in train if SS.passes(t, c)])) for c in SS.make_conditions(train)]
        tests += len(options)
        qualified = [(c, st) for c, st in options if _ok(st, TRAIN_MIN_N, TRAIN_MIN_DAYS, TRAIN_MIN_T)]
        best = max(options, key=lambda x: x[1]["t"] if x[1]["t"] is not None and x[1]["n"] >= TRAIN_MIN_N else -99.0)
        label = "조건 없음" if best[0] is None else "%s %s %.4g" % (best[0][1], best[0][2], best[0][3])
        log("   TRAIN 최상: %s  n=%d net %s t %s" % (label, best[1]["n"], _pct(best[1]["net"]), _tt(best[1]["t"])))
        if not qualified:
            log("   -> TRAIN 기준을 넘은 조건 없음")
            continue
        cond, st = max(qualified, key=lambda x: x[1]["t"])
        hs = SS.cluster_stats(hold if cond is None else [t for t in hold if SS.passes(t, cond)])
        ok = _ok(hs, HOLD_MIN_N, HOLD_MIN_DAYS, HOLD_MIN_T)
        log("   HOLDOUT 재현: n=%d days=%d net %s t %s => %s" % (hs["n"], hs["days"], _pct(hs["net"]), _tt(hs["t"]), "발견" if ok else "재현 안 됨"))
        if ok:
            found.append({"pop": pop, "cond": list(cond) if cond else None, "train": _slim(st), "holdout": _slim(hs)})
    found.sort(key=lambda f: -f["holdout"]["t"])
    return found, tests


def cand_key(c):
    cond = c.get("cond")
    return (c["event"], c["hold"], cond[1] if cond else None, cond[2] if cond else None)


def register(registry, found, run_day, universe):
    """발견한 것을 장부 형식으로 → 새로 등록할 후보 목록 (registry 는 바꾸지 않는다). 달에 MAX_NEW 개까지."""
    active = {cand_key(c) for c in registry if c.get("status") == "ACTIVE"}
    taken = {c["id"] for c in registry}
    new = []
    for f in found:
        event, hold = f["pop"].split("/")
        spec = {"engine": ENGINE, "registered": run_day, "event": event, "hold": int(hold), "cond": f["cond"], "universe": list(universe),
                "move": MOVE, "vol_x": VOL_X, "cost": dict(COST), "cost_other": COST_OTHER, "train": f["train"], "holdout": f["holdout"],
                "status": "ACTIVE"}
        if cand_key(spec) in active or len(new) >= MAX_NEW:
            continue
        k = 1
        while "D-%s-%02d" % (run_day[:7].replace("-", ""), k) in taken:
            k += 1
        spec["id"] = "D-%s-%02d" % (run_day[:7].replace("-", ""), k)
        taken.add(spec["id"])
        active.add(cand_key(spec))
        new.append(spec)
    return new


def describe(c):
    cond = c.get("cond")
    tail = "조건 없음" if not cond else "%s %s %.4g" % (cond[1], cond[2], cond[3])
    move = "%.0f%%" % (100 * c["move"])
    event = {"SURGE": "1시간 +%s 이상 급등" % move, "CRASH": "1시간 −%s 이하 급락" % move,
             "BREAKOUT": "24시간 고가 돌파 + 거래대금 %.0f배" % c["vol_x"]}.get(c["event"], c["event"])
    return "%s → %d시간 보유, %s" % (event, c["hold"], tail)


# ---------------------------------------------------------------------------------------------------------------------
def candidate_trades(panel, cand, end_h):
    """등록된 후보의, 등록일 00:00 UTC 이후 사건에서 나온 끝난 거래. 등록 때 고정한 정의(문턱·비용)만 쓴다."""
    if cand.get("engine") != ENGINE:
        raise RuntimeError("후보 %s 는 틀 %s 로 등록됐고 지금 코드는 틀 %s 다 — 채점하지 않는다" % (cand.get("id"), cand.get("engine"), ENGINE))
    rows = build_trades(panel, day_hour(cand["registered"]), end_h, cand["move"], cand["vol_x"], cand["cost"], cand["cost_other"],
                        only=(cand["event"], cand["hold"]))
    cond = cand.get("cond")
    if cond:
        rows = [t for t in rows if SS.passes(t, cond)]
    return [{k: t[k] for k in ("market", "entry_h", "exit_h", "day", "gross", "net", "move")} for t in rows]


def merge_trades(old_rows, new_rows):
    """장부에 새 거래만 덧붙인다 → (합친 목록, 새로 붙은 수, 값이 달라진 거래 표시)."""
    by_key = {(r["market"], r["entry_h"]): r for r in old_rows}
    added, mismatch = 0, []
    for r in new_rows:
        key = (r["market"], r["entry_h"])
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = r
            added += 1
        elif abs(prev["net"] - r["net"]) > 1e-9:
            mismatch.append("%s %s" % (r["market"][4:], r["day"]))
    return sorted(by_key.values(), key=lambda t: (t["entry_h"], t["market"])), added, mismatch


def decide_once(entry, cand, run_day):
    """30건·10일이 처음 채워지는 거래까지로 한 번만 판정. 180일이 지나도록 못 채우면 EXPIRED. 이미 판정이 있으면 그대로."""
    if entry.get("decision"):
        return entry
    days = set()
    for k, t in enumerate(entry["rows"], 1):
        days.add(t["day"])
        if k >= FWD_MIN_N and len(days) >= FWD_MIN_DAYS:
            st = SS.cluster_stats(entry["rows"][:k])
            entry["decision"] = {"verdict": "PASS" if st["net"] > 0 and st["daymean"] > 0 else "FAIL", "decided_on": run_day, "trades": k,
                                 "stats": _slim(st)}
            return entry
    age = (dt.date.fromisoformat(run_day) - dt.date.fromisoformat(cand["registered"])).days
    if age > FWD_EXPIRE_DAYS:
        entry["decision"] = {"verdict": "EXPIRED", "decided_on": run_day, "trades": len(entry["rows"]), "stats": None}
    return entry


# ---------------------------------------------------------------------------------------------------------------------
def run(today, days, markets, fetch=None, log=print):
    """탐색 한 번 → (발견 목록, 보고 줄들)."""
    lines = []

    def out(s):
        lines.append(s)
        log(s)

    end_h = day_hour(today)
    start_h = end_h - days * 24
    out("DISCOVER %s | 틀 %s%s | %d종목 | %s ~ %s | TRAIN 앞 %.0f%%" % (
        today, ENGINE, "" if FRAME_APPROVED else " (초안 — 등록 잠김)", len(markets), hour_day(start_h), hour_day(end_h - 1), 100 * TRAIN_FRAC))
    panel = load_panel(start_h - WARMUP_H, end_h, markets, fetch, log=log)
    trades = build_trades(panel, start_h, end_h)
    cut_day = hour_day(start_h + int(TRAIN_FRAC * (end_h - start_h)))
    out("거래 %d건, TRAIN < %s <= HOLDOUT" % (len(trades), cut_day))
    found, tests = search(trades, cut_day, out)
    out("훑은 조합 %d개 → 발견 %d개" % (tests, len(found)))
    return found, lines


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--days", type=int, default=HISTORY_DAYS)
    ap.add_argument("--markets", help="쉼표로 구분 (점검용). 기본은 고정 12종목")
    ap.add_argument("--today", help="UTC 날짜 YYYY-MM-DD (기본은 오늘)")
    a = ap.parse_args(argv)
    today = a.today or dt.datetime.now(UTC).date().isoformat()
    custom = bool(a.markets) or a.days != HISTORY_DAYS
    markets = tuple(a.markets.split(",")) if a.markets else pick_universe(today)
    found, lines = run(today, a.days, markets, log=lambda s: print(s, flush=True))
    out = Path(a.out)
    path = out / "registry.json"
    registry = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    new = register(registry, found, today, markets)
    for c in new:
        lines.append("후보 %s: %s (TRAIN t %s, HOLDOUT t %s)" % (c["id"], describe(c), _tt(c["train"]["t"]), _tt(c["holdout"]["t"])))
    if not new:
        lines.append("새로 등록할 후보 없음")
    if a.register and new and FRAME_APPROVED and not custom:
        out.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(registry + new, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        lines.append("등록함: %s" % ", ".join(c["id"] for c in new))
    elif a.register and new:
        lines.append("등록하지 않음: %s" % ("점검용 범위로 돌린 결과" if custom else "틀이 아직 초안(FRAME_APPROVED=False)"))
    if a.register and not custom:
        out.mkdir(parents=True, exist_ok=True)
        (out / "DISCOVERY.md").write_text("# 후보 탐색 기록\n\n자동으로 만들어지는 파일입니다. 마지막 실행 %s (UTC).\n\n```\n%s\n```\n" % (
            today, "\n".join(lines)), encoding="utf-8")
    print("\n".join(lines[-max(1, len(new)):]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
