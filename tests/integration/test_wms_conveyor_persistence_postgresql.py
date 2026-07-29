"""WMS 输送线持久化关系的真实 PostgreSQL 合同。"""

from __future__ import annotations

import asyncio
from datetime import datetime

import asyncpg
import pytest

from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

REVISION = "f9ffbef8992a"
PARENT_REVISION = "36aa187238cc"


async def _seed_runtime_intents(connection: asyncpg.Connection, *intent_ids: int) -> None:
    """仅为新表 FK 场景建立最小 intent 根，不引入无关业务 fixture。"""
    await connection.execute("SET session_replication_role = replica")
    try:
        for intent_id in intent_ids:
            await connection.execute(
                """
                INSERT INTO wes_runtime.runtime_intent_logs (
                    id,
                    execution_session_id,
                    correlation_id,
                    provider_code,
                    target_domain,
                    target_action,
                    idempotency_key,
                    request_hash,
                    dispatch_key,
                    effect_status
                )
                VALUES ($1, 1, $2, 'WMS', 'wms_integration', 'test', $3, $4, $5, 'PROPOSED')
                """,
                intent_id,
                f"corr-{intent_id}",
                f"idem-{intent_id}",
                f"hash-{intent_id}",
                f"dispatch-{intent_id}",
            )
    finally:
        await connection.execute("SET session_replication_role = origin")


async def _insert_route(
    connection: asyncpg.Connection,
    *,
    route_id: str,
    bin_code: str,
    intent_id: int,
) -> None:
    await connection.execute(
        """
        INSERT INTO wes_runtime.bin_route_instances (
            route_instance_id,
            bin_code,
            workline_id,
            created_by_e12_intent_id,
            current_node,
            route_version,
            lifecycle_state,
            last_transition_source,
            last_transition_source_event_id
        )
        VALUES ($1, $2, 10, $3, 'SCAN3', 1, 'ACTIVE', 'integration_test', $4)
        """,
        route_id,
        bin_code,
        intent_id,
        f"route-event-{route_id}",
    )


async def _assert_schema_and_constraints(connection: asyncpg.Connection) -> None:
    tables = set(
        await connection.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'wes_runtime'
              AND table_name = ANY($1::text[])
            """,
            [
                "wms_rack_demands",
                "material_flow_owners",
                "bin_route_instances",
                "wms_conveyor_batch_members",
            ],
        )
    )
    assert {row["table_name"] for row in tables} == {
        "wms_rack_demands",
        "material_flow_owners",
        "bin_route_instances",
        "wms_conveyor_batch_members",
    }

    constraint_names = set(
        await connection.fetch(
            """
            SELECT conname
            FROM pg_constraint
            WHERE connamespace = 'wes_runtime'::regnamespace
            """
        )
    )
    assert {
        "ck_wms_rack_demands_root_shape",
        "ux_material_flow_owners_active_object",
        "ck_bin_route_instances_location_shape",
        "fk_conveyor_queue_memberships_route_instance",
        "ck_conveyor_queue_memberships_claim_shape",
        "fk_wms_conveyor_batch_members_route",
    } - {row["conname"] for row in constraint_names} == {
        "ux_material_flow_owners_active_object",
    }

    index_names = {
        row["indexname"]
        for row in await connection.fetch(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'wes_runtime'
            """
        )
    }
    assert {
        "ux_wms_rack_demands_active_station_rack_type",
        "ux_material_flow_owners_active_object",
        "ux_bin_route_instances_active_bin",
        "ix_wes_runtime_conveyor_queue_memberships_return_fifo_unclaimed",
        "ux_wms_conveyor_batch_members_active_inbound_position",
    } <= index_names


