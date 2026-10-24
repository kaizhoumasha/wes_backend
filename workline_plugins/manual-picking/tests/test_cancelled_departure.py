"""取消搬运与已满足的离场目标分别保留，业务消费不得重发清退。"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_scan_flow import NOW, _drain_rack_recovery, _setup

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.plugin_binding import BusinessEvidenceDisposition


def _cancelled_departure(*, step="MANUAL_PICKING_SOURCE_RACK_OUT", target_kind="ZONE"):
    member = {
        "object_id": "510063",
        "final_position": {"kind": "RACK_POSITION", "location_code": "WH01"},
        "position_unknown": False,
        "failure_code": "RCS_TASK_CANCELLED",
        "arrival_face": None,
    }
    task = SimpleNamespace(
        kind="RACK_MOVE",
        status="FAILED",
        reason_code="RCS_TASK_CANCELLED",
        transport_task_id="transport-original",
        client_request_id="request-original",
        submit_operation_id="operation-original",
        submit_request_body='{"frozen":"original"}',
        outcome_version=1,
        published_outcome_version=1,
        outcome_json={"members": [deepcopy(member)]},
        request_json={"rack_id": "510063", "target": {"kind": target_kind, "location_code": "WH01"}},
    )
    flow, evidences, _, _, _ = _setup(transport_reader=SimpleNamespace(get_task=AsyncMock(return_value=task)))
    flow._retry_terminal_rack = AsyncMock(return_value=None)
    flow._rack_creator = SimpleNamespace(create=AsyncMock(), create_source_return=AsyncMock())
    newer_projection = SimpleNamespace(
        source_transport_task_id="transport-newer",
        source_causal_token=2000,
        position_json={"kind": "RACK_POSITION", "location_code": "ANOTHER-WORKLINE"},
    )
    flow._positions = SimpleNamespace(get=AsyncMock(return_value=newer_projection), apply_transport_result=AsyncMock())
    evidence = InboundEvidence(
        id=10,
        kind=InboundEvidenceKind.TRANSPORT_RESULT,
        source_identity="transport:transport-original:outcome:1",
        payload_digest="d" * 64,
        normalized_payload={
            "transport_task_id": task.transport_task_id,
            "client_request_id": task.client_request_id,
            "caller": {"workline_id": "7"},
            "status": "FAILED",
            "reason_code": "RCS_TASK_CANCELLED",
            "rack_id": "510063",
            "step": step,
            "source_evidence_id": 1,
            "outcome_version": 1,
            "members": [member],
        },
        received_at=NOW,
        workline_id=7,
        transport_task_id=task.transport_task_id,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    evidences.rows[10] = evidence
    return flow, evidence, task, newer_projection


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["ZONE", "RACK_POSITION"])
@pytest.mark.parametrize(
    "step",
    [
        "MANUAL_PICKING_SOURCE_RACK_OUT",
        "MANUAL_PICKING_RETURN_RACK_OUT",
        "MANUAL_PICKING_TRANSFER_RACK_OUT",
        "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT",
    ],
)
@pytest.mark.parametrize("parent_status", ["EXECUTING", "EXECUTION_COMPLETED", "CANCELLED"])
async def test_cancelled_departure_at_frozen_target_closes_without_retry(step, target_kind, parent_status):
    flow, evidence, task, projection = _cancelled_departure(step=step, target_kind=target_kind)
    flow._tasks.get_by_id_for_update = AsyncMock(return_value=SimpleNamespace(status=parent_status))
    before = deepcopy((vars(task), evidence.normalized_payload, vars(projection)))

    first = await flow.apply_in_session(object(), evidence.id, workline_id=7)
    repeated = await flow.apply_in_session(object(), evidence.id, workline_id=7)

    assert first.disposition is BusinessEvidenceDisposition.APPLIED
    assert repeated == first
    assert (vars(task), evidence.normalized_payload, vars(projection)) == before
    flow._retry_terminal_rack.assert_not_awaited()
    flow._positions.get.assert_not_awaited()
    flow._positions.apply_transport_result.assert_not_awaited()
    flow._rack_creator.create.assert_not_awaited()
    flow._rack_creator.create_source_return.assert_not_awaited()
    flow._tasks.get_by_id_for_update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    ["unknown", "other_position", "zone_position", "other_rack", "multiple_members", "other_failure", "missing"],
)
async def test_cancelled_departure_without_exact_target_fact_keeps_existing_recovery(invalid):
    flow, evidence, _, _ = _cancelled_departure()
    member = evidence.normalized_payload["members"][0]
    if invalid == "unknown":
        member["position_unknown"] = True
    elif invalid == "other_position":
        member["final_position"]["location_code"] = "WHE0809"
    elif invalid == "zone_position":
        member["final_position"]["kind"] = "ZONE"
    elif invalid == "other_rack":
        member["object_id"] = "510012"
    elif invalid == "multiple_members":
        evidence.normalized_payload["members"].append(deepcopy(member))
    elif invalid == "other_failure":
        member["failure_code"] = "OTHER_FAILURE"
    else:
        evidence.normalized_payload["members"] = []

    result = await flow.apply_in_session(object(), evidence.id, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.RECONCILING
    flow._retry_terminal_rack.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["inbound", "rotate", "other_terminal", "other_identity", "other_revision", "other_original_result"]
)
async def test_target_fact_does_not_close_other_actions(invalid):
    flow, evidence, task, _ = _cancelled_departure()
    if invalid == "inbound":
        evidence.normalized_payload["step"] = "PICKING_TASK_BIN_SOURCE_RACK_IN"
    elif invalid == "rotate":
        task.kind = "RACK_ROTATE"
        evidence.normalized_payload["step"] = "MANUAL_PICKING_SOURCE_RACK_ROTATE"
    elif invalid == "other_terminal":
        task.reason_code = "OTHER_FAILURE"
    elif invalid == "other_identity":
        task.client_request_id = "request-another"
    elif invalid == "other_revision":
        task.outcome_version = 2
    else:
        task.outcome_json["members"][0]["final_position"]["location_code"] = "ANOTHER-POSITION"

    result = await flow.apply_in_session(object(), evidence.id, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.RECONCILING


@pytest.mark.asyncio
async def test_archived_picking_task_late_cancelled_departure_never_creates_retry():
    flow, _, original, evidence = _drain_rack_recovery("ACTIVE", step="MANUAL_PICKING_SOURCE_RACK_OUT")
    binding = flow._transport_bindings.get_by_client_request_id.return_value
    binding.picking_task_id = 31
    binding.causal_token = 100
    flow._tasks.get_by_id_for_update = AsyncMock(
        return_value=SimpleNamespace(
            id=31,
            task_id="PICK-001",
            status="ARCHIVED",
            workline_id=7,
        )
    )
    original.created_at = NOW
    position = SimpleNamespace(
        workline_id=7,
        position_unknown=False,
        arrival_face="90",
        source_causal_token=100,
        source_transport_task_id=original.transport_task_id,
        position_json={
            "kind": "RACK_POSITION",
            "location_code": flow._worklines.line.position_bindings["FIVE_RACK"]["location_id"],
        },
    )
    flow._positions.get = AsyncMock(return_value=position)
    flow._batch_progress.working_rack_position = AsyncMock(return_value=position)
    flow._batch_progress.has_newer_rack_action = AsyncMock(return_value=False)
    flow._batch_progress.has_current_rack_dependency = AsyncMock(return_value=False)
    flow._batch_progress.current_round_started_at = AsyncMock(return_value=NOW)
    result = await flow.apply_in_session(object(), evidence.id, workline_id=7)
    assert result.disposition is BusinessEvidenceDisposition.IGNORED
    flow._rack_creator.create.assert_not_awaited()
    flow._rack_creator.create_rotate.assert_not_awaited()
    flow._rack_creator.create_windowed_inbound.assert_not_awaited()
