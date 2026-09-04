# -*- coding: utf-8 -*-
"""
run_edge_discovery_c — EDGE_DISCOVERY_C_v1 end-to-end runner (advisor 3자 · 2026-09-04).

목적: "offline C 대기" 무한루프 종료 · 매 실행마다 반드시 C_RESULT ∈ {0개 / 후보N / NOT_RUN} 배출.

배경 (advisor 3자 · 2026-09-04):
  4리포트째 offline C 산출물 부재 · "0개인지 안 돌렸는지"조차 불명 = 관측 불가 = 최악.
  → 자동 실행 파이프라인 · JSON 계약 · frozen threshold · untouched forward.

프로토콜 (사전등록 · 사후 재튜닝 금지):
  A. TRAIN (앞 60%) · OOS (뒤 40%) · 시간순 · 경계 1회 고정
  B. Frozen 6-feature (decision-time · look-ahead 배제)
     - adx_60 · rsi_60m · macd_hist_5m_bps · atr_pct · ob_spread_pct · entry_vol_krw_m
  C. Threshold = TRAIN quantile (P50, P66) · 관찰 cutpoint 금지
  D. Direction: 승자 mean > 패자 mean → ">=" · 아니면 "<="
  E. Max 3 candidates per route (다중비교 할인) · 유사 threshold 중복 제거
  F. Concentration check: 상위 1 coin ≤ 40% · 상위 1 hour ≤ 40%
  G. UNTOUCHED FORWARD: frozen threshold 로 OOS matched delta
     - SURVIVE: OOS delta > 0 AND coin/hour concentration ≤ 40%
     - KILL: 그 외

C_RESULT 계약 (반드시 셋 중 하나):
  {"status": "OK", "routes": [...]}     # 정상 실행 · candidates 0~N
  {"status": "NOT_RUN", "reason": ...}  # 실행 실패 (파일 없음/데이터 부족 등)

사용:
  python research/run_edge_discovery_c.py [--input /tmp/clm_trades.json]
                                          [--output /tmp/c_result_latest.json]
                                          [--min-n 30]
                                          [--train-frac 0.6]
                                          [--max-candidates 3]

트리거 (advisor 3자):
  - 매 정규 리포트 전 write-session 이 실행
  - NOT_RUN 2회 연속 → 운영 실패 escalate
  - "0개" 도 답 (대기 종료 · null result = 유효)
"""
import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

# ── 프로토콜 버전 (advisor 3 · 2026-09-04 · C1_v1 lock) ────────────────
# C1 frozen 6 은 이 시점부터 v1 로 고정 · 재변경 시 v2 프로토콜 새 번호 필수
_PROTOCOL_VERSION = "C1_v1"


# ── frozen decision-time features (사전등록 · advisor 1 목록 · 2026-09-04 정정) ─
# advisor 3 지적: C1 은 frozen 6 만 · entry_vol_krw_m 은 C2 (capture) 로 이동
# 진입시각에 확정 가능한 지표만 · post-entry 자동 배제
_FROZEN_FEATURES = (
    "adx_60",
    "ob_slip_sell_100k",
    "rsi_60m",
    "macd_hist_5m_bps",
    "entry_spread_pct",
    "atr_pct",
)
# advisor 3 (2026-09-04): feature universe 봉인 검증 · 해시로 재변경 즉시 감지
# 재변경 시 hash 변화 → 실행 로그 즉시 mismatch · protocol v2 로 새 번호 강제
_FROZEN_FEATURES_HASH = hashlib.sha256(
    "|".join(sorted(_FROZEN_FEATURES)).encode("utf-8")
).hexdigest()[:12]

# look-ahead 자동 배제 (feature_screen.py 와 동일 규율)
_LOOKAHEAD_KEYS = frozenset({
    "mfe", "mfe_peak_sec", "mfe_peak", "mfe_30s", "mfe_60s", "mfe_120s",
    "dd_peak_30s", "dd_peak_60s", "dd_peak_120s",
    "mae", "mae_60s", "mae_120s", "mae_peak",
    "hold", "hold_sec", "exit_reason", "exit_origin", "realized_pnl", "pnl",
    "kst_hour",  # matched sim 반증 (누적 재버킷 착시)
})

