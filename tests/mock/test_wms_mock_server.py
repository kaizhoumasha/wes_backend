import asyncio
import hashlib
import hmac
import json
import os
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.app.runtime.orchestration.sandbox_catalog_bridge import (
    mock_wms_inventory_seed,
    rough_sorter_scan_completed_payload,
)
from src.app.sys.canonical_dispatch import CanonicalPayload, ExternalHttpDispatchRequest
from src.app.sys.external_http_binding import (
    ExternalHttpBindingDefinition,
    ExternalHttpProviderProfileDefinition,
    freeze_external_http_binding,
)
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.operation_contract import WmsCompletionMode
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS
from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck
from src.app.wms_integration.services.http_transport import sign_wms_hmac_request
from src.core.api_security import calculate_body_hmac_signature
from tests.mock import wms_mock_server
from tests.mock.wms_northbound_contract import (
    ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
    MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V1,
    MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2,
    canonical_status_string,
    canonical_submit_string,
)
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

ASYNC_OPERATION_IDENTITY = "wms.fulfillment.request_rack_supply@v1"
ASYNC_OPERATION_PATH = "/api/wms/fulfillment/rack-supply"


def _async_payload() -> dict[str, object]:
    return dict(REQUEST_FIXTURES[ASYNC_OPERATION_IDENTITY])


def _async_body() -> bytes:
    return json.dumps(_async_payload(), separators=(",", ":")).encode()


def _wire_fixture_case(
    operation_identity: str,
    *,
    remove: str | None = None,
    update: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """在参数化阶段生成正反 wire fixture，测试体不覆写 operation 参数。"""

    valid_payload = dict(REQUEST_FIXTURES[operation_identity])
    invalid_payload = {**valid_payload, **(update or {})}
    if remove is not None:
        invalid_payload.pop(remove)
    return valid_payload, invalid_payload


@pytest.fixture(autouse=True)
def reset_wms_mock_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V1, "test-mock-northbound-secret-v1")
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, "test-mock-northbound-secret-v2")
    wms_mock_server.reset_mock_wms_state()


def _submit_headers(
    *,
    body: bytes,
    path: str,
    operation_identity: str,
    idempotency_key: str,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or uuid4().hex
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical = canonical_submit_string(
        method="POST",
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        payload_hash=payload_hash,
        operation_identity=operation_identity,
        idempotency_key=idempotency_key,
    )
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
        "X-WES-Content-SHA256": payload_hash,
        "X-WES-Credential-Reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "X-WES-Nonce": nonce,
        "X-WES-Operation-Identity": operation_identity,
        "X-WES-Signature": hmac.new(
            os.environ[MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2].encode(),
            canonical.encode(),
            hashlib.sha256,
        ).hexdigest(),
        "X-WES-Signature-Algorithm": "HMAC_SHA256",
        "X-WES-Timestamp": timestamp,
    }


def _status_headers(
    *,
    path: str,
    body: bytes = b"",
    method: str = "GET",
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or uuid4().hex
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical = canonical_status_string(
        method=method, path=path, timestamp=timestamp, nonce=nonce, payload_hash=payload_hash
    )
    return {
        "X-WMS-Content-SHA256": payload_hash,
        "X-WMS-Credential-Reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "X-WMS-Nonce": nonce,
        "X-WMS-Signature": hmac.new(
            os.environ[MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2].encode(),
            canonical.encode(),
            hashlib.sha256,
        ).hexdigest(),
        "X-WMS-Signature-Algorithm": "HMAC_SHA256",
        "X-WMS-Timestamp": timestamp,
    }


def _inventory_query_request(
    client: TestClient,
    *,
    material_code: str,
    lot_no: str | None = None,
    warehouse_code: str | None = None,
    owner_code: str | None = None,
):
    params = {
        key: value
        for key, value in {
            "material_code": material_code,
            "lot_no": lot_no,
            "warehouse_code": warehouse_code,
            "owner_code": owner_code,
        }.items()
        if value is not None
    }
    raw_path = "/api/wms/inventory/query?" + urlencode(tuple(params.items()))
    return client.get(
        "/api/wms/inventory/query",
        params=params,
        headers=_status_headers(path=raw_path),
    )


def _typed_inventory_record(item: dict[str, object]) -> dict[str, object]:
    return {
        "material_code": item["sku"],
        "available_quantity": str(item["available_qty"]),
        "total_quantity": str(item["total_qty"]),
        "reserved_quantity": str(item["reserved_qty"]),
        "location_code": None,
        "lot_no": item.get("lot_no"),
    }


def test_wms_mock_loads_shared_catalog_without_importing_runtime_package() -> None:
    source = Path(wms_mock_server.__file__).read_text()

    assert "from src.workline_runtime.sandbox_catalog import" not in source
    assert "spec_from_file_location" in source


def test_wms_mock_registers_exact_static_routes_for_all_frozen_operations() -> None:
    expected = {(f"/api/wms{operation.path_template}", operation.http_method.value) for operation in WMS_OPERATIONS}
    registered = {
        (route.path, method)
        for route in wms_mock_server.app.routes
        for method in route.methods or ()
        if route.path.startswith("/api/wms")
    }

    assert registered == expected
    assert all("{operation_path:path}" not in route.path for route in wms_mock_server.app.routes)


def test_q19_post_query_requires_valid_hmac_and_rejects_replay() -> None:
    operation_identity = "wms.document.validate_rough_sorter_admission@v1"
    path = "/api/wms/documents/rough-sorter-admission/validate"
    body = json.dumps(REQUEST_FIXTURES[operation_identity], separators=(",", ":")).encode()
    valid_headers = _status_headers(path=path, body=body, method="POST", nonce="q19-valid-nonce")

    with TestClient(wms_mock_server.app) as client:
        assert client.post(path, content=body).status_code == 401

        bad_signature = {**valid_headers, "X-WMS-Signature": "0" * 64}
        assert client.post(path, content=body, headers=bad_signature).status_code == 401

        stale_headers = _status_headers(
            path=path,
            body=body,
            method="POST",
            timestamp=str(int(time.time()) - 600),
            nonce="q19-stale-nonce",
        )
        assert client.post(path, content=body, headers=stale_headers).status_code == 401

        accepted = client.post(path, content=body, headers=valid_headers)
        replayed = client.post(path, content=body, headers=valid_headers)

    assert accepted.status_code == 200
    assert replayed.status_code == 401


def test_wms_mock_does_not_expose_deprecated_operation_alias_paths() -> None:
    registered = {(route.path, method) for route in wms_mock_server.app.routes for method in route.methods or ()}

    assert ("/api/wms/fulfillment/package-binding", "POST") not in registered
    assert ("/api/wms/inventory/reserve", "POST") not in registered
    assert ("/api/wms/outbound/confirm", "POST") not in registered


def test_wms_mock_does_not_expose_legacy_transport_or_callback_routes() -> None:
    registered = {(route.path, method) for route in wms_mock_server.app.routes for method in route.methods or ()}

    assert ("/api/wms/rack-operation", "POST") not in registered
    assert ("/api/wms/transport-request", "POST") not in registered
    assert ("/api/wms/legacy/full-box-exchange", "POST") not in registered


def test_debug_grn_endpoint_returns_direct_po_line_without_nested_items() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/debug/wms/grn/GRN.0001")

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    assert payload["data"] == {
        "grn_id": "GRN.0001",
        "po_number": "PO-2025-001",
        "po_item": "001",
        "material_code": "CAP001",
        "planned_quantity": "50000",
        "received_quantity": "25000",
        "remaining_quantity": "25000",
        "batch_no": "LOT-2026-001",
        "quality_status": "PARTIAL_RECEIVED",
    }
    assert "items" not in payload["data"]


def test_e02_uses_typed_post_without_legacy_envelope_or_delete_route() -> None:
    operation_identity = "wms.inventory.release_reservation@v1"
    path = "/api/wms/inventory/reservations/release"
    payload = REQUEST_FIXTURES[operation_identity]
    body = json.dumps(payload, separators=(",", ":")).encode()

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key="idem-e02-typed",
            ),
        )
        deleted = client.delete("/api/wms/inventory/reserve/RES-001")

    assert response.status_code == 200
    WMS_OPERATION_BY_IDENTITY[operation_identity].result_model.model_validate(response.json())
    assert set(response.json()) != {"code", "data"}
    assert deleted.status_code in {404, 405}


