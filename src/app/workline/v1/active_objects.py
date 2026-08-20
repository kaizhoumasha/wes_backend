"""WorkLine active objects 只读 API facade。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Path, status

from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
    WorklineActiveObjectsResponse,
    workline_active_objects_service,
)
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.database.dependencies import AsyncSessionDep  # noqa: TC001 - FastAPI needs runtime annotation

router = APIRouter(tags=["作业线运行视图"])


@router.get(
    "/work_lines/{id}/active-objects",
    summary="[biz:workline:active-objects] 查询作业线当前 active objects",
    response_model=ResponseSchemaModel[WorklineActiveObjectsResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:active-objects"))],
)
async def get_workline_active_objects(
    db: AsyncSessionDep,
    id: int = Path(..., description="WorkLine.id"),
) -> ResponseSchemaModel[WorklineActiveObjectsResponse]:
    """读取 WorklineActiveObjects；API 层不直接访问 repository。"""

    result = await workline_active_objects_service.get_active_objects(db, workline_id=id)
    return cast("ResponseSchemaModel[WorklineActiveObjectsResponse]", response_builder.success(data=result))


__all__ = ["router"]
