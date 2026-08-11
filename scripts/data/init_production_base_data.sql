-- WES 生产基础/主数据初始化脚本
--
-- 适用范围：已完成 Alembic 迁移后的 PostgreSQL 生产库。
-- 执行方式示例：
--   psql "$DATABASE_URL" \
--     -v admin_password_hash='<bcrypt/argon2 password hash>' \
--     -v admin_email='admin@example.com' \
--     -f scripts/data/init_production_base_data.sql
--
-- 设计约束：
-- - 幂等：按自然键 upsert，可重复执行。
-- - 生产安全：不内置明文默认密码，不写入本地 mock 地址。
-- - 只初始化基础/主数据：角色、权限和菜单。
-- - 不写入过程数据：不初始化 workline_sessions、inbox/outbox、device_commands、active placement/mount 等运行态事实。

\set ON_ERROR_STOP on

\if :{?admin_password_hash}
\else
  \echo 'ERROR: 必须通过 -v admin_password_hash=... 传入已加密的管理员初始密码哈希。'
  \quit 1
\endif

\if :{?admin_email}
\else
  \set admin_email 'admin@example.com'
\endif

BEGIN;

CREATE TEMP TABLE _seed_vars AS
SELECT
    :'admin_password_hash'::text AS admin_password_hash,
    :'admin_email'::text AS admin_email;

DO $$
DECLARE
    v record;
BEGIN
    SELECT * INTO v FROM _seed_vars;
    IF btrim(v.admin_password_hash) = '' OR v.admin_password_hash IN ('admin123', '<CHANGE_ME>') THEN
        RAISE EXCEPTION 'admin_password_hash 必须是生产密码哈希，不能是明文或占位符';
    END IF;
END $$;

-- ============================================================================
-- 1. 系统角色与 break-glass 管理员
-- ============================================================================

CREATE TEMP TABLE _seed_roles(name text PRIMARY KEY, description text);
INSERT INTO _seed_roles(name, description) VALUES
    ('系统管理员', '系统最高权限，拥有所有操作权限'),
    ('管理员', '系统管理员，拥有大部分管理权限'),
    ('运营人员', '日常运营操作人员'),
    ('财务人员', '财务相关操作人员'),
    ('普通用户', '普通用户，基础查看权限');

INSERT INTO wes_sys.roles (created_at, updated_at, version, is_deleted, name, description)
SELECT now() AT TIME ZONE 'UTC', NULL, 0, false, name, description
FROM _seed_roles
ON CONFLICT (name) WHERE NOT is_deleted DO UPDATE SET
    updated_at = now() AT TIME ZONE 'UTC',
    description = EXCLUDED.description;

INSERT INTO wes_sys.users (
    created_at, updated_at, version, is_deleted,
    username, email, full_name, hashed_password, is_superuser, is_multi_login
)
SELECT
    now() AT TIME ZONE 'UTC', NULL, 0, false,
    'admin', v.admin_email, '系统管理员', v.admin_password_hash, true, true
FROM _seed_vars v
ON CONFLICT (username) WHERE NOT is_deleted DO UPDATE SET
    updated_at = now() AT TIME ZONE 'UTC',
    email = EXCLUDED.email,
    full_name = EXCLUDED.full_name,
    hashed_password = EXCLUDED.hashed_password,
    is_superuser = true,
    is_multi_login = true;

INSERT INTO wes_sys.user_roles(user_id, role_id)
SELECT u.id, r.id
FROM wes_sys.users u
JOIN wes_sys.roles r ON r.name = '系统管理员' AND r.is_deleted = false
WHERE u.username = 'admin' AND u.is_deleted = false
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 2. API 权限树
-- ============================================================================

CREATE TEMP TABLE _seed_permissions(
    name text PRIMARY KEY,
    parent_name text,
    description text,
    type text NOT NULL,
    category text,
    resource text,
    action text,
    method text,
    path text,
    sort_order integer NOT NULL,
    level_order integer NOT NULL
);

