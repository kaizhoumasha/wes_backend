from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _stage_body(jenkins_text: str, stage_name: str, next_stage_name: str) -> str:
    return jenkins_text.split(f"stage('{stage_name}')", maxsplit=1)[1].split(f"stage('{next_stage_name}')", maxsplit=1)[
        0
    ]


def test_heavy_required_does_not_expose_the_host_docker_daemon_to_pytest() -> None:
    jenkins_text = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "/var/run/docker.sock" not in heavy_body
    assert "FROM docker:" not in dockerfile_text
    assert "COPY --from=docker_cli" not in dockerfile_text


def test_merge_request_mock_image_contracts_run_as_fixed_host_commands() -> None:
    jenkins_text = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    mock_body = _stage_body(jenkins_text, "Mock Image Contracts", "HEAVY Required")

    assert "env.CI_IS_MERGE_REQUEST == 'true'" in mock_body
    assert "docker build -f tests/mock/Dockerfile -t ${MOCK_ECS_IMAGE} -t ${MOCK_WMS_IMAGE} ." in mock_body
    assert "docker run --rm ${MOCK_ECS_IMAGE} python -c 'import ecs_mock_server'" in mock_body
    assert "docker run --rm ${MOCK_WMS_IMAGE} python -c 'import wms_mock_server'" in mock_body
    assert "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS=2.5" in mock_body
    assert "asyncio.run(module.northbound_contract())" in mock_body
    assert "TestClient" not in mock_body
    assert "assert contract['status_visibility_sla_seconds'] == 2.5" in mock_body
    assert "docker image rm -f ${MOCK_ECS_IMAGE} ${MOCK_WMS_IMAGE}" in mock_body
    assert "wes-mock:ecs" not in mock_body
    assert "wes-mock:wms" not in mock_body


def test_heavy_required_uses_a_build_scoped_compose_project() -> None:
    jenkins_text = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert "-f docker-compose.ci-heavy.yml" in heavy_body
    assert '--project-name "${HEAVY_COMPOSE_PROJECT}"' in heavy_body
    assert '--network "${HEAVY_COMPOSE_PROJECT}_default"' in heavy_body
    assert "down --volumes --remove-orphans" in heavy_body
    assert "--profile test" not in heavy_body
    assert "wesp9-test-network" not in heavy_body


def test_heavy_compose_does_not_publish_or_reuse_deployment_resources() -> None:
    compose_text = (REPO_ROOT / "docker-compose.ci-heavy.yml").read_text(encoding="utf-8")

    assert "container_name:" not in compose_text
    assert "ports:" not in compose_text
    assert "./docker_data" not in compose_text
    assert "external:" not in compose_text


def test_ci_image_commands_never_resolve_dependencies_at_runtime() -> None:
    jenkins_text = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert "uv run --no-sync pytest tests/scripts -q" in jenkins_text
    assert "uv run --no-sync scripts/select_heavy_tests.py" in heavy_body
    assert "uv run --no-sync alembic upgrade head" in heavy_body
    assert "uv run --no-sync scripts/run_selected_heavy_tests.py" in heavy_body
    assert "sh -c '\n                                set -eu" in heavy_body


def test_heavy_required_rejects_selected_tests_that_are_skipped() -> None:
    jenkins_text = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert "uv run --no-sync scripts/run_selected_heavy_tests.py" in heavy_body
    assert 'subprocess.call(["pytest", "-q", *tests])' not in heavy_body


def test_heavy_required_publishes_the_runner_junit_report() -> None:
    jenkins_text = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert '-v "$WORKSPACE/reports:/reports"' in heavy_body
    assert "/reports/heavy-required.xml" in heavy_body
    assert "junit testResults: 'reports/heavy-required.xml', allowEmptyResults: true" in heavy_body
    assert "reports/heavy-tests.txt,reports/heavy-required.xml" in heavy_body


def test_non_publishing_builds_still_validate_the_production_target() -> None:
    jenkins_text = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    build_body = _stage_body(jenkins_text, "Build Runtime Image", "Publish Runtime Image")

    assert "when {" not in build_body
    assert "if (env.PUBLISH_IMAGE == 'true')" in build_body
    assert "--target ${RUNTIME_BUILD_TARGET}" in build_body
    assert "-t ${RUNTIME_VALIDATION_IMAGE}" in build_body
    assert "docker image rm -f ${RUNTIME_VALIDATION_IMAGE}" in build_body
