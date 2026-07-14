from pathlib import Path

from src.celery_app.worker_healthcheck import has_celery_worker_process

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_docker_compose_dev_uses_beat_autoreload_script() -> None:
    compose_text = (BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'if [ "${ENV:-prod}" = "dev" ]; then' in compose_text
    assert "exec sh /app/src/celery_app/dev_beat_autoreload.sh;" in compose_text


def test_deploy_compose_dev_uses_beat_autoreload_script() -> None:
    compose_text = (BACKEND_ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8")

    assert 'if [ "${ENV:-prod}" = "dev" ]; then' in compose_text
    assert "exec sh /app/src/celery_app/dev_beat_autoreload.sh;" in compose_text


def test_dev_beat_autoreload_script_runs_celery_beat() -> None:
    script_text = (BACKEND_ROOT / "src/celery_app/dev_beat_autoreload.sh").read_text(encoding="utf-8")

    assert "Development-only auto-restart wrapper for Celery beat." in script_text
    assert 'WATCH_PATH="${CELERY_WATCH_PATH:-/app/src}"' in script_text
    assert 'CELERY_CMD="celery -A src.celery_app.app beat --loglevel=${CELERY_LOG_LEVEL:-INFO}"' in script_text
    assert 'echo "[celery-beat-dev-reload] code change detected, restarting beat"' in script_text


def test_dev_worker_autoreload_replaces_worker_process_on_restart() -> None:
    script_text = (BACKEND_ROOT / "src/celery_app/dev_worker_autoreload.sh").read_text(encoding="utf-8")

    assert 'setsid sh -c "exec $CELERY_CMD" &' in script_text
    assert '\n    sh -c "exec $CELERY_CMD" &' in script_text
    assert 'kill -TERM "$worker_pid"' in script_text
    assert 'kill -TERM "$stop_target"' not in script_text
    assert 'kill -KILL "$stop_target"' in script_text


def test_celery_shutdown_deadlines_are_strictly_layered() -> None:
    from src.celery_app.app import celery_app

    script_text = (BACKEND_ROOT / "src/celery_app/dev_worker_autoreload.sh").read_text(encoding="utf-8")
    compose_text = (BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    deploy_compose_text = (BACKEND_ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8")

    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.worker_soft_shutdown_timeout == 10
    assert celery_app.conf.worker_enable_soft_shutdown_on_idle is True
    assert 'SHUTDOWN_GRACE_SECONDS="${CELERY_RELOAD_SHUTDOWN_GRACE_SECONDS:-20}"' in script_text
    assert compose_text.count("stop_grace_period: 30s") >= 1
    assert deploy_compose_text.count("stop_grace_period: 30s") >= 1


def test_celery_worker_healthcheck_uses_process_probe() -> None:
    compose_text = (BACKEND_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert '["CMD", "python", "/app/src/celery_app/worker_healthcheck.py"]' in compose_text
    assert "celery -A src.celery_app.app inspect ping" not in compose_text


def test_deploy_overlay_inherits_local_worker_healthcheck() -> None:
    deploy_compose_text = (BACKEND_ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8")
    worker_section = deploy_compose_text.split("  celery_worker:", maxsplit=1)[1].split("  celery_beat:", maxsplit=1)[0]

    assert "healthcheck:" not in worker_section
    assert "inspect ping" not in worker_section


def test_worker_healthcheck_detects_celery_worker_process(tmp_path: Path) -> None:
    proc_dir = tmp_path / "123"
    proc_dir.mkdir()
    (proc_dir / "cmdline").write_bytes(b"python\0-m\0celery\0-A\0src.celery_app.app\0worker\0")

    assert has_celery_worker_process(tmp_path)


def test_worker_healthcheck_ignores_celery_inspect_ping(tmp_path: Path) -> None:
    proc_dir = tmp_path / "123"
    proc_dir.mkdir()
    (proc_dir / "cmdline").write_bytes(b"celery\0-A\0src.celery_app.app\0inspect ping\0")

    assert not has_celery_worker_process(tmp_path)


def test_worker_healthcheck_ignores_healthcheck_process_itself(tmp_path: Path) -> None:
    proc_dir = tmp_path / "123"
    proc_dir.mkdir()
    (proc_dir / "cmdline").write_bytes(b"python\0/app/src/celery_app/worker_healthcheck.py\0")

    assert not has_celery_worker_process(tmp_path)


def test_dev_beat_autoreload_replaces_beat_process_on_restart() -> None:
    script_text = (BACKEND_ROOT / "src/celery_app/dev_beat_autoreload.sh").read_text(encoding="utf-8")

    assert 'setsid sh -c "exec $CELERY_CMD" &' in script_text
    assert '\n    sh -c "exec $CELERY_CMD" &' in script_text
    assert 'kill -TERM "$stop_target"' in script_text
    assert 'kill -KILL "$stop_target"' in script_text
