"""WorkLine API 路由"""

from typing import cast
from urllib.parse import unquote

from fastapi import APIRouter, Body, Depends, Path, status

from src.app.workline.models import (
    WorkLine,
    WorkLineConfigurationStatus,
    WorkLineCreate,
    WorkLinePluginManifestSummary,
    WorkLinePluginOption,
    WorkLineResponse,
    WorkLineStateTransitionRequest,
    WorkLineUpdate,
)
from src.app.workline.services import workline_service
from src.core.base_api import BaseAPI
from src.core.rbac import RequirePermission
from src.core.response import (
    BusinessErrorCode,
    ClientErrorCode,
    ResourceErrorCode,
    ResponseSchemaModel,
    response_builder,
)
from src.database.dependencies import AsyncSessionDep, CacheDep

router = APIRouter(tags=["作业线管理"])


def _workline_value_error_response(exc: ValueError) -> dict[str, object]:
    message = str(exc)
    if "不存在" in message:
        return response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=message)
    return response_builder.fail(code=BusinessErrorCode.INVALID_STATE, message=message)


@router.get(
    "/plugins/options",
    summary="[biz:workline:list] 获取作业线插件选项",
    response_model=ResponseSchemaModel[list[WorkLinePluginOption]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def list_workline_plugin_options() -> ResponseSchemaModel[list[WorkLinePluginOption]]:
    """从插件注册表导出作业线插件与契约版本下拉选项。"""

    return cast(
        "ResponseSchemaModel[list[WorkLinePluginOption]]",
        response_builder.success(data=workline_service.list_plugin_options()),
    )


@router.get(
    "/plugins/{plugin_key:path}/manifest",
    summary="[biz:workline:list] 获取单个作业线插件 manifest 摘要",
    response_model=ResponseSchemaModel[WorkLinePluginManifestSummary],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_workline_plugin_manifest(
    plugin_key: str = Path(...),
) -> ResponseSchemaModel[WorkLinePluginManifestSummary]:
    """从插件注册表导出单个作业线插件 manifest 摘要。"""

    plugin_key = unquote(plugin_key)
    try:
        summary = workline_service.get_plugin_manifest_summary(plugin_key)
    except (TypeError, ValueError) as exc:
        return cast(
            "ResponseSchemaModel[WorkLinePluginManifestSummary]",
            response_builder.fail(
                code=ClientErrorCode.VALIDATION_ERROR,
                message=f"工作线插件 manifest 无效: {plugin_key}: {exc}",
            ),
        )

    if summary is None:
        return cast(
            "ResponseSchemaModel[WorkLinePluginManifestSummary]",
            response_builder.fail(
                code=ResourceErrorCode.NOT_FOUND,
                message=f"工作线插件不存在: {plugin_key}",
            ),
        )

    return cast(
        "ResponseSchemaModel[WorkLinePluginManifestSummary]",
        response_builder.success(data=summary),
    )


@router.get(
    "/work_lines/{id}/configuration-status",
    summary="[biz:workline:detail] 查询作业线配置状态",
    response_model=ResponseSchemaModel[WorkLineConfigurationStatus],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:detail"))],
)
async def get_workline_configuration_status(
    db: AsyncSessionDep,
    id: int = Path(...),
) -> ResponseSchemaModel[WorkLineConfigurationStatus]:
    """查询 WorkLine 启用前配置状态。"""

    try:
        status_data = await workline_service.configuration_status(db, id)
    except ValueError as exc:
        return cast("ResponseSchemaModel[WorkLineConfigurationStatus]", _workline_value_error_response(exc))

    return cast(
        "ResponseSchemaModel[WorkLineConfigurationStatus]",
        response_builder.success(data=status_data),
    )


@router.post(
    "/work_lines/{id}/activate",
    summary="[biz:workline:activate] 启用作业线",
    response_model=ResponseSchemaModel[WorkLineResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:activate"))],
)
async def activate_workline(
    db: AsyncSessionDep,
    cache: CacheDep,
    id: int = Path(...),
    payload: WorkLineStateTransitionRequest = Body(...),
) -> ResponseSchemaModel[WorkLineResponse]:
    """通过配置预检后启用 WorkLine。"""

    try:
        updated = await workline_service.activate(db, id, version=payload.version, cache=cache)
    except ValueError as exc:
        return cast("ResponseSchemaModel[WorkLineResponse]", _workline_value_error_response(exc))

    response_resource = cast("WorkLine", await workline_service.get_by_id(db, cache, id, max_depth=1) or updated)
    return cast(
        "ResponseSchemaModel[WorkLineResponse]",
        response_builder.success(data=workline_service.to_response(response_resource, WorkLineResponse)),
    )


@router.post(
    "/work_lines/{id}/deactivate",
    summary="[biz:workline:deactivate] 停用作业线",
    response_model=ResponseSchemaModel[WorkLineResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:deactivate"))],
)
async def deactivate_workline(
    db: AsyncSessionDep,
    cache: CacheDep,
    id: int = Path(...),
    payload: WorkLineStateTransitionRequest = Body(...),
) -> ResponseSchemaModel[WorkLineResponse]:
    """确认无未完成运行负载后停用 WorkLine。"""

    try:
        updated = await workline_service.deactivate(db, id, version=payload.version, cache=cache)
    except ValueError as exc:
        return cast("ResponseSchemaModel[WorkLineResponse]", _workline_value_error_response(exc))

    response_resource = cast("WorkLine", await workline_service.get_by_id(db, cache, id, max_depth=1) or updated)
    return cast(
        "ResponseSchemaModel[WorkLineResponse]",
        response_builder.success(data=workline_service.to_response(response_resource, WorkLineResponse)),
    )


# 使用 BaseAPI 零代码生成 CRUD 路由
workline_api = BaseAPI(
    module_name="biz",
    model=WorkLine,
    service=workline_service,
    create_schema=WorkLineCreate,
    update_schema=WorkLineUpdate,
    response_schema=WorkLineResponse,
    prefix="/work_lines",
    tags=["作业线管理"],
)

router.include_router(workline_api.router)
