#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""shadow_salvage — H27 사전 등록: 봇이 쌓아 둔 초단타 섀도우 거래를 '지표 1개 조건'으로 되살릴 수 있는가.
(봇 파일은 복사본을 읽기만 한다. 거래소에는 공개 시세(KRW-BTC 5분봉)만 묻는다 — 키·주문 없음. 표준 라이브러리.
 PC 에서 실행 — 거래 서버에서 돌리지 않는다. 번호는 H27. 2026-11-10 에 잠깐 H28 로 바꿨다가(커밋 ebe52d1 의 'H28' 이 이 검사)
 고문이 번호 변경 요청을 거둬 되돌렸다. 일·주 단위 conditional_salvage 는 H26 그대로다.)

사용자 지적(2026-11-10): 시나리오를 통째로 돌려 "전체 평균이 음수 → 폐기" 로 끝내지 말고, 그 시나리오가 만든 거래 전체를 모수로 놓고
    그 안에서 승패를 가르는 지표 1개를 찾아 붙였을 때 음에서 양으로 바뀌는지를 봐야 한다. 번갈아 봐도 없으면 그때 버린다.
    H26(conditional_salvage) 은 이것을 일·주 단위 시나리오에 했다(FAIL). 이 스크립트는 같은 절차를 초·분 단위 시나리오에 한다.

모수: bot.py 가 매 신호마다 가상 진입·청산해 shadow_stats.json 의 route 별 trade_records 에 남긴 거래.
    한 건 = {pnl(비용 전), hold, exit_ts, signal_id("종목:신호시각"), inds{진입 판단 시점 지표 수십 개 + 호가 비용}}.
    route 하나가 '기본 시나리오' 하나다(예: 급등 추격 CS40, 클라이맥스 CLM, 모멘텀 GT …).

기존 도구와의 차이 (같은 파일을 읽는 도구가 이미 둘 있다):
    research/shadow_net_cost.py   route 를 통째로 비용 차감 → 전부 음수. 조건은 스프레드 3분위 하나만 봄.
    bots/filter_tournament.py     사람이 고른 필터 17개를 '비용 전' 손익으로, 같은 자료 안에서만 평가(검증 구간 없음).
    이 스크립트는 ① 비용을 뺀 손익으로 ② 기록된 지표 전부를 ③ 앞 구간에서만 고르고 뒤 구간에서 판정한다.

