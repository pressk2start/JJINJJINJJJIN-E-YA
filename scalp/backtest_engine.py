"""backtest_engine.py — 1분봉 스켈핑 **체결·비용·갭 모델** (전략 아님).

⚠ 이 파일에는 진입 임계치가 없다. 그건 research/ 단계 산출물이며, 아직 나오지 않았다.
   초안에 있던 사전등록 7 trial 격자(z≥2.0, stop 1.5×ATR 등)는 **근거 없는 감**이었으므로 제거했다.
   "사전등록"이라는 형식은 감으로 고른 숫자를 정당화해주지 않는다.
   임계치는 research/sweep.py·seconds.py 가 데이터에서 뽑아낸 뒤 여기에 주입한다.

남겨둔 것 = 실측으로 검증된 실행 모델 (2026-08-23 Upbit API 직접 측정 근거):
  S1. 수수료(Book)와 슬리피지(체결가) 분리. Upbit KRW 현물 0.05%×2 = 0.10% 왕복
      (config.py:53 FEE_RATE_ROUNDTRIP=0.001 과 일치) + 편도 슬리피지 별도 부과.
  S2. 봉 안에서 stop/TP 동시터치 → stop-first (보수적 확정).
      ⚠ research/sweep.py 실측: 1분봉에서 이 가정의 보수/낙관 간격이 0.32%p =
        찾는 엣지(0.1%대)보다 크다. 따라서 **1분봉 백테스트로 브래킷 성과를 결론내면 안 된다.**
        초봉 해상도로 순서를 확정한 뒤에만 이 엔진의 브래킷 수치가 의미를 가진다.
  S3. 신호=완성봉 t, 체결=t+1 open. 사이징도 open 기준 = 룩어헤드 없음.
  S4. 1분봉 결측 = 무체결 분 (데이터 장애 아님). 실측 coverage: BTC/XRP 0.997,
      24h 거래대금 2위 TRUMP 0.52. → 결측분은 마지막 체결가로 MTM,
      체결 불가 분에 주문을 채우지 않고 대기(진입은 stale 취소, 청산은 무기한).
  S5. 참여 현실성: 주문 notional / 분당 거래대금 비율을 리포트 (스켈핑에선 생존조건).
  S6. PSR/DSR 입력은 **일별** 수익률. 분봉 Sharpe 연환산 금지(자기상관 과대추정).

섹션: [STATS] Sharpe/PSR/DSR/MDD/Calmar · [DATA] Upbit 1분봉 · [ENGINE] 지표+체결엔진 · [UNIVERSE] preflight
"""
import math, time, threading, datetime, collections
import requests
from statistics import NormalDist

# ============================================================
# [STATS] Sharpe / PSR / DSR / MDD / Calmar
#   PSR/DSR = Bailey & López de Prado. 내부 단위 = '일별' Sharpe (S6: 분봉 단위 금지).
#   연환산(×√365)은 표시·Calmar용. 결측일은 0으로 채우지 않음.
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
    """Central-moment skewness γ3 = m3/m2^1.5, non-excess kurtosis γ4 = m4/m2² (분모 n).
    Bailey-LdP PSR denom = √(1 - γ3·SR + ((γ4-1)/4)·SR²)에 사용."""
    n=len(returns)
    if n<3: return 0.0, 3.0
    m=_mean(returns)
    d=[r-m for r in returns]
    m2=sum(x*x for x in d)/n
    if m2<=0: return 0.0, 3.0
    m3=sum(x*x*x for x in d)/n
    m4=sum(x*x*x*x for x in d)/n
    return m3/(m2**1.5), m4/(m2*m2)

def psr(sr_hat, sr_star, n, skew, kurt):
    """P(참 Sharpe > sr_star). sr_hat/sr_star는 동일(일별) 단위. Bailey–LdP."""
    denom=math.sqrt(max(1e-12, 1.0 - skew*sr_hat + ((kurt-1.0)/4.0)*sr_hat**2))
    if n<2: return 0.0
    z=(sr_hat - sr_star)*math.sqrt(n-1)/denom
    return _ND.cdf(z)

