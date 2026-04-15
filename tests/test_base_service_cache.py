from __future__ import annotations

from fnmatch import fnmatch
from typing import Any
from unittest.mock import AsyncMock

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
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[tuple[int, dict[str, Any]]] = []
        self.delete_calls: list[int] = []
        self.soft_delete_calls: list[tuple[int, int | None]] = []
        self.restore_calls: list[int] = []
        self.permanent_delete_calls: list[int] = []
        self.by_id_result: FakeModel | None = None
        self.list_result: tuple[int, list[FakeModel]] = (0, [])
        self.delete_result: bool | None = True
        self.permanent_delete_result = True

    async def get_by_id(self, db: object, id: int, **kwargs: Any) -> FakeModel | None:
        self.get_by_id_calls += 1
        return self.by_id_result

    async def get_list(
        self, db: object, limit: int, offset: int, filters: Any, sort: Any, **kwargs: Any
    ) -> tuple[int, list[FakeModel]]:
        self.get_list_calls += 1
        return self.list_result

    async def create(self, db: object, data: dict[str, Any]) -> FakeModel:
        self.create_calls.append(dict(data))
        return FakeModel(id=1, name=data.get("name", "created"))

    async def update(self, db: object, id: int, data: dict[str, Any]) -> FakeModel:
        self.update_calls.append((id, dict(data)))
        return FakeModel(id=id, name=data.get("name", "updated"))

    async def delete(self, db: object, id: int) -> bool | None:
        self.delete_calls.append(id)
        return self.delete_result

    async def soft_delete(self, db: object, id: int, deleted_by: int | None = None) -> FakeModel:
        self.soft_delete_calls.append((id, deleted_by))
        return FakeModel(id=id, name="soft-deleted")

    async def restore(self, db: object, id: int) -> FakeModel:
        self.restore_calls.append(id)
        return FakeModel(id=id, name="restored")

    async def permanent_delete(self, db: object, id: int) -> bool:
        self.permanent_delete_calls.append(id)
        return self.permanent_delete_result


@pytest.mark.asyncio
async def test_create_commits_and_invalidates_only_list_cache() -> None:
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
    db = AsyncMock()

    created = await service.create(db, {"name": "created"}, cache)

    assert created is not None
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    assert cache.deleted_patterns == ["fake:list:*"]


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
    db = AsyncMock()

    await service.update(db, 1, {"name": "changed"}, cache)

    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    assert "fake:detail:1:*" in cache.deleted_patterns
    assert "fake:list:*" in cache.deleted_patterns


@pytest.mark.asyncio
async def test_delete_missing_record_skips_commit_and_cache_invalidation() -> None:
    repo = FakeRepo()
    repo.delete_result = False
    cache = FakeRedisCache()
    service = BaseService(
        repo,
        enable_cache=True,
        cache_prefix="fake:detail",
        cache_expire=7200,
        list_cache_prefix="fake:list",
        list_cache_expire=600,
    )
    db = AsyncMock()

    success = await service.delete(db, 9, cache)

    assert success is False
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
    assert cache.deleted_patterns == []


@pytest.mark.asyncio
async def test_soft_delete_commits_and_invalidates_detail_and_list_cache(monkeypatch: pytest.MonkeyPatch) -> None:
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
    db = AsyncMock()

    monkeypatch.setattr("src.utils.audit.get_current_user_id", lambda: 77)

    result = await service.soft_delete(db, 3, cache)

    assert result is not None
    assert repo.soft_delete_calls == [(3, 77)]
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    assert cache.deleted_patterns == ["fake:detail:3:*", "fake:list:*"]


@pytest.mark.asyncio
async def test_restore_commits_and_invalidates_detail_and_list_cache() -> None:
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
    db = AsyncMock()

    result = await service.restore(db, 4, cache)

    assert result is not None
    assert repo.restore_calls == [4]
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    assert cache.deleted_patterns == ["fake:detail:4:*", "fake:list:*"]


@pytest.mark.asyncio
async def test_permanent_delete_commits_and_invalidates_detail_and_list_cache() -> None:
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
    db = AsyncMock()

    success = await service.permanent_delete(db, 5, cache)

    assert success is True
    assert repo.permanent_delete_calls == [5]
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    assert cache.deleted_patterns == ["fake:detail:5:*", "fake:list:*"]


@pytest.mark.asyncio
async def test_commit_mutation_rolls_back_on_commit_failure() -> None:
    repo = FakeRepo()
    service = BaseService(repo)
    db = AsyncMock()
    db.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        await service._commit_mutation(db)

    db.commit.assert_awaited_once()
    db.rollback.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_get_by_id_cache_preserves_relation_fields_via_response_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo()
    repo.model = FakeModelFlat
    repo.by_id_result = _FakeRowWithRelation(1, "parent", [_FakeChildRow(10, "child")])
    cache = FakeRedisCache()
    service = BaseService(
        repo,
        enable_cache=True,
        cache_prefix="fake:detail",
        cache_expire=7200,
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

    first_item = await service.get_by_id(object(), cache, 1)
    assert first_item is not None

    cache_key, cache_value, cache_expire, _ = cache.set_calls[-1]
    assert cache_key == "fake:detail:1:depth2:delFalse"
    assert cache_value == {"id": 1, "name": "parent", "children": [{"id": 10, "name": "child"}]}
    assert cache_expire == 7200

    cached_item = await service.get_by_id(object(), cache, 1)
    assert repo.get_by_id_calls == 1

    response_item = service.to_response(cached_item, FakeModelWithRelationResponse)
    assert response_item.children[0].id == 10
    assert response_item.children[0].name == "child"


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
