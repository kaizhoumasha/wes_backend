"""RuntimeInbox PostgreSQL CI 验收入口合同。"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import scripts.run_runtime_inbox_postgresql_acceptance as acceptance_runner
from scripts.classify_runtime_inbox_acceptance import classify_runtime_inbox_acceptance
from scripts.run_runtime_inbox_postgresql_acceptance import (
    EXECUTOR_CLEANUP_TIMEOUT_SECONDS,
    EXECUTOR_ERROR_MAX_CHARS,
    EXECUTOR_STREAM_CHUNK_SIZE,
    EXECUTOR_TAIL_LINE_MAX_CHARS,
    EXECUTOR_TAIL_MAX_CHARS,
    EXECUTOR_TAIL_MAX_LINES,
    REQUIRED_FREE_CONNECTION_SLOTS,
    AcceptanceCommand,
    AcceptanceFailure,
    _default_executor,
    run_acceptance,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
JENKINSFILE = REPO_ROOT / "Jenkinsfile.backend-ci"
LIFECYCLE_SCRIPT = REPO_ROOT / "scripts/run_runtime_inbox_postgresql_acceptance_ci.sh"
RUNNER = REPO_ROOT / "scripts/run_runtime_inbox_postgresql_acceptance.py"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def test_ci_uses_isolated_postgresql_and_archives_contract_artifacts():
    jenkins = JENKINSFILE.read_text(encoding="utf-8")
    lifecycle = LIFECYCLE_SCRIPT.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "stage('RuntimeInbox PostgreSQL Acceptance')" in jenkins
    assert "stage('Classify Required HEAVY')" in jenkins
    assert "CI_RUNTIME_INBOX_ACCEPTANCE_MODE" in jenkins
    assert jenkins.count("scripts/select_heavy_tests.py --base") == 1
    assert "run_runtime_inbox_postgresql_acceptance_ci.sh run" in jenkins
    assert "run_runtime_inbox_postgresql_acceptance_ci.sh cleanup" in jenkins
    assert "reports/runtime-inbox-acceptance/junit/*.xml" in jenkins
    assert "reports/runtime-inbox-acceptance/**/*" in jenkins
    assert "allowEmptyArchive: true" in jenkins
    assert "fingerprint: true" in jenkins
    assert "timescale/timescaledb:latest-pg17" in lifecycle
    assert 'test "$(cat /proc/1/comm)" = postgres && pg_isready' in lifecycle
    assert "docker network create" in lifecycle
    assert "docker volume create" in lifecycle
    assert "ALTER ROLE runtime_acceptance CREATEDB" in lifecycle
    assert 'RUNTIME_INBOX_DATABASE_TEMPLATE="wes_tmp_runtime_inbox_template"' in lifecycle
    assert 'createdb -U runtime_acceptance -T template0 "${RUNTIME_INBOX_DATABASE_TEMPLATE}"' in lifecycle
    assert "--entrypoint /opt/venv/bin/alembic" in lifecycle
    assert "max_connections=100" in lifecycle
    assert "INTEGRATION_DATABASE_URL=postgresql://runtime_acceptance:" in lifecycle
    assert "INTEGRATION_DATABASE_SAFE_HOSTS=runtime-inbox-postgres" in lifecycle
    assert "POSTGRES_HOST=runtime-inbox-postgres" in lifecycle
    assert '--env-file "${WORKSPACE}/.env.test"' in lifecycle
    assert "\n    -p " not in lifecycle
    assert "trap cleanup EXIT" in lifecycle
    assert 'docker rm -f "${ACCEPTANCE_CONTAINER}"' in lifecycle
    assert 'docker rm -f "${POSTGRES_CONTAINER}"' in lifecycle
    assert 'docker network rm "${POSTGRES_NETWORK}"' in lifecycle
    assert 'docker volume rm "${POSTGRES_VOLUME}"' in lifecycle
    assert "tests/integration/test_runtime_inbox_migration_postgresql.py" in runner
    assert "tests/integration/test_runtime_inbox_processing_postgresql.py" in runner
    assert "test_claim_crash_recovers_with_new_owner_and_rejects_old_fence" in runner
    assert "test_writeback_crash_recovers_terminal_processing_once" in runner
    assert "tests/load/test_runtime_inbox_claim_benchmark.py" in runner
    assert "validate_runtime_inbox_benchmark_evidence" in runner
    assert 'source_environment.get("GIT_COMMIT") != expected_commit' in runner
    assert 'mkdir -p "${WORKSPACE}/logs"' in lifecycle
    assert '-v "${WORKSPACE}:/workspace:ro"' in lifecycle
    assert '-v "${REPORT_DIR}/logs:/workspace/logs:rw"' in lifecycle
    assert '-v "${WORKSPACE}/reports:/artifacts/reports:rw"' in lifecycle
    assert "--workdir /workspace" in lifecycle
    assert "UV_PROJECT_ENVIRONMENT=/app/.venv" in lifecycle
    assert "PYTHONPATH=/workspace" in lifecycle
    assert "GIT_CONFIG_VALUE_0=/workspace" in lifecycle
    assert "GIT_CONFIG_VALUE_0=/app" not in lifecycle
    assert "git status --porcelain" in lifecycle
    assert '-v "${WORKSPACE}/.git:/app/.git:ro"' not in lifecycle
    assert "RUN ln -s /opt/venv /app/.venv" in dockerfile


def test_runtime_inbox_acceptance_classifier_uses_the_selected_heavy_manifest() -> None:
    assert classify_runtime_inbox_acceptance([]) == "none"
    assert classify_runtime_inbox_acceptance(["tests/integration/test_wms_deployment_attestation.py"]) == "none"
    assert (
        classify_runtime_inbox_acceptance(["tests/integration/test_runtime_inbox_processing_postgresql.py"])
        == "correctness"
    )
    assert (
        classify_runtime_inbox_acceptance(["tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py"])
        == "correctness"
    )
    assert classify_runtime_inbox_acceptance(["tests/integration/test_runtime_inbox_migration_postgresql.py"]) == "full"


def test_template_migration_loads_the_same_required_settings_as_acceptance() -> None:
    lifecycle = LIFECYCLE_SCRIPT.read_text(encoding="utf-8")
    template_migration = lifecycle.split('echo "Preparing migrated RuntimeInbox database template"', maxsplit=1)[1]
    template_migration = template_migration.split("set +e", maxsplit=1)[0]

    assert '--env-file "${WORKSPACE}/.env.test"' in template_migration
    assert "ALEMBIC_DATABASE_URL=postgresql+asyncpg://runtime_acceptance:" in lifecycle


def test_acceptance_runner_runs_correctness_suites_concurrently_before_benchmark_and_validates_evidence(
    tmp_path: Path,
):
    events: list[str] = []
    correctness_started = threading.Barrier(3)
    correctness_finished: set[str] = set()
    event_lock = threading.Lock()

    def preflight(_environment, required_free_slots):
        events.append("preflight")
        assert required_free_slots == REQUIRED_FREE_CONNECTION_SLOTS == 10

    def execute(command, environment):
        assert command.argv[:3] == ("uv", "run", "--no-sync")
        if command.name != "benchmark":
            correctness_started.wait(timeout=2)
            with event_lock:
                correctness_finished.add(command.name)
                events.append(command.name)
            return
        assert correctness_finished == {"migration_matrix", "processing_integration", "crash_recovery"}
        events.append(command.name)
        if command.name == "benchmark":
            Path(environment["RUNTIME_INBOX_BENCHMARK_EVIDENCE"]).write_text("{}", encoding="utf-8")

    def validate(path, expected_commit):
        events.append("validator")
        assert path == tmp_path / "runtime-inbox-claim-benchmark.json"
        assert expected_commit == "a" * 40

    run_acceptance(
        tmp_path,
        "a" * 40,
        environment={
            "GIT_COMMIT": "a" * 40,
            "INTEGRATION_DATABASE_URL": "postgresql://user:secret@runtime-inbox-postgres/postgres",
        },
        preflight_check=preflight,
        executor=execute,
        evidence_validator=validate,
    )

    assert events[0] == "preflight"
    assert set(events[1:4]) == {"migration_matrix", "processing_integration", "crash_recovery"}
    assert events[4:] == ["benchmark", "validator"]
    diagnostic = json.loads((tmp_path / "diagnostic.json").read_text(encoding="utf-8"))
    assert diagnostic["status"] == "passed"


def test_acceptance_runner_correctness_mode_skips_only_the_migration_matrix(tmp_path: Path) -> None:
    executed: list[str] = []

    def execute(command, environment):
        executed.append(command.name)
        if command.name == "benchmark":
            Path(environment["RUNTIME_INBOX_BENCHMARK_EVIDENCE"]).write_text("{}", encoding="utf-8")

    run_acceptance(
        tmp_path,
        "a" * 40,
        mode="correctness",
        environment={"GIT_COMMIT": "a" * 40},
        preflight_check=lambda _environment, _required_free_slots: None,
        executor=execute,
        evidence_validator=lambda _path, _expected_commit: None,
    )

    assert set(executed[:-1]) == {"processing_integration", "crash_recovery"}
    assert executed[-1] == "benchmark"
    diagnostic = json.loads((tmp_path / "diagnostic.json").read_text(encoding="utf-8"))
    assert diagnostic["mode"] == "correctness"


def test_acceptance_runner_named_pytest_targets_exist(tmp_path: Path) -> None:
    for command in acceptance_runner._commands(tmp_path):
        for target in (argument for argument in command.argv if argument.startswith("tests/")):
            path_text, separator, function_name = target.partition("::")
            if not separator:
                continue
            tree = ast.parse((REPO_ROOT / path_text).read_text(encoding="utf-8"))
            functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}
            assert function_name in functions, target


def test_acceptance_runner_rejects_commit_mismatch_before_postgresql_preflight(tmp_path: Path):
    preflight_called = False

    def preflight(_environment, _required_free_slots):
        nonlocal preflight_called
        preflight_called = True

    with pytest.raises(AcceptanceFailure, match="GIT_COMMIT does not match"):
        run_acceptance(
            tmp_path,
            "a" * 40,
            environment={"GIT_COMMIT": "b" * 40},
            preflight_check=preflight,
        )

    assert preflight_called is False


def test_default_executor_streams_redacted_full_log_and_raises_with_bounded_tail(tmp_path: Path):
    (tmp_path / "logs").mkdir()
    secret_url = "postgresql://runtime_user:super-secret@runtime-inbox-postgres/postgres"
    early_marker = "EARLY-OUTPUT-MUST-STAY-IN-ARTIFACT"
    tail_boundary_marker = "TAIL-CANDIDATE-OUTSIDE-8000-BOUNDARY"
    oversized_line_start = "OVERSIZED-LATE-LINE-START-MUST-NOT-ENTER-ERROR"
    late_marker = "LATE-OUTPUT-MUST-APPEAR-IN-TAIL"
    oversized_fill = "z" * (EXECUTOR_TAIL_LINE_MAX_CHARS + 500)
    script = "\n".join(
        (
            "import sys",
            f"secret_url = {secret_url!r}",
            f"print({early_marker!r}, secret_url)",
            "for index in range(200):",
            "    print(f'stdout-line-{index:03d}-' + ('x' * 120))",
            "    print(f'stderr-line-{index:03d}-' + ('y' * 120), file=sys.stderr)",
            f"for index in range({EXECUTOR_TAIL_MAX_LINES + 5}):",
            f"    marker = {tail_boundary_marker!r} if index == 0 else f'tail-candidate-{{index:03d}}'",
            f"    print(('q' * {EXECUTOR_TAIL_LINE_MAX_CHARS + 200}) + '-' + marker, file=sys.stderr)",
            f"print({oversized_line_start!r} + {oversized_fill!r} + {late_marker!r} + ' ' + secret_url, file=sys.stderr)",
            "raise SystemExit(23)",
        )
    )
    command = AcceptanceCommand("streaming_contract", (sys.executable, "-u", "-c", script))
    environment = {
        "RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR": str(tmp_path),
        "INTEGRATION_DATABASE_URL": secret_url,
    }

    with pytest.raises(RuntimeError) as exc_info:
        _default_executor(command, environment)

    log_path = tmp_path / "logs" / "streaming_contract.log"
    log = log_path.read_text(encoding="utf-8")
    error = str(exc_info.value)
    assert early_marker in log
    assert late_marker in log
    assert tail_boundary_marker in log
    assert oversized_line_start + oversized_fill + late_marker in log
    assert "stdout-line-000" in log
    assert "stderr-line-199" in log
    assert secret_url not in log
    assert "super-secret" not in log
    assert "postgresql://***@runtime-inbox-postgres/postgres" in log
    assert "streaming_contract" in error
    assert "exited with 23" in error
    assert str(log_path) in error
    assert late_marker in error
    assert early_marker not in error
    assert tail_boundary_marker not in error
    assert oversized_line_start not in error
    assert secret_url not in error
    assert "super-secret" not in error
    assert "postgresql://***@runtime-inbox-postgres/postgres" in error
    tail = error.split("redacted tail:\n", maxsplit=1)[1]
    assert len(tail) <= EXECUTOR_TAIL_MAX_CHARS
    assert len(error) <= EXECUTOR_ERROR_MAX_CHARS
    assert "capture_output=True" not in RUNNER.read_text(encoding="utf-8")


def test_default_executor_streams_only_redacted_output_to_console(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    (tmp_path / "logs").mkdir()
    controlled_password = "top-secret"
    secret_url = "postgresql://runtime_user:" + controlled_password + "@runtime-inbox-postgres/postgres"
    command = AcceptanceCommand(
        "console_stream",
        (sys.executable, "-c", f"print({secret_url!r})"),
    )

    _default_executor(command, {"RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR": str(tmp_path)})

    console = capsys.readouterr().out
    assert "top-secret" not in console
    assert secret_url not in console
    assert "postgresql://***@runtime-inbox-postgres/postgres" in console


def test_default_executor_reads_fixed_chunks_and_bounds_unterminated_output(tmp_path: Path, monkeypatch):
    (tmp_path / "logs").mkdir()
    early_marker = "UNTERMINATED-EARLY-MARKER"
    late_marker = "UNTERMINATED-LATE-MARKER"
    boundary_fill = "u" * (EXECUTOR_STREAM_CHUNK_SIZE - len(early_marker) - 1)
    output = early_marker + boundary_fill + "中" + ("u" * (EXECUTOR_STREAM_CHUNK_SIZE * 5)) + late_marker
    encoded_output = output.encode()

    class FixedChunkPipe:
        def __init__(self):
            self.offset = 0
            self.read_sizes: list[int] = []

        def __iter__(self):
            raise AssertionError("executor must not iterate an unbounded logical line")

        def read1(self, size=-1):
            self.read_sizes.append(size)
            assert size == EXECUTOR_STREAM_CHUNK_SIZE
            chunk = encoded_output[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

        def close(self):
            pass

    class CompletedProcess:
        def __init__(self):
            self.stdout = FixedChunkPipe()

        def wait(self, timeout=None):
            assert timeout is None
            return 29

    process = CompletedProcess()
    monkeypatch.setattr(acceptance_runner.subprocess, "Popen", lambda *_args, **_kwargs: process)
    command = AcceptanceCommand("unterminated", ("fixed-command",))
    environment = {"RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR": str(tmp_path)}

    with pytest.raises(RuntimeError) as exc_info:
        _default_executor(command, environment)

    log = (tmp_path / "logs" / "unterminated.log").read_text(encoding="utf-8")
    error = str(exc_info.value)
    assert log == output
    assert "�" not in log
    assert early_marker not in error
    assert late_marker in error
    assert process.stdout.read_sizes[-1] == EXECUTOR_STREAM_CHUNK_SIZE
    assert len(error.split("redacted tail:\n", maxsplit=1)[1]) <= EXECUTOR_TAIL_LINE_MAX_CHARS


def test_default_executor_flushes_short_output_before_process_exit(tmp_path: Path):
    (tmp_path / "logs").mkdir()
    release_path = tmp_path / "release-child"
    marker = "SHORT-OUTPUT-MUST-BE-VISIBLE-BEFORE-EXIT"
    script = "\n".join(
        (
            "import time",
            "from pathlib import Path",
            f"release_path = Path({str(release_path)!r})",
            f"print({marker!r}, flush=True)",
            "while not release_path.exists():",
            "    time.sleep(0.01)",
        )
    )
    command = AcceptanceCommand("short_live_output", (sys.executable, "-u", "-c", script))
    environment = {"RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR": str(tmp_path)}
    executor_errors: list[BaseException] = []

    def execute() -> None:
        try:
            _default_executor(command, environment)
        except BaseException as exc:  # pragma: no cover - 断言会报告线程中的异常
            executor_errors.append(exc)

    executor_thread = threading.Thread(target=execute, daemon=True)
    executor_thread.start()
    log_path = tmp_path / "logs" / "short_live_output.log"
    deadline = time.monotonic() + 2.0
    try:
        while time.monotonic() < deadline:
            if log_path.exists() and marker in log_path.read_text(encoding="utf-8"):
                break
            time.sleep(0.01)
        else:
            pytest.fail("short child output was not flushed before process exit")
        assert executor_thread.is_alive(), "child must still be waiting when the artifact becomes visible"
    finally:
        release_path.touch()
        executor_thread.join(timeout=2.0)

    assert not executor_thread.is_alive()
    assert not executor_errors


def test_default_executor_redacts_database_url_split_across_chunks(tmp_path: Path):
    (tmp_path / "logs").mkdir()
    secret = "split-user:split-secret"
    safe_url = "postgresql://***@runtime-inbox-postgres/postgres"
    prefix = "p" * (EXECUTOR_STREAM_CHUNK_SIZE * 6 - len("postgre"))
    output = prefix + f"postgresql://{secret}@runtime-inbox-postgres/postgres SPLIT-SECRET-TAIL"
    script = f"import sys; sys.stdout.write({output!r}); raise SystemExit(31)"
    command = AcceptanceCommand("split_secret", (sys.executable, "-c", script))
    environment = {"RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR": str(tmp_path)}

    with pytest.raises(RuntimeError) as exc_info:
        _default_executor(command, environment)

    log = (tmp_path / "logs" / "split_secret.log").read_text(encoding="utf-8")
    error = str(exc_info.value)
    assert secret not in log
    assert "split-secret" not in error
    assert safe_url in log
    assert safe_url in error
    assert log == prefix + safe_url + " SPLIT-SECRET-TAIL"


def test_default_executor_preserves_credentialless_database_url_split_across_chunks(tmp_path: Path):
    (tmp_path / "logs").mkdir()
    credentialless_url = "postgresql://localhost/db"
    prefix = "c" * (EXECUTOR_STREAM_CHUNK_SIZE * 2 - len("postgre"))
    output = prefix + credentialless_url + " CREDENTIALLESS-TAIL"
    script = f"import sys; sys.stdout.write({output!r}); raise SystemExit(37)"
    command = AcceptanceCommand("credentialless", (sys.executable, "-c", script))
    environment = {"RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR": str(tmp_path)}

    with pytest.raises(RuntimeError) as exc_info:
        _default_executor(command, environment)

    log = (tmp_path / "logs" / "credentialless.log").read_text(encoding="utf-8")
    error = str(exc_info.value)
    assert log == output
    assert credentialless_url in error
    assert "postgresql://***" not in log
    assert "postgresql://***" not in error


def test_default_executor_terminates_then_kills_on_stream_failure_without_masking_error(tmp_path: Path, monkeypatch):
    (tmp_path / "logs").mkdir()

    class FailingPipe:
        def read1(self, _size=-1):
            raise RuntimeError("original stream failure")

        def close(self):
            pass

    class StuckProcess:
        def __init__(self):
            self.stdout = FailingPipe()
            self.events: list[object] = []
            self.killed = False

        def poll(self):
            self.events.append("poll")

        def terminate(self):
            self.events.append("terminate")

        def kill(self):
            self.events.append("kill")
            self.killed = True

        def wait(self, timeout=None):
            self.events.append(("wait", timeout))
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd="stuck-command", timeout=timeout)
            return -9

    process = StuckProcess()
    monkeypatch.setattr(acceptance_runner.subprocess, "Popen", lambda *_args, **_kwargs: process)
    command = AcceptanceCommand("stream_failure", ("stuck-command",))
    environment = {"RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR": str(tmp_path)}

    with pytest.raises(RuntimeError, match="original stream failure"):
        _default_executor(command, environment)

    assert "terminate" in process.events
    assert "kill" in process.events
    waits = [event for event in process.events if isinstance(event, tuple) and event[0] == "wait"]
    assert len(waits) == 2
    assert all(timeout == EXECUTOR_CLEANUP_TIMEOUT_SECONDS for _, timeout in waits)


def test_default_executor_cleans_up_when_normal_wait_raises_without_masking_error(tmp_path: Path, monkeypatch):
    (tmp_path / "logs").mkdir()

    class EofPipe:
        def read1(self, _size=-1):
            return b""

        def close(self):
            pass

    class WaitFailureProcess:
        def __init__(self):
            self.stdout = EofPipe()
            self.events: list[object] = []

        def poll(self):
            self.events.append("poll")

        def terminate(self):
            self.events.append("terminate")

        def wait(self, timeout=None):
            self.events.append(("wait", timeout))
            if timeout is None:
                raise OSError("original normal wait failure")
            return -15

    process = WaitFailureProcess()
    monkeypatch.setattr(acceptance_runner.subprocess, "Popen", lambda *_args, **_kwargs: process)
    command = AcceptanceCommand("wait_failure", ("wait-failure-command",))
    environment = {"RUNTIME_INBOX_ACCEPTANCE_OUTPUT_DIR": str(tmp_path)}

    with pytest.raises(OSError, match="original normal wait failure"):
        _default_executor(command, environment)

    assert "terminate" in process.events
    assert process.events.count(("wait", None)) == 1
    assert ("wait", EXECUTOR_CLEANUP_TIMEOUT_SECONDS) in process.events


@pytest.mark.parametrize("failure_at", ["preflight", "processing_integration", "validator"])
def test_acceptance_runner_returns_failure_and_keeps_redacted_diagnostic(tmp_path: Path, failure_at: str):
    secret_url = "postgresql://user:top-secret@runtime-inbox-postgres/postgres"

    def preflight(_environment, _required_free_slots):
        if failure_at == "preflight":
            raise RuntimeError(f"cannot connect: {secret_url}")

    def execute(command, environment):
        if command.name == "benchmark":
            Path(environment["RUNTIME_INBOX_BENCHMARK_EVIDENCE"]).write_text("{}", encoding="utf-8")
        if command.name == failure_at:
            raise RuntimeError(f"suite failed: {secret_url}")

    def validate(_path, _expected_commit):
        if failure_at == "validator":
            raise RuntimeError(f"invalid evidence: {secret_url}")

    with pytest.raises(AcceptanceFailure):
        run_acceptance(
            tmp_path,
            "b" * 40,
            environment={"GIT_COMMIT": "b" * 40, "INTEGRATION_DATABASE_URL": secret_url},
            preflight_check=preflight,
            executor=execute,
            evidence_validator=validate,
        )

    diagnostic = (tmp_path / "diagnostic.json").read_text(encoding="utf-8")
    assert "top-secret" not in diagnostic
    assert secret_url not in diagnostic
    assert "postgresql://***@runtime-inbox-postgres/postgres" in diagnostic
