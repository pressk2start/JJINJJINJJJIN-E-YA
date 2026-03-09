# -*- coding: utf-8 -*-
"""
업비트 신호 연구 v1.0 — 수집 + 분석 통합
==========================================
1) 거래량 상위 코인 자동 선정
2) 5분봉/15분봉 30일치 수집 → 코인별 JSON 저장 (이미 있으면 스킵)
3) 다양한 "상승 신호" 정의 비교 분석
4) 조건별 승률/수익률 통계
5) 리포트 파일 저장 + 텔레그램 전송(선택)

사용법:
  python upbit_signal_study.py                  # 기본 (30일, 30코인)
  python upbit_signal_study.py --days 60        # 60일치
  python upbit_signal_study.py --coins 50       # 50코인
  python upbit_signal_study.py --skip-collect   # 수집 스킵 (분석만)
  python upbit_signal_study.py --include-1m     # 1분봉도 수집

저장 구조:
  ./candle_data/BTC_5m.json
  ./candle_data/BTC_15m.json
  ./analysis_output/signal_study_20260309_1800.txt
"""
import requests, time, json, os, sys, gc, argparse, statistics
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ================================================================
# 설정
# ================================================================
KST = timezone(timedelta(hours=9))
BASE = "https://api.upbit.com/v1"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "candle_data")
OUT_DIR = os.path.join(SCRIPT_DIR, "analysis_output")

# 텔레그램 (선택 — .env 있으면 자동 연결)
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass
TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TG_TOKEN") or ""
_raw_chats = os.getenv("TG_CHATS") or os.getenv("TELEGRAM_CHAT_ID") or ""
CHAT_IDS = []
for p in _raw_chats.split(","):
    p = p.strip()
    if p:
        try: CHAT_IDS.append(int(p))
        except: pass

def tg(msg):
    """콘솔 출력 + 텔레그램 전송"""
    print(msg)
    if not TG_TOKEN or not CHAT_IDS:
        return
    for cid in CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": cid, "text": msg[:4000], "disable_web_page_preview": True},
                timeout=10
            )
        except:
            pass

def safe_get(url, params=None, retries=4, timeout=10):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(2 + i * 2)
                continue
            time.sleep(0.5 + i)
        except:
            time.sleep(1 + i)
    return None


# ================================================================
# PART 1: 데이터 수집
# ================================================================

def get_top_markets(n=30):
    tickers = safe_get(f"{BASE}/market/all", {"isDetails": "true"})
    if not tickers:
        return []
    krw = [t["market"] for t in tickers if t["market"].startswith("KRW-")]
    stable = {"USDT", "USDC", "DAI", "TUSD", "BUSD"}
    krw = [m for m in krw if m.split("-")[1] not in stable]
    all_t = []
    for i in range(0, len(krw), 100):
        b = safe_get(f"{BASE}/ticker", {"markets": ",".join(krw[i:i+100])})
        if b: all_t.extend(b)
        time.sleep(0.15)
    all_t.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
    return [t["market"] for t in all_t[:n]]


def fetch_candles(unit, market, total_count):
    """캔들 수집 (페이징, 시간순 반환)"""
    result, to = [], None
    pages = (total_count + 199) // 200
    for page in range(pages):
        params = {"market": market, "count": min(200, total_count - len(result))}
        if to:
            params["to"] = to
        data = safe_get(f"{BASE}/candles/minutes/{unit}", params)
        if not data:
            break
        result.extend(data)
        if len(data) < 200:
            break
        to = data[-1]["candle_date_time_utc"] + "Z"
        if (page + 1) % 20 == 0:
            print(f"    📥 {len(result)}/{total_count}")
        time.sleep(0.12)
    result.sort(key=lambda x: x["candle_date_time_kst"])
    return result


def compress(c):
    """캔들 경량화 (~80바이트)"""
    return {
        "t": c["candle_date_time_kst"],
        "o": c["opening_price"],
        "h": c["high_price"],
        "l": c["low_price"],
        "c": c["trade_price"],
        "v": round(c.get("candle_acc_trade_volume", 0), 6),
        "vk": round(c.get("candle_acc_trade_price", 0)),
    }


