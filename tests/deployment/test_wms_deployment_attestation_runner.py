"""共享 WMS deployment attestation runner 的真实 shell 控制流。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts/run_wms_deployment_attestation.sh"


def _write_fake_docker(path: Path) -> None:
    path.write_text(
        """#!/bin/bash
set -e
echo "$*" >> "$FAKE_DOCKER_LOG"
if [ "$1" = "compose" ]; then
  shift
  while [ "$1" != "pull" ] && [ "$1" != "run" ]; do shift; done
  action=$1
  shift
  if [ "$action" = "pull" ]; then
    [ "${FAKE_DOCKER_FAIL:-}" != "pull" ] || exit 11
    exit 0
  fi
  while [ "$1" != "--entrypoint" ]; do shift; done
  service="${@: -2:1}"
  echo "cid-$service"
  exit 0
fi
if [ "$1" = "inspect" ]; then
  container_id="${@: -1}"
  service="${container_id#cid-}"
  if [[ "$*" == *".Image"* ]]; then
    printf 'sha256:%064d\\n' 0
    exit 0
  fi
  case "$service" in
    api) role=api ;;
    celery) role=wes-worker ;;
    celery-wms-fulfillment) role=fulfillment-worker ;;
    celery_beat) role=beat ;;
  esac
  [ "${FAKE_DOCKER_FAIL:-}" != "role:$service" ] || role=wrong
  echo "WMS_DEPLOYMENT_ROLE=$role"
  exit 0
fi
if [ "$1" = "exec" ]; then
  container_id=""
  for argument in "$@"; do
    [[ "$argument" == cid-* ]] && container_id="$argument"
  done
  service="${container_id#cid-}"
  if [[ "$*" == *" emit"* ]]; then
    [ "${FAKE_DOCKER_FAIL:-}" != "emit:$service" ] || exit 17
    echo "{\\"service\\":\\"$service\\"}"
    exit 0
  fi
  [ "${FAKE_DOCKER_FAIL:-}" != "verify" ] || exit 18
  line_count=$(grep -c '^{' || true)
  [ "$line_count" -eq 4 ] || exit 19
  echo '{"verified":true}'
  exit 0
fi
if [ "$1" = "rm" ]; then exit 0; fi
exit 99
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_runner(tmp_path: Path, *, failure: str = "") -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_docker(fake_bin / "docker")
    log_path = tmp_path / "docker.log"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FAKE_DOCKER_LOG": str(log_path),
            "FAKE_DOCKER_FAIL": failure,
        }
    )
    completed = subprocess.run(
        [
            "/bin/bash",
            str(RUNNER),
            "--compose-file",
            "docker-compose.yml",
            "--compose-file",
            "docker-compose.deploy.yml",
            "--env-file",
            ".env.prod",
            "--profile",
            "prod",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    return completed, lines


def test_runner_pulls_then_binds_each_emit_and_verify_to_actual_containers(tmp_path: Path) -> None:
    completed, lines = _run_runner(tmp_path)

    assert completed.returncode == 0, completed.stderr
    pull_index = next(
        index for index, line in enumerate(lines) if " compose " in f" {line} " and " pull " in f" {line} "
    )
    first_run_index = next(index for index, line in enumerate(lines) if " run -d " in f" {line} ")
    first_inspect_index = next(index for index, line in enumerate(lines) if line.startswith("inspect "))
    assert pull_index < first_run_index < first_inspect_index
    assert sum(line.startswith("exec ") and " emit" in line for line in lines) == 4
    assert any(line.startswith("exec ") and " verify-stdin" in line for line in lines)
    assert sum(line.startswith("rm -f cid-") for line in lines) == 4
    assert completed.stdout.strip() == '{"verified":true}'


@pytest.mark.parametrize("failure", ("pull", "role:celery", "emit:celery", "verify"))
def test_runner_fails_closed_and_cleans_created_containers(tmp_path: Path, failure: str) -> None:
    completed, lines = _run_runner(tmp_path, failure=failure)

    assert completed.returncode != 0
    created = [f"cid-{line.split()[-2]}" for line in lines if " run -d " in f" {line} "]
    removed = [line.split()[-1] for line in lines if line.startswith("rm -f ")]
    assert set(created) <= set(removed)
