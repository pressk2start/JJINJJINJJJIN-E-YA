# -*- coding: utf-8 -*-
"""
업비트 신호 연구 v4.0 — 1분봉 전용 고속수집
=============================================
- 수집: 일별 gzip jsonl + 4 worker 병렬 + 증분수집
- 분석: 1분봉 피처 추출, 시그널은 c5(디스크 or 1m합성) 기반
- 완료 후 확실한 종료 (완료 마커로 재실행 방지)
- PID 검증 + flock 이중 잠금
"""
import requests, time, json, os, sys, gc, argparse, math, atexit, signal as sig_mod, fcntl
import gzip, threading, ctypes
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
KST = timezone(timedelta(hours=9))
BASE = "https://api.upbit.com/v1"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "candle_data")
OUT_DIR = os.path.join(SCRIPT_DIR, "analysis_output")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".study_lock")
DONE_FILE = os.path.join(SCRIPT_DIR, ".study_done")

# 하트비트 설정
HEARTBEAT_INTERVAL = 90  # 90초마다 텔레그램 살아있음 알람
DONE_COOLDOWN = 3600      # 완료 후 재실행 방지 시간 (1시간)
NUM_WORKERS = 4           # 병렬 수집 워커 수
RATE_LIMIT_PER_SEC = 8    # 전역 초당 최대 API 요청 수

# ── 완료 마커 (재실행 방지) ──
def _mark_done():
    """분석 완료 시 타임스탬프 기록"""
    try:
        with open(DONE_FILE, "w") as f:
            f.write(f"{time.time()}\n{os.getpid()}\n{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    except: pass
    # 정상 완료 → 크래시 카운터 리셋
    try: os.remove(CRASH_FILE)
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

# ── 중복 실행 방지 (시작 마커 파일 기반 — SIGKILL에도 안전) ──
_lock_fd = None
RUNNING_FILE = os.path.join(SCRIPT_DIR, ".study_running")  # 시작 마커 (SIGKILL에도 남음)
CRASH_FILE = os.path.join(SCRIPT_DIR, ".study_crashes")    # 연속 크래시 카운터
MAX_CRASHES = 3  # 연속 크래시 이 횟수 초과 시 재시작 차단

def _is_pid_alive(pid):
    """해당 PID가 살아있는지 확인"""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _acquire_lock():
    """시작 마커 파일 + flock으로 중복 방지. SIGKILL에도 안전."""
    global _lock_fd

    # 0단계: 최근 완료됐으면 즉시 종료 (atexit 우회)
    if _is_recently_done():
        print(f"[스킵] 최근 완료됨 (쿨다운 {DONE_COOLDOWN}초). 재실행 불필요.")
        os._exit(0)

    # 0-1단계: 연속 크래시 감지 → 무한 재시작 루프 방지
    try:
        if os.path.exists(CRASH_FILE):
            with open(CRASH_FILE) as f:
                parts = f.read().strip().split("\n")
            crash_count = int(parts[0]) if parts else 0
            crash_ts = float(parts[1]) if len(parts) > 1 else 0
            # 30분 이상 지나면 카운터 리셋
            if time.time() - crash_ts > 1800:
                crash_count = 0
                os.remove(CRASH_FILE)
            elif crash_count >= MAX_CRASHES:
                print(f"[차단] 연속 크래시 {crash_count}회. 30분 후 자동 리셋됩니다.")
                tg(f"[크래시루프] 연속 {crash_count}회 크래시 감지. 자동 재시작 차단.\n"
                   f"수동: rm .study_crashes 후 재시작")
                os._exit(0)
    except (ValueError, FileNotFoundError):
        pass

    # 1단계: 시작 마커 파일 검증 (SIGKILL에도 남아있음)
    try:
        if os.path.exists(RUNNING_FILE):
            with open(RUNNING_FILE) as f:
                lines = f.read().strip().split("\n")
            old_pid = int(lines[0]) if lines else 0
            run_start = float(lines[1]) if len(lines) > 1 else 0
            run_age = time.time() - run_start if run_start > 0 else 99999

            if old_pid and old_pid != os.getpid():
                if _is_pid_alive(old_pid):
                    # 프로세스 살아있음 → 차단 (print만, TG 스팸 방지)
                    print(f"[잠금] PID {old_pid} 실행중 ({run_age:.0f}초 경과). 새 인스턴스 차단.")
                    os._exit(0)  # atexit 우회 → [비정상종료] 오탐 방지
                elif run_age < 300:
                    # 프로세스 죽었지만 5분 이내 시작 → 재시작 루프 방지
                    print(f"[잠금] 최근 비정상종료 ({run_age:.0f}초 전). 5분 쿨다운. 재시작 차단.")
                    os._exit(0)
                # 5분 이상 지났고 프로세스 죽음 → stale, 새로 시작 허용
                print(f"[락해제] 이전 프로세스 PID {old_pid} 사망 ({run_age:.0f}초 전). 새로 시작.")
    except (ValueError, FileNotFoundError):
        pass

    # 2단계: flock (레이스 컨디션 방지)
    try:
        _lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
    except (IOError, OSError):
        print("[잠금] 다른 인스턴스가 실행중 (flock). 종료.")
        os._exit(0)

    # 3단계: 시작 마커 기록 (SIGKILL에도 파일로 남음)
    try:
        with open(RUNNING_FILE, "w") as f:
            f.write(f"{os.getpid()}\n{time.time():.0f}")
    except: pass

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
    try: os.remove(RUNNING_FILE)
    except: pass

def _death_handler(signum, frame):
    """프로세스 종료 시그널 포착 → 텔레그램으로 원인 전송.
    락 파일은 삭제하지 않음 (시간 기반으로 재시작 루프 방지)."""
    import signal as _sig
    sig_name = _sig.Signals(signum).name if hasattr(_sig, 'Signals') else str(signum)
    mem = _get_mem_mb()
    try:
        tg(f"[강제종료] 시그널={sig_name}({signum}) | mem={mem:.0f}MB\n"
           f"프로세스가 외부에서 kill 되었습니다. (5분간 재시작 차단)")
    except: pass
    # 락 파일 남겨둠 → _acquire_lock의 시간 기반 보호로 재시작 루프 방지
    # flock만 해제 (파일은 삭제 안 함)
    global _lock_fd
    try:
        if _lock_fd:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
            _lock_fd = None
    except: pass
    sys.exit(1)

def _increment_crash():
    """크래시 카운터 증가"""
    try:
        count = 0
        if os.path.exists(CRASH_FILE):
            with open(CRASH_FILE) as f:
                parts = f.read().strip().split("\n")
            count = int(parts[0]) if parts else 0
        count += 1
        with open(CRASH_FILE, "w") as f:
            f.write(f"{count}\n{time.time():.0f}")
    except: pass

def _atexit_diag():
    """atexit: 정상종료가 아닌 경우 알림.
    비정상 종료 시에는 락 파일을 보존하여 재시작 루프를 방지."""
    if not os.path.exists(DONE_FILE):
        _increment_crash()
        mem = _get_mem_mb()
        try:
            tg(f"[비정상종료] _mark_done() 호출 없이 종료됨 | mem={mem:.0f}MB\n"
               f"완료 전에 프로세스가 죽었습니다. (5분간 재시작 차단)")
        except: pass
        # 비정상 종료 → 락 파일 보존 (시간 기반 재시작 차단)
        global _lock_fd
        try:
            if _lock_fd:
                fcntl.flock(_lock_fd, fcntl.LOCK_UN)
                _lock_fd.close()
                _lock_fd = None
        except: pass
    else:
        _release_lock()

atexit.register(_atexit_diag)
for _s in (sig_mod.SIGTERM, sig_mod.SIGINT, sig_mod.SIGHUP):
    sig_mod.signal(_s, _death_handler)

FEE_RATE = 0.0005
SLIPPAGE = 0.0008
TOTAL_COST = FEE_RATE * 2 + SLIPPAGE
MIN_TRADES = 100
TRAIN_RATIO = 0.7
MAX_HOLD_BARS = 60
MAX_HOLD_1M = 10

# ── 워크포워드 검증 설정 ──
WF_TRAIN_DAYS = 21   # 학습 윈도우 (일) — 60일 데이터용, 3주
WF_TEST_DAYS  = 7    # 검증 윈도우 (일)
WF_STEP_DAYS  = 5    # 슬라이딩 스텝 (일) — 겹침 증가로 폴드 수 확보
WF_MIN_TRAIN  = 20   # fold 내 최소 학습 시그널 수
WF_MIN_TEST   = 5    # fold 내 최소 검증 시그널 수
MAX_SIGS_PER_COIN = 250  # OOM 방지: 코인당 시그널 수 제한

COND_KEYS = {
    "rsi14","rsi7","bb20","bb10","stoch_k","stoch_d",
    "macd_x","macd_h","adx","cci","willr","mfi",
    "ema_al","disp10","disp20","ed5","ed20","ed50",
    "vr5","vr20","m3","m5","m10","m20",
    "pos10","pos20","vol10","vol20",
    "hammer","inv_ham","engulf","mstar","doji","cg","cr",
    "bar_atr","green","bbw20","uw_pct","lw_pct","body_pct",
}

COLLECT_TFS = []  # 3m~60m은 다른 스크립트가 수집 → 여기선 1m만
ALL_TFS = [(1, 1440)]
ANALYSIS_TFS = [5, 15, 60]  # 1분봉 피처 제외 (메모리 절감, c1 조기 해제)

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

def _force_free():
    """gc.collect() + malloc_trim: Python이 해제한 메모리를 OS에 실제 반환"""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except: pass

# ── requests.Session (TCP/TLS 재사용) ──
_thread_local = threading.local()

def get_session():
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0"})
        _thread_local.session = s
    return _thread_local.session

# ── 전역 rate limiter (thread-safe) ──
_rate_lock = threading.Lock()
_rate_timestamps = []  # 최근 요청 시각들

def _rate_wait():
    """초당 RATE_LIMIT_PER_SEC 이하로 요청 제한 (lock 밖에서 sleep)"""
    while True:
        with _rate_lock:
            now = time.time()
            while _rate_timestamps and _rate_timestamps[0] < now - 1.0:
                _rate_timestamps.pop(0)
            if len(_rate_timestamps) < RATE_LIMIT_PER_SEC:
                _rate_timestamps.append(now)
                return
            wait = 1.0 - (now - _rate_timestamps[0])
        # lock 해제 후 sleep → 다른 워커 블로킹 안 함
        if wait > 0:
            time.sleep(wait)
        else:
            time.sleep(0.01)

def safe_get(url, params=None, retries=4, timeout=10):
    for i in range(retries):
        _rate_wait()
        try:
            r = get_session().get(url, params=params, timeout=timeout)
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

# ── 일별 gzip jsonl 저장 구조 ──
# DATA_DIR/BTC/2026-03-01.jsonl.gz, DATA_DIR/BTC/2026-03-02.jsonl.gz ...
def _coin_dir(coin):
    return os.path.join(DATA_DIR, coin)

def _day_path(coin, date_str):
    """date_str: 'YYYY-MM-DD'"""
    return os.path.join(_coin_dir(coin), f"{date_str}.jsonl.gz")

def _get_existing_dates(coin):
    """이미 수집된 날짜 set 반환"""
    d = _coin_dir(coin)
    if not os.path.exists(d): return set()
    dates = set()
    for f in os.listdir(d):
        if f.endswith(".jsonl.gz") and len(f) == 19:  # YYYY-MM-DD.jsonl.gz
            dates.add(f[:10])
    return dates

def _save_day_file(coin, date_str, rows):
    """하루치 캔들을 gzip jsonl로 저장"""
    d = _coin_dir(coin)
    os.makedirs(d, exist_ok=True)
    fpath = _day_path(coin, date_str)
    tmp = fpath + f".{os.getpid()}.tmp"
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, fpath)
    except Exception:
        try: os.remove(tmp)
        except OSError: pass
        raise

