"""WES 运行时资源底座测试。"""

from sqlmodel import SQLModel


def test_resource_ref_covers_wes_runtime_resource_types() -> None:
    """ResourceRef 必须覆盖计划第一阶段要求的运行时资源类型。"""

    from src.app.resource.models import ResourceRef, ResourceType

    assert {item.value for item in ResourceType} == {
        "WORKLINE",
        "DEVICE",
        "RACK",
        "BIN",
        "MATERIAL",
        "LOCATION",
        "EXCHANGE_TASK",
    }

    ref = ResourceRef(resource_type=ResourceType.RACK, resource_code="RACK-001", source_version="v1")

    assert ref.resource_type == ResourceType.RACK
    assert ref.resource_code == "RACK-001"
    assert ref.source_version == "v1"


def test_first_stage_resource_tables_are_registered_with_required_fields() -> None:
    """第一阶段资源底座表必须进入 SQLModel metadata，并包含槽位承载类型字段。"""

    from src.app.resource.models import (
        Bin,
        BinSlotTemplate,
        BinType,
        ExecutionLocation,
        ExecutionZone,
        Rack,
        RackSlotKind,
        RackSlotTemplate,
        RackType,
    )

    expected_tables = {
        "wes_biz.resource_execution_zones",
        "wes_biz.resource_execution_locations",
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

    for model in (ExecutionZone, ExecutionLocation, RackType, RackSlotTemplate, Rack, BinType, BinSlotTemplate, Bin):
        assert model.__schema__ == "wes_biz"


def test_second_stage_resource_fact_and_projection_tables_are_registered() -> None:
    """第二阶段资源事实账本与当前投影表必须进入 SQLModel metadata。"""

    from src.app.resource.models import (
        RackBinMount,
        RackMaterialMount,
        RackPlacement,
        ResourceStateEvent,
        ResourceStateEventType,
    )

    expected_tables = {
        "wes_biz.resource_state_events",
        "wes_biz.resource_rack_placements",
        "wes_biz.resource_rack_bin_mounts",
        "wes_biz.resource_rack_material_mounts",
    }

    assert expected_tables.issubset(SQLModel.metadata.tables)
    assert ResourceStateEventType.RACK_ARRIVED.value == "RACK_ARRIVED"
    assert ResourceStateEvent.__table__.c.source_event_id.nullable is False
    assert RackPlacement.__table__.c.ended_at.nullable is True
    assert RackBinMount.__table__.c.bin_code.nullable is False
    assert RackMaterialMount.__table__.c.material_identity_key.nullable is False


def test_fourth_stage_wms_writeback_evidence_table_is_registered() -> None:
    """第四阶段 WMS 回写证据表必须进入 metadata，并保留幂等与脱敏摘要字段。"""

    from src.app.resource.models import WmsWritebackEvidence

    assert "wes_biz.resource_wms_writeback_evidence" in SQLModel.metadata.tables
    assert WmsWritebackEvidence.__table__.c.evidence_code.nullable is False
    assert WmsWritebackEvidence.__table__.c.idempotency_key.nullable is False
    assert WmsWritebackEvidence.__table__.c.request_summary_json.nullable is False
    assert WmsWritebackEvidence.__table__.c.response_summary_json.nullable is False


def test_fifth_stage_release_snapshot_and_exchange_tables_are_registered() -> None:
    """第五阶段释放快照与满箱交换任务表必须进入 metadata。"""

    from src.app.resource.models import (
        BinContentSnapshot,
        BinContentSnapshotItem,
        BinContentSnapshotStatus,
        FullBoxExchangeStatus,
        FullBoxExchangeTask,
        RackRelease,
        RackReleaseBinSnapshot,
        RackReleaseStatus,
    )

    expected_tables = {
        "wes_biz.resource_rack_releases",
        "wes_biz.resource_rack_release_bin_snapshots",
        "wes_biz.resource_bin_content_snapshots",
        "wes_biz.resource_bin_content_snapshot_items",
        "wes_biz.resource_full_box_exchange_tasks",
    }

    assert expected_tables.issubset(SQLModel.metadata.tables)
    assert RackReleaseStatus.CANDIDATE.value == "CANDIDATE"
    assert BinContentSnapshotStatus.COMPLETE.value == "COMPLETE"
    assert FullBoxExchangeStatus.PHYSICAL_COMPLETED.value == "PHYSICAL_COMPLETED"
    assert RackRelease.__table__.c.rack_release_id.nullable is False
    assert RackReleaseBinSnapshot.__table__.c.slot_code.nullable is False
    assert BinContentSnapshot.__table__.c.snapshot_hash.nullable is False
    assert BinContentSnapshotItem.__table__.c.snapshot_id.nullable is False
    assert FullBoxExchangeTask.__table__.c.exchange_request_code.nullable is False


def test_resource_v1_router_exposes_first_stage_crud_routes() -> None:
    """resource v1 路由应暴露第一阶段底座资源的查询入口。"""

    from src.app.resource import router_v1

    paths = {route.path for route in router_v1.routes}

    assert "/v1/resource/execution-zones/query" in paths
    assert "/v1/resource/execution-locations/query" in paths
    assert "/v1/resource/rack-types/query" in paths
    assert "/v1/resource/rack-slot-templates/query" in paths
    assert "/v1/resource/racks/query" in paths
    assert "/v1/resource/bin-types/query" in paths
    assert "/v1/resource/bin-slot-templates/query" in paths
    assert "/v1/resource/bins/query" in paths


def test_resource_v1_router_exposes_readonly_process_and_projection_routes() -> None:
    """事实、投影、证据和过程镜像只暴露查询/详情，写入必须走领域服务。"""

    from src.app.resource import router_v1

    paths = {route.path for route in router_v1.routes}
    route_methods = {(route.path, method) for route in router_v1.routes for method in getattr(route, "methods", set())}
    readonly_prefixes = (
        "/v1/resource/state-events",
        "/v1/resource/rack-placements",
        "/v1/resource/rack-bin-mounts",
        "/v1/resource/rack-material-mounts",
        "/v1/resource/wms-writeback-evidence",
        "/v1/resource/rack-releases",
        "/v1/resource/rack-release-bin-snapshots",
        "/v1/resource/bin-content-snapshots",
        "/v1/resource/bin-content-snapshot-items",
        "/v1/resource/full-box-exchange-tasks",
    )

    for prefix in readonly_prefixes:
        assert f"{prefix}/query" in paths
        assert f"{prefix}/{{id}}" in paths
        assert (prefix, "POST") not in route_methods
        assert (f"{prefix}/{{id}}", "PUT") not in route_methods
        assert (f"{prefix}/{{id}}", "DELETE") not in route_methods
