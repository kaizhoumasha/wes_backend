"""Material-flow 本机 MOCK 验收。

这些测试只验证本机 mock 能表达目标合同，不代表 evidence profile 闭合。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import ClassVar

from fastapi.testclient import TestClient

from src.app.runtime.capabilities.material_flow.sorter_inbound_preview_service import SorterInboundPreviewService
from tests.mock import ecs_mock_server, wms_mock_server
from tests.mock.wms_northbound_contract import (
    ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
    MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2,
    canonical_submit_string,
)


class _CapturingAsyncClient:
    requests: ClassVar[list[dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, json: dict, headers: dict | None = None):
        self.requests.append({"url": url, "json": json, "headers": headers or {}})
        return _FakeResponse()


class _FakeResponse:
    status_code = 200
    text = '{"code": 200, "message": "OK"}'

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"code": 200, "message": "OK"}


def setup_function() -> None:
    _CapturingAsyncClient.requests.clear()
    ecs_mock_server.reset_mock_state()
    wms_mock_server.reset_mock_wms_state()


def _typed_submit(
    client: TestClient,
    *,
    path: str,
    operation_identity: str,
    idempotency_key: str,
    payload: dict,
    secret: str,
):
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = "1721865600"
    nonce = f"acceptance-{idempotency_key}"
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
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
        "X-WES-Content-SHA256": payload_hash,
        "X-WES-Credential-Reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "X-WES-Nonce": nonce,
        "X-WES-Operation-Identity": operation_identity,
        "X-WES-Signature": hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest(),
        "X-WES-Signature-Algorithm": "HMAC_SHA256",
        "X-WES-Timestamp": timestamp,
    }
    return client.post(path, content=body, headers=headers)


def test_sorter_inbound_mock_acceptance_separates_pkg_binding_from_inventory_transaction(monkeypatch) -> None:
    """PKG binding 走 fulfillment mock，库存事务走 inventory mock。"""

    secret = "material-flow-acceptance-v2"
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, secret)
    monkeypatch.setattr(wms_mock_server.httpx, "AsyncClient", _CapturingAsyncClient)
    with TestClient(wms_mock_server.app) as client:
        binding_response = _typed_submit(
            client,
            path="/api/wms/fulfillment/package-binding",
            operation_identity="wms.fulfillment.notify_pkg_binding@v1",
            idempotency_key="mock-sorter-binding-001",
            secret=secret,
            payload={
                "dispatch_key": "mock-sorter-binding-001",
                "package_id": "PKG-CAP001-LOT-A-001",
                "pallet_id": "PALLET-A",
                "station_code": "SORTER-STATION-A",
            },
        )
        inventory_response = _typed_submit(
            client,
            path="/api/wms/inventory/confirm-inbound",
            operation_identity="wms.inventory.confirm_inbound@v1",
            idempotency_key="wms-confirm-inbound:WMS:INBOUND-PKG-CAP001-LOT-A-001",
            secret=secret,
            payload={
                "dispatch_key": "wms-confirm-inbound:WMS:INBOUND-PKG-CAP001-LOT-A-001",
                "inbound_key": "INBOUND-PKG-CAP001-LOT-A-001",
                "material_code": "MAT-CAP001",
                "quantity": "1",
            },
        )

    assert binding_response.status_code == 202
    assert binding_response.json()["data"] | {"northbound_status": None} == {
        "request_id": "mock-sorter-binding-001",
        "binding_key": "PKG-CAP001-LOT-A-001:PALLET-A:SORTER-STATION-A",
        "package_id": "PKG-CAP001-LOT-A-001",
        "pallet_id": "PALLET-A",
        "station_code": "SORTER-STATION-A",
        "accepted": True,
        "northbound_status": None,
    }
    assert inventory_response.status_code == 202
    assert inventory_response.json()["data"] | {"northbound_status": None} == {
        "dispatch_key": "wms-confirm-inbound:WMS:INBOUND-PKG-CAP001-LOT-A-001",
        "inbound_key": "INBOUND-PKG-CAP001-LOT-A-001",
        "accepted": True,
        "northbound_status": None,
    }


def test_sorter_inbound_mock_acceptance_models_full_box_pre_diversion_contract(monkeypatch) -> None:
    """WES 先完成满箱安全分流，再通过严格 typed EFFECT 合同提交 WMS。"""

    secret = "material-flow-acceptance-v2"
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, secret)
    preview_service = SorterInboundPreviewService()
    preview = preview_service.preview_full_box_exchange(
        {
            "request_id": "mock-sorter-full-box-001",
            "rack_code": "RACK-6CELL-001",
            "rack_side": "A",
            "exchange_zone": "FULL_BOX_EXCHANGE_ZONE_A",
            "full_box_object_keys": ["PKG-FULL-001", "PKG-FULL-002"],
            "remaining_object_keys": ["PKG-FULL-001", "PKG-PIECE-001"],
        }
    )
    no_exchange_preview = preview_service.preview_full_box_exchange(
        {
            "request_id": "mock-sorter-no-full-box-001",
            "rack_code": "RACK-6CELL-002",
            "rack_side": "B",
            "exchange_zone": "FULL_BOX_EXCHANGE_ZONE_A",
            "full_box_object_keys": [],
            "remaining_object_keys": ["PKG-PIECE-002"],
        }
    )

    with TestClient(wms_mock_server.app) as client:
        response = _typed_submit(
            client,
            path="/api/wms/fulfillment/full-box-exchange",
            operation_identity="wms.fulfillment.full_box_exchange@v1",
            idempotency_key="mock-sorter-full-box-001",
            secret=secret,
            payload={
                "dispatch_key": "mock-sorter-full-box-001",
                "rack_id": "RACK-6CELL-001",
                "empty_box_id": "BOX-EMPTY-001",
                "full_box_id": "BOX-FULL-001",
            },
        )

    assert preview["fulfillment_action"] == "FULL_BOX_EXCHANGE"
    assert preview["batch_key"] == "RACK-6CELL-001:A"
    assert preview["station_admission_blocked_until_exchange_completed"] is True
    assert preview["box_level_inventory_transaction_required"] is True
    assert preview["completion_policy"] == "CALLBACK_AND_RECONCILIATION_REQUIRED"
    assert preview["sorting_candidate_object_keys"] == ["PKG-PIECE-001"]
    assert not set(preview["full_box_object_keys"]) & set(preview["sorting_candidate_object_keys"])
    assert no_exchange_preview["fulfillment_action"] == "SORTER_STATION_ADMISSION"
    assert no_exchange_preview["station_admission_blocked_until_exchange_completed"] is False
    assert no_exchange_preview["box_level_inventory_transaction_required"] is False
    assert no_exchange_preview["sorting_candidate_object_keys"] == ["PKG-PIECE-002"]

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["environment"] == "LOCAL_MOCK_ONLY"
    assert data["production_write_path"] is False
    assert data["rack_id"] == "RACK-6CELL-001"
    assert data["northbound_status"]["state"] == "ACCEPTED"


def test_sorter_inbound_mock_acceptance_keeps_change_rack_face_as_independent_fulfillment() -> None:
    """CHANGE_RACK_FACE 是独立履约，不能被 full-box exchange 成功吞并。"""

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/fulfillment/change-rack-face",
            json={
                "request_id": "mock-sorter-change-face-001",
                "parent_request_id": "mock-sorter-full-box-001",
                "rack_code": "RACK-6CELL-001",
                "from_rack_side": "A",
                "to_rack_side": "B",
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["environment"] == "LOCAL_MOCK_ONLY"
    assert data["production_write_path"] is False
    assert data["fulfillment_action"] == "CHANGE_RACK_FACE"
    assert data["parent_request_id"] == "mock-sorter-full-box-001"
    assert data["independent_fulfillment"] is True
    assert data["does_not_mark_full_box_exchange_completed"] is True


def test_sorter_inbound_mock_acceptance_uses_ecs_mock_without_production_callback(monkeypatch) -> None:
    """ECS mock 可以本机闭环 ACK/RESULT callback，但只打 localhost WES callback。"""

    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", _CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-MATERIAL-FLOW-MOCK-001",
                "task_type": "MOVE_FORWARD",
                "params": {"queue_code": "ROUGH_SORTER_OUTBOUND"},
            },
        )

    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert _CapturingAsyncClient.requests
    assert _CapturingAsyncClient.requests[0]["url"].startswith("http://localhost:8001/")


def test_reconciliation_mock_acceptance_models_conflicts_locally() -> None:
    """SMT/NG/WMS 对账 mock 表达冲突场景，但不推进生产写路径。"""

    scenarios = {
        "NG_EVIDENCE": "RECONCILING",
        "MISSING_LOCAL_PHYSICAL_FACT": "RECONCILING",
        "WMS_REJECT": "RECONCILING",
        "TARGET_BIN_WRITEBACK_FAILED": "RECONCILING",
        "DUPLICATE_CALLBACK": "IDEMPOTENT_DUPLICATE",
        "OUT_OF_ORDER_CALLBACK": "RECONCILING",
        "SOURCE_VERSION_DRIFT": "RECONCILING",
    }

    with TestClient(wms_mock_server.app) as client:
        for scenario, expected_state in scenarios.items():
            response = client.post(
                "/api/wms/reconciliation/snapshot",
                json={
                    "scenario": scenario,
                    "object_type": "PACKAGE",
                    "object_key": "PKG-CAP001-LOT-A-001",
                    "source_event_id": f"mock-{scenario.lower()}",
                    "source_version": "mock-wms.v2",
                },
            )

            assert response.status_code == 200
            data = response.json()["data"]
            assert data["conflict_state"] == expected_state
            assert data["environment"] == "LOCAL_MOCK_ONLY"
            assert data["production_write_path"] is False


def test_reconciliation_mock_acceptance_runtime_hold_release_is_scope_only() -> None:
    """RuntimeHold 人工解除只能释放声明 scope，不得顺手放行整线 effect。"""

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/reconciliation/runtime-hold-release-preview",
            json={
                "hold_id": "HOLD-MOCK-001",
                "scope_type": "OBJECT",
                "scope_key": "PKG-CAP001-LOT-A-001",
                "allowed_next_effect_scope": "OBJECT_ONLY",
                "requested_release_scope": "WORKLINE",
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["environment"] == "LOCAL_MOCK_ONLY"
    assert data["production_write_path"] is False
    assert data["released_effect_scopes"] == ["OBJECT"]
    assert data["blocked_effect_scopes"] == ["WORKLINE", "QUEUE", "DEVICE", "RESOURCE"]
    assert data["requires_manual_review"] is True
