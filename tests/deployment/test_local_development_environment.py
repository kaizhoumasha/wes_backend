import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _compose(name: str) -> dict:
    return yaml.safe_load((BACKEND_ROOT / name).read_text(encoding="utf-8"))


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
    expected_mount = "./deployment/dev/wms-provider.yaml:/run/wes/wms-provider.yaml:ro"
    for service_name in ("api", "celery", "celery-wms-fulfillment", "celery_beat"):
        service = overlay["services"][service_name]
        assert service["environment"]["WMS_PROVIDER_PROFILE_FILE"] == "/run/wes/wms-provider.yaml"
        assert expected_mount in service["volumes"]

    profile = load_wms_provider_profile(BACKEND_ROOT / "deployment/dev/wms-provider.yaml")
    assert profile.server_url == "http://mock-wms-provider:8012"
    compile_wms_provider_profile(profile)
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
    assert "--frontend-path /workspace/frontend" in runner
    assert "--check" in runner
    assert 'case "$COMMAND" in' in runner
    assert "down --remove-orphans" in runner
    assert "down -v" not in runner
    assert "down --volumes" not in runner


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
        'if [ "$1" = inspect ]; then printf "running healthy\\n"; exit 0; fi\n'
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
            "WES_FRONTEND_ROOT": str(frontend),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1


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

    with pytest.raises(RuntimeError, match="仅允许在 dev 环境运行"):
        seed_initial_data.require_development_environment({"ENV": "test"})

    with pytest.raises(RuntimeError, match="仅允许通过本机开发编排运行"):
        seed_initial_data.require_development_environment({"ENV": "dev"})
    with pytest.raises(RuntimeError, match="仅允许连接 Compose 开发数据库"):
        seed_initial_data.require_development_environment(
            {"ENV": "dev", "DEV_SEED_ALLOWED": "true", "POSTGRES_HOST": "localhost"}
        )

    seed_initial_data.require_development_environment({"ENV": "dev", "DEV_SEED_ALLOWED": "true", "POSTGRES_HOST": "db"})

    assert {seed.name for seed in seed_initial_data.ROLE_SEEDS} == {
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
    for forbidden in ("WorkLine", "DeviceCommand", "TransportTask", "inventory", "库存"):
        assert forbidden not in seed_source