def expected_max_sharpe(trial_sharpes):
    """N trial iid N(0,V) 하의 기대 최대 Sharpe = SR*_DSR (일별 단위)."""
    N=len(trial_sharpes)
    if N<2: return 0.0
    sd=_std(trial_sharpes)
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

# α-spending: prospective 12/24/36 look, Bonferroni /3 (swing과 동일 규율)
PROSPECTIVE_PSR_THRESHOLD = 1.0 - 0.05/3.0   # ≈ 0.98333
ROBUSTNESS_DSR_THRESHOLD  = 0.95

# ============================================================
# [DATA] Upbit 1분봉 (keep-alive + pagination).
#   시각 키 = candle_date_time_utc[:16] = "YYYY-MM-DDTHH:MM" (UTC 고정, DST/tz 모호성 제거).
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

def ticker_value_24h(markets):
    """24h 누적 거래대금(KRW) — universe 선정용 pre-hash 진단. 결과(수익) 무관."""
    out={}
    for i in range(0,len(markets),100):
        chunk=markets[i:i+100]
        for t in _get("/ticker",{"markets":",".join(chunk)}):
            out[t["market"]]=t.get("acc_trade_price_24h",0.0)
        time.sleep(0.05)
    return out

def candles_min1(market,count=200,to=None):
    p={"market":market,"count":count}
    if to: p["to"]=to
    return _get("/candles/minutes/1",p)

def load_min1(market, start, end, max_bars=200000):
    """[start, end] 구간 1분봉(오래된→최신). start/end = "YYYY-MM-DDTHH:MM".
    각 봉 dict: t, o,h,l,c, value(거래대금KRW). Upbit는 체결 없는 분을 건너뜀 = 결측 정상."""
    out=[]; to=(end+":00")
    seen=set()
    while len(out)<max_bars:
        cs=candles_min1(market,200,to)
        if not cs: break
        stop=False
        for c in cs:
            t=c["candle_date_time_utc"][:16]
            if t<start: stop=True; continue
            if t>end or t in seen: continue
            seen.add(t)
            out.append({"t":t,
                        "o":c["opening_price"],"h":c["high_price"],
                        "l":c["low_price"],"c":c["trade_price"],
                        "value":c.get("candle_acc_trade_price",0.0)})
        if stop or len(cs)<200: break
        to=cs[-1]["candle_date_time_utc"]; time.sleep(0.08)
    out.sort(key=lambda r:r["t"])
    return out

# ============================================================
# [ENGINE] 지표 + 모멘텀 브레이크아웃 7 trial + 분봉 포트폴리오 엔진
#   공통 규약: 신호=완성봉 t → 체결=t+1 open, 사이징=open 기준 equity(look-ahead 없음),
#   stop=fill−stop_mult×ATR_t, TP=fill+tp_mult×ATR_t, time-cap=hold_bars, stop-first,
#   per-coin notional ≤20%, gross ≤100%, N=3 동시, lexical tie-break, 무레버리지(cash≥0).
# ============================================================
RISK=0.005; NMAX=3; PERCOIN_CAP=0.20; GROSS_CAP=1.00
FEE_ROUNDTRIP_PCT=0.10        # Upbit KRW 현물 0.05% × 2 (S1)
SLIP_BPS=5.0                  # 편도 슬리피지 기본값(bp). robustness에서 스트레스 스윕.
MAX_WAIT_BARS=5               # pending 진입이 체결가능 봉을 기다리는 최대 분 (초과=신호 stale 취소)
MAX_HALT_BARS=60              # 보유 중 연속 무체결 분이 이 값 초과 = 거래정지로 간주 → 강제청산 예약
MIN_ORDER_KRW=5000            # Upbit KRW 마켓 최소 주문금액 (feasibility 진단용, 봉인 전 재확인 항목)
CHANNELS=(10,20,60)           # 사전등록된 브레이크아웃 채널 창 (이 밖의 값 사용 금지)
VOL_WIN=60                    # 거래대금 z-score 창
TREND_WIN=60                  # 추세 필터 SMA 창

