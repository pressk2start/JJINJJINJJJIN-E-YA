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
             규칙 파일은 auto_monitor.py SHA-256 791ea2ce…8e09 로 테스트에 고정한다 (고문 원본 94a65c59…854f + '정확히 60분' 3줄
             = b27aabc8…cdcd, 거기서 머리말의 옛 실행 설정 문구만 고친 것. 코드는 b27aabc8 과 같다 — 2026-11-12 고문 요청).
             경위: 고문 원본은 봉 12개 전과 비교해서, 봉이 빠진 구간을 건너뛴 하락도 사건으로 잡았다 (예: 2026-11-09 업비트 점검
             02:00~07:00 KST 직후 ETH, 355분 사이의 −4.4%). 제3세션이 고쳤고, 고문이 한 번 원본 유지를 지시했다가 과거 전체를
             수정본으로 다시 집계한 뒤 수정본으로 확정했다 — 빠지는 거래는 대부분 거래소 점검·장애 직후라 실제 체결이 어려웠을
             구간이고, 추적 시작 이후 사건이 0건일 때 바꿔서 전진 검증은 오염되지 않는다.
             등록 규칙(수정본)의 과거 기준 수치 — 고문 집계, 처음 정한 통과 기준 넷을 모두 통과:
               2017–20  1,190건, 평균 순수익 +0.219%, 일별 평균 하한 +0.257%, 상위 5일 제외 +0.323%
               2021–26  1,134건, 평균 순수익 +0.361%, 일별 평균 하한 +0.373%, 상위 5일 제외 +0.440%
             (참고: 원본 규칙이면 2017–20 1,198건 +0.260%, 2021–26 1,139건 +0.359%. 이 수치는 더 이상 기준이 아니다.)
  CR_variants 급락 반등 변형 3개 — 고문 정의(2026-11-13 확정). 기준 규칙과 같은 계산(crv_events)에 숫자 하나만 바꾼다.
               CR_drop5   낙폭 기준 −5% (12봉 = 60분 뒤 매도)      CR_drop6   낙폭 기준 −6% (60분 뒤 매도)
               CR_hold4h  낙폭 기준 −4%, 48봉(정확히 240분) 뒤 매도
             종목·비용·'정확히 60분'·코인당 동시 1건·판정 기준(30건·10일, 평균 > 0 · 날짜 가중 평균 > 0)은 CR_rebound 와 같다.
             변형끼리는 서로 독립으로 센다. 추적 시작 2026-11-14 00:00 UTC (등록한 2026-11-13 의 다음 날 — 고문: "등록 다음 날 00:00 UTC").
             과거 수치(고문 집계, 2017–20 / 2021–26): −5% 705건 +0.48% / 569건 +0.68%, −6% 450건 +0.96% / 317건 +1.02%,
             4시간 843건 +0.28% / 881건 +0.46%.
             만료(고문 결정 2026-11-13): 급락 반등 계열은 시작 뒤 24개월 안에 30건·10일을 못 채우면 EXPIRED — 표본 부족 종료이지 탈락이 아니다.
  알트 동반 급락  고문 정의(2026-11-13). forward/registry.json 의 A-202611-01 로 등록해 '자동 등록 후보' 와 같은 경로로 채점한다.
             고문의 고정 20종목 15분봉. 급락 사건(15분 수익률 ≤ −3% 이고 거래대금이 직전 96개 봉 중앙값의 3배 이상)이면서
             같은 시각 BTC 4봉 수익률 ≤ −1.821% → 다음 봉 시가 매수, 4봉 뒤 시가 매도. 비용 편도 BTC·ETH 0.07%, 그 밖 0.10%.
             판정: 30건·10일, 평균 > 0, 날짜 가중 평균 > 0, 최고 수익일(일별 평균이 가장 높은 날) 하루를 빼도 날짜 가중 평균 > 0.
             추적 시작 2026-11-14 00:00 UTC, 만료 24개월. 과거 수치(고문): 2023–24 244건 +1.450% / 2025– 146건 +1.541%
             (discover.py --repro 가 같은 수치를 다시 계산해 대조한다).
  BTC_surge  BTC 주간 급등 관찰 (auto_monitor.surge_weeks). 월요일 00:00 UTC 에 직전 7일 수익률 ≥ +7.45% 인 주와 전체 주의
             BTC·ETH 주간 수익률을 기록한다. 판정 없음.
  자동 등록 후보  discover.py 가 매주 월요일과 매월 1일에 '사건 × 조건 하나 × 보유 시간' 틀에서 찾아 forward/registry.json 에 등록한 것.
             등록 때 고정한 정의로 등록일 이후 거래만 채점한다. 판정은 30건·10일이 처음 채워질 때 한 번, 12개월 안에 못 채우면 만료.
             후보마다 자기 종목만 받아 따로 채점한다(다른 후보의 종목이 섞이지 않고, 하나가 실패해도 나머지는 진행한다).
             상장폐지(고문 판정 2026-11-14): 업비트 상장 목록에서 사라진 종목은 그 뒤로 새 사건을 만들지 않는다. 이미 적힌 거래는
             그대로 두고, 후보는 남은 종목으로 계속 채점하며, 현황과 판정 기록에 '자료 불완전(종목, 날짜)' 으로 남긴다.
             그런 후보가 기한까지 표본을 못 채우면 '만료(표본 부족)' 가 아니라 '자료 불완전' 으로 끝낸다. 목록을 못 받으면 빼지 않는다.
  값 차이         다시 계산한 값이 장부와 다르면 장부는 그대로 두고, 처음 발견한 실행에서 한 번만 경고(실패)로 알린다.
             그 뒤로는 현황의 '알려진 값 차이' 에만 남고, 새 차이가 생겼을 때만 다시 실패로 끝난다.
             달라진 거래가 판정에 쓰는 표본에 들어 있으면 그 항목의 판정을 보류로 표시한다(진행 중이면 판정하지 않는다).
             보류 기록(hold)은 지우지 않는다. 달라진 값의 원본(장부 값과 다시 계산한 값)을 함께 남긴다. 해제는 고문 판정이
             있을 때만: --release-hold "항목 이름" --reason "사유" 가 해제 기록(날짜, 사유)을 덧붙인다 (고문 판정 2026-11-16 ②).
  H2 주석        검사 절차(주 1회 실행)는 바꾸지 않는다. 상장폐지 편향(통과 쪽)을 현황과 판정 기록에 주석으로 남긴다.
             추적 중인 후보가 없으면 시세도 받지 않는다. 틀(고문 지정, 15분봉)과 한계는 discover.py 머리말에 있다.
  실행 주기      (고문 판정 2026-11-17 ①) 매일 돈다. 예약은 하루 세 번(00:25·04:25·08:25 UTC) 걸려 있고, 그날(UTC) 이미 경고 없이
             끝난 실행이 있으면 뒤의 예약은 시세를 받지 않고 바로 끝난다(--done-today). 앞의 실행이 실패했으면 뒤의 예약이 다시 한다.
             판정 규칙은 그대로다 — 표본이 찼을 때 한 번만. 매일 돈다고 판정이 빨라지거나 여러 번 되지 않는다.
  새 거래 목록    그날 장부에 새로 붙은 거래(종목, 수익률)를 ledger["new"] 에 모아 하루 한 번 보내는 요약에 싣는다.
             실패한 날에 붙은 거래는 다음에 성공한 실행의 요약까지 남는다.
  관찰 표        (고문 판정 2026-11-17 ①) 현황에 최근 7일·28일의 건수와 거래당 평균(비용 뒤)을 싣는다: 기본 사건 3종(급등·돌파·급락,
             조건 없음, 1시간 보유 — 고문의 고정 20종목)과 등록 후보 각각. 관찰용이다. 판정에도 등록에도 쓰지 않는다
             (며칠 치 숫자를 보고 규칙을 고르기 시작하면 지금까지 막아 온 '찾을 때까지 다시 보기' 가 된다).
