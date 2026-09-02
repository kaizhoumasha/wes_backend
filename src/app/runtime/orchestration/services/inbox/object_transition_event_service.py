"""统一对象状态迁移事件 Service。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.models.object_transition_event import ObjectTransitionDomain, ObjectTransitionEvent
from src.app.runtime.orchestration.repositories.object_transition_event_repository import (
    ObjectTransitionEventRepository,
    object_transition_event_repository,
)
from src.app.runtime.orchestration.services._text import escape_key_part as _escape_key_part
from src.app.runtime.orchestration.services._text import normalize_required_text as _required
from src.core.base_service import BaseService
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime


class ObjectTransitionEventService(BaseService[ObjectTransitionEvent, ObjectTransitionEventRepository]):
    """集中生成和持久化对象状态迁移事件。"""

    def __init__(self, repository: ObjectTransitionEventRepository | None = None) -> None:
        super().__init__(repository or object_transition_event_repository, enable_cache=False)

    @staticmethod
    def build_idempotency_key(
        *,
        source_event_id: str,
        domain: ObjectTransitionDomain | str,
        object_type: str,
        object_key: str,
        projection_type: str,
        to_state: str,
        reason_code: str,
    ) -> str:
        """按派生 transition 粒度生成幂等键。"""

        parts = (
            "object-transition",
            _required(source_event_id, "source_event_id"),
            _domain_value(domain),
            _required(object_type, "object_type"),
            _required(object_key, "object_key"),
            _required(projection_type, "projection_type"),
            _required(to_state, "to_state"),
            _required(reason_code, "reason_code"),
        )
        return ":".join(_escape_key_part(part) for part in parts)

    async def record_transition(
        self,
        db: Any,
        *,
        domain: ObjectTransitionDomain | str,
        object_type: str,
        object_key: str,
        projection_type: str,
        from_state: str | None,
        to_state: str,
        reason_code: str,
        source_event_id: str,
        source_ref_json: dict[str, Any] | None = None,
        evidence_json: dict[str, Any] | None = None,
        workline_session_id: int | None = None,
        trace_id: str | None = None,
        occurred_at: datetime | None = None,
        idempotency_key: str | None = None,
        auto_commit: bool = True,
    ) -> ObjectTransitionEvent:
        """按派生幂等键创建或复用对象迁移事件。"""

        resolved_key = idempotency_key or self.build_idempotency_key(
            source_event_id=source_event_id,
            domain=domain,
            object_type=object_type,
            object_key=object_key,
            projection_type=projection_type,
            to_state=to_state,
            reason_code=reason_code,
        )
        existing = await self.repo.get_by_idempotency_key(db, resolved_key)
        if existing is not None:
            return existing

        data: dict[str, Any] = {
            "domain": _domain_value(domain),
            "object_type": _required(object_type, "object_type"),
            "object_key": _required(object_key, "object_key"),
            "projection_type": _required(projection_type, "projection_type"),
            "from_state": from_state,
            "to_state": _required(to_state, "to_state"),
            "reason_code": _required(reason_code, "reason_code"),
            "source_event_id": _required(source_event_id, "source_event_id"),
            "source_ref_json": source_ref_json or {},
            "evidence_json": evidence_json or {},
            "workline_session_id": workline_session_id,
            "trace_id": trace_id,
            "occurred_at": occurred_at or timezone.now_for_db(),
            "idempotency_key": resolved_key,
        }
        created = await self.repo.create_idempotent_by_key(db, data)
        if auto_commit:
            await self._commit_mutation(db)
        return created

    async def get_by_trace_id(self, db: Any, trace_id: str) -> list[ObjectTransitionEvent]:
        """查询指定 trace 的迁移事件。"""

        return await self.repo.list_by_trace_id(db, trace_id)

    async def get_by_workline_session_id(
        self,
        db: Any,
        workline_session_id: int,
    ) -> list[ObjectTransitionEvent]:
        """查询指定工作线 session 的迁移事件。"""

        return await self.repo.list_by_workline_session_id(db, workline_session_id)

    async def get_by_object(
        self,
        db: Any,
        *,
        domain: ObjectTransitionDomain | str,
        object_type: str,
        object_key: str,
    ) -> list[ObjectTransitionEvent]:
        """查询指定对象的迁移事件。"""

        return await self.repo.list_by_object(
            db,
            domain=domain,
            object_type=object_type,
            object_key=object_key,
        )

    async def get_by_source_event(
        self,
        db: Any,
        *,
        domain: ObjectTransitionDomain | str,
        source_event_id: str,
    ) -> list[ObjectTransitionEvent]:
        """查询同一来源事实派生出的迁移事件。"""

        return await self.repo.list_by_source_event(
            db,
            domain=domain,
            source_event_id=source_event_id,
        )


def _domain_value(domain: ObjectTransitionDomain | str) -> str:
    return domain.value if isinstance(domain, ObjectTransitionDomain) else _required(str(domain), "domain")


object_transition_event_service = ObjectTransitionEventService()


__all__ = ["ObjectTransitionEventService", "object_transition_event_service"]
