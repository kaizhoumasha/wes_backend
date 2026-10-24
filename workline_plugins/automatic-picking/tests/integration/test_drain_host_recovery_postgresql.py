"""宿主持久扫描发现既有 drain 义务；仅注入离线插件，不启用生产装配。"""

import asyncio

import pytest
from automatic_picking.application.plugin import build_plugin
from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
from automatic_picking.application.work_repository import WorkRepository
from sqlalchemy import select
from test_drain_completion_postgresql import clean_closed_root
from test_drain_continuation_postgresql import restart
from test_drain_departure_postgresql import departure_result, departures
from test_drain_progress_postgresql import current, requests
from test_drain_ready_continuation_postgresql import actions, input_for, serve, successor
from test_drain_replenishment_postgresql import (
    database,
    drainable,
    fingerprint,
    observed,
    pending,
    qualified,
)
from test_drain_wait_postgresql import waiting
from test_return_allocation_postgresql import arrived

from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.services.picking_task_plan_activation import (
    PickingTaskPlanActivationService,
)
from src.app.workline.models import WorkLine
from src.utils.timezone import timezone

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def scanner(p):
    batch = restart(p)
    plugin = build_plugin(scan_flow=p.app[1], batch_driver=batch, works=WorkRepository())
    async with p.sessions() as db:
        line = await db.get(WorkLine, p.line.id)
        assert line.is_active and (line.plugin_key, line.plugin_version) == (
            plugin.plugin_key,
            plugin.plugin_version,
        )
        assert (
            await db.scalar(
                select(PickingTask.id).where(PickingTask.workline_id == line.id, PickingTask.status == "EXECUTING")
            )
            is None
        )
    return PickingTaskPlanActivationService(p.sessions, plugins=(plugin,), transport_creator=p.app[2].inbound.creator)


async def test_host_scan_finds_due_wait_without_executing_or_notification(drainable):
    p = drainable
    first, wait, _ = await waiting(p)
    await clean_closed_root(p, first)
    p.now = wait.retry_at
    host = await scanner(p)
    assert await host.activate_batch() > 0
    next_current = await current(p)
    assert next_current.operation_id not in {first.operation_id, wait.operation_id}
    assert next_current.started_operation_id == first.operation_id
    assert next_current.return_ids == next_current.request_return_ids == wait.return_ids
    assert next_current.response_evidence_id is None and next_current.retry_at is None
    assert {row.operation_id for row in await requests(p)} == {wait.operation_id, next_current.operation_id}
    before = await fingerprint(p)
    assert await (await scanner(p)).activate_batch() == 0
    assert await fingerprint(p) == before


async def test_host_scan_resumes_successor_ready_and_creates_one_exact_inbound(drainable):
    p = drainable
    first, ready, _ = await successor(p)
    await input_for(p, ready, "OFFLINE-NEXT")
    await clean_closed_root(p, first)
    host = await scanner(p)
    await asyncio.gather(*(host.activate_batch() for _ in range(4)))
    inbound = await actions(p, ready, "CTU01")
    assert len(inbound) == 1
    assert inbound[0].request_json["rack_id"] == "OFFLINE-NEXT"
    assert inbound[0].request_json["target"] == {
        "kind": "RACK_POSITION",
        "location_code": "OFFLINE-NEXT-WORK",
    }
    assert (await current(p)).id == ready.id and len(await requests(p)) == 1
    before = await fingerprint(p)
    assert await (await scanner(p)).activate_batch() == 0
    assert await fingerprint(p) == before


async def test_host_scan_recovers_original_departures_after_current_exit_and_root_cleanup(drainable):
    p = drainable
    first, ready, _ = await successor(p)
    original_departure = (await departures(p))[0]
    _, last_departure = await serve(p, ready, fill=True)
    assert await current(p) is None
    await clean_closed_root(p, first)
    await clean_closed_root(p, ready)
    p.positions, p.buffer = {}, None
    async with p.sessions() as db:
        phases = (await db.scalars(select(AutomaticPickingReturnMotion))).all()
        transport_ids = set((await db.scalars(select(TransportTask.transport_task_id))).all())
        frozen = {row.id: row.departure_transport_task_id for row in phases}
        assert set(frozen.values()) == {original_departure.transport_task_id, last_departure.transport_task_id}
        assert all(row.archived_at is None for row in phases)
    host = await scanner(p)
    before = await fingerprint(p)
    assert await host.activate_batch() == 0
    assert await fingerprint(p) == before
    for task in (original_departure, last_departure):
        await departure_result(p, task)
        p.now = timezone.now_for_db()
        assert await (await scanner(p)).activate_batch() > 0
    async with p.sessions() as db:
        phases = (await db.scalars(select(AutomaticPickingReturnMotion))).all()
        assert {row.id: row.departure_transport_task_id for row in phases} == frozen
        assert all(row.archived_at is not None for row in phases)
        assert set((await db.scalars(select(TransportTask.transport_task_id))).all()) == transport_ids
    assert await current(p) is None and await requests(p) == []
    before = await fingerprint(p)
    assert await (await scanner(p)).activate_batch() == 0
    assert await fingerprint(p) == before
