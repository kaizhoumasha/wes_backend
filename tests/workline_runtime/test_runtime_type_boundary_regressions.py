"""运行时持久化身份的 fail-closed 类型边界回归测试。"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_id", [None, "101"])
async def test_material_unit_fact_snapshot_ignores_unpersisted_identity(
    monkeypatch: pytest.MonkeyPatch, raw_id: object
) -> None:
    repository_module = import_module("src.app.runtime.orchestration.repositories.material_unit_repository")

    class _Column:
        __hash__ = object.__hash__

        def __eq__(self, _other: object) -> _Column:
            return self

    class _Query:
        def where(self, _condition: object) -> _Query:
            return self

        def limit(self, _count: int) -> _Query:
            return self

    class _MaterialUnit:
        id = _Column()
        __table__ = SimpleNamespace(c=SimpleNamespace(id=_Column()))

    monkeypatch.setattr(repository_module, "MaterialUnit", _MaterialUnit)
    monkeypatch.setattr(repository_module, "select", lambda _model: _Query())
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=raw_id)))
    )

    snapshot = await repository_module.MaterialUnitRepository().get_fact_snapshot(db, material_unit_id=101)

    assert snapshot is None
