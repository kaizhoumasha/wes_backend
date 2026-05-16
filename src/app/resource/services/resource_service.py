"""Resource Service 层。"""

from src.app.resource.models import (
    Bin,
    BinSlotTemplate,
    BinType,
    ExecutionLocation,
    ExecutionZone,
    Rack,
    RackSlotTemplate,
    RackType,
)
from src.app.resource.repositories import (
    BinRepository,
    BinSlotTemplateRepository,
    BinTypeRepository,
    ExecutionLocationRepository,
    ExecutionZoneRepository,
    RackRepository,
    RackSlotTemplateRepository,
    RackTypeRepository,
    bin_repository,
    bin_slot_template_repository,
    bin_type_repository,
    execution_location_repository,
    execution_zone_repository,
    rack_repository,
    rack_slot_template_repository,
    rack_type_repository,
)
from src.core.base_service import BaseService


class ExecutionZoneService(BaseService[ExecutionZone, ExecutionZoneRepository]):
    """执行区域 Service。"""

    def __init__(self, repo: ExecutionZoneRepository = execution_zone_repository) -> None:
        super().__init__(repo)


class ExecutionLocationService(BaseService[ExecutionLocation, ExecutionLocationRepository]):
    """执行地码 Service。"""

    def __init__(self, repo: ExecutionLocationRepository = execution_location_repository) -> None:
        super().__init__(repo)


class RackTypeService(BaseService[RackType, RackTypeRepository]):
    """货架类型 Service。"""

    def __init__(self, repo: RackTypeRepository = rack_type_repository) -> None:
        super().__init__(repo)


class RackSlotTemplateService(BaseService[RackSlotTemplate, RackSlotTemplateRepository]):
    """货架槽位模板 Service。"""

    def __init__(self, repo: RackSlotTemplateRepository = rack_slot_template_repository) -> None:
        super().__init__(repo)


class RackService(BaseService[Rack, RackRepository]):
    """货架实例 Service。"""

    def __init__(self, repo: RackRepository = rack_repository) -> None:
        super().__init__(repo)


class BinTypeService(BaseService[BinType, BinTypeRepository]):
    """料箱类型 Service。"""

    def __init__(self, repo: BinTypeRepository = bin_type_repository) -> None:
        super().__init__(repo)


class BinSlotTemplateService(BaseService[BinSlotTemplate, BinSlotTemplateRepository]):
    """料箱槽位模板 Service。"""

    def __init__(self, repo: BinSlotTemplateRepository = bin_slot_template_repository) -> None:
        super().__init__(repo)


class BinService(BaseService[Bin, BinRepository]):
    """料箱实例 Service。"""

    def __init__(self, repo: BinRepository = bin_repository) -> None:
        super().__init__(repo)


execution_zone_service = ExecutionZoneService()
execution_location_service = ExecutionLocationService()
rack_type_service = RackTypeService()
rack_slot_template_service = RackSlotTemplateService()
rack_service = RackService()
bin_type_service = BinTypeService()
bin_slot_template_service = BinSlotTemplateService()
bin_service = BinService()

__all__ = [
    "BinService",
    "BinSlotTemplateService",
    "BinTypeService",
    "ExecutionLocationService",
    "ExecutionZoneService",
    "RackService",
    "RackSlotTemplateService",
    "RackTypeService",
    "bin_service",
    "bin_slot_template_service",
    "bin_type_service",
    "execution_location_service",
    "execution_zone_service",
    "rack_service",
    "rack_slot_template_service",
    "rack_type_service",
]
