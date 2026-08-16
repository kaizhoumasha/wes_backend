"""WmsConfirmation 应用服务。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.execution.models.inbound_evidence import InboundEvidenceKind
from src.app.execution.models.wms_confirmation import WmsConfirmation, WmsConfirmationStatus
from src.app.execution.repositories.wms_confirmation_repository import wms_confirmation_repository
from src.app.execution.services.inbound_evidence_service import (
    InboundEvidenceConflictResult,
    InboundEvidenceService,
)
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    normalized = cast("object", json.loads(encoded))
    if not isinstance(normalized, dict):
        raise TypeError("request_payload 必须是 JSON object")
    return cast("dict[str, Any]", normalized), hashlib.sha256(encoded).hexdigest()


class WmsConfirmationService:
    def __init__(
        self,
        repository: WmsConfirmationRepositoryPort | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        adapter: WmsConfirmationAdapterPort | None = None,
        evidence_service: InboundEvidenceService | None = None,
    ) -> None:
        self._repository: WmsConfirmationRepositoryPort = repository or wms_confirmation_repository
        self._sessions = session_factory
        self._adapter = adapter
        self._evidence = evidence_service or InboundEvidenceService()

    async def create_or_get(
        self,
        db: object,
        *,
        operation: str,
        operation_id: str,
        material_execution_id: int,
        request_payload: dict[str, Any],
        deadline_at: datetime,
        created_at: datetime,
    ) -> WmsConfirmationAcceptance | WmsConfirmationIdentityConflictResult:
        payload, digest = _immutable_request(request_payload)
        await self._repository.lock_identity(db, operation, operation_id)
        existing = await self._repository.get_by_identity_for_update(db, operation, operation_id)
        if existing is not None:
            if (
                existing.request_digest != digest
                or existing.material_execution_id != material_execution_id
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

    async def _dispatch_claimed(
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
        async with sessions.begin() as db:
            confirmation = await self._repository.get_claimed_for_update(db, confirmation_id, claim_token)
            if confirmation is None:
                return
            code = getattr(result.code, "value", result.code)
            if code in {"DETERMINATE", "RECONCILING"} and result.normalized_response is not None:
                evidence_result = await self._evidence.accept(
                    db,
                    kind=InboundEvidenceKind.WMS_RESULT,
                    source_identity=f"{operation}:{operation_id}",
                    normalized_payload=result.normalized_response,
                    received_at=changed_at,
                    material_execution_id=confirmation.material_execution_id,
                    contract_key="rough_sorter_inbound",
                    contract_version="1.0",
                    operation=operation,
                    operation_id=operation_id,
                )
                if isinstance(evidence_result, InboundEvidenceConflictResult):
                    _ = await self.mark_reconciling(db, confirmation, changed_at=changed_at)
                    return
                if code == "DETERMINATE":
                    if evidence_result.evidence.id is None or result.response_result is None:
                        _ = await self.mark_reconciling(db, confirmation, changed_at=changed_at)
                        return
                    _ = await self.complete(
                        db,
                        confirmation,
                        response_evidence_id=evidence_result.evidence.id,
                        response_result=result.response_result,
                        completed_at=changed_at,
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


def _required_id(confirmation: WmsConfirmation) -> int:
    if confirmation.id is None:
        raise RuntimeError("已 claim 的 WmsConfirmation 缺少主键")
    return confirmation.id


wms_confirmation_service = WmsConfirmationService()

__all__ = [
    "WmsConfirmationAcceptance",
    "WmsConfirmationIdentityConflictError",
    "WmsConfirmationIdentityConflictResult",
    "WmsConfirmationResponseConflictError",
    "WmsConfirmationResponseConflictResult",
    "WmsConfirmationService",
    "wms_confirmation_service",
]
