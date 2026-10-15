"""原入站已到位箱的迟到扫码保持原任务归属。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_scan_flow import _scan, _setup

from src.app.execution.plugin_binding import BusinessEvidenceDisposition


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_status", ["EXECUTION_COMPLETED", "CANCELLED", "EXECUTING"])
@pytest.mark.parametrize("new_task", [False, True])
@pytest.mark.parametrize("suffix", ["B", "C"])
async def test_late_scan1_bypasses_workstation_and_keeps_original_owner(parent_status, new_task, suffix):
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[1] = _scan(1, "S1", f"A000000001-{suffix}")
    old = SimpleNamespace(id=31, task_id="PICK-001", workline_id=7, status=parent_status, archived_at=None)
    current = SimpleNamespace(id=32, task_id="NEW-TASK", workline_id=7, status="EXECUTING") if new_task else None
    flow._tasks.get_executing_for_workline_for_update = AsyncMock(return_value=current)
    flow._tasks.get_by_id_for_update = AsyncMock(return_value=old)
    flow._tasks.get_by_task_id_for_update = AsyncMock(return_value=old)
    flow._batch_progress.scan1_task_id = AsyncMock(return_value=31)
    flow._source_racks.list_bin_source_racks = AsyncMock(
        return_value=[SimpleNamespace(rack_id="RACK-1", rack_face="90")]
    )
    assert (await flow.apply_in_session(object(), 1, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passages.rows[0].task_id == "PICK-001"
    assert len(commands.requests) == 1
    assert commands.requests[0].task_type == "MOVE_RIGHT"
    assert passages.rows[0].disposition == "NG"
    assert passages.rows[0].reason_code == (
        "SCAN1_OUTSIDE_CURRENT_BATCH" if suffix == "B" else "SCAN1_INVALID_BIN_CODE"
    )
    assert admissions.intents == []
    assert flow._batch_progress.scanned == {"A000000001"}
    await flow.apply_in_session(object(), 1, workline_id=7)
    assert len(commands.requests) == 1
    evidences.rows[3] = _scan(3, "S3", "A000000001-B")
    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert [request.task_type for request in commands.requests] == ["MOVE_RIGHT", "MOVE_FORWARD"]
    assert passages.rows[0].scan2_evidence_id is None
    assert passages.rows[0].disposition == "CLOSED"
    assert passages.rows[0].task_id == "PICK-001"
    assert flow._returns.rows[0].scan3_evidence_id == passages.rows[0].scan3_evidence_id
    assert admissions.intents == []


@pytest.mark.asyncio
async def test_late_scan1_does_not_repeat_a_recorded_inbound_member():
    flow, _, passages, commands, _ = _setup()
    flow._tasks.get_executing_for_workline_for_update = AsyncMock(return_value=None)
    flow._tasks.get_by_id_for_update = AsyncMock(
        return_value=SimpleNamespace(id=31, task_id="PICK-001", workline_id=7, archived_at=None)
    )
    flow._batch_progress.scanned.add("A000000001")
    result = await flow.apply_in_session(object(), 1, workline_id=7)
    assert result.disposition is BusinessEvidenceDisposition.RECONCILING
    assert passages.rows == []
    assert commands.requests == []


@pytest.mark.asyncio
async def test_late_scan1_waits_automatically_for_current_causal_dependency():
    flow, _, passages, commands, _ = _setup()
    flow._tasks.get_executing_for_workline_for_update = AsyncMock(return_value=None)
    flow._tasks.get_by_id_for_update = AsyncMock(
        return_value=SimpleNamespace(id=31, task_id="PICK-001", workline_id=7, archived_at=None)
    )
    flow._batch_progress.scan1_task_id = AsyncMock(side_effect=["WAIT_FOR_RESULT", 31])
    result = await flow.apply_in_session(object(), 1, workline_id=7)
    assert (result.disposition, result.retry_after_ms) == (BusinessEvidenceDisposition.DEFERRED, 1000)
    assert passages.rows == []
    assert commands.requests == []
    assert (await flow.apply_in_session(object(), 1, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(commands.requests) == 1
    assert commands.requests[0].task_type == "MOVE_RIGHT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner",
    [
        None,
        SimpleNamespace(id=31, task_id="PICK-001", workline_id=8, status="EXECUTION_COMPLETED", archived_at=None),
        SimpleNamespace(id=31, task_id="PICK-001", workline_id=7, status="EXECUTION_COMPLETED", archived_at=1),
    ],
)
async def test_late_scan1_refuses_missing_wrong_line_or_archived_original_owner(owner):
    flow, _, passages, commands, _ = _setup()
    flow._batch_progress.scan1_task_id = AsyncMock(return_value=31 if owner is not None else None)
    flow._tasks.get_by_id_for_update = AsyncMock(return_value=owner)
    result = await flow.apply_in_session(object(), 1, workline_id=7)
    assert result.disposition is BusinessEvidenceDisposition.RECONCILING
    assert passages.rows == []
    assert commands.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "ended",
        "cancelled",
        "pending",
        "unpublished",
        "unknown",
        "wrong_final",
        "wrong_line",
        "wrong_source",
        "unpublished_batch",
        "wrong_batch_bin",
        "new_pending",
        "archived",
        "wrong_binding_operation",
        "wrong_binding_evidence",
        "wrong_owner_business",
        "wrong_owner_line",
        "wrong_transport_line",
        "wrong_target",
        "wrong_evidence_kind",
        "wrong_evidence_line",
        "wrong_evidence_operation",
        "wrong_evidence_id",
        "unapplied_batch",
        "wrong_ready_bin",
        "wrong_ready_rack",
        "wrong_ready_face",
        "new_accepted",
        "new_reconciling",
        "new_unpublished",
        "other_bin_pending",
        "old_pending",
        "new_finished",
    ],
)
async def test_original_batch_owner_requires_exact_published_arrival(change):
    from datetime import datetime

    import wes_plugin_sdk as sdk
    from manual_picking.application.batch_repository import BatchRepository
    from test_batch_repository import _new_sessions, _seed_current_inbound

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding
    from src.app.transport.models import TransportMember, TransportTask
    from src.app.wms_integration.outbound_picking.models import PickingTask

    engine, sessions = await _new_sessions()
    now = datetime(2026, 10, 15)
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="019f0000-0000-7000-8000-000000000031",
        task_id="OLD-TASK",
        plan_revision=1,
        rack_id="R1",
        rack_face="90",
    )
    ready = sdk.BinInboundBatchReady(
        (sdk.BinInboundBatchMember("A000000341", sdk.TransportRackBinSlot("R1", "90", "S1")),)
    )
    try:
        async with sessions.begin() as db:
            await _seed_current_inbound(db, intent, ready)
            old = PickingTask(
                id=31,
                task_id="OLD-TASK",
                task_type="MANUAL",
                status="EXECUTION_COMPLETED",
                workline_id=7,
                issued_evidence_id=31,
                issued_at_ms=1,
                queue_revision=1,
                dispatch_sequence=1,
            )
            binding = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=31,
                correlation_id=f"{intent.operation_id}:0",
                causal_token=100,
                step="MANUAL_PICKING_INBOUND_BATCH",
                resource_fence_id=intent.operation_id,
                source_evidence_id=31,
                client_request_id="original-client",
            )
            transport = TransportTask(
                transport_task_id="original",
                client_request_id="original-client",
                kind="BIN_MOVE",
                caller_json={"workline_id": "7"},
                request_json={},
                request_digest="a" * 64,
                submit_operation_id=intent.operation_id,
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="b" * 64,
                status="SUCCEEDED",
                authority_workline_id=7,
                outcome_version=1,
                published_outcome_version=1,
                created_at=now,
                updated_at=now,
            )
            member = TransportMember(
                transport_task_id="original",
                ordinal=1,
                object_type="BIN",
                object_id="A000000341",
                source_json={"kind": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": "S1"},
                target_json={"kind": "HANDOFF_POSITION", "location_code": "INLET"},
                final_position_json={"kind": "HANDOFF_POSITION", "location_code": "INLET"},
                position_unknown=False,
                status="SUCCEEDED",
                updated_at=now,
            )
            db.add_all([old, binding, transport, member])
            if change == "cancelled":
                old.status = "CANCELLED"
            if change == "pending":
                transport.status = "ACCEPTED"
            if change == "unpublished":
                transport.published_outcome_version = 0
            if change == "unknown":
                member.position_unknown = True
            if change == "wrong_final":
                member.final_position_json = {"kind": "HANDOFF_POSITION", "location_code": "ELSEWHERE"}
            if change == "wrong_line":
                binding.workline_id = 8
            if change == "wrong_source":
                member.source_json = {**member.source_json, "slot_id": "OTHER"}
            if change == "archived":
                old.archived_at = now
                old.status = "ARCHIVED"
            evidence = await db.get(InboundEvidence, 31)
            mismatches = {
                "wrong_binding_operation": (binding, "resource_fence_id", "OTHER"),
                "wrong_binding_evidence": (binding, "source_evidence_id", 32),
                "wrong_owner_business": (old, "task_id", "OTHER-TASK"),
                "wrong_owner_line": (old, "workline_id", 8),
                "wrong_transport_line": (transport, "authority_workline_id", 8),
                "wrong_target": (member, "target_json", {"kind": "HANDOFF_POSITION", "location_code": "OTHER"}),
                "wrong_evidence_kind": (evidence, "kind", "WMS_EVENT"),
                "wrong_evidence_line": (evidence, "workline_id", 8),
                "wrong_evidence_operation": (evidence, "operation", "outbound.bin.return_batch@v1"),
                "wrong_evidence_id": (evidence, "operation_id", "019f0000-0000-7000-8000-000000000032"),
                "unapplied_batch": (evidence, "apply_status", "RECONCILING"),
            }
            if change in mismatches:
                obj, field, value = mismatches[change]
                setattr(obj, field, value)
            if change == "unpublished_batch":
                evidence.published_at = None
            if change == "wrong_batch_bin":
                evidence.normalized_payload = {**evidence.normalized_payload, "data": {"result": "RACK_FACE_DONE"}}
            if change in {"wrong_ready_bin", "wrong_ready_rack", "wrong_ready_face"}:
                bin_data = dict(evidence.normalized_payload["data"]["bins"][0])
                locator = dict(bin_data["source_locator"])
                if change == "wrong_ready_bin":
                    bin_data["bin_code"] = "A000000342"
                elif change == "wrong_ready_rack":
                    locator["rack_id"] = "OTHER"
                else:
                    locator["rack_face"] = "270"
                bin_data["source_locator"] = locator
                evidence.normalized_payload = {
                    **evidence.normalized_payload,
                    "data": {"result": "READY", "bins": [bin_data]},
                }
            if change in {
                "new_pending",
                "new_accepted",
                "new_reconciling",
                "new_unpublished",
                "other_bin_pending",
                "old_pending",
                "new_finished",
            }:
                next_status = {
                    "new_accepted": "ACCEPTED",
                    "new_reconciling": "RECONCILING",
                    "new_unpublished": "SUCCEEDED",
                    "new_finished": "SUCCEEDED",
                }.get(change, "PENDING")
                db.add(
                    TransportTask(
                        **{
                            **transport.model_dump(exclude={"id"}),
                            "transport_task_id": "new",
                            "client_request_id": "new",
                            "submit_operation_id": "new",
                            "status": next_status,
                            "published_outcome_version": 0 if change == "new_unpublished" else 1,
                        }
                    )
                )
                db.add(
                    TransportMember(
                        **{
                            **member.model_dump(exclude={"id"}),
                            "transport_task_id": "new",
                            "status": next_status,
                            "object_id": "A000000342" if change == "other_bin_pending" else "A000000341",
                        }
                    )
                )
                db.add(
                    TransportDecisionBinding(
                        workline_id=8,
                        picking_task_id=42,
                        correlation_id="new",
                        causal_token=99 if change == "old_pending" else 101,
                        step="MANUAL_PICKING_INBOUND_BATCH",
                        resource_fence_id="new",
                        source_evidence_id=32,
                        client_request_id="new",
                    )
                )
            await db.flush()
            owner = await BatchRepository().scan1_task_id(
                db, workline_id=7, transport_task_id="original", bin_code="A000000341", inlet_location="INLET"
            )
            expected = (
                31 if change in {"ended", "cancelled", "other_bin_pending", "old_pending", "new_finished"} else None
            )
            if change in {"unpublished", "new_pending", "new_accepted", "new_reconciling", "new_unpublished"}:
                expected = "WAIT_FOR_RESULT"
            assert owner == expected
            assert binding.picking_task_id == 31
            assert transport.transport_task_id == "original"
            assert transport.status == ("ACCEPTED" if change == "pending" else "SUCCEEDED")
    finally:
        await engine.dispose()
