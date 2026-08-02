"""QA ISSUE-005：已删除的 HEAVY 测试不得成为不可执行的 selector 输出。"""

import subprocess
from unittest.mock import Mock

from scripts.select_heavy_tests import main


def test_main_skips_deleted_direct_heavy_test(tmp_path, capsys) -> None:
    """Git diff 保留删除记录，但 selector 只输出当前树中仍可执行的 HEAVY 测试。"""

    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text('ignore_globs = ["docs/**"]\n', encoding="utf-8")
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout="tests/integration/test_deleted_plugin_flow.py\n",
            stderr="",
        )
    )

    exit_code = main(
        ["--base", "develop"],
        repo_root=tmp_path,
        mapping_path=mapping_path,
        runner=runner,
    )

    assert exit_code == 0
    assert capsys.readouterr().out == ""
