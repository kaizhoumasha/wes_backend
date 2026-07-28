"""扫描 active surface 中已退役的 WorklineInbox 引用。"""

from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class LegacySignature:
    key: str
    value: str
    reason: str


@dataclass(frozen=True, order=True, slots=True)
class LegacyReference:
    path: str
    line: int
    signature: str
    reason: str


LEGACY_SIGNATURES = (
    LegacySignature("legacy_symbol", "WorklineInbox", "引用已删除的 WorklineInbox symbol"),
    LegacySignature(
        "legacy_repository_symbol",
        "WorklineInboxRepository",
        "引用已删除的 WorklineInboxRepository symbol",
    ),
    LegacySignature("legacy_service_symbol", "WorklineInboxService", "引用已删除的 WorklineInboxService symbol"),
    LegacySignature("legacy_batch_processor_symbol", "InboxBatchProcessor", "引用已删除的 InboxBatchProcessor"),
    LegacySignature("legacy_consumer_symbol", "RuntimeInboxConsumer", "引用已删除的 RuntimeInboxConsumer facade"),
    LegacySignature("legacy_table", "wes_biz.workline_inbox", "引用已删除的旧 Inbox 表"),
    LegacySignature(
        "legacy_callback_owner",
        "callback_ingress_service.inbox_service",
        "引用已删除的 callback inbox service owner",
    ),
    LegacySignature("legacy_create_device", "create_device_event_inbox", "调用已删除的旧 Inbox producer"),
    LegacySignature("legacy_create_result", "create_command_result_inbox", "调用已删除的旧 Inbox producer"),
    LegacySignature("legacy_create_external", "create_external_http_inbox", "调用已删除的旧 Inbox producer"),
    LegacySignature("legacy_create_internal", "create_internal_event_inbox", "调用已删除的旧 Inbox producer"),
    LegacySignature("legacy_create_timeout", "create_timeout_inbox", "调用已删除的旧 Inbox producer"),
    LegacySignature(
        "legacy_task",
        "src.celery_app.tasks.workline.process_inbox_batch",
        "引用已删除的旧 Inbox Celery task",
    ),
    LegacySignature("legacy_task_short", "process_inbox_batch", "引用已删除的旧 Inbox batch task"),
    LegacySignature("legacy_enqueue", "enqueue_workline_inbox", "调用已删除的旧 Inbox enqueue shim"),
    LegacySignature(
        "legacy_model_import",
        "src.app.runtime.orchestration.models.inbox",
        "import 已删除的旧 Inbox model module",
    ),
    LegacySignature(
        "legacy_model_member_import",
        "src.app.runtime.orchestration.models import inbox",
        "import 已删除的旧 Inbox model member",
    ),
    LegacySignature(
        "legacy_repository_import",
        "src.app.runtime.orchestration.repositories.inbox_repository",
        "import 已删除的旧 Inbox repository module",
    ),
    LegacySignature(
        "legacy_repository_member_import",
        "src.app.runtime.orchestration.repositories import inbox_repository",
        "import 已删除的旧 Inbox repository member",
    ),
    LegacySignature(
        "legacy_service_import",
        "src.app.runtime.orchestration.services.inbox.inbox_service",
        "import 已删除的旧 Inbox service module",
    ),
    LegacySignature(
        "legacy_service_member_import",
        "src.app.runtime.orchestration.services.inbox import inbox_service",
        "import 已删除的旧 Inbox service member",
    ),
    LegacySignature(
        "legacy_workline_model_import",
        "src.app.workline.models.inbox",
        "import 已删除的 Workline Inbox model module",
    ),
    LegacySignature(
        "legacy_workline_model_member_import",
        "src.app.workline.models import inbox",
        "import 已删除的 Workline Inbox model member",
    ),
    LegacySignature(
        "legacy_workline_repository_import",
        "src.app.workline.repositories.inbox_repository",
        "import 已删除的 Workline Inbox repository module",
    ),
    LegacySignature(
        "legacy_workline_repository_member_import",
        "src.app.workline.repositories import inbox_repository",
        "import 已删除的 Workline Inbox repository member",
    ),
    LegacySignature(
        "legacy_workline_service_import",
        "src.app.workline.services.inbox_service",
        "import 已删除的 Workline Inbox service module",
    ),
    LegacySignature(
        "legacy_workline_service_member_import",
        "src.app.workline.services import inbox_service",
        "import 已删除的 Workline Inbox service member",
    ),
    LegacySignature(
        "legacy_workline_batch_processor_import",
        "src.app.workline.services.inbox_batch_processor",
        "import 已删除的 Workline Inbox batch processor module",
    ),
    LegacySignature(
        "legacy_workline_batch_processor_member_import",
        "src.app.workline.services import inbox_batch_processor",
        "import 已删除的 Workline Inbox batch processor member",
    ),
    LegacySignature(
        "legacy_batch_processor_import",
        "src.app.runtime.orchestration.services.inbox.inbox_batch_processor",
        "import 已删除的 Runtime Inbox batch processor module",
    ),
    LegacySignature(
        "legacy_batch_processor_member_import",
        "src.app.runtime.orchestration.services.inbox import inbox_batch_processor",
        "import 已删除的 Runtime Inbox batch processor member",
    ),
    LegacySignature(
        "legacy_consumer_import",
        "src.app.runtime.orchestration.consumers.runtime_inbox_consumer",
        "import 已删除的 Runtime Inbox consumer facade module",
    ),
    LegacySignature(
        "legacy_consumer_member_import",
        "src.app.runtime.orchestration.consumers import runtime_inbox_consumer",
        "import 已删除的 Runtime Inbox consumer facade member",
    ),
    LegacySignature(
        "legacy_consumer_repository_import",
        "src.app.runtime.orchestration.consumers.runtime_inbox_repository",
        "import 已删除的 Runtime Inbox consumer repository module",
    ),
    LegacySignature(
        "legacy_consumer_repository_member_import",
        "src.app.runtime.orchestration.consumers import runtime_inbox_repository",
        "import 已删除的 Runtime Inbox consumer repository member",
    ),
    LegacySignature(
        "legacy_claim_repository_import",
        "src.app.runtime.orchestration.repositories.runtime_inbox_claim_repository",
        "import 已删除的 Runtime Inbox claim repository module",
    ),
    LegacySignature(
        "legacy_claim_repository_member_import",
        "src.app.runtime.orchestration.repositories import runtime_inbox_claim_repository",
        "import 已删除的 Runtime Inbox claim repository member",
    ),
)

