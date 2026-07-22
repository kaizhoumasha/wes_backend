"""QUERY shadow/readiness 最小真实 PostgreSQL 分区与不可变性验证。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.runtime.orchestration.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.app.runtime.system_capabilities.evidence import QueryEvidence
from src.app.runtime.system_capabilities.shadow_partitioning import QueryShadowPartitionMaintainer
from src.app.runtime.system_capabilities.shadow_readiness import (
    BoundedQueryShadowEvaluator,
    QueryShadowExpected,
    QueryShadowReadinessPolicy,
    ReadinessVerdict,
    ShadowDecision,
    ShadowVersionSet,
    build_query_shadow_expected,
    build_query_shadow_readiness_report,
)
from src.app.runtime.system_capabilities.shadow_repository import (
    QueryShadowComparisonRepository,
    QueryShadowPartitionMissing,
    QueryShadowReadinessRepository,
)
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database
from tests.support.runtime_inbox_processing_postgresql import seed_scan_flow


def _month_start(value: datetime) -> datetime:
    utc = value.astimezone(UTC)
    return datetime(utc.year, utc.month, 1, tzinfo=UTC)


def _shift_month(value: datetime, offset: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + offset
    return datetime(month_index // 12, month_index % 12 + 1, 1, tzinfo=UTC)


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
        current_month = _month_start(datetime.now(UTC))
        next_month = _shift_month(current_month, 1)
        missing_month = _shift_month(current_month, 4)
        expired_month = _shift_month(current_month, -4)
        expired_partition_name = f"query_shadow_comparisons_{expired_month:%Y_%m}"

        async def append(payload: dict[str, object]) -> None:
            async with session_factory() as db:
                await repository.append_from_task(db, payload=payload)
                await db.commit()

        await asyncio.gather(
            append(_task_payload(attempt_id="current", observed_at=next_month - timedelta(minutes=1))),
            append(_task_payload(attempt_id="next", observed_at=next_month)),
        )
        async with session_factory() as db:
            assert (await db.scalar(text("SELECT count(*) FROM wes_runtime.query_shadow_comparisons"))) == 2
            with pytest.raises(QueryShadowPartitionMissing):
                await repository.append_from_task(
                    db,
                    payload=_task_payload(
                        attempt_id="missing",
                        observed_at=missing_month,
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
                    f"CREATE TABLE wes_runtime.{expired_partition_name} "
                    "PARTITION OF wes_runtime.query_shadow_comparisons "
                    f"FOR VALUES FROM ('{expired_month:%Y-%m-%d} 00:00:00+00') "
                    f"TO ('{_shift_month(expired_month, 1):%Y-%m-%d} 00:00:00+00')"
                )
            )
        lock_connection = await engine.connect()
        lock_transaction = await lock_connection.begin()
        await lock_connection.execute(text(f"LOCK TABLE wes_runtime.{expired_partition_name} IN ACCESS SHARE MODE"))
        try:
            async with session_factory() as db:
                with pytest.raises(DBAPIError):
                    await QueryShadowPartitionMaintainer(lock_timeout_seconds=1).maintain(
                        db,
                        now=datetime.now(UTC),
                    )
                    await db.commit()
                await db.rollback()
        finally:
            await lock_transaction.rollback()
            await lock_connection.close()

        async with session_factory() as db:
            result = await QueryShadowPartitionMaintainer(lock_timeout_seconds=1).maintain(
                db,
                now=datetime.now(UTC),
            )
            await db.commit()
            assert result.dropped == (expired_partition_name,)
            assert await db.scalar(text(f"SELECT to_regclass('wes_runtime.{expired_partition_name}')")) is None
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_exact_duplicate_is_idempotent_but_divergent_duplicate_is_marked_conflict() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        repository = QueryShadowComparisonRepository()
        current_month = _month_start(datetime.now(UTC))
        next_month = _shift_month(current_month, 1)
        observed_at = current_month + timedelta(days=1)

        async def append(payload: dict[str, object]) -> None:
            async with session_factory() as db:
                await repository.append_from_task(db, payload=payload)
                await db.commit()

        exact = _task_payload(attempt_id="exact-duplicate", observed_at=observed_at)
        await asyncio.gather(append(exact), append(exact))

        conflict = _task_payload(attempt_id="conflicting-duplicate", observed_at=observed_at)
        divergent = {
            **conflict,
            "candidate_decision": {"action": "HOLD", "reason": "WMS_ADMITTED", "error_class": "NONE"},
            "difference_class": "ACTION_MISMATCH",
            "divergence_diff": {"action": ["ADMIT", "HOLD"]},
        }
        await asyncio.gather(append(conflict), append(divergent))

        hash_conflict = _task_payload(attempt_id="hash-conflicting-duplicate", observed_at=observed_at)
        different_hash = {**hash_conflict, "input_hash": "f" * 64}
        await asyncio.gather(append(hash_conflict), append(different_hash))

        async with session_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT comparison_key, comparison_status "
                        "FROM wes_runtime.query_shadow_comparisons "
                        "WHERE comparison_key IN (:exact_key, :conflict_key, :hash_conflict_key)"
                    ),
                    {
                        "exact_key": exact["comparison_key"],
                        "conflict_key": conflict["comparison_key"],
                        "hash_conflict_key": hash_conflict["comparison_key"],
                    },
                )
            ).all()
            comparisons = await QueryShadowReadinessRepository().list_comparisons(
                db,
                provider_profile_identity=str(conflict["provider_profile_identity"]),
                operation_identity=str(conflict["operation_identity"]),
                observed_from=current_month,
                observed_until=next_month,
            )

        expected = QueryShadowExpected(
            shadow_eligible=True,
            comparison_key=str(conflict["comparison_key"]),
            provider_profile_identity=str(conflict["provider_profile_identity"]),
            operation_identity=str(conflict["operation_identity"]),
            versions=ShadowVersionSet.model_validate(conflict["versions"]),
            observed_at=observed_at,
            evidence_ref=str(conflict["evidence_ref"]),
            input_hash=str(conflict["input_hash"]),
            output_hash=str(conflict["output_hash"]),
        )
        report = build_query_shadow_readiness_report(
            provider_profile_identity=expected.provider_profile_identity,
            operation_identity=expected.operation_identity,
            expected_samples=[expected],
            comparisons=comparisons,
            generated_at=observed_at + timedelta(days=1),
            policy=QueryShadowReadinessPolicy(min_window_days=0, min_eligible_samples=1),
        )

        assert sorted(rows) == sorted(
            [
                (exact["comparison_key"], "STORED"),
                (conflict["comparison_key"], "CONFLICT"),
                (hash_conflict["comparison_key"], "CONFLICT"),
            ]
        )
        assert report.verdict is ReadinessVerdict.INVALID
        assert "COMPARISON_CONFLICT" in report.reset_reasons
        await engine.dispose()


@pytest.mark.asyncio
async def test_month_end_expected_committed_next_month_uses_observed_time_for_readiness_window() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        current_month = _month_start(datetime.now(UTC))
        next_month = _shift_month(current_month, 1)
        current_payload = _task_payload(
            attempt_id="month-end-observed",
            observed_at=next_month - timedelta(minutes=1),
        )
        next_payload = _task_payload(
            attempt_id="next-month-observed",
            observed_at=next_month,
        )

        def expected_from(payload: dict[str, object]) -> QueryShadowExpected:
            return QueryShadowExpected(
                shadow_eligible=True,
                comparison_key=str(payload["comparison_key"]),
                provider_profile_identity=str(payload["provider_profile_identity"]),
                operation_identity=str(payload["operation_identity"]),
                versions=ShadowVersionSet.model_validate(payload["versions"]),
                observed_at=datetime.fromisoformat(str(payload["observed_at"])),
                evidence_ref=str(payload["evidence_ref"]),
                input_hash=str(payload["input_hash"]),
                output_hash=str(payload["output_hash"]),
            )

        current_expected = expected_from(current_payload)
        next_expected = expected_from(next_payload)

        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            timeline_commit_time = (next_month + timedelta(minutes=5)).replace(tzinfo=None)
            evidence_rows: list[WorklineTimeline] = []
            for seq_no, expected in ((901, current_expected), (902, next_expected)):
                evidence = QueryEvidence(
                    capability_key="wms.inventory.query_inventory",
                    contract_version="v1",
                    input_hash=expected.input_hash,
                    output_hash=expected.output_hash,
                    authority="WMS",
                    source="material-flow",
                    evidence_at=expected.observed_at,
                    source_version=f"inventory-{seq_no}",
                    admission_snapshot={"profile": expected.provider_profile_identity},
                    summary={"outcome": {"kind": "success"}},
                    shadow_expected=expected,
                )
                evidence_rows.append(
                    WorklineTimeline(
                        session_id=seeded.session_id,
                        workline_id=seeded.workline_id,
                        trace_id=seeded.trace_id,
                        seq_no=seq_no,
                        occurred_at=timeline_commit_time,
                        stage=TimelineStage.DECISION,
                        action_type=TimelineActionType.DECISION_MADE,
                        actor_type=TimelineActorType.ORCHESTRATOR,
                        actor_code="system-capability-gateway",
                        status=TimelineStatus.SUCCESS,
                        message="System Capability QUERY evidence",
                        payload_json={
                            "record_type": "SYSTEM_CAPABILITY_EVIDENCE",
                            "evidence": evidence.payload(),
                        },
                        related_inbox_id=seeded.inbox_id,
                    )
                )
            db.add_all(evidence_rows)
            await db.commit()

            comparison_repository = QueryShadowComparisonRepository()
            await comparison_repository.append_from_task(db, payload=current_payload)
            await comparison_repository.append_from_task(db, payload=next_payload)
            await db.commit()

            readiness_repository = QueryShadowReadinessRepository()
            expected_samples = await readiness_repository.list_expected(
                db,
                provider_profile_identity=current_expected.provider_profile_identity,
                operation_identity=current_expected.operation_identity,
                observed_from=current_month,
                observed_until=next_month,
            )
            comparisons = await readiness_repository.list_comparisons(
                db,
                provider_profile_identity=current_expected.provider_profile_identity,
                operation_identity=current_expected.operation_identity,
                observed_from=current_month,
                observed_until=next_month,
            )

        report = build_query_shadow_readiness_report(
            provider_profile_identity=current_expected.provider_profile_identity,
            operation_identity=current_expected.operation_identity,
            expected_samples=expected_samples,
            comparisons=comparisons,
            generated_at=next_month + timedelta(minutes=10),
            policy=QueryShadowReadinessPolicy(min_window_days=0, min_eligible_samples=1),
        )

        assert expected_samples == [current_expected]
        assert [item.expected for item in comparisons] == [current_expected]
        assert report.verdict is ReadinessVerdict.READY
        assert report.evidence_refs == (current_expected.evidence_ref,)
        await engine.dispose()
