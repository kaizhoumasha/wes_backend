"""
权限管理 API
"""

from src.app.admin.models import Permission, PermissionCreate, PermissionResponse, PermissionTree, PermissionUpdate
from src.app.admin.services.perm_service import permission_service
from src.core.tree_api import TreeAPI

perm_api = TreeAPI(
    module_name="admin",
    model=Permission,
    service=permission_service,  # type: ignore[arg-type]
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
    tree_response_schema=PermissionTree,
)

router = perm_api.router
