"""RuntimeInbox Celery task (Plan Task 6).

消费 RuntimeInbox 表中 RECEIVED 状态行 → 转 PROCESSING (token fencing) →
调用 RuntimeInboxProcessorService.process_claimed → 终态写回.

claim-one / process-one 循环, 每条 timeout INBOX_PROCESS_TIMEOUT_SECONDS.

本 task 是 RuntimeInbox 主链路收束后的入口 (Task 6), 全部走 RuntimeInboxService
(`src.app.runtime.orchestration.consumers.runtime_inbox_service`),
不再使用 legacy `inbox_service` (WorklineInboxService). 任何误用 legacy inbox_service
(claim_pending_messages / mark_as_processed / park_for_retry 等) 都会导致:
- 写入旧 WorklineInbox 表, 而当前 claim 的是 RuntimeInbox id
- 跨表终态写回失败, RuntimeInbox 行永远卡在 PROCESSING

Task 7b 验证: 此文件 0 处使用 legacy `inbox_service` (grep inbox_service 返回的 5 行
全部是 `runtime_inbox_service`)。无需修复。
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from typing import Any

from celery import Task

# 预加载外键目标模型, 确保独立 Celery worker 进程内 mapper/metadata 完整注册.
from src.app.device.models.command import DeviceCommand as _DeviceCommand  # noqa: F401
from src.celery_app.app import celery_app
from src.core.logger import logger
from src.database import db as db_module

_WORKLINE_TASK_LOOP: asyncio.AbstractEventLoop | None = None


class RuntimeInboxTask(Task):
    """RuntimeInbox 任务基类 - 提供数据库会话管理."""

    def __init__(self) -> None:
        super().__init__()
        self._db: Any | None = None

    @property
    def db(self) -> Any:
        """懒加载数据库会话."""
        if self._db is None:
            _lazy_init_db()
            session_local = db_module.AsyncSessionLocal
            if session_local is None:
                raise RuntimeError("数据库未初始化, 请先调用 init_db()")
            self._db = session_local()
        return self._db

    def cleanup(self) -> None:
        """清理资源."""
        if self._db:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._db.close())
            except Exception as exc:
                logger.warning(f"关闭任务数据库会话失败: {exc}")
            finally:
                loop.close()
                self._db = None

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        _ = args, kwargs, einfo
        self.cleanup()
        logger.error(f"任务 {task_id} 失败: {exc}")

    def on_success(
        self,
        retval: Any,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        _ = retval, args, kwargs
        self.cleanup()
        logger.info(f"任务 {task_id} 成功完成")


def _get_sync_event_loop() -> asyncio.AbstractEventLoop:
    global _WORKLINE_TASK_LOOP
    try:
        _ = asyncio.get_running_loop()
    except RuntimeError:
        if _WORKLINE_TASK_LOOP is None or _WORKLINE_TASK_LOOP.is_closed():
            _WORKLINE_TASK_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_WORKLINE_TASK_LOOP)
        return _WORKLINE_TASK_LOOP
    raise RuntimeError("当前事件循环正在运行, 无法同步执行 RuntimeInbox Celery 任务")


def _lazy_init_db(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """在直接调用或 Worker 子进程未完成 signal 初始化时懒初始化数据库."""
    if db_module.AsyncSessionLocal is not None:
        return
    from src.database.db import init_db

    init_loop = loop or _get_sync_event_loop()
    init_loop.run_until_complete(init_db())
    logger.info("✓ RuntimeInbox Celery 任务懒初始化数据库连接成功")


def _run_async(coro: Any) -> Any:
    """在 Celery 同步任务中运行异步函数."""
    try:
        loop = _get_sync_event_loop()
        _lazy_init_db(loop)
    except Exception:
        with suppress(Exception):
            cast("Any", coro).close()
        raise
    return loop.run_until_complete(coro)


from typing import cast  # noqa: E402  (after _run_async to keep import grouped)

# ============================================================
# 任务结果 TypedDict
# ============================================================

# RuntimeInbox claim / 终态写回 5 态结果统计.
ClaimBatchResult = dict[str, int]


# ============================================================
# 主任务: process_runtime_inbox_batch
# ============================================================


@celery_app.task(
    name="src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch",
    base=RuntimeInboxTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_runtime_inbox_batch(self: RuntimeInboxTask, limit: int = 10) -> ClaimBatchResult:
    """顺序 claim 并处理 RuntimeInbox 表 (Plan Task 6).

    claim-one / process-one 循环, 单条 INBOX_PROCESS_TIMEOUT_SECONDS 超时.
    """
    logger.debug(f"开始处理 RuntimeInbox, limit={limit}")

    async def _process() -> ClaimBatchResult:
        async with self.db as db:
            from src.app.runtime.orchestration.consumers.runtime_inbox_service import runtime_inbox_service
            from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
                RuntimeInboxProcessorBridge,
            )
            from src.app.workline.constants import (
                INBOX_PROCESS_TIMEOUT_SECONDS,
                WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
            )

            result: ClaimBatchResult = {
                "processed": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
                "resource_wait": 0,
            }
            processor = RuntimeInboxProcessorBridge()

            remaining = limit
            while remaining > 0:
                processor_token = str(uuid.uuid4())
                claims = await runtime_inbox_service.claim_for_processing(
                    db,
                    limit=1,
                    processor_token=processor_token,
                    stale_after_seconds=WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
                )
                if not claims:
                    break

                claim = claims[0]
                # claim 的 PROCESSING/token/attempt 必须先独立提交。后续 processor
                # rollback 只回滚业务处理事务，lease 保留到 stale recovery。
                await db.commit()
                try:
                    message_result = await asyncio.wait_for(
                        processor.process_claimed(db, claim=claim),
                        timeout=INBOX_PROCESS_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    # processor 被单条 timeout 取消后不越权写终态；已提交的
                    # PROCESSING lease 不受本次 rollback 影响，由 stale recovery 重开。
                    await db.rollback()
                    logger.error(f"RuntimeInbox {claim.get('id')} 处理超时")
                    result["processed"] += 1
                    result["failed"] += 1
                except Exception:
                    await db.rollback()
                    raise
                else:
                    for key in result:
                        result[key] += message_result.get(key, 0)
                remaining -= 1

            # 回收 stale leases
            try:
                recovered = await runtime_inbox_service.recover_stale_leases(
                    db,
                    stale_after_seconds=WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
                    limit=100,
                )
                if recovered > 0:
                    logger.info(f"回收了 {recovered} 个 stale lease")
            except Exception as exc:
                logger.warning(f"回收 stale lease 失败: {exc}")

            await db.commit()
            return result

    try:
        result = _run_async(_process())
        if result.get("processed", 0) > 0:
            logger.info(f"RuntimeInbox 处理完成: {result}")
        else:
            logger.debug(f"RuntimeInbox 处理完成: {result}")
        return result
    except Exception as exc:
        logger.error(f"RuntimeInbox 处理失败: {exc}")
        retries = int(getattr(self.request, "retries", 0) or 0)
        countdown = 5 * (2**retries)
        raise self.retry(exc=exc, countdown=countdown) from None


# ============================================================
# 信号: process_signal
# ============================================================


@celery_app.task(
    name="src.celery_app.tasks.runtime_inbox.process_signal",
    base=RuntimeInboxTask,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_signal(self: RuntimeInboxTask, payload: dict[str, Any]) -> None:
    logger.info(f"runtime_inbox process_signal 接收到 payload: {payload}")


__all__ = [
    "ClaimBatchResult",
    "RuntimeInboxTask",
    "process_runtime_inbox_batch",
    "process_signal",
]
