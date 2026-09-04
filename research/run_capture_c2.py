# -*- coding: utf-8 -*-
"""
run_capture_c2 — C2_CAPTURE_ENTRY_v1 (advisor 3자 · 2026-09-04).

목적: capture 갭 정면 공격 · flagship realized/MFE 28% 개선 후보를 decision-time
      feature 로 사전 식별 (exit intervention 아님 · A 부활 절대 X).

배경 (advisor 3):
  flagship MFE +0.85% 대비 realized 28% = 회수 실패가 넉 달째 최대 관찰 손실원.
  Exit 손질 (A_CLEAN) 은 죽었음. 이제 exit 이 아니라 **entry 로** 고회수 체결을 사전 식별.

프로토콜 (사전등록 · 사후 재정의 금지 · outcome label 은 미리 못 박음):

  A. OUTCOME LABEL (미리 고정 · TRAIN 보기 전 · 사후 변경 X)
     high_capture = (pnl > 0) AND (mfe >= MIN_MFE_FLOOR) AND (pnl/mfe >= RATIO_THR)
     - MIN_MFE_FLOOR = 0.002 (MFE 0.2% 미만 배제 · ratio explosion 방지)
     - RATIO_THR = 0.5 (realized/MFE ≥ 50%)
     Rationale (advisor 3): ratio 단독은 MFE<0.2% 에서 분모 폭발 · pnl>0 필터 없으면
                             음수 pnl 로 음의 ratio 발생. 세 조건 결합이 안전.

  B. TRAIN 60% · OOS 40% · 시간순 · 경계 1회 고정 (C1 과 동일)

  C. FEATURE UNIVERSE (C1 frozen 6 + entry_vol_krw_m · advisor 3 승인):
     adx_60 · ob_slip_sell_100k · rsi_60m · macd_hist_5m_bps · entry_spread_pct ·
     atr_pct · entry_vol_krw_m
     (post-entry feature 절대 배제 · label 정의에만 mfe 사용 · feature 로는 X)

  D. Threshold = TRAIN quantile (P50, P66) · 관찰 cutpoint 금지

  E. Direction: TRAIN 에서 high_capture group mean > else mean → ">=" 아니면 "<="

  F. Max 3 candidates · 같은 feature 중복 제거

  G. Concentration: top coin ≤ 40% · top hour ≤ 40%

  H. UNTOUCHED FORWARD: frozen threshold 로 OOS 에서 high_capture 비율 개선 확인
     SURVIVE: OOS high_capture_rate 개선 > 0 AND concentration OK
     KILL: 그 외

C_RESULT_C2 계약:
  {"status": "OK", "protocol": "C2_CAPTURE_ENTRY_v1", "label_definition": {...}, "routes": [...]}
  {"status": "NOT_RUN", "reason_key": ..., "reason_detail": ...}

Look-ahead 금지 강조:
  - Feature 로 mfe/dd/mae/hold/exit_reason 사용 X (자동 필터)
  - Label 정의에만 post-entry (pnl, mfe) 사용 = look-ahead 위반 아님 (advisor 3 명시)
  - Feature 로 pnl/mfe 넣으면 위반 · frozen list 로 자동 방어

사용:
  python3 research/run_capture_c2.py [--input /tmp/clm_trades.json]
                                     [--output /tmp/c2_result_latest.json]
                                     [--min-n 30]
                                     [--train-frac 0.6]
                                     [--max-candidates 3]
"""
import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

# ── 프로토콜 버전 (advisor 3 · 2026-09-04) ────────────────────────────
# C2_v1 = a3daf6b 시점 · 재변경 시 v2 로 새 프로토콜 번호
_PROTOCOL_VERSION = "C2_v1"


# ── C2 outcome label preregister (사전등록 · advisor 3 · 2026-09-04) ──
# 사후 변경 금지 · TRAIN 결과 보고 조정 = protocol 무효
MIN_MFE_FLOOR = 0.002   # MFE 0.2% 미만 배제 (ratio explosion 방지)
RATIO_THR = 0.5         # realized/MFE ≥ 50%