# 当前事实源使用显式文件清单；archive/superpowers plan/spec 不属于 current docs。
CURRENT_DOC_FILES = (
    "docs/architecture/file_index.md",
    "docs/architecture/runtime-orchestration-spec.md",
    "docs/architecture/runtime-ownership-map.md",
    "docs/business/e2e_conveyor_plan.md",
    "docs/business/workline_business_data_event_flow_spec.md",
    "docs/business/workline_runtime_workflow_guide.md",
    "docs/contracts/observability-contract.md",
    "docs/architecture/adr/2026-05-26-wms-integration-domain.md",
)
DEFAULT_SCAN_ROOTS = ("src", "tests", "scripts")

# 精确到“文件 + 签名”的历史/负向证据 allowlist；不跳过整文件。
_ALL_SIGNATURE_KEYS = frozenset(signature.key for signature in LEGACY_SIGNATURES)
ALLOWED_EVIDENCE: dict[str, frozenset[str]] = {
    "scripts/workline_inbox_retirement_guardrail.py": _ALL_SIGNATURE_KEYS,
    "tests/architecture/test_workline_inbox_retirement_guardrail.py": _ALL_SIGNATURE_KEYS,
    "migrations/versions/20260711_1819_ec426c628516_retire_workline_inbox.py": _ALL_SIGNATURE_KEYS,
    "migrations/versions/retire_workline_inbox.py": _ALL_SIGNATURE_KEYS,
    "tests/integration/test_runtime_inbox_migration_postgresql.py": _ALL_SIGNATURE_KEYS,
    "tests/deployment/test_retire_workline_inbox_migration.py": _ALL_SIGNATURE_KEYS,
    "tests/deployment/test_runtime_inbox_celery_cutover.py": frozenset(
        {"legacy_task", "legacy_task_short", "legacy_enqueue"}
    ),
    "tests/api/test_runtime_inbox_enqueue_contract.py": frozenset({"legacy_enqueue"}),
    "tests/sys/test_outbox_delivery.py": frozenset({"legacy_enqueue"}),
    "tests/deployment/test_reset_runtime_data.py": frozenset({"legacy_symbol", "legacy_table"}),
    "scripts/generate_legacy_matrix.py": frozenset(
        {
            "legacy_batch_processor_symbol",
            "legacy_repository_import",
            "legacy_service_symbol",
            "legacy_service_import",
            "legacy_workline_batch_processor_import",
            "legacy_workline_repository_import",
            "legacy_workline_service_import",
        }
    ),
    "scripts/architecture-guardrails.sh": frozenset({"legacy_consumer_repository_import"}),
    "tests/architecture/test_legacy_matrix_contract.py": frozenset(
        {"legacy_batch_processor_symbol", "legacy_workline_batch_processor_import"}
    ),
    "tests/architecture/test_legacy_runtime_import_guardrail.py": frozenset({"legacy_consumer_symbol"}),
    "tests/architecture/test_runtime_inbox_repository_consumer_guardrail.py": frozenset(
        {"legacy_claim_repository_import", "legacy_consumer_repository_import"}
    ),
    "tests/architecture/test_runtime_inbox_processor_ownership.py": frozenset({"legacy_batch_processor_symbol"}),
    "tests/architecture/test_workline_service_shim_contract.py": frozenset({"legacy_batch_processor_symbol"}),
    "docs/architecture/file_index.md": frozenset(
        {"legacy_batch_processor_symbol", "legacy_consumer_symbol", "legacy_symbol"}
    ),
    "docs/architecture/runtime-ownership-map.md": frozenset({"legacy_consumer_symbol"}),
    "docs/architecture/runtime-orchestration-spec.md": frozenset({"legacy_symbol", "legacy_table"}),
}


