#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""discover — '사건이 일어난 사례들 안에서 어떤 조건일 때만 되는가' 를 달마다 자동으로 묻고, 넘은 것만 후보로 등록한다.
(공개 시세만, 키·주문 없음, 표준 라이브러리. GitHub Actions 월 1회 실행용 — 거래 서버에서 돌리지 않는다.)

왜 (사용자 원칙, 2026-11-10): "급등하면 버는가" 가 아니라 "급등한 수많은 사례 중 어떤 조건일 때만 이어지는가" 를 물어야 한다.
    지금까지는 사람이 한 번씩 손으로 돌렸다. 이 파일은 같은 질문을 정해진 틀 안에서 달마다 다시 묻는다.
    찾은 것을 믿을지는 여기서 정하지 않는다 — 등록일 이후의 새 자료로 forward_watch 가 판정한다.

틀 (고문 지정 2026-11-12. 번호는 고문 메시지의 항목 번호)
  1 자료    업비트 15분봉, 최근 약 4년. 대상 종목은 'HOLDOUT 시작일 기준' 으로 고른다 (고문 2026-11-12 추가 지시):
            그날 이전 200일 거래대금 상위 30종목, 그날 기준 상장 2년 이상, 스테이블코인 제외, BTC 는 항상 포함.
            오늘 기준으로 고르면 뒤 구간에서 살아남은 코인만 들어가기 때문이다. 재현(8번)만 고문의 고정 20종목을 쓴다.
  2 사건    봉 하나가 끝난 시점에 판단한다. 셋 다 '그 봉의 거래대금이 직전 96개 봉 중앙값의 3배 이상' 이 붙는다.
            SURGE     15분 수익률 ≥ +3%
            CRASH     15분 수익률 ≤ −3%
            BREAKOUT  종가가 직전 96개 봉의 최고 고가를 넘고 수익률 > 0
            (고문 재현 정의 2026-11-13: 거래대금 = candle_acc_trade_price, 기준은 현재 봉을 뺀 직전 96개 봉의 '중앙값'.
             96개는 행 기준이다 — 거래가 없어 빠진 봉이 있으면 그만큼 더 먼 과거까지 본다. 이 정의로 고문의 코인별 건수가 맞는다.)
            다음 봉 시가에 사서 1시간·4시간·24시간 뒤 봉의 시가에 판다 (모집단 = 사건 3 × 보유 3). 한 종목·한 모집단에서 동시에 1건.
            (24시간 보유는 내 초안에 있던 것으로, 고문이 유지하라고 했다.)
            비용 편도 BTC·ETH 0.07%, 그 밖 0.10%.
  3 조건    지표 하나만 붙인다. 사건 봉이 끝난 시점에 알 수 있는 값만 쓴다.
            시장  btc_ret_1h · btc_ret_4h · btc_ret_1d · btc_above_50d(50일 평균 위=1) · btc_dist_50d(평균 대비 거리)
                  · up_ratio(1일 수익률이 양수인 종목 비율) · mkt_ret_1h(다른 종목 1시간 수익률 평균) · same_events_1h(최근 1시간 안의 같은 사건 수)
            종목  bar_size(사건 봉 크기) · vol_mult(거래대금 배수) · ret_1d · pos_1d_high(1일 고점 대비 위치) · compress(변동성 압축) · hour_kst
  4 문턱    TRAIN 분위수 5·10·20·33·50% '이하' 와 50·67·80·90·95% '이상' (지표마다 최대 10개).
  5 선별    TRAIN: 거래 ≥ 150건, 평균 순수익 > 0, t ≥ 2 인 것은 전부 HOLDOUT 으로 넘긴다.
            HOLDOUT: 거래 ≥ 100건 · 평균 > 0 · 일 단위 하한 > 0 · 전반·후반 모두 > 0 · 날짜 가중 평균 > 0 · 상위 5일을 빼도 > 0.
  6 등록    통과한 것 중 사건마다 최대 1개(일 단위 하한이 가장 높은 것), 한 달 최대 3개. 등록 내용은 forward/registry.json 에 고정된다.
            판정은 forward_watch 가 등록 이후 자료 30건·10일에서 한 번만 한다. 12개월 안에 못 채우면 EXPIRED
            (고문 결정 2026-11-13: 자동 등록 후보 12개월, 급락 반등 계열 24개월. 만료 = 표본 부족 종료이지 탈락이 아니다).
  7 기록    실행마다 훑은 규칙 수·선별 수·통과 수를 forward/scan_log.json 에 덧붙인다 (몇 개를 봤는지가 남아야 우연인지 판단할 수 있다).
  8 재현    첫 실행은 고문 결과의 재현이다: --repro 는 고문의 고정 20종목(REPRO_UNIVERSE)을 고문 정의 그대로 돌린다 —
            첫 봉 2022-12-20 00:00 UTC, 진입일(UTC) 2023-01-01 이상 2026-11-10 미만, 2025-01-01 미만이 앞 구간.
            조건 없는 1시간·4시간 보유 여섯 모집단의 건수(정확히)와 평균(0.001%p 이내), 급등 1시간의 코인별 건수,
            알트 동반 급락 후보의 과거 수치를 고문 값과 나란히 찍고, 전부 맞으면 종료 코드 0. 맞으면 고문이 잠금 해제를 정한다.

*** 아직 잠겨 있다. FRAME_APPROVED 가 False 인 동안 --register 는 기록(훑은 수·탐색 결과)만 남기고 후보를 등록하지 않는다.
    잠금은 고문이 재현 결과를 보고 푼다. 틀을 고치면 ENGINE 번호를 올린다(옛 번호로 등록된 후보는 채점을 멈춘다). ***