# ---------- indicators (per-coin local arrays, 전부 O(n)) ----------
def atr(h,l,c,period):
    """Wilder ATR: seed=첫 period개 TR 평균, 이후 ATR_t=((N-1)ATR_{t-1}+TR_t)/N."""
    n=len(c); tr=[0.0]*n
    for i in range(n):
        if i==0: tr[i]=h[i]-l[i]
        else: tr[i]=max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    out=[None]*n
    if n>=period:
        out[period-1]=sum(tr[:period])/period
        for i in range(period,n):
            out[i]=((period-1)*out[i-1]+tr[i])/period
    return out

def sma(c,period):
    n=len(c); out=[None]*n; s=0.0
    for i in range(n):
        s+=c[i]
        if i>=period: s-=c[i-period]
        if i>=period-1: out[i]=s/period
    return out

def roll_max(x,period):
    """직전 period봉의 최대 (현재 i 제외 = i-period..i-1). monotonic deque로 O(n)."""
    n=len(x); out=[None]*n; dq=collections.deque()   # (idx) 내림차순 값 유지
    for i in range(n):
        while dq and dq[0] < i-period: dq.popleft()
        out[i]=x[dq[0]] if i>=period else None
        while dq and x[dq[-1]]<=x[i]: dq.pop()
        dq.append(i)
    return out

def roll_min(x,period):
    """직전 period봉의 최소 (현재 i 제외). monotonic deque로 O(n)."""
    n=len(x); out=[None]*n; dq=collections.deque()
    for i in range(n):
        while dq and dq[0] < i-period: dq.popleft()
        out[i]=x[dq[0]] if i>=period else None
        while dq and x[dq[-1]]>=x[i]: dq.pop()
        dq.append(i)
    return out

def roll_z(x,period):
    """직전 period봉 기준 z-score = (x_i − mean_prior)/sd_prior. 현재 봉 제외(look-ahead 차단).
    sd=0(전부 동일값)이면 None (판정 불가 → 신호 없음). ddof=1."""
    n=len(x); out=[None]*n; s=0.0; s2=0.0
    for i in range(n):
        if i>=period:
            m=s/period
            var=(s2-period*m*m)/(period-1)
            sd=math.sqrt(var) if var>0 else 0.0
            out[i]=((x[i]-m)/sd) if sd>0 else None
        s+=x[i]; s2+=x[i]*x[i]
        if i>=period:
            s-=x[i-period]; s2-=x[i-period]*x[i-period]
    return out

# ---------- per-coin prep ----------
def prep_coin(rows):
    """1분봉 rows → 지표 배열. 사전등록 창(CHANNELS/VOL_WIN/TREND_WIN)만 계산."""
    o=[r["o"] for r in rows]; h=[r["h"] for r in rows]; l=[r["l"] for r in rows]
    c=[r["c"] for r in rows]; v=[r["value"] for r in rows]; t=[r["t"] for r in rows]
    cd={"o":o,"h":h,"l":l,"c":c,"v":v,"t":t,"n":len(rows),
        "atr14":atr(h,l,c,14),
        f"sma{TREND_WIN}":sma(c,TREND_WIN),
        f"vz{VOL_WIN}":roll_z(v,VOL_WIN)}
    for k in CHANNELS:
        cd[f"hi{k}"]=roll_max(h,k)
        cd[f"lo{k}"]=roll_min(l,k)
    return cd

