"""Celery 任务族迁移到单一异步运行时后的静态与注册合同。"""

from __future__ import annotations

import ast
import asyncio
import importlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_MODULES = ("core", "handling", "runtime_inbox", "sys", "workline")
ASYNC_TASKS = {
    "core": ("health_check", "clear_cache", "send_notification"),
    "runtime_inbox": ("process_runtime_inbox_batch",),
    "sys": (
        "dispatch_system_outbox_batch",
        "dispatch_wms_data_outbox_batch",
        "dispatch_wms_fulfillment_outbox_batch",
    ),
    "workline": (
        "scan_timeouts_batch",
        "scan_device_heartbeats_batch",
        "scan_smt_inbound_handoff_demands_batch",
    ),
}
DB_TASKS = {
    "core": ("health_check", "send_notification"),
    "runtime_inbox": ("process_runtime_inbox_batch",),
    "sys": ASYNC_TASKS["sys"],
    "workline": ASYNC_TASKS["workline"],
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
        if isinstance(node, ast.Attribute) and node.attr == "AsyncSessionLocal":
            violations.append("AsyncSessionLocal")

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


@pytest.mark.parametrize(
    ("module_name", "task_name", "base_countdown"),
    [
        ("runtime_inbox", "process_runtime_inbox_batch", 5),
        ("sys", "dispatch_system_outbox_batch", 10),
        ("workline", "scan_timeouts_batch", 60),
        ("workline", "scan_device_heartbeats_batch", 60),
        ("workline", "scan_smt_inbound_handoff_demands_batch", 60),
    ],
)
def test_retry_countdown_keeps_exponential_backoff_contract(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    task_name: str,
    base_countdown: int,
) -> None:
    module = importlib.import_module(f"src.celery_app.tasks.{module_name}")
    task = getattr(module, task_name)
    retry = MagicMock(side_effect=RuntimeError("retry requested"))

    def fail_runtime(factory_or_coroutine: Any) -> None:
        if asyncio.iscoroutine(factory_or_coroutine):
            factory_or_coroutine.close()
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(module, "run_async", fail_runtime, raising=False)
    monkeypatch.setattr(module, "_run_async", fail_runtime, raising=False)
    monkeypatch.setattr(task, "retry", retry)

    task.push_request(retries=2)
    try:
        with pytest.raises(RuntimeError, match="retry requested"):
            task.run()
    finally:
        task.pop_request()

    assert isinstance(retry.call_args.kwargs["exc"], ConnectionError)
    assert retry.call_args.kwargs["countdown"] == base_countdown * 4


TASK_CONTRACTS: dict[str, tuple[str, int, int, str]] = {
    "src.celery_app.tasks.core.health_check": ("default", 3, 180, "core"),
    "src.celery_app.tasks.core.clear_cache": ("default", 3, 180, "core"),
    "src.celery_app.tasks.core.send_notification": ("default", 3, 180, "core"),
    "src.celery_app.tasks.core.cleanup_old_logs": ("default", 3, 180, "core"),
    "src.celery_app.tasks.core.process_signal": ("default", 3, 10, "core"),
    "src.celery_app.tasks.handling.process_signal": ("celery", 3, 10, "handling"),
    "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch": ("celery", 3, 5, "runtime_inbox"),
    "src.celery_app.tasks.runtime_inbox.process_signal": ("celery", 3, 10, "runtime_inbox"),
    "src.celery_app.tasks.sys.dispatch_system_outbox_batch": ("celery", 3, 10, "sys"),
    "src.celery_app.tasks.sys.process_signal": ("celery", 3, 10, "sys"),
    "src.celery_app.tasks.workline.scan_timeouts_batch": ("celery", 3, 60, "workline"),
    "src.celery_app.tasks.workline.scan_device_heartbeats_batch": ("celery", 3, 60, "workline"),
    "src.celery_app.tasks.workline.scan_smt_inbound_handoff_demands_batch": ("celery", 3, 60, "workline"),
    "src.celery_app.tasks.workline.process_signal": ("celery", 3, 10, "workline"),
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
