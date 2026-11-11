# -*- coding: utf-8 -*-
"""배포 워크플로(deploy.yml)의 경로 조건 — 연구 전용 변경은 서버에 닿지 않고, 서버가 쓰는 파일은 반드시 배포되는지.

GitHub 의 paths 규칙을 그대로 흉내 낸다: 위에서 아래로 보고 마지막에 맞은 줄이 이긴다('!' 로 시작하면 제외).
바뀐 파일 중 하나라도 포함되면 워크플로가 돈다. '**' 는 '/' 를 포함한 아무 글자, '*' 는 '/' 를 뺀 아무 글자."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"


def deploy_paths():
    """deploy.yml 의 on.push.paths 목록 (표준 라이브러리만으로 읽는다)."""
    lines = DEPLOY.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "paths:")
    indent = len(lines[start]) - len(lines[start].lstrip())
    out = []
    for ln in lines[start + 1:]:
        if not ln.strip() or ln.strip().startswith("#"):
            continue
        if len(ln) - len(ln.lstrip()) <= indent:
            break
        m = re.match(r'\s*-\s*"([^"]+)"', ln)
        if m:
            out.append(m.group(1))
    return out


def _regex(pattern):
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile("^" + out + "$")


def included(path, patterns):
    hit = False
    for p in patterns:
        neg = p.startswith("!")
        if _regex(p[1:] if neg else p).match(path):
            hit = not neg
    return hit


def triggers(changed, patterns):
    return any(included(f, patterns) for f in changed)


class DeployPaths(unittest.TestCase):
    def setUp(self):
        self.patterns = deploy_paths()

    def test_the_list_starts_from_everything(self):
        self.assertEqual(self.patterns[0], "**")                 # 목록에 없는 경로는 배포 대상
        self.assertTrue(included("some_new_file_nobody_listed.py", self.patterns))

    def test_files_the_server_runs_always_deploy(self):
        for f in ("bot.py", "config.py", "_a_clean_purity.py", "deploy_restart.sh", "upbit-bot.service",
                  "trend/h3_executor.py", "trend/h3-executor.timer", "trend/bot_version_check.py",
                  "scalp/research/trend_paper.py", "research/sweep_defense.py", "bots/momentum_scanner.py", "tools/order_smoke.py"):
            self.assertTrue(included(f, self.patterns), f)

    def test_research_only_files_do_not_deploy(self):
        for f in ("scalp/research/forward/ledger.json", "scalp/research/forward/STATUS.md", "scalp/research/forward_watch.py",
                  "scalp/research/auto_monitor.py", "scalp/research/shadow_salvage.py", "scalp/research/reports/x.md",
                  "tests/test_forward_watch.py", "docs/CLM_DESIGN.md", "README.md", "ARCHIVE.md",
                  ".github/workflows/forward-watch.yml", ".github/workflows/deploy.yml", ".gitignore", "scalp/research/.gitignore"):
            self.assertFalse(included(f, self.patterns), f)

    def test_a_mixed_change_still_deploys(self):
        self.assertTrue(triggers(["scalp/research/forward_watch.py", "bot.py"], self.patterns))
        self.assertFalse(triggers(["scalp/research/forward_watch.py", "tests/test_forward_watch.py", "README.md"], self.patterns))

    def test_the_weekly_tracker_commit_never_reaches_the_server(self):
        """Forward watch 가 매주 올리는 것은 forward/ 아래 두 파일뿐이다."""
        wf = (ROOT / ".github" / "workflows" / "forward-watch.yml").read_text(encoding="utf-8")
        self.assertIn("git add scalp/research/forward", wf)
        self.assertFalse(triggers(["scalp/research/forward/ledger.json", "scalp/research/forward/STATUS.md"], self.patterns))

    def test_every_research_file_the_executor_loads_is_deployed(self):
        """H3 실행기가 scalp/research 에서 불러 쓰는 파일이 늘면 deploy.yml 에도 넣어야 한다."""
        src = (ROOT / "trend" / "h3_executor.py").read_text(encoding="utf-8")
        loaded = re.findall(r'"scalp"\s*/\s*"research"\s*/\s*"([\w.]+\.py)"', src)
        self.assertEqual(loaded, ["trend_paper.py"])
        for name in loaded:
            self.assertTrue(included("scalp/research/" + name, self.patterns), name)


if __name__ == "__main__":
    unittest.main()
