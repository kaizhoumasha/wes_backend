"""人工拣料批次创建遵守业务串行和退箱 FIFO。"""

from datetime import datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk

from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot


class _Repository:
    def __init__(
        self, *, busy: bool = False, return_due: bool = True, face_done: bool = False, between_chunks: bool = False
    ):
        self.busy = busy
        self.return_due = return_due
        self.face_done = face_done
        self.between_chunks = between_chunks

    async def has_unclosed_action_for_face(self, _db, _workline_id, _task_id, _plan_revision, _rack_id, _rack_face):  # type: ignore[no-untyped-def]
        return self.busy

    async def return_retry_due(self, _db, _workline_id, _rack_id, _rack_face, _now, _after):  # type: ignore[no-untyped-def]
        return self.return_due

    async def inbound_progress(
        self, _db, _workline_id, _task_id, _plan_revision, _rack_id, _rack_face, _inlet_location
    ):  # type: ignore[no-untyped-def]
        return (
            SimpleNamespace(next_offset=None, feed_complete=True)
            if self.face_done
            else (
                SimpleNamespace(next_offset=4, feed_complete=False, last_chunk_created_at=datetime(2026, 9, 13, 11))
                if self.between_chunks
                else None
            )
        )


class _Passages:
    def __init__(self, bins):  # type: ignore[no-untyped-def]
        self.rows = tuple(SimpleNamespace(bin_code=code, return_state="READY") for code in bins)

    async def ready_return_prefix_for_update(self, _db, _workline_id):  # type: ignore[no-untyped-def]
        return self.rows


class _Scheduler:
    def __init__(self) -> None:
        self.intents = []

    async def create_in_session(self, _db, intent, *, workline_id, created_at):  # type: ignore[no-untyped-def]
        self.intents.append((intent, workline_id, created_at))