def test_q14_static_route_consumes_typed_material_code_contract() -> None:
    operation_identity = "wms.inventory.query_inventory@v1"
    payload = REQUEST_FIXTURES[operation_identity]
    raw_path = "/api/wms/inventory/query?" + urlencode(payload)

    with TestClient(wms_mock_server.app) as client:
        response = client.get(raw_path, headers=_status_headers(path=raw_path))

    assert response.status_code == 200
    result = WMS_OPERATION_BY_IDENTITY[operation_identity].result_model.model_validate(response.json())
    assert result.source_version == "mock-inventory-v1"


def test_standalone_wms_mock_server_disables_query_bearing_access_log() -> None:
    server = wms_mock_server.WmsMockServer()

    assert server.config.access_log is False


def test_wms_mock_locations_route_passes_ruff_safe_variable_path() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/debug/wms/locations", params={"zone": "KITTING_AREA"})

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "location_code": "KITTING_AREA_LOC_01",
            "zone_code": "KITTING_AREA",
            "location_type": "BUFFER",
            "status": "AVAILABLE",
        }
    ]


def test_wms_mock_racks_route_returns_stateful_six_and_three_cell_pool() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/debug/wms/racks", params={"type": "SINGLE_LAYER"})

    assert response.status_code == 200
    racks = response.json()["data"]
    six_cell_racks = [rack for rack in racks if rack["layout_code"] == "SIX_CELL"]
    three_cell_racks = [rack for rack in racks if rack["layout_code"] == "THREE_CELL"]
    mixed_racks = [rack for rack in racks if rack["layout_code"] == "MIXED"]
    assert len(six_cell_racks) >= 6
    assert len(three_cell_racks) >= 4
    assert "RACK-001" in {rack["rack_id"] for rack in mixed_racks}
    assert "RACK-3CELL-001" in {rack["rack_id"] for rack in three_cell_racks}
    for rack in racks:
        assert {
            "rack_id",
            "rack_type",
            "status",
            "current_location",
            "layout_code",
            "bin_type",
            "active_position_code",
            "allocated_operation_key",
        } <= rack.keys()


def test_wms_mock_debug_reset_restores_rack_state_and_clears_fault_injection() -> None:
    wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] = "ALLOCATED"
    wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["active_position_code"] = "SINGLE_LAYER_A"
    wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = "RACK-001"
    wms_mock_server.mock_wms_state.recent_operations.append({"operation_key": "op-mutated"})
    wms_mock_server.fault_injection_state["next_status"] = 503
    wms_mock_server.fault_injection_state["next_delay"] = 1.5

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/debug/reset")

    assert response.status_code == 200
    assert response.json()["data"]["reset"] is True
    rack = wms_mock_server.mock_wms_state.rack_pool["RACK-001"]
    assert rack["status"] == "AVAILABLE"
    assert rack["active_position_code"] is None
    assert rack["allocated_operation_key"] is None
    assert wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] is None
    assert wms_mock_server.mock_wms_state.recent_operations == []
    assert wms_mock_server.fault_injection_state == {"next_status": 200, "next_delay": 0.0}


def test_wms_mock_debug_reset_bypasses_pending_fault_injection_delay() -> None:
    wms_mock_server.fault_injection_state["next_status"] = 503
    wms_mock_server.fault_injection_state["next_delay"] = 1.5

    started_at = monotonic()
    with TestClient(wms_mock_server.app) as client:
        response = client.post("/debug/reset")
    elapsed = monotonic() - started_at

    assert response.status_code == 200
    assert elapsed < 0.5
    assert wms_mock_server.fault_injection_state == {"next_status": 200, "next_delay": 0.0}


@pytest.mark.parametrize(
    ("path", "operation_identity", "payload"),
    tuple(
        (
            f"/api/wms{operation.path_template}",
            operation.identity,
            REQUEST_FIXTURES[operation.identity],
        )
        for operation in WMS_OPERATIONS
        if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
    ),
)
def test_wms_mock_northbound_submit_is_idempotent_and_sends_one_callback_hint(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    operation_identity: str,
    payload: dict[str, object],
) -> None:
    callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", callback)
    body = json.dumps(payload, separators=(",", ":")).encode()
    with TestClient(wms_mock_server.app) as client:
        first = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body, path=path, operation_identity=operation_identity, idempotency_key="idem-submit-001"
            ),
        )
        processing_replay = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body, path=path, operation_identity=operation_identity, idempotency_key="idem-submit-001"
            ),
        )
        status_path = "/northbound/operations/status?" + urlencode(
            {"operation_identity": operation_identity, "idempotency_key": "idem-submit-001"}
        )
        accepted = client.get(status_path, headers=_status_headers(path=status_path))
        processing = client.get(status_path, headers=_status_headers(path=status_path))
        completed = client.get(status_path, headers=_status_headers(path=status_path))
        completed_replay = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body, path=path, operation_identity=operation_identity, idempotency_key="idem-submit-001"
            ),
        )
        conflict_body = json.dumps({**payload, "dispatch_key": "changed"}, separators=(",", ":")).encode()
        conflict = client.post(
            path,
            content=conflict_body,
            headers=_submit_headers(
                body=conflict_body, path=path, operation_identity=operation_identity, idempotency_key="idem-submit-001"
            ),
        )
        effects = client.get(
            "/debug/northbound/effects?"
            + urlencode({"operation_identity": operation_identity, "idempotency_key": "idem-submit-001"})
        )

    assert first.status_code == 202
    first_ack = WmsEffectAck.model_validate(first.json()["data"])
    assert first_ack.operation_identity == operation_identity
    assert first_ack.idempotency_key == "idem-submit-001"
    assert first_ack.submission_state == "ACCEPTED"
    assert processing_replay.status_code == 409
    assert processing_replay.json()["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    replay_ack = WmsEffectAck.model_validate(processing_replay.json()["data"])
    assert replay_ack.operation_identity == operation_identity
    assert replay_ack.idempotency_key == "idem-submit-001"
    assert replay_ack.provider_reference == first_ack.provider_reference
    assert replay_ack.submission_state == "IN_PROGRESS_REPLAY"
    assert set(accepted.json()) == {
        "state",
        "provider_reference",
        "reason_code",
        "updated_at",
        "source_version",
        "result_payload",
    }
    assert [accepted.json()["state"], processing.json()["state"], completed.json()["state"]] == [
        "ACCEPTED",
        "PROCESSING",
        "COMPLETED",
    ]
    WMS_OPERATION_BY_IDENTITY[operation_identity].result_model.model_validate(completed.json()["result_payload"])
    assert completed_replay.status_code == 200
    completed_ack = WmsEffectAck.model_validate(completed_replay.json()["data"])
    assert completed_ack.operation_identity == operation_identity
    assert completed_ack.idempotency_key == "idem-submit-001"
    assert completed_ack.provider_reference == first_ack.provider_reference
    assert completed_ack.submission_state == "REPLAY"
    assert conflict.status_code == 422
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert effects.json()["effect_count"] == 1
    callback.assert_awaited_once()


@pytest.mark.parametrize(
    ("path", "operation_identity"),
    tuple(
        (f"/api/wms{operation.path_template}", operation.identity)
        for operation in WMS_OPERATIONS
        if operation.completion_mode is WmsCompletionMode.SYNC_RESULT
    ),
)
def test_wms_mock_sync_effect_returns_terminal_result_without_status_or_hint(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    operation_identity: str,
) -> None:
    callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", callback)
    payload = REQUEST_FIXTURES[operation_identity]
    body = json.dumps(payload, separators=(",", ":")).encode()
    status_path = "/northbound/operations/status?" + urlencode(
        {"operation_identity": operation_identity, "idempotency_key": "idem-sync-001"}
    )

    with TestClient(wms_mock_server.app) as client:
        submitted = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key="idem-sync-001",
            ),
        )
        status = client.get(status_path, headers=_status_headers(path=status_path))
        hints = client.get(
            "/debug/northbound/callback-hints?"
            + urlencode({"operation_identity": operation_identity, "idempotency_key": "idem-sync-001"})
        )

    assert submitted.status_code == 200
    WMS_OPERATION_BY_IDENTITY[operation_identity].result_model.model_validate(submitted.json())
    assert "northbound_status" not in submitted.json()
    assert status.status_code == 422
    assert status.json()["code"] == "STATUS_OPERATION_NOT_ASYNC_EFFECT"
    assert hints.json() == {"hints": []}
    callback.assert_not_awaited()


