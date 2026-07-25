"""WMS EFFECT status claim 的显式 capability 身份合同。"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from src.app.runtime.orchestration.repositories.wms_effect_status_repository import WmsEffectStatusRepository
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.wms_integration.ports.effect_status import WMS_EFFECT_OPERATION_IDENTITIES


@pytest.mark.asyncio
async def test_claim_uses_paired_wms_operation_identity_and_never_filters_corrupt_binding() -> None:
    intent = SimpleNamespace(
        id=1,
        capability_key="wms.fulfillment.notify_pkg_binding",
        capability_contract_version="v1",
        operation_identity="WMS:PKG-001:PALLET-001",
        status_binding_snapshot_json=None,
        status_binding_snapshot_hash=None,
        status_check_started_at=None,
        status_check_count=0,
        status_check_lease_token=None,
        status_check_lease_until=None,
    )
    outbox = SimpleNamespace(operation_identity="wms.fulfillment.notify_pkg_binding@v1")

    class Result:
        @staticmethod
        def all() -> list[tuple[Any, Any]]:
            return [(intent, outbox)]

    class Db:
        def __init__(self) -> None:
            self.statement: Any | None = None
            self.flushes = 0

        async def execute(self, statement: Any) -> Result:
            self.statement = statement
            return Result()

        async def flush(self) -> None:
            self.flushes += 1

    db = Db()
    claims = await WmsEffectStatusRepository().claim_due_batch(
        db,
        now=datetime(2026, 7, 24, 12, 0),
        lease_seconds=10,
        limit=10,
    )

    compiled = str(
        db.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert len(claims) == 1
    assert "status_binding_snapshot_hash IS NOT NULL" not in compiled
    assert "runtime_intent_logs.capability_key = 'wms.fulfillment.notify_pkg_binding'" in compiled
    assert "runtime_intent_logs.capability_contract_version = 'v1'" in compiled
    assert "system_outbox.operation_identity = 'wms.fulfillment.notify_pkg_binding@v1'" in compiled
    assert db.flushes == 1


class _HintResult:
    def __init__(self, row: tuple[Any, Any] | None) -> None:
        self.row = row

    def one_or_none(self) -> tuple[Any, Any] | None:
        return self.row


class _HintDb:
    def __init__(self, row: tuple[Any, Any] | None) -> None:
        self.row = row
        self.flushes = 0

    async def execute(self, _statement: Any) -> _HintResult:
        return _HintResult(self.row)

    async def flush(self) -> None:
        self.flushes += 1


def _hint_row(
    *,
    status: RuntimeIntentStatus = RuntimeIntentStatus.ACCEPTED,
    contract_operation_identity: str = "wms.fulfillment.notify_pkg_binding@v1",
    business_operation_identity: str = "BUSINESS:PKG-001:PALLET-001",
    capability_key: str | None = None,
    capability_contract_version: str | None = None,
    outbox_operation_identity: str | None = None,
    idempotency_key: str = "idem-001",
    status_check_after: datetime | None = datetime(2026, 7, 24, 12, 5),
) -> tuple[Any, Any]:
    expected_capability_key, expected_contract_version = contract_operation_identity.rsplit("@", maxsplit=1)
    intent = SimpleNamespace(
        operation_identity=business_operation_identity,
        capability_key=capability_key or expected_capability_key,
        capability_contract_version=capability_contract_version or expected_contract_version,
        idempotency_key=idempotency_key,
        effect_status=status,
        status_check_after=status_check_after,
    )
    outbox = SimpleNamespace(
        operation_identity=outbox_operation_identity or contract_operation_identity,
        idempotency_key=idempotency_key,
        status="SENT",
        attempt_count=3,
    )
    return intent, outbox


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_identity", sorted(WMS_EFFECT_OPERATION_IDENTITIES))
async def test_hint_lock_advances_only_intent_schedule_without_transport_write(operation_identity: str) -> None:
    now = datetime(2026, 7, 24, 12, 0)
    intent, outbox = _hint_row(
        contract_operation_identity=operation_identity,
        status_check_after=now + timedelta(minutes=5),
    )
    db = _HintDb((intent, outbox))
    transport_before = (outbox.status, outbox.attempt_count)

    outcome = await WmsEffectStatusRepository().advance_status_check_after_from_hint(
        db,
        operation_identity=outbox.operation_identity,
        idempotency_key=outbox.idempotency_key,
        dispatch_key="dispatch-001",
        now=now,
    )

    assert outcome == "SCHEDULED"
    assert intent.operation_identity == "BUSINESS:PKG-001:PALLET-001"
    assert intent.operation_identity != operation_identity
    assert intent.status_check_after == now
    assert db.flushes == 1
    assert (outbox.status, outbox.attempt_count) == transport_before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (None, "NOT_FOUND"),
        (_hint_row(status=RuntimeIntentStatus.COMPLETED), "TERMINAL"),
        (_hint_row(status_check_after=None), "ALREADY_DUE"),
        (_hint_row(capability_key="wms.fulfillment.full_box_exchange"), "CORRELATION_MISMATCH"),
        (_hint_row(capability_contract_version="v2"), "CORRELATION_MISMATCH"),
        (
            _hint_row(outbox_operation_identity="wms.fulfillment.full_box_exchange@v1"),
            "CORRELATION_MISMATCH",
        ),
        (_hint_row(idempotency_key="other-idem"), "CORRELATION_MISMATCH"),
    ],
)
async def test_hint_lock_names_missing_mismatch_and_safe_ignore_outcomes(
    row: tuple[Any, Any] | None,
    expected: str,
) -> None:
    db = _HintDb(row)

    outcome = await WmsEffectStatusRepository().advance_status_check_after_from_hint(
        db,
        operation_identity="wms.fulfillment.notify_pkg_binding@v1",
        idempotency_key="idem-001",
        dispatch_key="dispatch-001",
        now=datetime(2026, 7, 24, 12, 0),
    )

    assert outcome == expected
    assert db.flushes == 0
