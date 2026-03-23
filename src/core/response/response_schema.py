"""
响应模型模块

定义统一的API响应格式，使用Pydantic确保类型安全和数据验证。

响应格式规范:
{
    "code": "响应码",
    "message": "响应消息",
    "data": "响应数据",
    "timestamp": "ISO时间戳"
}
"""

from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from src.utils.timezone import timezone

# ==================== 类型变量 ====================

SchemaT = TypeVar("SchemaT")


# ==================== 基础响应模型 ====================


class ResponseModel(BaseModel):
    """
    基础响应模型

    不包含返回数据schema的通用型统一返回模型。

    Attributes:
        code: 响应码（字符串类型，与error_response保持一致）
        message: 响应消息（用户友好的描述）
        data: 响应数据（任意类型）
        timestamp: 响应时间戳（ISO 8601格式）

    Example:
        ```python
        @router.get('/test')
        def test() -> ResponseModel:
            return ResponseModel(
                code=SuccessCode.SUCCESS,
                message="操作成功",
                data={'test': 'test'}
            )
        ```
    """

    model_config = ConfigDict(
        json_encoders={datetime: lambda v: v.isoformat().replace("+00:00", "Z")},
        populate_by_name=True,
    )

    code: str = Field(default="1000", description="响应码", examples=["1000", "2000"])
    message: str = Field(default="操作成功", description="响应消息", examples=["操作成功", "参数错误"])
    data: Any | None = Field(default=None, description="响应数据")
    timestamp: str = Field(
        default_factory=lambda: timezone.now_utc().isoformat().replace("+00:00", "Z"),
        description="响应时间戳(ISO 8601格式)",
        examples=["2024-01-01T00:00:00Z"],
    )


# ==================== 泛型响应模型 ====================


class ResponseSchemaModel[SchemaT](ResponseModel):
    """
    泛型响应模型

    包含返回数据schema的通用型统一返回模型，支持类型推断。

    Type Parameters:
        SchemaT: 数据模型的类型

    Attributes:
        data: 具体类型的响应数据

    Example:
        ```python
        class UserResponse(BaseModel):
            id: int
            name: str

        @router.get('/users/{user_id}', response_model=ResponseSchemaModel[UserResponse])
        def get_user(user_id: int) -> ResponseSchemaModel[UserResponse]:
            user = fetch_user(user_id)
            return ResponseSchemaModel[UserResponse](
                code=SuccessCode.SUCCESS,
                data=user
            )
        ```
    """

    # 显式声明默认值，保持与父类字段一致，避免静态检查器误报覆盖告警
    data: SchemaT | None = Field(default=None, description="响应数据")


# ==================== 列表响应模型 ====================


class ListResponseData[SchemaT](BaseModel):
    """
    列表响应数据模型

    用于统一描述带总数和分页参数的列表响应数据。
    """

    total: int = Field(default=0, ge=0, description="总数量")
    items: list[SchemaT] = Field(default_factory=list, description="列表数据")
    limit: int = Field(default=0, ge=0, description="分页大小")
    offset: int = Field(default=0, ge=0, description="偏移量")


class ListResponseSchemaModel[SchemaT](ResponseSchemaModel[ListResponseData[SchemaT]]):
    """
    列表响应模型

    为 CRUD 列表接口提供统一的泛型 response_model。
    """


# ==================== 批量操作响应模型 ====================


class BatchOperationResult(BaseModel):
    """
    批量操作结果模型

    用于批量操作（如批量创建、批量更新、批量删除）的响应数据。

    Attributes:
        success: 成功数量
        failed: 失败数量
        total: 总数量
        results: 详细结果列表（可选）
        errors: 错误信息列表（可选）

    Example:
        ```python
        result = BatchOperationResult(
            success=8,
            failed=2,
            total=10,
            errors=[
                {"index": 3, "message": "参数错误"},
                {"index": 7, "message": "权限不足"}
            ]
        )
        ```
    """

    success: int = Field(default=0, ge=0, description="成功数量")
    failed: int = Field(default=0, ge=0, description="失败数量")
    total: int = Field(default=0, ge=0, description="总数量")
    results: list[Any] | None = Field(default=None, description="详细结果列表")
    errors: list[dict[str, Any]] | None = Field(default=None, description="错误信息列表")

    @classmethod
    def create(
        cls,
        success: int,
        failed: int,
        results: list[Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> "BatchOperationResult":
        """
        创建批量操作结果

        Args:
            success: 成功数量
            failed: 失败数量
            results: 详细结果列表
            errors: 错误信息列表

        Returns:
            批量操作结果实例
        """
        return cls(success=success, failed=failed, total=success + failed, results=results, errors=errors)


class BatchOperationResponseModel(ResponseSchemaModel[BatchOperationResult]):
    """
    批量操作响应模型

    专门用于批量操作的响应模型。

    Example:
        ```python
        @router.post('/users/batch', response_model=BatchOperationResponseModel)
        def batch_create_users(users: List[UserCreate]) -> BatchOperationResponseModel:
            result = process_batch_create(users)
            return BatchOperationResponseModel(
                code=SuccessCode.CREATED,
                data=result
            )
        ```
    """


# ==================== 导出 ====================

__all__ = [
    "BatchOperationResponseModel",
    "BatchOperationResult",
    "ListResponseData",
    "ListResponseSchemaModel",
    "ResponseModel",
    "ResponseSchemaModel",
]