INSERT INTO _seed_permissions(
    name, parent_name, description, type, category, resource, action, method, path, sort_order, level_order
) VALUES
('admin:system:group', NULL, 'admin 模块权限分组', 'user_api', 'admin', 'system', 'group', 'GET', '/admin', 1, 1),
('api:system:group', NULL, 'api 模块权限分组', 'app_api', 'api', 'system', 'group', 'GET', '/api', 1, 1),
('sys:system:group', NULL, 'sys 模块权限分组', 'user_api', 'sys', 'system', 'group', 'GET', '/sys', 2, 1),
('biz:system:group', NULL, 'biz 模块权限分组', 'user_api', 'biz', 'system', 'group', 'GET', '/biz', 3, 1),
('resource:system:group', NULL, 'resource 模块权限分组', 'user_api', 'resource', 'system', 'group', 'GET', '/resource', 4, 1),
('api-auth:system:group', NULL, 'api-auth 模块权限分组', 'user_api', 'api-auth', 'system', 'group', 'GET', '/api-auth', 5, 1),
('callback:system:group', NULL, 'callback 模块权限分组', 'user_api', 'callback', 'system', 'group', 'GET', '/callback', 6, 1),
('admin:user:group', 'admin:system:group', 'user 权限分组', 'user_api', 'admin', 'user', 'group', 'GET', '/admin/user', 1, 2),
('admin:role:group', 'admin:system:group', 'role 权限分组', 'user_api', 'admin', 'role', 'group', 'GET', '/admin/role', 2, 2),
('admin:permission:group', 'admin:system:group', 'permission 权限分组', 'user_api', 'admin', 'permission', 'group', 'GET', '/admin/permission', 3, 2),
('admin:audit:group', 'admin:system:group', '审计日志权限分组', 'user_api', 'admin', 'audit', 'group', 'GET', '/admin/audit-logs', 4, 2),
('admin:menu:group', 'admin:system:group', 'menu 权限分组', 'user_api', 'admin', 'menu', 'group', 'GET', '/admin/menu', 4, 2),
('api-auth:api_application:group', 'api-auth:system:group', 'api_application 权限分组', 'user_api', 'api-auth', 'api_application', 'group', 'GET', '/api-auth/api-application', 1, 2),
('api-auth:apiaccesslog:group', 'api-auth:system:group', 'apiaccesslog 权限分组', 'user_api', 'api-auth', 'apiaccesslog', 'group', 'GET', '/api-auth/apiaccesslog', 2, 2),
('api:try:group', 'api:system:group', 'try 权限分组', 'app_api', 'api', 'try', 'group', 'GET', '/api/try', 1, 2),
('api:callback:group', 'api:system:group', 'callback 权限分组', 'app_api', 'api', 'callback', 'group', 'GET', '/api/callback', 2, 2),
('biz:workline:group', 'biz:system:group', 'workline 权限分组', 'user_api', 'biz', 'workline', 'group', 'GET', '/biz/workline', 1, 2),
('biz:device:group', 'biz:system:group', 'device 权限分组', 'user_api', 'biz', 'device', 'group', 'GET', '/biz/device', 2, 2),
('callback:callback_log:group', 'callback:system:group', 'callback_log 权限分组', 'user_api', 'callback', 'callback_log', 'group', 'GET', '/callback/callback-log', 1, 2),
('resource:racktype:group', 'resource:system:group', 'racktype 权限分组', 'user_api', 'resource', 'racktype', 'group', 'GET', '/resource/racktype', 1, 2),
('resource:rackslottemplate:group', 'resource:system:group', 'rackslottemplate 权限分组', 'user_api', 'resource', 'rackslottemplate', 'group', 'GET', '/resource/rackslottemplate', 2, 2),
('resource:rack:group', 'resource:system:group', 'rack 权限分组', 'user_api', 'resource', 'rack', 'group', 'GET', '/resource/rack', 3, 2),
('resource:bintype:group', 'resource:system:group', 'bintype 权限分组', 'user_api', 'resource', 'bintype', 'group', 'GET', '/resource/bintype', 4, 2),
('resource:binslottemplate:group', 'resource:system:group', 'binslottemplate 权限分组', 'user_api', 'resource', 'binslottemplate', 'group', 'GET', '/resource/binslottemplate', 5, 2),
('resource:bin:group', 'resource:system:group', 'bin 权限分组', 'user_api', 'resource', 'bin', 'group', 'GET', '/resource/bin', 6, 2),
('resource:resourcestateevent:group', 'resource:system:group', 'resourcestateevent 权限分组', 'user_api', 'resource', 'resourcestateevent', 'group', 'GET', '/resource/resourcestateevent', 7, 2),
('resource:rackplacement:group', 'resource:system:group', 'rackplacement 权限分组', 'user_api', 'resource', 'rackplacement', 'group', 'GET', '/resource/rackplacement', 8, 2),
('resource:rackbinmount:group', 'resource:system:group', 'rackbinmount 权限分组', 'user_api', 'resource', 'rackbinmount', 'group', 'GET', '/resource/rackbinmount', 9, 2),
('resource:binmaterialmount:group', 'resource:system:group', 'binmaterialmount 权限分组', 'user_api', 'resource', 'binmaterialmount', 'group', 'GET', '/resource/binmaterialmount', 10, 2),
('resource:bincelloccupancy:group', 'resource:system:group', 'bincelloccupancy 权限分组', 'user_api', 'resource', 'bincelloccupancy', 'group', 'GET', '/resource/bincelloccupancy', 11, 2),
('resource:bincontentsnapshot:group', 'resource:system:group', 'bincontentsnapshot 权限分组', 'user_api', 'resource', 'bincontentsnapshot', 'group', 'GET', '/resource/bincontentsnapshot', 12, 2),
('resource:bincontentsnapshotitem:group', 'resource:system:group', 'bincontentsnapshotitem 权限分组', 'user_api', 'resource', 'bincontentsnapshotitem', 'group', 'GET', '/resource/bincontentsnapshotitem', 13, 2),
('sys:auditlog:group', 'sys:system:group', 'auditlog 权限分组', 'user_api', 'sys', 'auditlog', 'group', 'GET', '/sys/auditlog', 1, 2),
('admin:audit:list', 'admin:audit:group', '查询审计日志', 'user_api', 'admin', 'audit', 'read', 'POST', '/api/v1/admin/audit-logs/query', 1, 3),
('admin:audit:export', 'admin:audit:group', '导出审计日志', 'user_api', 'admin', 'audit', 'export', 'GET', '/api/v1/admin/audit-logs/export', 2, 3),
('admin:menu:tree', 'admin:menu:group', 'get_tree', 'user_api', 'admin', 'menu', 'tree', 'GET', '/api/v1/admin/menus/tree', 1, 3),
('admin:menu:update', 'admin:menu:group', 'move_node', 'user_api', 'admin', 'menu', 'update', 'PUT', '/api/v1/admin/menus/move', 2, 3),
('admin:menu:restore', 'admin:menu:group', '批量恢复Menu', 'user_api', 'admin', 'menu', 'restore', 'POST', '/api/v1/admin/menus/trash/restore', 3, 3),
('admin:menu:permanent_delete', 'admin:menu:group', '批量永久删除Menu', 'user_api', 'admin', 'menu', 'permanent_delete', 'DELETE', '/api/v1/admin/menus/trash/permanent', 4, 3),
('admin:menu:trash', 'admin:menu:group', '获取已删除Menu', 'user_api', 'admin', 'menu', 'trash', 'GET', '/api/v1/admin/menus/trash', 5, 3),
('admin:menu:create', 'admin:menu:group', '创建Menu', 'user_api', 'admin', 'menu', 'create', 'POST', '/api/v1/admin/menus', 6, 3),
('admin:menu:delete', 'admin:menu:group', '删除Menu', 'user_api', 'admin', 'menu', 'delete', 'DELETE', '/api/v1/admin/menus/{id}', 7, 3),
('admin:menu:detail', 'admin:menu:group', '获取Menu', 'user_api', 'admin', 'menu', 'detail', 'GET', '/api/v1/admin/menus/{id}', 8, 3),
('admin:menu:list', 'admin:menu:group', '获取Menu列表', 'user_api', 'admin', 'menu', 'list', 'POST', '/api/v1/admin/menus/query', 9, 3),
('admin:permission:tree', 'admin:permission:group', 'get_tree', 'user_api', 'admin', 'permission', 'tree', 'GET', '/api/v1/admin/permissions/tree', 1, 3),
('admin:permission:update', 'admin:permission:group', 'move_node', 'user_api', 'admin', 'permission', 'update', 'PUT', '/api/v1/admin/permissions/move', 2, 3),
('admin:permission:restore', 'admin:permission:group', '批量恢复Permission', 'user_api', 'admin', 'permission', 'restore', 'POST', '/api/v1/admin/permissions/trash/restore', 3, 3),
('admin:permission:permanent_delete', 'admin:permission:group', '批量永久删除Permission', 'user_api', 'admin', 'permission', 'permanent_delete', 'DELETE', '/api/v1/admin/permissions/trash/permanent', 4, 3),
('admin:permission:trash', 'admin:permission:group', '获取已删除Permission', 'user_api', 'admin', 'permission', 'trash', 'GET', '/api/v1/admin/permissions/trash', 5, 3),
('admin:permission:create', 'admin:permission:group', '创建Permission', 'user_api', 'admin', 'permission', 'create', 'POST', '/api/v1/admin/permissions', 6, 3),
('admin:permission:delete', 'admin:permission:group', '删除Permission', 'user_api', 'admin', 'permission', 'delete', 'DELETE', '/api/v1/admin/permissions/{id}', 7, 3),
('admin:permission:detail', 'admin:permission:group', '获取Permission', 'user_api', 'admin', 'permission', 'detail', 'GET', '/api/v1/admin/permissions/{id}', 8, 3),
('admin:permission:list', 'admin:permission:group', '获取Permission列表', 'user_api', 'admin', 'permission', 'list', 'POST', '/api/v1/admin/permissions/query', 9, 3),
('admin:role:restore', 'admin:role:group', '批量恢复Role', 'user_api', 'admin', 'role', 'restore', 'POST', '/api/v1/admin/roles/trash/restore', 1, 3),
('admin:role:permanent_delete', 'admin:role:group', '批量永久删除Role', 'user_api', 'admin', 'role', 'permanent_delete', 'DELETE', '/api/v1/admin/roles/trash/permanent', 2, 3),
('admin:role:trash', 'admin:role:group', '获取已删除Role', 'user_api', 'admin', 'role', 'trash', 'GET', '/api/v1/admin/roles/trash', 3, 3),
('admin:role:create', 'admin:role:group', '创建Role', 'user_api', 'admin', 'role', 'create', 'POST', '/api/v1/admin/roles', 4, 3),
('admin:role:update', 'admin:role:group', '更新Role', 'user_api', 'admin', 'role', 'update', 'PUT', '/api/v1/admin/roles/{id}', 5, 3),
('admin:role:delete', 'admin:role:group', '删除Role', 'user_api', 'admin', 'role', 'delete', 'DELETE', '/api/v1/admin/roles/{id}', 6, 3),
('admin:role:detail', 'admin:role:group', '获取Role', 'user_api', 'admin', 'role', 'detail', 'GET', '/api/v1/admin/roles/{id}', 7, 3),
('admin:role:list', 'admin:role:group', '获取Role列表', 'user_api', 'admin', 'role', 'list', 'POST', '/api/v1/admin/roles/query', 8, 3),
('admin:user:stats', 'admin:user:group', '获取缓存统计', 'user_api', 'admin', 'user', 'stats', 'GET', '/api/v1/admin/users/stats/cache', 1, 3),
('admin:user:reset-password', 'admin:user:group', '重置用户密码', 'user_api', 'admin', 'user', 'reset-password', 'PUT', '/api/v1/admin/users/{id}/reset-password', 2, 3),
('admin:user:assign-roles', 'admin:user:group', '为用户分配角色', 'user_api', 'admin', 'user', 'assign-roles', 'PUT', '/api/v1/admin/users/{id}/assign-roles', 3, 3),
('admin:user:bulk_delete', 'admin:user:group', '批量删除User', 'user_api', 'admin', 'user', 'bulk_delete', 'DELETE', '/api/v1/admin/users/bulk', 4, 3),
('admin:user:restore', 'admin:user:group', '批量恢复User', 'user_api', 'admin', 'user', 'restore', 'POST', '/api/v1/admin/users/trash/restore', 5, 3),
('admin:user:export', 'admin:user:group', '导出用户数据', 'user_api', 'admin', 'user', 'export', 'GET', '/api/v1/admin/users/export', 6, 3),
('admin:user:permanent_delete', 'admin:user:group', '批量永久删除User', 'user_api', 'admin', 'user', 'permanent_delete', 'DELETE', '/api/v1/admin/users/trash/permanent', 6, 3),
('admin:user:trash', 'admin:user:group', '获取已删除User', 'user_api', 'admin', 'user', 'trash', 'GET', '/api/v1/admin/users/trash', 7, 3),
('admin:user:create', 'admin:user:group', '创建User', 'user_api', 'admin', 'user', 'create', 'POST', '/api/v1/admin/users', 8, 3),
('admin:user:update', 'admin:user:group', '更新User', 'user_api', 'admin', 'user', 'update', 'PUT', '/api/v1/admin/users/{id}', 9, 3),
('admin:user:delete', 'admin:user:group', '删除User', 'user_api', 'admin', 'user', 'delete', 'DELETE', '/api/v1/admin/users/{id}', 10, 3),
('admin:user:detail', 'admin:user:group', '获取User', 'user_api', 'admin', 'user', 'detail', 'GET', '/api/v1/admin/users/{id}', 11, 3),
('admin:user:list', 'admin:user:group', '获取User列表', 'user_api', 'admin', 'user', 'list', 'POST', '/api/v1/admin/users/query', 12, 3),
('api-auth:api_application:list_permissions', 'api-auth:api_application:group', '获取系统支持的 API 权限列表', 'user_api', 'api-auth', 'api_application', 'list_permissions', 'GET', '/api/v1/api_auth/applications/available-permissions', 1, 3),
('api-auth:api_application:sync_permissions', 'api-auth:api_application:group', '重新扫描并同步 API 权限', 'user_api', 'api-auth', 'api_application', 'sync_permissions', 'POST', '/api/v1/api_auth/applications/available-permissions/sync', 2, 3),
('api-auth:api_application:create', 'api-auth:api_application:group', '创建 API 应用', 'user_api', 'api-auth', 'api_application', 'create', 'POST', '/api/v1/api_auth/applications', 3, 3),
('api-auth:api_application:revoke', 'api-auth:api_application:group', '撤销 API 应用', 'user_api', 'api-auth', 'api_application', 'revoke', 'POST', '/api/v1/api_auth/applications/{id}/revoke', 4, 3),
('api-auth:api_application:reset_validity', 'api-auth:api_application:group', '重置应用有效期', 'user_api', 'api-auth', 'api_application', 'reset_validity', 'POST', '/api/v1/api_auth/applications/{id}/reset-validity', 5, 3),
('api-auth:api_application:assign_permission', 'api-auth:api_application:group', '分配权限', 'user_api', 'api-auth', 'api_application', 'assign_permission', 'POST', '/api/v1/api_auth/applications/{id}/permissions', 6, 3),
('api-auth:api_application:reset_secret', 'api-auth:api_application:group', '重置应用密钥', 'user_api', 'api-auth', 'api_application', 'reset_secret', 'POST', '/api/v1/api_auth/applications/{id}/reset-secret', 7, 3),
('api-auth:api_application:restore', 'api-auth:api_application:group', '批量恢复APIApplication', 'user_api', 'api-auth', 'api_application', 'restore', 'POST', '/api/v1/api_auth/applications/trash/restore', 8, 3),
('api-auth:api_application:permanent_delete', 'api-auth:api_application:group', '批量永久删除APIApplication', 'user_api', 'api-auth', 'api_application', 'permanent_delete', 'DELETE', '/api/v1/api_auth/applications/trash/permanent', 9, 3),
('api-auth:api_application:trash', 'api-auth:api_application:group', '获取已删除APIApplication', 'user_api', 'api-auth', 'api_application', 'trash', 'GET', '/api/v1/api_auth/applications/trash', 10, 3),
('api-auth:api_application:update', 'api-auth:api_application:group', '更新APIApplication', 'user_api', 'api-auth', 'api_application', 'update', 'PUT', '/api/v1/api_auth/applications/{id}', 11, 3),
('api-auth:api_application:delete', 'api-auth:api_application:group', '删除APIApplication', 'user_api', 'api-auth', 'api_application', 'delete', 'DELETE', '/api/v1/api_auth/applications/{id}', 12, 3),
('api-auth:api_application:detail', 'api-auth:api_application:group', '获取APIApplication', 'user_api', 'api-auth', 'api_application', 'detail', 'GET', '/api/v1/api_auth/applications/{id}', 13, 3),
('api-auth:api_application:list', 'api-auth:api_application:group', '获取APIApplication列表', 'user_api', 'api-auth', 'api_application', 'list', 'POST', '/api/v1/api_auth/applications/query', 14, 3),
('api-auth:apiaccesslog:detail', 'api-auth:apiaccesslog:group', '获取APIAccessLog', 'user_api', 'api-auth', 'apiaccesslog', 'detail', 'GET', '/api/v1/api_auth/access-log/{id}', 1, 3),
('api-auth:apiaccesslog:list', 'api-auth:apiaccesslog:group', '获取APIAccessLog列表', 'user_api', 'api-auth', 'apiaccesslog', 'list', 'POST', '/api/v1/api_auth/access-log/query', 2, 3),
('api:callback:result', 'api:callback:group', '任务结果回传', 'app_api', 'api', 'callback', 'result', 'POST', '/api/v1/callback/result', 1, 3),
('api:callback:event', 'api:callback:group', '设备事件上报', 'app_api', 'api', 'callback', 'event', 'POST', '/api/v1/callback/event', 2, 3),
('api:try:invoke', 'api:try:group', '测试 API 调用', 'app_api', 'api', 'try', 'invoke', 'POST', '/api/v1/api_auth/applications/try/invoke', 1, 3),
('biz:device:list', 'biz:device:group', '设备运行态列表', 'user_api', 'biz', 'device', 'list', 'GET', '/api/v1/workline/runtime/devices', 1, 3),
('biz:device:update', 'biz:device:group', '设备进入维护', 'user_api', 'biz', 'device', 'update', 'POST', '/api/v1/device/devices/{id}/runtime/enter-maintenance', 2, 3),
('biz:device:restore', 'biz:device:group', '批量恢复Device', 'user_api', 'biz', 'device', 'restore', 'POST', '/api/v1/device/devices/trash/restore', 3, 3),
('biz:device:permanent_delete', 'biz:device:group', '批量永久删除Device', 'user_api', 'biz', 'device', 'permanent_delete', 'DELETE', '/api/v1/device/devices/trash/permanent', 4, 3),
('biz:device:trash', 'biz:device:group', '获取已删除Device', 'user_api', 'biz', 'device', 'trash', 'GET', '/api/v1/device/devices/trash', 5, 3),
('biz:device:create', 'biz:device:group', '创建Device', 'user_api', 'biz', 'device', 'create', 'POST', '/api/v1/device/devices', 6, 3),
('biz:device:delete', 'biz:device:group', '删除Device', 'user_api', 'biz', 'device', 'delete', 'DELETE', '/api/v1/device/devices/{id}', 7, 3),
('biz:device:detail', 'biz:device:group', '获取Device', 'user_api', 'biz', 'device', 'detail', 'GET', '/api/v1/device/devices/{id}', 8, 3),
('biz:workline:list', 'biz:workline:group', '获取WorkLine列表', 'user_api', 'biz', 'workline', 'list', 'POST', '/api/v1/workline/work_lines/query', 1, 3),
('biz:workline:detail', 'biz:workline:group', '查询作业线配置状态', 'user_api', 'biz', 'workline', 'detail', 'GET', '/api/v1/workline/work_lines/{id}/configuration-status', 2, 3),
('biz:workline:activate', 'biz:workline:group', '启用作业线', 'user_api', 'biz', 'workline', 'activate', 'POST', '/api/v1/workline/work_lines/{id}/activate', 3, 3),
('biz:workline:deactivate', 'biz:workline:group', '停用作业线', 'user_api', 'biz', 'workline', 'deactivate', 'POST', '/api/v1/workline/work_lines/{id}/deactivate', 4, 3),
('biz:workline:restore', 'biz:workline:group', '批量恢复WorkLine', 'user_api', 'biz', 'workline', 'restore', 'POST', '/api/v1/workline/work_lines/trash/restore', 5, 3),
('biz:workline:permanent_delete', 'biz:workline:group', '批量永久删除WorkLine', 'user_api', 'biz', 'workline', 'permanent_delete', 'DELETE', '/api/v1/workline/work_lines/trash/permanent', 6, 3),
('biz:workline:trash', 'biz:workline:group', '获取已删除WorkLine', 'user_api', 'biz', 'workline', 'trash', 'GET', '/api/v1/workline/work_lines/trash', 7, 3),
('biz:workline:create', 'biz:workline:group', '创建WorkLine', 'user_api', 'biz', 'workline', 'create', 'POST', '/api/v1/workline/work_lines', 8, 3),
('biz:workline:update', 'biz:workline:group', '更新WorkLine', 'user_api', 'biz', 'workline', 'update', 'PUT', '/api/v1/workline/work_lines/{id}', 9, 3),
('biz:workline:delete', 'biz:workline:group', '删除WorkLine', 'user_api', 'biz', 'workline', 'delete', 'DELETE', '/api/v1/workline/work_lines/{id}', 10, 3),
('biz:workline:view-runtime-hold', 'biz:workline:group', '查询 Runtime Hold NG 原因选项', 'user_api', 'biz', 'workline', 'view-runtime-hold', 'GET', '/api/v1/workline/runtime-holds/ng-reasons', 11, 3),
('biz:workline:resolve-runtime-hold', 'biz:workline:group', '解除 Runtime Hold', 'user_api', 'biz', 'workline', 'resolve-runtime-hold', 'POST', '/api/v1/workline/runtime-holds/{hold_id}/resolve', 12, 3),
('biz:workline:list-ng-return-item', 'biz:workline:group', '查询 NG Return Items', 'user_api', 'biz', 'workline', 'list-ng-return-item', 'GET', '/api/v1/workline/ng-return-items', 13, 3),
('biz:workline:resolve-reconciliation', 'biz:workline:group', '解除 runtime reconciliation 隔离，不重发设备命令、不调用 timeout 插件处理、释放安全停靠队列', 'user_api', 'biz', 'workline', 'resolve-reconciliation', 'POST', '/api/v1/workline/operations/reconciliations/sessions/{session_id}/resolve', 14, 3),
('biz:workline:clear-estop', 'biz:workline:group', '人工确认 checklist 后清除工作线急停', 'user_api', 'biz', 'workline', 'clear-estop', 'POST', '/api/v1/workline/operations/safety/worklines/{workline_id}/clear-estop', 15, 3),
('biz:workline:cleanup-sandbox', 'biz:workline:group', '清理工作线沙箱运行时数据', 'user_api', 'biz', 'workline', 'cleanup-sandbox', 'POST', '/api/v1/workline/operations/sandbox/worklines/{workline_id}/cleanup', 16, 3),
('biz:workline:cleanup-debug-data', 'biz:workline:group', '清理工作线调试过程数据', 'user_api', 'biz', 'workline', 'cleanup-debug-data', 'POST', '/api/v1/workline/operations/debug-data/worklines/{workline_id}/cleanup', 17, 3),
('callback:callback_log:detail', 'callback:callback_log:group', '根据请求 ID 查询回调日志', 'user_api', 'callback', 'callback_log', 'detail', 'GET', '/api/v1/callback/logs/request/{request_id}', 1, 3),
('callback:callback_log:list', 'callback:callback_log:group', '根据 Trace ID 查询回调日志', 'user_api', 'callback', 'callback_log', 'list', 'GET', '/api/v1/callback/logs/trace/{trace_id}', 2, 3),
('resource:bin:detail', 'resource:bin:group', '获取Bin', 'user_api', 'resource', 'bin', 'detail', 'GET', '/api/v1/resource/bins/{id}', 1, 3),
('resource:bin:list', 'resource:bin:group', '获取Bin列表', 'user_api', 'resource', 'bin', 'list', 'POST', '/api/v1/resource/bins/query', 2, 3),
('resource:bincelloccupancy:detail', 'resource:bincelloccupancy:group', '获取BinCellOccupancy', 'user_api', 'resource', 'bincelloccupancy', 'detail', 'GET', '/api/v1/resource/bin-cell-occupancies/{id}', 1, 3),
('resource:bincelloccupancy:list', 'resource:bincelloccupancy:group', '获取BinCellOccupancy列表', 'user_api', 'resource', 'bincelloccupancy', 'list', 'POST', '/api/v1/resource/bin-cell-occupancies/query', 2, 3),
('resource:bincontentsnapshot:detail', 'resource:bincontentsnapshot:group', '获取BinContentSnapshot', 'user_api', 'resource', 'bincontentsnapshot', 'detail', 'GET', '/api/v1/resource/bin-content-snapshots/{id}', 1, 3),
('resource:bincontentsnapshot:list', 'resource:bincontentsnapshot:group', '获取BinContentSnapshot列表', 'user_api', 'resource', 'bincontentsnapshot', 'list', 'POST', '/api/v1/resource/bin-content-snapshots/query', 2, 3),
('resource:bincontentsnapshotitem:detail', 'resource:bincontentsnapshotitem:group', '获取BinContentSnapshotItem', 'user_api', 'resource', 'bincontentsnapshotitem', 'detail', 'GET', '/api/v1/resource/bin-content-snapshot-items/{id}', 1, 3),
('resource:bincontentsnapshotitem:list', 'resource:bincontentsnapshotitem:group', '获取BinContentSnapshotItem列表', 'user_api', 'resource', 'bincontentsnapshotitem', 'list', 'POST', '/api/v1/resource/bin-content-snapshot-items/query', 2, 3),
('resource:binmaterialmount:detail', 'resource:binmaterialmount:group', '获取BinMaterialMount', 'user_api', 'resource', 'binmaterialmount', 'detail', 'GET', '/api/v1/resource/bin-material-mounts/{id}', 1, 3),
('resource:binmaterialmount:list', 'resource:binmaterialmount:group', '获取BinMaterialMount列表', 'user_api', 'resource', 'binmaterialmount', 'list', 'POST', '/api/v1/resource/bin-material-mounts/query', 2, 3),
('resource:binslottemplate:detail', 'resource:binslottemplate:group', '获取BinSlotTemplate', 'user_api', 'resource', 'binslottemplate', 'detail', 'GET', '/api/v1/resource/bin-slot-templates/{id}', 1, 3),
('resource:binslottemplate:list', 'resource:binslottemplate:group', '获取BinSlotTemplate列表', 'user_api', 'resource', 'binslottemplate', 'list', 'POST', '/api/v1/resource/bin-slot-templates/query', 2, 3),
('resource:bintype:detail', 'resource:bintype:group', '获取BinType', 'user_api', 'resource', 'bintype', 'detail', 'GET', '/api/v1/resource/bin-types/{id}', 1, 3),
('resource:bintype:list', 'resource:bintype:group', '获取BinType列表', 'user_api', 'resource', 'bintype', 'list', 'POST', '/api/v1/resource/bin-types/query', 2, 3),
('resource:rack:detail', 'resource:rack:group', '获取Rack', 'user_api', 'resource', 'rack', 'detail', 'GET', '/api/v1/resource/racks/{id}', 1, 3),
('resource:rack:list', 'resource:rack:group', '获取Rack列表', 'user_api', 'resource', 'rack', 'list', 'POST', '/api/v1/resource/racks/query', 2, 3),
('resource:rackbinmount:detail', 'resource:rackbinmount:group', '获取RackBinMount', 'user_api', 'resource', 'rackbinmount', 'detail', 'GET', '/api/v1/resource/rack-bin-mounts/{id}', 1, 3),
('resource:rackbinmount:list', 'resource:rackbinmount:group', '获取RackBinMount列表', 'user_api', 'resource', 'rackbinmount', 'list', 'POST', '/api/v1/resource/rack-bin-mounts/query', 2, 3),
('resource:rackplacement:detail', 'resource:rackplacement:group', '获取RackPlacement', 'user_api', 'resource', 'rackplacement', 'detail', 'GET', '/api/v1/resource/rack-placements/{id}', 1, 3),
('resource:rackplacement:list', 'resource:rackplacement:group', '获取RackPlacement列表', 'user_api', 'resource', 'rackplacement', 'list', 'POST', '/api/v1/resource/rack-placements/query', 2, 3),
('resource:rackslottemplate:detail', 'resource:rackslottemplate:group', '获取RackSlotTemplate', 'user_api', 'resource', 'rackslottemplate', 'detail', 'GET', '/api/v1/resource/rack-slot-templates/{id}', 1, 3),
('resource:rackslottemplate:list', 'resource:rackslottemplate:group', '获取RackSlotTemplate列表', 'user_api', 'resource', 'rackslottemplate', 'list', 'POST', '/api/v1/resource/rack-slot-templates/query', 2, 3),
('resource:racktype:detail', 'resource:racktype:group', '获取RackType', 'user_api', 'resource', 'racktype', 'detail', 'GET', '/api/v1/resource/rack-types/{id}', 1, 3),
('resource:racktype:list', 'resource:racktype:group', '获取RackType列表', 'user_api', 'resource', 'racktype', 'list', 'POST', '/api/v1/resource/rack-types/query', 2, 3),
('resource:resourcestateevent:detail', 'resource:resourcestateevent:group', '获取ResourceStateEvent', 'user_api', 'resource', 'resourcestateevent', 'detail', 'GET', '/api/v1/resource/state-events/{id}', 1, 3),
('resource:resourcestateevent:list', 'resource:resourcestateevent:group', '获取ResourceStateEvent列表', 'user_api', 'resource', 'resourcestateevent', 'list', 'POST', '/api/v1/resource/state-events/query', 2, 3),
('sys:auditlog:detail', 'sys:auditlog:group', '获取AuditLog', 'user_api', 'sys', 'auditlog', 'detail', 'GET', '/api/v1/sys/audit-logs/{id}', 1, 3),
('sys:auditlog:list', 'sys:auditlog:group', '获取AuditLog列表', 'user_api', 'sys', 'auditlog', 'list', 'POST', '/api/v1/sys/audit-logs/query', 2, 3);

