"""树形数据 Repository（物化路径模式）"""

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.base_repository import BaseRepository

T = TypeVar("T")


class TreeRepository[T](BaseRepository[T]):
    """树形数据 Repository（基于 TreeMixin 的 tree_path 物化路径）"""

    async def get_children(
        self,
        db: AsyncSession,
        parent_id: int | None,
        include_inactive: bool = False,
    ) -> list[T]:
        """获取直接子节点"""
        # 使用 getattr 避免泛型类型的属性访问错误
        parent_id_attr = self.model.parent_id
        where_clauses = [parent_id_attr == parent_id]

        is_active_attr = getattr(self.model, "is_active", None)
        if not include_inactive and is_active_attr is not None:
            where_clauses.append(is_active_attr)

        sort_order_attr = self.model.sort_order
        return await self.get_all(db, where_clauses=where_clauses, order_by=sort_order_attr)

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
        # 使用 getattr 避免泛型类型的属性访问错误
        tree_path_attr = parent.tree_path
        stmt = select(self.model).where(
            self.model.tree_path.like(f"{tree_path_attr}%")  # type: ignore[attr-defined]
        )
        if max_depth:
            parent_level = parent.level
            stmt = stmt.where(
                self.model.level <= parent_level + max_depth  # type: ignore[attr-defined]
            )

        stmt = stmt.order_by(
            self.model.tree_path,
            self.model.sort_order,
        )  # type: ignore[attr-defined]
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_ancestors(self, db: AsyncSession, node_id: int) -> list[T]:
        """获取祖先路径"""
        node = await self.get_by_id(db, node_id)
        tree_path = getattr(node, "tree_path", None) if node else None
        if not node or not tree_path or tree_path == "/":
            return []

        ancestor_ids = [int(id_) for id_ in tree_path.strip("/").split("/") if id_]
        if not ancestor_ids:
            return []

        result = await db.execute(
            select(self.model)
            .where(self.model.id.in_(ancestor_ids))  # type: ignore[attr-defined]
            .order_by(self.model.level)  # type: ignore[attr-defined]
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
        level = getattr(node, "level", 1) if node else 1
        return level - 1

    async def is_leaf(self, db: AsyncSession, node_id: int) -> bool:
        """判断是否叶子节点"""
        children = await self.get_children(db, node_id)
        return len(children) == 0


__all__ = ["TreeRepository"]
