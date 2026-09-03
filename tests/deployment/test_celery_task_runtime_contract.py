"""Celery 任务族迁移到单一异步运行时后的静态与注册合同。"""

from __future__ import annotations

import ast
import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_MODULES = ("core", "device_command", "execution", "safety", "transport", "wms_confirmation")
ASYNC_TASKS = {
    "core": ("health_check", "clear_cache", "send_notification"),
    "device_command": (
        "dispatch_device_commands_batch",
        "process_device_evidence_batch",
        "reconcile_device_commands_batch",
    ),
    "execution": ("process_execution_facts_batch",),
    "safety": ("drain_safety_incidents_batch",),
    "transport": (
        "advance_transport_debug_runs_batch",
        "submit_transport_tasks_batch",
        "process_transport_evidence_batch",
        "reconcile_transport_tasks_batch",
        "publish_transport_outcomes_batch",
    ),
    "wms_confirmation": ("dispatch_wms_confirmations_batch",),
}
DB_TASKS = {
    "core": ("health_check", "send_notification"),
    "safety": ("drain_safety_incidents_batch",),
}


def _task_tree(module_name: str) -> ast.Module:
    path = REPO_ROOT / "src" / "celery_app" / "tasks" / f"{module_name}.py"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _expression_path(expression: ast.expr) -> tuple[str, ...] | None:
    if isinstance(expression, ast.Name):
        return (expression.id,)
    if isinstance(expression, ast.Attribute):
        prefix = _expression_path(expression.value)
        return (*prefix, expression.attr) if prefix else None
    return None


def _approved_call_paths(tree: ast.Module, module_name: str, symbol_name: str) -> set[tuple[str, ...]]:
    """只接受从批准模块导入的 binding，避免把任意同名方法当成目标调用。"""
    paths: set[tuple[str, ...]] = set()
    parent_module, imported_module = module_name.rsplit(".", 1)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            paths.update(((alias.asname or alias.name),) for alias in node.names if alias.name == symbol_name)
        elif isinstance(node, ast.ImportFrom) and node.module == parent_module:
            paths.update(
                ((alias.asname or alias.name), symbol_name) for alias in node.names if alias.name == imported_module
            )
        elif isinstance(node, ast.Import):
            paths.update(
                ((alias.asname, symbol_name) if alias.asname else (*module_name.split("."), symbol_name))
                for alias in node.names
                if alias.name == module_name
            )
    return paths


@pytest.mark.parametrize("module_name", TASK_MODULES)
def test_task_modules_do_not_own_private_event_loops(module_name: str) -> None:
    tree = _task_tree(module_name)
    forbidden_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "asyncio"
            and node.func.attr in {"get_event_loop", "new_event_loop", "set_event_loop"}
        ):
            forbidden_calls.append(f"asyncio.{node.func.attr}")
        if node.func.attr == "run_until_complete":
            forbidden_calls.append("loop.run_until_complete")

    assert forbidden_calls == [], f"{module_name} 必须通过统一 CeleryAsyncRuntime 执行异步工作"


@pytest.mark.parametrize("module_name", TASK_MODULES)
def test_task_modules_do_not_cache_database_sessions_on_task_instances(module_name: str) -> None:
    tree = _task_tree(module_name)
    violations: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr in {"_db", "db"}
        ):
            violations.append(f"self.{node.attr}")
    assert violations == [], f"{module_name} 每条消息必须通过 get_db_context() 创建 task-local Session"


@pytest.mark.parametrize(
    ("module_name", "task_name"),
    [(module_name, task_name) for module_name, task_names in ASYNC_TASKS.items() for task_name in task_names],
)
def test_each_async_sync_task_calls_run_async_once_with_a_factory(module_name: str, task_name: str) -> None:
    tree = _task_tree(module_name)
    function = _function(tree, task_name)
    approved_paths = _approved_call_paths(tree, "src.celery_app.async_runtime", "run_async")
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _expression_path(node.func) in approved_paths
    ]

    assert len(calls) == 1, f"{module_name}.{task_name} 必须恰好一次进入统一 runtime"
    assert calls[0].args, "run_async 必须接收 coroutine factory"
    assert not isinstance(calls[0].args[0], ast.Call), "不得在进入 runtime 前创建 coroutine"


