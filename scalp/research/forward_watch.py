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
  급락 소진 반등  고문 직접 등록(판정 2026-11-22 ②·③). 알트 15분 −3% 이하 + 그 종목 4시간 −5.92% 이하 + BTC 24시간 −1.73% 이하, 거래대금 조건 없음.
                 등록부에 세 항목으로 넣어 같은 경로로 채점한다: A-202611-02(주 대상 = 큰 5종목, 돈에 관한 판단은 이것으로만),
                 A-202611-02-비교(나머지 14종목, 비교용), A-202611-02-체결(큰 5종목의 −3% 급락 전부에 체결 추정만 붙인다 — 판정 없음).
                 BTC 는 지표용이라 거래로 적지 않는다. 손절도, 순간 폭락을 걸러 내는 조건도 없다 — 현황의 '위험과 배분' 절에 과거 위험을 싣는다.
  BTC_surge  BTC 주간 급등 관찰 (auto_monitor.surge_weeks). 월요일 00:00 UTC 에 직전 7일 수익률 ≥ +7.45% 인 주와 전체 주의
             BTC·ETH 주간 수익률을 기록한다. 판정 없음.
  자동 등록 후보  discover.py 가 매월 1일에 '사건 × 조건 하나 × 보유 시간' 틀에서 찾아 forward/registry.json 에 등록한 것 (매주 월요일은 점검만).
             등록 때 고정한 정의로 등록일 이후 거래만 채점한다. 판정은 30건·10일이 처음 채워질 때 한 번, 12개월 안에 못 채우면 만료.
             후보마다 자기 종목만 받아 따로 채점한다(다른 후보의 종목이 섞이지 않고, 하나가 실패해도 나머지는 진행한다).
             상장폐지(고문 판정 2026-11-14): 업비트 상장 목록에서 사라진 종목은 그 뒤로 새 사건을 만들지 않는다. 이미 적힌 거래는
             그대로 두고, 후보는 남은 종목으로 계속 채점하며, 현황과 판정 기록에 '자료 불완전(종목, 날짜)' 으로 남긴다.
             그런 후보가 기한까지 표본을 못 채우면 '만료(표본 부족)' 가 아니라 '자료 불완전' 으로 끝낸다. 목록을 못 받으면 빼지 않는다.
  값 차이         다시 계산한 값이 장부와 다르면 장부는 그대로 두고, 처음 발견한 실행에서 한 번만 경고(실패)로 알린다.
             그 뒤로는 현황의 '알려진 값 차이' 에만 남고, 새 차이가 생겼을 때만 다시 실패로 끝난다.
             달라진 거래가 판정에 쓰는 표본에 들어 있으면 그 항목의 판정을 보류로 표시한다(진행 중이면 판정하지 않는다).
             보류 기록(hold)은 지우지 않는다. 달라진 값의 원본(장부 값과 다시 계산한 값)을 함께 남긴다. 보류 중에 새로 드러난
             차이도 같은 기록에 덧붙인다(제3세션 지적 2026-11-17). 보류 중인 후보는 탐색에서 '끝난 후보' 로 치지 않는다. 해제는 고문 판정이
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
  체결 내역 기반 추정 (고문 판정 2026-11-19, 이름과 내용은 2026-11-20) 봉 기준 수익이 실제 체결에서도 남는지 추정한다.
             대상: 급락 반등, 변형 3종, 자동 등록 후보. 우리 주문의 실제 체결이 아니라 다른 사람들의 공개 체결로 추정한 값이다 —
             그때의 호가 잔량과 진입 지연은 들어 있지 않고, 봉과의 차이에는 60초 동안의 가격 움직임도 섞인다(제3세션 지적).
             새 거래마다 업비트 공개 체결 기록(/v1/trades/ticks)에서 진입 시각부터 60초, 청산 시각부터 60초의 체결을 받는다.
             · 60초 값: 진입 60초의 시장가 매수(BID) 평균가(가격×수량의 합 ÷ 수량의 합)에 사서, 청산 60초의 시장가 매도(ASK) 평균가에 판 값.
             · 주문 크기 값(2026-11-20): 진입 시각 뒤 첫 ORDER_KRW(기본 100만 원)어치 BID 체결의 평균가에 사서, 청산 시각 뒤 첫 ORDER_KRW
               어치 ASK 체결의 평균가에 판 값. 60초 안에 그만큼 차지 않으면 '자료 부족'. 2단계 ② 의 체결 차이는 이 값으로 잰다.
             수수료 편도 0.05%. 한쪽이라도 60초 안에 체결이 없으면 '60초 안 체결 자료 부족'(주문이 불가능했다는 뜻은 아니다).
             업비트는 7일 전까지만 준다(days_ago 1~7) — 처음 채점할 때 이미 지났으면 '체결 자료 없음'. days_ago 는 문서에는 '요청 시점과의
             차이' 라고 적혀 있지만, 실제 API 는 그 종목의 마지막 체결일을 기준으로 센다(2026-11-20 00:01 UTC 확인: 오늘 체결이 없던
             CARV·LSK 에 days_ago=1 → 11/18). 그래서 종목의 마지막 체결일을 먼저 받아 세고, 받은 체결의 날짜가 어긋나면 값을 내지 않는다.
             원본 체결(시각, 가격, 수량, BID/ASK)은 거래마다 forward/ticks/ 에 gzip JSON 으로 남긴다(다시 검증할 수 있게 — 2026-11-20).
             조회에 실패한 거래는 비워 두고 다음 실행에서 다시 받는다(그 실행은 경고로 끝나 뒤의 예약이 다시 돈다).
             1단계 선별은 등록된 대로 봉 기준이다. 현황에 봉 기준 평균과 추정 평균, 체결 자료 건수(n / 전체 N건)를 나란히 싣는다.
  진입일 수      (고문 판정 2026-11-19) 급락형은 거래가 급락일 몇 날에 몰린다. 진짜 표본 크기를 보이려고 현황의 각 후보에 진입일 수,
             일별 승률(일별 평균 > 0 인 날의 비율), 일별 중앙값을 싣는다. 등록·판정 기준은 바꾸지 않는다.
  세 단계       (고문 판정 2026-11-19 ②, 2026-11-20 ②) 30건·10일 판정은 '1단계 선별' 이라 부른다(기준 그대로) —
             '계속 볼 가치가 있다' 는 뜻이지 돈을 넣는 신호가 아니다. 드문 사건의 우위를 새 자료만으로 증명하려면 1년 넘게 걸리므로
             (고문 집계: 급락 반등 −4% 약 11~13개월, 알트 동반 급락 5~7년), 2단계는 증명이 아니라 '소액 시험 자격' 이고, 증명은 돈을 늘리는
             3단계에서 요구한다. 둘 다 표시만 한다(자동 결정 없음. 돈은 고문과 사용자가 정한다).
             2단계 점검 시점은 둘뿐이다: 체결 자료 20건과 서로 다른 진입일 5일이 처음 함께 채워진 때, 40건과 10일이 처음 채워진 때.
             그 시점까지의 체결 자료로 낸 값과, 그때까지의 전체 신호 수·자료 없음 수·진입일 수를 그날 장부에 고정하고 덮어쓰지 않는다.
               ① 1단계 통과
               ② 과거 거래당 평균(두 시기 중 낮은 쪽) − 체결 차이의 97.5% 상한 > 0. 체결 차이 = 봉 기준 − 추정(주문 크기 값이 있으면 그것,
                  없으면 60초 값). 과거 쪽도 같은 봉 기준 비용을 쓰므로 비용 가정 차이는 상쇄된다. 표시는 가격 차이와 수수료 차이를 나눠 보인다.
               ③ 주문 금액 × 10 이상의 체결이 진입 매수와 청산 매도 양쪽에 다 있었던 거래의 비율 ≥ 80% (분모 = 체결 자료가 있는 거래 전체)
               ④ 붕괴 점검: 전진 진입일 10일 미만이면 '판단 보류'. 10일 이상이면 전진 일별 평균의 95% 상한이 과거 일별 평균(낮은 쪽)의
                  절반보다 낮을 때 '미달(붕괴 징후)', 아니면 '충족(붕괴 증거 없음, 증명 아님)'.
             3단계(증액 기준): 전진 일별 평균의 99% 하한 > 0. 점검은 전진 진입일 30·60·90일이 처음 된 때만, 그때 값으로 고정한다.
             전진은 지금 가상 거래(봉 기준)뿐이다 — 실거래가 생기면 함께 센다.
             과거 값(거래당 평균, 일별 평균)은 고문 집계: 급락 반등 계열은 CR_PAST, 후보는 등록부의 train/holdout.
  조건별 전진 표 (고문 판정 2026-11-22 ⑤, 사용자 지적) 장부는 거래마다 신호 낙폭·60분 수익·체결·짝만 남긴다. 사용자 설계(급락 반등 검토안
               1~8번과 '볼 값')가 새 자료에서도 쌓이도록, 고정 20종목의 15분 −3% 급락 전부(거래대금 조건 없음)와 BTC 직접 사건을
               사건마다 forward/conditions.csv 에 한 줄씩 붙인다(crash_log.py — 과거 9년 파일과 같은 정의·같은 열, 뒤에 붙이기만 한다).
               현황에는 그 줄들을 조건별로 묶은 표를 과거 9년 값과 나란히 싣는다. 관찰용이다 — 판정에도 등록에도 쓰지 않는다.
  짝 관찰       (고문 판정 2026-11-20 ② 7) 자동 등록 후보의 거래마다 같은 진입·청산 시각의 BTC·ETH 봉 기준 수익(비용 편도 0.07%)을
             함께 적고, 현황에 '신호 종목 vs BTC vs ETH' 평균을 나란히 싣는다. 판정 없음 — 알트 동반 급락의 수익이 시장 전체의 반등인지 보려는 것.
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
import gzip
import importlib.util
import json
import sys
import urllib.error
from pathlib import Path
from statistics import median

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
CL = _load("crash_log")                             # 전진 조건 기록 (고문 판정 2026-11-22 ⑤)

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
FIRST_SEARCH_WEEKLY = "2026-11-23"                  # 주간 탐색 점검(등록 없음)의 첫 예약일 (UTC, 월요일)
OBS_DAYS = (7, 28)                                  # 관찰 표의 창 (실행일 직전 UTC 일수)
OBS_EVENTS = (("SURGE", "급등"), ("BREAKOUT", "돌파"), ("CRASH", "급락"))
OBS_WARMUP_Q = 3 * DISC.BARS_D                      # 거래대금 기준(직전 96개 봉의 중앙값)에 필요한 앞쪽 자료
OBS_TAG = "관찰용, 판정과 등록에 쓰지 않는다"
COND_FILE = "conditions.csv"                        # 전진 조건 기록: 급락 사건마다 한 줄 (forward/ 아래, 뒤에 붙이기만 한다)
COND_WARMUP_Q = 3 * DISC.BARS_D                     # 직전 96개 봉(거래대금 중앙값)과 BTC 24시간 수익에 필요한 앞쪽 자료
COND_PAST = "conditions_past.json"                  # 같은 표를 과거 9년 자료로 낸 값 (같은 코드로 한 번 계산해 둔 것. 비교용)
NEW_SHOW = 12                                       # 요약에 싣는 새 거래 줄 수 (넘으면 '외 N건')
FILL_SEC = 60                                       # 진입·청산 시각부터 이만큼(초) 동안의 실제 체결로 값을 낸다 (고문 판정 2026-11-19)
FILL_FEE = 0.0005                                   # 추정에 쓰는 수수료 편도 (업비트 원화 시장)
FILL_DAYS = 7                                       # 업비트가 체결 기록을 주는 기간: days_ago 1~7 (8 은 HTTP 400). 기준일은 그 종목의 마지막 체결일
NO_FILL, NO_DATA, SHORT = "60초 안 체결 자료 부족", "체결 자료 없음", "자료 부족"   # 이름은 고문 판정 2026-11-20
LEGACY_STATUS = {"체결 없음": NO_FILL}               # 2026-11-20 전 이름 — 읽을 때 새 이름으로 본다
TICKS_DIR = "ticks"                                 # 원본 체결을 남기는 하위 폴더 (forward/ticks/)
TICKS_PAGE, TICKS_MAX_PAGES = 500, 60               # 한 번에 받는 체결 수(업비트 최대), 한 구간에서 넘기는 쪽수 한도
ORDER_KRW = 1000000                                 # 주문 금액 (설정값 — 고문 판정 2026-11-19 ②·11-20: 기본 100만 원). 주문 크기 값과 2단계 ③ 에 쓴다
STAGE2_POINTS = ((20, 5), (40, 10))                 # 2단계 점검 시점: (체결 자료 건수, 서로 다른 진입일 수) 가 처음 함께 채워진 때 (고문 판정 2026-11-20 ②)
STAGE2_CAPACITY_X, STAGE2_CAPACITY_MIN = 10, 0.80   # ③ 주문 금액 × 10 이상이 진입 매수·청산 매도 양쪽에 다 있었던 거래의 비율 ≥ 80%
STAGE2_HOLD_DAYS, STAGE2_COLLAPSE = 10, 0.5         # ④ 전진 진입일이 10일 미만이면 판단 보류, 95% 상한 < 과거 일별 평균 × 0.5 면 붕괴 징후
STAGE3_POINTS = (30, 60, 90)                        # 3단계(증액 기준) 점검: 전진 진입일이 이만큼 처음 된 때 (고문 판정 2026-11-20 ②)
PAIR_MARKETS, PAIR_COST = ("KRW-BTC", "KRW-ETH"), 0.0007   # 짝 관찰: 같은 시각의 BTC·ETH 봉 기준 수익, 비용 편도 0.07%
# 2단계 ②·④ 의 과거 봉 기준 값 — 급락 반등 계열. (기간, 거래당 평균, 일별 평균)
CR_PAST = {"CR_rebound": (("2017–20", 0.00219, 0.00410), ("2021–26", 0.00361, 0.00507)),     # 고문 집계 2026-11-20 ② (봉 기준, 비용 뒤)
           "CR_drop5": (("2017–20", 0.00484, 0.00674), ("2021–26", 0.00681, 0.00875)),
           "CR_drop6": (("2017–20", 0.00958, 0.01155), ("2021–26", 0.01022, 0.01198)),
           "CR_hold4h": (("2017–20", 0.00283, 0.00305), ("2021–26", 0.00456, 0.00640))}
