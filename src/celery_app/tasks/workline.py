"""
作业线编排 Celery 任务

消费 WorklineInbox 消息，调用 OrchestratorService 进行处理。

设计参考: 设计文档 phase2-orchestrator
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict, cast

from celery import Task
from loguru import logger

# 预加载外键目标模型，确保独立 Celery worker 进程内 mapper/metadata 完整注册。
from src.app.device.models.command import DeviceCommand  # noqa: F401
from src.app.device.models.device import Device  # noqa: F401
from src.celery_app.app import celery_app
from src.workline_runtime.orchestrator import OrchestratorResult, OrchestratorService

# ============================================
# 类型定义
# ============================================


class ProcessResult(TypedDict):
    """处理结果"""

    processed: int
    success: int
    failed: int
    skipped: int


class ScanResult(TypedDict):
    """扫描结果"""

    scanned: int
    timeouts_created: int
    errors: int


class DispatchResult(TypedDict):
    """派发结果"""

    dispatched: int
    success: int
    failed: int
    skipped: int


class LoadedEntities(TypedDict):
    """加载的关联实体"""

    session: Any | None
    workline: Any | None
    devices_by_role: dict[str, list[Any]]
    services: Any | None


# ============================================
# 辅助函数
# ============================================


def _resolve_int_pk(entity: Any, *field_names: str) -> int | None:
    """从实体上提取真实整型主键，兼容旧测试替身对象。"""
    for field_name in field_names:
        value = getattr(entity, field_name, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None


def _should_resolve_session(inbox: Any) -> bool:
    """仅在具备足够归属信息时才触发 SessionResolver。"""
    raw_payload = getattr(inbox, "payload_json", None)
    payload: dict[str, Any] = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
    kind = getattr(getattr(inbox, "kind", None), "value", getattr(inbox, "kind", None))

    if kind == "DEVICE_EVENT":
        return bool(
            _resolve_int_pk(inbox, "device_id")
            or payload.get("device_code")
            or payload.get("business_key")
        )
    if kind == "EXTERNAL_HTTP":
        return bool(getattr(inbox, "correlation_id", None))
    if kind in {"TIMER_TIMEOUT", "MANUAL_HOLD", "MANUAL_RESUME", "MANUAL_CANCEL", "REPLAY_REQUEST"}:
        return _resolve_int_pk(inbox, "session_id") is not None
    return False


def _resolve_device_role(device: Any) -> str | None:
    """优先提取真实字符串角色，兼容旧测试里的 role 字段。"""
    for field_name in ("device_role", "role"):
        value = getattr(device, field_name, None)
        if isinstance(value, str) and value:
            return value
    return None


def _ensure_non_empty_retry_result(task_name: str, result: dict[str, int], retries: int) -> None:
    """避免“重试后空跑”被 Celery 误记为成功。"""
    if retries <= 0:
        return

    if any(value > 0 for value in result.values()):
        return

    raise RuntimeError(
        f"{task_name} returned an empty result after {retries} retries; refusing to mark it as succeeded"
    )


def _run_async(coro: Awaitable[Any]) -> Any:
    """在 Celery 同步任务中运行异步函数"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _resolve_required_pk(entity: Any, entity_name: str, *field_names: str) -> int:
    """提取必需的整型主键，不存在时抛出 ValueError。"""
    pk = _resolve_int_pk(entity, *field_names)
    if pk is None:
        raise ValueError(f"{entity_name} missing primary key")
    return pk


