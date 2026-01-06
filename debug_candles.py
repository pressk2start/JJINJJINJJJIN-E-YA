# -*- coding: utf-8 -*-
import requests
from datetime import datetime, timedelta

def get_candles(market, to_time, count=40):
    url = "https://api.upbit.com/v1/candles/minutes/1"
    params = {"market": f"KRW-{market}", "to": to_time, "count": count}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"Error: {resp.status_code}")
            return []
    except Exception as e:
        print(f"Error: {e}")
        return []

# 테스트: TOSHI @ 2026-01-06 10:09
ticker = "TOSHI"
date_str = "2026-01-06"
time_str = "10:09"

dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
to_time = (dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")

print(f"조회: KRW-{ticker}, to={to_time}, count=40")
candles = get_candles(ticker, to_time, 40)

if candles:
    print(f"\n받은 캔들 수: {len(candles)}")
    print("\n최근 5개 캔들 시간:")
    for c in candles[:5]:
        print(f"  {c['candle_date_time_kst']}")
    print("\n가장 오래된 5개 캔들 시간:")
    for c in candles[-5:]:
        print(f"  {c['candle_date_time_kst']}")
else:
    print("캔들 데이터 없음")
