"""WMS 普通事件 typed normalizer 与 registry 合同。"""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from src.app.wms_integration.ports.event import (
    WmsGrnReceivedEvent,
    WmsInventoryUpdatedEvent,
    WmsPalletArrivedEvent,
    WmsPdaOperationRecordedEvent,
)


def _event(event_type: str, data: dict[str, object]) -> dict[str, object]:
    return {
        "source_system": "WMS",
        "event_type": event_type,
        "source_event_id": "evt-001",
        "source_version": "1",
        "occurred_at": "2026-07-30T10:00:00+08:00",
        "request_id": "req-001",
        "correlation_id": "corr-001",
        "data": data,
    }


@pytest.mark.parametrize(
    ("event_type", "data", "expected_type"),
    (
        (
            "WMS_GRN_RECEIVED",
            {
                "grn_id": "GRN-001",
                "po_number": "PO-001",
                "po_item": "10",
                "material_code": "MAT-001",
                "received_quantity": 5,
                "warehouse_code": "WH-A",
            },
            WmsGrnReceivedEvent,
        ),
        (
            "WMS_PALLET_ARRIVED",
            {"pallet_id": "PLT-001", "arrived_station": "ST-A"},
            WmsPalletArrivedEvent,
        ),
        (
            "WMS_INVENTORY_UPDATED",
            {"inventory_reference": "INV-EVT-001", "material_code": "MAT-001"},
            WmsInventoryUpdatedEvent,
        ),
        (
            "WMS_PDA_OPERATION_RECORDED",
            {
                "operation_record_id": "PDA-OP-001",
                "operation_type": "MANUAL_COUNT",
                "operator_code": "OP-001",
            },
            WmsPdaOperationRecordedEvent,
        ),
    ),
)
def test_wms_event_normalizer_accepts_public_top_level_contract(
    event_type: str,
    data: dict[str, object],
    expected_type: type,
) -> None:
    from src.app.wms_integration.services.wms_event_normalizer import WmsEventNormalizer

    event = WmsEventNormalizer().dispatch(event_type, cast("dict", _event(event_type, data)))

    assert isinstance(event, expected_type)
    assert event.source_event_id == "evt-001"
    assert event.correlation_id == "corr-001"


@pytest.mark.parametrize("forbidden", ("item_count", "items"))
def test_grn_rejects_fabricated_line_items(forbidden: str) -> None:
    from src.app.wms_integration.services.wms_event_normalizer import WmsEventNormalizer

    payload = _event(
        "WMS_GRN_RECEIVED",
        {
            "grn_id": "GRN-001",
            "po_number": "PO-001",
            "po_item": "10",
            "material_code": "MAT-001",
            "received_quantity": 5,
            "warehouse_code": "WH-A",
            forbidden: [] if forbidden == "items" else 1,
        },
    )

    with pytest.raises(ValidationError, match=forbidden):
        WmsEventNormalizer().dispatch("WMS_GRN_RECEIVED", cast("dict", payload))


def test_wms_event_rejects_nested_legacy_envelope() -> None:
    from src.app.wms_integration.services.wms_event_normalizer import WmsEventNormalizer

    with pytest.raises(ValidationError):
        WmsEventNormalizer().dispatch("WMS_GRN_RECEIVED", {"envelope": {}, "data": {}})


def test_wms_event_normalizer_rejects_unknown_event_type() -> None:
    from src.app.wms_integration.services.wms_event_normalizer import WmsEventNormalizer

    with pytest.raises(ValueError, match="unknown wms event_type"):
        WmsEventNormalizer().dispatch("WMS_FAKE_EVENT", {"source_event_id": "x"})


@pytest.mark.parametrize(
    ("path", "blank_field"),
    (
        ((), "source_event_id"),
        ((), "source_version"),
        ((), "request_id"),
        ((), "correlation_id"),
        (("data",), "grn_id"),
        (("data",), "po_number"),
        (("data",), "po_item"),
        (("data",), "material_code"),
        (("data",), "warehouse_code"),
    ),
)
def test_wms_event_rejects_whitespace_only_stable_identity(path: tuple[str, ...], blank_field: str) -> None:
    from src.app.wms_integration.services.wms_event_normalizer import WmsEventNormalizer

    payload = _event(
        "WMS_GRN_RECEIVED",
        {
            "grn_id": "GRN-001",
            "po_number": "PO-001",
            "po_item": "10",
            "material_code": "MAT-001",
            "received_quantity": 5,
            "warehouse_code": "WH-A",
        },
    )
    target = payload if not path else cast("dict[str, object]", payload[path[0]])
    target[blank_field] = "   "

    with pytest.raises(ValidationError, match=blank_field):
        WmsEventNormalizer().dispatch("WMS_GRN_RECEIVED", cast("dict", payload))