async def _load_related_entities(db: Any, inbox: Any) -> LoadedEntities:
    """加载关联实体

    Args:
        db: 数据库会话
        inbox: Inbox 消息

    Returns:
        加载的实体字典
    """
    from src.app.device.repositories import DeviceRepository
    from src.app.workline.repositories import WorkLineRepository
    from src.app.workline.repositories.session_repository import (
        WorklineSessionRepository,
    )
    from src.workline_runtime.session_resolver import session_resolver

    session_repo = WorklineSessionRepository()
    workline_repo = WorkLineRepository()
    device_repo = DeviceRepository()

    # 加载 Session
    session = None
    if inbox.session_id:
        session = await session_repo.get_by_id(db, inbox.session_id)

    # 加载 Workline
    workline = None
    if inbox.workline_id:
        workline = await workline_repo.get_by_id(db, inbox.workline_id)
    elif session is not None and getattr(session, "workline_id", None):
        workline = await workline_repo.get_by_id(db, session.workline_id)

    device = None
    if inbox.device_id:
        device = await device_repo.get_by_id(db, inbox.device_id)
    elif isinstance(getattr(inbox, "payload_json", None), dict):
        device_code = inbox.payload_json.get("device_code")
        if device_code:
            device = await device_repo.get_by_device_code(db, device_code)
            if device:
                inbox.device_id = device.id

    if workline is None and device and device.work_line_id:
        workline = await workline_repo.get_by_id(db, device.work_line_id)
        if workline:
            inbox.workline_id = workline.id

    # 加载设备按角色分组
    devices_by_role: dict[str, list[Any]] = {}
    workline_pk = _resolve_int_pk(workline, "id")
    if workline and workline_pk is not None:
        devices = await device_repo.get_by_work_line_id(db, workline_pk)
        for device in devices:
            role = _resolve_device_role(device)
            if role:
                if role not in devices_by_role:
                    devices_by_role[role] = []
                devices_by_role[role].append(device)

    if session is None and workline is not None and _should_resolve_session(inbox):
        session = await session_resolver.resolve_or_create(
            db=db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
        )
        session_pk = _resolve_int_pk(session, "id", "session_id")
        if session_pk is not None:
            inbox.session_id = session_pk

    # 服务容器（Phase 2 简化实现）
    services: dict[str, Any] = {}

    return {
        "session": session,
        "workline": workline,
        "devices_by_role": devices_by_role,
        "services": services,
    }


# ============================================
# Celery 任务
# ============================================


