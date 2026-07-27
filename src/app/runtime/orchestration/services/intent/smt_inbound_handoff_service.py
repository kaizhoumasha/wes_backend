# 已从 workline/services/ 迁入
# 旧位置: src/app/workline/services/smt_inbound_handoff_service.py
# (stable PEP 562 re-export shim)
# 新位置: src/app/runtime/orchestration/services/intent/smt_inbound_handoff_service.py
# cleanup-matrix strategy=rebuild,
# target_path=src/app/runtime/orchestration/services/(同 runtime service shim 模式)

"""SMT 入库 handoff 应用服务。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.device.repositories.command_repository import (
    DeviceCommandRepository,
    device_command_repository,
)
from src.app.runtime.capabilities.material_flow.contracts.smt_inbound_handoff_reason import (
    SMT_INBOUND_HANDOFF_REASON_CATALOG,
    SmtInboundHandoffReasonCatalog,
    SmtInboundHandoffReasonCode,
)
from src.app.runtime.capabilities.material_flow.contracts.smt_usage_policy import SMT_USAGE_POLICY, SmtUsagePolicy
from src.app.runtime.capabilities.material_flow.contracts.sorting_inbound_context import SortingInboundContext
from src.app.runtime.capabilities.material_flow.smt_inbound_handoff_route_service import (
    SmtInboundHandoffRouteService,
    smt_inbound_handoff_route_service,
)
from src.app.runtime.orchestration.models.material_unit import MaterialUnit
from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.repository_wiring import smt_inbound_handoff_repository
from src.app.workline.services.plugin_binding_service import (
    WorklinePluginBindingService,
    workline_plugin_binding_service,
)
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_value

_TERMINAL_MATERIAL_OWNER_SESSION_STATUSES = {
    SessionStatus.COMPLETED.value,
    SessionStatus.FAILED.value,
    SessionStatus.CANCELLED.value,
}

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.handling.services import HandlingOperationService
    from src.app.runtime.orchestration.repositories.smt_inbound_handoff_repository import SmtInboundHandoffRepository


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
_HANDOFF_CLAIM_IN_FLIGHT_ITEM_STATUSES = _SORTING_IN_PROGRESS_ITEM_STATUSES | _CLAIMED_ITEM_STATUSES
_CLAIMABLE_DEMAND_STATUSES = {
    SmtInboundHandoffDemandStatus.READY_FOR_SORTING,
}
_RouteProbeCache = dict[tuple[Any, ...], tuple[Any, Any]]
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
_SORTING_TARGET_RACK_POSITION_CODE = "TARGET_STATION"
_CLAIM_ROUTE_PROBE_EVIDENCE_TTL_SECONDS = 5


@dataclass(frozen=True, slots=True)
class SmtInboundHandoffClaimResult:
    """SMT claim 的 typed 结果。"""

    kind: str
    failure_code: str | None = None
    failure_message: str | None = None
    next_attempt_at: Any | None = None
    demand: Any | None = None
    source_item: Any | None = None
    inbox: Any | None = None
    session: Any | None = None


@dataclass(frozen=True, slots=True)
class _SmtSortingClaimRuntime:
    """一次 SMT claim 原子创建的 Workline/Execution 运行锚点。"""

    session: WorklineSession
    execution_session_id: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class SmtInboundHandoffLedgerResult:
    """SMT source item 账本推进结果。"""

    outcome: str
    advanced: bool
    already_terminal: bool
    source_item: Any
    current_demand_id: int | None = None
    demand: Any | None = None
    session: Any | None = None


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
        plugin_binding_service: WorklinePluginBindingService = workline_plugin_binding_service,
        command_repository: DeviceCommandRepository = device_command_repository,
    ) -> None:
        self.repository = repository
        self.usage_policy = usage_policy
        self.reason_catalog = reason_catalog
        self.handling_operation_service = handling_operation_service
        self.route_service = route_service
        self.plugin_binding_service = plugin_binding_service
        self.command_repository = command_repository

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
        demand_id: int | None = None,
        trace_id: str | None = None,
        route_probe_cache: _RouteProbeCache | None = None,
    ) -> Any:
        """两阶段认领下一条 READY source item，并创建内部 source-pick Inbox。"""

        now = timezone.now_for_db()
        demand, candidate = await self._next_claim_candidate(db, demand_id=demand_id, now=now)
        if demand is None or candidate is None:
            return self._claim_result("EMPTY")

        candidate_worklines = await self.repository.list_sorting_candidate_worklines(db)
        route, route_probe_started_at = await self._resolve_claim_route(
            db,
            demand=demand,
            source_item=candidate,
            candidate_worklines=candidate_worklines,
            route_probe_cache=route_probe_cache,
        )
        return await self._claim_routed_candidate(
            db,
            candidate=candidate,
            route=route,
            now=now,
            route_probe_started_at=route_probe_started_at,
            trace_id=trace_id,
        )

    async def _next_claim_candidate(
        self,
        db: AsyncSession,
        *,
        demand_id: int | None,
        now: Any,
    ) -> tuple[SmtInboundHandoffDemand | None, SmtInboundHandoffSourceItem | None]:
        if demand_id is None:
            candidates = await self.repository.list_ready_source_items_for_claim(db, now=now, limit=1)
            candidate = candidates[0] if candidates else None
            if candidate is None:
                return None, None
            demand = await db.get(SmtInboundHandoffDemand, candidate.handoff_demand_id)
            if demand is None or not self._demand_is_claimable(demand):
                return None, None
            return demand, candidate

        demand = await db.get(SmtInboundHandoffDemand, demand_id)
        if demand is None:
            return None, None
        if not self._demand_is_claimable(demand):
            return None, None

        ready_items = [
            item
            for item in await self.repository.list_source_items(db, demand_id)
            if self._source_item_is_ready_and_due(item, now=now)
        ]
        ready_items.sort(
            key=lambda item: (
                getattr(item, "next_attempt_at", None) is not None,
                getattr(item, "next_attempt_at", None) or now,
                getattr(item, "id", 0) or 0,
            )
        )
        return demand, ready_items[0] if ready_items else None

    async def _resolve_claim_route(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        source_item: SmtInboundHandoffSourceItem,
        candidate_worklines: Sequence[Any],
        route_probe_cache: _RouteProbeCache | None,
    ) -> tuple[Any, Any]:
        route_probe_started_at = timezone.now_for_db()
        if route_probe_cache is None or not isinstance(self.route_service, SmtInboundHandoffRouteService):
            return (
                await self.route_service.resolve_route(
                    db,
                    demand=demand,
                    source_item=source_item,
                    candidate_worklines=candidate_worklines,
                ),
                route_probe_started_at,
            )

        probe_times: dict[tuple[Any, ...], Any] = {}

        async def cached_ecs_probe(probe_db: AsyncSession, *, workline: Any, route: Any) -> Any:
            cache_key = self._ecs_probe_cache_key(workline=workline, route=route)
            now = timezone.now_for_db()
            cached = route_probe_cache.get(cache_key)
            if cached is not None:
                cached_result, probed_at = cached
                if now - probed_at <= timedelta(seconds=_CLAIM_ROUTE_PROBE_EVIDENCE_TTL_SECONDS):
                    probe_times[cache_key] = probed_at
                    return cached_result
            result = await self.route_service.ecs_status_probe(probe_db, workline=workline, route=route)
            probed_at = timezone.now_for_db()
            route_probe_cache[cache_key] = (result, probed_at)
            probe_times[cache_key] = probed_at
            return result

        route_service = SmtInboundHandoffRouteService(
            station_lease_service=self.route_service.station_lease_service,
            session_repository=self.route_service.session_repository,
            ecs_status_probe=cached_ecs_probe,
            reason_catalog=self.route_service.reason_catalog,
            retry_delay_seconds=self.route_service.retry_delay_seconds,
        )
        route = await route_service.resolve_route(
            db,
            demand=demand,
            source_item=source_item,
            candidate_worklines=candidate_worklines,
        )
        route_probe_started_at = self._route_probe_started_at_from_cache(route=route, probe_times=probe_times)
        return route, route_probe_started_at

    @staticmethod
    def _ecs_probe_cache_key(*, workline: Any, route: Any) -> tuple[Any, ...]:
        route_context = dict(route) if isinstance(route, Mapping) else {}
        host = getattr(workline, "host", None)
        port = getattr(workline, "port", None)
        endpoint_identity = (
            getattr(workline, "ecs_endpoint", None)
            or getattr(workline, "ecs_base_url", None)
            or getattr(workline, "endpoint", None)
            or ((host, port) if host is not None and port is not None else None)
        )
        workline_id = getattr(workline, "id", None)
        workline_code = getattr(workline, "line_code", None)
        target_identity = (
            ("endpoint", endpoint_identity)
            if endpoint_identity is not None
            else (
                "workline",
                workline_id,
                workline_code,
                id(workline) if workline_id is None and workline_code is None else None,
            )
        )
        return (
            *target_identity,
            route_context.get("source_rack_position_code") or route_context.get("source_position_code"),
            route_context.get("target_rack_position_code") or route_context.get("target_position_code"),
        )

    @staticmethod
    def _route_probe_started_at_from_cache(*, route: Any, probe_times: Mapping[tuple[Any, ...], Any]) -> Any:
        if getattr(route, "kind", None) != "SELECTED" or not probe_times:
            return timezone.now_for_db()
        return min(probe_times.values())

    async def _claim_routed_candidate(
        self,
        db: AsyncSession,
        *,
        candidate: SmtInboundHandoffSourceItem,
        route: Any,
        now: Any,
        route_probe_started_at: Any,
        trace_id: str | None,
    ) -> Any:
        if getattr(route, "kind", None) == "MANUAL_HOLD" or bool(getattr(route, "manual_hold", False)):
            return await self._manual_hold_claim_candidate(db, candidate=candidate, route=route, now=now)

        if getattr(route, "kind", None) == "RETRY" or bool(getattr(route, "retryable", False)):
            return await self._retry_claim_candidate(db, candidate=candidate, route=route, now=now)

        workline_id = getattr(route, "selected_workline_id", None)
        workline_code = getattr(route, "selected_workline_code", None)
        if not isinstance(workline_id, int):
            return await self._manual_hold_invalid_claim_candidate(db, candidate=candidate, now=now)

        return await self._claim_selected_route_source_item(
            db,
            candidate=candidate,
            route=route,
            workline_id=workline_id,
            workline_code=workline_code,
            now=now,
            route_probe_started_at=route_probe_started_at,
            trace_id=trace_id,
        )

    async def _manual_hold_claim_candidate(
        self,
        db: AsyncSession,
        *,
        candidate: SmtInboundHandoffSourceItem,
        route: Any,
        now: Any,
    ) -> Any:
        locked_demand, locked_item, blocked_result = await self._lock_claimable_demand_and_ready_candidate_or_retry(
            db,
            candidate=candidate,
            now=now,
        )
        if blocked_result is not None or locked_demand is None or locked_item is None:
            return blocked_result or self._claim_result("EMPTY")

        self._apply_item_failure(locked_item, str(route.failure_code), message=getattr(route, "failure_message", None))
        self._apply_failure(locked_demand, str(route.failure_code), message=getattr(route, "failure_message", None))
        db.add(locked_item)
        _ = await self.recalculate_demand_status(db, locked_demand, reason="claim_route_manual_hold")
        return route

    async def _retry_claim_candidate(
        self,
        db: AsyncSession,
        *,
        candidate: SmtInboundHandoffSourceItem,
        route: Any,
        now: Any,
    ) -> Any:
        locked_demand, locked_item, blocked_result = await self._lock_claimable_demand_and_ready_candidate_or_retry(
            db,
            candidate=candidate,
            now=now,
        )
        if blocked_result is not None or locked_demand is None or locked_item is None:
            return blocked_result or self._claim_result("EMPTY")

        await self._release_claim_candidate_for_retry(
            db,
            demand=locked_demand,
            item=locked_item,
            failure_code=cast("str", getattr(route, "failure_code", None)),
            failure_message=getattr(route, "failure_message", None),
            next_attempt_at=getattr(route, "next_attempt_at", None),
            reason="claim_route_retry",
        )
        return route

    async def _manual_hold_invalid_claim_candidate(
        self,
        db: AsyncSession,
        *,
        candidate: SmtInboundHandoffSourceItem,
        now: Any,
    ) -> Any:
        locked_demand, locked_item, blocked_result = await self._lock_claimable_demand_and_ready_candidate_or_retry(
            db,
            candidate=candidate,
            now=now,
        )
        if blocked_result is not None or locked_demand is None or locked_item is None:
            return blocked_result or self._claim_result("EMPTY")

        self._apply_item_failure(locked_item, SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value)
        self._apply_failure(locked_demand, SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value)
        db.add(locked_item)
        _ = await self.recalculate_demand_status(db, locked_demand, reason="claim_route_invalid")
        return self._claim_result("MANUAL_HOLD", failure_code=SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value)

    async def _claim_selected_route_source_item(
        self,
        db: AsyncSession,
        *,
        candidate: SmtInboundHandoffSourceItem,
        route: Any,
        workline_id: int,
        workline_code: Any,
        now: Any,
        route_probe_started_at: Any,
        trace_id: str | None,
    ) -> Any:
        locked_demand, locked_item, blocked_result = await self._lock_claimable_demand_and_ready_candidate_or_retry(
            db,
            candidate=candidate,
            now=now,
        )
        if blocked_result is not None or locked_demand is None or locked_item is None:
            return blocked_result or self._claim_result("EMPTY")
        target_workline = await self.repository.lock_target_workline_by_id(db, workline_id=workline_id)
        if target_workline is None:
            return self._claim_result("EMPTY")
        binding = await self.plugin_binding_service.resolve_new_session_binding(db, workline=target_workline)
        if timezone.now_for_db() - route_probe_started_at > timedelta(seconds=_CLAIM_ROUTE_PROBE_EVIDENCE_TTL_SECONDS):
            return await self._release_claim_candidate_for_retry(
                db,
                demand=locked_demand,
                item=locked_item,
                failure_code=SmtInboundHandoffReasonCode.ECS_DEVICE_NOT_IDLE.value,
                failure_message="ECS realtime probe evidence 已过期，等待下一轮 claim 重新准入",
                next_attempt_at=timezone.now_for_db() + timedelta(seconds=30),
                reason="claim_route_probe_evidence_expired",
            )
        if await self._target_has_open_current_material(db, workline_id=workline_id):
            return await self._release_claim_candidate_for_retry(
                db,
                demand=locked_demand,
                item=locked_item,
                failure_code=SmtInboundHandoffReasonCode.TARGET_SESSION_BUSY.value,
                failure_message=None,
                next_attempt_at=timezone.now_for_db() + timedelta(seconds=30),
                reason="claim_current_material_busy",
            )
        if await self._target_has_in_flight_handoff_source_item(
            db, workline_id=workline_id, source_item_id=locked_item.id
        ):
            return await self._release_claim_candidate_for_retry(
                db,
                demand=locked_demand,
                item=locked_item,
                failure_code=SmtInboundHandoffReasonCode.SOURCE_ITEM_CLAIM_CONFLICT.value,
                failure_message=None,
                next_attempt_at=timezone.now_for_db() + timedelta(seconds=30),
                reason="claim_target_in_flight",
            )

        claim_runtime = await self._create_sorting_claim_session(
            db,
            workline=target_workline,
            binding=binding,
            workline_code=workline_code,
            demand=locked_demand,
            item=locked_item,
            trace_id=trace_id,
            route_evidence=getattr(route, "route_evidence", None),
        )
        inbox = await self._create_source_pick_request_inbox(
            db,
            demand=locked_demand,
            item=locked_item,
            session=claim_runtime.session,
            workline_id=workline_id,
            execution_session_id=claim_runtime.execution_session_id,
            correlation_id=claim_runtime.correlation_id,
            trace_id=trace_id,
            route_evidence=getattr(route, "route_evidence", None),
        )

        locked_item.status = SmtInboundHandoffSourceItemStatus.PICK_REQUESTED
        locked_item.target_workline_id = workline_id
        locked_item.target_workline_code = self._text_or_none(workline_code)
        locked_item.sorting_session_id = cast("int", claim_runtime.session.id)
        locked_item.source_pick_inbox_id = cast("int", inbox.id)
        locked_item.claimed_at = now
        locked_item.failure_code = None
        locked_item.failure_message = None
        locked_item.next_attempt_at = None
        locked_demand.target_workline_id = workline_id
        locked_demand.target_workline_code = self._text_or_none(workline_code)
        locked_demand.failure_code = None
        locked_demand.failure_message = None
        db.add(locked_item)
        db.add(locked_demand)
        _ = await self.recalculate_demand_status(db, locked_demand, reason="claim_source_item")
        return self._claim_result(
            "CLAIMED",
            demand=locked_demand,
            source_item=locked_item,
            inbox=inbox,
            session=claim_runtime.session,
        )

    async def _lock_ready_candidate_or_retry(
        self,
        db: AsyncSession,
        *,
        candidate: SmtInboundHandoffSourceItem,
        now: Any,
    ) -> tuple[SmtInboundHandoffSourceItem | None, Any | None]:
        item = await self.repository.lock_source_item_by_id(db, source_item_id=cast("int", candidate.id))
        if item is None:
            return None, self._claim_result("EMPTY")
        if not self._source_item_is_ready_and_due(item, now=now):
            return item, self._claim_result(
                "RETRY",
                failure_code=SmtInboundHandoffReasonCode.SOURCE_ITEM_CLAIM_CONFLICT.value,
            )
        return item, None

    async def _lock_claimable_demand_and_ready_candidate_or_retry(
        self,
        db: AsyncSession,
        *,
        candidate: SmtInboundHandoffSourceItem,
        now: Any,
    ) -> tuple[SmtInboundHandoffDemand | None, SmtInboundHandoffSourceItem | None, Any | None]:
        demand = await self.repository.lock_demand_by_id(db, demand_id=candidate.handoff_demand_id)
        if demand is None or not self._demand_is_claimable(demand):
            return demand, None, self._claim_result("EMPTY")

        item, blocked_result = await self._lock_ready_candidate_or_retry(db, candidate=candidate, now=now)
        return demand, item, blocked_result

    async def _release_claim_candidate_for_retry(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        item: SmtInboundHandoffSourceItem,
        failure_code: str,
        failure_message: str | None,
        next_attempt_at: Any,
        reason: str,
    ) -> Any:
        retry_at = next_attempt_at or (timezone.now_for_db() + timedelta(seconds=30))
        reason_definition = self.reason_catalog.get(failure_code)
        item.status = SmtInboundHandoffSourceItemStatus.READY
        item.next_attempt_at = retry_at
        item.failure_code = reason_definition.failure_code
        item.failure_message = self._text_or_none(failure_message) or reason_definition.default_message
        demand.next_attempt_at = retry_at
        db.add(item)
        db.add(demand)
        _ = await self.recalculate_demand_status(db, demand, reason=reason)
        return self._claim_result(
            "RETRY",
            failure_code=reason_definition.failure_code,
            failure_message=item.failure_message,
            next_attempt_at=retry_at,
        )

    @staticmethod
    def _source_item_is_ready_and_due(
        item: SmtInboundHandoffSourceItem,
        *,
        now: Any,
    ) -> bool:
        return item.status == SmtInboundHandoffSourceItemStatus.READY and (
            item.next_attempt_at is None or item.next_attempt_at <= now
        )

    @staticmethod
    def _demand_is_claimable(demand: SmtInboundHandoffDemand) -> bool:
        return demand.status in _CLAIMABLE_DEMAND_STATUSES

    async def _target_has_open_current_material(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
    ) -> bool:
        sessions = await self.repository.list_open_sorting_sessions_with_current_material(
            db,
            workline_id=workline_id,
            limit=50,
        )
        return bool(sessions)

    async def _target_has_in_flight_handoff_source_item(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        source_item_id: int | None,
    ) -> bool:
        items = await self.repository.list_in_flight_source_items_by_target_workline(
            db,
            target_workline_id=workline_id,
            limit=50,
        )
        return any(
            item.id != source_item_id and item.status in _HANDOFF_CLAIM_IN_FLIGHT_ITEM_STATUSES for item in items
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
            _ = await self.recalculate_demand_status(db, demand, reason="release_source_pick_dead_letter_for_retry")
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
        session_id: int,
        workline_id: int,
        execution_session_id: int,
        correlation_id: str,
        plugin_key: str,
        contract_version: str,
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
        if item.sorting_session_id != session_id or item.target_workline_id != workline_id:
            raise ValueError("source pick command correlation session/workline 不匹配")
        if not isinstance(execution_session_id, int) or execution_session_id <= 0:
            raise TypeError("source pick command correlation execution_session_id 无效")

        inbox = await self.repository.get_runtime_inbox_by_id(db, source_pick_inbox_id)
        command = await self.repository.get_device_command_by_id(db, command_id)
        if inbox is None or command is None:
            raise ValueError("source pick command correlation 权威 evidence 缺失")
        if (
            getattr(command, "command_code", None) != command_code
            or getattr(command, "workline_id", None) != workline_id
            or getattr(command, "correlation_id", None) != correlation_id
            or getattr(command, "plugin_key", None) != plugin_key
            or getattr(command, "contract_version", None) != contract_version
        ):
            raise ValueError("source pick command correlation command ownership 不匹配")
        existing = (
            (item.source_pick_command_id, command_id),
            (item.source_pick_command_code, command_code),
            (item.source_pick_dispatch_key, dispatch_key),
        )
        if any(current is not None and current != expected for current, expected in existing):
            raise ValueError("source pick command correlation 禁止覆盖既有关联")

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
            _ = await self.recalculate_demand_status(db, demand, reason="source_pick_command_correlation")
        else:
            await db.flush()
        return item

    async def record_source_pick_success(
        self,
        db: AsyncSession,
        *,
        handoff_demand_id: int | None = None,
        source_item_id: int | None = None,
        claim_attempt_no: int | None = None,
        source_pick_inbox_id: int | None = None,
        command_id: int | None = None,
        command_code: str | None = None,
        session: Any | None = None,
        trace_id: str | None = None,
    ) -> SmtInboundHandoffLedgerResult:
        """记录 source-pick 成功账本，只允许 claimed 状态幂等推进到 PICKED。"""

        _ = trace_id
        context_request = self._source_pick_request_from_session(session)
        command_request = await self._source_pick_request_from_command(db, command_id)
        authoritative_command_code = self._text_or_none(command_request.get("command_code"))
        if command_code is not None and authoritative_command_code != command_code:
            raise ValueError("source pick success command_code 不匹配")
        resolved_source_item_id = (
            source_item_id
            or self._int_or_none(context_request.get("handoff_source_item_id"))
            or self._int_or_none(command_request.get("handoff_source_item_id"))
        )
        self._validate_source_pick_success_source_item_evidence(
            resolved_source_item_id,
            source_item_id=source_item_id,
            context_source_item_id=self._int_or_none(context_request.get("handoff_source_item_id")),
            command_source_item_id=self._int_or_none(command_request.get("handoff_source_item_id")),
        )
        item = await self._resolve_source_pick_success_item_for_update(
            db,
            source_item_id=resolved_source_item_id,
        )
        if item is None and resolved_source_item_id is None:
            raise ValueError("source pick success 缺少 source_item_id")
        if item is None:
            raise ValueError(f"未找到 handoff source item: {resolved_source_item_id}")

        resolved_demand_id = (
            handoff_demand_id
            or self._int_or_none(context_request.get("handoff_demand_id"))
            or self._int_or_none(command_request.get("handoff_demand_id"))
        )
        resolved_attempt_no = (
            claim_attempt_no
            or self._int_or_none(context_request.get("claim_attempt_no"))
            or self._int_or_none(command_request.get("claim_attempt_no"))
        )
        resolved_source_pick_inbox_id = source_pick_inbox_id or self._int_or_none(
            command_request.get("source_pick_inbox_id")
        )
        self._validate_source_pick_success_evidence(
            item,
            handoff_demand_id=resolved_demand_id,
            claim_attempt_no=resolved_attempt_no,
            source_pick_inbox_id=resolved_source_pick_inbox_id,
            command_id=command_id,
        )

        if item.status in _CLAIMED_ITEM_STATUSES:
            item.status = SmtInboundHandoffSourceItemStatus.PICKED
            item.failure_code = None
            item.failure_message = None
            item.next_attempt_at = None
            db.add(item)
            demand = await db.get(SmtInboundHandoffDemand, item.handoff_demand_id)
            if demand is not None:
                demand.failure_code = None
                demand.failure_message = None
                db.add(demand)
                _ = await self.recalculate_demand_status(db, demand, reason="source_pick_success")
            else:
                await db.flush()
            return SmtInboundHandoffLedgerResult(
                outcome="advanced",
                advanced=True,
                already_terminal=False,
                source_item=item,
            )

        if item.status == SmtInboundHandoffSourceItemStatus.PICKED:
            db.add(item)
            await db.flush()
            return SmtInboundHandoffLedgerResult(
                outcome="already_picked",
                advanced=False,
                already_terminal=False,
                source_item=item,
            )

        if item.status in _TERMINAL_ITEM_STATUSES:
            db.add(item)
            await db.flush()
            return SmtInboundHandoffLedgerResult(
                outcome="already_terminal",
                advanced=False,
                already_terminal=True,
                source_item=item,
            )

        if item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD:
            db.add(item)
            await db.flush()
            return SmtInboundHandoffLedgerResult(
                outcome="manual_hold",
                advanced=False,
                already_terminal=False,
                source_item=item,
            )

        raise ValueError(f"source pick success 状态不允许: {item.status}")

    async def record_source_item_terminal_result(
        self,
        db: AsyncSession,
        *,
        session: Any,
        terminal_status: str | SmtInboundHandoffSourceItemStatus,
        command_id: int | None = None,
        trace_id: str | None = None,
        terminal_evidence: Mapping[str, Any] | None = None,
    ) -> SmtInboundHandoffLedgerResult:
        """记录 target/NG 终态账本，幂等关闭当前 source item 和 sorting session。"""

        status = self._terminal_source_item_status(terminal_status)
        source_pick_request = self._source_pick_request_from_session(session)
        if not source_pick_request:
            raise ValueError("sorting.source_pick_request 缺失，拒绝写入 terminal handoff ledger")

        source_item_id = self._int_or_none(source_pick_request.get("handoff_source_item_id"))
        handoff_demand_id = self._int_or_none(source_pick_request.get("handoff_demand_id"))
        if source_item_id is None:
            raise ValueError("sorting.source_pick_request.handoff_source_item_id 缺失")

        item = await self.repository.get_source_item_for_update(db, source_item_id)
        if item is None:
            raise ValueError(f"未找到 handoff source item: {source_item_id}")
        if handoff_demand_id is not None and item.handoff_demand_id != handoff_demand_id:
            raise ValueError("terminal handoff ledger demand/item 不匹配")
        self._validate_terminal_source_item_session_binding(item, session)

        demand = await db.get(SmtInboundHandoffDemand, item.handoff_demand_id)
        if demand is None:
            raise ValueError(f"未找到 handoff demand: {item.handoff_demand_id}")

        if item.status == status:
            self._write_terminal_session_evidence(
                session,
                item=item,
                terminal_status=status,
                command_id=command_id,
                trace_id=trace_id,
                terminal_evidence=terminal_evidence,
                already_terminal=True,
            )
            db.add(session)
            db.add(item)
            await db.flush()
            return SmtInboundHandoffLedgerResult(
                outcome="already_terminal",
                advanced=False,
                already_terminal=True,
                current_demand_id=item.handoff_demand_id,
                source_item=item,
                demand=demand,
                session=session,
            )

        if item.status in _TERMINAL_ITEM_STATUSES:
            return await self._manual_hold_terminal_conflict(
                db,
                demand=demand,
                item=item,
                session=session,
                requested_status=status,
                command_id=command_id,
                trace_id=trace_id,
                terminal_evidence=terminal_evidence,
            )

        if item.status not in _SORTING_IN_PROGRESS_ITEM_STATUSES:
            raise ValueError(f"terminal handoff ledger 状态不允许: {item.status}")

        item.status = status
        item.completed_at = timezone.now_for_db()
        item.failure_code = None
        item.failure_message = None
        item.next_attempt_at = None
        demand.failure_code = None
        demand.failure_message = None
        self._write_terminal_session_evidence(
            session,
            item=item,
            terminal_status=status,
            command_id=command_id,
            trace_id=trace_id,
            terminal_evidence=terminal_evidence,
            already_terminal=False,
        )

        from src.app.runtime.orchestration.models.session import SessionStatus
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
        from src.app.workline.domain.services.session_lifecycle_service import workline_session_lifecycle_service

        now = timezone.now_for_db()
        if self._enum_text(getattr(session, "status", None)) != SessionStatus.COMPLETED.value:
            workline_session_lifecycle_service.complete(session, occurred_at=now)
            session.failure_domain = None
            session.failure_code = None
            session.failure_message = None
            await WorklineSessionRepository().persist_completed(
                db,
                session_id=cast("int", getattr(session, "id", None)),
                occurred_at=now,
                context_json=getattr(session, "context_json", None),
            )
        db.add(item)
        db.add(demand)
        db.add(session)
        _ = await self.recalculate_demand_status(db, demand, reason="source_item_terminal_result")
        return SmtInboundHandoffLedgerResult(
            outcome="advanced",
            advanced=True,
            already_terminal=False,
            current_demand_id=item.handoff_demand_id,
            source_item=item,
            demand=demand,
            session=session,
        )

    async def _manual_hold_terminal_conflict(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        item: SmtInboundHandoffSourceItem,
        session: Any,
        requested_status: SmtInboundHandoffSourceItemStatus,
        command_id: int | None,
        trace_id: str | None,
        terminal_evidence: Mapping[str, Any] | None,
    ) -> SmtInboundHandoffLedgerResult:
        self._apply_item_failure(
            item,
            SmtInboundHandoffReasonCode.PLUGIN_CONTRACT_INVALID.value,
            message=(
                f"terminal handoff ledger 冲突: current={self._enum_text(item.status)}, "
                f"requested={requested_status.value}"
            ),
        )
        self._apply_failure(
            demand,
            SmtInboundHandoffReasonCode.PLUGIN_CONTRACT_INVALID.value,
            message="terminal handoff ledger 收到冲突终态，需人工确认 source item 实际去向",
        )
        self._write_terminal_session_evidence(
            session,
            item=item,
            terminal_status=requested_status,
            command_id=command_id,
            trace_id=trace_id,
            terminal_evidence=terminal_evidence,
            already_terminal=False,
            conflict=True,
        )

        from src.app.runtime.orchestration.models.session import SessionStatus
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
        from src.app.workline.domain.services.session_lifecycle_service import (
            InvalidSessionTransition,
            workline_session_lifecycle_service,
        )

        session_status = self._enum_text(getattr(session, "status", None))
        terminal_session_statuses = {
            SessionStatus.COMPLETED.value,
            SessionStatus.FAILED.value,
            SessionStatus.CANCELLED.value,
        }
        if session_status not in terminal_session_statuses:
            with suppress(InvalidSessionTransition):
                workline_session_lifecycle_service.manual_hold(session, occurred_at=timezone.now_for_db())
            session.failure_domain = "SMT_INBOUND_HANDOFF"
            session.failure_code = SmtInboundHandoffReasonCode.PLUGIN_CONTRACT_INVALID.value
            session.failure_message = "terminal handoff ledger 收到冲突终态，需人工确认"
            await WorklineSessionRepository().persist_manual_hold(
                db,
                session_id=cast("int", getattr(session, "id", None)),
                occurred_at=timezone.now_for_db(),
                failure_domain=session.failure_domain,
                failure_code=session.failure_code,
                failure_message=session.failure_message,
            )
        db.add(item)
        db.add(demand)
        db.add(session)
        _ = await self.recalculate_demand_status(db, demand, reason="source_item_terminal_conflict")
        return SmtInboundHandoffLedgerResult(
            outcome="manual_hold",
            advanced=False,
            already_terminal=False,
            current_demand_id=item.handoff_demand_id,
            source_item=item,
            demand=demand,
            session=session,
        )

    async def _resolve_source_pick_success_item_for_update(
        self,
        db: AsyncSession,
        *,
        source_item_id: int | None,
    ) -> SmtInboundHandoffSourceItem | None:
        if source_item_id is not None:
            return await self.repository.get_source_item_for_update(db, source_item_id)
        return None

    async def scan_smt_inbound_handoff_demands_batch(
        self,
        db: AsyncSession,
        *,
        scan_limit: int = 100,
        recovery_limit: int = 100,
        claim_limit: int = 10,
        stale_after_seconds: int = 300,
        trace_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, int]:
        """扫描到期 demand、claim 后卡住项和 READY claim 兜底。"""

        if limit is not None:
            scan_limit = limit
            recovery_limit = limit
            claim_limit = 0
        summary = self._empty_recovery_summary()
        summary["claimed"] = 0

        now = timezone.now_for_db()
        if scan_limit > 0:
            due_demands = await self.repository.list_due_recovery_demands(db, now=now, limit=scan_limit)
            for demand in due_demands:
                summary["scanned"] += 1
                before_status = demand.status
                _ = await self.recalculate_demand_status(db, demand, reason="recovery_due_demand_scan")
                if demand.status != before_status:
                    summary["advanced"] += 1

        if recovery_limit > 0:
            stuck_items = await self.repository.list_stuck_source_items_for_recovery(
                db,
                now=now,
                limit=recovery_limit,
                stale_after_seconds=stale_after_seconds,
            )
            for item in stuck_items:
                summary["scanned"] += 1
                try:
                    # 单项恢复使用 savepoint 隔离 DB 错误，避免一次坏数据污染后续候选。
                    async with db.begin_nested():
                        outcome = await self._recover_stuck_source_item(db, item, now=now)
                except Exception:
                    summary["recovery_errors"] += 1
                    continue
                if outcome in summary:
                    summary[outcome] += 1

        route_probe_cache: _RouteProbeCache = {}
        for _ in range(max(claim_limit, 0)):
            claim_result = await self.claim_next_source_item(
                db,
                trace_id=trace_id,
                route_probe_cache=route_probe_cache,
            )
            if getattr(claim_result, "kind", None) == "CLAIMED":
                summary["claimed"] += 1
            elif getattr(claim_result, "kind", None) == "EMPTY":
                break
        return summary

    async def list_handoff_demand_summaries(
        self,
        db: AsyncSession,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        """查询 handoff demand 摘要，供人工处置页列表使用。"""

        normalized_status = self._text_or_none(status)
        demands = await self.repository.list_demands_for_api(
            db,
            limit=limit,
            offset=offset,
            status=normalized_status,
        )
        total = await self.repository.count_demands_for_api(db, status=normalized_status)
        items: list[dict[str, Any]] = []
        for demand in demands:
            demand_id = getattr(demand, "id", None)
            source_items = await self.repository.list_source_items(db, demand_id) if isinstance(demand_id, int) else []
            items.append(self._demand_summary(demand, source_items))
        return {"total": total, "items": items, "limit": limit, "offset": offset}

    async def get_handoff_demand_detail(
        self,
        db: AsyncSession,
        demand_id: int,
    ) -> dict[str, Any] | None:
        """查询 handoff demand 详情和 source-pick evidence。"""

        demand = await self.repository.get_by_id(db, demand_id)
        if demand is None:
            return None
        source_items = await self.repository.list_source_items(db, demand_id)
        detail = self._demand_summary(demand, source_items)
        detail["release_snapshot"] = self._dict_or_empty(demand.bin_snapshots_json)
        detail["source_items"] = [await self._source_item_detail(db, item) for item in source_items]
        return detail

    async def retry_source_pick_action(
        self,
        db: AsyncSession,
        *,
        source_item_id: int,
    ) -> dict[str, Any]:
        """人工释放 source-pick 失败 item，允许后续 claim 重新创建内部事件和命令。"""

        item = await self.repository.get_source_item_by_id(db, source_item_id)
        if item is None:
            raise ValueError(f"handoff source item 不存在: {source_item_id}")
        if item.status != SmtInboundHandoffSourceItemStatus.MANUAL_HOLD:
            raise ValueError("当前状态不可重试 source pick")
        if "RETRY_SOURCE_PICK" not in self._available_actions(item):
            raise ValueError("当前失败原因不可重试 source pick")

        released = await self.release_source_pick_dead_letter_for_retry(db, source_item_id=source_item_id)
        return {
            "id": released.id,
            "status": enum_value(released.status),
            "available_actions": self._available_actions(released),
        }

    async def _recover_stuck_source_item(
        self,
        db: AsyncSession,
        item: SmtInboundHandoffSourceItem,
        *,
        now: Any,
    ) -> str | None:
        from src.app.device.models.command import CommandResult, CommandStatus, DeviceCommand
        from src.app.runtime.orchestration.repositories.runtime_inbox_repository import (
            runtime_inbox_repository,
        )

        demand = await db.get(SmtInboundHandoffDemand, item.handoff_demand_id)
        if demand is None:
            raise ValueError(f"未找到 handoff demand: {item.handoff_demand_id}")
        # source_pick_inbox_id 指向 RuntimeInbox；状态映射如下：
        # - "in progress" = {RECEIVED, PROCESSING};
        # - 可重试 = FAILED + next_retry_at 已设置；
        #   终态失败 = DEAD_LETTER; 成功 = PROCESSED.
        # - next_retry_at 单位: RuntimeInbox 使用 Unix 毫秒, item.next_attempt_at 是
        #   naive datetime, 通过 timezone.to_utc() 转换。
        source_pick_inbox_id = item.source_pick_inbox_id
        inbox = (
            await runtime_inbox_repository.get_by_id_for_update(db, source_pick_inbox_id)
            if isinstance(source_pick_inbox_id, int)
            else None
        )
        inbox_status = self._enum_text(getattr(inbox, "status", None))
        outcome: str | None = None
        if inbox is None:
            await self._manual_hold_source_pick_recovery(
                db,
                demand=demand,
                item=item,
                failure_code=SmtInboundHandoffReasonCode.INTERNAL_INBOX_ENVELOPE_INVALID.value,
                message="source pick inbox 不存在，无法确认内部事件处理结果",
            )
            outcome = "manual_hold"
        elif inbox_status in {"RECEIVED", "PROCESSING"}:
            outcome = None
        elif inbox_status == "FAILED":
            raw_next_retry_at = getattr(inbox, "next_retry_at", None)
            retry_at: Any = None
            if isinstance(raw_next_retry_at, (int, float)):
                retry_at = timezone.to_utc(raw_next_retry_at / 1000)
            else:
                retry_at = None
            self._release_item_for_source_pick_retry(item, next_attempt_at=retry_at or now)
            db.add(item)
            demand.failure_code = None
            demand.failure_message = None
            db.add(demand)
            _ = await self.recalculate_demand_status(db, demand, reason="source_pick_inbox_retryable_failed")
            outcome = "retry_scheduled"
        elif inbox_status == "DEAD_LETTER":
            await self._manual_hold_source_pick_recovery(
                db,
                demand=demand,
                item=item,
                failure_code=SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER.value,
                message=getattr(inbox, "last_error_message", None),
            )
            outcome = "manual_hold"
        elif inbox_status == "PROCESSED" and item.source_pick_command_id is None:
            session = (
                await db.get(WorklineSession, item.sorting_session_id)
                if isinstance(item.sorting_session_id, int)
                else None
            )
            if session is None:
                await self._manual_hold_source_pick_recovery(
                    db,
                    demand=demand,
                    item=item,
                    failure_code=SmtInboundHandoffReasonCode.SOURCE_PICK_COMMAND_NOT_CREATED.value,
                    message="source pick inbox 已处理但 sorting session 不存在",
                )
                outcome = "manual_hold"
            else:
                outcome = await self._recover_processed_source_pick_command(
                    db,
                    demand=demand,
                    item=item,
                    inbox=inbox,
                    session=session,
                    now=now,
                )
        elif item.source_pick_command_id is not None:
            command = await db.get(DeviceCommand, item.source_pick_command_id)
            command_status = self._enum_text(getattr(command, "status", None))
            command_result = self._enum_text(getattr(command, "result", None))
            if command_status == CommandStatus.COMPLETED.value and command_result == CommandResult.SUCCESS.value:
                result = await self.record_source_pick_success(
                    db,
                    handoff_demand_id=item.handoff_demand_id,
                    source_item_id=cast("int", item.id),
                    claim_attempt_no=item.claim_attempt_no,
                    source_pick_inbox_id=item.source_pick_inbox_id,
                    command_id=item.source_pick_command_id,
                )
                outcome = result.outcome if result.outcome == "manual_hold" else "advanced" if result.advanced else None
            elif command_status in {
                CommandStatus.FAILED.value,
                CommandStatus.TIMEOUT.value,
                CommandStatus.CANCELLED.value,
            }:
                await self._manual_hold_source_pick_recovery(
                    db,
                    demand=demand,
                    item=item,
                    failure_code=SmtInboundHandoffReasonCode.SOURCE_PICK_COMMAND_NOT_CREATED.value,
                    message="source pick command 已进入失败终态但 source item 未推进",
                )
                outcome = "manual_hold"
        return outcome

    async def _recover_processed_source_pick_command(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        item: SmtInboundHandoffSourceItem,
        inbox: Any,
        session: Any,
        now: Any,
    ) -> str | None:
        """用 correlation 索引与五项业务证据恢复 source-pick command 关联。"""

        _ = now
        if item.status == SmtInboundHandoffSourceItemStatus.PICKED or item.status in _TERMINAL_ITEM_STATUSES:
            return None

        correlation_id = self._text_or_none(getattr(inbox, "correlation_id", None))
        awaiting_command_code = self._text_or_none(getattr(session, "awaiting_device_command_code", None))
        workline_id = self._int_or_none(getattr(session, "workline_id", None))
        source_pick_request_event_id = self._text_or_none(getattr(inbox, "event_id", None))
        candidates = (
            await self.command_repository.list_by_runtime_correlation(
                db,
                correlation_id=correlation_id,
                awaiting_command_code=awaiting_command_code,
                workline_id=workline_id,
                task_type="SORTING_SOURCE_PICK",
                handoff_demand_id=item.handoff_demand_id,
                handoff_source_item_id=cast("int", item.id),
                claim_attempt_no=item.claim_attempt_no,
                source_pick_inbox_id=cast("int", item.source_pick_inbox_id),
                source_pick_request_event_id=source_pick_request_event_id,
                limit=2,
            )
            if (
                correlation_id is not None
                and awaiting_command_code is not None
                and workline_id is not None
                and source_pick_request_event_id is not None
            )
            else []
        )
        if len(candidates) != 1:
            await self._manual_hold_source_pick_recovery(
                db,
                demand=demand,
                item=item,
                failure_code=SmtInboundHandoffReasonCode.SOURCE_PICK_COMMAND_NOT_CREATED.value,
                message=f"source pick command correlation 候选数量为 {len(candidates)}，拒绝猜测",
            )
            return "manual_hold"

        command = candidates[0]
        if not self._source_pick_recovery_evidence_matches(
            item=item,
            inbox=inbox,
            session=session,
            command=command,
            correlation_id=correlation_id,
        ):
            await self._manual_hold_source_pick_recovery(
                db,
                demand=demand,
                item=item,
                failure_code=SmtInboundHandoffReasonCode.SOURCE_PICK_COMMAND_NOT_CREATED.value,
                message="source pick command correlation evidence 不一致，拒绝恢复",
            )
            return "manual_hold"

        command_id = cast("int", getattr(command, "id", None))
        command_code = cast("str", getattr(command, "command_code", None))
        await self.record_source_pick_command_correlation(
            db,
            handoff_demand_id=item.handoff_demand_id,
            source_item_id=cast("int", item.id),
            claim_attempt_no=item.claim_attempt_no,
            source_pick_inbox_id=cast("int", item.source_pick_inbox_id),
            command_id=command_id,
            command_code=command_code,
            dispatch_key=f"device-command:{command_code}",
            session_id=cast("int", getattr(session, "id", None)),
            workline_id=cast("int", workline_id),
            execution_session_id=cast("int", getattr(inbox, "execution_session_id", None)),
            correlation_id=cast("str", correlation_id),
            plugin_key=cast("str", getattr(command, "plugin_key", None)),
            contract_version=cast("str", getattr(command, "contract_version", None)),
            trace_id=getattr(inbox, "trace_id", None),
        )

        command_status = self._enum_text(getattr(command, "status", None))
        command_result = self._enum_text(getattr(command, "result", None))
        if command_status == "COMPLETED" and command_result == "SUCCESS":
            result = await self.record_source_pick_success(
                db,
                handoff_demand_id=item.handoff_demand_id,
                source_item_id=cast("int", item.id),
                claim_attempt_no=item.claim_attempt_no,
                source_pick_inbox_id=item.source_pick_inbox_id,
                command_id=command_id,
            )
            return "manual_hold" if result.outcome == "manual_hold" else "advanced"
        if command_status in {"FAILED", "TIMEOUT", "CANCELLED"}:
            await self._manual_hold_source_pick_recovery(
                db,
                demand=demand,
                item=item,
                failure_code=SmtInboundHandoffReasonCode.SOURCE_PICK_COMMAND_NOT_CREATED.value,
                message="source pick command 已进入失败终态但 source item 未推进",
            )
            return "manual_hold"
        return "advanced"

    def _source_pick_recovery_evidence_matches(
        self,
        *,
        item: SmtInboundHandoffSourceItem,
        inbox: Any,
        session: Any,
        command: Any,
        correlation_id: str,
    ) -> bool:
        """校验 demand/item/attempt/inbox/event 五项证据及 session/command 归属。"""

        params = self._dict_or_empty(getattr(command, "params", None))
        request = self._source_pick_request_from_session(session)
        expected = {
            "handoff_demand_id": item.handoff_demand_id,
            "handoff_source_item_id": item.id,
            "claim_attempt_no": item.claim_attempt_no,
            "source_pick_inbox_id": item.source_pick_inbox_id,
            "source_pick_request_event_id": getattr(inbox, "event_id", None),
        }
        if any(params.get(key) != value for key, value in expected.items()):
            return False
        request_expected = {
            "handoff_demand_id": expected["handoff_demand_id"],
            "handoff_source_item_id": expected["handoff_source_item_id"],
            "claim_attempt_no": expected["claim_attempt_no"],
            "event_id": expected["source_pick_request_event_id"],
        }
        if any(request.get(key) != value for key, value in request_expected.items()):
            return False
        command_code = self._text_or_none(getattr(command, "command_code", None))
        return (
            isinstance(item.sorting_session_id, int)
            and isinstance(getattr(inbox, "execution_session_id", None), int)
            and getattr(session, "id", None) == item.sorting_session_id
            and getattr(inbox, "workline_session_id", None) == item.sorting_session_id
            and getattr(command, "correlation_id", None) == correlation_id
            and getattr(command, "workline_id", None) == getattr(inbox, "workline_id", None)
            and getattr(session, "workline_id", None) == getattr(inbox, "workline_id", None)
            and getattr(command, "plugin_key", None) == getattr(session, "plugin_key", None)
            and getattr(command, "contract_version", None) == getattr(session, "contract_version", None)
            and self._enum_text(getattr(command, "task_type", None)) == "SORTING_SOURCE_PICK"
            and command_code is not None
            and getattr(session, "awaiting_device_command_code", None) == command_code
        )

    def _release_item_for_source_pick_retry(
        self,
        item: SmtInboundHandoffSourceItem,
        *,
        next_attempt_at: Any,
    ) -> None:
        item.claim_attempt_no += 1
        item.status = SmtInboundHandoffSourceItemStatus.READY
        item.source_pick_inbox_id = None
        item.source_pick_command_id = None
        item.source_pick_command_code = None
        item.source_pick_dispatch_key = None
        item.sorting_session_id = None
        item.failure_code = None
        item.failure_message = None
        item.next_attempt_at = next_attempt_at

    async def _manual_hold_source_pick_recovery(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        item: SmtInboundHandoffSourceItem,
        failure_code: str,
        message: str | None,
    ) -> None:
        self._apply_item_failure(item, failure_code, message=message)
        self._apply_failure(demand, failure_code, message=message)
        db.add(item)
        db.add(demand)
        _ = await self.recalculate_demand_status(db, demand, reason="source_pick_recovery_manual_hold")

    @staticmethod
    def _empty_recovery_summary() -> dict[str, int]:
        return {
            "scanned": 0,
            "advanced": 0,
            "retry_scheduled": 0,
            "manual_hold": 0,
            "recovery_errors": 0,
        }

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
        workline: Any,
        binding: Any,
        workline_code: str | None,
        demand: SmtInboundHandoffDemand,
        item: SmtInboundHandoffSourceItem,
        trace_id: str | None,
        route_evidence: Any,
    ) -> _SmtSortingClaimRuntime:
        workline_id = cast("int", getattr(workline, "id", None))
        event_id = self._source_pick_event_id(item)
        session = WorklineSession(
            session_code=f"smt-inbound-handoff:{demand.id}:source-item:{item.id}:claim:{item.claim_attempt_no}",
            workline_id=workline_id,
            plugin_key=binding.plugin_key,
            run_mode=RunMode.AUTO,
            business_key=demand.demand_key,
            status=SessionStatus.RUNNING,
            context_json={},
            context_schema_version="smt-sorting-inbound.v1",
            contract_version=binding.contract_version,
            plugin_binding_id=binding.id,
            plugin_binding_version=binding.binding_version,
            plugin_config_hash=binding.typed_config_hash,
            plugin_index_digest=binding.generated_index_digest,
            started_at=timezone.now_for_db(),
            trace_id=self._text_or_none(trace_id) or demand.trace_id,
        )
        route_context = self._dict_or_empty(route_evidence)
        sorting_context = SortingInboundContext.initialize(session)
        sorting_context.write_source_pick_request(
            handoff_demand_id=cast("int", demand.id),
            handoff_source_item_id=cast("int", item.id),
            claim_attempt_no=item.claim_attempt_no,
            event_id=event_id,
            target_workline_code=cast("str", self._text_or_none(workline_code)),
            manifest_contract_version=cast(
                "str",
                self._text_or_none(route_context.get("manifest_contract_version")) or binding.contract_version,
            ),
            source_rack_position_code=cast(
                "str",
                self._text_or_none(route_context.get("source_rack_position_code"))
                or self._text_or_none(route_context.get("source_position_code")),
            ),
            target_rack_position_code=cast(
                "str",
                self._text_or_none(route_context.get("target_rack_position_code"))
                or _SORTING_TARGET_RACK_POSITION_CODE,
            ),
            route_evidence=route_context,
        )
        sorting_context.set_station_state(scan_platform="EMPTY")
        db.add(session)
        await db.flush()
        execution_session, work_item = await self.plugin_binding_service.pin_new_runtime_session(
            db,
            workline=workline,
            session=session,
            binding=binding,
        )
        execution_session_id = getattr(execution_session, "id", None)
        correlation_id = self._text_or_none(getattr(work_item, "correlation_id", None))
        if not isinstance(execution_session_id, int) or correlation_id is None:
            raise RuntimeError("SMT sorting claim runtime aggregate missing execution anchor")
        await self._link_claim_session_material_unit(db, session=session, item=item)
        return _SmtSortingClaimRuntime(
            session=session,
            execution_session_id=execution_session_id,
            correlation_id=correlation_id,
        )

    async def _link_claim_session_material_unit(
        self,
        db: AsyncSession,
        *,
        session: WorklineSession,
        item: SmtInboundHandoffSourceItem,
    ) -> None:
        """SMT claim 路径所有权检查：黑名单语义（仅当 owner 明确终态才放行）。

        与 create 路径（runtime_intent_effects._reject_reuse_when_owned_by_active_session）
        的白名单语义有意区分：
        - 本函数处理"跨线 handoff 接管"场景，owner 未知（已硬删/未注册 ID）→ 保守拒绝，
          避免 SMT 接管错的料盘造成跨产线状态分裂。
        - create 路径处理"扫码新建/补建"场景，owner 不存在视为孤儿料盘，放行回收。
        """
        pkg_code = self._text_or_none(getattr(item, "pkg_code", None))
        session_id = getattr(session, "id", None)
        if pkg_code is None or not isinstance(session_id, int):
            return
        material_columns = cast("Any", MaterialUnit).__table__.c
        result = await db.execute(
            select(MaterialUnit).where(material_columns.pkg_code == pkg_code).limit(1).with_for_update()
        )
        material_unit = result.scalar_one_or_none()
        if material_unit is None:
            return
        owner_session_id = getattr(material_unit, "current_session_id", None)
        if isinstance(owner_session_id, int) and owner_session_id != session_id:
            session_columns = cast("Any", WorklineSession).__table__.c
            owner_status_result = await db.execute(
                select(session_columns.status).where(session_columns.id == owner_session_id).limit(1)
            )
            owner_status = enum_value(owner_status_result.scalars().first())
            if owner_status not in _TERMINAL_MATERIAL_OWNER_SESSION_STATUSES:
                raise ValueError(
                    f"material unit {getattr(material_unit, 'id', None)} (pkg_code={pkg_code}) is still owned by "
                    f"non-terminal session {owner_session_id} (status={owner_status}), refuse silent takeover"
                )
        session.current_material_unit_id = cast("int", material_unit.id)
        material_unit.current_session_id = session_id
        db.add(session)
        db.add(material_unit)
        await db.flush()

    async def _create_source_pick_request_inbox(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        item: SmtInboundHandoffSourceItem,
        session: WorklineSession,
        workline_id: int,
        execution_session_id: int,
        correlation_id: str,
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
        causation_id = f"handoff-source-item:{item_id}"
        event_type = _SOURCE_PICK_REQUESTED_EVENT
        _ = route_evidence

        # RuntimeInbox 是 INTERNAL_EVENT 唯一事实源，item.source_pick_inbox_id 指向该记录。
        from src.app.runtime.orchestration.services.runtime_inbox import (
            runtime_inbox_service,
        )

        runtime_inbox_result = await runtime_inbox_service.accept_internal_event(
            db,
            event_type=event_type,
            payload_json={
                "logical_route": "SOURCE_PICK_REQUESTED",
                "input": {
                    "route": "SOURCE_PICK_REQUESTED",
                    "handoff_demand_id": demand_id,
                    "handoff_source_item_id": item_id,
                    "claim_attempt_no": item.claim_attempt_no,
                    "source_pick_request_event_id": event_id,
                },
            },
            trace_id=resolved_trace_id,
            event_id=event_id,
            causation_id=causation_id,
            workline_session_id=session_id,
            workline_id=workline_id,
            execution_session_id=execution_session_id,
            correlation_id=correlation_id,
            auto_commit=False,
        )
        return runtime_inbox_result.record

    @staticmethod
    def _source_pick_event_id(item: SmtInboundHandoffSourceItem) -> str:
        return f"smt-inbound-handoff-source-item:{item.id}:claim:{item.claim_attempt_no}"

    @staticmethod
    def _source_pick_request_from_session(session: Any | None) -> dict[str, Any]:
        if session is None:
            return {}
        context_json = getattr(session, "context_json", None)
        if not isinstance(context_json, Mapping):
            return {}
        sorting = context_json.get("sorting")
        if not isinstance(sorting, Mapping) or not isinstance(sorting.get("source_pick_request"), Mapping):
            return {}
        return SortingInboundContext.load_for_automatic(session).get_source_pick_request()

    async def _source_pick_request_from_command(self, db: AsyncSession, command_id: int | None) -> dict[str, Any]:
        if command_id is None:
            return {}
        command = await self.repository.get_device_command_by_id(db, command_id)
        params = self._dict_or_empty(getattr(command, "params", None))
        return {
            "command_code": self._text_or_none(getattr(command, "command_code", None)),
            "handoff_demand_id": self._int_or_none(params.get("handoff_demand_id")),
            "handoff_source_item_id": self._int_or_none(params.get("handoff_source_item_id")),
            "claim_attempt_no": self._int_or_none(params.get("claim_attempt_no")),
            "source_pick_inbox_id": self._int_or_none(params.get("source_pick_inbox_id")),
        }

    @staticmethod
    def _validate_source_pick_success_source_item_evidence(
        resolved_source_item_id: int | None,
        *,
        source_item_id: int | None,
        context_source_item_id: int | None,
        command_source_item_id: int | None,
    ) -> None:
        if resolved_source_item_id is None:
            return
        for evidence_source_item_id in (source_item_id, context_source_item_id, command_source_item_id):
            if evidence_source_item_id is not None and evidence_source_item_id != resolved_source_item_id:
                raise ValueError("source pick success source_item_id 不匹配")

    @staticmethod
    def _validate_source_pick_success_evidence(
        item: SmtInboundHandoffSourceItem,
        *,
        handoff_demand_id: int | None,
        claim_attempt_no: int | None,
        source_pick_inbox_id: int | None,
        command_id: int | None,
    ) -> None:
        if handoff_demand_id is not None and item.handoff_demand_id != handoff_demand_id:
            raise ValueError("source pick success demand/item 不匹配")
        if claim_attempt_no is not None and item.claim_attempt_no != claim_attempt_no:
            raise ValueError("source pick success claim_attempt_no 不匹配")
        if source_pick_inbox_id is not None and item.source_pick_inbox_id != source_pick_inbox_id:
            raise ValueError("source pick success inbox 不匹配")
        if command_id is not None and item.source_pick_command_id != command_id:
            raise ValueError("source pick success command 不匹配")

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
    def _terminal_source_item_status(
        value: str | SmtInboundHandoffSourceItemStatus,
    ) -> SmtInboundHandoffSourceItemStatus:
        raw = value.value if isinstance(value, SmtInboundHandoffSourceItemStatus) else str(value)
        if raw == SmtInboundHandoffSourceItemStatus.SORTED.value:
            return SmtInboundHandoffSourceItemStatus.SORTED
        if raw == SmtInboundHandoffSourceItemStatus.SKIPPED.value:
            return SmtInboundHandoffSourceItemStatus.SKIPPED
        raise ValueError("terminal_status 仅允许 SORTED 或 SKIPPED")

    def _validate_terminal_source_item_session_binding(
        self,
        item: SmtInboundHandoffSourceItem,
        session: Any,
    ) -> None:
        session_id = self._int_or_none(getattr(session, "id", None))
        item_session_id = self._int_or_none(getattr(item, "sorting_session_id", None))
        if session_id is None or item_session_id != session_id:
            raise ValueError(
                "terminal handoff ledger sorting_session 不匹配: "
                f"item.sorting_session_id={item_session_id}, session.id={session_id}"
            )

    def _write_terminal_session_evidence(
        self,
        session: Any,
        *,
        item: SmtInboundHandoffSourceItem,
        terminal_status: SmtInboundHandoffSourceItemStatus,
        command_id: int | None,
        trace_id: str | None,
        terminal_evidence: Mapping[str, Any] | None,
        already_terminal: bool,
        conflict: bool = False,
    ) -> None:
        root_context = self._dict_or_empty(getattr(session, "context_json", None))
        sorting = self._dict_or_empty(root_context.get("sorting"))
        sorting["handoff_terminal_result"] = {
            "handoff_demand_id": item.handoff_demand_id,
            "handoff_source_item_id": item.id,
            "terminal_status": terminal_status.value,
            "command_id": command_id,
            "trace_id": self._text_or_none(trace_id),
            "already_terminal": already_terminal,
            "conflict": conflict,
            "recorded_at": timezone.now_utc().isoformat(),
            "evidence": self._dict_or_empty(terminal_evidence),
        }
        root_context["sorting"] = sorting
        session.context_json = root_context

    @staticmethod
    def _claim_result(kind: str, **values: Any) -> SmtInboundHandoffClaimResult:
        return SmtInboundHandoffClaimResult(kind=kind, **values)

    @staticmethod
    def _dict_or_empty(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    def _demand_summary(
        self,
        demand: SmtInboundHandoffDemand,
        source_items: Sequence[SmtInboundHandoffSourceItem],
    ) -> dict[str, Any]:
        return {
            "id": demand.id,
            "demand_key": demand.demand_key,
            "rack_release_id": demand.rack_release_id,
            "source_workline_id": demand.source_workline_id,
            "source_workline_code": demand.source_workline_code,
            "target_workline_id": demand.target_workline_id,
            "target_workline_code": demand.target_workline_code,
            "single_layer_rack_code": demand.single_layer_rack_code,
            "release_reason_code": demand.release_reason_code,
            "decision_status": demand.decision_status,
            "handling_operation_key": demand.handling_operation_key,
            "sorting_source_demand_key": demand.sorting_source_demand_key,
            "status": enum_value(demand.status),
            "failure_code": demand.failure_code,
            "failure_message": demand.failure_message,
            "trace_id": demand.trace_id,
            "item_status_counts": self._item_status_counts(source_items),
            "handling_trace_summary": {
                "handling_operation_key": demand.handling_operation_key,
                "decision_status": demand.decision_status,
            },
            "claim_recovery_summary": self._claim_recovery_summary(source_items),
            "available_actions": self._available_actions(demand, source_items=source_items),
        }

    async def _source_item_detail(
        self,
        db: AsyncSession,
        item: SmtInboundHandoffSourceItem,
    ) -> dict[str, Any]:
        return {
            "id": item.id,
            "item_key": item.item_key,
            "bin_code": item.bin_code,
            "bin_cell_index": item.bin_cell_index,
            "bin_cell_code": item.bin_cell_code,
            "material_identity_key": item.material_identity_key,
            "pkg_code": item.pkg_code,
            "reel_thickness_mm": str(item.reel_thickness_mm) if item.reel_thickness_mm is not None else None,
            "status": enum_value(item.status),
            "target_workline_id": item.target_workline_id,
            "target_workline_code": item.target_workline_code,
            "sorting_session_id": item.sorting_session_id,
            "claim_attempt_no": item.claim_attempt_no,
            "source_pick_inbox_id": item.source_pick_inbox_id,
            "source_pick_command_id": item.source_pick_command_id,
            "source_pick_command_code": item.source_pick_command_code,
            "source_pick_dispatch_key": item.source_pick_dispatch_key,
            "failure_code": item.failure_code,
            "failure_message": item.failure_message,
            "source_pick_inbox": await self._source_pick_inbox_evidence(db, item.source_pick_inbox_id),
            "source_pick_command": await self._source_pick_command_evidence(db, item.source_pick_command_id),
            "source_pick_outbox": await self._source_pick_outbox_evidence(db, item.source_pick_dispatch_key),
            "available_actions": self._available_actions(item),
        }

    @staticmethod
    def _item_status_counts(source_items: Sequence[SmtInboundHandoffSourceItem]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in source_items:
            status = str(enum_value(item.status))
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _claim_recovery_summary(self, source_items: Sequence[SmtInboundHandoffSourceItem]) -> dict[str, int]:
        summary = {
            "dead_letter": 0,
            "command_missing": 0,
            "inbox_invalid": 0,
            "manual_hold": 0,
            "claim_stuck": 0,
        }
        for item in source_items:
            failure_code = item.failure_code
            status = enum_value(item.status)
            if status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD.value:
                summary["manual_hold"] += 1
            if status in {
                SmtInboundHandoffSourceItemStatus.PICK_REQUESTED.value,
                SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING.value,
            }:
                summary["claim_stuck"] += 1
            if failure_code == SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER.value:
                summary["dead_letter"] += 1
            elif failure_code == SmtInboundHandoffReasonCode.SOURCE_PICK_COMMAND_NOT_CREATED.value:
                summary["command_missing"] += 1
            elif failure_code == SmtInboundHandoffReasonCode.INTERNAL_INBOX_ENVELOPE_INVALID.value:
                summary["inbox_invalid"] += 1
        return {key: value for key, value in summary.items() if value}

    def _available_actions(
        self,
        entity: Any,
        *,
        source_items: Sequence[SmtInboundHandoffSourceItem] = (),
    ) -> list[str]:
        failure_code = self._text_or_none(getattr(entity, "failure_code", None))
        if failure_code in self.reason_catalog.by_code:
            return list(self.reason_catalog.get(failure_code).available_actions)

        actions: list[str] = []
        status = enum_value(getattr(entity, "status", None))
        if status in {
            SmtInboundHandoffDemandStatus.CLAIMED_BY_SORTING.value,
            SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS.value,
            SmtInboundHandoffSourceItemStatus.PICK_REQUESTED.value,
            SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING.value,
        }:
            actions.append("SCAN_RECOVERY")
        for item in source_items:
            actions.extend(self._available_actions(item))
        return list(dict.fromkeys(actions))

    async def _source_pick_inbox_evidence(
        self,
        db: AsyncSession,
        inbox_id: int | None,
    ) -> dict[str, Any] | None:
        if inbox_id is None:
            return None
        inbox = await self.repository.get_runtime_inbox_by_id(db, inbox_id)
        if inbox is None:
            return None
        return {
            "id": inbox.id,
            "status": enum_value(inbox.status),
            "event_id": inbox.event_id,
            "attempt_count": inbox.attempt_count,
            "max_retries": inbox.max_retries,
            "next_retry_at": inbox.next_retry_at,
            "processed_at": inbox.processed_at,
            "failed_at": inbox.failed_at,
            "last_error_code": inbox.last_error_code,
            "last_error_message": inbox.last_error_message,
        }

    async def _source_pick_command_evidence(
        self,
        db: AsyncSession,
        command_id: int | None,
    ) -> dict[str, Any] | None:
        if command_id is None:
            return None
        command = await self.repository.get_device_command_by_id(db, command_id)
        if command is None:
            return None
        return {
            "id": command.id,
            "command_code": command.command_code,
            "status": enum_value(command.status),
            "result": enum_value(command.result),
            "result_data": command.result_data,
            "error_detail": command.error_detail,
            "sent_at": command.sent_at,
            "ack_received_at": command.ack_received_at,
            "completed_at": command.completed_at,
        }

    async def _source_pick_outbox_evidence(
        self,
        db: AsyncSession,
        dispatch_key: str | None,
    ) -> dict[str, Any] | None:
        if dispatch_key is None:
            return None
        outbox = await self.repository.get_outbox_by_dispatch_key(db, dispatch_key)
        if outbox is None:
            return None
        return {
            "id": outbox.id,
            "dispatch_key": outbox.dispatch_key,
            "status": enum_value(outbox.status),
            "attempt_count": outbox.attempt_count,
            "next_retry_at": outbox.next_retry_at,
            "last_error": outbox.last_error,
            "sent_at": outbox.sent_at,
            "finished_at": outbox.finished_at,
        }

    @staticmethod
    def _enum_text(value: Any) -> str | None:
        raw = getattr(value, "value", value)
        return raw if isinstance(raw, str) else None

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
                    "rack_release_id": demand.rack_release_id,
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
        if status in {"EMPTY", "EMPTY_VERIFIED", "AVAILABLE"}:
            return True
        if status:
            return False
        return not any(str(cell.get(field) or "").strip() for field in ("material_identity_key", "pkg_code", "PkgID"))

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