DO $$
DECLARE
    r record;
    v_id bigint;
    v_parent_id bigint;
    v_parent_tree text;
    v_parent_level integer;
BEGIN
    FOR r IN SELECT * FROM _seed_permissions ORDER BY level_order, sort_order, name LOOP
        v_parent_id := NULL;
        IF r.parent_name IS NOT NULL THEN
            SELECT id INTO v_parent_id
            FROM wes_sys.permissions
            WHERE name = r.parent_name AND is_deleted = false
            LIMIT 1;
            IF v_parent_id IS NULL THEN
                RAISE EXCEPTION '权限 % 的父权限 % 不存在', r.name, r.parent_name;
            END IF;
        END IF;

        SELECT id INTO v_id
        FROM wes_sys.permissions
        WHERE name = r.name AND is_deleted = false
        LIMIT 1;

        IF v_id IS NULL THEN
            INSERT INTO wes_sys.permissions (
                created_at, updated_at, version, is_deleted,
                parent_id, tree_path, level, sort_order, has_children,
                name, description, type, category, resource, action, method, path
            ) VALUES (
                now() AT TIME ZONE 'UTC', NULL, 0, false,
                v_parent_id, '/', 1, r.sort_order, false,
                r.name, r.description, r.type, r.category, r.resource, r.action, r.method, r.path
            ) RETURNING id INTO v_id;
        ELSE
            UPDATE wes_sys.permissions SET
                updated_at = now() AT TIME ZONE 'UTC',
                parent_id = v_parent_id,
                sort_order = r.sort_order,
                name = r.name,
                description = r.description,
                type = r.type,
                category = r.category,
                resource = r.resource,
                action = r.action,
                method = r.method,
                path = r.path
            WHERE id = v_id;
        END IF;

        IF v_parent_id IS NULL THEN
            UPDATE wes_sys.permissions
            SET tree_path = '/' || v_id || '/', level = 1
            WHERE id = v_id;
        ELSE
            SELECT tree_path, level INTO v_parent_tree, v_parent_level
            FROM wes_sys.permissions
            WHERE id = v_parent_id;
            UPDATE wes_sys.permissions
            SET tree_path = v_parent_tree || v_id || '/', level = v_parent_level + 1
            WHERE id = v_id;
        END IF;
    END LOOP;

    UPDATE wes_sys.permissions p
    SET has_children = EXISTS (
        SELECT 1 FROM wes_sys.permissions c WHERE c.parent_id = p.id AND c.is_deleted = false
    )
    WHERE p.is_deleted = false;
