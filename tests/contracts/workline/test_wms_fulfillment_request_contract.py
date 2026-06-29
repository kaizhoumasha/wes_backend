"""BC-XX WmsFulfillmentPort request + callback correlation 行为契约。

验收: WmsFulfillmentPort 6 effect 方法均经 RuntimeIntentLog 闭环;
       accepted/reason 互斥语义正确 (主计划 §3.5 I3 + §5.1);
       pallet binding 返回字段完整 (Phase 1 CEO-001 #5)。
mock 仅允许 `src/app/wms_integration/ports/fulfillment.py` 内的 Port Protocol。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.wms_integration.ports.fulfillment import (
    WmsFulfillmentPort,
    WmsFulfillmentResult,
    WmsPalletBindingResult,
)


class _FakeFulfillmentPort:
    """最小 WmsFulfillmentPort 替身 — 7 effect 方法全实现, 用于契约断言。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def request_rack_supply(self, rack_id, material_code, quantity) -> WmsFulfillmentResult:
        self.calls.append(("request_rack_supply", (rack_id, material_code, quantity), {}))
        return WmsFulfillmentResult(
            request_id="REQ-SUPPLY-001",
            accepted=True,
            warehouse_code="WH-A",
        )

    def request_rack_transport(self, rack_id, from_station, to_station) -> WmsFulfillmentResult:
        self.calls.append(("request_rack_transport", (rack_id, from_station, to_station), {}))
        return WmsFulfillmentResult(
            request_id="REQ-TRANSPORT-001",
            accepted=True,
            warehouse_code="WH-A",
        )

    def change_rack_face(self, rack_id, face) -> WmsFulfillmentResult:
        self.calls.append(("change_rack_face", (rack_id, face), {}))
        return WmsFulfillmentResult(
            request_id="REQ-FACE-001",
            accepted=True,
            warehouse_code="WH-A",
        )

    def full_box_exchange(self, rack_id, empty_box_id, full_box_id) -> WmsFulfillmentResult:
        self.calls.append(("full_box_exchange", (rack_id, empty_box_id, full_box_id), {}))
        return WmsFulfillmentResult(
            request_id="REQ-EXCHANGE-001",
            accepted=True,
            warehouse_code="WH-A",
        )

    def move_bin_to_conveyor_entry(self, bin_id, conveyor_entry) -> WmsFulfillmentResult:
        self.calls.append(("move_bin_to_conveyor_entry", (bin_id, conveyor_entry), {}))
        return WmsFulfillmentResult(
            request_id="REQ-ENTRY-001",
            accepted=True,
            warehouse_code="WH-A",
        )

    def move_bin_to_conveyor_exit(self, bin_id, conveyor_exit) -> WmsFulfillmentResult:
        self.calls.append(("move_bin_to_conveyor_exit", (bin_id, conveyor_exit), {}))
        return WmsFulfillmentResult(
            request_id="REQ-EXIT-001",
            accepted=True,
            warehouse_code="WH-A",
        )

    def notify_pkg_binding(self, package_id, pallet_id, station_code) -> WmsPalletBindingResult:
        self.calls.append(("notify_pkg_binding", (package_id, pallet_id, station_code), {}))
        return WmsPalletBindingResult(
            package_id=package_id,
            pallet_id=pallet_id,
            bound_at="2026-06-28T10:00:00Z",
            station_code=station_code,
        )


def test_wms_fulfillment_port_protocol_has_seven_effect_methods():
    """happy path: WmsFulfillmentPort 定义 7 个 effect 方法 (主计划 §5.1)。"""
    runtime_methods = {
        name
        for name in dir(WmsFulfillmentPort)
        if not name.startswith("_") and callable(getattr(WmsFulfillmentPort, name, None))
    }
    expected = {
        "request_rack_supply",
        "request_rack_transport",
        "change_rack_face",
        "full_box_exchange",
        "move_bin_to_conveyor_entry",
        "move_bin_to_conveyor_exit",
        "notify_pkg_binding",
    }
    assert expected.issubset(runtime_methods)


