from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.tree_service import TreeServiceMixin


class _FakeRepo:
    def __init__(self, move_result: object):
        self._move_result = move_result
        self.move_node = AsyncMock(return_value=move_result)
        self.get_descendants = AsyncMock()


class _FakeTreeService(TreeServiceMixin[object]):
    def __init__(self, repo: object):
        self.repo = repo
        self.invalidate_cache = AsyncMock()

    def _to_dict(self, item: object, schema: type[object] | None = None) -> dict[str, object]:
        return {"id": getattr(item, "id", None)}


@pytest.mark.asyncio
async def test_move_node_uses_repo_descendant_metadata_for_cache_invalidation() -> None:
    result = SimpleNamespace(id=10, _moved_descendant_ids=[11, 12])
    repo = _FakeRepo(result)
    service = _FakeTreeService(repo)
    db = AsyncMock()
    cache = object()

    payload = await service.move_node(db, 10, 20, cache=cache)

    db.commit.assert_awaited_once()
    repo.move_node.assert_awaited_once_with(db, 10, 20)
    repo.get_descendants.assert_not_awaited()
    assert payload == {"id": 10}

    invalidate_calls = service.invalidate_cache.await_args_list
    assert len(invalidate_calls) == 3
    assert invalidate_calls[0].args == (cache,)
    assert invalidate_calls[0].kwargs == {"id": 10, "invalidate_list": True, "invalidate_tree": True}
    assert invalidate_calls[1].args == (cache,)
    assert invalidate_calls[1].kwargs == {"id": 11, "invalidate_list": False}
    assert invalidate_calls[2].args == (cache,)
    assert invalidate_calls[2].kwargs == {"id": 12, "invalidate_list": False}
