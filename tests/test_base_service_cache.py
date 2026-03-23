from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from src.core.base_service import BaseService
from src.database.redis_cache import RedisCache


class FakeChildResponse(BaseModel):
    id: int
    name: str


class FakeModel(BaseModel):
    id: int
    name: str


class FakeModelFlat(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class FakeModelWithRelationResponse(BaseModel):
    id: int
    name: str
    children: list[FakeChildResponse] = Field(default_factory=list)


class _FakeChildRow:
    def __init__(self, id: int, name: str) -> None:
        self.id = id
        self.name = name


class _FakeRowWithRelation:
    def __init__(self, id: int, name: str, children: list[_FakeChildRow]) -> None:
        self.id = id
        self.name = name
        self.children = children


class FakeRedisCache(RedisCache):
    def __init__(self) -> None:
        super().__init__(redis=None, prefix="app")
        self.storage: dict[str, Any] = {}
        self.set_calls: list[tuple[str, Any, int | None, bool]] = []
        self.deleted_patterns: list[str] = []

    async def get(self, key: str) -> Any | None:
        return self.storage.get(key)

    async def set(self, key: str, value: Any, expire: int | None = None, is_hot: bool = False) -> bool:
        self.storage[key] = value
        self.set_calls.append((key, value, expire, is_hot))
        return True

    async def delete_pattern(self, pattern: str) -> int:
        self.deleted_patterns.append(pattern)
        keys_to_delete = [key for key in self.storage if fnmatch(key, pattern)]
        for key in keys_to_delete:
            del self.storage[key]
        return len(keys_to_delete)


class FakeRepo:
    _model_name = "FakeModel"
    model = FakeModel

    def __init__(self) -> None:
        self.get_by_id_calls = 0
        self.get_list_calls = 0
        self.update_calls: list[tuple[int, dict[str, Any]]] = []
        self.by_id_result: FakeModel | None = None
        self.list_result: tuple[int, list[FakeModel]] = (0, [])

    async def get_by_id(self, db: object, id: int, **kwargs: Any) -> FakeModel | None:
        self.get_by_id_calls += 1
        return self.by_id_result

    async def get_list(self, db: object, limit: int, offset: int, filters: Any, sort: Any, **kwargs: Any) -> tuple[int, list[FakeModel]]:
        self.get_list_calls += 1
        return self.list_result

    async def update(self, db: object, id: int, data: dict[str, Any]) -> FakeModel:
        self.update_calls.append((id, dict(data)))
        return FakeModel(id=id, name=data.get("name", "updated"))


@pytest.mark.asyncio
async def test_update_invalidates_detail_and_list_cache() -> None:
    repo = FakeRepo()
    cache = FakeRedisCache()
    service = BaseService(
        repo,
        enable_cache=True,
        cache_prefix="fake:detail",
        cache_expire=7200,
        list_cache_prefix="fake:list",
        list_cache_expire=600,
    )

    await service.update(object(), 1, {"name": "changed"}, cache)

    assert "fake:detail:1:*" in cache.deleted_patterns
    assert "fake:list:*" in cache.deleted_patterns


@pytest.mark.asyncio
async def test_get_list_uses_list_cache_ttl_and_caches_empty_page() -> None:
    repo = FakeRepo()
    repo.list_result = (5, [])
    cache = FakeRedisCache()
    service = BaseService(
        repo,
        enable_cache=True,
        cache_prefix="fake:detail",
        cache_expire=7200,
        list_cache_prefix="fake:list",
        list_cache_expire=600,
    )

    total, items = await service.get_list(object(), cache, limit=10, offset=10)
    assert (total, items) == (5, [])
    assert repo.get_list_calls == 1
    cache_key, cache_value, cache_expire, _ = cache.set_calls[-1]
    assert cache_key.startswith("fake:list:l10:o10:f")
    assert cache_value == {"total": 5, "items": []}
    assert cache_expire == 600

    total, items = await service.get_list(object(), cache, limit=10, offset=10)
    assert (total, items) == (5, [])
    assert repo.get_list_calls == 1


@pytest.mark.asyncio
async def test_get_list_cache_preserves_relation_fields_via_response_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo()
    repo.model = FakeModelFlat
    repo.list_result = (
        1,
        [_FakeRowWithRelation(1, "parent", [_FakeChildRow(10, "child")])],
    )
    cache = FakeRedisCache()
    service = BaseService(
        repo,
        enable_cache=True,
        cache_prefix="fake:detail",
        cache_expire=7200,
        list_cache_prefix="fake:list",
        list_cache_expire=600,
        response_schema=FakeModelWithRelationResponse,
    )

    def fake_model_to_schema(obj: Any, schema: type[BaseModel]) -> BaseModel:
        if schema is FakeModelWithRelationResponse:
            return FakeModelWithRelationResponse(
                id=obj.id,
                name=obj.name,
                children=[FakeChildResponse(id=child.id, name=child.name) for child in obj.children],
            )
        raise AssertionError(f"unexpected schema: {schema}")

    monkeypatch.setattr("src.core.base_service.model_to_schema", fake_model_to_schema)

    total, first_items = await service.get_list(object(), cache, limit=10, offset=0)
    assert total == 1
    assert len(first_items) == 1

    cache_key, cache_value, _, _ = cache.set_calls[-1]
    assert cache_key.startswith("fake:list:l10:o0:f")
    assert cache_value == {
        "total": 1,
        "items": [{"id": 1, "name": "parent", "children": [{"id": 10, "name": "child"}]}],
    }

    total, cached_items = await service.get_list(object(), cache, limit=10, offset=0)
    assert total == 1
    assert repo.get_list_calls == 1

    response_items = service.to_list_response(cached_items, FakeModelWithRelationResponse)
    assert response_items[0].children[0].id == 10
    assert response_items[0].children[0].name == "child"


def test_to_response_uses_model_to_schema_for_non_schema_basemodel(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo()
    service = BaseService(repo, response_schema=FakeModelWithRelationResponse)
    source = FakeModel(id=1, name="parent")

    def fake_model_to_schema(obj: Any, schema: type[BaseModel]) -> BaseModel:
        assert obj is source
        assert schema is FakeModelWithRelationResponse
        return FakeModelWithRelationResponse(id=1, name="parent", children=[FakeChildResponse(id=10, name="child")])

    monkeypatch.setattr("src.core.base_service.model_to_schema", fake_model_to_schema)

    response = service.to_response(source, FakeModelWithRelationResponse)

    assert response.children[0].id == 10
    assert response.children[0].name == "child"


@pytest.mark.asyncio
async def test_get_by_id_caches_null_result_with_short_ttl() -> None:
    repo = FakeRepo()
    cache = FakeRedisCache()
    service = BaseService(
        repo,
        enable_cache=True,
        cache_prefix="fake:detail",
        cache_expire=7200,
        null_cache_expire=120,
    )

    assert await service.get_by_id(object(), cache, 42) is None
    assert repo.get_by_id_calls == 1
    assert cache.set_calls[-1][:3] == ("fake:detail:42:depth2:delFalse", "__BASE_SERVICE_NULL__", 120)

    assert await service.get_by_id(object(), cache, 42) is None
    assert repo.get_by_id_calls == 1
