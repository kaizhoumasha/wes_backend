import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
from time import monotonic
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx
import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture(autouse=True)
def reset_wms_mock_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V1, "test-mock-northbound-secret-v1")
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, "test-mock-northbound-secret-v2")
    wms_mock_server.reset_mock_wms_state()


def _submit_headers(*, body: bytes, path: str, operation_identity: str, idempotency_key: str) -> dict[str, str]:
    timestamp = "1721865600"
    nonce = "submit-nonce"
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


def _status_headers(*, path: str) -> dict[str, str]:
    timestamp = "1721865600"
    nonce = "status-nonce"
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical = canonical_status_string(
        method="GET", path=path, timestamp=timestamp, nonce=nonce, payload_hash=payload_hash
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


def test_wms_mock_loads_shared_catalog_without_importing_runtime_package() -> None:
    source = Path(wms_mock_server.__file__).read_text()

    assert "from src.workline_runtime.sandbox_catalog import" not in source
    assert "spec_from_file_location" in source


def test_wms_mock_release_reservation_matches_typed_port_contract() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.delete("/api/wms/inventory/reserve/RSV-1")

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "data": {
            "reservation_key": "RSV-1",
            "released": True,
        },
    }


def test_wms_mock_locations_route_passes_ruff_safe_variable_path() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/api/wms/locations", params={"zone": "KITTING_AREA"})

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
        response = client.get("/api/wms/racks", params={"type": "SINGLE_LAYER"})

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
    ("path", "operation_identity", "payload", "compatibility_field"),
    (
        (
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            {
                "dispatch_key": "dispatch-confirm",
                "inbound_key": "inbound-001",
                "material_code": "material-001",
                "quantity": "1",
            },
            "inbound_key",
        ),
        (
            "/api/wms/fulfillment/full-box-exchange",
            "wms.fulfillment.full_box_exchange@v1",
            {
                "dispatch_key": "dispatch-exchange",
                "rack_id": "rack-001",
                "empty_box_id": "empty-001",
                "full_box_id": "full-001",
            },
            "rack_id",
        ),
        (
            "/api/wms/fulfillment/package-binding",
            "wms.fulfillment.notify_pkg_binding@v1",
            {
                "dispatch_key": "dispatch-binding",
                "package_id": "package-001",
                "pallet_id": "pallet-001",
                "station_code": "station-001",
            },
            "package_id",
        ),
    ),
)
def test_wms_mock_northbound_submit_is_idempotent_and_sends_one_callback_hint(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    operation_identity: str,
    payload: dict[str, str],
    compatibility_field: str,
) -> None:
    callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", callback)
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = _submit_headers(
        body=body, path=path, operation_identity=operation_identity, idempotency_key="idem-submit-001"
    )

    with TestClient(wms_mock_server.app) as client:
        first = client.post(path, content=body, headers=headers)
        processing_replay = client.post(path, content=body, headers=headers)
        status_path = "/northbound/operations/status?" + urlencode(
            {"operation_identity": operation_identity, "idempotency_key": "idem-submit-001"}
        )
        accepted = client.get(status_path, headers=_status_headers(path=status_path))
        processing = client.get(status_path, headers=_status_headers(path=status_path))
        completed = client.get(status_path, headers=_status_headers(path=status_path))
        completed_replay = client.post(path, content=body, headers=headers)
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
    assert first.json()["data"][compatibility_field] == payload[compatibility_field]
    assert processing_replay.status_code == 409
    assert processing_replay.json()["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    assert [accepted.json()["state"], processing.json()["state"], completed.json()["state"]] == [
        "ACCEPTED",
        "PROCESSING",
        "COMPLETED",
    ]
    assert completed.json()["result_payload"][compatibility_field] == payload[compatibility_field]
    assert completed_replay.status_code == 200
    assert completed_replay.json()["data"]["northbound_status"]["state"] == "COMPLETED"
    assert conflict.status_code == 422
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert effects.json()["effect_count"] == 1
    callback.assert_awaited_once()


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


def test_wms_mock_northbound_status_exposes_not_found_and_rejected_states() -> None:
    operation_identity = "wms.fulfillment.full_box_exchange@v1"
    status_path = "/northbound/operations/status?" + urlencode(
        {"operation_identity": operation_identity, "idempotency_key": "idem-rejected-001"}
    )
    path = "/api/wms/fulfillment/full-box-exchange"
    body = (
        b'{"dispatch_key":"dispatch-rejected","rack_id":"rack-001","empty_box_id":"empty-001","full_box_id":"full-001"}'
    )

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
                "reason_code": "RACK_LOCKED",
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
    assert status.json()["reason_code"] == "RACK_LOCKED"
    assert status.json()["result_payload"] is None


def test_wms_mock_northbound_status_hmac_binds_exact_raw_query_path() -> None:
    signed_path = (
        "/northbound/operations/status?operation_identity=wms.inventory.confirm_inbound%40v1"
        "&idempotency_key=idem-raw-query-001"
    )
    semantically_equivalent_path = (
        "/northbound/operations/status?idempotency_key=idem-raw-query-001"
        "&operation_identity=wms.inventory.confirm_inbound@v1"
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
    operation_identity = "wms.inventory.confirm_inbound@v1"
    path = "/api/wms/inventory/confirm-inbound"
    body = (
        b'{"dispatch_key":"dispatch-reset","inbound_key":"inbound-reset",'
        b'"material_code":"material-reset","quantity":"1"}'
    )
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

    operation_identity = "wms.inventory.confirm_inbound@v1"
    idempotency_key = "idem-public-evidence-001"
    path = "/api/wms/inventory/confirm-inbound"
    body = (
        b'{"dispatch_key":"dispatch-public","inbound_key":"inbound-public",'
        b'"material_code":"material-public","quantity":"1"}'
    )
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
        replay = client.post(path, content=body, headers=headers)
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
                "dispatch_key": "dispatch-public",
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


def test_wms_mock_rack_operation_ignores_unknown_requested_rack_code() -> None:
    callback_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-unknown-rack:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-unknown-rack:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-unknown-rack",
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "rack_code": "RACK-DOES-NOT-EXIST",
        }
    )

    assert callback_payload["rack_code"] == "RACK-001"


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
        response = client.post(
            "/api/wms/inventory/query",
            json={"sku": payload_data["HHPN"], "lot_no": payload_data["LotCode"]},
        )

    assert response.status_code == 200
    assert response.json()["data"]["items"] == [inventory]


