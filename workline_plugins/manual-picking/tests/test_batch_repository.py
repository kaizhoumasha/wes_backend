"""批次互斥只依赖本线未闭合可靠义务与物理结果。"""

import json
from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.batch_progress_model import ManualPickingInboundBatch, ManualPickingInboundBatchScan
from manual_picking.application.manual_source_arrival_model import ManualPickingSourceArrival
from manual_picking.application.passage_model import ManualPickingPassage
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.execution.models import (
    InboundEvidence,
    MaterialExecution,
    PositionProjection,
    TransportDecisionBinding,
    WmsConfirmation,
)
from src.app.transport.models import TransportEvidence, TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack
from src.app.workline.models import WorkLine


async def _new_sessions():  # type: ignore[no-untyped-def]
    _ = (MaterialExecution, PickingTask, WorkLine)  # 注册跨表 FK 元数据；无需创建业务表。
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schemas(connection, _record):  # type: ignore[no-untyped-def]
        connection.create_function("hashtextextended", 2, lambda identity, seed: 1)
        connection.create_function("pg_advisory_xact_lock", 1, lambda identity: None)
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    async with engine.begin() as connection:
        for table in (
            ManualPickingSourceArrival.__table__,
            InboundEvidence.__table__,
            WmsConfirmation.__table__,
            TransportTask.__table__,
            TransportEvidence.__table__,
            TransportMember.__table__,
            PositionProjection.__table__,
            PickingTask.__table__,
            TransportDecisionBinding.__table__,
            ManualPickingPassage.__table__,
            PickingTaskBinSourceRack.__table__,
            ManualPickingInboundBatch.__table__,
            ManualPickingInboundBatchScan.__table__,
        ):
            await connection.run_sync(table.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_gate_windows(db):
    for workline_id, rack_id in ((7, "R1"), (7, "R2"), (8, "R1"), (7, "510002")):
        db.add(
            TransportDecisionBinding(
                workline_id=workline_id,
                correlation_id=f"gate:{workline_id}:{rack_id}",
                step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                resource_fence_id=rack_id,
                client_request_id=f"gate:{workline_id}:{rack_id}",
                source_evidence_id=31,
                window_target_location_code=f"POS-{rack_id}",
                created_at=datetime(2000, 1, 1),
            )
        )
    await db.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["member_first", "transport_first"])
@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("result_status", ["FAILED", "SUCCEEDED"])
async def test_cancelled_source_window_rechecks_both_facts_and_latest_round_action(
    order, completed, result_status
) -> None:
    engine, sessions = await _new_sessions()
    started = datetime(2026, 10, 14)
    repo = import_module("manual_picking.application.batch_repository").BatchRepository()

    def transport(identity, status):
        return TransportTask(
            transport_task_id=identity,
            client_request_id=identity,
            kind="RACK_MOVE",
            authority_workline_id=7,
            caller_json={"workline_id": "7"},
            request_json={},
            request_digest="a" * 64,
            submit_operation_id=identity,
            submit_timestamp_ms=1,
            submit_request_body="{}",
            submit_request_body_digest="b" * 64,
            status=status,
            outcome_version=0,
            published_outcome_version=0,
            created_at=started,
            updated_at=started,
        )

    try:
        async with sessions.begin() as db:
            window = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=11,
                correlation_id="round",
                causal_token=100,
                step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                resource_fence_id="510021",
                client_request_id="ingress",
                source_evidence_id=31,
                window_target_location_code="KT16",
                created_at=started,
            )
            source = PickingTaskBinSourceRack(
                picking_task_id=11,
                plan_revision=1,
                rack_id="510021",
                rack_face="90",
                source_evidence_id=31,
            )
            ingress = transport("ingress", "ACCEPTED")
            position = PositionProjection(
                workline_id=7,
                object_type="RACK",
                object_id="510021",
                position_unknown=True,
                source_operation_id="cancel-result",
                source_transport_task_id="ingress",
                source_causal_token=100,
            )
            db.add_all([window, source, ingress, position])
            if completed:
                db.add(
                    PickingTask(
                        id=11,
                        task_id="COMPLETED",
                        task_type="MANUAL",
                        status="EXECUTION_COMPLETED",
                        workline_id=7,
                        queue_revision=1,
                        dispatch_sequence=1,
                        issued_at_ms=1,
                        issued_evidence_id=31,
                        last_applied_plan_revision=1,
                        initial_plan_evidence_id=31,
                        last_plan_evidence_id=31,
                    )
                )
            await db.flush()
            if completed:
                assert await repo.release_completed_source_windows(db, workline_id=7, now=started) == 1

            async def contexts():
                return await repo.cancelled_source_windows(db, workline_id=7, target_location_code="KT16")

            def cancelled_result():
                ingress.status = result_status
                ingress.outcome_version = ingress.published_outcome_version = 1
                position.position_unknown = False
                position.source_effect_phase = "FINAL_RESULT"
                position.position_json = {"kind": "RACK_POSITION", "location_code": "KT16"}

            if order == "member_first":
                source.cancelled_evidence_id = 91
            else:
                cancelled_result()
            await db.flush()
            assert await contexts() == []
            source.cancelled_evidence_id = 91
            cancelled_result()
            await db.flush()
            assert (await contexts())[0][1].transport_task_id == "ingress"

            old_member = PickingTaskBinSourceRack(
                picking_task_id=11,
                plan_revision=1,
                rack_id="510021",
                rack_face="270",
                source_evidence_id=30,
                created_at=started - timedelta(days=1),
            )
            db.add(old_member)
            await db.flush()
            assert await contexts() == []
            old_member.cancelled_evidence_id = 90
            await db.flush()
            assert (await contexts())[0][1].transport_task_id == "ingress"

            retry = transport("retry", "ACCEPTED")
            retry_binding = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=11,
                correlation_id="retry",
                causal_token=101,
                step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                resource_fence_id="510021",
                client_request_id="retry",
                source_evidence_id=31,
                created_at=started,
            )
            db.add_all([retry, retry_binding])
            await db.flush()
            # 即使投影仍是旧取消终位，也必须交出当前重试，不能拿旧失败清退。
            assert (await contexts())[0][1].status == "ACCEPTED"
            retry.status = "FAILED"
            retry.outcome_version = retry.published_outcome_version = 1
            position.source_transport_task_id = "retry"
            position.source_causal_token = 101
            await db.flush()
            assert (await contexts())[0][1].transport_task_id == "retry"

            retry_binding.step = "MANUAL_PICKING_SOURCE_RACK_OUT"
            window.window_departure_client_request_id = "retry"
            await db.flush()
            assert await contexts() == []
            if completed:
                window.window_departure_client_request_id = None
                retry_binding.source_evidence_id = 99
                await db.flush()
                assert await contexts() == []
                window.window_departure_client_request_id = "retry"
                retry_binding.source_evidence_id = 31
            retry.status = "REJECTED"
            await db.flush()
            assert (await contexts())[0][1].transport_task_id == "retry"
            retry.status = "FAILED"
            retry_binding.step = "PICKING_TASK_BIN_SOURCE_RACK_IN"
            window.window_departure_client_request_id = None
            await db.flush()

            other = transport("other-owner", "REJECTED")
            other.outcome_version = other.published_outcome_version = 1
            other_binding = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=12,
                correlation_id="other-owner",
                causal_token=102,
                step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                resource_fence_id="510021",
                client_request_id="other-owner",
                source_evidence_id=32,
                created_at=started,
            )
            db.add_all([other, other_binding])
            await db.flush()
            assert (await contexts())[0][1].transport_task_id == "retry"
            other.status = "ACCEPTED"
            await db.flush()
            assert await contexts() == []
            other.status = "REJECTED"
            await db.flush()
            assert (await contexts())[0][1].transport_task_id == "retry"

            new_member = PickingTaskBinSourceRack(
                picking_task_id=11,
                plan_revision=2,
                rack_id="510021",
                rack_face="90",
                source_evidence_id=32,
                created_at=started - timedelta(seconds=1),
            )
            db.add(new_member)
            await db.flush()
            assert await contexts() == []
            new_member.cancelled_evidence_id = 92
            position.source_causal_token = 99
            await db.flush()
            assert await contexts() == []
            position.source_causal_token = 101
            retry_binding.workline_id = 8
            await db.flush()
            assert await contexts() == []
            retry_binding.workline_id = 7
            position.position_json = {"kind": "RACK_POSITION", "location_code": "STORAGE"}
            await db.flush()
            assert await contexts() == []
            position.position_json = {"kind": "RACK_POSITION", "location_code": "KT16"}
            window.window_released_at = started + timedelta(seconds=1)
            await db.flush()
            assert bool(await contexts()) is completed
            assert (await db.scalars(select(TransportTask))).all() == [ingress, retry, other]
    finally:
        await engine.dispose()


