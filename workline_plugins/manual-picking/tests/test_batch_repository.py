"""批次互斥只依赖本线未闭合可靠义务与物理结果。"""

from datetime import datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.passage_model import ManualPickingPassage
from sqlalchemy import event
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
from src.app.workline.models import WorkLine


async def _new_sessions():  # type: ignore[no-untyped-def]
    _ = (MaterialExecution, PickingTask, WorkLine)  # 注册跨表 FK 元数据；无需创建业务表。
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schemas(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    async with engine.begin() as connection:
        for table in (
            InboundEvidence.__table__,
            WmsConfirmation.__table__,
            TransportTask.__table__,
            TransportEvidence.__table__,
            TransportMember.__table__,
            PositionProjection.__table__,
            PickingTask.__table__,
            TransportDecisionBinding.__table__,
            ManualPickingPassage.__table__,
        ):
            await connection.run_sync(table.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_inbound_results_for_same_rack_remain_revision_scoped() -> None:
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
                        "data": {"task_id": "PICK-1", "plan_revision": revision, "rack_id": "A", "rack_face": "90"},
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
            for revision in (1, 2):
                intent, _, _, _ = await reader.latest_inbound_detail(
                    db, workline_id=7, task_id="PICK-1", plan_revision=revision, rack_id="A", rack_face="90"
                )
                assert intent.plan_revision == revision
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
            repo = module.BatchRepository()
            db.add(
                WmsConfirmation(
                    operation="outbound.bin.inbound_batch@v1",
                    operation_id="019f0000-0000-7000-8000-000000000101",
                    workline_id=7,
                    request_digest="a" * 64,
                    request_payload={
                        "operation": "outbound.bin.inbound_batch@v1",
                        "data": {"task_id": "PICK-2", "plan_revision": 1, "rack_id": "R2", "rack_face": "270"},
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
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_face_gate_blocks_unpublished_wms_result_and_other_workline_or_unrelated_history() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    try:
        async with sessions.begin() as db:
            repo = module.BatchRepository()
            # 1) PENDING confirmation 同 workline/同 task/同 face 阻塞;不同 workline 不阻塞。
            confirmation = WmsConfirmation(
                operation="outbound.bin.inbound_batch@v1",
                operation_id="019f0000-0000-7000-8000-000000000201",
                workline_id=7,
                request_digest="a" * 64,
                request_payload={
                    "operation": "outbound.bin.inbound_batch@v1",
                    "data": {"task_id": "PICK-1", "plan_revision": 1, "rack_id": "R1", "rack_face": "90"},
                },
                deadline_at=now,
                status="PENDING",
            )
            db.add(confirmation)
            await db.flush()
            assert await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")
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
async def test_face_gate_transport_state_transitions_release_only_after_publication() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    try:
        async with sessions.begin() as db:
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
            db.add(
                TransportMember(
                    transport_task_id="transport-1",
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
            task.status = "SUCCEEDED"
            task.outcome_version = 1
            assert await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")
            task.published_outcome_version = 1
            await db.flush()
            assert not await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")
            task.status = "FAILED"
            await db.flush()
            assert not await repo.has_unclosed_action_for_face(db, 7, "PICK-1", 1, "R1", "90")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_batch_retry_and_face_done_are_derived_from_matched_wms_results() -> None:
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
                ManualPickingPassage(
                    workline_id=7,
                    task_id="PICK-1",
                    bin_code="A000000002",
                    scan1_evidence_id=return_evidence.id,
                    scan1_received_at=now - timedelta(milliseconds=2),
                    scan4_evidence_id=return_evidence.id,
                    scan4_received_at=now - timedelta(milliseconds=1),
                    scan4_command_code="MOVE-1",
                    return_state="READY",
                )
            )
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
                    "data": {"task_id": "PICK-1", "plan_revision": 1, "rack_id": "R1", "rack_face": "90"},
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
    now = datetime(2026, 9, 13, 12)
    history = type("History", (), {})()
    history.latest_inbound_detail = AsyncMock(
        return_value=(
            sdk.wms_operations.outbound_bin_inbound_batch(
                operation_id="batch-1", task_id="PICK-1", plan_revision=1, rack_id="R1", rack_face="90"
            ),
            sdk.BinInboundBatchOutcome(sdk.BinInboundBatchRackFaceDone()),
            SimpleNamespace(id=31),
            now,
        )
    )
    repo = module.BatchRepository(history)
    db = SimpleNamespace(scalar=AsyncMock(return_value=13 if has_chunk else None))
    progress = await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
    assert progress.feed_complete is (not has_chunk)
    assert progress.complete is (not has_chunk)
    assert progress.next_offset is None


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
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-1", task_id="PICK-1", plan_revision=1, rack_id="R1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady(
        tuple(
            sdk.BinInboundBatchMember(f"BIN-{index}", sdk.TransportRackBinSlot("R1", "90", f"S-{index}"))
            for index in range(1, bin_count + 1)
        )
    )
    history = SimpleNamespace(
        latest_inbound_detail=AsyncMock(
            return_value=(intent, sdk.BinInboundBatchOutcome(ready), SimpleNamespace(id=31), now)
        )
    )
    repo = module.BatchRepository(history)

    def add_chunk(db, offset):  # type: ignore[no-untyped-def]
        db.add(
            TransportDecisionBinding(
                workline_id=7,
                correlation_id=f"batch-1:{offset}",
                step="MANUAL_PICKING_INBOUND_BATCH",
                resource_fence_id="batch-1",
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
            progress = await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
            assert progress.next_offset == 0 and not progress.complete
            add_chunk(db, 0)
            await db.flush()
            assert (await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")).next_offset is None
            for index in range(1, 5):
                db.add(
                    ManualPickingPassage(
                        workline_id=7,
                        task_id="PICK-1",
                        bin_code=f"BIN-{index}",
                        scan1_evidence_id=index,
                        scan1_received_at=now,
                        disposition="OPEN",
                        return_state="NONE",
                    )
                )
            await db.flush()
            assert (await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")).next_offset == 4
            for offset in range(4, bin_count, 4):
                add_chunk(db, offset)
            await db.flush()
            progress = await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
            assert progress.feed_complete and not progress.complete
            for index in range(5, bin_count + 1):
                db.add(
                    ManualPickingPassage(
                        workline_id=7,
                        task_id="PICK-1",
                        bin_code=f"BIN-{index}",
                        scan1_evidence_id=index,
                        scan1_received_at=now,
                        disposition="OPEN",
                        return_state="NONE",
                    )
                )
            await db.flush()
            queries.clear()
            progress = await repo.inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
            assert progress.complete and progress.feed_complete and progress.next_offset is None
            assert progress.last_chunk_created_at == now
            assert len(queries) <= 3, f"{bin_count} bins required {len(queries)} SELECTs"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_frozen_face_does_not_redispatch_legacy_inbound_binding() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-legacy", task_id="PICK-1", plan_revision=1, rack_id="R1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady((sdk.BinInboundBatchMember("BIN-1", sdk.TransportRackBinSlot("R1", "90", "S-1")),))
    history = SimpleNamespace(
        latest_inbound_detail=AsyncMock(
            return_value=(intent, sdk.BinInboundBatchOutcome(ready), SimpleNamespace(id=31), now)
        )
    )
    try:
        async with sessions.begin() as db:
            db.add(
                TransportDecisionBinding(
                    workline_id=7,
                    correlation_id="batch-legacy",
                    step="MANUAL_PICKING_INBOUND_BATCH",
                    resource_fence_id="batch-legacy",
                    source_evidence_id=31,
                    client_request_id="legacy-client",
                )
            )
            await db.flush()
            with pytest.raises(ValueError, match="legacy inbound binding"):
                await module.BatchRepository(history).inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
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
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="feed-1", task_id="PICK-1", plan_revision=1, rack_id="R1", rack_face="90"
    )
    result = sdk.BinInboundBatchReady((sdk.BinInboundBatchMember("BIN-1", sdk.TransportRackBinSlot("R1", "90", "S1")),))
    history = SimpleNamespace(
        latest_inbound_detail=AsyncMock(
            return_value=(
                intent,
                sdk.BinInboundBatchOutcome(result),
                SimpleNamespace(id=31, published_at=now),
                now,
            )
        )
    )
    try:
        async with sessions.begin() as db:
            if case != "missing_transport":
                for index in range(1):
                    db.add(
                        TransportDecisionBinding(
                            workline_id=7,
                            correlation_id="feed-1:0",
                            step="MANUAL_PICKING_INBOUND_BATCH",
                            resource_fence_id="feed-1",
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
                        return_state="NONE",
                    )
                )
            await db.flush()
            progress = await module.BatchRepository(history).inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
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
async def test_duplicate_transport_rows_do_not_complete_or_redispatch_a_chunk() -> None:
    module = import_module("manual_picking.application.batch_repository")
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-1", task_id="PICK-1", plan_revision=1, rack_id="R1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady((sdk.BinInboundBatchMember("BIN-1", sdk.TransportRackBinSlot("R1", "90", "S1")),))
    history = SimpleNamespace(
        latest_inbound_detail=AsyncMock(
            return_value=(
                intent,
                sdk.BinInboundBatchOutcome(ready),
                SimpleNamespace(id=31),
                datetime(2026, 9, 15, 12),
            )
        )
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [("batch-1:0", object()), ("batch-1:0", object())])),
        scalars=AsyncMock(side_effect=[[], SimpleNamespace(all=list)]),
    )
    progress = await module.BatchRepository(history).inbound_progress(db, 7, "PICK-1", 1, "R1", "90", "CNV0301")
    assert not progress.feed_complete and not progress.complete and progress.next_offset is None
