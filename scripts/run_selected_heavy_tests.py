#!/usr/bin/env python3
"""执行 selector 选出的 HEAVY 测试，并拒绝未实际执行的结果。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

INVALID_RESULT_STATUS = 2


def _load_selected_tests(manifest_path: Path) -> list[str]:
    selected_tests = [line.strip() for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not selected_tests:
        raise ValueError("selected HEAVY manifest 不能为空")

    for selected_test in selected_tests:
        path = PurePosixPath(selected_test)
        if (
            path.is_absolute()
            or path.as_posix() != selected_test
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(f"selected HEAVY 路径必须是规范仓库相对路径: {selected_test!r}")
    return selected_tests


def _junit_counts(junit_path: Path) -> tuple[int, int]:
    # JUnit 由本进程刚启动的 pytest 生成，不接收外部 XML 输入。
    root = ET.parse(junit_path).getroot()  # noqa: S314
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return (
        sum(int(suite.attrib.get("tests", "0")) for suite in suites),
        sum(int(suite.attrib.get("skipped", "0")) for suite in suites),
    )


def run_selected_heavy_tests(*, manifest_path: Path, junit_path: Path, repo_root: Path) -> int:
    """运行已选择测试；pytest 失败、零执行或任一跳过均返回非零。"""
    try:
        selected_tests = _load_selected_tests(manifest_path)
    except (OSError, ValueError) as error:
        print(f"selected HEAVY manifest 无效: {error}", file=sys.stderr)
        return INVALID_RESULT_STATUS

    # 选出的 HEAVY 文件必须完整执行；拒绝继承会通过 -k/-m 缩小收集面的外部 addopts。
    pytest_environment = os.environ.copy()
    pytest_environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-o",
            "addopts=",
            f"--junitxml={junit_path}",
            "--",
            *selected_tests,
        ],
        cwd=repo_root,
        check=False,
        env=pytest_environment,
    )
    if result.returncode != 0:
        return result.returncode

    try:
        total, skipped = _junit_counts(junit_path)
    except (OSError, ET.ParseError, ValueError) as error:
        print(f"selected HEAVY JUnit 报告无效: {error}", file=sys.stderr)
        return INVALID_RESULT_STATUS

    if total == 0 or skipped > 0:
        print(f"selected HEAVY 未全部实际执行: total={total}, skipped={skipped}", file=sys.stderr)
        return INVALID_RESULT_STATUS
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("junit", type=Path)
    args = parser.parse_args()
    return run_selected_heavy_tests(
        manifest_path=args.manifest,
        junit_path=args.junit,
        repo_root=Path.cwd(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