async def _seed_current_inbound(db, intent, result, *, evidence_id=31, plan_revision=1):  # type: ignore[no-untyped-def]
    data = (
        {"result": "RACK_FACE_DONE"}
        if isinstance(result, sdk.BinInboundBatchRackFaceDone)
        else {
            "result": "READY",
            "bins": [
                {
                    "bin_code": member.bin_code,
                    "source_locator": {
                        "type": "RACK_BIN_SLOT",
                        "rack_id": member.source_locator.rack_id,
                        "rack_face": member.source_locator.rack_face,
                        "slot_id": member.source_locator.slot_id,
                    },
                }
                for member in result.bins
            ],
        }
    )
    db.add(
        InboundEvidence(
            id=evidence_id,
            kind="WMS_RESULT",
            source_identity=f"wms:inbound:{intent.operation_id}",
            payload_digest="b" * 64,
            normalized_payload={"operation_id": intent.operation_id, "code": "DECIDED", "timestamp": 1, "data": data},
            received_at=datetime(2026, 9, 13, 12),
            published_at=datetime(2026, 9, 13, 12),
            decision_digest="c" * 64,
            workline_id=7,
            operation="outbound.bin.inbound_batch@v1",
            operation_id=intent.operation_id,
            apply_status="APPLIED",
        )
    )
    await db.flush()
    await (
        import_module("manual_picking.application.batch_repository")
        .BatchRepository()
        .record_allocation(
            db, workline_id=7, picking_task_id=31, plan_revision=plan_revision, intent=intent, evidence_id=evidence_id
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_inbound_allocation_replay_keeps_first_local_revision() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="019f0000-0000-7000-8000-000000000501", task_id="PICK-1", rack_id="R1", rack_face="90"
    )
    try:
        async with sessions.begin() as db:
            await _seed_current_inbound(db, intent, sdk.BinInboundBatchRackFaceDone())
            repo = module.BatchRepository()
            with pytest.raises(ValueError, match="identity changed"):
                await repo.record_allocation(
                    db, workline_id=7, picking_task_id=31, plan_revision=2, intent=intent, evidence_id=31
                )
            await repo.record_allocation(
                db, workline_id=7, picking_task_id=31, plan_revision=1, intent=intent, evidence_id=31
            )
            await db.flush()
            progress = await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
            assert progress.intent == intent
            assert progress.complete
            batches = (await db.scalars(select(ManualPickingInboundBatch))).all()
            assert len(batches) == 1 and batches[0].plan_revision == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scan1_batch_progress_survives_closed_passage_deletion() -> None:
    from manual_picking.application.batch_repository import BatchRepository

    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    operation_id = "019f0000-0000-7000-8000-000000000001"
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id=operation_id, task_id="PICK-1", rack_id="R1", rack_face="90"
    )
    repo = BatchRepository()
    try:
        async with sessions.begin() as db:
            confirmation = WmsConfirmation(
                operation="outbound.bin.inbound_batch@v1",
                operation_id=operation_id,
                workline_id=7,
                request_digest="a" * 64,
                request_payload={},
                deadline_at=now,
                status="COMPLETED",
                completed_at=now,
            )
            evidence = InboundEvidence(
                kind="WMS_RESULT",
                source_identity="batch-result-1",
                payload_digest="b" * 64,
                normalized_payload={
                    "operation_id": operation_id,
                    "code": "DECIDED",
                    "timestamp": 1,
                    "data": {
                        "result": "READY",
                        "bins": [
                            {
                                "bin_code": "BIN-1",
                                "source_locator": {
                                    "type": "RACK_BIN_SLOT",
                                    "rack_id": "R1",
                                    "rack_face": "90",
                                    "slot_id": "S-1",
                                },
                            }
                        ],
                    },
                },
                received_at=now,
                published_at=now,
                decision_digest="c" * 64,
                workline_id=7,
                operation=confirmation.operation,
                operation_id=operation_id,
                apply_status="APPLIED",
            )
            db.add_all((confirmation, evidence))
            await db.flush()
            confirmation.response_evidence_id = evidence.id
            await repo.record_allocation(
                db, plan_revision=1, workline_id=7, picking_task_id=31, intent=intent, evidence_id=evidence.id
            )
            db.add_all(
                (
                    TransportDecisionBinding(
                        workline_id=7,
                        picking_task_id=31,
                        correlation_id=f"{operation_id}:0",
                        step="MANUAL_PICKING_INBOUND_BATCH",
                        resource_fence_id=operation_id,
                        source_evidence_id=evidence.id,
                        client_request_id="client-1",
                    ),
                    TransportTask(
                        transport_task_id="transport-1",
                        client_request_id="client-1",
                        request_digest="a" * 64,
                        kind="BIN_MOVE",
                        caller_json={"workline_id": "7"},
                        request_json={"moves": []},
                        submit_operation_id="submit-1",
                        submit_timestamp_ms=1,
                        submit_request_body="{}",
                        submit_request_body_digest="b" * 64,
                        status="SUCCEEDED",
                        outcome_version=1,
                        published_outcome_version=1,
                        authority_workline_id=7,
                        created_at=now,
                        updated_at=now,
                    ),
                    TransportMember(
                        transport_task_id="transport-1",
                        ordinal=1,
                        object_type="BIN",
                        object_id="BIN-1",
                        source_json={},
                        target_json={"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
                        final_position_json={"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
                        status="SUCCEEDED",
                        updated_at=now,
                    ),
                )
            )
            await db.flush()
            await db.delete(confirmation)
            await db.flush()
            assert await repo.record_scan1(
                db, workline_id=7, picking_task_id=31, transport_task_id="transport-1", bin_code="BIN-1"
            )
            await db.flush()
            assert not await repo.record_scan1(
                db, workline_id=7, picking_task_id=31, transport_task_id="transport-1", bin_code="BIN-1"
            )
            passage = ManualPickingPassage(
                workline_id=7,
                task_id="PICK-1",
                bin_code="BIN-1",
                scan1_evidence_id=evidence.id,
                scan1_received_at=now,
                scan3_evidence_id=evidence.id,
                disposition="CLOSED",
            )
            db.add(passage)
            await db.flush()
            await db.delete(passage)
            await db.flush()
            progress = await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
            assert progress is not None and progress.complete and progress.feed_complete
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_inbound_results_for_same_rack_reject_multiple_allocations() -> None:
    from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader

    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    try:
        async with sessions.begin() as db:
            for revision in (1, 2):
                operation_id = f"019f0000-0000-7000-8000-{revision:012d}"
                confirmation = WmsConfirmation(
                    operation="outbound.bin.inbound_batch@v1",
                    operation_id=operation_id,
                    workline_id=7,
                    request_digest="a" * 64,
                    request_payload={
                        "operation": "outbound.bin.inbound_batch@v1",
                        "operation_id": operation_id,
                        "timestamp": 1,
                        "data": {"task_id": "PICK-1", "rack_id": "A", "rack_face": "90"},
                    },
                    deadline_at=now,
                    status="COMPLETED",
                    completed_at=now,
                )
                evidence = InboundEvidence(
                    kind="WMS_RESULT",
                    source_identity=f"wms:inbound:{revision}",
                    payload_digest="b" * 64,
                    normalized_payload={
                        "operation_id": operation_id,
                        "code": "DECIDED",
                        "timestamp": 2,
                        "data": {"result": "RACK_FACE_DONE"},
                    },
                    received_at=now,
                    published_at=now,
                    decision_digest="c" * 64,
                    workline_id=7,
                    operation=confirmation.operation,
                    operation_id=operation_id,
                    apply_status="APPLIED",
                )
                db.add_all((confirmation, evidence))
                await db.flush()
                confirmation.response_evidence_id = evidence.id
            reader = BinBatchResultReader()
            with pytest.raises(ValueError, match="multiple inbound allocations"):
                await reader.latest_inbound_detail(db, workline_id=7, task_id="PICK-1", rack_id="A", rack_face="90")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_drain_unclosed_transport_is_scoped_to_its_reserved_rack() -> None:
    from manual_picking.application.drain_repository import DRAIN_RACK_IN_STEP, DrainRepository

    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 22, 16)
    decision = SimpleNamespace(workline_id=7, evidence_id=10, intent=SimpleNamespace(operation_id="drain-1"))
    try:
        async with sessions.begin() as db:
            for rack_id, status in (("R1", "SUCCEEDED"), ("R2", "PENDING")):
                client_request_id = f"drain-{rack_id}"
                db.add(
                    TransportDecisionBinding(
                        workline_id=7,
                        picking_task_id=None,
                        correlation_id=f"drain:drain-1:rack:{rack_id}",
                        step=DRAIN_RACK_IN_STEP,
                        resource_fence_id=rack_id,
                        source_evidence_id=10,
                        client_request_id=client_request_id,
                    )
                )
                db.add(
                    TransportTask(
                        transport_task_id=f"transport-{rack_id}",
                        client_request_id=client_request_id,
                        request_digest="a" * 64,
                        kind="RACK_MOVE",
                        caller_json={"workline_id": "7"},
                        request_json={"rack_id": rack_id},
                        submit_operation_id=f"submit-{rack_id}",
                        submit_timestamp_ms=1,
                        submit_request_body="{}",
                        submit_request_body_digest="b" * 64,
                        status=status,
                        authority_workline_id=7,
                        created_at=now,
                        updated_at=now,
                    )
                )

        async with sessions() as db:
            repository = DrainRepository()
            assert not await repository.has_unclosed_rack_action(db, decision, "R1")
            assert await repository.has_unclosed_rack_action(db, decision, "R2")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_face_gate_ignores_unrelated_pending_batch() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    try:
        async with sessions.begin() as db:
            await _seed_gate_windows(db)
            repo = module.BatchRepository()
            db.add(
                WmsConfirmation(
                    operation="outbound.bin.inbound_batch@v1",
                    operation_id="019f0000-0000-7000-8000-000000000101",
                    workline_id=7,
                    request_digest="a" * 64,
                    request_payload={
                        "operation": "outbound.bin.inbound_batch@v1",
                        "data": {"task_id": "PICK-2", "rack_id": "R2", "rack_face": "270"},
                    },
                    deadline_at=now,
                    status="PENDING",
                )
            )
            await db.flush()
            assert not await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")
            assert await repo.has_unclosed_action_for_face(db, 7, "PICK-2", 1, "R2", "270")
            db.add(
                TransportTask(
                    transport_task_id="return-1",
                    client_request_id="return-client-1",
                    request_digest="c" * 64,
                    kind="BIN_MOVE",
                    caller_json={"workline_id": "7"},
                    request_json={"moves": []},
                    submit_operation_id="return-submit-1",
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="d" * 64,
                    status="RECONCILING",
                    authority_workline_id=7,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                TransportMember(
                    transport_task_id="return-1",
                    ordinal=1,
                    object_type="BIN",
                    object_id="BIN-1",
                    source_json={"kind": "HANDOFF_POSITION", "location_code": "RETURN"},
                    target_json={"kind": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": "1"},
                    updated_at=now,
                )
            )
            await db.flush()
            assert await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")
            assert await repo.has_unclosed_return_transport_for_face(db, workline_id=7, rack_id="R1", rack_face="90")
            assert not await repo.has_unclosed_action_for_face(db, 7, None, None, "R2", "270")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_face_gate_blocks_unpublished_wms_result_and_other_workline_or_unrelated_history() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    try:
        async with sessions.begin() as db:
            await _seed_gate_windows(db)
            repo = module.BatchRepository()
            # 1) PENDING confirmation 同 workline/同 task/同 face 阻塞;不同 workline 不阻塞。
            confirmation = WmsConfirmation(
                operation="outbound.bin.inbound_batch@v1",
                operation_id="019f0000-0000-7000-8000-000000000201",
                workline_id=7,
                request_digest="a" * 64,
                request_payload={
                    "operation": "outbound.bin.inbound_batch@v1",
                    "data": {"task_id": "PICK-1", "rack_id": "R1", "rack_face": "90"},
                },
                deadline_at=now,
                status="PENDING",
            )
            db.add(confirmation)
            await db.flush()
            assert await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")
            assert await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 2, "R1", "90")
            assert not await repo.has_unclosed_action_for_face(db, 8, "PICK-1", 1, "R1", "90")

            # 2) COMPLETED confirmation 但 response_evidence 未 published 仍阻塞。
            confirmation.status = "COMPLETED"
            evidence = InboundEvidence(
                kind="WMS_RESULT",
                source_identity="wms:batch-1",
                payload_digest="b" * 64,
                normalized_payload={"code": "DECIDED", "data": {"result": "RACK_FACE_DONE"}},
                received_at=now,
                workline_id=7,
                operation="outbound.bin.inbound_batch@v1",
                operation_id=confirmation.operation_id,
                apply_status="APPLIED",
            )
            db.add(evidence)
            await db.flush()
            confirmation.response_evidence_id = evidence.id
            assert await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")
            evidence.published_at = now
            evidence.decision_digest = "e" * 64
            await db.flush()
            assert not await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")

            # 3) 未关联的 history evidence(其他 operation_id)不构成阻塞。
            db.add(
                InboundEvidence(
                    kind="WMS_RESULT",
                    source_identity="wms:unassociated-history",
                    payload_digest="1" * 64,
                    normalized_payload={"code": "DECIDED", "data": {"result": "RACK_FACE_DONE"}},
                    received_at=now,
                    published_at=now,
                    decision_digest="c" * 64,
                    workline_id=7,
                    operation="outbound.bin.inbound_batch@v1",
                    operation_id="019f0000-0000-7000-8000-000000000299",
                    apply_status="APPLIED",
                )
            )
            await db.flush()
            assert not await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")

            # 4) TRANSPORT_RESULT evidence 不计入 WMS_RESULT 阻塞条件。
            db.add(
                InboundEvidence(
                    kind="TRANSPORT_RESULT",
                    source_identity="transport:old-rack:outcome:1",
                    payload_digest="f" * 64,
                    normalized_payload={"step": "PICKING_TASK_BIN_SOURCE_RACK_IN"},
                    received_at=now,
                    workline_id=7,
                    transport_task_id="old-rack",
                    apply_status="APPLIED",
                )
            )
            await db.flush()
            assert not await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_face_gate_waits_for_published_terminal_position() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    try:
        async with sessions.begin() as db:
            await _seed_gate_windows(db)
            repo = module.BatchRepository()
            task = TransportTask(
                transport_task_id="transport-1",
                client_request_id="client-1",
                request_digest="c" * 64,
                kind="BIN_MOVE",
                caller_json={"workline_id": "7"},
                request_json={"moves": []},
                submit_operation_id="submit-1",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="d" * 64,
                status="PENDING",
                authority_workline_id=7,
                created_at=now,
                updated_at=now,
            )
            db.add(task)
            member = TransportMember(
                transport_task_id="transport-1",
                ordinal=1,
                object_type="BIN",
                object_id="BIN-1",
                source_json={"kind": "HANDOFF_POSITION", "location_code": "RETURN"},
                target_json={"kind": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": "1"},
                updated_at=now,
            )
            db.add(member)
            await db.flush()
            assert await repo.has_unclosed_return_transport_for_face(db, workline_id=7, rack_id="R1", rack_face="90")
            task.status = "ACCEPTED"
            await db.flush()
            assert await repo.has_unclosed_return_transport_for_face(db, workline_id=7, rack_id="R1", rack_face="90")
            task.status = "SUCCEEDED"
            member.status = "SUCCEEDED"
            member.final_position_json = {"kind": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": "2"}
            task.outcome_version = 1
            assert await repo.has_unclosed_return_transport_for_face(db, workline_id=7, rack_id="R1", rack_face="90")
            task.published_outcome_version = 1
            await db.flush()
            assert await repo.has_unclosed_return_transport_for_face(db, workline_id=7, rack_id="R1", rack_face="90")
            member.final_position_json = member.target_json
            task.outcome_version = 2
            await db.flush()
            assert await repo.has_unclosed_return_transport_for_face(db, workline_id=7, rack_id="R1", rack_face="90")
            task.published_outcome_version = 2
            await db.flush()
            assert not await repo.has_unclosed_return_transport_for_face(
                db, workline_id=7, rack_id="R1", rack_face="90"
            )
            task.status = "FAILED"
            member.status = "FAILED"
            task.outcome_version = 3
            task.published_outcome_version = 3
            member.final_position_json = None
            member.position_unknown = False
            await db.flush()
            assert await repo.has_unclosed_return_transport_for_face(db, workline_id=7, rack_id="R1", rack_face="90")
            member.final_position_json = member.target_json
            await db.flush()
            assert not await repo.has_unclosed_return_transport_for_face(
                db, workline_id=7, rack_id="R1", rack_face="90"
            )
            member.final_position_json = {"kind": "HANDOFF_POSITION", "location_code": "RETURN"}
            await db.flush()
            assert not await repo.has_unclosed_return_transport_for_face(
                db, workline_id=7, rack_id="R1", rack_face="90"
            )
            task.status = "REJECTED"
            member.status = "REJECTED"
            member.final_position_json = None
            await db.flush()
            assert not await repo.has_unclosed_return_transport_for_face(
                db, workline_id=7, rack_id="R1", rack_face="90"
            )
            task.status = "RECONCILING"
            member.status = "FAILED"
            member.position_unknown = True
            await db.flush()
            assert await repo.has_unclosed_return_transport_for_face(db, workline_id=7, rack_id="R1", rack_face="90")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_return_transport_face_gate_compiles_json_position_comparison_for_postgres() -> None:
    repo = import_module("manual_picking.application.batch_repository").BatchRepository()
    repo.current_round_started_at = AsyncMock(return_value=datetime(2000, 1, 1))
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))

    await repo.has_unclosed_return_transport_for_face(db, workline_id=7, rack_id="R1", rack_face="90")

    sql = str(db.scalar.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "IS NOT DISTINCT FROM" in sql
    assert "final_position_json !=" not in sql
    assert "final_position_json =" not in sql


@pytest.mark.asyncio
async def test_face_gate_without_task_scope_tracks_return_confirmation_and_evidence() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    try:
        async with sessions.begin() as db:
            await _seed_gate_windows(db)
            repo = module.BatchRepository()
            confirmation = WmsConfirmation(
                operation="outbound.bin.return_batch@v1",
                operation_id="019f0000-0000-7000-8000-000000000401",
                workline_id=7,
                request_digest="a" * 64,
                request_payload={
                    "operation": "outbound.bin.return_batch@v1",
                    "data": {"rack_id": "R1", "rack_face": "90"},
                },
                deadline_at=now,
                status="PENDING",
            )
            db.add(confirmation)
            await db.flush()
            assert await repo.has_unclosed_action_for_face(db, 7, None, None, "R1", "90")

            confirmation.status = "COMPLETED"
            evidence = InboundEvidence(
                kind="WMS_RESULT",
                source_identity="wms:return:401",
                payload_digest="b" * 64,
                normalized_payload={"code": "DECIDED", "data": {"result": "READY"}},
                received_at=now,
                workline_id=7,
                operation="outbound.bin.return_batch@v1",
                operation_id=confirmation.operation_id,
                apply_status="APPLIED",
            )
            db.add(evidence)
            await db.flush()
            confirmation.response_evidence_id = evidence.id
            await db.flush()
            assert await repo.has_unclosed_action_for_face(db, 7, None, None, "R1", "90")

            evidence.published_at = now
            evidence.decision_digest = "c" * 64
            await db.flush()
            assert not await repo.has_unclosed_action_for_face(db, 7, None, None, "R1", "90")

            db.add(
                WmsConfirmation(
                    operation="outbound.bin.inbound_batch@v1",
                    operation_id="019f0000-0000-7000-8000-000000000402",
                    workline_id=7,
                    request_digest="d" * 64,
                    request_payload={
                        "operation": "outbound.bin.inbound_batch@v1",
                        "data": {
                            "task_id": "PICK-2",
                            "rack_id": "R1",
                            "rack_face": "90",
                        },
                    },
                    deadline_at=now,
                    status="PENDING",
                )
            )
            await db.flush()
            assert not await repo.has_unclosed_action_for_face(db, 7, None, None, "R1", "90")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_batch_retry_and_face_done_are_derived_from_matched_wms_results() -> None:
    from src.utils.canonical_json import canonical_json_digest

    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    try:
        async with sessions.begin() as db:
            repo = module.BatchRepository()
            assert await repo.return_retry_due(db, 7, "R1", "90", 51, now, now + timedelta(microseconds=1))
            assert await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301") is None
            return_confirmation = WmsConfirmation(
                operation="outbound.bin.return_batch@v1",
                operation_id="019f0000-0000-7000-8000-000000000002",
                workline_id=7,
                request_digest="a" * 64,
                request_payload={
                    "operation": "outbound.bin.return_batch@v1",
                    "operation_id": "019f0000-0000-7000-8000-000000000002",
                    "timestamp": 1_788_975_600_000,
                    "data": {
                        "workline_code": "LINE-1",
                        "rack_id": "R1",
                        "rack_face": "90",
                        "return_candidates": [
                            {
                                "sequence_no": 1,
                                "bin_code": "A000000001",
                                "source": {"type": "HANDOFF_POSITION", "location_code": "CNV0302"},
                            }
                        ],
                    },
                },
                deadline_at=now,
                status="COMPLETED",
                completed_at=now,
            )
            return_confirmation.request_digest = canonical_json_digest(return_confirmation.request_payload)
            db.add(return_confirmation)
            return_evidence = InboundEvidence(
                kind="WMS_RESULT",
                source_identity="wms:return-1",
                payload_digest="b" * 64,
                normalized_payload={
                    "operation_id": return_confirmation.operation_id,
                    "code": "DECIDED",
                    "timestamp": 1_788_975_600_100,
                    "data": {"result": "NO_BATCH", "retry_after_ms": 1000},
                },
                received_at=now,
                published_at=now,
                decision_digest="c" * 64,
                workline_id=7,
                operation=return_confirmation.operation,
                operation_id=return_confirmation.operation_id,
                apply_status="APPLIED",
            )
            return_evidence.payload_digest = canonical_json_digest(return_evidence.normalized_payload)
            db.add(return_evidence)
            await db.flush()
            return_confirmation.response_evidence_id = return_evidence.id
            await db.flush()
            assert not await repo.return_retry_due(
                db, 7, "R1", "90", 51, now + timedelta(milliseconds=999), now + timedelta(microseconds=1)
            )
            assert not await repo.return_retry_due(
                db, 7, "R1", "90", 51, now + timedelta(milliseconds=1000), now + timedelta(microseconds=1)
            )
            assert await repo.return_retry_due(db, 7, "R1", "270", 51, now, now + timedelta(microseconds=1))
            db.add(
                InboundEvidence(
                    kind="DEVICE_RESULT",
                    source_identity="device:move-1",
                    payload_digest="d" * 64,
                    normalized_payload={"command_code": "MOVE-1", "result": "SUCCESS"},
                    received_at=now,
                    published_at=now + timedelta(milliseconds=1),
                    decision_digest="e" * 64,
                    workline_id=7,
                    device_code="SCAN4",
                    command_code="MOVE-1",
                    apply_status="APPLIED",
                )
            )
            await db.flush()
            assert not await repo.return_retry_due(
                db, 7, "R1", "90", 51, now + timedelta(milliseconds=1), now + timedelta(microseconds=1)
            )

            inbound_confirmation = WmsConfirmation(
                operation="outbound.bin.inbound_batch@v1",
                operation_id="019f0000-0000-7000-8000-000000000003",
                workline_id=7,
                request_digest="d" * 64,
                request_payload={
                    "operation": "outbound.bin.inbound_batch@v1",
                    "operation_id": "019f0000-0000-7000-8000-000000000003",
                    "timestamp": 1_788_975_600_000,
                    "data": {"task_id": "PICK-1", "rack_id": "R1", "rack_face": "90"},
                },
                deadline_at=now,
                status="COMPLETED",
                completed_at=now,
            )
            db.add(inbound_confirmation)
            inbound_evidence = InboundEvidence(
                kind="WMS_RESULT",
                source_identity="wms:inbound-1",
                payload_digest="e" * 64,
                normalized_payload={
                    "operation_id": inbound_confirmation.operation_id,
                    "code": "DECIDED",
                    "timestamp": 1_788_975_600_100,
                    "data": {"result": "RACK_FACE_DONE"},
                },
                received_at=now,
                published_at=now,
                decision_digest="f" * 64,
                workline_id=7,
                operation=inbound_confirmation.operation,
                operation_id=inbound_confirmation.operation_id,
                apply_status="APPLIED",
            )
            db.add(inbound_evidence)
            await db.flush()
            inbound_confirmation.response_evidence_id = inbound_evidence.id
            await db.flush()
            await repo.record_allocation(
                db,
                plan_revision=1,
                workline_id=7,
                picking_task_id=31,
                intent=sdk.wms_operations.outbound_bin_inbound_batch(
                    operation_id=inbound_confirmation.operation_id,
                    task_id="PICK-1",
                    rack_id="R1",
                    rack_face="90",
                ),
                evidence_id=inbound_evidence.id,
            )
            await db.flush()
            await db.delete(inbound_confirmation)
            await db.flush()
            progress = await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
            assert progress.complete and progress.feed_complete
            inbound_evidence.published_at = None
            await db.flush()
            assert await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301") is None
            assert await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "270", "CNV0301") is None
            assert await repo.inbound_progress(db, 8, "PICK-1", 1, "R1", "90", "CNV0301") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_chunk", [False, True])
async def test_inbound_closed_face_does_not_request_again(has_chunk: bool) -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            intent = sdk.wms_operations.outbound_bin_inbound_batch(
                operation_id="019f0000-0000-7000-8000-000000000011",
                task_id="PICK-1",
                rack_id="R1",
                rack_face="90",
            )
            await _seed_current_inbound(db, intent, sdk.BinInboundBatchRackFaceDone())
            if has_chunk:
                db.add(
                    TransportDecisionBinding(
                        workline_id=7,
                        correlation_id=f"{intent.operation_id}:0",
                        step="MANUAL_PICKING_INBOUND_BATCH",
                        resource_fence_id=intent.operation_id,
                        source_evidence_id=31,
                        client_request_id="client-1",
                    )
                )
                await db.flush()
            progress = await module.BatchRepository().inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
            assert progress.feed_complete is (not has_chunk)
            assert progress.complete is (not has_chunk)
            assert progress.next_offset is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("bin_count", [5, 41])
async def test_frozen_face_advances_only_after_transport_success_and_matching_scans(bin_count: int) -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    queries: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def count_queries(_connection, _cursor, statement, _parameters, _context, _executemany):  # type: ignore[no-untyped-def]
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    now = datetime(2026, 9, 13, 12)
    operation_id = "019f0000-0000-7000-8000-000000000012"
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id=operation_id, task_id="PICK-1", rack_id="R1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady(
        tuple(
            sdk.BinInboundBatchMember(f"BIN-{index}", sdk.TransportRackBinSlot("R1", "90", f"S-{index}"))
            for index in range(1, bin_count + 1)
        )
    )
    repo = module.BatchRepository()

    def add_chunk(db, offset):  # type: ignore[no-untyped-def]
        db.add(
            TransportDecisionBinding(
                workline_id=7,
                correlation_id=f"{operation_id}:{offset}",
                step="MANUAL_PICKING_INBOUND_BATCH",
                resource_fence_id=operation_id,
                source_evidence_id=31,
                client_request_id=f"client-{offset}",
            )
        )
        db.add(
            TransportTask(
                transport_task_id=f"transport-{offset}",
                client_request_id=f"client-{offset}",
                request_digest="a" * 64,
                kind="BIN_MOVE",
                caller_json={"workline_id": "7"},
                request_json={"moves": []},
                submit_operation_id=f"submit-{offset}",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="b" * 64,
                status="SUCCEEDED",
                outcome_version=1,
                published_outcome_version=1,
                authority_workline_id=7,
                created_at=now,
                updated_at=now,
            )
        )

        for ordinal, item in enumerate(ready.bins[offset : offset + 4], 1):
            db.add(
                TransportMember(
                    transport_task_id=f"transport-{offset}",
                    ordinal=ordinal,
                    object_type="BIN",
                    object_id=item.bin_code,
                    source_json={},
                    target_json={"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
                    final_position_json={"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
                    status="SUCCEEDED",
                    updated_at=now,
                )
            )

    try:
        async with sessions.begin() as db:
            await _seed_current_inbound(db, intent, ready, plan_revision=5)
            progress = await repo.inbound_progress(db, 7, "PICK-1", 5, "R1", "90", "CNV0301")
            assert progress.next_offset == 0 and not progress.complete
            add_chunk(db, 0)
            await db.flush()
            assert (await repo.inbound_progress(db, 7, "PICK-1", 5, "R1", "90", "CNV0301")).next_offset is None
            waiting = await repo.inbound_progress(db, 7, "PICK-1", 13, "R1", "90", "CNV0301")
            assert waiting.intent.operation_id == operation_id
            assert waiting.next_offset is None and not waiting.feed_complete
            for index in range(1, 5):
                db.add(
                    ManualPickingInboundBatchScan(
                        workline_id=7,
                        operation_id=operation_id,
                        bin_code=f"BIN-{index}",
                    )
                )
            await db.flush()
            assert (await repo.inbound_progress(db, 7, "PICK-1", 5, "R1", "90", "CNV0301")).next_offset == 4
            resumed = await repo.inbound_progress(db, 7, "PICK-1", 13, "R1", "90", "CNV0301")
            assert resumed.intent.operation_id == operation_id
            assert resumed.evidence_id == 31
            assert resumed.next_offset == 4 and not resumed.feed_complete
            assert (await db.scalar(select(ManualPickingInboundBatch))).plan_revision == 5
            for offset in range(4, bin_count, 4):
                add_chunk(db, offset)
            await db.flush()
            progress = await repo.inbound_progress(db, 7, "PICK-1", 5, "R1", "90", "CNV0301")
            assert progress.feed_complete and not progress.complete
            assert await repo.inbound_progress(db, 7, "PICK-1", 13, "R1", "90", "CNV0301") is None
            for index in range(5, bin_count + 1):
                db.add(
                    ManualPickingInboundBatchScan(
                        workline_id=7,
                        operation_id=operation_id,
                        bin_code=f"BIN-{index}",
                    )
                )
            await db.flush()
            queries.clear()
            progress = await repo.inbound_progress(db, 7, "PICK-1", 5, "R1", "90", "CNV0301")
            assert progress.complete and progress.feed_complete and progress.next_offset is None
            assert progress.last_chunk_created_at == now
            assert len(queries) <= 5, f"{bin_count} bins required {len(queries)} SELECTs"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_current_revision_rejects_multiple_unfinished_face_allocations() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    ready = sdk.BinInboundBatchReady((sdk.BinInboundBatchMember("BIN-1", sdk.TransportRackBinSlot("R1", "90", "S1")),))
    try:
        async with sessions.begin() as db:
            for revision in (5, 6):
                intent = sdk.wms_operations.outbound_bin_inbound_batch(
                    operation_id=f"019f0000-0000-7000-8000-{revision:012d}",
                    task_id="PICK-1",
                    rack_id="R1",
                    rack_face="90",
                )
                await _seed_current_inbound(db, intent, ready, evidence_id=30 + revision, plan_revision=revision)
            with pytest.raises(ValueError, match="multiple unfinished inbound allocations"):
                await module.BatchRepository().inbound_progress(db, 7, "PICK-1", 13, "R1", "90", "CNV0301")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_frozen_face_does_not_redispatch_legacy_inbound_binding() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    operation_id = "019f0000-0000-7000-8000-000000000013"
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id=operation_id, task_id="PICK-1", rack_id="R1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady((sdk.BinInboundBatchMember("BIN-1", sdk.TransportRackBinSlot("R1", "90", "S-1")),))
    try:
        async with sessions.begin() as db:
            await _seed_current_inbound(db, intent, ready)
            db.add(
                TransportDecisionBinding(
                    workline_id=7,
                    correlation_id=operation_id,
                    step="MANUAL_PICKING_INBOUND_BATCH",
                    resource_fence_id=operation_id,
                    source_evidence_id=31,
                    client_request_id="legacy-client",
                )
            )
            await db.flush()
            with pytest.raises(ValueError, match="legacy inbound binding"):
                await module.BatchRepository().inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("success_without_scans", True),
        ("pending", False),
        ("accepted", False),
        ("reconciling", False),
        ("failed", False),
        ("unpublished", False),
        ("no_outcome", False),
        ("missing_transport", False),
        ("orphan_transport", False),
        ("missing_member", False),
        ("failed_member", False),
        ("unknown_position", False),
        ("missing_position", False),
        ("wrong_position", False),
        ("wrong_kind", False),
        ("wrong_bin", False),
        ("wrong_workline", False),
    ],
)
async def test_feed_complete_requires_published_transport_members_at_inlet(case: str, expected: bool) -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 15, 12)
    operation_id = "019f0000-0000-7000-8000-000000000014"
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id=operation_id, task_id="PICK-1", rack_id="R1", rack_face="90"
    )
    result = sdk.BinInboundBatchReady((sdk.BinInboundBatchMember("BIN-1", sdk.TransportRackBinSlot("R1", "90", "S1")),))
    try:
        async with sessions.begin() as db:
            await _seed_current_inbound(db, intent, result)
            if case != "missing_transport":
                for index in range(1):
                    db.add(
                        TransportDecisionBinding(
                            workline_id=7,
                            correlation_id=f"{operation_id}:0",
                            step="MANUAL_PICKING_INBOUND_BATCH",
                            resource_fence_id=operation_id,
                            source_evidence_id=31,
                            client_request_id=f"client-{index}",
                        )
                    )
                    if case == "orphan_transport":
                        continue
                    db.add(
                        TransportTask(
                            transport_task_id=f"feed-{index}",
                            client_request_id=f"client-{index}",
                            request_digest="a" * 64,
                            kind="BIN_MOVE",
                            caller_json={},
                            request_json={},
                            submit_operation_id=f"op-{index}",
                            submit_timestamp_ms=1,
                            submit_request_body="{}",
                            submit_request_body_digest="b" * 64,
                            status=case.upper()
                            if case in ("pending", "accepted", "reconciling", "failed")
                            else "SUCCEEDED",
                            authority_workline_id=8 if case == "wrong_workline" else 7,
                            outcome_version=0 if case == "no_outcome" else 1,
                            published_outcome_version=0 if case == "unpublished" else 1,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    if case != "missing_member":
                        db.add(
                            TransportMember(
                                transport_task_id=f"feed-{index}",
                                ordinal=1,
                                object_type="RACK" if case == "wrong_kind" else "BIN",
                                object_id="BIN-OTHER" if case == "wrong_bin" else "BIN-1",
                                source_json={},
                                target_json={"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
                                status="FAILED" if case == "failed_member" else "SUCCEEDED",
                                position_unknown=case == "unknown_position",
                                final_position_json=None
                                if case == "missing_position"
                                else {
                                    "kind": "HANDOFF_POSITION",
                                    "location_code": "WRONG" if case == "wrong_position" else "CNV0301",
                                },
                                updated_at=now,
                            )
                        )
            if not expected:
                db.add(
                    ManualPickingPassage(
                        workline_id=7,
                        task_id="PICK-1",
                        bin_code="BIN-1",
                        scan1_evidence_id=1,
                        scan1_received_at=now,
                        disposition="OPEN",
                    )
                )
            await db.flush()
            progress = await module.BatchRepository().inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
            assert progress.feed_complete is expected
            assert not progress.complete
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_batch_never_reopens_return_check_for_the_same_face() -> None:
    module = import_module("manual_picking.application.batch_repository")
    chunk_created = datetime(2026, 9, 15, 12)
    completed = chunk_created + timedelta(seconds=1)
    history = SimpleNamespace(
        latest_return=AsyncMock(
            return_value=(
                sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1)),
                completed,
            )
        )
    )
    repo = module.BatchRepository(history)
    db = SimpleNamespace(scalar=AsyncMock(return_value=chunk_created))
    assert not await repo.return_retry_due(db, 7, "R1", "90", 51, completed + timedelta(minutes=1), chunk_created)
    assert not await repo.return_retry_due(
        db, 7, "R1", "90", 51, completed + timedelta(minutes=1), completed + timedelta(seconds=1)
    )
    db.scalar.return_value = completed + timedelta(seconds=2)
    assert await repo.return_retry_due(db, 7, "R1", "90", 52, completed + timedelta(minutes=1), chunk_created)


@pytest.mark.asyncio
async def test_late_transport_publication_does_not_reopen_no_batch() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    arrived = datetime(2026, 9, 15, 12)
    completed = arrived + timedelta(seconds=1)
    try:
        async with sessions.begin() as db:
            db.add(
                TransportTask(
                    transport_task_id="source-1",
                    client_request_id="source-request-1",
                    request_digest="a" * 64,
                    kind="RACK_MOVE",
                    caller_json={},
                    request_json={},
                    submit_operation_id="source-op-1",
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="b" * 64,
                    status="SUCCEEDED",
                    authority_workline_id=7,
                    created_at=arrived,
                    updated_at=completed + timedelta(seconds=1),
                )
            )
            db.add(
                TransportDecisionBinding(
                    correlation_id="source-1",
                    step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                    workline_id=7,
                    resource_fence_id="R1",
                    client_request_id="source-request-1",
                    source_evidence_id=51,
                )
            )
            db.add(
                TransportEvidence(
                    operation_id="source-result-1",
                    transport_task_id="source-1",
                    operation="transport.task.resulted@v1",
                    outcome_revision=1,
                    event_timestamp_ms=1,
                    message_digest="c" * 64,
                    payload_json={},
                    ack_timestamp_ms=1,
                    ack_data_json={},
                    status="APPLIED",
                    received_at=arrived,
                    processed_at=arrived,
                )
            )
            history = SimpleNamespace(
                latest_return=AsyncMock(return_value=(sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1)), completed))
            )
            repo = module.BatchRepository(history)
            assert not await repo.return_retry_due(db, 7, "R1", "90", 51, completed, arrived)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PENDING", "ACCEPTED", "RECONCILING", "SUCCEEDED", "FAILED", "REJECTED"])