사전 등록 (이 커밋이 결과보다 먼저다. 자료를 본 뒤 아래 숫자를 바꾸지 않는다):
  자료      shadow_stats*.json 스냅샷 여러 개를 합친다(봇은 route 당 최근 300건/50건만 보관 → 날짜가 다른 스냅샷을 합치면 늘어난다).
            같은 거래는 (route, signal_id, 청산시각) 으로 한 번만 센다.
  제외      pnl·exit_ts·hold 가 없는 건, 진입 시각이 2025 년 이전으로 찍힌 건(기록 오류), 청산 사유 '가격없음'(시세 조회
            실패로 닫힌 건), 비용 지표가 없는 건. 건수를 보고한다.
  비용      순손익 = pnl − 수수료 왕복 0.10% − 매수 불리폭(ob_slip_buy_100k) − 매도 불리폭(ob_exit_slip_sell_100k, 없으면
            진입 시점 ob_slip_sell_100k). shadow_net_cost.py 와 같은 정의. 10만 원 주문 기준 호가 실측.
  시나리오  위 제외 후 거래 ≥ 120건 이고 진입일 ≥ 15일인 route 만 판정 대상(그 밖은 표본 부족으로 목록에만 표시).
  구간      route 마다 진입 시각 순으로, 날짜 경계에서 앞 60% = TRAIN, 뒤 40% = HOLDOUT (하루가 양쪽에 걸치지 않게).
  지표      inds 의 숫자 지표 전부. 단 진입 뒤에 생기는 값은 제외: mfe_*, dd_peak_*, mae_*, ob_exit_*, at_*.
            파생 1개 추가: ob_imb5 = 매수 5호가 잔량 / (매수+매도 5호가 잔량). TRAIN 거래의 80% 이상에 값이 있는 지표만.
  시장 상태 봇 기록에는 코인 자체 지표만 있고 시장 상태가 없다. 2개를 붙인다: btc_ret_15m · btc_ret_60m = 진입 전에 끝난
            마지막 KRW-BTC 5분봉 종가 / 그 15분·60분 전 종가 − 1 (진입 뒤 값은 쓰지 않는다). 넣는 이유: 고문의 15분봉 검증
            (2026-11-10)에서 급락 매수의 갈림선이 코인 지표가 아니라 'BTC 도 같이 빠졌는가' 였다. 결과 실행 전에 추가.
            시세를 못 받으면 머리말에 UNAVAILABLE 로 표시하고 이 2개 없이 진행한다(그 결과는 '시장 상태 미검사').
  조건      지표마다 2개: TRAIN 3분위의 아래 1/3('low')·위 1/3('high'). 값이 두 가지뿐인 지표는 각 값. 지표 1개짜리만(쌍 없음).
  통계      표본 단위는 거래지만 같은 날 거래는 독립이 아니므로 '진입일(KST)' 을 묶음으로 한 군집 표준오차로 t 를 낸다(자유도 = 일수−1).
  선택      TRAIN 에서 거래 ≥ 30건·일수 ≥ 8·평균 순손익 > 0·t ≥ 2.0 인 조건 중 t 가 가장 큰 1개를 route 마다 잠근다(지표 1개).
            없으면 그 route 는 '되살릴 조건 없음'.
  판정      잠긴 조건만 HOLDOUT 에서: 거래 ≥ 15건·일수 ≥ 6, 평균 순손익의 단측 하한(유의수준 0.05/잠긴 조건 수, t 분포) > 0 → PASS.
            PASS 는 '후보' 다. 이 파일의 10월 자료는 봇 리포트·다른 분석에서 이미 본 구간이므로, 최종 판정은 --frozen 으로
            동결 시각 이후 새로 쌓인 섀도우 거래에서 한 번 더 한다. 전부 FAIL 이면 초단타 route 의 조건부 구제는 닫는다.
  보고만    (판정에 쓰지 않는다. 고문·제3세션이 결과를 읽을 때 확인하려던 항목, 결과 실행 전에 추가)
            · route 별 제외 비율: 비용 지표가 없어 빠진 거래가 많으면 남은 거래만 좋아 보일 수 있다 → kept % 와 사유별 건수.
            · 날짜 가중 평균(daymean): 하루를 한 표로 센 평균. 거래 평균과 부호가 다르면 며칠에 몰린 결과다.
            · 조건에 쓴 지표 이름 전부와 '진입 뒤 값' 으로 뺀 이름 전부(INDICATORS 줄) — 미래 정보가 섞였는지 눈으로 감사.
              bot.py 대조(2026-11-10): 진입 뒤에 inds 에 더해지는 키는 mfe_peak_sec·mfe_30s/60s·dd_peak_30s/60s·mae_60s·
              at_ob_slip/at_trail_pct/at_tier/at_max_hold·ob_exit_* 뿐이고 모두 위 제외 접두어에 걸린다.
            · route_epoch 태그 수: 같은 route 안에 실험 계약 버전이 몇 개 섞였는지. 단 대부분 route 는 이 값이 배포 SHA 라
              '배포 횟수' 일 뿐이다. 버전 문자열(예: CONTROL_v1)을 쓰는 route 에서만 규칙 변경을 뜻한다.
  보조 진단 route 마다 TRAIN 상위 5개 조건이 HOLDOUT 에서 (a) 기본보다 나았는지 (b) 비용 후 양수였는지 센다. 아무 신호가
            없으면 (a) 는 절반쯤 나온다 — '조건을 붙여 살리는' 방법이 이 자료에서 얼마나 재현되는지 보는 수치(판정에는 안 씀).
  한계      가상 진입은 스캔 시작 시각의 현재가에 100% 체결 가정. 실거래는 더 늦고 더 비싸게 들어간다(shadow_net_cost.py 머리말:
            섀도우 +0.02%/건 대 실거래 −0.47%/건). 그래서 PASS 도 상한이다. 반대로 비용은 '중간가 기준 불리폭' 을 '현재가 진입' 위에 더하므로
            최대 반 스프레드만큼 이중 계산일 수 있다(보수적). route 정의가 중간에 바뀐 경우 섞여 있을 수 있다.
            같은 신호를 청산 방식만 달리한 route 들은 서로 독립이 아니다 — 잠긴 조건 수로 나누는 보정은 그래서 보수적이다.

