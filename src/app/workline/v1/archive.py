"""WorkLine 一键归档/清线 API。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Body, Depends, Path, Request, status

from src.app.workline.models import WorkLineArchiveOpenWorkResponse, WorkLineStateTransitionRequest
from src.app.workline.services.workline_archive_service import (
    WorkLineArchiveConfigurationError,
    WorkLineArchiveNotFoundError,
    WorkLineArchiveService,
    WorkLineArchiveVersionConflictError,
)
from src.app.workline.services.workline_service import workline_service
from src.app.workline.unit_of_work import WorklineUnitOfWork
from src.core.rbac import RequirePermission
from src.core.response import (
    BusinessErrorCode,
    ResourceErrorCode,
    ResponseSchemaModel,
    ServerErrorCode,
    response_builder,
)
from src.database.dependencies import AsyncSessionDep, CacheDep  # noqa: TC001 - FastAPI needs runtime annotation

router = APIRouter(tags=["作业线管理"])


@router.post(
    "/work_lines/{id}/archive-open-work",
    summary="[biz:workline:archive-open-work] 一键归档当前及未闭合任务",
    response_model=ResponseSchemaModel[WorkLineArchiveOpenWorkResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:archive-open-work"))],
)
async def archive_workline_open_work(
    db: AsyncSessionDep,
    cache: CacheDep,
    request: Request,
    id: int = Path(...),
    payload: WorkLineStateTransitionRequest = Body(...),
) -> ResponseSchemaModel[WorkLineArchiveOpenWorkResponse]:
    """原子归档本线业务任务；设备命令、搬运与 Evidence 保持原身份。"""

    service = cast("WorkLineArchiveService | None", getattr(request.app.state, "workline_archive_service", None))
    if service is None:
        return cast(
            "ResponseSchemaModel[WorkLineArchiveOpenWorkResponse]",
            response_builder.fail(code=ServerErrorCode.SERVICE_UNAVAILABLE, message="作业线清线服务不可用"),
        )
    try:
        async with WorklineUnitOfWork(db=db) as uow:
            result = await service.archive_open_work(uow.session, workline_id=id, version=payload.version)
            await uow.commit()
    except WorkLineArchiveNotFoundError as exc:
        return cast(
            "ResponseSchemaModel[WorkLineArchiveOpenWorkResponse]",
            response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=str(exc)),
        )
    except WorkLineArchiveVersionConflictError as exc:
        return cast(
            "ResponseSchemaModel[WorkLineArchiveOpenWorkResponse]",
            response_builder.fail(code=ResourceErrorCode.CONFLICT, message=str(exc)),
        )
    except WorkLineArchiveConfigurationError as exc:
        return cast(
            "ResponseSchemaModel[WorkLineArchiveOpenWorkResponse]",
            response_builder.fail(code=BusinessErrorCode.INVALID_STATE, message=str(exc)),
        )

    await workline_service.invalidate_cache(cache, id, invalidate_list=True)
    data = WorkLineArchiveOpenWorkResponse(
        workline_id=result.workline_id,
        version=result.version,
        archived_picking_tasks=result.archived_picking_tasks,
        archived_plugin_tasks=result.archived_plugin_tasks,
        archived_integration_runs=result.archived_integration_runs,
        archived_total=result.archived_total,
    )
    return cast(
        "ResponseSchemaModel[WorkLineArchiveOpenWorkResponse]",
        response_builder.success(data=data),
    )


__all__ = ["router"]
