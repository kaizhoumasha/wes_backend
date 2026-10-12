"""宿主确定 Result 待重放判定；不应用事实，不依赖真实数据库。"""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from src.app.transport.models import TransportEvidence, TransportMember, TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService

NOW = datetime(2026, 10, 11)
TARGET = {"kind": "RACK_POSITION", "location_code": "FIVE_RACK"}


def _case(*, bin_move=False):
    kind = "BIN_MOVE" if bin_move else "RACK_MOVE"
    task = TransportTask(
        transport_task_id="original",
        client_request_id="request",
        request_digest="digest",
        kind=kind,
        caller_json={"workline_id": "7"},
        request_json={"target_face": "90"},
        submit_operation_id="submit",
        submit_timestamp_ms=1,
        submit_request_body="original-body",
        submit_request_body_digest="digest",
        status="ACCEPTED",
        created_at=NOW,
        updated_at=NOW,
    )
    members = [
        TransportMember(
            transport_task_id="original",
            ordinal=index,
            object_type="BIN" if bin_move else "RACK",
            object_id=object_id,
            source_json={},
            target_json={"kind": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": object_id}
            if bin_move
            else TARGET,
            updated_at=NOW,
        )
        for index, object_id in enumerate(("B1", "B2") if bin_move else ("R1",))
    ]
    payload = {"kind": kind, "outcome_revision": 1}
    if bin_move:
        payload["results"] = [
            {"container_id": member.object_id, "status": "SUCCEEDED", "final_position": member.target_json}
            for member in members
        ]
    else:
        payload.update(rack_id="R1", status="SUCCEEDED", final_position=TARGET)
    evidence = TransportEvidence(
        operation="transport.task.resulted@v1",
        operation_id="result",
        transport_task_id="original",
        payload_json=payload,
        payload_digest="digest",
        received_at=NOW,
        outcome_revision=1,
        status="PENDING",
    )
    positions = (
        [
            SimpleNamespace(
                payload_json={
                    "milestone": "TARGET_PLACED",
                    "container_id": member.object_id,
                    "final_position": member.target_json,
                }
            )
            for member in members
        ]
        if bin_move
        else []
    )
    repository = SimpleNamespace(
        list_pending_result_evidence=AsyncMock(return_value=[evidence]),
        list_members=AsyncMock(return_value=members),
        list_applied_position_evidence=AsyncMock(return_value=positions),
    )
    service = TransportService(SimpleNamespace(), repository, SimpleNamespace(), result_timeout=timedelta(seconds=1))
    return service, repository, task, members, evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,expected",
    [
        ("success", True),
        ("failed", True),
        ("cancelled", True),
        ("wrong_kind", False),
        ("wrong_member", False),
        ("wrong_target", False),
        ("wrong_face", False),
        ("stale", False),
        ("unknown", False),
        ("missing_failure", False),
        ("success_failure", False),
        ("contradiction", False),
        ("wrong_position_type", False),
        ("terminal_task", False),
    ],
)
async def test_pending_result_requires_current_matching_definite_fact_and_is_read_only(case, expected):
    service, repository, task, members, evidence = _case()
    payload = evidence.payload_json
    if case == "failed":
        payload.update(status="FAILED", failure_code="RCS_EXECUTION_FAILED")
    elif case == "cancelled":
        payload.update(status="CANCELLED")
    elif case == "wrong_kind":
        payload["kind"] = "BIN_MOVE"
    elif case == "wrong_member":
        payload["rack_id"] = "other"
    elif case == "wrong_target":
        payload["final_position"] = {"kind": "RACK_POSITION", "location_code": "OTHER"}
    elif case == "wrong_face":
        payload["arrival_face"] = "270"
    elif case == "stale":
        task.last_applied_wms_outcome_revision = 1
    elif case == "unknown":
        payload.update(status="FAILED", failure_code="POSITION_UNKNOWN", position_unknown=True)
        del payload["final_position"]
    elif case == "missing_failure":
        payload["status"] = "FAILED"
    elif case == "success_failure":
        payload["failure_code"] = "RCS_EXECUTION_FAILED"
    elif case == "contradiction":
        members[0].final_position_json = {"kind": "RACK_POSITION", "location_code": "OTHER"}
    elif case == "wrong_position_type":
        payload["final_position"] = {"kind": "CONVEYOR_LOCATION", "location_code": "OTHER"}
    elif case == "terminal_task":
        task.status = "FAILED"
    before = deepcopy([task.model_dump(), evidence.model_dump(), *[member.model_dump() for member in members]])
    db = SimpleNamespace(flush=AsyncMock(), add=AsyncMock())
    assert await service.has_pending_definite_result_in_session(db, task) is expected
    assert [task.model_dump(), evidence.model_dump(), *[member.model_dump() for member in members]] == before
    db.flush.assert_not_awaited()
    db.add.assert_not_called()
    repository.list_pending_result_evidence.assert_awaited_once_with(db, "original")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,expected",
    [
        ("all_positions", True),
        ("missing_position", False),
        ("wrong_position", False),
        ("partial_members", False),
        ("duplicate_members", False),
    ],
)
async def test_bin_result_requires_full_frozen_members_and_applied_exact_positions(case, expected):
    service, repository, task, _, evidence = _case(bin_move=True)
    if case == "missing_position":
        repository.list_applied_position_evidence.return_value.pop()
    elif case == "wrong_position":
        repository.list_applied_position_evidence.return_value[0].payload_json["final_position"] = TARGET
    elif case == "partial_members":
        evidence.payload_json["results"].pop()
    elif case == "duplicate_members":
        evidence.payload_json["results"].append(evidence.payload_json["results"][0].copy())
    assert await service.has_pending_definite_result_in_session(SimpleNamespace(), task) is expected


