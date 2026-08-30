"""WorklineActiveObjects 只读视图合同。"""

from __future__ import annotations

import importlib
from datetime import timedelta
from pathlib import Path

import pytest

from src.app.active_objects.registry import ActiveObjectFact
from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
    WorklineActiveObjectConflictState,
    WorklineActiveObjectsService,
    workline_active_objects_service,
)
from src.app.workline.repositories.workline_repository import workline_repository
from src.utils.timezone import timezone


class _TargetRows:
    def __init__(
        self,
        facts: list[ActiveObjectFact],
        *,
        location_scope: str | None = None,
        location_code: str | None = None,
        location_conflict: bool = False,
    ) -> None:
        self.facts = facts
        self.location_scope = location_scope
        self.location_code = location_code
        self.location_conflict = location_conflict

    async def list_target_active_object_facts(self, _db, *, workline_id: int, limit: int):
        return [
            {
                "object_type": fact.object_type,
                "object_key": fact.object_code,
                "owner_kind": fact.owner_kind,
                "owner_code": fact.owner_code,
                "evidence_ref": fact.evidence_ref,
                "location_scope": self.location_scope,
                "location_code": self.location_code,
                "location_conflict": self.location_conflict,
                "presence_type": fact.presence_type,
                "transient_until": fact.transient_until,
            }
            for fact in self.facts[:limit]
        ]


def test_default_active_objects_composition_uses_only_target_repository() -> None:
    """API 默认实例只能消费 target aggregate repository，不得回接 Task 5 DELETE owner。"""

    assert workline_active_objects_service.target_repository is workline_repository
    assert not hasattr(workline_active_objects_service, "active_fact_provider")
    assert not hasattr(workline_active_objects_service, "material_location_query_service")
    module = importlib.import_module("src.app.runtime.orchestration.services.query.workline_active_objects_service")
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "active_object_fact_provider" not in source
    assert "material_location_query_service" not in source


@pytest.mark.asyncio
async def test_workline_active_objects_returns_ok_for_single_source() -> None:
    """单来源 active object 返回 OK。"""

    service = WorklineActiveObjectsService(
        target_repository=_TargetRows(
            [
                ActiveObjectFact(
                    object_code="BIN-OK",
                    object_type="BIN",
                    owner_kind="ON_CONVEYOR",
                    owner_code="Q-IN",
                    evidence_ref="queue:BIN-OK",
                )
            ],
            location_scope="CONVEYOR_QUEUE",
            location_code="Q-IN",
        ),
    )

    response = await service.get_active_objects(None, workline_id=1)

    assert response.total_count == 1
    assert response.truncated is False
    assert response.objects[0].conflict_state == WorklineActiveObjectConflictState.OK
    assert response.objects[0].primary_source == "ON_CONVEYOR:Q-IN"
    assert "runtime_hold" not in type(response.objects[0]).model_fields


@pytest.mark.asyncio
async def test_workline_active_objects_marks_multi_owner_reconciling() -> None:
    """ON_CONVEYOR + AT_WORK_POSITION 同时出现时进入 RECONCILING。"""

    service = WorklineActiveObjectsService(
        target_repository=_TargetRows(
            [
                ActiveObjectFact("BIN-CONFLICT", "ON_CONVEYOR", "Q-IN", "queue:BIN-CONFLICT", object_type="BIN"),
                ActiveObjectFact(
                    "BIN-CONFLICT",
                    "AT_WORK_POSITION",
                    "WP-01",
                    "position:BIN-CONFLICT",
                    object_type="BIN",
                ),
            ],
            location_scope="CONVEYOR_QUEUE",
            location_code="Q-IN",
        ),
    )

    response = await service.get_active_objects(None, workline_id=1)

    assert response.objects[0].conflict_state == WorklineActiveObjectConflictState.RECONCILING
    assert response.objects[0].operator_hint == "RECONCILIATION_REQUIRED"
    assert response.objects[0].all_sources == ["ON_CONVEYOR:Q-IN", "AT_WORK_POSITION:WP-01"]


@pytest.mark.asyncio
async def test_workline_active_objects_promotes_location_reconciling_to_object_state() -> None:
    """单 owner 本身 OK 时，位置查询 RECONCILING 也必须展示在对象顶层。"""

    service = WorklineActiveObjectsService(
        target_repository=_TargetRows(
            [
                ActiveObjectFact(
                    object_code="BIN-LOCATION-CONFLICT",
                    object_type="BIN",
                    owner_kind="ON_CONVEYOR",
                    owner_code="Q-IN",
                    evidence_ref="queue:BIN-LOCATION-CONFLICT",
                )
            ],
            location_scope="CONVEYOR_QUEUE",
            location_code="Q-IN",
            location_conflict=True,
        ),
    )

    response = await service.get_active_objects(None, workline_id=1)

    assert response.objects[0].conflict_state == WorklineActiveObjectConflictState.RECONCILING
    assert response.objects[0].operator_hint == "RECONCILIATION_REQUIRED"
    assert response.objects[0].location_summary is not None
    assert response.objects[0].location_summary.conflict_state == WorklineActiveObjectConflictState.RECONCILING


@pytest.mark.asyncio
async def test_workline_active_objects_transient_window_expires_to_reconciling() -> None:
    """IN_TRANSFER + ON_CONVEYOR 在 transient window 内返回 TRANSIENT，超时后返回 RECONCILING。"""

    now = timezone.now_utc()
    facts = [
        ActiveObjectFact(
            "BIN-MOVE",
            "IN_TRANSFER",
            "CTU-01",
            "transfer:BIN-MOVE",
            object_type="BIN",
            presence_type="IN_TRANSFER",
            transient_until=now + timedelta(seconds=30),
        ),
        ActiveObjectFact(
            "BIN-MOVE",
            "ON_CONVEYOR",
            "Q-IN",
            "queue:BIN-MOVE",
            object_type="BIN",
            presence_type="ON_CONVEYOR",
            transient_until=now + timedelta(seconds=30),
        ),
    ]
    service = WorklineActiveObjectsService(
        target_repository=_TargetRows(facts, location_scope="CONVEYOR_QUEUE", location_code="Q-IN"),
    )

    transient = await service.get_active_objects(None, workline_id=1, now=now)
    expired = await service.get_active_objects(None, workline_id=1, now=now + timedelta(seconds=31))

    assert transient.objects[0].conflict_state == WorklineActiveObjectConflictState.TRANSIENT
    assert expired.objects[0].conflict_state == WorklineActiveObjectConflictState.RECONCILING
