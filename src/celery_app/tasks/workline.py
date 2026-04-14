"""
作业线编排 Celery 任务

消费 WorklineInbox 消息，调用 OrchestratorService 进行处理。

设计参考: 设计文档 phase2-orchestrator
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict, cast

from celery import Task

# 预加载外键目标模型，确保独立 Celery worker 进程内 mapper/metadata 完整注册。
from src.app.device.models.command import DeviceCommand  # noqa: F401
from src.app.device.models.device import Device  # noqa: F401
from src.celery_app.app import celery_app
from src.celery_app.constants import (
    DEFAULT_COMMAND_PRIORITY,
    DEFAULT_COMMAND_TIMEOUT_MS,
    EXTERNAL_HTTP_DECISION_TYPE,
    EXTERNAL_HTTP_INBOX_KIND,
    INBOX_PROCESS_TIMEOUT_SECONDS,
)
from src.core.logger import logger
from src.utils.device_cache import workline_device_cache
from src.utils.timezone import timezone
from src.workline_plugin_registry import get_plugin_contract_version
from src.workline_runtime.orchestrator import OrchestratorResult, OrchestratorService
from src.workline_runtime.payloads import SixInOne

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from src.workline_runtime.utils import JsonDict

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


# 常量已提取到 src.celery_app.constants


# ============================================
# 辅助函数
# ============================================


def _resolve_entity_id(entity: Any) -> int | None:
    """从实体上提取真实整型主键。"""
    value = getattr(entity, "id", None)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _should_resolve_session(inbox: Any) -> bool:
    """仅在具备足够归属信息时才触发 SessionResolver。"""
    raw_payload = getattr(inbox, "payload_json", None)
    payload: dict[str, Any] = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
    kind = getattr(getattr(inbox, "kind", None), "value", getattr(inbox, "kind", None))

    if kind == "DEVICE_EVENT":
        return bool(getattr(inbox, "device_id", None) or payload.get("device_code") or payload.get("business_key"))
    if kind == "COMMAND_RESULT":
        return bool(getattr(inbox, "command_id", None) or payload.get("command_code"))
    if kind == EXTERNAL_HTTP_INBOX_KIND:
        return bool(getattr(inbox, "correlation_id", None))
    if kind in {"TIMER_TIMEOUT", "MANUAL_HOLD", "MANUAL_RESUME", "MANUAL_CANCEL", "REPLAY_REQUEST"}:
        return isinstance(getattr(inbox, "session_id", None), int)
    return False


def _resolve_device_role(device: Any) -> str | None:
    """提取设备真实字符串角色。"""
    value = getattr(device, "device_role", None)
    return value if isinstance(value, str) and value else None


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
    _ = field_names
    pk = _resolve_entity_id(entity)
    if pk is None:
        raise ValueError(f"{entity_name} missing primary key")
    return pk


def _payload_dict(raw_payload: Any) -> JsonDict:
    return cast("JsonDict", raw_payload) if isinstance(raw_payload, dict) else {}


def _string_value(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _session_context(session: Any) -> dict[str, Any]:
    raw_context = getattr(session, "context_json", None)
    if isinstance(raw_context, dict):
        return dict(cast("JsonDict", raw_context))
    return {}


def _set_session_context(session: Any, context: dict[str, Any]) -> None:
    session.context_json = context


def _sync_session_contract_snapshot(session: Any, *, workline: Any, context: dict[str, Any]) -> None:
    plugin_key = getattr(session, "plugin_key", None) or getattr(workline, "plugin_key", None)
    if isinstance(plugin_key, str) and plugin_key:
        session.plugin_key = plugin_key

    contract_version = getattr(session, "contract_version", None)
    if not isinstance(contract_version, str) or not contract_version:
        contract_version = get_plugin_contract_version(plugin_key)
        if isinstance(contract_version, str) and contract_version:
            session.contract_version = contract_version

    step_code = context.get("step_code")
    if isinstance(step_code, str) and step_code:
        session.step_code = step_code


def _clear_session_wait(session: Any) -> None:
    session.current_wait_type = None
    session.current_wait_token = None
    session.waiting_since = None
    session.deadline_at = None
    session.awaiting_command_id = None


def _clear_session_failure(session: Any) -> None:
    session.failure_domain = None
    session.failure_code = None
    session.failure_message = None


def _wait_session_status(wait_type: str) -> str:
    if wait_type == "EXTERNAL_HTTP":
        return "WAITING_EXTERNAL"
    return "WAITING_DEVICE_RESULT"


def _device_map_from_roles(devices_by_role: dict[str, list[Any]]) -> dict[int, Any]:
    device_by_id: dict[int, Any] = {}
    for devices in devices_by_role.values():
        for device in devices:
            device_id = _resolve_entity_id(device)
            if device_id is not None:
                device_by_id[device_id] = device
    return device_by_id


def _map_command_task_type(action: str) -> str:
    if action == "PICK_AND_PUT":
        return "PICK_AND_PLACE"
    if action == "MOVE_FORWARD":
        return "PROCESS"
    return action


def _build_command_code(task_type: str) -> str:
    date_str = timezone.now_for_db().strftime("%Y%m%d")
    return f"CMD-{date_str}-{task_type}-{uuid.uuid4().hex[:8].upper()}"


def _normalize_vendor_command_payload(
    parameters: Any,
    *,
    action: str,
    default_command_code: str,
) -> JsonDict:
    """归一化插件产出的设备协议 payload。

    目标是保留 vendor payload 语义，只补齐派发必须字段。
    """

    payload = dict(_payload_dict(parameters))
    payload["command_code"] = _string_value(payload.get("command_code")) or default_command_code
    payload["task_type"] = _string_value(payload.get("task_type"), action)

    if not isinstance(payload.get("priority"), int):
        payload["priority"] = DEFAULT_COMMAND_PRIORITY
    if not isinstance(payload.get("timeout"), int):
        payload["timeout"] = DEFAULT_COMMAND_TIMEOUT_MS
    if not isinstance(payload.get("timestamp"), int):
        payload["timestamp"] = int(timezone.now_utc().timestamp() * 1000)

    return payload


def _build_outbox_payload(command: Any) -> dict[str, Any]:
    command_params = _payload_dict(getattr(command, "params", None))
    if command_params:
        return dict(command_params)

    command_task_type = getattr(command, "task_type", None)
    if isinstance(command_task_type, Enum):
        normalized_task_type = _string_value(command_task_type.value)
    else:
        normalized_task_type = _string_value(command_task_type)

    return {
        "command_code": command.command_code,
        "task_type": normalized_task_type,
        "priority": command.priority,
        "timeout": command.timeout_ms,
        "params": {},
        "timestamp": int(timezone.now_utc().timestamp() * 1000),
    }


async def _add_timeline(db: Any, timeline: Any) -> None:
    from sqlalchemy import func, select

    from src.app.workline.models.timeline import WorklineTimeline

    result = await db.execute(
        select(func.max(WorklineTimeline.seq_no)).where(WorklineTimeline.session_id == timeline.session_id)  # type: ignore[arg-type]
    )
    max_seq_no = result.scalar_one_or_none()
    timeline.seq_no = (max_seq_no or 0) + 1
    db.add(timeline)


async def _apply_orchestrator_effects(  # noqa: PLR0912
    db: Any,
    *,
    session: Any,
    workline: Any,
    inbox: Any,
    devices_by_role: dict[str, list[Any]],
    orch_result: OrchestratorResult,
) -> None:
    from src.app.device.repositories import DeviceRepository
    from src.app.device.repositories.command_repository import DeviceCommandRepository
    from src.app.workline.models.outbox import DispatchType, TargetType, WorklineOutbox
    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage, TimelineStatus
    from src.utils.timezone import timezone
    from src.workline_runtime.timeline_generator import timeline_generator

    device_repo = DeviceRepository()
    command_repo = DeviceCommandRepository()
    device_by_id = _device_map_from_roles(devices_by_role)
    now = timezone.now_for_db()
    correlation_id = getattr(session, "correlation_id", None) or getattr(inbox, "correlation_id", None)
    current_status = getattr(session, "status", None)
    session_ctx = _session_context(session)
    awaiting_command_id: int | None = None

    if orch_result.context_patch:
        session_ctx.update(orch_result.context_patch)
        _set_session_context(session, session_ctx)
        _sync_session_contract_snapshot(session, workline=workline, context=session_ctx)
        # 同步 barcode 到 session 字段
        if "barcode" in orch_result.context_patch:
            barcode_value = orch_result.context_patch["barcode"]
            if barcode_value:
                session.barcode = barcode_value
    else:
        _sync_session_contract_snapshot(session, workline=workline, context=session_ctx)

    if correlation_id and getattr(session, "correlation_id", None) is None:
        session.correlation_id = correlation_id

    session.last_inbox_id = _resolve_entity_id(inbox)

    if orch_result.transition:
        await _add_timeline(
            db,
            timeline_generator.generate(
                session=session,
                stage=TimelineStage.DECISION,
                action_type=TimelineActionType.DECISION_MADE,
                payload={
                    "transition": orch_result.transition,
                    "context_patch": orch_result.context_patch or {},
                },
                actor_type=TimelineActorType.PLUGIN,
                actor_code=getattr(workline, "plugin_key", None),
                related_inbox_id=_resolve_entity_id(inbox),
            ),
        )

    for decision in orch_result.decisions or []:
        if not isinstance(decision, dict):
            continue

        decision_type = _string_value(decision.get("decision_type"))
        if decision_type != EXTERNAL_HTTP_DECISION_TYPE:
            continue

        dispatch_key = _string_value(decision.get("dispatch_key"))
        target_code = _string_value(decision.get("target_code"))
        payload_json = _payload_dict(decision.get("payload"))
        source_system = _string_value(decision.get("source_system"), "EXTERNAL_SYSTEM")
        if not dispatch_key:
            raise ValueError("EXTERNAL_HTTP decision missing dispatch_key")
        if not target_code:
            raise ValueError("EXTERNAL_HTTP decision missing target_code")
        if not payload_json:
            raise ValueError("EXTERNAL_HTTP decision missing payload")

        db.add(
            WorklineOutbox(
                session_id=session.id,
                workline_id=session.workline_id,
                dispatch_type=DispatchType.EXTERNAL_HTTP,
                dispatch_key=dispatch_key,
                target_type=TargetType.HTTP_ENDPOINT,
                target_code=target_code,
                payload_json=payload_json,
            )
        )
        await _add_timeline(
            db,
            timeline_generator.generate(
                session=session,
                stage=TimelineStage.DISPATCH_PREPARE,
                action_type=TimelineActionType.EXTERNAL_CALL_STARTED,
                payload={
                    "dispatch_key": dispatch_key,
                    "target_code": target_code,
                    "payload": payload_json,
                },
                actor_type=TimelineActorType.EXTERNAL_SYSTEM,
                actor_code=source_system,
                related_inbox_id=_resolve_entity_id(inbox),
                status=TimelineStatus.PENDING,
            ),
        )

    for command_intent in orch_result.commands or []:
        target_device_id = command_intent.target_device_id
        target_device = device_by_id.get(target_device_id)
        if target_device is None:
            target_device = await device_repo.get_by_id(db, target_device_id)
        if target_device is None:
            raise ValueError(f"Target device not found: {target_device_id}")

        device_code = getattr(target_device, "device_code", None)
        if not isinstance(device_code, str) or not device_code:
            raise ValueError(f"Target device missing device_code: {target_device_id}")

        generated_command_code = _build_command_code(_map_command_task_type(command_intent.action))
        vendor_payload = _normalize_vendor_command_payload(
            command_intent.parameters,
            action=command_intent.action,
            default_command_code=generated_command_code,
        )
        logger.info(
            f"[Orchestrator] Command parameters: command_intent.parameters={command_intent.parameters}, "
            f"vendor_payload={vendor_payload}"
        )
        resolved_command_code = _string_value(vendor_payload.get("command_code"), generated_command_code)
        vendor_task_type = _string_value(vendor_payload.get("task_type"), command_intent.action)
        priority_value = vendor_payload.get("priority")
        timeout_value = vendor_payload.get("timeout")

        command_data: dict[str, Any] = {
            "command_code": resolved_command_code,
            "device_id": target_device_id,
            "task_type": _map_command_task_type(vendor_task_type),
            "priority": priority_value if isinstance(priority_value, int) else 5,
            "timeout_ms": timeout_value if isinstance(timeout_value, int) else 300000,
            "params": vendor_payload,
            "correlation_id": correlation_id,
            "session_id": str(session.id),
            "workline_id": session.workline_id,
            "plugin_key": getattr(session, "plugin_key", None) or getattr(workline, "plugin_key", None),
            "contract_version": getattr(session, "contract_version", None),
            "step_code": session_ctx.get("step_code"),
        }
        command = await command_repo.create(db, command_data)
        if command is None:
            raise RuntimeError("Failed to create device command from PluginResult")

        if awaiting_command_id is None:
            awaiting_command_id = command.id

        db.add(
            WorklineOutbox(
                session_id=session.id,
                workline_id=session.workline_id,
                dispatch_type=DispatchType.DEVICE_COMMAND,
                dispatch_key=f"device-command:{command.command_code}",
                target_type=TargetType.DEVICE,
                target_code=device_code,
                payload_json=_build_outbox_payload(command),
            )
        )
        await _add_timeline(
            db,
            timeline_generator.generate(
                session=session,
                stage=TimelineStage.DISPATCH_PREPARE,
                action_type=TimelineActionType.COMMAND_SENT,
                payload={
                    "command_code": command.command_code,
                    "command_type": command_intent.action,
                    "parameters": vendor_payload,
                },
                actor_type=TimelineActorType.ORCHESTRATOR,
                actor_code=device_code,
                related_inbox_id=_resolve_entity_id(inbox),
                related_command_id=command.id,
                status=TimelineStatus.PENDING,
            ),
        )

    if orch_result.failure is not None:
        session.status = "FAILED"
        _clear_session_wait(session)
        session.ended_at = now
        session.failure_domain = orch_result.failure.domain
        session.failure_code = orch_result.failure.code
        session.failure_message = orch_result.failure.message
        await _add_timeline(
            db,
            timeline_generator.generate(
                session=session,
                stage=TimelineStage.FAIL,
                action_type=TimelineActionType.SESSION_FAILED,
                payload={"message": orch_result.failure.message},
                from_status=current_status,
                to_status="FAILED",
                actor_type=TimelineActorType.ORCHESTRATOR,
                related_inbox_id=_resolve_entity_id(inbox),
                status=TimelineStatus.FAILED,
                failure_domain=orch_result.failure.domain,
                message=orch_result.failure.message,
            ),
        )
        return

    _clear_session_failure(session)

    if orch_result.transition == "manual_cancel":
        session.status = "CANCELLED"
        _clear_session_wait(session)
        session.ended_at = now
        await _add_timeline(
            db,
            timeline_generator.generate(
                session=session,
                stage=TimelineStage.MANUAL,
                action_type=TimelineActionType.SESSION_CANCELLED,
                from_status=current_status,
                to_status="CANCELLED",
                actor_type=TimelineActorType.ORCHESTRATOR,
                related_inbox_id=_resolve_entity_id(inbox),
            ),
        )
        return

    if orch_result.complete:
        session.status = "COMPLETED"
        _clear_session_wait(session)
        session.ended_at = now
        await _add_timeline(
            db,
            timeline_generator.generate(
                session=session,
                stage=TimelineStage.COMPLETE,
                action_type=TimelineActionType.SESSION_COMPLETED,
                from_status=current_status,
                to_status="COMPLETED",
                actor_type=TimelineActorType.ORCHESTRATOR,
                related_inbox_id=_resolve_entity_id(inbox),
            ),
        )
        return

    if orch_result.wait is not None:
        session.status = _wait_session_status(orch_result.wait.wait_type)
        session.current_wait_type = orch_result.wait.wait_type
        session.current_wait_token = orch_result.wait.wait_token
        session.waiting_since = now
        session.deadline_at = now + timedelta(seconds=orch_result.wait.deadline_seconds)
        session.awaiting_command_id = awaiting_command_id
        session.ended_at = None
        await _add_timeline(
            db,
            timeline_generator.generate(
                session=session,
                stage=TimelineStage.WAITING,
                action_type=TimelineActionType.WAIT_STARTED,
                payload={
                    "wait_type": orch_result.wait.wait_type,
                    "wait_token": orch_result.wait.wait_token,
                    "deadline_seconds": orch_result.wait.deadline_seconds,
                },
                from_status=current_status,
                to_status=session.status,
                actor_type=TimelineActorType.ORCHESTRATOR,
                related_inbox_id=_resolve_entity_id(inbox),
                related_command_id=awaiting_command_id,
                status=TimelineStatus.PENDING,
            ),
        )
        return

    if orch_result.transition == "manual_hold":
        session.status = "MANUAL_HOLD"
        session.ended_at = None
        return

    if orch_result.transition == "manual_resume":
        if session.current_wait_type:
            session.status = _wait_session_status(session.current_wait_type)
        else:
            session.status = "RUNNING"
        session.ended_at = None
        return

    if orch_result.transition or orch_result.context_patch or orch_result.commands or orch_result.decisions:
        session.status = "RUNNING"

    _clear_session_wait(session)
    session.ended_at = None


async def _load_workline_session(db: Any, inbox: Any, session_repo: Any) -> Any | None:
    """按 inbox.session_id 加载 Session。"""
    session_id = getattr(inbox, "session_id", None)
    if not session_id:
        return None
    return await session_repo.get_by_id(db, session_id)


async def _load_workline_entity(db: Any, inbox: Any, session: Any, workline_repo: Any | None = None) -> Any | None:
    """按 inbox/workline session 归属加载 Workline（带缓存）。"""
    from src.app.workline.services import workline_service
    from src.database.redis_cache import get_cache

    workline_id = getattr(inbox, "workline_id", None)
    if workline_id:
        cache = get_cache()
        return await workline_service.get_by_id(db, cache, workline_id)

    session_workline_id = getattr(session, "workline_id", None)
    if session_workline_id:
        cache = get_cache()
        return await workline_service.get_by_id(db, cache, session_workline_id)

    return None


async def _load_command_entity(db: Any, inbox: Any, command_repo: Any) -> Any | None:
    """按 command_id 或 payload.command_code 加载命令（带缓存），并回填 inbox.command_id。"""
    from src.app.device.services import device_command_service
    from src.database.redis_cache import get_cache

    command_id = getattr(inbox, "command_id", None)
    if command_id:
        # 使用 Service 层缓存
        cache = get_cache()
        return await device_command_service.get_by_id(db, cache, command_id)

    raw_payload = getattr(inbox, "payload_json", None)
    payload: dict[str, Any] = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
    command_code = payload.get("command_code")
    if not command_code:
        return None

    # command_code 查询仍使用 repo（无对应 Service 方法）
    command = await command_repo.get_by_command_code(db, command_code)
    if command is not None:
        inbox.command_id = command.id
    return command


def _hydrate_inbox_from_command(inbox: Any, command: Any | None) -> None:
    """从命令记录回填 inbox 归属信息。"""
    if command is None:
        return

    if getattr(inbox, "device_id", None) is None:
        inbox.device_id = command.device_id
    if getattr(inbox, "workline_id", None) is None and command.workline_id is not None:
        inbox.workline_id = command.workline_id
    if getattr(inbox, "correlation_id", None) is None and command.correlation_id:
        inbox.correlation_id = command.correlation_id


async def _load_device_entity(db: Any, inbox: Any, device_repo: Any) -> Any | None:
    """按 device_id 或 payload.device_code 加载设备（带缓存），并回填 inbox.device_id。"""
    from src.app.device.services import device_service
    from src.database.redis_cache import get_cache

    device_id = getattr(inbox, "device_id", None)
    if device_id:
        cache = get_cache()
        return await device_service.get_by_id(db, cache, device_id)

    raw_payload = getattr(inbox, "payload_json", None)
    payload: dict[str, Any] = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
    device_code = payload.get("device_code")
    if not device_code:
        return None

    # 使用 DeviceService 查询（带缓存）
    device = await device_service.get_device_by_code(db, device_code)
    if device is not None:
        inbox.device_id = device.id
    return device


async def _backfill_workline_from_device(db: Any, inbox: Any, device: Any, workline_repo: Any) -> Any | None:
    """按设备归属补全 Workline，并回填 inbox.workline_id。"""
    device_workline_id = getattr(device, "work_line_id", None)
    if device is None or not device_workline_id:
        return None

    workline = await workline_repo.get_by_id(db, device_workline_id)
    if workline is not None:
        inbox.workline_id = workline.id
    return workline


async def _load_devices_by_role(db: Any, workline: Any, device_repo: Any) -> dict[str, list[Any]]:
    """加载工作线下设备并按角色分组（带缓存）。"""
    workline_pk = _resolve_entity_id(workline)
    if workline is None or workline_pk is None:
        return {}

    # 使用缓存获取设备，避免重复查询
    devices = await workline_device_cache.get_devices(
        db,
        workline_pk,
        fetch_func=lambda d, wid: device_repo.get_by_work_line_id(d, wid),
    )

    if not devices:
        return {}

    devices_by_role: dict[str, list[Any]] = {}
    for device in devices:
        role = _resolve_device_role(device)
        if role:
            devices_by_role.setdefault(role, []).append(device)

    return devices_by_role


async def _load_related_entities(db: Any, inbox: Any) -> LoadedEntities:
    """加载关联实体

    Args:
        db: 数据库会话
        inbox: Inbox 消息

    Returns:
        加载的实体字典
    """
    from src.app.device.repositories import DeviceRepository
    from src.app.device.repositories.command_repository import DeviceCommandRepository
    from src.app.workline.repositories import WorkLineRepository
    from src.app.workline.repositories.session_repository import (
        WorklineSessionRepository,
    )
    from src.workline_runtime.session_resolver import session_resolver

    session_repo = WorklineSessionRepository()
    workline_repo = WorkLineRepository()
    device_repo = DeviceRepository()
    command_repo = DeviceCommandRepository()

    session = await _load_workline_session(db, inbox, session_repo)
    workline = await _load_workline_entity(db, inbox, session, workline_repo)
    command = await _load_command_entity(db, inbox, command_repo)
    _hydrate_inbox_from_command(inbox, command)
    device = await _load_device_entity(db, inbox, device_repo)

    if workline is None and device is not None:
        workline = await _backfill_workline_from_device(db, inbox, device, workline_repo)

    devices_by_role = await _load_devices_by_role(db, workline, device_repo)

    if session is None and _should_resolve_session(inbox):
        session = await session_resolver.resolve_or_create(
            db=db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
        )
        session_pk = _resolve_entity_id(session)
        if session_pk is not None:
            inbox.session_id = session_pk
        if workline is None:
            workline = await _load_workline_entity(db, inbox, session, workline_repo)
            if workline is None and device is not None:
                workline = await _backfill_workline_from_device(db, inbox, device, workline_repo)
            devices_by_role = await _load_devices_by_role(db, workline, device_repo)

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

    def __init__(self) -> None:
        super().__init__()
        self._db: Any | None = None

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
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self._db.close())
                loop.close()
            except Exception:
                pass
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
    """处理 Inbox 消息的内部类（批量处理核心逻辑）

    职责：
    - 批量获取待处理 Inbox 消息
    - 并发控制：防止多 worker 重复处理同一消息
    - 前置验证：检查 SCAN_COMPLETED 事件必填字段
    - 编排执行：调用 OrchestratorService 处理消息
    - 结果应用：执行编排产出的命令/决策/状态转换
    - 状态更新：标记消息为成功/失败/跳过

    调用方式：
    - 通过 Celery 任务 process_inbox_batch 间接调用
    - 可直接调用 _process_batch() 进行单元测试
    """

    @staticmethod
    async def _process_batch(db: Any, limit: int = 10) -> ProcessResult:
        """批量处理 Inbox 消息

        处理流程：
        1. 从数据库获取 status='NEW' 的待处理消息（limit 限制数量）
        2. 遍历每个消息：
           a. 尝试加锁标记为 PROCESSING（并发控制）
           b. 前置验证：SCAN_COMPLETED 事件必须包含条码信息
           c. 加载关联实体（session/workline/device/devices_by_role）
           d. 调用 OrchestratorService.process_inbox() 执行编排
           e. 成功：应用编排结果，更新状态为 PROCESSED
           f. 失败：更新状态为 FAILED
        3. 提交数据库事务

        并发控制：
        - 使用 SELECT ... FOR UPDATE SKIP LOCKED 获取消息
        - 使用 processor_token 标记处理 worker
        - 已被锁定的消息会被标记为 SKIPPED

        Args:
            db: 数据库会话
            limit: 批处理数量，默认 10

        Returns:
            处理结果统计 {
                "processed": 处理总数,
                "success": 成功数,
                "failed": 失败数,
                "skipped": 跳过数（已被其他 worker 锁定）
            }
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
            inbox_pk: int | None = None  # 初始化，避免 basedpyright 警告
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

                # ========== 前置验证：检查必填字段 ==========
                raw_payload = getattr(inbox, "payload_json", None)
                payload: dict[str, Any] = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
                event_type = payload.get("event_type")
                data_field: dict[str, Any] = payload.get("data") or {}

                # SCAN_COMPLETED 事件必须包含条码信息
                if event_type == "SCAN_COMPLETED":
                    # 从 data 字段和 payload 中收集条码
                    barcode_values: list[Any] = [data_field.get(f) for f in SixInOne.BARCODE_FIELDS]
                    barcode_values.extend([
                        payload.get("LotCode"),
                        payload.get("DateCode"),
                        payload.get("barcode"),
                    ])
                    if not any(barcode_values):
                        error_msg = "SCAN_COMPLETED 缺少条码信息（LotCode/DateCode/PONumber/MfrPN/ProductNo/Qty）"
                        logger.warning(f"Inbox {inbox_pk} {error_msg}")
                        _ = await inbox_service.mark_as_failed(db, inbox_pk, error_msg)
                        result["failed"] += 1
                        result["processed"] += 1
                        continue

                # 加载关联实体
                entities = await _load_related_entities(db, inbox)

                # 调用编排器（带超时保护）
                orchestrator = OrchestratorService()
                orch_result: OrchestratorResult = await asyncio.wait_for(
                    orchestrator.process_inbox(
                        session=entities["session"],
                        workline=entities["workline"],
                        inbox=inbox,
                        devices_by_role=entities["devices_by_role"],
                        services=entities["services"],
                        correlation_id=inbox.correlation_id or "",
                    ),
                    timeout=INBOX_PROCESS_TIMEOUT_SECONDS,
                )

                # 根据结果更新状态
                if orch_result.success:
                    session = entities["session"]
                    workline = entities["workline"]
                    if session is None or workline is None:
                        raise ValueError("Inbox processing missing session/workline context")

                    # FAST FAIL: 如果 success=True 但没有任何产出，记录警告
                    has_output = (
                        orch_result.commands is not None
                        or orch_result.decisions is not None
                        or orch_result.transition is not None
                    )
                    if not has_output:
                        logger.warning(
                            f"Inbox {inbox_pk} 成功但无产出: transition={orch_result.transition}, "
                            f"commands={orch_result.commands}, decisions={orch_result.decisions}"
                        )
                        # 标记为失败，让硬件商重试
                        _ = await inbox_service.mark_as_failed(
                            db, inbox_pk, "Processing succeeded but no commands generated"
                        )
                        result["failed"] += 1
                        result["processed"] += 1
                        continue

                    await _apply_orchestrator_effects(
                        db,
                        session=session,
                        workline=workline,
                        inbox=inbox,
                        devices_by_role=entities["devices_by_role"],
                        orch_result=orch_result,
                    )
                    _ = await inbox_service.mark_as_processed(db, inbox_pk)
                    result["success"] += 1
                    logger.info(f"Inbox {inbox_pk} 处理成功")
                else:
                    error_msg = orch_result.error or (
                        orch_result.failure.message if orch_result.failure is not None else "Unknown error"
                    )
                    _ = await inbox_service.mark_as_failed(db, inbox_pk, error_msg)
                    result["failed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")

                result["processed"] += 1

            except TimeoutError:
                # 处理超时，不阻塞其他消息
                logger.error(f"Inbox {inbox_pk} 处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)")
                try:
                    # 使用已解析的 inbox_pk（如果在前面解析成功）
                    pk_to_mark = locals().get("inbox_pk") or _resolve_entity_id(inbox)
                    if pk_to_mark is not None:
                        _ = await inbox_service.mark_as_failed(db, pk_to_mark, f"处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)")
                except Exception as mark_error:
                    logger.warning(f"Inbox 超时标记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

            except Exception as e:
                logger.exception(f"Inbox {inbox_pk_text} 处理异常")
                try:
                    inbox_pk = _resolve_entity_id(inbox)
                    if inbox_pk is not None:
                        _ = await inbox_service.mark_as_failed(db, inbox_pk, str(e))
                except Exception as mark_error:
                    logger.warning(f"Inbox {inbox_pk_text} 异常补记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

        # 提交事务
        await db.commit()

        return result


class TimeoutScanner:
    """超时扫描器内部类

    职责：
    - 扫描超时的 Session（deadline_at < now）
    - 为超时 Session 创建 timeout 类型的 Inbox 消息
    - 触发后续编排流程处理超时

    调用方式：
    - 通过 Celery 任务 scan_timeouts_batch 间接调用
    - 可直接调用 _scan() 进行单元测试
    """

    @staticmethod
    async def _scan(db: Any, limit: int = 100) -> ScanResult:
        """扫描超时 Session 并创建 Timeout Inbox

        处理流程：
        1. 查询 deadline_at < NOW() 的超时 Session
        2. 遍历每个超时会话：
           a. 创建 type='timeout' 的 Inbox 消息
           b. 继承原 Session 的 correlation_id
        3. 提交数据库事务

        Args:
            db: 数据库会话
            limit: 批处理数量，默认 100

        Returns:
            扫描结果统计 {
                "scanned": 扫描的 Session 数,
                "timeouts_created": 创建的超时 Inbox 数,
                "errors": 错误数
            }
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
                session_pk = _resolve_entity_id(session)
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
                session_pk = _resolve_entity_id(session)
                logger.error(f"Session {session_pk or 'unknown'} 创建超时 Inbox 失败: {e}")
                result["errors"] += 1

        # 提交事务
        await db.commit()

        return result


@celery_app.task(
    name="src.celery_app.tasks.workline.process_inbox_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_inbox_batch(self: WorklineTask, limit: int = 10) -> ProcessResult:
    """批量处理 Inbox 消息 (Celery 任务入口)

    从数据库获取 status='NEW' 的 Inbox 消息，调用 ProcessInboxMessages._process_batch() 执行处理。

    处理流程（详见 ProcessInboxMessages）：
    1. 批量获取待处理消息（limit 限制）
    2. 并发控制：标记为 PROCESSING，防止重复处理
    3. 前置验证：检查 SCAN_COMPLETED 事件必填字段
    4. 加载关联实体：session/workline/device/devices_by_role
    5. 调用 OrchestratorService 执行编排
    6. 应用编排结果：command/outbox/timeline
    7. 更新状态：PROCESSED/FAILED

    执行模式：
    - bind=True：任务方法接收 self（WorklineTask 实例）
    - max_retries=3：失败后自动重试最多 3 次
    - default_retry_delay=5：重试间隔 5 秒（指数退避）

    调用链：
        process_inbox_batch() → ProcessInboxMessages._process_batch()

    Args:
        self: Celery 任务实例（bind=True）
        limit: 批处理数量，默认 10

    Returns:
        处理结果统计 {
            "processed": 处理总数,
            "success": 成功数,
            "failed": 失败数,
            "skipped": 跳过数
        }

    触发方式：
        celery beat 定时调度（默认每 5 秒）
        手动调用：process_inbox_batch.delay(limit=10)
    """
    logger.debug(f"开始处理 Inbox 消息, limit={limit}")

    async def _process() -> ProcessResult:
        async with self.db as db:
            return await ProcessInboxMessages._process_batch(db, limit=limit)

    try:
        result = _run_async(_process())
        _ensure_non_empty_retry_result(
            "process_inbox_batch",
            result,
            int(getattr(self.request, "retries", 0) or 0),
        )
        if result.get("processed", 0) > 0:
            logger.info(f"Inbox 处理完成: {result}")
        else:
            logger.debug(f"Inbox 处理完成: {result}")
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

    扫描 deadline_at < NOW() 的超时 Session，为每个超时 Session 创建 timeout 类型的 Inbox 消息，
    触发后续编排流程处理超时。

    处理流程（详见 TimeoutScanner）：
    1. 查询 deadline_at < NOW() 的超时 Session
    2. 遍历每个超时会话：
       a. 创建 type='TIMEOUT' 的 Inbox 消息
       b. 继承原 Session 的 correlation_id
    3. 提交数据库事务

    执行模式：
    - bind=True：任务方法接收 self（WorklineTask 实例）
    - max_retries=3：失败后自动重试最多 3 次
    - default_retry_delay=60：重试间隔 60 秒（超时场景不频繁）

    调用链：
        scan_timeouts_batch() → TimeoutScanner._scan()

    Args:
        self: Celery 任务实例（bind=True）
        limit: 批处理数量，默认 100

    Returns:
        扫描结果统计 {
            "scanned": 扫描的 Session 数,
            "timeouts_created": 创建的超时 Inbox 数,
            "errors": 错误数
        }

    触发方式：
        celery beat 定时调度（默认每 30 秒）
        手动调用：scan_timeouts_batch.delay(limit=100)

    注意：
        - 使用幂等性键防止重复创建 timeout Inbox
        - 创建的 Inbox 类型为 InboxKind.TIMER_TIMEOUT
    """
    logger.info(f"开始扫描超时 Session, limit={limit}")

    async def _scan() -> ScanResult:
        async with self.db as db:
            return await TimeoutScanner._scan(db, limit=limit)

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
                    _ = await outbox_repo.mark_as_failed(db, outbox_pk, "Dispatch failed", OutboxDispatcher.MAX_RETRIES)
                    result["failed"] += 1
                    logger.warning(f"Outbox {outbox_pk} 派发失败")

                result["dispatched"] += 1

            except Exception as e:
                logger.error(f"Outbox {outbox_pk_text} 派发异常: {e}")
                try:
                    outbox_pk = _resolve_entity_id(outbox)
                    if outbox_pk is not None:
                        _ = await outbox_repo.mark_as_failed(db, outbox_pk, str(e), OutboxDispatcher.MAX_RETRIES)
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

            device = await device_repository.get_by_device_code(db, outbox.target_code)
            if device is None or not device.host or not device.port:
                logger.error(f"设备不存在或通信配置不完整: {outbox.target_code}")
                return False

            # 确保 scheme 是 http 或 https
            protocol_value = getattr(device, "protocol", None)
            if protocol_value:
                scheme = str(protocol_value).lower()
                if scheme not in ("http", "https"):
                    scheme = "http"
            else:
                scheme = "http"

            url = f"{scheme}://{device.host}:{device.port}/api/v1/device/command"
            logger.info(f"发送设备指令到 {url}: {outbox.payload_json.get('command_code')}")
            timeout = (device.timeout or 10000) / 1000
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=outbox.payload_json)
                if response.status_code == 200:
                    logger.info(f"设备指令发送成功: {outbox.payload_json.get('command_code')}")
                    return True
                logger.warning(f"设备指令发送失败: HTTP {response.status_code}")
                return False
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


@celery_app.task(
    name="src.celery_app.tasks.workline.dispatch_outbox_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def dispatch_outbox_batch(self: WorklineTask, limit: int = 50) -> DispatchResult:
    """批量派发 Outbox 消息 (Celery 任务入口)

    从数据库获取 status='PENDING' 的 Outbox 消息，根据 dispatch_type 执行派发：
    - DEVICE_COMMAND：调用设备 HTTP API
    - EXTERNAL_HTTP：调用外部系统 HTTP API
    - INTERNAL_SIGNAL：触发内部 Celery 任务

    处理流程（详见 OutboxDispatcher）：
    1. 批量获取待派发消息（limit 限制）
    2. 遍历每个消息：
       a. 标记为 DISPATCHING（并发控制）
       b. 根据 dispatch_type 调用对应派发方法
       c. 成功：标记为 SENT
       d. 失败：标记为 FAILED（超过最大重试次数）
    3. 提交数据库事务

    执行模式：
    - bind=True：任务方法接收 self（WorklineTask 实例）
    - max_retries=3：失败后自动重试最多 3 次
    - default_retry_delay=10：重试间隔 10 秒

    调用链：
        dispatch_outbox_batch() → OutboxDispatcher._dispatch()

    Args:
        self: Celery 任务实例（bind=True）
        limit: 批处理数量，默认 50

    Returns:
        派发结果统计 {
            "dispatched": 派发总数,
            "success": 成功数,
            "failed": 失败数,
            "skipped": 跳过数
        }

    触发方式：
        celery beat 定时调度（默认每 5 秒）
        手动调用：dispatch_outbox_batch.delay(limit=50)

    派发类型详解：
        - DEVICE_COMMAND: 向设备下发指令（HTTP POST /api/v1/device/command）
        - EXTERNAL_HTTP: 调用外部系统 API（HTTP POST dispatch_key）
        - INTERNAL_SIGNAL: 触发内部任务（celery.send_task）
    """
    logger.debug(f"开始派发 Outbox 消息, limit={limit}")

    async def _dispatch() -> DispatchResult:
        async with self.db as db:
            return await OutboxDispatcher._dispatch(db, limit=limit)

    try:
        result = _run_async(_dispatch())
        if result.get("dispatched", 0) > 0:
            logger.info(f"Outbox 派发完成: {result}")
        else:
            logger.debug(f"Outbox 派发完成: {result}")
        return result
    except Exception as e:
        logger.error(f"Outbox 派发失败: {e}")
        countdown = 10 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


# ============================================
# 导出
# ============================================

__all__ = [
    # 内部辅助函数
    "_load_related_entities",
    # Celery 任务入口（公共 API）
    "dispatch_outbox_batch",
    "process_inbox_batch",
    "scan_timeouts_batch",
    # 内部类（已注释：不导出，仅供 Celery 任务内部使用）
    # "OutboxDispatcher",
    # "ProcessInboxMessages",
    # "TimeoutScanner",
]