엔진은 이것 하나다 (고문 지시 2026-11-11): auto_monitor.py 는 규칙 계산을 빌려 쓰는 파일이고, 따로 도는 워크플로는 두지 않는다.

안전장치
  - 종목 하나라도 시세를 못 받으면 그 후보는 이번 실행에서 장부를 건드리지 않는다 (빠진 종목 때문에 다른 종목이 뽑히는 것을 막음).
    바이낸스는 '그 종목 없음(400)' 과 '못 받음' 을 구분한다.
  - H2 는 끝난 UTC 일자까지만 쓴다 (오늘 일봉이 아직 안 생긴 종목 때문에 값이 달라지는 것을 막음).
    급락 반등도 실행일 00:00 UTC 이전 봉까지만 쓴다 (같은 날 다시 돌려도 같은 결과).
  - 급락 반등은 4종목 중 하나라도 못 받으면 그 실행에서 건드리지 않는다 (판정에 쓰는 거래 순서가 실행마다 달라지는 것을 막음).
  - 후보 하나가 실패해도 나머지는 갱신하고, 실패는 현황의 '경고' 와 종료 코드 1 로 알린다.
한계: 가상 성적이며 실제 체결이 아니다. H2 는 주 1회만 반영하므로 그 사이에 상장폐지된 종목은 마지막 며칠 가격을 놓칠 수 있다.
      급락 반등 자료는 매번 시작 시각부터 다시 받는다 (하루 약 6회 조회씩 늘어난다 — 1년이면 2천 회 남짓).

실행
  python3 scalp/research/forward_watch.py              장부·현황 갱신
  python3 scalp/research/forward_watch.py --dry-run    계산만 하고 파일은 쓰지 않음
  python3 scalp/research/forward_watch.py --summary    장부만 읽어 짧은 요약을 출력 (시세 조회 없음)
  python3 scalp/research/forward_watch.py --done-today  오늘(UTC) 이미 경고 없이 끝난 실행이 있으면 종료 코드 0, 없으면 1 (시세 조회 없음)
  python3 scalp/research/forward_watch.py --stale-check --daily-schedule
                                                       어제(UTC) 추적 실행과 가장 최근 탐색 예약일(월요일·매월 1일)의 실행이 기록에 있는지 확인 (없으면 종료 코드 1)
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
CRV_START = AM.parse_utc("2026-11-14T00:00")        # 변형 추적 시작 (UTC). 등록(2026-11-13) 다음 날 00:00 — 바꾸지 않는다
CR_VARIANTS = (
    {"key": "CR_drop5", "name": "−5%", "rule": "1시간에 5% 넘게 빠지면 60분 보유", "drop": -0.05, "hold": 12},
    {"key": "CR_drop6", "name": "−6%", "rule": "1시간에 6% 넘게 빠지면 60분 보유", "drop": -0.06, "hold": 12},
    {"key": "CR_hold4h", "name": "4시간 보유", "rule": "1시간에 4% 넘게 빠지면 4시간 보유", "drop": -0.04, "hold": 48},
)
WAITING = ()
CR_EXPIRE_DAYS = 730                                # 급락 반등 계열: 24개월 안에 표본을 못 채우면 만료 (고문 결정 2026-11-13)
FIRST_WEEKLY, FIRST_MONTHLY = "2026-11-16", "2026-12-01"   # 첫 예약 실행일 (UTC). 그 전에는 빠진 실행이 있을 수 없다
FIRST_SEARCH_WEEKLY = "2026-11-23"                  # 주간 탐색(등록)의 첫 예약일 (UTC, 월요일) — 고문 판정 2026-11-17 ①
OBS_DAYS = (7, 28)                                  # 관찰 표의 창 (실행일 직전 UTC 일수)
OBS_EVENTS = (("SURGE", "급등"), ("BREAKOUT", "돌파"), ("CRASH", "급락"))
OBS_WARMUP_Q = 3 * DISC.BARS_D                      # 거래대금 기준(직전 96개 봉의 중앙값)에 필요한 앞쪽 자료
OBS_TAG = "관찰용, 판정과 등록에 쓰지 않는다"
NEW_SHOW = 12                                       # 요약에 싣는 새 거래 줄 수 (넘으면 '외 N건')
VERDICT = {"PASS": "통과", "FAIL": "탈락", "EXPIRED": "만료(표본 부족)", "INCOMPLETE": "자료 불완전"}
HOLD_TAG = " · 판정 보류(값 차이)"


def active_hold(entry):
    """지금 걸려 있는 보류 기록(해제 기록이 없는 것) 또는 None."""
    hold = (entry or {}).get("hold")
    return hold if hold and not hold.get("released") else None
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


def merge_rows(old_rows, new_rows, bad=None):
    """장부에 새 기간만 덧붙인다 → (합친 목록, 새로 붙은 수, 불일치 날짜들). 이미 적힌 기간은 그대로 둔다.
    bad(목록)를 주면 불일치한 기간의 열쇠(날짜)를 같은 순서로 넣는다."""
    by_date = {r["date"]: r for r in old_rows}
    added, mismatch = 0, []
    for r in new_rows:
        prev = by_date.get(r["date"])
        if prev is None:
            by_date[r["date"]] = r
            added += 1
        elif any(abs(prev[k] - r[k]) > TOL for k in ("strat", "ew", "btc")) or prev.get("picks") != r.get("picks"):
            mismatch.append(r["date"])
            if bad is not None:
                keep = ("strat", "ew", "btc", "picks")
                bad.append({"key": r["date"], "ledger": {k: prev.get(k) for k in keep}, "recomputed": {k: r.get(k) for k in keep}})
    return [by_date[d] for d in sorted(by_date)], added, mismatch


H2_BIAS_NOTE = ("상장폐지 편향(통과 쪽): 지금 상장돼 있는 종목만으로 지난 기간을 계산합니다. 그사이 상장폐지된 종목은 과거 선택에서도 "
                "빠지므로 결과가 통과 쪽으로 치우칠 수 있습니다.")     # 고문 판정 2026-11-14: 등록된 검사 절차는 바꾸지 않고 주석만 단다
LATE_OK_Q = 8                # 상장폐지가 확인된 후보에서, 지난 실행 기준 시각보다 이만큼(봉 수) 앞에서 끝난 거래까지는 새 거래로 받는다


def listed_markets():
    """지금 업비트 원화 시장에 있는 종목들."""
    return {m["market"] for m in AM.http_get(AM.UPBIT + "/v1/market/all") if m["market"].startswith("KRW-")}


class _Skip(Exception):
    """이번 실행에서는 그 항목을 반영하지 않는다(실패가 아니다)."""


