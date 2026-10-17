# -*- coding: utf-8 -*-
"""trade_features.csv 헤더 불일치 → 청산 결과 미기록 결함 재현 + 수정 검증.

결함 (2026-10-17 서버 관측: result 공백 723/730):
  2026-07-19 커밋 6df0241 이 FEATURE_FIELDS 끝에 v7 필드 4개를 추가했지만 서버의 기존
  CSV 헤더(40열)는 그대로. 이후 append 행은 44열 → csv.DictReader 가 초과 값을 key=None
  에 담음 → update_trade_result 의 DictWriter.writerows 가 ValueError
  ("dict contains fields not in fieldnames: None") → except 로 삼켜져 청산 결과가
  한 번도 기록되지 않음.

접근: bot.py 를 import 하지 않고 AST 로 _trade_log_migrate_header / log_trade_features /
FEATURE_FIELDS 를 추출해 임시 디렉터리에서 실행.

실행: python3 -m pytest tests/test_trade_features_header_migrate.py -v
"""
import ast
import csv
import io
import os
import tempfile
import threading
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(REPO_ROOT, "bot.py")


def _load():
    with open(BOT_PATH, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    nodes = []
    fields = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in (
                "_trade_log_migrate_header", "log_trade_features"):
            nodes.append(node)
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "FEATURE_FIELDS" for t in node.targets):
            fields = ast.literal_eval(node.value)
    assert len(nodes) == 2 and fields, "required symbols missing in bot.py"
    ns = {"os": os, "csv": csv, "threading": threading,
          "_trade_log_lock": threading.Lock(), "FEATURE_FIELDS": fields}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), BOT_PATH, "exec"), ns)
    return ns, fields


# 서버 trade_features.csv 의 실제 헤더 (2026-10-17 `head -n 1` 캡처, 40열)
SERVER_HEADER = (
    "ts,market,entry_price,exit_price,buy_ratio,spread,turn,imbalance,volume_surge,"
    "fresh,score,entry_mode,signal_tag,filter_type,consecutive_buys,avg_krw_per_tick,"
    "flow_acceleration,overheat,fresh_age,cv,pstd,best_ask_krw,shadow_flags,would_cut,"
    "is_prebreak,added,exit_reason,mfe_pct,mae_pct,pnl_pct,result,hold_sec,entry_atr_pct,"
    "entry_pstd,entry_spread,entry_consec,mfe_sec,trail_dist,trail_stop_pct,peak_drop"
).split(",")


