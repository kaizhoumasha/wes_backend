"""RuntimeInbox 编排前关联实体加载。"""

from __future__ import annotations

from typing import Any, TypedDict

from src.app.runtime.orchestration.diagnostics import ErrorCode
from src.app.runtime.orchestration.events_bridge import RESERVED_RUNTIME_EVENTS
from src.app.workline.constants import EXTERNAL_HTTP_INBOX_KIND
from src.app.workline.runtime_services import WorklineRuntimeServices, build_workline_runtime_services
from src.app.workline.services.plugin_binding_service import (
    WorklinePluginBindingService,
    workline_plugin_binding_service,
)
from src.app.workline.utils import payload_dict
from src.core.conf import settings
from src.utils.timezone import timezone
from src.utils.value_normalization import canonical_event_type, optional_int, optional_str, resolve_entity_id


class RuntimeInboxRelatedEntities(TypedDict):
    """RuntimeInbox 进入编排前需要的领域实体。"""

    session: Any | None
    workline: Any | None
    device: Any | None
    command: Any | None
    devices_by_role: dict[str, list[Any]]
    services: WorklineRuntimeServices
    safety_checked: bool


def _kind_value(entity: Any) -> str | None:
    value = getattr(getattr(entity, "kind", None), "value", getattr(entity, "kind", None))
    return value if isinstance(value, str) and value else None


def _canonical_workline_session_id(inbox: Any) -> int | None:
    """从显式列读取 WorklineSession ID，并校验 canonical 合同一致。"""

    explicit = optional_int(getattr(inbox, "workline_session_id", None))
    payload = payload_dict(getattr(inbox, "payload_json", None))
    data = payload_dict(payload.get("data"))
    canonical = optional_int(data.get("session_id"))
    if explicit is not None and canonical is not None and explicit != canonical:
        raise ValueError(
            f"RuntimeInbox workline_session_id mismatch: explicit={explicit}, canonical.data.session_id={canonical}"
        )
    return explicit or canonical


def _should_resolve_session(inbox: Any, *, session_id: int | None) -> bool:
    """仅在具备足够归属信息时触发 WorklineSession 解析。"""
    payload = payload_dict(getattr(inbox, "payload_json", None))
    kind = _kind_value(inbox)
    if canonical_event_type(payload) in RESERVED_RUNTIME_EVENTS:
        return False
    if kind == "DEVICE_EVENT":
        return bool(getattr(inbox, "device_id", None) or payload.get("device_code") or payload.get("business_key"))
    if kind == "COMMAND_RESULT":
        return bool(getattr(inbox, "command_id", None) or payload.get("command_code"))
    if kind == EXTERNAL_HTTP_INBOX_KIND:
        return bool(getattr(inbox, "trace_id", None))
    if kind == "INTERNAL_EVENT":
        return session_id is not None and isinstance(getattr(inbox, "workline_id", None), int)
    if kind in {"TIMER_TIMEOUT", "REPLAY_REQUEST"}:
        return session_id is not None
    return False


async def _load_workline_session(db: Any, *, session_id: int | None, session_repo: Any) -> Any | None:
    if session_id is not None:
        return await session_repo.get_by_id(db, session_id)
    return None


async def _load_workline_entity(db: Any, inbox: Any, session: Any, workline_repo: Any) -> Any | None:
    inbox_workline_id = getattr(inbox, "workline_id", None)
    session_workline_id = getattr(session, "workline_id", None)
    if _kind_value(inbox) == "INTERNAL_EVENT" and isinstance(session_workline_id, int):
        if isinstance(inbox_workline_id, int) and inbox_workline_id != session_workline_id:
            raise ValueError(
                "INTERNAL_EVENT workline_id mismatch: "
                f"inbox.workline_id={inbox_workline_id}, session.workline_id={session_workline_id}"
            )
        workline_id = session_workline_id
    else:
        workline_id = inbox_workline_id or session_workline_id
    if isinstance(workline_id, int):
        return await workline_repo.get_by_id(db, workline_id)
    return None


async def _load_command_entity(db: Any, inbox: Any, command_repo: Any) -> Any | None:
    command_id = getattr(inbox, "command_id", None)
    if isinstance(command_id, int):
        return await command_repo.get_by_id(db, command_id)
    command_code = payload_dict(getattr(inbox, "payload_json", None)).get("command_code")
    if isinstance(command_code, str) and command_code:
        return await command_repo.get_by_command_code(db, command_code)
    return None


def _hydrate_inbox_from_command(inbox: Any, command: Any | None) -> None:
    if command is None:
        return
    command_pk = resolve_entity_id(command)
    if command_pk is not None and not getattr(inbox, "command_id", None):
        inbox.command_id = command_pk
    workline_id = getattr(command, "workline_id", None)
    if isinstance(workline_id, int) and not getattr(inbox, "workline_id", None):
        inbox.workline_id = workline_id


async def _load_device_entity(db: Any, inbox: Any, device_repo: Any) -> Any | None:
    device_id = getattr(inbox, "device_id", None)
    if isinstance(device_id, int):
        return await device_repo.get_by_id(db, device_id)
    payload = payload_dict(getattr(inbox, "payload_json", None))
    device_code = payload.get("device_code") or payload.get("location")
    if isinstance(device_code, str) and device_code:
        return await device_repo.get_by_device_code(db, device_code)
    return None


async def _backfill_workline_from_device(db: Any, inbox: Any, device: Any, workline_repo: Any) -> Any | None:
    workline_id = getattr(device, "work_line_id", None)
    if not isinstance(workline_id, int):
        return None
    inbox.workline_id = workline_id
    return await workline_repo.get_by_id(db, workline_id)


