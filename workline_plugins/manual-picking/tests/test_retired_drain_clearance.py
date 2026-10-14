"""退役只停止排空，迟到的当前到位事实仍有独立清退 owner。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.batch_repository import BatchRepository
from manual_picking.application.drain_execution_model import ManualPickingDrainExecution
from manual_picking.application.drain_repository import DrainRepository
from test_batch_repository import _new_sessions
from test_scan_flow import _drain_rack_recovery
from test_source_progression import setup_driver
from test_transport_outcome import _outcome, _publisher

from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding
from src.app.execution.plugin_binding import BusinessEvidenceDisposition
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.models.plan_members import DirectPickExecution, PickingTaskBinSourceRack

CLEARANCE = "MANUAL_PICKING_RETIRED_DRAIN_RACK_OUT"
START = datetime(2026, 10, 14)


@pytest.mark.asyncio
@pytest.mark.parametrize("rack_id", ["510003", "510004"])
@pytest.mark.parametrize("inflight_status", ["PENDING", "ACCEPTED", "RECONCILING"])
async def test_retired_clearance_tracks_current_fact_and_yields_to_new_owner(rack_id, inflight_status):
    engine, sessions = await _new_sessions()
    async with engine.begin() as connection:
        for table in (ManualPickingDrainExecution.__table__, DirectPickExecution.__table__):
            await connection.run_sync(table.create)
    repository = DrainRepository(SimpleNamespace(for_operation=AsyncMock()))
    try:
        async with sessions.begin() as db:
            retired = ManualPickingDrainExecution(
                workline_id=7,
                drain_operation_id="old-drain",
                decision_evidence_id=31,
                status="EXCEPTION_RETIRED",
                closed_at=START + timedelta(minutes=20),
                reason_code="DRAIN_RESULT_OVERDUE",
                blocker_client_request_id="ingress",
                trigger_picking_task_id=11,
            )
            root = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=None,
                correlation_id="round",
                causal_token=100,
                step="MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN",
                resource_fence_id=rack_id,
                client_request_id="ingress",
                source_evidence_id=31,
                window_target_location_code="KT16",
                window_released_at=retired.closed_at,
                created_at=START,
            )
            ingress = TransportTask(
                transport_task_id="ingress",
                client_request_id="ingress",
                kind="RACK_MOVE",
                authority_workline_id=7,
                caller_json={"workline_id": "7"},
                request_json={"rack_id": rack_id},
                request_digest="a" * 64,
                submit_operation_id="submit",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="b" * 64,
                status="SUCCEEDED",
                outcome_version=1,
                published_outcome_version=1,
                created_at=START,
                updated_at=START,
            )
            position = PositionProjection(
                workline_id=7,
                object_type="RACK",
                object_id=rack_id,
                position_unknown=False,
                position_json={"kind": "RACK_POSITION", "location_code": "KT16"},
                arrival_face="270",
                source_operation_id="late-result",
                source_transport_task_id="ingress",
                source_causal_token=100,
                source_effect_phase="FINAL_RESULT",
            )
            old = TransportTask(
                **{
                    **ingress.model_dump(exclude={"id"}),
                    "transport_task_id": "old-accepted",
                    "client_request_id": "old-accepted",
                    "submit_operation_id": "old-submit",
                    "status": "ACCEPTED",
                    "outcome_version": 0,
                    "published_outcome_version": 0,
                    "created_at": START - timedelta(days=19),
                    "updated_at": START - timedelta(days=19),
                }
            )
            old_evidence = InboundEvidence(
                id=30,
                kind="WMS_RESULT",
                source_identity="old-departure-ready",
                payload_digest="c" * 64,
                normalized_payload={},
                operation="outbound.rack.departure_decide@v1",
                operation_id="old-departure",
                workline_id=7,
                apply_status="APPLIED",
                received_at=old.created_at,
                published_at=old.created_at,
                decision_digest="d" * 64,
            )
            old_binding = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=None,
                correlation_id="old-round-departure",
                causal_token=99,
                step="MANUAL_PICKING_SOURCE_RACK_OUT",
                resource_fence_id=rack_id,
                client_request_id="old-accepted",
                source_evidence_id=30,
                created_at=old.created_at,
            )
            db.add_all([retired, root, ingress, position, old, old_evidence, old_binding])
            await db.flush()

            async def contexts():
                return await repository.retired_clearance_racks(db, workline_id=7)

            assert (await contexts())[0] == (root, ingress, position)
            assert await BatchRepository().current_round_started_at(db, workline_id=7, rack_id=rack_id) == START
            # 原窗口已释放，当前轮次仍有明确的直接依赖范围。
            assert not await BatchRepository().has_unclosed_return_dependency_for_rack(
                db,
                workline_id=7,
                rack_id=rack_id,
                round_started_at=root.created_at,
            )
            with pytest.raises(ValueError, match="exactly one active"):
                await BatchRepository().has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id=rack_id)

            successor = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=None,
                correlation_id="clear",
                causal_token=101,
                step=CLEARANCE,
                resource_fence_id=rack_id,
                client_request_id="clear",
                source_evidence_id=31,
                created_at=START + timedelta(minutes=21),
            )
            current = TransportTask(
                **{
                    **ingress.model_dump(exclude={"id"}),
                    "transport_task_id": "clear",
                    "client_request_id": "clear",
                    "submit_operation_id": "clear",
                    "status": inflight_status,
                    "outcome_version": 0,
                    "published_outcome_version": 0,
                    "created_at": successor.created_at,
                }
            )
            db.add_all([successor, current])
            await db.flush()
            assert await contexts() == []
            current.status = "FAILED"
            current.outcome_version = current.published_outcome_version = 1
            await db.flush()
            assert (await contexts())[0][1] is current
            successor.source_evidence_id = 32
            await db.flush()
            assert (await contexts())[0][1] is ingress
            successor.source_evidence_id = 31

            new_task = PickingTask(
                id=11,
                workline_id=7,
                task_id="new-task",
                task_type="MANUAL",
                status="EXECUTING",
                issued_evidence_id=32,
                issued_at_ms=1,
                queue_revision=1,
                dispatch_sequence=1,
                last_applied_plan_revision=1,
                initial_plan_evidence_id=32,
                last_plan_evidence_id=32,
            )
            member = PickingTaskBinSourceRack(
                picking_task_id=11,
                plan_revision=1,
                rack_id=rack_id,
                rack_face="90",
                source_evidence_id=32,
            )
            db.add_all([new_task, member])
            await db.flush()
            assert len(await contexts()) == 1
            member.cancelled_evidence_id = 33
            await db.flush()
            assert len(await contexts()) == 1
            new_task.target_rack_id, new_task.target_rack_face = rack_id, "90"
            await db.flush()
            assert len(await contexts()) == 1
            new_task.target_rack_id = new_task.target_rack_face = None
            active = ManualPickingDrainExecution(workline_id=8, drain_operation_id="new-drain", decision_evidence_id=34)
            db.add(active)
            repository._reader.for_operation.return_value = SimpleNamespace(
                result=sdk.ReturnBufferDrainReady((sdk.RackFaceSequence(rack_id, ("90",)),)),
            )
            await db.flush()
            assert len(await contexts()) == 1
            repository._reader.for_operation.return_value = SimpleNamespace(
                result=sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("other", ("90",)),)),
            )
            assert len(await contexts()) == 1
            position.source_causal_token = 101
            position.source_transport_task_id = "clear"
            position.position_json = {"kind": "RACK_POSITION", "location_code": "WH01-001"}
            await db.flush()
            assert await contexts() == []
            assert retired.status == "EXCEPTION_RETIRED" and root.window_released_at == retired.closed_at
            assert old.status == "ACCEPTED" and old.outcome_version == old.published_outcome_version == 0
            assert old.client_request_id == "old-accepted" and old.submit_request_body == "{}"
            assert old_binding.causal_token == 99 and old_binding.source_evidence_id == 30
            assert await db.get(InboundEvidence, 30) is old_evidence
            assert old_evidence.source_identity == "old-departure-ready" and old_evidence.payload_digest == "c" * 64
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("hold", [None, "PENDING", "ACCEPTED", "RECONCILING", "unpublished", "bin", "batch"])
async def test_retired_clearance_scanner_is_independent_and_preserves_direct_dependencies(hold):
    driver, line, *_ = setup_driver()
    root = SimpleNamespace(
        resource_fence_id="510003", source_evidence_id=31, created_at=START, window_target_location_code="KT16"
    )
    current = SimpleNamespace(
        status="SUCCEEDED" if hold not in {"PENDING", "ACCEPTED", "RECONCILING"} else hold,
        transport_task_id="ingress",
        outcome_version=1,
        published_outcome_version=0 if hold == "unpublished" else 1,
    )
    driver._drain = SimpleNamespace(
        repository=SimpleNamespace(
            retired_clearance_racks=AsyncMock(return_value=[(root, current, SimpleNamespace(arrival_face="270"))])
        )
    )
    driver._batches.has_current_rack_dependency.return_value = hold == "bin"
    driver._batches.has_unclosed_return_dependency_for_rack.return_value = hold == "batch"
    count = await driver.project_exits_in_session(object(), line)
    assert count == (1 if hold is None else 0)
    if hold is None:
        request = driver._departure_scheduler.create_in_session.await_args.args[1]
        assert request.task_id is None and request.rack_id == "510003"
        assert request.current_location.location_code == "KT16" and request.current_face == "270"
        assert (
            driver._batches.has_unclosed_return_dependency_for_rack.await_args_list[0].kwargs["round_started_at"]
            == START
        )
    else:
        driver._departure_scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_clearance_cancelled_result_is_consumed_for_scanner_without_old_drain_retry():
    flow, execution, _, _ = _drain_rack_recovery("EXCEPTION_RETIRED", step=CLEARANCE)
    result = await flow.apply_in_session(object(), 10, workline_id=7)
    assert result.disposition is BusinessEvidenceDisposition.APPLIED
    assert execution.status == "EXCEPTION_RETIRED"
    flow._rack_creator.create.assert_not_awaited()
    flow._drains.current.assert_not_awaited()


@pytest.mark.asyncio
async def test_clearance_result_is_published_from_frozen_drain_fact():
    publisher, accept = _publisher(step=CLEARANCE)
    publisher._drain_reader = SimpleNamespace(
        read=AsyncMock(
            return_value=(
                SimpleNamespace(operation_id="old-drain"),
                SimpleNamespace(result=sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("RACK-1", ("90",)),))),
            )
        )
    )
    assert await publisher.publish(object(), _outcome())
    payload = accept.await_args.kwargs["normalized_payload"]
    assert payload["step"] == CLEARANCE and payload["drain_operation_id"] == "old-drain"
    assert payload["source_evidence_id"] == 101 and payload["rack_id"] == "RACK-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous_step,status",
    [
        (None, None),
        (CLEARANCE, "FAILED"),
        (CLEARANCE, "REJECTED"),
        ("MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT", "FAILED"),
    ],
)
async def test_clearance_ready_creates_independent_action_and_terminal_failure_redecides(previous_step, status):
    driver, line, *_ = setup_driver()
    creator = driver._rack_creator
    driver._drain = SimpleNamespace(
        repository=SimpleNamespace(
            retired_clearance_racks=AsyncMock(
                return_value=[
                    (
                        SimpleNamespace(
                            resource_fence_id="510003",
                            source_evidence_id=31,
                            created_at=START,
                            window_target_location_code="KT16",
                        ),
                        SimpleNamespace(
                            status="SUCCEEDED" if status is None else status,
                            transport_task_id="current",
                            outcome_version=1,
                            published_outcome_version=1,
                        ),
                        SimpleNamespace(arrival_face="270"),
                    )
                ]
            )
        )
    )
    driver._departure_reader.latest_for_workline.return_value = SimpleNamespace(
        intent=SimpleNamespace(task_id=None, rack_id="510003", operation_id="departure-op"),
        status="COMPLETED",
        evidence_id=41,
        outcome=SimpleNamespace(result=sdk.RackDepartureReady(sdk.TransportZonePosition("WH01"))),
    )
    if status is not None:
        driver._transports.get_task.return_value = SimpleNamespace(created_at=START + timedelta(minutes=21))
        consumed_ready = driver._departure_reader.latest_for_workline.return_value

        async def only_newer_decision(_db, _workline, _rack, *, arrived_at):
            # 原 READY 早于已终态动作；新步骤不能用新 step 身份重复消费它。
            assert previous_step is not None
            assert arrived_at == START + timedelta(minutes=21)
            return consumed_ready if arrived_at <= START else None

        driver._batches.current_rack_arrival_at.return_value = START + timedelta(minutes=21)
        driver._departure_reader.latest_for_workline.side_effect = only_newer_decision
    assert await driver.project_exits_in_session(object(), line) == 1
    if status is None:
        assert creator.depart[0]["step"] == CLEARANCE and creator.depart[0]["picking_task_id"] is None
        assert creator.depart[0]["source_evidence_id"] == 31
        assert creator.depart[0]["rcs_template_id"] is sdk.TransportRcsTemplateId.CTU03
    else:
        assert creator.depart == []
        assert driver._departure_scheduler.create_in_session.await_args.args[1].task_id is None
