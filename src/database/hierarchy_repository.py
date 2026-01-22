"""层级数据 Repository（物化路径模式）"""

from typing import TypeVar

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.base_repository import BaseRepository

T = TypeVar("T")


class HierarchyRepository[T](BaseRepository[T]):
    """层级数据 Repository（基于 tree_path 物化路径）"""

    async def get_children(
        self,
        db: AsyncSession,
        parent_id: int | None,
        include_inactive: bool = False,
    ) -> list[T]:
        """获取直接子节点"""
        where_clauses = [self.model.parent_id == parent_id]
        if not include_inactive and hasattr(self.model, "is_active"):
            where_clauses.append(self.model.is_active == True)  # noqa: E712

        return await self.get_all(db, where_clauses=where_clauses, order_by=self.model.sort_order)

    async def get_descendants(
        self,
        db: AsyncSession,
        parent_id: int,
        max_depth: int | None = None,
    ) -> list[T]:
        """获取所有后代节点"""
        parent = await self.get_by_id(db, parent_id)
        if not parent:
            return []

        # 使用 bind parameter 防止 SQL 注入
        stmt = select(self.model).where(self.model.tree_path.like(f"{parent.tree_path}%"))  # type: ignore
        if max_depth:
            stmt = stmt.where(self.model.level <= parent.level + max_depth)  # type: ignore

        stmt = stmt.order_by(self.model.tree_path, self.model.sort_order)  # type: ignore
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_ancestors(self, db: AsyncSession, node_id: int) -> list[T]:
        """获取祖先路径"""
        node = await self.get_by_id(db, node_id)
        if not node or not node.tree_path or node.tree_path == "/":  # type: ignore
            return []

        ancestor_ids = [int(id_) for id_ in node.tree_path.strip("/").split("/") if id_]  # type: ignore
        if not ancestor_ids:
            return []

        result = await db.execute(
            select(self.model).where(self.model.id.in_(ancestor_ids)).order_by(self.model.level)  # type: ignore
        )
        return list(result.scalars().all())

    async def move_node(
        self,
        db: AsyncSession,
        node_id: int,
        new_parent_id: int | None,
    ) -> T:
        """移动节点（含循环检测）"""
        if new_parent_id is not None:
            if node_id == new_parent_id:
                raise ValueError("节点不能成为自己的父节点")

            descendants = await self.get_descendants(db, node_id)
            if any(d.id == new_parent_id for d in descendants):
                raise ValueError("不能将节点移动到其后代节点下")

        return await self.update(db, node_id, {"parent_id": new_parent_id})

    async def get_depth(self, db: AsyncSession, node_id: int) -> int:
        """获取节点深度"""
        node = await self.get_by_id(db, node_id)
        return node.level - 1 if node else 0  # type: ignore

    async def is_leaf(self, db: AsyncSession, node_id: int) -> bool:
        """判断是否叶子节点"""
        children = await self.get_children(db, node_id)
        return len(children) == 0


__all__ = ["HierarchyRepository"]
