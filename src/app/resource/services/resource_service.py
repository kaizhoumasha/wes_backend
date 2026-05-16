"""Resource Service 层。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.resource.models import (
    Bin,
    BinContentSnapshot,
    BinContentSnapshotItem,
    BinSlotTemplate,
    BinType,
    ExecutionLocation,
    ExecutionZone,
    FullBoxExchangeStatus,
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

    async def record_requested_from_external_request(
        self,
        db: AsyncSession,
        *,
        session: Any,
        outbox: Any,
        dispatch_key: str,
        target_code: str,
        payload_json: Mapping[str, Any],
        trace_id: str | None,
    ) -> FullBoxExchangeTask | None:
        """把 Runtime 外部请求镜像为满箱交换任务证据。

        Runtime 仍拥有等待状态和 Outbox；资源服务只在 payload 明确携带满箱交换业务键时落业务过程表。
        """

        _ = target_code
        exchange_request_code = _optional_text(payload_json.get("exchange_request_code"))
        rack_release_id = _optional_text(payload_json.get("rack_release_id"))
        if exchange_request_code is None or rack_release_id is None:
            return None

        existing = await self.repo.get_by_exchange_request_code(db, exchange_request_code)
        if existing is not None:
            return existing

        flush = getattr(db, "flush", None)
        if callable(flush):
            await flush()

        data = {
            "exchange_request_code": exchange_request_code,
            "rack_release_id": rack_release_id,
            "session_id": _optional_int(getattr(session, "id", None)),
            "outbox_id": _optional_int(getattr(outbox, "id", None)),
            "dispatch_key": dispatch_key,
            "exchange_status": FullBoxExchangeStatus.REQUESTED,
            "exchange_area_code": _optional_text(payload_json.get("exchange_area_code")),
            "requested_bins_json": _requested_bins(payload_json),
            "wms_rcs_task_id": _optional_text(payload_json.get("wms_rcs_task_id")),
            "queue_position": _optional_int(payload_json.get("queue_position")),
            "eta_seconds": _optional_int(payload_json.get("eta_seconds")),
            "request_payload_hash": _payload_hash(payload_json),
            "trace_id": trace_id or _optional_text(payload_json.get("trace_id")),
        }
        return await self.repo.create(db, data)

    async def record_callback_from_external_http(
        self,
        db: AsyncSession,
        *,
        payload_json: Mapping[str, Any],
        trace_id: str | None,
    ) -> FullBoxExchangeTask | None:
        """把 WMS/RCS 满箱交换回调归因到任务镜像。"""

        if _optional_text(payload_json.get("callback_type")) != "WMS_FULL_BOX_EXCHANGE_RESULT":
            return None

        exchange_request_code = _optional_text(payload_json.get("exchange_request_code"))
        if exchange_request_code is None:
            return None

        task = await self.repo.get_by_exchange_request_code(db, exchange_request_code)
        if task is None:
            return None

        task_id = _optional_int(getattr(task, "id", None))
        if task_id is None:
            return task

        data: dict[str, Any] = {
            "exchange_status": _exchange_status(payload_json),
            "last_callback_payload_hash": _payload_hash(payload_json),
        }
        optional_updates = {
            "wms_rcs_task_id": _optional_text(payload_json.get("wms_rcs_task_id")),
            "wms_rcs_event_id": _optional_text(payload_json.get("source_event_id")),
            "queue_position": _optional_int(payload_json.get("queue_position")),
            "eta_seconds": _optional_int(payload_json.get("eta_seconds")),
            "failure_code": _optional_text(payload_json.get("failure_code")),
            "failure_message": _optional_text(payload_json.get("failure_message")),
            "trace_id": trace_id or _optional_text(payload_json.get("trace_id")),
        }
        data.update({key: value for key, value in optional_updates.items() if value is not None})
        version = _optional_int(getattr(task, "version", None))
        if version is not None:
            data["version"] = version

        updated = await self.repo.update(db, task_id, data)
        return updated or task


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _requested_bins(payload_json: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_bins = payload_json.get("requested_bins_json")
    if raw_bins is None:
        raw_bins = payload_json.get("requested_bins")
    if not isinstance(raw_bins, Sequence) or isinstance(raw_bins, (str, bytes)):
        return []
    return [dict(item) for item in raw_bins if isinstance(item, Mapping)]


def _payload_hash(payload_json: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload_json,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _exchange_status(payload_json: Mapping[str, Any]) -> FullBoxExchangeStatus:
    status_text = _optional_text(payload_json.get("exchange_status"))
    if status_text is None:
        raise ValueError("exchange_status is required")
    try:
        return FullBoxExchangeStatus(status_text)
    except ValueError as exc:
        raise ValueError(f"unsupported full box exchange_status: {status_text}") from exc


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
