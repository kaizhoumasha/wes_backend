"""
统一响应系统

提供全局统一的模型转换和响应构建功能。

模块组成：
- response_code: 响应码枚举定义
- response_schema: 响应模型定义
- response_util: 响应构建器和模型序列化工具

使用示例：
```python
from src.core.response import (
    response_builder,
    model_serializer,
    SuccessCode,
    ClientErrorCode,
    ResponseModel,
    ResponseSchemaModel,
    PaginationResponseModel,
)

# 快速构建响应
@router.get("/users/{user_id}")
async def get_user(user_id: int):
    user = await user_service.get_by_id(user_id)
    user_dict = model_serializer.to_dict(user, exclude={'password'})
    return response_builder.success(data=user_dict)

# 使用响应模型（类型安全）
class UserResponse(BaseModel):
    id: int
    name: str

@router.get("/users/{user_id}", response_model=ResponseSchemaModel[UserResponse])
async def get_user(user_id: int) -> ResponseSchemaModel[UserResponse]:
    user = await user_service.get_by_id(user_id)
    user_schema = UserResponse.model_validate(user)
    return ResponseSchemaModel[UserResponse](
        code=SuccessCode.SUCCESS,
        data=user_schema
    )

# 分页响应
@router.get("/users", response_model=PaginationResponseModel[UserResponse])
async def get_users(page: int = 1, size: int = 10):
    users, total = await user_service.get_list(page, size)
    pagination = model_serializer.paginate_models(
        models=users,
        total=total,
        page=page,
        size=size
    )
    return response_builder.paginate(
        items=pagination.items,
        total=total,
        page=page,
        size=size
    )
```
"""

from typing import Any

from src.utils.timezone import timezone

# ==================== 响应码 ====================
from .response_code import (
    DEFAULT_ERROR,
    DEFAULT_FORBIDDEN,
    DEFAULT_NOT_FOUND,
    DEFAULT_SUCCESS,
    DEFAULT_UNAUTHORIZED,
    BusinessErrorCode,
    ClientErrorCode,
    ExternalServiceErrorCode,
    MiscErrorCode,
    ResourceErrorCode,
    ResponseCode,
    ServerErrorCode,
    SuccessCode,
)
from .response_code import (
    AllResponseCode as ResponseType,
)

# ==================== 响应模型 ====================
from .response_schema import (
    BatchOperationResponseModel,
    BatchOperationResult,
    PaginationData,
    PaginationResponseModel,
    ResponseModel,
    ResponseSchemaModel,
)

# ==================== 响应工具 ====================
from .response_util import (
    ResponseBuilder,
    response_builder,
)

# ==================== 便捷别名 ====================


# 为了向后兼容，提供与error_response格式一致的导出
def error_response_dict(
    code: str,
    message: str,
    detail: Any = None,
) -> dict:
    """
    构建错误响应字典（与error_response格式一致）

    Args:
        code: 错误码
        message: 错误消息
        detail: 详细信息

    Returns:
        错误响应字典
    """
    response = {
        "code": code,
        "message": message,
        "timestamp": timezone.now_utc().isoformat().replace("+00:00", "Z"),
    }

    if detail is not None:
        response["detail"] = detail

    return response


# ==================== 公共导出 ====================

__all__ = [
    "DEFAULT_ERROR",
    "DEFAULT_FORBIDDEN",
    "DEFAULT_NOT_FOUND",
    "DEFAULT_SUCCESS",
    "DEFAULT_UNAUTHORIZED",
    "BatchOperationResponseModel",
    "BatchOperationResult",
    "BusinessErrorCode",
    "ClientErrorCode",
    "ExternalServiceErrorCode",
    "MiscErrorCode",
    "PaginationData",
    "PaginationResponseModel",
    "ResourceErrorCode",
    # 响应工具
    "ResponseBuilder",
    # 响应码
    "ResponseCode",
    # 响应模型
    "ResponseModel",
    "ResponseSchemaModel",
    "ResponseType",
    "ServerErrorCode",
    "SuccessCode",
    # 便捷函数
    "error_response_dict",
    "response_builder",
]

# ==================== 版本信息 ====================

__version__ = "1.0.0"
__author__ = "WES Backend Team"
__description__ = "统一响应系统 - 提供全局一致的API响应格式和模型序列化工具"