# concentration 임계 (사전등록)
_TOP_COIN_MAX_SHARE = 0.40
_TOP_HOUR_MAX_SHARE = 0.40

# ── SURVIVE 판정 강화 (advisor 3 · 2026-09-04) ────────────────────────
# 이전: SURVIVE = OOS delta > 0 (+concentration) → +0.01%p 노이즈도 통과
# 정정: 사전등록 승격 기준 = 아래 4중 조건 전부 통과 시에만 SURVIVE
#   1. OOS Δavg ≥ +0.10%p (경제적 유의 · 노이즈 컷)
#   2. OOS ΔMDD ≤ +2.0%p (MDD 악화 없음)
#   3. Cost stress 후 Δavg > 0 (spread+slippage α 차감 후 부호 유지)
#   4. TRAIN 3-fold walk-forward 방향 일관성 (rolling window · 3/3 or 2/3)
# 미충족이면 WEAK (OOS Δ>0 이지만 승격 기준 미달) / KILL / INSUFFICIENT
_PROMOTE_DELTA_MIN = 0.10       # OOS Δavg %p 최소
_PROMOTE_MDD_MAX = 2.0          # OOS ΔMDD %p 최대 (악화 허용)
_COST_STRESS_ALPHA = 0.05       # 진입당 %p 비용 (spread + slippage · 보수적)
_WALK_FORWARD_FOLDS = 3
_WF_MIN_POSITIVE = 2            # 3-fold 중 최소 2개에서 delta > 0

# 판정 대상 route (paired shadow · A/A2 봉인 이후에도 flagship 관측)
_TARGET_ROUTES = (
    "CS40_VR3_TR180_bp30_240",   # flagship (CONTROL)
    "CLM_A_CLEAN_bp30",          # A (archived · reference)
    "CLM_A_x_A2_bp30",           # A×A2 (archived · reference)
)


def _quantile(sorted_vals, q):
    """빠른 quantile · numpy 미사용 (의존성 최소화)."""
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
    """export_trade_records() JSON 로드 · offline C 필수 필드 검증."""
    if not os.path.exists(json_path):
        return None, f"input file not found: {json_path}"
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return None, f"json load failed: {exc}"
    if not isinstance(data, list):
        return None, f"json is not a list (got {type(data).__name__})"
    if not data:
        return None, "empty records"
    return data, None


def _prep_route_rows(records, route):
    """route filter · look-ahead 배제 · 시간정렬 · feature dict flatten."""
    rows = []
    for rec in records:
        if rec.get("route") != route:
            continue
        exit_ts = rec.get("exit_ts")
        entry_ts = rec.get("entry_ts")
        if entry_ts is None or exit_ts is None:
            continue
        pnl = rec.get("pnl")
        if pnl is None:
            continue
        row = {
            "entry_ts": float(entry_ts),
            "exit_ts": float(exit_ts),
            "pnl": float(pnl),
            "market": rec.get("market", ""),
            "signal_id": rec.get("signal_id"),
            "route_epoch": rec.get("route_epoch"),
        }
        # ind_ prefix flatten (frozen features only)
        for feat in _FROZEN_FEATURES:
            v = rec.get(f"ind_{feat}")
            if v is not None:
                try:
                    row[feat] = float(v)
                except (TypeError, ValueError):
                    pass
        # kst_hour 유도 (look-ahead 아님 · 진입시각 확정)
        # 배제 규율에 있음 · matched sim 반증 · 판정 대상 X (정보만)
        try:
            dt = datetime.fromtimestamp(row["entry_ts"], tz=timezone.utc)
            # KST = UTC+9
            row["_hour_kst"] = (dt.hour + 9) % 24
        except Exception:
            row["_hour_kst"] = None
        rows.append(row)
    rows.sort(key=lambda r: r["entry_ts"])
    return rows