def this_monday(today):
    """today 가 든 주의 월요일 (today 가 월요일이면 그날)."""
    d = dt.date.fromisoformat(today)
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def h2_decide(entry, run_day):
    """첫 H2_DECIDE_AT 기간이 찼고 아직 판정이 없으면 한 번만 판정해 적는다. 이미 있으면 건드리지 않는다."""
    rows = entry["rows"]
    if entry.get("decision") or len(rows) < H2_DECIDE_AT:
        return entry
    stats = DF.summarize(rows[:H2_DECIDE_AT])
    entry["decision"] = {"verdict": DF.verdict(stats, H2_DECIDE_AT), "decided_on": run_day, "stats": stats,
                         "periods": [rows[0]["date"], rows[H2_DECIDE_AT - 1]["date"]], "note": H2_BIAS_NOTE}
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


def crv_events(bars, cost, drop_thr, hold):
    """auto_monitor.cr_events 와 같은 계산에 낙폭 기준과 보유 봉 수만 바꾼 것 → 끝난 거래 목록. bars = [(분, 시가, 종가)] 시간순.
    drop_thr=AM.CR_DROP, hold=AM.CR_HOLD 를 주면 cr_events 의 끝난 거래와 같다 (테스트로 묶어 둔다)."""
    look, bar, n, done, last = AM.CR_LOOK, AM.CR_BAR, len(bars), [], -1
    for i in range(look, n):
        if bars[i][0] - bars[i - look][0] != look * bar:           # 12봉이 정확히 60분일 때만
            continue
        drop = bars[i][2] / bars[i - look][2] - 1
        if drop > drop_thr:
            continue
        e = i + 1
        if e <= last:                                              # 앞 거래를 아직 들고 있다
            continue
        if e >= n:
            break
        if bars[e][0] - bars[i - 1][0] != 2 * bar:
            continue
        x = e + hold
        if x >= n:                                                 # 보유 시간이 아직 안 지났다
            break
        if bars[x][0] - bars[e][0] != hold * bar:
            continue
        gross = bars[x][1] / bars[e][1] - 1
        done.append({"entry_min": bars[e][0], "exit_min": bars[x][0], "drop": drop, "entry": bars[e][1], "exit": bars[x][1], "gross": gross,
                     "net": (1 + gross) * (1 - cost) / (1 + cost) - 1})
        last = x
    return done


def crv_rows(today, fetch_bars=None):
    """변형별로, 추적 시작 이후 진입해 끝난 거래 → {key: [거래…]} 시간순. 5분봉은 종목당 한 번만 받아 세 변형에 같이 쓴다.
    실행일 00:00 UTC 이전 봉만 쓴다. 한 종목이라도 못 받으면 예외(세 변형 모두 이번 실행에서 건드리지 않는다)."""
    end = AM.parse_utc(today + "T00:00")
    out = {v["key"]: [] for v in CR_VARIANTS}
    if end <= CRV_START:
        return out
    first = AM.to_min(CRV_START)
    for m, cost in AM.CR_COST.items():
        try:
            bars = (fetch_bars or AM.fetch_5m)(m, CRV_START - CR_WARMUP, end)
        except Exception as e:
            raise RuntimeError("5분봉을 못 받은 종목: %s (%s)" % (m[4:], e))
        for v in CR_VARIANTS:
            out[v["key"]] += [dict(t, market=m) for t in crv_events(bars, cost, v["drop"], v["hold"]) if t["entry_min"] >= first]
    return {k: sorted(rows, key=lambda t: (t["entry_min"], t["market"])) for k, rows in out.items()}


def merge_cr(old_rows, new_rows, bad=None):
    """merge_rows 와 같은 원칙(덧붙이기만). 거래는 (종목, 진입 시각)으로 구분한다. bad 에는 달라진 거래의 열쇠가 들어간다."""
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
            if bad is not None:
                keep = ("net", "exit_min")
                bad.append({"key": key, "ledger": {k: prev.get(k) for k in keep}, "recomputed": {k: r.get(k) for k in keep}})
    return sorted(by_key.values(), key=lambda t: (t["entry_min"], t["market"])), added, mismatch


