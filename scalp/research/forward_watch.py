#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""forward_watch — 등록된 후보를 '등록일 이후에 생긴 자료' 로만 채점해 장부에 쌓는 자동 추적기.
(공개 시세만, 키·주문 없음, 표준 라이브러리. GitHub Actions 예약 실행용 — 거래 서버에서 돌리지 않는다.)

왜 (2026-11-11): 이번 주에 조건·규칙 1,879개를 봤고(H26 146 · 고문 15분봉 1,509 · H27 224) 앞 구간에서 좋아 보인 것 중 뒤 구간에서
    살아남은 것은 0 이었다. '찾기' 를 자동으로 돌리면 이런 가짜 발견이 매일 나온다. 자동화할 것은 찾기가 아니라
    '찾아 둔 후보를 새 자료로 검증하기' 다. 사람이 PC 에서 가끔 돌리던 H2·H3 추적을 그대로 옮긴 것이 1단계.

실행할 때마다 하는 일
  1. 공개 일봉을 새로 받는다 (업비트 KRW 전 종목 200일, 바이낸스 USDT 일봉 200일, H3 는 BTC·ETH).
  2. 후보마다 등록일 이후 기간을 다시 계산해 장부(forward/ledger.json)에 '새 기간만' 덧붙인다. 이미 적힌 기간은 고치지 않는다.
     다시 계산한 값이 장부와 다르면 고치지 않고 경고로 남긴다 (시세 정정·상장폐지 감지).
  3. 판정 시점이 된 후보는 미리 정한 규칙으로 '한 번만' 판정해 장부에 적는다. 그 뒤로는 추적만 한다.
  4. 현황(forward/STATUS.md)을 다시 쓴다. 같은 날 다시 돌려도 결과 파일은 같다.

등록된 후보 (규칙은 각 스크립트에 고정돼 있고 여기서는 불러 쓰기만 한다)
  H2_kimchi  daily_factor_test.kimchi_periods (김치 프리미엄 되돌림, 3일 보유). 추적 시작 2026-10-23.
             3일 격자는 사용자 PC 실행과 같게 맞춘다: PC 출력의 'HOLDOUT 61기간' 과 '11/02 시점 FORWARD 2기간' 에서 역산하면
             결정일은 2026-10-24, 27, 30, … (매수일 10-25, 28, 31, …) 이다.
             판정: forward 첫 20기간으로 한 번만 — daily_factor_test.verdict(…, 20) 그대로
             (비용 후 평균 > 0 · 동일비중 대비 초과 > 0 · 초과수익 부트스트랩 90% 하한 > 0 · 누적 > BTC 누적).
             원 등록(2026-10-23)은 '20기간 이상이 되면 판정' 이었다. 매주 다시 보다가 통과한 주에 멈추는 일을 막으려고
             '첫 20기간, 한 번' 으로 좁힌다 (2026-11-11 고정. 그때까지 본 forward 수치는 11/02 의 2기간뿐).
  H3_trend   trend_paper (BTC·ETH, 전일 종가 > 50일 평균이면 보유). 시작 2026-10-24.
             통과/탈락 판정은 없다 — 신호 상태와 가상 성적을 기록만 한다 (근거는 9년 이력 검증, trend_history.py).
             H3 계산은 이 함수 하나만 쓴다. auto_monitor.h3_state 는 엔진에서 부르지 않고, 두 계산이 같다는 것만 테스트로 묶는다.
             trend_paper.py 는 H3 실행기가 서버에서 불러 쓰는 운영 코드다(고문 2026-11-11) — 연구 변경에 섞어 고치지 않고,
             고칠 때는 실행기처럼 검토를 받는다. 파일 해시를 테스트에 고정해 둔다.
  CR_rebound 급락 반등 — 고문 세션이 2026-11-10 에 고정한 규칙. 계산은 auto_monitor.cr_events 를 그대로 부른다.
             KRW-BTC/ETH/XRP/SOL 5분봉. 종가가 1시간 전 종가 대비 −4% 이하가 된 첫 봉 → 다음 봉 시가 매수, 60분 뒤 시가 매도.
             코인당 동시에 1건. 비용 편도 BTC/ETH 0.07%, XRP/SOL 0.10%. 추적 시작 2026-11-11 00:00 UTC.
             판정: 30건 이상·10일 이상이 처음 채워지는 시점까지의 거래로 한 번만 — 평균 순수익 > 0 이고 날짜 가중 평균 > 0 이면
             PASS, 아니면 FAIL (auto_monitor.cr_summary 그대로). 봉 기준 PASS 는 실제 호가 체결을 확인한 것이 아니다.
             등록 규칙(고문 확정 2026-11-11): close[i]/close[i-12] − 1 ≤ −4% 이고 t[i] − t[i-12] 가 정확히 60분일 때만 신호.
             규칙 파일은 auto_monitor.py SHA-256 b27aabc8…cdcd 로 테스트에 고정한다 (고문 원본 94a65c59…854f + '정확히 60분' 3줄).
             경위: 고문 원본은 봉 12개 전과 비교해서, 봉이 빠진 구간을 건너뛴 하락도 사건으로 잡았다 (예: 2026-11-09 업비트 점검
             02:00~07:00 KST 직후 ETH, 355분 사이의 −4.4%). 제3세션이 고쳤고, 고문이 한 번 원본 유지를 지시했다가 과거 전체를
             수정본으로 다시 집계한 뒤 수정본으로 확정했다 — 빠지는 거래는 대부분 거래소 점검·장애 직후라 실제 체결이 어려웠을
             구간이고, 추적 시작 이후 사건이 0건일 때 바꿔서 전진 검증은 오염되지 않는다.
             등록 규칙(수정본)의 과거 기준 수치 — 고문 집계, 처음 정한 통과 기준 넷을 모두 통과:
               2017–20  1,190건, 평균 순수익 +0.219%, 일별 평균 하한 +0.257%, 상위 5일 제외 +0.323%
               2021–26  1,134건, 평균 순수익 +0.361%, 일별 평균 하한 +0.373%, 상위 5일 제외 +0.440%
             (참고: 원본 규칙이면 2017–20 1,198건 +0.260%, 2021–26 1,139건 +0.359%. 이 수치는 더 이상 기준이 아니다.)
  BTC_surge  BTC 주간 급등 관찰 (auto_monitor.surge_weeks). 월요일 00:00 UTC 에 직전 7일 수익률 ≥ +7.45% 인 주와 전체 주의
             BTC·ETH 주간 수익률을 기록한다. 판정 없음.
  자동 등록 후보  discover.py 가 달마다 '사건 × 조건 하나 × 보유 시간' 틀에서 찾아 forward/registry.json 에 등록한 것.
             등록 때 고정한 정의로 등록일 이후 거래만 채점한다. 판정은 30건·10일이 처음 채워질 때 한 번, 180일 안에 못 채우면 만료.
             추적 중인 후보가 없으면 시세도 받지 않는다. 틀과 한계는 discover.py 머리말에 있다.
