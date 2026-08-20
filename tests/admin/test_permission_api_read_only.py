"""权限目录仅允许发现，不允许运行时变更。"""

from src.app.admin.services.perm_service import PermissionService, permission_service


def test_permission_catalog_openapi_is_read_only() -> None:
    """管理端只能读取权限树、详情和语义查询。"""
    from main import app

    paths = app.openapi()["paths"]
    permission_paths = {
        path: set(operations) for path, operations in paths.items() if path.startswith("/api/v1/admin/permissions")
    }

    assert permission_paths["/api/v1/admin/permissions/tree"] == {"get"}
    assert permission_paths["/api/v1/admin/permissions/{id}"] == {"get"}
    assert permission_paths["/api/v1/admin/permissions/query"] == {"post"}
    assert "/api/v1/admin/permissions" not in permission_paths
    assert permission_paths["/api/v1/admin/permissions/siblings/{node_id}"] == {"get"}
    assert permission_paths["/api/v1/admin/permissions/ancestors/{node_id}"] == {"get"}
    assert permission_paths["/api/v1/admin/permissions/children/{node_id}"] == {"get"}
    assert "/api/v1/admin/permissions/trash" not in permission_paths
    assert "/api/v1/admin/permissions/trash/restore" not in permission_paths
    assert "/api/v1/admin/permissions/trash/permanent" not in permission_paths
    assert "/api/v1/admin/permissions/{id}/restore" not in permission_paths
    assert "/api/v1/admin/permissions/{id}/permanent" not in permission_paths
    assert "/api/v1/admin/permissions/move" not in permission_paths
    assert "/api/v1/admin/permissions/batch-sort" not in permission_paths
    assert "/api/v1/api_auth/applications/available-permissions/sync" not in paths


def test_permission_service_does_not_cache_catalog_entities_or_lists() -> None:
    """目录重建后，管理读取不得命中通用实体或列表缓存。"""
    assert permission_service.enable_cache is False
    assert PermissionService().enable_cache is False