# ── C2 feature universe: C1 frozen 6 + entry_vol_krw_m ────────────────
_FROZEN_FEATURES_C2 = (
    "adx_60",
    "ob_slip_sell_100k",
    "rsi_60m",
    "macd_hist_5m_bps",
    "entry_spread_pct",
    "atr_pct",
    "entry_vol_krw_m",   # advisor 3: C2 에서만 · C1 은 6 만
)
# advisor 3 (2026-09-04): feature universe 봉인 검증 · 해시로 재변경 감지
_FROZEN_FEATURES_C2_HASH = hashlib.sha256(
    "|".join(sorted(_FROZEN_FEATURES_C2)).encode("utf-8")
).hexdigest()[:12]

# concentration 임계 (사전등록 · C1 과 동일)
_TOP_COIN_MAX_SHARE = 0.40
_TOP_HOUR_MAX_SHARE = 0.40

_TARGET_ROUTES = (
    "CS40_VR3_TR180_bp30_240",
    "CLM_A_CLEAN_bp30",
    "CLM_A_x_A2_bp30",
)


def _is_high_capture(pnl, mfe):
    """사전등록 label 함수 · 절대 변경 금지 (사후 튜닝 = protocol 무효)."""
    if pnl is None or mfe is None:
        return None
    if pnl <= 0:
        return False
    if mfe < MIN_MFE_FLOOR:
        return False
    return (pnl / mfe) >= RATIO_THR


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def _load_records(json_path):
    if not os.path.exists(json_path):
        return None, "input_file_not_found"
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return None, f"json_load_error: {exc}"
    if not isinstance(data, list):
        return None, f"malformed_json (type={type(data).__name__})"
    if not data:
        return None, "no_records_in_file"
    return data, None


def _prep_route_rows(records, route):
    rows = []
    for rec in records:
        if rec.get("route") != route:
            continue
        exit_ts = rec.get("exit_ts")
        entry_ts = rec.get("entry_ts")
        pnl = rec.get("pnl")
        mfe = rec.get("mfe")
        if entry_ts is None or exit_ts is None or pnl is None or mfe is None:
            continue
        try:
            pnl_f = float(pnl)
            mfe_f = float(mfe)
        except (TypeError, ValueError):
            continue
        label = _is_high_capture(pnl_f, mfe_f)
        if label is None:
            continue
        row = {
            "entry_ts": float(entry_ts),
            "exit_ts": float(exit_ts),
            "pnl": pnl_f,
            "mfe": mfe_f,
            "high_capture": label,
            "market": rec.get("market", ""),
            "signal_id": rec.get("signal_id"),
            "route_epoch": rec.get("route_epoch"),
        }
        for feat in _FROZEN_FEATURES_C2:
            v = rec.get(f"ind_{feat}")
            if v is not None:
                try:
                    row[feat] = float(v)
                except (TypeError, ValueError):
                    pass
        try:
            dt = datetime.fromtimestamp(row["entry_ts"], tz=timezone.utc)
            row["_hour_kst"] = (dt.hour + 9) % 24
        except Exception:
            row["_hour_kst"] = None
        rows.append(row)
    rows.sort(key=lambda r: r["entry_ts"])
    return rows


def _high_capture_rate(rows):
    """cohort 의 high_capture 비율."""
    if not rows:
        return 0.0
    return sum(1 for r in rows if r["high_capture"]) / len(rows)


def _matched_capture_delta(rows, feature, threshold, direction):
    """filter 통과 cohort 의 high_capture rate vs baseline."""
    kept, dropped = [], []
    for r in rows:
        v = r.get(feature)
        if v is None:
            continue
        passes = (v >= threshold) if direction == ">=" else (v <= threshold)
        if passes:
            kept.append(r)
        else:
            dropped.append(r)
    total_n = len(kept) + len(dropped)
    if total_n == 0 or not kept:
        return None
    baseline_rate = _high_capture_rate(kept + dropped)
    kept_rate = _high_capture_rate(kept)
    return {
        "kept_n": len(kept),
        "dropped_n": len(dropped),
        "total_n": total_n,
        "baseline_rate": baseline_rate,
        "kept_rate": kept_rate,
        "delta_rate": kept_rate - baseline_rate,
        "keep_ratio": len(kept) / total_n,
        "kept_rows": kept,
    }


