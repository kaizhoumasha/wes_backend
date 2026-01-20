"""
角色管理 API
"""

from src.app.admin.models import Role, RoleCreate, RoleResponse, RoleUpdate
from src.app.admin.services.role_service import role_service
from src.core.base_api import BaseAPI

role_api = BaseAPI(
    module_name="admin",
    model=Role,
    service=role_service,
    create_schema=RoleCreate,
    update_schema=RoleUpdate,
    response_schema=RoleResponse,
    prefix="/roles",
    tags=["角色管理"],
    gen_create=True,
    gen_update=True,
    gen_delete=True,
    enable_permission=True,
    max_depth=2,
)

router = role_api.router
