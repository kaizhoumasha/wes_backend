import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.wms_integration.models import (
    ConfirmInboundRequest,
    ConfirmOutboundRequest,
    QueryInventoryRequest,
    QueryInventoryResponse,
    ReleaseReservationRequest,
    ReserveInventoryRequest,
    WmsCallEvidence,
    WmsCircuitBreakerState,
)
from src.app.wms_integration.services import (
    WMS_QUERY_CACHE_TTL_SECONDS,
    WmsCircuitBreakerService,
    WmsEndpointConfig,
    WmsHttpClient,
    WmsHttpTimeoutConfig,
    WmsTypedPortService,
    build_query_inventory_cache_key,
)
from src.app.wms_integration.services.cache import WmsQueryCacheService
from src.database.redis_cache import RedisCache


def _session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


def _endpoint_config() -> WmsEndpointConfig:
    return WmsEndpointConfig(
        base_url="http://wms.test/api",
        timeout=WmsHttpTimeoutConfig(connect=1.1, read=2.2, write=3.3, pool=4.4),
    )


def _service(
    db_engine,
    transport: httpx.AsyncBaseTransport,
    *,
    cache=None,
    query_cache_ttl_seconds: int = WMS_QUERY_CACHE_TTL_SECONDS,
) -> WmsTypedPortService:
    return WmsTypedPortService(
        session_factory=_session_factory(db_engine),
        endpoint_config=_endpoint_config(),
        http_client=WmsHttpClient(transport=transport),
        breaker_service=WmsCircuitBreakerService(failure_threshold=2, retry_after_seconds=60),
        evidence_key_factory=lambda operation_name, request_id: f"ev:{operation_name}:{request_id}",
        cache=cache,
        query_cache_ttl_seconds=query_cache_ttl_seconds,
    )


async def _get_evidence(db_engine, evidence_key: str) -> WmsCallEvidence | None:
    async with _session_factory(db_engine)() as db:
        result = await db.execute(select(WmsCallEvidence).where(WmsCallEvidence.evidence_key == evidence_key))
        return result.scalar_one_or_none()


async def _get_breaker_state(db_engine, *, operation_name: str) -> WmsCircuitBreakerState | None:
    async with _session_factory(db_engine)() as db:
        result = await db.execute(
            select(WmsCircuitBreakerState).where(WmsCircuitBreakerState.operation_name == operation_name)
        )
        return result.scalar_one_or_none()


class FakeCache:
    def __init__(self, initial: dict[str, object] | None = None, *, fail_get: bool = False, fail_set: bool = False):
        self.storage = dict(initial or {})
        self.fail_get = fail_get
        self.fail_set = fail_set
        self.get_keys: list[str] = []
        self.set_calls: list[dict[str, object]] = []
        self.delete_keys: list[str] = []

    async def get(self, key: str):
        self.get_keys.append(key)
        if self.fail_get:
            raise RuntimeError("redis unavailable")
        return self.storage.get(key)

    async def set(self, key: str, value, expire: int | None = None, is_hot: bool = False) -> bool:
        self.set_calls.append({"key": key, "value": value, "expire": expire, "is_hot": is_hot})
        if self.fail_set:
            raise RuntimeError("redis write unavailable")
        self.storage[key] = value
        return True

    async def delete(self, key: str) -> bool:
        self.delete_keys.append(key)
        self.storage.pop(key, None)
        return True


class FakeRedis:
    def __init__(self) -> None:
        self.setex_calls: list[dict[str, object]] = []

    async def ping(self) -> bool:
        return True

    async def setex(self, key: str, expire: int, value: str) -> bool:
        self.setex_calls.append({"key": key, "expire": expire, "value": value})
        return True


@pytest.mark.asyncio
async def test_cache_hit_returns_query_inventory_without_http_evidence_or_breaker(db_engine) -> None:
    request = QueryInventoryRequest(request_id="REQ-HIT-1", trace_id="TRACE-1", sku="SKU-1", warehouse_code="WH-1")
    cache_key = build_query_inventory_cache_key(request)
    cache = FakeCache(
        {
            cache_key: {
                "request_id": "REQ-CACHED",
                "items": [{"sku": "SKU-1", "warehouse_code": "WH-1", "available_qty": "9"}],
            }
        }
    )
    http_called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_called
        http_called = True
        return httpx.Response(200, json={"items": []})

    service = _service(db_engine, httpx.MockTransport(handler), cache=cache)

    response = await service.query_inventory(request)

    evidence = await _get_evidence(db_engine, "ev:query_inventory:REQ-HIT-1")
    breaker_state = await _get_breaker_state(db_engine, operation_name="query_inventory")
    assert response.request_id == "REQ-HIT-1"
    assert response.items[0].available_qty == 9
    assert http_called is False
    assert evidence is None
    assert breaker_state is None


@pytest.mark.asyncio
async def test_query_inventory_cache_key_is_canonical_and_excludes_request_identity(db_engine) -> None:
    first_request = QueryInventoryRequest(
        request_id="REQ-CANON-1",
        trace_id="TRACE-A",
        sku="SKU-1",
        warehouse_code="WH-1",
        owner_code="OWNER-1",
    )
    second_request = QueryInventoryRequest(
        request_id="REQ-CANON-2",
        trace_id="TRACE-B",
        owner_code="OWNER-1",
        warehouse_code="WH-1",
        sku="SKU-1",
    )
    cache = FakeCache()
    http_calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(
            200,
            json={"items": [{"sku": "SKU-1", "warehouse_code": "WH-1", "available_qty": "7"}]},
        )

    service = _service(db_engine, httpx.MockTransport(handler), cache=cache)

    first_response = await service.query_inventory(first_request)
    second_response = await service.query_inventory(second_request)

    assert build_query_inventory_cache_key(first_request) == build_query_inventory_cache_key(second_request)
    assert first_response.items[0].available_qty == 7
    assert second_response.items[0].available_qty == 7
    assert http_calls == 1
    assert await _get_evidence(db_engine, "ev:query_inventory:REQ-CANON-1") is not None
    assert await _get_evidence(db_engine, "ev:query_inventory:REQ-CANON-2") is None