def _resolve_device_role(device: Any) -> str | None:
    value = getattr(device, "device_role", None)
    return value if isinstance(value, str) and value else None


async def _load_devices_by_role(db: Any, workline: Any, device_repo: Any) -> dict[str, list[Any]]:
    workline_id = resolve_entity_id(workline)
    if workline_id is None:
        return {}
    devices = await device_repo.get_by_work_line_id(db, workline_id)
    devices_by_role: dict[str, list[Any]] = {}
    for device in devices:
        role = _resolve_device_role(device)
        if role:
            devices_by_role.setdefault(role, []).append(device)
    return devices_by_role


def _resolve_effect_source_device(inbox: Any, session: Any, devices_by_role: dict[str, list[Any]]) -> Any | None:
    """为无 device_id 回调恢复 RuntimeIntent effect 来源设备。"""
    payload = payload_dict(getattr(inbox, "payload_json", None))
    device_code = optional_str(payload.get("device_code")) or optional_str(payload.get("location"))
    if device_code is None:
        session_context = payload_dict(getattr(session, "context_json", None))
        rack_operation = payload_dict(session_context.get("rack_operation"))
        device_code = optional_str(rack_operation.get("resume_source_device_code")) or optional_str(
            session_context.get("resume_source_device_code")
        )
    if device_code is None:
        return None
    for devices in devices_by_role.values():
        for device in devices:
            if optional_str(getattr(device, "device_code", None)) == device_code:
                return device
    return None


async def _assert_workline_accepting_runtime_event(
    db: Any,
    *,
    workline: Any | None,
    resolved_event_type: str | None,
) -> bool:
    if resolved_event_type is None or resolved_event_type == "ESTOP_PRESSED":
        return True
    workline_pk = resolve_entity_id(workline)
    if workline_pk is None:
        return False
    from src.app.workline.services.safety_service import workline_safety_service

    await workline_safety_service.assert_accepting_work(db, workline_id=workline_pk)
    return True


async def _assert_platform_plugin_binding_admitted(
    db: Any,
    *,
    workline: Any | None,
    session: Any | None,
    devices_by_role: dict[str, list[Any]],
) -> None:
    """每次 inbox/retry 都重查历史 pin 的撤权、有效期、环境与 kill switch。"""

    if workline is None or session is None:
        return
    binding_id = getattr(session, "plugin_binding_id", None)
    if not isinstance(binding_id, int):
        from src.app.workline.services.plugin_binding_service import PluginBindingAdmissionError

        raise PluginBindingAdmissionError(ErrorCode.PLUGIN_BINDING_REQUIRED.value)
    binding = await workline_plugin_binding_service.get_pinned(db, binding_id=binding_id)
    workline_plugin_binding_service.assert_pinned_identity(binding=binding, workline=workline, session=session)
    workline_plugin_binding_service.assert_execution_admitted(
        binding,
        environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
        now=timezone.now_utc(),
    )
    workline_plugin_binding_service.assert_device_snapshot(binding, devices_by_role=devices_by_role)


async def load_related_entities(
    db: Any,
    inbox: Any,
    *,
    resolved_event_type: str | None = None,
) -> RuntimeInboxRelatedEntities:
    """加载 RuntimeInbox 编排所需关联实体与运行时服务。"""
    from src.app.device.repositories import DeviceRepository
    from src.app.device.repositories.command_repository import DeviceCommandRepository
    from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
    from src.app.runtime.orchestration.repository_wiring import workline_repository
    from src.app.runtime.orchestration.services.session.session_resolver import session_resolver

    session_repo = WorklineSessionRepository()
    workline_repo = workline_repository
    device_repo = DeviceRepository()
    command_repo = DeviceCommandRepository()
    session_id = _canonical_workline_session_id(inbox)
    session = await _load_workline_session(db, session_id=session_id, session_repo=session_repo)
    workline = await _load_workline_entity(db, inbox, session, workline_repo)
    command = await _load_command_entity(db, inbox, command_repo)
    _hydrate_inbox_from_command(inbox, command)
    device = await _load_device_entity(db, inbox, device_repo)
    if workline is None and device is not None:
        workline = await _backfill_workline_from_device(db, inbox, device, workline_repo)
    safety_checked = await _assert_workline_accepting_runtime_event(
        db,
        workline=workline,
        resolved_event_type=resolved_event_type,
    )
    devices_by_role = await _load_devices_by_role(db, workline, device_repo)
    if session is None and _should_resolve_session(inbox, session_id=session_id):
        session = await session_resolver.resolve_or_create(
            db=db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
            session_id=session_id,
        )
        if workline is None:
            workline = await _load_workline_entity(db, inbox, session, workline_repo)
            if workline is None and device is not None:
                workline = await _backfill_workline_from_device(db, inbox, device, workline_repo)
            devices_by_role = await _load_devices_by_role(db, workline, device_repo)
            if not safety_checked:
                safety_checked = await _assert_workline_accepting_runtime_event(
                    db,
                    workline=workline,
                    resolved_event_type=resolved_event_type,
                )
    if session is not None:
        resolved_session_id = resolve_entity_id(session)
        if resolved_session_id is not None:
            inbox.workline_session_id = resolved_session_id
    if device is None and session is not None:
        device = _resolve_effect_source_device(inbox, session, devices_by_role)
    await _assert_platform_plugin_binding_admitted(
        db,
        workline=workline,
        session=session,
        devices_by_role=devices_by_role,
    )
    return {
        "session": session,
        "workline": workline,
        "device": device,
        "command": command,
        "devices_by_role": devices_by_role,
        "services": build_workline_runtime_services(db=db, workline=workline, session=session),
        "safety_checked": safety_checked,
    }


__all__ = ["RuntimeInboxRelatedEntities", "load_related_entities"]
