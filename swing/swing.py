"""swing.py — 스윙 전략 연구 라이브러리 (Protocol v1.7 구현, v1 하드닝).
⚠ WIP — 이 파일은 3차 감사 반영 이전 상태.
   외부 audit 결과 blocker 5개(open/close map · full calendar · dynamic eligibility ·
   skew/kurt central-moment · snapshot pre-hash provenance) + entry-gap unit test 남음.
   hash-ready = NO. 상세는 swing/README.md.

섹션: [STATS] Sharpe/PSR/DSR/MDD/Calmar · [DATA] Upbit 일봉 · [ENGINE] 지표+엔진+7 trial · [UNIVERSE] §10 preflight.
공식 robustness 실행은 run_robustness.py (분리 유지 — 성과 수치 출력·hash 후 1회 전용)."""
import math, time, threading, datetime
import requests
from statistics import NormalDist

# ============================================================
# [STATS] Sharpe / PSR / DSR / MDD / Calmar — Protocol §4.2 코드사양
#   PSR/DSR = Bailey & López de Prado. 내부는 관측주기(일별) Sharpe(이론적 정확형);
#   연환산(×√365)은 표시·Calmar용. 결측일은 0으로 채우지 않음(호출 측 실제 return만 전달).
# ============================================================
_ND = NormalDist()
GAMMA = 0.5772156649015329          # Euler–Mascheroni
SQRT_YEAR = math.sqrt(365.0)

def _mean(xs): return sum(xs)/len(xs)
def _std(xs):                        # sample std (ddof=1)
    n=len(xs)
    if n<2: return 0.0
    m=_mean(xs); return math.sqrt(sum((x-m)**2 for x in xs)/(n-1))

def daily_sharpe(returns):
    """관측주기(일별) Sharpe, risk-free=0."""
    sd=_std(returns)
    return (_mean(returns)/sd) if sd>0 else 0.0

def ann_sharpe(returns):             # 표시용 연환산
    return daily_sharpe(returns)*SQRT_YEAR

def skew_kurt(returns):
    """Central-moment skewness γ3 = m3/m2^1.5, non-excess kurtosis γ4 = m4/m2².
    m_k = Σ(x-μ)^k / n (분모 n). 정규분포에서 skew=0, kurt=3.
    Bailey-LdP PSR denom = √(1 - γ3·SR + ((γ4-1)/4)·SR²)에 사용."""
    n=len(returns)
    if n<3: return 0.0, 3.0
    m=_mean(returns)
    d=[r-m for r in returns]
    m2=sum(x*x for x in d)/n
    if m2<=0: return 0.0, 3.0
    m3=sum(x*x*x for x in d)/n
    m4=sum(x*x*x*x for x in d)/n
    skew=m3/(m2**1.5)
    kurt=m4/(m2*m2)
    return skew, kurt

def psr(sr_hat, sr_star, n, skew, kurt):
    """P(참 Sharpe > sr_star). sr_hat/sr_star는 동일(관측주기) 단위. Bailey–LdP."""
    denom=math.sqrt(max(1e-12, 1.0 - skew*sr_hat + ((kurt-1.0)/4.0)*sr_hat**2))
    if n<2: return 0.0
    z=(sr_hat - sr_star)*math.sqrt(n-1)/denom
    return _ND.cdf(z)

def expected_max_sharpe(trial_sharpes):
    """N trial iid N(0,V) 하의 기대 최대 Sharpe = SR*_DSR (관측주기 단위)."""
    N=len(trial_sharpes)
    if N<2: return 0.0
    V=_std(trial_sharpes)**2
    sd=math.sqrt(V)
    if sd==0: return 0.0
    a=_ND.inv_cdf(1.0 - 1.0/N)
    b=_ND.inv_cdf(1.0 - 1.0/(N*math.e))
    return sd*((1.0-GAMMA)*a + GAMMA*b)

def dsr(sr_hat_selected, all_trial_daily_sharpes, n, skew, kurt):
    """Deflated SR = PSR(SR*_DSR). all_trial_daily_sharpes = raw N trial(=7) 전부의 일별 Sharpe."""
    sr_star=expected_max_sharpe(all_trial_daily_sharpes)
    return psr(sr_hat_selected, sr_star, n, skew, kurt), sr_star

