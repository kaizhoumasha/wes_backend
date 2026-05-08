"""WorkLine 安全控制服务。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.app.device.repositories.command_repository import DeviceCommandRepository
from src.app.device.services.device_service import DeviceService
from src.app.workline.models.safety import WorkLineRuntimeStatus, WorklineSafetyIncident, WorklineSafetyIncidentStatus
from src.app.workline.repositories.outbox_repository import WorklineOutboxRepository
from src.app.workline.repositories.safety_incident_repository import WorklineSafetyIncidentRepository
from src.app.workline.repositories.session_repository import WorklineSessionRepository
from src.app.workline.repositories.workline_repository import WorkLineRepository
from src.core.logger import logger
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class WorkLineSafetyBlocked(RuntimeError):
    """WorkLine 当前安全状态不允许继续接收新工作。"""


SAFETY_TRIGGER_PAYLOAD_MAX_BYTES = 16 * 1024
SAFETY_EVIDENCE_MAX_BYTES = 64 * 1024
_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact_url_query(value: str) -> str:
    if "?" not in value:
        return value

    parsed = urlsplit(value)
    if not parsed.query:
        return value

    redacted_pairs = [
        (key, _REDACTED if _is_sensitive_key(key) else query_value)
        for key, query_value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit(parsed._replace(query=urlencode(redacted_pairs, doseq=True)))


def _sanitize_safety_value(value: Any, *, key: object | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize_safety_value(item_value, key=item_key) for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [_sanitize_safety_value(item) for item in value]
    if isinstance(value, str):
        return _redact_url_query(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)


def _to_json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _bounded_safety_json(payload: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    sanitized = cast("dict[str, Any]", _sanitize_safety_value(payload))
    encoded = _to_json_text(sanitized).encode("utf-8")
    if len(encoded) <= max_bytes:
        return sanitized

    preview_budget = max(max_bytes - 512, 256)
    summary: dict[str, Any] = {
        "_truncated": True,
        "max_bytes": max_bytes,
        "original_bytes": len(encoded),
        "preview_json": _truncate_utf8(_to_json_text(sanitized), preview_budget),
    }
    while len(_to_json_text(summary).encode("utf-8")) > max_bytes and preview_budget > 128:
        preview_budget //= 2
        summary["preview_json"] = _truncate_utf8(_to_json_text(sanitized), preview_budget)
    return summary


class WorkLineSafetyService:
    """WorkLine 安全事件服务。"""

    def __init__(
        self,
        *,
        workline_repository: WorkLineRepository | None = None,
        incident_repository: WorklineSafetyIncidentRepository | None = None,
        session_repository: WorklineSessionRepository | None = None,
        outbox_repository: WorklineOutboxRepository | None = None,
        command_repository: DeviceCommandRepository | None = None,
        device_service: DeviceService | None = None,
    ) -> None:
        """初始化安全服务依赖。"""

        self.workline_repository = workline_repository or WorkLineRepository()
        self.incident_repository = incident_repository or WorklineSafetyIncidentRepository()
        self.session_repository = session_repository or WorklineSessionRepository()
        self.outbox_repository = outbox_repository or WorklineOutboxRepository()
        self.command_repository = command_repository or DeviceCommandRepository()
        self.device_service = device_service or DeviceService()

    async def assert_accepting_work(self, db: AsyncSession, *, workline_id: int) -> None:
        """校验 WorkLine 当前可接收新事件/新任务。"""

        workline = await self.workline_repository.get_for_update(db, workline_id)
        if workline is None:
            raise WorkLineSafetyBlocked(f"WORKLINE_NOT_FOUND: workline_id={workline_id}")
        runtime_status = _enum_value(workline.runtime_status)
        if runtime_status != WorkLineRuntimeStatus.READY.value:
            raise WorkLineSafetyBlocked(f"WORKLINE_{runtime_status}: workline_id={workline_id}")

    async def handle_estop(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        source_inbox_id: int | None = None,
        source_device_id: int | None = None,
        source_command_id: int | None = None,
        trigger_payload: dict[str, Any] | None = None,
    ) -> WorklineSafetyIncident:
        """处理 WorkLine 级急停上报：冻结主表投影并排空未完成工作。"""

        workline = await self.workline_repository.get_for_update(db, workline_id)
        if workline is None:
            raise WorkLineSafetyBlocked(f"WORKLINE_NOT_FOUND: workline_id={workline_id}")

        incident = await self.incident_repository.get_active_for_workline(db, workline_id)
        if incident is None:
            incident = WorklineSafetyIncident(
                workline_id=workline_id,
                source_inbox_id=source_inbox_id,
                source_device_id=source_device_id,
                source_command_id=source_command_id,
                trigger_payload_json=_bounded_safety_json(
                    dict(trigger_payload or {}),
                    max_bytes=SAFETY_TRIGGER_PAYLOAD_MAX_BYTES,
                ),
            )
            db.add(incident)
            await db.flush()

        now = timezone.now_for_db()
        workline.runtime_status = WorkLineRuntimeStatus.ESTOPPED
        workline.active_safety_incident_id = incident.id
        workline.stopped_at = workline.stopped_at or now
        workline.stopped_reason = "ESTOP_PRESSED"
        await db.flush()
        await db.commit()

        try:
            session_count = await self.session_repository.fail_open_by_workline(
                db,
                workline_id,
                incident_id=cast("int", incident.id),
            )
            outbox_count = await self.outbox_repository.cancel_active_by_workline(
                db,
                workline_id,
                incident_id=cast("int", incident.id),
            )
            command_count = await self.command_repository.cancel_active_by_workline(
                db,
                workline_id,
                incident_id=cast("int", incident.id),
            )
            device_count = await self.device_service.mark_workline_safety_error(
                db,
                workline_id=workline_id,
                auto_commit=False,
            )
        except Exception as exc:
            logger.error(f"WorkLine 急停冻结已提交，但排空失败: workline_id={workline_id}, error={exc}")
            incident.drain_status = "FAILED"
            incident.drain_error_json = _bounded_safety_json(
                {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
                max_bytes=SAFETY_EVIDENCE_MAX_BYTES,
            )
            await db.flush()
            await db.commit()
            return incident

        incident.drain_status = "COMPLETED"
        incident.drain_error_json = {}
        incident.evidence_json = _bounded_safety_json(
            {
                "sessions_failed": session_count,
                "outboxes_cancelled": outbox_count,
                "commands_cancelled": command_count,
                "devices_marked_error": device_count,
            },
            max_bytes=SAFETY_EVIDENCE_MAX_BYTES,
        )
        await db.flush()
        await db.commit()
        return incident

    async def simulate_estop(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        reason: str | None = None,
        source_device_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> WorklineSafetyIncident:
        """沙箱/开发环境模拟 WorkLine 软件急停。"""

        trigger_payload = {
            "event_type": "ESTOP_PRESSED",
            "source": "sandbox",
            **dict(payload or {}),
        }
        if reason:
            trigger_payload["reason"] = reason

        incident = await self.handle_estop(
            db,
            workline_id=workline_id,
            source_device_id=source_device_id,
            trigger_payload=trigger_payload,
        )
        if reason:
            incident.reason = reason
        await db.flush()
        return incident

    async def clear_estop(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        checks: dict[str, bool],
        reason: str | None = None,
        operator_id: int | None = None,
    ) -> WorklineSafetyIncident:
        """人工确认 checklist 后恢复 WorkLine 到 READY。"""

        if not checks or not all(checks.values()):
            raise ValueError("急停恢复 checklist 必须全部确认")

        workline = await self.workline_repository.get_for_update(db, workline_id)
        if workline is None:
            raise ValueError(f"工作线不存在: {workline_id}")
        if _enum_value(workline.runtime_status) != WorkLineRuntimeStatus.ESTOPPED.value:
            raise ValueError("工作线当前不处于急停状态")

        incident = await self.incident_repository.get_active_for_workline(db, workline_id)
        if incident is None:
            raise ValueError("未找到生效中的急停事件")

        now = timezone.now_for_db()
        incident.status = WorklineSafetyIncidentStatus.CLEARED
        incident.recovery_check_json = dict(checks)
        incident.clear_reason = reason
        incident.cleared_by = operator_id
        incident.cleared_at = now
        released_device_count = await self.device_service.clear_workline_safety_error(
            db,
            workline_id=workline_id,
            auto_commit=False,
        )
        incident.release_evidence_json = _bounded_safety_json(
            {
                "released_device_count": released_device_count,
                "released_device_error_code": "WORKLINE_ESTOPPED",
            },
            max_bytes=SAFETY_EVIDENCE_MAX_BYTES,
        )

        workline.runtime_status = WorkLineRuntimeStatus.READY
        workline.active_safety_incident_id = None
        workline.resumed_at = now
        await db.flush()
        return incident


workline_safety_service = WorkLineSafetyService()


__all__ = [
    "SAFETY_EVIDENCE_MAX_BYTES",
    "SAFETY_TRIGGER_PAYLOAD_MAX_BYTES",
    "WorkLineSafetyBlocked",
    "WorkLineSafetyService",
    "workline_safety_service",
]
