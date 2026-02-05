import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import Body, Depends, Request

from src.app.api_auth.constants import CacheExpire, CacheKeys
from src.app.api_auth.models import APIApplication
from src.app.api_auth.services import SignatureService, api_app_service, get_app_permissions
from src.core.encryption import encryption_service
from src.core.exceptions import AuthException, PermissionException, RateLimitException
from src.database.dependencies import AsyncSessionDep, CacheDep
from src.utils.timezone import timezone


@dataclass
class APIAppContext:
    app_id: str
    app_name: str
    app_type: str
    permissions: set[str]


async def verify_api_auth(
    request: Request, db: AsyncSessionDep, cache: CacheDep, body: bytes = Body(default=b"")
) -> APIAppContext | None:
    app_id: str | None = request.headers.get("X-App-ID")
    timestamp: str | None = request.headers.get("X-Timestamp")
    signature: str | None = request.headers.get("X-Signature")

    if not app_id or not timestamp or not signature:
        return None

    try:
        request_time = int(str(timestamp))
        current_time = int(time.time())
        if current_time - request_time > 300:
            raise AuthException(f"请求已过期 (时间差: {current_time - request_time}秒)")
        if request_time > current_time + 60:  # 允许 1 分钟时钟偏差
            raise AuthException("请求时间戳不能是未来时间")
    except ValueError as ve:
        raise AuthException("时间戳格式错误") from ve

    app = await api_app_service.get_by_app_id(db, cache, app_id)
    if not app:
        raise AuthException(f"应用不存在: {app_id}")

    if app.status != "active":
        raise AuthException(f"应用已被禁用: {app.status}")

    if app.expires_at and app.expires_at < timezone.now_for_db():
        raise AuthException("应用已过期")

    app_secret = encryption_service.decrypt(app.app_secret_encrypted)

    expected_signature = SignatureService.calculate(
        app_secret=app_secret,
        app_id=app_id,
        timestamp=str(timestamp),
        method=request.method,
        path=str(request.url.path),
        body=body.decode("utf-8"),
    )

    if not SignatureService.verify(expected_signature, signature):
        raise AuthException("签名验证失败")

    if app.ip_whitelist:
        client_ip = request.client.host if request.client else "unknown"
        if client_ip not in app.ip_whitelist:
            raise AuthException(f"IP {client_ip} 不在白名单中")

    await _check_rate_limit(cache, app)

    permissions = await get_app_permissions(db, cache, app.id)  # type: ignore[arg-type]

    request.state.api_app_id = app.app_id
    request.state.api_app_name = app.app_name

    return APIAppContext(
        app_id=app.app_id,
        app_name=app.app_name,
        app_type=app.app_type,
        permissions=permissions,
    )


async def _check_rate_limit(cache: CacheDep, app: APIApplication) -> None:
    current_time = int(time.time())

    # 分钟级速率限制
    minute_key = CacheKeys.rate_limit_minute(app.app_id, current_time // 60)
    minute_count = await cache.incr_with_expire(minute_key, CacheExpire.RATE_LIMIT_MINUTE)

    if minute_count is None:
        return  # Redis 不可用,跳过速率限制
    if minute_count > app.rate_limit_per_minute:
        raise RateLimitException(f"超过每分钟请求限制 ({app.rate_limit_per_minute})")

    # 小时级速率限制
    hour_key = CacheKeys.rate_limit_hour(app.app_id, current_time // 3600)
    hour_count = await cache.incr_with_expire(hour_key, CacheExpire.RATE_LIMIT_HOUR)

    if hour_count is None:
        return  # Redis 不可用,跳过速率限制

    if hour_count > app.rate_limit_per_hour:
        raise RateLimitException(f"超过每小时请求限制 ({app.rate_limit_per_hour})")


DependsAPIAuth = Annotated[APIAppContext | None, Depends(verify_api_auth)]


async def require_api_auth(
    app_ctx: DependsAPIAuth,
) -> APIAppContext:
    if app_ctx is None:
        raise AuthException("需要 API 认证")
    return app_ctx


RequireAPIAuth = Annotated[APIAppContext, Depends(require_api_auth)]


def RequireAPIPermission(permission_name: str):
    async def verify_permission(
        app_ctx: RequireAPIAuth,
    ) -> None:
        if permission_name not in app_ctx.permissions:
            raise PermissionException(f"需要权限: {permission_name}")

    # 关键点：挂载元数据，方便扫描器读取
    verify_permission.permission_required = permission_name
    verify_permission.is_api_auth = True
    return verify_permission
