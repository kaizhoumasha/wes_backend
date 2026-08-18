from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_JENKINSFILE = REPO_ROOT / "Jenkinsfile.backend-ci"


def _stage_body(jenkins_text: str, stage_name: str, next_stage_name: str) -> str:
    return jenkins_text.split(f"stage('{stage_name}')", maxsplit=1)[1].split(f"stage('{next_stage_name}')", maxsplit=1)[
        0
    ]


def test_merge_request_without_target_branch_fails_closed() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    checkout_body = _stage_body(jenkins_text, "Checkout Source", "Build CI Image")

    assert "if (isMergeRequest && !targetBranch)" in checkout_body
    assert "error('Merge request build requires gitlabTargetBranch')" in checkout_body


def test_quality_gate_uses_the_fixed_fast_reference_resources() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    quality_body = _stage_body(jenkins_text, "Quality Gate", "Compose Contracts")

    assert "--cpus=2" in quality_body
    assert "--memory=4g" in quality_body


def test_heavy_required_does_not_expose_the_host_docker_daemon_to_pytest() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "/var/run/docker.sock" not in heavy_body
    assert "FROM docker:" not in dockerfile_text
    assert "COPY --from=docker_cli" not in dockerfile_text


def test_backend_images_embed_the_checked_out_revision_and_source_tree() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    checkout_body = _stage_body(jenkins_text, "Checkout Source", "Build CI Image")
    ci_build_body = _stage_body(jenkins_text, "Build CI Image", "Quality Gate")
    runtime_build_body = _stage_body(jenkins_text, "Build Runtime Image", "Push Runtime Image")

    assert "ARG WES_VCS_REVISION" in dockerfile_text
    assert "ARG WES_SOURCE_TREE" in dockerfile_text
    assert 'org.opencontainers.image.revision="${WES_VCS_REVISION}"' in dockerfile_text
    assert 'com.zontec.wes.source-manifest="${WES_SOURCE_TREE}"' in dockerfile_text
    assert "env.CI_SOURCE_TREE = sourceTree" in checkout_body
    assert "git rev-parse HEAD^{tree}" in checkout_body
    for build_body in (ci_build_body, runtime_build_body):
        assert '--build-arg "WES_VCS_REVISION=${CI_COMMIT_SHA}"' in build_body
        assert '--build-arg "WES_SOURCE_TREE=${CI_SOURCE_TREE}"' in build_body


def test_testing_image_context_keeps_ci_contract_assets_and_ruff_layout_inputs() -> None:
    required_paths = (
        "Dockerfile",
        "docker-compose.ci-heavy.yml",
        "docker-compose.test-deploy.yml",
        "docker-compose.wms-acceptance.yml",
        "redis/redis.conf",
    )
    assert all((REPO_ROOT / path).is_file() for path in required_paths)

    dockerignore = REPO_ROOT / ".dockerignore"
    if dockerignore.exists():
        dockerignore_lines = dockerignore.read_text(encoding="utf-8").splitlines()
        assert {
            "**/__pycache__/",
            "**/*.pyc",
            "!Dockerfile",
            "!docker-compose.ci-heavy.yml",
            "!docker-compose.test-deploy.yml",
            "!docker-compose.wms-acceptance.yml",
            "!redis/",
            "!redis/**",
        } <= set(dockerignore_lines)


def test_host_compose_contracts_render_production_and_test_deploy_stacks() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    compose_body = _stage_body(jenkins_text, "Compose Contracts", "RuntimeInbox PostgreSQL Acceptance")

    assert 'BACKEND_IMAGE="${RUNTIME_IMAGE_LOCAL}"' in compose_body
    assert 'WMS_PROVIDER_PROFILE_HOST_FILE="$WORKSPACE/.env.test"' in compose_body
    assert "--env-file .env.prod" in compose_body
    assert "-f docker-compose.yml" in compose_body
    assert "-f docker-compose.deploy.yml" in compose_body
    assert "--profile prod" in compose_body
    assert "--env-file .env.test" in compose_body
    assert "-f docker-compose.test-deploy.yml" in compose_body
    assert compose_body.count("config --no-env-resolution --quiet") == 2