class _Inbound:
    def __init__(self) -> None:
        self.calls = []

    async def create_inbound_chunk(self, _db, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_batch_flow_returns_between_inbound_chunks_and_keeps_single_wms_request() -> None:
    module = import_module("manual_picking.application.batch_flow")
    repo = _Repository(between_chunks=True)
    passages = _Passages(("A000000001", "A000000002"))
    scheduler = _Scheduler()
    flow = module.ManualPickingBatchFlow(repo, passages, scheduler, _Inbound(), uuid_factory=lambda: "op-1")
    now = datetime(2026, 9, 13, 12)

    created = await flow.advance_in_session(
        object(),
        workline_id=7,
        workline_code="LINE-1",
        picking_task_id=31,
        task_id="PICK-1",
        plan_revision=1,
        rack_id="R1",
        rack_face="90",
        return_location="CNV0302",
        inlet_location="CNV0301",
        now=now,
    )

    assert created
    assert type(scheduler.intents[0][0]) is sdk.BinReturnBatchIntent
    assert [row.return_state for row in passages.rows] == ["READY", "READY"]
    assert [candidate.bin_code for candidate in scheduler.intents[0][0].return_candidates] == [
        "A000000001",
        "A000000002",
    ]
    assert scheduler.intents[0][1:] == (7, now)
    repo.busy = True
    assert not await flow.advance_in_session(
        object(),
        workline_id=7,
        workline_code="LINE-1",
        picking_task_id=31,
        task_id="PICK-1",
        plan_revision=1,
        rack_id="R1",
        rack_face="90",
        return_location="CNV0302",
        inlet_location="CNV0301",
        now=now,
    )
    assert len(scheduler.intents) == 1


@pytest.mark.asyncio
async def test_batch_flow_uses_inbound_four_when_return_retry_waits() -> None:
    module = import_module("manual_picking.application.batch_flow")
    scheduler = _Scheduler()
    flow = module.ManualPickingBatchFlow(
        _Repository(return_due=False), _Passages(("A000000001",)), scheduler, _Inbound(), uuid_factory=lambda: "op-2"
    )

    assert await flow.advance_in_session(
        object(),
        workline_id=7,
        workline_code="LINE-1",
        picking_task_id=31,
        task_id="PICK-1",
        plan_revision=1,
        rack_id="R1",
        rack_face="90",
        return_location="CNV0302",
        inlet_location="CNV0301",
        now=datetime(2026, 9, 13, 12),
    )
    assert type(scheduler.intents[0][0]) is sdk.BinInboundBatchIntent
    assert scheduler.intents[0][0].rack_face == "90"


@pytest.mark.asyncio
@pytest.mark.parametrize("face_done", [True, False])
async def test_batch_flow_requests_inbound_before_return_on_first_face(face_done: bool) -> None:
    module = import_module("manual_picking.application.batch_flow")
    scheduler = _Scheduler()
    flow = module.ManualPickingBatchFlow(
        _Repository(face_done=face_done, return_due=True),
        _Passages(("A000000001",)),
        scheduler,
        _Inbound(),
        uuid_factory=lambda: "return-op",
    )

    assert await flow.advance_in_session(
        object(),
        workline_id=7,
        workline_code="LINE-1",
        picking_task_id=31,
        task_id="PICK-1",
        plan_revision=1,
        rack_id="R1",
        rack_face="90",
        return_location="CNV0302",
        inlet_location="CNV0301",
        now=datetime(2026, 9, 13, 12),
    )
    expected = sdk.BinReturnBatchIntent if face_done else sdk.BinInboundBatchIntent
    assert type(scheduler.intents[0][0]) is expected


@pytest.mark.asyncio
async def test_cancelled_rack_can_start_return_batch_without_inbound_progress() -> None:
    module = import_module("manual_picking.application.batch_flow")
    scheduler = _Scheduler()
    flow = module.ManualPickingBatchFlow(
        _Repository(return_due=True),
        _Passages(("A000001905",)),
        scheduler,
        _Inbound(),
        uuid_factory=lambda: "return-op",
    )

    assert await flow.advance_in_session(
        object(),
        workline_id=7,
        workline_code="LINE-1",
        picking_task_id=31,
        task_id="PICK-1",
        plan_revision=1,
        rack_id="510050",
        rack_face="270",
        return_location="CNV0302",
        inlet_location="CNV0301",
        now=datetime(2026, 9, 13, 12),
        allow_inbound=False,
    )
    intent = scheduler.intents[0][0]
    assert type(intent) is sdk.BinReturnBatchIntent
    assert intent.rack_id == "510050"
    assert intent.return_candidates[0].bin_code == "A000001905"


@pytest.mark.asyncio
async def test_frozen_face_schedules_second_transport_without_second_wms_request() -> None:
    module = import_module("manual_picking.application.batch_flow")
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-1", task_id="PICK-1", plan_revision=1, rack_id="R1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady(
        tuple(
            sdk.BinInboundBatchMember(f"BIN-{index}", sdk.TransportRackBinSlot("R1", "90", f"S-{index}"))
            for index in range(1, 6)
        )
    )

    class Repository(_Repository):
        async def inbound_progress(
            self, _db, _workline_id, _task_id, _plan_revision, _rack_id, _rack_face, _inlet_location
        ):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                intent=intent,
                result=ready,
                evidence_id=31,
                next_offset=4,
                feed_complete=False,
                last_chunk_created_at=datetime(2026, 9, 13, 11),
            )

    scheduler = _Scheduler()
    inbound = _Inbound()
    flow = module.ManualPickingBatchFlow(
        Repository(return_due=False), _Passages(()), scheduler, inbound, uuid_factory=lambda: "unused"
    )
    assert await flow.advance_in_session(
        object(),
        workline_id=7,
        workline_code="LINE-1",
        picking_task_id=31,
        task_id="PICK-1",
        plan_revision=1,
        rack_id="R1",
        rack_face="90",
        return_location="CNV0302",
        inlet_location="CNV0301",
        now=datetime(2026, 9, 13, 12),
    )
    assert scheduler.intents == []
    assert inbound.calls[0]["offset"] == 4
    assert inbound.calls[0]["evidence_id"] == 31


