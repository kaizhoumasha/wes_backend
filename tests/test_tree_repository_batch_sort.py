from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from src.core.mixins import TreeMixin
from src.database.tree_repository import TreeRepository


class BatchSortTreeNode(TreeMixin, SQLModel, table=True):
    __tablename__ = "test_batch_sort_tree_nodes"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _ExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarResult(self._items)


@pytest.mark.asyncio
async def test_batch_sort_prefetches_external_parent_once() -> None:
    repo = TreeRepository[BatchSortTreeNode](BatchSortTreeNode)
    repo.get_descendants = AsyncMock(return_value=[])
    repo._check_and_update_has_children = AsyncMock()

    node1 = SimpleNamespace(id=1, parent_id=None, level=1, tree_path="/1/", sort_order=0, has_children=False)
    node2 = SimpleNamespace(id=2, parent_id=None, level=1, tree_path="/2/", sort_order=0, has_children=False)
    external_parent = SimpleNamespace(id=99, parent_id=None, level=1, tree_path="/99/", sort_order=0, has_children=False)

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(
        side_effect=[
            _ExecuteResult([node1, node2]),
            _ExecuteResult([external_parent]),
        ]
    )
    db.flush = AsyncMock()

    await repo.batch_sort(
        db,
        [
            {"id": 1, "parent_id": 99, "sort_order": 10},
            {"id": 2, "parent_id": 99, "sort_order": 20},
        ],
    )

    assert db.execute.await_count == 2
    repo.get_descendants.assert_any_await(db, 1)
    repo.get_descendants.assert_any_await(db, 2)
    assert external_parent.has_children is True
    assert node1.tree_path == "/99/1/"
    assert node2.tree_path == "/99/2/"
