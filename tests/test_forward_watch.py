# -*- coding: utf-8 -*-
"""forward_watch 테스트 — 장부는 덧붙이기만 하는지, 판정은 첫 20기간으로 한 번만 하는지, 시세를 못 받으면 장부를 건드리지 않는지."""
import contextlib
import copy
import datetime as dt
import hashlib
import importlib.util
import inspect
import io
import json
import os
import random
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fw", ROOT / "scalp" / "research" / "forward_watch.py")
FW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FW)
DF, TP, AM, DISC = FW.DF, FW.TP, FW.AM, FW.DISC
D0 = dt.date(2026, 6, 1)


def day(k):
    return (D0 + dt.timedelta(days=k)).isoformat()


def market_world(n_days, seed=7, n_alts=24):
    """업비트 일봉·바이낸스 종가 가짜 세계. 업비트 가격 = 바이낸스 × 환율 × (1 + 잡음 프리미엄)."""
    rng = random.Random(seed)
    data, bn = {"KRW-USDT": {}, "KRW-BTC": {}}, {}
    usdt = 1400.0
    names = ["KRW-BTC"] + ["KRW-A%02d" % i for i in range(n_alts)]
    px = {m: 100.0 + 10 * i for i, m in enumerate(names)}
    for m in names:
        data.setdefault(m, {})
        bn[m] = {}
    for k in range(n_days):
        d = day(k)
        data["KRW-USDT"][d] = (usdt, usdt, 1e11)
        for m in names:
            o = px[m]
            c = o * (1 + rng.gauss(0, 0.02))
            px[m] = c
            prem = rng.gauss(0.01, 0.01)
            data[m][d] = (o * usdt * (1 + prem), c * usdt * (1 + prem), 1e10 * (1 + names.index(m)))
            bn[m][d] = c
    return data, bn


def row(k, strat, ew=0.0, btc=0.0):
    return {"date": day(k), "picks": ["KRW-A01"], "strat": strat, "ew": ew, "btc": btc}


class Grid(unittest.TestCase):
    def test_first_forward_buy_day_matches_the_pc_runs(self):
        # PC 출력: HOLDOUT 61기간(매수일 04-25 … 10-22), 11/02 시점 FORWARD 2기간(10-25, 10-28) → 첫 결정일 10-24
        self.assertEqual(DF._shift(FW.H2_FIRST_DECISION, 1), "2026-10-25")
        self.assertEqual((dt.date(2026, 10, 25) - dt.date(2026, 4, 25)).days % 3, 0)
        self.assertGreaterEqual(DF._shift(FW.H2_FIRST_DECISION, 1), DF.FORWARD_START)

    def test_start_stays_on_the_three_day_grid(self):
        self.assertEqual(FW.h2_start("2026-04-26"), FW.H2_FIRST_DECISION)          # 자료가 충분하면 첫 결정일 그대로
        for first in ("2026-08-21", "2026-08-22", "2026-08-23", "2027-01-05"):
            start = FW.h2_start(first)
            self.assertGreaterEqual(start, DF._shift(first, DF.MIN_HIST + 5))
            self.assertEqual((dt.date.fromisoformat(start) - dt.date.fromisoformat(FW.H2_FIRST_DECISION)).days % 3, 0)

    def test_rows_use_only_completed_days_and_the_registered_grid(self):
        data, bn = market_world(200)
        real_first, real_fwd = FW.H2_FIRST_DECISION, DF.FORWARD_START
        FW.H2_FIRST_DECISION, DF.FORWARD_START = day(100), day(100)
        try:
            rows = FW.h2_rows(data, bn, day(150))
            more = FW.h2_rows(data, bn, day(160))
        finally:
            FW.H2_FIRST_DECISION, DF.FORWARD_START = real_first, real_fwd
        self.assertTrue(rows)
        self.assertEqual(rows[0]["date"], day(101))                                 # 결정일 다음 날 시가 매수
        self.assertTrue(all((dt.date.fromisoformat(r["date"]) - dt.date.fromisoformat(day(101))).days % 3 == 0 for r in rows))
        self.assertLessEqual(DF._shift(rows[-1]["date"], DF.KP_HOLD), day(150))     # 매도일이 끝난 날짜 안
        self.assertEqual(more[:len(rows)], rows)                                    # 날짜가 지나도 앞 기간 값은 그대로


class Ledger(unittest.TestCase):
    def test_merge_appends_new_periods_and_never_rewrites_old_ones(self):
        old = [row(0, 0.01), row(3, -0.02)]
        new = [row(3, -0.05), row(6, 0.03)]                                         # 3일차는 다시 계산하니 값이 다르다
        merged, added, mismatch = FW.merge_rows(old, new)
        self.assertEqual([r["date"] for r in merged], [day(0), day(3), day(6)])
        self.assertEqual(merged[1]["strat"], -0.02)                                 # 장부 값 유지
        self.assertEqual((added, mismatch), (1, [day(3)]))
        self.assertEqual(FW.merge_rows(merged, merged), (merged, 0, []))

    def test_decision_uses_the_first_twenty_periods_once(self):
        good = [row(3 * k, 0.02 + 0.001 * (k % 3), 0.0, 0.0) for k in range(20)]
        entry = {"rows": good[:19], "decision": None}
        FW.h2_decide(entry, "2026-12-20")
        self.assertIsNone(entry["decision"])                                        # 19기간: 아직
        entry["rows"] = good + [row(60, -0.9), row(63, -0.9)]                        # 21·22번째가 폭락해도
        FW.h2_decide(entry, "2026-12-28")
        self.assertEqual(entry["decision"]["verdict"], "PASS")                      # 판정은 첫 20기간만 본다
        self.assertEqual(entry["decision"]["periods"], [day(0), day(57)])
        frozen = copy.deepcopy(entry["decision"])
        entry["rows"] += [row(66, -0.9)]
        FW.h2_decide(entry, "2027-01-05")
        self.assertEqual(entry["decision"], frozen)                                 # 한 번 적힌 판정은 바뀌지 않는다

    def test_the_verdict_record_and_the_status_page_carry_the_delisting_bias_note(self):
        """고문 판정 2026-11-14: 검사 절차(주 1회 실행)는 바꾸지 않고, 상장폐지 편향을 주석으로 남긴다."""
        entry = FW.h2_decide({"rows": [row(k, 0.01, 0.0, 0.0) for k in range(FW.H2_DECIDE_AT)], "decision": None}, "2026-12-28")
        self.assertIn("상장폐지 편향(통과 쪽)", entry["decision"]["note"])
        text = FW.render({"H2_kimchi": {"rows": [row(0, 0.01)], "decision": None}, "last_run": "2026-11-16"})
        self.assertIn("주의 — 상장폐지 편향(통과 쪽)", text)

    def test_daily_runs_touch_the_kimchi_item_only_once_a_week(self):
        """추적은 매일 돌지만(2026-11-17) H2 는 등록된 절차대로 주 1회만 반영한다(고문 판정 2026-11-14)."""
        calls = []
        rows = [row(k, 0.01) for k in range(3)]

        def inputs():
            calls.append(1)
            return {}, {}, []
        real = FW.h2_rows
        FW.h2_rows = lambda data, bn, last: rows
        try:
            args = offline()
            args["h2_inputs"] = inputs
            ledger, warnings = FW.update({}, "2026-11-23", **args)                                    # 월요일: 반영한다
            self.assertEqual((len(calls), ledger["H2_kimchi"]["updated"], len(ledger["H2_kimchi"]["rows"])), (1, "2026-11-23", 3))
            for day in ("2026-11-24", "2026-11-25", "2026-11-29"):                                    # 화·수·일: 건너뛴다(실패가 아니다)
                ledger, warnings = FW.update(ledger, day, **args)
                self.assertEqual((len(calls), ledger["H2_kimchi"]["updated"]), (1, "2026-11-23"))
                self.assertFalse(any("H2" in w for w in warnings))
            ledger, warnings = FW.update(ledger, "2026-11-30", **args)                                # 다음 월요일: 다시 반영한다
            self.assertEqual((len(calls), ledger["H2_kimchi"]["updated"]), (2, "2026-11-30"))
            args["h2_inputs"] = lambda: ({}, {}, ["KRW-X (TimeoutError)"])                            # 그다음 월요일에는 시세를 못 받았다
            ledger, warnings = FW.update(ledger, "2026-12-07", **args)
            self.assertTrue(any("H2: 이번 실행에서 갱신하지 못함" in w for w in warnings))
            args["h2_inputs"] = inputs
            ledger, warnings = FW.update(ledger, "2026-12-08", **args)                                # 화요일 실행이 그 주의 반영을 대신한다
            self.assertEqual((len(calls), ledger["H2_kimchi"]["updated"]), (3, "2026-12-08"))
        finally:
            FW.h2_rows = real
        self.assertEqual((FW.this_monday("2026-11-23"), FW.this_monday("2026-11-29"), FW.this_monday("2026-11-30")),
                         ("2026-11-23", "2026-11-23", "2026-11-30"))

    def test_a_losing_candidate_is_marked_fail(self):
        entry = {"rows": [row(3 * k, -0.01, 0.0, 0.0) for k in range(20)], "decision": None}
        FW.h2_decide(entry, "2026-12-28")
        self.assertEqual(entry["decision"]["verdict"], "FAIL")


class Fetching(unittest.TestCase):
    def test_binance_missing_symbol_is_none_but_an_outage_raises(self):
        def no_symbol(url):
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)

        def outage(url):
            raise urllib.error.URLError("down")

        self.assertIsNone(FW.fetch_binance_strict("NOPEUSDT", get=no_symbol))
        with self.assertRaises(urllib.error.URLError):
            FW.fetch_binance_strict("BTCUSDT", get=outage)
        ok = FW.fetch_binance_strict("BTCUSDT", get=lambda url: [[1790000000000 - 1790000000000 % 86400000, "1", "2", "0.5", "1.5"]])
        self.assertEqual(list(ok.values()), [1.5])

    def test_one_failed_market_is_reported_not_silently_dropped(self):
        def upbit(m):
            if m == "KRW-B":
                raise TimeoutError("slow")
            return {"2026-11-01": (1.0, 1.0, 1.0)}

        data, bn, failed = FW.fetch_h2_inputs(lambda: ["KRW-A", "KRW-B", "KRW-USDT"], upbit, lambda sym: {"2026-11-01": 1.0})
        self.assertEqual(sorted(data), ["KRW-A", "KRW-USDT"])
        self.assertEqual(failed, ["KRW-B (TimeoutError)"])
        self.assertEqual(sorted(bn), ["KRW-A", "KRW-B"])

    def test_update_leaves_the_ledger_alone_when_a_fetch_failed(self):
        ledger = {"H2_kimchi": {"rows": [row(0, 0.01)], "decision": None, "updated": "2026-11-01"}}
        before = copy.deepcopy(ledger["H2_kimchi"])
        out, warnings = FW.update(ledger, "2026-11-10", h2_inputs=lambda: ({}, {}, ["KRW-X (TimeoutError)"]),
                                  h3_fetch=lambda m, d: (_ for _ in ()).throw(OSError("no network")),
                                  cr_fetch=lambda s, e: {m: ([], None) for m in AM.CR_COST}, surge_fetch=lambda m: [], crv_fetch=lambda m, s, e: [])
        self.assertEqual(out["H2_kimchi"], before)
        self.assertEqual(len(warnings), 2)
        self.assertIn("H2", warnings[0])
        self.assertIn("H3", warnings[1])
        self.assertIn("경고", FW.render(out))


def h3_candles(n, slope):
    """START 50일 전부터 n일. slope>0 이면 오르는 장(종가 > 평균)."""
    start = dt.date.fromisoformat(TP.START) - dt.timedelta(days=TP.N + 1)
    out, p = {}, 100.0
    for k in range(n):
        o = p
        p = p * (1 + slope)
        out[(start + dt.timedelta(days=k)).isoformat()] = (o, p)
    return out


class Trend(unittest.TestCase):
    def test_status_reports_gap_to_the_average_and_paper_position(self):
        today = DF._shift(TP.START, 10)
        up, down = h3_candles(70, 0.01), h3_candles(70, -0.01)
        st = FW.h3_status(today, lambda m, d: up if m == "KRW-BTC" else down)
        btc, eth = st["assets"]["KRW-BTC"], st["assets"]["KRW-ETH"]
        self.assertTrue(btc["on"] and btc["position"] and btc["gap"] > 0)
        self.assertFalse(eth["on"] or eth["position"])
        self.assertEqual(len(eth["trades"]), 0)
        self.assertAlmostEqual(eth["equity"], 1.0)
        text = FW.render({"H3_trend": st, "last_run": today})
        self.assertIn("매수 신호까지", text)                                         # 현금인 종목에만 표시
        self.assertEqual(text.count("매수 신호까지"), 1)


