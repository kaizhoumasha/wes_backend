"""Phase 3 WMS fulfillment lifecycle service tests."""

from __future__ import annotations

from src.utils.timezone import timezone


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
