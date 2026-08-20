"""为本机开发环境收敛确定、幂等且可校验的基础调试数据。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete, insert, select, tuple_

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.app.admin.models import Role, User, user_role
from src.app.admin.services.menu_sync_service import menu_sync_service
from src.app.admin.services.permission_catalog_service import permission_catalog_service
from src.core.rbac import invalidate_users_permissions
from src.core.security import get_password_hash, verify_password
from src.database.db import get_db_context, init_db
from src.database.redis_cache import get_cache
from src.register import create_app
from src.utils.permission_scanner import (
    managed_permission_names_for_app,
    sync_builtin_role_permissions,
)


@dataclass(frozen=True, slots=True)
class RoleSeed:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class UserSeed:
    username: str
    email: str
    full_name: str
    role_name: str
    is_superuser: bool = False
    is_multi_login: bool = False


ROLE_SEEDS = (
    RoleSeed("系统管理员", "系统最高权限，拥有所有操作权限"),
    RoleSeed("管理员", "系统管理员，拥有大部分管理权限"),
    RoleSeed("运营人员", "日常运营操作人员"),
    RoleSeed("财务人员", "财务相关操作人员"),
    RoleSeed("普通用户", "普通用户，基础查看权限"),
)

USER_SEEDS = (
    UserSeed(
        "admin",
        "admin@localhost.localdomain",
        "系统管理员",
        "系统管理员",
        is_superuser=True,
        is_multi_login=True,
    ),
    UserSeed("manager", "manager@localhost.localdomain", "管理员", "管理员"),
    UserSeed("operator", "operator@localhost.localdomain", "运营人员", "运营人员"),
    UserSeed("finance", "finance@localhost.localdomain", "财务人员", "财务人员"),
    UserSeed("user1", "user1@localhost.localdomain", "普通用户1", "普通用户"),
    UserSeed("user2", "user2@localhost.localdomain", "普通用户2", "普通用户"),
)


def require_development_environment(env: Mapping[str, str] | None = None) -> None:
    values = env or os.environ
    if values.get("ENV", "").strip().lower() != "dev":
        raise RuntimeError("初始化调试数据仅允许在 dev 环境运行")
    if values.get("DEV_SEED_ALLOWED", "").strip().lower() != "true":
        raise RuntimeError("初始化调试数据仅允许通过本机开发编排运行")
    if values.get("POSTGRES_HOST", "").strip().lower() != "db":
        raise RuntimeError("初始化调试数据仅允许连接 Compose 开发数据库")


def _seed_password(env: Mapping[str, str] | None = None) -> str:
    values = env or os.environ
    password = values.get("DEV_SEED_PASSWORD", "admin123")
    if len(password) < 8:
        raise ValueError("DEV_SEED_PASSWORD 长度必须至少为 8")
    return password


async def _active_roles(db: AsyncSession) -> dict[str, Role]:
    result = await db.execute(select(Role).where(Role.is_deleted.is_(False)))
    return {role.name: role for role in result.scalars().all()}


async def _active_users(db: AsyncSession) -> dict[str, User]:
    result = await db.execute(select(User).where(User.is_deleted.is_(False)))
    return {user.username: user for user in result.scalars().all()}


async def ensure_roles(db: AsyncSession) -> dict[str, int]:
    roles = await _active_roles(db)
    result = {"created": 0, "updated": 0, "skipped": 0}

    for seed in ROLE_SEEDS:
        role = roles.get(seed.name)
        if role is None:
            role = Role(name=seed.name, description=seed.description)
            db.add(role)
            roles[seed.name] = role
            result["created"] += 1
        elif role.description != seed.description:
            role.description = seed.description
            result["updated"] += 1
        else:
            result["skipped"] += 1

    await db.flush()
    return result


async def ensure_users(db: AsyncSession, password: str) -> dict[str, int]:
    users = await _active_users(db)
    result = {"created": 0, "updated": 0, "skipped": 0}

    for seed in USER_SEEDS:
        user = users.get(seed.username)
        if user is None:
            user = User(
                username=seed.username,
                email=seed.email,
                full_name=seed.full_name,
                hashed_password=get_password_hash(password),
                is_superuser=seed.is_superuser,
                is_multi_login=seed.is_multi_login,
            )
            db.add(user)
            users[seed.username] = user
            result["created"] += 1
            continue

        changed = False
        for field_name, expected in (
            ("email", seed.email),
            ("full_name", seed.full_name),
            ("is_superuser", seed.is_superuser),
            ("is_multi_login", seed.is_multi_login),
        ):
            if getattr(user, field_name) != expected:
                setattr(user, field_name, expected)
                changed = True
        if not verify_password(password, user.hashed_password):
            user.hashed_password = get_password_hash(password)
            changed = True

        result["updated" if changed else "skipped"] += 1

    await db.flush()
    return result


async def ensure_user_roles(db: AsyncSession) -> dict[str, int]:
    roles = await _active_roles(db)
    users = await _active_users(db)
    existing = {
        (int(user_id), int(role_id))
        for user_id, role_id in (await db.execute(select(user_role.c.user_id, user_role.c.role_id))).all()
    }
    expected: set[tuple[int, int]] = set()

    for seed in USER_SEEDS:
        user = users[seed.username]
        role = roles[seed.role_name]
        if user.id is None or role.id is None:
            raise RuntimeError(f"初始化身份尚未持久化: {seed.username}/{seed.role_name}")
        expected.add((user.id, role.id))

    seed_user_ids = {user_id for user_id, _role_id in expected}
    missing = expected - existing
    extra = {pair for pair in existing if pair[0] in seed_user_ids and pair not in expected}

    if missing:
        await db.execute(
            insert(user_role),
            [{"user_id": user_id, "role_id": role_id} for user_id, role_id in sorted(missing)],
        )
    if extra:
        await db.execute(delete(user_role).where(tuple_(user_role.c.user_id, user_role.c.role_id).in_(extra)))
    return {
        "added": len(missing),
        "removed": len(extra),
        "skipped": len(expected & existing),
    }


async def _builtin_role_user_ids(db: AsyncSession) -> list[int]:
    result = await db.execute(
        select(user_role.c.user_id)
        .join(Role, Role.id == user_role.c.role_id)
        .where(Role.name.in_([seed.name for seed in ROLE_SEEDS]), Role.is_deleted.is_(False))
        .distinct()
    )
    return [int(user_id) for user_id in result.scalars().all()]


async def _invalidate_builtin_role_user_permission_cache(db: AsyncSession) -> None:
    await invalidate_users_permissions(get_cache(), await _builtin_role_user_ids(db))


async def _seed_foundation_data(db: AsyncSession, frontend_path: str, password: str) -> None:
    app = create_app()
    menu_definitions = menu_sync_service.load_frontend_menu_definitions(frontend_path)
    if not menu_definitions:
        raise RuntimeError("前端菜单定义为空，拒绝生成不可调试的开发数据")
    managed_permission_names = managed_permission_names_for_app(app)
    if not managed_permission_names:
        raise RuntimeError("权限定义为空，拒绝生成不可调试的开发数据")
    managed_menu_names = {definition.name for definition in menu_definitions}

    try:
        role_result = await ensure_roles(db)
        user_result = await ensure_users(db, password)
        user_role_result = await ensure_user_roles(db)
        permission_result = await permission_catalog_service.sync(app, db, dry_run=False)
        role_permission_result = await sync_builtin_role_permissions(
            db,
            auto_commit=False,
            exact=True,
            managed_permission_names=managed_permission_names,
        )
        menu_result = await menu_sync_service.sync_menus(db, menu_definitions, auto_commit=False)
        if menu_result.errors:
            raise RuntimeError(f"前端菜单同步失败: {menu_result.errors}")
        role_menu_result = await menu_sync_service.sync_builtin_role_menus(
            db,
            auto_commit=False,
            exact=True,
            managed_menu_names=managed_menu_names,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    print("开发基础数据已收敛:")
    print(f"  roles={role_result}")
    print(f"  users={user_result}")
    print(f"  user_roles={user_role_result}")
    print(f"  permissions={permission_result}")
    print(f"  role_permissions={role_permission_result}")
    print(f"  menus=created:{menu_result.created},updated:{menu_result.updated},skipped:{menu_result.skipped}")
    print(
        "  role_menus="
        f"processed:{role_menu_result.roles_processed},added:{role_menu_result.added},"
        f"removed:{role_menu_result.removed},skipped:{role_menu_result.skipped}"
    )


async def _check_foundation_data(db: AsyncSession, frontend_path: str, password: str) -> None:
    errors: list[str] = []
    roles = await _active_roles(db)
    users = await _active_users(db)

    for seed in ROLE_SEEDS:
        role = roles.get(seed.name)
        if role is None:
            errors.append(f"缺少角色 {seed.name}")
        elif role.description != seed.description:
            errors.append(f"角色字段漂移 {seed.name}")

    existing_links = {
        (int(user_id), int(role_id))
        for user_id, role_id in (await db.execute(select(user_role.c.user_id, user_role.c.role_id))).all()
    }
    for seed in USER_SEEDS:
        user = users.get(seed.username)
        role = roles.get(seed.role_name)
        if user is None:
            errors.append(f"缺少用户 {seed.username}")
            continue
        if (
            user.email != seed.email
            or user.full_name != seed.full_name
            or user.is_superuser != seed.is_superuser
            or user.is_multi_login != seed.is_multi_login
            or not verify_password(password, user.hashed_password)
        ):
            errors.append(f"用户字段漂移 {seed.username}")
        if user.id is not None and role is not None and role.id is not None:
            actual_role_ids = {role_id for user_id, role_id in existing_links if user_id == user.id}
            if actual_role_ids != {role.id}:
                errors.append(f"用户角色未收敛 {seed.username}/{seed.role_name}")
        else:
            errors.append(f"缺少用户角色关系 {seed.username}/{seed.role_name}")

    app = create_app()
    managed_permission_names = managed_permission_names_for_app(app)
    if not managed_permission_names:
        errors.append("权限定义为空")
    permission_result = await permission_catalog_service.sync(app, db, dry_run=True)
    if permission_result.created or permission_result.updated or permission_result.deleted:
        errors.append(f"权限未收敛 {permission_result}")
    role_permission_result = await sync_builtin_role_permissions(
        db,
        dry_run=True,
        auto_commit=False,
        exact=True,
        managed_permission_names=managed_permission_names,
    )
    if role_permission_result["added"] or role_permission_result["removed"]:
        errors.append(f"角色权限未收敛 {role_permission_result}")

    menu_definitions = menu_sync_service.load_frontend_menu_definitions(frontend_path)
    if not menu_definitions:
        errors.append("前端菜单定义为空")
    menu_result = await menu_sync_service.sync_menus(db, menu_definitions, dry_run=True, auto_commit=False)
    if menu_result.created or menu_result.updated or menu_result.errors:
        errors.append(f"菜单未收敛 {menu_result.summary()}")
    role_menu_result = await menu_sync_service.sync_builtin_role_menus(
        db,
        dry_run=True,
        auto_commit=False,
        exact=True,
        managed_menu_names={definition.name for definition in menu_definitions},
    )
    if role_menu_result.added or role_menu_result.removed:
        errors.append(f"角色菜单未收敛 added={role_menu_result.added},removed={role_menu_result.removed}")

    if errors:
        raise RuntimeError("开发基础数据检查失败: " + "; ".join(errors))
    print("开发基础数据检查通过")


async def main_async(args: argparse.Namespace) -> None:
    require_development_environment()
    password = _seed_password()

    await init_db()
    async with get_db_context() as db:
        if args.check:
            await _check_foundation_data(db, args.frontend_path, password)
        else:
            await _seed_foundation_data(db, args.frontend_path, password)
            await _invalidate_builtin_role_user_permission_cache(db)


def main() -> None:
    parser = argparse.ArgumentParser(description="收敛本机 dev 环境的基础调试数据")
    parser.add_argument("--frontend-path", required=True, help="当前前端源码根目录")
    parser.add_argument("--check", action="store_true", help="只读检查数据是否已经收敛")
    args = parser.parse_args()
    frontend_path = Path(args.frontend_path).resolve()
    if not Path(frontend_path, "src/router/index.ts").is_file():
        raise FileNotFoundError(f"前端源码路径无效: {frontend_path}")
    args.frontend_path = str(frontend_path)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
