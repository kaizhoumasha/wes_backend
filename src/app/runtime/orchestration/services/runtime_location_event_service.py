"""RuntimeLocationEvent 位置事实 Service。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.models.runtime_location_event import RuntimeLocationEvent
from src.app.runtime.orchestration.repositories.runtime_location_event_repository import (
    RuntimeLocationEventRepository,
    runtime_location_event_repository,
)
from src.core.base_service import BaseService
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime


class RuntimeLocationEventService(BaseService[RuntimeLocationEvent, RuntimeLocationEventRepository]):
    """集中生成和查询作业期位置事实。"""

    def __init__(self, repository: RuntimeLocationEventRepository | None = None) -> None:
        super().__init__(repository or runtime_location_event_repository, enable_cache=False)

    async def record(
        self,
        db: Any,
        *,
        object_type: str,
        object_key: str,
        location_scope: str,
        location_code: str,
        business_step: str,
        source: str,
        evidence_json: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        source_event_id: str | None = None,
        source_version: str | None = None,
        idempotency_key: str | None = None,
        external_reference_type: str | None = None,
        external_reference_value: str | None = None,
        provider_code: str | None = None,
        occurred_at: datetime | None = None,
        auto_commit: bool = True,
    ) -> RuntimeLocationEvent:
        """按幂等键创建或复用位置事实。"""

        resolved_key = idempotency_key or self.build_idempotency_key(
            object_type=object_type,
            object_key=object_key,
            location_scope=location_scope,
            location_code=location_code,
            business_step=business_step,
            source=source,
            source_event_id=source_event_id,
        )
        existing = await self.repo.get_by_idempotency_key(db, resolved_key)
        if existing is not None:
            return existing

        data: dict[str, Any] = {
            "object_type": _required(object_type, "object_type"),
            "object_key": _required(object_key, "object_key"),
            "location_scope": _required(location_scope, "location_scope"),
            "location_code": _required(location_code, "location_code"),
            "business_step": _required(business_step, "business_step"),
            "source": _required(source, "source"),
            "evidence_json": evidence_json or {},
            "correlation_id": _optional(correlation_id),
            "source_event_id": _optional(source_event_id),
            "source_version": _optional(source_version),
            "idempotency_key": resolved_key,
            "external_reference_type": _optional(external_reference_type),
            "external_reference_value": _optional(external_reference_value),
            "provider_code": _optional(provider_code),
            "occurred_at": occurred_at or timezone.now_for_db(),
        }
        created = await self.repo.create_idempotent_by_key(db, data)
        if auto_commit:
            await self._commit_mutation(db)
        return created

    @staticmethod
    def build_idempotency_key(
        *,
        object_type: str,
        object_key: str,
        location_scope: str,
        location_code: str,
        business_step: str,
        source: str,
        source_event_id: str | None = None,
    ) -> str:
        """按位置事实粒度生成派生幂等键。"""

        parts = (
            "runtime-location",
            _required(source_event_id or "no-source-event", "source_event_id"),
            _required(source, "source"),
            _required(object_type, "object_type"),
            _required(object_key, "object_key"),
            _required(location_scope, "location_scope"),
            _required(location_code, "location_code"),
            _required(business_step, "business_step"),
        )
        return ":".join(_escape_key_part(part) for part in parts)

    async def list_by_object(
        self,
        db: Any,
        *,
        object_type: str,
        object_key: str,
    ) -> list[RuntimeLocationEvent]:
        """按对象业务键查询位置事实历史。"""

        return await self.repo.list_by_object(
            db,
            object_type=_required(object_type, "object_type"),
            object_key=_required(object_key, "object_key"),
        )

    async def list_by_correlation_id(self, db: Any, *, correlation_id: str) -> list[RuntimeLocationEvent]:
        """按 correlation_id 查询位置事实历史。"""

        return await self.repo.list_by_correlation_id(db, correlation_id=_required(correlation_id, "correlation_id"))

    async def list_by_external_reference(
        self,
        db: Any,
        *,
        external_reference_type: str,
        external_reference_value: str,
        provider_code: str | None = None,
    ) -> list[RuntimeLocationEvent]:
        """按外部引用查询位置事实历史。"""

        return await self.repo.list_by_external_reference(
            db,
            external_reference_type=_required(external_reference_type, "external_reference_type"),
            external_reference_value=_required(external_reference_value, "external_reference_value"),
            provider_code=_optional(provider_code),
        )


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    return normalized


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _escape_key_part(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


runtime_location_event_service = RuntimeLocationEventService()


__all__ = ["RuntimeLocationEventService", "runtime_location_event_service"]