def test_wms_mock_inventory_query_matches_additional_catalog_products() -> None:
    inventory = mock_wms_inventory_seed()
    with TestClient(wms_mock_server.app) as client:
        resistor_response = client.post(
            "/api/wms/inventory/query",
            json={"sku": "RES001", "lot_no": "LOT-R"},
        )
        ic_response = client.get(
            "/api/wms/inventory/query",
            params={"sku": "IC001", "lot_no": "LOT-I"},
        )

    assert resistor_response.status_code == 200
    assert resistor_response.json()["data"]["items"] == [inventory[("RES001", "LOT-R")]]
    assert ic_response.status_code == 200
    assert ic_response.json()["data"]["items"] == [inventory[("IC001", "LOT-I")]]


def test_wms_mock_inventory_query_returns_empty_items_for_unknown_sku_or_lot_no() -> None:
    payload_data = rough_sorter_scan_completed_payload()["data"]
    with TestClient(wms_mock_server.app) as client:
        unknown_sku_response = client.post(
            "/api/wms/inventory/query",
            json={"sku": "UNKNOWN", "lot_no": payload_data["LotCode"]},
        )
        unknown_lot_response = client.get(
            "/api/wms/inventory/query",
            params={"sku": payload_data["HHPN"], "lot_no": "UNKNOWN"},
        )

    assert unknown_sku_response.status_code == 200
    assert unknown_sku_response.json()["data"]["items"] == []
    assert unknown_lot_response.status_code == 200
    assert unknown_lot_response.json()["data"]["items"] == []


def test_wms_mock_rack_operation_builds_wes_external_callback(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json={
                "request_id": "rack-operation:op-001:1:ALLOCATE_AND_MOVE_RACK",
                "dispatch_key": "rack-operation:op-001:1:ALLOCATE_AND_MOVE_RACK",
                "callback_type": "WMS_RACK_ARRIVED",
                "operation_key": "op-001",
                "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
                "sequence_no": 1,
                "task_type": "ALLOCATE_AND_MOVE_RACK",
                "workline_code": "WL-ROUGH-SORTER-TEST",
                "rack_kind": "SINGLE_LAYER",
                "target_position_code": "SINGLE_LAYER_A",
                "trace_id": "trace-rack-001",
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["accepted"] is True
    mock_post_callback.assert_awaited_once()
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["dispatch_key"] == "rack-operation:op-001:1:ALLOCATE_AND_MOVE_RACK"
    assert callback_payload["source_event_id"].startswith("wms-mock:rack-operation:")
    assert callback_payload["source_system"] == "WMS"
    assert callback_payload["active_bin_rack"]["rack_code"] == "RACK-001"
    assert {cell["rack_slot_code"] for cell in callback_payload["active_bin_rack"]["cells"]} == {"A", "B", "C", "D"}
    assert len(callback_payload["active_bin_rack"]["cells"]) == 18
    assert {cell["bin_type"] for cell in callback_payload["active_bin_rack"]["cells"]} == {"6格箱", "3格箱"}
    assert {
        cell["bin_cell_index"] for cell in callback_payload["active_bin_rack"]["cells"] if cell["rack_slot_code"] == "A"
    } == {"1", "2", "3", "4", "5", "6"}
    assert {
        cell["bin_cell_index"] for cell in callback_payload["active_bin_rack"]["cells"] if cell["rack_slot_code"] == "C"
    } == {"1", "2", "7"}
    assert {mount["rack_slot_code"] for mount in callback_payload["bin_mounts"]} == {"A", "B", "C", "D"}
    assert callback_payload["bin_mounts"][0]["bin_code"] == "BIN-001"


@pytest.mark.parametrize(
    ("path", "payload", "operation_identity"),
    (
        (
            "/api/wms/inventory/confirm-inbound",
            {
                "dispatch_key": "confirm-inbound-001",
                "inbound_key": "INBOUND-001",
                "material_code": "MAT-001",
                "quantity": "1",
            },
            "wms.inventory.confirm_inbound@v1",
        ),
        (
            "/api/wms/fulfillment/package-binding",
            {
                "dispatch_key": "package-binding-001",
                "package_id": "PKG-001",
                "pallet_id": "PALLET-001",
                "station_code": "STATION-001",
            },
            "wms.fulfillment.notify_pkg_binding@v1",
        ),
        (
            "/api/wms/fulfillment/full-box-exchange",
            {
                "dispatch_key": "full-box-001",
                "rack_id": "RACK-001",
                "empty_box_id": "EMPTY-001",
                "full_box_id": "FULL-001",
            },
            "wms.fulfillment.full_box_exchange@v1",
        ),
    ),
)
def test_wms_mock_typed_effect_routes_deliver_declared_callback(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, object],
    operation_identity: str,
) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    body = json.dumps(payload, separators=(",", ":")).encode()

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key="idem-mock-001",
            ),
        )

    assert response.status_code == 202
    mock_post_callback.assert_awaited_once()
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_EFFECT_STATUS_HINT"
    assert callback_payload["source_system"] == "WMS"
    assert callback_payload["source_event_id"].startswith("wms-mock:typed-effect:")
    assert callback_payload["data"] == {
        "operation_identity": operation_identity,
        "idempotency_key": "idem-mock-001",
        "dispatch_key": payload["dispatch_key"],
    }


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


