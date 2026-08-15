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


def test_pytest_default_collection_excludes_quality_only_directories() -> None:
    pyproject_config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    norecursedirs = set(pyproject_config["tool"]["pytest"]["ini_options"]["norecursedirs"])

    assert {"tests/architecture", "tests/scripts"} <= norecursedirs


def test_pytest_fast_defaults_use_xunit2_without_implicit_coverage_noise() -> None:
    pyproject_config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_options = pyproject_config["tool"]["pytest"]["ini_options"]

    assert pytest_options["junit_family"] == "xunit2"
    assert pytest_options["addopts"] == ["-v", "--durations=10", "--tb=short"]


def test_quality_gate_enforces_fast_budget() -> None:
    gate_text = (REPO_ROOT / "scripts" / "git-quality-gate.sh").read_text(encoding="utf-8")

    assert "pytest --junitxml=reports/fast-tests.xml" in gate_text
    assert "check_fast_test_budget.py reports/fast-tests.xml" in gate_text
    assert "--report-only" not in gate_text


def test_test_files_stay_in_governed_top_level_directories() -> None:
    actual_directories = _relative_paths(top_level_test_directories())

    assert "tests/docs" not in FINAL_TEST_DIRECTORY_ALLOWLIST
    assert actual_directories <= FINAL_TEST_DIRECTORY_ALLOWLIST


def test_pre_commit_routes_docs_only_changes_before_quality_fallback() -> None:
    hook_text = (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")

    assert "--no-renames" in hook_text
    assert "git diff --cached --check" in hook_text
    assert 'exec ./scripts/git-quality-gate.sh --profile "quality"' in hook_text
