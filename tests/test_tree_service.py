from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.base_service import BaseService
from src.core.tree_service import TreeServiceMixin


class _FakeRepo:
    _model_name = "FakeTreeNode"
    model = SimpleNamespace(__name__="FakeTreeNode")

    def __init__(self, move_result: object | None = None, update_result: object | None = None):
        self._move_result = move_result
        self._update_result = update_result
        self.move_node = AsyncMock(return_value=move_result)
        self.update = AsyncMock(return_value=update_result)
        self.get_by_id = AsyncMock()
        self.get_descendants = AsyncMock()


class _FakeTreeService(TreeServiceMixin[object], BaseService[object, _FakeRepo]):
    def __init__(self, repo: _FakeRepo):
        super().__init__(repo)
        self.invalidate_cache = AsyncMock()

    def _to_dict(self, item: object, schema: type[object] | None = None) -> dict[str, object]:
        return {"id": getattr(item, "id", None)}


@pytest.mark.asyncio
async def test_move_node_uses_repo_descendant_metadata_for_cache_invalidation() -> None:
    result = SimpleNamespace(id=10, _moved_descendant_ids=[11, 12])
    repo = _FakeRepo(move_result=result)
    repo.get_by_id = AsyncMock(return_value=None)
    service = _FakeTreeService(repo)
    db = AsyncMock()
    cache = object()

    payload = await service.move_node(db, 10, 20, cache=cache)

    db.commit.assert_awaited_once()
    repo.get_by_id.assert_awaited_once_with(db, 10)
    repo.move_node.assert_awaited_once_with(db, 10, 20)
    repo.get_descendants.assert_not_awaited()
    assert payload == {"id": 10}

    invalidate_calls = service.invalidate_cache.await_args_list
    assert len(invalidate_calls) == 4
    assert invalidate_calls[0].args == (cache,)
    assert invalidate_calls[0].kwargs == {"id": 10, "invalidate_list": True, "invalidate_tree": True}
    assert invalidate_calls[1].args == (cache,)
    assert invalidate_calls[1].kwargs == {"id": 11, "invalidate_list": False}
    assert invalidate_calls[2].args == (cache,)
    assert invalidate_calls[2].kwargs == {"id": 12, "invalidate_list": False}
    assert invalidate_calls[3].args == (cache,)
    assert invalidate_calls[3].kwargs == {"id": 20}


@pytest.mark.asyncio
async def test_move_node_invalidates_old_and_new_parent_cache() -> None:
    result = SimpleNamespace(id=10, _moved_descendant_ids=[])
    repo = _FakeRepo(move_result=result)
    repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id=10, parent_id=5))

    service = _FakeTreeService(repo)
    db = AsyncMock()
    cache = object()

    payload = await service.move_node(db, 10, 20, cache=cache)

    assert payload == {"id": 10}
    repo.get_by_id.assert_awaited_once_with(db, 10)
    repo.move_node.assert_awaited_once_with(db, 10, 20)
    db.commit.assert_awaited_once()

    invalidate_calls = service.invalidate_cache.await_args_list
    assert len(invalidate_calls) == 3
    assert invalidate_calls[0].args == (cache,)
    assert invalidate_calls[0].kwargs == {"id": 10, "invalidate_list": True, "invalidate_tree": True}
    assert invalidate_calls[1].args == (cache,)
    assert invalidate_calls[1].kwargs == {"id": 20}
    assert invalidate_calls[2].args == (cache,)
    assert invalidate_calls[2].kwargs == {"id": 5}


@pytest.mark.asyncio
async def test_update_invalidates_old_and_new_parent_cache_on_parent_change() -> None:
    repo = _FakeRepo(
        update_result=SimpleNamespace(id=10, parent_id=20),
    )
    repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id=10, parent_id=5))

    service = _FakeTreeService(repo)
    db = AsyncMock()
    cache = object()

    result = await service.update(db, 10, {"parent_id": 20}, cache=cache)

    assert result is not None
    repo.get_by_id.assert_awaited_once_with(db, 10)
    repo.update.assert_awaited_once_with(db, 10, {"parent_id": 20})
    db.commit.assert_awaited_once()

    invalidate_calls = service.invalidate_cache.await_args_list
    assert len(invalidate_calls) == 4
    assert invalidate_calls[0].args == (cache, 10)
    assert invalidate_calls[0].kwargs == {"invalidate_list": True}
    assert invalidate_calls[1].args == (cache,)
    assert invalidate_calls[1].kwargs == {"invalidate_tree": True}
    assert invalidate_calls[2].args == (cache,)
    assert invalidate_calls[2].kwargs == {"id": 20}
    assert invalidate_calls[3].args == (cache,)
    assert invalidate_calls[3].kwargs == {"id": 5}
