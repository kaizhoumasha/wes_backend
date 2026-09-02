"""PostgreSQL schema catalog 的稳定采集与 manifest 比较。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from pathlib import Path

RAW_MANIFEST_SHA256 = "77214740a6fd48c113c043aeb32887209681c4fd213833e7e9ee391f317117c9"
DISPOSITION_SHA256 = "04ad7dbf1278b9507b5ae58b92071772e9e31948c79e92c2035fba161a8878f8"
OLD_DERIVED_MANIFEST_SHA256 = "87c2f3461068b97c713032bd83312bc64cdfbe481574d4d8c4e331b37a5bc0b6"
FINAL_MANIFEST_SHA256 = "9b47ad6a40fb6affaaaeffab2e3b530d31493a82562c092440ec017add7aa744"
TRANSITION_DISPOSITION_SHA256 = "5ce70024332cb6903810750154b3bab10a38cf439a9cfdc578e4e526c910999c"

EXPECTED_DISPOSITION_TABLES = {
    "wes_biz.ng_return_items": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_biz.resource_c0_session_cleanup_report": ("FINAL_DELETE", True, "NONE"),
    "wes_biz.runtime_holds": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_biz.system_outbox": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_biz.wms_call_evidence": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_biz.wms_circuit_breaker_state": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_biz.workline_bin_cell_reservations": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_biz.workline_diagnostics": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_biz.workline_dispatch_attempts": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.bin_route_instances": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.conveyor_queue_memberships": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.device_runtime_projections": ("FINAL_DELETE", True, "NONE"),
    "wes_runtime.execution_correlations": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.execution_sessions": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.execution_work_items": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.idempotency_keys": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.material_flow_owners": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.reconciliation_cases": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.runtime_holds": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.runtime_inbox": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.runtime_intent_logs": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.runtime_timelines": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.wms_rack_demands": ("FINAL_DELETE_AFTER_SUCCESSOR", False, None),
    "wes_runtime.workline_runtime_status_projections": (
        "RETAIN",
        False,
        "wes_runtime.workline_runtime_status_projections",
    ),
}

TIMESCALEDB_VOLATILE_FIELDS = (
    "last_run_duration",
    "last_run_finished_at",
    "last_run_started_at",
    "last_run_status",
    "next_start",
    "total_crashes",
    "total_failures",
    "total_runs",
    "total_successes",
)

EXCLUSIONS = {
    "schemas": ["information_schema", "pg_catalog", "pg_toast", "pg_*", "_timescaledb_*"],
    "objects": ["wes_sys.alembic_version"],
    "ownership": ["extension-owned custom objects"],
    "not_available": ["timescaledb_information.jobs.timezone"],
    "timescaledb_volatile_fields": list(TIMESCALEDB_VOLATILE_FIELDS),
}

_SCHEMA_FILTER = """
namespace.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
AND namespace.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'
AND namespace.nspname NOT LIKE '\\_timescaledb\\_%' ESCAPE '\\'
"""


def _with_schema_filter(query: str) -> str:
    return query.replace("__SCHEMA_FILTER__", _SCHEMA_FILTER)


CATALOG_QUERIES: dict[str, str] = {
    "server": """
        /* catalog:server */
        SELECT
            current_setting('server_version') AS server_version,
            current_setting('server_version_num') AS server_version_num
        ORDER BY server_version, server_version_num
    """,
    "extensions": """
        /* catalog:extensions */
        SELECT extension.extname AS name,
               extension.extversion AS version,
               namespace.nspname AS schema
        FROM pg_extension AS extension
        JOIN pg_namespace AS namespace ON namespace.oid = extension.extnamespace
        ORDER BY extension.extname, extension.extversion, namespace.nspname
    """,
    "schemas": _with_schema_filter("""
        /* catalog:schemas */
        SELECT namespace.nspname AS name
        FROM pg_namespace AS namespace
        WHERE __SCHEMA_FILTER__
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_namespace'::regclass
                AND dependency.objid = namespace.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY namespace.nspname
    """),
    "tables": _with_schema_filter("""
        /* catalog:tables */
        SELECT namespace.nspname AS schema,
               relation.relname AS name,
               CASE relation.relkind WHEN 'p' THEN 'partitioned_table' ELSE 'table' END AS kind
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE relation.relkind IN ('r', 'p')
          AND __SCHEMA_FILTER__
          AND NOT (namespace.nspname = 'wes_sys' AND relation.relname = 'alembic_version')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY namespace.nspname, relation.relname, relation.relkind
    """),
    "columns": _with_schema_filter("""
        /* catalog:columns */
        SELECT namespace.nspname AS schema,
               relation.relname AS table,
               attribute.attnum AS ordinal_position,
               attribute.attname AS name,
               format_type(attribute.atttypid, attribute.atttypmod) AS format_type,
               NOT attribute.attnotnull AS nullable,
               (attribute_default.oid IS NOT NULL) AS default_present,
               pg_get_expr(attribute_default.adbin, attribute_default.adrelid) AS default_expression,
               attribute.attidentity AS identity,
               attribute.attgenerated AS generated,
               CASE
                   WHEN attribute.attcollation = 0 THEN NULL
                   ELSE format('%I.%I', collation_namespace.nspname, collation_record.collname)
               END AS "collation"
        FROM pg_attribute AS attribute
        JOIN pg_class AS relation ON relation.oid = attribute.attrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_attrdef AS attribute_default
               ON attribute_default.adrelid = attribute.attrelid
              AND attribute_default.adnum = attribute.attnum
        LEFT JOIN pg_collation AS collation_record ON collation_record.oid = attribute.attcollation
        LEFT JOIN pg_namespace AS collation_namespace ON collation_namespace.oid = collation_record.collnamespace
        WHERE relation.relkind IN ('r', 'p')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND __SCHEMA_FILTER__
          AND NOT (namespace.nspname = 'wes_sys' AND relation.relname = 'alembic_version')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY namespace.nspname, relation.relname, attribute.attnum
    """),
    "constraints": _with_schema_filter("""
        /* catalog:constraints */
        SELECT namespace.nspname AS schema,
               relation.relname AS table,
               constraint_record.conname AS name,
               CASE constraint_record.contype
                   WHEN 'p' THEN 'primary_key'
                   WHEN 'f' THEN 'foreign_key'
                   WHEN 'u' THEN 'unique'
                   WHEN 'c' THEN 'check'
                   WHEN 'x' THEN 'exclude'
                   ELSE constraint_record.contype::text
               END AS type,
               pg_get_constraintdef(constraint_record.oid, true) AS definition
        FROM pg_constraint AS constraint_record
        JOIN pg_class AS relation ON relation.oid = constraint_record.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE __SCHEMA_FILTER__
          AND NOT (namespace.nspname = 'wes_sys' AND relation.relname = 'alembic_version')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_constraint'::regclass
                AND dependency.objid = constraint_record.oid
                AND dependency.deptype = 'e'
          )
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY namespace.nspname, relation.relname, constraint_record.conname
    """),
    "indexes": _with_schema_filter("""
        /* catalog:indexes */
        SELECT namespace.nspname AS schema,
               relation.relname AS table,
               index_relation.relname AS name,
               pg_get_indexdef(index_record.indexrelid) AS definition,
               access_method.amname AS access_method,
               index_record.indisunique AS unique,
               index_record.indisprimary AS primary,
               index_record.indisexclusion AS exclusion,
               index_record.indisvalid AS valid,
               index_record.indisready AS ready,
               ARRAY(
                   SELECT pg_get_indexdef(index_record.indexrelid, position, true)
                   FROM generate_series(1, index_record.indnkeyatts) AS position
                   ORDER BY position
               ) AS key_expressions,
               ARRAY(
                   SELECT pg_get_indexdef(index_record.indexrelid, position, true)
                   FROM generate_series(index_record.indnkeyatts + 1, index_record.indnatts) AS position
                   ORDER BY position
               ) AS include_expressions,
               ARRAY(
                   SELECT format('%I.%I', opclass_namespace.nspname, opclass.opcname)
                   FROM unnest(index_record.indclass::oid[]) WITH ORDINALITY AS item(opclass_oid, position)
                   JOIN pg_opclass AS opclass ON opclass.oid = item.opclass_oid
                   JOIN pg_namespace AS opclass_namespace ON opclass_namespace.oid = opclass.opcnamespace
                   WHERE item.position <= index_record.indnkeyatts
                   ORDER BY item.position
               ) AS opclasses,
               ARRAY(
                   SELECT CASE
                              WHEN item.collation_oid = 0 THEN NULL
                              ELSE format('%I.%I', collation_namespace.nspname, collation_record.collname)
                          END
                   FROM unnest(index_record.indcollation::oid[]) WITH ORDINALITY AS item(collation_oid, position)
                   LEFT JOIN pg_collation AS collation_record ON collation_record.oid = item.collation_oid
                   LEFT JOIN pg_namespace AS collation_namespace
                          ON collation_namespace.oid = collation_record.collnamespace
                   WHERE item.position <= index_record.indnkeyatts
                   ORDER BY item.position
               ) AS collations,
               pg_get_expr(index_record.indpred, index_record.indrelid, true) AS predicate
        FROM pg_index AS index_record
        JOIN pg_class AS relation ON relation.oid = index_record.indrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        JOIN pg_class AS index_relation ON index_relation.oid = index_record.indexrelid
        JOIN pg_am AS access_method ON access_method.oid = index_relation.relam
        WHERE __SCHEMA_FILTER__
          AND NOT (namespace.nspname = 'wes_sys' AND relation.relname = 'alembic_version')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = index_relation.oid
                AND dependency.deptype = 'e'
          )
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY namespace.nspname, relation.relname, index_relation.relname
    """),
    "routines": _with_schema_filter("""
        /* catalog:routines */
        SELECT namespace.nspname AS schema,
               routine.proname AS name,
               CASE routine.prokind WHEN 'p' THEN 'procedure' ELSE 'function' END AS kind,
               pg_get_function_identity_arguments(routine.oid) AS identity_arguments,
               pg_get_functiondef(routine.oid) AS definition
        FROM pg_proc AS routine
        JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
        WHERE routine.prokind IN ('f', 'p')
          AND __SCHEMA_FILTER__
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_proc'::regclass
                AND dependency.objid = routine.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY namespace.nspname, routine.proname,
                 pg_get_function_identity_arguments(routine.oid), routine.prokind
    """),
    "triggers": _with_schema_filter("""
        /* catalog:triggers */
        SELECT namespace.nspname AS schema,
               relation.relname AS table,
               trigger_record.tgname AS name,
               pg_get_triggerdef(trigger_record.oid, true) AS definition
        FROM pg_trigger AS trigger_record
        JOIN pg_class AS relation ON relation.oid = trigger_record.tgrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE NOT trigger_record.tgisinternal
          AND __SCHEMA_FILTER__
          AND NOT (namespace.nspname = 'wes_sys' AND relation.relname = 'alembic_version')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_trigger'::regclass
                AND dependency.objid = trigger_record.oid
                AND dependency.deptype = 'e'
          )
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY namespace.nspname, relation.relname, trigger_record.tgname
    """),
    "views": _with_schema_filter("""
        /* catalog:views */
        SELECT namespace.nspname AS schema,
               relation.relname AS name,
               CASE relation.relkind WHEN 'm' THEN 'materialized_view' ELSE 'view' END AS kind,
               pg_get_viewdef(relation.oid, true) AS definition
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE relation.relkind IN ('v', 'm')
          AND __SCHEMA_FILTER__
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY namespace.nspname, relation.relname, relation.relkind
    """),
    "timescaledb_hypertables": """
        /* catalog:timescaledb_hypertables */
        SELECT hypertable_schema, hypertable_name, owner,
               num_dimensions, compression_enabled
        FROM timescaledb_information.hypertables
        ORDER BY hypertable_schema, hypertable_name
    """,
    "timescaledb_dimensions": """
        /* catalog:timescaledb_dimensions */
        SELECT hypertable_schema, hypertable_name, dimension_number,
               column_name, column_type, dimension_type,
               time_interval::text AS time_interval,
               integer_interval, integer_now_func, num_partitions
        FROM timescaledb_information.dimensions
        ORDER BY hypertable_schema, hypertable_name, dimension_number
    """,
    "timescaledb_continuous_aggregates": """
        /* catalog:timescaledb_continuous_aggregates */
        SELECT hypertable_schema, hypertable_name,
               view_schema, view_name, view_owner,
               materialized_only, compression_enabled,
               materialization_hypertable_schema, materialization_hypertable_name,
               view_definition
        FROM timescaledb_information.continuous_aggregates
        ORDER BY view_schema, view_name
    """,
    "timescaledb_jobs": r"""
        /* catalog:timescaledb_jobs */
        SELECT application_name,
               schedule_interval::text AS schedule_interval,
               max_runtime::text AS max_runtime,
               max_retries,
               retry_period::text AS retry_period,
               proc_schema, proc_name, owner,
               scheduled, fixed_schedule,
               initial_start::text AS initial_start,
               hypertable_schema, hypertable_name,
               check_schema, check_name,
               config
        FROM timescaledb_information.jobs AS job
        WHERE (
            (job.hypertable_schema IS NOT NULL
             AND job.hypertable_schema NOT LIKE '\_timescaledb\_%' ESCAPE '\')
            OR (job.proc_schema NOT IN ('pg_catalog', 'information_schema')
                AND job.proc_schema NOT LIKE '\_timescaledb\_%' ESCAPE '\')
            OR EXISTS (
                SELECT 1
                FROM timescaledb_information.continuous_aggregates AS aggregate
                WHERE aggregate.materialization_hypertable_schema = job.hypertable_schema
                  AND aggregate.materialization_hypertable_name = job.hypertable_name
            )
        )
        ORDER BY hypertable_schema, hypertable_name, proc_schema, proc_name,
                 application_name, schedule_interval, config::text
    """,
}

_DATABASE_HEAD_QUERY = "SELECT version_num FROM wes_sys.alembic_version"


class CatalogCollectionError(RuntimeError):
    """Catalog 采集失败。"""


class ManifestMismatch(AssertionError):
    """Live catalog 与冻结 manifest 不一致。"""


def normalize_json_value(value: Any) -> Any:
    """把 asyncpg/catalog 值规范化为稳定 JSON 值。"""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, Mapping) or hasattr(value, "items"):
        return {str(key): normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        normalized = [normalize_json_value(item) for item in value]
        return sorted(normalized, key=_canonical_sort_key)
    if isinstance(value, Sequence):
        return [normalize_json_value(item) for item in value]
    raise CatalogCollectionError("catalog 包含无法规范化的值")


def canonical_json_bytes(payload: Any) -> bytes:
    """生成带尾换行的 canonical UTF-8 JSON bytes。"""

    normalized = normalize_json_value(payload)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _canonical_sort_key(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _stable_rows(rows: Sequence[Any]) -> list[dict[str, Any]]:
    normalized = [normalize_json_value(row) for row in rows]
    if any(not isinstance(row, dict) for row in normalized):
        raise CatalogCollectionError("catalog query 必须返回 record mapping")
    return sorted(normalized, key=_canonical_sort_key)


async def collect_postgresql_catalog(connection: Any, *, database: str) -> dict[str, Any]:
    """直接从 live PostgreSQL 读取一次稳定 catalog manifest。"""

    collected: dict[str, list[dict[str, Any]]] = {}
    timescaledb_present = False
    for section, query in CATALOG_QUERIES.items():
        if section.startswith("timescaledb_") and not timescaledb_present:
            collected[section] = []
            continue
        try:
            rows = await connection.fetch(query)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise CatalogCollectionError(f"catalog section 读取失败: {section} ({type(exc).__name__}: {exc})") from exc
        collected[section] = _stable_rows(rows)
        if section == "extensions":
            timescaledb_present = any(row.get("name") == "timescaledb" for row in collected[section])
    return {
        "kind": "wes.schema.catalog.v1",
        "database": database,
        "exclusions": EXCLUSIONS,
        "catalog": collected,
    }


def load_json_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ManifestMismatch(f"fixture must contain one JSON object: {path.name}")
    return payload


def validate_raw_manifest_bytes(payload: bytes) -> None:
    digest = hashlib.sha256(payload).hexdigest()
    if digest != RAW_MANIFEST_SHA256:
        raise ManifestMismatch(f"raw catalog hash mismatch: {digest}")
    if canonical_json_bytes(json.loads(payload)) != payload:
        raise ManifestMismatch("raw catalog is not canonical JSON")


def validate_disposition_bytes(payload: bytes) -> None:
    digest = hashlib.sha256(payload).hexdigest()
    if digest != DISPOSITION_SHA256:
        raise ManifestMismatch(f"disposition hash mismatch: {digest}")
    disposition = json.loads(payload)
    if not isinstance(disposition, dict) or not isinstance(disposition.get("tables"), list):
        raise ManifestMismatch("disposition must contain a tables list")
    actual = {
        entry["identity"]: (
            entry["disposition"],
            entry["catalog_only"],
            entry.get("target"),
        )
        for entry in disposition["tables"]
    }
    if actual != EXPECTED_DISPOSITION_TABLES or len(disposition["tables"]) != len(EXPECTED_DISPOSITION_TABLES):
        raise ManifestMismatch("disposition identity map differs from frozen review")


def validate_transition_disposition_bytes(
    payload: bytes,
    *,
    old_derived_manifest: Mapping[str, Any],
    final_manifest: Mapping[str, Any],
) -> None:
    digest = hashlib.sha256(payload).hexdigest()
    if digest != TRANSITION_DISPOSITION_SHA256:
        raise ManifestMismatch(f"transition disposition hash mismatch: {digest}")
    if canonical_json_bytes(json.loads(payload)) != payload:
        raise ManifestMismatch("transition disposition is not canonical JSON")
    disposition = json.loads(payload)
    expected_keys = {
        "candidate_manifest_sha256",
        "differences",
        "kind",
        "old_derived_manifest_sha256",
        "raw_manifest_sha256",
        "summary",
        "unresolved",
    }
    if not isinstance(disposition, dict) or set(disposition) != expected_keys:
        raise ManifestMismatch("transition disposition top-level shape differs")
    actual_old_derived_sha256 = hashlib.sha256(canonical_json_bytes(old_derived_manifest)).hexdigest()
    actual_final_sha256 = hashlib.sha256(canonical_json_bytes(final_manifest)).hexdigest()
    if (
        disposition["kind"] != "wes.schema.catalog-transition-disposition.v1"
        or disposition["raw_manifest_sha256"] != RAW_MANIFEST_SHA256
        or actual_old_derived_sha256 != OLD_DERIVED_MANIFEST_SHA256
        or disposition["old_derived_manifest_sha256"] != actual_old_derived_sha256
        or actual_final_sha256 != FINAL_MANIFEST_SHA256
        or disposition["candidate_manifest_sha256"] != actual_final_sha256
    ):
        raise ManifestMismatch("transition disposition provenance differs")
    differences = disposition["differences"]
    if not isinstance(differences, list) or disposition.get("unresolved") != 0:
        raise ManifestMismatch("transition disposition must contain resolved differences")
    identities = [(entry.get("section"), entry.get("identity")) for entry in differences if isinstance(entry, dict)]
    if len(identities) != len(differences) or len(set(identities)) != len(differences):
        raise ManifestMismatch("transition disposition identities must be unique")
    actual_summary = {
        section: sum(entry.get("section") == section for entry in differences)
        for section in ("columns", "constraints", "indexes", "tables")
    }
    if disposition["summary"] != actual_summary or sum(actual_summary.values()) != len(differences):
        raise ManifestMismatch("transition disposition summary differs")
    allowed_shapes = {
        "RETAIN_CURRENT_COLUMN_ORDER": (True, True),
        "CORRECT_REFERENCE_TYPE": (True, True),
        "RETAIN_CURRENT_JSON_CONTRACT": (True, True),
        "RETAIN_CURRENT_DEFAULT_CONTRACT": (True, True),
        "RENAME_TO_CURRENT_CONVENTION": None,
        "REMOVE_BUSINESS_SPECIFIC_INVARIANT": (True, False),
        "RETAIN_CURRENT_INVARIANT": (False, True),
        "CORRECT_REFERENCE_INDEX_TYPE": (True, True),
        "REMOVE_REDUNDANT_PRIMARY_KEY_INDEX": (True, False),
    }
    for entry in differences:
        disposition_name = entry.get("disposition")
        shape = (entry.get("old") is not None, entry.get("new") is not None)
        if (
            set(entry) != {"disposition", "identity", "new", "old", "reason", "section", "sources"}
            or not isinstance(entry["sources"], list)
            or not entry["sources"]
            or not isinstance(entry["reason"], str)
            or not entry["reason"]
            or disposition_name not in allowed_shapes
            or (allowed_shapes[disposition_name] is not None and shape != allowed_shapes[disposition_name])
            or (disposition_name == "RENAME_TO_CURRENT_CONVENTION" and shape not in {(True, False), (False, True)})
        ):
            raise ManifestMismatch("transition disposition entry is incomplete")


async def assert_database_head(connection: Any, expected_head: str) -> None:
    actual_head = await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version")
    if actual_head != expected_head:
        raise ManifestMismatch(f"database Alembic head mismatch: expected {expected_head}, got {actual_head}")


def _table_identity(row: Mapping[str, Any], *, name_field: str) -> str:
    return f"{row['schema']}.{row[name_field]}"


def _disposed_identities(disposition: Mapping[str, Any]) -> set[str]:
    entries = disposition.get("tables")
    if not isinstance(entries, list):
        raise ManifestMismatch("schema disposition tables must be a list")
    identities = [entry.get("identity") for entry in entries if isinstance(entry, dict)]
    if len(identities) != len(entries) or len(set(identities)) != len(entries):
        raise ManifestMismatch("schema disposition identities must be unique strings")
    return {
        entry["identity"]
        for entry in entries
        if entry.get("disposition") in {"FINAL_DELETE", "FINAL_DELETE_AFTER_SUCCESSOR"}
    }


def _without_disposed_tables(manifest: Mapping[str, Any], disposed: set[str]) -> dict[str, Any]:
    filtered = copy.deepcopy(manifest)
    table_bound_sections = {
        "tables": "name",
        "columns": "table",
        "constraints": "table",
        "indexes": "table",
        "triggers": "table",
    }
    for section, name_field in table_bound_sections.items():
        filtered["catalog"][section] = [
            row for row in manifest["catalog"][section] if _table_identity(row, name_field=name_field) not in disposed
        ]
    return filtered


def derive_final_manifest(raw: Mapping[str, Any], disposition: Mapping[str, Any]) -> dict[str, Any]:
    """只按已评审 table disposition 从 immutable raw catalog 派生最终 manifest。"""

    disposed = _disposed_identities(disposition)
    raw_table_identities = {_table_identity(row, name_field="name") for row in raw["catalog"]["tables"]}
    if not disposed <= raw_table_identities:
        missing = sorted(disposed - raw_table_identities)
        raise ManifestMismatch(f"disposition identities absent from raw catalog: {missing}")
    return _without_disposed_tables(raw, disposed)


def disposed_table_identities(raw: Mapping[str, Any], final: Mapping[str, Any]) -> set[str]:
    raw_tables = {_table_identity(row, name_field="name") for row in raw["catalog"]["tables"]}
    final_tables = {_table_identity(row, name_field="name") for row in final["catalog"]["tables"]}
    return raw_tables - final_tables


def _transition_identity(section: str, row: Mapping[str, Any]) -> str:
    if section == "tables":
        return f"{row['schema']}.{row['name']}"
    if section == "columns":
        return f"{row['schema']}.{row['table']}.{row['name']}"
    if section == "constraints":
        return f"{row['schema']}.{row['table']}.{row['type']}.{row['name']}"
    if section == "indexes":
        return f"{row['schema']}.{row['table']}.{row['name']}"
    raise ManifestMismatch(f"unsupported transition section: {section}")


def catalog_transition_differences(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[dict[str, Any]]:
    """枚举 old-derived 与 current live catalog 之间全部已允许的逐项差异。"""

    transition_sections = {"columns", "constraints", "indexes", "tables"}
    platform_sections = {"extensions", "server"}
    if {key: value for key, value in old.items() if key != "catalog"} != {
        key: value for key, value in new.items() if key != "catalog"
    }:
        raise ManifestMismatch("catalog transition differs outside catalog sections")
    old_catalog = old["catalog"]
    new_catalog = new["catalog"]
    if set(old_catalog) != set(new_catalog):
        raise ManifestMismatch("catalog transition section set differs")
    for section in set(old_catalog) - transition_sections - platform_sections:
        if old_catalog[section] != new_catalog[section]:
            raise ManifestMismatch(f"catalog transition differs in unreviewed section: {section}")

    differences: list[dict[str, Any]] = []
    for section in sorted(transition_sections):
        old_rows = {_transition_identity(section, row): row for row in old_catalog[section]}
        new_rows = {_transition_identity(section, row): row for row in new_catalog[section]}
        for identity in sorted(set(old_rows) | set(new_rows)):
            old_row = old_rows.get(identity)
            new_row = new_rows.get(identity)
            if old_row != new_row:
                differences.append({"identity": identity, "new": new_row, "old": old_row, "section": section})
    return differences


def assert_reviewed_catalog_transition(
    old: Mapping[str, Any],
    final: Mapping[str, Any],
    transition_disposition: Mapping[str, Any],
) -> None:
    reviewed_differences = [
        {key: entry[key] for key in ("identity", "new", "old", "section")}
        for entry in transition_disposition["differences"]
    ]
    if catalog_transition_differences(old, final) != reviewed_differences:
        raise ManifestMismatch("catalog transition differs from reviewed disposition")


def assert_old_chain_manifest(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    if canonical_json_bytes(actual) != canonical_json_bytes(expected):
        raise ManifestMismatch("old-chain live catalog differs from immutable raw characterization")


def assert_final_manifest(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    disposition: Mapping[str, Any],
    *,
    allow_disposed_tables: bool = False,
) -> None:
    reviewed_disposed = _disposed_identities(disposition)
    normalized_actual = _without_disposed_tables(actual, reviewed_disposed)
    if canonical_json_bytes(normalized_actual) != canonical_json_bytes(expected):
        raise ManifestMismatch("unregistered catalog difference outside reviewed disposition")
    actual_tables = {_table_identity(row, name_field="name") for row in actual["catalog"]["tables"]}
    present_disposed = actual_tables & reviewed_disposed
    if present_disposed and not allow_disposed_tables:
        raise ManifestMismatch("reviewed disposed tables still present: " + ", ".join(sorted(present_disposed)))
