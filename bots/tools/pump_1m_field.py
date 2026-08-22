# -*- coding: utf-8 -*-
"""
B) 라이브 봇에 붙여넣을 pump_1m 계산 함수.

목적: 진입 순간, 봇이 이미 들고 있는 price_history 만으로
      "진입 직전 완성 1분봉 상승률(%)"을 계산해 로그에 남긴다.
      → enrich_clm_csv_with_pump.py(A)와 '완전히 동일한 정의'라
        기존 세션(A) + 향후 세션(B)을 같은 잣대로 비교 가능.

정의: 진입 시각 now 기준, 직전 '완성된' 1분 구간 [floor(now)-60s, floor(now))
      의 (마지막가격 - 첫가격)/첫가격 * 100.
      (진입 순간엔 현재 분이 아직 안 끝났으므로 '직전 완성분'을 쓴다 = 미래정보 없음)

price_history 형식: 시간오름차순 리스트. 아래 둘 중 뭐든 지원.
  - [(epoch_ms:int, price:float), ...]
  - [{'ts':epoch_ms, 'price':float}, ...]  (키 이름은 아래 TS_KEY/PX_KEY로 조정)
"""
TS_KEY, PX_KEY = "ts", "price"   # dict 형식일 때 키 이름

def _pair(x):
    if isinstance(x, (tuple, list)):
        return int(x[0]), float(x[1])
    return int(x[TS_KEY]), float(x[PX_KEY])

def compute_pump_1m(price_history, now_ms):
    """직전 완성 1분봉 상승률(%). 데이터 부족하면 None."""
    if not price_history:
        return None
    minute = (now_ms // 60000) * 60000          # 현재 분 시작(ms)
    lo, hi = minute - 60000, minute             # 직전 완성 분 [lo, hi)
    seg = []
    for x in price_history:
        ts, px = _pair(x)
        if lo <= ts < hi:
            seg.append((ts, px))
    if len(seg) < 2:
        return None
    seg.sort(key=lambda p: p[0])
    o, c = seg[0][1], seg[-1][1]
    if o <= 0:
        return None
    return round((c - o) / o * 100, 3)

def compute_pump_60s_rolling(price_history, now_ms):
    """참고용: 진입 직전 60초 롤링 상승률(%). 캔들정렬 아님.
       A와의 정합성은 compute_pump_1m 쪽을 쓸 것. 이건 보조 지표."""
    if not price_history:
        return None
    px_now = px_then = None
    for x in price_history:
        ts, px = _pair(x)
        if ts <= now_ms:
            px_now = px
        if ts <= now_ms - 60000:
            px_then = px
    if not px_now or not px_then or px_then <= 0:
        return None
    return round((px_now - px_then) / px_then * 100, 3)


# ────────────────── 봇에 넣는 법 ──────────────────
# 1) 진입이 확정되는 지점에서 아래처럼 호출해 로그 컬럼에 추가:
#
#     from pump_1m_field import compute_pump_1m
#     pump_1m = compute_pump_1m(self.price_history[market], entry_ts_ms)
#     row["pump_1m"] = pump_1m            # CSV/DB에 저장
#
# 2) 이후 세션 CSV에는 pump_1m 컬럼이 이미 있으므로,
#    A 스크립트 없이 바로 버킷별 성과 분해 가능.
#
# 3) price_history가 tick이 아니라 1분봉 리스트라면 더 간단:
#      직전 완성봉 candle 에 대해 (candle.close - candle.open)/candle.open*100
#
# ※ now_ms(=entry_ts_ms)는 반드시 UTC epoch millis 로 넘길 것.
#   봇이 KST datetime을 쓰면: int(kst_dt.timestamp()*1000) (tz-aware면 자동 UTC 변환)


if __name__ == "__main__":
    # 자체 테스트: 12:00:00~12:00:59 사이 100->103 (=+3%) 상승, 진입은 12:01:20
    base = 1_700_000_000_000
    minute0 = (base // 60000) * 60000
    hist = []
    for s in range(60):                       # 직전 완성분: +3%
        hist.append((minute0 + s*1000, 100 + 3*(s/59)))
    for s in range(20):                       # 현재분(진입 전까지)
        hist.append((minute0 + 60000 + s*1000, 103 + 0.5*(s/19)))
    now = minute0 + 60000 + 20*1000           # 12:01:20
    print("pump_1m       =", compute_pump_1m(hist, now), "(기대 ~+3.0)")
    print("pump_60s_roll =", compute_pump_60s_rolling(hist, now))