STAGE1 = "1단계 선별"
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


def day_stats(pairs):
    """[(진입일, 수익률)] → (진입일 수, 일별 승률, 일별 중앙값). 일별 승률 = 그날 거래의 평균이 0 보다 큰 날의 비율.
    거래가 없으면 (0, None, None). 고문 판정 2026-11-19: 급락형은 진짜 표본 크기가 거래 수보다 급락 일수에 가깝다."""
    by_day = {}
    for d, v in pairs:
        by_day.setdefault(d, []).append(v)
    means = [sum(v) / len(v) for v in by_day.values()]
    if not means:
        return 0, None, None
    return len(means), sum(1 for m in means if m > 0) / float(len(means)), median(means)


def _utc_date(ms):
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc).date()


def _status(f):
    """체결 값의 상태 (옛 이름은 새 이름으로)."""
    s = (f or {}).get("status")
    return LEGACY_STATUS.get(s, s)


def ticks(market, start_ms, end_ms, base_day, get=None):
    """[start_ms, end_ms) 의 실제 체결 → [(시각 ms, 가격, 수량, "ASK" 또는 "BID")] 시각순.
    업비트 /v1/trades/ticks: days_ago = 조회할 날짜와 기준일(base_day)의 일 단위 차이(1~7, 같은 날이면 넣지 않는다), to = 그날의 UTC 시각
    (그 초 직전까지), 새 것부터 500건씩, cursor(sequential_id) 로 이어 받는다. 기준일은 문서에는 '요청 시점' 이라고 적혀 있지만 실제로는
    그 종목의 마지막 체결일이다(2026-11-20 확인) — 부르는 쪽이 그 날짜를 받아 넘긴다. 받은 체결의 날짜가 조회한 날보다 뒤면(기준이 어긋남)
    값을 내지 않고 예외로 끝낸다 — 다음 실행에서 다시 받는다. sequential_id 는 순서를 보장하지 않는다고 문서에 있어, 같은 체결은 번호로
    한 번만 세고 시각으로 거른다."""
    get = get or AM.http_get
    utc = dt.timezone.utc
    start = dt.datetime.fromtimestamp(start_ms / 1000.0, tz=utc)
    stop = dt.datetime.fromtimestamp(end_ms / 1000.0, tz=utc)
    day = start.date()
    ago = (base_day - day).days
    if ago < 0:                                                     # 그 종목의 마지막 체결보다 뒤의 구간이다 — 체결이 없다
        return []
    if ago > FILL_DAYS:
        raise ValueError("체결 기록은 %d일 전까지만 받을 수 있다 (%d일 전)" % (FILL_DAYS, ago))
    to = stop.strftime("%H%M%S") if stop.date() == day else "235959"   # 봉 시가에서 60초라 자정을 넘는 일은 없다
    url = "%s/v1/trades/ticks?market=%s&count=%d&to=%s%s" % (AM.UPBIT, market, TICKS_PAGE, to, "&days_ago=%d" % ago if ago else "")
    out, cursor = {}, None
    for _ in range(TICKS_MAX_PAGES):
        rows = get(url + ("&cursor=%s" % cursor if cursor else ""))
        if not rows:
            return sorted(out.values())
        if max(r["trade_date_utc"] for r in rows) > day.isoformat():
            raise RuntimeError("%s 체결 기록의 날짜가 맞지 않음 (조회 %s, 받은 %s)" % (market[4:], day, max(r["trade_date_utc"] for r in rows)))
        for r in rows:
            if start_ms <= r["timestamp"] < end_ms:
                out[r["sequential_id"]] = (r["timestamp"], float(r["trade_price"]), float(r["trade_volume"]), r["ask_bid"])
        if min(r["timestamp"] for r in rows) < start_ms or len(rows) < TICKS_PAGE:
            return sorted(out.values())
        cursor = rows[-1]["sequential_id"]
    raise RuntimeError("%s 체결이 %d건을 넘어 끝까지 받지 못함" % (market[4:], TICKS_PAGE * TICKS_MAX_PAGES))


def order_price(trades, side, krw):
    """시각순 체결에서 side 방향을 krw 원어치까지 모은 평균가(마지막 체결은 필요한 만큼만 센다) → 가격. 그만큼 차지 않으면 None."""
    need, cost, vol = float(krw), 0.0, 0.0
    for _ts, p, v, s in trades:
        if s != side or p <= 0 or v <= 0:
            continue
        take = min(v, need / p)
        cost, vol, need = cost + take * p, vol + take, need - take * p
        if need <= krw * 1e-9:
            return cost / vol
    return None


def _net(buy, sell):
    return round(sell * (1 - FILL_FEE) / (buy * (1 + FILL_FEE)) - 1, 10)


def _vs(px, open_px):
    return round(px / open_px - 1, 10) if open_px else None


def order_values(trades_in, trades_out, entry_open, exit_open):
    """주문 크기 값 (고문 판정 2026-11-20 3): 진입 뒤 첫 ORDER_KRW 어치 BID, 청산 뒤 첫 ORDER_KRW 어치 ASK 의 평균가."""
    ob, os_ = order_price(trades_in, "BID", ORDER_KRW), order_price(trades_out, "ASK", ORDER_KRW)
    if ob is None or os_ is None:
        return {"order_krw": ORDER_KRW, "order_status": SHORT}
    return {"order_krw": ORDER_KRW, "order_status": "ok", "order_buy": ob, "order_sell": os_, "order_net": _net(ob, os_),
            "order_buy_vs_open": _vs(ob, entry_open), "order_sell_vs_open": _vs(os_, exit_open)}


def summarize(trades_in, trades_out, entry_open, exit_open):
    """원본 체결(진입 60초, 청산 60초) → 요약 값: 60초 값, 양쪽 체결 금액, 주문 크기 값."""
    def side(rows, s):
        return [(p, v) for _ts, p, v, x in rows if x == s]

    def krw(rows):
        return int(round(sum(p * v for p, v in rows)))

    def vwap(rows):
        return sum(p * v for p, v in rows) / sum(v for p, v in rows)
    buy, sell = side(trades_in, "BID"), side(trades_out, "ASK")
    out = {"entry_bid_krw": krw(buy), "entry_ask_krw": krw(side(trades_in, "ASK")), "exit_bid_krw": krw(side(trades_out, "BID")), "exit_ask_krw": krw(sell)}
    if not buy or not sell or sum(v for _, v in buy) <= 0 or sum(v for _, v in sell) <= 0:
        out["status"] = NO_FILL
    else:
        bp, sp = vwap(buy), vwap(sell)
        out.update(status="ok", buy=bp, sell=sp, net=_net(bp, sp), buy_vs_open=_vs(bp, entry_open), sell_vs_open=_vs(sp, exit_open))
    out.update(order_values(trades_in, trades_out, entry_open, exit_open))
    return out


def fetch_windows(market, entry_ms, exit_ms, get=None):
    """진입·청산 60초 창의 원본 체결 → (진입 쪽, 청산 쪽). 그 종목의 마지막 체결일을 먼저 받아 기준일로 쓴다. 7일이 지났으면 None."""
    get = get or AM.http_get
    latest = get("%s/v1/trades/ticks?market=%s&count=1" % (AM.UPBIT, market))
    if not latest:
        raise RuntimeError("%s 최근 체결을 받지 못함" % market[4:])
    base = dt.date.fromisoformat(latest[0]["trade_date_utc"])
    if (base - _utc_date(entry_ms)).days > FILL_DAYS:
        return None
    return (ticks(market, entry_ms, entry_ms + FILL_SEC * 1000, base, get), ticks(market, exit_ms, exit_ms + FILL_SEC * 1000, base, get))


