"""RuntimeInbox PostgreSQL heavy harness 的真实清理 smoke。"""

from __future__ import annotations

import asyncio

import pytest

from tests.support.runtime_inbox_postgresql import HeavyHarnessError, preflight, temporary_database


@pytest.mark.integration
def test_scenario_error_forces_temporary_database_cleanup() -> None:
    async def scenario() -> None:
        created_database: str | None = None
        with pytest.raises(HeavyHarnessError) as exc_info:
            async with temporary_database() as (database, _database_url):
                created_database = database
                raise RuntimeError("intentional scenario failure")

        assert exc_info.value.code == "scenario"
        assert created_database is not None
        checked = await preflight()
        try:
            assert not await checked.admin.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)",
                created_database,
            )
        finally:
            await checked.close()

    asyncio.run(scenario())