def test_wms_mock_northbound_submit_rejects_invalid_hmac() -> None:
    path = "/api/wms/inventory/confirm-inbound"
    body = b'{"dispatch_key":"dispatch-auth","inbound_key":"inbound-auth"}'
    headers = _submit_headers(
        body=body,
        path=path,
        operation_identity="wms.inventory.confirm_inbound@v1",
        idempotency_key="idem-auth-001",
    )
    headers["X-WES-Signature"] = "invalid"

    with TestClient(wms_mock_server.app) as client:
        response = client.post(path, content=body, headers=headers)

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_HMAC_SIGNATURE"


def test_wms_mock_northbound_submit_rejects_stale_signed_timestamp() -> None:
    path = "/api/wms/inventory/confirm-inbound"
    body = (
        b'{"dispatch_key":"dispatch-stale","inbound_key":"inbound-stale",'
        b'"material_code":"material-stale","quantity":"1"}'
    )

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity="wms.inventory.confirm_inbound@v1",
                idempotency_key="idem-stale-001",
                timestamp="1721865600",
            ),
        )

    assert response.status_code == 401
    assert response.json() == {"code": "SIGNATURE_TIMESTAMP_OUT_OF_WINDOW"}


def test_wms_mock_northbound_submit_rejects_captured_request_after_idempotency_expiry() -> None:
    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = "idem-captured-replay-001"
    path = ASYNC_OPERATION_PATH
    body = _async_body()
    headers = _submit_headers(
        body=body,
        path=path,
        operation_identity=operation_identity,
        idempotency_key=idempotency_key,
    )
    accepted_at = datetime(2026, 7, 25, tzinfo=UTC)
    wms_mock_server.northbound_clock_state["now"] = accepted_at

    with TestClient(wms_mock_server.app) as client:
        first = client.post(path, content=body, headers=headers)
        wms_mock_server.northbound_clock_state["now"] = accepted_at + timedelta(seconds=9)
        replay = client.post(path, content=body, headers=headers)
        effects = client.get(
            "/debug/northbound/effects",
            params={"operation_identity": operation_identity, "idempotency_key": idempotency_key},
        )

    assert first.status_code == 202
    assert replay.status_code == 401
    assert replay.json() == {"code": "HMAC_NONCE_REPLAYED"}
    assert effects.json()["effect_count"] == 1


def test_wms_mock_northbound_status_rejects_reused_nonce() -> None:
    status_path = "/northbound/operations/status?" + urlencode(
        {
            "operation_identity": ASYNC_OPERATION_IDENTITY,
            "idempotency_key": "idem-status-replay-001",
        }
    )
    headers = _status_headers(path=status_path)

    with TestClient(wms_mock_server.app) as client:
        first = client.get(status_path, headers=headers)
        replay = client.get(status_path, headers=headers)

    assert first.status_code == 200
    assert replay.status_code == 401
    assert replay.json() == {"code": "HMAC_NONCE_REPLAYED"}


def test_wms_mock_northbound_rejects_nonce_reused_across_submit_and_status() -> None:
    nonce = "shared-submit-status-nonce"
    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = "idem-cross-channel-replay-001"
    submit_path = ASYNC_OPERATION_PATH
    status_path = "/northbound/operations/status?" + urlencode(
        {
            "operation_identity": operation_identity,
            "idempotency_key": idempotency_key,
        }
    )
    body = _async_body()

    with TestClient(wms_mock_server.app) as client:
        accepted = client.post(
            submit_path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=submit_path,
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
                nonce=nonce,
            ),
        )
        replay = client.get(status_path, headers=_status_headers(path=status_path, nonce=nonce))

    assert accepted.status_code == 202
    assert replay.status_code == 401
    assert replay.json() == {"code": "HMAC_NONCE_REPLAYED"}


def test_typed_submit_rejects_whitespace_variant_before_idempotency_write() -> None:
    path = ASYNC_OPERATION_PATH
    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = "idem-authenticated-scope-001"
    body = _async_body()
    clean_headers = _submit_headers(
        body=body,
        path=path,
        operation_identity=operation_identity,
        idempotency_key=idempotency_key,
    )
    whitespace_headers = {**clean_headers, "Idempotency-Key": f"  {idempotency_key}  "}

    with TestClient(wms_mock_server.app) as client:
        whitespace = client.post(path, content=body, headers=whitespace_headers)
        clean = client.post(path, content=body, headers=clean_headers)
        clean_effects = client.get(
            "/debug/northbound/effects",
            params={"operation_identity": operation_identity, "idempotency_key": idempotency_key},
        )
        whitespace_effects = client.get(
            "/debug/northbound/effects",
            params={"operation_identity": operation_identity, "idempotency_key": f"  {idempotency_key}  "},
        )

    assert whitespace.status_code == 401
    assert whitespace.json() == {"code": "MISSING_OR_INVALID_AUTH_HEADER"}
    assert clean.status_code == 202
    assert clean_effects.json()["effect_count"] == 1
    assert whitespace_effects.json()["effect_count"] == 0


def test_status_query_rejects_self_consistently_signed_non_empty_get_body() -> None:
    body = b"unexpected"
    status_path = "/northbound/operations/status?" + urlencode(
        {
            "operation_identity": ASYNC_OPERATION_IDENTITY,
            "idempotency_key": "idem-non-empty-status-body-001",
        }
    )

    with TestClient(wms_mock_server.app) as client:
        response = client.request(
            "GET",
            status_path,
            content=body,
            headers=_status_headers(path=status_path, body=body),
        )

    assert response.status_code == 401
    assert response.json() == {"code": "CONTENT_HASH_MISMATCH"}


