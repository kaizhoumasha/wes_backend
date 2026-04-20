from datetime import datetime
from typing import Any, TypeVar, cast

from pydantic import BaseModel
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger

# Model 类型 (如 Warehouse, Container 等)
M = TypeVar("M")


class TreeServiceMixin[M]:
    """树形服务混入类

    提供 TreeRepository 的树形操作能力。
    使用混入模式让 Service 同时获得 BaseService 和 TreeService 的能力。

    类型参数:
        M: Model 类型 (如 Menu, Warehouse 等)

    Note:
        repo 属性继承自 BaseService（MRO 保证），此处使用 type: ignore 避免
        basedpyright 的属性覆盖检查问题。
    """

    # 继承自 BaseService，不需要重新定义 repo
    # 由于 BaseService 中 repo 是 property，Mixin 无需定义

    def _get_response_schema(self) -> type[Any] | None:
        return cast("type[Any] | None", getattr(self, "response_schema", None))

    def _serialize_items(self, items: list[Any], schema: type[Any] | None = None) -> list[dict[str, Any]]:
        return [self._to_dict(item, schema) for item in items]

    @staticmethod
    def _exclude_node(items: list[Any], node_id: int) -> list[Any]:
        return [item for item in items if getattr(item, "id", None) != node_id]

    @staticmethod
    def _tree_sort_key(item: dict[str, Any]) -> tuple[int, int]:
        sort_order = item.get("sort_order")
        item_id = item.get("id")
        return (
            sort_order if isinstance(sort_order, int) else 0,
            item_id if isinstance(item_id, int) else 0,
        )

    def _sort_tree_nodes(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nodes.sort(key=self._tree_sort_key)
        for node in nodes:
            children = node.get("children")
            if isinstance(children, list):
                _ = self._sort_tree_nodes(children)
        return nodes

    async def get_tree(
        self,
        db: AsyncSession,
        root_id: int | None = None,
        max_depth: int = 1,
        tree_depth: int = 0,
        schema: type[Any] | None = None,
        cache: object | None = None,
    ) -> list[dict[str, Any]]:
        """获取树形结构数据（支持关联数据加载与缓存）

        Args:
            db: 数据库会话
            root_id: 根节点 ID（None 表示获取所有节点）
            max_depth: 关联数据加载深度（默认 1）
            tree_depth: 树深度（-1=完整树, 0=仅顶层/懒加载）
            schema: 树接口响应 Schema（为空时回退到 service.response_schema）
            cache: 缓存实例

        Returns:
            树形结构数据（字典列表）
        """
        schema = schema or self._get_response_schema()

        # 🔥 树形结构缓存（可选功能）
        # 注意：树形缓存适用于变更不频繁的树（如配置树），不适用于频繁变更的树（如菜单）
        # 性能考虑：树形缓存 TTL = 详情缓存 TTL 的 50%
        if cache and hasattr(self, "enable_cache") and self.enable_cache and hasattr(self, "cache_prefix"):
            cache_key = f"{self.cache_prefix}:tree:r{root_id}:td{tree_depth}:md{max_depth}"
            hit, cached_data = await self._get_cached_tree(cache, cache_key)
            if hit:
                return cached_data

            items = await self._query_tree(db, root_id, max_depth, tree_depth, schema)
            await self._set_cached_tree(cache, cache_key, items)
            return items

        return await self._query_tree(db, root_id, max_depth, tree_depth, schema)

    async def _query_tree(
        self,
        db: AsyncSession,
        root_id: int | None,
        max_depth: int,
        tree_depth: int,
        schema: type[Any] | None,
    ) -> list[dict[str, Any]]:
        """查询树形数据（内部方法）"""
        # tree_depth=0 表示懒加载模式：只返回顶层节点，children 为空，has_children 标识存在子节点
        if tree_depth == 0:
            return await self._get_tree_lazy(db, root_id, schema, max_depth)

        if root_id:
            items = await self.repo.get_descendants(
                db,
                root_id,
                tree_depth if tree_depth > 0 else None,
                schema=schema,
                relation_max_depth=max_depth,
            )
        else:
            _, items = await self.repo.get_list(
                db,
                limit=10000,
                schema=schema,
                max_depth=max_depth,
            )

        return self._build_tree_optimized(self._serialize_items(items, schema))

    async def _get_cached_tree(self, cache: object, cache_key: str) -> tuple[bool, list[dict[str, Any]]]:
        """读取树形缓存"""
        from src.database.cache_helpers import get_cached_value

        hit, cached = await get_cached_value(cache, cache_key)
        if hit and isinstance(cached, list):
            return True, cached
        return False, []

    async def _set_cached_tree(self, cache: object, cache_key: str, items: list[dict[str, Any]]) -> None:
        """写入树形缓存（使用较短的 TTL）"""
        if hasattr(self, "cache_expire"):
            # 树形缓存 TTL = 普通 TTL 的 50%
            tree_cache_expire = max(300, self.cache_expire // 2)
            await cache.set(cache_key, items, expire=tree_cache_expire)

    async def _get_tree_lazy(
        self,
        db: AsyncSession,
        root_id: int | None,
        schema: type[Any] | None,
        max_depth: int,
    ) -> list[dict[str, Any]]:
        """获取懒加载树形数据（只返回顶层节点 + has_children 标志）

        Args:
            db: 数据库会话
            root_id: 根节点 ID（None 表示获取所有根节点）
            schema: 响应 Schema
            max_depth: 关联数据加载深度

        Returns:
            顶层节点列表，每个节点包含 has_children 标志
        """
        # 获取顶层节点（直接子节点）
        children = await self.repo.get_children(db, root_id, schema=schema, relation_max_depth=max_depth)  # type: ignore[attr-defined]

        # 复用 _serialize_items 序列化
        result = self._serialize_items(children, schema)

        # 添加懒加载标志
        for item in result:
            # has_children 来自数据库字段，若不存在则默认 False
            item["has_children"] = item.get("has_children", False)
            # 懒加载模式：children 为空数组，前端展开时调用 /children 接口
            item["children"] = []

        return result

    def _to_dict(self, item: Any, schema: type[Any] | None = None) -> dict[str, Any]:
        """将模型转换为字典（支持关联数据）

        优化：如果 item 已经是 Pydantic 模型，直接返回其 dict，避免重复转换。

        Args:
            item: 模型实例
            schema: 响应 Schema（如果提供，使用其序列化）

        Returns:
            字典形式的数据
        """
        if schema:
            try:
                to_response = getattr(self, "to_response", None)
                if callable(to_response):
                    response = to_response(item, schema)
                    if hasattr(response, "model_dump"):
                        # 使用 cast 避免 basedpyright 错误
                        response_dict: dict[str, Any] = cast("Any", response).model_dump(mode="json")
                        return response_dict
                    if isinstance(response, dict):
                        return response

                from src.core.schema_loader import model_to_schema

                schema_obj = model_to_schema(item, schema)
                return cast("dict[str, Any]", schema_obj.model_dump(mode="json"))
            except (AttributeError, TypeError, ValueError) as e:
                # 只捕获预期的异常，避免隐藏真实错误
                logger.debug(f"Schema serialization failed for {item.__class__.__name__}: {e}")

        # 优化：仅在未指定 schema 时，Pydantic 模型直接返回 dict
        if isinstance(item, BaseModel):
            return cast("dict[str, Any]", item.model_dump(mode="json"))

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

        return self._sort_tree_nodes(root_nodes)

    async def get_siblings(
        self,
        db: AsyncSession,
        node_id: int,
        include_self: bool = False,
    ) -> list[dict[str, Any]]:
        schema = self._get_response_schema()
        node = await self.repo.get_by_id(db, node_id)
        if not node:
            return []
        # 获取父节点的直接子节点 = 同级节点
        siblings = await self.repo.get_children(db, node.parent_id, schema=schema, relation_max_depth=1)  # type: ignore[attr-defined]
        if not include_self:
            siblings = self._exclude_node(siblings, node_id)
        return self._serialize_items(siblings, schema)

    async def get_ancestors(
        self,
        db: AsyncSession,
        node_id: int,
        include_self: bool = False,
    ) -> list[dict[str, Any]]:
        schema = self._get_response_schema()
        ancestors = await self.repo.get_ancestors(db, node_id, schema=schema, relation_max_depth=1)
        if not include_self:
            ancestors = self._exclude_node(ancestors, node_id)
        return self._serialize_items(ancestors, schema)

    async def get_children(
        self,
        db: AsyncSession,
        node_id: int,
    ) -> list[dict[str, Any]]:
        schema = self._get_response_schema()
        children = await self.repo.get_children(db, node_id, schema=schema, relation_max_depth=1)
        return self._serialize_items(children, schema)

    async def move_node(
        self,
        db: AsyncSession,
        node_id: int,
        new_parent_id: int | None,
        cache: object | None = None,
    ) -> dict[str, Any]:
        old_parent_id: int | None = None
        if cache and hasattr(self, "repo"):
            try:
                instance_before = await self.repo.get_by_id(db, node_id)  # type: ignore[attr-defined]
                if instance_before:
                    old_parent_id = getattr(instance_before, "parent_id", None)
            except Exception as exc:
                logger.debug(f"读取节点 {node_id} 迁移前父节点失败，继续迁移流程: {exc}")

        result = await self.repo.move_node(db, node_id, new_parent_id)
        await db.commit()

        # 失效当前节点和列表缓存
        if cache and hasattr(self, "invalidate_cache"):
            await self.invalidate_cache(cache, id=node_id, invalidate_list=True, invalidate_tree=True)

            moved_descendant_ids = getattr(result, "_moved_descendant_ids", None)
            if not isinstance(moved_descendant_ids, list):
                descendants = await self.repo.get_descendants(db, node_id)
                moved_descendant_ids = [
                    descendant_id
                    for descendant in descendants
                    if (descendant_id := getattr(descendant, "id", None)) is not None and descendant_id != node_id
                ]

            for descendant_id in moved_descendant_ids:
                await self.invalidate_cache(cache, id=descendant_id, invalidate_list=False)

            if new_parent_id is not None:
                await self.invalidate_cache(cache, id=new_parent_id)
            if old_parent_id is not None and old_parent_id != new_parent_id:
                await self.invalidate_cache(cache, id=old_parent_id)

        return self._to_dict(result)

    async def batch_sort(
        self,
        db: AsyncSession,
        items: list[dict[str, Any]],
        cache: object | None = None,
    ) -> None:
        """批量排序节点

        Args:
            db: 数据库会话
            items: 排序项列表 [{"id": 1, "parent_id": None, "sort_order": 0}, ...]
            cache: 缓存实例
        """
        result = await self.repo.batch_sort(db, items)
        await db.commit()

        # 批量失效缓存
        if cache and hasattr(self, "invalidate_cache"):
            await self.invalidate_cache(cache, invalidate_list=True, invalidate_tree=True)

            invalidated_detail_ids: set[int] = set()
            for item in items:
                node_id = item["id"]
                if node_id in invalidated_detail_ids:
                    continue
                invalidated_detail_ids.add(node_id)
                await self.invalidate_cache(cache, id=node_id, invalidate_list=False)

            moved_descendant_ids = getattr(result, "moved_descendant_ids", [])
            if not isinstance(moved_descendant_ids, list):
                moved_descendant_ids = []

            for descendant_id in moved_descendant_ids:
                if not isinstance(descendant_id, int) or descendant_id in invalidated_detail_ids:
                    continue
                invalidated_detail_ids.add(descendant_id)
                await self.invalidate_cache(cache, id=descendant_id, invalidate_list=False)

            affected_parent_ids = getattr(result, "affected_parent_ids", None)
            if not isinstance(affected_parent_ids, list):
                affected_parent_ids = [
                    parent_id for item in items if isinstance((parent_id := item.get("parent_id")), int)
                ]

            for parent_id in affected_parent_ids:
                if not isinstance(parent_id, int) or parent_id in invalidated_detail_ids:
                    continue
                invalidated_detail_ids.add(parent_id)
                await self.invalidate_cache(cache, id=parent_id, invalidate_list=False)
        elif cache:
            # 兜底：如果没有 invalidate_cache 方法，尝试手动失效
            logger.warning("TreeServiceMixin 未继承 BaseService，无法自动失效缓存")

    # ==================== 重写 CRUD 方法（增加树形缓存失效） ====================

    async def _ensure_node_has_no_children(self, db: AsyncSession, node_id: int) -> None:
        """删除前校验节点是否仍有子节点。

        设计约束：
        - 当前树资源（菜单、权限）前端采用懒加载树展示
        - 若允许直接删除父节点，会导致子节点在懒加载模式下变成不可见孤儿节点
        - 这里连同已删除子节点一起校验，避免父节点删除后回收站中的子节点变成孤儿
        """
        children = await self.repo.get_children(  # type: ignore[attr-defined]
            db,
            node_id,
            include_inactive=True,
            relation_max_depth=1,
        )
        if children:
            raise ValueError("当前节点存在下级节点，请先删除或移动下级节点后再删除")

    async def _ensure_parent_available_for_restore(self, db: AsyncSession, id: int) -> None:
        """恢复前校验父节点状态，避免恢复出孤儿节点。"""
        if not hasattr(self, "repo"):
            return

        instance_before = await self.repo.get_by_id(db, id, include_deleted=True)  # type: ignore[attr-defined]
        if not instance_before:
            return

        parent_id = getattr(instance_before, "parent_id", None)
        if parent_id is None:
            return

        parent = await self.repo.get_by_id(db, parent_id, include_deleted=True)  # type: ignore[attr-defined]
        if parent is None:
            raise ValueError("父节点不存在，无法恢复当前节点，请先处理父节点")

        if getattr(parent, "is_deleted", False):
            raise ValueError("父节点仍在回收站中，请先恢复父节点后再恢复当前节点")

    async def create(self, db: AsyncSession, data: dict[str, Any], cache: object | None = None) -> Any:
        """创建节点（失效树形缓存 + 父节点详情缓存）"""
        # 调用 BaseService.create
        result = await super().create(db, data, cache)  # type: ignore[misc]

        # 失效缓存
        if cache and hasattr(self, "invalidate_cache") and result:
            # 失效树形缓存
            await self.invalidate_cache(cache, invalidate_tree=True)

            # 🔥 关键修复：失效父节点详情缓存
            # 创建子节点时，父节点的 has_children 字段被 Hook 更新为 True
            # 必须失效父节点缓存，否则 API 返回旧的 has_children=False
            parent_id = getattr(result, "parent_id", None)
            if parent_id is not None:
                await self.invalidate_cache(cache, id=parent_id)

        return result

    async def update(self, db: AsyncSession, id: int, data: dict[str, Any], cache: object | None = None) -> Any:
        """更新节点（如果修改了 parent_id，失效新旧父节点缓存）"""
        old_parent_id: int | None = None
        if cache and "parent_id" in data and hasattr(self, "repo"):
            try:
                instance_before = await self.repo.get_by_id(db, id)  # type: ignore[attr-defined]
                if instance_before:
                    old_parent_id = getattr(instance_before, "parent_id", None)
            except Exception as exc:
                logger.debug(f"读取节点 {id} 更新前父节点失败，继续更新流程: {exc}")

        # 调用 BaseService.update
        result = await super().update(db, id, data, cache)  # type: ignore[misc]

        # 如果修改了 parent_id 或 sort_order，额外失效缓存
        if cache and hasattr(self, "invalidate_cache") and result and ("parent_id" in data or "sort_order" in data):
            # 失效树形缓存
            await self.invalidate_cache(cache, invalidate_tree=True)

            # 如果修改了 parent_id，失效新旧父节点详情缓存
            if "parent_id" in data:
                new_parent_id = data.get("parent_id")
                if new_parent_id is not None:
                    await self.invalidate_cache(cache, id=new_parent_id)
                if old_parent_id is not None and old_parent_id != new_parent_id:
                    await self.invalidate_cache(cache, id=old_parent_id)

        return result

    async def delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool | None:
        """删除节点（失效树形缓存 + 父节点详情缓存）"""
        await self._ensure_node_has_no_children(db, id)

        # 删除前先获取 parent_id（删除后实例不可用）
        parent_id_to_invalidate: int | None = None
        if cache and hasattr(self, "repo"):
            try:
                instance_before = await self.repo.get_by_id(db, id)  # type: ignore[attr-defined]
                if instance_before:
                    parent_id_to_invalidate = getattr(instance_before, "parent_id", None)
            except Exception as exc:
                logger.debug(f"读取节点 {id} 删除前父节点失败，继续删除流程: {exc}")

        # 调用 BaseService.delete
        success = await super().delete(db, id, cache)  # type: ignore[misc]

        # 失效缓存
        if cache and hasattr(self, "invalidate_cache") and success:
            # 失效树形缓存
            await self.invalidate_cache(cache, invalidate_tree=True)

            # 🔥 失效父节点详情缓存（has_children 可能变为 False）
            if parent_id_to_invalidate is not None:
                await self.invalidate_cache(cache, id=parent_id_to_invalidate)

        return success

    async def soft_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> Any:
        """软删除节点（失效树形缓存 + 父节点详情缓存）"""
        # 调用 BaseService.soft_delete
        result = await super().soft_delete(db, id, cache)  # type: ignore[misc]

        # 失效缓存
        if cache and hasattr(self, "invalidate_cache") and result:
            # 失效树形缓存
            await self.invalidate_cache(cache, invalidate_tree=True)

            # 🔥 失效父节点详情缓存（has_children 可能变为 False）
            parent_id = getattr(result, "parent_id", None)
            if parent_id is not None:
                await self.invalidate_cache(cache, id=parent_id)

        return result

    async def restore(self, db: AsyncSession, id: int, cache: object | None = None) -> Any:
        """恢复节点（失效树形缓存 + 父节点详情缓存）"""
        await self._ensure_parent_available_for_restore(db, id)

        # 调用 BaseService.restore
        result = await super().restore(db, id, cache)  # type: ignore[misc]

        # 失效缓存
        if cache and hasattr(self, "invalidate_cache") and result:
            # 失效树形缓存
            await self.invalidate_cache(cache, invalidate_tree=True)

            # 🔥 失效父节点详情缓存（has_children 可能变为 True）
            parent_id = getattr(result, "parent_id", None)
            if parent_id is not None:
                await self.invalidate_cache(cache, id=parent_id)

        return result

    async def permanent_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool:
        """永久删除节点（失效树形缓存 + 父节点详情缓存）"""
        await self._ensure_node_has_no_children(db, id)

        parent_id_to_invalidate: int | None = None
        if cache and hasattr(self, "repo"):
            try:
                instance_before = await self.repo.get_by_id(db, id, include_deleted=True)  # type: ignore[attr-defined]
                if instance_before:
                    parent_id_to_invalidate = getattr(instance_before, "parent_id", None)
            except Exception as exc:
                logger.debug(f"读取节点 {id} 永久删除前父节点失败，继续永久删除流程: {exc}")

        success = await super().permanent_delete(db, id, cache)  # type: ignore[misc]

        if cache and hasattr(self, "invalidate_cache") and success:
            await self.invalidate_cache(cache, invalidate_tree=True)
            if parent_id_to_invalidate is not None:
                await self.invalidate_cache(cache, id=parent_id_to_invalidate)

        return success


__all__ = ["TreeServiceMixin"]
