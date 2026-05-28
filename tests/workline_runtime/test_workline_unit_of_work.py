from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.unit_of_work import WorklineUnitOfWork

pytestmark = pytest.mark.asyncio


def _db():
    return SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
        close=AsyncMock(),
    )


async def test_external_session_is_not_closed_by_uow() -> None:
    db = _db()

    async with WorklineUnitOfWork(db=db) as uow:
        assert uow.session is db
        await uow.commit()

    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    db.close.assert_not_awaited()


async def test_internal_session_comes_from_session_factory() -> None:
    db = _db()

    @asynccontextmanager
    async def session_factory():
        yield db

    async with WorklineUnitOfWork(session_factory=session_factory) as uow:
        assert uow.session is db
        await uow.commit()

    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


async def test_exception_rolls_back_uncommitted_work() -> None:
    db = _db()

    with pytest.raises(RuntimeError):
        async with WorklineUnitOfWork(db=db):
            raise RuntimeError("boom")

    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


async def test_checkpoint_commits_and_allows_later_rollback() -> None:
    db = _db()

    with pytest.raises(RuntimeError):
        async with WorklineUnitOfWork(db=db) as uow:
            await uow.checkpoint()
            raise RuntimeError("after checkpoint")

    db.commit.assert_awaited_once()
    db.rollback.assert_awaited_once()
