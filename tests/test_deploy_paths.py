# -*- coding: utf-8 -*-
"""배포 워크플로(deploy.yml)가 수동 실행으로만 시작되는지 확인한다."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"


def workflow_events():
    """deploy.yml의 최상위 on 이벤트 이름을 읽는다."""
    lines = DEPLOY.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "on:")
    events = []
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break
        if indent == 2:
            name = line.strip().split(":", 1)[0]
            if re.fullmatch(r"[A-Za-z_]+", name):
                events.append(name)
    return events


class DeployWorkflow(unittest.TestCase):
    def test_manual_dispatch_is_the_only_trigger(self):
        self.assertEqual(workflow_events(), ["workflow_dispatch"])


if __name__ == "__main__":
    unittest.main()