def test_wms_mock_bin_transport_route_delivers_completion_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = {
        "dispatch_key": "handling:bin-op-001:move:1",
        "callback_type": "WMS_TRANSPORT_COMPLETED",
        "operation_key": "bin-op-001",
        "trace_id": "trace-bin-op-001",
    }

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/transport-request", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["accepted"] is True
    mock_post_callback.assert_awaited_once()
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_TRANSPORT_COMPLETED"
    assert callback_payload["dispatch_key"] == payload["dispatch_key"]
    assert callback_payload["status"] == "SUCCESS"
    assert set(callback_payload) == {
        "callback_type",
        "dispatch_key",
        "exchange_request_code",
        "exchange_status",
        "source_event_id",
        "source_system",
        "source_version",
        "status",
        "occurred_at",
        "request_id",
        "signature",
        "timestamp",
        "trace_id",
        "wms_rcs_task_id",
    }


def test_wms_mock_full_box_northbound_submit_never_sends_legacy_completion_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = {
        "dispatch_key": "handling:full-box-001:move:1",
        "rack_id": "RACK-001",
        "empty_box_id": "EMPTY-001",
        "full_box_id": "FULL-001",
    }
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

    operation_identity = "wms.fulfillment.notify_pkg_binding@v1"
    credential_reference = "secret://wms/material-flow-sandbox-hmac@v2"
    secret = b"real-wes-material-flow-v2"
    submit_path = "/api/wms/fulfillment/package-binding"
    idempotency_key = "idem-real-wes-signer-001"
    payload = {
        "dispatch_key": "dispatch-real-wes-signer-001",
        "package_id": "package-real-wes-signer-001",
        "pallet_id": "pallet-real-wes-signer-001",
        "station_code": "station-real-wes-signer-001",
    }
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
        timestamp="2026-07-25T00:00:00+00:00",
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
            now=lambda: wms_mock_server.datetime(2026, 7, 25, tzinfo=wms_mock_server.UTC),
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
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
                "quantity": "1",
            },
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
            },
        ),
        (
            "confirm-extra",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
                "quantity": "1",
            },
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
                "quantity": "1",
                "trace_id": "forbidden-wire-field",
            },
        ),
        (
            "confirm-blank",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
                "quantity": "1",
            },
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": " ",
                "material_code": "material-valid",
                "quantity": "1",
            },
        ),
        (
            "confirm-number-quantity",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
                "quantity": "1",
            },
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
                "quantity": 1,
            },
        ),
        (
            "confirm-non-finite-quantity",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
                "quantity": "1",
            },
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
                "quantity": "NaN",
            },
        ),
        (
            "confirm-non-positive-quantity",
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
                "quantity": "1",
            },
            {
                "dispatch_key": "dispatch-confirm-valid",
                "inbound_key": "inbound-valid",
                "material_code": "material-valid",
                "quantity": "0",
            },
        ),
        (
            "full-box-extra",
            "/api/wms/fulfillment/full-box-exchange",
            "wms.fulfillment.full_box_exchange@v1",
            {
                "dispatch_key": "dispatch-full-box-valid",
                "rack_id": "rack-valid",
                "empty_box_id": "empty-valid",
                "full_box_id": "full-valid",
            },
            {
                "dispatch_key": "dispatch-full-box-valid",
                "provider_code": "WMS",
                "rack_id": "rack-valid",
                "empty_box_id": "empty-valid",
                "full_box_id": "full-valid",
            },
        ),
        (
            "package-binding-missing",
            "/api/wms/fulfillment/package-binding",
            "wms.fulfillment.notify_pkg_binding@v1",
            {
                "dispatch_key": "dispatch-binding-valid",
                "package_id": "package-valid",
                "pallet_id": "pallet-valid",
                "station_code": "station-valid",
            },
            {
                "dispatch_key": "dispatch-binding-valid",
                "package_id": "package-valid",
                "pallet_id": "pallet-valid",
            },
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
    assert accepted.status_code == 202


@pytest.mark.parametrize(
    ("path", "operation_identity", "payload", "expected_result"),
    (
        (
            "/api/wms/inventory/confirm-inbound",
            "wms.inventory.confirm_inbound@v1",
            {
                "dispatch_key": "dispatch-result-confirm",
                "inbound_key": "inbound-result-confirm",
                "material_code": "material-result-confirm",
                "quantity": "1",
            },
            {
                "dispatch_key": "dispatch-result-confirm",
                "inbound_key": "inbound-result-confirm",
                "document_no": "inbound-result-confirm",
            },
        ),
        (
            "/api/wms/fulfillment/full-box-exchange",
            "wms.fulfillment.full_box_exchange@v1",
            {
                "dispatch_key": "dispatch-result-full-box",
                "rack_id": "rack-result-full-box",
                "empty_box_id": "empty-result-full-box",
                "full_box_id": "full-result-full-box",
            },
            {
                "dispatch_key": "dispatch-result-full-box",
                "rack_id": "rack-result-full-box",
                "empty_box_id": "empty-result-full-box",
                "full_box_id": "full-result-full-box",
                "exchange_request_code": "dispatch-result-full-box",
            },
        ),
        (
            "/api/wms/fulfillment/package-binding",
            "wms.fulfillment.notify_pkg_binding@v1",
            {
                "dispatch_key": "dispatch-result-binding",
                "package_id": "package-result-binding",
                "pallet_id": "pallet-result-binding",
                "station_code": "station-result-binding",
            },
            {
                "dispatch_key": "dispatch-result-binding",
                "package_id": "package-result-binding",
                "pallet_id": "pallet-result-binding",
            },
        ),
    ),
)
def test_completed_typed_result_has_non_empty_request_correlation(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    operation_identity: str,
    payload: dict[str, str],
    expected_result: dict[str, str],
) -> None:
    monkeypatch.setattr(wms_mock_server, "_post_callback", AsyncMock(return_value={"delivered": True}))
    idempotency_key = f"idem-{payload['dispatch_key']}"
    body = json.dumps(payload, separators=(",", ":")).encode()
    status_path = "/northbound/operations/status?" + urlencode(
        (("operation_identity", operation_identity), ("idempotency_key", idempotency_key))
    )

    with TestClient(wms_mock_server.app) as client:
        submitted = client.post(
            path,
            content=body,
            headers=_submit_headers(
                body=body,
                path=path,
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
            ),
        )
        snapshots = [client.get(status_path, headers=_status_headers(path=status_path)).json() for _ in range(3)]

    assert submitted.status_code == 202
    assert snapshots[-1]["state"] == "COMPLETED"
    for field_name, expected_value in expected_result.items():
        assert snapshots[-1]["result_payload"][field_name] == expected_value
        assert expected_value.strip()


def test_legacy_full_box_endpoint_keeps_200_completion_callback_separate_from_typed_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", callback)
    payload = {
        "dispatch_key": "legacy-full-box-001",
        "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
        "rack_release_id": "release-legacy-001",
        "trace_id": "trace-legacy-full-box-001",
    }

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/legacy/full-box-exchange", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "data": {"accepted": True, "dispatch_key": "legacy-full-box-001"},
    }
    callback.assert_awaited_once()
    callback_payload = callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_FULL_BOX_EXCHANGE_RESULT"
    assert callback_payload["exchange_status"] == "PHYSICAL_COMPLETED"
    assert callback_payload["rack_release_id"] == "release-legacy-001"


