from __future__ import annotations

import hashlib
import json
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
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
        self.transport_debug_wakes = 0
        self.error = error

    def enqueue_execution_facts(self) -> None:
        self.execution_wakes += 1
        if self.error is not None:
            raise self.error

    def enqueue_transport_debug(self) -> None:
        self.transport_debug_wakes += 1

    def enqueue_wms_confirmations(self) -> None:
        self.wms_wakes += 1


class _Adapter:
    def __init__(self, result) -> None:  # type: ignore[no-untyped-def]
        self.result = result
        self.calls = []

    async def dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self.result


async def test_diagnostics_observation_finishes_before_original_result_transaction() -> None:
    from src.app.wms_adapter.dispatch import WmsDispatchResult
    from src.app.wms_diagnostics.observation import WmsCallObservation

    now = datetime(2026, 9, 7, tzinfo=UTC)
    confirmation = _picking_confirmation(1, now)
    repository = _ConfirmationRepository([confirmation])
    adapter = _Adapter(WmsDispatchResult(WmsDispatchCode.NOT_SENT))
    observation = WmsCallObservation(direction="WES_TO_WMS")
    diagnostics = SimpleNamespace(start=AsyncMock(return_value=observation), finish=AsyncMock())
    states = []

    async def finish(observed):
        assert observed is observation
        states.append(confirmation.status)

    diagnostics.finish.side_effect = finish
    service = WmsConfirmationService(
        repository=repository,
        session_factory=_Sessions(),
        adapter=adapter,
        picking_task_owner=_PickingTaskOwner(),
        diagnostics=diagnostics,
    )
    assert await service.dispatch_batch(now=now) == 1
    assert adapter.calls[0]["observation"] is observation
    assert states == [WmsConfirmationStatus.DISPATCHING]
    assert confirmation.status == WmsConfirmationStatus.PENDING
    assert confirmation.next_attempt_at == now + timedelta(seconds=1)


