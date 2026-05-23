"""WES 运行时资源底座测试。"""

from typing import Any, cast

from sqlmodel import SQLModel


def _table_columns(model: type[Any]) -> set[str]:
    return set(cast("Any", model).__table__.c.keys())


def _schema_fields(schema: type[Any]) -> set[str]:
    return set(cast("Any", schema).model_fields)


RESOURCE_STATE_COLUMNS = {"version", "created_by", "updated_by", "deleted_by", "deleted_at", "is_deleted"}


def test_resource_ref_covers_wes_runtime_resource_types() -> None:
    """ResourceRef 只覆盖 Phase B 后保留的资源主对象类型。"""

    from src.app.resource.models import ResourceRef, ResourceType

    assert {item.value for item in ResourceType} == {"RACK", "BIN", "MATERIAL"}

    ref = ResourceRef(resource_type=ResourceType.RACK, resource_code="RACK-001", source_version="v1")

    assert ref.resource_type == ResourceType.RACK
    assert ref.resource_code == "RACK-001"
    assert ref.source_version == "v1"


def test_first_stage_resource_tables_are_registered_with_required_fields() -> None:
    """资源主数据表必须进入 SQLModel metadata，并包含槽位承载类型字段。"""

    from src.app.resource.models import (
        Bin,
        BinSlotTemplate,
        BinType,
        Rack,
        RackSlotKind,
        RackSlotTemplate,
        RackType,
    )

    expected_tables = {
        "wes_biz.resource_rack_types",
        "wes_biz.resource_rack_slot_templates",
        "wes_biz.resource_racks",
        "wes_biz.resource_bin_types",
        "wes_biz.resource_bin_slot_templates",
        "wes_biz.resource_bins",
    }

    assert expected_tables.issubset(SQLModel.metadata.tables)
    assert RackSlotKind.BIN_SLOT.value == "BIN_SLOT"
    assert RackSlotKind.MATERIAL_SLOT.value == "MATERIAL_SLOT"
    assert RackSlotTemplate.__table__.c.slot_kind is not None

    for model in (RackType, RackSlotTemplate, Rack, BinType, BinSlotTemplate, Bin):
        assert model.__schema__ == "wes_biz"


def test_second_stage_resource_fact_and_projection_tables_are_registered() -> None:
    """第二阶段资源事实账本与当前投影表必须进入 SQLModel metadata。"""

    from src.app.resource.models import (
        BinCellOccupancy,
        BinCellOccupancyStatus,
        BinMaterialMount,
        RackBinMount,
        RackPlacement,
        ResourceStateEvent,
        ResourceStateEventType,
    )

    expected_tables = {
        "wes_biz.resource_state_events",
        "wes_biz.resource_rack_placements",
        "wes_biz.resource_rack_bin_mounts",
        "wes_biz.resource_bin_cell_occupancies",
        "wes_biz.resource_bin_material_mounts",
    }

    assert expected_tables.issubset(SQLModel.metadata.tables)
    assert ResourceStateEventType.RACK_ARRIVED.value == "RACK_ARRIVED"
    assert ResourceStateEvent.__table__.c.source_event_id.nullable is False
    assert RackPlacement.__table__.c.ended_at.nullable is True
    assert RackBinMount.__table__.c.bin_code.nullable is False
    assert BinCellOccupancyStatus.OCCUPIED.value == "OCCUPIED"
    assert BinCellOccupancy.__table__.c.reel_count.nullable is False
    assert BinMaterialMount.__table__.c.cell_stack_position.nullable is False
    assert BinMaterialMount.__table__.c.material_identity_key.nullable is False


def test_phase_b_removed_resource_tables_are_not_registered() -> None:
    """Phase B 后迁出/删除的旧 resource 职责不再注册为 resource 表。"""

    removed_tables = {
        "wes_biz.resource_execution_zones",
        "wes_biz.resource_execution_locations",
        "wes_biz.resource_rack_material_mounts",
        "wes_biz.resource_wms_writeback_evidence",
        "wes_biz.resource_rack_releases",
        "wes_biz.resource_rack_release_bin_snapshots",
        "wes_biz.resource_full_box_exchange_tasks",
    }

    assert removed_tables.isdisjoint(SQLModel.metadata.tables)


def test_bin_content_snapshot_tables_are_registered() -> None:
    """料箱内容快照头和明细仍属于 resource 域。"""

    from src.app.resource.models import (
        BinContentSnapshot,
        BinContentSnapshotItem,
        BinContentSnapshotStatus,
    )

    expected_tables = {
        "wes_biz.resource_bin_content_snapshots",
        "wes_biz.resource_bin_content_snapshot_items",
    }

    assert expected_tables.issubset(SQLModel.metadata.tables)
    assert BinContentSnapshotStatus.COMPLETE.value == "COMPLETE"
    assert BinContentSnapshot.__table__.c.snapshot_hash.nullable is False
    assert BinContentSnapshotItem.__table__.c.snapshot_id.nullable is False