def save_coin(coin, unit, candles):
    os.makedirs(DATA_DIR, exist_ok=True)
    compressed = [compress(c) for c in candles]
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump({
            "coin": coin, "unit": unit, "count": len(compressed),
            "from": compressed[0]["t"] if compressed else "",
            "to": compressed[-1]["t"] if compressed else "",
            "collected_at": datetime.now(KST).isoformat(),
            "candles": compressed
        }, f, ensure_ascii=False)
    kb = os.path.getsize(fpath) / 1024
    print(f"    💾 {coin}_{unit}m.json: {len(compressed)}개, {kb:.0f}KB")


def is_collected(coin, unit, min_count=100):
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath):
        return False
    try:
        with open(fpath) as f:
            return json.load(f).get("count", 0) >= min_count
    except:
        return False


def collect(days=30, top_n=30, include_1m=False):
    tg(f"📥 데이터 수집 시작: {days}일, {top_n}코인")
    markets = get_top_markets(top_n)
    if not markets:
        tg("❌ 종목 가져오기 실패!")
        return []
    coins = [m.split("-")[1] for m in markets]
    tg(f"📋 종목: {', '.join(coins)}")

    timeframes = [("5", 288), ("15", 96)]
    if include_1m:
        timeframes.insert(0, ("1", 1440))

    t0 = time.time()
    for i, market in enumerate(markets):
        coin = market.split("-")[1]
        print(f"\n[{i+1}/{top_n}] {coin}")

        for tf, per_day in timeframes:
            total = per_day * days
            if is_collected(coin, int(tf), total // 2):
                print(f"    ✅ {tf}분봉 이미 있음")
                continue
            print(f"    📥 {tf}분봉 {total}개 수집...")
            candles = fetch_candles(int(tf), market, total)
            if candles:
                save_coin(coin, int(tf), candles)
                del candles
                gc.collect()
            else:
                print(f"    ❌ 실패")
            time.sleep(0.3)

        elapsed = time.time() - t0
        eta = elapsed / (i+1) * (top_n - i - 1)
        print(f"    ⏱️ {elapsed/60:.1f}분 경과, 잔여 ~{eta/60:.1f}분")

    tg(f"✅ 수집 완료! {(time.time()-t0)/60:.1f}분 소요")
    return coins


# ================================================================
# PART 2: 분석
# ================================================================

def load_coin_data(coin, unit):
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath):
        return []
    with open(fpath, encoding="utf-8") as f:
        return json.load(f).get("candles", [])


def get_saved_coins():
    if not os.path.exists(DATA_DIR):
        return []
    return sorted(set(f.replace("_5m.json","") for f in os.listdir(DATA_DIR) if f.endswith("_5m.json")))


def make_15m(c5):
    out = []
    for i in range(0, len(c5)-2, 3):
        a, b, c = c5[i], c5[i+1], c5[i+2]
        out.append({
            "t": a["t"], "o": a["o"], "c": c["c"],
            "h": max(a["h"],b["h"],c["h"]),
            "l": min(a["l"],b["l"],c["l"]),
            "v": a["v"]+b["v"]+c["v"],
            "_si": i
        })
    return out


# --- 지표 ---
def rsi(closes, period=14):
    if len(closes) < period+1: return 50
    g, l = 0, 0
    for i in range(len(closes)-period, len(closes)):
        d = closes[i]-closes[i-1]
        if d > 0: g += d
        else: l -= d
    return 100 - 100/(1+g/max(l,1e-9)) if l else 100

def bb_pos(closes, period=20):
    if len(closes) < period: return 50
    s = closes[-period:]
    m = sum(s)/period
    std = (sum((x-m)**2 for x in s)/period)**0.5
    u, lo = m+2*std, m-2*std
    return (closes[-1]-lo)/(u-lo)*100 if u != lo else 50

