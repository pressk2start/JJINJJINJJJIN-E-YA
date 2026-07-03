"""
Upbit 1분봉 다운로더 + 캐싱.

API: https://api.upbit.com/v1/candles/minutes/{unit}
- 인증 불필요 (public)
- 200개/요청, 10 req/s 제한
- 1분봉 히스토리 무제한
"""
import os, time, json
import requests
import pandas as pd
from datetime import datetime, timedelta

UPBIT_BASE = "https://api.upbit.com/v1"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
REQ_DELAY = 0.12  # ~8 req/s


def get_krw_markets():
    """Upbit KRW 마켓 전체 목록 (정렬)"""
    r = requests.get(f"{UPBIT_BASE}/market/all", params={"is_details": "false"}, timeout=5)
    r.raise_for_status()
    return sorted([m["market"] for m in r.json() if m["market"].startswith("KRW-")])


def _fetch_page(market, unit=1, count=200, to=None):
    """단일 API 호출"""
    params = {"market": market, "count": min(count, 200)}
    if to:
        params["to"] = to
    r = requests.get(f"{UPBIT_BASE}/candles/minutes/{unit}", params=params, timeout=5)
    r.raise_for_status()
    return r.json()  # newest-first


def download_candles(market, days_back=90, unit=1, force=False):
    """
    market의 unit분봉을 days_back일치 다운로드 → parquet 캐시.
    Returns: parquet 파일 경로 (데이터 없으면 None)
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    fpath = os.path.join(DATA_DIR, f"{market}_m{unit}.parquet")
    if os.path.exists(fpath) and not force:
        return fpath

    cutoff = datetime.utcnow() - timedelta(days=days_back)
    all_candles = []
    to_param = None
    retries = 0

    while True:
        try:
            batch = _fetch_page(market, unit, 200, to_param)
        except requests.exceptions.RequestException as e:
            retries += 1
            if retries > 3:
                break
            time.sleep(1)
            continue
        retries = 0

        if not batch:
            break
        all_candles.extend(batch)

        # oldest in batch (newest-first → last = oldest)
        oldest = batch[-1]
        oldest_dt = datetime.strptime(oldest["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S")
        if oldest_dt <= cutoff:
            break
        to_param = oldest["candle_date_time_utc"]
        time.sleep(REQ_DELAY)

    if not all_candles:
        return None

    # 시간순 정렬 + 중복 제거
    all_candles.reverse()
    df = pd.DataFrame(all_candles)
    df = df.drop_duplicates(subset=["candle_date_time_utc"]).sort_values("candle_date_time_utc").reset_index(drop=True)

    # cutoff 이후만
    df["dt_utc"] = pd.to_datetime(df["candle_date_time_utc"])
    df = df[df["dt_utc"] >= cutoff].reset_index(drop=True)

    df.to_parquet(fpath, index=False)
    return fpath


def load_candles(market, unit=1):
    """캐시된 parquet 로드"""
    fpath = os.path.join(DATA_DIR, f"{market}_m{unit}.parquet")
    if not os.path.exists(fpath):
        return None
    return pd.read_parquet(fpath)


def quick_volume_rank(markets, top_n=30):
    """최근 5분봉 기준 거래대금 상위 N개 마켓 선별"""
    vol_map = {}
    for m in markets:
        try:
            batch = _fetch_page(m, unit=1, count=5)
            if batch:
                vol_map[m] = sum(c.get("candle_acc_trade_price", 0) for c in batch)
            time.sleep(REQ_DELAY)
        except Exception:
            pass
    return sorted(vol_map, key=vol_map.get, reverse=True)[:top_n]
