"""WES 运行时资源底座 API。"""

from fastapi import APIRouter

from src.app.resource.models import (
    Bin,
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
    Rack,
    RackCreate,
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
)
from src.app.resource.services import (
    bin_service,
    bin_slot_template_service,
    bin_type_service,
    execution_location_service,
    execution_zone_service,
    rack_service,
    rack_slot_template_service,
    rack_type_service,
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

router = APIRouter()
router.include_router(execution_zone_api.router)
router.include_router(execution_location_api.router)
router.include_router(rack_type_api.router)
router.include_router(rack_slot_template_api.router)
router.include_router(rack_api.router)
router.include_router(bin_type_api.router)
router.include_router(bin_slot_template_api.router)
router.include_router(bin_api.router)

__all__ = ["router"]
