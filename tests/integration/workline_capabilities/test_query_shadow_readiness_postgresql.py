"""QUERY shadow/readiness 最小真实 PostgreSQL 分区与不可变性验证。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.runtime.system_capabilities.shadow_partitioning import QueryShadowPartitionMaintainer
from src.app.runtime.system_capabilities.shadow_readiness import (
    BoundedQueryShadowEvaluator,
    ShadowDecision,
    ShadowVersionSet,
    build_query_shadow_expected,
)
from src.app.runtime.system_capabilities.shadow_repository import (
    QueryShadowComparisonRepository,
    QueryShadowPartitionMissing,
)
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database


def _task_payload(*, attempt_id: str, observed_at: datetime) -> dict[str, object]:
    expected = build_query_shadow_expected(
        attempt_id=attempt_id,
        capability_key="wms.inventory.query_inventory",
        provider_profile_identity="wms.material-flow.production",
        operation_identity="wms.inventory.query_inventory@v1",
        versions=ShadowVersionSet(
            legacy_policy_version="policy.v1",
            candidate_policy_version="policy.v2",
            legacy_contract_version="inventory.v1",
            candidate_contract_version="inventory.v2",
            normalization_version="normalization.v1",
            evaluator_version="evaluator.v1",
        ),
        observed_at=observed_at,
        input_hash="a" * 64,
        output_hash="b" * 64,
    )
    decision = ShadowDecision(action="ADMIT", reason="WMS_ADMITTED", error_class="NONE")
    return (
        BoundedQueryShadowEvaluator()
        .compare(
            expected=expected,
            legacy_decision=decision,
            candidate_decision=decision,
            legacy_policy_duration_ns=1_000,
            candidate_policy_duration_ns=1_100,
            query_end_to_end_duration_ms=12.0,
        )
        .task_payload()
    )


@pytest.mark.asyncio
async def test_cross_month_store_missing_partition_retention_drop_and_immutable_report() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        repository = QueryShadowComparisonRepository()

        async def append(payload: dict[str, object]) -> None:
            async with session_factory() as db:
                await repository.append_from_task(db, payload=payload)
                await db.commit()

        await asyncio.gather(
            append(_task_payload(attempt_id="july", observed_at=datetime(2026, 7, 31, 23, 59, tzinfo=UTC))),
            append(_task_payload(attempt_id="august", observed_at=datetime(2026, 8, 1, tzinfo=UTC))),
        )
        async with session_factory() as db:
            assert (await db.scalar(text("SELECT count(*) FROM wes_runtime.query_shadow_comparisons"))) == 2
            with pytest.raises(QueryShadowPartitionMissing):
                await repository.append_from_task(
                    db,
                    payload=_task_payload(
                        attempt_id="missing",
                        observed_at=datetime(2026, 12, 1, tzinfo=UTC),
                    ),
                )
            await db.rollback()
            await db.execute(
                text(
                    "INSERT INTO wes_runtime.query_shadow_readiness_reports "
                    "(report_id, generated_at, provider_profile_identity, operation_identity, verdict, report_json) "
                    "VALUES (:report_id, now(), 'wms.material-flow.production', "
                    "'wms.inventory.query_inventory@v1', 'READY', '{}'::jsonb)"
                ),
                {"report_id": "f" * 64},
            )
            await db.commit()
            with pytest.raises(DBAPIError):
                await db.execute(
                    text(
                        "UPDATE wes_runtime.query_shadow_readiness_reports "
                        "SET verdict = 'NOT_READY' WHERE report_id = :report_id"
                    ),
                    {"report_id": "f" * 64},
                )
                await db.commit()
            await db.rollback()

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE wes_runtime.query_shadow_comparisons_2026_03 "
                    "PARTITION OF wes_runtime.query_shadow_comparisons "
                    "FOR VALUES FROM ('2026-03-01 00:00:00+00') TO ('2026-04-01 00:00:00+00')"
                )
            )
        lock_connection = await engine.connect()
        lock_transaction = await lock_connection.begin()
        await lock_connection.execute(
            text("LOCK TABLE wes_runtime.query_shadow_comparisons_2026_03 IN ACCESS SHARE MODE")
        )
        try:
            async with session_factory() as db:
                with pytest.raises(DBAPIError):
                    await QueryShadowPartitionMaintainer(lock_timeout_seconds=1).maintain(
                        db,
                        now=datetime(2026, 7, 22, tzinfo=UTC),
                    )
                    await db.commit()
                await db.rollback()
        finally:
            await lock_transaction.rollback()
            await lock_connection.close()

        async with session_factory() as db:
            result = await QueryShadowPartitionMaintainer(lock_timeout_seconds=1).maintain(
                db,
                now=datetime(2026, 7, 22, tzinfo=UTC),
            )
            await db.commit()
            assert result.dropped == ("query_shadow_comparisons_2026_03",)
            assert await db.scalar(text("SELECT to_regclass('wes_runtime.query_shadow_comparisons_2026_03')")) is None
        await engine.dispose()
