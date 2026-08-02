"""普通 WMS event 的专用端点与 typed admission 合同。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxCorrelationUnavailable
from tests.api import callback_test_support
from tests.api.callback_test_support import RequestFactory, _response_data


@pytest.fixture(autouse=True)
def mock_fast_fail_check():
    yield from callback_test_support.mock_fast_fail_check.__wrapped__()


@pytest.fixture
def db_session():
    return callback_test_support.db_session.__wrapped__()


@pytest.fixture
def build_request() -> RequestFactory:
    return callback_test_support.build_request.__wrapped__()


def _grn_event(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_system": "WMS",
        "event_type": "WMS_GRN_RECEIVED",
        "source_event_id": "grn-event-001",
        "source_version": "1",
        "occurred_at": "2026-07-30T08:00:00Z",
        "request_id": "wms-request-001",
        "correlation_id": "corr-grn-001",
        "data": {
            "grn_id": "GRN-001",
            "po_number": "PO-001",
            "po_item": "10",
            "material_code": "MAT-001",
            "received_quantity": 5,
            "warehouse_code": "WH-A",
        },
    }
    payload.update(overrides)
    return payload


WMS_EVENT_CASES = (
    (_grn_event(), "po_item", "10"),
    (
        {
            **_grn_event(event_type="WMS_PALLET_ARRIVED", source_event_id="pallet-event-001"),
            "data": {"pallet_id": "PALLET-001", "arrived_station": "STATION-A"},
        },
        "pallet_id",
        "PALLET-001",
    ),
    (
        {
            **_grn_event(event_type="WMS_INVENTORY_UPDATED", source_event_id="inventory-event-001"),
            "data": {"inventory_reference": "INVENTORY-001", "material_code": "MAT-001"},
        },
        "inventory_reference",
        "INVENTORY-001",
    ),
    (
        {
            **_grn_event(event_type="WMS_PDA_OPERATION_RECORDED", source_event_id="pda-event-001"),
            "data": {
                "operation_record_id": "PDA-001",
                "operation_type": "MANUAL_COUNT",
                "operator_code": "OP-001",
            },
        },
        "operation_record_id",
        "PDA-001",
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "key_field", "key_value"), WMS_EVENT_CASES)
async def test_callback_event_accepts_typed_wms_event_without_device_lookup(
    db_session,
    build_request,
    payload,
    key_field,
    key_value,
) -> None:
    outcome = SimpleNamespace(trace_id="trace-wms-001", is_duplicate=False)
    with (
        patch(
            "src.app.callback.services.callback_ingress_service.callback_orchestration_service.process_wms_event",
            new=AsyncMock(return_value=outcome),
        ) as process_wms_event,
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ),
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ),
        patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-001"),
    ):
        from src.app.callback.v1.callback import callback_event

        response = SimpleNamespace(status_code=200)
        body = await callback_event(
            request=build_request(body=payload, path="/api/v1/callback/event"),
            db=db_session,
            response=response,
        )

    assert response.status_code == 200
    data = _response_data(body)
    assert data["device_code"] is None
    assert data["source_system"] == "WMS"
    assert data["event_type"] == payload["event_type"]
    canonical = process_wms_event.await_args.kwargs["payload"]
    assert canonical["source_event_id"] == payload["source_event_id"]
    assert canonical["data"][key_field] == key_value
    assert process_wms_event.await_args.kwargs["correlation_id"] == payload.get("correlation_id")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: payload.pop("source_version"),
        lambda payload: payload.update({"occurred_at": "2026-07-30T08:00:00"}),
        lambda payload: payload.update({"source_event_id": "   "}),
        lambda payload: payload.update({"correlation_id": "   "}),
        lambda payload: payload["data"].update({"grn_id": "   "}),
        lambda payload: payload["data"].update({"items": []}),
        lambda payload: payload["data"].update({"item_count": 1}),
    ),
)
async def test_callback_event_rejects_invalid_wms_event_before_persistence(
    db_session,
    build_request,
    mutate,
) -> None:
    payload = _grn_event()
    mutate(payload)
    with patch(
        "src.app.callback.services.callback_ingress_service.callback_orchestration_service.process_wms_event",
        new=AsyncMock(),
    ) as process_wms_event:
        from src.app.callback.v1.callback import callback_event

        response = SimpleNamespace(status_code=200)
        body = await callback_event(
            request=build_request(body=payload, path="/api/v1/callback/event"),
            db=db_session,
            response=response,
        )

    assert response.status_code == 400
    assert body["code"] == "2004"
    process_wms_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_event_rejects_unknown_wms_correlation_with_stable_503(
    db_session,
    build_request,
) -> None:
    with patch(
        "src.app.callback.services.callback_ingress_service.callback_orchestration_service.process_wms_event",
        new=AsyncMock(side_effect=RuntimeInboxCorrelationUnavailable(correlation_id="corr-grn-001")),
    ):
        from src.app.callback.v1.callback import callback_event

        response = SimpleNamespace(status_code=200)
        with pytest.raises(HTTPException) as exc_info:
            await callback_event(
                request=build_request(body=_grn_event(), path="/api/v1/callback/event"),
                db=db_session,
                response=response,
            )

    assert exc_info.value.status_code == 503