# ---------- portfolio book ----------
class Book:
    """정규화 equity(초기 1.0), cash 회계. fee = 왕복%(half each side). 슬리피지는 체결가에 반영(엔진)."""
    def __init__(self, fee_roundtrip_pct):
        self.cash=1.0; self.pos={}; self.fee=fee_roundtrip_pct/100.0/2.0
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
    def open(self,m,fill,stop,eq_now,price_of,meta=None):
        """fill = 슬리피지 반영된 매수 체결가. eq_now/price_of는 open 기준(look-ahead 없음)."""
        if fill<=stop or fill<=0: return False
        risk_notional=eq_now*RISK/((fill-stop)/fill)      # = eq*RISK/stop_dist_frac
        cap_notional=eq_now*PERCOIN_CAP
        room_gross=eq_now*GROSS_CAP - self.gross(price_of)
        room_cash=self.cash/(1.0+self.fee)                # fee 포함 cash≥0 유지 = 무레버리지
        notional=max(0.0,min(risk_notional,cap_notional,room_gross,room_cash))
        if notional<=1e-12: return False
        qty=notional/fill
        self.cash-=notional; self.cash-=notional*self.fee
        p={"qty":qty,"entry":fill,"stop":stop,"basis":notional*(1+self.fee),
           "risk":qty*(fill-stop),"peak":fill}
        if meta: p.update(meta)
        self.pos[m]=p
        return True
    def close(self,m,fill):
        """fill = 슬리피지 반영된 매도 체결가. 반환 (pnl_equity, R_multiple)."""
        p=self.pos.pop(m); proceeds=p["qty"]*fill; net=proceeds*(1-self.fee)
        self.cash+=proceeds; self.cash-=proceeds*self.fee
        pnl=net-p["basis"]
        self.realized[m]=self.realized.get(m,0.0)+pnl
        r=(pnl/p["risk"]) if p.get("risk",0)>0 else 0.0
        return pnl, r

class InvalidRun(Exception):
    """dataset integrity 위반 → run INVALID (build_universe에서 사전 검출).

    ⚠ 1분봉에서 '결측분'은 integrity 위반이 아님 (S4: 무체결 = 정상 정보).
       따라서 swing 일봉과 달리 시뮬레이션 중 결측으로는 raise 하지 않는다.
    raise 대상:
      · 동일 timestamp 중복 봉
      · 시간 역행(비정렬) 봉
      · 분 격자에 정렬되지 않은 timestamp
    """
    pass