def max_drawdown(equity):
    peak=equity[0]; mdd=0.0
    for e in equity:
        if e>peak: peak=e
        if peak>0:
            dd=e/peak-1.0
            if dd<mdd: mdd=dd
    return mdd                        # ≤0

def calmar_from_equity(equity, n_days):
    if len(equity)<2 or equity[0]<=0 or n_days<=0: return 0.0, 0.0, 0.0
    years=n_days/365.0
    growth=equity[-1]/equity[0]
    cagr=growth**(1.0/years)-1.0 if (years>0 and growth>0) else -1.0
    mdd=abs(max_drawdown(equity))
    calmar=(cagr/mdd) if mdd>0 else float('inf')
    return calmar, cagr, mdd

# α-spending (Protocol §9): prospective 12/24/36 look, Bonferroni /3
PROSPECTIVE_PSR_THRESHOLD = 1.0 - 0.05/3.0   # ≈ 0.98333
ROBUSTNESS_DSR_THRESHOLD  = 0.95

# ============================================================
# [DATA] Upbit 일봉 (keep-alive + pagination). Protocol §8 구간 로딩.
# ============================================================
BASE="https://api.upbit.com/v1"
_tl=threading.local()
def _sess():
    s=getattr(_tl,"s",None)
    if s is None:
        s=requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0 (research)","Accept":"application/json"})
        s.mount("https://",requests.adapters.HTTPAdapter(pool_connections=4,pool_maxsize=4,max_retries=0))
        _tl.s=s
    return s
def _get(path,params=None,tries=7):
    last=None
    for i in range(tries):
        try:
            r=_sess().get(BASE+path,params=params,timeout=20)
            if r.status_code==200: return r.json()
            if r.status_code==429: time.sleep(0.8+i*0.7); last="429"; continue
            if 400<=r.status_code<500: raise RuntimeError(f"HTTP {r.status_code} {r.text[:80]}")
            last=f"HTTP {r.status_code}"; time.sleep(0.4+i*0.4)
        except requests.RequestException as e:
            last=e; time.sleep(0.4+i*0.5)
    raise RuntimeError(f"GET fail {path}: {last}")
def markets_krw():
    return [m["market"] for m in _get("/market/all",{"isDetails":"false"}) if m["market"].startswith("KRW-")]
def candles_day(market,count=200,to=None):
    p={"market":market,"count":count}
    if to: p["to"]=to
    return _get("/candles/days",p)
def load_daily(market, count_max=4000):
    """전체 일봉(오래된→최신). 각 캔들 dict: date(YYYY-MM-DD), o,h,l,c, value(거래대금KRW)."""
    out=[]; to=None
    while len(out)<count_max:
        cs=candles_day(market,200,to)
        if not cs: break
        out+=cs
        if len(cs)<200: break
        to=cs[-1]["candle_date_time_utc"]; time.sleep(0.02)
    rows=[{"date":c["candle_date_time_utc"][:10],
           "o":c["opening_price"],"h":c["high_price"],"l":c["low_price"],"c":c["trade_price"],
           "value":c.get("candle_acc_trade_price",0.0)} for c in reversed(out)]
    return rows

# ============================================================
# [ENGINE] 지표 + 7 trial 신호 + 포트폴리오 엔진 — §5/§6/§11
#   공통(§6.2): 신호=완성봉 t, fill=t+1 open, stop=fill−mult×ATR_t, size=equity×1%/(fill−stop),
#   per-coin notional ≤20%, gross ≤100%, N=5 동시, stop-first, cost 왕복=half each side, lexical tie-break.
# ============================================================
RISK=0.01; NMAX=5; PERCOIN_CAP=0.20; GROSS_CAP=1.00

# ---------- indicators (per-coin local arrays) ----------
def atr(h,l,c,period):
    """Wilder ATR (§6.1 확정): seed=첫 period개 TR 평균, 이후 ATR_t=((N-1)ATR_{t-1}+TR_t)/N."""
    n=len(c); tr=[0.0]*n
    for i in range(n):
        if i==0: tr[i]=h[i]-l[i]
        else: tr[i]=max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    out=[None]*n
    if n>=period:
        out[period-1]=sum(tr[:period])/period                # Wilder seed
        for i in range(period,n):
            out[i]=((period-1)*out[i-1]+tr[i])/period         # Wilder smoothing
    return out
def sma(c,period):
    n=len(c); out=[None]*n; s=0.0
    for i in range(n):
        s+=c[i]
        if i>=period: s-=c[i-period]
        if i>=period-1: out[i]=s/period
    return out
