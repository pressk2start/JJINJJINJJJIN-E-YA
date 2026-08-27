#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
일일 특징 파생 — 원본이 지워져도 남는 것을 만든다.

왜 필요한가
-----------
원본(data/ws)은 `--retain-days 14` 로 굴러가므로 14일째부터 첫날이 지워진다.
서버가 죽어도 전부 사라진다. 오더북은 과거를 받을 방법이 없으니 그건 영구
손실이다. 반면 분석이 실제로 쓰는 1초 격자 특징은 원본보다 훨씬 작아서
영구 보관과 반출이 쉽다.

  원본        약 500MB/일
  파생 특징   약 20~30MB/일

그래서 역할을 나눈다.
  · 원본     — 14일 롤링. 나중에 **다른** 특징을 다시 뽑고 싶을 때를 위한 것.
  · 파생     — 영구 보관. 서버가 죽어도 이건 남는다.
최악의 경우 잃는 것은 '재계산할 권리'이지 데이터 자체가 아니다.

왜 시간 단위로 쪼개는가
-----------------------
ws_features 는 출력 행을 메모리에 모았다가 마지막에 쓴다(`out += s.rows`).
하루치 1초 격자는 종목당 86,400행이라 416MB 서버에서는 스왑을 타거나 죽는다.
그래서 **시간 파일 하나씩 별도 프로세스로** 돌리고 결과를 이어붙인다.
프로세스가 끝날 때마다 메모리가 확실히 반환된다.

이어붙이기는 gzip 스트림 연결을 쓴다 — gzip 은 연결된 스트림을 하나로 읽으므로
재압축이 필요 없다. 1 vCPU 서버에서 재압축은 낭비다.

시간 경계 비용
--------------
시간마다 프로세스가 새로 시작하므로 각 시간의 첫 프레임은 직전 스냅샷이 없다.
업비트 WS 오더북은 델타가 아니라 **전체 스냅샷**이라 호가 상태는 첫 메시지
(보통 40ms 이내)에 복원되지만, transition 단위 depletion 귀속은 직전 스냅샷을
필요로 하므로 그 한 프레임은 값이 비어 있다.
비용은 하루 24프레임 / 86,400프레임 = 0.03%. 이 정도를 감수하는 대신
메모리 안전을 얻는다. 후처리에서 경계 프레임을 제외하려면 매시 정각(초=0)
프레임을 보면 된다.
"""
import os, sys, glob, gzip, time, shutil, argparse, subprocess, datetime

R = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(R, "data", "ws")
FEAT = os.path.join(R, "data", "features")
MARKETS = ["KRW-XRP", "KRW-TRUMP", "KRW-ETH", "KRW-SOL", "KRW-BTC"]


def completed_days(today_utc):
    """오늘은 아직 안 끝났으므로 제외한다. 반쪽짜리 날을 만들지 않는다."""
    if not os.path.isdir(WS):
        return []
    return sorted(d for d in os.listdir(WS)
                  if len(d) == 10 and d < today_utc
                  and os.path.isdir(os.path.join(WS, d)))


def build_day(day, market, grid, max_stale, nice, verbose=True):
    """한 종목의 하루치를 시간 파일 단위로 처리해 이어붙인다."""
    hours = sorted(glob.glob(os.path.join(WS, day, "*.jsonl.gz")))
    if not hours:
        return None
    outdir = os.path.join(FEAT, day)
    os.makedirs(outdir, exist_ok=True)
    final = os.path.join(outdir, f"{market}.jsonl.gz")
    if os.path.exists(final):
        return ("skip", os.path.getsize(final), len(hours))

    tmp = final + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    n_ok = n_fail = 0
    with open(tmp, "wb") as dst:
        for hf in hours:
            piece = tmp + ".h"
            cmd = ["nice", "-n", str(nice), sys.executable,
                   os.path.join(R, "ws_features.py"), hf,
                   "--market", market, "--grid", str(grid),
                   "--max-stale-sec", str(max_stale), "--out", piece]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if r.returncode != 0 or not os.path.exists(piece):
                n_fail += 1
                if verbose:
                    tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] 
                    print(f"    ! {os.path.basename(hf)} 실패: {tail}", flush=True)
                continue
            # gzip 스트림 연결 — 재압축 없이 그대로 이어붙인다
            with open(piece, "rb") as src:
                shutil.copyfileobj(src, dst, 1 << 20)
            os.remove(piece)
            n_ok += 1
    if n_ok == 0:
        os.remove(tmp)
        return ("fail", 0, len(hours))
    os.replace(tmp, final)              # 완성된 것만 최종 이름을 갖는다
    return ("ok" if n_fail == 0 else "partial", os.path.getsize(final), n_ok)


def count_rows(path):
    n = 0
    try:
        with gzip.open(path, "rt") as f:
            for _ in f:
                n += 1
    except Exception:
        return None
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default=",".join(MARKETS))
    ap.add_argument("--grid", type=float, default=1.0)
    ap.add_argument("--max-stale-sec", type=float, default=60.0)
    ap.add_argument("--nice", type=int, default=15,
                    help="라이브 봇과 레코더에 CPU 를 양보한다")
    ap.add_argument("--day", default="", help="특정 날짜만 (기본: 끝난 날 전부)")
    ap.add_argument("--verify", action="store_true", help="행 수까지 센다 (느림)")
    a = ap.parse_args()

    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    days = [a.day] if a.day else completed_days(today)
    mks = [s.strip() for s in a.markets.split(",") if s.strip()]
    if not days:
        print(f"[daily] 처리할 완료 날짜 없음 (오늘 {today} 는 제외)")
        return

    t0 = time.time()
    total = 0
    for day in days:
        for mk in mks:
            r = build_day(day, mk, a.grid, a.max_stale_sec, a.nice)
            if r is None:
                continue
            status, size, n = r
            total += size
            extra = ""
            if a.verify and status in ("ok", "partial", "skip"):
                rows = count_rows(os.path.join(FEAT, day, f"{mk}.jsonl.gz"))
                extra = f" · {rows:,}행" if rows else ""
            print(f"[daily] {day} {mk:<10} {status:<8} "
                  f"{size/1e6:>7.1f}MB · 시간파일 {n}{extra}", flush=True)

    free = shutil.disk_usage(R).free / 1e9
    print(f"[daily] 완료 {time.time()-t0:.0f}초 · 이번 실행 {total/1e6:.1f}MB "
          f"· 파생 누적 {dir_mb(FEAT):.1f}MB · 디스크 여유 {free:.1f}GB")


def dir_mb(p):
    t = 0
    for root, _, fs in os.walk(p):
        for f in fs:
            try:
                t += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return t / 1e6


if __name__ == "__main__":
    main()
