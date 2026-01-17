"""
数据库初始化数据脚本

用于初始化系统基础数据:
- 权限 (Permissions)
- 角色 (Roles)
- 用户 (Users)
- 角色权限关联
- 用户角色关联

使用方法:
    python scripts/init_db_data.py

环境要求:
    - 数据库已启动并可连接
    - .env 配置正确
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from src.core.conf import settings
from src.core.logger import logger
from src.core.security import get_password_hash
from src.app.admin.models import (
    User,
    Role,
    Permission,
    user_role,
    role_permission,
)


# 创建数据库引擎和会话工厂
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ==================== 初始数据定义 ====================

# 基础权限定义
INITIAL_PERMISSIONS = [
    # 用户管理权限
    {"name": "user:create", "description": "创建用户"},
    {"name": "user:read", "description": "查看用户"},
    {"name": "user:update", "description": "更新用户"},
    {"name": "user:delete", "description": "删除用户"},
    {"name": "user:export", "description": "导出用户数据"},
    # 角色管理权限
    {"name": "role:create", "description": "创建角色"},
    {"name": "role:read", "description": "查看角色"},
    {"name": "role:update", "description": "更新角色"},
    {"name": "role:delete", "description": "删除角色"},
    {"name": "role:assign", "description": "分配用户角色"},
    # 权限管理权限
    {"name": "permission:create", "description": "创建权限"},
    {"name": "permission:read", "description": "查看权限"},
    {"name": "permission:update", "description": "更新权限"},
    {"name": "permission:delete", "description": "删除权限"},
    # 系统管理权限
    {"name": "system:config", "description": "系统配置管理"},
    {"name": "system:log", "description": "查看系统日志"},
    {"name": "system:monitor", "description": "系统监控"},
    # 文件管理权限
    {"name": "file:upload", "description": "上传文件"},
    {"name": "file:download", "description": "下载文件"},
    {"name": "file:delete", "description": "删除文件"},
]

# 基础角色定义及其权限
INITIAL_ROLES = [
    {
        "name": "超级管理员",
        "description": "系统最高权限管理员，拥有所有权限",
        "is_active": True,
        "permissions": "*",  # 通配符表示所有权限
    },
    {
        "name": "管理员",
        "description": "系统管理员，拥有大部分管理权限",
        "is_active": True,
        "permissions": [
            "user:read", "user:update",
            "role:read",
            "permission:read",
            "file:upload", "file:download",
        ],
    },
    {
        "name": "普通用户",
        "description": "系统普通用户，基础权限",
        "is_active": True,
        "permissions": [
            "user:read",
            "file:download",
        ],
    },
    {
        "name": "访客",
        "description": "访客用户，只读权限",
        "is_active": True,
        "permissions": [
            "user:read",
        ],
    },
]

# 基础用户定义
# 注意:这里使用副本保存密码信息用于显示,实际创建时会使用副本
INITIAL_USERS_CREDENTIALS = [
    {"username": "admin", "password": "admin123456"},
    {"username": "manager", "password": "manager123456"},
    {"username": "user", "password": "user123456"},
]

INITIAL_USERS = [
    {
        "username": "admin",
        "email": "admin@example.com",
        "full_name": "系统管理员",
        "password": "admin123456",  # 生产环境请修改密码
        "is_active": True,
        "is_superuser": True,
        "is_multi_login": True,
        "roles": ["超级管理员"],
    },
    {
        "username": "manager",
        "email": "manager@example.com",
        "full_name": "系统经理",
        "password": "manager123456",  # 生产环境请修改密码
        "is_active": True,
        "is_superuser": False,
        "is_multi_login": True,
        "roles": ["管理员"],
    },
    {
        "username": "user",
        "email": "user@example.com",
        "full_name": "测试用户",
        "password": "user123456",  # 生产环境请修改密码
        "is_active": True,
        "is_superuser": False,
        "is_multi_login": True,
        "roles": ["普通用户"],
    },
]


# ==================== 初始化函数 ====================


async def create_permissions(session: AsyncSession) -> dict[str, Permission]:
    """
    创建初始权限数据

    Returns:
        权限名字典 {permission_name: Permission}
    """
    logger.info(f"开始创建权限数据，共 {len(INITIAL_PERMISSIONS)} 条")

    permission_map = {}

    for perm_data in INITIAL_PERMISSIONS:
        # 检查权限是否已存在
        result = await session.execute(
            select(Permission).where(Permission.name == perm_data["name"])
        )
        existing = result.scalar_one_or_none()

        if existing:
            logger.info(f"权限已存在，跳过: {perm_data['name']}")
            permission_map[perm_data["name"]] = existing
        else:
            permission = Permission(**perm_data)
            session.add(permission)
            await session.flush()
            await session.refresh(permission)
            permission_map[perm_data["name"]] = permission
            logger.info(f"创建权限: {perm_data['name']}")

    await session.commit()
    logger.info(f"权限数据创建完成，共 {len(permission_map)} 条")

    return permission_map


async def create_roles(
    session: AsyncSession, permission_map: dict[str, Permission]
) -> dict[str, Role]:
    """
    创建初始角色数据及其权限关联

    Args:
        permission_map: 权限名字典

    Returns:
        角色名字典 {role_name: Role}
    """
    logger.info(f"开始创建角色数据，共 {len(INITIAL_ROLES)} 条")

    role_map = {}

    for role_data in INITIAL_ROLES:
        role_name = role_data["name"]
        permissions = role_data.pop("permissions")

        # 检查角色是否已存在
        result = await session.execute(
            select(Role).where(Role.name == role_name)
        )
        existing = result.scalar_one_or_none()

        if existing:
            logger.info(f"角色已存在，跳过: {role_name}")
            role_map[role_name] = existing
            continue

        # 创建角色（不包含 permissions 字段）
        role = Role(**role_data)
        session.add(role)
        await session.flush()
        await session.refresh(role)

        # 关联权限（通过 relationship）
        if permissions == "*":
            # 超级管理员拥有所有权限
            for permission in permission_map.values():
                role.permissions.append(permission)
            logger.info(f"角色 {role_name} 拥有全部 {len(permission_map)} 个权限")
        else:
            # 分配指定权限
            for perm_name in permissions:
                if perm_name in permission_map:
                    role.permissions.append(permission_map[perm_name])
            logger.info(f"角色 {role_name} 拥有 {len(role.permissions)} 个权限")

        role_map[role_name] = role
        logger.info(f"创建角色: {role_name}")

    await session.commit()
    logger.info(f"角色数据创建完成，共 {len(role_map)} 条")

    return role_map


async def create_users(
    session: AsyncSession, role_map: dict[str, Role]
) -> dict[str, User]:
    """
    创建初始用户数据及其角色关联

    Args:
        role_map: 角色名字典

    Returns:
        用户名字典 {username: User}
    """
    logger.info(f"开始创建用户数据，共 {len(INITIAL_USERS)} 条")

    user_map = {}

    for user_data in INITIAL_USERS:
        username = user_data["username"]
        role_names = user_data.pop("roles")
        plain_password = user_data.pop("password")

        # 检查用户是否已存在
        result = await session.execute(
            select(User).where(User.username == username)
        )
        existing = result.scalar_one_or_none()

        if existing:
            logger.info(f"用户已存在，跳过: {username}")
            user_map[username] = existing
            continue

        # 创建用户（密码哈希）
        user = User(
            **user_data,
            hashed_password=get_password_hash(plain_password)
        )
        session.add(user)
        await session.flush()
        await session.refresh(user)

        # 关联角色（通过 relationship）
        for role_name in role_names:
            if role_name in role_map:
                user.roles.append(role_map[role_name])
        logger.info(f"用户 {username} 拥有角色: {', '.join(role_names)}")

        user_map[username] = user
        logger.info(f"创建用户: {username} (密码: {plain_password})")

    await session.commit()
    logger.info(f"用户数据创建完成，共 {len(user_map)} 条")

    return user_map


async def init_all_data():
    """
    初始化所有基础数据
    """
    logger.info("=" * 60)
    logger.info("开始初始化数据库基础数据")
    logger.info("=" * 60)

    async with AsyncSessionLocal() as session:
        try:
            # 1. 创建权限
            permission_map = await create_permissions(session)

            # 2. 创建角色（及其权限关联）
            role_map = await create_roles(session, permission_map)

            # 3. 创建用户（及其角色关联）
            user_map = await create_users(session, role_map)

            logger.info("=" * 60)
            logger.info("数据库基础数据初始化完成!")
            logger.info("=" * 60)
            logger.info(f"权限: {len(permission_map)} 条")
            logger.info(f"角色: {len(role_map)} 条")
            logger.info(f"用户: {len(user_map)} 条")
            logger.info("")
            logger.info("默认登录账号:")
            for cred in INITIAL_USERS_CREDENTIALS:
                logger.info(
                    f"  - {cred['username']} / {cred['password']}"
                )
            logger.info("")
            logger.warning("⚠️  生产环境请立即修改默认密码!")

        except Exception as e:
            logger.error(f"初始化数据失败: {e}")
            await session.rollback()
            raise


def main():
    """
    主函数
    """
    try:
        # 运行异步初始化
        asyncio.run(init_all_data())
    except KeyboardInterrupt:
        logger.info("初始化被用户中断")
        sys.exit(1)
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
