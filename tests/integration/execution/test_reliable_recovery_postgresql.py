"""真实 PostgreSQL recovery boundary and candidate-query gates."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_tx1_commit_survives_tx2_crash_and_replay(integration_session_factory) -> None:
    """A Tx2 failure must not roll back the already durable Tx1 fact."""

    table_name = f"public.recovery_boundary_{uuid4().hex}"
    async with integration_session_factory() as db:
        await db.execute(
            text(
                f"""
                CREATE TABLE {table_name} (
                    fact_id text PRIMARY KEY,
                    fact_status text NOT NULL,
                    projection_applied boolean NOT NULL DEFAULT false
                )
                """
            )
        )
        await db.commit()
        try:
            async with db.begin():
                await db.execute(
                    text(f"INSERT INTO {table_name} (fact_id, fact_status) VALUES ('fact-1', 'FACT_COMMITTED')")
                )

            tx2 = await db.begin()
            await db.execute(text(f"UPDATE {table_name} SET projection_applied = true WHERE fact_id = 'fact-1'"))
            await tx2.rollback()

            durable = await db.scalar(
                text(f"SELECT (fact_status, projection_applied) FROM {table_name} WHERE fact_id = 'fact-1'")
            )
            assert durable == ("FACT_COMMITTED", False)
            await db.rollback()

            async with db.begin():
                await db.execute(
                    text(
                        f"UPDATE {table_name} SET projection_applied = true "
                        "WHERE fact_status = 'FACT_COMMITTED' AND NOT projection_applied"
                    )
                )
            assert (
                await db.scalar(text(f"SELECT projection_applied FROM {table_name} WHERE fact_id = 'fact-1'")) is True
            )
        finally:
            await db.rollback()
            await db.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            await db.commit()


async def test_commit_outcome_unknown_is_recovered_from_durable_fact(integration_session_factory) -> None:
    """A lost commit acknowledgement must be handled as durable fact, not resubmission."""

    table_name = f"public.recovery_commit_unknown_{uuid4().hex}"
    async with integration_session_factory() as writer:
        await writer.execute(
            text(
                f"""
                CREATE TABLE {table_name} (
                    fact_id text PRIMARY KEY,
                    fact_status text NOT NULL,
                    projection_applied boolean NOT NULL DEFAULT false,
                    physical_submit_count integer NOT NULL DEFAULT 0
                )
                """
            )
        )
        await writer.commit()
        try:
            await writer.execute(
                text(
                    f"INSERT INTO {table_name} (fact_id, fact_status, physical_submit_count) "
                    "VALUES ('fact-unknown', 'ACK_COMMITTED', 1)"
                )
            )
            await writer.commit()
            with pytest.raises(ConnectionError, match="commit acknowledgement lost"):
                raise ConnectionError("commit acknowledgement lost")

            async with integration_session_factory() as reader:
                durable = (
                    await reader.execute(
                        text(
                            f"SELECT fact_status, projection_applied, physical_submit_count "
                            f"FROM {table_name} WHERE fact_id = 'fact-unknown'"
                        )
                    )
                ).one()
                assert tuple(durable) == ("ACK_COMMITTED", False, 1)
                await reader.execute(
                    text(
                        f"UPDATE {table_name} SET projection_applied = true "
                        "WHERE fact_status = 'ACK_COMMITTED' AND NOT projection_applied"
                    )
                )
                await reader.commit()

            async with integration_session_factory() as verifier:
                result = (
                    await verifier.execute(
                        text(
                            f"SELECT projection_applied, physical_submit_count "
                            f"FROM {table_name} WHERE fact_id = 'fact-unknown'"
                        )
                    )
                ).one()
                assert tuple(result) == (True, 1)
        finally:
            await writer.rollback()
            await writer.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            await writer.commit()


async def test_worker_interruption_replays_projection_without_duplicate_submit(integration_session_factory) -> None:
    """A cancelled worker leaves Tx1 durable and lets the next tick replay Tx2 only."""

    table_name = f"public.recovery_worker_interrupt_{uuid4().hex}"
    async with integration_session_factory() as db:
        await db.execute(
            text(
                f"""
                CREATE TABLE {table_name} (
                    fact_id text PRIMARY KEY,
                    fact_status text NOT NULL,
                    projection_applied boolean NOT NULL DEFAULT false,
                    physical_submit_count integer NOT NULL DEFAULT 0
                )
                """
            )
        )
        await db.commit()
        try:
            async with db.begin():
                await db.execute(
                    text(
                        f"INSERT INTO {table_name} (fact_id, fact_status, physical_submit_count) "
                        "VALUES ('fact-worker', 'ACK_COMMITTED', 1)"
                    )
                )

            tx2 = await db.begin()
            await db.execute(text(f"UPDATE {table_name} SET projection_applied = true WHERE fact_id = 'fact-worker'"))
            with pytest.raises(asyncio.CancelledError):
                raise asyncio.CancelledError
            await tx2.rollback()

            async with db.begin():
                await db.execute(
                    text(
                        f"UPDATE {table_name} SET projection_applied = true "
                        "WHERE fact_status = 'ACK_COMMITTED' AND NOT projection_applied"
                    )
                )
            result = (
                await db.execute(
                    text(
                        f"SELECT projection_applied, physical_submit_count "
                        f"FROM {table_name} WHERE fact_id = 'fact-worker'"
                    )
                )
            ).one()
            assert tuple(result) == (True, 1)
        finally:
            await db.rollback()
            await db.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            await db.commit()


async def test_candidate_scan_is_oldest_first_without_starvation(integration_session_factory) -> None:
    """Stable oldest-first ordering keeps a bounded scan from starving old candidates."""

    table_name = f"public.recovery_starvation_{uuid4().hex}"
    async with integration_session_factory() as db:
        await db.execute(
            text(
                f"""
                CREATE TABLE {table_name} (
                    candidate_id text PRIMARY KEY,
                    updated_at integer NOT NULL,
                    projection_applied boolean NOT NULL DEFAULT false
                )
                """
            )
        )
        await db.execute(
            text(
                f"INSERT INTO {table_name} (candidate_id, updated_at) VALUES "
                "('old-a', 1), ('old-b', 2), ('new-a', 100), ('new-b', 101)"
            )
        )
        await db.commit()
        try:
            query = text(
                f"SELECT candidate_id FROM {table_name} WHERE NOT projection_applied "
                "ORDER BY updated_at ASC, candidate_id ASC LIMIT 2"
            )
            first = [row[0] for row in (await db.execute(query)).all()]
            assert first == ["old-a", "old-b"]
            await db.execute(
                text(f"UPDATE {table_name} SET projection_applied = true WHERE candidate_id IN ('old-a', 'old-b')")
            )
            await db.commit()
            second = [row[0] for row in (await db.execute(query)).all()]
            assert second == ["new-a", "new-b"]
        finally:
            await db.rollback()
            await db.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            await db.commit()


async def test_candidate_query_is_null_safe_stable_and_bounded(integration_session_factory) -> None:
    """Candidate discovery must not lose nullable provenance rows."""

    fact_table = f"public.recovery_candidate_fact_{uuid4().hex}"
    projection_table = f"public.recovery_candidate_projection_{uuid4().hex}"
    async with integration_session_factory() as db:
        await db.execute(
            text(
                f"""
                CREATE TABLE {fact_table} (
                    task_id text NOT NULL,
                    member_id text NOT NULL,
                    last_operation_id text,
                    outcome_status text NOT NULL,
                    updated_at integer NOT NULL,
                    active_execution boolean NOT NULL DEFAULT true,
                    PRIMARY KEY (task_id, member_id)
                )
                """
            )
        )
        await db.execute(
            text(
                f"""
                CREATE TABLE {projection_table} (
                    task_id text NOT NULL,
                    member_id text NOT NULL,
                    source_transport_task_id text,
                    source_operation_id text,
                    PRIMARY KEY (task_id, member_id)
                )
                """
            )
        )
        await db.execute(
            text(
                f"""
                INSERT INTO {fact_table}
                    (task_id, member_id, last_operation_id, outcome_status, updated_at, active_execution)
                VALUES
                    ('missing', 'm1', 'op-missing', 'FINAL', 1, true),
                    ('both-null', 'm1', 'op-null', 'FINAL', 2, true),
                    ('transport-match-op-null', 'm1', 'op-a', 'FINAL', 3, true),
                    ('transport-null-op-match', 'm1', 'op-b', 'FINAL', 4, true),
                    ('exact', 'm1', 'op-exact', 'FINAL', 5, true),
                    ('stale-active-filter', 'm1', 'op-stale', 'FINAL', 6, false)
                """
            )
        )
        await db.execute(
            text(
                f"""
                INSERT INTO {projection_table}
                    (task_id, member_id, source_transport_task_id, source_operation_id)
                VALUES
                    ('both-null', 'm1', NULL, NULL),
                    ('transport-match-op-null', 'm1', 'transport-match-op-null', NULL),
                    ('transport-null-op-match', 'm1', NULL, 'op-b'),
                    ('exact', 'm1', 'exact', 'op-exact'),
                    ('stale-active-filter', 'm1', 'other', 'other-op')
                """
            )
        )
        await db.commit()
        try:
            candidate_sql = text(
                f"""
                SELECT f.task_id, f.member_id, f.last_operation_id
                FROM {fact_table} AS f
                LEFT JOIN {projection_table} AS p
                  ON p.task_id = f.task_id AND p.member_id = f.member_id
                WHERE f.last_operation_id IS NOT NULL
                  AND f.outcome_status = 'FINAL'
                  AND f.active_execution
                  AND (
                        p.task_id IS NULL
                     OR p.source_transport_task_id IS DISTINCT FROM f.task_id
                     OR p.source_operation_id IS DISTINCT FROM f.last_operation_id
                  )
                ORDER BY f.updated_at ASC, f.task_id ASC, f.member_id ASC
                LIMIT 100
                """
            )
            rows = (await db.execute(candidate_sql)).all()
            assert [(row[0], row[1]) for row in rows] == [
                ("missing", "m1"),
                ("both-null", "m1"),
                ("transport-match-op-null", "m1"),
                ("transport-null-op-match", "m1"),
            ]

            explain = await db.scalar(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + candidate_sql.text))
            plan = explain[0] if isinstance(explain, list) else explain
            serialized = json.dumps(plan)
            assert "Limit" in serialized
            assert "Actual" in serialized or "actual" in serialized
            assert "Buffers" in serialized or "Shared Hit Blocks" in serialized
        finally:
            await db.rollback()
            await db.execute(text(f"DROP TABLE IF EXISTS {fact_table}, {projection_table}"))
            await db.commit()
