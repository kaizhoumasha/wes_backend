"""WorklineActiveObjects runtime read model service."""

from __future__ import annotations

from collections import defaultdict
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.app.active_objects.registry import ActiveObjectFact, ActiveObjectRegistry
from src.app.workline.repositories.workline_repository import WorkLineRepository, workline_repository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class WorklineActiveObjectConflictState(str, Enum):
    """WorklineActiveObjects 冲突展示状态。"""

    OK = "OK"
    TRANSIENT = "TRANSIENT"
    RECONCILING = "RECONCILING"


class WorklineActiveObjectLocationView(BaseModel):
    """来自具体 Resource projection 的当前位置证据。"""

    location_scope: str
    location_code: str
    conflict_state: WorklineActiveObjectConflictState
    evidence_refs: list[str] = Field(default_factory=list)


class WorklineActiveObjectView(BaseModel):
    """单个 active object 只读视图。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    object_type: str
    object_key: str
    conflict_state: WorklineActiveObjectConflictState
    primary_source: str | None = None
    all_sources: list[str] = Field(default_factory=list)
    operator_hint: str | None = None
    location_summary: WorklineActiveObjectLocationView | None = None
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
        target_repository: WorkLineRepository | Any = workline_repository,
        active_object_registry: ActiveObjectRegistry | None = None,
        max_objects: int = 200,
    ) -> None:
        self.target_repository = target_repository
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
        rows = await self.target_repository.list_target_active_object_facts(
            db,
            workline_id=workline_id,
            limit=max(self.max_objects * 3, self.max_objects + 1),
        )
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(str(row["object_type"]).upper(), str(row["object_key"]))].append(row)

        views: list[WorklineActiveObjectView] = []
        for (object_type, object_key), object_rows in grouped.items():
            if not object_rows:
                continue
            object_facts = [
                ActiveObjectFact(
                    object_code=object_key,
                    object_type=object_type,
                    owner_kind=str(row["owner_kind"]),
                    owner_code=str(row["owner_code"]),
                    evidence_ref=str(row["evidence_ref"]),
                    presence_type=str(row["presence_type"]) if row.get("presence_type") else None,
                    transient_until=row.get("transient_until"),
                )
                for row in object_rows
            ]
            resolution = self.active_object_registry.resolve(object_facts, now=resolved_now)
            conflict_state = _map_conflict_state(resolution.status)
            location_summary = _location_summary(object_rows)
            if (
                location_summary is not None
                and location_summary.conflict_state == WorklineActiveObjectConflictState.RECONCILING
            ):
                conflict_state = WorklineActiveObjectConflictState.RECONCILING
            views.append(
                WorklineActiveObjectView(
                    object_type=object_type,
                    object_key=object_key,
                    conflict_state=conflict_state,
                    primary_source=_primary_source(resolution.owner_kind, resolution.owner_code),
                    all_sources=[_source_label(fact.owner_kind, fact.owner_code) for fact in object_facts],
                    operator_hint=_operator_hint(conflict_state),
                    location_summary=location_summary,
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


def _location_summary(object_rows: list[dict[str, Any]]) -> WorklineActiveObjectLocationView | None:
    location_rows = [row for row in object_rows if row.get("location_scope") and row.get("location_code")]
    if not location_rows:
        return None
    locations = {(str(row["location_scope"]), str(row["location_code"])) for row in location_rows}
    conflict_state = (
        WorklineActiveObjectConflictState.RECONCILING
        if len(locations) > 1 or any(bool(row.get("location_conflict")) for row in location_rows)
        else WorklineActiveObjectConflictState.OK
    )
    location_scope, location_code = sorted(locations)[0]
    return WorklineActiveObjectLocationView(
        location_scope=location_scope,
        location_code=location_code,
        conflict_state=conflict_state,
        evidence_refs=[str(row["evidence_ref"]) for row in location_rows],
    )


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


workline_active_objects_service = WorklineActiveObjectsService()


__all__ = [
    "WorklineActiveObjectConflictState",
    "WorklineActiveObjectLocationView",
    "WorklineActiveObjectView",
    "WorklineActiveObjectsResponse",
    "WorklineActiveObjectsService",
    "workline_active_objects_service",
]