@pytest.mark.asyncio
async def test_status_query_handles_client_disconnect_without_raising() -> None:
    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/northbound/operations/status",
            "query_string": (
                b"operation_identity=wms.fulfillment.request_rack_supply%40v1&idempotency_key=idem-disconnected-status-001"
            ),
            "headers": [],
        },
        receive=receive,
    )

    response = await wms_mock_server.northbound_operation_status(
        request,
        operation_identity=ASYNC_OPERATION_IDENTITY,
        idempotency_key="idem-disconnected-status-001",
    )

    assert response.status_code == 499


def test_typed_submit_replay_uses_canonical_json_fingerprint() -> None:
    path = ASYNC_OPERATION_PATH
    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = "idem-canonical-replay-001"
    canonical_body = _async_body()
    equivalent_body = json.dumps(_async_payload(), indent=2, sort_keys=True).encode()

    with TestClient(wms_mock_server.app) as client:
        first = client.post(
            path,
            content=canonical_body,
            headers=_submit_headers(
                body=canonical_body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
            ),
        )
        replay = client.post(
            path,
            content=equivalent_body,
            headers=_submit_headers(
                body=equivalent_body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
            ),
        )
        effects = client.get(
            "/debug/northbound/effects",
            params={"operation_identity": operation_identity, "idempotency_key": idempotency_key},
        )

    assert first.status_code == 202
    assert replay.status_code == 409
    assert replay.json()["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    assert effects.json()["effect_count"] == 1


@pytest.mark.parametrize(
    ("body", "content_type"),
    (
        (b"[]", "application/json"),
        (b'"scalar"', "application/json"),
        (b"{", "application/json"),
        (
            b'{"dispatch_key":"dispatch-content-type","package_id":"package-content-type",'
            b'"pallet_id":"pallet-content-type","station_code":"station-content-type"}',
            "text/plain",
        ),
    ),
)
def test_typed_submit_rejects_non_object_malformed_or_wrong_content_type_with_fixed_error(
    body: bytes,
    content_type: str,
) -> None:
    path = ASYNC_OPERATION_PATH
    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = f"idem-invalid-json-{hashlib.sha256(body + content_type.encode()).hexdigest()[:12]}"
    headers = _submit_headers(
        body=body,
        path=path,
        operation_identity=operation_identity,
        idempotency_key=idempotency_key,
    )
    headers["Content-Type"] = content_type

    with TestClient(wms_mock_server.app) as client:
        response = client.post(path, content=body, headers=headers)
        effects = client.get(
            "/debug/northbound/effects",
            params={"operation_identity": operation_identity, "idempotency_key": idempotency_key},
        )

    assert response.status_code == 422
    assert response.json() == {"code": "INVALID_TYPED_REQUEST"}
    assert effects.json()["effect_count"] == 0


def test_streaming_fault_response_does_not_allocate_the_declared_body_up_front() -> None:
    fault = wms_mock_server._NorthboundFault(
        status=503,
        target_path="/northbound/contract",
        method="GET",
        operation_identity=None,
        retry_after=None,
        delay=0,
        after_response=False,
        not_found=False,
        max_response_bytes=None,
        response_body_bytes=4 * 1024 * 1024,
    )

    tracemalloc.start()
    try:
        response = wms_mock_server._northbound_fault_response(fault)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert isinstance(response, StreamingResponse)
    assert peak_bytes < 256 * 1024


def test_wms_mock_northbound_status_exposes_not_found_and_rejected_states() -> None:
    operation_identity = ASYNC_OPERATION_IDENTITY
    status_path = "/northbound/operations/status?" + urlencode(
        {"operation_identity": operation_identity, "idempotency_key": "idem-rejected-001"}
    )
    path = ASYNC_OPERATION_PATH
    body = _async_body()

    with TestClient(wms_mock_server.app) as client:
        not_found = client.get(status_path, headers=_status_headers(path=status_path))
        submit = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body, path=path, operation_identity=operation_identity, idempotency_key="idem-rejected-001"
            ),
        )
        rejected = client.post(
            "/debug/northbound/reject",
            json={
                "operation_identity": operation_identity,
                "idempotency_key": "idem-rejected-001",
                "reason_code": "NO_RACK_AVAILABLE",
            },
        )
        status = client.get(status_path, headers=_status_headers(path=status_path))

    assert not_found.json() == {
        "state": "NOT_FOUND",
        "provider_reference": None,
        "reason_code": None,
        "updated_at": None,
        "source_version": None,
        "result_payload": None,
    }
    assert submit.status_code == 202
    assert rejected.status_code == 200
    assert status.json()["state"] == "REJECTED"
    assert status.json()["reason_code"] == "NO_RACK_AVAILABLE"
    assert status.json()["result_payload"] is None


def test_wms_mock_northbound_status_hmac_binds_exact_raw_query_path() -> None:
    signed_path = (
        "/northbound/operations/status?operation_identity=wms.fulfillment.request_rack_supply%40v1"
        "&idempotency_key=idem-raw-query-001"
    )
    semantically_equivalent_path = (
        "/northbound/operations/status?idempotency_key=idem-raw-query-001"
        "&operation_identity=wms.fulfillment.request_rack_supply@v1"
    )
    headers = _status_headers(path=signed_path)

    with TestClient(wms_mock_server.app) as client:
        mismatched = client.get(semantically_equivalent_path, headers=headers)
        exact = client.get(signed_path, headers=headers)

    assert mismatched.status_code == 401
    assert mismatched.json()["code"] == "INVALID_HMAC_SIGNATURE"
    assert exact.status_code == 200
    assert exact.json()["state"] == "NOT_FOUND"


def test_wms_mock_northbound_contract_faults_and_reset_are_publicly_controllable() -> None:
    operation_identity = ASYNC_OPERATION_IDENTITY
    path = ASYNC_OPERATION_PATH
    body = _async_body()
    headers = _submit_headers(
        body=body, path=path, operation_identity=operation_identity, idempotency_key="idem-reset-001"
    )

    with TestClient(wms_mock_server.app) as client:
        contract = client.get("/northbound/contract")
        configured_fault = client.post(
            "/debug/northbound/faults",
            json={
                "status": 429,
                "target_path": "/northbound/contract",
                "method": "GET",
                "retry_after": 3,
                "delay": 0.01,
                "max_response_bytes": 32,
            },
        )
        throttled = client.get("/northbound/contract")
        configured_5xx = client.post(
            "/debug/northbound/faults",
            json={"status": 503, "target_path": "/northbound/contract", "method": "GET"},
        )
        unavailable = client.get("/northbound/contract")
        configured_delay = client.post(
            "/debug/northbound/faults",
            json={
                "status": 200,
                "target_path": "/northbound/contract",
                "method": "GET",
                "delay": 0.02,
            },
        )
        started_at = monotonic()
        delayed = client.get("/northbound/contract")
        elapsed = monotonic() - started_at
        status_path = "/northbound/operations/status?" + urlencode(
            {"operation_identity": operation_identity, "idempotency_key": "idem-reset-001"}
        )
        invalid_status_hmac = client.get(status_path, headers={"X-WMS-Signature": "invalid"})
        clock = client.post("/debug/northbound/clock", json={"now": "2026-07-25T00:00:00+00:00"})
        first = client.post(path, content=body, headers=headers)
        reset = client.post("/debug/reset")
        resubmitted = client.post(path, content=body, headers=headers)

    assert contract.status_code == 200
    assert contract.json()["credential_reference"] == ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE
    assert {"idempotency_retention_seconds", "status_visibility_sla_seconds", "max_response_bytes"} <= set(
        contract.json()
    )
    assert configured_fault.status_code == 200
    assert throttled.status_code == 429
    assert throttled.headers["Retry-After"] == "3"
    assert len(throttled.content) <= 32
    assert configured_5xx.status_code == 200
    assert unavailable.status_code == 503
    assert configured_delay.status_code == 200
    assert delayed.status_code == 200
    assert elapsed >= 0.015
    assert invalid_status_hmac.status_code == 401
    assert clock.json()["data"]["now"] == "2026-07-25T00:00:00+00:00"
    assert first.status_code == 202
    assert reset.status_code == 200
    assert resubmitted.status_code == 202


