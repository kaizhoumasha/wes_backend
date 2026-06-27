"""IdempotencyKey Repository 层。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.runtime.orchestration.idempotency_key import IdempotencyKey

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class IdempotencyKeyRepository:
    """幂等键数据访问层。"""

    async def get_by_identity(
        self,
        db: AsyncSession,
        *,
        provider_code: str,
        operation_kind: str,
        idempotency_key: str,
    ) -> IdempotencyKey | None:
        """按复合幂等身份查询。"""

        columns = cast("Any", IdempotencyKey).__table__.c
        result = await db.execute(
            select(IdempotencyKey).where(
                columns.provider_code == provider_code,
                columns.operation_kind == operation_kind,
                columns.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def claim_if_absent(
        self,
        db: AsyncSession,
        *,
        provider_code: str,
        operation_kind: str,
        idempotency_key: str,
        request_hash: str,
        execution_correlation_id: str,
        now_ms: int,
        business_owner_key: str | None,
    ) -> bool:
        """原子 claim 幂等键；插入成功返回 True, 已存在返回 False。"""

        table = cast("Any", IdempotencyKey).__table__
        dialect_name = db.get_bind().dialect.name
        if dialect_name == "sqlite":
            insert_fn = sqlite_insert
        elif dialect_name == "postgresql":
            insert_fn = postgresql_insert
        else:
            raise NotImplementedError(f"IdempotencyKey claim 暂不支持数据库方言: {dialect_name}")

        statement = (
            insert_fn(table)
            .values(
                provider_code=provider_code,
                operation_kind=operation_kind,
                idempotency_key=idempotency_key,
                execution_correlation_id=execution_correlation_id,
                request_hash=request_hash,
                business_owner_key=business_owner_key,
                created_at=now_ms,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    table.c.provider_code,
                    table.c.operation_kind,
                    table.c.idempotency_key,
                ],
            )
            .returning(table.c.idempotency_key)
        )
        return isinstance((await db.execute(statement)).scalar_one_or_none(), str)


idempotency_key_repository = IdempotencyKeyRepository()


__all__ = ["IdempotencyKeyRepository", "idempotency_key_repository"]