@pytest.mark.parametrize(
    ("module_name", "task_name"),
    [(module_name, task_name) for module_name, task_names in DB_TASKS.items() for task_name in task_names],
)
def test_database_task_async_body_uses_task_local_get_db_context(module_name: str, task_name: str) -> None:
    tree = _task_tree(module_name)
    function = _function(tree, task_name)
    approved_paths = _approved_call_paths(tree, "src.database.db", "get_db_context")
    context_calls: list[ast.Call] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.AsyncWith):
            continue
        for item in node.items:
            context_calls.extend(
                call
                for call in ast.walk(item.context_expr)
                if isinstance(call, ast.Call) and _expression_path(call.func) in approved_paths
            )

    assert len(context_calls) == 1, f"{module_name}.{task_name} 必须在 async with 中创建 task-local Session"


TASK_CONTRACTS: dict[str, tuple[str, int, int, str]] = {
    "src.celery_app.tasks.core.health_check": ("default", 3, 180, "core"),
    "src.celery_app.tasks.core.clear_cache": ("default", 3, 180, "core"),
    "src.celery_app.tasks.core.send_notification": ("default", 3, 180, "core"),
    "src.celery_app.tasks.core.cleanup_old_logs": ("default", 3, 180, "core"),
}


def _route_queue(task_name: str, task_routes: dict[str, dict[str, Any]]) -> str:
    module = TASK_CONTRACTS[task_name][3]
    return str(task_routes[f"src.celery_app.tasks.{module}.*"]["queue"])


def test_task_names_routes_retry_ack_and_time_limit_contracts_are_preserved() -> None:
    from src.celery_app.app import celery_app
    from src.celery_app.config import task_routes

    celery_app.loader.import_default_modules()
    assert bool(celery_app.conf.task_acks_late) is True

    for task_name, (queue, max_retries, retry_delay, _module) in TASK_CONTRACTS.items():
        task = celery_app.tasks[task_name]
        assert task.name == task_name
        assert _route_queue(task_name, task_routes) == queue
        assert task.max_retries == max_retries
        assert task.default_retry_delay == retry_delay
        assert task.time_limit is None
        assert task.soft_time_limit is None


def test_retired_smt_handoff_task_is_not_registered_or_scheduled() -> None:
    """零插件基线不得继续装配 SMT handoff 定时任务。"""

    from src.celery_app.app import celery_app
    from src.celery_app.config import beat_schedule

    task_name = "src.celery_app.tasks.workline.scan_smt_inbound_handoff_demands_batch"
    celery_app.loader.import_default_modules()

    assert task_name not in celery_app.tasks
    assert all(item.get("task") != task_name for item in beat_schedule.values())
    assert not (REPO_ROOT / "src/celery_app/tasks/workline.py").exists()


@pytest.mark.parametrize(
    ("task_name", "service_method"),
    (
        ("submit_transport_tasks_batch", "submit_pending_tasks"),
        ("process_transport_evidence_batch", "process_pending_evidence"),
        ("reconcile_transport_tasks_batch", "reconcile_overdue_tasks"),
    ),
)
def test_transport_tasks_use_the_current_child_runtime_service_with_fixed_batch(
    monkeypatch: pytest.MonkeyPatch,
    task_name: str,
    service_method: str,
) -> None:
    module = importlib.import_module("src.celery_app.tasks.transport")
    service = SimpleNamespace(
        submit_pending_tasks=AsyncMock(return_value=11),
        process_pending_evidence=AsyncMock(return_value=12),
        reconcile_overdue_tasks=AsyncMock(return_value=13),
    )
    runtime = SimpleNamespace(service=service)
    monkeypatch.setattr(module, "celery_async_runtime", SimpleNamespace(transport_runtime=runtime))
    monkeypatch.setattr(module, "run_async", lambda factory: asyncio.run(factory()))

    result = getattr(module, task_name).run(limit=100)

    assert result in {11, 12, 13}
    getattr(service, service_method).assert_awaited_once_with(100)
    for other_method in {
        "submit_pending_tasks",
        "process_pending_evidence",
        "reconcile_overdue_tasks",
    } - {service_method}:
        getattr(service, other_method).assert_not_awaited()


