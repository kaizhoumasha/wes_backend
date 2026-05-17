"""WES 运行时资源底座 API。"""

from fastapi import APIRouter

from src.app.resource.models import (
    Bin,
    BinContentSnapshot,
    BinContentSnapshotCreate,
    BinContentSnapshotItem,
    BinContentSnapshotItemCreate,
    BinContentSnapshotItemResponse,
    BinContentSnapshotItemUpdate,
    BinContentSnapshotResponse,
    BinContentSnapshotUpdate,
    BinCreate,
    BinResponse,
    BinSlotTemplate,
    BinSlotTemplateCreate,
    BinSlotTemplateResponse,
    BinSlotTemplateUpdate,
    BinType,
    BinTypeCreate,
    BinTypeResponse,
    BinTypeUpdate,
    BinUpdate,
    ExecutionLocation,
    ExecutionLocationCreate,
    ExecutionLocationResponse,
    ExecutionLocationUpdate,
    ExecutionZone,
    ExecutionZoneCreate,
    ExecutionZoneResponse,
    ExecutionZoneUpdate,
    FullBoxExchangeTask,
    FullBoxExchangeTaskCreate,
    FullBoxExchangeTaskResponse,
    FullBoxExchangeTaskUpdate,
    Rack,
    RackBinMount,
    RackBinMountCreate,
    RackBinMountResponse,
    RackBinMountUpdate,
    RackCreate,
    RackMaterialMount,
    RackMaterialMountCreate,
    RackMaterialMountResponse,
    RackMaterialMountUpdate,
    RackPlacement,
    RackPlacementCreate,
    RackPlacementResponse,
    RackPlacementUpdate,
    RackRelease,
    RackReleaseBinSnapshot,
    RackReleaseBinSnapshotCreate,
    RackReleaseBinSnapshotResponse,
    RackReleaseBinSnapshotUpdate,
    RackReleaseCreate,
    RackReleaseResponse,
    RackReleaseUpdate,
    RackResponse,
    RackSlotTemplate,
    RackSlotTemplateCreate,
    RackSlotTemplateResponse,
    RackSlotTemplateUpdate,
    RackType,
    RackTypeCreate,
    RackTypeResponse,
    RackTypeUpdate,
    RackUpdate,
    ResourceStateEvent,
    ResourceStateEventCreate,
    ResourceStateEventResponse,
    ResourceStateEventUpdate,
    WmsWritebackEvidence,
    WmsWritebackEvidenceCreate,
    WmsWritebackEvidenceResponse,
    WmsWritebackEvidenceUpdate,
)
from src.app.resource.services import (
    bin_content_snapshot_item_service,
    bin_content_snapshot_service,
    bin_service,
    bin_slot_template_service,
    bin_type_service,
    execution_location_service,
    execution_zone_service,
    full_box_exchange_task_service,
    rack_bin_mount_service,
    rack_material_mount_service,
    rack_placement_service,
    rack_release_bin_snapshot_service,
    rack_release_service,
    rack_service,
    rack_slot_template_service,
    rack_type_service,
    resource_state_event_service,
    wms_writeback_evidence_service,
)
from src.core.base_api import BaseAPI

execution_zone_api = BaseAPI(
    module_name="resource",
    model=ExecutionZone,
    service=execution_zone_service,
    create_schema=ExecutionZoneCreate,
    update_schema=ExecutionZoneUpdate,
    response_schema=ExecutionZoneResponse,
    prefix="/execution-zones",
    tags=["资源模型-执行区域"],
)

execution_location_api = BaseAPI(
    module_name="resource",
    model=ExecutionLocation,
    service=execution_location_service,
    create_schema=ExecutionLocationCreate,
    update_schema=ExecutionLocationUpdate,
    response_schema=ExecutionLocationResponse,
    prefix="/execution-locations",
    tags=["资源模型-执行地码"],
)

rack_type_api = BaseAPI(
    module_name="resource",
    model=RackType,
    service=rack_type_service,
    create_schema=RackTypeCreate,
    update_schema=RackTypeUpdate,
    response_schema=RackTypeResponse,
    prefix="/rack-types",
    tags=["资源模型-货架类型"],
)

rack_slot_template_api = BaseAPI(
    module_name="resource",
    model=RackSlotTemplate,
    service=rack_slot_template_service,
    create_schema=RackSlotTemplateCreate,
    update_schema=RackSlotTemplateUpdate,
    response_schema=RackSlotTemplateResponse,
    prefix="/rack-slot-templates",
    tags=["资源模型-货架槽位模板"],
)

rack_api = BaseAPI(
    module_name="resource",
    model=Rack,
    service=rack_service,
    create_schema=RackCreate,
    update_schema=RackUpdate,
    response_schema=RackResponse,
    prefix="/racks",
    tags=["资源模型-货架实例"],
)

bin_type_api = BaseAPI(
    module_name="resource",
    model=BinType,
    service=bin_type_service,
    create_schema=BinTypeCreate,
    update_schema=BinTypeUpdate,
    response_schema=BinTypeResponse,
    prefix="/bin-types",
    tags=["资源模型-料箱类型"],
)

