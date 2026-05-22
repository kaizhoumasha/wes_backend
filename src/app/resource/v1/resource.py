"""WES 运行时资源底座 API。"""

from fastapi import APIRouter

from src.app.resource.models import (
    Bin,
    BinCellOccupancy,
    BinCellOccupancyCreate,
    BinCellOccupancyResponse,
    BinCellOccupancyUpdate,
    BinContentSnapshot,
    BinContentSnapshotCreate,
    BinContentSnapshotItem,
    BinContentSnapshotItemCreate,
    BinContentSnapshotItemResponse,
    BinContentSnapshotItemUpdate,
    BinContentSnapshotResponse,
    BinContentSnapshotUpdate,
    BinCreate,
    BinMaterialMount,
    BinMaterialMountCreate,
    BinMaterialMountResponse,
    BinMaterialMountUpdate,
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
    Rack,
    RackBinMount,
    RackBinMountCreate,
    RackBinMountResponse,
    RackBinMountUpdate,
    RackCreate,
    RackPlacement,
    RackPlacementCreate,
    RackPlacementResponse,
    RackPlacementUpdate,
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
)
from src.app.resource.services import (
    bin_cell_occupancy_service,
    bin_content_snapshot_item_service,
    bin_content_snapshot_service,
    bin_material_mount_service,
    bin_service,
    bin_slot_template_service,
    bin_type_service,
    rack_bin_mount_service,
    rack_placement_service,
    rack_service,
    rack_slot_template_service,
    rack_type_service,
    resource_state_event_service,
)
from src.core.base_api import BaseAPI

rack_type_api = BaseAPI(
    module_name="resource",
    model=RackType,
    service=rack_type_service,
    create_schema=RackTypeCreate,
    update_schema=RackTypeUpdate,
    response_schema=RackTypeResponse,
    prefix="/rack-types",
    tags=["资源模型-货架类型"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
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
    gen_create=False,
    gen_update=False,
    gen_delete=False,
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
    gen_create=False,
    gen_update=False,
    gen_delete=False,
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
    gen_create=False,
    gen_update=False,
    gen_delete=False,
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
    gen_create=False,
    gen_update=False,
    gen_delete=False,
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
    gen_create=False,
    gen_update=False,
    gen_delete=False,
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

bin_material_mount_api = BaseAPI(
    module_name="resource",
    model=BinMaterialMount,
    service=bin_material_mount_service,
    create_schema=BinMaterialMountCreate,
    update_schema=BinMaterialMountUpdate,
    response_schema=BinMaterialMountResponse,
    prefix="/bin-material-mounts",
    tags=["资源模型-物料料箱格位投影"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

bin_cell_occupancy_api = BaseAPI(
    module_name="resource",
    model=BinCellOccupancy,
    service=bin_cell_occupancy_service,
    create_schema=BinCellOccupancyCreate,
    update_schema=BinCellOccupancyUpdate,
    response_schema=BinCellOccupancyResponse,
    prefix="/bin-cell-occupancies",
    tags=["资源模型-料箱格位聚合占用"],
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

router = APIRouter()
router.include_router(rack_type_api.router)
router.include_router(rack_slot_template_api.router)
router.include_router(rack_api.router)
router.include_router(bin_type_api.router)
router.include_router(bin_slot_template_api.router)
router.include_router(bin_api.router)
router.include_router(resource_state_event_api.router)
router.include_router(rack_placement_api.router)
router.include_router(rack_bin_mount_api.router)
router.include_router(bin_material_mount_api.router)
router.include_router(bin_cell_occupancy_api.router)
router.include_router(bin_content_snapshot_api.router)
router.include_router(bin_content_snapshot_item_api.router)

__all__ = ["router"]
