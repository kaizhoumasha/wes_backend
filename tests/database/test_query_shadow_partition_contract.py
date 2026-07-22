from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import DateTime
from sqlalchemy.dialects import postgresql


def test_partition_plan_precreates_current_and_future_three_months_without_default() -> None:
    from src.app.runtime.system_capabilities.shadow_partitioning import build_query_shadow_partition_plan

    plan = build_query_shadow_partition_plan(datetime(2026, 11, 15, tzinfo=UTC))

    assert [partition.name for partition in plan.create] == [
        "query_shadow_comparisons_2026_11",
        "query_shadow_comparisons_2026_12",
        "query_shadow_comparisons_2027_01",
        "query_shadow_comparisons_2027_02",
    ]
    assert all("DEFAULT" not in partition.create_sql.upper() for partition in plan.create)


def test_shadow_partition_plan_uses_naive_utc_boundaries() -> None:
    from src.app.runtime.system_capabilities.shadow_partitioning import build_query_shadow_partition_plan

    plan = build_query_shadow_partition_plan(datetime(2026, 8, 1, 0, 30, tzinfo=timezone(timedelta(hours=8))))

    assert plan.create[0].starts_at == datetime(2026, 7, 1)
    assert plan.create[0].ends_at == datetime(2026, 8, 1)
    assert plan.create[0].starts_at.tzinfo is None
    assert plan.create[0].ends_at.tzinfo is None
    assert "+00" not in plan.create[0].create_sql


@pytest.mark.asyncio
async def test_partition_maintainer_uses_advisory_lock_and_lock_timeout_for_online_drop() -> None:
    from src.app.runtime.system_capabilities.shadow_partitioning import QueryShadowPartitionMaintainer

    statements: list[tuple[str, dict[str, object] | None]] = []

    class Result:
        def scalar_one(self) -> bool:
            return True

        def all(self) -> list[tuple[str]]:
            return [("query_shadow_comparisons_2026_03",), ("unrelated_table",)]

    class Db:
        async def execute(self, statement: object, params: dict[str, object] | None = None) -> Result:
            statements.append((str(statement), params))
            return Result()

    result = await QueryShadowPartitionMaintainer(lock_timeout_seconds=5).maintain(
        Db(),
        now=datetime(2026, 7, 22, tzinfo=UTC),
    )
    sql = "\n".join(statement for statement, _ in statements)

    assert result.lock_acquired is True
    assert len(result.created) == 4
    assert result.dropped == ("query_shadow_comparisons_2026_03",)
    assert "pg_try_advisory_xact_lock" in sql
    assert "SET LOCAL lock_timeout = '5s'" in sql
    assert "DROP TABLE IF EXISTS wes_runtime.query_shadow_comparisons_2026_03" in sql


def test_migration_declares_partitioned_reference_only_store_and_immutable_reports() -> None:
    root = Path(__file__).resolve().parents[2]
    migrations = sorted((root / "migrations/versions").glob("*query_shadow_readiness*.py"))
    assert len(migrations) == 1
    text = migrations[0].read_text(encoding="utf-8")

    assert "PARTITION BY RANGE (observed_at)" in text
    assert "DEFAULT" not in text
    assert "query_shadow_readiness_reports" in text
    assert "query_shadow_readiness_approvals" in text
    assert "raise_exception" in text
    assert not any(token in text for token in ("request_payload", "response_payload", "authority_snapshot"))


def test_shadow_schema_and_migration_use_timestamp_without_timezone_consistently() -> None:
    from src.app.runtime.system_capabilities.shadow_models import (
        QueryShadowComparison,
        QueryShadowReadinessApprovalRecord,
        QueryShadowReadinessReportRecord,
    )

    datetime_columns = (
        QueryShadowComparison.__table__.c.observed_at,
        QueryShadowReadinessReportRecord.__table__.c.generated_at,
        QueryShadowReadinessApprovalRecord.__table__.c.approved_at,
    )
    assert all(isinstance(column.type, DateTime) and column.type.timezone is False for column in datetime_columns)

    root = Path(__file__).resolve().parents[2]
    migration = next((root / "migrations/versions").glob("*query_shadow_readiness*.py"))
    migration_text = migration.read_text(encoding="utf-8")
    assert "TIMESTAMP WITHOUT TIME ZONE" in migration_text
    assert "TIMESTAMP WITH TIME ZONE" not in migration_text
    assert "TIMESTAMPTZ" not in migration_text
    assert "DateTime(timezone=True)" not in migration_text


@pytest.mark.asyncio
async def test_shadow_queries_bind_naive_utc_windows_and_cast_expected_as_naive_timestamp() -> None:
    from src.app.runtime.system_capabilities.shadow_repository import QueryShadowReadinessRepository

    statements: list[object] = []

    class Scalars:
        def all(self) -> list[object]:
            return []

    class Result:
        def scalars(self) -> Scalars:
            return Scalars()

    class Db:
        async def execute(self, statement: object, *_args: object, **_kwargs: object) -> Result:
            statements.append(statement)
            return Result()

    observed_from = datetime(2026, 8, 1, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    observed_until = datetime(2026, 9, 1, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    repository = QueryShadowReadinessRepository()
    await repository.list_expected(
        Db(),
        provider_profile_identity="wms.material-flow.production",
        operation_identity="wms.inventory.query_inventory@v1",
        observed_from=observed_from,
        observed_until=observed_until,
    )
    await repository.list_comparisons(
        Db(),
        provider_profile_identity="wms.material-flow.production",
        operation_identity="wms.inventory.query_inventory@v1",
        observed_from=observed_from,
        observed_until=observed_until,
    )

    compiled = [statement.compile(dialect=postgresql.dialect()) for statement in statements]
    assert "TIMESTAMP WITHOUT TIME ZONE" in str(compiled[0])
    assert "TIMESTAMP WITH TIME ZONE" not in str(compiled[0])
    for query in compiled:
        datetimes = [value for value in query.params.values() if isinstance(value, datetime)]
        assert datetimes
        assert all(value.tzinfo is None for value in datetimes)
    assert datetime(2026, 7, 31, 16, 0) in compiled[0].params.values()
    assert datetime(2026, 8, 31, 16, 0) in compiled[1].params.values()


def test_migration_precreates_execution_month_and_future_three_months_dynamically() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = next((root / "migrations/versions").glob("*query_shadow_readiness*.py"))
    text = migration.read_text(encoding="utf-8")

    assert "CURRENT_TIMESTAMP AT TIME ZONE 'UTC'" in text
    assert "FOR month_offset IN 0..3 LOOP" in text
    assert not any(f"query_shadow_comparisons_2026_{month:02d}" in text for month in range(1, 13))


def test_comparison_model_has_no_full_payload_copy_columns() -> None:
    from src.app.runtime.system_capabilities.shadow_models import QueryShadowComparison

    columns = set(QueryShadowComparison.__table__.columns.keys())

    assert {"comparison_key", "evidence_ref", "input_hash", "output_hash", "divergence_diff"} <= columns
    assert not ({"payload", "request_payload", "response_payload", "authority_snapshot"} & columns)


def test_comparison_store_does_not_declare_unused_recorded_replay_reference() -> None:
    from src.app.runtime.system_capabilities.shadow_models import QueryShadowComparison

    root = Path(__file__).resolve().parents[2]
    migration = next((root / "migrations/versions").glob("*query_shadow_readiness*.py"))

    assert "replay_ref" not in QueryShadowComparison.__table__.columns
    assert "replay_ref" not in migration.read_text(encoding="utf-8")
