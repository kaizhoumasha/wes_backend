"""退料货架直接取料按权威在位面、WMS 到位事实和面级完成事实推进。"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.batch_driver import ManualPickingBatchDriver
from manual_picking.application.drain_repository import RETURN_RACK_OUT_STEP, RETURN_RACK_ROTATE_STEP

from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.utils.timezone import timezone


class Positions:
    def __init__(self):  # type: ignore[no-untyped-def]
        self.current = SimpleNamespace(
            object_id="RETURN-RACK-01",
            workline_id=7,
            position_unknown=False,
            position_json={"kind": "RACK_POSITION", "location_code": "RETURN-POS"},
            arrival_face="A",
            source_transport_task_id="return-arrival-1",
            updated_at=timezone.now_for_db(),
        )

    async def count(self, _db, *, where_clauses):  # type: ignore[no-untyped-def]
        return 1

    async def get(self, _db, _kind, rack_id):  # type: ignore[no-untyped-def]
        return self.current if self.current is not None and rack_id == self.current.object_id else None


class Plans:
    def __init__(self):  # type: ignore[no-untyped-def]
        self.picks = [
            SimpleNamespace(id=21, rack_id="RETURN-RACK-01", rack_face="A", slot_id="A-03", source_evidence_id=61),
            SimpleNamespace(id=22, rack_id="RETURN-RACK-01", rack_face="A", slot_id="A-04", source_evidence_id=61),
        ]
        self.completed: set[tuple[str, str]] = set()

    async def list_active_direct_picks(self, _db, _task_id):  # type: ignore[no-untyped-def]
        return self.picks

    async def has_direct_pick_face_completion(self, _db, *, picking_task_id, rack_id, rack_face):  # type: ignore[no-untyped-def]
        assert picking_task_id == 31
        return (rack_id, rack_face) in self.completed

    async def list_bin_source_racks(self, _db, _task_id):  # type: ignore[no-untyped-def]
        return []

    async def list_active_bin_source_racks(self, _db, _task_id):  # type: ignore[no-untyped-def]
        return []

    async def source_transport_matches(self, _db, *_args):  # type: ignore[no-untyped-def]
        return True


class Creator:
    def __init__(self):  # type: ignore[no-untyped-def]
        self.rotate = []
        self.depart = []
        self.created = []

    async def create(self, _db, **kwargs):  # type: ignore[no-untyped-def]
        self.created.append(kwargs)

    async def create_rotate(self, _db, **kwargs):  # type: ignore[no-untyped-def]
        self.rotate.append(kwargs)

    async def create_source_return(self, _db, **kwargs):  # type: ignore[no-untyped-def]
        self.depart.append(kwargs)


def setup_driver():  # type: ignore[no-untyped-def]
    line = SimpleNamespace(
        id=7,
        line_code="LINE-1",
        config={
            "position_bindings": {
                "FIVE_RACK": "FIVE_LAYER",
                "RETURN_RACK": "RETURN",
                "TRANSFER_RACK": "TRANSFER",
                "INLET": "IN",
                "OUTLET": "OUT",
            }
        },
        position_bindings={
            "FIVE_RACK": {"location_id": "FIVE-POS"},
            "RETURN_RACK": {"location_id": "RETURN-POS"},
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
        last_applied_plan_revision=1,
    )
    positions, plans, creator = Positions(), Plans(), Creator()
    arrival_reader = SimpleNamespace(latest=AsyncMock(return_value=None))
    arrival_scheduler = SimpleNamespace(create_in_session=AsyncMock())
    departure_reader = SimpleNamespace(
        latest=AsyncMock(return_value=None), latest_for_workline=AsyncMock(return_value=None)
    )
    departure_scheduler = SimpleNamespace(create_in_session=AsyncMock())
    driver = ManualPickingBatchDriver(
        SimpleNamespace(
            has_unclosed_action_for_face=AsyncMock(return_value=False),
        ),
        plans=plans,
        positions=positions,
        transports=SimpleNamespace(
            get_task=AsyncMock(
                return_value=SimpleNamespace(
                    status="SUCCEEDED", published_outcome_version=3, created_at=timezone.now_for_db()
                )
            )
        ),
        rack_creator=creator,
        departure_scheduler=departure_scheduler,
        departure_reader=departure_reader,
        arrival_scheduler=arrival_scheduler,
        arrival_reader=arrival_reader,
        tasks=SimpleNamespace(get_by_task_id_for_update=AsyncMock(return_value=task)),
        bindings=SimpleNamespace(list_task_resource_fence_ids=AsyncMock(return_value=set())),
        uuid_factory=lambda: "019f3405-2200-7b01-8b01-000000000009",
    )
    return driver, line, task, positions, plans, creator, arrival_reader, arrival_scheduler, departure_scheduler


def recorded_snapshot(rack_id="RETURN-RACK-01"):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        status=WmsConfirmationStatus.COMPLETED,
        intent=sdk.wms_operations.outbound_return_rack_arrival_report(
            operation_id="019f3405-2200-7b01-8b01-000000000009",
            task_id="PICK-1",
            transport_task_id="return-arrival-1",
            outcome_revision=3,
            rack_id=rack_id,
            final_position=sdk.TransportRackPosition("RETURN-POS"),
            arrival_face="A",
        ),
        outcome=sdk.ReturnRackArrivalReportOutcome(sdk.FactRecorded(False)),
        evidence_id=91,
        completed_at=timezone.now_for_db(),
    )


@pytest.mark.asyncio
async def test_present_return_rack_reports_arrival_once_before_any_departure() -> None:
    driver, line, task, _, _, creator, _, scheduler, departure = setup_driver()

    assert await driver.advance_in_session(object(), line, task) == 1
    scheduler.create_in_session.assert_awaited_once()
    intent = scheduler.create_in_session.await_args.args[1]
    assert intent.rack_id == "RETURN-RACK-01"
    assert intent.task_id == "PICK-1"
    assert intent.transport_task_id == "return-arrival-1"
    assert intent.outcome_revision == 3
    assert intent.final_position == sdk.TransportRackPosition("RETURN-POS")
    assert intent.arrival_face == "A"
    assert scheduler.create_in_session.await_args.kwargs["picking_task_id"] == 31
    assert creator.rotate == [] and creator.depart == []
    departure.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_absent_return_rack_never_reports_arrival() -> None:
    driver, line, task, positions, _, creator, _, scheduler, _ = setup_driver()
    positions.current = None

    assert await driver.advance_in_session(object(), line, task) == 0
    scheduler.create_in_session.assert_not_awaited()
    assert creator.rotate == [] and creator.depart == []


@pytest.mark.asyncio
async def test_recorded_arrival_waits_for_the_face_completion_fact() -> None:
    driver, line, task, _, _, creator, reader, scheduler, departure = setup_driver()
    reader.latest.return_value = recorded_snapshot()

    assert await driver.advance_in_session(object(), line, task) == 0
    scheduler.create_in_session.assert_not_awaited()
    assert creator.rotate == [] and creator.depart == []
    departure.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_single_face_requests_workline_owned_departure() -> None:
    driver, line, task, _, plans, creator, reader, _, departure = setup_driver()
    plans.picks = plans.picks[:1]
    reader.latest.return_value = recorded_snapshot()
    plans.completed.add(("RETURN-RACK-01", "A"))

    assert await driver.advance_in_session(object(), line, task) == 1
    departure.create_in_session.assert_awaited_once()
    intent = departure.create_in_session.await_args.args[1]
    assert intent.rack_id == "RETURN-RACK-01"
    assert intent.task_id is None
    assert intent.current_location == sdk.TransportRackPosition("RETURN-POS")
    assert intent.current_face == "A"
    assert departure.create_in_session.await_args.kwargs["workline_id"] == 7
    assert creator.rotate == []


@pytest.mark.asyncio
async def test_completed_face_rotates_to_the_next_pending_face_on_the_same_rack() -> None:
    driver, line, task, _, plans, creator, reader, _, departure = setup_driver()
    plans.picks.append(
        SimpleNamespace(id=23, rack_id="RETURN-RACK-01", rack_face="B", slot_id="B-01", source_evidence_id=62)
    )
    reader.latest.return_value = recorded_snapshot()
    plans.completed.add(("RETURN-RACK-01", "A"))

    assert await driver.advance_in_session(object(), line, task) == 1
    assert creator.rotate[0]["step"] == RETURN_RACK_ROTATE_STEP
    assert creator.rotate[0]["rack_id"] == "RETURN-RACK-01"
    assert creator.rotate[0]["target_face"] == "B"
    assert creator.rotate[0]["source_evidence_id"] == 62
    assert creator.rotate[0]["position"] == sdk.TransportRackPosition("RETURN-POS")
    assert creator.rotate[0]["correlation_id"] == "pt:31:return-face:23"
    departure.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_ready_departure_decision_returns_the_return_rack_with_its_own_step() -> None:
    driver, line, task, _, plans, creator, reader, _, departure = setup_driver()
    plans.picks = plans.picks[:1]
    plans.completed.add(("RETURN-RACK-01", "A"))
    reader.latest.return_value = recorded_snapshot()
    driver._departure_reader.latest_for_workline.return_value = SimpleNamespace(
        intent=sdk.RackDepartureIntent(
            operation_id="019f3405-2200-7b01-8b01-000000000009",
            task_id=None,
            rack_id="RETURN-RACK-01",
            current_location=sdk.TransportRackPosition("RETURN-POS"),
            current_face="A",
        ),
        status=WmsConfirmationStatus.COMPLETED,
        outcome=sdk.RackDepartureOutcome(sdk.RackDepartureReady(sdk.TransportZonePosition("WH05"))),
        evidence_id=93,
        completed_at=timezone.now_for_db(),
    )

    assert await driver.advance_in_session(object(), line, task) == 1
    assert creator.depart[0]["step"] == RETURN_RACK_OUT_STEP
    assert creator.depart[0]["rack_id"] == "RETURN-RACK-01"
    assert creator.depart[0]["picking_task_id"] == 31
    assert creator.depart[0]["source_evidence_id"] == 93
    assert creator.depart[0]["correlation_id"] == "pt:31:return-out:RETURN-RACK-01"
    assert creator.depart[0]["destination"] == sdk.TransportZonePosition("WH05")
    departure.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_arrival_on_a_later_face_still_rotates_back_to_the_earlier_unfinished_face() -> None:
    """到位面由外部搬运决定，不保证是计划首面；更早的未结面不能被跳过。"""
    driver, line, task, positions, plans, creator, reader, _, departure = setup_driver()
    plans.picks.append(
        SimpleNamespace(id=23, rack_id="RETURN-RACK-01", rack_face="B", slot_id="B-01", source_evidence_id=62)
    )
    positions.current.arrival_face = "B"
    reader.latest.return_value = recorded_snapshot()
    plans.completed.add(("RETURN-RACK-01", "B"))

    assert await driver.advance_in_session(object(), line, task) == 1
    assert creator.rotate[0]["target_face"] == "A"
    assert creator.rotate[0]["source_evidence_id"] == 61
    assert creator.rotate[0]["correlation_id"] == "pt:31:return-face:21"
    assert creator.depart == []
    departure.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_five_rack_subflow_does_not_block_the_return_rack_subflow() -> None:
    driver, line, task, positions, plans, _, _, scheduler, _ = setup_driver()
    plans.list_bin_source_racks = AsyncMock(  # type: ignore[method-assign]
        return_value=[SimpleNamespace(id=11, rack_id="R1", rack_face="90", source_evidence_id=51)]
    )
    driver._flow.has_unclosed_action_for_face.return_value = True  # 当前货架面停在未闭合动作上。
    positions.count = AsyncMock(return_value=1)

    assert await driver.advance_in_session(object(), line, task) == 1
    scheduler.create_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_unbound_return_rack_position_leaves_the_five_rack_subflow_untouched() -> None:
    driver, line, task, _, _, creator, _, scheduler, _ = setup_driver()
    del line.position_bindings["RETURN_RACK"]

    assert await driver.advance_in_session(object(), line, task) == 0
    scheduler.create_in_session.assert_not_awaited()
    assert creator.rotate == [] and creator.depart == []


@pytest.mark.asyncio
async def test_five_rack_admission_and_return_rack_arrival_progress_together_in_one_call() -> None:
    """合同 §1.1 示例场景：任务同时含五层架来源与 RETURN-RACK-01 直接取料，两条子流程物理上
    并行、互不阻塞；一次 advance_in_session 必须同时推进子流程 A（五层架进场）与子流程 B
    （退料货架到位上报），而不是其中一条阻塞另一条。"""
    driver, line, task, _, plans, creator, _, arrival_scheduler, departure = setup_driver()
    driver._bindings = SimpleNamespace(list_task_resource_fence_ids=AsyncMock(return_value=set()))
    plans.list_active_bin_source_racks = AsyncMock(  # type: ignore[method-assign]
        return_value=[SimpleNamespace(rack_id="R1", rack_face="90", source_evidence_id=51, plan_revision=1)]
    )

    assert await driver.advance_in_session(object(), line, task) == 2

    assert len(creator.created) == 1
    assert creator.created[0]["intent"].rack_id == "R1"
    assert creator.created[0]["resource_fence_id"] == "R1"
    arrival_scheduler.create_in_session.assert_awaited_once()
    return_intent = arrival_scheduler.create_in_session.await_args.args[1]
    assert return_intent.rack_id == "RETURN-RACK-01"
    departure.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_arrival_identity_drift_is_logged_and_never_rolls_back_the_shared_transaction(caplog) -> None:  # type: ignore[no-untyped-def]
    """两条子流程共用事务，身份漂移抛异常会回滚五层架子流程本拍的成果，因此只记录并让位。"""
    driver, line, task, _, _, creator, reader, scheduler, departure = setup_driver()
    reader.latest.return_value = recorded_snapshot(rack_id="OTHER-RACK")

    with caplog.at_level(logging.ERROR, logger="manual_picking.application.batch_driver"):
        assert await driver.advance_in_session(object(), line, task) == 0

    assert "manual_picking.return_rack_arrival_identity_drift" in caplog.text
    scheduler.create_in_session.assert_not_awaited()
    departure.create_in_session.assert_not_awaited()
    assert creator.rotate == [] and creator.depart == []


@pytest.mark.asyncio
async def test_rejected_arrival_result_returns_zero_with_a_greppable_warning(caplog) -> None:  # type: ignore[no-untyped-def]
    driver, line, task, _, _, creator, reader, scheduler, departure = setup_driver()
    snapshot = recorded_snapshot()
    snapshot.outcome = sdk.ReturnRackArrivalReportOutcome(sdk.OperationRejected("INVALID_DATA"))
    reader.latest.return_value = snapshot

    with caplog.at_level(logging.WARNING, logger="manual_picking.application.batch_driver"):
        assert await driver.advance_in_session(object(), line, task) == 0

    assert "manual_picking.return_rack_arrival_not_recorded" in caplog.text
    scheduler.create_in_session.assert_not_awaited()
    departure.create_in_session.assert_not_awaited()
    assert creator.rotate == [] and creator.depart == []


@pytest.mark.asyncio
async def test_reconciling_confirmation_warns_because_the_rack_will_never_leave_on_its_own(caplog) -> None:  # type: ignore[no-untyped-def]
    driver, line, task, _, _, _, reader, scheduler, _ = setup_driver()
    snapshot = recorded_snapshot()
    snapshot.status = WmsConfirmationStatus.RECONCILING
    reader.latest.return_value = snapshot

    with caplog.at_level(logging.WARNING, logger="manual_picking.application.batch_driver"):
        assert await driver.advance_in_session(object(), line, task) == 0

    assert "manual_picking.return_rack_arrival_stuck" in caplog.text
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_confirmation_stays_quiet_because_it_is_still_normally_in_flight(caplog) -> None:  # type: ignore[no-untyped-def]
    driver, line, task, _, _, _, reader, _, _ = setup_driver()
    snapshot = recorded_snapshot()
    snapshot.status = WmsConfirmationStatus.PENDING
    reader.latest.return_value = snapshot

    with caplog.at_level(logging.WARNING, logger="manual_picking.application.batch_driver"):
        assert await driver.advance_in_session(object(), line, task) == 0

    assert caplog.text == ""


@pytest.mark.asyncio
async def test_direct_pick_only_completed_task_still_advances_its_return_rack() -> None:
    """合同 §5.5 允许只含直接取料的任务；完成后五层架来源查询找不到它，退料货架不能因此永久滞留。"""
    driver, line, task, _, plans, _, _, scheduler, _ = setup_driver()
    task.status = "EXECUTION_COMPLETED"
    plans.first_completed_source_owner_at_position = AsyncMock(return_value=None)  # type: ignore[method-assign]
    plans.first_completed_direct_pick_owner_at_position = AsyncMock(return_value=task)  # type: ignore[method-assign]
    plans.first_completed_transfer_owner_at_position = AsyncMock(return_value=None)  # type: ignore[method-assign]

    assert await driver.advance_completed_in_session(object(), line) == 1
    scheduler.create_in_session.assert_awaited_once()
    assert scheduler.create_in_session.await_args.args[1].rack_id == "RETURN-RACK-01"


@pytest.mark.asyncio
async def test_completed_task_found_by_both_lookups_advances_its_return_rack_only_once() -> None:
    driver, line, task, _, plans, _, _, scheduler, _ = setup_driver()
    task.status = "EXECUTION_COMPLETED"
    plans.first_completed_source_owner_at_position = AsyncMock(return_value=task)  # type: ignore[method-assign]
    plans.first_completed_direct_pick_owner_at_position = AsyncMock(return_value=task)  # type: ignore[method-assign]
    plans.first_completed_transfer_owner_at_position = AsyncMock(return_value=None)  # type: ignore[method-assign]

    assert await driver.advance_completed_in_session(object(), line) == 1
    scheduler.create_in_session.assert_awaited_once()