def test_public_clock_drives_visibility_and_retention_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wms_mock_server, "_post_callback", AsyncMock(return_value={"delivered": True}))
    operation_identity = "wms.fulfillment.notify_pkg_binding@v1"
    idempotency_key = "idem-public-clock-boundary-001"
    path = "/api/wms/fulfillment/package-binding"
    payload = {
        "dispatch_key": "dispatch-public-clock-boundary-001",
        "package_id": "package-public-clock-boundary-001",
        "pallet_id": "pallet-public-clock-boundary-001",
        "station_code": "station-public-clock-boundary-001",
    }
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
        replay_before_boundary = client.post(path, content=body, headers=headers)
        client.post("/debug/northbound/clock", json={"now": "2026-07-25T00:00:09+00:00"})
        expired_at_boundary = client.get(status_path, headers=_status_headers(path=status_path))
        recovered_at_boundary = client.post(path, content=body, headers=headers)
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


def test_northbound_fault_scope_does_not_affect_health_inventory_or_legacy_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wms_mock_server, "_post_callback", AsyncMock(return_value={"delivered": True}))
    operation_identity = "wms.inventory.confirm_inbound@v1"
    idempotency_key = "idem-fault-scope-001"
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
        health = client.get("/")
        inventory = client.get("/api/wms/materials/MAT001")
        legacy = client.post(
            "/api/wms/legacy/full-box-exchange",
            json={"dispatch_key": "legacy-unaffected-001"},
        )
        faulted = client.get(status_path, headers=_status_headers(path=status_path))

    assert configured.status_code == 200
    assert health.status_code == 200
    assert inventory.status_code in {200, 404}
    assert legacy.status_code == 200
    assert faulted.status_code == 503
    assert faulted.json() == {"code": "TEMPORARILY_UNAVAILABLE"}


def test_visible_then_lost_is_an_independent_one_shot_status_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wms_mock_server, "_post_callback", AsyncMock(return_value={"delivered": True}))
    operation_identity = "wms.fulfillment.notify_pkg_binding@v1"
    idempotency_key = "idem-visible-then-lost-001"
    path = "/api/wms/fulfillment/package-binding"
    payload = {
        "dispatch_key": "dispatch-visible-then-lost-001",
        "package_id": "package-visible-then-lost-001",
        "pallet_id": "pallet-visible-then-lost-001",
        "station_code": "station-visible-then-lost-001",
    }
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
    operation_identity = "wms.inventory.confirm_inbound@v1"
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


@pytest.mark.asyncio
async def test_concurrent_identical_http_replay_creates_exactly_one_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wms_mock_server, "_post_callback", AsyncMock(return_value={"delivered": True}))
    operation_identity = "wms.fulfillment.notify_pkg_binding@v1"
    idempotency_key = "idem-concurrent-replay-001"
    path = "/api/wms/fulfillment/package-binding"
    payload = {
        "dispatch_key": "dispatch-concurrent-replay-001",
        "package_id": "package-concurrent-replay-001",
        "pallet_id": "pallet-concurrent-replay-001",
        "station_code": "station-concurrent-replay-001",
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = _submit_headers(
        body=body,
        path=path,
        operation_identity=operation_identity,
        idempotency_key=idempotency_key,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wms_mock_server.app),
        base_url="http://mock-wms.test",
    ) as client:
        responses = await asyncio.gather(*(client.post(path, content=body, headers=headers) for _ in range(8)))
        effects = await client.get(
            "/debug/northbound/effects",
            params={"operation_identity": operation_identity, "idempotency_key": idempotency_key},
        )

    assert [response.status_code for response in responses].count(202) == 1
    assert [response.status_code for response in responses].count(409) == 7
    assert effects.json()["effect_count"] == 1


@pytest.mark.asyncio
async def test_concurrent_matching_requests_atomically_claim_one_shot_fault() -> None:
    operation_identity = "wms.inventory.confirm_inbound@v1"
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


def _rack_allocate_payload(
    operation_key: str,
    material: dict[str, str],
    target_position_code: str = "SINGLE_LAYER_A",
) -> dict[str, object]:
    return {
        "request_id": f"rack-operation:{operation_key}:1:ALLOCATE_AND_MOVE_RACK",
        "dispatch_key": f"rack-operation:{operation_key}:1:ALLOCATE_AND_MOVE_RACK",
        "callback_type": "WMS_RACK_ARRIVED",
        "operation_key": operation_key,
        "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
        "sequence_no": 1,
        "task_type": "ALLOCATE_AND_MOVE_RACK",
        "workline_code": "WL-ROUGH-SORTER-TEST",
        "rack_kind": "SINGLE_LAYER",
        "target_position_code": target_position_code,
        "trace_id": f"trace-{operation_key}",
        "material": material,
    }


