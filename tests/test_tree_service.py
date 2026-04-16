from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.base_service import BaseService
from src.core.tree_service import TreeServiceMixin


class _FakeRepo:
    _model_name = "FakeTreeNode"
    model = SimpleNamespace(__name__="FakeTreeNode")

    def __init__(
        self,
        move_result: object | None = None,
        update_result: object | None = None,
        batch_sort_result: object | None = None,
        delete_result: bool = True,
        restore_result: object | None = None,
        permanent_delete_result: bool = True,
    ):
        self._move_result = move_result
        self._update_result = update_result
        self._batch_sort_result = batch_sort_result
        self._permanent_delete_result = permanent_delete_result
        self.move_node = AsyncMock(return_value=move_result)
        self.update = AsyncMock(return_value=update_result)
        self.batch_sort = AsyncMock(return_value=batch_sort_result)
        self.delete = AsyncMock(return_value=delete_result)
        self.restore = AsyncMock(return_value=restore_result)
        self.permanent_delete = AsyncMock(return_value=permanent_delete_result)
        self.get_by_id = AsyncMock()
        self.get_children = AsyncMock(return_value=[])
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
async def test_batch_sort_invalidates_list_tree_once_and_all_affected_details() -> None:
    repo = _FakeRepo(
        batch_sort_result=SimpleNamespace(
            moved_descendant_ids=[11, 12],
            affected_parent_ids=[5, 20],
        )
    )
    service = _FakeTreeService(repo)
    db = AsyncMock()
    cache = object()
    items = [
        {"id": 10, "parent_id": 20, "sort_order": 1},
        {"id": 30, "parent_id": None, "sort_order": 2},
    ]

    await service.batch_sort(db, items, cache=cache)

    repo.batch_sort.assert_awaited_once_with(db, items)
    db.commit.assert_awaited_once()

    invalidate_calls = service.invalidate_cache.await_args_list
    assert len(invalidate_calls) == 7
    assert invalidate_calls[0].args == (cache,)
    assert invalidate_calls[0].kwargs == {"invalidate_list": True, "invalidate_tree": True}
    assert invalidate_calls[1].args == (cache,)
    assert invalidate_calls[1].kwargs == {"id": 10, "invalidate_list": False}
    assert invalidate_calls[2].args == (cache,)
    assert invalidate_calls[2].kwargs == {"id": 30, "invalidate_list": False}
    assert invalidate_calls[3].args == (cache,)
    assert invalidate_calls[3].kwargs == {"id": 11, "invalidate_list": False}
    assert invalidate_calls[4].args == (cache,)
    assert invalidate_calls[4].kwargs == {"id": 12, "invalidate_list": False}
    assert invalidate_calls[5].args == (cache,)
    assert invalidate_calls[5].kwargs == {"id": 5, "invalidate_list": False}
    assert invalidate_calls[6].args == (cache,)
    assert invalidate_calls[6].kwargs == {"id": 20, "invalidate_list": False}


@pytest.mark.asyncio
async def test_permanent_delete_invalidates_tree_and_parent_cache() -> None:
    repo = _FakeRepo(permanent_delete_result=True)
    repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id=10, parent_id=5, is_deleted=True))

    service = _FakeTreeService(repo)
    db = AsyncMock()
    cache = object()

    success = await service.permanent_delete(db, 10, cache=cache)

    assert success is True
    repo.get_by_id.assert_awaited_once_with(db, 10, include_deleted=True)
    repo.permanent_delete.assert_awaited_once_with(db, 10)
    db.commit.assert_awaited_once()

    invalidate_calls = service.invalidate_cache.await_args_list
    assert len(invalidate_calls) == 3
    assert invalidate_calls[0].args == (cache, 10)
    assert invalidate_calls[0].kwargs == {"invalidate_list": True}
    assert invalidate_calls[1].args == (cache,)
    assert invalidate_calls[1].kwargs == {"invalidate_tree": True}
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


@pytest.mark.asyncio
async def test_delete_rejects_node_with_children_before_repo_delete() -> None:
    repo = _FakeRepo(delete_result=True)
    repo.get_children = AsyncMock(return_value=[SimpleNamespace(id=99)])
    service = _FakeTreeService(repo)
    db = AsyncMock()
    cache = object()

    with pytest.raises(ValueError, match="当前节点存在下级节点"):
        await service.delete(db, 10, cache=cache)

    repo.get_children.assert_awaited_once_with(db, 10, include_inactive=True, relation_max_depth=1)
    repo.delete.assert_not_awaited()
    db.commit.assert_not_awaited()
    service.invalidate_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_permanent_delete_rejects_node_with_children_before_repo_delete() -> None:
    repo = _FakeRepo(permanent_delete_result=True)
    repo.get_children = AsyncMock(return_value=[SimpleNamespace(id=99)])
    service = _FakeTreeService(repo)
    db = AsyncMock()
    cache = object()

    with pytest.raises(ValueError, match="当前节点存在下级节点"):
        await service.permanent_delete(db, 10, cache=cache)

    repo.get_children.assert_awaited_once_with(db, 10, include_inactive=True, relation_max_depth=1)
    repo.permanent_delete.assert_not_awaited()
    db.commit.assert_not_awaited()
    service.invalidate_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_rejects_when_parent_missing() -> None:
    repo = _FakeRepo(restore_result=SimpleNamespace(id=10, parent_id=5, is_deleted=False))
    repo.get_by_id = AsyncMock(
        side_effect=[
            SimpleNamespace(id=10, parent_id=5, is_deleted=True),
            None,
        ]
    )
    service = _FakeTreeService(repo)
    db = AsyncMock()
    cache = object()

    with pytest.raises(ValueError, match="父节点不存在，无法恢复当前节点"):
        await service.restore(db, 10, cache=cache)

    assert repo.get_by_id.await_args_list[0].args == (db, 10)
    assert repo.get_by_id.await_args_list[0].kwargs == {"include_deleted": True}
    assert repo.get_by_id.await_args_list[1].args == (db, 5)
    assert repo.get_by_id.await_args_list[1].kwargs == {"include_deleted": True}
    repo.restore.assert_not_awaited()
    db.commit.assert_not_awaited()
    service.invalidate_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_rejects_when_parent_is_deleted() -> None:
    repo = _FakeRepo(restore_result=SimpleNamespace(id=10, parent_id=5, is_deleted=False))
    repo.get_by_id = AsyncMock(
        side_effect=[
            SimpleNamespace(id=10, parent_id=5, is_deleted=True),
            SimpleNamespace(id=5, parent_id=None, is_deleted=True),
        ]
    )
    service = _FakeTreeService(repo)
    db = AsyncMock()
    cache = object()

    with pytest.raises(ValueError, match="父节点仍在回收站中"):
        await service.restore(db, 10, cache=cache)

    assert repo.get_by_id.await_args_list[0].args == (db, 10)
    assert repo.get_by_id.await_args_list[0].kwargs == {"include_deleted": True}
    assert repo.get_by_id.await_args_list[1].args == (db, 5)
    assert repo.get_by_id.await_args_list[1].kwargs == {"include_deleted": True}
    repo.restore.assert_not_awaited()
    db.commit.assert_not_awaited()
    service.invalidate_cache.assert_not_awaited()
