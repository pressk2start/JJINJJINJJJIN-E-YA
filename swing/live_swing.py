"""live_swing.py — 스윙 봇 라이브 엔트리 (skeleton).
⚠⚠⚠ 실주문 금지 상태 (v1.7 protocol에 의해 hash+robustness+prospective PASS 전까지 하드락).
    이 파일은 아직 hash 전이라 항상 paper 모드로 강제됨. 아래 4중 게이트 통과해야만 실주문:

      1. AUTO_TRADE=1 (환경변수, kill switch)
      2. .sealed_spec_marker 파일 존재 (봉인 확인 anchor)
      3. .prospective_pass_marker 파일 존재 (검증 통과 anchor)
      4. --arm-live CLI 플래그 (매 실행 명시적 동의)

  중 하나라도 없으면 paper 모드 강제 · 실주문 함수 자체가 raise SafetyBlocked.

telegram 알림 통합. 시작·paper 트레이드·live 트레이드·에러·safety block 모두 텔레그램 전송.
사용:
  paper 모드: python3 live_swing.py
  live 모드:  AUTO_TRADE=1 python3 live_swing.py --arm-live  (+ 두 marker 파일 필요)
"""
import os, sys, time, json, datetime, traceback
sys.path.insert(0, os.path.dirname(__file__) or ".")
import swing as SW
import telegram_notify as TG

SEALED_MARKER=os.path.join(os.path.dirname(__file__) or ".", ".sealed_spec_marker")
PROSPECTIVE_MARKER=os.path.join(os.path.dirname(__file__) or ".", ".prospective_pass_marker")
STATE_FILE=os.path.join(os.path.dirname(__file__) or ".", "swing_live_state.json")

class SafetyBlocked(Exception):
    """4중 게이트 실패 시 raise. 절대 무시하지 말 것."""
    pass

def check_live_gates(cli_arm_live):
    """4중 게이트 검사. 모두 통과 = live_ok=True. 하나라도 실패 = False + 사유."""
    gates=[]
    gates.append(("AUTO_TRADE=1", os.environ.get("AUTO_TRADE","0")=="1"))
    gates.append((f"sealed marker ({SEALED_MARKER})", os.path.exists(SEALED_MARKER)))
    gates.append((f"prospective marker ({PROSPECTIVE_MARKER})", os.path.exists(PROSPECTIVE_MARKER)))
    gates.append(("--arm-live CLI flag", cli_arm_live))
    failed=[name for name,ok in gates if not ok]
    return len(failed)==0, failed, gates

def load_state():
    if os.path.exists(STATE_FILE):
        try: return json.load(open(STATE_FILE))
        except Exception: return {}
    return {}

def save_state(s):
    tmp=STATE_FILE+".tmp"
    json.dump(s, open(tmp,"w"), indent=1, default=str)
    os.replace(tmp, STATE_FILE)

def place_upbit_order(market, side, volume=None, price=None):
    """실주문 함수. SafetyBlocked 예외로 하드락 (게이트 미통과 시 절대 실행 안 됨)."""
    raise SafetyBlocked(
        "live order path invoked but v1.7 protocol requires hash+robustness+prospective PASS. "
        "This function is stubbed until user explicitly arms live mode after all gates pass."
    )

def run_paper_iteration(state):
    """paper 모드 1 iteration: 신호 계산 → 로그·텔레그램 (실주문 X)."""
    # TODO: hash 후 최종 spec 확정되면 그 signal generator 로드
    #       현재는 skeleton으로 상태 카운터만 증가
    state["iterations"]=state.get("iterations",0)+1
    state["last_iter_utc"]=datetime.datetime.utcnow().isoformat()+"Z"
    return state

def main():
    cli_arm=("--arm-live" in sys.argv)
    live_ok, failed, all_gates = check_live_gates(cli_arm)
    mode = "LIVE" if live_ok else "PAPER"
    print(f"[swing] mode={mode}")
    for name,ok in all_gates:
        print(f"  gate: {name} = {'✓' if ok else '✗'}")
    if not live_ok:
        print(f"[swing] PAPER 모드 (실주문 금지) · 실패 gate: {failed}")
    TG.notify_startup(mode, extra=f"gates_failed={len(failed)}")
    state=load_state()
    try:
        # skeleton loop — 1회만 실행하고 종료 (systemd Timer로 반복 가정)
        state=run_paper_iteration(state)
        save_state(state)
        TG.send(f"iteration #{state['iterations']} 완료 (paper skeleton · 신호 로직 미탑재)")
        if live_ok:
            # 실주문 경로 = 절대 도달 안 함 (SafetyBlocked 로 하드락)
            try:
                place_upbit_order("KRW-BTC","buy",volume=0.0001)
            except SafetyBlocked as sb:
                TG.notify_safety_block(str(sb))
                print(f"[swing] SafetyBlocked (예상된 동작): {sb}")
    except Exception as e:
        TG.notify_error("main", e)
        traceback.print_exc()
        sys.exit(1)
    print(f"[swing] 정상 종료. state={state}")

if __name__=="__main__":
    main()