def _load_day_file(fpath):
    """일별 gzip jsonl 파일 로드"""
    rows = []
    try:
        with gzip.open(fpath, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception:
        pass
    return rows

_GLOBAL_DAYS = 45  # main()에서 args.days로 갱신

def _load_coin_1m(coin, days=None):
    """코인의 일별 파일들을 합쳐서 시간순 캔들 리스트 반환"""
    if days is None:
        days = _GLOBAL_DAYS
    d = _coin_dir(coin)
    if not os.path.exists(d): return []
    files = sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz"))
    if not files: return []
    # 최근 days일치만
    cutoff = (datetime.now(KST) - timedelta(days=days+1)).strftime("%Y-%m-%d")
    all_candles = []
    for fname in files:
        if fname[:10] < cutoff: continue
        fpath = os.path.join(d, fname)
        all_candles.extend(_load_day_file(fpath))
    # 이미 날짜별 파일이라 대부분 정렬됨, 안전하게 정렬
    all_candles.sort(key=lambda x: x["t"])
    return all_candles

def _fetch_one_coin(market, coin, days, existing_dates):
    """단일 코인 1분봉 수집 (증분: 없는 날짜만, 오늘은 항상 갱신)"""
    today = datetime.now(KST).date()
    today_str = today.strftime("%Y-%m-%d")
    target_dates = set()
    for d in range(days):
        dt = today - timedelta(days=d)
        target_dates.add(dt.strftime("%Y-%m-%d"))
    # 오늘은 항상 다시 받는다 (부분 데이터 갱신)
    existing_dates = set(existing_dates)
    existing_dates.discard(today_str)
    missing = sorted(target_dates - existing_dates, reverse=True)  # 최신→과거
    if not missing:
        return 0, 0, "skip"

    total_saved = 0
    total_pages = 0
    day_buffer = {}  # date_str → list of candles
    to = None
    fails = 0
    start_ts = time.time()

    # 최신부터 과거로 수집 (Upbit 기본 순서)
    while True:
        if time.time() - start_ts > 900:
            break
        params = {"market": market, "count": 200}
        if to: params["to"] = to

        data = safe_get(f"{BASE}/candles/minutes/1", params=params, retries=5, timeout=12)
        if not data:
            fails += 1
            if fails >= 5: break
            time.sleep(0.5 + fails * 0.3)
            continue
        fails = 0
        total_pages += 1

        oldest_date = None
        for c in data:
            row = compress(c)
            date_str = row["t"][:10]  # "YYYY-MM-DD"
            oldest_date = date_str
            if date_str in existing_dates:
                continue  # 이미 있는 날짜의 캔들 → 스킵
            if date_str not in target_dates:
                continue  # 범위 밖
            if date_str not in day_buffer:
                day_buffer[date_str] = []
            day_buffer[date_str].append(row)

        if len(data) < 200: break
        to = data[-1]["candle_date_time_utc"] + "Z"
        del data

        # 수집 범위를 벗어나면 중단 (가장 오래된 날짜가 target 밖)
        if oldest_date and oldest_date < min(missing):
            break

        # 완료된 날짜 즉시 저장 (메모리 절약)
        done_dates = []
        for ds, rows in day_buffer.items():
            if len(rows) >= 1400 or ds != oldest_date:  # 거의 하루치 채워짐 or 더 이상 이 날짜 안 옴
                rows.sort(key=lambda x: x["t"])
                # dedupe
                seen = set(); deduped = []
                for r in rows:
                    if r["t"] not in seen: seen.add(r["t"]); deduped.append(r)
                _save_day_file(coin, ds, deduped)
                total_saved += len(deduped)
                done_dates.append(ds)
        for ds in done_dates:
            del day_buffer[ds]
            existing_dates.add(ds)

    # 남은 버퍼 저장
    for ds, rows in day_buffer.items():
        if not rows: continue
        rows.sort(key=lambda x: x["t"])
        seen = set(); deduped = []
        for r in rows:
            if r["t"] not in seen: seen.add(r["t"]); deduped.append(r)
        _save_day_file(coin, ds, deduped)
        total_saved += len(deduped)
    day_buffer.clear()

    elapsed = time.time() - start_ts
    return total_saved, total_pages, f"{elapsed:.0f}s"

# ── 수집 통계 (thread-safe) ──
_collect_lock = threading.Lock()
_collect_stats = {"done": 0, "skip": 0, "fail": 0, "candles": 0}

def _collect_worker(market, coin, days):
    """워커 함수: 한 코인 수집"""
    try:
        existing = _get_existing_dates(coin)
        saved, pages, info = _fetch_one_coin(market, coin, days, existing)
        with _collect_lock:
            if info == "skip":
                _collect_stats["skip"] += 1
                return coin, "skip", 0
            elif saved > 0:
                _collect_stats["done"] += 1
                _collect_stats["candles"] += saved
                return coin, "done", saved
            else:
                _collect_stats["fail"] += 1
                return coin, "fail", 0
    except Exception as e:
        with _collect_lock:
            _collect_stats["fail"] += 1
        return coin, f"error: {e}", 0

def collect(days=30, top_n=30):
    """1분봉 수집: 일별 gzip jsonl + 4 worker 병렬 + 증분"""
    if days > 200: days = 200  # API 부하 방지 상한

    tg(f"[수집] 1분봉 | {days}일, {top_n}코인, {NUM_WORKERS} worker 병렬, 증분수집\n"
       f"※ 증분수집: 빠진 날짜만 받음, 첫 실행 시 5~15분 소요 가능")
    markets = get_top_markets(top_n)
    if not markets: tg("[오류] 종목 실패"); return []
    coins = [m.split("-")[1] for m in markets]
    tg(f"종목: {', '.join(coins)}")
    t0 = time.time()

    # 전역 통계 리셋
    _collect_stats["done"] = _collect_stats["skip"] = _collect_stats["fail"] = _collect_stats["candles"] = 0

    COLLECT_TIMEOUT = 900  # 수집 전체 15분 제한
    results = []
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {}
        for market in markets:
            coin = market.split("-")[1]
            f = executor.submit(_collect_worker, market, coin, days)
            futures[f] = coin

        last_hb = t0
        timed_out = False
        for i, future in enumerate(as_completed(futures)):
            # 전체 수집 타임아웃 체크
            if time.time() - t0 > COLLECT_TIMEOUT and not timed_out:
                timed_out = True
                tg(f"[수집타임아웃] {COLLECT_TIMEOUT//60}분 초과 → 나머지 스킵, 있는 데이터로 분석 진행")
                executor.shutdown(wait=False, cancel_futures=True)
                break

            coin_name, status, count = future.result()
            results.append((coin_name, status, count))

            # 진행 상황 출력
            if status == "skip":
                print(f"  {coin_name}: 스킵 (이미 수집됨)")
            elif status == "done":
                tg(f"  {coin_name}: {count:,}개 저장 완료")
            else:
                tg(f"  {coin_name}: {status}")

            # 하트비트
            now = time.time()
            if now - last_hb >= HEARTBEAT_INTERVAL:
                s = _collect_stats
                mem = _get_mem_mb()
                mem_str = f" | mem={mem:.0f}MB" if mem > 0 else ""
                tg(f"[살아있음] 수집 {i+1}/{len(markets)} | "
                   f"완료{s['done']}/스킵{s['skip']}/실패{s['fail']} | "
                   f"{int(now-t0)}초{mem_str}")
                last_hb = now

    s = _collect_stats
    total_el = time.time() - t0
    tg(f"[수집완료] {total_el/60:.1f}분 | {s['candles']:,}캔들 | "
       f"완료{s['done']}/스킵{s['skip']}/실패{s['fail']}")
    return coins

# ================================================================
# PART 2: 지표 함수
# ================================================================
def load_candles(coin, unit):
    if unit == 1:
        # 새 포맷: 일별 gzip jsonl
        candles = _load_coin_1m(coin)
        if candles:
            return candles
        # fallback: 구 포맷 (마이그레이션 전 호환)
        fpath = os.path.join(DATA_DIR, f"{coin}_1m.json")
        if os.path.exists(fpath):
            data = _safe_json_load(fpath)
            if data: return data.get("candles", [])
        return []
    # 다른 TF는 기존 방식
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath): return []
    data = _safe_json_load(fpath)
    if data is None: return []
    return data.get("candles", [])

