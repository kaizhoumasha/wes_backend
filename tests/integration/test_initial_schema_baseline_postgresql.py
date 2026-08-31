"""最终初始 migration 的 PostgreSQL schema successor。"""

from __future__ import annotations

import copy
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
    assert FINAL_MANIFEST_SHA256 == "2cf84f8ebdd9b9533813c4765abb186d9c024312efd5b494866abb7be5cd389d"
    assert TRANSITION_DISPOSITION_SHA256 == "4192a879f7cf4ed006d63b95ea74529d36f1678fd0ad9ae490287e3efd5730b7"
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
    assert disposed_table_identities(raw, final) == {
        entry["identity"] for entry in entries if entry["disposition"] != "RETAIN"
    }
    assert_final_manifest(raw, old_derived, disposition, allow_disposed_tables=True)
    with pytest.raises(ManifestMismatch, match="reviewed disposed tables still present") as raised:
        assert_final_manifest(raw, old_derived, disposition)
    for identity in disposed_table_identities(raw, final):
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def upgraded_initial_schema_catalog() -> dict[str, Any]:
    _raw, final, _disposition = _fixtures()
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await assert_database_head(connection, INITIAL_REVISION)
            live_manifest = await collect_postgresql_catalog(connection, database=final["database"])
        finally:
            await connection.close()
        check = run_alembic("check", database_url=database_url)
        assert "No new upgrade operations detected." in check.stdout
    return live_manifest


@pytest.mark.asyncio(loop_scope="module")
async def test_initial_schema_matches_reviewed_final_manifest(
    upgraded_initial_schema_catalog: dict[str, Any],
) -> None:
    _raw, final, disposition = _fixtures()
    assert_final_manifest(upgraded_initial_schema_catalog, final, disposition, allow_disposed_tables=False)
