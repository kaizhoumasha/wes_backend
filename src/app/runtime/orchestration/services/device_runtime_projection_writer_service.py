"""DeviceRuntimeProjection writer service."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.device_runtime_projection import DeviceRuntimeProjection
from src.app.runtime.orchestration.repositories.device_runtime_projection_repository import (
    DeviceRuntimeProjectionRepository,
    device_runtime_projection_repository,
)
from src.app.runtime.orchestration.services.device_dispatch_policy import (
    DeviceDispatchPolicy,
    DeviceRuntimeStatus,
    device_dispatch_policy,
)
from src.core.base_service import BaseService
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_text(value: Any, *, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _normalize_runtime_status(value: Any) -> str:
    status = _optional_text(_enum_value(value))
    if status in DeviceRuntimeStatus.__members__:
        return status
    return DeviceRuntimeStatus.UNKNOWN.value


def _resolve_status_snapshot_ttl_ms(device: Any, policy: DeviceDispatchPolicy) -> int:
    diagnostic_profile = getattr(device, "diagnostic_profile", None)
    if isinstance(diagnostic_profile, dict):
        runtime_profile = diagnostic_profile.get("device_runtime")
        if isinstance(runtime_profile, dict):
            return _positive_int(
                runtime_profile.get("status_snapshot_ttl_ms"),
                default=policy.status_snapshot_ttl_ms,
            )
        return _positive_int(
            diagnostic_profile.get("status_snapshot_ttl_ms"),
            default=policy.status_snapshot_ttl_ms,
        )
    return policy.status_snapshot_ttl_ms


class DeviceRuntimeProjectionWriterService(BaseService[DeviceRuntimeProjection, DeviceRuntimeProjectionRepository]):
    """维护 runtime/orchestration 域的设备运行态持久投影。"""

    def __init__(
        self,
        repository: DeviceRuntimeProjectionRepository = device_runtime_projection_repository,
        policy: DeviceDispatchPolicy = device_dispatch_policy,
    ) -> None:
        super().__init__(repository, enable_cache=False)
        self.policy = policy

    async def upsert_from_device(
        self,
        db: AsyncSession,
        *,
        device: Any,
        evidence_json: dict[str, Any] | None = None,
        auto_commit: bool = True,
    ) -> DeviceRuntimeProjection:
        """从 Device 当前运行态同步一条持久投影。"""

        now = timezone.now_for_db()
        device_code = _required_text(getattr(device, "device_code", None), field_name="device_code")
        runtime_status = _normalize_runtime_status(getattr(device, "device_status", None))
        current_command_id = getattr(device, "current_command_id", None)
        concurrency_limit = _positive_int(getattr(device, "max_concurrent_tasks", None), default=1)
        observed_at = getattr(device, "updated_at", None) or getattr(device, "last_heartbeat_at", None) or now
        status_valid_until = observed_at + timedelta(milliseconds=_resolve_status_snapshot_ttl_ms(device, self.policy))
        in_flight_count = (
            1 if current_command_id is not None or runtime_status == DeviceRuntimeStatus.RUNNING.value else 0
        )
        data = {
            "device_id": getattr(device, "id", None),
            "device_code": device_code,
            "workline_id": getattr(device, "work_line_id", None),
            "device_role": _optional_text(getattr(device, "device_role", None)),
            "provider_code": _optional_text(getattr(device, "vendor_type", None)) or "UNKNOWN_PROVIDER",
            "runtime_status": runtime_status,
            "current_command_id": current_command_id,
            "error_code": _optional_text(getattr(device, "error_code", None)),
            "maintenance_mode": bool(getattr(device, "maintenance_mode", False)),
            "last_heartbeat_at": getattr(device, "last_heartbeat_at", None),
            "status_observed_at": observed_at,
            "status_valid_until": status_valid_until,
            "in_flight_count": in_flight_count,
            "concurrency_limit": concurrency_limit,
            "evidence_json": dict(evidence_json or {}),
        }

        projection = await self.repo.get_by_device_code(db, device_code)
        if projection is None:
            created = await self.repo.create(db, data)
            if created is None:  # pragma: no cover - BaseRepository create either returns or raises
                raise RuntimeError(f"DeviceRuntimeProjection 创建失败: device_code={device_code}")
            projection = created
        else:
            projection_id = getattr(projection, "id", None)
            if not isinstance(projection_id, int):
                raise RuntimeError(f"DeviceRuntimeProjection 缺少 id: device_code={device_code}")
            updated = await self.repo.update(db, projection_id, data)
            if updated is None:  # pragma: no cover - BaseRepository update either returns or raises
                raise RuntimeError(f"DeviceRuntimeProjection 更新失败: device_code={device_code}")
            projection = updated

        if auto_commit:
            await db.commit()
            await db.refresh(projection)
        return projection


device_runtime_projection_writer_service = DeviceRuntimeProjectionWriterService()


__all__ = [
    "DeviceRuntimeProjectionWriterService",
    "device_runtime_projection_writer_service",
]