def _concentration(kept_rows):
    if not kept_rows:
        return {"top_coin": None, "top_coin_share": 0.0,
                "top_hour": None, "top_hour_share": 0.0}
    coin_cnt = defaultdict(int)
    hour_cnt = defaultdict(int)
    n = len(kept_rows)
    for r in kept_rows:
        market = r.get("market", "")
        coin = market.split("-")[-1] if "-" in market else market
        coin_cnt[coin] += 1
        h = r.get("_hour_kst")
        if h is not None:
            hour_cnt[h] += 1
    top_coin = max(coin_cnt.items(), key=lambda x: x[1]) if coin_cnt else (None, 0)
    top_hour = max(hour_cnt.items(), key=lambda x: x[1]) if hour_cnt else (None, 0)
    return {
        "top_coin": top_coin[0],
        "top_coin_share": round(top_coin[1] / n, 3),
        "top_hour": top_hour[0],
        "top_hour_share": round(top_hour[1] / max(sum(hour_cnt.values()), 1), 3),
    }


def _screen_route_c2(rows, min_n=30, train_frac=0.6, max_candidates=3):
    n_total = len(rows)
    if n_total == 0:
        return {"n_total": 0, "not_run_reason": "no_target_route_data",
                "verdict": "NOT_RUN", "candidates_final": []}
    if n_total < min_n:
        return {"n_total": n_total,
                "not_run_reason": f"insufficient_n (n={n_total} < {min_n})",
                "verdict": "NOT_RUN", "candidates_final": []}

    n_train = int(n_total * train_frac)
    if n_train < 20 or (n_total - n_train) < 10:
        return {"n_total": n_total,
                "not_run_reason": f"split_impossible (train={n_train}, oos={n_total-n_train})",
                "verdict": "NOT_RUN", "candidates_final": []}

    train_rows = rows[:n_train]
    oos_rows = rows[n_train:]
    train_last_ts = train_rows[-1]["entry_ts"]
    boundary_iso = None
    try:
        boundary_iso = datetime.fromtimestamp(
            train_last_ts, tz=timezone.utc
        ).isoformat()
    except Exception:
        pass

    _feats_present = [f for f in _FROZEN_FEATURES_C2
                      if any(f in r for r in train_rows)]
    if not _feats_present:
        return {"n_total": n_total, "n_train": n_train, "n_oos": n_total - n_train,
                "not_run_reason": "no_frozen_features_present",
                "verdict": "NOT_RUN", "candidates_final": []}

    train_baseline_rate = _high_capture_rate(train_rows)
    train_candidates = []
    for feat in _FROZEN_FEATURES_C2:
        vals_train = sorted(r[feat] for r in train_rows if feat in r)
        if len(vals_train) < min_n // 2:
            continue
        hc = [r[feat] for r in train_rows if r["high_capture"] and feat in r]
        lc = [r[feat] for r in train_rows if not r["high_capture"] and feat in r]
        if len(hc) < 5 or len(lc) < 5:
            continue
        direction = ">=" if _mean(hc) > _mean(lc) else "<="

        for q in (0.50, 0.66):
            threshold = _quantile(vals_train, q)
            if threshold is None:
                continue
            train_r = _matched_capture_delta(train_rows, feat, threshold, direction)
            if not train_r or train_r["kept_n"] < 10:
                continue
            conc = _concentration(train_r["kept_rows"])
            train_candidates.append({
                "feature": feat,
                "quantile": q,
                "threshold": round(threshold, 6),
                "direction": direction,
                "train_kept_n": train_r["kept_n"],
                "train_keep_ratio": round(train_r["keep_ratio"], 3),
                "train_baseline_rate": round(train_r["baseline_rate"], 4),
                "train_kept_rate": round(train_r["kept_rate"], 4),
                "train_delta_rate": round(train_r["delta_rate"], 4),
                "train_concentration": conc,
            })

    train_pass = [
        c for c in train_candidates
        if c["train_delta_rate"] > 0.0
        and c["train_concentration"]["top_coin_share"] <= _TOP_COIN_MAX_SHARE
        and c["train_concentration"]["top_hour_share"] <= _TOP_HOUR_MAX_SHARE
    ]
    train_pass.sort(key=lambda c: -c["train_delta_rate"])
    seen_features = set()
    final_train_pass = []
    for c in train_pass:
        if c["feature"] in seen_features:
            continue
        seen_features.add(c["feature"])
        final_train_pass.append(c)
        if len(final_train_pass) >= max_candidates:
            break

    for c in final_train_pass:
        oos_r = _matched_capture_delta(oos_rows, c["feature"], c["threshold"], c["direction"])
        if not oos_r or oos_r["kept_n"] < 5:
            c["oos_status"] = "INSUFFICIENT"
            c["verdict"] = "KILL"
            continue
        oos_conc = _concentration(oos_r["kept_rows"])
        c["oos_kept_n"] = oos_r["kept_n"]
        c["oos_keep_ratio"] = round(oos_r["keep_ratio"], 3)
        c["oos_baseline_rate"] = round(oos_r["baseline_rate"], 4)
        c["oos_kept_rate"] = round(oos_r["kept_rate"], 4)
        c["oos_delta_rate"] = round(oos_r["delta_rate"], 4)
        c["oos_concentration"] = oos_conc
        survives = (
            c["oos_delta_rate"] > 0.0
            and oos_conc["top_coin_share"] <= _TOP_COIN_MAX_SHARE
            and oos_conc["top_hour_share"] <= _TOP_HOUR_MAX_SHARE
        )
        c["verdict"] = "SURVIVE" if survives else "KILL"

    survivors = [c for c in final_train_pass if c.get("verdict") == "SURVIVE"]

    return {
        "n_total": n_total,
        "n_train": n_train,
        "n_oos": n_total - n_train,
        "train_baseline_capture_rate": round(train_baseline_rate, 4),
        "split_boundary_ts": train_last_ts,
        "split_boundary_iso": boundary_iso,
        "train_grid_evaluated": len(train_candidates),
        "train_passed_filters": len(train_pass),
        "features_present": _feats_present,
        "candidates_final": final_train_pass,
        "survivors_n": len(survivors),
        "verdict": "CANDIDATES" if survivors else "ZERO",
        "verdict_detail": f"{len(survivors)}개" if survivors else "0개",
    }