END $$;

-- 内置角色权限规则，与应用同步逻辑保持一致。
INSERT INTO wes_sys.role_permissions(role_id, permission_id)
SELECT r.id, p.id
FROM wes_sys.roles r
JOIN wes_sys.permissions p ON p.is_deleted = false
WHERE r.is_deleted = false
  AND (
    r.name = '系统管理员'
    OR (r.name = '管理员' AND p.name LIKE 'admin:%')
    OR (r.name = '运营人员' AND (p.name LIKE '%:list' OR p.name LIKE '%:detail' OR p.name LIKE '%:tree'))
    OR (r.name = '财务人员' AND p.name LIKE 'admin:audit:%')
    OR (r.name = '普通用户' AND (p.name LIKE '%:list' OR p.name LIKE '%:detail'))
  )
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 3. 前端菜单树
-- ============================================================================

CREATE TEMP TABLE _seed_menus(
    name text PRIMARY KEY,
    parent_name text,
    title text NOT NULL,
    path text NOT NULL,
    component text,
    icon text,
    is_hidden boolean NOT NULL,
    sort_order integer NOT NULL,
    level_order integer NOT NULL
);

INSERT INTO _seed_menus(name, parent_name, title, path, component, icon, is_hidden, sort_order, level_order) VALUES
('system:dashboard:menu', NULL, '仪表盘', '/dashboard', 'views/dashboard/Dashboard.vue', NULL, false, 1, 1),
('admin:system:menu', NULL, '系统管理', '/admin', NULL, 'ep:setting', false, 10, 1),
('biz:system:menu', NULL, '业务管理', '/biz', NULL, 'ep:box', false, 20, 1),
('api-auth:system:menu', NULL, 'API 认证', '/api-auth', NULL, 'ep:key', false, 30, 1),
('runtime:system:menu', NULL, '运行监控中心', '/runtime', 'views/runtime/RuntimeLayout.vue', 'ep:monitor', false, 30, 1),
('logs:system:menu', NULL, '日志中心', '/logs', NULL, 'ep:document', false, 40, 1),
('admin:permission:menu', 'admin:system:menu', '权限管理', '/admin/permissions', 'views/admin/permissions/PermissionListPage.vue', 'ep:lock', false, 96, 2),
('admin:menu:menu', 'admin:system:menu', '菜单管理', '/admin/menus', 'views/admin/menus/MenuListPage.vue', 'ep:menu', false, 97, 2),
('admin:role:menu', 'admin:system:menu', '角色管理', '/admin/roles', 'views/admin/roles/RoleListPage.vue', 'ep:collection-tag', false, 98, 2),
('admin:user:menu', 'admin:system:menu', '用户管理', '/admin/users', 'views/admin/users/UserListPage.vue', 'ep:user', false, 99, 2),
('api-auth:application:menu', 'api-auth:system:menu', 'API 应用管理', '/api-auth/applications', 'views/admin/api-applications/APIApplicationListPage.vue', 'ep:lock', false, 1, 2),
('biz:device:menu', 'biz:system:menu', '设备管理', '/biz/devices', 'views/admin/devices/DeviceListPage.vue', 'ep:cpu', false, 1, 2),
('biz:workline:menu', 'biz:system:menu', '作业线管理', '/biz/worklines', 'views/admin/worklines/WorkLineListPage.vue', 'ep:connection', false, 2, 2),
('biz:workline:config', 'biz:system:menu', '作业线配置工作台', '/biz/worklines/:id/config', 'views/admin/worklines/config/WorkLineConfigPage.vue', NULL, true, 10, 2),
('logs:audit:menu', 'logs:system:menu', '审计日志', '/logs/audit', 'views/logs/audit/AuditLogListPage.vue', 'ep:document-checked', false, 1, 2),
('logs:api-access:menu', 'logs:system:menu', 'API 访问日志', '/logs/api-access', 'views/logs/api-access/APIAccessLogListPage.vue', 'ep:histogram', false, 2, 2),
('runtime:overview:menu', 'runtime:system:menu', '运行总览', '/runtime/overview', 'views/runtime/overview/RuntimeOverviewPage.vue', 'ep:data-board', false, 1, 2),
('runtime:monitor:menu', 'runtime:system:menu', '工作线监控', '/runtime/monitor', 'views/runtime/worklines/WorklineMonitorPage.vue', 'ep:share', false, 2, 2),
('runtime:traces:menu', 'runtime:system:menu', 'Trace 追溯', '/runtime/traces', 'views/runtime/traces/TraceExplorerPage.vue', 'ep:search', false, 3, 2),
('runtime:integration-debug:menu', 'runtime:system:menu', '集成调试', '/runtime/integration-debug', 'views/runtime/integration-debug/IntegrationDebugPage.vue', 'ep:connection', false, 4, 2),
('runtime:holds:menu', 'runtime:system:menu', 'Hold 处置', '/runtime/holds', 'views/runtime/holds/HoldListPage.vue', 'ep:warn-triangle-filled', false, 5, 2),
('runtime:sandbox:menu', 'runtime:system:menu', '沙箱测试', '/runtime/sandbox', 'views/runtime/sandbox/RuntimeSandboxPage.vue', 'ep:tools', false, 6, 2),
('runtime:devices:menu', 'runtime:system:menu', '设备运行时', '/runtime/devices', 'views/runtime/devices/DeviceRuntimePage.vue', 'ep:cpu', false, 7, 2),
('runtime:hold:detail', 'runtime:system:menu', 'Hold 详情', '/runtime/holds/:holdId', 'views/runtime/holds/RuntimeHoldPage.vue', NULL, true, 19, 2),
('runtime:sandbox:workbench', 'runtime:system:menu', '沙箱工作台', '/runtime/sandbox/:worklineId', 'views/runtime/sandbox/SandboxWorkbenchPage.vue', NULL, true, 21, 2);

