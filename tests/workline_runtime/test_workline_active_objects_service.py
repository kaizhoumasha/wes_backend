"""WorklineActiveObjects 只读视图合同。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.app.active_objects.registry import ActiveObjectFact
from src.app.runtime.orchestration.services.query.active_object_fact_provider import RuntimeActiveObjectFactProvider
from src.app.runtime.orchestration.services.query.material_location_query_service import (
    MaterialLocationConflictState,
    MaterialLocationResult,
)
from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
    WorklineActiveObjectConflictState,
    WorklineActiveObjectsService,
)
from src.utils.timezone import timezone


class _Facts:
    def __init__(self, facts: list[ActiveObjectFact]) -> None:
        self.facts = facts

    async def list_active_object_facts(self, _db, *, workline_id: int):
        return self.facts


class _LocationQuery:
    async def query_by_workline_active_object(self, _db, *, workline_id: int, object_type: str, object_key: str):
        return MaterialLocationResult(
            query_entry="by workline active object",
            location_scope="CONVEYOR_QUEUE",
            location_code="Q-IN",
            conflict_state=MaterialLocationConflictState.OK,
            evidence=[],
        )


class _RuntimeHolds:
    def __init__(self, holds=None) -> None:
        self.holds = holds or []

    async def get_active_blocking_by_workline(self, _db, workline_id: int):
        return self.holds


class _Memberships:
    def __init__(self, memberships) -> None:
        self.memberships = memberships

    async def list_active_by_workline(self, _db, *, workline_id: int, limit: int = 200):
        return self.memberships[:limit]


@pytest.mark.asyncio
async def test_workline_active_objects_returns_ok_for_single_source() -> None:
    """单来源 active object 返回 OK。"""

    service = WorklineActiveObjectsService(
        active_fact_provider=_Facts(
            [
                ActiveObjectFact(
                    object_code="BIN-OK",
                    object_type="BIN",
                    owner_kind="ON_CONVEYOR",
                    owner_code="Q-IN",
                    evidence_ref="queue:BIN-OK",
                )
            ]
        ),
        material_location_query_service=_LocationQuery(),
        runtime_hold_repository=_RuntimeHolds(),
    )

    response = await service.get_active_objects(None, workline_id=1)

    assert response.total_count == 1
    assert response.truncated is False
    assert response.objects[0].conflict_state == WorklineActiveObjectConflictState.OK
    assert response.objects[0].primary_source == "ON_CONVEYOR:Q-IN"


@pytest.mark.asyncio
async def test_workline_active_objects_marks_multi_owner_reconciling() -> None:
    """ON_CONVEYOR + AT_WORK_POSITION 同时出现时进入 RECONCILING。"""

    service = WorklineActiveObjectsService(
        active_fact_provider=_Facts(
            [
                ActiveObjectFact("BIN-CONFLICT", "ON_CONVEYOR", "Q-IN", "queue:BIN-CONFLICT", object_type="BIN"),
                ActiveObjectFact(
                    "BIN-CONFLICT",
                    "AT_WORK_POSITION",
                    "WP-01",
                    "position:BIN-CONFLICT",
                    object_type="BIN",
                ),
            ]
        ),
        material_location_query_service=_LocationQuery(),
        runtime_hold_repository=_RuntimeHolds(),
    )

    response = await service.get_active_objects(None, workline_id=1)

    assert response.objects[0].conflict_state == WorklineActiveObjectConflictState.RECONCILING
    assert response.objects[0].operator_hint == "RECONCILIATION_REQUIRED"
    assert response.objects[0].all_sources == ["ON_CONVEYOR:Q-IN", "AT_WORK_POSITION:WP-01"]


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
        active_fact_provider=_Facts(facts),
        material_location_query_service=_LocationQuery(),
        runtime_hold_repository=_RuntimeHolds(),
    )

    transient = await service.get_active_objects(None, workline_id=1, now=now)
    expired = await service.get_active_objects(None, workline_id=1, now=now + timedelta(seconds=31))

    assert transient.objects[0].conflict_state == WorklineActiveObjectConflictState.TRANSIENT
    assert expired.objects[0].conflict_state == WorklineActiveObjectConflictState.RECONCILING


@pytest.mark.asyncio
async def test_workline_active_objects_includes_runtime_hold_freeze_scope() -> None:
    """RuntimeHold open 时视图展示 freeze scope 和 allowed_next_effect_scope。"""

    service = WorklineActiveObjectsService(
        active_fact_provider=_Facts(
            [
                ActiveObjectFact(
                    object_code="BIN-HOLD",
                    object_type="BIN",
                    owner_kind="ON_CONVEYOR",
                    owner_code="Q-IN",
                    evidence_ref="queue:BIN-HOLD",
                )
            ]
        ),
        material_location_query_service=_LocationQuery(),
        runtime_hold_repository=_RuntimeHolds(
            [
                SimpleNamespace(
                    source_reason="BIN_CELL_RESERVATION_OWNER_MISMATCH",
                    evidence_snapshot_json={
                        "object_key": "BIN-HOLD",
                        "freeze_scope": "BIN_CELL",
                        "allowed_next_effect_scope": "RECONCILIATION_ONLY",
                    },
                )
            ]
        ),
    )

    response = await service.get_active_objects(None, workline_id=1)

    assert response.objects[0].runtime_hold is not None
    assert response.objects[0].runtime_hold.freeze_scope == "BIN_CELL"
    assert response.objects[0].runtime_hold.allowed_next_effect_scope == "RECONCILIATION_ONLY"


@pytest.mark.asyncio
async def test_active_object_fact_provider_uses_membership_entered_at_for_transient_deadline() -> None:
    """transient_until 必须基于事实进入时间，不能随每次读取向后滑动。"""

    entered_at = timezone.now_utc() - timedelta(minutes=5)
    entered_at_ms = int(entered_at.timestamp() * 1000)
    provider = RuntimeActiveObjectFactProvider(
        membership_repository=_Memberships(
            [
                SimpleNamespace(
                    id=99,
                    bin_code="BIN-MOVE",
                    placeholder_key=None,
                    queue_code="Q-IN",
                    entered_at=entered_at_ms,
                )
            ]
        ),
        transient_seconds=30,
    )

    facts = await provider.list_active_object_facts(None, workline_id=1)

    assert len(facts) == 1
    assert facts[0].transient_until == timezone.to_utc(entered_at_ms / 1000) + timedelta(seconds=30)