def fill_check(market, entry_ms, exit_ms, entry_open, exit_open, get=None):
    """한 거래의 체결 내역 기반 추정 (고문 판정 2026-11-19·20) → 요약 값. 7일이 지났으면 {"status": 체결 자료 없음}."""
    w = fetch_windows(market, entry_ms, exit_ms, get)
    if w is None:
        return {"status": NO_DATA, "order_status": NO_DATA}
    return summarize(w[0], w[1], entry_open, exit_open)


def tick_name(market, entry_ms, exit_ms):
    """원본 체결 파일 이름: 종목_진입_청산(UTC).json.gz. 같은 거래를 여러 항목이 추적해도 한 파일."""
    def stamp(ms):
        return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc).strftime("%Y%m%dT%H%M")
    return "%s_%s_%s.json.gz" % (market, stamp(entry_ms), stamp(exit_ms))


def save_ticks(path, market, entry_ms, exit_ms, trades_in, trades_out):
    """원본 체결을 gzip JSON 으로 (고문 판정 2026-11-20 2). 같은 내용이면 같은 바이트가 나오게 쓴다."""
    body = {"market": market, "entry_ms": entry_ms, "exit_ms": exit_ms, "window_sec": FILL_SEC, "source": "upbit /v1/trades/ticks",
            "fields": ["timestamp_ms", "price", "volume", "ask_bid"], "entry": [list(t) for t in trades_in], "exit": [list(t) for t in trades_out]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"), mtime=0))


def load_ticks(path):
    body = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    return [tuple(t) for t in body["entry"]], [tuple(t) for t in body["exit"]]


_T95 = (6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812, 1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734,
        1.729, 1.725, 1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697)
_T975 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101,
         2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)


def t95(df):
    """단측 95% t 분위수 (자유도 df ≥ 1). 30 까지는 표, 그 뒤는 근사(40: 1.685, 120: 1.658)."""
    return _T95[df - 1] if df <= 30 else 1.645 + 1.6 / df


_T99 = (31.821, 6.965, 4.541, 3.747, 3.365, 3.143, 2.998, 2.896, 2.821, 2.764, 2.718, 2.681, 2.650, 2.624, 2.602, 2.583, 2.567, 2.552,
        2.539, 2.528, 2.518, 2.508, 2.500, 2.492, 2.485, 2.479, 2.473, 2.467, 2.462, 2.457)


def t99(df):
    """단측 99% t 분위수 (자유도 df ≥ 1). 30 까지는 표, 그 뒤는 근사(40: 2.423, 120: 2.358)."""
    return _T99[df - 1] if df <= 30 else 2.326 + 3.9 / df


def t975(df):
    """단측 97.5% t 분위수 (자유도 df ≥ 1). 30 까지는 표, 그 뒤는 근사(40: 2.021, 120: 1.980)."""
    return _T975[df - 1] if df <= 30 else 1.96 + 2.4 / df


def _est(t):
    """거래 하나의 추정 → (추정 순수익, 추정 매수가, 추정 매도가, 주문 크기 값인가). 주문 크기 값이 있으면 그것, 없으면 60초 값, 둘 다 없으면 None."""
    f = t.get("fill") or {}
    if f.get("order_status") == "ok":
        return f["order_net"], f.get("order_buy"), f.get("order_sell"), True
    if _status(f) == "ok":
        return f["net"], f.get("buy"), f.get("sell"), False
    return None


def fill_stats(rows):
    """한 항목의 체결 통계 (고문 판정 2026-11-19 ②·11-20·11-20 ②).
    tried = 체결 자료가 있는 거래(60초 값이 나왔거나 60초 안 자료 부족), ok = 60초 값, order_ok = 주문 크기 값이 나온 거래.
    gap = 봉 기준 − 추정(주문 크기 값이 있으면 그것, 없으면 60초 값; + 면 실제가 나쁘다), gap_up975 = 그 평균의 단측 97.5% 상한.
    price·fee = gap 을 나눈 것: 가격 차이 = 봉 기준 등락 − 추정 등락(같은 수수료로 본 차이), 수수료 차이 = 나머지(봉 기준 비용과 실제 수수료의 차이).
    cap_rate = 주문 금액 × STAGE2_CAPACITY_X 이상의 체결이 진입 매수와 청산 매도 양쪽에 다 있었던 거래의 비율(분모 tried).
    short_rate = 주문 크기 값이 '자료 부족' 인 비율, depth = 거래마다 min(진입 60초 매수, 청산 60초 매도) 체결 금액의 중앙값."""
    tried = [t for t in rows if _status(t.get("fill")) in ("ok", NO_FILL)]
    ok = [t for t in tried if _status(t["fill"]) == "ok"]
    order_ok = [t for t in tried if t["fill"].get("order_status") == "ok"]
    gaps, price, n_order = [], [], 0
    for t in tried:
        e = _est(t)
        if e is None:
            continue
        net, buy, sell, is_order = e
        gaps.append(t["net"] - net)
        n_order += is_order
        if buy and sell and "gross" in t:
            price.append(t["gross"] - (sell / buy - 1))
    need = ORDER_KRW * STAGE2_CAPACITY_X
    out = {"rows": len(rows), "tried": len(tried), "ok": len(ok), "nofill": len(tried) - len(ok), "order_ok": len(order_ok),
           "nodata": sum(1 for t in rows if _status(t.get("fill")) == NO_DATA), "gap_n": len(gaps), "gap_order_n": n_order,
           "gap": None, "gap_up975": None, "price": None, "fee": None, "cap_ok": 0, "cap_rate": None, "short_rate": None, "depth": None}
    if gaps:
        out["gap"] = sum(gaps) / len(gaps)
    if len(gaps) >= 2:
        sd = (sum((g - out["gap"]) ** 2 for g in gaps) / (len(gaps) - 1)) ** 0.5
        out["gap_up975"] = out["gap"] + t975(len(gaps) - 1) * sd / len(gaps) ** 0.5
    if price and len(price) == len(gaps):
        out["price"] = sum(price) / len(price)
        out["fee"] = out["gap"] - out["price"]
    if tried:
        out["cap_ok"] = sum(1 for t in tried if t["fill"].get("entry_bid_krw", 0) >= need and t["fill"].get("exit_ask_krw", 0) >= need)
        out["cap_rate"] = out["cap_ok"] / float(len(tried))
        out["short_rate"] = (len(tried) - len(order_ok)) / float(len(tried))
        out["depth"] = median(min(t["fill"].get("entry_bid_krw", 0), t["fill"].get("exit_ask_krw", 0)) for t in tried)
    return out


def _day_means(rows, day):
    by_day = {}
    for t in rows:
        by_day.setdefault(day(t), []).append(t["net"])
    return [sum(x) / len(x) for _d, x in sorted(by_day.items())]


