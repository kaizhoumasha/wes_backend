-- ================================================================
-- WES Backend 数据库初始化脚本
-- ================================================================
-- 功能：
--   1. 插入系统基础权限（用户、角色、权限管理）
--   2. 创建系统角色（超级管理员、管理员）
--   3. 创建超级管理员账户
--   4. 建立角色与权限的关联
--   5. 建立用户与角色的关联
--
-- 使用方法：
--   psql -h localhost -U postgres -d wes_db -f init_db.sql
-- ================================================================

-- 设置时区
SET timezone = 'UTC';

-- ================================================================
-- 1. 插入基础权限数据
-- ================================================================
--
-- ID 分配方案（按功能模块分组）：
--
-- 【1-99: 系统管理模块】
--   1    - 系统管理根菜单
--   10   - 用户管理菜单
--   11-15 - 用户管理 API (create, update, delete, detail, list)
--   20   - 角色管理菜单
--   21-25 - 角色管理 API (create, update, delete, detail, list)
--   30   - 权限管理菜单
--   31-35 - 权限管理 API (create, update, delete, detail, list)
--
-- 【100-199: 业务功能模块】
--   100   - 仪表盘菜单
--   101   - 仪表盘 API
--   110   - 数据报表菜单
--   111-116 - 数据报表 API
--   120   - 内容管理菜单
--   121-126 - 内容管理 API
--
-- ================================================================

-- ================================================================
-- 第一步：插入所有根菜单（parent_id = NULL）
-- ================================================================

-- 系统管理根菜单 (ID: 1)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, path, title, icon, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(1, NULL, 'admin:system:menu', '系统管理', 'menu', 'admin', 'system', 'menu', '/admin', '系统管理', 'Settings', 0, true, false, false, false, false, NOW());

-- 仪表盘菜单 (ID: 100)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, title, icon, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(100, NULL, 'dashboard:menu', '仪表盘', 'menu', 'dashboard', 'dashboard', 'menu', NULL, '/dashboard', '首页', 'Dashboard', 0, true, false, false, false, false, NOW());

-- 数据报表菜单 (ID: 110)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, title, icon, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(110, NULL, 'report:menu', '数据报表', 'menu', 'report', 'report', 'menu', NULL, '/reports', '数据报表', 'BarChart', 50, true, false, false, false, false, NOW());

-- 内容管理菜单 (ID: 120)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, title, icon, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(120, NULL, 'content:menu', '内容管理', 'menu', 'content', 'content', 'menu', NULL, '/content', '内容管理', 'FileText', 40, true, false, false, false, false, NOW());

-- ================================================================
-- 第二步：插入所有子菜单（parent_id 指向根菜单）
-- ================================================================

-- 用户管理菜单 (ID: 10) - 父节点: 系统管理(1)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(10, 1, 'admin:user:menu', '用户管理', 'menu', 'admin', 'user', 'menu', NULL, '/admin/users', 10, true, false, false, false, false, NOW());

-- 角色管理菜单 (ID: 20) - 父节点: 系统管理(1)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(20, 1, 'admin:role:menu', '角色管理', 'menu', 'admin', 'role', 'menu', NULL, '/admin/roles', 20, true, false, false, false, false, NOW());

-- 权限管理菜单 (ID: 30) - 父节点: 系统管理(1)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(30, 1, 'admin:permission:menu', '权限管理', 'menu', 'admin', 'permission', 'menu', NULL, '/admin/permissions', 30, true, false, false, false, false, NOW());

-- ================================================================
-- 第三步：插入所有 API 权限（parent_id 指向子菜单或根菜单）
-- ================================================================

-- ================================================================
-- 系统管理模块 API 权限 (ID: 11-35)
-- ================================================================