엔진은 이것 하나다 (고문 지시 2026-11-11): auto_monitor.py 는 규칙 계산을 빌려 쓰는 파일이고, 따로 도는 워크플로는 두지 않는다.

안전장치
  - 종목 하나라도 시세를 못 받으면 그 후보는 이번 실행에서 장부를 건드리지 않는다 (빠진 종목 때문에 다른 종목이 뽑히는 것을 막음).
    바이낸스는 '그 종목 없음(400)' 과 '못 받음' 을 구분한다.
  - H2 는 끝난 UTC 일자까지만 쓴다 (오늘 일봉이 아직 안 생긴 종목 때문에 값이 달라지는 것을 막음).
    급락 반등도 실행일 00:00 UTC 이전 봉까지만 쓴다 (같은 날 다시 돌려도 같은 결과).
  - 급락 반등은 4종목 중 하나라도 못 받으면 그 실행에서 건드리지 않는다 (판정에 쓰는 거래 순서가 실행마다 달라지는 것을 막음).
  - 후보 하나가 실패해도 나머지는 갱신하고, 실패는 현황의 '경고' 와 종료 코드 1 로 알린다.
한계: 가상 성적이며 실제 체결이 아니다. 실행 주기(주 1회) 사이에 상장폐지된 종목은 마지막 며칠 가격을 놓칠 수 있다.
      2단계(후보 자동 등록)는 아직 없다 — 지금은 사람이 등록한 후보만 추적한다.
      급락 반등 자료는 매번 시작 시각부터 다시 받는다 (하루 약 6회 조회씩 늘어난다 — 1년이면 2천 회 남짓).

실행
  python3 scalp/research/forward_watch.py              장부·현황 갱신
  python3 scalp/research/forward_watch.py --dry-run    계산만 하고 파일은 쓰지 않음
  python3 scalp/research/forward_watch.py --summary    장부만 읽어 짧은 요약을 출력 (시세 조회 없음)
