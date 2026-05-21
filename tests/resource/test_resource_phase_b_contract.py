"""Phase B resource 域破坏性裁剪合同测试。"""

from __future__ import annotations

from typing import Any, cast


def _columns(model: type[Any]) -> set[str]:
    return set(cast("Any", model).__table__.c.keys())


def test_phase_b_resource_type_is_only_resource_objects() -> None:
    """ResourceType 只保留 resource 域主对象，流程上下文不再作为资源类型。"""

    from src.app.resource.models import ResourceType

    assert {item.value for item in ResourceType} == {"RACK", "BIN", "MATERIAL"}


def test_phase_b_removes_obsolete_resource_exports() -> None:
    """迁出或删除的旧 resource 职责不应再从 resource.models 导出。"""

    from src.app.resource import models

    removed_names = {
        "ExecutionZone",
        "ExecutionLocation",
        "RackMaterialMount",
        "ResourceRelationSourceSystem",
        "RackRelease",
        "RackReleaseBinSnapshot",
        "FullBoxExchangeTask",
        "WmsWritebackEvidence",
    }

    for name in removed_names:
        assert not hasattr(models, name), name
        assert name not in models.__all__


def test_phase_b_rack_and_bin_master_data_do_not_store_runtime_location() -> None:
    """货架/料箱主数据不保存当前位置或现场确认时间。"""

    from src.app.resource.models import Bin, Rack, ResourceMasterStatus

    assert {"current_location_code", "last_seen_at"}.isdisjoint(_columns(Rack))
    assert {"last_seen_at"}.isdisjoint(_columns(Bin))
    assert Rack.__table__.c.status.type.enum_class is ResourceMasterStatus
    assert Bin.__table__.c.status.type.enum_class is ResourceMasterStatus


def test_phase_b_projection_source_system_uses_unified_resource_source_system() -> None:
    """active 投影统一使用 ResourceSourceSystem，不再维护 ResourceRelationSourceSystem。"""

    from src.app.resource.models import RackBinMount, ResourceSourceSystem

    assert RackBinMount.__table__.c.source_system.type.enum_class is ResourceSourceSystem


def test_phase_b_snapshot_items_use_bin_cell_not_legacy_slot() -> None:
    """料箱内容快照明细使用 bin_cell 字段，不再暴露旧 bin_slot_code。"""

    from src.app.resource.models import BinContentSnapshotItem

    columns = _columns(BinContentSnapshotItem)
    assert "bin_slot_code" not in columns
    assert {"bin_cell_code", "bin_cell_index"}.issubset(columns)
