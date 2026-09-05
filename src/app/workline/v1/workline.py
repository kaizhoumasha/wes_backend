"""WorkLine API 路由"""

from typing import Annotated, cast

from fastapi import APIRouter, Body, Depends, Path, Request, status

from src.app.workline.models import (
    PlaneSceneView,
    PlaneSnapshot,
    WorkLine,
    WorkLineConfigurationResponse,
    WorkLineConfigurationStatus,
    WorkLineConfigurationUpdate,
    WorkLineCreate,
    WorkLinePluginSummary,
    WorkLineResponse,
    WorkLineStateTransitionRequest,
    WorkLineUpdate,
)
from src.app.workline.services import workline_plane_service
from src.app.workline.services.plane_service import PlaneReadPrincipal, plane_read_security_policy
from src.app.workline.services.workline_service import workline_service
from src.core.base_api import BaseAPI
from src.core.rbac import RequirePermission
from src.core.response import (
    BusinessErrorCode,
    ResourceErrorCode,
    ResponseSchemaModel,
    ServerErrorCode,
    response_builder,
)
from src.core.security import require_auth
from src.database.dependencies import AsyncSessionDep, CacheDep

router = APIRouter(tags=["作业线管理"])


@router.get(
    "/work_lines/{id}/available-plugins",
    summary="[biz:workline:available-plugins] 查询可装配业务插件",
    response_model=ResponseSchemaModel[list[WorkLinePluginSummary]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:available-plugins"))],
)
async def get_available_workline_plugins(
    db: AsyncSessionDep,
    request: Request,
    id: int = Path(...),
) -> ResponseSchemaModel[list[WorkLinePluginSummary]]:
    service = getattr(request.app.state, "workline_configuration_service", None)
    if service is None:
        return cast(
            "ResponseSchemaModel[list[WorkLinePluginSummary]]",
            response_builder.fail(code=ServerErrorCode.SERVICE_UNAVAILABLE, message="工作线配置服务不可用"),
        )
    try:
        plugins = await service.available_plugins(db, workline_id=id)
    except ValueError as exc:
        return cast("ResponseSchemaModel[list[WorkLinePluginSummary]]", _workline_value_error_response(exc))
    return cast("ResponseSchemaModel[list[WorkLinePluginSummary]]", response_builder.success(data=list(plugins)))


def _workline_value_error_response(exc: ValueError) -> dict[str, object]:
    message = str(exc)
    if "不存在" in message:
        return response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=message)
    return response_builder.fail(code=BusinessErrorCode.INVALID_STATE, message=message)


def _plane_read_principal(
    request: Request,
    user_id: Annotated[int, Depends(require_auth)],
) -> PlaneReadPrincipal:
    return PlaneReadPrincipal(
        user_id=user_id,
        is_superuser=bool(getattr(request.state, "is_superuser", False)),
    )


@router.get(
    "/work_lines/{id}/configuration-status",
    summary="[biz:workline:configuration-status] 查询作业线配置状态",
    response_model=ResponseSchemaModel[WorkLineConfigurationStatus],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:configuration-status"))],
)
async def get_workline_configuration_status(
    db: AsyncSessionDep,
    request: Request,
    id: int = Path(...),
) -> ResponseSchemaModel[WorkLineConfigurationStatus]:
    """查询 WorkLine 启用前配置状态。"""

    service = getattr(request.app.state, "workline_configuration_service", None)
    if service is None:
        return cast(
            "ResponseSchemaModel[WorkLineConfigurationStatus]",
            response_builder.fail(code=ServerErrorCode.SERVICE_UNAVAILABLE, message="工作线配置服务不可用"),
        )
    try:
        status_data = await service.configuration_status(db, workline_id=id)
    except ValueError as exc:
        return cast("ResponseSchemaModel[WorkLineConfigurationStatus]", _workline_value_error_response(exc))

    return cast(
        "ResponseSchemaModel[WorkLineConfigurationStatus]",
        response_builder.success(data=status_data),
    )