bin_slot_template_api = BaseAPI(
    module_name="resource",
    model=BinSlotTemplate,
    service=bin_slot_template_service,
    create_schema=BinSlotTemplateCreate,
    update_schema=BinSlotTemplateUpdate,
    response_schema=BinSlotTemplateResponse,
    prefix="/bin-slot-templates",
    tags=["资源模型-料箱槽位模板"],
)

bin_api = BaseAPI(
    module_name="resource",
    model=Bin,
    service=bin_service,
    create_schema=BinCreate,
    update_schema=BinUpdate,
    response_schema=BinResponse,
    prefix="/bins",
    tags=["资源模型-料箱实例"],
)

resource_state_event_api = BaseAPI(
    module_name="resource",
    model=ResourceStateEvent,
    service=resource_state_event_service,
    create_schema=ResourceStateEventCreate,
    update_schema=ResourceStateEventUpdate,
    response_schema=ResourceStateEventResponse,
    prefix="/state-events",
    tags=["资源模型-事实账本"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

rack_placement_api = BaseAPI(
    module_name="resource",
    model=RackPlacement,
    service=rack_placement_service,
    create_schema=RackPlacementCreate,
    update_schema=RackPlacementUpdate,
    response_schema=RackPlacementResponse,
    prefix="/rack-placements",
    tags=["资源模型-货架位置投影"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

rack_bin_mount_api = BaseAPI(
    module_name="resource",
    model=RackBinMount,
    service=rack_bin_mount_service,
    create_schema=RackBinMountCreate,
    update_schema=RackBinMountUpdate,
    response_schema=RackBinMountResponse,
    prefix="/rack-bin-mounts",
    tags=["资源模型-料箱挂载投影"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

rack_material_mount_api = BaseAPI(
    module_name="resource",
    model=RackMaterialMount,
    service=rack_material_mount_service,
    create_schema=RackMaterialMountCreate,
    update_schema=RackMaterialMountUpdate,
    response_schema=RackMaterialMountResponse,
    prefix="/rack-material-mounts",
    tags=["资源模型-物料卡槽投影"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

wms_writeback_evidence_api = BaseAPI(
    module_name="resource",
    model=WmsWritebackEvidence,
    service=wms_writeback_evidence_service,
    create_schema=WmsWritebackEvidenceCreate,
    update_schema=WmsWritebackEvidenceUpdate,
    response_schema=WmsWritebackEvidenceResponse,
    prefix="/wms-writeback-evidence",
    tags=["资源模型-WMS回写证据"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

rack_release_api = BaseAPI(
    module_name="resource",
    model=RackRelease,
    service=rack_release_service,
    create_schema=RackReleaseCreate,
    update_schema=RackReleaseUpdate,
    response_schema=RackReleaseResponse,
    prefix="/rack-releases",
    tags=["资源模型-释放周期"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

rack_release_bin_snapshot_api = BaseAPI(
    module_name="resource",
    model=RackReleaseBinSnapshot,
    service=rack_release_bin_snapshot_service,
    create_schema=RackReleaseBinSnapshotCreate,
    update_schema=RackReleaseBinSnapshotUpdate,
    response_schema=RackReleaseBinSnapshotResponse,
    prefix="/rack-release-bin-snapshots",
    tags=["资源模型-释放槽位快照"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

bin_content_snapshot_api = BaseAPI(
    module_name="resource",
    model=BinContentSnapshot,
    service=bin_content_snapshot_service,
    create_schema=BinContentSnapshotCreate,
    update_schema=BinContentSnapshotUpdate,
    response_schema=BinContentSnapshotResponse,
    prefix="/bin-content-snapshots",
    tags=["资源模型-料箱内容快照"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

bin_content_snapshot_item_api = BaseAPI(
    module_name="resource",
    model=BinContentSnapshotItem,
    service=bin_content_snapshot_item_service,
    create_schema=BinContentSnapshotItemCreate,
    update_schema=BinContentSnapshotItemUpdate,
    response_schema=BinContentSnapshotItemResponse,
    prefix="/bin-content-snapshot-items",
    tags=["资源模型-料箱内容快照明细"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

full_box_exchange_task_api = BaseAPI(
    module_name="resource",
    model=FullBoxExchangeTask,
    service=full_box_exchange_task_service,
    create_schema=FullBoxExchangeTaskCreate,
    update_schema=FullBoxExchangeTaskUpdate,
    response_schema=FullBoxExchangeTaskResponse,
    prefix="/full-box-exchange-tasks",
    tags=["资源模型-满箱交换任务"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

router = APIRouter()
router.include_router(execution_zone_api.router)
router.include_router(execution_location_api.router)
router.include_router(rack_type_api.router)
router.include_router(rack_slot_template_api.router)
router.include_router(rack_api.router)
router.include_router(bin_type_api.router)
router.include_router(bin_slot_template_api.router)
router.include_router(bin_api.router)
router.include_router(resource_state_event_api.router)
router.include_router(rack_placement_api.router)
router.include_router(rack_bin_mount_api.router)
router.include_router(rack_material_mount_api.router)
router.include_router(wms_writeback_evidence_api.router)
router.include_router(rack_release_api.router)
router.include_router(rack_release_bin_snapshot_api.router)
router.include_router(bin_content_snapshot_api.router)
router.include_router(bin_content_snapshot_item_api.router)
router.include_router(full_box_exchange_task_api.router)

__all__ = ["router"]
