"""
使用 SQLModel 对象初始化系统基础数据

优势：
- 自动维护 tree_path（通过 TreeRepository Hook）
- 类型安全（Pydantic 验证）
- 自动处理自增 ID（ORM flush）
- 易于维护和扩展
"""

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.admin.models.perm import Permission
from src.app.admin.models.role import Role
from src.app.admin.models.user import User
from src.core.security import get_password_hash
from src.database.hooks import HookType


def _disable_audit_hooks(repo) -> None:
    """
    禁用 Repository 的审计日志 Hook

    在种子数据初始化期间禁用审计日志，避免 ENUM 类型问题。
    只移除审计日志 hooks（priority=100），保留其他 hooks（如 tree_path 维护）。

    Args:
        repo: Repository 实例
    """
    for hook_type in [HookType.AFTER_CREATE, HookType.AFTER_UPDATE, HookType.AFTER_DELETE]:
        # 只移除审计日志 hooks（priority=100），保留 tree_path hooks（priority=10）
        repo.hook_manager.hooks[hook_type] = [
            hook for hook in repo.hook_manager.hooks[hook_type] if hook.priority != 100
        ]


if TYPE_CHECKING:
    from src.core.conf import Settings
else:
    Settings = "Settings"


async def seed_permissions(db: AsyncSession) -> None:
    """
    初始化 API 权限数据

    注意：
    - TreeRepository 会自动计算 tree_path
    - tree_path 格式：/父ID/当前ID/
    - 需要使用 flush() 获取自增 ID
    - 权限按模块分组（使用 user_api 类型）
    """
    from src.app.admin.repositories.perm_repository import PermissionRepository

    repo = PermissionRepository()
    _disable_audit_hooks(repo)  # 禁用审计 Hook

    # ========== 1. 系统管理根分组 ==========
    system_group = await repo.create(
        db,
        {
            "name": "admin:system:group",
            "description": "系统管理权限分组",
            "type": "user_api",
            "category": "admin",
            "resource": "system",
            "action": "group",
            "method": "GET",
            "path": "/admin",
            "sort_order": 1,
        },
    )
    # ✅ Hook 自动计算：tree_path = "/{id}/", level = 1

    # ========== 2. 用户管理模块 ==========
    user_group = await repo.create(
        db,
        {
            "name": "admin:user:group",
            "description": "用户管理权限分组",
            "type": "user_api",
            "category": "admin",
            "parent_id": system_group.id,
            "resource": "user",
            "action": "group",
            "method": "GET",
            "path": "/admin/users",
            "sort_order": 1,
        },
    )
    # ✅ Hook 自动计算：tree_path = "/{system_id}/{user_group_id}/", level = 2

    # 用户 API 权限
    await repo.create(
        db,
        {
            "name": "admin:user:create",
            "description": "创建用户",
            "type": "user_api",
            "category": "admin",
            "parent_id": user_group.id,
            "path": "/api/v1/admin/users",
            "method": "POST",
            "resource": "user",
            "action": "create",
            "sort_order": 1,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:user:update",
            "description": "更新用户",
            "type": "user_api",
            "category": "admin",
            "parent_id": user_group.id,
            "path": "/api/v1/admin/users/{id}",
            "method": "PUT",
            "resource": "user",
            "action": "update",
            "sort_order": 2,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:user:delete",
            "description": "删除用户",
            "type": "user_api",
            "category": "admin",
            "parent_id": user_group.id,
            "path": "/api/v1/admin/users/{id}",
            "method": "DELETE",
            "resource": "user",
            "action": "delete",
            "sort_order": 3,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:user:detail",
            "description": "查看用户详情",
            "type": "user_api",
            "category": "admin",
            "parent_id": user_group.id,
            "path": "/api/v1/admin/users/{id}",
            "method": "GET",
            "resource": "user",
            "action": "read",
            "sort_order": 4,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:user:list",
            "description": "查询用户列表",
            "type": "user_api",
            "category": "admin",
            "parent_id": user_group.id,
            "path": "/api/v1/admin/users/query",
            "method": "POST",
            "resource": "user",
            "action": "read",
            "sort_order": 5,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:user:export",
            "description": "导出用户数据",
            "type": "user_api",
            "category": "admin",
            "parent_id": user_group.id,
            "path": "/api/v1/admin/users/export",
            "method": "GET",
            "resource": "user",
            "action": "export",
            "sort_order": 6,
        },
    )

    # ========== 3. 角色管理模块 ==========
    role_group = await repo.create(
        db,
        {
            "name": "admin:role:group",
            "description": "角色管理权限分组",
            "type": "user_api",
            "category": "admin",
            "parent_id": system_group.id,
            "resource": "role",
            "action": "group",
            "method": "GET",
            "path": "/admin/roles",
            "sort_order": 2,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:role:create",
            "description": "创建角色",
            "type": "user_api",
            "category": "admin",
            "parent_id": role_group.id,
            "path": "/api/v1/admin/roles",
            "method": "POST",
            "resource": "role",
            "action": "create",
            "sort_order": 1,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:role:update",
            "description": "更新角色",
            "type": "user_api",
            "category": "admin",
            "parent_id": role_group.id,
            "path": "/api/v1/admin/roles/{id}",
            "method": "PUT",
            "resource": "role",
            "action": "update",
            "sort_order": 2,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:role:delete",
            "description": "删除角色",
            "type": "user_api",
            "category": "admin",
            "parent_id": role_group.id,
            "path": "/api/v1/admin/roles/{id}",
            "method": "DELETE",
            "resource": "role",
            "action": "delete",
            "sort_order": 3,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:role:detail",
            "description": "查看角色详情",
            "type": "user_api",
            "category": "admin",
            "parent_id": role_group.id,
            "path": "/api/v1/admin/roles/{id}",
            "method": "GET",
            "resource": "role",
            "action": "read",
            "sort_order": 4,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:role:list",
            "description": "查询角色列表",
            "type": "user_api",
            "category": "admin",
            "parent_id": role_group.id,
            "path": "/api/v1/admin/roles/query",
            "method": "POST",
            "resource": "role",
            "action": "read",
            "sort_order": 5,
        },
    )

    # ========== 4. 权限管理模块 ==========
    perm_group = await repo.create(
        db,
        {
            "name": "admin:permission:group",
            "description": "权限管理权限分组",
            "type": "user_api",
            "category": "admin",
            "parent_id": system_group.id,
            "resource": "permission",
            "action": "group",
            "method": "GET",
            "path": "/admin/permissions",
            "sort_order": 3,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:permission:create",
            "description": "创建权限",
            "type": "user_api",
            "category": "admin",
            "parent_id": perm_group.id,
            "path": "/api/v1/admin/permissions",
            "method": "POST",
            "resource": "permission",
            "action": "create",
            "sort_order": 1,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:permission:update",
            "description": "更新权限",
            "type": "user_api",
            "category": "admin",
            "parent_id": perm_group.id,
            "path": "/api/v1/admin/permissions/{id}",
            "method": "PUT",
            "resource": "permission",
            "action": "update",
            "sort_order": 2,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:permission:delete",
            "description": "删除权限",
            "type": "user_api",
            "category": "admin",
            "parent_id": perm_group.id,
            "path": "/api/v1/admin/permissions/{id}",
            "method": "DELETE",
            "resource": "permission",
            "action": "delete",
            "sort_order": 3,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:permission:detail",
            "description": "查看权限详情",
            "type": "user_api",
            "category": "admin",
            "parent_id": perm_group.id,
            "path": "/api/v1/admin/permissions/{id}",
            "method": "GET",
            "resource": "permission",
            "action": "read",
            "sort_order": 4,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:permission:list",
            "description": "查询权限列表",
            "type": "user_api",
            "category": "admin",
            "parent_id": perm_group.id,
            "path": "/api/v1/admin/permissions/query",
            "method": "POST",
            "resource": "permission",
            "action": "read",
            "sort_order": 5,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:permission:tree",
            "description": "获取权限树",
            "type": "user_api",
            "category": "admin",
            "parent_id": perm_group.id,
            "path": "/api/v1/admin/permissions/tree",
            "method": "GET",
            "resource": "permission",
            "action": "read",
            "sort_order": 6,
        },
    )

    # ========== 5. 审计日志模块 ==========
    audit_group = await repo.create(
        db,
        {
            "name": "admin:audit:group",
            "description": "审计日志权限分组",
            "type": "user_api",
            "category": "admin",
            "parent_id": system_group.id,
            "resource": "audit",
            "action": "group",
            "method": "GET",
            "path": "/admin/audit-logs",
            "sort_order": 4,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:audit:list",
            "description": "查询审计日志",
            "type": "user_api",
            "category": "admin",
            "parent_id": audit_group.id,
            "path": "/api/v1/admin/audit-logs/query",
            "method": "POST",
            "resource": "audit",
            "action": "read",
            "sort_order": 1,
        },
    )

    await repo.create(
        db,
        {
            "name": "admin:audit:export",
            "description": "导出审计日志",
            "type": "user_api",
            "category": "admin",
            "parent_id": audit_group.id,
            "path": "/api/v1/admin/audit-logs/export",
            "method": "GET",
            "resource": "audit",
            "action": "export",
            "sort_order": 2,
        },
    )