def _signature_pattern(signature: LegacySignature) -> re.Pattern[str]:
    """生成只跨标点/引号/拼接符的模式，避免把远距离普通单词拼成命中。"""

    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", signature.value)
    words = re.findall(r"[A-Za-z0-9]+", camel_split)
    separator = r"(?:|[\s]*['\"`+._/\\-][\s'\"`+._/\\-]*)" if signature.key.endswith("_symbol") else r"[\s'\"`+._/\\-]*"
    expression = separator.join(re.escape(word) for word in words)
    return re.compile(rf"(?<![A-Za-z0-9_]){expression}(?![A-Za-z0-9_])", re.IGNORECASE)


_SIGNATURE_PATTERNS = tuple((signature, _signature_pattern(signature)) for signature in LEGACY_SIGNATURES)
_IMPORT_MODULE_SIGNATURES: dict[str, LegacySignature] = {}
_IMPORT_MEMBER_SIGNATURES: dict[tuple[str, str], LegacySignature] = {}
for _signature in LEGACY_SIGNATURES:
    if not _signature.key.endswith("_import"):
        continue
    if " import " in _signature.value:
        _module, _member = _signature.value.split(" import ", maxsplit=1)
        _IMPORT_MEMBER_SIGNATURES[(_module, _member)] = _signature
    else:
        _IMPORT_MODULE_SIGNATURES[_signature.value] = _signature


def _is_archived_or_plan(path: str) -> bool:
    parts = Path(path).parts
    return "archive" in parts or ("superpowers" in parts and ("plans" in parts or "specs" in parts))


