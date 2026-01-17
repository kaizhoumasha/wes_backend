"""
响应工具模块

提供便捷的响应构建方法和模型序列化工具。

核心功能：
1. 响应构建器 - 快速构建标准响应
2. 模型序列化器 - 将ORM/Pydantic模型转换为字典
3. 分页工具 - 构建分页响应
4. 批量操作工具 - 构建批量操作响应
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar, Union

from fastapi.responses import ORJSONResponse
from pydantic import BaseModel
from sqlalchemy.inspection import inspect as sqlalchemy_inspect
from sqlmodel import SQLModel

from .response_code import DEFAULT_SUCCESS, ResponseCode
from .response_schema import (
    BatchOperationResult,
    PaginationData,
)

# ==================== 类型变量 ====================

T = TypeVar("T", bound=BaseModel)
ModelT = TypeVar("ModelT", bound=Union[SQLModel, BaseModel])


# ==================== 响应构建器 ====================


class ResponseBuilder:
    """
    响应构建器

    提供便捷的方法来构建标准格式的API响应。

    Example:
        ```python
        # 成功响应
        return response_builder.success(data={"user": user_dict})

        # 失败响应
        return response_builder.fail(
            code=ClientErrorCode.UNAUTHORIZED,
            data={"reason": "token expired"}
        )

        # 分页响应
        return response_builder.paginate(
            items=users,
            total=total,
            page=page,
            size=size
        )
        ```
    """

    @staticmethod
    def _build_response_dict(
        code: str, message: str, data: Any = None, timestamp: str | None = None
    ) -> dict:
        """
        构建响应字典

        Args:
            code: 响应码
            message: 响应消息
            data: 响应数据
            timestamp: 时间戳（可选，默认使用当前时间）

        Returns:
            响应字典
        """
        if timestamp is None:
            timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        response_dict = {"code": code, "message": message, "timestamp": timestamp}

        if data is not None:
            response_dict["data"] = data

        return response_dict

    def success(
        self, data: Any = None, code: ResponseCode = DEFAULT_SUCCESS, message: str | None = None
    ) -> dict:
        """
        构建成功响应

        Args:
            data: 响应数据
            code: 响应码（默认为成功码）
            message: 响应消息（默认使用code的message）

        Returns:
            响应字典

        Example:
            ```python
            return response_builder.success(data={"id": 1, "name": "test"})
            ```
        """
        msg = message if message is not None else code.message
        return self._build_response_dict(code=code.code, message=msg, data=data)

    def fail(self, code: ResponseCode, message: str | None = None, data: Any = None) -> dict:
        """
        构建失败响应

        Args:
            code: 错误响应码
            message: 错误消息（默认使用code的message）
            data: 附加数据

        Returns:
            响应字典

        Example:
            ```python
            return response_builder.fail(
                code=ClientErrorCode.INVALID_PARAMETER,
                message="用户名不能为空"
            )
            ```
        """
        msg = message if message is not None else code.message
        return self._build_response_dict(code=code.code, message=msg, data=data)

    def paginate(
        self,
        items: list[Any],
        total: int,
        page: int = 1,
        size: int = 10,
        code: ResponseCode = DEFAULT_SUCCESS,
    ) -> dict:
        """
        构建分页响应

        Args:
            items: 数据列表
            total: 总记录数
            page: 当前页码
            size: 每页大小
            code: 响应码（默认为成功码）

        Returns:
            分页响应字典

        Example:
            ```python
            users, total = fetch_users(page=1, size=10)
            return response_builder.paginate(
                items=users,
                total=total,
                page=1,
                size=10
            )
            ```
        """
        pagination_data = PaginationData.create(items=items, total=total, page=page, size=size)

        return self._build_response_dict(
            code=code.code, message=code.message, data=pagination_data.model_dump()
        )

    def batch_operation(
        self,
        success: int,
        failed: int,
        results: list[Any] | None = None,
        errors: list[dict] | None = None,
        code: ResponseCode = DEFAULT_SUCCESS,
    ) -> dict:
        """
        构建批量操作响应

        Args:
            success: 成功数量
            failed: 失败数量
            results: 详细结果列表
            errors: 错误信息列表
            code: 响应码（默认为成功码）

        Returns:
            批量操作响应字典

        Example:
            ```python
            return response_builder.batch_operation(
                success=8,
                failed=2,
                errors=[
                    {"index": 3, "message": "参数错误"},
                    {"index": 7, "message": "权限不足"}
                ]
            )
            ```
        """
        batch_result = BatchOperationResult.create(
            success=success, failed=failed, results=results, errors=errors
        )

        return self._build_response_dict(
            code=code.code, message=code.message, data=batch_result.model_dump()
        )

    def fast_success(
        self, data: Any = None, code: ResponseCode = DEFAULT_SUCCESS, message: str | None = None
    ) -> ORJSONResponse:
        """
        快速成功响应（性能优化）

        此方法直接返回ORJSONResponse，跳过Pydantic验证，在处理大量数据时有性能提升。
        注意：使用此方法时，路由不能指定response_model参数。

        Args:
            data: 响应数据
            code: 响应码
            message: 响应消息

        Returns:
            ORJSONResponse对象

        Example:
            ```python
            @router.get('/large-data')
            # 注意：不能指定 response_model
            def get_large_data():
                # 大量数据...
                return response_builder.fast_success(data=large_data)
            ```
        """
        msg = message if message is not None else code.message
        response_dict = self._build_response_dict(code=code.code, message=msg, data=data)

        return ORJSONResponse(status_code=code.status, content=response_dict)


# ==================== 模型序列化器 ====================


class ModelSerializer:
    """
    模型序列化器

    将SQLAlchemy/SQLModel/Pydantic模型转换为字典格式。

    支持的模型类型：
    - Pydantic BaseModel
    - SQLModel
    - SQLAlchemy模型
    - 普通dict

    Example:
        ```python
        # Pydantic模型
        user_schema = UserRead.model_validate(user_obj)
        user_dict = model_serializer.to_dict(user_schema)

        # SQLAlchemy模型
        user_dict = model_serializer.to_dict(user_obj)

        # 列表
        users_dict = model_serializer.to_dict_list([user1, user2])
        ```
    """

    @staticmethod
    def is_pydantic_model(obj: Any) -> bool:
        """
        判断对象是否为Pydantic模型

        Args:
            obj: 待判断的对象

        Returns:
            是否为Pydantic模型
        """
        return isinstance(obj, BaseModel)

    @staticmethod
    def is_sqlalchemy_model(obj: Any) -> bool:
        """
        判断对象是否为SQLAlchemy/SQLModel模型

        Args:
            obj: 待判断的对象

        Returns:
            是否为SQLAlchemy模型
        """
        try:
            # 检查是否有_sqla_registry属性（SQLAlchemy 2.0）
            if hasattr(obj, "__tablename__"):
                return True
            # 使用sqlalchemy的inspect检查
            sqlalchemy_inspect(obj)
            return True
        except Exception:
            return False

    def to_dict(
        self, model: ModelT, exclude: set[str] | None = None, exclude_none: bool = False
    ) -> dict:
        """
        将模型转换为字典

        Args:
            model: 模型实例（Pydantic/SQLAlchemy/SQLModel）
            exclude: 要排除的字段集合
            exclude_none: 是否排除None值字段

        Returns:
            字典格式的数据

        Raises:
            ValueError: 不支持的模型类型

        Example:
            ```python
            user_dict = model_serializer.to_dict(
                user_obj,
                exclude={'password', 'deleted_at'},
                exclude_none=True
            )
            ```
        """
        # Pydantic/SQLModel模型
        if self.is_pydantic_model(model):
            return model.model_dump(
                exclude=exclude,
                exclude_none=exclude_none,
                mode="json",  # JSON模式，处理datetime等特殊类型
            )

        # SQLAlchemy模型
        if self.is_sqlalchemy_model(model):
            result = {}
            for column in model.__table__.columns:
                key = column.name
                if exclude and key in exclude:
                    continue
                value = getattr(model, key, None)
                if exclude_none and value is None:
                    continue
                # 处理datetime等特殊类型
                if isinstance(value, datetime):
                    value = value.isoformat()
                result[key] = value
            return result

        # 已经是字典
        if isinstance(model, dict):
            if exclude:
                model = {k: v for k, v in model.items() if k not in exclude}
            if exclude_none:
                model = {k: v for k, v in model.items() if v is not None}
            return model

        raise ValueError(
            f"不支持的模型类型: {type(model)}. 仅支持 Pydantic/SQLAlchemy/SQLModel 模型或字典"
        )

    def to_dict_list(
        self, models: Sequence[ModelT], exclude: set[str] | None = None, exclude_none: bool = False
    ) -> list[dict]:
        """
        将模型列表转换为字典列表

        Args:
            models: 模型实例列表
            exclude: 要排除的字段集合
            exclude_none: 是否排除None值字段

        Returns:
            字典列表

        Example:
            ```python
            users_dict = model_serializer.to_dict_list(
                user_list,
                exclude={'password'}
            )
            ```
        """
        return [self.to_dict(model, exclude=exclude, exclude_none=exclude_none) for model in models]

    def paginate_models(
        self,
        models: Sequence[ModelT],
        total: int,
        page: int = 1,
        size: int = 10,
        exclude: set[str] | None = None,
        exclude_none: bool = False,
    ) -> PaginationData:
        """
        将模型列表转换为分页数据

        Args:
            models: 模型实例列表
            total: 总记录数
            page: 当前页码
            size: 每页大小
            exclude: 要排除的字段集合
            exclude_none: 是否排除None值字段

        Returns:
            分页数据对象

        Example:
            ```python
            users, total = fetch_users(page=1, size=10)
            pagination = model_serializer.paginate_models(
                models=users,
                total=total,
                page=1,
                size=10,
                exclude={'password'}
            )
            ```
        """
        items = self.to_dict_list(models=models, exclude=exclude, exclude_none=exclude_none)

        return PaginationData.create(items=items, total=total, page=page, size=size)


# ==================== 全局实例 ====================

# 全局响应构建器实例
response_builder: ResponseBuilder = ResponseBuilder()

# 全局模型序列化器实例
model_serializer: ModelSerializer = ModelSerializer()


# ==================== 导出 ====================

__all__ = [
    "ModelSerializer",
    "ResponseBuilder",
    "model_serializer",
    "response_builder",
]
