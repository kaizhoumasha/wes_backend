"""Celery prefork 子进程唯一的 asyncio 运行时。"""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypeVar

from src.core.logger import logger
from src.database.db import close_db, init_db
from src.database.redis_client import redis_manager

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

INITIALIZATION_TIMEOUT_SECONDS = 3.0
REDIS_PING_TIMEOUT_SECONDS = 1.0
SHUTDOWN_STAGE_TIMEOUT_SECONDS = 1.0

T = TypeVar("T")


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
        self._owner_pid: int | None = None
        self._state_lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._abandoned_runners: list[asyncio.Runner] = []

    @property
    def state(self) -> RuntimeState:
        with self._state_lock:
            return self._state

    @staticmethod
    def _assert_sync_entrypoint() -> None:
        try:
            asyncio.get_running_loop()
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
    async def _wait_for_without_cancel_wait(awaitable: Coroutine[Any, Any, T], timeout: float) -> T:
        """超时后只发出取消，不等待目标协程完成其取消清理。"""
        task = asyncio.create_task(awaitable)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except BaseException:
            task.cancel()
            raise

    @staticmethod
    async def _wait_task_without_cancel_wait(awaitable: Coroutine[Any, Any, T], timeout: float) -> T:
        """用 asyncio.wait 提供不受 wait_for test double 影响的 DB 硬边界。"""
        task = asyncio.create_task(awaitable)
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if task not in done:
            task.cancel()
            raise TimeoutError
        return task.result()

    @staticmethod
    async def _initialize_infrastructure(deadline: float, progress: dict[str, bool]) -> None:
        database_budget = max(deadline - time.monotonic(), 0.0)
        await CeleryAsyncRuntime._wait_task_without_cancel_wait(init_db(), database_budget)
        progress["database"] = True

        remaining = max(deadline - time.monotonic(), 0.0)
        if remaining <= 0:
            logger.warning("Worker Redis 初始化跳过：3 秒初始化预算已耗尽，进入降级模式")
            return

        redis_timeout = min(REDIS_PING_TIMEOUT_SECONDS, remaining)
        try:
            await CeleryAsyncRuntime._wait_for_without_cancel_wait(redis_manager.init_redis(), redis_timeout)
        except TimeoutError:
            # init_redis 被取消时会清理其未发布候选资源，无需再次跨阶段关闭。
            logger.warning("Worker Redis 初始化超时，进入降级模式")
        except Exception as exc:
            logger.warning(f"Worker Redis 初始化失败（降级模式）: {exc}")
            cleanup_budget = max(deadline - time.monotonic(), 0.0)
            if cleanup_budget > 0:
                try:
                    await CeleryAsyncRuntime._wait_for_without_cancel_wait(redis_manager.close_redis(), cleanup_budget)
                except Exception as cleanup_exc:
                    logger.warning(f"Worker Redis 初始化失败后的清理未完成: {cleanup_exc}")

    @staticmethod
    def _close_runner_best_effort(runner: asyncio.Runner) -> None:
        try:
            runner.close()
        except BaseException as exc:
            logger.warning(f"Celery asyncio Runner 关闭失败（已忽略）: {exc}")

    def _close_runner_if_safe(self, runner: asyncio.Runner) -> None:
        """初始化失败时仅关闭没有 pending task 的 Runner。"""
        try:
            pending = [task for task in asyncio.all_tasks(runner.get_loop()) if not task.done()]
        except BaseException as exc:
            logger.warning(f"无法确认 Celery asyncio Runner 是否可安全关闭，已放弃: {exc}")
            self._abandoned_runners.append(runner)
            return

        if pending:
            logger.warning(f"Celery asyncio Runner 仍有 {len(pending)} 个 pending task，跳过无界 close")
            # 保留引用直到 child 退出，避免 loop/task 被 GC 时产生残留 warning。
            self._abandoned_runners.append(runner)
            return
        self._close_runner_best_effort(runner)

    @staticmethod
    async def _rollback_failed_initialization(deadline: float) -> None:
        """按 Redis → DB 顺序，在剩余初始化预算内回滚已发布资源。"""
        for name, factory in (("Redis", redis_manager.close_redis), ("database", close_db)):
            remaining = max(deadline - time.monotonic(), 0.0)
            try:
                await CeleryAsyncRuntime._wait_for_without_cancel_wait(factory(), remaining)
            except BaseException as exc:
                logger.warning(f"Celery runtime 初始化失败后的 {name} 回滚未完成: {exc}")

    def initialize(self) -> None:
        """在同步入口中有界初始化 child 的 DB 和可降级 Redis。"""
        self._assert_sync_entrypoint()
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
        progress = {"database": False}
        try:
            runner = asyncio.Runner()
            runner.run(self._initialize_infrastructure(deadline, progress), context=contextvars.Context())
        except BaseException:
            if runner is not None:
                if progress["database"]:
                    try:
                        runner.run(self._rollback_failed_initialization(deadline), context=contextvars.Context())
                    except BaseException as exc:
                        logger.warning(f"Celery runtime 初始化失败回滚编排异常（已忽略）: {exc}")
                self._close_runner_if_safe(runner)
            with self._state_lock:
                self._runner = None
                self._owner_pid = None
                self._state = RuntimeState.NEW
            raise

        with self._state_lock:
            self._runner = runner
            self._owner_pid = os.getpid()
            self._state = RuntimeState.READY

    def run_async(self, factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """在唯一 Runner 上运行 factory 新建的协程，每条消息使用全新 Context。"""
        self._assert_sync_entrypoint()
        with self._state_lock:
            self._assert_owner_pid()
            state = self._state
        if state is RuntimeState.NEW:
            self.initialize()
        elif state is not RuntimeState.READY:
            raise RuntimeError(f"CeleryAsyncRuntime is {state.value}")

        with self._run_lock:
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
            task.cancel()
        _, stubborn = await asyncio.wait(pending, timeout=timeout)

        # 第二次 cancel 用于终止吞掉首次 CancelledError 后继续等待的后台任务，
        # 避免 Runner.close 再次无界等待这些任务。
        for task in stubborn:
            task.cancel()
        if stubborn:
            await asyncio.wait(stubborn, timeout=0)
            await asyncio.sleep(0)
        return any(not task.done() for task in stubborn)

    @staticmethod
    async def _run_shutdown_stage(factory: Callable[[], Coroutine[Any, Any, Any]], name: str) -> None:
        try:
            await CeleryAsyncRuntime._wait_for_without_cancel_wait(factory(), SHUTDOWN_STAGE_TIMEOUT_SECONDS)
        except BaseException as exc:
            logger.warning(f"Celery runtime {name} 清理失败或超时（继续后续阶段）: {exc}")

    def shutdown(self) -> None:
        """按 pending → Redis → DB → Runner 顺序执行分阶段有界清理。"""
        self._assert_sync_entrypoint()
        with self._state_lock:
            self._assert_owner_pid()
            if self._state is RuntimeState.CLOSED:
                return
            if self._state is RuntimeState.NEW:
                self._state = RuntimeState.CLOSED
                return
            if self._state is RuntimeState.INITIALIZING:
                raise RuntimeError("CeleryAsyncRuntime is INITIALIZING")
            if self._state is RuntimeState.CLOSING:
                return
            self._state = RuntimeState.CLOSING
            runner = self._runner

        with self._run_lock:
            if runner is not None:
                try:
                    runner.run(
                        self._cancel_pending_tasks(SHUTDOWN_STAGE_TIMEOUT_SECONDS),
                        context=contextvars.Context(),
                    )
                except BaseException as exc:
                    logger.warning(f"Celery runtime pending task 清理失败（继续）: {exc}")

                runner.run(self._run_shutdown_stage(redis_manager.close_redis, "Redis"), context=contextvars.Context())
                runner.run(self._run_shutdown_stage(close_db, "database"), context=contextvars.Context())
                stubborn_tasks = runner.run(
                    self._cancel_pending_tasks(SHUTDOWN_STAGE_TIMEOUT_SECONDS),
                    context=contextvars.Context(),
                )
                if stubborn_tasks:
                    # Runner.close 会无界等待拒绝取消的任务；此时直接关闭 loop，
                    # 保住 child shutdown 的硬边界，Runner.close 仍是正常路径的 best-effort。
                    try:
                        runner.get_loop().close()
                    except BaseException as exc:
                        logger.warning(f"Celery asyncio loop 强制关闭失败（已忽略）: {exc}")
                else:
                    self._close_runner_best_effort(runner)

        with self._state_lock:
            self._runner = None
            self._owner_pid = None
            self._state = RuntimeState.CLOSED


celery_async_runtime = CeleryAsyncRuntime()


def run_async(factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """统一的 Celery 同步任务到异步运行时入口。"""
    return celery_async_runtime.run_async(factory)


__all__ = ["CeleryAsyncRuntime", "RuntimeState", "celery_async_runtime", "run_async"]
