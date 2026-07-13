#!/usr/bin/env python3
"""按固定顺序运行 RuntimeInbox PostgreSQL 正式验收并生成诊断。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tests.load.runtime_inbox_postgresql_benchmark import validate_runtime_inbox_benchmark_evidence
from tests.support.runtime_inbox_postgresql import preflight

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

REQUIRED_FREE_CONNECTION_SLOTS = 5
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
_DATABASE_URL_PATTERN = re.compile(r"(postgres(?:ql)?(?:\+asyncpg)?://)[^\s/@]+(?::[^\s/@]*)?@")


class AcceptanceFailure(RuntimeError):
    """稳定、可安全写入 CI diagnostic 的验收失败。"""


@dataclass(frozen=True, slots=True)
class AcceptanceCommand:
    name: str
    argv: tuple[str, ...]
    extra_environment: Mapping[str, str] = field(default_factory=dict)


def _commands(output_dir: Path) -> tuple[AcceptanceCommand, ...]:
    junit = output_dir / "junit"
    evidence = output_dir / "runtime-inbox-claim-benchmark.json"
    return (
        AcceptanceCommand(
            "migration_matrix",
            (
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/integration/test_runtime_inbox_migration_postgresql.py",
                "-q",
                f"--junitxml={junit / 'migration-matrix.xml'}",
            ),
        ),
        AcceptanceCommand(
            "processing_integration",
            (
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/integration/test_runtime_inbox_processing_postgresql.py",
                "-q",
                f"--junitxml={junit / 'processing-integration.xml'}",
            ),
        ),
        AcceptanceCommand(
            "crash_after_claim",
            (
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py::test_claim_crash_recovers_with_new_owner_and_rejects_old_fence",
                "-q",
                f"--junitxml={junit / 'crash-after-claim.xml'}",
            ),
        ),
        AcceptanceCommand(
            "crash_before_terminal",
            (
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py::test_writeback_crash_rolls_back_effects_before_reprocessing_once",
                "-q",
                f"--junitxml={junit / 'crash-before-terminal.xml'}",
            ),
        ),
        AcceptanceCommand(
            "benchmark",
            (
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/load/test_runtime_inbox_claim_benchmark.py",
                "-q",
                f"--junitxml={junit / 'benchmark.xml'}",
            ),
            {"RUNTIME_INBOX_BENCHMARK_EVIDENCE": str(evidence)},
        ),
    )


def _redact(value: object) -> str:
    return _DATABASE_URL_PATTERN.sub(r"\1***@", str(value))


def _default_preflight(environment: Mapping[str, str], required_free_slots: int) -> None:
    async def check() -> None:
        result = await preflight(environ=environment, required_free_slots=required_free_slots)
        await result.close()

    asyncio.run(check())


def _default_executor(command: AcceptanceCommand, environment: Mapping[str, str]) -> None:
    # argv 仅来自本模块固定合同，不接收用户输入或 shell 字符串。
    completed = subprocess.run(  # noqa: S603
        command.argv,
        env=dict(environment),
        check=False,
        capture_output=True,
        text=True,
    )
    log_path = Path(environment["RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR"]) / "logs" / f"{command.name}.log"
    log_path.write_text(_redact(completed.stdout + completed.stderr), encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"{command.name} exited with {completed.returncode}")


def _default_evidence_validator(path: Path, expected_commit: str) -> None:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    result = validate_runtime_inbox_benchmark_evidence(evidence, expected_commit=expected_commit)
    if not result.valid:
        raise RuntimeError(f"benchmark evidence invalid: {result.reason} ({result.field or '-'})")


def run_acceptance(
    output_dir: Path,
    expected_commit: str,
    *,
    environment: Mapping[str, str] | None = None,
    preflight_check: Callable[[Mapping[str, str], int], None] = _default_preflight,
    executor: Callable[[AcceptanceCommand, Mapping[str, str]], None] = _default_executor,
    evidence_validator: Callable[[Path, str], None] = _default_evidence_validator,
) -> None:
    """执行严格验收；preflight、场景或 evidence 任一失败都抛出非零语义。"""

    output_dir = output_dir.resolve()
    (output_dir / "junit").mkdir(parents=True, exist_ok=True)
    (output_dir / "logs").mkdir(parents=True, exist_ok=True)
    source_environment = dict(os.environ if environment is None else environment)
    source_environment["RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR"] = str(output_dir)
    diagnostic: dict[str, object] = {
        "schema_version": "runtime-inbox-postgresql-acceptance/v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "expected_commit": expected_commit,
        "status": "failed",
        "completed_suites": [],
    }
    current_step = "commit_preflight"
    try:
        if _COMMIT_PATTERN.fullmatch(expected_commit) is None:
            raise RuntimeError("expected commit must be a full lowercase SHA-1")
        if source_environment.get("GIT_COMMIT") != expected_commit:
            raise RuntimeError("GIT_COMMIT does not match expected commit")
        current_step = "postgresql_preflight"
        preflight_check(source_environment, REQUIRED_FREE_CONNECTION_SLOTS)
        for command in _commands(output_dir):
            current_step = command.name
            command_environment = source_environment | dict(command.extra_environment)
            executor(command, command_environment)
            completed = diagnostic["completed_suites"]
            assert isinstance(completed, list)
            completed.append(command.name)
        current_step = "evidence_validator"
        evidence_validator(output_dir / "runtime-inbox-claim-benchmark.json", expected_commit)
        diagnostic["status"] = "passed"
    except Exception as exc:
        diagnostic["failed_step"] = current_step
        diagnostic["error"] = _redact(exc)
        raise AcceptanceFailure(f"{current_step}: {_redact(exc)}") from None
    finally:
        (output_dir / "diagnostic.json").write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    arguments = parser.parse_args(argv)
    try:
        run_acceptance(arguments.output_dir, arguments.expected_commit)
    except AcceptanceFailure as exc:
        print(f"RuntimeInbox PostgreSQL acceptance failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
