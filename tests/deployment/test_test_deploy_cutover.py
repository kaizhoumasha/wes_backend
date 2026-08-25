from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from tools.release_checker.release_checker import ModeDecision, build_compatibility_report, canonical_json_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_PATH = REPO_ROOT / "Jenkinsfile.test-deploy"
GIT_BIN = shutil.which("git")
if GIT_BIN is None:
    raise RuntimeError("git is required for deployment pipeline tests")


def _pipeline() -> str:
    return PIPELINE_PATH.read_text(encoding="utf-8")


def _marked_shell(begin: str, end: str) -> str:
    pipeline = _pipeline()
    start = pipeline.index(begin) + len(begin)
    stop = pipeline.index(end, start)
    return "set -e\nset -o pipefail\n" + textwrap.dedent(pipeline[start:stop]).replace(r"\$", "$")


def _marked_python(begin: str, end: str) -> str:
    pipeline = _pipeline()
    assert begin in pipeline, f"missing embedded Python marker: {begin}"
    start = pipeline.index(begin) + len(begin)
    stop = pipeline.index(end, start)
    return textwrap.dedent(pipeline[start:stop])


def _checker_preflight_functions() -> str:
    pipeline = _pipeline()
    start = pipeline.index("write_checker_inputs() {")
    stop = pipeline.index("business_preflight() {", start)
    return textwrap.dedent(pipeline[start:stop]).replace(r"\$", "$")


def _embedded_python_blocks() -> list[str]:
    pipeline = _pipeline()
    return [
        textwrap.dedent(match.group("source"))
        for pattern in (r"<<'PY'\n(?P<source>.*?)\nPY", r"-c '\n(?P<source>.*?)\n\s*'")
        for match in re.finditer(pattern, pipeline, flags=re.DOTALL)
    ]


def _render_groovy_newline_escapes(source: str) -> str:
    rendered: list[str] = []
    index = 0
    while index < len(source):
        if source[index : index + 2] == r"\n":
            rendered.append("\n")
            index += 2
        elif source[index : index + 2] == r"\\":
            rendered.append("\\")
            index += 2
        else:
            rendered.append(source[index])
            index += 1
    return "".join(rendered)