def test_wms_mock_northbound_visibility_callback_evidence_and_large_body_are_publicly_controllable() -> None:
    """验收控制面只暴露脱敏投影，状态推进和效果计数仍通过公开 HTTP 观察。"""

    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = "idem-public-evidence-001"
    path = ASYNC_OPERATION_PATH
    body = _async_body()
    headers = _submit_headers(
        body=body,
        path=path,
        operation_identity=operation_identity,
        idempotency_key=idempotency_key,
    )
    status_path = "/northbound/operations/status?" + urlencode(
        {"operation_identity": operation_identity, "idempotency_key": idempotency_key}
    )

    with TestClient(wms_mock_server.app) as client:
        contract = client.get("/northbound/contract")
        client.post("/debug/northbound/clock", json={"now": "2026-07-25T00:00:00+00:00"})
        configured_visibility = client.post(
            "/debug/northbound/visibility",
            json={
                "operation_identity": operation_identity,
                "idempotency_key": idempotency_key,
                "delay_seconds": 1,
            },
        )
        first = client.post(path, content=body, headers=headers)
        hidden = client.get(status_path, headers=_status_headers(path=status_path))
        client.post("/debug/northbound/clock", json={"now": "2026-07-25T00:00:01+00:00"})
        visible = client.get(status_path, headers=_status_headers(path=status_path))
        replay = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
            ),
        )
        effects = client.get(
            "/debug/northbound/effects?"
            + urlencode({"operation_identity": operation_identity, "idempotency_key": idempotency_key})
        )
        hints = client.get(
            "/debug/northbound/callback-hints?"
            + urlencode({"operation_identity": operation_identity, "idempotency_key": idempotency_key})
        )
        configured_large_body = client.post(
            "/debug/northbound/faults",
            json={
                "status": 503,
                "target_path": "/northbound/contract",
                "method": "GET",
                "response_body_bytes": contract.json()["max_response_bytes"] + 1,
            },
        )
        oversized = client.get("/northbound/contract")
        reset = client.post("/debug/reset")
        hints_after_reset = client.get(
            "/debug/northbound/callback-hints?"
            + urlencode({"operation_identity": operation_identity, "idempotency_key": idempotency_key})
        )
        resubmitted_after_reset = client.post(path, content=body, headers=headers)
        visible_after_reset = client.get(status_path, headers=_status_headers(path=status_path))

    assert configured_visibility.status_code == 200
    assert first.status_code == 202
    assert hidden.json()["state"] == "NOT_FOUND"
    assert hidden.json()["source_version"] is None
    assert visible.json()["state"] == "ACCEPTED"
    assert replay.status_code == 409
    assert effects.json()["effect_count"] == 1
    assert hints.json() == {
        "hints": [
            {
                "callback_type": "WMS_EFFECT_STATUS_HINT",
                "dispatch_key": _async_payload()["dispatch_key"],
                "idempotency_key": idempotency_key,
                "operation_identity": operation_identity,
            }
        ]
    }
    assert configured_large_body.status_code == 200
    assert oversized.status_code == 503
    assert len(oversized.content) > contract.json()["max_response_bytes"]
    assert reset.status_code == 200
    assert hints_after_reset.json() == {"hints": []}
    assert resubmitted_after_reset.status_code == 202
    assert visible_after_reset.json()["state"] == "ACCEPTED"


def test_wms_mock_active_bin_rack_builder_does_not_read_or_mutate_state() -> None:
    removed_rack = wms_mock_server.mock_wms_state.rack_pool.pop("RACK-6CELL-001")

    payload = wms_mock_server.build_active_bin_rack_payload("RACK-6CELL-001")

    assert payload["active_bin_rack"]["rack_code"] == "RACK-6CELL-001"
    assert {cell["bin_type"] for cell in payload["active_bin_rack"]["cells"]} == {"6格箱"}
    assert "RACK-6CELL-001" not in wms_mock_server.mock_wms_state.rack_pool
    wms_mock_server.mock_wms_state.rack_pool["RACK-6CELL-001"] = removed_rack


def test_wms_mock_inventory_query_matches_known_sku_and_lot_no() -> None:
    payload_data = rough_sorter_scan_completed_payload()["data"]
    inventory = mock_wms_inventory_seed()[("CAP001", "LOT-A")]
    with TestClient(wms_mock_server.app) as client:
        response = _inventory_query_request(
            client,
            material_code=payload_data["HHPN"],
            lot_no=payload_data["LotCode"],
        )

    assert response.status_code == 200
    assert response.json() == {
        "items": [_typed_inventory_record(inventory)],
        "next_cursor": None,
        "source_version": "mock-inventory-v1",
    }


def test_wms_mock_inventory_query_returns_rough_sorter_dimensions_for_canonical_material_id() -> None:
    inventory = mock_wms_inventory_seed()[("CAP001", "LOT-A")]
    with TestClient(wms_mock_server.app) as client:
        response = _inventory_query_request(
            client,
            material_code="CAP001",
            lot_no="LOT-A",
            warehouse_code="WH-IT",
            owner_code="OWNER-IT",
        )

    assert response.status_code == 200
    assert response.json()["items"] == [_typed_inventory_record(inventory)]
    assert response.json()["source_version"] == "mock-inventory-v1"
    assert inventory["warehouse_code"] == "WH-IT"
    assert inventory["owner_code"] == "OWNER-IT"


def test_wms_mock_inventory_seed_limits_rough_sorter_dimensions_to_canonical_product() -> None:
    inventory = mock_wms_inventory_seed()

    assert inventory[("CAP001", "LOT-A")]["warehouse_code"] == "WH-IT"
    assert inventory[("CAP001", "LOT-A")]["owner_code"] == "OWNER-IT"
    assert "warehouse_code" not in inventory[("RES001", "LOT-R")]
    assert "owner_code" not in inventory[("RES001", "LOT-R")]


def test_wms_mock_inventory_query_matches_additional_catalog_products() -> None:
    inventory = mock_wms_inventory_seed()
    with TestClient(wms_mock_server.app) as client:
        resistor_response = _inventory_query_request(
            client,
            material_code="RES001",
            lot_no="LOT-R",
        )
        ic_response = _inventory_query_request(
            client,
            material_code="IC001",
            lot_no="LOT-I",
        )

    assert resistor_response.status_code == 200
    assert resistor_response.json()["items"] == [_typed_inventory_record(inventory[("RES001", "LOT-R")])]
    assert ic_response.status_code == 200
    assert ic_response.json()["items"] == [_typed_inventory_record(inventory[("IC001", "LOT-I")])]


def test_wms_mock_inventory_query_returns_empty_items_for_unknown_sku_or_lot_no() -> None:
    payload_data = rough_sorter_scan_completed_payload()["data"]
    with TestClient(wms_mock_server.app) as client:
        unknown_sku_response = _inventory_query_request(
            client,
            material_code="UNKNOWN",
            lot_no=payload_data["LotCode"],
        )
        unknown_lot_response = _inventory_query_request(
            client,
            material_code=payload_data["HHPN"],
            lot_no="UNKNOWN",
        )

    assert unknown_sku_response.status_code == 200
    assert unknown_sku_response.json()["items"] == []
    assert unknown_lot_response.status_code == 200
    assert unknown_lot_response.json()["items"] == []


