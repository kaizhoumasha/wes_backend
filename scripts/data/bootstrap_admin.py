"""
生产环境首个超级管理员 bootstrap 工具。

使用方式：
    export BOOTSTRAP_ADMIN_USERNAME=admin
    export BOOTSTRAP_ADMIN_PASSWORD='StrongPassw0rd!'
    export BOOTSTRAP_ADMIN_FULL_NAME='系统管理员'
    export BOOTSTRAP_ADMIN_EMAIL='admin@example.com'
    uv run python scripts/data/bootstrap_admin.py
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.app.admin.repositories.user_repository import UserRepository, user_repository
from src.core.security import get_password_hash
from src.database.db import get_db_context, init_db


@dataclass(slots=True)
class BootstrapAdminConfig:
    username: str
    password: str
    full_name: str | None = None
    email: str | None = None

    @property
    def normalized_username(self) -> str:
        return self.username.strip()

    @property
    def normalized_full_name(self) -> str:
        value = (self.full_name or "").strip()
        return value or self.normalized_username

    @property
    def normalized_email(self) -> str:
        value = (self.email or "").strip().lower()
        if value:
            return value
        return f"{self.normalized_username}@bootstrap.localdomain"


@dataclass(slots=True)
class BootstrapAdminResult:
    action: str
    username: str


def load_bootstrap_admin_config(env: dict[str, str] | None = None) -> BootstrapAdminConfig:
    values = env or os.environ
    username = values.get("BOOTSTRAP_ADMIN_USERNAME", "").strip()
    password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    full_name = values.get("BOOTSTRAP_ADMIN_FULL_NAME")
    email = values.get("BOOTSTRAP_ADMIN_EMAIL")

    if not username:
        raise ValueError("缺少环境变量 BOOTSTRAP_ADMIN_USERNAME")
    if not password:
        raise ValueError("缺少环境变量 BOOTSTRAP_ADMIN_PASSWORD")
    if len(password) < 8:
        raise ValueError("BOOTSTRAP_ADMIN_PASSWORD 长度必须至少为 8")

    return BootstrapAdminConfig(
        username=username,
        password=password,
        full_name=full_name,
        email=email,
    )


async def bootstrap_admin(
    db: object,
    config: BootstrapAdminConfig,
    repo: UserRepository = user_repository,
) -> BootstrapAdminResult:
    existing = await repo.get_first_superuser(db)  # type: ignore[arg-type]
    if existing is not None:
        return BootstrapAdminResult(action="skipped", username=existing.username)

    created = await repo.create(
        db,  # type: ignore[arg-type]
        {
            "username": config.normalized_username,
            "email": config.normalized_email,
            "full_name": config.normalized_full_name,
            "hashed_password": get_password_hash(config.password),
            "is_superuser": True,
            "is_multi_login": True,
        },
    )
    if created is None:
        raise RuntimeError("创建首个超级管理员失败")

    return BootstrapAdminResult(action="created", username=created.username)


async def main_async() -> None:
    config = load_bootstrap_admin_config()

    print("🚀 超级管理员 bootstrap 工具")
    print("=" * 80)
    print(f"👤 目标用户名: {config.normalized_username}")

    await init_db()
    async with get_db_context() as session:
        result = await bootstrap_admin(session, config)
        if result.action == "created":
            await session.commit()

    if result.action == "skipped":
        print(f"ℹ️  已存在超级管理员，跳过创建: {result.username}")
    else:
        print(f"✅ 已创建首个超级管理员: {result.username}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