def get_saved_coins():
    """수집된 코인 목록 (일별 디렉토리 구조 기준)"""
    if not os.path.exists(DATA_DIR): return []
    coins = set()
    for name in os.listdir(DATA_DIR):
        d = os.path.join(DATA_DIR, name)
        if os.path.isdir(d) and not name.startswith("."):
            # .jsonl.gz 파일이 하나라도 있으면 유효
            if any(f.endswith(".jsonl.gz") for f in os.listdir(d)):
                coins.add(name)
    # fallback: 구 포맷 호환
    for f in os.listdir(DATA_DIR):
        if f.endswith("_1m.json"):
            coins.add(f.replace("_1m.json", ""))
    return sorted(coins)

def make_nmin(c_small, ratio):
    out = []
    for i in range(0, len(c_small)-ratio+1, ratio):
        b = c_small[i:i+ratio]
        out.append({"t":b[0]["t"],"o":b[0]["o"],"c":b[-1]["c"],
                     "h":max(x["h"] for x in b),"l":min(x["l"] for x in b),
                     "v":sum(x["v"] for x in b),"_si":i})
    return out

def _tk16(t):
    """시간 문자열을 분 단위(YYYY-MM-DDTHH:MM)로 정규화"""
    return t[:16]

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
    """time_map miss 시 fallback — full timestamp 기준의 직전 봉 탐색 (이진탐색)"""
    lo,hi=0,len(candles)-1; r=None
    while lo<=hi:
        mid=(lo+hi)//2
        if candles[mid]["t"]<=sig_time: r=mid; lo=mid+1
        else: hi=mid-1
    return r