@pytest.mark.asyncio
async def test_cancelled_face_does_not_create_an_inbound_chunk() -> None:
    module = import_module("manual_picking.application.batch_flow")
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-1", task_id="PICK-1", plan_revision=1, rack_id="R1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady((sdk.BinInboundBatchMember("BIN-1", sdk.TransportRackBinSlot("R1", "90", "S-1")),))

    class Repository(_Repository):
        async def inbound_progress(
            self, _db, _workline_id, _task_id, _plan_revision, _rack_id, _rack_face, _inlet_location
        ):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                intent=intent,
                result=ready,
                evidence_id=31,
                next_offset=0,
                feed_complete=False,
                last_chunk_created_at=datetime(2026, 9, 13, 11),
            )

    scheduler = _Scheduler()
    inbound = _Inbound()
    flow = module.ManualPickingBatchFlow(
        Repository(return_due=False), _Passages(()), scheduler, inbound, uuid_factory=lambda: "unused"
    )

    assert not await flow.advance_in_session(
        object(),
        workline_id=7,
        workline_code="LINE-1",
        picking_task_id=31,
        task_id="PICK-1",
        plan_revision=1,
        rack_id="R1",
        rack_face="90",
        return_location="CNV0302",
        inlet_location="CNV0301",
        now=datetime(2026, 9, 13, 12),
        allow_inbound=False,
    )
    assert scheduler.intents == []
    assert inbound.calls == []