@pytest.mark.parametrize(
    ("warehouse_code", "owner_code"),
    [
        ("WH-WRONG", "OWNER-IT"),
        ("WH-IT", "OWNER-WRONG"),
    ],
)
def test_wms_mock_inventory_query_filters_all_requested_dimensions(
    warehouse_code: str,
    owner_code: str,
) -> None:
    with TestClient(wms_mock_server.app) as client:
        response = _inventory_query_request(
            client,
            material_code="CAP001",
            lot_no="LOT-A",
            warehouse_code=warehouse_code,
            owner_code=owner_code,
        )

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "next_cursor": None,
        "source_version": "mock-inventory-v1",
    }


def test_wms_mock_inventory_query_hmac_fails_closed_without_leaking_secret() -> None:
    params = {
        "material_code": "CAP001",
        "lot_no": "LOT-A",
        "warehouse_code": "WH-IT",
        "owner_code": "OWNER-IT",
    }
    raw_path = "/api/wms/inventory/query?" + urlencode(tuple(params.items()))
    invalid_headers = _status_headers(path=raw_path)
    invalid_headers["X-WMS-Signature"] = "0" * 64
    with TestClient(wms_mock_server.app) as client:
        unsigned = client.get("/api/wms/inventory/query", params=params)
        invalid_secret = client.get("/api/wms/inventory/query", params=params, headers=invalid_headers)
        tampered = client.get(
            "/api/wms/inventory/query",
            params={**params, "owner_code": "OWNER-TAMPERED"},
            headers=_status_headers(path=raw_path),
        )

    assert unsigned.status_code == 401
    assert unsigned.json() == {"code": "MISSING_OR_INVALID_AUTH_HEADER"}
    assert invalid_secret.status_code == 401
    assert invalid_secret.json() == {"code": "INVALID_HMAC_SIGNATURE"}
    assert tampered.status_code == 401
    assert tampered.json() == {"code": "INVALID_HMAC_SIGNATURE"}
    response_text = f"{unsigned.text}{invalid_secret.text}{tampered.text}"
    assert os.environ[MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2] not in response_text


def test_wms_mock_inventory_query_rejects_legacy_post_and_sku_alias() -> None:
    sku_alias_path = "/api/wms/inventory/query?" + urlencode({"sku": "CAP001", "lot_no": "LOT-A"})
    with TestClient(wms_mock_server.app) as client:
        post_response = client.post(
            "/api/wms/inventory/query",
            json={"material_id": "CAP001", "lot_no": "LOT-A"},
        )
        sku_alias_response = client.get(
            sku_alias_path,
            headers=_status_headers(path=sku_alias_path),
        )

    assert post_response.status_code == 405
    assert sku_alias_response.status_code == 422


def test_wms_mock_typed_effect_first_acceptances_use_unique_callback_event_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    path = ASYNC_OPERATION_PATH
    operation_identity = ASYNC_OPERATION_IDENTITY
    payload = _async_payload()
    body = json.dumps(payload, separators=(",", ":")).encode()

    with TestClient(wms_mock_server.app) as client:
        for idempotency_key in ("idem-mock-001", "idem-mock-002"):
            response = client.post(
                path,
                content=body,
                headers=_submit_headers(
                    body=body,
                    path=path,
                    operation_identity=operation_identity,
                    idempotency_key=idempotency_key,
                ),
            )
            assert response.status_code == 202

        reset = client.post("/debug/reset")
        assert reset.status_code == 200
        restored = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key="idem-mock-001",
            ),
        )
        assert restored.status_code == 202

    source_event_ids = {call.args[1]["source_event_id"] for call in mock_post_callback.await_args_list}
    assert mock_post_callback.await_count == 3
    assert len(source_event_ids) == 3


@pytest.mark.asyncio
async def test_wms_mock_callback_sender_uses_authenticated_canonical_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200
        text = "ok"

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> _Response:
            captured.update(url=url, content=content, headers=headers)
            return _Response()

    monkeypatch.setattr(wms_mock_server, "CALLBACK_API_APP_ID", "app-wms-mock")
    monkeypatch.setattr(wms_mock_server, "CALLBACK_API_APP_SECRET", "secret-wms-mock")
    monkeypatch.setattr(wms_mock_server.httpx, "AsyncClient", lambda **_kwargs: _Client())
    payload = {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "data": {
            "operation_identity": "wms.inventory.confirm_inbound@v1",
            "idempotency_key": "idem-001",
            "dispatch_key": "dispatch-001",
        },
    }

    result = await wms_mock_server._post_callback(
        "http://api:8001/api/v1/callback/external",
        payload,
    )

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert result == {"delivered": True, "status_code": 200, "response_text": "ok"}
    assert captured["content"] == body
    assert headers["X-App-ID"] == "app-wms-mock"
    assert headers["X-Body-SHA256"] == wms_mock_server.hashlib.sha256(body).hexdigest()
    assert headers["X-Signature"] == calculate_body_hmac_signature(
        app_secret="secret-wms-mock",
        method="POST",
        path="/api/v1/callback/external",
        timestamp=headers["X-Timestamp"],
        nonce=headers["X-Nonce"],
        body_sha256=headers["X-Body-SHA256"],
        app_id=headers["X-App-ID"],
    )


def test_wms_mock_full_box_northbound_submit_never_sends_legacy_completion_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = dict(REQUEST_FIXTURES["wms.fulfillment.full_box_exchange@v1"])
    path = "/api/wms/fulfillment/full-box-exchange"
    body = json.dumps(payload, separators=(",", ":")).encode()

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity="wms.fulfillment.full_box_exchange@v1",
                idempotency_key="idem-full-box-legacy-001",
            ),
        )

    assert response.status_code == 202
    mock_post_callback.assert_awaited_once()
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_EFFECT_STATUS_HINT"
    assert callback_payload["data"] == {
        "operation_identity": "wms.fulfillment.full_box_exchange@v1",
        "idempotency_key": "idem-full-box-legacy-001",
        "dispatch_key": payload["dispatch_key"],
    }


def test_real_wes_submit_sender_and_status_signer_interoperate_with_mock_active_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """集成证据必须复用真实 WES sender/signer，不能由探针自创 credential reference。"""

    operation_identity = ASYNC_OPERATION_IDENTITY
    credential_reference = "secret://wms/material-flow-sandbox-hmac@v2"
    secret = b"real-wes-material-flow-v2"
    submit_path = ASYNC_OPERATION_PATH
    idempotency_key = "idem-real-wes-signer-001"
    payload = _async_payload()
    profile = ExternalHttpProviderProfileDefinition(
        identity="wms.material-flow.v1.sandbox",
        environment="sandbox",
        bindings=(
            ExternalHttpBindingDefinition(
                operation_identity=operation_identity,
                allowed_target_codes=("WMS_NOTIFY_PACKAGE_BINDING",),
                http_method="POST",
                timeout_seconds=2,
                auth_scheme="HMAC_SHA256",
                credential_reference=credential_reference,
            ),
        ),
    )
    binding = freeze_external_http_binding(
        profile=profile,
        operation_identity=operation_identity,
        target_code="WMS_NOTIFY_PACKAGE_BINDING",
        endpoint_registry=EndpointRegistry({"WMS_NOTIFY_PACKAGE_BINDING": f"http://testserver{submit_path}"}),
    )
    canonical = CanonicalPayload.from_projection(payload)
    outbound = ExternalHttpDispatchRequest.from_persisted(
        binding=binding,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        secret=secret,
        timestamp=datetime.now(UTC).isoformat(),
        nonce="real-wes-submit-nonce",
        idempotency_key=idempotency_key,
    )
    monkeypatch.setenv("WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2", secret.decode())

    with TestClient(wms_mock_server.app) as client:
        submitted = client.post(submit_path, content=outbound.body, headers=outbound.headers)
        status_path = "/northbound/operations/status?" + urlencode(
            (("operation_identity", operation_identity), ("idempotency_key", idempotency_key))
        )
        status_request = httpx.Request("GET", f"http://testserver{status_path}")
        sign_wms_hmac_request(
            status_request,
            credential_reference=credential_reference,
            auth_scheme="HMAC_SHA256",
            secret=secret,
            now=lambda: datetime.now(UTC),
            nonce_factory=lambda: "real-wes-status-nonce",
        )
        status = client.get(status_path, headers=status_request.headers)

    assert submitted.status_code == 202
    assert status.status_code == 200
    assert status.json()["state"] == "ACCEPTED"


