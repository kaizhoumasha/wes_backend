"""QA ISSUE-005：已删除的 HEAVY 测试不得成为不可执行的 selector 输出。"""

import subprocess
from unittest.mock import Mock

import pytest

from scripts.select_heavy_tests import main

RETIRED_BOOTSTRAP_PATHS = (
    "scripts/data/bootstrap_admin.py",
    "scripts/data/bootstrap_admin.sh",
)


def test_main_skips_deleted_direct_heavy_test(tmp_path, capsys) -> None:
    """Git diff 保留删除记录，但 selector 只输出当前树中仍可执行的 HEAVY 测试。"""

    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text('ignore_globs = [".github/**"]\n', encoding="utf-8")
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


def test_main_rejects_deleted_heavy_test_still_referenced_by_mapping(tmp_path, capsys) -> None:
    """mapping 输出已失效时必须 fail closed，不能因 direct 删除过滤而静默通过。"""

    deleted_heavy_test = "tests/integration/test_deleted_core_flow.py"
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text(
        "\n".join(
            [
                'ignore_globs = [".github/**"]',
                "",
                "[[mapping]]",
                'source_glob = "src/runtime.py"',
                f'heavy_tests = ["{deleted_heavy_test}"]',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout=f"{deleted_heavy_test}\n",
            stderr="",
        )
    )

    exit_code = main(
        ["--base", "develop"],
        repo_root=tmp_path,
        mapping_path=mapping_path,
        runner=runner,
    )

    assert exit_code == 2
    assert "mapping 引用不存在的 HEAVY 测试" in capsys.readouterr().err


def test_main_skips_deleted_assets_from_retired_project_archive(tmp_path, capsys) -> None:
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text('ignore_globs = [".github/**"]\n', encoding="utf-8")
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout="docs/archive/legacy-sample/plugin.py\n",
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


def test_main_rejects_reintroduced_executable_in_retired_project_archive(tmp_path, capsys) -> None:
    archived_code = tmp_path / "docs/archive/legacy-sample/plugin.py"
    archived_code.parent.mkdir(parents=True)
    archived_code.write_text("VALUE = 1\n", encoding="utf-8")
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text('ignore_globs = [".github/**"]\n', encoding="utf-8")
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout="docs/archive/legacy-sample/plugin.py\n",
            stderr="",
        )
    )

    exit_code = main(
        ["--base", "develop"],
        repo_root=tmp_path,
        mapping_path=mapping_path,
        runner=runner,
    )

    assert exit_code == 2
    assert "未分类改动路径" in capsys.readouterr().err


def test_main_skips_deleted_retired_root_jenkinsfile(tmp_path, capsys) -> None:
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text('ignore_globs = [".github/**"]\n', encoding="utf-8")
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout="Jenkinsfile\n",
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


def test_main_rejects_reintroduced_root_jenkinsfile(tmp_path, capsys) -> None:
    (tmp_path / "Jenkinsfile").write_text("pipeline {}\n", encoding="utf-8")
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text('ignore_globs = [".github/**"]\n', encoding="utf-8")
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout="Jenkinsfile\n",
            stderr="",
        )
    )

    exit_code = main(
        ["--base", "develop"],
        repo_root=tmp_path,
        mapping_path=mapping_path,
        runner=runner,
    )

    assert exit_code == 2
    assert "未分类改动路径" in capsys.readouterr().err


def test_main_skips_deleted_static_production_base_data_sql(tmp_path, capsys) -> None:
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text('ignore_globs = [".github/**"]\n', encoding="utf-8")
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout="scripts/data/init_production_base_data.sql\n",
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


def test_main_rejects_reintroduced_static_production_base_data_sql(tmp_path, capsys) -> None:
    retired_sql = tmp_path / "scripts/data/init_production_base_data.sql"
    retired_sql.parent.mkdir(parents=True)
    retired_sql.write_text("SELECT 1;\n", encoding="utf-8")
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text('ignore_globs = [".github/**"]\n', encoding="utf-8")
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout="scripts/data/init_production_base_data.sql\n",
            stderr="",
        )
    )

    exit_code = main(
        ["--base", "develop"],
        repo_root=tmp_path,
        mapping_path=mapping_path,
        runner=runner,
    )

    assert exit_code == 2
    assert "候选路径未配置 mapping/NONE" in capsys.readouterr().err


@pytest.mark.parametrize("retired_path", RETIRED_BOOTSTRAP_PATHS)
def test_main_skips_deleted_retired_bootstrap_path(retired_path, tmp_path, capsys) -> None:
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text('ignore_globs = [".github/**"]\n', encoding="utf-8")
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout=f"{retired_path}\n",
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


@pytest.mark.parametrize("retired_path", RETIRED_BOOTSTRAP_PATHS)
def test_main_rejects_reintroduced_retired_bootstrap_path(retired_path, tmp_path, capsys) -> None:
    bootstrap_path = tmp_path / retired_path
    bootstrap_path.parent.mkdir(parents=True)
    bootstrap_path.write_text("# retired\n", encoding="utf-8")
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text('ignore_globs = [".github/**"]\n', encoding="utf-8")
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout=f"{retired_path}\n",
            stderr="",
        )
    )

    exit_code = main(
        ["--base", "develop"],
        repo_root=tmp_path,
        mapping_path=mapping_path,
        runner=runner,
    )

    assert exit_code == 2
    assert "候选路径未配置 mapping/NONE" in capsys.readouterr().err