async def test_return_target_conflict_uses_only_active_frozen_targets(status: str) -> None:
    from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot

    repo = import_module("manual_picking.application.batch_repository").BatchRepository()
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            db.add(
                TransportTask(
                    transport_task_id="other-return",
                    client_request_id="other-client",
                    request_digest="a" * 64,
                    kind="BIN_MOVE",
                    caller_json={"workline_id": "99"},
                    request_json={},
                    submit_operation_id="other-submit",
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="b" * 64,
                    status=status,
                    created_at=datetime(2026, 9, 25),
                    updated_at=datetime(2026, 9, 25),
                )
            )
            await db.flush()
            db.add(
                TransportMember(
                    transport_task_id="other-return",
                    ordinal=1,
                    object_type="BIN",
                    object_id="BOX-A",
                    source_json={"kind": "HANDOFF_POSITION", "location_code": "OTHER-OUT"},
                    target_json={"kind": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": "S1"},
                    updated_at=datetime(2026, 9, 25),
                )
            )
            await db.flush()
            move = BinMove("BOX-B", HandoffPosition("OUT"), RackBinSlot("R1", "90", "S1"))
            assert await repo.has_conflicting_return_target(db, (move,)) == (
                status in {"PENDING", "ACCEPTED", "RECONCILING"}
            )
            for target in (
                RackBinSlot("R2", "90", "S1"),
                RackBinSlot("R1", "270", "S1"),
                RackBinSlot("R1", "90", "S2"),
            ):
                assert not await repo.has_conflicting_return_target(
                    db, (BinMove("BOX-B", HandoffPosition("OUT"), target),)
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence_state,blocked", [("RECONCILING", True), ("IGNORED", False), ("PENDING", True), ("APPLIED", True)]
)
async def test_current_return_response_waits_for_application(evidence_state, blocked):  # type: ignore[no-untyped-def]
    """本轮未闭合回架响应仍是直接依赖；旧轮次由独立用例验证。"""
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            await _seed_gate_windows(db)
            for operation_id in (
                "01a0d943-de51-71b8-ac6f-5a445a01db6e",
                "01a0d944-2cb6-73c3-b05c-1733c2f32bb0",
            ):
                evidence = InboundEvidence(
                    kind="WMS_RESULT",
                    source_identity=f"wms:{operation_id}",
                    payload_digest="b" * 64,
                    normalized_payload={"code": "DECIDED", "data": {"result": "READY"}},
                    received_at=datetime(2026, 9, 25, 15, 52),
                    workline_id=7,
                    operation="outbound.bin.return_batch@v1",
                    operation_id=operation_id,
                    apply_status=evidence_state,
                )
                db.add(evidence)
                await db.flush()
                db.add(
                    WmsConfirmation(
                        operation=evidence.operation,
                        operation_id=operation_id,
                        workline_id=7,
                        request_digest="a" * 64,
                        request_payload={"data": {"rack_id": "510002", "rack_face": "270"}},
                        deadline_at=datetime(2026, 9, 25, 15, 52),
                        status="COMPLETED",
                        response_evidence_id=evidence.id,
                    )
                )
            await db.flush()
            repo = import_module("manual_picking.application.batch_repository").BatchRepository()
            assert await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 8, "510002", "270") is blocked
            # 判定不能改写或发布异常证据，也不能伪造执行成功。
            assert evidence.apply_status == evidence_state
            assert evidence.published_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PENDING", "ACCEPTED", "RECONCILING", "SUCCEEDED", "FAILED", "REJECTED"])