def rsi_wilder(c,period):
    n=len(c); out=[None]*n
    if n<period+1: return out
    ag=al=0.0
    for i in range(1,period+1):
        d=c[i]-c[i-1]; ag+=max(d,0); al+=max(-d,0)
    ag/=period; al/=period
    out[period]=100-100/(1+(ag/al)) if al>0 else 100.0
    for i in range(period+1,n):
        d=c[i]-c[i-1]
        ag=(ag*(period-1)+max(d,0))/period
        al=(al*(period-1)+max(-d,0))/period
        out[i]=100-100/(1+(ag/al)) if al>0 else 100.0
    return out
def roll_max(x,period):                      # prior `period` bars EXCLUDING current i (i-period..i-1)
    n=len(x); out=[None]*n
    for i in range(n):
        if i>=period: out[i]=max(x[i-period:i])
    return out
def roll_min(x,period):
    n=len(x); out=[None]*n
    for i in range(n):
        if i>=period: out[i]=min(x[i-period:i])
    return out

# ---------- per-coin prep ----------
def prep_coin(rows):
    o=[r["o"] for r in rows]; h=[r["h"] for r in rows]; l=[r["l"] for r in rows]
    c=[r["c"] for r in rows]; v=[r["value"] for r in rows]; d=[r["date"] for r in rows]
    return {"o":o,"h":h,"l":l,"c":c,"v":v,"d":d,"n":len(rows),
            "atr14":atr(h,l,c,14),"atr20":atr(h,l,c,20),
            "sma5":sma(c,5),"sma200":sma(c,200),"rsi2":rsi_wilder(c,2),
            "dhi20":roll_max(h,20),"dlo10":roll_min(l,10),
            "dhi55":roll_max(h,55),"dlo20":roll_min(l,20)}

# ---------- generic portfolio simulator ----------
class Book:
    """정규화 equity(초기 1.0), cash 회계. cost = 왕복%(half each side)."""
    def __init__(self,cost_roundtrip):
        self.cash=1.0; self.pos={}; self.cost=cost_roundtrip/100.0/2.0  # 편도 비율
        self.realized={}   # 종목별 실현 PnL(정규화 equity 단위)
    def equity(self,price_of):
        e=self.cash
        for m,p in self.pos.items():
            px=price_of(m)
            if px is not None: e+=p["qty"]*px
        return e
    def gross(self,price_of):
        g=0.0
        for m,p in self.pos.items():
            px=price_of(m)
            if px is not None: g+=p["qty"]*px
        return g
    def open(self,m,fill,stop,eq_now,price_of):
        """Fixed audit item 1: 호출측에서 open-based eq_now/price_of 전달 (§6.2).
        room_gross도 open 기반 = t+1 open 체결 시점에 알 수 있는 가격만 사용, look-ahead 없음."""
        if fill<=stop or fill<=0: return False
        risk_notional=eq_now*RISK/((fill-stop)/fill)      # = eq*1%/stop_dist_frac
        cap_notional=eq_now*PERCOIN_CAP
        room_gross=eq_now*GROSS_CAP - self.gross(price_of)
        room_cash=self.cash/(1.0+self.cost)               # fee 포함 cash≥0 유지 = 무레버리지(§11)
        notional=max(0.0,min(risk_notional,cap_notional,room_gross,room_cash))
        if notional<=1e-9: return False
        qty=notional/fill
        self.cash-=notional; self.cash-=notional*self.cost
        self.pos[m]={"qty":qty,"entry":fill,"stop":stop,"basis":notional*(1+self.cost)}
        return True
    def close(self,m,fill):
        p=self.pos.pop(m); proceeds=p["qty"]*fill; net=proceeds*(1-self.cost)
        self.cash+=proceeds; self.cash-=proceeds*self.cost
        self.realized[m]=self.realized.get(m,0.0)+(net-p["basis"])

class InvalidRun(Exception):
    """§10 execution-critical gap → run/dataset INVALID (사용자 확정).
    ① 보유 종목의 당일 봉 결측 (MTM 불가)
    ② pending entry/exit의 t+1 open 결측 (fill 불가, silent skip 금지)
    carry-forward/0충전 안 함 = §4.2 '결측일 0수익 미충전' 준수."""
    pass

