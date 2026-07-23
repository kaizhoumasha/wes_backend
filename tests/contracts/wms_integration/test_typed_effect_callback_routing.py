"""WMS typed EFFECT callback 的生产路由与业务关联合同。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from src.app.runtime.orchestration.services.inbox.wms_typed_effect_callback_router import WmsTypedEffectCallbackRouter
from src.app.sys.models.outbox import SystemOutboxStatus


@pytest.mark.parametrize(
    ("callback_type", "data"),
    [
        (
            "WMS_INBOUND_CONFIRMED",
            {"dispatch_key": "dispatch-1", "inbound_key": "INBOUND-A", "accepted": True},
        ),
        (
            "WMS_PACKAGE_BOUND",
            {
                "dispatch_key": "dispatch-1",
                "package_id": "PKG-A",
                "pallet_id": "PALLET-A",
                "accepted": True,
            },
        ),
        (
            "WMS_FULL_BOX_EXCHANGE_COMPLETED",
            {
                "dispatch_key": "dispatch-1",
                "rack_id": "RACK-A",
                "empty_box_id": "EMPTY-A",
                "full_box_id": "FULL-A",
                "accepted": True,
            },
        ),
    ],
)
def test_production_ingress_accepts_typed_effect_callback_envelope(
    callback_type: str,
    data: dict[str, object],
) -> None:
    from src.app.callback.services.callback_ingress_service import _normalize_external_callback_payload

    normalized = _normalize_external_callback_payload(
        {
            "callback_type": callback_type,
            "source_system": "WMS",
            "source_event_id": f"event:{callback_type}",
            "trace_id": f"trace:{callback_type}",
            "data": data,
        }
    )

    assert normalized["callback_type"] == callback_type


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("callback_type", "operation_identity", "frozen_payload", "callback_payload"),
    [
        (
            "WMS_INBOUND_CONFIRMED",
            "wms.inventory.confirm_inbound@v1",
            {"inbound_key": "INBOUND-A"},
            {"dispatch_key": "dispatch-1", "inbound_key": "INBOUND-B", "accepted": True},
        ),
        (
            "WMS_PACKAGE_BOUND",
            "wms.fulfillment.notify_pkg_binding@v1",
            {"package_id": "PKG-A", "pallet_id": "PALLET-A"},
            {
                "dispatch_key": "dispatch-1",
                "package_id": "PKG-B",
                "pallet_id": "PALLET-A",
                "accepted": True,
            },
        ),
        (
            "WMS_FULL_BOX_EXCHANGE_COMPLETED",
            "wms.fulfillment.full_box_exchange@v1",
            {"rack_id": "RACK-A", "empty_box_id": "EMPTY-A", "full_box_id": "FULL-A"},
            {
                "dispatch_key": "dispatch-1",
                "rack_id": "RACK-B",
                "empty_box_id": "EMPTY-A",
                "full_box_id": "FULL-A",
                "accepted": True,
            },
        ),
    ],
)
async def test_mismatched_business_identity_opens_reconciliation_without_completing_intent(
    callback_type: str,
    operation_identity: str,
    frozen_payload: dict[str, object],
    callback_payload: dict[str, object],
) -> None:
    adapter = SimpleNamespace(record=AsyncMock())
    reconciliation = SimpleNamespace(open=AsyncMock())
    router = WmsTypedEffectCallbackRouter()
    outbox_repository = SimpleNamespace(
        get_by_dispatch_key_for_update=AsyncMock(
            return_value=SimpleNamespace(
                dispatch_key="dispatch-1",
                operation_identity=operation_identity,
                payload_json=frozen_payload,
                status=SystemOutboxStatus.DISPATCHING,
            )
        ),
        finish_sent_external_by_dispatch_key=AsyncMock(),
        isolate_for_reconciliation_by_dispatch_key=AsyncMock(),
    )
    router._outbox_repository = outbox_repository
    router._reconciliation_bridge = reconciliation
    router._callback_adapters = {callback_type: adapter}

    handled = await router.route(
        SimpleNamespace(),
        callback_type=callback_type,
        payload={"callback_type": callback_type, "data": callback_payload},
        occurred_at_ms=1_700_000_000_000,
        source_event_id="wms-event-1",
    )

    assert handled is True
    adapter.record.assert_not_awaited()
    outbox_repository.finish_sent_external_by_dispatch_key.assert_not_awaited()
    outbox_repository.isolate_for_reconciliation_by_dispatch_key.assert_awaited_once_with(
        ANY,
        "dispatch-1",
        reason="WMS_CALLBACK_BUSINESS_IDENTITY_MISMATCH",
    )
    reconciliation.open.assert_awaited_once()
    assert reconciliation.open.await_args.kwargs["reason_code"] == "WMS_CALLBACK_BUSINESS_IDENTITY_MISMATCH"


@pytest.mark.asyncio
async def test_matching_typed_callback_routes_to_operation_adapter() -> None:
    adapter = SimpleNamespace(record=AsyncMock())
    router = WmsTypedEffectCallbackRouter()
    outbox_repository = SimpleNamespace(
        get_by_dispatch_key_for_update=AsyncMock(
            return_value=SimpleNamespace(
                dispatch_key="dispatch-1",
                operation_identity="wms.inventory.confirm_inbound@v1",
                payload_json={"inbound_key": "INBOUND-A"},
                status=SystemOutboxStatus.DISPATCHING,
            )
        ),
        finish_sent_external_by_dispatch_key=AsyncMock(),
        isolate_for_reconciliation_by_dispatch_key=AsyncMock(),
    )
    router._outbox_repository = outbox_repository
    router._reconciliation_bridge = SimpleNamespace(open=AsyncMock())
    router._callback_adapters = {"WMS_INBOUND_CONFIRMED": adapter}

    handled = await router.route(
        SimpleNamespace(),
        callback_type="WMS_INBOUND_CONFIRMED",
        payload={
            "callback_type": "WMS_INBOUND_CONFIRMED",
            "data": {"dispatch_key": "dispatch-1", "inbound_key": "INBOUND-A", "accepted": True},
        },
        occurred_at_ms=1_700_000_000_000,
        source_event_id="wms-event-1",
    )

    assert handled is True
    adapter.record.assert_awaited_once()
    outbox_repository.finish_sent_external_by_dispatch_key.assert_awaited_once_with(
        ANY,
        "dispatch-1",
    )


@pytest.mark.asyncio
async def test_matching_callback_before_dispatch_opens_reconciliation_without_completing_intent() -> None:
    adapter = SimpleNamespace(record=AsyncMock())
    reconciliation = SimpleNamespace(open=AsyncMock())
    outbox_repository = SimpleNamespace(
        get_by_dispatch_key_for_update=AsyncMock(
            return_value=SimpleNamespace(
                dispatch_key="dispatch-1",
                operation_identity="wms.inventory.confirm_inbound@v1",
                payload_json={"inbound_key": "INBOUND-A"},
                status=SystemOutboxStatus.NEW,
            )
        ),
        finish_sent_external_by_dispatch_key=AsyncMock(),
        isolate_for_reconciliation_by_dispatch_key=AsyncMock(),
    )
    router = WmsTypedEffectCallbackRouter()
    router._outbox_repository = outbox_repository
    router._reconciliation_bridge = reconciliation
    router._callback_adapters = {"WMS_INBOUND_CONFIRMED": adapter}

    handled = await router.route(
        SimpleNamespace(),
        callback_type="WMS_INBOUND_CONFIRMED",
        payload={
            "callback_type": "WMS_INBOUND_CONFIRMED",
            "data": {"dispatch_key": "dispatch-1", "inbound_key": "INBOUND-A", "accepted": True},
        },
        occurred_at_ms=1_700_000_000_000,
        source_event_id="wms-event-before-dispatch",
    )

    assert handled is True
    adapter.record.assert_not_awaited()
    outbox_repository.finish_sent_external_by_dispatch_key.assert_not_awaited()
    reconciliation.open.assert_awaited_once()
    assert reconciliation.open.await_args.kwargs["reason_code"] == "WMS_CALLBACK_BEFORE_DISPATCH"
    outbox_repository.isolate_for_reconciliation_by_dispatch_key.assert_awaited_once_with(
        ANY,
        "dispatch-1",
        reason="WMS_CALLBACK_BEFORE_DISPATCH",
    )


@pytest.mark.asyncio
async def test_matching_late_callback_for_unknown_outbox_records_full_typed_evidence() -> None:
    adapter = SimpleNamespace(record=AsyncMock())
    reconciliation = SimpleNamespace(open=AsyncMock())
    outbox_repository = SimpleNamespace(
        get_by_dispatch_key_for_update=AsyncMock(
            return_value=SimpleNamespace(
                dispatch_key="dispatch-1",
                operation_identity="wms.inventory.confirm_inbound@v1",
                payload_json={"inbound_key": "INBOUND-A"},
                status=SystemOutboxStatus.UNKNOWN,
            )
        ),
        finish_sent_external_by_dispatch_key=AsyncMock(),
        isolate_for_reconciliation_by_dispatch_key=AsyncMock(),
    )
    router = WmsTypedEffectCallbackRouter()
    router._outbox_repository = outbox_repository
    router._reconciliation_bridge = reconciliation
    router._callback_adapters = {"WMS_INBOUND_CONFIRMED": adapter}

    handled = await router.route(
        SimpleNamespace(),
        callback_type="WMS_INBOUND_CONFIRMED",
        payload={
            "callback_type": "WMS_INBOUND_CONFIRMED",
            "data": {"dispatch_key": "dispatch-1", "inbound_key": "INBOUND-A", "accepted": True},
        },
        occurred_at_ms=1_700_000_000_000,
        source_event_id="wms-late-event-1",
    )

    assert handled is True
    adapter.record.assert_awaited_once()
    reconciliation.open.assert_not_awaited()
    outbox_repository.isolate_for_reconciliation_by_dispatch_key.assert_not_awaited()
