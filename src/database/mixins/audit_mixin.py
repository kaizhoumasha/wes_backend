"""
Repository Audit Mixin

提供审计日志功能的 Mixin,从 BaseRepository 中提取以遵循单一职责原则
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.database.hooks import HookContext, HookFunc, HookType


class AuditMixin:
    """
    审计日志 Mixin

    为 Repository 提供自动审计日志功能
    """

    _model_name: str
    _pk_column: str

    def _has_audit_model_mixin(self) -> bool:
        """
        检测模型是否混入了 AuditModelMixin

        Returns:
            如果模型混入了 AuditModelMixin 则返回 True
        """
        return any(base.__name__ in ("AuditModelMixin", "AuditMixin") for base in self.model.__mro__)

    def _register_audit_log_hooks(self) -> None:
        """
        注册审计日志 Hook

        在 AFTER_CREATE、AFTER_UPDATE、AFTER_DELETE 时记录审计日志
        """
        self.add_hook(
            HookType.AFTER_CREATE,
            self._create_audit_log_hook("create"),
            priority=100,
        )

        self.add_hook(
            HookType.AFTER_UPDATE,
            self._create_audit_log_hook("update"),
            priority=100,
        )

        self.add_hook(
            HookType.AFTER_DELETE,
            self._create_audit_log_hook("delete"),
            priority=100,
        )

    def _create_audit_log_hook(self, operation: str) -> HookFunc:
        """
        创建审计日志 Hook 函数（支持后台任务模式）

        Args:
            operation: 操作类型（create/update/delete）

        Returns:
            Hook 函数
        """

        async def audit_log_hook(ctx: HookContext) -> None:
            from src.utils.background_tasks import get_background_tasks

            audit_data = self._prepare_audit_data(ctx, operation)
            background_tasks = get_background_tasks()

            if background_tasks:
                background_tasks.add_task(
                    self._write_audit_log_background,
                    operation=operation,
                    model_name=self._model_name,
                    **audit_data,
                )
            else:
                await self._write_audit_log_sync(ctx.session, operation, audit_data)

        return audit_log_hook

    def _prepare_audit_data(self, ctx: HookContext, operation: str) -> dict[str, Any]:
        """
        准备审计数据（可序列化格式）

        Args:
            ctx: Hook 执行上下文
            operation: 操作类型（create/update/delete）

        Returns:
            包含审计数据的字典
        """
        import time

        instance = ctx.params.get("instance")
        data = ctx.params.get("data")
        record_id = getattr(instance, self._pk_column, None) if instance else None

        start_time = ctx.params.get("_audit_start_time")
        cost_time = 0.0
        if start_time:
            cost_time = time.time() - start_time

        audit_data = None

        if operation == "create":
            audit_data = data

        elif operation == "update":
            old_values = ctx.params.get("_audit_old_values", {})
            if data and old_values:
                audit_data = {}
                for key, new_value in data.items():
                    old_value = old_values.get(key)
                    if old_value != new_value:
                        audit_data[key] = {
                            "old": str(old_value) if old_value is not None else None,
                            "new": str(new_value) if new_value is not None else None,
                        }

        elif operation == "delete":
            old_values = ctx.params.get("_audit_old_values", {})
            if old_values:
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

        Args:
            operation: 操作类型
            model_name: 模型名称
            record_id: 记录 ID
            data: 审计数据
            cost_time: 操作耗时
        """
        try:
            from src.app.sys.services.audit_service import audit_log_service
            from src.database.db import get_db_context

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
            logger.error(f"后台写入审计日志失败 [{model_name}:{record_id}]: {e}")

    async def _write_audit_log_sync(
        self,
        session: AsyncSession,
        operation: str,
        audit_data: dict[str, Any],
    ) -> None:
        """
        同步模式：写入审计日志

        Args:
            session: 数据库会话
            operation: 操作类型
            audit_data: 审计数据字典
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
            logger.error(f"同步写入审计日志失败 [{self._model_name}]: {e}")


__all__ = ["AuditMixin"]
