from __future__ import annotations

from pydantic import BaseModel

from src.database.cache_helpers import (
    CACHE_NULL_MARKER,
    get_cached_value,
    is_null_cache_value,
    serialize_for_cache,
    set_cached_value,
)


class _FakeCache:
    def __init__(self) -> None:
        self.storage: dict[str, object] = {}
        self.deleted_keys: list[str] = []
        self.set_calls: list[tuple[str, object, int | None, bool]] = []

    async def get(self, key: str) -> object | None:
        return self.storage.get(key)

    async def set(self, key: str, value: object, expire: int | None = None, is_hot: bool = False) -> bool:
        self.storage[key] = value
        self.set_calls.append((key, value, expire, is_hot))
        return True

    async def delete(self, key: str) -> bool:
        self.deleted_keys.append(key)
        self.storage.pop(key, None)
        return True


class _FakeModel(BaseModel):
    id: int
    name: str


async def test_get_cached_value_recognizes_legacy_null_marker() -> None:
    cache = _FakeCache()
    cache.storage["legacy"] = "__NULL_CACHE_MARKER__"

    hit, value = await get_cached_value(cache, "legacy")

    assert hit is True
    assert value is None
    assert is_null_cache_value("__NULL_CACHE_MARKER__") is True
    assert is_null_cache_value(["*"]) is False


async def test_get_cached_value_deletes_invalid_payload() -> None:
    cache = _FakeCache()
    cache.storage["broken"] = 123

    def parser(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        raise TypeError("invalid payload")

    hit, value = await get_cached_value(cache, "broken", parser=parser)

    assert hit is False
    assert value is None
    assert cache.deleted_keys == ["broken"]


async def test_set_cached_value_serializes_models_and_nulls() -> None:
    cache = _FakeCache()

    await set_cached_value(cache, "model", _FakeModel(id=1, name="demo"), expire=60)
    await set_cached_value(cache, "null", None, null_expire=30)

    assert serialize_for_cache(_FakeModel(id=2, name="x")) == {"id": 2, "name": "x"}
    assert cache.set_calls[0][:3] == ("model", {"id": 1, "name": "demo"}, 60)
    assert cache.set_calls[1][:3] == ("null", CACHE_NULL_MARKER, 30)
