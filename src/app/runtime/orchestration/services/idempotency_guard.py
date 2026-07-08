"""IdempotencyGuard runtime idempotency implementation。

WES outbound effect 幂等闸门: dispatch 前对 RuntimeIntentLog / DeviceCommand
等出站操作做 claim, 防止崩溃重放或重试导致下游双发。

核心语义:
- NEW    首次 claim, 写入 IdempotencyKey, 调用方可继续 dispatch
- MATCH  同 (provider, op_kind, key) 已存在且 request_hash 一致, 调用方安全跳过
- 同 key 不同 hash → 抛 IdempotencyConflict
- 调用方必须中止 dispatch 并输出 runtime audit matrix payload

WES 内部 key 命名: `WES-{OPERATION_KIND}-{HASH}` (主计划 §5.4)。
外部 provider 提供的 key (e.g. WMS 回调 source_event_id) 不强制此前缀。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from src.app.runtime.orchestration.repositories.idempotency_key_repository import (
    IdempotencyKeyRepository,
    idempotency_key_repository,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.orchestration.idempotency_key import IdempotencyKey

# 主计划 §5.4 WES 内部 key 命名: `WES-{OPERATION_KIND}-{HASH}`
_WES_KEY_PATTERN = re.compile(r"^WES-[A-Z0-9_]+-[A-Za-z0-9_.\-]+$")


class ClaimResult(str, Enum):
    """`IdempotencyGuard.claim_or_match` 返回值。"""

    NEW = "NEW"
    MATCH = "MATCH"


@dataclass(frozen=True, slots=True)
class IdempotencyOperationSpec:
    """跨域幂等审计矩阵条目。"""

    operation_kind: str
    domain: str


_IDEMPOTENCY_OPERATION_MATRIX = {
    "callback": IdempotencyOperationSpec(operation_kind="callback", domain="callback"),
    "fulfillment": IdempotencyOperationSpec(operation_kind="fulfillment", domain="wms_integration"),
    "device_command": IdempotencyOperationSpec(operation_kind="device_command", domain="device"),
    "device_event": IdempotencyOperationSpec(operation_kind="device_event", domain="device"),
    "reconciliation": IdempotencyOperationSpec(operation_kind="reconciliation", domain="reconciliation"),
}

_IDEMPOTENCY_OPERATION_ALIASES = {
    "external_callback": "callback",
    "wms_callback": "callback",
    "rcs_callback": "callback",
    "wms_fulfillment": "fulfillment",
    "device_dispatch": "device_command",
    "dispatch_command": "device_command",
    "command_result": "device_event",
    "event_push": "device_event",
    "runtime_reconciliation": "reconciliation",
    "resource_reconciliation": "reconciliation",
}


def _normalize_operation_kind(operation_kind: str) -> str:
    return operation_kind.strip().lower().replace("-", "_")


def default_idempotency_operation_matrix() -> dict[str, IdempotencyOperationSpec]:
    """返回 canonical operation_kind 审计矩阵。"""

    return dict(_IDEMPOTENCY_OPERATION_MATRIX)


def get_idempotency_operation_spec(operation_kind: str) -> IdempotencyOperationSpec:
    """按 canonical kind 或 legacy alias 获取幂等审计域。"""

    normalized = _normalize_operation_kind(operation_kind)
    canonical = _IDEMPOTENCY_OPERATION_ALIASES.get(normalized, normalized)
    spec = _IDEMPOTENCY_OPERATION_MATRIX.get(canonical)
    if spec is not None:
        return spec
    return IdempotencyOperationSpec(operation_kind=normalized or "unknown", domain="runtime")


class IdempotencyConflict(Exception):
    """同 key 不同 hash, 必须中止 dispatch 并产生安全审计事件。"""

    status_code = 409

    def __init__(
        self,
        *,
        provider_code: str,
        operation_kind: str,
        idempotency_key: str,
        existing_request_hash: str | None = None,
        incoming_request_hash: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            f"idempotency conflict: provider={provider_code} op={operation_kind} key={idempotency_key} "
            "(same key, different request_hash; 中止 dispatch 防止双发)"
        )
        self.provider_code = provider_code
        self.operation_kind = operation_kind
        self.idempotency_key = idempotency_key
        self.existing_request_hash = existing_request_hash
        self.incoming_request_hash = incoming_request_hash
        self.correlation_id = correlation_id

    def to_audit_event(self) -> dict[str, object]:
        """转换为稳定安全审计 payload。"""

        spec = get_idempotency_operation_spec(self.operation_kind)
        return {
            "event_type": "IDEMPOTENCY_CONFLICT",
            "provider_code": self.provider_code,
            "operation_kind": self.operation_kind,
            "normalized_operation_kind": spec.operation_kind,
            "domain": spec.domain,
            "idempotency_key": self.idempotency_key,
            "existing_request_hash": self.existing_request_hash,
            "incoming_request_hash": self.incoming_request_hash,
            "correlation_id": self.correlation_id,
            "status_code": self.status_code,
            "security_control": "idempotency_key_request_hash",
        }


def is_wes_internal_key(idempotency_key: str) -> bool:
    """WES 内部生成的 key 是否符合 `WES-{OPERATION_KIND}-{HASH}` 命名 (主计划 §5.4)。"""
    return bool(_WES_KEY_PATTERN.match(idempotency_key))


def make_wes_internal_key(operation_kind: str, payload_hash: str) -> str:
    """构造符合命名约束的 WES 内部 key。"""
    op = operation_kind.strip().upper().replace("-", "_")
    if not op or not re.fullmatch(r"[A-Z0-9_]+", op):
        raise ValueError(f"operation_kind 必须是大写字母/数字/下划线: {operation_kind!r}")
    if not payload_hash:
        raise ValueError("payload_hash 不能为空")
    return f"WES-{op}-{payload_hash}"


def _match_existing_or_raise(
    row: IdempotencyKey,
    *,
    provider_code: str,
    operation_kind: str,
    idempotency_key: str,
    request_hash: str,
) -> ClaimResult:
    if row.request_hash != request_hash:
        raise IdempotencyConflict(
            provider_code=provider_code,
            operation_kind=operation_kind,
            idempotency_key=idempotency_key,
            existing_request_hash=row.request_hash,
            incoming_request_hash=request_hash,
            correlation_id=row.execution_correlation_id,
        )
    return ClaimResult.MATCH


class IdempotencyGuard:
    """outbound effect 幂等闸门 (主计划 §5.4 H5 最小实现)。"""

    def __init__(self, repository: IdempotencyKeyRepository = idempotency_key_repository) -> None:
        self.repository = repository

    async def claim_or_match(
        self,
        db: AsyncSession,
        *,
        provider_code: str,
        operation_kind: str,
        idempotency_key: str,
        request_hash: str,
        execution_correlation_id: str,
        now_ms: int,
        business_owner_key: str | None = None,
    ) -> ClaimResult:
        """声明幂等键 (NEW=首次插入, MATCH=同 hash 已存在)。

        同 key 不同 hash 抛 `IdempotencyConflict`, 调用方必须中止 dispatch。
        使用 no_autoflush 避免 claim 提前 flush 调用方事务中的无关 pending ORM 对象;
        execution_correlation_id 对应的 ExecutionCorrelation 必须已持久化。
        """
        with db.no_autoflush:
            inserted = await self.repository.claim_if_absent(
                db,
                provider_code=provider_code,
                operation_kind=operation_kind,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                execution_correlation_id=execution_correlation_id,
                now_ms=now_ms,
                business_owner_key=business_owner_key,
            )
            if inserted:
                return ClaimResult.NEW

            row = await self.repository.get_by_identity(
                db,
                provider_code=provider_code,
                operation_kind=operation_kind,
                idempotency_key=idempotency_key,
            )
        if row is None:
            raise RuntimeError(
                f"幂等 claim 冲突后无法读取既有 key: provider={provider_code} op={operation_kind} key={idempotency_key}"
            )
        return _match_existing_or_raise(
            row,
            provider_code=provider_code,
            operation_kind=operation_kind,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )


idempotency_guard = IdempotencyGuard()


__all__ = [
    "ClaimResult",
    "IdempotencyConflict",
    "IdempotencyGuard",
    "IdempotencyOperationSpec",
    "default_idempotency_operation_matrix",
    "get_idempotency_operation_spec",
    "idempotency_guard",
    "is_wes_internal_key",
    "make_wes_internal_key",
]