"""
import argparse
import datetime as dt
import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(HERE / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DF = _load("daily_factor_test")
TP = _load("trend_paper")
AM = _load("auto_monitor")
DISC = _load("discover")

FETCH_DAYS = 200
H2_FIRST_DECISION = "2026-10-24"     # forward 첫 결정일 (매수는 다음 날 시가). 이후 3일 간격
H2_DECIDE_AT = 20
OUT_DIR = HERE / "forward"
CR_START = AM.parse_utc(AM.FORWARD_START)          # 2026-11-11 00:00 UTC. 고문이 고정한 값 — 바꾸지 않는다
CR_WARMUP = dt.timedelta(hours=6)                   # 시작 직전 봉(1시간 전 종가 비교용). auto_monitor.run_weekly 와 같다
WAITING = ()
TOL = 1e-9


# ---------------------------------------------------------------------------------------------------------------------
def fetch_binance_strict(sym, get=None, limit=FETCH_DAYS):
    """{date: close}. 바이낸스에 없는 종목이면 None, 받지 못하면 예외 (둘을 섞지 않는다)."""
    get, last = get or DF._get, None
    for base in DF.BINANCE:
        try:
            rows = get("%s?symbol=%s&interval=1d&limit=%d" % (base, sym, limit))
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return None
            last = e
            continue
        except Exception as e:                                      # 다음 주소로 넘어간다
            last = e
            continue
        return {dt.datetime.fromtimestamp(int(r[0]) / 1000, dt.timezone.utc).date().isoformat(): float(r[4]) for r in rows}
    raise last if last else RuntimeError("no binance endpoint")


def fetch_h2_inputs(list_markets=None, fetch_upbit=None, fetch_binance=None, log=None):
    """→ (data, bn, 실패 목록). 실패가 하나라도 있으면 호출한 쪽이 H2 갱신을 건너뛴다."""
    list_markets = list_markets or (lambda: [m["market"] for m in DF._get(DF.UPBIT + "/market/all") if m["market"].startswith("KRW-")])
    fetch_upbit = fetch_upbit or (lambda m: DF.fetch_upbit_daily(m, FETCH_DAYS))
    fetch_binance = fetch_binance or fetch_binance_strict
    markets = sorted(list_markets())
    data, bn, failed = {}, {}, []
    for i, m in enumerate(markets, 1):
        try:
            rows = fetch_upbit(m)
        except Exception as e:
            failed.append("%s (%s)" % (m, type(e).__name__))
            continue
        if rows:
            data[m] = {d: tuple(v) for d, v in rows.items()}
        if log and i % 100 == 0:
            log("  upbit %d/%d" % (i, len(markets)))
    for i, m in enumerate(markets, 1):
        if m == "KRW-USDT":
            continue
        sym = m.split("-", 1)[1] + "USDT"
        try:
            v = fetch_binance(sym)
        except Exception as e:
            failed.append("%s (%s)" % (sym, type(e).__name__))
            continue
        if v:
            bn[m] = v
        if log and i % 100 == 0:
            log("  binance %d/%d matched %d" % (i, len(markets), len(bn)))
    return data, bn, failed


def h2_start(first_day):
    """받은 자료로 계산할 수 있는 첫 격자 결정일 (격자 = H2_FIRST_DECISION + 3k)."""
    base = DF._shift(first_day, DF.MIN_HIST + 5)
    gap = (dt.date.fromisoformat(base) - dt.date.fromisoformat(H2_FIRST_DECISION)).days
    return DF._shift(H2_FIRST_DECISION, 3 * max(0, -(-gap // 3)))


def h2_rows(data, bn, last_done_day):
    """등록일 이후의 완료된 기간 → [{date, picks, strat, ew, btc, …}] (date = 매수일)."""
    if not data:
        return []
    first = min(min(s) for s in data.values() if s)
    rows = DF.kimchi_periods(data, bn, h2_start(first), last_done_day)
    return [r for r in rows if r["date"] >= DF.FORWARD_START]


def merge_rows(old_rows, new_rows):
    """장부에 새 기간만 덧붙인다 → (합친 목록, 새로 붙은 수, 불일치 날짜들). 이미 적힌 기간은 그대로 둔다."""
    by_date = {r["date"]: r for r in old_rows}
    added, mismatch = 0, []
    for r in new_rows:
        prev = by_date.get(r["date"])
        if prev is None:
            by_date[r["date"]] = r
            added += 1
        elif any(abs(prev[k] - r[k]) > TOL for k in ("strat", "ew", "btc")) or prev.get("picks") != r.get("picks"):
            mismatch.append(r["date"])
    return [by_date[d] for d in sorted(by_date)], added, mismatch


def h2_decide(entry, run_day):
    """첫 H2_DECIDE_AT 기간이 찼고 아직 판정이 없으면 한 번만 판정해 적는다. 이미 있으면 건드리지 않는다."""
    rows = entry["rows"]
    if entry.get("decision") or len(rows) < H2_DECIDE_AT:
        return entry
    stats = DF.summarize(rows[:H2_DECIDE_AT])
    entry["decision"] = {"verdict": DF.verdict(stats, H2_DECIDE_AT), "decided_on": run_day, "stats": stats,
                         "periods": [rows[0]["date"], rows[H2_DECIDE_AT - 1]["date"]]}
    return entry


def h3_status(today, fetch_candles=None):
    """H3 가상 성적과 오늘 시가 기준 신호 상태."""
    fetch_candles = fetch_candles or (lambda m, day: TP.candles(m, through=day))
    legs, assets = {}, {}
    for m in TP.ASSETS:
        data = fetch_candles(m, today)
        leg = TP.simulate_leg(data, TP.START, today)
        sig = TP.signal_for_open(data, today)
        legs[m] = leg
        assets[m] = {"close": sig["close"], "sma": sig["average"], "gap": sig["close"] / sig["average"] - 1, "on": sig["on"],
                     "position": leg["position"], "equity": leg["equity"], "benchmark": leg["benchmark_equity"],
                     "trades": leg["trades"]}
    total = TP.portfolio_result(legs)
    return {"as_of": today, "assets": assets, "equity": total["equity"], "benchmark": total["benchmark_equity"]}


def cr_rows(today, fetch_all=None):
    """등록 시각 이후 진입해 끝난 급락 반등 거래 → [{market, entry_min, exit_min, drop, entry, exit, gross, net}] 시간순.
    실행일 00:00 UTC 이전 봉만 쓴다. 한 종목이라도 못 받으면 예외."""
    end = AM.parse_utc(today + "T00:00")
    res = (fetch_all or AM.cr_all)(CR_START - CR_WARMUP, end)
    failed = ["%s (%s)" % (m[4:], v) for m, v in res.items() if isinstance(v, Exception)]
    if failed or set(res) != set(AM.CR_COST):
        raise RuntimeError("5분봉을 못 받은 종목: %s" % (", ".join(failed) or "목록 불일치"))
    first = AM.to_min(CR_START)
    rows = [dict(t, market=m) for m, (done, _pending) in res.items() for t in done if t["entry_min"] >= first]
    return sorted(rows, key=lambda t: (t["entry_min"], t["market"]))


def merge_cr(old_rows, new_rows):
    """merge_rows 와 같은 원칙(덧붙이기만). 거래는 (종목, 진입 시각)으로 구분한다."""
    by_key = {(r["market"], r["entry_min"]): r for r in old_rows}
    added, mismatch = 0, []
    for r in new_rows:
        key = (r["market"], r["entry_min"])
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = r
            added += 1
        elif abs(prev["net"] - r["net"]) > TOL or prev["exit_min"] != r["exit_min"]:
            mismatch.append("%s %s" % (r["market"][4:], AM.kst(r["entry_min"])))
    return sorted(by_key.values(), key=lambda t: (t["entry_min"], t["market"])), added, mismatch


def _utc_day(minute):
    return dt.datetime.fromtimestamp(minute * 60, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def cr_decide(entry, run_day):
    """30건·10일이 처음 채워지는 거래까지로 한 번만 판정한다. 이미 판정이 있으면 건드리지 않는다."""
    if entry.get("decision"):
        return entry
    days = set()
    for k, t in enumerate(entry["rows"], 1):
        days.add(_utc_day(t["entry_min"]))
        if k >= AM.CR_MIN_N and len(days) >= AM.CR_MIN_DAYS:
            stats = AM.cr_summary([(r["market"], r) for r in entry["rows"][:k]])
            entry["decision"] = {"verdict": "PASS" if stats["verdict"].startswith("PASS") else "FAIL", "decided_on": run_day,
                                 "trades": k, "stats": stats}
            break
    return entry


def surge_rows(today, fetch_days=None):
    """끝난 주(다음 월요일 시가가 있는 주)의 BTC 주간 급등 관찰 → [{monday, r7, surge, net{종목: 수익률}}]."""
    now = AM.parse_utc(today + "T00:00")
    need = (now.date() - CR_START.date()).days + 20
    fetch_days = fetch_days or (lambda m: AM.fetch_days(m, need, now, keep_today=True))
    days = {m: fetch_days(m) for m in AM.H3_MARKETS}
    return [w for w in AM.surge_weeks(days, CR_START.strftime("%Y-%m-%d")) if set(w["net"]) == set(AM.H3_MARKETS)]


# ---------------------------------------------------------------------------------------------------------------------
def update(ledger, today, h2_inputs=None, h3_fetch=None, cr_fetch=None, surge_fetch=None, log=None, registry=None, panel_loader=None):
    """장부를 갱신한다 → (장부, 경고 목록). h2_inputs() 는 (data, bn, failed) 를 돌려주는 함수."""
    log = log or (lambda s: None)
    warnings = []
    last_done = DF._shift(today, -1)
    h2 = ledger.setdefault("H2_kimchi", {"rows": [], "decision": None})
    try:
        data, bn, failed = (h2_inputs or (lambda: fetch_h2_inputs(log=log)))()
        if failed:
            raise RuntimeError("시세를 못 받은 종목 %d개: %s" % (len(failed), ", ".join(failed[:5])))
        rows, added, mismatch = merge_rows(h2["rows"], h2_rows(data, bn, last_done))
        h2["rows"] = rows
        h2["updated"] = today
        if mismatch:
            warnings.append("H2: 다시 계산한 값이 장부와 다른 기간 %d개 (%s) — 장부 값은 그대로 둠" % (len(mismatch), ", ".join(mismatch[:5])))
        h2_decide(h2, today)
        log("H2: %d periods in ledger (+%d)" % (len(rows), added))
    except Exception as e:
        warnings.append("H2: 이번 실행에서 갱신하지 못함 — %s" % e)
    try:
        ledger["H3_trend"] = h3_status(today, h3_fetch)
        log("H3: status as of %s" % today)
    except Exception as e:
        warnings.append("H3: 이번 실행에서 갱신하지 못함 — %s: %s" % (type(e).__name__, e))
    cr = ledger.setdefault("CR_rebound", {"rows": [], "decision": None})
    try:
        rows, added, mismatch = merge_cr(cr["rows"], cr_rows(today, cr_fetch))
        cr["rows"] = rows
        cr["updated"] = today
        if mismatch:
            warnings.append("급락 반등: 다시 계산한 값이 장부와 다른 거래 %d건 (%s) — 장부 값은 그대로 둠" % (len(mismatch), ", ".join(mismatch[:5])))
        cr_decide(cr, today)
        log("CR: %d trades in ledger (+%d)" % (len(rows), added))
    except Exception as e:
        warnings.append("급락 반등: 이번 실행에서 갱신하지 못함 — %s" % e)
    sg = ledger.setdefault("BTC_surge", {"rows": []})
    try:
        known = {r["monday"] for r in sg["rows"]}
        sg["rows"] = sorted(sg["rows"] + [w for w in surge_rows(today, surge_fetch) if w["monday"] not in known], key=lambda w: w["monday"])
        sg["updated"] = today
        log("SURGE: %d weeks in ledger" % len(sg["rows"]))
    except Exception as e:
        warnings.append("BTC 주간 급등: 이번 실행에서 갱신하지 못함 — %s: %s" % (type(e).__name__, e))
    active = [c for c in (registry or []) if c.get("status") == "ACTIVE"]
    cands = ledger.setdefault("candidates", {})
    open_now = [c for c in active if not (cands.get(c["id"]) or {}).get("decision")]
    if open_now:                                                    # 추적 중인 후보가 있을 때만 60분봉을 받는다
        try:
            end_h = DISC.day_hour(today)
            start_h = min(DISC.day_hour(c["registered"]) for c in open_now) - DISC.WARMUP_H
            markets = tuple(sorted({m for c in open_now for m in c["universe"]}))
            panel = (panel_loader or DISC.load_panel)(start_h, end_h, markets)
            for c in open_now:
                entry = cands.setdefault(c["id"], {"rows": [], "decision": None, "rule": DISC.describe(c), "registered": c["registered"]})
                rows, added, mismatch = DISC.merge_trades(entry["rows"], DISC.candidate_trades(panel, c, end_h))
                entry["rows"], entry["updated"] = rows, today
                if mismatch:
                    warnings.append("후보 %s: 다시 계산한 값이 장부와 다른 거래 %d건 — 장부 값은 그대로 둠" % (c["id"], len(mismatch)))
                DISC.decide_once(entry, c, today)
                log("%s: %d trades in ledger (+%d)" % (c["id"], len(rows), added))
        except Exception as e:
            warnings.append("자동 등록 후보: 이번 실행에서 갱신하지 못함 — %s" % e)
    ledger["last_run"] = today
    ledger["warnings"] = warnings
    return ledger, warnings


def _pct(v, digits=2, unit="%"):
    return ("%+." + str(digits) + "f%s") % (100 * v, unit)


def _mark(ok):
    return "충족" if ok else "미달"


def render(ledger):
    """현황 문서(마크다운)."""
    out = ["# 후보 추적 현황", "",
           "자동으로 만들어지는 파일입니다. 손으로 고치지 마세요. 마지막 실행: %s (UTC)." % ledger.get("last_run", "-"), ""]
    h2 = ledger.get("H2_kimchi") or {"rows": []}
    rows, dec = h2.get("rows", []), h2.get("decision")
    if dec:
        head = "판정 완료: %s (%s, 첫 %d기간 %s ~ %s)" % (
            "통과" if dec["verdict"] == "PASS" else "탈락", dec["decided_on"], H2_DECIDE_AT, dec["periods"][0], dec["periods"][1])
    else:
        head = "진행 중 (%d / %d기간)" % (len(rows), H2_DECIDE_AT)
    out += ["## H2 김치 프리미엄 되돌림 — " + head, "",
            "업비트 가격이 평소보다 싸진 종목 5개를 3일 보유. 추적 시작 2026-10-23, 자료 반영 %s." % h2.get("updated", "-"), ""]
    if rows:
        st = dec["stats"] if dec else DF.summarize(rows)
        out += ["| 항목 | %s | 기준 | 상태 |" % ("판정에 쓴 값" if dec else "지금까지"), "|---|---|---|---|",
                "| 기간당 평균 (비용 후) | %+.2f%% | 0 초과 | %s |" % (st["mean_pct"], _mark(st["mean_pct"] > 0)),
                "| 동일비중 대비 초과 | %+.2f%%p | 0 초과 | %s |" % (st["excess_pct"], _mark(st["excess_pct"] > 0)),
                "| 초과수익 하한 (90%%) | %+.2f%%p | 0 초과 | %s |" % (st["excess_ci_low_pct"], _mark(st["excess_ci_low_pct"] > 0)),
                "| 누적 수익 | %+.1f%% | BTC %+.1f%% 초과 | %s |" % (st["cum_pct"], st["btc_cum_pct"],
                                                              _mark(st["cum_pct"] > st["btc_cum_pct"])), ""]
        if dec:
            now = DF.summarize(rows)
            out += ["판정 이후 포함 전체 %d기간: 기간당 %+.2f%%, 초과 %+.2f%%p, 누적 %+.1f%% (BTC %+.1f%%). 판정은 바뀌지 않습니다." % (
                now["periods"], now["mean_pct"], now["excess_pct"], now["cum_pct"], now["btc_cum_pct"]), ""]
        else:
            due = DF._shift(H2_FIRST_DECISION, 3 * (H2_DECIDE_AT - 1) + 1 + DF.KP_HOLD)
            out += ["아직 판정 전이라 위 숫자는 참고용입니다. 20번째 기간이 %s 에 끝나고, 그 뒤 첫 실행에서 한 번만 판정합니다." % due, ""]
        last = rows[-1]
        out += ["최근 기간 (%s 매수): %s → %s (동일비중 %s)" % (
            last["date"], ", ".join(m.replace("KRW-", "") for m in last.get("picks", [])), _pct(last["strat"]), _pct(last["ew"])), ""]
    else:
        out += ["아직 끝난 기간이 없습니다.", ""]
    h3 = ledger.get("H3_trend")
    out += ["## H3 BTC·ETH 추세 — 추적 중 (통과/탈락 판정 없음)", ""]
    if h3:
        out += ["전일 종가가 50일 평균보다 높으면 보유, 아니면 현금. %s 시가 기준." % h3["as_of"], "",
                "| 종목 | 전일 종가 | 50일 평균 | 차이 | 가상 포지션 |", "|---|---|---|---|---|"]
        for m, a in h3["assets"].items():
            need = "" if a["on"] else " (매수 신호까지 %s)" % _pct(a["sma"] / a["close"] - 1, 1)
            out.append("| %s | %s | %s | %s%s | %s |" % (m.replace("KRW-", ""), format(a["close"], ",.0f"), format(a["sma"], ",.0f"),
                                                       _pct(a["gap"], 1), need, "보유" if a["position"] else "현금"))
        n_tr = sum(len(a["trades"]) for a in h3["assets"].values())
        out += ["", "가상 성적 (2026-10-24 ~ ): 전략 %s, 단순 보유 %s. 거래 %d건." % (
            _pct(h3["equity"] - 1), _pct(h3["benchmark"] - 1), n_tr), ""]
    else:
        out += ["아직 기록이 없습니다.", ""]
    cr = ledger.get("CR_rebound") or {"rows": []}
    rows, dec = cr.get("rows", []), cr.get("decision")
    n_days = len({_utc_day(t["entry_min"]) for t in rows})
    if dec:
        head = "판정 완료: %s (%s, 첫 %d건)" % ("통과" if dec["verdict"] == "PASS" else "탈락", dec["decided_on"], dec["trades"])
    else:
        head = "진행 중 (%d / %d건, %d / %d일)" % (len(rows), AM.CR_MIN_N, n_days, AM.CR_MIN_DAYS)
    out += ["## 급락 반등 — " + head, "",
            "BTC·ETH·XRP·SOL 이 1시간에 4%% 넘게 빠지면 다음 5분봉 시가에 사서 60분 뒤 판다. 추적 시작 2026-11-11, 자료 반영 %s." % cr.get("updated", "-"), ""]
    if rows:
        st = dec["stats"] if dec else AM.cr_summary([(t["market"], t) for t in rows])
        out += ["| 항목 | %s | 기준 | 상태 |" % ("판정에 쓴 값" if dec else "지금까지"), "|---|---|---|---|",
                "| 거래당 평균 (비용 후) | %s | 0 초과 | %s |" % (_pct(st["mean"]), _mark(st["mean"] > 0)),
                "| 날짜 가중 평균 | %s | 0 초과 | %s |" % (_pct(st["day_mean"]), _mark(st["day_mean"] > 0)),
                "| 승률 / 최악 거래 | %.0f%% / %s | 참고 | - |" % (100 * st["win"], _pct(st["worst"], 1)), ""]
        if dec:
            now = AM.cr_summary([(t["market"], t) for t in rows])
            out += ["판정 이후 포함 전체 %d건: 거래당 %s, 날짜 가중 %s. 판정은 바뀌지 않습니다." % (now["n"], _pct(now["mean"]), _pct(now["day_mean"])), ""]
        else:
            out += ["아직 판정 전이라 위 숫자는 참고용입니다. 30건·10일이 처음 채워지는 거래까지로 한 번만 판정합니다.", ""]
        out += ["최근 거래: " + " / ".join("%s %s 1시간 %s → %s" % (AM.kst(t["entry_min"]), t["market"][4:], _pct(t["drop"], 1), _pct(t["net"]))
                                       for t in rows[-3:]), ""]
    else:
        out += ["아직 끝난 거래가 없습니다. 통과해도 봉 기준이며, 급락 순간의 실제 호가 체결은 따로 확인해야 합니다.", ""]
    sg = ledger.get("BTC_surge") or {"rows": []}
    out += ["## BTC 주간 급등 — 관찰만 (판정 없음)", ""]
    if sg.get("rows"):
        weeks = sg["rows"]
        hit = [w for w in weeks if w["surge"]]

        def avg(ws, m):
            return _pct(sum(w["net"][m] for w in ws) / len(ws)) if ws else "-"
        out += ["직전 7일에 BTC 가 7.45% 넘게 오른 주(급등 주)와 전체 주의 그다음 주 수익률.", "",
                "| 구분 | 주 수 | BTC | ETH |", "|---|---|---|---|",
                "| 전체 | %d | %s | %s |" % (len(weeks), avg(weeks, "KRW-BTC"), avg(weeks, "KRW-ETH")),
                "| 급등 뒤 | %d | %s | %s |" % (len(hit), avg(hit, "KRW-BTC"), avg(hit, "KRW-ETH")), ""]
    else:
        out += ["아직 끝난 주가 없습니다 (첫 주는 2026-11-16 시작).", ""]
    out += ["## 자동 등록 후보 — 달마다 탐색해 넘은 것만 등록", ""]
    cands = ledger.get("candidates") or {}
    if cands:
        out += ["| 번호 | 규칙 | 등록 | 진행 | 거래당 평균 | 판정 |", "|---|---|---|---|---|---|"]
        for cid in sorted(cands):
            e = cands[cid]
            rows, dec = e.get("rows", []), e.get("decision")
            st = DISC.SS.cluster_stats(rows)
            verdict = {"PASS": "통과", "FAIL": "탈락", "EXPIRED": "만료"}[dec["verdict"]] if dec else "진행 중"
            out.append("| %s | %s | %s | %d / %d건, %d / %d일 | %s | %s |" % (
                cid, e.get("rule", "-"), e.get("registered", "-"), st["n"], DISC.FWD_MIN_N, st["days"], DISC.FWD_MIN_DAYS,
                "-" if st["net"] is None else _pct(st["net"]), verdict))
        out += ["", "통과는 '후보' 라는 뜻입니다. 봉 기준이고 실제 호가 체결은 확인하지 않은 값입니다.", ""]
    else:
        out += ["아직 등록된 후보가 없습니다. 탐색 결과는 DISCOVERY.md 에 남습니다.", ""]
    out += ["## 대기 중", ""] + (["- %s: %s" % w for w in WAITING] or ["- 없음"]) + [""]
    out += ["## 경고", ""] + (["- " + w for w in ledger.get("warnings", [])] or ["- 없음"]) + [""]
    return "\n".join(out)


def summary(ledger):
    """텔레그램 등에 보낼 짧은 요약 (몇 줄)."""
    lines = ["[후보 추적] %s" % ledger.get("last_run", "-")]
    h2 = ledger.get("H2_kimchi") or {}
    rows, dec = h2.get("rows", []), h2.get("decision")
    if dec:
        lines.append("H2 김치 되돌림: 판정 %s (%s)" % ("통과" if dec["verdict"] == "PASS" else "탈락", dec["decided_on"]))
    elif rows:
        st = DF.summarize(rows)
        lines.append("H2 김치 되돌림: %d/%d기간, 기간당 %+.2f%%, 초과 %+.2f%%p (판정 전)" % (
            len(rows), H2_DECIDE_AT, st["mean_pct"], st["excess_pct"]))
    else:
        lines.append("H2 김치 되돌림: 끝난 기간 없음")
    h3 = ledger.get("H3_trend")
    if h3:
        parts = ["%s %s%s" % (m.replace("KRW-", ""), "보유" if a["position"] else "현금",
                              "" if a["on"] else "(신호까지 %s)" % _pct(a["sma"] / a["close"] - 1, 1)) for m, a in h3["assets"].items()]
        lines.append("H3 추세: " + ", ".join(parts) + " | 가상 %s vs 보유 %s" % (_pct(h3["equity"] - 1), _pct(h3["benchmark"] - 1)))
    cr = ledger.get("CR_rebound")
    if cr is not None:
        rows, dec = cr.get("rows", []), cr.get("decision")
        if dec:
            lines.append("급락 반등: 판정 %s (%s)" % ("통과" if dec["verdict"] == "PASS" else "탈락", dec["decided_on"]))
        elif rows:
            st = AM.cr_summary([(t["market"], t) for t in rows])
            lines.append("급락 반등: %d/%d건 %d/%d일, 거래당 %s (판정 전)" % (st["n"], AM.CR_MIN_N, st["days"], AM.CR_MIN_DAYS, _pct(st["mean"])))
        else:
            lines.append("급락 반등: 발생 없음")
    cands = ledger.get("candidates") or {}
    if cands:
        done = [c for c, e in cands.items() if e.get("decision")]
        passed = [c for c in done if cands[c]["decision"]["verdict"] == "PASS"]
        lines.append("자동 등록 후보: %d개 추적, 판정 %d개%s" % (len(cands), len(done), " (통과: %s)" % ", ".join(sorted(passed)) if passed else ""))
    for w in ledger.get("warnings", []):
        lines.append("경고: " + w)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--today", help="UTC 날짜 YYYY-MM-DD (시험용, 기본은 지금)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    path = out / "ledger.json"
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if a.summary:
        print(summary(ledger))
        return 0
    today = a.today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    reg_path = out / "registry.json"
    registry = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else []
    ledger, warnings = update(ledger, today, log=lambda s: print(s, flush=True), registry=registry)
    text = render(ledger)
    if not a.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        (out / "STATUS.md").write_text(text, encoding="utf-8")
    print(text)
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
