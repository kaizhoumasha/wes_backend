"""IdempotencyGuard (Phase 1 H5 最小实现, 主计划 §5.4)。

WES outbound effect 幂等闸门: dispatch 前对 RuntimeIntentLog / DeviceCommand
等出站操作做 claim, 防止崩溃重放或重试导致下游双发。

最小语义 (Phase 3 ENG-009 才扩展 409 + 安全审计):
- NEW    首次 claim, 写入 IdempotencyKey, 调用方可继续 dispatch
- MATCH  同 (provider, op_kind, key) 已存在且 request_hash 一致, 调用方安全跳过
- 同 key 不同 hash → 抛 IdempotencyConflict, 调用方必须中止 dispatch

WES 内部 key 命名: `WES-{OPERATION_KIND}-{HASH}` (主计划 §5.4)。
外部 provider 提供的 key (e.g. WMS 回调 source_event_id) 不强制此前缀。
"""

from __future__ import annotations

import re
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


class IdempotencyConflict(Exception):
    """同 key 不同 hash, 必须中止 dispatch (主计划 §5.4)。"""

    def __init__(self, *, provider_code: str, operation_kind: str, idempotency_key: str) -> None:
        super().__init__(
            f"idempotency conflict: provider={provider_code} op={operation_kind} key={idempotency_key} "
            "(same key, different request_hash; 中止 dispatch 防止双发)"
        )
        self.provider_code = provider_code
        self.operation_kind = operation_kind
        self.idempotency_key = idempotency_key


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
    "idempotency_guard",
    "is_wes_internal_key",
    "make_wes_internal_key",
]
