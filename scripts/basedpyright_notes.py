#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


DIAG_RE = re.compile(r"^\s+.+ - (information|warning|error): ")
RULE_RE = re.compile(r"\((report[^)]+)\)$")


def build_command(paths: list[str], level: str | None) -> list[str]:
    cmd = ["uv", "run", "basedpyright"]
    if level:
        cmd.extend(["--level", level])
    cmd.extend(paths or ["."])
    return cmd


def summarize(output: str, repo_root: Path, top: int) -> tuple[Counter[str], Counter[str], Counter[str]]:
    current_file: str | None = None
    files: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    levels: Counter[str] = Counter()

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if line.startswith(str(repo_root)) and Path(line).suffix == ".py":
            current_file = os.path.relpath(line, repo_root)
            continue

        match = DIAG_RE.match(line)
        if current_file is None or not match:
            continue

        level = match.group(1)
        rule_match = RULE_RE.search(line)
        rule = rule_match.group(1) if rule_match else "unknown"
        files[current_file] += 1
        rules[rule] += 1
        levels[level] += 1

    return (
        Counter(dict(files.most_common(top))),
        Counter(dict(rules.most_common(top))),
        levels,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize basedpyright diagnostics by file and rule.")
    parser.add_argument("paths", nargs="*", help="Paths passed to basedpyright. Defaults to '.'")
    parser.add_argument("--top", type=int, default=10, help="Number of top files/rules to show")
    parser.add_argument(
        "--level",
        choices=("error", "warning", "information", "hint"),
        default=None,
        help="Optional basedpyright --level filter",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    cmd = build_command(args.paths, args.level)

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", ".")

    result = subprocess.run(
        cmd,
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    output = result.stdout
    top_files, top_rules, levels = summarize(output, repo_root, args.top)

    print("Levels:")
    for level in ("error", "warning", "information", "hint"):
        count = levels.get(level, 0)
        if count:
            print(f"  {level:11} {count}")

    print("\nTop files:")
    if top_files:
        for path, count in top_files.items():
            print(f"  {count:4} {path}")
    else:
        print("     none")

    print("\nTop rules:")
    if top_rules:
        for rule, count in top_rules.items():
            print(f"  {count:4} {rule}")
    else:
        print("     none")

    print("\nSummary tail:")
    tail = "\n".join(output.strip().splitlines()[-5:])
    if tail:
        print(tail)

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
