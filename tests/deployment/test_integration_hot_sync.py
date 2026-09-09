"""联调服务器前后端源码热更新入口测试。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_SCRIPT = REPO_ROOT / "scripts/integration-hot-sync.sh"
REMOTE_SCRIPT = REPO_ROOT / "scripts/integration-hot-remote.sh"
GIT_EXECUTABLE = shutil.which("git")
TAR_EXECUTABLE = shutil.which("tar")
if GIT_EXECUTABLE is None or TAR_EXECUTABLE is None:
    raise RuntimeError("git and tar are required for integration hot sync tests")


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    local_variables = subprocess.run(
        [GIT_EXECUTABLE, "rev-parse", "--local-env-vars"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for variable in local_variables:
        environment.pop(variable, None)
    return environment


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def _init_source_repositories(tmp_path: Path) -> tuple[Path, Path, str]:
    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    git_environment = _git_environment()
    for repository in (backend, frontend):
        repository.mkdir()
        subprocess.run([GIT_EXECUTABLE, "init", "-q"], cwd=repository, env=git_environment, check=True)
        subprocess.run(
            [GIT_EXECUTABLE, "config", "user.email", "test@example.com"],
            cwd=repository,
            env=git_environment,
            check=True,
        )
        subprocess.run([GIT_EXECUTABLE, "config", "user.name", "Test"], cwd=repository, env=git_environment, check=True)

    backend_files = {
        "main.py": "app = object()\n",
        "pyproject.toml": "[project]\nname = 'backend'\nversion = '0'\n",
        "uv.lock": "version = 1\n",
        "migrations/env.py": "# baseline\n",
        "src/app.py": "VALUE = 'base'\n",
        "deployment/plugin_composition.py": "# composition\n",
        "workline_plugins/rough_sorter/src/plugin.py": "# plugin\n",
        "workline_plugins/rough_sorter/.venv/lib/runtime.bin": "large cache\n",
        "workline_plugins/rough_sorter/tests/test_plugin.py": "def test_plugin(): pass\n",
        "scripts/frontend-dev-entrypoint.sh": "#!/bin/sh\nexec pnpm dev\n",
        "docker-compose.integration-hot.yml": "services: {}\n",
    }
    frontend_files = {
        ".npmrc": "registry=https://registry.npmmirror.com\n",
        "package.json": '{"name":"frontend"}\n',
        "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        "index.html": "<div id='app'></div>\n",
        "vite.config.ts": "export default {}\n",
        "tsconfig.json": "{}\n",
        "src/main.ts": "export const value = 'base'\n",
    }
    for root, files in ((backend, backend_files), (frontend, frontend_files)):
        for relative_path, content in files.items():
            target = root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        subprocess.run([GIT_EXECUTABLE, "add", "."], cwd=root, env=git_environment, check=True)
        subprocess.run([GIT_EXECUTABLE, "commit", "-qm", "baseline"], cwd=root, env=git_environment, check=True)

    backend_revision = subprocess.run(
        [GIT_EXECUTABLE, "rev-parse", "HEAD"],
        cwd=backend,
        env=git_environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return backend, frontend, backend_revision


def _fake_transport(tmp_path: Path, backend_revision: str, *, hot_mode: bool = False) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    trace = tmp_path / "transport.trace"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(
        "#!/bin/sh\n"
        'printf "ssh %s\\n" "$*" >>"$HOT_TEST_TRACE"\n'
        'case "$*" in\n'
        f'  *" probe "*) printf "BACKEND_REVISION={backend_revision}\\nHOT_MODE={str(hot_mode).lower()}\\n" ;;\n'
        '  *" prepare "*) printf "/opt/wes_backend/.integration-hot/uploads/test-release\\n" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    scp = bin_dir / "scp"
    scp.write_text(
        "#!/bin/sh\n"
        'printf "scp %s\\n" "$*" >>"$HOT_TEST_TRACE"\n'
        'for argument in "$@"; do\n'
        '  case "$argument" in *.tar.gz) [ -f "$argument" ] && tar -tzf "$argument" >>"$HOT_TEST_TRACE" ;; esac\n'
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    scp.chmod(0o755)
    return bin_dir, trace


def test_bootstrap_transfers_runtime_sources_without_local_state(tmp_path: Path) -> None:
    backend, frontend, backend_revision = _init_source_repositories(tmp_path)
    (backend / "src/app.py").write_text("VALUE = 'hot'\n", encoding="utf-8")
    (backend / ".env").write_text("SECRET=backend\n", encoding="utf-8")
    (frontend / ".env.local").write_text("SECRET=frontend\n", encoding="utf-8")
    bin_dir, trace = _fake_transport(tmp_path, backend_revision)

    result = _run(
        ["/bin/bash", str(LOCAL_SCRIPT), "bootstrap"],
        cwd=backend,
        env=_git_environment()
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WES_BACKEND_ROOT": str(backend),
            "WES_FRONTEND_ROOT": str(frontend),
            "WES_INTEGRATION_SSH_KEY": str(tmp_path / "id_test"),
            "HOT_TEST_TRACE": str(trace),
        },
    )

    assert result.returncode == 0, result.stderr
    transferred = trace.read_text(encoding="utf-8")
    assert "src/app.py" in transferred
    assert "src/main.ts" in transferred
    assert ".env\n" not in transferred
    assert ".env.local" not in transferred
    assert "workline_plugins/rough_sorter/.venv" not in transferred
    assert "workline_plugins/rough_sorter/tests" not in transferred
    assert transferred.count("scp ") == 2
    assert "CANTAISYS@100.94.216.118" in transferred
    assert "/srv/wes/app/current-single" in transferred


def test_bootstrap_refuses_backend_dependency_or_migration_drift_before_transfer(tmp_path: Path) -> None:
    backend, frontend, backend_revision = _init_source_repositories(tmp_path)
    (backend / "uv.lock").write_text("version = 2\n", encoding="utf-8")
    bin_dir, trace = _fake_transport(tmp_path, backend_revision)

    result = _run(
        ["/bin/bash", str(LOCAL_SCRIPT), "bootstrap"],
        cwd=backend,
        env=_git_environment()
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WES_BACKEND_ROOT": str(backend),
            "WES_FRONTEND_ROOT": str(frontend),
            "HOT_TEST_TRACE": str(trace),
        },
    )

    assert result.returncode == 1
    assert "后端依赖或 migration 与联调服务器基础镜像不一致" in result.stderr
    assert "scp " not in trace.read_text(encoding="utf-8")


def test_sync_refuses_to_reenable_hot_mode_after_an_immutable_release(tmp_path: Path) -> None:
    backend, frontend, backend_revision = _init_source_repositories(tmp_path)
    bin_dir, trace = _fake_transport(tmp_path, backend_revision, hot_mode=False)

    result = _run(
        ["/bin/bash", str(LOCAL_SCRIPT), "sync"],
        cwd=backend,
        env=_git_environment()
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WES_BACKEND_ROOT": str(backend),
            "WES_FRONTEND_ROOT": str(frontend),
            "HOT_TEST_TRACE": str(trace),
        },
    )

    assert result.returncode == 1
    assert "当前服务器未启用热更新模式，请执行 bootstrap" in result.stderr
    assert "scp " not in trace.read_text(encoding="utf-8")


def test_remote_bootstrap_activates_both_sources_and_records_baseline(tmp_path: Path) -> None:
    deploy_root = tmp_path / "deploy"
    upload = deploy_root / ".integration-hot/uploads/release-1"
    source = tmp_path / "source"
    bin_dir = tmp_path / "bin"
    docker_trace = tmp_path / "docker.trace"
    upload.mkdir(parents=True)
    source.mkdir()
    bin_dir.mkdir()
    (deploy_root / ".env").write_text("ENV=test\n", encoding="utf-8")
    (deploy_root / "docker-compose.test-deploy.yml").write_text("services: {}\n", encoding="utf-8")
    for name, files in {
        "backend": {
            "main.py": "app = object()\n",
            "src/app.py": "VALUE = 'hot'\n",
            "docker-compose.integration-hot.yml": "services: {}\n",
        },
        "frontend": {"src/main.ts": "export const value = 'hot'\n", "package.json": "{}\n"},
    }.items():
        root = source / name
        for relative_path, content in files.items():
            target = root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        subprocess.run([TAR_EXECUTABLE, "-czf", str(upload / f"{name}.tar.gz"), "-C", root, "."], check=True)

    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >>"$HOT_DOCKER_TRACE"\n'
        'case "$*" in\n'
        '  *"inspect wes_api_test"*) printf "registry/backend@sha256:test\\n" ;;\n'
        '  *"inspect wes_frontend_test"*) printf "registry/frontend@sha256:test\\n" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    result = _run(
        [
            "/bin/bash",
            str(REMOTE_SCRIPT),
            "activate",
            str(deploy_root),
            "release-1",
            "bootstrap",
            "backend-baseline",
            "frontend-baseline",
            "control-baseline",
        ],
        cwd=deploy_root,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOT_DOCKER_TRACE": str(docker_trace)},
    )

    assert result.returncode == 0, result.stderr
    hot_root = deploy_root / ".integration-hot/source"
    assert (hot_root / "backend/src/app.py").read_text(encoding="utf-8") == "VALUE = 'hot'\n"
    assert (hot_root / "frontend/src/main.ts").is_file()
    assert (hot_root / "frontend/node_modules").is_dir()
    baseline = (deploy_root / ".integration-hot/baseline.env").read_text(encoding="utf-8")
    assert "BACKEND_PROTECTED_SHA=backend-baseline" in baseline
    assert "FRONTEND_PROTECTED_SHA=frontend-baseline" in baseline
    docker_calls = docker_trace.read_text(encoding="utf-8") if docker_trace.exists() else ""
    assert "compose" in docker_calls
    assert "--no-build" in docker_calls


@pytest.mark.parametrize("recreate_exit", [0, 1])
def test_remote_sync_waits_for_recreated_sources(tmp_path: Path, recreate_exit: int) -> None:
    deploy_root = tmp_path / "deploy"
    state = deploy_root / ".integration-hot"
    upload = state / "uploads/release-2"
    source = tmp_path / "source"
    bin_dir = tmp_path / "bin"
    docker_trace = tmp_path / "docker.trace"
    upload.mkdir(parents=True)
    source.mkdir()
    bin_dir.mkdir()
    (deploy_root / ".env").write_text("ENV=test\n", encoding="utf-8")
    (deploy_root / "docker-compose.test-deploy.yml").write_text("services: {}\n", encoding="utf-8")
    (state / "baseline.env").write_text(
        "BACKEND_PROTECTED_SHA=backend-baseline\n"
        "FRONTEND_PROTECTED_SHA=frontend-baseline\n"
        "CONTROL_SHA=control-baseline\n",
        encoding="utf-8",
    )
    for name, files in {
        "backend": {
            "main.py": "app = object()\n",
            "src/app.py": "VALUE = 'updated'\n",
            "docker-compose.integration-hot.yml": "services: {}\n",
        },
        "frontend": {"src/main.ts": "export const value = 'updated'\n", "package.json": "{}\n"},
    }.items():
        root = source / name
        for relative_path, content in files.items():
            target = root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        subprocess.run([TAR_EXECUTABLE, "-czf", str(upload / f"{name}.tar.gz"), "-C", root, "."], check=True)

    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >>"$HOT_DOCKER_TRACE"\n'
        'case "$*" in\n'
        '  *"inspect wes_api_test"*) printf "registry/backend@sha256:test\\n" ;;\n'
        '  *"--force-recreate"*) exit "$HOT_RECREATE_EXIT" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    result = _run(
        [
            "/bin/bash",
            str(REMOTE_SCRIPT),
            "activate",
            str(deploy_root),
            "release-2",
            "sync",
            "backend-baseline",
            "frontend-baseline",
            "control-baseline",
        ],
        cwd=deploy_root,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOT_DOCKER_TRACE": str(docker_trace),
            "HOT_RECREATE_EXIT": str(recreate_exit),
        },
    )

    assert result.returncode == recreate_exit, result.stderr
    docker_calls = docker_trace.read_text(encoding="utf-8") if docker_trace.exists() else ""
    assert "restart" not in docker_calls
    assert " up -d --no-build --force-recreate --wait --wait-timeout 300 " in docker_calls
    assert upload.exists() is bool(recreate_exit)


def test_remote_check_retries_transient_frontend_probe(tmp_path: Path) -> None:
    deploy_root = tmp_path / "deploy"
    state = deploy_root / ".integration-hot"
    bin_dir = tmp_path / "bin"
    docker_trace = tmp_path / "docker.trace"
    frontend_attempts = tmp_path / "frontend.attempts"
    state.mkdir(parents=True)
    bin_dir.mkdir()
    (deploy_root / ".env").write_text("ENV=test\n", encoding="utf-8")
    (deploy_root / "docker-compose.test-deploy.yml").write_text("services: {}\n", encoding="utf-8")
    (state / "baseline.env").write_text("BACKEND_PROTECTED_SHA=backend-baseline\n", encoding="utf-8")

    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >>"$HOT_DOCKER_TRACE"\n'
        'case "$*" in\n'
        '  *"inspect wes_api_test"*".Mounts"*) printf "/app/src\\n" ;;\n'
        '  *"exec wes_frontend_test"*)\n'
        '    attempts=0; [ ! -f "$HOT_FRONTEND_ATTEMPTS" ] || attempts=$(cat "$HOT_FRONTEND_ATTEMPTS")\n'
        '    attempts=$((attempts + 1)); printf "%s\\n" "$attempts" >"$HOT_FRONTEND_ATTEMPTS"\n'
        '    [ "$attempts" -gt 1 ] ;;\n'
        '  *"inspect "*) printf "running\\n" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    timeout = bin_dir / "timeout"
    timeout.write_text('#!/bin/sh\nshift\nexec "$@"\n', encoding="utf-8")
    timeout.chmod(0o755)

    result = _run(
        ["/bin/bash", str(REMOTE_SCRIPT), "check", str(deploy_root)],
        cwd=deploy_root,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOT_DOCKER_TRACE": str(docker_trace),
            "HOT_FRONTEND_ATTEMPTS": str(frontend_attempts),
            "HOT_CHECK_TIMEOUT": "1",
            "HOT_CHECK_INTERVAL": "0",
        },
    )

    assert result.returncode == 0, result.stderr
    assert frontend_attempts.read_text(encoding="utf-8").strip() == "2"
    docker_calls = docker_trace.read_text(encoding="utf-8")
    assert "worker_healthcheck.py" in docker_calls
    assert "beat_healthcheck.py" in docker_calls
    assert "http://127.0.0.1:5173/" in docker_calls


def test_remote_sync_rejects_changed_protected_inputs_before_source_mutation(tmp_path: Path) -> None:
    deploy_root = tmp_path / "deploy"
    state = deploy_root / ".integration-hot"
    live_file = state / "source/backend/src/app.py"
    upload = state / "uploads/release-2"
    live_file.parent.mkdir(parents=True)
    upload.mkdir(parents=True)
    (deploy_root / ".env").write_text("ENV=test\n", encoding="utf-8")
    (deploy_root / "docker-compose.test-deploy.yml").write_text("services: {}\n", encoding="utf-8")
    live_file.write_text("VALUE = 'current'\n", encoding="utf-8")
    (state / "baseline.env").write_text(
        "BACKEND_PROTECTED_SHA=backend-baseline\n"
        "FRONTEND_PROTECTED_SHA=frontend-baseline\n"
        "CONTROL_SHA=control-baseline\n",
        encoding="utf-8",
    )

    result = _run(
        [
            "/bin/bash",
            str(REMOTE_SCRIPT),
            "activate",
            str(deploy_root),
            "release-2",
            "sync",
            "backend-baseline",
            "frontend-changed",
            "control-baseline",
        ],
        cwd=tmp_path,
        env=os.environ,
    )

    assert result.returncode == 1
    assert "受保护输入已变化，请重新执行 bootstrap" in result.stderr
    assert live_file.read_text(encoding="utf-8") == "VALUE = 'current'\n"


def test_remote_probe_supports_current_single_integration_layout(tmp_path: Path) -> None:
    deploy_root = tmp_path / "current-single"
    bin_dir = tmp_path / "bin"
    docker_trace = tmp_path / "docker.trace"
    deploy_root.mkdir()
    bin_dir.mkdir()
    (deploy_root / ".env.integration").write_text("ENV=int\n", encoding="utf-8")
    compose = deploy_root / "compose.sh"
    compose.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    compose.chmod(0o755)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >>"$HOT_DOCKER_TRACE"\n'
        'case "$*" in\n'
        '  "exec -i "*) cat >/dev/null; printf "protected-sha\\n" ;;\n'
        '  *"org.opencontainers.image.revision"*) printf "revision-sha\\n" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    result = _run(
        ["/bin/bash", str(REMOTE_SCRIPT), "probe", str(deploy_root)],
        cwd=deploy_root,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOT_DOCKER_TRACE": str(docker_trace)},
    )

    assert result.returncode == 0, result.stderr
    assert "DEPLOY_LAYOUT=integration" in result.stdout
    assert result.stdout.count("BACKEND_REVISION=revision-sha") == 4
    assert result.stdout.count("BACKEND_PROTECTED_SHA=protected-sha") == 4
    assert "HOT_MODE=false" in result.stdout
    docker_calls = docker_trace.read_text(encoding="utf-8")
    assert "inspect wes_api_int" in docker_calls
    assert "exec -i wes_integration-celery-1" in docker_calls


def test_remote_disable_restores_saved_immutable_images(tmp_path: Path) -> None:
    deploy_root = tmp_path / "deploy"
    state = deploy_root / ".integration-hot"
    bin_dir = tmp_path / "bin"
    docker_trace = tmp_path / "docker.trace"
    state.mkdir(parents=True)
    bin_dir.mkdir()
    (deploy_root / ".env").write_text("ENV=test\n", encoding="utf-8")
    (deploy_root / "docker-compose.test-deploy.yml").write_text("services: {}\n", encoding="utf-8")
    (state / "baseline.env").write_text(
        "BACKEND_PROTECTED_SHA=backend-baseline\n"
        "FRONTEND_PROTECTED_SHA=frontend-baseline\n"
        "CONTROL_SHA=control-baseline\n"
        "BASE_BACKEND_IMAGE=registry/backend@sha256:abc\n"
        "BASE_FRONTEND_IMAGE=registry/frontend@sha256:def\n",
        encoding="utf-8",
    )
    docker = bin_dir / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s|%s|%s\\n" "$BACKEND_IMAGE" "$FRONTEND_IMAGE" "$*" >>"$HOT_DOCKER_TRACE"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)

    result = _run(
        ["/bin/bash", str(REMOTE_SCRIPT), "disable", str(deploy_root)],
        cwd=tmp_path,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOT_DOCKER_TRACE": str(docker_trace)},
    )

    assert result.returncode == 0, result.stderr
    docker_calls = docker_trace.read_text(encoding="utf-8")
    assert "registry/backend@sha256:abc|registry/frontend@sha256:def|compose" in docker_calls
    assert not (state / "baseline.env").exists()


def test_hot_frontend_allows_vite_generated_files_and_slow_first_install() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.integration-hot.yml").read_text(encoding="utf-8"))
    frontend = compose["services"]["frontend"]

    assert "${HOT_FRONTEND_ROOT:?HOT_FRONTEND_ROOT is required}:/app:rw,z" in frontend["volumes"]
    assert frontend["healthcheck"]["start_period"] == "300s"
