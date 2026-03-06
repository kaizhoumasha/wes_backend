"""
全局异常类定义

定义应用中所有自定义异常类，提供统一的错误处理机制。

异常层级结构:
    Exception (Python 基础异常)
        └── AppException (应用异常基类)
                ├── BusinessException (业务逻辑异常)
                ├── AuthException (认证相关异常)
                ├── PermissionException (权限相关异常)
                └── ValidationException (数据验证异常)

错误码体系（与 response_code.py 对齐）:
    - 1xxx: 成功响应
    - 2xxx: 客户端错误（参数、权限等）
    - 3xxx: 资源相关错误（不存在、冲突等）
    - 4xxx: 业务逻辑错误
    - 5xxx: 服务器内部错误
    - 8xxx: 第三方服务错误
    - 9xxx: 其他错误
"""

from typing import Any


class AppException(Exception):
    """
    应用异常基类

    所有自定义异常的基类，提供统一的异常处理接口。
    """

    # 默认 HTTP 状态码
    status_code: int = 500

    # 错误代码（与 response_code.py 对齐的数字码）
    code: str = "5000"

    # 错误消息（面向用户）
    message: str = "服务器内部错误"

    # 详细信息（可选，用于调试）
    detail: dict[str, Any] | None = None

    def __init__(
        self,
        message: str | None = None,
        code: str | None = None,
        status_code: int | None = None,
        detail: dict[str, Any] | None = None,
    ):
        """
        初始化异常

        Args:
            message: 错误消息
            code: 错误代码（数字码格式，如 "2000"）
            status_code: HTTP 状态码
            detail: 详细信息
        """
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if detail is not None:
            self.detail = detail
        super().__init__(self.message)


class BusinessException(AppException):
    """
    业务逻辑异常

    用于处理业务逻辑中的错误情况。

    示例:
        raise BusinessException("用户名已存在", code="3010")
    """

    status_code = 400
    code = "4000"
    message = "业务逻辑错误"


# ==================== 认证相关异常 (2xxx) ====================


class AuthException(AppException):
    """
    认证异常基类

    用于处理用户认证相关的错误。
    """

    status_code = 401
    code = "2010"
    message = "认证失败"


class UnauthorizedException(AuthException):
    """未认证异常（未登录）"""

    code = "2010"
    message = "请先登录"


class InvalidTokenException(AuthException):
    """无效 Token 异常"""

    code = "2012"
    message = "Token 无效或已过期"


class InvalidCredentialsException(AuthException):
    """无效凭证异常（用户名或密码错误）"""

    code = "2011"
    message = "用户名或密码错误"


class TokenExpiredException(AuthException):
    """Token 过期异常"""

    code = "2013"
    message = "Token 已过期，请重新登录"


class TokenMissingException(AuthException):
    """缺少 Token 异常"""

    code = "2014"
    message = "缺少访问令牌"


# ==================== 权限相关异常 (2xxx) ====================


class PermissionException(AppException):
    """
    权限异常基类

    用于处理用户权限相关的错误。
    """

    status_code = 403
    code = "2020"
    message = "权限不足"


class ForbiddenException(PermissionException):
    """禁止访问异常（无权限）"""

    code = "2020"
    message = "您没有权限访问该资源"


class AdminRequiredException(PermissionException):
    """需要管理员权限异常"""

    code = "2021"
    message = "此操作需要管理员权限"


class PermissionDeniedException(PermissionException):
    """权限不足异常"""

    code = "2022"
    message = "权限不足"


# ==================== 资源相关异常 (3xxx) ====================


class ResourceException(AppException):
    """
    资源异常基类

    用于处理资源相关的错误。
    """

    status_code = 404
    code = "3000"
    message = "资源错误"


