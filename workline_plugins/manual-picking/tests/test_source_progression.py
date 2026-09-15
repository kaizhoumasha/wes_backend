"""来源五层架按权威在位面、原任务和 WMS 离场决定推进。"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.batch_driver import ManualPickingBatchDriver

from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.utils.timezone import timezone


class Positions:
    def __init__(self):  # type: ignore[no-untyped-def]
        self.source = SimpleNamespace(
            object_id="R1",
            workline_id=7,
            position_unknown=False,
            position_json={"kind": "RACK_POSITION", "location_code": "FIVE-POS"},
            arrival_face="90",
            source_transport_task_id="arrival-1",
        )
        self.target = SimpleNamespace(
            workline_id=7,
            position_unknown=False,
            position_json={"kind": "RACK_POSITION", "location_code": "TRANSFER-POS"},
            arrival_face="A",
            source_transport_task_id="target-1",
        )

    async def get(self, _db, _kind, rack_id):  # type: ignore[no-untyped-def]
        return self.target if rack_id == "TARGET" else self.source if rack_id == self.source.object_id else None


class Plans:
    def __init__(self):  # type: ignore[no-untyped-def]
        self.rows = [
            SimpleNamespace(id=11, rack_id="R1", rack_face="90", source_evidence_id=51),
            SimpleNamespace(id=12, rack_id="R1", rack_face="270", source_evidence_id=51),
            SimpleNamespace(id=13, rack_id="R2", rack_face="90", source_evidence_id=52),
        ]
        self.matches = True
        self.owner = None
        self.transfer_owner = None

    async def list_bin_source_racks(self, _db, _task_id):  # type: ignore[no-untyped-def]
        return self.rows

    async def source_transport_matches(self, _db, *_args):  # type: ignore[no-untyped-def]
        return self.matches

    async def first_completed_source_owner_at_position(self, _db, *_args):  # type: ignore[no-untyped-def]
        return self.owner

    async def first_completed_transfer_owner_at_position(self, _db, *_args):  # type: ignore[no-untyped-def]
        return self.transfer_owner


class Flow:
    def __init__(self):  # type: ignore[no-untyped-def]
        self.created = False
        self.busy = False
        self.complete = {("R1", "90")}
        self.calls = []

    async def advance_in_session(self, _db, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self.created

    async def face_progress(self, _db, _line_id, _task_id, rack_id, face):  # type: ignore[no-untyped-def]
        return SimpleNamespace(complete=True) if (rack_id, face) in self.complete else None

    async def has_unclosed_action(self, _db, _line_id):  # type: ignore[no-untyped-def]
        return self.busy


class Creator:
    def __init__(self):  # type: ignore[no-untyped-def]
        self.rotate = []
        self.depart = []
        self.transfer_depart = []

    async def create_rotate(self, _db, **kwargs):  # type: ignore[no-untyped-def]
        self.rotate.append(kwargs)

    async def create_source_return(self, _db, **kwargs):  # type: ignore[no-untyped-def]
        self.depart.append(kwargs)

    async def create_transfer_departure(self, _db, **kwargs):  # type: ignore[no-untyped-def]
        self.transfer_depart.append(kwargs)


def setup_driver():  # type: ignore[no-untyped-def]
    line = SimpleNamespace(
        id=7,
        line_code="LINE-1",
        position_bindings={
            "FIVE_RACK": {"location_id": "FIVE-POS"},
            "TRANSFER_RACK": {"location_id": "TRANSFER-POS"},
            "INLET": {"location_id": "INLET-POS"},
            "OUTLET": {"location_id": "OUTLET-POS"},
        },
    )
    task = SimpleNamespace(
        id=31,
        task_id="PICK-1",
        workline_id=7,
        status="EXECUTING",
        target_rack_id="TARGET",
        target_rack_face="A",
    )
    positions, plans, flow, creator = Positions(), Plans(), Flow(), Creator()
    departure_reader = SimpleNamespace(latest=AsyncMock(return_value=None))
    departure_scheduler = SimpleNamespace(create_in_session=AsyncMock())
    transports = SimpleNamespace(get_task=AsyncMock(return_value=SimpleNamespace(status="SUCCEEDED")))
    tasks = SimpleNamespace(get_by_task_id_for_update=AsyncMock(return_value=task))
    driver = ManualPickingBatchDriver(
        flow,
        plans=plans,
        positions=positions,
        transports=transports,
        rack_creator=creator,
        departure_scheduler=departure_scheduler,
        departure_reader=departure_reader,
        passages=SimpleNamespace(has_bin_before_return_buffer=AsyncMock(return_value=False)),
        tasks=tasks,
        uuid_factory=lambda: "019f3405-2200-7b01-8b01-000000000001",
    )
    return driver, line, task, positions, plans, flow, creator, departure_reader, departure_scheduler


@pytest.mark.asyncio
async def test_same_rack_rotates_once_after_closed_face_and_uses_planned_next_face() -> None:
    driver, line, task, _, _, flow, creator, _, scheduler = setup_driver()
    driver._passages.has_bin_before_return_buffer.return_value = True
    assert await driver.advance_in_session(object(), line, task) == 0
    driver._passages.has_bin_before_return_buffer.return_value = False
    flow.busy = True
    assert await driver.advance_in_session(object(), line, task) == 0
    flow.busy = False
    flow.created = True
    assert await driver.advance_in_session(object(), line, task) == 1
    assert creator.rotate == []
    flow.created = False

    assert await driver.advance_in_session(object(), line, task) == 1
    assert creator.rotate[0]["correlation_id"] == "pt:31:source-face:12"
    assert creator.rotate[0]["target_face"] == "270"
    assert creator.rotate[0]["source_evidence_id"] == 51
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_source_rack_returns_directly_to_wh01_without_wms_decision() -> None:
    driver, line, task, positions, plans, flow, creator, reader, scheduler = setup_driver()
    task.status = "EXECUTION_COMPLETED"
    plans.owner = task
    positions.source.arrival_face = "270"
    positions.source.source_transport_task_id = "rotate-1"
    flow.complete.add(("R1", "270"))

    assert await driver.advance_completed_in_session(object(), line) == 1
    scheduler.create_in_session.assert_not_awaited()
    reader.latest.assert_not_awaited()
    assert creator.depart[0]["source_evidence_id"] == 51
    assert creator.depart[0]["rack_id"] == "R1"
    assert creator.depart[0]["destination"] == sdk.TransportZonePosition("WH01")


@pytest.mark.asyncio
async def test_transfer_decision_starts_after_wms_completion_while_source_return_is_still_open() -> None:
    driver, line, task, _, plans, flow, creator, _, scheduler = setup_driver()
    task.status = "EXECUTION_COMPLETED"
    plans.owner = task
    plans.transfer_owner = task
    flow.busy = True
    driver._passages.has_bin_before_return_buffer.return_value = True

    assert await driver.advance_completed_in_session(object(), line) == 1
    assert creator.depart == []
    intent = scheduler.create_in_session.await_args.args[1]
    assert intent.task_id == task.task_id and intent.rack_id == "TARGET"


@pytest.mark.asyncio
async def test_transfer_rack_wait_retries_and_ready_uses_f01_destination() -> None:
    driver, line, task, _, plans, _, creator, reader, scheduler = setup_driver()
    task.status = "EXECUTION_COMPLETED"
    plans.transfer_owner = task
    intent = sdk.wms_operations.outbound_rack_departure_decide(
        operation_id="old-op",
        task_id="PICK-1",
        rack_id="TARGET",
        current_location=sdk.TransportRackPosition("TRANSFER-POS"),
        current_face="A",
    )
    reader.latest.return_value = SimpleNamespace(
        status=WmsConfirmationStatus.COMPLETED,
        intent=intent,
        outcome=sdk.RackDepartureOutcome(sdk.RackDepartureWait(1000)),
        evidence_id=71,
        completed_at=timezone.now_for_db(),
    )
    assert await driver.advance_completed_in_session(object(), line) == 0
    scheduler.create_in_session.assert_not_awaited()
    reader.latest.return_value.completed_at -= timedelta(seconds=2)
    assert await driver.advance_completed_in_session(object(), line) == 1
    assert scheduler.create_in_session.await_args.args[1].operation_id != "old-op"
    reader.latest.return_value.outcome = sdk.RackDepartureOutcome(
        sdk.RackDepartureReady(sdk.TransportRackPosition("TRANSFER-POS"))
    )
    with pytest.raises(ValueError, match="physical position"):
        await driver.advance_completed_in_session(object(), line)
    assert creator.transfer_depart == []
    reader.latest.return_value.outcome = sdk.RackDepartureOutcome(
        sdk.RackDepartureReady(sdk.TransportRackPosition("STORE-POS"))
    )
    assert await driver.advance_completed_in_session(object(), line) == 1
    assert creator.transfer_depart[0]["source_evidence_id"] == 71
    assert creator.transfer_depart[0]["operation_id"] == intent.operation_id
    assert creator.transfer_depart[0]["destination"] == sdk.TransportRackPosition("STORE-POS")


@pytest.mark.asyncio
async def test_new_task_never_adopts_old_rack_projection() -> None:
    driver, line, task, _, plans, flow, creator, _, _ = setup_driver()
    plans.matches = False
    assert await driver.advance_in_session(object(), line, task) == 0
    assert flow.calls == [] and creator.rotate == [] and creator.depart == []


@pytest.mark.asyncio
async def test_later_rack_starts_when_its_own_ingress_succeeded_even_if_prior_source_is_unfinished() -> None:
    driver, line, task, positions, _, flow, creator, _, scheduler = setup_driver()
    positions.source.object_id = "R2"
    positions.source.arrival_face = "90"
    positions.source.source_transport_task_id = "arrival-2"
    flow.complete.clear()

    flow.created = True

    assert await driver.advance_in_session(object(), line, task) == 1
    assert flow.calls[0]["allow_inbound"] is True
    assert creator.rotate == [] and creator.depart == []
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_later_rack_uses_its_own_success_when_prior_projection_is_still_known() -> None:
    driver, line, task, positions, _, flow, creator, _, scheduler = setup_driver()
    prior = positions.source
    now = timezone.now_for_db()
    prior.updated_at = now - timedelta(seconds=1)
    current = SimpleNamespace(
        object_id="R2",
        workline_id=7,
        position_unknown=False,
        position_json={"kind": "RACK_POSITION", "location_code": "FIVE-POS"},
        arrival_face="90",
        source_transport_task_id="arrival-2",
        updated_at=now,
    )

    async def get_projection(_db, _kind, rack_id):  # type: ignore[no-untyped-def]
        if rack_id == "TARGET":
            return positions.target
        return {"R1": prior, "R2": current}.get(rack_id)

    positions.get = AsyncMock(side_effect=get_projection)
    flow.created = True

    assert await driver.advance_in_session(object(), line, task) == 1
    assert flow.calls[0]["rack_id"] == "R2"
    assert creator.rotate == [] and creator.depart == []
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_later_revision_face_on_current_rack_precedes_other_rack() -> None:
    driver, line, task, positions, plans, flow, creator, _, _ = setup_driver()
    plans.rows = [plans.rows[0], plans.rows[2], plans.rows[1]]
    positions.source.arrival_face = "270"
    flow.created = True

    assert await driver.advance_in_session(object(), line, task) == 1
    assert flow.calls[0]["allow_inbound"] is True
    assert creator.rotate == [] and creator.depart == []
