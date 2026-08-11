"""Rack supply 履约 schema 的真实 PostgreSQL 合同。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text

from src.app.runtime.orchestration.repositories.wms_fulfillment_domain_repository import (
    WmsFulfillmentDomainRepository,
)
from src.app.runtime.orchestration.services.rack_demand_service import RackDemandService
from src.app.runtime.orchestration.wms_rack_demand import WmsRackDemand
from src.app.workline.models.workline import LineType, WorkLine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_concurrent_rack_supply_reservations_reuse_one_postgresql_demand(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid4().hex
    async with integration_session_factory.begin() as seed_db:
        workline = WorkLine(
            line_code=f"RACK-SUPPLY-PG-{suffix}",
            line_name="Rack supply PostgreSQL owner",
            line_type=LineType.AUTO,
            is_active=True,
        )
        seed_db.add(workline)
        await seed_db.flush()
        workline_id = workline.id
    assert workline_id is not None

    first_db = integration_session_factory()
    second_db = integration_session_factory()
    second_reservation_task: asyncio.Task | None = None
    service = RackDemandService(repository=WmsFulfillmentDomainRepository(), now_ms=lambda: 123)
    try:
        first_context = {
            "db": first_db,
            "session": SimpleNamespace(id=1),
            "workline": SimpleNamespace(id=workline_id),
        }
        first = await service.reserve_root(
            first_context,
            station_code=f"STATION-{suffix}",
            rack_type="SINGLE_LAYER",
            demand_generation=1,
            dispatch_key=f"rack-supply:{suffix}:first",
        )
        second_context = {
            "db": second_db,
            "session": SimpleNamespace(id=2),
            "workline": SimpleNamespace(id=workline_id),
        }
        second_backend_pid = await second_db.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(second_backend_pid, int)
        second_reservation_task = asyncio.create_task(
            service.reserve_root(
                second_context,
                station_code=f"STATION-{suffix}",
                rack_type="SINGLE_LAYER",
                demand_generation=2,
                dispatch_key=f"rack-supply:{suffix}:second",
            )
        )

        lock_wait_deadline = asyncio.get_running_loop().time() + 10
        last_wait_state: dict[str, object] | None = None
        async with integration_session_factory() as observer_db:
            while True:
                wait_state = (
                    (
                        await observer_db.execute(
                            text(
                                "SELECT state, wait_event_type, wait_event "
                                "FROM pg_stat_activity WHERE pid = :backend_pid"
                            ),
                            {"backend_pid": second_backend_pid},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                last_wait_state = dict(wait_state) if wait_state is not None else None
                if last_wait_state is not None and last_wait_state["wait_event_type"] == "Lock":
                    break
                if second_reservation_task.done():
                    pytest.fail(
                        "competing reservation completed before PostgreSQL lock wait; "
                        f"last_wait_state={last_wait_state!r}, exception={second_reservation_task.exception()!r}"
                    )
                if asyncio.get_running_loop().time() >= lock_wait_deadline:
                    pytest.fail(
                        "competing reservation did not enter PostgreSQL lock wait before deadline; "
                        f"last_wait_state={last_wait_state!r}"
                    )
                await observer_db.rollback()
                await asyncio.sleep(0.01)

        assert last_wait_state == {
            "state": "active",
            "wait_event_type": "Lock",
            "wait_event": "transactionid",
        }
        assert not second_reservation_task.done()

        await first_db.commit()
        second = await asyncio.wait_for(second_reservation_task, timeout=10)
        await second_db.commit()

        assert first.created is True
        assert first.operation is not None
        assert first.operation.identity == "wms.fulfillment.request_rack_supply@v1"
        assert first.request is not None
        assert first_context["wms_rack_demand_claim"] == first.claim
        assert second.created is False
        assert second.operation is None
        assert second.request is None
        assert "wms_rack_demand_claim" not in second_context
        assert second.demand.id == first.demand.id

        async with integration_session_factory() as verify_db:
            demand_count = await verify_db.scalar(
                select(func.count())
                .select_from(WmsRackDemand)
                .where(
                    WmsRackDemand.workline_id == workline_id,
                    WmsRackDemand.station_code == f"STATION-{suffix}",
                    WmsRackDemand.rack_type == "SINGLE_LAYER",
                )
            )
        assert demand_count == 1
    finally:
        if second_reservation_task is not None and not second_reservation_task.done():
            second_reservation_task.cancel()
            await asyncio.gather(second_reservation_task, return_exceptions=True)
        await first_db.rollback()
        await second_db.rollback()
        await first_db.close()
        await second_db.close()
        async with integration_session_factory.begin() as cleanup_db:
            await cleanup_db.execute(delete(WmsRackDemand).where(WmsRackDemand.workline_id == workline_id))
            await cleanup_db.execute(delete(WorkLine).where(WorkLine.id == workline_id))


async def test_wms_rack_supply_schema_excludes_retired_transport_owner(
    integration_db_session: AsyncSession,
) -> None:
    columns_result = await integration_db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'wes_runtime' AND table_name = 'wms_rack_demands'"
        )
    )
    assert {row[0] for row in columns_result} == {
        "id",
        "workline_id",
        "station_code",
        "rack_type",
        "demand_generation",
        "root_intent_id",
        "lifecycle_state",
        "opened_at_ms",
        "closed_at_ms",
        "reconciliation_case_id",
    }

    constraints_result = await integration_db_session.execute(
        text("SELECT conname FROM pg_constraint WHERE conrelid = 'wes_runtime.wms_rack_demands'::regclass")
    )
    assert {row[0] for row in constraints_result} == {
        "ck_wms_rack_demands_generation",
        "ck_wms_rack_demands_lifecycle",
        "fk_wms_rack_demands_reconciliation_case",
        "fk_wms_rack_demands_root_intent",
        "pk_wms_rack_demands",
        "uq_wms_rack_demands_generation",
        "uq_wms_rack_demands_root_intent",
    }

    owner_constraint = await integration_db_session.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'wes_runtime.material_flow_owners'::regclass "
            "AND conname = 'ck_material_flow_owners_owner_type'"
        )
    )
    owner_definition = owner_constraint.scalar_one()
    assert "FULL_BOX_EXCHANGE" in owner_definition
    assert "PIECE_SORTING" in owner_definition
    assert "STATION_TRANSPORT" not in owner_definition