def _matched_delta(rows, feature, threshold, direction):
    """filter 통과 vs 전체 baseline net PnL 차이."""
    kept, dropped = [], []
    for r in rows:
        v = r.get(feature)
        if v is None:
            continue
        pnl = r["pnl"]
        passes = (v >= threshold) if direction == ">=" else (v <= threshold)
        if passes:
            kept.append(r)
        else:
            dropped.append(r)
    total_n = len(kept) + len(dropped)
    if total_n == 0 or not kept:
        return None
    baseline = _mean([r["pnl"] for r in kept + dropped])
    kept_mean = _mean([r["pnl"] for r in kept])
    return {
        "kept_n": len(kept),
        "dropped_n": len(dropped),
        "total_n": total_n,
        "baseline_mean": baseline,
        "kept_mean": kept_mean,
        "delta_vs_baseline": kept_mean - baseline,
        "keep_ratio": len(kept) / total_n,
        "kept_rows": kept,
    }


def _mdd_pct(pnls):
    """equal-weight 누적 equity 의 max drawdown %p 반환 (0 이상)."""
    if not pnls:
        return 0.0
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for p in pnls:
        eq += p
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > mdd:
            mdd = dd
    return mdd * 100.0  # 소수 → %p


def _walk_forward_direction_consistency(rows, feature, threshold, direction,
                                         folds=_WALK_FORWARD_FOLDS):
    """TRAIN 내부 3-fold walk-forward · direction 일관성 검증.
    각 fold 에서 matched_delta > 0 이면 pass · min_positive/folds 로 판정."""
    if len(rows) < folds * 10:
        return None
    fold_size = len(rows) // folds
    positive_count = 0
    fold_deltas = []
    for i in range(folds):
        start = i * fold_size
        end = (i + 1) * fold_size if i < folds - 1 else len(rows)
        fold_rows = rows[start:end]
        r = _matched_delta(fold_rows, feature, threshold, direction)
        if r and r["delta_vs_baseline"] > 0:
            positive_count += 1
        fold_deltas.append(r["delta_vs_baseline"] if r else None)
    return {
        "folds": folds,
        "positive_count": positive_count,
        "pass_rate": positive_count / folds,
        "fold_deltas": [round(d, 5) if d is not None else None for d in fold_deltas],
        "consistent": positive_count >= _WF_MIN_POSITIVE,
    }


def _concentration(kept_rows):
    """kept cohort 의 상위 coin/hour 집중도 · 사후최적화 방어."""
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


