#!/usr/bin/env python3
"""根据 Git 改动选择受影响的 HEAVY 测试。"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import tomllib
import unicodedata
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath

HEAVY_DIRECT_GLOB = "tests/{integration,e2e,resilience,load,mock}/**/test_*.py"
HUMAN_DOCUMENT_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".docx", ".pdf", ".eddx"})
RETIRED_REMOVED_ROOTS = (
    "docs/archive/",
    "docs/superpowers/archive/",
    "packages/wes_plugin_sdk/",
)
RETIRED_REMOVED_PATHS = frozenset(
    {
        "Jenkinsfile",
        "scripts/data/bootstrap_admin.py",
        "scripts/data/bootstrap_admin.sh",
        "scripts/data/init_production_base_data.sql",
        "scripts/data/sync_menus.py",
        "scripts/data/sync_menus.sh",
        "src/app/admin/models/menu.py",
        "src/app/admin/repositories/menu_repository.py",
        "src/app/admin/services/menu_service.py",
        "src/app/admin/services/menu_sync_service.py",
        "src/app/admin/v1/menu.py",
        "src/utils/frontend_menu_parser.py",
    }
)
CANDIDATE_GLOBS = (
    "src/**",
    "deployment/**",
    "nginx/**",
    "main.py",
    "migrations/**",
    "alembic.ini",
    "docker-compose*.yml",
    "Dockerfile",
    "docker/test/*.entrypoint.sh",
    ".dockerignore",
    "Jenkinsfile.backend-ci",
    "Jenkinsfile.release-checker-ci",
    "pyproject.toml",
    "uv.lock",
    ".env*",
    "scripts/**",
    "tools/release_checker/release_checker.py",
    "tools/release_checker/Dockerfile",
    "docs/**/*.{toml,csv,yaml,yml,json}",
    "tests/{integration,e2e,resilience,load,mock}/**",
    "tests/api/callback_test_support.py",
    "tests/contracts/wms_integration/provider_profile_support.py",
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
    reviewed_content_sha256: str | None = None


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


def is_human_document(path: str) -> bool:
    pure_path = PurePosixPath(path)
    file_name = pure_path.name.lower()
    suffix = pure_path.suffix.lower()
    if suffix in HUMAN_DOCUMENT_SUFFIXES or file_name in {"readme", "license"}:
        return True
    if suffix != ".txt":
        return False

    # 候选执行目录中的 txt 可能是合同、fixture 或脚本输入；依赖清单也不是人类阅读文档。
    return not is_candidate(path) and not file_name.startswith(("requirements", "constraints"))


def _validate_repository_relative(value: str, *, field: str, allow_glob: bool) -> str:
    """只接受未经归一化、无穿越语义的 POSIX 仓库相对路径。"""
    if not isinstance(value, str) or not value:
        raise SelectorError(f"{field} 必须是非空规范仓库相对路径")
    if "\\" in value or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise SelectorError(f"{field} 必须是规范仓库相对路径: {value!r}")

    path = PurePosixPath(value)
    raw_parts = value.split("/")
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
        or path.as_posix() != value
        or (not allow_glob and any(character in value for character in "*?[]{}"))
    ):
        raise SelectorError(f"{field} 必须是规范仓库相对路径: {value!r}")
    return value


def _validate_character_class_syntax(pattern: str, *, field: str) -> None:
    """限制为交集算法可与 ``PurePath.full_match`` 一致分析的字符类。"""
    position = 0
    while position < len(pattern):
        opening = pattern.find("[", position)
        if opening == -1:
            return
        closing = pattern.find("]", opening + 1)
        specification = pattern[opening + 1 : closing] if closing != -1 else ""
        content = specification[1:] if specification.startswith("!") else specification
        if (
            closing == -1
            or not content
            or specification.startswith("!")
            or "[" in specification
            or "--" in specification
        ):
            raise SelectorError(f"{field} 包含不支持的 glob 字符类: {pattern}")
        for index, character in enumerate(content):
            if character == "-" and 0 < index < len(content) - 1 and content[index - 1] > content[index + 1]:
                raise SelectorError(f"{field} 包含不支持的 glob 字符类: {pattern}")
        position = closing + 1


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
    # pathlib/fnmatch 仅以 ``!`` 表示否定；``^`` 是字符类中的普通成员。
    negated = specification.startswith("!")
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


@cache
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


@cache
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
        for expanded in expand_braces(pattern):
            _validate_repository_relative(expanded, field="ignore_globs", allow_glob=True)
            _validate_character_class_syntax(expanded, field="ignore_globs")

    for mapping in normalized_mappings:
        if not mapping.source_glob:
            raise SelectorError("mapping.source_glob 必须是非空字符串")
        for expanded in expand_braces(mapping.source_glob):
            _validate_repository_relative(expanded, field="mapping.source_glob", allow_glob=True)
            _validate_character_class_syntax(expanded, field="mapping.source_glob")
        if not any(is_candidate(sample) for sample in _sample_paths(mapping.source_glob)):
            raise SelectorError(f"mapping.source_glob 不在候选范围: {mapping.source_glob}")
        for heavy_test in mapping.heavy_tests:
            _validate_repository_relative(heavy_test, field="mapping.heavy_tests", allow_glob=False)
            if not is_heavy_test(heavy_test):
                raise SelectorError(f"无效 HEAVY 测试路径: {heavy_test}")
        if mapping.reviewed_content_sha256 is not None:
            try:
                _validate_repository_relative(
                    mapping.source_glob,
                    field="reviewed_content_sha256 source_glob",
                    allow_glob=False,
                )
            except SelectorError as error:
                raise SelectorError("reviewed_content_sha256 只允许搭配精确 source_glob") from error
            if re.fullmatch(r"[0-9a-f]{64}", mapping.reviewed_content_sha256) is None:
                raise SelectorError("reviewed_content_sha256 必须是小写十六进制 SHA-256")

    for index, left in enumerate(normalized_mappings):
        for right in normalized_mappings[index + 1 :]:
            left_policy = (left.heavy_tests, left.reviewed_content_sha256)
            right_policy = (right.heavy_tests, right.reviewed_content_sha256)
            if left_policy != right_policy and _patterns_overlap(left.source_glob, right.source_glob):
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
        reviewed_content_sha256 = raw_mapping.get("reviewed_content_sha256")
        if (
            not isinstance(source_glob, str)
            or not isinstance(heavy_tests, list)
            or not all(isinstance(test_path, str) for test_path in heavy_tests)
            or (reviewed_content_sha256 is not None and not isinstance(reviewed_content_sha256, str))
        ):
            raise SelectorError(
                "mapping 要求 source_glob 字符串、heavy_tests 字符串列表和可选 reviewed_content_sha256 字符串"
            )
        mappings.append(
            MappingEntry(
                source_glob=source_glob,
                heavy_tests=tuple(sorted(set(heavy_tests))),
                reviewed_content_sha256=reviewed_content_sha256,
            )
        )

    return validate_config(ignore_globs, mappings)


def _decode_git_quoted_path(value: str) -> str:
    """解码 Git ``core.quotePath`` 产生的 C 风格路径，不放宽后续路径校验。"""
    if not value.startswith('"') and not value.endswith('"'):
        return value
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        raise SelectorError(f"git diff 返回不完整的引号路径: {value!r}")

    escapes = {
        "a": 0x07,
        "b": 0x08,
        "t": 0x09,
        "n": 0x0A,
        "v": 0x0B,
        "f": 0x0C,
        "r": 0x0D,
        '"': 0x22,
        "\\": 0x5C,
    }
    decoded = bytearray()
    content = value[1:-1]
    position = 0
    while position < len(content):
        character = content[position]
        if character != "\\":
            decoded.extend(character.encode("utf-8"))
            position += 1
            continue

        position += 1
        if position >= len(content):
            raise SelectorError(f"git diff 返回不完整的路径转义: {value!r}")
        escape = content[position]
        if escape in escapes:
            decoded.append(escapes[escape])
            position += 1
            continue
        if escape in "01234567":
            end = position + 1
            while end < min(position + 3, len(content)) and content[end] in "01234567":
                end += 1
            octet = int(content[position:end], 8)
            if octet > 0xFF:
                raise SelectorError(f"git diff 返回超出字节范围的路径转义: {value!r}")
            decoded.append(octet)
            position = end
            continue
        raise SelectorError(f"git diff 返回不支持的路径转义: {value!r}")

    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SelectorError(f"git diff 返回非 UTF-8 路径: {value!r}") from error


def get_changed_files(
    *,
    scope: str | None,
    base: str | None,
    repo_root: Path,
    runner: GitRunner = subprocess.run,
) -> list[str]:
    """按本地 scope 或 CI base 协议读取 Git 改动路径。"""
    if base is not None:
        # base 会直接作为 Git argv 传入；拒绝 option-like 值，
        # 避免 Git 把 ref 当成参数并返回空 diff。
        if (
            not base
            or base.startswith("-")
            or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in base)
        ):
            raise SelectorError(f"base ref 必须是非空且非 option 的 Git revision: {base!r}")
        commands = [["git", "diff", "--no-renames", "--name-only", f"{base}...HEAD"]]
    elif scope == "staged":
        commands = [["git", "diff", "--cached", "--no-renames", "--name-only"]]
    elif scope in (None, "unstaged"):
        commands = [
            ["git", "diff", "--no-renames", "--name-only"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ]
    else:
        raise SelectorError(f"不支持的 diff scope: {scope}")

    try:
        results = [runner(command, cwd=repo_root, check=True, capture_output=True, text=True) for command in commands]
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip()
        raise SelectorError(f"git diff 失败: {detail or error}") from error
    # 只解码 Git 自身的可逆引号表示；异常空白/控制字符仍交给严格路径校验 fail closed。
    return sorted({_decode_git_quoted_path(line) for result in results for line in result.stdout.split("\n") if line})


def select_heavy_tests(
    changed_files: Iterable[str],
    config: SelectorConfig,
    *,
    repo_root: Path | None = None,
) -> list[str]:
    """按直接 HEAVY → 人类文档 → 候选 mapping → ignore 的顺序进行选择。"""
    ignore_globs, mappings = config
    selected: set[str] = set()

    for raw_path in changed_files:
        normalized_path = _validate_repository_relative(raw_path, field="changed path", allow_glob=False)

        if is_heavy_test(normalized_path):
            selected.add(normalized_path)
            continue

        if is_human_document(normalized_path):
            continue

        if is_candidate(normalized_path):
            matched = [mapping for mapping in mappings if matches_glob(normalized_path, mapping.source_glob)]
            if not matched:
                raise SelectorError(f"候选路径未配置 mapping/NONE: {normalized_path}")
            heavy_sets = {mapping.heavy_tests for mapping in matched}
            if len(heavy_sets) > 1:
                raise SelectorError(f"候选路径命中歧义 mapping: {normalized_path}")
            for mapping in matched:
                if mapping.reviewed_content_sha256 is not None:
                    content_path = (repo_root or Path.cwd()) / normalized_path
                    try:
                        actual_sha256 = hashlib.sha256(content_path.read_bytes()).hexdigest()
                    except OSError as error:
                        raise SelectorError(f"reviewed mapping 无法读取当前内容: {normalized_path}: {error}") from error
                    if actual_sha256 != mapping.reviewed_content_sha256:
                        raise SelectorError(
                            "reviewed mapping 内容指纹不匹配: "
                            f"{normalized_path} expected={mapping.reviewed_content_sha256} actual={actual_sha256}"
                        )
                selected.update(mapping.heavy_tests)
            continue

        if any(matches_glob(normalized_path, pattern) for pattern in ignore_globs):
            continue
        raise SelectorError(f"未分类改动路径: {normalized_path}")

    for test_path in selected:
        _validate_repository_relative(test_path, field="selector output", allow_glob=False)
        if not is_heavy_test(test_path):
            raise SelectorError(f"selector 输出不是 HEAVY 测试路径: {test_path}")
    return sorted(selected)


def filter_deleted_retired_archive_paths(changed_files: Iterable[str], *, repo_root: Path) -> list[str]:
    """只跳过已从当前树删除的历史路径；重新引入的文件仍 fail closed。"""
    retained: list[str] = []
    for raw_path in changed_files:
        normalized_path = _validate_repository_relative(raw_path, field="changed path", allow_glob=False)
        is_retired = normalized_path in RETIRED_REMOVED_PATHS or any(
            normalized_path.startswith(root) for root in RETIRED_REMOVED_ROOTS
        )
        current_path = repo_root / normalized_path
        if is_retired and not (current_path.exists() or current_path.is_symlink()):
            continue
        retained.append(normalized_path)
    return retained


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
        config = load_config(resolved_mapping)
        missing_mapped_tests = sorted(
            {
                heavy_test
                for mapping in config[1]
                for heavy_test in mapping.heavy_tests
                if not (resolved_root / heavy_test).is_file()
            }
        )
        if missing_mapped_tests:
            raise SelectorError("mapping 引用不存在的 HEAVY 测试: " + ", ".join(missing_mapped_tests))
        changed_files = filter_deleted_retired_archive_paths(changed_files, repo_root=resolved_root)
        # 删除记录仍用于 source/support mapping；只有已不存在、无法执行的直接 HEAVY 测试需要剔除。
        changed_files = [path for path in changed_files if not is_heavy_test(path) or (resolved_root / path).is_file()]
        selected = select_heavy_tests(changed_files, config, repo_root=resolved_root)
    except SelectorError as error:
        print(f"HEAVY selector fail closed: {error}", file=sys.stderr)
        return 2

    if selected:
        print("\n".join(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
