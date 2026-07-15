"""工作线迁移清单 CLI 的部署合同。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

import scripts.workline_migration_inventory as cli
from src.app.workline.models import WorklineMigrationInventoryReport
from src.app.workline.services import (
    WorklineMigrationInventoryInvariantError,
    WorklineMigrationInventoryLimitExceeded,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/workline_migration_inventory.py"
SECRET_DATABASE_URL = "postgresql+asyncpg://inventory:super-secret@db.internal/wes"


def _report(*, foundation_ready: bool = True) -> WorklineMigrationInventoryReport:
    return WorklineMigrationInventoryReport(
        environment="test",
        generated_at=datetime(2026, 7, 15, 9, 30, tzinfo=UTC),
        inventory_digest="a" * 64,
        foundation_ready=foundation_ready,
    )


def _settings(*, environment: str = "test") -> SimpleNamespace:
    return SimpleNamespace(APP_ENV=environment, DATABASE_URL=SECRET_DATABASE_URL)


@pytest.mark.parametrize(
    ("foundation_ready", "check_foundation", "expected_exit"),
    [
        (True, False, cli.EXIT_OK),
        (False, False, cli.EXIT_OK),
        (True, True, cli.EXIT_OK),
        (False, True, cli.EXIT_FOUNDATION_BLOCKED),
    ],
)
def test_main_outputs_report_before_applying_foundation_gate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    foundation_ready: bool,
    check_foundation: bool,
    expected_exit: int,
) -> None:
    report = _report(foundation_ready=foundation_ready)
    build_report = AsyncMock(return_value=report)
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "build_report", build_report)
    arguments = ["--expected-environment", "test"]
    if check_foundation:
        arguments.append("--check-foundation")

    exit_code = cli.main(arguments)

    assert exit_code == expected_exit
    assert json.loads(capsys.readouterr().out) == report.model_dump(mode="json")
    build_report.assert_awaited_once()


def test_main_writes_report_atomically_before_returning_blocked_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "inventory.json"
    target.write_text("old-report", encoding="utf-8")
    report = _report(foundation_ready=False)
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "build_report", AsyncMock(return_value=report))

    exit_code = cli.main(
        [
            "--expected-environment",
            "test",
            "--output",
            str(target),
            "--check-foundation",
        ]
    )

    assert exit_code == cli.EXIT_FOUNDATION_BLOCKED
    assert json.loads(target.read_text(encoding="utf-8")) == report.model_dump(mode="json")
    assert list(tmp_path.glob(".inventory.json.*.tmp")) == []


def test_run_creates_new_output_file_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "inventory.json"
    report = _report()
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "build_report", AsyncMock(return_value=report))

    exit_code = cli.run(["--expected-environment", "test", "--output", str(target)])

    assert exit_code == cli.EXIT_OK
    assert json.loads(target.read_text(encoding="utf-8")) == report.model_dump(mode="json")
    assert list(tmp_path.glob(".inventory.json.*.tmp")) == []


@pytest.mark.parametrize("arguments", [[], ["--expected-environment", "staging"]])
def test_argparse_usage_errors_raise_system_exit_two_without_building_report(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    build_report = AsyncMock()
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "build_report", build_report)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(arguments)

    assert exc_info.value.code == cli.EXIT_USAGE_ERROR == 2
    build_report.assert_not_awaited()


def test_environment_mismatch_is_usage_error_before_database_access(monkeypatch: pytest.MonkeyPatch) -> None:
    build_report = AsyncMock()
    create_engine = MagicMock()
    monkeypatch.setattr(cli, "settings", _settings(environment="prod"))
    monkeypatch.setattr(cli, "build_report", build_report)
    monkeypatch.setattr(cli, "create_async_engine", create_engine)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--expected-environment", "test"])

    assert exc_info.value.code == cli.EXIT_USAGE_ERROR
    build_report.assert_not_awaited()
    create_engine.assert_not_called()


def test_subprocess_preserves_argparse_exit_code_two() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == cli.EXIT_USAGE_ERROR
    assert "--expected-environment" in completed.stderr


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timeout with super-secret"),
        WorklineMigrationInventoryInvariantError("invariant with super-secret"),
        OSError("disk with super-secret"),
        SQLAlchemyError("sql with super-secret params={'password': 'super-secret'}"),
    ],
)
def test_main_maps_known_runtime_errors_to_sanitized_exit_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "build_report", AsyncMock(side_effect=error))

    exit_code = cli.main(["--expected-environment", "test"])

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_RUNTIME_ERROR
    assert "迁移清单生成失败" in captured.err
    assert "super-secret" not in captured.err
    assert SECRET_DATABASE_URL not in captured.err
    assert captured.out == ""


def test_main_maps_inventory_limit_to_stable_actionable_sanitized_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(
        cli,
        "build_report",
        AsyncMock(side_effect=WorklineMigrationInventoryLimitExceeded("活动清单包含秘密 super-secret")),
    )

    exit_code = cli.main(["--expected-environment", "test"])

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_RUNTIME_ERROR
    assert captured.err == "活动 WorkLine 超过安全盘点上限（100 条）；请先实现 bulk summary port\n"
    assert "秘密" not in captured.err
    assert "super-secret" not in captured.err
    assert SECRET_DATABASE_URL not in captured.err
    assert captured.out == ""


def test_main_reraises_unknown_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = RuntimeError("unknown")
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "build_report", AsyncMock(side_effect=expected))

    with pytest.raises(RuntimeError) as exc_info:
        cli.main(["--expected-environment", "test"])

    assert exc_info.value is expected


def test_run_keeps_runtime_error_mapping_outside_command_orchestration(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = WorklineMigrationInventoryInvariantError("invalid")
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "build_report", AsyncMock(side_effect=expected))

    with pytest.raises(WorklineMigrationInventoryInvariantError) as exc_info:
        cli.run(["--expected-environment", "test"])

    assert exc_info.value is expected


def test_missing_output_parent_is_runtime_error_without_creating_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_parent = tmp_path / "missing"
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "build_report", AsyncMock(return_value=_report()))

    exit_code = cli.main(["--expected-environment", "test", "--output", str(missing_parent / "inventory.json")])

    assert exit_code == cli.EXIT_RUNTIME_ERROR
    assert missing_parent.exists() is False


class _FailingFile:
    def __init__(self, wrapped, failure_stage: str) -> None:
        self._wrapped = wrapped
        self._failure_stage = failure_stage

    def __enter__(self):
        self._wrapped.__enter__()
        return self

    def __exit__(self, *args):
        return self._wrapped.__exit__(*args)

    def write(self, payload: str):
        if self._failure_stage == "write":
            raise OSError("write failed")
        return self._wrapped.write(payload)

    def flush(self):
        if self._failure_stage == "flush":
            raise OSError("flush failed")
        return self._wrapped.flush()

    def fileno(self):
        return self._wrapped.fileno()


@pytest.mark.parametrize("failure_stage", ["write", "flush", "fsync", "replace"])
def test_atomic_output_failure_preserves_existing_target_and_cleans_tempfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    target = tmp_path / "inventory.json"
    target.write_text("old-report", encoding="utf-8")
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "build_report", AsyncMock(return_value=_report()))
    if failure_stage in {"write", "flush"}:
        original_fdopen = cli.os.fdopen

        def failing_fdopen(*args, **kwargs):
            return _FailingFile(original_fdopen(*args, **kwargs), failure_stage)

        monkeypatch.setattr(cli.os, "fdopen", failing_fdopen)
    elif failure_stage == "fsync":
        monkeypatch.setattr(cli.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("fsync failed")))
    else:
        monkeypatch.setattr(
            cli.os, "replace", lambda _source, _target: (_ for _ in ()).throw(OSError("replace failed"))
        )

    exit_code = cli.main(["--expected-environment", "test", "--output", str(target)])

    assert exit_code == cli.EXIT_RUNTIME_ERROR
    assert target.read_text(encoding="utf-8") == "old-report"
    assert list(tmp_path.glob(".inventory.json.*.tmp")) == []


class _TransactionContext:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def __aenter__(self):
        self._events.append("begin")

    async def __aexit__(self, *_args):
        self._events.append("end")


class _Session:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.commit = AsyncMock(side_effect=AssertionError("CLI 不得提交只读快照"))

    async def __aenter__(self):
        self.events.append("session_enter")
        return self

    async def __aexit__(self, *_args):
        self.events.append("session_exit")

    def begin(self):
        return _TransactionContext(self.events)

    async def execute(self, statement):
        self.events.append(str(statement))


class _Engine:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def dispose(self) -> None:
        self.events.append("dispose")


@pytest.mark.asyncio
async def test_build_report_uses_read_only_repeatable_read_snapshot_in_fixed_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    engine = _Engine(events)
    session = _Session(events)
    service = SimpleNamespace(
        build_report=AsyncMock(
            side_effect=lambda db, *, environment: events.append(f"service:{environment}") or _report()
        )
    )
    create_engine = MagicMock(return_value=engine)
    sessionmaker = lambda received_engine, **kwargs: (  # noqa: E731 - 测试中精确记录工厂输入。
        events.append(f"factory:{received_engine is engine}:{kwargs}") or (lambda: session)
    )
    timeout_values: list[int] = []
    original_timeout = asyncio.timeout

    def recording_timeout(seconds: int):
        timeout_values.append(seconds)
        return original_timeout(seconds)

    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "workline_migration_inventory_service", service)
    monkeypatch.setattr(cli, "create_async_engine", create_engine)
    monkeypatch.setattr(cli, "async_sessionmaker", sessionmaker)
    monkeypatch.setattr(cli.asyncio, "timeout", recording_timeout)

    result = await cli.build_report()

    assert result == _report()
    create_engine.assert_called_once_with(SECRET_DATABASE_URL, isolation_level="REPEATABLE READ")
    assert timeout_values == [cli.INVENTORY_TOTAL_TIMEOUT_SECONDS] == [60]
    assert cli.INVENTORY_STATEMENT_TIMEOUT_SECONDS == 5
    assert cli.INVENTORY_IDLE_TRANSACTION_TIMEOUT_SECONDS == 15
    assert events == [
        "factory:True:{}",
        "session_enter",
        "begin",
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = '5s'",
        "SET LOCAL idle_in_transaction_session_timeout = '15s'",
        "service:test",
        "end",
        "session_exit",
        "dispose",
    ]
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.CancelledError(), RuntimeError("failure")])
async def test_build_report_disposes_engine_after_cancellation_or_failure(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    events: list[str] = []
    engine = _Engine(events)
    session = _Session(events)
    service = SimpleNamespace(build_report=AsyncMock(side_effect=error))
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "workline_migration_inventory_service", service)
    monkeypatch.setattr(cli, "create_async_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(cli, "async_sessionmaker", lambda *_args, **_kwargs: lambda: session)

    with pytest.raises(type(error)):
        await cli.build_report()

    assert events[-1] == "dispose"
    assert events.count("dispose") == 1
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_report_timeout_actively_cancels_report_and_releases_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    engine = _Engine(events)
    session = _Session(events)
    entered = asyncio.Event()

    async def blocked_report(_db, *, environment: str):
        assert environment == "test"
        events.append("service_enter")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("service_cancelled")

    service = SimpleNamespace(build_report=blocked_report)
    monkeypatch.setattr(cli, "settings", _settings())
    monkeypatch.setattr(cli, "workline_migration_inventory_service", service)
    monkeypatch.setattr(cli, "create_async_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(cli, "async_sessionmaker", lambda *_args, **_kwargs: lambda: session)
    monkeypatch.setattr(cli, "INVENTORY_TOTAL_TIMEOUT_SECONDS", 0.001)

    with pytest.raises(TimeoutError):
        await cli.build_report()

    assert entered.is_set()
    assert events[-4:] == ["service_cancelled", "end", "session_exit", "dispose"]
    session.commit.assert_not_awaited()
