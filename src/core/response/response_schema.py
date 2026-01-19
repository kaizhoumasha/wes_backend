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

from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# ==================== 类型变量 ====================

SchemaT = TypeVar("SchemaT")
T = TypeVar("T")


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
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
        class UserRead(BaseModel):
            id: int
            name: str

        @router.get('/users/{user_id}', response_model=ResponseSchemaModel[UserRead])
        def get_user(user_id: int) -> ResponseSchemaModel[UserRead]:
            user = fetch_user(user_id)
            return ResponseSchemaModel[UserRead](
                code=SuccessCode.SUCCESS,
                data=user
            )
        ```
    """

    data: SchemaT = Field(description="响应数据")


# ==================== 分页数据模型 ====================


class PaginationData[T](BaseModel):
    """
    分页数据模型

    用于包装分页查询的结果数据。

    Type Parameters:
        T: 列表项的数据类型

    Attributes:
        items: 数据列表
        total: 总记录数
        page: 当前页码（从1开始）
        size: 每页大小
        pages: 总页数

    Example:
        ```python
        pagination = PaginationData[UserRead](
            items=[user1, user2],
            total=100,
            page=1,
            size=10,
            pages=10
        )
        ```
    """

    items: list[T] = Field(default=[], description="数据列表")
    total: int = Field(default=0, ge=0, description="总记录数")
    page: int = Field(default=1, ge=1, description="当前页码")
    size: int = Field(default=10, ge=1, le=100, description="每页大小")
    pages: int = Field(default=0, ge=0, description="总页数")

    @classmethod
    def create(cls, items: list[T], total: int, page: int = 1, size: int = 10) -> "PaginationData[T]":
        """
        创建分页数据

        Args:
            items: 数据列表
            total: 总记录数
            page: 当前页码
            size: 每页大小

        Returns:
            分页数据实例
        """
        pages = (total + size - 1) // size if size > 0 else 0
        return cls(items=items, total=total, page=page, size=size, pages=pages)


# ==================== 分页响应模型 ====================


class PaginationResponseModel[T](ResponseSchemaModel[PaginationData[T]]):
    """
    分页响应模型

    专门用于分页查询的响应模型，提供完整的分页信息。

    Type Parameters:
        T: 列表项的数据类型

    Example:
        ```python
        @router.get('/users', response_model=PaginationResponseModel[UserRead])
        def get_users(page: int = 1, size: int = 10) -> PaginationResponseModel[UserRead]:
            users, total = fetch_users(page, size)
            return PaginationResponseModel[UserRead](
                code=SuccessCode.SUCCESS,
                data=PaginationData.create(users, total, page, size)
            )
        ```
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
    errors: list[dict] | None = Field(default=None, description="错误信息列表")

    @classmethod
    def create(
        cls,
        success: int,
        failed: int,
        results: list[Any] | None = None,
        errors: list[dict] | None = None,
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
    "PaginationData",
    "PaginationResponseModel",
    "ResponseModel",
    "ResponseSchemaModel",
]
