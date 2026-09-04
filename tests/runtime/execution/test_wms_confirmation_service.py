"""WmsConfirmation 以 operation + operation_id 拥有可靠义务身份。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.app.execution.models.wms_confirmation import WmsConfirmation, WmsConfirmationStatus
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationIdentityConflictError,
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationResponseConflictError,
    WmsConfirmationResponseConflictResult,
    WmsConfirmationService,
)


class FakeWmsConfirmationRepository:
    def __init__(self) -> None:
        self.confirmations: dict[tuple[str, str], WmsConfirmation] = {}

    async def lock_identity(self, _db: object, operation: str, operation_id: str) -> None:
        return None

    async def get_by_identity_for_update(
        self,
        _db: object,
        operation: str,
        operation_id: str,
    ) -> WmsConfirmation | None:
        return self.confirmations.get((operation, operation_id))

    async def add(self, _db: object, confirmation: WmsConfirmation) -> WmsConfirmation:
        confirmation.id = len(self.confirmations) + 1
        self.confirmations[(confirmation.operation, confirmation.operation_id)] = confirmation
        return confirmation

    async def flush(self, _db: object) -> None:
        return None


async def _create(
    service: WmsConfirmationService,
    *,
    request_payload: dict[str, object] | None = None,
) -> WmsConfirmation:
    result = await service.create_or_get(
        object(),
        operation="inbound.material.admission_decide@v1",
        operation_id="OP-001",
        material_execution_id=21,
        request_payload=request_payload or {"data": {"material_execution_id": "EXEC-001"}},
        deadline_at=datetime(2026, 8, 16, 0, 5),
        created_at=datetime(2026, 8, 16),
    )
    return result.confirmation


@pytest.mark.asyncio
async def test_same_operation_identity_and_request_is_idempotent_but_payload_cannot_change() -> None:
    repository = FakeWmsConfirmationRepository()
    service = WmsConfirmationService(repository=repository)

    first = await service.create_or_get(
        object(),
        operation="inbound.material.admission_decide@v1",
        operation_id="OP-001",
        material_execution_id=21,
        request_payload={"operation_id": "OP-001", "data": {"trace": "TRACE-001"}},
        deadline_at=datetime(2026, 8, 16, 0, 5),
        created_at=datetime(2026, 8, 16),
    )
    duplicate = await service.create_or_get(
        object(),
        operation="inbound.material.admission_decide@v1",
        operation_id="OP-001",
        material_execution_id=21,
        request_payload={"data": {"trace": "TRACE-001"}, "operation_id": "OP-001"},
        deadline_at=datetime(2026, 8, 16, 0, 5),
        created_at=datetime(2026, 8, 16),
    )

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.confirmation is first.confirmation
    first.confirmation.status = WmsConfirmationStatus.DISPATCHING
    first.confirmation.claim_token = "claim-in-flight"
    first.confirmation.claimed_at = datetime(2026, 8, 16, 0, 1)
    first.confirmation.claim_expires_at = datetime(2026, 8, 16, 0, 2)
    first.confirmation.retry_eligible = True
    first.confirmation.next_attempt_at = datetime(2026, 8, 16, 0, 1) + timedelta(seconds=1)
    conflict_result = await service.create_or_get(
        object(),
        operation="inbound.material.admission_decide@v1",
        operation_id="OP-001",
        material_execution_id=21,
        request_payload={"operation_id": "OP-001", "data": {"trace": "OTHER"}},
        deadline_at=datetime(2026, 8, 16, 0, 5),
        created_at=datetime(2026, 8, 16),
    )
    assert isinstance(conflict_result, WmsConfirmationIdentityConflictResult)
    assert isinstance(conflict_result.to_exception(), WmsConfirmationIdentityConflictError)
    assert first.confirmation.status == WmsConfirmationStatus.RECONCILING
    assert first.confirmation.claim_token is None
    assert first.confirmation.claimed_at is None
    assert first.confirmation.claim_expires_at is None
    assert first.confirmation.retry_eligible is False
    assert first.confirmation.next_attempt_at is None


@pytest.mark.asyncio
async def test_delivery_unknown_reuses_identity_and_only_safe_retry_returns_pending() -> None:
    service = WmsConfirmationService(repository=FakeWmsConfirmationRepository())
    confirmation = await _create(service)
    operation_id = confirmation.operation_id
    request_digest = confirmation.request_digest

    await service.mark_dispatching(
        object(),
        confirmation,
        claim_token="claim-1",
        claimed_at=datetime(2026, 8, 16, 0, 1),
    )
    await service.record_delivery_unknown(
        object(),
        confirmation,
        retry_eligible=True,
        next_attempt_at=datetime(2026, 8, 16, 0, 2),
        changed_at=datetime(2026, 8, 16, 0, 1, 30),
    )

    assert confirmation.status == WmsConfirmationStatus.PENDING
    assert confirmation.operation_id == operation_id
    assert confirmation.request_digest == request_digest
    assert confirmation.attempt_count == 1
    assert confirmation.next_attempt_at == datetime(2026, 8, 16, 0, 2)

    await service.mark_dispatching(
        object(),
        confirmation,
        claim_token="claim-2",
        claimed_at=datetime(2026, 8, 16, 0, 2),
    )
    await service.record_delivery_unknown(
        object(),
        confirmation,
        retry_eligible=False,
        next_attempt_at=None,
        changed_at=datetime(2026, 8, 16, 0, 2, 30),
    )
    assert confirmation.status == WmsConfirmationStatus.RECONCILING
    assert confirmation.operation_id == operation_id


@pytest.mark.asyncio
async def test_wait_is_a_completed_response_and_conflicting_response_reconciles() -> None:
    service = WmsConfirmationService(repository=FakeWmsConfirmationRepository())
    confirmation = await _create(service)

    completed = await service.complete(
        object(),
        confirmation,
        response_evidence_id=301,
        response_result="WAIT",
        completed_at=datetime(2026, 8, 16, 0, 1),
    )

    assert completed.status == WmsConfirmationStatus.COMPLETED
    assert completed.response_result == "WAIT"
    assert completed.response_evidence_id == 301
    duplicate = await service.complete(
        object(),
        confirmation,
        response_evidence_id=301,
        response_result="WAIT",
        completed_at=datetime(2026, 8, 16, 0, 2),
    )
    assert duplicate is confirmation
    conflict_result = await service.complete(
        object(),
        confirmation,
        response_evidence_id=302,
        response_result="ACCEPT",
        completed_at=datetime(2026, 8, 16, 0, 3),
    )
    assert isinstance(conflict_result, WmsConfirmationResponseConflictResult)
    assert isinstance(conflict_result.to_exception(), WmsConfirmationResponseConflictError)
    assert confirmation.status == WmsConfirmationStatus.RECONCILING


def test_status_is_the_minimal_approved_closed_set() -> None:
    assert {status.value for status in WmsConfirmationStatus} == {
        "PENDING",
        "DISPATCHING",
        "COMPLETED",
        "RECONCILING",
    }


@pytest.mark.asyncio
async def test_lifecycle_accepts_exactly_one_picking_task_owner() -> None:
    service = WmsConfirmationService(repository=FakeWmsConfirmationRepository())

    result = await service.create_or_get(
        object(),
        operation="outbound.picking_task.prepare@v1",
        operation_id="019f3400-0e17-7d2a-b944-3cf7953804da",
        picking_task_id=31,
        request_payload={"data": {"task_id": "PICK-1", "workline_code": "LINE-1"}},
        deadline_at=datetime(2026, 9, 4, 0, 0, 30),
        created_at=datetime(2026, 9, 4),
    )

    assert result.confirmation.picking_task_id == 31
    assert result.confirmation.material_execution_id is None
    assert result.confirmation.bin_execution_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owners",
    [
        {},
        {"material_execution_id": 21, "picking_task_id": 31},
    ],
)
async def test_lifecycle_rejects_missing_or_ambiguous_owner(owners: dict[str, int]) -> None:
    service = WmsConfirmationService(repository=FakeWmsConfirmationRepository())

    with pytest.raises(ValueError, match="恰好一个"):
        await service.create_or_get(
            object(),
            operation="outbound.picking_task.prepare@v1",
            operation_id="019f3400-0e17-7d2a-b944-3cf7953804da",
            request_payload={"data": {}},
            deadline_at=datetime(2026, 9, 4, 0, 0, 30),
            created_at=datetime(2026, 9, 4),
            **owners,
        )