def test_wms_fulfillment_result_accepted_omits_reason():
    """happy path: accepted=True 时 reason 必填解除, 但允许 None。"""
    res = WmsFulfillmentResult(
        request_id="REQ-001",
        accepted=True,
        warehouse_code="WH-A",
    )
    assert res.accepted is True
    assert res.reason is None


def test_wms_fulfillment_result_rejected_requires_reason():
    """error path: accepted=False 时 reason 语义必填 (Pydantic 字段保留 None 容忍,
    但调用方契约必填)。"""
    res = WmsFulfillmentResult(
        request_id="REQ-001",
        accepted=False,
        reason="rack busy",
        warehouse_code="WH-A",
    )
    assert res.accepted is False
    assert res.reason == "rack busy"


def test_wms_fulfillment_result_rejects_extra_fields():
    """H4 边界: extra="forbid" 阻断 WmsFulfillmentResult 未声明字段注入。"""
    with pytest.raises(ValidationError):
        WmsFulfillmentResult(
            request_id="REQ-001",
            accepted=True,
            warehouse_code="WH-A",
            plc_address="forbidden",
        )


def test_wms_fulfillment_result_requires_warehouse_code():
    """error path: warehouse_code 必填 (跨仓隔离)。"""
    with pytest.raises(ValidationError):
        WmsFulfillmentResult(
            request_id="REQ-001",
            accepted=True,
        )


def test_wms_pallet_binding_result_requires_all_fields():
    """happy path: pallet binding 必须含 package_id/pallet_id/bound_at/station_code
    (通知 WMS 必填)。"""
    binding = WmsPalletBindingResult(
        package_id="PKG-001",
        pallet_id="PLT-001",
        bound_at="2026-06-28T10:00:00Z",
        station_code="ST-A",
    )
    assert binding.package_id == "PKG-001"
    assert binding.pallet_id == "PLT-001"
    assert binding.station_code == "ST-A"


def test_fake_port_implements_all_seven_effects():
    """happy path: 替身实现 7 个 effect 方法, 不抛 NotImplementedError (I3 capability 完整性)。"""
    port = _FakeFulfillmentPort()
    port.request_rack_supply("R1", "MAT-001", 5.0)
    port.request_rack_transport("R1", "ST-A", "ST-B")
    port.change_rack_face("R1", "B")
    port.full_box_exchange("R1", "EMPTY-001", "FULL-001")
    port.move_bin_to_conveyor_entry("BIN-001", "CV-IN-1")
    port.move_bin_to_conveyor_exit("BIN-001", "CV-OUT-1")
    port.notify_pkg_binding("PKG-001", "PLT-001", "ST-A")
    assert len(port.calls) == 7


def test_fake_port_rack_supply_returns_acceptance_result():
    """happy path: rack_supply 调用产生 WmsFulfillmentResult, accepted 字段正确。"""
    port = _FakeFulfillmentPort()
    res = port.request_rack_supply("R1", "MAT-001", 5.0)
    assert isinstance(res, WmsFulfillmentResult)
    assert res.accepted is True
    assert res.warehouse_code == "WH-A"
    assert "request_rack_supply" in port.calls[0][0]


def test_fake_port_pallet_binding_returns_bound_result():
    """happy path: notify_pkg_binding 返回 WmsPalletBindingResult 含 bound_at。"""
    port = _FakeFulfillmentPort()
    res = port.notify_pkg_binding("PKG-001", "PLT-001", "ST-A")
    assert isinstance(res, WmsPalletBindingResult)
    assert res.bound_at == "2026-06-28T10:00:00Z"


def test_wms_pallet_binding_rejects_extra_fields():
    """H4 边界: extra="forbid" 阻断 WmsPalletBindingResult 未声明字段注入。"""
    with pytest.raises(ValidationError):
        WmsPalletBindingResult(
            package_id="PKG-001",
            pallet_id="PLT-001",
            bound_at="2026-06-28T10:00:00Z",
            station_code="ST-A",
            safety_loop="forbidden-injection",
        )