def _utc_day(minute):
    return dt.datetime.fromtimestamp(minute * 60, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def cr_decide(entry, run_day, start=None):
    """30건·10일이 처음 채워지는 거래까지로 한 번만 판정한다. 이미 판정이 있으면 건드리지 않는다.
    start(추적 시작 시각)를 주면 기한이 생긴다: 시작 뒤 CR_EXPIRE_DAYS 안에 진입한 거래만 표본으로 세고, 그 안에 못 채웠으면
    EXPIRED 로 끝낸다(표본 부족 종료, 탈락 아님). 기한 뒤의 거래로 부족분을 채우지 못한다. 실행이 늦었어도 기한 안에 채웠으면 판정한다."""
    if entry.get("decision"):
        return entry
    deadline = (start.date() + dt.timedelta(days=CR_EXPIRE_DAYS)).isoformat() if start is not None else None
    rows = [t for t in entry["rows"] if deadline is None or _utc_day(t["entry_min"]) <= deadline]
    days = set()
    for k, t in enumerate(rows, 1):
        days.add(_utc_day(t["entry_min"]))
        if k >= AM.CR_MIN_N and len(days) >= AM.CR_MIN_DAYS:
            stats = AM.cr_summary([(r["market"], r) for r in rows[:k]])
            entry["decision"] = {"verdict": "PASS" if stats["verdict"].startswith("PASS") else "FAIL", "decided_on": run_day,
                                 "trades": k, "stats": stats}
            return entry
    if deadline is not None and run_day > deadline:
        entry["decision"] = {"verdict": "EXPIRED", "decided_on": run_day, "trades": len(rows), "stats": None}
    return entry


def surge_rows(today, fetch_days=None):
    """끝난 주(다음 월요일 시가가 있는 주)의 BTC 주간 급등 관찰 → [{monday, r7, surge, net{종목: 수익률}}]."""
    now = AM.parse_utc(today + "T00:00")
    need = (now.date() - CR_START.date()).days + 20
    fetch_days = fetch_days or (lambda m: AM.fetch_days(m, need, now, keep_today=True))
    days = {m: fetch_days(m) for m in AM.H3_MARKETS}
    return [w for w in AM.surge_weeks(days, CR_START.strftime("%Y-%m-%d")) if set(w["net"]) == set(AM.H3_MARKETS)]


def window_stats(pairs, today, days):
    """pairs = [(진입일 UTC, 비용 뒤 수익률)] 가운데 today 직전 days 일(UTC)에 든 것 → [건수, 평균(없으면 None)]."""
    first = DF._shift(today, -days)
    vals = [v for d, v in pairs if first <= d < today]
    return [len(vals), sum(vals) / len(vals) if vals else None]


def observe(today, list_markets=None, panel_loader=None):
    """관찰 표의 '기본 사건' 부분 (고문 판정 2026-11-17 ①). 고문의 고정 20종목에서 조건 없는 급등·돌파·급락 사건 뒤 1시간 보유의,
    최근 7일·28일 건수와 거래당 평균(비용 뒤). 사건·비용 정의는 discover.py 그대로다. 관찰용 — 판정에도 등록에도 쓰지 않는다.
    상장 목록에서 빠진 종목은 빼고 계산해 이름을 남긴다(목록을 못 받으면 빼지 않는다). 시세를 못 받으면 예외.
    → {"as_of", "markets": 종목 수, "missing": [뺀 종목], "events": {사건: {"7": [건수, 평균], "28": [건수, 평균]}}}"""
    missing = []
    try:
        got = set((list_markets or listed_markets)())
        if "KRW-BTC" in got:                                        # 목록이 이상하면(비었거나 BTC 가 없으면) 믿지 않는다
            missing = sorted(m for m in DISC.REPRO_UNIVERSE if m not in got)
    except Exception:
        pass
    markets = tuple(m for m in DISC.REPRO_UNIVERSE if m not in missing)
    end_q = DISC.day_q(today)
    start_q = end_q - max(OBS_DAYS) * DISC.BARS_D
    panel = (panel_loader or DISC.load_panel)(start_q - OBS_WARMUP_Q, end_q, markets)
    trades = [t for t in DISC.build_trades(panel, start_q, end_q) if t["hold"] == DISC.BARS_H]
    events = {}
    for name, _label in OBS_EVENTS:
        pairs = [(t["day"], t["net"]) for t in trades if t["event"] == name]
        events[name] = {str(n): window_stats(pairs, today, n) for n in OBS_DAYS}
    return {"as_of": today, "markets": len(markets), "missing": missing, "events": events}


def observed_items(ledger):
    """관찰 표의 '등록 후보' 부분 → [(이름, [(진입일 UTC, 비용 뒤 수익률)])]. 장부에 적힌 거래만 쓴다(시세 조회 없음)."""
    out = []
    h2 = ledger.get("H2_kimchi")
    if h2 is not None:
        out.append(("H2 김치 되돌림 (3일 기간 단위)", [(r["date"], r["strat"]) for r in h2.get("rows", [])]))
    cr = ledger.get("CR_rebound")
    if cr is not None:
        out.append(("급락 반등", [(_utc_day(t["entry_min"]), t["net"]) for t in cr.get("rows", [])]))
    crv = ledger.get("CR_variants")
    if crv is not None:
        out += [("급락 반등 변형 " + v["name"], [(_utc_day(t["entry_min"]), t["net"]) for t in (crv.get(v["key"]) or {}).get("rows", [])])
                for v in CR_VARIANTS]
    out += [("후보 " + cid, [(t["day"], t["net"]) for t in e.get("rows", [])]) for cid, e in sorted((ledger.get("candidates") or {}).items())]
    return out


# ---------------------------------------------------------------------------------------------------------------------
def update(ledger, today, h2_inputs=None, h3_fetch=None, cr_fetch=None, surge_fetch=None, log=None, registry=None, panel_loader=None,
           crv_fetch=None, list_markets=None, observer=None):
    """장부를 갱신한다 → (장부, 경고 목록). h2_inputs() 는 (data, bn, failed) 를 돌려주는 함수.
    observer(today) 는 관찰 표의 기본 사건 부분을 돌려주는 함수(실행 때는 observe). 주지 않으면 그 부분은 건드리지 않는다."""
    log = log or (lambda s: None)
    warnings = []
    seen = ledger.setdefault("mismatch_seen", {})                  # 이미 알린 값 차이 (항목 → 표시 목록)
    # 새로 붙은 거래 목록(요약용). 지난 성공 실행의 요약에 실린 것은 날이 바뀌면 비우고, 실패한 날의 것은 다음 성공까지 남긴다.
    news = ledger.get("new") or {}
    if news.get("day") != today and news.get("day") == ledger.get("last_ok"):
        news = {}
    news = ledger["new"] = {"day": today, "items": list(news.get("items") or [])}

    def note_new(item, had, rows, key, market, when, value="net"):
        """had(갱신 전 장부에 있던 거래 열쇠)에 없는 거래를 새 거래 목록에 적는다."""
        for r in rows:
            if key(r) not in had:
                news["items"].append({"item": item, "market": market(r), "when": when(r), "net": r[value]})

    def cr_new(item, had, rows):
        note_new(item, had, rows, lambda r: (r["market"], r["entry_min"]), lambda r: r["market"][4:], lambda r: AM.kst(r["entry_min"]))

    def fresh(item, mismatch):
        """처음 보는 값 차이만 돌려주고 '알린 것' 으로 적어 둔다. 같은 차이는 다시 계산할 때마다 또 나오므로, 매번 경고로 올리면
        그 주부터 모든 실행이 실패로 끝난다(전체 검토 18번). 처음 한 번만 실패로 알리고, 그 뒤로는 현황표에만 남긴다."""
        known = set(seen.get(item, []))
        new = [m for m in mismatch if m not in known]
        if new:
            seen[item] = sorted(known | set(new))
        return new

    def hold_if_sampled(entry, new_labels, labels, bad, sample, always=()):
        """새로 드러난 값 차이가 판정에 쓰는 표본(sample: 거래 열쇠의 집합)에 걸리면 그 항목의 판정을 보류로 표시한다
        (고문 판정 2026-11-14 두 번째). always 에 든 표시는 표본과 무관하게 걸린 것으로 본다(지난 구간에 뒤늦게 나타난 거래).
        보류 기록에는 달라진 값의 원본(장부 값과 다시 계산한 값)을 남긴다. 이미 걸려 있는 보류는 건드리지 않고,
        해제된 보류가 있으면 지난 기록(hold_history)으로 옮긴 뒤 새로 건다 — 기록은 지우지 않는다."""
        by_label = dict(zip(labels, bad))
        hit = [lab for lab in new_labels if (by_label.get(lab) or {}).get("key") in sample or lab in always]
        if not hit or active_hold(entry):
            return
        if entry.get("hold"):
            entry.setdefault("hold_history", []).append(entry["hold"])
        entry["hold"] = {"since": today, "why": "값 차이: " + ", ".join(hit[:5]) + (" 외 %d건" % (len(hit) - 5) if len(hit) > 5 else ""),
                         "diffs": [{"what": lab, "ledger": by_label[lab]["ledger"], "recomputed": by_label[lab]["recomputed"]} for lab in hit],
                         "released": None}

    last_done = DF._shift(today, -1)
    h2 = ledger.setdefault("H2_kimchi", {"rows": [], "decision": None})
    try:
        # 추적은 매일 돌지만 H2 는 주 1회만 반영한다 (고문 판정 2026-11-14: 등록된 검사 절차를 중간에 바꾸지 않는다 — H2 는
        # 실행하는 날의 상장 목록으로 계산하므로 반영 주기가 달라지면 결과가 달라진다). 그 주 월요일 이후 첫 성공 실행이 반영한다.
        if (h2.get("updated") or "") >= this_monday(today):
            raise _Skip()
        data, bn, failed = (h2_inputs or (lambda: fetch_h2_inputs(log=log)))()
        if failed:
            raise RuntimeError("시세를 못 받은 종목 %d개: %s" % (len(failed), ", ".join(failed[:5])))
        bad = []
        had = {r["date"] for r in h2["rows"]}
        rows, added, mismatch = merge_rows(h2["rows"], h2_rows(data, bn, last_done), bad=bad)
        note_new("H2 김치 되돌림", had, rows, lambda r: r["date"], lambda r: ", ".join(m[4:] for m in r.get("picks", [])),
                 lambda r: r["date"] + " 매수", value="strat")
        h2["rows"] = rows
        h2["updated"] = today
        new = fresh("H2 김치 되돌림", mismatch)
        if new:
            warnings.append("H2: 다시 계산한 값이 장부와 다른 기간 %d개 (%s) — 장부 값은 그대로 둠" % (len(new), ", ".join(new[:5])))
            hold_if_sampled(h2, new, mismatch, bad, {r["date"] for r in rows[:H2_DECIDE_AT]})
        if not active_hold(h2):                                     # 보류 중이면 판정하지 않는다
            h2_decide(h2, today)
        log("H2: %d periods in ledger (+%d)" % (len(rows), added))
    except _Skip:
        log("H2: 이번 주 반영은 끝났음(주 1회, %s) — 건너뜀" % h2.get("updated"))
    except Exception as e:
        warnings.append("H2: 이번 실행에서 갱신하지 못함 — %s" % e)
    try:
        ledger["H3_trend"] = h3_status(today, h3_fetch)
        log("H3: status as of %s" % today)
    except Exception as e:
        warnings.append("H3: 이번 실행에서 갱신하지 못함 — %s: %s" % (type(e).__name__, e))
    cr = ledger.setdefault("CR_rebound", {"rows": [], "decision": None})
    try:
        bad = []
        had = {(r["market"], r["entry_min"]) for r in cr["rows"]}
        rows, added, mismatch = merge_cr(cr["rows"], cr_rows(today, cr_fetch), bad=bad)
        cr_new("급락 반등", had, rows)
        cr["rows"] = rows
        cr["updated"] = today
        new = fresh("급락 반등", mismatch)
        if new:
            warnings.append("급락 반등: 다시 계산한 값이 장부와 다른 거래 %d건 (%s) — 장부 값은 그대로 둠" % (len(new), ", ".join(new[:5])))
            used = (cr.get("decision") or {}).get("trades") or len(rows)    # 판정에 쓴(쓸) 거래: 판정 뒤에는 그때의 표본, 그 전에는 지금까지 전부
            hold_if_sampled(cr, new, mismatch, bad, {(r["market"], r["entry_min"]) for r in rows[:used]})
        if not active_hold(cr):
            cr_decide(cr, today, CR_START)
        log("CR: %d trades in ledger (+%d)" % (len(rows), added))
    except Exception as e:
        warnings.append("급락 반등: 이번 실행에서 갱신하지 못함 — %s" % e)
    crv = ledger.setdefault("CR_variants", {})
    try:
        for key, new_rows in crv_rows(today, crv_fetch).items():
            entry = crv.setdefault(key, {"rows": [], "decision": None})
            bad = []
            had = {(r["market"], r["entry_min"]) for r in entry["rows"]}
            rows, added, mismatch = merge_cr(entry["rows"], new_rows, bad=bad)
            cr_new("급락 반등 변형 " + next(v["name"] for v in CR_VARIANTS if v["key"] == key), had, rows)
            entry["rows"], entry["updated"] = rows, today
            new = fresh("급락 반등 변형 " + key, mismatch)
            if new:
                warnings.append("급락 반등 변형 %s: 다시 계산한 값이 장부와 다른 거래 %d건 (%s) — 장부 값은 그대로 둠" % (
                    key, len(new), ", ".join(new[:5])))
                used = (entry.get("decision") or {}).get("trades") or len(rows)
                hold_if_sampled(entry, new, mismatch, bad, {(r["market"], r["entry_min"]) for r in rows[:used]})
            if not active_hold(entry):
                cr_decide(entry, today, CRV_START)
            log("%s: %d trades in ledger (+%d)" % (key, len(rows), added))
    except Exception as e:
        warnings.append("급락 반등 변형: 이번 실행에서 갱신하지 못함 — %s" % e)
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
    listed = None                                                   # 지금 상장된 종목. 못 받으면 '모름' 으로 두고 어떤 종목도 빼지 않는다
    if open_now:
        try:
            got = set((list_markets or listed_markets)())
            listed = got if "KRW-BTC" in got else None              # 목록이 이상하면(비었거나 BTC 가 없으면) 믿지 않는다
        except Exception as e:
            log("상장 목록을 받지 못함: %s" % type(e).__name__)
    for c in open_now:                                              # 후보마다 따로: 자기 종목만 받아 채점하고, 하나가 실패해도 나머지는 진행한다
        entry = cands.setdefault(c["id"], {"rows": [], "decision": None, "rule": DISC.describe(c), "registered": c["registered"]})
        try:
            # 상장폐지 종목 (고문 판정 2026-11-14): 이미 적힌 거래는 그대로 둔다. 그 종목은 새 사건을 만들지 않고 현황에 표시한다.
            # 후보는 남은 종목으로 계속 채점한다. 한 번 빠진 종목은 다시 넣지 않는다.
            gone = entry.setdefault("delisted", {})
            if listed is not None:
                for m in c["universe"]:
                    if m not in listed and m not in gone:
                        gone[m] = today                             # 처음 확인한 날
            alive = tuple(sorted(m for m in c["universe"] if m not in gone))
            end_q = DISC.day_q(today)
            start_q = DISC.day_q(DISC.cand_start(c)) - DISC.WARMUP_Q
            panel = (panel_loader or DISC.load_panel)(start_q, end_q, alive)
            # 종목이 빠지면 다른 종목에 의존하는 지표가 달라져, 지난 구간에서 전에 없던 거래가 새로 계산될 수 있다 → 그런 거래는 넣지 않는다
            prev = entry.get("updated")
            not_before = DISC.day_q(prev) - LATE_OK_Q if (gone and prev) else None
            bad = []
            had = {(r["market"], r["entry_q"]) for r in entry["rows"]}
            rows, added, mismatch = DISC.merge_trades(entry["rows"], DISC.candidate_trades(panel, dict(c, universe=list(alive)), end_q),
                                                      not_before_q=not_before, bad=bad)
            note_new("후보 " + c["id"], had, rows, lambda r: (r["market"], r["entry_q"]), lambda r: r["market"][4:],
                     lambda r: AM.kst(r["entry_q"] * DISC.BAR_MIN))
            entry["rows"], entry["updated"] = rows, today
            new = fresh("후보 " + c["id"], mismatch)
            if new:
                warnings.append("후보 %s: 다시 계산한 값이 장부와 다른 거래 %d건 — 장부 값은 그대로 둠" % (c["id"], len(new)))
                hold_if_sampled(entry, new, mismatch, bad, {(r["market"], r["entry_q"]) for r in rows},      # 판정 전이라 지금까지의 거래가 전부 표본이다
                                always=[lab for lab in new if "뒤늦게 나타남" in lab])
            if not active_hold(entry):
                DISC.decide_once(entry, c, today)
            if entry.get("decision") and gone:                      # 자료 불완전: 판정 기록에 종목과 날짜를 남긴다
                entry["decision"].setdefault("delisted", dict(gone))
                if entry["decision"]["verdict"] == "EXPIRED":       # 종목이 빠져 표본이 모자란 것을 '표본 부족 만료' 로 끝내지 않는다
                    entry["decision"]["verdict"] = "INCOMPLETE"
            log("%s: %d trades in ledger (+%d)%s" % (c["id"], len(rows), added, " | 상장폐지: " + ", ".join(sorted(gone)) if gone else ""))
        except Exception as e:
            warnings.append("후보 %s: 이번 실행에서 갱신하지 못함 — %s" % (c["id"], e))
    if observer is not None:                                        # 관찰 표 — 판정·등록과 무관하다. 못 받으면 지난 값을 그대로 둔다
        try:
            ledger["observation"] = observer(today)
            log("관찰 표: %s 까지" % today)
        except Exception as e:
            warnings.append("관찰 표: 이번 실행에서 갱신하지 못함 — %s: %s" % (type(e).__name__, e))
    ledger["last_run"] = today
    ledger["warnings"] = warnings
    if not warnings:
        ledger["last_ok"] = today                                   # 경고 없이 끝난 마지막 실행일 — 같은 날의 뒤 예약은 이것을 보고 건너뛴다
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
    out += ["## H2 김치 프리미엄 되돌림 — " + head + (HOLD_TAG if active_hold(h2) else ""), "",
            "업비트 가격이 평소보다 싸진 종목 5개를 3일 보유. 추적 시작 2026-10-23, 자료 반영 %s." % h2.get("updated", "-"), "",
            "주의 — " + H2_BIAS_NOTE, ""]
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
        head = "판정 완료: %s (%s, 첫 %d건)" % (VERDICT[dec["verdict"]], dec["decided_on"], dec["trades"])
    else:
        head = "진행 중 (%d / %d건, %d / %d일)" % (len(rows), AM.CR_MIN_N, n_days, AM.CR_MIN_DAYS)
    out += ["## 급락 반등 — " + head + (HOLD_TAG if active_hold(cr) else ""), "",
            "BTC·ETH·XRP·SOL 이 1시간에 4%% 넘게 빠지면 다음 5분봉 시가에 사서 60분 뒤 판다. 추적 시작 2026-11-11, 자료 반영 %s." % cr.get("updated", "-"), ""]
    if rows:
        st = (dec or {}).get("stats") or AM.cr_summary([(t["market"], t) for t in rows])
        out += ["| 항목 | %s | 기준 | 상태 |" % ("판정에 쓴 값" if (dec or {}).get("stats") else "지금까지"), "|---|---|---|---|",
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
    crv = ledger.get("CR_variants") or {}
    out += ["## 급락 반등 변형 — 숫자만 바꾼 3개 (판정 기준은 위와 같음)", "",
            "같은 4종목, 같은 비용. 서로 독립으로 셉니다. 추적 시작 2026-11-14 (UTC).", "",
            "| 변형 | 규칙 | 진행 | 거래당 평균 | 날짜 가중 | 판정 |", "|---|---|---|---|---|---|"]
    for v in CR_VARIANTS:
        e = crv.get(v["key"]) or {"rows": []}
        rows, dec = e.get("rows", []), e.get("decision")
        st = ((dec or {}).get("stats") or AM.cr_summary([(t["market"], t) for t in rows])) if rows else None
        days = len({_utc_day(t["entry_min"]) for t in rows})
        out.append("| %s | %s | %d / %d건, %d / %d일 | %s | %s | %s |" % (
            v["name"], v["rule"], len(rows), AM.CR_MIN_N, days, AM.CR_MIN_DAYS, _pct(st["mean"]) if st else "-",
            _pct(st["day_mean"]) if st else "-", (VERDICT[dec["verdict"]] if dec else "진행 중") + (HOLD_TAG if active_hold(e) else "")))
    out += ["", "고문 정의(2026-11-13 확정). 24개월 안에 30건·10일을 못 채우면 만료로 끝납니다(탈락이 아니라 표본 부족).", ""]
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
    out += ["## 자동 등록 후보 — 매주 월요일과 매월 1일에 탐색해 넘은 것만 등록", ""]
    cands = ledger.get("candidates") or {}
    if cands:
        out += ["| 번호 | 규칙 | 등록 | 진행 | 거래당 평균 | 판정 |", "|---|---|---|---|---|---|"]
        for cid in sorted(cands):
            e = cands[cid]
            rows, dec = e.get("rows", []), e.get("decision")
            st = DISC.SS.cluster_stats(rows)
            verdict = VERDICT[dec["verdict"]] if dec else "진행 중"
            if e.get("delisted") and not (dec and dec["verdict"] == "INCOMPLETE"):
                verdict += " · 자료 불완전"
            if e.get("delisted"):
                verdict += "(%s)" % ", ".join("%s %s" % (m[4:], d) for m, d in sorted(e["delisted"].items()))
            if active_hold(e):
                verdict += HOLD_TAG
            out.append("| %s | %s | %s | %d / %d건, %d / %d일 | %s | %s |" % (
                cid, e.get("rule", "-"), e.get("registered", "-"), st["n"], DISC.FWD_MIN_N, st["days"], DISC.FWD_MIN_DAYS,
                "-" if st["net"] is None else _pct(st["net"]), verdict))
        out += ["", "통과는 '후보' 라는 뜻입니다. 봉 기준이고 실제 호가 체결은 확인하지 않은 값입니다.", ""]
        for cid in sorted(cands):
            gone = cands[cid].get("delisted") or {}
            if gone:
                out += ["- %s 자료 불완전: %s. 이미 적힌 거래는 그대로 두고, 확인한 날부터는 이 종목 없이 남은 종목으로 채점합니다. "
                        "기한까지 표본을 못 채워도 '표본 부족 만료' 로 처리하지 않습니다." % (
                            cid, ", ".join("%s 가 %s 에 상장 목록에서 빠진 것을 확인" % (m[4:], d) for m, d in sorted(gone.items()))), ""]
    else:
        out += ["아직 등록된 후보가 없습니다. 탐색 결과는 DISCOVERY.md 에 남습니다.", ""]
    out += render_observation(ledger)
    out += ["## 대기 중", ""] + (["- %s: %s" % w for w in WAITING] or ["- 없음"]) + [""]
    out += ["## 경고", ""] + (["- " + w for w in ledger.get("warnings", [])] or ["- 없음"]) + [""]
    held, freed = holds(ledger), holds(ledger, released=True)
    if held:
        out += ["## 판정 보류", "", "값이 달라진 거래가 판정에 쓰는 표본에 들어 있는 항목입니다. 진행 중이던 항목은 판정하지 않고, 이미 판정한 항목은 "
                "그 판정을 확정으로 보지 않습니다. 보류 기록은 지우지 않습니다. 고문 판정이 있을 때만 해제 기록(날짜, 사유)을 덧붙여 풉니다: "
                "forward_watch.py --release-hold \"항목 이름\" --reason \"사유\".", ""]
        for name, h in held:
            out.append("- %s: %s 부터 — %s" % (name, h.get("since", "-"), h.get("why", "-")))
            for d in (h.get("diffs") or [])[:5]:
                out.append("  - %s: 장부 %s → 다시 계산 %s" % (d["what"], _values(d.get("ledger")), _values(d.get("recomputed"))))
        out.append("")
    if freed:
        out += ["## 해제된 보류", ""]
        out += ["- %s: %s 보류(%s) → %s 해제 — %s" % (name, h.get("since", "-"), h.get("why", "-"), h["released"].get("on", "-"),
                                                  h["released"].get("reason", "-")) for name, h in freed] + [""]
    known = sorted((k, v) for k, v in (ledger.get("mismatch_seen") or {}).items() if v)
    if known:
        out += ["## 알려진 값 차이", "", "지난 봉을 다시 받아 계산한 값이 장부에 적힌 값과 달랐던 곳입니다. 장부 값은 바꾸지 않으며, "
                "처음 발견한 실행에서 한 번만 경고로 알렸습니다.", ""]
        out += ["- %s: %d건 (%s%s)" % (k, len(v), ", ".join(v[:5]), " 외" if len(v) > 5 else "") for k, v in known] + [""]
    return "\n".join(out)


def render_observation(ledger):
    """현황의 관찰 표 (고문 판정 2026-11-17 ①). 기본 사건은 실행 때 계산해 둔 값(ledger["observation"]), 등록 후보는 장부의 거래에서."""
    today = ledger.get("last_run")
    if not today:
        return []
    cols = "".join(" 최근 %d일 건수 | 평균 |" % n for n in OBS_DAYS)
    out = ["## 관찰 표 — " + OBS_TAG, "",
           "실행일 직전 7일·28일(UTC)에 진입한 거래의 건수와 거래당 평균(비용 뒤)입니다. 며칠 치 숫자는 우연에 크게 흔들립니다. "
           "이 표로 통과·탈락을 정하지 않고 후보를 등록하지도 않습니다. 판정은 위의 각 항목이 표본을 채웠을 때 한 번만 합니다.", "",
           "| 항목 |" + cols, "|---|" + "---|---|" * len(OBS_DAYS)]

    def cells(stats):
        return "".join(" %d | %s |" % (n, "-" if avg is None else _pct(avg)) for n, avg in stats)

    obs = ledger.get("observation")
    if obs:
        for name, label in OBS_EVENTS:
            out.append("| %s 뒤 1시간 보유 (조건 없음) |%s" % (label, cells([obs["events"][name][str(n)] for n in OBS_DAYS])))
    for name, pairs in observed_items(ledger):
        out.append("| %s |%s" % (name, cells([window_stats(pairs, today, n) for n in OBS_DAYS])))
    out.append("")
    if obs:
        ref = ", ".join("%s %s(%s건)" % (label, _pct(DISC.ADVISOR_REF[(name, DISC.BARS_H)][1][1], 3), format(DISC.ADVISOR_REF[(name, DISC.BARS_H)][1][0], ","))
                        for name, label in OBS_EVENTS)
        scope = "고문의 고정 20종목"
        if obs.get("missing"):
            scope += " 가운데 %d종목(상장 목록에 없어 뺀 종목: %s)" % (obs["markets"], ", ".join(m[4:] for m in obs["missing"]))
        out += ["기본 사건(위 %d줄): %s의 15분봉. 사건 봉 다음 봉 시가에 사서 1시간 뒤 시가에 판 것으로 계산합니다. 자료 반영 %s. "
                "참고로 같은 20종목의 2025-01 ~ 2026-11 평균은 %s 입니다(고문 집계)." % (len(OBS_EVENTS), scope, obs["as_of"], ref), ""]
    else:
        out += ["기본 사건(급등·돌파·급락)은 아직 계산된 값이 없습니다.", ""]
    out += ["등록 후보는 추적을 시작한 뒤의 거래만 장부에 있습니다. 시작한 지 28일이 안 된 항목의 28일 칸은 시작일부터의 값입니다.", ""]
    return out


def _values(d):
    """보류 기록에 남긴 값 한 벌을 한 줄로 (없으면 '없음')."""
    if not d:
        return "없음"
    return ", ".join("%s=%s" % (k, ("%.6f" % v) if isinstance(v, float) else v) for k, v in sorted(d.items()) if v is not None) or "없음"


def hold_entries(ledger):
    """보류가 걸릴 수 있는 항목들 → [(이름, 항목)]."""
    out = [("H2 김치 되돌림", ledger.get("H2_kimchi")), ("급락 반등", ledger.get("CR_rebound"))]
    out += [("급락 반등 변형 " + v["name"], (ledger.get("CR_variants") or {}).get(v["key"])) for v in CR_VARIANTS]
    out += [("후보 " + cid, e) for cid, e in sorted((ledger.get("candidates") or {}).items())]
    return [(name, e) for name, e in out if e]


def holds(ledger, released=False):
    """판정이 보류된 항목 → [(이름, hold 기록)]. released=True 면 해제된 보류 기록(지난 것 포함)을 돌려준다."""
    out = []
    for name, entry in hold_entries(ledger):
        if released:
            out += [(name, h) for h in (entry.get("hold_history") or []) + [entry.get("hold")] if h and h.get("released")]
        elif active_hold(entry):
            out.append((name, entry["hold"]))
    return out


def release_hold(ledger, name, reason, today):
    """보류를 해제한다 — 기록을 지우지 않고 해제 기록(날짜, 사유)을 덧붙인다. 고문 판정이 있을 때만 쓴다.
    → 해제한 hold 기록. 그 이름의 걸려 있는 보류가 없거나 사유가 비었으면 ValueError."""
    if not (reason or "").strip():
        raise ValueError("해제 사유(--reason)가 필요하다 — 고문 판정의 날짜와 내용을 적는다")
    for item, entry in hold_entries(ledger):
        if item == name and active_hold(entry):
            entry["hold"]["released"] = {"on": today, "reason": reason.strip()}
            return entry["hold"]
    raise ValueError("걸려 있는 보류가 없다: %s (지금 걸린 것: %s)" % (name, ", ".join(n for n, _h in holds(ledger)) or "없음"))


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
            lines.append("급락 반등: 판정 %s (%s)" % (VERDICT[dec["verdict"]], dec["decided_on"]))
        elif rows:
            st = AM.cr_summary([(t["market"], t) for t in rows])
            lines.append("급락 반등: %d/%d건 %d/%d일, 거래당 %s (판정 전)" % (st["n"], AM.CR_MIN_N, st["days"], AM.CR_MIN_DAYS, _pct(st["mean"])))
        else:
            lines.append("급락 반등: 발생 없음")
    crv = ledger.get("CR_variants")
    if crv is not None:
        parts = []
        for v in CR_VARIANTS:
            e = crv.get(v["key"]) or {}
            dec = e.get("decision")
            parts.append("%s %s" % (v["name"], VERDICT[dec["verdict"]] if dec else "%d건" % len(e.get("rows", []))))
        lines.append("급락 반등 변형: " + ", ".join(parts))
    cands = ledger.get("candidates") or {}
    if cands:
        done = [c for c, e in cands.items() if e.get("decision")]
        passed = [c for c in done if cands[c]["decision"]["verdict"] == "PASS"]
        lines.append("자동 등록 후보: %d개 추적, 판정 %d개%s" % (len(cands), len(done), " (통과: %s)" % ", ".join(sorted(passed)) if passed else ""))
        for c in sorted(cands):
            if cands[c].get("delisted"):
                lines.append("  %s 자료 불완전: %s 상장폐지 (남은 종목으로 계속 채점)" % (c, ", ".join(m[4:] for m in sorted(cands[c]["delisted"]))))
    held = holds(ledger)
    if held:
        lines.append("판정 보류(값 차이): " + ", ".join(name for name, _h in held))
    items = (ledger.get("new") or {}).get("items") or []           # 하루 한 메시지: 새로 끝난 거래가 있으면 종목과 수익률, 없으면 위의 진행 숫자만
    if items:
        lines.append("새로 끝난 거래 %d건:" % len(items))
        lines += ["  %s %s %s → %s" % (x["item"], x["market"], x["when"], _pct(x["net"])) for x in items[:NEW_SHOW]]
        if len(items) > NEW_SHOW:
            lines.append("  외 %d건" % (len(items) - NEW_SHOW))
    else:
        lines.append("새로 끝난 거래 없음")
    for w in ledger.get("warnings", []):
        lines.append("경고: " + w)
    return "\n".join(lines)


def expected_runs(today, daily=False):
    """오늘보다 앞선 가장 최근 예약일 → (주간 월요일, 월간 1일). 아직 첫 예약일 전이면 그 자리는 None.
    오늘이 예약일이면 오늘 것은 아직 기다리지 않는다(지난번 것만 본다)."""
    d = dt.date.fromisoformat(today)
    monday = (d - dt.timedelta(days=d.weekday() or 7)).isoformat()
    if daily and today > "2026-11-17":
        monday = (d - dt.timedelta(days=1)).isoformat()
    first = (d.replace(day=1) if d.day > 1 else (d - dt.timedelta(days=1)).replace(day=1)).isoformat()
    return (monday if monday >= FIRST_WEEKLY else None), (first if first >= FIRST_MONTHLY else None)


def expected_search(today):
    """오늘보다 앞선 가장 최근 탐색 예약일 → (날짜, "주간" 또는 "월간"). 탐색은 매주 월요일과 매월 1일에 돈다(둘 다 등록 실행).
    두 날 중 늦은 쪽을 기다린다. 아직 첫 예약일 전이면 None. 오늘이 예약일이면 오늘 것은 아직 기다리지 않는다."""
    d = dt.date.fromisoformat(today)
    monday = (d - dt.timedelta(days=d.weekday() or 7)).isoformat()
    first = (d.replace(day=1) if d.day > 1 else (d - dt.timedelta(days=1)).replace(day=1)).isoformat()
    due = [(day, kind) for day, kind, start in ((monday, "주간", FIRST_SEARCH_WEEKLY), (first, "월간", FIRST_MONTHLY)) if day >= start]
    return max(due) if due else None


def stale(ledger, scan_log, today, registry=(), daily=False):
    """예약 실행이 빠졌거나 일부 항목이 갱신되지 못했는지 → 문제 목록(없으면 빈 목록).
    '며칠 지났나' 가 아니라 '가장 최근 예약일의 실행이 기록에 있나' 를 본다. last_run 은 일부 항목이 실패해도 찍히므로,
    항목마다 따로 남는 갱신일(성공했을 때만 바뀜)도 같이 본다.
    매일 일정(daily)에서는 어제 실행을 기다린다. 김치 프리미엄(H2)만은 주 1회 반영이라 지난 월요일을 기다린다.
    탐색은 매일 일정에서는 주간·월간 예약일 중 늦은 쪽을, 그 밖에는 월간만 본다."""
    weekly, monthly = expected_runs(today, daily=daily)
    h2_need = expected_runs(today)[0] if daily else weekly          # H2 가 반영됐어야 하는 가장 최근 월요일
    label = "일간 추적" if daily else "주간 추적"
    suffix = "" if daily else "(월)"
    out = []
    if weekly:
        last = ledger.get("last_run")
        if not last or last < weekly:
            out.append("%s: %s%s 예약 실행이 기록에 없음 — 마지막 실행 %s" % (label, weekly, suffix, last or "없음"))
        else:
            h2_day = (ledger.get("H2_kimchi") or {}).get("updated")
            if h2_need and (not h2_day or h2_day < h2_need):
                out.append("김치 프리미엄: %s(월) 이후 갱신되지 못함 — 마지막 갱신 %s" % (h2_need, h2_day or "없음"))
            items = [("H3 추세", (ledger.get("H3_trend") or {}).get("as_of")),
                     ("급락 반등", (ledger.get("CR_rebound") or {}).get("updated")), ("BTC 주간 급등", (ledger.get("BTC_surge") or {}).get("updated")),
                     ("관찰 표", (ledger.get("observation") or {}).get("as_of"))]
            items += [("급락 반등 변형 " + v["name"], ((ledger.get("CR_variants") or {}).get(v["key"]) or {}).get("updated")) for v in CR_VARIANTS]
            cands = ledger.get("candidates") or {}                  # 등록부 기준으로 본다 — 한 번도 채점되지 못해 장부에 없는 후보도 잡는다
            for c in sorted(registry, key=lambda c: c["id"]):
                done = (cands.get(c["id"]) or {}).get("decision")
                # 기다리는 실행일보다 '앞서' 등록된 후보만 본다 — 같은 날 등록된 후보는 그날의 추적 실행이 등록보다 먼저 돌았을 수 있다
                if c.get("status") == "ACTIVE" and not done and c.get("registered", "") < weekly:
                    items.append(("후보 " + c["id"], (cands.get(c["id"]) or {}).get("updated")))
            for name, day in items:
                if not day or day < weekly:
                    out.append("%s: %s%s 이후 갱신되지 못함 — 마지막 갱신 %s" % (name, weekly, suffix, day or "없음"))
    need, kind = (expected_search(today) or (None, None)) if daily else (monthly, "월간")
    if need:
        run = scan_log[-1].get("run") if scan_log else None
        if not run or run < need:
            out.append("%s 탐색: %s 예약 실행이 기록에 없음 — 마지막 실행 %s" % (kind, need, run or "없음"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--stale-check", action="store_true")
    ap.add_argument("--daily-schedule", action="store_true", help="일간 실행 일정으로 누락 점검")
    ap.add_argument("--done-today", action="store_true", help="오늘(UTC) 이미 경고 없이 끝난 실행이 있으면 0, 없으면 1 (시세 조회 없음)")
    ap.add_argument("--today", help="UTC 날짜 YYYY-MM-DD (시험용, 기본은 지금)")
    ap.add_argument("--release-hold", metavar="NAME", help="판정 보류를 해제한다(고문 판정이 있을 때만). 현황의 '판정 보류' 에 적힌 항목 이름")
    ap.add_argument("--reason", default="", help="--release-hold 와 함께: 해제 사유 (고문 판정의 날짜와 내용)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    path = out / "ledger.json"
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if a.summary:
        print(summary(ledger))
        return 0
    today = a.today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    if a.done_today:                                                # 하루 세 번 걸린 예약 중 뒤의 것이 먼저 묻는다 — 장부의 날짜만 읽는다
        done = ledger.get("last_ok") == today
        print("오늘(%s UTC) 실행은 이미 경고 없이 끝났음 — 건너뜀" % today if done else
              "오늘(%s UTC) 경고 없이 끝난 실행이 아직 없음 (마지막 %s) — 실행함" % (today, ledger.get("last_ok") or "없음"))
        return 0 if done else 1
    if a.release_hold:                                              # 시세를 받지 않는다. 해제 기록만 덧붙이고 현황을 다시 쓴다
        try:
            hold = release_hold(ledger, a.release_hold, a.reason, today)
        except ValueError as e:
            print("해제하지 않음: %s" % e)
            return 2
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        (out / "STATUS.md").write_text(render(ledger), encoding="utf-8")
        print("해제 기록을 덧붙임: %s — %s 보류(%s) → %s 해제 (%s)" % (a.release_hold, hold["since"], hold["why"], today, a.reason.strip()))
        return 0
    if a.stale_check:
        log_path, reg_file = out / "scan_log.json", out / "registry.json"
        problems = stale(ledger, json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else [], today,
                         json.loads(reg_file.read_text(encoding="utf-8")) if reg_file.exists() else [], daily=a.daily_schedule)
        weekly, monthly = expected_runs(today, daily=a.daily_schedule)
        search = (expected_search(today) or (None,))[0] if a.daily_schedule else monthly
        print("\n".join(problems) if problems else "예약 실행 기록 정상 (추적 %s 이후 실행 %s, 탐색 기준일 %s)" % (
            weekly or "첫 예약 전", ledger.get("last_run"), search or "첫 예약 전"))
        return 1 if problems else 0
    reg_path = out / "registry.json"
    registry = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else []
    ledger, warnings = update(ledger, today, log=lambda s: print(s, flush=True), registry=registry, observer=observe)
    text = render(ledger)
    if not a.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        (out / "STATUS.md").write_text(text, encoding="utf-8")
    print(text)
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