def _rack_replace_payload(operation_key: str) -> dict[str, object]:
    payload = _rack_allocate_payload(operation_key, THIRTEEN_INCH_MATERIAL)
    payload.pop("task_type")
    payload.pop("sequence_no")
    payload["rack_tasks"] = [
        {
            "sequence_no": 1,
            "task_type": "MOVE_OUT_ACTIVE_RACK",
            "rack_kind": "SINGLE_LAYER",
            "rack_code": "RACK-001",
            "source_position_code": "SINGLE_LAYER_A",
            "target_position_role": "SMT_EMPTY_RACK_AREA",
        },
        {
            "sequence_no": 2,
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "rack_kind": "SINGLE_LAYER",
            "target_position_code": "SINGLE_LAYER_A",
            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
        },
    ]
    return payload


SEVEN_INCH_MATERIAL = {
    "HHPN": "RES001",
    "LotCode": "LOT-R",
    "DateCode": "20260407",
    "PkgID": "PKG-RES001-LOT-R-001",
}

THIRTEEN_INCH_MATERIAL = {
    "HHPN": "IC001",
    "LotCode": "LOT-I",
    "DateCode": "20260413",
    "PkgID": "PKG-IC001-LOT-I-001",
}


def _assert_failure_callback_contract(
    payload: dict[str, object],
    *,
    dispatch_key: str,
    operation_key: str,
    reason_code: str,
    reason_message: str | None = None,
) -> None:
    assert payload["callback_type"] == "WMS_RACK_EXCHANGE_FAILED"
    assert payload["dispatch_key"] == dispatch_key
    assert payload["request_id"] == dispatch_key
    assert payload["operation_key"] == operation_key
    assert payload["status"] == "FAILED"
    assert payload["task_status"] == "FAILED"
    assert payload["result"] == "FAILED"
    assert payload["reason_code"] == reason_code
    assert payload["error_code"] == reason_code
    if reason_message is not None:
        assert payload["reason_message"] == reason_message
        assert payload["error_message"] == reason_message
    else:
        assert isinstance(payload["reason_message"], str)
        assert payload["reason_message"]
        assert payload["error_message"] == payload["reason_message"]


def test_wms_mock_rack_operation_allocates_seven_inch_six_cell_rack_and_marks_unavailable(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json=_rack_allocate_payload("op-7inch-allocated", SEVEN_INCH_MATERIAL),
        )

    assert response.status_code == 200
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["rack_code"] == "RACK-001"
    assert {cell["bin_type"] for cell in callback_payload["active_bin_rack"]["cells"]} == {"6格箱", "3格箱"}
    allocated_rack = wms_mock_server.mock_wms_state.rack_pool["RACK-001"]
    assert allocated_rack["status"] == "ACTIVE"
    assert allocated_rack["active_position_code"] == "SINGLE_LAYER_A"
    assert allocated_rack["allocated_operation_key"] == "op-7inch-allocated"
    assert wms_mock_server.mock_wms_state.recent_operations[-1]["rack_code"] == "RACK-001"


def test_wms_mock_rack_operation_allocates_13inch_three_cell_callback_cells(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json=_rack_allocate_payload("op-13inch-allocated", THIRTEEN_INCH_MATERIAL),
        )

    assert response.status_code == 200
    callback_payload = mock_post_callback.await_args.args[1]
    cells = callback_payload["active_bin_rack"]["cells"]
    assert callback_payload["rack_code"] == "RACK-3CELL-001"
    assert callback_payload["active_bin_rack"]["rack_code"] == "RACK-3CELL-001"
    assert {cell["bin_cell_index"] for cell in cells if cell["rack_slot_code"] == "A"} == {"1", "2", "7"}
    assert {cell["capacity_depth_mm"] for cell in cells if cell["bin_cell_index"] == "7"} == {80.0}
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-3CELL-001"]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_wms_mock_state_allocates_distinct_three_cell_racks_until_pool_exhaustion() -> None:
    callback_payloads = []
    for index in range(1, 5):
        callback_payloads.append(
            await wms_mock_server.mock_wms_state.allocate_rack_for_payload(
                _rack_allocate_payload(f"op-13inch-{index}", THIRTEEN_INCH_MATERIAL)
            )
        )
        wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = None

    exhausted_payload = await wms_mock_server.mock_wms_state.allocate_rack_for_payload(
        _rack_allocate_payload("op-13inch-exhausted-natural", THIRTEEN_INCH_MATERIAL)
    )

    assert [payload["rack_code"] for payload in callback_payloads] == [
        "RACK-3CELL-001",
        "RACK-3CELL-002",
        "RACK-3CELL-003",
        "RACK-3CELL-004",
    ]
    assert exhausted_payload["callback_type"] == "WMS_RACK_EXCHANGE_FAILED"
    assert exhausted_payload["reason_code"] == "NO_AVAILABLE_RACK"
    assert all(
        wms_mock_server.mock_wms_state.rack_pool[f"RACK-3CELL-{index:03d}"]["status"] == "ACTIVE"
        for index in range(1, 5)
    )


def test_wms_mock_rack_operation_exhausted_pool_uses_failure_callback_contract(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    for rack in wms_mock_server.mock_wms_state.rack_pool.values():
        if rack["bin_type"] == "3格箱":
            rack["status"] = "ALLOCATED"

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json=_rack_allocate_payload("op-13inch-exhausted", THIRTEEN_INCH_MATERIAL),
        )

    assert response.status_code == 200
    assert response.json()["data"]["accepted"] is True
    failure_payload = mock_post_callback.await_args.args[1]
    _assert_failure_callback_contract(
        failure_payload,
        dispatch_key="rack-operation:op-13inch-exhausted:1:ALLOCATE_AND_MOVE_RACK",
        operation_key="op-13inch-exhausted",
        reason_code="NO_AVAILABLE_RACK",
    )
    assert "3格箱" in failure_payload["reason_message"]


