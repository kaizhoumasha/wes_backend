"""当前 Return 按原 Transport 的执行权交接闭合，不伪造物理取走。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from manual_picking.application.batch_driver import ManualPickingBatchDriver


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "deadline", "revision", "closed"),
    [
        ("PENDING", None, 0, False),
        ("REJECTED", None, 0, False),
        ("RECONCILING", None, 0, False),
        ("ACCEPTED", 1, 0, True),
        ("RECONCILING", 1, 0, True),
        ("RECONCILING", None, 1, True),
        ("SUCCEEDED", None, 1, True),
        ("FAILED", None, 1, True),
    ],
)
async def test_transport_handoff_closes_only_its_current_return(status, deadline, revision, closed) -> None:
    row = SimpleNamespace(bin_code="BOX-A", return_batch_evidence_id=31, return_state="RETURN_REQUESTED")
    returns = SimpleNamespace(requested_for_update=AsyncMock(return_value=(row,)))
    evidence = SimpleNamespace(
        id=31,
        kind="WMS_RESULT",
        apply_status="APPLIED",
        operation="outbound.bin.return_batch@v1",
        operation_id="current-batch",
    )
    bindings = SimpleNamespace(
        get_by_decision_identity_for_update=AsyncMock(
            return_value=SimpleNamespace(
                source_evidence_id=31, resource_fence_id="current-batch", client_request_id="current-client"
            )
        )
    )
    task = SimpleNamespace(
        kind="BIN_MOVE",
        authority_workline_id=7,
        client_request_id="current-client",
        transport_task_id="current-task",
        status=status,
        result_deadline_at=deadline,
        last_applied_wms_outcome_revision=revision,
    )
    transports = SimpleNamespace(
        get_task_by_client_request=AsyncMock(return_value=task),
        list_applied_position_evidence=AsyncMock(return_value=()),
        list_members=AsyncMock(
            return_value=[
                SimpleNamespace(
                    object_type="BIN",
                    object_id="BOX-A",
                    source_json={"kind": "HANDOFF_POSITION", "location_code": "OUT"},
                )
            ]
        ),
    )
    driver = ManualPickingBatchDriver(
        object(),
        plans=object(),
        positions=object(),
        transports=transports,
        rack_creator=object(),
        departure_scheduler=object(),
        departure_reader=object(),
        passages=returns,
        bindings=bindings,
        evidences=SimpleNamespace(get_by_id=AsyncMock(return_value=evidence)),
    )
    line = SimpleNamespace(id=7, position_bindings={"OUTLET": {"location_id": "OUT"}})
    db = object()

    row.return_batch_evidence_id = None
    assert await driver.project_exits_in_session(db, line) == 0
    row.return_batch_evidence_id = 31
    current_binding = bindings.get_by_decision_identity_for_update.return_value
    current_binding.source_evidence_id = 20
    assert await driver.project_exits_in_session(db, line) == 0
    current_binding.source_evidence_id = 31
    member = transports.list_members.return_value[0]
    member.source_json = {"kind": "HANDOFF_POSITION", "location_code": "OTHER"}
    assert await driver.project_exits_in_session(db, line) == 0
    member.source_json = {"kind": "HANDOFF_POSITION", "location_code": "OUT"}
    assert await driver.project_exits_in_session(db, line) == int(closed)
    assert row.return_state == ("EXITED" if closed else "RETURN_REQUESTED")
    assert task.status == status
    transports.list_applied_position_evidence.assert_not_awaited()
    assert member.source_json == {"kind": "HANDOFF_POSITION", "location_code": "OUT"}
    bindings.get_by_decision_identity_for_update.assert_awaited_with(
        db,
        workline_id=7,
        correlation_id="current-batch",
        step="MANUAL_PICKING_RETURN_BATCH",
    )
