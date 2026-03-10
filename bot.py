# -*- coding: utf-8 -*-
"""
업비트 신호 연구 v3.1 — 메모리 최적화
======================================
- 수집: 1m,3m,5m,15m,30m,1h (6TF)
- 분석: TF별 순차 로드→피처추출→해제 (메모리 안전)
- 지표: TF당 ~35개 피처, 총 200+개
- 진입: 다음 캔들 시가 (미래 누출 없음)
- 수수료 0.18% 반영
- EV/PF/MDD/손익비 중심 평가
- Train/Test 70/30

사용법:
  python upbit_signal_study.py
  python upbit_signal_study.py --days 60
  python upbit_signal_study.py --skip-collect
"""
import requests, time, json, os, sys, gc, argparse, math
from datetime import datetime, timedelta, timezone
from collections import defaultdict

KST = timezone(timedelta(hours=9))
BASE = "https://api.upbit.com/v1"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "candle_data")
OUT_DIR = os.path.join(SCRIPT_DIR, "analysis_output")

FEE_RATE = 0.0005
SLIPPAGE = 0.0008
TOTAL_COST = FEE_RATE * 2 + SLIPPAGE
MIN_TRADES = 100
TRAIN_RATIO = 0.7
MAX_HOLD_BARS = 60

TIMEFRAMES = [(1, 1440), (3, 480), (5, 288), (15, 96), (30, 48), (60, 24)]
# 분석에 사용할 TF (1분봉 포함 — 실제 봇이 1분봉으로 진입)
ANALYSIS_TFS = [1, 3, 5, 15, 30, 60]

# 핵심 exit 전략만 (15개로 축소)
EXIT_CONFIGS = [
    ("FIX_TP0.5/SL0.5", "fix", 0.5, 0.5, 0, 0),
    ("FIX_TP0.7/SL0.7", "fix", 0.7, 0.7, 0, 0),
    ("FIX_TP1.0/SL0.7", "fix", 1.0, 0.7, 0, 0),
    ("FIX_TP1.0/SL1.0", "fix", 1.0, 1.0, 0, 0),
    ("FIX_TP1.5/SL1.0", "fix", 1.5, 1.0, 0, 0),
    ("TRAIL_SL0.7/A0.3/T0.2", "trail", 0, 0.7, 0.3, 0.2),
    ("TRAIL_SL0.7/A0.5/T0.3", "trail", 0, 0.7, 0.5, 0.3),
    ("TRAIL_SL1.0/A0.3/T0.2", "trail", 0, 1.0, 0.3, 0.2),
    ("TRAIL_SL1.0/A0.5/T0.3", "trail", 0, 1.0, 0.5, 0.3),
    ("HOLD_6봉", "hold", 0, 0, 0, 6),
    ("HOLD_12봉", "hold", 0, 0, 0, 12),
    ("HOLD_24봉", "hold", 0, 0, 0, 24),
    ("TIME12_SL0.7", "time", 0, 0.7, 0, 12),
    ("TIME24_SL0.7", "time", 0, 0.7, 0, 24),
    ("TIME24_SL1.0", "time", 0, 1.0, 0, 24),
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

def tg(msg):
    print(msg)
    if not TG_TOKEN or not CHAT_IDS:
        return
    for cid in CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": cid, "text": msg[:4000], "disable_web_page_preview": True},
                timeout=10)
        except: pass

def safe_get(url, params=None, retries=4, timeout=10):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200: return r.json()
            if r.status_code == 429: time.sleep(2 + i*2); continue
            time.sleep(0.5 + i)
        except: time.sleep(1 + i)
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

