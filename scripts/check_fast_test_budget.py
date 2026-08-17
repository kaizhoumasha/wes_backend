"""检查 FAST pytest JUnit 报告的执行速度预算。"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

SUITE_BUDGET_SECONDS = 90.0
CASE_BUDGET_SECONDS = 3.0
DIRECTORY_P95_BUDGET_SECONDS = 0.1
MINIMUM_DIRECTORY_CASE_COUNT = 30
P95_BUDGET_DIRECTORIES = ("tests/unit/",)


def parse_arguments() -> argparse.Namespace:
    """解析报告路径和报告模式开关。"""

    parser = argparse.ArgumentParser(description="检查 FAST pytest JUnit 报告的速度预算")
    parser.add_argument("report", type=Path, help="pytest --junitxml 生成的 xunit2 XML 报告")
    parser.add_argument("--report-only", action="store_true", help="仅报告预算超限，不以非零状态退出")
    return parser.parse_args()


def classname_to_path(classname: str) -> str:
    """将 xunit2 classname 转成可归属目录的 POSIX 风格测试路径。"""

    return classname.replace(".", "/")


def percentile_95(values: list[float]) -> float:
    """按 nearest-rank 计算 p95。"""

    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def suite_duration(root: ET.Element) -> float:
    """读取 pytest 写入的顶层 testsuite 总耗时。"""

    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    return sum(float(suite.attrib.get("time", "0")) for suite in suites)


def collect_violations(report_path: Path) -> list[str]:
    """解析 JUnit 报告并返回全部预算超限描述。"""

    root = ET.parse(report_path).getroot()  # noqa: S314 -- 仅解析本地 pytest 生成的 JUnit 报告。
    violations: list[str] = []
    total_duration = suite_duration(root)
    if total_duration > SUITE_BUDGET_SECONDS:
        violations.append(f"套件总耗时 {total_duration:.3f}s，预算 {SUITE_BUDGET_SECONDS:.3f}s")

    directory_durations: dict[str, list[float]] = defaultdict(list)
    for testcase in root.iter("testcase"):
        duration = float(testcase.attrib.get("time", "0"))
        classname = testcase.attrib.get("classname", "")
        name = testcase.attrib.get("name", "")
        if duration > CASE_BUDGET_SECONDS:
            violations.append(f"{classname}::{name}：{duration:.3f}s，预算 {CASE_BUDGET_SECONDS:.3f}s")

        test_path = classname_to_path(classname)
        for directory in P95_BUDGET_DIRECTORIES:
            if test_path.startswith(directory):
                directory_durations[directory].append(duration)

    for directory in P95_BUDGET_DIRECTORIES:
        durations = directory_durations[directory]
        if len(durations) < MINIMUM_DIRECTORY_CASE_COUNT:
            continue
        p95_duration = percentile_95(durations)
        if p95_duration > DIRECTORY_P95_BUDGET_SECONDS:
            violations.append(
                f"{directory} p95：{p95_duration:.3f}s，预算 {DIRECTORY_P95_BUDGET_SECONDS:.3f}s，N={len(durations)}"
            )

    return violations


def main() -> int:
    """输出预算结果，并按模式返回进程状态。"""

    args = parse_arguments()
    violations = collect_violations(args.report)
    if not violations:
        print("FAST 测试速度预算通过")
        return 0

    print("FAST 测试速度预算超限：")
    for violation in violations:
        print(f"- {violation}")
    if args.report_only:
        print("当前为 --report-only 模式，记录实测结果但不阻断质量流程。")
        return 0
    print("超出 FAST 测试速度预算")
    return 1


if __name__ == "__main__":
    sys.exit(main())