def test_wms_mock_rack_operation_occupied_position_uses_failure_callback_contract(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = "RACK-001"
    wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] = "ALLOCATED"

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json=_rack_allocate_payload("op-position-occupied", SEVEN_INCH_MATERIAL),
        )

    assert response.status_code == 200
    assert response.json()["data"]["accepted"] is True
    failure_payload = mock_post_callback.await_args.args[1]
    _assert_failure_callback_contract(
        failure_payload,
        dispatch_key="rack-operation:op-position-occupied:1:ALLOCATE_AND_MOVE_RACK",
        operation_key="op-position-occupied",
        reason_code="TARGET_POSITION_OCCUPIED",
        reason_message="目标工位 SINGLE_LAYER_A 已有活动货架 RACK-001",
    )


def test_wms_mock_rack_operation_requested_rack_layout_mismatch_uses_failure_callback_contract(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = _rack_allocate_payload("op-layout-mismatch", THIRTEEN_INCH_MATERIAL)
    payload["rack_code"] = "RACK-6CELL-001"

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/rack-operation", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["accepted"] is True
    failure_payload = mock_post_callback.await_args.args[1]
    _assert_failure_callback_contract(
        failure_payload,
        dispatch_key="rack-operation:op-layout-mismatch:1:ALLOCATE_AND_MOVE_RACK",
        operation_key="op-layout-mismatch",
        reason_code="RACK_LAYOUT_MISMATCH",
        reason_message="指定货架 RACK-6CELL-001 不匹配 3格箱",
    )


def test_wms_mock_rack_operation_honors_requested_matching_available_rack(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = _rack_allocate_payload("op-requested-rack", SEVEN_INCH_MATERIAL)
    payload["rack_code"] = "RACK-6CELL-003"

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/rack-operation", json=payload)

    assert response.status_code == 200
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["rack_code"] == "RACK-6CELL-003"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-6CELL-003"]["status"] == "ACTIVE"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "AVAILABLE"


def test_wms_mock_single_task_rack_operation_preserves_request_dispatch_key(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = _rack_allocate_payload("op-preserve-dispatch", SEVEN_INCH_MATERIAL)
    payload["dispatch_key"] = "custom-dispatch-key"
    payload["request_id"] = "custom-request-id"

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/rack-operation", json=payload)

    assert response.status_code == 200
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["dispatch_key"] == "custom-dispatch-key"
    assert callback_payload["request_id"] == "custom-request-id"


def test_wms_mock_rack_operation_move_out_then_allocate_releases_position(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = "RACK-001"
    old_rack = wms_mock_server.mock_wms_state.rack_pool["RACK-001"]
    old_rack["status"] = "ACTIVE"
    old_rack["active_position_code"] = "SINGLE_LAYER_A"
    old_rack["allocated_operation_key"] = "op-old-active"

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json=_rack_replace_payload("op-replace-active"),
        )

    assert response.status_code == 200
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["dispatch_key"] == "rack-operation:op-replace-active:2:ALLOCATE_AND_MOVE_RACK"
    assert callback_payload["request_id"] == "rack-operation:op-replace-active:2:ALLOCATE_AND_MOVE_RACK"
    assert callback_payload["rack_code"] == "RACK-3CELL-001"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "MOVED_OUT"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["active_position_code"] is None
    new_rack = wms_mock_server.mock_wms_state.rack_pool["RACK-3CELL-001"]
    assert new_rack["status"] == "ACTIVE"
    assert new_rack["active_position_code"] == "SINGLE_LAYER_A"
    assert wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] == "RACK-3CELL-001"


def test_wms_mock_rack_operation_move_out_mismatch_uses_failure_callback_contract(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = "RACK-001"
    wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] = "ACTIVE"
    wms_mock_server.mock_wms_state.rack_pool["RACK-6CELL-001"]["status"] = "ACTIVE"
    payload = _rack_replace_payload("op-move-out-mismatch")
    payload["rack_tasks"][0]["rack_code"] = "RACK-6CELL-001"

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/rack-operation", json=payload)

    assert response.status_code == 200
    failure_payload = mock_post_callback.await_args.args[1]
    _assert_failure_callback_contract(
        failure_payload,
        dispatch_key="rack-operation:op-move-out-mismatch:1:MOVE_OUT_ACTIVE_RACK",
        operation_key="op-move-out-mismatch",
        reason_code="MOVE_OUT_RACK_MISMATCH",
    )
    assert wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] == "RACK-001"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-6CELL-001"]["status"] == "ACTIVE"


def test_wms_mock_rack_operation_move_out_empty_position_uses_failure_callback_contract(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = _rack_replace_payload("op-move-out-empty-position")

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/rack-operation", json=payload)

    assert response.status_code == 200
    failure_payload = mock_post_callback.await_args.args[1]
    _assert_failure_callback_contract(
        failure_payload,
        dispatch_key="rack-operation:op-move-out-empty-position:1:MOVE_OUT_ACTIVE_RACK",
        operation_key="op-move-out-empty-position",
        reason_code="MOVE_OUT_RACK_MISMATCH",
        reason_message="工位 SINGLE_LAYER_A 当前活动货架为 无，不是 RACK-001",
    )
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "AVAILABLE"


def test_wms_mock_recent_operations_keeps_bounded_history() -> None:
    for index in range(wms_mock_server.RECENT_OPERATION_LIMIT + 5):
        wms_mock_server.mock_wms_state._record_operation({"operation_key": f"op-{index}"})

    recent_operations = wms_mock_server.mock_wms_state.recent_operations
    assert len(recent_operations) == wms_mock_server.RECENT_OPERATION_LIMIT
    assert recent_operations[0]["operation_key"] == "op-5"


def test_wms_mock_debug_racks_returns_pool_positions_and_recent_operations(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)

    with TestClient(wms_mock_server.app) as client:
        client.post(
            "/api/wms/rack-operation",
            json=_rack_allocate_payload("op-debug-racks", SEVEN_INCH_MATERIAL),
        )
        response = client.get("/debug/racks")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["work_positions"]["SINGLE_LAYER_A"] == "RACK-001"
    assert data["racks"]["RACK-001"]["status"] == "ACTIVE"
    assert data["recent_operations"][-1]["operation_key"] == "op-debug-racks"
    assert data["recent_operations"][-1]["rack_code"] == "RACK-001"


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
        response = client.get("/api/wms/racks/RACK-001")

    assert response.status_code == 200
    rack_payload = response.json()["data"]
    rack_payload["status"] = "MUTATED_BY_TEST"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "AVAILABLE"


def test_wms_mock_rack_list_returns_copy_not_internal_state() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/api/wms/racks")

    assert response.status_code == 200
    rack_payload = response.json()["data"][0]
    rack_payload["status"] = "MUTATED_BY_TEST"
    assert wms_mock_server.mock_wms_state.rack_pool[rack_payload["rack_id"]]["status"] == "AVAILABLE"


@pytest.mark.asyncio
async def test_wms_mock_state_concurrent_allocate_and_move_rack_requests_do_not_share_rack() -> None:
    async def allocate(operation_key: str, target_position_code: str) -> str:
        callback_payload = await wms_mock_server.mock_wms_state.allocate_rack_for_payload(
            _rack_allocate_payload(operation_key, SEVEN_INCH_MATERIAL, target_position_code=target_position_code)
        )
        return str(callback_payload["rack_code"])

    first_rack_code, second_rack_code = await asyncio.gather(
        allocate("op-concurrent-1", "SINGLE_LAYER_A"),
        allocate("op-concurrent-2", "SINGLE_LAYER_B"),
    )

    assert first_rack_code != second_rack_code
    assert {first_rack_code, second_rack_code} == {"RACK-001", "RACK-6CELL-001"}


@pytest.mark.asyncio
async def test_wms_mock_rack_operation_route_concurrent_requests_do_not_share_rack(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    transport = httpx.ASGITransport(app=wms_mock_server.app)

    async def post_operation(operation_key: str, target_position_code: str) -> None:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/wms/rack-operation",
                json=_rack_allocate_payload(
                    operation_key,
                    SEVEN_INCH_MATERIAL,
                    target_position_code=target_position_code,
                ),
            )
        assert response.status_code == 200
        assert response.json()["data"]["accepted"] is True

    await asyncio.gather(
        post_operation("op-route-concurrent-1", "SINGLE_LAYER_A"),
        post_operation("op-route-concurrent-2", "SINGLE_LAYER_B"),
    )

    delivered_rack_codes = {call.args[1]["rack_code"] for call in mock_post_callback.await_args_list}
    assert delivered_rack_codes == {"RACK-001", "RACK-6CELL-001"}
    assert (
        wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"]
        != (wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_B"])
    )


def test_wms_mock_rack_operation_supplies_three_cell_bins_for_13inch_material() -> None:
    callback_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-13inch",
            "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
            "sequence_no": 1,
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "workline_code": "WL-ROUGH-SORTER-TEST",
            "rack_kind": "SINGLE_LAYER",
            "target_position_code": "SINGLE_LAYER_A",
            "material": {
                "HHPN": "IC001",
                "LotCode": "LOT-I",
                "DateCode": "20260413",
                "PkgID": "PKG-IC001-LOT-I-001",
            },
        }
    )

    cells = callback_payload["active_bin_rack"]["cells"]
    assert callback_payload["rack_code"] == "RACK-3CELL-001"
    assert callback_payload["active_bin_rack"]["rack_code"] == "RACK-3CELL-001"
    assert len(cells) == 12
    assert {cell["bin_type"] for cell in cells} == {"3格箱"}
    assert {cell["bin_cell_index"] for cell in cells if cell["rack_slot_code"] == "A"} == {"1", "2", "7"}
    assert {cell["capacity_depth_mm"] for cell in cells if cell["bin_cell_index"] == "7"} == {80.0}


def test_wms_mock_rack_operation_uses_distinct_bin_codes_per_rack() -> None:
    six_cell_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-7inch:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-7inch:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-7inch",
            "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
            "sequence_no": 1,
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "workline_code": "WL-ROUGH-SORTER-TEST",
            "rack_kind": "SINGLE_LAYER",
            "target_position_code": "SINGLE_LAYER_A",
            "material": {
                "HHPN": "RES001",
                "LotCode": "LOT-R",
                "DateCode": "20260407",
                "PkgID": "PKG-RES001-LOT-R-001",
            },
        }
    )
    three_cell_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-13inch",
            "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
            "sequence_no": 1,
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "workline_code": "WL-ROUGH-SORTER-TEST",
            "rack_kind": "SINGLE_LAYER",
            "target_position_code": "SINGLE_LAYER_A",
            "material": {
                "HHPN": "IC001",
                "LotCode": "LOT-I",
                "DateCode": "20260413",
                "PkgID": "PKG-IC001-LOT-I-001",
            },
        }
    )

    six_cell_bins = {mount["bin_code"] for mount in six_cell_payload["bin_mounts"]}
    three_cell_bins = {mount["bin_code"] for mount in three_cell_payload["bin_mounts"]}
    assert six_cell_payload["rack_code"] == "RACK-001"
    assert three_cell_payload["rack_code"] == "RACK-3CELL-001"
    assert six_cell_bins.isdisjoint(three_cell_bins)