@pytest.mark.parametrize("side", ["source", "target"])
async def test_mock_bad_case_ctu03_waits_only_for_current_rack_moves(status, side):  # type: ignore[no-untyped-def]
    repo = import_module("manual_picking.application.batch_repository").BatchRepository()
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 25, 19, 31)
    rack = {"kind": "RACK_BIN_SLOT", "rack_id": "510027", "rack_face": "90", "slot_id": "S1"}
    handoff = {"kind": "HANDOFF_POSITION", "location_code": "OUT"}
    try:
        async with sessions.begin() as db:
            task = TransportTask(
                transport_task_id="current-bin",
                client_request_id="current-client",
                request_digest="a" * 64,
                kind="BIN_MOVE",
                caller_json={"workline_id": "7"},
                authority_workline_id=7,
                request_json={},
                submit_operation_id="submit",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="b" * 64,
                status=status,
                created_at=now,
                updated_at=now,
            )
            db.add(task)
            await db.flush()
            member = TransportMember(
                transport_task_id=task.transport_task_id,
                ordinal=1,
                object_type="BIN",
                object_id="A000000341",
                source_json=rack if side == "source" else handoff,
                target_json=rack if side == "target" else handoff,
                updated_at=now,
            )
            db.add(member)
            await db.flush()
            assert await repo.has_current_rack_dependency(db, 7, "510027", now) is (
                status in {"PENDING", "ACCEPTED", "RECONCILING", "FAILED"}
            )
            assert await repo.has_active_bin_transport(db, "A000000341") is (
                status in {"PENDING", "ACCEPTED", "RECONCILING"}
            )
            assert not await repo.has_active_bin_transport(db, "OTHER-BIN")
            assert not await repo.has_current_rack_dependency(db, 8, "510027", now)
            assert not await repo.has_current_rack_dependency(db, 7, "OTHER", now)
            assert not await repo.has_current_rack_dependency(db, 7, "510027", now + timedelta(seconds=1))
            member.final_position_json = member.target_json
            member.position_unknown = False
            await db.flush()
            assert not await repo.has_current_rack_dependency(db, 7, "510027", now)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scan2_membership_preserves_entered_bin_after_source_cancellation() -> None:
    from manual_picking.application.batch_repository import BatchRepository

    engine, sessions = await _new_sessions()
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="019f0000-0000-7000-8000-000000000099",
        task_id="PICK-1",
        rack_id="R1",
        rack_face="90",
    )
    result = sdk.BinInboundBatchReady(
        bins=(
            sdk.BinInboundBatchMember(
                bin_code="BIN-1", source_locator=sdk.TransportRackBinSlot(rack_id="R1", rack_face="90", slot_id="S1")
            ),
        )
    )
    try:
        async with sessions.begin() as db:
            await _seed_current_inbound(db, intent, result)
            source = PickingTaskBinSourceRack(
                picking_task_id=31, plan_revision=1, rack_id="R1", rack_face="90", source_evidence_id=31
            )
            db.add(source)
            await db.flush()
            repo = BatchRepository()
            assert await repo.record_scan2(db, workline_id=7, picking_task_id=31, bin_code="BIN-1")
            assert not await repo.record_scan2(db, workline_id=8, picking_task_id=31, bin_code="BIN-1")
            assert not await repo.record_scan2(db, workline_id=7, picking_task_id=32, bin_code="BIN-1")
            assert not await repo.record_scan2(db, workline_id=7, picking_task_id=31, bin_code="BIN-2")
            source.cancelled_evidence_id = 31
            await db.flush()
            assert await repo.record_scan2(db, workline_id=7, picking_task_id=31, bin_code="BIN-1")
            scans = (await db.scalars(select(ManualPickingInboundBatchScan))).all()
            assert len(scans) == 1
            await db.delete(scans[0])
            await db.flush()
            assert not await repo.record_scan2(db, workline_id=7, picking_task_id=31, bin_code="BIN-1")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rack_return_dependency_checks_unpublished_response_before_transport() -> None:
    repo = import_module("manual_picking.application.batch_repository").BatchRepository()
    started_at = datetime(2026, 10, 13, 13, 53)
    db = SimpleNamespace(
        execute=AsyncMock(),
        scalar=AsyncMock(return_value=19),
        scalars=AsyncMock(return_value=[SimpleNamespace(created_at=started_at)]),
    )
    assert await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="510004")
    from sqlalchemy.dialects import postgresql

    sql = str(
        db.scalar.await_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "outbound.bin.return_batch@v1" in sql
    assert "510004" in sql
    assert "published_at IS NULL" in sql
    assert "DISPATCHING" in sql
    assert "rack_face" not in sql
    assert "wms_confirmations.created_at >= '2026-10-13 13:53:00'" in sql


@pytest.mark.asyncio
async def test_rack_return_dependency_checks_all_faces_without_closed_history_fence() -> None:
    repo = import_module("manual_picking.application.batch_repository").BatchRepository()
    db = SimpleNamespace(
        execute=AsyncMock(),
        scalar=AsyncMock(side_effect=[None, 253, None, None]),
        scalars=AsyncMock(return_value=[SimpleNamespace(created_at=datetime(2026, 10, 13, 13, 53))]),
    )
    assert await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="510004")
    assert not await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="510004")
    from sqlalchemy.dialects import postgresql

    sql = str(
        db.scalar.await_args_list[1]
        .args[0]
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "'ACCEPTED'" in sql
    assert "'510004'" in sql
    assert "transport_tasks.created_at >= '2026-10-13 13:53:00'" in sql
    assert "target_json ->> 'rack_face') =" not in sql


@pytest.mark.asyncio
async def test_mock_bad_case_a087_a000002668_preserves_original_return_dependency() -> None:
    case = json.loads((Path(__file__).parent / "fixtures/kt16_a087_a000002668_return_before_rotate.json").read_text())
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            task = TransportTask.model_validate(case["transport"])
            member = TransportMember.model_validate(case["member"])
            db.add_all([task, member])
            db.add(
                TransportDecisionBinding(
                    workline_id=348950323769920,
                    correlation_id="current-rack-window",
                    step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                    resource_fence_id="510004",
                    client_request_id="rack-arrival",
                    source_evidence_id=31,
                    window_target_location_code="KT16",
                    created_at=task.created_at - timedelta(seconds=1),
                )
            )
            await db.flush()
            repo = import_module("manual_picking.application.batch_repository").BatchRepository()
            frozen = (
                task.transport_task_id,
                task.client_request_id,
                task.submit_request_body,
                member.target_json.copy(),
            )
            for status in ("PENDING", "ACCEPTED", "RECONCILING"):
                task.status = status
                await db.flush()
                assert await repo.has_unclosed_return_dependency_for_rack(
                    db, workline_id=348950323769920, rack_id="510004"
                )
                with pytest.raises(ValueError, match="exactly one active inbound window"):
                    await repo.has_unclosed_return_dependency_for_rack(db, workline_id=348950323769920, rack_id="OTHER")
            assert (
                task.transport_task_id,
                task.client_request_id,
                task.submit_request_body,
                member.target_json,
            ) == frozen
            task.status = member.status = "SUCCEEDED"
            member.final_position_json = member.target_json.copy()
            task.outcome_version = 1
            task.published_outcome_version = 0
            await db.flush()
            assert await repo.has_unclosed_return_dependency_for_rack(db, workline_id=348950323769920, rack_id="510004")
            task.published_outcome_version = 1
            await db.flush()
            assert not await repo.has_unclosed_return_dependency_for_rack(
                db, workline_id=348950323769920, rack_id="510004"
            )
            await db.delete(member)
            await db.delete(task)
            await db.flush()
            assert not await repo.has_unclosed_return_dependency_for_rack(
                db, workline_id=348950323769920, rack_id="510004"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,final_known,final_matches,published,blocked",
    [
        ("PENDING", False, False, 0, True),
        ("ACCEPTED", False, False, 0, True),
        ("RECONCILING", False, False, 0, True),
        ("FAILED", False, False, 1, True),
        ("FAILED", True, False, 1, False),
        ("SUCCEEDED", True, False, 1, True),
        ("SUCCEEDED", True, True, 0, True),
        ("SUCCEEDED", True, True, 1, False),
    ],
)
@pytest.mark.parametrize("retired", [False, True])
async def test_rack_departure_ignores_old_failed_return_but_preserves_current_round_dependency(
    status, final_known, final_matches, published, blocked, retired
):
    engine, sessions = await _new_sessions()
    started_at = datetime(2026, 10, 13, 13, 53)
    target = {"kind": "RACK_BIN_SLOT", "rack_id": "510002", "rack_face": "270", "slot_id": "S1"}
    try:
        async with sessions.begin() as db:
            window = TransportDecisionBinding(
                workline_id=7,
                correlation_id="current-window",
                step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                resource_fence_id="510002",
                client_request_id="current-arrival",
                source_evidence_id=31,
                window_target_location_code="KT16",
                created_at=started_at,
            )
            db.add(window)
            if retired:
                window.window_released_at = started_at + timedelta(minutes=1)
            tasks = []
            members = []
            for identity, created_at in (
                ("old", datetime(2026, 9, 25)),
                ("current", started_at + timedelta(seconds=1)),
            ):
                old = identity == "old"
                task = TransportTask(
                    transport_task_id=identity,
                    client_request_id=identity,
                    request_digest="a" * 64,
                    kind="BIN_MOVE",
                    caller_json={"workline_id": "7"},
                    authority_workline_id=7,
                    request_json={},
                    submit_operation_id=identity,
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="b" * 64,
                    status="FAILED" if old else status,
                    reason_code="OPERATOR_CONFIRMED_CANCELLED" if old else None,
                    outcome_version=1,
                    published_outcome_version=1 if old else published,
                    created_at=created_at,
                    updated_at=started_at + timedelta(minutes=1),
                )
                position = None if old or not final_known else {**target, "slot_id": "S1" if final_matches else "OTHER"}
                member = TransportMember(
                    transport_task_id=identity,
                    ordinal=1,
                    object_type="BIN",
                    object_id=identity,
                    source_json={"kind": "HANDOFF_POSITION", "location_code": "OUT"},
                    target_json=target.copy(),
                    status=task.status,
                    final_position_json=position,
                    position_unknown=position is None,
                    updated_at=created_at,
                )
                tasks.append(task)
                members.append(member)
                db.add_all([task, member])
            await db.flush()
            repo = import_module("manual_picking.application.batch_repository").BatchRepository()
            scope = {"round_started_at": started_at} if retired else {}
            assert (
                await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="510002", **scope)
                is blocked
            )
            # 换面不新建窗口；其他面的本轮在途回架仍然属于整架依赖。
            assert window.created_at == started_at
            assert tasks[0].status == "FAILED" and tasks[0].reason_code == "OPERATOR_CONFIRMED_CANCELLED"
            assert members[0].final_position_json is None and members[0].position_unknown
            assert len((await db.scalars(select(TransportTask))).all()) == 2
            tasks[1].authority_workline_id = 8
            await db.flush()
            assert not await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="510002", **scope)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["outbound.bin.return_batch@v1", "outbound.bin.inbound_batch@v1"])