DO $$
DECLARE
    r record;
    v_id bigint;
    v_parent_id bigint;
    v_parent_tree text;
    v_parent_level integer;
BEGIN
    FOR r IN SELECT * FROM _seed_menus ORDER BY level_order, sort_order, name LOOP
        v_parent_id := NULL;
        IF r.parent_name IS NOT NULL THEN
            SELECT id INTO v_parent_id
            FROM wes_sys.menus
            WHERE name = r.parent_name AND is_deleted = false
            LIMIT 1;
            IF v_parent_id IS NULL THEN
                RAISE EXCEPTION '菜单 % 的父菜单 % 不存在', r.name, r.parent_name;
            END IF;
        END IF;

        SELECT id INTO v_id
        FROM wes_sys.menus
        WHERE name = r.name AND is_deleted = false
        LIMIT 1;

        IF v_id IS NULL THEN
            INSERT INTO wes_sys.menus (
                created_at, updated_at, version, is_deleted,
                parent_id, tree_path, level, sort_order, has_children,
                name, title, path, component, icon, is_hidden
            ) VALUES (
                now() AT TIME ZONE 'UTC', NULL, 0, false,
                v_parent_id, '/', 1, r.sort_order, false,
                r.name, r.title, r.path, r.component, r.icon, r.is_hidden
            ) RETURNING id INTO v_id;
        ELSE
            UPDATE wes_sys.menus SET
                updated_at = now() AT TIME ZONE 'UTC',
                parent_id = v_parent_id,
                sort_order = r.sort_order,
                name = r.name,
                title = r.title,
                path = r.path,
                component = r.component,
                icon = r.icon,
                is_hidden = r.is_hidden
            WHERE id = v_id;
        END IF;

        IF v_parent_id IS NULL THEN
            UPDATE wes_sys.menus
            SET tree_path = '/' || v_id || '/', level = 1
            WHERE id = v_id;
        ELSE
            SELECT tree_path, level INTO v_parent_tree, v_parent_level
            FROM wes_sys.menus
            WHERE id = v_parent_id;
            UPDATE wes_sys.menus
            SET tree_path = v_parent_tree || v_id || '/', level = v_parent_level + 1
            WHERE id = v_id;
        END IF;
    END LOOP;

    UPDATE wes_sys.menus m
    SET has_children = EXISTS (
        SELECT 1 FROM wes_sys.menus c WHERE c.parent_id = m.id AND c.is_deleted = false
    )
    WHERE m.is_deleted = false;
