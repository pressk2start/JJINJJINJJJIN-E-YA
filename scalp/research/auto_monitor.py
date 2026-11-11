#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_monitor — 공개 시세만으로 (1) 매일 현재 상황을 수집하고 (2) 고정된 규칙의 전진 성적을 누적한다.
주문 없음 · API 키 없음 · 표준 라이브러리만 사용. GitHub Actions(.github/workflows/auto-monitor.yml)가 실행한다.

여기서 하는 일은 "새 규칙 찾기"가 아니다. 2026-11-10 에 고정한 규칙을 그 뒤 새로 생긴 시세에만 적용해 맞는지 본다.
규칙·임계값을 결과를 보고 바꾸지 않는다(바꾸면 전진 검증이 아니다). 새 가설은 따로 등록해 FORWARD_START 를 새로 잡는다.

고정 규칙
  H3   BTC·ETH: 직전 완료 일봉(UTC) 종가 > 그 날 포함 최근 50개 종가 평균 -> 보유, 아니면 현금.  (상태·전환만 보고)
  CR   급락 반등(고문 H26): KRW-BTC/ETH/XRP/SOL 5분봉. 종가가 12봉(1시간) 전 종가 대비 -4% 이하가 된 첫 봉 -> 다음 봉 시가
       매수, 12봉(60분) 뒤 시가 매도. 코인당 동시에 1건. 봉이 빠진 구간은 건너뜀. 비용 편도 BTC/ETH 0.07%, XRP/SOL 0.10%.
       전진 판정: 30건 이상 & 10일 이상 쌓이면 평균 순수익 > 0 이고 날짜 가중 평균 > 0 일 때만 PASS(봉 기준). 그 전에는 표본 부족.
       봉 기준 PASS 는 실제 호가 체결을 확인한 것이 아니다(급락 순간의 호가·장애는 섀도 기록기로 따로 확인).
  SURGE BTC 주간 급등(관찰, 판정 없음): 월요일 00:00 UTC 에 직전 7일 수익률(일봉 종가) >= +7.45% 이면 그 주 BTC·ETH 수익률
       (월요일 시가 -> 다음 월요일 시가, 왕복 0.14%)을 기록해 전체 주와 비교한다.

실행
  python3 auto_monitor.py daily            지난 24시간에 끝난 거래와 현재 상태. 텔레그램은 사건(H3 전환·CR 발생·SURGE 조건 변화·조회 실패)이 있을 때만.
  python3 auto_monitor.py weekly           FORWARD_START 이후 누적 성적. 텔레그램은 항상 한 번.
  옵션: --out 파일  --no-telegram  --now 2026-11-12T00:12 (UTC, 점검용)  --start 2026-10-01T00:00 (UTC, 점검용: 전진 시작 시각 대체)