class WorklineTask(Task):
    """作业线任务基类 - 提供数据库会话管理"""

    _db: Any | None = None

    @property
    def db(self) -> Any:
        """懒加载数据库会话"""
        if self._db is None:
            from src.database.db import AsyncSessionLocal as AsyncSessionLocalDynamic

            session_local = AsyncSessionLocalDynamic
            if session_local is None:
                raise RuntimeError("数据库未初始化，请先调用 init_db()")
            self._db = session_local()
        return self._db

    def cleanup(self) -> None:
        """清理资源"""
        if self._db:
            asyncio.get_event_loop().run_until_complete(self._db.close())
            self._db = None

    def on_failure(
        self, exc: Exception, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any], einfo: Any
    ) -> None:
        """任务失败时清理资源"""
        _ = args, kwargs, einfo
        self.cleanup()
        logger.error(f"任务 {task_id} 失败: {exc}")

    def on_success(self, retval: Any, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """任务成功时清理资源"""
        _ = retval, args, kwargs
        self.cleanup()
        logger.info(f"任务 {task_id} 成功完成")


class ProcessInboxMessages:
    """处理 Inbox 消息的内部类（用于测试）"""

    @staticmethod
    async def _process_batch(db: Any, limit: int = 10) -> ProcessResult:
        """批量处理 Inbox 消息

        Args:
            db: 数据库会话
            limit: 批处理数量

        Returns:
            处理结果统计
        """
        from src.app.workline.services.inbox_service import inbox_service

        result: ProcessResult = {
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }

        # 获取待处理消息
        messages = await inbox_service.get_new_messages(db, limit=limit)

        for inbox in messages:
            inbox_pk_text = str(getattr(inbox, "id", "unknown"))
            try:
                inbox_pk = _resolve_required_pk(inbox, "inbox", "id", "inbox_id")
                # 尝试标记为处理中（并发控制）
                processor_token = str(uuid.uuid4())
                try:
                    _ = await inbox_service.mark_as_processing(db, inbox_pk, processor_token)
                except ValueError:
                    # 已被其他 worker 处理
                    result["skipped"] += 1
                    continue

                # 加载关联实体
                entities = await _load_related_entities(db, inbox)

                # 调用编排器
                orchestrator = OrchestratorService()
                orch_result: OrchestratorResult = await orchestrator.process_inbox(
                    session=entities["session"],
                    workline=entities["workline"],
                    inbox=inbox,
                    devices_by_role=entities["devices_by_role"],
                    services=entities["services"],
                    correlation_id=inbox.correlation_id or "",
                )

                # 根据结果更新状态
                if orch_result.success:
                    _ = await inbox_service.mark_as_processed(db, inbox_pk)
                    result["success"] += 1
                    logger.info(f"Inbox {inbox_pk} 处理成功")
                else:
                    error_msg = orch_result.error or "Unknown error"
                    _ = await inbox_service.mark_as_failed(db, inbox_pk, error_msg)
                    result["failed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")

                result["processed"] += 1

            except Exception as e:
                logger.exception(f"Inbox {inbox_pk_text} 处理异常")
                try:
                    inbox_pk = _resolve_int_pk(inbox, "id", "inbox_id")
                    if inbox_pk is not None:
                        _ = await inbox_service.mark_as_failed(db, inbox_pk, str(e))
                except Exception as mark_error:
                    logger.warning(f"Inbox {inbox_pk_text} 异常补记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

        # 提交事务
        await db.commit()

        return result


# 创建实例用于测试
process_inbox_messages = ProcessInboxMessages()


class TimeoutScanner:
    """超时扫描器内部类（用于测试）"""

    @staticmethod
    async def _scan(db: Any, limit: int = 100) -> ScanResult:
        """扫描超时 Session 并创建 Timeout Inbox

        Args:
            db: 数据库会话
            limit: 批处理数量

        Returns:
            扫描结果统计
        """
        from src.app.workline.repositories.session_repository import (
            WorklineSessionRepository,
        )
        from src.app.workline.services.inbox_service import inbox_service

        result: ScanResult = {
            "scanned": 0,
            "timeouts_created": 0,
            "errors": 0,
        }

        # 获取超时 Session
        session_repo = WorklineSessionRepository()
        sessions = await session_repo.get_timed_out_sessions(db, limit=limit)
        result["scanned"] = len(sessions)

        for session in sessions:
            try:
                session_pk = _resolve_int_pk(session, "id", "session_id")
                if session_pk is None:
                    raise ValueError("Timed out session missing primary key")

                # 创建超时 Inbox
                _ = await inbox_service.create_timeout_inbox(
                    db=db,
                    session_id=session_pk,
                    workline_id=session.workline_id,
                    deadline_at=session.deadline_at,
                    correlation_id=session.correlation_id,
                )
                result["timeouts_created"] += 1
                logger.info(f"Session {session_pk} 超时，已创建 Timeout Inbox")
            except Exception as e:
                session_pk = _resolve_int_pk(session, "id", "session_id")
                logger.error(f"Session {session_pk or 'unknown'} 创建超时 Inbox 失败: {e}")
                result["errors"] += 1

        # 提交事务
        await db.commit()

        return result


# 创建实例用于测试
scan_timeouts = TimeoutScanner()


@celery_app.task(
    name="src.celery_app.tasks.workline.process_inbox_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_inbox_batch(self: WorklineTask, limit: int = 10) -> ProcessResult:
    """批量处理 Inbox 消息 (Celery 任务入口)

    Args:
        limit: 批处理数量，默认 10

    Returns:
        处理结果统计
    """
    logger.info(f"开始处理 Inbox 消息, limit={limit}")

    async def _process() -> ProcessResult:
        async with self.db as db:
            return await process_inbox_messages._process_batch(db, limit=limit)

    try:
        result = _run_async(_process())
        _ensure_non_empty_retry_result(
            "process_inbox_batch",
            result,
            int(getattr(self.request, "retries", 0) or 0),
        )
        logger.info(f"Inbox 处理完成: {result}")
        return result
    except Exception as e:
        logger.error(f"Inbox 处理失败: {e}")
        countdown = 5 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


@celery_app.task(
    name="src.celery_app.tasks.workline.scan_timeouts_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def scan_timeouts_batch(self: WorklineTask, limit: int = 100) -> ScanResult:
    """扫描超时 Session (Celery 任务入口)

    Args:
        limit: 批处理数量，默认 100

    Returns:
        扫描结果统计
    """
    logger.info(f"开始扫描超时 Session, limit={limit}")

    async def _scan() -> ScanResult:
        async with self.db as db:
            return await scan_timeouts._scan(db, limit=limit)

    try:
        result = _run_async(_scan())
        _ensure_non_empty_retry_result(
            "scan_timeouts_batch",
            result,
            int(getattr(self.request, "retries", 0) or 0),
        )
        logger.info(f"超时扫描完成: {result}")
        return result
    except Exception as e:
        logger.error(f"超时扫描失败: {e}")
        countdown = 60 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


class OutboxDispatcher:
    """Outbox 派发器内部类（用于测试）"""

    MAX_RETRIES = 3

    @staticmethod
    async def _dispatch(db: Any, limit: int = 50) -> DispatchResult:
        """派发 Outbox 消息

        Args:
            db: 数据库会话
            limit: 批处理数量

        Returns:
            派发结果统计
        """
        from src.app.workline.repositories.outbox_repository import (
            WorklineOutboxRepository,
        )

        result: DispatchResult = {
            "dispatched": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }

        # 获取待派发消息
        outbox_repo = WorklineOutboxRepository()
        messages = await outbox_repo.get_pending_messages(db, limit=limit)

        for outbox in messages:
            outbox_pk_text = str(getattr(outbox, "id", "unknown"))
            try:
                outbox_pk = _resolve_required_pk(outbox, "outbox", "id", "outbox_id")
                # 尝试标记为派发中（并发控制）
                updated = await outbox_repo.mark_as_dispatching(db, outbox_pk)
                if updated is None:
                    # 已被其他 worker 处理
                    result["skipped"] += 1
                    continue

                # 派发消息
                success = await OutboxDispatcher._dispatch_single(db, outbox)

                if success:
                    _ = await outbox_repo.mark_as_sent(db, outbox_pk)
                    result["success"] += 1
                    logger.info(f"Outbox {outbox_pk} 派发成功")
                else:
                    _ = await outbox_repo.mark_as_failed(
                        db, outbox_pk, "Dispatch failed", OutboxDispatcher.MAX_RETRIES
                    )
                    result["failed"] += 1
                    logger.warning(f"Outbox {outbox_pk} 派发失败")

                result["dispatched"] += 1

            except Exception as e:
                logger.error(f"Outbox {outbox_pk_text} 派发异常: {e}")
                try:
                    outbox_pk = _resolve_int_pk(outbox, "id", "outbox_id")
                    if outbox_pk is not None:
                        _ = await outbox_repo.mark_as_failed(
                            db, outbox_pk, str(e), OutboxDispatcher.MAX_RETRIES
                        )
                except Exception as mark_error:
                    logger.warning(f"Outbox {outbox_pk_text} 异常补记失败: {mark_error}")
                result["failed"] += 1
                result["dispatched"] += 1

        # 提交事务
        await db.commit()

        return result

    @staticmethod
    async def _dispatch_single(db: Any, outbox: Any) -> bool:
        """派发单个 Outbox 消息

        Args:
            db: 数据库会话
            outbox: Outbox 消息

        Returns:
            是否成功
        """
        from src.app.workline.models.outbox import DispatchType

        if outbox.dispatch_type == DispatchType.DEVICE_COMMAND:
            return await OutboxDispatcher._dispatch_device_command(db, outbox)
        if outbox.dispatch_type == DispatchType.EXTERNAL_HTTP:
            return await OutboxDispatcher._dispatch_external_http(outbox)
        if outbox.dispatch_type == DispatchType.INTERNAL_SIGNAL:
            return await OutboxDispatcher._dispatch_internal_signal(outbox)
        logger.warning(f"未知的派发类型: {outbox.dispatch_type}")
        return False

    @staticmethod
    async def _dispatch_device_command(db: Any, outbox: Any) -> bool:
        """派发设备指令"""
        try:
            import httpx

            from src.app.device.repositories.device_repository import device_repository
            from src.app.device.services.device_service import device_service

            send_command_obj = getattr(device_service, "send_command", None)
            if callable(send_command_obj):
                send_command = cast("Callable[..., Awaitable[object] | object]", send_command_obj)
                command_result: Awaitable[object] | object = send_command(
                    device_code=outbox.target_code,
                    command_data=outbox.payload_json,
                )
                if isinstance(command_result, Awaitable):
                    awaited_result = await cast("Awaitable[object]", command_result)
                    if not isinstance(awaited_result, dict):
                        awaited_result = None
                    result_dict = cast("dict[str, object] | None", awaited_result)
                else:
                    result_dict = cast("dict[str, object] | None", command_result if isinstance(command_result, dict) else None)

                if result_dict is not None:
                    return bool(result_dict.get("success", False))

            device = await device_repository.get_by_device_code(db, outbox.target_code)
            if device is None or not device.host or not device.port:
                logger.error(f"设备不存在或通信配置不完整: {outbox.target_code}")
                return False

            scheme = str(getattr(device, "protocol", "http")).lower()
            url = f"{scheme}://{device.host}:{device.port}/api/v1/device/command"
            async with httpx.AsyncClient(timeout=(device.timeout or 10000) / 1000) as client:
                response = await client.post(url, json=outbox.payload_json)
                return response.status_code == 200
        except Exception as e:
            logger.error(f"设备指令派发失败: {e}")
            return False

    @staticmethod
    async def _dispatch_external_http(outbox: Any) -> bool:
        """派发外部 HTTP 调用"""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    outbox.target_code,
                    json=outbox.payload_json,
                )
                return response.status_code == 200
        except Exception as e:
            logger.error(f"外部 HTTP 派发失败: {e}")
            return False

    @staticmethod
    async def _dispatch_internal_signal(outbox: Any) -> bool:
        """派发内部信号"""
        try:
            from src.celery_app.app import celery_app

            # 发送到目标服务的任务队列
            celery_app.send_task(
                f"src.celery_app.tasks.{outbox.target_code}.process_signal",
                kwargs={"payload": outbox.payload_json},
            )
            return True
        except Exception as e:
            logger.error(f"内部信号派发失败: {e}")
            return False


# 创建实例用于测试
dispatch_outbox = OutboxDispatcher()


@celery_app.task(
    name="src.celery_app.tasks.workline.dispatch_outbox_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def dispatch_outbox_batch(self: WorklineTask, limit: int = 50) -> DispatchResult:
    """批量派发 Outbox 消息 (Celery 任务入口)

    Args:
        limit: 批处理数量，默认 50

    Returns:
        派发结果统计
    """
    logger.info(f"开始派发 Outbox 消息, limit={limit}")

    async def _dispatch() -> DispatchResult:
        async with self.db as db:
            return await dispatch_outbox._dispatch(db, limit=limit)

    try:
        result = _run_async(_dispatch())
        logger.info(f"Outbox 派发完成: {result}")
        return result
    except Exception as e:
        logger.error(f"Outbox 派发失败: {e}")
        countdown = 10 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


# ============================================
# 导出
# ============================================

__all__ = [
    "OutboxDispatcher",
    "ProcessInboxMessages",
    "TimeoutScanner",
    "_load_related_entities",
    "dispatch_outbox",
    "dispatch_outbox_batch",
    "process_inbox_batch",
    "process_inbox_messages",
    "scan_timeouts",
    "scan_timeouts_batch",
]
