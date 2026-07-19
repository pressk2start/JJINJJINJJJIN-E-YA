#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enrich_clm_csv_with_pump.py  (자립형: 이 파일 하나면 로컬에서 바로 실행)

라이브 CLM CSV의 각 진입에 대해:
  - 진입 timestamp x market 으로 업비트 1분봉을 재조회
  - "진입 직전 완성 1분봉"의 상승률(pump_1m = (close-open)/open*100)을 붙임
    (백테스트와 동일 정의 = 미래 정보 없는, 진입 순간 이미 알 수 있는 신호 캔들)
  - ≥2 / ≥3 / ≥4 % 버킷별로 CLM 실제 성과를 분해
  - 급등 세기 분포 히스토그램 출력
  - A/B/C 판정:
      A) 진입이 대부분 ≥2%대  -> RSI/EMA 필터 alpha 없음 -> 임계값 상향
      B) 진입이 ≥3%+에 몰림   -> 필터가 강한 급등을 고름 -> 더 파고들기
      C) 진입이 ≥3%+인데 성과 저조 -> 필터가 나쁜 급등을 고름(alpha 마이너스)

사용법:
  python enrich_clm_csv_with_pump.py CLM_log.csv
  # 컬럼 자동탐지. 안 잡히면 명시:
  python enrich_clm_csv_with_pump.py CLM_log.csv --ts entry_time --market market --pnl ret_pct

출력:
  <입력파일>_enriched.csv  (원본 + pump_1m, pump_1m_bucket 컬럼)
  콘솔에 히스토그램/버킷성과/판정
