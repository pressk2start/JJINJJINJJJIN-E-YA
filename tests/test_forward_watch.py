# -*- coding: utf-8 -*-
"""forward_watch 테스트 — 장부는 덧붙이기만 하는지, 판정은 첫 20기간으로 한 번만 하는지, 시세를 못 받으면 장부를 건드리지 않는지."""
import contextlib
import copy
import datetime as dt
import hashlib
import importlib.util
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
        self.assertIn("| −6% | 1시간에 6% 넘게 빠지면 60분 보유 | 5 / 30건, 2 / 10일 | -5.00% | -5.00% | 0% | -5.00% | 만료(표본 부족) |", FW.render(ledger))
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
            self.assertIn("| %s | %s | 0 / 30건, 0 / 10일 | - | - | - | - | 진행 중 |" % (v["name"], v["rule"]), text)
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
        self.assertEqual((c["verdict"], c["expire_days"], c["status"], c["engine"]), ("ex_best_day", 730, "ACTIVE", DISC.ENGINE))

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
                                             "CRASH": {"7": [2, 0.01], "28": [9, -0.002]}}},
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
        for line in ("| 급등 뒤 1시간 보유 (조건 없음) | 3 | -0.40% | 11 | -0.60% |", "| 돌파 뒤 1시간 보유 (조건 없음) | 0 | - | 40 | +0.10% |",
                     "| 급락 뒤 1시간 보유 (조건 없음) | 2 | +1.00% | 9 | -0.20% |",
                     "| H2 김치 되돌림 (3일 기간 단위) (자료 반영 2026-11-23) | 1 | -3.00% | 2 | -1.00% |",   # 주 1회 반영이라 날짜를 붙인다
                     "| 급락 반등 | 2 | +0.50% | 4 | +1.25% |",                                        # 7일 전과 1일 전 / 28일 전까지. 40일 전은 어느 쪽에도 없다
                     "| 급락 반등 변형 −5% | 1 | +0.40% | 1 | +0.40% |", "| 급락 반등 변형 −6% | 0 | - | 0 | - |",
                     "| 급락 반등 변형 4시간 보유 (아직 채점 전) | - | - | - | - |",                       # 한 번도 채점되지 못한 항목은 0건으로 보이지 않는다
                     "| 후보 A-202611-01 | 1 | -1.40% | 1 | -1.40% |"):
            self.assertIn(line, text)
        self.assertIn("고문의 고정 20종목 가운데 19종목(상장 목록에 없어 뺀 종목: WAVES)의 15분봉", text)
        self.assertIn("급등 -0.770%(924건)", text)                                                     # 고문 집계 과거 평균을 옆에 둔다
        self.assertNotIn("관찰", FW.summary(ledger))                                                   # 텔레그램 요약에는 싣지 않는다
        ledger["observation"].update(markets=20, missing=[])
        self.assertIn("기본 사건(위 3줄): 고문의 고정 20종목의 15분봉.", FW.render(ledger))
        del ledger["observation"]
        self.assertIn("기본 사건(급등·돌파·급락)은 아직 계산된 값이 없습니다", FW.render(ledger))