# ---------- simulator ----------
def simulate(coins, timeline, cfg):
    """1분봉 스켈핑 시뮬레이터.

    cfg: {'signals': fn, 'fee': 왕복 수수료%, 'slip_bps': 편도 슬리피지 bp,
          'max_wait_bars': int, 'max_halt_bars': int}
    return (eq_series, bar_returns, trades, book, exec_report)

    바 처리 순서 (봉 t):
      A) pending 청산 → pending 진입을 이 봉 OPEN에 체결 (슬리피지 반영)
         · 해당 봉이 결측이면 체결 불가 → 대기(age+1). 진입은 max_wait 초과 시 취소, 청산은 무기한 대기.
      B) intrabar 관리: stop-first → TP → trailing 갱신 (S2)
         · 결측 봉은 판정 자체를 건너뜀 (체결이 없으면 트리거 불가) + 연속 결측 카운트
         · 연속 결측 > max_halt → halt_exit 예약
      C) 완성봉 t 기준 time-cap 판정 + 신호 생성 → t+1 open pending
      D) 종가 MTM (결측 봉은 마지막 체결가로 마크 = S4)

    B가 A 뒤에 오는 것은 의도 = 이 봉 open에 진입한 포지션도 같은 봉의 저가에 stop 맞을 수 있음(보수적).
    """
    book=Book(cfg["fee"]); slip=cfg.get("slip_bps",SLIP_BPS)/10000.0
    max_wait=cfg.get("max_wait_bars",MAX_WAIT_BARS)
    max_halt=cfg.get("max_halt_bars",MAX_HALT_BARS)
    eq_series=[]; trades=[]
    pending={}            # market -> {"kind":"entry"/"exit", "age":int, "why":str, "extra":dict}
    rep={"gap_bars_held":0,"waited_fills":0,"cancelled_entries":0,"halt_exits":0,
         "unclosed_at_end":0,"entry_blocked_nmax":0}
    for gi,t in enumerate(timeline):
        def px_open(m,_t=t):
            i=coins[m]["idx"].get(_t)
            if i is not None: return coins[m]["o"][i]
            p=book.pos.get(m); return p["last_px"] if p else None
        def px_close(m,_t=t):
            i=coins[m]["idx"].get(_t)
            if i is not None: return coins[m]["c"][i]
            p=book.pos.get(m); return p["last_px"] if p else None

        # A-1) pending 청산 @ OPEN
        for m in sorted(pending):
            a=pending[m]
            if a["kind"]!="exit": continue
            i=coins[m]["idx"].get(t)
            if i is None:                       # 무체결 분 = 체결 불가 → 대기 (청산은 취소 없음)
                a["age"]+=1; continue
            if m not in book.pos: del pending[m]; continue
            if a["age"]>0: rep["waited_fills"]+=1
            fill=coins[m]["o"][i]*(1.0-slip)
            pnl,r=book.close(m,fill)
            trades.append({"t":t,"m":m,"kind":a["why"],"px":fill,"pnl":pnl,"r":r,
                           "waited":a["age"]})
            del pending[m]
        # A-2) pending 진입 @ OPEN (lexical tie-break)
        eq_now=book.equity(px_open)             # 사이징은 open 기준 (look-ahead 없음)
        for m in sorted(pending):
            a=pending[m]
            if a["kind"]!="entry": continue
            i=coins[m]["idx"].get(t)
            if i is None:
                a["age"]+=1
                if a["age"]>max_wait:           # 신호 stale → 취소
                    del pending[m]; rep["cancelled_entries"]+=1
                continue
            del pending[m]
            if m in book.pos: continue
            if len(book.pos)>=NMAX: rep["entry_blocked_nmax"]+=1; continue
            if a["age"]>0: rep["waited_fills"]+=1
            ex=a["extra"]
            fill=coins[m]["o"][i]*(1.0+slip)    # 매수 슬리피지 (S1)
            av=ex["atr"]; stop=fill-ex["stop_mult"]*av
            tp=(fill+ex["tp_mult"]*av) if ex.get("tp_mult") else None
            meta={"tp":tp,"hold":ex["hold"],"trail_mult":ex.get("trail_mult"),
                  "atr":av,"entry_gi":gi,"gap":0,"last_px":coins[m]["c"][i]}
            if book.open(m,fill,stop,eq_now,px_open,meta):
                trades.append({"t":t,"m":m,"kind":"entry","px":fill,"pnl":0.0,"r":0.0,
                               "qty":book.pos[m]["qty"],
                               "notional":book.pos[m]["qty"]*fill,"waited":a["age"]})
        # B) intrabar 관리
        for m in sorted(book.pos):
            p=book.pos[m]; i=coins[m]["idx"].get(t)
            if i is None:                        # 무체결 분: 판정 불가 (S4)
                p["gap"]+=1; rep["gap_bars_held"]+=1
                if p["gap"]>max_halt and m not in pending:
                    pending[m]={"kind":"exit","age":0,"why":"halt_exit"}
                    rep["halt_exits"]+=1
                continue
            p["gap"]=0; p["last_px"]=coins[m]["c"][i]
            hi=coins[m]["h"][i]; lo=coins[m]["l"][i]; op=coins[m]["o"][i]
            if lo<=p["stop"]:                    # S2: 동시터치는 stop 우선(보수적)
                fill=min(p["stop"],op)*(1.0-slip)
                pnl,r=book.close(m,fill); pending.pop(m,None)
                trades.append({"t":t,"m":m,"kind":"stop","px":fill,"pnl":pnl,"r":r}); continue
            if p["tp"] is not None and hi>=p["tp"]:
                fill=max(p["tp"],op)*(1.0-slip)
                pnl,r=book.close(m,fill); pending.pop(m,None)
                trades.append({"t":t,"m":m,"kind":"tp","px":fill,"pnl":pnl,"r":r}); continue
            if p.get("trail_mult"):              # stop 검사 후 갱신 = 봉내 look-ahead 없음
                if hi>p["peak"]: p["peak"]=hi
                p["stop"]=max(p["stop"], p["peak"]-p["trail_mult"]*p["atr"])
        # C) time-cap + 신호 (완성봉 t) → t+1 open pending
        for m in sorted(book.pos):
            if m in pending: continue
            if gi-book.pos[m]["entry_gi"] >= book.pos[m]["hold"]:
                pending[m]={"kind":"exit","age":0,"why":"timecap"}
        if len(book.pos)+sum(1 for a in pending.values() if a["kind"]=="entry") < NMAX:
            for kind,m,ex in cfg["signals"](coins,timeline,gi,book):
                if m in pending or m in book.pos: continue
                pending[m]={"kind":"entry","age":0,"why":"entry","extra":ex}
        # D) 종가 MTM
        eq_series.append(book.equity(px_close))
    # 종말 미청산 → 마지막 체결가 기준 실현 근사 (concentration/LOO 입력)
    for m,p in list(book.pos.items()):
        rep["unclosed_at_end"]+=1
        px=p["last_px"]
        net=p["qty"]*px*(1-book.fee)
        book.realized[m]=book.realized.get(m,0.0)+(net-p["basis"])
    rets=[]
    for i in range(1,len(eq_series)):
        if eq_series[i-1]>0: rets.append(eq_series[i]/eq_series[i-1]-1.0)
    return eq_series, rets, trades, book, rep

