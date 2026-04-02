"""树形数据 Repository（物化路径模式）"""

from typing import Any, Protocol, TypeVar, cast

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.base_repository import BaseRepository
from src.database.hooks import HookContext, HookFunc, HookType

T = TypeVar("T")
FilterClause = ColumnElement[bool]


class TreeNodeLike(Protocol):
    id: int
    parent_id: int | None
    tree_path: str
    level: int


class TreeRepository[T](BaseRepository[T]):
    """
    树形数据 Repository（基于 TreeMixin 的 tree_path 物化路径）

    自动维护 tree_path 字段：
    - 创建节点时自动计算 tree_path
    - 移动节点时自动更新 tree_path（含所有后代）
    - 使用 flush() 获取自增 ID 后再计算 tree_path

    使用示例：
        class PermissionRepository(TreeRepository[Permission]):
            pass

        repo = PermissionRepository()

        # 创建根节点（自动计算 tree_path = "/1/"）
        root = await repo.create(db, {"name": "root"})

        # 创建子节点（自动计算 tree_path = "/1/2/"）
        child = await repo.create(db, {
            "name": "child",
            "parent_id": root.id,
        })
    """

    def __init__(self, model: type[T]):
        """
        初始化 TreeRepository

        自动注册 tree_path 维护 Hook：
        - BEFORE_CREATE: 创建节点时自动计算 tree_path
        - BEFORE_UPDATE: 移动节点时自动更新 tree_path
        """
        super().__init__(model)
        self._register_tree_hooks()

    def _register_tree_hooks(self) -> None:
        """注册 tree_path 自动维护 Hook"""
        # 检查模型是否有 TreeMixin 的特征字段
        has_tree_fields = all(hasattr(self.model, field) for field in ["parent_id", "tree_path", "level", "sort_order"])

        if not has_tree_fields:
            return

        # 注册创建 Hook：在 refresh 之后自动计算 tree_path
        # 使用 AFTER_CREATE 确保在 db.refresh() 之后执行
        # priority=10 确保 AFTER_CREATE Hook 在其他 Hook 之后执行
        self.add_hook(
            HookType.AFTER_CREATE,
            self._create_tree_path_hook(),
            priority=10,  # 在 refresh 之后执行
        )

        # 注册更新 Hook：移动节点时更新 tree_path
        self.add_hook(
            HookType.BEFORE_UPDATE,
            self._update_tree_path_hook(),
            priority=-10,
        )

        # 注册 has_children 维护 Hook
        self._register_has_children_hooks()

    def _register_has_children_hooks(self) -> None:
        """注册 has_children 自动维护 Hook"""
        # 检查模型是否有 has_children 字段
        if not hasattr(self.model, "has_children"):
            return

        # 创建子节点后：更新父节点的 has_children = True
        self.add_hook(
            HookType.AFTER_CREATE,
            self._update_parent_has_children_hook(),
            priority=5,
        )

        # 删除子节点后：更新父节点的 has_children
        self.add_hook(
            HookType.AFTER_DELETE,
            self._cleanup_parent_has_children_hook(),
            priority=5,
        )

        # 移动节点后：更新原父节点和新父节点的 has_children
        self.add_hook(
            HookType.AFTER_UPDATE,
            self._move_node_has_children_hook(),
            priority=5,
        )

    def _create_tree_path_hook(self) -> HookFunc:
        """创建 tree_path 自动计算 Hook（AFTER_CREATE）"""

        async def hook(ctx: HookContext) -> None:
            # ⚠️ 重要：BaseRepository 传递的是 session，不是 db
            db = ctx.params.get("session") or ctx.session
            instance = cast("TreeNodeLike | None", ctx.params.get("instance"))

            if not instance or not db:
                return

            # AFTER_CREATE 阶段，instance 已经有 ID 了
            current_id = instance.id
            parent_id = getattr(instance, "parent_id", None)

            if parent_id is None:
                # 根节点
                new_tree_path = f"/{current_id}/"
                new_level = 1
            else:
                # 子节点：查询父节点获取 tree_path
                parent = await db.execute(
                    select(self.model).where(self.model.id == parent_id)  # type: ignore[attr-defined]
                )
                parent = parent.scalar_one_or_none()

                if parent:
                    parent_node = cast("TreeNodeLike", parent)
                    # 父节点存在：tree_path = parent.tree_path + current_id + /
                    parent_path = parent_node.tree_path
                    new_level = parent_node.level + 1
                    new_tree_path = f"{parent_path}{current_id}/"
                else:
                    # 父节点不存在：作为根节点处理
                    new_tree_path = f"/{current_id}/"
                    new_level = 1

            # 更新实例并同步到数据库
            instance.tree_path = new_tree_path
            instance.level = new_level
            _ = await db.flush()

        return hook

    def _update_tree_path_hook(self) -> HookFunc:
        """创建 tree_path 更新 Hook（移动节点）"""

        async def hook(ctx: HookContext) -> None:
            # ⚠️ 重要：BaseRepository 传递的是 session，不是 db
            db = ctx.params.get("session") or ctx.session
            data = ctx.params.get("data", {})
            instance = cast("TreeNodeLike | None", ctx.params.get("instance"))

            if not instance or not db:
                return

            # 只处理 parent_id 变更的情况
            if "parent_id" not in data:
                return

            new_parent_id = data["parent_id"]
            old_parent_id = instance.parent_id

            if new_parent_id == old_parent_id:
                return  # 父节点未变化，直接返回

            # 获取当前节点的旧 tree_path
            old_tree_path = instance.tree_path
            old_level = instance.level

            # 计算新的 tree_path
            if new_parent_id is None:
                # 移动到根节点
                new_parent_level = 0
                new_tree_path = f"/{instance.id}/"
            else:
                # 移动到新的父节点下
                parent = await db.execute(
                    select(self.model).where(self.model.id == new_parent_id)  # type: ignore[attr-defined]
                )
                parent = parent.scalar_one_or_none()

                if parent:
                    parent_node = cast("TreeNodeLike", parent)
                    new_parent_level = parent_node.level
                    new_tree_path = f"{parent_node.tree_path}{instance.id}/"
                else:
                    new_parent_level = 0
                    new_tree_path = f"/{instance.id}/"

            _ = await db.flush()
            instance.tree_path = new_tree_path
            instance.level = new_parent_level + 1
            level_delta = instance.level - old_level

            # 更新所有后代的 tree_path
            # 新路径 = 旧路径.replace(old_tree_path, new_tree_path)
            # 例如：old = "/1/5/", new = "/2/10/"
            #      descendant.tree_path = "/1/5/12/" → "/2/10/12/"
            descendants = await db.execute(
                select(self.model).where(
                    self.model.tree_path.like(f"{old_tree_path}%"),  # type: ignore[attr-defined]
                    self.model.id != instance.id,  # type: ignore[attr-defined]
                )
            )
            descendants = descendants.scalars().all()

            for descendant in descendants:
                descendant_node = cast("TreeNodeLike", descendant)
                # 替换路径前缀
                descendant_node.tree_path = descendant_node.tree_path.replace(old_tree_path, new_tree_path)
                descendant_node.level += level_delta

            # 🔥 新增：失效所有后代节点的缓存
            # 将后代 ID 列表存入 Hook context，供 AFTER_UPDATE Hook 使用
            if descendants and hasattr(ctx, "results"):
                ctx.results["moved_descendant_ids"] = [d.id for d in descendants if hasattr(d, "id")]

        return hook

    def _update_parent_has_children_hook(self) -> HookFunc:
        """创建子节点后，更新父节点的 has_children = True（AFTER_CREATE）"""

        async def hook(ctx: HookContext) -> None:
            db = ctx.params.get("session") or ctx.session
            instance = cast("TreeNodeLike | None", ctx.params.get("instance"))

            if not instance or not db:
                return

            parent_id = getattr(instance, "parent_id", None)
            if parent_id is None:
                return

            # 更新父节点的 has_children = True
            parent = await db.execute(
                select(self.model).where(self.model.id == parent_id)  # type: ignore[attr-defined]
            )
            parent = parent.scalar_one_or_none()
            if parent:
                parent.has_children = True  # type: ignore[attr-defined]
                _ = await db.flush()

        return hook

    def _cleanup_parent_has_children_hook(self) -> HookFunc:
        """删除子节点后，检查并更新父节点的 has_children（AFTER_DELETE）"""

        async def hook(ctx: HookContext) -> None:
            db = ctx.params.get("session") or ctx.session
            instance = ctx.params.get("instance")

            if not instance or not db:
                return

            parent_id = getattr(instance, "parent_id", None)
            if parent_id is None:
                return

            # 检查父节点是否还有其他非软删除子节点
            parent_id_attr = self.model.parent_id  # type: ignore[attr-defined]
            stmt = select(self.model).where(parent_id_attr == parent_id)  # type: ignore[attr-defined]
            is_deleted_attr = getattr(self.model, "is_deleted", None)
            if is_deleted_attr is not None:
                stmt = stmt.where(is_deleted_attr == False)  # noqa: E712
            result = await db.execute(stmt.limit(1))
            remaining_child = result.scalar_one_or_none()

            # 如果没有剩余子节点，更新父节点的 has_children = False
            if not remaining_child:
                parent = await db.execute(
                    select(self.model).where(self.model.id == parent_id)  # type: ignore[attr-defined]
                )
                parent = parent.scalar_one_or_none()
                if parent:
                    parent.has_children = False  # type: ignore[attr-defined]
                    _ = await db.flush()

        return hook

    def _move_node_has_children_hook(self) -> HookFunc:
        """移动节点后，更新原父节点和新父节点的 has_children（AFTER_UPDATE）"""

        async def hook(ctx: HookContext) -> None:
            db = ctx.params.get("session") or ctx.session
            data = ctx.params.get("data", {})
            instance = cast("TreeNodeLike | None", ctx.params.get("instance"))

            if not instance or not db:
                return

            # 只处理 parent_id 变更的情况
            if "parent_id" not in data:
                return

            new_parent_id = data["parent_id"]
            # AFTER_UPDATE 时 instance 已经被 db.refresh()，parent_id 是新值
            # 必须从 _audit_old_values 中获取变更前的原始 parent_id
            old_values = ctx.params.get("_audit_old_values", {})
            old_parent_id = old_values.get("parent_id")

            if new_parent_id == old_parent_id:
                return

            # 更新原父节点的 has_children（可能从有子节点变为无子节点）
            if old_parent_id is not None:
                await self._check_and_update_has_children(db, old_parent_id)

            # 更新新父节点的 has_children = True
            if new_parent_id is not None:
                parent = await db.execute(
                    select(self.model).where(self.model.id == new_parent_id)  # type: ignore[attr-defined]
                )
                parent = parent.scalar_one_or_none()
                if parent:
                    parent.has_children = True  # type: ignore[attr-defined]
                    _ = await db.flush()

        return hook

    async def _check_and_update_has_children(self, db: AsyncSession, parent_id: int) -> None:
        """检查并更新父节点的 has_children 状态（只计算未软删除的子节点）"""
        parent_id_attr = self.model.parent_id  # type: ignore[attr-defined]
        stmt = select(self.model).where(parent_id_attr == parent_id)  # type: ignore[attr-defined]
        is_deleted_attr = getattr(self.model, "is_deleted", None)
        if is_deleted_attr is not None:
            stmt = stmt.where(is_deleted_attr == False)  # noqa: E712
        stmt = stmt.limit(1)
        result = await db.execute(stmt)
        remaining_child = result.scalar_one_or_none()

        parent = await db.execute(
            select(self.model).where(self.model.id == parent_id)  # type: ignore[attr-defined]
        )
        parent = parent.scalar_one_or_none()
        if parent:
            parent.has_children = remaining_child is not None  # type: ignore[attr-defined]
            _ = await db.flush()

    async def get_children(
        self,
        db: AsyncSession,
        parent_id: int | None,
        include_inactive: bool = False,
        schema: type | None = None,
        relation_max_depth: int = 1,
    ) -> list[T]:
        """获取直接子节点"""
        # 使用 getattr 避免泛型类型的属性访问错误
        parent_id_attr = self.model.parent_id  # type: ignore[attr-defined]
        where_clauses: list[FilterClause] = [cast("FilterClause", parent_id_attr == parent_id)]

        # 使用软删除控制状态
        if not include_inactive:
            is_deleted_attr = getattr(self.model, "is_deleted", None)
            if is_deleted_attr is not None:
                where_clauses.append(cast("FilterClause", is_deleted_attr == False))  # noqa: E712

        sort_order_attr = self.model.sort_order  # type: ignore[attr-defined]
        _, children = await self.get_list(
            db,
            limit=1000,
            where_clauses_raw=where_clauses,
            order_by_raw=[sort_order_attr],
            schema=schema,
            max_depth=relation_max_depth,
        )
        return children

    async def get_descendants(
        self,
        db: AsyncSession,
        parent_id: int,
        max_depth: int | None = None,
        schema: type | None = None,
        relation_max_depth: int = 1,
    ) -> list[T]:
        """获取所有后代节点（支持关联数据加载）

        Args:
            db: 数据库会话
            parent_id: 父节点 ID
            max_depth: 树形深度限制
            schema: 响应 Schema（用于自动加载关联数据）
            relation_max_depth: 关联数据加载深度

        Returns:
            后代节点列表
        """
        parent = await self.get_by_id(db, parent_id)
        if not parent:
            return []

        # 使用 bind parameter 防止 SQL 注入
        # 使用 getattr 避免泛型类型的属性访问错误
        tree_path_attr = parent.tree_path  # type: ignore[attr-defined]
        stmt = select(self.model).where(
            self.model.tree_path.like(f"{tree_path_attr}%")  # type: ignore[attr-defined]
        )
        if max_depth:
            parent_level = parent.level  # type: ignore[attr-defined]
            stmt = stmt.where(
                self.model.level <= parent_level + max_depth  # type: ignore[attr-defined]
            )

        stmt = stmt.order_by(
            self.model.tree_path,  # type: ignore[attr-defined]
            self.model.sort_order,  # type: ignore[attr-defined]
        )  # type: ignore[attr-defined]
        result = await db.execute(stmt)
        items = list(result.scalars().all())

        # 如果提供了 schema，自动加载关联数据
        if schema and items:
            items = await self._load_relations_for_items(db, items, schema, relation_max_depth)

        return items

    async def get_ancestors(
        self,
        db: AsyncSession,
        node_id: int,
        schema: type | None = None,
        relation_max_depth: int = 1,
    ) -> list[T]:
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
        items = list(result.scalars().all())

        # 如果提供了 schema，自动加载关联数据
        if schema and items:
            items = await self._load_relations_for_items(db, items, schema, relation_max_depth)

        return items

    async def move_node(
        self,
        db: AsyncSession,
        node_id: int,
        new_parent_id: int | None,
    ) -> T | None:
        """移动节点（含循环检测）"""
        if new_parent_id is not None:
            if node_id == new_parent_id:
                raise ValueError("节点不能成为自己的父节点")

            descendants = await self.get_descendants(db, node_id)
            if any(d.id == new_parent_id for d in descendants):  # type: ignore[attr-defined]
                raise ValueError("不能将节点移动到其后代节点下")

        return await self.update(db, node_id, {"parent_id": new_parent_id})

    async def batch_sort(
        self,
        db: AsyncSession,
        items: list[dict[str, Any]],
    ) -> None:
        """批量更新节点的 parent_id 和 sort_order

        Args:
            db: 数据库会话
            items: 排序项列表 [{"id": 1, "parent_id": null, "sort_order": 0}, ...]

        Raises:
            ValueError: 节点不存在或形成循环依赖
        """
        if not items:
            return

        node_ids = [item["id"] for item in items]

        result = await db.execute(
            select(self.model).where(self.model.id.in_(node_ids))  # type: ignore[attr-defined]
        )
        nodes = {node.id: node for node in result.scalars().all()}

        missing_ids = set(node_ids) - set(nodes.keys())
        if missing_ids:
            raise ValueError(f"节点不存在: {missing_ids}")

        # 在任何变更前捕获原始状态，避免第二阶段读到已修改的值
        original: dict[int, dict[str, Any]] = {
            nid: {
                "parent_id": getattr(node, "parent_id", None),
                "level": getattr(node, "level", 1),
                "tree_path": getattr(node, "tree_path", f"/{nid}/"),
            }
            for nid, node in nodes.items()
        }

        # 循环检测 + 预取后代（合并两次查询为一次）
        # changed_nodes[node_id] = (new_parent_id, descendants_list)
        changed_nodes: dict[int, tuple[int | None, list[Any]]] = {}
        for item in items:
            node_id = item["id"]
            new_parent_id = item.get("parent_id")
            if new_parent_id != original[node_id]["parent_id"]:
                descendants = await self.get_descendants(db, node_id)
                if new_parent_id is not None:
                    descendant_ids = {d.id for d in descendants}  # type: ignore[attr-defined]
                    if new_parent_id in descendant_ids:
                        raise ValueError(f"节点 {node_id} 不能移动到其后代节点 {new_parent_id} 下")
                changed_nodes[node_id] = (new_parent_id, descendants)

        # 第一阶段：更新当前节点的 sort_order / parent_id / tree_path / level
        for item in items:
            node_id = item["id"]
            new_parent_id = item.get("parent_id")
            node = nodes[node_id]

            node.sort_order = item.get("sort_order", 0)  # type: ignore[attr-defined]

            if node_id not in changed_nodes:
                continue

            if new_parent_id is None:
                node.tree_path = f"/{node_id}/"  # type: ignore[attr-defined]
                node.level = 1  # type: ignore[attr-defined]
            else:
                parent_node = nodes.get(new_parent_id)
                if parent_node is None:
                    parent_result = await db.execute(
                        select(self.model).where(self.model.id == new_parent_id)  # type: ignore[attr-defined]
                    )
                    parent_node = parent_result.scalar_one_or_none()
                if parent_node is not None:
                    node.tree_path = f"{parent_node.tree_path}{node_id}/"  # type: ignore[attr-defined]
                    node.level = parent_node.level + 1  # type: ignore[attr-defined]

            node.parent_id = new_parent_id  # type: ignore[attr-defined]
            if hasattr(node, "version"):
                node.version += 1  # type: ignore[attr-defined]

        # 第二阶段：更新所有后代的 tree_path / level（使用变更前的原始路径做替换）
        for node_id, (_, descendants) in changed_nodes.items():
            node = nodes[node_id]
            old_tree_path = original[node_id]["tree_path"]
            new_tree_path = getattr(node, "tree_path", old_tree_path)
            level_delta = getattr(node, "level", 1) - original[node_id]["level"]

            for descendant in descendants:
                descendant_node = cast("TreeNodeLike", descendant)
                descendant_node.tree_path = descendant_node.tree_path.replace(old_tree_path, new_tree_path)
                descendant_node.level += level_delta
                if hasattr(descendant, "version"):
                    descendant.version += 1  # type: ignore[attr-defined]

        await db.flush()

        # 第三阶段：batch_sort 绕过了 Hook 系统，手动维护 has_children
        # TODO: 考虑重构为"批量更新后触发 AFTER_UPDATE Hook"，避免手动维护
        # 只有模型具有 has_children 字段时才执行
        if changed_nodes and hasattr(self.model, "has_children"):
            # 收集所有受影响的父节点 ID
            old_parent_ids = {
                original[nid]["parent_id"]
                for nid in changed_nodes
                if original[nid]["parent_id"] is not None
            }
            new_parent_ids = {new_pid for new_pid, _ in changed_nodes.values() if new_pid is not None}

            # 原父节点：重新检查是否还有非软删除子节点
            for pid in old_parent_ids:
                await self._check_and_update_has_children(db, pid)

            # 新父节点：直接标记为有子节点
            all_new_parent_ids = new_parent_ids - old_parent_ids  # 避免重复处理
            for pid in all_new_parent_ids:
                parent_result = await db.execute(
                    select(self.model).where(self.model.id == pid)  # type: ignore[attr-defined]
                )
                parent_obj = parent_result.scalar_one_or_none()
                if parent_obj:
                    parent_obj.has_children = True  # type: ignore[attr-defined]

            await db.flush()


__all__ = ["TreeRepository"]
