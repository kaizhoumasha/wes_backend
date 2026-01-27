"""
审计日志服务

提供审计日志的创建和查询功能
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.sys.models.audit_log import AuditLog, OperaStatus
from src.app.sys.repositories.audit_log_repository import audit_log_repository
from src.core.base_service import BaseService
from src.core.logger import logger
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone
from src.utils.audit import get_current_username, get_request_id, get_request_info


class AuditLogService(BaseService[AuditLog, BaseRepository]):
    """审计日志服务类"""

    def __init__(self, repo: BaseRepository = audit_log_repository):
        super().__init__(repo)

    async def create_audit_log(
        self,
        db: AsyncSession,
        *,
        method: str,
        title: str,
        path: str,
        args: dict | None = None,
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
        request_info = get_request_info()
        request_id = get_request_id() or "unknown"
        username = get_current_username()

        # 构建审计日志数据
        audit_data = {
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
        }

        try:
            # 创建审计日志
            return await self.repo.create(db, audit_data)
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