def daily_returns(timeline, eq_series):
    """분봉 equity → 일별 수익률 (S6: PSR/DSR 입력 단위). 각 날짜의 마지막 분 equity 사용.
    결측일은 timeline에 존재하므로 자동 포함(0충전 아님 = 실제 MTM)."""
    day_last={}
    for t,e in zip(timeline,eq_series):
        day_last[t[:10]]=e
    days=sorted(day_last)
    eq=[day_last[d] for d in days]
    rets=[eq[i]/eq[i-1]-1.0 for i in range(1,len(eq)) if eq[i-1]>0]
    return days, eq, rets

# ---------- signal generators ----------
def _is_eligible(cd, t):
    """point-in-time eligibility: t >= coin.eligible_from AND 해당 분봉 존재."""
    ef=cd.get("eligible_from")
    if ef is not None and t < ef: return False
    return cd["idx"].get(t) is not None

def make_breakout_signals(chan, z_thresh, stop_mult, tp_mult, hold_bars,
                          trend_win=TREND_WIN, trail_mult=None):
    """모멘텀 브레이크아웃 진입 (완성봉 t 판정 → t+1 open 체결).

    진입 3조건 AND:
      (1) 채널 돌파   : close_t > max(high, 직전 chan봉)      ← 현재봉 제외
      (2) 거래대금 급증: value z-score(직전 VOL_WIN봉) ≥ z_thresh
      (3) 추세 정렬   : close_t > SMA(close, trend_win)

    청산은 전부 엔진 담당 (stop / TP / time-cap / trailing) = 신호함수는 진입만 생성.
    """
    hi_f=f"hi{chan}"; vz_f=f"vz{VOL_WIN}"; sma_f=f"sma{trend_win}"
    def sig(coins,timeline,gi,book):
        t=timeline[gi]; acts=[]
        if len(book.pos)>=NMAX: return acts
        for m,cd in coins.items():
            if m in book.pos: continue
            if not _is_eligible(cd,t): continue
            i=cd["idx"][t]
            ch=cd[hi_f][i]; z=cd[vz_f][i]; tr=cd[sma_f][i]; a=cd["atr14"][i]
            if ch is None or z is None or tr is None or a is None or a<=0: continue
            if cd["c"][i]>ch and z>=z_thresh and cd["c"][i]>tr:
                acts.append(("entry",m,{"atr":a,"stop_mult":stop_mult,"tp_mult":tp_mult,
                                        "hold":hold_bars,"trail_mult":trail_mult}))
        return acts
    return sig

