"""
审计日志服务

提供审计日志的创建和查询功能
"""

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.sys.models.audit_log import AuditLog, OperaStatus
from src.app.sys.repositories.audit_log_repository import audit_log_repository
from src.core.base_service import BaseService
from src.core.logger import logger
from src.database.base_repository import BaseRepository
from src.utils.audit import get_current_username, get_request_id, get_request_info
from src.utils.timezone import timezone


class AuditLogService(BaseService[AuditLog, BaseRepository]):
    """审计日志服务类"""

    def __init__(self, repo: BaseRepository[AuditLog] = audit_log_repository):
        cast("Any", BaseService.__init__)(self, repo)

    @staticmethod
    def _as_non_empty_text(value: Any) -> str | None:
        if value is None:
            return None

        text = str(value).strip()
        return text or None

    def _build_change_summary(
        self,
        *,
        operation: str | None,
        changes: Mapping[str, Any] | None,
    ) -> str | None:
        normalized_operation = (operation or "").strip().lower()
        labels = {
            "create": "创建记录",
            "update": "更新记录",
            "delete": "删除记录",
            "read": "读取记录",
        }

        base_summary = labels.get(normalized_operation)
        if changes is None:
            return base_summary

        field_names = [field for field in changes if isinstance(field, str) and field and not field.startswith("_")]
        if not field_names:
            return base_summary

        preview = "、".join(field_names[:3])
        if len(field_names) > 3:
            preview = f"{preview} 等 {len(field_names)} 个字段"

        if normalized_operation == "update":
            return f"更新字段：{preview}"
        if normalized_operation == "create":
            return f"创建记录，写入字段：{preview}"
        if normalized_operation == "delete":
            return f"删除记录，保留快照字段：{preview}"
        if base_summary:
            return f"{base_summary}：{preview}"
        return preview

    def _extract_audit_dimensions(self, args: dict[str, Any] | None) -> dict[str, Any]:
        if not args:
            return {
                "object_type": None,
                "action": None,
                "object_id": None,
                "change_summary": None,
            }

        changes = args.get("changes")
        normalized_changes = changes if isinstance(changes, Mapping) else None

        return {
            "object_type": self._as_non_empty_text(args.get("model")),
            "action": self._as_non_empty_text(args.get("operation")),
            "object_id": self._as_non_empty_text(args.get("record_id")),
            "change_summary": self._build_change_summary(
                operation=self._as_non_empty_text(args.get("operation")),
                changes=normalized_changes,
            ),
        }

    async def create_audit_log(
        self,
        db: AsyncSession,
        *,
        method: str,
        title: str,
        path: str,
        args: dict[str, Any] | None = None,
        status: OperaStatus = OperaStatus.SUCCESS,
        code: str = "200",
        msg: str | None = None,
        cost_time: float = 0.0,
    ) -> AuditLog:
        """
        创建审计日志

        Args:
            db: 数据库会话
            method: HTTP 方法
            title: 操作标题
            path: 请求路径
            args: 请求参数
            status: 操作状态
            code: 响应代码
            msg: 响应消息
            cost_time: 耗时（秒）

        Returns:
            创建的审计日志对象
        """
        # 获取请求信息
        request_info = cast("dict[str, Any]", get_request_info())
        request_id = get_request_id() or "unknown"
        username = get_current_username()
        audit_dimensions = self._extract_audit_dimensions(args)

        # 构建审计日志数据
        audit_data: dict[str, Any] = {
            "trace_id": request_id,
            "username": username,
            "method": method,
            "title": title,
            "path": path,
            "ip": request_info.get("ip") or "unknown",
            "country": request_info.get("country"),
            "region": request_info.get("region"),
            "city": request_info.get("city"),
            "user_agent": request_info.get("user_agent") or "unknown",
            "os": request_info.get("os"),
            "browser": request_info.get("browser"),
            "device": request_info.get("device"),
            "args": args,
            "status": status,
            "code": code,
            "msg": msg,
            "cost_time": cost_time,
            "opera_time": timezone.now(),
            **audit_dimensions,
        }

        try:
            # 创建审计日志
            audit_log = await self._repo_base.create(db, audit_data)
            if audit_log is None:
                raise RuntimeError("创建审计日志失败")
            return cast("AuditLog", audit_log)
        except Exception as e:
            logger.error(f"创建审计日志失败: {e}")
            raise

    async def create_operation_log(
        self,
        db: AsyncSession,
        *,
        operation: str,
        model_name: str,
        record_id: int | None = None,
        data: dict[str, Any] | None = None,
        success: bool = True,
        error_msg: str | None = None,
        cost_time: float = 0.0,
    ) -> AuditLog | None:
        """
        创建操作日志（用于 Repository Hook）

        Args:
            db: 数据库会话
            operation: 操作类型（create/update/delete）
            model_name: 模型名称
            record_id: 记录 ID
            data: 操作数据
            success: 是否成功
            error_msg: 错误消息
            cost_time: 操作耗时（秒）

        Returns:
            创建的审计日志对象，如果创建失败则返回 None
        """
        try:
            from src.utils.audit import get_request_method

            title = f"{operation.upper()} {model_name}"
            if record_id:
                title += f" (ID: {record_id})"

            # 构建参数信息 - 记录更详细的数据
            args: dict[str, Any] = {
                "model": model_name,
                "operation": operation,
            }
            if record_id:
                args["record_id"] = str(record_id)

            # 记录实际修改的数据（而不是所有数据）
            if data:
                # 过滤掉敏感字段
                sensitive_fields = {"password", "token", "secret", "key"}
                filtered_data = {k: v for k, v in data.items() if k not in sensitive_fields and not k.startswith("_")}
                args["changes"] = filtered_data

            status = OperaStatus.SUCCESS if success else OperaStatus.FAIL
            code = "200" if success else "500"

            # 获取实际的 HTTP 方法，如果无法获取则使用操作类型映射
            http_method = get_request_method()
            if not http_method:
                # 根据操作类型映射到 HTTP 方法
                method_mapping = {
                    "create": "POST",
                    "update": "PUT",
                    "delete": "DELETE",
                    "read": "GET",
                }
                http_method = method_mapping.get(operation.lower(), "POST")

            return await self.create_audit_log(
                db,
                method=http_method,
                title=title,
                path=f"/repository/{model_name.lower()}",
                args=args,
                status=status,
                code=code,
                msg=error_msg,
                cost_time=cost_time,
            )
        except Exception as e:
            # 审计日志创建失败不应该影响主业务
            logger.error(f"创建操作日志失败: {e}")
            return None


# 全局审计日志服务实例
audit_log_service = AuditLogService()


__all__ = ["AuditLogService", "audit_log_service"]
