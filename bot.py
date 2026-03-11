# -*- coding: utf-8 -*-
"""
업비트 신호 연구 v3.3 — 지표확장+리포트강화+잠금강화
======================================
- 수집: 1m,3m,5m,15m,30m,1h (6TF)
- 1분봉 30일 청크수집 (메모리 안전)
- 중간중간 하트비트 알람 (텔레그램)
- 완료 후 확실한 종료 (완료 마커로 재실행 방지)
- PID 검증 + flock 이중 잠금
"""
import requests, time, json, os, sys, gc, argparse, math, atexit, signal as sig_mod, fcntl
from datetime import datetime, timedelta, timezone
from collections import defaultdict
KST = timezone(timedelta(hours=9))
BASE = "https://api.upbit.com/v1"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "candle_data")
OUT_DIR = os.path.join(SCRIPT_DIR, "analysis_output")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".study_lock")
DONE_FILE = os.path.join(SCRIPT_DIR, ".study_done")

# 하트비트 설정
HEARTBEAT_INTERVAL = 90  # 90초마다 텔레그램 살아있음 알람
CHUNK_SIZE = 5000         # 1분봉 청크 저장 단위
DONE_COOLDOWN = 3600      # 완료 후 재실행 방지 시간 (1시간)

# ── 완료 마커 (재실행 방지) ──
def _mark_done():
    """분석 완료 시 타임스탬프 기록"""
    try:
        with open(DONE_FILE, "w") as f:
            f.write(f"{time.time()}\n{os.getpid()}\n{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    except: pass

def _is_recently_done():
    """최근 DONE_COOLDOWN 이내에 완료된 적 있으면 True"""
    try:
        if not os.path.exists(DONE_FILE): return False
        with open(DONE_FILE) as f:
            ts = float(f.readline().strip())
        age = time.time() - ts
        if age < DONE_COOLDOWN:
            return True
        # 쿨다운 지남 → 마커 삭제
        os.remove(DONE_FILE)
        return False
    except:
        return False

# ── 중복 실행 방지 (PID 파일 + fcntl.flock) ──
_lock_fd = None

def _is_pid_alive(pid):
    """해당 PID가 살아있는지 확인"""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False

def _acquire_lock():
    """fcntl.flock + PID 검증으로 확실한 중복 방지."""
    global _lock_fd

    # 0단계: 최근 완료됐으면 즉시 종료
    if _is_recently_done():
        print(f"[스킵] 최근 완료됨 (쿨다운 {DONE_COOLDOWN}초). 재실행 불필요.")
        sys.exit(0)

    # 1단계: 기존 PID 파일 검증 (flock 실패 대비)
    _script_basename = os.path.basename(os.path.abspath(__file__))  # "upbit_signal_study.py"
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid() and _is_pid_alive(old_pid):
                # /proc/PID/cmdline으로 같은 스크립트인지 확인
                try:
                    with open(f"/proc/{old_pid}/cmdline", "rb") as f:
                        cmdline = f.read().decode("utf-8", errors="ignore")
                    # 스크립트 파일명 또는 모듈명으로 매칭 (실행 방식 무관)
                    if _script_basename in cmdline or "signal_study" in cmdline:
                        print(f"[잠금] PID {old_pid} 실행중 확인 (cmdline 매칭). 종료.")
                        sys.exit(0)
                except (FileNotFoundError, PermissionError):
                    # /proc 접근 실패해도, PID가 살아있으면 안전하게 차단
                    print(f"[잠금] PID {old_pid} 살아있음 (/proc 접근불가). 종료.")
                    sys.exit(0)
    except (ValueError, FileNotFoundError):
        pass

    # 2단계: flock 원자적 잠금 (레이스 컨디션 완전 방지)
    try:
        _lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 비차단 배타적 잠금
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
    except (IOError, OSError):
        print("[잠금] 다른 인스턴스가 실행중 (flock). 종료.")
        sys.exit(0)

def _release_lock(*a):
    global _lock_fd
    try:
        if _lock_fd:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
            _lock_fd = None
    except: pass
    try: os.remove(LOCK_FILE)
    except: pass

atexit.register(_release_lock)
for _s in (sig_mod.SIGTERM, sig_mod.SIGINT):
    sig_mod.signal(_s, lambda s,f: (_release_lock(), sys.exit(0)))

FEE_RATE = 0.0005
SLIPPAGE = 0.0008
TOTAL_COST = FEE_RATE * 2 + SLIPPAGE
MIN_TRADES = 100
TRAIN_RATIO = 0.7
MAX_HOLD_BARS = 60
MAX_HOLD_1M = 10
MAX_SIGS_PER_COIN = 200

COND_KEYS = {
    "rsi14","rsi7","bb20","bb10","stoch_k","stoch_d",
    "macd_x","macd_h","adx","cci","willr","mfi",
    "ema_al","disp10","disp20","ed5","ed20","ed50",
    "vr5","vr20","m3","m5","m10","m20",
    "pos10","pos20","vol10","vol20",
    "hammer","inv_ham","engulf","mstar","doji","cg","cr",
    "bar_atr","green","bbw20","uw_pct","lw_pct","body_pct",
}

COLLECT_TFS = [(3, 480), (5, 288), (15, 96), (30, 48), (60, 24)]
ALL_TFS = [(1, 1440), (3, 480), (5, 288), (15, 96), (30, 48), (60, 24)]
ANALYSIS_TFS = [5, 15, 60]  # 핵심 TF만 (1m/3m/30m 피처추출 제거 → 속도 2배↑)

SIG_TPSL = {
    "15m_눌림반전":  {"tp": 1.5, "sl": 1.0},
    "15m_눌림+돌파": {"tp": 1.6, "sl": 1.0},
    "5m_양봉":       {"tp": 1.0, "sl": 0.7},
    "20봉_고점돌파":  {"tp": 1.2, "sl": 0.8},
    "거래량3배":      {"tp": 0.8, "sl": 0.6},
    "BB하단반등":     {"tp": 1.2, "sl": 0.8},
    "RSI과매도반등":  {"tp": 1.0, "sl": 0.7},
    "MACD골든":      {"tp": 1.0, "sl": 0.8},
    "EMA정배열진입":  {"tp": 1.5, "sl": 1.0},
    "망치형반전":     {"tp": 1.2, "sl": 0.8},
    "5m_큰양봉":     {"tp": 0.8, "sl": 0.6},
    "쌍바닥":        {"tp": 1.5, "sl": 1.0},
}
EXIT_CONFIGS = [
    ("FIX_TP1.0/SL0.7", "fix", 1.0, 0.7, 0, 0),
    ("FIX_TP1.5/SL1.0", "fix", 1.5, 1.0, 0, 0),
    ("TRAIL_SL0.7/A0.3/T0.2", "trail", 0, 0.7, 0.3, 0.2),
    ("TRAIL_SL1.0/A0.5/T0.3", "trail", 0, 1.0, 0.5, 0.3),
    ("HOLD_12봉", "hold", 0, 0, 0, 12),
    ("TIME24_SL0.7", "time", 0, 0.7, 0, 24),
]

try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass
TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN") or ""
_raw = os.getenv("TG_CHATS") or os.getenv("TELEGRAM_CHAT_ID") or ""
CHAT_IDS = []
for p in _raw.split(","):
    p = p.strip()
    if p:
        try: CHAT_IDS.append(int(p))
        except: pass

_tg_last_send = 0.0  # rate limit용

def tg(msg):
    """텔레그램 전송 (rate limit 1초)"""
    global _tg_last_send
    print(msg)
    if not TG_TOKEN or not CHAT_IDS: return
    # rate limit: 최소 1초 간격
    now = time.time()
    gap = now - _tg_last_send
    if gap < 1.0:
        time.sleep(1.0 - gap)
    chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)] if len(msg) > 4000 else [msg]
    for cid in CHAT_IDS:
        for chunk in chunks:
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={"chat_id": cid, "text": chunk, "disable_web_page_preview": True},
                    timeout=10)
                if r.status_code == 429:
                    retry = int(r.headers.get("Retry-After", 3))
                    print(f"[TG] rate limit, {retry}초 대기")
                    time.sleep(retry)
                elif r.status_code != 200:
                    print(f"[TG경고] {r.status_code}: {r.text[:200]}")
            except Exception as e:
                print(f"[TG에러] {e}")
    _tg_last_send = time.time()

def _get_mem_mb():
    try:
        with open(f"/proc/{os.getpid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except: pass
    return -1

def safe_get(url, params=None, retries=4, timeout=10):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200: return r.json()
            if r.status_code == 429: time.sleep(1 + i); continue
            time.sleep(0.3)
        except: time.sleep(0.5)
    return None

def _atomic_json_write(fpath, obj):
    """원자적 JSON 쓰기 — PID 포함 tmp로 동시실행 충돌 방지"""
    tmp = f"{fpath}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, fpath)  # 원자적 교체
    except Exception:
        try: os.remove(tmp)
        except: pass
        raise

def _safe_json_load(fpath):
    """JSON 로드 — 깨진 파일 자동 복구 (Extra data 등)"""
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[JSON복구] {os.path.basename(fpath)}: {e}")
        try:
            with open(fpath, encoding="utf-8") as f:
                raw = f.read()
            decoder = json.JSONDecoder()
            obj, idx = decoder.raw_decode(raw)
            tmp = fpath + ".fix"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)
            os.replace(tmp, fpath)
            print(f"[JSON복구] {os.path.basename(fpath)} 복구 성공")
            return obj
        except Exception as e2:
            print(f"[JSON삭제] {os.path.basename(fpath)} 복구 실패, 삭제: {e2}")
            try: os.remove(fpath)
            except: pass
            return None
    except Exception as e:
        print(f"[JSON에러] {os.path.basename(fpath)}: {e}")
        return None

