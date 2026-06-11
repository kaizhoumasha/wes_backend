"""SMT inbound handoff source item claim tests."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from src.app.workline.domain.services.smt_inbound_handoff_reason import SmtInboundHandoffReasonCode
from src.app.workline.models import LineType, WorkLine, WorklineInbox, WorkLineRunMode, WorklineSession
from src.app.workline.models.inbox import InboxKind, SourceSystem
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import SessionStatus
from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.repositories.smt_inbound_handoff_repository import SmtInboundHandoffRepository
from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService
from src.utils.timezone import timezone
from src.workline_plugins.smt_sorting_inbound.constants import (
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)


class _RouteService:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def resolve_route(self, _db: object, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return self.result


def _selected_route(workline: WorkLine) -> object:
    return SimpleNamespace(
        kind="SELECTED",
        manual_hold=False,
        retryable=False,
        selected_workline=workline,
        selected_workline_id=workline.id,
        selected_workline_code=workline.line_code,
        source_station_code="SOURCE_STATION_A",
        source_position_code="SOURCE_STATION_A",
        route_evidence={
            "source_station_code": "SOURCE_STATION_A",
            "source_position_code": "SOURCE_STATION_A",
            "route_priority": 1,
        },
        failure_code=None,
        failure_message=None,
        next_attempt_at=None,
    )


def _retry_route(next_attempt_at: datetime) -> object:
    return SimpleNamespace(
        kind="RETRY",
        manual_hold=False,
        retryable=True,
        selected_workline=None,
        selected_workline_id=None,
        selected_workline_code=None,
        source_station_code=None,
        source_position_code=None,
        route_evidence={"reason": "target not ready"},
        failure_code=SmtInboundHandoffReasonCode.TARGET_WORKLINE_NOT_READY.value,
        failure_message="目标分拣 WorkLine 暂未 READY",
        next_attempt_at=next_attempt_at,
    )


def _manual_hold_route() -> object:
    return SimpleNamespace(
        kind="MANUAL_HOLD",
        manual_hold=True,
        retryable=False,
        selected_workline=None,
        selected_workline_id=None,
        selected_workline_code=None,
        source_station_code=None,
        source_position_code=None,
        route_evidence={"reason": "no route"},
        failure_code=SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value,
        failure_message="未找到可承接 source item 的 SMT 入库分拣 WorkLine 配置候选",
        next_attempt_at=None,
    )


def _release_bin(slot_code: str, usage: float = 0.25) -> dict[str, Any]:
    return {
        "slot_code": slot_code,
        "bin_code": f"BIN-{slot_code}",
        "usage": usage,
        "status": "IN_USE",
        "cells": [
            {
                "bin_code": f"BIN-{slot_code}",
                "bin_cell_index": 1,
                "bin_cell_code": f"BIN-{slot_code}-1",
                "status": "OCCUPIED",
                "material_identity_key": f"MAT-{slot_code}",
                "pkg_code": f"PKG-{slot_code}",
                "reel_thickness_mm": "1.2",
            }
        ],
    }


async def _ready_demand(
    db_session: Any,
    service: SmtInboundHandoffService,
    *,
    rack_release_id: str,
) -> tuple[SmtInboundHandoffDemand, SmtInboundHandoffSourceItem]:
    demand = await service.create_or_get_from_release(
        db_session,
        rack_release_id=rack_release_id,
        single_layer_rack_code=f"RACK-{rack_release_id}",
        source_workline_id=None,
        source_workline_code="WL-SMT-ROUGH-01",
        release_reason_code="NO_COMPATIBLE_OR_EMPTY_CELL",
        bin_snapshots=[_release_bin("A")],
        trace_id=f"trace-{rack_release_id}",
    )
    demand = await service.evaluate(db_session, demand=demand)
    item = (
        await db_session.execute(
            select(SmtInboundHandoffSourceItem).where(SmtInboundHandoffSourceItem.handoff_demand_id == demand.id)
        )
    ).scalar_one()
    return demand, item


def _target_workline(line_code: str = "WL-SMT-SORT-01") -> WorkLine:
    return WorkLine(
        line_code=line_code,
        line_name=f"{line_code} 分拣线",
        line_type=LineType.AUTO,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        run_mode=WorkLineRunMode.AUTO,
        runtime_status=WorkLineRuntimeStatus.READY,
        is_active=True,
        config={
            "smt_inbound_handoff_route": {
                "enabled": True,
                "priority": 1,
                "source_station_code": "SOURCE_STATION_A",
            }
        },
    )


@pytest.mark.asyncio
async def test_route_missing_manual_holds_claimed_source_item_and_demand(db_session: Any) -> None:
    service = SmtInboundHandoffService(route_service=_RouteService(_manual_hold_route()))
    demand, item = await _ready_demand(db_session, service, rack_release_id="claim-route-missing")

    result = await service.claim_next_source_item(db_session, trace_id="trace-claim-route-missing")
    await db_session.refresh(demand)
    await db_session.refresh(item)

    assert result.kind == "MANUAL_HOLD"
    assert result.failure_code == SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value
    assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
    assert demand.failure_code == SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value
    assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    assert item.failure_code == SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value


@pytest.mark.asyncio
async def test_config_candidate_runtime_busy_releases_ready_item_for_retry(db_session: Any) -> None:
    next_attempt_at = timezone.now_for_db() + timedelta(seconds=30)
    service = SmtInboundHandoffService(route_service=_RouteService(_retry_route(next_attempt_at)))
    demand, item = await _ready_demand(db_session, service, rack_release_id="claim-route-busy")

    result = await service.claim_next_source_item(db_session, trace_id="trace-claim-route-busy")
    await db_session.refresh(demand)
    await db_session.refresh(item)

    assert result.kind == "RETRY"
    assert result.failure_code == SmtInboundHandoffReasonCode.TARGET_WORKLINE_NOT_READY.value
    assert demand.status == SmtInboundHandoffDemandStatus.READY_FOR_SORTING
    assert demand.next_attempt_at == next_attempt_at
    assert item.status == SmtInboundHandoffSourceItemStatus.READY
    assert item.next_attempt_at == next_attempt_at
    assert item.failure_code == SmtInboundHandoffReasonCode.TARGET_WORKLINE_NOT_READY.value


@pytest.mark.asyncio
async def test_claim_creates_internal_source_pick_inbox_with_session_workline_bucket(db_session: Any) -> None:
    workline = _target_workline()
    db_session.add(workline)
    await db_session.flush()
    service = SmtInboundHandoffService(route_service=_RouteService(_selected_route(workline)))
    demand, item = await _ready_demand(db_session, service, rack_release_id="claim-inbox")

    result = await service.claim_next_source_item(db_session, trace_id="trace-claim-inbox")
    await db_session.refresh(demand)
    await db_session.refresh(item)
    inbox = (
        await db_session.execute(select(WorklineInbox).where(WorklineInbox.id == item.source_pick_inbox_id))
    ).scalar_one()
    session = (
        await db_session.execute(select(WorklineSession).where(WorklineSession.id == item.sorting_session_id))
    ).scalar_one()

    assert result.kind == "CLAIMED"
    assert demand.status == SmtInboundHandoffDemandStatus.CLAIMED_BY_SORTING
    assert demand.target_workline_id == workline.id
    assert demand.target_workline_code == workline.line_code
    assert item.status == SmtInboundHandoffSourceItemStatus.PICK_REQUESTED
    assert item.target_workline_id == workline.id
    assert item.target_workline_code == workline.line_code
    assert item.claim_attempt_no == 1
    assert item.claimed_at is not None

    assert session.status == SessionStatus.RUNNING
    assert session.workline_id == workline.id
    assert session.plugin_key == SMT_SORTING_INBOUND_PLUGIN_KEY
    assert "current_material" not in session.context_json["sorting"]
    assert session.context_json["sorting"]["source_pick_request"]["handoff_source_item_id"] == item.id

    assert inbox.kind == InboxKind.INTERNAL_EVENT
    assert inbox.source_system == SourceSystem.SYSTEM
    assert inbox.session_id == session.id
    assert inbox.workline_id == workline.id
    assert inbox.claim_bucket_key == f"session:{session.id}"
    assert inbox.claim_bucket_key != "serial:unknown"
    assert inbox.event_id == f"smt-inbound-handoff-source-item:{item.id}:claim:1"
    assert inbox.causation_id == f"handoff-source-item:{item.id}"
    assert inbox.idempotency_key == f"internal_event:SORTING_SOURCE_PICK_REQUESTED:{inbox.event_id}"
    assert inbox.payload_json["event_type"] == "SORTING_SOURCE_PICK_REQUESTED"
    data = inbox.payload_json["data"]
    assert data["handoff_demand_id"] == demand.id
    assert data["handoff_source_item_id"] == item.id
    assert data["claim_attempt_no"] == 1
    assert data["bin_code"] == "BIN-A"
    assert data["bin_cell_code"] == "BIN-A-1"
    assert data["material_identity_key"] == "MAT-A"
    assert data["pkg_code"] == "PKG-A"
    assert data["route_evidence"]["source_station_code"] == "SOURCE_STATION_A"


def test_repository_ready_claim_statement_uses_row_lock_skip_locked_and_stable_ordering() -> None:
    repository = SmtInboundHandoffRepository()

    statement = repository.build_ready_source_item_claim_statement(now=timezone.now_for_db(), limit=1)
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY" in sql
    assert "next_attempt_at" in sql
    assert "handoff_demand_id" in sql
    assert "id" in sql
    order_by = sql.split("ORDER BY", maxsplit=1)[1]
    assert order_by.index("next_attempt_at") < order_by.index("handoff_demand_id") < order_by.rindex("id")


def test_repository_claim_method_delegates_to_row_lock_statement() -> None:
    source = inspect.getsource(SmtInboundHandoffRepository.claim_next_ready_source_item)

    assert "build_ready_source_item_claim_statement" in source
    assert "with_for_update" not in source


@pytest.mark.asyncio
async def test_dead_letter_release_increments_attempt_before_next_claim(db_session: Any) -> None:
    workline = _target_workline("WL-SMT-SORT-RETRY")
    db_session.add(workline)
    await db_session.flush()
    service = SmtInboundHandoffService(route_service=_RouteService(_selected_route(workline)))
    _demand, item = await _ready_demand(db_session, service, rack_release_id="claim-dead-letter-release")
    item.status = SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    item.failure_code = SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER.value
    item.source_pick_inbox_id = 1001
    db_session.add(item)
    await db_session.flush()

    released = await service.release_source_pick_dead_letter_for_retry(
        db_session,
        source_item_id=item.id,
        trace_id="trace-claim-dead-letter-release",
    )
    assert released.claim_attempt_no == 2
    assert released.status == SmtInboundHandoffSourceItemStatus.READY
    assert released.source_pick_inbox_id is None

    result = await service.claim_next_source_item(db_session, trace_id="trace-claim-dead-letter-release")
    await db_session.refresh(item)
    inbox = (
        await db_session.execute(select(WorklineInbox).where(WorklineInbox.id == item.source_pick_inbox_id))
    ).scalar_one()

    assert result.kind == "CLAIMED"
    assert item.claim_attempt_no == 2
    assert inbox.event_id == f"smt-inbound-handoff-source-item:{item.id}:claim:2"
