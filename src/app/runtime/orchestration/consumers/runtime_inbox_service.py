"""RuntimeInbox Phase 3 consumer service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.exc import IntegrityError

from src.app.runtime.orchestration.consumers.runtime_inbox_repository import (
    RuntimeInboxRepository,
    runtime_inbox_repository,
)
from src.app.runtime.orchestration.services.idempotency_guard import (
    IdempotencyGuard,
    get_idempotency_operation_spec,
)
from src.app.runtime.orchestration.services.idempotency_guard import (
    idempotency_guard as default_idempotency_guard,
)
from src.app.sys.models.audit_log import OperaStatus
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox


class _AuditService(Protocol):
    async def create_audit_log(
        self,
        db: AsyncSession,
        *,
        method: str,
        title: str,
        path: str,
        args: dict[str, Any] | None = None,
        status: OperaStatus = OperaStatus.SUCCESS,
        code: str = "200",
        msg: str | None = None,
        cost_time: float = 0.0,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class RuntimeInboxAcceptResult:
    """RuntimeInbox ACK 接收结果。"""

    record: RuntimeInbox
    created: bool


@dataclass(frozen=True, slots=True)
class RuntimeInboxReplayResult:
    """RuntimeInbox 人工重放结果。"""

    source_record: RuntimeInbox
    replay_record: RuntimeInbox
    audit_event: dict[str, str | None]


class RuntimeInboxConflict(Exception):
    """同 source event 不同 payload_hash，必须 409 并审计。"""

    status_code = 409

    def __init__(
        self,
        *,
        provider_code: str,
        event_type: str,
        source_event_id: str,
        existing_payload_hash: str | None,
        incoming_payload_hash: str | None,
    ) -> None:
        super().__init__(
            f"runtime inbox conflict: provider={provider_code} event={event_type} source_event_id={source_event_id}"
        )
        self.provider_code = provider_code
        self.event_type = event_type
        self.source_event_id = source_event_id
        self.existing_payload_hash = existing_payload_hash
        self.incoming_payload_hash = incoming_payload_hash

    def to_audit_event(self) -> dict[str, str | None]:
        """转换为稳定安全审计 payload。"""

        return {
            "event_type": "RUNTIME_INBOX_PAYLOAD_CONFLICT",
            "provider_code": self.provider_code,
            "operation_kind": "callback",
            "source_event_id": self.source_event_id,
            "callback_type": self.event_type,
            "existing_payload_hash": self.existing_payload_hash,
            "incoming_payload_hash": self.incoming_payload_hash,
        }


class RuntimeInboxService:
    """RuntimeInbox ACK-before-processing 与人工重放服务。"""

    def __init__(
        self,
        repository: RuntimeInboxRepository = runtime_inbox_repository,
        audit_service: _AuditService | None = None,
        idempotency_guard: IdempotencyGuard = default_idempotency_guard,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.idempotency_guard = idempotency_guard

    async def accept_received(
        self,
        db: AsyncSession,
        *,
        provider_code: str,
        event_type: str,
        source_event_id: str | None,
        payload_hash: str | None,
        execution_session_id: int | None = None,
        correlation_id: str | None = None,
        max_retries: int = 5,
        now_ms: int | None = None,
    ) -> RuntimeInboxAcceptResult:
        """持久化入站消息并返回 ACK 语义结果。

        有 source_event_id 时按 provider_code + event_type + source_event_id
        做幂等；同 hash 返回既有记录，不同 hash 409。
        """

        record_data = {
            "execution_session_id": execution_session_id,
            "correlation_id": correlation_id,
            "provider_code": provider_code,
            "event_type": event_type,
            "source_event_id": source_event_id,
            "payload_hash": payload_hash,
            "status": "RECEIVED",
            "attempt_count": 0,
            "max_retries": max_retries,
        }

        if source_event_id:
            existing = await self.repository.get_by_source_event_identity(
                db,
                provider_code=provider_code,
                event_type=event_type,
                source_event_id=source_event_id,
            )
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    raise RuntimeInboxConflict(
                        provider_code=provider_code,
                        event_type=event_type,
                        source_event_id=source_event_id,
                        existing_payload_hash=existing.payload_hash,
                        incoming_payload_hash=payload_hash,
                    )
                await self._claim_device_event_idempotency_if_needed(
                    db,
                    provider_code=provider_code,
                    event_type=event_type,
                    source_event_id=source_event_id,
                    payload_hash=payload_hash,
                    correlation_id=correlation_id,
                    now_ms=now_ms,
                )
                return RuntimeInboxAcceptResult(record=existing, created=False)

            try:
                async with db.begin_nested():
                    await self._claim_device_event_idempotency_if_needed(
                        db,
                        provider_code=provider_code,
                        event_type=event_type,
                        source_event_id=source_event_id,
                        payload_hash=payload_hash,
                        correlation_id=correlation_id,
                        now_ms=now_ms,
                    )
                    record = await self.repository.add_received(db, record_data)
            except IntegrityError:
                existing = await self.repository.get_by_source_event_identity(
                    db,
                    provider_code=provider_code,
                    event_type=event_type,
                    source_event_id=source_event_id,
                )
                if existing is None:
                    raise
                if existing.payload_hash != payload_hash:
                    raise RuntimeInboxConflict(
                        provider_code=provider_code,
                        event_type=event_type,
                        source_event_id=source_event_id,
                        existing_payload_hash=existing.payload_hash,
                        incoming_payload_hash=payload_hash,
                    ) from None
                await self._claim_device_event_idempotency_if_needed(
                    db,
                    provider_code=provider_code,
                    event_type=event_type,
                    source_event_id=source_event_id,
                    payload_hash=payload_hash,
                    correlation_id=correlation_id,
                    now_ms=now_ms,
                )
                return RuntimeInboxAcceptResult(record=existing, created=False)
        else:
            record = await self.repository.add_received(db, record_data)
        return RuntimeInboxAcceptResult(record=record, created=True)

    async def replay_from_dead_letter(
        self,
        db: AsyncSession,
        *,
        source_inbox_id: int,
        actor: str,
        reason: str,
        replay_source_event_id: str | None = None,
    ) -> RuntimeInboxReplayResult:
        """从 DEAD_LETTER 新建重放记录；原记录保持终态。"""

        normalized_actor = actor.strip()
        normalized_reason = reason.strip()
        if not normalized_actor:
            raise ValueError("actor 不能为空")
        if not normalized_reason:
            raise ValueError("reason 不能为空")

        source = await self.repository.get_by_id_for_update(db, source_inbox_id)
        if source is None:
            raise ValueError(f"RuntimeInbox 不存在: {source_inbox_id}")
        if source.status != "DEAD_LETTER":
            raise ValueError(f"仅 DEAD_LETTER 可重放, 当前 status={source.status}")

        replay = await self.accept_received(
            db,
            provider_code=source.provider_code,
            event_type=source.event_type,
            source_event_id=replay_source_event_id,
            payload_hash=source.payload_hash,
            execution_session_id=source.execution_session_id,
            correlation_id=source.correlation_id,
            max_retries=source.max_retries,
        )
        audit_event = {
            "event_type": "RUNTIME_INBOX_MANUAL_REPLAY",
            "source_inbox_id": str(source.id) if source.id is not None else None,
            "replay_inbox_id": str(replay.record.id) if replay.record.id is not None else None,
            "provider_code": source.provider_code,
            "callback_type": source.event_type,
            "source_event_id": source.source_event_id,
            "replay_source_event_id": replay.record.source_event_id,
            "payload_hash": source.payload_hash,
            "actor": normalized_actor,
            "reason": normalized_reason,
        }
        await self._write_replay_audit(db, audit_event)
        return RuntimeInboxReplayResult(source_record=source, replay_record=replay.record, audit_event=audit_event)

    async def _claim_device_event_idempotency_if_needed(
        self,
        db: AsyncSession,
        *,
        provider_code: str,
        event_type: str,
        source_event_id: str | None,
        payload_hash: str | None,
        correlation_id: str | None,
        now_ms: int | None,
    ) -> None:
        """device_event 入站消息写入 RuntimeInbox 时，同步 claim 跨域 IdempotencyKey。"""

        if not source_event_id or not payload_hash or not correlation_id:
            return
        spec = get_idempotency_operation_spec(event_type)
        if spec.operation_kind != "device_event":
            return

        await self.idempotency_guard.claim_or_match(
            db,
            provider_code=provider_code,
            operation_kind=spec.operation_kind,
            idempotency_key=source_event_id,
            request_hash=payload_hash,
            execution_correlation_id=correlation_id,
            now_ms=now_ms if now_ms is not None else int(timezone.now_utc().timestamp() * 1000),
            business_owner_key=f"device_event:{source_event_id}",
        )

    async def _write_replay_audit(self, db: AsyncSession, audit_event: dict[str, str | None]) -> None:
        """写入人工重放审计；未注入审计服务时只返回 audit_event。"""

        service = self.audit_service
        if service is None:
            from src.app.sys.services import audit_log_service

            service = audit_log_service

        await service.create_audit_log(
            db,
            method="POST",
            title="RuntimeInbox 人工重放",
            path=f"/runtime/inbox/{audit_event['source_inbox_id']}/replay",
            args={
                "model": "RuntimeInbox",
                "operation": "manual_replay",
                "record_id": audit_event["source_inbox_id"],
                **audit_event,
            },
            status=OperaStatus.SUCCESS,
            code="200",
            msg="RuntimeInbox dead-letter replay created",
        )


runtime_inbox_service = RuntimeInboxService()


__all__ = [
    "RuntimeInboxAcceptResult",
    "RuntimeInboxConflict",
    "RuntimeInboxReplayResult",
    "RuntimeInboxService",
    "runtime_inbox_service",
]