환경변수(선택): TELEGRAM_TOKEN|TG_TOKEN, TG_CHATS|TELEGRAM_CHAT_ID|TG_CHAT. 거래소 키는 읽지 않는다.
"""
import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from statistics import mean, median

UPBIT = "https://api.upbit.com"
UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))
FORWARD_START = "2026-11-11T00:00"          # UTC. 규칙 고정(2026-11-10) 뒤의 첫 시각. 바꾸지 않는다.
H3_MARKETS = ("KRW-BTC", "KRW-ETH")
H3_N = 50
CR_COST = {"KRW-BTC": 0.0007, "KRW-ETH": 0.0007, "KRW-XRP": 0.0010, "KRW-SOL": 0.0010}
CR_DROP, CR_LOOK, CR_HOLD, CR_BAR = -0.04, 12, 12, 5
CR_MIN_N, CR_MIN_DAYS = 30, 10
SURGE_THR, SURGE_COST = 0.0745, 0.0007
_last_call, _gate = [0.0], threading.Lock()                 # 전체 호출 간격 0.13초(초당 8회 이하)


# ---------------------------------------------------------------------------------------------------------------- 수집
def http_get(url, tries=6, timeout=20, raw=False):
    last = "?"
    for k in range(tries):
        with _gate:
            wait = 0.13 - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "auto-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            return body.decode("utf-8", "replace") if raw else json.loads(body)
        except urllib.error.HTTPError as exc:
            last = "HTTP %s" % exc.code
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except Exception as exc:                                  # 시간 초과 등
            last = type(exc).__name__
        time.sleep(1.5 * (k + 1))
    raise RuntimeError("조회 실패 (%s) %s" % (last, url.split("?")[0]))


def parse_utc(text):
    return dt.datetime.strptime(text[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)


def to_min(t):
    return int(t.timestamp() // 60)


def fetch_5m(market, start, end):
    """[start, end) 의 완료된 5분봉 -> [(분 단위 시각, 시가, 종가)] 시간순. 진행 중인 봉은 버린다."""
    got, to = {}, end
    while True:
        rows = http_get("%s/v1/candles/minutes/%d?market=%s&count=200&to=%s" % (
            UPBIT, CR_BAR, market, urllib.parse.quote(to.strftime("%Y-%m-%dT%H:%M:%SZ"))))
        if not rows:
            break
        for r in rows:
            got[r["candle_date_time_utc"][:16]] = (float(r["opening_price"]), float(r["trade_price"]))
        oldest = parse_utc(min(r["candle_date_time_utc"] for r in rows))
        if oldest <= start or len(rows) < 200 or oldest >= to:
            break
        to = oldest
    lo, hi = to_min(start), to_min(end) - CR_BAR                  # 시작 시각이 hi 보다 늦은 봉은 아직 진행 중
    bars = sorted((to_min(parse_utc(k)), v[0], v[1]) for k, v in got.items())
    return [b for b in bars if lo <= b[0] <= hi]


def fetch_days(market, need, now, keep_today=False):
    """완료된 일봉(UTC) 최근 need 개 -> [(날짜, 시가, 종가)] 시간순. 오늘(진행 중) 봉은 버린다.
    keep_today=True 면 오늘 봉도 붙인다(시가는 이미 확정 — 종가는 쓰면 안 된다)."""
    got, to, today = {}, None, now.strftime("%Y-%m-%d")
    while len(got) < need + 1:
        url = "%s/v1/candles/days?market=%s&count=200" % (UPBIT, market)
        if to:
            url += "&to=" + urllib.parse.quote(to + "T00:00:00Z")
        rows = http_get(url)
        if not rows:
            break
        for r in rows:
            got[r["candle_date_time_utc"][:10]] = (float(r["opening_price"]), float(r["trade_price"]))
        oldest = min(r["candle_date_time_utc"][:10] for r in rows)
        if len(rows) < 200 or oldest == to:
            break
        to = oldest
    rows = [(d, v[0], v[1]) for d, v in sorted(got.items())]
    done = [r for r in rows if r[0] < today][-need:]
    return done + [r for r in rows if r[0] == today] if keep_today else done


# ---------------------------------------------------------------------------------------------------------------- 규칙
def h3_state(days):
    """-> dict(day, close, sma, on, gap, prev_on). days 는 완료 일봉 시간순, 51개 이상."""
    closes = [c for _, _, c in days]
    if len(closes) < H3_N + 1:
        raise RuntimeError("일봉 부족 (%d개)" % len(closes))
    sma = mean(closes[-H3_N:])
    prev = mean(closes[-H3_N - 1:-1])
    return {"day": days[-1][0], "close": closes[-1], "sma": sma, "on": closes[-1] > sma, "gap": closes[-1] / sma - 1,
            "prev_on": closes[-2] > prev}


def cr_events(bars, cost):
    """급락 반등 규칙 -> (끝난 거래 목록, 진행 중 거래 또는 None). bars = [(분, 시가, 종가)] 시간순.
    어느 구간을 셀지는 호출하는 쪽이 entry_min / exit_min 으로 고른다(앞 거래가 뒤 신호를 막는 것은 여기서 처리)."""
    n, done, last, pending = len(bars), [], -1, None
    for i in range(CR_LOOK, n):
        drop = bars[i][2] / bars[i - CR_LOOK][2] - 1
        if drop > CR_DROP:
            continue
        e = i + 1
        if e <= last:                                             # 앞 거래를 아직 들고 있다
            continue
        if e >= n:                                                # 신호 봉이 마지막 완료 봉 -> 진입 대기
            pending = {"signal_min": bars[i][0] + CR_BAR, "drop": drop, "entry": None}
            break
        if bars[e][0] - bars[i - 1][0] != 2 * CR_BAR:
            continue
        x = e + CR_HOLD
        if x >= n:                                                # 진입했고 60분이 아직 안 지났다
            pending = {"signal_min": bars[i][0] + CR_BAR, "drop": drop, "entry": bars[e][1], "entry_min": bars[e][0]}
            break
        if bars[x][0] - bars[e][0] != CR_HOLD * CR_BAR:
            continue
        gross = bars[x][1] / bars[e][1] - 1
        done.append({"entry_min": bars[e][0], "exit_min": bars[x][0], "drop": drop, "entry": bars[e][1], "exit": bars[x][1], "gross": gross,
                     "net": (1 + gross) * (1 - cost) / (1 + cost) - 1})
        last = x
    return done, pending


def surge_weeks(days_by_market, start_day):
    """월요일(UTC)마다 BTC 직전 7일 수익률과 그 주 수익률 -> [dict(monday, r7, surge, net={market: r})].
    시가만 쓰는 날(월요일)은 진행 중인 오늘 봉이어도 된다. 종가를 쓰는 날(전날·8일 전)은 항상 지난 날이다."""
    btc = {d: (o, c) for d, o, c in days_by_market["KRW-BTC"]}
    idx = {m: {d: (o, c) for d, o, c in rows} for m, rows in days_by_market.items()}
    out = []
    for d in sorted(btc):
        day = dt.date.fromisoformat(d)
        if day.weekday() != 0 or d < start_day:
            continue
        p1, p8, nxt = (day - dt.timedelta(1)).isoformat(), (day - dt.timedelta(8)).isoformat(), (day + dt.timedelta(7)).isoformat()
        if p1 not in btc or p8 not in btc:
            continue
        r7 = btc[p1][1] / btc[p8][1] - 1
        net = {}
        for m, tbl in idx.items():
            if d in tbl and nxt in tbl:
                net[m] = tbl[nxt][0] / tbl[d][0] * (1 - SURGE_COST) / (1 + SURGE_COST) - 1
        out.append({"monday": d, "r7": r7, "surge": r7 >= SURGE_THR, "net": net})
    return out


# ---------------------------------------------------------------------------------------------------------------- 보고
def kst(minute):
    return dt.datetime.fromtimestamp(minute * 60, tz=KST).strftime("%m/%d %H:%M")


def pct(x, digits=2):
    return "%+.*f%%" % (digits, 100 * x)


def h3_lines(now, problems):
    lines, events, days_out = [], [], {}
    for m in H3_MARKETS:
        try:
            days = fetch_days(m, 60, now)
            days_out[m] = days
            s = h3_state(days)
            flip = s["on"] != s["prev_on"]
            lines.append("H3 %s: %s (종가 %s, 50일 평균 %s, %s)%s" % (
                m[4:], "보유" if s["on"] else "현금", format(s["close"], ",.0f"), format(s["sma"], ",.0f"), pct(s["gap"], 1),
                "  ← 오늘 전환" if flip else ""))
            if flip:
                events.append("H3 %s %s 전환" % (m[4:], "보유로" if s["on"] else "현금으로"))
        except Exception as exc:
            problems.append("H3 %s: %s" % (m[4:], exc))
    return lines, events, days_out


def run_daily(now, hours=24):
    problems, events = [], []
    lines = ["[자동 점검] %s KST 기준 지난 %d시간" % (now.astimezone(KST).strftime("%m/%d %H:%M"), hours)]
    h3, ev, days = h3_lines(now, problems)
    lines += h3
    events += ev
    since, found = to_min(now - dt.timedelta(hours=hours)), 0
    for m, res in cr_all(now - dt.timedelta(hours=hours + 6), now).items():
        try:
            if isinstance(res, Exception):
                raise res
            done, pending = res
            for t in done:
                if t["exit_min"] < since:                         # 어제 보고에 이미 나간 거래
                    continue
                found += 1
                lines.append("급락 반등 %s: %s 진입(1시간 %s) → 60분 뒤 순수익 %s" % (m[4:], kst(t["entry_min"]), pct(t["drop"], 1), pct(t["net"])))
            if pending:
                found += 1
                lines.append("급락 반등 %s: %s 신호(1시간 %s) → %s" % (
                    m[4:], kst(pending["signal_min"]), pct(pending["drop"], 1), "진행 중" if pending["entry"] else "진입 대기"))
        except Exception as exc:
            problems.append("급락 반등 %s: %s" % (m[4:], exc))
    if found:
        events.append("급락 반등 %d건" % found)
    else:
        lines.append("급락 반등: 발생 없음")
    btc = days.get("KRW-BTC") or []
    if len(btc) >= 9:
        r7, r7_prev = btc[-1][2] / btc[-8][2] - 1, btc[-2][2] / btc[-9][2] - 1
        on, was = r7 >= SURGE_THR, r7_prev >= SURGE_THR
        lines.append("BTC 7일 수익률 %s (급등 기준 %s: %s)" % (pct(r7, 1), pct(SURGE_THR, 2), "충족" if on else "미충족"))
        if on != was:
            events.append("BTC 주간 급등 조건 %s" % ("충족" if on else "해제"))
    if problems:
        lines.append("⚠ " + " / ".join(problems))
    lines.append("요약: " + (", ".join(events) if events else "변화 없음"))
    return "\n".join(lines), bool(events or problems), len(problems) >= len(H3_MARKETS) + len(CR_COST)


def cr_all(start, now):
    """코인 4개를 동시에 받아 규칙을 적용 -> {market: (끝난 거래, 진행 중) 또는 예외}."""
    def one(m):
        try:
            return cr_events(fetch_5m(m, start, now), CR_COST[m])
        except Exception as exc:
            return exc
    with ThreadPoolExecutor(len(CR_COST)) as pool:
        return dict(zip(CR_COST, pool.map(one, CR_COST)))


def cr_summary(trades):
    """trades = [(market, 거래 dict)] -> 통계 dict."""
    nets = [t["net"] for _, t in trades]
    by_day = {}
    for _, t in trades:
        by_day.setdefault(dt.datetime.fromtimestamp(t["entry_min"] * 60, tz=UTC).strftime("%Y-%m-%d"), []).append(t["net"])
    day_means = [mean(v) for v in by_day.values()]
    out = {"n": len(nets), "days": len(by_day), "mean": mean(nets) if nets else None, "median": median(nets) if nets else None,
           "win": sum(x > 0 for x in nets) / len(nets) if nets else None, "day_mean": mean(day_means) if day_means else None,
           "worst": min(nets) if nets else None}
    if out["n"] >= CR_MIN_N and out["days"] >= CR_MIN_DAYS:
        out["verdict"] = "PASS(봉 기준)" if out["mean"] > 0 and out["day_mean"] > 0 else "FAIL"
    else:
        out["verdict"] = "표본 부족 (%d/%d건, %d/%d일)" % (out["n"], CR_MIN_N, out["days"], CR_MIN_DAYS)
    return out


def run_weekly(now, start):
    problems = []
    lines = ["[주간 전진 검증] %s KST · 시작 %s UTC · 규칙 고정" % (now.astimezone(KST).strftime("%m/%d %H:%M"), start.strftime("%Y-%m-%d %H:%M"))]
    h3, _, _ = h3_lines(now, problems)
    lines += h3
    trades, open_now = [], []
    for m, res in cr_all(start - dt.timedelta(hours=6), now).items():
        try:
            if isinstance(res, Exception):
                raise res
            done, pending = res
            trades += [(m, t) for t in done if t["entry_min"] >= to_min(start)]
            if pending:
                open_now.append(m[4:])
        except Exception as exc:
            problems.append("급락 반등 %s: %s" % (m[4:], exc))
    s = cr_summary(trades)
    if s["n"]:
        lines.append("급락 반등 누적: %d건 %d일 | 평균 %s 중앙값 %s 승률 %.0f%% | 날짜 가중 %s | 최악 %s" % (
            s["n"], s["days"], pct(s["mean"]), pct(s["median"]), 100 * s["win"], pct(s["day_mean"]), pct(s["worst"], 1)))
        per = []
        for m in CR_COST:
            v = [t["net"] for mm, t in trades if mm == m]
            if v:
                per.append("%s %s(%d)" % (m[4:], pct(mean(v)), len(v)))
        lines.append("  코인별: " + "  ".join(per))
        for m, t in sorted(trades, key=lambda x: x[1]["entry_min"])[-5:]:
            lines.append("  %s %s 1시간 %s → %s" % (kst(t["entry_min"]), m[4:], pct(t["drop"], 1), pct(t["net"])))
    else:
        lines.append("급락 반등 누적: 0건")
    if open_now:
        lines.append("  진행 중: " + ", ".join(open_now))
    lines.append("  판정: " + s["verdict"])
    try:
        need = (now.date() - start.date()).days + 20
        days = {m: fetch_days(m, need, now, keep_today=True) for m in H3_MARKETS}
        weeks = [w for w in surge_weeks(days, start.strftime("%Y-%m-%d")) if "KRW-BTC" in w["net"]]
        hit = [w for w in weeks if w["surge"]]
        if weeks:
            def avg(rows, m):
                v = [w["net"][m] for w in rows if m in w["net"]]
                return pct(mean(v)) if v else "-"
            lines.append("BTC 주간 급등(관찰): 전체 %d주 BTC %s ETH %s | 급등 뒤 %d주 BTC %s ETH %s" % (
                len(weeks), avg(weeks, "KRW-BTC"), avg(weeks, "KRW-ETH"), len(hit), avg(hit, "KRW-BTC"), avg(hit, "KRW-ETH")))
        else:
            lines.append("BTC 주간 급등(관찰): 끝난 주 없음")
    except Exception as exc:
        problems.append("주간 급등: %s" % exc)
    if problems:
        lines.append("⚠ " + " / ".join(problems))
    return "\n".join(lines), True, len(problems) >= len(CR_COST)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TG_TOKEN") or ""
    chats = os.environ.get("TG_CHATS") or os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TG_CHAT") or ""
    chats = [c.strip() for c in chats.split(",") if c.strip()]
    if not (token and chats):
        return "텔레그램 설정 없음(건너뜀)"
    sent = 0
    for chat in chats:
        try:
            body = urllib.parse.urlencode({"chat_id": chat, "text": text[:3900], "disable_web_page_preview": "true"}).encode()
            urllib.request.urlopen(urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body), timeout=15).read()
            sent += 1
        except Exception as exc:                                  # 토큰이 든 주소를 찍지 않는다
            print("[auto] telegram failed: %s" % type(exc).__name__)
    return "텔레그램 %d/%d 전송" % (sent, len(chats))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("daily", "weekly"))
    ap.add_argument("--out")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--now", help="UTC, 예: 2026-11-12T00:12 (점검용)")
    ap.add_argument("--start", default=FORWARD_START, help="UTC (점검용: 전진 시작 시각 대체)")
    a = ap.parse_args(argv)
    now = parse_utc(a.now) if a.now else dt.datetime.now(UTC)
    start = parse_utc(a.start)
    text, notify, failed = run_daily(now) if a.mode == "daily" else run_weekly(now, start)
    if a.mode == "weekly" and a.start != FORWARD_START:
        text += "\n(점검 실행: 시작 시각을 %s 로 바꿔 돌린 결과 — 전진 성적이 아님)" % a.start
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("```\n%s\n```\n" % text)
    if notify and not a.no_telegram and a.start == FORWARD_START:
        print("[auto] " + send_telegram(text))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