async def test_rack_departure_filters_old_confirmation_but_keeps_current_pending_delivery(operation):
    engine, sessions = await _new_sessions()
    started_at = datetime(2026, 10, 13, 13, 53)
    try:
        async with sessions.begin() as db:
            db.add(
                TransportDecisionBinding(
                    workline_id=7,
                    correlation_id="current-window",
                    step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                    resource_fence_id="510002",
                    client_request_id="current-arrival",
                    source_evidence_id=31,
                    window_target_location_code="KT16",
                    created_at=started_at,
                )
            )
            confirmation = WmsConfirmation(
                operation=operation,
                operation_id="return-delivery",
                workline_id=7,
                request_digest="a" * 64,
                request_payload={"data": {"rack_id": "510002", "rack_face": "270"}},
                deadline_at=started_at + timedelta(hours=1),
                status="PENDING",
                created_at=started_at - timedelta(days=1),
            )
            db.add(confirmation)
            await db.flush()
            repo = import_module("manual_picking.application.batch_repository").BatchRepository()
            assert not await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="510002")
            confirmation.created_at = started_at
            await db.flush()
            assert await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="510002")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("windows", [[], [SimpleNamespace(created_at=datetime(2026, 10, 13))] * 2])
async def test_rack_departure_requires_unique_current_window(windows):
    repo = import_module("manual_picking.application.batch_repository").BatchRepository()
    db = SimpleNamespace(execute=AsyncMock(), scalars=AsyncMock(return_value=windows), scalar=AsyncMock())
    repo.manual_source_arrival = AsyncMock(return_value=None)
    repo.working_rack_position = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="exactly one active inbound window"):
        await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="510002")
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_mock_a000002121_cancelled_source_allows_confirmed_inbound_without_scan1() -> None:
    case = json.loads((Path(__file__).parent / "fixtures/kt16_a087_a000002121_cancelled_source_scan2.json").read_text())
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            transport = TransportTask.model_validate(case["transport"])
            db.add_all(
                [
                    transport,
                    TransportMember.model_validate(case["member"]),
                    TransportDecisionBinding.model_validate(case["binding"]),
                    PickingTaskBinSourceRack.model_validate(case["source"]),
                    ManualPickingInboundBatch.model_validate(case["batch"]),
                    InboundEvidence.model_validate(case["evidence"]),
                ]
            )
            await db.flush()
            repo = import_module("manual_picking.application.batch_repository").BatchRepository()
            args = {"workline_id": 348950323769920, "picking_task_id": 359500596499008, "bin_code": "A000002121"}
            transport.status = "ACCEPTED"
            await db.flush()
            assert not await repo.record_scan2(db, **args)
            transport.status = "SUCCEEDED"
            transport.published_outcome_version = 0
            await db.flush()
            assert not await repo.record_scan2(db, **args)
            transport.published_outcome_version = transport.outcome_version
            binding = await db.get(TransportDecisionBinding, case["binding"]["id"])
            binding.resource_fence_id = "OTHER-BATCH"
            await db.flush()
            assert not await repo.record_scan2(db, **args)
            binding.resource_fence_id = case["binding"]["resource_fence_id"]
            member = await db.get(TransportMember, case["member"]["id"])
            member.final_position_json = {"kind": "HANDOFF_POSITION", "location_code": "OTHER"}
            await db.flush()
            assert not await repo.record_scan2(db, **args)
            member.final_position_json = case["member"]["final_position_json"]
            await db.flush()
            assert await repo.record_scan2(db, **args)
            assert await repo.record_scan2(db, **args)
            assert len((await db.scalars(select(ManualPickingInboundBatchScan))).all()) == 1
            assert not await repo.record_scan2(db, **{**args, "bin_code": "OTHER"})
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_status", ["SUCCEEDED", "FAILED", "ACCEPTED", "RECONCILING"])
async def test_old_606_unpublished_result_does_not_block_new_round_and_current_action_still_blocks(old_status):
    engine, sessions = await _new_sessions()
    start = datetime(2026, 10, 13, 22, 55)
    repo = import_module("manual_picking.application.batch_repository").BatchRepository()
    try:
        async with sessions.begin() as db:
            old = TransportTask(
                transport_task_id="old-606",
                client_request_id="old-606",
                kind="RACK_MOVE",
                authority_workline_id=7,
                caller_json={"workline_id": "7"},
                request_json={"rack_id": "510021"},
                request_digest="a" * 64,
                submit_operation_id="old-submit",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="b" * 64,
                status=old_status,
                outcome_version=1,
                published_outcome_version=0,
                created_at=start - timedelta(hours=2),
                updated_at=start,
            )
            old_binding = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=31,
                correlation_id="departure:original",
                step="MANUAL_PICKING_SOURCE_RACK_OUT",
                resource_fence_id="510021",
                source_evidence_id=101,
                client_request_id="old-606",
                causal_token=606,
                created_at=old.created_at,
            )
            root = TransportDecisionBinding(
                workline_id=7,
                correlation_id="current",
                step="MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN",
                resource_fence_id="510021",
                source_evidence_id=202,
                client_request_id="current",
                causal_token=664,
                window_target_location_code="KT16",
                created_at=start,
            )
            db.add_all([old, old_binding, root])
            await db.flush()
            assert not await repo.has_current_rack_dependency(db, 7, "510021", start)
            assert not await repo.has_newer_rack_action(db, rack_id="510021", causal_token=664)
            current = TransportTask(
                **{
                    **old.model_dump(exclude={"id"}),
                    "transport_task_id": "current",
                    "client_request_id": "current",
                    "submit_operation_id": "current-submit",
                    "status": "ACCEPTED",
                    "created_at": start,
                    "outcome_version": 0,
                    "published_outcome_version": 0,
                }
            )
            db.add(current)
            await db.flush()
            assert await repo.has_current_rack_dependency(db, 7, "510021", start)
            assert old.published_outcome_version == 0 and old.status == old_status
            assert old_binding.source_evidence_id == 101 and old_binding.causal_token == 606
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_completed_source_window_releases_admission_without_completing_old_transport():
    engine, sessions = await _new_sessions()
    now = datetime(2026, 10, 22)
    repo = import_module("manual_picking.application.batch_repository").BatchRepository()
    try:
        async with sessions.begin() as db:
            for task_id, status, line_id in (
                (11, "EXECUTION_COMPLETED", 7),
                (12, "EXECUTING", 7),
                (13, "CANCELLED", 7),
                (14, "EXECUTION_COMPLETED", 8),
            ):
                db.add(
                    PickingTask(
                        id=task_id,
                        task_id=f"PICK-{task_id}",
                        task_type="MANUAL",
                        status=status,
                        workline_id=line_id,
                        queue_revision=1,
                        dispatch_sequence=task_id,
                        issued_at_ms=1,
                        issued_evidence_id=task_id,
                        last_applied_plan_revision=0,
                    )
                )
                db.add(
                    TransportDecisionBinding(
                        workline_id=line_id,
                        picking_task_id=task_id,
                        correlation_id=f"source-{task_id}",
                        step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                        resource_fence_id=f"R{task_id}",
                        client_request_id=f"source-{task_id}",
                        source_evidence_id=task_id,
                        window_target_location_code="KT18",
                        created_at=now,
                    )
                )
            old = TransportTask(
                transport_task_id="old-625",
                client_request_id="old-625",
                kind="BIN_MOVE",
                authority_workline_id=7,
                caller_json={"workline_id": "7"},
                request_json={"bin_code": "625"},
                request_digest="a" * 64,
                submit_operation_id="original-operation",
                submit_timestamp_ms=1,
                submit_request_body="frozen-body",
                submit_request_body_digest="b" * 64,
                status="ACCEPTED",
                outcome_version=0,
                published_outcome_version=0,
                created_at=now,
                updated_at=now,
            )
            db.add(old)
            await db.flush()
            before = old.model_dump()
            assert await repo.release_completed_source_windows(db, workline_id=7, now=now) == 1
            windows = (
                await db.scalars(select(TransportDecisionBinding).order_by(TransportDecisionBinding.picking_task_id))
            ).all()
            assert [row.window_released_at for row in windows] == [now, None, None, None]
            assert windows[0].window_departure_client_request_id is None
            assert old.model_dump() == before
            assert await repo.release_completed_source_windows(db, workline_id=7, now=now) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("takeover", [False, True])
