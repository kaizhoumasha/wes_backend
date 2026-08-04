"""HEAVY selector 的 Git 路径解析回归测试。"""

import subprocess
from pathlib import Path
from unittest.mock import Mock

from scripts.select_heavy_tests import get_changed_files


# Regression: ISSUE-001 — Git 对中文路径的 C 风格引号转义导致 HEAVY selector fail-closed
# Found by /qa on 2026-08-02
def test_get_changed_files_decodes_git_quoted_utf8_path(tmp_path: Path) -> None:
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout='"assets/\\344\\270\\255\\346\\226\\207.bin"\n',
            stderr="",
        )
    )

    changed_files = get_changed_files(scope=None, base="develop", repo_root=tmp_path, runner=runner)

    assert changed_files == ["assets/中文.bin"]
