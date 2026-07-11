"""RuntimeInbox consumer service."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
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


def _fit_runtime_identity(raw_value: str, *, max_length: int) -> str:
    """将可审计 identity 稳定压缩到数据库字段上限。"""

    if len(raw_value) <= max_length:
        return raw_value
    digest = sha256(raw_value.encode("utf-8")).hexdigest()[:16]
    return f"{raw_value[: max_length - len(digest) - 1]}:{digest}"


def _runtime_claim_bucket_key(
    *,
    session_id: int | None = None,
    execution_session_id: int | None = None,
    device_id: int | None = None,
    correlation_id: str | None = None,
    workline_id: int | None = None,
    command_id: int | None = None,
    provider_code: str,
    event_type: str,
    source_event_id: str | None,
) -> str:
    """按稳定业务身份生成 RuntimeInbox FIFO 桶键。"""

    candidates = (
        ("session", session_id if session_id is not None else execution_session_id),
        ("device", device_id),
        ("correlation", correlation_id),
        ("workline", workline_id),
        ("command", command_id),
    )
    for prefix, value in candidates:
        if value is not None and str(value).strip():
            return _fit_runtime_identity(f"{prefix}:{value}", max_length=120)

    fallback = f"source:{provider_code}:{event_type}:{source_event_id or 'anonymous'}"
    return _fit_runtime_identity(fallback, max_length=120)


def _received_at_ms(now_ms: int | None = None) -> int:
    """返回 RuntimeInbox 使用的 Unix 毫秒接收时间。"""

    return now_ms if now_ms is not None else int(timezone.now_utc().timestamp() * 1000)


def _format_runtime_temporal(value: object | None) -> str:
    """将 timeout identity/payload 的时间值归一为稳定字符串。"""

    if value is None:
        return "unknown"
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


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
            "claim_bucket_key": _runtime_claim_bucket_key(
                execution_session_id=execution_session_id,
                correlation_id=correlation_id,
                provider_code=provider_code,
                event_type=event_type,
                source_event_id=source_event_id,
            ),
            "received_at": _received_at_ms(now_ms),
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

    # ============================================================
    # Internal event acceptors (Task 7c-a) — device event / internal
    # event / command result, all writing RuntimeInbox directly and
    # skipping source_event_id idempotency.
    # ============================================================

    @staticmethod
    def _derive_provider_code_for_device(device_code: str) -> str:
        """从 device_code 前缀派生 provider_code (ARM_01 -> ARM, OVEN_01 -> OVEN)."""

        if not isinstance(device_code, str) or not device_code:
            return "ECS"
        prefix = device_code.split("_", 1)[0].strip().upper()
        return prefix or "ECS"

    async def _resolve_correlation_id_by_trace(
        self,
        db: AsyncSession,
        *,
        trace_id: str | None,
    ) -> str | None:
        """按 trace_id 查 ExecutionCorrelation, 命中返回 correlation_id; 未命中返回 None。"""

        if not trace_id:
            return None
        from sqlalchemy import select

        from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation

        correlation = (
            await db.execute(select(ExecutionCorrelation).where(ExecutionCorrelation.trace_id == trace_id))
        ).scalar_one_or_none()
        return correlation.correlation_id if correlation is not None else None

    async def accept_device_event(
        self,
        db: AsyncSession,
        *,
        device_code: str,
        event_type: str,
        payload_json: dict[str, Any],
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        workline_id: int | None = None,
        device_id: int | None = None,
        command_id: int | None = None,
        auto_commit: bool = False,
    ) -> RuntimeInboxAcceptResult:
        """接收 device event 写入 RuntimeInbox (kind=DEVICE_EVENT).

        区别于 accept_received:
        - 不做 source_event_id 幂等检查 (内部事件, source_event_id 可能为空或可重复)。
        - provider_code 从 device_code 前缀派生 (ARM_01 -> ARM), 默认 "ECS"。
        - execution_session_id 留空 (Task 5 processor 在后续解析时填充)。
        - correlation_id 通过 trace_id 反查 ExecutionCorrelation, 查不到则回退 trace_id。
        """

        if not isinstance(event_type, str) or not event_type:
            raise ValueError("device event requires event_type")
        if not isinstance(payload_json, dict):
            raise TypeError("device event payload_json must be a dict")

        provider_code = self._derive_provider_code_for_device(device_code)
        source_event_id = event_id
        correlation_id = await self._resolve_correlation_id_by_trace(db, trace_id=trace_id)
        if correlation_id is None and trace_id:
            correlation_id = trace_id

        record_data: dict[str, Any] = {
            "kind": "DEVICE_EVENT",
            "provider_code": provider_code,
            "event_type": event_type,
            "source_event_id": source_event_id,
            "payload_hash": None,
            "status": "RECEIVED",
            "attempt_count": 0,
            "max_retries": 5,
            "workline_id": workline_id,
            "device_id": device_id,
            "command_id": command_id,
            "trace_id": trace_id,
            "event_id": event_id,
            "causation_id": causation_id,
            "correlation_id": correlation_id,
            "payload_json": payload_json,
            "claim_bucket_key": _runtime_claim_bucket_key(
                device_id=device_id,
                correlation_id=correlation_id,
                workline_id=workline_id,
                command_id=command_id,
                provider_code=provider_code,
                event_type=event_type,
                source_event_id=source_event_id,
            ),
            "received_at": _received_at_ms(),
        }

        record = await self.repository.add_received(db, record_data)
        if auto_commit:
            _ = await db.commit()
        return RuntimeInboxAcceptResult(record=record, created=True)

    async def accept_internal_event(
        self,
        db: AsyncSession,
        *,
        event_type: str,
        payload_json: dict[str, Any],
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        workline_id: int | None = None,
        execution_session_id: int | None = None,
        correlation_id: str | None = None,
        auto_commit: bool = False,
    ) -> RuntimeInboxAcceptResult:
        """接收内部事件写入 RuntimeInbox (kind=INTERNAL_EVENT).

        - provider_code 固定 "RUNTIME"。
        - 不做 source_event_id 幂等检查 (内部事件, source_event_id 可缺失)。
        - correlation_id 缺省时按 trace_id 反查, 再回退 trace_id。
        """

        if not isinstance(event_type, str) or not event_type:
            raise ValueError("internal event requires event_type")
        if not isinstance(payload_json, dict):
            raise TypeError("internal event payload_json must be a dict")

        if correlation_id is None:
            correlation_id = await self._resolve_correlation_id_by_trace(db, trace_id=trace_id)
        if correlation_id is None and trace_id:
            correlation_id = trace_id

        record_data: dict[str, Any] = {
            "kind": "INTERNAL_EVENT",
            "provider_code": "RUNTIME",
            "event_type": event_type,
            "source_event_id": event_id,
            "payload_hash": None,
            "status": "RECEIVED",
            "attempt_count": 0,
            "max_retries": 5,
            "workline_id": workline_id,
            "trace_id": trace_id,
            "event_id": event_id,
            "causation_id": causation_id,
            "execution_session_id": execution_session_id,
            "correlation_id": correlation_id,
            "payload_json": payload_json,
            "claim_bucket_key": _runtime_claim_bucket_key(
                execution_session_id=execution_session_id,
                correlation_id=correlation_id,
                workline_id=workline_id,
                provider_code="RUNTIME",
                event_type=event_type,
                source_event_id=event_id,
            ),
            "received_at": _received_at_ms(),
        }

        record = await self.repository.add_received(db, record_data)
        if auto_commit:
            _ = await db.commit()
        return RuntimeInboxAcceptResult(record=record, created=True)

    async def accept_command_result(
        self,
        db: AsyncSession,
        *,
        command_code: str,
        device_code: str | None = None,
        workline_id: int | None = None,
        device_id: int | None = None,
        command_id: int | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        payload_json: dict[str, Any] | None = None,
        auto_commit: bool = False,
    ) -> RuntimeInboxAcceptResult:
        """接收 command result 写入 RuntimeInbox (kind=COMMAND_RESULT).

        - provider_code: 有 device_code 走 "DEVICE_RESULT", 缺省走 "RUNTIME"。
        - source_event_id: 优先 event_id, 否则按 command_code+event_id 派生稳定 key。
        - 不做 source_event_id 幂等检查 (synthesize 出的 command result 场景)。
        """

        if not isinstance(command_code, str) or not command_code:
            raise ValueError("command result requires command_code")

        provider_code = "DEVICE_RESULT" if device_code else "RUNTIME"
        source_event_id = event_id or f"command-result:{command_code}:{event_id or 'synth'}"

        record_data: dict[str, Any] = {
            "kind": "COMMAND_RESULT",
            "provider_code": provider_code,
            "event_type": "COMMAND_RESULT",
            "source_event_id": source_event_id,
            "payload_hash": None,
            "status": "RECEIVED",
            "attempt_count": 0,
            "max_retries": 5,
            "workline_id": workline_id,
            "device_id": device_id,
            "command_id": command_id,
            "trace_id": trace_id,
            "event_id": event_id,
            "causation_id": causation_id,
            "payload_json": payload_json or {"command_code": command_code, "device_code": device_code},
            "claim_bucket_key": _runtime_claim_bucket_key(
                device_id=device_id,
                workline_id=workline_id,
                command_id=command_id,
                provider_code=provider_code,
                event_type="COMMAND_RESULT",
                source_event_id=source_event_id,
            ),
            "received_at": _received_at_ms(),
        }

        record = await self.repository.add_received(db, record_data)
        if auto_commit:
            _ = await db.commit()
        return RuntimeInboxAcceptResult(record=record, created=True)

    async def accept_timer_timeout(
        self,
        db: AsyncSession,
        *,
        session_id: int,
        execution_session_id: int | None = None,
        workline_id: int,
        deadline_at: object | None = None,
        trace_id: str | None = None,
        wait_token: str | None = None,
        wait_type: str | None = None,
        awaiting_device_command_code: str | None = None,
        command_code: str | None = None,
        device_id: int | None = None,
        device_code: str | None = None,
        command_id: int | None = None,
        command_status: str | None = None,
        ack_received_at: object | None = None,
        now_ms: int | None = None,
        auto_commit: bool = False,
    ) -> RuntimeInboxAcceptResult:
        """幂等接收系统 TIMER_TIMEOUT 并保存 canonical payload。"""

        deadline_key = _format_runtime_temporal(deadline_at)
        wait_key = wait_token or "no-wait-token"
        command_key = awaiting_device_command_code or command_code or "no-command"
        source_event_id = _fit_runtime_identity(
            f"timeout:{session_id}:{deadline_key}:{wait_key}:{command_key}",
            max_length=160,
        )
        existing = await self.repository.get_by_source_event_identity(
            db,
            provider_code="RUNTIME",
            event_type="TIMER_TIMEOUT",
            source_event_id=source_event_id,
        )
        if existing is not None:
            return RuntimeInboxAcceptResult(record=existing, created=False)

        payload_data = {
            "session_id": session_id,
            "workline_id": workline_id,
            "deadline_at": deadline_key,
            "wait_token": wait_token,
            "wait_type": wait_type,
            "awaiting_device_command_code": awaiting_device_command_code,
            "command_code": command_code,
            "device_id": device_id,
            "device_code": device_code,
            "command_status": command_status,
            "ack_received_at": _format_runtime_temporal(ack_received_at),
        }
        record_data: dict[str, Any] = {
            "kind": "TIMER_TIMEOUT",
            "execution_session_id": execution_session_id,
            "workline_id": workline_id,
            "device_id": device_id,
            "command_id": command_id,
            "trace_id": trace_id,
            "provider_code": "RUNTIME",
            "event_type": "TIMER_TIMEOUT",
            "source_event_id": source_event_id,
            "payload_hash": None,
            "payload_json": {"event_type": "TIMER_TIMEOUT", "data": payload_data},
            "status": "RECEIVED",
            "attempt_count": 0,
            "max_retries": 5,
            "claim_bucket_key": _runtime_claim_bucket_key(
                session_id=session_id,
                device_id=device_id,
                workline_id=workline_id,
                command_id=command_id,
                provider_code="RUNTIME",
                event_type="TIMER_TIMEOUT",
                source_event_id=source_event_id,
            ),
            "received_at": _received_at_ms(now_ms),
        }
        try:
            async with db.begin_nested():
                record = await self.repository.add_received(db, record_data)
        except IntegrityError:
            existing = await self.repository.get_by_source_event_identity(
                db,
                provider_code="RUNTIME",
                event_type="TIMER_TIMEOUT",
                source_event_id=source_event_id,
            )
            if existing is None:
                raise
            return RuntimeInboxAcceptResult(record=existing, created=False)

        if auto_commit:
            _ = await db.commit()
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
        """原子回收 stale PROCESSING；耗尽预算时直接 DEAD_LETTER。"""
        return await self.claim_repo.recover_stale_leases(
            db,
            stale_after_seconds=stale_after_seconds,
            limit=limit,
        )

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
        consume_attempt: bool = True,
    ) -> bool:
        """按 retry/attempt 状态机 fenced 写入 FAILED 或 DEAD_LETTER。"""
        from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
        from src.utils.timezone import timezone

        now = timezone.now_for_db()
        now_ms = int(now.timestamp() * 1000) if hasattr(now, "timestamp") else None
        extra: dict[str, Any] = {"last_error_message": error_message}
        if now_ms is not None:
            extra["failed_at"] = now_ms

        attempt_count = 0
        max_retries = 0
        if retryable:
            from sqlalchemy import select

            inbox = (
                await db.execute(select(RuntimeInbox).where(cast("Any", RuntimeInbox).__table__.c.id == inbox_id))
            ).scalar_one_or_none()
            attempt_count = int(getattr(inbox, "attempt_count", 0) or 0)
            max_retries = int(getattr(inbox, "max_retries", 0) or 0)

        effective_attempt_count = max(0, attempt_count - (0 if consume_attempt else 1))
        exhausted = retryable and effective_attempt_count >= max_retries
        target_state = "DEAD_LETTER" if not retryable or exhausted else "FAILED"

        if not consume_attempt:
            # RESOURCE_WAIT 在 claim 时已 +1；fenced 写 FAILED 时原子恢复，且不低于 0。
            extra["attempt_count"] = effective_attempt_count

        if target_state == "FAILED":
            # 指数退避 (10s, 20s, 40s, ... 600s cap)
            delay_seconds = min(600, 10 * (2**effective_attempt_count))
            if now_ms is not None:
                extra["next_retry_at"] = now_ms + delay_seconds * 1000
        else:
            extra["next_retry_at"] = None
        return await self.claim_repo.update_terminal_state(
            db,
            inbox_id=inbox_id,
            lease_token=lease_token,
            target_state=target_state,
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
