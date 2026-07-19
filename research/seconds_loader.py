# -*- coding: utf-8 -*-
"""
초봉 타겟 수집기 (event-scoped seconds fetch).

핵심 아이디어:
  90일치 초봉을 전 종목 bulk로 받으면 데이터 폭발.
  대신 CLM 신호(1분봉 탐지)의 "진입 직후 horizon초"만 타겟 수집.
  → 신호당 API 1~2콜. A/C 코호트(진입 후 10~30초 모멘텀)를 잡는 유일한 해상도.

Upbit 초봉 API: https://api.upbit.com/v1/candles/seconds
  - 인증 불필요, 200개/요청, ~10 req/s
  - 거래 발생한 초에만 캔들 존재(빈 초 없음) → 갭 정상
  - 과거 ~90일 제공
"""
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests
import pandas as pd

UPBIT_BASE = "https://api.upbit.com/v1"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_sec")
REQ_DELAY = 0.12  # ~8 req/s (여유)
FMT = "%Y-%m-%dT%H:%M:%S"


def _fetch_seconds(market, to=None, count=200, retries=3):
    """단일 초봉 API 호출. to = 'YYYY-MM-DDTHH:MM:SSZ' (이 시각 이전, newest-first)."""
    params = {"market": market, "count": min(count, 200)}
    if to:
        params["to"] = to
    for attempt in range(retries):
        try:
            r = requests.get(f"{UPBIT_BASE}/candles/seconds", params=params, timeout=7)
            if r.status_code == 429:  # rate limit
                time.sleep(0.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()  # newest-first
        except requests.exceptions.RequestException:
            time.sleep(0.4 * (attempt + 1))
    return []


def fetch_window(market, start_dt, end_dt, max_pages=6):
    """
    [start_dt, end_dt] 구간의 초봉을 오름차순 DataFrame으로 반환.
    캔들이 sparse 하므로 end_dt 부터 뒤로 페이지네이션하여 start_dt 를 덮을 때까지 수집.

    Returns: DataFrame[dt, open, high, low, close, value]  (오름차순), 없으면 빈 DF
    """
    to_param = (end_dt + timedelta(seconds=1)).strftime(FMT) + "Z"
    rows = []
    seen = set()
    for _ in range(max_pages):
        batch = _fetch_seconds(market, to=to_param, count=200)
        if not batch:
            break
        for c in batch:
            t = c["candle_date_time_utc"]
            if t in seen:
                continue
            seen.add(t)
            rows.append(c)
        oldest = batch[-1]["candle_date_time_utc"]
        oldest_dt = datetime.strptime(oldest, FMT)
        if oldest_dt <= start_dt:
            break
        to_param = oldest + "Z"
        time.sleep(REQ_DELAY)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["dt"] = pd.to_datetime(df["candle_date_time_utc"])
    df = df.rename(columns={
        "opening_price": "open",
        "high_price": "high",
        "low_price": "low",
        "trade_price": "close",
        "candle_acc_trade_price": "value",
    })
    df = df[(df["dt"] >= start_dt) & (df["dt"] <= end_dt)]
    df = df.drop_duplicates(subset=["dt"]).sort_values("dt").reset_index(drop=True)
    return df[["dt", "open", "high", "low", "close", "value"]]


def collect_events_seconds(signals, horizon=300, cache_path=None, log_every=25):
    """
    신호 리스트 각각에 대해 진입 직후 horizon초 초봉을 수집.

    signals: iterable of dict — 최소 {market, entry_dt(datetime, 진입시각=신호 1분봉 종료시각)}
    Returns: dict[(market, entry_iso)] -> seconds DataFrame
    캐시: parquet 하나에 long-form 저장(key 컬럼 포함) → 재실행 시 스킵.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    cache = {}
    if cache_path and os.path.exists(cache_path):
        cached = pd.read_parquet(cache_path)
        for key, g in cached.groupby("_key"):
            cache[key] = g.drop(columns=["_key"]).reset_index(drop=True)

    out = {}
    all_long = []
    n = len(signals)
    fetched = 0
    for i, sig in enumerate(signals):
        market = sig["market"]
        entry_dt = sig["entry_dt"]
        key = f"{market}|{entry_dt.strftime(FMT)}"
        if key in cache:
            out[key] = cache[key]
            continue
        end_dt = entry_dt + timedelta(seconds=horizon)
        w = fetch_window(market, entry_dt, end_dt)
        out[key] = w
        fetched += 1
        if not w.empty:
            wl = w.copy()
            wl["_key"] = key
            all_long.append(wl)
        if (i + 1) % log_every == 0:
            print(f"  [{i+1:>4}/{n}] 수집 {fetched}건 (캐시 {i+1-fetched})", flush=True)
        time.sleep(REQ_DELAY)

    # 캐시 병합 저장
    if cache_path:
        parts = []
        if all_long:
            parts.append(pd.concat(all_long, ignore_index=True))
        if cache:
            old = pd.concat(
                [g.assign(_key=k) for k, g in cache.items()], ignore_index=True
            )
            parts.append(old)
        if parts:
            pd.concat(parts, ignore_index=True).to_parquet(cache_path, index=False)

    return out


def collect_events_seconds_threaded(signals, horizon=300, cache_path=None,
                                    workers=6, log_every=50):
    """collect_events_seconds 의 병렬 버전. 네트워크 지연이 병목일 때 크게 빠름."""
    os.makedirs(DATA_DIR, exist_ok=True)
    cache = {}
    if cache_path and os.path.exists(cache_path):
        cached = pd.read_parquet(cache_path)
        for key, g in cached.groupby("_key"):
            cache[key] = g.drop(columns=["_key"]).reset_index(drop=True)

    todo = []
    out = {}
    for sig in signals:
        key = f"{sig['market']}|{sig['entry_dt'].strftime(FMT)}"
        if key in cache:
            out[key] = cache[key]
        else:
            todo.append((key, sig))

    lock = threading.Lock()
    done = [0]
    new_long = []

    def work(item):
        key, sig = item
        end_dt = sig["entry_dt"] + timedelta(seconds=horizon)
        w = fetch_window(sig["market"], sig["entry_dt"], end_dt)
        with lock:
            out[key] = w
            if not w.empty:
                wl = w.copy(); wl["_key"] = key
                new_long.append(wl)
            done[0] += 1
            if done[0] % log_every == 0:
                print(f"  [{done[0]:>4}/{len(todo)}] 초봉 수집", flush=True)

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, todo))

    if cache_path:
        parts = list(new_long)
        if cache:
            parts.append(pd.concat(
                [g.assign(_key=k) for k, g in cache.items()], ignore_index=True))
        if parts:
            pd.concat(parts, ignore_index=True).to_parquet(cache_path, index=False)

    return out
