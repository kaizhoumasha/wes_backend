"""WmsInventoryQueryPort contract test。"""

from __future__ import annotations

import pytest

from src.app.wms_integration.ports.inventory_query import (
    WmsInventoryItem,
    WmsInventoryQueryPort,
)


class _FakeWmsInventoryQueryPort:
    def __init__(self) -> None:
        self.items: list[WmsInventoryItem] = [
            WmsInventoryItem(
                material_code="M001",
                warehouse_code="WH-A",
                storage_location_code="BIN-01",
                quantity=100.0,
                batch_no="B-2026-01",
            ),
            WmsInventoryItem(
                material_code="M001",
                warehouse_code="WH-B",
                storage_location_code="BIN-02",
                quantity=50.0,
            ),
            WmsInventoryItem(
                material_code="M002",
                warehouse_code="WH-A",
                storage_location_code="BIN-03",
                quantity=200.0,
            ),
        ]

    async def query_inventory(
        self,
        material_code: str,
        *,
        warehouse_code: str | None = None,
    ) -> list[WmsInventoryItem]:
        items = [i for i in self.items if i.material_code == material_code]
        if warehouse_code is not None:
            items = [i for i in items if i.warehouse_code == warehouse_code]
        return items

    def query_empty_bins(
        self,
        warehouse_code: str,
        *,
        zone_code: str | None = None,
    ) -> list[str]:
        # 简化: 每仓库返回固定 2 个空库位
        all_bins = {
            "WH-A": ["BIN-10", "BIN-11"],
            "WH-B": ["BIN-20", "BIN-21"],
        }
        bins = list(all_bins.get(warehouse_code, []))
        if zone_code is not None:
            bins = [b for b in bins if b.startswith(zone_code)]
        return bins


def test_wms_inventory_query_port_is_protocol():
    """WmsInventoryQueryPort 是 Protocol (Duck typing)。"""
    assert hasattr(WmsInventoryQueryPort, "query_inventory")
    assert hasattr(WmsInventoryQueryPort, "query_empty_bins")


@pytest.mark.asyncio
async def test_wms_inventory_query_returns_all_warehouses():
    """query_inventory 不传 warehouse_code 返回所有仓库同物料库存。"""
    port: WmsInventoryQueryPort = _FakeWmsInventoryQueryPort()
    items = await port.query_inventory("M001")
    assert len(items) == 2
    codes = {i.warehouse_code for i in items}
    assert codes == {"WH-A", "WH-B"}


@pytest.mark.asyncio
async def test_wms_inventory_query_filters_by_warehouse():
    """query_inventory(warehouse_code=...) 仅返回指定仓库。"""
    port: WmsInventoryQueryPort = _FakeWmsInventoryQueryPort()
    items = await port.query_inventory("M001", warehouse_code="WH-A")
    assert len(items) == 1
    assert items[0].warehouse_code == "WH-A"
    assert items[0].storage_location_code == "BIN-01"
    assert items[0].batch_no == "B-2026-01"


@pytest.mark.asyncio
async def test_wms_inventory_query_returns_empty_for_unknown_material():
    """query_inventory 不存在物料返回空列表 (不是异常, 业务可判断空)."""
    port: WmsInventoryQueryPort = _FakeWmsInventoryQueryPort()
    assert await port.query_inventory("UNKNOWN-M") == []


def test_wms_inventory_query_empty_bins_by_warehouse():
    """query_empty_bins 按 warehouse 返回空库位列表。"""
    port: WmsInventoryQueryPort = _FakeWmsInventoryQueryPort()
    bins = port.query_empty_bins("WH-A")
    assert bins == ["BIN-10", "BIN-11"]


def test_wms_inventory_query_empty_bins_filters_by_zone():
    """query_empty_bins(zone_code=...) 按 zone 前缀过滤。"""
    port: WmsInventoryQueryPort = _FakeWmsInventoryQueryPort()
    bins = port.query_empty_bins("WH-A", zone_code="BIN-1")
    assert bins == ["BIN-10", "BIN-11"]


def test_wms_inventory_item_extra_forbid():
    """WmsInventoryItem extra='forbid' 阻断未声明字段 (H4 一致, 防止 ID/owner 注入)。"""
    with pytest.raises(ValueError):
        WmsInventoryItem(
            material_code="M001",
            warehouse_code="WH-A",
            storage_location_code="BIN-01",
            quantity=100.0,
            owner_user_id="u-internal",  # type: ignore[call-arg]
        )
