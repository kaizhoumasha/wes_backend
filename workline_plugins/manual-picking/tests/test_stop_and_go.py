"""停靠只服务当前轮次，业务取消与面向不足不能丢失清退 owner。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.batch_repository import BatchRepository
from test_batch_repository import _new_sessions
from test_source_progression import setup_driver

from src.app.execution.models import PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.transport.models import TransportEvidence, TransportTask
from src.app.wms_integration.outbound_picking.models.plan_members import DirectPickExecution, DirectPickFaceCompletion
from src.app.wms_integration.return_rack_task_completion.models import ReturnRackTaskCompletion

START = datetime(2026, 10, 14)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PENDING", "ACCEPTED", "RECONCILING", "FAILED", "SUCCEEDED", "REJECTED"])
async def test_cancelled_return_rack_has_independent_exit_owner(status):
    driver, line, *_ = setup_driver()
    line.position_bindings["RETURN_RACK"] = {"location_id": "RETURN-POS"}
    window = SimpleNamespace(
        resource_fence_id="R1",
        picking_task_id=31,
        source_evidence_id=51,
        created_at=START,
        window_target_location_code="RETURN-POS",
    )
    driver._batches.cancelled_return_windows = AsyncMock(
        return_value=[
            (
                window,
                SimpleNamespace(
                    status=status, outcome_version=1, published_outcome_version=1, transport_task_id="current"
                ),
                SimpleNamespace(arrival_face=None),
            )
        ]
    )
    assert await driver.project_exits_in_session(object(), line) == (
        1 if status in {"FAILED", "SUCCEEDED", "REJECTED"} else 0
    )
    if status in {"FAILED", "SUCCEEDED", "REJECTED"}:
        intent = driver._departure_scheduler.create_in_session.await_args.args[1]
        assert intent.task_id is None and intent.current_face is None
        assert intent.current_location.location_code == "RETURN-POS"
    else:
        driver._departure_scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_motion_dependencies_keep_ingress_round_after_rotation():
    driver, line, *_ = setup_driver()
    driver._batches.current_round_started_at = AsyncMock(return_value=START)
    driver._transports.get_task.return_value = SimpleNamespace(created_at=START + timedelta(hours=1))
    await driver._return_before_rack_motion(object(), line, "R1", "90", START + timedelta(hours=2))
    assert driver._batches.has_current_rack_dependency.await_args.args[-1] == START
    assert driver._batches.has_unclosed_return_dependency_for_rack.await_args.kwargs["round_started_at"] == START


@pytest.mark.asyncio
async def test_cancelled_return_departure_freezes_departure_evidence_and_f01():
    driver, line, *_ = setup_driver()
    line.position_bindings["RETURN_RACK"] = {"location_id": "RETURN-POS"}
    driver._batches.cancelled_return_windows.return_value = [
        (
            SimpleNamespace(
                resource_fence_id="R1",
                picking_task_id=31,
                source_evidence_id=51,
                created_at=START,
                window_target_location_code="RETURN-POS",
            ),
            SimpleNamespace(
                status="SUCCEEDED", transport_task_id="current", outcome_version=1, published_outcome_version=1
            ),
            SimpleNamespace(arrival_face="90"),
        )
    ]
    driver._departure_reader.latest_for_workline.return_value = SimpleNamespace(
        status="COMPLETED",
        intent=SimpleNamespace(task_id=None, rack_id="R1", operation_id="depart"),
        evidence_id=41,
        outcome=SimpleNamespace(result=sdk.RackDepartureReady(sdk.TransportZonePosition("WH01"))),
    )
    assert await driver.project_exits_in_session(object(), line) == 1
    action = driver._rack_creator.depart[0]
    assert action["step"] == "MANUAL_PICKING_RETURN_RACK_OUT"
    assert action["source_evidence_id"] == 41 and action["rcs_template_id"] is sdk.TransportRcsTemplateId.F01


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["bin", "batch"])
async def test_null_face_departure_cannot_skip_current_dependency(dependency):
    driver, line, *_ = setup_driver()
    driver._batches.has_current_rack_dependency.return_value = dependency == "bin"
    driver._batches.has_unclosed_return_dependency_for_rack.return_value = dependency == "batch"
    assert (
        await driver._advance_workline_departure(
            object(),
            line,
            rack_id="R1",
            current_face=None,
            step="MANUAL_PICKING_SOURCE_RACK_OUT",
            picking_task_id=31,
            arrival_transport_task_id="current",
            now=START,
            round_started_at=START,
        )
        == 0
    )
    driver._departure_scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_face_gate_keeps_current_request_but_not_previous_round():
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            db.add(
                TransportDecisionBinding(
                    workline_id=7,
                    picking_task_id=31,
                    correlation_id="round",
                    causal_token=100,
                    step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                    resource_fence_id="R1",
                    client_request_id="ingress",
                    source_evidence_id=51,
                    window_target_location_code="KT16",
                    created_at=START,
                )
            )
            request = WmsConfirmation(
                workline_id=7,
                operation="outbound.bin.return_batch@v1",
                operation_id="old-request",
                request_digest="a" * 64,
                request_payload={"data": {"rack_id": "R1", "rack_face": "90"}},
                deadline_at=START,
                status="RECONCILING",
                created_at=START - timedelta(days=1),
            )
            db.add(request)
            await db.flush()
            repo = BatchRepository()
            assert not await repo.has_unclosed_action_for_face(db, 7, None, None, "R1", "90")
            request.created_at = START
            await db.flush()
            assert await repo.has_unclosed_action_for_face(db, 7, None, None, "R1", "90")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PENDING", "ACCEPTED", "RECONCILING", "SUCCEEDED", "FAILED"])
async def test_new_rack_motion_blocks_exit_before_ack_changes_projection(status):
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            task = TransportTask(
                transport_task_id="new-motion",
                client_request_id="new-motion",
                kind="RACK_MOVE",
                authority_workline_id=8,
                request_json={"rack_id": "R1"},
                caller_json={"workline_id": "8"},
                request_digest="a" * 64,
                submit_operation_id="new-motion",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="b" * 64,
                status=status,
                outcome_version=1,
                published_outcome_version=0,
                created_at=START,
                updated_at=START,
            )
            db.add(task)
            await db.flush()
            repo = BatchRepository()
            assert await repo.has_current_rack_dependency(db, 7, "R1", START)
            task.status = "SUCCEEDED"
            task.published_outcome_version = 1
            await db.flush()
            assert not await repo.has_current_rack_dependency(db, 7, "R1", START)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_released_round_requires_frozen_departure_identity():
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            root = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=31,
                correlation_id="in",
                causal_token=100,
                step="PICKING_TASK_RETURN_RACK_IN",
                resource_fence_id="R1",
                client_request_id="in",
                source_evidence_id=51,
                window_target_location_code="RETURN-POS",
                created_at=START,
                window_released_at=START,
                window_departure_client_request_id="out",
            )
            original = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=31,
                correlation_id="out",
                causal_token=101,
                step="MANUAL_PICKING_RETURN_RACK_OUT",
                resource_fence_id="R1",
                client_request_id="out",
                source_evidence_id=61,
                created_at=START,
            )
            current = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=31,
                correlation_id="retry:1:out",
                causal_token=102,
                step="MANUAL_PICKING_RETURN_RACK_OUT",
                resource_fence_id="R1",
                client_request_id="retry",
                source_evidence_id=61,
                created_at=START,
            )
            position = PositionProjection(
                object_type="RACK",
                object_id="R1",
                workline_id=7,
                position_unknown=False,
                position_json={"kind": "RACK_POSITION", "location_code": "RETURN-POS"},
                source_operation_id="final",
                source_transport_task_id="retry",
                source_causal_token=102,
                source_effect_phase="FINAL_RESULT",
            )
            db.add_all([root, original, current, position])
            await db.flush()
            repo = BatchRepository()
            assert await repo.current_round_started_at(db, workline_id=7, rack_id="R1") == START
            current.source_evidence_id = 62
            await db.flush()
            with pytest.raises(ValueError, match="current physical round"):
                await repo.current_round_started_at(db, workline_id=7, rack_id="R1")
            current.source_evidence_id = 61
            current.step = "MANUAL_PICKING_SOURCE_RACK_OUT"
            await db.flush()
            with pytest.raises(ValueError, match="current physical round"):
                await repo.current_round_started_at(db, workline_id=7, rack_id="R1")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_return_window_requires_members_and_actual_owner():
    engine, sessions = await _new_sessions()
    async with engine.begin() as connection:
        for table in (
            DirectPickExecution.__table__,
            DirectPickFaceCompletion.__table__,
            ReturnRackTaskCompletion.__table__,
        ):
            await connection.run_sync(table.create)
    try:
        async with sessions.begin() as db:
            root = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=31,
                correlation_id="return-round",
                causal_token=100,
                step="PICKING_TASK_RETURN_RACK_IN",
                resource_fence_id="R1",
                client_request_id="ingress",
                source_evidence_id=51,
                window_target_location_code="RETURN-POS",
                created_at=START,
            )
            member = DirectPickExecution(
                picking_task_id=31, plan_revision=2, rack_id="R1", rack_face="90", slot_id="S1", source_evidence_id=51
            )
            task = TransportTask(
                transport_task_id="ingress",
                client_request_id="ingress",
                kind="RACK_MOVE",
                authority_workline_id=7,
                request_json={"rack_id": "R1"},
                caller_json={"workline_id": "7"},
                request_digest="a" * 64,
                submit_operation_id="submit",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="b" * 64,
                status="FAILED",
                outcome_version=1,
                published_outcome_version=1,
                created_at=START,
                updated_at=START,
            )
            position = PositionProjection(
                workline_id=7,
                object_type="RACK",
                object_id="R1",
                position_json={"kind": "RACK_POSITION", "location_code": "RETURN-POS"},
                position_unknown=False,
                source_operation_id="final",
                source_transport_task_id="ingress",
                source_causal_token=100,
                source_effect_phase="FINAL_RESULT",
            )
            db.add_all([root, member, task, position])
            await db.flush()
            repo = BatchRepository()

            async def contexts():
                return await repo.cancelled_return_windows(db, workline_id=7, target_location_code="RETURN-POS")

            assert await contexts() == []
            member.cancelled_evidence_id = 91
            await db.flush()
            assert (await contexts())[0] == (root, task, position)
            db.add_all(
                [
                    DirectPickExecution(
                        picking_task_id=31,
                        plan_revision=1,
                        rack_id="R1",
                        rack_face="270",
                        slot_id="OLD",
                        source_evidence_id=50,
                    ),
                    ReturnRackTaskCompletion(
                        picking_task_id=31,
                        rack_id="R1",
                        through_plan_revision=1,
                        operation_id="old-completion",
                        completed_at_ms=1,
                        source_evidence_id=90,
                    ),
                ]
            )
            await db.flush()
            assert len(await contexts()) == 1
            physical = TransportEvidence(
                operation_id="final",
                transport_task_id="ingress",
                operation="transport.task.resulted@v1",
                outcome_revision=1,
                event_timestamp_ms=1,
                message_digest="b" * 64,
                payload_json={},
                ack_timestamp_ms=1,
                ack_data_json={},
                status="APPLIED",
                received_at=START,
            )
            rejected_binding = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=31,
                correlation_id="out",
                causal_token=101,
                step="MANUAL_PICKING_RETURN_RACK_OUT",
                resource_fence_id="R1",
                client_request_id="out",
                source_evidence_id=61,
                created_at=START + timedelta(minutes=1),
            )
            rejected = TransportTask(
                **{
                    **task.model_dump(exclude={"id"}),
                    "transport_task_id": "out",
                    "client_request_id": "out",
                    "submit_operation_id": "out",
                    "status": "REJECTED",
                    "created_at": START + timedelta(minutes=1),
                }
            )
            root.window_departure_client_request_id = "out"
            db.add_all([physical, rejected_binding, rejected])
            await db.flush()
            assert (await contexts())[0] == (root, rejected, position)
            driver, line, *_ = setup_driver()
            driver._batches = repo
            driver._positions.get = AsyncMock(return_value=position)
            driver._flow.return_before_motion = AsyncMock(return_value=None)
            driver._transports.get_task.return_value = rejected
            line.position_bindings["RETURN_RACK"] = {"location_id": "RETURN-POS"}
            assert await driver.project_exits_in_session(db, line) == 1
            assert driver._flow.return_before_motion.await_args.kwargs["arrived_at"] == START
            driver._departure_scheduler.create_in_session.assert_awaited_once()
            root.picking_task_id = 32
            await db.flush()
            assert await contexts() == []
            root.picking_task_id = 31
            newer = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=32,
                correlation_id="new-owner",
                causal_token=102,
                step="PICKING_TASK_RETURN_RACK_IN",
                resource_fence_id="R1",
                client_request_id="new-owner",
                source_evidence_id=52,
                created_at=START + timedelta(minutes=1),
            )
            new_task = TransportTask(
                **{
                    **task.model_dump(exclude={"id"}),
                    "transport_task_id": "new-owner",
                    "client_request_id": "new-owner",
                    "submit_operation_id": "new-owner",
                }
            )
            db.add_all([newer, new_task])
            await db.flush()
            # 已发布且不承接当前事实或窗口的外部 owner 历史不占有货架。
            for status in ("REJECTED", "FAILED", "SUCCEEDED"):
                new_task.status = status
                await db.flush()
                assert (await contexts())[0] == (root, rejected, position)
            for status in ("PENDING", "ACCEPTED", "RECONCILING"):
                new_task.status = status
                await db.flush()
                assert await contexts() == []
            new_task.status = "REJECTED"
            new_task.published_outcome_version = 0
            await db.flush()
            assert await contexts() == []
            new_task.published_outcome_version = 1
            position.source_causal_token = newer.causal_token
            position.source_transport_task_id = new_task.transport_task_id
            await db.flush()
            assert await contexts() == []
            position.source_causal_token = root.causal_token
            position.source_transport_task_id = task.transport_task_id
            newer.workline_id = 8
            newer.window_target_location_code = "RETURN-POS"
            await db.flush()
            assert await contexts() == []
            newer.window_released_at = START + timedelta(minutes=2)
            await db.flush()
            assert (await contexts())[0] == (root, rejected, position)
    finally:
        await engine.dispose()