def test_merge_request_mock_image_contracts_run_as_fixed_host_commands() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    mock_body = _stage_body(jenkins_text, "Mock Image Contracts", "HEAVY Required")

    assert "env.CI_IS_MERGE_REQUEST == 'true'" in mock_body
    assert "docker build -f tests/mock/Dockerfile -t ${MOCK_ECS_IMAGE} -t ${MOCK_WMS_IMAGE} ." in mock_body
    assert "docker run --rm ${MOCK_ECS_IMAGE} python -c 'import ecs_mock_server'" in mock_body
    assert "docker run --rm ${MOCK_WMS_IMAGE} python -c 'import wms_mock_server'" in mock_body
    assert "asyncio.run(module.root())" in mock_body
    assert "TestClient" not in mock_body
    assert "assert status['transport_path'] == '/api/v1/wes/transport-requests'" in mock_body
    assert "assert status['authentication'] == 'NONE'" in mock_body
    assert "northbound_contract" not in mock_body
    assert "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS" not in mock_body
    assert "docker image rm -f ${MOCK_ECS_IMAGE} ${MOCK_WMS_IMAGE}" in mock_body
    assert "wes-mock:ecs" not in mock_body
    assert "wes-mock:wms" not in mock_body


def test_heavy_required_uses_a_build_scoped_compose_project() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert "-f docker-compose.ci-heavy.yml" in heavy_body
    assert '--project-name "${HEAVY_COMPOSE_PROJECT}"' in heavy_body
    assert '--network "${HEAVY_COMPOSE_PROJECT}_default"' in heavy_body
    assert "down --volumes --remove-orphans" in heavy_body
    assert "--profile test" not in heavy_body
    assert "wesp9-test-network" not in heavy_body


def test_heavy_required_uses_non_zero_build_scoped_redis_database() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert 'INTEGRATION_REDIS_URL="redis://:${REDIS_PASSWORD}@redis:6379/15"' in heavy_body


def test_heavy_compose_does_not_publish_or_reuse_deployment_resources() -> None:
    compose_text = (REPO_ROOT / "docker-compose.ci-heavy.yml").read_text(encoding="utf-8")

    assert "container_name:" not in compose_text
    assert "ports:" not in compose_text
    assert "./docker_data" not in compose_text
    assert "external:" not in compose_text


def test_ci_image_commands_never_resolve_dependencies_at_runtime() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    quality_gate_text = (REPO_ROOT / "scripts" / "git-quality-gate.sh").read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert 'if [[ "$CI_MODE" == "true" ]]; then' in quality_gate_text
    assert 'uv run --no-sync "$@"' in quality_gate_text
    assert "uv run --no-sync scripts/select_heavy_tests.py" in heavy_body
    assert "uv run --no-sync alembic upgrade head" in heavy_body
    assert "uv run --no-sync scripts/run_selected_heavy_tests.py" in heavy_body
    assert "sh -c '\n                            set -eu" in heavy_body


def test_heavy_required_rejects_selected_tests_that_are_skipped() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert "uv run --no-sync scripts/run_selected_heavy_tests.py" in heavy_body
    assert 'subprocess.call(["pytest", "-q", *tests])' not in heavy_body


def test_heavy_required_publishes_the_runner_junit_report() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert '-v "$WORKSPACE/reports:/reports"' in heavy_body
    assert "/reports/heavy-required.xml" in heavy_body
    assert "junit testResults: 'reports/heavy-required.xml', allowEmptyResults: true" in heavy_body
    assert "reports/heavy-tests.txt,reports/heavy-required.xml" in heavy_body


def test_non_publishing_builds_still_validate_the_production_target() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    build_body = _stage_body(jenkins_text, "Build Runtime Image", "Push Runtime Image")

    assert "when {" not in build_body
    assert '--target "${RUNTIME_BUILD_TARGET}"' in build_body
    assert '-t "${RUNTIME_IMAGE_LOCAL}"' in build_body
