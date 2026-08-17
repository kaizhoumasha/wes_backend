"""粗分 WMS 确认与入站事件的 PostgreSQL 事务验收。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, or_, select, update

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceConflict,
    InboundEvidenceKind,
    MaterialExecution,
    MaterialExecutionStatus,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.repositories import WmsConfirmationRepository
from src.app.execution.services import (
    WmsBusinessWaitFollowUp,
    WmsConfirmationFollowUpPlan,
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationService,
)
from src.app.wms_adapter.inbound_adapter import (
    InboundDispatchCode,
    WmsInboundBusinessWaitPlanner,
)
from src.app.wms_adapter.inbound_event_handler import InboundEventEvidenceRecorder, InboundEventHandler
from src.app.wms_adapter.inbound_wire import ADMISSION_OPERATION, RECOVERY_OPERATION
from src.app.workline.models.line_run_epoch import LineRunEpoch
from src.app.workline.models.workline import LineType, WorkLine
from src.core.uuid7 import new_uuid7

PREFIX = "WMS-INBOUND-"
RECOVERY_OPERATION_IDS: set[str] = set()


async def _seed_execution(db) -> MaterialExecution:  # type: ignore[no-untyped-def]
    identity = uuid4().hex
    line = WorkLine(
        line_code=f"{PREFIX}{identity[:12]}",
        line_name="WMS inbound confirmation",
        line_type=LineType.AUTO,
    )
    db.add(line)
    await db.flush()
    epoch = LineRunEpoch(
        epoch_code=f"{PREFIX}EPOCH-{identity}",
        workline_id=line.id,
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
        flow_mode="ROUGH_SORT_INBOUND",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        started_at=datetime(2026, 8, 16),
    )
    db.add(epoch)
    await db.flush()
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=f"{PREFIX}SCAN-{identity}",
        payload_digest="c" * 64,
        normalized_payload={"source_event_id": f"{PREFIX}SCAN-{identity}"},
        received_at=datetime(2026, 8, 16),
        line_run_epoch_id=epoch.id,
        device_code=f"{PREFIX}DEVICE-{identity}",
    )
    db.add(evidence)
    await db.flush()
    execution = MaterialExecution(
        execution_code=f"{PREFIX}EXEC-{identity}",
        material_trace_id=f"{PREFIX}TRACE-{identity}",
        workline_id=line.id,
        line_run_epoch_id=epoch.id,
        status=MaterialExecutionStatus.CREATED,
        last_transition_reason="SCAN_ACCEPTED",
        last_transition_evidence_id=evidence.id,
        status_changed_at=datetime(2026, 8, 16),
    )
    db.add(execution)
    await db.flush()
    evidence.material_execution_id = execution.id
    await db.flush()
    return execution


def _request(operation_id: str, execution_code: str) -> dict[str, object]:
    return {
        "operation_id": operation_id,
        "operation": ADMISSION_OPERATION,
        "timestamp": 1,
        "data": {
            "material_execution_id": execution_code,
            "material_trace_id": "TRACE-1",
            "six_in_one": {
                "LotCode": "LOT",
                "DateCode": "DATE",
                "Qty": "1",
                "ProductNo": "PN",
                "MfrPN": "MFR",
                "PONumber": "PO",
            },
            "measurements": {"diameter_mm": "1.000", "thickness_mm": "0.500"},
            "shape_result": "PASS",
            "line_run_epoch_id": "EPOCH-1",
            "workline_code": "WL-1",
            "source_position": {"type": "HANDOFF_POSITION", "location_code": "IN-1"},
        },
    }


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest_asyncio.fixture(autouse=True)
async def cleanup_rows(integration_session_factory):  # type: ignore[no-untyped-def]
    yield
    async with integration_session_factory.begin() as db:
        execution_ids = select(MaterialExecution.id).where(MaterialExecution.execution_code.like(f"{PREFIX}%"))
        evidence_filters = [
            InboundEvidence.source_identity.like(f"{PREFIX}%"),
            InboundEvidence.material_execution_id.in_(execution_ids),
        ]
        if RECOVERY_OPERATION_IDS:
            evidence_filters.append(InboundEvidence.operation_id.in_(RECOVERY_OPERATION_IDS))
        evidence_ids = select(InboundEvidence.id).where(or_(*evidence_filters))
        epoch_ids = select(LineRunEpoch.id).where(LineRunEpoch.epoch_code.like(f"{PREFIX}%"))
        line_ids = select(WorkLine.id).where(WorkLine.line_code.like(f"{PREFIX}%"))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.material_execution_id.in_(execution_ids)))
        await db.execute(
            delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id.in_(evidence_ids))
        )
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id.in_(execution_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id.in_(epoch_ids)))
        await db.execute(delete(WorkLine).where(WorkLine.id.in_(line_ids)))
    RECOVERY_OPERATION_IDS.clear()


@pytest.mark.asyncio
async def test_claim_is_exclusive_fifo_and_each_batch_is_capped_at_100(integration_session_factory) -> None:  # type: ignore[no-untyped-def]
    repository = WmsConfirmationRepository()
    now = datetime(2026, 8, 16)
    async with integration_session_factory.begin() as setup:
        execution = await _seed_execution(setup)
        for index in range(101):
            operation_id = new_uuid7(timestamp_ms=index + 1, random_bits=index)
            payload = _request(operation_id, execution.execution_code)
            setup.add(
                WmsConfirmation(
                    operation=ADMISSION_OPERATION,
                    operation_id=operation_id,
                    material_execution_id=execution.id,
                    request_digest=_digest(payload),
                    request_payload=payload,
                    deadline_at=now + timedelta(minutes=5),
                    created_at=now + timedelta(microseconds=index),
                )
            )

    first = integration_session_factory()
    second = integration_session_factory()
    try:
        async with first.begin():
            first_claim = await repository.claim_eligible(
                first,
                now=now,
                claim_token="claim-1",
                claim_expires_at=now + timedelta(minutes=1),
                limit=100,
            )
            async with second.begin():
                second_claim = await repository.claim_eligible(
                    second,
                    now=now,
                    claim_token="claim-2",
                    claim_expires_at=now + timedelta(minutes=1),
                    limit=100,
                )
        assert len(first_claim) == 100
        assert len(second_claim) == 1
        assert {item.id for item in first_claim}.isdisjoint({item.id for item in second_claim})
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_claim_fifo_uses_creation_order_across_due_retry_and_new_pending(integration_session_factory) -> None:  # type: ignore[no-untyped-def]
    repository = WmsConfirmationRepository()
    now = datetime(2026, 8, 16)
    operation_ids: list[str] = []
    async with integration_session_factory.begin() as setup:
        execution = await _seed_execution(setup)
        for index, (created_at, next_attempt_at) in enumerate(
            [
                (now - timedelta(minutes=2), now - timedelta(seconds=1)),
                (now - timedelta(minutes=1), None),
            ]
        ):
            operation_id = new_uuid7(timestamp_ms=index + 201, random_bits=index)
            operation_ids.append(operation_id)
            payload = _request(operation_id, execution.execution_code)
            setup.add(
                WmsConfirmation(
                    operation=ADMISSION_OPERATION,
                    operation_id=operation_id,
                    material_execution_id=execution.id,
                    request_digest=_digest(payload),
                    request_payload=payload,
                    deadline_at=now + timedelta(minutes=5),
                    retry_eligible=next_attempt_at is not None,
                    next_attempt_at=next_attempt_at,
                    created_at=created_at,
                )
            )

    async with integration_session_factory.begin() as db:
        claimed = await repository.claim_eligible(
            db,
            now=now,
            claim_token="claim-fifo",
            claim_expires_at=now + timedelta(minutes=1),
            limit=2,
        )

    assert [item.operation_id for item in claimed] == operation_ids


class _DispatchAdapter:
    async def dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
        operation_id = kwargs["operation_id"]
        if kwargs["request_payload"]["data"]["workline_code"] == "EXPIRED":
            raise AssertionError("过期确认不得调用 HTTP")
        if kwargs["request_payload"]["data"]["workline_code"] == "UNKNOWN":
            return SimpleNamespace(
                code=InboundDispatchCode.DELIVERY_UNKNOWN,
                normalized_response=None,
                response_result=None,
                retry_after_ms=None,
                follow_up_plan=None,
            )
        return SimpleNamespace(
            code=InboundDispatchCode.DETERMINATE,
            normalized_response={
                "operation_id": operation_id,
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
            },
            response_result="ACCEPT",
            retry_after_ms=None,
            follow_up_plan=None,
        )


class _ConflictDuringDispatchAdapter:
    def __init__(self, integration_session_factory, *, material_execution_id: int, deadline_at: datetime) -> None:  # type: ignore[no-untyped-def]
        self._sessions = integration_session_factory
        self._material_execution_id = material_execution_id
        self._deadline_at = deadline_at

    async def dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
        conflicting_payload = json.loads(json.dumps(kwargs["request_payload"]))
        conflicting_payload["data"]["workline_code"] = "CONFLICT-DURING-HTTP"
        async with self._sessions.begin() as db:
            conflict = await WmsConfirmationService(repository=WmsConfirmationRepository()).create_or_get(
                db,
                operation=kwargs["operation"],
                operation_id=kwargs["operation_id"],
                material_execution_id=self._material_execution_id,
                request_payload=conflicting_payload,
                deadline_at=self._deadline_at,
                created_at=datetime(2026, 8, 16, 0, 1),
            )
        assert isinstance(conflict, WmsConfirmationIdentityConflictResult)
        return SimpleNamespace(
            code=InboundDispatchCode.DETERMINATE,
            normalized_response={
                "operation_id": kwargs["operation_id"],
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
            },
            response_result="ACCEPT",
            retry_after_ms=None,
            follow_up_plan=None,
        )


class _WaitDispatchAdapter:
    async def dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            code=InboundDispatchCode.DETERMINATE,
            normalized_response={
                "operation_id": kwargs["operation_id"],
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "WAIT", "reason_code": "CELL_PENDING", "retry_after_ms": 250},
            },
            response_result="WAIT",
            retry_after_ms=250,
            follow_up_plan=WmsConfirmationFollowUpPlan(retry_after_ms=250),
        )


class _OversizedTimestampPlanner:
    def __init__(self, operation_id: str) -> None:
        self._operation_id = operation_id

    def plan(
        self,
        confirmation: WmsConfirmation,
        planning: WmsConfirmationFollowUpPlan,
    ) -> WmsBusinessWaitFollowUp:
        assert planning.retry_after_ms == 250
        assert confirmation.completed_at is not None
        request_payload = json.loads(json.dumps(confirmation.request_payload))
        request_payload["operation_id"] = self._operation_id
        request_payload["timestamp"] = 2**63
        return WmsBusinessWaitFollowUp(
            operation=confirmation.operation,
            operation_id=self._operation_id,
            request_payload=request_payload,
            next_attempt_at=confirmation.completed_at + timedelta(milliseconds=250),
        )


@pytest.mark.asyncio
async def test_dispatch_rechecks_deadline_retries_same_identity_and_commits_evidence_before_completion(
    integration_session_factory,
) -> None:
    now = datetime(2026, 8, 16)
    ids: dict[str, int] = {}
    async with integration_session_factory.begin() as db:
        execution = await _seed_execution(db)
        for name, deadline in {
            "COMPLETE": now + timedelta(minutes=5),
            "EXPIRED": now,
            "UNKNOWN": now + timedelta(minutes=5),
        }.items():
            operation_id = new_uuid7()
            payload = _request(operation_id, execution.execution_code)
            payload["data"]["workline_code"] = name
            confirmation = WmsConfirmation(
                operation=ADMISSION_OPERATION,
                operation_id=operation_id,
                material_execution_id=execution.id,
                request_digest=_digest(payload),
                request_payload=payload,
                deadline_at=deadline,
                created_at=now,
            )
            db.add(confirmation)
            await db.flush()
            ids[name] = confirmation.id

    service = WmsConfirmationService(
        repository=WmsConfirmationRepository(),
        session_factory=integration_session_factory,
        adapter=_DispatchAdapter(),
    )
    assert await service.dispatch_batch(limit=10, now=now) == 3

    async with integration_session_factory() as db:
        rows = {
            row.id: row
            for row in (await db.execute(select(WmsConfirmation).where(WmsConfirmation.id.in_(ids.values())))).scalars()
        }
        completed = rows[ids["COMPLETE"]]
        assert completed.status == WmsConfirmationStatus.COMPLETED
        assert completed.response_evidence_id is not None
        assert await db.get(InboundEvidence, completed.response_evidence_id) is not None
        assert rows[ids["EXPIRED"]].status == WmsConfirmationStatus.RECONCILING
        unknown = rows[ids["UNKNOWN"]]
        assert unknown.status == WmsConfirmationStatus.PENDING
        assert unknown.retry_eligible is True
        assert unknown.next_attempt_at == now + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_business_wait_completion_and_follow_up_are_committed_together(integration_session_factory) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 8, 16)
    follow_up_operation_id = new_uuid7()
    async with integration_session_factory.begin() as db:
        execution = await _seed_execution(db)
        original_operation_id = new_uuid7()
        payload = _request(original_operation_id, execution.execution_code)
        confirmation = WmsConfirmation(
            operation=ADMISSION_OPERATION,
            operation_id=original_operation_id,
            material_execution_id=execution.id,
            request_digest=_digest(payload),
            request_payload=payload,
            deadline_at=now + timedelta(minutes=5),
            created_at=now,
        )
        db.add(confirmation)
        await db.flush()
        execution_id = execution.id

    service = WmsConfirmationService(
        repository=WmsConfirmationRepository(),
        session_factory=integration_session_factory,
        adapter=_WaitDispatchAdapter(),
        business_wait_planner=WmsInboundBusinessWaitPlanner(
            operation_id_factory=lambda: follow_up_operation_id,
        ),
    )

    assert await service.dispatch_batch(limit=1, now=now) == 1
    async with integration_session_factory() as db:
        rows = list(
            (
                await db.execute(select(WmsConfirmation).where(WmsConfirmation.material_execution_id == execution_id))
            ).scalars()
        )
        by_operation_id = {row.operation_id: row for row in rows}
        original = by_operation_id[original_operation_id]
        follow_up = by_operation_id[follow_up_operation_id]
        assert original.status == WmsConfirmationStatus.COMPLETED
        assert original.response_result == "WAIT"
        assert original.response_evidence_id is not None
        assert await db.get(InboundEvidence, original.response_evidence_id) is not None
        assert follow_up.status == WmsConfirmationStatus.PENDING
        assert follow_up.next_attempt_at == now + timedelta(milliseconds=250)
        assert follow_up.request_payload["operation_id"] == follow_up_operation_id
        assert follow_up.deadline_at == original.deadline_at


@pytest.mark.asyncio
async def test_oversized_business_wait_timestamp_preserves_response_evidence_before_reconciling(
    integration_session_factory,
) -> None:
    now = datetime(2026, 8, 16)
    follow_up_operation_id = new_uuid7()
    async with integration_session_factory.begin() as db:
        execution = await _seed_execution(db)
        original_operation_id = new_uuid7()
        payload = _request(original_operation_id, execution.execution_code)
        confirmation = WmsConfirmation(
            operation=ADMISSION_OPERATION,
            operation_id=original_operation_id,
            material_execution_id=execution.id,
            request_digest=_digest(payload),
            request_payload=payload,
            deadline_at=now + timedelta(minutes=5),
            created_at=now,
        )
        db.add(confirmation)
        await db.flush()
        confirmation_id = confirmation.id
        execution_id = execution.id

    service = WmsConfirmationService(
        repository=WmsConfirmationRepository(),
        session_factory=integration_session_factory,
        adapter=_WaitDispatchAdapter(),
        business_wait_planner=_OversizedTimestampPlanner(follow_up_operation_id),
    )

    assert await service.dispatch_batch(limit=1, now=now) == 1
    async with integration_session_factory() as db:
        rows = list(
            (
                await db.execute(select(WmsConfirmation).where(WmsConfirmation.material_execution_id == execution_id))
            ).scalars()
        )
        assert len(rows) == 1
        original = rows[0]
        assert original.id == confirmation_id
        assert original.status == WmsConfirmationStatus.RECONCILING
        assert original.response_result == "WAIT"
        assert original.response_evidence_id is not None
        assert await db.get(InboundEvidence, original.response_evidence_id) is not None


@pytest.mark.asyncio
async def test_inflight_identity_conflict_fences_late_http_response(integration_session_factory) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 8, 16)
    deadline_at = now + timedelta(minutes=5)
    async with integration_session_factory.begin() as db:
        execution = await _seed_execution(db)
        operation_id = new_uuid7()
        payload = _request(operation_id, execution.execution_code)
        confirmation = WmsConfirmation(
            operation=ADMISSION_OPERATION,
            operation_id=operation_id,
            material_execution_id=execution.id,
            request_digest=_digest(payload),
            request_payload=payload,
            deadline_at=deadline_at,
            created_at=now,
        )
        db.add(confirmation)
        await db.flush()
        confirmation_id = confirmation.id
        execution_id = execution.id

    repository = WmsConfirmationRepository()
    service = WmsConfirmationService(
        repository=repository,
        session_factory=integration_session_factory,
        adapter=_ConflictDuringDispatchAdapter(
            integration_session_factory,
            material_execution_id=execution_id,
            deadline_at=deadline_at,
        ),
    )
    assert await service.dispatch_batch(limit=1, now=now) == 1

    async with integration_session_factory.begin() as db:
        persisted = await db.get(WmsConfirmation, confirmation_id)
        assert persisted is not None
        assert persisted.status == WmsConfirmationStatus.RECONCILING
        assert persisted.response_evidence_id is None
        assert persisted.claim_token is None
        assert persisted.claimed_at is None
        assert persisted.claim_expires_at is None
        assert persisted.retry_eligible is False
        assert persisted.next_attempt_at is None

        persisted.claim_token = "stale-late-response"
        await db.flush()
        assert await repository.get_claimed_for_update(db, confirmation_id, "stale-late-response") is None
        assert persisted.status == WmsConfirmationStatus.RECONCILING


def _recovery(
    operation_id: str,
    *,
    reason_code: str,
    execution_code: str,
    material_trace_id: str,
    reconciling_evidence_id: int,
) -> bytes:
    return json.dumps(
        {
            "operation_id": operation_id,
            "operation": RECOVERY_OPERATION,
            "timestamp": 1,
            "data": {
                "recovery_id": f"{PREFIX}REC-1",
                "material_execution_id": execution_code,
                "material_trace_id": material_trace_id,
                "reconciling_evidence_id": str(reconciling_evidence_id),
                "decision": "ABORT",
                "authoritative_position": None,
                "reason_code": reason_code,
            },
        },
        separators=(",", ":"),
    ).encode()


@pytest.mark.asyncio
async def test_recovery_identity_conflict_is_committed_before_409(integration_session_factory) -> None:  # type: ignore[no-untyped-def]
    operation_id = new_uuid7()
    RECOVERY_OPERATION_IDS.add(operation_id)
    handler = InboundEventHandler(InboundEventEvidenceRecorder(integration_session_factory))
    async with integration_session_factory.begin() as db:
        execution = await _seed_execution(db)
        execution.status = MaterialExecutionStatus.RECONCILING
        await db.flush()

    first = await handler.handle(
        _recovery(
            operation_id,
            reason_code="MANUAL_ABORT",
            execution_code=execution.execution_code,
            material_trace_id=execution.material_trace_id,
            reconciling_evidence_id=execution.last_transition_evidence_id,
        )
    )
    conflict = await handler.handle(
        _recovery(
            operation_id,
            reason_code="CHANGED_REASON",
            execution_code=execution.execution_code,
            material_trace_id=execution.material_trace_id,
            reconciling_evidence_id=execution.last_transition_evidence_id,
        )
    )

    assert (first.http_status, conflict.http_status) == (202, 409)
    async with integration_session_factory() as db:
        persisted = await db.scalar(
            select(InboundEvidenceConflict).where(
                InboundEvidenceConflict.source_identity == f"{RECOVERY_OPERATION}:{operation_id}"
            )
        )
    assert persisted is not None
    assert persisted.reason_code == "SOURCE_IDENTITY_PAYLOAD_CONFLICT"


@pytest.mark.asyncio
async def test_stale_recovery_causal_fence_rolls_back_new_evidence(integration_session_factory) -> None:  # type: ignore[no-untyped-def]
    operation_id = new_uuid7()
    RECOVERY_OPERATION_IDS.add(operation_id)
    handler = InboundEventHandler(InboundEventEvidenceRecorder(integration_session_factory))
    async with integration_session_factory.begin() as db:
        execution = await _seed_execution(db)
        stale_causal_id = execution.last_transition_evidence_id
        newer_causal = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=f"{PREFIX}NEWER-CAUSAL-{uuid4().hex}",
            payload_digest="d" * 64,
            normalized_payload={"source_event_id": f"{PREFIX}NEWER"},
            received_at=datetime(2026, 8, 16, 0, 1),
            line_run_epoch_id=execution.line_run_epoch_id,
            material_execution_id=execution.id,
            device_code=f"{PREFIX}DEVICE-NEWER",
        )
        db.add(newer_causal)
        await db.flush()
        execution.status = MaterialExecutionStatus.RECONCILING
        execution.last_transition_evidence_id = newer_causal.id
        execution_code = execution.execution_code
        material_trace_id = execution.material_trace_id
        await db.flush()

    response = await handler.handle(
        _recovery(
            operation_id,
            reason_code="STALE_OPERATOR_VIEW",
            execution_code=execution_code,
            material_trace_id=material_trace_id,
            reconciling_evidence_id=stale_causal_id,
        )
    )

    assert response.http_status == 409
    async with integration_session_factory() as db:
        persisted = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.source_identity == f"{RECOVERY_OPERATION}:{operation_id}")
        )
    assert persisted is None
