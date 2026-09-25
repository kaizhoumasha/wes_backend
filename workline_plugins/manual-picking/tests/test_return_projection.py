"""当前 Return 只由本次批次的已应用逐箱取走事实关闭。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from manual_picking.application.batch_driver import ManualPickingBatchDriver


@pytest.mark.asyncio
async def test_source_picked_projects_current_return_after_confirmation_cleanup() -> None:
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
        kind="BIN_MOVE", authority_workline_id=7, client_request_id="current-client", transport_task_id="current-task"
    )
    current_fact = SimpleNamespace(payload_json={"container_id": "BOX-A", "milestone": "SOURCE_PICKED"})
    old_fact = SimpleNamespace(payload_json={"container_id": "BOX-A", "milestone": "SOURCE_PICKED"})
    facts_by_task = {"old-task": [old_fact], "current-task": []}
    transports = SimpleNamespace(
        get_task_by_client_request=AsyncMock(return_value=task),
        list_members=AsyncMock(
            return_value=[
                SimpleNamespace(
                    object_type="BIN",
                    object_id="BOX-A",
                    source_json={"kind": "HANDOFF_POSITION", "location_code": "OUT"},
                )
            ]
        ),
        list_applied_position_evidence=AsyncMock(side_effect=lambda _db, task_id: facts_by_task[task_id]),
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
    assert await driver.project_exits_in_session(db, line) == 0
    assert row.return_state == "RETURN_REQUESTED"
    assert {call.args[1] for call in transports.list_applied_position_evidence.await_args_list} == {"current-task"}
    facts_by_task["current-task"] = [current_fact]
    member = transports.list_members.return_value[0]
    member.source_json = {"kind": "HANDOFF_POSITION", "location_code": "OTHER"}
    assert await driver.project_exits_in_session(db, line) == 0
    member.source_json = {"kind": "HANDOFF_POSITION", "location_code": "OUT"}
    assert await driver.project_exits_in_session(db, line) == 1
    assert row.return_state == "EXITED"
    bindings.get_by_decision_identity_for_update.assert_awaited_with(
        db,
        workline_id=7,
        correlation_id="current-batch",
        step="MANUAL_PICKING_RETURN_BATCH",
    )