def test_resource_table_models_do_not_use_enterprise_or_soft_delete_mixins() -> None:
    """资源域表模型不应携带人工审计、乐观锁或软删除字段。"""

    from src.app.resource.models import (
        Bin,
        BinCellOccupancy,
        BinContentSnapshot,
        BinContentSnapshotItem,
        BinMaterialMount,
        BinSlotTemplate,
        BinType,
        Rack,
        RackBinMount,
        RackPlacement,
        RackSlotTemplate,
        RackType,
        ResourceStateEvent,
    )

    for model in (
        RackType,
        RackSlotTemplate,
        Rack,
        BinType,
        BinSlotTemplate,
        Bin,
        ResourceStateEvent,
        RackPlacement,
        RackBinMount,
        BinCellOccupancy,
        BinMaterialMount,
        BinContentSnapshot,
        BinContentSnapshotItem,
    ):
        assert RESOURCE_STATE_COLUMNS.isdisjoint(_table_columns(model)), model.__name__


def test_resource_schemas_do_not_expose_optimistic_version() -> None:
    """资源域 Schema 不应暴露乐观锁 version。"""

    from src.app.resource.models import (
        BinCellOccupancyResponse,
        BinCellOccupancyUpdate,
        BinContentSnapshotCreate,
        BinContentSnapshotItemResponse,
        BinContentSnapshotItemUpdate,
        BinContentSnapshotResponse,
        BinContentSnapshotUpdate,
        BinCreate,
        BinMaterialMountResponse,
        BinMaterialMountUpdate,
        BinResponse,
        BinSlotTemplateCreate,
        BinSlotTemplateResponse,
        BinSlotTemplateUpdate,
        BinTypeCreate,
        BinTypeResponse,
        BinTypeUpdate,
        BinUpdate,
        RackBinMountResponse,
        RackBinMountUpdate,
        RackCreate,
        RackPlacementResponse,
        RackPlacementUpdate,
        RackResponse,
        RackSlotTemplateCreate,
        RackSlotTemplateResponse,
        RackSlotTemplateUpdate,
        RackTypeCreate,
        RackTypeResponse,
        RackTypeUpdate,
        RackUpdate,
        ResourceStateEventResponse,
        ResourceStateEventUpdate,
    )

    for schema in (
        RackTypeCreate,
        RackTypeUpdate,
        RackTypeResponse,
        RackSlotTemplateCreate,
        RackSlotTemplateUpdate,
        RackSlotTemplateResponse,
        RackCreate,
        RackUpdate,
        RackResponse,
        BinTypeCreate,
        BinTypeUpdate,
        BinTypeResponse,
        BinSlotTemplateCreate,
        BinSlotTemplateUpdate,
        BinSlotTemplateResponse,
        BinCreate,
        BinUpdate,
        BinResponse,
        ResourceStateEventUpdate,
        ResourceStateEventResponse,
        RackPlacementUpdate,
        RackPlacementResponse,
        RackBinMountUpdate,
        RackBinMountResponse,
        BinCellOccupancyUpdate,
        BinCellOccupancyResponse,
        BinMaterialMountUpdate,
        BinMaterialMountResponse,
        BinContentSnapshotCreate,
        BinContentSnapshotUpdate,
        BinContentSnapshotResponse,
        BinContentSnapshotItemUpdate,
        BinContentSnapshotItemResponse,
    ):
        assert "version" not in _schema_fields(schema), schema.__name__


def test_resource_v1_router_exposes_readonly_routes() -> None:
    """资源域只暴露查询/详情，写入必须走同步或领域服务。"""

    from src.app.resource import router_v1

    paths = {route.path for route in router_v1.routes}
    route_methods = {(route.path, method) for route in router_v1.routes for method in getattr(route, "methods", set())}
    readonly_prefixes = (
        "/v1/resource/rack-types",
        "/v1/resource/rack-slot-templates",
        "/v1/resource/racks",
        "/v1/resource/bin-types",
        "/v1/resource/bin-slot-templates",
        "/v1/resource/bins",
        "/v1/resource/state-events",
        "/v1/resource/rack-placements",
        "/v1/resource/rack-bin-mounts",
        "/v1/resource/bin-cell-occupancies",
        "/v1/resource/bin-material-mounts",
        "/v1/resource/bin-content-snapshots",
        "/v1/resource/bin-content-snapshot-items",
    )

    for prefix in readonly_prefixes:
        assert f"{prefix}/query" in paths
        assert f"{prefix}/{{id}}" in paths
        assert (prefix, "POST") not in route_methods
        assert (f"{prefix}/{{id}}", "PUT") not in route_methods
        assert (f"{prefix}/{{id}}", "DELETE") not in route_methods
