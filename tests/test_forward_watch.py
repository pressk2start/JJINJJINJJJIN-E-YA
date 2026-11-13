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

    def test_two_years_without_enough_trades_ends_as_expired_not_failed(self):
        thin = {"rows": [cr_trade(k, -0.05) for k in range(5)], "decision": None}
        self.assertIsNone(FW.cr_decide(thin, "2028-11-13", FW.CRV_START)["decision"])                 # 730일째까지는 기다린다
        FW.cr_decide(thin, "2028-11-15", FW.CRV_START)
        self.assertEqual((thin["decision"]["verdict"], thin["decision"]["trades"]), ("EXPIRED", 5))
        self.assertIsNone(FW.cr_decide({"rows": [], "decision": None}, "2030-01-01")["decision"])     # 시작 시각을 안 주면 만료도 없다
        ledger, _ = FW.update({"CR_variants": {"CR_drop6": thin}}, "2026-11-13", **offline())
        self.assertIn("| −6% | 1시간에 6% 넘게 빠지면 60분 보유 | 5 / 30건, 2 / 10일 | -5.00% | -5.00% | 만료(표본 부족) |", FW.render(ledger))
        self.assertIn("−6% 만료(표본 부족)", FW.summary(ledger))

    def test_status_shows_the_variants_and_what_is_still_waiting(self):
        ledger, _ = FW.update({}, "2026-11-13", **offline())
        text = FW.render(ledger)
        self.assertIn("## 급락 반등 변형", text)
        for v in FW.CR_VARIANTS:
            self.assertIn("| %s | %s | 0 / 30건, 0 / 10일 | - | - | 진행 중 |" % (v["name"], v["rule"]), text)
        self.assertIn("고문 정의(2026-11-13 확정)", text)
        self.assertIn("## 대기 중\n\n- 없음", text)                                                  # 알트 동반 급락은 등록부(registry.json)로 옮겼다
        self.assertIn("급락 반등 변형: −5% 0건, −6% 0건, 4시간 보유 0건", FW.summary(ledger))


class MissedRuns(unittest.TestCase):
    """'며칠 지났나' 가 아니라 '가장 최근 예약일의 실행이 기록에 있나' 를 본다 (제3세션 지적 2026-11-13)."""

    def ledger(self, day, **over):
        out = {"last_run": day, "H2_kimchi": {"updated": day}, "H3_trend": {"as_of": day}, "CR_rebound": {"updated": day},
               "BTC_surge": {"updated": day}, "CR_variants": {v["key"]: {"updated": day} for v in FW.CR_VARIANTS},
               "candidates": {"A-1": {"updated": day, "decision": None}, "D-0": {"updated": "2026-01-01", "decision": {"verdict": "FAIL"}}}}
        out.update(over)
        return out

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
        out = FW.stale(ledger, [], "2026-11-18")
        self.assertEqual(out, ["김치 프리미엄: 2026-11-16(월) 이후 갱신되지 못함 — 마지막 갱신 2026-11-11",
                               "급락 반등 변형 −6%: 2026-11-16(월) 이후 갱신되지 못함 — 마지막 갱신 없음",
                               "후보 A-1: 2026-11-16(월) 이후 갱신되지 못함 — 마지막 갱신 2026-11-09"])       # 판정이 끝난 D-0 은 보지 않는다

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
            self.assertIn("if: failure()", last, name)                                                # 마지막 단계가 실패 알림이다
            self.assertIn("scalp/research/tg_send.py", last, name)
            self.assertNotIn("curl", text, name)                                                      # 응답을 버리는 전송은 쓰지 않는다
            self.assertNotIn("sendMessage", text, name)

    def test_the_watchdog_runs_between_weekly_runs_and_writes_nothing(self):
        text = (self.WF / "watchdog.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "17 1 * * 3"', text)
        self.assertIn('cron: "17 1 * * 1"', (self.WF / "forward-watch.yml").read_text(encoding="utf-8"))
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
                crv_fetch=lambda m, s, e: [])


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
        self.assertTrue(any("자동 등록 후보" in w for w in warnings))

    def test_a_candidate_from_an_older_frame_is_not_scored(self):
        q0, panel = crash_panel(150)
        reg = DISC.q_day(q0 + 70 * 96)
        ledger, warnings = FW.update({}, DISC.q_day(q0 + 90 * 96), registry=[self.cand(reg, engine="1")], **offline(self.cut(panel, q0, 90)))
        self.assertEqual(ledger["candidates"]["D-202610-01"]["rows"], [])
        self.assertTrue(any("틀" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
