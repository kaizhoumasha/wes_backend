from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.admin.repositories.menu_repository import MenuRepository


@pytest.mark.asyncio
async def test_get_menus_by_user_superuser_query_not_where_false() -> None:
    repo = MenuRepository()
    db = AsyncMock()

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = SimpleNamespace(is_superuser=True)

    menu_scalars = MagicMock()
    menu_scalars.all.return_value = []
    menu_result = MagicMock()
    menu_result.scalars.return_value = menu_scalars

    statements = []

    async def execute_side_effect(statement):
        statements.append(statement)
        return user_result if len(statements) == 1 else menu_result

    db.execute.side_effect = execute_side_effect

    menus = await repo.get_menus_by_user(db, user_id=1)

    assert menus == []
    assert len(statements) == 2

    compiled_sql = str(statements[1].compile(compile_kwargs={"literal_binds": True})).lower()
    assert "is_deleted" in compiled_sql
    assert "where false" not in compiled_sql


@dataclass
class _MenuStub:
    id: int
    is_deleted: bool = False


@pytest.mark.asyncio
async def test_get_menus_by_user_regular_user_deduplicates_unhashable_menu_objects() -> None:
    repo = MenuRepository()
    db = AsyncMock()

    shared_menu = _MenuStub(id=101, is_deleted=False)
    unique_menu = _MenuStub(id=202, is_deleted=False)
    deleted_menu = _MenuStub(id=303, is_deleted=True)

    user = SimpleNamespace(
        is_superuser=False,
        roles=[
            SimpleNamespace(is_deleted=False, menus=[shared_menu, unique_menu, deleted_menu]),
            SimpleNamespace(is_deleted=False, menus=[shared_menu]),
            SimpleNamespace(is_deleted=True, menus=[_MenuStub(id=404, is_deleted=False)]),
        ],
    )

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    db.execute.return_value = user_result

    menus = await repo.get_menus_by_user(db, user_id=1)

    assert [menu.id for menu in menus] == [101, 202]
