#!/usr/bin/env python3
"""
Overnight Sweep — 자면서 자동으로 여러 표본 크기 실행.

3단계 순차 실행:
  1. top10 / 14일  (~10분)
  2. top20 / 30일  (~30분)
  3. top30 / 90일  (~60~90분)

각 단계 완료 시 텔레그램 알림.
실패해도 다음 단계 계속.
캐시된 캔들은 재사용 (--force 없음).

사용:
    nohup python3 research/overnight_sweep.py > /tmp/overnight.log 2>&1 &
    tail -f /tmp/overnight.log
"""
import os
import sys
import subprocess
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from tg_notify import send as tg_send
except Exception:
    tg_send = None


STAGES = [
    ("stage1_top10_14d", ["--top-markets", "10", "--days", "14"]),
    ("stage2_top20_30d", ["--top-markets", "20", "--days", "30"]),
    ("stage3_top30_90d", ["--top-markets", "30", "--days", "90"]),
]

OUTPUT_DIR = "/tmp/overnight_results"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_stage(stage_name, args):
    """단일 stage 실행. 성공/실패 tuple 반환."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log_path = f"{OUTPUT_DIR}/{stage_name}.log"
    parquet_path = f"{OUTPUT_DIR}/{stage_name}.parquet"

    cmd = [
        "python3",
        os.path.join(os.path.dirname(__file__), "run_pipeline.py"),
        *args,
        "--output", parquet_path,
        "--no-tg",  # run_pipeline 자체 텔레그램 스킵 (overnight에서 통합 알림)
    ]

    start = time.time()
    print(f"\n{'='*60}")
    print(f"[{now()}] {stage_name} 시작")
    print(f"  명령: {' '.join(cmd)}")
    print(f"{'='*60}\n")

    if tg_send:
        tg_send(f"🌙 Overnight: {stage_name} 시작", f"{' '.join(args)}\n시작: {now()}")

    try:
        with open(log_path, "w") as log_file:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=7200,  # 2시간 상한
            )
            log_file.write(result.stdout)

        elapsed = time.time() - start
        success = result.returncode == 0

        # 로그 마지막 부분 (Ceiling 결과)
        tail = "\n".join(result.stdout.split("\n")[-70:])

        if success:
            print(f"[{now()}] {stage_name} 완료 ({elapsed:.0f}초)")
            if tg_send:
                tg_send(
                    f"✅ Overnight: {stage_name} 완료 ({elapsed:.0f}s)",
                    tail,
                )
        else:
            print(f"[{now()}] {stage_name} 실패 (code={result.returncode})")
            if tg_send:
                tg_send(
                    f"❌ Overnight: {stage_name} 실패",
                    f"returncode: {result.returncode}\n\n{tail[-2000:]}",
                )
        return success, elapsed

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"[{now()}] {stage_name} 타임아웃 (>{elapsed:.0f}s)")
        if tg_send:
            tg_send(
                f"⏰ Overnight: {stage_name} 타임아웃",
                f"2시간 초과. 다음 단계 계속.",
            )
        return False, elapsed
    except Exception as e:
        print(f"[{now()}] {stage_name} 예외: {e}")
        if tg_send:
            tg_send(f"💥 Overnight: {stage_name} 예외", str(e))
        return False, 0


def main():
    total_start = time.time()
    print(f"\n🌙 Overnight Sweep 시작 — {now()}\n")
    print(f"단계: {[s[0] for s in STAGES]}")
    print(f"결과 저장: {OUTPUT_DIR}/")
    print(f"각 stage 로그: /tmp/overnight_results/*.log\n")

    if tg_send:
        tg_send(
            "🌙 Overnight Sweep 시작",
            f"3단계 순차 실행:\n"
            f"1. top10 x 14일  (~10분)\n"
            f"2. top20 x 30일  (~30분)\n"
            f"3. top30 x 90일  (~60~90분)\n\n"
            f"완료 시 각각 텔레그램 도착.\n"
            f"시작: {now()}",
        )

    results = []
    for stage_name, args in STAGES:
        success, elapsed = run_stage(stage_name, args)
        results.append((stage_name, success, elapsed))
        # stage 간 30초 sleep (Upbit API 안정화)
        time.sleep(30)

    total_elapsed = time.time() - total_start

    # 최종 요약
    summary = f"총 소요: {total_elapsed:.0f}초 ({total_elapsed/60:.1f}분)\n\n"
    for name, success, elapsed in results:
        emoji = "✅" if success else "❌"
        summary += f"{emoji} {name}: {elapsed:.0f}초\n"
    summary += f"\n결과 파일: {OUTPUT_DIR}/\n"
    summary += f"로그: /tmp/overnight_results/*.log"

    print(f"\n{'='*60}")
    print(f"🌙 Overnight Sweep 완료 — {now()}")
    print(f"{'='*60}")
    print(summary)

    if tg_send:
        tg_send("🌙 Overnight Sweep 완료", summary)


if __name__ == "__main__":
    main()
