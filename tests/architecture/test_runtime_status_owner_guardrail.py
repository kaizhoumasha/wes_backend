"""Runtime status ownership guardrail."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROJECTION_SERVICE = Path("src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py")
READONLY_PROJECTION_VIEWS = {
    Path("src/app/runtime/orchestration/services/query/runtime_query_service.py"),
    Path("src/app/runtime/orchestration/services/trace/trace_query_service.py"),
}
OWNER_SENSITIVE_ROOTS = (
    Path("src/app/workline"),
    Path("src/app/runtime/capabilities/material_flow"),
)
MIGRATIONS_DIR = Path("migrations/versions")


def _token(*parts: str) -> str:
    return "".join(parts)


_HANDLING_QUEUE_MEMBERSHIP_TABLE = _token("bin", "_", "transit", "_", "memberships")


def _source(path: Path) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _migration_text_containing(*tokens: str) -> str:
    migrations = sorted((REPO_ROOT / MIGRATIONS_DIR).glob("*.py"))
    assert migrations, "migrations/versions 下必须存在 Alembic revision"
    for migration in reversed(migrations):
        migration_text = migration.read_text(encoding="utf-8")
        if all(token in migration_text for token in tokens):
            return migration_text
    raise AssertionError(f"未找到同时包含目标标识的迁移: {tokens}")


def _parse_source(source: str) -> ast.Module:
    return ast.parse(source)


def _is_runtime_status_attr(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "runtime_status"


def _is_runtime_status_snapshot_call(node: ast.AST) -> bool:
    if isinstance(node, ast.Await):
        node = node.value
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Attribute) and node.func.attr == "runtime_status_snapshot")
        or (isinstance(node.func, ast.Name) and node.func.id == "runtime_status_snapshot")
    )


def _runtime_status_snapshot_vars(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_runtime_status_snapshot_call(node.value):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _is_runtime_status_snapshot_call(node.value)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return names


def _is_projection_snapshot_attr(node: ast.Attribute, snapshot_vars: set[str]) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id in snapshot_vars


def _is_runtime_status_getattr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "runtime_status"
    )


def _direct_runtime_status_reads(tree: ast.AST) -> list[int]:
    snapshot_vars = _runtime_status_snapshot_vars(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if _is_runtime_status_getattr(node) or (
            _is_runtime_status_attr(node)
            and not isinstance(node.ctx, ast.Store)
            and not _is_projection_snapshot_attr(node, snapshot_vars)
        ):
            lines.append(node.lineno)
    return sorted(set(lines))


def _target_contains_runtime_status_attr(target: ast.AST) -> bool:
    return any(_is_runtime_status_attr(node) for node in ast.walk(target))


def _is_runtime_status_setattr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "runtime_status"
    )


def _is_values_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "values"


def _dict_has_runtime_status_key(node: ast.AST) -> bool:
    return isinstance(node, ast.Dict) and any(
        isinstance(key, ast.Constant) and key.value == "runtime_status" for key in node.keys
    )


def _values_call_writes_runtime_status(node: ast.AST) -> bool:
    return _is_values_call(node) and (
        any(keyword.arg == "runtime_status" for keyword in node.keywords)
        or any(_dict_has_runtime_status_key(arg) for arg in node.args)
    )


def _direct_runtime_status_writes(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = [node.target] if isinstance(node, ast.AnnAssign | ast.AugAssign) else list(node.targets)
            if any(_target_contains_runtime_status_attr(target) for target in targets):
                lines.append(node.lineno)
        elif _is_runtime_status_setattr(node) or _values_call_writes_runtime_status(node):
            lines.append(node.lineno)
    return sorted(set(lines))


def test_runtime_status_writes_are_centralized_in_projection_service() -> None:
    """runtime_status 直接写入只能出现在 runtime/orchestration 兼容投影服务。"""
    violations: list[str] = []
    for rel_path in sorted(Path("src/app").glob("**/*.py")):
        path = REPO_ROOT / rel_path
        try:
            source = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # 其它 guardrail 会短暂创建并删除违规 fixture；不应把 collection 缓存转成竞态。
            source = None
        if source is None:
            continue
        tree = ast.parse(source, filename=str(rel_path))
        if rel_path == PROJECTION_SERVICE:
            continue
        lines = _direct_runtime_status_writes(tree)
        violations.extend(f"{rel_path}:{line}" for line in lines)

    assert not violations, "WorkLine 运行态直接写入必须集中到 projection service:\n  " + "\n  ".join(violations)


def test_runtime_status_scan_tolerates_disappearing_guardrail_fixture(monkeypatch) -> None:
    missing = Path("src/app/runtime/orchestration/services/_deleted_guardrail_fixture.py")
    monkeypatch.setattr(Path, "glob", lambda _self, _pattern: iter((missing,)))

    test_runtime_status_writes_are_centralized_in_projection_service()


def test_workline_model_no_longer_declares_runtime_status_column() -> None:
    source = _source(Path("src/app/workline/models/workline.py"))

    assert "runtime_status:" not in source
    assert "WorkLineRuntimeStatus" not in source


def test_runtime_status_projection_service_no_longer_writes_workline_field() -> None:
    source = _source(PROJECTION_SERVICE)

    assert "workline.runtime_status =" not in source
    assert 'getattr(workline, "runtime_status"' not in source


def test_runtime_status_migration_mentions_runtime_status_targets() -> None:
    migration_text = _migration_text_containing(
        "workline_runtime_status_projections",
        _HANDLING_QUEUE_MEMBERSHIP_TABLE,
    )

    assert "workline_runtime_status_projections" in migration_text
    assert _HANDLING_QUEUE_MEMBERSHIP_TABLE in migration_text
    assert "runtime_status" in migration_text


def test_runtime_status_write_detector_catches_nested_assignment_and_setattr() -> None:
    """写入扫描器必须覆盖非顶层 target 与 setattr 写入。"""
    tree = _parse_source(
        """