class NotFoundException(ResourceException):
    """资源未找到异常"""

    code = "3000"
    message = "请求的资源不存在"

    def __init__(
        self,
        message: str | None = None,
        resource_type: str | None = None,
        resource_id: Any | None = None,
        **kwargs,
    ):
        """
        初始化资源未找到异常

        Args:
            message: 错误消息
            resource_type: 资源类型（如 "User", "Order"）
            resource_id: 资源 ID
        """
        if resource_type and resource_id:
            if message is None:
                message = f"{resource_type} (ID: {resource_id}) 不存在"
            detail = {"resource_type": resource_type, "resource_id": str(resource_id)}
            super().__init__(message=message, detail=detail, **kwargs)
        else:
            super().__init__(message=message, **kwargs)


class ConflictException(AppException):
    """资源冲突异常（如重复创建）"""

    status_code = 409
    code = "3012"
    message = "资源冲突"


class DuplicateException(ConflictException):
    """重复资源异常"""

    code = "3010"
    message = "资源已存在"

    def __init__(
        self,
        message: str | None = None,
        field: str | None = None,
        value: Any | None = None,
        **kwargs,
    ):
        """
        初始化重复资源异常

        Args:
            message: 错误消息
            field: 冲突字段
            value: 冲突值
        """
        if field and value:
            if message is None:
                message = f"{field} '{value}' 已存在"
            detail = {"field": field, "value": str(value)}
            super().__init__(message=message, detail=detail, **kwargs)
        else:
            super().__init__(message=message, **kwargs)


class OptimisticLockException(ConflictException):
    """
    乐观锁异常

    当检测到并发修改时抛出，表示记录已被其他用户修改。

    使用场景:
        - 用户 A 和用户 B 同时读取同一条记录
        - 用户 A 先提交修改
        - 用户 B 提交修改时检测到版本号不匹配
        - 抛出此异常，提示用户刷新后重试

    示例:
        raise OptimisticLockException(
            resource_type="Product",
            resource_id=123,
            current_version=2,
            provided_version=1
        )
    """

    code = "3012"
    message = "记录已被其他用户修改，请刷新后重试"

    def __init__(
        self,
        message: str | None = None,
        resource_type: str | None = None,
        resource_id: Any | None = None,
        current_version: int | None = None,
        provided_version: int | None = None,
        **kwargs,
    ):
        """
        初始化乐观锁异常

        Args:
            message: 错误消息
            resource_type: 资源类型（如 "User", "Order"）
            resource_id: 资源 ID
            current_version: 当前数据库中的版本号
            provided_version: 客户端提供的版本号
        """
        if resource_type and resource_id:
            if message is None:
                message = f"{resource_type} (ID: {resource_id}) 已被其他用户修改，请刷新后重试"
            detail = {
                "resource_type": resource_type,
                "resource_id": str(resource_id),
            }
            if current_version is not None:
                detail["current_version"] = str(current_version)
            if provided_version is not None:
                detail["provided_version"] = str(provided_version)
            super().__init__(message=message, detail=detail, **kwargs)
        else:
            super().__init__(message=message, **kwargs)


class ResourceGoneException(ResourceException):
    """资源已被删除异常"""

    status_code = 410
    code = "3021"
    message = "资源已被删除"


class ResourceLockedException(ResourceException):
    """资源已被锁定异常"""

    status_code = 423
    code = "3020"
    message = "资源已被锁定"


# ==================== 数据验证异常 (2xxx) ====================


class ValidationException(AppException):
    """
    数据验证异常

    用于处理数据验证失败的情况。
    """

    status_code = 422
    code = "2004"
    message = "数据验证失败"


class InvalidParameterException(ValidationException):
    """无效参数异常"""

    code = "2001"
    message = "参数无效"

    def __init__(
        self,
        message: str | None = None,
        field: str | None = None,
        **kwargs,
    ):
        """
        初始化无效参数异常

        Args:
            message: 错误消息
            field: 无效参数字段
        """
        if field:
            if message is None:
                message = f"参数 '{field}' 无效"
            detail = {"field": field}
            super().__init__(message=message, detail=detail, **kwargs)
        else:
            super().__init__(message=message, **kwargs)


class MissingParameterException(ValidationException):
    """缺少必需参数异常"""

    code = "2002"
    message = "缺少必需参数"


