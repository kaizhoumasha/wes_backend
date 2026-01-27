from datetime import datetime

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.tree_repository import TreeRepository


class TreeServiceMixin:
    repo: TreeRepository

    async def get_tree(
        self,
        db: AsyncSession,
        root_id: int | None = None,
        max_depth: int = -1,
    ) -> list:
        if root_id:
            items = await self.repo.get_descendants(db, root_id, max_depth if max_depth > 0 else None)
        else:
            items = await self.repo.get_all(db)

        dict_items = [self._to_dict(item) for item in items]
        return self._build_tree_optimized(dict_items)

    def _to_dict(self, item) -> dict:
        mapper = inspect(item.__class__)
        result = {}
        for c in mapper.columns:
            value = getattr(item, c.key, None)
            result[c.key] = value.isoformat() if isinstance(value, datetime) else value
        return result

    def _build_tree_optimized(self, items: list[dict]) -> list:
        node_map: dict[int, dict] = {}
        root_nodes: list[dict] = []

        for item in items:
            item["children"] = []
            node_map[item["id"]] = item

        for item in items:
            if item["parent_id"] and item["parent_id"] in node_map:
                node_map[item["parent_id"]]["children"].append(item)
            else:
                root_nodes.append(item)

        return root_nodes

    async def get_siblings(
        self,
        db: AsyncSession,
        node_id: int,
        include_self: bool = False,
    ) -> list:
        node = await self.repo.get_by_id(db, node_id)
        if not node:
            return []
        children = await self.repo.get_children(db, node.parent_id)  # type: ignore[attr-defined]
        if not include_self:
            children = [c for c in children if c.id != node_id]  # type: ignore[attr-defined]
        return [self._to_dict(c) for c in children]

    async def get_ancestors(
        self,
        db: AsyncSession,
        node_id: int,
        include_self: bool = False,
    ) -> list:
        ancestors = await self.repo.get_ancestors(db, node_id)
        if not include_self:
            ancestors = [a for a in ancestors if a.id != node_id]  # type: ignore[attr-defined]
        return [self._to_dict(a) for a in ancestors]

    async def move_node(
        self,
        db: AsyncSession,
        node_id: int,
        new_parent_id: int | None,
    ):
        result = await self.repo.move_node(db, node_id, new_parent_id)
        return self._to_dict(result)


__all__ = ["TreeServiceMixin"]
