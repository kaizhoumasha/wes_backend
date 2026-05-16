"""Resource Repository 层。"""

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
from src.database.base_repository import BaseRepository


class ExecutionZoneRepository(BaseRepository[ExecutionZone]):
    """执行区域 Repository。"""

    def __init__(self) -> None:
        super().__init__(ExecutionZone)


class ExecutionLocationRepository(BaseRepository[ExecutionLocation]):
    """执行地码 Repository。"""

    def __init__(self) -> None:
        super().__init__(ExecutionLocation)


class RackTypeRepository(BaseRepository[RackType]):
    """货架类型 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackType)


class RackSlotTemplateRepository(BaseRepository[RackSlotTemplate]):
    """货架槽位模板 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackSlotTemplate)


class RackRepository(BaseRepository[Rack]):
    """货架实例 Repository。"""

    def __init__(self) -> None:
        super().__init__(Rack)


class BinTypeRepository(BaseRepository[BinType]):
    """料箱类型 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinType)


class BinSlotTemplateRepository(BaseRepository[BinSlotTemplate]):
    """料箱槽位模板 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinSlotTemplate)


class BinRepository(BaseRepository[Bin]):
    """料箱实例 Repository。"""

    def __init__(self) -> None:
        super().__init__(Bin)


execution_zone_repository = ExecutionZoneRepository()
execution_location_repository = ExecutionLocationRepository()
rack_type_repository = RackTypeRepository()
rack_slot_template_repository = RackSlotTemplateRepository()
rack_repository = RackRepository()
bin_type_repository = BinTypeRepository()
bin_slot_template_repository = BinSlotTemplateRepository()
bin_repository = BinRepository()

__all__ = [
    "BinRepository",
    "BinSlotTemplateRepository",
    "BinTypeRepository",
    "ExecutionLocationRepository",
    "ExecutionZoneRepository",
    "RackRepository",
    "RackSlotTemplateRepository",
    "RackTypeRepository",
    "bin_repository",
    "bin_slot_template_repository",
    "bin_type_repository",
    "execution_location_repository",
    "execution_zone_repository",
    "rack_repository",
    "rack_slot_template_repository",
    "rack_type_repository",
]