@pytest.mark.parametrize(
    "fact",
    ["ingress", "rotate", "missing", "unknown", "wrong_location", "wrong_transport", "wrong_token", "wrong_phase"],
)
async def test_completed_source_cleanup_keeps_original_causal_round_after_admission_release(takeover, fact):
    from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import (
        PickingTaskPlanDeltaRepository,
    )

    engine, sessions = await _new_sessions()
    now = datetime(2026, 10, 22)
    try:
        async with sessions.begin() as db:
            db.add(
                PickingTask(
                    id=11,
                    task_id="OLD",
                    task_type="MANUAL",
                    status="EXECUTION_COMPLETED",
                    workline_id=7,
                    queue_revision=1,
                    dispatch_sequence=1,
                    issued_at_ms=1,
                    issued_evidence_id=31,
                    last_applied_plan_revision=0,
                )
            )
            db.add(
                TransportDecisionBinding(
                    workline_id=7,
                    picking_task_id=11,
                    causal_token=100,
                    correlation_id="old-source",
                    step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                    resource_fence_id="R1",
                    client_request_id="old-in",
                    source_evidence_id=31,
                    window_target_location_code="KT18",
                    window_released_at=now,
                    created_at=now,
                )
            )
            db.add(
                TransportTask(
                    transport_task_id="old-arrival",
                    client_request_id="old-in",
                    kind="RACK_MOVE",
                    authority_workline_id=7,
                    caller_json={"workline_id": "7"},
                    request_json={},
                    request_digest="a" * 64,
                    submit_operation_id="old-operation",
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="b" * 64,
                    status="SUCCEEDED",
                    outcome_version=1,
                    published_outcome_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                PositionProjection(
                    object_type="RACK",
                    object_id="R1",
                    workline_id=7,
                    position_json={"kind": "RACK_POSITION", "location_code": "KT18"},
                    arrival_face="90",
                    position_unknown=False,
                    source_operation_id="original-fact",
                    source_causal_token=100,
                    source_effect_phase="FINAL_RESULT",
                    source_transport_task_id="old-arrival",
                )
            )
            await db.flush()
            projection = await db.scalar(select(PositionProjection))
            current_transport = "old-arrival"
            if fact == "rotate":
                current_transport = "rotation"
                ingress = await db.scalar(select(TransportTask))
                db.add(
                    TransportTask(
                        **{
                            **ingress.model_dump(exclude={"id"}),
                            "transport_task_id": "rotation",
                            "client_request_id": "rotation",
                            "submit_operation_id": "rotation",
                            "kind": "RACK_ROTATE",
                        }
                    )
                )
                db.add(
                    TransportDecisionBinding(
                        workline_id=7,
                        picking_task_id=11,
                        causal_token=101,
                        correlation_id="new-face",
                        step="MANUAL_PICKING_SOURCE_RACK_ROTATE",
                        resource_fence_id="R1",
                        client_request_id="rotation",
                        source_evidence_id=32,
                        created_at=now + timedelta(minutes=1),
                    )
                )
                projection.source_transport_task_id, projection.source_causal_token = "rotation", 101
            elif fact == "missing":
                await db.delete(projection)
            elif fact == "unknown":
                projection.position_unknown = True
            elif fact == "wrong_location":
                projection.position_json = {"kind": "RACK_POSITION", "location_code": "ELSEWHERE"}
            elif fact == "wrong_transport":
                projection.source_transport_task_id = "OTHER"
            elif fact == "wrong_token":
                projection.source_causal_token = 999
            elif fact == "wrong_phase":
                projection.source_effect_phase = "SUBMIT_ACCEPTED"
            if takeover:
                db.add(
                    TransportDecisionBinding(
                        workline_id=7,
                        picking_task_id=12,
                        causal_token=102,
                        correlation_id="new-source",
                        step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                        resource_fence_id="R1",
                        client_request_id="new-in",
                        source_evidence_id=32,
                        window_target_location_code="KT18",
                        created_at=now,
                    )
                )
            await db.flush()
            eligible = not takeover and fact in {"ingress", "rotate"}
            assert (
                await PickingTaskPlanDeltaRepository().source_transport_matches(db, 7, "R1", 11, current_transport)
                is eligible
            )
            if eligible:
                repo = import_module("manual_picking.application.batch_repository").BatchRepository()
                assert await repo.current_round_started_at(db, workline_id=7, rack_id="R1") == now
                assert not await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="R1")
                confirmation = WmsConfirmation(
                    operation="outbound.bin.return_batch@v1",
                    operation_id="current-return",
                    workline_id=7,
                    request_digest="a" * 64,
                    request_payload={"data": {"rack_id": "R1", "rack_face": "90"}},
                    deadline_at=now + timedelta(hours=1),
                    status="PENDING",
                    created_at=now,
                )
                db.add(confirmation)
                await db.flush()
                assert await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="R1")
                confirmation.status = "COMPLETED"
                await db.flush()
                assert not await repo.has_unclosed_return_dependency_for_rack(db, workline_id=7, rack_id="R1")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "accepted",
        "reconciling",
        "succeeded",
        "unpublished",
        "other_bin",
        "other_source",
        "other_target",
        "failed_unknown",
    ],
)
async def test_equivalent_inbound_guard_keeps_only_its_direct_dependency(case):
    from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot

    engine, sessions = await _new_sessions()
    now = datetime(2026, 10, 22)
    try:
        async with sessions.begin() as db:
            task = TransportTask(
                transport_task_id="original-625",
                client_request_id="original-625",
                kind="BIN_MOVE",
                authority_workline_id=7,
                caller_json={"workline_id": "7"},
                request_json={},
                request_digest="a" * 64,
                submit_operation_id="original-identity",
                submit_timestamp_ms=1,
                submit_request_body="frozen",
                submit_request_body_digest="b" * 64,
                status={
                    "reconciling": "RECONCILING",
                    "succeeded": "SUCCEEDED",
                    "unpublished": "SUCCEEDED",
                    "failed_unknown": "FAILED",
                }.get(case, "ACCEPTED"),
                outcome_version=1 if case in {"succeeded", "unpublished", "failed_unknown"} else 0,
                published_outcome_version=1 if case in {"succeeded", "failed_unknown"} else 0,
                created_at=now,
                updated_at=now,
            )
            member = TransportMember(
                transport_task_id=task.transport_task_id,
                ordinal=1,
                object_type="BIN",
                object_id="OTHER" if case == "other_bin" else "625",
                source_json={
                    "kind": "RACK_BIN_SLOT",
                    "rack_id": "R1",
                    "rack_face": "90",
                    "slot_id": "OTHER" if case == "other_source" else "S1",
                },
                target_json={
                    "kind": "HANDOFF_POSITION",
                    "location_code": "OTHER" if case == "other_target" else "INLET",
                },
                position_unknown=case == "failed_unknown",
                updated_at=now,
            )
            db.add_all([task, member])
            await db.flush()
            before = task.model_dump()
            blocked = (
                await import_module("manual_picking.application.batch_repository")
                .BatchRepository()
                .has_unclosed_equivalent_inbound(
                    db, (BinMove("625", RackBinSlot("R1", "90", "S1"), HandoffPosition("INLET")),)
                )
            )
            assert blocked is (case in {"accepted", "reconciling", "unpublished", "failed_unknown"})
            assert task.model_dump() == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("rack_id", ["CURRENT", "OTHER"])
