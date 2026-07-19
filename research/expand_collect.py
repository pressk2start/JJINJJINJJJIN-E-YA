# CS40+VR3 표본확대: 새 마켓 배치 1분봉 다운→CS40+VR3 탐지→초봉 수집(캐시 누적)
import sys; sys.path.insert(0,'research')
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import requests
import clm_detector as cd
from data_loader import download_candles, load_candles
from seconds_loader import collect_events_seconds_threaded
FMT="%Y-%m-%dT%H:%M:%S"

r=requests.get('https://api.upbit.com/v1/market/all',params={'is_details':'false'},timeout=8)
allm=[m['market'] for m in r.json() if m['market'].startswith('KRW-')]
stable={'USDT','USDC','USDS','USDE','USD1','XAUT','DAI'}
allm=[m for m in allm if m.split('-')[1] not in stable]
old=set(open('research/markets.txt').read().split(','))
new=[m for m in allm[120:250] if m not in old]
print(f"새 마켓 {len(new)}개 다운로드(21일)...", flush=True)

def dl(m):
    try: download_candles(m, days_back=21); return m
    except Exception as e: print(f"DL ERR {m}: {e}", flush=True); return None
with ThreadPoolExecutor(max_workers=8) as ex:
    for j,_ in enumerate(ex.map(dl, new)):
        if (j+1)%20==0: print(f"  다운 {j+1}/{len(new)}", flush=True)

cd.CS_MAX=0.40; cd.VR5_MIN=3.0
metas=[]
for m in new:
    df=load_candles(m)
    if df is None or len(df)<10: continue
    df["market"]=m
    for _,rr in cd.detect_clm(df).iterrows():
        st=datetime.strptime(rr["candle_date_time_utc"],FMT)
        metas.append({"market":m,"entry_dt":st+timedelta(seconds=60),"entry_price":float(rr["trade_price"])})
print(f"새 CS40+VR3 신호 {len(metas)}건 → 초봉 수집...", flush=True)
collect_events_seconds_threaded(metas,300,"research/data_sec/sec_cache.parquet",workers=8)

# markets.txt 확대
allmk=sorted(old | set(new))
open('research/markets.txt','w').write(','.join(allmk))
print(f"markets.txt 확대: {len(allmk)}개. 수집 완료.", flush=True)