class Report(unittest.TestCase):
    def ledger(self, n):
        return {"H2_kimchi": {"rows": [row(3 * k, 0.01, 0.002, 0.001) for k in range(n)], "decision": None, "updated": "2026-11-10"},
                "last_run": "2026-11-11", "warnings": []}

    def test_status_shows_progress_before_the_decision_and_the_verdict_after(self):
        text = FW.render(self.ledger(6))
        self.assertIn("진행 중 (6 / 20기간)", text)
        self.assertIn("2026-12-24", text)                                           # 20번째 기간이 끝나는 날
        self.assertIn("참고용", text)
        done = self.ledger(22)
        FW.h2_decide(done["H2_kimchi"], "2026-12-28")
        text = FW.render(done)
        self.assertIn("판정 완료: 통과", text)
        self.assertIn("전체 22기간", text)
        self.assertIn("판정 통과", FW.summary(done))

    def test_summary_is_short_and_mentions_every_candidate(self):
        lines = FW.summary(self.ledger(6)).splitlines()
        self.assertLessEqual(len(lines), 4)
        self.assertIn("6/20기간", lines[1])

    def test_cli_summary_reads_the_ledger_without_fetching(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ledger.json").write_text(json.dumps(self.ledger(3)), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = FW.main(["--summary", "--out", tmp])
        self.assertEqual(code, 0)
        self.assertIn("3/20기간", buf.getvalue())


def five_min(closes, start_min):
    """5분봉 [(분, 시가, 종가)]. 시가 = 직전 종가."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append((start_min + 5 * i, prev, c))
        prev = c
    return out


def cr_trade(k, net, market="KRW-BTC", per_day=3):
    """k 번째 가짜 거래. 하루에 per_day 건씩, 등록 시각 이후."""
    entry = AM.to_min(FW.CR_START) + (k // per_day) * 1440 + (k % per_day) * 120
    return {"market": market, "entry_min": entry, "exit_min": entry + 60, "drop": -0.05, "entry": 100.0, "exit": 100.0 * (1 + net),
            "gross": net, "net": net}


class CrashRebound(unittest.TestCase):
    def world(self, start_min, drop_at=20):
        closes = [100.0] * drop_at + [95.0] + [96.0] * 30
        flat = five_min([100.0] * (drop_at + 31), start_min)
        res = {m: AM.cr_events(flat, c) for m, c in AM.CR_COST.items()}
        res["KRW-SOL"] = AM.cr_events(five_min(closes, start_min), AM.CR_COST["KRW-SOL"])
        return res

    def test_rows_come_from_the_advisors_rule_and_only_after_the_registered_start(self):
        first = AM.to_min(FW.CR_START)
        asked = []

        def after(start, end):
            asked.append((start, end))
            return self.world(first + 60)

        rows = FW.cr_rows("2026-11-13", after)
        self.assertEqual(asked, [(FW.CR_START - FW.CR_WARMUP, AM.parse_utc("2026-11-13T00:00"))])   # 실행일 0시 이전 봉만
        self.assertEqual([(r["market"], r["entry_min"]) for r in rows], [("KRW-SOL", first + 60 + 5 * 21)])
        self.assertAlmostEqual(rows[0]["net"], 96.0 / 95.0 * (1 - 0.0010) / (1 + 0.0010) - 1)       # SOL 비용 편도 0.10%
        self.assertEqual(FW.cr_rows("2026-11-13", lambda s, e: self.world(first - 300)), [])        # 등록 전에 진입한 거래는 세지 않는다

    def test_one_missing_market_blocks_the_whole_update(self):
        res = self.world(AM.to_min(FW.CR_START) + 60)
        res["KRW-XRP"] = RuntimeError("조회 실패")
        with self.assertRaises(RuntimeError):
            FW.cr_rows("2026-11-13", lambda s, e: res)
        ledger = {"CR_rebound": {"rows": [cr_trade(0, 0.01)], "decision": None}}
        before = copy.deepcopy(ledger["CR_rebound"])
        out, warnings = FW.update(ledger, "2026-11-13", h2_inputs=lambda: ({}, {}, ["x"]), h3_fetch=lambda m, d: {},
                                  cr_fetch=lambda s, e: res, surge_fetch=lambda m: [], crv_fetch=lambda m, s, e: [])
        self.assertEqual(out["CR_rebound"], before)
        self.assertTrue(any("급락 반등" in w for w in warnings))

    def test_ledger_only_appends_and_flags_a_changed_trade(self):
        old = [cr_trade(0, 0.01), cr_trade(1, -0.02)]
        new = [dict(cr_trade(1, -0.02), net=-0.5), cr_trade(2, 0.03)]
        merged, added, mismatch = FW.merge_cr(old, new)
        self.assertEqual([t["net"] for t in merged], [0.01, -0.02, 0.03])
        self.assertEqual((added, len(mismatch)), (1, 1))

    def test_verdict_is_taken_once_when_thirty_trades_and_ten_days_are_first_reached(self):
        good = [cr_trade(k, 0.01) for k in range(29)]                                # 29건·10일: 건수가 모자란다
        entry = {"rows": list(good), "decision": None}
        FW.cr_decide(entry, "2026-12-01")
        self.assertIsNone(entry["decision"])
        entry["rows"] = good + [cr_trade(29, 0.01)] + [cr_trade(k, -0.9) for k in range(30, 40)]    # 31번째부터 폭락해도
        FW.cr_decide(entry, "2026-12-08")
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("PASS", 30))  # 처음 채워진 30건만 본다
        frozen = copy.deepcopy(entry["decision"])
        entry["rows"] += [cr_trade(40, -0.9)]
        FW.cr_decide(entry, "2026-12-15")
        self.assertEqual(entry["decision"], frozen)

    def test_thirty_trades_on_too_few_days_wait_for_the_tenth_day(self):
        entry = {"rows": [cr_trade(k, -0.01, per_day=10) for k in range(60)], "decision": None}     # 60건이지만 6일
        FW.cr_decide(entry, "2026-12-01")
        self.assertIsNone(entry["decision"])
        entry["rows"] = [cr_trade(k, -0.01, per_day=10) for k in range(95)]                        # 10일째 첫 거래 = 91번째
        FW.cr_decide(entry, "2026-12-08")
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("FAIL", 91))

    def test_status_shows_progress_and_the_bar_based_caveat(self):
        text = FW.render({"CR_rebound": {"rows": [], "decision": None}, "last_run": "2026-11-13"})
        self.assertIn("급락 반등 — 진행 중 (0 / 30건, 0 / 10일)", text)
        self.assertIn("봉 기준", text)
        led = {"CR_rebound": {"rows": [cr_trade(k, 0.004) for k in range(7)], "decision": None}, "last_run": "2026-11-20"}
        text = FW.render(led)
        self.assertIn("진행 중 (7 / 30건, 3 / 10일)", text)
        self.assertIn("7/30건 3/10일", FW.summary(led))


class CrashReboundVariants(unittest.TestCase):
    """고문 권고 변형 3개: 기준 규칙과 같은 계산에 낙폭 기준·보유 시간만 다르다."""
    START = AM.to_min(FW.CRV_START)

    def bars(self, drop, start_min=None, drop_at=20, after=60):
        closes = [100.0] * drop_at + [100.0 * (1 + drop)] + [100.0 * (1 + drop) * 1.01] * after
        return five_min(closes, self.START + 60 if start_min is None else start_min)

    def test_same_numbers_as_the_advisors_rule_give_the_same_trades(self):
        rng = random.Random(3)
        for _ in range(40):
            closes, px = [], 100.0
            for _k in range(400):
                px *= 1 + rng.choice([0.0, 0.002, -0.002, -0.03, 0.01, -0.015])
                closes.append(px)
            bars = five_min(closes, 10_000_000)
            for hole in sorted(rng.sample(range(30, 390), 6), reverse=True):      # 빠진 봉도 섞는다
                del bars[hole]
            done, _pending = AM.cr_events(bars, 0.0010)
            self.assertEqual(FW.crv_events(bars, 0.0010, AM.CR_DROP, AM.CR_HOLD), done)

    def test_each_variant_changes_exactly_one_number(self):
        by_key = {v["key"]: v for v in FW.CR_VARIANTS}
        self.assertEqual(sorted(by_key), ["CR_drop5", "CR_drop6", "CR_hold4h"])
        self.assertEqual((by_key["CR_drop5"]["drop"], by_key["CR_drop5"]["hold"]), (-0.05, AM.CR_HOLD))
        self.assertEqual((by_key["CR_drop6"]["drop"], by_key["CR_drop6"]["hold"]), (-0.06, AM.CR_HOLD))
        self.assertEqual((by_key["CR_hold4h"]["drop"], by_key["CR_hold4h"]["hold"]), (AM.CR_DROP, 48))
        self.assertGreater(FW.CRV_START, FW.CR_START)                              # 기준 규칙보다 늦게 등록됐다

    def test_a_deeper_drop_enters_more_variants(self):
        def keys(drop):
            rows = FW.crv_rows("2026-11-20", lambda m, s, e: self.bars(drop) if m == "KRW-SOL" else five_min([100.0] * 90, self.START + 60))
            return sorted(k for k, v in rows.items() if v)
        self.assertEqual(keys(-0.045), ["CR_hold4h"])
        self.assertEqual(keys(-0.055), ["CR_drop5", "CR_hold4h"])
        self.assertEqual(keys(-0.065), ["CR_drop5", "CR_drop6", "CR_hold4h"])
        self.assertEqual(keys(-0.030), [])

    def test_hold_length_and_cost_follow_the_variant(self):
        rows = FW.crv_rows("2026-11-20", lambda m, s, e: self.bars(-0.065) if m == "KRW-SOL" else [])
        short, long_ = rows["CR_drop6"][0], rows["CR_hold4h"][0]
        self.assertEqual(short["exit_min"] - short["entry_min"], 60)
        self.assertEqual(long_["exit_min"] - long_["entry_min"], 240)
        self.assertEqual(short["market"], "KRW-SOL")
        self.assertAlmostEqual(short["net"], (1 + short["gross"]) * (1 - 0.0010) / (1 + 0.0010) - 1)   # SOL 비용 편도 0.10%

    def test_only_entries_after_the_start_count_and_bars_are_fetched_once_per_coin(self):
        asked = []

        def fetch(m, start, end):
            asked.append((m, start, end))
            return self.bars(-0.065, start_min=self.START - 300) if m == "KRW-BTC" else []
        rows = FW.crv_rows("2026-11-20", fetch)
        self.assertEqual(rows, {"CR_drop5": [], "CR_drop6": [], "CR_hold4h": []})                    # 시작 전에 진입한 거래는 세지 않는다
        self.assertEqual(asked, [(m, FW.CRV_START - FW.CR_WARMUP, AM.parse_utc("2026-11-20T00:00")) for m in AM.CR_COST])
        asked.clear()
        self.assertEqual(FW.crv_rows("2026-11-14", fetch), {"CR_drop5": [], "CR_drop6": [], "CR_hold4h": []})
        self.assertEqual(asked, [])                                                                   # 시작일 전에는 시세도 받지 않는다

    def test_update_appends_decides_once_and_survives_a_failed_fetch(self):
        def good(m, s, e):
            return self.bars(-0.065) if m == "KRW-SOL" else five_min([100.0] * 90, self.START + 60)
        base = dict(h2_inputs=lambda: ({}, {}, ["x"]), h3_fetch=lambda m, d: {}, cr_fetch=lambda s, e: {m: ([], None) for m in AM.CR_COST},
                    surge_fetch=lambda m: [])
        ledger, _ = FW.update({}, "2026-11-20", crv_fetch=good, **base)
        self.assertEqual({k: len(v["rows"]) for k, v in ledger["CR_variants"].items()}, {"CR_drop5": 1, "CR_drop6": 1, "CR_hold4h": 1})
        self.assertIsNone(ledger["CR_variants"]["CR_drop5"]["decision"])
        before = copy.deepcopy(ledger["CR_variants"])

        def broken(m, s, e):
            raise TimeoutError("조회 실패")
        ledger, warnings = FW.update(ledger, "2026-11-21", crv_fetch=broken, **base)
        self.assertEqual(ledger["CR_variants"], before)                                               # 못 받으면 장부를 건드리지 않는다
        self.assertTrue(any("급락 반등 변형" in w for w in warnings))
        rows = [cr_trade(k, 0.01) for k in range(40)]                                                 # 하루 3건씩 → 30건째에 10일
        entry = FW.cr_decide({"rows": rows, "decision": None}, "2026-12-30")
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("PASS", 30))    # 판정 기준은 기준 규칙과 같다

    def test_a_changed_value_is_a_warning_once_and_then_only_a_note(self):
        """전체 검토 18번: 같은 값 차이가 매주 경고로 다시 올라오면 그 뒤 모든 실행이 실패로 끝난다."""
        def good(m, s, e):
            return self.bars(-0.065) if m == "KRW-SOL" else five_min([100.0] * 90, self.START + 60)
        base = dict(h2_inputs=lambda: ({}, {}, ["x"]), h3_fetch=lambda m, d: {}, cr_fetch=lambda s, e: {m: ([], None) for m in AM.CR_COST},
                    surge_fetch=lambda m: [])
        ledger, _ = FW.update({}, "2026-11-20", crv_fetch=good, **base)
        ledger["CR_variants"]["CR_drop5"]["rows"][0]["net"] += 0.01                                   # 장부 값과 다시 계산한 값이 달라졌다
        kept = ledger["CR_variants"]["CR_drop5"]["rows"][0]["net"]
        mine = lambda ws: [w for w in ws if "다시 계산한 값" in w]
        ledger, warnings = FW.update(ledger, "2026-11-27", crv_fetch=good, **base)
        self.assertEqual(len(mine(warnings)), 1)                                                      # 처음 한 번은 경고
        self.assertIn("급락 반등 변형 CR_drop5: 다시 계산한 값이 장부와 다른 거래 1건", mine(warnings)[0])
        self.assertEqual(ledger["CR_variants"]["CR_drop5"]["rows"][0]["net"], kept)                   # 장부 값은 그대로
        ledger = json.loads(json.dumps(ledger))                                                       # 파일에 썼다 읽은 것과 같게
        ledger, warnings = FW.update(ledger, "2026-12-04", crv_fetch=good, **base)
        self.assertEqual(mine(warnings), [])                                                          # 같은 차이는 다시 경고하지 않는다
        self.assertEqual(len(ledger["mismatch_seen"]["급락 반등 변형 CR_drop5"]), 1)
        text = FW.render(ledger)
        self.assertIn("## 알려진 값 차이", text)
        self.assertIn("- 급락 반등 변형 CR_drop5: 1건 (SOL ", text)
        ledger["CR_variants"]["CR_drop6"]["rows"][0]["net"] -= 0.02                                   # 다른 곳에서 새 차이가 생겼다
        ledger, warnings = FW.update(ledger, "2026-12-11", crv_fetch=good, **base)
        self.assertEqual(len(mine(warnings)), 1)                                                      # 새 차이는 바로 알린다
        self.assertIn("CR_drop6", mine(warnings)[0])
        self.assertNotIn("## 알려진 값 차이", FW.render(FW.update({}, "2026-11-13", **offline())[0]))   # 차이가 없으면 그 절도 없다

    def test_two_years_without_enough_trades_ends_as_expired_not_failed(self):
        thin = {"rows": [cr_trade(k, -0.05) for k in range(5)], "decision": None}
        self.assertIsNone(FW.cr_decide(thin, "2028-11-13", FW.CRV_START)["decision"])                 # 730일째까지는 기다린다
        FW.cr_decide(thin, "2028-11-15", FW.CRV_START)
        self.assertEqual((thin["decision"]["verdict"], thin["decision"]["trades"]), ("EXPIRED", 5))
        self.assertIsNone(FW.cr_decide({"rows": [], "decision": None}, "2030-01-01")["decision"])     # 시작 시각을 안 주면 만료도 없다
        ledger, _ = FW.update({"CR_variants": {"CR_drop6": thin}}, "2026-11-13", **offline())
        self.assertIn("| −6% | 1시간에 6% 넘게 빠지면 60분 보유 | 5 / 30건, 2 / 10일 | -5.00% | -5.00% | 0% | -5.00% | 0 / 5건 | 만료(표본 부족) |", FW.render(ledger))
        self.assertIn("−6% 만료(표본 부족)", FW.summary(ledger))

    def test_trades_after_the_deadline_cannot_fill_the_sample(self):
        """제3세션 지적(2026-11-13): 기한 뒤에 들어온 거래로 30건·10일을 채워도 PASS 가 아니라 EXPIRED."""
        early = [cr_trade(k, 0.01) for k in range(27)]                                                # 하루 3건 × 9일 (기한 안)
        late = [cr_trade(3 * 800 + k, 0.01) for k in range(3)]                                        # 시작 800일째 (기한 730일 밖)
        entry = FW.cr_decide({"rows": early + late, "decision": None}, "2029-03-01", FW.CR_START)
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("EXPIRED", 27))
        filled = [cr_trade(k, 0.01) for k in range(30)]                                               # 기한 안에 30건·10일
        entry = FW.cr_decide({"rows": filled + late, "decision": None}, "2029-03-01", FW.CR_START)
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("PASS", 30))    # 실행이 늦었어도 그 자료로 판정
        same = FW.cr_decide({"rows": early + late, "decision": None}, "2029-03-01")                   # 기한이 없으면(시작 시각 없음) 전부 센다
        self.assertEqual(same["decision"]["trades"], 30)

    def test_status_shows_the_variants_and_what_is_still_waiting(self):
        ledger, _ = FW.update({}, "2026-11-13", **offline())
        text = FW.render(ledger)
        self.assertIn("## 급락 반등 변형", text)
        for v in FW.CR_VARIANTS:
            self.assertIn("| %s | %s | 0 / 30건, 0 / 10일 | - | - | - | - | 0 / 0건 | 진행 중 |" % (v["name"], v["rule"]), text)
        self.assertIn("고문 정의(2026-11-13 확정)", text)
        self.assertIn("## 대기 중\n\n- 없음", text)                                                  # 알트 동반 급락은 등록부(registry.json)로 옮겼다
        self.assertIn("급락 반등 변형: −5% 0건, −6% 0건, 4시간 보유 0건", FW.summary(ledger))


class VerdictHold(unittest.TestCase):
    """고문 판정(2026-11-14 두 번째): 값이 달라진 거래가 판정에 쓰는 표본에 걸리면 그 항목의 판정을 보류로 표시한다."""

    def variant_update(self, ledger, today, recomputed):
        real = FW.crv_rows
        FW.crv_rows = lambda day, fetch=None: {v["key"]: (recomputed if v["key"] == "CR_drop5" else []) for v in FW.CR_VARIANTS}
        try:
            return FW.update(ledger, today, **offline())
        finally:
            FW.crv_rows = real

    def test_a_changed_trade_before_the_verdict_holds_the_verdict(self):
        rows = [cr_trade(k, 0.01) for k in range(29)]                                # 판정 전(29건)
        ledger = {"CR_variants": {"CR_drop5": {"rows": list(rows), "decision": None}}}
        changed = [dict(rows[3], net=rows[3]["net"] + 0.02)]                          # 4번째 거래의 값이 달라졌다
        ledger, warnings = self.variant_update(ledger, "2026-12-07", changed + [cr_trade(k, 0.01) for k in range(29, 40)])
        entry = ledger["CR_variants"]["CR_drop5"]
        self.assertEqual(len(entry["rows"]), 40)                                      # 거래는 계속 쌓인다
        self.assertEqual(entry["rows"][3]["net"], rows[3]["net"])                     # 장부 값은 그대로
        self.assertEqual(entry["hold"]["since"], "2026-12-07")
        self.assertIsNone(entry["decision"])                                          # 30건·10일이 찼어도 판정하지 않는다
        self.assertEqual(len([w for w in warnings if "다시 계산한 값" in w]), 1)
        text = FW.render(ledger)
        self.assertIn("| 진행 중 · 판정 보류(값 차이) |", text)
        self.assertIn("## 판정 보류", text)
        self.assertIn("- 급락 반등 변형 −5%: 2026-12-07 부터 — 값 차이: ", text)
        self.assertIn("판정 보류(값 차이): 급락 반등 변형 −5%", FW.summary(ledger))
        ledger = json.loads(json.dumps(ledger))
        ledger, warnings = self.variant_update(ledger, "2026-12-14", changed)         # 다음 주: 같은 차이 → 경고는 없고 보류는 그대로
        self.assertEqual(([w for w in warnings if "다시 계산한 값" in w], ledger["CR_variants"]["CR_drop5"]["decision"]), ([], None))
        hold = ledger["CR_variants"]["CR_drop5"]["hold"]
        self.assertEqual(hold["diffs"], [{"what": hold["diffs"][0]["what"], "found": "2026-12-07", "ledger": {"net": rows[3]["net"], "exit_min": rows[3]["exit_min"]},
                                          "recomputed": {"net": rows[3]["net"] + 0.02, "exit_min": rows[3]["exit_min"]}}])   # 달라진 값의 원본이 남는다
        self.assertIn("장부 exit_min=", FW.render(ledger))
        for bad in (("급락 반등 변형 −5%", "  "), ("급락 반등 변형 −6%", "고문 판정")):   # 사유가 없거나, 보류가 걸려 있지 않은 항목
            with self.assertRaises(ValueError):
                FW.release_hold(ledger, bad[0], bad[1], "2026-12-20")
        FW.release_hold(ledger, "급락 반등 변형 −5%", "고문 판정 2026-12-20: 거래소의 시세 정정으로 확인", "2026-12-20")   # 고문 판정이 있어 해제한다
        hold = ledger["CR_variants"]["CR_drop5"]["hold"]
        self.assertEqual(hold["released"], {"on": "2026-12-20", "reason": "고문 판정 2026-12-20: 거래소의 시세 정정으로 확인"})
        self.assertEqual((hold["since"], len(hold["diffs"])), ("2026-12-07", 1))      # 보류 기록은 지우지 않고 해제 기록을 덧붙인다
        ledger, warnings = self.variant_update(ledger, "2026-12-21", changed)
        entry = ledger["CR_variants"]["CR_drop5"]
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("PASS", 30))   # 해제 뒤에 판정한다
        text = FW.render(ledger)
        self.assertNotIn("판정 보류(값 차이) |", text)
        self.assertIn("## 해제된 보류", text)
        self.assertIn("- 급락 반등 변형 −5%: 2026-12-07 보류(값 차이: ", text)
        self.assertIn("→ 2026-12-20 해제 — 고문 판정 2026-12-20: 거래소의 시세 정정으로 확인", text)
        again = [dict(rows[7], net=rows[7]["net"] - 0.03)]                            # 그 뒤 표본 안에서 또 다른 차이가 드러났다
        ledger, warnings = self.variant_update(ledger, "2026-12-28", again)
        entry = ledger["CR_variants"]["CR_drop5"]
        self.assertEqual((entry["hold"]["since"], entry["hold"]["released"]), ("2026-12-28", None))   # 새로 보류를 걸고
        self.assertEqual([h["since"] for h in entry["hold_history"]], ["2026-12-07"])                # 지난 보류 기록은 남긴다
        self.assertEqual(len(FW.holds(ledger, released=True)), 1)

    def test_a_second_change_during_a_hold_is_added_to_the_same_record(self):
        """제3세션 지적(2026-11-17): 보류가 걸린 뒤에 드러난 차이는 원본이 버려졌다."""
        rows = [cr_trade(k, 0.01) for k in range(29)]
        ledger = {"CR_variants": {"CR_drop5": {"rows": list(rows), "decision": None}}}
        first = [dict(rows[3], net=rows[3]["net"] + 0.02)]
        ledger, _ = self.variant_update(ledger, "2026-12-07", first)
        second = first + [dict(rows[7], net=rows[7]["net"] - 0.05)]                    # 일주일 뒤 다른 거래도 달라졌다
        ledger, warnings = self.variant_update(ledger, "2026-12-14", second)
        hold = ledger["CR_variants"]["CR_drop5"]["hold"]
        self.assertEqual((hold["since"], [d["found"] for d in hold["diffs"]]), ("2026-12-07", ["2026-12-07", "2026-12-14"]))   # 보류는 그대로, 원본만 늘어난다
        self.assertEqual((hold["diffs"][1]["ledger"]["net"], hold["diffs"][1]["recomputed"]["net"]), (rows[7]["net"], rows[7]["net"] - 0.05))
        self.assertNotIn("hold_history", ledger["CR_variants"]["CR_drop5"])
        self.assertEqual(len([w for w in warnings if "다시 계산한 값" in w]), 1)       # 새 차이는 한 번 경고로 알린다
        ledger, warnings = self.variant_update(ledger, "2026-12-21", second)          # 같은 차이가 또 잡혀도 다시 적지 않는다
        self.assertEqual(len(ledger["CR_variants"]["CR_drop5"]["hold"]["diffs"]), 2)
        self.assertIn("다시 계산", FW.render(ledger))

    def test_after_the_verdict_only_a_change_inside_its_sample_holds_it(self):
        rows = [cr_trade(k, 0.01) for k in range(40)]
        entry = FW.cr_decide({"rows": list(rows), "decision": None}, "2026-12-01", FW.CRV_START)
        self.assertEqual(entry["decision"]["trades"], 30)
        ledger = {"CR_variants": {"CR_drop5": entry}}
        outside = [dict(rows[35], net=rows[35]["net"] - 0.03)]                        # 판정에 쓰지 않은 36번째 거래
        ledger, warnings = self.variant_update(ledger, "2026-12-07", outside)
        self.assertEqual(len([w for w in warnings if "다시 계산한 값" in w]), 1)       # 알리기는 한다
        self.assertIsNone(ledger["CR_variants"]["CR_drop5"].get("hold"))              # 판정 대상이 아니므로 보류하지 않는다
        inside = [dict(rows[10], net=rows[10]["net"] - 0.03)]                         # 판정에 쓴 11번째 거래
        ledger, warnings = self.variant_update(ledger, "2026-12-14", inside)
        self.assertEqual(ledger["CR_variants"]["CR_drop5"]["hold"]["since"], "2026-12-14")
        self.assertEqual(ledger["CR_variants"]["CR_drop5"]["decision"]["verdict"], "PASS")   # 적힌 판정은 지우지 않는다
        self.assertIn("| 통과 · 판정 보류(값 차이) |", FW.render(ledger))

    def test_a_changed_period_among_the_first_twenty_holds_the_kimchi_verdict(self):
        rows = [row(k, 0.01) for k in range(FW.H2_DECIDE_AT)]
        real = FW.h2_rows
        args = offline()
        args["h2_inputs"] = lambda: ({}, {}, [])
        try:
            FW.h2_rows = lambda data, bn, last: [dict(rows[5], strat=0.5)] + rows[6:]        # 6번째 기간의 값이 달라졌다
            ledger, warnings = FW.update({"H2_kimchi": {"rows": list(rows), "decision": None}}, "2026-12-28", **args)
        finally:
            FW.h2_rows = real
        h2 = ledger["H2_kimchi"]
        self.assertEqual((h2["rows"][5]["strat"], h2["decision"]), (0.01, None))      # 장부 값은 그대로, 20기간이 찼어도 판정하지 않는다
        self.assertEqual(h2["hold"]["why"], "값 차이: %s" % rows[5]["date"])
        self.assertIn("## H2 김치 프리미엄 되돌림 — 진행 중 (20 / 20기간) · 판정 보류(값 차이)", FW.render(ledger))

    def test_a_kimchi_difference_explained_by_a_delisting_is_recorded_without_a_hold(self):
        """고문 판정 2026-11-24 ② 3: ew 만 다르고 구성 종목이 목록에서 빠졌으면(또는 구성 목록이 없는 옛 기간이면) 보류하지 않고 적어만 둔다."""
        old = dict(row(1, 0.01, ew=0.02), members=["KRW-A01", "KRW-B02", "KRW-GONE"])
        new = dict(old, ew=0.03)
        here = {"KRW-A01", "KRW-B02", "KRW-C03"}
        self.assertEqual(FW.listing_reason(old, new, here), "비교 기준선(ew) 구성 종목이 지금 상장 목록에 없음: GONE")
        self.assertIsNone(FW.listing_reason(old, new, here | {"KRW-GONE"}))                             # 구성 종목이 다 있는데 값이 다르면 설명되지 않는다
        self.assertEqual(FW.listing_reason(row(1, 0.01, ew=0.02), row(1, 0.01, ew=0.03), here), "비교 기준선(ew)만 다름 — 구성 종목 목록이 없는 옛 기간")
        self.assertIsNone(FW.listing_reason(old, dict(new, strat=0.5), here))                           # 전략 수익이 달라졌으면 상장 목록 탓으로 보지 않는다
        self.assertIsNone(FW.listing_reason(old, dict(new, btc=0.5), here))
        self.assertIsNone(FW.listing_reason(old, dict(new, picks=["KRW-B02"]), here))
        self.assertEqual(FW.listing_reason(old, dict(new, strat=0.5, picks=["KRW-B02"]), here - {"KRW-A01"}), "선정 종목이 지금 상장 목록에 없음: A01")
        self.assertIsNone(FW.listing_reason(old, new, None))                                            # 지금 목록을 모르면 가리지 않는다
        rows = [dict(row(k, 0.01, ew=0.02), members=["KRW-A01", "KRW-GONE"]) for k in range(FW.H2_DECIDE_AT)]
        real, args = FW.h2_rows, offline()
        args["h2_inputs"] = lambda: ({"KRW-A01": {}, "KRW-USDT": {}}, {}, [])                           # GONE 은 지금 자료에 없다
        try:
            FW.h2_rows = lambda data, bn, last: [dict(rows[5], ew=0.05), dict(rows[7], ew=0.07)] + rows[8:]
            ledger, warnings = FW.update({"H2_kimchi": {"rows": list(rows), "decision": None}}, "2026-12-28", **args)
            h2 = ledger["H2_kimchi"]
            self.assertFalse([w for w in warnings if w.startswith("H2")])                               # 실패로 알리지 않는다
            self.assertIsNone(h2.get("hold"))
            self.assertIsNotNone(h2["decision"])                                                        # 20기간이 찼으니 판정은 그대로 한다
            self.assertEqual((h2["rows"][5]["ew"], h2["rows"][7]["ew"]), (0.02, 0.02))                  # 장부 값은 그대로
            self.assertEqual(sorted(h2["listing_diffs"]), [rows[5]["date"], rows[7]["date"]])
            self.assertEqual(h2["listing_diffs"][rows[5]["date"]], {"found": "2026-12-28", "why": "비교 기준선(ew) 구성 종목이 지금 상장 목록에 없음: GONE"})
            text = FW.render(ledger)
            self.assertIn("  - H2 %s: 상장 목록 변화로 본다 — 비교 기준선(ew) 구성 종목이 지금 상장 목록에 없음: GONE (보류하지 않음, 2026-12-28 발견)" % rows[5]["date"], text)
            self.assertNotIn("판정 보류(값 차이)", text)
            args["h2_inputs"] = lambda: ({"KRW-A01": {}, "KRW-GONE": {}}, {}, [])                       # 구성 종목이 다 있는데 값이 다르면 지금처럼 보류한다
            FW.h2_rows = lambda data, bn, last: [dict(rows[5], ew=0.05)] + rows[6:]
            held, warnings = FW.update({"H2_kimchi": {"rows": list(rows), "decision": None}}, "2026-12-28", **args)
            self.assertEqual(held["H2_kimchi"]["hold"]["why"], "값 차이: %s" % rows[5]["date"])
            self.assertTrue([w for w in warnings if w.startswith("H2")])
            self.assertIsNone(held["H2_kimchi"]["decision"])
        finally:
            FW.h2_rows = real

    def test_the_release_command_appends_a_record_and_needs_a_reason(self):
        rows = [cr_trade(k, 0.01) for k in range(29)]
        ledger = {"CR_variants": {"CR_drop5": {"rows": list(rows), "decision": None}}}
        ledger, _ = self.variant_update(ledger, "2026-12-07", [dict(rows[3], net=rows[3]["net"] + 0.02)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "ledger.json")
            path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = FW.main(["--out", tmp, "--release-hold", "급락 반등 변형 −5%", "--today", "2026-12-20"])     # 사유가 없다
            self.assertEqual(code, 2)
            self.assertIsNone(json.loads(path.read_text(encoding="utf-8"))["CR_variants"]["CR_drop5"]["hold"]["released"])
            with contextlib.redirect_stdout(buf):
                code = FW.main(["--out", tmp, "--release-hold", "급락 반등 변형 −5%", "--reason", "고문 판정 2026-12-20", "--today", "2026-12-20"])
            self.assertEqual(code, 0)
            saved = json.loads(path.read_text(encoding="utf-8"))["CR_variants"]["CR_drop5"]["hold"]
            self.assertEqual((saved["since"], saved["released"]), ("2026-12-07", {"on": "2026-12-20", "reason": "고문 판정 2026-12-20"}))
            self.assertIn("## 해제된 보류", Path(tmp, "STATUS.md").read_text(encoding="utf-8"))
            with contextlib.redirect_stdout(buf):
                code = FW.main(["--out", tmp, "--release-hold", "급락 반등 변형 −5%", "--reason", "한 번 더", "--today", "2026-12-21"])
            self.assertEqual(code, 2)                                                 # 이미 해제된 보류는 다시 해제할 것이 없다

    def test_nothing_is_held_when_nothing_changed(self):
        ledger, _ = FW.update({}, "2026-11-13", **offline())
        self.assertEqual(FW.holds(ledger), [])
        self.assertNotIn("## 판정 보류", FW.render(ledger))
        self.assertNotIn("판정 보류", FW.summary(ledger))


class MissedRuns(unittest.TestCase):
    """'며칠 지났나' 가 아니라 '가장 최근 예약일의 실행이 기록에 있나' 를 본다 (제3세션 지적 2026-11-13)."""

    def ledger(self, day, **over):
        out = {"last_run": day, "H2_kimchi": {"updated": day}, "H3_trend": {"as_of": day}, "CR_rebound": {"updated": day},
               "BTC_surge": {"updated": day}, "CR_variants": {v["key"]: {"updated": day} for v in FW.CR_VARIANTS}, "observation": {"as_of": day},
               "candidates": {"A-1": {"updated": day, "decision": None}, "D-0": {"updated": "2026-01-01", "decision": {"verdict": "FAIL"}}}}
        out.update(over)
        return out

    def test_daily_schedule_checks_yesterday_and_keeps_monthly_separate(self):
        self.assertEqual(FW.expected_runs("2026-11-17", daily=True), ("2026-11-16", None))
        self.assertEqual(FW.expected_runs("2026-11-19", daily=True), ("2026-11-18", None))
        self.assertEqual(FW.expected_runs("2026-12-02", daily=True), ("2026-12-01", "2026-12-01"))
        self.assertTrue(FW.stale(self.ledger("2026-11-16"), [], "2026-11-19", daily=True))
        self.assertEqual(FW.stale(self.ledger("2026-11-18"), [], "2026-11-19", daily=True), [])
        weekly_ok = self.ledger("2026-11-18", H2_kimchi={"updated": "2026-11-16"})                     # 김치 프리미엄은 주 1회: 월요일(11/16)에 반영됐으면 된다
        self.assertEqual(FW.stale(weekly_ok, [], "2026-11-19", daily=True), [])
        broken = self.ledger("2026-11-18", H2_kimchi={"updated": "2026-11-11"})                       # 이번 주 월요일 이후로 반영되지 못했다
        self.assertEqual(FW.stale(broken, [], "2026-11-19", daily=True), ["김치 프리미엄: 2026-11-16(월) 이후 갱신되지 못함 — 마지막 갱신 2026-11-11"])
        late = self.ledger("2026-11-18")
        late["CR_rebound"]["updated"] = "2026-11-17"                                                  # 다른 항목은 어제 실행에서 갱신됐어야 한다
        self.assertEqual(FW.stale(late, [], "2026-11-19", daily=True), ["급락 반등: 2026-11-18 이후 갱신되지 못함 — 마지막 갱신 2026-11-17"])

    def test_daily_schedule_checks_the_monthly_search_and_the_weekly_check_in_their_own_records(self):
        """등록은 매월 1일(공식 기록 scan_log), 매주 월요일은 등록 없는 점검(weekly_discovery 의 기록) — 고문 판정 2026-11-18."""
        self.assertIsNone(FW.expected_check("2026-11-23"))                                             # 첫 주간 점검일 당일에는 아직 기다리지 않는다
        self.assertEqual((FW.expected_check("2026-11-24"), FW.expected_check("2026-12-02")), ("2026-11-23", "2026-11-30"))
        self.assertEqual(dt.date.fromisoformat(FW.FIRST_SEARCH_WEEKLY).weekday(), 0)
        ok = self.ledger("2026-11-23")
        self.assertEqual(FW.stale(ok, [], "2026-11-24", daily=True, check_log=[{"run": "2026-11-17"}]),
                         ["주간 탐색 점검: 2026-11-23(월) 예약 실행이 기록에 없음 — 마지막 실행 2026-11-17"])
        self.assertEqual(FW.stale(ok, [], "2026-11-24", daily=True, check_log=[{"run": "2026-11-23"}]), [])
        self.assertEqual(FW.stale(ok, [{"run": "2026-11-23"}], "2026-11-24", daily=True),                         # 공식 기록에 있어도 점검 기록이 따로 있어야 한다
                         ["주간 탐색 점검: 2026-11-23(월) 예약 실행이 기록에 없음 — 마지막 실행 없음"])
        self.assertEqual(FW.stale(self.ledger("2026-12-01"), [{"run": "2026-11-12"}], "2026-12-02", daily=True, check_log=[{"run": "2026-11-30"}]),
                         ["월간 탐색: 2026-12-01 예약 실행이 기록에 없음 — 마지막 실행 2026-11-12"])
        self.assertEqual(FW.stale(self.ledger("2026-12-01"), [{"run": "2026-12-01"}], "2026-12-02", daily=True, check_log=[{"run": "2026-11-30"}]), [])
        self.assertEqual(FW.stale(self.ledger("2026-11-16"), [], "2026-11-24"), ["주간 추적: 2026-11-23(월) 예약 실행이 기록에 없음 — 마지막 실행 2026-11-16"])   # 옛 주간 일정은 점검 기록을 보지 않는다

    def test_the_observation_table_and_same_day_registrations(self):
        old = self.ledger("2026-11-18", observation={"as_of": "2026-11-16"})                          # 관찰 표만 이틀째 못 받았다
        self.assertEqual(FW.stale(old, [], "2026-11-19", daily=True), ["관찰 표: 2026-11-18 이후 갱신되지 못함 — 마지막 갱신 2026-11-16"])
        ledger = self.ledger("2026-11-23")
        ledger["candidates"] = {}
        registry = [{"id": "D-202611-01", "status": "ACTIVE", "registered": "2026-11-23", "start": "2026-11-24"},   # 어제 등록, 오늘부터 채점: 아직 기다리지 않는다
                    {"id": "D-202611-02", "status": "ACTIVE", "registered": "2026-11-22", "start": "2026-11-23"}]   # 그제 등록: 어제 실행에서 채점됐어야 한다
        self.assertEqual(FW.stale(ledger, [], "2026-11-24", registry, daily=True, check_log=[{"run": "2026-11-23"}]),
                         ["후보 D-202611-02: 2026-11-23 이후 갱신되지 못함 — 마지막 갱신 없음"])

    def test_expected_dates(self):
        self.assertEqual(FW.expected_runs("2026-11-15"), (None, None))                                 # 첫 예약 전
        self.assertEqual(FW.expected_runs("2026-11-16"), (None, None))                                 # 예약일 당일에는 아직 기다리지 않는다
        self.assertEqual(FW.expected_runs("2026-11-18"), ("2026-11-16", None))
        self.assertEqual(FW.expected_runs("2026-11-23"), ("2026-11-16", None))
        self.assertEqual(FW.expected_runs("2026-12-01"), ("2026-11-30", None))
        self.assertEqual(FW.expected_runs("2026-12-02"), ("2026-11-30", "2026-12-01"))
        self.assertEqual(FW.expected_runs("2027-01-01"), ("2026-12-28", "2026-12-01"))

    def test_up_to_date_records_raise_nothing(self):
        self.assertEqual(FW.stale(self.ledger("2026-11-11"), [], "2026-11-15"), [])                    # 첫 예약 전에는 볼 것이 없다
        self.assertEqual(FW.stale(self.ledger("2026-11-16"), [], "2026-11-18"), [])
        self.assertEqual(FW.stale(self.ledger("2026-11-30"), [{"run": "2026-11-12"}, {"run": "2026-12-01"}], "2026-12-02"), [])

    def test_a_missed_first_weekly_run_is_caught(self):
        out = FW.stale(self.ledger("2026-11-11"), [], "2026-11-18")
        self.assertEqual(out, ["주간 추적: 2026-11-16(월) 예약 실행이 기록에 없음 — 마지막 실행 2026-11-11"])
        self.assertTrue(FW.stale({}, [], "2026-11-18")[0].endswith("마지막 실행 없음"))

    def test_a_missing_or_old_monthly_record_is_caught(self):
        fresh = self.ledger("2026-11-30")
        self.assertEqual(FW.stale(fresh, [], "2026-12-02"), ["월간 탐색: 2026-12-01 예약 실행이 기록에 없음 — 마지막 실행 없음"])
        self.assertEqual(FW.stale(fresh, [{"run": "2026-11-12"}], "2026-12-02"),
                         ["월간 탐색: 2026-12-01 예약 실행이 기록에 없음 — 마지막 실행 2026-11-12"])

    def test_an_item_that_failed_inside_a_run_is_caught_even_though_the_run_is_recorded(self):
        ledger = self.ledger("2026-11-16", H2_kimchi={"updated": "2026-11-11"})
        ledger["candidates"]["A-1"]["updated"] = "2026-11-09"
        ledger["CR_variants"]["CR_drop6"] = {}
        registry = [{"id": "A-1", "status": "ACTIVE", "registered": "2026-11-13"}, {"id": "D-0", "status": "ACTIVE", "registered": "2026-01-01"}]
        out = FW.stale(ledger, [], "2026-11-18", registry)
        self.assertEqual(out, ["김치 프리미엄: 2026-11-16(월) 이후 갱신되지 못함 — 마지막 갱신 2026-11-11",
                               "급락 반등 변형 −6%: 2026-11-16(월) 이후 갱신되지 못함 — 마지막 갱신 없음",
                               "후보 A-1: 2026-11-16(월) 이후 갱신되지 못함 — 마지막 갱신 2026-11-09"])       # 판정이 끝난 D-0 은 보지 않는다

    def test_a_registered_candidate_that_never_got_a_ledger_entry_is_caught(self):
        """2026-11-14 검토: 첫 채점부터 실패해 장부에 없는 후보는 점검에 잡히지 않았다."""
        ledger = self.ledger("2026-11-16")
        ledger["candidates"] = {}
        registry = [{"id": "A-202611-01", "status": "ACTIVE", "registered": "2026-11-13"},
                    {"id": "D-202611-09", "status": "ACTIVE", "registered": "2026-11-17"}]            # 월요일 실행 뒤에 등록된 후보는 아직 기다리지 않는다
        self.assertEqual(FW.stale(ledger, [], "2026-11-18", registry),
                         ["후보 A-202611-01: 2026-11-16(월) 이후 갱신되지 못함 — 마지막 갱신 없음"])
        self.assertEqual(FW.stale(ledger, [], "2026-11-18"), [])                                     # 등록부가 비어 있으면 볼 후보가 없다

    def test_command_line_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ledger.json").write_text(json.dumps(self.ledger("2026-11-16")), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                self.assertEqual(FW.main(["--out", d, "--stale-check", "--today", "2026-11-18"]), 0)
                self.assertEqual(FW.main(["--out", d, "--stale-check", "--today", "2026-11-25"]), 1)
            self.assertIn("2026-11-23(월) 예약 실행이 기록에 없음", buf.getvalue())


class ScheduledWorkflows(unittest.TestCase):
    """예약 워크플로는 실패하면 알리고, 빠진 실행은 따로 점검한다."""
    WF = ROOT / ".github" / "workflows"

    def test_each_scheduled_run_reports_its_own_failure_through_the_checked_sender(self):
        for name in ("forward-watch.yml", "discover.yml", "watchdog.yml"):
            text = (self.WF / name).read_text(encoding="utf-8")
            last = text.split("      - ")[-1]
            self.assertIn("failure()", last.split("\n")[1], name)                                    # 마지막 단계가 실패 알림이다
            if name != "watchdog.yml":
                self.assertIn("cancelled()", last.split("\n")[1], name)                              # 시간 초과로 취소돼도 알린다
            self.assertIn("scalp/research/tg_send.py", last, name)
            self.assertNotIn("curl", text, name)                                                      # 응답을 버리는 전송은 쓰지 않는다
            self.assertNotIn("sendMessage", text, name)

    def steps(self, name):
        """워크플로의 단계들 → {단계 이름: 단계 글}."""
        text = (self.WF / name).read_text(encoding="utf-8")
        out = {}
        for block in text.split("\n      - ")[1:]:
            block = "\n".join(ln for ln in block.split("\n") if not ln.strip().startswith("#"))
            out[block.split("\n")[0].replace("name: ", "").strip()] = block
        return text, out

    def test_the_tracker_is_scheduled_three_times_a_day_and_later_slots_skip_a_finished_day(self):
        """고문 판정 2026-11-17 ①: 예약을 하루 세 번 걸고, 그날(UTC) 이미 성공했으면 시세를 받지 않고 바로 끝난다."""
        text, steps = self.steps("forward-watch.yml")
        crons = [ln.split('"')[1] for ln in text.splitlines() if ln.strip().startswith("- cron:")]
        self.assertEqual(crons, ["25 0 * * *", "25 4 * * *", "25 8 * * *"])
        gate = steps["Already done today (scheduled runs only)"]
        self.assertIn("if: github.event_name == 'schedule'", gate)                                    # 사람이 돌린 실행은 묻지 않고 돈다
        self.assertIn("forward_watch.py --done-today", gate)
        self.assertIn('echo "skip=1"', gate)
        names = list(steps)
        after = names[names.index("Already done today (scheduled runs only)") + 1:]
        self.assertEqual(after[-1], "Telegram on failure (only when both secrets exist)")
        for name in after[:-1]:                                                                       # 건너뛴 날에는 받지도, 올리지도, 보내지도, 실패로 끝나지도 않는다
            cond = [ln for ln in steps[name].split("\n") if ln.strip().startswith("if:")]
            self.assertEqual(len(cond), 1, name)
            self.assertIn("steps.gate.outputs.skip != '1'", cond[0], name)
        self.assertIn("steps.run.outputs.code != '0'", steps["Fail if any candidate could not be updated"])
        release = steps["Release a hold (manual runs with an item name only)"]                       # 사람이 직접 돌릴 때만, 항목 이름을 적었을 때만 (고문 판정 2026-11-24 ②)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.release_hold != ''", release)
        self.assertIn('forward_watch.py --release-hold "$HOLD_NAME" --reason "$HOLD_REASON"', release)   # 입력은 환경 변수로만 넘긴다
        self.assertIn("HOLD_NAME: ${{ inputs.release_hold }}", release)
        self.assertNotIn("${{ inputs", release.split("run:")[1])
        self.assertLess(names.index("Release a hold (manual runs with an item name only)"), names.index("Update ledger and status"))

    def test_one_summary_a_day_and_the_last_slot_reports_even_a_failed_day(self):
        text, steps = self.steps("forward-watch.yml")
        cond = next(ln for ln in steps["Telegram daily summary (only when both secrets exist)"].split("\n") if ln.strip().startswith("if:"))
        self.assertIn("steps.run.outputs.code == '0'", cond)                                          # 예약 실행은 경고 없이 끝났을 때만
        self.assertIn("github.event_name != 'schedule'", cond)                                        # 사람이 돌린 실행은 결과와 무관하게
        last = cond.split("github.event.schedule == '")[1].split("'")[0]
        self.assertEqual(last, "25 8 * * *")                                                          # 그날의 마지막 예약은 실패했어도 보낸다
        self.assertIn('cron: "%s"' % last, text)
        self.assertNotIn("--report-only", text)                                                       # 추적 워크플로는 탐색을 돌리지 않는다
        self.assertIn("forward_watch.py --summary", steps["Telegram daily summary (only when both secrets exist)"])

    def test_only_the_monthly_search_registers_and_does_not_queue_behind_the_tracker(self):
        """고문 판정 2026-11-18: 주간 등록을 뺀다. 등록은 매월 1일 예약(과 사람이 register 를 고른 실행)만."""
        text, steps = self.steps("discover.yml")
        self.assertIn('cron: "7 2 1 * *"', text)
        self.assertIn('cron: "37 3 * * 1"', text)
        pick = steps["Select register or diagnostic run"]
        cond = pick.split("then")[0]
        self.assertIn('[ "$CRON" = "7 2 1 * *" ]', cond)                                              # 월간 예약만
        self.assertIn('[ "$MANUAL_MODE" = "register" ]', cond)                                        # 사람이 고르면
        self.assertNotIn("37 3 * * 1", cond)
        self.assertNotIn('"$EVENT_NAME" = "schedule"', cond)                                          # 예약이라고 다 등록하지 않는다
        register, rest = pick.split("else")
        self.assertIn("args=--register", register)
        self.assertIn("out=scalp/research/forward\"", register)
        self.assertIn("args=--report-only", rest)                                                     # 주간 예약, push, 사람이 고른 diagnostic 은 별도 폴더
        self.assertIn("weekly_discovery", rest)
        self.assertIn("default: diagnostic", text)                                                    # 그냥 누르면 등록하지 않는다
        tracker = (self.WF / "forward-watch.yml").read_text(encoding="utf-8")
        group = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("group:")]
        self.assertEqual(group, ["group: research-search"])                                           # 추적과 같은 묶음이면 기다리다 취소될 수 있다
        self.assertIn("group: research-records", tracker)
        for body in (text, tracker):                                                                  # 서로 밀리면 다시 받아서 올린다
            self.assertIn("for try in 1 2 3; do", body)
            self.assertIn("git pull --rebase origin main && git push origin HEAD:main", body)

    def test_the_daily_watchdog_writes_nothing(self):
        text = (self.WF / "watchdog.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "47 5 * * *"', text)
        self.assertIn("--daily-schedule", text)
        self.assertIn("contents: read", text)
        self.assertIn("forward_watch.py --stale-check", text)
        for word in ("git push", "git commit", "contents: write"):
            self.assertNotIn(word, text, word)

    def test_first_scheduled_dates_match_the_cron_lines(self):
        self.assertEqual(dt.date.fromisoformat(FW.FIRST_WEEKLY).weekday(), 0)                         # 월요일
        self.assertEqual(FW.FIRST_MONTHLY[-2:], "01")
        self.assertIn('cron: "7 2 1 * *"', (self.WF / "discover.yml").read_text(encoding="utf-8"))


class TelegramSender(unittest.TestCase):
    """전달이 확인되지 않으면 실패로 끝나야 한다 (제3세션 지적 2026-11-13)."""
    SPEC = importlib.util.spec_from_file_location("tg_send", ROOT / "scalp" / "research" / "tg_send.py")

    def setUp(self):
        self.TG = importlib.util.module_from_spec(self.SPEC)
        self.SPEC.loader.exec_module(self.TG)

    def test_delivery_needs_http_200_and_ok_true(self):
        ok = lambda url, data, timeout: (200, '{"ok": true, "result": {}}')
        self.assertEqual(self.TG.send("hi", "TOKEN", ["111", "222"], ok), [])
        refused = lambda url, data, timeout: (401, '{"ok": false, "error_code": 401, "description": "Unauthorized"}')
        self.assertEqual(self.TG.send("hi", "TOKEN", ["12345"], refused), ["채팅 …45: HTTP 401 Unauthorized"])
        odd = lambda url, data, timeout: (200, "<html>proxy</html>")
        self.assertEqual(self.TG.send("hi", "TOKEN", ["12345"], odd), ["채팅 …45: HTTP 200"])       # 200 이어도 ok 가 없으면 실패
        calls = []

        def one_bad(url, data, timeout):
            calls.append(data)
            return (400, '{"ok": false, "description": "Bad Request: chat not found"}') if b"chat_id=999" in data else (200, '{"ok": true}')
        self.assertEqual(self.TG.send("hi", "TOKEN", ["111", "999"], one_bad), ["채팅 …99: HTTP 400 Bad Request: chat not found"])
        self.assertEqual(len(calls), 2)                                                               # 틀린 번호는 다시 보내지 않는다

    def test_busy_or_timeout_is_retried_once_and_the_token_is_never_printed(self):
        seq = iter([(429, '{"ok": false, "description": "Too Many Requests"}'), (200, '{"ok": true}')])
        self.assertEqual(self.TG.send("hi", "TOKEN", ["111"], lambda u, d, t: next(seq)), [])

        def boom(url, data, timeout):
            raise TimeoutError("https://api.telegram.org/botSECRET-TOKEN/sendMessage timed out")
        out = self.TG.send("hi", "SECRET-TOKEN", ["111"], boom)
        self.assertEqual(out, ["채팅 …11: TimeoutError"])
        self.assertNotIn("SECRET-TOKEN", " ".join(out))

    def test_command_line(self):
        saved = {k: os.environ.pop(k, None) for k in ("TG_TOKEN", "TG_CHATS")}
        try:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                self.assertEqual(self.TG.main(["--text", "hi"]), 0)                                   # 설정 전에는 조용히 넘어간다
                os.environ.update(TG_TOKEN="SECRET-TOKEN", TG_CHATS="111, 222")
                self.assertEqual(self.TG.main(["--text", "hi"], post=lambda u, d, t: (200, '{"ok": true}')), 0)
                self.assertEqual(self.TG.main(["--text", "hi"], post=lambda u, d, t: (403, '{"ok": false, "description": "Forbidden"}')), 1)
                self.assertEqual(self.TG.main(["--text", "  "], post=lambda u, d, t: (200, '{"ok": true}')), 1)
            text = buf.getvalue()
            self.assertIn("telegram not configured", text)
            self.assertIn("telegram 2곳 전달 확인", text)
            self.assertIn("telegram 전달 실패 — 채팅 …11: HTTP 403 Forbidden", text)
            self.assertNotIn("SECRET-TOKEN", text)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


class AdvisorCandidate(unittest.TestCase):
    """알트 동반 급락 반등 — 고문 정의(2026-11-13)를 등록부에 그대로 적었는지."""

    def entry(self):
        reg = json.loads((ROOT / "scalp" / "research" / "forward" / "registry.json").read_text(encoding="utf-8"))
        return next(c for c in reg if c["id"] == "A-202611-01")

    def test_the_registered_definition_is_the_advisors(self):
        c = self.entry()
        self.assertEqual((c["event"], c["hold"], c["move"], c["vol_x"]), ("CRASH", 4, 0.03, 3.0))     # 15분 −3%·거래대금 3배, 4봉 보유
        self.assertEqual(c["cond"][1:], ["btc_ret_1h", "<=", -0.01821])                               # 같은 시각 BTC 4봉 수익률 ≤ −1.821%
        self.assertEqual(c["cond"][1:], list(DISC.ADVISOR_ALT["cond"]))
        self.assertEqual(sorted(c["universe"]), sorted(DISC.REPRO_UNIVERSE))                          # 고문의 고정 20종목
        self.assertEqual((c["cost"], c["cost_other"]), ({"KRW-BTC": 0.0007, "KRW-ETH": 0.0007}, 0.001))
        self.assertEqual((c["registered"], c["start"]), ("2026-11-13", "2026-11-14"))                 # 등록 다음 날 00:00 UTC 부터
        self.assertEqual((c["verdict"], c["expire_days"], c["status"], c["engine"]), ("ex_best_day", 730, "ACTIVE", "3"))
        self.assertIn(c["engine"], DISC.SAME_TRADES)                                                  # 4판에서도 그대로 채점한다

    def world(self, btc_drop):
        """crash_panel 과 같은 알트 급락에, 같은 봉에서 BTC 도 btc_drop 만큼 빠졌다가 4봉에 걸쳐 되돌아오는 세계."""
        q0, panel = crash_panel(90)
        btc, price = {}, 100.0
        for k in range(90 * 96):
            o, step = price, k % 120
            if step == 0 and k:
                price = o * (1 + btc_drop)
            elif 1 <= step <= 4:
                price = o * (1 - btc_drop / 4.0)
            btc[q0 + k] = (o, max(o, price), min(o, price), price, 1e9)
        return q0, {"KRW-BTC": DISC.Bars(btc), "KRW-AAA": panel["KRW-AAA"]}

    def test_the_tracker_scores_it_only_when_btc_fell_too_and_only_from_the_start_day(self):
        q0, panel = self.world(-0.02)                                                                 # BTC 4봉 수익률 −2% ≤ −1.821%
        c = dict(self.entry(), universe=["KRW-AAA", "KRW-BTC"], registered=DISC.q_day(q0 + 59 * 96), start=DISC.q_day(q0 + 60 * 96))
        ledger, warnings = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[c], **offline(panel))
        entry = ledger["candidates"]["A-202611-01"]
        self.assertEqual(len(entry["rows"]), 24)                                                      # 30일 동안 30시간마다 한 건
        self.assertGreaterEqual(min(r["entry_q"] for r in entry["rows"]), DISC.day_q(c["start"]) + 1)
        self.assertEqual({r["market"] for r in entry["rows"]}, {"KRW-AAA"})
        self.assertIn("| A-202611-01 | 15분 −3% 이하 급락(거래대금 3배) → 1시간 보유, btc_ret_1h <= -0.01821 |", FW.render(ledger))
        self.assertFalse([w for w in warnings if "후보" in w])
        q0, calm = self.world(-0.01)                                                                  # BTC 가 1%만 빠졌으면 사건이 아니다
        ledger, _ = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[c], **offline(calm))
        self.assertEqual(ledger["candidates"]["A-202611-01"]["rows"], [])


class SecondCandidate(unittest.TestCase):
    """A-202611-02 (고문 판정 2026-11-22 ②·③): 급락 소진 반등. 주 대상(큰 5종목)·비교용(나머지 14종목)·체결 관찰 세 항목으로 등록한다."""
    BIG = ["KRW-ADA", "KRW-DOGE", "KRW-ETH", "KRW-SOL", "KRW-XRP"]

    def entries(self):
        reg = json.loads((ROOT / "scalp" / "research" / "forward" / "registry.json").read_text(encoding="utf-8"))
        by = {c["id"]: c for c in reg}
        return by["A-202611-02"], by["A-202611-02-비교"], by["A-202611-02-체결"]

    def test_the_registered_definitions_are_the_advisors(self):
        main, comp, obs = self.entries()
        for c in (main, comp, obs):
            self.assertEqual((c["event"], c["hold"], c["move"], c["vol_x"]), ("CRASH", 4, 0.03, 0.0))        # 15분 −3%, 거래대금 조건 없음, 4봉 보유
            self.assertEqual((c["cost"], c["cost_other"], c["no_trade"]), ({"KRW-BTC": 0.0007, "KRW-ETH": 0.0007}, 0.001, ["KRW-BTC"]))   # BTC 는 지표용
            self.assertEqual((c["registered"], c["start"], c["status"], c["engine"]), ("2026-11-22", "2026-11-23", "ACTIVE", "4"))
            self.assertIn(c["engine"], DISC.SAME_TRADES)
        for c in (main, comp):
            self.assertEqual([x[1:] for x in c["conds"]], [["ret_4h", "<=", -0.0592], ["btc_ret_1d", "<=", -0.0173]])   # 반올림해 고정한 문턱
            self.assertEqual((c["verdict"], c["expire_days"]), ("ex_best_day", 730))                               # 1단계는 A-202611-01 과 같은 방식
            self.assertEqual([x["period"] for x in c["past"]], ["2017–22", "2023–24", "2025–26"])
            self.assertTrue(all(x["by"] == "tracker" and x["n"] > 0 for x in c["past"]))
        self.assertEqual((main["role"], comp["role"], obs["role"]), ("main", "compare", "observe"))
        self.assertEqual(sorted(main["universe"]), sorted(["KRW-BTC"] + self.BIG))                              # 주 대상 = 큰 5종목
        self.assertEqual(sorted(obs["universe"]), sorted(main["universe"]))
        self.assertIsNone(obs.get("cond"))                                                                       # 체결 관찰은 조건 없이 급락 전부
        self.assertNotIn("conds", obs)
        rest = set(comp["universe"]) - {"KRW-BTC"}
        self.assertEqual(len(rest), 14)
        self.assertFalse(rest & set(self.BIG))
        self.assertEqual(rest | set(main["universe"]), set(DISC.REPRO_UNIVERSE))                                 # 합치면 고문의 고정 20종목
        self.assertEqual([(x["net"], x["daymean"]) for x in main["past"]], [(0.01322, 0.01517), (0.02553, 0.0192), (0.0174, 0.01999)])   # 추적기 관례로 다시 낸 값
        self.assertEqual((main["risk"]["sim_mdd"], main["risk"]["sim_worst_day"], main["risk"]["sim_skipped"]), (-0.114, -0.0697, 0))

    def world(self, alt_drop, btc_drop, days=90, every=120):
        """알트가 every 봉마다 한 봉에 alt_drop 만큼 급락한 뒤 4봉에 걸쳐 2% 되돌리고, 같은 봉에서 BTC 도 btc_drop 만큼 빠졌다가 되돌아오는 세계."""
        q0 = DISC.day_q("2026-09-01")
        out = {}
        for m, drop, back in (("KRW-BTC", btc_drop, None), ("KRW-ETH", alt_drop, 0.02)):
            rows, price = {}, 100.0
            for k in range(days * 96):
                o, step = price, k % every
                if step == 0 and k:
                    price = o * (1 + drop)
                elif 1 <= step <= 4:
                    price = o * (1 + (back / 4.0 if back is not None else -drop / 4.0))
                rows[q0 + k] = (o, max(o, price), min(o, price), price, 1e9)
            out[m] = DISC.Bars(rows)
        return q0, out

    def run_one(self, c, alt_drop, btc_drop, **kw):
        q0, panel = self.world(alt_drop, btc_drop, **kw)
        c = dict(c, universe=["KRW-BTC", "KRW-ETH"], registered=DISC.q_day(q0 + 59 * 96), start=DISC.q_day(q0 + 60 * 96))
        ledger, warnings = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[c], **offline(panel))
        self.assertFalse([w for w in warnings if "후보" in w])
        return ledger, ledger["candidates"][c["id"]]

    def test_the_main_entry_scores_only_when_both_conditions_hold_and_never_trades_btc(self):
        main = self.entries()[0]
        ledger, entry = self.run_one(main, -0.07, -0.02)                                                # 그 종목 4시간 −7%, BTC 24시간 −2%
        self.assertEqual(len(entry["rows"]), 24)
        self.assertEqual({r["market"] for r in entry["rows"]}, {"KRW-ETH"})
        self.assertEqual(self.run_one(main, -0.05, -0.02)[1]["rows"], [])                               # 4시간 −5% 는 −5.92% 에 못 미친다
        self.assertEqual(self.run_one(main, -0.07, -0.01)[1]["rows"], [])                               # BTC 24시간 −1% 는 −1.73% 에 못 미친다
        self.assertEqual(self.run_one(main, -0.07, -0.04)[1]["rows"] and {r["market"] for r in self.run_one(main, -0.07, -0.04)[1]["rows"]}, {"KRW-ETH"})   # BTC 가 −4% 급락해도 BTC 는 사지 않는다
        self.assertEqual([p[0] for p in entry["past"]], ["2017–22", "2023–24", "2025–26"])              # 과거 값 세 기간이 장부로 옮겨진다
        self.assertEqual(min(p[1] for p in entry["past"]), 0.01322)
        self.assertEqual(entry["role"], "main")
        text = FW.render(ledger)
        self.assertIn("| A-202611-02 | 15분 −3% 이하 급락(거래대금 조건 없음) → 1시간 보유, ret_4h <= -0.0592 그리고 btc_ret_1d <= -0.0173 [주 대상: 큰 5종목] |", text)
        self.assertIn("진행 중 (주 대상)", text)
        self.assertIn("## 위험과 배분 (표시만)", text)
        self.assertIn("| A-202611-02 | -1.17% / -4.95% / -20.06% (2023–26, 90건) | -11.4% / -7.0% (2017-09 ~ 2026-11) |", text)
        self.assertIn("ADA 2024-12-03 13:45 -43.9% → -7.5%", text)                                      # 가장 나빴던 사건 5건

    def test_the_observe_entry_records_every_crash_and_is_never_judged(self):
        obs = self.entries()[2]
        ledger, entry = self.run_one(obs, -0.05, 0.0, every=60)                                         # 조합 조건을 못 채운 급락도 전부 적는다
        self.assertGreaterEqual(len(entry["rows"]), DISC.FWD_MIN_N)
        self.assertGreaterEqual(len({r["day"] for r in entry["rows"]}), DISC.FWD_MIN_DAYS)
        self.assertIsNone(entry["decision"])                                                             # 30건·10일을 넘겨도 판정하지 않는다
        self.assertFalse(entry.get("stage2") or entry.get("stage3"))
        judged, e2 = self.run_one(dict(obs, role="main"), -0.05, 0.0, every=60)                          # 같은 거래라도 관찰 전용이 아니면 판정이 난다
        self.assertIsNotNone(e2["decision"])
        text = FW.render(ledger)
        self.assertIn("판정 없음 (체결 관찰 전용 — 판정 없음)", text)
        self.assertNotIn("후보 A-202611-02-체결", "\n".join(FW.render_stage2(ledger) + FW.render_stage3(ledger)))
        self.assertIn("후보 A-202611-02-체결", "\n".join(FW.render_fills(ledger)))                        # 체결 추정 표에는 나온다
        self.assertIn("후보 A-202611-02-체결", [x[0] for x in FW.staged_items(judged)])                   # 판정하는 항목은 2·3단계에 들어간다
        self.assertNotIn("후보 A-202611-02-체결", [x[0] for x in FW.staged_items(ledger)])                # 관찰 전용은 빠진다
        self.assertIn("후보 A-202611-02-체결", [x[0] for x in FW.fill_items(ledger)])

    def test_the_allocation_convention_uses_five_slots_and_skips_when_full(self):
        def tr(m, entry, net):
            return {"market": m, "entry_q": entry, "exit_q": entry + 4, "net": net}
        same = [tr("KRW-A%d" % i, 100, 0.05) for i in range(7)]                                          # 같은 봉에 7종목 → 5개만 잡는다
        self.assertEqual(FW.slot_result(same)[:2], (5, 2))
        self.assertAlmostEqual(FW.slot_result(same)[2], 0.05)                                            # 다섯 칸 × 5% ÷ 5칸 = 자금의 5%
        late = same + [tr("KRW-Z1", 103, 0.10), tr("KRW-Z2", 104, 0.10)]                                 # 103 봉은 아직 꽉 차 있고, 104 봉 시가에 자리가 빈다
        self.assertEqual(FW.slot_result(late)[:2], (6, 3))
        self.assertAlmostEqual(FW.slot_result(late)[2], 0.05 + 0.02)
        self.assertEqual(FW.slot_result([]), (0, 0, 0.0))
        self.assertEqual(FW.SLOTS, 5)


class ConditionLog(unittest.TestCase):
    """전진 조건 기록 (고문 판정 2026-11-22 ⑤): 급락 사건마다 조건 값을 forward/conditions.csv 에 붙이고 현황에 조건별 표를 싣는다. 관찰용."""

    def world(self, crash_day="2026-11-21", bar=40):
        q0, n = DISC.day_q("2026-11-15"), 12 * 96
        hit = DISC.day_q(crash_day) + bar
        out = {}
        for m, drop in (("KRW-BTC", 0.0), ("KRW-ETH", -0.05)):
            rows, price = {}, 100.0
            for k in range(n):
                o = price
                if q0 + k == hit:
                    price = o * (1 + drop)
                rows[q0 + k] = (o, max(o, price), min(o, price), price, 1e9)
            out[m] = DISC.Bars(rows)
        return out

    def run_cond(self, today, world, path, prev=None, write=True, markets=("KRW-BTC", "KRW-ETH")):
        asked = []

        def loader(s, e, want):
            asked.append((s, e, tuple(want)))
            return {m: world[m] for m in want}

        def get(url):                                                                                # 5분봉: 어느 덩어리든 100 원으로 평평하다
            end = AM.to_min(AM.parse_utc(url.split("to=")[1].replace("%3A", ":")))
            return [{"candle_date_time_utc": dt.datetime.fromtimestamp(t * 60, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                     "opening_price": 100.0, "high_price": 100.0, "low_price": 100.0, "trade_price": 100.0} for t in range(end - 1000, end, 5)]
        got = FW.conditions(today, prev, path=path, write=write, list_markets=lambda: list(markets) + ["KRW-ZZZ"], panel_loader=loader, get=get)
        return got, asked

    def test_events_are_appended_once_from_the_start_day_and_rescanned_from_the_day_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / FW.COND_FILE
            world = self.world()
            dry, _ = self.run_cond("2026-11-22", world, path, write=False)
            self.assertEqual((dry["events"], dry["added"]), (1, 1))
            self.assertFalse(path.exists())                                                          # 시험 실행은 파일을 건드리지 않는다
            got, asked = self.run_cond("2026-11-22", world, path)
            self.assertEqual((got["since"], got["through"], got["events"], got["added"]), ("2026-11-21", "2026-11-22", 1, 1))
            self.assertEqual(asked, [(DISC.day_q("2026-11-21") - FW.COND_WARMUP_Q, DISC.day_q("2026-11-22"), ("KRW-BTC", "KRW-ETH"))])
            self.assertEqual(sorted(got["missing"]), sorted(m for m in DISC.REPRO_UNIVERSE if m not in ("KRW-BTC", "KRW-ETH")))
            rows = FW.CL.load(path)
            self.assertEqual([(r["종목"], r["구분"], r["날"]) for r in rows], [("ETH", "알트만", "2026-11-21")])
            self.assertEqual(rows[0]["① 60분"], FW.CL.text(FW.CL.net("KRW-ETH", 0.0)))
            self.assertEqual([c["key"] for c in got["table"]], list(FW.CL.KEYS))
            before = path.read_text(encoding="utf-8")
            again, asked = self.run_cond("2026-11-24", world, path, prev=got)
            self.assertEqual((again["events"], again["added"]), (1, 0))                               # 같은 사건은 다시 적지 않는다
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertEqual(asked[0][0], DISC.day_q("2026-11-21") - FW.COND_WARMUP_Q)                # 지난 실행(11-22)의 하루 전부터 다시 훑는다
            later, asked = self.run_cond("2026-11-26", world, path, prev=again)
            self.assertEqual(asked[0][0], DISC.day_q("2026-11-23") - FW.COND_WARMUP_Q)
        with tempfile.TemporaryDirectory() as tmp:                                                    # 시작일(연구 자료 끝 다음 날) 앞의 사건은 적지 않는다
            early, _ = self.run_cond("2026-11-22", self.world("2026-11-20"), Path(tmp) / FW.COND_FILE)
            self.assertEqual(early["events"], 0)
            late, _ = self.run_cond("2026-11-22", self.world("2026-11-21", bar=94), Path(tmp) / FW.COND_FILE)   # 신호 뒤 60분이 아직 안 지났다
            self.assertEqual(late["events"], 0)
            next_day, _ = self.run_cond("2026-11-23", self.world("2026-11-21", bar=94), Path(tmp) / FW.COND_FILE, prev=late)
            self.assertEqual(next_day["events"], 1)                                                   # 다음 실행에서 줍는다
        self.assertEqual(FW.CL.START_DAY, "2026-11-21")

    def test_events_without_five_minute_bars_are_counted_and_a_short_file_is_rebuilt(self):
        """고문 판정 2026-11-24 ① 2: 5분봉이 없어 못 적은 사건 수를 보이고, 파일의 줄이 장부보다 적으면 시작일부터 다시 훑는다."""
        world = self.world()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / FW.COND_FILE
            loader = lambda s, e, want: {m: world[m] for m in want}
            none = FW.conditions("2026-11-22", None, path=path, list_markets=lambda: ["KRW-BTC", "KRW-ETH"], panel_loader=loader, get=lambda url: [])
            self.assertEqual((none["events"], len(none["no5m"]), none["rescanned"]), (0, 1, False))     # 5분봉을 못 받았다
            self.assertTrue(none["no5m"][0].startswith("ETH_20261121T"))
            self.assertIn("5분봉이 없어 적지 못한 사건 1건 (%s)." % none["no5m"][0], FW.render({"last_run": "2026-11-22", "warnings": [], "conditions": none}))
            got, asked = self.run_cond("2026-11-23", world, path, prev=none)
            self.assertEqual((got["events"], got["no5m"]), (1, []))                                     # 다음 실행에서 적히면 못 적은 목록에서 빠진다
            self.assertNotIn("5분봉이 없어", FW.render({"last_run": "2026-11-23", "warnings": [], "conditions": got}))
            keep = dict(got, no5m=["XRP_20261122T0100"])                                                # 훑는 구간을 벗어난 것은 계속 남는다
            later, asked = self.run_cond("2026-11-27", world, path, prev=keep)
            self.assertEqual((later["no5m"], later["rescanned"]), (["XRP_20261122T0100"], False))
            self.assertEqual(asked[0][0], DISC.day_q("2026-11-22") - FW.COND_WARMUP_Q)                  # 지난 실행(11-23)의 하루 전부터
            path.unlink()                                                                               # 파일이 사라졌다 → 장부에 적힌 수보다 적다
            again, asked = self.run_cond("2026-11-28", world, path, prev=later)
            self.assertEqual((again["events"], again["added"], again["rescanned"]), (1, 1, True))
            self.assertEqual(asked[0][0], DISC.day_q("2026-11-21") - FW.COND_WARMUP_Q)                  # 시작일부터 다시

    def test_the_tracker_keeps_the_last_table_when_the_step_fails_and_never_touches_verdicts(self):
        prev = {"since": "2026-11-21", "through": "2026-11-22", "events": 3, "added": 0, "missing": [], "table": [], "past": None}

        def boom(day, old):
            raise RuntimeError("시세 조회 실패")
        ledger, warnings = FW.update({"conditions": prev}, "2026-11-23", cond_log=boom, **offline())
        self.assertEqual(ledger["conditions"], prev)
        self.assertEqual([w for w in warnings if "조건 기록" in w], ["조건 기록: 이번 실행에서 갱신하지 못함 — RuntimeError: 시세 조회 실패"])
        ledger, warnings = FW.update({"conditions": prev}, "2026-11-23", cond_log=lambda day, old: dict(old, through=day, events=4, added=1), **offline())
        self.assertEqual((ledger["conditions"]["through"], ledger["conditions"]["events"]), ("2026-11-23", 4))
        self.assertFalse([w for w in warnings if "조건 기록" in w])
        untouched, _ = FW.update({}, "2026-11-23", **offline())                                       # 함수를 주지 않으면 건드리지 않는다
        self.assertNotIn("conditions", untouched)

    def test_status_shows_the_table_with_past_values_and_hides_small_samples(self):
        cells = [{"key": "1", "label": "1번 BTC 직접", "n": 3, "days": 3, "ok": False, "means": [None] * 4, "up": None, "down": None},
                 {"key": "4", "label": "4번 BTC 조건 없는 알트 급락", "n": 40, "days": 12, "ok": True, "means": [-0.001, -0.002, 0.001, 0.0123], "up": 0.031, "down": -0.022}]
        ledger = {"last_run": "2026-12-20", "warnings": [],
                  "conditions": {"since": "2026-11-21", "through": "2026-12-20", "events": 43, "added": 2, "missing": ["KRW-WAVES"], "table": cells,
                                 "past": {"period": "2017-09 ~ 2026-11-19", "cells": {"1": [1663, 804, -0.0002], "4": [7039, 1822, -0.002]}}}}
        text = FW.render(ledger)
        self.assertIn("## 조건별 전진 표 — 관찰용, 판정과 등록에 쓰지 않는다", text)
        self.assertIn("지금까지 43건, 자료 반영 2026-12-20 (상장 목록에 없어 뺀 종목: WAVES).", text)
        self.assertIn("| 1번 BTC 직접 | 3 | 3 | 표본 부족 | - | - | - | - | - | 1663 / 804 · -0.02% |", text)       # 10일 미만이면 값을 싣지 않는다
        self.assertIn("| 4번 BTC 조건 없는 알트 급락 | 40 | 12 | -0.10% | -0.20% | +0.10% | +1.23% | +3.10% | -2.20% | 7039 / 1822 · -0.20% |", text)
        self.assertIn("| 과거 (2017-09 ~ 2026-11-19): 사건 / 날 · 60분 |", text)
        self.assertNotIn("조건별", FW.summary(ledger))                                                 # 텔레그램 요약에는 싣지 않는다
        ledger["conditions"]["past"] = None
        self.assertIn("| 1번 BTC 직접 | 3 | 3 | 표본 부족 | - | - | - | - | - | - |", FW.render(ledger))
        del ledger["conditions"]
        self.assertNotIn("조건별 전진 표", FW.render(ledger))

    def test_the_past_table_in_the_repository_matches_the_code(self):
        past = json.loads((ROOT / "scalp" / "research" / "forward" / FW.COND_PAST).read_text(encoding="utf-8"))
        self.assertEqual([c["key"] for c in past["table"]], list(FW.CL.KEYS))
        self.assertEqual((past["period"], past["events"]), ("2017-09 ~ 2026-11-19", 11222))
        by = {c["key"]: c for c in past["table"]}
        self.assertEqual((by["combo"]["n"], by["combo"]["days"], by["combo-big5"]["n"]), (2148, 384, 515))   # 9년 급락 보고서의 조합 건수와 같다
        self.assertEqual(by["5a"]["n"], 9559)
        self.assertEqual((by["day-first"]["n"], by["day-repeat"]["n"], by["day-first"]["days"]), (5489, 4070, 1886))   # 같은 종목의 그날 첫 급락과 그 뒤 (판정 11/25 ①)


class MarketFlags(unittest.TestCase):
    """유의·주의 표시 기록 (고문 판정 2026-11-24 ③ 4-③)."""

    def listing(self, flags=None, drop=()):
        flags = flags or {}
        asked = []

        def get(url):
            asked.append(url)
            quiet = {k: False for k in FW.FLAG_CAUTION}
            out = [{"market": "BTC-ETH", "market_event": {"warning": True, "caution": dict(quiet)}}]   # 원화 시장이 아닌 것은 보지 않는다
            for m in DISC.REPRO_UNIVERSE + ("KRW-NEW",):
                if m in drop:
                    continue
                warning, caution = flags.get(m, (False, ()))
                out.append({"market": m, "market_event": {"warning": warning, "caution": dict(quiet, **{c: True for c in caution})}})
            return out
        return get, asked

    def test_each_run_appends_the_days_flags_for_the_fixed_coins_once(self):
        get, asked = self.listing({"KRW-WAVES": (True, ()), "KRW-KAVA": (True, ("TRADING_VOLUME_SOARING", "GLOBAL_PRICE_DIFFERENCES")),
                                   "KRW-ALGO": (False, ("PRICE_FLUCTUATIONS",)), "KRW-NEW": (True, ())}, drop=("KRW-IQ",))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "forward" / FW.FLAG_FILE
            got = FW.market_flags("2026-11-26", path=path, get=get)
            self.assertTrue(asked[0].endswith("/v1/market/all?is_details=true"))
            self.assertEqual(got, {"day": "2026-11-26", "added": 20, "unlisted": ["KRW-IQ"],
                                   "flagged": [{"market": "KRW-WAVES", "warning": True, "caution": []},
                                               {"market": "KRW-KAVA", "warning": True, "caution": ["GLOBAL_PRICE_DIFFERENCES", "TRADING_VOLUME_SOARING"]},
                                               {"market": "KRW-ALGO", "warning": False, "caution": ["PRICE_FLUCTUATIONS"]}]})   # 고정 20종목만 본다
            first = path.read_text(encoding="utf-8")
            lines = first.splitlines()
            self.assertEqual((lines[0], len(lines)), ("날(UTC),종목,상장,유의,주의", 21))
            self.assertIn("2026-11-26,BTC,1,0,", lines)
            self.assertIn("2026-11-26,WAVES,1,1,", lines)
            self.assertIn("2026-11-26,KAVA,1,1,GLOBAL_PRICE_DIFFERENCES;TRADING_VOLUME_SOARING", lines)
            self.assertIn("2026-11-26,ALGO,1,0,PRICE_FLUCTUATIONS", lines)
            self.assertIn("2026-11-26,IQ,0,,", lines)                                                   # 목록에 없는 종목도 한 줄 남긴다
            self.assertEqual(FW.market_flags("2026-11-26", path=path, get=get)["added"], 0)             # 같은 날 다시 돌아도 또 붙이지 않는다
            self.assertEqual(path.read_text(encoding="utf-8"), first)
            calm, _ = self.listing()
            later = FW.market_flags("2026-11-27", path=path, get=calm)
            self.assertEqual((later["added"], later["flagged"], later["unlisted"]), (20, [], []))
            second = path.read_text(encoding="utf-8")
            self.assertTrue(second.startswith(first))                                                   # 앞에 적은 줄은 그대로 — 뒤에 붙이기만 한다
            self.assertEqual((second.count("\n"), second.count("날(UTC)")), (41, 1))
            dry = Path(tmp) / "dry" / FW.FLAG_FILE
            self.assertEqual(FW.market_flags("2026-11-26", path=dry, write=False, get=get)["added"], 0)  # 시험 실행은 파일을 만들지 않는다
            self.assertFalse(dry.exists())
            broken, _ = self.listing(drop=("KRW-BTC",))
            with self.assertRaises(RuntimeError):                                                       # 목록이 비정상이면 적지 않는다
                FW.market_flags("2026-11-28", path=path, get=broken)
            self.assertEqual(path.read_text(encoding="utf-8"), second)

    def test_the_tracker_warns_when_the_flags_cannot_be_read_and_never_touches_verdicts(self):
        prev = {"day": "2026-11-25", "flagged": [{"market": "KRW-WAVES", "warning": True, "caution": []}], "unlisted": [], "added": 20}

        def boom(day):
            raise RuntimeError("목록 조회 실패")
        ledger, warnings = FW.update({"market_flags": prev}, "2026-11-26", flag_log=boom, **offline())
        self.assertEqual(ledger["market_flags"], prev)                                                 # 못 받으면 지난 값을 그대로 둔다
        self.assertEqual([w for w in warnings if "유의" in w], ["유의·주의 표시: 이번 실행에서 받지 못함 — RuntimeError: 목록 조회 실패"])
        self.assertNotEqual(ledger.get("last_ok"), "2026-11-26")                                       # 경고로 끝나 뒤의 예약이 다시 받는다(소급해 받을 수 없다)
        ledger, warnings = FW.update({"market_flags": prev}, "2026-11-26", flag_log=lambda day: dict(prev, day=day, flagged=[]), **offline())
        self.assertEqual((ledger["market_flags"]["day"], ledger["market_flags"]["flagged"]), ("2026-11-26", []))
        self.assertFalse([w for w in warnings if "유의" in w])
        untouched, _ = FW.update({}, "2026-11-26", **offline())                                       # 함수를 주지 않으면 건드리지 않는다
        self.assertNotIn("market_flags", untouched)
        self.assertIn("flag_log=lambda day: market_flags(day, path=out / FLAG_FILE, write=not a.dry_run)", inspect.getsource(FW.main))

    def test_status_shows_the_flagged_coins_on_one_line(self):
        ledger = {"last_run": "2026-11-26", "warnings": [],
                  "market_flags": {"day": "2026-11-26", "added": 20, "unlisted": ["KRW-IQ"],
                                   "flagged": [{"market": "KRW-WAVES", "warning": True, "caution": []},
                                               {"market": "KRW-KAVA", "warning": True, "caution": ["GLOBAL_PRICE_DIFFERENCES", "NEW_KIND"]},
                                               {"market": "KRW-ALGO", "warning": False, "caution": ["PRICE_FLUCTUATIONS"]}]}}
        text = FW.render(ledger)
        line = [x for x in text.splitlines() if "유의·주의 표시" in x]
        self.assertEqual(len(line), 1)
        self.assertIn("(2026-11-26 기준, 고정 20종목 — 관찰용, 판정과 등록에 쓰지 않는다): "
                      "WAVES 유의 · KAVA 유의, 주의(국내외 가격 차이·NEW_KIND) · ALGO 주의(가격 급등락). 상장 목록에 없는 종목: IQ.", line[0])
        self.assertIn("market_flags.csv", line[0])
        self.assertNotIn("유의", FW.summary(ledger))                                                   # 텔레그램 요약에는 싣지 않는다
        ledger["market_flags"].update(flagged=[], unlisted=[])
        self.assertIn("관찰용, 판정과 등록에 쓰지 않는다): 없음. 날마다", FW.render(ledger))
        del ledger["market_flags"]
        self.assertNotIn("유의·주의 표시", FW.render(ledger))


class SurgeAndSharedH3(unittest.TestCase):
    def test_surge_weeks_are_recorded_once_complete_for_both_coins(self):
        d0 = dt.date(2026, 11, 2)
        rows = [((d0 + dt.timedelta(i)).isoformat(), 100.0 if i < 14 else 110.0, 100.0 if i < 13 else 110.0) for i in range(22)]
        weeks = FW.surge_rows("2026-11-23", lambda m: rows)
        self.assertEqual([w["monday"] for w in weeks], ["2026-11-16"])               # 11/23 주는 다음 월요일 시가가 아직 없다
        self.assertTrue(weeks[0]["surge"])
        ledger, _ = FW.update({}, "2026-11-23", h2_inputs=lambda: ({}, {}, ["x"]), h3_fetch=lambda m, d: {},
                              cr_fetch=lambda s, e: {m: ([], None) for m in AM.CR_COST}, surge_fetch=lambda m: rows, crv_fetch=lambda m, s, e: [])
        again, _ = FW.update(ledger, "2026-11-24", h2_inputs=lambda: ({}, {}, ["x"]), h3_fetch=lambda m, d: {},
                             cr_fetch=lambda s, e: {m: ([], None) for m in AM.CR_COST}, surge_fetch=lambda m: rows, crv_fetch=lambda m, s, e: [])
        self.assertEqual(len(again["BTC_surge"]["rows"]), 1)                         # 같은 주를 두 번 적지 않는다
        self.assertIn("| 급등 뒤 | 1 |", FW.render(again))

    def test_the_two_h3_calculations_agree(self):
        """엔진은 trend_paper 만 쓴다. auto_monitor.h3_state 가 같은 답을 내는지 묶어 둔다."""
        rng = random.Random(4)
        for _ in range(40):
            start, p, data, days = dt.date(2026, 8, 1), 100.0, {}, []
            for k in range(70):
                o = p
                p *= 1 + rng.gauss(0, 0.03)
                d = (start + dt.timedelta(days=k)).isoformat()
                data[d] = (o, p)
                days.append((d, o, p))
            today = (start + dt.timedelta(days=70)).isoformat()
            a, b = TP.signal_for_open(data, today), AM.h3_state(days)
            self.assertEqual(a["on"], b["on"])
            self.assertAlmostEqual(a["average"], b["sma"])
            self.assertAlmostEqual(a["close"], b["close"])


class FrozenRuleFile(unittest.TestCase):
    def sha(self, *parts):
        raw = ROOT.joinpath(*parts).read_bytes().replace(b"\r\n", b"\n")
        return hashlib.sha256(raw).hexdigest()

    def test_the_registered_rule_file_has_not_changed(self):
        """급락 반등·주간 급등 규칙은 고문이 고정했다(수정본 확정 2026-11-11). 파일이 바뀌면 전진 검증이 아니다.
        규칙을 바꿔야 하면 새 후보로 등록하고 시작 시각을 새로 잡는다 — 이 해시만 고치지 않는다."""
        self.assertEqual(self.sha("scalp", "research", "auto_monitor.py"),
                         "791ea2ce0b617352f23d504c94eef05d8fb367e5aa68e46ed1297c54378a8e09")
        self.assertEqual(AM.FORWARD_START, "2026-11-11T00:00")
        self.assertEqual((AM.CR_DROP, AM.CR_LOOK, AM.CR_HOLD, AM.CR_BAR, AM.CR_MIN_N, AM.CR_MIN_DAYS), (-0.04, 12, 12, 5, 30, 10))

    def test_the_signal_needs_a_true_sixty_minute_lookback(self):
        """고문이 확정한 정의: close[i]/close[i-12] - 1 <= -4% 이고 t[i] - t[i-12] 가 정확히 60분."""
        closes = [100.0] * 13 + [95.0] + [97.0] * 20                          # 급락 뒤 97 로 회복(1시간 전 대비 -3%: 새 신호 없음)
        whole = five_min(closes, 1000)
        done, _ = AM.cr_events(whole, 0.0007)
        self.assertEqual([t["entry_min"] for t in done], [1000 + 5 * 14])     # 봉이 다 있으면 사건
        gapped = whole[:6] + [(m + 300, o, c) for m, o, c in whole[6:]]       # 비교 구간 안에 5시간 공백(거래소 점검)
        self.assertEqual(gapped[13][0] - gapped[1][0], 12 * 5 + 300)          # 12봉 전이 실제로는 6시간 전
        self.assertEqual(AM.cr_events(gapped, 0.0007), ([], None))            # 사건이 아니다
        edge = five_min([100.0] * 13 + [96.0] + [96.0] * 20, 1000)            # 정확히 -4.0% 는 신호
        self.assertEqual(len(AM.cr_events(edge, 0.0007)[0]), 1)

    def test_the_h3_signal_file_is_operational_code(self):
        """trend_paper.py 는 H3 실행기가 서버에서 불러 쓴다. 연구 변경에 섞어 고치지 않는다 —
        고쳐야 하면 실행기처럼 검토를 받은 뒤 이 해시를 함께 바꾼다."""
        self.assertEqual(self.sha("scalp", "research", "trend_paper.py"),
                         "c913074c9df9f04c82f04845dbf235105928a03aea5d108962354b527789afbf")


def crash_panel(days, every=120, rebound=0.02):
    """BTC 는 평평하고, 알트 하나가 every 봉(15분봉)마다 한 봉에 −5% 급락(거래대금 5배)한 뒤 다음 4봉에 걸쳐 rebound 만큼 되돌린다."""
    q0 = DISC.day_q("2026-09-01")
    btc = {q0 + k: (100.0, 100.0, 100.0, 100.0, 1e9) for k in range(days * 96)}
    alt, price = {}, 100.0
    for k in range(days * 96):
        o, step, value = price, k % every, 1e9
        if step == 0 and k:
            price, value = o * 0.95, 5e9
        elif 1 <= step <= 4:
            price = o * (1 + rebound / 4.0)
        alt[q0 + k] = (o, max(o, price), min(o, price), price, value)
    return q0, {"KRW-BTC": DISC.Bars(btc), "KRW-AAA": DISC.Bars(alt)}


def offline(panel=None):
    """시세를 받지 않는 update 인자들."""
    return dict(h2_inputs=lambda: ({}, {}, ["x"]), h3_fetch=lambda m, d: {}, cr_fetch=lambda s, e: {m: ([], None) for m in AM.CR_COST},
                surge_fetch=lambda m: [], panel_loader=(lambda s, e, markets: {m: panel[m] for m in markets}) if panel else None,
                crv_fetch=lambda m, s, e: [], list_markets=lambda: (_ for _ in ()).throw(RuntimeError("목록 조회 없음")))


class RegisteredCandidates(unittest.TestCase):
    def cand(self, registered, **kw):
        base = {"id": "D-202610-01", "engine": DISC.ENGINE, "registered": registered, "event": "CRASH", "hold": 4, "cond": None,
                "universe": ["KRW-BTC", "KRW-AAA"], "move": 0.03, "vol_x": 3.0, "cost": {"KRW-BTC": 0.0007}, "cost_other": 0.0010, "status": "ACTIVE"}
        base.update(kw)
        return base

    def cut(self, panel, q0, days):
        """days 일까지만 있는 시세 (그날 실행했을 때 받을 수 있는 만큼)."""
        out = {}
        for m, b in panel.items():
            j = b.window(q0, q0 + days * 96 - 1)[1]
            out[m] = DISC.Bars({b.ts[t]: (b.o[t], b.h[t], 0.0, b.c[t], b.v[t]) for t in range(j)})
        return out

    def test_trades_after_registration_are_scored_and_judged_once(self):
        q0, panel = crash_panel(150)
        reg = DISC.q_day(q0 + 70 * 96)
        ledger, warnings = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[self.cand(reg)], **offline(self.cut(panel, q0, 90)))
        entry = ledger["candidates"]["D-202610-01"]
        self.assertEqual(len(entry["rows"]), 16)                                     # 20일 동안 30시간마다 한 건
        self.assertTrue(all(r["entry_q"] > DISC.day_q(reg) for r in entry["rows"]))
        self.assertIsNone(entry["decision"])
        self.assertIn("진행 중", FW.render(ledger))
        ledger, _ = FW.update(ledger, DISC.q_day(q0 + 120 * 96), registry=[self.cand(reg)], **offline(self.cut(panel, q0, 120)))
        entry = ledger["candidates"]["D-202610-01"]
        self.assertEqual((entry["decision"]["verdict"], entry["decision"]["trades"]), ("PASS", 30))
        text = FW.render(ledger)
        self.assertIn("| D-202610-01 | 15분 −3% 이하 급락(거래대금 3배) → 1시간 보유, 조건 없음 |", text)
        self.assertIn("| 통과 |", text)
        self.assertIn("통과: D-202610-01", FW.summary(ledger))

    def test_nothing_is_downloaded_when_no_candidate_is_open(self):
        def boom(*a):
            raise AssertionError("panel should not be loaded")
        args = offline()
        args["panel_loader"] = boom
        ledger, warnings = FW.update({}, "2026-11-20", registry=[], **args)
        self.assertEqual(ledger["candidates"], {})
        closed = {"candidates": {"D-202610-01": {"rows": [], "decision": {"verdict": "FAIL", "decided_on": "2026-11-01", "trades": 30, "stats": None}}}}
        FW.update(closed, "2026-11-20", registry=[self.cand("2026-10-01")], **args)   # 판정이 끝난 후보는 더 받지 않는다
        self.assertIn("아직 등록된 후보가 없습니다", FW.render(ledger))

    def test_a_failed_download_leaves_candidate_rows_alone(self):
        q0, panel = crash_panel(150)
        reg = DISC.q_day(q0 + 70 * 96)
        ledger, _ = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[self.cand(reg)], **offline(self.cut(panel, q0, 90)))
        before = copy.deepcopy(ledger["candidates"])
        args = offline()
        args["panel_loader"] = lambda s, e, markets: (_ for _ in ()).throw(RuntimeError("조회 실패"))
        ledger, warnings = FW.update(ledger, DISC.q_day(q0 + 100 * 96), registry=[self.cand(reg)], **args)
        self.assertEqual(ledger["candidates"], before)
        self.assertTrue(any("후보 D-202610-01: 이번 실행에서 갱신하지 못함" in w for w in warnings))

    def test_candidates_do_not_share_coins_and_one_failure_does_not_stop_the_other(self):
        """2026-11-14 검토: 후보 둘의 종목이 합쳐져 채점됐고, 하나가 실패하면 전부 멈췄다."""
        q0, panel = crash_panel(120)
        twin = {"KRW-BTC": panel["KRW-BTC"], "KRW-AAA": panel["KRW-AAA"], "KRW-BBB": panel["KRW-AAA"]}   # BBB 도 같은 식으로 급락한다
        reg = DISC.q_day(q0 + 70 * 96)
        one = self.cand(reg, id="D-202610-01", universe=["KRW-BTC", "KRW-AAA"])
        two = self.cand(reg, id="D-202610-02", universe=["KRW-BTC", "KRW-BBB"], event="SURGE")
        asked = []

        def loader(start_q, end_q, markets):
            asked.append(tuple(markets))
            return {m: twin[m] for m in markets}
        args = offline()
        args["panel_loader"] = loader
        ledger, warnings = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[one, two], **args)
        self.assertEqual(sorted(asked), [("KRW-AAA", "KRW-BTC"), ("KRW-BBB", "KRW-BTC")])            # 후보마다 자기 종목만 받는다
        self.assertEqual({r["market"] for r in ledger["candidates"]["D-202610-01"]["rows"]}, {"KRW-AAA"})
        alone, _ = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[one], **args)
        self.assertEqual(ledger["candidates"]["D-202610-01"]["rows"], alone["candidates"]["D-202610-01"]["rows"])   # 다른 후보가 있든 없든 같다

        def flaky(start_q, end_q, markets):
            if "KRW-BBB" in markets:
                raise RuntimeError("조회 실패 (HTTP 404)")                                             # 한 후보의 종목이 상장폐지됐다
            return {m: twin[m] for m in markets}
        args["panel_loader"] = flaky
        ledger, warnings = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[two, one], **args)
        self.assertTrue(ledger["candidates"]["D-202610-01"]["rows"])                                 # 다른 후보는 그대로 채점된다
        self.assertEqual(ledger["candidates"]["D-202610-01"]["updated"], DISC.q_day(q0 + 90 * 96))
        self.assertNotIn("updated", ledger["candidates"]["D-202610-02"])                             # 실패한 후보는 갱신일이 찍히지 않는다
        self.assertEqual([w for w in warnings if "후보" in w], ["후보 D-202610-02: 이번 실행에서 갱신하지 못함 — 조회 실패 (HTTP 404)"])

    # ---- 상장폐지 종목 (고문 판정 2026-11-14)
    def three(self, days=150):
        q0, panel = crash_panel(days)
        flat = DISC.Bars({q: (100.0, 100.0, 100.0, 100.0, 1e9) for q in panel["KRW-BTC"].ts})
        return q0, {"KRW-BTC": panel["KRW-BTC"], "KRW-AAA": panel["KRW-AAA"], "KRW-BBB": panel["KRW-AAA"]}, flat

    def run_with(self, ledger, today, cand, panel, listed, asked=None):
        args = offline(panel)
        if asked is not None:
            args["panel_loader"] = lambda s, e, markets: (asked.append(tuple(markets)), {m: panel[m] for m in markets})[1]
        args["list_markets"] = listed
        return FW.update(ledger, today, registry=[cand], **args)

    def test_a_delisted_coin_stops_making_events_and_the_candidate_goes_on(self):
        q0, panel, _flat = self.three()
        reg = DISC.q_day(q0 + 80 * 96)                                                 # 등록 뒤 10일에 종목당 8건
        cand = self.cand(reg, universe=["KRW-BTC", "KRW-AAA", "KRW-BBB"])
        everything = lambda: ["KRW-BTC", "KRW-AAA", "KRW-BBB", "KRW-ZZZ"]
        day1, day2 = DISC.q_day(q0 + 90 * 96), DISC.q_day(q0 + 100 * 96)
        ledger, warnings = self.run_with({}, day1, cand, self.cut(panel, q0, 90), everything)
        entry = ledger["candidates"]["D-202610-01"]
        self.assertEqual((len(entry["rows"]), entry["delisted"], entry["decision"]), (16, {}, None))   # 두 종목이 8건씩
        old_bbb = [r for r in entry["rows"] if r["market"] == "KRW-BBB"]
        asked = []
        ledger, warnings = self.run_with(ledger, day2, cand, self.cut(panel, q0, 100), lambda: ["KRW-BTC", "KRW-AAA"], asked)
        entry = ledger["candidates"]["D-202610-01"]
        self.assertEqual(asked, [("KRW-AAA", "KRW-BTC")])                              # 상장폐지된 종목의 시세는 더 받지 않는다
        self.assertEqual(entry["delisted"], {"KRW-BBB": day2})
        self.assertEqual([r for r in entry["rows"] if r["market"] == "KRW-BBB"], old_bbb)   # 이미 적힌 거래는 그대로
        self.assertEqual(len([r for r in entry["rows"] if r["market"] == "KRW-AAA"]), 16)   # 남은 종목은 계속 채점
        self.assertFalse(any("D-202610-01" in w for w in warnings))                    # 실패가 아니다 — 현황에만 표시
        text = FW.render(ledger)
        self.assertIn("| 진행 중 · 자료 불완전(BBB %s) |" % day2, text)                  # 현황에 종목과 날짜가 보인다
        self.assertIn("- D-202610-01 자료 불완전: BBB 가 %s 에 상장 목록에서 빠진 것을 확인." % day2, text)
        self.assertIn("D-202610-01 자료 불완전: BBB 상장폐지 (남은 종목으로 계속 채점)", FW.summary(ledger))
        ledger, _ = self.run_with(ledger, DISC.q_day(q0 + 120 * 96), cand, self.cut(panel, q0, 120), everything)   # 목록에 다시 보여도 넣지 않는다
        entry = ledger["candidates"]["D-202610-01"]
        self.assertEqual((entry["delisted"], [r for r in entry["rows"] if r["market"] == "KRW-BBB"]), ({"KRW-BBB": day2}, old_bbb))
        self.assertEqual(entry["decision"]["delisted"], {"KRW-BBB": day2})             # 판정문에도 종목과 날짜가 남는다
        self.assertIn("| 통과 · 자료 불완전(BBB %s) |" % day2, FW.render(ledger))

    def test_a_candidate_that_lost_a_coin_does_not_end_as_a_plain_expiry(self):
        """고문 판정(두 번째): 종목이 빠져 표본이 모자란 것을 '표본 부족 만료' 로 처리하지 않는다."""
        q0, panel, flat = self.three()
        reg = DISC.q_day(q0 + 80 * 96)
        quiet = {"KRW-BTC": panel["KRW-BTC"], "KRW-AAA": flat, "KRW-BBB": flat}       # 사건이 없어 표본이 차지 않는다
        late = DISC.q_day(q0 + 100 * 96)                                              # 기한(10일)이 지난 날
        gone = self.cand(reg, universe=["KRW-BTC", "KRW-AAA", "KRW-BBB"], expire_days=10)
        ledger, _ = self.run_with({}, late, gone, self.cut(quiet, q0, 100), lambda: ["KRW-BTC", "KRW-AAA"])
        dec = ledger["candidates"]["D-202610-01"]["decision"]
        self.assertEqual((dec["verdict"], dec["delisted"]), ("INCOMPLETE", {"KRW-BBB": late}))
        text = FW.render(ledger)
        self.assertIn("| 자료 불완전(BBB %s) |" % late, text)
        self.assertNotIn("만료(표본 부족)", text.split("## 자동 등록 후보")[1].split("## 대기 중")[0])
        whole = self.cand(reg, universe=["KRW-BTC", "KRW-AAA", "KRW-BBB"], expire_days=10)
        ledger, _ = self.run_with({}, late, whole, self.cut(quiet, q0, 100), lambda: ["KRW-BTC", "KRW-AAA", "KRW-BBB"])
        self.assertEqual(ledger["candidates"]["D-202610-01"]["decision"]["verdict"], "EXPIRED")   # 종목이 다 있으면 전과 같이 만료

    def test_an_unusable_market_list_removes_nothing(self):
        q0, panel, _flat = self.three()
        reg = DISC.q_day(q0 + 70 * 96)
        cand = self.cand(reg, universe=["KRW-BTC", "KRW-AAA", "KRW-BBB"])
        for listed in (lambda: [], lambda: ["KRW-AAA"], lambda: (_ for _ in ()).throw(TimeoutError("조회 실패"))):
            asked = []
            ledger, warnings = self.run_with({}, DISC.q_day(q0 + 90 * 96), cand, self.cut(panel, q0, 90), listed, asked)
            self.assertEqual(asked, [("KRW-AAA", "KRW-BBB", "KRW-BTC")])               # 목록이 비었거나 BTC 가 없거나 못 받으면 믿지 않는다
            self.assertEqual(ledger["candidates"]["D-202610-01"]["delisted"], {})

    def test_after_a_delisting_trades_that_appear_in_the_past_are_not_added(self):
        q0, panel, flat = self.three()
        reg = DISC.q_day(q0 + 70 * 96)
        cand = self.cand(reg, universe=["KRW-BTC", "KRW-AAA", "KRW-BBB"])
        quiet = {"KRW-BTC": panel["KRW-BTC"], "KRW-AAA": flat, "KRW-BBB": flat}       # 처음에는 AAA 에 사건이 없었다
        day1, day2, day3 = (DISC.q_day(q0 + d * 96) for d in (90, 100, 110))
        ledger, _ = self.run_with({}, day1, cand, self.cut(quiet, q0, 90), lambda: ["KRW-BTC", "KRW-AAA", "KRW-BBB"])
        ledger, _ = self.run_with(ledger, day2, cand, self.cut(quiet, q0, 100), lambda: ["KRW-BTC", "KRW-AAA"])   # BBB 상장폐지
        self.assertEqual(ledger["candidates"]["D-202610-01"]["rows"], [])
        ledger, warnings = self.run_with(ledger, day3, cand, self.cut(panel, q0, 110), lambda: ["KRW-BTC", "KRW-AAA"])   # 다시 계산하니 지난 구간에도 거래가 생겼다
        rows = ledger["candidates"]["D-202610-01"]["rows"]
        self.assertTrue(rows)
        self.assertTrue(all(r["exit_q"] >= DISC.day_q(day2) - FW.LATE_OK_Q for r in rows))   # 지난 실행 뒤에 끝난 거래만 들어간다
        self.assertEqual(len([w for w in warnings if "D-202610-01: 다시 계산한 값이 장부와 다른 거래" in w]), 1)
        self.assertTrue(all("뒤늦게 나타남" in m for m in ledger["mismatch_seen"]["후보 D-202610-01"]))
        self.assertEqual(ledger["candidates"]["D-202610-01"]["hold"]["since"], day3)   # 판정 대상 구간이 달라진 것이므로 판정을 보류한다
        self.assertIn("· 판정 보류(값 차이) |", FW.render(ledger))
        ledger, warnings = self.run_with(ledger, DISC.q_day(q0 + 111 * 96), cand, self.cut(panel, q0, 111), lambda: ["KRW-BTC", "KRW-AAA"])
        self.assertFalse(any("다시 계산한 값" in w for w in warnings))                  # 같은 것을 다시 경고하지 않는다

    def test_a_candidate_from_an_older_frame_is_not_scored(self):
        q0, panel = crash_panel(150)
        reg = DISC.q_day(q0 + 70 * 96)
        ledger, warnings = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[self.cand(reg, engine="1")], **offline(self.cut(panel, q0, 90)))
        self.assertEqual(ledger["candidates"]["D-202610-01"]["rows"], [])
        self.assertTrue(any("틀" in w for w in warnings))


def clean(monday, **over):
    """경고 없이 끝나는 update 인자들과 처음 장부. H2 는 그 주 반영이 끝난 것으로 둔다(건너뜀)."""
    args = dict(h2_inputs=lambda: (_ for _ in ()).throw(AssertionError("H2 는 건너뛰어야 한다")), h3_fetch=lambda m, d: h3_candles(140, 0.01),
                cr_fetch=lambda s, e: {m: ([], None) for m in AM.CR_COST}, surge_fetch=lambda m: [], crv_fetch=lambda m, s, e: [],
                list_markets=lambda: (_ for _ in ()).throw(RuntimeError("목록 조회 없음")))
    args.update(over)
    return args, {"H2_kimchi": {"rows": [], "decision": None, "updated": monday}}


class DailyRuns(unittest.TestCase):
    """고문 판정 2026-11-17 ①: 매일 돌고(예약 하루 세 번), 하루 한 메시지에 새로 끝난 거래를 싣는다."""
    START = AM.to_min(FW.CRV_START)

    def sol_drop(self, m, s, e):
        """SOL 만 추적 시작 뒤에 한 번 6.5% 빠진다 → 세 변형에 거래가 한 건씩."""
        closes = [100.0] * 20 + [93.5] + [93.5 * 1.01] * 60
        return five_min(closes, self.START + 60) if m == "KRW-SOL" else five_min([100.0] * 90, self.START + 60)

    def test_only_a_run_without_warnings_marks_the_day_done(self):
        args, ledger = clean("2026-11-16")
        ledger, warnings = FW.update(ledger, "2026-11-20", **args)
        self.assertEqual((warnings, ledger["last_ok"], ledger["last_run"]), ([], "2026-11-20", "2026-11-20"))
        args["crv_fetch"] = lambda m, s, e: (_ for _ in ()).throw(TimeoutError("조회 실패"))
        ledger, warnings = FW.update(ledger, "2026-11-21", **args)
        self.assertTrue(warnings)
        self.assertEqual((ledger["last_ok"], ledger["last_run"]), ("2026-11-20", "2026-11-21"))        # 실행은 했지만 끝난 날로 치지 않는다

    def test_the_done_today_question_reads_only_the_ledger(self):
        real = FW.update
        FW.update = lambda *a, **k: (_ for _ in ()).throw(AssertionError("시세를 받으면 안 된다"))
        try:
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()) as buf:
                self.assertEqual(FW.main(["--out", d, "--done-today", "--today", "2026-11-20"]), 1)    # 장부가 없으면 아직 안 끝난 것
                (Path(d) / "ledger.json").write_text(json.dumps({"last_ok": "2026-11-20", "last_run": "2026-11-21"}), encoding="utf-8")
                self.assertEqual(FW.main(["--out", d, "--done-today", "--today", "2026-11-20"]), 0)
                self.assertEqual(FW.main(["--out", d, "--done-today", "--today", "2026-11-21"]), 1)    # 실행 기록(last_run)만으로는 끝난 것이 아니다
                self.assertEqual(sorted(x.name for x in Path(d).iterdir()), ["ledger.json"])           # 아무것도 쓰지 않는다
            self.assertIn("건너뜀", buf.getvalue())
        finally:
            FW.update = real

    def test_new_trades_are_listed_once_with_coin_and_return(self):
        args, ledger = clean("2026-11-16", crv_fetch=self.sol_drop)
        ledger, warnings = FW.update(ledger, "2026-11-20", **args)
        items = ledger["new"]["items"]
        self.assertEqual((warnings, ledger["new"]["day"]), ([], "2026-11-20"))
        self.assertEqual([(x["item"], x["market"]) for x in items],
                         [("급락 반등 변형 −5%", "SOL"), ("급락 반등 변형 −6%", "SOL"), ("급락 반등 변형 4시간 보유", "SOL")])
        rows = ledger["CR_variants"]["CR_drop5"]["rows"]
        self.assertEqual((items[0]["net"], items[0]["when"]), (rows[0]["net"], AM.kst(rows[0]["entry_min"])))
        text = FW.summary(ledger)
        self.assertIn("새로 끝난 거래 3건:", text)
        self.assertIn("  급락 반등 변형 −5% SOL " + AM.kst(rows[0]["entry_min"]) + " → " + FW._pct(rows[0]["net"]), text)
        again, _ = FW.update(copy.deepcopy(ledger), "2026-11-20", **args)                              # 같은 날 다시 돌려도 두 번 적지 않는다
        self.assertEqual(again["new"], ledger["new"])
        ledger, _ = FW.update(ledger, "2026-11-21", **args)                                            # 어제 요약에 실린 것은 다음 날 비운다
        self.assertEqual(ledger["new"], {"day": "2026-11-21", "items": []})
        text = FW.summary(ledger)
        self.assertIn("새로 끝난 거래 없음", text)
        self.assertNotIn("SOL", text)                                                                 # 없으면 진행 숫자만

    def test_trades_from_a_failed_day_stay_until_a_clean_run_reports_them(self):
        broken = lambda m, d: (_ for _ in ()).throw(OSError("no network"))
        args, ledger = clean("2026-11-16", crv_fetch=self.sol_drop, h3_fetch=broken)
        ledger, warnings = FW.update(ledger, "2026-11-20", **args)                                     # 거래는 붙었지만 H3 를 못 받아 실패한 날
        self.assertTrue(warnings)
        self.assertEqual((len(ledger["new"]["items"]), ledger.get("last_ok")), (3, None))
        good, _ = clean("2026-11-16", crv_fetch=self.sol_drop)
        ledger, warnings = FW.update(ledger, "2026-11-21", **good)                                     # 다음 날 성공: 어제 것이 아직 실려 있다
        self.assertEqual((warnings, len(ledger["new"]["items"]), ledger["new"]["day"], ledger["last_ok"]), ([], 3, "2026-11-21", "2026-11-21"))
        ledger, _ = FW.update(ledger, "2026-11-22", **good)                                            # 그 요약이 나간 뒤에야 비운다
        self.assertEqual(ledger["new"]["items"], [])

    def test_new_periods_and_candidate_trades_are_listed_too_and_a_long_list_is_cut(self):
        q0, panel = crash_panel(90)
        cand = RegisteredCandidates().cand(DISC.q_day(q0 + 80 * 96))
        today = DISC.q_day(q0 + 90 * 96)
        args = offline(panel)
        rows = [row(k, 0.01 * (k + 1)) for k in range(2)]
        real = FW.h2_rows
        FW.h2_rows = lambda data, bn, last: rows
        try:
            args["h2_inputs"] = lambda: ({}, {}, [])
            ledger, _ = FW.update({"H2_kimchi": {"rows": rows[:1], "decision": None}}, today, registry=[cand], **args)
        finally:
            FW.h2_rows = real
        items = ledger["new"]["items"]
        self.assertEqual(items[0], {"item": "H2 김치 되돌림", "market": "A01", "when": rows[1]["date"] + " 매수", "net": 0.02})   # 이미 있던 기간은 적지 않는다
        got = ledger["candidates"][cand["id"]]["rows"]
        self.assertEqual(len(items), 1 + len(got))
        self.assertEqual(items[1], {"item": "후보 " + cand["id"], "market": "AAA", "when": AM.kst(got[0]["entry_q"] * 15), "net": got[0]["net"]})
        ledger["new"]["items"] = [dict(items[1], when="t%02d" % k) for k in range(FW.NEW_SHOW + 3)]
        lines = FW.summary(ledger).splitlines()
        self.assertIn("새로 끝난 거래 %d건:" % (FW.NEW_SHOW + 3), lines)
        self.assertEqual(sum(1 for ln in lines if ln.startswith("  후보 ")), FW.NEW_SHOW)
        self.assertIn("  외 3건", lines)


class Observation(unittest.TestCase):
    """고문 판정 2026-11-17 ①: 현황에 최근 7일·28일 관찰 표. 관찰용 — 판정과 등록에 쓰지 않는다."""

    def setUp(self):
        self.saved = DISC.REPRO_UNIVERSE
        DISC.REPRO_UNIVERSE = ("KRW-BTC", "KRW-AAA")
        self.q0, self.panel = crash_panel(90)
        self.today = DISC.q_day(self.q0 + 90 * 96)
        self.asked = []

    def tearDown(self):
        DISC.REPRO_UNIVERSE = self.saved

    def loader(self, start_q, end_q, markets):
        self.asked.append((start_q, end_q, tuple(markets)))
        return {m: self.panel[m] for m in markets}

    def expected(self, days):
        """crash_panel 의 급락(120봉마다) 가운데 진입 봉이 직전 days 일에 든 것의 수와 거래당 순수익."""
        end = self.q0 + 90 * 96
        n = sum(1 for k in range(120, 90 * 96, 120) if end - days * 96 <= self.q0 + k + 1 < end)
        gross = (1 + 0.02 / 4.0) ** 4 - 1
        return n, (1 + gross) * (1 - 0.001) / (1 + 0.001) - 1

    def test_the_three_basic_events_are_counted_over_seven_and_twenty_eight_days(self):
        obs = FW.observe(self.today, list_markets=lambda: ["KRW-BTC", "KRW-AAA", "KRW-ZZZ"], panel_loader=self.loader)
        end = DISC.day_q(self.today)
        self.assertEqual(self.asked, [(end - 28 * 96 - FW.OBS_WARMUP_Q, end, ("KRW-BTC", "KRW-AAA"))])   # 28일과 앞쪽 자료만 받는다
        self.assertEqual((obs["as_of"], obs["markets"], obs["missing"]), (self.today, 2, []))
        self.assertEqual(sorted(obs["events"]), ["BREAKOUT", "CRASH", "SURGE"])
        for days in (7, 28):
            n, net = self.expected(days)
            got = obs["events"]["CRASH"][str(days)]
            self.assertEqual(got[0], n, days)
            self.assertAlmostEqual(got[1], net, places=9)
            self.assertEqual(obs["events"]["SURGE"][str(days)], [0, None])                             # 없는 사건은 0건, 평균 없음
        self.assertEqual((self.expected(7)[0], self.expected(28)[0]), (5, 22))                         # 30시간마다 한 건
        self.assertEqual(FW.OBS_DAYS, (7, 28))
        self.assertEqual(obs["events0"], obs["events"])                                                # 이 세계의 급락은 모두 거래대금 5배라 두 표가 같다

    def test_events_without_the_volume_condition_are_counted_separately(self):
        """고문 판정 11/22 ⑤ 5: '조건 없음' 줄에는 거래대금 3배가 걸려 있었다. 3배 조건이 없는 사건을 따로 센다."""
        q0, panel = crash_panel(40)
        b = panel["KRW-AAA"]
        quiet = DISC.Bars({b.ts[i]: (b.o[i], b.h[i], min(b.o[i], b.c[i]), b.c[i], 1e9) for i in range(len(b))})   # 같은 급락, 거래대금은 평소 그대로
        world = {"KRW-BTC": panel["KRW-BTC"], "KRW-AAA": quiet}
        obs = FW.observe(DISC.q_day(q0 + 40 * 96), list_markets=lambda: ["KRW-BTC", "KRW-AAA"], panel_loader=lambda s, e, markets: {m: world[m] for m in markets})
        self.assertEqual(obs["events"]["CRASH"]["28"], [0, None])                                      # 3배 조건으로는 사건이 아니다
        self.assertEqual(obs["events0"]["CRASH"]["28"][0], 22)                                         # 조건을 빼면 30시간마다 한 건
        self.assertGreater(obs["events0"]["CRASH"]["28"][1], 0)

    def test_the_window_is_the_days_before_the_run_day(self):
        pairs = [("2026-11-30", 9.0), ("2026-11-29", 0.01), ("2026-11-23", 0.03), ("2026-11-22", 0.05), ("2026-11-02", 0.07), ("2026-11-01", 9.0)]
        self.assertEqual(FW.window_stats(pairs, "2026-11-30", 7), [2, 0.02])                           # 11/23 ~ 11/29. 실행일 당일은 세지 않는다
        self.assertEqual(FW.window_stats(pairs, "2026-11-30", 28)[0], 4)                               # 11/02 ~ 11/29
        self.assertAlmostEqual(FW.window_stats(pairs, "2026-11-30", 28)[1], 0.04)
        self.assertEqual(FW.window_stats([], "2026-11-30", 7), [0, None])

    def test_a_delisted_coin_is_left_out_and_named_and_an_unusable_list_removes_nothing(self):
        obs = FW.observe(self.today, list_markets=lambda: ["KRW-BTC"], panel_loader=self.loader)
        self.assertEqual((obs["markets"], obs["missing"], self.asked[-1][2]), (1, ["KRW-AAA"], ("KRW-BTC",)))
        self.assertEqual(obs["events"]["CRASH"]["28"], [0, None])
        for bad in (lambda: [], lambda: (_ for _ in ()).throw(TimeoutError("목록 조회 실패"))):          # 빈 목록, 조회 실패
            obs = FW.observe(self.today, list_markets=bad, panel_loader=self.loader)
            self.assertEqual((obs["markets"], obs["missing"]), (2, []))

    def test_the_table_changes_nothing_else_in_the_ledger(self):
        """관찰 표는 판정에도 등록에도 쓰지 않는다: 있든 없든 나머지 장부(거래, 판정, 보류)는 글자 하나 다르지 않다."""
        cand = RegisteredCandidates().cand(DISC.q_day(self.q0 + 30 * 96))
        args = offline(self.panel)
        plain, _ = FW.update({}, self.today, registry=[cand], **args)
        seen, _ = FW.update({}, self.today, registry=[cand], observer=lambda day: FW.observe(day, lambda: [], self.loader), **args)
        self.assertIsNotNone(seen["candidates"][cand["id"]]["decision"])                               # 판정까지 간 장부로 비교한다
        table = seen.pop("observation")
        self.assertEqual(table["events"]["CRASH"]["28"][0], 22)
        self.assertEqual(json.dumps(seen, sort_keys=True), json.dumps(plain, sort_keys=True))
        reg_source = (ROOT / "scalp" / "research" / "discover.py").read_text(encoding="utf-8")
        self.assertNotIn("observation", reg_source)                                                   # 탐색·등록 쪽은 이 값을 읽지 않는다

    def test_a_failed_fetch_keeps_the_old_table_and_the_day_is_not_done(self):
        args, ledger = clean("2026-11-16")
        ledger["observation"] = {"as_of": "2026-11-19", "markets": 20, "missing": [], "events": {}}
        before = copy.deepcopy(ledger["observation"])
        ledger, warnings = FW.update(ledger, "2026-11-20", observer=lambda day: (_ for _ in ()).throw(TimeoutError("조회 실패")), **args)
        self.assertEqual(warnings, ["관찰 표: 이번 실행에서 갱신하지 못함 — TimeoutError: 조회 실패"])
        self.assertEqual((ledger["observation"], ledger.get("last_ok")), (before, None))               # 뒤의 예약이 다시 받는다
        ledger, warnings = FW.update(ledger, "2026-11-20", observer=lambda day: dict(before, as_of=day), **args)
        self.assertEqual((warnings, ledger["observation"]["as_of"], ledger["last_ok"]), ([], "2026-11-20", "2026-11-20"))

    def test_the_real_run_computes_the_table(self):
        got = {}

        def spy(ledger, today, **kw):
            got.update(kw)
            return ledger, []
        real = FW.update
        FW.update = spy
        try:
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(FW.main(["--out", d, "--dry-run", "--today", "2026-11-20"]), 0)
        finally:
            FW.update = real
        self.assertIs(got["observer"], FW.observe)

    def test_status_shows_events_and_every_tracked_item_and_says_what_the_table_is_not_for(self):
        today = "2026-11-30"

        def trade(days_ago, net, market="KRW-BTC"):
            entry = AM.to_min(AM.parse_utc(DF._shift(today, -days_ago) + "T03:00"))
            return {"market": market, "entry_min": entry, "exit_min": entry + 60, "drop": -0.05, "entry": 1.0, "exit": 1.0, "gross": net, "net": net}
        ledger = {"last_run": today, "warnings": [],
                  "observation": {"as_of": today, "markets": 19, "missing": ["KRW-WAVES"],
                                  "events": {"SURGE": {"7": [3, -0.004], "28": [11, -0.006]}, "BREAKOUT": {"7": [0, None], "28": [40, 0.001]},
                                             "CRASH": {"7": [2, 0.01], "28": [9, -0.002]}},
                                  "events0": {"SURGE": {"7": [5, -0.003], "28": [20, -0.005]}, "BREAKOUT": {"7": [1, 0.002], "28": [70, 0.0]},
                                              "CRASH": {"7": [4, 0.005], "28": [15, -0.001]}}},
                  "H2_kimchi": {"rows": [{"date": DF._shift(today, -5), "picks": [], "strat": -0.03, "ew": 0.0, "btc": 0.0},
                                         {"date": DF._shift(today, -20), "picks": [], "strat": 0.01, "ew": 0.0, "btc": 0.0}], "decision": None,
                                "updated": "2026-11-23"},
                  "CR_rebound": {"rows": [trade(40, 0.5), trade(28, 0.03), trade(8, 0.01), trade(7, 0.02), trade(1, -0.01)], "decision": None,
                                 "updated": today},
                  "CR_variants": {"CR_drop5": {"rows": [trade(3, 0.004)], "decision": None, "updated": today},
                                  "CR_drop6": {"rows": [], "decision": None, "updated": today}},
                  "candidates": {"A-202611-01": {"rows": [{"market": "KRW-SAND", "entry_q": 1, "exit_q": 5, "day": DF._shift(today, -2),
                                                           "gross": -0.012, "net": -0.014, "move": -0.04}], "decision": None, "updated": today}}}
        text = FW.render(ledger)
        self.assertIn("## 관찰 표 — 관찰용, 판정과 등록에 쓰지 않는다", text)
        self.assertIn("이 표로 통과·탈락을 정하지 않고 후보를 등록하지도 않습니다", text)
        self.assertIn("| 항목 | 최근 7일 건수 | 평균 | 최근 28일 건수 | 평균 |", text)
        for line in ("| 급등 뒤 1시간 보유 (거래대금 3배) | 3 | -0.40% | 11 | -0.60% |", "| 돌파 뒤 1시간 보유 (거래대금 3배) | 0 | - | 40 | +0.10% |",
                     "| 급락 뒤 1시간 보유 (거래대금 3배) | 2 | +1.00% | 9 | -0.20% |",                 # 지표 조건은 없어도 거래대금 3배는 걸려 있다 (고문 판정 11/22 ⑤ 5)
                     "| 급등 뒤 1시간 보유 (거래대금 조건 없음) | 5 | -0.30% | 20 | -0.50% |", "| 돌파 뒤 1시간 보유 (거래대금 조건 없음) | 1 | +0.20% | 70 | +0.00% |",
                     "| 급락 뒤 1시간 보유 (거래대금 조건 없음) | 4 | +0.50% | 15 | -0.10% |",
                     "| H2 김치 되돌림 (3일 기간 단위) (자료 반영 2026-11-23) | 1 | -3.00% | 2 | -1.00% |",   # 주 1회 반영이라 날짜를 붙인다
                     "| 급락 반등 | 2 | +0.50% | 4 | +1.25% |",                                        # 7일 전과 1일 전 / 28일 전까지. 40일 전은 어느 쪽에도 없다
                     "| 급락 반등 변형 −5% | 1 | +0.40% | 1 | +0.40% |", "| 급락 반등 변형 −6% | 0 | - | 0 | - |",
                     "| 급락 반등 변형 4시간 보유 (아직 채점 전) | - | - | - | - |",                       # 한 번도 채점되지 못한 항목은 0건으로 보이지 않는다
                     "| 후보 A-202611-01 | 1 | -1.40% | 1 | -1.40% |"):
            self.assertIn(line, text)
        self.assertIn("고문의 고정 20종목 가운데 19종목(상장 목록에 없어 뺀 종목: WAVES)의 15분봉", text)
        self.assertIn("급등 -0.770%(924건)", text)                                                     # 고문 집계 과거 평균을 옆에 둔다
        self.assertNotIn("(조건 없음) |", text)                                                        # 예전 라벨은 남지 않는다
        self.assertIn("기본 사건(위 6줄)", text)
        self.assertNotIn("관찰", FW.summary(ledger))                                                   # 텔레그램 요약에는 싣지 않는다
        ledger["observation"].update(markets=20, missing=[])
        self.assertIn("기본 사건(위 6줄): 고문의 고정 20종목의 15분봉.", FW.render(ledger))
        del ledger["observation"]["events0"]                                                         # 예전 장부(3배 줄만 있음)도 그대로 읽는다
        old = FW.render(ledger)
        self.assertIn("기본 사건(위 3줄): 고문의 고정 20종목의 15분봉.", old)
        self.assertNotIn("거래대금 조건 없음) |", old)
        del ledger["observation"]
        self.assertIn("기본 사건(급등·돌파·급락)은 아직 계산된 값이 없습니다", FW.render(ledger))



def ms(text):
    return int(AM.parse_utc(text).timestamp() * 1000)


class FakeTicks(object):
    """업비트 /v1/trades/ticks 흉내. trades = {종목: [(UTC 시각 ms, 가격, 수량, "ASK"|"BID")]}. 2026-11-20 에 실제 API 로 확인한 동작:
    days_ago = 조회할 날짜와 '그 종목의 마지막 체결일' 의 일 단위 차이(1~7, 넘으면 400), to 는 그날의 그 초 직전까지, 새 것부터 count 건,
    cursor 는 그 sequential_id 이전. count=1 에 to 가 없으면 가장 최근 체결 하나. wrong_day 를 주면 그만큼 뒤의 날을 준다(어긋난 응답 시험용).
    latest 를 주면 그 시각에 체결 하나를 더해 그 종목의 마지막 체결일을 정한다."""

    def __init__(self, trades, latest="2026-11-19T09:00", loose_to=False, wrong_day=0):
        trades = {m: list(v) + ([(ms(latest), 1.0, 1.0, "BID")] if latest else []) for m, v in trades.items()}
        self.trades = {m: sorted(v, reverse=True) for m, v in trades.items()}
        self.loose_to, self.wrong_day = loose_to, wrong_day
        self.calls = []

    def __call__(self, url):
        import urllib.parse
        self.calls.append(url)
        q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        rows = self.trades.get(q["market"])
        if rows is None:
            raise RuntimeError("조회 실패 (HTTP 404)")
        ago = int(q.get("days_ago", 0))
        if "days_ago" in q and not 1 <= ago <= 7:
            raise RuntimeError("조회 실패 (HTTP 400)")
        utc = dt.timezone.utc
        last = dt.datetime.fromtimestamp(rows[0][0] / 1000.0, tz=utc)
        if q.get("count") == "1" and "to" not in q:
            return [{"trade_date_utc": last.date().isoformat(), "trade_time_utc": last.strftime("%H:%M:%S"), "timestamp": rows[0][0],
                     "trade_price": rows[0][1], "trade_volume": rows[0][2], "ask_bid": rows[0][3], "sequential_id": rows[0][0] * 10000}]
        day = last.date() - dt.timedelta(days=ago - self.wrong_day)
        to, out = q.get("to"), []
        for k, (ms_, price, vol, side) in enumerate(rows):
            t = dt.datetime.fromtimestamp(ms_ / 1000.0, tz=utc)
            seq = ms_ * 10000 + (len(rows) - k)
            hms = t.strftime("%H%M%S")
            if t.date() != day or (to and (hms > to if self.loose_to else hms >= to)) or ("cursor" in q and seq >= int(q["cursor"])):
                continue
            out.append({"trade_date_utc": t.date().isoformat(), "trade_time_utc": t.strftime("%H:%M:%S"), "timestamp": ms_, "trade_price": price,
                        "trade_volume": vol, "ask_bid": side, "sequential_id": seq})
            if len(out) >= int(q.get("count", 1)):
                break
        return out


class Fills(unittest.TestCase):
    """고문 판정 2026-11-19·20: 봉 기준 수익이 실제 체결에서도 남는지 — 업비트 공개 체결 내역으로 추정한다."""
    ENTRY, EXIT = "2026-11-15T01:15", "2026-11-15T02:15"

    def world(self, extra=(), latest="2026-11-19T09:00", **kw):
        e, x = ms(self.ENTRY), ms(self.EXIT)
        trades = [(e - 1000, 50.0, 100.0, "BID"),                                  # 구간 앞: 세지 않는다
                  (e + 1000, 61.0, 10.0, "BID"), (e + 20000, 62.0, 30.0, "BID"),   # 60초 매수 평균 = (610 + 1860) / 40 = 61.75
                  (e + 30000, 60.0, 50.0, "ASK"),                                  # 진입 구간의 매도 체결: 금액만 센다
                  (e + 60000, 99.0, 100.0, "BID"),                                 # 60초째: 구간 밖
                  (x + 5000, 59.0, 20.0, "ASK"), (x + 50000, 60.0, 20.0, "ASK"),   # 60초 매도 평균 = 59.5
                  (x + 10000, 59.8, 5.0, "BID")] + list(extra)
        return FakeTicks({"KRW-KAVA": trades}, latest=latest, **kw)

    def check(self, get):
        return FW.fill_check("KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), 61.4, 59.4, get)

    def test_sixty_second_values_are_the_amount_weighted_market_orders(self):
        get = self.world()
        f = self.check(get)
        self.assertEqual(f["status"], "ok")
        self.assertAlmostEqual(f["buy"], 61.75)
        self.assertAlmostEqual(f["sell"], 59.5)
        self.assertAlmostEqual(f["net"], 59.5 * (1 - 0.0005) / (61.75 * (1 + 0.0005)) - 1, places=9)    # 수수료 편도 0.05%
        self.assertAlmostEqual(f["buy_vs_open"], 61.75 / 61.4 - 1, places=9)
        self.assertAlmostEqual(f["sell_vs_open"], 59.5 / 59.4 - 1, places=9)
        self.assertEqual((f["entry_bid_krw"], f["entry_ask_krw"], f["exit_bid_krw"], f["exit_ask_krw"]), (2470, 3000, 299, 2380))
        self.assertEqual(f["order_status"], "자료 부족")                                               # 60초 매수가 2,470원뿐: 100만 원이 안 찬다
        self.assertTrue(get.calls[0].endswith("market=KRW-KAVA&count=1"))                             # 마지막 체결일을 먼저 묻는다
        self.assertTrue(all("days_ago=4" in u for u in get.calls[1:]))                                 # 그 종목의 마지막 체결일(11/19) 기준 4일 전
        self.assertTrue(any("to=011600" in u for u in get.calls) and any("to=021600" in u for u in get.calls))
        self.assertEqual((FW.FILL_SEC, FW.FILL_FEE, FW.FILL_DAYS, FW.ORDER_KRW), (60, 0.0005, 7, 1000000))
        self.assertAlmostEqual(self.check(self.world(loose_to=True))["buy"], 61.75)                    # 60초째 체결까지 받아도 우리 쪽에서 뺀다

    def test_days_ago_counts_from_the_coins_last_trade_date_as_the_api_actually_does(self):
        """문서에는 '요청 시점과의 차이' 라고 적혀 있지만(제3세션 지적), 실제 API 는 그 종목의 마지막 체결일을 기준으로 센다
        (2026-11-20 00:01 UTC: 오늘 체결이 없던 CARV·LSK 에 days_ago=1 → 11/18). 오늘 체결이 없는 종목에서도 날짜가 맞아야 한다."""
        get = self.world(latest="2026-11-19T23:21")                                                    # 지금은 11/20 이지만 이 종목의 마지막 체결은 11/19
        self.assertEqual(self.check(get)["status"], "ok")
        self.assertTrue(all("days_ago=4" in u for u in get.calls[1:]))
        bad = self.world(extra=[(ms("2026-11-16T01:00"), 1.0, 1.0, "BID")], wrong_day=1)            # 조회한 날보다 뒤의 날을 받았다
        with self.assertRaises(RuntimeError):
            self.check(bad)

    def test_no_market_order_in_a_window_is_a_shortage_not_an_impossibility(self):
        get = self.world()
        get.trades["KRW-KAVA"] = [t for t in get.trades["KRW-KAVA"] if not (t[3] == "BID" and ms(self.ENTRY) <= t[0] < ms(self.ENTRY) + 60000)]
        f = self.check(get)
        self.assertEqual((f["status"], f["order_status"], f["entry_bid_krw"], f["entry_ask_krw"], f["exit_ask_krw"]),
                         ("60초 안 체결 자료 부족", "자료 부족", 0, 3000, 2380))
        self.assertNotIn("net", f)
        get = self.world()
        get.trades["KRW-KAVA"] = [t for t in get.trades["KRW-KAVA"] if not (t[3] == "ASK" and t[0] >= ms(self.EXIT))]
        self.assertEqual(self.check(get)["status"], "60초 안 체결 자료 부족")

    def test_a_window_after_the_coins_last_trade_has_no_trades(self):
        """거래가 멈춘 종목: 청산 창이 그 종목의 마지막 체결보다 뒤면 조회하지 않고 체결 없음으로 본다(음수 days_ago 를 보내지 않는다)."""
        e = ms(self.ENTRY)
        halted = FakeTicks({"KRW-KAVA": [(e + 1000, 61.0, 10.0, "BID")]}, latest=None)                 # 마지막 체결이 11/15 진입 창
        f = FW.fill_check("KRW-KAVA", e, ms("2026-11-16T00:15"), 61.4, 59.4, halted)
        self.assertEqual(f["status"], "60초 안 체결 자료 부족")
        self.assertFalse([u for u in halted.calls if "days_ago=-" in u])

    def test_older_than_seven_days_is_no_data_without_asking_for_the_trades(self):
        get = self.world(latest="2026-11-23T00:30")
        self.assertEqual(self.check(get), {"status": "체결 자료 없음", "order_status": "체결 자료 없음"})
        self.assertEqual(len(get.calls), 1)                                                            # 마지막 체결일만 묻고 끝
        self.assertEqual(self.check(self.world(latest="2026-11-22T23:59"))["status"], "ok")            # 7일째까지는 받는다

    def test_more_than_one_page_is_followed_with_the_cursor(self):
        e = ms(self.ENTRY)
        busy = [(e + 2000 + k * 40, 70.0, 1.0, "BID") for k in range(1200)]
        get = self.world(extra=busy)
        f = self.check(get)
        self.assertAlmostEqual(f["buy"], (610 + 1860 + 70.0 * 1200) / (40 + 1200))
        self.assertGreaterEqual(sum(1 for u in get.calls if "cursor=" in u), 2)
        real = FW.TICKS_MAX_PAGES
        FW.TICKS_MAX_PAGES = 2
        try:
            with self.assertRaises(RuntimeError):                                                      # 끝까지 못 받으면 값을 내지 않는다
                self.check(self.world(extra=busy))
        finally:
            FW.TICKS_MAX_PAGES = real

    def test_order_size_values_take_the_first_million_won_of_each_side(self):
        """고문 판정 2026-11-20 3: 신호 뒤 첫 100만 원어치 BID, 청산 뒤 첫 100만 원어치 ASK 의 평균가. 60초 안에 안 차면 자료 부족."""
        e, x = ms(self.ENTRY), ms(self.EXIT)
        big = [(e + 1000, 100.0, 6000.0, "BID"), (e + 5000, 101.0, 5000.0, "BID"), (e + 9000, 150.0, 9999.0, "BID"),   # 60만 + 40만 = 100만에서 멈춘다
               (e + 2000, 99.0, 9999.0, "ASK"), (x + 2000, 98.0, 20000.0, "ASK"), (x + 3000, 10.0, 1.0, "ASK")]
        f = self.check(FakeTicks({"KRW-KAVA": big}))
        self.assertEqual(f["status"], "ok")
        buy = 1000000.0 / (6000 + 400000.0 / 101)
        self.assertEqual(f["order_status"], "ok")
        self.assertAlmostEqual(f["order_buy"], buy)
        self.assertAlmostEqual(f["order_sell"], 98.0)
        self.assertAlmostEqual(f["order_net"], 98.0 * (1 - 0.0005) / (buy * (1 + 0.0005)) - 1, places=9)
        self.assertAlmostEqual(f["order_buy_vs_open"], buy / 61.4 - 1, places=9)
        self.assertNotAlmostEqual(f["buy"], f["order_buy"])                                            # 60초 값과 다르다 — 60초 값은 따로 남는다
        thin = [t for t in big if not (t[3] == "BID" and t[1] != 100.0)]                               # 매수 체결 60만 원뿐
        self.assertEqual(self.check(FakeTicks({"KRW-KAVA": thin}))["order_status"], "자료 부족")
        self.assertIsNone(FW.order_price([(1, 100.0, 9999.0, "ASK")], "BID", 1000))

    def candidate(self):
        return {"id": "A-1", "engine": DISC.ENGINE, "registered": "2026-11-13", "start": "2026-11-14", "event": "CRASH", "hold": 4, "cond": None,
                "universe": ["KRW-BTC", "KRW-KAVA"], "move": 0.03, "vol_x": 3.0, "cost": {"KRW-BTC": 0.0007}, "cost_other": 0.001, "status": "ACTIVE"}

    def scored(self, fill_get, ledger=None, today="2026-11-19", tick_dir=None):
        """후보 A-1 의 11/15 KAVA 거래 한 건이 채점된 상태로 update 를 돌린다. 패널에는 진입·청산 봉의 시가가 있다."""
        q_in, q_out = ms(self.ENTRY) // 900000, ms(self.EXIT) // 900000
        row = {"market": "KRW-KAVA", "entry_q": q_in, "exit_q": q_out, "day": "2026-11-15", "gross": -0.0326, "net": -0.0345, "move": -0.04}
        real = DISC.candidate_trades
        DISC.candidate_trades = lambda panel, cand, end_q: [dict(row)]
        bars = {q: (61.4 if q == q_in else 59.4 if q == q_out else 60.0, 62.0, 58.0, 60.0, 1e9) for q in range(q_in - 4, q_out + 4)}
        panel = {"KRW-BTC": DISC.Bars(bars), "KRW-KAVA": DISC.Bars(bars)}
        args, start = clean("2026-11-16")
        args["panel_loader"] = lambda s, e, markets: {m: panel[m] for m in markets}
        try:
            return FW.update(ledger if ledger is not None else start, today, registry=[self.candidate()], fill_get=fill_get, tick_dir=tick_dir, **args)
        finally:
            DISC.candidate_trades = real

    def test_the_tracker_attaches_estimates_once_and_shows_them_with_counts(self):
        get = self.world()
        ledger, warnings = self.scored(get)
        f = ledger["candidates"]["A-1"]["rows"][0]["fill"]
        self.assertEqual((warnings, f["status"], f["order_status"]), ([], "ok", "자료 부족"))
        self.assertAlmostEqual(f["sell_vs_open"], 59.5 / 59.4 - 1, places=9)                           # 청산 봉의 시가와 비교한다
        n = len(get.calls)
        ledger, _ = self.scored(get, ledger, "2026-11-20")
        self.assertEqual(len(get.calls), n)                                                            # 이미 붙은 거래는 다시 받지 않는다
        item = self.scored(self.world())[0]["new"]["items"][0]
        self.assertEqual((round(item["fill"], 9), item["order"]), (round(f["net"], 9), "자료 부족"))
        text = FW.render(ledger)
        self.assertIn("## 체결 내역 기반 추정 — 1단계 선별은 등록된 대로 봉 기준", text)
        gap, price = -0.0345 - f["net"], -0.0326 - (59.5 / 61.75 - 1)                                 # 주문 크기 값이 없어 60초 값으로 잰 차이
        self.assertIn("| 후보 A-1 | 1 / 1건 | -3.45%% | -3.45%% → %s (1건) | - → - (0건) | %s (-) = %s + %s | - / - | 0 / 1 | 1 / 1 | 0 / 1 | 2,380원 |" % (
            FW._pct(f["net"]), FW._pct(gap), FW._pct(price), FW._pct(gap - price)), text)
        self.assertIn("- 후보 A-1 KAVA 11/15 10:15: 봉 -3.45%% → 60초 %s, 주문 크기 자료 부족 / 60초 체결 금액: 진입 매수 2,470원·매도 3,000원, 청산 매수 299원·매도 2,380원" % FW._pct(f["net"]), text)
        self.assertIn("| 1 / 1건 | 진행 중 |", text)                                                  # 후보 표에도 '체결 자료 n / 전체 N건'
        self.assertIn("(추정 60초 %s, 1,000,000원 자료 부족)" % FW._pct(f["net"]), FW.summary(self.scored(self.world())[0]))
        self.assertNotIn("체결 기준", text.replace("체결 기준 비용", ""))                               # 옛 이름은 쓰지 않는다 (고문 판정 2026-11-20 1)

    def test_raw_trades_are_kept_and_reused(self):
        """고문 판정 2026-11-20 2: 거래마다 진입·청산 60초 창의 원본 체결을 forward/ticks/ 에 gzip JSON 으로."""
        import gzip
        with tempfile.TemporaryDirectory() as d:
            ledger, _ = self.scored(self.world(), tick_dir=d)
            path = Path(d) / "KRW-KAVA_20261115T0115_20261115T0215.json.gz"
            body = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
            self.assertEqual((body["market"], body["entry_ms"], body["window_sec"], body["fields"]),
                             ("KRW-KAVA", ms(self.ENTRY), 60, ["timestamp_ms", "price", "volume", "ask_bid"]))
            self.assertEqual(body["entry"], [[ms(self.ENTRY) + 1000, 61.0, 10.0, "BID"], [ms(self.ENTRY) + 20000, 62.0, 30.0, "BID"],
                                             [ms(self.ENTRY) + 30000, 60.0, 50.0, "ASK"]])
            self.assertEqual(len(body["exit"]), 3)
            first = path.read_bytes()
            FW.save_ticks(path, "KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), *FW.load_ticks(path))
            self.assertEqual(path.read_bytes(), first)                                                 # 같은 내용이면 같은 바이트
            self.assertEqual(first[4:8], b"\x00\x00\x00\x00")                                         # gzip 머리의 시각을 0 으로(실행마다 파일이 바뀌지 않게)
            old = copy.deepcopy(ledger)                                                                # 2026-11-20 전에 채점된 거래: 60초 값만 있다
            f = old["candidates"]["A-1"]["rows"][0]["fill"]
            for k in [k for k in f if k.startswith("order_")]:
                del f[k]
            f["net"] = -0.0441                                                                         # 이미 있는 60초 값은 건드리지 않는다

            def down(url):
                raise RuntimeError("조회 실패 (TimeoutError)")
            again, warnings = self.scored(down, old, "2026-11-20", tick_dir=d)                         # 원본 파일이 있으면 받지 않고 그것으로
            g = again["candidates"]["A-1"]["rows"][0]["fill"]
            self.assertEqual((warnings, g["order_status"], g["net"]), ([], "자료 부족", -0.0441))

    def test_the_raw_trade_folder_is_committed(self):
        """워크플로는 scalp/research/forward 를 통째로 커밋한다. 연구 폴더의 무시 목록에 다른 용도의 ticks/ 가 있어 예외를 둔다."""
        lines = (ROOT / "scalp" / "research" / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("!forward/ticks/", lines)
        self.assertGreater(lines.index("!forward/ticks/"), lines.index("ticks/"))                      # 뒤에 와야 예외가 먹는다
        self.assertEqual(FW.TICKS_DIR, "ticks")

    def test_an_old_row_without_order_values_is_fetched_once_within_seven_days(self):
        ledger, _ = self.scored(self.world())
        f = ledger["candidates"]["A-1"]["rows"][0]["fill"]
        for k in [k for k in f if k.startswith("order_")]:
            del f[k]
        f["status"] = "체결 없음"                                                                      # 옛 이름
        get = self.world()
        with tempfile.TemporaryDirectory() as d:
            again, warnings = self.scored(get, ledger, "2026-11-20", tick_dir=d)
            g = again["candidates"]["A-1"]["rows"][0]["fill"]
            self.assertEqual((warnings, g["status"], g["order_status"]), ([], "60초 안 체결 자료 부족", "자료 부족"))
            self.assertTrue(get.calls and list(Path(d).iterdir()))
        late = copy.deepcopy(ledger)
        for k in [k for k in late["candidates"]["A-1"]["rows"][0]["fill"] if k.startswith("order_")]:
            del late["candidates"]["A-1"]["rows"][0]["fill"][k]
        later, _ = self.scored(self.world(latest="2026-11-24T01:00"), late, "2026-11-24")
        self.assertEqual(later["candidates"]["A-1"]["rows"][0]["fill"]["order_status"], "체결 자료 없음")

    def test_a_failed_lookup_is_retried_on_the_next_run_and_fails_that_run(self):
        def down(url):
            raise RuntimeError("조회 실패 (TimeoutError)")
        ledger, warnings = self.scored(down)
        self.assertNotIn("fill", ledger["candidates"]["A-1"]["rows"][0])
        self.assertEqual(warnings, ["체결 내역: 1건 조회 실패 — 비워 두고 다음 실행에서 다시 받음 (후보 A-1 KAVA (RuntimeError))"])
        self.assertIsNone(ledger.get("last_ok"))                                                       # 뒤의 예약이 다시 돈다
        ledger, warnings = self.scored(self.world(), ledger)
        self.assertEqual((warnings, ledger["candidates"]["A-1"]["rows"][0]["fill"]["status"]), ([], "ok"))
        late, _ = self.scored(self.world(latest="2026-11-24T01:00"), self.scored(down)[0], "2026-11-24")
        self.assertEqual(late["candidates"]["A-1"]["rows"][0]["fill"], {"status": "체결 자료 없음", "order_status": "체결 자료 없음"})

    def test_a_delisted_coin_is_no_data_without_a_lookup_and_nothing_runs_without_a_fetcher(self):
        get = self.world()
        ledger, warnings = self.scored(get, dict(clean("2026-11-16")[1], candidates={"A-1": {"rows": [], "decision": None, "delisted": {"KRW-KAVA": "2026-11-16"}}}))
        rows = ledger["candidates"]["A-1"]["rows"]
        self.assertTrue(all(r["fill"] == {"status": "체결 자료 없음", "order_status": "체결 자료 없음", "why": "상장폐지"} for r in rows))
        self.assertEqual(get.calls, [])
        plain, _ = self.scored(None)
        self.assertNotIn("fill", plain["candidates"]["A-1"]["rows"][0])
        self.assertNotIn("추정 60초", FW.summary(plain))

    def test_crash_rebound_rows_use_their_own_bar_prices_and_times(self):
        e = AM.to_min(AM.parse_utc(self.ENTRY))
        row = {"market": "KRW-KAVA", "entry_min": e, "exit_min": e + 60, "drop": -0.05, "entry": 61.4, "exit": 59.4, "gross": -0.03, "net": -0.032}
        args, start = clean("2026-11-16", cr_fetch=lambda s, x: {m: (([dict(row, market=m)] if m == "KRW-BTC" else []), None) for m in AM.CR_COST})
        get = self.world()
        get.trades["KRW-BTC"] = get.trades["KRW-KAVA"]
        ledger, warnings = FW.update(start, "2026-11-19", fill_get=get, **args)
        f = ledger["CR_rebound"]["rows"][0]["fill"]
        self.assertEqual((warnings, f["status"]), ([], "ok"))
        self.assertAlmostEqual(f["buy_vs_open"], 61.75 / 61.4 - 1, places=9)
        self.assertEqual(ledger["CR_rebound"]["rows"][0]["market"], "KRW-BTC")
        text = FW.render(ledger)
        self.assertIn("| 급락 반등 | 1 / 1건 | -3.20% | -3.20% → ", text)
        self.assertIn("| 급락 반등 변형 4시간 보유 | 0 / 0건 | - | - → - (0건) | - → - (0건) | - (-) = - + - | - / - | - | - | - | - |", text)
        self.assertIn("체결 자료 1 / 전체 1건.", text)                                                 # 급락 반등 절에도 건수

    def test_the_real_run_looks_up_fills_and_keeps_raw_trades_beside_the_ledger(self):
        got = {}

        def spy(ledger, today, **kw):
            got.update(kw)
            return ledger, []
        real = FW.update
        FW.update = spy
        try:
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
                FW.main(["--out", d, "--today", "2026-11-20"])
                self.assertEqual((got["fill_get"], Path(got["tick_dir"])), (AM.http_get, Path(d) / "ticks"))
                FW.main(["--out", d, "--dry-run", "--today", "2026-11-20"])
                self.assertIsNone(got["tick_dir"])                                                     # 점검 실행은 파일을 쓰지 않는다
        finally:
            FW.update = real


class DayCounts(unittest.TestCase):
    """고문 판정 2026-11-19: 진짜 표본 크기를 보이려고 진입일 수, 일별 승률, 일별 중앙값을 싣는다. 판정 기준은 그대로."""

    def test_daily_means_decide_the_win_rate_and_the_median(self):
        pairs = [("d1", 0.05), ("d1", 0.03), ("d1", -0.01), ("d2", -0.02), ("d3", 0.01), ("d3", -0.03), ("d4", 0.002), ("d5", 0.01), ("d5", -0.01)]
        days, win, mid = FW.day_stats(pairs)                                                          # 일별 평균 +0.0233, -0.02, -0.01, +0.002, 0
        self.assertEqual((days, win), (5, 0.4))                                                       # 평균이 정확히 0 인 날은 이긴 날이 아니다
        self.assertAlmostEqual(mid, 0.0)
        self.assertEqual(FW.day_stats([]), (0, None, None))

    def test_status_shows_them_for_every_event_item(self):
        rows = [cr_trade(k, 0.01 if k % 3 else -0.05) for k in range(6)]                              # 하루 3건씩 2일: 일별 평균 -1% 두 번
        ledger = {"last_run": "2026-11-20", "CR_rebound": {"rows": rows, "decision": None},
                  "CR_variants": {"CR_drop5": {"rows": rows[:3], "decision": None}},
                  "candidates": {"A-1": {"rows": [{"market": "KRW-KAVA", "entry_q": 1, "exit_q": 5, "day": "2026-11-15", "gross": 0.0, "net": 0.02, "move": -0.04},
                                                  {"market": "KRW-SAND", "entry_q": 2, "exit_q": 6, "day": "2026-11-15", "gross": 0.0, "net": -0.01, "move": -0.04}],
                                         "decision": None}}}
        text = FW.render(ledger)
        self.assertIn("진입일 2일, 일별 승률 0%, 일별 중앙값 -1.00%.", text)
        self.assertIn("| −5% | 1시간에 5% 넘게 빠지면 60분 보유 | 3 / 30건, 1 / 10일 | -1.00% | -1.00% | 0% | -1.00% | 0 / 3건 | 진행 중 |", text)
        self.assertIn("| +0.50% | 100% | +0.50% | 0 / 2건 | 진행 중 |", text)                                    # 후보: 거래당 평균, 일별 승률, 일별 중앙값, 판정
        self.assertIn("일별 승률 = 거래가 있었던 날 가운데", text)



def filled(net, order_net=None, status="ok", day="2026-11-15", bid=2e7, ask=2e7, market="KRW-KAVA", k=0, order_status=None):
    """체결 값이 붙은 후보 거래 하나. order_net 이 있으면 주문 크기 값도 ok."""
    f = {"status": status, "entry_bid_krw": int(bid), "entry_ask_krw": 0, "exit_bid_krw": 0, "exit_ask_krw": int(ask), "order_krw": 1000000}
    if status == "ok":
        f.update(net=net - 0.001, buy=1.0, sell=1.0, buy_vs_open=0.001, sell_vs_open=-0.002)
    if order_net is not None:
        f.update(order_status="ok", order_net=order_net, order_buy=1.0, order_sell=1.0, order_buy_vs_open=0.002, order_sell_vs_open=-0.003)
    else:
        f["order_status"] = order_status or "자료 부족"
    if status == "체결 자료 없음":
        f = {"status": status, "order_status": status}
    return {"market": market, "entry_q": 1000 + k, "exit_q": 1004 + k, "day": day, "gross": net, "net": net, "move": -0.04, "fill": f}


def by_q(t):
    return t["entry_q"] * 15


def by_day(t):
    return t["day"]


class StageTwo(unittest.TestCase):
    """고문 판정 2026-11-20 ②: 2단계 = 소액 시험 자격(증명 아님). 점검은 (20건·5일), (40건·10일) 이 처음 함께 된 두 시점에만, 그 값을 고정한다."""
    PAST = (("앞", 0.0145, 0.012), ("뒤", 0.01541, 0.015))

    def test_t_quantiles(self):
        self.assertEqual((FW.t975(1), FW.t975(19), FW.t975(30), FW.t95(19), FW.t99(29)), (12.706, 2.093, 2.042, 1.729, 2.462))
        self.assertAlmostEqual(FW.t975(40), 2.02)
        self.assertAlmostEqual(FW.t99(40), 2.4235)
        self.assertAlmostEqual(FW.t99(120), 2.3585)

    def test_fill_statistics(self):
        rows = [filled(0.02, 0.015, k=0), filled(0.01, 0.002, k=1, bid=3e6, ask=9e6), filled(-0.01, -0.013, k=2),
                filled(0.03, None, k=3),                                                              # 60초 값은 있는데 100만 원이 안 찼다
                filled(0.03, None, status="60초 안 체결 자료 부족", bid=0, ask=8e6, k=4), filled(0.0, status="체결 자료 없음", k=5)]
        fs = FW.fill_stats(rows)
        self.assertEqual((fs["rows"], fs["tried"], fs["ok"], fs["nofill"], fs["order_ok"], fs["nodata"]), (6, 5, 4, 1, 3, 1))
        gaps = [0.005, 0.008, 0.003, 0.001]                                                            # 주문 크기 값이 없으면 60초 값으로 잰다
        mean = sum(gaps) / 4
        sd = (sum((g - mean) ** 2 for g in gaps) / 3) ** 0.5
        self.assertEqual((fs["gap_n"], fs["gap_order_n"]), (4, 3))
        self.assertAlmostEqual(fs["gap"], mean)
        self.assertAlmostEqual(fs["gap_up975"], mean + 3.182 * sd / 2)                                # 97.5% 상한
        self.assertAlmostEqual(fs["price"], (0.02 + 0.01 - 0.01 + 0.03) / 4)                          # 가격 차이: 봉 등락 − 추정 등락(여기서 추정 등락 0)
        self.assertAlmostEqual(fs["fee"], fs["gap"] - fs["price"])                                     # 체결 차이 = 가격 + 수수료
        self.assertEqual((fs["cap_ok"], fs["cap_rate"]), (3, 0.6))                                     # 양쪽 1,000만 원 이상: 3 / 5 (자료 부족도 분모에)
        self.assertEqual(fs["short_rate"], 2 / 5.0)
        legacy = filled(0.01, None, status="체결 없음", k=6)
        self.assertEqual(FW.fill_stats([legacy])["nofill"], 1)

    def twenty(self, gap=0.003, order=True, bid=2e7, days=20, n=20):
        return [filled(0.012 + 0.001 * (k % 3), (0.012 + 0.001 * (k % 3) - gap) if order else None, day="2026-11-%02d" % (1 + k % days), k=k, bid=bid)
                for k in range(n)]

    def point(self, rows, n=20, min_days=5):
        return FW.stage2_point(rows, n, min_days, by_q, by_day)

    def test_a_point_needs_both_the_count_and_the_distinct_days(self):
        self.assertIsNone(self.point(self.twenty()[:19]))
        crowded = self.twenty(days=4, n=30)                                                            # 30건이지만 진입일 4일
        self.assertIsNone(self.point(crowded))
        late = crowded + [filled(0.01, 0.007, day="2026-11-28", k=40)]                                 # 다섯째 날이 온 31번째 거래에서 처음 채워진다
        p = self.point(late)
        self.assertEqual((p["k"], p["sample_days"], p["signals"]), (31, 5, 31))
        rows = self.twenty() + [filled(-0.05, -0.06, day="2026-11-25", k=30)]                          # 21번째: 20건 점검에 들어가지 않는다
        p = self.point(rows)
        self.assertEqual((p["k"], p["gap_n"], p["fwd_trades"], p["fwd_days"]), (20, 20, 20, 20))
        self.assertAlmostEqual(p["gap"], 0.003)
        nodata = [filled(0.0, status="체결 자료 없음", k=-1, day="2026-10-31")]                        # 체결 자료 없는 신호도 그때까지의 수로 남긴다
        q = self.point(nodata + rows)
        self.assertEqual((q["k"], q["signals"], q["nodata"], q["fwd_days"]), (20, 21, 1, 21))
        self.assertEqual(FW.STAGE2_POINTS, ((20, 5), (40, 10)))

    def cells(self, rows, past=None, n=20, min_days=5):
        return FW.stage2_cells(self.point(rows, n, min_days), list(self.PAST if past is None else past))

    def test_each_item_is_met_or_missed_by_its_own_rule(self):
        r = self.cells(self.twenty())
        self.assertEqual([x[0] for x in r], [True, True, True])
        self.assertEqual(r[0][1], "+1.45% − +0.30% = +1.15% (체결 차이 20건, 그중 주문 크기 값 20건)")      # 두 시기 중 낮은 쪽, 차이가 일정해 상한 = 평균
        self.assertEqual(r[1][1], "양쪽 10,000,000원 이상 20 / 20건 (100%, 기준 80%)")
        self.assertTrue(r[2][1].startswith("붕괴 증거 없음(증명 아님): 95% 상한 "))
        self.assertIs(self.cells(self.twenty(gap=0.016))[0][0], False)                                 # 체결 차이 1.6%p: 과거 우위를 다 먹는다
        self.assertIs(self.cells(self.twenty(bid=9e6))[1][0], False)                                   # 진입 매수가 주문의 9배
        rows = self.twenty()
        for t in rows[:4]:                                                                             # 4 / 20 미달 → 80% 충족
            t["fill"]["entry_bid_krw"] = 0
        self.assertIs(self.cells(rows)[1][0], True)
        rows[4]["fill"]["exit_ask_krw"] = 9999999                                                      # 5 / 20 미달 → 75%
        self.assertIs(self.cells(rows)[1][0], False)
        bad = [dict(t, net=-0.01) for t in self.twenty()]                                              # 전진 -1%: 과거 1.2% 의 절반에도 못 미침
        r = self.cells(bad)
        self.assertIs(r[2][0], False)
        self.assertTrue(r[2][1].startswith("붕괴 징후: 전진 일별 평균 -1.00% 의 95% 상한 "))
        self.assertEqual(self.cells(self.twenty(days=6))[2], (None, "판단 보류 — 전진 진입일 6일 (10일부터)"))
        self.assertEqual(self.cells(self.twenty(), past=())[0][1], "과거 우위 미기록")
        self.assertEqual(self.cells(self.twenty(), past=(("2017–20", 0.002, None),))[2], (None, "과거 일별 평균 미기록"))
        near = [dict(t, net=0.0 if k % 2 else 0.012) for k, t in enumerate(self.twenty())]            # 전진 0.6% — 과거 1.2% 보다 낮지만 붕괴 증거는 아님
        self.assertIs(self.cells(near)[2][0], True)
        flat = [dict(t, net=0.0065) for t in self.twenty()]                                            # 0.65%: 낮은 쪽(1.2%)의 절반 0.6% 는 넘고, 높은 쪽(1.5%)의 절반 0.75% 는 못 넘는다
        self.assertIs(self.cells(flat)[2][0], True)
        one = self.twenty(order=False)
        for t in one:
            t["fill"]["status"] = "60초 안 체결 자료 부족"
        one[0] = self.twenty()[0]                                                                      # 추정이 한 건뿐
        self.assertEqual(self.cells(one)[0], (None, "체결 차이 1건 — 상한을 낼 수 없음"))
        self.assertEqual((FW.STAGE2_CAPACITY_X, FW.STAGE2_CAPACITY_MIN, FW.STAGE2_HOLD_DAYS, FW.STAGE2_COLLAPSE), (10, 0.80, 10, 0.5))

    def test_points_are_frozen_when_first_reached(self):
        """고문 판정 2026-11-20 ②: 각 점검 결과는 그 시점 값으로 고정한다(다시 계산해 덮어쓰지 않는다)."""
        def cr_rows(n, net=0.01, gap=0.003):
            return [dict(cr_trade(k, net), fill={"status": "ok", "net": net - gap, "buy": 1.0, "sell": 1.0, "entry_bid_krw": 20000000, "entry_ask_krw": 0,
                                                 "exit_bid_krw": 0, "exit_ask_krw": 20000000, "order_status": "ok", "order_net": net - gap,
                                                 "order_buy": 1.0, "order_sell": 1.0}) for k in range(n)]
        args = offline()
        ledger, _ = FW.update({"CR_rebound": {"rows": cr_rows(19), "decision": None}}, "2026-12-01", **args)
        self.assertNotIn("stage2", ledger["CR_rebound"])
        ledger["CR_rebound"]["rows"] = cr_rows(25)
        ledger, _ = FW.update(ledger, "2026-12-02", **args)
        point = ledger["CR_rebound"]["stage2"]["20"]
        self.assertEqual((point["recorded"], point["k"], point["sample_days"], point["fwd_trades"]), ("2026-12-02", 20, 7, 20))
        frozen = copy.deepcopy(point)
        ledger["CR_rebound"]["rows"] = cr_rows(25, gap=0.05)                                           # 그 뒤 값이 달라져도
        ledger, _ = FW.update(ledger, "2026-12-03", **args)
        self.assertEqual(ledger["CR_rebound"]["stage2"]["20"], frozen)                                 # 덮어쓰지 않는다
        self.assertNotIn("40", ledger["CR_rebound"]["stage2"])
        ledger["CR_rebound"]["rows"] = cr_rows(40, gap=0.05)
        ledger, _ = FW.update(ledger, "2026-12-09", **args)
        self.assertAlmostEqual(ledger["CR_rebound"]["stage2"]["40"]["gap"], 0.05)
        text = FW.render(ledger)
        self.assertIn("| 급락 반등 | 40 / 40건 | 통과 | 2026-12-02 기록 (체결 자료 20건·진입일 7일, 그때까지 신호 20건 중 자료 없음 0건): "
                      "② 미달 +0.22% − +0.30% = -0.08% (체결 차이 20건, 그중 주문 크기 값 20건) · ③ 충족 양쪽 10,000,000원 이상 20 / 20건 (100%, 기준 80%) · "
                      "④ 보류 전진 진입일 7일 (10일부터) | 2026-12-09 기록 (체결 자료 40건·진입일 14일", text)    # 1단계는 지금 값, 점검은 기록된 값
        self.assertIn("④ 충족 붕괴 증거 없음(증명 아님): 95% 상한 +1.00% ≥ ", text)                     # 급락 반등 과거 일별 평균(낮은 쪽) 0.41% 의 절반과 비교
        self.assertIn("| 급락 반등 변형 −5% | 0 / 0건 | 진행 중 | 점검 전 (체결 자료 0 / 20건, 진입일 0 / 5일) | 점검 전 (체결 자료 0 / 40건, 진입일 0 / 10일) |", text)
        self.assertIn("## 2단계 점검 — 소액 시험 자격 (증명 아님. 표시만, 자동 결정 없음. 돈은 고문과 사용자가 정한다)", text)

    def test_the_status_page_decides_nothing_and_candidates_carry_the_registered_past(self):
        q0, panel = crash_panel(150)
        cand = dict(RegisteredCandidates().cand(DISC.q_day(q0 + 70 * 96)), train={"n": 300, "days": 40, "net": 0.02, "t": 3.0},
                    holdout={"n": 120, "days": 30, "net": 0.015, "daymean": 0.011})
        cut = RegisteredCandidates().cut
        ledger, _ = FW.update({}, DISC.q_day(q0 + 120 * 96), registry=[cand], **offline(cut(panel, q0, 120)))
        entry = ledger["candidates"]["D-202610-01"]
        self.assertEqual(entry["past"], [["앞 구간", 0.02, None], ["뒤 구간", 0.015, 0.011]])         # 등록 때 고정된 값 그대로
        before = copy.deepcopy(entry["decision"])
        text = FW.render(ledger)
        self.assertEqual(entry["decision"], before)                                                   # 표시가 판정을 바꾸지 않는다
        self.assertIn("| 후보 D-202610-01 | 0 / 40건 | 통과 | 점검 전 (체결 자료 0 / 20건, 진입일 0 / 5일) |", text)
        self.assertIn("| 번호 | 규칙 | 등록 | 진행 | 거래당 평균 | 일별 승률 | 일별 중앙값 | 체결 자료 | 1단계 선별 |", text)
        self.assertIn("1단계 선별 통과는 '계속 볼 가치가 있다' 는 뜻이지 돈을 넣는 신호가 아닙니다", text)
        self.assertIn("자동 등록 후보: 1개 추적, 1단계 선별 끝 1개 (통과: D-202610-01)", FW.summary(ledger))

    def test_past_values_are_the_advisors(self):
        """고문 집계 2026-11-20 ② (봉 기준, 비용 뒤). (거래당 평균, 일별 평균)."""
        self.assertEqual(FW.CR_PAST, {"CR_rebound": (("2017–20", 0.00219, 0.00410), ("2021–26", 0.00361, 0.00507)),
                                      "CR_drop5": (("2017–20", 0.00484, 0.00674), ("2021–26", 0.00681, 0.00875)),
                                      "CR_drop6": (("2017–20", 0.00958, 0.01155), ("2021–26", 0.01022, 0.01198)),
                                      "CR_hold4h": (("2017–20", 0.00283, 0.00305), ("2021–26", 0.00456, 0.00640))})
        reg = json.loads((ROOT / "scalp" / "research" / "forward" / "registry.json").read_text(encoding="utf-8"))
        c = next(c for c in reg if c["id"] == "A-202611-01")
        self.assertEqual(((c["train"]["net"], c["train"]["daymean"]), (c["holdout"]["net"], c["holdout"]["daymean"])), ((0.0145, 0.00708), (0.01541, 0.00747)))

    def test_crash_rebound_heading_says_stage_one(self):
        rows = [cr_trade(k, 0.01) for k in range(40)]
        ledger = {"last_run": "2026-12-30", "CR_rebound": FW.cr_decide({"rows": rows, "decision": None}, "2026-12-30")}
        text = FW.render(ledger)
        self.assertIn("## 급락 반등 — 1단계 선별 끝: 통과 (2026-12-30, 첫 30건)", text)
        self.assertIn("급락 반등: 1단계 선별 통과 (2026-12-30)", FW.summary(ledger))


class StageThree(unittest.TestCase):
    """고문 판정 2026-11-20 ② 6: 3단계(증액 기준) = 전진 일별 평균의 99% 하한 > 0. 전진 진입일 30·60·90일이 처음 된 때만, 그 값을 고정."""

    def test_a_point_uses_the_first_n_days_and_its_99_percent_lower_bound(self):
        rows = [cr_trade(k, 0.01 if k % 2 else 0.002, per_day=2) for k in range(70)]                   # 하루 2건, 일별 평균 0.6%
        self.assertIsNone(FW.stage3_point(rows[:58], 30, by_cr_day))
        p = FW.stage3_point(rows, 30, by_cr_day)
        self.assertEqual((p["days"], p["trades"]), (30, 60))
        self.assertAlmostEqual(p["daymean"], 0.006)
        self.assertAlmostEqual(p["low99"], 0.006)                                                      # 날마다 같아 흔들림 0
        noisy = [cr_trade(k, 0.03 if (k // 2) % 2 else -0.025, per_day=2) for k in range(60)]          # 일별 +3%, -2.5% 번갈아
        q = FW.stage3_point(noisy, 30, by_cr_day)
        means = [0.03 if d % 2 else -0.025 for d in range(30)]
        m = sum(means) / 30
        sd = (sum((x - m) ** 2 for x in means) / 29) ** 0.5
        self.assertAlmostEqual(q["low99"], m - 2.462 * sd / 30 ** 0.5)
        self.assertLess(q["low99"], 0)
        self.assertEqual(FW.STAGE3_POINTS, (30, 60, 90))

    def test_points_are_frozen_and_shown(self):
        args = offline()
        ledger, _ = FW.update({"CR_rebound": {"rows": [cr_trade(k, 0.01, per_day=2) for k in range(60)], "decision": None}}, "2027-01-01", **args)
        p = ledger["CR_rebound"]["stage3"]["30"]
        frozen = copy.deepcopy(p)
        ledger["CR_rebound"]["rows"] = [cr_trade(k, -0.05, per_day=2) for k in range(70)]
        ledger, _ = FW.update(ledger, "2027-01-08", **args)
        self.assertEqual(ledger["CR_rebound"]["stage3"]["30"], frozen)
        self.assertNotIn("60", ledger["CR_rebound"]["stage3"])
        text = FW.render(ledger)
        self.assertIn("## 3단계 점검 — 증액 기준 (표시만)", text)
        self.assertIn("| 급락 반등 | 35일 | 2027-01-01 기록: 일별 평균 +1.00%, 99% 하한 +1.00% → 충족", text)
        self.assertIn("| 점검 전 (35 / 60일) | 점검 전 (35 / 90일) |", text)


def by_cr_day(t):
    return FW._utc_day(t["entry_min"])


class Pairs(unittest.TestCase):
    """고문 판정 2026-11-20 ② 7: 후보의 거래마다 같은 진입·청산 시각의 BTC·ETH 봉 기준 수익(비용 0.07%)을 같이 적는다. 판정 없음."""

    def test_btc_and_eth_bought_at_the_same_bars_are_recorded_and_shown(self):
        q0, panel = crash_panel(150)
        eth = {q: (200.0 + (q - q0) * 0.01, 1.0, 1.0, 1.0, 1e9) for q in range(q0, q0 + 150 * 96)}       # 15분마다 0.01 오르는 ETH
        panel = dict(panel, **{"KRW-ETH": DISC.Bars(eth)})
        cand = RegisteredCandidates().cand(DISC.q_day(q0 + 70 * 96), universe=["KRW-BTC", "KRW-ETH", "KRW-AAA"])
        cut = RegisteredCandidates().cut
        ledger, _ = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[cand], **offline(cut(panel, q0, 90)))
        rows = ledger["candidates"]["D-202610-01"]["rows"]
        self.assertTrue(rows and all(set(r["pair"]) == {"KRW-BTC", "KRW-ETH"} for r in rows))
        r = rows[0]
        self.assertAlmostEqual(r["pair"]["KRW-BTC"], (1 - 0.0007) / (1 + 0.0007) - 1, places=9)        # BTC 는 평평
        e_in, e_out = eth[r["entry_q"]][0], eth[r["exit_q"]][0]
        self.assertAlmostEqual(r["pair"]["KRW-ETH"], e_out / e_in * (1 - 0.0007) / (1 + 0.0007) - 1, places=9)
        kept = copy.deepcopy(rows[0]["pair"])
        rows[0]["pair"] = {"KRW-BTC": 9.0}                                                             # 이미 적힌 값은 다시 계산하지 않는다
        ledger, _ = FW.update(ledger, DISC.q_day(q0 + 91 * 96), registry=[cand], **offline(cut(panel, q0, 91)))
        self.assertEqual(ledger["candidates"]["D-202610-01"]["rows"][0]["pair"], {"KRW-BTC": 9.0})
        rows = [t for t in ledger["candidates"]["D-202610-01"]["rows"] if "KRW-ETH" in t["pair"]]
        text = FW.render(ledger)
        self.assertIn("## 짝 관찰 — 같은 시각에 BTC·ETH 를 샀다면 (판정 없음)", text)
        line = next(ln for ln in text.splitlines() if ln.startswith("| 후보 D-202610-01 | ") and "BTC" not in ln and ln.count("|") == 5)
        self.assertIn("(%d건)" % len(ledger["candidates"]["D-202610-01"]["rows"]), line)
        self.assertEqual(FW.pair_returns({}, {"entry_q": 1, "exit_q": 2}), {})                         # BTC·ETH 시세가 없으면 적지 않는다
        self.assertEqual((FW.PAIR_MARKETS, FW.PAIR_COST), (("KRW-BTC", "KRW-ETH"), 0.0007))
        self.assertTrue(kept)


if __name__ == "__main__":
    unittest.main()
