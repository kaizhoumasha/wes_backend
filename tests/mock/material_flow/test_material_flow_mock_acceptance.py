"""Material-flow 本机 MOCK 验收。

这些测试只验证本机 mock 能表达目标合同，不代表 evidence profile 闭合。
"""

from __future__ import annotations

from typing import ClassVar

from fastapi.testclient import TestClient

from tests.mock import ecs_mock_server, wms_mock_server


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


def test_sorter_inbound_mock_acceptance_separates_pkg_binding_from_inventory_transaction() -> None:
    """PKG binding 走 fulfillment mock，库存事务走 inventory mock。"""

    with TestClient(wms_mock_server.app) as client:
        binding_response = client.post(
            "/api/wms/fulfillment/pkg-binding",
            json={
                "request_id": "mock-sorter-binding-001",
                "package_id": "PKG-CAP001-LOT-A-001",
                "pallet_id": "PALLET-A",
                "station_code": "SORTER-STATION-A",
            },
        )
        inventory_response = client.post(
            "/api/wms/inbound/confirm",
            json={
                "request_id": "mock-sorter-inbound-001",
                "inbound_key": "INBOUND-PKG-CAP001-LOT-A-001",
            },
        )

    assert binding_response.status_code == 200
    assert binding_response.json()["data"] == {
        "request_id": "mock-sorter-binding-001",
        "binding_key": "PKG-CAP001-LOT-A-001:PALLET-A:SORTER-STATION-A",
        "package_id": "PKG-CAP001-LOT-A-001",
        "pallet_id": "PALLET-A",
        "station_code": "SORTER-STATION-A",
        "accepted": True,
    }
    assert inventory_response.status_code == 200
    assert inventory_response.json()["data"]["confirmed"] is True


def test_sorter_inbound_mock_acceptance_models_full_box_pre_diversion_contract() -> None:
    """满箱交换必须在分拣机逐件流程前分流，且已满箱物料不得进入逐件候选集。"""

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/fulfillment/full-box-exchange",
            json={
                "request_id": "mock-sorter-full-box-001",
                "rack_code": "RACK-6CELL-001",
                "rack_side": "A",
                "exchange_zone": "FULL_BOX_EXCHANGE_ZONE_A",
                "full_box_object_keys": ["PKG-FULL-001", "PKG-FULL-002"],
                "remaining_object_keys": ["PKG-PIECE-001"],
            },
        )
        no_exchange_response = client.post(
            "/api/wms/fulfillment/full-box-exchange",
            json={
                "request_id": "mock-sorter-no-full-box-001",
                "rack_code": "RACK-6CELL-002",
                "rack_side": "B",
                "exchange_zone": "FULL_BOX_EXCHANGE_ZONE_A",
                "full_box_object_keys": [],
                "remaining_object_keys": ["PKG-PIECE-002"],
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["environment"] == "LOCAL_MOCK_ONLY"
    assert data["production_write_path"] is False
    assert data["fulfillment_action"] == "FULL_BOX_EXCHANGE"
    assert data["batch_key"] == "RACK-6CELL-001:A"
    assert data["station_admission_blocked_until_exchange_completed"] is True
    assert data["box_level_inventory_transaction_required"] is True
    assert data["sorting_candidate_object_keys"] == ["PKG-PIECE-001"]
    assert not set(data["full_box_object_keys"]) & set(data["sorting_candidate_object_keys"])
    assert no_exchange_response.status_code == 200
    no_exchange_data = no_exchange_response.json()["data"]
    assert no_exchange_data["fulfillment_action"] == "SORTER_STATION_ADMISSION"
    assert no_exchange_data["station_admission_blocked_until_exchange_completed"] is False
    assert no_exchange_data["sorting_candidate_object_keys"] == ["PKG-PIECE-002"]


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
