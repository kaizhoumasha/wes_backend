"""Seed initial data

Revision ID: 228d14bf0037
Revises: 09e93363376d
Create Date: 2026-01-29 09:53:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "228d14bf0037"
down_revision: Union[str, Sequence[str], None] = "09e93363376d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - Insert seed data."""

    # ================================================================
    # 1. 插入基础权限数据
    # ================================================================

    # 根菜单（parent_id = NULL）
    op.execute(
        """
        INSERT INTO permissions (id, parent_id, tree_path, level, name, description, type, category, resource, action, path, title, icon, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at)
        VALUES
        (1, NULL, '/1/', 1, 'admin:system:menu', '系统管理', 'menu', 'admin', 'system', 'menu', '/admin', '系统管理', 'Settings', 0, true, false, false, false, false, NOW()),
        (100, NULL, '/100/', 1, 'dashboard:menu', '仪表盘', 'menu', 'dashboard', 'dashboard', 'menu', '/dashboard', '首页', 'Dashboard', 0, true, false, false, false, false, NOW()),
        (110, NULL, '/110/', 1, 'report:menu', '数据报表', 'menu', 'report', 'report', 'menu', '/reports', '数据报表', 'BarChart', 50, true, false, false, false, false, NOW()),
        (120, NULL, '/120/', 1, 'content:menu', '内容管理', 'menu', 'content', 'content', 'menu', '/content', '内容管理', 'FileText', 40, true, false, false, false, false, NOW());
        """
    )

    # 系统管理子菜单
    op.execute(
        """
        INSERT INTO permissions (id, parent_id, tree_path, level, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at)
        VALUES
        (10, 1, '/1/10/', 2, 'admin:user:menu', '用户管理', 'menu', 'admin', 'user', 'menu', NULL, '/admin/users', 10, true, false, false, false, false, NOW()),
        (20, 1, '/1/20/', 2, 'admin:role:menu', '角色管理', 'menu', 'admin', 'role', 'menu', NULL, '/admin/roles', 20, true, false, false, false, false, NOW()),
        (30, 1, '/1/30/', 2, 'admin:permission:menu', '权限管理', 'menu', 'admin', 'permission', 'menu', NULL, '/admin/permissions', 30, true, false, false, false, false, NOW());
        """
    )

    # 用户管理 API
    op.execute(
        """
        INSERT INTO permissions (id, parent_id, tree_path, level, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at)
        VALUES
        (11, 10, '/1/10/11/', 3, 'admin:user:create', '创建用户', 'api', 'admin', 'user', 'create', 'POST', '/api/v1/admin/users', 1, true, false, false, false, false, NOW()),
        (12, 10, '/1/10/12/', 3, 'admin:user:update', '更新用户', 'api', 'admin', 'user', 'update', 'PUT', '/api/v1/admin/users/{id}', 2, true, false, false, false, false, NOW()),
        (13, 10, '/1/10/13/', 3, 'admin:user:delete', '删除用户', 'api', 'admin', 'user', 'delete', 'DELETE', '/api/v1/admin/users/{id}', 3, true, false, false, false, false, NOW()),
        (14, 10, '/1/10/14/', 3, 'admin:user:detail', '查看用户详情', 'api', 'admin', 'user', 'detail', 'GET', '/api/v1/admin/users/{id}', 4, true, false, false, false, false, NOW()),
        (15, 10, '/1/10/15/', 3, 'admin:user:list', '查看用户列表', 'api', 'admin', 'user', 'list', 'POST', '/api/v1/admin/users/query', 5, true, false, false, false, false, NOW());
        """
    )

    # 角色管理 API
    op.execute(
        """
        INSERT INTO permissions (id, parent_id, tree_path, level, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at)
        VALUES
        (21, 20, '/1/20/21/', 3, 'admin:role:create', '创建角色', 'api', 'admin', 'role', 'create', 'POST', '/api/v1/admin/roles', 1, true, false, false, false, false, NOW()),
        (22, 20, '/1/20/22/', 3, 'admin:role:update', '更新角色', 'api', 'admin', 'role', 'update', 'PUT', '/api/v1/admin/roles/{id}', 2, true, false, false, false, false, NOW()),
        (23, 20, '/1/20/23/', 3, 'admin:role:delete', '删除角色', 'api', 'admin', 'role', 'delete', 'DELETE', '/api/v1/admin/roles/{id}', 3, true, false, false, false, false, NOW()),
        (24, 20, '/1/20/24/', 3, 'admin:role:detail', '查看角色详情', 'api', 'admin', 'role', 'detail', 'GET', '/api/v1/admin/roles/{id}', 4, true, false, false, false, false, NOW()),
        (25, 20, '/1/20/25/', 3, 'admin:role:list', '查看角色列表', 'api', 'admin', 'role', 'list', 'POST', '/api/v1/admin/roles/query', 5, true, false, false, false, false, NOW());
        """
    )

    # 权限管理 API
    op.execute(
        """
        INSERT INTO permissions (id, parent_id, tree_path, level, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at)
        VALUES
        (31, 30, '/1/30/31/', 3, 'admin:permission:create', '创建权限', 'api', 'admin', 'permission', 'create', 'POST', '/api/v1/admin/permissions', 1, true, false, false, false, false, NOW()),
        (32, 30, '/1/30/32/', 3, 'admin:permission:update', '更新权限', 'api', 'admin', 'permission', 'update', 'PUT', '/api/v1/admin/permissions/{id}', 2, true, false, false, false, false, NOW()),
        (33, 30, '/1/30/33/', 3, 'admin:permission:delete', '删除权限', 'api', 'admin', 'permission', 'delete', 'DELETE', '/api/v1/admin/permissions/{id}', 3, true, false, false, false, false, NOW()),
        (34, 30, '/1/30/34/', 3, 'admin:permission:detail', '查看权限详情', 'api', 'admin', 'permission', 'detail', 'GET', '/api/v1/admin/permissions/{id}', 4, true, false, false, false, false, NOW()),
        (35, 30, '/1/30/35/', 3, 'admin:permission:list', '查看权限列表', 'api', 'admin', 'permission', 'list', 'POST', '/api/v1/admin/permissions/query', 5, true, false, false, false, false, NOW());
        """
    )

    # 仪表盘 API
    op.execute(
        """
        INSERT INTO permissions (id, parent_id, tree_path, level, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at)
        VALUES
        (101, 100, '/100/101/', 2, 'dashboard:view', '查看仪表盘', 'api', 'dashboard', 'dashboard', 'view', 'GET', '/api/v1/dashboard', 1, true, false, false, false, false, NOW());
        """
    )

    # 数据报表 API
    op.execute(
        """
        INSERT INTO permissions (id, parent_id, tree_path, level, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at)
        VALUES
        (111, 110, '/110/111/', 2, 'report:sales:view', '查看销售报表', 'api', 'report', 'sales', 'view', 'GET', '/api/v1/reports/sales', 1, true, false, false, false, false, NOW()),
        (112, 110, '/110/112/', 2, 'report:sales:export', '导出销售报表', 'api', 'report', 'sales', 'export', 'POST', '/api/v1/reports/sales/export', 2, true, false, false, false, false, NOW()),
        (113, 110, '/110/113/', 2, 'report:inventory:view', '查看库存报表', 'api', 'report', 'inventory', 'view', 'GET', '/api/v1/reports/inventory', 3, true, false, false, false, false, NOW()),
        (114, 110, '/110/114/', 2, 'report:inventory:export', '导出库存报表', 'api', 'report', 'inventory', 'export', 'POST', '/api/v1/reports/inventory/export', 4, true, false, false, false, false, NOW()),
        (115, 110, '/110/115/', 2, 'report:export:all', '导出所有报表', 'api', 'report', 'report', 'export:all', 'POST', '/api/v1/reports/export-all', 10, true, false, false, false, false, NOW()),
        (116, 110, '/110/116/', 2, 'report:analytics:view', '查看数据分析', 'api', 'report', 'analytics', 'view', 'GET', '/api/v1/reports/analytics', 5, true, false, false, false, false, NOW());
        """
    )

    # 内容管理 API
    op.execute(
        """
        INSERT INTO permissions (id, parent_id, tree_path, level, name, description, type, category, resource, action, method, path, sort_order, is_active, is_hidden, is_cached, is_affix, is_external, created_at)
        VALUES
        (121, 120, '/120/121/', 2, 'content:article:create', '创建文章', 'api', 'content', 'article', 'create', 'POST', '/api/v1/content/articles', 1, true, false, false, false, false, NOW()),
        (122, 120, '/120/122/', 2, 'content:article:update', '更新文章', 'api', 'content', 'article', 'update', 'PUT', '/api/v1/content/articles/{id}', 2, true, false, false, false, false, NOW()),
        (123, 120, '/120/123/', 2, 'content:article:delete', '删除文章', 'api', 'content', 'article', 'delete', 'DELETE', '/api/v1/content/articles/{id}', 3, true, false, false, false, false, NOW()),
        (124, 120, '/120/124/', 2, 'content:article:publish', '发布文章', 'api', 'content', 'article', 'publish', 'POST', '/api/v1/content/articles/{id}/publish', 4, true, false, false, false, false, NOW()),
        (125, 120, '/120/125/', 2, 'content:article:list', '文章列表', 'api', 'content', 'article', 'list', 'GET', '/api/v1/content/articles', 5, true, false, false, false, false, NOW()),
        (126, 120, '/120/126/', 2, 'content:category:manage', '分类管理', 'api', 'content', 'category', 'manage', 'POST', '/api/v1/content/categories', 6, true, false, false, false, false, NOW());
        """
    )

    # 更新菜单的额外字段
    op.execute(
        "UPDATE permissions SET title = '用户管理', icon = 'User', component = '/views/admin/users/index.vue' WHERE id = 10;"
    )
    op.execute(
        "UPDATE permissions SET title = '角色管理', icon = 'Shield', component = '/views/admin/roles/index.vue' WHERE id = 20;"
    )
    op.execute(
        "UPDATE permissions SET title = '权限管理', icon = 'Key', component = '/views/admin/permissions/index.vue' WHERE id = 30;"
    )

    # ================================================================
    # 2. 创建系统角色
    # ================================================================
    op.execute(
        """
        INSERT INTO roles (id, name, description, is_active, created_at)
        VALUES
        (1, '超级管理员', '拥有系统所有权限，包括用户、角色、权限管理', true, NOW()),
        (2, '管理员', '拥有基本的管理权限，可以管理用户和查看系统信息', true, NOW()),
        (3, '运营人员', '负责内容管理和发布，可以管理文章、新闻等', true, NOW()),
        (4, '财务人员', '可以查看和导出各类业务报表', true, NOW()),
        (5, '普通用户', '拥有基本的查看权限，可以浏览系统内容', true, NOW());
        """
    )

    # ================================================================
    # 3. 创建用户
    # ================================================================
    # 密码统一使用: admin123 (Argon2 哈希)
    op.execute(
        """
        INSERT INTO users (id, username, email, full_name, hashed_password, is_active, is_superuser, is_multi_login, created_at)
        VALUES
        (1, 'admin', 'admin@wes.local', '系统管理员', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, true, true, NOW()),
        (2, 'manager', 'manager@wes.local', '系统管理员', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, false, NOW()),
        (3, 'operator', 'operator@wes.local', '运营专员', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, false, NOW()),
        (4, 'finance', 'finance@wes.local', '财务专员', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, false, NOW()),
        (5, 'user1', 'user1@wes.local', '普通用户一', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, false, NOW()),
        (6, 'user2', 'user2@wes.local', '普通用户二', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, false, NOW()),
        (7, 'super_manager', 'super_manager@wes.local', '超级经理', '$argon2id$v=19$m=65536,t=3,p=4$FMBTWB4kA+tbCbHmceUlvQ$VVj2huZ9OV1QVuQtcdSW2jmkBEu9BFY2+N/ORN/fyYo', true, false, false, NOW());
        """
    )

    # ================================================================
    # 4. 建立角色与权限的关联
    # ================================================================

    # 超级管理员 (ID: 1) 拥有所有权限
    op.execute("INSERT INTO role_permissions (role_id, permission_id) SELECT 1, id FROM permissions;")

    # 管理员 (ID: 2) 拥有部分权限
    op.execute(
        """
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
        """
    )

    # 运营人员 (ID: 3) 拥有内容管理权限
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 3, id FROM permissions
        WHERE id IN (
            100,            -- 仪表盘菜单
            101,            -- dashboard:view
            120,            -- 内容管理菜单
            121, 122, 123, 124, 125, 126  -- 内容管理 API
        );
        """
    )

    # 财务人员 (ID: 4) 拥有报表查看权限
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 4, id FROM permissions
        WHERE id IN (
            100,            -- 仪表盘菜单
            101,            -- dashboard:view
            110,            -- 数据报表菜单
            111, 112, 113, 114, 115, 116  -- 数据报表 API
        );
        """
    )

    # 普通用户 (ID: 5) 只有基本查看权限
    op.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES (5, 100), (5, 101);")

    # ================================================================
    # 5. 建立用户与角色的关联
    # ================================================================
    op.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        VALUES
        (1, 1),  -- admin -> 超级管理员
        (2, 2),  -- manager -> 管理员
        (3, 3),  -- operator -> 运营人员
        (4, 4),  -- finance -> 财务人员
        (5, 5),  -- user1 -> 普通用户
        (6, 5),  -- user2 -> 普通用户
        (7, 2),  -- super_manager -> 管理员
        (7, 3);  -- super_manager -> 运营人员（多角色用户）
        """
    )

    # 重置序列
    op.execute("SELECT setval('permissions_id_seq', (SELECT MAX(id) FROM permissions));")
    op.execute("SELECT setval('roles_id_seq', (SELECT MAX(id) FROM roles));")
    op.execute("SELECT setval('users_id_seq', (SELECT MAX(id) FROM users));")


def downgrade() -> None:
    """Downgrade schema - Remove seed data."""

    # 删除顺序：关联表 -> 用户 -> 角色 -> 权限

    # 1. 删除用户角色关联
    op.execute("DELETE FROM user_roles WHERE user_id IN (1, 2, 3, 4, 5, 6, 7);")

    # 2. 删除角色权限关联
    op.execute("DELETE FROM role_permissions WHERE role_id IN (1, 2, 3, 4, 5);")

    # 3. 删除用户
    op.execute("DELETE FROM users WHERE id IN (1, 2, 3, 4, 5, 6, 7);")

    # 4. 删除角色
    op.execute("DELETE FROM roles WHERE id IN (1, 2, 3, 4, 5);")

    # 5. 删除权限（按ID范围删除）
    op.execute(
        """
        DELETE FROM permissions
        WHERE id IN (
            -- 系统管理模块 (1-35)
            1, 10, 11, 12, 13, 14, 15,
            20, 21, 22, 23, 24, 25,
            30, 31, 32, 33, 34, 35,
            -- 业务功能模块 (100-126)
            100, 101,
            110, 111, 112, 113, 114, 115, 116,
            120, 121, 122, 123, 124, 125, 126
        );
        """
    )
