"""
全局异常处理器

提供统一的异常处理机制，将各类异常转换为标准化的错误响应。

错误响应格式:
{
    "code": "1000",                  # 错误代码（数字码格式）
    "message": "错误消息",            # 用户友好的错误描述
    "detail": {},                    # 详细信息（可选）
    "timestamp": "2026-01-16T10:30:00Z"  # ISO 8601 时间戳
}
"""

import re
from typing import Any, cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import DatabaseError, DBAPIError, IntegrityError, SQLAlchemyError

from src.core.exceptions import (
    AppException,
    AuthException,
    ConflictException,
    NotFoundException,
    PermissionException,
    RateLimitException,
    ValidationException,
)
from src.core.logger import logger
from src.utils.timezone import timezone

# ==================== 错误响应模型 ====================


def error_response(
    code: str,
    message: str,
    detail: Any = None,
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
) -> ORJSONResponse:
    """
    构建标准化的错误响应

    Args:
        code: 错误代码（数字码格式，如 "2010"）
        message: 错误消息
        detail: 详细信息
        status_code: HTTP 状态码

    Returns:
        标准化的错误响应 ORJSONResponse
    """
    from fastapi.responses import ORJSONResponse

    response: dict[str, Any] = {
        "code": code,
        "message": message,
        "timestamp": timezone.now_utc().isoformat().replace("+00:00", "Z"),
    }

    # 自动添加 request_id 到 detail
    final_detail: dict[str, Any] = {}
    if detail is not None:
        if isinstance(detail, dict):
            final_detail = dict(cast("dict[str, Any]", detail))
        else:
            final_detail = {"info": detail}

    # 尝试获取 request_id
    try:
        from src.core.context import RequestContext

        request_id = RequestContext.get_request_id()
        if request_id and request_id != "SYSTEM":
            final_detail["request_id"] = request_id
    except (ImportError, RuntimeError):
        pass

    if final_detail:
        response["detail"] = final_detail

    return ORJSONResponse(
        status_code=status_code,
        content=response,
    )


# ==================== 自定义异常处理 ====================


async def app_exception_handler(request: Request, exc: AppException) -> ORJSONResponse:
    """
    处理所有自定义应用异常

    Args:
        request: 请求对象
        exc: 应用异常实例

    Returns:
        标准化的错误响应
    """
    # 记录错误日志
    logger.error(
        f"AppException: {exc.code} - {exc.message}",
        extra={
            "error_code": exc.code,
            "status_code": exc.status_code,
            "path": request.url.path,
            "method": request.method,
            "detail": exc.detail,
        },
    )

    return error_response(
        code=exc.code,
        message=exc.message,
        detail=exc.detail,
        status_code=exc.status_code,
    )


async def auth_exception_handler(request: Request, exc: AuthException) -> ORJSONResponse:
    """处理认证异常"""
    logger.warning(
        f"AuthException: {exc.code} - {exc.message}",
        extra={
            "error_code": exc.code,
            "path": request.url.path,
            "method": request.method,
        },
    )

    return error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
    )


async def permission_exception_handler(request: Request, exc: PermissionException) -> ORJSONResponse:
    """处理权限异常"""
    logger.warning(
        f"PermissionException: {exc.code} - {exc.message}",
        extra={
            "error_code": exc.code,
            "path": request.url.path,
            "method": request.method,
        },
    )

    return error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
    )


async def not_found_exception_handler(request: Request, exc: NotFoundException) -> ORJSONResponse:
    """处理资源未找到异常"""
    logger.info(
        f"NotFoundException: {exc.message}",
        extra={
            "error_code": exc.code,
            "path": request.url.path,
            "detail": exc.detail,
        },
    )

    return error_response(
        code=exc.code,
        message=exc.message,
        detail=exc.detail,
        status_code=exc.status_code,
    )


async def conflict_exception_handler(request: Request, exc: ConflictException) -> ORJSONResponse:
    """处理资源冲突异常"""
    logger.warning(
        f"ConflictException: {exc.code} - {exc.message}",
        extra={
            "error_code": exc.code,
            "path": request.url.path,
            "detail": exc.detail,
        },
    )

    return error_response(
        code=exc.code,
        message=exc.message,
        detail=exc.detail,
        status_code=exc.status_code,
    )