class FakeTicks(object):
    """업비트 /v1/trades/ticks 흉내. trades = {종목: [(UTC 시각 ms, 가격, 수량, "ASK"|"BID")]}. 2026-11-19 에 확인한 동작 그대로:
    days_ago 는 그 종목의 가장 최근 체결 날짜 기준, to 는 그날의 그 초 직전까지, 새 것부터 count 건, cursor 는 sequential_id 이전.
    days_ago 가 7 을 넘으면 400."""

    def __init__(self, trades, loose_to=False):
        self.trades = {m: sorted(v, reverse=True) for m, v in trades.items()}
        self.calls = []
        self.loose_to = loose_to                                                                       # to 의 그 초까지 주는 경우(우리 쪽 구간 거르기 시험용)

    def __call__(self, url):
        import urllib.parse
        self.calls.append(url)
        q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        rows = self.trades.get(q["market"])
        if rows is None:
            raise RuntimeError("조회 실패 (HTTP 404)")
        utc = dt.timezone.utc
        out = []
        latest = dt.datetime.fromtimestamp(rows[0][0] / 1000.0, tz=utc).date()
        ago = int(q.get("days_ago", 0))
        if ago > 7:
            raise RuntimeError("조회 실패 (HTTP 400)")
        day = latest - dt.timedelta(days=ago)
        to = q.get("to")
        for k, (ms, price, vol, side) in enumerate(rows):
            t = dt.datetime.fromtimestamp(ms / 1000.0, tz=utc)
            seq = ms * 10000 + (len(rows) - k)
            if "count" in q and q["count"] == "1" and not to:
                return [{"trade_date_utc": t.date().isoformat(), "timestamp": ms, "sequential_id": seq, "trade_price": price, "trade_volume": vol, "ask_bid": side}]
            if t.date() != day or (to and t.strftime("%H%M%S") > to if self.loose_to else to and t.strftime("%H%M%S") >= to) or (
                    "cursor" in q and seq >= int(q["cursor"])):
                continue
            out.append({"trade_date_utc": t.date().isoformat(), "trade_time_utc": t.strftime("%H:%M:%S"), "timestamp": ms, "trade_price": price,
                        "trade_volume": vol, "ask_bid": side, "sequential_id": seq})
            if len(out) >= int(q["count"]):
                break
        return out


def ms(text):
    return int(AM.parse_utc(text[:16]).timestamp() * 1000) + int(float(text[17:] or 0) * 1000) if len(text) > 16 else int(AM.parse_utc(text).timestamp() * 1000)


