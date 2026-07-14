"""reset runtime data 在隔离 PostgreSQL 临时库上的运维验收。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from scripts.data.reset_runtime_data import reset_runtime_data
from tests.support.runtime_inbox_postgresql import connect, run_alembic, temporary_database


def _session_factory(database_url: str) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _seed_master_and_runtime(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO wes_biz.resource_bin_types "
            "(created_at, bin_type_code, bin_type_name, active, metadata_json) "
            "VALUES (CURRENT_TIMESTAMP, 'RESET-MASTER', 'Reset master survives', true, '{}'::json)"
        )
    )
    await session.execute(
        text(
            "INSERT INTO wes_runtime.runtime_inbox ("
            "provider_code, event_type, received_at, failed_at, status, attempt_count, max_retries, "
            "next_retry_at, lease_until, last_error_code, last_error_message"
            ") VALUES ("
            "'reset-test', 'AUDIT_ONLY', 1783699200123, 1783699200123, 'DEAD_LETTER', 1, 5, "
            "1783699200123, 1783699200123, 'PRE_CUTOVER_AUDIT_ONLY', 'reset integration audit only'"
            ")"
        )
    )
    await session.commit()


@pytest.mark.integration
def test_reset_dry_run_and_apply_preserve_master_data() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            session_factory, engine = _session_factory(database_url)
            try:
                async with session_factory() as session:
                    await _seed_master_and_runtime(session)
                    dry_summary = await reset_runtime_data(
                        session,
                        apply=False,
                        include_audit_logs=False,
                        reset_mocks=False,
                    )
                    assert any(
                        row["table"] == "wes_runtime.runtime_inbox" and row["rows_before"] == 1
                        for row in dry_summary.truncated
                    )
                    assert await session.scalar(text("SELECT count(*) FROM wes_runtime.runtime_inbox")) == 1

                    await reset_runtime_data(
                        session,
                        apply=True,
                        include_audit_logs=False,
                        reset_mocks=False,
                    )
                    assert await session.scalar(text("SELECT count(*) FROM wes_runtime.runtime_inbox")) == 0
                    assert (
                        await session.scalar(
                            text("SELECT count(*) FROM wes_biz.resource_bin_types WHERE bin_type_code = 'RESET-MASTER'")
                        )
                        == 1
                    )
            finally:
                await engine.dispose()

            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT count(*) FROM wes_runtime.runtime_inbox") == 0
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("failure_mode", ["missing", "schema-mismatch"])
def test_reset_rejects_missing_or_wrong_schema_without_mutation(failure_mode: str) -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            connection = await connect(database)
            try:
                await connection.execute("ALTER TABLE wes_runtime.runtime_inbox RENAME TO runtime_inbox_saved")
                if failure_mode == "schema-mismatch":
                    await connection.execute("CREATE TABLE wes_biz.runtime_inbox (id bigint primary key)")
                await connection.execute(
                    "INSERT INTO wes_biz.resource_bin_types "
                    "(created_at, bin_type_code, bin_type_name, active, metadata_json) "
                    "VALUES (CURRENT_TIMESTAMP, 'RESET-GUARD', 'Reset guard survives', true, '{}'::json)"
                )
            finally:
                await connection.close()

            session_factory, engine = _session_factory(database_url)
            try:
                async with session_factory() as session:
                    expected = "schema 不匹配" if failure_mode == "schema-mismatch" else "目标表不存在"
                    with pytest.raises(RuntimeError, match=expected):
                        await reset_runtime_data(
                            session,
                            apply=True,
                            include_audit_logs=False,
                            reset_mocks=False,
                        )
                    await session.rollback()
                    assert (
                        await session.scalar(
                            text("SELECT count(*) FROM wes_biz.resource_bin_types WHERE bin_type_code = 'RESET-GUARD'")
                        )
                        == 1
                    )
                    assert await session.scalar(text("SELECT count(*) FROM wes_runtime.runtime_inbox_saved")) == 0
            finally:
                await engine.dispose()

    asyncio.run(scenario())