-- 用户管理 API (ID: 11-15) - 父节点: 用户管理菜单(10)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(11, 10, 'admin:user:create', '创建用户', 'api', 'admin', 'user', 'create', 'POST', '/api/v1/admin/users', 1, true, false, false, false, false, NOW()),
(12, 10, 'admin:user:update', '更新用户', 'api', 'admin', 'user', 'update', 'PUT', '/api/v1/admin/users/{id}', 2, true, false, false, false, false, NOW()),
(13, 10, 'admin:user:delete', '删除用户', 'api', 'admin', 'user', 'delete', 'DELETE', '/api/v1/admin/users/{id}', 3, true, false, false, false, false, NOW()),
(14, 10, 'admin:user:detail', '查看用户详情', 'api', 'admin', 'user', 'detail', 'GET', '/api/v1/admin/users/{id}', 4, true, false, false, false, false, NOW()),
(15, 10, 'admin:user:list', '查看用户列表', 'api', 'admin', 'user', 'list', 'POST', '/api/v1/admin/users/query', 5, true, false, false, false, false, NOW());

-- 角色管理 API (ID: 21-25) - 父节点: 角色管理菜单(20)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(21, 20, 'admin:role:create', '创建角色', 'api', 'admin', 'role', 'create', 'POST', '/api/v1/admin/roles', 1, true, false, false, false, false, NOW()),
(22, 20, 'admin:role:update', '更新角色', 'api', 'admin', 'role', 'update', 'PUT', '/api/v1/admin/roles/{id}', 2, true, false, false, false, false, NOW()),
(23, 20, 'admin:role:delete', '删除角色', 'api', 'admin', 'role', 'delete', 'DELETE', '/api/v1/admin/roles/{id}', 3, true, false, false, false, false, NOW()),
(24, 20, 'admin:role:detail', '查看角色详情', 'api', 'admin', 'role', 'detail', 'GET', '/api/v1/admin/roles/{id}', 4, true, false, false, false, false, NOW()),
(25, 20, 'admin:role:list', '查看角色列表', 'api', 'admin', 'role', 'list', 'POST', '/api/v1/admin/roles/query', 5, true, false, false, false, false, NOW());

-- 权限管理 API (ID: 31-35) - 父节点: 权限管理菜单(30)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(31, 30, 'admin:permission:create', '创建权限', 'api', 'admin', 'permission', 'create', 'POST', '/api/v1/admin/permissions', 1, true, false, false, false, false, NOW()),
(32, 30, 'admin:permission:update', '更新权限', 'api', 'admin', 'permission', 'update', 'PUT', '/api/v1/admin/permissions/{id}', 2, true, false, false, false, false, NOW()),
(33, 30, 'admin:permission:delete', '删除权限', 'api', 'admin', 'permission', 'delete', 'DELETE', '/api/v1/admin/permissions/{id}', 3, true, false, false, false, false, NOW()),
(34, 30, 'admin:permission:detail', '查看权限详情', 'api', 'admin', 'permission', 'detail', 'GET', '/api/v1/admin/permissions/{id}', 4, true, false, false, false, false, NOW()),
(35, 30, 'admin:permission:list', '查看权限列表', 'api', 'admin', 'permission', 'list', 'POST', '/api/v1/admin/permissions/query', 5, true, false, false, false, false, NOW());

-- ================================================================
-- 业务功能模块 API 权限 (ID: 101-126)
-- ================================================================

-- 仪表盘 API (ID: 101) - 父节点: 仪表盘菜单(100)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(101, 100, 'dashboard:view', '查看仪表盘', 'api', 'dashboard', 'dashboard', 'view', 'GET', '/api/v1/dashboard', 1, true, false, false, false, false, NOW());

-- 数据报表 API (ID: 111-116) - 父节点: 数据报表菜单(110)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(111, 110, 'report:sales:view', '查看销售报表', 'api', 'report', 'sales', 'view', 'GET', '/api/v1/reports/sales', 1, true, false, false, false, false, NOW()),
(112, 110, 'report:sales:export', '导出销售报表', 'api', 'report', 'sales', 'export', 'POST', '/api/v1/reports/sales/export', 2, true, false, false, false, false, NOW()),
(113, 110, 'report:inventory:view', '查看库存报表', 'api', 'report', 'inventory', 'view', 'GET', '/api/v1/reports/inventory', 3, true, false, false, false, false, NOW()),
(114, 110, 'report:inventory:export', '导出库存报表', 'api', 'report', 'inventory', 'export', 'POST', '/api/v1/reports/inventory/export', 4, true, false, false, false, false, NOW()),
(115, 110, 'report:export:all', '导出所有报表', 'api', 'report', 'report', 'export:all', 'POST', '/api/v1/reports/export-all', 10, true, false, false, false, false, NOW()),
(116, 110, 'report:analytics:view', '查看数据分析', 'api', 'report', 'analytics', 'view', 'GET', '/api/v1/reports/analytics', 5, true, false, false, false, false, NOW());