def _write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def _isolated_git_env() -> dict[str, str]:
    env = os.environ.copy()
    names = subprocess.run(
        [GIT_BIN, "rev-parse", "--local-env-vars"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for name in names:
        env.pop(name, None)
    return env


def test_deploy_interface_accepts_only_independent_candidate_digests() -> None:
    pipeline = _pipeline()

    for name in (
        "DEPLOY_SCOPE",
        "FRONTEND_CANDIDATE_DIGEST",
        "BACKEND_CANDIDATE_DIGEST",
        "DEPLOY_SOURCE_COMMIT_SHA",
        "FORCE_FULL",
        "WARN_APPROVAL_REASON",
    ):
        assert f"name: '{name}'" in pipeline
    for forbidden in (
        "BACKEND_IMAGE_TAG",
        "FRONTEND_IMAGE_TAG",
        "BACKEND_COMMIT_SHA",
        "FRONTEND_COMMIT_SHA",
        "OPENAPI_SHA256",
        "PERMISSIONS_SHA256",
        "FORCE_FAST",
        "PEER_IMAGE",
        "SOURCE_BRANCH",
    ):
        assert forbidden not in pipeline
    assert "name: 'CURRENT_FRONTEND'" not in pipeline
    assert "name: 'CURRENT_BACKEND'" not in pipeline
    assert "name: 'CHECKER_DIGEST'" not in pipeline


def test_embedded_python_compiles_after_groovy_newline_rendering() -> None:
    blocks = _embedded_python_blocks()

    assert len(blocks) == 11
    for index, source in enumerate(blocks):
        compile(_render_groovy_newline_escapes(source), f"Jenkinsfile.test-deploy:{index}", "exec")


def test_release_label_extraction_executes_after_groovy_newline_rendering(tmp_path: Path) -> None:
    source = next(block for block in _embedded_python_blocks() if "consumer-openapi.sha256" in block)
    all_labels = tmp_path / "all-labels.json"
    selected_labels = tmp_path / "selected-labels.json"
    labels = {
        "org.wes.release.consumer-openapi.sha256": "openapi",
        "org.wes.release.required-operations.sha256": "operations",
        "org.wes.release.required-permissions.sha256": "permissions",
        "org.wes.release.frontend-dependencies.sha256": "dependencies",
        "org.wes.release.frontend-recipe.sha256": "recipe",
        "unrelated": "ignored",
    }
    all_labels.write_text(json.dumps(labels), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-", "frontend", str(all_labels), str(selected_labels)],
        input=_render_groovy_newline_escapes(source),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(selected_labels.read_text(encoding="utf-8")) == {
        key: value for key, value in labels.items() if key != "unrelated"
    }
    assert selected_labels.read_bytes().endswith(b"\n")


def test_database_head_queries_use_schema_qualified_alembic_version_table() -> None:
    pipeline = _pipeline()

    qualified_query = "select version_num from wes_sys.alembic_version order by version_num"
    assert pipeline.count(qualified_query) == 2
    assert 'from alembic_version order by version_num"' not in pipeline


@pytest.mark.parametrize(
    ("scope", "selected_name", "selected_side"),
    [
        ("FRONTEND", "FRONTEND_CANDIDATE_DIGEST", "frontend"),
        ("BACKEND", "BACKEND_CANDIDATE_DIGEST", "backend"),
    ],
)
def test_optional_empty_jenkins_parameters_are_safe_under_nounset(
    tmp_path: Path, scope: str, selected_name: str, selected_side: str
) -> None:
    digest = "sha256:" + "1" * 64
    (tmp_path / "effective-facts-base.json").write_text(json.dumps({"deploy": {}, "runtime": {}}), encoding="utf-8")
    report = tmp_path / "compatibility-report.json"
    report.write_text(json.dumps({"pre_cutover_state": "READY", "effective_mode": "FAST"}), encoding="utf-8")
    checker_trace = tmp_path / "checker-arguments.txt"
    shell = """
        set -eu
        run_checker_container() { printf '%s\n' "$*" >"$CHECKER_TRACE"; }
    """ + _checker_preflight_functions()
    shell += "\nwrite_checker_inputs\nrun_release_checker\nprintf '%s\\n' \"$EFFECTIVE_MODE\"\n"
    env = os.environ | {
        "CHECKER_DIGEST_VALUE": digest,
        "CHECKER_IMAGE": "checker-image",
        "CHECKER_TRACE": str(checker_trace),
        "CURRENT_BACKEND_DIGEST": digest,
        "CURRENT_EVIDENCE_VALID": "false",
        "CURRENT_FRONTEND_DIGEST": digest,
        "CURRENT_RELEASE_EVIDENCE_DIR": str(tmp_path / "current"),
        "DATABASE_HEADS": "db-head",
        "DATABASE_RELATION": "ancestor",
        "DEPLOY_SCOPE": scope,
        "FORCE_FULL_VALUE": "false",
        "PREFLIGHT_DIR": str(tmp_path),
        "RELEASE_ID": "test-release",
        "REPORT_DIR": str(tmp_path),
        "REPORT_FILE": str(report),
        selected_name: digest,
    }

    completed = subprocess.run(
        ["/bin/bash", "-c", shell],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "FAST\n"
    assert "current-fingerprints" not in checker_trace.read_text(encoding="utf-8")
    assert json.loads((tmp_path / "candidate-digests.json").read_text(encoding="utf-8")) == {selected_side: digest}


def test_scope_validation_requires_only_selected_candidates_and_rejects_peer_input() -> None:
    pipeline = _pipeline()

    assert "DEPLOY_SCOPE 必须是 FRONTEND、BACKEND 或 BOTH" in pipeline
    assert "FRONTEND scope 只接受 frontend candidate digest" in pipeline
    assert "BACKEND scope 只接受 backend candidate digest" in pipeline
    assert "BOTH scope 必须同时提供两个 candidate digest" in pipeline
    assert "sha256:[0-9a-f]{64}" in pipeline


def test_checker_digest_is_fixed_by_deploy_source_and_selected_images_keep_revision_identity() -> None:
    pipeline = _pipeline()

    assert "git log -1 --format=%H -- tools/release_checker Jenkinsfile.release-checker-ci" in pipeline
    assert 'CHECKER_IMAGE_TAG="${CHECKER_IMAGE_REPO}:${CHECKER_SOURCE_COMMIT}"' in pipeline
    assert "CHECKER_SOURCE_COMMIT = DEPLOY_SOURCE_COMMIT_SHA" not in pipeline
    assert "CHECKER_CANDIDATE_DIGEST" not in pipeline
    assert "org.opencontainers.image.revision" in pipeline
    assert pipeline.count('validate_selected_image_revision "${FRONTEND_IMAGE}"') >= 2
    assert pipeline.count('validate_selected_image_revision "${BACKEND_IMAGE}"') >= 2
    assert "image revision 必须是 40 位小写 commit SHA" in pipeline
    assert "com.zontec.wes.backend-contract-revision" not in pipeline
    assert "com.zontec.wes.openapi-sha256" not in pipeline
    assert "com.zontec.wes.permissions-sha256" not in pipeline


def test_checker_repo_digest_selection_is_exact_and_requires_one_match() -> None:
    pipeline = _pipeline()

    assert "checker-repo-digests.json" in pipeline
    assert 're.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}"' in pipeline
    assert "if len(matches) != 1:" in pipeline
    assert "index .RepoDigests 0" not in pipeline


@pytest.mark.parametrize("expected_matches", [0, 2])
def test_checker_repo_digest_selector_rejects_missing_or_duplicate_repository_matches(
    tmp_path: Path, expected_matches: int
) -> None:
    repository = "registry.example/wes/checker"
    digest = "sha256:" + "a" * 64
    values = ["mirror.example/wes/checker@" + digest]
    values.extend([repository + "@" + digest] * expected_matches)
    source = tmp_path / "repo-digests.json"
    source.write_text(json.dumps(values), encoding="utf-8")

    completed = subprocess.run(
        [
            os.environ.get("PYTHON", "python3"),
            "-c",
            _marked_python("# TEST_CHECKER_REPODIGEST_SELECTOR_BEGIN", "# TEST_CHECKER_REPODIGEST_SELECTOR_END"),
            str(source),
            repository,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""


def test_checker_repo_digest_selector_picks_the_only_exact_repository_from_multiple_entries(tmp_path: Path) -> None:
    repository = "registry.example/wes/checker"
    expected = repository + "@sha256:" + "a" * 64
    source = tmp_path / "repo-digests.json"
    source.write_text(
        json.dumps(["mirror.example/wes/checker@sha256:" + "b" * 64, expected]),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            os.environ.get("PYTHON", "python3"),
            "-c",
            _marked_python("# TEST_CHECKER_REPODIGEST_SELECTOR_BEGIN", "# TEST_CHECKER_REPODIGEST_SELECTOR_END"),
            str(source),
            repository,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == expected


def test_deploy_commit_can_differ_from_checker_owner_commit(tmp_path: Path) -> None:
    env = _isolated_git_env()
    subprocess.run([GIT_BIN, "init", "-b", "develop", str(tmp_path)], env=env, check=True, capture_output=True)
    subprocess.run([GIT_BIN, "config", "user.name", "Task 6"], cwd=tmp_path, env=env, check=True)
    subprocess.run([GIT_BIN, "config", "user.email", "task6@example.invalid"], cwd=tmp_path, env=env, check=True)
    checker = tmp_path / "tools/release_checker/release_checker.py"
    checker.parent.mkdir(parents=True)
    checker.write_text("print('checker')\n", encoding="utf-8")
    (tmp_path / "Jenkinsfile.release-checker-ci").write_text("checker pipeline\n", encoding="utf-8")
    checker_test = tmp_path / "tests/deployment/test_release_checker_ci.py"
    checker_test.parent.mkdir(parents=True)
    checker_test.write_text("def test_checker(): pass\n", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "."], cwd=tmp_path, env=env, check=True)
    subprocess.run([GIT_BIN, "commit", "-m", "checker owner"], cwd=tmp_path, env=env, check=True, capture_output=True)
    checker_commit = subprocess.run(
        [GIT_BIN, "rev-parse", "HEAD"], cwd=tmp_path, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()
    (tmp_path / "Jenkinsfile.test-deploy").write_text("deploy pipeline\n", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "Jenkinsfile.test-deploy"], cwd=tmp_path, env=env, check=True)
    subprocess.run([GIT_BIN, "commit", "-m", "deploy owner"], cwd=tmp_path, env=env, check=True, capture_output=True)
    deploy_commit = subprocess.run(
        [GIT_BIN, "rev-parse", "HEAD"], cwd=tmp_path, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()

    selected = subprocess.run(
        [
            GIT_BIN,
            "log",
            "-1",
            "--format=%H",
            "--",
            "tools/release_checker",
            "Jenkinsfile.release-checker-ci",
            "tests/deployment/test_release_checker_ci.py",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert selected == checker_commit
    assert selected != deploy_commit


def test_single_side_discovers_and_reverifies_live_peer_without_operator_input() -> None:
    pipeline = _pipeline()

    assert "discover_live_digest frontend" in pipeline
    assert "discover_current_backend_topology" in pipeline
    assert 'container_id=$(compose ps -a -q "$service")' in pipeline
    assert "for service in api celery celery-wms-fulfillment celery_beat flower" in pipeline
    assert "verify_current_peer_evidence" in pipeline
    assert "reverify_unselected_peer" in pipeline
    assert pipeline.index("verify_current_peer_evidence") < pipeline.index("extract_release_artifacts")
    assert pipeline.index("reverify_unselected_peer") < pipeline.index("MAINTENANCE_MODE=true")


def test_preflight_does_not_mutate_live_deploy_source_before_maintenance() -> None:
    pipeline = _pipeline()
    deploy_body = pipeline[pipeline.index("stage('Deploy Test Environment')") :]
    maintenance = deploy_body.index("MAINTENANCE_MODE=true")
    premaintenance = deploy_body[:maintenance]
    postmaintenance = deploy_body[maintenance:]

    assert 'STAGED_DEPLOY_PATH="${PREFLIGHT_DIR}/deploy-source"' in premaintenance
    assert 'COMPOSE_PROJECT_DIR="${STAGED_DEPLOY_PATH}"' in premaintenance
    assert 'cd "${DEPLOY_PATH}"' not in premaintenance
    assert 'git reset --hard "${DEPLOY_SOURCE_COMMIT_SHA}"' not in premaintenance
    assert "git clean -fd" not in premaintenance
    assert 'cp -f "${DEPLOY_ENV_FILE}" .env' not in premaintenance
    assert "switch_live_deploy_source" in postmaintenance
    assert postmaintenance.index("compose stop nginx") < postmaintenance.index("switch_live_deploy_source")
    assert 'mv -f "${runtime_env_tmp}" "${DEPLOY_PATH}/.env"' in pipeline


def test_all_identity_compatibility_and_business_preflight_precede_maintenance() -> None:
    pipeline = _pipeline()
    maintenance = pipeline.index("MAINTENANCE_MODE=true")

    ordered = [
        "validate_selected_image_revision",
        "pin_checker_image",
        "discover_current_backend_topology",
        "verify_current_peer_evidence",
        "extract_release_artifacts",
        "write_effective_facts",
        "query_database_state",
        "run_release_checker",
        "business_preflight",
        "reverify_unselected_peer",
    ]
    calls = pipeline[pipeline.index('FRONTEND_IMAGE=""') :]
    positions = [calls.index(item) for item in ordered]
    assert positions == sorted(positions)
    assert positions[-1] < maintenance


def test_checker_is_reused_once_with_hard_timeout_and_report_archival() -> None:
    pipeline = _pipeline()

    assert pipeline.count("run_release_checker()") == 1
    invocation = pipeline[pipeline.index("run_release_checker()") : pipeline.index("business_preflight()")]
    assert "run_checker_container 60" in invocation
    assert '"${CHECKER_IMAGE}"' in invocation
    assert "--force-full" in invocation
    assert "--warn-approval-reason" in invocation
    assert "tools/release_checker/release_checker.py" not in pipeline
    assert "classify_release_mode" not in pipeline
    assert "oasdiff" not in pipeline
    assert "archiveArtifacts" in pipeline
    assert "compatibility-report.json" in pipeline


def test_current_report_is_strictly_validated_by_the_pinned_checker_image() -> None:
    pipeline = _pipeline()

    validation = pipeline[
        pipeline.index("validate_current_report()") : pipeline.index("verify_current_peer_evidence()")
    ]
    assert '"${CHECKER_IMAGE}"' in validation
    assert "validate_compatibility_report" in validation
    assert "_read_json_object" in validation
    assert "--entrypoint python" in validation
    assert "json.load(open" not in validation


@pytest.mark.parametrize("tamper", ["extra-field", "diff-hash"])
def test_current_report_validator_rejects_malformed_or_tampered_report(tmp_path: Path, tamper: str) -> None:
    digest = "sha256:" + "1" * 64
    report = build_compatibility_report(
        release_id="previous",
        deploy_scope="BOTH",
        candidate_digests={"frontend": digest, "backend": digest},
        current_digests={"frontend": digest, "backend": digest},
        checker_digest=digest,
        artifact_hashes={
            "frontend": {
                "consumer_openapi": "1" * 64,
                "required_operations": "2" * 64,
                "required_permissions": "3" * 64,
            },
            "backend": {"provider_openapi": "4" * 64, "provided_permissions": "5" * 64},
        },
        mode=ModeDecision(auto_mode="FAST", effective_mode="FAST", reasons=()),
        findings=(),
    )
    if tamper == "extra-field":
        report["unexpected"] = True
    else:
        report["compatibility"]["diff_hash"] = "f" * 64
    report_path = tmp_path / "compatibility-report.json"
    report_path.write_bytes(canonical_json_bytes(report))

    completed = subprocess.run(
        [
            os.environ.get("PYTHON", "python3"),
            "-c",
            _render_groovy_newline_escapes(
                _marked_python("# TEST_CURRENT_REPORT_VALIDATOR_BEGIN", "# TEST_CURRENT_REPORT_VALIDATOR_END")
            ),
            str(report_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""


def test_checker_container_has_one_fixed_name_and_is_always_force_removed() -> None:
    pipeline = _pipeline()

    assert (
        'env.CHECKER_CONTAINER_NAME = "wes-release-checker-${env.BUILD_NUMBER}-${deploySourceCommit.take(12)}"'
        in pipeline
    )
    assert pipeline.count('--name "${CHECKER_CONTAINER_NAME}"') == 1
    assert pipeline.count('docker rm -f "${CHECKER_CONTAINER_NAME}"') >= 3
    validator = pipeline[pipeline.index("validate_current_report()") : pipeline.index("verify_current_peer_evidence()")]
    checker = pipeline[pipeline.index("run_release_checker()") : pipeline.index("business_preflight()")]
    assert "docker run --rm" not in validator + checker


def test_checker_timeout_force_removes_a_lingering_named_container(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "checker-container"
    trace = tmp_path / "checker-trace"
    _write_executable(
        bin_dir / "docker",
        """
        #!/bin/bash
        if [ "$1" = rm ]; then
            printf 'rm:%s\n' "${@: -1}" >>"$TRACE_FILE"
            rm -f "$CHECKER_STATE"
            exit 0
        fi
        if [ "$1" = run ]; then
            printf 'run\n' >>"$TRACE_FILE"
            printf lingering >"$CHECKER_STATE"
            exit 124
        fi
        exit 90
        """,
    )
    _write_executable(
        bin_dir / "timeout",
        """
        #!/bin/bash
        [ "$1" = --signal=KILL ] && shift
        shift
        exec "$@"
        """,
    )
    shell = _marked_shell("# TEST_CHECKER_CONTAINER_RUNNER_BEGIN", "# TEST_CHECKER_CONTAINER_RUNNER_END")
    completed = subprocess.run(
        ["/bin/bash", "-c", shell + "\nrun_checker_container 60 checker-image --help"],
        cwd=tmp_path,
        env=os.environ
        | {
            "CHECKER_CONTAINER_NAME": "wes-release-checker-test",
            "CHECKER_STATE": str(state),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TRACE_FILE": str(trace),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 124
    assert not state.exists()
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "rm:wes-release-checker-test",
        "run",
        "rm:wes-release-checker-test",
    ]


def test_effective_inputs_and_database_rules_are_owned_by_checker_inputs() -> None:
    pipeline = _pipeline()

    for path in (
        "Jenkinsfile.test-deploy",
        "docker-compose.test-deploy.yml",
        "scripts/wait_for_http.py",
        "nginx/nginx.conf",
        "nginx/conf.d/default.conf",
        "postgresql/base.conf",
        "postgresql/test.conf",
        "redis/base.conf",
        "redis/test.conf",
        "runtime/.env",
        "runtime/wms-provider.yaml",
    ):
        assert path in pipeline
    assert (
        'deploy_paths = ["Jenkinsfile.test-deploy", "docker-compose.test-deploy.yml", "scripts/wait_for_http.py", '
        '"nginx/nginx.conf", "nginx/conf.d/default.conf", "postgresql/base.conf", "postgresql/test.conf", '
        '"redis/base.conf", "redis/test.conf"]'
    ) in pipeline
    assert (
        "docker-compose.deploy.yml"
        not in pipeline[pipeline.index("write_effective_facts()") : pipeline.index("query_database_state()")]
    )
    assert ".rglob(" not in pipeline
    assert "postgresql/dev.conf" not in pipeline
    assert "postgresql/prod.conf" not in pipeline
    assert "redis/dev.conf" not in pipeline
    assert "redis/prod.conf" not in pipeline
    assert "relation_to_candidate" in pipeline
    assert "current_heads" in pipeline
    assert "--current-fingerprints" in pipeline
    assert "CURRENT_EVIDENCE_VALID=false" in pipeline
    assert '[ "${CURRENT_FRONTEND_DIGEST}" = "${EVIDENCE_FRONTEND_DIGEST}" ]' in pipeline
    assert '[ "${CURRENT_BACKEND_DIGEST}" = "${EVIDENCE_BACKEND_DIGEST}" ]' in pipeline
    assert '[ "${CURRENT_EVIDENCE_VALID:-false}" != true ] || set -- "$@" --current-fingerprints' in pipeline
    assert "--force-fast" not in pipeline
    assert "current-evidence.missing" not in pipeline


def test_fast_is_side_specific_and_backend_is_an_atomic_service_set() -> None:
    pipeline = _pipeline()
    fast = pipeline[pipeline.index("run_fast_cutover()") : pipeline.index("run_full_cutover()")]

    assert '"${DEPLOY_SCOPE}" = "FRONTEND"' in fast
    assert "--no-deps --force-recreate --wait frontend" in fast
    assert "api celery celery-wms-fulfillment celery_beat flower" in fast
    backend_stop = "compose stop api celery celery-wms-fulfillment celery_beat flower"
    backend_start = (
        "compose up -d --no-deps --force-recreate --wait api celery celery-wms-fulfillment celery_beat flower"
    )
    assert fast.count(backend_stop) == 2
    assert fast.index(backend_stop) < fast.index(backend_start)
    frontend_branch = fast[fast.index('if [ "${DEPLOY_SCOPE}" = "FRONTEND" ]') : fast.index("elif")]
    assert backend_stop not in frontend_branch
    assert "migration" not in fast
    assert "backup" not in fast


def test_fast_cutover_has_one_two_minute_budget_covering_start_and_readiness() -> None:
    pipeline = _pipeline()
    cutover = pipeline[pipeline.index("# TEST_DEPLOY_CUTOVER_BEGIN") : pipeline.index("# TEST_DEPLOY_CUTOVER_END")]

    assert "FAST_CUTOVER_DEADLINE=$(( $(date +%s) + 120 ))" in cutover
    assert "fast_remaining_seconds" in cutover
    assert 'timeout --signal=KILL "${remaining}s"' in cutover
    assert "cutover_compose" in cutover
    assert "run_with_cutover_budget python3 scripts/wait_for_http.py" in cutover
    assert "--wait-timeout 120" not in cutover


def test_external_listener_uses_the_real_compose_http_port_variable() -> None:
    pipeline = _pipeline()

    assert "NGINX_PORT" not in pipeline
    assert pipeline.count("${NGINX_HTTP_PORT:-8080}") == 3


def test_full_preserves_backup_forward_migration_authorization_and_readiness() -> None:
    pipeline = _pipeline()
    full = pipeline[pipeline.index("run_full_cutover()") : pipeline.index("# TEST_DEPLOY_CUTOVER_END")]

    assert "pg_dump" in full
    assert "compose stop api celery celery-wms-fulfillment celery_beat flower frontend" in full
    assert full.index("compose stop api celery") < full.index("pg_dump") < full.index("upgrade head")
    assert "migrated_head_count" in full
    assert '[ "$migrated_heads" = "${EXPECTED_SCHEMA_HEAD}" ]' in full
    assert "alembic" in full and "upgrade head" in full
    assert "sync_permissions.py --apply" in full
    assert "sync_permissions.py --check" in full
    assert "scripts/check_bootstrap_admin_login.py" in full
    assert "for service in api celery celery-wms-fulfillment celery_beat flower" in full
    assert "verify_service_digest frontend" in full
    assert "compose config --services" in full
    assert "scripts/wait_for_http.py" in full
    assert "sync_menus.py" not in pipeline
    assert "menu-manifest.json" not in pipeline


def test_compose_files_remain_the_complete_backend_digest_topology() -> None:
    for name in ("docker-compose.test-deploy.yml", "docker-compose.deploy.yml"):
        compose = yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8").replace("!override", ""))
        services = compose["services"]
        assert {"api", "celery", "celery-wms-fulfillment", "celery_beat", "flower", "frontend", "nginx"} <= set(
            services
        )
        for service_name in ("api", "celery", "celery-wms-fulfillment", "celery_beat", "flower"):
            service = services[service_name]
            parent_name = service.get("extends", {}).get("service")
            parent = services[parent_name] if parent_name else service
            assert parent["image"].startswith("${BACKEND_IMAGE")


def _fake_docker() -> str:
    return r"""
        #!/bin/bash
        set -u
        trace() { printf '%s\n' "$1" >>"$TRACE_FILE"; }
        fail() { [ "${FAIL_STAGE:-}" = "$1" ]; }
        if [ "$1" = inspect ]; then
            service="${@: -1}"
            case "$service" in
                svc-api|svc-celery|svc-celery-wms-fulfillment|svc-celery_beat|svc-flower)
                    name="${service#svc-}"
                    if [ "$(cat "$NGINX_STATE")" = running ]; then
                        trace "peer-reverify:${name}"
                        if [ "${FAIL_STAGE:-}" = peer-drift-non-api ] && [ "$name" = celery ]; then
                            printf 'repo/backend@sha256:%064d\n' 9
                        else
                            cat "$LIVE_STATE_DIR/$name"
                        fi
                    else
                        trace "digest-check:${name}"
                        cat "$LIVE_STATE_DIR/$name"
                    fi
                    exit 0 ;;
                svc-frontend)
                    if [ "$(cat "$NGINX_STATE")" = running ]; then
                        trace peer-reverify:frontend
                    else
                        trace digest-check:frontend
                    fi
                    cat "$LIVE_STATE_DIR/frontend"
                    exit 0 ;;
            esac
            exit 79
        fi
        [ "$1" = compose ] || exit 80
        shift
        while [ "$1" = --env-file ] || [ "$1" = --project-directory ] || [ "$1" = -f ]; do shift 2; done
        operation="$1"; shift
        args="$*"
        case "$operation:$args" in
            "stop:nginx") trace nginx-stop; printf stopped >"$NGINX_STATE" ;;
            stop:*api*celery*celery-wms-fulfillment*celery_beat*flower*frontend*) trace application-stop; exit 0 ;;
            stop:*api*celery*celery-wms-fulfillment*celery_beat*flower*) trace backend-stop; exit 0 ;;
            up:*api*celery*celery-wms-fulfillment*celery_beat*flower*frontend*)
                trace backend-start; trace frontend-start; fail application-start && exit 82
                for service in api celery celery-wms-fulfillment celery_beat flower; do
                    printf '%s\n' "$BACKEND_IMAGE" >"$LIVE_STATE_DIR/$service"
                done
                printf '%s\n' "$FRONTEND_IMAGE" >"$LIVE_STATE_DIR/frontend"
                exit 0 ;;
            up:*api*celery*celery-wms-fulfillment*celery_beat*flower*)
                trace backend-start; fail application-start && exit 82
                for service in api celery celery-wms-fulfillment celery_beat flower; do
                    printf '%s\n' "$BACKEND_IMAGE" >"$LIVE_STATE_DIR/$service"
                done
                exit 0 ;;
            up:*frontend*)
                trace frontend-start; fail application-start && exit 81
                printf '%s\n' "$FRONTEND_IMAGE" >"$LIVE_STATE_DIR/frontend"
                exit 0 ;;
            exec:*pg_dump*) trace backup; fail backup && exit 83; exit 0 ;;
            exec:*test*-s*) trace backup-proof; exit 0 ;;
            exec:*psql*alembic_version*)
                trace migration-head
                fail migration-head && printf 'wrong-head\n' || printf '%s\n' "$EXPECTED_SCHEMA_HEAD"
                exit 0 ;;
            run:*alembic*upgrade*head*) trace migration; fail migration && exit 84; exit 0 ;;
            run:*sync_permissions.py*--apply*) trace authorization-apply; fail authorization && exit 85; exit 0 ;;
            run:*sync_permissions.py*--check*) trace authorization-check; exit 0 ;;
            exec:*check_bootstrap_admin_login.py*) trace admin-login; fail admin-login && exit 86; exit 0 ;;
            exec:*curl*) trace backend-ready; fail readiness && exit 87; exit 0 ;;
            exec:*wget*) trace frontend-ready; fail readiness && exit 88; exit 0 ;;
            up:*nginx*) trace nginx-start; printf running >"$NGINX_STATE"; exit 0 ;;
            config:--services)
                printf '%s\n' api celery celery-wms-fulfillment celery_beat flower frontend nginx db redis
                fail topology-config-query && exit 90
                exit 0
                ;;
            ps:-a\ -q*) printf 'svc-%s\n' "${args#-a -q }" ;;
            ps:-q*) printf 'svc-%s\n' "${args#-q }" ;;
            ps:*--status*running*--services*)
                printf '%s\n' api celery celery-wms-fulfillment celery_beat flower frontend redis
                fail topology-missing-db || printf '%s\n' db
                [ "$(cat "$NGINX_STATE")" != running ] || printf '%s\n' nginx
                trace topology-running
                fail topology-running-query && exit 91
                exit 0
                ;;
            *) exit 89 ;;
        esac
    """


def _run_preflight_order(tmp_path: Path, fail_stage: str = "") -> tuple[int, list[str]]:
    trace_file = tmp_path / "preflight-trace"
    harness = r"""
        trace() { printf '%s\n' "$1" >>"$TRACE_FILE"; }
        step() { trace "$1"; [ "${FAIL_STAGE:-}" != "$1" ]; }
        abort_pre_cutover() { trace "abort:$1"; return 1; }
        wait_for_image() {
            case "$1" in *frontend*) step frontend-candidate-pull ;; *backend*) step backend-candidate-pull ;; esac
        }
        validate_selected_image_revision() { step "revision:${1%%@*}"; }
        pin_checker_image() { step checker-image-pin; }
        compose() {
            [ "$1 $2 $3" = "ps -q frontend" ] && trace current-frontend-container && printf 'current-frontend\n' && return 0
            [ "$1 $2 $3" = "ps -q api" ] && trace current-backend-container && printf 'current-backend\n' && return 0
            return 1
        }
        discover_live_image() { trace "live-image:$1"; printf 'repo/%s@sha256:%064d\n' "$1" 1; }
        discover_live_digest() { trace "live-digest:$1"; printf 'sha256:%064d\n' 1; }
        discover_current_backend_topology() { step current-backend-topology; }
        verify_current_peer_evidence() { step current-peer-evidence; }
        extract_release_artifacts() { step "artifact:$1"; }
        write_effective_facts() { step effective-input-hash; }
        query_database_state() { step database-state; }
        write_checker_inputs() { step checker-inputs; }
        run_release_checker() { step compatibility-check; }
        business_preflight() { step business-preflight; }
        write_next_current_fingerprints() { step current-evidence-build; }
    """
    env = os.environ | {
        "BACKEND_CANDIDATE_DIGEST": "sha256:" + "2" * 64,
        "BACKEND_IMAGE_REPO": "repo/backend",
        "DEPLOY_SCOPE": "BOTH",
        "FAIL_STAGE": fail_stage,
        "FRONTEND_CANDIDATE_DIGEST": "sha256:" + "1" * 64,
        "FRONTEND_IMAGE_REPO": "repo/frontend",
        "TRACE_FILE": str(trace_file),
    }
    completed = subprocess.run(
        [
            "/bin/bash",
            "-c",
            "set -e\nset -o pipefail\n"
            + textwrap.dedent(harness)
            + _marked_shell("# TEST_DEPLOY_PREFLIGHT_ORDER_BEGIN", "# TEST_DEPLOY_PREFLIGHT_ORDER_END"),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    trace = trace_file.read_text(encoding="utf-8").splitlines() if trace_file.exists() else []
    return completed.returncode, trace


def test_preflight_simulation_runs_each_gate_once_in_order(tmp_path: Path) -> None:
    status, trace = _run_preflight_order(tmp_path)

    assert status == 0, trace
    expected = [
        "frontend-candidate-pull",
        "revision:repo/frontend",
        "backend-candidate-pull",
        "revision:repo/backend",
        "checker-image-pin",
        "live-image:frontend",
        "current-backend-topology",
        "revision:repo/frontend",
        "revision:repo/backend",
        "current-peer-evidence",
        "artifact:frontend",
        "artifact:backend",
        "effective-input-hash",
        "database-state",
        "checker-inputs",
        "compatibility-check",
        "business-preflight",
        "current-evidence-build",
    ]
    assert trace == expected
    assert all(trace.count(item) == 1 for item in expected if not item.startswith("revision:"))


@pytest.mark.parametrize(
    "fail_stage",
    [
        "frontend-candidate-pull",
        "checker-image-pin",
        "current-backend-topology",
        "current-peer-evidence",
        "artifact:frontend",
        "effective-input-hash",
        "database-state",
        "compatibility-check",
        "business-preflight",
        "current-evidence-build",
    ],
)
def test_each_premaintenance_failure_aborts_once_before_any_cutover(tmp_path: Path, fail_stage: str) -> None:
    status, trace = _run_preflight_order(tmp_path, fail_stage)

    assert status != 0, trace
    assert trace.count(fail_stage) == 1
    assert trace[-1].startswith("abort:")
    assert all(token not in trace for token in ("nginx-stop", "backup", "migration", "frontend-start", "backend-start"))


def _run_cutover(tmp_path: Path, *, mode: str, scope: str, fail_stage: str = "") -> tuple[int, list[str], str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace_file = tmp_path / "trace"
    nginx_state = tmp_path / "nginx-state"
    nginx_state.write_text("running", encoding="utf-8")
    report_file = tmp_path / "compatibility-report.json"
    report_file.write_text("{}\n", encoding="utf-8")
    next_current = tmp_path / "next-current-fingerprints.json"
    if fail_stage != "persist-facts":
        next_current.write_text("{}\n", encoding="utf-8")
    evidence_root = tmp_path / "evidence"
    old_release = evidence_root / "old-release"
    old_release.mkdir(parents=True)
    (old_release / "compatibility-report.json").write_text('{"old":true}\n', encoding="utf-8")
    (old_release / "current-fingerprints.json").write_text('{"old":true}\n', encoding="utf-8")
    (evidence_root / "current").symlink_to("old-release")
    live_state = tmp_path / "live-state"
    live_state.mkdir()
    _write_executable(bin_dir / "docker", _fake_docker())
    _write_executable(bin_dir / "curl", "#!/bin/sh\nexit 7\n")
    _write_executable(
        bin_dir / "timeout",
        """
        #!/bin/bash
        [ "$1" = --signal=KILL ] && shift
        printf 'budget:%s\n' "$1" >>"$TRACE_FILE"
        shift
        exec "$@"
        """,
    )
    _write_executable(
        bin_dir / "git",
        """
        #!/bin/bash
        [ "$1" = -C ] && shift 2
        [ "$1 $2" = "rev-parse HEAD" ] && printf '%s\n' "$DEPLOY_SOURCE_COMMIT_SHA"
        exit 0
        """,
    )
    _write_executable(
        bin_dir / "python3",
        '#!/bin/bash\nif [ "$1" = scripts/wait_for_http.py ]; then printf \'http-ready\\n\' >>"$TRACE_FILE"; [ "${FAIL_STAGE:-}" != external-readiness ]; else exec "$REAL_PYTHON" "$@"; fi\n',
    )
    current_frontend = "repo/frontend@sha256:" + "1" * 64
    current_backend = "repo/backend@sha256:" + "2" * 64
    candidate_frontend = "repo/frontend@sha256:" + "4" * 64
    candidate_backend = "repo/backend@sha256:" + "3" * 64
    for service in ("api", "celery", "celery-wms-fulfillment", "celery_beat", "flower"):
        (live_state / service).write_text(f"{current_backend}\n", encoding="utf-8")
    (live_state / "frontend").write_text(f"{current_frontend}\n", encoding="utf-8")
    deploy_path = tmp_path / "deploy"
    deploy_path.mkdir()
    staged_runtime_env = tmp_path / "runtime.env"
    staged_runtime_env.write_text("COMPOSE_PROJECT_NAME=wes_backend_test\n", encoding="utf-8")
    env = os.environ | {
        "BACKEND_IMAGE": current_backend if scope == "FRONTEND" else candidate_backend,
        "CURRENT_BACKEND_DIGEST": "sha256:" + "2" * 64,
        "CURRENT_BACKEND_IMAGE": current_backend,
        "CURRENT_FRONTEND_DIGEST": "sha256:" + "1" * 64,
        "CURRENT_FRONTEND_IMAGE": current_frontend,
        "COMPOSE_ENV_FILE": str(staged_runtime_env),
        "COMPOSE_FILE": "docker-compose.test-deploy.yml",
        "COMPOSE_PROJECT_DIR": str(tmp_path),
        "DEPLOY_COMPOSE_FILE": "docker-compose.test-deploy.yml",
        "DEPLOY_PATH": str(deploy_path),
        "DEPLOY_SOURCE_COMMIT_SHA": "a" * 40,
        "DEPLOY_ENV_FILE": ".env.test",
        "DEPLOY_SCOPE": scope,
        "EFFECTIVE_MODE": mode,
        "FAIL_STAGE": fail_stage,
        "EXPECTED_SCHEMA_HEAD": "head-new",
        "FRONTEND_IMAGE": current_frontend if scope == "BACKEND" else candidate_frontend,
        "HEALTH_CHECK_URL": "http://127.0.0.1:8001/ready",
        "NGINX_HTTP_PORT": "8080",
        "NGINX_STATE": str(nginx_state),
        "LIVE_STATE_DIR": str(live_state),
        "NEXT_CURRENT_FINGERPRINTS": str(next_current),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RELEASE_DIR": str(evidence_root / "release-test"),
        "RELEASE_EVIDENCE_ROOT": str(evidence_root),
        "CURRENT_RELEASE_EVIDENCE_DIR": str(evidence_root / "current"),
        "RELEASE_ID": "release-test",
        "REAL_PYTHON": sys.executable,
        "REPORT_FILE": str(report_file),
        "STAGED_RUNTIME_ENV_FILE": str(staged_runtime_env),
        "TRACE_FILE": str(trace_file),
        "UNSELECTED_PEER_DIGEST": ("sha256:" + "2" * 64) if scope == "FRONTEND" else ("sha256:" + "1" * 64),
        "WORKSPACE": str(tmp_path),
    }
    completed = subprocess.run(
        [
            "/bin/bash",
            "-c",
            """
compose() {
    docker compose --env-file "${COMPOSE_ENV_FILE}" --project-directory "${COMPOSE_PROJECT_DIR}" -f "${COMPOSE_FILE}" "$@"
}
discover_live_image() {
    container_id=$(compose ps -a -q "$1")
    [ -n "$container_id" ] || return 1
    docker inspect --format '{{ .Config.Image }}' "$container_id"
}
discover_live_digest() {
    image_ref=$(discover_live_image "$1") || return 1
    case "$image_ref" in
        *@sha256:[0-9a-f]*) printf '%s\n' "${image_ref##*@}" ;;
        *) return 1 ;;
    esac
}
"""
            + _marked_shell("# TEST_DEPLOY_CUTOVER_BEGIN", "# TEST_DEPLOY_CUTOVER_END"),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    trace = trace_file.read_text(encoding="utf-8").splitlines() if trace_file.exists() else []
    return completed.returncode, trace, nginx_state.read_text(encoding="utf-8"), completed.stdout + completed.stderr


def test_premaintenance_non_api_backend_peer_drift_leaves_entrypoint_untouched(tmp_path: Path) -> None:
    status, trace, nginx, _output = _run_cutover(
        tmp_path, mode="FAST", scope="FRONTEND", fail_stage="peer-drift-non-api"
    )

    assert status != 0
    assert nginx == "running"
    assert trace == ["peer-reverify:api", "peer-reverify:celery"]


def test_initial_mixed_backend_peer_aborts_before_cutover(tmp_path: Path) -> None:
    status, trace = _run_preflight_order(tmp_path, "current-backend-topology")

    assert status != 0
    assert trace[-2:] == ["current-backend-topology", "abort:current-backend-discovery"]
    assert all(token not in trace for token in ("nginx-stop", "frontend-start", "backend-start"))


@pytest.mark.parametrize(
    "fail_stage", ["backup", "migration", "migration-head", "authorization", "application-start", "readiness"]
)
def test_postmaintenance_failure_holds_nginx_closed_and_does_not_repeat_mutation(
    tmp_path: Path, fail_stage: str
) -> None:
    status, trace, nginx, output = _run_cutover(tmp_path, mode="FULL", scope="BOTH", fail_stage=fail_stage)

    assert status != 0, output
    assert nginx == "stopped"
    assert trace[-1] == "nginx-stop"
    for stage in ("backup", "migration", "authorization-apply"):
        assert trace.count(stage) <= 1
    assert "nginx-start" not in trace


def test_internal_topology_excludes_stopped_nginx_but_requires_every_other_compose_service() -> None:
    pipeline = _pipeline()
    readiness = pipeline[
        pipeline.index("verify_readiness_and_topology()") : pipeline.index("persist_success_evidence()")
    ]

    assert "runtime_compose config --services" in readiness
    assert "runtime_compose ps --status running --services" in readiness
    assert readiness.count("sed '/^nginx$/d'") == 2
    assert "expected=$(runtime_compose config --services | sed '/^nginx$/d' | LC_ALL=C sort) || return 1" in readiness
    assert (
        "running=$(runtime_compose ps --status running --services | sed '/^nginx$/d' | LC_ALL=C sort) || return 1"
        in readiness
    )
    assert "api celery celery-wms-fulfillment celery_beat flower frontend db redis" not in readiness


def test_missing_non_nginx_compose_service_fails_before_entrypoint_restore(tmp_path: Path) -> None:
    status, trace, nginx, output = _run_cutover(tmp_path, mode="FULL", scope="BOTH", fail_stage="topology-missing-db")

    assert status != 0, (trace, output)
    assert nginx == "stopped"
    assert "topology-running" in trace
    assert "nginx-start" not in trace


@pytest.mark.parametrize("fail_stage", ["topology-config-query", "topology-running-query"])
def test_topology_query_failure_cannot_pass_on_matching_captured_output(tmp_path: Path, fail_stage: str) -> None:
    status, trace, nginx, output = _run_cutover(tmp_path, mode="FULL", scope="BOTH", fail_stage=fail_stage)

    assert status != 0, (trace, output)
    assert nginx == "stopped"
    assert "nginx-start" not in trace
    assert os.readlink(tmp_path / "evidence/current") == "old-release"
    assert not (tmp_path / "evidence/release-test").exists()


def test_evidence_persist_failure_keeps_the_previous_current_pair_atomic(tmp_path: Path) -> None:
    status, trace, nginx, output = _run_cutover(tmp_path, mode="FULL", scope="BOTH", fail_stage="persist-facts")

    assert status != 0, (trace, output)
    assert nginx == "stopped"
    current = tmp_path / "evidence/current"
    assert current.is_symlink()
    assert os.readlink(current) == "old-release"
    assert (current / "compatibility-report.json").read_text(encoding="utf-8") == '{"old":true}\n'
    assert (current / "current-fingerprints.json").read_text(encoding="utf-8") == '{"old":true}\n'


@pytest.mark.parametrize(
    ("mode", "scope", "expected", "forbidden"),
    [
        ("FAST", "FRONTEND", "frontend-start", "backend-start"),
        ("FAST", "BACKEND", "backend-start", "frontend-start"),
        ("FULL", "BOTH", "migration", "never"),
    ],
)
def test_successful_fast_full_simulation_changes_only_the_required_scope(
    tmp_path: Path, mode: str, scope: str, expected: str, forbidden: str
) -> None:
    status, trace, nginx, output = _run_cutover(tmp_path, mode=mode, scope=scope)

    assert status == 0, (trace, output)
    assert nginx == "running"
    assert expected in trace
    assert forbidden not in trace
    assert trace[-1] == "http-ready"
    assert trace.index("topology-running") < trace.index("nginx-start")
    current = tmp_path / "evidence/current"
    assert current.is_symlink()
    assert os.readlink(current) == "release-test"
    if mode == "FAST":
        assert "backup" not in trace
        assert "migration" not in trace
        budgets = [int(item.removeprefix("budget:").removesuffix("s")) for item in trace if item.startswith("budget:")]
        assert budgets
        assert all(0 < seconds <= 120 for seconds in budgets)
        if scope == "BACKEND":
            assert trace.index("backend-stop") < trace.index("backend-start")
            assert (tmp_path / "live-state/frontend").read_text(
                encoding="utf-8"
            ).strip() == "repo/frontend@sha256:" + "1" * 64
        else:
            assert "backend-stop" not in trace
            for service in ("api", "celery", "celery-wms-fulfillment", "celery_beat", "flower"):
                assert (tmp_path / "live-state" / service).read_text(encoding="utf-8").strip() == (
                    "repo/backend@sha256:" + "2" * 64
                )