고문 틀에 적혀 있지 않아 내가 정한 것
  · 15분 수익률 = 그 봉 종가 / 직전 봉 종가 − 1 (직전 봉이 정확히 15분 전일 때만).
  · 진입부터 청산까지 간격이 정확히 15분 × 봉 수가 아니면(거래소 점검 등) 거래로 세지 않는다.
  · t = 진입일(UTC)을 묶음으로 한 군집 t.                      · 일 단위 하한 = 일별 평균의 단측 95% 하한(정규 근사).
  · 전반·후반 = HOLDOUT 거래를 시간순으로 반씩.               · 상위 5일 = 그날 순수익 합이 가장 큰 5일.
  · up_ratio 는 1일 수익률 기준, mkt_ret_1h 는 자기 종목 제외, same_events_1h 는 자기 사건 제외.
  · compress = 최근 1일 평균 변동 / 최근 7일 평균 변동.        · 달마다 도는 탐색의 구간 = 앞 60% TRAIN, 뒤 40% HOLDOUT.
  · 조건을 붙인 거래 = 기본 모집단(코인당 1건 규칙 적용 뒤)에서 조건을 통과한 거래. 등록 후 채점도 같은 방식이다.
한계: 봉 기준이라 실제 호가 체결을 확인한 값이 아니다. 업비트는 상장폐지된 종목의 시세를 주지 않아서, 대상을 HOLDOUT 시작일
      기준으로 골라도 '그 뒤 상장폐지된 종목' 은 빠진다(등록 뒤의 전진 채점에는 이 편향이 없다). 초·분 단위와 호가는 다루지 않는다.

이력: 2026-11-12 오전에 내 초안(60분봉·30종목·3분위)으로 전체를 한 번 돌렸다 — 조합 213개, 발견 0 (커밋 4b4ce75 의 DISCOVERY.md).
      같은 날 고문 틀로 바꿨다(ENGINE 2: 거래대금 기준을 직전 1일 '평균' 으로 잡았다). 그 판으로 전체를 한 번 돌린 기록이
      scan_log.json 의 engine "2" 줄이다(규칙 1,161개, 통과 1개, 등록 없음).
      2026-11-13 고문이 재현 정의를 줬다: 기준은 '중앙값', 돌파에 수익률 > 0 (ENGINE 3). 어느 판으로도 등록된 후보는 없다.
      같은 날 재현: 여섯 모집단의 건수·평균, 급등 1시간의 코인별 건수 20종목, 알트 동반 급락 과거 수치(244건 +1.450% / 146건 +1.541%)가
      고문 값과 전부 일치했다. 잠금 해제는 고문이 정한다.

실행
  python3 scalp/research/discover.py --repro [--markets KRW-BTC,KRW-ETH,...]   고문 수치 재현 (등록·기록 안 함)
  python3 scalp/research/discover.py --register                              달마다 도는 실행 (잠겨 있으면 기록만)
  python3 scalp/research/discover.py --markets KRW-BTC,KRW-ETH --days 90      작은 범위 점검 (등록·기록 안 함)
