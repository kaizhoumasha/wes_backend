"""E09 出站 owner handoff 的真实 PostgreSQL 合同。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func
from sqlmodel import select

from src.app.runtime.orchestration.material_flow_owner import MaterialFlowOwner
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.fulfillment_operations import RequestRackTransportResult
from tests.integration.workline_capabilities.test_wms_rack_demand_domain_postgresql import (
    E09,
    _completed_event,
    _domain_types,
    _owner_identity,
    _prepare_e09,
    _RackArrivalProjection,
    _reject_event,
    _reserve_e09,
    _seed_active_placement,
    _station_role,
    _with_database,
    _workline_code,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.integration
def test_e09_outbound_prepare_reject_restore_and_success_release_are_directional() -> None:
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
            rejected = await _reserve_e09(
                service,
                db,
                generation=1,
                station_code="FIVE-STATION",
                rack_id="RACK-OUTBOUND-REJECT",
                source="STATION-B",
                destination="FIVE-STATION",
            )
            owner = MaterialFlowOwner(
                workline_id=10,
                object_type="RACK",
                object_key="RACK-OUTBOUND-REJECT",
                owner_type="PIECE_SORTING",
                owner_key="source-piece-owner",
                lifecycle_state="ACTIVE",
                source_event_id="source-piece-owner",
                acquired_at_ms=1000,
            )
            db.add(owner)
            await _seed_active_placement(
                db,
                rack_id="RACK-OUTBOUND-REJECT",
                source_location="STATION-B",
                location_field="position_code",
            )
            await db.flush()
            original_identity = _owner_identity(owner)

            await _prepare_e09(projector, db, rejected, intent_id=9402)

            transport = await db.scalar(
                select(MaterialFlowOwner).where(
                    MaterialFlowOwner.object_key == "RACK-OUTBOUND-REJECT",
                    MaterialFlowOwner.lifecycle_state == "ACTIVE",
                )
            )
            assert rejected.demand.handoff_from_owner_id == owner.id
            assert owner.lifecycle_state == "RELEASED"
            assert owner.released_at_ms is not None
            assert _owner_identity(owner) == original_identity
            assert transport is not None and transport.id != owner.id
            assert transport.owner_type == "STATION_TRANSPORT"
            assert transport.owner_key == str(rejected.demand.id)
            assert transport.owner_intent_id == 9402
            await projector.project_event(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E09],
                request_payload=rejected.request.model_dump(mode="json"),
                event=_reject_event(rejected.request.dispatch_key),
                reduction=SimpleNamespace(state_changed=True, contradiction=False),
            )
            assert owner.lifecycle_state == "ACTIVE"
            assert owner.released_at_ms is None
            assert _owner_identity(owner) == original_identity
            assert transport.lifecycle_state == "RELEASED"
            assert rejected.demand.lifecycle_state == "CLOSED"
            await db.rollback()

        async with session_factory() as db:
            completed = await _reserve_e09(
                service,
                db,
                generation=2,
                station_code="FIVE-STATION",
                rack_id="RACK-OUTBOUND-SUCCESS",
                source="STATION-A",
                destination="FIVE-STATION",
            )
            owner = MaterialFlowOwner(
                workline_id=10,
                object_type="RACK",
                object_key="RACK-OUTBOUND-SUCCESS",
                owner_type="PIECE_SORTING",
                owner_key="source-piece-owner",
                lifecycle_state="ACTIVE",
                source_event_id="source-piece-owner",
                acquired_at_ms=1000,
            )
            db.add(owner)
            await _seed_active_placement(
                db,
                rack_id="RACK-OUTBOUND-SUCCESS",
                source_location="STATION-A",
                location_field="location_code",
            )
            await db.flush()
            original_identity = _owner_identity(owner)
            await _prepare_e09(projector, db, completed, intent_id=9403)
            transport = await db.scalar(
                select(MaterialFlowOwner).where(
                    MaterialFlowOwner.object_key == "RACK-OUTBOUND-SUCCESS",
                    MaterialFlowOwner.lifecycle_state == "ACTIVE",
                )
            )
            assert completed.demand.handoff_from_owner_id == owner.id
            assert owner.lifecycle_state == "RELEASED"
            assert _owner_identity(owner) == original_identity
            assert transport is not None and transport.id != owner.id
            assert transport.owner_type == "STATION_TRANSPORT"
            result = RequestRackTransportResult(
                dispatch_key=completed.request.dispatch_key,
                provider_reference="wms-e09-out",
                source_version="1",
                rack_id="RACK-OUTBOUND-SUCCESS",
                source_location_code="STATION-A",
                destination_station_code="FIVE-STATION",
                final_location_code="FIVE-STATION",
                task_outcome="SUCCESS",
            )

            await projector.project_event(
                db,
                operation=WMS_OPERATION_BY_IDENTITY[E09],
                request_payload=completed.request.model_dump(mode="json"),
                event=_completed_event(result),
                reduction=SimpleNamespace(state_changed=True, contradiction=False),
            )

            assert owner.lifecycle_state == "RELEASED"
            assert _owner_identity(owner) == original_identity
            assert transport.lifecycle_state == "RELEASED"
            assert transport.released_at_ms == 3000
            assert completed.demand.lifecycle_state == "CLOSED"
            assert arrival.calls[-1]["rack_code"] == "RACK-OUTBOUND-SUCCESS"
            assert arrival.calls[-1]["position_code"] == "FIVE-STATION"
            assert arrival.calls[-1]["idempotency_key"].startswith("wms-rack-arrival:")
            await db.rollback()

    asyncio.run(_with_database(scenario))


@pytest.mark.integration
def test_e09_outbound_handoff_rolls_back_to_the_original_piece_owner_identity() -> None:
    async def scenario(session_factory: async_sessionmaker[AsyncSession]) -> None:
        service_type, projector_type = _domain_types()
        service = service_type()
        projector = projector_type(
            station_role_resolver=_station_role,
            workline_code_resolver=_workline_code,
        )
        async with session_factory() as setup_db:
            await _seed_active_placement(
                setup_db,
                rack_id="RACK-HANDOFF-ROLLBACK",
                source_location="STATION-B",
                location_field="external_location_code",
            )
            original = MaterialFlowOwner(
                workline_id=10,
                object_type="RACK",
                object_key="RACK-HANDOFF-ROLLBACK",
                owner_type="PIECE_SORTING",
                owner_key="piece:rollback",
                owner_intent_id=None,
                lifecycle_state="ACTIVE",
                source_event_id="piece:rollback:source",
                acquired_at_ms=777,
            )
            setup_db.add(original)
            await setup_db.commit()
            original_id = original.id
            original_identity = _owner_identity(original)

        async with session_factory() as db:
            reservation = await _reserve_e09(
                service,
                db,
                generation=1,
                station_code="FIVE-STATION",
                rack_id="RACK-HANDOFF-ROLLBACK",
                source="STATION-B",
                destination="FIVE-STATION",
            )
            await _prepare_e09(projector, db, reservation, intent_id=9450)
            assert reservation.demand.handoff_from_owner_id == original_id
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(MaterialFlowOwner)
                    .where(
                        MaterialFlowOwner.object_key == "RACK-HANDOFF-ROLLBACK",
                        MaterialFlowOwner.owner_type == "STATION_TRANSPORT",
                        MaterialFlowOwner.lifecycle_state == "ACTIVE",
                    )
                )
                == 1
            )
            await db.rollback()

        async with session_factory() as verify_db:
            restored = await verify_db.get(MaterialFlowOwner, original_id)
            assert restored is not None
            assert _owner_identity(restored) == original_identity
            assert restored.lifecycle_state == "ACTIVE"
            assert restored.released_at_ms is None
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(MaterialFlowOwner)
                    .where(
                        MaterialFlowOwner.object_key == "RACK-HANDOFF-ROLLBACK",
                        MaterialFlowOwner.owner_type == "STATION_TRANSPORT",
                    )
                )
                == 0
            )

    asyncio.run(_with_database(scenario))
