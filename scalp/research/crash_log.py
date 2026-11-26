# -*- coding: utf-8 -*-
"""전진 조건 기록 (고문 판정 2026-11-22 ⑤, 사용자 지적) — 급락 사건마다 조건 값과 신호 뒤 60분의 움직임을 적는다. 관찰용이다.

추적 장부는 거래마다 신호 낙폭·60분 수익·체결·짝만 남긴다. 사용자 설계(급락 반등 검토안 1~8번과 '볼 값')가 새 자료에서도 쌓이도록,
과거 9년 파일(reports/crash_surge_2026-11/crash9/crash_events_9y.csv.gz)과 같은 정의·같은 열로 사건을 적는다. 1·2·3단계 판정과
등록 규칙에는 쓰지 않는다.

정의 (연구 코드 long/study.py, crash9/crash9.py, rebound/study.py 와 같다 — 같은 값이 나오는지 9년치로 대조했다):
  알트 급락   BTC 를 뺀 고정 19종목의 완료된 15분봉 종가 수익 ≤ −3%. 거래대금 조건 없음. 직전 봉이 바로 앞 15분이어야 하고,
             같은 종목은 앞 급락에서 4봉(60분)이 지나야 다시 센다. 신호 시각 = 그 봉 마감.
  BTC 직접    BTC 의 1시간(4봉) 수익이 −1.821% 이하로 처음 내려간 봉. 그 뒤 4봉은 같은 사건.
  구분        후행 = 알트 급락 봉의 15~60분 전(1~4봉 전)에 BTC 신호. 동반 = 후행이 아니고 급락 봉 때 BTC 1시간 ≤ −1.821%. 알트만 = 그 밖.
  ① 바로      다음 15분봉의 첫 체결가(신호 뒤 15분 안의 첫 5분봉 시가)에 사서 신호 뒤 5·15·30·60분의 5분봉 종가에 판 값(비용 뒤).
  ② 확인      신호 뒤 5분봉 종가가 직전 5분봉 종가보다 높게 마감하면 그다음 5분봉 시가. 45분 안에 없으면 미진입. 청산은 ① 과 같은 시각.
  5분봉       거래가 없어 빠진 5분봉은 직전 30분 안의 마지막 종가로 채운다. 한 봉 안의 고점·저점 순서는 '미상'.
  비용        편도 BTC·ETH 0.07%, 그 밖 0.10%. 추가 슬리피지 0.
"""
import bisect
import csv
import datetime as dt
import os
import statistics as st

BTC, ETH = "KRW-BTC", "KRW-ETH"
BIG = ("KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE", "KRW-ADA")
COST, COST_OTHER = {BTC: 0.0007, ETH: 0.0007}, 0.001
MOVE, TH_BTC, COOL, VOLB = 0.03, -0.01821, 4, 96
A4H, B24, WAIT_BARS = -0.0592, -0.0173, 9
START_DAY = "2026-11-21"                                    # 연구 자료 끝 다음 날 00:00 UTC 부터 적는다
MIN_DAYS = 10                                               # 조건별 표에서 날 수가 이보다 적은 칸은 '표본 부족'
UTC = dt.timezone.utc
COLUMNS = ["사건 ID", "종목", "신호 시각(UTC)", "날", "구분", "BTC 신호 뒤 경과 분", "15분 수익", "거래대금 배수", "같은 봉 BTC 15분 수익", "알트−BTC 15분 차",
           "알트 4시간", "BTC 24시간", "BTC 1시간", "4시간 조건", "BTC24 조건", "조합", "큰 5종목",
           "① 진입가", "① 5분", "① 15분", "① 30분", "① 60분", "15분봉 기준 60분(대조)", "최대 반등", "고점까지 분", "최대 추가 하락", "저점까지 분",
           "고점 뒤 되밀림", "순서", "② 진입", "② 기다린 분", "② 진입가", "② 진입가 ÷ ① 진입가", "② 60분(진입 건)", "② 신호당(미진입 0)",
           "BTC 5분", "BTC 15분", "BTC 30분", "BTC 60분", "ETH 5분", "ETH 15분", "ETH 30분", "ETH 60분"]