(workline.runtime_status, ignored) = ("READY", 1)
setattr(workline, "runtime_status", "READY")
"""
    )

    assert _direct_runtime_status_writes(tree) == [2, 3]


def test_runtime_status_write_detector_catches_values_updates_but_not_plain_payload() -> None:
    """仅在明显写入口上下文拦截 dict key，避免误伤普通 payload/read-model fixture。"""
    write_tree = _parse_source(
        """
update_stmt.values(runtime_status="READY")
update_stmt.values({"runtime_status": "READY"})
"""
    )
    payload_tree = _parse_source(
        """
payload = {"runtime_status": "READY"}
"""
    )

    assert _direct_runtime_status_writes(write_tree) == [2, 3]
    assert _direct_runtime_status_writes(payload_tree) == []


def test_runtime_status_read_detector_only_allows_projection_snapshot_variables() -> None:
    """只有来自 runtime_status_snapshot(...) 调用的数据流变量可以读 .runtime_status。"""
    allowed_tree = _parse_source(
        """
runtime_snapshot = projection.runtime_status_snapshot(workline)
status = runtime_snapshot.runtime_status
"""
    )
    violation_tree = _parse_source(
        """
workline_snapshot = object()
status = workline_snapshot.runtime_status
"""
    )

    assert _direct_runtime_status_reads(allowed_tree) == []
    assert _direct_runtime_status_reads(violation_tree) == [3]


def test_workline_and_material_flow_owner_sensitive_paths_use_projection_snapshot_for_runtime_status() -> None:
    """WorkLine 域与 material-flow capability 不能直接读取 runtime_status 作归属判断。

    允许列表保持很窄：projection service 是唯一字段读写入口；query/trace 是
    runtime/orchestration 只读展示层，且另有专门测试要求它们通过 snapshot 暴露。
    """
    violations: list[str] = []
    owner_sensitive_files = sorted(
        rel_path
        for root in OWNER_SENSITIVE_ROOTS
        for rel_path in root.rglob("*.py")
        if rel_path != PROJECTION_SERVICE and rel_path not in READONLY_PROJECTION_VIEWS
    )
    for rel_path in owner_sensitive_files:
        tree = ast.parse(_source(rel_path), filename=str(rel_path))
        lines = _direct_runtime_status_reads(tree)
        violations.extend(f"{rel_path}:{line}" for line in lines)

    assert not violations, (
        "归属敏感路径必须通过 WorkLineRuntimeStatusProjectionService snapshot/readiness 读取 runtime_status:\n  "
        + "\n  ".join(violations)
    )


def test_runtime_query_and_trace_only_expose_runtime_status_snapshot() -> None:
    """query/trace 只允许展示 projection snapshot，不直接把 WorkLine 字段当事实源。"""
    violations: list[str] = []
    for rel_path in sorted(READONLY_PROJECTION_VIEWS):
        tree = ast.parse(_source(rel_path), filename=str(rel_path))
        lines = _direct_runtime_status_reads(tree)
        violations.extend(f"{rel_path}:{line}" for line in lines)

    assert not violations, "query/trace 应通过 runtime_status_snapshot 暴露兼容字段:\n  " + "\n  ".join(violations)