-- 内容管理 API (ID: 121-126) - 父节点: 内容管理菜单(120)
INSERT INTO permissions (id, parent_id, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at) VALUES
(121, 120, 'content:article:create', '创建文章', 'api', 'content', 'article', 'create', 'POST', '/api/v1/content/articles', 1, true, false, false, false, false, NOW()),
(122, 120, 'content:article:update', '更新文章', 'api', 'content', 'article', 'update', 'PUT', '/api/v1/content/articles/{id}', 2, true, false, false, false, false, NOW()),
(123, 120, 'content:article:delete', '删除文章', 'api', 'content', 'article', 'delete', 'DELETE', '/api/v1/content/articles/{id}', 3, true, false, false, false, false, NOW()),
(124, 120, 'content:article:publish', '发布文章', 'api', 'content', 'article', 'publish', 'POST', '/api/v1/content/articles/{id}/publish', 4, true, false, false, false, false, NOW()),
(125, 120, 'content:article:list', '文章列表', 'api', 'content', 'article', 'list', 'GET', '/api/v1/content/articles', 5, true, false, false, false, false, NOW()),
(126, 120, 'content:category:manage', '分类管理', 'api', 'content', 'category', 'manage', 'POST', '/api/v1/content/categories', 6, true, false, false, false, false, NOW());

-- ================================================================
-- 更新菜单的额外字段（title, icon, component）
-- ================================================================

UPDATE permissions SET title = '用户管理', icon = 'User', component = '/views/admin/users/index.vue' WHERE id = 10;
UPDATE permissions SET title = '角色管理', icon = 'Shield', component = '/views/admin/roles/index.vue' WHERE id = 20;
UPDATE permissions SET title = '权限管理', icon = 'Key', component = '/views/admin/permissions/index.vue' WHERE id = 30;

-- ================================================================
-- 2. 创建系统角色
-- ================================================================

INSERT INTO roles (id, name, description, is_active, created_at) VALUES
(1, '超级管理员', '拥有系统所有权限，包括用户、角色、权限管理', true, NOW()),
(2, '管理员', '拥有基本的管理权限，可以管理用户和查看系统信息', true, NOW()),
(3, '运营人员', '负责内容管理和发布，可以管理文章、新闻等', true, NOW()),
(4, '财务人员', '可以查看和导出各类业务报表', true, NOW()),
(5, '普通用户', '拥有基本的查看权限，可以浏览系统内容', true, NOW());

-- ================================================================
-- 3. 创建用户
-- ================================================================
-- 密码统一使用: admin123 (Argon2 哈希)
-- 哈希值: $argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo

INSERT INTO users (id, username, email, full_name, hashed_password, is_active, is_superuser, is_multi_login, created_at) VALUES
(1, 'admin', 'admin@wes.local', '系统管理员', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, true, true, NOW()),
(2, 'manager', 'manager@wes.local', '系统管理员', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, true, NOW()),
(3, 'operator', 'operator@wes.local', '运营专员', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, true, NOW()),
(4, 'finance', 'finance@wes.local', '财务专员', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, true, NOW()),
(5, 'user1', 'user1@wes.local', '普通用户一', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, true, NOW()),
(6, 'user2', 'user2@wes.local', '普通用户二', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, true, NOW());

-- ================================================================
-- 4. 建立角色与权限的关联
-- ================================================================
--
-- 新 ID 映射说明：
--   系统管理模块 (1-35)
--   业务功能模块 (100-126)
--
-- ================================================================

-- 超级管理员 (ID: 1) 拥有所有权限
INSERT INTO role_permissions (role_id, permission_id)
SELECT 1, id FROM permissions;

-- 管理员 (ID: 2) 拥有部分权限
-- 包括：用户查看/列表、角色查看/列表、权限查看/列表、系统管理菜单
INSERT INTO role_permissions (role_id, permission_id)
SELECT 2, id FROM permissions
WHERE id IN (
    1,              -- 系统管理菜单
    10,             -- 用户管理菜单
    14,             -- admin:user:detail
    15,             -- admin:user:list
    20,             -- 角色管理菜单
    24,             -- admin:role:detail
    25,             -- admin:role:list
    30              -- 权限管理菜单
);