END $$;

-- 内置角色菜单规则，与应用同步逻辑保持一致。
INSERT INTO wes_sys.role_menus(role_id, menu_id)
SELECT r.id, m.id
FROM wes_sys.roles r
JOIN wes_sys.menus m ON m.is_deleted = false
WHERE r.is_deleted = false
  AND NOT m.is_hidden
  AND (
    r.name = '系统管理员'
    OR (r.name = '管理员' AND (m.name LIKE 'admin:%' OR m.name = 'system:dashboard:menu'))
    OR (r.name = '运营人员' AND (m.name LIKE 'biz:%' OR m.name = 'system:dashboard:menu'))
    OR (r.name = '财务人员' AND m.name IN ('admin:audit:menu', 'system:dashboard:menu'))
    OR (r.name = '普通用户' AND m.name = 'system:dashboard:menu')
  )
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 4. 初始化验收摘要
-- ============================================================================

DO $$
DECLARE
    v_permissions integer;
    v_menus integer;
    v_roles integer;
BEGIN
    SELECT count(*) INTO v_roles FROM wes_sys.roles WHERE is_deleted = false;
    SELECT count(*) INTO v_permissions FROM wes_sys.permissions WHERE is_deleted = false;
    SELECT count(*) INTO v_menus FROM wes_sys.menus WHERE is_deleted = false;
    IF v_roles < 5 THEN RAISE EXCEPTION '角色初始化数量不足: %', v_roles; END IF;
    IF v_permissions < 148 THEN RAISE EXCEPTION '权限初始化数量不足: %', v_permissions; END IF;
    IF v_menus < 25 THEN RAISE EXCEPTION '菜单初始化数量不足: %', v_menus; END IF;

    RAISE NOTICE 'WES production base data initialized: roles=%, permissions=%, menus=%',
        v_roles, v_permissions, v_menus;
END $$;

COMMIT;
