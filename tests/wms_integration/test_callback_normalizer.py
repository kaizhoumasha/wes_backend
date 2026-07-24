from __future__ import annotations

import pytest

from src.app.wms_integration.services.callback_normalizer import (
    WMS_RCS_FULL_BOX_EXCHANGE_REQUIRED_FIELDS,
    WMS_RCS_RACK_SOURCE_ENVELOPE_FIELDS,
    WmsExecutionCallbackNormalizer,
)


def test_callback_normalizer_accepts_rack_callback_without_trace_id() -> None:
    payload = _rack_payload(callback_type="WMS_RACK_TASK_RESULT", status="SUCCEEDED")
    payload.pop("trace_id")

    normalized = WmsExecutionCallbackNormalizer().normalize(payload)

    assert normalized == {
        "callback_type": "WMS_RACK_TASK_RESULT",
        "trace_id": None,
        "payload": payload,
    }


def test_callback_normalizer_accepts_rack_arrived_without_status() -> None:
    payload = _rack_payload(callback_type="WMS_RACK_ARRIVED")
    payload.pop("status")

    normalized = WmsExecutionCallbackNormalizer().normalize(payload)

    assert normalized["callback_type"] == "WMS_RACK_ARRIVED"
    assert normalized["trace_id"] == "trace-wms-001"


@pytest.mark.parametrize("missing_field", ["dispatch_key", *WMS_RCS_RACK_SOURCE_ENVELOPE_FIELDS, "status"])
def test_callback_normalizer_rejects_rack_task_result_missing_required_field(missing_field: str) -> None:
    payload = _rack_payload(callback_type="WMS_RACK_TASK_RESULT", status="SUCCEEDED")
    payload.pop(missing_field)

    with pytest.raises(ValueError, match=missing_field):
        WmsExecutionCallbackNormalizer().normalize(payload)


@pytest.mark.parametrize("missing_field", WMS_RCS_FULL_BOX_EXCHANGE_REQUIRED_FIELDS)
def test_callback_normalizer_rejects_full_box_exchange_missing_required_field(missing_field: str) -> None:
    payload = _full_box_payload()
    payload.pop(missing_field)

    with pytest.raises(ValueError, match=missing_field):
        WmsExecutionCallbackNormalizer().normalize(payload)


def test_callback_normalizer_rejects_non_rack_wms_callback_without_trace_id() -> None:
    payload = {
        "callback_type": "WMS_INVENTORY_STATUS",
        "dispatch_key": "inventory:sync:001",
        "status": "SUCCEEDED",
        "source_system": "WMS",
        "trace_id": "",
    }

    with pytest.raises(ValueError, match="trace_id is required"):
        WmsExecutionCallbackNormalizer().normalize(payload)


@pytest.mark.parametrize("invalid_operation_identity", [None, [], {}, 42])
def test_callback_normalizer_rejects_non_string_effect_hint_operation_identity(
    invalid_operation_identity: object,
) -> None:
    payload = {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "trace_id": "trace-wms-hint-001",
        "data": {
            "operation_identity": invalid_operation_identity,
            "idempotency_key": "idem-001",
            "dispatch_key": "dispatch-001",
        },
    }

    with pytest.raises(ValueError, match="operation_identity"):
        WmsExecutionCallbackNormalizer().normalize(payload)


@pytest.mark.parametrize("source_system", ["", None])
def test_callback_normalizer_rejects_non_rack_wms_callback_without_source_system(
    source_system: str | None,
) -> None:
    payload = {
        "callback_type": "WMS_INVENTORY_STATUS",
        "dispatch_key": "inventory:sync:001",
        "status": "SUCCEEDED",
        "source_system": source_system,
        "trace_id": "trace-wms-001",
    }
    if source_system is None:
        payload.pop("source_system")

    with pytest.raises(ValueError, match="source_system must be WMS or RCS"):
        WmsExecutionCallbackNormalizer().normalize(payload)


def test_callback_normalizer_rejects_invalid_wms_rcs_source_system() -> None:
    payload = _rack_payload(callback_type="RCS_RACK_TASK_RESULT", status="SUCCEEDED", source_system="ERP")

    with pytest.raises(ValueError, match="source_system must be WMS or RCS"):
        WmsExecutionCallbackNormalizer().normalize(payload)


@pytest.mark.parametrize(
    ("callback_type", "source_system"),
    [
        ("WMS_RACK_TASK_RESULT", "WMS"),
        ("RCS_RACK_TASK_RESULT", "RCS"),
        ("WMS_FULL_BOX_EXCHANGE_RESULT", "WMS"),
        ("RCS_FULL_BOX_EXCHANGE_RESULT", "RCS"),
    ],
)
def test_callback_normalizer_accepts_provider_source_matrix(callback_type: str, source_system: str) -> None:
    if "FULL_BOX" in callback_type:
        payload = _full_box_payload(callback_type=callback_type, source_system=source_system)
    else:
        payload = _rack_payload(callback_type=callback_type, status="SUCCEEDED", source_system=source_system)

    normalized = WmsExecutionCallbackNormalizer().normalize(payload)

    assert normalized["callback_type"] == callback_type


@pytest.mark.parametrize(
    ("callback_type", "source_system"),
    [
        ("WMS_RACK_TASK_RESULT", "RCS"),
        ("RCS_RACK_TASK_RESULT", "WMS"),
        ("WMS_FULL_BOX_EXCHANGE_RESULT", "RCS"),
        ("RCS_FULL_BOX_EXCHANGE_RESULT", "WMS"),
    ],
)
def test_callback_normalizer_rejects_provider_source_mismatch(callback_type: str, source_system: str) -> None:
    if "FULL_BOX" in callback_type:
        payload = _full_box_payload(callback_type=callback_type, source_system=source_system)
    else:
        payload = _rack_payload(callback_type=callback_type, status="SUCCEEDED", source_system=source_system)

    with pytest.raises(ValueError, match="source_system must match callback_type provider"):
        WmsExecutionCallbackNormalizer().normalize(payload)


def _rack_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "callback_type": "WMS_RACK_ARRIVED",
        "trace_id": "trace-wms-001",
        "dispatch_key": "rack-operation:op-001:1:ALLOCATE_AND_MOVE_RACK",
        "status": "SUCCEEDED",
        "source_system": "WMS",
        "source_event_id": "evt-wms-001",
        "source_version": "1",
        "occurred_at": "2026-05-26T12:00:00Z",
        "request_id": "req-wms-001",
        "timestamp": "2026-05-26T12:00:01Z",
        "signature": "signed",
    }
    payload.update(overrides)
    return payload


def _full_box_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
        "trace_id": "trace-full-box-001",
        "dispatch_key": "handling:full-box:release-001:move:1",
        "exchange_request_code": "handling:full-box:release-001:move:1",
        "rack_release_id": "release-001",
        "wms_rcs_task_id": "task-wms-001",
        "source_system": "WMS",
        "source_event_id": "evt-full-box-001",
        "source_version": "1",
        "occurred_at": "2026-05-26T12:00:00Z",
        "request_id": "req-full-box-001",
        "timestamp": "2026-05-26T12:00:01Z",
        "signature": "signed",
        "exchange_status": "BUSINESS_COMPLETED",
    }
    payload.update(overrides)
    return payload
