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

REMOVED_WMS_RCS_CALLBACK_TYPES = (
    "RCS_GRN_RECEIVED",
    "RCS_PALLET_ARRIVED",
    "RCS_INVENTORY_UPDATED",
    "RCS_PDA_OPERATION_RECORDED",
    "WMS_EXCHANGE_COMPLETED",
    "RCS_EXCHANGE_COMPLETED",
    "WMS_TASK_CHANGE",
    "RCS_TASK_CHANGE",
    "WMS_REJECTED",
    "RCS_REJECTED",
    "WMS_FAILED",
    "RCS_FAILED",
    "WMS_RACK_TASK_RESULT",
    "RCS_RACK_TASK_RESULT",
    "WMS_RACK_TASK_PROGRESS",
    "RCS_RACK_TASK_PROGRESS",
    "WMS_RACK_ARRIVED",
    "RCS_RACK_ARRIVED",
    "WMS_RACK_EXCHANGE_PROGRESS",
    "RCS_RACK_EXCHANGE_PROGRESS",
    "WMS_RACK_EXCHANGE_FAILED",
    "RCS_RACK_EXCHANGE_FAILED",
    "WMS_RACK_OPERATION_FAILED",
    "RCS_RACK_OPERATION_FAILED",
    "WMS_BIN_MOVE_PROGRESS",
    "RCS_BIN_MOVE_PROGRESS",
    "WMS_BIN_MOVE_COMPLETED",
    "RCS_BIN_MOVE_COMPLETED",
    "WMS_BIN_MOVE_FAILED",
    "RCS_BIN_MOVE_FAILED",
    "WMS_FULL_BOX_EXCHANGE_RESULT",
    "RCS_FULL_BOX_EXCHANGE_RESULT",
    "WMS_EMPTY_BOX_TRANSFER_RESULT",
    "RCS_EMPTY_BOX_TRANSFER_RESULT",
    "WMS_FULL_BOX_TRANSFER_RESULT",
    "RCS_FULL_BOX_TRANSFER_RESULT",
    "WMS_HANDLING_TASK_RESULT",
    "RCS_HANDLING_TASK_RESULT",
    "WMS_TRANSPORT_COMPLETED",
    "RCS_TRANSPORT_COMPLETED",
    "WMS_ROUGH_SORTER_INBOUND",
)


@pytest.mark.parametrize("callback_type", WMS_ORDINARY_EVENT_TYPES)
def test_callback_normalizer_accepts_only_declared_ordinary_wms_events(callback_type: str) -> None:
    payload = {
        "callback_type": callback_type,
        "source_system": "WMS",
        "trace_id": f"trace-{callback_type.lower()}",
    }

    normalized = WmsExecutionCallbackNormalizer().normalize(payload)

    assert normalized == {
        "callback_type": callback_type,
        "trace_id": payload["trace_id"],
        "payload": payload,
    }


def test_callback_normalizer_accepts_typed_effect_status_hint() -> None:
    payload = {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "trace_id": "trace-wms-hint-001",
        "data": {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "idem-001",
            "dispatch_key": "dispatch-001",
        },
    }

    normalized = WmsExecutionCallbackNormalizer().normalize(payload)

    assert normalized["callback_type"] == "WMS_EFFECT_STATUS_HINT"


@pytest.mark.parametrize("callback_type", REMOVED_WMS_RCS_CALLBACK_TYPES)
def test_callback_normalizer_rejects_removed_wms_rcs_callback_family(callback_type: str) -> None:
    source_system = "RCS" if callback_type.startswith("RCS_") else "WMS"
    payload = {
        "callback_type": callback_type,
        "source_system": source_system,
        "trace_id": f"trace-{callback_type.lower()}",
        "dispatch_key": f"dispatch-{callback_type.lower()}",
        "status": "SUCCEEDED",
    }

    with pytest.raises(ValueError, match="callback_type is not allowed"):
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
        "trace_id": "trace-wms-hint-001",
        "data": {
            "operation_identity": invalid_operation_identity,
            "idempotency_key": "idem-001",
            "dispatch_key": "dispatch-001",
        },
    }

    with pytest.raises(ValueError, match="operation_identity"):
        WmsExecutionCallbackNormalizer().normalize(payload)
