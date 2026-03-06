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