결과 2026-11-11 (사용자 PC 실행. 스크립트는 ebe52d1 — 출력 머리의 'H28' 은 이 검사의 옛 번호이고 판정 기준은 현재 버전과 같다):
    H27 = 되살릴 조건 없음. 잠금 0개(판정 문구 NOTHING TO TEST). 초단타 '과열감지' route 의 조건부 구제는 닫는다.
  자료   스냅샷 3개(10/20 두 벌은 같은 내용, 11/11 복사본) → 쓸 수 있는 거래 1,434건, route 8개. BTC 5분봉 27,849개, 거래의 99% 에 붙음.
         제외 1,568건은 전부 '시각·손익 없음' 이고 비용 지표가 없어 빠진 거래는 0건이다(누락 편향 해당 없음).
         (추정: exit_ts 필드가 생긴 2026-08-04 이전의 옛 기록. 쓸 수 있는 가장 이른 진입일이 08-06 이다. route 별 건수는 이 버전 출력에 없다.)
         고문 감사(2026-11-11)로 확인: 제외분은 시각 기록이 없는 옛 형식 route(RET·LTRP·SVE1·CLM 변형)이고, 이들의 누적 평균은
         비용 전에도 모두 음수(RET −0.10% 2,550건, LTRP −0.28%, SVE1 −0.15%) — 빠진 쪽에 숨은 승자는 없다.
  route (거래/일수)                    비용 전    비용 후    t      호가 비용 중앙값
  CS40_VR3_TR180_bp30_240    (458/70)  −0.007%   −0.443%   −7.1   0.322%    판정 대상
  CLM_A_CLEAN_bp30           (400/92)  +0.076%   −0.364%   −5.6   0.317%    판정 대상
  CLM_CS40_TR180_15_240      (100/24)  −0.203%   −0.654%   −6.5   0.330%    표본 부족
  CS40_TR180_bp30_240        (100/24)  −0.213%   −0.662%   −6.9   0.328%    표본 부족
  CS40_VR3_TR180_bp100_240   (100/27)  −0.179%   −0.620%   −5.2   0.333%    표본 부족
  B45_CS40_VR3_TR180_bp30    (100/40)  −0.007%   −0.413%   −4.1   0.309%    표본 부족
  B45_CS40_VR35_TR180_bp30   ( 99/44)  −0.051%   −0.458%   −4.6   0.310%    표본 부족
  CLM_A_x_A2_bp30            ( 77/47)  +0.009%   −0.430%   −2.9   0.312%    표본 부족
  TRAIN 조건 탐색 (route 당 112개)
  CS40_VR3     기본 −0.373% (비용 전 +0.045%). 비용 후 양수 0개. 최상: ob_ask1_krw low → 비용 전 +0.362%, 비용 후 −0.035% (t −0.2)
  CLM_A_CLEAN  기본 −0.278% (비용 전 +0.157%). 비용 후 양수 9개, 선택 기준(t ≥ 2) 통과 0개. 최상: ob_slip_buy_10000k high →
               비용 전 +0.584%, 비용 후 +0.108% (t +0.5). 상위 5개가 전부 '호가가 얇다' 는 같은 조건(매수 불리폭 큼·잔량 적음).
  보조 진단: TRAIN 상위 10개 조건 중 HOLDOUT 에서 기본보다 나은 것 7개, 비용 후 양수 0개.
  읽기
  - 신호에 방향성이 없다. 8개 route 모두 비용 전 평균이 −0.21 ~ +0.08% 로 0 근처다. '조금 모자란' 것이 아니라, 0 에서 왕복 비용
    약 0.42%(수수료 0.10 + 호가 0.32)를 그대로 잃는 구조다.
  - 조건을 붙이면 비용 전 수익은 오른다(+0.2 ~ 0.6%). 그런데 그 조건이 '호가가 얇은 종목' 이라 비용도 같이 오른다. 수익을 키우는
    지표와 비용을 키우는 지표가 같아서 상쇄된다. HOLDOUT 에서 7/10 이 기본보다 나았던 것은 이 기울기가 실재한다는 뜻이지 비용을 넘는다는 뜻이 아니다.
  - BTC 직전 15·60분 수익률은 두 route 어느 쪽에서도 상위 5개에 들지 못했다.
  - 뒤 구간이 더 나쁘다(전체와 TRAIN 에서 역산한 근사: HOLDOUT 비용 후 CS40_VR3 약 −0.55%, CLM_A_CLEAN 약 −0.49%. 비용 전도 약 −0.09%·−0.05%).
  정정 (위 '한계' 의 내 문구): 비용 이중 계산을 '최대 반 스프레드' 라고 적었으나 진입·청산 각각 반 스프레드, 합쳐 최대 한 스프레드다.
    가장 낙관적으로 호가 비용을 0 으로 놓고 수수료만 빼면 CLM_A_CLEAN 최상 조건은 TRAIN 약 +0.48% (t 약 2.3) 로 선택 기준에 걸친다.
    그래도 결론은 같다: (1) 112개 중 최상이 t 2.3 인 것은 잡음에서도 나오는 수준이고 그 조건은 뒤 구간에서 판정된 적이 없다.
    (2) 실거래 기록은 비용이 더 크다고 말한다 — shadow_net_cost.py 머리말의 섀도우 +0.02%/건 대 실거래 −0.47%/건, 차이 0.49% 는
    이 검사가 쓴 0.42% 보다 크다. 비용을 과하게 잡았다는 근거는 없다.
  한계: 표본 부족 6개 route 는 판정하지 않았다(다만 전부 비용 전 +0.01% 이하, 비용 후 t −2.9 ~ −6.9). 지표 1개짜리 조건만 봤다.
        결론은 봇의 과열감지 route 에 한정된다 — '급등 뒤 지속' 일반을 기각한 것이 아니다.
  후속: 잠긴 조건이 없어 --frozen 재판정은 없다. 고문이 제안한 '섀도우 거래를 지우지 않고 쌓는 기능' 은 만들지 않는다 —
        다시 판정할 조건이 없고, 판정 대상 두 route 는 하루 4 ~ 7건이라 300건 보관으로 46 ~ 73일치가 남는다.