def _screen_route(rows, min_n=30, train_frac=0.6, max_candidates=3):
    """TRAIN 스크리닝 · frozen quantile threshold · concentration check."""
    n_total = len(rows)
    if n_total == 0:
        return {
            "n_total": 0,
            "not_run_reason": "no_target_route_data",
            "verdict": "NOT_RUN",
            "candidates_final": [],
        }
    if n_total < min_n:
        return {
            "n_total": n_total,
            "not_run_reason": f"insufficient_n (n={n_total} < min_n={min_n})",
            "verdict": "NOT_RUN",
            "candidates_final": [],
        }

    n_train = int(n_total * train_frac)
    if n_train < 20 or (n_total - n_train) < 10:
        return {
            "n_total": n_total,
            "not_run_reason": f"split_impossible (train={n_train}, oos={n_total-n_train})",
            "verdict": "NOT_RUN",
            "candidates_final": [],
        }

    train_rows = rows[:n_train]
    oos_rows = rows[n_train:]
    # advisor 3 (2026-09-04): split boundary timestamp lock · reproducibility
    # OOS 재열기 방지 · TRAIN 완료 후 threshold 는 절대 재선택 X
    train_last_ts = train_rows[-1]["entry_ts"] if train_rows else None
    oos_first_ts = oos_rows[0]["entry_ts"] if oos_rows else None
    boundary_iso = None
    if train_last_ts is not None:
        try:
            boundary_iso = datetime.fromtimestamp(
                train_last_ts, tz=timezone.utc
            ).isoformat()
        except Exception:
            pass

    # frozen feature 존재 여부 검증 (모두 결측이면 NOT_RUN)
    _feats_present = [
        f for f in _FROZEN_FEATURES
        if any(f in r for r in train_rows)
    ]
    if not _feats_present:
        return {
            "n_total": n_total,
            "n_train": n_train,
            "n_oos": n_total - n_train,
            "not_run_reason": "no_frozen_features_present (missing columns in train)",
            "verdict": "NOT_RUN",
            "candidates_final": [],
        }

    # frozen feature × quantile × direction 그리드 (사전등록)
    train_candidates = []
    for feat in _FROZEN_FEATURES:
        vals_train = sorted(r[feat] for r in train_rows if feat in r)
        if len(vals_train) < min_n // 2:
            continue
        # W/L direction 결정 (TRAIN 만 · OOS 절대 X)
        wins = [r[feat] for r in train_rows if r["pnl"] > 0 and feat in r]
        losses = [r[feat] for r in train_rows if r["pnl"] <= 0 and feat in r]
        if len(wins) < 5 or len(losses) < 5:
            continue
        direction = ">=" if _mean(wins) > _mean(losses) else "<="

        for q in (0.50, 0.66):
            threshold = _quantile(vals_train, q)
            if threshold is None:
                continue
            train_r = _matched_delta(train_rows, feat, threshold, direction)
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
                "train_baseline_pct": round(train_r["baseline_mean"] * 100, 4),
                "train_kept_pct": round(train_r["kept_mean"] * 100, 4),
                "train_delta_pct": round(train_r["delta_vs_baseline"] * 100, 4),
                "train_concentration": conc,
            })

    # TRAIN 통과 조건: delta > 0 + concentration OK
    train_pass = [
        c for c in train_candidates
        if c["train_delta_pct"] > 0.0
        and c["train_concentration"]["top_coin_share"] <= _TOP_COIN_MAX_SHARE
        and c["train_concentration"]["top_hour_share"] <= _TOP_HOUR_MAX_SHARE
    ]
    # delta 내림차순 · max_candidates 로 축소 · 유사 threshold 중복 제거 (같은 feature 중복 방지)
    train_pass.sort(key=lambda c: -c["train_delta_pct"])
    seen_features = set()
    final_train_pass = []
    for c in train_pass:
        if c["feature"] in seen_features:
            continue  # 같은 feature 다른 quantile 은 상위만 채택
        seen_features.add(c["feature"])
        final_train_pass.append(c)
        if len(final_train_pass) >= max_candidates:
            break

    # TRAIN 3-fold walk-forward · direction 일관성 (advisor 3 · 2026-09-04)
    # OOS 열기 전 · TRAIN 내부 다중비교 방어
    for c in final_train_pass:
        wf = _walk_forward_direction_consistency(
            train_rows, c["feature"], c["threshold"], c["direction"]
        )
        c["train_walk_forward"] = wf

    # UNTOUCHED FORWARD (OOS 를 처음이자 마지막으로 열음 · advisor 3 · 강화)
    for c in final_train_pass:
        oos_r = _matched_delta(oos_rows, c["feature"], c["threshold"], c["direction"])
        if not oos_r or oos_r["kept_n"] < 5:
            c["oos_status"] = "INSUFFICIENT"
            c["verdict"] = "INSUFFICIENT"
            continue
        oos_conc = _concentration(oos_r["kept_rows"])
        c["oos_kept_n"] = oos_r["kept_n"]
        c["oos_keep_ratio"] = round(oos_r["keep_ratio"], 3)
        c["oos_baseline_pct"] = round(oos_r["baseline_mean"] * 100, 4)
        c["oos_kept_pct"] = round(oos_r["kept_mean"] * 100, 4)
        c["oos_delta_pct"] = round(oos_r["delta_vs_baseline"] * 100, 4)
        c["oos_concentration"] = oos_conc

        # MDD 계산 (kept vs baseline · 전체 OOS)
        kept_pnls = [r["pnl"] for r in oos_r["kept_rows"]]
        all_pnls = [r["pnl"] for r in oos_rows]
        mdd_kept = _mdd_pct(kept_pnls)
        mdd_baseline = _mdd_pct(all_pnls)
        c["oos_mdd_kept_pct"] = round(mdd_kept, 3)
        c["oos_mdd_baseline_pct"] = round(mdd_baseline, 3)
        c["oos_delta_mdd_pct"] = round(mdd_kept - mdd_baseline, 3)

        # Cost stress: 진입당 α %p 비용 차감 후 부호 유지 확인
        cost_stressed_kept_mean = oos_r["kept_mean"] - (_COST_STRESS_ALPHA / 100.0)
        cost_stressed_delta = cost_stressed_kept_mean - oos_r["baseline_mean"]
        c["oos_cost_stressed_delta_pct"] = round(cost_stressed_delta * 100, 4)

        # 사전등록 4중 승격 조건 판정 (advisor 3 · SURVIVE 강화)
        wf = c.get("train_walk_forward")
        wf_consistent = bool(wf and wf.get("consistent"))
        cond_delta = c["oos_delta_pct"] >= _PROMOTE_DELTA_MIN
        cond_mdd = c["oos_delta_mdd_pct"] <= _PROMOTE_MDD_MAX
        cond_cost = cost_stressed_delta > 0
        cond_conc = (
            oos_conc["top_coin_share"] <= _TOP_COIN_MAX_SHARE
            and oos_conc["top_hour_share"] <= _TOP_HOUR_MAX_SHARE
        )
        c["promotion_criteria"] = {
            "delta_min": cond_delta,
            "mdd_max": cond_mdd,
            "cost_stress": cond_cost,
            "concentration": cond_conc,
            "wf_consistent": wf_consistent,
        }

        if cond_delta and cond_mdd and cond_cost and cond_conc and wf_consistent:
            c["verdict"] = "SURVIVE"
        elif c["oos_delta_pct"] > 0 and cond_conc:
            # 통과선 못 넘음 · 관측 가치는 있음 (승격 아님)
            c["verdict"] = "WEAK"
        else:
            c["verdict"] = "KILL"

    survivors = [c for c in final_train_pass if c.get("verdict") == "SURVIVE"]
    weak = [c for c in final_train_pass if c.get("verdict") == "WEAK"]

    return {
        "n_total": n_total,
        "n_train": n_train,
        "n_oos": n_total - n_train,
        "split_boundary_ts": train_last_ts,
        "split_boundary_iso": boundary_iso,
        "oos_first_ts": oos_first_ts,
        "train_grid_evaluated": len(train_candidates),
        "train_passed_filters": len(train_pass),
        "features_present": _feats_present,
        "candidates_final": final_train_pass,
        "survivors_n": len(survivors),
        "weak_n": len(weak),
        "verdict": (
            "CANDIDATES" if survivors else "ZERO"
        ),
        "verdict_detail": (
            f"SURVIVE={len(survivors)} · WEAK={len(weak)} · "
            f"total_evaluated={len(final_train_pass)}"
        ),
    }


