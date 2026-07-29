"""E08/E09 demand root、owner 与终态投影的真实 PostgreSQL 合同。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from importlib import import_module
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import select

from src.app.resource.models import RackKind, RackPlacement, RackPlacementStatus, ResourceSourceSystem
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.material_flow_owner import MaterialFlowOwner
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import effect_reducer
from src.app.runtime.orchestration.wms_rack_demand import WmsRackDemand
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.fulfillment_operations import (
    RequestRackSupplyResult,
    RequestRackTransportResult,
)
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

REVISION = "f9ffbef8992a"
E08 = "wms.fulfillment.request_rack_supply@v1"
E09 = "wms.fulfillment.request_rack_transport@v1"


def _domain_types() -> tuple[type[Any], type[Any]]:
    service_module = import_module("src.app.runtime.orchestration.services.rack_demand_service")
    projector_module = import_module("src.app.runtime.orchestration.services.wms_fulfillment_domain_projector")
    service_type = getattr(service_module, "RackDemandService", None)
    projector_type = getattr(projector_module, "WmsFulfillmentDomainProjector", None)
    assert service_type is not None, "RackDemandService is missing"
    assert projector_type is not None, "WmsFulfillmentDomainProjector is missing"
    return service_type, projector_type


async def _with_database(
    scenario: Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]],
) -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", REVISION, database_url=database_url)
        engine = create_async_engine(database_url, pool_pre_ping=True, pool_size=6, max_overflow=0)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            await scenario(session_factory)
        finally:
            await engine.dispose()


def _ctx(db: AsyncSession) -> dict[str, Any]:
    return {
        "db": db,
        "session": SimpleNamespace(id=101),
        "workline": SimpleNamespace(id=10, line_code="LINE-10"),
        "trace_id": "trace-rack-demand",
    }


async def _seed_runtime_intent(db: AsyncSession, intent_id: int, dispatch_key: str) -> None:
    await db.execute(text("SET session_replication_role = replica"))
    try:
        await db.execute(
            text(
                """
                INSERT INTO wes_runtime.runtime_intent_logs (
                    id,
                    execution_session_id,
                    correlation_id,
                    provider_code,
                    target_domain,
                    target_action,
                    idempotency_key,
                    request_hash,
                    dispatch_key,
                    effect_status
                )
                VALUES (
                    :intent_id,
                    1,
                    :correlation_id,
                    'WMS',
                    'wms_integration',
                    'rack-demand',
                    :idempotency_key,
                    :request_hash,
                    :dispatch_key,
                    'PROPOSED'
                )
                """
            ),
            {
                "intent_id": intent_id,
                "correlation_id": f"corr-{intent_id}",
                "idempotency_key": f"idem-{intent_id}",
                "request_hash": f"hash-{intent_id}",
                "dispatch_key": dispatch_key,
            },
        )
    finally:
        await db.execute(text("SET session_replication_role = origin"))


async def _seed_active_placement(
    db: AsyncSession,
    *,
    rack_id: str,
    source_location: str,
    workline_id: int = 10,
    location_field: str = "position_code",
) -> RackPlacement:
    placement = RackPlacement(
        rack_code=rack_id,
        rack_kind=RackKind.SINGLE_LAYER,
        workline_id=workline_id,
        workline_code=f"LINE-{workline_id}",
        placement_status=RackPlacementStatus.ARRIVED,
        source_system=ResourceSourceSystem.WMS,
        source_event_id=f"placement:{rack_id}",
        started_at=datetime(2026, 7, 30, 1, 0),
        **{location_field: source_location},
    )
    db.add(placement)
    await db.flush()
    return placement


def _owner_identity(owner: MaterialFlowOwner) -> dict[str, Any]:
    return {
        "id": owner.id,
        "workline_id": owner.workline_id,
        "object_type": owner.object_type,
        "object_key": owner.object_key,
        "owner_type": owner.owner_type,
        "owner_key": owner.owner_key,
        "owner_intent_id": owner.owner_intent_id,
        "source_event_id": owner.source_event_id,
        "acquired_at_ms": owner.acquired_at_ms,
        "reconciliation_case_id": owner.reconciliation_case_id,
    }


async def _reserve_e08(
    service: Any,
    db: AsyncSession,
    *,
    generation: int,
    station_code: str = "STATION-A",
    dispatch_key: str | None = None,
) -> Any:
    return await service.reserve_root(
        _ctx(db),
        station_code=station_code,
        rack_type="SINGLE_LAYER",
        demand_generation=generation,
        dispatch_key=dispatch_key or f"rack-demand:{station_code}:{generation}",
        required_rack_code=None,
        source_location_code=None,
        destination_station_code=None,
    )


async def _reserve_e09(
    service: Any,
    db: AsyncSession,
    *,
    generation: int,
    station_code: str = "STATION-A",
    rack_id: str = "RACK-09",
    source: str = "FIVE-STATION",
    destination: str | None = None,
) -> Any:
    return await service.reserve_root(
        _ctx(db),
        station_code=station_code,
        rack_type="SINGLE_LAYER",
        demand_generation=generation,
        dispatch_key=f"rack-transport:{station_code}:{generation}",
        required_rack_code=rack_id,
        source_location_code=source,
        destination_station_code=destination or station_code,
    )


@pytest.mark.integration
def test_rack_demand_first_claim_concurrency_rollback_reuse_and_generation() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, _projector_type = _domain_types()
        service = service_type()

        async with session_factory() as winner_db, session_factory() as loser_db:
            winner = await _reserve_e08(service, winner_db, generation=1)
            loser_task = asyncio.create_task(
                _reserve_e09(service, loser_db, generation=1),
                name="competing-e09-demand",
            )
            await asyncio.sleep(0.05)
            assert not loser_task.done(), "unique PREPARING winner must fence the competing root until commit"
            await winner_db.commit()
            loser = await loser_task
            assert winner.created is True
            assert winner.operation.identity == E08
            assert loser.created is False
            assert loser.demand.id == winner.demand.id
            assert loser.operation is None and loser.request is None
            await loser_db.rollback()

        async with session_factory() as rollback_winner_db, session_factory() as rollback_loser_db:
            rolled_back = await _reserve_e08(
                service,
                rollback_winner_db,
                generation=1,
                station_code="STATION-ROLLBACK",
            )
            assert rolled_back.created is True
            rolled_back_id = rolled_back.demand.id
            promoted_task = asyncio.create_task(
                _reserve_e09(
                    service,
                    rollback_loser_db,
                    generation=1,
                    station_code="STATION-ROLLBACK",
                ),
                name="rollback-promoted-e09-demand",
            )
            await asyncio.sleep(0.05)
            assert not promoted_task.done(), "loser must remain blocked until the first insert resolves"
            await rollback_winner_db.rollback()
            promoted = await promoted_task
            assert promoted.created is True
            assert promoted.operation.identity == E09
            assert promoted.demand.id != rolled_back_id
            await rollback_loser_db.rollback()

        async with session_factory() as generation_db:
            first = await _reserve_e08(
                service,
                generation_db,
                generation=1,
                station_code="STATION-GENERATION",
            )
            await _seed_runtime_intent(generation_db, 9101, first.request.dispatch_key)
            first.demand.root_intent_id = 9101
            first.demand.lifecycle_state = "CLOSED"
            first.demand.closed_at_ms = 2000
            await generation_db.commit()
        async with session_factory() as next_db:
            next_generation = await _reserve_e08(
                service,
                next_db,
                generation=2,
                station_code="STATION-GENERATION",
            )
            assert next_generation.created is True
            assert next_generation.demand.demand_generation == 2
            await next_db.rollback()

    asyncio.run(_with_database(scenario))


@pytest.mark.integration
def test_rack_demand_static_root_shape_and_active_e08_e09_mutex() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, _projector_type = _domain_types()
        service = service_type()
        async with session_factory() as db:
            e08 = await _reserve_e08(service, db, generation=1, station_code="STATION-SHAPE")
            assert e08.operation.identity == E08
            assert e08.request.station_code == "STATION-SHAPE"
            assert e08.request.demand_generation == 1

            reused_by_e09 = await _reserve_e09(
                service,
                db,
                generation=1,
                station_code="STATION-SHAPE",
            )
            assert reused_by_e09.created is False
            assert reused_by_e09.demand.id == e08.demand.id
            assert (
                await db.scalar(
                    select(func.count()).select_from(WmsRackDemand).where(WmsRackDemand.station_code == "STATION-SHAPE")
                )
                == 1
            )
            await db.rollback()

        async with session_factory() as db:
            e09 = await _reserve_e09(
                service,
                db,
                generation=2,
                station_code="STATION-KNOWN",
                rack_id="RACK-KNOWN",
                source="FIVE-STATION",
            )
            assert e09.operation.identity == E09
            assert e09.request.rack_id == "RACK-KNOWN"
            assert e09.request.source_location_code == "FIVE-STATION"
            assert e09.request.destination_station_code == "STATION-KNOWN"

            invalid_shapes = (
                {
                    "required_rack_code": "RACK-PARTIAL",
                    "source_location_code": None,
                    "destination_station_code": "STATION-KNOWN",
                },
                {
                    "required_rack_code": None,
                    "source_location_code": "FIVE-STATION",
                    "destination_station_code": None,
                },
                {
                    "required_rack_code": "RACK-MISMATCH",
                    "source_location_code": "FIVE-STATION",
                    "destination_station_code": "OTHER-STATION",
                },
            )
            for index, shape in enumerate(invalid_shapes, start=1):
                with pytest.raises(ValueError, match=r"rack|source|destination|station"):
                    await service.reserve_root(
                        _ctx(db),
                        station_code="STATION-INVALID",
                        rack_type="SINGLE_LAYER",
                        demand_generation=index,
                        dispatch_key=f"invalid-shape-{index}",
                        **shape,
                    )
            await db.rollback()

    asyncio.run(_with_database(scenario))


@pytest.mark.integration
def test_e09_prepare_fails_closed_on_missing_stale_or_wrong_workline_placement_before_owner() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = _domain_types()
        service = service_type()
        projector = projector_type(
            station_role_resolver=_station_role,
            workline_code_resolver=_workline_code,
        )
        cases = (
            ("RACK-PLACEMENT-MISSING", None, 10),
            ("RACK-PLACEMENT-STALE", "OTHER-LOCATION", 10),
            ("RACK-PLACEMENT-WRONG-LINE", "STATION-B", 99),
        )
        for offset, (rack_id, placement_location, placement_workline_id) in enumerate(cases, start=1):
            async with session_factory() as db:
                reservation = await _reserve_e09(
                    service,
                    db,
                    generation=offset,
                    station_code="FIVE-STATION",
                    rack_id=rack_id,
                    source="STATION-B",
                    destination="FIVE-STATION",
                )
                await _seed_runtime_intent(db, 9150 + offset, reservation.request.dispatch_key)
                original_owner = MaterialFlowOwner(
                    workline_id=10,
                    object_type="RACK",
                    object_key=rack_id,
                    owner_type="PIECE_SORTING",
                    owner_key=f"piece:{rack_id}",
                    lifecycle_state="ACTIVE",
                    source_event_id=f"piece:{rack_id}",
                    acquired_at_ms=1000,
                )
                db.add(original_owner)
                if placement_location is not None:
                    await _seed_active_placement(
                        db,
                        rack_id=rack_id,
                        source_location=placement_location,
                        workline_id=placement_workline_id,
                    )
                await db.flush()
                original_identity = _owner_identity(original_owner)
                execution = SimpleNamespace(
                    db=db,
                    ctx=_ctx(db),
                    intent_log=SimpleNamespace(
                        id=9150 + offset,
                        dispatch_key=reservation.request.dispatch_key,
                    ),
                )
                execution.ctx["wms_rack_demand_claim"] = reservation.claim

                with pytest.raises(RuntimeError, match="active rack placement"):
                    await projector.prepare_effect(
                        db,
                        operation=reservation.operation,
                        request=reservation.request,
                        execution=execution,
                    )

                assert _owner_identity(original_owner) == original_identity
                assert original_owner.lifecycle_state == "ACTIVE"
                assert original_owner.released_at_ms is None
                assert reservation.demand.lifecycle_state == "PREPARING"
                assert reservation.demand.root_intent_id is None
                await db.rollback()

    asyncio.run(_with_database(scenario))


@pytest.mark.integration
def test_preparation_hook_binds_generation_root_and_e09_transport_owner_atomically() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = _domain_types()
        service = service_type()
        projector = projector_type()

        async with session_factory() as db:
            reservation = await _reserve_e08(
                service,
                db,
                generation=1,
                station_code="STATION-PREPARE",
            )
            await _seed_runtime_intent(db, 9201, reservation.request.dispatch_key)
            execution = SimpleNamespace(
                db=db,
                ctx=_ctx(db),
                intent_log=SimpleNamespace(id=9201, dispatch_key=reservation.request.dispatch_key),
            )
            execution.ctx["wms_rack_demand_claim"] = reservation.claim
            mismatched = reservation.request.model_copy(update={"demand_generation": 2})
            with pytest.raises(ValueError, match="generation"):
                await projector.prepare_effect(
                    db,
                    operation=reservation.operation,
                    request=mismatched,
                    execution=execution,
                )
            await db.rollback()

        async with session_factory() as db:
            reservation = await _reserve_e09(
                service,
                db,
                generation=1,
                station_code="STATION-OWNER",
                rack_id="RACK-OWNER",
            )
            await _seed_active_placement(
                db,
                rack_id="RACK-OWNER",
                source_location=reservation.request.source_location_code,
                location_field="external_location_code",
            )
            await _seed_runtime_intent(db, 9202, reservation.request.dispatch_key)
            db.add(
                MaterialFlowOwner(
                    workline_id=10,
                    object_type="RACK",
                    object_key="RACK-OWNER",
                    owner_type="PIECE_SORTING",
                    owner_key="existing-piece-flow",
                    lifecycle_state="ACTIVE",
                    source_event_id="existing-owner",
                    acquired_at_ms=1000,
                )
            )
            await db.flush()
            execution = SimpleNamespace(
                db=db,
                ctx=_ctx(db),
                intent_log=SimpleNamespace(id=9202, dispatch_key=reservation.request.dispatch_key),
            )
            execution.ctx["wms_rack_demand_claim"] = reservation.claim
            with pytest.raises(RuntimeError, match="owner"):
                await projector.prepare_effect(
                    db,
                    operation=reservation.operation,
                    request=reservation.request,
                    execution=execution,
                )
            demand = await db.get(WmsRackDemand, reservation.demand.id)
            assert demand is not None
            assert demand.lifecycle_state == "PREPARING"
            assert demand.root_intent_id is None
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(MaterialFlowOwner)
                    .where(
                        MaterialFlowOwner.object_key == "RACK-OWNER",
                        MaterialFlowOwner.lifecycle_state == "ACTIVE",
                    )
                )
                == 1
            )
            await db.rollback()

        async with session_factory() as db:
            reservation = await _reserve_e09(
                service,
                db,
                generation=2,
                station_code="STATION-OWNER",
                rack_id="RACK-OWNER",
            )
            await _seed_active_placement(
                db,
                rack_id="RACK-OWNER",
                source_location=reservation.request.source_location_code,
                location_field="logic_location_code",
            )
            await _seed_runtime_intent(db, 9203, reservation.request.dispatch_key)
            execution = SimpleNamespace(
                db=db,
                ctx=_ctx(db),
                intent_log=SimpleNamespace(id=9203, dispatch_key=reservation.request.dispatch_key),
            )
            execution.ctx["wms_rack_demand_claim"] = reservation.claim
            await projector.prepare_effect(
                db,
                operation=reservation.operation,
                request=reservation.request,
                execution=execution,
            )
            demand = await db.get(WmsRackDemand, reservation.demand.id)
            owner = await db.scalar(
                select(MaterialFlowOwner).where(
                    MaterialFlowOwner.object_type == "RACK",
                    MaterialFlowOwner.object_key == "RACK-OWNER",
                    MaterialFlowOwner.lifecycle_state == "ACTIVE",
                )
            )
            assert demand is not None
            assert demand.lifecycle_state == "ACTIVE"
            assert demand.root_intent_id == 9203
            assert owner is not None
            assert owner.owner_type == "STATION_TRANSPORT"
            assert owner.owner_intent_id == 9203
            await db.rollback()

    asyncio.run(_with_database(scenario))


class _RackArrivalProjection:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record_rack_arrived_at_workline_position(self, _db: AsyncSession, **kwargs: Any) -> None:
        self.calls.append(kwargs)


async def _station_role(
    _db: AsyncSession,
    *,
    workline_id: int,
    station_code: str,
) -> str:
    assert workline_id == 10
    return {
        "STATION-A": "STATION_A",
        "STATION-B": "STATION_B",
    }.get(station_code, "OTHER")


async def _workline_code(_db: AsyncSession, *, workline_id: int) -> str:
    assert workline_id == 10
    return "LINE-10"


async def _prepare_e09(projector: Any, db: AsyncSession, reservation: Any, *, intent_id: int) -> None:
    await _seed_runtime_intent(db, intent_id, reservation.request.dispatch_key)
    execution = SimpleNamespace(
        db=db,
        ctx=_ctx(db),
        intent_log=SimpleNamespace(id=intent_id, dispatch_key=reservation.request.dispatch_key),
    )
    execution.ctx["wms_rack_demand_claim"] = reservation.claim
    await projector.prepare_effect(
        db,
        operation=reservation.operation,
        request=reservation.request,
        execution=execution,
    )


def _completed_event(result: Any) -> EffectReducerEvent:
    return EffectReducerEvent(
        event_type=EffectReducerEventType.STATUS_COMPLETED,
        dispatch_key=result.dispatch_key,
        occurred_at_ms=3000,
        source_event_id=f"completed:{result.dispatch_key}",
        evidence_json={"snapshot": {"result": result.model_dump(mode="json")}},
    )


def _reject_event(dispatch_key: str) -> EffectReducerEvent:
    return EffectReducerEvent(
        event_type=EffectReducerEventType.ASYNC_SUBMIT_REJECTED,
        dispatch_key=dispatch_key,
        attempt_no=1,
        occurred_at_ms=3000,
        source_event_id=f"reject:{dispatch_key}",
        reason_code="NO_RACK_AVAILABLE",
        evidence_json={},
    )


@pytest.mark.integration
def test_terminal_projector_closes_rejects_and_preserves_unknown_owner_state() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = _domain_types()
        service = service_type()
        arrival = _RackArrivalProjection()
        projector = projector_type(
            resource_projection_service=arrival,
            station_role_resolver=_station_role,
            workline_code_resolver=_workline_code,
        )

        async with session_factory() as db:
            reservation = await _reserve_e08(service, db, generation=1)
            await _seed_runtime_intent(db, 9301, reservation.request.dispatch_key)
            reservation.demand.root_intent_id = 9301
            reservation.demand.lifecycle_state = "ACTIVE"
            success = RequestRackSupplyResult(
                dispatch_key=reservation.request.dispatch_key,
                provider_reference="wms-e08",
                source_version="1",
                station_code="STATION-A",
                rack_type="SINGLE_LAYER",
                demand_generation=1,
                rack_id="RACK-ACTUAL",
                final_station_code="STATION-A",
                arrival_relation="AT_STATION",
                task_outcome="SUCCESS",
            )
            await projector.project_event(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E08],
                request_payload=reservation.request.model_dump(mode="json"),
                event=_completed_event(success),
                reduction=SimpleNamespace(state_changed=True, contradiction=False),
            )
            assert reservation.demand.lifecycle_state == "CLOSED"
            assert reservation.demand.closed_at_ms == 3000
            assert arrival.calls[0]["rack_code"] == "RACK-ACTUAL"
            assert arrival.calls[0]["position_code"] == "STATION-A"
            owner = await db.scalar(
                select(MaterialFlowOwner).where(
                    MaterialFlowOwner.object_key == "RACK-ACTUAL",
                    MaterialFlowOwner.lifecycle_state == "ACTIVE",
                )
            )
            assert owner is not None and owner.owner_type == "PIECE_SORTING"
            await db.rollback()

        async with session_factory() as db:
            reservation = await _reserve_e08(
                service,
                db,
                generation=1,
                station_code="FIVE-STATION",
            )
            await _seed_runtime_intent(db, 9302, reservation.request.dispatch_key)
            reservation.demand.root_intent_id = 9302
            reservation.demand.lifecycle_state = "ACTIVE"
            success = RequestRackSupplyResult(
                dispatch_key=reservation.request.dispatch_key,
                provider_reference="wms-e08-five",
                source_version="1",
                station_code="FIVE-STATION",
                rack_type="SINGLE_LAYER",
                demand_generation=1,
                rack_id="RACK-FIVE",
                final_station_code="FIVE-STATION",
                arrival_relation="AT_STATION",
                task_outcome="SUCCESS",
            )
            await projector.project_event(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E08],
                request_payload=reservation.request.model_dump(mode="json"),
                event=_completed_event(success),
                reduction=SimpleNamespace(state_changed=True, contradiction=False),
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(MaterialFlowOwner)
                    .where(
                        MaterialFlowOwner.object_key == "RACK-FIVE",
                        MaterialFlowOwner.owner_type == "PIECE_SORTING",
                    )
                )
                == 0
            )
            await db.rollback()

        async with session_factory() as db:
            reservation = await _reserve_e09(
                service,
                db,
                generation=1,
                station_code="STATION-B",
                rack_id="RACK-UNKNOWN",
            )
            await _seed_runtime_intent(db, 9303, reservation.request.dispatch_key)
            reservation.demand.root_intent_id = 9303
            reservation.demand.lifecycle_state = "ACTIVE"
            db.add(
                MaterialFlowOwner(
                    workline_id=10,
                    object_type="RACK",
                    object_key="RACK-UNKNOWN",
                    owner_type="STATION_TRANSPORT",
                    owner_key=str(reservation.demand.id),
                    owner_intent_id=9303,
                    lifecycle_state="ACTIVE",
                    source_event_id="transport-owner",
                    acquired_at_ms=2000,
                )
            )
            await db.flush()
            reconciliation = EffectReducerEvent(
                event_type=EffectReducerEventType.RECONCILIATION_OPENED,
                dispatch_key=reservation.request.dispatch_key,
                occurred_at_ms=3000,
                source_event_id="reconciliation",
                reason_code="WMS_STATUS_UNKNOWN",
                evidence_json={},
            )
            await projector.project_event(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E09],
                request_payload=reservation.request.model_dump(mode="json"),
                event=reconciliation,
                reduction=SimpleNamespace(state_changed=True, contradiction=False),
            )
            assert reservation.demand.lifecycle_state == "ACTIVE"
            owner = await db.scalar(
                select(MaterialFlowOwner).where(
                    MaterialFlowOwner.object_key == "RACK-UNKNOWN",
                    MaterialFlowOwner.lifecycle_state == "ACTIVE",
                )
            )
            assert owner is not None and owner.owner_type == "STATION_TRANSPORT"

            await projector.project_event(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E09],
                request_payload=reservation.request.model_dump(mode="json"),
                event=_reject_event(reservation.request.dispatch_key),
                reduction=SimpleNamespace(state_changed=True, contradiction=False),
            )
            assert reservation.demand.lifecycle_state == "CLOSED"
            assert owner.lifecycle_state == "RELEASED"
            await db.rollback()

    asyncio.run(_with_database(scenario))


@pytest.mark.integration
def test_e09_inbound_success_transfers_owner_and_projects_final_position() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = _domain_types()
        service = service_type()
        arrival = _RackArrivalProjection()
        projector = projector_type(
            resource_projection_service=arrival,
            station_role_resolver=_station_role,
            workline_code_resolver=_workline_code,
        )

        async with session_factory() as db:
            inbound = await _reserve_e09(
                service,
                db,
                generation=1,
                station_code="STATION-A",
                rack_id="RACK-INBOUND",
                source="FIVE-STATION",
            )
            await _seed_runtime_intent(db, 9401, inbound.request.dispatch_key)
            inbound.demand.root_intent_id = 9401
            inbound.demand.lifecycle_state = "ACTIVE"
            owner = MaterialFlowOwner(
                workline_id=10,
                object_type="RACK",
                object_key="RACK-INBOUND",
                owner_type="STATION_TRANSPORT",
                owner_key=str(inbound.demand.id),
                owner_intent_id=9401,
                lifecycle_state="ACTIVE",
                source_event_id="inbound-owner",
                acquired_at_ms=2000,
            )
            db.add(owner)
            await db.flush()
            result = RequestRackTransportResult(
                dispatch_key=inbound.request.dispatch_key,
                provider_reference="wms-e09-in",
                source_version="1",
                rack_id="RACK-INBOUND",
                source_location_code="FIVE-STATION",
                destination_station_code="STATION-A",
                final_location_code="STATION-A",
                task_outcome="SUCCESS",
            )
            await projector.project_event(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E09],
                request_payload=inbound.request.model_dump(mode="json"),
                event=_completed_event(result),
                reduction=SimpleNamespace(state_changed=True, contradiction=False),
            )

            assert owner.owner_type == "PIECE_SORTING"
            assert owner.lifecycle_state == "ACTIVE"
            assert inbound.demand.lifecycle_state == "CLOSED"
            assert arrival.calls[0]["rack_code"] == "RACK-INBOUND"
            assert arrival.calls[0]["position_code"] == "STATION-A"
            assert arrival.calls[0]["idempotency_key"].startswith("wms-rack-arrival:")
            await db.rollback()

    asyncio.run(_with_database(scenario))


@pytest.mark.integration
def test_real_reducer_and_projector_failure_roll_back_intent_and_demand_atomically() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = _domain_types()
        service = service_type()
        projector = projector_type(
            resource_projection_service=_RackArrivalProjection(),
            station_role_resolver=_station_role,
            workline_code_resolver=_workline_code,
        )
        async with session_factory() as setup_db:
            reservation = await _reserve_e09(
                service,
                setup_db,
                generation=1,
                station_code="FIVE-STATION",
                rack_id="RACK-MISSING-OWNER",
                source="WAREHOUSE",
                destination="FIVE-STATION",
            )
            await _seed_runtime_intent(setup_db, 9501, reservation.request.dispatch_key)
            reservation.demand.root_intent_id = 9501
            reservation.demand.lifecycle_state = "ACTIVE"
            demand_id = reservation.demand.id
            request_payload = reservation.request.model_dump(mode="json")
            await setup_db.commit()

        result = RequestRackTransportResult(
            dispatch_key=reservation.request.dispatch_key,
            provider_reference="wms-e09-missing-owner",
            source_version="1",
            rack_id="RACK-MISSING-OWNER",
            source_location_code="WAREHOUSE",
            destination_station_code="FIVE-STATION",
            final_location_code="FIVE-STATION",
            task_outcome="SUCCESS",
        )
        event = _completed_event(result)
        async with session_factory() as db:
            reduction = await effect_reducer.reduce(db, event)
            assert reduction is not None and reduction.state_changed is True
            with pytest.raises(RuntimeError, match="transport owner is missing"):
                await projector.project_event(
                    db,
                    operation=WMS_OPERATION_BY_IDENTITY[E09],
                    request_payload=request_payload,
                    event=event,
                    reduction=reduction,
                )
            await db.rollback()

        async with session_factory() as verify_db:
            intent = await verify_db.scalar(
                select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == reservation.request.dispatch_key)
            )
            demand = await verify_db.get(WmsRackDemand, demand_id)
            assert intent is not None and intent.effect_status is RuntimeIntentStatus.PROPOSED
            assert demand is not None and demand.lifecycle_state == "ACTIVE"
            assert demand.closed_at_ms is None

    asyncio.run(_with_database(scenario))
