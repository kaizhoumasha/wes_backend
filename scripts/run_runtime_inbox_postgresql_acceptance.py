#!/usr/bin/env python3
"""按固定顺序运行 RuntimeInbox PostgreSQL 正式验收并生成诊断。"""

from __future__ import annotations

import argparse
import asyncio
import codecs
import json
import os
import re
import subprocess
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tests.load.runtime_inbox_postgresql_benchmark import validate_runtime_inbox_benchmark_evidence
from tests.support.runtime_inbox_postgresql import preflight

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

REQUIRED_FREE_CONNECTION_SLOTS = 10
EXECUTOR_TAIL_MAX_LINES = 50
EXECUTOR_TAIL_MAX_CHARS = 8_000
EXECUTOR_TAIL_LINE_MAX_CHARS = 1_000
EXECUTOR_ERROR_MAX_CHARS = 9_000
EXECUTOR_STREAM_CHUNK_SIZE = 4_096
EXECUTOR_CLEANUP_TIMEOUT_SECONDS = 5.0
EXECUTOR_REDACTION_AUTHORITY_MAX_CHARS = 4_096
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
_DATABASE_URL_PATTERN = re.compile(r"(postgres(?:ql)?(?:\+asyncpg)?://)[^\s/@]+(?::[^\s/@]*)?@")
_DATABASE_URL_SCHEMES = (
    "postgres://",
    "postgresql://",
    "postgres+asyncpg://",
    "postgresql+asyncpg://",
)


class AcceptanceFailure(RuntimeError):
    """稳定、可安全写入 CI diagnostic 的验收失败。"""


@dataclass(frozen=True, slots=True)
class AcceptanceCommand:
    name: str
    argv: tuple[str, ...]
    extra_environment: Mapping[str, str] = field(default_factory=dict)


