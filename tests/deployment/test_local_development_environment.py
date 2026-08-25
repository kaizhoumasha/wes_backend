import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _compose(name: str) -> dict:
    return yaml.safe_load((BACKEND_ROOT / name).read_text(encoding="utf-8"))


def _run_development_check(
    tmp_path: Path,
    *,
    expected_root: Path,
    mounted_root: str,
    detached_frontend: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    trace_file = tmp_path / "dev-env.trace"
    bin_dir.mkdir()
    (expected_root / "package.json").write_text("{}", encoding="utf-8")
    (expected_root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    required_services = (
        "db redis api celery celery-wms-fulfillment celery_beat mock_ecs mock_wms mock_wms_provider frontend nginx"
    )
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >>"$DEV_ENV_TRACE"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = inspect ]; then\n'
        '  case "$*" in *.Mounts*) printf "%s\\n" "$FRONTEND_MOUNT" ;; *) printf "running healthy\\n" ;; esac\n'
        "  exit 0\n"
        "fi\n"
        f"case \"$*\" in *'ps --status running --services'*) printf '%s\\n' {required_services} ;;\n"
        "  *'ps -q '*) service=; previous=; for value in \"$@\"; do "
        'if [ "$previous" = -q ]; then service=$value; break; fi; previous=$value; done; '
        "printf 'container-%s\\n' \"$service\" ;;\n"
        "  *) exit 0 ;; esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    curl = bin_dir / "curl"
    curl.write_text(
        '#!/bin/sh\nprintf "curl %s\\n" "$*" >>"$DEV_ENV_TRACE"\nexit 0\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)

    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        'if [ "$3" = symbolic-ref ]; then\n'
        '  if [ "$2" = "$WES_FRONTEND_ROOT" ] && [ "$DETACHED_FRONTEND" = true ]; then exit 1; fi\n'
        "  printf 'develop\\n'\n"
        'elif [ "$3" = rev-parse ]; then\n'
        "  printf '5d566bd92b15162c5acfcbe30bad6dde7da5c5f5\\n'\n"
        "fi\n",
        encoding="utf-8",
    )
    git.chmod(0o755)

    completed = subprocess.run(
        ["/bin/bash", str(BACKEND_ROOT / "scripts/dev-env.sh"), "check"],
        cwd=BACKEND_ROOT,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WES_FRONTEND_ROOT": str(expected_root),
            "FRONTEND_MOUNT": mounted_root,
            "DEV_ENV_TRACE": str(trace_file),
            "DETACHED_FRONTEND": str(detached_frontend).lower(),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, trace_file.read_text(encoding="utf-8").splitlines()


def test_development_check_accepts_docker_desktop_frontend_mount(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()

    completed, _ = _run_development_check(
        tmp_path,
        expected_root=frontend,
        mounted_root=f"/host_mnt{frontend}",
    )

    assert completed.returncode == 0, completed.stderr


def test_development_check_rejects_a_different_running_frontend_before_http_or_seed(tmp_path: Path) -> None:
    expected_frontend = tmp_path / "expected-frontend"
    running_frontend = tmp_path / "running-frontend"
    expected_frontend.mkdir()
    running_frontend.mkdir()

    completed, trace = _run_development_check(
        tmp_path,
        expected_root=expected_frontend,
        mounted_root=f"/host_mnt{running_frontend}",
    )

    assert completed.returncode == 1
    assert f"expected={expected_frontend}" in completed.stderr
    assert f"actual={running_frontend}" in completed.stderr
    assert not any("curl" in command for command in trace)
    assert not any("seed_initial_data.py --check" in command for command in trace)


def test_development_check_reports_a_detached_frontend_with_its_root(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()

    completed, _ = _run_development_check(
        tmp_path,
        expected_root=frontend,
        mounted_root=str(frontend),
        detached_frontend=True,
    )

    commit = "5d566bd92b15162c5acfcbe30bad6dde7da5c5f5"
    assert completed.returncode == 0, completed.stderr
    assert f"后端: develop {commit}" in completed.stdout
    assert f"前端: detached {commit} root={frontend}" in completed.stdout


def test_backend_development_services_mount_all_runtime_source_roots() -> None:
    compose = _compose("docker-compose.yml")
    expected_mounts = {
        "${SOURCE_MOUNT:-./src}:/app/src:rw",
        "./main.py:/app/main.py:ro",
        "./deployment:/app/deployment:rw",
        "./packages/wes_plugin_sdk/src:/app/packages/wes_plugin_sdk/src:rw",
        "./workline_plugins/rough_sorter/src:/app/workline_plugins/rough_sorter/src:rw",
    }

    for service_name in ("api", "celery", "celery_beat"):
        assert expected_mounts <= set(compose["services"][service_name]["volumes"])

    fulfillment = compose["services"]["celery-wms-fulfillment"]
    assert fulfillment["extends"]["service"] == "celery"
    assert "dev_worker_autoreload.sh" in fulfillment["command"]
    assert fulfillment["environment"]["CELERY_WORKER_CONCURRENCY"] == "1"


def test_ecs_mock_healthcheck_uses_the_unconditional_root_endpoint() -> None:
    compose = _compose("docker-compose.yml")

    assert compose["services"]["mock_ecs"]["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "-f",
        "http://localhost:8010/",
    ]
    runner = (BACKEND_ROOT / "scripts/dev-env.sh").read_text(encoding="utf-8")
    assert '"http://127.0.0.1:8010/"' in runner
    assert '"http://127.0.0.1:8010/api/v1/device/status"' not in runner


def test_celery_development_reload_watches_core_sdk_and_plugin_sources() -> None:
    expected_paths = (
        "/app/src",
        "/app/deployment",
        "/app/packages/wes_plugin_sdk/src",
        "/app/workline_plugins/rough_sorter/src",
    )

    for relative_path in (
        "src/celery_app/dev_worker_autoreload.sh",
        "src/celery_app/dev_beat_autoreload.sh",
    ):
        source = (BACKEND_ROOT / relative_path).read_text(encoding="utf-8")
        assert "CELERY_WATCH_PATHS" in source
        for path in expected_paths:
            assert path in source


def test_frontend_development_overlay_enables_reliable_hmr_without_mutating_lockfile() -> None:
    overlay = _compose("docker-compose.frontend.yml")
    frontend = overlay["services"]["frontend"]

    assert "${FRONTEND_ROOT:-../wes_frontend}:/app:rw" in frontend["volumes"]
    assert frontend["environment"]["CHOKIDAR_USEPOLLING"] == "true"
    assert frontend["environment"]["CHOKIDAR_INTERVAL"] == "300"

    entrypoint = (BACKEND_ROOT / "scripts/frontend-dev-entrypoint.sh").read_text(encoding="utf-8")
    assert "pnpm install --frozen-lockfile" in entrypoint
    assert "trying without --frozen-lockfile" not in entrypoint
    assert "if pnpm install; then" not in entrypoint


def test_development_overlay_mounts_a_valid_mock_wms_profile_for_every_runtime_process() -> None:
    from src.app.wms_integration.endpoint_compiler import compile_wms_provider_profile
    from src.app.wms_integration.provider_profile import load_wms_provider_profile

    overlay = _compose("docker-compose.frontend.yml")
    expected_mount = (
        "${WES_DEV_PROVIDER_PROFILE_FILE:-./deployment/dev/wms-provider.yaml}:/run/wes/wms-provider.yaml:ro"
    )
    for service_name in ("api", "celery", "celery-wms-fulfillment", "celery_beat"):
        service = overlay["services"][service_name]
        assert service["environment"]["WMS_PROVIDER_PROFILE_FILE"] == "/run/wes/wms-provider.yaml"
        assert expected_mount in service["volumes"]

    profile = load_wms_provider_profile(BACKEND_ROOT / "deployment/dev/wms-provider.yaml")
    assert profile.server_url == "http://mock-wms-provider:8012"
    compiled_profile = compile_wms_provider_profile(profile)
    assert compiled_profile.transport_submit_path == "/api/v1/wes/transport-requests"
    compose = _compose("docker-compose.yml")
    provider = compose["services"]["mock_wms_provider"]
    assert provider["environment"]["WMS_PROVIDER_PROFILE_FILE"] == "/run/wes/wms-provider.yaml"
    assert "./tests:/app/tests:rw" in provider["volumes"]
    assert "./src:/app/src:rw" in provider["volumes"]
    assert provider["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "-f",
        "http://localhost:8012/",
    ]


def test_frontend_entrypoint_reinstalls_native_dependencies_when_container_platform_changes(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    bin_dir = tmp_path / "bin"
    node_modules = app_dir / "node_modules"
    app_dir.mkdir()
    bin_dir.mkdir()
    node_modules.mkdir()
    (app_dir / "package.json").write_text('{"name":"frontend-platform-test"}', encoding="utf-8")
    lockfile = app_dir / "pnpm-lock.yaml"
    lockfile.write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (node_modules / ".modules.yaml").write_text("layoutVersion: 5\n", encoding="utf-8")
    lock_hash = hashlib.sha256(lockfile.read_bytes()).hexdigest()
    (node_modules / ".pnpm-lock.sha256").write_text(lock_hash, encoding="utf-8")

    call_log = tmp_path / "pnpm-calls.log"
    for name, source in {
        "corepack": "#!/bin/sh\nexit 0\n",
        "node": "#!/bin/sh\nprintf 'linux/x64\\n'\n",
        "pnpm": '#!/bin/sh\nprintf "%s\\n" "$*" >>"$PNPM_CALL_LOG"\n',
    }.items():
        executable = bin_dir / name
        executable.write_text(source, encoding="utf-8")
        executable.chmod(0o755)

    env = os.environ | {
        "FRONTEND_APP_DIR": str(app_dir),
        "PNPM_HOME": str(bin_dir),
        "PNPM_STORE_DIR": str(tmp_path / "store"),
        "PNPM_CALL_LOG": str(call_log),
    }
    subprocess.run(
        ["/bin/sh", str(BACKEND_ROOT / "scripts/frontend-dev-entrypoint.sh")],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "install --frozen-lockfile" in call_log.read_text(encoding="utf-8")


def test_canonical_development_runner_migrates_seeds_checks_and_preserves_data() -> None:
    runner = (BACKEND_ROOT / "scripts/dev-env.sh").read_text(encoding="utf-8")

    assert "docker-compose.frontend.yml" in runner
    assert "alembic upgrade head" in runner
    assert "scripts/data/seed_initial_data.py" in runner
    assert "--frontend-path" not in runner
    assert "/workspace/frontend" not in runner
    assert "--check" in runner
    assert 'case "$COMMAND" in' in runner
    assert "down --remove-orphans" in runner
    assert "down -v" not in runner
    assert "down --volumes" not in runner


def test_development_runner_reserves_a_project_scoped_local_environment() -> None:
    runner = (BACKEND_ROOT / "scripts/dev-env.sh").read_text(encoding="utf-8")
    overlay = _compose("docker-compose.frontend.yml")

    assert 'DEV_COMPOSE_PROJECT="${WES_DEV_COMPOSE_PROJECT:-wes_backend_dev}"' in runner
    assert '--project-name "$DEV_COMPOSE_PROJECT"' in runner

    expected_containers = {
        "api": "api",
        "celery_beat": "celery_beat",
        "mock_ecs": "mock_ecs",
        "mock_wms": "mock_wms",
        "mock_wms_provider": "mock_wms_provider",
        "frontend": "frontend",
        "nginx": "nginx",
        "db": "postgres",
        "redis": "redis",
    }
    for service_name, suffix in expected_containers.items():
        assert overlay["services"][service_name]["container_name"] == (
            f"${{COMPOSE_PROJECT_NAME:-wes_backend_dev}}_{suffix}"
        )

    assert overlay["volumes"]["frontend_node_modules"]["name"] == (
        "${COMPOSE_PROJECT_NAME:-wes_backend_dev}_frontend_node_modules"
    )
    assert overlay["volumes"]["frontend_pnpm_store"]["name"] == (
        "${COMPOSE_PROJECT_NAME:-wes_backend_dev}_frontend_pnpm_store"
    )


def test_test_deploy_converges_authorization_before_starting_application_services() -> None:
    pipeline = (BACKEND_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")

    full_cutover = pipeline.split("run_full_cutover()", maxsplit=1)[1].split(
        "verify_readiness_and_topology()", maxsplit=1
    )[0]
    full_stage_markers = [
        "🗄️ 备份当前数据库",
        "🗄️ 执行数据库迁移",
        "🔐 收敛 existing DB 基础授权",
        "🔎 校验权限目录零漂移",
        "🐳 启动已固定版本的应用服务",
    ]
    positions = [full_cutover.index(marker) for marker in full_stage_markers]

    assert positions == sorted(positions)
    runtime_flow = pipeline[pipeline.index("🔒 进入维护态并停止旧应用容器") :]
    assert runtime_flow.index("run_full_cutover") < runtime_flow.index("🌐 恢复外部入口")
    assert "compose stop api celery celery-wms-fulfillment celery_beat flower frontend" in pipeline
    assert "for service in api celery celery-wms-fulfillment celery_beat flower" in pipeline
    assert "verify_service_digest frontend" in pipeline
    assert "--entrypoint /opt/venv/bin/alembic" in pipeline
    assert "--entrypoint /opt/venv/bin/python" in pipeline
    assert "api upgrade head" in pipeline
    assert "api scripts/data/sync_permissions.py --apply" in pipeline
    assert "scripts/data/sync_permissions.py --check" in pipeline
    assert "bootstrap_foundation.sh" not in pipeline


def test_test_deploy_injects_dedicated_admin_login_credentials() -> None:
    pipeline = (BACKEND_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")

    assert "credentialsId: 'wes-test-bootstrap-admin'" in pipeline
    assert "usernameVariable: 'BOOTSTRAP_ADMIN_USERNAME'" in pipeline
    assert "passwordVariable: 'BOOTSTRAP_ADMIN_PASSWORD'" in pipeline
    assert "-e BOOTSTRAP_ADMIN_USERNAME -e BOOTSTRAP_ADMIN_PASSWORD api" in pipeline
    assert "scripts/check_bootstrap_admin_login.py" in pipeline
    assert "BOOTSTRAP_ADMIN_PASSWORD=" not in pipeline


def test_test_deploy_repairs_postcommit_cache_failure_without_repeating_database_mutation() -> None:
    pipeline = (BACKEND_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")

    recovery = pipeline.split("DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED", maxsplit=1)[1]
    recovery = recovery.split("🐳 启动已固定版本的应用服务", maxsplit=1)[0]

    assert "scripts/data/sync_permissions.py --repair-cache" in recovery
    assert "scripts/data/sync_permissions.py --check" in recovery
    assert "bootstrap_foundation" not in recovery
    assert "--apply" not in recovery


def test_test_deploy_keeps_external_entrypoint_closed_until_every_gate_passes() -> None:
    pipeline = (BACKEND_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")

    assert "MAINTENANCE_MODE=true" in pipeline
    assert "trap keep_external_entrypoint_closed EXIT" in pipeline
    assert "compose stop nginx" in pipeline
    assert "curl -sS --connect-timeout 1 --max-time 2" in pipeline
    assert "compose up -d --no-deps --wait --wait-timeout 60 nginx" in pipeline
    assert "fail_cutover external-health" in pipeline
    assert "fail_cutover external-frontend" in pipeline
    assert pipeline.rindex("MAINTENANCE_MODE=false") > pipeline.index("🌐 恢复外部入口")


@pytest.mark.parametrize(
    ("service_state", "expected_message"),
    [
        ("running unhealthy", "服务健康检查失败: celery (running unhealthy)"),
        ("running none", "服务缺少健康检查: celery"),
    ],
)
def test_development_runner_rejects_an_unhealthy_or_unprobed_service(
    tmp_path: Path,
    service_state: str,
    expected_message: str,
) -> None:
    bin_dir = tmp_path / "bin"
    frontend = tmp_path / "frontend"
    bin_dir.mkdir()
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (frontend / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    required_services = (
        "db redis api celery celery-wms-fulfillment celery_beat mock_ecs mock_wms mock_wms_provider frontend nginx"
    )
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = inspect ]; then\n'
        f"  case \"$*\" in *container-celery) printf '{service_state}\\n' ;; "
        "*) printf 'running healthy\\n' ;; esac\n"
        "  exit 0\n"
        "fi\n"
        f"case \"$*\" in *'ps --status running --services'*) printf '%s\\n' {required_services} ;;\n"
        "  *'ps -q '*) service=; previous=; for value in \"$@\"; do "
        'if [ "$previous" = -q ]; then service=$value; break; fi; previous=$value; done; '
        "printf 'container-%s\\n' \"$service\" ;;\n"
        "  *) exit 0 ;; esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    for command in ("curl", "git"):
        executable = bin_dir / command
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)

    completed = subprocess.run(
        ["/bin/bash", str(BACKEND_ROOT / "scripts/dev-env.sh"), "check"],
        cwd=BACKEND_ROOT,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WES_FRONTEND_ROOT": str(frontend),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert expected_message in completed.stderr


def test_development_runner_rejects_provider_http_200_with_invalid_body(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    frontend = tmp_path / "frontend"
    trace_file = tmp_path / "provider-invalid-body.trace"
    bin_dir.mkdir()
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (frontend / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    required_services = (
        "db redis api celery celery-wms-fulfillment celery_beat mock_ecs mock_wms mock_wms_provider frontend nginx"
    )
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >>"$DEV_ENV_TRACE"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = inspect ]; then\n'
        '  case "$*" in *.Mounts*) printf "%s\\n" "$FRONTEND_MOUNT" ;; *) printf "running healthy\\n" ;; esac\n'
        "  exit 0\n"
        "fi\n"
        f"case \"$*\" in *'ps --status running --services'*) printf '%s\\n' {required_services} ;;\n"
        "  *'ps -q '*) service=; previous=; for value in \"$@\"; do "
        'if [ "$previous" = -q ]; then service=$value; break; fi; previous=$value; done; '
        "printf 'container-%s\\n' \"$service\" ;;\n"
        "  *'exec -T api python'*) exit 1 ;;\n"
        "  *) exit 0 ;; esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/sh\nprintf '{}\\n'\n", encoding="utf-8")
    curl.chmod(0o755)
    git = bin_dir / "git"
    git.write_text("#!/bin/sh\nprintf 'test\\n'\n", encoding="utf-8")
    git.chmod(0o755)

    completed = subprocess.run(
        ["/bin/bash", str(BACKEND_ROOT / "scripts/dev-env.sh"), "check"],
        cwd=BACKEND_ROOT,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DEV_ENV_TRACE": str(trace_file),
            "FRONTEND_MOUNT": str(frontend),
            "WES_FRONTEND_ROOT": str(frontend),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    trace = trace_file.read_text(encoding="utf-8").splitlines()
    assert any("exec -T api python -c" in command for command in trace)


def test_development_runner_can_stop_when_frontend_checkout_is_missing(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        '#!/bin/sh\nif [ "$1" = info ]; then exit 0; fi\nexit 0\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)

    completed = subprocess.run(
        ["/bin/bash", str(BACKEND_ROOT / "scripts/dev-env.sh"), "down"],
        cwd=BACKEND_ROOT,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WES_FRONTEND_ROOT": str(tmp_path / "missing-frontend"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_development_seed_contract_is_dev_only_and_contains_no_business_facts() -> None:
    from scripts.data import seed_initial_data
    from src.app.admin.services.authorization_bootstrap_service import BUILTIN_ROLE_SPECS

    with pytest.raises(RuntimeError, match="仅允许在 dev 环境运行"):
        seed_initial_data.require_development_environment({"ENV": "test"})

    with pytest.raises(RuntimeError, match="仅允许通过本机开发编排运行"):
        seed_initial_data.require_development_environment({"ENV": "dev"})
    with pytest.raises(RuntimeError, match="仅允许连接 Compose 开发数据库"):
        seed_initial_data.require_development_environment(
            {"ENV": "dev", "DEV_SEED_ALLOWED": "true", "POSTGRES_HOST": "localhost"}
        )

    seed_initial_data.require_development_environment({"ENV": "dev", "DEV_SEED_ALLOWED": "true", "POSTGRES_HOST": "db"})

    assert {spec.name for spec in BUILTIN_ROLE_SPECS} == {
        "系统管理员",
        "管理员",
        "运营人员",
        "财务人员",
        "普通用户",
    }
    assert {seed.username for seed in seed_initial_data.USER_SEEDS} == {
        "admin",
        "manager",
        "operator",
        "finance",
        "user1",
        "user2",
    }

    seed_source = (BACKEND_ROOT / "scripts/data/seed_initial_data.py").read_text(encoding="utf-8")
    for forbidden in (
        "WorkLine",
        "DeviceCommand",
        "TransportTask",
        "frontend_path",
        "--frontend-path",
        "inventory",
        "库存",
    ):
        assert forbidden not in seed_source