@pytest.mark.asyncio
async def test_inbound_ready_creates_one_bound_transport_only_for_confirmed_rack_face() -> None:
    module = import_module("manual_picking.application.batch_result")
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-1", task_id="PICK-1", plan_revision=1, rack_id="R1", rack_face="90"
    )
    outcome = sdk.BinInboundBatchOutcome(
        sdk.BinInboundBatchReady(
            tuple(
                sdk.BinInboundBatchMember(f"A00000000{index}", sdk.TransportRackBinSlot("R1", "90", f"S{index}"))
                for index in range(1, 6)
            )
        )
    )

    class Reader:
        async def read_inbound(self, _db, _evidence, *, workline_id):  # type: ignore[no-untyped-def]
            assert workline_id == 7
            return intent, outcome

    class Transport:
        def __init__(self):
            self.calls = []

        async def create(self, _db, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs)

    transport = Transport()
    flow = module.ManualPickingBatchResultFlow(Reader(), transport, _Passages(()))
    evidence = SimpleNamespace(id=31, operation_id="batch-1")

    assert (
        await flow.apply_inbound_in_session(
            object(),
            evidence,
            workline_id=7,
            picking_task_id=31,
            confirmed_rack_id="R1",
            confirmed_face="90",
            inlet_location="CNV0301",
        )
        == "INBOUND_READY"
    )
    assert len(transport.calls) == 1
    assert transport.calls[0]["picking_task_id"] == 31
    assert transport.calls[0]["source_evidence_id"] == 31
    assert transport.calls[0]["correlation_id"] == "batch-1:0"
    assert transport.calls[0]["moves"] == tuple(
        BinMove(f"A00000000{index}", RackBinSlot("R1", "90", f"S{index}"), HandoffPosition("CNV0301"))
        for index in range(1, 5)
    )
    await flow.create_inbound_chunk(
        object(),
        workline_id=7,
        picking_task_id=31,
        intent=intent,
        ready=outcome.result,
        evidence_id=31,
        offset=4,
        inlet_location="CNV0301",
    )
    assert transport.calls[1]["correlation_id"] == "batch-1:4"
    assert transport.calls[1]["picking_task_id"] == 31
    assert transport.calls[1]["moves"] == (
        BinMove("A000000005", RackBinSlot("R1", "90", "S5"), HandoffPosition("CNV0301")),
    )
    assert (
        await flow.apply_inbound_in_session(
            object(),
            evidence,
            workline_id=7,
            picking_task_id=31,
            confirmed_rack_id="R2",
            confirmed_face="90",
            inlet_location="CNV0301",
        )
        is None
    )
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_return_ready_moves_only_selected_fifo_prefix_and_no_batch_leaves_queue() -> None:
    module = import_module("manual_picking.application.batch_result")
    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="batch-2",
        workline_code="LINE-1",
        rack_id="R1",
        rack_face="90",
        return_candidates=(
            sdk.BinReturnCandidate(1, "A000000001", "CNV0302"),
            sdk.BinReturnCandidate(2, "A000000002", "CNV0302"),
        ),
    )

    class Reader:
        outcome = sdk.BinReturnBatchOutcome(
            sdk.BinReturnBatchReady((sdk.BinReturnMove(1, "A000000001", sdk.TransportRackBinSlot("R1", "90", "S1")),))
        )

        async def read_return(self, _db, _evidence, *, workline_id):  # type: ignore[no-untyped-def]
            assert workline_id == 7
            return intent, self.outcome

    class Transport:
        def __init__(self):
            self.calls = []

        async def create(self, _db, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs)

    reader = Reader()
    transport = Transport()
    passages = _Passages(("A000000001", "A000000002"))
    flow = module.ManualPickingBatchResultFlow(reader, transport, passages)
    evidence = SimpleNamespace(id=32, operation_id="batch-2")

    assert (
        await flow.apply_return_in_session(
            object(),
            evidence,
            workline_id=7,
            workline_code="LINE-1",
            confirmed_rack_id="R1",
            confirmed_face="90",
            return_location="CNV0302",
        )
        == "RETURN_READY"
    )
    assert [row.return_state for row in passages.rows] == ["RETURN_REQUESTED", "READY"]
    assert transport.calls[0]["moves"] == (
        BinMove("A000000001", HandoffPosition("CNV0302"), RackBinSlot("R1", "90", "S1")),
    )

    new_passages = _Passages(("A000000001", "A000000002"))
    reader.outcome = sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1000))
    flow = module.ManualPickingBatchResultFlow(reader, transport, new_passages)
    assert (
        await flow.apply_return_in_session(
            object(),
            evidence,
            workline_id=7,
            workline_code="LINE-1",
            confirmed_rack_id="R1",
            confirmed_face="90",
            return_location="CNV0302",
        )
        == "RETURN_NO_BATCH"
    )
    assert [row.return_state for row in new_passages.rows] == ["READY", "READY"]
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_batch_driver_starts_only_for_authoritatively_positioned_rack_and_target() -> None:
    module = import_module("manual_picking.application.batch_driver")
    source = SimpleNamespace(
        object_id="R1",
        workline_id=7,
        position_unknown=False,
        position_json={"kind": "RACK_POSITION", "location_code": "FIVE-POS"},
        arrival_face="90",
        source_transport_task_id="source-move",
    )
    target = SimpleNamespace(
        workline_id=7,
        position_unknown=False,
        position_json={"kind": "RACK_POSITION", "location_code": "TRANSFER-POS"},
        arrival_face="270",
        source_transport_task_id="target-move",
    )
    projections = {"R1": source, "TARGET-1": target}

    class Positions:
        async def count(self, _db, *, where_clauses):
            return 1

        async def get(self, _db, _kind, rack_id):  # type: ignore[no-untyped-def]
            return projections.get(rack_id)

    transports = {
        "source-move": SimpleNamespace(status="SUCCEEDED"),
        "target-move": SimpleNamespace(status="SUCCEEDED"),
    }

    class TransportReader:
        async def get_task(self, _db, task_id):  # type: ignore[no-untyped-def]
            return transports.get(task_id)

    class Plans:
        async def list_active_bin_source_racks(self, _db, _task_id):  # type: ignore[no-untyped-def]
            return []

        async def list_bin_source_racks(self, _db, _task_id):  # type: ignore[no-untyped-def]
            return [SimpleNamespace(id=1, rack_id="R1", rack_face="90", source_evidence_id=11, plan_revision=1)]

        async def source_transport_matches(self, _db, *_args):  # type: ignore[no-untyped-def]
            return True

    class Flow:
        def __init__(self):
            self.calls = []

        async def advance_in_session(self, _db, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs)
            return True

    flow = Flow()
    flow.has_unclosed_action_for_face = AsyncMock(return_value=False)
    flow.face_progress = AsyncMock(return_value=None)
    driver = module.ManualPickingBatchDriver(
        flow,
        plans=Plans(),
        positions=Positions(),
        transports=TransportReader(),
        rack_creator=object(),
        departure_scheduler=object(),
        departure_reader=object(),
        passages=SimpleNamespace(ready_return_prefix_for_update=AsyncMock(return_value=())),
        bindings=SimpleNamespace(list_task_member_bindings=AsyncMock(return_value={(11, "R1")})),
    )
    line = SimpleNamespace(
        id=7,
        line_code="LINE-1",
        config={
            "position_bindings": {
                "FIVE_RACK": "FIVE_LAYER",
                "TRANSFER_RACK": "TRANSFER",
                "RETURN_RACK": "RETURN",
                "INLET": "IN",
                "OUTLET": "OUT",
            }
        },
        position_bindings={
            "FIVE_RACK": {"location_id": "FIVE-POS"},
            "TRANSFER_RACK": {"location_id": "TRANSFER-POS"},
            "INLET": {"location_id": "CNV0301"},
            "OUTLET": {"location_id": "CNV0302"},
        },
    )
    task = SimpleNamespace(
        id=31, task_id="PICK-1", plan_revision=1, status="EXECUTING", target_rack_id="TARGET-1", target_rack_face="270"
    )

    assert await driver.advance_in_session(object(), line, task) == 1
    assert flow.calls[0]["rack_id"] == "R1"
    assert flow.calls[0]["rack_face"] == "90"
    assert flow.calls[0]["return_location"] == "CNV0302"
    assert flow.calls[0]["inlet_location"] == "CNV0301"
    transports["source-move"] = SimpleNamespace(status="ACCEPTED")
    assert await driver.advance_in_session(object(), line, task) == 0
    assert len(flow.calls) == 1