def run(input_path, output_path, min_n=30, train_frac=0.6, max_candidates=3):
    ts = int(time.time())
    records, err = _load_records(input_path)
    if records is None:
        # advisor 3 (2026-09-04): NOT_RUN 사유 세분화 · 5가지 blocking artifact 강제
        # {input_file_not_found, json_load_error, no_records_in_file, ...}
        reason_key = "unknown"
        if err and "not found" in err:
            reason_key = "input_file_not_found"
        elif err and "load failed" in err:
            reason_key = "json_load_error"
        elif err and "empty" in err:
            reason_key = "no_records_in_file"
        elif err and "not a list" in err:
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
        "protocol": "EDGE_DISCOVERY_C_v1",
        "protocol_version": _PROTOCOL_VERSION,
        "frozen_features_hash": _FROZEN_FEATURES_HASH,
        "config": {
            "min_n": min_n,
            "train_frac": train_frac,
            "max_candidates": max_candidates,
            "frozen_features": list(_FROZEN_FEATURES),
            "frozen_features_hash": _FROZEN_FEATURES_HASH,
            "quantiles": [0.50, 0.66],
            "top_coin_max_share": _TOP_COIN_MAX_SHARE,
            "top_hour_max_share": _TOP_HOUR_MAX_SHARE,
            "promote_delta_min_pct": _PROMOTE_DELTA_MIN,
            "promote_mdd_max_pct": _PROMOTE_MDD_MAX,
            "cost_stress_alpha_pct": _COST_STRESS_ALPHA,
            "walk_forward_folds": _WALK_FORWARD_FOLDS,
            "wf_min_positive": _WF_MIN_POSITIVE,
        },
        "routes": [],
    }
    for route in _TARGET_ROUTES:
        rows = _prep_route_rows(records, route)
        route_result = _screen_route(
            rows,
            min_n=min_n,
            train_frac=train_frac,
            max_candidates=max_candidates,
        )
        route_result["route"] = route
        result["routes"].append(route_result)

    _write_result(output_path, result)
    return result