@pytest.mark.parametrize(
    ("task_name", "service_method", "invalid_limit"),
    [
        (task_name, service_method, invalid_limit)
        for task_name, service_method in (
            ("submit_transport_tasks_batch", "submit_pending_tasks"),
            ("process_transport_evidence_batch", "process_pending_evidence"),
            ("reconcile_transport_tasks_batch", "reconcile_overdue_tasks"),
        )
        for invalid_limit in (99, 101)
    ],
)
def test_transport_tasks_reject_non_fixed_batch_before_calling_service(
    monkeypatch: pytest.MonkeyPatch,
    task_name: str,
    service_method: str,
    invalid_limit: int,
) -> None:
    module = importlib.import_module("src.celery_app.tasks.transport")
    service = SimpleNamespace(
        submit_pending_tasks=AsyncMock(return_value=11),
        process_pending_evidence=AsyncMock(return_value=12),
        reconcile_overdue_tasks=AsyncMock(return_value=13),
    )
    runtime = SimpleNamespace(service=service)
    monkeypatch.setattr(module, "celery_async_runtime", SimpleNamespace(transport_runtime=runtime))
    monkeypatch.setattr(module, "run_async", lambda factory: asyncio.run(factory()))

    with pytest.raises(ValueError, match="Transport batch limit must be 100"):
        getattr(module, task_name).run(limit=invalid_limit)

    getattr(service, service_method).assert_not_awaited()


@pytest.mark.parametrize(
    ("task_name", "invalid_limit"),
    [
        (task_name, invalid_limit)
        for task_name in (
            "src.celery_app.tasks.transport.submit_transport_tasks_batch",
            "src.celery_app.tasks.transport.process_transport_evidence_batch",
            "src.celery_app.tasks.transport.reconcile_transport_tasks_batch",
        )
        for invalid_limit in (99, 101)
    ],
)
def test_registered_transport_celery_tasks_reject_non_fixed_batch(
    monkeypatch: pytest.MonkeyPatch,
    task_name: str,
    invalid_limit: int,
) -> None:
    from src.celery_app.app import celery_app
    from src.celery_app.tasks import transport

    service = SimpleNamespace(
        submit_pending_tasks=AsyncMock(return_value=11),
        process_pending_evidence=AsyncMock(return_value=12),
        reconcile_overdue_tasks=AsyncMock(return_value=13),
    )
    monkeypatch.setattr(
        transport,
        "celery_async_runtime",
        SimpleNamespace(transport_runtime=SimpleNamespace(service=service)),
    )
    monkeypatch.setattr(transport, "run_async", lambda factory: asyncio.run(factory()))
    celery_app.loader.import_default_modules()

    with pytest.raises(ValueError, match="Transport batch limit must be 100"):
        result = celery_app.tasks[task_name].apply(kwargs={"limit": invalid_limit})
        result.get(propagate=True)
    for method in (
        service.submit_pending_tasks,
        service.process_pending_evidence,
        service.reconcile_overdue_tasks,
    ):
        method.assert_not_awaited()


def test_transport_tasks_are_registered_and_statically_routed_to_the_single_fulfillment_queue() -> None:
    from src.celery_app.app import celery_app
    from src.celery_app.config import task_routes

    task_names = {
        "src.celery_app.tasks.transport.advance_transport_debug_runs_batch",
        "src.celery_app.tasks.transport.submit_transport_tasks_batch",
        "src.celery_app.tasks.transport.process_transport_evidence_batch",
        "src.celery_app.tasks.transport.reconcile_transport_tasks_batch",
    }
    celery_app.loader.import_default_modules()

    assert task_names <= set(celery_app.tasks)
    assert {task_routes[name]["queue"] for name in task_names} == {"wms-fulfillment"}


def test_transport_debug_run_scanner_uses_current_runtime_with_fixed_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("src.celery_app.tasks.transport")
    debug_run_service = SimpleNamespace(advance_active_runs=AsyncMock(return_value=17))
    runtime = SimpleNamespace(debug_run_service=debug_run_service)
    monkeypatch.setattr(module, "celery_async_runtime", SimpleNamespace(transport_runtime=runtime))
    monkeypatch.setattr(module, "run_async", lambda factory: asyncio.run(factory()))

    assert module.advance_transport_debug_runs_batch.run(limit=100) == 17
    debug_run_service.advance_active_runs.assert_awaited_once_with(100)

    with pytest.raises(ValueError, match="Transport batch limit must be 100"):
        module.advance_transport_debug_runs_batch.run(limit=99)