def fetch_candles(unit, market, total_count):
    result, to = [], None
    for page in range((total_count+199)//200):
        params = {"market": market, "count": min(200, total_count-len(result))}
        if to: params["to"] = to
        data = safe_get(f"{BASE}/candles/minutes/{unit}", params)
        if not data: break
        result.extend(data)
        if len(data) < 200: break
        to = data[-1]["candle_date_time_utc"] + "Z"
        time.sleep(0.12)
    result.sort(key=lambda x: x["candle_date_time_kst"])
    return result

def compress(c):
    return {"t":c["candle_date_time_kst"],"o":c["opening_price"],"h":c["high_price"],
            "l":c["low_price"],"c":c["trade_price"],
            "v":round(c.get("candle_acc_trade_volume",0),6)}

def save_coin(coin, unit, candles):
    os.makedirs(DATA_DIR, exist_ok=True)
    comp = [compress(c) for c in candles]
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    with open(fpath,"w",encoding="utf-8") as f:
        json.dump({"coin":coin,"unit":unit,"count":len(comp),
                    "from":comp[0]["t"] if comp else "",
                    "to":comp[-1]["t"] if comp else "",
                    "candles":comp}, f, ensure_ascii=False)

def is_collected(coin, unit, min_count=100):
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath): return False
    try:
        with open(fpath) as f: return json.load(f).get("count",0) >= min_count
    except: return False