@router.put(
    "/work_lines/{id}/configuration",
    summary="[biz:workline:configure] 保存业务插件配置与设备全集",
    response_model=ResponseSchemaModel[WorkLineConfigurationResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:configure"))],
)
async def save_workline_configuration(
    db: AsyncSessionDep,
    cache: CacheDep,
    request: Request,
    id: int = Path(...),
    payload: WorkLineConfigurationUpdate = Body(...),
) -> ResponseSchemaModel[WorkLineConfigurationResponse]:
    """在一个事务中替换插件配置和 Device 归属。"""

    service = getattr(request.app.state, "workline_configuration_service", None)
    if service is None:
        return cast(
            "ResponseSchemaModel[WorkLineConfigurationResponse]",
            response_builder.fail(code=ServerErrorCode.SERVICE_UNAVAILABLE, message="工作线配置服务不可用"),
        )
    try:
        result = await service.save(
            db,
            workline_id=id,
            version=payload.version,
            plugin_key=payload.plugin_key,
            config=payload.config,
            device_codes=payload.device_codes,
            cache=cache,
        )
    except ValueError as exc:
        return cast("ResponseSchemaModel[WorkLineConfigurationResponse]", _workline_value_error_response(exc))
    data = WorkLineConfigurationResponse(
        workline_id=result.workline.id,
        version=result.workline.version,
        plugin_key=result.workline.plugin_key,
        config=result.workline.config,
        device_codes=result.device_codes,
    )
    return cast(
        "ResponseSchemaModel[WorkLineConfigurationResponse]",
        response_builder.success(data=data),
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
    request: Request,
    id: int = Path(...),
    payload: WorkLineStateTransitionRequest = Body(...),
) -> ResponseSchemaModel[WorkLineResponse]:
    """确认无未完成运行负载后停用 WorkLine。"""

    service = getattr(request.app.state, "workline_configuration_service", None)
    if service is None:
        return cast(
            "ResponseSchemaModel[WorkLineResponse]",
            response_builder.fail(code=ServerErrorCode.SERVICE_UNAVAILABLE, message="工作线配置服务不可用"),
        )
    try:
        updated = await service.deactivate(db, workline_id=id, version=payload.version, cache=cache)
    except ValueError as exc:
        return cast("ResponseSchemaModel[WorkLineResponse]", _workline_value_error_response(exc))

    response_resource = cast("WorkLine", await workline_service.get_by_id(db, cache, id, max_depth=1) or updated)
    return cast(
        "ResponseSchemaModel[WorkLineResponse]",
        response_builder.success(data=workline_service.to_response(response_resource, WorkLineResponse)),
    )


@router.get(
    "/work_lines/{id}/plane/scene",
    summary="[biz:workline:view-plane-scene] 获取作业线平面静态场景",
    response_model=ResponseSchemaModel[PlaneSceneView],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission(plane_read_security_policy.scene_permission))],
)
async def get_workline_plane_scene(
    db: AsyncSessionDep,
    cache: CacheDep,
    principal: Annotated[PlaneReadPrincipal, Depends(_plane_read_principal)],
    id: int = Path(...),
) -> ResponseSchemaModel[PlaneSceneView]:
    """读取 WorkLine 平面态势静态 scene。"""

    try:
        scene = await workline_plane_service.get_scene(db, cache, id, principal=principal)
    except ValueError as exc:
        return cast("ResponseSchemaModel[PlaneSceneView]", _workline_value_error_response(exc))
    await workline_plane_service.record_read_audit(db, view="scene", workline_id=id, workline_code=scene.workline_code)
    return cast("ResponseSchemaModel[PlaneSceneView]", response_builder.success(data=scene))


@router.get(
    "/work_lines/{id}/plane/snapshot",
    summary="[biz:workline:view-plane-snapshot] 获取作业线平面动态快照",
    response_model=ResponseSchemaModel[PlaneSnapshot],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission(plane_read_security_policy.snapshot_permission))],
)
async def get_workline_plane_snapshot(
    db: AsyncSessionDep,
    cache: CacheDep,
    principal: Annotated[PlaneReadPrincipal, Depends(_plane_read_principal)],
    id: int = Path(...),
) -> ResponseSchemaModel[PlaneSnapshot]:
    """读取 WorkLine 平面态势动态 snapshot。"""

    try:
        snapshot = await workline_plane_service.get_snapshot(db, cache, id, principal=principal)
    except ValueError as exc:
        return cast("ResponseSchemaModel[PlaneSnapshot]", _workline_value_error_response(exc))
    await workline_plane_service.record_read_audit(
        db,
        view="snapshot",
        workline_id=id,
        workline_code=snapshot.workline_code,
    )
    return cast("ResponseSchemaModel[PlaneSnapshot]", response_builder.success(data=snapshot))


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
