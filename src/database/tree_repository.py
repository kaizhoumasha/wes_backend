"""树形数据 Repository（物化路径模式）"""

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.base_repository import BaseRepository
from src.database.hooks import HookContext, HookFunc, HookType

T = TypeVar("T")


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

    def _create_tree_path_hook(self) -> HookFunc:
        """创建 tree_path 自动计算 Hook（AFTER_CREATE）"""

        async def hook(ctx: HookContext) -> None:
            # ⚠️ 重要：BaseRepository 传递的是 session，不是 db
            db = ctx.params.get("session") or ctx.session
            instance = ctx.params.get("instance")

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
                    # 父节点存在：tree_path = parent.tree_path + current_id + /
                    parent_path = parent.tree_path
                    new_level = parent.level + 1
                    new_tree_path = f"{parent_path}{current_id}/"
                else:
                    # 父节点不存在：作为根节点处理
                    new_tree_path = f"/{current_id}/"
                    new_level = 1

            # 更新实例并同步到数据库
            instance.tree_path = new_tree_path
            instance.level = new_level
            await db.flush()

        return hook

    def _update_tree_path_hook(self) -> HookFunc:
        """创建 tree_path 更新 Hook（移动节点）"""

        async def hook(ctx: HookContext) -> None:
            # ⚠️ 重要：BaseRepository 传递的是 session，不是 db
            db = ctx.params.get("session") or ctx.session
            data = ctx.params.get("data", {})
            instance = ctx.params.get("instance")

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

            # 计算新的 tree_path
            if new_parent_id is None:
                # 移动到根节点
                await db.flush()
                instance.tree_path = f"/{instance.id}/"
                instance.level = 1
                new_tree_path = instance.tree_path
            else:
                # 移动到新的父节点下
                parent = await db.execute(
                    select(self.model).where(self.model.id == new_parent_id)  # type: ignore[attr-defined]
                )
                parent = parent.scalar_one_or_none()

                await db.flush()

                if parent:
                    instance.tree_path = f"{parent.tree_path}{instance.id}/"
                    instance.level = parent.level + 1
                else:
                    instance.tree_path = f"/{instance.id}/"
                    instance.level = 1

                new_tree_path = instance.tree_path

            # 更新所有后代的 tree_path
            # 新路径 = 旧路径.replace(old_tree_path, new_tree_path)
            # 例如：old = "/1/5/", new = "/2/10/"
            #      descendant.tree_path = "/1/5/12/" → "/2/10/12/"
            descendants = await db.execute(
                select(self.model).where(
                    self.model.tree_path.like(f"{old_tree_path}%")  # type: ignore[attr-defined]
                )
            )
            descendants = descendants.scalars().all()

            for descendant in descendants:
                # 替换路径前缀
                descendant.tree_path = descendant.tree_path.replace(old_tree_path, new_tree_path)
                # 更新层级（level = 旧level - 旧parent.level + 新parent.level）
                old_parent_level = (old_parent_id and (await self.get_by_id(db, old_parent_id)).level) or 0
                new_parent_level = (new_parent_id and parent.level) or 0
                descendant.level = descendant.level - old_parent_level + new_parent_level

        return hook

    async def get_children(
        self,
        db: AsyncSession,
        parent_id: int | None,
        include_inactive: bool = False,
    ) -> list[T]:
        """获取直接子节点"""
        # 使用 getattr 避免泛型类型的属性访问错误
        parent_id_attr = self.model.parent_id  # type: ignore[attr-defined]
        where_clauses = [parent_id_attr == parent_id]

        # 使用软删除控制状态
        if not include_inactive:
            is_deleted_attr = getattr(self.model, "is_deleted", None)
            if is_deleted_attr is not None:
                where_clauses.append(is_deleted_attr == False)  # noqa: E712

        sort_order_attr = self.model.sort_order  # type: ignore[attr-defined]
        _, children = await self.get_list(
            db, limit=1000, where_clauses_raw=where_clauses, order_by_raw=[sort_order_attr]
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
        items = result.scalars().all()  # .scalars().all() 已返回 list，无需再次包装

        # 如果提供了 schema，自动加载关联数据
        if schema and items:
            items = await self._load_relations_for_items(db, items, schema, relation_max_depth)

        return items

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
    ) -> T | None:
        """移动节点（含循环检测）"""
        if new_parent_id is not None:
            if node_id == new_parent_id:
                raise ValueError("节点不能成为自己的父节点")

            descendants = await self.get_descendants(db, node_id)
            if any(d.id == new_parent_id for d in descendants):  # type: ignore[attr-defined]
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