def net(m, r):
    c = COST.get(m, COST_OTHER)
    return (1 + r) * (1 - c) / (1 + c) - 1


def idx(b, q):
    i = bisect.bisect_left(b.ts, q)
    return i if i < len(b.ts) and b.ts[i] == q else -1


def close_at(b, q):
    """봉 q 의 종가. 그 봉이 없으면 그 앞 마지막 봉의 종가(거래가 없으면 가격이 그대로였다고 본다)."""
    i = bisect.bisect_right(b.ts, q) - 1
    return b.c[i] if i >= 0 else None


def ret_back(b, i, k):
    p = close_at(b, b.ts[i] - k)
    return b.c[i] / p - 1 if p else None


def alt_signals(b):
    """한 종목의 급락 봉 위치 목록 (조건을 못 채워도 급락이면 다음 4봉의 자리를 차지한다 — 연구와 같다)."""
    out, last = [], -99
    for i in range(VOLB, len(b.ts)):
        if b.ts[i - 1] != b.ts[i] - 1 or b.c[i - 1] <= 0:
            continue
        if b.c[i] / b.c[i - 1] - 1 <= -MOVE and b.ts[i] - last >= COOL:
            last = b.ts[i]
            out.append(i)
    return out


def btc_signals(b):
    """BTC 1시간 수익이 −1.821% 이하로 처음 내려간 봉 번호 목록. 두 봉이 다 있을 때만 수익을 잰다."""
    def r1h(q):
        i, j = idx(b, q), idx(b, q - 4)
        return b.c[i] / b.c[j] - 1 if i >= 0 and j >= 0 and b.c[j] > 0 else None
    out, last = [], -99
    for q in b.ts:
        r, prev = r1h(q), r1h(q - 1)
        if r is not None and r <= TH_BTC and (prev is None or prev > TH_BTC) and q - last >= COOL:
            out.append(q)
            last = q
    return out