def _iter_policy_files(
    repo_root: Path,
    roots: tuple[str, ...],
    *,
    include_default_files: bool,
) -> list[Path]:
    files: set[Path] = set()
    for root_name in roots:
        root = repo_root / root_name
        if not root.exists():
            continue
        relative_root = root.resolve().relative_to(repo_root.resolve())
        top_level = relative_root.parts[0] if relative_root.parts else ""
        suffixes = (".py", ".sh") if top_level == "scripts" else ((".md",) if top_level == "docs" else (".py",))
        files.update(
            path for path in root.rglob("*") if path.is_symlink() or (path.is_file() and path.suffix in suffixes)
        )
    if include_default_files:
        files.update(repo_root / relative for relative in CURRENT_DOC_FILES if (repo_root / relative).is_file())
        historical = ("migrations/versions/20260711_1819_ec426c628516_retire_workline_inbox.py",)
        files.update(repo_root / relative for relative in historical if (repo_root / relative).is_file())
    return sorted(files)


def _path_boundary_error(*, path: Path, display_path: str, repo_root: Path) -> LegacyReference | None:
    """同时拒绝词法越界和解析后越界的 root/symlink，避免越界读取。"""

    repo_lexical = repo_root if repo_root.is_absolute() else Path.cwd() / repo_root
    path_lexical = path if path.is_absolute() else Path.cwd() / path
    repo_parts = repo_lexical.parts
    path_parts = path_lexical.parts
    lexically_contained = path_parts[: len(repo_parts)] == repo_parts
    depth = 0
    if lexically_contained:
        for part in path_parts[len(repo_parts) :]:
            if part == "..":
                if depth == 0:
                    lexically_contained = False
                    break
                depth -= 1
            elif part not in {"", "."}:
                depth += 1
    if not lexically_contained:
        return LegacyReference(
            display_path,
            1,
            "policy_error",
            "扫描路径词法上越过仓库边界，拒绝读取",
        )

    try:
        resolved = path.resolve()
        repo_resolved = repo_root.resolve()
    except (OSError, RuntimeError) as exc:
        return LegacyReference(
            display_path,
            1,
            "policy_error",
            f"扫描路径解析失败，拒绝读取: {exc}",
        )
    if resolved.is_relative_to(repo_resolved):
        return None
    return LegacyReference(
        display_path,
        1,
        "policy_error",
        "扫描路径解析到仓库外，拒绝越界读取",
    )


def _directory_symlink_error(*, path: Path, display_path: str, repo_root: Path) -> LegacyReference | None:
    repo_lexical = repo_root if repo_root.is_absolute() else Path.cwd() / repo_root
    path_lexical = path if path.is_absolute() else Path.cwd() / path
    relative_parts = path_lexical.parts[len(repo_lexical.parts) :]
    current = repo_lexical
    for index, part in enumerate(relative_parts):
        if part in {"", "."}:
            continue
        if part == "..":
            current = current.parent
            continue
        current /= part
        if not current.is_symlink():
            continue
        is_final_component = not any(remaining not in {"", "."} for remaining in relative_parts[index + 1 :])
        if is_final_component and current.is_file():
            # 仓库内最终 file symlink 仍按其仓库路径扫描；目录 symlink 一律不展开。
            continue
        break
    else:
        return None
    return LegacyReference(
        display_path,
        1,
        "policy_error",
        "扫描 root/目录项是 directory symlink，拒绝展开以免缩小或越过扫描边界",
    )


def _resolve_import_from_module(node: ast.ImportFrom, relative: str) -> str | None:
    """把绝对/相对 ImportFrom 解析为仓库内完整模块名。"""

    if node.level == 0:
        return node.module

    package_parts = list(Path(relative).parent.parts)
    parents_to_drop = node.level - 1
    if parents_to_drop > len(package_parts):
        return None
    if parents_to_drop:
        package_parts = package_parts[:-parents_to_drop]
    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


ImportSpan = tuple[int, int, int, int]


