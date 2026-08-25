#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
레코더 감시 — 이상할 때만 텔레그램으로 알린다.

지금까지 서버 확인은 전부 수동이었다. 레코더가 죽으면 systemd 가 되살리지만
아무한테도 안 알리고, 디스크 가드가 수집을 중단시켜도 로그에만 남는다.
오더북은 과거를 복구할 방법이 없으므로, 조용히 멈춰 있던 시간은 그대로
영구 손실이다. 그래서 감시가 필요하다.

설계 원칙
---------
1. **정상일 때는 아무 말도 안 한다.** 매시간 "정상입니다"가 오면 사람이
   알림을 끄게 되고, 그러면 진짜 사고도 놓친다.
2. **상태 전이에만 알린다.** 같은 문제가 계속되면 REPEAT_HOURS 마다 한 번만
   다시 알린다. 복구되면 복구 사실을 한 번 알리고 상태를 지운다.
3. **감시가 감시 대상을 죽이지 않는다.** 416MB 서버다. 외부 의존성 없이
   /proc 과 systemctl 만 읽고, 즉시 끝낸다.
4. **알림 실패가 감시를 멈추지 않는다.** 텔레그램이 안 되면 stdout 으로
   떨어뜨리고 계속 간다.
"""
import os, sys, json, time, shutil, subprocess, argparse

R = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, R)
# tg_notify 는 저장소 research/ 에 있다. scalp/research 에서도, bot/research
# 에서도 찾을 수 있게 두 경로를 모두 시도한다.
for cand in (os.path.join(R, "..", "..", "research"),
             os.path.expanduser("~/bot/research")):
    if os.path.isdir(cand):
        sys.path.insert(0, os.path.abspath(cand))

try:
    from tg_notify import send as tg_send
except Exception:                                   # 알림 경로가 없어도 감시는 돈다
    def tg_send(title, body, code_block=True):
        print(f"[{title}]\n{body}")
        return False

STATE = os.path.join(R, "data", "watchdog_state.json")
UNIT = "ws-recorder"
REPEAT_HOURS = 6.0          # 같은 문제 재알림 간격


def sh(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=15).stdout.strip()
    except Exception:
        return ""


def mem_available_mb():
    for ln in open("/proc/meminfo"):
        if ln.startswith("MemAvailable:"):
            return int(ln.split()[1]) / 1024
    return float("nan")


def collect(data_dir, min_free_gb, min_avail_mb, max_disc):
    """이상 항목만 담아 반환한다. 정상 항목은 담지 않는다."""
    bad = {}
    info = {}

    active = sh("systemctl", "is-active", UNIT)
    info["unit"] = active
    if active != "active":
        bad["down"] = f"서비스가 {active} 상태다"

    n_restart = sh("systemctl", "show", UNIT, "-p", "NRestarts", "--value")
    info["restarts"] = n_restart
    if n_restart.isdigit() and int(n_restart) > 0:
        bad["restarted"] = f"자동 재시작 {n_restart}회 — 조용히 죽은 구간이 있다"

    free_gb = shutil.disk_usage(data_dir).free / 1e9
    info["free_gb"] = round(free_gb, 2)
    if free_gb < min_free_gb:
        bad["disk"] = f"디스크 여유 {free_gb:.1f}GB < {min_free_gb}GB"

    avail = mem_available_mb()
    info["mem_avail_mb"] = round(avail)
    if avail < min_avail_mb:
        bad["mem"] = f"메모리 여유 {avail:.0f}MB < {min_avail_mb}MB"

    # 최근 1시간 끊김. 소량은 정상이지만 폭증하면 그 구간에 구멍이 많다.
    log = sh("journalctl", "-u", UNIT, "--since", "1 hour ago", "--no-pager")
    n_disc = log.count("끊김")
    info["disc_1h"] = n_disc
    if n_disc > max_disc:
        bad["disconnect"] = f"최근 1시간 끊김 {n_disc}회 > {max_disc}회"

    # 디스크 가드가 수집을 스스로 멈춘 경우 — 가장 조용한 실패다
    if "disk_stop" in log or "setup_failed" in log:
        bad["selfstop"] = "레코더가 스스로 수집을 중단했다 (디스크 가드 또는 설정 오류)"

    # 하트비트가 최근에 찍혔는지. 프로세스는 살아있는데 수신이 멎은 경우를 잡는다.
    hb = sh("journalctl", "-u", UNIT, "-n", "1", "--no-pager", "-o", "cat")
    info["last_hb"] = hb[-90:] if hb else ""
    if active == "active" and "up=" not in hb and n_disc == 0:
        bad["silent"] = "서비스는 active 인데 최근 하트비트가 안 보인다"

    return bad, info


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(s, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.path.join(R, "data"))
    ap.add_argument("--min-free-gb", type=float, default=3.0)
    ap.add_argument("--min-avail-mb", type=float, default=80.0)
    ap.add_argument("--max-disconnect", type=int, default=10)
    ap.add_argument("--repeat-hours", type=float, default=REPEAT_HOURS)
    ap.add_argument("--test", action="store_true",
                    help="정상이어도 현재 상태를 한 번 보낸다 (설치 확인용)")
    a = ap.parse_args()

    os.makedirs(a.data_dir, exist_ok=True)
    bad, info = collect(a.data_dir, a.min_free_gb, a.min_avail_mb, a.max_disconnect)
    st = load_state()
    now = time.time()

    if a.test:
        tg_send("🔎 레코더 감시 설치 확인",
                json.dumps(info, ensure_ascii=False, indent=1) +
                ("\n\n이상 없음" if not bad else "\n\n이상:\n" +
                 "\n".join(f"· {v}" for v in bad.values())))
        return

    if not bad:
        # 직전에 문제가 있었다면 복구 사실만 한 번 알리고 상태를 지운다
        if st.get("open"):
            tg_send("✅ 레코더 정상 복구",
                    "직전 이상:\n" + "\n".join(f"· {v}" for v in st["open"].values())
                    + "\n\n현재:\n" + json.dumps(info, ensure_ascii=False, indent=1))
            save_state({})
        return

    prev = st.get("open", {})
    last = st.get("sent_at", 0)
    new_keys = set(bad) - set(prev)
    stale = (now - last) > a.repeat_hours * 3600

    # 새 문제가 생겼거나, 같은 문제가 오래 지속될 때만 보낸다
    if new_keys or stale:
        head = "🚨 레코더 이상" + (" (지속)" if not new_keys else "")
        tg_send(head,
                "\n".join(f"· {v}" for v in bad.values())
                + "\n\n현재 상태:\n" + json.dumps(info, ensure_ascii=False, indent=1)
                + "\n\n확인: journalctl -u ws-recorder -n 20 --no-pager")
        save_state({"open": bad, "sent_at": now})
    else:
        save_state({"open": bad, "sent_at": last})


if __name__ == "__main__":
    main()
