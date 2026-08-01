"""BaseRepository Hook 的数据库 CRUD 集成测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from src.core.mixins import SoftDeleteMixin
from src.database.base_repository import BaseRepository, HookContext, HookType


class HookTestModel(SQLModel, table=True):
    """测试用模型"""

    __tablename__ = "test_hook_model"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    value: int = 0


class SoftDeleteHookTestModel(SoftDeleteMixin, SQLModel, table=True):
    """软删除 Hook 测试模型"""

    __tablename__ = "test_soft_delete_hook_model"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    value: int = 0


class TestHookDatabaseIntegration:
    """测试 Hook 系统与 BaseRepository 数据库 CRUD 的集成。"""

    def setup_method(self):
        """设置测试环境"""
        self.repo = BaseRepository[HookTestModel](HookTestModel)

    @pytest.mark.asyncio
    async def test_hooks_execute_in_create(self, db_session: AsyncSession):
        """测试 create 操作时 Hook 执行"""
        before_executed = []
        after_executed = []

        def before_hook(ctx: HookContext) -> None:
            before_executed.append(True)
            # 修改数据
            if "data" in ctx.params:
                ctx.params["data"]["value"] = 100

        async def after_hook(ctx: HookContext) -> None:
            after_executed.append(True)

        self.repo.add_hook(HookType.BEFORE_CREATE, before_hook, priority=-100)
        self.repo.add_hook(HookType.AFTER_CREATE, after_hook, priority=100)

        # 执行创建操作
        data = {"name": "test", "value": 0}
        instance = await self.repo.create(db_session, data)

        assert len(before_executed) == 1
        assert len(after_executed) == 1
        assert instance.value == 100  # 被 before_hook 修改

    @pytest.mark.asyncio
    async def test_hooks_execute_in_update(self, db_session: AsyncSession):
        """测试 update 操作时 Hook 执行"""
        # 先创建一条记录
        instance = await self.repo.create(db_session, {"name": "test", "value": 0})
        await db_session.commit()

        before_executed = []
        after_executed = []

        def before_hook(ctx: HookContext) -> None:
            before_executed.append(True)

        async def after_hook(ctx: HookContext) -> None:
            after_executed.append(True)

        self.repo.add_hook(HookType.BEFORE_UPDATE, before_hook)
        self.repo.add_hook(HookType.AFTER_UPDATE, after_hook)

        # 执行更新操作
        await self.repo.update(db_session, instance.id, {"value": 200})  # type: ignore[arg-type]

        assert len(before_executed) == 1
        assert len(after_executed) == 1

    @pytest.mark.asyncio
    async def test_hooks_execute_in_delete(self, db_session: AsyncSession):
        """测试 delete 操作时 Hook 执行"""
        # 先创建一条记录
        instance = await self.repo.create(db_session, {"name": "test", "value": 0})
        await db_session.commit()

        before_executed = []
        after_executed = []

        def before_hook(ctx: HookContext) -> None:
            before_executed.append(True)

        async def after_hook(ctx: HookContext) -> None:
            after_executed.append(True)

        self.repo.add_hook(HookType.BEFORE_DELETE, before_hook)
        self.repo.add_hook(HookType.AFTER_DELETE, after_hook)

        # 执行删除操作
        await self.repo.delete(db_session, instance.id)  # type: ignore[arg-type]

        assert len(before_executed) == 1
        assert len(after_executed) == 1

    @pytest.mark.asyncio
    async def test_hooks_execute_in_delete_soft_delete_branch(self, db_session: AsyncSession):
        """测试软删除分支同样执行 delete hooks。"""
        repo = BaseRepository[SoftDeleteHookTestModel](SoftDeleteHookTestModel)
        instance = await repo.create(db_session, {"name": "soft-test", "value": 0})
        await db_session.commit()

        before_executed = []
        after_executed = []

        def before_hook(ctx: HookContext) -> None:
            before_executed.append(True)

        async def after_hook(ctx: HookContext) -> None:
            after_executed.append(True)

        repo.add_hook(HookType.BEFORE_DELETE, before_hook)
        repo.add_hook(HookType.AFTER_DELETE, after_hook)

        result = await repo.delete(db_session, instance.id)  # type: ignore[arg-type]
        deleted = await repo.get_by_id(db_session, instance.id, include_deleted=True)  # type: ignore[arg-type]

        assert result is True
        assert len(before_executed) == 1
        assert len(after_executed) == 1
        assert deleted is not None
        assert deleted.is_deleted is True
