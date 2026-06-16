"""SMT inbound handoff source item claim tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import func, select
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
from src.app.workline.services.single_layer_rack_orchestration_service import SingleLayerRackOrchestrationService
from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService
from src.utils.timezone import timezone
from src.workline_plugins.smt_sorting_inbound.constants import (
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
    SORTING_CONTEXT_SCHEMA_VERSION,
)


class _RouteService:
    def __init__(self, result: object, *, call_log: list[str] | None = None) -> None:
        self.result = result
        self.call_log = call_log
        self.calls: list[dict[str, Any]] = []

    async def resolve_route(self, _db: object, **kwargs: Any) -> object:
        if self.call_log is not None:
            self.call_log.append("route_probe")
        self.calls.append(kwargs)
        return self.result


class _ReleaseClaimingHandoffService:
    def __init__(self, *, claim_result: object | None = None) -> None:
        self.demand = SimpleNamespace(id=901, demand_key="handoff:release:auto-claim")
        self.claim_result = claim_result or SimpleNamespace(
            kind="CLAIMED",
            failure_code=None,
            failure_message=None,
            next_attempt_at=None,
            source_item=SimpleNamespace(id=301),
            session=SimpleNamespace(id=401),
            inbox=SimpleNamespace(id=501),
        )
        self.create_calls: list[dict[str, Any]] = []
        self.evaluate_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []

    async def create_or_get_from_release(self, _db: object, **kwargs: Any) -> object:
        self.create_calls.append(kwargs)
        return self.demand

    async def evaluate(self, _db: object, **kwargs: Any) -> object:
        self.evaluate_calls.append(kwargs)
        assert kwargs["demand"] is self.demand
        assert kwargs["prefer_full_box_exchange"] is False
        return self.demand

    async def claim_next_source_item(self, _db: object, **kwargs: Any) -> object:
        self.claim_calls.append(kwargs)
        return self.claim_result


def _selected_route(workline: WorkLine, *, target_rack_position_code: str = "TARGET_STATION_FROM_ROUTE") -> object:
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
            "manifest_contract_version": SMT_SORTING_INBOUND_CONTRACT_VERSION,
            "source_rack_position_code": "SOURCE_STATION_A",
            "source_station_code": "SOURCE_STATION_A",
            "source_position_code": "SOURCE_STATION_A",
            "target_rack_position_code": target_rack_position_code,
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
async def test_rough_sorter_release_path_evaluates_and_claims_first_source_item() -> None:
    handoff_service = _ReleaseClaimingHandoffService()
    orchestrator = SingleLayerRackOrchestrationService(smt_inbound_handoff_service=handoff_service)
    workline = SimpleNamespace(
        id=101,
        line_code="WL-SMT-ROUGH-RELEASE",
        runtime_status=WorkLineRuntimeStatus.READY,
    )

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="release-demand:auto-claim",
        demand_type="ROUGH_SORTER_RELEASE_FACT",
        workline=workline,
        station_code="SINGLE_LAYER_A",
        fact_payload={
            "rack_release_id": "release:auto-claim",
            "single_layer_rack_code": "RACK-AUTO-CLAIM",
            "trace_id": "trace-release-auto-claim",
            "bin_snapshots": [
                {
                    "slot_code": "A",
                    "bin_code": "SRC-BIN-A",
                    "usage": 0.25,
                    "cells": [
                        {
                            "bin_code": "SRC-BIN-A",
                            "bin_cell_index": 1,
                            "bin_cell_code": "A01",
                            "material_identity_key": "MAT-A",
                            "pkg_code": "PKG-A",
                            "reel_thickness_mm": "7.125",
                        }
                    ],
                }
            ],
        },
    )

    assert decision.reason == "ROUGH_SORTER_RELEASE_FACT_RECORDED"
    assert handoff_service.create_calls
    assert handoff_service.evaluate_calls == [
        {
            "demand": handoff_service.demand,
            "prefer_full_box_exchange": False,
            "trace_id": "trace-release-auto-claim",
        }
    ]
    assert handoff_service.claim_calls == [
        {
            "trace_id": "trace-release-auto-claim",
            "demand_id": 901,
        }
    ]
    assert decision.diagnostics["handoff_claim_result"] == "CLAIMED"
    assert decision.diagnostics["handoff_claim_source_item_id"] == 301
    assert decision.diagnostics["handoff_claim_session_id"] == 401
    assert decision.diagnostics["handoff_claim_inbox_id"] == 501


@pytest.mark.asyncio
async def test_rough_sorter_release_path_exposes_claim_failure_diagnostics() -> None:
    next_attempt_at = datetime(2026, 1, 1, 0, 3, 0)
    handoff_service = _ReleaseClaimingHandoffService(
        claim_result=SimpleNamespace(
            kind="RETRY",
            failure_code="TARGET_WORKLINE_NOT_READY",
            failure_message="目标分拣 WorkLine 暂未 READY",
            next_attempt_at=next_attempt_at,
            source_item=SimpleNamespace(id=302),
            session=None,
            inbox=None,
        )
    )
    orchestrator = SingleLayerRackOrchestrationService(smt_inbound_handoff_service=handoff_service)
    workline = SimpleNamespace(
        id=101,
        line_code="WL-SMT-ROUGH-RELEASE",
        runtime_status=WorkLineRuntimeStatus.READY,
    )

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="release-demand:target-busy",
        demand_type="ROUGH_SORTER_RELEASE_FACT",
        workline=workline,
        station_code="SINGLE_LAYER_A",
        fact_payload={
            "rack_release_id": "release:target-busy",
            "single_layer_rack_code": "RACK-TARGET-BUSY",
            "trace_id": "trace-release-target-busy",
            "bin_snapshots": [
                {
                    "slot_code": "A",
                    "bin_code": "SRC-BIN-A",
                    "usage": 0.25,
                    "cells": [
                        {
                            "bin_code": "SRC-BIN-A",
                            "bin_cell_index": 1,
                            "bin_cell_code": "A01",
                            "material_identity_key": "MAT-A",
                            "pkg_code": "PKG-A",
                            "reel_thickness_mm": "7.125",
                        }
                    ],
                }
            ],
        },
    )

    assert decision.diagnostics["handoff_claim_result"] == "RETRY"
    assert decision.diagnostics["handoff_claim_failure_code"] == "TARGET_WORKLINE_NOT_READY"
    assert decision.diagnostics["handoff_claim_failure_message"] == "目标分拣 WorkLine 暂未 READY"
    assert decision.diagnostics["handoff_claim_next_attempt_at"] == next_attempt_at.isoformat()
    assert decision.diagnostics["handoff_claim_source_item_id"] == 302
    assert decision.diagnostics["handoff_claim_session_id"] is None
    assert decision.diagnostics["handoff_claim_inbox_id"] is None


async def _count_workline_sessions(db_session: Any, workline_id: int) -> int:
    return int(
        (
            await db_session.execute(
                select(func.count()).select_from(WorklineSession).where(WorklineSession.workline_id == workline_id)
            )
        ).scalar_one()
        or 0
    )


async def _count_workline_inboxes(db_session: Any, workline_id: int) -> int:
    return int(
        (
            await db_session.execute(
                select(func.count()).select_from(WorklineInbox).where(WorklineInbox.workline_id == workline_id)
            )
        ).scalar_one()
        or 0
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
    sorting_context = session.context_json["sorting"]
    assert sorting_context["context_schema_version"] == SORTING_CONTEXT_SCHEMA_VERSION
    assert sorting_context["stations"]["scan_platform"] == "EMPTY"
    assert sorting_context["source_pick_request"]["handoff_source_item_id"] == item.id
    assert sorting_context["source_pick_request"]["manifest_contract_version"] == SMT_SORTING_INBOUND_CONTRACT_VERSION
    assert sorting_context["source_pick_request"]["source_rack_position_code"] == "SOURCE_STATION_A"
    assert sorting_context["source_pick_request"]["target_rack_position_code"] == "TARGET_STATION_FROM_ROUTE"
    assert sorting_context["source_pick_request"]["route_evidence"]["manifest_contract_version"] == (
        SMT_SORTING_INBOUND_CONTRACT_VERSION
    )
    assert sorting_context["source_pick_request"]["route_evidence"]["source_rack_position_code"] == "SOURCE_STATION_A"
    assert sorting_context["source_pick_request"]["route_evidence"]["target_rack_position_code"] == (
        "TARGET_STATION_FROM_ROUTE"
    )

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "in_flight_status",
    [
        SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
        SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING,
        SmtInboundHandoffSourceItemStatus.PICKED,
        SmtInboundHandoffSourceItemStatus.SORTING,
    ],
)
async def test_existing_in_flight_source_item_keeps_second_ready_for_retry(
    db_session: Any,
    in_flight_status: SmtInboundHandoffSourceItemStatus,
) -> None:
    workline = _target_workline("WL-SMT-SORT-IN-FLIGHT")
    db_session.add(workline)
    await db_session.flush()
    service = SmtInboundHandoffService(route_service=_RouteService(_selected_route(workline)))
    _first_demand, first_item = await _ready_demand(
        db_session,
        service,
        rack_release_id=f"claim-in-flight-{in_flight_status.value}-first",
    )
    second_demand, second_item = await _ready_demand(
        db_session,
        service,
        rack_release_id=f"claim-in-flight-{in_flight_status.value}-second",
    )
    first_item.status = in_flight_status
    first_item.target_workline_id = workline.id
    first_item.target_workline_code = workline.line_code
    db_session.add(first_item)
    await db_session.flush()

    result = await service.claim_next_source_item(db_session, trace_id=f"trace-claim-{in_flight_status.value}")
    await db_session.refresh(second_demand)
    await db_session.refresh(second_item)

    assert result.kind == "RETRY"
    assert result.failure_code == SmtInboundHandoffReasonCode.SOURCE_ITEM_CLAIM_CONFLICT.value
    assert second_demand.status == SmtInboundHandoffDemandStatus.READY_FOR_SORTING
    assert second_item.status == SmtInboundHandoffSourceItemStatus.READY
    assert second_item.failure_code == SmtInboundHandoffReasonCode.SOURCE_ITEM_CLAIM_CONFLICT.value
    assert second_item.next_attempt_at is not None
    assert second_item.sorting_session_id is None
    assert second_item.source_pick_inbox_id is None
    assert await _count_workline_sessions(db_session, cast("int", workline.id)) == 0
    assert await _count_workline_inboxes(db_session, cast("int", workline.id)) == 0


class _Phase2NotReadyRepository(SmtInboundHandoffRepository):
    def __init__(self, item: SmtInboundHandoffSourceItem, workline: WorkLine, call_log: list[str]) -> None:
        super().__init__()
        self.item = item
        self.workline = workline
        self.call_log = call_log

    async def claim_next_ready_source_item(self, *_args: Any, **_kwargs: Any) -> SmtInboundHandoffSourceItem | None:
        pytest.fail("phase 1 must not claim or lock the READY source item before route/ECS probe")

    async def list_ready_source_items_for_claim(self, *_args: Any, **_kwargs: Any) -> list[SmtInboundHandoffSourceItem]:
        self.call_log.append("list_ready_candidates")
        return [self.item]

    async def list_sorting_candidate_worklines(self, *_args: Any, **_kwargs: Any) -> list[WorkLine]:
        self.call_log.append("list_candidate_worklines")
        return [self.workline]

    async def lock_source_item_by_id(self, *_args: Any, **_kwargs: Any) -> SmtInboundHandoffSourceItem | None:
        self.call_log.append("lock_source_item")
        self.item.status = SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
        return self.item

    async def lock_target_workline_by_id(self, *_args: Any, **_kwargs: Any) -> WorkLine | None:
        self.call_log.append("lock_target_workline")
        return self.workline


@pytest.mark.asyncio
async def test_claim_route_probe_happens_before_phase2_source_and_target_locks(db_session: Any) -> None:
    workline = _target_workline("WL-SMT-SORT-PHASE-ORDER")
    db_session.add(workline)
    await db_session.flush()
    base_service = SmtInboundHandoffService()
    _demand, item = await _ready_demand(db_session, base_service, rack_release_id="claim-phase-lock-order")
    call_log: list[str] = []
    repository = _Phase2NotReadyRepository(item, workline, call_log)
    service = SmtInboundHandoffService(
        repository=repository,
        route_service=_RouteService(_selected_route(workline), call_log=call_log),
    )

    result = await service.claim_next_source_item(db_session, trace_id="trace-claim-phase-lock-order")

    assert result.kind == "RETRY"
    assert result.failure_code == SmtInboundHandoffReasonCode.SOURCE_ITEM_CLAIM_CONFLICT.value
    assert call_log == [
        "list_ready_candidates",
        "list_candidate_worklines",
        "route_probe",
        "lock_source_item",
        "lock_target_workline",
    ]


@pytest.mark.asyncio
async def test_phase2_recheck_not_ready_skips_session_and_inbox(db_session: Any) -> None:
    workline = _target_workline("WL-SMT-SORT-PHASE-RECHECK")
    db_session.add(workline)
    await db_session.flush()
    base_service = SmtInboundHandoffService()
    _demand, item = await _ready_demand(db_session, base_service, rack_release_id="claim-phase-recheck")
    repository = _Phase2NotReadyRepository(item, workline, [])
    service = SmtInboundHandoffService(repository=repository, route_service=_RouteService(_selected_route(workline)))

    result = await service.claim_next_source_item(db_session, trace_id="trace-claim-phase-recheck")

    assert result.kind == "RETRY"
    assert item.sorting_session_id is None
    assert item.source_pick_inbox_id is None
    assert await _count_workline_sessions(db_session, cast("int", workline.id)) == 0
    assert await _count_workline_inboxes(db_session, cast("int", workline.id)) == 0


@pytest.mark.asyncio
async def test_phase2_recheck_expired_probe_evidence_retries_without_session_or_inbox(
    db_session: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workline = _target_workline("WL-SMT-SORT-PHASE-PROBE-EXPIRED")
    db_session.add(workline)
    await db_session.flush()
    base_service = SmtInboundHandoffService()
    demand, item = await _ready_demand(db_session, base_service, rack_release_id="claim-phase-probe-expired")
    probe_started_at = timezone.now_for_db()
    recheck_at = probe_started_at + timedelta(seconds=6)
    clock = {"now": probe_started_at}

    class _AgingRouteService:
        async def resolve_route(self, _db: object, **_kwargs: Any) -> object:
            clock["now"] = recheck_at
            return _selected_route(workline)

    service = SmtInboundHandoffService(route_service=_AgingRouteService())
    monkeypatch.setattr(timezone, "now_for_db", lambda: clock["now"])

    result = await service.claim_next_source_item(db_session, trace_id="trace-claim-phase-probe-expired")
    await db_session.refresh(demand)
    await db_session.refresh(item)

    assert result.kind == "RETRY"
    assert result.failure_code == SmtInboundHandoffReasonCode.ECS_DEVICE_NOT_IDLE.value
    assert demand.status == SmtInboundHandoffDemandStatus.READY_FOR_SORTING
    assert demand.next_attempt_at == recheck_at + timedelta(seconds=30)
    assert item.status == SmtInboundHandoffSourceItemStatus.READY
    assert item.failure_code == SmtInboundHandoffReasonCode.ECS_DEVICE_NOT_IDLE.value
    assert item.next_attempt_at == recheck_at + timedelta(seconds=30)
    assert item.sorting_session_id is None
    assert item.source_pick_inbox_id is None
    assert await _count_workline_sessions(db_session, cast("int", workline.id)) == 0
    assert await _count_workline_inboxes(db_session, cast("int", workline.id)) == 0


def test_repository_ready_candidate_statement_reads_due_items_without_row_lock() -> None:
    repository = SmtInboundHandoffRepository()

    statement = repository.build_ready_source_item_candidate_statement(now=timezone.now_for_db(), limit=1)
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "FOR UPDATE" not in sql
    assert "ORDER BY" in sql
    assert "next_attempt_at" in sql
    assert "handoff_demand_id" in sql
    assert "id" in sql
    order_by = sql.split("ORDER BY", maxsplit=1)[1]
    assert order_by.index("next_attempt_at") < order_by.index("handoff_demand_id") < order_by.rindex("id")


def test_repository_phase2_lock_statements_use_row_locks() -> None:
    repository = SmtInboundHandoffRepository()

    source_statement = repository.build_source_item_by_id_lock_statement(source_item_id=1)
    target_statement = repository.build_target_workline_by_id_lock_statement(workline_id=2)
    source_sql = str(source_statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    target_sql = str(target_statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "FOR UPDATE" in source_sql
    assert "smt_inbound_handoff_source_items" in source_sql
    assert "FOR UPDATE" in target_sql
    assert "work_lines" in target_sql


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