def run(input_path, output_path, min_n=30, train_frac=0.6, max_candidates=3):
    ts = int(time.time())
    records, err = _load_records(input_path)
    if records is None:
        reason_key = "unknown"
        if err and "not_found" in err:
            reason_key = "input_file_not_found"
        elif err and "load_error" in err:
            reason_key = "json_load_error"
        elif err and "no_records" in err:
            reason_key = "no_records_in_file"
        elif err and "malformed" in err:
            reason_key = "malformed_json"
        result = {
            "status": "NOT_RUN",
            "ts": ts,
            "input_file": input_path,
            "reason_key": reason_key,
            "reason_detail": err,
        }
        _write_result(output_path, result)
        return result

    result = {
        "status": "OK",
        "ts": ts,
        "input_file": input_path,
        "protocol": "C2_CAPTURE_ENTRY_v1",
        "protocol_version": _PROTOCOL_VERSION,
        "frozen_features_hash": _FROZEN_FEATURES_C2_HASH,
        # advisor 3 (2026-09-04): C2 실데이터 실행 blocker · A_CLEAN audit 미완료
        # 시 실데이터 실행 금지 (label = post-entry exit behavior 에 의존 · 오염 시 왜곡)
        "blocker_warning": (
            "C2 실데이터 실행은 A_CLEAN execution-contract audit 완료 후만 가능. "
            "smoke test 는 OK · A_CLEAN 오염 확정 근거 있으면 label 왜곡 가능."
        ),
        "label_definition": {
            "name": "high_capture",
            "rule": "(pnl > 0) AND (mfe >= MIN_MFE_FLOOR) AND (pnl/mfe >= RATIO_THR)",
            "MIN_MFE_FLOOR": MIN_MFE_FLOOR,
            "RATIO_THR": RATIO_THR,
            "note": "preregistered · post-entry OK for label · NOT for feature",
        },
        "config": {
            "min_n": min_n,
            "train_frac": train_frac,
            "max_candidates": max_candidates,
            "frozen_features_c2": list(_FROZEN_FEATURES_C2),
            "frozen_features_hash": _FROZEN_FEATURES_C2_HASH,
            "quantiles": [0.50, 0.66],
            "top_coin_max_share": _TOP_COIN_MAX_SHARE,
            "top_hour_max_share": _TOP_HOUR_MAX_SHARE,
        },
        "routes": [],
    }
    for route in _TARGET_ROUTES:
        rows = _prep_route_rows(records, route)
        route_result = _screen_route_c2(
            rows, min_n=min_n, train_frac=train_frac, max_candidates=max_candidates,
        )
        route_result["route"] = route
        result["routes"].append(route_result)

    _write_result(output_path, result)
    return result


