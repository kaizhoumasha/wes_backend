import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from src.core.mixins import TreeMixin
from src.database.tree_repository import TreeRepository


class TreeNode(TreeMixin, SQLModel, table=True):
    __tablename__ = "test_tree_nodes"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)


class TreeNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None
    tree_path: str
    level: int
    sort_order: int


@pytest.mark.asyncio
async def test_move_node_keeps_expected_levels(db_session: AsyncSession) -> None:
    repo = TreeRepository[TreeNode](TreeNode)

    root_a = await repo.create(db_session, {"name": "Root A"})
    root_b = await repo.create(db_session, {"name": "Root B"})
    child = await repo.create(db_session, {"name": "Child", "parent_id": root_a.id})
    grandchild = await repo.create(db_session, {"name": "Grandchild", "parent_id": child.id})
    await db_session.commit()

    moved = await repo.move_node(db_session, child.id, root_b.id)  # type: ignore[arg-type]
    await db_session.commit()

    reloaded_child = await repo.get_by_id(db_session, child.id)  # type: ignore[arg-type]
    reloaded_grandchild = await repo.get_by_id(db_session, grandchild.id)  # type: ignore[arg-type]

    assert moved is not None
    assert reloaded_child is not None
    assert reloaded_grandchild is not None
    assert reloaded_child.level == 2
    assert reloaded_child.tree_path == f"/{root_b.id}/{child.id}/"
    assert reloaded_grandchild.level == 3
    assert reloaded_grandchild.tree_path == f"/{root_b.id}/{child.id}/{grandchild.id}/"
    assert getattr(moved, "_moved_descendant_ids", None) == [grandchild.id]


@pytest.mark.asyncio
async def test_batch_sort_returns_metadata_for_affected_parents_and_descendants(db_session: AsyncSession) -> None:
    repo = TreeRepository[TreeNode](TreeNode)

    root_a = await repo.create(db_session, {"name": "Root A"})
    root_b = await repo.create(db_session, {"name": "Root B"})
    child = await repo.create(db_session, {"name": "Child", "parent_id": root_a.id})
    grandchild = await repo.create(db_session, {"name": "Grandchild", "parent_id": child.id})
    await db_session.commit()

    metadata = await repo.batch_sort(
        db_session,
        [
            {"id": child.id, "parent_id": root_b.id, "sort_order": 0},
        ],
    )

    assert getattr(metadata, "moved_descendant_ids", None) == [grandchild.id]
    assert set(getattr(metadata, "affected_parent_ids", [])) == {root_a.id, root_b.id}


@pytest.mark.asyncio
async def test_batch_sort_resolves_paths_against_moved_parent_regardless_of_item_order(
    db_session: AsyncSession,
) -> None:
    repo = TreeRepository[TreeNode](TreeNode)

    node_a = await repo.create(db_session, {"name": "A"})
    node_b = await repo.create(db_session, {"name": "B"})
    node_c = await repo.create(db_session, {"name": "C"})
    await db_session.commit()

    await repo.batch_sort(
        db_session,
        [
            {"id": node_c.id, "parent_id": node_a.id, "sort_order": 0},
            {"id": node_a.id, "parent_id": node_b.id, "sort_order": 0},
        ],
    )
    await db_session.commit()

    reloaded_a = await repo.get_by_id(db_session, node_a.id)  # type: ignore[arg-type]
    reloaded_c = await repo.get_by_id(db_session, node_c.id)  # type: ignore[arg-type]

    assert reloaded_a is not None
    assert reloaded_c is not None
    assert reloaded_a.tree_path == f"/{node_b.id}/{node_a.id}/"
    assert reloaded_a.level == 2
    assert reloaded_c.tree_path == f"/{node_b.id}/{node_a.id}/{node_c.id}/"
    assert reloaded_c.level == 3


@pytest.mark.asyncio
async def test_batch_sort_rejects_cycle_created_within_same_request(db_session: AsyncSession) -> None:
    repo = TreeRepository[TreeNode](TreeNode)

    node_a = await repo.create(db_session, {"name": "A"})
    node_b = await repo.create(db_session, {"name": "B"})
    await db_session.commit()

    with pytest.raises(ValueError, match="循环"):
        await repo.batch_sort(
            db_session,
            [
                {"id": node_a.id, "parent_id": node_b.id, "sort_order": 0},
                {"id": node_b.id, "parent_id": node_a.id, "sort_order": 0},
            ],
        )


@pytest.mark.asyncio
async def test_get_descendants_with_schema_loads_items_without_pk_attr_errors(db_session: AsyncSession) -> None:
    repo = TreeRepository[TreeNode](TreeNode)

    root = await repo.create(db_session, {"name": "Root"})
    child = await repo.create(db_session, {"name": "Child", "parent_id": root.id})
    await db_session.commit()

    items = await repo.get_descendants(
        db_session,
        root.id,  # type: ignore[arg-type]
        schema=TreeNodeResponse,
        relation_max_depth=1,
    )

    assert [item.id for item in items] == [root.id, child.id]
