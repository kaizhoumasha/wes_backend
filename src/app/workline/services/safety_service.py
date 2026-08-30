"""WorkLine 安全控制服务。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.app.device.repositories.command_repository import DeviceCommandRepository
from src.app.workline.models.safety import WorklineSafetyIncident, WorklineSafetyIncidentStatus
from src.app.workline.repositories.safety_incident_repository import WorklineSafetyIncidentRepository
from src.app.workline.repositories.workline_repository import workline_repository as default_workline_repository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.workline.repositories.workline_repository import WorkLineRepository


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
        command_repository: DeviceCommandRepository | None = None,
    ) -> None:
        """初始化安全服务依赖。"""

        self.workline_repository = workline_repository or default_workline_repository
        self.incident_repository = incident_repository or WorklineSafetyIncidentRepository()
        self.command_repository = command_repository or DeviceCommandRepository()

    async def assert_accepting_work(self, db: AsyncSession, *, workline_id: int) -> None:
        """校验 WorkLine 当前可接收新事件/新任务。"""

        workline = await self.workline_repository.get_for_update(db, workline_id, populate_existing=True)
        if workline is None:
            raise WorkLineSafetyBlocked(f"WORKLINE_NOT_FOUND: workline_id={workline_id}")
        if not bool(getattr(workline, "is_active", False)):
            raise WorkLineSafetyBlocked(f"WORKLINE_INACTIVE: workline_id={workline_id}")
        if await self.incident_repository.get_active_for_workline(db, workline_id) is not None:
            raise WorkLineSafetyBlocked(f"WORKLINE_ESTOPPED: workline_id={workline_id}")

    async def handle_estop(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        source_evidence_id: int | None = None,
        source_device_id: int | None = None,
        source_command_id: int | None = None,
        trigger_payload: dict[str, Any] | None = None,
    ) -> WorklineSafetyIncident:
        """在调用方事务内建立/复用 incident 并冻结运行态；排空由 incident worker 承担。"""

        workline = await self.workline_repository.get_for_update(db, workline_id)
        if workline is None:
            raise WorkLineSafetyBlocked(f"WORKLINE_NOT_FOUND: workline_id={workline_id}")

        incident = await self.incident_repository.get_active_for_workline(db, workline_id)
        if incident is None:
            incident = WorklineSafetyIncident(
                workline_id=workline_id,
                source_evidence_id=source_evidence_id,
                source_device_id=source_device_id,
                source_command_id=source_command_id,
                trigger_payload_json=_bounded_safety_json(
                    dict(trigger_payload or {}),
                    max_bytes=SAFETY_TRIGGER_PAYLOAD_MAX_BYTES,
                ),
            )
            db.add(incident)
            await db.flush()
        elif source_evidence_id is not None and incident.source_evidence_id is None:
            incident.source_evidence_id = source_evidence_id

        incident.drain_status = "PENDING"
        incident.drain_error_json = {}

        await db.flush()
        return incident

    async def drain_one(self, db: AsyncSession, *, command_limit: int = 100) -> WorklineSafetyIncident | None:
        """有界关闭尚未发送的命令；已发送或结果不确定的身份保持不变。"""

        incident = await self.incident_repository.claim_next_drain(db)
        if incident is None:
            return None
        if incident.workline_id is None:
            incident.drain_status = "FAILED"
            incident.drain_error_json = {"reason": "MISSING_WORKLINE_ID"}
            await db.flush()
            return incident

        command_count = await self.command_repository.fail_pending_by_workline(
            db,
            workline_id=incident.workline_id,
            failure_code="WORKLINE_ESTOPPED_BEFORE_SEND",
            limit=command_limit,
        )
        previous_count = int((incident.evidence_json or {}).get("pending_commands_failed", 0))
        incident.evidence_json = _bounded_safety_json(
            {
                "pending_commands_failed": previous_count + command_count,
                "dispatched_acknowledged_or_reconciling_commands_preserved": True,
            },
            max_bytes=SAFETY_EVIDENCE_MAX_BYTES,
        )
        incident.drain_status = "PENDING" if command_count == command_limit else "COMPLETED"
        incident.drain_error_json = {}
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
        """人工确认 checklist 后解除冻结，WorkLine 回到 STOPPED 等待现场 START。"""

        if not checks or not all(checks.values()):
            raise ValueError("急停恢复 checklist 必须全部确认")

        workline = await self.workline_repository.get_for_update(db, workline_id)
        if workline is None:
            raise ValueError(f"工作线不存在: {workline_id}")
        incident = await self.incident_repository.get_active_for_workline(db, workline_id)
        if incident is None:
            raise ValueError("未找到生效中的急停事件")

        now = timezone.now_for_db()
        incident.status = WorklineSafetyIncidentStatus.CLEARED
        incident.recovery_check_json = dict(checks)
        incident.clear_reason = reason
        incident.cleared_by = operator_id
        incident.cleared_at = now
        incident.release_evidence_json = _bounded_safety_json(
            {
                "device_runtime_authority": "ECS_STATUS_OBSERVATION",
                "released_device_rows": 0,
                "workline_runtime_status": "STOPPED",
            },
            max_bytes=SAFETY_EVIDENCE_MAX_BYTES,
        )
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
