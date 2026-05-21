from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _Scalar:
    def __init__(self, value: int | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> int | None:
        return self.value


class _Db:
    def __init__(self) -> None:
        self.execute = AsyncMock(side_effect=[None, _Scalar(8)])
        self.add = lambda _item: None

    def get_bind(self) -> object:
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))


@pytest.mark.asyncio
async def test_allocate_timeline_seq_no_uses_transaction_advisory_lock_before_max_query() -> None:
    from src.app.workline.services.timeline_sequence_service import allocate_timeline_seq_no

    db = _Db()

    seq_no = await allocate_timeline_seq_no(db, session_id=42)

    assert seq_no == 9
    assert db.execute.await_count == 2
    first_statement = str(db.execute.await_args_list[0].args[0])
    assert "pg_advisory_xact_lock" in first_statement
