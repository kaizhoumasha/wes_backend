"""
响应码枚举模块

定义系统中所有的响应码，包括成功和各类错误码。

响应码规范:
- 1xxx: 成功响应
- 2xxx: 客户端错误（参数、权限等）
- 3xxx: 资源相关错误（不存在、冲突等）
- 4xxx: 业务逻辑错误
- 5xxx: 服务器内部错误
- 8xxx: 第三方服务错误
- 9xxx: 其他错误
"""

from enum import Enum
from typing import Final


class ResponseCode(str, Enum):
    """
    响应码枚举基类

    所有响应码都继承自此类，确保类型安全和一致性。
    """

    code: str
    message: str
    http_status: int

    def __new__(cls, code: str, message: str, http_status: int = 200):
        """
        创建响应码枚举实例

        Args:
            code: 响应码字符串
            message: 响应消息
            http_status: HTTP状态码
        """
        obj = str.__new__(cls, code)
        obj._value_ = code
        obj.code = code
        obj.message = message
        obj.http_status = http_status
        return obj

    @property
    def status(self) -> int:
        """获取HTTP状态码"""
        return self.http_status


# ==================== 成功响应 (1xxx) ====================


class SuccessCode(ResponseCode):
    """成功响应码"""

    SUCCESS = ("1000", "操作成功", 200)
    CREATED = ("1001", "创建成功", 201)
    UPDATED = ("1002", "更新成功", 200)
    DELETED = ("1003", "删除成功", 200)
    ACCEPTED = ("1004", "请求已接受", 202)


# ==================== 客户端错误 (2xxx) ====================


class ClientErrorCode(ResponseCode):
    """客户端错误码"""

    # 通用客户端错误
    BAD_REQUEST = ("2000", "请求参数错误", 400)
    INVALID_PARAMETER = ("2001", "无效的参数", 400)
    MISSING_PARAMETER = ("2002", "缺少必需参数", 400)
    INVALID_FORMAT = ("2003", "数据格式错误", 400)
    VALIDATION_ERROR = ("2004", "数据验证失败", 400)

    # 认证相关
    UNAUTHORIZED = ("2010", "未授权，请先登录", 401)
    INVALID_CREDENTIALS = ("2011", "用户名或密码错误", 401)
    INVALID_TOKEN = ("2012", "无效的令牌", 401)
    TOKEN_EXPIRED = ("2013", "令牌已过期", 401)
    TOKEN_MISSING = ("2014", "缺少令牌", 401)

    # 权限相关
    FORBIDDEN = ("2020", "无权访问", 403)
    ADMIN_REQUIRED = ("2021", "需要管理员权限", 403)
    PERMISSION_DENIED = ("2022", "权限不足", 403)


# ==================== 资源错误 (3xxx) ====================


class ResourceErrorCode(ResponseCode):
    """资源相关错误码"""

    NOT_FOUND = ("3000", "资源不存在", 404)
    USER_NOT_FOUND = ("3001", "用户不存在", 404)
    ROLE_NOT_FOUND = ("3002", "角色不存在", 404)
    PERMISSION_NOT_FOUND = ("3003", "权限不存在", 404)

    ALREADY_EXISTS = ("3010", "资源已存在", 409)
    DUPLICATE_RESOURCE = ("3011", "重复的资源", 409)
    CONFLICT = ("3012", "资源冲突", 409)

    RESOURCE_LOCKED = ("3020", "资源已被锁定", 423)
    RESOURCE_GONE = ("3021", "资源已被删除", 410)


# ==================== 业务逻辑错误 (4xxx) ====================


