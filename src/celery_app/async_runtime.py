"""Celery prefork 子进程唯一的 asyncio 运行时。"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import os
import threading
import time
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import uuid4

from src.core.logger import logger
from src.database.db import close_db, init_db
from src.database.redis_client import redis_manager

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

INITIALIZATION_TIMEOUT_SECONDS = 3.0
REDIS_PING_TIMEOUT_SECONDS = 1.0
SHUTDOWN_STAGE_TIMEOUT_SECONDS = 1.0

T = TypeVar("T")


class _UnterminatedAsyncTaskError(TimeoutError):
    """任务收到取消后仍未终止，当前 Runner 不得继续复用。"""


class RuntimeState(StrEnum):
    NEW = "NEW"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


class CeleryAsyncRuntime:
    """为一个 Celery child 持有一个 Runner，并隔离每条消息的 Context。"""

    def __init__(self) -> None:
        self._state = RuntimeState.NEW
        self._runner: asyncio.Runner | None = None
        self._runner_generation: str | None = None
        self._owner_pid: int | None = None
        self._state_lock = threading.RLock()
        # 生命周期与消息执行统一按 run_lock -> state_lock 取锁，禁止交叉顺序。
        self._run_lock = threading.RLock()
        self._abandoned_runners: list[asyncio.Runner] = []

    @property
    def state(self) -> RuntimeState:
        with self._state_lock:
            return self._state

    @property
    def runner_generation(self) -> str | None:
        """返回成功发布的 Runner generation；未就绪或已关闭时为空。"""
        with self._state_lock:
            return self._runner_generation

    @staticmethod
    def _assert_sync_entrypoint() -> None:
        try:
            _ = asyncio.get_running_loop()
        except RuntimeError:
            return
        raise RuntimeError("CeleryAsyncRuntime cannot be called from a running event loop")

    def _assert_owner_pid(self) -> None:
        if self._owner_pid is not None and self._owner_pid != os.getpid():
            raise RuntimeError(
                f"CeleryAsyncRuntime owner PID mismatch (owner={self._owner_pid}, current={os.getpid()}); "
                "refusing fork-inherited runtime access"
            )

    @staticmethod
    async def _wait_for_without_cancel_wait(
        awaitable: Coroutine[Any, Any, T],
        timeout: float,
        *,
        cancellation_deadline: float | None = None,
    ) -> T:
        """超时后发出取消，并仅让出有限调度轮次观察顶层任务终态。"""
        task = asyncio.create_task(awaitable)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except BaseException as exc:
            _ = task.cancel()
            if cancellation_deadline is not None:
                cancellation_budget = max(cancellation_deadline - time.monotonic(), 0.0)
                if cancellation_budget > 0:
                    _ = await asyncio.wait({task}, timeout=cancellation_budget)
            if not task.done():
                for _ in range(2):
                    await asyncio.sleep(0)
                    if task.done():
                        break
            if not task.done():
                raise _UnterminatedAsyncTaskError("async task ignored cancellation") from exc
            raise

    @staticmethod
    async def _wait_task_without_cancel_wait(awaitable: Coroutine[Any, Any, T], timeout: float) -> T:
        """用 asyncio.wait 提供不受 wait_for test double 影响的 DB 硬边界。"""
        task = asyncio.create_task(awaitable)
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if task not in done:
            _ = task.cancel()
            # 只让出有限的零时长调度轮次，使常规 CancelledError 清理有机会终态；
            # 不等待计时器，因此不扩大 worker 初始化的 3 秒 wall-clock 边界。
            for _ in range(2):
                await asyncio.sleep(0)
                if task.done():
                    break
            raise TimeoutError
        return task.result()

    @staticmethod
    async def _initialize_infrastructure(deadline: float, progress: dict[str, bool]) -> None:
        database_budget = max(deadline - time.monotonic(), 0.0)
        await CeleryAsyncRuntime._wait_task_without_cancel_wait(init_db(), database_budget)
        progress["database"] = True

        from src.app.runtime.system_capabilities.wms.provider_catalog import validate_wms_transport_configuration
        from src.app.wms_integration.query_runtime import (
            bind_wms_data_lane_query_runtime,
            build_wms_data_lane_query_runtime,
        )
        from src.core.conf import settings

        startup = validate_wms_transport_configuration(settings_source=settings)
        bind_wms_data_lane_query_runtime(build_wms_data_lane_query_runtime(startup, settings_source=settings))
        progress["wms_data_lane"] = True

        remaining = max(deadline - time.monotonic(), 0.0)
        if remaining <= 0:
            logger.warning("Worker Redis 初始化跳过：3 秒初始化预算已耗尽，进入降级模式")
            return

        redis_timeout = min(REDIS_PING_TIMEOUT_SECONDS, remaining)
        cleanup_reserve = min(0.01, max(remaining - redis_timeout, 0.0))
        cleanup_budget = max(remaining - redis_timeout - cleanup_reserve, 0.0)
        bounded_initializer = getattr(redis_manager, "init_redis_with_cleanup_budget", None)
        redis_init = (
            bounded_initializer(cleanup_budget) if bounded_initializer is not None else redis_manager.init_redis()
        )
        try:
            await CeleryAsyncRuntime._wait_for_without_cancel_wait(
                redis_init,
                redis_timeout,
                cancellation_deadline=deadline,
            )
        except _UnterminatedAsyncTaskError:
            # 顶层 init_redis 仍可能继续修改 manager，当前 Runner 不得发布为 READY。
            raise
        except TimeoutError:
            # init_redis 被取消时会清理其未发布候选资源，无需再次跨阶段关闭。
            logger.warning("Worker Redis 初始化超时，进入降级模式")
        except Exception as exc:
            logger.warning(f"Worker Redis 初始化失败（降级模式）: type={type(exc).__name__}, error={exc!r}")
            cleanup_budget = max(deadline - time.monotonic(), 0.0)
            if cleanup_budget > 0:
                try:
                    await CeleryAsyncRuntime._wait_for_without_cancel_wait(redis_manager.close_redis(), cleanup_budget)
                except Exception as cleanup_exc:
                    logger.warning(
                        "Worker Redis 初始化失败后的清理未完成: "
                        f"type={type(cleanup_exc).__name__}, error={cleanup_exc!r}"
                    )

    @staticmethod
    def _close_runner_best_effort(runner: asyncio.Runner) -> None:
        try:
            runner.close()
        except BaseException as exc:
            logger.warning(f"Celery asyncio Runner 关闭失败（已忽略）: type={type(exc).__name__}, error={exc!r}")

    def _close_runner_if_safe(self, runner: asyncio.Runner) -> bool:
        """初始化失败时仅关闭没有 pending task 的 Runner。"""
        try:
            pending = [task for task in asyncio.all_tasks(runner.get_loop()) if not task.done()]
        except BaseException as exc:
            logger.warning(
                "无法确认 Celery asyncio Runner 是否可安全关闭，child 必须重启: "
                f"type={type(exc).__name__}, error={exc!r}"
            )
            if not self._abandoned_runners:
                self._abandoned_runners.append(runner)
            return False

        if pending:
            logger.warning(f"Celery asyncio Runner 仍有 {len(pending)} 个 pending task，跳过无界 close；child 必须重启")
            # 保留引用直到 child 退出，避免 loop/task 被 GC 时产生残留 warning。
            if not self._abandoned_runners:
                self._abandoned_runners.append(runner)
            return False
        self._close_runner_best_effort(runner)
        return True

    @staticmethod
    async def _rollback_failed_initialization(deadline: float) -> None:
        """按 Redis → DB 顺序，在剩余初始化预算内回滚已发布资源。"""
        from src.app.wms_integration.query_runtime import close_bound_wms_data_lane_query_runtime

        for name, factory in (
            ("Redis", redis_manager.close_redis),
            ("wms-data", close_bound_wms_data_lane_query_runtime),
            ("database", close_db),
        ):
            remaining = max(deadline - time.monotonic(), 0.0)
            try:
                await CeleryAsyncRuntime._wait_for_without_cancel_wait(factory(), remaining)
            except BaseException as exc:
                logger.warning(
                    f"Celery runtime 初始化失败后的 {name} 回滚未完成: type={type(exc).__name__}, error={exc!r}"
                )

    def initialize(self) -> None:
        """在同步入口中有界初始化 child 的 DB 和可降级 Redis。"""
        self._assert_sync_entrypoint()
        with self._run_lock:
            self._initialize_locked()

    def _initialize_locked(self) -> None:
        """在持有 run lock 时初始化，避免 shutdown 与候选 Runner 发布竞态。"""
        with self._state_lock:
            self._assert_owner_pid()
            if self._state is RuntimeState.READY:
                return
            if self._state is RuntimeState.INITIALIZING:
                raise RuntimeError("CeleryAsyncRuntime is INITIALIZING")
            if self._state is RuntimeState.CLOSING:
                raise RuntimeError("CeleryAsyncRuntime is CLOSING")
            if self._state is RuntimeState.CLOSED:
                raise RuntimeError("CeleryAsyncRuntime is CLOSED")
            self._state = RuntimeState.INITIALIZING

        runner: asyncio.Runner | None = None
        deadline = time.monotonic() + INITIALIZATION_TIMEOUT_SECONDS
        progress = {"database": False, "wms_data_lane": False}
        try:
            runner = asyncio.Runner()
            runner.run(self._initialize_infrastructure(deadline, progress), context=contextvars.Context())
            candidate_runner_generation = uuid4().hex
        except BaseException:
            reusable = runner is None
            if runner is not None:
                if progress["database"]:
                    try:
                        runner.run(self._rollback_failed_initialization(deadline), context=contextvars.Context())
                    except BaseException as exc:
                        logger.warning(
                            f"Celery runtime 初始化失败回滚编排异常（已忽略）: type={type(exc).__name__}, error={exc!r}"
                        )
                reusable = self._close_runner_if_safe(runner)
            with self._state_lock:
                self._runner = None
                self._runner_generation = None
                self._owner_pid = None
                self._state = RuntimeState.NEW if reusable else RuntimeState.CLOSED
            raise

        with self._state_lock:
            self._runner = runner
            self._runner_generation = candidate_runner_generation
            self._owner_pid = os.getpid()
            self._state = RuntimeState.READY

    def run_async(self, factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """在唯一 Runner 上运行 factory 新建的协程，每条消息使用全新 Context。"""
        self._assert_sync_entrypoint()
        with self._run_lock:
            with self._state_lock:
                self._assert_owner_pid()
                state = self._state
            if state is RuntimeState.NEW:
                self._initialize_locked()
            elif state is not RuntimeState.READY:
                raise RuntimeError(f"CeleryAsyncRuntime is {state.value}")

            with self._state_lock:
                self._assert_owner_pid()
                if self._state is not RuntimeState.READY or self._runner is None:
                    raise RuntimeError(f"CeleryAsyncRuntime is {self._state.value}")
                runner = self._runner
            awaitable = factory()
            return runner.run(awaitable, context=contextvars.Context())

    @staticmethod
    async def _cancel_pending_tasks(timeout: float) -> bool:
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
        if not pending:
            return False

        for task in pending:
            _ = task.cancel()
        _, stubborn = await asyncio.wait(pending, timeout=timeout)

        # 第二次 cancel 用于终止吞掉首次 CancelledError 后继续等待的后台任务，
        # 避免 Runner.close 再次无界等待这些任务。
        for task in stubborn:
            _ = task.cancel()
        if stubborn:
            _ = await asyncio.wait(stubborn, timeout=0)
            await asyncio.sleep(0)
        return any(not task.done() for task in stubborn)

    @staticmethod
    async def _run_shutdown_stage(factory: Callable[[], Coroutine[Any, Any, Any]], name: str) -> None:
        try:
            await CeleryAsyncRuntime._wait_for_without_cancel_wait(factory(), SHUTDOWN_STAGE_TIMEOUT_SECONDS)
        except BaseException as exc:
            logger.warning(
                f"Celery runtime {name} 清理失败或超时（继续后续阶段）: type={type(exc).__name__}, error={exc!r}"
            )

    @staticmethod
    def _run_runner_stage(
        runner: asyncio.Runner,
        awaitable: Coroutine[Any, Any, T],
        name: str,
        *,
        failure_result: T,
    ) -> T:
        """隔离单个 shutdown runner.run，确保信号清理继续后续阶段。"""
        try:
            return runner.run(awaitable, context=contextvars.Context())
        except BaseException as exc:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            logger.warning(f"Celery runtime {name} runner.run 失败（继续）: type={type(exc).__name__}, error={exc!r}")
            return failure_result

    def shutdown(self) -> None:
        """按 pending → Redis → DB → Runner 顺序执行分阶段有界清理。"""
        self._assert_sync_entrypoint()
        with self._run_lock:
            self._shutdown_locked()

    def _shutdown_locked(self) -> None:
        """在持有 run lock 时关闭，锁顺序始终保持 run_lock -> state_lock。"""
        runner: asyncio.Runner | None = None
        try:
            with self._state_lock:
                self._assert_owner_pid()
                if self._state is RuntimeState.CLOSED:
                    return
                if self._state is RuntimeState.NEW:
                    return
                if self._state is RuntimeState.INITIALIZING:
                    raise RuntimeError("CeleryAsyncRuntime is INITIALIZING")
                if self._state is RuntimeState.CLOSING:
                    return
                self._state = RuntimeState.CLOSING
                runner = self._runner

            if runner is None:
                return

            _ = self._run_runner_stage(
                runner,
                self._cancel_pending_tasks(SHUTDOWN_STAGE_TIMEOUT_SECONDS),
                "pending task",
                failure_result=True,
            )
            self._run_runner_stage(
                runner,
                self._run_shutdown_stage(redis_manager.close_redis, "Redis"),
                "Redis cleanup",
                failure_result=None,
            )
            from src.app.wms_integration.query_runtime import close_bound_wms_data_lane_query_runtime

            self._run_runner_stage(
                runner,
                self._run_shutdown_stage(close_bound_wms_data_lane_query_runtime, "wms-data"),
                "wms-data cleanup",
                failure_result=None,
            )
            self._run_runner_stage(
                runner,
                self._run_shutdown_stage(close_db, "database"),
                "database cleanup",
                failure_result=None,
            )
            stubborn_tasks = self._run_runner_stage(
                runner,
                self._cancel_pending_tasks(SHUTDOWN_STAGE_TIMEOUT_SECONDS),
                "final pending task",
                failure_result=True,
            )
            if stubborn_tasks:
                # Runner.close 会无界等待拒绝取消的任务；此时直接关闭 loop，
                # 保住 child shutdown 的硬边界，Runner.close 仍是正常路径的 best-effort。
                try:
                    runner.get_loop().close()
                except BaseException as exc:
                    logger.warning(
                        f"Celery asyncio loop 强制关闭失败（已忽略）: type={type(exc).__name__}, error={exc!r}"
                    )
            else:
                self._close_runner_best_effort(runner)
        except BaseException as exc:
            logger.warning(f"Celery runtime shutdown 异常已隔离: type={type(exc).__name__}, error={exc!r}")
        finally:
            with self._state_lock:
                self._runner = None
                self._runner_generation = None
                self._owner_pid = None
                self._state = RuntimeState.CLOSED


celery_async_runtime = CeleryAsyncRuntime()


def run_async(factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """统一的 Celery 同步任务到异步运行时入口。"""
    return celery_async_runtime.run_async(factory)


__all__ = ["CeleryAsyncRuntime", "RuntimeState", "celery_async_runtime", "run_async"]
