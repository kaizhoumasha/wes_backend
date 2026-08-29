"""reset runtime data 在隔离 PostgreSQL 临时库上的运维验收。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from scripts.data.reset_runtime_data import reset_runtime_data, reset_transport_task_data
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


async def _seed_transport_task(session: AsyncSession, transport_task_id: str, resource_id: str) -> None:
    await session.execute(
        text(
            "INSERT INTO wes_runtime.transport_tasks ("
            "transport_task_id, client_request_id, request_digest, kind, caller_json, request_json, "
            "submit_operation_id, submit_timestamp_ms, submit_request_body, submit_request_body_digest, "
            "status, submit_attempt_count, outcome_version, published_outcome_version, "
            "last_applied_wms_outcome_revision, created_at, updated_at"
            ") VALUES ("
            ":task_id, :client_request_id, :digest, 'RACK_MOVE', '{}'::json, '{}'::json, "
            ":operation_id, 1787942960963, '{}', :digest, 'RECONCILING', 1, 0, 0, 0, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
            ")"
        ),
        {
            "task_id": transport_task_id,
            "client_request_id": f"client-{transport_task_id}",
            "operation_id": f"00000000-0000-0000-0000-{resource_id[-12:].zfill(12)}",
            "digest": "0" * 64,
        },
    )
    await session.execute(
        text(
            "INSERT INTO wes_runtime.transport_members ("
            "transport_task_id, ordinal, object_type, object_id, source_json, target_json, "
            "status, position_unknown, updated_at"
            ") VALUES ("
            ":task_id, 0, 'RACK', :resource_id, '{}'::json, '{}'::json, "
            "'PENDING', false, CURRENT_TIMESTAMP"
            ")"
        ),
        {"task_id": transport_task_id, "resource_id": resource_id},
    )
    await session.execute(
        text(
            "INSERT INTO wes_runtime.transport_resource_bindings ("
            "transport_task_id, resource_type, resource_id, created_at"
            ") VALUES (:task_id, 'RACK', :resource_id, CURRENT_TIMESTAMP)"
        ),
        {"task_id": transport_task_id, "resource_id": resource_id},
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


@pytest.mark.integration
def test_targeted_transport_reset_deletes_only_requested_aggregate() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            session_factory, engine = _session_factory(database_url)
            try:
                async with session_factory() as session:
                    await _seed_transport_task(session, "transport-delete", "RACK-DELETE")
                    await _seed_transport_task(session, "transport-keep", "RACK-KEEP")

                    dry_summary = await reset_transport_task_data(
                        session,
                        transport_task_id="transport-delete",
                        apply=False,
                    )
                    assert dry_summary.rows_before["wes_runtime.transport_tasks"] == 1

                    summary = await reset_transport_task_data(
                        session,
                        transport_task_id="transport-delete",
                        apply=True,
                    )
                    assert summary.deleted["wes_runtime.transport_tasks"] == 1

                    for table in ("transport_resource_bindings", "transport_members", "transport_tasks"):
                        assert (
                            await session.scalar(
                                text(f"SELECT count(*) FROM wes_runtime.{table} WHERE transport_task_id = :task_id"),
                                {"task_id": "transport-delete"},
                            )
                            == 0
                        )
                        assert (
                            await session.scalar(
                                text(f"SELECT count(*) FROM wes_runtime.{table} WHERE transport_task_id = :task_id"),
                                {"task_id": "transport-keep"},
                            )
                            == 1
                        )
            finally:
                await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_targeted_transport_reset_deletes_persisted_evidence_receipt_projection_and_outcome() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            session_factory, engine = _session_factory(database_url)
            try:
                async with session_factory() as session:
                    await _seed_transport_task(session, "transport-evidence", "RACK-EVIDENCE")
                    await session.execute(
                        text(
                            "INSERT INTO wes_runtime.transport_evidence ("
                            "operation_id, transport_task_id, operation, outcome_revision, event_timestamp_ms, "
                            "message_digest, payload_json, ack_timestamp_ms, ack_data_json, status, received_at"
                            ") VALUES ("
                            "'00000000-0000-0000-0000-000000000001', 'transport-evidence', "
                            "'transport.task.resulted@v1', 1, 1787942960963, :digest, '{}'::json, "
                            "1787942960964, '{}'::json, 'PENDING', CURRENT_TIMESTAMP"
                            ")"
                        ),
                        {"digest": "1" * 64},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO wes_runtime.transport_callback_receipts ("
                            "operation_id, operation, message_digest, message_json, response_http_status, "
                            "response_code, response_timestamp_ms, response_data_json, received_at"
                            ") VALUES ("
                            "'00000000-0000-0000-0000-000000000001', 'transport.task.resulted@v1', "
                            ":digest, '{}'::json, 202, 'RECEIVED', 1787942960964, "
                            '\'{"transport_task_id": "transport-evidence"}\'::json, CURRENT_TIMESTAMP'
                            ")"
                        ),
                        {"digest": "1" * 64},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO wes_runtime.transport_position_projections ("
                            "object_type, object_id, position_json, position_unknown, arrival_face, "
                            "source_operation_id, source_transport_task_id, updated_at"
                            ") VALUES ("
                            "'RACK', 'RACK-EVIDENCE', '{}'::json, false, 'A', "
                            "'00000000-0000-0000-0000-000000000001', 'transport-evidence', CURRENT_TIMESTAMP"
                            ")"
                        )
                    )
                    await _seed_transport_task(session, "transport-outcome", "RACK-OUTCOME")
                    await session.execute(
                        text(
                            "UPDATE wes_runtime.transport_tasks SET outcome_version = 1, outcome_json = '{}'::json "
                            "WHERE transport_task_id = 'transport-outcome'"
                        )
                    )
                    await session.commit()

                    for task_id in ("transport-evidence", "transport-outcome"):
                        await reset_transport_task_data(session, transport_task_id=task_id, apply=True)
                        assert (
                            await session.scalar(
                                text(
                                    "SELECT count(*) FROM wes_runtime.transport_tasks "
                                    "WHERE transport_task_id = :task_id"
                                ),
                                {"task_id": task_id},
                            )
                            == 0
                        )
                    assert (
                        await session.scalar(
                            text(
                                "SELECT count(*) FROM wes_runtime.transport_callback_receipts "
                                "WHERE response_data_json ->> 'transport_task_id' = 'transport-evidence'"
                            )
                        )
                        == 0
                    )
                    assert (
                        await session.scalar(
                            text(
                                "SELECT count(*) FROM wes_runtime.transport_position_projections "
                                "WHERE source_operation_id = '00000000-0000-0000-0000-000000000001'"
                            )
                        )
                        == 0
                    )
            finally:
                await engine.dispose()

    asyncio.run(scenario())