def ema_val(closes, period):
    if len(closes) < period: return closes[-1] if closes else 0
    k = 2/(period+1)
    e = sum(closes[:period])/period
    for v in closes[period:]: e = v*k + e*(1-k)
    return e


# --- 신호 정의 + outcome 측정 ---
def process_coin(coin):
    """코인 하나에 대해 모든 신호 유형 감지 + outcome 측정"""
    c5 = load_coin_data(coin, 5)
    if len(c5) < 100:
        return {}

    c15 = load_coin_data(coin, 15)
    if not c15:
        c15 = make_15m(c5)

    results = {}  # {signal_type: [signals]}

    # ====== 신호 1: 15분봉 눌림반전 ======
    sigs = []
    last = -999
    for i in range(12, len(c15)):
        bar, prev = c15[i], c15[i-1]
        if prev["c"] >= prev["o"]: continue  # 이전봉 음봉이어야
        if bar["c"] <= bar["o"]: continue     # 현재봉 양봉이어야
        if (bar["c"]-bar["o"])/bar["o"]*100 < 0.1: continue
        si = bar.get("_si", i*3) + 2
        if si - last < 10 or si >= len(c5)-30: continue
        last = si
        sig = _features(coin, c5, c15, i, si, bar)
        if sig: sigs.append(sig)
    results["15m_눌림반전"] = sigs

    # ====== 신호 2: 5분봉 양봉 ======
    sigs = []
    last = -999
    for i in range(30, len(c5)-30):
        c = c5[i]
        if c["c"] <= c["o"]: continue
        if (c["c"]-c["o"])/c["o"]*100 < 0.1: continue
        if i-last < 10: continue
        last = i
        sig = _features(coin, c5, c15, min(i//3, len(c15)-1), i, c)
        if sig: sigs.append(sig)
    results["5m_양봉"] = sigs

    # ====== 신호 3: 20봉 고점돌파 ======
    sigs = []
    last = -999
    for i in range(30, len(c5)-30):
        c = c5[i]
        if c["c"] <= c["o"]: continue
        ph = max(c5[j]["h"] for j in range(max(0,i-20), i))
        if c["h"] <= ph: continue
        if i-last < 10: continue
        last = i
        sig = _features(coin, c5, c15, min(i//3,len(c15)-1), i, c)
        if sig: sigs.append(sig)
    results["20봉_고점돌파"] = sigs

    # ====== 신호 4: 거래량3배+양봉 ======
    sigs = []
    last = -999
    for i in range(30, len(c5)-30):
        c = c5[i]
        if c["c"] <= c["o"]: continue
        vs = [c5[j]["v"] for j in range(max(0,i-5),i)]
        if c["v"] < sum(vs)/max(len(vs),1) * 3: continue
        if i-last < 10: continue
        last = i
        sig = _features(coin, c5, c15, min(i//3,len(c15)-1), i, c)
        if sig: sigs.append(sig)
    results["거래량3배"] = sigs

    # ====== 신호 5: 15분봉 눌림반전+이전고가돌파 ======
    sigs = []
    last = -999
    for i in range(12, len(c15)):
        bar, prev = c15[i], c15[i-1]
        if prev["c"] >= prev["o"]: continue
        if bar["c"] <= bar["o"]: continue
        if (bar["c"]-bar["o"])/bar["o"]*100 < 0.1: continue
        if bar["c"] <= prev["h"]: continue  # 이전 15분봉 고가 돌파
        si = bar.get("_si", i*3) + 2
        if si - last < 10 or si >= len(c5)-30: continue
        last = si
        sig = _features(coin, c5, c15, i, si, bar)
        if sig: sigs.append(sig)
    results["15m_눌림+돌파"] = sigs

    del c5, c15
    gc.collect()
    return results


def _features(coin, c5, c15, i15, i5, bar):
    """신호의 feature + outcome 계산"""
    entry = bar["c"]
    if entry <= 0: return None
    try:
        hour = int(bar["t"][11:13])
    except:
        hour = 12

    # 5분봉 지표
    cl5 = [c5[j]["c"] for j in range(max(0,i5-29), i5+1)]
    r5 = rsi(cl5, 14)
    b5 = bb_pos(cl5, 20)

    # 15분봉 지표
    cl15 = [c15[j]["c"] for j in range(max(0,i15-19), i15+1)]
    r15 = rsi(cl15, 14)
    b15 = bb_pos(cl15, 20)

    # 거래량비
    vs = [c5[j]["v"] for j in range(max(0,i5-5), i5)]
    vr = c5[i5]["v"] / (sum(vs)/max(len(vs),1)) if vs else 1

    # EMA, 모멘텀
    e20 = ema_val(cl15, 20) if len(cl15) >= 20 else entry
    above_ema = 1 if entry > e20 else 0
    mom5 = (entry - c5[max(0,i5-5)]["c"]) / c5[max(0,i5-5)]["c"] * 100 if i5>=5 else 0
    mom10 = (entry - c5[max(0,i5-10)]["c"]) / c5[max(0,i5-10)]["c"] * 100 if i5>=10 else 0

    # 패턴
    dip3 = 1 if (i5>=3 and c5[i5-3]["c"] > c5[i5-1]["c"]) else 0
    brk = 1 if (i5>=1 and bar["c"] > c5[i5-1]["h"]) else 0
    body = (bar["c"]-bar["o"])/bar["o"]*100

    # === Outcome: 30봉 추적 + 다중 SL/Trail ===
    maxL = min(30, len(c5) - i5 - 1)
    mfe, mae, mfe_bar = 0, 0, 0
    for j in range(1, maxL+1):
        fc = c5[i5+j]
        hi = (fc["h"]-entry)/entry*100
        lo = (fc["l"]-entry)/entry*100
        if hi > mfe: mfe, mfe_bar = hi, j
        if lo < mae: mae = lo

    oc = {}
    for sl in [0.5, 0.7, 1.0]:
        for arm in [0.2, 0.3]:
            for trail in [0.1, 0.15, 0.2, 0.3]:
                if trail > arm: continue
                mp, ar, ep = 0, False, 0
                for j in range(1, maxL+1):
                    fc = c5[i5+j]
                    hi = (fc["h"]-entry)/entry*100
                    lo = (fc["l"]-entry)/entry*100
                    cl = (fc["c"]-entry)/entry*100
                    if hi > mp: mp = hi
                    if lo <= -sl: ep = -sl; break
                    if mp >= arm: ar = True
                    if ar and (mp-lo) >= trail: ep = max(mp-trail, -sl); break
                    if j == maxL: ep = cl
                oc[f"SL{sl}/A{arm}/T{trail}"] = round(ep, 4)

    return {
        "coin": coin, "time": bar["t"], "hour": hour, "entry": entry,
        "rsi5": round(r5,1), "rsi15": round(r15,1),
        "bb5": round(b5,1), "bb15": round(b15,1),
        "vr": round(vr,2), "body": round(body,3),
        "above_ema": above_ema, "mom5": round(mom5,3), "mom10": round(mom10,3),
        "dip3": dip3, "brk": brk,
        "mfe": round(mfe,4), "mae": round(mae,4), "mfe_bar": mfe_bar,
        "oc": oc,
    }


# ================================================================
# PART 3: 리포트
# ================================================================

def report(all_results):
    lines = []
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    lines.append(f"📊 업비트 신호 연구 리포트 ({ts})")
    lines.append("="*60)

    dk = "SL0.7/A0.3/T0.2"  # default key

    # --- 신호 유형 비교 ---
    lines.append(f"\n## 신호 유형별 비교 (기준: {dk})")
    lines.append(f"{'유형':<20s} {'n':>6s} {'WR%':>6s} {'avgPnL':>8s} {'MFE중앙':>8s}")
    lines.append("-"*55)

    for stype, sigs in sorted(all_results.items(), key=lambda x: -_avg_pnl(x[1], dk)):
        if not sigs: continue
        n = len(sigs)
        pnls = [s["oc"].get(dk, 0) for s in sigs]
        wr = sum(1 for p in pnls if p > 0) / n * 100
        avg = sum(pnls) / n
        mfes = sorted([s["mfe"] for s in sigs])
        med_mfe = mfes[n//2]
        star = " ★" if avg > 0 else ""
        lines.append(f"{stype:<20s} {n:>6d} {wr:>5.1f}% {avg:>+7.4f}% {med_mfe:>7.3f}%{star}")

    # --- 각 신호 유형별 상세 분석 ---
    for stype, sigs in all_results.items():
        if len(sigs) < 20: continue
        lines.append(f"\n{'#'*60}")
        lines.append(f"# {stype} (n={len(sigs)})")
        lines.append(f"{'#'*60}")

        # SL/Trail 조합
        lines.append(f"\n  [SL/Trail 조합]")
        combos = []
        for key in sorted(set(k for s in sigs for k in s["oc"])):
            pnls = [s["oc"].get(key, 0) for s in sigs]
            w = sum(1 for p in pnls if p > 0)
            avg = sum(pnls)/len(sigs)
            combos.append((key, w/len(sigs)*100, avg))
        combos.sort(key=lambda x: -x[2])
        for k, wr, avg in combos[:8]:
            star = " ★" if avg > 0 else ""
            lines.append(f"  {k:20s}: WR={wr:5.1f}% avg={avg:+.4f}%{star}")

        # 조건별 승률
        def cwr(name, fn):
            sub = [s for s in sigs if fn(s)]
            if len(sub) < 5: return
            pnls = [s["oc"].get(dk,0) for s in sub]
            w = sum(1 for p in pnls if p > 0)
            avg = sum(pnls)/len(sub)
            star = " ★" if avg > 0 else ""
            lines.append(f"  {name:30s}: n={len(sub):5d} WR={w/len(sub)*100:5.1f}% avg={avg:+.4f}%{star}")

        lines.append(f"\n  [RSI 5분봉]")
        for lo,hi in [(0,30),(30,40),(40,50),(50,60),(60,100)]:
            cwr(f"RSI5 {lo}-{hi}", lambda s,lo=lo,hi=hi: lo<=s["rsi5"]<hi)

        lines.append(f"\n  [RSI 15분봉]")
        for lo,hi in [(0,30),(30,40),(40,50),(50,60),(60,100)]:
            cwr(f"RSI15 {lo}-{hi}", lambda s,lo=lo,hi=hi: lo<=s["rsi15"]<hi)

        lines.append(f"\n  [BB 15분봉]")
        for lo,hi in [(0,20),(20,40),(40,60),(60,80),(80,100)]:
            cwr(f"BB15 {lo}-{hi}", lambda s,lo=lo,hi=hi: lo<=s["bb15"]<hi)

        lines.append(f"\n  [BB 5분봉]")
        for lo,hi in [(0,20),(20,40),(40,60),(60,80),(80,100)]:
            cwr(f"BB5 {lo}-{hi}", lambda s,lo=lo,hi=hi: lo<=s["bb5"]<hi)

        lines.append(f"\n  [시간대]")
        for h in range(24):
            cwr(f"{h:02d}시", lambda s,h=h: s["hour"]==h)

        lines.append(f"\n  [진입 패턴]")
        cwr("눌림3봉(dip3)", lambda s: s["dip3"]==1)
        cwr("이전봉돌파(brk)", lambda s: s["brk"]==1)
        cwr("눌림+이전봉돌파", lambda s: s["dip3"]==1 and s["brk"]==1)
        cwr("EMA 위", lambda s: s["above_ema"]==1)
        cwr("EMA 아래", lambda s: s["above_ema"]==0)

        lines.append(f"\n  [복합 필터]")
        for name, fn in [
            ("BB15<20", lambda s: s["bb15"]<20),
            ("BB15<30", lambda s: s["bb15"]<30),
            ("BB15<40", lambda s: s["bb15"]<40),
            ("RSI15<50+BB15<40", lambda s: s["rsi15"]<50 and s["bb15"]<40),
            ("RSI15<40+BB15<30", lambda s: s["rsi15"]<40 and s["bb15"]<30),
            ("RSI5<50+BB5<40", lambda s: s["rsi5"]<50 and s["bb5"]<40),
            ("BB15<40+눌림+돌파", lambda s: s["bb15"]<40 and s["dip3"]==1 and s["brk"]==1),
            ("BB15<30+RSI15<50+돌파", lambda s: s["bb15"]<30 and s["rsi15"]<50 and s["brk"]==1),
        ]:
            cwr(name, fn)

        # 최적 필터 × Trail 최적화
        lines.append(f"\n  [최적 필터 × Trail 최적화]")
        for fname, ffn in [
            ("BB15<20", lambda s: s["bb15"]<20),
            ("RSI15<50+BB15<40", lambda s: s["rsi15"]<50 and s["bb15"]<40),
        ]:
            sub = [s for s in sigs if ffn(s)]
            if len(sub) < 10: continue
            lines.append(f"\n    [{fname}] n={len(sub)}")
            best = []
            for key in sorted(set(k for s in sub for k in s["oc"])):
                pnls = [s["oc"].get(key,0) for s in sub]
                w = sum(1 for p in pnls if p>0)
                avg = sum(pnls)/len(sub)
                best.append((key, w/len(sub)*100, avg))
            best.sort(key=lambda x: -x[2])
            for k, wr, avg in best[:5]:
                star = " ★" if avg > 0 else ""
                lines.append(f"    {k:20s}: WR={wr:5.1f}% avg={avg:+.4f}%{star}")

    return lines


def _avg_pnl(sigs, key):
    if not sigs: return -999
    return sum(s["oc"].get(key,0) for s in sigs) / len(sigs)


# ================================================================
# 메인
# ================================================================
def main():
    parser = argparse.ArgumentParser(description="업비트 신호 연구")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--coins", type=int, default=30)
    parser.add_argument("--skip-collect", action="store_true", help="수집 스킵, 분석만")
    parser.add_argument("--include-1m", action="store_true")
    args = parser.parse_args()

    t0 = time.time()

    # === 수집 ===
    if not args.skip_collect:
        collect(days=args.days, top_n=args.coins, include_1m=args.include_1m)

    # === 분석 ===
    coins = get_saved_coins()
    if not coins:
        tg(f"❌ 데이터 없음! {DATA_DIR}")
        return

    tg(f"\n📊 분석 시작: {len(coins)}개 코인")

    all_results = defaultdict(list)  # {signal_type: [signals]}

    for i, coin in enumerate(coins):
        coin_results = process_coin(coin)
        for stype, sigs in coin_results.items():
            all_results[stype].extend(sigs)
        if (i+1) % 10 == 0:
            counts = {k: len(v) for k, v in all_results.items()}
            print(f"  진행 {i+1}/{len(coins)}: {counts}")

    # 요약
    tg(f"\n📊 신호 감지 완료:")
    for stype, sigs in all_results.items():
        tg(f"  {stype}: {len(sigs)}건")

    # 리포트 생성
    lines = report(dict(all_results))

    # 저장
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now(KST).strftime("%Y%m%d_%H%M")
    fpath = os.path.join(OUT_DIR, f"signal_study_{ts}.txt")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    elapsed = (time.time()-t0)/60
    tg(f"\n✅ 완료! {elapsed:.1f}분 소요")
    tg(f"💾 리포트: {fpath}")

    # 텔레그램에 핵심 결과 전송
    summary = "\n".join(lines[:30])
    tg(f"\n{summary}")

    # 데이터 사이즈
    if os.path.exists(DATA_DIR):
        files = os.listdir(DATA_DIR)
        total_mb = sum(os.path.getsize(os.path.join(DATA_DIR, f)) for f in files) / 1024 / 1024
        tg(f"📁 데이터: {len(files)}파일, {total_mb:.1f}MB")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        tg("⏹️ 중단됨 (이미 저장된 데이터는 유지)")
    except Exception as e:
        import traceback
        tg(f"❌ 에러: {e}\n{traceback.format_exc()[-500:]}")