def _commands(output_dir: Path, mode: str = "full") -> tuple[AcceptanceCommand, ...]:
    junit = output_dir / "junit"
    evidence = output_dir / "runtime-inbox-claim-benchmark.json"
    commands = (
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
            "crash_recovery",
            (
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py::test_claim_crash_recovers_with_new_owner_and_rejects_old_fence",
                "tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py::test_writeback_crash_recovers_terminal_processing_once",
                "-q",
                f"--junitxml={junit / 'crash-recovery.xml'}",
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
    if mode == "full":
        return commands
    if mode == "correctness":
        return commands[1:]
    raise ValueError(f"unsupported RuntimeInbox acceptance mode: {mode}")


def _redact(value: object) -> str:
    return _DATABASE_URL_PATTERN.sub(r"\1***@", str(value))


class _StreamingDatabaseUrlRedactor:
    """跨读取块脱敏 PostgreSQL URL，并以硬上限缓存待判定的 authority。"""

    def __init__(self) -> None:
        self._pending = ""
        self._authority_candidate: str | None = None
        self._conservative_redaction = False

    def feed(self, value: str) -> str:
        output: list[str] = []
        for character in value:
            if self._authority_candidate is not None:
                if character == "@":
                    output.append("***@")
                    self._authority_candidate = None
                elif character.isspace() or character == "/":
                    output.extend((self._authority_candidate, character))
                    self._authority_candidate = None
                else:
                    self._authority_candidate += character
                    if len(self._authority_candidate) > EXECUTOR_REDACTION_AUTHORITY_MAX_CHARS:
                        self._authority_candidate = None
                        self._conservative_redaction = True
                continue

            if self._conservative_redaction:
                if character == "@":
                    output.append("***@")
                    self._conservative_redaction = False
                elif character.isspace() or character == "/":
                    output.extend(("***", character))
                    self._conservative_redaction = False
                continue

            self._pending += character
            while self._pending:
                if self._pending in _DATABASE_URL_SCHEMES:
                    output.append(self._pending)
                    self._pending = ""
                    self._authority_candidate = ""
                    break
                if any(scheme.startswith(self._pending) for scheme in _DATABASE_URL_SCHEMES):
                    break
                output.append(self._pending[0])
                self._pending = self._pending[1:]
        return "".join(output)

    def finish(self) -> str:
        if self._conservative_redaction:
            self._conservative_redaction = False
            return "***"
        if self._authority_candidate is not None:
            authority, self._authority_candidate = self._authority_candidate, None
            return authority
        pending, self._pending = self._pending, ""
        return pending


class _BoundedLogTail:
    """按逻辑行保存有界日志末尾，读取块大小不会改变 tail 合同。"""

    def __init__(self) -> None:
        self._lines: deque[str] = deque()
        self._current_line = ""
        self._chars = 0

    def feed(self, value: str) -> None:
        parts = value.split("\n")
        for index, part in enumerate(parts):
            self._append_current(part)
            if index < len(parts) - 1:
                self._append_current("\n")
                self._lines.append(self._current_line)
                self._current_line = ""
                self._rebalance()

    def summary(self) -> str:
        return "".join((*self._lines, self._current_line))

    def _append_current(self, value: str) -> None:
        previous_length = len(self._current_line)
        self._current_line = (self._current_line + value)[-EXECUTOR_TAIL_LINE_MAX_CHARS:]
        self._chars += len(self._current_line) - previous_length
        self._rebalance()

    def _rebalance(self) -> None:
        current_line_count = 1 if self._current_line else 0
        while len(self._lines) + current_line_count > EXECUTOR_TAIL_MAX_LINES:
            self._chars -= len(self._lines.popleft())
        while self._chars > EXECUTOR_TAIL_MAX_CHARS and self._lines:
            self._chars -= len(self._lines.popleft())


def _cleanup_failed_process(process: subprocess.Popen[bytes]) -> None:
    """限时回收失败子进程；任何清理错误都不得覆盖原始异常。"""

    try:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=EXECUTOR_CLEANUP_TIMEOUT_SECONDS)
            return
        except subprocess.TimeoutExpired:
            process.kill()
        process.wait(timeout=EXECUTOR_CLEANUP_TIMEOUT_SECONDS)
    except BaseException:
        return


def _default_preflight(environment: Mapping[str, str], required_free_slots: int) -> None:
    async def check() -> None:
        result = await preflight(environ=environment, required_free_slots=required_free_slots)
        await result.close()

    asyncio.run(check())


def _default_executor(command: AcceptanceCommand, environment: Mapping[str, str]) -> None:
    log_path = Path(environment["RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR"]) / "logs" / f"{command.name}.log"
    tail = _BoundedLogTail()
    redactor = _StreamingDatabaseUrlRedactor()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    print(f"[runtime-inbox] {command.name}: START", flush=True)

    # argv 仅来自本模块固定合同，不接收用户输入或 shell 字符串；输出先脱敏，再落盘和进入有界 tail。
    process = subprocess.Popen(  # noqa: S603
        command.argv,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            while raw_chunk := process.stdout.read1(EXECUTOR_STREAM_CHUNK_SIZE):
                decoded_chunk = decoder.decode(raw_chunk, final=False)
                redacted_chunk = redactor.feed(decoded_chunk)
                log_file.write(redacted_chunk)
                log_file.flush()
                print(redacted_chunk, end="", flush=True)
                tail.feed(redacted_chunk)
            final_chunk = redactor.feed(decoder.decode(b"", final=True)) + redactor.finish()
            log_file.write(final_chunk)
            log_file.flush()
            print(final_chunk, end="", flush=True)
            tail.feed(final_chunk)
        process.stdout.close()
    except BaseException:
        with suppress(BaseException):
            process.stdout.close()
        _cleanup_failed_process(process)
        raise
    try:
        returncode = process.wait()
    except BaseException:
        _cleanup_failed_process(process)
        raise
    if returncode != 0:
        tail_summary = tail.summary() or "<no output>"
        prefix = f"{command.name} exited with {returncode}; log_path={log_path}; redacted tail:\n"
        available_tail_chars = max(0, EXECUTOR_ERROR_MAX_CHARS - len(prefix))
        bounded_tail = tail_summary[-available_tail_chars:] if available_tail_chars else ""
        raise RuntimeError((prefix + bounded_tail)[:EXECUTOR_ERROR_MAX_CHARS])
    print(f"[runtime-inbox] {command.name}: PASS", flush=True)


def _default_evidence_validator(path: Path, expected_commit: str) -> None:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    result = validate_runtime_inbox_benchmark_evidence(evidence, expected_commit=expected_commit)
    if not result.valid:
        raise RuntimeError(f"benchmark evidence invalid: {result.reason} ({result.field or '-'})")


def run_acceptance(
    output_dir: Path,
    expected_commit: str,
    *,
    mode: str = "full",
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
        "mode": mode,
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
        commands = _commands(output_dir, mode)
        correctness_commands = commands[:-1]
        benchmark_command = commands[-1]
        with ThreadPoolExecutor(
            max_workers=len(correctness_commands),
            thread_name_prefix="runtime-inbox-acceptance",
        ) as executor_pool:
            futures = [
                executor_pool.submit(
                    executor,
                    command,
                    source_environment | dict(command.extra_environment),
                )
                for command in correctness_commands
            ]
            for command, future in zip(correctness_commands, futures, strict=True):
                current_step = command.name
                future.result()
                completed = diagnostic["completed_suites"]
                assert isinstance(completed, list)
                completed.append(command.name)

        current_step = benchmark_command.name
        executor(
            benchmark_command,
            source_environment | dict(benchmark_command.extra_environment),
        )
        completed = diagnostic["completed_suites"]
        assert isinstance(completed, list)
        completed.append(benchmark_command.name)
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
    parser.add_argument("--mode", choices=("full", "correctness"), default="full")
    arguments = parser.parse_args(argv)
    try:
        run_acceptance(arguments.output_dir, arguments.expected_commit, mode=arguments.mode)
    except AcceptanceFailure as exc:
        print(f"RuntimeInbox PostgreSQL acceptance failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
