"""Resource Service 层。"""

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
from src.app.resource.repositories import (
    BinCellOccupancyRepository,
    BinContentSnapshotItemRepository,
    BinContentSnapshotRepository,
    BinMaterialMountRepository,
    BinRepository,
    BinSlotTemplateRepository,
    BinTypeRepository,
    RackBinMountRepository,
    RackPlacementRepository,
    RackRepository,
    RackSlotTemplateRepository,
    RackTypeRepository,
    ResourceStateEventRepository,
    bin_cell_occupancy_repository,
    bin_content_snapshot_item_repository,
    bin_content_snapshot_repository,
    bin_material_mount_repository,
    bin_repository,
    bin_slot_template_repository,
    bin_type_repository,
    rack_bin_mount_repository,
    rack_placement_repository,
    rack_repository,
    rack_slot_template_repository,
    rack_type_repository,
    resource_state_event_repository,
)
from src.core.base_service import BaseService


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


class ResourceStateEventService(BaseService[ResourceStateEvent, ResourceStateEventRepository]):
    """资源事实 Service。"""

    def __init__(self, repo: ResourceStateEventRepository = resource_state_event_repository) -> None:
        super().__init__(repo)


class RackPlacementService(BaseService[RackPlacement, RackPlacementRepository]):
    """货架位置投影 Service。"""

    def __init__(self, repo: RackPlacementRepository = rack_placement_repository) -> None:
        super().__init__(repo)


class RackBinMountService(BaseService[RackBinMount, RackBinMountRepository]):
    """料箱挂载投影 Service。"""

    def __init__(self, repo: RackBinMountRepository = rack_bin_mount_repository) -> None:
        super().__init__(repo)


class BinMaterialMountService(BaseService[BinMaterialMount, BinMaterialMountRepository]):
    """料盘/PKG 料箱格位明细 Service。"""

    def __init__(self, repo: BinMaterialMountRepository = bin_material_mount_repository) -> None:
        super().__init__(repo)


class BinCellOccupancyService(BaseService[BinCellOccupancy, BinCellOccupancyRepository]):
    """料箱格位聚合占用 Service。"""

    def __init__(self, repo: BinCellOccupancyRepository = bin_cell_occupancy_repository) -> None:
        super().__init__(repo)


class BinContentSnapshotService(BaseService[BinContentSnapshot, BinContentSnapshotRepository]):
    """料箱内容快照头 Service。"""

    def __init__(self, repo: BinContentSnapshotRepository = bin_content_snapshot_repository) -> None:
        super().__init__(repo)


class BinContentSnapshotItemService(BaseService[BinContentSnapshotItem, BinContentSnapshotItemRepository]):
    """料箱内容快照明细 Service。"""

    def __init__(self, repo: BinContentSnapshotItemRepository = bin_content_snapshot_item_repository) -> None:
        super().__init__(repo)


rack_type_service = RackTypeService()
rack_slot_template_service = RackSlotTemplateService()
rack_service = RackService()
bin_type_service = BinTypeService()
bin_slot_template_service = BinSlotTemplateService()
bin_service = BinService()
resource_state_event_service = ResourceStateEventService()
rack_placement_service = RackPlacementService()
rack_bin_mount_service = RackBinMountService()
bin_material_mount_service = BinMaterialMountService()
bin_cell_occupancy_service = BinCellOccupancyService()
bin_content_snapshot_service = BinContentSnapshotService()
bin_content_snapshot_item_service = BinContentSnapshotItemService()

__all__ = [
    "BinCellOccupancyService",
    "BinContentSnapshotItemService",
    "BinContentSnapshotService",
    "BinMaterialMountService",
    "BinService",
    "BinSlotTemplateService",
    "BinTypeService",
    "RackBinMountService",
    "RackPlacementService",
    "RackService",
    "RackSlotTemplateService",
    "RackTypeService",
    "ResourceStateEventService",
    "bin_cell_occupancy_service",
    "bin_content_snapshot_item_service",
    "bin_content_snapshot_service",
    "bin_material_mount_service",
    "bin_service",
    "bin_slot_template_service",
    "bin_type_service",
    "rack_bin_mount_service",
    "rack_placement_service",
    "rack_service",
    "rack_slot_template_service",
    "rack_type_service",
    "resource_state_event_service",
]
