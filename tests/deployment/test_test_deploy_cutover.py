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


def _existing_database_authorization_shell() -> str:
    runbook = (REPO_ROOT / "docs/devops/prod-release-deploy.md").read_text(encoding="utf-8")
    snippets: list[str] = []
    for marker_name in ("EXISTING_DATABASE_AUTHORIZATION", "AUTHORIZATION_FRESH_CHECK"):
        start_marker = f"# {marker_name}_BEGIN"
        end_marker = f"# {marker_name}_END"
        start = runbook.index(start_marker) + len(start_marker)
        end = runbook.index(end_marker, start)
        snippets.append(textwrap.dedent(runbook[start:end]))
    return "set -e\nset -o pipefail\n" + "\n".join(snippets)


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
                fail_at application-discovery && exit 41
                printf '%s\n' \
                    old-api \
                    old-celery \
                    old-celery-wms-fulfillment \
                    old-celery-beat \
                    old-flower \
                    old-frontend \
                    old-nginx \
                    db \
                    redis
            else
                trace remaining-check
                fail_at remaining-discovery && exit 42
                fail_at remaining-unknown-service && printf '%s\n' old-unknown && exit 0
                printf '%s\n' db redis
            fi
            exit 0
        fi

        if [ "$1" = "inspect" ]; then
            container_id="${@: -1}"
            trace "application-inspect:${container_id}"
            fail_at application-inspect && [ "$container_id" = old-api ] && exit 43
            case "$container_id" in
                old-api) printf 'api\n' ;;
                old-celery) printf 'celery\n' ;;
                old-celery-wms-fulfillment) printf 'celery-wms-fulfillment\n' ;;
                old-celery-beat) printf 'celery_beat\n' ;;
                old-flower) printf 'flower\n' ;;
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
            fail_at "stop-${2#old-}" && exit 44
            exit 0
        fi

        if [ "$1" = "rm" ]; then
            trace manifest-extract-cleanup
            exit 0
        fi

        if [ "$1" = "create" ]; then
            trace manifest-extract-create
            fail_at manifest-extract-create && exit 64
            printf 'frontend-extract-id\n'
            exit 0
        fi

        if [ "$1" = "cp" ]; then
            if [ "$2" = "frontend-manifest-extract:/opt/wes/menu-manifest.json" ]; then
                trace manifest-extract-copy
                fail_at manifest-extract-copy && exit 65
                fail_at manifest-extract-empty && : >"$3" || printf '{}\n' >"$3"
                exit 0
            fi
            trace menu-manifest-copy
            fail_at manifest-copy && exit 45
            exit 0
        fi

        if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
            args="$*"
            case "$args" in
                *org.opencontainers.image.revision*"$BACKEND_IMAGE"*)
                    trace backend-revision-inspect
                    fail_at backend-revision-inspect && exit 46
                    fail_at backend-revision-mismatch \
                        && printf 'wrong\n' \
                        || printf '%s\n' "$EXPECTED_BACKEND_COMMIT_SHA"
                    ;;
                *org.opencontainers.image.revision*"$FRONTEND_IMAGE"*)
                    trace frontend-revision-inspect
                    fail_at frontend-revision-inspect && exit 47
                    fail_at frontend-revision-mismatch \
                        && printf 'wrong\n' \
                        || printf '%s\n' "$EXPECTED_FRONTEND_COMMIT_SHA"
                    ;;
                *com.zontec.wes.backend-contract-revision*)
                    trace frontend-backend-provenance-inspect
                    fail_at backend-provenance-inspect && exit 48
                    fail_at backend-provenance-mismatch \
                        && printf 'wrong\n' \
                        || printf '%s\n' "$EXPECTED_BACKEND_COMMIT_SHA"
                    ;;
                *com.zontec.wes.openapi-sha256*)
                    trace openapi-provenance-inspect
                    fail_at openapi-provenance-inspect && exit 49
                    fail_at openapi-provenance-mismatch \
                        && printf 'wrong\n' \
                        || printf '%s\n' "$EXPECTED_OPENAPI_SHA256"
                    ;;
                *com.zontec.wes.permissions-sha256*)
                    trace permission-provenance-inspect
                    fail_at permission-provenance-inspect && exit 50
                    fail_at permission-provenance-mismatch \
                        && printf 'wrong\n' \
                        || printf '%s\n' "$EXPECTED_PERMISSIONS_SHA256"
                    ;;
                *) exit 51 ;;
            esac
            exit 0
        fi

        [ "$1" = "compose" ] || exit 52
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
                    *"db redis"*)
                        trace infrastructure-start
                        fail_at infrastructure-start && exit 47
                        :
                        ;;
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
                        if fail_at bootstrap-non-marker; then
                            printf 'BOOTSTRAP_FAILED_WITHOUT_POSTCOMMIT_MARKER\n'
                            exit 49
                        fi
                        if fail_at repair-failure || fail_at postcommit-recovery; then
                            printf 'DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED\n'
                            printf 'CACHE_INVALIDATION_FAILURE_DETAIL: RuntimeError: injected redis failure\n'
                            exit 50
                        fi
                        ;;
                    *"sync_permissions.py --repair-cache"*)
                        trace repair
                        fail_at repair-failure && exit 51
                        :
                        ;;
                    *"sync_permissions.py --check"*)
                        trace permission-check
                        fail_at authorization-check && exit 52
                        :
                        ;;
                    *) exit 53 ;;
                esac
                ;;
            exec)
                case "$args" in
                    *"pg_isready"*)
                        trace postgres-ready
                        fail_at postgres-readiness && exit 54
                        :
                        ;;
                    *"redis-cli"*)
                        trace redis-ready
                        fail_at redis-readiness && exit 55
                        :
                        ;;
                    *"createdb"*)
                        trace fresh-database
                        fail_at fresh-database && exit 56
                        :
                        ;;
                    *"pg_catalog.pg_tables"*)
                        trace fresh-proof
                        fail_at fresh-proof && printf '1\n' || printf '0\n'
                        ;;
                    *"api curl"*"/ready"*)
                        trace backend-ready
                        fail_at backend-readiness && exit 57
                        :
                        ;;
                    *"frontend wget"*)
                        trace frontend-asset
                        fail_at frontend-asset && exit 58
                        :
                        ;;
                    *"sync_menus.py"*)
                        trace menu-sync
                        fail_at menu-sync && exit 59
                        :
                        ;;
                    *"select count(*) from wes_sys.menus"*)
                        trace menu-count
                        fail_at menu-count-query && exit 60
                        fail_at menu-count-check && printf '0\n' || printf '5\n'
                        ;;
                    *) exit 61 ;;
                esac
                ;;
            ps)
                if [ "$args" = "-q api" ]; then
                    trace api-container-lookup
                    fail_at api-container-lookup && exit 62
                    fail_at api-container-missing || printf 'api-container\n'
                else
                    trace compose-ps
                    fail_at final-compose-status && exit 63
                    :
                fi
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
        "BUILD_NUMBER": "bad-build" if fail_stage == "fresh-database-name" else "42",
        "CI_COMMIT_SHA": "e" * 40,
        "DEPLOY_SOURCE_COMMIT_SHA": BACKEND_REVISION,
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
        "WORKSPACE": str(tmp_path),
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