# ⚠ 임계치 격자 없음 — 의도적.
#   초안의 TRIAL_PARAMS(chan 10/20/60, z 2.0/3.0, stop 1.0~2.0×ATR, hold 15~60)는 전부 감이었다.
#   research/ 단계가 임계치를 산출하기 전까지 이 파일은 파라미터를 하드코딩하지 않는다.
#   사용법: make_breakout_signals(**params) 에 research 산출 파라미터를 주입.

# ============================================================
# [UNIVERSE] point-in-time eligibility + 데이터품질 preflight (result-independent)
#   결과(수익) 무관 판정만. 정책 임계 비교·INVALID 선언은 run_robustness.
# ============================================================
_MFMT="%Y-%m-%dT%H:%M"

def _mdt(s): return datetime.datetime.strptime(s,_MFMT)
def _mstr(d): return d.strftime(_MFMT)

def full_minute_grid(start, end):
    """START~END 전체 분 리스트 ("YYYY-MM-DDTHH:MM", 양끝 포함).
    coin 봉의 union으로 하면 전 종목 결측분 방문 누락 → stealth carry-forward. 이 함수 사용 강제."""
    d=_mdt(start); e=_mdt(end); step=datetime.timedelta(minutes=1); out=[]
    while d<=e: out.append(_mstr(d)); d+=step
    return out