async def seed_roles(db: AsyncSession) -> None:
    """初始化角色数据"""
    from src.app.admin.repositories.role_repository import RoleRepository

    repo = RoleRepository()
    _disable_audit_hooks(repo)  # 禁用审计 Hook

    await repo.create(
        db,
        {
            "name": "系统管理员",
            "description": "系统最高权限，拥有所有操作权限",
        },
    )

    await repo.create(
        db,
        {
            "name": "管理员",
            "description": "系统管理员，拥有大部分管理权限",
        },
    )

    await repo.create(
        db,
        {
            "name": "运营人员",
            "description": "日常运营操作人员",
        },
    )

    await repo.create(
        db,
        {
            "name": "财务人员",
            "description": "财务相关操作人员",
        },
    )

    await repo.create(
        db,
        {
            "name": "普通用户",
            "description": "普通用户，基础查看权限",
        },
    )


async def seed_users(db: AsyncSession) -> None:
    """初始化用户数据"""
    from src.app.admin.repositories.user_repository import UserRepository

    repo = UserRepository()
    _disable_audit_hooks(repo)  # 禁用审计 Hook

    await repo.create(
        db,
        {
            "username": "admin",
            "email": "admin@localhost.localdomain",
            "full_name": "系统管理员",
            "hashed_password": get_password_hash("admin123"),
            "is_superuser": True,
            "is_multi_login": True,
        },
    )

    await repo.create(
        db,
        {
            "username": "manager",
            "email": "manager@localhost.localdomain",
            "full_name": "管理员",
            "hashed_password": get_password_hash("admin123"),
            "is_superuser": False,
            "is_multi_login": False,
        },
    )

    await repo.create(
        db,
        {
            "username": "operator",
            "email": "operator@localhost.localdomain",
            "full_name": "运营人员",
            "hashed_password": get_password_hash("admin123"),
            "is_superuser": False,
            "is_multi_login": False,
        },
    )

    await repo.create(
        db,
        {
            "username": "finance",
            "email": "finance@localhost.localdomain",
            "full_name": "财务人员",
            "hashed_password": get_password_hash("admin123"),
            "is_superuser": False,
            "is_multi_login": False,
        },
    )

    await repo.create(
        db,
        {
            "username": "user1",
            "email": "user1@localhost.localdomain",
            "full_name": "普通用户1",
            "hashed_password": get_password_hash("admin123"),
            "is_superuser": False,
            "is_multi_login": False,
        },
    )

    await repo.create(
        db,
        {
            "username": "user2",
            "email": "user2@localhost.localdomain",
            "full_name": "普通用户2",
            "hashed_password": get_password_hash("admin123"),
            "is_superuser": False,
            "is_multi_login": False,
        },
    )


