"""WmsConfirmation 应用服务。"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.execution.models.inbound_evidence import (
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
)
from src.app.execution.models.wms_confirmation import WmsConfirmation, WmsConfirmationStatus
from src.app.execution.repositories.material_execution_repository import material_execution_repository
from src.app.execution.repositories.wms_confirmation_repository import wms_confirmation_repository
from src.app.execution.services.inbound_evidence_service import (
    InboundEvidenceConflictResult,
    InboundEvidenceService,
)
from src.core.uuid7 import is_uuid7, new_uuid7
from src.utils.canonical_json import canonical_json_bytes
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.execution.models.material_execution import MaterialExecution
    from src.core.task_queue_gateway import TaskQueueGateway

logger = logging.getLogger(__name__)

WMS_CONFIRMATION_DISPATCH_WINDOW = timedelta(seconds=30)


class WmsConfirmationIdentityConflictError(ValueError):
    """同一 WMS 身份出现不同请求。"""


class WmsConfirmationResponseConflictError(ValueError):
    """已完成确认收到冲突响应。"""


class WmsConfirmationRepositoryPort(Protocol):
    async def lock_identity(self, db: object, operation: str, operation_id: str) -> None: ...

    async def get_by_identity_for_update(
        self,
        db: object,
        operation: str,
        operation_id: str,
    ) -> WmsConfirmation | None: ...

    async def add(self, db: object, confirmation: WmsConfirmation) -> WmsConfirmation: ...

    async def claim_eligible(
        self,
        db: object,
        *,
        now: datetime,
        claim_token: str,
        claim_expires_at: datetime,
        limit: int,
    ) -> list[WmsConfirmation]: ...

    async def get_claimed_for_update(
        self,
        db: object,
        confirmation_id: int,
        claim_token: str,
    ) -> WmsConfirmation | None: ...

    async def flush(self, db: object) -> None: ...


class MaterialExecutionEpochRepositoryPort(Protocol):
    async def get_by_id(self, db: object, execution_id: int) -> MaterialExecution | None: ...


class PickingTaskConfirmationOwnerPort(Protocol):
    async def validate_prepare_response_owner(
        self,
        db: object,
        *,
        picking_task_id: int,
        operation: str,
    ) -> bool: ...


class WmsConfirmationDispatchResultPort(Protocol):
    code: object
    normalized_response: dict[str, Any] | None
    response_result: str | None
    retry_after_ms: int | None


class WmsConfirmationAdapterPort(Protocol):
    async def dispatch(
        self,
        *,
        operation: str,
        operation_id: str,
        request_payload: dict[str, Any],
        request_digest: str,
    ) -> WmsConfirmationDispatchResultPort: ...


@dataclass(frozen=True, slots=True)
class WmsConfirmationFollowUp:
    operation: str
    operation_id: str
    request_payload: dict[str, object]
    next_attempt_at: datetime


class WmsConfirmationFollowUpPlanner(Protocol):
    async def plan(
        self,
        db: object,
        confirmation: WmsConfirmation,
        *,
        response_result: str,
        retry_after_ms: int,
        received_at: datetime,
    ) -> WmsConfirmationFollowUp | None: ...


@dataclass(frozen=True, slots=True)
class WmsConfirmationAcceptance:
    confirmation: WmsConfirmation
    duplicate: bool


@dataclass(frozen=True, slots=True)
class WmsConfirmationIdentityConflictResult:
    confirmation: WmsConfirmation
    identity: str

    def to_exception(self) -> WmsConfirmationIdentityConflictError:
        return WmsConfirmationIdentityConflictError(self.identity)


@dataclass(frozen=True, slots=True)
class WmsConfirmationResponseConflictResult:
    confirmation: WmsConfirmation
    identity: str

    def to_exception(self) -> WmsConfirmationResponseConflictError:
        return WmsConfirmationResponseConflictError(self.identity)


def _immutable_request(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    encoded = canonical_json_bytes(payload)
    normalized = cast("object", json.loads(encoded))
    if not isinstance(normalized, dict):
        raise TypeError("request_payload 必须是 JSON object")
    return cast("dict[str, Any]", normalized), hashlib.sha256(encoded).hexdigest()


class WmsConfirmationLifecycleService:
    """持有 WMS 确认身份、不可变请求和可靠状态迁移。"""

    def __init__(
        self,
        repository: WmsConfirmationRepositoryPort | None = None,
        *,
        execution_repository: MaterialExecutionEpochRepositoryPort | None = None,
    ) -> None:
        self._repository: WmsConfirmationRepositoryPort = repository or wms_confirmation_repository
        self._executions = execution_repository or cast(
            "MaterialExecutionEpochRepositoryPort",
            material_execution_repository,
        )

    async def create_or_get(
        self,
        db: object,
        *,
        operation: str,
        operation_id: str,
        material_execution_id: int | None = None,
        bin_execution_id: int | None = None,
        picking_task_id: int | None = None,
        request_payload: dict[str, Any],
        deadline_at: datetime,
        created_at: datetime,
    ) -> WmsConfirmationAcceptance | WmsConfirmationIdentityConflictResult:
        owners = (material_execution_id, bin_execution_id, picking_task_id)
        if sum(owner is not None for owner in owners) != 1:
            raise ValueError("WmsConfirmation 必须恰好一个 owner")
        if any(
            owner is not None and (not isinstance(owner, int) or isinstance(owner, bool) or owner <= 0)
            for owner in owners
        ):
            raise ValueError("WmsConfirmation owner 必须是正整数")
        payload, digest = _immutable_request(request_payload)
        await self._repository.lock_identity(db, operation, operation_id)
        existing = await self._repository.get_by_identity_for_update(db, operation, operation_id)
        if existing is not None:
            if (
                existing.request_digest != digest
                or existing.material_execution_id != material_execution_id
                or existing.bin_execution_id != bin_execution_id
                or existing.picking_task_id != picking_task_id
                or existing.deadline_at != deadline_at
            ):
                _ = await self.mark_reconciling(db, existing, changed_at=created_at)
                return WmsConfirmationIdentityConflictResult(existing, f"{operation}:{operation_id}")
            return WmsConfirmationAcceptance(existing, duplicate=True)
        confirmation = await self._repository.add(
            db,
            WmsConfirmation(
                operation=operation,
                operation_id=operation_id,
                material_execution_id=material_execution_id,
                bin_execution_id=bin_execution_id,
                picking_task_id=picking_task_id,
                request_digest=digest,
                request_payload=payload,
                deadline_at=deadline_at,
                created_at=created_at,
            ),
        )
        return WmsConfirmationAcceptance(confirmation, duplicate=False)

    async def mark_dispatching(
        self,
        db: object,
        confirmation: WmsConfirmation,
        *,
        claim_token: str,
        claimed_at: datetime,
        claim_expires_at: datetime | None = None,
    ) -> WmsConfirmation:
        if confirmation.status != WmsConfirmationStatus.PENDING:
            raise ValueError("只有 PENDING WmsConfirmation 可进入 DISPATCHING")
        if claimed_at >= confirmation.deadline_at:
            raise ValueError("WmsConfirmation 已超过 deadline")
        confirmation.status = WmsConfirmationStatus.DISPATCHING
        confirmation.claim_token = claim_token
        confirmation.claimed_at = claimed_at
        confirmation.claim_expires_at = claim_expires_at
        confirmation.last_dispatch_at = claimed_at
        confirmation.attempt_count += 1
        await self._repository.flush(db)
        return confirmation

    async def record_delivery_unknown(
        self,
        db: object,
        confirmation: WmsConfirmation,
        *,
        retry_eligible: bool,
        next_attempt_at: datetime | None,
        changed_at: datetime,
    ) -> WmsConfirmation:
        if confirmation.status != WmsConfirmationStatus.DISPATCHING:
            raise ValueError("只有 DISPATCHING WmsConfirmation 可记录 delivery unknown")
        if retry_eligible and next_attempt_at is None:
            raise ValueError("可安全重试时必须给出 next_attempt_at")
        if not retry_eligible and next_attempt_at is not None:
            raise ValueError("不可重试时不得保留 next_attempt_at")
        confirmation.status = WmsConfirmationStatus.PENDING if retry_eligible else WmsConfirmationStatus.RECONCILING
        confirmation.retry_eligible = retry_eligible
        confirmation.next_attempt_at = next_attempt_at
        confirmation.claim_token = None
        confirmation.claimed_at = None
        confirmation.claim_expires_at = None
        confirmation.updated_at = changed_at
        await self._repository.flush(db)
        return confirmation

    async def complete(
        self,
        db: object,
        confirmation: WmsConfirmation,
        *,
        response_evidence_id: int,
        response_result: str,
        completed_at: datetime,
    ) -> WmsConfirmation | WmsConfirmationResponseConflictResult:
        if response_evidence_id <= 0 or not response_result.strip():
            raise ValueError("确定 WMS 响应必须引用 evidence 和类型化结果")
        if confirmation.status == WmsConfirmationStatus.COMPLETED:
            if (
                confirmation.response_evidence_id == response_evidence_id
                and confirmation.response_result == response_result
            ):
                return confirmation
            confirmation.status = WmsConfirmationStatus.RECONCILING
            await self._repository.flush(db)
            return WmsConfirmationResponseConflictResult(
                confirmation,
                f"{confirmation.operation}:{confirmation.operation_id}",
            )
        confirmation.status = WmsConfirmationStatus.COMPLETED
        confirmation.response_evidence_id = response_evidence_id
        confirmation.response_result = response_result
        confirmation.completed_at = completed_at
        confirmation.retry_eligible = False
        confirmation.next_attempt_at = None
        confirmation.claim_token = None
        confirmation.claimed_at = None
        confirmation.claim_expires_at = None
        await self._repository.flush(db)
        return confirmation

    async def mark_reconciling(
        self,
        db: object,
        confirmation: WmsConfirmation,
        *,
        changed_at: datetime,
    ) -> WmsConfirmation:
        confirmation.status = WmsConfirmationStatus.RECONCILING
        confirmation.retry_eligible = False
        confirmation.next_attempt_at = None
        confirmation.claim_token = None
        confirmation.claimed_at = None
        confirmation.claim_expires_at = None
        confirmation.updated_at = changed_at
        await self._repository.flush(db)
        return confirmation


class WmsConfirmationService(WmsConfirmationLifecycleService):
    """短事务 claim、无锁 HTTP 和独立短事务结果回写 dispatcher。"""

    def __init__(
        self,
        repository: WmsConfirmationRepositoryPort | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        adapter: WmsConfirmationAdapterPort | None = None,
        evidence_service: InboundEvidenceService | None = None,
        execution_repository: MaterialExecutionEpochRepositoryPort | None = None,
        picking_task_owner: PickingTaskConfirmationOwnerPort | None = None,
        task_queue_gateway: TaskQueueGateway | None = None,
        follow_up_planner: WmsConfirmationFollowUpPlanner | None = None,
    ) -> None:
        super().__init__(repository, execution_repository=execution_repository)
        self._sessions = session_factory
        self._adapter = adapter
        self._evidence = evidence_service or InboundEvidenceService()
        self._picking_task_owner = picking_task_owner
        self._task_queue = task_queue_gateway
        self._follow_up_planner = follow_up_planner

    async def dispatch_batch(self, *, limit: int = 100, now: datetime | None = None) -> int:
        """短事务 claim 后逐条派发；单次最多处理 100 个可靠确认。"""

        if self._sessions is None or self._adapter is None:
            raise RuntimeError("WmsConfirmation 派发尚未完成运行时装配")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit 必须是正整数")
        batch_limit = min(limit, 100)
        claimed_at = now if now is not None else timezone.now_for_db()
        claim_token = new_uuid7()
        async with self._sessions.begin() as db:
            claimed = await self._repository.claim_eligible(
                db,
                now=claimed_at,
                claim_token=claim_token,
                claim_expires_at=claimed_at + timedelta(minutes=1),
                limit=batch_limit,
            )
        for claimed_confirmation in claimed:
            await self._dispatch_claimed(
                confirmation_id=_required_id(claimed_confirmation),
                claim_token=claim_token,
                now=now,
            )
        return len(claimed)

    async def _dispatch_claimed(  # noqa: PLR0911,PLR0912 - 每个持久状态分支保留明确的 fail-closed 终点。
        self,
        *,
        confirmation_id: int,
        claim_token: str,
        now: datetime | None,
    ) -> None:
        sessions = self._sessions
        adapter = self._adapter
        if sessions is None or adapter is None:
            raise RuntimeError("WmsConfirmation 派发尚未完成运行时装配")
        checked_at = now if now is not None else timezone.now_for_db()
        async with sessions.begin() as db:
            confirmation = await self._repository.get_claimed_for_update(db, confirmation_id, claim_token)
            if confirmation is None:
                return
            if checked_at >= confirmation.deadline_at:
                _ = await self.mark_reconciling(db, confirmation, changed_at=checked_at)
                return
            if confirmation.next_attempt_at is not None and checked_at < confirmation.next_attempt_at:
                _ = await self.record_delivery_unknown(
                    db,
                    confirmation,
                    retry_eligible=True,
                    next_attempt_at=confirmation.next_attempt_at,
                    changed_at=checked_at,
                )
                return
            owner_count = sum(
                owner is not None
                for owner in (
                    confirmation.material_execution_id,
                    confirmation.bin_execution_id,
                    confirmation.picking_task_id,
                )
            )
            if owner_count != 1 or confirmation.bin_execution_id is not None:
                _ = await self.mark_reconciling(db, confirmation, changed_at=checked_at)
                return
            if confirmation.picking_task_id is not None:
                owner = self._picking_task_owner
                if owner is None or not await owner.validate_prepare_response_owner(
                    db,
                    picking_task_id=confirmation.picking_task_id,
                    operation=confirmation.operation,
                ):
                    _ = await self.mark_reconciling(db, confirmation, changed_at=checked_at)
                    return
            operation = confirmation.operation
            operation_id = confirmation.operation_id
            request_payload = dict(confirmation.request_payload)
            request_digest = confirmation.request_digest
        result = await adapter.dispatch(
            operation=operation,
            operation_id=operation_id,
            request_payload=request_payload,
            request_digest=request_digest,
        )
        changed_at = now if now is not None else timezone.now_for_db()
        async with self._execution_wake_transaction(sessions) as (db, wake_execution):
            confirmation = await self._repository.get_claimed_for_update(db, confirmation_id, claim_token)
            if confirmation is None:
                return
            code = getattr(result.code, "value", result.code)
            if code in {"DETERMINATE", "RECONCILING"} and result.normalized_response is not None:
                owner_count = sum(
                    owner is not None
                    for owner in (
                        confirmation.material_execution_id,
                        confirmation.bin_execution_id,
                        confirmation.picking_task_id,
                    )
                )
                if owner_count != 1 or confirmation.bin_execution_id is not None:
                    _ = await self.mark_reconciling(db, confirmation, changed_at=changed_at)
                    return
                line_run_epoch_id: int | None = None
                material_execution_id: int | None = None
                wake_material_execution = False
                if confirmation.material_execution_id is not None:
                    material_execution_id = confirmation.material_execution_id
                    execution = await self._executions.get_by_id(db, material_execution_id)
                    if execution is None:
                        raise LookupError("MaterialExecution 不存在")
                    line_run_epoch_id = execution.line_run_epoch_id
                    if (
                        not isinstance(line_run_epoch_id, int)
                        or isinstance(line_run_epoch_id, bool)
                        or line_run_epoch_id <= 0
                    ):
                        raise ValueError("MaterialExecution 缺少有效 line_run_epoch_id")
                    wake_material_execution = True
                else:
                    picking_task_id = confirmation.picking_task_id
                    owner = self._picking_task_owner
                    if (
                        not isinstance(picking_task_id, int)
                        or isinstance(picking_task_id, bool)
                        or picking_task_id <= 0
                        or owner is None
                        or not await owner.validate_prepare_response_owner(
                            db,
                            picking_task_id=picking_task_id,
                            operation=operation,
                        )
                    ):
                        _ = await self.mark_reconciling(db, confirmation, changed_at=changed_at)
                        return
                evidence_result = await self._evidence.accept(
                    db,
                    kind=InboundEvidenceKind.WMS_RESULT,
                    source_identity=f"{operation}:{operation_id}",
                    normalized_payload=result.normalized_response,
                    received_at=changed_at,
                    line_run_epoch_id=line_run_epoch_id,
                    material_execution_id=material_execution_id,
                    contract_key=operation,
                    contract_version="1.0",
                    operation=operation,
                    operation_id=operation_id,
                    apply_status=InboundEvidenceApplyStatus.APPLIED,
                )
                if isinstance(evidence_result, InboundEvidenceConflictResult):
                    _ = await self.mark_reconciling(db, confirmation, changed_at=changed_at)
                    return
                wake_execution[0] = wake_material_execution
                if code == "DETERMINATE":
                    if evidence_result.evidence.id is None or result.response_result is None:
                        _ = await self.mark_reconciling(db, confirmation, changed_at=changed_at)
                        return
                    completed = await self.complete(
                        db,
                        confirmation,
                        response_evidence_id=evidence_result.evidence.id,
                        response_result=result.response_result,
                        completed_at=changed_at,
                    )
                    if isinstance(completed, WmsConfirmationResponseConflictResult):
                        return
                    if result.retry_after_ms is not None:
                        if material_execution_id is None:
                            _ = await self.mark_reconciling(db, confirmation, changed_at=changed_at)
                            return
                        await self._create_follow_up(
                            db,
                            confirmation,
                            response_result=result.response_result,
                            retry_after_ms=result.retry_after_ms,
                            received_at=changed_at,
                        )
                    return
            if code in {"RETRY", "NOT_SENT", "DELIVERY_UNKNOWN"}:
                retry_delay_ms = result.retry_after_ms if result.retry_after_ms is not None else 1000
                next_attempt_at = changed_at + timedelta(milliseconds=retry_delay_ms)
                if next_attempt_at < confirmation.deadline_at:
                    _ = await self.record_delivery_unknown(
                        db,
                        confirmation,
                        retry_eligible=True,
                        next_attempt_at=next_attempt_at,
                        changed_at=changed_at,
                    )
                    return
            _ = await self.mark_reconciling(db, confirmation, changed_at=changed_at)

    @asynccontextmanager
    async def _execution_wake_transaction(self, sessions: object):
        wake_execution = [False]
        async with sessions.begin() as db:  # type: ignore[attr-defined]
            yield db, wake_execution
        if wake_execution[0]:
            self._enqueue_execution_facts()

    def _enqueue_execution_facts(self) -> None:
        if self._task_queue is None:
            return
        try:
            self._task_queue.enqueue_execution_facts()
        except Exception:
            logger.exception("execution.wms_result_wake_failed", extra={"event": "execution_wake_failed"})

    async def _create_follow_up(
        self,
        db: object,
        confirmation: WmsConfirmation,
        *,
        response_result: str,
        retry_after_ms: int,
        received_at: datetime,
    ) -> None:
        planner = self._follow_up_planner
        try:
            follow_up = (
                await planner.plan(
                    db,
                    confirmation,
                    response_result=response_result,
                    retry_after_ms=retry_after_ms,
                    received_at=received_at,
                )
                if planner is not None
                else None
            )
        except Exception:
            logger.exception(
                "execution.wms_follow_up_planning_failed",
                extra={"operation": confirmation.operation, "operation_id": confirmation.operation_id},
            )
            _ = await self.mark_reconciling(db, confirmation, changed_at=received_at)
            return
        if not self._valid_follow_up(
            confirmation,
            follow_up,
            retry_after_ms=retry_after_ms,
            received_at=received_at,
        ):
            _ = await self.mark_reconciling(db, confirmation, changed_at=received_at)
            return
        if follow_up is None:
            _ = await self.mark_reconciling(db, confirmation, changed_at=received_at)
            return
        created = await self.create_or_get(
            db,
            operation=follow_up.operation,
            operation_id=follow_up.operation_id,
            material_execution_id=confirmation.material_execution_id,
            request_payload=cast("dict[str, Any]", follow_up.request_payload),
            deadline_at=follow_up.next_attempt_at + WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=received_at,
        )
        if isinstance(created, WmsConfirmationIdentityConflictResult):
            _ = await self.mark_reconciling(db, confirmation, changed_at=received_at)
            return
        created.confirmation.next_attempt_at = follow_up.next_attempt_at
        await self._repository.flush(db)

    @staticmethod
    def _valid_follow_up(
        confirmation: WmsConfirmation,
        follow_up: WmsConfirmationFollowUp | None,
        *,
        retry_after_ms: int,
        received_at: datetime,
    ) -> bool:
        try:
            if type(follow_up) is not WmsConfirmationFollowUp or not isinstance(follow_up.request_payload, dict):
                return False
            original_timestamp = confirmation.request_payload.get("timestamp")
            follow_up_timestamp = follow_up.request_payload.get("timestamp")
            expected_timestamp = int(timezone.to_utc(received_at).timestamp() * 1000)
            original_payload, _ = _immutable_request(confirmation.request_payload)
            follow_up_payload, _ = _immutable_request(cast("dict[str, Any]", follow_up.request_payload))
            original_payload.pop("operation_id", None)
            original_payload.pop("timestamp", None)
            follow_up_payload.pop("operation_id", None)
            follow_up_payload.pop("timestamp", None)
            return (
                isinstance(original_timestamp, int)
                and not isinstance(original_timestamp, bool)
                and 0 < original_timestamp < expected_timestamp <= 2**63 - 1
                and follow_up_timestamp == expected_timestamp
                and follow_up.operation == confirmation.operation
                and bool(follow_up.operation_id.strip())
                and len(follow_up.operation_id) <= 160
                and is_uuid7(follow_up.operation_id)
                and follow_up.operation_id != confirmation.operation_id
                and follow_up.request_payload.get("operation") == follow_up.operation
                and follow_up.request_payload.get("operation_id") == follow_up.operation_id
                and follow_up_payload == original_payload
                and follow_up.next_attempt_at == received_at + timedelta(milliseconds=retry_after_ms)
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False


def _required_id(confirmation: WmsConfirmation) -> int:
    if confirmation.id is None:
        raise RuntimeError("已 claim 的 WmsConfirmation 缺少主键")
    return confirmation.id


wms_confirmation_service = WmsConfirmationService()

__all__ = [
    "WMS_CONFIRMATION_DISPATCH_WINDOW",
    "WmsConfirmationAcceptance",
    "WmsConfirmationFollowUp",
    "WmsConfirmationFollowUpPlanner",
    "WmsConfirmationIdentityConflictError",
    "WmsConfirmationIdentityConflictResult",
    "WmsConfirmationLifecycleService",
    "WmsConfirmationResponseConflictError",
    "WmsConfirmationResponseConflictResult",
    "WmsConfirmationService",
    "wms_confirmation_service",
]
