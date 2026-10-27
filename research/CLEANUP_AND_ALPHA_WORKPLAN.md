# 코드 정리와 독립 alpha 연구 실행 범위

기준: main 07932c524f7f84bed1bbbb9bf0f117c41c3fbace. 서버 실행 버전은 미확인.
이 문서는 실행 계획이며 F2 사전등록을 대체하지 않는다.

## 확인한 현재 상태

bot.py는 23,662줄이다. A_CLEAN과 A×A2는 enabled=False지만 shadow_enabled=True다.
따라서 실주문 경로는 비활성이고 shadow 평가 경로에서는 계속 실행된다.
REJECTED_FINAL/CLOSED와 TERMINATED_INFEASIBLE은 판정 상태이며 실행 중단을 의미하지 않는다.
일반 shadow 평가 루프는 enabled=False와 shadow_enabled=False인 경로를 건너뛴다.
A/A2는 CONTROL과 paired summary, export, 저장 통계를 공유하므로 이름만 검색해 삭제하면 안 된다.

## 정리 순서와 완료 기준

1. 종료 실험 결과 보존
   - 서버의 원본 export와 상태 파일을 별도 보존하고 hash, 실행 commit, epoch, route를 기록한다.
   - 서버 접근 전에는 보존 완료라고 표시하지 않는다.
2. 종료 실험의 신규 생성 중단
   - 먼저 두 route의 신규 shadow 생성을 중단하는 작은 변경을 준비한다.
   - 이미 열린 가상 포지션은 기존 조건으로 종료시킨다. LIVE 청산은 계속 유지한다.
   - 신규 A/A2 VP가 생성되지 않고, 기존 VP 종료와 CONTROL/실주문 행동이 유지되는지 검증한다.
   - 봉인된 결과는 archival snapshot에서 보고하며 표본 0을 새 판정으로 해석하지 않는다.
3. archival report와 의존성 분리
   - A/A2 전용 audit/report를 live report에서 분리한다.
   - lifetime_n/detail_window_n/metric_source를 노출한다.
   - divergence 감소는 이전/현재 pair ID와 계산 경로를 비교해 해결한다. cap-pop을 확정 원인으로 가정하지 않는다.
4. 사용하지 않는 코드 제거
   - registry, check_fn, exit profile, persistence loader, export consumer, tests의 참조를 모두 확인한다.
   - LIVE와 연구가 공유하는 함수는 삭제하지 않는다. 증거와 이력은 git/archival artifact에 보존한다.
5. LIVE와 연구의 모듈 경계 마련
   - 주문/청산/계좌상태와 shadow/research/report를 단계적으로 분리한다.
   - 기준 입력에서 주문 의사결정, 수량, 청산 이유가 이전과 같은지 비교한다.
   - 이전 상태 파일 로딩과 잔여 포지션 청산 호환성을 확인한다.
   - 파일 분리 자체와 전략 파라미터 변경을 같은 PR에 섞지 않는다.

실서버 변경은 보존·검증·롤백 절차가 구체화된 변경으로 진행한다.
줄 수 감소와 scan 속도 개선은 실제 측정 전 성과로 주장하지 않는다.

## 병행 연구: 기존 봇에 후보를 계속 붙이지 않기

- C2: frozen 입력 계약을 확인한 실제 RUN 및 artifact 보존.
- P1: 고유 TRADE_CLOSED로 완료 거래 수 확인; schema/unit, fill/fee 회계, shadow pairing을 각각 검증.
- F2: 기존 DRAFT의 미결 계약값을 확정; F2a 필터가치/F2b 청산진단/F2c 빈번한 작은 순수익을 각각 판정.
- 새 독립 가설은 동시에 최대 2개만 진행한다. 세 번째는 기존 가설의 판정 후 시작한다.

다음 두 가설은 초안 후보이며 edge나 사전등록 완료를 뜻하지 않는다.

H1: 유동성 높은 종목에서 거래대금과 aggressive buy/sell 불균형이 일시적 snapshot이 아니라 지속될 때,
실제 매수·매도 비용 이후 단기 continuation이 남는가?
필요 데이터: 수신시각/거래소시각이 있는 체결·호가 이력. 결정 시점 이후 정보는 특징에 금지.

H2: 시장 공통 움직임을 제거한 종목 상대강도가 이후에도 지속되는가?
필요 데이터: 동시점 종목/BTC 또는 사전 고정 시장 basket, point-in-time universe.
동기화와 유동성 조건을 고정하고 시간순 untouched 평가를 한다.

각 가설은 경제적 이유, 데이터 가용성, 이벤트 정의, 후보 예산, 비용, 평가 기간,
채택/종료 조건을 먼저 작성한다. 부족한 데이터는 DATA_INSUFFICIENT이며 ZERO가 아니다.
같은 OOS를 여러 가설에서 재사용했는지 기록하고 연구 전체 탐색 이력을 보존한다.
새 가설은 독립 research 경로에 두고 LIVE registry에는 검증 전 추가하지 않는다.

## 진행 보고 방식

완료한 artifact/commit, 검증 결과, 미결 항목만 보고한다.
기존 봉인 문구를 반복하는 것을 작업 완료로 세지 않는다.
실제 자본 수익은 포트폴리오 제약과 fill/fee를 반영해 평가하며 이벤트 평균을 계좌 수익으로 표현하지 않는다.