실행 (PC):
  py -3 -X utf8 shadow_salvage.py "shadow_stats*.json" "ws_tight\_bot\shadow_stats*.json" --out h27_result
      (패턴에 맞는 파일이 없으면 건너뛴다. 같은 파일이 두 군데 있어도 거래는 한 번만 센다. --no-market 은 시세 조회 생략)
  py -3 -X utf8 shadow_salvage.py <새 스냅샷들> --frozen h27_result\h27_result.json --out h27_forward      (PASS 가 있을 때만, 나중에)
"""
import argparse
import datetime as dt
import glob
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FEE_RT = 0.0010
SIZE_K = 100
TRAIN_FRAC = 0.60
MIN_ROUTE_TRADES, MIN_ROUTE_DAYS = 120, 15
MIN_IND_COVERAGE = 0.80
COND_MIN_TRADES, COND_MIN_DAYS, LOCK_MIN_T = 30, 8, 2.0
HOLD_MIN_TRADES, HOLD_MIN_DAYS = 15, 6
ALPHA = 0.05
TOP_SHOW = 5
LEAK_PREFIXES = ("mfe_", "dd_peak_", "mae_", "ob_exit_", "at_")
SKIP_EXIT = ("가격없음",)
KST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc
UPBIT = "https://api.upbit.com/v1"
BTC_BAR_SEC = 300
MIN_ENTRY_TS = 1735689600.0          # 2025-01-01 UTC. 그 전 시각은 기록 오류로 본다
MARKET_IND = (("btc_ret_15m", 900), ("btc_ret_60m", 3600))


# ---------------------------------------------------------------------------------------------------------------------
def _num(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


def is_decision_key(key):
    """진입 판단 시점에 알 수 있는 지표인가(진입 뒤에 채워지는 값이면 False)."""
    return not key.startswith(LEAK_PREFIXES)


def trade_cost(inds):
    """왕복 호가 비용(비율). 매수·매도 불리폭 둘 다 있어야 한다. 없으면 None."""
    buy = _num(inds.get("ob_slip_buy_%dk" % SIZE_K))
    sell = _num(inds.get("ob_exit_slip_sell_%dk" % SIZE_K))
    if sell is None:
        sell = _num(inds.get("ob_slip_sell_%dk" % SIZE_K))
    if buy is None or sell is None:
        return None
    return (buy + sell) / 100.0


def to_trade(key, rec):
    """trade_records 한 건 → 분석용 dict. 못 쓰는 건은 (None, 사유)."""
    pnl, exit_ts, hold = _num(rec.get("pnl")), _num(rec.get("exit_ts")), _num(rec.get("hold"))
    if pnl is None or exit_ts is None or hold is None:
        return None, "no_time_or_pnl"
    if exit_ts - hold < MIN_ENTRY_TS:
        return None, "bad_time"
    if rec.get("exit_reason") in SKIP_EXIT:
        return None, "price_unavailable"
    inds = rec.get("inds") or {}
    cost = trade_cost(inds)
    if cost is None:
        return None, "no_cost"
    ind = {k: _num(v) for k, v in inds.items() if is_decision_key(k) and _num(v) is not None}
    bid, ask = ind.get("ob_bid_cum5_krw"), ind.get("ob_ask_cum5_krw")
    if bid is not None and ask is not None and bid + ask > 0:
        ind["ob_imb5"] = bid / (bid + ask)
    entry_ts = exit_ts - hold
    sid = rec.get("signal_id")
    return {"key": key, "entry_ts": entry_ts, "exit_ts": exit_ts,
            "day": dt.datetime.fromtimestamp(entry_ts, KST).date().isoformat(),
            "market": sid.rsplit(":", 1)[0] if isinstance(sid, str) and ":" in sid else None,
            "gross": pnl, "cost": cost, "net": pnl - FEE_RT - cost, "ind": ind, "epoch": rec.get("route_epoch")}, None


def load_snapshots(paths, log=None, excluded=None):
    """스냅샷 여러 개 → ({route키: [거래]}, {route키: {사유: 건수}}). 같은 거래는 한 번만.
    excluded 에 집합을 주면 '진입 뒤 값' 이라 뺀 지표 이름을 거기에 모은다(감사용 보고)."""
    by_key, dropped, seen = {}, {}, set()
    for p in paths:
        with open(p, encoding="utf-8") as f:
            stats = json.load(f)
        n_new = 0
        for key, s in (stats.items() if isinstance(stats, dict) else []):
            if not isinstance(s, dict):
                continue
            for rec in s.get("trade_records") or []:
                if not isinstance(rec, dict):
                    continue
                ident = (key, rec.get("signal_id"), round(_num(rec.get("exit_ts")) or 0.0, 1), rec.get("pnl"))
                if ident in seen:
                    continue
                seen.add(ident)
                if excluded is not None:
                    excluded.update(k for k in (rec.get("inds") or {}) if not is_decision_key(k))
                tr, why = to_trade(key, rec)
                if tr is None:
                    dropped.setdefault(key, {})
                    dropped[key][why] = dropped[key].get(why, 0) + 1
                    continue
                by_key.setdefault(key, []).append(tr)
                n_new += 1
        if log:
            log("  %s: +%d usable trades" % (Path(p).name, n_new))
    for rows in by_key.values():
        rows.sort(key=lambda t: t["entry_ts"])
    return by_key, dropped

# ---------------------------------------------------------------------------------------------------------------------
def _get(url, retries=6):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=20) as r:
                time.sleep(0.12)
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < retries - 1:
                time.sleep(1 + i)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < retries - 1:
                time.sleep(1 + i)
                continue
            raise


def fetch_btc_bars(t_from, t_to):
    """KRW-BTC 5분봉 종가 {봉 시작 시각(초): 종가}, 봉 시작이 [t_from, t_to) 인 것."""
    out, to = {}, int(t_to)
    while to > t_from:
        iso = dt.datetime.fromtimestamp(to, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = _get(UPBIT + "/candles/minutes/5?" + urllib.parse.urlencode({"market": "KRW-BTC", "to": iso, "count": 200}))
        if not rows:
            break
        starts = [int(dt.datetime.fromisoformat(r["candle_date_time_utc"]).replace(tzinfo=UTC).timestamp()) for r in rows]
        for t, r in zip(starts, rows):
            if t_from <= t < t_to:
                out[t] = float(r["trade_price"])
        if min(starts) >= to:
            break
        to = min(starts)
    return out


def load_btc_bars(t_from, t_to, cache_path):
    """캐시가 구간을 덮으면 그대로, 아니면 받아서 합쳐 저장."""
    cache_path, bars = Path(cache_path), {}
    if cache_path.exists():
        bars = {int(k): float(v) for k, v in json.loads(cache_path.read_text(encoding="utf-8")).items()}
    if not bars or min(bars) > t_from + 2 * BTC_BAR_SEC or max(bars) < t_to - 3 * BTC_BAR_SEC:
        bars.update(fetch_btc_bars(t_from, t_to))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({str(k): bars[k] for k in sorted(bars)}), encoding="utf-8")
    return bars


def btc_state(bars, entry_ts):
    """진입 전에 끝난 마지막 5분봉 기준 BTC 수익률. 봉이 비어 있으면 그 항목은 뺀다."""
    last = (int(entry_ts) - BTC_BAR_SEC) // BTC_BAR_SEC * BTC_BAR_SEC
    now, out = bars.get(last), {}
    if now:
        for name, back in MARKET_IND:
            ref = bars.get(last - back)
            if ref:
                out[name] = now / ref - 1.0
    return out


def attach_market(by_key, bars):
    """모든 거래에 시장 상태 지표를 붙이고, 60분 지표가 붙은 거래 비율을 돌려준다."""
    total = got = 0
    for rows in by_key.values():
        for t in rows:
            st = btc_state(bars, t["entry_ts"])
            t["ind"].update(st)
            total += 1
            got += 1 if MARKET_IND[-1][0] in st else 0
    return got / float(total) if total else 0.0


def split(trades):
    """진입 시각 순, 날짜 경계에서 앞 TRAIN_FRAC = TRAIN. 반환 (train, hold)."""
    days = sorted({t["day"] for t in trades})
    count = {d: 0 for d in days}
    for t in trades:
        count[t["day"]] += 1
    need, acc, cut = TRAIN_FRAC * len(trades), 0, None
    for d in days:
        acc += count[d]
        if acc >= need:
            cut = d
            break
    return [t for t in trades if t["day"] <= cut], [t for t in trades if t["day"] > cut]


# ---------------------------------------------------------------------------------------------------------------------
def _betacf(a, b, x):
    tiny, c, d = 1e-300, 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for num in (m * (b - m) * x / ((a + m2 - 1) * (a + m2)), -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1))):
            d = 1.0 + num * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + num / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-12:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t, df):
    p = 0.5 * _betai(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - p if t >= 0 else p


def t_quantile(p, df):
    """Student t 분포의 p 분위수 (p ≥ 0.5)."""
    lo, hi = 0.0, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def cluster_stats(trades):
    """거래 평균 순손익과 진입일 군집 표준오차 → dict(n, days, net, gross, win, se, t, df, daymean).
    daymean = 하루를 한 표로 센 평균(보고용)."""
    n = len(trades)
    out = {"n": n, "days": len({t["day"] for t in trades}), "net": None, "gross": None, "win": None, "se": None, "t": None,
           "df": None, "daymean": None}
    if n == 0:
        return out
    mean = sum(t["net"] for t in trades) / n
    out.update(net=mean, gross=sum(t["gross"] for t in trades) / n, win=sum(1 for t in trades if t["net"] > 0) / float(n))
    g = out["days"]
    sums, counts = {}, {}
    for t in trades:
        sums[t["day"]] = sums.get(t["day"], 0.0) + (t["net"] - mean)
        counts[t["day"]] = counts.get(t["day"], 0) + 1
    out["daymean"] = mean + sum(sums[d] / counts[d] for d in sums) / g
    if g < 2:
        return out
    var = g / (g - 1.0) * sum(s * s for s in sums.values()) / (n * n)
    out["df"] = g - 1
    if var > 0:
        out["se"] = math.sqrt(var)
        out["t"] = mean / out["se"]
    return out


def make_conditions(train):
    """TRAIN 자료로 조건 목록 → [(이름, 지표, 연산, 문턱)]. 문턱은 이후 그대로 쓴다."""
    names = sorted({k for t in train for k in t["ind"]})
    conds = []
    for name in names:
        vals = sorted(t["ind"][name] for t in train if name in t["ind"])
        if len(vals) < MIN_IND_COVERAGE * len(train):
            continue
        distinct = sorted(set(vals))
        if len(distinct) < 2:
            continue
        lo, hi = vals[len(vals) // 3], vals[(2 * len(vals)) // 3]
        if lo == hi:
            if len(distinct) != 2:
                continue
            lo, hi = distinct
        conds.append(("%s low" % name, name, "<=", lo))
        conds.append(("%s high" % name, name, ">=", hi))
    return conds


def passes(trade, cond):
    v = trade["ind"].get(cond[1])
    if v is None:
        return False
    return v <= cond[3] if cond[2] == "<=" else v >= cond[3]


def scan(train):
    """TRAIN 조건 탐색 → 모든 조건의 통계(t 내림차순)."""
    out = []
    for cond in make_conditions(train):
        st = cluster_stats([t for t in train if passes(t, cond)])
        st["cond"] = list(cond)
        st["qualified"] = bool(st["n"] >= COND_MIN_TRADES and st["days"] >= COND_MIN_DAYS and st["net"] is not None
                               and st["net"] > 0 and st["t"] is not None and st["t"] >= LOCK_MIN_T)
        out.append(st)
    out.sort(key=lambda s: -(s["t"] if s["t"] is not None else -99.0))
    return out


def judge(trades, cond, alpha):
    """잠긴 조건의 판정 통계. low = 평균 순손익의 단측 하한."""
    st = cluster_stats([t for t in trades if passes(t, cond)])
    st["low"] = st["net"] - t_quantile(1.0 - alpha, st["df"]) * st["se"] if st["se"] else None
    st["pass"] = bool(st["n"] >= HOLD_MIN_TRADES and st["days"] >= HOLD_MIN_DAYS and st["low"] is not None and st["low"] > 0)
    return st


# ---------------------------------------------------------------------------------------------------------------------
def _p(v, spec="{:+.3f}"):
    return "n/a" if v is None else spec.format(100 * v)


def _t(v):
    return "n/a" if v is None else "{:+.2f}".format(v)


def _line(tag, st):
    return "%s n=%-4d days=%-3d gross %s%% net %s%% t %s win %s%%" % (
        tag, st["n"], st["days"], _p(st["gross"]), _p(st["net"]), _t(st["t"]), _p(st["win"], "{:.0f}"))


def inventory(by_key, dropped, log):
    log("INVENTORY (usable trades per route; net = gross - fee %.2f%% - measured book cost)" % (100 * FEE_RT))
    eligible = []
    for key in sorted(by_key, key=lambda k: -len(by_key[k])):
        rows = by_key[key]
        st = cluster_stats(rows)
        ok = st["n"] >= MIN_ROUTE_TRADES and st["days"] >= MIN_ROUTE_DAYS
        if ok:
            eligible.append(key)
        if st["n"] >= 20:
            cost = sorted(t["cost"] for t in rows)[len(rows) // 2]
            lost = sum(dropped.get(key, {}).values())
            log("  %-44s %s..%s %s book cost med %s%% kept %.0f%% epochs %d %s" % (
                key[:44], rows[0]["day"], rows[-1]["day"], _line("", st), _p(cost, "{:.3f}"),
                100.0 * st["n"] / (st["n"] + lost), len({t.get("epoch") for t in rows}), "ELIGIBLE" if ok else ""))
    small = sum(1 for k in by_key if len(by_key[k]) < 20)
    drop = {}
    for d in dropped.values():
        for why, c in d.items():
            drop[why] = drop.get(why, 0) + c
    log("  (%d more routes with < 20 trades not shown; dropped records: %s; kept = usable share of a route's records, "
        "epochs = distinct route_epoch tags)" % (small, drop or "none"))
    return eligible


def run(by_key, dropped=None, log=print, excluded=None):
    """목록 → 탐색(TRAIN) → 잠금 → 판정(HOLDOUT). 반환: 결과 dict."""
    eligible = inventory(by_key, dropped or {}, log)
    log("ELIGIBLE routes (>= %d trades, >= %d days): %d" % (MIN_ROUTE_TRADES, MIN_ROUTE_DAYS, len(eligible)))
    locked, shown, used = [], {}, set()
    for key in eligible:
        train, hold = split(by_key[key])
        log("== %s | TRAIN %s..%s | HOLDOUT %s..%s ==" % (key, train[0]["day"], train[-1]["day"],
                                                         hold[0]["day"] if hold else "-", hold[-1]["day"] if hold else "-"))
        log(_line("   base TRAIN  ", cluster_stats(train)))
        scans = scan(train)
        used.update(st["cond"][1] for st in scans)
        shown[key] = scans[:TOP_SHOW]
        for st in shown[key]:
            log(_line("   TRAIN %-30s" % ("%s %s %.4g" % (st["cond"][1], st["cond"][2], st["cond"][3])), st)
                + (" QUALIFIED" if st["qualified"] else ""))
        best = next((st for st in scans if st["qualified"]), None)
        log("   conditions tried: %d, net > 0: %d, qualified: %d" % (
            len(scans), sum(1 for s in scans if (s["net"] or 0) > 0 and s["n"] >= COND_MIN_TRADES),
            sum(1 for s in scans if s["qualified"])))
        if best is None:
            log("   -> no condition qualified on TRAIN: nothing to carry to HOLDOUT")
        else:
            locked.append({"key": key, "cond": best["cond"], "train": {k: best[k] for k in ("n", "days", "net", "gross", "t")}})
    log("INDICATORS offered as conditions (%d): %s" % (len(used), ", ".join(sorted(used)) or "-"))
    log("INDICATORS excluded as post-entry (%d): %s" % (len(excluded or ()), ", ".join(sorted(excluded or ())) or "-"))
    log("LOCKED %d condition(s) on TRAIN. HOLDOUT is opened only for these." % len(locked))
    alpha = ALPHA / max(1, len(locked))
    verdicts = []
    for item in locked:
        hold = split(by_key[item["key"]])[1]
        base, st = cluster_stats(hold), judge(hold, item["cond"], alpha)
        verdicts.append({"key": item["key"], "cond": item["cond"], "train": item["train"], "hold_base_net": base["net"],
                         "hold": {k: st[k] for k in ("n", "days", "net", "gross", "t", "daymean", "low", "pass")}})
        log("  %s + %s %s %.4g" % (item["key"], item["cond"][1], item["cond"][2], item["cond"][3]))
        log(_line("     HOLDOUT base", base) + " daymean %s%%" % _p(base["daymean"]))
        log(_line("     HOLDOUT cond", st) + " daymean %s%% low %s%% => %s" % (
            _p(st["daymean"]), _p(st["low"]), "PASS" if st["pass"] else "FAIL"))
    better = positive = total = 0
    for key in eligible:
        hold = split(by_key[key])[1]
        base = cluster_stats(hold)
        for st in shown[key]:
            h = cluster_stats([t for t in hold if passes(t, st["cond"])])
            if h["n"] >= HOLD_MIN_TRADES and base["net"] is not None:
                total += 1
                better += 1 if h["net"] > base["net"] else 0
                positive += 1 if h["net"] > 0 else 0
    log("CALIBRATION: of %d TRAIN top-%d conditions with enough HOLDOUT trades, %d beat their route's base and %d were "
        "positive after costs (no signal -> about half beat base)." % (total, TOP_SHOW, better, positive))
    n_pass = sum(1 for v in verdicts if v["hold"]["pass"])
    freeze = max((t["entry_ts"] for rows in by_key.values() for t in rows), default=0.0)
    log("H27 VERDICT: %s (%d of %d locked conditions passed; alpha per condition %.4f)" % (
        "PASS (candidates only - confirm with --frozen on trades after the freeze time)" if n_pass
        else ("FAIL" if locked else "NOTHING TO TEST"), n_pass, len(locked), alpha))
    return {"eligible": eligible, "locked": locked, "verdicts": verdicts, "freeze_ts": freeze,
            "calibration": {"total": total, "beat_base": better, "positive": positive},
            "indicators_offered": sorted(used), "indicators_excluded": sorted(excluded or ())}


def forward(by_key, frozen, log=print):
    """동결된 PASS 조건을 동결 시각 이후 거래에서만 다시 판정."""
    rules = [v for v in frozen.get("verdicts", []) if v["hold"]["pass"]]
    freeze = float(frozen.get("freeze_ts", 0.0))
    log("FORWARD: %d frozen rule(s), trades entered after %s KST only" % (
        len(rules), dt.datetime.fromtimestamp(freeze, KST).strftime("%Y-%m-%d %H:%M")))
    alpha, out = ALPHA / max(1, len(rules)), []
    for v in rules:
        rows = [t for t in by_key.get(v["key"], []) if t["entry_ts"] > freeze]
        base, st = cluster_stats(rows), judge(rows, v["cond"], alpha)
        out.append({"key": v["key"], "cond": v["cond"], "forward": {k: st[k] for k in ("n", "days", "net", "t", "low", "pass")}})
        log("  %s + %s %s %.4g" % (v["key"], v["cond"][1], v["cond"][2], v["cond"][3]))
        log(_line("     FORWARD base", base))
        log(_line("     FORWARD cond", st) + " daymean %s%% low %s%% => %s" % (
            _p(st["daymean"]), _p(st["low"]), "PASS" if st["pass"] else ("FAIL" if st["n"] >= HOLD_MIN_TRADES and st["days"] >= HOLD_MIN_DAYS
                                                      else "NOT ENOUGH YET")))
    return {"forward": out}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshots", nargs="+", help="shadow_stats*.json (copies of the bot file, read-only)")
    ap.add_argument("--out", default="h27_result")
    ap.add_argument("--no-market", action="store_true", help="skip the public BTC candle download (btc_* not tested)")
    ap.add_argument("--frozen", help="earlier h27_result.json: judge its PASS rules on trades after its freeze time")
    a = ap.parse_args(argv)
    paths = sorted({p for pat in a.snapshots for p in glob.glob(pat)})
    if not paths:
        print("no snapshot file matches: %s" % ", ".join(a.snapshots))
        return 2
    lines = []

    def log(s):
        print(s, flush=True)
        lines.append(s)

    log("H27 SHADOW SALVAGE | fee %.2f%% round trip + book cost at %dk KRW | TRAIN first %.0f%% by entry time" % (
        100 * FEE_RT, SIZE_K, 100 * TRAIN_FRAC))
    excluded = set()
    by_key, dropped = load_snapshots(paths, log=log, excluded=excluded)
    out = Path(a.out)
    entries = [t["entry_ts"] for rows in by_key.values() for t in rows]
    if a.no_market or not entries:
        log("MARKET STATE: UNAVAILABLE (skipped) - btc_* conditions not tested")
    else:
        try:
            bars = load_btc_bars(min(entries) - MARKET_IND[-1][1] - 3 * BTC_BAR_SEC, max(entries), out / "btc_5m.json")
            log("MARKET STATE: KRW-BTC 5m bars %d, attached to %.0f%% of trades" % (len(bars), 100 * attach_market(by_key, bars)))
        except Exception as e:                                   # 시세를 못 받아도 봇 지표만으로 진행한다
            log("MARKET STATE: UNAVAILABLE (%s: %s) - btc_* conditions not tested" % (type(e).__name__, e))
    if a.frozen:
        with open(a.frozen, encoding="utf-8") as f:
            result = forward(by_key, json.load(f), log=log)
    else:
        result = run(by_key, dropped, log=log, excluded=excluded)
    out.mkdir(parents=True, exist_ok=True)
    (out / "h27_brief.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "h27_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved:", out / "h27_brief.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