@pytest.mark.asyncio
async def test_query_inventory_cache_ttl_is_clamped_to_thirty_seconds(db_engine) -> None:
    cache = FakeCache()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"sku": "SKU-1", "available_qty": "3"}]})

    service = _service(db_engine, httpx.MockTransport(handler), cache=cache, query_cache_ttl_seconds=999)

    response = await service.query_inventory(QueryInventoryRequest(request_id="REQ-TTL", sku="SKU-1"))

    assert response.items[0].available_qty == 3
    assert cache.set_calls[-1]["expire"] == 30


@pytest.mark.asyncio
async def test_query_inventory_real_redis_adapter_ttl_never_exceeds_thirty_seconds(monkeypatch) -> None:
    redis = FakeRedis()
    cache = RedisCache(redis=redis, prefix="test")
    monkeypatch.setattr(cache, "_random_expire", lambda _base_expire: 330)
    service = WmsQueryCacheService(cache, ttl_seconds=999)
    request = QueryInventoryRequest(request_id="REQ-REAL-REDIS-TTL", sku="SKU-1")
    response = QueryInventoryResponse(request_id="REQ-REAL-REDIS-TTL", items=[])

    ok = await service.set_query_inventory(request, response)

    assert ok is True
    assert redis.setex_calls[-1]["expire"] == 30


@pytest.mark.asyncio
async def test_bad_cache_and_redis_unavailable_fall_back_to_wms(db_engine) -> None:
    bad_request = QueryInventoryRequest(request_id="REQ-BAD-CACHE", sku="SKU-1")
    bad_cache_key = build_query_inventory_cache_key(bad_request)
    bad_cache = FakeCache({bad_cache_key: {"items": [{"available_qty": "missing sku"}]}})
    unavailable_cache = FakeCache(fail_get=True, fail_set=True)
    http_calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(200, json={"items": [{"sku": "SKU-1", "available_qty": "5"}]})

    service_with_bad_cache = _service(db_engine, httpx.MockTransport(handler), cache=bad_cache)
    service_with_unavailable_cache = _service(db_engine, httpx.MockTransport(handler), cache=unavailable_cache)

    bad_cache_response = await service_with_bad_cache.query_inventory(bad_request)
    unavailable_response = await service_with_unavailable_cache.query_inventory(
        QueryInventoryRequest(request_id="REQ-REDIS-DOWN", sku="SKU-1")
    )

    assert bad_cache_response.items[0].available_qty == 5
    assert unavailable_response.items[0].available_qty == 5
    assert bad_cache.delete_keys == [bad_cache_key]
    assert http_calls == 2
    assert await _get_evidence(db_engine, "ev:query_inventory:REQ-BAD-CACHE") is not None
    assert await _get_evidence(db_engine, "ev:query_inventory:REQ-REDIS-DOWN") is not None


@pytest.mark.asyncio
async def test_cache_write_failure_does_not_affect_wms_success(db_engine) -> None:
    cache = FakeCache(fail_set=True)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"sku": "SKU-1", "available_qty": "11"}]})

    service = _service(db_engine, httpx.MockTransport(handler), cache=cache)

    response = await service.query_inventory(QueryInventoryRequest(request_id="REQ-WRITE-FAIL", sku="SKU-1"))

    evidence = await _get_evidence(db_engine, "ev:query_inventory:REQ-WRITE-FAIL")
    assert response.items[0].available_qty == 11
    assert evidence is not None
    assert evidence.status == "SUCCEEDED"


@pytest.mark.asyncio
async def test_mutating_ports_never_read_or_write_cache(db_engine) -> None:
    cache = FakeCache()
    captured_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_paths.append(request.url.path)
        if request.url.path.endswith("/reserve"):
            return httpx.Response(200, json={"reservation_key": "RSV-1", "accepted": True})
        if request.method == "DELETE" and request.url.path.endswith("/reserve/RSV-1"):
            return httpx.Response(200, json={"reservation_key": "RSV-1", "released": True})
        if request.url.path.endswith("/inbound/confirm"):
            return httpx.Response(200, json={"inbound_key": "IN-1", "confirmed": True})
        return httpx.Response(200, json={"outbound_key": "OUT-1", "confirmed": True})

    service = _service(db_engine, httpx.MockTransport(handler), cache=cache)

    await service.reserve_inventory(
        ReserveInventoryRequest(request_id="REQ-RESERVE-CACHE", reservation_key="RSV-1", sku="SKU-1", qty="1")
    )
    await service.release_reservation(
        ReleaseReservationRequest(request_id="REQ-RELEASE-CACHE", reservation_key="RSV-1")
    )
    await service.confirm_inbound(
        ConfirmInboundRequest(request_id="REQ-IN-CACHE", inbound_key="IN-1", sku="SKU-1", qty="1")
    )
    await service.confirm_outbound(
        ConfirmOutboundRequest(request_id="REQ-OUT-CACHE", outbound_key="OUT-1", sku="SKU-1", qty="1")
    )

    assert captured_paths == [
        "/api/inventory/reserve",
        "/api/inventory/reserve/RSV-1",
        "/api/inbound/confirm",
        "/api/outbound/confirm",
    ]
    assert cache.get_keys == []
    assert cache.set_calls == []
