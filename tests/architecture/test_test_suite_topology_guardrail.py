import re
import tomllib
from pathlib import Path

from tests.support.test_suite_topology import (
    DEFAULT_EXCLUDED_TEST_DIRS,
    ROOT_LEVEL_TEST_FILE_ALLOWLIST,
    TEST_FILE_LINE_LIMIT_ALLOWLIST,
    root_level_test_files,
)
from tests.support.test_suite_topology import (
    test_files_over_line_limit as find_test_files_over_line_limit,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _relative_paths(paths: list[Path]) -> set[str]:
    return {path.relative_to(REPO_ROOT).as_posix() for path in paths}


def test_root_level_test_files_stay_in_allowlist() -> None:
    actual_files = _relative_paths(root_level_test_files())

    assert actual_files == ROOT_LEVEL_TEST_FILE_ALLOWLIST


def test_oversized_test_files_stay_in_allowlist() -> None:
    actual_files = _relative_paths(find_test_files_over_line_limit())

    assert actual_files == TEST_FILE_LINE_LIMIT_ALLOWLIST


def test_pytest_default_collection_excludes_heavy_test_directories() -> None:
    pyproject_config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    norecursedirs = set(pyproject_config["tool"]["pytest"]["ini_options"]["norecursedirs"])

    assert norecursedirs >= DEFAULT_EXCLUDED_TEST_DIRS


def test_readme_documents_default_fast_regression_exclusions() -> None:
    readme_text = (REPO_ROOT / "tests" / "README.md").read_text(encoding="utf-8")

    assert "默认快速回归集" in readme_text
    assert "重测试目录" in readme_text
    for excluded_dir in sorted(DEFAULT_EXCLUDED_TEST_DIRS):
        assert excluded_dir in readme_text


def test_readme_does_not_publish_fixed_test_inventory_counts() -> None:
    readme_text = (REPO_ROOT / "tests" / "README.md").read_text(encoding="utf-8")

    stale_patterns = (
        r"下共有\s*`\d+`\s*个\s*`test_\*\.py`\s*文件",
        r"collect\s*为\s*`\d+`\s*个测试",
    )

    assert not any(re.search(pattern, readme_text) for pattern in stale_patterns)


def test_runtime_inbox_postgresql_crash_scenarios_live_in_resilience() -> None:
    """真实崩溃恢复属于 resilience；integration 仅保留正常闭环。"""

    integration_source = (REPO_ROOT / "tests/integration/test_runtime_inbox_processing_postgresql.py").read_text(
        encoding="utf-8"
    )
    resilience_path = REPO_ROOT / "tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py"

    assert "test_claim_crash_recovers_with_new_owner_and_rejects_old_fence" not in integration_source
    assert "test_writeback_crash_rolls_back_effects_before_reprocessing_once" not in integration_source
    assert resilience_path.is_file()