def _fold_static_string(node: ast.AST) -> str | None:
    """只折叠纯静态字符串 AST，不解析名称、不调用函数。"""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_static_string(node.left)
        right = _fold_static_string(node.right)
        return None if left is None or right is None else left + right
    if not isinstance(node, ast.JoinedStr):
        return None

    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
            continue
        if not isinstance(value, ast.FormattedValue) or value.conversion != -1 or value.format_spec is not None:
            return None
        folded = _fold_static_string(value.value)
        if folded is None:
            return None
        parts.append(folded)
    return "".join(parts)


def _node_span(node: ast.AST) -> ImportSpan:
    return (
        node.lineno,  # type: ignore[attr-defined]
        node.col_offset,  # type: ignore[attr-defined]
        node.end_lineno or node.lineno,  # type: ignore[attr-defined]
        node.end_col_offset or node.col_offset,  # type: ignore[attr-defined]
    )


def _find_python_import_references(
    *,
    relative: str,
    content: str,
    strict_policy: bool,
) -> tuple[list[LegacyReference], tuple[ImportSpan, ...]]:
    """使用 AST 识别真实 import 与纯静态字符串；regex 补充路径和非静态文本引用。"""

    try:
        tree = ast.parse(content, filename=relative)
    except SyntaxError as exc:
        if not strict_policy:
            return [], ()
        return (
            [
                LegacyReference(
                    relative,
                    exc.lineno or 1,
                    "policy_error",
                    f"Python 语法解析失败，拒绝跳过 legacy import 检查: {exc.msg}",
                )
            ],
            (),
        )

    findings: set[LegacyReference] = set()
    ast_spans: set[ImportSpan] = set()
    for node in ast.walk(tree):
        node_findings: set[LegacyReference] = set()
        if isinstance(node, ast.Import):
            for alias in node.names:
                signature = _IMPORT_MODULE_SIGNATURES.get(alias.name)
                if signature is not None:
                    node_findings.add(LegacyReference(relative, node.lineno, signature.key, signature.reason))
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(node, relative)
            if module is not None:
                module_signature = _IMPORT_MODULE_SIGNATURES.get(module)
                if module_signature is not None:
                    node_findings.add(
                        LegacyReference(relative, node.lineno, module_signature.key, module_signature.reason)
                    )
                for alias in node.names:
                    signature = _IMPORT_MEMBER_SIGNATURES.get((module, alias.name))
                    if signature is not None:
                        node_findings.add(LegacyReference(relative, node.lineno, signature.key, signature.reason))
        if not node_findings:
            continue
        findings.update(node_findings)
        ast_spans.add(_node_span(node))

    nodes = list(ast.walk(tree))
    parents = {id(child): parent for parent in nodes for child in ast.iter_child_nodes(parent)}
    static_values = {id(node): value for node in nodes if (value := _fold_static_string(node)) is not None}
    for node in nodes:
        value = static_values.get(id(node))
        if value is None:
            continue
        ancestor = parents.get(id(node))
        has_static_ancestor = False
        while ancestor is not None:
            if id(ancestor) in static_values:
                has_static_ancestor = True
                break
            ancestor = parents.get(id(ancestor))
        if has_static_ancestor:
            continue
        matched = [signature for signature, pattern in _SIGNATURE_PATTERNS if pattern.search(value)]
        if any(signature.key.endswith("_import") for signature in matched):
            matched = [signature for signature in matched if not signature.key.endswith("_symbol")]
        if not matched:
            continue
        findings.update(
            LegacyReference(relative, node.lineno, signature.key, signature.reason)  # type: ignore[attr-defined]
            for signature in matched
        )
        ast_spans.add(_node_span(node))
    return sorted(findings), tuple(sorted(ast_spans))


def _match_position(content: str, start: int) -> tuple[int, int]:
    line = content.count("\n", 0, start) + 1
    line_start = content.rfind("\n", 0, start) + 1
    return line, start - line_start


def _position_in_import_span(line: int, column: int, spans: tuple[ImportSpan, ...]) -> bool:
    for start_line, start_column, end_line, end_column in spans:
        if (start_line, start_column) <= (line, column) < (end_line, end_column):
            return True
    return False


