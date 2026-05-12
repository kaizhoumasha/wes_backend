"""WorkLine API 路由"""

from typing import cast

from fastapi import APIRouter, Depends, status

from src.app.workline.models import (
    WorkLine,
    WorkLineCreate,
    WorkLinePluginOption,
    WorkLineResponse,
    WorkLineUpdate,
)
from src.app.workline.services import workline_service
from src.core.base_api import BaseAPI
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder

router = APIRouter(tags=["作业线管理"])


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
