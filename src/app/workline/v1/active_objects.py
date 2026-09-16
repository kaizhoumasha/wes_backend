"""WorkLine active objects 只读 API facade。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Path, Request, status

from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
    WorklineActiveObjectsResponse,
    workline_active_objects_service,
)
from src.app.workline.models import PlaneActiveObjectsV2
from src.app.workline.services import workline_plane_service
from src.core.rbac import PermissionDep, RequirePermission
from src.core.response import ResourceErrorCode, ResponseSchemaModel, ServerErrorCode, response_builder
from src.database.dependencies import AsyncSessionDep, CacheDep  # noqa: TC001 - FastAPI needs runtime annotation

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


@router.get(
    "/work_lines/{id}/active-objects/v2",
    operation_id="work_lines_by_id_active_objects_v2_get",
    summary="[biz:workline:active-objects] 查询作业线当前 active objects v2（含 scene_revision 与 resource_ref）",
    response_model=ResponseSchemaModel[PlaneActiveObjectsV2],
    status_code=status.HTTP_200_OK,
)
async def get_workline_active_objects_v2(
    request: Request,
    db: AsyncSessionDep,
    cache: CacheDep,
    _permission: PermissionDep("biz:workline:active-objects"),
    id: int = Path(..., description="WorkLine.id"),
) -> ResponseSchemaModel[PlaneActiveObjectsV2]:
    """读取 Active Objects v2；与 v1 并存，互不改变语义。"""

    plugins = getattr(getattr(request.app.state, "deployment_runtime", None), "plugins", None)
    if plugins is None:
        return cast(
            "ResponseSchemaModel[PlaneActiveObjectsV2]",
            response_builder.fail(code=ServerErrorCode.SERVICE_UNAVAILABLE, message="部署运行时不可用"),
        )
    try:
        result = await workline_plane_service.get_active_objects_v2(db, cache, id, plugins=plugins)
    except ValueError as exc:
        return cast(
            "ResponseSchemaModel[PlaneActiveObjectsV2]",
            response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=str(exc)),
        )
    return cast("ResponseSchemaModel[PlaneActiveObjectsV2]", response_builder.success(data=result))


__all__ = ["router"]
