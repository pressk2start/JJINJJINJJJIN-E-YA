# -*- coding: utf-8 -*-
"""배포 워크플로(deploy.yml)는 사람이 직접 실행할 때만 돈다 — merge·푸시·예약으로는 서버에 접속하지 않는다.

2026-11-12 고문 결정: "서버는 배포 버튼을 누를 때만 바뀐다". 기본 동작(check)은 서버를 바꾸지 않고 차이만 보여 주고,
deploy 를 골랐을 때만 deploy_restart.sh 가 돈다. 이 파일은 그 약속이 조용히 되돌려지지 않게 고정한다(표준 라이브러리만)."""
import re
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
        self.assertRegex(src, r"(?m)^          envs: ACTION$")
        self.assertFalse(any("${{" in ln for ln in script_lines(src)))       # 입력값을 명령 문자열에 끼워 넣지 않는다

    def test_deploy_runs_only_after_the_gate(self):
        lines = script_lines(text())
        gate = lines.index(GATE)
        self.assertEqual(lines[gate + 2], "exit 0")                          # deploy 가 아니면 여기서 끝난다
        self.assertEqual(lines[gate + 3], "fi")
        run = [i for i, ln in enumerate(lines) if "deploy_restart.sh" in ln]
        self.assertEqual(len(run), 1)
        self.assertGreater(run[0], gate + 3)

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
            for word in ("LIGHTSAIL", "ssh-action", "deploy_restart", "auto_deploy"):
                self.assertNotIn(word, body, "%s: %s" % (path.name, word))

    def test_scheduled_workflows_commit_only_the_forward_folder(self):
        """Forward watch(매주)와 discover(매월)가 main 에 올리는 것은 scalp/research/forward 아래뿐이다."""
        for name in ("forward-watch.yml", "discover.yml"):
            adds = [ln.strip() for ln in code_lines((WF / name).read_text(encoding="utf-8")) if "git add" in ln]
            self.assertTrue(adds, name)
            for ln in adds:
                self.assertIn("git add scalp/research/forward", ln, name)


if __name__ == "__main__":
    unittest.main()
