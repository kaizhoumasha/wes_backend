"""WMS 入站允许集与旧 terminal callback 拒绝合同。"""

from __future__ import annotations

import pytest

from src.app.wms_integration.services.callback_normalizer import WmsExecutionCallbackNormalizer

WMS_ORDINARY_EVENT_TYPES = (
    "WMS_GRN_RECEIVED",
    "WMS_PALLET_ARRIVED",
    "WMS_INVENTORY_UPDATED",
    "WMS_PDA_OPERATION_RECORDED",
)


@pytest.mark.parametrize("callback_type", WMS_ORDINARY_EVENT_TYPES)
def test_external_callback_normalizer_rejects_ordinary_wms_events(callback_type: str) -> None:
    payload = {
        "callback_type": callback_type,
        "source_system": "WMS",
        "trace_id": f"trace-{callback_type.lower()}",
    }

    with pytest.raises(ValueError, match="/api/v1/wms/events"):
        WmsExecutionCallbackNormalizer().normalize(payload)


def test_callback_normalizer_accepts_typed_effect_status_hint() -> None:
    payload = {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": "hint-event-001",
        "occurred_at": "2026-07-30T08:00:00Z",
        "trace_id": "trace-wms-hint-001",
        "data": {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "idem-001",
            "dispatch_key": "dispatch-001",
        },
    }

    normalized = WmsExecutionCallbackNormalizer().normalize(payload)

    assert normalized["callback_type"] == "WMS_EFFECT_STATUS_HINT"


def test_callback_normalizer_accepts_hint_without_optional_trace_id() -> None:
    payload = {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": "hint-event-001",
        "occurred_at": "2026-07-30T08:00:00Z",
        "data": {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "idem-001",
            "dispatch_key": "dispatch-001",
        },
    }

    normalized = WmsExecutionCallbackNormalizer().normalize(payload)

    assert normalized["trace_id"] is None


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "command_code",
        "device_code",
        "finish_time",
        "status",
        "result",
        "reason",
        "reason_code",
        "items",
        "source_version",
        "request_id",
    ),
)
def test_callback_normalizer_rejects_hint_extra_top_level_fields(forbidden_field: str) -> None:
    payload = {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": "hint-event-terminal",
        "occurred_at": "2026-07-30T08:00:00Z",
        "trace_id": "trace-wms-hint-terminal",
        "data": {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "idem-001",
            "dispatch_key": "dispatch-001",
        },
        forbidden_field: [] if forbidden_field == "items" else "COMPLETED",
    }

    with pytest.raises(ValueError, match=forbidden_field):
        WmsExecutionCallbackNormalizer().normalize(payload)


@pytest.mark.parametrize("missing_field", ("source_system", "callback_type", "source_event_id", "occurred_at", "data"))
def test_callback_normalizer_rejects_hint_without_stable_event_evidence(missing_field: str) -> None:
    payload = {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": "hint-event-001",
        "occurred_at": "2026-07-30T08:00:00Z",
        "trace_id": "trace-wms-hint-001",
        "data": {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "idem-001",
            "dispatch_key": "dispatch-001",
        },
    }
    payload.pop(missing_field)

    with pytest.raises(ValueError, match=missing_field):
        WmsExecutionCallbackNormalizer().normalize(payload)


def test_callback_normalizer_rejects_hint_source_event_id_over_runtime_inbox_limit() -> None:
    payload = {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": "x" * 161,
        "occurred_at": "2026-07-30T08:00:00Z",
        "data": {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "idem-001",
            "dispatch_key": "dispatch-001",
        },
    }

    with pytest.raises(ValueError, match="source_event_id"):
        WmsExecutionCallbackNormalizer().normalize(payload)


def test_callback_normalizer_rejects_unknown_wms_event() -> None:
    with pytest.raises(ValueError, match="callback_type is not allowed"):
        WmsExecutionCallbackNormalizer().normalize(
            {
                "callback_type": "WMS_INVENTORY_STATUS",
                "source_system": "WMS",
                "trace_id": "trace-unknown",
            }
        )


@pytest.mark.parametrize("invalid_operation_identity", [None, [], {}, 42])
def test_callback_normalizer_rejects_invalid_status_hint_operation_identity(
    invalid_operation_identity: object,
) -> None:
    payload = {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": "hint-event-invalid",
        "occurred_at": "2026-07-30T08:00:00Z",
        "trace_id": "trace-wms-hint-001",
        "data": {
            "operation_identity": invalid_operation_identity,
            "idempotency_key": "idem-001",
            "dispatch_key": "dispatch-001",
        },
    }

    with pytest.raises(ValueError, match="operation_identity"):
        WmsExecutionCallbackNormalizer().normalize(payload)
