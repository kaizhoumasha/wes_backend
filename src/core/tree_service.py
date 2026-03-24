from datetime import datetime
from typing import Any, TypeVar, cast

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.database.tree_repository import TreeRepository

# Model 类型 (如 Warehouse, Container 等)
M = TypeVar("M")


class TreeServiceMixin[M]:
    """树形服务混入类

    提供 TreeRepository 的树形操作能力。
    使用混入模式让 Service 同时获得 BaseService 和 TreeService 的能力。

    类型参数:
        M: Model 类型 (如 Menu, Warehouse 等)
    """

    def __init__(self, repo: TreeRepository[M]):
        self.repo = repo

    def _get_response_schema(self) -> type[Any] | None:
        return cast("type[Any] | None", getattr(self, "response_schema", None))

    def _serialize_items(self, items: list[Any], schema: type[Any] | None = None) -> list[dict[str, Any]]:
        return [self._to_dict(item, schema) for item in items]

    @staticmethod
    def _exclude_node(items: list[Any], node_id: int) -> list[Any]:
        return [item for item in items if getattr(item, "id", None) != node_id]

    async def get_tree(
        self,
        db: AsyncSession,
        root_id: int | None = None,
        max_depth: int = -1,
        relation_max_depth: int = 1,
        schema: type[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """获取树形结构数据（支持关联数据加载）

        Args:
            db: 数据库会话
            root_id: 根节点 ID（None 表示获取所有节点）
            max_depth: 树形深度限制（-1 表示无限制）
            relation_max_depth: 关联数据加载深度
            schema: 树接口响应 Schema（为空时回退到 service.response_schema）

        Returns:
            树形结构数据（字典列表）
        """
        schema = schema or self._get_response_schema()

        if root_id:
            items = await self.repo.get_descendants(
                db,
                root_id,
                max_depth if max_depth > 0 else None,
                schema=schema,
                relation_max_depth=relation_max_depth,
            )
        else:
            _, items = await self.repo.get_list(
                db,
                limit=10000,
                schema=schema,
                max_depth=relation_max_depth,
            )

        return self._build_tree_optimized(self._serialize_items(items, schema))

    def _to_dict(self, item: Any, schema: type[Any] | None = None) -> dict[str, Any]:
        """将模型转换为字典（支持关联数据）

        Args:
            item: 模型实例
            schema: 响应 Schema（如果提供，使用其序列化）

        Returns:
            字典形式的数据
        """
        # 使用现有的 model_to_schema 工具（处理关联数据）
        if schema:
            from src.core.schema_loader import model_to_schema

            try:
                schema_obj = model_to_schema(item, schema)
                return cast("dict[str, Any]", schema_obj.model_dump(mode="json"))
            except (AttributeError, TypeError, ValueError) as e:
                # 只捕获预期的异常，避免隐藏真实错误
                logger.debug(f"Schema serialization failed for {item.__class__.__name__}: {e}")

        # 回退到默认的列序列化
        mapper = cast("Any", inspect(item.__class__))
        result: dict[str, Any] = {}
        for c in mapper.columns:
            value = getattr(item, c.key, None)
            result[c.key] = value.isoformat() if isinstance(value, datetime) else value
        return result

    def _build_tree_optimized(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        node_map: dict[int, dict[str, Any]] = {}
        root_nodes: list[dict[str, Any]] = []

        for item in items:
            item["children"] = []
            node_map[item["id"]] = item

        for item in items:
            parent_id = item.get("parent_id")
            if parent_id and parent_id in node_map:
                node_map[parent_id]["children"].append(item)
            else:
                root_nodes.append(item)

        return root_nodes

    async def get_siblings(
        self,
        db: AsyncSession,
        node_id: int,
        include_self: bool = False,
    ) -> list[dict[str, Any]]:
        node = await self.repo.get_by_id(db, node_id)
        if not node:
            return []
        children = await self.repo.get_children(db, node.parent_id)  # type: ignore[attr-defined]
        if not include_self:
            children = self._exclude_node(children, node_id)
        return self._serialize_items(children)

    async def get_ancestors(
        self,
        db: AsyncSession,
        node_id: int,
        include_self: bool = False,
    ) -> list[dict[str, Any]]:
        ancestors = await self.repo.get_ancestors(db, node_id)
        if not include_self:
            ancestors = self._exclude_node(ancestors, node_id)
        return self._serialize_items(ancestors)

    async def move_node(
        self,
        db: AsyncSession,
        node_id: int,
        new_parent_id: int | None,
    ) -> dict[str, Any]:
        result = await self.repo.move_node(db, node_id, new_parent_id)
        return self._to_dict(result)


__all__ = ["TreeServiceMixin"]