def _write_result(output_path, result):
    """atomic write."""
    tmp = output_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    os.rename(tmp, output_path)


def _print_summary(result):
    print(f"=== EDGE_DISCOVERY_C_v1 ({result.get('status')}) ===")
    if result.get("status") == "OK":
        # advisor 3 (2026-09-04): feature universe echo + hash · 재변경 즉시 감지
        print(f"  protocol_version: {result.get('protocol_version', '?')}")
        print(f"  frozen_features_hash: {result.get('frozen_features_hash', '?')}")
        print(f"  frozen_features: {list(_FROZEN_FEATURES)}")
    if result.get("status") != "OK":
        print(f"  reason_key: {result.get('reason_key', 'unknown')}")
        print(f"  reason_detail: {result.get('reason_detail', 'unknown')}")
        return
    for r in result.get("routes", []):
        print(f"\n[{r['route']}] n_total={r['n_total']} · {r.get('verdict_detail', r['verdict'])}")
        if r.get("candidates_final"):
            for c in r["candidates_final"]:
                v = c.get("verdict", "N/A")
                markers = {"SURVIVE": "✅", "WEAK": "🟡", "KILL": "❌",
                           "INSUFFICIENT": "⏳"}
                marker = markers.get(v, "?")
                oos_delta = c.get("oos_delta_pct", "N/A")
                cost_delta = c.get("oos_cost_stressed_delta_pct", "N/A")
                mdd_delta = c.get("oos_delta_mdd_pct", "N/A")
                wf = c.get("train_walk_forward") or {}
                wf_str = f"WF={wf.get('positive_count', '?')}/{wf.get('folds', '?')}"
                print(f"  {marker} {c['feature']} {c['direction']}{c['threshold']} "
                      f"(q={c['quantile']}) · TRAIN Δ={c['train_delta_pct']:+.4f}%p · "
                      f"OOS Δ={oos_delta}%p (cost {cost_delta}%p · MDD Δ{mdd_delta}%p · "
                      f"{wf_str}) · verdict={v}")
                pc = c.get("promotion_criteria")
                if pc:
                    ok = [k for k, v in pc.items() if v]
                    fail = [k for k, v in pc.items() if not v]
                    if fail:
                        print(f"      fail: {fail}")
        elif r.get("not_run_reason"):
            print(f"  NOT_RUN: {r['not_run_reason']}")
        else:
            print(f"  (0개 · TRAIN 필터 통과 없음)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/tmp/clm_trades.json",
                    help="export_trade_records() JSON path")
    ap.add_argument("--output", default="/tmp/c_result_latest.json",
                    help="C_RESULT JSON output path")
    ap.add_argument("--min-n", type=int, default=30,
                    help="minimum records per route to run")
    ap.add_argument("--train-frac", type=float, default=0.6,
                    help="TRAIN fraction (rest = OOS)")
    ap.add_argument("--max-candidates", type=int, default=3,
                    help="max candidates per route (다중비교 할인)")
    args = ap.parse_args()

    result = run(
        input_path=args.input,
        output_path=args.output,
        min_n=args.min_n,
        train_frac=args.train_frac,
        max_candidates=args.max_candidates,
    )
    _print_summary(result)
    print(f"\n→ C_RESULT written: {args.output}")
    # exit code: OK=0 · NOT_RUN=1
    sys.exit(0 if result.get("status") == "OK" else 1)


if __name__ == "__main__":
    main()
