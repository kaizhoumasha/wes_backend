#!/usr/bin/env python3
"""根据 Git 改动选择受影响的 HEAVY 测试。"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

HEAVY_DIRECT_GLOB = "tests/{integration,e2e,resilience,load,mock}/**/test_*.py"
CANDIDATE_GLOBS = (
    "src/**",
    "main.py",
    "migrations/**",
    "alembic.ini",
    "docker-compose*.yml",
    "pyproject.toml",
    ".env*",
    "tests/{integration,e2e,resilience,load,mock}/**",
    "tests/fixtures/**",
    "tests/conftest.py",
    "tests/*/conftest.py",
    "tests/support/**",
)


class SelectorError(RuntimeError):
    """表示 selector 必须 fail closed 的输入或配置错误。"""


@dataclass(frozen=True)
class MappingEntry:
    """一条候选源路径到 HEAVY 测试集合的映射。"""

    source_glob: str
    heavy_tests: tuple[str, ...]


type SelectorConfig = tuple[tuple[str, ...], tuple[MappingEntry, ...]]
type GitRunner = Callable[..., subprocess.CompletedProcess[str]]


def expand_braces(pattern: str) -> list[str]:
    """按合同递归展开 ``{a,b}``，保持声明顺序。"""
    opening = pattern.find("{")
    if opening == -1:
        if "}" in pattern:
            raise SelectorError(f"glob brace 不完整: {pattern}")
        return [pattern]

    closing = pattern.find("}", opening + 1)
    if closing == -1 or "{" in pattern[opening + 1 : closing]:
        raise SelectorError(f"glob brace 不完整或嵌套: {pattern}")

    choices = pattern[opening + 1 : closing].split(",")
    if len(choices) < 2 or any(not choice for choice in choices):
        raise SelectorError(f"glob brace 必须包含至少两个非空选项: {pattern}")

    expanded: list[str] = []
    for choice in choices:
        expanded.extend(expand_braces(f"{pattern[:opening]}{choice}{pattern[closing + 1 :]}"))
    return expanded


def matches_glob(path: str, pattern: str) -> bool:
    """使用 POSIX ``PurePath.full_match`` 语义匹配展开后的 glob。"""
    pure_path = PurePosixPath(path)
    return any(pure_path.full_match(expanded) for expanded in expand_braces(pattern))


def is_heavy_test(path: str) -> bool:
    return matches_glob(path, HEAVY_DIRECT_GLOB)


def is_candidate(path: str) -> bool:
    return any(matches_glob(path, pattern) for pattern in CANDIDATE_GLOBS)


def _sample_paths(pattern: str) -> set[str]:
    """生成保守样例，用于在加载 schema 时识别常见 glob 重叠。"""
    samples: set[str] = set()
    for expanded in expand_braces(pattern):
        partials: list[list[str]] = [[]]
        for segment in expanded.split("/"):
            if segment == "**":
                partials = partials + [[*parts, "sample"] for parts in partials]
                continue

            sample_segment = re.sub(r"\[([^]]+)]", lambda match: match.group(1)[0], segment)
            sample_segment = sample_segment.replace("*", "sample").replace("?", "x")
            partials = [[*parts, sample_segment] for parts in partials]
        samples.update("/".join(parts) for parts in partials)
    return samples


def _class_matches(specification: str, character: str) -> bool:
    negated = specification.startswith(("!", "^"))
    content = specification[1:] if negated else specification
    matched = False
    index = 0
    while index < len(content):
        if index + 2 < len(content) and content[index + 1] == "-":
            matched = matched or content[index] <= character <= content[index + 2]
            index += 3
        else:
            matched = matched or content[index] == character
            index += 1
    return not matched if negated else matched


def _epsilon_positions(pattern: str, position: int) -> set[int]:
    positions = {position}
    while position < len(pattern) and pattern[position] == "*":
        position += 1
        positions.add(position)
    return positions


def _segment_transitions(pattern: str, position: int, character: str) -> set[int]:
    if position >= len(pattern):
        return set()
    glob_character = pattern[position]
    if glob_character == "*":
        return {position}
    if glob_character == "?":
        return {position + 1}
    if glob_character == "[":
        closing = pattern.find("]", position + 1)
        if closing != -1:
            return {closing + 1} if _class_matches(pattern[position + 1 : closing], character) else set()
    return {position + 1} if glob_character == character else set()


def _segment_patterns_overlap(left: str, right: str) -> bool:
    # 有限字符表包含所有 ASCII 路径字符及 glob 中的非 ASCII 字面量；足以区分字符类与通配符。
    alphabet = {chr(codepoint) for codepoint in range(32, 127) if chr(codepoint) != "/"}
    alphabet.update(character for character in left + right if character not in "/*?[]!^-")
    pending = deque(
        (left_position, right_position)
        for left_position in _epsilon_positions(left, 0)
        for right_position in _epsilon_positions(right, 0)
    )
    visited: set[tuple[int, int]] = set()

    while pending:
        state = pending.popleft()
        if state in visited:
            continue
        visited.add(state)
        left_position, right_position = state
        if left_position == len(left) and right_position == len(right):
            return True

        for character in alphabet:
            for next_left in _segment_transitions(left, left_position, character):
                for next_right in _segment_transitions(right, right_position, character):
                    pending.extend(
                        (closed_left, closed_right)
                        for closed_left in _epsilon_positions(left, next_left)
                        for closed_right in _epsilon_positions(right, next_right)
                    )
    return False


def _expanded_patterns_overlap(left: str, right: str) -> bool:
    left_parts = left.split("/")
    right_parts = right.split("/")
    pending = deque([(0, 0)])
    visited: set[tuple[int, int]] = set()

    while pending:
        state = pending.popleft()
        if state in visited:
            continue
        visited.add(state)
        left_position, right_position = state
        if left_position == len(left_parts) and right_position == len(right_parts):
            return True
        if left_position < len(left_parts) and left_parts[left_position] == "**":
            pending.append((left_position + 1, right_position))
            if right_position < len(right_parts):
                pending.append((left_position, right_position + 1))
            continue
        if right_position < len(right_parts) and right_parts[right_position] == "**":
            pending.append((left_position, right_position + 1))
            if left_position < len(left_parts):
                pending.append((left_position + 1, right_position))
            continue
        if left_position == len(left_parts) or right_position == len(right_parts):
            continue
        if _segment_patterns_overlap(left_parts[left_position], right_parts[right_position]):
            pending.append((left_position + 1, right_position + 1))
    return False


def _patterns_overlap(left: str, right: str) -> bool:
    return any(
        _expanded_patterns_overlap(expanded_left, expanded_right)
        for expanded_left in expand_braces(left)
        for expanded_right in expand_braces(right)
    )


def validate_config(ignore_globs: Iterable[str], mappings: Iterable[MappingEntry]) -> SelectorConfig:
    """验证机器可读映射；任何不确定性都作为配置错误。"""
    normalized_ignores = tuple(ignore_globs)
    normalized_mappings = tuple(mappings)

    for pattern in normalized_ignores:
        if not isinstance(pattern, str) or not pattern:
            raise SelectorError("ignore_globs 必须是非空字符串列表")
        expand_braces(pattern)

    for mapping in normalized_mappings:
        if not mapping.source_glob:
            raise SelectorError("mapping.source_glob 必须是非空字符串")
        expand_braces(mapping.source_glob)
        if not any(is_candidate(sample) for sample in _sample_paths(mapping.source_glob)):
            raise SelectorError(f"mapping.source_glob 不在候选范围: {mapping.source_glob}")
        for heavy_test in mapping.heavy_tests:
            if not is_heavy_test(heavy_test):
                raise SelectorError(f"无效 HEAVY 测试路径: {heavy_test}")

    for index, left in enumerate(normalized_mappings):
        for right in normalized_mappings[index + 1 :]:
            if left.heavy_tests != right.heavy_tests and _patterns_overlap(left.source_glob, right.source_glob):
                raise SelectorError(f"mapping source_glob 歧义: {left.source_glob} 与 {right.source_glob}")

    return normalized_ignores, normalized_mappings


def load_config(path: Path) -> SelectorConfig:
    """加载并严格验证 ``heavy-test-impact.toml``。"""
    try:
        with path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SelectorError(f"无法加载 HEAVY 映射 {path}: {error}") from error

    ignore_globs = raw.get("ignore_globs")
    raw_mappings = raw.get("mapping", [])
    if not isinstance(ignore_globs, list) or not isinstance(raw_mappings, list):
        raise SelectorError("TOML schema 要求 ignore_globs 列表和 mapping 表数组")

    mappings: list[MappingEntry] = []
    for raw_mapping in raw_mappings:
        if not isinstance(raw_mapping, dict):
            raise SelectorError("每条 mapping 必须是 TOML table")
        source_glob = raw_mapping.get("source_glob")
        heavy_tests = raw_mapping.get("heavy_tests")
        if (
            not isinstance(source_glob, str)
            or not isinstance(heavy_tests, list)
            or not all(isinstance(test_path, str) for test_path in heavy_tests)
        ):
            raise SelectorError("mapping 要求 source_glob 字符串和 heavy_tests 字符串列表")
        mappings.append(MappingEntry(source_glob=source_glob, heavy_tests=tuple(sorted(set(heavy_tests)))))

    return validate_config(ignore_globs, mappings)


def get_changed_files(
    *,
    scope: str | None,
    base: str | None,
    repo_root: Path,
    runner: GitRunner = subprocess.run,
) -> list[str]:
    """按本地 scope 或 CI base 协议读取 Git 改动路径。"""
    if base is not None:
        commands = [["git", "diff", "--name-only", f"{base}...HEAD"]]
    elif scope == "staged":
        commands = [["git", "diff", "--cached", "--name-only"]]
    elif scope in (None, "unstaged"):
        commands = [
            ["git", "diff", "--name-only"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ]
    else:
        raise SelectorError(f"不支持的 diff scope: {scope}")

    try:
        results = [runner(command, cwd=repo_root, check=True, capture_output=True, text=True) for command in commands]
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip()
        raise SelectorError(f"git diff 失败: {detail or error}") from error
    return sorted({line.strip() for result in results for line in result.stdout.splitlines() if line.strip()})


def select_heavy_tests(changed_files: Iterable[str], config: SelectorConfig) -> list[str]:
    """按直接 HEAVY → 候选 mapping → ignore 的顺序进行选择。"""
    ignore_globs, mappings = config
    selected: set[str] = set()

    for raw_path in changed_files:
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or str(path) in ("", "."):
            raise SelectorError(f"无效改动路径: {raw_path}")
        normalized_path = path.as_posix()

        if is_heavy_test(normalized_path):
            selected.add(normalized_path)
            continue

        if is_candidate(normalized_path):
            matched = [mapping for mapping in mappings if matches_glob(normalized_path, mapping.source_glob)]
            if not matched:
                raise SelectorError(f"候选路径未配置 mapping/NONE: {normalized_path}")
            heavy_sets = {mapping.heavy_tests for mapping in matched}
            if len(heavy_sets) > 1:
                raise SelectorError(f"候选路径命中歧义 mapping: {normalized_path}")
            for mapping in matched:
                selected.update(mapping.heavy_tests)
            continue

        if any(matches_glob(normalized_path, pattern) for pattern in ignore_globs):
            continue
        raise SelectorError(f"未分类改动路径: {normalized_path}")

    for test_path in selected:
        if not is_heavy_test(test_path):
            raise SelectorError(f"selector 输出不是 HEAVY 测试路径: {test_path}")
    return sorted(selected)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    diff_source = parser.add_mutually_exclusive_group()
    diff_source.add_argument("--scope", choices=("staged", "unstaged"), default="unstaged")
    diff_source.add_argument("--base", metavar="REF")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    repo_root: Path | None = None,
    mapping_path: Path | None = None,
    runner: GitRunner = subprocess.run,
) -> int:
    arguments = build_parser().parse_args(argv)
    resolved_root = repo_root or Path(__file__).resolve().parents[1]
    resolved_mapping = mapping_path or resolved_root / "docs/architecture/heavy-test-impact.toml"

    try:
        changed_files = get_changed_files(
            scope=None if arguments.base else arguments.scope,
            base=arguments.base,
            repo_root=resolved_root,
            runner=runner,
        )
        selected = select_heavy_tests(changed_files, load_config(resolved_mapping))
    except SelectorError as error:
        print(f"HEAVY selector fail closed: {error}", file=sys.stderr)
        return 2

    if selected:
        print("\n".join(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
