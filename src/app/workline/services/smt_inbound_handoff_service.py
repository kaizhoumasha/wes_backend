"""SMT 入库 handoff 应用服务。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from src.app.workline.domain.services.smt_inbound_handoff_reason import (
    SMT_INBOUND_HANDOFF_REASON_CATALOG,
    SmtInboundHandoffReasonCatalog,
    SmtInboundHandoffReasonCode,
)
from src.app.workline.domain.services.smt_inbound_handoff_route_service import (
    SmtInboundHandoffRouteService,
    smt_inbound_handoff_route_service,
)
from src.app.workline.domain.services.smt_usage_policy import SMT_USAGE_POLICY, SmtUsagePolicy
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.repositories.smt_inbound_handoff_repository import (
    SmtInboundHandoffRepository,
    smt_inbound_handoff_repository,
)
from src.app.workline.services.inbox_service import WorklineInboxService, inbox_service
from src.utils.timezone import timezone
from src.workline_plugins.smt_sorting_inbound.constants import (
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.handling.services import HandlingOperationService


_FULL_BOX_EXCHANGE_OPERATION_TYPE = "SINGLE_LAYER_FULL_BOX_EXCHANGE"
_DIRECT_SORTING_DECISION = "DIRECT_SORTING"
_PREFERRED_EXCHANGE_REQUESTED_DECISION = "PREFERRED_FULL_BOX_EXCHANGE_REQUESTED"
_PREFERRED_EXCHANGE_FALLBACK_DECISION = "PREFERRED_FULL_BOX_EXCHANGE_FALLBACK_SORTING"
_REQUIRED_EXCHANGE_REQUESTED_DECISION = "REQUIRED_FULL_BOX_EXCHANGE_REQUESTED"
_FULL_BOX_EXCHANGED_DECISION = "FULL_BOX_EXCHANGED"
_RECONCILING_DECISION = "RECONCILING"
_REJECTED_OR_FAILED_STATUSES = {"FAILED", "FAILED_CTU", "WMS_REJECTED", "REJECTED", "ERROR", "UNKNOWN"}
_TIMEOUT_STATUSES = {"TIMEOUT", "TIMED_OUT"}
_SUCCESS_STATUSES = {"SUCCEEDED", "SUCCESS", "COMPLETED", "BUSINESS_COMPLETED"}
_PHYSICAL_COMPLETED_STATUSES = {"PHYSICAL_COMPLETED", "RESOURCE_PROJECTED"}
_RELATION_READY_STATUSES = {"READY", "REMAINING", "UNCHANGED", "NOT_EXCHANGED"}
_TERMINAL_ITEM_STATUSES = {
    SmtInboundHandoffSourceItemStatus.SORTED,
    SmtInboundHandoffSourceItemStatus.EXCHANGED,
    SmtInboundHandoffSourceItemStatus.SKIPPED,
}
_SORTING_IN_PROGRESS_ITEM_STATUSES = {
    SmtInboundHandoffSourceItemStatus.PICKED,
    SmtInboundHandoffSourceItemStatus.SORTING,
}
_CLAIMED_ITEM_STATUSES = {
    SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
    SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING,
}
_FORBIDDEN_EXTERNAL_MOVE_FIELDS = {
    "dispatch_key",
    "external_target_code",
    "outbox_id",
    "payload_json",
    "http_headers",
    "url",
    "auth",
    "retry",
}
_SOURCE_PICK_REQUESTED_EVENT = "SORTING_SOURCE_PICK_REQUESTED"


class SmtInboundHandoffService:
    """处理粗分机 release fact 到 handoff demand 的幂等入口。"""

    def __init__(
        self,
        *,
        repository: SmtInboundHandoffRepository = smt_inbound_handoff_repository,
        usage_policy: SmtUsagePolicy = SMT_USAGE_POLICY,
        reason_catalog: SmtInboundHandoffReasonCatalog = SMT_INBOUND_HANDOFF_REASON_CATALOG,
        handling_operation_service: HandlingOperationService | None = None,
        route_service: SmtInboundHandoffRouteService = smt_inbound_handoff_route_service,
        inbox_service: WorklineInboxService = inbox_service,
    ) -> None:
        self.repository = repository
        self.usage_policy = usage_policy
        self.reason_catalog = reason_catalog
        self.handling_operation_service = handling_operation_service
        self.route_service = route_service
        self.inbox_service = inbox_service

    async def create_or_get_from_release(
        self,
        db: AsyncSession,
        *,
        rack_release_id: str | None = None,
        single_layer_rack_code: str | None = None,
        source_workline_id: int | None = None,
        source_workline_code: str | None = None,
        release_reason_code: str | None = None,
        bin_snapshots: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        business_demand_key: str | None = None,
        station_code: str | None = None,
        **release_evidence: Any,
    ) -> SmtInboundHandoffDemand:
        """从 release fact 幂等创建或返回已有 handoff demand。"""

        snapshot_doc, snapshots = self._normalize_bin_snapshots(bin_snapshots)
        release_fact = {
            "rack_release_id": rack_release_id,
            "single_layer_rack_code": single_layer_rack_code,
            "source_workline_id": source_workline_id,
            "source_workline_code": source_workline_code,
            "release_reason_code": release_reason_code,
            "bin_snapshots": snapshots,
            "trace_id": trace_id,
            "business_demand_key": business_demand_key,
            "station_code": station_code,
            **release_evidence,
        }
        resolved_release_id = self._release_id_or_hold_key(
            rack_release_id,
            business_demand_key=business_demand_key,
            trace_id=trace_id,
            release_fact=release_fact,
        )

        existing = await self.repository.get_demand_by_release_id(db, resolved_release_id)
        if existing is not None:
            return existing

        failure_code = self._release_fact_failure_code(
            rack_release_id=rack_release_id,
            single_layer_rack_code=single_layer_rack_code,
            snapshots=snapshots,
        )
        if failure_code is None:
            failure_code = self._usage_failure_code(snapshots)
        source_items = (
            []
            if failure_code is not None
            else self._source_items_from_snapshots(
                rack_release_id=resolved_release_id,
                snapshots=snapshots,
            )
        )
        if failure_code is None and not source_items:
            failure_code = SmtInboundHandoffReasonCode.RELEASE_SNAPSHOT_INVALID.value

        demand = await self.repository.create_or_get_demand_by_release(
            db,
            self._demand_data(
                rack_release_id=resolved_release_id,
                single_layer_rack_code=single_layer_rack_code,
                source_workline_id=source_workline_id,
                source_workline_code=source_workline_code,
                release_reason_code=release_reason_code,
                snapshot_doc=snapshot_doc,
                trace_id=trace_id,
                failure_code=failure_code,
            ),
        )
        if failure_code is None and getattr(demand, "id", None) is not None:
            await self.repository.create_source_items_idempotent(
                db,
                [
                    {
                        **item,
                        "handoff_demand_id": int(cast("int", demand.id)),
                    }
                    for item in source_items
                ],
            )
        return demand

    async def evaluate(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        prefer_full_box_exchange: bool = False,
        trace_id: str | None = None,
    ) -> SmtInboundHandoffDemand:
        """按 release usage 决定直接分拣或发起满箱交换。"""

        if demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD:
            return await self.recalculate_demand_status(db, demand, reason="evaluate")

        snapshots = self._snapshots_from_demand(demand)
        usage_decision = self._usage_band_from_snapshots(snapshots)
        if usage_decision[1] is not None:
            self._apply_failure(demand, usage_decision[1])
            return await self.recalculate_demand_status(db, demand, reason="evaluate")

        band = usage_decision[0]
        if band == "DIRECT_SORTING" or (band == "PREFERRED_FULL_BOX_EXCHANGE" and not prefer_full_box_exchange):
            demand.decision_status = _DIRECT_SORTING_DECISION
            demand.status = SmtInboundHandoffDemandStatus.READY_FOR_SORTING
            demand.failure_code = None
            demand.failure_message = None
            db.add(demand)
            return await self.recalculate_demand_status(db, demand, reason="evaluate")

        decision_status = (
            _PREFERRED_EXCHANGE_REQUESTED_DECISION
            if band == "PREFERRED_FULL_BOX_EXCHANGE"
            else _REQUIRED_EXCHANGE_REQUESTED_DECISION
        )
        await self._request_full_box_exchange(
            db,
            demand=demand,
            snapshots=snapshots,
            operation_key=self._full_box_exchange_operation_key(demand),
            decision_status=decision_status,
            trace_id=trace_id,
        )
        return await self.recalculate_demand_status(db, demand, reason="evaluate")

    async def handle_exchange_callback(
        self,
        db: AsyncSession,
        *,
        callback_payload: Mapping[str, Any],
        handling_operation_key: str | None = None,
        trace_id: str | None = None,
    ) -> SmtInboundHandoffDemand:
        """按 WMS/RCS full-box exchange 回调推进 handoff demand。"""

        _ = trace_id
        operation_key = self._resolve_handling_operation_key(handling_operation_key, callback_payload)
        if operation_key is None:
            raise ValueError("handling_operation_key 不能为空")
        demand = await self.repository.get_demand_by_handling_operation_key(db, operation_key)
        if demand is None:
            raise ValueError(f"未找到满箱交换 handoff demand: {operation_key}")

        incoming_release_id = self._text_or_none(callback_payload.get("rack_release_id"))
        if incoming_release_id is not None and incoming_release_id != demand.rack_release_id:
            self._apply_failure(
                demand,
                SmtInboundHandoffReasonCode.WMS_RCS_RACK_RELEASE_ID_MISMATCH.value,
                message=(
                    f"WMS/RCS 回调 rack_release_id={incoming_release_id} 与 demand {demand.rack_release_id} 不一致"
                ),
            )
            return await self.recalculate_demand_status(db, demand, reason="exchange_callback")

        status = self._exchange_callback_status(callback_payload)
        if status in _SUCCESS_STATUSES or (
            status in _PHYSICAL_COMPLETED_STATUSES and self._has_post_exchange_relations(callback_payload)
        ):
            await self._apply_post_exchange_relations(
                db,
                demand=demand,
                post_exchange_relations=callback_payload.get("post_exchange_relations"),
            )
            demand.status = SmtInboundHandoffDemandStatus.FULL_BOX_EXCHANGED
            demand.decision_status = _FULL_BOX_EXCHANGED_DECISION
            demand.failure_code = None
            demand.failure_message = None
            db.add(demand)
            return await self.recalculate_demand_status(db, demand, reason="exchange_callback")

        if status in _PHYSICAL_COMPLETED_STATUSES:
            self._apply_failure(demand, SmtInboundHandoffReasonCode.POST_EXCHANGE_RELATIONS_MISSING.value)
            demand.status = SmtInboundHandoffDemandStatus.RECONCILING
            demand.decision_status = _RECONCILING_DECISION
            db.add(demand)
            return await self.recalculate_demand_status(db, demand, reason="exchange_callback")

        if status in _REJECTED_OR_FAILED_STATUSES and self._is_preferred_exchange(demand):
            demand.status = SmtInboundHandoffDemandStatus.READY_FOR_SORTING
            demand.decision_status = _PREFERRED_EXCHANGE_FALLBACK_DECISION
            demand.failure_code = None
            demand.failure_message = None
            db.add(demand)
            return await self.recalculate_demand_status(db, demand, reason="exchange_callback")

        if status in _TIMEOUT_STATUSES:
            self._apply_failure(
                demand,
                SmtInboundHandoffReasonCode.WMS_RCS_TIMEOUT.value,
                message=self._callback_error_message(callback_payload),
            )
        elif status in _REJECTED_OR_FAILED_STATUSES:
            self._apply_failure(
                demand,
                SmtInboundHandoffReasonCode.WMS_RCS_REJECTED.value,
                message=self._callback_error_message(callback_payload),
            )
        else:
            demand.decision_status = demand.decision_status or _REQUIRED_EXCHANGE_REQUESTED_DECISION
            db.add(demand)
            return await self.recalculate_demand_status(db, demand, reason="exchange_callback")
        return await self.recalculate_demand_status(db, demand, reason="exchange_callback")

    async def manual_reconcile_exchange(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        post_exchange_relations: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        trace_id: str | None = None,
    ) -> SmtInboundHandoffDemand:
        """人工补充满箱交换对账 evidence 后推进 demand。"""

        _ = trace_id
        await self._apply_post_exchange_relations(
            db,
            demand=demand,
            post_exchange_relations=post_exchange_relations,
        )
        demand.status = SmtInboundHandoffDemandStatus.FULL_BOX_EXCHANGED
        demand.decision_status = _FULL_BOX_EXCHANGED_DECISION
        demand.failure_code = None
        demand.failure_message = None
        db.add(demand)
        return await self.recalculate_demand_status(db, demand, reason="manual_reconcile_exchange")

    async def retry_exchange(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        trace_id: str | None = None,
    ) -> SmtInboundHandoffDemand:
        """人工重试满箱交换，使用独立重试幂等 key。"""

        snapshots = self._snapshots_from_demand(demand)
        await self._request_full_box_exchange(
            db,
            demand=demand,
            snapshots=snapshots,
            operation_key=f"{self._full_box_exchange_operation_key(demand)}:retry",
            decision_status=_REQUIRED_EXCHANGE_REQUESTED_DECISION,
            trace_id=trace_id,
        )
        return await self.recalculate_demand_status(db, demand, reason="retry_exchange")

    async def recalculate_demand_status(
        self,
        db: AsyncSession,
        demand: SmtInboundHandoffDemand,
        *,
        reason: str | None = None,
    ) -> SmtInboundHandoffDemand:
        """统一重算 demand 摘要状态，避免各推进路径自行推断。"""

        _ = reason
        demand_id = getattr(demand, "id", None)
        if not isinstance(demand_id, int):
            db.add(demand)
            await db.flush()
            return demand

        if demand.status == SmtInboundHandoffDemandStatus.CANCELLED:
            db.add(demand)
            await db.flush()
            return demand
        if demand.status == SmtInboundHandoffDemandStatus.RECONCILING:
            db.add(demand)
            await db.flush()
            return demand
        if demand.failure_code is not None:
            demand.status = SmtInboundHandoffDemandStatus.MANUAL_HOLD
            db.add(demand)
            await db.flush()
            return demand
        if demand.status == SmtInboundHandoffDemandStatus.WAITING_FULL_BOX_EXCHANGE:
            db.add(demand)
            await db.flush()
            return demand

        items = await self.repository.list_source_items(db, demand_id)
        if any(item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD for item in items):
            demand.status = SmtInboundHandoffDemandStatus.MANUAL_HOLD
        elif items and all(item.status in _TERMINAL_ITEM_STATUSES for item in items):
            demand.status = SmtInboundHandoffDemandStatus.COMPLETED
        elif any(item.status in _SORTING_IN_PROGRESS_ITEM_STATUSES for item in items):
            demand.status = SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS
        elif any(item.status in _CLAIMED_ITEM_STATUSES for item in items):
            demand.status = SmtInboundHandoffDemandStatus.CLAIMED_BY_SORTING
        elif any(item.status == SmtInboundHandoffSourceItemStatus.READY for item in items):
            demand.status = SmtInboundHandoffDemandStatus.READY_FOR_SORTING
        db.add(demand)
        await db.flush()
        return demand

    async def claim_next_source_item(
        self,
        db: AsyncSession,
        *,
        trace_id: str | None = None,
    ) -> Any:
        """认领下一条 READY source item，并创建内部 source-pick Inbox。"""

        now = timezone.now_for_db()
        item = await self.repository.claim_next_ready_source_item(db, now=now)
        if item is None:
            return self._claim_result("EMPTY")
        demand = await db.get(SmtInboundHandoffDemand, item.handoff_demand_id)
        if demand is None:
            return self._claim_result("EMPTY")

        candidates = await self.repository.list_sorting_candidate_worklines(db)
        route = await self.route_service.resolve_route(
            db,
            demand=demand,
            source_item=item,
            candidate_worklines=candidates,
        )
        if getattr(route, "kind", None) == "MANUAL_HOLD" or bool(getattr(route, "manual_hold", False)):
            self._apply_item_failure(item, str(route.failure_code), message=getattr(route, "failure_message", None))
            self._apply_failure(demand, str(route.failure_code), message=getattr(route, "failure_message", None))
            db.add(item)
            await self.recalculate_demand_status(db, demand, reason="claim_route_manual_hold")
            return route

        if getattr(route, "kind", None) == "RETRY" or bool(getattr(route, "retryable", False)):
            item.status = SmtInboundHandoffSourceItemStatus.READY
            item.next_attempt_at = getattr(route, "next_attempt_at", None)
            item.failure_code = getattr(route, "failure_code", None)
            item.failure_message = getattr(route, "failure_message", None)
            demand.next_attempt_at = getattr(route, "next_attempt_at", None)
            db.add(item)
            db.add(demand)
            await self.recalculate_demand_status(db, demand, reason="claim_route_retry")
            return route

        workline_id = getattr(route, "selected_workline_id", None)
        workline_code = getattr(route, "selected_workline_code", None)
        if not isinstance(workline_id, int):
            self._apply_item_failure(item, SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value)
            self._apply_failure(demand, SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value)
            db.add(item)
            await self.recalculate_demand_status(db, demand, reason="claim_route_invalid")
            return self._claim_result("MANUAL_HOLD", failure_code=SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value)

        session = await self._create_sorting_claim_session(
            db,
            workline_id=workline_id,
            workline_code=workline_code,
            demand=demand,
            item=item,
            trace_id=trace_id,
            route_evidence=getattr(route, "route_evidence", None),
        )
        inbox = await self._create_source_pick_request_inbox(
            db,
            demand=demand,
            item=item,
            session=session,
            workline_id=workline_id,
            trace_id=trace_id,
            route_evidence=getattr(route, "route_evidence", None),
        )

        item.status = SmtInboundHandoffSourceItemStatus.PICK_REQUESTED
        item.target_workline_id = workline_id
        item.target_workline_code = self._text_or_none(workline_code)
        item.sorting_session_id = cast("int", session.id)
        item.source_pick_inbox_id = cast("int", inbox.id)
        item.claimed_at = now
        item.failure_code = None
        item.failure_message = None
        item.next_attempt_at = None
        demand.target_workline_id = workline_id
        demand.target_workline_code = self._text_or_none(workline_code)
        demand.failure_code = None
        demand.failure_message = None
        db.add(item)
        db.add(demand)
        await self.recalculate_demand_status(db, demand, reason="claim_source_item")
        return self._claim_result(
            "CLAIMED",
            demand=demand,
            source_item=item,
            inbox=inbox,
            session=session,
        )

    async def release_source_pick_dead_letter_for_retry(
        self,
        db: AsyncSession,
        *,
        source_item_id: int,
        trace_id: str | None = None,
    ) -> SmtInboundHandoffSourceItem:
        """释放 source-pick dead-letter item，使下一次 claim 使用新 attempt。"""

        _ = trace_id
        item = await self.repository.get_source_item_by_id(db, source_item_id)
        if item is None:
            raise ValueError(f"未找到 handoff source item: {source_item_id}")
        item.claim_attempt_no += 1
        item.status = SmtInboundHandoffSourceItemStatus.READY
        item.source_pick_inbox_id = None
        item.source_pick_command_id = None
        item.source_pick_command_code = None
        item.source_pick_dispatch_key = None
        item.sorting_session_id = None
        item.target_workline_id = None
        item.target_workline_code = None
        item.failure_code = None
        item.failure_message = None
        item.claimed_at = None
        item.next_attempt_at = None
        db.add(item)
        demand = await db.get(SmtInboundHandoffDemand, item.handoff_demand_id)
        if demand is not None:
            demand.failure_code = None
            demand.failure_message = None
            db.add(demand)
            await self.recalculate_demand_status(db, demand, reason="release_source_pick_dead_letter_for_retry")
        else:
            await db.flush()
        return item

    async def record_source_pick_command_correlation(
        self,
        db: AsyncSession,
        *,
        handoff_demand_id: int,
        source_item_id: int,
        claim_attempt_no: int,
        source_pick_inbox_id: int,
        command_id: int,
        command_code: str,
        dispatch_key: str,
        trace_id: str | None = None,
    ) -> SmtInboundHandoffSourceItem:
        """记录首盘 source-pick command/outbox evidence，并推进 claim 后状态。"""

        _ = trace_id
        item = await self.repository.get_source_item_for_update(db, source_item_id)
        if item is None:
            raise ValueError(f"未找到 handoff source item: {source_item_id}")
        if item.handoff_demand_id != handoff_demand_id:
            raise ValueError("source pick command correlation demand/item 不匹配")
        if item.claim_attempt_no != claim_attempt_no:
            raise ValueError("source pick command correlation claim_attempt_no 不匹配")
        if item.source_pick_inbox_id != source_pick_inbox_id:
            raise ValueError("source pick command correlation inbox 不匹配")
        if item.status not in _CLAIMED_ITEM_STATUSES:
            raise ValueError(f"source pick command correlation 状态不允许: {item.status}")

        item.status = SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING
        item.source_pick_command_id = command_id
        item.source_pick_command_code = self._text_or_none(command_code)
        item.source_pick_dispatch_key = self._text_or_none(dispatch_key)
        item.failure_code = None
        item.failure_message = None
        item.next_attempt_at = None
        db.add(item)

        demand = await db.get(SmtInboundHandoffDemand, handoff_demand_id)
        if demand is not None:
            demand.failure_code = None
            demand.failure_message = None
            db.add(demand)
            await self.recalculate_demand_status(db, demand, reason="source_pick_command_correlation")
        else:
            await db.flush()
        return item

    def _demand_data(
        self,
        *,
        rack_release_id: str,
        single_layer_rack_code: str | None,
        source_workline_id: int | None,
        source_workline_code: str | None,
        release_reason_code: str | None,
        snapshot_doc: dict[str, Any],
        trace_id: str | None,
        failure_code: str | None,
    ) -> dict[str, Any]:
        reason = self.reason_catalog.get(failure_code) if failure_code is not None else None
        return {
            "demand_key": f"smt-inbound-handoff:{rack_release_id}",
            "rack_release_id": rack_release_id,
            "source_workline_id": source_workline_id,
            "source_workline_code": self._text_or_none(source_workline_code),
            "single_layer_rack_code": self._text_or_none(single_layer_rack_code) or "",
            "release_reason_code": self._text_or_none(release_reason_code),
            "bin_snapshots_json": snapshot_doc,
            "status": (
                SmtInboundHandoffDemandStatus.MANUAL_HOLD
                if failure_code is not None
                else SmtInboundHandoffDemandStatus.CREATED
            ),
            "failure_code": reason.failure_code if reason is not None else None,
            "failure_message": reason.default_message if reason is not None else None,
            "trace_id": self._text_or_none(trace_id),
        }

    async def _create_sorting_claim_session(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        workline_code: str | None,
        demand: SmtInboundHandoffDemand,
        item: SmtInboundHandoffSourceItem,
        trace_id: str | None,
        route_evidence: Any,
    ) -> WorklineSession:
        event_id = self._source_pick_event_id(item)
        session = WorklineSession(
            session_code=f"smt-inbound-handoff:{demand.id}:source-item:{item.id}:claim:{item.claim_attempt_no}",
            workline_id=workline_id,
            plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
            run_mode=RunMode.AUTO,
            business_key=demand.demand_key,
            status=SessionStatus.RUNNING,
            context_json={
                "sorting": {
                    "source_pick_request": {
                        "handoff_demand_id": demand.id,
                        "handoff_source_item_id": item.id,
                        "claim_attempt_no": item.claim_attempt_no,
                        "event_id": event_id,
                        "target_workline_code": self._text_or_none(workline_code),
                        "route_evidence": self._dict_or_empty(route_evidence),
                    }
                }
            },
            context_schema_version="smt-sorting-inbound.v1",
            contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
            started_at=timezone.now_for_db(),
            trace_id=self._text_or_none(trace_id) or demand.trace_id,
        )
        db.add(session)
        await db.flush()
        return session

    async def _create_source_pick_request_inbox(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        item: SmtInboundHandoffSourceItem,
        session: WorklineSession,
        workline_id: int,
        trace_id: str | None,
        route_evidence: Any,
    ) -> Any:
        session_id = getattr(session, "id", None)
        item_id = getattr(item, "id", None)
        demand_id = getattr(demand, "id", None)
        if not isinstance(session_id, int) or not isinstance(item_id, int) or not isinstance(demand_id, int):
            raise TypeError("source pick request requires persisted demand, source item and session")
        event_id = self._source_pick_event_id(item)
        resolved_trace_id = self._text_or_none(trace_id) or self._text_or_none(demand.trace_id) or event_id
        return await self.inbox_service.create_internal_event_inbox(
            db,
            event_type=_SOURCE_PICK_REQUESTED_EVENT,
            canonical_event_type=_SOURCE_PICK_REQUESTED_EVENT,
            data={
                "handoff_demand_id": demand_id,
                "handoff_source_item_id": item_id,
                "claim_attempt_no": item.claim_attempt_no,
                "rack_release_id": demand.rack_release_id,
                "single_layer_rack_code": demand.single_layer_rack_code,
                "bin_code": item.bin_code,
                "bin_cell_index": item.bin_cell_index,
                "bin_cell_code": item.bin_cell_code,
                "material_identity_key": item.material_identity_key,
                "pkg_code": item.pkg_code,
                "reel_thickness_mm": str(item.reel_thickness_mm) if item.reel_thickness_mm is not None else None,
                "route_evidence": self._dict_or_empty(route_evidence),
            },
            session_id=session_id,
            workline_id=workline_id,
            trace_id=resolved_trace_id,
            event_id=event_id,
            causation_id=f"handoff-source-item:{item_id}",
            auto_commit=False,
        )

    @staticmethod
    def _source_pick_event_id(item: SmtInboundHandoffSourceItem) -> str:
        return f"smt-inbound-handoff-source-item:{item.id}:claim:{item.claim_attempt_no}"

    def _apply_item_failure(
        self,
        item: SmtInboundHandoffSourceItem,
        failure_code: str,
        *,
        message: str | None = None,
    ) -> None:
        reason = self.reason_catalog.get(failure_code)
        item.status = SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
        item.failure_code = reason.failure_code
        item.failure_message = self._text_or_none(message) or reason.default_message

    @staticmethod
    def _claim_result(kind: str, **values: Any) -> SimpleNamespace:
        return SimpleNamespace(kind=kind, **values)

    @staticmethod
    def _dict_or_empty(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    def _release_fact_failure_code(
        self,
        *,
        rack_release_id: str | None,
        single_layer_rack_code: str | None,
        snapshots: list[dict[str, Any]],
    ) -> str | None:
        if self._text_or_none(rack_release_id) is None or self._text_or_none(single_layer_rack_code) is None:
            return SmtInboundHandoffReasonCode.RELEASE_FACT_MISSING.value
        if not snapshots:
            return SmtInboundHandoffReasonCode.RELEASE_SNAPSHOT_INVALID.value
        return None

    def _usage_failure_code(self, snapshots: Sequence[Mapping[str, Any]]) -> str | None:
        for snapshot in snapshots:
            result = self.usage_policy.resolve_release_bin_usage(snapshot)
            if not result.valid:
                return result.failure_code or SmtInboundHandoffReasonCode.USAGE_INVALID.value
        return None

    def _source_items_from_snapshots(
        self,
        *,
        rack_release_id: str,
        snapshots: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for snapshot_index, snapshot in enumerate(snapshots, start=1):
            bin_code = self._text_or_none(snapshot.get("bin_code") or snapshot.get("bin_id"))
            cells = snapshot.get("cells") or snapshot.get("bin_cells")
            if not isinstance(cells, Sequence) or isinstance(cells, (str, bytes)):
                continue
            for cell_index, cell in enumerate(cells, start=1):
                if not isinstance(cell, Mapping) or self._cell_is_empty(cell):
                    continue
                item = self._source_item_from_cell(
                    rack_release_id=rack_release_id,
                    bin_code=bin_code,
                    fallback_index=f"{snapshot_index}-{cell_index}",
                    cell=cell,
                )
                if item is not None:
                    items.append(item)
        return items

    def _source_item_from_cell(
        self,
        *,
        rack_release_id: str,
        bin_code: str | None,
        fallback_index: str,
        cell: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        resolved_bin_code = self._text_or_none(cell.get("bin_code") or cell.get("bin_id")) or bin_code
        bin_cell_index = self._int_or_none(cell.get("bin_cell_index") or cell.get("cell_index"))
        bin_cell_code = self._text_or_none(cell.get("bin_cell_code") or cell.get("bin_cell_location"))
        material_identity_key = self._text_or_none(cell.get("material_identity_key"))
        pkg_code = self._text_or_none(cell.get("pkg_code") or cell.get("PkgID"))
        if resolved_bin_code is None or (material_identity_key is None and pkg_code is None):
            return None
        item_identity = bin_cell_code or str(bin_cell_index or fallback_index)
        return {
            "item_key": f"{rack_release_id}:{resolved_bin_code}:{item_identity}",
            "bin_code": resolved_bin_code,
            "bin_cell_index": bin_cell_index,
            "bin_cell_code": bin_cell_code,
            "material_identity_key": material_identity_key,
            "pkg_code": pkg_code,
            "reel_thickness_mm": self._decimal_or_none(cell.get("reel_thickness_mm") or cell.get("reel_thickness")),
        }

    async def _request_full_box_exchange(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        snapshots: Sequence[Mapping[str, Any]],
        operation_key: str,
        decision_status: str,
        trace_id: str | None,
    ) -> None:
        moves = self._full_box_exchange_moves(demand=demand, snapshots=snapshots)
        if not moves:
            self._apply_failure(demand, SmtInboundHandoffReasonCode.RELEASE_SNAPSHOT_INVALID.value)
            return

        for move in moves:
            leaked = _FORBIDDEN_EXTERNAL_MOVE_FIELDS.intersection(move)
            if leaked:
                raise ValueError(f"满箱交换 move 泄漏外部派发字段: {', '.join(sorted(leaked))}")

        await self._handling_operation_service().request_bin_operation(
            db,
            operation_type=_FULL_BOX_EXCHANGE_OPERATION_TYPE,
            operation_key=operation_key,
            moves=moves,
            trace_id=self._text_or_none(trace_id) or self._text_or_none(demand.trace_id) or demand.rack_release_id,
            workline_id=demand.source_workline_id,
            workline_code=demand.source_workline_code,
            carrier_type="CTU",
            carrier_code=demand.single_layer_rack_code,
        )
        demand.handling_operation_key = operation_key
        demand.status = SmtInboundHandoffDemandStatus.WAITING_FULL_BOX_EXCHANGE
        demand.decision_status = decision_status
        demand.failure_code = None
        demand.failure_message = None
        db.add(demand)

    def _handling_operation_service(self) -> HandlingOperationService:
        if self.handling_operation_service is None:
            from src.app.handling.services.operation_service import handling_operation_service

            self.handling_operation_service = handling_operation_service
        return self.handling_operation_service

    def _full_box_exchange_moves(
        self,
        *,
        demand: SmtInboundHandoffDemand,
        snapshots: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        moves: list[dict[str, Any]] = []
        for index, snapshot in enumerate(snapshots, start=1):
            usage_result = self.usage_policy.resolve_release_bin_usage(snapshot)
            if not usage_result.valid or usage_result.usage is None:
                continue
            if self.usage_policy.usage_band(usage_result.usage) == "DIRECT_SORTING":
                continue
            slot_code = self._snapshot_slot_code(snapshot, fallback=index)
            bin_code = self._text_or_none(snapshot.get("bin_code") or snapshot.get("bin_id"))
            moves.append(
                {
                    "source_type": "RACK_SLOT",
                    "source_code": f"{demand.single_layer_rack_code}:{slot_code}",
                    "target_type": "FULL_BOX_EXCHANGE_BUFFER",
                    "target_code": "SMT_FULL_BOX_EXCHANGE",
                    "rack_code": demand.single_layer_rack_code,
                    "rack_slot_code": slot_code,
                    "bin_code": bin_code,
                    "required": True,
                }
            )
        return moves

    @staticmethod
    def _full_box_exchange_operation_key(demand: SmtInboundHandoffDemand) -> str:
        return f"smt-inbound-handoff:{demand.rack_release_id}:full-box-exchange"

    def _usage_band_from_snapshots(
        self,
        snapshots: Sequence[Mapping[str, Any]],
    ) -> tuple[str | None, str | None]:
        usages: list[float] = []
        if not snapshots:
            return None, SmtInboundHandoffReasonCode.RELEASE_SNAPSHOT_INVALID.value
        for snapshot in snapshots:
            result = self.usage_policy.resolve_release_bin_usage(snapshot)
            if not result.valid or result.usage is None:
                return None, result.failure_code or SmtInboundHandoffReasonCode.USAGE_INVALID.value
            usages.append(result.usage)
        if not usages:
            return None, SmtInboundHandoffReasonCode.RELEASE_SNAPSHOT_INVALID.value
        return self.usage_policy.usage_band(max(usages)), None

    @staticmethod
    def _snapshots_from_demand(demand: SmtInboundHandoffDemand) -> list[dict[str, Any]]:
        snapshot_doc = demand.bin_snapshots_json if isinstance(demand.bin_snapshots_json, Mapping) else {}
        raw_snapshots = snapshot_doc.get("bins") or snapshot_doc.get("bin_snapshots")
        return [dict(item) for item in (raw_snapshots or []) if isinstance(item, Mapping)]

    def _apply_failure(
        self,
        demand: SmtInboundHandoffDemand,
        failure_code: str,
        *,
        message: str | None = None,
    ) -> None:
        reason = self.reason_catalog.get(failure_code)
        demand.status = SmtInboundHandoffDemandStatus.MANUAL_HOLD
        demand.failure_code = reason.failure_code
        demand.failure_message = self._text_or_none(message) or reason.default_message

    @classmethod
    def _resolve_handling_operation_key(
        cls,
        handling_operation_key: str | None,
        callback_payload: Mapping[str, Any],
    ) -> str | None:
        explicit_key = cls._text_or_none(handling_operation_key)
        if explicit_key is not None:
            return explicit_key
        dispatch_key = (
            cls._text_or_none(callback_payload.get("dispatch_key"))
            or cls._text_or_none(callback_payload.get("exchange_request_code"))
            or cls._text_or_none(callback_payload.get("request_code"))
        )
        if dispatch_key is None:
            return None
        if dispatch_key.startswith("handling:") and ":move:" in dispatch_key:
            return dispatch_key[len("handling:") : dispatch_key.rfind(":move:")]
        return dispatch_key

    @classmethod
    def _exchange_callback_status(cls, callback_payload: Mapping[str, Any]) -> str:
        raw_status = (
            cls._text_or_none(callback_payload.get("exchange_status"))
            or cls._text_or_none(callback_payload.get("task_status"))
            or cls._text_or_none(callback_payload.get("status"))
            or cls._text_or_none(callback_payload.get("result"))
            or cls._text_or_none(callback_payload.get("external_status"))
        )
        return raw_status.upper() if raw_status is not None else "IN_PROGRESS"

    @classmethod
    def _callback_error_message(cls, callback_payload: Mapping[str, Any]) -> str | None:
        return (
            cls._text_or_none(callback_payload.get("reason_message"))
            or cls._text_or_none(callback_payload.get("error_message"))
            or cls._text_or_none(callback_payload.get("message"))
        )

    @staticmethod
    def _has_post_exchange_relations(callback_payload: Mapping[str, Any]) -> bool:
        relations = callback_payload.get("post_exchange_relations")
        if isinstance(relations, Mapping):
            return bool(relations)
        if isinstance(relations, Sequence) and not isinstance(relations, (str, bytes)):
            return bool(relations)
        return False

    async def _apply_post_exchange_relations(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        post_exchange_relations: Any,
    ) -> None:
        demand_id = getattr(demand, "id", None)
        if not isinstance(demand_id, int):
            return
        relations = self._relation_records(post_exchange_relations)
        items = await self.repository.list_source_items(db, demand_id)
        for item in items:
            relation = self._matching_relation(item, relations)
            if relation is None:
                if item.status not in _TERMINAL_ITEM_STATUSES:
                    item.status = SmtInboundHandoffSourceItemStatus.READY
            elif self._relation_marks_ready(relation):
                item.status = SmtInboundHandoffSourceItemStatus.READY
            else:
                item.status = SmtInboundHandoffSourceItemStatus.EXCHANGED
            item.failure_code = None
            item.failure_message = None
            db.add(item)
        await db.flush()

    @classmethod
    def _relation_records(cls, post_exchange_relations: Any) -> list[Mapping[str, Any]]:
        if isinstance(post_exchange_relations, Sequence) and not isinstance(post_exchange_relations, (str, bytes)):
            return [item for item in post_exchange_relations if isinstance(item, Mapping)]
        if not isinstance(post_exchange_relations, Mapping):
            return []
        records: list[Mapping[str, Any]] = []
        for key in ("items", "relations", "exchanged_items", "post_exchange_relations"):
            value = post_exchange_relations.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                records.extend(item for item in value if isinstance(item, Mapping))
        if records:
            return records
        return [post_exchange_relations] if cls._relation_has_identity(post_exchange_relations) else []

    @staticmethod
    def _relation_has_identity(relation: Mapping[str, Any]) -> bool:
        return any(
            key in relation
            for key in (
                "item_key",
                "source_item_key",
                "material_identity_key",
                "pkg_code",
                "source_pkg_code",
                "bin_code",
            )
        )

    def _matching_relation(
        self,
        item: SmtInboundHandoffSourceItem,
        relations: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any] | None:
        for relation in relations:
            if self._relation_matches_item(item, relation):
                return relation
        return None

    def _relation_matches_item(
        self,
        item: SmtInboundHandoffSourceItem,
        relation: Mapping[str, Any],
    ) -> bool:
        relation_item_key = self._text_or_none(relation.get("item_key") or relation.get("source_item_key"))
        if relation_item_key is not None and relation_item_key == item.item_key:
            return True
        material_key = self._text_or_none(relation.get("material_identity_key"))
        if material_key is not None and material_key == item.material_identity_key:
            return True
        pkg_code = self._text_or_none(relation.get("pkg_code") or relation.get("source_pkg_code"))
        if pkg_code is not None and pkg_code == item.pkg_code:
            return True
        bin_code = self._text_or_none(relation.get("bin_code") or relation.get("source_bin_code"))
        if bin_code is None or bin_code != item.bin_code:
            return False
        cell_code = self._text_or_none(relation.get("bin_cell_code") or relation.get("source_bin_cell_code"))
        if cell_code is not None:
            return cell_code == item.bin_cell_code
        cell_index = self._int_or_none(relation.get("bin_cell_index") or relation.get("source_bin_cell_index"))
        return cell_index is not None and cell_index == item.bin_cell_index

    @classmethod
    def _relation_marks_ready(cls, relation: Mapping[str, Any]) -> bool:
        raw_status = (
            cls._text_or_none(relation.get("exchange_result"))
            or cls._text_or_none(relation.get("status"))
            or cls._text_or_none(relation.get("result"))
        )
        return raw_status is not None and raw_status.upper() in _RELATION_READY_STATUSES

    @staticmethod
    def _is_preferred_exchange(demand: SmtInboundHandoffDemand) -> bool:
        return str(demand.decision_status or "").startswith("PREFERRED_FULL_BOX_EXCHANGE")

    @classmethod
    def _snapshot_slot_code(cls, snapshot: Mapping[str, Any], *, fallback: int) -> str:
        return (
            cls._text_or_none(snapshot.get("slot_code"))
            or cls._text_or_none(snapshot.get("rack_slot_code"))
            or cls._text_or_none(snapshot.get("position_code"))
            or cls._text_or_none(snapshot.get("bin_slot_code"))
            or str(fallback)
        )

    @staticmethod
    def _normalize_bin_snapshots(
        bin_snapshots: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if isinstance(bin_snapshots, Mapping):
            snapshot_doc = dict(bin_snapshots)
            raw_snapshots = snapshot_doc.get("bins") or snapshot_doc.get("bin_snapshots")
        else:
            snapshot_doc = {}
            raw_snapshots = bin_snapshots
        snapshots = [dict(item) for item in (raw_snapshots or []) if isinstance(item, Mapping)]
        snapshot_doc["bins"] = snapshots
        return snapshot_doc, snapshots

    @classmethod
    def _release_id_or_hold_key(
        cls,
        rack_release_id: str | None,
        *,
        business_demand_key: str | None,
        trace_id: str | None,
        release_fact: Mapping[str, Any],
    ) -> str:
        value = cls._text_or_none(rack_release_id)
        if value is not None:
            return value
        fallback = cls._text_or_none(business_demand_key) or cls._text_or_none(trace_id)
        if fallback is not None:
            return f"missing-rack-release:{fallback}"
        digest = hashlib.sha256(json.dumps(release_fact, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return f"missing-rack-release:{digest}"

    @staticmethod
    def _cell_is_empty(cell: Mapping[str, Any]) -> bool:
        status = str(cell.get("status") or cell.get("cell_status") or "").strip().upper()
        return status in {"", "EMPTY", "EMPTY_VERIFIED", "AVAILABLE"}

    @staticmethod
    def _text_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return decimal if decimal.is_finite() else None


smt_inbound_handoff_service = SmtInboundHandoffService()


__all__ = [
    "SmtInboundHandoffService",
    "smt_inbound_handoff_service",
]
