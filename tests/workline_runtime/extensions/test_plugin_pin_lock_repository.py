"""WorkLine 插件 pin advisory 锁的 Repository 合同。"""

from types import SimpleNamespace

import pytest

from src.app.workline.repositories.workline_repository import WorkLineRepository


class RecordingDb:
    def __init__(self, dialect: str) -> None:
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_bind(self) -> object:
        return self.bind

    async def execute(self, statement: object, params: dict[str, object]) -> None:
        self.calls.append((str(statement), params))


@pytest.mark.asyncio
async def test_postgresql_plugin_pin_locks_share_one_stable_namespace_key() -> None:
    repository = WorkLineRepository(runtime_inbox_query=SimpleNamespace())
    db = RecordingDb("postgresql")

    await repository.acquire_plugin_pin_shared(db, 9007199254740993)
    await repository.acquire_plugin_pin_exclusive(db, 9007199254740993)

    assert db.calls == [
        (
            "SELECT pg_advisory_xact_lock_shared(hashtextextended(:lock_key, 0))",
            {"lock_key": "workline-plugin-pin:v1:9007199254740993"},
        ),
        (
            "SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))",
            {"lock_key": "workline-plugin-pin:v1:9007199254740993"},
        ),
    ]


@pytest.mark.asyncio
async def test_non_postgresql_plugin_pin_locks_are_explicit_no_ops() -> None:
    repository = WorkLineRepository(runtime_inbox_query=SimpleNamespace())
    db = RecordingDb("sqlite")

    await repository.acquire_plugin_pin_shared(db, 7)
    await repository.acquire_plugin_pin_exclusive(db, 7)

    assert db.calls == []
