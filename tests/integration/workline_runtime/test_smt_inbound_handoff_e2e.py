"""PostgreSQL gated SMT inbound handoff backend smoke."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import delete, func, select

from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device, DeviceStatus
from src.app.sys.models.outbox import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.services.single_layer_rack_orchestration_service import (
    SingleLayerRackOrchestrationDecisionCode,
    SingleLayerRackOrchestrationService,
)
from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService
from src.app.workline.services.station_lease_service import StationLeaseResult
from src.utils.timezone import timezone
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_SOURCE_PICK,
    ROLE_SORTING_SOURCE_ARM,
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
    SORTING_CONTEXT_SCHEMA_VERSION,
)
from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import RuntimeIntentKind
from src.workline_runtime.runtime_intent_effects import RuntimeIntentEffectApplier
from src.workline_runtime.trace_context import TraceContext

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.workline.models.inbox import WorklineInbox


class _AvailableStationLeaseService:
    def __init__(self) -> None:
        self.status_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []

    async def get_station_lease_status(self, *_args: Any, **kwargs: Any) -> StationLeaseResult:
        self.status_calls.append(kwargs)
        return StationLeaseResult(workline_code="WL-SMT-ROUGH", position_code="SINGLE_LAYER_A", available=True)

    async def claim_station_dispatch_lease(self, *_args: Any, **kwargs: Any) -> None:
        self.claim_calls.append(kwargs)


class _SelectedRouteService:
    def __init__(self, workline: WorkLine) -> None:
        self.workline = workline
        self.calls: list[dict[str, Any]] = []

    async def resolve_route(self, _db: object, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            kind="SELECTED",
            manual_hold=False,
            retryable=False,
            selected_workline=self.workline,
            selected_workline_id=self.workline.id,
            selected_workline_code=self.workline.line_code,
            route_evidence={
                "source_station_code": "SOURCE_STATION_A",
                "source_position_code": "SOURCE_STATION_A",
                "route_priority": 1,
            },
            failure_code=None,
            failure_message=None,
            next_attempt_at=None,
        )


def _release_fact_payload(test_prefix: str) -> dict[str, Any]:
    return {
        "rack_release_id": f"{test_prefix}:release",
        "single_layer_rack_code": f"{test_prefix}:rack",
        "release_reason_code": "NO_COMPATIBLE_OR_EMPTY_CELL",
        "bin_snapshots": [
            {
                "slot_code": "A",
                "bin_code": f"{test_prefix}:SRC-BIN-A",
                "usage": 0.25,
                "cells": [
                    {
                        "bin_code": f"{test_prefix}:SRC-BIN-A",
                        "bin_cell_index": 1,
                        "bin_cell_code": "A01",
                        "material_identity_key": f"{test_prefix}:MAT-A",
                        "pkg_code": f"{test_prefix}:PKG-A",
                        "reel_thickness_mm": "7.125",
                    }
                ],
            }
        ],
        "trace_id": f"{test_prefix}:trace",
    }


def _rough_workline(test_prefix: str) -> WorkLine:
    return WorkLine(
        line_code=f"{test_prefix}:ROUGH",
        line_name=f"{test_prefix} 粗分机",
        line_type=LineType.AUTO,
        run_mode=WorkLineRunMode.AUTO,
        runtime_status=WorkLineRuntimeStatus.READY,
        is_active=True,
    )


def _sorting_workline(test_prefix: str) -> WorkLine:
    return WorkLine(
        line_code=f"{test_prefix}:SORT",
        line_name=f"{test_prefix} SMT 入库分拣",
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


def _source_arm(test_prefix: str, workline: WorkLine) -> Device:
    return Device(
        device_code=f"{test_prefix}:SOURCE-ARM",
        device_name=f"{test_prefix} source arm",
        work_line_id=workline.id,
        device_role=ROLE_SORTING_SOURCE_ARM,
        device_status=DeviceStatus.IDLE,
        is_active=True,
    )


async def _cleanup_handoff_rows(db: AsyncSession, *, test_prefix: str) -> None:
    demand_ids = select(SmtInboundHandoffDemand.id).where(
        SmtInboundHandoffDemand.rack_release_id.like(f"{test_prefix}%")
    )
    await db.execute(
        delete(SmtInboundHandoffSourceItem).where(SmtInboundHandoffSourceItem.handoff_demand_id.in_(demand_ids))
    )
    await db.execute(
        delete(SmtInboundHandoffDemand).where(SmtInboundHandoffDemand.rack_release_id.like(f"{test_prefix}%"))
    )
    await db.execute(delete(WorklineSession).where(WorklineSession.session_code.like(f"{test_prefix}%")))
    await db.commit()


def _handoff_source_pick_request(*, demand_id: int, source_item_id: int) -> dict[str, Any]:
    return {
        "handoff_demand_id": demand_id,
        "handoff_source_item_id": source_item_id,
        "claim_attempt_no": 1,
        "event_id": f"source-pick-requested:{demand_id}:{source_item_id}:1",
        "target_workline_code": "SMT_SORTER_01",
        "manifest_contract_version": SMT_SORTING_INBOUND_CONTRACT_VERSION,
        "source_rack_position_code": "SINGLE_LAYER_A",
        "target_rack_position_code": "TARGET_STATION",
        "route_evidence": {},
    }


async def _seed_terminal_handoff(
    db: AsyncSession,
    *,
    test_prefix: str,
    item_status: SmtInboundHandoffSourceItemStatus,
    include_source_pick_request: bool = True,
    suffix: str = "terminal",
) -> tuple[SmtInboundHandoffDemand, SmtInboundHandoffSourceItem, WorklineSession]:
    workline = _sorting_workline(f"{test_prefix}:{suffix}")
    db.add(workline)
    await db.flush()

    demand = SmtInboundHandoffDemand(
        demand_key=f"{test_prefix}:{suffix}:demand",
        rack_release_id=f"{test_prefix}:{suffix}:release",
        single_layer_rack_code=f"{test_prefix}:{suffix}:rack",
        status=SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS,
        trace_id=f"{test_prefix}:{suffix}:trace",
    )
    db.add(demand)
    await db.flush()

    item = SmtInboundHandoffSourceItem(
        handoff_demand_id=demand.id,
        item_key=f"{test_prefix}:{suffix}:item",
        bin_code=f"{test_prefix}:SRC-BIN-A",
        bin_cell_index=1,
        bin_cell_code="A01",
        material_identity_key=f"{test_prefix}:MAT-A",
        pkg_code=f"{test_prefix}:PKG-A",
        status=item_status,
        target_workline_id=workline.id,
        target_workline_code=workline.line_code,
        sorting_session_id=None,
        claim_attempt_no=1,
        failure_code="PLUGIN_CONTRACT_INVALID",
        failure_message="旧失败",
        next_attempt_at=timezone.now_for_db(),
    )
    db.add(item)
    await db.flush()

    sorting_context: dict[str, Any] = {
        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
        "current_material": {
            "source_bin_code": item.bin_code,
            "source_cell_code": item.bin_cell_code,
            "material_identity_key": item.material_identity_key,
            "reel_thickness_mm": "7.125",
            "evidence": {"pkg_code": item.pkg_code},
        },
        "stations": {"scan_platform": "OCCUPIED"},
    }
    if include_source_pick_request:
        sorting_context["source_pick_request"] = _handoff_source_pick_request(
            demand_id=demand.id,
            source_item_id=item.id,
        )
    session = WorklineSession(
        session_code=f"{test_prefix}:{suffix}:session",
        workline_id=workline.id,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        run_mode=RunMode.AUTO,
        business_key=demand.demand_key,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        context_json={"sorting": sorting_context},
        context_schema_version="smt-sorting-inbound.v1",
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        started_at=timezone.now_for_db(),
        current_wait_type="COMMAND_RESULT",
        waiting_since=timezone.now_for_db(),
        current_wait_timeout_seconds=60,
        trace_id=demand.trace_id,
    )
    db.add(session)
    await db.flush()

    item.sorting_session_id = session.id
    db.add(item)
    await db.flush()
    return demand, item, session


def _effect_ctx(*, db: AsyncSession, workline: WorkLine, session: Any, inbox: WorklineInbox, source_arm: Device) -> Any:
    trace_id = getattr(inbox, "trace_id", None) or "trace-smt-handoff-e2e"
    return {
        "db": db,
        "session": session,
        "workline": workline,
        "inbox": inbox,
        "devices_by_role": {ROLE_SORTING_SOURCE_ARM: [source_arm]},
        "source_device": source_arm,
        "orch_result": OrchestratorResult(success=True, intents=[]),
        "current_status": getattr(getattr(session, "status", None), "value", getattr(session, "status", None)),
        "trace_id": trace_id,
        "trace": TraceContext.from_runtime(session=session, trace_id=trace_id),
        "session_ctx": dict(getattr(session, "context_json", None) or {}),
        "now": timezone.now_for_db(),
        "awaiting_command_id": None,
        "awaiting_command_code": None,
        "next_timeline_seq_no": None,
    }


@pytest.mark.asyncio
async def test_smt_inbound_handoff_release_claim_plugin_effect_smoke(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    async with integration_session_factory() as db:
        try:
            rough_workline = _rough_workline(test_prefix)
            sorting_workline = _sorting_workline(test_prefix)
            db.add_all([rough_workline, sorting_workline])
            await db.flush()
            source_arm = _source_arm(test_prefix, sorting_workline)
            db.add(source_arm)
            await db.flush()

            handoff_service = SmtInboundHandoffService(route_service=_SelectedRouteService(sorting_workline))
            orchestrator = SingleLayerRackOrchestrationService(
                station_lease_service=_AvailableStationLeaseService(),
                smt_inbound_handoff_service=handoff_service,
            )
            payload = _release_fact_payload(test_prefix)

            first_decision = await orchestrator.plan_single_layer_rack_dispatch(
                db,
                business_demand_key=f"{test_prefix}:release-demand",
                demand_type="ROUGH_SORTER_RELEASE_FACT",
                workline=rough_workline,
                station_code="SINGLE_LAYER_A",
                fact_payload=payload,
            )
            second_decision = await orchestrator.plan_single_layer_rack_dispatch(
                db,
                business_demand_key=f"{test_prefix}:release-demand",
                demand_type="ROUGH_SORTER_RELEASE_FACT",
                workline=rough_workline,
                station_code="SINGLE_LAYER_A",
                fact_payload=payload,
            )
            await db.flush()

            assert first_decision.decision == SingleLayerRackOrchestrationDecisionCode.WAITING
            assert second_decision.diagnostics["handoff_demand_id"] == first_decision.diagnostics["handoff_demand_id"]
            demand_count = await db.scalar(
                select(func.count())
                .select_from(SmtInboundHandoffDemand)
                .where(SmtInboundHandoffDemand.rack_release_id == payload["rack_release_id"])
            )
            assert demand_count == 1

            demand = (
                await db.execute(
                    select(SmtInboundHandoffDemand).where(
                        SmtInboundHandoffDemand.rack_release_id == payload["rack_release_id"]
                    )
                )
            ).scalar_one()
            source_item_count = await db.scalar(
                select(func.count())
                .select_from(SmtInboundHandoffSourceItem)
                .where(SmtInboundHandoffSourceItem.handoff_demand_id == demand.id)
            )
            assert source_item_count == 1

            demand = await handoff_service.evaluate(db, demand=demand, prefer_full_box_exchange=False)
            assert demand.status == SmtInboundHandoffDemandStatus.READY_FOR_SORTING

            claim_result = await handoff_service.claim_next_source_item(db, trace_id=payload["trace_id"])
            assert claim_result.kind == "CLAIMED"
            item = claim_result.source_item
            session = claim_result.session
            inbox = claim_result.inbox
            assert item.status == SmtInboundHandoffSourceItemStatus.PICK_REQUESTED
            assert inbox.claim_bucket_key == f"session:{session.id}"
            assert inbox.claim_bucket_key != "serial:unknown"

            intents = await SmtSortingInboundPlugin().on_device_event(
                SimpleNamespace(
                    trace_id=payload["trace_id"],
                    config={},
                    logger=SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None),
                    normalized_input=None,
                    session=session,
                    services=SimpleNamespace(),
                ),
                inbox,
            )
            assert [intent.kind for intent in intents] == [RuntimeIntentKind.COMMAND]
            assert intents[0].action == COMMAND_SOURCE_PICK

            await RuntimeIntentEffectApplier().apply(
                _effect_ctx(db=db, workline=sorting_workline, session=session, inbox=inbox, source_arm=source_arm),
                intents,
            )
            await db.flush()
            await db.refresh(item)

            command = await db.get(DeviceCommand, item.source_pick_command_id)
            assert command is not None
            assert command.command_code == item.source_pick_command_code
            outbox = (
                await db.execute(select(SystemOutbox).where(SystemOutbox.dispatch_key == item.source_pick_dispatch_key))
            ).scalar_one()
            assert outbox.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND
            assert outbox.target_type == SystemOutboxTargetType.DEVICE
            assert outbox.target_code == source_arm.device_code
            assert outbox.blocked_workline_id is None

            recovery_summary = await handoff_service.scan_smt_inbound_handoff_demands_batch(db, limit=10)
            assert recovery_summary["manual_hold"] == 0
            assert recovery_summary["recovery_errors"] == 0
        finally:
            await _cleanup_handoff_rows(db, test_prefix=test_prefix)


@pytest.mark.asyncio
async def test_handoff_target_terminal_ledger_marks_source_item_sorted_and_completes_session(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    async with integration_session_factory() as db:
        try:
            handoff_service = SmtInboundHandoffService()
            demand, item, session = await _seed_terminal_handoff(
                db,
                test_prefix=test_prefix,
                item_status=SmtInboundHandoffSourceItemStatus.PICKED,
                suffix="target-terminal",
            )

            result = await handoff_service.record_source_item_terminal_result(
                db,
                session=session,
                terminal_status="SORTED",
                command_id=9001,
                trace_id=f"{test_prefix}:target-terminal:trace",
                terminal_evidence={"target_command_payload": {"command_code": "TARGET-CMD-001"}},
            )
            await db.flush()
            await db.refresh(item)
            await db.refresh(demand)
            await db.refresh(session)

            assert result.outcome == "advanced"
            assert result.advanced is True
            assert item.status == SmtInboundHandoffSourceItemStatus.SORTED
            assert item.completed_at is not None
            assert item.failure_code is None
            assert item.failure_message is None
            assert item.next_attempt_at is None
            assert demand.status == SmtInboundHandoffDemandStatus.COMPLETED
            assert session.status == SessionStatus.COMPLETED
            terminal_result = session.context_json["sorting"]["handoff_terminal_result"]
            assert terminal_result["terminal_status"] == "SORTED"
            assert terminal_result["handoff_source_item_id"] == item.id

            replay = await handoff_service.record_source_item_terminal_result(
                db,
                session=session,
                terminal_status="SORTED",
                command_id=9001,
                trace_id=f"{test_prefix}:target-terminal:trace",
                terminal_evidence={"target_command_payload": {"command_code": "TARGET-CMD-001"}},
            )

            assert replay.outcome == "already_terminal"
            assert replay.advanced is False
            assert replay.already_terminal is True
        finally:
            await _cleanup_handoff_rows(db, test_prefix=test_prefix)


@pytest.mark.asyncio
async def test_handoff_ng_terminal_ledger_marks_source_item_skipped_and_recalculates_demand(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    async with integration_session_factory() as db:
        try:
            handoff_service = SmtInboundHandoffService()
            demand, item, session = await _seed_terminal_handoff(
                db,
                test_prefix=test_prefix,
                item_status=SmtInboundHandoffSourceItemStatus.SORTING,
                suffix="ng-terminal",
            )

            result = await handoff_service.record_source_item_terminal_result(
                db,
                session=session,
                terminal_status="SKIPPED",
                command_id=9002,
                trace_id=f"{test_prefix}:ng-terminal:trace",
                terminal_evidence={
                    "ng_command_payload": {"command_code": "NG-CMD-001", "ng_location": "NG-01"},
                },
            )
            await db.flush()
            await db.refresh(item)
            await db.refresh(demand)
            await db.refresh(session)

            assert result.advanced is True
            assert item.status == SmtInboundHandoffSourceItemStatus.SKIPPED
            assert item.completed_at is not None
            assert demand.status == SmtInboundHandoffDemandStatus.COMPLETED
            assert session.status == SessionStatus.COMPLETED
            assert session.context_json["sorting"]["handoff_terminal_result"]["terminal_status"] == "SKIPPED"
        finally:
            await _cleanup_handoff_rows(db, test_prefix=test_prefix)


@pytest.mark.asyncio
async def test_handoff_terminal_conflict_moves_item_and_demand_to_manual_hold(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    async with integration_session_factory() as db:
        try:
            handoff_service = SmtInboundHandoffService()
            demand, item, session = await _seed_terminal_handoff(
                db,
                test_prefix=test_prefix,
                item_status=SmtInboundHandoffSourceItemStatus.SORTED,
                suffix="terminal-conflict",
            )
            item.completed_at = timezone.now_for_db()
            db.add(item)
            await db.flush()

            result = await handoff_service.record_source_item_terminal_result(
                db,
                session=session,
                terminal_status="SKIPPED",
                trace_id=f"{test_prefix}:terminal-conflict:trace",
                terminal_evidence={"ng_command_payload": {"command_code": "NG-CMD-CONFLICT"}},
            )
            await db.flush()
            await db.refresh(item)
            await db.refresh(demand)

            assert result.outcome == "manual_hold"
            assert result.advanced is False
            assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
            assert item.failure_code == "PLUGIN_CONTRACT_INVALID"
            assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
            assert demand.failure_code == "PLUGIN_CONTRACT_INVALID"
        finally:
            await _cleanup_handoff_rows(db, test_prefix=test_prefix)


@pytest.mark.asyncio
async def test_handoff_terminal_requires_source_pick_request_before_ledger_write(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    async with integration_session_factory() as db:
        try:
            handoff_service = SmtInboundHandoffService()
            _demand, item, session = await _seed_terminal_handoff(
                db,
                test_prefix=test_prefix,
                item_status=SmtInboundHandoffSourceItemStatus.PICKED,
                include_source_pick_request=False,
                suffix="terminal-missing-request",
            )

            with pytest.raises(ValueError, match="source_pick_request"):
                await handoff_service.record_source_item_terminal_result(
                    db,
                    session=session,
                    terminal_status="SORTED",
                    trace_id=f"{test_prefix}:terminal-missing-request:trace",
                    terminal_evidence={"target_command_payload": {"command_code": "TARGET-CMD-001"}},
                )

            await db.refresh(item)
            assert item.status == SmtInboundHandoffSourceItemStatus.PICKED
            assert item.completed_at is None
        finally:
            await _cleanup_handoff_rows(db, test_prefix=test_prefix)


@pytest.mark.asyncio
async def test_handoff_terminal_requires_source_item_bound_to_current_session(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    async with integration_session_factory() as db:
        try:
            handoff_service = SmtInboundHandoffService()
            _demand, item, session = await _seed_terminal_handoff(
                db,
                test_prefix=test_prefix,
                item_status=SmtInboundHandoffSourceItemStatus.PICKED,
                suffix="terminal-session-mismatch",
            )
            other_session = WorklineSession(
                session_code=f"{test_prefix}:terminal-session-mismatch:other-session",
                workline_id=session.workline_id,
                plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
                run_mode=RunMode.AUTO,
                business_key=f"{test_prefix}:terminal-session-mismatch:other",
                status=SessionStatus.WAITING_DEVICE_RESULT,
                context_json={},
                context_schema_version="smt-sorting-inbound.v1",
                contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
                started_at=timezone.now_for_db(),
                current_wait_type="COMMAND_RESULT",
                waiting_since=timezone.now_for_db(),
                current_wait_timeout_seconds=60,
                trace_id=f"{test_prefix}:terminal-session-mismatch:other-trace",
            )
            db.add(other_session)
            await db.flush()
            item.sorting_session_id = other_session.id
            db.add(item)
            await db.flush()

            with pytest.raises(ValueError, match="sorting_session"):
                await handoff_service.record_source_item_terminal_result(
                    db,
                    session=session,
                    terminal_status="SORTED",
                    trace_id=f"{test_prefix}:terminal-session-mismatch:trace",
                    terminal_evidence={"target_command_payload": {"command_code": "TARGET-CMD-001"}},
                )

            await db.refresh(item)
            await db.refresh(session)
            assert item.status == SmtInboundHandoffSourceItemStatus.PICKED
            assert item.completed_at is None
            assert session.status == SessionStatus.WAITING_DEVICE_RESULT
        finally:
            await _cleanup_handoff_rows(db, test_prefix=test_prefix)
