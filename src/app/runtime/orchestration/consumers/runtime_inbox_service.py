"""RuntimeInbox consumer service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy.exc import IntegrityError

from src.app.runtime.orchestration.consumers.runtime_inbox_repository import (
    RuntimeInboxRepository,
    runtime_inbox_repository,
)
from src.app.runtime.orchestration.services.idempotency_guard import (
    IdempotencyGuard,
    IdempotencyOperationSpec,
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

        spec = _runtime_inbox_operation_spec(self.event_type)
        return {
            "event_type": "RUNTIME_INBOX_PAYLOAD_CONFLICT",
            "provider_code": self.provider_code,
            "operation_kind": spec.operation_kind,
            "domain": spec.domain,
            "source_event_id": self.source_event_id,
            "callback_type": self.event_type,
            "existing_payload_hash": self.existing_payload_hash,
            "incoming_payload_hash": self.incoming_payload_hash,
        }


def _runtime_inbox_operation_spec(event_type: str) -> IdempotencyOperationSpec:
    """将 callback channel/event_type 归一到 runtime operation_kind 审计矩阵。"""

    normalized = event_type.strip().lower().replace("-", "_")
    aliases = {
        "result": "command_result",
        "device_result": "command_result",
        "event": "event_push",
        "external": "external_callback",
    }
    return get_idempotency_operation_spec(aliases.get(normalized, normalized))


class RuntimeInboxService:
    """RuntimeInbox ACK-before-processing 与人工重放服务。"""

    def __init__(
        self,
        repository: RuntimeInboxRepository = runtime_inbox_repository,
        claim_repo: Any | None = None,
        audit_service: _AuditService | None = None,
        idempotency_guard: IdempotencyGuard = default_idempotency_guard,
    ) -> None:
        self.repository = repository
        # 5 态 claim + write-back 依赖的 claim repository.
        # 缺省 lazy import, 避免循环依赖.
        if claim_repo is None:
            from src.app.runtime.orchestration.repositories.runtime_inbox_claim_repository import (
                runtime_inbox_claim_repository,
            )

            claim_repo = runtime_inbox_claim_repository
        self.claim_repo = claim_repo
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
        spec = _runtime_inbox_operation_spec(event_type)
        if spec.operation_kind != "device_event":
            return

        claimed_now_ms = now_ms if now_ms is not None else int(timezone.now_utc().timestamp() * 1000)
        _ = await self.idempotency_guard.claim_or_match(
            db,
            provider_code=provider_code,
            operation_kind=spec.operation_kind,
            idempotency_key=source_event_id,
            request_hash=payload_hash,
            execution_correlation_id=correlation_id,
            now_ms=claimed_now_ms,
            business_owner_key=f"device_event:{source_event_id}",
        )

    async def _write_replay_audit(self, db: AsyncSession, audit_event: dict[str, str | None]) -> None:
        """写入人工重放审计；未注入审计服务时只返回 audit_event。"""

        service = self.audit_service
        if service is None:
            from src.app.sys.services import audit_log_service

            service = audit_log_service

        _ = await service.create_audit_log(
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

    # ============================================================
    # 5-state claim + write-back (Task 3 主计划 §3)
    # ============================================================

    async def claim_for_processing(
        self,
        db: AsyncSession,
        *,
        limit: int,
        processor_token: str,
        stale_after_seconds: int,
    ) -> list[dict[str, Any]]:
        """原子 claim RECEIVED 行（含 stale PROCESSING 回收）。

        委托 claim_repo.claim_received_with_token。
        底层 SQL 匹配:
        - status='RECEIVED'
        - status='FAILED' AND next_retry_at <= now AND attempt_count < max_retries
        - status='PROCESSING' AND lease_until <= now (stale reclaim)
        """
        return await self.claim_repo.claim_received_with_token(
            db,
            limit=limit,
            processor_token=processor_token,
            stale_after_seconds=stale_after_seconds,
        )

    async def recover_stale_leases(
        self,
        db: AsyncSession,
        *,
        stale_after_seconds: int,
        limit: int,
    ) -> int:
        """回收 stale PROCESSING 行（lease_until < now）为 RECEIVED。

        由 claim_repo.find_stale_processing 列出候选行, 然后
        把 state='RECEIVED', processor_token=None, lease_until=None 重置。
        """
        stale = await self.claim_repo.find_stale_processing(
            db,
            stale_after_seconds=stale_after_seconds,
            limit=limit,
        )
        # 重置为 RECEIVED 状态, 等待下次 claim
        from sqlalchemy import update

        from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox

        cast("Any", RuntimeInbox)
        for row in stale:
            row_id = getattr(row, "id", None)
            if row_id is None:
                continue
            await db.execute(
                update(RuntimeInbox)
                .where(cast("Any", RuntimeInbox).__table__.c.id == row_id)
                .values(
                    status="RECEIVED",
                    processor_token=None,
                    lease_until=None,
                )
            )
        return len(stale)

    async def mark_processed(
        self,
        db: AsyncSession,
        *,
        inbox_id: int,
        lease_token: str,
    ) -> bool:
        """写终态 PROCESSED + processed_at, 匹配 processor_token (fencing)."""
        from src.utils.timezone import timezone

        now = timezone.now_for_db()
        now_ms = int(now.timestamp() * 1000) if hasattr(now, "timestamp") else None
        extra_values: dict[str, Any] | None = {"processed_at": now_ms} if now_ms is not None else None
        return await self.claim_repo.update_terminal_state(
            db,
            inbox_id=inbox_id,
            lease_token=lease_token,
            target_state="PROCESSED",
            extra_values=extra_values,
        )

    async def mark_failed(
        self,
        db: AsyncSession,
        *,
        inbox_id: int,
        lease_token: str,
        error_message: str,
        retryable: bool,
    ) -> bool:
        """写终态 FAILED + failed_at. retryable=True 时设置 next_retry_at."""
        from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
        from src.utils.timezone import timezone

        now = timezone.now_for_db()
        now_ms = int(now.timestamp() * 1000) if hasattr(now, "timestamp") else None
        extra: dict[str, Any] = {"last_error_message": error_message}
        if now_ms is not None:
            extra["failed_at"] = now_ms
        if retryable:
            # 指数退避 (10s, 20s, 40s, ... 600s cap)
            # attempt_count 从当前行读
            from sqlalchemy import select

            inbox = (
                await db.execute(select(RuntimeInbox).where(cast("Any", RuntimeInbox).__table__.c.id == inbox_id))
            ).scalar_one_or_none()
            attempt_count = int(getattr(inbox, "attempt_count", 0) or 0)
            delay_seconds = min(600, 10 * (2**attempt_count))
            if now_ms is not None:
                extra["next_retry_at"] = now_ms + delay_seconds * 1000
        return await self.claim_repo.update_terminal_state(
            db,
            inbox_id=inbox_id,
            lease_token=lease_token,
            target_state="FAILED",
            extra_values=extra,
        )

    async def mark_dead_letter(
        self,
        db: AsyncSession,
        *,
        inbox_id: int,
        lease_token: str,
        error_message: str,
    ) -> bool:
        """写终态 DEAD_LETTER + failed_at。"""
        from src.utils.timezone import timezone

        now = timezone.now_for_db()
        now_ms = int(now.timestamp() * 1000) if hasattr(now, "timestamp") else None
        extra: dict[str, Any] = {"last_error_message": error_message}
        if now_ms is not None:
            extra["failed_at"] = now_ms
        return await self.claim_repo.update_terminal_state(
            db,
            inbox_id=inbox_id,
            lease_token=lease_token,
            target_state="DEAD_LETTER",
            extra_values=extra,
        )


runtime_inbox_service = RuntimeInboxService()


__all__ = [
    "RuntimeInboxAcceptResult",
    "RuntimeInboxConflict",
    "RuntimeInboxReplayResult",
    "RuntimeInboxService",
    "runtime_inbox_service",
]