# ================================================================
# PART 1: 수집
# ================================================================
def get_top_markets(n=30):
    tickers = safe_get(f"{BASE}/market/all", {"isDetails": "true"})
    if not tickers: return []
    krw = [t["market"] for t in tickers if t["market"].startswith("KRW-")]
    stable = {"USDT","USDC","DAI","TUSD","BUSD"}
    krw = [m for m in krw if m.split("-")[1] not in stable]
    all_t = []
    for i in range(0, len(krw), 100):
        b = safe_get(f"{BASE}/ticker", {"markets": ",".join(krw[i:i+100])})
        if b: all_t.extend(b)
        time.sleep(0.15)
    all_t.sort(key=lambda x: x.get("acc_trade_price_24h",0), reverse=True)
    return [t["market"] for t in all_t[:n]]

def compress(c):
    return {"t":c["candle_date_time_kst"],"o":c["opening_price"],"h":c["high_price"],
            "l":c["low_price"],"c":c["trade_price"],
            "v":round(c.get("candle_acc_trade_volume",0),6)}

def fetch_candles(unit, market, total_count, max_time=120):
    """일반 TF 수집 (3m~60m). 메모리에 모아서 반환."""
    result, to = [], None
    t0 = time.time()
    fails = 0
    for page in range((total_count+199)//200):
        if time.time()-t0 > max_time: break
        params = {"market": market, "count": min(200, total_count-len(result))}
        if to: params["to"] = to
        data = safe_get(f"{BASE}/candles/minutes/{unit}", params)
        if not data:
            fails += 1
            if fails >= 3: break
            continue
        fails = 0
        result.extend(data)
        if len(data) < 200: break
        to = data[-1]["candle_date_time_utc"] + "Z"
        time.sleep(0.15)
    result.sort(key=lambda x: x["candle_date_time_kst"])
    return result

def _chunk_path_1m(coin, chunk_idx):
    return os.path.join(DATA_DIR, f".{coin}_1m_p{os.getpid()}_chunk{chunk_idx}.tmp")

def _cleanup_1m_chunks(coin, max_chunks=200):
    """이 프로세스의 청크 + 오래된 고아 청크 정리"""
    # 이 프로세스 청크 삭제
    for ci in range(max_chunks):
        cp = _chunk_path_1m(coin, ci)
        if os.path.exists(cp):
            try: os.remove(cp)
            except: pass
    # 고아 청크 (다른 프로세스가 남긴 것) 정리
    if os.path.exists(DATA_DIR):
        for f in os.listdir(DATA_DIR):
            if f.startswith(f".{coin}_1m_p") and f.endswith(".tmp"):
                fp = os.path.join(DATA_DIR, f)
                try:
                    age = time.time() - os.path.getmtime(fp)
                    if age > 1800:  # 30분 이상 된 고아 청크
                        os.remove(fp)
                except: pass

def fetch_candles_1m_chunked(market, coin, total_count, max_time=900):
    """1분봉 30일치 청크 수집 (메모리 안전). 43200개도 OK."""
    _cleanup_1m_chunks(coin)
    os.makedirs(DATA_DIR, exist_ok=True)

    buffer = []
    chunk_idx = 0
    total_fetched = 0
    to = None
    start_ts = time.time()
    last_hb = start_ts
    fails = 0

    while total_fetched < total_count:
        elapsed = time.time() - start_ts
        if elapsed > max_time:
            tg(f"    {coin} 1m 시간초과 {int(elapsed)}초 → {total_fetched}개까지")
            break

        remain = total_count - total_fetched
        count = min(200, remain)
        params = {"market": market, "count": count}
        if to: params["to"] = to

        data = safe_get(f"{BASE}/candles/minutes/1", params=params, retries=6, timeout=12)
        if not data:
            fails += 1
            if fails >= 5:
                tg(f"    {coin} 1m 연속 {fails}회 실패, 중단 ({total_fetched}개)")
                break
            time.sleep(1.0 + fails * 0.5)
            continue

        fails = 0
        got = len(data)
        if got > 0:
            to = data[-1]["candle_date_time_utc"] + "Z"

        for c in data:
            buffer.append(compress(c))
        total_fetched += got
        del data

        # 하트비트 알람
        now = time.time()
        if now - last_hb >= HEARTBEAT_INTERVAL:
            pct = total_fetched / total_count * 100
            mem = _get_mem_mb()
            mem_str = f" | mem={mem:.0f}MB" if mem > 0 else ""
            tg(f"[살아있음] {coin} 1m수집 {total_fetched:,}/{total_count:,} ({pct:.0f}%) | {int(elapsed)}초{mem_str}")
            last_hb = now

        # 청크 저장 (메모리 보호)
        if len(buffer) >= CHUNK_SIZE:
            fpath = _chunk_path_1m(coin, chunk_idx)
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(buffer, f, ensure_ascii=False)
            chunk_idx += 1
            buffer.clear()
            gc.collect()

        if got < 200: break
        time.sleep(0.35)

    # 나머지 버퍼 저장
    if buffer:
        fpath = _chunk_path_1m(coin, chunk_idx)
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(buffer, f, ensure_ascii=False)
        chunk_idx += 1
        buffer.clear()
        gc.collect()

    if chunk_idx == 0: return 0

    # 청크 병합 → 최종 파일
    all_candles = []
    for ci in range(chunk_idx):
        cp = _chunk_path_1m(coin, ci)
        if not os.path.exists(cp): continue
        with open(cp, "r", encoding="utf-8") as f:
            all_candles.extend(json.load(f))
        os.remove(cp)
    gc.collect()

    if not all_candles: return 0
    all_candles.sort(key=lambda x: x["t"])
    seen = set(); deduped = []
    for c in all_candles:
        if c["t"] not in seen: seen.add(c["t"]); deduped.append(c)
    del all_candles, seen; gc.collect()

    fpath = os.path.join(DATA_DIR, f"{coin}_1m.json")
    payload = {"coin":coin,"unit":1,"count":len(deduped),
               "from":deduped[0]["t"],"to":deduped[-1]["t"],
               "candles":deduped,
               "saved_at":datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")}
    _atomic_json_write(fpath, payload)
    count = len(deduped)
    del deduped, payload; gc.collect()
    return count

def save_coin(coin, unit, candles):
    os.makedirs(DATA_DIR, exist_ok=True)
    comp = [compress(c) for c in candles]
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    _atomic_json_write(fpath, {"coin":coin,"unit":unit,"count":len(comp),
                    "from":comp[0]["t"] if comp else "",
                    "to":comp[-1]["t"] if comp else "",
                    "candles":comp})

def is_collected(coin, unit, min_count=100):
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath): return False
    try:
        data = _safe_json_load(fpath)
        if data is None: return False
        return data.get("count",0) >= min_count
    except: return False

def collect(days=30, top_n=30):
    """전체 TF 수집 (1분봉 포함, 30일 청크방식)"""
    tg(f"[수집] {days}일, {top_n}코인, 6TF (1m포함 청크방식)")
    markets = get_top_markets(top_n)
    if not markets: tg("[오류] 종목 실패"); return []
    coins = [m.split("-")[1] for m in markets]
    tg(f"종목: {', '.join(coins)}")
    t0 = time.time()
    last_hb = t0

    for i, market in enumerate(markets):
        coin = market.split("-")[1]

        # 하트비트 (코인 사이)
        now = time.time()
        if now - last_hb >= HEARTBEAT_INTERVAL:
            elapsed = now - t0
            mem = _get_mem_mb()
            mem_str = f" | mem={mem:.0f}MB" if mem > 0 else ""
            tg(f"[살아있음] 수집 진행중 {i+1}/{len(markets)} | {int(elapsed)}초 경과{mem_str}")
            last_hb = now

        try:
            # 3m~60m 일반 수집
            for tf, per_day in COLLECT_TFS:
                total = per_day * days
                if is_collected(coin, tf, total//2): continue
                candles = fetch_candles(tf, market, total)
                if candles: save_coin(coin, tf, candles); del candles; gc.collect()
                time.sleep(0.2)

            # 1분봉 청크 수집 (30일 = 43200개, 메모리 안전)
            need_1m = 1440 * days
            if not is_collected(coin, 1, int(need_1m * 0.9)):
                tg(f"  {coin} 1분봉 수집 시작 (목표 {need_1m:,}개)")
                cnt = fetch_candles_1m_chunked(market, coin, need_1m, max_time=900)
                tg(f"  {coin} 1분봉 완료: {cnt:,}개")
                gc.collect()
            else:
                print(f"  {coin} 1분봉 스킵 (이미 수집됨)")

        except Exception as e:
            tg(f"[수집에러] {coin}: {e}")
            _cleanup_1m_chunks(coin)
            continue

        if (i+1) % 5 == 0:
            el = time.time()-t0; eta = el/(i+1)*(top_n-i-1)
            tg(f"  수집 {i+1}/{top_n} ({el/60:.1f}분, ~{eta/60:.1f}분 남음)")

    tg(f"[수집완료] {(time.time()-t0)/60:.1f}분")
    return coins

# ================================================================
# PART 2: 지표 함수
# ================================================================
def load_candles(coin, unit):
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath): return []
    data = _safe_json_load(fpath)
    if data is None: return []
    return data.get("candles", [])

def get_saved_coins():
    if not os.path.exists(DATA_DIR): return []
    return sorted(set(f.replace("_5m.json","") for f in os.listdir(DATA_DIR) if f.endswith("_5m.json")))

def make_nmin(c_small, ratio):
    out = []
    for i in range(0, len(c_small)-ratio+1, ratio):
        b = c_small[i:i+ratio]
        out.append({"t":b[0]["t"],"o":b[0]["o"],"c":b[-1]["c"],
                     "h":max(x["h"] for x in b),"l":min(x["l"] for x in b),
                     "v":sum(x["v"] for x in b),"_si":i})
    return out

def _rsi(cl, p=14):
    if len(cl)<p+1: return 50.0
    g,l=0.0,0.0
    for i in range(len(cl)-p,len(cl)):
        d=cl[i]-cl[i-1]
        if d>0: g+=d
        else: l-=d
    return 100.0-100.0/(1.0+g/l) if l else 100.0

def _bb(cl, p=20):
    if len(cl)<p: return 50.0,0.0
    s=cl[-p:]; m=sum(s)/p; std=(sum((x-m)**2 for x in s)/p)**0.5
    if std==0 or m==0: return 50.0,0.0
    u,lo=m+2*std,m-2*std
    return (cl[-1]-lo)/(u-lo)*100, (u-lo)/m*100

def _ema(cl, p):
    if not cl: return 0.0
    if len(cl)<p: return cl[-1]
    k=2.0/(p+1); e=sum(cl[:p])/p
    for v in cl[p:]: e=v*k+e*(1-k)
    return e

def _stoch(hi, lo, cl, p=14):
    if len(cl)<p: return 50.0
    hh,ll = max(hi[-p:]),min(lo[-p:])
    return (cl[-1]-ll)/(hh-ll)*100 if hh!=ll else 50.0

def _atr(hi,lo,cl,p=14):
    if len(cl)<p+1: return 0.0
    return sum(max(hi[i]-lo[i],abs(hi[i]-cl[i-1]),abs(lo[i]-cl[i-1])) for i in range(len(cl)-p,len(cl)))/p

def _adx(hi,lo,cl,p=14):
    if len(cl)<p+2: return 25.0
    dp,dm,tr=0.0,0.0,0.0
    for i in range(len(cl)-p,len(cl)):
        u,d=hi[i]-hi[i-1],lo[i-1]-lo[i]
        dp+=(u if u>d and u>0 else 0); dm+=(d if d>u and d>0 else 0)
        tr+=max(hi[i]-lo[i],abs(hi[i]-cl[i-1]),abs(lo[i]-cl[i-1]))
    if tr==0: return 25.0
    dip,dim=dp/tr*100,dm/tr*100
    return abs(dip-dim)/(dip+dim)*100 if dip+dim>0 else 0

def _cci(hi,lo,cl,p=20):
    if len(cl)<p: return 0.0
    tps=[(hi[i]+lo[i]+cl[i])/3 for i in range(len(cl)-p,len(cl))]
    m=sum(tps)/p; md=sum(abs(t-m) for t in tps)/p
    return (tps[-1]-m)/(0.015*md) if md else 0.0

def _willr(hi,lo,cl,p=14):
    if len(cl)<p: return -50.0
    hh,ll=max(hi[-p:]),min(lo[-p:])
    return (hh-cl[-1])/(hh-ll)*-100 if hh!=ll else -50.0

def _mfi(hi,lo,cl,vol,p=14):
    if len(cl)<p+1: return 50.0
    pf,nf=0.0,0.0
    for i in range(len(cl)-p,len(cl)):
        tp=(hi[i]+lo[i]+cl[i])/3; tp0=(hi[i-1]+lo[i-1]+cl[i-1])/3
        mf=tp*vol[i]
        if tp>tp0: pf+=mf
        else: nf+=mf
    return 100.0-100.0/(1.0+pf/nf) if nf else 100.0

def _obv_chg(cl,vol,p=10):
    if len(cl)<p+1: return 0.0
    obv=0.0; start=None
    for i in range(len(cl)-p-1,len(cl)):
        if i<1: continue
        if cl[i]>cl[i-1]: obv+=vol[i]
        elif cl[i]<cl[i-1]: obv-=vol[i]
        if start is None: start=obv
    if start is None: return 0.0
    return (obv-start)/max(abs(start),1)*100

def _disp(cl,p=20):
    if len(cl)<p: return 100.0
    ma=sum(cl[-p:])/p
    return cl[-1]/ma*100 if ma else 100.0

def _ema_align(cl):
    if len(cl)<50: return 0
    e5,e10,e20,e50=_ema(cl,5),_ema(cl,10),_ema(cl,20),_ema(cl,50)
    s=0
    if e5>e10: s+=1
    else: s-=1
    if e10>e20: s+=1
    else: s-=1
    if e20>e50: s+=1
    else: s-=1
    if cl[-1]>e5: s+=1
    else: s-=1
    return s

def _vol(cl,p=20):
    if len(cl)<p: return 0.0
    s=cl[-p:]; m=sum(s)/p
    return (sum((x-m)**2 for x in s)/p)**0.5/m*100 if m else 0.0

def _stoch_d(hi, lo, cl, kp=14, dp=3):
    if len(cl)<kp+dp: return 50.0
    ks=[]
    for i in range(dp):
        end=len(cl)-dp+1+i
        hh,ll=max(hi[end-kp:end]),min(lo[end-kp:end])
        ks.append((cl[end-1]-ll)/(hh-ll)*100 if hh!=ll else 50.0)
    return sum(ks)/dp

def _macd_hist(cl):
    if len(cl)<35: return 0.0
    ef=_ema(cl,12); es=_ema(cl,26)
    macd=ef-es
    price=cl[-1] if cl[-1]>0 else 1
    return round(macd/price*100,4)

def _patterns(candles, idx):
    if idx<3: return {}
    c=candles[idx]; p1=candles[idx-1]; p2=candles[idx-2]; p3=candles[idx-3] if idx>=3 else p2
    o,h,l,cl=c["o"],c["h"],c["l"],c["c"]
    rng=h-l if h!=l else 0.0001
    body=abs(cl-o); uw=h-max(o,cl); lw=min(o,cl)-l
    pat = {}
    pat["hammer"]=1 if lw>body*2 and uw<body*0.5 and body>0 else 0
    pat["inv_ham"]=1 if uw>body*2 and lw<body*0.5 and body>0 else 0
    pat["doji"]=1 if body/rng<0.1 else 0
    p1b=abs(p1["c"]-p1["o"])
    pat["engulf"]=1 if p1["c"]<p1["o"] and cl>o and body>p1b and o<=p1["c"] and cl>=p1["o"] else 0
    p2b=abs(p2["c"]-p2["o"]); p2rng=p2["h"]-p2["l"] if p2["h"]!=p2["l"] else 0.0001
    pat["mstar"]=1 if (p3["c"]<p3["o"]) and (p2b/p2rng<0.3) and (cl>o) and (body>p1b) else 0
    cg=0
    for k in range(idx, max(idx-6,-1),-1):
        if candles[k]["c"]>candles[k]["o"]: cg+=1
        else: break
    pat["cg"]=cg
    cr=0
    for k in range(idx, max(idx-6,-1),-1):
        if candles[k]["c"]<candles[k]["o"]: cr+=1
        else: break
    pat["cr"]=cr
    pat["uw_pct"]=round(uw/rng*100,1)
    pat["lw_pct"]=round(lw/rng*100,1)
    pat["body_pct"]=round(body/rng*100,1)
    return pat

# ================================================================
# PART 3: 코인별 분석
# ================================================================
def extract_tf_features(candles, idx, pfx):
    if idx < 55: return {}
    f = {}
    start = idx - 54
    cl = [candles[j]["c"] for j in range(start, idx+1)]
    hi = [candles[j]["h"] for j in range(start, idx+1)]
    lo = [candles[j]["l"] for j in range(start, idx+1)]
    vo = [candles[j]["v"] for j in range(start, idx+1)]
    if len(cl)<30: return {}
    price = cl[-1] if cl[-1]>0 else 1
    f[pfx+"rsi7"]=round(_rsi(cl,7),1)
    f[pfx+"rsi14"]=round(_rsi(cl,14),1)
    f[pfx+"rsi21"]=round(_rsi(cl,21),1)
    bp10,bw10=_bb(cl,10); bp20,bw20=_bb(cl,20)
    f[pfx+"bb10"]=round(bp10,1); f[pfx+"bbw10"]=round(bw10,3)
    f[pfx+"bb20"]=round(bp20,1); f[pfx+"bbw20"]=round(bw20,3)
    ef,es=_ema(cl,12),_ema(cl,26)
    f[pfx+"macd_x"]=1 if ef>es else 0
    f[pfx+"macd_h"]=_macd_hist(cl)
    f[pfx+"stoch_k"]=round(_stoch(hi,lo,cl),1)
    f[pfx+"stoch_d"]=round(_stoch_d(hi,lo,cl),1)
    atr=_atr(hi,lo,cl); f[pfx+"atr"]=round(atr/price*100,3) if price else 0
    f[pfx+"adx"]=round(_adx(hi,lo,cl),1)
    f[pfx+"cci"]=round(_cci(hi,lo,cl),1)
    f[pfx+"willr"]=round(_willr(hi,lo,cl),1)
    f[pfx+"mfi"]=round(_mfi(hi,lo,cl,vo),1)
    f[pfx+"obv"]=round(_obv_chg(cl,vo),2)
    f[pfx+"disp10"]=round(_disp(cl,10),2)
    f[pfx+"disp20"]=round(_disp(cl,20),2)
    f[pfx+"ema_al"]=_ema_align(cl)
    for ep in [5,10,20,50]:
        ev=_ema(cl,ep)
        f[pfx+f"ed{ep}"]=round((cl[-1]-ev)/ev*100,3) if ev>0 else 0
    f[pfx+"vol10"]=round(_vol(cl,10),3)
    f[pfx+"vol20"]=round(_vol(cl,20),3)
    av5=sum(vo[-6:-1])/5 if len(vo)>=6 else 1
    f[pfx+"vr5"]=round(vo[-1]/av5,2) if av5>0 else 1
    if len(vo)>=21:
        av20=sum(vo[-21:-1])/20
        f[pfx+"vr20"]=round(vo[-1]/av20,2) if av20>0 else 1
    else:
        f[pfx+"vr20"]=1.0
    for mp in [3,5,10,20]:
        f[pfx+f"m{mp}"]=round((cl[-1]-cl[-(mp+1)])/cl[-(mp+1)]*100,3) if len(cl)>mp and cl[-(mp+1)]>0 else 0
    for np in [10,20]:
        if len(cl)>=np:
            hh,ll=max(cl[-np:]),min(cl[-np:])
            f[pfx+f"pos{np}"]=round((cl[-1]-ll)/(hh-ll)*100,1) if hh!=ll else 50.0
    bar_size=abs(candles[idx]["c"]-candles[idx]["o"])
    f[pfx+"bar_atr"]=round(bar_size/atr,2) if atr>0 else 0
    cp=_patterns(candles,idx)
    for k,v in cp.items(): f[pfx+k]=v
    f[pfx+"green"]=1 if candles[idx]["c"]>candles[idx]["o"] else 0
    return f

def find_idx(candles, sig_time):
    lo,hi=0,len(candles)-1; r=None
    while lo<=hi:
        mid=(lo+hi)//2
        if candles[mid]["t"]<=sig_time: r=mid; lo=mid+1
        else: hi=mid-1
    return r

def process_coin(coin, btc_regime, dist_acc):
    print(f"  >> {coin} 분석 시작")  # print만 (텔레그램 폭격 방지)
    c5 = load_candles(coin, 5)
    if len(c5) < 200: return {}
    c15 = make_nmin(c5, 3)

    raw_signals = {}
    for name, req_brk in [("15m_눌림반전", False), ("15m_눌림+돌파", True)]:
        sigs = []; last = -20
        for i in range(14, len(c15)):
            bar, prev = c15[i], c15[i-1]
            if prev["c"]>=prev["o"] or bar["c"]<=bar["o"]: continue
            if (bar["c"]-bar["o"])/bar["o"]*100<0.1: continue
            if req_brk and bar["c"]<=prev["h"]: continue
            si = bar.get("_si", i*3)+2; ei = si+1
            if ei-last<10 or ei>=len(c5)-MAX_HOLD_BARS: continue
            last=ei; sigs.append((si, ei, bar))
        raw_signals[name] = sigs

    sigs = []; last=-30
    for i in range(60, len(c5)-MAX_HOLD_BARS-1):
        bar=c5[i]
        if bar["c"]<=bar["o"]: continue
        if (bar["c"]-bar["o"])/bar["o"]*100<0.15: continue
        ei=i+1
        if ei-last<30: continue
        last=ei; sigs.append((i, ei, bar))
    raw_signals["5m_양봉"] = sigs

    sigs=[]; last=-20
    for i in range(30, len(c5)-MAX_HOLD_BARS-1):
        bar=c5[i]
        if bar["c"]<=bar["o"]: continue
        ph=max(c5[j]["h"] for j in range(max(0,i-20),i))
        if bar["h"]<=ph: continue
        ei=i+1
        if ei-last<20: continue
        last=ei; sigs.append((i, ei, bar))
    raw_signals["20봉_고점돌파"] = sigs

    sigs=[]; last=-20
    for i in range(30, len(c5)-MAX_HOLD_BARS-1):
        bar=c5[i]
        if bar["c"]<=bar["o"]: continue
        vs=[c5[j]["v"] for j in range(max(0,i-5),i)]
        av=sum(vs)/max(len(vs),1)
        if av==0 or bar["v"]<av*3: continue
        ei=i+1
        if ei-last<20: continue
        last=ei; sigs.append((i, ei, bar))
    raw_signals["거래량3배"] = sigs

    # ── 추가 신호유형 (pre-compute 방식, 성능 최적화) ──
    # 전체 close 배열 한 번만 생성 (매 봉마다 리스트 생성 방지)
    n5 = len(c5)
    end_idx = n5 - MAX_HOLD_BARS - 1
    closes = [c5[j]["c"] for j in range(n5)]
    highs = [c5[j]["h"] for j in range(n5)]
    lows = [c5[j]["l"] for j in range(n5)]
    opens = [c5[j]["o"] for j in range(n5)]
    bodies = [abs(closes[j] - opens[j]) for j in range(n5)]

    # pre-compute: RSI14, BB20위치, MACD교차, EMA정배열 (한 번에 전체)
    pre_rsi = [0.0] * n5
    pre_bb = [50.0] * n5
    pre_macd_cross = [False] * n5  # 이 봉에서 골든크로스 발생?
    pre_ema_aligned = [False] * n5  # 이 봉에서 정배열?

    # RSI14 pre-compute
    for i in range(20, n5):
        pre_rsi[i] = _rsi(closes[i-19:i+1], 14)

    # BB20 위치 pre-compute
    for i in range(24, n5):
        pre_bb[i], _ = _bb(closes[i-24:i+1], 20)

    # MACD/EMA pre-compute (EMA를 증분으로 계산)
    if n5 > 60:
        k12 = 2.0 / 13; k26 = 2.0 / 27
        k5 = 2.0 / 6; k10 = 2.0 / 11; k20 = 2.0 / 21; k50 = 2.0 / 51
        ema12 = sum(closes[:12]) / 12
        ema26 = sum(closes[:26]) / 26
        ema5 = sum(closes[:5]) / 5
        ema10 = sum(closes[:10]) / 10
        ema20 = sum(closes[:20]) / 20
        ema50 = sum(closes[:50]) / 50 if n5 >= 50 else closes[0]
        prev_macd_diff = 0.0
        prev_aligned = False
        for i in range(max(26, 50), n5):
            ema12 = closes[i] * k12 + ema12 * (1 - k12)
            ema26 = closes[i] * k26 + ema26 * (1 - k26)
            ema5 = closes[i] * k5 + ema5 * (1 - k5)
            ema10 = closes[i] * k10 + ema10 * (1 - k10)
            ema20 = closes[i] * k20 + ema20 * (1 - k20)
            ema50 = closes[i] * k50 + ema50 * (1 - k50)
            macd_diff = ema12 - ema26
            if prev_macd_diff <= 0 and macd_diff > 0:
                pre_macd_cross[i] = True
            prev_macd_diff = macd_diff
            aligned = ema5 > ema10 > ema20 > ema50
            if aligned and not prev_aligned:
                pre_ema_aligned[i] = True  # 첫 진입만
            prev_aligned = aligned

    # BB하단반등: pre-computed BB20 < 10 && 양봉
    sigs=[]; last=-15
    for i in range(60, end_idx):
        if closes[i] <= opens[i]: continue
        if pre_bb[i] >= 10: continue
        ei=i+1
        if ei-last<15: continue
        last=ei; sigs.append((i, ei, c5[i]))
    raw_signals["BB하단반등"] = sigs

    # RSI과매도반등: pre-computed RSI14
    sigs=[]; last=-15
    for i in range(60, end_idx):
        if pre_rsi[i-1] >= 30 or pre_rsi[i] < 30: continue
        ei=i+1
        if ei-last<15: continue
        last=ei; sigs.append((i, ei, c5[i]))
    raw_signals["RSI과매도반등"] = sigs

    # MACD골든크로스: pre-computed
    sigs=[]; last=-20
    for i in range(60, end_idx):
        if not pre_macd_cross[i]: continue
        ei=i+1
        if ei-last<20: continue
        last=ei; sigs.append((i, ei, c5[i]))
    raw_signals["MACD골든"] = sigs

    # EMA정배열진입: pre-computed
    sigs=[]; last=-30
    for i in range(60, end_idx):
        if not pre_ema_aligned[i]: continue
        ei=i+1
        if ei-last<30: continue
        last=ei; sigs.append((i, ei, c5[i]))
    raw_signals["EMA정배열진입"] = sigs

    # 망치형반전: 순수 봉 형태 (계산 가벼움)
    sigs=[]; last=-15
    for i in range(60, end_idx):
        o,h,l,cl_v=opens[i],highs[i],lows[i],closes[i]
        rng=h-l if h!=l else 0.0001
        body=abs(cl_v-o); lw=min(o,cl_v)-l; uw=h-max(o,cl_v)
        if not (lw>body*2 and uw<body*0.5 and body>0): continue
        if not (closes[i-1]<opens[i-1] and closes[i-2]<opens[i-2]): continue
        ei=i+1
        if ei-last<15: continue
        last=ei; sigs.append((i, ei, c5[i]))
    raw_signals["망치형반전"] = sigs

    # 5m_큰양봉: pre-computed bodies 배열 사용
    sigs=[]; last=-20
    # 20봉 rolling average body (한 번에 계산)
    body_sum20 = sum(bodies[:20]) if n5 >= 20 else 0
    for i in range(60, end_idx):
        if closes[i] <= opens[i]: continue
        body = closes[i] - opens[i]
        # rolling average 갱신
        if i >= 20:
            body_sum20 = body_sum20 - bodies[i-20] + bodies[i-1]
            avg_body = body_sum20 / 20
        else:
            avg_body = body
        if avg_body == 0 or body < avg_body * 2: continue
        ei=i+1
        if ei-last<20: continue
        last=ei; sigs.append((i, ei, c5[i]))
    raw_signals["5m_큰양봉"] = sigs

    # 쌍바닥: lows 배열 직접 참조 (리스트 생성 최소화)
    sigs=[]; last=-30
    for i in range(60, end_idx):
        if closes[i] <= opens[i]: continue
        # 직전 20봉 저점 2개 찾기
        start = max(0, i-20)
        if i - start < 10: continue
        min1_j = start
        for j in range(start+1, i):
            if lows[j] < lows[min1_j]: min1_j = j
        min2_j = -1; min2_v = float('inf')
        for j in range(start, i):
            if abs(j - min1_j) > 3 and lows[j] < min2_v:
                min2_j = j; min2_v = lows[j]
        if min2_j < 0: continue
        spread = abs(lows[min1_j] - min2_v) / max(lows[min1_j], 0.0001) * 100
        if spread > 0.5: continue
        if lows[i] <= max(lows[min1_j], min2_v): continue
        ei=i+1
        if ei-last<30: continue
        last=ei; sigs.append((i, ei, c5[i]))
    raw_signals["쌍바닥"] = sigs

    # pre-compute 배열 해제
    del closes, highs, lows, opens, bodies, pre_rsi, pre_bb, pre_macd_cross, pre_ema_aligned

    total_sigs = sum(len(v) for v in raw_signals.values())
    print(f"    {coin} 신호: {total_sigs}개")  # print만
    if total_sigs == 0:
        del c5, c15; gc.collect(); return {}

    if total_sigs > MAX_SIGS_PER_COIN:
        ratio = MAX_SIGS_PER_COIN / total_sigs
        for stype in raw_signals:
            orig = raw_signals[stype]
            step = max(1, int(1/ratio))
            raw_signals[stype] = orig[::step][:int(len(orig)*ratio)+1]
        total_sigs = sum(len(v) for v in raw_signals.values())

    all_sig_times = set()
    for sigs in raw_signals.values():
        for si5, ei, bar in sigs:
            all_sig_times.add(bar["t"])

    # 1m 데이터 미리 한 번만 로드 (2번 로드 방지)
    c1 = load_candles(coin, 1)
    c1_map = {}
    if c1 and len(c1) > 100:
        c1_map = {c["t"][:16]: i for i, c in enumerate(c1)}
    use_1m = len(c1_map) > 100

    tf_feat_cache = defaultdict(dict)
    for tf in ANALYSIS_TFS:
        if tf == 5:
            candles = c5
        else:
            candles = load_candles(coin, tf)
            if not candles or len(candles)<60:
                if tf==15: candles=c15
                elif tf==30: candles=make_nmin(c5,6)
                elif tf==60: candles=make_nmin(c5,12)
                else: candles=None
                if not candles or len(candles)<60: continue
        if tf == 5: time_map = None
        else: time_map = {cc["t"][:16]: idx for idx,cc in enumerate(candles)}
        pfx = f"tf{tf}_"
        for sig_time in all_sig_times:
            if tf == 5: continue
            tk = sig_time[:16]
            idx = time_map.get(tk)
            if idx is None: idx = find_idx(candles, sig_time)
            if idx is not None and idx >= 55:
                all_feats = extract_tf_features(candles, idx, pfx)
                for fk, fv in all_feats.items():
                    short_key = fk[len(pfx):]
                    lst = dist_acc[(tf, short_key)]
                    if len(lst) < 10000:  # 메모리 보호
                        lst.append(fv)
                for fk, fv in all_feats.items():
                    short_key = fk[len(pfx):]
                    if short_key in COND_KEYS:
                        tf_feat_cache[sig_time][fk] = fv
                del all_feats
        if tf != 5: del candles; gc.collect()

    pfx5 = "tf5_"
    for sigs in raw_signals.values():
        for si5, ei, bar in sigs:
            if si5 >= 55:
                all_feats = extract_tf_features(c5, si5, pfx5)
                for fk, fv in all_feats.items():
                    short_key = fk[len(pfx5):]
                    lst = dist_acc[(5, short_key)]
                    if len(lst) < 10000: lst.append(fv)  # 메모리 보호
                for fk, fv in all_feats.items():
                    short_key = fk[len(pfx5):]
                    if short_key in COND_KEYS:
                        tf_feat_cache[bar["t"]][fk] = fv
                del all_feats

    results = {}
    cost_pct = TOTAL_COST * 100
    for stype, sigs_raw in raw_signals.items():
        built = []
        tpsl = SIG_TPSL.get(stype, {"tp": 1.0, "sl": 0.7})
        for si5, ei, bar in sigs_raw:
            entry = c5[ei]["o"]
            if entry <= 0: continue
            try: hour=int(bar["t"][11:13])
            except: hour=12
            tk=bar["t"][:16]
            regime=btc_regime.get(tk,"횡보")
            dip3=1 if si5>=3 and c5[si5-3]["c"]>c5[si5-1]["c"] else 0
            brk=1 if si5>=1 and bar["c"]>c5[si5-1]["h"] else 0
            mb=min(MAX_HOLD_BARS, len(c5)-ei-1)
            mfe,mae,mfe_bar=0,0,0
            for j in range(1,mb+1):
                fc=c5[ei+j]
                hp=(fc["h"]-entry)/entry*100
                lp=(fc["l"]-entry)/entry*100
                if hp>mfe: mfe=hp; mfe_bar=j
                if lp<mae: mae=lp
            strict = _sim_strict_1m(c1, c1_map, c5, ei, entry,
                                     tpsl["tp"], tpsl["sl"], cost_pct) if use_1m else \
                     _sim_strict_5m(c5, ei, entry, tpsl["tp"], tpsl["sl"], cost_pct)
            exits = {}
            for ename, etype, tp, sl, arm, trail_or_n in EXIT_CONFIGS:
                if etype=="fix": exits[ename]=_sim_fix(c5,ei,entry,tp,sl,mb,cost_pct)
                elif etype=="trail": exits[ename]=_sim_trail(c5,ei,entry,sl,arm,trail_or_n,mb,cost_pct)
                elif etype=="hold":
                    n=int(trail_or_n)
                    if n<=mb:
                        raw=(c5[ei+n]["c"]-entry)/entry*100
                        exits[ename]=round(raw-cost_pct,4)
                elif etype=="time": exits[ename]=_sim_time(c5,ei,entry,sl,int(trail_or_n),mb,cost_pct)
            sig = {"coin":coin,"time":bar["t"],"hour":hour,"entry":entry,
                   "regime":regime,"dip3":dip3,"brk":brk,
                   "mfe":round(mfe,4),"mae":round(mae,4),"mfe_bar":mfe_bar,
                   "exits":exits, "strict":strict, "stype_tpsl":tpsl}
            sig.update(tf_feat_cache.get(bar["t"], {}))
            built.append(sig)
        results[stype] = built

    del c5, c15, c1, c1_map, tf_feat_cache, raw_signals; gc.collect()
    return results

def _sim_strict_1m(c1, c1_map, c5, ei5, entry, tp, sl, cost):
    entry_time = c5[ei5]["t"][:16]
    start_idx = c1_map.get(entry_time)
    if start_idx is None:
        r = _sim_strict_5m(c5, ei5, entry, tp, sl, cost); r["src"]="5m_fb"; return r
    max_bars = min(MAX_HOLD_1M, len(c1) - start_idx - 1)
    if max_bars <= 0: return {"pnl": 0.0, "result": "TIMEOUT", "bars": 0, "src": "1m"}
    for j in range(max_bars + 1):
        fc = c1[start_idx + j]
        hp = (fc["h"] - entry) / entry * 100; lp = (fc["l"] - entry) / entry * 100
        sl_hit = lp <= -sl; tp_hit = hp >= tp
        if sl_hit and tp_hit: return {"pnl": round(-sl - cost, 4), "result": "BOTH_SL", "bars": j, "src": "1m"}
        if sl_hit: return {"pnl": round(-sl - cost, 4), "result": "SL", "bars": j, "src": "1m"}
        if tp_hit: return {"pnl": round(tp - cost, 4), "result": "TP", "bars": j, "src": "1m"}
    last_c = c1[start_idx + max_bars]["c"]
    raw = (last_c - entry) / entry * 100
    return {"pnl": round(raw - cost, 4), "result": "TIMEOUT", "bars": max_bars, "src": "1m"}

def _sim_strict_5m(c5, ei5, entry, tp, sl, cost):
    max_bars = min(2, len(c5) - ei5 - 1)
    if max_bars <= 0: return {"pnl": 0.0, "result": "TIMEOUT", "bars": 0, "src": "5m"}
    for j in range(max_bars + 1):
        fc = c5[ei5 + j]
        hp = (fc["h"] - entry) / entry * 100; lp = (fc["l"] - entry) / entry * 100
        sl_hit = lp <= -sl; tp_hit = hp >= tp
        if sl_hit and tp_hit: return {"pnl": round(-sl - cost, 4), "result": "BOTH_SL", "bars": j, "src": "5m"}
        if sl_hit: return {"pnl": round(-sl - cost, 4), "result": "SL", "bars": j, "src": "5m"}
        if tp_hit: return {"pnl": round(tp - cost, 4), "result": "TP", "bars": j, "src": "5m"}
    last_c = c5[ei5 + max_bars]["c"]
    raw = (last_c - entry) / entry * 100
    return {"pnl": round(raw - cost, 4), "result": "TIMEOUT", "bars": max_bars, "src": "5m"}

def _sim_fix(c5,ei,entry,tp,sl,mb,cost):
    for j in range(1,mb+1):
        fc=c5[ei+j]
        if (fc["l"]-entry)/entry*100<=-sl: return round(-sl-cost,4)
        if (fc["h"]-entry)/entry*100>=tp: return round(tp-cost,4)
    return round((c5[ei+mb]["c"]-entry)/entry*100-cost,4)

def _sim_trail(c5,ei,entry,sl,arm,trail,mb,cost):
    mp,armed=0,False
    for j in range(1,mb+1):
        fc=c5[ei+j]
        hp=(fc["h"]-entry)/entry*100; lp=(fc["l"]-entry)/entry*100
        if hp>mp: mp=hp
        if lp<=-sl: return round(-sl-cost,4)
        if mp>=arm: armed=True
        if armed and mp-lp>=trail: return round(max(mp-trail,-sl)-cost,4)
    return round((c5[ei+mb]["c"]-entry)/entry*100-cost,4)

def _sim_time(c5,ei,entry,sl,n,mb,cost):
    hold=min(n,mb)
    for j in range(1,hold+1):
        if (c5[ei+j]["l"]-entry)/entry*100<=-sl: return round(-sl-cost,4)
    return round((c5[ei+hold]["c"]-entry)/entry*100-cost,4)

# ================================================================
# PART 4: 통계 + 리포트
# ================================================================
def calc_m(pnls):
    if not pnls: return None
    n=len(pnls); w=[p for p in pnls if p>0]; l=[p for p in pnls if p<=0]
    wr=len(w)/n*100; ev=sum(pnls)/n
    aw=sum(w)/len(w) if w else 0; al=abs(sum(l)/len(l)) if l else 0
    rr=aw/al if al>0 else 999
    tg_=sum(w); tl=abs(sum(l)); pf=tg_/tl if tl>0 else 999
    cum=pk=mdd=0
    for p in pnls:
        cum+=p
        if cum>pk: pk=cum
        dd=pk-cum
        if dd>mdd: mdd=dd
    return {"n":n,"wr":wr,"ev":ev,"pf":pf,"mdd":mdd,"rr":rr,"aw":aw,"al":al,"tp":sum(pnls)}

def fmt(m):
    if not m: return "N/A"
    pf=f"{m['pf']:.2f}" if m['pf']<999 else "INF"
    rr=f"{m['rr']:.2f}" if m['rr']<999 else "INF"
    return f"n={m['n']:>4d} EV={m['ev']:>+.4f}% PF={pf:>5s} MDD={m['mdd']:>.3f}% WR={m['wr']:>5.1f}% RR={rr:>5s} Tot={m['tp']:>+.2f}%"

def _pct(vals, p):
    if not vals: return 0.0
    s=sorted(vals); i=int(len(s)*p/100)
    return s[min(i,len(s)-1)]

def _avg(vals):
    return sum(vals)/len(vals) if vals else 0.0

def generate_report(all_results, dist_acc):
    L = []
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    L.append(f"업비트 신호 연구 v3.3 ({ts})")
    L.append(f"비용:{TOTAL_COST*100:.2f}% | 진입:다음봉시가 | 최대:{MAX_HOLD_BARS}봉")
    L.append(f"Train/Test:{TRAIN_RATIO*100:.0f}/{(1-TRAIN_RATIO)*100:.0f} | 최소:{MIN_TRADES}건")
    L.append("="*65)
    tf_feat_counts = defaultdict(int)
    for (tf, fk) in dist_acc: tf_feat_counts[tf] += 1
    total_feats = sum(tf_feat_counts.values())
    L.append(f"총 피처: {total_feats}개 ({', '.join(f'{tf}m:{v}개' for tf,v in sorted(tf_feat_counts.items()))})")
    dk="TRAIL_SL0.7/A0.3/T0.2"

    # [0] 엄격 판정
    L.append(f"\n[0] 엄격 판정 (1분봉 기준, 10분 제한, 시그널별 고정 TP/SL)")
    L.append("-"*65)
    L.append(f"  {'신호유형':<14s} {'TP/SL':>7s} {'n':>5s} {'EV':>8s} {'Hit%':>5s} {'WR%':>5s} {'TP':>5s} {'SL':>5s} {'TO':>5s} {'양면':>4s} {'봉':>4s} {'1m':>4s} {'5m':>4s}")
    L.append(f"  {'-'*95}")
    total_1m = total_5m = 0
    for st, sigs in all_results.items():
        if not sigs: continue
        stricts = [s["strict"] for s in sigs if "strict" in s and s["strict"]]
        if not stricts: continue
        tpsl = SIG_TPSL.get(st, {"tp":1.0,"sl":0.7})
        n = len(stricts); pnls = [s["pnl"] for s in stricts]
        ev = sum(pnls) / n if n else 0
        wins = sum(1 for p in pnls if p > 0); wr = wins / n * 100 if n else 0
        tp_cnt = sum(1 for s in stricts if s["result"]=="TP")
        sl_cnt = sum(1 for s in stricts if s["result"]=="SL")
        to_cnt = sum(1 for s in stricts if s["result"]=="TIMEOUT")
        both_cnt = sum(1 for s in stricts if s["result"]=="BOTH_SL")
        avg_bars = sum(s["bars"] for s in stricts) / n if n else 0
        hit_rate = tp_cnt / n * 100 if n else 0
        n_1m = sum(1 for s in stricts if s.get("src")=="1m")
        n_5m = sum(1 for s in stricts if s.get("src") in ("5m","5m_fb"))
        total_1m += n_1m; total_5m += n_5m
        tp_pct=tp_cnt/n*100; sl_pct=sl_cnt/n*100; to_pct=to_cnt/n*100; both_pct=both_cnt/n*100
        star=" ***" if ev>0 and n>=MIN_TRADES else ""
        L.append(f"  {st:<14s} {tpsl['tp']:+.1f}/{tpsl['sl']:-.1f} {n:5d} {ev:+8.4f}% {hit_rate:5.1f} {wr:5.1f} {tp_pct:5.1f} {sl_pct:5.1f} {to_pct:5.1f} {both_pct:4.1f} {avg_bars:4.1f} {n_1m:4d} {n_5m:4d}{star}")
        si = int(n * TRAIN_RATIO)
        for nm, sub_s in [("TR", stricts[:si]), ("TE", stricts[si:])]:
            if len(sub_s)<10: continue
            sn=len(sub_s); sev=sum(s["pnl"] for s in sub_s)/sn
            swr=sum(1 for s in sub_s if s["pnl"]>0)/sn*100
            s_hit=sum(1 for s in sub_s if s["result"]=="TP")/sn*100
            L.append(f"    {nm}: n={sn:4d} EV={sev:+.4f}% Hit={s_hit:.1f}% WR={swr:.1f}%")
    if (total_1m+total_5m)>0:
        L.append(f"  -- 데이터소스: 1분봉={total_1m}건, 5분봉fb={total_5m}건 ({total_1m/(total_1m+total_5m)*100:.0f}%/{total_5m/(total_1m+total_5m)*100:.0f}%)")

    # [1] 탐색용 비교
    L.append(f"\n[1] 탐색용 비교 ({dk})")
    L.append("-"*65)
    ranked=[]
    for st,sigs in all_results.items():
        if not sigs: continue
        pnl_list=[s["exits"].get(dk,0) for s in sigs if s.get("exits")]
        if not pnl_list: continue
        m=calc_m(pnl_list)
        if m: ranked.append((st,m))
    ranked.sort(key=lambda x:-x[1]["ev"])
    for st,m in ranked:
        star=" ***" if m["ev"]>0 and m["n"]>=MIN_TRADES else ""
        L.append(f"  {st:<16s} {fmt(m)}{star}")

    # [2] TF별 지표 분포
    L.append(f"\n{'='*65}")
    L.append("[2] TF별 지표 분포")
    L.append("-"*65)
    key_indicators = [
        ("rsi7","RSI7"),("rsi14","RSI14"),("rsi21","RSI21"),
        ("bb10","BB10위치"),("bb20","BB20위치"),("bbw10","BB10폭"),("bbw20","BB20폭"),
        ("macd_x","MACD골든%"),("macd_h","MACD히스토"),
        ("stoch_k","Stoch%K"),("stoch_d","Stoch%D"),
        ("atr","ATR%"),("adx","ADX"),("cci","CCI"),("willr","W%R"),("mfi","MFI"),
        ("obv","OBV변화"),("disp10","이격도10"),("disp20","이격도20"),
        ("ema_al","EMA정배열"),
        ("ed5","EMA5거리"),("ed10","EMA10거리"),("ed20","EMA20거리"),("ed50","EMA50거리"),
        ("vol10","변동성10"),("vol20","변동성20"),
        ("vr5","VR5"),("vr20","VR20"),
        ("m3","모멘텀3"),("m5","모멘텀5"),("m10","모멘텀10"),("m20","모멘텀20"),
        ("pos10","위치10"),("pos20","위치20"),("bar_atr","봉/ATR"),
        ("hammer","망치%"),("inv_ham","역망치%"),("doji","도지%"),
        ("engulf","감싸기%"),("mstar","샛별%"),
        ("cg","연속양봉"),("cr","연속음봉"),
        ("uw_pct","윗꼬리%"),("lw_pct","아랫꼬리%"),("body_pct","몸통%"),("green","양봉%"),
    ]
    detail_tfs={5,15,60}
    summary_indicators={"rsi14","bb20","stoch_k","macd_x","adx","mfi","ema_al","disp20","vr5","m3","m10","pos20","vol20","hammer","engulf"}
    for tf in ANALYSIS_TFS:
        has_data=(tf,"rsi14") in dist_acc and len(dist_acc[(tf,"rsi14")])>0
        if not has_data: continue
        n_data=len(dist_acc.get((tf,"rsi14"),[]))
        is_detail=tf in detail_tfs
        show_indicators=key_indicators if is_detail else [(k,n) for k,n in key_indicators if k in summary_indicators]
        mode="상세" if is_detail else "요약"
        L.append(f"\n  [{tf}분봉] [{mode}] (n={n_data}):")
        for feat_key, feat_name in show_indicators:
            vals=dist_acc.get((tf,feat_key),[])
            if not vals: continue
            if feat_key in ("macd_x","hammer","inv_ham","doji","engulf","mstar","green"):
                pct=sum(1 for v in vals if v==1)/len(vals)*100
                L.append(f"    {feat_name:12s} 발생={pct:5.1f}%")
            elif feat_key in ("cg","cr","ema_al"):
                L.append(f"    {feat_name:12s} avg={_avg(vals):+.1f} P50={_pct(vals,50):.0f} P75={_pct(vals,75):.0f}")
            else:
                L.append(f"    {feat_name:12s} avg={_avg(vals):+.2f} P25={_pct(vals,25):+.2f} P50={_pct(vals,50):+.2f} P75={_pct(vals,75):+.2f} P95={_pct(vals,95):+.2f}")

    # [3] 신호유형별 상세
    for st, sigs in all_results.items():
        if len(sigs)<30: continue
        L.append(f"\n{'='*65}")
        L.append(f"[3] {st} (n={len(sigs)})")
        si=int(len(sigs)*TRAIN_RATIO); tr,te=sigs[:si],sigs[si:]
        ek_list=sorted(set(k for s in sigs for k in s["exits"]))
        for nm,sub in [("TRAIN",tr),("TEST",te),("ALL",sigs)]:
            if len(sub)<10: continue
            L.append(f"\n  [{nm} n={len(sub)}]")
            L.append(f"  청산전략 TOP5:")
            er=[]
            for ek in ek_list:
                m=calc_m([s["exits"].get(ek,0) for s in sub])
                if m: er.append((ek,m))
            er.sort(key=lambda x:-x[1]["ev"])
            for ek,m in er[:5]:
                star=" ***" if m["ev"]>0 else ""
                L.append(f"    {ek:25s} {fmt(m)}{star}")
            if nm=="ALL":
                mfes=sorted(s["mfe"] for s in sub); maes=sorted(s["mae"] for s in sub)
                L.append(f"\n  MFE: P25={mfes[len(mfes)//4]:+.3f}% P50={mfes[len(mfes)//2]:+.3f}% P75={mfes[3*len(mfes)//4]:+.3f}%")
                L.append(f"  MAE: P25={maes[len(maes)//4]:+.3f}% P50={maes[len(maes)//2]:+.3f}% P75={maes[3*len(maes)//4]:+.3f}%")
        # 조건별 EV
        L.append(f"\n  [조건별 EV] ({dk})")
        def ce(name,fn):
            sub=[s for s in sigs if fn(s)]
            if len(sub)<10: return
            m=calc_m([s["exits"].get(dk,0) for s in sub])
            if m:
                star=" ***" if m["ev"]>0 and m["n"]>=MIN_TRADES else ""
                L.append(f"    {name:35s} {fmt(m)}{star}")
        for tf in ANALYSIS_TFS:
            p=f"tf{tf}_"
            has=[s for s in sigs if p+"rsi14" in s]
            if len(has)<30: continue
            L.append(f"\n    [{tf}분봉 조건]")
            for lo_v,hi_v in [(0,30),(30,50),(50,70),(70,100)]:
                ce(f"{tf}m RSI14 {lo_v}-{hi_v}", lambda s,lo_v=lo_v,hi_v=hi_v,k=p+"rsi14": k in s and lo_v<=s[k]<hi_v)
            for lo_v,hi_v in [(0,20),(20,50),(50,80),(80,100)]:
                ce(f"{tf}m BB20 {lo_v}-{hi_v}", lambda s,lo_v=lo_v,hi_v=hi_v,k=p+"bb20": k in s and lo_v<=s[k]<hi_v)
            ce(f"{tf}m Stoch_K<20", lambda s,k=p+"stoch_k": k in s and s[k]<20)
            ce(f"{tf}m MACD골든", lambda s,k=p+"macd_x": k in s and s[k]==1)
            ce(f"{tf}m ADX>25", lambda s,k=p+"adx": k in s and s[k]>25)
            ce(f"{tf}m MFI<20", lambda s,k=p+"mfi": k in s and s[k]<20)
            ce(f"{tf}m EMA정배열3+", lambda s,k=p+"ema_al": k in s and s[k]>=3)
            ce(f"{tf}m VR5>3", lambda s,k=p+"vr5": k in s and s[k]>3)
            ce(f"{tf}m 망치형", lambda s,k=p+"hammer": k in s and s[k]==1)
            ce(f"{tf}m 감싸기", lambda s,k=p+"engulf": k in s and s[k]==1)
        # BTC 레짐
        L.append(f"\n    [BTC 레짐]")
        for r in ["상승","횡보","하락"]:
            ce(f"BTC_{r}", lambda s,r=r: s["regime"]==r)
        # 복합필터
        L.append(f"\n    [복합필터]")
        combos=[
            ("5m_RSI<50+15m_BB<40", lambda s: s.get("tf5_rsi14",50)<50 and s.get("tf15_bb20",50)<40),
            ("5m_MACD골든+15m_ADX>25", lambda s: s.get("tf5_macd_x")==1 and s.get("tf15_adx",0)>25),
            ("15m_MACD골든+1h_EMA정배열", lambda s: s.get("tf15_macd_x")==1 and s.get("tf60_ema_al",0)>=3),
            ("15m_BB<20+BTC상승", lambda s: s.get("tf15_bb20",50)<20 and s["regime"]=="상승"),
            ("5m_RSI<40+BTC상승+15m_MACD골든", lambda s: s.get("tf5_rsi14",50)<40 and s["regime"]=="상승" and s.get("tf15_macd_x")==1),
            ("5m_망치+15m_과매도", lambda s: s.get("tf5_hammer")==1 and s.get("tf15_rsi14",50)<40),
        ]
        for name,fn in combos: ce(name,fn)
        # Train→Test
        L.append(f"\n  [Train→Test 검증]")
        validated=[]
        for fname,ffn in combos:
            trsub=[s for s in tr if ffn(s)]; tesub=[s for s in te if ffn(s)]
            if len(trsub)<10 or len(tesub)<3: continue
            best_ek,best_ev=None,-999
            for ek in ek_list:
                m=calc_m([s["exits"].get(ek,0) for s in trsub])
                if m and m["ev"]>best_ev: best_ev=m["ev"]; best_ek=ek
            if best_ek:
                trm=calc_m([s["exits"].get(best_ek,0) for s in trsub])
                tem=calc_m([s["exits"].get(best_ek,0) for s in tesub])
                L.append(f"    {fname}")
                L.append(f"      TR: {fmt(trm)}")
                L.append(f"      TE: {fmt(tem)}")
                if trm and tem and trm["ev"]>0 and tem["ev"]>0:
                    L.append(f"      >>> BOTH EV>0 <<<"); validated.append((fname,best_ek,trm,tem))
        if validated: L.append(f"\n  ** 검증 통과 {len(validated)}개 **")

    # [4] 자동 탐색
    L.append(f"\n{'='*65}")
    L.append("[4] 자동 탐색 — TEST EV TOP10")
    all_sigs=[]
    for sigs in all_results.values(): all_sigs.extend(sigs)
    if all_sigs:
        si=int(len(all_sigs)*TRAIN_RATIO); all_te=all_sigs[si:]
        auto_results=[]
        for tf in ANALYSIS_TFS:
            pfx=f"tf{tf}_"
            for feat_key in ["rsi14","rsi7","bb20","stoch_k","adx","mfi","disp20","m3","m10","pos20","vr5","vol20","macd_h"]:
                full_key=pfx+feat_key
                vals=[s[full_key] for s in all_te if full_key in s]
                if len(vals)<50: continue
                p33,p66=_pct(vals,33),_pct(vals,66)
                for lo_v,hi_v,label in [(float('-inf'),p33,"하위33%"),(p33,p66,"중위33%"),(p66,float('inf'),"상위33%")]:
                    sub=[s for s in all_te if full_key in s and lo_v<=s[full_key]<hi_v]
                    if len(sub)<15: continue
                    m=calc_m([s["exits"].get(dk,0) for s in sub])
                    if m and m["ev"]>0: auto_results.append((f"{tf}m_{feat_key}_{label}",m))
        auto_results.sort(key=lambda x:-x[1]["ev"])
        for name,m in auto_results[:10]:
            L.append(f"  {name:35s} {fmt(m)}")

    # [5] 결론
    L.append(f"\n{'='*65}")
    L.append("[5] 결론 — TEST EV>0 조합")
    found=False
    for st,sigs in all_results.items():
        if len(sigs)<MIN_TRADES: continue
        si=int(len(sigs)*TRAIN_RATIO); te=sigs[si:]
        for ek in sorted(set(k for s in sigs for k in s["exits"])):
            m=calc_m([s["exits"].get(ek,0) for s in te])
            if m and m["ev"]>0 and m["n"]>=20:
                L.append(f"  {st} + {ek}: {fmt(m)}"); found=True
    if not found: L.append("  >>> TEST EV>0 조합 없음 <<<")
    L.append(f"\n{'='*65}\n끝.")
    return L

# ================================================================
# 메인
# ================================================================
def _has_enough_data(min_coins=10):
    if not os.path.exists(DATA_DIR): return False
    files_5m = [f for f in os.listdir(DATA_DIR) if f.endswith("_5m.json")]
    files_1m = [f for f in os.listdir(DATA_DIR) if f.endswith("_1m.json")]
    return len(files_5m) >= min_coins and len(files_1m) >= min_coins

def main():
    _acquire_lock()
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--coins", type=int, default=30)
    parser.add_argument("--skip-collect", action="store_true")
    args = parser.parse_args()

    tg("[시작] upbit_signal_study v3.3 실행 (1분봉 30일 포함)")
    t0 = time.time()
    last_hb = t0

    # 데이터 있으면 수집 스킵
    if not args.skip_collect and _has_enough_data():
        tg("[자동] 기존 데이터 감지 → 수집 스킵")
        args.skip_collect = True

    if not args.skip_collect:
        tg("[STEP 1/4] 전체 TF 수집 (1분봉 30일 청크방식 포함)")
        collect(days=args.days, top_n=args.coins)

    # 분석
    coins = get_saved_coins()
    if not coins:
        tg("[오류] 데이터 없음")
        _release_lock()
        tg("\n프로그램을 종료합니다.")
        sys.exit(0)

    tg(f"\n[STEP 2/4] 분석 시작: {len(coins)}코인")
    btc5 = load_candles("BTC", 5)
    btc_regime = {}
    if btc5:
        for i in range(60, len(btc5)):
            ret=(btc5[i]["c"]-btc5[i-60]["c"])/btc5[i-60]["c"]*100
            t=btc5[i]["t"][:16]
            btc_regime[t]="상승" if ret>0.5 else ("하락" if ret<-0.5 else "횡보")
        tg(f"  BTC 레짐: {len(btc_regime)}시점")
    del btc5; gc.collect()

    all_results = defaultdict(list)
    dist_acc = defaultdict(list)
    analysis_start = time.time()
    for i, coin in enumerate(coins):
        # 하트비트 (분석 중간)
        now = time.time()
        if now - last_hb >= HEARTBEAT_INTERVAL:
            mem = _get_mem_mb()
            mem_str = f" | mem={mem:.0f}MB" if mem > 0 else ""
            tg(f"[살아있음] 분석 진행중 {i+1}/{len(coins)} | {int(now-t0)}초 경과{mem_str}")
            last_hb = now

        coin_t0 = time.time()
        try:
            cr = process_coin(coin, btc_regime, dist_acc)
            for st,sigs in cr.items():
                all_results[st].extend(sigs)
            del cr; gc.collect()
        except Exception as e:
            import traceback
            tg(f"[분석에러] {coin}: {e}\n{traceback.format_exc()[-300:]}")
        coin_elapsed = time.time() - coin_t0
        if (i+1) % 10 == 0:
            total_elapsed = time.time() - analysis_start
            avg_per_coin = total_elapsed / (i+1)
            remaining = avg_per_coin * (len(coins) - i - 1)
            tg(f"  분석 {i+1}/{len(coins)} 완료 ({total_elapsed:.0f}초, 예상잔여 {remaining:.0f}초)")

    for st in all_results:
        all_results[st].sort(key=lambda s: s["time"])

    tg(f"\n[STEP 3/4] 신호 감지 완료")
    total_sigs = 0
    for st,sigs in all_results.items():
        tg(f"  {st}: {len(sigs)}건"); total_sigs += len(sigs)
    tg(f"  총: {total_sigs}건")

    # 리포트
    tg("[STEP 4/4] 리포트 생성")
    try:
        lines = generate_report(dict(all_results), dict(dist_acc))
    except Exception as e:
        import traceback
        tg(f"[리포트에러] {e}\n{traceback.format_exc()[-500:]}")
        lines = [f"에러: {e}"]
    del dist_acc; gc.collect()

    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now(KST).strftime("%Y%m%d_%H%M")
    fpath = os.path.join(OUT_DIR, f"signal_v3_{ts}.txt")
    with open(fpath,"w",encoding="utf-8") as f: f.write("\n".join(lines))
    tg(f"\n리포트: {fpath}")

    # 분할 전송
    cur=""; chunks=[]
    for line in lines:
        if len(cur)+len(line)+1>3800: chunks.append(cur); cur=line
        else: cur=cur+"\n"+line if cur else line
    if cur: chunks.append(cur)
    for ci,ch in enumerate(chunks):
        tg(f"[리포트 {ci+1}/{len(chunks)}]\n{ch}")
        time.sleep(3 if len(chunks) > 10 else 1)

    # ===== 확실한 종료 =====
    total_elapsed = time.time() - t0
    finish_time = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    _mark_done()  # 완료 마커 기록 (재실행 방지)
    tg(
        f"\n{'='*50}\n"
        f"[최종 완료]\n"
        f"  소요시간: {total_elapsed/60:.1f}분\n"
        f"  종료시각: {finish_time}\n"
        f"{'='*50}\n"
        f"모든 작업 완료. 프로그램을 종료합니다. ({DONE_COOLDOWN//60}분간 재실행 차단)"
    )
    _release_lock()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _release_lock()
        tg("[중단] 사용자 중단. 종료합니다.")
        sys.exit(0)
    except Exception as e:
        import traceback
        _release_lock()
        tg(f"[에러] {e}\n{traceback.format_exc()[-800:]}")
        tg("에러 발생. 프로그램을 종료합니다.")
        sys.exit(1)