"""
import sys, csv, json, time, argparse, re, os, datetime as dt

# ---------------- 업비트 fetch (keep-alive + UA + 재시도) ----------------
import urllib.request, urllib.error, socket
_UA = {"User-Agent": "Mozilla/5.0 (clm-enrich)", "Accept": "application/json"}
_BASE = "https://api.upbit.com/v1"
try:
    import requests
    _SESS = requests.Session(); _SESS.headers.update(_UA)
    _SESS.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0))
    def _get(path, params, tries=7):
        last = None
        for i in range(tries):
            try:
                r = _SESS.get(_BASE + path, params=params, timeout=20)
                if r.status_code == 200: return r.json()
                if r.status_code == 429: time.sleep(0.8 + i*0.7); last="429"; continue
                if 400 <= r.status_code < 500: raise RuntimeError(f"HTTP {r.status_code} {r.text[:80]}")
                last=f"HTTP {r.status_code}"; time.sleep(0.4+i*0.4)
            except requests.RequestException as e:
                last=e; time.sleep(0.4+i*0.5)
        raise RuntimeError(f"GET fail {path}: {last}")
except ImportError:  # requests 없으면 urllib 폴백
    from urllib.parse import urlencode
    def _get(path, params, tries=7):
        last=None
        for i in range(tries):
            try:
                req=urllib.request.Request(_BASE+path+"?"+urlencode(params), headers=_UA)
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code==429: time.sleep(0.8+i*0.7)
                elif 400<=e.code<500: raise
                else: time.sleep(0.4+i*0.4)
                last=e
            except (urllib.error.URLError, ConnectionResetError, socket.timeout) as e:
                last=e; time.sleep(0.4+i*0.5)
        raise RuntimeError(f"GET fail {path}: {last}")

# 1분봉 캐시: (market, minute_utc_iso) -> pump_pct
_cache = {}

def prior_minute_pump(market, entry_ms):
    """진입 직전 '완성된' 1분봉의 상승률(%) = 미래정보 없는 신호 캔들."""
    entry = dt.datetime.utcfromtimestamp(entry_ms/1000).replace(second=0, microsecond=0)
    target = entry - dt.timedelta(minutes=1)               # 직전 완성 분
    key = (market, target.isoformat())
    if key in _cache:
        return _cache[key]
    to_iso = entry.strftime("%Y-%m-%dT%H:%M:%S")            # entry 분까지 조회
    cs = _get("/candles/minutes/1", {"market": market, "count": 3, "to": to_iso})
    by = {c["candle_date_time_utc"]: c for c in cs}
    c = by.get(target.strftime("%Y-%m-%dT%H:%M:%S"))
    if not c:                                               # 정확 매칭 실패 시 가장 최근 완성분
        cs2 = [x for x in cs if x["candle_date_time_utc"] <= target.strftime("%Y-%m-%dT%H:%M:%S")]
        c = cs2[0] if cs2 else (cs[0] if cs else None)
    if not c or c["opening_price"] <= 0:
        _cache[key] = None; return None
    pct = (c["trade_price"] - c["opening_price"]) / c["opening_price"] * 100
    _cache[key] = round(pct, 3)
    return _cache[key]

# ---------------- 파싱 헬퍼 ----------------
def parse_ts(v):
    v = str(v).strip()
    # epoch?
    try:
        f = float(v)
        if f > 1e12:  return int(f)          # ms
        if f > 1e9:   return int(f*1000)     # s
    except ValueError:
        pass
    v2 = v.replace("Z", "").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            d = dt.datetime.strptime(v2[:26], fmt)
            return int(d.replace(tzinfo=dt.timezone.utc).timestamp()*1000)
        except ValueError:
            continue
    raise ValueError(f"timestamp 파싱 실패: {v!r}")

def norm_market(v):
    v = str(v).strip().upper().replace("/", "-").replace("_", "-")
    if v.startswith("KRW-"): return v
    if v.endswith("-KRW"):   return "KRW-" + v[:-4]
    return "KRW-" + v

def auto_col(header, cands):
    low = {h.lower().strip(): h for h in header}
    for c in cands:
        if c in low: return low[c]
    for h in header:                       # 부분일치
        if any(c in h.lower() for c in cands): return h
    return None

TS_CANDS     = ["entry_time","entry_ts","timestamp","time","datetime","ts","진입시각","진입","open_time"]
MKT_CANDS    = ["market","symbol","coin","ticker","종목","pair"]
PNL_CANDS    = ["net_pnl","pnl","ret","return","ret_pct","pnl_pct","profit","수익","손익","result_pct","pct"]

# ---------------- 메인 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--ts"); ap.add_argument("--market"); ap.add_argument("--pnl")
    ap.add_argument("--tz-offset", type=float, default=0.0,
                    help="CSV time이 KST 등 로컬시각이면 UTC와의 시차(시간). KST면 9")
    ap.add_argument("--date", help="time이 HH:MM:SS만 있을 때 기준날짜 YYYYMMDD. 미지정시 파일명에서 유추")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.csv, encoding="utf-8-sig")))
    if not rows:
        print("빈 CSV"); return
    header = list(rows[0].keys())
    ts_col  = a.ts     or auto_col(header, TS_CANDS)
    mkt_col = a.market or auto_col(header, MKT_CANDS)
    pnl_col = a.pnl    or auto_col(header, PNL_CANDS)
    print(f"컬럼: timestamp={ts_col!r} market={mkt_col!r} pnl={pnl_col!r}  (총 {len(rows)}행)")
    if not ts_col or not mkt_col:
        print("!! timestamp/market 컬럼 자동탐지 실패. --ts / --market 로 지정하세요.")
        print("   사용가능 컬럼:", header); return

    off_ms = int(a.tz_offset*3600*1000)
    # 날짜 없는 time 컬럼 대응: --date 또는 파일명(YYYYMMDD)에서 기준일 추출
    base_date = a.date
    if not base_date:
        m = re.search(r'(20\d{6})', os.path.basename(a.csv))
        base_date = m.group(1) if m else None
    sample = str(rows[0][ts_col]).strip()
    time_only = bool(re.match(r'^\d{1,2}:\d{2}(:\d{2})?$', sample))
    if time_only and not base_date:
        print("!! time이 시:분:초만 있는데 기준날짜를 못 찾음.")
        print("   파일명에 YYYYMMDD가 없으면  --date 20260719  처럼 지정하세요."); return
    if time_only:
        print(f"time-only 감지 → 기준날짜 {base_date} + 자정넘김 자동보정  (tz-offset={a.tz_offset})")

    def resolve_ms(raw, state):
        raw = str(raw).strip()
        if not time_only:
            return parse_ts(raw)
        p = raw.split(":"); secs = int(p[0])*3600 + int(p[1])*60 + (int(p[2]) if len(p) > 2 else 0)
        if state["prev"] is not None and secs < state["prev"]:
            state["day"] += 1                                  # 자정 넘어감
        state["prev"] = secs
        d = dt.datetime.strptime(base_date, "%Y%m%d").replace(tzinfo=dt.timezone.utc) \
            + dt.timedelta(days=state["day"], seconds=secs)
        return int(d.timestamp()*1000)

    out = []
    t0 = time.time(); fails = 0
    state = {"prev": None, "day": 0}
    for i, r in enumerate(rows):
        try:
            ems = resolve_ms(r[ts_col], state) - off_ms   # 로컬->UTC 보정
            mkt = norm_market(r[mkt_col])
            pump = prior_minute_pump(mkt, ems)
        except Exception as e:
            pump = None; fails += 1
            if fails <= 5: print(f"  skip row{i}: {e}")
        r = dict(r)
        r["pump_1m"] = pump
        r["pump_1m_bucket"] = bucket(pump)
        out.append(r)
        if (i+1) % 25 == 0:
            print(f"  {i+1}/{len(rows)} ... {time.time()-t0:.0f}s")
        time.sleep(0.05)

    outpath = a.csv.rsplit(".", 1)[0] + "_enriched.csv"
    with open(outpath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    print(f"\n저장: {outpath}  (실패 {fails}건)")

    report(out, pnl_col)

def bucket(p):
    if p is None: return "NA"
    if p < 1:  return "<1%"
    if p < 2:  return "1-2%"
    if p < 3:  return "2-3%"
    if p < 4:  return "3-4%"
    if p < 5:  return "4-5%"
    return "5%+"

def report(rows, pnl_col):
    vals = [r["pump_1m"] for r in rows if r["pump_1m"] is not None]
    if not vals:
        print("pump_1m 계산된 행이 없습니다 (timestamp/market/tz 확인)."); return
    n = len(vals)
    import statistics as st
    print("\n" + "="*56)
    print(f"진입 급등세기 분포 (n={n})  avg {st.mean(vals):+.2f}% / median {st.median(vals):+.2f}%")
    order = ["<1%","1-2%","2-3%","3-4%","4-5%","5%+"]
    cnt = {b:0 for b in order}
    for v in vals: cnt[bucket(v)] += 1
    for b in order:
        c = cnt[b]; bar = "█"*int(round(40*c/n))
        print(f"  {b:>5}: {c:4d} ({100*c/n:4.0f}%) {bar}")
    ge2 = sum(1 for v in vals if v>=2); ge3 = sum(1 for v in vals if v>=3); ge4 = sum(1 for v in vals if v>=4)
    print(f"  ≥2%: {100*ge2/n:.0f}%   ≥3%: {100*ge3/n:.0f}%   ≥4%: {100*ge4/n:.0f}%")

    # 성과 분해 (pnl 컬럼 있으면)
    verdict_data = {"med": st.median(vals), "ge3": 100*ge3/n}
    if pnl_col:
        def num(x):
            try: return float(str(x).replace("%","").strip())
            except: return None
        seg = {"<2%":[], "2-3%":[], "3-4%":[], "4%+":[]}
        for r in rows:
            p = r["pump_1m"]; pv = num(r.get(pnl_col))
            if p is None or pv is None: continue
            k = "<2%" if p<2 else "2-3%" if p<3 else "3-4%" if p<4 else "4%+"
            seg[k].append(pv)
        print("\n[급등세기 버킷별 CLM 실제 성과]  (pnl='%s')" % pnl_col)
        for k in ["<2%","2-3%","3-4%","4%+"]:
            xs = seg[k]
            if xs:
                wr = 100*sum(1 for x in xs if x>0)/len(xs)
                print(f"  {k:>5}: {len(xs):3d}건  avg {st.mean(xs):+.3f}  wr {wr:.0f}%")
            else:
                print(f"  {k:>5}:   0건")
        strong = seg["3-4%"] + seg["4%+"]
        verdict_data["strong_avg"] = st.mean(strong) if strong else None

    print("\n[판정]")
    med = verdict_data["med"]; ge3p = verdict_data["ge3"]
    if ge3p < 35:
        print(f"  ▶ A: 진입의 {100- ge3p:.0f}%가 ≥3% 미만(중앙값 {med:+.2f}%). 약한 급등에 반응.")
        print("     → RSI/EMA 필터가 급등 세기 게이트 역할을 못함. '임계값 상향'이 1순위 수정.")
    else:
        sa = verdict_data.get("strong_avg")
        if sa is not None and sa <= 0.15:
            print(f"  ▶ C: 진입의 {ge3p:.0f}%가 ≥3%인데 그 강한 급등 성과도 avg {sa:+.3f}로 저조.")
            print("     → 필터가 '나쁜 강한급등'을 고르는 중(alpha 마이너스 가능). 필터 반전/폐기 검토.")
        else:
            print(f"  ▶ B: 진입의 {ge3p:.0f}%가 ≥3%에 몰림. 필터가 강한 급등을 선택.")
            print("     → RSI/EMA가 뭔가 하고 있음. 2순위(필터 alpha 정밀측정)로 진행.")
    print("="*56)

if __name__ == "__main__":
    main()