class Fills(unittest.TestCase):
    """고문 판정 2026-11-19: 봉 기준 수익이 실제 체결에서도 남는지 — 업비트 공개 체결 기록으로 다시 계산한다."""
    ENTRY, EXIT = "2026-11-15T01:15", "2026-11-15T02:15"

    def world(self, extra=(), latest="2026-11-19T09:00"):
        e, x = ms(self.ENTRY), ms(self.EXIT)
        trades = [(e - 1000, 50.0, 100.0, "BID"),                                  # 구간 앞: 세지 않는다
                  (e + 1000, 61.0, 10.0, "BID"), (e + 20000, 62.0, 30.0, "BID"),   # 매수 VWAP = (610 + 1860) / 40 = 61.75
                  (e + 30000, 60.0, 50.0, "ASK"),                                  # 진입 구간의 매도 체결: 금액만 센다
                  (e + 60000, 99.0, 100.0, "BID"),                                 # 60초째: 구간 밖
                  (x + 5000, 59.0, 20.0, "ASK"), (x + 50000, 60.0, 20.0, "ASK"),   # 매도 VWAP = 59.5
                  (x + 10000, 59.8, 5.0, "BID"),
                  (ms(latest), 1.0, 1.0, "BID")] + list(extra)                     # 그 종목의 가장 최근 체결
        return FakeTicks({"KRW-KAVA": trades})

    def test_buy_and_sell_are_the_amount_weighted_market_orders_of_the_first_sixty_seconds(self):
        get = self.world()
        f = FW.fill_check("KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), 61.4, 59.4, get)
        self.assertEqual(f["status"], "ok")
        self.assertAlmostEqual(f["buy"], 61.75)
        self.assertAlmostEqual(f["sell"], 59.5)
        self.assertAlmostEqual(f["net"], 59.5 * (1 - 0.0005) / (61.75 * (1 + 0.0005)) - 1, places=9)    # 수수료 편도 0.05%
        self.assertAlmostEqual(f["buy_vs_open"], 61.75 / 61.4 - 1, places=9)
        self.assertAlmostEqual(f["sell_vs_open"], 59.5 / 59.4 - 1, places=9)
        self.assertEqual((f["entry_bid_krw"], f["entry_ask_krw"], f["exit_bid_krw"], f["exit_ask_krw"]), (2470, 3000, 299, 2380))
        days = [u for u in get.calls if "days_ago" in u]
        self.assertTrue(days and all("days_ago=4" in u for u in days))                                 # 11/19 기준 4일 전
        self.assertTrue(any("to=011600" in u for u in days) and any("to=021600" in u for u in days))    # 각 구간 끝의 초 직전까지
        self.assertEqual((FW.FILL_SEC, FW.FILL_FEE, FW.FILL_DAYS), (60, 0.0005, 7))
        loose = self.world()
        loose.loose_to = True                                                                          # 업비트가 60초째 체결까지 주더라도 우리 쪽에서 뺀다
        self.assertAlmostEqual(FW.fill_check("KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), 61.4, 59.4, loose)["buy"], 61.75)

    def test_days_ago_counts_from_the_coins_own_latest_trade_date(self):
        """오늘 아직 체결이 없는 종목은 업비트가 그 종목의 마지막 체결 날짜를 기준으로 센다 — 달력의 오늘로 세면 하루가 밀린다."""
        get = self.world(latest="2026-11-18T23:00")
        get.trades["KRW-KAVA"] = [t for t in get.trades["KRW-KAVA"] if t[0] < ms("2026-11-19T00:00")]
        f = FW.fill_check("KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), 61.4, 59.4, get)
        self.assertEqual(f["status"], "ok")
        self.assertTrue(all("days_ago=3" in u for u in get.calls if "days_ago" in u))

    def test_no_market_buy_or_sell_in_the_window_is_no_fill_and_the_amounts_stay(self):
        get = self.world()
        get.trades["KRW-KAVA"] = [t for t in get.trades["KRW-KAVA"] if not (t[3] == "BID" and ms(self.ENTRY) <= t[0] < ms(self.ENTRY) + 60000)]
        f = FW.fill_check("KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), 61.4, 59.4, get)
        self.assertEqual((f["status"], f["entry_bid_krw"], f["entry_ask_krw"], f["exit_ask_krw"]), ("체결 없음", 0, 3000, 2380))
        self.assertNotIn("net", f)
        get = self.world()
        get.trades["KRW-KAVA"] = [t for t in get.trades["KRW-KAVA"] if not (t[3] == "ASK" and t[0] >= ms(self.EXIT))]
        self.assertEqual(FW.fill_check("KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), 61.4, 59.4, get)["status"], "체결 없음")

    def test_older_than_seven_days_is_no_data_without_asking_for_the_trades(self):
        get = self.world(latest="2026-11-23T00:30")
        f = FW.fill_check("KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), 61.4, 59.4, get)
        self.assertEqual(f, {"status": "체결 자료 없음"})
        self.assertEqual(len(get.calls), 1)                                                            # 최근 체결 하나만 묻고 끝
        get = self.world(latest="2026-11-22T23:59")                                                    # 7일째까지는 받는다
        self.assertEqual(FW.fill_check("KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), 61.4, 59.4, get)["status"], "ok")

    def test_more_than_one_page_of_trades_is_followed_with_the_cursor(self):
        e = ms(self.ENTRY)
        busy = [(e + 2000 + k * 40, 70.0, 1.0, "BID") for k in range(1200)]                           # 60초 안에 1,200건 + 앞의 두 건
        get = self.world(extra=busy)
        f = FW.fill_check("KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), 61.4, 59.4, get)
        self.assertAlmostEqual(f["buy"], (610 + 1860 + 70.0 * 1200) / (40 + 1200))
        self.assertGreaterEqual(sum(1 for u in get.calls if "cursor=" in u), 2)
        real = FW.TICKS_MAX_PAGES
        FW.TICKS_MAX_PAGES = 2
        try:
            with self.assertRaises(RuntimeError):                                                      # 끝까지 못 받으면 값을 내지 않는다
                FW.fill_check("KRW-KAVA", ms(self.ENTRY), ms(self.EXIT), 61.4, 59.4, self.world(extra=busy))
        finally:
            FW.TICKS_MAX_PAGES = real

    def candidate(self):
        return {"id": "A-1", "engine": DISC.ENGINE, "registered": "2026-11-13", "start": "2026-11-14", "event": "CRASH", "hold": 4, "cond": None,
                "universe": ["KRW-BTC", "KRW-KAVA"], "move": 0.03, "vol_x": 3.0, "cost": {"KRW-BTC": 0.0007}, "cost_other": 0.001, "status": "ACTIVE"}

    def scored(self, fill_get, ledger=None, today="2026-11-19", listed=None):
        """후보 A-1 의 11/15 KAVA 거래 한 건이 채점된 상태로 update 를 돌린다. 패널에는 진입·청산 봉의 시가가 있다."""
        q_in, q_out = ms(self.ENTRY) // 900000, ms(self.EXIT) // 900000
        row = {"market": "KRW-KAVA", "entry_q": q_in, "exit_q": q_out, "day": "2026-11-15", "gross": -0.0326, "net": -0.0345, "move": -0.04}
        real = DISC.candidate_trades
        DISC.candidate_trades = lambda panel, cand, end_q: [dict(row)]
        bars = {q: (61.4 if q == q_in else 59.4 if q == q_out else 60.0, 62.0, 58.0, 60.0, 1e9) for q in range(q_in - 4, q_out + 4)}
        panel = {"KRW-BTC": DISC.Bars(bars), "KRW-KAVA": DISC.Bars(bars)}
        args, start = clean("2026-11-16")                                                             # 다른 항목은 경고 없이 끝나게
        args["panel_loader"] = lambda s, e, markets: {m: panel[m] for m in markets}
        if listed is not None:
            args["list_markets"] = lambda: listed
        try:
            return FW.update(ledger if ledger is not None else start, today, registry=[self.candidate()], fill_get=fill_get, **args)
        finally:
            DISC.candidate_trades = real

    def test_the_tracker_attaches_fills_once_and_reports_them_beside_the_bar_value(self):
        get = self.world()
        ledger, warnings = self.scored(get)
        f = ledger["candidates"]["A-1"]["rows"][0]["fill"]
        self.assertEqual((warnings, f["status"]), ([], "ok"))
        self.assertAlmostEqual(f["buy_vs_open"], 61.75 / 61.4 - 1, places=9)                           # 봉 시가는 그 후보의 패널에서
        self.assertAlmostEqual(f["sell_vs_open"], 59.5 / 59.4 - 1, places=9)                           # 청산 봉의 시가와 비교한다
        n = len(get.calls)
        ledger, _ = self.scored(get, ledger, "2026-11-20")
        self.assertEqual(len(get.calls), n)                                                            # 이미 붙은 거래는 다시 받지 않는다
        item = next(x for x in self.scored(self.world())[0]["new"]["items"])
        self.assertAlmostEqual(item["fill"], f["net"])
        text = FW.render(ledger)
        self.assertIn("## 체결 기준 확인 (공개 체결 내역으로 추정) — 1단계 선별은 등록된 대로 봉 기준", text)
        self.assertIn("| 후보 A-1 | 1 | 1 | -3.45%% | -3.45%% → %s | %s (-) | %s / %s | 0%% (0 / 1) | 2,380원 | 0 |" % (
            FW._pct(f["net"]), FW._pct(-0.0345 - f["net"]), FW._pct(f["buy_vs_open"]), FW._pct(f["sell_vs_open"])), text)   # 60초 체결 금액 = min(진입 매수 2,470, 청산 매도 2,380)
        self.assertIn("- 후보 A-1 KAVA 11/15 10:15: 봉 -3.45%% → 체결 %s (매수가 %s, 매도가 %s / 60초 체결 금액: 진입 매수 2,470원·매도 3,000원, 청산 매수 299원·매도 2,380원)" % (
            FW._pct(f["net"]), FW._pct(f["buy_vs_open"]), FW._pct(f["sell_vs_open"])), text)
        self.assertIn("(체결 기준 %s)" % FW._pct(f["net"]), FW.summary(self.scored(self.world())[0]))
        self.assertEqual(ledger["candidates"]["A-1"]["decision"], None)                                # 판정은 봉 기준 그대로(아직 1건)

    def test_a_failed_lookup_is_retried_on_the_next_run_and_fails_that_run(self):
        def down(url):
            raise RuntimeError("조회 실패 (TimeoutError)")
        ledger, warnings = self.scored(down)
        self.assertNotIn("fill", ledger["candidates"]["A-1"]["rows"][0])
        self.assertEqual(warnings, ["체결 기준: 1건 조회 실패 — 비워 두고 다음 실행에서 다시 받음 (후보 A-1 KAVA (RuntimeError))"])
        self.assertIsNone(ledger.get("last_ok"))                                                       # 뒤의 예약이 다시 돈다
        ledger, warnings = self.scored(self.world(), ledger)
        self.assertEqual((warnings, ledger["candidates"]["A-1"]["rows"][0]["fill"]["status"]), ([], "ok"))
        late, _ = self.scored(self.world(latest="2026-11-24T01:00"), self.scored(down)[0], "2026-11-24")   # 그사이 7일이 지났으면 자료 없음
        self.assertEqual(late["candidates"]["A-1"]["rows"][0]["fill"], {"status": "체결 자료 없음"})

    def test_a_delisted_coin_is_no_data_without_a_lookup_and_nothing_runs_without_a_fetcher(self):
        get = self.world()
        ledger, warnings = self.scored(get, dict(clean("2026-11-16")[1], candidates={"A-1": {"rows": [], "decision": None, "delisted": {"KRW-KAVA": "2026-11-16"}}}))
        rows = ledger["candidates"]["A-1"]["rows"]
        self.assertTrue(all(r["fill"] == {"status": "체결 자료 없음", "why": "상장폐지"} for r in rows))
        self.assertEqual(get.calls, [])
        plain, _ = self.scored(None)                                                                   # 시험·점검 실행: 체결은 건드리지 않는다
        self.assertNotIn("fill", plain["candidates"]["A-1"]["rows"][0])
        self.assertNotIn("체결 기준", FW.summary(plain))

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
        self.assertIn("| 급락 반등 | 1 | 1 | -3.20% | -3.20% → ", FW.render(ledger))
        self.assertEqual(ledger["CR_rebound"]["rows"][0]["market"], "KRW-BTC")
        self.assertIn("| 급락 반등 변형 4시간 보유 | 0 | 0 | - | - → - | - (-) | - / - | - | - | 0 |", FW.render(ledger))

    def test_the_real_run_looks_up_fills(self):
        got = {}

        def spy(ledger, today, **kw):
            got.update(kw)
            return ledger, []
        real = FW.update
        FW.update = spy
        try:
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
                FW.main(["--out", d, "--dry-run", "--today", "2026-11-20"])
        finally:
            FW.update = real
        self.assertIs(got["fill_get"], AM.http_get)


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
        self.assertIn("| −5% | 1시간에 5% 넘게 빠지면 60분 보유 | 3 / 30건, 1 / 10일 | -1.00% | -1.00% | 0% | -1.00% | 진행 중 |", text)
        self.assertIn("| +0.50% | 100% | +0.50% | 진행 중 |", text)                                    # 후보: 거래당 평균, 일별 승률, 일별 중앙값, 판정
        self.assertIn("일별 승률 = 거래가 있었던 날 가운데", text)



def filled(net, fill_net=None, status="ok", day="2026-11-15", bid=5e6, ask=5e6, market="KRW-KAVA", k=0):
    """체결 값이 붙은 후보 거래 하나."""
    f = {"status": status, "entry_bid_krw": int(bid), "entry_ask_krw": 0, "exit_bid_krw": 0, "exit_ask_krw": int(ask)}
    if status == "ok":
        f.update(net=fill_net, buy=1.0, sell=1.0, buy_vs_open=0.001, sell_vs_open=-0.002)
    elif status == "체결 자료 없음":
        f = {"status": status}
    return {"market": market, "entry_q": 1000 + k, "exit_q": 1004 + k, "day": day, "gross": net, "net": net, "move": -0.04, "fill": f}


class StageTwo(unittest.TestCase):
    """고문 판정 2026-11-19 ②: 지금 판정은 '1단계 선별'. 2단계 네 항목은 표시만 한다(자동 결정 없음)."""

    def test_t_quantiles(self):
        self.assertEqual((FW.t95(1), FW.t95(19), FW.t95(30)), (6.314, 1.729, 1.697))
        self.assertAlmostEqual(FW.t95(40), 1.685)
        self.assertAlmostEqual(FW.t95(120), 1.6583, places=3)

    def test_fill_statistics(self):
        rows = [filled(0.02, 0.015, k=0), filled(0.01, 0.002, k=1, bid=3e6, ask=9e6), filled(-0.01, -0.013, k=2),
                filled(0.03, status="체결 없음", bid=0, ask=8e6, k=3), filled(0.0, status="체결 자료 없음", k=4)]
        fs = FW.fill_stats(rows)
        self.assertEqual((fs["rows"], fs["ok"], fs["tried"], fs["nofill"], fs["nodata"]), (5, 3, 4, 1, 1))
        gaps = [0.005, 0.008, 0.003]
        mean = sum(gaps) / 3
        sd = (sum((g - mean) ** 2 for g in gaps) / 2) ** 0.5
        self.assertAlmostEqual(fs["gap"], mean)
        self.assertAlmostEqual(fs["gap_up95"], mean + 2.920 * sd / 3 ** 0.5)
        self.assertEqual(fs["nofill_rate"], 0.25)                                                     # 체결 자료 없음은 분모에 넣지 않는다
        self.assertEqual(fs["depth"], (3e6 + 5e6) / 2)                                                # min(매수, 매도): 5,3,5,0 → 중앙값 4백만
        self.assertEqual(FW.fill_stats([filled(0.01, 0.0)])["gap_up95"], None)                        # 한 건으로는 상한을 낼 수 없다

    def stage(self, rows, decision=None, past=(("앞", 0.0145, 0.012), ("뒤", 0.01541, 0.015))):
        return FW.stage2(rows, decision, list(past), lambda t: t["day"])

    def test_before_enough_fills_nothing_is_decided(self):
        rows = [filled(0.01, 0.005, k=k) for k in range(3)]
        r = self.stage(rows)
        self.assertEqual([x[0] for x in r], [None, None, None, None])
        self.assertEqual(r[0][1], "진행 중")
        self.assertEqual(r[1][1], "체결 자료 3 / 20건 (과거 우위 +1.45%)")                            # 두 시기 중 낮은 쪽
        self.assertIn("자료 부족", r[2][1])
        self.assertEqual(r[3][1], "전진 진입일 1일 — 표준오차를 낼 수 없음")

    def test_each_item_is_met_or_missed_by_its_own_rule(self):
        days = ["2026-11-%02d" % d for d in range(1, 21)]
        good = [filled(0.012 + 0.001 * (k % 3), 0.009 + 0.001 * (k % 3), day=days[k], k=k, bid=2e7, ask=2e7) for k in range(20)]   # 차이 0.3%p
        r = self.stage(good, {"verdict": "PASS"})
        self.assertEqual([x[0] for x in r], [True, True, True, True])
        self.assertTrue(r[1][1].startswith("+1.45% − +0.30% = +1.15% (체결 20건)"))
        self.assertEqual(r[2][1], "체결 없음 0%, 60초 체결 금액 중앙값 20,000,000원 (기준 10,000,000원, 20건)")
        wide = [dict(t, fill=dict(t["fill"], net=t["net"] - 0.016)) for t in good]                     # 체결 차이 1.6%p: 과거 우위를 다 먹는다
        self.assertIs(self.stage(wide, {"verdict": "PASS"})[1][0], False)
        thin = [dict(t, fill=dict(t["fill"], entry_bid_krw=9_000_000)) for t in good]                 # 60초 매수 체결이 주문의 9배
        self.assertIs(self.stage(thin)[2][0], False)
        missing = good[:17] + [filled(0.01, status="체결 없음", day=days[17 + k], k=17 + k) for k in range(3)]   # 체결 없음 15%
        self.assertIs(self.stage(missing)[2][0], False)
        bad = [dict(t, net=-0.01) for t in good]                                                      # 전진 일별 평균 -1%: 과거 1.2% 에서 크게 떨어짐
        self.assertIs(self.stage(bad, {"verdict": "FAIL"})[0][0], False)
        self.assertIs(self.stage(bad)[3][0], False)
        near = [dict(t, net=0.0 if k % 2 else 0.02) for k, t in enumerate(good)]                       # 전진 1.0% < 과거 1.2% 지만 2 표준오차 안
        r = self.stage(near)
        self.assertIs(r[3][0], True)
        self.assertIn("전진 +1.00% ≥ 과거 +1.20% − 2 ×", r[3][1])
        self.assertEqual(self.stage(good, past=())[1][1], "과거 우위 미기록")
        self.assertEqual(self.stage(good, past=(("2017–20", 0.002, None),))[3][1], "과거 일별 평균 미기록")
        self.assertEqual((FW.ORDER_KRW, FW.STAGE2_MIN_FILLS, FW.STAGE2_MAX_NOFILL, FW.STAGE2_DEPTH_X), (1000000, 20, 0.10, 10))

    def test_the_status_page_shows_both_stages_and_decides_nothing(self):
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
        self.assertIn("## 2단계 점검 — 표시만 (자동 결정 없음. 소액 실거래 시험은 고문과 사용자가 정한다)", text)
        self.assertIn("| 후보 D-202610-01 | 충족 — 통과 | 체결 자료 0 / 20건 (과거 우위 +1.50%) | 체결 자료 없음 |", text)
        self.assertIn("| 급락 반등 | 진행 중 | 체결 자료 0 / 20건 (과거 우위 +0.22%) | 체결 자료 없음 | 과거 일별 평균 미기록 |", text)
        self.assertIn("| 번호 | 규칙 | 등록 | 진행 | 거래당 평균 | 일별 승률 | 일별 중앙값 | 1단계 선별 |", text)
        self.assertIn("1단계 선별 통과는 '계속 볼 가치가 있다' 는 뜻이지 돈을 넣는 신호가 아닙니다", text)
        self.assertIn("자동 등록 후보: 1개 추적, 1단계 선별 끝 1개 (통과: D-202610-01)", FW.summary(ledger))
        self.assertEqual(FW.CR_PAST["CR_rebound"][0][1], 0.00219)                                    # 머리말의 고문 집계와 같은 값
        self.assertEqual(sorted(FW.CR_PAST), sorted(["CR_rebound"] + [v["key"] for v in FW.CR_VARIANTS]))

    def test_crash_rebound_heading_says_stage_one(self):
        rows = [cr_trade(k, 0.01) for k in range(40)]
        ledger = {"last_run": "2026-12-30", "CR_rebound": FW.cr_decide({"rows": rows, "decision": None}, "2026-12-30")}
        text = FW.render(ledger)
        self.assertIn("## 급락 반등 — 1단계 선별 끝: 통과 (2026-12-30, 첫 30건)", text)
        self.assertIn("급락 반등: 1단계 선별 통과 (2026-12-30)", FW.summary(ledger))


if __name__ == "__main__":
    unittest.main()
