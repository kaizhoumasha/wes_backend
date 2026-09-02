import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_JENKINSFILE = REPO_ROOT / "Jenkinsfile.backend-ci"
GIT_EXECUTABLE = shutil.which("git")
if GIT_EXECUTABLE is None:
    raise RuntimeError("git executable is required")
GIT_LOCAL_ENV_VARS = subprocess.run(
    [GIT_EXECUTABLE, "rev-parse", "--local-env-vars"],
    cwd=REPO_ROOT,
    check=True,
    capture_output=True,
    text=True,
).stdout.splitlines()


def _stage_body(jenkins_text: str, stage_name: str, next_stage_name: str) -> str:
    return jenkins_text.split(f"stage('{stage_name}')", maxsplit=1)[1].split(f"stage('{next_stage_name}')", maxsplit=1)[
        0
    ]


def _git(*args: str, cwd: Path) -> str:
    clean_env = os.environ.copy()
    for name in GIT_LOCAL_ENV_VARS:
        clean_env.pop(name, None)
    result = subprocess.run(
        [GIT_EXECUTABLE, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=clean_env,
    )
    return result.stdout.strip()


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
    ci_build_body = _stage_body(jenkins_text, "Build CI Image", "Classify Required HEAVY")
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


def test_backend_provenance_labels_do_not_invalidate_shared_dependency_layers() -> None:
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    shared_layers = dockerfile_text.split("FROM base AS development", maxsplit=1)[0]

    assert "WES_VCS_REVISION" not in shared_layers
    assert "WES_SOURCE_TREE" not in shared_layers
    for stage_header, next_stage_header, source_copy in (
        ("FROM base AS development\n", "FROM base AS testing\n", "COPY . ."),
        (
            "FROM base AS testing\n",
            "FROM testing AS provider-artifact-validation\n",
            "COPY . .",
        ),
        ("FROM base AS production\n", None, "COPY --from=production-source /app /app"),
    ):
        stage = dockerfile_text.split(stage_header, maxsplit=1)[1]
        if next_stage_header is not None:
            stage = stage.split(next_stage_header, maxsplit=1)[0]
        assert stage.index(source_copy) < stage.index("ARG WES_VCS_REVISION")
        assert stage.index("ARG WES_VCS_REVISION") < stage.index("LABEL org.opencontainers.image.revision")
        assert 'com.zontec.wes.source-manifest="${WES_SOURCE_TREE}"' in stage


def test_builder_uses_pinned_uv_bootstrap_image() -> None:
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    builder_stage = dockerfile_text.split("FROM base AS builder", maxsplit=1)[1].split(
        "FROM base AS development", maxsplit=1
    )[0]

    assert (
        "FROM ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 "
        "AS uv-tool"
    ) in dockerfile_text
    assert "COPY --from=uv-tool /uv /usr/local/bin/uv" in builder_stage
    assert "pip install --no-cache-dir uv" not in builder_stage


def test_backend_and_mock_images_keep_cached_debian_layer_before_python_mirror_override() -> None:
    for dockerfile in (REPO_ROOT / "Dockerfile", REPO_ROOT / "tests/mock/Dockerfile"):
        dockerfile_text = dockerfile.read_text(encoding="utf-8")

        assert "ARG DEBIAN_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian" in dockerfile_text
        assert "ARG DEBIAN_SECURITY_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian-security" in dockerfile_text
        assert "ARG PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple" in dockerfile_text
        assert "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" in dockerfile_text
        assert "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" in dockerfile_text
        assert "ARG PYPI_INSTALL_MIRROR=https://mirrors.aliyun.com/pypi/simple" in dockerfile_text
        assert "PIP_INDEX_URL=${PYPI_INSTALL_MIRROR}" in dockerfile_text
        apt_install_index = dockerfile_text.index("apt-get update")
        python_mirror_arg_index = dockerfile_text.index("ARG PYPI_INSTALL_MIRROR=")
        python_mirror_env_index = dockerfile_text.index("PIP_INDEX_URL=${PYPI_INSTALL_MIRROR}")
        python_install_marker = "uv sync" if dockerfile == REPO_ROOT / "Dockerfile" else "pip install --no-cache-dir"
        python_install_index = dockerfile_text.index(python_install_marker)
        assert apt_install_index < python_mirror_arg_index < python_mirror_env_index < python_install_index

    backend_dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert backend_dockerfile_text.index("UV_DEFAULT_INDEX=${PYPI_INSTALL_MIRROR}") < backend_dockerfile_text.index(
        "uv sync"
    )


def test_testing_image_context_keeps_ci_contract_assets_and_ruff_layout_inputs() -> None:
    required_paths = (
        "Dockerfile",
        "docker-compose.ci-heavy.yml",
        "docker-compose.frontend.yml",
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
            "!docker-compose.frontend.yml",
            "!docker-compose.test-deploy.yml",
            "!docker-compose.wms-acceptance.yml",
            "!redis/",
            "!redis/**",
        } <= set(dockerignore_lines)


def test_host_compose_contracts_render_production_and_test_deploy_stacks() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    compose_body = _stage_body(jenkins_text, "Compose Contracts", "Mock Image Contracts")

    assert 'BACKEND_IMAGE="${RUNTIME_IMAGE_LOCAL}"' in compose_body
    assert 'FRONTEND_IMAGE="example.invalid/wes/wes_frontend:compose-contract-${CI_SHORT_COMMIT}"' in compose_body
    assert "WMS_PROVIDER_PROFILE_HOST_FILE" not in compose_body
    assert "--env-file .env.prod" in compose_body
    assert "-f docker-compose.yml" in compose_body
    assert "-f docker-compose.deploy.yml" in compose_body
    assert "--profile prod" in compose_body
    assert "--env-file .env.test" in compose_body
    assert "-f docker-compose.test-deploy.yml" in compose_body
    assert compose_body.count("config --no-env-resolution --quiet") == 2


def test_mock_image_contracts_run_for_merge_requests_and_verified_develop_pushes() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    mock_body = _stage_body(jenkins_text, "Mock Image Contracts", "HEAVY Required")

    assert "env.CI_IS_MERGE_REQUEST == 'true' || env.CI_RELEASE_GATE_READY == 'true'" in mock_body
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

    assert "timeout(time: 120, unit: 'MINUTES')" in jenkins_text
    assert "heavy_compose_attempt=1" in heavy_body
    assert 'if [ "$heavy_compose_attempt" -ge 3 ]; then' in heavy_body
    assert "heavy_compose_attempt=$((heavy_compose_attempt + 1))" in heavy_body
    assert "sleep 10" in heavy_body
    assert "-f docker-compose.ci-heavy.yml" in heavy_body
    assert '--project-name "${HEAVY_COMPOSE_PROJECT}"' in heavy_body
    assert '--network "${HEAVY_COMPOSE_PROJECT}_default"' in heavy_body
    assert "down --volumes --remove-orphans" in heavy_body
    assert "--profile test" not in heavy_body
    assert "wesp9-test-network" not in heavy_body


def test_develop_push_uses_the_verified_previous_sha_for_release_gates() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    checkout_body = _stage_body(jenkins_text, "Checkout Source", "Build CI Image")
    classification_body = _stage_body(jenkins_text, "Classify Required HEAVY", "Verification")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert "String beforeCommit = (env.gitlabBefore ?: '').trim()" in checkout_body
    assert "String afterCommit = (env.gitlabAfter ?: '').trim()" in checkout_body
    assert (
        "boolean isDevelopPush = gitlabActionType == 'PUSH' && sourceBranch == 'develop' && !isMergeRequest"
        in checkout_body
    )
    assert "env.CI_DIFF_BASE = ''" in checkout_body
    assert "env.CI_DIFF_BASE = targetRefFields[0].toLowerCase()" in checkout_body
    assert "if (!(beforeCommit ==~ /^[0-9a-fA-F]{40}$/) || beforeCommit ==~ /^0{40}$/)" in checkout_body
    assert "error('Develop push requires a non-zero 40-character gitlabBefore')" in checkout_body
    assert "if (!(afterCommit ==~ /^[0-9a-fA-F]{40}$/) || !afterCommit.equalsIgnoreCase(fullCommit))" in checkout_body
    assert "error('Develop push gitlabAfter must match the checked out HEAD')" in checkout_body
    assert "git merge-base --is-ancestor '${beforeCommit}' '${fullCommit}'" in checkout_body
    assert "if (ancestryStatus != 0)" in checkout_body
    assert "error('Develop push must fast-forward from gitlabBefore')" in checkout_body
    assert "env.CI_DIFF_BASE = beforeCommit" in checkout_body
    assert "env.CI_RELEASE_GATE_READY = 'true'" in checkout_body

    assert "env.CI_IS_MERGE_REQUEST == 'true' || env.CI_RELEASE_GATE_READY == 'true'" in heavy_body
    assert '-e CI_DIFF_BASE="${CI_DIFF_BASE}"' in classification_body
    assert 'scripts/select_heavy_tests.py --base "${CI_DIFF_BASE}"' in classification_body
    assert '--base "origin/${CI_TARGET_BRANCH}"' not in classification_body


def test_checkout_binds_internal_objects_to_trusted_event_refs() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    checkout_body = _stage_body(jenkins_text, "Checkout Source", "Build CI Image")

    assert "deleteDir()" in checkout_body
    assert "git remote add origin http://192.168.0.220:9080/wes/wes_backend.git" in checkout_body
    assert "git remote add origin https://git.zontecmes.com/wes/wes_backend.git" not in checkout_body
    assert checkout_body.count("timeout --kill-after=10s 600s") == 2
    assert "fetch_ref()" in checkout_body
    assert "for attempt in 1 2 3" in checkout_body
    assert 'if [ "$attempt" -lt 3 ]' in checkout_body
    assert "sleep 10" in checkout_body
    assert 'fetch_ref "+refs/heads/${CI_SOURCE_BRANCH}:refs/remotes/origin/${CI_SOURCE_BRANCH}"' in checkout_body
    assert 'fetch_ref "+refs/heads/${CI_TARGET_BRANCH}:refs/remotes/origin/${CI_TARGET_BRANCH}"' in checkout_body
    assert "fetch --no-tags --force --filter=blob:none origin" in checkout_body
    assert checkout_body.count("--filter=blob:none") == 1
    assert "--depth" not in checkout_body
    assert '"+refs/heads/${CI_SOURCE_BRANCH}:refs/remotes/origin/${CI_SOURCE_BRANCH}"' in checkout_body
    assert '"+refs/heads/${CI_TARGET_BRANCH}:refs/remotes/origin/${CI_TARGET_BRANCH}"' in checkout_body
    assert 'checkout --detach "refs/remotes/origin/${CI_SOURCE_BRANCH}"' in checkout_body
    assert "checkout([" not in checkout_body
    assert checkout_body.count("withCredentials([usernamePassword(") == 2
    assert "env.gitlabMergeRequestLastCommit" in checkout_body
    assert 'git rev-parse "refs/remotes/origin/${CI_SOURCE_BRANCH}^{commit}"' in checkout_body
    assert "Source event requires a non-zero 40-character trusted commit" in checkout_body
    assert "Fetched source ref must match the trusted event commit" in checkout_body
    assert "timeout --kill-after=5s 30s" in checkout_body
    assert "ls-remote --heads https://git.zontecmes.com/wes/wes_backend.git" in checkout_body
    assert "withCredentials([usernamePassword(" in checkout_body
    assert "credentialsId: 'gitlab-http-creds'" in checkout_body
    assert "usernameVariable: 'GITLAB_USERNAME'" in checkout_body
    assert "passwordVariable: 'GITLAB_PASSWORD'" in checkout_body
    assert "set +x" in checkout_body
    assert "credential.helper=!f()" in checkout_body
    assert '"username=$GITLAB_USERNAME" "password=$GITLAB_PASSWORD"' in checkout_body
    assert "ls-remote --heads https://git.zontecmes.com/wes/wes_backend.git" in checkout_body
    assert '"refs/heads/${CI_TARGET_BRANCH}"' in checkout_body
    assert "Merge request target lookup must return one exact trusted ref" in checkout_body
    assert 'git cat-file -e "${CI_DIFF_BASE}^{commit}"' in checkout_body
    assert "Fetched repository must contain the trusted target commit" in checkout_body
    assert "PreBuildMerge" not in checkout_body
    assert "mergeTarget" not in checkout_body


def test_exact_ref_fetch_preserves_multi_commit_ancestry_and_merge_base(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    fetched = tmp_path / "fetched"
    _git("init", "--bare", str(remote), cwd=tmp_path)
    _git("init", str(seed), cwd=tmp_path)
    _git("config", "user.name", "CI Test", cwd=seed)
    _git("config", "user.email", "ci@example.invalid", cwd=seed)

    tracked = seed / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    _git("add", "tracked.txt", cwd=seed)
    _git("commit", "-m", "base", cwd=seed)
    base = _git("rev-parse", "HEAD", cwd=seed)
    _git("branch", "-M", "develop", cwd=seed)
    tracked.write_text("develop\n", encoding="utf-8")
    _git("commit", "-am", "develop", cwd=seed)
    _git("branch", "feature", base, cwd=seed)
    _git("switch", "feature", cwd=seed)
    for index in range(3):
        tracked.write_text(f"feature-{index}\n", encoding="utf-8")
        _git("commit", "-am", f"feature-{index}", cwd=seed)
    feature_tip = _git("rev-parse", "HEAD", cwd=seed)
    _git("remote", "add", "origin", str(remote), cwd=seed)
    _git("push", "origin", "develop", "feature", cwd=seed)
    _git("config", "uploadpack.allowFilter", "true", cwd=remote)

    _git("init", str(fetched), cwd=tmp_path)
    _git("remote", "add", "origin", str(remote), cwd=fetched)
    for branch in ("develop", "feature"):
        _git(
            "fetch",
            "--no-tags",
            "--force",
            "--filter=blob:none",
            "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
            cwd=fetched,
        )

    assert any(
        line.startswith("?")
        for line in _git("rev-list", "--objects", "--all", "--missing=print", cwd=fetched).splitlines()
    )
    _git("merge-base", "--is-ancestor", base, feature_tip, cwd=fetched)
    assert _git("merge-base", "origin/develop", "origin/feature", cwd=fetched) == base
    assert _git("diff", "--name-only", "origin/develop...origin/feature", cwd=fetched) == "tracked.txt"
    _git("checkout", "--detach", "origin/feature", cwd=fetched)
    assert (fetched / "tracked.txt").read_text(encoding="utf-8") == "feature-2\n"


def test_git_topology_helper_removes_parent_repository_environment(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    _git("init", str(repository), cwd=tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign.git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "foreign.index"))

    assert _git("rev-parse", "--show-toplevel", cwd=repository) == str(repository)


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
    classification_body = _stage_body(jenkins_text, "Classify Required HEAVY", "Verification")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert 'if [[ "$CI_MODE" == "true" ]]; then' in quality_gate_text
    assert 'uv run --no-sync "$@"' in quality_gate_text
    assert "uv run --no-sync scripts/select_heavy_tests.py" in classification_body
    assert "uv run --no-sync alembic upgrade head" in heavy_body
    assert "uv run --no-sync scripts/run_selected_heavy_tests.py" in heavy_body
    assert "sh -c '\n                            set -eu" in heavy_body


def test_heavy_required_rejects_selected_tests_that_are_skipped() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert "uv run --no-sync scripts/run_selected_heavy_tests.py" in heavy_body
    assert 'subprocess.call(["pytest", "-q", *tests])' not in heavy_body


def test_heavy_required_runs_the_exact_selector_manifest_without_legacy_filtering() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    classification_body = _stage_body(jenkins_text, "Classify Required HEAVY", "Verification")
    heavy_body = _stage_body(jenkins_text, "HEAVY Required", "Build Runtime Image")

    assert "scripts/select_heavy_tests.py" in classification_body
    assert "> reports/heavy-tests.selected.txt" in classification_body
    assert "cp reports/heavy-tests.selected.txt reports/heavy-tests.txt" in heavy_body
    assert "runtime-inbox-acceptance-owned.txt" not in heavy_body
    assert "grep -Fvx -f" not in heavy_body


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


def test_runtime_image_publication_requires_a_verified_gitlab_develop_push_without_cross_repo_deploy() -> None:
    jenkins_text = ACTIVE_JENKINSFILE.read_text(encoding="utf-8")
    push_body = jenkins_text.split("stage('Push Runtime Image')", maxsplit=1)[1].split("\n    post {", maxsplit=1)[0]

    assert "env.CI_EVENT_TYPE == 'PUSH'" in push_body
    assert "env.CI_IS_MERGE_REQUEST != 'true'" in push_body
    assert "env.CI_SOURCE_BRANCH == 'develop'" in push_body
    assert "env.CI_RELEASE_GATE_READY == 'true'" in push_body
    assert "stage('Trigger Test Deploy')" not in jenkins_text
    assert "FRONTEND_IMAGE_TAG" not in jenkins_text
