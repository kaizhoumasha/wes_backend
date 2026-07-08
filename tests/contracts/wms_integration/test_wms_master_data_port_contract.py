"""WmsMasterDataPort contract test。"""

from __future__ import annotations

import pytest

from src.app.wms_integration.ports.master_data import (
    WmsMasterDataItem,
    WmsMasterDataPort,
)


class _FakeWmsMasterDataPort:
    """最小 WmsMasterDataPort stub 实现 (用于 contract test)。"""

    def __init__(self) -> None:
        self.materials: dict[str, WmsMasterDataItem] = {
            "M001": WmsMasterDataItem(
                material_code="M001",
                material_name="Material 001",
                unit="EA",
                batch_managed=True,
            ),
            "M002": WmsMasterDataItem(
                material_code="M002",
                material_name="Material 002",
                unit="KG",
            ),
        }

    def get_material(self, material_code: str) -> WmsMasterDataItem:
        if material_code not in self.materials:
            raise KeyError(f"material_code={material_code} 不存在")
        return self.materials[material_code]

    def list_materials(self, *, batch_managed: bool | None = None) -> list[WmsMasterDataItem]:
        items = list(self.materials.values())
        if batch_managed is not None:
            items = [i for i in items if i.batch_managed == batch_managed]
        return items


def test_wms_master_data_port_is_protocol():
    """WmsMasterDataPort 是 Protocol (Duck typing), capability 注入只拿 Port.method 接口。"""
    assert hasattr(WmsMasterDataPort, "get_material")
    assert hasattr(WmsMasterDataPort, "list_materials")


def test_wms_master_data_get_material_returns_item():
    """get_material 返回 WmsMasterDataItem, 含 5 字段 (material_code/name/unit/batch/serial)。"""
    port: WmsMasterDataPort = _FakeWmsMasterDataPort()
    item = port.get_material("M001")
    assert item.material_code == "M001"
    assert item.material_name == "Material 001"
    assert item.unit == "EA"
    assert item.batch_managed is True
    assert item.serial_managed is False


def test_wms_master_data_get_material_raises_for_missing():
    """get_material 不存在时抛 KeyError, capability 调用方需处理。"""
    port: WmsMasterDataPort = _FakeWmsMasterDataPort()
    with pytest.raises(KeyError, match="M999"):
        port.get_material("M999")


def test_wms_master_data_list_materials_all():
    """list_materials 不传过滤参数返回全部。"""
    port: WmsMasterDataPort = _FakeWmsMasterDataPort()
    items = port.list_materials()
    assert len(items) == 2
    codes = {i.material_code for i in items}
    assert codes == {"M001", "M002"}


def test_wms_master_data_list_materials_filter_batch_managed():
    """list_materials(batch_managed=True) 仅返回批次管理物料。"""
    port: WmsMasterDataPort = _FakeWmsMasterDataPort()
    items = port.list_materials(batch_managed=True)
    assert len(items) == 1
    assert items[0].material_code == "M001"


def test_wms_master_data_item_extra_forbid():
    """WmsMasterDataItem extra='forbid' 阻断未声明字段 (H4 一致, 防止 PII/ID 注入)。"""
    with pytest.raises(ValueError):
        WmsMasterDataItem(
            material_code="M001",
            material_name="Test",
            unit="EA",
            unknown_field="x",  # type: ignore[call-arg]
        )
