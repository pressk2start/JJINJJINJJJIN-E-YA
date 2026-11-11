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
대기 (규칙 정의가 오면 등록): 급락 반등 — 고문 세션의 후보.

안전장치
  - 종목 하나라도 시세를 못 받으면 그 후보는 이번 실행에서 장부를 건드리지 않는다 (빠진 종목 때문에 다른 종목이 뽑히는 것을 막음).
    바이낸스는 '그 종목 없음(400)' 과 '못 받음' 을 구분한다.
  - H2 는 끝난 UTC 일자까지만 쓴다 (오늘 일봉이 아직 안 생긴 종목 때문에 값이 달라지는 것을 막음).
  - 후보 하나가 실패해도 나머지는 갱신하고, 실패는 현황의 '경고' 와 종료 코드 1 로 알린다.
한계: 가상 성적이며 실제 체결이 아니다. 실행 주기(주 1회) 사이에 상장폐지된 종목은 마지막 며칠 가격을 놓칠 수 있다.
      2단계(후보 자동 등록)는 아직 없다 — 지금은 사람이 등록한 후보만 추적한다.

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

FETCH_DAYS = 200
H2_FIRST_DECISION = "2026-10-24"     # forward 첫 결정일 (매수는 다음 날 시가). 이후 3일 간격
H2_DECIDE_AT = 20
OUT_DIR = HERE / "forward"
WAITING = (("급락 반등", "고문 세션의 후보 — 규칙 정의를 받으면 등록"),)
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


# ---------------------------------------------------------------------------------------------------------------------
def update(ledger, today, h2_inputs=None, h3_fetch=None, log=None):
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
    out += ["## 대기 중", ""] + ["- %s: %s" % w for w in WAITING] + [""]
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
    ledger, warnings = update(ledger, today, log=lambda s: print(s, flush=True))
    text = render(ledger)
    if not a.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        (out / "STATUS.md").write_text(text, encoding="utf-8")
    print(text)
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
