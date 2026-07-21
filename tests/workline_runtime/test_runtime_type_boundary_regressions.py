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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_id", "inbox_id", "field_name"),
    [(None, 91, "session.id"), (41, None, "inbox.id")],
)
async def test_plugin_attempt_persistence_rejects_unpersisted_authoritative_rows(
    session_id: object, inbox_id: object, field_name: str
) -> None:
    from src.app.runtime.orchestration.repositories.plugin_attempt_repository import (
        AuthoritativePluginAttempt,
        PluginAttemptRepository,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet

    with pytest.raises(TypeError, match=field_name):
        await PluginAttemptRepository().persist_locked_attempt(
            object(),  # type: ignore[arg-type]
            locked=AuthoritativePluginAttempt(
                inbox=SimpleNamespace(id=inbox_id), session=SimpleNamespace(id=session_id)
            ),
            workline_id=8,
            trace_id="trace-type-boundary",
            snapshot=AttemptSnapshot(processor_token="lease-1", session_version=1, plugin_state_version=1),
            write_set=AttemptWriteSet(evidence=(), next_state={}, intents=()),
        )


@pytest.mark.parametrize("raw_id", [None, "101"])
def test_material_unit_write_rejects_unpersisted_mutation_result(raw_id: object) -> None:
    from src.app.runtime.system_capabilities.material_flow.material_unit_write.handler import (
        _persisted_material_unit_id,
    )

    with pytest.raises(TypeError, match="unpersisted material unit"):
        _persisted_material_unit_id(SimpleNamespace(id=raw_id))
