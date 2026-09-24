"""PostgreSQL candidate scan for durable facts and causal projection order."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.models.position_projection import PositionProjection
from src.app.execution.models.transport_decision_binding import TransportDecisionBinding
from src.app.transport.models import TransportMember, TransportTask
from src.app.transport.repository import TransportRepository
from src.app.wms_adapter.return_buffer_drain.wire import RETURN_BUFFER_DRAIN_OPERATION
from src.app.wms_integration.outbound_picking.models import (
    PickingTask,
    PickingTaskBinSourceRack,
    PickingTaskStatus,
    PickingTaskType,
)
from src.app.workline.models import WorkLine
from src.app.workline.models.workline import LineType
from src.core.uuid7 import new_uuid7

pytestmark = pytest.mark.asyncio


class _CaptureExecute:
    def __init__(self, db):
        self.db = db
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return await self.db.execute(statement)


def _transport(identity: str, suffix: str, line_id: int, status: str, updated_at: datetime) -> TransportTask:
    return TransportTask(
        transport_task_id=f"T5-PG-TASK-{identity}-{suffix}",
        client_request_id=f"T5-PG-REQUEST-{identity}-{suffix}",
        request_digest="a" * 64,
        kind="RACK_MOVE",
        caller_json={"workline_id": str(line_id)},
        request_json={"rack_id": f"T5-PG-RACK-{identity}-{suffix}"},
        submit_operation_id=new_uuid7(),
        submit_timestamp_ms=1_800_000_000_000,
        submit_request_body="{}",
        submit_request_body_digest="b" * 64,
        status=status,
        authority_workline_id=line_id,
        created_at=updated_at,
        updated_at=updated_at,
    )


def _member(task: TransportTask, updated_at: datetime, *, status: str) -> TransportMember:
    return TransportMember(
        transport_task_id=task.transport_task_id,
        ordinal=0,
        object_type="RACK",
        object_id=task.request_json["rack_id"],
        source_json={"kind": "RACK", "location_code": "SOURCE"},
        target_json={"kind": "RACK_POSITION", "location_code": "TARGET"},
        status=status,
        final_position_json={"kind": "RACK_POSITION", "location_code": "TARGET"},
        position_unknown=False,
        arrival_face="A",
        last_operation_id=new_uuid7(),
        updated_at=updated_at,
    )


async def _drain_evidence(db, *, identity: str, line_id: int, operation_id: str, now: datetime):
    confirmation = WmsConfirmation(
        operation=RETURN_BUFFER_DRAIN_OPERATION,
        operation_id=operation_id,
        workline_id=line_id,
        request_digest="c" * 64,
        request_payload={"operation_id": operation_id, "data": {}},
        deadline_at=now + timedelta(minutes=1),
        status=WmsConfirmationStatus.COMPLETED,
        response_result="READY",
        completed_at=now,
    )
    db.add(confirmation)
    await db.flush()
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"T5-PG-DRAIN-{identity}-{operation_id}",
        payload_digest="d" * 64,
        normalized_payload={"operation_id": operation_id, "data": {"result": "READY"}},
        received_at=now,
        workline_id=line_id,
        operation=RETURN_BUFFER_DRAIN_OPERATION,
        operation_id=operation_id,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
        processed_at=now,
        published_at=now,
        decision_digest="f" * 64,
    )
    db.add(evidence)
    await db.flush()
    confirmation.response_evidence_id = evidence.id
    await db.flush()
    return evidence


async def _add_binding(db, task, *, line_id: int, evidence_id: int, picking_task_id: int | None) -> None:
    db.add(
        TransportDecisionBinding(
            correlation_id=f"T5-PG-{task.client_request_id}",
            step="T5_POSTGRESQL_CANDIDATE",
            workline_id=line_id,
            picking_task_id=picking_task_id,
            resource_fence_id=task.request_json["rack_id"],
            client_request_id=task.client_request_id,
            source_evidence_id=evidence_id,
        )
    )


async def test_projection_scan_exposes_missing_recovery_authority(integration_session_factory) -> None:
    identity = uuid4().hex
    now = datetime(2026, 9, 23, 12)
    repository = TransportRepository()
    async with integration_session_factory() as db:
        transaction = await db.begin()
        try:
            line = WorkLine(
                line_code=f"ORPHAN-{identity[:20]}",
                line_name="Projection orphan scan",
                line_type=LineType.AUTO,
                is_active=True,
            )
            db.add(line)
            await db.flush()
            tasks = [
                _transport(identity, suffix, line.id, "SUCCEEDED", now) for suffix in ("missing", "zero", "source")
            ]
            db.add_all(tasks)
            await db.flush()
            db.add_all(_member(task, now, status="SUCCEEDED") for task in tasks)
            await db.flush()
            evidence = InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=f"ORPHAN-{identity}",
                device_code="ORPHAN-DEVICE",
                payload_digest="a" * 64,
                normalized_payload={},
                received_at=now,
                workline_id=line.id,
            )
            db.add(evidence)
            await db.flush()
            for task in tasks[1:]:
                await _add_binding(db, task, line_id=line.id, evidence_id=evidence.id, picking_task_id=None)
            await db.flush()
            zero_binding = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == tasks[1].client_request_id
                )
            )
            zero_binding.causal_token = 0
            db.add(
                PositionProjection(
                    object_type="RACK",
                    object_id=tasks[2].request_json["rack_id"],
                    workline_id=line.id,
                    source_operation_id=new_uuid7(),
                    source_transport_task_id="earlier-task",
                )
            )
            await db.flush()

            rows = await repository.list_projection_recovery_orphans(db, limit=100)

            assert {(row[0], row[3]) for row in rows} == {
                (tasks[0].transport_task_id, "MISSING_BINDING"),
                (tasks[1].transport_task_id, "NONPOSITIVE_CAUSAL_TOKEN"),
                (tasks[2].transport_task_id, "INCOMPLETE_PROJECTION_PROVENANCE"),
            }
        finally:
            await transaction.rollback()


async def test_mixed_picking_and_drain_candidates_are_stable_and_aba_fenced(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 9, 22, 12)
    repository = TransportRepository()
    async with integration_session_factory() as db:
        transaction = await db.begin()
        try:
            line = WorkLine(
                line_code=f"T5-PG-{identity[:20]}",
                line_name="T5 mixed projection candidates",
                line_type=LineType.AUTO,
                is_active=True,
            )
            db.add(line)
            await db.flush()

            issued = InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=f"T5-PG-ISSUED-{identity}",
                payload_digest="e" * 64,
                normalized_payload={"task_id": identity},
                received_at=now,
                workline_id=line.id,
                device_code=f"T5-PG-DEVICE-{identity}",
            )
            db.add(issued)
            await db.flush()
            picking = PickingTask(
                task_id=f"T5-PG-PICKING-{identity}",
                task_type=PickingTaskType.MANUAL,
                status=PickingTaskStatus.EXECUTING,
                queue_revision=1,
                dispatch_sequence=int(identity[:12], 16),
                issued_at_ms=1,
                issued_evidence_id=issued.id,
                workline_id=line.id,
            )
            db.add(picking)
            await db.flush()

            drain_a = await _drain_evidence(
                db,
                identity=identity,
                line_id=line.id,
                operation_id=new_uuid7(timestamp_ms=1_800_000_000_000),
                now=now,
            )
            drain_b = await _drain_evidence(
                db,
                identity=identity,
                line_id=line.id,
                operation_id=new_uuid7(timestamp_ms=1_800_000_001_000),
                now=now + timedelta(seconds=1),
            )
            rows = (
                ("historical-drain", drain_a.id, None, now),
                ("current-drain", drain_b.id, None, now + timedelta(seconds=1)),
                ("picking", issued.id, picking.id, now + timedelta(seconds=2)),
            )
            expected = []
            authority_tasks = {}
            for suffix, evidence_id, picking_task_id, updated_at in rows:
                for branch, status in (("final", "SUCCEEDED"), ("ack", "ACCEPTED")):
                    task = _transport(identity, f"{branch}-{suffix}", line.id, status, updated_at)
                    member = _member(task, updated_at, status="SUCCEEDED" if branch == "final" else "PENDING")
                    db.add(task)
                    await db.flush()
                    db.add(member)
                    await _add_binding(
                        db,
                        task,
                        line_id=line.id,
                        evidence_id=evidence_id,
                        picking_task_id=picking_task_id,
                    )
                    authority_tasks[(branch, suffix)] = task
                    expected.append((branch, task.transport_task_id))
            await db.flush()
            picking_member = PickingTaskBinSourceRack(
                picking_task_id=picking.id,
                rack_id=authority_tasks[("final", "picking")].request_json["rack_id"],
                rack_face="A",
                plan_revision=1,
                source_evidence_id=issued.id,
            )
            db.add(picking_member)
            await db.flush()

            for branch, suffix, offset in (("final", "current-drain", 1), ("ack", "picking", -1)):
                stale_task = authority_tasks[(branch, suffix)]
                binding = await db.scalar(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.client_request_id == stale_task.client_request_id
                    )
                )
                assert binding is not None
                db.add(
                    PositionProjection(
                        object_type="RACK",
                        object_id=stale_task.request_json["rack_id"],
                        workline_id=line.id,
                        source_operation_id=new_uuid7(),
                        source_transport_task_id=f"newer-{branch}",
                        source_causal_token=binding.causal_token + offset,
                        source_effect_phase="FINAL_RESULT",
                    )
                )
            await db.flush()

            picking.status = PickingTaskStatus.EXECUTION_COMPLETED
            await db.flush()

            final_capture = _CaptureExecute(db)
            final_rows = await repository.list_final_result_projection_candidates(final_capture, limit=100)
            assert [row[0] for row in final_rows] == [
                task_id for branch, task_id in expected if branch == "final" and "current-drain" not in task_id
            ]
            ack_capture = _CaptureExecute(db)
            ack_rows = await repository.list_ack_invalidation_projection_candidates(ack_capture, limit=100)
            assert [row[0] for row in ack_rows] == [
                task_id for branch, task_id in expected if branch == "ack" and "picking" in task_id
            ]

            cancelled = InboundEvidence(
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=f"T5-PG-CANCEL-{identity}",
                payload_digest="c" * 64,
                normalized_payload={"task_id": picking.task_id, "cancel_scope": "PLAN_MEMBERS"},
                received_at=now,
                workline_id=line.id,
                operation="outbound.picking_task.cancel@v1",
                operation_id=new_uuid7(),
            )
            db.add(cancelled)
            await db.flush()
            picking_member.cancelled_evidence_id = cancelled.id
            picking.status = PickingTaskStatus.EXECUTING
            await db.flush()
            assert [row[0] for row in await repository.list_final_result_projection_candidates(db, limit=100)] == [
                row[0] for row in final_rows
            ]

            for statement, expected_rows in (
                (final_capture.statement, len(final_rows)),
                (ack_capture.statement, len(ack_rows)),
            ):
                compiled = statement.compile(dialect=db.bind.dialect, compile_kwargs={"literal_binds": True})
                plan = await db.scalar(text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {compiled}"))
                assert plan[0]["Plan"]["Actual Rows"] == expected_rows
                assert plan[0]["Execution Time"] >= 0
        finally:
            await transaction.rollback()