class InvalidFormatException(ValidationException):
    """数据格式错误异常"""

    code = "2003"
    message = "数据格式错误"


class BadRequestException(AppException):
    """请求参数错误异常"""

    status_code = 400
    code = "2000"
    message = "请求参数错误"


# ==================== 服务相关异常 (5xxx, 9xxx) ====================


class ServiceUnavailableException(AppException):
    """服务不可用异常"""

    status_code = 503
    code = "5030"
    message = "服务暂时不可用，请稍后重试"


class RateLimitException(AppException):
    """请求频率限制异常"""

    status_code = 429
    code = "9000"
    message = "请求过于频繁，请稍后再试"


class DatabaseException(AppException):
    """数据库异常"""

    status_code = 500
    code = "5010"
    message = "数据库操作失败"


class CacheException(AppException):
    """缓存异常"""

    status_code = 500
    code = "5020"
    message = "缓存操作失败"


class ConfigurationException(AppException):
    """配置错误异常"""

    status_code = 500
    code = "5002"
    message = "配置错误"


class RuntimeException(AppException):
    """运行时错误异常"""

    status_code = 500
    code = "5001"
    message = "运行时错误"


class ConnectionException(DatabaseException):
    """数据库连接失败异常"""

    code = "5011"
    message = "数据库连接失败"


class CacheConnectionException(CacheException):
    """缓存连接失败异常"""

    code = "5021"
    message = "缓存连接失败"


class MaintenanceModeException(ServiceUnavailableException):
    """系统维护中异常"""

    code = "5031"
    message = "系统维护中"


# ==================== 外部服务异常 (8xxx) ====================


class ExternalServiceException(AppException):
    """外部服务异常基类"""

    status_code = 502
    code = "8000"
    message = "外部服务调用失败"


class ThirdPartyAPIException(ExternalServiceException):
    """第三方 API 异常"""

    code = "8000"
    message = "第三方服务调用失败"


class ExternalAPITimeoutException(ExternalServiceException):
    """外部服务超时异常"""

    status_code = 504
    code = "8001"
    message = "外部服务超时"


class ExternalAPIUnavailableException(ExternalServiceException):
    """外部服务不可用异常"""

    status_code = 503
    code = "8002"
    message = "外部服务不可用"


# ==================== 便捷函数 ====================


def raise_not_found(resource_type: str, resource_id: Any) -> None:
    """
    抛出资源未找到异常的便捷函数

    Args:
        resource_type: 资源类型
        resource_id: 资源 ID

    Raises:
        NotFoundException: 资源未找到异常
    """
    raise NotFoundException(resource_type=resource_type, resource_id=resource_id)


def raise_duplicate(field: str, value: Any) -> None:
    """
    抛出重复资源异常的便捷函数

    Args:
        field: 冲突字段
        value: 冲突值

    Raises:
        DuplicateException: 重复资源异常
    """
    raise DuplicateException(field=field, value=value)


def raise_forbidden(message: str | None = None) -> None:
    """
    抛出禁止访问异常的便捷函数

    Args:
        message: 错误消息

    Raises:
        ForbiddenException: 禁止访问异常
    """
    raise ForbiddenException(message=message)


def raise_unauthorized(message: str | None = None) -> None:
    """
    抛出未授权异常的便捷函数

    Args:
        message: 错误消息

    Raises:
        UnauthorizedException: 未授权异常
    """
    raise UnauthorizedException(message=message)


def raise_validation_error(message: str | None = None, field: str | None = None) -> None:
    """
    抛出验证错误异常的便捷函数

    Args:
        message: 错误消息
        field: 错误字段

    Raises:
        ValidationException: 验证错误异常
    """
    raise ValidationException(message=message, detail={"field": field} if field else None)


def raise_invalid_parameter(message: str | None = None, field: str | None = None) -> None:
    """
    抛出无效参数异常的便捷函数

    Args:
        message: 错误消息
        field: 错误字段

    Raises:
        InvalidParameterException: 无效参数异常
    """
    raise InvalidParameterException(message=message, field=field)
