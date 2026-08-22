"""fetch_snapshot.py — pre-hash Upbit 일봉 스냅샷 생성 (audit item 5A).

이 스크립트 = 데이터 fetch만. 성과 계산 안 함 → discovery-performance contamination 아님.
결과 = swing/data/snapshot.json.gz + snapshot_sha256 + market list + created_utc metadata.

봉인 순서:
  1. python3 swing/fetch_snapshot.py            # ← 이 스크립트 (pre-hash)
  2. git add swing/data/snapshot.json.gz
  3. git commit + git tag (봉인)                # ← snapshot이 hash에 포함됨
  4. python3 swing/run_robustness.py            # ← post-hash, snapshot만 읽음, 네트워크 X

⚠ survivorship: /market/all = 현재 상장만 (§8/§10(d) caveat, 공개 API 한계).
"""
import os, sys, json, gzip, hashlib, time, datetime
sys.path.insert(0, os.path.dirname(__file__) or ".")
import swing as SW

CACHE_VERSION="v1"
SCRIPT_DIR=os.path.dirname(os.path.abspath(__file__))
DATA_DIR=os.path.join(SCRIPT_DIR, "data")
SNAPSHOT=os.path.join(DATA_DIR, "snapshot.json.gz")

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"[fetch] Upbit KRW markets 리스트 조회...", flush=True)
    mk=sorted(SW.markets_krw())
    print(f"[fetch] {len(mk)} markets 발견")
    data={}; skipped=0
    for i, m in enumerate(mk):
        try:
            data[m]=SW.load_daily(m)
        except Exception as e:
            print(f"skip {m}: {e}"); skipped+=1
        if i%20==0: print(f"[fetch] {i}/{len(mk)}", flush=True)
        time.sleep(0.01)
    print(f"[fetch] {len(data)}/{len(mk)} markets 완료 (skipped {skipped})")

    # deterministic serialization + sha256 anchor
    payload=json.dumps(data, sort_keys=True, ensure_ascii=False)
    snap_sha=hashlib.sha256(payload.encode("utf-8")).hexdigest()
    blob={
        "_meta": {
            "version": CACHE_VERSION,
            "n_markets": len(mk),
            "n_data": len(data),
            "skipped": skipped,
            "markets": sorted(data.keys()),
            "snapshot_sha256": snap_sha,
            "created_utc": datetime.datetime.utcnow().isoformat()+"Z",
            "note": "survivorship: /market/all=현재 상장만. 재현성 anchor = snapshot_sha256.",
        },
        "data": data,
    }

    tmp=SNAPSHOT+".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(blob, f)
    os.replace(tmp, SNAPSHOT)
    size_mb=os.path.getsize(SNAPSHOT)/1024/1024
    print(f"[snapshot] 저장 완료: {SNAPSHOT} ({size_mb:.1f} MB)")
    print(f"[snapshot] sha256={snap_sha}")
    print(f"[snapshot] 다음 단계: git add swing/data/snapshot.json.gz && git commit + tag")

if __name__=="__main__":
    main()
