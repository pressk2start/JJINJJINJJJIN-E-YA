"""Upbit 캔들 데이터 수집.

목적: 3~6개월치 1분 캔들을 다운로드하여 CLM 진입 후보를 오프라인에서 재생.

사용법:
    python 01_collect.py --market KRW-BTC --days 180 --interval 1m

TODO:
- [ ] Upbit API 인증 (public 캔들은 인증 불필요)
- [ ] 200개 제한 대응 페이지네이션
- [ ] rate limit 대응 (초당 10회)
- [ ] parquet 저장
- [ ] 진행률 표시
- [ ] 재시작 가능 (부분 다운로드 이어받기)

주의:
- Upbit 무료 캔들 API: /v1/candles/minutes/{unit}
- unit: 1, 3, 5, 15, 10, 30, 60, 240
- to: 마지막 캔들 시각 (ISO8601)
- count: 최대 200개
"""

import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path


UPBIT_CANDLE_URL = "https://api.upbit.com/v1/candles/minutes/{unit}"
RATE_LIMIT_SEC = 0.1  # 초당 10회
DATA_DIR = Path(__file__).parent / "data" / "candles_1m"


def fetch_candles(market: str, unit: int, to: str, count: int = 200):
    """Upbit 캔들 API 호출. 최대 200개.

    TODO: 실제 requests 호출 구현. 지금은 스켈레톤.
    """
    raise NotImplementedError("Upbit API 호출 구현 필요")


def collect(market: str, days: int, unit: int = 1):
    """market의 최근 days일간 캔들 수집.

    페이지네이션으로 200개씩 역방향 조회.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=days)

    print(f"수집: {market} {start_time} ~ {end_time} ({days}일)")
    print(f"저장: {DATA_DIR}/{market}_{unit}m.parquet")

    # TODO: 실제 수집 루프
    # to = end_time
    # all_candles = []
    # while to > start_time:
    #     batch = fetch_candles(market, unit, to.isoformat(), count=200)
    #     if not batch:
    #         break
    #     all_candles.extend(batch)
    #     to = min(c["candle_date_time_utc"] for c in batch)
    #     time.sleep(RATE_LIMIT_SEC)
    # df = pd.DataFrame(all_candles)
    # df.to_parquet(DATA_DIR / f"{market}_{unit}m.parquet")

    raise NotImplementedError("스켈레톤. 실제 수집 구현 필요.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, help="KRW-BTC 등")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--interval", default="1m", choices=["1m", "5m", "15m"])
    args = parser.parse_args()
    unit = int(args.interval.rstrip("m"))
    collect(args.market, args.days, unit)
