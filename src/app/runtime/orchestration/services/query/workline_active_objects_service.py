"""WorklineActiveObjects Phase4 read model service."""

from __future__ import annotations

from collections import defaultdict
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.app.active_objects.registry import ActiveObjectFact, ActiveObjectRegistry
from src.app.runtime.orchestration.repositories.runtime_hold_repository import (
    RuntimeHoldRepository,
    runtime_hold_repository,
)
from src.app.runtime.orchestration.services.query.active_object_fact_provider import (
    runtime_active_object_fact_provider,
)
from src.app.runtime.orchestration.services.query.material_location_query_service import (
    MaterialLocationQueryService,
    MaterialLocationResult,
    material_location_query_service,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class WorklineActiveObjectConflictState(str, Enum):
    """WorklineActiveObjects 冲突展示状态。"""

    OK = "OK"
    TRANSIENT = "TRANSIENT"
    RECONCILING = "RECONCILING"


class RuntimeHoldView(BaseModel):
    """Active object 关联 RuntimeHold 展示字段。"""

    reason_code: str | None = None
    freeze_scope: str | None = None
    allowed_next_effect_scope: str | None = None


class WorklineActiveObjectView(BaseModel):
    """单个 active object 只读视图。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    object_type: str
    object_key: str
    conflict_state: WorklineActiveObjectConflictState
    primary_source: str | None = None
    all_sources: list[str] = Field(default_factory=list)
    operator_hint: str | None = None
    location_summary: MaterialLocationResult | None = None
    runtime_hold: RuntimeHoldView | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class WorklineActiveObjectsResponse(BaseModel):
    """WorkLine active objects 聚合响应。"""

    workline_id: int
    objects: list[WorklineActiveObjectView] = Field(default_factory=list)
    truncated: bool = False
    total_count: int = 0


class WorklineActiveObjectsService:
    """WorkLine 当前作业对象只读聚合视图。"""

    def __init__(
        self,
        *,
        active_fact_provider: Any = runtime_active_object_fact_provider,
        material_location_query_service: MaterialLocationQueryService | Any = material_location_query_service,
        runtime_hold_repository: RuntimeHoldRepository | Any = runtime_hold_repository,
        active_object_registry: ActiveObjectRegistry | None = None,
        max_objects: int = 200,
    ) -> None:
        self.active_fact_provider = active_fact_provider
        self.material_location_query_service = material_location_query_service
        self.runtime_hold_repository = runtime_hold_repository
        self.active_object_registry = active_object_registry or ActiveObjectRegistry()
        self.max_objects = max_objects

    async def get_active_objects(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        now: datetime | None = None,
    ) -> WorklineActiveObjectsResponse:
        """读取 WorkLine active/current 对象视图。"""

        resolved_now = now or timezone.now_utc()
        facts = await self.active_fact_provider.list_active_object_facts(db, workline_id=workline_id)
        holds = await self.runtime_hold_repository.get_active_blocking_by_workline(db, workline_id)
        holds_by_object = _index_holds_by_object(holds)

        grouped: dict[tuple[str, str], list[ActiveObjectFact]] = defaultdict(list)
        for fact in facts:
            grouped[(fact.object_type.upper(), fact.object_code)].append(fact)

        views: list[WorklineActiveObjectView] = []
        for (object_type, object_key), object_facts in grouped.items():
            if not object_facts:
                continue
            resolution = self.active_object_registry.resolve(object_facts, now=resolved_now)
            conflict_state = _map_conflict_state(resolution.status)
            location_summary = await self.material_location_query_service.query_by_workline_active_object(
                db,
                workline_id=workline_id,
                object_type=object_type,
                object_key=object_key,
            )
            views.append(
                WorklineActiveObjectView(
                    object_type=object_type,
                    object_key=object_key,
                    conflict_state=conflict_state,
                    primary_source=_primary_source(resolution.owner_kind, resolution.owner_code),
                    all_sources=[_source_label(fact.owner_kind, fact.owner_code) for fact in object_facts],
                    operator_hint=_operator_hint(conflict_state),
                    location_summary=location_summary,
                    runtime_hold=holds_by_object.get(object_key),
                    evidence_refs=resolution.evidence_refs,
                )
            )

        total_count = len(views)
        limited = views[: self.max_objects]
        return WorklineActiveObjectsResponse(
            workline_id=workline_id,
            objects=limited,
            truncated=total_count > len(limited),
            total_count=total_count,
        )


def _map_conflict_state(status: str) -> WorklineActiveObjectConflictState:
    if status == "TRANSIENT":
        return WorklineActiveObjectConflictState.TRANSIENT
    if status == "RECONCILING":
        return WorklineActiveObjectConflictState.RECONCILING
    return WorklineActiveObjectConflictState.OK


def _primary_source(owner_kind: str | None, owner_code: str | None) -> str | None:
    if not owner_kind or not owner_code:
        return None
    return _source_label(owner_kind, owner_code)


def _source_label(owner_kind: str, owner_code: str) -> str:
    return f"{owner_kind}:{owner_code}"


def _operator_hint(conflict_state: WorklineActiveObjectConflictState) -> str | None:
    if conflict_state == WorklineActiveObjectConflictState.RECONCILING:
        return "RECONCILIATION_REQUIRED"
    if conflict_state == WorklineActiveObjectConflictState.TRANSIENT:
        return "WAIT_TRANSIENT_HANDOFF"
    return None


def _index_holds_by_object(holds: list[Any]) -> dict[str, RuntimeHoldView]:
    indexed: dict[str, RuntimeHoldView] = {}
    for hold in holds:
        evidence = dict(getattr(hold, "evidence_snapshot_json", None) or {})
        object_key = evidence.get("object_key") or evidence.get("bin_code") or evidence.get("pkg_code")
        if not object_key:
            continue
        indexed[str(object_key)] = RuntimeHoldView(
            reason_code=getattr(hold, "source_reason", None),
            freeze_scope=evidence.get("freeze_scope"),
            allowed_next_effect_scope=evidence.get("allowed_next_effect_scope"),
        )
    return indexed


workline_active_objects_service = WorklineActiveObjectsService()


__all__ = [
    "RuntimeHoldView",
    "WorklineActiveObjectConflictState",
    "WorklineActiveObjectView",
    "WorklineActiveObjectsResponse",
    "WorklineActiveObjectsService",
    "workline_active_objects_service",
]
