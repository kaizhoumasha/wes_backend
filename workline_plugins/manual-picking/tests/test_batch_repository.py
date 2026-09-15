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

from src.app.execution.models import InboundEvidence, MaterialExecution, TransportDecisionBinding, WmsConfirmation
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.workline.models import WorkLine


async def _new_sessions():  # type: ignore[no-untyped-def]
    _ = (MaterialExecution, PickingTask, WorkLine)  # 注册跨表 FK 元数据；无需创建业务表。
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schemas(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")
        connection.execute("ATTACH DATABASE ':memory:' AS wes_runtime")

    async with engine.begin() as connection:
        for table in (
            InboundEvidence.__table__,
            WmsConfirmation.__table__,
            TransportTask.__table__,
            TransportDecisionBinding.__table__,
            ManualPickingPassage.__table__,
        ):
            await connection.run_sync(table.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_batch_gate_waits_for_wms_result_application_and_transport_publication() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    try:
        async with sessions.begin() as db:
            repo = module.BatchRepository()
            assert not await repo.has_unclosed_action(db, 7)
            confirmation = WmsConfirmation(
                operation="outbound.bin.inbound_batch@v1",
                operation_id="019f0000-0000-7000-8000-000000000001",
                workline_id=7,
                request_digest="a" * 64,
                request_payload={"operation": "outbound.bin.inbound_batch@v1"},
                deadline_at=now,
                status="PENDING",
            )
            db.add(confirmation)
            await db.flush()
            assert await repo.has_unclosed_action(db, 7)
            assert not await repo.has_unclosed_action(db, 8)
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
            assert await repo.has_unclosed_action(db, 7)
            evidence.published_at = now
            evidence.decision_digest = "e" * 64
            await db.flush()
            assert not await repo.has_unclosed_action(db, 7)

            db.add(
                InboundEvidence(
                    kind="WMS_RESULT",
                    source_identity="wms:unassociated-history",
                    payload_digest="1" * 64,
                    normalized_payload={"code": "DECIDED", "data": {"result": "RACK_FACE_DONE"}},
                    received_at=now,
                    workline_id=7,
                    operation="outbound.bin.inbound_batch@v1",
                    operation_id="019f0000-0000-7000-8000-000000000099",
                    apply_status="APPLIED",
                )
            )
            await db.flush()
            assert not await repo.has_unclosed_action(db, 7)

            old_rack_result = InboundEvidence(
                kind="TRANSPORT_RESULT",
                source_identity="transport:old-rack:outcome:1",
                payload_digest="f" * 64,
                normalized_payload={"step": "PICKING_TASK_BIN_SOURCE_RACK_IN"},
                received_at=now,
                workline_id=7,
                transport_task_id="old-rack",
                apply_status="APPLIED",
            )
            db.add(old_rack_result)
            await db.flush()
            assert not await repo.has_unclosed_action(db, 7)

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
            await db.flush()
            assert await repo.has_unclosed_action(db, 7)
            task.status = "SUCCEEDED"
            task.outcome_version = 1
            assert await repo.has_unclosed_action(db, 7)
            task.published_outcome_version = 1
            await db.flush()
            assert not await repo.has_unclosed_action(db, 7)
            task.status = "FAILED"
            await db.flush()
            assert not await repo.has_unclosed_action(db, 7)
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
            assert await repo.return_retry_due(db, 7, "R1", "90", now)
            assert await repo.inbound_progress(db, 7, "PICK-1", "R1", "90") is None
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
            assert not await repo.return_retry_due(db, 7, "R1", "90", now + timedelta(milliseconds=999))
            assert await repo.return_retry_due(db, 7, "R1", "90", now + timedelta(milliseconds=1000))
            assert await repo.return_retry_due(db, 7, "R1", "270", now)
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
            assert await repo.return_retry_due(db, 7, "R1", "90", now + timedelta(milliseconds=1))

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
            assert (await repo.inbound_progress(db, 7, "PICK-1", "R1", "90")).complete
            assert await repo.inbound_progress(db, 7, "PICK-1", "R1", "270") is None
            assert await repo.inbound_progress(db, 8, "PICK-1", "R1", "90") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_inbound_closed_face_does_not_request_again() -> None:
    module = import_module("manual_picking.application.batch_repository")
    now = datetime(2026, 9, 13, 12)
    history = type("History", (), {})()
    history.latest_inbound_detail = AsyncMock(
        return_value=(
            sdk.wms_operations.outbound_bin_inbound_batch(
                operation_id="batch-1", task_id="PICK-1", rack_id="R1", rack_face="90"
            ),
            sdk.BinInboundBatchOutcome(sdk.BinInboundBatchRackFaceDone()),
            SimpleNamespace(id=31),
            now,
        )
    )
    repo = module.BatchRepository(history)
    assert (await repo.inbound_progress(object(), 7, "PICK-1", "R1", "90")).complete


@pytest.mark.asyncio
async def test_frozen_face_advances_only_after_transport_success_and_matching_scans() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-1", task_id="PICK-1", rack_id="R1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady(
        tuple(
            sdk.BinInboundBatchMember(f"BIN-{index}", sdk.TransportRackBinSlot("R1", "90", f"S-{index}"))
            for index in range(1, 6)
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

    try:
        async with sessions.begin() as db:
            progress = await repo.inbound_progress(db, 7, "PICK-1", "R1", "90")
            assert progress.next_offset == 0 and not progress.complete
            add_chunk(db, 0)
            await db.flush()
            assert (await repo.inbound_progress(db, 7, "PICK-1", "R1", "90")).next_offset is None
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
            assert (await repo.inbound_progress(db, 7, "PICK-1", "R1", "90")).next_offset == 4
            add_chunk(db, 4)
            db.add(
                ManualPickingPassage(
                    workline_id=7,
                    task_id="PICK-1",
                    bin_code="BIN-5",
                    scan1_evidence_id=5,
                    scan1_received_at=now,
                    disposition="OPEN",
                    return_state="NONE",
                )
            )
            await db.flush()
            assert (await repo.inbound_progress(db, 7, "PICK-1", "R1", "90")).complete
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_frozen_face_does_not_redispatch_legacy_inbound_binding() -> None:
    module = import_module("manual_picking.application.batch_repository")
    engine, sessions = await _new_sessions()
    now = datetime(2026, 9, 13, 12)
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-legacy", task_id="PICK-1", rack_id="R1", rack_face="90"
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
                await module.BatchRepository(history).inbound_progress(db, 7, "PICK-1", "R1", "90")
    finally:
        await engine.dispose()