class BusinessErrorCode(ResponseCode):
    """业务逻辑错误码"""

    OPERATION_FAILED = ("4000", "操作失败", 400)
    INVALID_STATE = ("4001", "无效的状态", 400)
    STATE_TRANSITION_INVALID = ("4002", "状态转换无效", 400)

    INSUFFICIENT_BALANCE = ("4010", "余额不足", 400)
    QUOTA_EXCEEDED = ("4011", "配额已用尽", 400)
    LIMIT_REACHED = ("4012", "已达上限", 400)

    # 数据相关
    DATA_INTEGRITY_ERROR = ("4020", "数据完整性错误", 400)
    DATA_CONFLICT = ("4021", "数据冲突", 400)
    CASCADING_DELETE_ERROR = ("4022", "级联删除错误", 400)

    # Runtime Hold 领域错误
    RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE = (
        "RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE",
        "Runtime Hold 缺少释放证据",
        422,
    )
    RUNTIME_HOLD_VERSION_CONFLICT = (
        "RUNTIME_HOLD_VERSION_CONFLICT",
        "Runtime Hold 版本冲突",
        409,
    )
    RUNTIME_HOLD_EVIDENCE_CHANGED = (
        "RUNTIME_HOLD_EVIDENCE_CHANGED",
        "Runtime Hold 证据已变化",
        409,
    )
    RUNTIME_HOLD_ALREADY_RESOLVED = (
        "RUNTIME_HOLD_ALREADY_RESOLVED",
        "Runtime Hold 已解除",
        409,
    )
    RUNTIME_HOLD_REASON_UNMAPPED = (
        "RUNTIME_HOLD_REASON_UNMAPPED",
        "Runtime Hold NG 原因未映射",
        422,
    )
    RUNTIME_HOLD_HANDOFF_LOCATION_UNMAPPED = (
        "RUNTIME_HOLD_HANDOFF_LOCATION_UNMAPPED",
        "Runtime Hold NG 位置未映射",
        422,
    )
    RUNTIME_HOLD_MATERIAL_CONFLICT = (
        "RUNTIME_HOLD_MATERIAL_CONFLICT",
        "Runtime Hold 物料处置冲突",
        409,
    )


# ==================== 服务器错误 (5xxx) ====================


class ServerErrorCode(ResponseCode):
    """服务器内部错误码"""

    INTERNAL_ERROR = ("5000", "服务器内部错误", 500)
    RUNTIME_ERROR = ("5001", "运行时错误", 500)
    CONFIGURATION_ERROR = ("5002", "配置错误", 500)

    # 数据库相关
    DATABASE_ERROR = ("5010", "数据库错误", 500)
    CONNECTION_ERROR = ("5011", "数据库连接失败", 503)
    TRANSACTION_ERROR = ("5012", "事务错误", 500)

    # 缓存相关
    CACHE_ERROR = ("5020", "缓存错误", 500)
    CACHE_CONNECTION_ERROR = ("5021", "缓存连接失败", 503)

    # 服务不可用
    SERVICE_UNAVAILABLE = ("5030", "服务暂不可用", 503)
    MAINTENANCE_MODE = ("5031", "系统维护中", 503)


# ==================== 第三方服务错误 (8xxx) ====================


class ExternalServiceErrorCode(ResponseCode):
    """第三方服务错误码"""

    EXTERNAL_API_ERROR = ("8000", "外部服务错误", 502)
    EXTERNAL_API_TIMEOUT = ("8001", "外部服务超时", 504)
    EXTERNAL_API_UNAVAILABLE = ("8002", "外部服务不可用", 503)


# ==================== 其他错误 (9xxx) ====================


class MiscErrorCode(ResponseCode):
    """其他错误码"""

    RATE_LIMIT_EXCEEDED = ("9000", "请求过于频繁", 429)
    TOO_MANY_REQUESTS = ("9001", "请求次数过多", 429)

    UNKNOWN_ERROR = ("9999", "未知错误", 500)


# ==================== 便捷导出 ====================

# 默认响应码
DEFAULT_SUCCESS: Final = SuccessCode.SUCCESS
DEFAULT_ERROR: Final = ServerErrorCode.INTERNAL_ERROR
DEFAULT_NOT_FOUND: Final = ResourceErrorCode.NOT_FOUND
DEFAULT_UNAUTHORIZED: Final = ClientErrorCode.UNAUTHORIZED
DEFAULT_FORBIDDEN: Final = ClientErrorCode.FORBIDDEN


# 响应码集合（用于类型提示）
class AllResponseCode:
    """所有响应码的集合类型"""

    Success = SuccessCode
    ClientError = ClientErrorCode
    ResourceError = ResourceErrorCode
    BusinessError = BusinessErrorCode
    ServerError = ServerErrorCode
    ExternalServiceError = ExternalServiceErrorCode
    MiscError = MiscErrorCode


# 响应码类型（用于类型提示）
ResponseType = AllResponseCode
