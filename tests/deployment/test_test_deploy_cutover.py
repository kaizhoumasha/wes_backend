from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_REVISION = "a" * 40
FRONTEND_REVISION = "b" * 40
OPENAPI_SHA256 = "c" * 64
PERMISSIONS_SHA256 = "d" * 64


def _cutover_shell() -> str:
    pipeline = (REPO_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")
    start_marker = "# TEST_DEPLOY_CUTOVER_BEGIN"
    end_marker = "# TEST_DEPLOY_CUTOVER_END"
    start = pipeline.index(start_marker) + len(start_marker)
    end = pipeline.index(end_marker, start)
    return "set -e\nset -o pipefail\n" + textwrap.dedent(pipeline[start:end])


def _write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def _fake_docker_source() -> str:
    return r"""
        #!/bin/bash
        set -u

        trace() { printf '%s\n' "$1" >>"$TRACE_FILE"; }
        fail_at() { [ "${FAIL_STAGE:-}" = "$1" ]; }
        nginx_state() { cat "$NGINX_STATE_FILE"; }
        set_nginx_state() { printf '%s\n' "$1" >"$NGINX_STATE_FILE"; }

        if [ "$1" = "ps" ]; then
            count=0
            [ ! -f "$PS_COUNT_FILE" ] || count=$(cat "$PS_COUNT_FILE")
            count=$((count + 1))
            printf '%s\n' "$count" >"$PS_COUNT_FILE"
            if [ "$count" -eq 1 ]; then
                trace discovery
                fail_at discovery && exit 41
                printf '%s\n' old-api old-celery old-frontend old-nginx db redis
            else
                trace remaining-check
                fail_at remaining && printf '%s\n' old-api && exit 0
                printf '%s\n' db redis
            fi
            exit 0
        fi

        if [ "$1" = "inspect" ]; then
            container_id="${@: -1}"
            case "$container_id" in
                old-api) printf 'api\n' ;;
                old-celery) printf 'celery\n' ;;
                old-frontend) printf 'frontend\n' ;;
                old-nginx) printf 'nginx\n' ;;
                db) printf 'db\n' ;;
                redis) printf 'redis\n' ;;
                *) printf 'unknown\n' ;;
            esac
            exit 0
        fi

        if [ "$1" = "stop" ]; then
            trace "container-stop:${2}"
            exit 0
        fi

        if [ "$1" = "cp" ]; then
            trace menu-manifest-copy
            exit 0
        fi

        if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
            args="$*"
            case "$args" in
                *org.opencontainers.image.revision*"$BACKEND_IMAGE"*)
                    trace backend-revision
                    fail_at backend-revision && printf 'wrong\n' || printf '%s\n' "$EXPECTED_BACKEND_COMMIT_SHA"
                    ;;
                *org.opencontainers.image.revision*"$FRONTEND_IMAGE"*)
                    trace frontend-revision
                    fail_at frontend-revision && printf 'wrong\n' || printf '%s\n' "$EXPECTED_FRONTEND_COMMIT_SHA"
                    ;;
                *com.zontec.wes.backend-contract-revision*)
                    trace frontend-backend-provenance
                    fail_at backend-provenance && printf 'wrong\n' || printf '%s\n' "$EXPECTED_BACKEND_COMMIT_SHA"
                    ;;
                *com.zontec.wes.openapi-sha256*)
                    trace openapi-provenance
                    fail_at openapi-provenance && printf 'wrong\n' || printf '%s\n' "$EXPECTED_OPENAPI_SHA256"
                    ;;
                *com.zontec.wes.permissions-sha256*)
                    trace permission-provenance
                    fail_at permission-provenance && printf 'wrong\n' || printf '%s\n' "$EXPECTED_PERMISSIONS_SHA256"
                    ;;
                *) exit 42 ;;
            esac
            exit 0
        fi

        [ "$1" = "compose" ] || exit 43
        shift
        while [ "$1" = "--env-file" ] || [ "$1" = "-f" ]; do shift 2; done
        operation="$1"
        shift
        args="$*"

        case "$operation" in
            stop)
                trace nginx-stop
                if fail_at maintenance-stop && [ ! -f "$STOP_FAILED_FILE" ]; then
                    : >"$STOP_FAILED_FILE"
                    exit 44
                fi
                set_nginx_state stopped
                ;;
            up)
                case "$args" in
                    *"--no-deps nginx"*)
                        trace nginx-start
                        fail_at nginx-start && exit 45
                        set_nginx_state running
                        ;;
                    *"api celery celery-wms-fulfillment celery_beat flower frontend"*)
                        trace application-start
                        fail_at application-start && exit 46
                        :
                        ;;
                    *"db redis"*) trace infrastructure-start ;;
                    *) exit 47 ;;
                esac
                ;;
            run)
                case "$args" in
                    *"api upgrade head"*)
                        trace migration
                        fail_at migration && exit 48
                        :
                        ;;
                    *"api scripts/data/bootstrap_foundation.sh"*)
                        trace bootstrap
                        if fail_at bootstrap-marker; then
                            printf 'BOOTSTRAP_FAILED_WITHOUT_POSTCOMMIT_MARKER\n'
                            exit 49
                        fi
                        if fail_at repair; then
                            printf 'DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED\n'
                            exit 50
                        fi
                        ;;
                    *"sync_permissions.py --repair-cache"*)
                        trace repair
                        fail_at repair && exit 51
                        :
                        ;;
                    *"sync_permissions.py --check"*)
                        trace permission-check
                        fail_at permission-check && exit 52
                        :
                        ;;
                    *) exit 53 ;;
                esac
                ;;
            exec)
                case "$args" in
                    *"pg_isready"*) trace postgres-ready ;;
                    *"redis-cli"*) trace redis-ready ;;
                    *"createdb"*)
                        trace fresh-database
                        fail_at fresh-database && exit 54
                        :
                        ;;
                    *"pg_catalog.pg_tables"*)
                        trace fresh-proof
                        fail_at fresh-proof && printf '1\n' || printf '0\n'
                        ;;
                    *"api curl"*"/ready"*)
                        trace backend-ready
                        fail_at readiness && exit 55
                        :
                        ;;
                    *"frontend wget"*)
                        trace frontend-asset
                        fail_at frontend-asset && exit 56
                        :
                        ;;
                    *"sync_menus.py"*) trace menu-sync ;;
                    *"select count(*) from wes_sys.menus"*)
                        trace menu-count
                        printf '5\n'
                        ;;
                    *) exit 57 ;;
                esac
                ;;
            ps)
                if [ "$args" = "-q api" ]; then printf 'api-container\n'; else trace compose-ps; fi
                ;;
            *) exit 58 ;;
        esac
    """


def _fake_curl_source() -> str:
    return r"""
        #!/bin/bash
        set -u
        args="$*"
        state=$(cat "$NGINX_STATE_FILE")
        if [ "$state" = stopped ]; then
            printf 'listener-probe\n' >>"$TRACE_FILE"
            [ "${FAIL_STAGE:-}" = listener-open ] && exit 0
            exit 7
        fi
        case "$args" in
            *"/health"*)
                printf 'external-health\n' >>"$TRACE_FILE"
                [ "${FAIL_STAGE:-}" = external-health ] && exit 59
                :
                ;;
            *)
                printf 'external-frontend\n' >>"$TRACE_FILE"
                [ "${FAIL_STAGE:-}" = external-frontend ] && exit 60
                :
                ;;
        esac
    """


def _run_cutover(tmp_path: Path, fail_stage: str = "") -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace_path = tmp_path / "trace.log"
    nginx_state_path = tmp_path / "nginx.state"
    nginx_state_path.write_text("running\n", encoding="utf-8")
    (tmp_path / ".env.test").write_text(
        "COMPOSE_PROJECT_NAME=wes_backend_test\nNGINX_HTTP_PORT=8080\nPOSTGRES_DB=legacy\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "menu-manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    _write_executable(bin_dir / "docker", _fake_docker_source())
    _write_executable(bin_dir / "curl", _fake_curl_source())
    _write_executable(bin_dir / "sleep", "#!/bin/sh\nexit 0\n")

    env = os.environ | {
        "BACKEND_IMAGE": "backend@sha256:" + "1" * 64,
        "BUILD_NUMBER": "42",
        "CI_COMMIT_SHA": "e" * 40,
        "DEPLOY_COMPOSE_FILE": "docker-compose.test-deploy.yml",
        "DEPLOY_ENV_FILE": ".env.test",
        "EXPECTED_BACKEND_COMMIT_SHA": BACKEND_REVISION,
        "EXPECTED_FRONTEND_COMMIT_SHA": FRONTEND_REVISION,
        "EXPECTED_OPENAPI_SHA256": OPENAPI_SHA256,
        "EXPECTED_PERMISSIONS_SHA256": PERMISSIONS_SHA256,
        "FAIL_STAGE": fail_stage,
        "FRONTEND_IMAGE": "frontend@sha256:" + "2" * 64,
        "HEALTH_CHECK_URL": "http://127.0.0.1:8001/ready",
        "MANIFEST_FILE": str(manifest_path),
        "NGINX_PORT": "8080",
        "NGINX_STATE_FILE": str(nginx_state_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PS_COUNT_FILE": str(tmp_path / "ps.count"),
        "STOP_FAILED_FILE": str(tmp_path / "stop.failed"),
        "TRACE_FILE": str(trace_path),
    }
    completed = subprocess.run(
        ["/bin/bash", "-c", _cutover_shell()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    trace = trace_path.read_text(encoding="utf-8").splitlines() if trace_path.exists() else []
    return completed, trace, nginx_state_path.read_text(encoding="utf-8").strip()


def _assert_subsequence(trace: list[str], expected: list[str]) -> None:
    position = 0
    for entry in trace:
        if position < len(expected) and entry == expected[position]:
            position += 1
    assert position == len(expected), trace


def test_test_deploy_compose_declares_the_complete_pre_exposure_application_set() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.test-deploy.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"api", "celery", "celery-wms-fulfillment", "celery_beat", "flower", "frontend"} <= services.keys()
    for service_name in ("api", "celery", "celery-wms-fulfillment", "celery_beat", "flower"):
        parent = services[service_name].get("extends", {}).get("service")
        service = services[parent] if parent else services[service_name]
        assert service["image"] == "${BACKEND_IMAGE:-wes-backend:local}"


def test_test_deploy_requires_and_compares_the_complete_paired_image_provenance() -> None:
    pipeline = (REPO_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")

    for parameter_name in (
        "BACKEND_IMAGE_TAG",
        "FRONTEND_IMAGE_TAG",
        "BACKEND_COMMIT_SHA",
        "FRONTEND_COMMIT_SHA",
        "OPENAPI_SHA256",
        "PERMISSIONS_SHA256",
    ):
        declaration = next(line for line in pipeline.splitlines() if f"name: '{parameter_name}'" in line)
        assert "defaultValue" not in declaration
    for label_name in (
        "org.opencontainers.image.revision",
        "com.zontec.wes.backend-contract-revision",
        "com.zontec.wes.openapi-sha256",
        "com.zontec.wes.permissions-sha256",
    ):
        assert label_name in pipeline
    assert '${BACKEND_REVISION}" != "${EXPECTED_BACKEND_COMMIT_SHA}' in pipeline
    assert '${FRONTEND_REVISION}" != "${EXPECTED_FRONTEND_COMMIT_SHA}' in pipeline
    assert '${FRONTEND_BACKEND_REVISION}" != "${EXPECTED_BACKEND_COMMIT_SHA}' in pipeline
    assert '${FRONTEND_OPENAPI_SHA256}" != "${EXPECTED_OPENAPI_SHA256}' in pipeline
    assert '${FRONTEND_PERMISSIONS_SHA256}" != "${EXPECTED_PERMISSIONS_SHA256}' in pipeline


@pytest.mark.parametrize(
    "relative_path",
    (
        "docs/devops/prod-release-deploy.md",
        "docs/devops/rocky-linux-server-initialization.md",
    ),
)
def test_current_release_commands_use_the_same_fail_closed_cutover_contract(relative_path: str) -> None:
    document = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    assert "for container_id in $(" not in document
    assert 'curl -fsS "http://127.0.0.1:${NGINX_HTTP_PORT}/"' not in document
    assert "compose up -d --force-recreate --wait nginx" not in document
    for contract_marker in (
        "PROJECT_CONTAINER_IDS=$(",
        "REMAINING_CONTAINER_IDS=$(",
        "curl -sS --connect-timeout 1 --max-time 2",
        "com.zontec.wes.backend-contract-revision",
        "com.zontec.wes.openapi-sha256",
        "com.zontec.wes.permissions-sha256",
        "compose up -d --no-deps nginx",
        "fail_cutover external-health",
        "fail_cutover external-frontend",
    ):
        assert contract_marker in document


@pytest.mark.parametrize(
    "fail_stage",
    [
        "maintenance-stop",
        "listener-open",
        "discovery",
        "remaining",
        "fresh-database",
        "fresh-proof",
        "migration",
        "bootstrap-marker",
        "repair",
        "permission-check",
        "application-start",
        "readiness",
        "frontend-asset",
        "backend-revision",
        "frontend-revision",
        "backend-provenance",
        "openapi-provenance",
        "permission-provenance",
        "nginx-start",
        "external-health",
        "external-frontend",
    ],
)
def test_test_deploy_failure_keeps_nginx_closed_and_never_repeats_database_mutation(
    tmp_path: Path, fail_stage: str
) -> None:
    completed, trace, nginx_state = _run_cutover(tmp_path, fail_stage)

    assert completed.returncode != 0, (completed.stdout, completed.stderr, trace)
    assert nginx_state == "stopped"
    assert trace[-1] == "nginx-stop"
    for mutating_stage in ("fresh-database", "migration", "bootstrap", "repair", "menu-sync"):
        assert trace.count(mutating_stage) <= 1
    if fail_stage == "repair":
        assert trace.count("bootstrap") == 1
        assert trace.count("repair") == 1
    if fail_stage in {"discovery", "remaining", "migration", "bootstrap-marker"}:
        assert "nginx-start" not in trace


def test_test_deploy_success_proves_complete_order_with_nginx_started_last(tmp_path: Path) -> None:
    completed, trace, nginx_state = _run_cutover(tmp_path)

    assert completed.returncode == 0, (completed.stdout, completed.stderr, trace)
    assert nginx_state == "running"
    assert trace.count("bootstrap") == 1
    assert "repair" not in trace
    _assert_subsequence(
        trace,
        [
            "nginx-stop",
            "listener-probe",
            "discovery",
            "remaining-check",
            "fresh-database",
            "fresh-proof",
            "migration",
            "bootstrap",
            "permission-check",
            "application-start",
            "backend-ready",
            "frontend-asset",
            "backend-revision",
            "frontend-revision",
            "frontend-backend-provenance",
            "openapi-provenance",
            "permission-provenance",
            "menu-sync",
            "menu-count",
            "nginx-start",
            "external-health",
            "external-frontend",
        ],
    )
    assert trace.index("nginx-start") > trace.index("permission-provenance")
