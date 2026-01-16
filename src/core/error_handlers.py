"""
全局异常处理器

提供统一的异常处理机制，将各类异常转换为标准化的错误响应。

错误响应格式:
{
    "code": "ERROR_CODE",           # 错误代码
    "message": "错误消息",            # 用户友好的错误描述
    "detail": {},                    # 详细信息（可选）
    "timestamp": "2026-01-16T10:30:00Z"  # ISO 8601 时间戳
}
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

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
        code: 错误代码
        message: 错误消息
        detail: 详细信息
        status_code: HTTP 状态码

    Returns:
        标准化的错误响应 ORJSONResponse
    """
    from fastapi.responses import ORJSONResponse

    response = {
        "code": code,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    if detail is not None:
        response["detail"] = detail

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


async def auth_exception_handler(
    request: Request, exc: AuthException
) -> ORJSONResponse:
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


async def permission_exception_handler(
    request: Request, exc: PermissionException
) -> ORJSONResponse:
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


async def not_found_exception_handler(
    request: Request, exc: NotFoundException
) -> ORJSONResponse:
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


async def conflict_exception_handler(
    request: Request, exc: ConflictException
) -> ORJSONResponse:
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


async def validation_exception_handler(
    request: Request, exc: ValidationException
) -> ORJSONResponse:
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


async def rate_limit_exception_handler(
    request: Request, exc: RateLimitException
) -> ORJSONResponse:
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


async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> ORJSONResponse:
    """
    处理请求参数验证异常

    Args:
        request: 请求对象
        exc: 请求验证异常实例

    Returns:
        标准化的错误响应
    """
    # 格式化验证错误
    errors = []
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
        code="VALIDATION_ERROR",
        message="请求参数验证失败",
        detail={"errors": errors},
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


async def pydantic_validation_exception_handler(
    request: Request, exc: ValidationError
) -> ORJSONResponse:
    """处理 Pydantic 验证异常"""
    errors = []
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
        code="VALIDATION_ERROR",
        message="数据验证失败",
        detail={"errors": errors},
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


# ==================== 数据库异常处理 ====================


async def sqlalchemy_exception_handler(
    request: Request, exc: SQLAlchemyError
) -> ORJSONResponse:
    """
    处理 SQLAlchemy 异常

    Args:
        request: 请求对象
        exc: SQLAlchemy 异常实例

    Returns:
        标准化的错误响应
    """
    # 记录详细错误信息
    error_detail = str(exc)
    if hasattr(exc, "__cause__") and exc.__cause__:
        error_detail = f"{error_detail} | 原因: {str(exc.__cause__)}"

    logger.error(
        f"SQLAlchemyError: {type(exc).__name__} - {error_detail}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "error": error_detail,
        },
    )

    # 处理唯一约束冲突
    if isinstance(exc, IntegrityError):
        return error_response(
            code="INTEGRITY_ERROR",
            message=f"数据完整性约束冲突: {error_detail}",
            status_code=status.HTTP_409_CONFLICT,
        )

    # 处理 DBAPIError，提取原始数据库错误
    if isinstance(exc, DBAPIError):
        orig_error = exc.orig
        return error_response(
            code="DATABASE_ERROR",
            message=f"数据库错误: {type(orig_error).__name__}: {str(orig_error)}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return error_response(
        code="DATABASE_ERROR",
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

    return error_response(
        code="INTERNAL_ERROR",
        message="服务器内部错误"
        if not logger.level <= 10
        else str(exc),  # DEBUG 模式显示详细错误
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
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(AuthException, auth_exception_handler)
    app.add_exception_handler(PermissionException, permission_exception_handler)
    app.add_exception_handler(NotFoundException, not_found_exception_handler)
    app.add_exception_handler(ConflictException, conflict_exception_handler)
    app.add_exception_handler(ValidationException, validation_exception_handler)
    app.add_exception_handler(RateLimitException, rate_limit_exception_handler)

    # FastAPI 内置异常
    app.add_exception_handler(
        RequestValidationError, request_validation_exception_handler
    )
    app.add_exception_handler(ValidationError, pydantic_validation_exception_handler)

    # 数据库异常
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)

    # 通用异常（兜底处理）
    app.add_exception_handler(Exception, general_exception_handler)

    logger.info("Exception handlers registered")
