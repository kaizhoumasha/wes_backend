"""
响应工具模块

提供便捷的响应构建方法和模型序列化工具。

核心功能：
1. 响应构建器 - 快速构建标准响应
2. 模型序列化器 - 将ORM/Pydantic模型转换为字典
3. 批量操作工具 - 构建批量操作响应
"""

from typing import Any

from fastapi.responses import Response
from pydantic import BaseModel

from src.utils.timezone import timezone

from .response_code import DEFAULT_SUCCESS, ResponseCode
from .response_schema import (
    BatchOperationResult,
)

# ==================== 成功响应模型 ====================


class SuccessResponse(BaseModel):
    """
    标准化成功响应模型

    使用 Pydantic 模型可利用 Rust 级别序列化，性能优于直接使用 JSONResponse。
    """

    code: str
    message: str
    timestamp: str
    data: Any | None = None

    model_config = {"frozen": True}


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
        ```
    """

    @staticmethod
    def _build_response_dict(code: str, message: str, data: Any = None, timestamp: str | None = None) -> dict[str, Any]:
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
            timestamp = timezone.now_utc().isoformat().replace("+00:00", "Z")

        response_dict = {"code": code, "message": message, "timestamp": timestamp}

        if data is not None:
            response_dict["data"] = data

        return response_dict

    def success(
        self, data: Any = None, code: ResponseCode = DEFAULT_SUCCESS, message: str | None = None
    ) -> dict[str, Any]:
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

    def fail(self, code: ResponseCode, message: str | None = None, data: Any = None) -> dict[str, Any]:
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

    def batch_operation(
        self,
        success: int,
        failed: int,
        results: list[Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        code: ResponseCode = DEFAULT_SUCCESS,
    ) -> dict[str, Any]:
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
        batch_result = BatchOperationResult.create(success=success, failed=failed, results=results, errors=errors)

        return self._build_response_dict(code=code.code, message=code.message, data=batch_result.model_dump())

    def fast_success(
        self, data: Any = None, code: ResponseCode = DEFAULT_SUCCESS, message: str | None = None
    ) -> Response:
        """
        快速成功响应（性能优化）

        使用 Pydantic 模型序列化，利用 Rust 级别的高效序列化。
        注意：使用此方法时，路由不能指定 response_model 参数。

        Args:
            data: 响应数据
            code: 响应码
            message: 响应消息

        Returns:
            Response 对象

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
        timestamp = timezone.now_utc().isoformat().replace("+00:00", "Z")

        # 使用 Pydantic 模型构建响应
        response_data = SuccessResponse(
            code=code.code,
            message=msg,
            timestamp=timestamp,
            data=data,
        )

        return Response(
            content=response_data.model_dump_json(),
            status_code=code.status,
            media_type="application/json",
        )


# ==================== 全局实例 ====================

# 全局响应构建器实例
response_builder: ResponseBuilder = ResponseBuilder()


# ==================== 导出 ====================

__all__ = [
    "ResponseBuilder",
    "response_builder",
]