def _mean_sd(vals):
    m = sum(vals) / len(vals)
    sd = (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 if len(vals) >= 2 else None
    return m, sd


def stage2_point(rows, n, min_days, minute, day):
    """2단계 점검 (고문 판정 2026-11-20 ②): 체결 자료가 있는 거래가 n 건 이상이고 그 진입일이 min_days 일 이상이 처음 함께 된 시점의 값
    → dict, 아직이면 None. 체결 쪽(②③)은 그 시점까지 체결 자료가 있는 거래로, 전진 쪽(④)은 그 시점까지 진입한 모든 거래로 낸다.
    그때까지의 전체 신호 수·자료 없음 수도 같이 남긴다. 처음 나온 날 장부에 고정한다(다시 계산해 덮어쓰지 않는다)."""
    tried = sorted((t for t in rows if _status(t.get("fill")) in ("ok", NO_FILL)), key=lambda t: (minute(t), t["market"]))
    days, k = set(), None
    for i, t in enumerate(tried, 1):
        days.add(day(t))
        if i >= n and len(days) >= min_days:
            k = i
            break
    if k is None:
        return None
    sample, last = tried[:k], minute(tried[k - 1])
    fs = fill_stats(sample)
    upto = [t for t in rows if minute(t) <= last]
    means = _day_means(upto, day)
    fwd, sd = _mean_sd(means)
    up95 = fwd + t95(len(means) - 1) * sd / len(means) ** 0.5 if sd is not None else None
    return {"n": n, "min_days": min_days, "k": k, "sample_days": len(days), "through_min": last, "signals": len(upto),
            "nodata": sum(1 for t in upto if _status(t.get("fill")) == NO_DATA), "gap_n": fs["gap_n"], "gap_order_n": fs["gap_order_n"],
            "gap": fs["gap"], "gap_up975": fs["gap_up975"], "price": fs["price"], "fee": fs["fee"], "cap_ok": fs["cap_ok"], "cap_rate": fs["cap_rate"],
            "fwd_days": len(means), "fwd_trades": len(upto), "fwd_daymean": fwd, "fwd_up95": up95}


def stage2_cells(point, past):
    """고정된 점검 값과 과거 값 → ②③④ 의 [(충족 True / 미달 False / 판단 못 함·보류 None, 설명)]. 표시만 한다."""
    out = []
    edge = min((p[1] for p in past), default=None)
    if edge is None:
        out.append((None, "과거 우위 미기록"))
    elif point["gap_up975"] is None:
        out.append((None, "체결 차이 %d건 — 상한을 낼 수 없음" % point["gap_n"]))
    else:
        val = edge - point["gap_up975"]
        out.append((val > 0, "%s − %s = %s (체결 차이 %d건, 그중 주문 크기 값 %d건)" % (
            _pct(edge), _pct(point["gap_up975"]), _pct(val), point["gap_n"], point["gap_order_n"])))
    out.append((point["cap_rate"] >= STAGE2_CAPACITY_MIN, "양쪽 %s원 이상 %d / %d건 (%s, 기준 %d%%)" % (
        format(ORDER_KRW * STAGE2_CAPACITY_X, ","), point["cap_ok"], point["k"], _rate(point["cap_rate"]), round(100 * STAGE2_CAPACITY_MIN))))
    past_day = min((p[2] for p in past if p[2] is not None), default=None)
    if point["fwd_days"] < STAGE2_HOLD_DAYS:
        out.append((None, "판단 보류 — 전진 진입일 %d일 (%d일부터)" % (point["fwd_days"], STAGE2_HOLD_DAYS)))
    elif past_day is None:
        out.append((None, "과거 일별 평균 미기록"))
    else:
        floor = STAGE2_COLLAPSE * past_day
        if point["fwd_up95"] < floor:
            out.append((False, "붕괴 징후: 전진 일별 평균 %s 의 95%% 상한 %s < 과거 %s 의 절반 %s (%d일)" % (
                _pct(point["fwd_daymean"]), _pct(point["fwd_up95"]), _pct(past_day), _pct(floor), point["fwd_days"])))
        else:
            out.append((True, "붕괴 증거 없음(증명 아님): 95%% 상한 %s ≥ %s (%d일)" % (_pct(point["fwd_up95"]), _pct(floor), point["fwd_days"])))
    return out


def stage3_point(rows, need_days, day):
    """3단계(증액 기준, 고문 판정 2026-11-20 ②): 전진 진입일이 need_days 일이 처음 된 시점 — 처음 need_days 일의 거래로 낸 일별 평균과
    99% 하한 → dict, 아직이면 None. 처음 나온 날 장부에 고정한다."""
    by_day = {}
    for t in rows:
        by_day.setdefault(day(t), []).append(t["net"])
    if len(by_day) < need_days:
        return None
    first = sorted(by_day)[:need_days]
    means = [sum(by_day[d]) / len(by_day[d]) for d in first]
    m, sd = _mean_sd(means)
    low = m - t99(len(means) - 1) * sd / len(means) ** 0.5
    return {"days": need_days, "through_day": first[-1], "trades": sum(len(by_day[d]) for d in first), "daymean": m, "low99": low}


def pair_returns(panel, row):
    """짝 관찰 (고문 판정 2026-11-20 ② 7): 같은 진입·청산 봉 시가로 산 BTC·ETH 의 봉 기준 수익(비용 편도 PAIR_COST) → {종목: 수익률}."""
    out = {}
    for m in PAIR_MARKETS:
        b = panel.get(m)
        if b is None:
            continue
        i, j = b.idx(row["entry_q"]), b.idx(row["exit_q"])
        if i >= 0 and j >= 0 and b.o[i] > 0:
            out[m] = round(b.o[j] / b.o[i] * (1 - PAIR_COST) / (1 + PAIR_COST) - 1, 10)
    return out


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
    out = {"as_of": today, "markets": len(markets), "missing": missing}
    # events = 거래대금 3배 조건이 걸린 사건(탐색의 기본 정의, VOL_X), events0 = 거래대금 조건 없는 사건 (고문 판정 2026-11-22 ⑤ 5)
    for key, vol_x in (("events", DISC.VOL_X), ("events0", 0.0)):
        trades = [t for t in DISC.build_trades(panel, start_q, end_q, vol_x=vol_x) if t["hold"] == DISC.BARS_H]
        out[key] = {}
        for name, _label in OBS_EVENTS:
            pairs = [(t["day"], t["net"]) for t in trades if t["event"] == name]
            out[key][name] = {str(n): window_stats(pairs, today, n) for n in OBS_DAYS}
    return out


def conditions(today, prev=None, path=None, write=True, list_markets=None, panel_loader=None, get=None, past_path=None):
    """전진 조건 기록 (고문 판정 2026-11-22 ⑤, 사용자 지적). 고정 20종목의 15분 −3% 급락 전부(거래대금 조건 없음)와 BTC 직접 사건마다
    조건 값과 신호 뒤 60분의 움직임을 path(CSV)에 한 줄씩 붙인다. 정의·열은 과거 9년 파일과 같다(crash_log.py). 관찰용 — 판정·등록에 쓰지 않는다.
    연구 자료 끝 다음 날(CL.START_DAY)부터 소급하고, 그 뒤로는 지난 실행의 하루 전부터 다시 훑는다(이미 적은 사건 ID 는 건너뛴다).
    신호 뒤 60분이 아직 안 지난 사건과 5분봉을 못 받은 사건은 적지 않는다. write=False 면 파일을 건드리지 않고 표만 계산한다.
    past_path 는 같은 표를 과거 9년 자료로 낸 파일(conditions_past.json). 있으면 칸마다 과거 값을 옆에 싣는다.
    → {"since", "through", "events": 적힌 사건 수, "added": 이번에 붙인 수, "missing": [뺀 종목], "table": 조건별 표의 칸, "past": 과거 값 또는 None}"""
    missing = []
    try:
        got = set((list_markets or listed_markets)())
        if "KRW-BTC" in got:
            missing = sorted(m for m in DISC.REPRO_UNIVERSE if m not in got)
    except Exception:
        pass
    markets = tuple(m for m in DISC.REPRO_UNIVERSE if m not in missing)
    since = CL.START_DAY
    if prev and prev.get("through"):                                # 지난 실행의 하루 전부터: 그때 60분이 안 지나 못 적은 사건을 줍는다
        since = max(since, (dt.date.fromisoformat(prev["through"]) - dt.timedelta(days=1)).isoformat())
    start_q, end_q = DISC.day_q(since), DISC.day_q(today)
    old = CL.load(path) if path else []
    new = []
    if end_q > start_q:
        panel = (panel_loader or DISC.load_panel)(start_q - COND_WARMUP_Q, end_q, markets)
        five = CL.FiveMin(fetch=CL.upbit_fetch5(get or AM.http_get, AM.UPBIT))
        new = CL.rows(panel, five, start_q, end_q, known={r["사건 ID"] for r in old})
    added = CL.append(path, new) if (path and write) else 0
    rows = CL.load(path) if (path and write) else old + [{k: CL.text(r.get(k)) for k in CL.COLUMNS} for r in new]
    past = None
    if past_path and Path(past_path).exists():
        got = json.loads(Path(past_path).read_text(encoding="utf-8"))
        past = {"period": got["period"], "cells": {c["key"]: [c["n"], c["days"], c["means"][3]] for c in got["table"]}}
    return {"since": CL.START_DAY, "through": today, "events": len(rows), "added": added if write else len(new), "missing": missing, "table": CL.table(rows),
            "past": past}


def observed_items(ledger):
    """관찰 표의 '등록 후보' 부분 → [(이름, [(진입일 UTC, 비용 뒤 수익률)], 자료 반영일 또는 None)]. 장부에 적힌 거래만 쓴다(시세 조회 없음)."""
    out = []
    h2 = ledger.get("H2_kimchi")
    if h2 is not None:
        out.append(("H2 김치 되돌림 (3일 기간 단위)", [(r["date"], r["strat"]) for r in h2.get("rows", [])], h2.get("updated")))
    cr = ledger.get("CR_rebound")
    if cr is not None:
        out.append(("급락 반등", [(_utc_day(t["entry_min"]), t["net"]) for t in cr.get("rows", [])], cr.get("updated")))
    crv = ledger.get("CR_variants")
    if crv is not None:
        for v in CR_VARIANTS:
            e = crv.get(v["key"]) or {}
            out.append(("급락 반등 변형 " + v["name"], [(_utc_day(t["entry_min"]), t["net"]) for t in e.get("rows", [])], e.get("updated")))
    out += [("후보 " + cid, [(t["day"], t["net"]) for t in e.get("rows", [])], e.get("updated"))
            for cid, e in sorted((ledger.get("candidates") or {}).items())]
    return out


# ---------------------------------------------------------------------------------------------------------------------
def update(ledger, today, h2_inputs=None, h3_fetch=None, cr_fetch=None, surge_fetch=None, log=None, registry=None, panel_loader=None,
           crv_fetch=None, list_markets=None, observer=None, fill_get=None, tick_dir=None, cond_log=None):
    """장부를 갱신한다 → (장부, 경고 목록). h2_inputs() 는 (data, bn, failed) 를 돌려주는 함수.
    observer(today) 는 관찰 표의 기본 사건 부분을 돌려주는 함수(실행 때는 observe). 주지 않으면 그 부분은 건드리지 않는다.
    fill_get(url) 은 체결 기록 조회 함수(실행 때는 auto_monitor.http_get). 주지 않으면 체결 내역 기반 추정은 건드리지 않는다.
    tick_dir 은 원본 체결을 남길 폴더(실행 때는 forward/ticks).
    cond_log(today, 지난 값) 은 전진 조건 기록을 갱신하고 요약을 돌려주는 함수(실행 때는 conditions). 주지 않으면 건드리지 않는다."""
    log = log or (lambda s: None)
    warnings = []
    seen = ledger.setdefault("mismatch_seen", {})                  # 이미 알린 값 차이 (항목 → 표시 목록)
    # 새로 붙은 거래 목록(요약용). 지난 성공 실행의 요약에 실린 것은 날이 바뀌면 비우고, 실패한 날의 것은 다음 성공까지 남긴다.
    news = ledger.get("new") or {}
    if news.get("day") != today and news.get("day") == ledger.get("last_ok"):
        news = {}
    news = ledger["new"] = {"day": today, "items": list(news.get("items") or [])}

    def note_new(item, had, rows, key, market, when, value="net"):
        """had(갱신 전 장부에 있던 거래 열쇠)에 없는 거래를 새 거래 목록에 적는다. 체결 내역 기반 추정이 있으면 같이 적는다."""
        for r in rows:
            if key(r) not in had:
                x = {"item": item, "market": market(r), "when": when(r), "net": r[value]}
                f = r.get("fill")
                if f:
                    x["fill"] = f["net"] if _status(f) == "ok" else _status(f)
                    if f.get("order_status"):
                        x["order"] = f["order_net"] if f["order_status"] == "ok" else f["order_status"]
                news["items"].append(x)

    fill_errors = []

    def attach(item, rows, times, opens):
        """체결 내역 기반 추정이 아직 없는 거래에 붙인다(fill_get 이 있을 때만). 이미 붙은 거래는 다시 받지 않는다.
        60초 값은 있는데 주문 크기 값이 없는 거래(2026-11-20 전에 채점된 것)는, 원본 파일이 있으면 그것으로, 없으면 7일 안일 때
        다시 받아 주문 크기 값만 더한다 — 이미 있는 60초 값은 그대로 둔다(고문 판정 2026-11-20 3). 받은 원본은 tick_dir 에 남긴다.
        조회에 실패한 거래는 비워 두고 다음 실행에서 다시 받는다 — 업비트가 7일만 주므로 경고로 알려 뒤의 예약이 다시 돌게 한다."""
        if fill_get is None:
            return
        for r in rows:
            f = r.get("fill")
            if f and f.get("status") in LEGACY_STATUS:
                f["status"] = LEGACY_STATUS[f["status"]]
            if f and "order_status" in f:
                continue
            if f and f["status"] == NO_DATA:
                f["order_status"] = NO_DATA
                continue
            try:
                (e_ms, x_ms), (e_open, x_open) = times(r), opens(r)
                path = Path(tick_dir) / tick_name(r["market"], e_ms, x_ms) if tick_dir else None
                if path is not None and path.exists():
                    w = load_ticks(path)
                else:
                    w = fetch_windows(r["market"], e_ms, x_ms, fill_get)
                    if w is not None and path is not None:
                        save_ticks(path, r["market"], e_ms, x_ms, w[0], w[1])
                if w is None:
                    if f:
                        f["order_status"] = NO_DATA
                    else:
                        r["fill"] = {"status": NO_DATA, "order_status": NO_DATA}
                    continue
                s = summarize(w[0], w[1], e_open, x_open)
                if f:
                    f.update((k, v) for k, v in s.items() if k.startswith("order_"))
                else:
                    r["fill"] = s
            except Exception as e:
                fill_errors.append("%s %s (%s)" % (item, r["market"][4:], type(e).__name__))

    def cr_times(r):
        return r["entry_min"] * 60000, r["exit_min"] * 60000

    def cr_opens(r):
        return r.get("entry"), r.get("exit")

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
        보류 기록에는 달라진 값의 원본(장부 값과 다시 계산한 값)과 발견한 날을 남긴다. 이미 보류가 걸려 있으면 새로 드러난 차이의
        원본을 그 기록에 덧붙인다(전에는 버려졌다 — 제3세션 지적 2026-11-17). 해제된 보류가 있으면 지난 기록(hold_history)으로
        옮긴 뒤 새로 건다 — 기록은 지우지 않는다."""
        by_label = dict(zip(labels, bad))
        hit = [lab for lab in new_labels if (by_label.get(lab) or {}).get("key") in sample or lab in always]
        if not hit:
            return
        diffs = [{"what": lab, "found": today, "ledger": by_label[lab]["ledger"], "recomputed": by_label[lab]["recomputed"]} for lab in hit]
        if active_hold(entry):
            entry["hold"].setdefault("diffs", []).extend(diffs)
            return
        if entry.get("hold"):
            entry.setdefault("hold_history", []).append(entry["hold"])
        entry["hold"] = {"since": today, "why": "값 차이: " + ", ".join(hit[:5]) + (" 외 %d건" % (len(hit) - 5) if len(hit) > 5 else ""),
                         "diffs": diffs, "released": None}

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
        attach("급락 반등", rows, cr_times, cr_opens)
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
            name = "급락 반등 변형 " + next(v["name"] for v in CR_VARIANTS if v["key"] == key)
            attach(name, rows, cr_times, cr_opens)
            cr_new(name, had, rows)
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
        entry["past"] = [[(part.get("period") or label), part.get("net"), part.get("daymean")] for label, part in (
            [(None, part) for part in c["past"]] if c.get("past") else (("앞 구간", c.get("train")), ("뒤 구간", c.get("holdout"))))
                         if part and part.get("net") is not None]     # 2단계 ②·④ 용 과거 봉 기준 값 (등록 때 고정된 값 그대로). past = 기간 여러 개
        for key in ("role", "risk"):                                 # 고문 판정 2026-11-22 ②·③: 역할(주 대상·비교용·체결 관찰)과 위험 표
            if c.get(key) is not None:
                entry[key] = c[key]
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
            for r in rows:                                          # 상장폐지된 종목은 체결 기록을 받을 수 없다
                if r["market"] in gone and "fill" not in r:
                    r["fill"] = {"status": NO_DATA, "order_status": NO_DATA, "why": "상장폐지"}

            def cand_opens(r, panel=panel):
                b = panel.get(r["market"])
                if b is None:
                    return None, None
                i, j = b.idx(r["entry_q"]), b.idx(r["exit_q"])
                return (b.o[i] if i >= 0 else None), (b.o[j] if j >= 0 else None)
            attach("후보 " + c["id"], rows, lambda r: (r["entry_q"] * DISC.BAR_MIN * 60000, r["exit_q"] * DISC.BAR_MIN * 60000), cand_opens)
            for r in rows:                                          # 짝 관찰: 같은 시각의 BTC·ETH (이미 적힌 거래는 그대로)
                if "pair" not in r:
                    pair = pair_returns(panel, r)
                    if pair:
                        r["pair"] = pair
            note_new("후보 " + c["id"], had, rows, lambda r: (r["market"], r["entry_q"]), lambda r: r["market"][4:],
                     lambda r: AM.kst(r["entry_q"] * DISC.BAR_MIN))
            entry["rows"], entry["updated"] = rows, today
            new = fresh("후보 " + c["id"], mismatch)
            if new:
                warnings.append("후보 %s: 다시 계산한 값이 장부와 다른 거래 %d건 — 장부 값은 그대로 둠" % (c["id"], len(new)))
                hold_if_sampled(entry, new, mismatch, bad, {(r["market"], r["entry_q"]) for r in rows},      # 판정 전이라 지금까지의 거래가 전부 표본이다
                                always=[lab for lab in new if "뒤늦게 나타남" in lab])
            if not active_hold(entry) and not observe_only(entry):
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
    if cond_log is not None:                                        # 전진 조건 기록 — 판정·등록과 무관하다. 못 받으면 지난 값을 그대로 둔다
        try:
            ledger["conditions"] = cond_log(today, ledger.get("conditions"))
            log("조건 기록: %d건 (+%d)" % (ledger["conditions"]["events"], ledger["conditions"]["added"]))
        except Exception as e:
            warnings.append("조건 기록: 이번 실행에서 갱신하지 못함 — %s: %s" % (type(e).__name__, e))
    if fill_errors:
        warnings.append("체결 내역: %d건 조회 실패 — 비워 두고 다음 실행에서 다시 받음 (%s)" % (len(fill_errors), ", ".join(fill_errors[:5])))
    for _name, entry, minute, day, _past in staged_items(ledger):  # 2·3단계 점검: 시점이 처음 된 실행의 값으로 고정한다
        rows = entry.get("rows", [])
        for n, min_days in STAGE2_POINTS:
            if str(n) not in (entry.get("stage2") or {}):
                point = stage2_point(rows, n, min_days, minute, day)
                if point:
                    point["recorded"] = today
                    entry.setdefault("stage2", {})[str(n)] = point
        for d in STAGE3_POINTS:
            if str(d) not in (entry.get("stage3") or {}):
                point = stage3_point(rows, d, day)
                if point:
                    point["recorded"] = today
                    entry.setdefault("stage3", {})[str(d)] = point
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
        head = "%s 끝: %s (%s, 첫 %d건)" % (STAGE1, VERDICT[dec["verdict"]], dec["decided_on"], dec["trades"])
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
            out += ["%s 이후 포함 전체 %d건: 거래당 %s, 날짜 가중 %s. 선별 결과는 바뀌지 않습니다." % (STAGE1, now["n"], _pct(now["mean"]), _pct(now["day_mean"])), ""]
        else:
            out += ["아직 %s 전이라 위 숫자는 참고용입니다. 30건·10일이 처음 채워지는 거래까지로 한 번만 선별합니다(통과 = 계속 볼 가치가 있다는 뜻)." % STAGE1, ""]
        out += [_day_line([(_utc_day(t["entry_min"]), t["net"]) for t in rows]) + " " + _fill_count(rows), ""]
        out += ["최근 거래: " + " / ".join("%s %s 1시간 %s → %s" % (AM.kst(t["entry_min"]), t["market"][4:], _pct(t["drop"], 1), _pct(t["net"]))
                                       for t in rows[-3:]), ""]
    else:
        out += ["아직 끝난 거래가 없습니다. 통과해도 봉 기준이며, 급락 순간의 실제 호가 체결은 따로 확인해야 합니다.", ""]
    crv = ledger.get("CR_variants") or {}
    out += ["## 급락 반등 변형 — 숫자만 바꾼 3개 (판정 기준은 위와 같음)", "",
            "같은 4종목, 같은 비용. 서로 독립으로 셉니다. 추적 시작 2026-11-14 (UTC).", "",
            "| 변형 | 규칙 | 진행 | 거래당 평균 | 날짜 가중 | 일별 승률 | 일별 중앙값 | 체결 자료 | %s |" % STAGE1, "|---|---|---|---|---|---|---|---|---|"]
    for v in CR_VARIANTS:
        e = crv.get(v["key"]) or {"rows": []}
        rows, dec = e.get("rows", []), e.get("decision")
        st = ((dec or {}).get("stats") or AM.cr_summary([(t["market"], t) for t in rows])) if rows else None
        days, win, mid = day_stats([(_utc_day(t["entry_min"]), t["net"]) for t in rows])
        out.append("| %s | %s | %d / %d건, %d / %d일 | %s | %s | %s | %s | %s | %s |" % (
            v["name"], v["rule"], len(rows), AM.CR_MIN_N, days, AM.CR_MIN_DAYS, _pct(st["mean"]) if st else "-",
            _pct(st["day_mean"]) if st else "-", _rate(win), "-" if mid is None else _pct(mid), _fill_count(rows, short=True),
            (VERDICT[dec["verdict"]] if dec else "진행 중") + (HOLD_TAG if active_hold(e) else "")))
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
    out += ["## 자동 등록 후보 — 매월 1일에 탐색해 넘은 것만 등록 (매주 월요일은 점검만)", ""]
    cands = ledger.get("candidates") or {}
    if cands:
        out += ["| 번호 | 규칙 | 등록 | 진행 | 거래당 평균 | 일별 승률 | 일별 중앙값 | 체결 자료 | %s |" % STAGE1, "|---|---|---|---|---|---|---|---|---|"]
        for cid in sorted(cands):
            e = cands[cid]
            rows, dec = e.get("rows", []), e.get("decision")
            st = DISC.SS.cluster_stats(rows)
            verdict = VERDICT[dec["verdict"]] if dec else ("판정 없음" if observe_only(e) else "진행 중")
            if e.get("role") in ROLE_NOTE:
                verdict += " (%s)" % ROLE_NOTE[e["role"]]
            if e.get("delisted") and not (dec and dec["verdict"] == "INCOMPLETE"):
                verdict += " · 자료 불완전"
            if e.get("delisted"):
                verdict += "(%s)" % ", ".join("%s %s" % (m[4:], d) for m, d in sorted(e["delisted"].items()))
            if active_hold(e):
                verdict += HOLD_TAG
            _days, win, mid = day_stats([(t["day"], t["net"]) for t in rows])
            out.append("| %s | %s | %s | %d / %d건, %d / %d일 | %s | %s | %s | %s | %s |" % (
                cid, e.get("rule", "-"), e.get("registered", "-"), st["n"], DISC.FWD_MIN_N, st["days"], DISC.FWD_MIN_DAYS,
                "-" if st["net"] is None else _pct(st["net"]), _rate(win), "-" if mid is None else _pct(mid), _fill_count(rows, short=True), verdict))
        out += ["", "1단계 선별 통과는 '계속 볼 가치가 있다' 는 뜻이지 돈을 넣는 신호가 아닙니다. 선별은 봉 기준이고, 체결 내역 기반 추정과 2단계 점검은 아래에 있습니다. "
                "일별 승률 = 거래가 있었던 날 가운데 그날 평균이 0 보다 큰 날의 비율, 일별 중앙값 = 그날 평균들의 중앙값.", ""]
        for cid in sorted(cands):
            gone = cands[cid].get("delisted") or {}
            if gone:
                out += ["- %s 자료 불완전: %s. 이미 적힌 거래는 그대로 두고, 확인한 날부터는 이 종목 없이 남은 종목으로 채점합니다. "
                        "기한까지 표본을 못 채워도 '표본 부족 만료' 로 처리하지 않습니다." % (
                            cid, ", ".join("%s 가 %s 에 상장 목록에서 빠진 것을 확인" % (m[4:], d) for m, d in sorted(gone.items()))), ""]
    else:
        out += ["아직 등록된 후보가 없습니다. 탐색 결과는 DISCOVERY.md 에 남습니다.", ""]
    out += render_risk(ledger)
    out += render_fills(ledger)
    out += render_stage2(ledger)
    out += render_stage3(ledger)
    out += render_pairs(ledger)
    out += render_observation(ledger)
    out += render_conditions(ledger)
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


def _rate(v):
    return "-" if v is None else "%.0f%%" % (100 * v)


def _day_line(pairs):
    days, win, mid = day_stats(pairs)
    return "진입일 %d일, 일별 승률 %s, 일별 중앙값 %s. 거래가 급락일 몇 날에 몰리므로 진짜 표본 크기는 거래 수보다 이 날 수에 가깝습니다." % (
        days, _rate(win), "-" if mid is None else _pct(mid))


def _fill_count(rows, short=False):
    """'체결 자료 n / 전체 N건' (고문 판정 2026-11-20 4: 후보마다 반드시 같이 싣는다)."""
    n = sum(1 for t in rows if _status(t.get("fill")) in ("ok", NO_FILL))
    return ("%d / %d건" if short else "체결 자료 %d / 전체 %d건.") % (n, len(rows))


ROLE_NOTE = {"main": "주 대상", "compare": "비교용 — 돈에 관한 판단에 쓰지 않음", "observe": "체결 관찰 전용 — 판정 없음"}
SLOTS = 5                                                           # 가상 추적의 배분 관례: 자금 5칸, 신호마다 1칸, 동시 5종목까지 (고문 판정 2026-11-22 ③)


def slot_result(rows, slots=SLOTS):
    """배분 관례로 본 전진 성적 → (잡은 거래 수, 건너뛴 거래 수, 자금 대비 누적 수익). 진입 순서대로 한 칸씩 쓰고, 칸이 다 차 있으면 건너뛴다.
    같은 봉에 든 신호는 종목 이름 순(주 대상은 종목이 5개라 칸이 모자랄 일이 없다). 그 봉 시가에 청산되는 자리는 비었다고 본다. 복리 없음."""
    open_x, taken, skipped, total = [], 0, 0, 0.0
    for t in sorted(rows, key=lambda t: (t["entry_q"], t["market"])):
        open_x = [x for x in open_x if x > t["entry_q"]]
        if len(open_x) >= slots:
            skipped += 1
            continue
        open_x.append(t["exit_q"])
        taken, total = taken + 1, total + t["net"] / slots
    return taken, skipped, total


def render_risk(ledger):
    """현황의 '위험과 배분' 절 (고문 판정 2026-11-22 ③ 3). 등록부에 적어 둔 과거 위험 값과, 배분 관례로 본 전진 성적. 표시만 한다."""
    items = [(cid, e) for cid, e in sorted((ledger.get("candidates") or {}).items()) if e.get("risk")]
    if not items:
        return []
    out = ["## 위험과 배분 (표시만)", "",
           "이 규칙에는 손절도, 순간 폭락을 걸러 내는 조건도 없습니다(진입 시점에는 가장 좋았던 날과 구별되지 않습니다). 위험은 한 번에 넣는 금액으로 조절합니다. "
           "가상 추적의 배분 관례는 자금 %d칸, 신호마다 1칸, 동시 %d종목까지입니다. 실제 금액은 사용자가 따로 정합니다." % (SLOTS, SLOTS), "",
           "| 번호 | 과거: 신호 뒤 60분 안 최대 추가 하락 (중앙값 / 하위 10%% / 하위 1%%) | 과거 배분 모의 (최대 낙폭 / 최악의 하루) | 전진: 배분 관례 누적 (잡은 / 건너뛴) |",
           "|---|---|---|---|"]
    for cid, e in items:
        r = e["risk"]
        taken, skipped, total = slot_result(e.get("rows", []))
        out.append("| %s | %s / %s / %s (%s, %d건) | %s / %s (%s) | %s (%d / %d) |" % (
            cid, _pct(r["dd_median"]), _pct(r["dd_p10"]), _pct(r["dd_p01"]), r["dd_period"], r["dd_n"], _pct(r["sim_mdd"], 1), _pct(r["sim_worst_day"], 1), r["sim_period"],
            _pct(total) if taken else "-", taken, skipped))
    out.append("")
    for cid, e in items:
        worst = e["risk"].get("worst") or []
        if worst:
            out.append("- %s 과거에 가장 나빴던 사건(60분 안 최저 → 60분 뒤): %s" % (
                cid, ", ".join("%s %s → %s" % (w[0], _pct(w[1], 1), _pct(w[2], 1)) for w in worst)))
    return out + [""]


def observe_only(entry):
    """체결 관찰 전용 항목(고문 판정 2026-11-22 ② 4): 체결 추정만 쌓고 1·2·3단계 판정을 하지 않는다."""
    return entry.get("role") == "observe"


def staged_items(ledger):
    """2·3단계 점검을 하는 항목 = 체결 추정 항목에서 체결 관찰 전용을 뺀 것."""
    return [x for x in fill_items(ledger) if not observe_only(x[1])]


def fill_items(ledger):
    """체결 내역 기반 추정을 하는 항목 → [(이름, 장부 항목, 진입 시각(분) 함수, 진입일 함수, 과거 값)]. 고문 판정 2026-11-19: 급락 반등,
    변형 3종, 자동 등록 후보. 과거 값 = [(기간, 거래당 평균, 일별 평균 또는 None)] — 급락 반등 계열은 CR_PAST, 후보는 등록부에서 옮겨 적은 값."""
    out = []

    def cr_minute(t):
        return t["entry_min"]

    def cr_day(t):
        return _utc_day(t["entry_min"])
    if ledger.get("CR_rebound") is not None:
        out.append(("급락 반등", ledger["CR_rebound"], cr_minute, cr_day, CR_PAST["CR_rebound"]))
    for v in CR_VARIANTS:
        e = (ledger.get("CR_variants") or {}).get(v["key"])
        if e is not None:
            out.append(("급락 반등 변형 " + v["name"], e, cr_minute, cr_day, CR_PAST[v["key"]]))
    out += [("후보 " + cid, e, lambda t: t["entry_q"] * DISC.BAR_MIN, lambda t: t["day"], [tuple(p) for p in e.get("past") or []])
            for cid, e in sorted((ledger.get("candidates") or {}).items())]
    return out


def render_fills(ledger):
    """현황의 '체결 내역 기반 추정' 절. 1단계 선별은 봉 기준 그대로이고 이 절은 그 옆에 추정 값을 나란히 놓는다."""
    items = fill_items(ledger)
    if not items:
        return []

    def avg(vals):
        return _pct(sum(vals) / len(vals)) if vals else "-"
    out = ["## 체결 내역 기반 추정 — 1단계 선별은 등록된 대로 봉 기준", "",
           "봉 기준은 봉 시가에 사고 판 값(비용 편도 0.07~0.10%)입니다. 추정은 업비트 공개 체결 내역으로 다시 계산한 값이고, 우리 주문의 실제 "
           "체결은 아닙니다 — 그때의 호가 잔량과 진입 지연은 들어 있지 않고, 봉과의 차이에는 그 사이의 가격 움직임도 섞입니다.", "",
           "- 60초 추정: 진입 시각부터 60초 동안의 시장가 매수(BID) 평균가(가격×수량의 합 ÷ 수량의 합)에 사서, 청산 시각부터 60초 동안의 "
           "시장가 매도(ASK) 평균가에 판 값.",
           "- 주문 크기 추정: 진입 시각 뒤 첫 %s원어치 BID 체결의 평균가에 사서, 청산 시각 뒤 첫 %s원어치 ASK 체결의 평균가에 판 값. "
           "60초 안에 그만큼 차지 않으면 '자료 부족'." % (format(ORDER_KRW, ","), format(ORDER_KRW, ",")),
           "- 수수료 편도 0.05%. '60초 안 체결 자료 부족' 은 그 방향의 시장가 체결이 60초 동안 없었다는 뜻이지 주문이 불가능했다는 뜻은 "
           "아닙니다. 업비트는 7일 전까지의 체결만 주므로, 그보다 늦게 처음 채점된 거래는 '체결 자료 없음' 입니다.",
           "- 체결 차이 = 같은 거래의 봉 기준 − 추정(주문 크기 값이 있으면 그것, 없으면 60초 값; + 면 실제가 나쁘다). 상한은 단측 97.5%%. "
           "가격 차이(같은 수수료로 본 등락의 차이)와 수수료 차이(봉 기준 비용 편도 0.07~0.10%% 와 실제 수수료 0.05%% 의 차이)로 나눠 보입니다. "
           "60초 체결 금액 = 거래마다 진입 60초 매수 체결과 청산 60초 매도 체결 중 작은 쪽, 그 중앙값. "
           "양쪽 주문×%d 이상 = 진입 매수와 청산 매도 양쪽에 주문 금액의 %d배 이상 체결이 있었던 거래." % (STAGE2_CAPACITY_X, STAGE2_CAPACITY_X),
           "- 추정이 나온 거래만의 평균은 체결이 어려운 거래가 빠져 좋아 보일 수 있습니다. 건수를 같이 보세요. 원본 체결은 forward/ticks/ 에 있습니다.", "",
           "| 항목 | 체결 자료 / 전체 | 봉 기준 평균 | 60초 추정: 봉 → 추정 | 주문 크기 추정: 봉 → 추정 | 체결 차이 (97.5%% 상한) = 가격 + 수수료 | 매수·매도가 − 봉 시가 | 60초 안 자료 부족 | 주문 크기 자료 부족 | 양쪽 주문×%d 이상 | 60초 체결 금액 중앙값 |" % STAGE2_CAPACITY_X,
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    recent = []
    for name, entry, minute, _day, _past in items:
        rows = entry.get("rows", [])
        fs = fill_stats(rows)
        ok = [t for t in rows if _status(t.get("fill")) == "ok"]
        od = [t for t in rows if (t.get("fill") or {}).get("order_status") == "ok"]
        out.append("| %s | %d / %d건%s | %s | %s → %s (%d건) | %s → %s (%d건) | %s (%s) = %s + %s | %s / %s | %s | %s | %s | %s |" % (
            name, fs["tried"], len(rows), " (자료 없음 %d)" % fs["nodata"] if fs["nodata"] else "", avg([t["net"] for t in rows]),
            avg([t["net"] for t in ok]), avg([t["fill"]["net"] for t in ok]), len(ok),
            avg([t["net"] for t in od]), avg([t["fill"]["order_net"] for t in od]), len(od),
            "-" if fs["gap"] is None else _pct(fs["gap"]), "-" if fs["gap_up975"] is None else _pct(fs["gap_up975"]),
            "-" if fs["price"] is None else _pct(fs["price"]), "-" if fs["fee"] is None else _pct(fs["fee"]),
            avg([t["fill"]["order_buy_vs_open"] for t in od if t["fill"].get("order_buy_vs_open") is not None]),
            avg([t["fill"]["order_sell_vs_open"] for t in od if t["fill"].get("order_sell_vs_open") is not None]),
            "-" if not fs["tried"] else "%d / %d" % (fs["nofill"], fs["tried"]),
            "-" if not fs["tried"] else "%d / %d" % (fs["tried"] - fs["order_ok"], fs["tried"]),
            "-" if not fs["tried"] else "%d / %d" % (fs["cap_ok"], fs["tried"]),
            "-" if fs["depth"] is None else format(int(fs["depth"]), ",") + "원"))
        recent += [(minute(t), name, t) for t in rows if t.get("fill")]
    out.append("")
    if recent:
        out += ["최근 거래의 추정:", ""]
        for m, name, t in sorted(recent, key=lambda x: (x[0], x[1]))[-8:]:
            f = t["fill"]
            head = "- %s %s %s: 봉 %s → " % (name, t["market"][4:], AM.kst(m), _pct(t["net"]))
            money = ("60초 체결 금액: 진입 매수 %s원·매도 %s원, 청산 매수 %s원·매도 %s원" % tuple(
                format(f[k], ",") for k in ("entry_bid_krw", "entry_ask_krw", "exit_bid_krw", "exit_ask_krw"))) if "entry_bid_krw" in f else ""
            if _status(f) == "ok":
                order = ("주문 크기 %s (매수가 %s, 매도가 %s)" % (_pct(f["order_net"]), "-" if f.get("order_buy_vs_open") is None else _pct(f["order_buy_vs_open"]),
                         "-" if f.get("order_sell_vs_open") is None else _pct(f["order_sell_vs_open"]))) if f.get("order_status") == "ok" else \
                    "주문 크기 %s" % (f.get("order_status") or "아직")
                out.append(head + "60초 %s, %s / %s" % (_pct(f["net"]), order, money))
            else:
                out.append(head + _status(f) + (" (%s)" % (money or f.get("why", "")) if (money or f.get("why")) else ""))
        out.append("")
    return out


def render_stage2(ledger):
    """현황의 '2단계 점검 — 소액 시험 자격' 절 (고문 판정 2026-11-20 ②). 표시만 한다 — 이 절이 무엇을 결정하지는 않는다.
    점검 값은 시점이 처음 된 날 장부에 고정된 것을 그대로 쓴다. 과거 값과의 비교만 여기서 한다."""
    items = staged_items(ledger)
    if not items:
        return []

    def mark(label, r):
        return "%s %s %s" % (label, "충족" if r[0] else "미달" if r[0] is False else "보류" if "보류" in r[1] else "판단 못 함",
                             r[1].replace("판단 보류 — ", ""))

    def cell(entry, n, min_days, tried, days, past):
        point = (entry.get("stage2") or {}).get(str(n))
        if not point:
            return "점검 전 (체결 자료 %d / %d건, 진입일 %d / %d일)" % (tried, n, days, min_days)
        return "%s 기록 (체결 자료 %d건·진입일 %d일, 그때까지 신호 %d건 중 자료 없음 %d건): %s" % (
            point["recorded"], point["k"], point["sample_days"], point["signals"], point["nodata"],
            " · ".join(mark(lab, r) for lab, r in zip("②③④", stage2_cells(point, past))))
    out = ["## 2단계 점검 — 소액 시험 자격 (증명 아님. 표시만, 자동 결정 없음. 돈은 고문과 사용자가 정한다)", "",
           "점검은 두 시점에만 하고 그때의 값을 고정해 둡니다(다시 계산해 덮어쓰지 않습니다): %s. "
           "① 1단계 선별 통과  ② 과거 거래당 평균(두 시기 중 낮은 쪽) − 체결 차이의 97.5%% 상한 > 0  "
           "③ 주문 금액 %s원 × %d 이상의 체결이 진입 매수와 청산 매도 양쪽에 다 있었던 거래 ≥ %d%% (분모 = 체결 자료가 있는 거래, 자료 부족은 미달)  "
           "④ 붕괴 점검 — 전진 진입일 %d일 미만이면 보류, 그 뒤로는 전진 일별 평균의 95%% 상한이 과거 일별 평균(낮은 쪽)의 %d%% 보다 낮으면 "
           "붕괴 징후, 아니면 '붕괴 증거 없음(증명 아님)'." % (
               ", ".join("체결 자료 %d건과 진입일 %d일이 처음 함께 된 때" % p for p in STAGE2_POINTS), format(ORDER_KRW, ","), STAGE2_CAPACITY_X,
               round(100 * STAGE2_CAPACITY_MIN), STAGE2_HOLD_DAYS, round(100 * STAGE2_COLLAPSE)), "",
           "| 항목 | 체결 자료 / 전체 | ① 1단계 선별 (지금) | %s |" % " | ".join("%d건·%d일 점검" % p for p in STAGE2_POINTS),
           "|---|---|---|" + "---|" * len(STAGE2_POINTS)]
    for name, entry, _minute, day, past in items:
        rows = entry.get("rows", [])
        tried = [t for t in rows if _status(t.get("fill")) in ("ok", NO_FILL)]
        days = len({day(t) for t in tried})
        v = (entry.get("decision") or {}).get("verdict")
        out.append("| %s | %d / %d건 | %s | %s |" % (name, len(tried), len(rows), VERDICT[v] if v else "진행 중",
                                                   " | ".join(cell(entry, n, d, len(tried), days, past) for n, d in STAGE2_POINTS)))
    out += ["", "과거 값(고문 집계, 봉 기준, 비용 뒤): 급락 반등 계열은 2017–20·2021–26, 후보는 등록 때 적힌 앞·뒤 구간. "
            "체결 차이를 과거 우위에서 빼도 되는 까닭: 과거 쪽도 같은 봉 기준 비용을 썼으므로 비용 가정의 차이가 상쇄됩니다.", ""]
    return out


def render_stage3(ledger):
    """현황의 '3단계 점검 — 증액 기준' 절 (고문 판정 2026-11-20 ②). 전진 일별 평균의 99% 하한 > 0. 표시만 한다."""
    items = staged_items(ledger)
    if not items:
        return []
    out = ["## 3단계 점검 — 증액 기준 (표시만)", "",
           "전진 일별 평균의 99%% 하한 > 0. 점검은 전진 진입일 %s일이 처음 된 때만 하고, 처음 그만큼의 날로 낸 값을 고정합니다. "
           "전진은 지금 가상 거래(봉 기준)뿐이고, 실거래가 생기면 함께 셉니다." % "·".join(str(d) for d in STAGE3_POINTS), "",
           "| 항목 | 전진 진입일 | %s |" % " | ".join("%d일 점검" % d for d in STAGE3_POINTS), "|---|---|" + "---|" * len(STAGE3_POINTS)]
    for name, entry, _minute, day, _past in items:
        rows = entry.get("rows", [])
        n_days = len({day(t) for t in rows})
        cells = []
        for d in STAGE3_POINTS:
            p = (entry.get("stage3") or {}).get(str(d))
            cells.append("점검 전 (%d / %d일)" % (n_days, d) if not p else "%s 기록: 일별 평균 %s, 99%% 하한 %s → %s (%s 까지 %d건)" % (
                p["recorded"], _pct(p["daymean"]), _pct(p["low99"]), "충족" if p["low99"] > 0 else "미달", p["through_day"], p["trades"]))
        out.append("| %s | %d일 | %s |" % (name, n_days, " | ".join(cells)))
    out.append("")
    return out


def render_pairs(ledger):
    """현황의 '짝 관찰' 절 (고문 판정 2026-11-20 ② 7). 판정 없음."""
    rows_by = [(cid, [t for t in e.get("rows", []) if t.get("pair")]) for cid, e in sorted((ledger.get("candidates") or {}).items())]
    rows_by = [(cid, rows) for cid, rows in rows_by if rows]
    if not rows_by:
        return []

    def avg(vals):
        return "%s (%d건)" % (_pct(sum(vals) / len(vals)), len(vals)) if vals else "-"
    out = ["## 짝 관찰 — 같은 시각에 BTC·ETH 를 샀다면 (판정 없음)", "",
           "자동 등록 후보의 거래마다 같은 진입·청산 봉 시가로 BTC·ETH 를 샀을 때의 봉 기준 수익(비용 편도 0.07%%)입니다. "
           "신호 종목의 수익이 시장 전체의 반등과 얼마나 다른지 보려는 것입니다.", "",
           "| 항목 | 신호 종목 (봉 기준) | BTC | ETH |", "|---|---|---|---|"]
    for cid, rows in rows_by:
        out.append("| 후보 %s | %s | %s | %s |" % (cid, avg([t["net"] for t in rows]), avg([t["pair"]["KRW-BTC"] for t in rows if "KRW-BTC" in t["pair"]]),
                                               avg([t["pair"]["KRW-ETH"] for t in rows if "KRW-ETH" in t["pair"]])))
    out.append("")
    return out


def render_conditions(ledger):
    """현황의 '조건별 전진 표' 절 (고문 판정 2026-11-22 ⑤ 4). 관찰용 — 표시만 한다."""
    cond = ledger.get("conditions")
    if not cond:
        return []
    out = ["## 조건별 전진 표 — %s" % OBS_TAG, "",
           "사용자 설계(급락 반등 검토안 1~8번)의 조건을 새 자료에서도 봅니다. 고정 20종목의 15분 −3%% 급락 전부(거래대금 조건 없음)와 BTC 직접 사건을 "
           "%s 00:00 UTC 부터 사건마다 %s 에 적고(과거 9년 파일과 같은 정의·같은 열), 그 줄들을 조건별로 묶은 표입니다. "
           "지금까지 %d건, 자료 반영 %s%s." % (cond["since"], COND_FILE, cond["events"], cond["through"],
                                    " (상장 목록에 없어 뺀 종목: %s)" % ", ".join(m[4:] for m in cond["missing"]) if cond.get("missing") else ""),
           "5·15·30·60분 = 신호 다음 15분봉의 첫 체결가에 사서 그 시점 5분봉 종가에 판 값(비용 뒤, 같은 시각의 사건을 먼저 평균하고 날마다 평균한 값). "
           "최대 반등·최대 추가 하락 = 신호 뒤 60분 안의 값의 중앙값(비용 빼기 전 — 그 값에 팔 수 있었다는 뜻이 아닙니다). "
           "구간 경계는 과거 보고서 값으로 고정했습니다. 날 수가 %d일 미만인 칸은 '표본 부족' 으로 두고 값을 싣지 않습니다. "
           "칸마다 표본이 작아 몇 달은 참고용입니다. 1분 급등은 넣지 않습니다(과거 1년 비용 뒤 마이너스로 종료)." % CL.MIN_DAYS, "",
           "| 조건 | 사건 | 날 | 5분 | 15분 | 30분 | 60분 | 최대 반등 | 최대 추가 하락 | 과거 (%s): 사건 / 날 · 60분 |" % ((cond.get("past") or {}).get("period") or "-"),
           "|---|---|---|---|---|---|---|---|---|---|"]
    cells = (cond.get("past") or {}).get("cells") or {}
    for c in cond["table"]:
        if c["ok"]:
            vals = " | ".join("-" if v is None else _pct(v) for v in list(c["means"]) + [c["up"], c["down"]])
        else:
            vals = "표본 부족 | - | - | - | - | -"
        old = cells.get(c.get("key"))
        out.append("| %s | %d | %d | %s | %s |" % (c["label"], c["n"], c["days"], vals,
                                                 "-" if not old else "%d / %d · %s" % (old[0], old[1], "-" if old[2] is None else _pct(old[2]))))
    return out + ["과거 값은 같은 코드를 2017-09 부터의 15분봉·5분봉에 돌린 것입니다(이미 여러 번 본 자료라 새 자료와 같은 무게로 읽지 않습니다).", ""]


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
        for name, label in OBS_EVENTS:                              # 지표 조건은 없지만 거래대금 3배는 걸려 있다 — 그대로 적는다
            out.append("| %s 뒤 1시간 보유 (거래대금 %.0f배) |%s" % (label, DISC.VOL_X, cells([obs["events"][name][str(n)] for n in OBS_DAYS])))
            if obs.get("events0"):
                out.append("| %s 뒤 1시간 보유 (거래대금 조건 없음) |%s" % (label, cells([obs["events0"][name][str(n)] for n in OBS_DAYS])))
    for name, pairs, updated in observed_items(ledger):
        if not updated:                                             # 한 번도 채점되지 못한 항목을 '0건' 으로 보이지 않게 한다
            out.append("| %s (아직 채점 전) |%s" % (name, " - | - |" * len(OBS_DAYS)))
            continue
        stamp = "" if updated == today else " (자료 반영 %s)" % updated
        out.append("| %s%s |%s" % (name, stamp, cells([window_stats(pairs, today, n) for n in OBS_DAYS])))
    out.append("")
    if obs:
        ref = ", ".join("%s %s(%s건)" % (label, _pct(DISC.ADVISOR_REF[(name, DISC.BARS_H)][1][1], 3), format(DISC.ADVISOR_REF[(name, DISC.BARS_H)][1][0], ","))
                        for name, label in OBS_EVENTS)
        scope = "고문의 고정 20종목"
        if obs.get("missing"):
            scope += " 가운데 %d종목(상장 목록에 없어 뺀 종목: %s)" % (obs["markets"], ", ".join(m[4:] for m in obs["missing"]))
        out += ["기본 사건(위 %d줄): %s의 15분봉. 사건 봉 다음 봉 시가에 사서 1시간 뒤 시가에 판 것으로 계산합니다. 자료 반영 %s. "
                "참고로 같은 20종목의 2025-01 ~ 2026-11 평균(거래대금 %.0f배 기준)은 %s 입니다(고문 집계)." % (
                    len(OBS_EVENTS) * (2 if obs.get("events0") else 1), scope, obs["as_of"], DISC.VOL_X, ref), ""]
    else:
        out += ["기본 사건(급등·돌파·급락)은 아직 계산된 값이 없습니다.", ""]
    out += ["등록 후보는 추적을 시작한 뒤의 거래만 장부에 있습니다. 시작한 지 28일이 안 된 항목의 28일 칸은 시작일부터의 값입니다. "
            "'자료 반영' 날짜가 붙은 항목은 그날까지의 값입니다(H2 는 주 1회 반영).", ""]
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
            lines.append("급락 반등: %s %s (%s)" % (STAGE1, VERDICT[dec["verdict"]], dec["decided_on"]))
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
        lines.append("자동 등록 후보: %d개 추적, %s 끝 %d개%s" % (len(cands), STAGE1, len(done), " (통과: %s)" % ", ".join(sorted(passed)) if passed else ""))
        for c in sorted(cands):
            if cands[c].get("delisted"):
                lines.append("  %s 자료 불완전: %s 상장폐지 (남은 종목으로 계속 채점)" % (c, ", ".join(m[4:] for m in sorted(cands[c]["delisted"]))))
    held = holds(ledger)
    if held:
        lines.append("판정 보류(값 차이): " + ", ".join(name for name, _h in held))
    items = (ledger.get("new") or {}).get("items") or []           # 하루 한 메시지: 새로 끝난 거래가 있으면 종목과 수익률, 없으면 위의 진행 숫자만
    if items:
        lines.append("새로 끝난 거래 %d건:" % len(items))
        lines += ["  %s %s %s → %s%s" % (x["item"], x["market"], x["when"], _pct(x["net"]), _fill_note(x.get("fill"), x.get("order"))) for x in items[:NEW_SHOW]]
        if len(items) > NEW_SHOW:
            lines.append("  외 %d건" % (len(items) - NEW_SHOW))
    else:
        lines.append("새로 끝난 거래 없음")
    for w in ledger.get("warnings", []):
        lines.append("경고: " + w)
    return "\n".join(lines)


def _fill_note(f, order=None):
    """요약 한 줄에 붙이는 체결 내역 기반 추정. 숫자면 추정 순수익, 글자면 그 사유(60초 안 체결 자료 부족 등)."""
    def show(v):
        return _pct(v) if isinstance(v, (int, float)) else LEGACY_STATUS.get(v, v)
    if f is None:
        return ""
    return " (추정 60초 %s%s)" % (show(f), "" if order is None else ", %s원 %s" % (format(ORDER_KRW, ","), show(order)))


def expected_runs(today, daily=False):
    """오늘보다 앞선 가장 최근 예약일 → (주간 월요일, 월간 1일). 아직 첫 예약일 전이면 그 자리는 None.
    오늘이 예약일이면 오늘 것은 아직 기다리지 않는다(지난번 것만 본다)."""
    d = dt.date.fromisoformat(today)
    monday = (d - dt.timedelta(days=d.weekday() or 7)).isoformat()
    if daily and today > "2026-11-17":
        monday = (d - dt.timedelta(days=1)).isoformat()
    first = (d.replace(day=1) if d.day > 1 else (d - dt.timedelta(days=1)).replace(day=1)).isoformat()
    return (monday if monday >= FIRST_WEEKLY else None), (first if first >= FIRST_MONTHLY else None)


def expected_check(today):
    """오늘보다 앞선 가장 최근 월요일 — 주간 탐색 점검(등록 없음, forward/weekly_discovery)의 예약일. 첫 예약일 전이면 None.
    오늘이 월요일이면 오늘 것은 아직 기다리지 않는다."""
    d = dt.date.fromisoformat(today)
    monday = (d - dt.timedelta(days=d.weekday() or 7)).isoformat()
    return monday if monday >= FIRST_SEARCH_WEEKLY else None


def stale(ledger, scan_log, today, registry=(), daily=False, check_log=None):
    """예약 실행이 빠졌거나 일부 항목이 갱신되지 못했는지 → 문제 목록(없으면 빈 목록).
    '며칠 지났나' 가 아니라 '가장 최근 예약일의 실행이 기록에 있나' 를 본다. last_run 은 일부 항목이 실패해도 찍히므로,
    항목마다 따로 남는 갱신일(성공했을 때만 바뀜)도 같이 본다.
    매일 일정(daily)에서는 어제 실행을 기다린다. 김치 프리미엄(H2)만은 주 1회 반영이라 지난 월요일을 기다린다.
    탐색: 월간 등록 실행은 scan_log(공식 기록)에서, 매일 일정이면 주간 점검 실행도 check_log(weekly_discovery 기록)에서 본다."""
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
                # 전진 시작일이 기다리는 실행일 이전인 후보만 본다. 자동 등록 후보는 등록 다음 날이 시작일이라, 등록한 날의 추적 실행
                # (등록보다 먼저 돌았을 수 있다)은 기다리지 않는다
                if c.get("status") == "ACTIVE" and not done and DISC.cand_start(c) <= weekly:
                    items.append(("후보 " + c["id"], (cands.get(c["id"]) or {}).get("updated")))
            for name, day in items:
                if not day or day < weekly:
                    out.append("%s: %s%s 이후 갱신되지 못함 — 마지막 갱신 %s" % (name, weekly, suffix, day or "없음"))
    if monthly:
        run = scan_log[-1].get("run") if scan_log else None
        if not run or run < monthly:
            out.append("월간 탐색: %s 예약 실행이 기록에 없음 — 마지막 실행 %s" % (monthly, run or "없음"))
    check = expected_check(today) if daily else None
    if check:
        run = check_log[-1].get("run") if check_log else None
        if not run or run < check:
            out.append("주간 탐색 점검: %s(월) 예약 실행이 기록에 없음 — 마지막 실행 %s" % (check, run or "없음"))
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
        log_path, reg_file, check_path = out / "scan_log.json", out / "registry.json", out / "weekly_discovery" / "scan_log.json"

        def read(p):
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        problems = stale(ledger, read(log_path), today, read(reg_file), daily=a.daily_schedule, check_log=read(check_path))
        weekly, monthly = expected_runs(today, daily=a.daily_schedule)
        print("\n".join(problems) if problems else "예약 실행 기록 정상 (추적 %s 이후 실행 %s, 월간 탐색 기준일 %s, 주간 점검 기준일 %s)" % (
            weekly or "첫 예약 전", ledger.get("last_run"), monthly or "첫 예약 전", (expected_check(today) if a.daily_schedule else None) or "-"))
        return 1 if problems else 0
    reg_path = out / "registry.json"
    registry = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else []
    ledger, warnings = update(ledger, today, log=lambda s: print(s, flush=True), registry=registry, observer=observe, fill_get=AM.http_get,
                              tick_dir=None if a.dry_run else out / TICKS_DIR,
                              cond_log=lambda day, prev: conditions(day, prev, path=out / COND_FILE, write=not a.dry_run, past_path=out / COND_PAST))
    text = render(ledger)
    if not a.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        (out / "STATUS.md").write_text(text, encoding="utf-8")
    print(text)
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
