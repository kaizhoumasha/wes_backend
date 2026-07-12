"""运行数据 reset 脚本的安全合同。"""

from __future__ import annotations

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


class _FakeSession:
    def __init__(self, *, catalog: list[tuple[str, str]] | None = None, row_count: int = 3) -> None:
        self.catalog = catalog or [(target.schema, target.table) for target in reset_module.RUNTIME_TABLES]
        self.row_count = row_count
        self.statements: list[str] = []
        self.commits = 0

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "information_schema.tables" in sql:
            return _Rows(self.catalog)
        if sql.startswith("SELECT count(*)"):
            return _Rows(scalar=self.row_count)
        return _Rows(rowcount=2)

    async def commit(self) -> None:
        self.commits += 1


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


def test_wrapper_preserves_current_flags_and_does_not_restore_retired_entrypoint() -> None:
    source = Path("scripts/data/reset_runtime_data.sh").read_text(encoding="utf-8")

    for flag in ("--yes", "--include-audit-logs", "--no-reset-mocks", "--force", "--json"):
        assert flag in source
    assert "workline_inbox" not in source
    assert "reset_runtime_data.py" in source