def collect(days=30, top_n=30):
    tg(f"[수집] {days}일, {top_n}코인, {len(TIMEFRAMES)}개 타임프레임")
    markets = get_top_markets(top_n)
    if not markets: tg("[오류] 종목 실패"); return []
    coins = [m.split("-")[1] for m in markets]
    tg(f"종목: {', '.join(coins)}")
    tg(f"타임프레임: {', '.join(f'{tf}m' for tf,_ in TIMEFRAMES)}")
    t0 = time.time()
    for i, market in enumerate(markets):
        coin = market.split("-")[1]
        try:
            for tf, per_day in TIMEFRAMES:
                total = per_day * days
                if tf == 1 and days > 7: total = 1440*7
                if is_collected(coin, tf, total//2): continue
                candles = fetch_candles(tf, market, total)
                if candles: save_coin(coin, tf, candles); del candles; gc.collect()
                time.sleep(0.3)
        except Exception as e:
            tg(f"[수집에러] {coin}: {e}")
            continue
        if (i+1) % 5 == 0:
            el = time.time()-t0; eta = el/(i+1)*(top_n-i-1)
            tg(f"  수집 {i+1}/{top_n} ({el/60:.1f}분, ~{eta/60:.1f}분 남음)")
    tg(f"[수집완료] {(time.time()-t0)/60:.1f}분")
    return coins

# ================================================================
# PART 2: 지표 함수 (과거만)
# ================================================================
def load_candles(coin, unit):
    fpath = os.path.join(DATA_DIR, f"{coin}_{unit}m.json")
    if not os.path.exists(fpath): return []
    with open(fpath, encoding="utf-8") as f: return json.load(f).get("candles",[])

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

def _patterns(candles, idx):
    if idx<2: return {}
    c=candles[idx]; p1=candles[idx-1]; p2=candles[idx-2]
    o,h,l,cl=c["o"],c["h"],c["l"],c["c"]
    rng=h-l if h!=l else 0.0001
    body=abs(cl-o); uw=h-max(o,cl); lw=min(o,cl)-l
    pat = {}
    pat["hammer"]=1 if lw>body*2 and uw<body*0.5 and body>0 else 0
    pat["doji"]=1 if body/rng<0.1 else 0
    p1b=abs(p1["c"]-p1["o"])
    pat["engulf"]=1 if p1["c"]<p1["o"] and cl>o and body>p1b and o<=p1["c"] and cl>=p1["o"] else 0
    cg=0
    for k in range(idx, max(idx-5,-1),-1):
        if candles[k]["c"]>candles[k]["o"]: cg+=1
        else: break
    pat["cg"]=cg
    pat["lw_pct"]=round(lw/rng*100,1)
    pat["body_pct"]=round(body/rng*100,1)
    return pat


# ================================================================
# PART 3: 코인별 분석 (메모리 안전)
# ================================================================
def extract_tf_features(candles, idx, pfx):
    """단일 TF에서 피처 추출. candles[idx]까지만 사용."""
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
    f[pfx+"bb10"]=round(bp10,1); f[pfx+"bb20"]=round(bp20,1)
    f[pfx+"bbw20"]=round(bw20,3)
    ef,es=_ema(cl,12),_ema(cl,26)
    f[pfx+"macd_x"]=1 if ef>es else 0
    f[pfx+"stoch"]=round(_stoch(hi,lo,cl),1)
    atr=_atr(hi,lo,cl); f[pfx+"atr"]=round(atr/price*100,3) if price else 0
    f[pfx+"adx"]=round(_adx(hi,lo,cl),1)
    f[pfx+"cci"]=round(_cci(hi,lo,cl),1)
    f[pfx+"willr"]=round(_willr(hi,lo,cl),1)
    f[pfx+"mfi"]=round(_mfi(hi,lo,cl,vo),1)
    f[pfx+"obv"]=round(_obv_chg(cl,vo),2)
    f[pfx+"disp20"]=round(_disp(cl,20),2)
    f[pfx+"ema_al"]=_ema_align(cl)
    for ep in [5,10,20]:
        ev=_ema(cl,ep)
        f[pfx+f"ed{ep}"]=round((cl[-1]-ev)/ev*100,3) if ev>0 else 0
    f[pfx+"vol20"]=round(_vol(cl,20),3)
    av5=sum(vo[-6:-1])/5 if len(vo)>=6 else 1
    f[pfx+"vr"]=round(vo[-1]/av5,2) if av5>0 else 1
    for mp in [3,5,10]:
        f[pfx+f"m{mp}"]=round((cl[-1]-cl[-(mp+1)])/cl[-(mp+1)]*100,3) if len(cl)>mp and cl[-(mp+1)]>0 else 0
    if len(cl)>=20:
        hh,ll=max(cl[-20:]),min(cl[-20:])
        f[pfx+"pos20"]=round((cl[-1]-ll)/(hh-ll)*100,1) if hh!=ll else 50.0
    cp=_patterns(candles,idx)
    for k,v in cp.items(): f[pfx+k]=v
    f[pfx+"green"]=1 if candles[idx]["c"]>candles[idx]["o"] else 0
    return f


def find_idx(candles, sig_time):
    """이진탐색"""
    lo,hi=0,len(candles)-1; r=None
    while lo<=hi:
        mid=(lo+hi)//2
        if candles[mid]["t"]<=sig_time: r=mid; lo=mid+1
        else: hi=mid-1
    return r


def process_coin(coin, btc_regime):
    """코인 1개: 5분봉 기반 신호 → TF별 순차 피처 추출 → 청산 시뮬"""
    tg(f"  >> {coin} 분석 시작")
    c5 = load_candles(coin, 5)
    if len(c5) < 200: return {}

    # 15분봉 합성 (5분봉에서)
    c15 = make_nmin(c5, 3)

    # 1단계: 신호 감지 → (sig_bar_i5, entry_idx, sig_bar) 목록만 모음
    raw_signals = {}  # {type: [(sig_i5, entry_idx, sig_bar), ...]}

    # 15m 눌림반전
    for name, req_brk in [("15m_눌림반전", False), ("15m_눌림+돌파", True)]:
        sigs = []
        last = -20
        for i in range(14, len(c15)):
            bar, prev = c15[i], c15[i-1]
            if prev["c"]>=prev["o"] or bar["c"]<=bar["o"]: continue
            if (bar["c"]-bar["o"])/bar["o"]*100<0.1: continue
            if req_brk and bar["c"]<=prev["h"]: continue
            si = bar.get("_si", i*3)+2
            ei = si+1
            if ei-last<10 or ei>=len(c5)-MAX_HOLD_BARS: continue
            last=ei; sigs.append((si, ei, bar))
        raw_signals[name] = sigs

    # 5m 양봉
    sigs = []; last=-30
    for i in range(60, len(c5)-MAX_HOLD_BARS-1):
        bar=c5[i]
        if bar["c"]<=bar["o"]: continue
        if (bar["c"]-bar["o"])/bar["o"]*100<0.15: continue
        ei=i+1
        if ei-last<30: continue  # 간격 30봉 (2.5시간)
        last=ei; sigs.append((i, ei, bar))
    raw_signals["5m_양봉"] = sigs

    # 20봉 고점돌파
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

    # 거래량3배
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

    total_sigs = sum(len(v) for v in raw_signals.values())
    tg(f"    {coin} 신호: {total_sigs}개 ({', '.join(f'{k}:{len(v)}' for k,v in raw_signals.items())})")

    if total_sigs == 0:
        del c5, c15; gc.collect()
        return {}

    # 2단계: 모든 신호의 시간 목록 수집
    all_sig_times = set()
    for sigs in raw_signals.values():
        for si5, ei, bar in sigs:
            all_sig_times.add(bar["t"])

    # 3단계: TF별 순차 로드 → 피처 추출 → 해제
    # sig_time → {feat_key: val} 캐시 (시간으로 중복 방지)
    tf_feat_cache = defaultdict(dict)  # {sig_time: {pfx_key: val}}

    for tf in ANALYSIS_TFS:
        if tf == 5:
            candles = c5
        else:
            candles = load_candles(coin, tf)
            if not candles or len(candles)<60:
                # 합성 시도
                if tf==15: candles=c15
                elif tf==30: candles=make_nmin(c5,6)
                elif tf==60: candles=make_nmin(c5,12)
                elif tf==3: candles=None
                elif tf==1: candles=None  # 1분봉 없으면 스킵 (합성 불가)
                if not candles or len(candles)<60:
                    continue

        # 시간→인덱스 매핑 (1회)
        if tf == 5:
            # 5분봉은 직접 인덱스 사용하므로 매핑 불필요
            time_map = None
        else:
            time_map = {c["t"][:16]: i for i,c in enumerate(candles)}

        pfx = f"tf{tf}_"
        for sig_time in all_sig_times:
            if tf == 5:
                continue  # 5분봉은 별도 처리
            tk = sig_time[:16]
            idx = time_map.get(tk)
            if idx is None:
                idx = find_idx(candles, sig_time)
            if idx is not None and idx >= 55:
                feats = extract_tf_features(candles, idx, pfx)
                tf_feat_cache[sig_time].update(feats)

        if tf != 5:
            del candles
            gc.collect()

    # 5분봉 피처는 sig_i5로 직접
    pfx5 = "tf5_"
    for sigs in raw_signals.values():
        for si5, ei, bar in sigs:
            if si5 >= 55:
                feats = extract_tf_features(c5, si5, pfx5)
                tf_feat_cache[bar["t"]].update(feats)

    # 4단계: 신호 → 완성된 결과 dict
    results = {}
    cost_pct = TOTAL_COST * 100

    for stype, sigs_raw in raw_signals.items():
        built = []
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

            # 청산 시뮬
            exits = {}
            for ename, etype, tp, sl, arm, trail_or_n in EXIT_CONFIGS:
                if etype=="fix":
                    exits[ename]=_sim_fix(c5,ei,entry,tp,sl,mb,cost_pct)
                elif etype=="trail":
                    exits[ename]=_sim_trail(c5,ei,entry,sl,arm,trail_or_n,mb,cost_pct)
                elif etype=="hold":
                    n=int(trail_or_n)
                    if n<=mb:
                        raw=(c5[ei+n]["c"]-entry)/entry*100
                        exits[ename]=round(raw-cost_pct,4)
                elif etype=="time":
                    n=int(trail_or_n)
                    exits[ename]=_sim_time(c5,ei,entry,sl,n,mb,cost_pct)

            sig = {"coin":coin,"time":bar["t"],"hour":hour,"entry":entry,
                   "regime":regime,"dip3":dip3,"brk":brk,
                   "mfe":round(mfe,4),"mae":round(mae,4),"mfe_bar":mfe_bar,
                   "exits":exits}
            # 다중TF 피처 병합
            sig.update(tf_feat_cache.get(bar["t"], {}))
            built.append(sig)
        results[stype] = built

    del c5, c15, tf_feat_cache, raw_signals
    gc.collect()
    return results


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


def generate_report(all_results):
    L = []
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    L.append(f"업비트 신호 연구 v3.1 ({ts})")
    L.append(f"비용:{TOTAL_COST*100:.2f}% | 진입:다음봉시가 | 최대:{MAX_HOLD_BARS}봉")
    L.append(f"Train/Test:{TRAIN_RATIO*100:.0f}/{(1-TRAIN_RATIO)*100:.0f} | 최소:{MIN_TRADES}건")
    L.append("="*65)

    # 피처 수
    for sigs in all_results.values():
        if sigs:
            tf_f=[k for k in sigs[0] if k.startswith("tf")]
            L.append(f"피처: {len(tf_f)}개 다중TF")
            break

    dk="TRAIL_SL0.7/A0.3/T0.2"

    # [1] 유형 비교
    L.append(f"\n[1] 신호유형 비교 ({dk})")
    L.append("-"*65)
    ranked=[]
    for st,sigs in all_results.items():
        if not sigs: continue
        m=calc_m([s["exits"].get(dk,0) for s in sigs])
        if m: ranked.append((st,m))
    ranked.sort(key=lambda x:-x[1]["ev"])
    for st,m in ranked:
        star=" ***" if m["ev"]>0 and m["n"]>=MIN_TRADES else ""
        L.append(f"  {st:<16s} {fmt(m)}{star}")

    # [2] 상세
    for st, sigs in all_results.items():
        if len(sigs)<30: continue
        L.append(f"\n{'='*65}")
        L.append(f"[2] {st} (n={len(sigs)})")

        si=int(len(sigs)*TRAIN_RATIO)
        tr,te=sigs[:si],sigs[si:]

        for nm,sub in [("TRAIN",tr),("TEST",te),("ALL",sigs)]:
            if len(sub)<10: continue
            L.append(f"\n  [{nm} n={len(sub)}]")
            L.append(f"  청산전략 TOP5:")
            ek_list=sorted(set(k for s in sub for k in s["exits"]))
            er=[]
            for ek in ek_list:
                m=calc_m([s["exits"].get(ek,0) for s in sub])
                if m: er.append((ek,m))
            er.sort(key=lambda x:-x[1]["ev"])
            for ek,m in er[:5]:
                star=" ***" if m["ev"]>0 else ""
                L.append(f"    {ek:25s} {fmt(m)}{star}")

            if nm!="ALL": continue
            mfes=sorted(s["mfe"] for s in sub)
            maes=sorted(s["mae"] for s in sub)
            L.append(f"\n  MFE: P25={mfes[len(mfes)//4]:+.3f}% P50={mfes[len(mfes)//2]:+.3f}% P75={mfes[3*len(mfes)//4]:+.3f}%")
            L.append(f"  MAE: P25={maes[len(maes)//4]:+.3f}% P50={maes[len(maes)//2]:+.3f}% P75={maes[3*len(maes)//4]:+.3f}%")

        # 조건별
        L.append(f"\n  [조건별 EV] ({dk})")
        def ce(name,fn):
            sub=[s for s in sigs if fn(s)]
            if len(sub)<10: return
            m=calc_m([s["exits"].get(dk,0) for s in sub])
            if m:
                star=" ***" if m["ev"]>0 and m["n"]>=MIN_TRADES else ""
                L.append(f"    {name:32s} {fmt(m)}{star}")

        for tf in ANALYSIS_TFS:
            p=f"tf{tf}_"
            has=[s for s in sigs if p+"rsi14" in s]
            if len(has)<30: continue
            L.append(f"\n    [{tf}분봉]")
            for lo,hi in [(0,30),(30,50),(50,70),(70,100)]:
                ce(f"{tf}m RSI {lo}-{hi}", lambda s,lo=lo,hi=hi,k=p+"rsi14": k in s and lo<=s[k]<hi)
            for lo,hi in [(0,20),(20,50),(50,80),(80,100)]:
                ce(f"{tf}m BB {lo}-{hi}", lambda s,lo=lo,hi=hi,k=p+"bb20": k in s and lo<=s[k]<hi)
            ce(f"{tf}m Stoch<20", lambda s,k=p+"stoch": k in s and s[k]<20)
            ce(f"{tf}m MACD골든", lambda s,k=p+"macd_x": k in s and s[k]==1)
            ce(f"{tf}m ADX>25", lambda s,k=p+"adx": k in s and s[k]>25)
            ce(f"{tf}m MFI<20", lambda s,k=p+"mfi": k in s and s[k]<20)
            ce(f"{tf}m EMA정배열3+", lambda s,k=p+"ema_al": k in s and s[k]>=3)

        L.append(f"\n    [레짐]")
        for r in ["상승","횡보","하락"]:
            ce(f"BTC_{r}", lambda s,r=r: s["regime"]==r)

        L.append(f"\n    [시간]")
        for h in range(24):
            ce(f"{h:02d}시", lambda s,h=h: s["hour"]==h)

        L.append(f"\n    [패턴]")
        ce("눌림+돌파", lambda s: s.get("dip3")==1 and s.get("brk")==1)
        ce("5m 망치형", lambda s: s.get("tf5_hammer")==1)
        ce("5m 감싸기", lambda s: s.get("tf5_engulf")==1)

        L.append(f"\n    [복합필터]")
        combos=[
            ("5m_RSI<50+15m_BB<40", lambda s: s.get("tf5_rsi14",50)<50 and s.get("tf15_bb20",50)<40),
            ("15m_RSI<40+30m_RSI<40", lambda s: s.get("tf15_rsi14",50)<40 and s.get("tf30_rsi14",50)<40),
            ("5m_Stoch<20+15m_BB<40", lambda s: s.get("tf5_stoch",50)<20 and s.get("tf15_bb20",50)<40),
            ("15m_MACD+1h_EMA정배열", lambda s: s.get("tf15_macd_x")==1 and s.get("tf60_ema_al",0)>=3),
            ("15m_BB<20+BTC상승", lambda s: s.get("tf15_bb20",50)<20 and s["regime"]=="상승"),
            ("5m_RSI<40+눌림+돌파", lambda s: s.get("tf5_rsi14",50)<40 and s.get("dip3")==1 and s.get("brk")==1),
        ]
        for name,fn in combos:
            ce(name,fn)

        # Train/Test 교차
        L.append(f"\n  [Train→Test 검증]")
        for fname,ffn in combos:
            trsub=[s for s in tr if ffn(s)]
            tesub=[s for s in te if ffn(s)]
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
                    L.append(f"      >>> BOTH EV>0 <<<")

    # [3] 결론
    L.append(f"\n{'='*65}")
    L.append("[3] 결론 — TEST EV>0")
    found=False
    for st,sigs in all_results.items():
        if len(sigs)<MIN_TRADES: continue
        si=int(len(sigs)*TRAIN_RATIO); te=sigs[si:]
        for ek in sorted(set(k for s in sigs for k in s["exits"])):
            m=calc_m([s["exits"].get(ek,0) for s in te])
            if m and m["ev"]>0 and m["n"]>=20:
                L.append(f"  {st} + {ek}: {fmt(m)}")
                found=True
    if not found:
        L.append("  >>> TEST EV>0 조합 없음 → 재설계 필요 <<<")

    return L


# ================================================================
# 메인
# ================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--coins", type=int, default=30)
    parser.add_argument("--skip-collect", action="store_true")
    args = parser.parse_args()
    t0 = time.time()

    if not args.skip_collect:
        collect(days=args.days, top_n=args.coins)

    coins = get_saved_coins()
    if not coins: tg(f"[오류] 데이터 없음"); return

    tg(f"\n[분석] {len(coins)}코인")

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
    for i, coin in enumerate(coins):
        try:
            cr = process_coin(coin, btc_regime)
            for st,sigs in cr.items():
                all_results[st].extend(sigs)
            del cr; gc.collect()
        except Exception as e:
            import traceback
            tg(f"[분석에러] {coin}: {e}\n{traceback.format_exc()[-300:]}")

    for st in all_results:
        all_results[st].sort(key=lambda s: s["time"])

    tg(f"\n[신호 완료]")
    for st,sigs in all_results.items():
        tg(f"  {st}: {len(sigs)}건")

    tg("[리포트 생성]")
    try:
        lines = generate_report(dict(all_results))
    except Exception as e:
        import traceback
        tg(f"[리포트에러] {e}\n{traceback.format_exc()[-500:]}")
        lines = [f"에러: {e}"]

    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now(KST).strftime("%Y%m%d_%H%M")
    fpath = os.path.join(OUT_DIR, f"signal_v3_{ts}.txt")
    with open(fpath,"w",encoding="utf-8") as f: f.write("\n".join(lines))

    tg(f"\n[완료] {(time.time()-t0)/60:.1f}분")
    tg(f"리포트: {fpath}")

    # 분할 전송
    cur=""
    chunks=[]
    for line in lines:
        if len(cur)+len(line)+1>3800: chunks.append(cur); cur=line
        else: cur=cur+"\n"+line if cur else line
    if cur: chunks.append(cur)
    for ci,ch in enumerate(chunks):
        tg(f"[{ci+1}/{len(chunks)}]\n{ch}")

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: tg("중단")
    except Exception as e:
        import traceback
        tg(f"[에러] {e}\n{traceback.format_exc()[-500:]}")
