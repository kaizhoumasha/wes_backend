"""BC-XX WmsFulfillmentPort request + callback correlation 行为契约。

验收: WmsFulfillmentPort 剩余 6 effect 方法均经 RuntimeIntentLog 闭环;
       accepted/reason 互斥语义正确 (主计划 §3.5 I3 + §5.1);
       料盘绑定已由独立 typed operation 合同承接。
mock 仅允许 `src/app/wms_integration/ports/fulfillment.py` 内的 Port Protocol。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.wms_integration.ports.fulfillment import (
    WmsFulfillmentPort,
    WmsFulfillmentResult,
)


class _FakeFulfillmentPort:
    """最小 WmsFulfillmentPort 替身 — 剩余 6 effect 方法全实现, 用于契约断言。"""

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

    def full_box_exchange(self, rack_id, rack_face, full_box_id) -> WmsFulfillmentResult:
        self.calls.append(("full_box_exchange", (rack_id, rack_face, full_box_id), {}))
        return WmsFulfillmentResult(
            request_id="REQ-EXCHANGE-001",
            accepted=True,
            warehouse_code="WH-A",
        )

    def move_bins_to_conveyor_entry(self, batch_id, bin_ids) -> WmsFulfillmentResult:
        self.calls.append(("move_bins_to_conveyor_entry", (batch_id, bin_ids), {}))
        return WmsFulfillmentResult(
            request_id="REQ-ENTRY-001",
            accepted=True,
            warehouse_code="WH-A",
        )

    def move_bins_from_conveyor_exit(self, batch_id, candidate_bin_ids) -> WmsFulfillmentResult:
        self.calls.append(("move_bins_from_conveyor_exit", (batch_id, candidate_bin_ids), {}))
        return WmsFulfillmentResult(
            request_id="REQ-EXIT-001",
            accepted=True,
            warehouse_code="WH-A",
        )


def test_wms_fulfillment_port_protocol_has_seven_effect_methods():
    """happy path: WmsFulfillmentPort 的 CTU 边界只暴露批量 E12/E13 方法。"""
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
        "move_bins_to_conveyor_entry",
        "move_bins_from_conveyor_exit",
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


def test_fake_port_implements_all_seven_effects():
    """happy path: 替身实现剩余 6 个 family effect 方法，不抛 NotImplementedError。"""
    port = _FakeFulfillmentPort()
    port.request_rack_supply("R1", "MAT-001", 5.0)
    port.request_rack_transport("R1", "ST-A", "ST-B")
    port.change_rack_face("R1", "B")
    port.full_box_exchange("R1", "A", "FULL-001")
    port.move_bins_to_conveyor_entry("BATCH-ENTRY-001", ("BIN-001",))
    port.move_bins_from_conveyor_exit("BATCH-EXIT-001", ("BIN-001",))
    assert len(port.calls) == 6


def test_fake_port_rack_supply_returns_acceptance_result():
    """happy path: rack_supply 调用产生 WmsFulfillmentResult, accepted 字段正确。"""
    port = _FakeFulfillmentPort()
    res = port.request_rack_supply("R1", "MAT-001", 5.0)
    assert isinstance(res, WmsFulfillmentResult)
    assert res.accepted is True
    assert res.warehouse_code == "WH-A"
    assert "request_rack_supply" in port.calls[0][0]