@pytest.mark.asyncio
async def test_batch_driver_checks_return_buffer_after_inbound_before_creating_next_rack_action() -> None:
    module = import_module("manual_picking.application.batch_driver")

    class Flow:
        def __init__(self):
            self.calls = []

        async def has_unclosed_action_for_face(self, _db, *_args):  # type: ignore[no-untyped-def]
            return False

        async def face_progress(self, *_args):  # type: ignore[no-untyped-def]
            return SimpleNamespace(feed_complete=True)

        async def advance_in_session(self, _db, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs)
            return True

    class Plans:
        async def list_bin_source_racks(self, _db, _task_id):  # type: ignore[no-untyped-def]
            return [SimpleNamespace(id=1, rack_id="R1", rack_face="90", source_evidence_id=11, plan_revision=1)]

        async def source_transport_matches(self, _db, *_args):  # type: ignore[no-untyped-def]
            return True

    class Positions:
        async def count(self, _db, *, where_clauses):
            return 1

        async def get(self, _db, _kind, rack_id):  # type: ignore[no-untyped-def]
            return {
                "R1": SimpleNamespace(
                    workline_id=7,
                    position_unknown=False,
                    position_json={"kind": "RACK_POSITION", "location_code": "FIVE-POS"},
                    arrival_face="90",
                    source_transport_task_id="source-move",
                )
            }.get(rack_id)

    class Transports:
        async def get_task(self, _db, _task_id):  # type: ignore[no-untyped-def]
            return SimpleNamespace(status="SUCCEEDED")

    flow = Flow()
    driver = module.ManualPickingBatchDriver(
        flow,
        plans=Plans(),
        positions=Positions(),
        transports=Transports(),
        rack_creator=object(),
        departure_scheduler=object(),
        departure_reader=object(),
        passages=SimpleNamespace(
            ready_return_prefix_for_update=AsyncMock(return_value=(SimpleNamespace(bin_code="BIN-1"),))
        ),
    )
    line = SimpleNamespace(
        id=7,
        line_code="LINE-1",
        position_bindings={
            "FIVE_RACK": {"location_id": "FIVE-POS"},
            "INLET": {"location_id": "CNV0301"},
            "OUTLET": {"location_id": "CNV0302"},
        },
    )
    task = SimpleNamespace(
        id=31, task_id="PICK-1", plan_revision=1, status="EXECUTING", target_rack_id="TARGET-1", target_rack_face="270"
    )

    assert await driver._advance_current_rack(object(), line, task) == 1
    assert flow.calls[0]["allow_inbound"] is False


