"""RuntimeInbox 领域服务。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy.exc import IntegrityError

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import (
    RuntimeInboxPayloadTooLarge,
    RuntimeInboxRepository,
    runtime_inbox_repository,
    validate_canonical_payload_size,
)
from src.app.runtime.orchestration.runtime_inbox import PRE_CUTOVER_AUDIT_ONLY
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


@dataclass(frozen=True, slots=True)
class RuntimeInboxReplaySourceValidation:
    """已由持久化 root 事实验证的 replay source。"""

    envelope: dict[str, Any]
    root_source: RuntimeInbox


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


class RuntimeInboxAuditPersistenceFailed(RuntimeError):
    """人工重放审计持久化失败，调用方必须按服务不可用处理。"""

    status_code = 503
    reason_code = "RUNTIME_INBOX_AUDIT_PERSISTENCE_FAILED"

    def __init__(self, *, audit_event_type: str, original_error: Exception) -> None:
        super().__init__(self.reason_code)
        self.audit_event_type = audit_event_type
        self.original_error = original_error


class RuntimeInboxNotFound(Exception):
    """RuntimeInbox 主键不存在。"""

    reason_code = "RUNTIME_INBOX_NOT_FOUND"

    def __init__(self, *, inbox_id: int) -> None:
        super().__init__(f"RuntimeInbox 不存在: {inbox_id}")
        self.inbox_id = inbox_id


class RuntimeInboxReplayNotAllowed(Exception):
    """RuntimeInbox 当前证据不满足人工重放合同。"""

    def __init__(self, *, reason_code: str, detail: str | None = None) -> None:
        message = reason_code if detail is None else f"{reason_code}: {detail}"
        super().__init__(message)
        self.reason_code = reason_code
        self.detail = detail


REPLAYABLE_ORIGINAL_KINDS = frozenset(
    {"COMMAND_RESULT", "DEVICE_EVENT", "EXTERNAL_HTTP", "INTERNAL_EVENT", "TIMER_TIMEOUT"}
)
REPLAY_MAX_RETRIES = 5
_REPLAY_EVIDENCE_FIELDS = (
    "original_provider_code",
    "original_event_type",
    "original_source_event_id",
    "original_payload_hash",
    "original_workline_id",
    "original_device_id",
    "original_command_id",
    "original_workline_session_id",
    "original_execution_session_id",
    "original_correlation_id",
    "original_trace_id",
    "original_event_id",
    "original_causation_id",
)


def validate_replay_envelope(payload: object) -> dict[str, Any]:
    """校验单层 canonical replay envelope，并返回独立副本。"""

    if not isinstance(payload, dict):
        raise RuntimeInboxReplayNotAllowed(reason_code="INVALID_REPLAY_ENVELOPE", detail="payload must be object")
    required = {
        "request_id",
        "actor",
        "reason",
        "immediate_source_inbox_id",
        "root_source_inbox_id",
        "original_kind",
        "original_payload",
        *_REPLAY_EVIDENCE_FIELDS,
    }
    if missing := sorted(required - payload.keys()):
        raise RuntimeInboxReplayNotAllowed(
            reason_code="INVALID_REPLAY_ENVELOPE",
            detail=f"missing fields: {','.join(missing)}",
        )
    original_kind = payload.get("original_kind")
    if original_kind not in REPLAYABLE_ORIGINAL_KINDS:
        raise RuntimeInboxReplayNotAllowed(
            reason_code="INVALID_REPLAY_ENVELOPE",
            detail="original_kind is not replayable",
        )
    if not isinstance(payload.get("original_payload"), dict):
        raise RuntimeInboxReplayNotAllowed(
            reason_code="INVALID_REPLAY_ENVELOPE",
            detail="original_payload must be object",
        )
    for field in ("request_id", "actor", "reason"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeInboxReplayNotAllowed(
                reason_code="INVALID_REPLAY_ENVELOPE",
                detail=f"{field} must be non-empty string",
            )
    for field in ("immediate_source_inbox_id", "root_source_inbox_id"):
        value = payload.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise RuntimeInboxReplayNotAllowed(
                reason_code="INVALID_REPLAY_ENVELOPE",
                detail=f"{field} must be positive integer",
            )
    for field in (
        "original_workline_id",
        "original_device_id",
        "original_command_id",
        "original_workline_session_id",
        "original_execution_session_id",
    ):
        value = payload.get(field)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
            raise RuntimeInboxReplayNotAllowed(
                reason_code="INVALID_REPLAY_ENVELOPE",
                detail=f"{field} must be positive integer or null",
            )
    for field in (
        "original_provider_code",
        "original_event_type",
        "original_source_event_id",
        "original_payload_hash",
    ):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeInboxReplayNotAllowed(
                reason_code="INVALID_REPLAY_ENVELOPE",
                detail=f"{field} must be non-empty string",
            )
    for field in ("original_correlation_id", "original_trace_id", "original_event_id", "original_causation_id"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise RuntimeInboxReplayNotAllowed(
                reason_code="INVALID_REPLAY_ENVELOPE",
                detail=f"{field} must be string or null",
            )
    return dict(payload)


def _original_replay_evidence(source: RuntimeInbox) -> dict[str, Any]:
    return {
        "original_provider_code": source.provider_code,
        "original_event_type": source.event_type,
        "original_source_event_id": source.source_event_id,
        "original_payload_hash": source.payload_hash,
        "original_workline_id": source.workline_id,
        "original_device_id": source.device_id,
        "original_command_id": source.command_id,
        "original_workline_session_id": source.workline_session_id,
        "original_execution_session_id": source.execution_session_id,
        "original_correlation_id": source.correlation_id,
        "original_trace_id": source.trace_id,
        "original_event_id": source.event_id,
        "original_causation_id": source.causation_id,
    }


def _raise_replay_source_integrity_violation(detail: str) -> None:
    raise RuntimeInboxReplayNotAllowed(reason_code="REPLAY_SOURCE_INTEGRITY_VIOLATION", detail=detail)


class RuntimeInboxReplaySourceValidator:
    """创建与消费共用的 replay source 持久化真实性校验器。"""

    def __init__(self, repository: RuntimeInboxRepository = runtime_inbox_repository) -> None:
        self.repository = repository

    async def validate(
        self,
        db: AsyncSession,
        *,
        source: RuntimeInbox,
    ) -> RuntimeInboxReplaySourceValidation:
        envelope = validate_replay_envelope(source.payload_json)
        if not isinstance(source.max_retries, int) or isinstance(source.max_retries, bool) or source.max_retries < 1:
            raise RuntimeInboxReplayNotAllowed(reason_code="INVALID_SOURCE_RETRY_BUDGET")
        if source.payload_hash != _canonical_payload_hash(envelope):
            _raise_replay_source_integrity_violation("immediate source payload hash mismatch")

        root_source_id = cast("int", envelope["root_source_inbox_id"])
        root = await self.repository.get_by_id_for_update(db, root_source_id, populate_existing=True)
        if root is None or root.id == source.id:
            _raise_replay_source_integrity_violation("root source unavailable")
        if root.kind not in REPLAYABLE_ORIGINAL_KINDS or not isinstance(root.payload_json, dict):
            _raise_replay_source_integrity_violation("root source kind is not replayable")
        if not isinstance(root.max_retries, int) or isinstance(root.max_retries, bool) or root.max_retries < 1:
            raise RuntimeInboxReplayNotAllowed(reason_code="INVALID_SOURCE_RETRY_BUDGET")
        if root.payload_hash != _canonical_payload_hash(root.payload_json):
            _raise_replay_source_integrity_violation("root source payload hash mismatch")

        expected_evidence = _original_replay_evidence(root)
        if envelope["original_kind"] != root.kind or envelope["original_payload"] != root.payload_json:
            _raise_replay_source_integrity_violation("root source payload evidence mismatch")
        if any(envelope[field] != expected_evidence[field] for field in _REPLAY_EVIDENCE_FIELDS):
            _raise_replay_source_integrity_violation("root source routing evidence mismatch")

        source_routing = {
            "original_workline_id": source.workline_id,
            "original_device_id": source.device_id,
            "original_command_id": source.command_id,
            "original_workline_session_id": source.workline_session_id,
            "original_execution_session_id": source.execution_session_id,
            "original_correlation_id": source.correlation_id,
            "original_trace_id": source.trace_id,
        }
        if any(source_routing[field] != expected_evidence[field] for field in source_routing):
            _raise_replay_source_integrity_violation("immediate source routing evidence mismatch")
        expected_source_event_id = (
            f"replay:{envelope['immediate_source_inbox_id']}:{cast('str', envelope['request_id']).strip()}"
        )
        if (
            source.provider_code != "RUNTIME"
            or source.event_type != "REPLAY_REQUEST"
            or source.source_event_id != expected_source_event_id
        ):
            _raise_replay_source_integrity_violation("immediate source identity mismatch")
        return RuntimeInboxReplaySourceValidation(envelope=envelope, root_source=root)


class RuntimeInboxCorrelationUnavailable(RuntimeError):
    """显式关联未持久化，调用方应按服务端完整性故障重试。"""

    status_code = 503

    def __init__(self, *, correlation_id: str) -> None:
        super().__init__("runtime inbox correlation is unavailable")
        self.correlation_id = correlation_id


class RuntimeInboxSessionOwnershipConflict(RuntimeInboxConflict):
    """同一入站幂等身份不能跨 WorklineSession 归属复用。"""

    status_code = 409

    def __init__(
        self,
        *,
        provider_code: str,
        event_type: str,
        source_event_id: str,
        existing_workline_session_id: int | None,
        incoming_workline_session_id: int | None,
    ) -> None:
        Exception.__init__(
            self,
            "runtime inbox session ownership conflict: "
            f"provider={provider_code} event={event_type} source_event_id={source_event_id} "
            f"existing_workline_session_id={existing_workline_session_id} "
            f"incoming_workline_session_id={incoming_workline_session_id}",
        )
        self.provider_code = provider_code
        self.event_type = event_type
        self.source_event_id = source_event_id
        self.existing_workline_session_id = existing_workline_session_id
        self.incoming_workline_session_id = incoming_workline_session_id

    def to_audit_event(self) -> dict[str, str | None]:
        """转换为不包含 payload 的稳定归属冲突审计证据。"""

        spec = _runtime_inbox_operation_spec(self.event_type)
        return {
            "event_type": "RUNTIME_INBOX_SESSION_OWNERSHIP_CONFLICT",
            "provider_code": self.provider_code,
            "operation_kind": spec.operation_kind,
            "domain": spec.domain,
            "source_event_id": self.source_event_id,
            "callback_type": self.event_type,
            "existing_workline_session_id": (
                str(self.existing_workline_session_id) if self.existing_workline_session_id is not None else None
            ),
            "incoming_workline_session_id": (
                str(self.incoming_workline_session_id) if self.incoming_workline_session_id is not None else None
            ),
        }


def _require_same_workline_session_owner(
    existing: Any,
    *,
    provider_code: str,
    event_type: str,
    source_event_id: str,
    incoming_workline_session_id: int | None,
) -> None:
    """在 ACK 或幂等 claim 前校验既有记录的 WorklineSession 归属。"""

    existing_workline_session_id = cast("int | None", getattr(existing, "workline_session_id", None))
    # None 表示入站方未声明 owner；只有双方都明确声明且不一致时才构成归属冲突。
    if (
        existing_workline_session_id is not None
        and incoming_workline_session_id is not None
        and existing_workline_session_id != incoming_workline_session_id
    ):
        raise RuntimeInboxSessionOwnershipConflict(
            provider_code=provider_code,
            event_type=event_type,
            source_event_id=source_event_id,
            existing_workline_session_id=existing_workline_session_id,
            incoming_workline_session_id=incoming_workline_session_id,
        )


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


def _canonical_payload_hash(payload_json: dict[str, Any]) -> str:
    """生成与 canonical JSON 持久化语义一致的稳定内容摘要。"""

    encoded = json.dumps(
        payload_json,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _normalize_persistent_event_id(event_id: str | None, *, producer: str) -> str:
    """校验并规范真实 occurrence identity，不截断也不生成替代值。"""

    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError(f"{producer} requires persistent event_id")
    normalized = event_id.strip()
    if len(normalized) > 120:
        raise ValueError(f"{producer} event_id length must not exceed 120")
    return normalized


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
        ("workline-session", session_id),
        ("execution-session", execution_session_id),
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


def _canonical_workline_session_id(payload_json: dict[str, Any]) -> int | None:
    """从锁定的 canonical payload.data.session_id 合同提取 WorklineSession ID。"""

    data = payload_json.get("data")
    if not isinstance(data, dict):
        return None
    value = data.get("session_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class RuntimeInboxService:
    """RuntimeInbox ACK-before-processing 与人工重放服务。"""

    def __init__(
        self,
        repository: RuntimeInboxRepository = runtime_inbox_repository,
        audit_service: _AuditService | None = None,
        idempotency_guard: IdempotencyGuard = default_idempotency_guard,
        replay_source_validator: RuntimeInboxReplaySourceValidator | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.idempotency_guard = idempotency_guard
        self.replay_source_validator = replay_source_validator or RuntimeInboxReplaySourceValidator(repository)

    async def accept_received(
        self,
        db: AsyncSession,
        *,
        provider_code: str,
        event_type: str,
        source_event_id: str | None,
        payload_hash: str | None,
        kind: str,
        payload_json: dict[str, Any],
        payload_schema_version: int,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        workline_id: int | None = None,
        device_id: int | None = None,
        command_id: int | None = None,
        workline_session_id: int | None = None,
        execution_session_id: int | None = None,
        correlation_id: str | None = None,
        max_retries: int = 5,
        now_ms: int | None = None,
    ) -> RuntimeInboxAcceptResult:
        """持久化入站消息并返回 ACK 语义结果。

        有 source_event_id 时按 provider_code + event_type + source_event_id
        做幂等；同 hash 返回既有记录，不同 hash 409。
        """

        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 1:
            raise ValueError("RuntimeInbox max_retries must be a positive integer")
        validate_canonical_payload_size(payload_json)
        canonical_session_id = _canonical_workline_session_id(payload_json)
        if (
            workline_session_id is not None
            and canonical_session_id is not None
            and workline_session_id != canonical_session_id
        ):
            raise ValueError(
                "RuntimeInbox workline_session_id mismatch: "
                f"explicit={workline_session_id}, canonical.data.session_id={canonical_session_id}"
            )
        workline_session_id = workline_session_id if workline_session_id is not None else canonical_session_id
        record_data = {
            "kind": kind,
            "payload_json": payload_json,
            "payload_schema_version": payload_schema_version,
            "trace_id": trace_id,
            "event_id": event_id,
            "causation_id": causation_id,
            "workline_id": workline_id,
            "device_id": device_id,
            "command_id": command_id,
            "workline_session_id": workline_session_id,
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
                session_id=workline_session_id,
                execution_session_id=execution_session_id,
                workline_id=workline_id,
                device_id=device_id,
                command_id=command_id,
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
                _require_same_workline_session_owner(
                    existing,
                    provider_code=provider_code,
                    event_type=event_type,
                    source_event_id=source_event_id,
                    incoming_workline_session_id=workline_session_id,
                )
                if existing.payload_hash != payload_hash:
                    raise RuntimeInboxConflict(
                        provider_code=provider_code,
                        event_type=event_type,
                        source_event_id=source_event_id,
                        existing_payload_hash=existing.payload_hash,
                        incoming_payload_hash=payload_hash,
                    )
                return RuntimeInboxAcceptResult(record=existing, created=False)

            # 既有 identity 的 ACK/冲突语义优先；只有确需新增时才校验关联 FK。
            if correlation_id is not None:
                try:
                    correlation_id = await self._require_existing_correlation_id(db, correlation_id=correlation_id)
                except RuntimeInboxCorrelationUnavailable:
                    # 查询与关联校验之间可能已有并发方插入；再次按既有 identity 语义收敛。
                    existing = await self.repository.get_by_source_event_identity(
                        db,
                        provider_code=provider_code,
                        event_type=event_type,
                        source_event_id=source_event_id,
                    )
                    if existing is None:
                        raise
                    _require_same_workline_session_owner(
                        existing,
                        provider_code=provider_code,
                        event_type=event_type,
                        source_event_id=source_event_id,
                        incoming_workline_session_id=workline_session_id,
                    )
                    if existing.payload_hash != payload_hash:
                        raise RuntimeInboxConflict(
                            provider_code=provider_code,
                            event_type=event_type,
                            source_event_id=source_event_id,
                            existing_payload_hash=existing.payload_hash,
                            incoming_payload_hash=payload_hash,
                        ) from None
                    return RuntimeInboxAcceptResult(record=existing, created=False)
                record_data["correlation_id"] = correlation_id
            try:
                async with db.begin_nested():
                    record = await self.repository.add_received(db, record_data)
                    await self._claim_device_event_idempotency_if_needed(
                        db,
                        provider_code=provider_code,
                        event_type=event_type,
                        source_event_id=source_event_id,
                        payload_hash=payload_hash,
                        correlation_id=correlation_id,
                        now_ms=now_ms,
                    )
            except IntegrityError:
                existing = await self.repository.get_by_source_event_identity(
                    db,
                    provider_code=provider_code,
                    event_type=event_type,
                    source_event_id=source_event_id,
                )
                if existing is None:
                    raise
                _require_same_workline_session_owner(
                    existing,
                    provider_code=provider_code,
                    event_type=event_type,
                    source_event_id=source_event_id,
                    incoming_workline_session_id=workline_session_id,
                )
                if existing.payload_hash != payload_hash:
                    raise RuntimeInboxConflict(
                        provider_code=provider_code,
                        event_type=event_type,
                        source_event_id=source_event_id,
                        existing_payload_hash=existing.payload_hash,
                        incoming_payload_hash=payload_hash,
                    ) from None
                return RuntimeInboxAcceptResult(record=existing, created=False)
        else:
            if correlation_id is not None:
                correlation_id = await self._require_existing_correlation_id(db, correlation_id=correlation_id)
                record_data["correlation_id"] = correlation_id
            record = await self.repository.add_received(db, record_data)
        return RuntimeInboxAcceptResult(record=record, created=True)

    # ============================================================
    # Internal event acceptors (Task 7c-a) — device event / internal
    # event / command result, all writing RuntimeInbox through the same
    # source identity idempotency contract when an identity is available.
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
        """按 trace_id 查唯一 ExecutionCorrelation；零条或多条命中均保持未关联。"""

        if not trace_id:
            return None
        return await self.repository.resolve_unique_correlation_id_by_trace(db, trace_id=trace_id)

    async def _require_existing_correlation_id(self, db: AsyncSession, *, correlation_id: str) -> str:
        """验证显式 correlation_id 已持久化，避免把 FK 错误推迟到 flush。"""

        if not await self.repository.correlation_id_exists(db, correlation_id=correlation_id):
            raise RuntimeInboxCorrelationUnavailable(correlation_id=correlation_id)
        return correlation_id

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

        - event_id 是持久上游 occurrence identity，缺失时 fail-closed。
        - provider_code 从 device_code 前缀派生 (ARM_01 -> ARM), 默认 "ECS"。
        - 本入口没有 ExecutionSession 映射参数，因此 execution_session_id 留空；
          processor 不得从 WorklineSession ID 推导该字段。
        - correlation_id 通过 trace_id 反查 ExecutionCorrelation；非唯一或查不到时保持为空。
        """

        if not isinstance(event_type, str) or not event_type:
            raise ValueError("device event requires event_type")
        if not isinstance(payload_json, dict):
            raise TypeError("device event payload_json must be a dict")
        event_id = _normalize_persistent_event_id(event_id, producer="device event")

        provider_code = self._derive_provider_code_for_device(device_code)
        payload_hash = _canonical_payload_hash(payload_json)
        source_event_id = event_id
        correlation_id = await self._resolve_correlation_id_by_trace(db, trace_id=trace_id)
        result = await self.accept_received(
            db,
            provider_code=provider_code,
            event_type=event_type,
            source_event_id=source_event_id,
            payload_hash=payload_hash,
            kind="DEVICE_EVENT",
            payload_json=payload_json,
            payload_schema_version=1,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            workline_id=workline_id,
            device_id=device_id,
            command_id=command_id,
            correlation_id=correlation_id,
        )
        if auto_commit:
            _ = await db.commit()
        return result

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
        - event_id 是 producer 持久 occurrence identity，缺失时 fail-closed。
        - correlation_id 缺省时按 trace_id 反查；未命中时保持为空，避免伪造外键。
        """

        if not isinstance(event_type, str) or not event_type:
            raise ValueError("internal event requires event_type")
        if not isinstance(payload_json, dict):
            raise TypeError("internal event payload_json must be a dict")
        event_id = _normalize_persistent_event_id(event_id, producer="internal event")

        if correlation_id is None:
            correlation_id = await self._resolve_correlation_id_by_trace(db, trace_id=trace_id)

        payload_hash = _canonical_payload_hash(payload_json)
        source_event_id = event_id
        result = await self.accept_received(
            db,
            provider_code="RUNTIME",
            event_type=event_type,
            source_event_id=source_event_id,
            payload_hash=payload_hash,
            kind="INTERNAL_EVENT",
            payload_json=payload_json,
            payload_schema_version=1,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            workline_id=workline_id,
            execution_session_id=execution_session_id,
            correlation_id=correlation_id,
        )
        if auto_commit:
            _ = await db.commit()
        return result

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
        - source_event_id: 优先 event_id, 否则按 command_code 派生稳定 key。
        - 稳定 source identity 同 hash ACK、异 hash 冲突。
        """

        if not isinstance(command_code, str) or not command_code:
            raise ValueError("command result requires command_code")

        provider_code = "DEVICE_RESULT" if device_code else "RUNTIME"
        source_event_id = event_id or f"command-result:{command_code}:{event_id or 'synth'}"

        canonical_payload = payload_json or {"command_code": command_code, "device_code": device_code}
        result = await self.accept_received(
            db,
            provider_code=provider_code,
            event_type="COMMAND_RESULT",
            source_event_id=source_event_id,
            payload_hash=_canonical_payload_hash(canonical_payload),
            kind="COMMAND_RESULT",
            payload_json=canonical_payload,
            payload_schema_version=1,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            workline_id=workline_id,
            device_id=device_id,
            command_id=command_id,
        )
        if auto_commit:
            _ = await db.commit()
        return result

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
        canonical_payload = {"event_type": "TIMER_TIMEOUT", "data": payload_data}
        record_data: dict[str, Any] = {
            "kind": "TIMER_TIMEOUT",
            "workline_session_id": session_id,
            "execution_session_id": execution_session_id,
            "workline_id": workline_id,
            "device_id": device_id,
            "command_id": command_id,
            "trace_id": trace_id,
            "provider_code": "RUNTIME",
            "event_type": "TIMER_TIMEOUT",
            "source_event_id": source_event_id,
            "payload_hash": _canonical_payload_hash(canonical_payload),
            "payload_json": canonical_payload,
            "payload_schema_version": 1,
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
        request_id: str,
        actor: str,
        reason: str,
    ) -> RuntimeInboxReplayResult:
        """从 DEAD_LETTER 新建重放记录；原记录保持终态。"""

        if not isinstance(request_id, str) or not request_id.strip() or len(request_id.strip()) > 100:
            raise RuntimeInboxReplayNotAllowed(reason_code="INVALID_REQUEST_ID")
        normalized_request_id = request_id.strip()
        normalized_actor = actor.strip() if isinstance(actor, str) else ""
        normalized_reason = reason.strip() if isinstance(reason, str) else ""
        if not normalized_actor or not normalized_reason:
            raise RuntimeInboxReplayNotAllowed(reason_code="INVALID_REPLAY_ENVELOPE")

        source = await self.repository.get_by_id_for_update(db, source_inbox_id, populate_existing=True)
        if source is None:
            raise RuntimeInboxNotFound(inbox_id=source_inbox_id)
        if source.status != "DEAD_LETTER":
            raise RuntimeInboxReplayNotAllowed(
                reason_code="SOURCE_NOT_DEAD_LETTER",
                detail=f"status={source.status}",
            )
        if source.last_error_code == PRE_CUTOVER_AUDIT_ONLY:
            raise RuntimeInboxReplayNotAllowed(reason_code="PRE_CUTOVER_AUDIT_ONLY")
        if not isinstance(source.max_retries, int) or isinstance(source.max_retries, bool) or source.max_retries < 1:
            raise RuntimeInboxReplayNotAllowed(reason_code="INVALID_SOURCE_RETRY_BUDGET")

        if source.kind == "REPLAY_REQUEST":
            validated_source = await self.replay_source_validator.validate(db, source=source)
            root_source = validated_source.root_source
            root_source_inbox_id = cast("int", root_source.id)
            original_kind = cast("str", root_source.kind)
            original_payload = dict(cast("dict[str, Any]", root_source.payload_json))
            evidence = _original_replay_evidence(root_source)
        else:
            if source.kind not in REPLAYABLE_ORIGINAL_KINDS or not isinstance(source.payload_json, dict):
                raise RuntimeInboxReplayNotAllowed(reason_code="INVALID_REPLAY_ENVELOPE")
            root_source_inbox_id = source_inbox_id
            original_kind = source.kind
            original_payload = dict(source.payload_json)
            evidence = _original_replay_evidence(source)

        canonical_payload = {
            "request_id": normalized_request_id,
            "actor": normalized_actor,
            "reason": normalized_reason,
            "immediate_source_inbox_id": source_inbox_id,
            "root_source_inbox_id": root_source_inbox_id,
            "original_kind": original_kind,
            "original_payload": original_payload,
            **evidence,
        }
        validate_replay_envelope(canonical_payload)
        replay_source_event_id = f"replay:{source_inbox_id}:{normalized_request_id}"
        if len(replay_source_event_id) > 160:
            raise RuntimeInboxReplayNotAllowed(reason_code="INVALID_REQUEST_ID")
        replay_event_id = f"replay:{source_inbox_id}:{sha256(normalized_request_id.encode()).hexdigest()}"
        replay_causation_id = source.event_id or f"inbox:{source_inbox_id}"

        incoming_payload_hash = _canonical_payload_hash(canonical_payload)
        try:
            replay = await self.accept_received(
                db,
                provider_code="RUNTIME",
                event_type="REPLAY_REQUEST",
                source_event_id=replay_source_event_id,
                payload_hash=incoming_payload_hash,
                kind="REPLAY_REQUEST",
                payload_json=canonical_payload,
                payload_schema_version=1,
                trace_id=cast("str | None", evidence["original_trace_id"]),
                event_id=replay_event_id,
                causation_id=replay_causation_id,
                workline_id=cast("int | None", evidence["original_workline_id"]),
                device_id=cast("int | None", evidence["original_device_id"]),
                command_id=cast("int | None", evidence["original_command_id"]),
                workline_session_id=cast("int | None", evidence["original_workline_session_id"]),
                execution_session_id=cast("int | None", evidence["original_execution_session_id"]),
                correlation_id=cast("str | None", evidence["original_correlation_id"]),
                max_retries=REPLAY_MAX_RETRIES,
            )
        except RuntimeInboxConflict as conflict:
            conflict_event = {
                "event_type": "RUNTIME_INBOX_MANUAL_REPLAY_CONFLICT",
                "source_inbox_id": str(source.id) if source.id is not None else None,
                "provider_code": "RUNTIME",
                "callback_type": "REPLAY_REQUEST",
                "source_event_id": replay_source_event_id,
                "existing_payload_hash": conflict.existing_payload_hash,
                "incoming_payload_hash": incoming_payload_hash,
                "actor": normalized_actor,
                "request_id": normalized_request_id,
            }
            try:
                await self._write_replay_conflict_audit(db, conflict_event)
            except Exception as audit_error:
                # 冲突审计是 409 合同的一部分；持久化失败时保留原冲突因果并 fail closed。
                raise RuntimeInboxAuditPersistenceFailed(
                    audit_event_type="RUNTIME_INBOX_MANUAL_REPLAY_CONFLICT",
                    original_error=audit_error,
                ) from conflict
            raise
        audit_event = {
            "event_type": "RUNTIME_INBOX_MANUAL_REPLAY",
            "source_inbox_id": str(source.id) if source.id is not None else None,
            "replay_inbox_id": str(replay.record.id) if replay.record.id is not None else None,
            "provider_code": source.provider_code,
            "callback_type": source.event_type,
            "source_event_id": source.source_event_id,
            "replay_source_event_id": replay.record.source_event_id,
            "payload_hash": source.payload_hash,
            "replay_payload_hash": replay.record.payload_hash,
            "actor": normalized_actor,
            "request_id": normalized_request_id,
        }
        if replay.created:
            try:
                await self._write_replay_audit(db, audit_event)
            except Exception as audit_error:
                raise RuntimeInboxAuditPersistenceFailed(
                    audit_event_type="RUNTIME_INBOX_MANUAL_REPLAY",
                    original_error=audit_error,
                ) from audit_error
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

    async def _write_replay_conflict_audit(
        self,
        db: AsyncSession,
        audit_event: dict[str, str | None],
    ) -> None:
        """写入受限冲突审计；只保留 identity/hash/actor，不记录原 payload。"""

        service = self.audit_service
        if service is None:
            from src.app.sys.services import audit_log_service

            service = audit_log_service

        _ = await service.create_audit_log(
            db,
            method="POST",
            title="RuntimeInbox 人工重放冲突",
            path=f"/runtime/inbox/{audit_event['source_inbox_id']}/replay",
            args={
                "model": "RuntimeInbox",
                "operation": "manual_replay_conflict",
                "record_id": audit_event["source_inbox_id"],
                **audit_event,
            },
            status=OperaStatus.FAIL,
            code="409",
            msg="RuntimeInbox replay identity payload conflict",
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

        委托唯一 RuntimeInboxRepository.claim_received_with_token。
        底层 SQL 匹配:
        - status='RECEIVED'
        - status='FAILED' AND next_retry_at <= now AND attempt_count < max_retries
        - status='PROCESSING' AND lease_until <= now (stale reclaim)
        """
        return await self.repository.claim_received_with_token(
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
        return await self.repository.recover_stale_leases(
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
        extra_values: dict[str, Any] = {"processed_at": now_ms}
        return await self.repository.update_terminal_state(
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
        from src.utils.timezone import timezone

        now = timezone.now_for_db()
        now_ms = int(now.timestamp() * 1000) if hasattr(now, "timestamp") else None
        extra: dict[str, Any] = {"last_error_message": error_message}
        if now_ms is not None:
            extra["failed_at"] = now_ms

        attempt_count = 0
        max_retries = 0
        if retryable:
            retry_metadata = await self.repository.get_retry_metadata(db, inbox_id=inbox_id)
            if retry_metadata is not None:
                attempt_count = retry_metadata.attempt_count
                max_retries = retry_metadata.max_retries

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
        return await self.repository.update_terminal_state(
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
        return await self.repository.update_terminal_state(
            db,
            inbox_id=inbox_id,
            lease_token=lease_token,
            target_state="DEAD_LETTER",
            extra_values=extra,
        )


runtime_inbox_service = RuntimeInboxService()


__all__ = [
    "RuntimeInboxAcceptResult",
    "RuntimeInboxAuditPersistenceFailed",
    "RuntimeInboxConflict",
    "RuntimeInboxCorrelationUnavailable",
    "RuntimeInboxNotFound",
    "RuntimeInboxPayloadTooLarge",
    "RuntimeInboxReplayNotAllowed",
    "RuntimeInboxReplayResult",
    "RuntimeInboxService",
    "RuntimeInboxSessionOwnershipConflict",
    "runtime_inbox_service",
    "validate_replay_envelope",
]
