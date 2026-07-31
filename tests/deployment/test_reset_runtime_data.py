"""运行数据 reset 脚本的安全合同。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
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


def _is_mutation(statement: str) -> bool:
    return statement.lstrip().upper().startswith(("TRUNCATE", "UPDATE", "INSERT", "DELETE"))


def test_runtime_targets_use_explicit_schema_identity_and_retire_workline_inbox() -> None:
    identities = {(target.schema, target.table) for target in reset_module.RUNTIME_TABLES}

    assert ("wes_runtime", "runtime_inbox") in identities
    assert ("wes_biz", "workline_inbox") not in identities
    assert all(target.schema and target.table for target in reset_module.RUNTIME_TABLES)
    assert identities.isdisjoint({(target.schema, target.table) for target in reset_module.MASTER_DATA_TABLES})


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
    catalog = [(target.schema, target.table) for target in reset_module.RUNTIME_TABLES]
    catalog.remove(("wes_runtime", "runtime_inbox"))
    session = _FakeSession(catalog=catalog)
    reset_mock = AsyncMock()
    monkeypatch.setattr(reset_module, "reset_mock_wms", reset_mock)

    with pytest.raises(RuntimeError, match=r"目标表不存在.*wes_runtime\.runtime_inbox"):
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
    catalog = [(target.schema, target.table) for target in reset_module.RUNTIME_TABLES]
    catalog.remove(("wes_runtime", "runtime_inbox"))
    catalog.append(("wes_biz", "runtime_inbox"))
    session = _FakeSession(catalog=catalog)
    reset_mock = AsyncMock()
    monkeypatch.setattr(reset_module, "reset_mock_wms", reset_mock)

    with pytest.raises(
        RuntimeError,
        match=r"schema 不匹配.*wes_runtime\.runtime_inbox.*wes_biz\.runtime_inbox",
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
        ("UPDATE wes_biz.devices", False),
        ("INSERT INTO wes_runtime.workline_runtime_status_projections", False),
        ("UPDATE wes_biz.work_lines", False),
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


def test_wrapper_preserves_current_flags_and_does_not_restore_retired_entrypoint() -> None:
    source = Path("scripts/data/reset_runtime_data.sh").read_text(encoding="utf-8")

    for flag in ("--yes", "--include-audit-logs", "--no-reset-mocks", "--force", "--json"):
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