async def _assert_database_shapes(connection: asyncpg.Connection) -> None:
    await _seed_runtime_intents(connection, 1001, 1002, 1003, 1004)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _insert_route(
            connection,
            route_id="route-invalid-fk",
            bin_code="BIN-INVALID-FK",
            intent_id=999999,
        )

    await _insert_route(connection, route_id="route-1", bin_code="BIN-1", intent_id=1001)
    await _insert_route(connection, route_id="route-2", bin_code="BIN-2", intent_id=1002)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_route(
            connection,
            route_id="route-duplicate-bin",
            bin_code="BIN-1",
            intent_id=1003,
        )

    await connection.execute(
        """
        INSERT INTO wes_runtime.wms_rack_demands (
            workline_id,
            station_code,
            rack_type,
            demand_generation,
            root_operation_identity,
            root_intent_id,
            lifecycle_state,
            opened_at_ms
        )
        VALUES (
            10,
            'STATION-A',
            'FULL_BOX',
            1,
            'wms.fulfillment.request_rack_supply@v1',
            1001,
            'ACTIVE',
            1000
        )
        """
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await connection.execute(
            """
            INSERT INTO wes_runtime.wms_rack_demands (
                workline_id,
                station_code,
                rack_type,
                demand_generation,
                root_operation_identity,
                root_intent_id,
                lifecycle_state,
                opened_at_ms
            )
            VALUES (
                10,
                'STATION-A',
                'FULL_BOX',
                2,
                'wms.fulfillment.request_rack_supply@v1',
                1002,
                'ACTIVE',
                1001
            )
            """
        )

    await connection.execute(
        """
        INSERT INTO wes_runtime.material_flow_owners (
            workline_id,
            object_type,
            object_key,
            owner_type,
            owner_key,
            lifecycle_state,
            source_event_id,
            acquired_at_ms
        )
        VALUES (10, 'RACK_FACE', 'RACK-1:FACE-A', 'STATION_TRANSPORT', 'route-1', 'ACTIVE', 'event-1', 1000)
        """
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await connection.execute(
            """
            INSERT INTO wes_runtime.material_flow_owners (
                workline_id,
                object_type,
                object_key,
                owner_type,
                owner_key,
                lifecycle_state,
                source_event_id,
                acquired_at_ms
            )
            VALUES (
                11,
                'RACK_FACE',
                'RACK-1:FACE-A',
                'PIECE_SORTING',
                'route-2',
                'ACTIVE',
                'event-2',
                1001
            )
            """
        )

    with pytest.raises(asyncpg.CheckViolationError):
        await connection.execute(
            """
            INSERT INTO wes_runtime.conveyor_queue_memberships (
                bin_code,
                workline_id,
                conveyor_code,
                queue_code,
                queue_role,
                membership_status,
                entered_at,
                evidence_json
            )
            VALUES ('BIN-MISSING-ROUTE', 10, 'CONV-1', 'RETURN', 'RETURN_QUEUE', 'ACTIVE', 1000, '{}')
            """
        )

    membership_id = await connection.fetchval(
        """
        INSERT INTO wes_runtime.conveyor_queue_memberships (
            bin_code,
            workline_id,
            conveyor_code,
            queue_code,
            queue_role,
            membership_status,
            entered_at,
            route_instance_id,
            scan3_enqueued_at,
            queue_position,
            evidence_json
        )
        VALUES (
            'BIN-1',
            10,
            'CONV-1',
            'RETURN',
            'RETURN_QUEUE',
            'ACTIVE',
            1000,
            'route-1',
            $1,
            1,
            '{}'
        )
        RETURNING id
        """,
        datetime(2026, 7, 30, 3, 45),
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await connection.execute(
            """
            INSERT INTO wes_runtime.conveyor_queue_memberships (
                bin_code,
                workline_id,
                conveyor_code,
                queue_code,
                queue_role,
                membership_status,
                entered_at,
                route_instance_id,
                scan3_enqueued_at,
                queue_position,
                evidence_json
            )
            VALUES (
                'BIN-OTHER',
                10,
                'CONV-1',
                'RETURN',
                'RETURN_QUEUE',
                'ACTIVE',
                1001,
                'route-1',
                $1,
                2,
                '{}'
            )
            """,
            datetime(2026, 7, 30, 3, 46),
        )

    await connection.execute("SET enable_seqscan = off")
    plan = "\n".join(
        row["QUERY PLAN"]
        for row in await connection.fetch(
            """
            EXPLAIN (COSTS OFF)
            SELECT id
            FROM wes_runtime.conveyor_queue_memberships
            WHERE workline_id = 10
              AND queue_code = 'RETURN'
              AND membership_status = 'ACTIVE'
              AND queue_role = 'RETURN_QUEUE'
              AND e13_claim_intent_id IS NULL
            ORDER BY scan3_enqueued_at, queue_position, bin_code
            LIMIT 1
            """
        )
    )
    assert "ix_wes_runtime_conveyor_queue_memberships_return_fifo_unclaimed" in plan
    await connection.execute("RESET enable_seqscan")

    with pytest.raises(asyncpg.CheckViolationError):
        await connection.execute(
            """
            UPDATE wes_runtime.conveyor_queue_memberships
            SET e13_claim_intent_id = 1003
            WHERE id = $1
            """,
            membership_id,
        )
    await connection.execute(
        """
        UPDATE wes_runtime.conveyor_queue_memberships
        SET e13_claim_intent_id = 1003,
            e13_claim_token = 'claim-1',
            e13_claim_until = $2,
            membership_status = 'RECONCILING'
        WHERE id = $1
        """,
        membership_id,
        datetime(2026, 7, 30, 4, 0),
    )

    await connection.execute(
        """
        INSERT INTO wes_runtime.wms_conveyor_batch_members (
            runtime_intent_log_id,
            route_instance_id,
            workline_id,
            queue_code,
            direction,
            sequence_no,
            bin_code,
            reserved_queue_position,
            member_state,
            staged_at_ms
        )
        VALUES (1001, 'route-1', 10, 'INBOUND', 'INBOUND', 1, 'BIN-1', 1, 'CANDIDATE', 1000)
        """
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await connection.execute(
            """
            INSERT INTO wes_runtime.wms_conveyor_batch_members (
                runtime_intent_log_id,
                route_instance_id,
                workline_id,
                queue_code,
                direction,
                sequence_no,
                bin_code,
                reserved_queue_position,
                member_state,
                staged_at_ms
            )
            VALUES (1002, 'route-2', 10, 'INBOUND', 'INBOUND', 1, 'BIN-2', 1, 'CANDIDATE', 1001)
            """
        )


async def _cleanup_scenario_rows(connection: asyncpg.Connection) -> None:
    """按本场景稳定主键清理，使 roundtrip 只验证 DDL 可逆性。"""
    await connection.execute(
        """
        DELETE FROM wes_runtime.wms_conveyor_batch_members
        WHERE runtime_intent_log_id IN (1001, 1002)
        """
    )
    await connection.execute(
        """
        DELETE FROM wes_runtime.conveyor_queue_memberships
        WHERE route_instance_id IN ('route-1', 'route-2')
        """
    )
    await connection.execute(
        """
        DELETE FROM wes_runtime.bin_route_instances
        WHERE route_instance_id IN ('route-1', 'route-2')
        """
    )
    await connection.execute(
        """
        DELETE FROM wes_runtime.material_flow_owners
        WHERE object_type = 'RACK_FACE' AND object_key = 'RACK-1:FACE-A'
        """
    )
    await connection.execute(
        """
        DELETE FROM wes_runtime.wms_rack_demands
        WHERE root_intent_id IN (1001, 1002)
        """
    )
    await connection.execute(
        """
        DELETE FROM wes_runtime.runtime_intent_logs
        WHERE id IN (1001, 1002, 1003, 1004)
        """
    )


@pytest.mark.integration
def test_wms_conveyor_relations_roundtrip_and_database_contracts() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", REVISION, database_url=database_url)
            connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://"))
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION
                await _assert_schema_and_constraints(connection)
                await _assert_database_shapes(connection)
                await _cleanup_scenario_rows(connection)
            finally:
                await connection.close()

            run_alembic("downgrade", PARENT_REVISION, database_url=database_url)
            connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://"))
            try:
                assert await connection.fetchval("SELECT to_regclass('wes_runtime.bin_route_instances')") is None
                columns = {
                    row["column_name"]
                    for row in await connection.fetch(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'wes_runtime'
                          AND table_name = 'conveyor_queue_memberships'
                        """
                    )
                }
                assert {
                    "route_instance_id",
                    "scan3_enqueued_at",
                    "queue_position",
                    "e13_claim_intent_id",
                    "e13_claim_token",
                    "e13_claim_until",
                }.isdisjoint(columns)
            finally:
                await connection.close()

            run_alembic("upgrade", REVISION, database_url=database_url)

    asyncio.run(scenario())
