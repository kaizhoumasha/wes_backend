"""Celery 任务族迁移到单一异步运行时后的静态与注册合同。"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_MODULES = ("core", "handling", "runtime_inbox", "sys", "workline")


def _task_tree(module_name: str) -> ast.Module:
    path = REPO_ROOT / "src" / "celery_app" / "tasks" / f"{module_name}.py"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


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
