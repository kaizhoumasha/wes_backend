"""
测试 BaseRepository 的 Hook 系统

验证 Hook 系统的核心功能：
- Hook 注册和执行
- 优先级排序
- 条件执行
- 错误处理
- 异步和同步 Hook
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from src.database.base_repository import (
    BaseRepository,
    Hook,
    HookContext,
    HookManager,
    HookType,
)


class HookTestModel(SQLModel, table=True):
    """测试用模型"""

    __tablename__ = "test_hook_model"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    value: int = 0


class TestHookManager:
    """测试 HookManager 类"""

    def setup_method(self):
        """设置测试环境"""
        self.manager = HookManager()

    def test_add_hook(self):
        """测试添加 Hook"""

        def test_hook(ctx: HookContext) -> None:
            pass

        self.manager.add_hook(HookType.BEFORE_CREATE, test_hook)

        assert len(self.manager.hooks[HookType.BEFORE_CREATE]) == 1
        assert self.manager.hooks[HookType.BEFORE_CREATE][0].func == test_hook

    def test_hook_priority_sorting(self):
        """测试 Hook 优先级排序"""
        execution_order = []

        def hook1(ctx: HookContext) -> None:
            execution_order.append(1)

        def hook2(ctx: HookContext) -> None:
            execution_order.append(2)

        def hook3(ctx: HookContext) -> None:
            execution_order.append(3)

        # 添加 Hook，优先级：hook2(0) > hook1(5) > hook3(10)
        self.manager.add_hook(HookType.BEFORE_CREATE, hook1, priority=5)
        self.manager.add_hook(HookType.BEFORE_CREATE, hook2, priority=0)
        self.manager.add_hook(HookType.BEFORE_CREATE, hook3, priority=10)

        # 验证排序
        hooks = self.manager.hooks[HookType.BEFORE_CREATE]
        assert hooks[0].func == hook2  # priority=0
        assert hooks[1].func == hook1  # priority=5
        assert hooks[2].func == hook3  # priority=10

    @pytest.mark.asyncio
    async def test_execute_hooks_sync(self):
        """测试执行同步 Hook"""
        executed = []

        def sync_hook(ctx: HookContext) -> None:
            executed.append("sync")
            ctx.results["sync"] = True

        self.manager.add_hook(HookType.BEFORE_CREATE, sync_hook)

        context = HookContext(session=None, params={}, results={})  # type: ignore[arg-type]
        await self.manager.execute_hooks(HookType.BEFORE_CREATE, context)

        assert "sync" in executed
        assert context.results["sync"] is True

    @pytest.mark.asyncio
    async def test_execute_hooks_async(self):
        """测试执行异步 Hook"""
        executed = []

        async def async_hook(ctx: HookContext) -> None:
            executed.append("async")
            ctx.results["async"] = True

        self.manager.add_hook(HookType.BEFORE_CREATE, async_hook)

        context = HookContext(session=None, params={}, results={})  # type: ignore[arg-type]
        await self.manager.execute_hooks(HookType.BEFORE_CREATE, context)

        assert "async" in executed
        assert context.results["async"] is True

    @pytest.mark.asyncio
    async def test_execute_hooks_with_condition(self):
        """测试条件执行 Hook"""
        executed = []

        def conditional_hook(ctx: HookContext) -> None:
            executed.append("conditional")

        def condition(ctx: HookContext) -> bool:
            return ctx.params.get("execute", False)

        self.manager.add_hook(HookType.BEFORE_CREATE, conditional_hook, condition=condition)

        # 条件不满足，不执行
        context1 = HookContext(session=None, params={"execute": False}, results={})  # type: ignore[arg-type]
        await self.manager.execute_hooks(HookType.BEFORE_CREATE, context1)
        assert len(executed) == 0

        # 条件满足，执行
        context2 = HookContext(session=None, params={"execute": True}, results={})  # type: ignore[arg-type]
        await self.manager.execute_hooks(HookType.BEFORE_CREATE, context2)
        assert len(executed) == 1

    @pytest.mark.asyncio
    async def test_execute_hooks_with_error_handler(self):
        """测试 Hook 错误处理"""
        error_handled = []

        def failing_hook(ctx: HookContext) -> None:
            raise ValueError("Test error")

        def error_handler(e: Exception, ctx: HookContext) -> None:
            error_handled.append(str(e))

        self.manager.add_hook(
            HookType.BEFORE_CREATE, failing_hook, error_handler=error_handler
        )

        context = HookContext(session=None, params={}, results={})  # type: ignore[arg-type]
        await self.manager.execute_hooks(HookType.BEFORE_CREATE, context)

        assert len(error_handled) == 1
        assert "Test error" in error_handled[0]

    @pytest.mark.asyncio
    async def test_execute_hooks_without_error_handler(self):
        """测试没有错误处理器时抛出异常"""

        def failing_hook(ctx: HookContext) -> None:
            raise ValueError("Test error")

        self.manager.add_hook(HookType.BEFORE_CREATE, failing_hook)

        context = HookContext(session=None, params={}, results={})  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="Test error"):
            await self.manager.execute_hooks(HookType.BEFORE_CREATE, context)


class TestHookIntegration:
    """测试 Hook 系统与 BaseRepository 的集成"""

    def setup_method(self):
        """设置测试环境"""
        self.repo = BaseRepository[HookTestModel](HookTestModel)

    def test_repository_has_hook_manager(self):
        """测试 Repository 包含 HookManager"""
        assert isinstance(self.repo.hook_manager, HookManager)

    def test_add_custom_hook(self):
        """测试添加自定义 Hook"""
        executed = []

        def custom_hook(ctx: HookContext) -> None:
            executed.append("custom")

        self.repo.add_hook(HookType.BEFORE_CREATE, custom_hook)

        assert len(self.repo.hook_manager.hooks[HookType.BEFORE_CREATE]) > 0

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