@pytest.mark.asyncio
async def test_completed_task_continues_return_fifo_without_target_rack() -> None:
    module = import_module("manual_picking.application.batch_driver")
    row = SimpleNamespace(task_id="PICK-1", plan_revision=1, return_state="READY")
    task = SimpleNamespace(
        id=31,
        task_id="PICK-1",
        plan_revision=1,
        workline_id=7,
        status="EXECUTION_COMPLETED",
        target_rack_id="TARGET-1",
        target_rack_face="270",
    )

    class Passages:
        async def unfinished_return_prefix_for_update(self, _db, _workline_id):  # type: ignore[no-untyped-def]
            return (row,)

    class Tasks:
        async def get_by_task_id_for_update(self, _db, task_id):  # type: ignore[no-untyped-def]
            assert task_id == "PICK-1"
            return task

    class Plans:
        async def list_bin_source_racks(self, _db, _task_id):  # type: ignore[no-untyped-def]
            return [SimpleNamespace(id=1, rack_id="R1", rack_face="90", source_evidence_id=11, plan_revision=1)]

        async def first_completed_source_owner_at_position(self, _db, *_args):  # type: ignore[no-untyped-def]
            return task

        async def first_completed_direct_pick_owner_at_position(self, _db, *_args):  # type: ignore[no-untyped-def]
            return None

        async def first_completed_transfer_owner_at_position(self, _db, *_args):  # type: ignore[no-untyped-def]
            return None

        async def source_transport_matches(self, _db, *_args):  # type: ignore[no-untyped-def]
            return True

    class Positions:
        async def count(self, _db, *, where_clauses):
            return 1

        async def get(self, _db, _kind, rack_id):  # type: ignore[no-untyped-def]
            if rack_id == "TARGET-1":
                return None
            return SimpleNamespace(
                object_id="R1",
                workline_id=7,
                position_unknown=False,
                position_json={"kind": "RACK_POSITION", "location_code": "FIVE-POS"},
                arrival_face="90",
                source_transport_task_id="source-move",
            )

    class TransportReader:
        async def get_task(self, _db, task_id):  # type: ignore[no-untyped-def]
            return SimpleNamespace(status="SUCCEEDED") if task_id == "source-move" else None

    class Flow:
        def __init__(self):
            self.calls = []

        async def advance_in_session(self, _db, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs)
            return True

    flow = Flow()
    flow.has_unclosed_action_for_face = AsyncMock(return_value=False)
    flow.face_progress = AsyncMock(return_value=None)
    driver = module.ManualPickingBatchDriver(
        flow,
        plans=Plans(),
        positions=Positions(),
        transports=TransportReader(),
        tasks=Tasks(),
        rack_creator=object(),
        departure_scheduler=object(),
        departure_reader=object(),
        passages=SimpleNamespace(ready_return_prefix_for_update=AsyncMock(return_value=())),
    )
    line = SimpleNamespace(
        id=7,
        line_code="LINE-1",
        position_bindings={
            "FIVE_RACK": {"location_id": "FIVE-POS"},
            "TRANSFER_RACK": {"location_id": "TRANSFER-POS"},
            "INLET": {"location_id": "CNV0301"},
            "OUTLET": {"location_id": "CNV0302"},
        },
    )

    assert await driver.advance_completed_in_session(object(), line) == 1
    assert flow.calls[0]["task_id"] == "PICK-1"
    assert flow.calls[0]["rack_id"] == "R1"