class _PickingTaskOwner:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.calls: list[tuple[object, int, str]] = []

    async def validate_dispatch_owner(self, db: object, *, picking_task_id: int, operation: str) -> bool:
        self.calls.append((db, picking_task_id, operation))
        return self.valid

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
@pytest.mark.parametrize(
    "operation", ["outbound.return_rack.arrival_report@v1", "outbound.material.movement_report@v1"]
)
@pytest.mark.parametrize("state", [None, "QUEUED", "EXECUTION_COMPLETED"])
async def test_frozen_picking_fact_retries_original_request_after_owner_changes(operation, state) -> None:
    from src.app.wms_integration.outbound_picking.services import PickingTaskConfirmationOwnerService

    now = datetime(2026, 9, 7, tzinfo=UTC)
    confirmation = _picking_confirmation(1, now)
    confirmation.operation = operation
    confirmation.request_payload["operation"] = operation
    confirmation.request_payload["data"] = (
        {
            "task_id": "PICK-1",
            "transport_task_id": "transport-1",
            "outcome_revision": 1,
            "rack_id": "RETURN-1",
            "final_position": {"type": "RACK_POSITION", "location_code": "WORK-1"},
            "arrival_face": "A",
        }
        if operation == "outbound.return_rack.arrival_report@v1"
        else {
            "task_id": "PICK-1",
            "source_locator": {"type": "RACK_SLOT", "rack_id": "SOURCE-1", "rack_face": "A", "slot_id": "1"},
            "PkgID": "PKG-1",
            "to_locator": {"type": "NG_ZONE", "zone_code": "NG-1"},
            "occurred_at": 1,
        }
    )
    confirmation.request_digest = _digest(confirmation.request_payload)
    frozen = deepcopy(confirmation.request_payload)
    task_repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=SimpleNamespace(status="EXECUTING", workline_id=1))
    )
    repository = _ConfirmationRepository([confirmation])
    evidence = _EvidenceService()
    queue = _TaskQueue()
    adapter = _Adapter(SimpleNamespace(code=WmsDispatchCode.RETRY, normalized_response=None, retry_after_ms=1000))
    kwargs = {
        "repository": repository,
        "session_factory": _Sessions(),
        "adapter": adapter,
        "evidence_service": evidence,
        "picking_task_owner": PickingTaskConfirmationOwnerService(task_repository),
        "task_queue_gateway": queue,
    }
    assert await WmsConfirmationService(**kwargs).dispatch_batch(now=now) == 1
    assert confirmation.status == WmsConfirmationStatus.PENDING
    task = None if state is None else SimpleNamespace(status=state, workline_id=1 if state != "QUEUED" else None)
    task_repository.get_by_id_for_update.return_value = task
    response = {"operation_id": confirmation.operation_id, "code": "RECORDED", "timestamp": 2, "data": {}}
    adapter.result = SimpleNamespace(
        code=WmsDispatchCode.DETERMINATE, normalized_response=response, response_result="RECORDED", retry_after_ms=None
    )

    restarted = WmsConfirmationService(**kwargs)
    assert await restarted.dispatch_batch(now=now + timedelta(seconds=1)) == 1

    assert len(adapter.calls) == 2
    assert all(call["request_payload"] == frozen for call in adapter.calls)
    assert all(call["operation_id"] == frozen["operation_id"] for call in adapter.calls)
    assert all(call["request_digest"] == _digest(frozen) for call in adapter.calls)
    assert confirmation.request_payload == frozen
    assert evidence.calls[0]["normalized_payload"] == response
    valid_response_owner = state == "EXECUTION_COMPLETED"
    assert evidence.calls[0]["apply_status"] == (
        InboundEvidenceApplyStatus.APPLIED if valid_response_owner else InboundEvidenceApplyStatus.RECONCILING
    )
    assert confirmation.status == (
        WmsConfirmationStatus.COMPLETED if valid_response_owner else WmsConfirmationStatus.RECONCILING
    )
    assert await restarted.dispatch_batch(now=now + timedelta(seconds=2)) == 0
    assert len(adapter.calls) == 2 and len(evidence.calls) == 1
    assert queue.execution_wakes == queue.wms_wakes == 0
    if task is not None:
        assert task.status == state


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
        validate_dispatch_owner=AsyncMock(return_value=True),
        validate_response_owner=AsyncMock(return_value=False),
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
@pytest.mark.parametrize("owner_available", [False, True])
@pytest.mark.parametrize(
    "operation", ["outbound.picking_task.prepare@v1", "outbound.rack.departure_decide@v1", "unknown@v1"]
)
async def test_picking_decision_is_not_sent_without_valid_business_owner(owner_available, operation) -> None:
    from src.app.wms_integration.outbound_picking.services import PickingTaskConfirmationOwnerService

    now = datetime(2026, 9, 4, tzinfo=UTC)
    confirmation = _picking_confirmation(1, now)
    confirmation.operation = operation
    confirmation.request_payload["operation"] = operation
    confirmation.request_digest = _digest(confirmation.request_payload)
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
        picking_task_owner=(
            PickingTaskConfirmationOwnerService(SimpleNamespace(get_by_id_for_update=AsyncMock(return_value=None)))
            if owner_available
            else None
        ),
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
async def test_confirmation_dispatch_keeps_safe_same_identity_retry_after_internal_window() -> None:
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
    assert [call["operation_id"] for call in adapter.calls] == [expired.operation_id, retryable.operation_id]
    for confirmation in (expired, retryable):
        assert confirmation.status == WmsConfirmationStatus.PENDING
        assert confirmation.retry_eligible is True
        assert confirmation.next_attempt_at == now + timedelta(seconds=1)
        assert confirmation.deadline_at == (now if confirmation is expired else now + timedelta(minutes=5))

    assert await service.dispatch_batch(limit=2, now=now + timedelta(seconds=1)) == 2
    assert [call["operation_id"] for call in adapter.calls] == [
        expired.operation_id,
        retryable.operation_id,
        expired.operation_id,
        retryable.operation_id,
    ]
    assert expired.next_attempt_at == retryable.next_attempt_at == now + timedelta(seconds=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [WmsDispatchCode.RETRY, WmsDispatchCode.DELIVERY_UNKNOWN])