def simulate(coins, dates, cfg):
    """cfg: dict with 'signals' + 'cost'. return (equity_series, daily_returns, trades, book).

    가격 map 분리 (§6.2, audit item 1 fixed, look-ahead 제거):
      - open_price_of(m,d)  : 오늘 open — entry 사이징·room_gross 계산 시점의 known 가격
      - close_price_of(m,d) : 오늘 close — 일말 MTM equity 기록용

    execution-critical gap → InvalidRun (§10, audit item ② strict):
      ① held-coin 당일 봉 결측 → raise (MTM 불가)
      ② pending entry의 target open 결측 → raise (fill 불가)
      ③ pending exit의 target open은 held-coin이라 ①이 먼저 잡음

    Timeline: 호출측에서 full_calendar(START, END) 전달 권장 (union 아님, audit item 2 fixed)."""
    book=Book(cfg["cost"])
    eq_series=[]; trades=[]
    pending=[]
    def li(m,d): return coins[m]["idx"].get(d)
    for gi,d in enumerate(dates):
        # ① held-coin 당일 봉 결측 검사
        for m in book.pos:
            if coins[m]["idx"].get(d) is None:
                raise InvalidRun(f"held-coin MTM gap: {m} @ {d}")
        def open_px(m,_d=d):
            i=coins[m]["idx"].get(_d)
            return coins[m]["o"][i] if i is not None else None
        def close_px(m,_d=d):
            i=coins[m]["idx"].get(_d)
            return coins[m]["c"][i] if i is not None else None
        # A) execute pending open-fill actions at today's OPEN
        exits=[a for a in pending if a[0]=="exit"]; ents=[a for a in pending if a[0]=="entry"]
        for _,m,_x in exits:
            i=li(m,d)
            if m in book.pos:
                if i is None:
                    raise InvalidRun(f"pending-exit open gap: {m} @ {d}")
                book.close(m, coins[m]["o"][i]); trades.append((d,m,"exit_open"))
        # ② pending entry의 target open 검사 (silent skip 금지)
        for _,m,_ex in ents:
            if coins[m]["idx"].get(d) is None:
                raise InvalidRun(f"pending-entry open gap: {m} @ {d}")
        eq_now=book.equity(open_px)                             # 사이징 시 open 기반 (look-ahead 없음)
        for _,m,ex in sorted(ents,key=lambda a:a[1]):
            if len(book.pos)>=NMAX: break
            if m in book.pos: continue
            i=li(m,d)
            fill=coins[m]["o"][i]; atrv=ex["atr"]; mult=ex["mult"]
            stop=fill-mult*atrv
            if book.open(m,fill,stop,eq_now,open_px):           # room_gross도 open
                book.pos[m]["entry_gi"]=gi
                trades.append((d,m,"entry"))
        pending=[]
        # B) intraday stop (stop-first)
        for m in list(book.pos):
            i=li(m,d)
            if i is None: continue
            lo=coins[m]["l"][i]; op=coins[m]["o"][i]; st=book.pos[m]["stop"]
            if lo<=st:
                fill=min(st,op)
                book.close(m,fill); trades.append((d,m,"stop"))
        # C) compute signals on COMPLETED bar today
        acts=cfg["signals"](coins,dates,gi,book)
        pending=acts
        # D) MTM at close (close_px 사용, audit item 1 fixed)
        eq_series.append(book.equity(close_px))
    # 종말 미실현 → concentration 근사
    last=dates[-1] if dates else None
    for m,p in list(book.pos.items()):
        i=coins[m]["idx"].get(last)
        if i is not None:
            net=p["qty"]*coins[m]["c"][i]*(1-book.cost)
            book.realized[m]=book.realized.get(m,0.0)+(net-p["basis"])
    rets=[]
    for i in range(1,len(eq_series)):
        if eq_series[i-1]>0: rets.append(eq_series[i]/eq_series[i-1]-1.0)
    return eq_series, rets, trades, book

# ---------- signal generators (return list of ('entry'/'exit', market, extra) for NEXT open) ----------
def _is_eligible(cd, d):
    """dynamic point-in-time eligibility (audit item 3a): d >= coin.eligible_from AND candle(d) exists."""
    ef=cd.get("eligible_from")
    if ef is not None and d < ef: return False
    return cd["idx"].get(d) is not None

