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
    flow._batch_progress.scan1_source = AsyncMock(return_value=(31, "MOVE-A000000001"))
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
    flow._batch_progress.scan1_source = AsyncMock(side_effect=["WAIT_FOR_RESULT", (31, "MOVE-A000000001")])
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
    flow._batch_progress.scan1_source = AsyncMock(return_value=(31, "MOVE-A000000001") if owner is not None else None)
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
        "scan_before_result",
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
        "already_scanned",
        "ambiguous_source",
        "superseded_rejected",
        "superseded_failed",
        "superseded_failed_unknown",
        "superseded_failed_unpublished",
        "ambiguous_current_fact",
        "ambiguous_current_fact_unknown",
        "ambiguous_current_fact_wrong_phase",
        "ambiguous_current_fact_wrong_transport",
        "ambiguous_current_fact_wrong_inlet",
        "ambiguous_current_fact_unpublished",
        "ambiguous_current_fact_wrong_member",
    ],
)
async def test_scan1_source_matches_frozen_member_without_requiring_transport_completion(change):
    from datetime import datetime

    import wes_plugin_sdk as sdk
    from manual_picking.application.batch_progress_model import ManualPickingInboundBatchScan
    from manual_picking.application.batch_repository import BatchRepository
    from sqlalchemy import select
    from test_batch_repository import _new_sessions, _seed_current_inbound

    from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding
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
            if change == "scan_before_result":
                old.status = "EXECUTING"
                transport.status = "ACCEPTED"
                transport.outcome_version = transport.published_outcome_version = 0
                member.status = "PENDING"
                member.final_position_json = None
                transport.request_json = {
                    "moves": [
                        {"bin_code": member.object_id, "source": member.source_json, "target": member.target_json}
                    ]
                }
            if change == "already_scanned":
                db.add(
                    ManualPickingInboundBatchScan(
                        workline_id=7, operation_id=intent.operation_id, bin_code=member.object_id
                    )
                )
            if change == "ambiguous_source" or change.startswith("ambiguous_current_fact"):
                db.add(
                    TransportTask(
                        **{
                            **transport.model_dump(exclude={"id"}),
                            "transport_task_id": "duplicate",
                            "client_request_id": "duplicate",
                        }
                    )
                )
                db.add(TransportMember(**{**member.model_dump(exclude={"id"}), "transport_task_id": "duplicate"}))
                db.add(
                    TransportDecisionBinding(
                        **{
                            **binding.model_dump(exclude={"id"}),
                            "client_request_id": "duplicate",
                            "correlation_id": f"{intent.operation_id}:1",
                            "causal_token": 101,
                        }
                    )
                )
            if change.startswith("superseded_"):
                next_intent = sdk.wms_operations.outbound_bin_inbound_batch(
                    operation_id="019f0000-0000-7000-8000-000000000032",
                    task_id="OLD-TASK",
                    plan_revision=2,
                    rack_id="R1",
                    rack_face="90",
                )
                await _seed_current_inbound(db, next_intent, ready, evidence_id=32)
                db.add(
                    TransportTask(
                        **{
                            **transport.model_dump(exclude={"id"}),
                            "transport_task_id": "next-round",
                            "client_request_id": "next-round",
                            "status": "ACCEPTED",
                            "outcome_version": 0,
                            "published_outcome_version": 0,
                        }
                    )
                )
                db.add(
                    TransportMember(
                        **{
                            **member.model_dump(exclude={"id"}),
                            "transport_task_id": "next-round",
                            "status": "PENDING",
                            "final_position_json": None,
                        }
                    )
                )
                db.add(
                    TransportDecisionBinding(
                        **{
                            **binding.model_dump(exclude={"id"}),
                            "client_request_id": "next-round",
                            "correlation_id": f"{next_intent.operation_id}:0",
                            "resource_fence_id": next_intent.operation_id,
                            "source_evidence_id": 32,
                            "causal_token": 101,
                        }
                    )
                )
                transport.status = "REJECTED" if change == "superseded_rejected" else "FAILED"
                member.status = "FAILED"
                member.final_position_json = None if change == "superseded_rejected" else member.source_json
                member.position_unknown = change == "superseded_failed_unknown"
                if change == "superseded_failed_unpublished":
                    transport.published_outcome_version = 0
            if change.startswith("ambiguous_current_fact"):
                transport.status = "ACCEPTED"
                transport.outcome_version = transport.published_outcome_version = 0
                member.status, member.final_position_json = "PENDING", None
                projection = PositionProjection(
                    object_type="BIN",
                    object_id=member.object_id,
                    workline_id=7,
                    position_json={"kind": "HANDOFF_POSITION", "location_code": "INLET"},
                    source_operation_id="actual-new-result",
                    source_transport_task_id="duplicate",
                    source_causal_token=101,
                    source_effect_phase="FINAL_RESULT",
                    position_unknown=False,
                )
                db.add(projection)
                if change.endswith("_unknown"):
                    projection.position_unknown = True
                elif change.endswith("_wrong_phase"):
                    projection.source_effect_phase = "SUBMIT_ACCEPTED"
                elif change.endswith("_wrong_transport"):
                    projection.source_transport_task_id = "OTHER"
                elif change.endswith("_wrong_inlet"):
                    projection.position_json = {"kind": "HANDOFF_POSITION", "location_code": "ELSEWHERE"}
                elif change.endswith("_unpublished"):
                    duplicate = await db.scalar(
                        select(TransportTask).where(TransportTask.transport_task_id == "duplicate")
                    )
                    duplicate.published_outcome_version = 0
                elif change.endswith("_wrong_member"):
                    duplicate = await db.scalar(
                        select(TransportMember).where(TransportMember.transport_task_id == "duplicate")
                    )
                    duplicate.final_position_json = {"kind": "HANDOFF_POSITION", "location_code": "ELSEWHERE"}
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
            owner = await BatchRepository().scan1_source(
                db, workline_id=7, bin_code="A000000341", inlet_location="INLET"
            )
            expected = (
                (31, "original")
                if change
                in {
                    "ended",
                    "cancelled",
                    "pending",
                    "scan_before_result",
                    "unpublished",
                    "unknown",
                    "wrong_final",
                    "other_bin_pending",
                    "old_pending",
                    "new_finished",
                }
                else None
            )
            if change in {"new_pending", "new_accepted", "new_reconciling", "new_unpublished"}:
                expected = "WAIT_FOR_RESULT"
            if change in {"superseded_rejected", "superseded_failed"}:
                expected = (31, "next-round")
            if change == "ambiguous_current_fact":
                expected = (31, "duplicate")
            assert owner == expected
            assert binding.picking_task_id == 31
            assert transport.transport_task_id == "original"
            if change.startswith("superseded_"):
                assert transport.status == ("REJECTED" if change == "superseded_rejected" else "FAILED")
                assert member.status == "FAILED"
            else:
                assert transport.status == (
                    "ACCEPTED"
                    if change in {"pending", "scan_before_result"} or change.startswith("ambiguous_current_fact")
                    else "SUCCEEDED"
                )
            if change == "scan_before_result":
                flow, evidences, passages, commands, admissions = _setup(missing_projection=("BIN", member.object_id))
                flow._batch_progress = BatchRepository()
                flow._tasks.get_executing_for_workline_for_update = AsyncMock(return_value=old)
                flow._tasks.get_by_id_for_update = AsyncMock(return_value=old)
                flow._transport_reader.get_task = AsyncMock(return_value=transport)
                flow._source_racks.list_bin_source_racks = AsyncMock(
                    return_value=[SimpleNamespace(rack_id="R1", rack_face="90", cancelled_evidence_id=None)]
                )
                flow._worklines.line.position_bindings["INLET"]["location_id"] = "INLET"
                evidences.rows[1] = _scan(1, "S1", f"{member.object_id}-B")
                applied = await flow.apply_in_session(db, 1, workline_id=7)
                assert applied.disposition is BusinessEvidenceDisposition.APPLIED
                assert [request.task_type for request in commands.requests] == ["MOVE_FORWARD"]
                assert passages.rows[0].task_id == "OLD-TASK"
                assert admissions.intents == []
                await db.flush()
                scan = await db.scalar(select(ManualPickingInboundBatchScan))
                assert (scan.operation_id, scan.bin_code) == (intent.operation_id, member.object_id)
                assert transport.status == "ACCEPTED" and member.status == "PENDING"
                assert transport.outcome_version == transport.published_outcome_version == 0
                assert member.final_position_json is None
                assert transport.submit_operation_id == intent.operation_id
                assert transport.submit_request_body == "{}"
                await flow.apply_in_session(db, 1, workline_id=7)
                assert len(commands.requests) == len(passages.rows) == 1
                assert (
                    await flow._batch_progress.scan1_source(
                        db, workline_id=7, bin_code=member.object_id, inlet_location="INLET"
                    )
                    is None
                )

                late_result = SimpleNamespace(
                    transport_task_id="original",
                    normalized_payload={
                        "transport_task_id": "original",
                        "client_request_id": "original-client",
                        "caller": {"workline_id": "7"},
                        "step": "MANUAL_PICKING_INBOUND_BATCH",
                        "batch_operation_id": intent.operation_id,
                        "status": "SUCCEEDED",
                        "members": [{"object_id": member.object_id, "final_position": member.target_json}],
                    },
                )
                assert await flow._apply_transport_result(db, late_result, 7) == "SUCCEEDED"
                assert len(commands.requests) == len(passages.rows) == 1
    finally:
        await engine.dispose()