class TestTradeFeaturesHeaderMigrate(unittest.TestCase):
    def setUp(self):
        self.ns, self.fields = _load()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "trade_features.csv")
        self.ns["TRADE_LOG_PATH"] = self.path
        self.v7 = [c for c in self.fields if c not in SERVER_HEADER]

    def tearDown(self):
        self.tmp.cleanup()

    def _write_server_like(self):
        """7/19 이전 40열 행 1개 + 이후 44열 행 2개 (서버 상태 재현)."""
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(SERVER_HEADER)
            old = {k: "" for k in SERVER_HEADER}
            old.update({"ts": "2026-07-01 10:00:00", "market": "KRW-OLD",
                        "entry_price": "100", "exit_price": "101", "result": "win"})
            w.writerow([old[k] for k in SERVER_HEADER])
            for i, m in enumerate(("KRW-XLM", "KRW-META2")):
                new = {k: "" for k in self.fields}
                new.update({"ts": f"2026-10-17 19:2{i}:00", "market": m,
                            "entry_price": str(7000 + i), "entry_rsi": "55.5",
                            "entry_vr5": "1.2", "entry_buy_ratio": "0.6",
                            "entry_accel": "0.1"})
                w.writerow([new[k] for k in self.fields])

    def _read_rows(self):
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_server_header_is_prefix_of_fields_and_v7_missing(self):
        self.assertEqual(self.fields[:len(SERVER_HEADER)], SERVER_HEADER)
        self.assertEqual(self.v7, ["entry_rsi", "entry_vr5", "entry_buy_ratio", "entry_accel"])

    def test_reproduce_old_update_failure_without_migration(self):
        """수정 전 update_trade_result 읽기→쓰기 경로: key=None 으로 ValueError."""
        self._write_server_like()
        rows = self._read_rows()
        self.assertIn(None, rows[-1])  # 초과 열이 None 키로 들어감
        out = io.StringIO()
        with self.assertRaises(ValueError):
            csv.DictWriter(out, fieldnames=self.fields).writerows(rows)

    def test_migrate_rewrites_header_and_maps_extras_positionally(self):
        self._write_server_like()
        self.assertTrue(self.ns["_trade_log_migrate_header"]())
        with open(self.path, newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        self.assertEqual(header, self.fields)
        rows = self._read_rows()
        self.assertEqual(len(rows), 3)
        self.assertNotIn(None, rows[-1])
        # 옛 행: 값 보존, v7 필드는 공백
        self.assertEqual(rows[0]["market"], "KRW-OLD")
        self.assertEqual(rows[0]["result"], "win")
        self.assertEqual(rows[0]["entry_rsi"], "")
        # 새 행: 초과 값이 v7 필드로 정확히 매핑
        self.assertEqual(rows[2]["market"], "KRW-META2")
        self.assertEqual(rows[2]["entry_rsi"], "55.5")
        self.assertEqual(rows[2]["entry_accel"], "0.1")
        self.assertEqual(rows[2]["entry_price"], "7001")
        # 멱등: 두 번째 호출은 아무것도 안 함
        self.assertFalse(self.ns["_trade_log_migrate_header"]())

    def test_update_path_succeeds_after_migration(self):
        """마이그레이션 후 '마지막 미체결 행 찾아 결과 기록 → DictWriter' 가 통과."""
        self._write_server_like()
        self.ns["_trade_log_migrate_header"]()
        rows = self._read_rows()
        for r in rows:
            r.pop(None, None)
        hit = None
        for i in range(len(rows) - 1, -1, -1):
            if rows[i]["market"] == "KRW-META2" and not rows[i].get("exit_price"):
                rows[i]["exit_price"] = "7320"
                rows[i]["result"] = "lose"
                rows[i]["mfe_pct"] = "0.1200"
                hit = i
                break
        self.assertIsNotNone(hit)
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=self.fields)
        w.writeheader()
        w.writerows(rows)  # 수정 전엔 여기서 ValueError
        back = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual(back[hit]["result"], "lose")
        self.assertEqual(back[hit]["mfe_pct"], "0.1200")

    def test_log_trade_features_migrates_before_append(self):
        self._write_server_like()
        self.ns["log_trade_features"]({"ts": "2026-10-17 20:00:00", "market": "KRW-NEW",
                                       "entry_price": 1.0, "entry_rsi": 40.0})
        with open(self.path, newline="", encoding="utf-8") as f:
            raw = list(csv.reader(f))
        self.assertEqual(raw[0], self.fields)
        self.assertTrue(all(len(r) == len(self.fields) for r in raw[1:]))  # 전 행 열 수 일치
        self.assertEqual(raw[-1][self.fields.index("market")], "KRW-NEW")
        self.assertEqual(raw[-1][self.fields.index("entry_rsi")], "40.0")

    def test_no_rewrite_when_header_matches(self):
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.fields)
            w.writeheader()
            w.writerow({k: "" for k in self.fields})
        before = os.stat(self.path).st_mtime_ns
        self.assertFalse(self.ns["_trade_log_migrate_header"]())
        self.assertEqual(os.stat(self.path).st_mtime_ns, before)

    def test_unmappable_extras_are_dropped_not_guessed(self):
        """초과 값 개수가 신규 필드 수와 다르면 추측 매핑하지 않고 버림 (값 보존 우선)."""
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(SERVER_HEADER)
            w.writerow([""] * len(SERVER_HEADER) + ["x", "y"])  # 2개 초과 (v7 는 4개)
        self.assertTrue(self.ns["_trade_log_migrate_header"]())
        rows = self._read_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(all(rows[0][k] == "" for k in self.v7))

    def test_missing_file_is_noop(self):
        self.assertFalse(self.ns["_trade_log_migrate_header"]())
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
