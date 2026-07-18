from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
from src.core.exceptions import OptimisticLockException
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")  # type: ignore[attr-defined]

    async with engine.begin() as connection:
        await connection.run_sync(WorklineSession.__table__.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _create_session(db: AsyncSession, *, code: str) -> WorklineSession:
    session = WorklineSession(session_code=code, workline_id=8, plugin_key="plugin.version-test")
    db.add(session)
    await db.commit()
    assert session.id is not None
    assert session.version == 0
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "expected_status", "kwargs"),
    [
        (
            "persist_command_result_wait",
            SessionStatus.WAITING_DEVICE_RESULT,
            {"command_code": "CMD-1", "timeout_seconds": 30},
        ),
        (
            "persist_external_wait",
            SessionStatus.WAITING_EXTERNAL,
            {"wait_type": "RESOURCE_WAIT", "timeout_seconds": None, "context_json": {"step": "waiting"}},
        ),
        (
            "persist_completed",
            SessionStatus.COMPLETED,
            {"context_json": {"step": "completed"}},
        ),
        (
            "persist_manual_hold",
            SessionStatus.MANUAL_HOLD,
            {"failure_domain": "MATERIAL", "failure_code": "BLOCKED", "failure_message": "manual review"},
        ),
        ("persist_cancelled", SessionStatus.CANCELLED, {}),
    ],
)
async def test_bulk_session_fact_updates_increment_version_once(
    session_factory: async_sessionmaker[AsyncSession],
    method_name: str,
    expected_status: SessionStatus,
    kwargs: dict[str, Any],
) -> None:
    async with session_factory() as db:
        session = await _create_session(db, code=f"SESSION-{method_name}")
        repository = WorklineSessionRepository()

        await getattr(repository, method_name)(
            db,
            session_id=int(session.id),
            occurred_at=timezone.now_for_db(),
            **kwargs,
        )
        await db.commit()

        persisted_status, persisted_version = (
            await db.execute(
                select(WorklineSession.status, WorklineSession.version).where(WorklineSession.id == session.id)
            )
        ).one()
        assert persisted_status == expected_status
        assert persisted_version == 1


@pytest.mark.asyncio
async def test_bulk_session_update_rejects_stale_expected_version_without_writing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        session = await _create_session(db, code="SESSION-STALE-BULK-CAS")
        session_id = int(session.id)
        repository = WorklineSessionRepository()
        occurred_at = timezone.now_for_db()
        await repository.persist_manual_hold(
            db,
            session_id=session_id,
            occurred_at=occurred_at,
            failure_domain="MATERIAL",
            failure_code="FIRST_HOLD",
            failure_message="first",
            expected_version=0,
        )
        await db.commit()

        with pytest.raises(OptimisticLockException):
            await repository.persist_manual_hold(
                db,
                session_id=session_id,
                occurred_at=occurred_at,
                failure_domain="MATERIAL",
                failure_code="STALE_HOLD",
                failure_message="must not persist",
                expected_version=0,
            )
        await db.rollback()

        persisted_version, persisted_failure_code = (
            await db.execute(
                select(WorklineSession.version, WorklineSession.failure_code).where(WorklineSession.id == session_id)
            )
        ).one()
        assert persisted_version == 1
        assert persisted_failure_code == "FIRST_HOLD"


@pytest.mark.asyncio
async def test_bulk_session_update_does_not_double_increment_dirty_attached_entity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        session = await _create_session(db, code="SESSION-DIRTY-ATTACHED")
        session.status = SessionStatus.MANUAL_HOLD
        session.failure_domain = "MATERIAL"
        session.failure_code = "DIRTY_HOLD"
        session.failure_message = "dirty entity"

        await WorklineSessionRepository().persist_manual_hold(
            db,
            session_id=int(session.id),
            occurred_at=timezone.now_for_db(),
            failure_domain=session.failure_domain,
            failure_code=session.failure_code,
            failure_message=session.failure_message,
        )
        await db.commit()

        persisted_version = await db.scalar(select(WorklineSession.version).where(WorklineSession.id == session.id))
        assert persisted_version == 1
        assert session.version == 1
