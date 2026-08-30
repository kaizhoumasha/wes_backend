from __future__ import annotations

import os
import pty
import signal
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/run_selected_heavy_local.sh"
CLEANUP_FINISHED = "docker cleanup-finished"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_environment(
    tmp_path: Path,
    monkeypatch,
    *,
    selected_test: str = "tests/e2e/transport/test_transport_production_wiring.py",
    runner_exit_code: int = 0,
    terminate_runner: bool = False,
    cleanup_exit_code: int = 0,
    runner_sleep_seconds: float = 0,
    cleanup_sleep_seconds: float = 0,
    runner_spawns_child: bool = False,
    blocking_query: bool = False,
    reject_inherited_stdin: bool = False,
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "calls.log"
    _write_executable(
        bin_dir / "docker",
        """#!/bin/sh
set -eu
printf 'docker %s\n' "$*" >> "$CALL_LOG"
case "$*" in
  *" down --volumes --remove-orphans") ;;
  *)
    if [ "$REJECT_INHERITED_STDIN" = "1" ] && [ ! -c /dev/fd/0 ]; then
      exit 91
    fi
    ;;
esac
case "$*" in
  *" port db 5432")
    if [ "$BLOCKING_QUERY" = "1" ]; then
      printf '%s\n' "$$" > "$QUERY_PID_FILE"
      sleep 5
    fi
    printf '127.0.0.1:15432\n'
    ;;
  *" port redis 6379") printf '127.0.0.1:16379\n' ;;
  *" exec -T db printenv POSTGRES_USER") printf 'heavy_user\n' ;;
  *" exec -T db printenv POSTGRES_PASSWORD") printf 'heavy_password\n' ;;
  *" exec -T db printenv POSTGRES_DB") printf 'test_heavy\n' ;;
  *" exec -T redis printenv REDIS_PASSWORD") printf 'redis_password\n' ;;
  *" down --volumes --remove-orphans")
    sleep "$CLEANUP_SLEEP_SECONDS"
    printf 'docker cleanup-finished\n' >> "$CALL_LOG"
    exit "$CLEANUP_EXIT_CODE"
    ;;
esac
""",
    )
    _write_executable(
        bin_dir / "uv",
        """#!/bin/sh
set -eu
if [ "$REJECT_INHERITED_STDIN" = "1" ] && [ ! -c /dev/fd/0 ]; then
  exit 91
fi
printf 'uv %s RUN=%s DB=%s REDIS=%s\n' "$*" "${RUN_WORKLINE_INTEGRATION:-}" "${INTEGRATION_DATABASE_URL:-}" "${INTEGRATION_REDIS_URL:-}" >> "$CALL_LOG"
case "$*" in
  "run scripts/select_heavy_tests.py "*)
    if [ -n "${SELECTED_TEST:-}" ]; then
      printf '%s\n' "$SELECTED_TEST"
    fi
    ;;
  "run scripts/run_selected_heavy_tests.py "*)
    if [ "$TERMINATE_RUNNER" = "1" ]; then
      kill -TERM "$PPID"
    fi
    if [ "$RUNNER_SLEEP_SECONDS" != "0" ]; then
      if [ "$RUNNER_SPAWNS_CHILD" = "1" ]; then
        sleep "$RUNNER_SLEEP_SECONDS" &
        runner_child=$!
        printf '%s %s\n' "$$" "$runner_child" > "$RUNNER_PID_FILE"
        wait "$runner_child"
      else
        exec sleep "$RUNNER_SLEEP_SECONDS"
      fi
    fi
    exit "$RUNNER_EXIT_CODE"
    ;;
esac
""",
    )
    monkeypatch.setenv("CALL_LOG", str(call_log))
    monkeypatch.setenv("SELECTED_TEST", selected_test)
    monkeypatch.setenv("RUNNER_EXIT_CODE", str(runner_exit_code))
    monkeypatch.setenv("TERMINATE_RUNNER", "1" if terminate_runner else "0")
    monkeypatch.setenv("CLEANUP_EXIT_CODE", str(cleanup_exit_code))
    monkeypatch.setenv("RUNNER_SLEEP_SECONDS", str(runner_sleep_seconds))
    monkeypatch.setenv("CLEANUP_SLEEP_SECONDS", str(cleanup_sleep_seconds))
    monkeypatch.setenv("RUNNER_SPAWNS_CHILD", "1" if runner_spawns_child else "0")
    monkeypatch.setenv("BLOCKING_QUERY", "1" if blocking_query else "0")
    monkeypatch.setenv("RUNNER_PID_FILE", str(tmp_path / "runner-pids.txt"))
    monkeypatch.setenv("QUERY_PID_FILE", str(tmp_path / "query-pid.txt"))
    monkeypatch.setenv("REJECT_INHERITED_STDIN", "1" if reject_inherited_stdin else "0")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return call_log


def _run_script() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), "--scope", "unstaged"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _run_script_in_pty() -> tuple[int, str]:
    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        os.chdir(REPO_ROOT)
        # forkpty 子进程必须直接替换自身，才能保留控制终端并覆盖 Bash 作业通知行为。
        os.execv("/bin/bash", ["/bin/bash", str(SCRIPT), "--scope", "unstaged"])  # noqa: S606
    output = bytearray()
    while True:
        try:
            chunk = os.read(master_fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
    _, wait_status = os.waitpid(child_pid, 0)
    os.close(master_fd)
    return os.waitstatus_to_exitcode(wait_status), output.decode(errors="replace")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_local_heavy_entry_selects_migrates_runs_and_cleans_isolated_services(tmp_path, monkeypatch) -> None:
    call_log = _fake_environment(tmp_path, monkeypatch)

    result = _run_script()

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert calls[0].startswith("uv run scripts/select_heavy_tests.py --scope unstaged ")
    assert any("docker-compose.ci-heavy.local.yml" in call and " up -d --wait" in call for call in calls)
    migration_index = next(index for index, call in enumerate(calls) if call.startswith("uv run alembic upgrade head "))
    runner_index = next(
        index for index, call in enumerate(calls) if call.startswith("uv run scripts/run_selected_heavy_tests.py ")
    )
    assert migration_index < runner_index
    assert "RUN=1" in calls[migration_index]
    assert "DB=postgresql+asyncpg://heavy_user:heavy_password@127.0.0.1:15432/test_heavy" in calls[migration_index]
    assert "REDIS=redis://:redis_password@127.0.0.1:16379/15" in calls[migration_index]
    assert not any(" exec -T db createdb " in call for call in calls)
    assert calls[-1] == CLEANUP_FINISHED


def test_local_heavy_entry_does_not_start_services_when_selector_is_empty(tmp_path, monkeypatch) -> None:
    call_log = _fake_environment(tmp_path, monkeypatch, selected_test="")

    result = _run_script()

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1
    assert calls[0].startswith("uv run scripts/select_heavy_tests.py --scope unstaged ")


def test_local_heavy_entry_cleans_services_when_runner_fails(tmp_path, monkeypatch) -> None:
    call_log = _fake_environment(tmp_path, monkeypatch, runner_exit_code=7)

    result = _run_script()

    assert result.returncode == 7
    assert call_log.read_text(encoding="utf-8").splitlines()[-1] == CLEANUP_FINISHED


def test_local_heavy_entry_cleans_services_when_terminated(tmp_path, monkeypatch) -> None:
    call_log = _fake_environment(tmp_path, monkeypatch, terminate_runner=True)

    result = _run_script()

    assert result.returncode == 143
    assert call_log.read_text(encoding="utf-8").splitlines()[-1] == CLEANUP_FINISHED


def test_local_heavy_entry_terminates_blocking_runner_before_cleanup(tmp_path, monkeypatch) -> None:
    call_log = _fake_environment(tmp_path, monkeypatch, runner_sleep_seconds=5)
    process = subprocess.Popen(
        ["/bin/bash", str(SCRIPT), "--scope", "unstaged"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if call_log.exists() and "run scripts/run_selected_heavy_tests.py" in call_log.read_text(encoding="utf-8"):
            break
        time.sleep(0.01)
    else:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise AssertionError("blocking HEAVY runner did not start")

    process.send_signal(signal.SIGTERM)
    timed_out = False
    try:
        returncode = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        returncode = process.wait()

    assert not timed_out
    assert returncode == 143
    assert call_log.read_text(encoding="utf-8").splitlines()[-1] == CLEANUP_FINISHED


def test_local_heavy_entry_terminates_the_blocking_runner_process_tree(tmp_path, monkeypatch) -> None:
    call_log = _fake_environment(
        tmp_path,
        monkeypatch,
        runner_sleep_seconds=5,
        runner_spawns_child=True,
    )
    runner_pid_file = tmp_path / "runner-pids.txt"
    process = subprocess.Popen(
        ["/bin/bash", str(SCRIPT), "--scope", "unstaged"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not runner_pid_file.exists():
        time.sleep(0.01)
    if not runner_pid_file.exists():
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise AssertionError("blocking HEAVY runner process tree did not start")

    runner_pid, descendant_pid = map(int, runner_pid_file.read_text(encoding="utf-8").split())
    process.send_signal(signal.SIGTERM)
    returncode = process.wait(timeout=1)

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and (_process_exists(runner_pid) or _process_exists(descendant_pid)):
        time.sleep(0.01)

    assert returncode == 143
    assert not _process_exists(runner_pid)
    assert not _process_exists(descendant_pid)
    assert call_log.read_text(encoding="utf-8").splitlines()[-1] == CLEANUP_FINISHED


def test_local_heavy_entry_terminates_a_blocking_compose_query(tmp_path, monkeypatch) -> None:
    call_log = _fake_environment(tmp_path, monkeypatch, blocking_query=True)
    query_pid_file = tmp_path / "query-pid.txt"
    process = subprocess.Popen(
        ["/bin/bash", str(SCRIPT), "--scope", "unstaged"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not query_pid_file.exists():
        time.sleep(0.01)
    if not query_pid_file.exists():
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise AssertionError("blocking Compose query did not start")

    query_pid = int(query_pid_file.read_text(encoding="utf-8"))
    process.send_signal(signal.SIGTERM)
    returncode = process.wait(timeout=1)

    assert returncode == 143
    assert not _process_exists(query_pid)
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert sum("down --volumes --remove-orphans" in call for call in calls) == 1
    assert calls[-1] == CLEANUP_FINISHED


def test_local_heavy_entry_does_not_pass_the_callers_stdin_to_background_commands(tmp_path, monkeypatch) -> None:
    _fake_environment(tmp_path, monkeypatch, reject_inherited_stdin=True)
    input_file = tmp_path / "caller-input.txt"
    input_file.write_text("unused\n", encoding="utf-8")

    with input_file.open(encoding="utf-8") as caller_input:
        result = subprocess.run(
            ["/bin/bash", str(SCRIPT), "--scope", "unstaged"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            stdin=caller_input,
        )

    assert result.returncode == 0, result.stderr


def test_local_heavy_entry_keeps_successful_terminal_output_free_of_job_notifications(tmp_path, monkeypatch) -> None:
    _fake_environment(tmp_path, monkeypatch)

    returncode, output = _run_script_in_pty()

    assert returncode == 0, output
    assert output == ""


def test_local_heavy_entry_finishes_cleanup_after_repeated_termination_signal(tmp_path, monkeypatch) -> None:
    call_log = _fake_environment(
        tmp_path,
        monkeypatch,
        runner_sleep_seconds=5,
        cleanup_sleep_seconds=0.2,
    )
    process = subprocess.Popen(
        ["/bin/bash", str(SCRIPT), "--scope", "unstaged"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if call_log.exists() and "run scripts/run_selected_heavy_tests.py" in call_log.read_text(encoding="utf-8"):
            break
        time.sleep(0.01)
    else:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise AssertionError("blocking HEAVY runner did not start")

    process.send_signal(signal.SIGTERM)
    while time.monotonic() < deadline:
        if "down --volumes --remove-orphans" in call_log.read_text(encoding="utf-8"):
            break
        time.sleep(0.01)
    else:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise AssertionError("HEAVY cleanup did not start")

    process.send_signal(signal.SIGTERM)
    try:
        returncode = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise

    assert returncode == 143
    assert call_log.read_text(encoding="utf-8").splitlines()[-1] == CLEANUP_FINISHED


def test_local_heavy_entry_reports_cleanup_failure(tmp_path, monkeypatch) -> None:
    _fake_environment(tmp_path, monkeypatch, cleanup_exit_code=1)

    result = _run_script()

    assert result.returncode == 1
    assert "HEAVY 临时服务清理失败" in result.stderr


def test_local_heavy_entry_uses_an_isolated_junit_path_per_run(tmp_path, monkeypatch) -> None:
    call_log = _fake_environment(tmp_path, monkeypatch)

    first_result = _run_script()
    second_result = _run_script()

    assert first_result.returncode == 0, first_result.stderr
    assert second_result.returncode == 0, second_result.stderr
    runner_calls = [
        call
        for call in call_log.read_text(encoding="utf-8").splitlines()
        if call.startswith("uv run scripts/run_selected_heavy_tests.py ")
    ]
    assert len(runner_calls) == 2
    assert len({call.split()[4] for call in runner_calls}) == 2


def test_local_heavy_entry_rejects_missing_selector_arguments_without_temp_state(tmp_path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    _write_executable(
        bin_dir / "mktemp",
        """#!/bin/sh
set -eu
mkdir "$TEMP_ROOT/leaked"
printf '%s\n' "$TEMP_ROOT/leaked"
""",
    )
    monkeypatch.setenv("TEMP_ROOT", str(temp_root))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert list(temp_root.iterdir()) == []
