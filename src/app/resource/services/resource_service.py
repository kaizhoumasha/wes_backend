"""Resource Service 层。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.resource.models import (
    Bin,
    BinContentSnapshot,
    BinContentSnapshotItem,
    BinSlotTemplate,
    BinStatus,
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
    RackReleaseStatus,
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
from src.utils.timezone import timezone


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

    async def record_confirmation_from_external_http(
        self,
        db: AsyncSession,
        *,
        payload_json: Mapping[str, Any],
        trace_id: str | None,
        session_id: str | None,
    ) -> WmsWritebackEvidence | None:
        """把 WMS 满箱交换确认回调记录为回写证据。"""

        status = _exchange_status(payload_json)
        if status not in {FullBoxExchangeStatus.WMS_CONFIRMED, FullBoxExchangeStatus.BUSINESS_COMPLETED}:
            return None

        confirmation = payload_json.get("wms_confirmation")
        if not isinstance(confirmation, Mapping):
            return None

        request_id = _optional_text(payload_json.get("request_id"))
        exchange_request_code = _optional_text(payload_json.get("exchange_request_code"))
        callback_type = _optional_text(payload_json.get("callback_type")) or "WMS_FULL_BOX_EXCHANGE_RESULT"
        if request_id is None or exchange_request_code is None:
            return None

        idempotency_key = _payload_hash(
            {
                "callback_type": callback_type,
                "exchange_request_code": exchange_request_code,
                "request_id": request_id,
            }
        )
        existing = await self.repo.get_by_idempotency_key(db, idempotency_key)
        if existing is not None:
            return existing

        request_summary = {
            "exchange_request_code": exchange_request_code,
            "rack_release_id": _optional_text(payload_json.get("rack_release_id")),
            "dispatch_key": _optional_text(payload_json.get("dispatch_key")),
            "wms_rcs_task_id": _optional_text(payload_json.get("wms_rcs_task_id")),
            "exchange_status": status.value,
        }
        response_summary = dict(confirmation)
        data = {
            "evidence_code": _evidence_code(idempotency_key),
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "dispatch_key": _optional_text(payload_json.get("dispatch_key")),
            "endpoint": callback_type,
            "source_system": _resource_source_system(payload_json),
            "request_hash": _payload_hash(request_summary),
            "response_hash": _payload_hash(response_summary),
            "request_summary_json": request_summary,
            "response_summary_json": response_summary,
            "wms_document_id": _optional_text(confirmation.get("wms_document_id"))
            or _optional_text(confirmation.get("document_id")),
            "inventory_version": _optional_text(confirmation.get("inventory_version")),
            "confirmed_at": timezone.to_db_datetime(confirmation.get("confirmed_at"))
            or timezone.to_db_datetime(payload_json.get("occurred_at")),
            "trace_id": trace_id or _optional_text(payload_json.get("trace_id")),
            "session_id": session_id,
        }
        return await self.repo.create(db, data)


class RackReleaseService(BaseService[RackRelease, RackReleaseRepository]):
    """释放周期 Service。"""

    def __init__(
        self,
        repo: RackReleaseRepository = rack_release_repository,
        snapshot_repo: RackReleaseBinSnapshotRepository = rack_release_bin_snapshot_repository,
    ) -> None:
        super().__init__(repo)
        self.snapshot_repo = snapshot_repo

    async def record_release_snapshot(
        self,
        db: AsyncSession,
        *,
        rack_release_id: str,
        single_layer_rack_code: str,
        released_at: Any,
        slot_snapshots: Sequence[Mapping[str, Any]],
        source_classifier_line_code: str | None = None,
        source_task_batch_id: str | None = None,
        source_event_id: str | None = None,
        inbox_id: int | None = None,
        session_id: int | None = None,
        release_cycle_seq: int = 1,
        trace_id: str | None = None,
    ) -> RackRelease:
        """记录单层货架释放周期和槽位料箱快照。"""

        existing = await self.repo.get_by_release_id(db, rack_release_id)
        if existing is not None:
            return existing

        normalized_snapshots = _normalize_release_snapshots(slot_snapshots)
        snapshot_hash = _payload_hash({"slot_snapshots": normalized_snapshots})
        release = await self.repo.create(
            db,
            {
                "rack_release_id": rack_release_id,
                "single_layer_rack_code": single_layer_rack_code,
                "source_classifier_line_code": source_classifier_line_code,
                "source_task_batch_id": source_task_batch_id,
                "source_event_id": source_event_id,
                "release_status": RackReleaseStatus.CANDIDATE.value,
                "released_at": released_at,
                "inbox_id": inbox_id,
                "session_id": session_id,
                "release_cycle_seq": release_cycle_seq,
                "idempotency_key": _payload_hash(
                    {
                        "rack_release_id": rack_release_id,
                        "single_layer_rack_code": single_layer_rack_code,
                        "release_cycle_seq": release_cycle_seq,
                        "snapshot_hash": snapshot_hash,
                    }
                ),
                "snapshot_hash": snapshot_hash,
                "trace_id": trace_id,
            },
        )

        for snapshot in normalized_snapshots:
            await self.snapshot_repo.create(
                db,
                {
                    "rack_release_id": rack_release_id,
                    **snapshot,
                },
            )
        if release is None:
            raise RuntimeError("Failed to create rack release snapshot")
        return release


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

    def __init__(
        self,
        repo: FullBoxExchangeTaskRepository = full_box_exchange_task_repository,
        relation_projector: Any | None = None,
        writeback_evidence_service: Any | None = None,
        session_repo: Any | None = None,
    ) -> None:
        super().__init__(repo)
        self._relation_projector = relation_projector
        self._writeback_evidence_service = writeback_evidence_service
        self._session_repo = session_repo

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
        if _callback_identity_mismatched(task, payload_json, trace_id=trace_id):
            return task

        status = _exchange_status(payload_json)
        source_status_text = _exchange_status_text(payload_json)
        writeback_evidence = await self._record_wms_confirmation(
            db,
            payload_json=payload_json,
            trace_id=trace_id,
            session_id=_optional_text(getattr(task, "session_id", None)),
        )
        projection_result = await self._project_post_exchange_relations(
            db,
            task=task,
            payload_json=payload_json,
            trace_id=trace_id,
        )
        data: dict[str, Any] = {
            "exchange_status": _resolved_callback_status(
                status,
                writeback_evidence,
                projection_result,
                current_status=_current_exchange_status(task),
            ),
            "last_callback_payload_hash": _payload_hash(payload_json),
        }
        if _projection_status_value(projection_result) == "RECONCILING":
            data["failure_code"] = _optional_text(getattr(projection_result, "reason_code", None))
            data["failure_message"] = _optional_text(getattr(projection_result, "message", None))
        writeback_evidence_id = _optional_int(getattr(writeback_evidence, "id", None))
        if writeback_evidence_id is not None:
            data["writeback_evidence_id"] = writeback_evidence_id
        if source_status_text in _WMS_RCS_DETAIL_FAILURE_STATUS_MAP:
            data.setdefault("failure_code", source_status_text)
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

    async def _record_wms_confirmation(
        self,
        db: AsyncSession,
        *,
        payload_json: Mapping[str, Any],
        trace_id: str | None,
        session_id: str | None,
    ) -> Any | None:
        status = _exchange_status(payload_json)
        if status not in {FullBoxExchangeStatus.WMS_CONFIRMED, FullBoxExchangeStatus.BUSINESS_COMPLETED}:
            return None
        return await self._resolve_writeback_evidence_service().record_confirmation_from_external_http(
            db,
            payload_json=payload_json,
            trace_id=trace_id,
            session_id=session_id,
        )

    async def _project_post_exchange_relations(
        self,
        db: AsyncSession,
        *,
        task: FullBoxExchangeTask,
        payload_json: Mapping[str, Any],
        trace_id: str | None,
    ) -> Any | None:
        status = _exchange_status(payload_json)
        if status not in {FullBoxExchangeStatus.PHYSICAL_COMPLETED, FullBoxExchangeStatus.RESOURCE_PROJECTED}:
            return None
        post_exchange_relations = payload_json.get("post_exchange_relations")
        if "post_exchange_relations" in payload_json and not isinstance(post_exchange_relations, Mapping):
            return SimpleNamespace(
                status="RECONCILING",
                reason_code="POST_EXCHANGE_RELATIONS_INVALID",
                message="满箱交换回调 post_exchange_relations 必须是对象",
            )
        if not isinstance(post_exchange_relations, Mapping):
            return None
        source_event_id = _optional_text(payload_json.get("source_event_id"))
        if source_event_id is None:
            return None

        runtime_context = await self._runtime_context_for_projection(
            db,
            task=task,
            payload_json=payload_json,
        )
        return await self._resolve_relation_projector().record_full_box_exchange_physical_completed(
            db,
            exchange_request_code=str(task.exchange_request_code),
            rack_release_id=str(task.rack_release_id),
            post_exchange_relations=post_exchange_relations,
            source_system=_resource_source_system(payload_json),
            source_event_id=source_event_id,
            source_version=_optional_text(payload_json.get("source_version")),
            source_task_id=_optional_text(payload_json.get("wms_rcs_task_id")),
            occurred_at=payload_json.get("occurred_at"),
            trace_id=trace_id or _optional_text(payload_json.get("trace_id")),
            session_id=runtime_context["session_id"],
            workline_id=runtime_context["workline_id"],
            workline_session_id=runtime_context["workline_session_id"],
            plugin_key=runtime_context["plugin_key"],
            contract_version=runtime_context["contract_version"],
        )

    def _resolve_relation_projector(self) -> Any:
        if self._relation_projector is None:
            from src.app.resource.services.relation_service import resource_relation_service

            self._relation_projector = resource_relation_service
        return self._relation_projector

    async def _runtime_context_for_projection(
        self,
        db: AsyncSession,
        *,
        task: FullBoxExchangeTask,
        payload_json: Mapping[str, Any],
    ) -> dict[str, Any]:
        """解析资源投影需要的 Runtime 上下文。"""

        task_session_id = _optional_int(getattr(task, "session_id", None))
        payload_session_id = _optional_int(payload_json.get("session_id"))
        workline_session_id = task_session_id or payload_session_id
        session = None
        if workline_session_id is not None:
            session = await self._resolve_session_repo().get_by_id(db, workline_session_id)

        return {
            "session_id": _optional_text(workline_session_id),
            "workline_session_id": workline_session_id,
            "workline_id": _optional_int(payload_json.get("workline_id"))
            or _optional_int(getattr(session, "workline_id", None)),
            "plugin_key": _optional_text(payload_json.get("plugin_key"))
            or _optional_text(getattr(session, "plugin_key", None)),
            "contract_version": _optional_text(payload_json.get("contract_version"))
            or _optional_text(getattr(session, "contract_version", None)),
        }

    def _resolve_session_repo(self) -> Any:
        if self._session_repo is None:
            from src.app.workline.repositories import workline_session_repository

            self._session_repo = workline_session_repository
        return self._session_repo

    def _resolve_writeback_evidence_service(self) -> Any:
        if self._writeback_evidence_service is None:
            self._writeback_evidence_service = wms_writeback_evidence_service
        return self._writeback_evidence_service


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


def _normalize_release_snapshots(slot_snapshots: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in sorted(slot_snapshots, key=lambda value: str(value.get("slot_code") or "")):
        slot_code = _optional_text(item.get("slot_code"))
        bin_code = _optional_text(item.get("bin_code"))
        if slot_code is None or bin_code is None:
            continue
        normalized.append(
            {
                "slot_code": slot_code,
                "bin_code": bin_code,
                "bin_type_code": _optional_text(item.get("bin_type_code")),
                "bin_execution_status": _bin_status(item.get("bin_execution_status")),
                "usage_snapshot": item.get("usage_snapshot"),
                "material_summary_json": _mapping_dict(item.get("material_summary_json")),
                "wms_inventory_refs_json": _mapping_dict(item.get("wms_inventory_refs_json")),
                "snapshot_id": _optional_text(item.get("snapshot_id")),
                "content_snapshot_hash": _optional_text(item.get("content_snapshot_hash")),
            }
        )
    return normalized


def _bin_status(value: Any) -> BinStatus:
    status_text = _optional_text(value)
    if status_text is None:
        return BinStatus.FULL_SNAPSHOT
    try:
        return BinStatus(status_text)
    except ValueError:
        return BinStatus.UNKNOWN


def _mapping_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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


def _evidence_code(idempotency_key: str) -> str:
    return f"WMSWB-{idempotency_key.removeprefix('sha256:')[:24]}"


_WMS_RCS_DETAIL_FAILURE_STATUS_MAP = {
    "REJECTED_EXCHANGE_AREA_FULL": FullBoxExchangeStatus.REJECTED,
    "REJECTED_EMPTY_BIN_UNAVAILABLE": FullBoxExchangeStatus.REJECTED,
    "FAILED_AGV": FullBoxExchangeStatus.FAILED,
    "FAILED_CTU": FullBoxExchangeStatus.FAILED,
    "UNKNOWN": FullBoxExchangeStatus.RECONCILING,
}
_FULL_BOX_EXCHANGE_STATUS_RANK = {
    FullBoxExchangeStatus.REQUESTED: 10,
    FullBoxExchangeStatus.ACCEPTED: 20,
    FullBoxExchangeStatus.QUEUED: 30,
    FullBoxExchangeStatus.IN_PROGRESS: 40,
    FullBoxExchangeStatus.PHYSICAL_COMPLETED: 50,
    FullBoxExchangeStatus.RESOURCE_PROJECTED: 60,
    FullBoxExchangeStatus.WMS_CONFIRMED: 70,
    FullBoxExchangeStatus.RECONCILING: 80,
    FullBoxExchangeStatus.WMS_REJECTED: 80,
    FullBoxExchangeStatus.REJECTED: 80,
    FullBoxExchangeStatus.FAILED: 80,
    FullBoxExchangeStatus.CANCELLED: 80,
    FullBoxExchangeStatus.BUSINESS_COMPLETED: 90,
}


def _exchange_status_text(payload_json: Mapping[str, Any]) -> str:
    status_text = _optional_text(payload_json.get("exchange_status"))
    if status_text is None:
        raise ValueError("exchange_status is required")
    return status_text.upper()


def _exchange_status(payload_json: Mapping[str, Any]) -> FullBoxExchangeStatus:
    status_text = _exchange_status_text(payload_json)
    try:
        return FullBoxExchangeStatus(status_text)
    except ValueError as exc:
        mapped_status = _WMS_RCS_DETAIL_FAILURE_STATUS_MAP.get(status_text)
        if mapped_status is not None:
            return mapped_status
        raise ValueError(f"unsupported full box exchange_status: {status_text}") from exc


def _resource_source_system(payload_json: Mapping[str, Any]) -> Any:
    from src.app.resource.models import ResourceSourceSystem

    source_system = _optional_text(payload_json.get("source_system"))
    try:
        return ResourceSourceSystem(source_system)
    except (TypeError, ValueError):
        return ResourceSourceSystem.WMS


def _projection_status_value(projection_result: Any | None) -> str | None:
    if projection_result is None:
        return None
    status = getattr(projection_result, "status", None)
    return str(getattr(status, "value", status)) if status is not None else None


def _resolved_callback_status(
    status: FullBoxExchangeStatus,
    writeback_evidence: Any | None,
    projection_result: Any | None,
    *,
    current_status: FullBoxExchangeStatus | None = None,
) -> FullBoxExchangeStatus:
    projection_status = _projection_status_value(projection_result)
    if projection_status == "DUPLICATE" and current_status is not None:
        return current_status
    if status == FullBoxExchangeStatus.PHYSICAL_COMPLETED and projection_status == "PROJECTED":
        resolved_status = FullBoxExchangeStatus.RESOURCE_PROJECTED
    elif projection_status == "RECONCILING":
        resolved_status = FullBoxExchangeStatus.RECONCILING
    else:
        resolved_status = status
    return _monotonic_exchange_status(current_status, resolved_status)


def _monotonic_exchange_status(
    current_status: FullBoxExchangeStatus | None,
    candidate_status: FullBoxExchangeStatus,
) -> FullBoxExchangeStatus:
    if current_status is None:
        return candidate_status
    current_rank = _FULL_BOX_EXCHANGE_STATUS_RANK.get(current_status)
    candidate_rank = _FULL_BOX_EXCHANGE_STATUS_RANK.get(candidate_status)
    if current_rank is None or candidate_rank is None:
        return candidate_status
    if current_rank > candidate_rank:
        return current_status
    return candidate_status


def _callback_identity_mismatched(task: Any, payload_json: Mapping[str, Any], *, trace_id: str | None) -> bool:
    expected_dispatch_key = _optional_text(getattr(task, "dispatch_key", None))
    callback_dispatch_key = _optional_text(payload_json.get("dispatch_key"))
    if expected_dispatch_key is not None and callback_dispatch_key != expected_dispatch_key:
        return True

    expected_rack_release_id = _optional_text(getattr(task, "rack_release_id", None))
    callback_rack_release_id = _optional_text(payload_json.get("rack_release_id"))
    if expected_rack_release_id is not None and callback_rack_release_id != expected_rack_release_id:
        return True

    expected_trace_id = _optional_text(getattr(task, "trace_id", None))
    if expected_trace_id is None:
        return False
    callback_trace_ids = {
        value
        for value in (
            _optional_text(payload_json.get("trace_id")),
            _optional_text(trace_id),
        )
        if value is not None
    }
    return not callback_trace_ids or any(value != expected_trace_id for value in callback_trace_ids)


def _current_exchange_status(task: Any) -> FullBoxExchangeStatus | None:
    raw_status = getattr(task, "exchange_status", None)
    status_text = _optional_text(getattr(raw_status, "value", raw_status))
    if status_text is None:
        return None
    try:
        return FullBoxExchangeStatus(status_text)
    except ValueError:
        return None


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
