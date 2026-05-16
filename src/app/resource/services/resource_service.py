"""Resource Service 层。"""

from src.app.resource.models import (
    Bin,
    BinContentSnapshot,
    BinContentSnapshotItem,
    BinSlotTemplate,
    BinType,
    ExecutionLocation,
    ExecutionZone,
    FullBoxExchangeTask,
    Rack,
    RackBinMount,
    RackMaterialMount,
    RackPlacement,
    RackRelease,
    RackReleaseBinSnapshot,
    RackSlotTemplate,
    RackType,
    ResourceStateEvent,
    WmsWritebackEvidence,
)
from src.app.resource.repositories import (
    BinContentSnapshotItemRepository,
    BinContentSnapshotRepository,
    BinRepository,
    BinSlotTemplateRepository,
    BinTypeRepository,
    ExecutionLocationRepository,
    ExecutionZoneRepository,
    FullBoxExchangeTaskRepository,
    RackBinMountRepository,
    RackMaterialMountRepository,
    RackPlacementRepository,
    RackReleaseBinSnapshotRepository,
    RackReleaseRepository,
    RackRepository,
    RackSlotTemplateRepository,
    RackTypeRepository,
    ResourceStateEventRepository,
    WmsWritebackEvidenceRepository,
    bin_content_snapshot_item_repository,
    bin_content_snapshot_repository,
    bin_repository,
    bin_slot_template_repository,
    bin_type_repository,
    execution_location_repository,
    execution_zone_repository,
    full_box_exchange_task_repository,
    rack_bin_mount_repository,
    rack_material_mount_repository,
    rack_placement_repository,
    rack_release_bin_snapshot_repository,
    rack_release_repository,
    rack_repository,
    rack_slot_template_repository,
    rack_type_repository,
    resource_state_event_repository,
    wms_writeback_evidence_repository,
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


class RackMaterialMountService(BaseService[RackMaterialMount, RackMaterialMountRepository]):
    """物料卡槽投影 Service。"""

    def __init__(self, repo: RackMaterialMountRepository = rack_material_mount_repository) -> None:
        super().__init__(repo)


class WmsWritebackEvidenceService(BaseService[WmsWritebackEvidence, WmsWritebackEvidenceRepository]):
    """WMS 回写证据 Service。"""

    def __init__(self, repo: WmsWritebackEvidenceRepository = wms_writeback_evidence_repository) -> None:
        super().__init__(repo)


class RackReleaseService(BaseService[RackRelease, RackReleaseRepository]):
    """释放周期 Service。"""

    def __init__(self, repo: RackReleaseRepository = rack_release_repository) -> None:
        super().__init__(repo)


class RackReleaseBinSnapshotService(BaseService[RackReleaseBinSnapshot, RackReleaseBinSnapshotRepository]):
    """释放槽位快照 Service。"""

    def __init__(self, repo: RackReleaseBinSnapshotRepository = rack_release_bin_snapshot_repository) -> None:
        super().__init__(repo)


class BinContentSnapshotService(BaseService[BinContentSnapshot, BinContentSnapshotRepository]):
    """料箱内容快照头 Service。"""

    def __init__(self, repo: BinContentSnapshotRepository = bin_content_snapshot_repository) -> None:
        super().__init__(repo)


class BinContentSnapshotItemService(BaseService[BinContentSnapshotItem, BinContentSnapshotItemRepository]):
    """料箱内容快照明细 Service。"""

    def __init__(self, repo: BinContentSnapshotItemRepository = bin_content_snapshot_item_repository) -> None:
        super().__init__(repo)


class FullBoxExchangeTaskService(BaseService[FullBoxExchangeTask, FullBoxExchangeTaskRepository]):
    """满箱交换任务 Service。"""

    def __init__(self, repo: FullBoxExchangeTaskRepository = full_box_exchange_task_repository) -> None:
        super().__init__(repo)


execution_zone_service = ExecutionZoneService()
execution_location_service = ExecutionLocationService()
rack_type_service = RackTypeService()
rack_slot_template_service = RackSlotTemplateService()
rack_service = RackService()
bin_type_service = BinTypeService()
bin_slot_template_service = BinSlotTemplateService()
bin_service = BinService()
resource_state_event_service = ResourceStateEventService()
rack_placement_service = RackPlacementService()
rack_bin_mount_service = RackBinMountService()
rack_material_mount_service = RackMaterialMountService()
wms_writeback_evidence_service = WmsWritebackEvidenceService()
rack_release_service = RackReleaseService()
rack_release_bin_snapshot_service = RackReleaseBinSnapshotService()
bin_content_snapshot_service = BinContentSnapshotService()
bin_content_snapshot_item_service = BinContentSnapshotItemService()
full_box_exchange_task_service = FullBoxExchangeTaskService()

__all__ = [
    "BinContentSnapshotItemService",
    "BinContentSnapshotService",
    "BinService",
    "BinSlotTemplateService",
    "BinTypeService",
    "ExecutionLocationService",
    "ExecutionZoneService",
    "FullBoxExchangeTaskService",
    "RackBinMountService",
    "RackMaterialMountService",
    "RackPlacementService",
    "RackReleaseBinSnapshotService",
    "RackReleaseService",
    "RackService",
    "RackSlotTemplateService",
    "RackTypeService",
    "ResourceStateEventService",
    "WmsWritebackEvidenceService",
    "bin_content_snapshot_item_service",
    "bin_content_snapshot_service",
    "bin_service",
    "bin_slot_template_service",
    "bin_type_service",
    "execution_location_service",
    "execution_zone_service",
    "full_box_exchange_task_service",
    "rack_bin_mount_service",
    "rack_material_mount_service",
    "rack_placement_service",
    "rack_release_bin_snapshot_service",
    "rack_release_service",
    "rack_service",
    "rack_slot_template_service",
    "rack_type_service",
    "resource_state_event_service",
    "wms_writeback_evidence_service",
]
