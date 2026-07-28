"""WMS fulfillment lifecycle service tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.utils.timezone import timezone
from tests.support.runtime_binding import binding_pin_fields

NOW_MS = 1_700_000_000_000


async def _seed_execution_correlation(db_session, *, correlation_id: str = "corr-fulfillment-001"):
    """建立 ExecutionSession + ExecutionCorrelation，满足 IdempotencyKey FK 前置。"""

    session = ExecutionSession(
        workline_id=1,
        plugin_key="test-plugin",
        manifest_version="v1",
        **binding_pin_fields(),
        state="RUNNING",
    )
    db_session.add(session)
    await db_session.flush()
    correlation = ExecutionCorrelation(
        correlation_id=correlation_id,
        execution_session_id=session.id,
        trace_id=f"trace-{correlation_id}",
    )
    db_session.add(correlation)
    await db_session.flush()
    return correlation


def test_fulfillment_lifecycle_opens_as_blocked_when_breaker_is_open() -> None:
    """CB open 时履约请求进入 BLOCKED_BY_CB, 不允许直接 dispatch。"""

    from src.app.wms_integration.services.fulfillment_lifecycle import (
        WmsFulfillmentLifecycleService,
    )
    from src.app.wms_integration.state_machine import FulfillmentState

    service = WmsFulfillmentLifecycleService()

    record = service.open_request(
        request_id="fulfillment-001",
        fulfillment_kind="FULL_BOX_EXCHANGE",
        now=timezone.now_for_db(),
        circuit_breaker_open=True,
    )

    assert record.state == FulfillmentState.BLOCKED_BY_CB
    assert record.request_id == "fulfillment-001"
    assert record.dispatch_allowed is False


def test_fulfillment_lifecycle_tracks_business_reject_separately_from_failure() -> None:
    """provider 业务拒绝必须转 REJECTED, 不混入 FAILED。"""

    from src.app.wms_integration.services.fulfillment_lifecycle import (
        WmsFulfillmentLifecycleService,
    )
    from src.app.wms_integration.state_machine import FulfillmentEvent, FulfillmentState

    service = WmsFulfillmentLifecycleService()
    record = service.open_request(
        request_id="fulfillment-002",
        fulfillment_kind="MOVE_BIN_TO_CONVEYOR_ENTRY",
        now=timezone.now_for_db(),
        circuit_breaker_open=False,
    )

    sent = service.apply_event(record, FulfillmentEvent.DISPATCH_SENT, now=timezone.now_for_db())
    rejected = service.apply_event(sent, FulfillmentEvent.PROVIDER_REJECTED, now=timezone.now_for_db())

    assert rejected.state == FulfillmentState.REJECTED
    assert rejected.last_reason == "PROVIDER_REJECTED"
    assert rejected.dispatch_allowed is False


def test_fulfillment_lifecycle_marks_callback_events_as_inbox_required() -> None:
    """callback 推进必须要求 RuntimeInbox evidence, 越级 callback 进入 RECONCILING。"""

    from src.app.wms_integration.services.fulfillment_lifecycle import (
        WmsFulfillmentLifecycleService,
    )
    from src.app.wms_integration.state_machine import FulfillmentEvent, FulfillmentState

    service = WmsFulfillmentLifecycleService()
    record = service.open_request(
        request_id="fulfillment-003",
        fulfillment_kind="RACK_SUPPLY",
        now=timezone.now_for_db(),
        circuit_breaker_open=False,
    )

    result = service.apply_event(record, FulfillmentEvent.CALLBACK_SUCCEEDED, now=timezone.now_for_db())

    assert result.state == FulfillmentState.RECONCILING
    assert result.runtime_inbox_required is True
    assert result.last_reason == "UNSUPPORTED_TRANSITION"


@pytest.mark.asyncio
async def test_fulfillment_lifecycle_idempotent_open_claims_key_and_replays_same_hash(db_session) -> None:
    """fulfillment 生产入口必须先 claim 幂等键，同 hash 重放返回 MATCH。"""

    from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
    from src.app.wms_integration.services.fulfillment_lifecycle import (
        WmsFulfillmentLifecycleService,
    )
    from src.app.wms_integration.state_machine import FulfillmentState

    correlation = await _seed_execution_correlation(db_session)
    service = WmsFulfillmentLifecycleService()

    first = await service.open_request_idempotent(
        db_session,
        request_id="fulfillment-idem-001",
        fulfillment_kind="FULL_BOX_EXCHANGE",
        now=timezone.now_for_db(),
        circuit_breaker_open=False,
        provider_code="WMS",
        idempotency_key="WES-FULFILLMENT-hash001",
        request_hash="sha256-fulfillment-001",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
        business_owner_key="fulfillment:fulfillment-idem-001",
    )
    second = await service.open_request_idempotent(
        db_session,
        request_id="fulfillment-idem-001",
        fulfillment_kind="FULL_BOX_EXCHANGE",
        now=timezone.now_for_db(),
        circuit_breaker_open=False,
        provider_code="WMS",
        idempotency_key="WES-FULFILLMENT-hash001",
        request_hash="sha256-fulfillment-001",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
        business_owner_key="fulfillment:fulfillment-idem-001",
    )

    assert first.claim_result is ClaimResult.NEW
    assert second.claim_result is ClaimResult.MATCH
    assert first.record.state == FulfillmentState.REQUESTED
    assert second.record.dispatch_allowed is True
    stored = (
        await db_session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.provider_code == "WMS",
                IdempotencyKey.operation_kind == "fulfillment",
                IdempotencyKey.idempotency_key == "WES-FULFILLMENT-hash001",
            )
        )
    ).scalar_one()
    assert stored.request_hash == "sha256-fulfillment-001"
    assert stored.business_owner_key == "fulfillment:fulfillment-idem-001"


@pytest.mark.asyncio
async def test_fulfillment_lifecycle_idempotent_open_rejects_same_key_different_hash(db_session) -> None:
    """fulfillment 同 key 不同 hash 必须 409 并暴露 wms_integration 审计域。"""

    from src.app.runtime.orchestration.services.idempotency_guard import IdempotencyConflict
    from src.app.wms_integration.services.fulfillment_lifecycle import (
        WmsFulfillmentLifecycleService,
    )

    correlation = await _seed_execution_correlation(db_session, correlation_id="corr-fulfillment-conflict")
    service = WmsFulfillmentLifecycleService()

    await service.open_request_idempotent(
        db_session,
        request_id="fulfillment-idem-conflict",
        fulfillment_kind="RACK_SUPPLY",
        now=timezone.now_for_db(),
        circuit_breaker_open=True,
        provider_code="WMS",
        idempotency_key="WES-FULFILLMENT-conflict",
        request_hash="sha256-original",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
        business_owner_key="fulfillment:fulfillment-idem-conflict",
    )

    with pytest.raises(IdempotencyConflict) as exc_info:
        await service.open_request_idempotent(
            db_session,
            request_id="fulfillment-idem-conflict",
            fulfillment_kind="RACK_SUPPLY",
            now=timezone.now_for_db(),
            circuit_breaker_open=True,
            provider_code="WMS",
            idempotency_key="WES-FULFILLMENT-conflict",
            request_hash="sha256-tampered",
            execution_correlation_id=correlation.correlation_id,
            now_ms=NOW_MS,
            business_owner_key="fulfillment:fulfillment-idem-conflict",
        )

    audit_event = exc_info.value.to_audit_event()
    assert audit_event["normalized_operation_kind"] == "fulfillment"
    assert audit_event["domain"] == "wms_integration"
    assert audit_event["status_code"] == 409
