"""WmsInventoryTransactionPort contract test。"""

from __future__ import annotations

import pytest

from src.app.wms_integration.ports.inventory_transaction import (
    WmsInventoryTransactionPort,
    WmsReservationResult,
    WmsTransferResult,
)


class _FakeWmsInventoryTransactionPort:
    def __init__(self) -> None:
        self.reservations: dict[str, WmsReservationResult] = {}
        self.transfers: list[WmsTransferResult] = []
        self.next_doc_id = 1000

    def reserve_inventory(self, material_code: str, quantity: float, warehouse_code: str) -> WmsReservationResult:
        rid = f"RES-{material_code}-{warehouse_code}-{len(self.reservations) + 1}"
        result = WmsReservationResult(
            reservation_id=rid,
            material_code=material_code,
            quantity=quantity,
            warehouse_code=warehouse_code,
        )
        self.reservations[rid] = result
        return result

    def release_reservation(self, reservation_id: str) -> None:
        if reservation_id not in self.reservations:
            raise KeyError(f"reservation_id={reservation_id} 不存在")
        del self.reservations[reservation_id]

    def confirm_inbound(self, material_code: str, quantity: float, warehouse_code: str) -> WmsTransferResult:
        doc_no = f"GRN-{self.next_doc_id}"
        self.next_doc_id += 1
        result = WmsTransferResult(
            document_no=doc_no,
            material_code=material_code,
            quantity=quantity,
            warehouse_code=warehouse_code,
        )
        self.transfers.append(result)
        return result

    def confirm_outbound(self, material_code: str, quantity: float, warehouse_code: str) -> WmsTransferResult:
        doc_no = f"SO-{self.next_doc_id}"
        self.next_doc_id += 1
        result = WmsTransferResult(
            document_no=doc_no,
            material_code=material_code,
            quantity=quantity,
            warehouse_code=warehouse_code,
        )
        self.transfers.append(result)
        return result

    def transfer_inventory(
        self,
        material_code: str,
        quantity: float,
        from_warehouse: str,
        to_warehouse: str,
    ) -> WmsTransferResult:
        doc_no = f"TRF-{self.next_doc_id}"
        self.next_doc_id += 1
        result = WmsTransferResult(
            document_no=doc_no,
            material_code=material_code,
            quantity=quantity,
            warehouse_code=to_warehouse,
        )
        self.transfers.append(result)
        return result


def test_wms_inventory_transaction_port_is_protocol():
    """WmsInventoryTransactionPort 是 Protocol。"""
    assert hasattr(WmsInventoryTransactionPort, "reserve_inventory")
    assert hasattr(WmsInventoryTransactionPort, "release_reservation")
    assert hasattr(WmsInventoryTransactionPort, "confirm_inbound")
    assert hasattr(WmsInventoryTransactionPort, "confirm_outbound")
    assert hasattr(WmsInventoryTransactionPort, "transfer_inventory")


def test_reserve_inventory_creates_reservation():
    """reserve_inventory 返回 WmsReservationResult 含 reservation_id/quantity/warehouse。"""
    port: WmsInventoryTransactionPort = _FakeWmsInventoryTransactionPort()
    result = port.reserve_inventory("M001", 10.0, "WH-A")
    assert result.reservation_id == "RES-M001-WH-A-1"
    assert result.material_code == "M001"
    assert result.quantity == 10.0
    assert result.warehouse_code == "WH-A"


def test_release_reservation_removes_reservation():
    """release_reservation 释放预留; 不存在抛 KeyError。"""
    port: WmsInventoryTransactionPort = _FakeWmsInventoryTransactionPort()
    rid = port.reserve_inventory("M001", 10.0, "WH-A").reservation_id
    port.release_reservation(rid)
    with pytest.raises(KeyError, match=rid):
        port.release_reservation(rid)


def test_confirm_inbound_generates_grn_document():
    """confirm_inbound 产生 GRN 单据号。"""
    port: WmsInventoryTransactionPort = _FakeWmsInventoryTransactionPort()
    result = port.confirm_inbound("M001", 50.0, "WH-A")
    assert result.document_no.startswith("GRN-")
    assert result.material_code == "M001"
    assert result.quantity == 50.0
    assert result.warehouse_code == "WH-A"


def test_confirm_outbound_generates_so_document():
    """confirm_outbound 产生 SO (Sales Order) 单据号。"""
    port: WmsInventoryTransactionPort = _FakeWmsInventoryTransactionPort()
    result = port.confirm_outbound("M001", 30.0, "WH-A")
    assert result.document_no.startswith("SO-")


def test_transfer_inventory_records_from_to_warehouse():
    """transfer_inventory 记录目标 warehouse (单据的 warehouse_code 是 to_warehouse)。"""
    port: WmsInventoryTransactionPort = _FakeWmsInventoryTransactionPort()
    result = port.transfer_inventory("M001", 100.0, "WH-A", "WH-B")
    assert result.document_no.startswith("TRF-")
    assert result.warehouse_code == "WH-B"  # to_warehouse


def test_reservation_result_extra_forbid():
    """WmsReservationResult extra='forbid' 阻断未声明字段 (H4 一致)。"""
    with pytest.raises(ValueError):
        WmsReservationResult(
            reservation_id="R-1",
            material_code="M001",
            quantity=10.0,
            warehouse_code="WH-A",
            tenant_id="internal",  # type: ignore[call-arg]
        )


def test_transfer_result_extra_forbid():
    """WmsTransferResult extra='forbid' 阻断未声明字段。"""
    with pytest.raises(ValueError):
        WmsTransferResult(
            document_no="GRN-1",
            material_code="M001",
            quantity=10.0,
            warehouse_code="WH-A",
            internal_doc_id="x",  # type: ignore[call-arg]
        )
