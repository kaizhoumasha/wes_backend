"""
回调日志查询 API 路由 (Callback Log Query API Routes)

提供设备回调日志的查询接口，用于监控和问题排查。
"""

from fastapi import APIRouter, status

from src.app.callback.models import CallbackLogResponse
from src.app.callback.services import callback_log_service
from src.core.query_models import FilterGroup, SortField
from src.core.response import response_builder
from src.database.dependencies import AsyncSessionDep, CacheDep

router = APIRouter()


# ==================== 查询接口 ====================


@router.get(
    "/request/{request_id}",
    response_model=CallbackLogResponse,
    status_code=status.HTTP_200_OK,
    summary="根据请求 ID 查询回调日志",
    description="根据 request_id 查询单条回调日志记录",
)
async def get_by_request_id(
    request_id: str,
    db: AsyncSessionDep,
) -> dict:
    """
    根据 request_id 查询单条回调日志

    用于追踪特定请求的回调记录。
    """
    log = await callback_log_service.repo.get_by_request_id(db, request_id)
    if not log:
        return response_builder.fail(
            code=404,
            message=f"回调日志不存在: request_id={request_id}",
        )
    return response_builder.success(data=log)


@router.get(
    "/correlation/{correlation_id}",
    status_code=status.HTTP_200_OK,
    summary="根据关联 ID 查询回调日志",
    description="根据 correlation_id 查询所有相关的回调日志（用于串联整个流程）",
)
async def get_by_correlation_id(
    correlation_id: str,
    db: AsyncSessionDep,
) -> dict:
    """
    根据 correlation_id 查询所有相关的回调日志

    用于追踪整个业务流程的回调链路。
    """
    logs = await callback_log_service.repo.get_by_correlation_id(db, correlation_id)
    return response_builder.success(
        data={
            "correlation_id": correlation_id,
            "count": len(logs),
            "items": logs,
        }
    )


@router.get(
    "/device/{device_id}",
    status_code=status.HTTP_200_OK,
    summary="根据设备 ID 查询回调日志",
    description="查询指定设备最近的回调记录",
)
async def get_by_device_id(
    device_id: str,
    db: AsyncSessionDep,
    limit: int = 100,
) -> dict:
    """
    根据设备 ID 查询最近的回调日志

    用于监控设备回调历史和排查问题。
    """
    logs = await callback_log_service.repo.get_by_device_id(db, device_id, limit)
    return response_builder.success(
        data={
            "device_id": device_id,
            "count": len(logs),
            "items": logs,
        }
    )


@router.post(
    "/query",
    status_code=status.HTTP_200_OK,
    summary="回调日志列表查询",
    description="通用列表查询接口，支持分页、过滤和排序",
)
async def query_callback_logs(
    db: AsyncSessionDep,
    cache: CacheDep,
    filters: FilterGroup | None = None,
    sort: list[SortField] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """
    回调日志列表查询

    支持的过滤字段：
    - callback_type: 回调类型 (event/result)
    - device_id: 设备 ID
    - response_status: 响应状态码
    - request_id: 请求 ID
    - correlation_id: 关联 ID

    示例：
    ```json
    {
      "filters": {
        "conditions": [
          {"field": "callback_type", "operator": "eq", "value": "event"},
          {"field": "response_status", "operator": "gte", "value": 400}
        ]
      },
      "sort": [{"field": "created_at", "order": "desc"}],
      "limit": 20,
      "offset": 0
    }
    ```
    """
    total, items = await callback_log_service.get_list(
        db,
        cache,
        limit,
        offset,
        filters,
        sort,
    )
    return response_builder.success(
        data={
            "total": total,
            "items": items,
        }
    )


# ==================== 导出 ====================


__all__ = ["router"]
