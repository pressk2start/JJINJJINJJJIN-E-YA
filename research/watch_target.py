#!/usr/bin/env python3
"""
관심 route 감시 + milestone 알림 (조언자 스펙 반영).

목적:
  - 매 리포트에서 관심 route만 별도 텔레 알림 (A: 리포트 필터링 효과)
  - n이 milestone (20/50/100) 통과 시 특별 알림 (B: 자동 알림)
  - bot.py 안 건드리고 리포트 파싱만

Usage (서버 crontab):
  */30 * * * * journalctl -u upbit-bot --since "35 minutes ago" | \
    python3 /home/ubuntu/bot/research/watch_target.py >> /tmp/watch_target.log 2>&1

또는:
  cat report.txt | python3 research/watch_target.py

핵심 관심 route (조언자 지정 5개):
  1. B45_CS40_VR35_TR180_bp30_240  — 최종 후보 (backtest +0.5004%)
  2. B45_CS40_VR3_TR180_bp30_240   — 안정형 비교군 (backtest +0.4598%)
  3. CS40_VR3_TR180_bp30_240       — B45 효과 확인용 (backtest +0.4297%)
  4. CS40_TR180_bp30_240           — VR 효과 확인용
  5. CLM_CS40_TR180_15_240         — 기존 LIVE 기준선

Milestone (사용자 지정):
  n=20:  1차 방향 판단선
  n=50:  후보 확정선
  n=100: LIVE 검토선
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from tg_notify import send as tg_send
except Exception:
    tg_send = None


# 조언자 지정: 관심 5개 route + backtest 기준값 (없으면 None)
WATCH = [
    ("B45_CS40_VR35_TR180_bp30_240", 0.5004),
    ("B45_CS40_VR3_TR180_bp30_240",  0.4598),
    ("CS40_VR3_TR180_bp30_240",      0.4297),
    ("CS40_TR180_bp30_240",          None),
    ("CLM_CS40_TR180_15_240",        None),  # LIVE
]

MILESTONES = [20, 50, 100]

# 상태 파일: /tmp 대신 영속 경로 (재부팅 시 milestone 중복 알림 방지)
_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(_HERE, "state")
STATE_FILE = os.path.join(STATE_DIR, "watch_target_state.json")


def parse_route(text, route):
    """
    리포트에서 route의 n/WR/PnL/MAE/MFE/cap 추출.
    두 가지 라인 패턴 대응:
      "🟡[B45_...] 17전8승 wr47% PnL+0.51% MAE-0.37% cap48% watch"
      "CS40_VR3_...:과열감지_... n=17 PnL+0.51% MFE+1.06% MAE-0.37% cap=48% ↘decay"
    """
    # 패턴 1: 대괄호
    p1 = (rf"\[{re.escape(route)}\][^\n]*?(\d+)전\d+승\s+wr(\d+)%\s+"
          rf"PnL([+-]?\d+\.\d+)%\s+MAE([+-]?\d+\.\d+)%\s+cap([+-]?\d+)%")
    m = re.search(p1, text)
    if m:
        return {
            "n": int(m.group(1)), "wr": int(m.group(2)),
            "pnl": float(m.group(3)), "mae": float(m.group(4)),
            "mfe": None, "cap": int(m.group(5)),
        }
    # 패턴 2: n= 스타일
    p2 = (rf"{re.escape(route)}[^\n]*?n=(\d+)\s+PnL([+-]?\d+\.\d+)%\s+"
          rf"MFE([+-]?\d+\.\d+)%\s+MAE([+-]?\d+\.\d+)%\s+cap=([+-]?\d+)%")
    m = re.search(p2, text)
    if m:
        return {
            "n": int(m.group(1)), "wr": None,
            "pnl": float(m.group(2)), "mae": float(m.group(4)),
            "mfe": float(m.group(3)), "cap": int(m.group(5)),
        }
    return None


def format_status(route, info, baseline):
    """route별 한 줄 상태."""
    parts = [f"n={info['n']}", f"PnL{info['pnl']:+.2f}%"]
    if info["wr"] is not None:
        parts.append(f"WR{info['wr']}%")
    if info["mae"] is not None:
        parts.append(f"MAE{info['mae']:+.2f}%")
    if info["cap"] is not None:
        parts.append(f"cap{info['cap']}%")
    line = f"  {route}: " + " ".join(parts)
    if baseline is not None:
        diff = info["pnl"] - baseline
        line += f" [BT{baseline:+.2f}% 대비{diff:+.2f}%p]"
    return line


def format_milestone(route, m, info, baseline):
    """milestone 도달 알림 포맷 (조언자 스펙)."""
    lines = [
        f"🎯 {route}",
        f"   n={m} 도달!",
        f"   PnL={info['pnl']:+.3f}%",
    ]
    if info["wr"] is not None:
        lines.append(f"   WR={info['wr']}%")
    if info["mae"] is not None:
        lines.append(f"   MAE={info['mae']:+.2f}%")
    if info["mfe"] is not None:
        lines.append(f"   MFE={info['mfe']:+.2f}%")
    if info["cap"] is not None:
        lines.append(f"   cap={info['cap']}%")
    if baseline is not None:
        diff = info["pnl"] - baseline
        lines.append(f"   Backtest 기준 {baseline:+.2f}% 대비 {diff:+.2f}%p")
    # 판정 문구
    if m == 20:
        lines.append("   판정: 관찰 지속 (방향 확인)")
    elif m == 50:
        lines.append("   판정: 후보 확정 검토 시점")
    elif m == 100:
        lines.append("   판정: 🚨 LIVE 승격 검토 시점")
    return "\n".join(lines)


def main():
    text = sys.stdin.read()
    if not text.strip():
        print("[watch] 입력 없음 (stdin)")
        return

    # 상태 디렉터리 생성 (없으면 만들기)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
    except Exception as e:
        print(f"[watch] STATE_DIR 생성 실패: {e}")

    prev = {}
    if os.path.exists(STATE_FILE):
        try:
            prev = json.load(open(STATE_FILE))
        except Exception:
            prev = {}

    now = {}
    status_lines = []
    milestone_hits = []

    for route, baseline in WATCH:
        info = parse_route(text, route)
        if info is None:
            continue
        now[route] = info
        status_lines.append(format_status(route, info, baseline))

        prev_n = prev.get(route, {}).get("n", 0)
        for m in MILESTONES:
            if prev_n < m <= info["n"]:
                milestone_hits.append(format_milestone(route, m, info, baseline))

    # milestone 통과 시 별도 특별 알림
    if milestone_hits:
        body = "\n\n".join(milestone_hits)
        body += "\n\n[전체 관심 route 현황]\n" + "\n".join(status_lines)
        title = f"🎯 Milestone 도달 ({len(milestone_hits)}건)"
        if tg_send:
            tg_send(title, body)
        print(title)
        print(body)
    else:
        # milestone 없어도 로그엔 상태 남김 (텔레 발송 X, 스팸 방지)
        if status_lines:
            print("[watch] 현황 (milestone 없음):")
            print("\n".join(status_lines))

    # 상태 저장
    try:
        json.dump(now, open(STATE_FILE, "w"), indent=2)
    except Exception as e:
        print(f"[watch] 상태 저장 실패: {e}")


if __name__ == "__main__":
    main()
