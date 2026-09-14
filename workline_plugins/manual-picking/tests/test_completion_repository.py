"""完成前提只检查本任务未结业务，不把退箱清场算进来。"""

from datetime import datetime
from types import SimpleNamespace

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.completion_repository import ManualPickingCompletionRepository
from manual_picking.application.passage_model import ManualPickingPassage
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.execution.models import InboundEvidence, TransportDecisionBinding
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, PickingTaskBinSourceRack


@pytest.mark.asyncio
async def test_completion_requires_closed_source_or_known_failure_and_no_unfinished_wms_work() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")
        connection.execute("ATTACH DATABASE ':memory:' AS wes_runtime")

    async with engine.begin() as connection:
        for table in (
            InboundEvidence.__table__,
            DirectPickExecution.__table__,
            PickingTaskBinSourceRack.__table__,
            ManualPickingPassage.__table__,
            TransportDecisionBinding.__table__,
            TransportTask.__table__,
            TransportMember.__table__,
        ):
            await connection.run_sync(table.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class History:
        done = False

        async def latest_inbound(self, _db, *, workline_id, task_id, rack_id, rack_face):  # type: ignore[no-untyped-def]
            assert (workline_id, task_id, rack_id, rack_face) == (7, "PICK-1", "R1", "90")
            return (
                (sdk.BinInboundBatchOutcome(sdk.BinInboundBatchRackFaceDone()), datetime(2026, 9, 14, 4))
                if self.done
                else None
            )

    history = History()
    repository = ManualPickingCompletionRepository(history=history)
    line = SimpleNamespace(
        id=7,
        config={
            "device_bindings": {
                "SCAN1": "S1",
                "SCAN2": "S2",
                "SCAN3": "S3",
                "SCAN4": "S4",
            }
        },
    )
    task = SimpleNamespace(id=11, task_id="PICK-1", last_applied_plan_revision=1)
    try:
        async with sessions.begin() as db:
            db.add(
                PickingTaskBinSourceRack(
                    picking_task_id=11, rack_id="R1", rack_face="90", plan_revision=1, source_evidence_id=1
                )
            )
            await db.flush()
            assert not await repository.ready_to_confirm(db, line, task)
            history.done = True
            assert await repository.ready_to_confirm(db, line, task)

            pending_scan = InboundEvidence(
                kind="DEVICE_EVENT",
                source_identity="scan-pending",
                payload_digest="a" * 64,
                normalized_payload={},
                received_at=datetime(2026, 9, 14, 4),
                workline_id=7,
                device_code="S1",
                apply_status="APPLIED",
            )
            db.add(pending_scan)
            await db.flush()
            assert not await repository.ready_to_confirm(db, line, task)
            pending_scan.published_at = datetime(2026, 9, 14, 4)
            pending_scan.decision_digest = "b" * 64
            await db.flush()
            assert await repository.ready_to_confirm(db, line, task)

            passage = ManualPickingPassage(
                workline_id=7,
                task_id="PICK-1",
                bin_code="A000000001",
                scan1_evidence_id=1,
                scan1_received_at=datetime(2026, 9, 14, 4),
                disposition="OPEN",
                return_state="NONE",
            )
            db.add(passage)
            await db.flush()
            assert not await repository.ready_to_confirm(db, line, task)
            passage.wms_result = "NORMAL"
            passage.disposition = "NORMAL"
            passage.return_state = "READY"
            await db.flush()
            assert await repository.ready_to_confirm(db, line, task)

            direct_pick = DirectPickExecution(
                picking_task_id=11,
                rack_id="R2",
                rack_face="90",
                slot_id="S1",
                plan_revision=1,
                source_evidence_id=1,
            )
            db.add(direct_pick)
            await db.flush()
            assert not await repository.ready_to_confirm(db, line, task)
            await db.delete(direct_pick)
            history.done = False
            binding = TransportDecisionBinding(
                correlation_id="pt:11:e:1:rack:R1",
                step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                workline_id=7,
                resource_fence_id="R1",
                client_request_id="rack-client-1",
                source_evidence_id=1,
            )
            db.add(binding)
            transport = TransportTask(
                transport_task_id="rack-transport-1",
                client_request_id="rack-client-1",
                request_digest="d" * 64,
                kind="RACK_MOVE",
                caller_json={"workline_id": "7"},
                request_json={"rack_id": "R1"},
                submit_operation_id="rack-operation-1",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="e" * 64,
                status="FAILED",
                authority_workline_id=7,
                outcome_version=1,
                published_outcome_version=1,
                created_at=datetime(2026, 9, 14, 4),
                updated_at=datetime(2026, 9, 14, 4),
            )
            db.add(transport)
            member = TransportMember(
                transport_task_id="rack-transport-1",
                ordinal=1,
                object_type="RACK",
                object_id="R1",
                source_json={"kind": "RACK_POSITION", "location_code": "WH05"},
                target_json={"kind": "RACK_POSITION", "location_code": "FIVE-POS"},
                status="FAILED",
                final_position_json={"kind": "RACK_POSITION", "location_code": "WH05"},
                position_unknown=False,
                failure_code="MOVE_FAILED",
                updated_at=datetime(2026, 9, 14, 4),
            )
            db.add(member)
            await db.flush()
            assert await repository.ready_to_confirm(db, line, task)
            binding.source_evidence_id = 2
            await db.flush()
            assert not await repository.ready_to_confirm(db, line, task)
            binding.source_evidence_id = 1
            member.position_unknown = True
            member.final_position_json = None
            await db.flush()
            assert not await repository.ready_to_confirm(db, line, task)
            transport.status = "REJECTED"
            member.status = "PENDING"
            member.position_unknown = False
            transport.published_outcome_version = 0
            await db.flush()
            assert not await repository.ready_to_confirm(db, line, task)
            transport.published_outcome_version = 1
            await db.flush()
            assert await repository.ready_to_confirm(db, line, task)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_target_only_plan_waits_for_confirmed_target_transport() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    async with engine.begin() as connection:
        for table in (InboundEvidence.__table__, DirectPickExecution.__table__, ManualPickingPassage.__table__):
            await connection.run_sync(table.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class Plans:
        async def list_bin_source_racks(self, _db, _task_id):  # type: ignore[no-untyped-def]
            return []

    class Positions:
        projection = None

        async def get(self, _db, _kind, _rack_id):  # type: ignore[no-untyped-def]
            return self.projection

    class Transports:
        async def get_task(self, _db, _task_id):  # type: ignore[no-untyped-def]
            return SimpleNamespace(status="SUCCEEDED")

    positions = Positions()
    repository = ManualPickingCompletionRepository(plans=Plans(), positions=positions, transports=Transports())
    line = SimpleNamespace(
        id=7,
        config={
            "device_bindings": {"SCAN1": "S1", "SCAN2": "S2", "SCAN3": "S3", "SCAN4": "S4"},
            "position_bindings": {
                "FIVE_RACK": "FIVE-POS",
                "RETURN_RACK": "RETURN-POS",
                "TRANSFER_RACK": "TRANSFER-POS",
                "INLET": "INLET-POS",
                "OUTLET": "OUTLET-POS",
            },
        },
        position_bindings={"TRANSFER_RACK": {"location_id": "TRANSFER-POS"}},
    )
    task = SimpleNamespace(
        id=11,
        task_id="PICK-1",
        target_rack_id="TARGET-1",
        target_rack_face="270",
        last_applied_plan_revision=1,
    )
    try:
        async with sessions.begin() as db:
            assert not await repository.ready_to_confirm(db, line, task)
            positions.projection = SimpleNamespace(
                workline_id=7,
                position_unknown=False,
                position_json={"kind": "RACK_POSITION", "location_code": "TRANSFER-POS"},
                arrival_face="270",
                source_transport_task_id="target-move",
            )
            assert await repository.ready_to_confirm(db, line, task)
    finally:
        await engine.dispose()
