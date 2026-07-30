"""E12 输送线入口批次的冻结 identity 与事务服务。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.effect_state_contract import generated_effect_source_event_id
from src.app.runtime.orchestration.repositories.wms_conveyor_batch_repository import (
    WmsConveyorAvailabilityFacts,
    WmsConveyorBatchRepository,
    WmsConveyorPreparedBatchRows,
    WmsConveyorSourceRow,
    wms_conveyor_batch_repository,
)
from src.app.runtime.workline_plugins.smt_sorting_inbound.contracts import SmtSortingInboundConfig
from src.app.wms_integration.ports.fulfillment_operations import (
    MOVE_BINS_TO_CONVEYOR_ENTRY,
    BatchItemResult,
    ConveyorBatchItem,
    MoveBinsToConveyorEntryRequest,
    MoveBinsToConveyorEntryResult,
    WmsEffectAck,
    validate_fulfillment_ack,
)
from src.app.wms_integration.ports.operation_common import validate_json_payload
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.wms_integration.operation_contract import WmsOperationDefinition


@dataclass(frozen=True, slots=True)
class WmsConveyorBatchCandidate:
    """submit 前冻结的单个 E12 成员。"""

    route_instance_id: str
    bin_code: str
    source_rack_code: str
    source_slot_code: str
    reserved_queue_position: int


@dataclass(frozen=True, slots=True)
class WmsConveyorBatchIdentity:
    """RuntimeIntent batch root 与外部派发共用的确定性 identity。"""

    batch_id: str
    dispatch_key: str


@dataclass(frozen=True, slots=True)
class WmsConveyorBatchClaim:
    """reserve→Intent claim→preparation hook 同事务传播的冻结集合。"""

    workline_id: int
    binding_id: int
    binding_version: int
    plugin_config_hash: str
    queue_code: str
    entry_capacity: int
    capacity_snapshot_version: str
    source_rack_code: str
    batch_id: str
    candidates: tuple[WmsConveyorBatchCandidate, ...]


@dataclass(frozen=True, slots=True)
class WmsConveyorBatchReservation:
    """无候选时不携带 operation/request，调用方不得进入 Intent claim。"""

    created: bool
    claim: WmsConveyorBatchClaim | None
    operation: WmsOperationDefinition | None
    request: MoveBinsToConveyorEntryRequest | None


def _new_batch_token() -> str:
    return uuid4().hex


def _now_ms() -> int:
    return int(timezone.now_utc().timestamp() * 1000)


class WmsConveyorBatchService:
    """以 RuntimeIntentLog 为唯一 batch root 的 E12 reserve/preparation 服务。"""

    def __init__(
        self,
        *,
        repository: WmsConveyorBatchRepository = wms_conveyor_batch_repository,
        id_factory: Callable[[], str] = _new_batch_token,
        now_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._repository = repository
        self._id_factory = id_factory
        self._now_ms = now_ms

    @staticmethod
    def capacity_snapshot_version(
        *,
        binding_id: int,
        binding_version: int,
        plugin_config_hash: str,
        entry_capacity: int,
    ) -> str:
        """只对 pinned binding identity 与入口容量生成 canonical hash。"""

        return sha256_digest(
            {
                "binding_id": binding_id,
                "binding_version": binding_version,
                "plugin_config_hash": plugin_config_hash,
                "conveyor_entry_capacity": entry_capacity,
            }
        )

    @staticmethod
    def batch_identity(
        *,
        workline_id: int,
        queue_code: str,
        batch_token: str,
    ) -> WmsConveyorBatchIdentity:
        """按单次 reserve winner token 生成可重放、但不跨物理循环复用的 identity。"""

        digest = sha256_digest(
            {
                "workline_id": workline_id,
                "queue_code": queue_code,
                "batch_token": batch_token,
            }
        )
        identity = f"wms-e12:{workline_id}:{digest}"
        return WmsConveyorBatchIdentity(batch_id=identity, dispatch_key=identity)

    @staticmethod
    def route_instance_id(batch_id: str, *, sequence_no: int) -> str:
        """同一 batch 重放保持 route identity，新 batch 必然创建新 route。"""

        if sequence_no <= 0:
            raise ValueError("E12 route sequence_no must be positive")
        route_instance_id = f"{batch_id}:route:{sequence_no}"
        if len(route_instance_id) > 160:
            raise ValueError("E12 route_instance_id exceeds storage limit")
        return route_instance_id

    async def reserve_batch(self, ctx: dict[str, Any]) -> WmsConveyorBatchReservation:
        """锁定 entry 与单个五层架候选，winner 才冻结 E12 request。"""

        db, workline, binding_id, binding_version, plugin_config_hash, config = self._validate_context(ctx)
        workline_id = workline.id
        queue = config.conveyor_entry_queue
        await self._repository.lock_entry_queue(db, workline_id=workline_id, queue_code=queue.code)
        member_positions = await self._repository.lock_active_member_positions(
            db,
            workline_id=workline_id,
            queue_code=queue.code,
        )
        membership_positions = await self._repository.lock_active_entry_membership_positions(
            db,
            workline_id=workline_id,
            queue_code=queue.code,
        )
        occupied_positions = member_positions | membership_positions
        free_positions = tuple(
            position for position in range(1, queue.capacity + 1) if position not in occupied_positions
        )
        if not free_positions:
            return self._empty_reservation()

        target_position = await self._repository.lock_target_position(
            db,
            workline_id=workline_id,
            workline_code=workline.line_code,
        )
        if target_position is None:
            return self._empty_reservation()
        placement_row = await self._repository.lock_first_target_placement(db, workline_id=workline_id)
        if placement_row is None:
            return self._empty_reservation()
        _placement, rack, _rack_type = placement_row
        source_rows = await self._repository.lock_source_rows(db, rack=rack)
        facts = await self._repository.load_availability_facts(db, source_rows=source_rows)
        available_rows = self._available_source_rows(source_rows, facts=facts)
        batch_size = min(len(free_positions), config.ctu_basket_capacity, len(available_rows))
        if batch_size <= 0:
            return self._empty_reservation()

        identity = self.batch_identity(
            workline_id=workline_id,
            queue_code=queue.code,
            batch_token=self._id_factory(),
        )
        candidates = tuple(
            WmsConveyorBatchCandidate(
                route_instance_id=self.route_instance_id(identity.batch_id, sequence_no=sequence_no),
                bin_code=row.bin_code,
                source_rack_code=row.rack_code,
                source_slot_code=row.rack_slot_code,
                reserved_queue_position=queue_position,
            )
            for sequence_no, (row, queue_position) in enumerate(
                zip(available_rows[:batch_size], free_positions[:batch_size], strict=True),
                start=1,
            )
        )
        capacity_snapshot = self.capacity_snapshot_version(
            binding_id=binding_id,
            binding_version=binding_version,
            plugin_config_hash=plugin_config_hash,
            entry_capacity=queue.capacity,
        )
        request = MoveBinsToConveyorEntryRequest(
            dispatch_key=identity.dispatch_key,
            batch_id=identity.batch_id,
            direction="TO_CONVEYOR_ENTRY",
            source_station_code="TARGET_STATION",
            destination_station_code=queue.code,
            capacity_snapshot_version=capacity_snapshot,
            items=tuple(
                ConveyorBatchItem(
                    sequence_no=sequence_no,
                    route_instance_id=candidate.route_instance_id,
                    bin_id=candidate.bin_code,
                    source_rack_id=candidate.source_rack_code,
                    source_slot_id=candidate.source_slot_code,
                    reserved_queue_position=candidate.reserved_queue_position,
                )
                for sequence_no, candidate in enumerate(candidates, start=1)
            ),
        )
        claim = WmsConveyorBatchClaim(
            workline_id=workline_id,
            binding_id=binding_id,
            binding_version=binding_version,
            plugin_config_hash=plugin_config_hash,
            queue_code=queue.code,
            entry_capacity=queue.capacity,
            capacity_snapshot_version=capacity_snapshot,
            source_rack_code=rack.rack_code,
            batch_id=identity.batch_id,
            candidates=candidates,
        )
        ctx["wms_conveyor_batch_claim"] = claim
        return WmsConveyorBatchReservation(
            created=True,
            claim=claim,
            operation=MOVE_BINS_TO_CONVEYOR_ENTRY,
            request=request,
        )

    async def prepare_effect(
        self,
        db: AsyncSession,
        *,
        claim: WmsConveyorBatchClaim,
        request: MoveBinsToConveyorEntryRequest,
        intent_id: int,
    ) -> None:
        """在 Outbox 前校验冻结 request，并原子写 route/member 投影。"""

        self._validate_frozen_request(claim=claim, request=request)
        await self._repository.lock_entry_queue(
            db,
            workline_id=claim.workline_id,
            queue_code=claim.queue_code,
        )
        member_positions = await self._repository.lock_active_member_positions(
            db,
            workline_id=claim.workline_id,
            queue_code=claim.queue_code,
        )
        membership_positions = await self._repository.lock_active_entry_membership_positions(
            db,
            workline_id=claim.workline_id,
            queue_code=claim.queue_code,
        )
        frozen_positions = {candidate.reserved_queue_position for candidate in claim.candidates}
        if frozen_positions & (member_positions | membership_positions):
            raise ValueError("E12 frozen entry positions are no longer available")
        if any(position < 1 or position > claim.entry_capacity for position in frozen_positions):
            raise ValueError("E12 frozen entry position exceeds pinned capacity")

        frozen_rows = await self._repository.lock_frozen_source_rows(
            db,
            rack_code=claim.source_rack_code,
            bin_codes=tuple(candidate.bin_code for candidate in claim.candidates),
        )
        facts = await self._repository.load_availability_facts(db, source_rows=frozen_rows)
        available_by_bin = {row.bin_code: row for row in self._available_source_rows(frozen_rows, facts=facts)}
        for candidate in claim.candidates:
            row = available_by_bin.get(candidate.bin_code)
            if (
                row is None
                or row.rack_code != candidate.source_rack_code
                or row.rack_slot_code != candidate.source_slot_code
            ):
                raise ValueError("E12 frozen source candidate is no longer available")
        await self._repository.add_prepared_batch(
            db,
            intent_id=intent_id,
            workline_id=claim.workline_id,
            queue_code=claim.queue_code,
            candidates=claim.candidates,
            staged_at_ms=self._now_ms(),
        )

    async def project_ack(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsToConveyorEntryRequest,
        ack: WmsEffectAck,
        occurred_at_ms: int,
        source_event_id: str | None,
    ) -> None:
        """整批 ACK 只冻结接纳成员，不推进任何物理 route。"""

        validate_fulfillment_ack(request, ack)
        if not source_event_id:
            raise ValueError("E12 ACK projection requires source_event_id")
        workline_id = await self._repository.resolve_prepared_batch_workline_id(
            db,
            dispatch_key=request.dispatch_key,
        )
        if workline_id is None:
            raise RuntimeError("E12 prepared batch is missing")
        await self._repository.lock_entry_queue(
            db,
            workline_id=workline_id,
            queue_code=request.destination_station_code,
        )
        prepared = await self._repository.lock_prepared_batch(db, dispatch_key=request.dispatch_key)
        if prepared is None:
            raise RuntimeError("E12 prepared batch is missing")
        self._validate_prepared_rows(request=request, prepared=prepared)
        for member in prepared.members:
            if member.member_state == "ACCEPTED":
                continue
            if member.member_state != "CANDIDATE":
                raise ValueError("E12 ACK member is not pending acceptance")
            member.member_state = "ACCEPTED"
            member.accepted_at_ms = occurred_at_ms
        await db.flush()

    async def project_reject(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsToConveyorEntryRequest,
        occurred_at_ms: int,
        source_event_id: str | None,
    ) -> None:
        """物理动作前的整批拒绝释放预约并关闭初始 source route。"""

        await self._release_pristine_batch(
            db,
            request=request,
            occurred_at_ms=occurred_at_ms,
            source_event_id=source_event_id,
            transition_source="E12_REJECTED",
        )

    async def project_transport_not_sent_exhausted(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsToConveyorEntryRequest,
        occurred_at_ms: int,
        source_event_id: str | None,
    ) -> None:
        """明确未发送且重试耗尽时释放仍处于初始态的批次预约。"""

        await self._release_pristine_batch(
            db,
            request=request,
            occurred_at_ms=occurred_at_ms,
            source_event_id=source_event_id,
            transition_source="E12_TRANSPORT_NOT_SENT_EXHAUSTED",
        )

    async def should_reconcile_transport_failure(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsToConveyorEntryRequest,
    ) -> bool:
        """判断 transport 失败是否已与 ACK 或本地物理事实冲突。"""

        prepared, memberships = await self._lock_terminal_projection(db, request=request)
        return (
            bool(memberships)
            or any(member.member_state != "CANDIDATE" for member in prepared.members)
            or any(
                route.current_node != "FIVE_RACK" or route.lifecycle_state != "ACTIVE" or route.route_version != 1
                for route in prepared.routes
            )
        )

    async def _release_pristine_batch(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsToConveyorEntryRequest,
        occurred_at_ms: int,
        source_event_id: str | None,
        transition_source: str,
    ) -> None:
        if not source_event_id:
            raise ValueError("E12 transport failure projection requires source_event_id")
        workline_id = await self._repository.resolve_prepared_batch_workline_id(
            db,
            dispatch_key=request.dispatch_key,
        )
        if workline_id is None:
            raise RuntimeError("E12 prepared batch is missing")
        await self._repository.lock_entry_queue(
            db,
            workline_id=workline_id,
            queue_code=request.destination_station_code,
        )
        prepared = await self._repository.lock_prepared_batch(db, dispatch_key=request.dispatch_key)
        if prepared is None:
            raise RuntimeError("E12 prepared batch is missing")
        self._validate_prepared_rows(request=request, prepared=prepared)
        routes_by_id = {route.route_instance_id: route for route in prepared.routes}
        for member in prepared.members:
            route = routes_by_id[member.route_instance_id]
            if (
                member.member_state != "CANDIDATE"
                or route.lifecycle_state != "ACTIVE"
                or route.current_node != "FIVE_RACK"
                or route.route_version != 1
                or route.current_rack_code is None
                or route.current_slot_code is None
            ):
                raise ValueError("E12 transport failure conflicts with later local physical evidence")
            member.member_state = "RELEASED"
            member.reservation_released_at_ms = occurred_at_ms
            route.lifecycle_state = "CLOSED"
            route.closed_at_ms = occurred_at_ms
            route.route_version += 1
            route.last_transition_source = transition_source
            route.last_transition_source_event_id = source_event_id
        await db.flush()

    async def project_status_reject(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsToConveyorEntryRequest,
        occurred_at_ms: int,
        source_event_id: str | None,
        reason_code: str | None,
    ) -> None:
        """ACK 后、物理动作前的 STATUS_REJECTED 终结成员并关闭 source route。"""

        if not source_event_id or not reason_code:
            raise ValueError("E12 status reject projection requires stable evidence")
        prepared, memberships = await self._lock_terminal_projection(db, request=request)
        if memberships:
            raise ValueError("E12 status reject conflicts with entry membership evidence")
        routes_by_id = {route.route_instance_id: route for route in prepared.routes}
        for member in prepared.members:
            route = routes_by_id[member.route_instance_id]
            if (
                member.member_state != "ACCEPTED"
                or member.accepted_at_ms is None
                or route.lifecycle_state != "ACTIVE"
                or route.current_node != "FIVE_RACK"
                or route.route_version != 1
            ):
                raise ValueError("E12 status reject conflicts with later local physical evidence")
            member.member_state = "TERMINAL"
            member.terminal_at_ms = occurred_at_ms
            member.terminal_outcome = "REJECTED"
            member.reservation_released_at_ms = occurred_at_ms
            route.lifecycle_state = "CLOSED"
            route.closed_at_ms = occurred_at_ms
            route.route_version += 1
            route.last_transition_source = "E12_STATUS_REJECTED"
            route.last_transition_source_event_id = source_event_id
        await db.flush()

    async def should_reconcile_status_reject(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
        queue_code: str,
    ) -> bool:
        """锁定批次后判断 STATUS_REJECTED 是否与后续物理事实冲突。"""

        workline_id = await self._repository.resolve_prepared_batch_workline_id(
            db,
            dispatch_key=dispatch_key,
        )
        if workline_id is None:
            raise RuntimeError("E12 prepared batch is missing")
        await self._repository.lock_entry_queue(db, workline_id=workline_id, queue_code=queue_code)
        prepared = await self._repository.lock_prepared_batch(db, dispatch_key=dispatch_key)
        if prepared is None:
            raise RuntimeError("E12 prepared batch is missing")
        return any(
            route.current_node != "FIVE_RACK" or route.lifecycle_state != "ACTIVE" or route.route_version != 1
            for route in prepared.routes
        )

    async def project_success(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsToConveyorEntryRequest,
        result: MoveBinsToConveyorEntryResult,
        occurred_at_ms: int,
        source_event_id: str | None,
    ) -> None:
        """SUCCESS terminal 将未消费预约原子 handoff 为 ACTIVE ENTRY。"""

        if result.task_outcome != "SUCCESS" or any(item.item_outcome != "SUCCESS" for item in result.items):
            raise ValueError("E12 success projection requires all-success terminal result")
        prepared, memberships = await self._lock_terminal_projection(db, request=request)
        self._validate_terminal_rows(request=request, result=result, prepared=prepared)
        if not source_event_id:
            raise ValueError("E12 terminal projection requires source_event_id")
        memberships_by_route = {membership.route_instance_id: membership for membership in memberships}
        routes_by_id = {route.route_instance_id: route for route in prepared.routes}
        for member, item in zip(prepared.members, result.items, strict=True):
            route = routes_by_id[member.route_instance_id]
            self._terminalize_member(member, item=item, occurred_at_ms=occurred_at_ms)
            self._project_known_entry(
                db,
                member=member,
                route=route,
                item=item,
                membership=memberships_by_route.get(member.route_instance_id),
                membership_status="ACTIVE",
                reconciliation_case_id=None,
                occurred_at_ms=occurred_at_ms,
                source_event_id=source_event_id,
            )
            member.reservation_released_at_ms = occurred_at_ms
        await db.flush()

    async def project_reconciliation(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsToConveyorEntryRequest,
        result: MoveBinsToConveyorEntryResult,
        reconciliation_case_id: int,
        occurred_at_ms: int | None,
        source_event_id: str | None,
        reason_code: str,
    ) -> None:
        """非成功 terminal 在 OPEN case 下逐成员收敛，UNKNOWN 继续占用预约。"""

        if result.task_outcome == "SUCCESS":
            raise ValueError("E12 reconciliation projection requires non-success terminal result")
        prepared, memberships = await self._lock_terminal_projection(db, request=request)
        self._validate_terminal_rows(request=request, result=result, prepared=prepared)
        case = await self._repository.get_open_reconciliation_case_for_update(
            db,
            reconciliation_case_id=reconciliation_case_id,
        )
        if case is None or case.runtime_intent_log_id != prepared.intent.id:
            raise ValueError("E12 reconciliation case differs from batch root")
        if not source_event_id or not reason_code:
            raise ValueError("E12 reconciliation projection requires stable evidence identity")
        terminal_at_ms = case.opened_at_ms if occurred_at_ms is None else occurred_at_ms
        memberships_by_route = {membership.route_instance_id: membership for membership in memberships}
        routes_by_id = {route.route_instance_id: route for route in prepared.routes}
        for member, item in zip(prepared.members, result.items, strict=True):
            route = routes_by_id[member.route_instance_id]
            membership = memberships_by_route.get(member.route_instance_id)
            if member.member_state == "TERMINAL":
                if member.terminal_at_ms is None or not member.terminal_outcome:
                    raise ValueError("E12 persisted terminal member fact is incomplete")
                self._freeze_reconciliation_projection(
                    route=route,
                    membership=membership,
                    reconciliation_case_id=reconciliation_case_id,
                )
                continue
            if member.member_state == "RELEASED":
                if member.reservation_released_at_ms is None:
                    raise ValueError("E12 persisted released member fact is incomplete")
                self._freeze_reconciliation_projection(
                    route=route,
                    membership=membership,
                    reconciliation_case_id=reconciliation_case_id,
                )
                continue
            if item.item_outcome == "UNKNOWN":
                self._terminalize_member(member, item=item, occurred_at_ms=terminal_at_ms)
                if self._has_later_position_fact(route=route, membership=membership):
                    member.reservation_released_at_ms = terminal_at_ms
                self._freeze_reconciliation_projection(
                    route=route,
                    membership=membership,
                    reconciliation_case_id=reconciliation_case_id,
                )
                continue
            self._terminalize_member(member, item=item, occurred_at_ms=terminal_at_ms)
            member.reservation_released_at_ms = terminal_at_ms
            self._project_known_entry(
                db,
                member=member,
                route=route,
                item=item,
                membership=membership,
                membership_status=("ACTIVE" if item.item_outcome == "SUCCESS" else "RECONCILING"),
                reconciliation_case_id=(None if item.item_outcome == "SUCCESS" else reconciliation_case_id),
                occurred_at_ms=terminal_at_ms,
                source_event_id=source_event_id,
            )
        await db.flush()

    async def project_reconciliation_opened(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
        result: MoveBinsToConveyorEntryResult | None,
        reason_code: str,
        evidence_json: dict[str, Any],
    ) -> None:
        """从现有 Outbox 冻结 request 与 OPEN case 收敛非成功 terminal。"""

        payload = await self._repository.get_frozen_request_payload(db, dispatch_key=dispatch_key)
        if payload is None:
            raise RuntimeError("E12 frozen outbox request is missing")
        request = validate_json_payload(MoveBinsToConveyorEntryRequest, payload)
        case_id = await self._repository.resolve_open_reconciliation_case_id(
            db,
            dispatch_key=dispatch_key,
        )
        if case_id is None:
            raise RuntimeError("E12 OPEN reconciliation case is missing")
        source_event_id = generated_effect_source_event_id(
            "wms-e12-reconciliation-projection",
            dispatch_key,
            reason_code,
            evidence_json,
        )
        if result is not None:
            await self.project_reconciliation(
                db,
                request=request,
                result=result,
                reconciliation_case_id=case_id,
                occurred_at_ms=None,
                source_event_id=source_event_id,
                reason_code=reason_code,
            )
            return
        await self._freeze_reconciliation(
            db,
            request=request,
            reconciliation_case_id=case_id,
        )

    async def _freeze_reconciliation(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsToConveyorEntryRequest,
        reconciliation_case_id: int,
    ) -> None:
        prepared, memberships = await self._lock_terminal_projection(db, request=request)
        case = await self._repository.get_open_reconciliation_case_for_update(
            db,
            reconciliation_case_id=reconciliation_case_id,
        )
        if case is None or case.runtime_intent_log_id != prepared.intent.id:
            raise ValueError("E12 reconciliation case differs from batch root")
        for member in prepared.members:
            if member.member_state not in {"CANDIDATE", "ACCEPTED", "TERMINAL", "RELEASED"}:
                raise ValueError("E12 generic reconciliation found unknown member state")
        memberships_by_route = {membership.route_instance_id: membership for membership in memberships}
        for route in prepared.routes:
            self._freeze_reconciliation_projection(
                route=route,
                membership=memberships_by_route.get(route.route_instance_id),
                reconciliation_case_id=reconciliation_case_id,
            )
        await db.flush()

    async def _lock_terminal_projection(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsToConveyorEntryRequest,
    ) -> tuple[WmsConveyorPreparedBatchRows, tuple[ConveyorQueueMembership, ...]]:
        workline_id = await self._repository.resolve_prepared_batch_workline_id(
            db,
            dispatch_key=request.dispatch_key,
        )
        if workline_id is None:
            raise RuntimeError("E12 prepared batch is missing")
        await self._repository.lock_entry_queue(
            db,
            workline_id=workline_id,
            queue_code=request.destination_station_code,
        )
        prepared = await self._repository.lock_prepared_batch(db, dispatch_key=request.dispatch_key)
        if prepared is None:
            raise RuntimeError("E12 prepared batch is missing")
        self._validate_prepared_rows(request=request, prepared=prepared)
        memberships = await self._repository.lock_entry_memberships(
            db,
            route_instance_ids=tuple(item.route_instance_id for item in request.items),
        )
        return prepared, memberships

    @staticmethod
    def _validate_terminal_rows(
        *,
        request: MoveBinsToConveyorEntryRequest,
        result: MoveBinsToConveyorEntryResult,
        prepared: WmsConveyorPreparedBatchRows,
    ) -> None:
        expected = tuple((item.sequence_no, item.route_instance_id, item.bin_id) for item in request.items)
        actual = tuple((item.sequence_no, item.route_instance_id, item.bin_id) for item in result.items)
        if (
            result.batch_id != request.batch_id
            or result.accepted_object_keys != tuple(item.bin_id for item in request.items)
            or actual != expected
        ):
            raise ValueError("E12 terminal members differ from frozen batch")
        WmsConveyorBatchService._validate_prepared_rows(request=request, prepared=prepared)

    @staticmethod
    def _terminalize_member(
        member: Any,
        *,
        item: BatchItemResult,
        occurred_at_ms: int,
    ) -> None:
        if member.member_state == "TERMINAL":
            if member.terminal_outcome != item.item_outcome:
                raise ValueError("E12 terminal replay conflicts with persisted member outcome")
            return
        if member.member_state != "ACCEPTED" or member.accepted_at_ms is None:
            raise ValueError("E12 terminal member was not accepted")
        member.member_state = "TERMINAL"
        member.terminal_at_ms = occurred_at_ms
        member.terminal_outcome = item.item_outcome

    @staticmethod
    def _project_known_entry(
        db: AsyncSession,
        *,
        member: Any,
        route: Any,
        item: BatchItemResult,
        membership: ConveyorQueueMembership | None,
        membership_status: str,
        reconciliation_case_id: int | None,
        occurred_at_ms: int,
        source_event_id: str,
    ) -> None:
        if item.final_queue_position != member.reserved_queue_position:
            raise ValueError("E12 terminal queue position differs from reservation")
        if route.current_node == "FIVE_RACK" and route.lifecycle_state == "ACTIVE" and route.route_version == 1:
            if membership is not None:
                raise ValueError("E12 initial route already has an entry membership")
            membership = ConveyorQueueMembership(
                bin_code=member.bin_code,
                workline_id=member.workline_id,
                conveyor_code=member.queue_code,
                queue_code=member.queue_code,
                queue_role="ENTRY",
                membership_status=membership_status,
                entered_at=occurred_at_ms,
                route_instance_id=member.route_instance_id,
                queue_position=member.reserved_queue_position,
                evidence_json={
                    "source": "WMS_E12_TERMINAL",
                    "source_event_id": source_event_id,
                    "item_outcome": item.item_outcome,
                },
            )
            db.add(membership)
            route.current_node = "CONVEYOR_ENTRY"
            route.current_rack_code = None
            route.current_slot_code = None
            route.route_version += 1
            route.last_transition_source = "WMS_E12_TERMINAL"
            route.last_transition_source_event_id = source_event_id
        elif route.current_node == "CONVEYOR_ENTRY":
            if (
                membership is None
                or membership.bin_code != member.bin_code
                or membership.queue_code != member.queue_code
                or membership.queue_position != member.reserved_queue_position
            ):
                raise ValueError("E12 entry route membership drifted")
            membership.membership_status = membership_status
        elif route.current_node not in {
            "SCAN1",
            "SCAN2_WORK",
            "SCAN3",
            "NG_LINE",
            "RETURN_QUEUE",
            "CTU_RETURN_IN_FLIGHT",
        } and not (route.current_node == "FIVE_RACK" and route.lifecycle_state == "CLOSED"):
            raise ValueError("E12 terminal conflicts with current route node")
        if reconciliation_case_id is not None:
            WmsConveyorBatchService._freeze_reconciliation_projection(
                route=route,
                membership=membership,
                reconciliation_case_id=reconciliation_case_id,
            )

    @staticmethod
    def _freeze_reconciliation_projection(
        *,
        route: Any,
        membership: ConveyorQueueMembership | None,
        reconciliation_case_id: int,
    ) -> None:
        """只冻结仍可调度的投影；CLOSED route 保留既有终态事实。"""

        if route.lifecycle_state == "ACTIVE":
            route.lifecycle_state = "RECONCILING"
            route.reconciliation_case_id = reconciliation_case_id
        elif route.lifecycle_state == "RECONCILING":
            if route.reconciliation_case_id != reconciliation_case_id:
                raise ValueError("E12 route is bound to a different reconciliation case")
        elif route.lifecycle_state != "CLOSED":
            raise ValueError("E12 route contains unknown lifecycle state")
        if membership is not None and membership.membership_status == "ACTIVE":
            membership.membership_status = "RECONCILING"

    @staticmethod
    def _has_later_position_fact(
        *,
        route: Any,
        membership: ConveyorQueueMembership | None,
    ) -> bool:
        return membership is not None or route.current_node != "FIVE_RACK"

    @staticmethod
    def _validate_prepared_rows(
        *,
        request: MoveBinsToConveyorEntryRequest,
        prepared: WmsConveyorPreparedBatchRows,
    ) -> None:
        if not prepared.members or len(prepared.members) != len(request.items):
            raise ValueError("E12 persisted batch members drifted")
        routes_by_id = {route.route_instance_id: route for route in prepared.routes}
        expected = tuple(
            (
                item.sequence_no,
                item.route_instance_id,
                item.bin_id,
                item.reserved_queue_position,
            )
            for item in request.items
        )
        actual = tuple(
            (
                member.sequence_no,
                member.route_instance_id,
                member.bin_code,
                member.reserved_queue_position,
            )
            for member in prepared.members
        )
        if actual != expected or set(routes_by_id) != {item.route_instance_id for item in request.items}:
            raise ValueError("E12 persisted batch identity drifted")
        if any(
            routes_by_id[item.route_instance_id].bin_code != item.bin_id
            or routes_by_id[item.route_instance_id].created_by_e12_intent_id != prepared.intent.id
            for item in request.items
        ):
            raise ValueError("E12 persisted route identity drifted")

    @classmethod
    def _validate_frozen_request(
        cls,
        *,
        claim: WmsConveyorBatchClaim,
        request: MoveBinsToConveyorEntryRequest,
    ) -> None:
        expected_snapshot = cls.capacity_snapshot_version(
            binding_id=claim.binding_id,
            binding_version=claim.binding_version,
            plugin_config_hash=claim.plugin_config_hash,
            entry_capacity=claim.entry_capacity,
        )
        if (
            request.batch_id != claim.batch_id
            or request.dispatch_key != claim.batch_id
            or request.source_station_code != "TARGET_STATION"
            or request.destination_station_code != claim.queue_code
            or request.capacity_snapshot_version != claim.capacity_snapshot_version
            or request.capacity_snapshot_version != expected_snapshot
            or len(request.items) != len(claim.candidates)
        ):
            raise ValueError("E12 frozen batch request drifted")
        expected_items = tuple(
            (
                sequence_no,
                candidate.route_instance_id,
                candidate.bin_code,
                candidate.source_rack_code,
                candidate.source_slot_code,
                candidate.reserved_queue_position,
            )
            for sequence_no, candidate in enumerate(claim.candidates, start=1)
        )
        actual_items = tuple(
            (
                item.sequence_no,
                item.route_instance_id,
                item.bin_id,
                item.source_rack_id,
                item.source_slot_id,
                item.reserved_queue_position,
            )
            for item in request.items
        )
        if actual_items != expected_items:
            raise ValueError("E12 frozen batch members drifted")

    @staticmethod
    def _empty_reservation() -> WmsConveyorBatchReservation:
        return WmsConveyorBatchReservation(created=False, claim=None, operation=None, request=None)

    @staticmethod
    def _available_source_rows(
        source_rows: tuple[WmsConveyorSourceRow, ...],
        *,
        facts: WmsConveyorAvailabilityFacts,
    ) -> tuple[WmsConveyorSourceRow, ...]:
        templates_by_type: dict[str, list[Any]] = {}
        for template in facts.slot_templates:
            templates_by_type.setdefault(template.bin_type_code, []).append(template)
        occupancy_by_cell = {
            (occupancy.bin_code, str(occupancy.bin_cell_index)): occupancy for occupancy in facts.occupancies
        }
        reserved_cells = {(reservation.bin_code, str(reservation.bin_cell_index)) for reservation in facts.reservations}
        unavailable_bins = facts.routed_bin_codes | facts.queued_bin_codes | facts.owned_bin_codes
        available: list[WmsConveyorSourceRow] = []
        for row in source_rows:
            if row.bin_code in unavailable_bins:
                continue
            if row.allowed_bin_types and row.bin_type_code not in row.allowed_bin_types:
                continue
            has_available_cell = False
            for template in templates_by_type.get(row.bin_type_code, ()):
                cell_key = (row.bin_code, str(template.bin_slot_index))
                if cell_key in reserved_cells:
                    continue
                occupancy = occupancy_by_cell.get(cell_key)
                if occupancy is None:
                    has_available_cell = True
                    break
                remaining = occupancy.remaining_depth_mm
                occupancy_status = getattr(occupancy.occupancy_status, "value", occupancy.occupancy_status)
                if occupancy_status == "OCCUPIED" and remaining is not None and Decimal(remaining) > Decimal("0"):
                    has_available_cell = True
                    break
            if has_available_cell:
                available.append(row)
        return tuple(available)

    @staticmethod
    def _validate_context(
        ctx: dict[str, Any],
    ) -> tuple[AsyncSession, Any, int, int, str, SmtSortingInboundConfig]:
        db = ctx.get("db")
        session = ctx.get("session")
        workline = ctx.get("workline")
        work_item = ctx.get("work_item")
        binding = ctx.get("plugin_binding")
        inbox = ctx.get("inbox")
        if any(value is None for value in (db, session, workline, work_item, binding, inbox)):
            raise PermissionError("E12 reserve requires locked session/work-item/binding identity")
        workline_id = getattr(workline, "id", None)
        binding_id = getattr(binding, "id", None)
        binding_version = getattr(binding, "binding_version", None)
        plugin_config_hash = getattr(binding, "typed_config_hash", None)
        if (
            not isinstance(workline_id, int)
            or not isinstance(binding_id, int)
            or not isinstance(binding_version, int)
            or not isinstance(plugin_config_hash, str)
        ):
            raise PermissionError("E12 reserve execution identity is incomplete")
        binding_identity = (
            getattr(binding, "workline_id", None),
            getattr(binding, "plugin_key", None),
            getattr(binding, "contract_version", None),
        )
        if binding_identity != (workline_id, "smt_sorting_inbound", "smt_sorting_inbound.v1"):
            raise PermissionError("E12 reserve requires pinned smt_sorting_inbound@v1 binding")
        if (
            getattr(workline, "is_active", False) is not True
            or getattr(workline, "is_deleted", False) is True
            or getattr(workline, "deleted_at", None) is not None
        ):
            raise PermissionError("E12 reserve requires active non-deleted WorkLine")
        expected_pin = (
            "smt_sorting_inbound",
            binding_id,
            binding_version,
            plugin_config_hash,
            getattr(binding, "generated_index_digest", None),
        )
        session_pin = (
            getattr(session, "plugin_key", None),
            getattr(session, "plugin_binding_id", None),
            getattr(session, "plugin_binding_version", None),
            getattr(session, "plugin_config_hash", None),
            getattr(session, "plugin_index_digest", None),
        )
        work_item_pin = (
            getattr(work_item, "plugin_key", None),
            getattr(work_item, "plugin_binding_id", None),
            getattr(work_item, "plugin_binding_version", None),
            getattr(work_item, "plugin_config_hash", None),
            getattr(work_item, "plugin_index_digest", None),
        )
        if session_pin != expected_pin or work_item_pin != expected_pin:
            raise PermissionError("E12 reserve pinned binding identity drifted")
        workline_pin = (
            getattr(workline, "plugin_key", None),
            getattr(workline, "contract_version", None),
            getattr(workline, "active_plugin_binding_id", None),
            getattr(workline, "active_plugin_binding_version", None),
            getattr(workline, "active_plugin_config_hash", None),
            getattr(workline, "active_plugin_index_digest", None),
        )
        expected_workline_pin = (
            "smt_sorting_inbound",
            "smt_sorting_inbound.v1",
            binding_id,
            binding_version,
            plugin_config_hash,
            getattr(binding, "generated_index_digest", None),
        )
        if workline_pin != expected_workline_pin:
            raise PermissionError("E12 reserve WorkLine active binding pin drifted")
        if getattr(binding, "is_enabled", True) is not True or getattr(binding, "is_revoked", False) is True:
            raise PermissionError("E12 reserve binding is disabled or revoked")
        if getattr(session, "workline_id", None) != workline_id or getattr(inbox, "workline_id", None) != workline_id:
            raise PermissionError("E12 reserve workline identity drifted")
        config = SmtSortingInboundConfig.model_validate(getattr(binding, "typed_config_json", None))
        return db, workline, binding_id, binding_version, plugin_config_hash, config


wms_conveyor_batch_service = WmsConveyorBatchService()

__all__ = [
    "WmsConveyorBatchCandidate",
    "WmsConveyorBatchClaim",
    "WmsConveyorBatchIdentity",
    "WmsConveyorBatchReservation",
    "WmsConveyorBatchService",
    "wms_conveyor_batch_service",
]
