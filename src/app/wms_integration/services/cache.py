"""WMS 查询短缓存。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.app.wms_integration.models import QueryInventoryRequest, QueryInventoryResponse
from src.database.cache_helpers import get_cached_value, set_cached_value

WMS_QUERY_CACHE_TTL_SECONDS = 30
WMS_QUERY_CACHE_KEY_VERSION = "v1"
_QUERY_INVENTORY_CACHE_EXCLUDE_FIELDS = {"request_id", "trace_id"}


def clamp_query_cache_ttl_seconds(ttl_seconds: int) -> int:
    """限制 WMS 查询缓存 TTL，避免把短缓存误配置成长缓存。"""

    return max(1, min(int(ttl_seconds), WMS_QUERY_CACHE_TTL_SECONDS))


def build_query_inventory_cache_key(request: QueryInventoryRequest) -> str:
    """构建查询库存缓存键；排除 request_id/trace_id 等调用身份字段。"""

    payload = request.model_dump(
        mode="json",
        exclude=_QUERY_INVENTORY_CACHE_EXCLUDE_FIELDS,
        exclude_none=True,
    )
    canonical_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return f"wms:query_inventory:{WMS_QUERY_CACHE_KEY_VERSION}:{digest}"


class WmsQueryCacheService:
    """只服务 read-only WMS 查询端口的短缓存。"""

    def __init__(self, cache: Any | None, *, ttl_seconds: int = WMS_QUERY_CACHE_TTL_SECONDS) -> None:
        self.cache = cache
        self.ttl_seconds = clamp_query_cache_ttl_seconds(ttl_seconds)

    async def get_query_inventory(self, request: QueryInventoryRequest) -> QueryInventoryResponse | None:
        if self.cache is None:
            return None

        try:
            _, cached_response = await get_cached_value(
                self.cache,
                build_query_inventory_cache_key(request),
                parser=QueryInventoryResponse.model_validate,
            )
        except Exception:
            return None
        if cached_response is None:
            return None
        return cached_response.model_copy(update={"request_id": request.request_id})

    async def set_query_inventory(self, request: QueryInventoryRequest, response: QueryInventoryResponse) -> bool:
        if self.cache is None:
            return False

        try:
            return await set_cached_value(
                self.cache,
                build_query_inventory_cache_key(request),
                response,
                expire=self.ttl_seconds,
                max_expire=self.ttl_seconds,
            )
        except Exception:
            return False


__all__ = [
    "WMS_QUERY_CACHE_TTL_SECONDS",
    "WmsQueryCacheService",
    "build_query_inventory_cache_key",
    "clamp_query_cache_ttl_seconds",
]