@pytest.mark.parametrize(
    ("case_id", "path", "operation_identity", "valid_payload", "invalid_payload"),
    (
        (
            "confirm-missing",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            *_wire_fixture_case("wms.inventory.confirm_inbound@v1", remove="quantity"),
        ),
        (
            "confirm-extra",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            *_wire_fixture_case(
                "wms.inventory.confirm_inbound@v1",
                update={"trace_id": "forbidden-wire-field"},
            ),
        ),
        (
            "confirm-blank",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            *_wire_fixture_case("wms.inventory.confirm_inbound@v1", update={"inbound_key": " "}),
        ),
        (
            "confirm-non-string-quantity",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            *_wire_fixture_case("wms.inventory.confirm_inbound@v1", update={"quantity": []}),
        ),
        (
            "confirm-non-finite-quantity",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            *_wire_fixture_case("wms.inventory.confirm_inbound@v1", update={"quantity": "NaN"}),
        ),
        (
            "confirm-non-positive-quantity",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            *_wire_fixture_case("wms.inventory.confirm_inbound@v1", update={"quantity": "0"}),
        ),
        (
            "full-box-extra",
            "/api/wms/fulfillment/full-box-exchange",
            "wms.fulfillment.full_box_exchange@v1",
            *_wire_fixture_case(
                "wms.fulfillment.full_box_exchange@v1",
                update={"provider_code": "WMS"},
            ),
        ),
        (
            "package-binding-missing",
            "/api/wms/fulfillment/pkg-bindings",
            "wms.fulfillment.notify_pkg_binding@v1",
            *_wire_fixture_case("wms.fulfillment.notify_pkg_binding@v1", remove="station_code"),
        ),
    ),
)
def test_typed_submit_rejects_invalid_wire_body_before_idempotency_write(
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    path: str,
    operation_identity: str,
    valid_payload: dict[str, object],
    invalid_payload: dict[str, object],
) -> None:
    monkeypatch.setattr(wms_mock_server, "_post_callback", AsyncMock(return_value={"delivered": True}))
    idempotency_key = f"idem-{case_id}"
    invalid_body = json.dumps(invalid_payload, separators=(",", ":")).encode()
    valid_body = json.dumps(valid_payload, separators=(",", ":")).encode()

    with TestClient(wms_mock_server.app) as client:
        rejected = client.post(
            path,
            content=invalid_body,
            headers=_submit_headers(
                body=invalid_body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
            ),
        )
        effects_before_valid = client.get(
            "/debug/northbound/effects",
            params={"operation_identity": operation_identity, "idempotency_key": idempotency_key},
        )
        accepted = client.post(
            path,
            content=valid_body,
            headers=_submit_headers(
                body=valid_body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
            ),
        )

    assert rejected.status_code == 422
    assert rejected.json() == {"code": "INVALID_TYPED_REQUEST"}
    assert effects_before_valid.json()["effect_count"] == 0
    expected_status = (
        202 if WMS_OPERATION_BY_IDENTITY[operation_identity].completion_mode is WmsCompletionMode.ASYNC_TASK else 200
    )
    assert accepted.status_code == expected_status


def test_public_clock_drives_visibility_and_retention_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wms_mock_server, "_post_callback", AsyncMock(return_value={"delivered": True}))
    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = "idem-public-clock-boundary-001"
    path = ASYNC_OPERATION_PATH
    payload = _async_payload()
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = _submit_headers(
        body=body,
        path=path,
        operation_identity=operation_identity,
        idempotency_key=idempotency_key,
    )
    status_path = "/northbound/operations/status?" + urlencode(
        (("operation_identity", operation_identity), ("idempotency_key", idempotency_key))
    )

    with TestClient(wms_mock_server.app) as client:
        contract = client.get("/northbound/contract").json()
        accepted_at = "2026-07-25T00:00:00+00:00"
        client.post("/debug/northbound/clock", json={"now": accepted_at})
        visibility = client.post(
            "/debug/northbound/visibility",
            json={
                "operation_identity": operation_identity,
                "idempotency_key": idempotency_key,
                "delay_seconds": contract["status_visibility_sla_seconds"],
            },
        )
        first = client.post(path, content=body, headers=headers)
        hidden_at_accept = client.get(status_path, headers=_status_headers(path=status_path))
        client.post("/debug/northbound/clock", json={"now": "2026-07-25T00:00:01+00:00"})
        hidden_before_sla = client.get(status_path, headers=_status_headers(path=status_path))
        client.post("/debug/northbound/clock", json={"now": "2026-07-25T00:00:02+00:00"})
        visible_at_sla = client.get(status_path, headers=_status_headers(path=status_path))
        client.post("/debug/northbound/clock", json={"now": "2026-07-25T00:00:08+00:00"})
        replay_before_boundary = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
            ),
        )
        client.post("/debug/northbound/clock", json={"now": "2026-07-25T00:00:09+00:00"})
        expired_at_boundary = client.get(status_path, headers=_status_headers(path=status_path))
        recovered_at_boundary = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
            ),
        )
        effects = client.get(
            "/debug/northbound/effects",
            params={"operation_identity": operation_identity, "idempotency_key": idempotency_key},
        )

    assert visibility.status_code == 200
    assert first.status_code == 202
    assert hidden_at_accept.json()["state"] == "NOT_FOUND"
    assert hidden_before_sla.json()["state"] == "NOT_FOUND"
    assert visible_at_sla.json()["state"] == "ACCEPTED"
    assert replay_before_boundary.status_code == 409
    assert expired_at_boundary.json()["state"] == "NOT_FOUND"
    assert recovered_at_boundary.status_code == 202
    assert effects.json()["effect_count"] == 2


def test_visible_then_lost_is_an_independent_one_shot_status_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wms_mock_server, "_post_callback", AsyncMock(return_value={"delivered": True}))
    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = "idem-visible-then-lost-001"
    path = ASYNC_OPERATION_PATH
    payload = _async_payload()
    body = json.dumps(payload, separators=(",", ":")).encode()
    status_path = "/northbound/operations/status?" + urlencode(
        (("operation_identity", operation_identity), ("idempotency_key", idempotency_key))
    )

    with TestClient(wms_mock_server.app) as client:
        first = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
            ),
        )
        visible = client.get(status_path, headers=_status_headers(path=status_path))
        configured = client.post(
            "/debug/northbound/faults",
            json={
                "status": 200,
                "target_path": "/northbound/operations/status",
                "method": "GET",
                "operation_identity": operation_identity,
                "not_found": True,
            },
        )
        lost = client.get(status_path, headers=_status_headers(path=status_path))
        visible_again = client.get(status_path, headers=_status_headers(path=status_path))

    assert first.status_code == 202
    assert visible.json()["state"] == "ACCEPTED"
    assert configured.status_code == 200
    assert lost.json()["state"] == "NOT_FOUND"
    assert visible_again.json()["state"] == "PROCESSING"