@pytest.mark.asyncio
async def test_pending_result_query_uses_real_columns_and_frozen_task_identity():
    statements = []

    async def scalars(statement):
        statements.append(statement)
        return []

    assert await TransportRepository().list_pending_result_evidence(SimpleNamespace(scalars=scalars), "original") == []
    query = str(statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "transport_task_id = 'original'" in query
    assert "transport.task.resulted@v1" in query and "status = 'PENDING'" in query
    assert "FOR UPDATE" not in query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,message",
    [
        ("wrong_kind", "result kind differs from frozen task"),
        ("partial_members", "result members differ from frozen task"),
        ("wrong_target", "successful final position differs from frozen exact target"),
        ("missing_failure", "failed member requires failure_code"),
    ],
)
async def test_result_application_preserves_existing_contract_errors_before_writing(case, message):
    from src.app.transport.contracts import TransportContractError

    service, _, task, _, evidence = _case(bin_move=case == "partial_members")
    if case == "wrong_kind":
        evidence.payload_json["kind"] = "BIN_MOVE"
    elif case == "partial_members":
        evidence.payload_json["results"].pop()
    elif case == "wrong_target":
        evidence.payload_json["final_position"] = {"kind": "RACK_POSITION", "location_code": "OTHER"}
    elif case == "missing_failure":
        evidence.payload_json["status"] = "FAILED"
    service._lock_projection_authority_root = AsyncMock()
    before = task.model_dump()
    db = SimpleNamespace(flush=AsyncMock())
    with pytest.raises(TransportContractError, match=message):
        await service._apply_result_evidence(db, task, evidence)
    assert task.model_dump() == before
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_result_application_still_waits_for_required_position_evidence_without_mutation():
    service, repository, task, _, evidence = _case(bin_move=True)
    repository.list_applied_position_evidence.return_value.pop()
    service._lock_projection_authority_root = AsyncMock()
    before = task.model_dump()
    assert await service._apply_result_evidence(SimpleNamespace(), task, evidence) is False
    assert task.model_dump() == before
    service._lock_projection_authority_root.assert_not_awaited()
