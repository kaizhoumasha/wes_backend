"""PostgreSQL evidence for projection ordering across transport decision domains."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from src.app.execution.models import InboundEvidence, InboundEvidenceKind, TransportDecisionBinding
from src.app.execution.services.position_projection_service import (
    PositionProjectionService,
    ProjectionCausalRelation,
    ProjectionEffectPhase,
    ProjectionSource,
    compare_projection_sources,
)
from src.app.transport.contracts import TransportExecutionAuthority
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.workline.models import WorkLine
from src.app.workline.models.workline import LineType


async def _add_binding(
    db,
    *,
    workline_id: int,
    evidence_id: int,
    identity: str,
    correlation_id: str,
    step: str,
    picking_task_id: int | None,
    resource_fence_id: str,
) -> TransportDecisionBinding:
    binding = TransportDecisionBinding(
        correlation_id=correlation_id,
        step=step,
        workline_id=workline_id,
        picking_task_id=picking_task_id,
        resource_fence_id=resource_fence_id,
        client_request_id=f"T3-{identity}-{step}",
        source_evidence_id=evidence_id,
    )
    db.add(binding)
    await db.flush()
    return binding


@pytest.mark.asyncio
async def test_postgresql_tokens_order_picking_drain_bin_and_multi_member_sources(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    async with integration_session_factory() as db:
        transaction = await db.begin()
        try:
            workline = WorkLine(
                line_code=f"T3-ORDER-{identity[:20]}",
                line_name="T3 projection domain ordering",
                line_type=LineType.AUTO,
            )
            db.add(workline)
            await db.flush()
            workline.is_active = True
            evidence = InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=f"T3-EVIDENCE-{identity}",
                payload_digest="3" * 64,
                normalized_payload={"source_event_id": f"T3-EVIDENCE-{identity}"},
                received_at=datetime(2026, 9, 22),
                workline_id=workline.id,
                device_code=f"T3-DEVICE-{identity}",
            )
            db.add(evidence)
            await db.flush()
            picking_task = PickingTask(
                task_id=f"T3-PICKING-{identity}",
                task_type=PickingTaskType.MANUAL,
                status=PickingTaskStatus.EXECUTING,
                queue_revision=1,
                dispatch_sequence=int(identity[:12], 16),
                issued_at_ms=1,
                issued_evidence_id=evidence.id,
                workline_id=workline.id,
            )
            db.add(picking_task)
            await db.flush()

            picking = await _add_binding(
                db,
                workline_id=workline.id,
                evidence_id=evidence.id,
                identity=identity,
                correlation_id=f"picking:{identity}",
                step="PICKING_TASK_TARGET_RACK_IN",
                picking_task_id=picking_task.id,
                resource_fence_id=f"RACK-{identity}",
            )
            drain = await _add_binding(
                db,
                workline_id=workline.id,
                evidence_id=evidence.id,
                identity=identity,
                correlation_id=f"drain:{identity}:rack:RACK-{identity}",
                step="MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN",
                picking_task_id=None,
                resource_fence_id=f"RACK-{identity}",
            )
            old_bin = await _add_binding(
                db,
                workline_id=workline.id,
                evidence_id=evidence.id,
                identity=identity,
                correlation_id=f"old-bin:{identity}",
                step="MANUAL_PICKING_INBOUND_BATCH",
                picking_task_id=None,
                resource_fence_id=f"BIN-BATCH-{identity}",
            )
            bin_batch = await _add_binding(
                db,
                workline_id=workline.id,
                evidence_id=evidence.id,
                identity=identity,
                correlation_id=f"bin-batch:{identity}",
                step="MANUAL_PICKING_RETURN_BATCH",
                picking_task_id=None,
                resource_fence_id=f"BIN-BATCH-{identity}",
            )

            assert picking.causal_token < drain.causal_token < old_bin.causal_token < bin_batch.causal_token
            rack_id = f"RACK-{identity}"
            assert (
                compare_projection_sources(
                    ProjectionSource(
                        "RACK", rack_id, "drain-task", drain.causal_token, ProjectionEffectPhase.FINAL_RESULT
                    ),
                    ProjectionSource(
                        "RACK", rack_id, "picking-task", picking.causal_token, ProjectionEffectPhase.FINAL_RESULT
                    ),
                )
                is ProjectionCausalRelation.AFTER
            )

            service = PositionProjectionService()
            authority = TransportExecutionAuthority(workline_id=workline.id)
            await service.apply_transport_result(
                db,
                authority=authority,
                object_type="RACK",
                object_id=rack_id,
                position={"kind": "RACK_POSITION", "location_code": "DRAIN-TARGET"},
                position_unknown=False,
                arrival_face=None,
                client_request_id=drain.client_request_id,
                operation_id=str(uuid4()),
                transport_task_id=f"drain-task-{identity}",
                updated_at=datetime(2026, 9, 22),
            )
            await service.apply_transport_result(
                db,
                authority=authority,
                object_type="RACK",
                object_id=rack_id,
                position={"kind": "RACK_POSITION", "location_code": "STALE-PICKING-TARGET"},
                position_unknown=False,
                arrival_face=None,
                client_request_id=picking.client_request_id,
                operation_id=str(uuid4()),
                transport_task_id=f"picking-task-{identity}",
                updated_at=datetime(2026, 9, 22),
            )
            rack_projection = await service.get_current(db, "RACK", rack_id)
            assert rack_projection.source_transport_task_id == f"drain-task-{identity}"
            assert rack_projection.source_causal_token == drain.causal_token
            assert rack_projection.position_json == {"kind": "RACK_POSITION", "location_code": "DRAIN-TARGET"}

            bin_ids = (f"BIN-A-{identity}", f"BIN-B-{identity}")
            for bin_id in bin_ids:
                await service.apply_transport_result(
                    db,
                    authority=authority,
                    object_type="BIN",
                    object_id=bin_id,
                    position={"kind": "HANDOFF_POSITION", "location_code": f"TARGET-{bin_id}"},
                    position_unknown=False,
                    arrival_face=None,
                    client_request_id=bin_batch.client_request_id,
                    operation_id=str(uuid4()),
                    transport_task_id=f"multi-member-task-{identity}",
                    updated_at=datetime(2026, 9, 22),
                )
            await service.apply_transport_result(
                db,
                authority=authority,
                object_type="BIN",
                object_id=bin_ids[0],
                position={"kind": "HANDOFF_POSITION", "location_code": "STALE-BIN-TARGET"},
                position_unknown=False,
                arrival_face=None,
                client_request_id=old_bin.client_request_id,
                operation_id=str(uuid4()),
                transport_task_id=f"old-bin-task-{identity}",
                updated_at=datetime(2026, 9, 22),
            )

            first = await service.get_current(db, "BIN", bin_ids[0])
            second = await service.get_current(db, "BIN", bin_ids[1])
            assert first.source_causal_token == second.source_causal_token == bin_batch.causal_token
            assert first.source_transport_task_id == second.source_transport_task_id == f"multi-member-task-{identity}"
            assert first.position_json == {
                "kind": "HANDOFF_POSITION",
                "location_code": f"TARGET-{bin_ids[0]}",
            }
            assert (
                compare_projection_sources(
                    ProjectionSource(
                        "BIN",
                        bin_ids[0],
                        first.source_transport_task_id,
                        first.source_causal_token,
                        ProjectionEffectPhase.FINAL_RESULT,
                    ),
                    ProjectionSource(
                        "BIN",
                        bin_ids[1],
                        second.source_transport_task_id,
                        second.source_causal_token,
                        ProjectionEffectPhase.FINAL_RESULT,
                    ),
                )
                is ProjectionCausalRelation.INCOMPARABLE
            )
        finally:
            await transaction.rollback()
