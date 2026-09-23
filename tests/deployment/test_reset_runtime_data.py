"""运行数据 reset 脚本的安全合同。"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.data import reset_runtime_data as reset_module


class _Rows:
    def __init__(self, rows: list[tuple[str, str]] | None = None, *, scalar: int = 0, rowcount: int = 0) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def all(self) -> list[tuple[str, str]]:
        return self._rows

    def scalar_one(self) -> int:
        return self._scalar

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(
        self,
        *,
        catalog: list[tuple[str, str]] | None = None,
        row_count: int = 3,
        fail_on_sql: str | None = None,
        fail_commit: bool = False,
    ) -> None:
        self.catalog = catalog or [(target.schema, target.table) for target in reset_module.RUNTIME_TABLES]
        self.row_count = row_count
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on_sql = fail_on_sql
        self.fail_commit = fail_commit

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if self.fail_on_sql is not None and self.fail_on_sql in sql:
            raise RuntimeError(f"simulated failure: {self.fail_on_sql}")
        if "information_schema.tables" in sql:
            return _Rows(self.catalog)
        if sql.startswith("SELECT count(*)"):
            return _Rows(scalar=self.row_count)
        return _Rows(rowcount=2)

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("simulated commit failure")

    async def rollback(self) -> None:
        self.rollbacks += 1


class _TransportResetSession(_FakeSession):
    def __init__(
        self,
        *,
        task_row: tuple[str, str] | None = ("transport-test", "RECONCILING"),
        evidence_count: int = 0,
        fail_on_sql: str | None = None,
        fail_commit: bool = False,
        task_delete_rowcount: int | None = None,
    ) -> None:
        super().__init__(fail_on_sql=fail_on_sql, fail_commit=fail_commit)
        self.task_row = task_row
        self.evidence_count = evidence_count
        self.task_delete_rowcount = task_delete_rowcount

    async def scalar(self, statement, parameters=None):
        self.statements.append(str(statement))

    async def execute(self, statement, parameters=None):
        sql = str(statement)
        self.statements.append(sql)
        if self.fail_on_sql is not None and self.fail_on_sql in sql:
            raise RuntimeError(f"simulated failure: {self.fail_on_sql}")
        if sql.startswith("SELECT transport_task_id, status"):
            return _Rows([self.task_row] if self.task_row is not None else [])
        if sql.startswith("SELECT count(*) FROM wes_biz.transport_evidence"):
            return _Rows(scalar=self.evidence_count)
        if sql.startswith("SELECT count(*) FROM wes_biz.transport_"):
            return _Rows(scalar=1)
        if sql.startswith("DELETE FROM wes_biz.transport_tasks"):
            rowcount = self.task_delete_rowcount
            if rowcount is None:
                rowcount = 1 if self.task_row is not None else 0
            return _Rows(rowcount=rowcount)
        return _Rows(rowcount=1)


def _is_mutation(statement: str) -> bool:
    return statement.lstrip().upper().startswith(("TRUNCATE", "UPDATE", "INSERT", "DELETE"))


def test_runtime_targets_use_explicit_schema_identity_and_exclude_retired_schema_tables() -> None:
    identities = {(target.schema, target.table) for target in reset_module.RUNTIME_TABLES}

    assert {
        ("wes_biz", "inbound_evidences"),
        ("wes_biz", "manual_picking_passages"),
        ("wes_biz", "picking_tasks"),
        ("wes_biz", "position_projections"),
        ("wes_biz", "transport_decision_bindings"),
        ("wes_biz", "wms_confirmations"),
        ("wes_biz", "transport_tasks"),
        ("wes_biz", "workline_runtime_status_projections"),
        ("wes_sys", "api_access_logs"),
    }.issubset(identities)
    assert {
        ("wes_biz", "line_run_epochs"),
        ("wes_biz", "line_run_epoch_device_bindings"),
        ("wes_biz", "line_run_epoch_position_bindings"),
    }.isdisjoint(identities)
    assert identities.isdisjoint(
        {
            ("wes_biz", "ng_return_items"),
            ("wes_biz", "runtime_holds"),
            ("wes_biz", "system_outbox"),
            ("wes_biz", "wms_call_evidence"),
            ("wes_biz", "wms_circuit_breaker_state"),
            ("wes_biz", "workline_bin_cell_reservations"),
            ("wes_biz", "workline_diagnostics"),
            ("wes_biz", "workline_dispatch_attempts"),
            ("wes_biz", "runtime_inbox"),
        }
    )
    assert all(target.schema and target.table for target in reset_module.RUNTIME_TABLES)
    assert identities.isdisjoint({(target.schema, target.table) for target in reset_module.MASTER_DATA_TABLES})


def test_redis_targets_clear_process_databases_but_scope_application_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reset_module,
        "settings",
        SimpleNamespace(
            POSTGRES_DB="wes_integration",
            REDIS_URL="redis://localhost:6379/0",
            CELERY_BROKER="redis://localhost:6379/1",
            CELERY_BACKEND="redis://localhost:6379/2",
        ),
    )

    targets = {target.roles: target for target in reset_module._redis_targets()}

    assert targets[("application-cache",)].database == 0
    assert targets[("application-cache",)].pattern.startswith("app:")
    assert targets[("application-cache",)].pattern.endswith(":*")
    assert targets[("celery-broker",)].pattern == "*"
    assert targets[("celery-result-backend",)].pattern == "*"


@pytest.mark.asyncio
async def test_redis_reset_deletes_only_scanned_process_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reset_module,
        "settings",
        SimpleNamespace(
            POSTGRES_DB="wes_integration",
            REDIS_URL="redis://localhost:6379/0",
            CELERY_BROKER="redis://localhost:6379/1",
            CELERY_BACKEND="redis://localhost:6379/2",
        ),
    )
    keys_by_url = {
        "redis://localhost:6379/0": {"app:scoped:cache", "auth:user_session:1"},
        "redis://localhost:6379/1": {"celery", "unacked"},
        "redis://localhost:6379/2": {"celery-task-meta-1"},
    }

    class FakeRedis:
        def __init__(self, url: str) -> None:
            self.url = url

        async def scan_iter(self, *, match: str, count: int):
            del count
            for key in sorted(keys_by_url[self.url]):
                if fnmatch.fnmatch(key, match):
                    yield key

        async def delete(self, *keys: str) -> int:
            before = len(keys_by_url[self.url])
            keys_by_url[self.url].difference_update(keys)
            return before - len(keys_by_url[self.url])

        async def aclose(self) -> None:
            return None

    def from_url(url: str, **_kwargs):
        return FakeRedis(url)

    monkeypatch.setattr(reset_module.Redis, "from_url", staticmethod(from_url))
    cache_target = next(target for target in reset_module._redis_targets() if target.database == 0)
    scoped_key = cache_target.pattern.removesuffix("*") + "cache"
    keys_by_url[cache_target.url] = {scoped_key, "auth:user_session:1"}

    summary = await reset_module.reset_redis_process_data(apply=True)

    assert sum(target["deleted"] for target in summary) == 4
    assert keys_by_url["redis://localhost:6379/0"] == {"auth:user_session:1"}
    assert keys_by_url["redis://localhost:6379/1"] == set()
    assert keys_by_url["redis://localhost:6379/2"] == set()


def test_runtime_targets_do_not_own_retired_execution_tables() -> None:
    identities = {target.identity for target in reset_module.RUNTIME_TABLES}

    retired_tables = {
        "wes_biz.handling_operation_moves",
        "wes_biz.handling_operation_steps",
        "wes_biz.handling_operations",
        "wes_biz.rack_operations",
        "wes_biz.rack_tasks",
        "wes_biz.smt_inbound_handoff_demands",
        "wes_biz.smt_inbound_handoff_source_items",
    }
    assert identities.isdisjoint(retired_tables)


def test_validate_table_sets_rejects_master_data_target() -> None:
    master_target = next(iter(reset_module.MASTER_DATA_TABLES))

    with pytest.raises(RuntimeError, match="主数据"):
        reset_module._validate_table_sets((*reset_module.RUNTIME_TABLES, master_target))


def test_validate_table_sets_rejects_duplicate_target() -> None:
    duplicate = reset_module.RUNTIME_TABLES[0]

    with pytest.raises(RuntimeError, match="重复目标"):
        reset_module._validate_table_sets((*reset_module.RUNTIME_TABLES, duplicate))


def test_mock_wms_reset_url_ignores_retired_sync_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WES_MOCK_WMS_URL", raising=False)
    monkeypatch.setenv("WMS_SYNC_" + "BASE_URL", "http://retired.example:9999/api/wms")

    assert reset_module._mock_wms_reset_url() == "http://localhost:8011/debug/reset"


@pytest.mark.asyncio
async def test_dry_run_lists_schema_qualified_targets_without_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(row_count=4)
    reset_mock = AsyncMock()
    monkeypatch.setattr(reset_module, "reset_mock_wms", reset_mock)

    summary = await reset_module.reset_runtime_data(
        session,
        apply=False,
        include_audit_logs=False,
        reset_mocks=True,
    )

    assert summary.mode == "dry-run"
    assert {entry["table"] for entry in summary.truncated} == {
        f"{target.schema}.{target.table}" for target in reset_module.RUNTIME_TABLES
    }
    assert not any(_is_mutation(statement) for statement in session.statements)
    assert session.commits == 0
    reset_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_rejects_missing_target_before_mock_or_database_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    missing_target = reset_module.RUNTIME_TABLES[0]
    catalog = [(target.schema, target.table) for target in reset_module.RUNTIME_TABLES]
    catalog.remove((missing_target.schema, missing_target.table))
    session = _FakeSession(catalog=catalog)
    reset_mock = AsyncMock()
    monkeypatch.setattr(reset_module, "reset_mock_wms", reset_mock)

    with pytest.raises(RuntimeError, match=rf"目标表不存在.*{missing_target.identity}"):
        await reset_module.reset_runtime_data(
            session,
            apply=True,
            include_audit_logs=False,
            reset_mocks=True,
        )

    reset_mock.assert_not_awaited()
    assert not any(_is_mutation(statement) for statement in session.statements)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_apply_rejects_schema_mismatch_before_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    missing_target = reset_module.RUNTIME_TABLES[0]
    wrong_schema = "wes_sys" if missing_target.schema != "wes_sys" else "wes_biz"
    catalog = [(target.schema, target.table) for target in reset_module.RUNTIME_TABLES]
    catalog.remove((missing_target.schema, missing_target.table))
    catalog.append((wrong_schema, missing_target.table))
    session = _FakeSession(catalog=catalog)
    reset_mock = AsyncMock()
    monkeypatch.setattr(reset_module, "reset_mock_wms", reset_mock)

    with pytest.raises(
        RuntimeError,
        match=rf"schema 不匹配.*{missing_target.identity}.*{wrong_schema}\.{missing_target.table}",
    ):
        await reset_module.reset_runtime_data(
            session,
            apply=True,
            include_audit_logs=False,
            reset_mocks=True,
        )

    reset_mock.assert_not_awaited()
    assert not any(_is_mutation(statement) for statement in session.statements)


@pytest.mark.asyncio
async def test_mock_wms_failure_is_fail_closed_before_database_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    reset_mock = AsyncMock(side_effect=RuntimeError("mock unavailable"))
    monkeypatch.setattr(reset_module, "reset_mock_wms", reset_mock)

    with pytest.raises(RuntimeError, match=r"Mock WMS 重置失败.*mock unavailable"):
        await reset_module.reset_runtime_data(
            session,
            apply=True,
            include_audit_logs=False,
            reset_mocks=True,
        )

    reset_mock.assert_awaited_once()
    assert not any(_is_mutation(statement) for statement in session.statements)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_no_reset_mocks_is_the_only_apply_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    reset_mock = AsyncMock()
    monkeypatch.setattr(reset_module, "reset_mock_wms", reset_mock)

    summary = await reset_module.reset_runtime_data(
        session,
        apply=True,
        include_audit_logs=False,
        reset_mocks=False,
    )

    assert summary.mode == "apply"
    reset_mock.assert_not_awaited()
    assert any(statement.lstrip().upper().startswith("TRUNCATE") for statement in session.statements)
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_on_sql", "fail_commit"),
    (
        ("TRUNCATE ", False),
        ("INSERT INTO wes_biz.workline_runtime_status_projections", False),
        (None, True),
    ),
)
async def test_apply_rolls_back_each_database_or_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
    fail_on_sql: str | None,
    fail_commit: bool,
) -> None:
    session = _FakeSession(fail_on_sql=fail_on_sql, fail_commit=fail_commit)
    monkeypatch.setattr(reset_module, "reset_mock_wms", AsyncMock())

    with pytest.raises(RuntimeError, match="simulated"):
        await reset_module.reset_runtime_data(
            session,
            apply=True,
            include_audit_logs=False,
            reset_mocks=False,
        )

    assert session.rollbacks == 1


def test_transport_task_reset_requires_force_for_production_profile() -> None:
    assert reset_module._transport_task_reset_allowed("dev", force=False) is True
    assert reset_module._transport_task_reset_allowed("test", force=False) is True
    assert reset_module._transport_task_reset_allowed("prod", force=False) is False
    assert reset_module._transport_task_reset_allowed("prod", force=True) is True


def test_transport_task_id_parser_rejects_blank_before_database_routing() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match=r"1\.\.80"):
        reset_module._parse_transport_task_id("   ")
    with pytest.raises(argparse.ArgumentTypeError, match=r"1\.\.80"):
        reset_module._parse_transport_task_id("invalid\x00id")
    with pytest.raises(argparse.ArgumentTypeError, match=r"1\.\.80"):
        reset_module._parse_transport_task_id("x" * 81)
    assert reset_module._parse_transport_task_id(" transport-test ") == "transport-test"


@pytest.mark.asyncio
async def test_transport_task_reset_function_rejects_nul_before_database_routing() -> None:
    session = _TransportResetSession()

    with pytest.raises(ValueError, match="non-NUL"):
        await reset_module.reset_transport_task_data(
            session,
            transport_task_id="invalid\x00id",
            apply=False,
        )

    assert session.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize("apply", [False, True])
async def test_transport_task_reset_rejects_missing_task_without_mutation(apply: bool) -> None:
    session = _TransportResetSession(task_row=None)

    with pytest.raises(RuntimeError, match="TransportTask 不存在"):
        await reset_module.reset_transport_task_data(
            session,
            transport_task_id="missing-transport-task",
            apply=apply,
        )

    assert not any(_is_mutation(statement) for statement in session.statements)
    assert session.commits == 0
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_transport_task_reset_dry_run_reports_exact_aggregate_without_mutation() -> None:
    session = _TransportResetSession()

    summary = await reset_module.reset_transport_task_data(
        session,
        transport_task_id="transport-test",
        apply=False,
    )

    assert summary.mode == "dry-run"
    assert summary.transport_task_id == "transport-test"
    assert summary.status == "RECONCILING"
    assert summary.rows_before == {
        "wes_biz.transport_callback_receipts": 1,
        "wes_biz.transport_evidence": 0,
        "wes_biz.transport_debug_position_projections": 1,
        "wes_biz.transport_members": 1,
        "wes_biz.transport_tasks": 1,
    }
    assert not any(_is_mutation(statement) for statement in session.statements)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_transport_task_reset_apply_deletes_children_then_task_in_one_commit() -> None:
    session = _TransportResetSession()

    summary = await reset_module.reset_transport_task_data(
        session,
        transport_task_id="transport-test",
        apply=True,
    )

    deletes = [statement for statement in session.statements if statement.startswith("DELETE FROM")]
    assert deletes == [
        "DELETE FROM wes_biz.transport_callback_receipts WHERE response_data_json ->> 'transport_task_id' = :transport_task_id",
        "DELETE FROM wes_biz.transport_evidence WHERE transport_task_id = :transport_task_id",
        "DELETE FROM wes_biz.transport_debug_position_projections WHERE source_transport_task_id = :transport_task_id",
        "DELETE FROM wes_biz.transport_members WHERE transport_task_id = :transport_task_id",
        "DELETE FROM wes_biz.transport_tasks WHERE transport_task_id = :transport_task_id",
    ]
    assert summary.mode == "apply"
    active_run_queries = [statement for statement in session.statements if "transport_debug_runs" in statement]
    assert len(active_run_queries) == 1
    assert "FOR SHARE" not in active_run_queries[0]
    assert summary.deleted == {
        "wes_biz.transport_callback_receipts": 1,
        "wes_biz.transport_evidence": 1,
        "wes_biz.transport_debug_position_projections": 1,
        "wes_biz.transport_members": 1,
        "wes_biz.transport_tasks": 1,
    }
    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_transport_task_reset_allows_any_status_and_existing_evidence() -> None:
    session = _TransportResetSession(task_row=("transport-test", "ACCEPTED"), evidence_count=1)

    summary = await reset_module.reset_transport_task_data(
        session,
        transport_task_id="transport-test",
        apply=True,
    )

    assert summary.status == "ACCEPTED"
    assert summary.deleted["wes_biz.transport_evidence"] == 1
    assert session.commits == 1


@pytest.mark.asyncio
async def test_transport_task_reset_rolls_back_delete_failure() -> None:
    session = _TransportResetSession(fail_on_sql="DELETE FROM wes_biz.transport_members")

    with pytest.raises(RuntimeError, match="simulated"):
        await reset_module.reset_transport_task_data(
            session,
            transport_task_id="transport-test",
            apply=True,
        )

    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_transport_task_reset_rolls_back_invalid_task_delete_count() -> None:
    session = _TransportResetSession(task_delete_rowcount=0)

    with pytest.raises(RuntimeError, match="删除数量异常"):
        await reset_module.reset_transport_task_data(
            session,
            transport_task_id="transport-test",
            apply=True,
        )

    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_transport_task_reset_rolls_back_commit_failure() -> None:
    session = _TransportResetSession(fail_commit=True)

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await reset_module.reset_transport_task_data(
            session,
            transport_task_id="transport-test",
            apply=True,
        )

    assert session.commits == 1
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_targeted_cli_rejects_audit_log_flag_before_database_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db = AsyncMock()
    monkeypatch.setattr(reset_module, "init_db", init_db)
    monkeypatch.setattr(
        "sys.argv",
        [
            "reset_runtime_data.py",
            "--transport-task-id",
            "transport-test",
            "--include-audit-logs",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        await reset_module._amain()

    assert exc_info.value.code == 2
    init_db.assert_not_awaited()


def test_wrapper_preserves_current_flags_and_does_not_restore_retired_entrypoint() -> None:
    source = Path("scripts/data/reset_runtime_data.sh").read_text(encoding="utf-8")

    for flag in (
        "--yes",
        "--include-audit-logs",
        "--no-reset-mocks",
        "--no-reset-redis",
        "--force",
        "--json",
        "--transport-task-id",
    ):
        assert flag in source
    assert "workline_inbox" not in source
    assert "reset_runtime_data.py" in source


def test_wrapper_json_mode_keeps_stdout_machine_readable(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv_stub = bin_dir / "uv"
    uv_stub.write_text('#!/bin/sh\nprintf \'{"mode":"apply"}\\n\'\n', encoding="utf-8")
    uv_stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        ["/bin/bash", "scripts/data/reset_runtime_data.sh", "--yes", "--json"],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"mode": "apply"}
    assert "WES 运行时数据清理工具" in result.stderr


def test_wrapper_rejects_blank_transport_task_id_before_invoking_python(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "uv-invoked"
    uv_stub = bin_dir / "uv"
    uv_stub.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    uv_stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        ["/bin/bash", "scripts/data/reset_runtime_data.sh", "--transport-task-id", "   ", "--yes"],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "缺少任务 ID" in result.stderr
    assert not marker.exists()
