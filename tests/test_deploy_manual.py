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
        self.assertEqual(lines[run], 'git show "$TARGET:deploy_restart.sh" > "$RUNNER"')                                   # 승인한 커밋의 스크립트를 꺼내서
        self.assertEqual(lines[run + 1], 'DEPLOY_SHA="$SHA" DEPLOY_TARGET="$TARGET" DEPLOY_REEXEC=1 bash "$RUNNER"')        # 그 커밋만을 대상으로, 다시 실행 없이 돌린다
        self.assertFalse(any("bash ./deploy_restart.sh" in ln for ln in lines))                                             # 서버에 있던 옛 스크립트는 돌리지 않는다
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
        self.assertIn("sudo systemctl status upbit-bot --no-pager -n 0 || true", self.src)   # 꺼져 있는 봇의 상태 조회가 배포를 실패시키지 않는다

    def test_config_changes_restart_the_bot_and_a_failed_check_fails_the_deploy(self):
        self.assertIn("cat bot.py config.py _a_clean_purity.py", self.src)
        self.assertIn('elif [ "$OLD_FILES_HASH" != "$NEW_FILES_HASH" ]; then', self.src)
        self.assertIn("DEPLOY_FAILED=1", self.src)
        tail = self.src.rstrip().splitlines()[-4:]
        self.assertEqual(tail[0], 'if [ "$DEPLOY_FAILED" = "1" ]; then')
        self.assertEqual(tail[2].strip(), "exit 1")

    def test_service_log_lines_never_reach_the_public_deploy_log(self):
        """systemctl status 는 기본으로 서비스의 최근 로그 10줄을 같이 찍는다. 배포 기록은 공개라서 그 줄을 찍지 않게 한다."""
        lines = [ln for ln in self.src.splitlines() if "systemctl status" in ln and not ln.lstrip().startswith("#")]
        self.assertEqual(len(lines), 2)
        for ln in lines:
            self.assertIn("--no-pager -n 0", ln)
        for ln in self.src.splitlines():                                              # 로그를 직접 찍는 명령도 없다
            code = ln.split("#", 1)[0]
            self.assertFalse(code.lstrip().startswith(("journalctl", "sudo journalctl")), ln)

    def test_the_deploy_starts_no_research_and_never_reads_the_env_file(self):
        """고문 판정 2026-11-16 ③: 커밋 메시지의 [sweep] 으로 거래 서버에서 연구 스크립트를 띄우고 .env 를 통째로 읽던 블록을 지웠다."""
        code = "\n".join(code_lines(self.src))
        for gone in ("sweep_defense.py", "[sweep]", "source /home/ubuntu/bot/.env", "nohup", "AUTO_TRADE", "TELEGRAM_TOKEN"):
            self.assertNotIn(gone, code)
        self.assertNotRegex(code, r"(?m)^\s*(source|\.)\s+\S*\.env")
        self.assertNotIn(".env", code)

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
            # FAKE_COMMITS/<커밋>/ 에 파일을 두면 reset --hard 가 그 파일들로 바꾸고 show 가 그 파일을 돌려준다.
            # FAKE_MAIN_SEQ(파일)에 줄이 있으면 rev-parse origin/main 이 부를 때마다 한 줄씩 꺼내 쓴다(그 사이 main 이 바뀌는 것을 흉내).
            "git": "\n".join([
                'echo "git $*" >> "$CALLS"',
                'case "$1 $2" in',
                '  "rev-parse origin/main")',
                '    if [ -n "$FAKE_MAIN_SEQ" ] && [ -s "$FAKE_MAIN_SEQ" ]; then head -1 "$FAKE_MAIN_SEQ"; sed -i 1d "$FAKE_MAIN_SEQ"; else echo "$FAKE_MAIN"; fi ;;',
                '  "rev-parse --short=12") echo "${FAKE_MAIN:0:12}" ;;',
                '  "log --oneline") echo "abc1234 msg" ;;',
                '  "log -1") echo "msg" ;;',
                '  "reset --hard") if [ -n "$FAKE_COMMITS" ] && [ -d "$FAKE_COMMITS/$3" ]; then cp -f "$FAKE_COMMITS/$3"/* "$FAKE_ROOT"/; fi ;;',
                '  show\\ *) cat "$FAKE_COMMITS/${2%%:*}/${2#*:}" ;;',
                'esac',
                'exit 0']),
            "systemctl": 'echo "systemctl $*" >> "$CALLS"\ncase "$1" in\n  show) echo "$FAKE_PID" ;;\n  is-enabled) exit 1 ;;\n  status) [ "$FAKE_PID" = "0" ] && exit 3 ;;\nesac\nexit 0',
            "sudo": 'exec "$@"',
            "sleep": "exit 0",
        }
        for name, body in stubs.items():
            path = self.root / "bin" / name
            path.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
            path.chmod(0o755)

    def env(self, pid, main, **extra):
        env = dict(os.environ, PATH="%s%s%s" % (self.root / "bin", os.pathsep, os.environ["PATH"]), CALLS=str(self.root / "calls.log"),
                   FAKE_PID=str(pid), FAKE_MAIN=main, FAKE_ROOT=str(self.root), FAKE_COMMITS=str(self.root / "commits"))
        for name in ("DEPLOY_TARGET", "DEPLOY_REEXEC", "DEPLOY_STAGE", "DEPLOY_SHA", "DEPLOY_OLD_BOT_HASH", "DEPLOY_OLD_FILES_HASH", "FAKE_MAIN_SEQ"):
            env.pop(name, None)
        env.update(extra)
        (self.root / "calls.log").write_text("", encoding="utf-8")
        return env

    def run_script(self, pid, main="1111111aaaaaaa", sha="approved", **extra):
        """디스크의 스크립트를 직접 돌린다. sha 를 따로 주지 않으면 지금의 main 을 승인한 것으로 한다. None 이면 승인 번호 없이."""
        if sha is not None:
            extra["DEPLOY_SHA"] = main[:12] if sha == "approved" else sha
        env = self.env(pid, main, **extra)
        done = subprocess.run(["bash", str(self.root / "deploy_restart.sh")], env=env, capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout + done.stderr, (self.root / "calls.log").read_text(encoding="utf-8")

    def commit(self, sha, script=None, **files):
        """가짜 커밋 하나: 그 커밋의 deploy_restart.sh(기본은 지금 저장소의 것)와 봇 파일들."""
        folder = self.root / "commits" / sha
        folder.mkdir(parents=True)
        src = script if script is not None else (ROOT / "deploy_restart.sh").read_text(encoding="utf-8")
        (folder / "deploy_restart.sh").write_text(src.replace("/home/ubuntu/bot", str(self.root)), encoding="utf-8")
        for name in ("bot.py", "config.py", "_a_clean_purity.py"):
            (folder / name).write_text(files.get(name.replace(".py", ""), "# %s\n" % name), encoding="utf-8")

    def old_server_script(self):
        """지금 서버(3154839)에 있는 스크립트를 디스크에 둔다. 돌았는지 알 수 있게 첫 줄에 표시를 남기게 한다."""
        old = (ROOT / "tests" / "fixtures" / "deploy_restart_server_3154839.sh").read_text(encoding="utf-8")
        old = old.replace("set -e\n", 'set -e\necho "OLD_SCRIPT_RAN" >> "$CALLS"\n', 1).replace("/home/ubuntu/bot", str(self.root))
        (self.root / "deploy_restart.sh").write_text(old, encoding="utf-8")

    def run_workflow(self, action, sha, pid, main, seq=None):
        """deploy.yml 이 ssh 로 서버에서 돌리는 줄들을 그대로 돌린다."""
        script = "\n".join(script_lines(text())).replace("/home/ubuntu/bot", str(self.root))
        extra = {"ACTION": action, "SHA": sha}
        if seq:
            (self.root / "main_seq").write_text("\n".join(seq) + "\n", encoding="utf-8")
            extra["FAKE_MAIN_SEQ"] = str(self.root / "main_seq")
        done = subprocess.run(["bash", "-c", script], env=self.env(pid, main, **extra), capture_output=True, text=True, timeout=60,
                              cwd=str(self.root))
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
        self.assertIn("[deploy] 완료", out)                                       # 나머지 단계는 마저 돈 뒤에 실패로 끝난다

    def test_a_moved_main_stops_the_deploy_before_anything_changes(self):
        code, out, calls = self.run_script(pid=4321, main="2222222bbbbbbb", sha="1111111")
        self.assertEqual(code, 3, out)
        self.assertIn("main 이 바뀌었다", out)
        self.assertNotIn("git reset", calls)
        self.assertNotIn("systemctl restart", calls)
        code, out, calls = self.run_script(pid=0, main="1111111aaaaaaa", sha="1111111")        # 같은 커밋이면 진행한다
        self.assertEqual(code, 0, out)
        self.assertIn("git reset --hard 1111111aaaaaaa", calls)

    # ---- 2026-11-16 제3세션 검토: 서버의 옛 스크립트에서 시작하는 첫 배포, 스크립트와 설정이 함께 바뀐 배포
    X, Y = "1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "2222222bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    def test_the_first_deploy_runs_the_approved_script_and_never_the_old_one(self):
        self.old_server_script()
        self.commit(self.X)
        code, out, calls = self.run_workflow("deploy", self.X[:12], pid=0, main=self.X)
        self.assertEqual(code, 0, out)
        self.assertNotIn("OLD_SCRIPT_RAN", calls)                               # 서버에 있던 옛 스크립트는 돌지 않았다
        self.assertIn("git reset --hard %s" % self.X, calls)                    # 승인한 커밋으로 바뀐다
        self.assertNotIn("reset --hard origin/main", calls)
        self.assertEqual(out.count("[deploy] 시작"), 1)                         # 승인한 스크립트가 한 번만 돈다(다시 실행하지 않는다)
        self.assertIn("upbit-bot 이 실행 중이 아니다 → 건드리지 않는다", out)      # 첫 배포부터 '꺼 둔 봇은 켜지 않는다' 가 적용된다
        self.assertNotIn("systemctl restart", calls)

    def test_main_moving_during_the_first_deploy_changes_no_file_and_ends_with_code_3(self):
        """고문 판정 2026-11-16 ②: 디스크에 옛 스크립트(3154839)가 있는 상태에서 시작해 도중에 main 이 바뀜 → 파일 변경 0, 종료 코드 3."""
        self.old_server_script()
        self.commit(self.X)
        self.commit(self.Y, bot="# unreviewed bot.py\n")
        before = {name: (self.root / name).read_text(encoding="utf-8") for name in ("deploy_restart.sh", "bot.py", "config.py")}
        code, out, calls = self.run_workflow("deploy", self.X[:12], pid=4321, main=self.Y, seq=[self.X])    # 워크플로가 확인한 직후 main 이 Y 로 바뀐다
        self.assertEqual(code, 3, out)
        self.assertIn("main 이 바뀌었다", out)
        self.assertNotIn("git reset", calls)
        self.assertNotIn("OLD_SCRIPT_RAN", calls)
        self.assertNotIn("systemctl restart", calls)
        self.assertEqual({name: (self.root / name).read_text(encoding="utf-8") for name in before}, before)   # 파일 변경 0

    def test_a_deploy_asked_for_after_main_moved_changes_nothing(self):
        self.old_server_script()
        self.commit(self.X)
        self.commit(self.Y)
        before = (self.root / "deploy_restart.sh").read_text(encoding="utf-8")
        code, out, calls = self.run_workflow("deploy", self.X[:12], pid=0, main=self.Y)
        self.assertEqual(code, 3, out)
        self.assertNotIn("git reset", calls)
        self.assertNotIn("OLD_SCRIPT_RAN", calls)
        self.assertEqual((self.root / "deploy_restart.sh").read_text(encoding="utf-8"), before)
        code, out, calls = self.run_workflow("check", "", pid=0, main=self.X)           # check 는 아무것도 바꾸지 않는다
        self.assertEqual(code, 0, out)
        self.assertIn("배포할 커밋 번호 (deploy 의 sha 칸에 넣는다): %s" % self.X[:12], out)
        self.assertNotIn("git reset", calls)

    def test_the_script_itself_refuses_a_target_that_is_not_the_approved_commit(self):
        self.commit(self.Y)
        for extra in (dict(DEPLOY_TARGET=self.Y), dict(DEPLOY_TARGET=self.Y, DEPLOY_REEXEC="1")):     # 대상이 어디서 왔든 승인 번호와 대조한다
            code, out, calls = self.run_script(pid=0, main=self.Y, sha=self.X[:12], **extra)
            self.assertEqual(code, 3, out)
            self.assertNotIn("git reset", calls)

    def test_without_an_approved_commit_number_nothing_is_changed(self):
        self.commit(self.X)
        for sha in (None, "", "not-hex", "abc12"):                              # 없거나, 이상하거나, 7자보다 짧다
            code, out, calls = self.run_script(pid=4321, main=self.X, sha=sha)
            self.assertEqual(code, 2, out)
            self.assertNotIn("git reset", calls)
            self.assertNotIn("git fetch", calls)
            self.assertNotIn("systemctl restart", calls)

    def test_the_first_deploy_restarts_a_running_bot_when_only_the_config_changed(self):
        self.old_server_script()
        self.commit(self.X, config="# config.py changed\n")
        self.marker(4321)                                                       # 봇은 지금의 bot.py 로 돌고 있다 (bot.py 는 안 바뀐다)
        code, out, calls = self.run_workflow("deploy", self.X[:12], pid=4321, main=self.X)
        self.assertEqual(code, 0, out)
        self.assertIn("봇 재시작: config.py or _a_clean_purity.py changed during this run", out)
        self.assertIn("systemctl restart upbit-bot", calls)

    def test_script_and_config_changed_together_still_restart_the_bot(self):
        """스크립트가 새 버전으로 다시 실행될 때 '변경 전' 해시를 잃어, 설정이 바뀌었는데도 '재시작 불필요' 로 끝났다."""
        newer = (ROOT / "deploy_restart.sh").read_text(encoding="utf-8") + "\n# a later version of this script\n"
        self.commit(self.X, script=newer, config="# config.py changed\n")
        self.marker(4321)
        code, out, calls = self.run_script(pid=4321, main=self.X)
        self.assertEqual(code, 0, out)
        self.assertIn("deploy_restart.sh 자체가 갱신됨 → 새 버전으로 재실행", out)   # 디스크의 스크립트를 직접 돌린 경우: 새 버전으로 넘어가고
        self.assertIn("봇 재시작: config.py or _a_clean_purity.py changed during this run", out)   # 설정이 바뀐 것을 잊지 않는다
        self.assertIn("systemctl restart upbit-bot", calls)
        self.assertEqual(calls.count("systemctl restart upbit-bot"), 1)

    def test_a_leftover_hash_in_the_environment_is_ignored_on_a_normal_run(self):
        self.commit(self.X, config="# config.py changed\n")
        self.marker(4321)
        stale = hashlib.md5(b"# bot.py\n# config.py changed\n# _a_clean_purity.py\n").hexdigest()   # '바뀐 뒤' 의 해시가 환경에 남아 있다
        same = hashlib.md5(b"# bot.py\n").hexdigest()
        for extra in ({"DEPLOY_TARGET": self.X}, {"DEPLOY_TARGET": self.X, "DEPLOY_REEXEC": "1"}):   # 워크플로가 부르는 방식 그대로
            (self.root / "config.py").write_text("# config.py\n", encoding="utf-8")
            code, out, calls = self.run_script(pid=4321, main=self.X, DEPLOY_OLD_FILES_HASH=stale, DEPLOY_OLD_BOT_HASH=same, **extra)
            self.assertIn("systemctl restart upbit-bot", calls, out)             # 최초 실행은 자기가 잰 값을 쓴다


if __name__ == "__main__":
    unittest.main()