def _first_regex_match(
    *,
    pattern: re.Pattern[str],
    content: str,
    suppress_import_spans: tuple[ImportSpan, ...],
) -> re.Match[str] | None:
    for match in pattern.finditer(content):
        line, column = _match_position(content, match.start())
        if not _position_in_import_span(line, column, suppress_import_spans):
            return match
    return None


def find_legacy_references(
    *,
    repo_root: Path = REPO_ROOT,
    roots: tuple[str, ...] | None = None,
) -> list[LegacyReference]:
    """按统一策略返回未被精确证据 allowlist 覆盖的引用。"""

    strict_policy = roots is None
    findings: set[LegacyReference] = set()
    requested_roots = DEFAULT_SCAN_ROOTS if roots is None else roots
    safe_roots: list[str] = []
    for root_name in requested_roots:
        root_path = repo_root / root_name
        display_path = Path(root_name).as_posix()
        boundary_error = _path_boundary_error(
            path=root_path,
            display_path=display_path,
            repo_root=repo_root,
        )
        if boundary_error is not None:
            findings.add(boundary_error)
            continue
        directory_symlink_error = _directory_symlink_error(
            path=root_path,
            display_path=display_path,
            repo_root=repo_root,
        )
        if directory_symlink_error is not None:
            findings.add(directory_symlink_error)
            continue
        safe_roots.append(root_name)
    if strict_policy:
        for relative in CURRENT_DOC_FILES:
            if not (repo_root / relative).is_file():
                findings.add(
                    LegacyReference(
                        relative,
                        1,
                        "policy_error",
                        "显式 current doc 不存在，拒绝缩小 legacy 扫描面",
                    )
                )
    for path in _iter_policy_files(
        repo_root,
        tuple(safe_roots),
        include_default_files=strict_policy,
    ):
        relative = path.relative_to(repo_root).as_posix()
        boundary_error = _path_boundary_error(path=path, display_path=relative, repo_root=repo_root)
        if boundary_error is not None:
            findings.add(boundary_error)
            continue
        directory_symlink_error = _directory_symlink_error(
            path=path,
            display_path=relative,
            repo_root=repo_root,
        )
        if directory_symlink_error is not None:
            findings.add(directory_symlink_error)
            continue
        # pathlib.rglob 默认不递归 directory symlink；上方同时将其作为 policy error 报告。
        if not path.is_file():
            continue
        if _is_archived_or_plan(relative):
            continue
        content = path.read_text(encoding="utf-8")
        allowed = ALLOWED_EVIDENCE.get(relative, frozenset())
        ast_spans: tuple[ImportSpan, ...] = ()
        if path.suffix == ".py":
            ast_references, ast_spans = _find_python_import_references(
                relative=relative,
                content=content,
                strict_policy=strict_policy,
            )
            for reference in ast_references:
                if reference.signature not in allowed:
                    findings.add(reference)
        for signature, pattern in _SIGNATURE_PATTERNS:
            content_match = _first_regex_match(
                pattern=pattern,
                content=content,
                suppress_import_spans=ast_spans,
            )
            path_match = None
            if signature.key in {"legacy_symbol", "legacy_repository_symbol", "legacy_service_symbol"}:
                path_match = pattern.search(path.name)
            elif signature.key.endswith("_import"):
                path_match = pattern.search(relative.replace("/", "."))
            if content_match is None and path_match is None:
                continue
            if signature.key in allowed:
                continue
            line = 1 if content_match is None else content.count("\n", 0, content_match.start()) + 1
            findings.add(LegacyReference(relative, line, signature.key, signature.reason))
    return sorted(findings)


def main(argv: list[str] | None = None, *, repo_root: Path = REPO_ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("text", "tsv"), default="text")
    args = parser.parse_args(argv)
    findings = find_legacy_references(repo_root=repo_root)
    for finding in findings:
        if args.format == "tsv":
            print(f"{finding.path}\t{finding.line}\t{finding.reason}")
        else:
            print(f"{finding.path}:{finding.line}: {finding.reason}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
