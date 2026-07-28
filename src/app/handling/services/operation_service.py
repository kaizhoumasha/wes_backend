"""Handling operation 服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


class HandlingOperationMigrationRequiredError(RuntimeError):
    """Handling operation 尚未迁移到 typed T5 dispatcher。"""


class HandlingOperationService:
    """系统级 Handling operation 服务。"""

    async def request_bin_operation(
        self,
        db: AsyncSession,
        *,
        operation_type: str,
        operation_key: str,
        moves: Sequence[Mapping[str, Any]],
        trace_id: str,
        workline_id: int | None = None,
        workline_code: str | None = None,
        material_session_id: int | None = None,
        carrier_type: str = "CTU",
        carrier_code: str | None = None,
        timeout_seconds: int | None = None,
    ) -> Any:
        """T5 dispatcher 实现前拒绝创建 Handling operation 或 outbox。"""

        del (
            db,
            operation_type,
            operation_key,
            moves,
            trace_id,
            workline_id,
            workline_code,
            material_session_id,
            carrier_type,
            carrier_code,
            timeout_seconds,
        )
        raise HandlingOperationMigrationRequiredError(
            "legacy handling transport is removed; T5 dispatcher is not implemented"
        )


handling_operation_service = HandlingOperationService()


__all__ = [
    "HandlingOperationMigrationRequiredError",
    "HandlingOperationService",
    "handling_operation_service",
]