async def seed_role_permissions(db: AsyncSession) -> None:
    """初始化角色权限关联 - 直接插入关系表"""
    # 获取所有角色和权限
    roles_result = await db.execute(select(Role))
    roles = roles_result.scalars().all()

    perms_result = await db.execute(select(Permission))
    permissions = perms_result.scalars().all()

    # 准备批量插入数据
    from src.app.admin.models.relationships import role_permission

    role_permission_links = []

    # 系统管理员：所有权限
    admin_role = next(r for r in roles if r.name == "系统管理员")
    for perm in permissions:
        role_permission_links.append({"role_id": admin_role.id, "permission_id": perm.id})

    # 管理员：系统管理权限
    manager_role = next(r for r in roles if r.name == "管理员")
    for perm in permissions:
        if perm.name.startswith("admin:"):
            role_permission_links.append({"role_id": manager_role.id, "permission_id": perm.id})

    # 运营人员：只读权限
    operator_role = next(r for r in roles if r.name == "运营人员")
    for perm in permissions:
        if any(perm.name.endswith(suffix) for suffix in [":list", ":detail", ":tree"]):
            role_permission_links.append({"role_id": operator_role.id, "permission_id": perm.id})

    # 财务人员：审计日志权限
    finance_role = next(r for r in roles if r.name == "财务人员")
    for perm in permissions:
        if perm.name.startswith("admin:audit:"):
            role_permission_links.append({"role_id": finance_role.id, "permission_id": perm.id})

    # 普通用户：基础只读权限
    user_role = next(r for r in roles if r.name == "普通用户")
    for perm in permissions:
        if any(perm.name.endswith(suffix) for suffix in [":list", ":detail"]):
            role_permission_links.append({"role_id": user_role.id, "permission_id": perm.id})

    # 批量插入
    if role_permission_links:
        await db.execute(role_permission.insert(), role_permission_links)

    await db.commit()