def test_wms_mock_rack_operation_keeps_rack_bin_cell_physical_constraints() -> None:
    six_cell_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-physical-7inch:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-physical-7inch:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-physical-7inch",
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "rack_code": "RACK-3CELL-001",
            "material": {
                "HHPN": "CAP001",
                "LotCode": "LOT-A",
                "DateCode": "20260409",
                "PkgID": "PKG-CAP001-LOT-A-001",
            },
        }
    )
    three_cell_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-physical-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-physical-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-physical-13inch",
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "rack_code": "RACK-001",
            "material": {
                "HHPN": "IC001",
                "LotCode": "LOT-I",
                "DateCode": "20260413",
                "PkgID": "PKG-IC001-LOT-I-001",
            },
        }
    )

    assert six_cell_payload["rack_code"] == "RACK-001"
    assert {cell["bin_type"] for cell in six_cell_payload["active_bin_rack"]["cells"]} == {"6格箱", "3格箱"}
    assert len(six_cell_payload["bin_mounts"]) == 4
    assert len(six_cell_payload["active_bin_rack"]["cells"]) == 18
    for mount in six_cell_payload["bin_mounts"]:
        slot_cells = [
            cell
            for cell in six_cell_payload["active_bin_rack"]["cells"]
            if cell["rack_slot_code"] == mount["rack_slot_code"]
        ]
        assert {cell["bin_code"] for cell in slot_cells} == {mount["bin_code"]}
        if mount["rack_slot_code"] in {"A", "B"}:
            assert {cell["bin_cell_index"] for cell in slot_cells} == {"1", "2", "3", "4", "5", "6"}
        else:
            assert {cell["bin_cell_index"] for cell in slot_cells} == {"1", "2", "7"}

    assert three_cell_payload["rack_code"] == "RACK-001"
    assert {cell["bin_type"] for cell in three_cell_payload["active_bin_rack"]["cells"]} == {"6格箱", "3格箱"}
    assert len(three_cell_payload["bin_mounts"]) == 4
    assert len(three_cell_payload["active_bin_rack"]["cells"]) == 18
    for mount in three_cell_payload["bin_mounts"]:
        slot_cells = [
            cell
            for cell in three_cell_payload["active_bin_rack"]["cells"]
            if cell["rack_slot_code"] == mount["rack_slot_code"]
        ]
        assert {cell["bin_code"] for cell in slot_cells} == {mount["bin_code"]}
        if mount["rack_slot_code"] in {"A", "B"}:
            assert {cell["bin_cell_index"] for cell in slot_cells} == {"1", "2", "3", "4", "5", "6"}
        else:
            assert {cell["bin_cell_index"] for cell in slot_cells} == {"1", "2", "7"}
            assert {cell["capacity_depth_mm"] for cell in slot_cells if cell["bin_cell_index"] == "7"} == {80.0}


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