-- 运营人员 (ID: 3) 拥有内容管理权限
INSERT INTO role_permissions (role_id, permission_id)
SELECT 3, id FROM permissions
WHERE id IN (
    100,            -- 仪表盘菜单
    101,            -- dashboard:view
    120,            -- 内容管理菜单
    121, 122, 123, 124, 125, 126  -- 内容管理 API
);

-- 财务人员 (ID: 4) 拥有报表查看权限
INSERT INTO role_permissions (role_id, permission_id)
SELECT 4, id FROM permissions
WHERE id IN (
    100,            -- 仪表盘菜单
    101,            -- dashboard:view
    110,            -- 数据报表菜单
    111, 112, 113, 114, 115, 116  -- 数据报表 API
);

-- 普通用户 (ID: 5) 只有基本查看权限
INSERT INTO role_permissions (role_id, permission_id)
SELECT 5, id FROM permissions
WHERE id IN (100, 101); -- 仪表盘菜单和查看

-- ================================================================
-- 5. 建立用户与角色的关联
-- ================================================================

-- admin (ID: 1) -> 超级管理员 (ID: 1)
INSERT INTO user_roles (user_id, role_id) VALUES (1, 1);

-- manager (ID: 2) -> 管理员 (ID: 2)
INSERT INTO user_roles (user_id, role_id) VALUES (2, 2);

-- operator (ID: 3) -> 运营人员 (ID: 3)
INSERT INTO user_roles (user_id, role_id) VALUES (3, 3);

-- finance (ID: 4) -> 财务人员 (ID: 4)
INSERT INTO user_roles (user_id, role_id) VALUES (4, 4);

-- user1 (ID: 5) -> 普通用户 (ID: 5)
INSERT INTO user_roles (user_id, role_id) VALUES (5, 5);

-- user2 (ID: 6) -> 普通用户 (ID: 5)
INSERT INTO user_roles (user_id, role_id) VALUES (6, 5);

-- ================================================================
-- 6. 创建数据审计日志（可选）
-- ================================================================

-- 创建操作日志表（如果需要审计功能）
CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50),
    resource_id BIGINT,
    details JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS ix_audit_logs_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs(created_at DESC);

COMMENT ON TABLE audit_logs IS '操作审计日志表';
COMMENT ON COLUMN audit_logs.user_id IS '操作用户 ID';
COMMENT ON COLUMN audit_logs.action IS '操作类型：create/update/delete/login/logout 等';
COMMENT ON COLUMN audit_logs.resource_type IS '资源类型：user/role/permission 等';
COMMENT ON COLUMN audit_logs.resource_id IS '资源 ID';
COMMENT ON COLUMN audit_logs.details IS '操作详情（JSON 格式）';
COMMENT ON COLUMN audit_logs.ip_address IS '客户端 IP 地址';
COMMENT ON COLUMN audit_logs.user_agent IS '客户端 User-Agent';
COMMENT ON COLUMN audit_logs.created_at IS '操作时间（UTC）';

-- ================================================================
-- 初始化完成
-- ================================================================

-- 输出统计信息
DO $$
DECLARE
    user_count INT;
    role_count INT;
    perm_count INT;
BEGIN
    SELECT COUNT(*) INTO user_count FROM users;
    SELECT COUNT(*) INTO role_count FROM roles;
    SELECT COUNT(*) INTO perm_count FROM permissions;

    RAISE NOTICE '==================================================';
    RAISE NOTICE '数据库初始化完成！';
    RAISE NOTICE '--------------------------------------------------';
    RAISE NOTICE '用户数量: %', user_count;
    RAISE NOTICE '角色数量: %', role_count;
    RAISE NOTICE '权限数量: %', perm_count;
    RAISE NOTICE '--------------------------------------------------';
    RAISE NOTICE '默认管理员账户:';
    RAISE NOTICE '  用户名: admin';
    RAISE NOTICE '  密码: admin123';
    RAISE NOTICE '  邮箱: admin@wes.local';
    RAISE NOTICE '==================================================';
END $$;