def _write_result(output_path, result):
    tmp = output_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    os.rename(tmp, output_path)


def _print_summary(result):
    print(f"=== C2_CAPTURE_ENTRY_v1 ({result.get('status')}) ===")
    if result.get("status") == "OK":
        print(f"  protocol_version: {result.get('protocol_version', '?')}")
        print(f"  frozen_features_hash: {result.get('frozen_features_hash', '?')}")
        print(f"  frozen_features_c2: {list(_FROZEN_FEATURES_C2)}")
        # advisor 3 blocker warning · A_CLEAN audit 완료 여부 사용자 확인 필요
        if result.get("blocker_warning"):
            print(f"  ⚠ BLOCKER: {result['blocker_warning']}")
    if result.get("status") != "OK":
        print(f"  reason_key: {result.get('reason_key')}")
        print(f"  reason_detail: {result.get('reason_detail')}")
        return
    label = result.get("label_definition", {})
    print(f"Label: {label.get('rule')} (floor={label.get('MIN_MFE_FLOOR')}, thr={label.get('RATIO_THR')})")
    for r in result.get("routes", []):
        v = r.get("verdict", "N/A")
        base_rate = r.get("train_baseline_capture_rate", "N/A")
        print(f"\n[{r['route']}] n_total={r['n_total']} · baseline_capture_rate={base_rate} · verdict={r.get('verdict_detail', v)}")
        if r.get("candidates_final"):
            for c in r["candidates_final"]:
                cv = c.get("verdict", "N/A")
                marker = "✅" if cv == "SURVIVE" else "❌"
                oos_delta = c.get("oos_delta_rate", "N/A")
                print(f"  {marker} {c['feature']} {c['direction']}{c['threshold']} "
                      f"(q={c['quantile']}) · TRAIN Δrate={c['train_delta_rate']:+.4f} · "
                      f"OOS Δrate={oos_delta} · verdict={cv}")
        elif r.get("not_run_reason"):
            print(f"  NOT_RUN: {r['not_run_reason']}")
        else:
            print(f"  (0개 · TRAIN 필터 통과 없음)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/tmp/clm_trades.json")
    ap.add_argument("--output", default="/tmp/c2_result_latest.json")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--train-frac", type=float, default=0.6)
    ap.add_argument("--max-candidates", type=int, default=3)
    args = ap.parse_args()

    result = run(
        input_path=args.input, output_path=args.output,
        min_n=args.min_n, train_frac=args.train_frac,
        max_candidates=args.max_candidates,
    )
    _print_summary(result)
    print(f"\n→ C2_RESULT written: {args.output}")
    sys.exit(0 if result.get("status") == "OK" else 1)


if __name__ == "__main__":
    main()
