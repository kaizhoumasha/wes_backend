from src.app.api_auth.models import (
    APIAccessLog,
    APIAccessLogCreate,
    APIAccessLogResponse,
    APIAccessLogUpdate,
)
from src.app.api_auth.services import api_access_log_service
from src.core.base_api import BaseAPI

api_access_log_api = BaseAPI(
    module_name="api-auth",
    model=APIAccessLog,
    service=api_access_log_service,
    create_schema=APIAccessLogCreate,
    update_schema=APIAccessLogUpdate,
    response_schema=APIAccessLogResponse,
    prefix="/access-log",
    tags=["API 访问日志"],
    enable_permission=True,
    gen_create=False,
    gen_update=False,
    gen_delete=False,
)

router = api_access_log_api.router
