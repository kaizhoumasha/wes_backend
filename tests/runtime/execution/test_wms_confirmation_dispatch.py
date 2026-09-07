from __future__ import annotations

import hashlib
import json
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.execution.models import InboundEvidenceApplyStatus, WmsConfirmation, WmsConfirmationStatus
from src.app.execution.services import (
    InboundEvidenceAcceptance,
    InboundEvidenceConflictResult,
    WmsConfirmationFollowUp,
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationService,
)
from src.app.wms_adapter.client import WmsClient
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.app.wms_adapter.inbound_material.typed import decode_request
from src.app.wms_adapter.inbound_material.wire import (
    ADMISSION_OPERATION,
    NG_PLACEMENT_OPERATION,
    PLACEMENT_OPERATION,
)
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpFailureKind, OutboundHttpResult
from tests.contracts.wms_adapter.inbound_material.support import (
    OPERATION_ID,
    OTHER_OPERATION_ID,
    THIRD_OPERATION_ID,
    _digest,
    _request,
    _response,
    _Transport,
)


class _Transaction(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        return None


class _Sessions:
    def begin(self) -> _Transaction:
        return _Transaction()


class _ConfirmationRepository:
    def __init__(self, confirmations: list[WmsConfirmation]) -> None:
        self.confirmations = confirmations

    async def lock_identity(self, db, operation, operation_id):  # type: ignore[no-untyped-def]
        return None

    async def get_by_identity_for_update(self, db, operation, operation_id):  # type: ignore[no-untyped-def]
        return next(
            (
                confirmation
                for confirmation in self.confirmations
                if confirmation.operation == operation and confirmation.operation_id == operation_id
            ),
            None,
        )

    async def add(self, db, confirmation):  # type: ignore[no-untyped-def]
        confirmation.id = max((item.id or 0 for item in self.confirmations), default=0) + 1
        self.confirmations.append(confirmation)
        return confirmation

    async def claim_eligible(self, db, *, now, claim_token, claim_expires_at, limit):  # type: ignore[no-untyped-def]
        claimed = [
            confirmation
            for confirmation in self.confirmations
            if (
                confirmation.status == WmsConfirmationStatus.PENDING
                and (confirmation.next_attempt_at is None or confirmation.next_attempt_at <= now)
            )
            or (
                confirmation.status == WmsConfirmationStatus.DISPATCHING
                and confirmation.claim_expires_at is not None
                and confirmation.claim_expires_at <= now
            )
        ][:limit]
        for confirmation in claimed:
            confirmation.status = WmsConfirmationStatus.DISPATCHING
            confirmation.claim_token = claim_token
            confirmation.claimed_at = now
            confirmation.claim_expires_at = claim_expires_at
            confirmation.attempt_count += 1
        return claimed

    async def get_claimed_for_update(self, db, confirmation_id, claim_token):  # type: ignore[no-untyped-def]
        return next(
            (
                confirmation
                for confirmation in self.confirmations
                if confirmation.id == confirmation_id
                and confirmation.status == WmsConfirmationStatus.DISPATCHING
                and confirmation.claim_token == claim_token
            ),
            None,
        )

    async def flush(self, db):  # type: ignore[no-untyped-def]
        return None


class _EvidenceService:
    def __init__(self) -> None:
        self.calls = []
        self.dbs = []

    async def accept(self, db, **kwargs):  # type: ignore[no-untyped-def]
        self.dbs.append(db)
        self.calls.append(kwargs)
        return InboundEvidenceAcceptance(SimpleNamespace(id=501, received_at=kwargs["received_at"]), duplicate=False)


class _ConflictEvidenceService(_EvidenceService):
    async def accept(self, db, **kwargs):  # type: ignore[no-untyped-def]
        await super().accept(db, **kwargs)
        return InboundEvidenceConflictResult(
            evidence=SimpleNamespace(id=501),
            conflict=SimpleNamespace(),
            source_identity=kwargs["source_identity"],
        )


_DEFAULT_EXECUTION = object()


class _ExecutionRepository:
    def __init__(self, execution: object | None = _DEFAULT_EXECUTION) -> None:
        self.execution = SimpleNamespace(id=21, workline_id=11) if execution is _DEFAULT_EXECUTION else execution
        self.calls = []

    async def get_by_id(self, db, execution_id):  # type: ignore[no-untyped-def]
        self.calls.append((db, execution_id))
        return self.execution


class _TaskQueue:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.execution_wakes = 0
        self.wms_wakes = 0
        self.error = error

    def enqueue_execution_facts(self) -> None:
        self.execution_wakes += 1
        if self.error is not None:
            raise self.error

    def enqueue_wms_confirmations(self) -> None:
        self.wms_wakes += 1


class _Adapter:
    def __init__(self, result) -> None:  # type: ignore[no-untyped-def]
        self.result = result
        self.calls = []

    async def dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self.result


class _PickingTaskOwner:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.calls: list[tuple[object, int, str]] = []

    async def validate_response_owner(
        self,
        db: object,
        *,
        picking_task_id: int,
        operation: str,
    ) -> bool:
        self.calls.append((db, picking_task_id, operation))
        return self.valid


class _FollowUpPlanner:
    def __init__(self, operation_ids: tuple[str, ...] = (OTHER_OPERATION_ID,)) -> None:
        self._operation_ids = iter(operation_ids)

    async def plan(
        self,
        _db: object,
        confirmation: WmsConfirmation,
        *,
        response_result: str,
        retry_after_ms: int,
        received_at: datetime,
    ) -> WmsConfirmationFollowUp | None:
        if response_result != "WAIT":
            return None
        operation_id = next(self._operation_ids)

        return WmsConfirmationFollowUp(
            intent=replace(decode_request(confirmation.request_payload, fact_id="wait"), operation_id=operation_id),
            next_attempt_at=received_at + timedelta(milliseconds=retry_after_ms),
        )


class _ConflictDuringDispatchAdapter:
    def __init__(self, conflict_service: WmsConfirmationService, confirmation: WmsConfirmation, now: datetime) -> None:
        self._conflict_service = conflict_service
        self._confirmation = confirmation
        self._now = now

    async def dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
        conflicting_payload = json.loads(json.dumps(kwargs["request_payload"]))
        conflicting_payload["data"]["workline_code"] = "CONFLICT-DURING-HTTP"
        conflict = await self._conflict_service.create_or_get(
            object(),
            operation=kwargs["operation"],
            operation_id=kwargs["operation_id"],
            material_execution_id=self._confirmation.material_execution_id,
            request_payload=conflicting_payload,
            deadline_at=self._confirmation.deadline_at,
            created_at=self._now,
        )
        assert isinstance(conflict, WmsConfirmationIdentityConflictResult)
        return SimpleNamespace(
            code=WmsDispatchCode.DETERMINATE,
            normalized_response={
                "operation_id": kwargs["operation_id"],
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
            },
            response_result="ACCEPT",
            retry_after_ms=None,
        )


def _confirmation(identifier: int, now: datetime) -> WmsConfirmation:
    payload = _request()
    return WmsConfirmation(
        id=identifier,
        operation=ADMISSION_OPERATION,
        operation_id=f"019f12d0-58d7-7b4d-a23a-{identifier:012x}",
        material_execution_id=21,
        request_digest=_digest(payload),
        request_payload={**payload, "operation_id": f"019f12d0-58d7-7b4d-a23a-{identifier:012x}"},
        deadline_at=now + timedelta(minutes=5),
        created_at=now,
        updated_at=now,
    )


def _picking_confirmation(identifier: int, now: datetime) -> WmsConfirmation:
    operation_id = f"019f3400-0e17-7d2a-b944-{identifier:012x}"
    payload = {
        "operation_id": operation_id,
        "operation": "outbound.picking_task.prepare@v1",
        "timestamp": int(now.timestamp() * 1000),
        "data": {"task_id": "PICK-1", "workline_code": "LINE-1"},
    }
    return WmsConfirmation(
        id=identifier,
        operation="outbound.picking_task.prepare@v1",
        operation_id=operation_id,
        picking_task_id=31,
        request_digest=_digest(payload),
        request_payload=payload,
        deadline_at=now + timedelta(seconds=30),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_picking_prepare_response_uses_owner_port_and_never_enters_material_decision_queue() -> None:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    confirmation = _picking_confirmation(1, now)
    repository = _ConfirmationRepository([confirmation])
    evidence = _EvidenceService()
    owner = _PickingTaskOwner()
    execution_repository = _ExecutionRepository()
    queue = _TaskQueue()
    service = WmsConfirmationService(
        repository=repository,
        execution_repository=execution_repository,
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_Adapter(
            SimpleNamespace(
                code=WmsDispatchCode.DETERMINATE,
                normalized_response={
                    "operation_id": confirmation.operation_id,
                    "code": "PREPARE_ACCEPTED",
                    "timestamp": 2,
                    "data": {},
                },
                response_result="PREPARE_ACCEPTED",
                retry_after_ms=None,
            )
        ),
        evidence_service=evidence,  # type: ignore[arg-type]
        picking_task_owner=owner,
        task_queue_gateway=queue,  # type: ignore[arg-type]
    )

    assert await service.dispatch_batch(now=now) == 1
    assert confirmation.status == WmsConfirmationStatus.COMPLETED
    assert evidence.calls[0]["workline_id"] is None
    assert evidence.calls[0]["material_execution_id"] is None
    assert owner.calls[0][1:] == (31, "outbound.picking_task.prepare@v1")
    assert execution_repository.calls == []
    assert queue.execution_wakes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_kind", ["picking_task", "workline"])
async def test_received_response_is_preserved_when_owner_changes_during_http(owner_kind: str) -> None:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    confirmation = _picking_confirmation(1, now)
    if owner_kind == "workline":
        confirmation.picking_task_id = None
        confirmation.workline_id = 11
        confirmation.operation = "outbound.bin.return_batch@v1"
    else:
        confirmation.operation = "outbound.material.decide@v1"
    confirmation.request_payload["operation"] = confirmation.operation
    confirmation.request_digest = _digest(confirmation.request_payload)
    response = {"operation_id": confirmation.operation_id, "code": "DECIDED", "data": {"result": "WAIT"}}
    evidence = _EvidenceService()
    queue = _TaskQueue()
    owner = SimpleNamespace(
        validate_response_owner=AsyncMock(side_effect=[True, False]),
        validate_owner=AsyncMock(side_effect=[True, False]),
    )
    service = WmsConfirmationService(
        repository=_ConfirmationRepository([confirmation]),
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_Adapter(
            SimpleNamespace(
                code=WmsDispatchCode.DETERMINATE,
                normalized_response=response,
                response_result="WAIT",
                retry_after_ms=None,
            )
        ),
        evidence_service=evidence,  # type: ignore[arg-type]
        picking_task_owner=owner,
        workline_owner=owner,
        task_queue_gateway=queue,  # type: ignore[arg-type]
    )

    assert await service.dispatch_batch(now=now) == 1
    assert [call["normalized_payload"] for call in evidence.calls] == [response]
    assert evidence.calls[0]["apply_status"] == InboundEvidenceApplyStatus.RECONCILING
    assert evidence.calls[0]["source_identity"] == f"{confirmation.operation}:{confirmation.operation_id}"
    assert confirmation.status == WmsConfirmationStatus.RECONCILING
    assert confirmation.completed_at is None
    assert confirmation.retry_eligible is False
    assert queue.execution_wakes == queue.wms_wakes == 0


@pytest.mark.asyncio
async def test_picking_prepare_is_not_sent_without_explicit_business_owner_port() -> None:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    confirmation = _picking_confirmation(1, now)
    repository = _ConfirmationRepository([confirmation])
    adapter = _Adapter(
        SimpleNamespace(
            code=WmsDispatchCode.DETERMINATE,
            normalized_response={},
            response_result="PREPARE_ACCEPTED",
            retry_after_ms=None,
        )
    )
    service = WmsConfirmationService(
        repository=repository,
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=adapter,
        evidence_service=_EvidenceService(),  # type: ignore[arg-type]
    )

    assert await service.dispatch_batch(now=now) == 1
    assert adapter.calls == []
    assert confirmation.status == WmsConfirmationStatus.RECONCILING


@pytest.mark.asyncio
async def test_confirmation_dispatch_batch_is_bounded_and_completes_only_after_response_evidence() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmations = [_confirmation(identifier, now) for identifier in range(1, 102)]
    for confirmation in confirmations:
        confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository(confirmations)
    evidence = _EvidenceService()
    adapter = _Adapter(
        SimpleNamespace(
            code=WmsDispatchCode.DETERMINATE,
            normalized_response={
                "operation_id": OPERATION_ID,
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
            },
            response_result="ACCEPT",
            retry_after_ms=None,
        )
    )
    queue = _TaskQueue()
    execution_repository = _ExecutionRepository()
    service = WmsConfirmationService(
        repository=repository,
        execution_repository=execution_repository,
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=adapter,
        evidence_service=evidence,  # type: ignore[arg-type]
        task_queue_gateway=queue,  # type: ignore[arg-type]
    )

    processed = await service.dispatch_batch(limit=101, now=now)

    assert processed == 100
    assert len(adapter.calls) == len(evidence.calls) == 100
    assert all(call["apply_status"] is InboundEvidenceApplyStatus.APPLIED for call in evidence.calls)
    assert all(call["workline_id"] == 11 for call in evidence.calls)
    assert all(call["contract_key"] == ADMISSION_OPERATION for call in evidence.calls)
    assert all(
        read_db is evidence_db
        for (read_db, _), evidence_db in zip(execution_repository.calls, evidence.dbs, strict=True)
    )
    assert all(confirmation.status == WmsConfirmationStatus.COMPLETED for confirmation in confirmations[:100])
    assert confirmations[100].status == WmsConfirmationStatus.PENDING
    assert queue.execution_wakes == 100
    assert queue.wms_wakes == 0


@pytest.mark.asyncio
async def test_confirmation_wait_renews_dispatch_window_for_max_delay_and_repeated_wait() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmation = _confirmation(1, now)
    confirmation.deadline_at = now + timedelta(seconds=30)
    confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository([confirmation])
    service = WmsConfirmationService(
        repository=repository,
        execution_repository=_ExecutionRepository(),
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_Adapter(
            SimpleNamespace(
                code=WmsDispatchCode.DETERMINATE,
                normalized_response={
                    "operation_id": confirmation.operation_id,
                    "code": "DECIDED",
                    "timestamp": 2,
                    "data": {"result": "WAIT", "reason_code": "CELL_PENDING", "retry_after_ms": 60_000},
                },
                response_result="WAIT",
                retry_after_ms=60_000,
            )
        ),
        evidence_service=_EvidenceService(),  # type: ignore[arg-type]
        follow_up_planner=_FollowUpPlanner((OTHER_OPERATION_ID, THIRD_OPERATION_ID)),
    )

    assert await service.dispatch_batch(now=now) == 1

    assert confirmation.status == WmsConfirmationStatus.COMPLETED
    assert len(repository.confirmations) == 2
    follow_up = repository.confirmations[1]
    assert follow_up.status == WmsConfirmationStatus.PENDING
    assert follow_up.operation == confirmation.operation
    assert follow_up.operation_id == OTHER_OPERATION_ID
    assert follow_up.request_payload["operation_id"] == OTHER_OPERATION_ID
    assert follow_up.request_payload["timestamp"] == int(now.timestamp() * 1000)
    assert follow_up.next_attempt_at == now + timedelta(seconds=60)
    assert follow_up.deadline_at == now + timedelta(seconds=90)

    assert await service.dispatch_batch(now=now + timedelta(seconds=60)) == 1

    assert follow_up.status == WmsConfirmationStatus.COMPLETED
    assert len(repository.confirmations) == 3
    repeated_follow_up = repository.confirmations[2]
    assert repeated_follow_up.status == WmsConfirmationStatus.PENDING
    assert repeated_follow_up.operation_id == THIRD_OPERATION_ID
    assert repeated_follow_up.next_attempt_at == now + timedelta(seconds=120)
    assert repeated_follow_up.deadline_at == now + timedelta(seconds=150)


@pytest.mark.asyncio
async def test_confirmation_dispatch_rechecks_deadline_and_delivery_unknown_reuses_identity() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    expired = _confirmation(1, now)
    expired.deadline_at = now
    retryable = _confirmation(2, now)
    retryable.request_digest = _digest(retryable.request_payload)
    repository = _ConfirmationRepository([expired, retryable])
    adapter = _Adapter(
        SimpleNamespace(
            code=WmsDispatchCode.DELIVERY_UNKNOWN,
            normalized_response=None,
            response_result=None,
            retry_after_ms=None,
        )
    )
    service = WmsConfirmationService(
        repository=repository,
        execution_repository=_ExecutionRepository(),
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=adapter,
        evidence_service=_EvidenceService(),  # type: ignore[arg-type]
    )

    processed = await service.dispatch_batch(limit=2, now=now)

    assert processed == 2
    assert expired.status == WmsConfirmationStatus.RECONCILING
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["operation_id"] == retryable.operation_id
    assert retryable.status == WmsConfirmationStatus.PENDING
    assert retryable.retry_eligible is True
    assert retryable.next_attempt_at == now + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_confirmation_persists_received_json_object_before_marking_reconciling() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmation = _confirmation(1, now)
    confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository([confirmation])
    evidence = _EvidenceService()
    response_body = {
        "operation_id": OTHER_OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    service = WmsConfirmationService(
        repository=repository,
        execution_repository=_ExecutionRepository(),
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_Adapter(
            SimpleNamespace(
                code=WmsDispatchCode.RECONCILING,
                normalized_response=response_body,
                response_result=None,
                retry_after_ms=None,
            )
        ),
        evidence_service=evidence,  # type: ignore[arg-type]
    )

    assert await service.dispatch_batch(now=now) == 1
    assert confirmation.status == WmsConfirmationStatus.RECONCILING
    assert [call["normalized_payload"] for call in evidence.calls] == [response_body]
    assert [call["workline_id"] for call in evidence.calls] == [11]


@pytest.mark.asyncio
async def test_wms_result_identity_conflict_keeps_execution_epoch_and_fails_closed() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmation = _confirmation(1, now)
    confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository([confirmation])
    evidence = _ConflictEvidenceService()
    execution_repository = _ExecutionRepository()
    service = WmsConfirmationService(
        repository=repository,
        execution_repository=execution_repository,
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_Adapter(
            SimpleNamespace(
                code=WmsDispatchCode.DETERMINATE,
                normalized_response={
                    "operation_id": confirmation.operation_id,
                    "code": "DECIDED",
                    "timestamp": 2,
                    "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
                },
                response_result="ACCEPT",
                retry_after_ms=None,
            )
        ),
        evidence_service=evidence,  # type: ignore[arg-type]
    )

    assert await service.dispatch_batch(now=now) == 1
    assert confirmation.status == WmsConfirmationStatus.RECONCILING
    assert confirmation.response_evidence_id is None
    assert evidence.calls[0]["workline_id"] == 11
    assert execution_repository.calls[0][1] == confirmation.material_execution_id


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", [None, SimpleNamespace(id=21, workline_id=None)])
async def test_wms_result_fails_closed_when_execution_epoch_cannot_be_resolved(execution: object | None) -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmation = _confirmation(1, now)
    confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository([confirmation])
    evidence = _EvidenceService()
    service = WmsConfirmationService(
        repository=repository,
        execution_repository=_ExecutionRepository(execution),
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_Adapter(
            SimpleNamespace(
                code=WmsDispatchCode.RECONCILING,
                normalized_response={
                    "operation_id": confirmation.operation_id,
                    "code": "DECIDED",
                    "timestamp": 2,
                    "data": {"result": "ACCEPT"},
                },
                response_result=None,
                retry_after_ms=None,
            )
        ),
        evidence_service=evidence,  # type: ignore[arg-type]
    )

    with pytest.raises((LookupError, ValueError), match=r"MaterialExecution|workline_id"):
        await service.dispatch_batch(now=now)
    assert evidence.calls == []


@pytest.mark.asyncio
async def test_inflight_identity_conflict_fences_late_response_in_fast_dispatch() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmation = _confirmation(1, now)
    confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository([confirmation])
    evidence = _EvidenceService()
    service = WmsConfirmationService(
        repository=repository,
        execution_repository=_ExecutionRepository(),
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_ConflictDuringDispatchAdapter(
            WmsConfirmationService(repository=repository),
            confirmation,
            now,
        ),
        evidence_service=evidence,  # type: ignore[arg-type]
    )

    assert await service.dispatch_batch(now=now) == 1
    assert confirmation.status == WmsConfirmationStatus.RECONCILING
    assert confirmation.response_evidence_id is None
    assert confirmation.claim_token is None
    assert confirmation.claimed_at is None
    assert confirmation.claim_expires_at is None
    assert evidence.calls == []

    confirmation.claim_token = "stale-late-response"
    assert await repository.get_claimed_for_update(object(), confirmation.id, "stale-late-response") is None