"""
import argparse
import datetime as dt
import importlib.util
import json
import math
import sys
import urllib.parse
from array import array
from bisect import bisect_left, bisect_right, insort
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean, stdev

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(HERE / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AM = _load("auto_monitor")          # 조회 함수(http_get)만 빌려 쓴다
SS = _load("shadow_salvage")        # 군집 통계 (H27 에서 쓴 것 그대로)

ENGINE = "3"                        # 3 = 고문 재현 정의(중앙값 기준·돌파에 수익률 > 0). 2 는 평균 기준이었다
FRAME_APPROVED = False              # 고문이 재현 결과를 보고 푼다. 그 전에는 등록하지 않는다
REPRO_UNIVERSE = tuple("KRW-" + c for c in                         # 고문의 고정 20종목 (2026-11-13 수령)
                       "BTC ETH XRP DOGE SOL WAVES AXS ETC CHZ SAND IQ APT ADA GMT KAVA POL TRX CELO ARK ALGO".split())
UNIVERSE_N, LIQ_DAYS, MIN_LISTED_DAYS = 30, 200, 730
STABLE = ("KRW-USDT", "KRW-USDC", "KRW-USD1", "KRW-USDE", "KRW-DAI", "KRW-TUSD")
UTC = dt.timezone.utc
BAR_MIN, BARS_H, BARS_D = 15, 4, 96
HISTORY_DAYS, TRAIN_FRAC = 1460, 0.60
MOVE, VOL_X, HOLDS = 0.03, 3.0, (BARS_H, 4 * BARS_H, BARS_D)
VOL_BARS = 96                       # 거래대금 중앙값·돌파 고가를 보는 직전 봉 수 (행 기준)
VOL_MIN_BARS = 77                   # 지표 pos_1d_high 는 직전 1일 봉이 80% 이상 있을 때만
COST, COST_OTHER = {"KRW-BTC": 0.0007, "KRW-ETH": 0.0007}, 0.0010
QUANT_LOW, QUANT_HIGH = (0.05, 0.10, 0.20, 0.33, 0.50), (0.50, 0.67, 0.80, 0.90, 0.95)
TRAIN_MIN_N, TRAIN_MIN_T, HOLD_MIN_N = 150, 2.0, 100
Z95 = 1.6449
MAX_NEW = 3
FWD_MIN_N, FWD_MIN_DAYS, FWD_EXPIRE_DAYS = 30, 10, 365
WARMUP_Q = 55 * BARS_D              # 지표 계산에 필요한 앞쪽 자료 (BTC 50일 평균)
WORKERS = 6                         # 동시에 받는 종목 수. 전체 호출 간격은 auto_monitor.http_get 이 초당 8회 아래로 묶는다
OUT_DIR = HERE / "forward"
REPRO_DATA_START, REPRO_START, REPRO_CUT, REPRO_END = "2022-12-20", "2023-01-01", "2025-01-01", "2026-11-10"
REPRO_TOL = 0.00001                 # 평균 허용 오차 0.001%p
# 고문 결과 (조건 없음): (사건, 보유 봉 수) → ((앞 구간 건수, 평균), (뒤 구간 건수, 평균))
ADVISOR_REF = {("SURGE", 4): ((1506, -0.00399), (924, -0.00770)), ("SURGE", 16): ((1309, -0.00610), (787, -0.01154)),
               ("BREAKOUT", 4): ((8673, -0.00282), (7204, -0.00269)), ("BREAKOUT", 16): ((6778, -0.00228), (5413, -0.00297)),
               ("CRASH", 4): ((1168, 0.00264), (825, -0.00010)), ("CRASH", 16): ((1053, 0.00696), (692, -0.00491))}
ADVISOR_SURGE_1H = {"ADA": (44, 32), "ALGO": (51, 29), "APT": (97, 31), "ARK": (203, 140), "AXS": (72, 82), "BTC": (5, 1), "CELO": (68, 49),
                    "CHZ": (45, 23), "DOGE": (68, 36), "ETC": (61, 19), "ETH": (10, 17), "GMT": (94, 60), "IQ": (154, 72), "KAVA": (110, 60),
                    "POL": (24, 26), "SAND": (81, 50), "SOL": (78, 22), "TRX": (17, 6), "WAVES": (161, 140), "XRP": (63, 29)}
# 알트 동반 급락 후보(고문 등록 권고): 급락 1시간 + 같은 시각 BTC 4봉 수익률 ≤ −1.821%
ADVISOR_ALT = {"cond": ("btc_ret_1h", "<=", -0.01821), "ref": ((244, 0.01450), (146, 0.01541))}


# ---------------------------------------------------------------------------------------------------------------------
def to_q(t):
    """시각 → 15분봉 번호."""
    return int(t.timestamp() // (60 * BAR_MIN))


def day_q(day):
    return to_q(dt.datetime.fromisoformat(day).replace(tzinfo=UTC))


def q_day(q):
    return dt.datetime.fromtimestamp(q * 60 * BAR_MIN, tz=UTC).strftime("%Y-%m-%d")


class Bars(object):
    """한 종목의 15분봉을 시간순 배열로 들고 있다. rows = {봉 번호: (시가, 고가, 저가, 종가, 거래대금)}."""
    __slots__ = ("ts", "o", "h", "c", "v", "cc")

    def __init__(self, rows):
        items = sorted(rows.items())
        self.ts = array("q", [q for q, _ in items])
        self.o = array("d", [b[0] for _, b in items])
        self.h = array("d", [b[1] for _, b in items])
        self.c = array("d", [b[3] for _, b in items])
        self.v = array("d", [b[4] for _, b in items])
        self.cc = array("d", [0.0])
        for x in self.c:
            self.cc.append(self.cc[-1] + x)

    def __len__(self):
        return len(self.ts)

    def idx(self, q):
        i = bisect_left(self.ts, q)
        return i if i < len(self.ts) and self.ts[i] == q else -1

    def close(self, q):
        i = self.idx(q)
        return self.c[i] if i >= 0 else None

    def ret(self, q, k):
        a, z = self.close(q), self.close(q - k)
        return a / z - 1 if a is not None and z else None

    def window(self, q_from, q_to):
        """봉 번호가 [q_from, q_to] 인 구간의 배열 위치 (i, j) — 반열린 구간 [i, j)."""
        return bisect_left(self.ts, q_from), bisect_right(self.ts, q_to)


def fetch_bars(market, start_q, end_q, get=None):
    """[start_q, end_q) 의 15분봉 → Bars. end_q 는 포함하지 않으므로 진행 중인 봉은 들어오지 않는다."""
    get = get or AM.http_get
    out, to = {}, end_q
    while True:
        stamp = dt.datetime.fromtimestamp(to * 60 * BAR_MIN, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = get("%s/v1/candles/minutes/%d?market=%s&count=200&to=%s" % (AM.UPBIT, BAR_MIN, market, urllib.parse.quote(stamp)))
        if not rows:
            break
        qs = []
        for r in rows:
            q = to_q(AM.parse_utc(r["candle_date_time_utc"]))
            qs.append(q)
            if start_q <= q < end_q:
                out[q] = (float(r["opening_price"]), float(r["high_price"]), float(r["low_price"]), float(r["trade_price"]),
                          float(r["candle_acc_trade_price"]))
        oldest = min(qs)
        if oldest <= start_q or len(rows) < 200 or oldest >= to:
            break
        to = oldest
    return Bars(out)


def pick_universe(as_of, n=UNIVERSE_N, list_markets=None, daily_value=None, listed_before=None):
    """as_of(YYYY-MM-DD) 이전 LIQ_DAYS 일 거래대금 합이 큰 n 종목. 그날 기준 상장 MIN_LISTED_DAYS 일 이상만.
    스테이블코인은 빼고 BTC 는 항상 넣는다. as_of 당일과 그 뒤의 자료는 쓰지 않는다."""
    get = AM.http_get
    list_markets = list_markets or (lambda: [m["market"] for m in get(AM.UPBIT + "/v1/market/all") if m["market"].startswith("KRW-")])
    old_day = (dt.date.fromisoformat(as_of) - dt.timedelta(days=MIN_LISTED_DAYS)).isoformat()

    def default_value(m):
        rows = get("%s/v1/candles/days?market=%s&count=%d&to=%s" % (AM.UPBIT, m, LIQ_DAYS, urllib.parse.quote(as_of + "T00:00:00Z")))
        return sum(float(r["candle_acc_trade_price"]) for r in rows if r["candle_date_time_utc"][:10] < as_of)

    def default_listed(m):                                           # 그 날짜 이전의 일봉이 하나라도 있으면 그때 이미 상장돼 있었다
        return bool(get("%s/v1/candles/days?market=%s&count=1&to=%s" % (AM.UPBIT, m, urllib.parse.quote(old_day + "T00:00:00Z"))))

    daily_value, listed_before = daily_value or default_value, listed_before or default_listed
    names = [m for m in list_markets() if m not in STABLE]
    with ThreadPoolExecutor(WORKERS) as pool:
        scored = sorted(zip(pool.map(daily_value, names), names), reverse=True)
    top = []
    for value, m in scored:                                          # 거래대금 순으로 내려가며 상장 기간을 확인한다
        if len(top) >= n or value <= 0:
            break
        if listed_before(m):
            top.append(m)
    if "KRW-BTC" not in top:
        top = ["KRW-BTC"] + top[:n - 1]
    return tuple(sorted(top))


def load_panel(start_q, end_q, markets, fetch=None, log=None):
    """{종목: Bars}. 한 종목이라도 못 받으면 예외 — 빠진 종목이 시장 지표를 바꾸는 것을 막는다."""
    fetch = fetch or fetch_bars
    with ThreadPoolExecutor(WORKERS) as pool:
        panel = dict(zip(markets, pool.map(lambda m: fetch(m, start_q, end_q), markets)))
    if log:
        log("  받은 봉: " + ", ".join("%s %d" % (m[4:], len(panel[m])) for m in markets))
    return panel


# ---------------------------------------------------------------------------------------------------------------------
def detect(bars, move=MOVE, vol_x=VOL_X):
    """한 종목의 사건 봉 → [(배열 위치, {사건: 15분 수익률}, 거래대금 배수)]. 고문 재현 정의(2026-11-13) 그대로:
    기준 = 현재 봉을 뺀 직전 96개 봉(행 기준)의 거래대금 중앙값. 직전 봉이 정확히 15분 전이 아니면 판단하지 않는다."""
    ts, c, v, h = bars.ts, bars.c, bars.v, bars.h
    out, win = [], []                                               # win = 직전 96개 봉 거래대금의 정렬 목록
    for k in range(1, len(ts)):
        insort(win, v[k - 1])
        if k > VOL_BARS:
            win.pop(bisect_left(win, v[k - 1 - VOL_BARS]))
        if k < VOL_BARS or ts[k - 1] != ts[k] - 1 or c[k - 1] <= 0:
            continue
        ref = (win[VOL_BARS // 2 - 1] + win[VOL_BARS // 2]) / 2.0
        if ref <= 0 or v[k] < vol_x * ref:
            continue
        ret, events = c[k] / c[k - 1] - 1, {}
        if ret >= move:
            events["SURGE"] = ret
        if ret <= -move:
            events["CRASH"] = ret
        if ret > 0 and c[k] > max(h[k - VOL_BARS:k]):
            events["BREAKOUT"] = ret
        if events:
            out.append((k, events, v[k] / ref))
    return out


def indicators(panel, market, k):
    """배열 위치 k 의 봉이 끝난 시점에 알 수 있는 값만으로 만든 지표. 계산할 수 없는 항목은 넣지 않는다."""
    b, btc, ind = panel[market], panel.get("KRW-BTC"), {}
    q = b.ts[k]

    def put(name, value):
        if value is not None:
            ind[name] = float(value)

    put("bar_size", abs(b.c[k] / b.c[k - 1] - 1) if k and b.c[k - 1] > 0 else None)
    put("ret_1d", b.ret(q, BARS_D))
    i, j = b.window(q - BARS_D + 1, q)
    if j - i >= VOL_MIN_BARS:
        put("pos_1d_high", b.c[k] / max(b.h[i:j]) - 1)

    def move(n):
        i, j = b.window(q - n + 1, q)
        vals = [abs(b.c[t] / b.c[t - 1] - 1) for t in range(max(i, 1), j) if b.ts[t - 1] == b.ts[t] - 1 and b.c[t - 1] > 0]
        return mean(vals) if len(vals) >= 0.8 * n else None

    short, long_ = move(BARS_D), move(7 * BARS_D)
    if short is not None and long_:
        put("compress", short / long_)
    put("hour_kst", (dt.datetime.fromtimestamp((q + 1) * 60 * BAR_MIN, tz=UTC) + dt.timedelta(hours=9)).hour)
    if btc is not None:
        put("btc_ret_1h", btc.ret(q, BARS_H))
        put("btc_ret_4h", btc.ret(q, 4 * BARS_H))
        put("btc_ret_1d", btc.ret(q, BARS_D))
        i, j = btc.window(q - 50 * BARS_D + 1, q)
        now = btc.close(q)
        if now is not None and j - i >= 45 * BARS_D:
            avg = (btc.cc[j] - btc.cc[i]) / (j - i)
            put("btc_dist_50d", now / avg - 1)
            put("btc_above_50d", 1.0 if now > avg else 0.0)
    day = [r for r in (panel[m].ret(q, BARS_D) for m in panel) if r is not None]
    if len(day) >= max(2, len(panel) // 2):
        put("up_ratio", sum(1 for r in day if r > 0) / float(len(day)))
    hour = [r for r in (panel[m].ret(q, BARS_H) for m in panel if m != market) if r is not None]
    if len(hour) >= max(1, (len(panel) - 1) // 2):
        put("mkt_ret_1h", mean(hour))
    return ind


def build_trades(panel, start_q, end_q, move=MOVE, vol_x=VOL_X, cost=None, cost_other=COST_OTHER, only=None):
    """사건 봉이 [start_q, end_q) 인 모든 모집단의 끝난 거래 → [{pop, event, hold, market, entry_q, exit_q, day, gross, net, ind}].
    only=(사건, 보유 봉 수) 를 주면 그 모집단만. 한 종목·한 모집단에서 동시에 1건(앞 거래의 청산 봉까지 새 신호를 받지 않는다)."""
    cost = COST if cost is None else cost
    detected = {m: detect(panel[m], move, vol_x) for m in panel}
    counts = {}
    for m, found in detected.items():
        for k, events, _ in found:
            for name in events:
                key = (name, panel[m].ts[k])
                counts[key] = counts.get(key, 0) + 1
    holds = (only[1],) if only else HOLDS
    trades = []
    for m in sorted(panel):
        b, busy, c, n = panel[m], {}, cost.get(m, cost_other), len(panel[m])
        for k, events, mult in detected[m]:
            q = b.ts[k]
            if not (start_q <= q < end_q) or k + 1 >= n or b.ts[k + 1] != q + 1:
                continue
            base = None
            for name in sorted(events):
                if only and name != only[0]:
                    continue
                for hold in holds:
                    pop, x = "%s/%dh" % (name, hold // BARS_H), k + 1 + hold
                    if q <= busy.get(pop, -1) or x >= n or b.ts[x] != q + 1 + hold:
                        continue
                    if base is None:
                        base = indicators(panel, m, k)
                        base["vol_mult"] = float(mult)
                    ind = dict(base)
                    ind["same_events_1h"] = float(sum(counts.get((name, q - d), 0) for d in range(BARS_H)) - 1)
                    gross = b.o[x] / b.o[k + 1] - 1
                    trades.append({"pop": pop, "event": name, "hold": hold, "market": m, "entry_q": q + 1, "exit_q": q + 1 + hold,
                                   "day": q_day(q + 1), "gross": gross, "net": (1 + gross) * (1 - c) / (1 + c) - 1, "move": events[name],
                                   "ind": ind})
                    busy[pop] = q + hold
    trades.sort(key=lambda t: (t["entry_q"], t["market"], t["pop"]))
    return trades


# ---------------------------------------------------------------------------------------------------------------------
def make_conditions(train):
    """TRAIN 분위수로 조건 목록을 만든다 → [(이름, 지표, 연산, 문턱)]. 같은 (지표, 연산, 문턱)은 한 번만."""
    conds, seen = [], set()
    for name in sorted({k for t in train for k in t["ind"]}):
        vals = sorted(t["ind"][name] for t in train if name in t["ind"])
        if len(vals) < 0.8 * len(train) or vals[0] == vals[-1]:
            continue
        for op, quants in (("<=", QUANT_LOW), (">=", QUANT_HIGH)):
            for p in quants:
                thr = vals[min(len(vals) - 1, int(p * len(vals)))]
                if (name, op, thr) not in seen:
                    seen.add((name, op, thr))
                    conds.append(("%s %s q%02d" % (name, op, round(100 * p)), name, op, thr))
    return conds


def holdout_checks(trades):
    """HOLDOUT 여섯 기준 → dict(n, days, net, daymean, day_low, first, second, ex_top5, pass)."""
    st = SS.cluster_stats(trades)
    out = {"n": st["n"], "days": st["days"], "net": st["net"], "t": st["t"], "daymean": st["daymean"], "day_low": None, "first": None,
           "second": None, "ex_top5": None}
    if trades:
        by_day = {}
        for t in trades:
            by_day.setdefault(t["day"], []).append(t["net"])
        means = [mean(v) for v in by_day.values()]
        if len(means) >= 2:
            out["day_low"] = mean(means) - Z95 * stdev(means) / math.sqrt(len(means))
        rows = sorted(trades, key=lambda t: (t["entry_q"], t["market"]))
        half = len(rows) // 2
        if half:
            out["first"], out["second"] = mean(t["net"] for t in rows[:half]), mean(t["net"] for t in rows[half:])
        best = set(sorted(by_day, key=lambda d: sum(by_day[d]), reverse=True)[:5])
        rest = [x for d, v in by_day.items() if d not in best for x in v]
        if rest:
            out["ex_top5"] = mean(rest)
    out["pass"] = bool(out["n"] >= HOLD_MIN_N and all(out[k] is not None and out[k] > 0
                                                       for k in ("net", "day_low", "first", "second", "daymean", "ex_top5")))
    return out


def _pct(v):
    return "n/a" if v is None else "%+.3f%%" % (100 * v)


def _tt(v):
    return "n/a" if v is None else "%+.2f" % v


def _label(cond):
    return "조건 없음" if cond is None else "%s %s %.4g" % (cond[1], cond[2], cond[3])


def search(trades, cut_day, log=print):
    """모집단마다 TRAIN 에서 선별하고 HOLDOUT 에서 여섯 기준을 본다 → (통과 목록, {scanned, selected, passed})."""
    passed, counts = [], {"scanned": 0, "selected": 0, "passed": 0}
    for pop in sorted({t["pop"] for t in trades}):
        train = [t for t in trades if t["pop"] == pop and t["day"] < cut_day]
        hold = [t for t in trades if t["pop"] == pop and t["day"] >= cut_day]
        bt, bh = SS.cluster_stats(train), SS.cluster_stats(hold)
        log("== %s | TRAIN n=%d days=%d net %s t %s | HOLDOUT n=%d net %s t %s" % (
            pop, bt["n"], bt["days"], _pct(bt["net"]), _tt(bt["t"]), bh["n"], _pct(bh["net"]), _tt(bh["t"])))
        options = [(None, bt)] + [(c, SS.cluster_stats([t for t in train if SS.passes(t, c)])) for c in make_conditions(train)]
        counts["scanned"] += len(options)
        chosen = [(c, st) for c, st in options
                  if st["n"] >= TRAIN_MIN_N and st["net"] is not None and st["net"] > 0 and st["t"] is not None and st["t"] >= TRAIN_MIN_T]
        counts["selected"] += len(chosen)
        chosen.sort(key=lambda x: -x[1]["t"])
        log("   훑은 규칙 %d개, TRAIN 선별 %d개" % (len(options), len(chosen)))
        for rank, (cond, st) in enumerate(chosen):
            hc = holdout_checks(hold if cond is None else [t for t in hold if SS.passes(t, cond)])
            if rank < 5 or hc["pass"]:
                log("   %-34s TRAIN n=%d net %s t %s | HOLDOUT n=%d net %s 하한 %s 전반 %s 후반 %s 상위5일 제외 %s => %s" % (
                    _label(cond), st["n"], _pct(st["net"]), _tt(st["t"]), hc["n"], _pct(hc["net"]), _pct(hc["day_low"]), _pct(hc["first"]),
                    _pct(hc["second"]), _pct(hc["ex_top5"]), "통과" if hc["pass"] else "탈락"))
            if hc["pass"]:
                counts["passed"] += 1
                passed.append({"pop": pop, "event": pop.split("/")[0], "hold": train[0]["hold"] if train else hold[0]["hold"],
                               "cond": list(cond) if cond else None,
                               "train": {k: st[k] for k in ("n", "days", "net", "t")},
                               "holdout": {k: hc[k] for k in ("n", "days", "net", "daymean", "day_low", "first", "second", "ex_top5")}})
    return passed, counts


def cand_key(c):
    cond = c.get("cond")
    return (c["event"], c["hold"], cond[1] if cond else None, cond[2] if cond else None)


def register(registry, passed, run_day, universe):
    """통과한 것 중 사건마다 1개(일 단위 하한이 가장 높은 것), 달에 MAX_NEW 개까지 → 새로 등록할 후보 목록."""
    active = {cand_key(c) for c in registry if c.get("status") == "ACTIVE"}
    taken = {c["id"] for c in registry}
    new, used_events = [], set()
    for f in sorted(passed, key=lambda f: -f["holdout"]["day_low"]):
        spec = {"engine": ENGINE, "registered": run_day, "event": f["event"], "hold": f["hold"], "cond": f["cond"], "universe": list(universe),
                "move": MOVE, "vol_x": VOL_X, "cost": dict(COST), "cost_other": COST_OTHER, "train": f["train"], "holdout": f["holdout"],
                "status": "ACTIVE"}
        if f["event"] in used_events or cand_key(spec) in active or len(new) >= MAX_NEW:
            continue
        k = 1
        while "D-%s-%02d" % (run_day[:7].replace("-", ""), k) in taken:
            k += 1
        spec["id"] = "D-%s-%02d" % (run_day[:7].replace("-", ""), k)
        taken.add(spec["id"])
        used_events.add(f["event"])
        new.append(spec)
    return new


def describe(c):
    move, mult = "%.0f%%" % (100 * c["move"]), "%.0f배" % c["vol_x"]
    event = {"SURGE": "15분 +%s 이상 급등(거래대금 %s)" % (move, mult), "CRASH": "15분 −%s 이하 급락(거래대금 %s)" % (move, mult),
             "BREAKOUT": "1일 고가 돌파(거래대금 %s)" % mult}.get(c["event"], c["event"])
    return "%s → %d시간 보유, %s" % (event, c["hold"] // BARS_H, _label(c.get("cond")))


# ---------------------------------------------------------------------------------------------------------------------
def cand_start(cand):
    """전진 채점 시작일(UTC). 따로 적혀 있지 않으면 등록일."""
    return cand.get("start") or cand["registered"]


def ex_best_day(rows):
    """일별 평균이 가장 높은 하루를 뺀 날짜 가중 평균. 남는 날이 없으면 None."""
    by_day = {}
    for t in rows:
        by_day.setdefault(t["day"], []).append(t["net"])
    means = sorted(mean(v) for v in by_day.values())
    return mean(means[:-1]) if len(means) >= 2 else None


def candidate_trades(panel, cand, end_q):
    """등록된 후보의, 시작일 00:00 UTC 이후 사건에서 나온 끝난 거래. 등록 때 고정한 정의(문턱·비용)만 쓴다."""
    if cand.get("engine") != ENGINE:
        raise RuntimeError("후보 %s 는 틀 %s 로 등록됐고 지금 코드는 틀 %s 다 — 채점하지 않는다" % (cand.get("id"), cand.get("engine"), ENGINE))
    rows = build_trades(panel, day_q(cand_start(cand)), end_q, cand["move"], cand["vol_x"], cand["cost"], cand["cost_other"],
                        only=(cand["event"], cand["hold"]))
    cond = cand.get("cond")
    if cond:
        rows = [t for t in rows if SS.passes(t, cond)]
    return [{k: t[k] for k in ("market", "entry_q", "exit_q", "day", "gross", "net", "move")} for t in rows]


def merge_trades(old_rows, new_rows):
    """장부에 새 거래만 덧붙인다 → (합친 목록, 새로 붙은 수, 값이 달라진 거래 표시)."""
    by_key = {(r["market"], r["entry_q"]): r for r in old_rows}
    added, mismatch = 0, []
    for r in new_rows:
        key = (r["market"], r["entry_q"])
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = r
            added += 1
        elif abs(prev["net"] - r["net"]) > 1e-9:
            mismatch.append("%s %s" % (r["market"][4:], r["day"]))
    return sorted(by_key.values(), key=lambda t: (t["entry_q"], t["market"])), added, mismatch


def decide_once(entry, cand, run_day):
    """30건·10일이 처음 채워지는 거래까지로 한 번만 판정. 기한(기본 12개월)이 지나도록 못 채우면 EXPIRED(표본 부족 종료, 탈락 아님).
    후보에 verdict="ex_best_day" 가 있으면 '최고 수익일 하루를 빼도 날짜 가중 평균 > 0' 을 더 본다. 이미 판정이 있으면 그대로."""
    if entry.get("decision"):
        return entry
    days = set()
    for k, t in enumerate(entry["rows"], 1):
        days.add(t["day"])
        if k >= FWD_MIN_N and len(days) >= FWD_MIN_DAYS:
            st = SS.cluster_stats(entry["rows"][:k])
            ok, stats = st["net"] > 0 and st["daymean"] > 0, {x: st[x] for x in ("n", "days", "net", "t", "daymean")}
            if cand.get("verdict") == "ex_best_day":
                stats["ex_best_day"] = ex_best_day(entry["rows"][:k])
                ok = ok and stats["ex_best_day"] is not None and stats["ex_best_day"] > 0
            entry["decision"] = {"verdict": "PASS" if ok else "FAIL", "decided_on": run_day, "trades": k, "stats": stats}
            return entry
    age = (dt.date.fromisoformat(run_day) - dt.date.fromisoformat(cand_start(cand))).days
    if age > cand.get("expire_days", FWD_EXPIRE_DAYS):
        entry["decision"] = {"verdict": "EXPIRED", "decided_on": run_day, "trades": len(entry["rows"]), "stats": None}
    return entry


# ---------------------------------------------------------------------------------------------------------------------
def run(start_day, end_day, cut_day, markets, fetch=None, log=print):
    """탐색 한 번 → (통과 목록, 수치, 보고 줄들, 거래 목록)."""
    lines = []

    def out(s):
        lines.append(s)
        log(s)

    start_q, end_q = day_q(start_day), day_q(end_day)
    out("DISCOVER %s | 틀 %s%s | %d종목 15분봉 | %s ~ %s | TRAIN < %s <= HOLDOUT" % (
        end_day, ENGINE, "" if FRAME_APPROVED else " (등록 잠김)", len(markets), start_day, q_day(end_q - 1), cut_day))
    out("대상: " + ", ".join(m[4:] for m in markets))               # 기록만 보고도 어떤 종목으로 돌렸는지 알 수 있게
    panel = load_panel(start_q - WARMUP_Q, end_q, markets, fetch, log=log)
    trades = build_trades(panel, start_q, end_q)
    out("거래 %d건" % len(trades))
    passed, counts = search(trades, cut_day, out)
    out("훑은 규칙 %d개 → TRAIN 선별 %d개 → HOLDOUT 통과 %d개" % (counts["scanned"], counts["selected"], counts["passed"]))
    return passed, counts, lines, trades


def repro_lines(trades):
    """고문 수치와 나란히 → (보고 줄들, 어긋난 항목 수). 건수는 정확히, 평균은 REPRO_TOL 이내여야 맞은 것."""
    rows = [t for t in trades if REPRO_START <= t["day"] < REPRO_END]
    lines = ["재현 대조 — 진입일 %s 이상 %s 미만, 앞 구간 < %s <= 뒤 구간 (건수는 정확히, 평균은 0.001%%p 이내)" % (REPRO_START, REPRO_END, REPRO_CUT)]
    bad = 0

    def pair(sel):
        a = [t["net"] for t in sel if t["day"] < REPRO_CUT]
        b = [t["net"] for t in sel if t["day"] >= REPRO_CUT]
        return (len(a), mean(a) if a else 0.0), (len(b), mean(b) if b else 0.0)

    def same(got, ref):
        return all(g[0] == r[0] and abs(g[1] - r[1]) <= REPRO_TOL + 5e-6 for g, r in zip(got, ref))   # 고문 값은 0.001% 자리에서 반올림돼 있다

    def show(x):
        return "%d건 %+.3f%% | %d건 %+.3f%%" % (x[0][0], 100 * x[0][1], x[1][0], 100 * x[1][1])

    for (event, hold), ref in ADVISOR_REF.items():
        got = pair([t for t in rows if t["event"] == event and t["hold"] == hold])
        ok = same(got, ref)
        bad += not ok
        lines.append("  %-8s %dh  여기 %s   고문 %s   %s" % (event, hold // BARS_H, show(got), show(ref), "일치" if ok else "불일치"))
    off = []
    for coin, ref in sorted(ADVISOR_SURGE_1H.items()):
        sel = [t for t in rows if t["event"] == "SURGE" and t["hold"] == BARS_H and t["market"] == "KRW-" + coin]
        got = (sum(1 for t in sel if t["day"] < REPRO_CUT), sum(1 for t in sel if t["day"] >= REPRO_CUT))
        if got != ref:
            off.append("%s %d/%d(고문 %d/%d)" % (coin, got[0], got[1], ref[0], ref[1]))
    bad += len(off)
    lines.append("  급등 1h 코인별 건수: %d종목 중 불일치 %d%s" % (len(ADVISOR_SURGE_1H), len(off), " — " + ", ".join(off) if off else ""))
    name, op, thr = ADVISOR_ALT["cond"]
    got = pair([t for t in rows if t["event"] == "CRASH" and t["hold"] == BARS_H and SS.passes(t, (None, name, op, thr))])
    ok = same(got, ADVISOR_ALT["ref"])
    bad += not ok
    lines.append("  알트 동반 급락 (급락 1h, %s %s %g)  여기 %s   고문 %s   %s" % (name, op, thr, show(got), show(ADVISOR_ALT["ref"]), "일치" if ok else "불일치"))
    lines.append("결과: " + ("전부 일치" if not bad else "어긋난 항목 %d개" % bad))
    return lines, bad


def repro(markets=None, fetch=None, log=print):
    """고문 정의 그대로 한 번 돌린다 → (보고 줄들, 어긋난 항목 수). 봉은 2022-12-20 00:00 UTC 부터 받는다(고문의 첫 봉)."""
    markets = tuple(markets or REPRO_UNIVERSE)
    start_q, end_q = day_q(REPRO_DATA_START), day_q(REPRO_END)
    log("REPRO | 틀 %s | %d종목 15분봉 | 봉 %s ~ , 진입일 %s ~ %s" % (ENGINE, len(markets), REPRO_DATA_START, REPRO_START, q_day(end_q - 1)))
    panel = load_panel(start_q, end_q + BARS_D + 2, markets, fetch, log=log)   # 마지막 진입의 청산 봉까지
    trades = build_trades(panel, start_q, end_q)                                # 첫 봉부터 순서대로 — 앞 거래가 뒤 신호를 막는 것까지 같게
    lines, bad = repro_lines(trades)
    for ln in lines:
        log(ln)
    return lines, bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--repro", action="store_true", help="고문 설정(2023–24 / 2025 이후)으로 돌려 고문 수치와 대조")
    ap.add_argument("--days", type=int, default=HISTORY_DAYS)
    ap.add_argument("--markets", help="쉼표로 구분 (점검·재현용). 기본: 재현은 REPRO_UNIVERSE, 그 밖은 HOLDOUT 시작일 기준 상위 30종목")
    ap.add_argument("--today", help="UTC 날짜 YYYY-MM-DD (기본은 오늘)")
    a = ap.parse_args(argv)
    today = a.today or dt.datetime.now(UTC).date().isoformat()
    if a.repro:
        markets = tuple(a.markets.split(",")) if a.markets else REPRO_UNIVERSE
        if not markets:
            print("재현에 쓸 고문의 고정 20종목이 없습니다(REPRO_UNIVERSE 비어 있음). --markets 로 주거나 목록을 넣은 뒤 실행하세요.")
            return 2                                                # 돌지 않은 것이 성공으로 보이지 않게 (0 = 실제로 돌았고 맞았다)
        _lines, bad = repro(markets, log=lambda s: print(s, flush=True))
        return 1 if bad else 0
    custom = bool(a.markets) or a.days != HISTORY_DAYS
    start_day = (dt.date.fromisoformat(today) - dt.timedelta(days=a.days)).isoformat()
    cut_day = (dt.date.fromisoformat(start_day) + dt.timedelta(days=int(TRAIN_FRAC * a.days))).isoformat()
    if a.markets:
        markets = tuple(a.markets.split(","))
    else:
        markets = pick_universe(cut_day)
        print("대상 %d종목 (%s 이전 %d일 거래대금 기준): %s" % (len(markets), cut_day, LIQ_DAYS, ", ".join(m[4:] for m in markets)), flush=True)
    passed, counts, lines, trades = run(start_day, today, cut_day, markets, log=lambda s: print(s, flush=True))
    tail = []
    out = Path(a.out)
    path = out / "registry.json"
    registry = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    new = register(registry, passed, today, markets)
    for c in new:
        tail.append("후보 %s: %s (HOLDOUT 일 단위 하한 %s)" % (c["id"], describe(c), _pct(c["holdout"]["day_low"])))
    if not new:
        tail.append("새로 등록할 후보 없음")
    wrote = []
    if a.register and not custom:
        out.mkdir(parents=True, exist_ok=True)
        if new and FRAME_APPROVED:
            path.write_text(json.dumps(registry + new, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            wrote = [c["id"] for c in new]
            tail.append("등록함: %s" % ", ".join(wrote))
        elif new:
            tail.append("등록하지 않음: 틀이 아직 잠겨 있음(FRAME_APPROVED=False)")
        log_path = out / "scan_log.json"
        history = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
        history.append(dict(counts, run=today, engine=ENGINE, markets=len(markets), start=start_day, cut=cut_day, registered=wrote,
                            locked=not FRAME_APPROVED))
        log_path.write_text(json.dumps(history, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (out / "DISCOVERY.md").write_text("# 후보 탐색 기록\n\n자동으로 만들어지는 파일입니다. 마지막 실행 %s (UTC). 실행별 훑은 수는 scan_log.json 에 쌓입니다.\n\n```\n%s\n```\n" % (
            today, "\n".join(lines + tail)), encoding="utf-8")
    elif a.register and new:
        tail.append("등록하지 않음: 점검용 범위로 돌린 결과")
    print("\n".join(tail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
