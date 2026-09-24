"""plan_activation 对 RETURN_RACK 直接取料的真实 PG owner。

plan_delta 落库 → activation 激活 → handler 产出 RETURN_RACK F01 intent →
TransportDecisionBinding 写入 RETURN_RACK_IN_STEP 行，断言 source_evidence_id、
resource_fence_id 与落库的 DirectPickExecution 对齐。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceKind,
    PositionProjection,
    TransportDecisionBinding,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.models import (
    InboundEvidenceApplyStatus as Status,
)
from src.app.execution.services import InboundEvidenceService
from src.app.execution.services.rack_inbound_window import RackInboundWindowService
from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.sys.models.audit_log import AuditLog
from src.app.transport.contracts import TransportHandle
from src.app.wms_adapter.outbound_picking.plan_delta_wire import (
    PickingTaskPlanDeltaEvent,
)
from src.app.wms_integration.outbound_picking.models import (
    DirectPickExecution,
    PickingTask,
    PickingTaskBinSourceRack,
    PickingTaskStatus,
)
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import (
    PickingTaskPlanDeltaRepository,
)
from src.app.wms_integration.outbound_picking.services.picking_task_plan_activation import (
    RETURN_RACK_IN_STEP,
    TARGET_RACK_IN_STEP,
    PickingTaskPlanActivationService,
)
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.core.uuid7 import new_uuid7

pytest_plugins = ("tests.integration.conftest",)
pytestmark = pytest.mark.asyncio


class _CaptureTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def move_rack_in_session(self, _db: Any, **kwargs: Any) -> TransportHandle:
        self.calls.append(kwargs)
        return TransportHandle(f"transport:{kwargs['client_request_id']}", kwargs["client_request_id"])


class _StubDriver:
    async def advance_completed_in_session(self, _db: Any, _line: Any) -> int:
        return 0

    async def advance_in_session(self, _db: Any, _line: Any, _task: Any) -> int:
        return 0


def _plugin(handler: Any) -> InstalledWorkLinePlugin:
    from manual_picking.definition import DEFINITION

    return InstalledWorkLinePlugin(
        definition=DEFINITION,
        picking_task_plan_applied_handler=handler,
        picking_task_batch_driver=_StubDriver(),
        picking_task_completion_driver=_StubDriver(),
    )


async def _setup_line_task(
    db: Any,
    task_name: str,
    *,
    with_direct_picks: bool,
) -> tuple[int, int, int, int, int]:
    line = WorkLine(
        line_code=task_name,
        line_name="plan activation return rack",
        line_type=LineType.MANUAL,
        run_mode=WorkLineRunMode.AUTO,
        is_active=True,
        plugin_key="manual-picking",
        plugin_version="0.1.0",
        position_bindings={
            "FIVE_RACK": {"location_type": "RACK_POSITION", "location_id": "FIVE-POS"},
            "RETURN_RACK": {"location_type": "RACK_POSITION", "location_id": "RETURN-POS"},
            "TRANSFER_RACK": {"location_type": "RACK_POSITION", "location_id": "TRANSFER-POS"},
            "INLET": {"location_type": "HANDOFF_POSITION", "location_id": "INLET-POS"},
            "OUTLET": {"location_type": "HANDOFF_POSITION", "location_id": "OUTLET-POS"},
        },
    )
    db.add(line)
    await db.flush()
    db.add_all(
        WorkLinePosition(
            workline_id=line.id,
            workline_code=line.line_code,
            position_code=code,
            position_name=code,
            position_role=role,
            allowed_rack_kind=kind,
            logic_location_code=location,
            capacity=1,
        )
        for code, role, kind, location in (
            ("TRANSFER", "SMT_TRANSFER_RACK_POSITION", "TRANSFER", "TRANSFER-POS"),
            ("RETURN", "SMT_RETURN_RACK_POSITION", "RETURN", "RETURN-POS"),
        )
    )

    issued_op_id = new_uuid7()
    plan_op_id = new_uuid7()
    prepare_op_id = new_uuid7()
    issued_evidence = await InboundEvidenceService().accept(
        db,
        kind=InboundEvidenceKind.WMS_EVENT,
        source_identity=f"outbound.picking_task.issued@v1:{issued_op_id}",
        operation="outbound.picking_task.issued@v1",
        operation_id=issued_op_id,
        normalized_payload={"data": {"task_id": task_name}},
        received_at=datetime(2026, 9, 17),
        apply_status=Status.APPLIED,
    )
    plan_payload: dict[str, Any] = {
        "operation": "outbound.picking_task.plan_delta@v1",
        "operation_id": plan_op_id,
        "timestamp": 1,
        "data": {
            "task_id": task_name,
            "plan_revision": 1,
            "target_rack": {"rack_id": "TRANSFER-1", "rack_face": "90"},
            "added_bin_source_racks": [
                {"rack_id": "FIVE-1", "rack_face": ["90"]},
            ],
        },
    }
    if with_direct_picks:
        plan_payload["data"]["added_direct_picks"] = [
            {
                "source_locator": {"rack_id": "RET-1", "rack_face": "A", "slot_id": "A-03", "type": "RACK_SLOT"},
            }
        ]
    plan_event = PickingTaskPlanDeltaEvent.model_validate(plan_payload)
    plan_evidence = await InboundEvidenceService().accept(
        db,
        kind=InboundEvidenceKind.WMS_EVENT,
        source_identity=f"outbound.picking_task.plan_delta@v1:{plan_op_id}",
        operation="outbound.picking_task.plan_delta@v1",
        operation_id=plan_op_id,
        normalized_payload=plan_event.model_dump(mode="json"),
        received_at=datetime(2026, 9, 17),
        apply_status=Status.APPLIED,
    )
    await db.flush()
    prepare_evidence = await InboundEvidenceService().accept(
        db,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"outbound.picking_task.prepare@v1:{prepare_op_id}",
        operation="outbound.picking_task.prepare@v1",
        operation_id=prepare_op_id,
        normalized_payload={"operation_id": prepare_op_id, "code": "PREPARE_ACCEPTED", "timestamp": 1, "data": {}},
        received_at=datetime(2026, 9, 17),
        apply_status=Status.APPLIED,
    )
    task = PickingTask(
        task_id=task_name,
        task_type="MANUAL",
        status=PickingTaskStatus.EXECUTING,
        queue_revision=1,
        dispatch_sequence=1,
        issued_at_ms=1,
        issued_evidence_id=issued_evidence.evidence.id,
        workline_id=line.id,
        target_rack_id="TRANSFER-1",
        target_rack_face="90",
        initial_plan_evidence_id=plan_evidence.evidence.id,
        last_plan_evidence_id=plan_evidence.evidence.id,
        last_applied_plan_revision=1,
    )
    db.add(task)
    await db.flush()
    repo = PickingTaskPlanDeltaRepository()
    await repo.add_members(db, task.id, plan_event.data, plan_evidence.evidence.id)
    confirmation = WmsConfirmation(
        operation="outbound.picking_task.prepare@v1",
        operation_id=prepare_op_id,
        picking_task_id=task.id,
        request_digest="a" * 64,
        request_payload={},
        deadline_at=datetime(2026, 9, 17) + timedelta(seconds=30),
        status=WmsConfirmationStatus.COMPLETED,
        response_evidence_id=prepare_evidence.evidence.id,
        response_result="PREPARE_ACCEPTED",
        completed_at=datetime(2026, 9, 17),
    )
    db.add(confirmation)
    await db.flush()
    return line.id, task.id, plan_evidence.evidence.id, prepare_evidence.evidence.id, issued_evidence.evidence.id


async def test_activation_creates_return_rack_transport_binding_from_direct_picks(integration_session_factory) -> None:
    from manual_picking.handlers import PickingTaskPlanAppliedHandler

    handler = PickingTaskPlanAppliedHandler()
    transport = _CaptureTransport()
    sessions = integration_session_factory
    task_name = f"PLAN-RET-{new_uuid7()}"
    line_id = task_id = plan_evidence_id = prepare_evidence_id = issued_evidence_id = 0
    try:
        async with sessions.begin() as db:
            line_id, task_id, plan_evidence_id, prepare_evidence_id, issued_evidence_id = await _setup_line_task(
                db, task_name, with_direct_picks=True
            )
        service = PickingTaskPlanActivationService(
            sessions,
            plugins=(_plugin(handler),),
            transport_creator=ReliableRackTransportCreator(transport, inbound_window=RackInboundWindowService()),
        )
        created = await service.activate_batch()
        assert created > 0, "activation should emit at least one transport"
        async with sessions() as db:
            bindings = (
                await db.scalars(
                    select(TransportDecisionBinding).where(TransportDecisionBinding.picking_task_id == task_id)
                )
            ).all()
            direct_picks = (
                await db.scalars(select(DirectPickExecution).where(DirectPickExecution.picking_task_id == task_id))
            ).all()
        assert len(direct_picks) == 1
        assert (direct_picks[0].rack_id, direct_picks[0].rack_face, direct_picks[0].source_evidence_id) == (
            "RET-1",
            "A",
            plan_evidence_id,
        )
        steps_by_step: dict[str, list[TransportDecisionBinding]] = {}
        for binding in bindings:
            steps_by_step.setdefault(binding.step, []).append(binding)
        assert RETURN_RACK_IN_STEP in steps_by_step, f"missing {RETURN_RACK_IN_STEP} binding"
        assert TARGET_RACK_IN_STEP in steps_by_step, "target rack binding should still exist"
        return_bindings = steps_by_step[RETURN_RACK_IN_STEP]
        assert len(return_bindings) == 1, f"expected exactly 1 RETURN_RACK binding, got {len(return_bindings)}"
        rb = return_bindings[0]
        assert rb.resource_fence_id == "RET-1"
        assert rb.source_evidence_id == plan_evidence_id
        assert rb.workline_id == line_id
        assert {call["rack_id"] for call in transport.calls} == {"TRANSFER-1", "RET-1"}
        return_call = next(call for call in transport.calls if call["rack_id"] == "RET-1")
        assert return_call["target_face"] == "A"
    finally:
        if task_id:
            async with sessions.begin() as db:
                await db.execute(
                    delete(TransportDecisionBinding).where(TransportDecisionBinding.picking_task_id == task_id)
                )
                await db.execute(delete(DirectPickExecution).where(DirectPickExecution.picking_task_id == task_id))
                await db.execute(
                    delete(PickingTaskBinSourceRack).where(PickingTaskBinSourceRack.picking_task_id == task_id)
                )
                await db.execute(delete(WmsConfirmation).where(WmsConfirmation.picking_task_id == task_id))
                if line_id:
                    await db.execute(delete(PositionProjection).where(PositionProjection.workline_id == line_id))
                    await db.execute(delete(WorkLinePosition).where(WorkLinePosition.workline_id == line_id))
                await db.execute(delete(AuditLog).where(AuditLog.args["task_id"].as_string() == task_name))
                await db.execute(delete(PickingTask).where(PickingTask.id == task_id))
                await db.execute(
                    delete(InboundEvidence).where(
                        InboundEvidence.id.in_([plan_evidence_id, prepare_evidence_id, issued_evidence_id])
                    )
                )
                if line_id:
                    await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


async def test_activation_skips_return_rack_when_no_direct_picks(integration_session_factory) -> None:
    from manual_picking.handlers import PickingTaskPlanAppliedHandler

    handler = PickingTaskPlanAppliedHandler()
    transport = _CaptureTransport()
    sessions = integration_session_factory
    task_name = f"PLAN-NO-RET-{new_uuid7()}"
    line_id = task_id = plan_evidence_id = issued_evidence_id = 0
    try:
        async with sessions.begin() as db:
            line_id, task_id, plan_evidence_id, _, issued_evidence_id = await _setup_line_task(
                db, task_name, with_direct_picks=False
            )
        service = PickingTaskPlanActivationService(
            sessions,
            plugins=(_plugin(handler),),
            transport_creator=ReliableRackTransportCreator(transport, inbound_window=RackInboundWindowService()),
        )
        await service.activate_batch()
        async with sessions() as db:
            bindings = (
                await db.scalars(
                    select(TransportDecisionBinding).where(TransportDecisionBinding.picking_task_id == task_id)
                )
            ).all()
        steps = {binding.step for binding in bindings}
        assert RETURN_RACK_IN_STEP not in steps
        assert TARGET_RACK_IN_STEP in steps
        assert all(call["rack_id"] != "RET-1" for call in transport.calls)
    finally:
        if task_id:
            async with sessions.begin() as db:
                await db.execute(
                    delete(TransportDecisionBinding).where(TransportDecisionBinding.picking_task_id == task_id)
                )
                await db.execute(
                    delete(PickingTaskBinSourceRack).where(PickingTaskBinSourceRack.picking_task_id == task_id)
                )
                await db.execute(delete(WmsConfirmation).where(WmsConfirmation.picking_task_id == task_id))
                if line_id:
                    await db.execute(delete(PositionProjection).where(PositionProjection.workline_id == line_id))
                    await db.execute(delete(WorkLinePosition).where(WorkLinePosition.workline_id == line_id))
                await db.execute(delete(AuditLog).where(AuditLog.args["task_id"].as_string() == task_name))
                await db.execute(delete(PickingTask).where(PickingTask.id == task_id))
                await db.execute(
                    delete(InboundEvidence).where(InboundEvidence.id.in_([plan_evidence_id, issued_evidence_id]))
                )
                if line_id:
                    await db.execute(delete(WorkLine).where(WorkLine.id == line_id))