def test_oversized_fault_response_is_streamed_as_an_independent_budget_case() -> None:
    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = "idem-streamed-oversized-001"
    status_path = "/northbound/operations/status?" + urlencode(
        (("operation_identity", operation_identity), ("idempotency_key", idempotency_key))
    )

    with TestClient(wms_mock_server.app) as client:
        max_response_bytes = client.get("/northbound/contract").json()["max_response_bytes"]
        configured = client.post(
            "/debug/northbound/faults",
            json={
                "status": 503,
                "target_path": "/northbound/operations/status",
                "method": "GET",
                "operation_identity": operation_identity,
                "response_body_bytes": max_response_bytes + 1,
            },
        )
        oversized = client.get(status_path, headers=_status_headers(path=status_path))

    assert configured.status_code == 200
    assert oversized.status_code == 503
    assert "content-length" not in oversized.headers
    assert len(oversized.content) > max_response_bytes


def test_sync_operation_status_rejected_before_matching_fault_is_consumed() -> None:
    """E03 等同步 operation 不属于 E08–E14 status 合同，fault 不能覆盖该拒绝。"""

    operation_identity = "wms.inventory.reserve_inventory@v1"
    idempotency_key = "idem-sync-status-rejected-001"
    status_path = "/northbound/operations/status?" + urlencode(
        (("operation_identity", operation_identity), ("idempotency_key", idempotency_key))
    )

    with TestClient(wms_mock_server.app) as client:
        configured = client.post(
            "/debug/northbound/faults",
            json={
                "status": 503,
                "target_path": "/northbound/operations/status",
                "method": "GET",
                "operation_identity": operation_identity,
            },
        )
        rejected = client.get(status_path, headers=_status_headers(path=status_path))

    assert configured.status_code == 200
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "STATUS_OPERATION_NOT_ASYNC_EFFECT"
    assert wms_mock_server.northbound_fault_state["next"] is not None


@pytest.mark.asyncio
async def test_concurrent_identical_http_replay_creates_exactly_one_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wms_mock_server, "_post_callback", AsyncMock(return_value={"delivered": True}))
    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = "idem-concurrent-replay-001"
    path = ASYNC_OPERATION_PATH
    payload = _async_payload()
    body = json.dumps(payload, separators=(",", ":")).encode()
    request_headers = [
        _submit_headers(
            body=body,
            path=path,
            operation_identity=operation_identity,
            idempotency_key=idempotency_key,
        )
        for _ in range(8)
    ]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wms_mock_server.app),
        base_url="http://mock-wms.test",
    ) as client:
        responses = await asyncio.gather(
            *(client.post(path, content=body, headers=headers) for headers in request_headers)
        )
        effects = await client.get(
            "/debug/northbound/effects",
            params={"operation_identity": operation_identity, "idempotency_key": idempotency_key},
        )

    assert [response.status_code for response in responses].count(202) == 1
    assert [response.status_code for response in responses].count(409) == 7
    assert effects.json()["effect_count"] == 1


@pytest.mark.asyncio
async def test_concurrent_matching_requests_atomically_claim_one_shot_fault() -> None:
    operation_identity = ASYNC_OPERATION_IDENTITY
    idempotency_key = "idem-concurrent-fault-claim-001"
    status_path = "/northbound/operations/status?" + urlencode(
        (("operation_identity", operation_identity), ("idempotency_key", idempotency_key))
    )
    headers = _status_headers(path=status_path)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wms_mock_server.app),
        base_url="http://mock-wms.test",
    ) as client:
        configured = await client.post(
            "/debug/northbound/faults",
            json={
                "status": 503,
                "target_path": "/northbound/operations/status",
                "method": "GET",
                "operation_identity": operation_identity,
                "delay": 0.01,
            },
        )
        first_status, second_status, health, inventory = await asyncio.gather(
            client.get(status_path, headers=headers),
            client.get(status_path, headers=headers),
            client.get("/"),
            client.get("/api/wms/materials/MAT001"),
        )

    assert configured.status_code == 200
    matching_statuses = [first_status, second_status]
    assert [response.status_code for response in matching_statuses].count(503) == 1
    assert [response.status_code for response in matching_statuses].count(200) == 1
    assert next(response for response in matching_statuses if response.status_code == 503).json() == {
        "code": "TEMPORARILY_UNAVAILABLE"
    }
    assert health.status_code == 200
    assert inventory.status_code in {200, 404}


def test_wms_mock_recent_operations_keeps_bounded_history() -> None:
    for index in range(wms_mock_server.RECENT_OPERATION_LIMIT + 5):
        wms_mock_server.mock_wms_state._record_operation({"operation_key": f"op-{index}"})

    recent_operations = wms_mock_server.mock_wms_state.recent_operations
    assert len(recent_operations) == wms_mock_server.RECENT_OPERATION_LIMIT
    assert recent_operations[0]["operation_key"] == "op-5"


def test_wms_mock_debug_rack_status_allows_manual_fault_setup() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/debug/racks/RACK-3CELL-001/status",
            json={"status": "UNAVAILABLE", "current_location": "MAINTENANCE"},
        )

    assert response.status_code == 200
    rack = response.json()["data"]
    assert rack["rack_id"] == "RACK-3CELL-001"
    assert rack["status"] == "UNAVAILABLE"
    assert rack["current_location"] == "MAINTENANCE"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-3CELL-001"]["status"] == "UNAVAILABLE"


def test_wms_mock_rack_query_returns_copy_not_internal_state() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/debug/wms/racks/RACK-001")

    assert response.status_code == 200
    rack_payload = response.json()["data"]
    rack_payload["status"] = "MUTATED_BY_TEST"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "AVAILABLE"


def test_wms_mock_rack_list_returns_copy_not_internal_state() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/debug/wms/racks")

    assert response.status_code == 200
    rack_payload = response.json()["data"][0]
    rack_payload["status"] = "MUTATED_BY_TEST"
    assert wms_mock_server.mock_wms_state.rack_pool[rack_payload["rack_id"]]["status"] == "AVAILABLE"


def test_wms_mock_large_reel_detection_does_not_match_dimension_substrings() -> None:
    assert wms_mock_server._has_large_reel_size({"reel_diameter": "13inch"}) is True
    assert wms_mock_server._has_large_reel_size({"reel_diameter": "330.0"}) is True
    assert wms_mock_server._has_large_reel_size({"reel_diameter": "113mm"}) is False
    assert wms_mock_server._has_large_reel_size({"reel_diameter": "150mm"}) is False


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "0", "-1", "invalid"])
def test_wms_mock_cell_capacity_env_rejects_invalid_values(monkeypatch, raw: str) -> None:
    monkeypatch.setenv("MOCK_WMS_CELL_CAPACITY_DEPTH_MM", raw)

    assert wms_mock_server._positive_float_env("MOCK_WMS_CELL_CAPACITY_DEPTH_MM") is None


def test_wms_mock_cell_capacity_env_accepts_positive_finite_value(monkeypatch) -> None:
    monkeypatch.setenv("MOCK_WMS_CELL_CAPACITY_DEPTH_MM", "30.5")

    assert wms_mock_server._positive_float_env("MOCK_WMS_CELL_CAPACITY_DEPTH_MM") == 30.5
