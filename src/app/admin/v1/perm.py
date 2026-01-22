"""
权限管理 API
"""

from src.app.admin.models import Permission, PermissionCreate, PermissionResponse, PermissionUpdate
from src.app.admin.services.perm_service import permission_service
from src.core.base_api import BaseAPI

perm_api = BaseAPI(
    module_name="admin",
    model=Permission,
    service=permission_service,
    create_schema=PermissionCreate,
    update_schema=PermissionUpdate,
    response_schema=PermissionResponse,
    prefix="/permissions",
    tags=["权限管理"],
    gen_create=True,
    gen_update=True,
    gen_delete=True,
    enable_permission=True,
    max_depth=2,
)

router = perm_api.router