def _minutes_between(a,b):
    """양끝 포함 분 수."""
    return int((_mdt(b)-_mdt(a)).total_seconds()//60)+1

def clean_rows(rows):
    """구조적 corruption 봉 드롭: 가격>0 · h≥max(o,c,l) · l≤min(o,c,h) · value≥0 · 전부 finite.
    손상봉 = 사용불가 → 제거(이후 결측분으로 집계). 드롭 수 반환(로깅용)."""
    out=[]; bad=0
    for r in rows:
        o,h,l,c=r["o"],r["h"],r["l"],r["c"]; v=r.get("value",0.0)
        ok=(all(isinstance(x,(int,float)) and math.isfinite(x) for x in (o,h,l,c,v))
            and o>0 and h>0 and l>0 and c>0 and v>=0
            and h>=max(o,c,l) and l<=min(o,c,h))
        if ok: out.append(r)
        else: bad+=1
    return out, bad

def build_universe(data, start, end, min_listing_days=30, min_coverage=0.995, warmup_bars=None):
    """point-in-time eligible universe (1분봉).

      eligible_from = first_valid_bar + max(min_listing_days*1440, warmup_bars) 분
        · min_listing_days: 신규 상장 초기 이상변동 구간 제외
        · warmup_bars: 지표(최대 창=max(CHANNELS,VOL_WIN,TREND_WIN)) 워밍업 — None이면 자동
      coverage = 창 [max(eligible_from,start), end] 내 실제 봉 수 / 기대 분 수
        · S4: 1분봉 결측(무체결 분)은 정상이지만, 보유 중 결측 = InvalidRun 이므로
          coverage 게이트로 유동성 높은 종목만 통과시켜 InvalidRun 빈발을 사전 차단.
        · min_coverage 미달 = universe 제외 (사후 예외처리 아님 = 사전 규칙)

    반환 (coins, report). INVALID 판정은 호출 측(run_robustness) 정책."""
    if warmup_bars is None:
        warmup_bars=max(max(CHANNELS), VOL_WIN, TREND_WIN)+14
    coins={}
    report={"total":len(data),"eligible":0,"corrupt_bars":0,"missing_rate":0.0,
            "per_coin_missing":{},"per_coin_median_value":{},"excluded":{}}
    tot_missing=0; tot_expected=0
    end_dt=_mdt(end)
    for m in sorted(data):
        rows=data[m]
        if not rows: report["excluded"][m]="empty"; continue
        rows,bad=clean_rows(rows); report["corrupt_bars"]+=bad
        if not rows: report["excluded"][m]="all-corrupt"; continue
        rows.sort(key=lambda r:r["t"])
        first_valid=_mdt(rows[0]["t"])
        ef_dt=first_valid+datetime.timedelta(minutes=min_listing_days*1440+warmup_bars)
        if ef_dt>end_dt: report["excluded"][m]="listing<min_listing"; continue
        eligible_from=_mstr(ef_dt)
        window_start=max(eligible_from, start)
        if window_start>end: report["excluded"][m]="no-window"; continue
        inwin=[r for r in rows if window_start<=r["t"]<=end]
        expected=_minutes_between(window_start,end)
        coverage=len(inwin)/expected if expected>0 else 0.0
        if coverage<min_coverage:
            report["excluded"][m]=f"coverage={coverage:.4f}"; continue
        missing=max(0, expected-len(inwin))
        tot_missing+=missing; tot_expected+=expected
        report["per_coin_missing"][m]=missing/expected
        vals=sorted(r["value"] for r in inwin)
        report["per_coin_median_value"][m]=vals[len(vals)//2] if vals else 0.0
        cd=prep_coin(rows); cd["idx"]={r["t"]:i for i,r in enumerate(rows)}
        cd["eligible_from"]=eligible_from
        coins[m]=cd
    report["eligible"]=len(coins)
    report["missing_rate"]=(tot_missing/tot_expected) if tot_expected>0 else 0.0
    return coins, report

def concentration(realized):
    """abs 기반: share_i=|pnl_i|/Σ|pnl_j|, HHI=Σshare². 손실 종목도 집중도에 잡힘.
    top1_pos = top1 positive PnL / total positive PnL 은 진단값(별도 반환)."""
    absv={m:abs(v) for m,v in realized.items() if v!=0}
    tot=sum(absv.values())
    if tot<=0: return 1.0, 1.0, None, 1.0
    shares=sorted((v/tot for v in absv.values()),reverse=True)
    top_coin=max(absv,key=absv.get)
    pos={m:v for m,v in realized.items() if v>0}; tp=sum(pos.values())
    top1_pos=(max(pos.values())/tp) if tp>0 else 0.0
    return shares[0], sum(s*s for s in shares), top_coin, top1_pos

def loo_target_coin(realized):
    """LOO 대상 = 최대 양(+) PnL 기여 종목(star winner). 없으면 None.
    ⚠ concentration()의 abs-max와 구별 — LOO는 winner 제거로 luck 의존성 검사."""
    pos={m:v for m,v in realized.items() if v>0}
    return max(pos,key=pos.get) if pos else None

def trade_stats(trades):
    """스켈핑 고유 진단: 체결 수·승률·기대 R·profit factor·청산 사유 분포·평균 보유봉.
    ⚠ 수익률 자체가 아니라 트레이드 구조 진단 (result-independent 아님 → robustness 전용)."""
    ex=[t for t in trades if t["kind"] in ("stop","tp","exit_open","timecap")]
    en={}
    for t in trades:
        if t["kind"]=="entry": en.setdefault(t["m"],[]).append(t["t"])
    wins=[t["r"] for t in ex if t["pnl"]>0]; losses=[t["r"] for t in ex if t["pnl"]<=0]
    gp=sum(t["pnl"] for t in ex if t["pnl"]>0); gl=-sum(t["pnl"] for t in ex if t["pnl"]<0)
    kinds={}
    for t in ex: kinds[t["kind"]]=kinds.get(t["kind"],0)+1
    n=len(ex)
    return {"n_entry":sum(len(v) for v in en.values()), "n_exit":n,
            "win_rate":(len(wins)/n) if n else 0.0,
            "avg_win_r":(sum(wins)/len(wins)) if wins else 0.0,
            "avg_loss_r":(sum(losses)/len(losses)) if losses else 0.0,
            "expectancy_r":(sum(t["r"] for t in ex)/n) if n else 0.0,
            "profit_factor":(gp/gl) if gl>0 else float('inf'),
            "exit_kinds":kinds}