async def validation_exception_handler(request: Request, exc: ValidationException) -> ORJSONResponse:
    """处理数据验证异常"""
    logger.warning(
        f"ValidationException: {exc.code} - {exc.message}",
        extra={
            "error_code": exc.code,
            "path": request.url.path,
            "detail": exc.detail,
        },
    )

    return error_response(
        code=exc.code,
        message=exc.message,
        detail=exc.detail,
        status_code=exc.status_code,
    )


async def rate_limit_exception_handler(request: Request, exc: RateLimitException) -> ORJSONResponse:
    """处理请求频率限制异常"""
    logger.warning(
        f"RateLimitException: {exc.code} - {exc.message}",
        extra={
            "error_code": exc.code,
            "path": request.url.path,
            "method": request.method,
        },
    )

    return error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
    )


# ==================== FastAPI 内置异常处理 ====================


async def request_validation_exception_handler(request: Request, exc: RequestValidationError) -> ORJSONResponse:
    """
    处理请求参数验证异常

    Args:
        request: 请求对象
        exc: 请求验证异常实例

    Returns:
        标准化的错误响应
    """
    # 格式化验证错误
    errors: list[dict[str, str]] = []
    for error in exc.errors():
        field = " -> ".join(str(loc) for loc in error["loc"])
        errors.append(
            {
                "field": field,
                "message": error["msg"],
                "type": error["type"],
            }
        )

    logger.warning(
        f"RequestValidationError: {len(errors)} validation error(s)",
        extra={
            "path": request.url.path,
            "method": request.method,
            "errors": errors,
        },
    )

    return error_response(
        code="2004",
        message="请求参数验证失败",
        detail={"errors": errors},
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


async def pydantic_validation_exception_handler(request: Request, exc: ValidationError) -> ORJSONResponse:
    """处理 Pydantic 验证异常"""
    errors: list[dict[str, str]] = []
    for error in exc.errors():
        field = " -> ".join(str(loc) for loc in error["loc"])
        errors.append(
            {
                "field": field,
                "message": error["msg"],
                "type": error["type"],
            }
        )

    logger.warning(
        f"ValidationError: {len(errors)} error(s)",
        extra={
            "path": request.url.path,
            "errors": errors,
        },
    )

    return error_response(
        code="2004",
        message="数据验证失败",
        detail={"errors": errors},
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


# ==================== 数据库异常处理 ====================


def _parse_integrity_error(exc: IntegrityError) -> tuple[str, str, dict[str, Any] | None]:
    """
    解析数据库完整性错误，返回错误信息

    Args:
        exc: SQLAlchemy IntegrityError 实例

    Returns:
        (错误代码, 错误消息, 详细信息字典)
    """
    error_msg = str(exc.orig) if hasattr(exc, "orig") else str(exc)

    # 唯一约束冲突
    if "unique constraint" in error_msg.lower():
        # PostgreSQL: "Key (column_name)=(value) already exists"
        match = re.search(r"Key \((\w+)\)=.*already exists", error_msg)
        field = match.group(1) if match else None
        return (
            "3010",
            "数据已存在，不能重复添加" + (f"（字段：{field}）" if field else ""),
            {"field": field, "constraint": "unique"} if field else {"constraint": "unique"},
        )

    # 外键约束冲突
    if "foreign key constraint" in error_msg.lower():
        return (
            "3012",
            "删除失败：存在关联数据",
            {"constraint": "foreign_key"},
        )

    # 非空约束冲突
    if "not-null constraint" in error_msg.lower():
        # PostgreSQL: 'Null value in column "column_name" violates not-null constraint'
        match = re.search(r'column "(\w+)"', error_msg)
        field = match.group(1) if match else None
        if field:
            return (
                "2002",
                f"字段 '{field}' 不能为空",
                {"field": field, "constraint": "not_null"},
            )
        return (
            "2002",
            "必填字段不能为空",
            {"constraint": "not_null"},
        )

    # 默认：其他完整性错误
    return (
        "4020",
        "数据完整性错误",
        {"error": error_msg},
    )


def _handle_integrity_error(_request: Request, exc: IntegrityError, _detail: str) -> ORJSONResponse:
    """处理数据完整性约束冲突"""
    code, message, detail_dict = _parse_integrity_error(exc)

    # 根据 code 选择状态码
    from fastapi import status

    status_code = status.HTTP_409_CONFLICT if code in ["3010", "3012"] else status.HTTP_422_UNPROCESSABLE_ENTITY

    return error_response(
        code=code,
        message=message,
        detail=detail_dict,
        status_code=status_code,
    )


def _handle_dbapi_error(_request: Request, exc: DBAPIError, _detail: str) -> ORJSONResponse:
    """处理数据库 API 错误"""
    return error_response(
        code="5011",
        message="数据库连接失败",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _handle_database_error(_request: Request, _exc: DatabaseError, detail: str) -> ORJSONResponse:
    """处理通用数据库错误"""
    return error_response(
        code="5010",
        message=f"数据库错误: {detail}",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


# 异常处理注册表（注册表模式）
# 优势：O(1) 查找、易于扩展、符合开闭原则
SQLALCHEMY_EXCEPTION_HANDLERS: dict[type[SQLAlchemyError], Any] = {
    IntegrityError: _handle_integrity_error,
    DBAPIError: _handle_dbapi_error,
    DatabaseError: _handle_database_error,
}


async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> ORJSONResponse:
    """
    处理 SQLAlchemy 异常（使用注册表模式）

    Args:
        request: 请求对象
        exc: SQLAlchemy 异常实例

    Returns:
        标准化的错误响应
    """
    # 提取详细错误信息
    error_detail = str(exc)
    if hasattr(exc, "__cause__") and exc.__cause__:
        error_detail = f"{error_detail} | 原因: {exc.__cause__!s}"

    # 记录错误日志（使用 opt() 方法避免格式化问题）
    logger.opt(lazy=True).error(
        "SQLAlchemyError: {} - {}",
        lambda: type(exc).__name__,
        lambda: error_detail,
    )

    # 使用注册表查找处理器（O(1) 查找）
    # 按照异常类型层次查找：先找精确类型，再找父类
    for exc_type, handler in SQLALCHEMY_EXCEPTION_HANDLERS.items():
        if isinstance(exc, exc_type):
            return handler(request, exc, error_detail)  # type: ignore[arg-type]

    # 默认通用处理
    return error_response(
        code="5010",
        message=f"数据库操作失败: {error_detail}",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


# ==================== 通用异常处理 ====================


async def general_exception_handler(request: Request, exc: Exception) -> ORJSONResponse:
    """
    处理所有未捕获的异常

    Args:
        request: 请求对象
        exc: 异常实例

    Returns:
        标准化的错误响应
    """
    logger.exception(
        f"Unhandled Exception: {type(exc).__name__}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "error": str(exc),
        },
    )

    # DEBUG 模式显示详细错误，否则显示通用消息
    from src.core.conf import settings

    message = str(exc) if settings.APP_DEBUG else "服务器内部错误"

    return error_response(
        code="5000",
        message=message,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


# ==================== 注册函数 ====================


def register_exception_handlers(app: FastAPI) -> None:
    """
    注册所有异常处理器

    Args:
        app: FastAPI 应用实例
    """
    # 自定义应用异常
    app.add_exception_handler(AppException, app_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(AuthException, auth_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(PermissionException, permission_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(NotFoundException, not_found_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ConflictException, conflict_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationException, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RateLimitException, rate_limit_exception_handler)  # type: ignore[arg-type]

    # FastAPI 内置异常
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationError, pydantic_validation_exception_handler)  # type: ignore[arg-type]

    # 数据库异常
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)  # type: ignore[arg-type]

    # 通用异常（兜底处理）
    app.add_exception_handler(Exception, general_exception_handler)

    logger.info("Exception handlers registered")
