"""WmsConfirmation 以 operation + operation_id 拥有可靠义务身份。"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.app.execution.models.wms_confirmation import WmsConfirmation, WmsConfirmationStatus
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationBarrierBlockedError,
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

    async def list_for_execution_operations_for_update(
        self,
        _db: object,
        *,
        material_execution_id: int,
        operations: tuple[str, ...],
    ) -> list[WmsConfirmation]:
        return sorted(
            (
                item
                for item in self.confirmations.values()
                if item.material_execution_id == material_execution_id and item.operation in operations
            ),
            key=lambda item: operations.index(item.operation),
        )


class FakeExecutionRepository:
    def __init__(self, execution_id: int, *, fifo_head_id: int | None = None) -> None:
        self.execution = SimpleNamespace(id=execution_id, workline_id=7, line_run_epoch_id=11)
        self.fifo_head_id = fifo_head_id if fifo_head_id is not None else execution_id

    async def get_by_id_for_update(self, _db: object, execution_id: int):
        return self.execution if execution_id == self.execution.id else None

    async def get_admission_head_for_update(self, _db: object, *, workline_id: int, line_run_epoch_id: int):
        assert (workline_id, line_run_epoch_id) == (7, 11)
        return SimpleNamespace(id=self.fifo_head_id, execution_code=f"EXEC-{self.fifo_head_id}")


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
async def test_e03_e07_creation_obeys_execution_mutex_fifo_and_predecessor_barrier() -> None:
    repository = FakeWmsConfirmationRepository()
    executions = FakeExecutionRepository(21)
    service = WmsConfirmationService(repository=repository, execution_repository=executions)
    common = {
        "material_execution_id": 21,
        "deadline_at": datetime(2026, 8, 16, 0, 5),
        "created_at": datetime(2026, 8, 16),
    }

    with pytest.raises(WmsConfirmationBarrierBlockedError, match="E03"):
        await service.create_or_get(
            object(),
            operation="wms.fulfillment.notify_pkg_binding@v1",
            operation_id="E07-001",
            request_payload={"operation": "wms.fulfillment.notify_pkg_binding@v1"},
            **common,
        )

    e03 = await service.create_or_get(
        object(),
        operation="wms.inventory.confirm_inbound@v1",
        operation_id="E03-001",
        request_payload={"operation": "wms.inventory.confirm_inbound@v1"},
        **common,
    )
    await service.complete(
        object(),
        e03.confirmation,
        response_evidence_id=301,
        response_result="RECORDED",
        completed_at=datetime(2026, 8, 16, 0, 1),
    )
    e07 = await service.create_or_get(
        object(),
        operation="wms.fulfillment.notify_pkg_binding@v1",
        operation_id="E07-001",
        request_payload={"operation": "wms.fulfillment.notify_pkg_binding@v1"},
        **common,
    )

    assert e07.duplicate is False

    executions.fifo_head_id = 20
    replayed_e03 = await service.create_or_get(
        object(),
        operation="wms.inventory.confirm_inbound@v1",
        operation_id="E03-001",
        request_payload={"operation": "wms.inventory.confirm_inbound@v1"},
        **common,
    )
    assert replayed_e03.duplicate is True
    with pytest.raises(WmsConfirmationBarrierBlockedError, match="FIFO"):
        await service.create_or_get(
            object(),
            operation="wms.inventory.confirm_inbound@v1",
            operation_id="E03-LATE",
            request_payload={"operation": "wms.inventory.confirm_inbound@v1"},
            **common,
        )
