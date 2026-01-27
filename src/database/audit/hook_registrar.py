"""
审计日志 Hook 注册器

负责为 Repository 自动注册审计日志 Hook，将审计日志记录委托给 audit_log_service。
遵循 DRY 原则，避免重复实现审计逻辑。
"""

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.database.hooks import HookContext, HookFunc, HookManager, HookType


class AuditHookRegistrar:
    """
    审计日志 Hook 注册器

    负责为 Repository 注册审计日志 Hook，将实际的审计日志写入委托给 audit_log_service。
    """

    def __init__(self, model_name: str, pk_column: str, hook_manager: HookManager):
        """
        初始化审计 Hook 注册器

        Args:
            model_name: 模型名称
            pk_column: 主键列名
            hook_manager: Hook 管理器
        """
        self._model_name = model_name
        self._pk_column = pk_column
        self._hook_manager = hook_manager

    def register_hooks(self) -> None:
        """
        注册审计日志 Hook

        在 AFTER_CREATE、AFTER_UPDATE、AFTER_DELETE 时记录审计日志
        """
        # 注册 AFTER_CREATE Hook
        self._hook_manager.add_hook(
            HookType.AFTER_CREATE,
            self._create_audit_log_hook("create"),
            priority=100,  # 最低优先级，在所有其他 Hook 之后执行
        )

        # 注册 AFTER_UPDATE Hook
        self._hook_manager.add_hook(
            HookType.AFTER_UPDATE,
            self._create_audit_log_hook("update"),
            priority=100,
        )

        # 注册 AFTER_DELETE Hook
        self._hook_manager.add_hook(
            HookType.AFTER_DELETE,
            self._create_audit_log_hook("delete"),
            priority=100,
        )

    def _create_audit_log_hook(self, operation: str) -> HookFunc:
        """
        创建审计日志 Hook 函数（支持后台任务模式）

        优先使用 BackgroundTasks 后台任务模式，提升响应速度。
        如果 BackgroundTasks 不可用，则降级为同步执行模式。

        Args:
            operation: 操作类型（create/update/delete）

        Returns:
            Hook 函数
        """

        async def audit_log_hook(ctx: HookContext) -> None:
            from src.utils.background_tasks import get_background_tasks

            # 准备审计数据（可序列化）
            audit_data = self._prepare_audit_data(ctx, operation)

            # 检查是否有 BackgroundTasks 可用
            background_tasks = get_background_tasks()

            if background_tasks:
                # 使用后台任务（异步非阻塞，提升响应速度）
                background_tasks.add_task(
                    self._write_audit_log_background,
                    operation=operation,
                    model_name=self._model_name,
                    **audit_data,
                )
            else:
                # 降级为同步执行（保持向后兼容）
                await self._write_audit_log_sync(ctx.session, operation, audit_data)

        return audit_log_hook

    def _prepare_audit_data(self, ctx: HookContext, operation: str) -> dict[str, Any]:
        """
        准备审计数据（可序列化格式）

        将审计所需的数据转换为可序列化的字典格式，
        以便在后台任务中使用（后台任务无法访问原始 ORM 对象）。

        Args:
            ctx: Hook 执行上下文
            operation: 操作类型（create/update/delete）

        Returns:
            包含审计数据的字典，包括：
            - record_id: 记录 ID
            - data: 审计数据（根据操作类型不同而不同）
            - cost_time: 操作耗时（秒）
        """
        instance = ctx.params.get("instance")
        data = ctx.params.get("data")
        record_id = getattr(instance, self._pk_column, None) if instance else None

        # 计算操作耗时
        start_time = ctx.params.get("_audit_start_time")
        cost_time = 0.0
        if start_time:
            cost_time = time.time() - start_time

        # 准备审计数据（根据操作类型）
        audit_data = None

        if operation == "create":
            # 创建操作：记录所有创建数据
            audit_data = data

        elif operation == "update":
            # 更新操作：记录修改前后的值对比
            old_values = ctx.params.get("_audit_old_values", {})
            if data and old_values:
                audit_data = {}
                for key, new_value in data.items():
                    old_value = old_values.get(key)
                    # 只记录实际发生变化的字段
                    if old_value != new_value:
                        audit_data[key] = {
                            "old": str(old_value) if old_value is not None else None,
                            "new": str(new_value) if new_value is not None else None,
                        }

        elif operation == "delete":
            # 删除操作：记录被删除的原始数据
            old_values = ctx.params.get("_audit_old_values", {})
            if old_values:
                # 过滤掉不需要的字段
                sensitive_fields = {"password", "token", "secret", "key"}
                audit_data = {
                    k: str(v) if v is not None else None
                    for k, v in old_values.items()
                    if k not in sensitive_fields and not k.startswith("_")
                }

        return {
            "record_id": record_id,
            "data": audit_data,
            "cost_time": cost_time,
        }

    async def _write_audit_log_background(
        self,
        operation: str,
        model_name: str,
        record_id: int | None,
        data: dict[str, Any] | None,
        cost_time: float,
    ) -> None:
        """
        后台任务：写入审计日志

        在后台任务中执行，不阻塞主请求。
        创建新的数据库会话来写入审计日志。

        Args:
            operation: 操作类型（create/update/delete）
            model_name: 模型名称
            record_id: 记录 ID
            data: 审计数据
            cost_time: 操作耗时（秒）

        Note:
            - 此方法在后台任务中执行，原请求的数据库会话已关闭
            - 需要创建新的数据库会话
            - 失败不影响主业务，只记录错误日志
        """
        try:
            from src.app.sys.services.audit_service import audit_log_service
            from src.database.db import get_db_context

            # 创建新的数据库会话（后台任务中原会话已关闭）
            async with get_db_context() as session:
                await audit_log_service.create_operation_log(
                    session,
                    operation=operation,
                    model_name=model_name,
                    record_id=record_id,
                    data=data,
                    success=True,
                    cost_time=cost_time,
                )
                await session.commit()
        except Exception as e:
            # 审计日志失败不应该影响主业务
            logger.error(f"后台写入审计日志失败 [{model_name}:{record_id}]: {e}")

    async def _write_audit_log_sync(
        self,
        session: AsyncSession,
        operation: str,
        audit_data: dict[str, Any],
    ) -> None:
        """
        同步模式：写入审计日志

        使用现有的数据库会话写入审计日志。
        用于降级场景（BackgroundTasks 不可用时）。

        Args:
            session: 数据库会话
            operation: 操作类型（create/update/delete）
            audit_data: 审计数据字典（包含 record_id、data、cost_time）

        Note:
            - 使用现有会话，不需要 commit
            - 失败不影响主业务，只记录错误日志
        """
        try:
            from src.app.sys.services.audit_service import audit_log_service

            await audit_log_service.create_operation_log(
                session,
                operation=operation,
                model_name=self._model_name,
                record_id=audit_data.get("record_id"),
                data=audit_data.get("data"),
                success=True,
                cost_time=audit_data.get("cost_time", 0.0),
            )
        except Exception as e:
            # 审计日志失败不应该影响主业务
            logger.error(f"同步写入审计日志失败 [{self._model_name}]: {e}")
