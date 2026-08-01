import re
import tomllib
from pathlib import Path

from tests.support.test_suite_topology import (
    DEFAULT_EXCLUDED_TEST_DIRS,
    FINAL_TEST_DIRECTORY_ALLOWLIST,
    ROOT_LEVEL_TEST_FILE_ALLOWLIST,
    TEST_FILE_LINE_LIMIT_ALLOWLIST,
    root_level_test_files,
    top_level_test_directories,
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


def test_test_files_stay_in_governed_top_level_directories() -> None:
    actual_directories = _relative_paths(top_level_test_directories())

    assert actual_directories <= FINAL_TEST_DIRECTORY_ALLOWLIST


def test_readme_documents_default_fast_regression_exclusions() -> None:
    readme_text = (REPO_ROOT / "tests" / "README.md").read_text(encoding="utf-8")

    assert "默认快速回归集" in readme_text
    assert "重测试目录" in readme_text
    for excluded_dir in sorted(DEFAULT_EXCLUDED_TEST_DIRS):
        assert excluded_dir in readme_text


def test_readme_documents_test_governance_hard_constraints() -> None:
    readme_text = (REPO_ROOT / "tests" / "README.md").read_text(encoding="utf-8")

    required_constraints = (
        "先建立目标对象测试并通过，再删除对应旧测试",
        "同一行为只有一个主要测试所有者",
        "承接的目标测试路径或 `NONE`",
        "不得按 `replay`、`legacy`、`reconciliation` 等关键词批量删除测试",
        "不得依赖真实数据库、HTTP、Celery、Redis、容器等真实服务",
    )

    assert all(constraint in readme_text for constraint in required_constraints)


def test_pre_commit_always_executes_quality_gate() -> None:
    hook_text = (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")

    assert 'exec ./scripts/git-quality-gate.sh --profile "quality"' in hook_text


def test_readme_does_not_publish_fixed_test_inventory_counts() -> None:
    readme_text = (REPO_ROOT / "tests" / "README.md").read_text(encoding="utf-8")

    stale_patterns = (
        r"下共有\s*`\d+`\s*个\s*`test_\*\.py`\s*文件",
        r"collect\s*为\s*`\d+`\s*个测试",
    )

    assert not any(re.search(pattern, readme_text) for pattern in stale_patterns)


def test_runtime_extension_platform_test_files_stay_below_hard_limit() -> None:
    paths = (
        REPO_ROOT / "tests/workline_plugins/conformance.py",
        REPO_ROOT / "tests/workline_plugins/rough_sorter/test_conformance.py",
        REPO_ROOT / "tests/architecture/test_runtime_extension_platform_guardrail.py",
    )

    assert all(path.exists() for path in paths)
    assert all(len(path.read_text(encoding="utf-8").splitlines()) < 1000 for path in paths)
