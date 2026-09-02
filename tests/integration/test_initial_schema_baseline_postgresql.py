"""最终初始 migration 的 PostgreSQL schema successor。"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from tests.support.postgresql_catalog import (
    DISPOSITION_SHA256,
    FINAL_MANIFEST_SHA256,
    RAW_MANIFEST_SHA256,
    TRANSITION_DISPOSITION_SHA256,
    ManifestMismatch,
    assert_database_head,
    assert_final_manifest,
    assert_reviewed_catalog_transition,
    canonical_json_bytes,
    catalog_transition_differences,
    collect_postgresql_catalog,
    derive_final_manifest,
    disposed_table_identities,
    load_json_fixture,
    validate_disposition_bytes,
    validate_raw_manifest_bytes,
    validate_transition_disposition_bytes,
)
from tests.support.postgresql_heavy import run_alembic, temporary_database

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
RAW_MANIFEST_PATH = FIXTURES_DIR / "initial_schema_old_chain_catalog.json"
FINAL_MANIFEST_PATH = FIXTURES_DIR / "initial_schema_final_manifest.json"
DISPOSITION_PATH = FIXTURES_DIR / "initial_schema_disposition.json"
TRANSITION_DISPOSITION_PATH = FIXTURES_DIR / "initial_schema_transition_disposition.json"
INITIAL_REVISION = "f9c7c2e5f501"
HEAD_REVISION = "ed5ed8eb0c46"


def _fixtures() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        load_json_fixture(RAW_MANIFEST_PATH),
        load_json_fixture(FINAL_MANIFEST_PATH),
        load_json_fixture(DISPOSITION_PATH),
    )


def test_raw_catalog_fixture_is_immutable_and_disposition_is_complete() -> None:
    raw_bytes = RAW_MANIFEST_PATH.read_bytes()
    disposition_bytes = DISPOSITION_PATH.read_bytes()
    transition_bytes = TRANSITION_DISPOSITION_PATH.read_bytes()
    validate_raw_manifest_bytes(raw_bytes)
    validate_disposition_bytes(disposition_bytes)
    raw, final, disposition = _fixtures()
    old_derived = derive_final_manifest(raw, disposition)
    validate_transition_disposition_bytes(
        transition_bytes,
        old_derived_manifest=old_derived,
        final_manifest=final,
    )
    transition = load_json_fixture(TRANSITION_DISPOSITION_PATH)
    entries = disposition["tables"]

    assert RAW_MANIFEST_SHA256 == "77214740a6fd48c113c043aeb32887209681c4fd213833e7e9ee391f317117c9"
    assert DISPOSITION_SHA256 == "04ad7dbf1278b9507b5ae58b92071772e9e31948c79e92c2035fba161a8878f8"
    assert FINAL_MANIFEST_SHA256 == "9b47ad6a40fb6affaaaeffab2e3b530d31493a82562c092440ec017add7aa744"
    assert TRANSITION_DISPOSITION_SHA256 == "5ce70024332cb6903810750154b3bab10a38cf439a9cfdc578e4e526c910999c"
    assert disposition["source_manifest_sha256"] == RAW_MANIFEST_SHA256
    assert sum(entry["disposition"] == "FINAL_DELETE_AFTER_SUCCESSOR" for entry in entries) == 21
    assert sum(entry["disposition"] == "RETAIN" for entry in entries) == 1
    assert sum(entry.get("catalog_only") is True and entry["target"] == "NONE" for entry in entries) == 2
    assert transition["unresolved"] == 0
    assert [
        {key: entry[key] for key in ("identity", "new", "old", "section")} for entry in transition["differences"]
    ] == catalog_transition_differences(old_derived, final)
    assert_reviewed_catalog_transition(old_derived, final, transition)
    with pytest.raises(ManifestMismatch, match="unregistered catalog difference"):
        assert_final_manifest(old_derived, final, disposition)
    reviewed_transition_removals = {
        entry["identity"]
        for entry in transition["differences"]
        if entry["section"] == "tables" and entry["old"] is not None and entry["new"] is None
    }
    assert (
        disposed_table_identities(raw, final)
        == {entry["identity"] for entry in entries if entry["disposition"] != "RETAIN"} | reviewed_transition_removals
    )
    assert_final_manifest(raw, old_derived, disposition, allow_disposed_tables=True)
    with pytest.raises(ManifestMismatch, match="reviewed disposed tables still present") as raised:
        assert_final_manifest(raw, old_derived, disposition)
    for identity in disposed_table_identities(raw, old_derived):
        assert identity in str(raised.value)
    assert_final_manifest(final, final, disposition, allow_disposed_tables=False)


def test_disposition_swap_add_and_regenerated_final_fail_closed() -> None:
    raw, _final, disposition = _fixtures()
    mutated = copy.deepcopy(disposition)
    mutated["tables"][0]["disposition"] = "RETAIN"
    mutated["tables"][0]["final_state"] = "PRESENT"
    mutated["tables"].append(
        {
            "catalog_only": False,
            "disposition": "FINAL_DELETE_AFTER_SUCCESSOR",
            "final_state": "ABSENT",
            "identity": "wes_biz.devices",
        }
    )
    regenerated_final = derive_final_manifest(raw, mutated)

    with pytest.raises(ManifestMismatch, match="disposition"):
        validate_disposition_bytes(canonical_json_bytes(mutated))
    with pytest.raises(ManifestMismatch, match="unregistered catalog difference"):
        assert_final_manifest(regenerated_final, _fixtures()[1], disposition, allow_disposed_tables=False)


@pytest.mark.asyncio()
async def test_database_head_contract_reads_version_table() -> None:
    class HeadConnection:
        def __init__(self) -> None:
            self.query = ""

        async def fetchval(self, query: str) -> str:
            self.query = query
            return INITIAL_REVISION

    connection = HeadConnection()

    await assert_database_head(connection, INITIAL_REVISION)

    assert connection.query == "SELECT version_num FROM wes_sys.alembic_version"


def test_unregistered_manifest_difference_fails_closed() -> None:
    raw, final, disposition = _fixtures()
    drifted = copy.deepcopy(final)
    retained_column = next(
        row
        for row in drifted["catalog"]["columns"]
        if f"{row['schema']}.{row['table']}" not in disposed_table_identities(raw, final)
    )
    retained_column["nullable"] = not retained_column["nullable"]

    with pytest.raises(ManifestMismatch, match="unregistered catalog difference"):
        assert_final_manifest(drifted, final, disposition)


def test_platform_version_is_owned_by_pinned_compose_not_schema_transition_disposition() -> None:
    raw, final, disposition = _fixtures()
    old_derived = derive_final_manifest(raw, disposition)
    platform_updated = copy.deepcopy(final)
    platform_updated["catalog"]["server"][0] = {
        "server_version": "17.11",
        "server_version_num": "170011",
    }
    timescaledb = next(row for row in platform_updated["catalog"]["extensions"] if row["name"] == "timescaledb")
    timescaledb["version"] = "2.28.0"

    assert catalog_transition_differences(old_derived, platform_updated) == catalog_transition_differences(
        old_derived,
        final,
    )
    with pytest.raises(ManifestMismatch, match="unregistered catalog difference"):
        assert_final_manifest(platform_updated, final, disposition)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def upgraded_initial_schema_catalog() -> dict[str, Any]:
    _raw, final, _disposition = _fixtures()
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", INITIAL_REVISION, database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await assert_database_head(connection, INITIAL_REVISION)
            live_manifest = await collect_postgresql_catalog(connection, database=final["database"])
        finally:
            await connection.close()
    return live_manifest


@pytest.mark.asyncio(loop_scope="module")
async def test_initial_schema_matches_reviewed_final_manifest(
    upgraded_initial_schema_catalog: dict[str, Any],
) -> None:
    _raw, final, disposition = _fixtures()
    assert_final_manifest(upgraded_initial_schema_catalog, final, disposition, allow_disposed_tables=False)


@pytest.mark.asyncio()
async def test_transport_face_successor_preserves_legacy_empty_and_has_lossless_downgrade_guard() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", INITIAL_REVISION, database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await connection.execute(
                """
                INSERT INTO wes_runtime.transport_tasks (
                    id, transport_task_id, client_request_id, request_digest, kind, caller_json, request_json,
                    submit_operation_id, submit_timestamp_ms, submit_request_body, submit_request_body_digest,
                    status, submit_attempt_count, outcome_version, published_outcome_version,
                    last_applied_wms_outcome_revision, created_at, updated_at
                ) VALUES (
                    1, 'transport-face-migration', 'request-face-migration', repeat('a', 64), 'RACK_MOVE',
                    '{}'::json, '{}'::json, '019f12d0-58d7-7b4d-a23a-1b90aa5d4472', 1, '{}', repeat('b', 64),
                    'PENDING', 0, 0, 0, 0, now(), now()
                )
                """
            )
            await connection.execute(
                """
                INSERT INTO wes_runtime.transport_members (
                    id, transport_task_id, ordinal, object_type, object_id, source_json, target_json,
                    status, position_unknown, arrival_face, updated_at
                ) VALUES (
                    1, 'transport-face-migration', 0, 'RACK', 'rack-1', '{}'::json, '{}'::json,
                    'PENDING', FALSE, '', now()
                )
                """
            )
        finally:
            await connection.close()

        run_alembic("upgrade", "head", database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await assert_database_head(connection, HEAD_REVISION)
            assert (
                await connection.fetchval("SELECT arrival_face FROM wes_runtime.transport_members WHERE id = 1") == ""
            )
            columns = await connection.fetch(
                """
                SELECT table_schema, table_name, data_type
                FROM information_schema.columns
                WHERE column_name = 'arrival_face'
                  AND ((table_schema = 'wes_runtime' AND table_name = 'transport_members')
                    OR (table_schema = 'wes_biz' AND table_name = 'position_projections'))
                ORDER BY table_schema, table_name
                """
            )
            assert [tuple(row) for row in columns] == [
                ("wes_biz", "position_projections", "text"),
                ("wes_runtime", "transport_members", "text"),
            ]
            for face in (None, "90", "270", "FACE@01", "面-1", " ", "x" * 1000):
                await connection.execute(
                    "UPDATE wes_runtime.transport_members SET arrival_face = $1 WHERE id = 1",
                    face,
                )
                assert (
                    await connection.fetchval("SELECT arrival_face FROM wes_runtime.transport_members WHERE id = 1")
                    == face
                )
            await connection.execute("UPDATE wes_runtime.transport_members SET arrival_face = 'FACE@01' WHERE id = 1")
        finally:
            await connection.close()

        with pytest.raises(subprocess.CalledProcessError) as downgrade_error:
            run_alembic("downgrade", INITIAL_REVISION, database_url=database_url)
        assert "cannot fit VARCHAR(1)" in downgrade_error.value.stderr

        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await connection.execute("UPDATE wes_runtime.transport_members SET arrival_face = '' WHERE id = 1")
        finally:
            await connection.close()
        run_alembic("downgrade", INITIAL_REVISION, database_url=database_url)

        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await assert_database_head(connection, INITIAL_REVISION)
            assert (
                await connection.fetchval("SELECT arrival_face FROM wes_runtime.transport_members WHERE id = 1") == ""
            )
        finally:
            await connection.close()


@pytest.mark.asyncio()
async def test_transport_debug_projection_successor_backfills_latest_applied_fact() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "e0da335c057d", database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await connection.execute(
                """
                INSERT INTO wes_runtime.transport_tasks (
                    id, transport_task_id, client_request_id, request_digest, kind, caller_json, request_json,
                    submit_operation_id, submit_timestamp_ms, submit_request_body, submit_request_body_digest,
                    status, submit_attempt_count, outcome_version, published_outcome_version,
                    last_applied_wms_outcome_revision, created_at, updated_at
                ) VALUES (
                    1, 'transport-debug-backfill', 'request-debug-backfill', repeat('a', 64), 'RACK_MOVE',
                    '{"workline_id":"TRANSPORT_DEBUG"}'::json, '{}'::json,
                    '019f12d0-58d7-7b4d-a23a-1b90aa5d4472', 1, '{}', repeat('b', 64),
                    'SUCCEEDED', 1, 2, 2, 1, now(), now()
                )
                """
            )
            await connection.execute(
                """
                INSERT INTO wes_runtime.transport_members (
                    id, transport_task_id, ordinal, object_type, object_id, source_json, target_json,
                    status, final_position_json, position_unknown, arrival_face, last_operation_id, updated_at
                ) VALUES (
                    1, 'transport-debug-backfill', 0, 'RACK', '510056', '{}'::json, '{}'::json,
                    'SUCCEEDED', '{"kind":"RACK_POSITION","location_code":"KT19"}'::json,
                    FALSE, '90', '019f12d0-58d7-7b4d-a23a-1b90aa5d4473', now()
                )
                """
            )
            await connection.execute(
                """
                INSERT INTO wes_runtime.transport_evidence (
                    id, operation_id, transport_task_id, operation, outcome_revision, event_timestamp_ms,
                    message_digest, payload_json, ack_timestamp_ms, ack_data_json, status, received_at, processed_at
                ) VALUES (
                    1, '019f12d0-58d7-7b4d-a23a-1b90aa5d4473', 'transport-debug-backfill',
                    'transport.task.resulted@v1', 1, 1, repeat('c', 64), '{}'::json, 2, '{}'::json,
                    'APPLIED', now(), now()
                )
                """
            )
        finally:
            await connection.close()

        run_alembic("upgrade", "head", database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            row = await connection.fetchrow(
                """
                SELECT object_type, object_id, position_json::text, position_unknown, arrival_face,
                       source_operation_id, source_transport_task_id
                FROM wes_runtime.transport_debug_position_projections
                """
            )
            assert tuple(row) == (
                "RACK",
                "510056",
                '{"kind":"RACK_POSITION","location_code":"KT19"}',
                False,
                "90",
                "019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
                "transport-debug-backfill",
            )
        finally:
            await connection.close()

        run_alembic("downgrade", "e0da335c057d", database_url=database_url)

        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await assert_database_head(connection, "e0da335c057d")
            assert (
                await connection.fetchval("SELECT arrival_face FROM wes_runtime.transport_members WHERE id = 1") == "90"
            )
        finally:
            await connection.close()
