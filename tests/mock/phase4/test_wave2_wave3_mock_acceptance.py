"""Phase 4 Wave2/Wave3 本机 MOCK 验收。

这些测试只验证本机 mock 能表达目标合同，不代表生产热路径接入。
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


def test_wave2_mock_acceptance_separates_pkg_binding_from_inventory_transaction() -> None:
    """PKG binding 走 fulfillment mock，库存事务走 inventory mock。"""

    with TestClient(wms_mock_server.app) as client:
        binding_response = client.post(
            "/api/wms/fulfillment/pkg-binding",
            json={
                "request_id": "mock-wave2-binding-001",
                "package_id": "PKG-CAP001-LOT-A-001",
                "pallet_id": "PALLET-A",
                "station_code": "SORTER-STATION-A",
            },
        )
        inventory_response = client.post(
            "/api/wms/inbound/confirm",
            json={
                "request_id": "mock-wave2-inbound-001",
                "inbound_key": "INBOUND-PKG-CAP001-LOT-A-001",
            },
        )

    assert binding_response.status_code == 200
    assert binding_response.json()["data"] == {
        "request_id": "mock-wave2-binding-001",
        "binding_key": "PKG-CAP001-LOT-A-001:PALLET-A:SORTER-STATION-A",
        "package_id": "PKG-CAP001-LOT-A-001",
        "pallet_id": "PALLET-A",
        "station_code": "SORTER-STATION-A",
        "accepted": True,
    }
    assert inventory_response.status_code == 200
    assert inventory_response.json()["data"]["confirmed"] is True


def test_wave2_mock_acceptance_uses_ecs_mock_without_production_callback(monkeypatch) -> None:
    """ECS mock 可以本机闭环 ACK/RESULT callback，但只打 localhost WES callback。"""

    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", _CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-PHASE4-MOCK-001",
                "task_type": "MOVE_FORWARD",
                "params": {"queue_code": "ROUGH_SORTER_OUTBOUND"},
            },
        )

    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert _CapturingAsyncClient.requests
    assert _CapturingAsyncClient.requests[0]["url"].startswith("http://localhost:8001/")


def test_wave3_mock_acceptance_models_reconciliation_conflicts_locally() -> None:
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
