"""WMS circuit breaker Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Select, select, text

from src.app.wms_integration.models import WmsCircuitBreakerState
from src.database.base_repository import BaseRepository
from src.database.dialect import dialect_name

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class WmsCircuitBreakerRepository(BaseRepository[WmsCircuitBreakerState]):
    """WMS 熔断器状态数据访问层。"""

    def __init__(self) -> None:
        super().__init__(WmsCircuitBreakerState)

    def build_key_lookup_statement(
        self,
        *,
        target_code: str,
        operation_name: str,
        for_update: bool = False,
    ) -> Select[tuple[WmsCircuitBreakerState]]:
        """构造按 breaker key 查询的语句，for_update=True 时用于锁定状态行。"""

        columns = cast("Any", WmsCircuitBreakerState).__table__.c
        statement = select(WmsCircuitBreakerState).where(
            columns.target_code == target_code,
            columns.operation_name == operation_name,
        )
        return statement.with_for_update() if for_update else statement

    async def acquire_key_lock(self, db: AsyncSession, *, target_code: str, operation_name: str) -> None:
        """按 breaker key 获取事务级锁。

        PostgreSQL 下锁住“状态行可能尚不存在”的并发创建窗口；SQLite 单测环境跳过。
        """

        if dialect_name(db) != "postgresql":
            return
        _ = await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"wms_circuit_breaker:{target_code}:{operation_name}"},
        )

    async def get_by_key(
        self,
        db: AsyncSession,
        *,
        target_code: str,
        operation_name: str,
    ) -> WmsCircuitBreakerState | None:
        result = await db.execute(
            self.build_key_lookup_statement(target_code=target_code, operation_name=operation_name)
        )
        return result.scalar_one_or_none()

    async def get_by_key_for_update(
        self,
        db: AsyncSession,
        *,
        target_code: str,
        operation_name: str,
    ) -> WmsCircuitBreakerState | None:
        result = await db.execute(
            self.build_key_lookup_statement(target_code=target_code, operation_name=operation_name, for_update=True)
        )
        return result.scalar_one_or_none()

    async def get_or_create_for_update(
        self,
        db: AsyncSession,
        *,
        target_code: str,
        operation_name: str,
    ) -> WmsCircuitBreakerState:
        """获取并锁定 breaker 状态；不存在时在同一事务锁保护下创建。"""

        await self.acquire_key_lock(db, target_code=target_code, operation_name=operation_name)
        state = await self.get_by_key_for_update(db, target_code=target_code, operation_name=operation_name)
        if state is not None:
            return state

        state = WmsCircuitBreakerState(target_code=target_code, operation_name=operation_name)
        db.add(state)
        await db.flush()
        await db.refresh(state)
        return state


wms_circuit_breaker_repository = WmsCircuitBreakerRepository()


__all__ = [
    "WmsCircuitBreakerRepository",
    "wms_circuit_breaker_repository",
]