async def test_return_batch_network_retry_preserves_identity_then_no_batch_closes(failure) -> None:
    from tests.contracts.wms_adapter.outbound_picking.test_return_batch import request

    now = datetime(2026, 9, 12, tzinfo=UTC)
    payload = request()
    confirmation = WmsConfirmation(
        id=1,
        operation=payload["operation"],
        operation_id=payload["operation_id"],
        workline_id=11,
        request_payload=payload,
        request_digest=_digest(payload),
        deadline_at=now + timedelta(minutes=5),
        created_at=now,
        updated_at=now,
    )
    frozen = deepcopy(payload)
    adapter = _Adapter(
        SimpleNamespace(
            code=failure,
            normalized_response=None,
            response_result=None,
            retry_after_ms=1000,
        )
    )
    owner = SimpleNamespace(validate_owner=AsyncMock(return_value=True))
    evidence = _EvidenceService()
    kwargs = {
        "repository": _ConfirmationRepository([confirmation]),
        "session_factory": _Sessions(),
        "adapter": adapter,
        "evidence_service": evidence,
        "workline_owner": owner,
        "task_queue_gateway": _TaskQueue(),
    }
    assert await WmsConfirmationService(**kwargs).dispatch_batch(now=now) == 1
    assert confirmation.status == WmsConfirmationStatus.PENDING
    response = {
        "operation_id": payload["operation_id"],
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "NO_BATCH", "retry_after_ms": 1000},
    }
    adapter.result = SimpleNamespace(
        code=WmsDispatchCode.DETERMINATE,
        normalized_response=response,
        response_result="NO_BATCH",
        retry_after_ms=None,
    )
    restarted = WmsConfirmationService(**kwargs)
    assert await restarted.dispatch_batch(now=now + timedelta(seconds=1)) == 1
    assert all(call["operation_id"] == frozen["operation_id"] for call in adapter.calls)
    assert all(call["request_payload"] == frozen for call in adapter.calls)
    assert all(call["request_digest"] == _digest(frozen) for call in adapter.calls)
    assert confirmation.status == WmsConfirmationStatus.COMPLETED
    assert confirmation.request_payload == frozen
    assert evidence.calls[0]["normalized_payload"] == response
    assert await restarted.dispatch_batch(now=now + timedelta(seconds=2)) == 0
    assert len(adapter.calls) == 2


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
async def test_wms_result_is_retained_without_business_wake_when_execution_cannot_be_resolved(
    execution: object | None,
) -> None:
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

    await service.dispatch_batch(now=now)
    assert confirmation.status == WmsConfirmationStatus.RECONCILING
    assert len(evidence.calls) == 1
    assert evidence.calls[0]["workline_id"] is None
    assert evidence.calls[0]["material_execution_id"] is None
    assert evidence.calls[0]["apply_status"] == InboundEvidenceApplyStatus.RECONCILING
    assert evidence.calls[0]["operation_id"] == confirmation.operation_id
    assert await service.dispatch_batch(now=now) == 0


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


@pytest.mark.parametrize("rollback", [False, True])
async def test_wms_writeback_wakes_transport_debug_only_after_commit(rollback):
    import asyncio
    from unittest.mock import Mock

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.core.transaction_wakeup import _pending

    gateway = Mock()
    service = WmsConfirmationService(task_queue_gateway=gateway)
    try:
        async with service._execution_wake_transaction(async_sessionmaker()):
            gateway.enqueue_transport_debug.assert_not_called()
            if rollback:
                raise ValueError("rollback")
    except ValueError:
        assert rollback
    if _pending:
        await asyncio.gather(*tuple(_pending))
    assert gateway.enqueue_transport_debug.call_count == (0 if rollback else 1)
