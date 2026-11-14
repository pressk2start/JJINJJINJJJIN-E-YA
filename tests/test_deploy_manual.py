# -*- coding: utf-8 -*-
"""배포 워크플로(deploy.yml)는 사람이 직접 실행할 때만 돈다 — merge·푸시·예약으로는 서버에 접속하지 않는다.

2026-11-12 고문 결정: "서버는 배포 버튼을 누를 때만 바뀐다". 기본 동작(check)은 서버를 바꾸지 않고 차이만 보여 주고,
deploy 를 골랐을 때만 deploy_restart.sh 가 돈다. 이 파일은 그 약속이 조용히 되돌려지지 않게 고정한다(표준 라이브러리만)."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
DEPLOY = WF / "deploy.yml"
GATE = 'if [ "$ACTION" != "deploy" ]; then'


def text():
    return DEPLOY.read_text(encoding="utf-8")


def code_lines(src):
    """주석과 빈 줄을 뺀 줄."""
    return [ln for ln in src.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def triggers(src):
    """최상위 on: 바로 아래(두 칸 들여쓴) 키 목록."""
    lines = code_lines(src)
    start = lines.index("on:")
    out = []
    for ln in lines[start + 1:]:
        if not ln.startswith(" "):
            break
        m = re.match(r"^  ([A-Za-z_]+):", ln)
        if m:
            out.append(m.group(1))
    return out


def script_lines(src):
    """ssh 로 서버에서 실행되는 줄들 (script: | 아래)."""
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "script: |")
    indent = len(lines[start]) - len(lines[start].lstrip())
    out = []
    for ln in lines[start + 1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) <= indent:
            break
        out.append(ln.strip())
    return [ln for ln in out if ln]


class ManualOnly(unittest.TestCase):
    def test_the_only_trigger_is_the_button(self):
        self.assertEqual(triggers(text()), ["workflow_dispatch"])
        for word in ("push:", "pull_request", "schedule:", "workflow_run", "repository_dispatch", "workflow_call"):
            self.assertFalse(any(word in ln for ln in code_lines(text())), word)

    def test_the_default_choice_does_not_touch_the_server(self):
        src = text()
        self.assertRegex(src, r"(?m)^        type: choice$")
        self.assertRegex(src, r"(?m)^        default: check$")
        options = re.search(r"(?m)^        options:\n((?:          - \w+\n)+)", src).group(1).split()
        self.assertEqual([o for o in options if o != "-"], ["check", "deploy"])

    def test_it_refuses_to_run_from_another_branch(self):
        self.assertIn("if: github.ref != 'refs/heads/main'", text())

    def test_the_choice_reaches_the_server_as_a_variable_not_as_script_text(self):
        src = text()
        self.assertIn("ACTION: ${{ inputs.action }}", src)
        self.assertRegex(src, r"(?m)^          envs: ACTION,SHA$")
        self.assertFalse(any("${{" in ln for ln in script_lines(src)))       # 입력값을 명령 문자열에 끼워 넣지 않는다

    def test_deploy_runs_only_after_the_gate(self):
        lines = script_lines(text())
        gate = lines.index(GATE)
        end = lines.index("fi", gate)
        self.assertEqual(lines[end - 1], "exit 0")                           # deploy 가 아니면 여기서 끝난다
        run = [i for i, ln in enumerate(lines) if "deploy_restart.sh" in ln]
        self.assertEqual(len(run), 1)
        self.assertGreater(run[0], end)

    def test_deploy_needs_the_commit_that_check_showed_and_stops_if_main_moved(self):
        """check 에서 본 커밋과 실제로 올라가는 커밋이 같아야 한다 (제3세션 지적 2026-11-14)."""
        src, lines = text(), script_lines(text())
        self.assertIn("SHA: ${{ inputs.sha }}", src)
        self.assertRegex(src, r"(?m)^          envs: ACTION,SHA$")
        gate_end = lines.index("fi", lines.index(GATE))
        run = next(i for i, ln in enumerate(lines) if "deploy_restart.sh" in ln)
        between = lines[gate_end + 1:run]
        self.assertIn('""|*[!0-9a-f]*) echo "deploy 에는 check 가 찍어 준 커밋 번호(sha)가 필요하다"; exit 2 ;;', between)   # 빈 값·이상한 값은 거부
        self.assertTrue(any(ln.startswith('*) echo "main 이 바뀌었다') and ln.endswith("exit 3 ;;") for ln in between))    # main 이 바뀌었으면 멈춘다
        self.assertEqual(lines[run], 'DEPLOY_SHA="$SHA" bash ./deploy_restart.sh')                                          # 스크립트에도 같은 번호를 넘긴다
        check_part = lines[lines.index(GATE):gate_end]
        self.assertTrue(any("git rev-parse --short=12 origin/main" in ln for ln in check_part))                             # check 가 번호를 찍어 준다


class DeployScript(unittest.TestCase):
    """deploy_restart.sh — 2026-11-14 전체 검토 반영분. 서버에서만 도는 스크립트라 글 내용으로 고정한다."""

    def setUp(self):
        self.src = (ROOT / "deploy_restart.sh").read_text(encoding="utf-8")

    def test_the_approved_commit_is_checked_before_the_code_is_changed(self):
        pin = self.src.index('"$DEPLOY_SHA"*) ;;')
        self.assertLess(pin, self.src.index('git reset --hard "$DEPLOY_TARGET"'))
        self.assertIn("exit 3 ;;", self.src[pin:pin + 300])
        self.assertNotIn("git reset --hard origin/main", self.src)                 # 움직이는 main 이 아니라 정해 둔 커밋으로 맞춘다
        self.assertIn("export DEPLOY_TARGET", self.src)                            # 재실행돼도 같은 커밋을 쓴다

    def test_a_stopped_bot_is_never_started(self):
        i = self.src.index('if [ -z "$BOT_PID_NOW" ] || [ "$BOT_PID_NOW" = "0" ]; then')
        branch = self.src[i:self.src.index("elif", i)]
        self.assertIn("건드리지 않는다", branch)
        self.assertNotIn("NEED_RESTART=1", branch)
        self.assertNotIn("systemctl", branch.replace("sudo systemctl start upbit-bot", ""))   # 안내 문구 말고는 systemctl 을 부르지 않는다
        self.assertEqual(self.src.count("sudo systemctl restart upbit-bot"), 1)
        restart = self.src.index("sudo systemctl restart upbit-bot")
        self.assertLess(self.src.rindex('if [ "$NEED_RESTART" = "1" ]; then', 0, restart), restart)
        self.assertIn("sudo systemctl status upbit-bot --no-pager || true", self.src)   # 꺼져 있는 봇의 상태 조회가 배포를 실패시키지 않는다

    def test_config_changes_restart_the_bot_and_a_failed_check_fails_the_deploy(self):
        self.assertIn("cat bot.py config.py _a_clean_purity.py", self.src)
        self.assertIn('elif [ "$OLD_FILES_HASH" != "$NEW_FILES_HASH" ]; then', self.src)
        self.assertIn("DEPLOY_FAILED=1", self.src)
        tail = self.src.rstrip().splitlines()[-4:]
        self.assertEqual(tail[0], 'if [ "$DEPLOY_FAILED" = "1" ]; then')
        self.assertEqual(tail[2].strip(), "exit 1")

    def test_scanners_and_units_are_still_left_alone(self):
        self.assertIn('if systemctl is-enabled "$svc" &>/dev/null; then', self.src)  # 꺼 둔 스캐너는 건드리지 않는다
        code = "\n".join(ln.split("#", 1)[0] for ln in self.src.splitlines())                # 주석을 뺀 실행 줄만
        for word in ("systemctl enable", "systemctl start", "h3-executor", "h3-sell-retry", "crontab"):
            self.assertNotIn(word, code.replace('echo "[deploy] upbit-bot 이 실행 중이 아니다 → 건드리지 않는다. 켜려면 사람이 직접: sudo systemctl start upbit-bot"', ""), word)

    def test_everything_before_the_gate_only_reads(self):
        """check 에서 실행되는 줄: 서버 코드·프로세스를 바꾸는 명령이 없어야 한다 (git fetch 는 원격 추적 정보만 갱신한다)."""
        lines = script_lines(text())
        before = lines[:lines.index(GATE)]
        banned = re.compile(r"\b(reset|checkout|pull|merge|rebase|stash|clean|restart|stop|start|enable|disable|kill|rm|mv|cp|sudo|tee|pip)\b|>")
        for ln in before:
            if ln.startswith("echo "):
                continue
            if ln.startswith("python3 trend/bot_version_check.py "):
                ln = ln.split(" || echo ")[0]                                # 뒤의 안내 문구는 본다/안 본다와 무관
                self.assertNotRegex(ln.replace("2>/dev/null", ""), banned, ln)
                continue
            self.assertNotRegex(ln, banned, ln)
        self.assertIn("git fetch origin main -q", before)

    def test_the_version_check_it_calls_only_reads(self):
        src = (ROOT / "trend" / "bot_version_check.py").read_text(encoding="utf-8")
        for word in ("subprocess", "os.remove", "os.system", "shutil", '"w"', "'w'", '"a"', "'a'", ".write("):
            self.assertNotIn(word, src, word)


class NothingElseReachesTheServer(unittest.TestCase):
    def test_no_other_workflow_uses_the_server_key(self):
        for path in sorted(WF.glob("*.yml")):
            if path.name == "deploy.yml":
                continue
            body = "\n".join(code_lines(path.read_text(encoding="utf-8")))
            for word in ("LIGHTSAIL", "ssh-action", "bash ./deploy_restart", "auto_deploy"):       # 경로 필터에 파일 이름이 나오는 것은 괜찮다
                self.assertNotIn(word, body, "%s: %s" % (path.name, word))

    def test_scheduled_workflows_commit_only_the_forward_folder(self):
        """Forward watch(매주)와 discover(매월)가 main 에 올리는 것은 scalp/research/forward 아래뿐이다."""
        for name in ("forward-watch.yml", "discover.yml"):
            adds = [ln.strip() for ln in code_lines((WF / name).read_text(encoding="utf-8")) if "git add" in ln]
            self.assertTrue(adds, name)
            for ln in adds:
                self.assertIn("git add scalp/research/forward", ln, name)


@unittest.skipUnless(shutil.which("bash"), "bash 가 있어야 한다")
class DeployScriptRuns(unittest.TestCase):
    """deploy_restart.sh 를 가짜 git·systemctl 로 실제로 돌려 본다 (서버 경로만 임시 폴더로 바꿔서)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "trend").mkdir()
        (self.root / "data").mkdir()
        (self.root / "bin").mkdir()
        shutil.copy(str(ROOT / "trend" / "bot_version_check.py"), str(self.root / "trend" / "bot_version_check.py"))
        for name in ("bot.py", "config.py", "_a_clean_purity.py"):
            (self.root / name).write_text("# %s\n" % name, encoding="utf-8")
        script = (ROOT / "deploy_restart.sh").read_text(encoding="utf-8").replace("/home/ubuntu/bot", str(self.root))
        (self.root / "deploy_restart.sh").write_text(script, encoding="utf-8")
        stubs = {
            "git": 'echo "git $*" >> "$CALLS"\ncase "$1 $2" in\n  "rev-parse origin/main") echo "$FAKE_MAIN" ;;\n  "log --oneline") echo "abc1234 msg" ;;\n  "log -1") echo "msg" ;;\nesac\nexit 0',
            "systemctl": 'echo "systemctl $*" >> "$CALLS"\ncase "$1" in\n  show) echo "$FAKE_PID" ;;\n  is-enabled) exit 1 ;;\n  status) [ "$FAKE_PID" = "0" ] && exit 3 ;;\nesac\nexit 0',
            "sudo": 'exec "$@"',
            "sleep": "exit 0",
        }
        for name, body in stubs.items():
            path = self.root / "bin" / name
            path.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
            path.chmod(0o755)

    def run_script(self, pid, main="1111111aaaaaaa", sha=None):
        env = dict(os.environ, PATH="%s%s%s" % (self.root / "bin", os.pathsep, os.environ["PATH"]), CALLS=str(self.root / "calls.log"),
                   FAKE_PID=str(pid), FAKE_MAIN=main)
        env.pop("DEPLOY_TARGET", None)
        env.pop("DEPLOY_REEXEC", None)
        if sha is not None:
            env["DEPLOY_SHA"] = sha
        (self.root / "calls.log").write_text("", encoding="utf-8")
        done = subprocess.run(["bash", str(self.root / "deploy_restart.sh")], env=env, capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout + done.stderr, (self.root / "calls.log").read_text(encoding="utf-8")

    def marker(self, pid):
        md5 = hashlib.md5((self.root / "bot.py").read_bytes()).hexdigest()
        (self.root / "data" / "h3_reserved.json").write_text(json.dumps({"pid": pid, "bot_md5": md5, "order_block": True}), encoding="utf-8")

    def test_a_stopped_bot_stays_stopped_and_the_deploy_still_succeeds(self):
        code, out, calls = self.run_script(pid=0)
        self.assertEqual(code, 0, out)
        self.assertIn("upbit-bot 이 실행 중이 아니다 → 건드리지 않는다", out)
        self.assertNotIn("systemctl restart", calls)
        self.assertNotIn("systemctl start", calls)
        self.assertIn("git reset --hard 1111111aaaaaaa", calls)                 # 코드는 맞춰 둔다

    def test_a_running_bot_on_the_same_code_is_left_alone(self):
        self.marker(4321)
        code, out, calls = self.run_script(pid=4321)
        self.assertEqual(code, 0, out)
        self.assertIn("재시작 불필요", out)
        self.assertNotIn("systemctl restart", calls)

    def test_a_restart_that_cannot_be_verified_ends_in_failure(self):
        code, out, calls = self.run_script(pid=4321)                           # 마커가 없다 → 재시작하지만 새 코드로 떴는지 확인되지 않는다
        self.assertIn("systemctl restart upbit-bot", calls)
        self.assertEqual(code, 1, out)
        self.assertIn("이 배포는 실패로 끝난다", out)
        self.assertIn("sweep 스킵", out)                                         # 나머지 단계는 마저 돈 뒤에 실패로 끝난다

    def test_a_moved_main_stops_the_deploy_before_anything_changes(self):
        code, out, calls = self.run_script(pid=4321, main="2222222bbbbbbb", sha="1111111")
        self.assertEqual(code, 3, out)
        self.assertIn("main 이 바뀌었다", out)
        self.assertNotIn("git reset", calls)
        self.assertNotIn("systemctl restart", calls)
        code, out, calls = self.run_script(pid=0, main="1111111aaaaaaa", sha="1111111")        # 같은 커밋이면 진행한다
        self.assertEqual(code, 0, out)
        self.assertIn("git reset --hard 1111111aaaaaaa", calls)


if __name__ == "__main__":
    unittest.main()