class FiveMin(object):
    """5분봉 창고. bars = {종목: {분: [시가, 고가, 저가, 종가]}}. fetch(종목, 덩어리) 를 주면 없는 덩어리(1000분)를 그때 받아 채운다."""
    BLOCK = 1000

    def __init__(self, bars=None, fetch=None):
        self.bars, self.fetch, self.got = bars if bars is not None else {}, fetch, set()

    def _load(self, m, x):
        key = (m, x // self.BLOCK)
        if self.fetch is not None and key not in self.got:
            self.got.add(key)
            self.bars.setdefault(m, {}).update(self.fetch(m, key[1]))

    def raw(self, m, x):
        self._load(m, x)
        return self.bars.get(m, {}).get(x)

    def bar(self, m, x):
        got = self.raw(m, x)
        if got is not None:
            return got
        for y in range(x - 5, x - 35, -5):
            prev = self.raw(m, y)
            if prev is not None:
                return [prev[3]] * 4
        return None


def upbit_fetch5(get, base):
    """FiveMin 에 넣을 받기 함수: (종목, 덩어리) → {분: [시가, 고가, 저가, 종가]}. get(url) 은 JSON 을 돌려주는 조회 함수."""
    def fetch(m, block):
        to = dt.datetime.fromtimestamp((block + 1) * FiveMin.BLOCK * 60, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = {}
        for r in get("%s/v1/candles/minutes/5?market=%s&count=200&to=%s" % (base, m, to.replace(":", "%3A"))) or []:
            t = int(dt.datetime.strptime(r["candle_date_time_utc"][:16], "%Y-%m-%dT%H:%M").replace(tzinfo=UTC).timestamp() // 60)
            if block * FiveMin.BLOCK <= t < (block + 1) * FiveMin.BLOCK:
                out[t] = [float(r["opening_price"]), float(r["high_price"]), float(r["low_price"]), float(r["trade_price"])]
        return out
    return fetch


def path(m, t, five):
    """신호 시각 t(분) 뒤 60분의 움직임 → dict. 5분봉이 모자라면 None."""
    first = next((b for b in (five.raw(m, x) for x in (t, t + 5, t + 10)) if b is not None), None)
    bars = [five.bar(m, t + 5 * j) for j in range(12)]
    pre = five.bar(m, t - 5)
    if first is None or pre is None or any(x is None for x in bars) or first[0] <= 0:
        return None
    e1, last = first[0], bars[11][3]
    hi = max(range(12), key=lambda j: (bars[j][1], -j))
    lo = min(range(12), key=lambda j: (bars[j][2], j))
    after = [bars[j][2] for j in range(hi + 1, 12)]
    e2 = wait = None
    prevc = pre[3]
    for j in range(WAIT_BARS):
        if bars[j][3] > prevc:
            wait, e2 = 5 * (j + 1), bars[j + 1][0]
            break
        prevc = bars[j][3]
    return {"① 진입가": e1, "① 5분": net(m, bars[0][3] / e1 - 1), "① 15분": net(m, bars[2][3] / e1 - 1), "① 30분": net(m, bars[5][3] / e1 - 1),
            "① 60분": net(m, last / e1 - 1), "최대 반등": bars[hi][1] / e1 - 1, "고점까지 분": 5 * hi + 5, "최대 추가 하락": bars[lo][2] / e1 - 1,
            "저점까지 분": 5 * lo + 5, "고점 뒤 되밀림": (min(after) / bars[hi][1] - 1) if after else None,
            "순서": "고점 먼저" if hi < lo else ("저점 먼저" if lo < hi else "미상"),
            "② 진입": int(e2 is not None), "② 기다린 분": wait, "② 진입가": e2, "② 진입가 ÷ ① 진입가": (e2 / e1 - 1) if e2 else None,
            "② 60분(진입 건)": net(m, last / e2 - 1) if e2 else None, "② 신호당(미진입 0)": net(m, last / e2 - 1) if e2 else 0.0}


def pair(m, t, five):
    """같은 시간에 m 을 샀다면: 신호 뒤 첫 5분봉 시가 → 5·15·30·60분 뒤 종가 (비용 뒤). 자료가 없으면 None 넷."""
    bb = [five.bar(m, t + 5 * j) for j in (0, 2, 5, 11)]
    if any(x is None for x in bb) or bb[0][0] <= 0:
        return [None] * 4
    return [net(m, x[3] / bb[0][0] - 1) for x in bb]


def event_id(m, t):
    return "%s_%s" % (m[4:], dt.datetime.fromtimestamp(t * 60, tz=UTC).strftime("%Y%m%dT%H%M"))


def rows(panel, five, start_q, end_q, known=(), missed=None):
    """사건 봉이 [start_q, end_q) 이고 신호 뒤 60분이 end_q 안에 다 들어온 사건의 줄 목록(시각·종목 순). known 에 있는 ID 는 건너뛴다.
    panel = {종목: 15분봉(Bars — ts, o, c, v)}. BTC 가 있어야 한다. 5분봉이 모자란 사건은 넣지 않는다(다음 실행에서 다시 본다) —
    missed(목록)를 주면 그런 사건의 ID 를 넣는다."""
    btc = panel[BTC]
    sig_btc = btc_signals(btc)
    sig_set = set(sig_btc)
    events = [(BTC, idx(btc, q)) for q in sig_btc] + [(m, i) for m in sorted(panel) if m != BTC for i in alt_signals(panel[m])]
    out = []
    for m, i in events:
        b = panel[m]
        q = b.ts[i]
        t = (q + 1) * 15
        if not (start_q <= q and q + 4 < end_q) or event_id(m, t) in known or i < VOLB:
            continue
        i1 = idx(b, q + 1)
        if i1 < 0 or b.o[i1] <= 0:                              # 다음 15분봉에 거래가 없으면 사건으로 적지 않는다(연구와 같다)
            continue
        got = path(m, t, five)
        if got is None:
            if missed is not None:
                missed.append(event_id(m, t))
            continue
        r15 = b.c[i] / b.c[i - 1] - 1
        ref = st.median(b.v[i - VOLB:i])
        bi = idx(btc, q)
        has_btc = bi >= VOLB
        btc15 = btc.c[bi] / btc.c[bi - 1] - 1 if has_btc and btc.c[bi - 1] > 0 else None
        btc1h, btc24 = (ret_back(btc, bi, 4), ret_back(btc, bi, 96)) if has_btc else (None, None)
        r4 = ret_back(b, i, 16)
        if m == BTC:
            kind, since = "BTC 직접", 0
        else:
            lag = next((d for d in range(1, 5) if (q - d) in sig_set), None)
            kind = "후행" if lag else ("동반" if btc1h is not None and btc1h <= TH_BTC else "알트만")
            since = 15 * lag if lag else (0 if q in sig_set else None)
        c4, cb = bool(m != BTC and r4 is not None and r4 <= A4H), bool(m != BTC and btc24 is not None and btc24 <= B24)
        j = bisect.bisect_right(b.ts, q + 4) - 1
        row = {"사건 ID": event_id(m, t), "종목": m[4:], "신호 시각(UTC)": dt.datetime.fromtimestamp(t * 60, tz=UTC).strftime("%Y-%m-%d %H:%M"),
               "날": dt.datetime.fromtimestamp((q + 1) * 900, tz=UTC).strftime("%Y-%m-%d"), "구분": kind, "BTC 신호 뒤 경과 분": since,
               "15분 수익": r15, "거래대금 배수": b.v[i] / ref if ref > 0 else None, "같은 봉 BTC 15분 수익": btc15,
               "알트−BTC 15분 차": (r15 - btc15) if (m != BTC and btc15 is not None) else None,
               "알트 4시간": r4, "BTC 24시간": btc24, "BTC 1시간": btc1h, "4시간 조건": int(c4), "BTC24 조건": int(cb), "조합": int(c4 and cb),
               "큰 5종목": int(c4 and cb and m in BIG), "15분봉 기준 60분(대조)": net(m, b.c[j] / b.o[i1] - 1)}
        row.update(got)
        own = [row["① 5분"], row["① 15분"], row["① 30분"], row["① 60분"]]
        for name, vals in (("BTC", own if m == BTC else pair(BTC, t, five)), ("ETH", own if m == ETH else pair(ETH, t, five))):
            for lab, v in zip(("5분", "15분", "30분", "60분"), vals):
                row["%s %s" % (name, lab)] = v
        out.append(row)
    out.sort(key=lambda r: (r["신호 시각(UTC)"], r["종목"]))
    return out


def text(v):
    return "" if v is None else ("%.6f" % v if isinstance(v, float) else str(v))


def load(path_):
    """적어 둔 줄 → [dict(열: 문자열)]. 파일이 없으면 빈 목록."""
    try:
        with open(path_, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def append(path_, new_rows):
    """새 줄만 뒤에 붙인다. 이미 있는 줄은 고치지 않고, 같은 사건 ID 는 다시 적지 않는다 → 붙인 줄 수."""
    have = {r["사건 ID"] for r in load(path_)}
    add = []
    for r in new_rows:
        if r["사건 ID"] not in have:
            have.add(r["사건 ID"])
            add.append(r)
    exists = os.path.exists(path_)
    if add or not exists:
        with open(path_, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
            if not exists:
                w.writeheader()
            for r in add:
                w.writerow({k: text(r.get(k)) for k in COLUMNS})
    return len(add)


# ---------------------------------------------------------------------------------------------------------------------
# 조건별 전진 표 (관찰용). 구간 경계는 과거 보고서 값으로 고정한다 — 새 자료로 다시 나누지 않는다.
REL_CUTS = {"2번": (-0.029, -0.018), "4번": (-0.040, -0.031)}     # 6번 알트−BTC 15분 차의 3분위 경계 (rebound/report.md 5절)
MULT_CUTS = (1.5, 3.0, 6.0)                                      # 7번 거래대금 배수 구간
KEYS = ("1", "2", "2x3", "3", "4", "4x3", "co", "lag", "alone", "5a", "5b", "6-2-lo", "6-2-mid", "6-2-hi", "6-4-lo", "6-4-mid", "6-4-hi",
        "7-2-a", "7-2-b", "7-2-c", "7-2-d", "7-4-a", "7-4-b", "7-4-c", "7-4-d", "8-1-up", "8-1-dn", "8-2-up", "8-2-dn", "8-3-up", "8-3-dn", "8-4-up", "8-4-dn",
        "big5", "rest14", "combo", "combo-big5", "4h-only", "b24-only", "neither", "day-first", "day-repeat")   # 칸의 고정 이름 — 과거 값과 맞출 때 쓴다(라벨에는 건수가 들어가 달라질 수 있다)


def _f(r, k):
    v = r.get(k)
    return float(v) if v not in (None, "") else None


def _days(rs, key):
    """같은 신호 시각의 사건을 먼저 평균 → 날마다 평균 → [일별 값]."""
    g = {}
    for r in rs:
        v = _f(r, key)
        if v is not None:
            g.setdefault((r["날"], r["신호 시각(UTC)"]), []).append(v)
    d = {}
    for (day, _t), v in g.items():
        d.setdefault(day, []).append(sum(v) / len(v))
    return [sum(v) / len(v) for v in d.values()]


def _cell(label, rs, cols=("① 5분", "① 15분", "① 30분", "① 60분")):
    days = len({r["날"] for r in rs})
    out = {"label": label, "n": len(rs), "days": days, "ok": days >= MIN_DAYS, "means": [None] * 4, "up": None, "down": None}
    if out["ok"]:
        out["means"] = [(lambda d: sum(d) / len(d) if d else None)(_days(rs, c)) if c else None for c in cols]
        ups, downs = [_f(r, "최대 반등") for r in rs], [_f(r, "최대 추가 하락") for r in rs]
        out["up"], out["down"] = st.median(ups), st.median(downs)
    return out


def table(all_rows):
    """적어 둔 줄 → 조건별 표의 칸 목록 [{label, n, days, ok, means[5·15·30·60분], up, down}]. 날 수가 MIN_DAYS 미만이면 ok=False(표본 부족)."""
    alt = [r for r in all_rows if r["구분"] != "BTC 직접"]
    b1h, b24, mult = (lambda r: _f(r, "BTC 1시간")), (lambda r: _f(r, "BTC 24시간")), (lambda r: _f(r, "거래대금 배수"))
    two = [r for r in alt if b1h(r) is not None and b1h(r) <= TH_BTC]
    four = [r for r in alt if b1h(r) is not None and b1h(r) > TH_BTC]
    three = [r for r in alt if r["구분"] == "후행"]
    x3 = lambda rs: [r for r in rs if mult(r) is not None and mult(r) >= 3.0]
    out = [_cell("1번 BTC 직접 (1시간 −1.821% 이하로 처음 내려간 봉)", [r for r in all_rows if r["구분"] == "BTC 직접"]),
           _cell("2번 알트 급락 + 그때 BTC 1시간 −1.821% 이하", two), _cell("2번 + 거래대금 3배 이상", x3(two)),
           _cell("3번 BTC 신호 뒤 15~60분의 알트 급락", three),
           _cell("4번 BTC 조건 없는 알트 급락 (BTC 1시간 > −1.821%)", four), _cell("4번 + 거래대금 3배 이상", x3(four)),
           _cell("구분: 동반", [r for r in alt if r["구분"] == "동반"]), _cell("구분: 후행", three), _cell("구분: 알트만", [r for r in alt if r["구분"] == "알트만"]),
           _cell("5번 알트 급락 전체 — ① 바로 진입", alt),
           _cell("5번 알트 급락 전체 — ② 반등 확인 뒤 진입 (미진입 %d건은 0)" % sum(1 for r in alt if r["② 진입"] == "0"), alt, (None, None, None, "② 신호당(미진입 0)"))]
    for name, rs in (("2번", two), ("4번", four)):
        lo, hi = REL_CUTS[name]
        rel = lambda r: _f(r, "알트−BTC 15분 차")
        out += [_cell("6번 %s 중 BTC 보다 가장 더 빠짐 (차 ≤ %+.1f%%)" % (name, 100 * lo), [r for r in rs if rel(r) is not None and rel(r) <= lo]),
                _cell("6번 %s 중 중간" % name, [r for r in rs if rel(r) is not None and lo < rel(r) <= hi]),
                _cell("6번 %s 중 덜 빠짐 (차 > %+.1f%%)" % (name, 100 * hi), [r for r in rs if rel(r) is not None and rel(r) > hi])]
    for name, rs in (("2번", two), ("4번", four)):
        a, b, c = MULT_CUTS
        out += [_cell("7번 %s 중 거래대금 %g배 미만" % (name, a), [r for r in rs if mult(r) is not None and mult(r) < a]),
                _cell("7번 %s 중 %g~%g배" % (name, a, b), [r for r in rs if mult(r) is not None and a <= mult(r) < b]),
                _cell("7번 %s 중 %g~%g배" % (name, b, c), [r for r in rs if mult(r) is not None and b <= mult(r) < c]),
                _cell("7번 %s 중 %g배 이상" % (name, c), [r for r in rs if mult(r) is not None and mult(r) >= c])]
    for name, rs in (("1번", [r for r in all_rows if r["구분"] == "BTC 직접"]), ("2번", two), ("3번", three), ("4번", four)):
        out += [_cell("8번 %s 중 BTC 24시간 수익 > 0" % name, [r for r in rs if b24(r) is not None and b24(r) > 0]),
                _cell("8번 %s 중 BTC 24시간 수익 ≤ 0" % name, [r for r in rs if b24(r) is not None and b24(r) <= 0])]
    big = {m[4:] for m in BIG}
    c4, cb = (lambda r: r["4시간 조건"] == "1"), (lambda r: r["BTC24 조건"] == "1")
    out += [_cell("큰 5종목의 급락 (ETH·XRP·SOL·DOGE·ADA)", [r for r in alt if r["종목"] in big]), _cell("나머지 14종목의 급락", [r for r in alt if r["종목"] not in big]),
            _cell("4시간 조건 + BTC24 조건 (등록 규칙의 조합)", [r for r in alt if c4(r) and cb(r)]),
            _cell("조합 중 큰 5종목", [r for r in alt if c4(r) and cb(r) and r["종목"] in big]),
            _cell("4시간 조건만 (BTC24 조건은 못 채움)", [r for r in alt if c4(r) and not cb(r)]),
            _cell("BTC24 조건만 (4시간 조건은 못 채움)", [r for r in alt if cb(r) and not c4(r)]),
            _cell("둘 다 못 채움", [r for r in alt if not c4(r) and not cb(r)])]
    seen, first, again = set(), [], []                                # 같은 종목의 그날(UTC) 첫 급락과 그 뒤의 급락 (고문 판정 2026-11-25 ①). 날·종목으로만 가른다
    for r in sorted(alt, key=lambda r: (r["날"], r["종목"], r["신호 시각(UTC)"])):
        (again if (r["날"], r["종목"]) in seen else first).append(r)
        seen.add((r["날"], r["종목"]))
    out += [_cell("그 종목의 그날(UTC) 첫 급락", first), _cell("그 종목의 그날 두 번째 이후 급락", again)]
    if len(out) != len(KEYS):
        raise RuntimeError("조건별 표의 칸 수가 이름 목록과 다르다")
    for key, c in zip(KEYS, out):
        c["key"] = key
    return out