def make_percoin_signals(entry_field_hi, exit_field_lo, atr_field, mult):
    def sig(coins,dates,gi,book):
        d=dates[gi]; acts=[]
        for m,cd in coins.items():
            if not _is_eligible(cd, d): continue
            i=cd["idx"][d]
            if m in book.pos:
                lo=cd[exit_field_lo][i]
                if lo is not None and cd["c"][i]<lo:
                    acts.append(("exit",m,{})); continue
            else:
                hi=cd[entry_field_hi][i]; a=cd[atr_field][i]
                if hi is not None and a is not None and a>0 and cd["c"][i]>hi:
                    acts.append(("entry",m,{"atr":a,"mult":mult}))
        return acts
    return sig

def make_connors_signals(rsi_thresh, mult, time_cap=10):
    def sig(coins,dates,gi,book):
        d=dates[gi]; acts=[]
        for m,cd in coins.items():
            if not _is_eligible(cd, d): continue
            i=cd["idx"][d]
            if m in book.pos:
                s5=cd["sma5"][i]
                held=gi-book.pos[m].get("entry_gi",gi)
                if s5 is not None and cd["c"][i]>s5: acts.append(("exit",m,{}))
                elif held>=time_cap: acts.append(("exit",m,{}))
            else:
                s200=cd["sma200"][i]; r=cd["rsi2"][i]; a=cd["atr14"][i]
                if (s200 is not None and r is not None and a is not None and a>0
                        and cd["c"][i]>s200 and r<rsi_thresh):
                    acts.append(("entry",m,{"atr":a,"mult":mult}))
        return acts
    return sig

def make_xs_signals(formation_days, mult, top_n=NMAX):
    """월간 리밸런싱 + dynamic eligibility. E<top_n이면 min(E,N)."""
    def sig(coins,dates,gi,book):
        d=dates[gi]; acts=[]
        is_month_end = (gi+1>=len(dates)) or (dates[gi+1][:7]!=d[:7])
        if not is_month_end: return acts
        ranked=[]
        for m,cd in coins.items():
            if not _is_eligible(cd, d): continue
            i=cd["idx"][d]
            if i<formation_days: continue
            base=cd["c"][i-formation_days]
            if base<=0: continue
            ranked.append((cd["c"][i]/base-1.0, m, cd["atr14"][i]))
        ranked.sort(reverse=True)
        target=set(m for _,m,_ in ranked[:top_n])
        for m in list(book.pos):
            if m not in target: acts.append(("exit",m,{}))
        for r,m,a in ranked[:top_n]:
            if m not in book.pos and a is not None and a>0:
                acts.append(("entry",m,{"atr":a,"mult":mult}))
        return acts
    return sig

TRIALS = {
  "A1": {"kind":"percoin","signals":make_percoin_signals("dhi20","dlo10","atr20",2)},
  "A2": {"kind":"percoin","signals":make_percoin_signals("dhi55","dlo20","atr20",2)},
  "B5": {"kind":"percoin","signals":make_connors_signals(5,2)},
  "B10":{"kind":"percoin","signals":make_connors_signals(10,2)},
  "C3": {"kind":"xs","signals":make_xs_signals(90,2)},
  "C6": {"kind":"xs","signals":make_xs_signals(180,2)},
  "C12":{"kind":"xs","signals":make_xs_signals(365,2)},
}

# ============================================================
# [UNIVERSE] §5 eligibility + §10 데이터품질 preflight (result-independent)
#   결과(수익) 무관 판정만. 정책 임계(MIN_UNIVERSE/MAX_MISSING 비교·INVALID 선언)는 run_robustness.
#   ⚠ audit item 3: 현재 static filter. protocol §5는 dynamic point-in-time + 30d turnover + halt 필요.
# ============================================================
def _span_days(a,b):
    """구간 달력일 수(양끝 포함). 일봉은 24/7 = 매 달력일 1캔들 기대."""
    return (datetime.date.fromisoformat(b)-datetime.date.fromisoformat(a)).days+1

def full_calendar(start, end):
    """START~END 전체 달력일 리스트 (audit item 2 fixed: union 아님).
    coin dates union으로 하면 전체 결측일 방문 누락 → stealth carry-forward. 이 함수 사용 강제."""
    d=datetime.date.fromisoformat(start); e=datetime.date.fromisoformat(end); out=[]
    while d<=e: out.append(d.isoformat()); d+=datetime.timedelta(days=1)
    return out

