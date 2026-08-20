"""
权限管理 API
"""

from src.app.admin.models import Permission, PermissionResponse, PermissionTree
from src.app.admin.services.perm_service import permission_service
from src.core.tree_api import TreeAPI

perm_api = TreeAPI(
    module_name="admin",
    model=Permission,
    service=permission_service,  # type: ignore[arg-type]
    create_schema=None,
    update_schema=None,
    response_schema=PermissionResponse,
    prefix="/permissions",
    tags=["权限管理"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
    gen_trash=False,
    gen_tree_navigation=False,
    enable_permission=True,
    max_depth=2,
    tree_response_schema=PermissionTree,
)

router = perm_api.router
