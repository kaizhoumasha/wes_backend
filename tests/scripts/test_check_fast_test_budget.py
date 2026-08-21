"""FAST 测试 JUnit 速度预算脚本的合同测试。"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_fast_test_budget.py"


def _write_junit_report(path: Path, testcases: list[tuple[str, float]], suite_duration: float = 0.0) -> None:
    """写入最小 xunit2 JUnit 报告。"""

    cases = "".join(
        f'<testcase classname="{classname}" name="case_{index}" time="{duration}" />'
        for index, (classname, duration) in enumerate(testcases)
    )
    path.write_text(f'<testsuites><testsuite name="fast" time="{suite_duration}">{cases}</testsuite></testsuites>')


def _run_budget_check(report_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """执行预算脚本并捕获其面向调用方的输出。"""

    return subprocess.run(
        [sys.executable, str(SCRIPT), str(report_path), *args],
        capture_output=True,
        check=False,
        text=True,
    )


def test_report_only_prints_actual_values_and_keeps_exit_zero(tmp_path: Path) -> None:
    report_path = tmp_path / "fast.xml"
    testcases = [("tests.unit.test_fast", 0.2)] * 30
    testcases.append(("tests.core.test_slow", 12.2))
    _write_junit_report(report_path, testcases, suite_duration=180.1)

    result = _run_budget_check(report_path, "--report-only")

    assert result.returncode == 0
    assert "套件总耗时 180.100s，预算 180.000s" in result.stdout
    assert "tests.core.test_slow::case_30：12.200s，预算 12.000s" in result.stdout
    assert "tests/unit/ p95：0.200s，预算 0.100s，N=30" in result.stdout


def test_enforced_mode_exits_nonzero_when_a_budget_is_exceeded(tmp_path: Path) -> None:
    report_path = tmp_path / "fast.xml"
    _write_junit_report(report_path, [("tests.core.test_slow", 12.2)])

    result = _run_budget_check(report_path)

    assert result.returncode == 1
    assert "超出 FAST 测试速度预算" in result.stdout


def test_enforced_mode_accepts_a_case_below_twelve_seconds(tmp_path: Path) -> None:
    report_path = tmp_path / "fast.xml"
    _write_junit_report(report_path, [("tests.core.test_fast_enough", 11.9)])

    result = _run_budget_check(report_path)

    assert result.returncode == 0


def test_p95_budget_silently_skips_directories_with_fewer_than_thirty_cases(tmp_path: Path) -> None:
    report_path = tmp_path / "fast.xml"
    _write_junit_report(report_path, [("tests.unit.test_fast", 0.2)] * 29)

    result = _run_budget_check(report_path)

    assert result.returncode == 0
    assert "tests/unit/ p95" not in result.stdout


def test_classname_is_parsed_as_a_test_path_for_directory_budgeting(tmp_path: Path) -> None:
    report_path = tmp_path / "fast.xml"
    testcases = [("tests.unit.nested.test_fast", 0.1)] * 28
    testcases.extend(("tests.unit.nested.test_slowest", 0.2) for _ in range(2))
    _write_junit_report(report_path, testcases)

    result = _run_budget_check(report_path)

    assert result.returncode == 1
    assert "tests/unit/ p95：0.200s，预算 0.100s，N=30" in result.stdout
