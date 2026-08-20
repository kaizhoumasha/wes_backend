"""为标准 E2E 环境幂等创建 Mock callback API 应用。"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from src.app.admin.services import permission_catalog_service, permission_service
from src.app.api_auth.services import api_app_service
from src.core.conf import settings
from src.core.encryption import encryption_service
from src.database.db import close_db, get_db_context, init_db
from src.database.redis_cache import get_cache
from src.database.redis_client import close_redis, init_redis
from src.register import create_app

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.admin.services import PermissionService
    from src.app.api_auth.services import APIAppService
    from src.database.redis_cache import RedisCache

CALLBACK_PERMISSION = "api:callback:event"


async def provision_e2e_callback_application(
    db: AsyncSession,
    cache: RedisCache,
    *,
    app_id: str,
    app_secret: str,
    app_service: APIAppService = api_app_service,
    permissions: PermissionService = permission_service,
) -> int:
    """创建或刷新专用 E2E callback 应用，并只授予 callback 权限。"""

    existing = await app_service.get_by_app_id(db, cache, app_id)
    app_data: dict[str, Any] = {
        "app_name": "E2E Mock Callback",
        "app_type": "WMS",
        "description": "仅供本地 E2E Mock 回调使用",
        "expires_at": None,
        "ip_whitelist": None,
        "validity_period": "never",
        "status": "active",
        "app_secret_encrypted": encryption_service.encrypt(app_secret),
    }
    if existing is None:
        application = await app_service.create(db, {**app_data, "app_id": app_id}, cache)
    else:
        if existing.id is None:
            raise RuntimeError(f"E2E callback API application has no id: {app_id}")
        application = await app_service.update(
            db,
            existing.id,
            {**app_data, "version": existing.version},
            cache,
        )
    if application is None or application.id is None:
        raise RuntimeError(f"Failed to provision E2E callback API application: {app_id}")

    callback_permission = next(
        (
            permission
            for permission in await permissions.get_api_permissions(db, perm_type="app_api")
            if permission.name == CALLBACK_PERMISSION
        ),
        None,
    )
    if callback_permission is None or callback_permission.id is None:
        raise RuntimeError(f"E2E callback permission is unavailable: {CALLBACK_PERMISSION}")

    await app_service.assign_permissions(db, cache, application.id, [callback_permission.id])
    return application.id


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for E2E callback credential provisioning")
    return value


async def main() -> None:
    if settings.APP_ENV != "test":
        raise RuntimeError("E2E callback credential provisioning is restricted to APP_ENV=test")

    await init_db()
    await init_redis()
    try:
        async with get_db_context() as db:
            permission_result = await permission_catalog_service.sync(create_app(), db, dry_run=False)
            if permission_result.created or permission_result.updated or permission_result.deleted:
                await db.commit()
            application_id = await provision_e2e_callback_application(
                db,
                get_cache(),
                app_id=_required_environment("API_APP_ID"),
                app_secret=_required_environment("API_APP_SECRET"),
            )
            print(f"E2E callback API application ready: id={application_id}, permission={CALLBACK_PERMISSION}")
    finally:
        await close_redis()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
