"""BinTransitMembership Repository 层。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.handling.models.bin_transit_membership import BinTransitMembership
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class BinTransitMembershipRepository(BaseRepository[BinTransitMembership]):
    """料箱队列 membership 数据访问层。"""

    def __init__(self) -> None:
        super().__init__(BinTransitMembership)

    async def create_without_session_rollback(
        self,
        db: AsyncSession,
        data: dict[str, Any],
    ) -> BinTransitMembership:
        """创建 membership；约束冲突交给调用方的 savepoint 隔离。"""

        try:
            membership = BinTransitMembership(**data)
            db.add(membership)
            await db.flush()
            await db.refresh(membership)
            return membership
        except IntegrityError as exc:
            self._handle_integrity_error(exc)
            raise

    async def get_active_by_bin_code(
        self,
        db: AsyncSession,
        bin_code: str,
    ) -> BinTransitMembership | None:
        """按真实料箱编码读取 active membership。"""

        columns = cast("Any", BinTransitMembership).__table__.c
        result = await db.execute(
            select(BinTransitMembership).where(columns.bin_code == bin_code, columns.left_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_active_by_placeholder_key(
        self,
        db: AsyncSession,
        placeholder_key: str,
    ) -> BinTransitMembership | None:
        """按未扫码占位键读取 active membership。"""

        columns = cast("Any", BinTransitMembership).__table__.c
        result = await db.execute(
            select(BinTransitMembership).where(columns.placeholder_key == placeholder_key, columns.left_at.is_(None))
        )
        return result.scalar_one_or_none()


bin_transit_membership_repository = BinTransitMembershipRepository()


__all__ = [
    "BinTransitMembershipRepository",
    "bin_transit_membership_repository",
]
