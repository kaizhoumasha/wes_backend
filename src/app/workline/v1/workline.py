"""WorkLine API 路由"""

from src.app.workline.models import (
    WorkLine,
    WorkLineCreate,
    WorkLineResponse,
    WorkLineUpdate,
)
from src.app.workline.services import workline_service
from src.core.base_api import BaseAPI

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

router = workline_api.router