async def test_site_current_arrival_is_consumed_without_closing_original_transport(rack_id):
    engine, sessions = await _new_sessions()
    now = datetime(2026, 10, 23)
    repo = import_module("manual_picking.application.batch_repository").BatchRepository()
    try:
        async with sessions.begin() as db:
            binding = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=11,
                correlation_id="current-round",
                causal_token=100,
                step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                resource_fence_id="CURRENT",
                client_request_id="original-arrival",
                source_evidence_id=31,
            )
            original = TransportTask(
                transport_task_id="original-arrival",
                client_request_id="original-arrival",
                kind="RACK_MOVE",
                authority_workline_id=7,
                caller_json={},
                request_json={},
                request_digest="a" * 64,
                submit_operation_id="original-operation",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="b" * 64,
                status="ACCEPTED",
                created_at=now,
                updated_at=now,
            )
            arrival = ManualPickingSourceArrival(
                workline_id=7,
                picking_task_id=11,
                source_evidence_id=31,
                source_causal_token=100,
                source_transport_task_id="original-arrival",
                plan_revision=1,
                rack_id="CURRENT",
                rack_face="90",
                location_code="SOURCE",
                observed_at=now,
                audit_id=42,
                confirmation_digest="c" * 64,
            )
            db.add_all([binding, original, arrival])
            await db.flush()
            assert await repo.manual_source_arrival(db, workline_id=7) is arrival
            current = await repo.working_rack_position(db, workline_id=7, rack_id=rack_id, location_code="SOURCE")
            assert current is (arrival if rack_id == "CURRENT" else None)
            assert original.status == "ACCEPTED"
            assert original.submit_operation_id == "original-operation"
    finally:
        await engine.dispose()
