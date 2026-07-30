"""E13 RETURN_QUEUE FIFO 候选冻结与 preparation claim 服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.app.resource.models import (
    RackBinMount,
    RackBinMountStatus,
    ResourceSourceSystem,
)
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.effect_state_contract import generated_effect_source_event_id
from src.app.runtime.orchestration.repositories.wms_conveyor_return_batch_repository import (
    WmsConveyorReturnBatchRepository,
    WmsConveyorReturnCandidateRow,
    WmsConveyorReturnPreparedRows,
    WmsConveyorReturnTerminalResources,
    wms_conveyor_return_batch_repository,
)
from src.app.wms_integration.effect_runtime import typed_wms_effect_ack_hash
from src.app.wms_integration.ports.fulfillment_operations import (
    MOVE_BINS_FROM_CONVEYOR_EXIT,
    BatchItemResult,
    ConveyorExitCandidate,
    MoveBinsFromConveyorExitRequest,
    MoveBinsFromConveyorExitResult,
    WmsEffectAck,
    frozen_candidate_digest,
    validate_batch_terminal_result,
    validate_fulfillment_ack,
)
from src.app.wms_integration.ports.operation_common import validate_json_payload
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.wms_integration.operation_contract import WmsOperationDefinition


@dataclass(frozen=True, slots=True)
class WmsConveyorReturnCandidate:
    """submit 前冻结的单个 E13 RETURN_QUEUE 候选。"""

    membership_id: int
    route_instance_id: str
    bin_code: str
    scan3_enqueued_at: datetime
    queue_position: int


@dataclass(frozen=True, slots=True)
class WmsConveyorReturnBatchClaim:
    """reserve→Intent claim→preparation hook 同事务传播的候选窗口。"""

    workline_id: int
    queue_code: str
    batch_id: str
    claim_token: str
    candidate_digest: str
    candidates: tuple[WmsConveyorReturnCandidate, ...]


@dataclass(frozen=True, slots=True)
class WmsConveyorReturnBatchReservation:
    """无候选时不创建空 E13 task。"""

    created: bool
    claim: WmsConveyorReturnBatchClaim | None
    operation: WmsOperationDefinition | None
    request: MoveBinsFromConveyorExitRequest | None


@dataclass(frozen=True, slots=True)
class _WmsConveyorReturnTerminalPlan:
    """完整校验后才允许执行的单成员 E13 终态变更计划。"""

    member: Any
    item: BatchItemResult
    route: Any
    source_membership: Any
    active_mount: RackBinMount | None
    already_final: bool
    preserve_first_terminal: bool
    preserve_current_physical: bool


def _new_claim_token() -> str:
    return uuid4().hex


def _now_ms() -> int:
    return int(timezone.now_utc().timestamp() * 1000)


class WmsConveyorReturnBatchService:
    """以 RuntimeIntentLog 为唯一 root 的 E13 reserve/preparation 服务。"""

    def __init__(
        self,
        *,
        repository: WmsConveyorReturnBatchRepository = wms_conveyor_return_batch_repository,
        id_factory: Callable[[], str] = _new_claim_token,
        now_for_db: Callable[[], datetime] = timezone.now_for_db,
        now_ms: Callable[[], int] = _now_ms,
        claim_lease_seconds: int = 60,
    ) -> None:
        if claim_lease_seconds <= 0:
            raise ValueError("E13 claim lease must be positive")
        self._repository = repository
        self._id_factory = id_factory
        self._now_for_db = now_for_db
        self._now_ms = now_ms
        self._claim_lease_seconds = claim_lease_seconds

    async def reserve_batch(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        queue_code: str,
        max_candidate_count: int | None = None,
    ) -> WmsConveyorReturnBatchReservation:
        """锁定 RETURN_QUEUE 的有界 FIFO 窗口；调用方必须在同一短事务继续 preparation。"""

        if workline_id <= 0 or not queue_code:
            raise ValueError("E13 reserve requires workline_id and queue_code")
        operation_limit = MOVE_BINS_FROM_CONVEYOR_EXIT.max_candidate_count
        if operation_limit is None:
            raise RuntimeError("E13 operation max_candidate_count is missing")
        limit = operation_limit if max_candidate_count is None else max_candidate_count
        if limit <= 0 or limit > operation_limit:
            raise ValueError("E13 reserve limit exceeds authored max_candidate_count")
        rows = await self._repository.lock_fifo_candidates(
            db,
            workline_id=workline_id,
            queue_code=queue_code,
            limit=limit,
        )
        if not rows:
            return self._empty_reservation()

        claim_token = self._id_factory()
        if not claim_token or len(claim_token) > 64:
            raise ValueError("E13 claim token is invalid")
        batch_id = self.batch_identity(
            workline_id=workline_id,
            queue_code=queue_code,
            claim_token=claim_token,
        )
        candidates = tuple(self._candidate_from_row(row) for row in rows)
        candidate_items = tuple(
            ConveyorExitCandidate(
                sequence_no=sequence_no,
                route_instance_id=candidate.route_instance_id,
                bin_id=candidate.bin_code,
                scan3_enqueued_at=self.candidate_timestamp(candidate.scan3_enqueued_at),
                queue_position=candidate.queue_position,
            )
            for sequence_no, candidate in enumerate(candidates, start=1)
        )
        digest = frozen_candidate_digest(
            workline_id=workline_id,
            queue_code=queue_code,
            candidate_items=candidate_items,
        )
        request = MoveBinsFromConveyorExitRequest(
            dispatch_key=batch_id,
            batch_id=batch_id,
            direction="FROM_CONVEYOR_EXIT",
            workline_id=workline_id,
            queue_code=queue_code,
            candidate_digest=digest,
            candidate_items=candidate_items,
        )
        claim = WmsConveyorReturnBatchClaim(
            workline_id=workline_id,
            queue_code=queue_code,
            batch_id=batch_id,
            claim_token=claim_token,
            candidate_digest=digest,
            candidates=candidates,
        )
        return WmsConveyorReturnBatchReservation(
            created=True,
            claim=claim,
            operation=MOVE_BINS_FROM_CONVEYOR_EXIT,
            request=request,
        )

    async def prepare_effect(
        self,
        db: AsyncSession,
        *,
        claim: WmsConveyorReturnBatchClaim,
        request: MoveBinsFromConveyorExitRequest,
        intent_id: int,
    ) -> None:
        """在 Outbox 前重验冻结窗口并持久化 source lease 与 RETURN member。"""

        if intent_id <= 0:
            raise ValueError("E13 preparation requires positive intent_id")
        self._validate_frozen_request(claim=claim, request=request)
        claim_until = self._now_for_db() + timedelta(seconds=self._claim_lease_seconds)
        await self._repository.claim_prepared_batch(
            db,
            intent_id=intent_id,
            workline_id=claim.workline_id,
            queue_code=claim.queue_code,
            claim_token=claim.claim_token,
            claim_until=claim_until,
            candidates=claim.candidates,
            staged_at_ms=self._now_ms(),
        )

    async def should_reconcile_ack(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsFromConveyorExitRequest,
        ack: WmsEffectAck,
    ) -> bool:
        """ACK 前缀遗漏任何已观察物理动作时只允许进入 reconciliation。"""

        try:
            validate_fulfillment_ack(request, ack)
            prepared = await self._lock_and_validate_prepared(db, request=request)
        except (RuntimeError, TypeError, ValueError):
            return True
        accepted_count = len(ack.accepted_scope.object_keys) if ack.accepted_scope is not None else 0
        if any(member.member_state != "CANDIDATE" for member in prepared.members):
            return True
        return any(
            self._member_has_observed_physical_action(prepared, member) for member in prepared.members[accepted_count:]
        )

    async def project_ack(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsFromConveyorExitRequest,
        ack: WmsEffectAck,
        occurred_at_ms: int,
        source_event_id: str | None,
    ) -> None:
        """提升非空有序 prefix，并释放 suffix source claim；不写 rack-slot/route。"""

        validate_fulfillment_ack(request, ack)
        if not source_event_id:
            raise ValueError("E13 ACK projection requires source_event_id")
        prepared = await self._lock_and_validate_prepared(db, request=request)
        accepted_count = len(ack.accepted_scope.object_keys) if ack.accepted_scope is not None else 0
        if any(member.member_state != "CANDIDATE" for member in prepared.members):
            raise ValueError("E13 ACK member is not pending acceptance")
        if any(
            self._member_has_observed_physical_action(prepared, member) for member in prepared.members[accepted_count:]
        ):
            raise ValueError("E13 ACK prefix omits a candidate with observed physical action")
        memberships_by_id = {membership.id: membership for membership in prepared.memberships}
        for index, member in enumerate(prepared.members):
            source = memberships_by_id[member.source_queue_membership_id]
            if index < accepted_count:
                member.member_state = "ACCEPTED"
                member.accepted_at_ms = occurred_at_ms
                continue
            member.member_state = "RELEASED"
            member.reservation_released_at_ms = occurred_at_ms
            source.e13_claim_intent_id = None
            source.e13_claim_token = None
            source.e13_claim_until = None
            if source.membership_status == "RECONCILING" and source.left_at is None:
                source.membership_status = "ACTIVE"
        await db.flush()

    async def should_reconcile_transport_failure(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsFromConveyorExitRequest,
    ) -> bool:
        try:
            prepared = await self._lock_and_validate_prepared(db, request=request)
        except (RuntimeError, TypeError, ValueError):
            return True
        return any(member.member_state in {"ACCEPTED", "TERMINAL"} for member in prepared.members) or any(
            self._member_has_observed_physical_action(prepared, member) for member in prepared.members
        )

    async def project_reject(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsFromConveyorExitRequest,
        occurred_at_ms: int,
        source_event_id: str | None,
    ) -> None:
        """NO_DESTINATION_CAPACITY 等 task-before-create 拒绝释放全部候选。"""

        if not source_event_id:
            raise ValueError("E13 reject projection requires source_event_id")
        prepared = await self._lock_and_validate_prepared(db, request=request)
        has_physical_action = any(
            self._member_has_observed_physical_action(prepared, member) for member in prepared.members
        )
        if all(member.member_state == "RELEASED" for member in prepared.members) and not has_physical_action:
            return
        if any(member.member_state != "CANDIDATE" for member in prepared.members) or has_physical_action:
            raise ValueError("E13 reject conflicts with accepted or physical candidate facts")
        memberships_by_id = {membership.id: membership for membership in prepared.memberships}
        for member in prepared.members:
            source = memberships_by_id[member.source_queue_membership_id]
            member.member_state = "RELEASED"
            member.reservation_released_at_ms = occurred_at_ms
            source.e13_claim_intent_id = None
            source.e13_claim_token = None
            source.e13_claim_until = None
            if source.membership_status == "RECONCILING" and source.left_at is None:
                source.membership_status = "ACTIVE"
        await db.flush()

    async def project_success(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsFromConveyorExitRequest,
        result: MoveBinsFromConveyorExitResult,
        occurred_at_ms: int,
        source_event_id: str | None,
        frozen_ack: WmsEffectAck | None = None,
    ) -> None:
        """全成功 terminal 原子关闭 source mount，并落到当前 TARGET 五层架。"""

        if result.task_outcome != "SUCCESS" or any(item.item_outcome != "SUCCESS" for item in result.items):
            raise ValueError("E13 success projection requires all-success terminal result")
        await self._project_terminal(
            db,
            request=request,
            result=result,
            reconciliation_case_id=None,
            occurred_at_ms=occurred_at_ms,
            source_event_id=source_event_id,
            reason_code=None,
            frozen_ack=frozen_ack,
        )

    async def project_reconciliation(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsFromConveyorExitRequest,
        result: MoveBinsFromConveyorExitResult,
        reconciliation_case_id: int,
        occurred_at_ms: int | None,
        source_event_id: str | None,
        reason_code: str,
        frozen_ack: WmsEffectAck | None = None,
    ) -> None:
        """非成功 terminal 仅对 known 成员收敛位置，UNKNOWN 冻结当前事实。"""

        if result.task_outcome == "SUCCESS":
            raise ValueError("E13 reconciliation projection requires non-success terminal result")
        await self._project_terminal(
            db,
            request=request,
            result=result,
            reconciliation_case_id=reconciliation_case_id,
            occurred_at_ms=occurred_at_ms,
            source_event_id=source_event_id,
            reason_code=reason_code,
            frozen_ack=frozen_ack,
        )

    async def _project_terminal(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsFromConveyorExitRequest,
        result: MoveBinsFromConveyorExitResult,
        reconciliation_case_id: int | None,
        occurred_at_ms: int | None,
        source_event_id: str | None,
        reason_code: str | None,
        frozen_ack: WmsEffectAck | None,
    ) -> None:
        if not source_event_id:
            raise ValueError("E13 terminal projection requires source_event_id")
        if reconciliation_case_id is not None and not reason_code:
            raise ValueError("E13 reconciliation projection requires stable reason")

        prepared = await self._lock_and_validate_prepared(db, request=request)
        self._validate_persisted_ack(
            request=request,
            result=result,
            prepared=prepared,
            frozen_ack=frozen_ack,
        )
        accepted_members = self._validate_terminal_identity(
            request=request,
            result=result,
            prepared=prepared,
        )
        target_keys = tuple(
            (item.final_rack_id, item.final_slot_id)
            for member, item in zip(accepted_members, result.items, strict=True)
            if member.member_state != "TERMINAL"
            and item.item_outcome != "UNKNOWN"
            and next(
                route for route in prepared.routes if route.route_instance_id == member.route_instance_id
            ).current_node
            == "RETURN_QUEUE"
            and item.final_rack_id is not None
            and item.final_slot_id is not None
        )
        resources = await self._repository.lock_terminal_resources(
            db,
            workline_id=request.workline_id,
            accepted_bin_codes=tuple(member.bin_code for member in accepted_members),
            target_keys=target_keys,
        )

        case = None
        if reconciliation_case_id is not None:
            case = await self._repository.get_open_reconciliation_case_for_update(
                db,
                reconciliation_case_id=reconciliation_case_id,
            )
            if case is None or case.runtime_intent_log_id != prepared.intent.id:
                raise ValueError("E13 reconciliation case differs from batch root")
        terminal_at_ms = case.opened_at_ms if occurred_at_ms is None and case is not None else occurred_at_ms
        if terminal_at_ms is None or terminal_at_ms < 0:
            raise ValueError("E13 terminal timestamp is invalid")
        occurred_at = timezone.to_db_datetime(terminal_at_ms / 1000)
        if occurred_at is None:
            raise ValueError("E13 terminal timestamp is invalid")

        plans = self._validate_terminal_resources(
            request=request,
            result=result,
            prepared=prepared,
            accepted_members=accepted_members,
            resources=resources,
            reconciliation_case_id=reconciliation_case_id,
        )
        new_mounts: list[RackBinMount] = []
        for plan in plans:
            if plan.preserve_first_terminal:
                continue
            self._terminalize_member(
                plan.member,
                item=plan.item,
                occurred_at_ms=terminal_at_ms,
            )
            if plan.item.item_outcome == "UNKNOWN":
                self._freeze_unknown_projection(
                    route=plan.route,
                    source_membership=plan.source_membership,
                    reconciliation_case_id=reconciliation_case_id,
                )
                continue

            plan.member.reservation_released_at_ms = terminal_at_ms
            self._leave_source_membership(
                plan.source_membership,
                occurred_at_ms=terminal_at_ms,
            )
            if plan.preserve_current_physical:
                self._freeze_existing_physical_projection(
                    route=plan.route,
                    reconciliation_case_id=reconciliation_case_id,
                )
                continue
            if not plan.already_final:
                if plan.active_mount is None:
                    raise AssertionError("validated E13 known member lost active mount")
                plan.active_mount.mount_status = RackBinMountStatus.UNMOUNTED
                plan.active_mount.ended_at = occurred_at
                new_mounts.append(
                    RackBinMount(
                        rack_code=plan.item.final_rack_id,
                        rack_slot_code=plan.item.final_slot_id,
                        bin_code=plan.member.bin_code,
                        mount_status=RackBinMountStatus.MOUNTED,
                        source_system=ResourceSourceSystem.WMS,
                        source_event_id=source_event_id,
                        source_version=result.source_version,
                        started_at=occurred_at,
                    )
                )
            self._project_known_route(
                route=plan.route,
                item=plan.item,
                item_outcome=plan.item.item_outcome,
                reconciliation_case_id=reconciliation_case_id,
                occurred_at_ms=terminal_at_ms,
                source_event_id=source_event_id,
                physical_already_projected=(
                    plan.already_final
                    and plan.route.current_node == "FIVE_RACK"
                    and plan.route.current_rack_code == plan.item.final_rack_id
                    and plan.route.current_slot_code == plan.item.final_slot_id
                ),
            )
        if new_mounts:
            db.add_all(new_mounts)
        # 所有成员、目标与 mount 已完整验证；终态聚合只在边界执行一次 flush。
        await db.flush()

    async def project_reconciliation_opened(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
        reason_code: str | None,
        evidence_json: dict[str, object] | None,
        result: MoveBinsFromConveyorExitResult | None = None,
        frozen_ack: WmsEffectAck | None = None,
    ) -> None:
        """使用既有 OPEN case 收敛 typed result；无结果时只冻结仍由本 intent claimed 的成员。"""

        if not reason_code or not isinstance(evidence_json, dict):
            raise ValueError("E13 reconciliation projection requires reason and evidence")
        prepared = await self._repository.lock_prepared_batch(db, dispatch_key=dispatch_key)
        if prepared is None:
            raise RuntimeError("E13 prepared batch is missing")
        reconciliation_case_id = await self._repository.resolve_open_reconciliation_case_id(
            db,
            dispatch_key=dispatch_key,
        )
        if reconciliation_case_id is None:
            raise RuntimeError("E13 OPEN reconciliation case is missing")
        if result is not None:
            request = validate_json_payload(MoveBinsFromConveyorExitRequest, prepared.outbox.payload_json)
            await self.project_reconciliation(
                db,
                request=request,
                result=result,
                reconciliation_case_id=reconciliation_case_id,
                occurred_at_ms=None,
                source_event_id=generated_effect_source_event_id(
                    "wms-e13-reconciliation-projection",
                    dispatch_key,
                    reason_code,
                    evidence_json,
                ),
                reason_code=reason_code,
                frozen_ack=frozen_ack,
            )
            return

        routes_by_id = {route.route_instance_id: route for route in prepared.routes}
        memberships_by_id = {membership.id: membership for membership in prepared.memberships}
        for member in prepared.members:
            if member.member_state not in {"CANDIDATE", "ACCEPTED"}:
                continue
            source = memberships_by_id[member.source_queue_membership_id]
            route = routes_by_id[member.route_instance_id]
            if route.lifecycle_state == "ACTIVE":
                route.lifecycle_state = "RECONCILING"
                route.closed_at_ms = None
                route.reconciliation_case_id = reconciliation_case_id
            if source.membership_status == "ACTIVE":
                source.membership_status = "RECONCILING"
        await db.flush()

    @staticmethod
    def has_observed_physical_action(
        *,
        route: object,
        source_membership: object,
        current_membership: object | None,
    ) -> bool:
        """仅位置事实代表已动作；RECONCILING 状态本身不是物理证据。"""

        return (
            getattr(route, "current_node", None) != "RETURN_QUEUE"
            or getattr(source_membership, "membership_status", None) == "LEFT"
            or getattr(source_membership, "left_at", None) is not None
            or current_membership is not None
        )

    @staticmethod
    def batch_identity(*, workline_id: int, queue_code: str, claim_token: str) -> str:
        digest = sha256_digest(
            {
                "workline_id": workline_id,
                "queue_code": queue_code,
                "claim_token": claim_token,
            }
        )
        return f"wms-e13:{workline_id}:{digest}"

    @staticmethod
    def candidate_timestamp(value: datetime) -> str:
        """数据库 naive UTC 转为 wire aware UTC；禁止直接对 naive datetime 取 timestamp。"""

        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.isoformat()

    @staticmethod
    def _candidate_from_row(row: WmsConveyorReturnCandidateRow) -> WmsConveyorReturnCandidate:
        return WmsConveyorReturnCandidate(
            membership_id=row.membership_id,
            route_instance_id=row.route_instance_id,
            bin_code=row.bin_code,
            scan3_enqueued_at=row.scan3_enqueued_at,
            queue_position=row.queue_position,
        )

    @staticmethod
    def _validate_terminal_identity(
        *,
        request: MoveBinsFromConveyorExitRequest,
        result: MoveBinsFromConveyorExitResult,
        prepared: WmsConveyorReturnPreparedRows,
    ) -> tuple[Any, ...]:
        if (
            result.dispatch_key != request.dispatch_key
            or result.batch_id != request.batch_id
            or result.candidate_digest != request.candidate_digest
        ):
            raise ValueError("E13 terminal root identity differs from frozen request")

        accepted: list[Any] = []
        released_suffix_started = False
        for member in prepared.members:
            if member.member_state in {"ACCEPTED", "TERMINAL"}:
                if released_suffix_started:
                    raise ValueError("E13 accepted members are not a contiguous ACK prefix")
                if member.accepted_at_ms is None:
                    raise ValueError("E13 accepted member is incomplete")
                accepted.append(member)
                continue
            if member.member_state == "RELEASED":
                released_suffix_started = True
                if member.reservation_released_at_ms is None:
                    raise ValueError("E13 released suffix member is incomplete")
                continue
            raise ValueError("E13 terminal member was not accepted by ACK")
        if not accepted:
            raise ValueError("E13 terminal requires a non-empty accepted ACK prefix")

        accepted_members = tuple(accepted)
        expected = tuple((member.sequence_no, member.route_instance_id, member.bin_code) for member in accepted_members)
        actual = tuple((item.sequence_no, item.route_instance_id, item.bin_id) for item in result.items)
        if result.accepted_object_keys != tuple(member.bin_code for member in accepted_members) or actual != expected:
            raise ValueError("E13 terminal members differ from accepted ACK prefix")

        expected_task_outcome = (
            "SUCCESS"
            if all(item.item_outcome == "SUCCESS" for item in result.items)
            else "PARTIAL_FAILURE"
            if any(item.item_outcome == "SUCCESS" for item in result.items)
            else "FAILED_AFTER_EXECUTION"
        )
        if result.task_outcome != expected_task_outcome:
            raise ValueError("E13 task_outcome differs from member outcomes")
        targets: list[tuple[str, str]] = []
        for item in result.items:
            if item.item_outcome == "UNKNOWN":
                if any(
                    value is not None for value in (item.final_rack_id, item.final_slot_id, item.final_queue_position)
                ):
                    raise ValueError("E13 UNKNOWN member must not claim final facts")
                continue
            if item.final_rack_id is None or item.final_slot_id is None or item.final_queue_position is not None:
                raise ValueError("E13 known member requires only final rack and slot")
            targets.append((item.final_rack_id, item.final_slot_id))
        if len(targets) != len(set(targets)):
            raise ValueError("E13 known members require unique final rack/slot targets")
        return accepted_members

    @staticmethod
    def _validate_persisted_ack(
        *,
        request: MoveBinsFromConveyorExitRequest,
        result: MoveBinsFromConveyorExitResult,
        prepared: WmsConveyorReturnPreparedRows,
        frozen_ack: WmsEffectAck | None,
    ) -> None:
        ack = frozen_ack
        if ack is None:
            current_outcome = prepared.intent.outcome_json
            envelope = current_outcome.get("outcome") if isinstance(current_outcome, dict) else None
            payload = (
                envelope.get("payload") if isinstance(envelope, dict) and envelope.get("kind") == "success" else None
            )
            if not isinstance(payload, dict):
                raise TypeError("E13 terminal requires persisted ACK authority")
            ack = validate_json_payload(WmsEffectAck, payload)
        ack_hash = typed_wms_effect_ack_hash(ack)
        expected_reference = f"runtime-intent-outcome:{prepared.intent.dispatch_key}"
        acceptance_evidence = [
            item
            for item in (prepared.intent.outcome_history_json or ())
            if isinstance(item, dict) and item.get("event_type") == "TRANSPORT_ACCEPTED"
        ]
        if not acceptance_evidence or any(
            item.get("typed_ack_hash") != ack_hash or item.get("typed_ack_reference") != expected_reference
            for item in acceptance_evidence
        ):
            raise ValueError("E13 terminal ACK differs from persisted acceptance evidence")
        validate_batch_terminal_result(request, ack, result)

    @staticmethod
    def _validate_terminal_resources(
        *,
        request: MoveBinsFromConveyorExitRequest,
        result: MoveBinsFromConveyorExitResult,
        prepared: WmsConveyorReturnPreparedRows,
        accepted_members: tuple[Any, ...],
        resources: WmsConveyorReturnTerminalResources,
        reconciliation_case_id: int | None,
    ) -> tuple[_WmsConveyorReturnTerminalPlan, ...]:
        routes_by_id = {route.route_instance_id: route for route in prepared.routes}
        memberships_by_id = {membership.id: membership for membership in prepared.memberships}
        active_mounts_by_bin = {mount.bin_code: mount for mount in resources.active_mounts}
        active_mounts_by_target = {(mount.rack_code, mount.rack_slot_code): mount for mount in resources.active_mounts}

        required_target_keys = {
            (item.final_rack_id, item.final_slot_id)
            for member, item in zip(accepted_members, result.items, strict=True)
            if member.member_state != "TERMINAL"
            and item.item_outcome != "UNKNOWN"
            and routes_by_id[member.route_instance_id].current_node == "RETURN_QUEUE"
        }
        target_rows_by_key = {(row.rack.rack_code, row.slot.slot_code): row for row in resources.targets}
        if required_target_keys:
            position = resources.target_position
            if position is None or set(target_rows_by_key) != required_target_keys:
                raise ValueError("E13 target rack-slot is not an eligible current work face")
            if any(
                row.placement.workline_id != request.workline_id
                or row.placement.workline_code != position.workline_code
                or row.placement.position_code != position.position_code
                for row in target_rows_by_key.values()
            ):
                raise ValueError("E13 target placement differs from WorklineRackPosition manifest")
            if len({row.rack.rack_code for row in target_rows_by_key.values()}) > position.capacity:
                raise ValueError("E13 target racks exceed manifest position capacity")

        plans: list[_WmsConveyorReturnTerminalPlan] = []
        for member, item in zip(accepted_members, result.items, strict=True):
            route = routes_by_id.get(member.route_instance_id)
            source = memberships_by_id.get(member.source_queue_membership_id)
            if route is None or source is None:
                raise ValueError("E13 terminal source aggregate is incomplete")
            active_mount = active_mounts_by_bin.get(member.bin_code)

            if member.member_state == "TERMINAL":
                if member.terminal_at_ms is None or member.terminal_outcome is None:
                    raise ValueError("E13 persisted terminal member fact is incomplete")
                matches = WmsConveyorReturnBatchService._matches_persisted_terminal(
                    member=member,
                    item=item,
                    route=route,
                    source_membership=source,
                    active_mount=active_mount,
                )
                if not matches and reconciliation_case_id is None:
                    raise ValueError("E13 terminal replay conflicts with persisted first facts")
                plans.append(
                    _WmsConveyorReturnTerminalPlan(
                        member=member,
                        item=item,
                        route=route,
                        source_membership=source,
                        active_mount=active_mount,
                        already_final=matches,
                        preserve_first_terminal=True,
                        preserve_current_physical=False,
                    )
                )
                continue
            if member.member_state != "ACCEPTED" or member.accepted_at_ms is None:
                raise ValueError("E13 terminal member was not accepted")

            if item.item_outcome == "UNKNOWN":
                if reconciliation_case_id is None:
                    raise ValueError("E13 UNKNOWN terminal requires an OPEN reconciliation case")
                plans.append(
                    _WmsConveyorReturnTerminalPlan(
                        member=member,
                        item=item,
                        route=route,
                        source_membership=source,
                        active_mount=active_mount,
                        already_final=False,
                        preserve_first_terminal=False,
                        preserve_current_physical=False,
                    )
                )
                continue

            target_key = (item.final_rack_id, item.final_slot_id)
            occupant = active_mounts_by_target.get(target_key)
            already_final = (
                route.current_node == "FIVE_RACK"
                and (route.current_rack_code, route.current_slot_code) == target_key
                and active_mount is not None
                and (active_mount.rack_code, active_mount.rack_slot_code) == target_key
            )
            if route.current_node != "RETURN_QUEUE":
                route_key = (route.current_rack_code, route.current_slot_code)
                mount_key = (active_mount.rack_code, active_mount.rack_slot_code) if active_mount is not None else None
                if route.current_node == "FIVE_RACK" and route_key == target_key and mount_key == target_key:
                    already_final = True
                else:
                    if reconciliation_case_id is None:
                        raise ValueError("E13 terminal conflicts with a later local route fact")
                    plans.append(
                        _WmsConveyorReturnTerminalPlan(
                            member=member,
                            item=item,
                            route=route,
                            source_membership=source,
                            active_mount=active_mount,
                            already_final=False,
                            preserve_first_terminal=False,
                            preserve_current_physical=True,
                        )
                    )
                    continue
            if occupant is not None and occupant.bin_code != member.bin_code:
                raise ValueError("E13 target rack-slot is occupied by another bin")
            if active_mount is None:
                raise ValueError("E13 known member has no active source mount")
            plans.append(
                _WmsConveyorReturnTerminalPlan(
                    member=member,
                    item=item,
                    route=route,
                    source_membership=source,
                    active_mount=active_mount,
                    already_final=already_final,
                    preserve_first_terminal=False,
                    preserve_current_physical=False,
                )
            )
        return tuple(plans)

    @staticmethod
    def _matches_persisted_terminal(
        *,
        member: Any,
        item: BatchItemResult,
        route: Any,
        source_membership: Any,
        active_mount: RackBinMount | None,
    ) -> bool:
        if member.terminal_outcome != item.item_outcome:
            return False
        if item.item_outcome == "UNKNOWN":
            return item.final_rack_id is None and item.final_slot_id is None
        return (
            route.current_node == "FIVE_RACK"
            and route.current_rack_code == item.final_rack_id
            and route.current_slot_code == item.final_slot_id
            and source_membership.membership_status == "LEFT"
            and source_membership.e13_claim_intent_id is None
            and source_membership.e13_claim_token is None
            and source_membership.e13_claim_until is None
            and active_mount is not None
            and active_mount.rack_code == item.final_rack_id
            and active_mount.rack_slot_code == item.final_slot_id
        )

    @staticmethod
    def _terminalize_member(
        member: Any,
        *,
        item: BatchItemResult,
        occurred_at_ms: int,
    ) -> None:
        member.member_state = "TERMINAL"
        member.terminal_at_ms = occurred_at_ms
        member.terminal_outcome = item.item_outcome

    @staticmethod
    def _leave_source_membership(source_membership: Any, *, occurred_at_ms: int) -> None:
        if source_membership.membership_status != "LEFT":
            source_membership.membership_status = "LEFT"
            source_membership.left_at = occurred_at_ms
        source_membership.e13_claim_intent_id = None
        source_membership.e13_claim_token = None
        source_membership.e13_claim_until = None

    @staticmethod
    def _project_known_route(
        *,
        route: Any,
        item: BatchItemResult,
        item_outcome: str,
        reconciliation_case_id: int | None,
        occurred_at_ms: int,
        source_event_id: str,
        physical_already_projected: bool,
    ) -> None:
        if not physical_already_projected:
            route.current_node = "FIVE_RACK"
            route.current_rack_code = item.final_rack_id
            route.current_slot_code = item.final_slot_id
            route.route_version += 1
            route.last_transition_source = "WMS_E13_TERMINAL"
            route.last_transition_source_event_id = source_event_id
        if item_outcome == "SUCCESS":
            route.lifecycle_state = "CLOSED"
            route.closed_at_ms = occurred_at_ms
            route.reconciliation_case_id = None
            return
        if reconciliation_case_id is None:
            raise ValueError("E13 FAILED terminal requires an OPEN reconciliation case")
        route.lifecycle_state = "RECONCILING"
        route.closed_at_ms = None
        route.reconciliation_case_id = reconciliation_case_id

    @staticmethod
    def _freeze_existing_physical_projection(
        *,
        route: Any,
        reconciliation_case_id: int | None,
    ) -> None:
        if reconciliation_case_id is None:
            raise ValueError("E13 physical conflict requires an OPEN reconciliation case")
        if route.lifecycle_state == "ACTIVE":
            route.lifecycle_state = "RECONCILING"
            route.closed_at_ms = None
            route.reconciliation_case_id = reconciliation_case_id

    @staticmethod
    def _freeze_unknown_projection(
        *,
        route: Any,
        source_membership: Any,
        reconciliation_case_id: int | None,
    ) -> None:
        if reconciliation_case_id is None:
            raise ValueError("E13 UNKNOWN terminal requires an OPEN reconciliation case")
        if route.lifecycle_state == "ACTIVE":
            route.lifecycle_state = "RECONCILING"
            route.closed_at_ms = None
            route.reconciliation_case_id = reconciliation_case_id
        if source_membership.membership_status == "ACTIVE":
            source_membership.membership_status = "RECONCILING"

    async def _lock_and_validate_prepared(
        self,
        db: AsyncSession,
        *,
        request: MoveBinsFromConveyorExitRequest,
    ) -> WmsConveyorReturnPreparedRows:
        prepared = await self._repository.lock_prepared_batch(db, dispatch_key=request.dispatch_key)
        if prepared is None or prepared.intent.id is None:
            raise RuntimeError("E13 prepared batch is missing")
        if prepared.outbox.payload_json != request.model_dump(mode="json"):
            raise ValueError("E13 frozen Outbox request drifted")
        expected = tuple((item.sequence_no, item.route_instance_id, item.bin_id) for item in request.candidate_items)
        actual = tuple((member.sequence_no, member.route_instance_id, member.bin_code) for member in prepared.members)
        if actual != expected:
            raise ValueError("E13 prepared members differ from frozen candidates")
        routes_by_id = {route.route_instance_id: route for route in prepared.routes}
        memberships_by_id = {membership.id: membership for membership in prepared.memberships}
        for member, item in zip(prepared.members, request.candidate_items, strict=True):
            source = memberships_by_id.get(member.source_queue_membership_id)
            route = routes_by_id.get(member.route_instance_id)
            current = next(
                (
                    membership
                    for membership in prepared.memberships
                    if membership.route_instance_id == member.route_instance_id
                    and membership.id != member.source_queue_membership_id
                    and membership.membership_status in {"ACTIVE", "RECONCILING"}
                ),
                None,
            )
            claim_fields = (
                getattr(source, "e13_claim_intent_id", None),
                getattr(source, "e13_claim_token", None),
                getattr(source, "e13_claim_until", None),
            )
            has_active_claim = (
                claim_fields[0] == prepared.intent.id
                and isinstance(claim_fields[1], str)
                and bool(claim_fields[1])
                and claim_fields[2] is not None
            )
            has_released_claim_after_physical_action = (
                claim_fields == (None, None, None)
                and source is not None
                and route is not None
                and self.has_observed_physical_action(
                    route=route,
                    source_membership=source,
                    current_membership=current,
                )
            )
            is_released_ack_suffix = (
                member.member_state == "RELEASED"
                and member.reservation_released_at_ms is not None
                and claim_fields == (None, None, None)
                and source is not None
                and source.membership_status == "ACTIVE"
                and source.left_at is None
                and route is not None
                and route.current_node == "RETURN_QUEUE"
            )
            if (
                source is None
                or route is None
                or not (has_active_claim or has_released_claim_after_physical_action or is_released_ack_suffix)
                or source.route_instance_id != item.route_instance_id
                or source.bin_code != item.bin_id
                or source.workline_id != request.workline_id
                or source.queue_code != request.queue_code
                or source.queue_role != "RETURN_QUEUE"
                or route.bin_code != item.bin_id
                or route.workline_id != request.workline_id
            ):
                raise ValueError("E13 source claim/member/route drifted")
        return prepared

    def _member_has_observed_physical_action(
        self,
        prepared: WmsConveyorReturnPreparedRows,
        member: object,
    ) -> bool:
        routes_by_id = {route.route_instance_id: route for route in prepared.routes}
        memberships_by_id = {membership.id: membership for membership in prepared.memberships}
        source_membership_id = member.source_queue_membership_id  # type: ignore[attr-defined]
        route_instance_id = member.route_instance_id  # type: ignore[attr-defined]
        source = memberships_by_id[source_membership_id]
        current = next(
            (
                membership
                for membership in prepared.memberships
                if membership.route_instance_id == route_instance_id
                and membership.id != source.id
                and membership.membership_status in {"ACTIVE", "RECONCILING"}
            ),
            None,
        )
        return self.has_observed_physical_action(
            route=routes_by_id[route_instance_id],
            source_membership=source,
            current_membership=current,
        )

    @staticmethod
    def _validate_frozen_request(
        *,
        claim: WmsConveyorReturnBatchClaim,
        request: MoveBinsFromConveyorExitRequest,
    ) -> None:
        expected_items = tuple(
            (
                sequence_no,
                candidate.route_instance_id,
                candidate.bin_code,
                WmsConveyorReturnBatchService.candidate_timestamp(candidate.scan3_enqueued_at),
                candidate.queue_position,
            )
            for sequence_no, candidate in enumerate(claim.candidates, start=1)
        )
        actual_items = tuple(
            (
                item.sequence_no,
                item.route_instance_id,
                item.bin_id,
                item.scan3_enqueued_at,
                item.queue_position,
            )
            for item in request.candidate_items
        )
        if (
            request.dispatch_key != claim.batch_id
            or request.batch_id != claim.batch_id
            or request.workline_id != claim.workline_id
            or request.queue_code != claim.queue_code
            or request.candidate_digest != claim.candidate_digest
            or actual_items != expected_items
        ):
            raise ValueError("E13 frozen request differs from reserved RETURN_QUEUE candidates")

    @staticmethod
    def _empty_reservation() -> WmsConveyorReturnBatchReservation:
        return WmsConveyorReturnBatchReservation(
            created=False,
            claim=None,
            operation=None,
            request=None,
        )


wms_conveyor_return_batch_service = WmsConveyorReturnBatchService()

__all__ = [
    "WmsConveyorReturnBatchClaim",
    "WmsConveyorReturnBatchReservation",
    "WmsConveyorReturnBatchService",
    "WmsConveyorReturnCandidate",
    "wms_conveyor_return_batch_service",
]