def _run_existing_database_authorization(
    tmp_path: Path, fail_stage: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    trace_file = tmp_path / "existing-db-trace.log"
    script = tmp_path / "existing-db-authorization.sh"
    _write_executable(
        script,
        f"""
        #!/bin/bash
        TRACE_FILE={trace_file!s}
        FAIL_STAGE={fail_stage!s}
        trace() {{ printf '%s\\n' "$1" >>"$TRACE_FILE"; }}
        fail_cutover() {{ trace "fail:$1"; exit 1; }}
        compose() {{
          args="$*"
          case "$args" in
            *"sync_permissions.py --apply"*)
              trace apply
              case "$FAIL_STAGE" in
                apply-normal-failure)
                  printf 'PERMISSION_SYNC_FAILED: RuntimeError: injected database failure\\n'
                  return 1
                  ;;
                detailed-marker-only)
                  printf 'DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED: RuntimeError: injected redis failure\\n'
                  return 3
                  ;;
                repair-failure|postcommit-recovery)
                  printf 'DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED\\n'
                  printf 'CACHE_INVALIDATION_FAILURE_DETAIL: RuntimeError: injected redis failure\\n'
                  return 3
                  ;;
                *)
                  printf 'apply completed\\n'
                  return 0
                  ;;
              esac
              ;;
            *"sync_permissions.py --repair-cache"*)
              trace repair
              [ "$FAIL_STAGE" != repair-failure ] || return 4
              return 0
              ;;
            *"sync_permissions.py --check"*)
              trace permission-check
              return 0
              ;;
            *) return 9 ;;
          esac
        }}
        {_existing_database_authorization_shell()}
        """,
    )
    completed = subprocess.run([str(script)], text=True, capture_output=True, check=False)
    trace = trace_file.read_text(encoding="utf-8").splitlines() if trace_file.exists() else []
    return completed, trace


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
        environment = service.get("environment", {}) | services[service_name].get("environment", {})
        assert environment["POSTGRES_DB"] == "${POSTGRES_DB}"

    pipeline = (REPO_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")
    assert 'POSTGRES_DB="wes_test_${BUILD_NUMBER}_${SHORT_COMMIT}"' in pipeline
    assert "export POSTGRES_DB" in pipeline


def test_test_deploy_requires_and_compares_the_complete_paired_image_provenance() -> None:
    pipeline = (REPO_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")

    for parameter_name in (
        "BACKEND_IMAGE_TAG",
        "FRONTEND_IMAGE_TAG",
        "BACKEND_COMMIT_SHA",
        "DEPLOY_SOURCE_COMMIT_SHA",
        "FRONTEND_COMMIT_SHA",
        "OPENAPI_SHA256",
        "PERMISSIONS_SHA256",
    ):
        declaration = next(line for line in pipeline.splitlines() if f"name: '{parameter_name}'" in line)
        assert "defaultValue" not in declaration
    assert "name: 'SOURCE_COMMIT_SHA'" not in pipeline
    assert "params.SOURCE_COMMIT_SHA" not in pipeline
    assert "deploySourceCommitSha ==~ /[0-9a-f]{40}/" in pipeline
    assert "deploySourceCommitSha != backendCommitSha" in pipeline
    assert 'git reset --hard "${DEPLOY_SOURCE_COMMIT_SHA}"' in pipeline
    assert "DEPLOY_ACTUAL_COMMIT=$(git rev-parse HEAD)" in pipeline
    assert '"${DEPLOY_ACTUAL_COMMIT}" != "${DEPLOY_SOURCE_COMMIT_SHA}"' in pipeline
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

    digest_resolution = pipeline.index("export BACKEND_IMAGE FRONTEND_IMAGE")
    backend_revision_gate = pipeline.index("if ! BACKEND_REVISION=$(docker image inspect")
    maintenance_stop = pipeline.index('echo "🔒 进入维护态并停止旧应用容器"')
    manifest_extraction = pipeline.index('echo "📄 在维护态从前端镜像提取菜单清单..."')
    assert digest_resolution < backend_revision_gate < maintenance_stop < manifest_extraction


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
    assert document.index("BACKEND_REVISION=$(") < document.index("CUTOVER_STAGE=maintenance-stop")


@pytest.mark.parametrize(
    ("fail_stage", "forbidden_later_stage"),
    [
        ("backend-revision-inspect", "fresh-database"),
        ("backend-revision-mismatch", "fresh-database"),
        ("frontend-revision-inspect", "fresh-database"),
        ("frontend-revision-mismatch", "fresh-database"),
        ("backend-provenance-inspect", "fresh-database"),
        ("backend-provenance-mismatch", "fresh-database"),
        ("openapi-provenance-inspect", "fresh-database"),
        ("openapi-provenance-mismatch", "fresh-database"),
        ("permission-provenance-inspect", "fresh-database"),
        ("permission-provenance-mismatch", "fresh-database"),
        ("fresh-database-name", "fresh-database"),
        ("maintenance-stop", "discovery"),
        ("listener-open", "discovery"),
        ("manifest-extract-create", "discovery"),
        ("manifest-extract-copy", "discovery"),
        ("manifest-extract-empty", "discovery"),
        ("application-discovery", "remaining-check"),
        ("application-inspect", "remaining-check"),
        ("stop-api", "remaining-check"),
        ("stop-celery", "remaining-check"),
        ("stop-celery-wms-fulfillment", "remaining-check"),
        ("stop-celery-beat", "remaining-check"),
        ("stop-flower", "remaining-check"),
        ("stop-frontend", "remaining-check"),
        ("stop-nginx", "remaining-check"),
        ("remaining-discovery", "infrastructure-start"),
        ("remaining-unknown-service", "infrastructure-start"),
        ("infrastructure-start", "postgres-ready"),
        ("postgres-readiness", "fresh-database"),
        ("redis-readiness", "fresh-database"),
        ("fresh-database", "fresh-proof"),
        ("fresh-proof", "migration"),
        ("migration", "bootstrap"),
        ("bootstrap-non-marker", "repair"),
        ("repair-failure", "permission-check"),
        ("authorization-check", "application-start"),
        ("application-start", "backend-ready"),
        ("backend-readiness", "frontend-asset"),
        ("frontend-asset", "api-container-lookup"),
        ("api-container-lookup", "menu-manifest-copy"),
        ("api-container-missing", "menu-manifest-copy"),
        ("manifest-copy", "menu-sync"),
        ("menu-sync", "menu-count"),
        ("menu-count-query", "nginx-start"),
        ("menu-count-check", "nginx-start"),
        ("nginx-start", "external-health"),
        ("external-health", "external-frontend"),
        ("external-frontend", "compose-ps"),
        ("final-compose-status", None),
    ],
)
def test_test_deploy_failure_keeps_nginx_closed_and_never_repeats_database_mutation(
    tmp_path: Path, fail_stage: str, forbidden_later_stage: str | None
) -> None:
    completed, trace, nginx_state = _run_cutover(tmp_path, fail_stage)

    assert completed.returncode != 0, (completed.stdout, completed.stderr, trace)
    assert nginx_state == "stopped"
    assert trace[-1] == "nginx-stop"
    if forbidden_later_stage is not None:
        assert forbidden_later_stage not in trace
    for mutating_stage in ("fresh-database", "migration", "bootstrap", "repair", "menu-sync"):
        assert trace.count(mutating_stage) <= 1
    if fail_stage == "repair-failure":
        assert trace.count("bootstrap") == 1
        assert trace.count("repair") == 1
    if fail_stage in {"manifest-extract-create", "manifest-extract-copy", "manifest-extract-empty"}:
        for later_stage in (
            "discovery",
            "container-stop:old-api",
            "infrastructure-start",
            "fresh-database",
            "migration",
            "application-start",
            "nginx-start",
        ):
            assert later_stage not in trace
        assert trace.count("manifest-extract-cleanup") == 2


def test_test_deploy_postcommit_recovery_repairs_once_checks_again_and_continues(tmp_path: Path) -> None:
    completed, trace, nginx_state = _run_cutover(tmp_path, "postcommit-recovery")

    assert completed.returncode == 0, (completed.stdout, completed.stderr, trace)
    assert nginx_state == "running"
    assert trace.count("bootstrap") == 1
    assert trace.count("repair") == 1
    assert trace.count("permission-check") == 1
    assert completed.stdout.splitlines().count("DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED") == 1
    assert "CACHE_INVALIDATION_FAILURE_DETAIL: RuntimeError: injected redis failure" in completed.stdout.splitlines()
    _assert_subsequence(trace, ["bootstrap", "repair", "permission-check", "application-start", "nginx-start"])


@pytest.mark.parametrize("fail_stage", ["apply-normal-failure", "detailed-marker-only"])
def test_existing_database_apply_rejects_failures_without_the_exact_bare_marker(
    tmp_path: Path, fail_stage: str
) -> None:
    completed, trace = _run_existing_database_authorization(tmp_path, fail_stage)

    assert completed.returncode != 0, (completed.stdout, completed.stderr, trace)
    assert trace == ["apply", "fail:authorization-apply"]


def test_existing_database_postcommit_recovery_repairs_once_then_runs_fresh_check(tmp_path: Path) -> None:
    completed, trace = _run_existing_database_authorization(tmp_path, "postcommit-recovery")

    assert completed.returncode == 0, (completed.stdout, completed.stderr, trace)
    assert trace == ["apply", "repair", "permission-check"]
    assert trace.count("apply") == 1
    assert trace.count("repair") == 1
    assert completed.stdout.splitlines().count("DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED") == 1
    assert "CACHE_INVALIDATION_FAILURE_DETAIL: RuntimeError: injected redis failure" in completed.stdout.splitlines()
    assert "authorization apply exit status: 3" in completed.stderr


def test_existing_database_repair_failure_stops_before_fresh_check(tmp_path: Path) -> None:
    completed, trace = _run_existing_database_authorization(tmp_path, "repair-failure")

    assert completed.returncode != 0, (completed.stdout, completed.stderr, trace)
    assert trace == ["apply", "repair", "fail:authorization-cache-repair"]


def test_test_deploy_success_proves_complete_order_with_nginx_started_last(tmp_path: Path) -> None:
    completed, trace, nginx_state = _run_cutover(tmp_path)

    assert completed.returncode == 0, (completed.stdout, completed.stderr, trace)
    assert nginx_state == "running"
    assert trace.count("bootstrap") == 1
    assert "repair" not in trace
    _assert_subsequence(
        trace,
        [
            "backend-revision-inspect",
            "frontend-revision-inspect",
            "frontend-backend-provenance-inspect",
            "openapi-provenance-inspect",
            "permission-provenance-inspect",
            "nginx-stop",
            "listener-probe",
            "manifest-extract-create",
            "manifest-extract-copy",
            "discovery",
            "remaining-check",
            "infrastructure-start",
            "postgres-ready",
            "redis-ready",
            "fresh-database",
            "fresh-proof",
            "migration",
            "bootstrap",
            "permission-check",
            "application-start",
            "backend-ready",
            "frontend-asset",
            "api-container-lookup",
            "menu-manifest-copy",
            "menu-sync",
            "menu-count",
            "nginx-start",
            "external-health",
            "external-frontend",
        ],
    )
    assert trace.index("backend-revision-inspect") < trace.index("nginx-stop")
    assert trace.index("nginx-start") > trace.index("menu-count")