def clean_rows(rows):
    """§10(c) 구조적 corruption 봉 드롭: 가격>0 · h≥max(o,c,l) · l≤min(o,c,h) · value≥0 · 전부 finite.
    손상봉 = 사용불가 → 제거(이후 결측일로 집계). 드롭 수 반환(로깅용)."""
    out=[]; bad=0
    for r in rows:
        o,h,l,c=r["o"],r["h"],r["l"],r["c"]; v=r.get("value",0.0)
        ok=(all(isinstance(x,(int,float)) and math.isfinite(x) for x in (o,h,l,c,v))
            and o>0 and h>0 and l>0 and c>0 and v>=0
            and h>=max(o,c,l) and l<=min(o,c,h))
        if ok: out.append(r)
        else: bad+=1
    return out, bad

def build_universe(data, start, end, min_listing=180, min_coverage=30):
    """Dynamic point-in-time eligible universe (§5 · audit item 3a fixed):
      각 코인의 eligible_from = first_valid_candle_date + min_listing calendar days.
      실제 사용 = 각 signal 생성 시 d >= coin.eligible_from AND candle(d) exists.
      static "전체 기간 in/out" 아님.

    §5 30d turnover: audit item 3b 확정 = universe cutoff 아님, participation feasibility report only.
    §10(c) corruption 봉 드롭 후 결측 집계. dataset missing_rate = eligible 창 [max(eligible_from, start), end] 기준.

    반환 (coins, report). 각 coin dict에 eligible_from 필드 추가.
    INVALID 판정은 호출 측(run_robustness) 정책."""
    coins={}; report={"total":len(data),"eligible":0,"corrupt_bars":0,"missing_rate":0.0,"per_coin_missing":{}}
    tot_missing=0; tot_expected=0
    end_d=datetime.date.fromisoformat(end)
    for m in sorted(data):
        rows=data[m]
        if not rows: continue
        rows,bad=clean_rows(rows); report["corrupt_bars"]+=bad
        if not rows: continue
        first_valid=rows[0]["date"]
        eligible_from_d=datetime.date.fromisoformat(first_valid)+datetime.timedelta(days=min_listing)
        eligible_from=eligible_from_d.isoformat()
        if eligible_from_d > end_d:                          # 아예 구간 내 eligible 창 없음
            continue
        window_start=max(eligible_from, start)
        indate=sorted({r["date"] for r in rows if window_start<=r["date"]<=end})
        if len(indate)<min_coverage: continue
        span_win=(end_d-datetime.date.fromisoformat(window_start)).days+1
        missing=max(0, span_win-len(indate))
        tot_missing+=missing; tot_expected+=span_win
        report["per_coin_missing"][m]=missing/span_win
        cd=prep_coin(rows); cd["idx"]={r["date"]:i for i,r in enumerate(rows)}
        cd["eligible_from"]=eligible_from                    # dynamic point-in-time anchor
        coins[m]=cd
    report["eligible"]=len(coins)
    report["missing_rate"]=(tot_missing/tot_expected) if tot_expected>0 else 0.0
    return coins, report

def concentration(realized):
    """§12(B) abs 기반: share_i=|pnl_i|/Σ|pnl_j|, HHI=Σshare². 손실 종목도 집중도에 잡힘.
    top1_pos = top1 positive PnL / total positive PnL 은 진단값(별도 반환)."""
    absv={m:abs(v) for m,v in realized.items() if v!=0}
    tot=sum(absv.values())
    if tot<=0: return 1.0, 1.0, None, 1.0
    shares=sorted((v/tot for v in absv.values()),reverse=True)
    top1=shares[0]; hhi=sum(s*s for s in shares)
    top_coin=max(absv,key=absv.get)
    pos={m:v for m,v in realized.items() if v>0}; tp=sum(pos.values())
    top1_pos=(max(pos.values())/tp) if tp>0 else 0.0        # 진단값
    return top1, hhi, top_coin, top1_pos

def loo_target_coin(realized):
    """§4.4 LOO 대상 = '최대 PnL 기여 종목' = 최대 양(+) PnL 기여 종목(star winner). 없으면 None.
    ⚠ concentration()의 abs-max(top_coin, 손실 지배 포함)와 구별 — LOO는 winner 제거로 luck 의존성 검사."""
    pos={m:v for m,v in realized.items() if v>0}
    return max(pos,key=pos.get) if pos else None
