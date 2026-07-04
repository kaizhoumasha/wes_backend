"""Active object fact provider for Phase4 read models."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from src.app.active_objects.registry import ActiveObjectFact
from src.app.runtime.orchestration.repositories.conveyor_queue_membership_repository import (
    ConveyorQueueMembershipRepository,
    conveyor_queue_membership_repository,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RuntimeActiveObjectFactProvider:
    """从 runtime active projections 提取只读 active object facts。"""

    def __init__(
        self,
        *,
        membership_repository: ConveyorQueueMembershipRepository = conveyor_queue_membership_repository,
        transient_seconds: int = 30,
    ) -> None:
        self.membership_repository = membership_repository
        self.transient_seconds = transient_seconds

    async def list_active_object_facts(self, db: AsyncSession, *, workline_id: int) -> list[ActiveObjectFact]:
        """为 WorklineActiveObjects 聚合 ActiveObjectRegistry facts。"""

        memberships = await self.membership_repository.list_active_by_workline(
            db,
            workline_id=workline_id,
            limit=500,
        )
        facts: list[ActiveObjectFact] = []
        for membership in memberships:
            object_code = str(membership.bin_code or membership.placeholder_key or "")
            if not object_code:
                continue
            entered_at = _utc_from_unix_ms(getattr(membership, "entered_at", None)) or timezone.now_utc()
            facts.append(
                ActiveObjectFact(
                    object_code=object_code,
                    object_type="BIN",
                    owner_kind="ON_CONVEYOR",
                    owner_code=str(membership.queue_code),
                    evidence_ref=f"conveyor_queue_membership:{membership.id}",
                    presence_type="ON_CONVEYOR",
                    transient_until=entered_at + timedelta(seconds=self.transient_seconds),
                )
            )
        return facts

    async def list_material_location_facts(
        self,
        db: AsyncSession,
        *,
        object_type: str | None = None,
        object_key: str | None = None,
        workline_id: int | None = None,
    ) -> list[Any]:
        """为 MaterialLocationQuery 提供 active projection 位置 facts。"""

        if workline_id is None:
            return []
        normalized_object_type = object_type.strip().upper() if object_type else "BIN"
        if normalized_object_type != "BIN":
            return []
        memberships = await self.membership_repository.list_active_by_workline(
            db,
            workline_id=workline_id,
            limit=500,
        )
        facts: list[dict[str, Any]] = []
        for membership in memberships:
            candidate_key = str(membership.bin_code or membership.placeholder_key or "")
            if not candidate_key:
                continue
            if object_key is not None and candidate_key != object_key:
                continue
            facts.append(
                {
                    "object_type": "BIN",
                    "object_key": candidate_key,
                    "location_scope": "CONVEYOR_QUEUE",
                    "location_code": membership.queue_code,
                    "source": "ActiveObjectRegistry",
                    "evidence_ref": f"conveyor_queue_membership:{membership.id}",
                    "evidence_json": getattr(membership, "evidence_json", {}) or {},
                    "observed_at": None,
                }
            )
        return facts


runtime_active_object_fact_provider = RuntimeActiveObjectFactProvider()


def _utc_from_unix_ms(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return None
    try:
        return timezone.to_utc(int(value) / 1000)
    except (TypeError, ValueError, OverflowError):
        return None


__all__ = ["RuntimeActiveObjectFactProvider", "runtime_active_object_fact_provider"]
