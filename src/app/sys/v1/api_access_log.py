"""统一外部交互日志的两个只读 facade。"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path

from src.app.sys.models.api_access_log import APIAccessLogResponse, APIAccessLogSummary
from src.app.sys.services.api_access_log_service import api_access_log_service
from src.core.query_models import QueryOptions
from src.core.rbac import RequirePermission
from src.core.response import DEFAULT_NOT_FOUND, ResponseSchemaModel, response_builder
from src.core.response.response_schema import ListResponseSchemaModel
from src.database.dependencies import AsyncSessionDep

router = APIRouter(prefix="/api-access-logs", tags=["API 访问日志"])


@router.post(
    "/query",
    summary="[sys:apiaccesslog:list] 查询 API 访问日志",
    operation_id="sys_apiaccesslog_query",
    response_model=ListResponseSchemaModel[APIAccessLogSummary],
    dependencies=[Depends(RequirePermission("sys:apiaccesslog:list"))],
)
async def query_api_access_logs(db: AsyncSessionDep, options: Annotated[QueryOptions, Body(...)]) -> dict[str, Any]:
    total, items = await api_access_log_service.query_summary(db, options)
    return response_builder.success(
        data={"total": total, "items": items, "offset": options.offset, "limit": options.limit}
    )


@router.get(
    "/{id}",
    summary="[sys:apiaccesslog:detail] 获取 API 访问日志详情",
    operation_id="sys_apiaccesslog_get",
    response_model=ResponseSchemaModel[APIAccessLogResponse],
    dependencies=[Depends(RequirePermission("sys:apiaccesslog:detail"))],
)
async def get_api_access_log(id: Annotated[int, Path(...)], db: AsyncSessionDep) -> dict[str, Any]:
    row = await api_access_log_service.get_by_id(db, None, id)
    if row is None:
        return response_builder.fail(code=DEFAULT_NOT_FOUND, message=f"API 访问日志不存在: {id}")
    return response_builder.success(data=APIAccessLogResponse.model_validate(row))


__all__ = ["router"]