def process_coin(coin, btc_regime, dist_acc):
    print(f"  >> {coin} 분석 시작")  # print만 (텔레그램 폭격 방지)

    # 1분봉 먼저 로드 (이 스크립트의 주 데이터)
    c1 = load_candles(coin, 1)
    if len(c1) < 300:
        print(f"    {coin} 1분봉 부족 ({len(c1)}개), 스킵")
        return {}

    # 전부 1분봉에서 합성 (디스크 5분봉 안 읽음 — 시점 일관성 보장)
    c5 = make_nmin(c1, 5)
    if len(c5) < 200:
        print(f"    {coin} 5분봉 합성 부족 ({len(c5)}개), 스킵")
        del c1; return {}
    print(f"    {coin} 5분봉 합성 ({len(c5)}개)")
    c15 = make_nmin(c5, 3)
    c60 = make_nmin(c1, 60) if len(c1) >= 3600 else []

    # ── 메모리 최적화: c1 dict 리스트 → compact 배열 변환 ──
    # dict 86,400개 ≈ 30MB → 배열 4개 ≈ 4MB (85% 절감)
    c1_h = [c["h"] for c in c1]
    c1_l = [c["l"] for c in c1]
    c1_t = [c["t"] for c in c1]
    c1_len = len(c1)
    c1_map = {_tk16(c1_t[i]): i for i in range(c1_len)}
    del c1, c1_t  # 원본 dict + 시간 문자열 즉시 해제 (~32MB 절감)
    _force_free()  # 30MB를 OS에 즉시 반환

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
        ema5 = sum(closes[:5]) / 5
        ema10 = sum(closes[:10]) / 10
        ema12 = sum(closes[:12]) / 12
        ema20 = sum(closes[:20]) / 20
        ema26 = sum(closes[:26]) / 26
        ema50 = sum(closes[:50]) / 50 if n5 >= 50 else closes[0]
        # EMA 워밍업: 초기화 구간 ~ 50 사이 누락 방지
        for i in range(5, 50):
            ema5 = closes[i] * k5 + ema5 * (1 - k5)
            if i >= 10: ema10 = closes[i] * k10 + ema10 * (1 - k10)
            if i >= 12: ema12 = closes[i] * k12 + ema12 * (1 - k12)
            if i >= 20: ema20 = closes[i] * k20 + ema20 * (1 - k20)
            if i >= 26: ema26 = closes[i] * k26 + ema26 * (1 - k26)
        prev_macd_diff = ema12 - ema26  # 워밍업 후 초기값
        prev_aligned = False
        for i in range(50, n5):
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
    # 20봉 rolling average body (i=60 시점 기준 초기화)
    body_sum20 = sum(bodies[40:60]) if n5 >= 60 else 0
    for i in range(60, end_idx):
        avg_body = body_sum20 / 20 if body_sum20 > 0 else 0
        # rolling 갱신 (스킵 여부 무관하게 항상 업데이트)
        body_sum20 = body_sum20 - bodies[i-20] + bodies[i]
        if closes[i] <= opens[i]: continue
        body = closes[i] - opens[i]
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
        del c5, c15, c60; return {}

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

    # c1_map, c1_h, c1_l은 이미 상단에서 compact 배열로 생성됨
    use_1m = len(c1_map) > 100

    # 멀티 TF 피처 추출 (tf15/60: 여기서, tf5: 아래 raw_signals 루프에서 별도 처리)
    tf_feat_cache = defaultdict(dict)
    for tf in ANALYSIS_TFS:
        if tf in (1, 5):
            continue  # tf1: 해제됨, tf5: 아래 raw_signals 루프에서 별도 처리
        elif tf == 15:
            candles = c15 if c15 and len(c15) >= 60 else None
        elif tf == 60:
            candles = c60 if c60 and len(c60) >= 60 else None
        else:
            candles = None

        if not candles or len(candles) < 60:
            continue

        time_map = {_tk16(cc["t"]): idx for idx, cc in enumerate(candles)}

        pfx = f"tf{tf}_"
        for sig_time in all_sig_times:
            tk = _tk16(sig_time)
            idx = time_map.get(tk) if time_map else None
            if idx is None: idx = find_idx(candles, sig_time)
            if idx is not None and idx >= 55:
                all_feats = extract_tf_features(candles, idx, pfx)
                for fk, fv in all_feats.items():
                    short_key = fk[len(pfx):]
                    lst = dist_acc[(tf, short_key)]
                    if len(lst) < 10000:
                        lst.append(fv)
                for fk, fv in all_feats.items():
                    short_key = fk[len(pfx):]
                    if short_key in COND_KEYS:
                        tf_feat_cache[sig_time][fk] = fv
                del all_feats

        if time_map is not None: del time_map

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
            tk=_tk16(bar["t"])
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
            strict = _sim_strict_1m_compact(c1_h, c1_l, c1_len, c1_map, c5, ei, entry,
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

    del c5, c15, c60, c1_h, c1_l, c1_map, tf_feat_cache, raw_signals
    return results

def _sim_strict_1m(c1, c1_map, c5, ei5, entry, tp, sl, cost):
    """legacy — 호출되지 않음, compact 버전 사용"""
    return _sim_strict_5m(c5, ei5, entry, tp, sl, cost)

def _sim_strict_1m_compact(c1_h, c1_l, c1_len, c1_map, c5, ei5, entry, tp, sl, cost):
    """compact 배열(c1_h, c1_l)로 1분봉 시뮬레이션 — 메모리 효율"""
    entry_time = _tk16(c5[ei5]["t"])
    start_idx = c1_map.get(entry_time)
    if start_idx is None:
        r = _sim_strict_5m(c5, ei5, entry, tp, sl, cost); r["src"]="5m_fb"; return r
    max_bars = min(MAX_HOLD_1M, c1_len - start_idx - 1)
    if max_bars <= 0: return {"pnl": 0.0, "result": "TIMEOUT", "bars": 0, "src": "1m"}
    for j in range(max_bars + 1):
        idx = start_idx + j
        hp = (c1_h[idx] - entry) / entry * 100
        lp = (c1_l[idx] - entry) / entry * 100
        sl_hit = lp <= -sl; tp_hit = hp >= tp
        if sl_hit and tp_hit: return {"pnl": round(-sl - cost, 4), "result": "BOTH_SL", "bars": j, "src": "1m"}
        if sl_hit: return {"pnl": round(-sl - cost, 4), "result": "SL", "bars": j, "src": "1m"}
        if tp_hit: return {"pnl": round(tp - cost, 4), "result": "TP", "bars": j, "src": "1m"}
    # TIMEOUT: 5분봉 종가로 대체 (c1 close 배열 없으므로)
    r = _sim_strict_5m(c5, ei5, entry, tp, sl, cost); r["src"]="1m_to"; return r

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
    L.append(f"업비트 신호 연구 v4.0 ({ts})")
    L.append(f"비용:{TOTAL_COST*100:.2f}% | 진입:다음봉시가 | 최대:{MAX_HOLD_BARS}봉")
    L.append(f"Train/Test:{TRAIN_RATIO*100:.0f}/{(1-TRAIN_RATIO)*100:.0f} | 최소:{MIN_TRADES}건")
    L.append(f"WF: Train={WF_TRAIN_DAYS}일 Test={WF_TEST_DAYS}일 Step={WF_STEP_DAYS}일")
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
    detail_tfs={1,5,15,60}
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
        # 복합필터 (멀티TF: 1분봉 합성 기반)
        L.append(f"\n    [복합필터]")
        combos=[
            ("5m_RSI<50+15m_BB<40", lambda s: s.get("tf5_rsi14",50)<50 and s.get("tf15_bb20",50)<40),
            ("5m_MACD골든+15m_ADX>25", lambda s: s.get("tf5_macd_x")==1 and s.get("tf15_adx",0)>25),
            ("15m_MACD골든+1h_EMA정배열", lambda s: s.get("tf15_macd_x")==1 and s.get("tf60_ema_al",0)>=3),
            ("5m_RSI<40+BTC상승+15m_MACD골든", lambda s: s.get("tf5_rsi14",50)<40 and s["regime"]=="상승" and s.get("tf15_macd_x")==1),
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

    # ================================================================
    # [6] 워크포워드 검증 (Rolling Window)
    # ================================================================
    L.append(f"\n{'='*65}")
    L.append(f"[6] 워크포워드 검증 (Train={WF_TRAIN_DAYS}일 → Test={WF_TEST_DAYS}일, Step={WF_STEP_DAYS}일)")
    L.append("-"*65)
    wf_summary = []  # [6-2] 종합 요약용

    # 시그널의 time 필드로 날짜 파싱 → datetime 객체
    from datetime import date as _date
    def _parse_day(t_str):
        """시그널 time 문자열에서 날짜만 추출"""
        try: return datetime.strptime(t_str[:10], "%Y-%m-%d").date()
        except: return None

    for st, sigs in all_results.items():
        if len(sigs) < WF_MIN_TRAIN + WF_MIN_TEST:
            continue

        # 시그널을 시간순 정렬 + 날짜 미리 파싱 (성능: strptime 1회만)
        sigs_sorted = sorted(sigs, key=lambda s: s["time"])
        for s in sigs_sorted:
            s["_day"] = _parse_day(s["time"])

        # 전체 날짜 범위
        days_all = [s["_day"] for s in sigs_sorted if s["_day"]]
        if len(days_all) < 2:
            continue
        d_min, d_max = min(days_all), max(days_all)
        total_span = (d_max - d_min).days

        if total_span < WF_TRAIN_DAYS + WF_TEST_DAYS:
            L.append(f"\n  {st}: 기간 부족 ({total_span}일 < {WF_TRAIN_DAYS+WF_TEST_DAYS}일)")
            continue

        # 모든 청산 전략 키
        ek_list = sorted(set(k for s in sigs_sorted for k in s.get("exits", {})))
        if not ek_list:
            continue

        # 폴드 생성
        folds = []
        fold_start = d_min
        fold_idx = 0
        while True:
            tr_start = fold_start
            tr_end   = tr_start + timedelta(days=WF_TRAIN_DAYS)
            te_start = tr_end
            te_end   = te_start + timedelta(days=WF_TEST_DAYS)

            if te_end > d_max + timedelta(days=1):
                break

            tr_sigs = [s for s in sigs_sorted if s["_day"] is not None
                       and tr_start <= s["_day"] < tr_end]
            te_sigs = [s for s in sigs_sorted if s["_day"] is not None
                       and te_start <= s["_day"] < te_end]

            if len(tr_sigs) >= WF_MIN_TRAIN and len(te_sigs) >= WF_MIN_TEST:
                # Train에서 최적 청산전략 선택
                best_ek, best_ev = None, -999
                for ek in ek_list:
                    m = calc_m([s["exits"].get(ek, 0) for s in tr_sigs])
                    if m and m["ev"] > best_ev:
                        best_ev = m["ev"]
                        best_ek = ek

                if best_ek:
                    tr_m = calc_m([s["exits"].get(best_ek, 0) for s in tr_sigs])
                    te_m = calc_m([s["exits"].get(best_ek, 0) for s in te_sigs])
                    folds.append({
                        "idx": fold_idx,
                        "tr_range": f"{tr_start}~{tr_end-timedelta(days=1)}",
                        "te_range": f"{te_start}~{te_end-timedelta(days=1)}",
                        "best_ek": best_ek,
                        "tr_m": tr_m,
                        "te_m": te_m,
                        "tr_n": len(tr_sigs),
                        "te_n": len(te_sigs),
                    })

            fold_idx += 1
            fold_start += timedelta(days=WF_STEP_DAYS)

        if not folds:
            L.append(f"\n  {st}: 유효 폴드 없음")
            continue

        L.append(f"\n  ■ {st} ({len(folds)}개 폴드, 전체 {len(sigs)}건)")
        L.append(f"    {'Fold':>4s} | {'Train기간':^23s} | {'Test기간':^23s} | {'최적전략':^25s} | {'TR_EV':>8s} {'TR_n':>5s} | {'TE_EV':>8s} {'TE_n':>5s} {'TE_WR':>5s} {'TE_PF':>5s}")
        L.append(f"    {'-'*130}")

        te_evs = []
        te_positive = 0
        te_total_pnl = 0.0
        te_total_n = 0

        for f in folds:
            te_ev = f["te_m"]["ev"] if f["te_m"] else 0
            tr_ev = f["tr_m"]["ev"] if f["tr_m"] else 0
            te_wr = f["te_m"]["wr"] if f["te_m"] else 0
            te_pf = f["te_m"]["pf"] if f["te_m"] else 0
            te_evs.append(te_ev)
            if te_ev > 0: te_positive += 1
            te_total_pnl += f["te_m"]["tp"] if f["te_m"] else 0
            te_total_n += f["te_n"]

            pf_str = f"{te_pf:.2f}" if te_pf < 999 else "INF"
            mark = " ✓" if te_ev > 0 else " ✗"
            L.append(f"    {f['idx']:>4d} | {f['tr_range']:^23s} | {f['te_range']:^23s} | {f['best_ek']:^25s} | {tr_ev:>+8.4f}% {f['tr_n']:>5d} | {te_ev:>+8.4f}% {f['te_n']:>5d} {te_wr:>5.1f} {pf_str:>5s}{mark}")

        # 종합 통계
        n_folds = len(folds)
        avg_te_ev = sum(te_evs) / n_folds if n_folds else 0
        pos_rate = te_positive / n_folds * 100 if n_folds else 0

        # EV 표준편차
        if n_folds > 1:
            var = sum((e - avg_te_ev)**2 for e in te_evs) / (n_folds - 1)
            std_ev = var ** 0.5
        else:
            std_ev = 0

        # 종합 TEST 성과 (모든 폴드 TEST 시그널 합산)
        agg_ev = te_total_pnl / te_total_n if te_total_n else 0

        L.append(f"    {'-'*130}")
        L.append(f"    종합: 평균TE_EV={avg_te_ev:+.4f}% | 양수폴드={te_positive}/{n_folds} ({pos_rate:.0f}%) | EV_std={std_ev:.4f}% | 합산EV={agg_ev:+.4f}% (n={te_total_n})")

        # 판정
        if avg_te_ev > 0 and pos_rate >= 60:
            verdict = "PASS — 시간 안정적 (양수 폴드 ≥60%)"
        elif avg_te_ev > 0 and pos_rate >= 40:
            verdict = "WEAK — 평균 양수이나 일관성 부족"
        elif pos_rate >= 50 and avg_te_ev > -0.05:
            verdict = "MARGINAL — 반반, 주의 필요"
        else:
            verdict = "FAIL — 과적합 의심 또는 비수익"
        L.append(f"    판정: {verdict}")
        # 종합 요약용 저장 (중복 계산 방지)
        wf_summary.append((st, avg_te_ev, pos_rate, n_folds, verdict.split(" —")[0]))

    # [6-2] 워크포워드 종합 요약 (위에서 수집한 결과 재사용)
    L.append(f"\n  {'─'*50}")
    L.append(f"  [워크포워드 종합]")
    if wf_summary:
        wf_summary.sort(key=lambda x: -x[1])
        for st, avg_e, pos_r, nf, tag in wf_summary:
            L.append(f"    [{tag:>8s}] {st:<16s} avgTE={avg_e:+.4f}% 양수율={pos_r:.0f}% ({nf}폴드)")
    else:
        L.append(f"    >>> 워크포워드 검증 가능한 시그널 없음 <<<")

    L.append(f"\n{'='*65}\n끝.")
    return L

# ================================================================
# 메인
# ================================================================
def _has_enough_data(min_coins=10, min_days=25):
    """충분한 코인이 충분한 일수 데이터를 가지고 있는지"""
    if not os.path.exists(DATA_DIR): return False
    good = 0
    for name in os.listdir(DATA_DIR):
        d = os.path.join(DATA_DIR, name)
        if not os.path.isdir(d) or name.startswith("."): continue
        gz_files = [f for f in os.listdir(d) if f.endswith(".jsonl.gz")]
        if len(gz_files) >= min_days:
            good += 1
    return good >= min_coins

# ================================================================
# 패턴 발굴 모드 (--discover)
# ================================================================
_DISC_SLIPPAGE = 0.002       # 0.2% 슬리피지
_DISC_MFE_THRESHOLD = 0.01   # 1% MFE 기준
_DISC_MFE_WINDOW = 5         # 5분 (5개 1분봉)
_DISC_OUT_DIR = os.path.join(SCRIPT_DIR, "discovery_output")

def _disc_compute_labels(candles):
    """각 시점 t의 MFE/MAE/종가수익률 레이블 생성"""
    n = len(candles)
    labels = []
    for t in range(n):
        if t + _DISC_MFE_WINDOW >= n:
            labels.append(None)
            continue
        entry_price = candles[t + 1]["o"] * (1 + _DISC_SLIPPAGE)
        if entry_price <= 0:
            labels.append(None)
            continue
        future_highs = [candles[t + 1 + j]["h"] for j in range(_DISC_MFE_WINDOW)]
        future_lows = [candles[t + 1 + j]["l"] for j in range(_DISC_MFE_WINDOW)]
        close_5m = candles[t + _DISC_MFE_WINDOW]["c"]
        mfe = max(future_highs) / entry_price - 1
        mae = min(future_lows) / entry_price - 1
        ret_close = close_5m / entry_price - 1
        labels.append({
            "entry_price": round(entry_price, 2),
            "mfe_5m": round(mfe * 100, 4),
            "mae_5m": round(mae * 100, 4),
            "ret_close_5m": round(ret_close * 100, 4),
            "success": 1 if mfe >= _DISC_MFE_THRESHOLD else 0,
        })
    return labels

def _disc_tag_runs(labels):
    """연속 성공 구간에 run_id, is_first 태깅"""
    run_id = 0; in_run = False; current_pos = 0
    for i, lb in enumerate(labels):
        if lb is None:
            in_run = False; continue
        if lb["success"] == 1:
            if not in_run:
                run_id += 1; in_run = True; current_pos = 0
                lb["run_id"] = run_id; lb["is_first"] = 1; lb["run_pos"] = 0
            else:
                current_pos += 1
                lb["run_id"] = run_id; lb["is_first"] = 0; lb["run_pos"] = current_pos
        else:
            in_run = False
            lb["run_id"] = 0; lb["is_first"] = 0; lb["run_pos"] = 0

def _disc_extract_features(candles, t):
    """시점 t의 직전 3분/15분/60분 피처 생성"""
    if t < 60: return None
    price = candles[t]["c"]
    if price <= 0: return None
    feat = {}
    cl_60 = [candles[t-60+j]["c"] for j in range(61)]
    hi_60 = [candles[t-60+j]["h"] for j in range(61)]
    lo_60 = [candles[t-60+j]["l"] for j in range(61)]
    vo_60 = [candles[t-60+j]["v"] for j in range(61)]
    op_60 = [candles[t-60+j]["o"] for j in range(61)]

    # ── 3분 피처 ──
    cl3 = cl_60[-4:]
    hi3, lo3, vo3 = hi_60[-3:], lo_60[-3:], vo_60[-3:]
    feat["m3_ret"] = round((cl3[-1]-cl3[0])/cl3[0]*100, 4) if cl3[0]>0 else 0
    green3 = sum(1 for i in range(-3,0) if cl_60[i]>op_60[i])
    feat["m3_green_ratio"] = round(green3/3, 2)
    feat["m3_higher_high"] = 1 if hi3[-1]>hi3[-2]>hi3[-3] else 0
    feat["m3_higher_low"] = 1 if lo3[-1]>lo3[-2]>lo3[-3] else 0
    v3_now = sum(vo3); v3_prev = sum(vo_60[-6:-3])
    feat["m3_vol_ratio"] = round(v3_now/v3_prev, 2) if v3_prev>0 else 1.0
    body = abs(candles[t]["c"]-candles[t]["o"])
    rng = candles[t]["h"]-candles[t]["l"]
    feat["m3_body_pct"] = round(body/price*100, 4)
    feat["m3_lw_pct"] = round((min(candles[t]["o"],candles[t]["c"])-candles[t]["l"])/rng*100, 1) if rng>0 else 0
    feat["m3_uw_pct"] = round((candles[t]["h"]-max(candles[t]["o"],candles[t]["c"]))/rng*100, 1) if rng>0 else 0

    # ── 15분 피처 ──
    cl15, hi15, lo15, vo15 = cl_60[-16:], hi_60[-15:], lo_60[-15:], vo_60[-15:]
    feat["m15_ret"] = round((cl15[-1]-cl15[0])/cl15[0]*100, 4) if cl15[0]>0 else 0
    feat["m15_volatility"] = round(_vol(cl15, min(15, len(cl15))), 3)
    feat["m15_rsi14"] = round(_rsi(cl15, 14), 1)
    bp, bw = _bb(cl15, min(15, len(cl15)))
    feat["m15_bb15_pos"] = round(bp, 1); feat["m15_bb15_width"] = round(bw, 3)
    v15_avg = sum(vo15)/15
    v15_std = (sum((v-v15_avg)**2 for v in vo15)/15)**0.5
    feat["m15_vol_zscore"] = round((vo15[-1]-v15_avg)/v15_std, 2) if v15_std>0 else 0
    green15 = sum(1 for i in range(15) if cl_60[-15+i]>op_60[-15+i])
    feat["m15_green_ratio"] = round(green15/15, 2)
    hh15, ll15 = max(hi15), min(lo15)
    feat["m15_pos"] = round((price-ll15)/(hh15-ll15)*100, 1) if hh15!=ll15 else 50.0
    recent_low = min(lo_60[-5:])
    feat["m15_recovery"] = round((price-recent_low)/recent_low*100, 3) if recent_low>0 else 0

    # ── 60분 피처 ──
    feat["m60_ret"] = round((cl_60[-1]-cl_60[0])/cl_60[0]*100, 4) if cl_60[0]>0 else 0
    feat["m60_volatility"] = round(_vol(cl_60, 20), 3)
    feat["m60_rsi14"] = round(_rsi(cl_60, 14), 1)
    feat["m60_ema_align"] = _ema_align(cl_60)
    feat["m60_macd_h"] = _macd_hist(cl_60)
    b60p, b60w = _bb(cl_60, 20)
    feat["m60_bb_pos"] = round(b60p, 1); feat["m60_bb_width"] = round(b60w, 3)
    feat["m60_adx"] = round(_adx(hi_60, lo_60, cl_60), 1)
    feat["m60_cci"] = round(_cci(hi_60, lo_60, cl_60), 1)
    feat["m60_mfi"] = round(_mfi(hi_60, lo_60, cl_60, vo_60), 1)
    v_recent = sum(vo_60[-15:])/15
    v_old_sum = sum(vo_60[:45])
    v_old = v_old_sum/45 if v_old_sum>0 else 1
    feat["m60_vol_trend"] = round(v_recent/v_old, 2) if v_old>0 else 1.0
    hh60, ll60 = max(hi_60), min(lo_60)
    feat["m60_pos"] = round((price-ll60)/(hh60-ll60)*100, 1) if hh60!=ll60 else 50.0
    ema20 = _ema(cl_60, 20)
    feat["m60_ema20_gap"] = round((price-ema20)/ema20*100, 3) if ema20>0 else 0
    for p in [5, 10, 20]:
        feat[f"mom_{p}"] = round((cl_60[-1]-cl_60[-(p+1)])/cl_60[-(p+1)]*100, 4) if cl_60[-(p+1)]>0 else 0
    return feat

def _disc_analyze_coin(coin, days):
    """단일 코인 패턴 분석"""
    candles = _load_coin_1m(coin, days)
    if len(candles) < 200:
        print(f"  {coin}: 데이터 부족 ({len(candles)}개), 스킵"); return None
    print(f"  {coin}: {len(candles)}개 1분봉 로드")
    labels = _disc_compute_labels(candles)
    _disc_tag_runs(labels)
    samples = []
    for t in range(len(candles)):
        lb = labels[t]
        if lb is None: continue
        feat = _disc_extract_features(candles, t)
        if feat is None: continue
        rid = lb.get("run_id", 0)
        rk = f"{coin}_{rid}" if rid > 0 else ""
        raw_t = candles[t]["t"]
        ts = int(datetime.strptime(raw_t[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=KST).timestamp() * 1000)
        samples.append({"coin": coin, "time": raw_t, "ts": ts, "run_key": rk, **feat, **lb})
    sc = sum(1 for s in samples if s["success"]==1)
    total = len(samples)
    print(f"  {coin}: {total}개 샘플, 성공 {sc}개 ({sc/total*100:.1f}%)" if total else f"  {coin}: 0개 샘플")
    return samples

def _disc_discover_rules(all_samples):
    """성공 vs 실패 피처 차이 분석"""
    if not all_samples: return {}
    meta = {"coin","time","ts","entry_price","mfe_5m","mae_5m","ret_close_5m","success","run_id","is_first","run_pos","run_key"}
    feat_keys = [k for k in all_samples[0].keys() if k not in meta]
    success = [s for s in all_samples if s["success"]==1]
    failure = [s for s in all_samples if s["success"]==0]
    if not success or not failure: return {}
    results = {}
    for key in feat_keys:
        s_vals = [s[key] for s in success if key in s and s[key] is not None]
        f_vals = [s[key] for s in failure if key in s and s[key] is not None]
        if not s_vals or not f_vals: continue
        s_mean, f_mean = sum(s_vals)/len(s_vals), sum(f_vals)/len(f_vals)
        diff = s_mean - f_mean
        all_vals = s_vals + f_vals
        all_mean = sum(all_vals)/len(all_vals)
        std = (sum((v-all_mean)**2 for v in all_vals)/len(all_vals))**0.5
        effect = diff/std if std>0 else 0
        s_sorted = sorted(s_vals); f_sorted = sorted(f_vals)
        s_med = s_sorted[len(s_sorted)//2]; f_med = f_sorted[len(f_sorted)//2]
        results[key] = {"success_mean":round(s_mean,4), "failure_mean":round(f_mean,4),
                         "success_median":round(s_med,4), "failure_median":round(f_med,4),
                         "diff":round(diff,4), "effect_size":round(effect,4),
                         "n_success":len(s_vals), "n_failure":len(f_vals)}
    return dict(sorted(results.items(), key=lambda x: abs(x[1]["effect_size"]), reverse=True))

def _disc_find_best_rules(all_samples, top_features, n_rules=10):
    """임계값 기반 규칙 탐색"""
    rules = []
    for feat_name, _ in list(top_features.items())[:20]:
        vals = [(s[feat_name], s["success"]) for s in all_samples if feat_name in s and s[feat_name] is not None]
        if len(vals) < 100: continue
        vals.sort(key=lambda x: x[0])
        n = len(vals)
        total_s = sum(v[1] for v in vals)
        cum = [0]*(n+1)
        for i in range(n): cum[i+1] = cum[i] + vals[i][1]
        best = None; base = total_s/n
        for pct in [10,20,25,30,70,75,80,90]:
            idx = int(n*pct/100); thr = vals[idx][0]
            n_ab = n-idx
            if n_ab >= 50:
                r_ab = (total_s - cum[idx])/n_ab; lift = r_ab/base if base>0 else 0
                if lift > 1.3 and (best is None or lift > best["lift"]):
                    best = {"feature":feat_name,"direction":">=","threshold":round(thr,4),
                            "success_rate":round(r_ab*100,2),"base_rate":round(base*100,2),
                            "lift":round(lift,2),"n_samples":n_ab}
            n_bl = idx+1
            if n_bl >= 50:
                r_bl = cum[idx+1]/n_bl; lift = r_bl/base if base>0 else 0
                if lift > 1.3 and (best is None or lift > best["lift"]):
                    best = {"feature":feat_name,"direction":"<=","threshold":round(thr,4),
                            "success_rate":round(r_bl*100,2),"base_rate":round(base*100,2),
                            "lift":round(lift,2),"n_samples":n_bl}
        if best: rules.append(best)
    rules.sort(key=lambda x: x["lift"], reverse=True)
    return rules[:n_rules]

def _disc_find_combo_rules(all_samples, single_rules, top_k=8, min_samples=100):
    """상위 단변수 규칙 중 2조건 조합 탐색"""
    if len(single_rules) < 2: return []
    base = sum(s["success"] for s in all_samples)/len(all_samples) if all_samples else 0
    candidates = single_rules[:top_k]
    combos = []
    for i in range(len(candidates)):
        for j in range(i+1, len(candidates)):
            r1, r2 = candidates[i], candidates[j]
            if r1["feature"] == r2["feature"]: continue
            f1, d1, th1 = r1["feature"], r1["direction"], r1["threshold"]
            f2, d2, th2 = r2["feature"], r2["direction"], r2["threshold"]
            hits = []
            for s in all_samples:
                if f1 not in s or f2 not in s: continue
                v1, v2 = s[f1], s[f2]
                if v1 is None or v2 is None: continue
                c1 = v1 >= th1 if d1 == ">=" else v1 <= th1
                c2 = v2 >= th2 if d2 == ">=" else v2 <= th2
                if c1 and c2: hits.append(s)
            if len(hits) < min_samples: continue
            sr = sum(s["success"] for s in hits)/len(hits)
            lift = sr/base if base > 0 else 0
            if lift > 1.3:
                combos.append({
                    "conditions": [
                        {"feature":f1,"direction":d1,"threshold":th1},
                        {"feature":f2,"direction":d2,"threshold":th2}],
                    "success_rate":round(sr*100,2), "base_rate":round(base*100,2),
                    "lift":round(lift,2), "n_samples":len(hits)})
    combos.sort(key=lambda x: x["lift"], reverse=True)
    return combos[:10]

def _disc_walk_forward(all_samples, rules, train_days=21, test_days=7, step_days=5):
    """Rolling out-of-sample validation (사전 발굴 규칙의 시간대별 안정성 검증)"""
    if not all_samples or not rules: return []
    all_samples.sort(key=lambda x: x["ts"])
    t_min = all_samples[0]["ts"]; t_max = all_samples[-1]["ts"]
    day_ms = 86400_000
    train_ms, test_ms, step_ms = train_days*day_ms, test_days*day_ms, step_days*day_ms
    # fold별 결과 수집
    rule_folds = {i: [] for i in range(len(rules))}
    base_folds = []
    fold_start = t_min
    n_folds = 0
    while fold_start + train_ms + test_ms <= t_max:
        tr_end = fold_start + train_ms
        te_end = tr_end + test_ms
        # train은 fold 유효성 체크용 (규칙은 이미 전체 데이터에서 발굴됨)
        n_train = sum(1 for s in all_samples if fold_start <= s["ts"] < tr_end)
        test = [s for s in all_samples if tr_end <= s["ts"] < te_end]
        if n_train < 100 or len(test) < 30:
            fold_start += step_ms; continue
        bte = sum(s["success"] for s in test)/len(test)
        base_folds.append(bte)
        for ri, rule in enumerate(rules):
            f, d, th = rule["feature"], rule["direction"], rule["threshold"]
            if d == ">=":
                teh = [s for s in test if f in s and s[f]>=th]
            else:
                teh = [s for s in test if f in s and s[f]<=th]
            ter = sum(s["success"] for s in teh)/len(teh) if teh else 0
            rule_folds[ri].append({"test_rate": ter, "test_n": len(teh), "base": bte})
        n_folds += 1
        fold_start += step_ms
    if n_folds == 0: return []
    results = []
    avg_base = sum(base_folds)/len(base_folds) if base_folds else 0
    for ri, rule in enumerate(rules):
        folds = rule_folds[ri]
        if not folds:
            results.append({**rule, "n_folds":0, "avg_test_rate":0, "avg_base":0,
                            "avg_test_lift":0, "survived":False, "fold_details":[]}); continue
        rates = [f["test_rate"] for f in folds]
        lifts = [f["test_rate"]/f["base"] if f["base"]>0 else 0 for f in folds]
        avg_rate = sum(rates)/len(rates)
        avg_lift = sum(lifts)/len(lifts)
        win_folds = sum(1 for l in lifts if l > 1.0)
        results.append({**rule, "n_folds":n_folds, "avg_test_rate":round(avg_rate*100,2),
                        "avg_base":round(avg_base*100,2), "avg_test_lift":round(avg_lift,2),
                        "win_rate":round(win_folds/n_folds*100,1),
                        "survived": avg_lift > 1.2 and win_folds/n_folds >= 0.5,
                        "fold_details": folds})
    return results

def _disc_walk_forward_combo(all_samples, combos, train_days=21, test_days=7, step_days=5):
    """Rolling out-of-sample validation (조합 규칙용)"""
    if not all_samples or not combos: return []
    all_samples.sort(key=lambda x: x["ts"])
    t_min = all_samples[0]["ts"]; t_max = all_samples[-1]["ts"]
    day_ms = 86400_000
    train_ms, test_ms, step_ms = train_days*day_ms, test_days*day_ms, step_days*day_ms
    combo_folds = {i: [] for i in range(len(combos))}
    base_folds = []; n_folds = 0; fold_start = t_min
    while fold_start + train_ms + test_ms <= t_max:
        tr_end = fold_start + train_ms; te_end = tr_end + test_ms
        n_train = sum(1 for s in all_samples if fold_start <= s["ts"] < tr_end)
        test = [s for s in all_samples if tr_end <= s["ts"] < te_end]
        if n_train < 100 or len(test) < 30:
            fold_start += step_ms; continue
        bte = sum(s["success"] for s in test)/len(test)
        base_folds.append(bte)
        for ci, combo in enumerate(combos):
            conds = combo["conditions"]
            hits = []
            for s in test:
                ok = True
                for c in conds:
                    f, d, th = c["feature"], c["direction"], c["threshold"]
                    if f not in s or s[f] is None: ok = False; break
                    if d == ">=" and s[f] < th: ok = False; break
                    if d == "<=" and s[f] > th: ok = False; break
                if ok: hits.append(s)
            ter = sum(s["success"] for s in hits)/len(hits) if hits else 0
            combo_folds[ci].append({"test_rate": ter, "test_n": len(hits), "base": bte})
        n_folds += 1; fold_start += step_ms
    if n_folds == 0: return []
    avg_base = sum(base_folds)/len(base_folds)
    results = []
    for ci, combo in enumerate(combos):
        folds = combo_folds[ci]
        if not folds:
            results.append({**combo, "n_folds":0, "avg_test_rate":0, "avg_test_lift":0, "survived":False}); continue
        rates = [f["test_rate"] for f in folds]
        lifts = [f["test_rate"]/f["base"] if f["base"]>0 else 0 for f in folds]
        avg_rate = sum(rates)/len(rates)
        avg_lift = sum(lifts)/len(lifts)
        win_folds = sum(1 for l in lifts if l > 1.0)
        results.append({**combo, "n_folds":n_folds, "avg_test_rate":round(avg_rate*100,2),
                        "avg_base":round(avg_base*100,2), "avg_test_lift":round(avg_lift,2),
                        "win_rate":round(win_folds/n_folds*100,1),
                        "survived": avg_lift > 1.2 and win_folds/n_folds >= 0.5})
    return results

def run_pattern_discovery(days=60):
    """패턴 발굴 메인 실행"""
    print("="*60)
    print("패턴 발굴 분석기 v2.0 (bot.py --discover)")
    print("="*60)
    coins = get_saved_coins()
    if not coins:
        print("candle_data 디렉토리에 데이터가 없습니다."); return
    print(f"\n수집된 코인: {len(coins)}개")
    print(f"분석 대상: {days}일")
    print(f"레이블: MFE({_DISC_MFE_WINDOW}분) >= {_DISC_MFE_THRESHOLD*100}%")
    print(f"슬리피지: {_DISC_SLIPPAGE*100}%\n")

    all_samples = []
    for i, coin in enumerate(coins):
        print(f"[{i+1}/{len(coins)}] {coin}")
        samples = _disc_analyze_coin(coin, days)
        if samples: all_samples.extend(samples)

    if not all_samples:
        print("\n분석 가능한 샘플이 없습니다."); return
    total = len(all_samples)
    success = sum(s["success"] for s in all_samples)
    print(f"\n{'='*60}\n전체 샘플: {total:,}\n성공: {success:,} ({success/total*100:.2f}%)\n{'='*60}")

    # is_first 샘플 분리
    first_samples = [s for s in all_samples if s.get("is_first",0)==1 or s.get("success",0)==0]
    fs_total = len(first_samples)
    fs_success = sum(s["success"] for s in first_samples)
    print(f"is_first 필터: {fs_total:,}개 (성공 {fs_success:,}, {fs_success/fs_total*100:.2f}%)" if fs_total else "")

    print("\n피처 분석 중...")
    feat_analysis = _disc_discover_rules(all_samples)
    feat_analysis_first = _disc_discover_rules(first_samples)
    print("규칙 발굴 중...")
    rules = _disc_find_best_rules(all_samples, feat_analysis)
    rules_first = _disc_find_best_rules(first_samples, feat_analysis_first)
    print("조합 규칙 탐색 중...")
    combo_rules = _disc_find_combo_rules(all_samples, rules)
    print("Rolling OOS 검증 중...")
    wf = _disc_walk_forward(all_samples, rules)
    wf_first = _disc_walk_forward(first_samples, rules_first)
    wf_combo = _disc_walk_forward_combo(all_samples, combo_rules)

    # 리포트 생성
    os.makedirs(_DISC_OUT_DIR, exist_ok=True)
    R = []
    R.append("="*70); R.append("패턴 발굴 분석 리포트")
    R.append(f"생성 시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    R.append("="*70)
    R.append(f"\n총 샘플: {total:,}")
    R.append(f"성공 샘플: {success:,} ({success/total*100:.2f}%)")
    R.append(f"실패 샘플: {total-success:,} ({(total-success)/total*100:.2f}%)")

    # 코인별 통계
    cs = defaultdict(lambda: {"total":0,"success":0})
    for s in all_samples: cs[s["coin"]]["total"]+=1; cs[s["coin"]]["success"]+=s["success"]
    R.append(f"\n── 코인별 통계 ──")
    R.append(f"{'코인':<10} {'총샘플':>8} {'성공':>6} {'성공률':>8}"); R.append("-"*35)
    for coin in sorted(cs.keys()):
        st=cs[coin]; r=st["success"]/st["total"]*100 if st["total"]>0 else 0
        R.append(f"{coin:<10} {st['total']:>8,} {st['success']:>6,} {r:>7.2f}%")

    # 연속 성공 구간
    rl = defaultdict(int)
    for s in all_samples:
        rk=s.get("run_key","")
        if rk: rl[rk]+=1
    runs = defaultdict(int)
    for length in rl.values(): runs[length]+=1
    R.append(f"\n── 연속 성공 구간 분포 ──")
    for length in sorted(runs.keys()): R.append(f"  {length}분 연속: {runs[length]}회")

    # 피처 분석 상위 15
    R.append(f"\n── 피처별 성공/실패 차이 (상위 15개) ──")
    R.append(f"{'피처':<20} {'성공평균':>9} {'실패평균':>9} {'성공중앙':>9} {'실패중앙':>9} {'효과크기':>8}"); R.append("-"*68)
    for i,(k,info) in enumerate(feat_analysis.items()):
        if i>=15: break
        R.append(f"{k:<20} {info['success_mean']:>9.4f} {info['failure_mean']:>9.4f} {info['success_median']:>9.4f} {info['failure_median']:>9.4f} {info['effect_size']:>8.4f}")

    # 규칙
    R.append(f"\n── 발굴된 규칙 후보 ──")
    for i, rule in enumerate(rules):
        R.append(f"\n규칙 {i+1}: {rule['feature']} {rule['direction']} {rule['threshold']}")
        R.append(f"  성공률: {rule['success_rate']}% (기본: {rule['base_rate']}%)")
        R.append(f"  리프트: {rule['lift']}x  |  샘플 수: {rule['n_samples']}")

    # 워크포워드
    n_folds_total = wf[0]["n_folds"] if wf else 0
    R.append(f"\n── Rolling Out-of-Sample 검증 (train {21}d / test {7}d / step {5}d, {n_folds_total} folds) ──")
    R.append(f"  * 규칙은 전체 데이터에서 사전 발굴 → 각 fold test 구간에 적용하여 안정성 확인")
    if n_folds_total < 3:
        R.append(f"  ⚠ fold 수 {n_folds_total}개 — 신뢰도 낮음. --days 60 이상 권장")
    R.append(f"{'규칙':<30} {'평균성공률':>10} {'평균리프트':>10} {'승률':>8} {'생존':>6}"); R.append("-"*70)
    for w in wf:
        sv="O" if w["survived"] else "X"; nm=f"{w['feature']} {w['direction']} {w['threshold']}"
        R.append(f"{nm:<30} {w['avg_test_rate']:>9.2f}% {w['avg_test_lift']:>9.2f}x {w.get('win_rate',0):>7.1f}% {sv:>6}")
    survived = [w for w in wf if w["survived"]]
    R.append(f"\n── OOS 생존 규칙: {len(survived)}개 ──")
    for w in survived:
        R.append(f"  {w['feature']} {w['direction']} {w['threshold']}  (평균 성공률: {w['avg_test_rate']}%, 리프트: {w['avg_test_lift']}x, 승률: {w.get('win_rate',0)}%)")

    # 조합 규칙
    if combo_rules:
        R.append(f"\n── 2조건 조합 규칙 ──")
        for i, combo in enumerate(combo_rules):
            conds = combo["conditions"]
            cond_str = " AND ".join(f"{c['feature']} {c['direction']} {c['threshold']}" for c in conds)
            R.append(f"\n조합 {i+1}: {cond_str}")
            R.append(f"  성공률: {combo['success_rate']}% (기본: {combo['base_rate']}%)")
            R.append(f"  리프트: {combo['lift']}x  |  샘플 수: {combo['n_samples']}")
    if wf_combo:
        R.append(f"\n── 조합 규칙 Rolling OOS 검증 ──")
        R.append(f"{'조합':<45} {'평균성공률':>10} {'리프트':>8} {'승률':>8} {'생존':>6}"); R.append("-"*80)
        for w in wf_combo:
            conds = w["conditions"]
            nm = " & ".join(f"{c['feature']}{c['direction']}{c['threshold']}" for c in conds)
            if len(nm) > 44: nm = nm[:41] + "..."
            sv = "O" if w["survived"] else "X"
            R.append(f"{nm:<45} {w['avg_test_rate']:>9.2f}% {w['avg_test_lift']:>7.2f}x {w.get('win_rate',0):>7.1f}% {sv:>6}")
        survived_combo = [w for w in wf_combo if w["survived"]]
        R.append(f"\n── 조합 OOS 생존: {len(survived_combo)}개 ──")
        for w in survived_combo:
            cond_str = " AND ".join(f"{c['feature']} {c['direction']} {c['threshold']}" for c in w["conditions"])
            R.append(f"  {cond_str}")
            R.append(f"    평균 성공률: {w['avg_test_rate']}%, 리프트: {w['avg_test_lift']}x, 승률: {w.get('win_rate',0)}%")

    # ── is_first 비교 분석 ──
    R.append(f"\n{'='*70}")
    R.append(f"── is_first 필터 비교 (연속 성공 중복 제거) ──")
    R.append(f"  * 필터 방식: 성공 run의 첫 시점만 유지 + 실패 전체 유지 (비대칭 압축)")
    R.append(f"  * 성공군만 줄어들므로 성공률/효과크기가 달라질 수 있음에 주의")
    R.append(f"전체 샘플: {total:,} (성공 {success:,}, {success/total*100:.2f}%)")
    R.append(f"is_first 필터: {fs_total:,} (성공 {fs_success:,}, {fs_success/fs_total*100:.2f}%)" if fs_total else "is_first 필터: 0")
    if feat_analysis_first:
        R.append(f"\n── is_first 피처 차이 (상위 10개) ──")
        R.append(f"{'피처':<25} {'효과(전체)':>10} {'효과(1st)':>10} {'차이':>8}"); R.append("-"*55)
        for i,(k,info) in enumerate(feat_analysis_first.items()):
            if i>=10: break
            ef_all = feat_analysis.get(k,{}).get("effect_size",0)
            R.append(f"{k:<25} {ef_all:>10.4f} {info['effect_size']:>10.4f} {info['effect_size']-ef_all:>8.4f}")
    if wf_first:
        survived_first = [w for w in wf_first if w["survived"]]
        R.append(f"\n── is_first OOS 생존 규칙: {len(survived_first)}개 ──")
        for w in survived_first:
            R.append(f"  {w['feature']} {w['direction']} {w['threshold']}  (평균 성공률: {w['avg_test_rate']}%, 리프트: {w['avg_test_lift']}x, 승률: {w.get('win_rate',0)}%)")

    # ── Top 규칙 레시피 요약 ──
    R.append(f"\n{'='*70}")
    R.append(f"── Top 규칙 레시피 (OOS 생존 + lift 순) ──")
    R.append(f"{'='*70}")
    recipe_idx = 0
    # 조합 규칙 생존 먼저
    if wf_combo:
        for w in sorted([w for w in wf_combo if w["survived"]], key=lambda x: x["avg_test_lift"], reverse=True)[:3]:
            recipe_idx += 1
            R.append(f"\n  Recipe #{recipe_idx} (조합)")
            for c in w["conditions"]:
                R.append(f"    {c['feature']} {c['direction']} {c['threshold']}")
            R.append(f"    → 평균 성공률: {w['avg_test_rate']}%  기본: {w['avg_base']}%  리프트: {w['avg_test_lift']}x  승률: {w.get('win_rate',0)}%")
    # 단변수 규칙 생존
    for w in sorted(survived, key=lambda x: x["avg_test_lift"], reverse=True)[:max(0, 3-recipe_idx)]:
        recipe_idx += 1
        R.append(f"\n  Recipe #{recipe_idx} (단변수)")
        R.append(f"    {w['feature']} {w['direction']} {w['threshold']}")
        R.append(f"    → 평균 성공률: {w['avg_test_rate']}%  기본: {w['avg_base']}%  리프트: {w['avg_test_lift']}x  승률: {w.get('win_rate',0)}%")
    if recipe_idx == 0:
        R.append("\n  OOS 생존 규칙 없음 — 데이터 추가 수집 또는 피처 재설계 필요")

    report_text = "\n".join(R)
    rpath = os.path.join(_DISC_OUT_DIR, "discovery_report.txt")
    with open(rpath,"w",encoding="utf-8") as f: f.write(report_text)
    dpath = os.path.join(_DISC_OUT_DIR, "discovery_data.json")
    with open(dpath,"w",encoding="utf-8") as f:
        wf_save = [{k:v for k,v in w.items() if k!="fold_details"} for w in wf]
        wf_first_save = [{k:v for k,v in w.items() if k!="fold_details"} for w in wf_first]
        json.dump({"feature_analysis":feat_analysis,"rules":rules,"walk_forward":wf_save,
                    "combo_rules":combo_rules, "combo_walk_forward":wf_combo,
                    "is_first_analysis":{"feature_analysis":feat_analysis_first,"rules":rules_first,"walk_forward":wf_first_save},
                    "stats":{"total_samples":total,"success_count":success,"success_rate":round(success/total*100,2),
                             "first_samples":fs_total,"first_success":fs_success}},
                   f, ensure_ascii=False, indent=2)
    print(f"\n{report_text}")
    print(f"\n리포트 저장: {rpath}")
    print("완료!")

def main():
    _acquire_lock()
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--coins", type=int, default=30)
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--discover", action="store_true",
                        help="패턴 발굴 모드: 1분봉 전체 시점 분석으로 규칙 발굴")
    args = parser.parse_args()

    if args.discover:
        _release_lock()
        if args.days < 50:
            print(f"[주의] --days {args.days}는 rolling WF에 fold가 적을 수 있음. --days 60 이상 권장")
        run_pattern_discovery(args.days)
        return

    global _GLOBAL_DAYS
    _GLOBAL_DAYS = args.days

    tg("[시작] upbit_signal_study v4.0 (1분봉 고속수집: 일별gzip + 4worker + 증분)")
    t0 = time.time()
    last_hb = t0

    # 1m 데이터 충분하면 수집 스킵 (요청 일수의 70% 이상이면 스킵)
    need_days = max(25, int(args.days * 0.7))
    if not args.skip_collect and _has_enough_data(min_days=need_days):
        tg(f"[자동] 1m 데이터 충분 ({need_days}일+, 10코인+) → 수집 스킵")
        args.skip_collect = True

    if not args.skip_collect:
        tg("[STEP 1/4] 1분봉 수집 (일별 gzip jsonl, 증분, 4 worker 병렬)")
        collect(days=args.days, top_n=args.coins)

    # 분석
    coins = get_saved_coins()
    if not coins:
        tg("[오류] 데이터 없음")
        _release_lock()
        tg("\n프로그램을 종료합니다.")
        sys.exit(0)

    tg(f"\n[STEP 2/4] 분석 시작: {len(coins)}코인")
    # BTC 레짐: 1분봉 로드 → 5분봉 합성 → 300봉(=60*5분) 회귀
    btc_regime = {}
    btc1 = load_candles("BTC", 1)
    if btc1 and len(btc1) >= 300:
        btc5 = make_nmin(btc1, 5)
        for i in range(60, len(btc5)):
            ret=(btc5[i]["c"]-btc5[i-60]["c"])/btc5[i-60]["c"]*100
            t=_tk16(btc5[i]["t"])
            btc_regime[t]="상승" if ret>0.5 else ("하락" if ret<-0.5 else "횡보")
        tg(f"  BTC 레짐: {len(btc_regime)}시점 (1m→5m 합성)")
        del btc5
    else:
        tg("  BTC 레짐: 1분봉 부족 → 전부 '횡보' 처리")
    del btc1
    _force_free()  # BTC 1분봉 메모리 OS 반환

    MEM_LIMIT_MB = 400  # 메모리 상한 (OOM 방지)
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

        # 메모리 감시: 상한 초과 시 잔여 코인 스킵
        mem = _get_mem_mb()
        if mem > MEM_LIMIT_MB:
            tg(f"[메모리경고] {mem:.0f}MB > {MEM_LIMIT_MB}MB | {i}/{len(coins)}코인 완료, 나머지 스킵")
            break

        # 코인별 상세 로그 (죽는 지점 추적)
        if i >= 9:
            tg(f"[코인시작] {i+1}/{len(coins)} {coin} | mem={mem:.0f}MB")

        try:
            cr = process_coin(coin, btc_regime, dist_acc)
            for st,sigs in cr.items():
                all_results[st].extend(sigs)
            del cr
        except Exception as e:
            import traceback
            tg(f"[분석에러] {coin}: {e}\n{traceback.format_exc()[-300:]}")

        # 매 코인마다 GC + malloc_trim (OOM 방지: OS에 메모리 실제 반환)
        _force_free()

        if i >= 9:
            mem = _get_mem_mb()
            tg(f"[코인완료] {i+1}/{len(coins)} {coin} | mem={mem:.0f}MB")
        if (i+1) % 5 == 0:
            total_elapsed = time.time() - analysis_start
            avg_per_coin = total_elapsed / (i+1)
            remaining = avg_per_coin * (len(coins) - i - 1)
            mem = _get_mem_mb()
            tg(f"  분석 {i+1}/{len(coins)} 완료 ({total_elapsed:.0f}초, 예상잔여 {remaining:.0f}초) | mem={mem:.0f}MB")

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
    del dist_acc

    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now(KST).strftime("%Y%m%d_%H%M")
    fpath = os.path.join(OUT_DIR, f"signal_v4_{ts}.txt")
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