async def seed_user_roles(db: AsyncSession) -> None:
    """初始化用户角色关联 - 直接插入关系表"""
    # 获取所有用户和角色
    users_result = await db.execute(select(User))
    users = users_result.scalars().all()

    roles_result = await db.execute(select(Role))
    roles = roles_result.scalars().all()

    # 创建角色映射
    role_map = {role.name: role for role in roles}

    # 准备批量插入数据
    from src.app.admin.models.relationships import user_role

    user_role_links = []

    # admin -> 系统管理员
    admin_user = next(u for u in users if u.username == "admin")
    user_role_links.append({"user_id": admin_user.id, "role_id": role_map["系统管理员"].id})

    # manager -> 管理员
    manager_user = next(u for u in users if u.username == "manager")
    user_role_links.append({"user_id": manager_user.id, "role_id": role_map["管理员"].id})

    # operator -> 运营人员
    operator_user = next(u for u in users if u.username == "operator")
    user_role_links.append({"user_id": operator_user.id, "role_id": role_map["运营人员"].id})

    # finance -> 财务人员
    finance_user = next(u for u in users if u.username == "finance")
    user_role_links.append({"user_id": finance_user.id, "role_id": role_map["财务人员"].id})

    # user1, user2 -> 普通用户
    user1 = next(u for u in users if u.username == "user1")
    user_role_links.append({"user_id": user1.id, "role_id": role_map["普通用户"].id})

    user2 = next(u for u in users if u.username == "user2")
    user_role_links.append({"user_id": user2.id, "role_id": role_map["普通用户"].id})

    # 批量插入
    if user_role_links:
        await db.execute(user_role.insert(), user_role_links)

    await db.commit()


async def seed_all(db: AsyncSession) -> None:
    """初始化所有数据"""
    print("🌱 开始初始化系统数据...")

    print("  1️⃣ 初始化 API 权限数据...")
    await seed_permissions(db)
    perm_count_result = await db.execute(select(Permission))
    print(f"     ✅ 权限数量: {perm_count_result.scalar()}")

    print("  2️⃣ 初始化角色数据...")
    await seed_roles(db)
    role_count_result = await db.execute(select(Role))
    print(f"     ✅ 角色数量: {role_count_result.scalar()}")

    print("  3️⃣ 初始化用户数据...")
    await seed_users(db)
    user_count_result = await db.execute(select(User))
    print(f"     ✅ 用户数量: {user_count_result.scalar()}")

    print("  4️⃣ 初始化角色权限关联...")
    await seed_role_permissions(db)

    print("  5️⃣ 初始化用户角色关联...")
    await seed_user_roles(db)

    print("🎉 系统数据初始化完成！")
    print("\n📝 默认登录账号:")
    print("  - admin / admin123 (系统管理员 - 多端登录)")
    print("  - manager / admin123 (管理员)")
    print("  - operator / admin123 (运营人员)")
    print("  - finance / admin123 (财务人员)")
    print("  - user1 / admin123 (普通用户)")
    print("  - user2 / admin123 (普通用户)")
    print("\n⚠️  生产环境请立即修改默认密码！")


# ============================================
# 入口点：可以直接运行此脚本
# ============================================

async def main() -> None:
    """主函数：初始化所有数据"""
    from src.core.conf import settings

    # 创建异步引擎
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
    )

    # 创建 Session Maker
    async_session_maker = async_sessionmaker(
        engine,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    try:
        async with async_session_maker() as db:
            await seed_all(db)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    """直接运行此脚本时执行初始化"""
    asyncio.run(main())