@pytest.mark.asyncio
@pytest.mark.parametrize("face_done", [False, True])
async def test_initial_feed_and_finished_face_do_not_start_opportunistic_return(face_done: bool) -> None:
    module = import_module("manual_picking.application.batch_flow")
    passages = SimpleNamespace(ready_return_prefix_for_update=AsyncMock(return_value=()))
    repo = _Repository(face_done=face_done)
    scheduler = _Scheduler()
    flow = module.ManualPickingBatchFlow(repo, passages, scheduler, _Inbound(), uuid_factory=lambda: "op-1")
    created = await flow.advance_in_session(
        object(),
        workline_id=7,
        workline_code="LINE-1",
        picking_task_id=31,
        task_id="PICK-1",
        plan_revision=1,
        rack_id="R1",
        rack_face="90",
        return_location="CNV0302",
        inlet_location="CNV0301",
        now=datetime(2026, 9, 15, 12),
    )
    assert created is (not face_done)
    assert len(scheduler.intents) == (0 if face_done else 1)
    if not face_done:
        assert type(scheduler.intents[0][0]) is sdk.BinInboundBatchIntent


@pytest.mark.asyncio
async def test_completed_return_check_resumes_feed_even_when_more_bins_are_ready() -> None:
    module = import_module("manual_picking.application.batch_flow")
    repository_module = import_module("manual_picking.application.batch_repository")
    created_at = datetime(2026, 9, 15, 12)
    now = created_at + timedelta(seconds=10)
    history = SimpleNamespace(latest_return=AsyncMock(return_value=None))
    repo = repository_module.BatchRepository(history)
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="feed-1", task_id="PICK-1", plan_revision=1, rack_id="R1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady(
        tuple(
            sdk.BinInboundBatchMember(f"BIN-{index}", sdk.TransportRackBinSlot("R1", "90", f"S-{index}"))
            for index in range(1, 6)
        )
    )
    repo.inbound_progress = AsyncMock(
        return_value=repository_module.InboundFaceProgress(
            intent,
            ready,
            31,
            4,
            False,
            False,
            created_at,
        )
    )
    repo.has_unclosed_action_for_face = AsyncMock(return_value=False)
    scheduler, inbound = _Scheduler(), _Inbound()
    flow = module.ManualPickingBatchFlow(
        repo, _Passages(("RETURN-1",)), scheduler, inbound, uuid_factory=lambda: "return-1"
    )
    kwargs = {
        "workline_id": 7,
        "workline_code": "LINE-1",
        "picking_task_id": 31,
        "task_id": "PICK-1",
        "plan_revision": 1,
        "rack_id": "R1",
        "rack_face": "90",
        "return_location": "CNV0302",
        "inlet_location": "CNV0301",
        "now": now,
    }
    assert await flow.advance_in_session(object(), **kwargs)
    assert len(scheduler.intents) == 1 and isinstance(scheduler.intents[0][0], sdk.BinReturnBatchIntent)
    assert inbound.calls == []
    history.latest_return.return_value = (
        sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1)),
        now + timedelta(seconds=1),
    )
    kwargs["now"] = now + timedelta(seconds=2)
    assert await flow.advance_in_session(object(), **kwargs)
    assert len(scheduler.intents) == 1
    assert len(inbound.calls) == 1 and inbound.calls[0]["offset"] == 4