def test_wms_mock_rack_operation_source_event_id_keeps_wes_idempotency_key_short() -> None:
    dispatch_key = (
        "rack-operation:external:smt_rack_bin:rough-sorter-mock-scan-1780455233:RACK_OPERATION:1:ALLOCATE_AND_MOVE_RACK"
    )

    callback_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": dispatch_key,
            "dispatch_key": dispatch_key,
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "external:smt_rack_bin:rough-sorter-mock-scan-1780455233:RACK_OPERATION",
            "trace_id": "rough-sorter-mock-scan-1780455233",
        }
    )

    idempotency_key = (
        f"external_http:{callback_payload['callback_type']}:{callback_payload['trace_id']}:"
        f"source_event:{callback_payload['source_event_id']}"
    )
    assert len(callback_payload["source_event_id"]) <= 200
    assert len(idempotency_key) <= 200


def test_wms_mock_rack_operation_task_result_includes_required_status() -> None:
    callback_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-002:2:MOVE_RACK",
            "dispatch_key": "rack-operation:op-002:2:MOVE_RACK",
            "callback_type": "WMS_RACK_TASK_RESULT",
            "operation_key": "op-002",
            "sequence_no": 2,
            "task_type": "MOVE_RACK",
            "workline_code": "WL-ROUGH-SORTER-TEST",
            "trace_id": "trace-rack-002",
        }
    )

    assert callback_payload["callback_type"] == "WMS_RACK_TASK_RESULT"
    assert callback_payload["status"] == "SUCCESS"
    assert callback_payload["task_status"] == "SUCCESS"
    assert callback_payload["result"] == "SUCCESS"


@pytest.mark.asyncio
async def test_wms_mock_move_rack_updates_location_status_and_releases_position() -> None:
    wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = "RACK-001"
    rack = wms_mock_server.mock_wms_state.rack_pool["RACK-001"]
    rack["status"] = "ACTIVE"
    rack["active_position_code"] = "SINGLE_LAYER_A"
    rack["allocated_operation_key"] = "op-active"

    callback_payload = await wms_mock_server.mock_wms_state.apply_operation(
        {
            "request_id": "rack-operation:op-002:2:MOVE_RACK",
            "dispatch_key": "rack-operation:op-002:2:MOVE_RACK",
            "callback_type": "WMS_RACK_TASK_RESULT",
            "operation_key": "op-002",
            "sequence_no": 2,
            "task_type": "MOVE_RACK",
            "rack_code": "RACK-001",
            "target_position_code": "EMPTY_RACK_AREA",
        }
    )

    assert callback_payload["callback_type"] == "WMS_RACK_TASK_RESULT"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["current_location"] == "EMPTY_RACK_AREA"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "MOVED_OUT"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["active_position_code"] is None
    assert wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] is None


@pytest.mark.asyncio
async def test_wms_mock_move_rack_callback_preserves_requested_three_cell_rack() -> None:
    wms_mock_server.mock_wms_state.rack_pool["RACK-3CELL-001"]["status"] = "ACTIVE"

    callback_payload = await wms_mock_server.mock_wms_state.apply_operation(
        {
            "request_id": "rack-operation:op-3cell-move:2:MOVE_RACK",
            "dispatch_key": "rack-operation:op-3cell-move:2:MOVE_RACK",
            "callback_type": "WMS_RACK_TASK_RESULT",
            "operation_key": "op-3cell-move",
            "sequence_no": 2,
            "task_type": "MOVE_RACK",
            "rack_code": "RACK-3CELL-001",
            "target_position_code": "EMPTY_RACK_AREA",
        }
    )

    assert callback_payload["callback_type"] == "WMS_RACK_TASK_RESULT"
    assert callback_payload["rack_code"] == "RACK-3CELL-001"
    assert callback_payload["active_bin_rack"]["rack_code"] == "RACK-3CELL-001"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-3CELL-001"]["current_location"] == "EMPTY_RACK_AREA"
