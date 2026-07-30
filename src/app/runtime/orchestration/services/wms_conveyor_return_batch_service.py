"""E13 RETURN_QUEUE FIFO 候选冻结与 preparation claim 服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.repositories.wms_conveyor_return_batch_repository import (
    WmsConveyorReturnBatchRepository,
    WmsConveyorReturnCandidateRow,
    WmsConveyorReturnPreparedRows,
    wms_conveyor_return_batch_repository,
)
from src.app.wms_integration.ports.fulfillment_operations import (
    MOVE_BINS_FROM_CONVEYOR_EXIT,
    ConveyorExitCandidate,
    MoveBinsFromConveyorExitRequest,
    WmsEffectAck,
    frozen_candidate_digest,
    validate_fulfillment_ack,
)
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
        return any(member.member_state != "CANDIDATE" for member in prepared.members) or any(
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
        if any(member.member_state != "CANDIDATE" for member in prepared.members) or any(
            self._member_has_observed_physical_action(prepared, member) for member in prepared.members
        ):
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

    async def project_reconciliation_opened(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
        reason_code: str | None,
        evidence_json: dict[str, object] | None,
    ) -> None:
        """只验证 E13 root 存在；case 由 reducer 持有，不改对象位置或 claim。"""

        if not reason_code or not isinstance(evidence_json, dict):
            raise ValueError("E13 reconciliation projection requires reason and evidence")
        if await self._repository.lock_prepared_batch(db, dispatch_key=dispatch_key) is None:
            raise RuntimeError("E13 prepared batch is missing")

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
            if (
                source is None
                or route is None
                or not (has_active_claim or has_released_claim_after_physical_action)
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
