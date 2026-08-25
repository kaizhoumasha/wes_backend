import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path
from shutil import which

import pytest

from scripts.export_release_provider import (
    ReleaseProviderExportError,
    validate_release_provider_artifacts,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _stage_body(jenkinsfile: str, stage: str, next_stage: str) -> str:
    return jenkinsfile.split(f"stage('{stage}')", maxsplit=1)[1].split(f"stage('{next_stage}')", maxsplit=1)[0]


def _write_provider_artifacts(root: Path) -> dict[str, str]:
    openapi = b'{"openapi":"3.1.0"}\n'
    permissions = b'{"kind":"wes.release.provided-permissions.v1","permissions":[]}\n'
    fingerprints = {
        "kind": "wes.release.backend-fingerprints.v1",
        "provider_openapi_sha256": hashlib.sha256(openapi).hexdigest(),
        "provided_permissions_sha256": hashlib.sha256(permissions).hexdigest(),
        "migration_tree_sha256": "1" * 64,
        "dependencies_sha256": "2" * 64,
        "recipe_sha256": "3" * 64,
        "expected_schema_head": "a" * 12,
    }
    root.mkdir()
    (root / "provider-openapi.json").write_bytes(openapi)
    (root / "provided-permissions.json").write_bytes(permissions)
    (root / "provider-fingerprints.json").write_text(
        json.dumps(fingerprints, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return fingerprints


def test_provider_image_input_validation_rejects_malformed_and_label_mismatch(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "provider"
    fingerprints = _write_provider_artifacts(artifact_dir)

    assert validate_release_provider_artifacts(artifact_dir, expected=fingerprints) == fingerprints

    wrong = {**fingerprints, "provider_openapi_sha256": "f" * 64}
    with pytest.raises(ReleaseProviderExportError, match="镜像 label 输入与 exporter 指纹不一致"):
        validate_release_provider_artifacts(artifact_dir, expected=wrong)

    (artifact_dir / "provider-openapi.json").write_text("{", encoding="utf-8")
    with pytest.raises((ReleaseProviderExportError, json.JSONDecodeError)):
        validate_release_provider_artifacts(artifact_dir, expected=fingerprints)


def test_backend_runtime_image_embeds_only_provider_release_artifacts_and_exact_labels() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    validation = dockerfile.split("FROM testing AS provider-artifact-validation\n", maxsplit=1)[1].split(
        "FROM base AS production-source\n", maxsplit=1
    )[0]
    production_source = dockerfile.split("FROM base AS production-source\n", maxsplit=1)[1].split(
        "FROM base AS production\n", maxsplit=1
    )[0]
    production = dockerfile.split("FROM base AS production\n", maxsplit=1)[1]

    assert "AS production-source" in dockerfile
    assert "COPY . /app" in production_source
    assert "/app/tools/release_checker" in production_source
    assert "rm -rf /app/reports/release-provider" in production_source
    assert "rm -rf /app/.agents" in production_source
    assert "COPY --from=production-source /app /app" in production
    assert "COPY . ." not in production
    assert "COPY reports/release-provider /tmp/wes-release-provider" in validation
    assert "validate_release_provider_artifacts" in validation
    assert "COPY --from=provider-artifact-validation /validated /opt/wes/release" in production
    assert "/validated/provider-openapi.json" in validation
    assert "/validated/provided-permissions.json" in validation
    assert "/opt/wes/release" in production
    assert "/opt/wes/release/provider-fingerprints.json" not in production
    for label in (
        "org.wes.release.provider-openapi.sha256",
        "org.wes.release.provided-permissions.sha256",
        "org.wes.release.migration-tree.sha256",
        "org.wes.release.backend-dependencies.sha256",
        "org.wes.release.backend-recipe.sha256",
        "org.wes.release.expected-schema-head",
    ):
        assert label in production
    assert 'org.opencontainers.image.revision="${WES_VCS_REVISION}"' in production
    assert 'com.zontec.wes.source-manifest="${WES_SOURCE_TREE}"' in production
    assert "org.wes.release.consumer-openapi" not in production
    assert "org.wes.release.required-operations" not in production
    assert "org.wes.release.required-permissions" not in production
    assert 'revision=os.environ["WES_VCS_REVISION"]' in validation
    assert 'source_tree=os.environ["WES_SOURCE_TREE"]' in validation
    assert "/app/tools/release_checker" not in production
    assert "/app/reports/release-provider" not in production


@pytest.mark.parametrize(
    ("revision", "source_tree"),
    [
        (None, "b" * 40),
        ("a" * 40, None),
        ("", "b" * 40),
        ("a" * 39, "b" * 40),
        ("A" * 40, "b" * 40),
        ("a" * 39 + "\n", "b" * 40),
        ("$(id)" + "a" * 35, "b" * 40),
        ("a" * 40, "b" * 41),
    ],
)
def test_provider_image_boundary_rejects_missing_or_malformed_git_identity(
    revision: str | None,
    source_tree: str | None,
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "provider"
    fingerprints = _write_provider_artifacts(artifact_dir)

    with pytest.raises(ReleaseProviderExportError, match="镜像 Git 身份"):
        validate_release_provider_artifacts(
            artifact_dir,
            expected=fingerprints,
            revision=revision,
            source_tree=source_tree,
        )


def test_backend_docker_context_keeps_only_the_provider_export_for_runtime_build() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "reports/" in dockerignore
    assert "!reports/release-provider/" in dockerignore
    assert "!reports/release-provider/**" in dockerignore
    assert "reports/release-provider/provider-fingerprints.json" not in dockerignore
    assert "tools/release_checker/" not in dockerignore
    assert ".superpowers/" in dockerignore


def test_backend_runtime_image_layers_exclude_ci_only_release_files(tmp_path: Path) -> None:
    image = os.environ.get("WES_BACKEND_RUNTIME_IMAGE")
    if image is None:
        pytest.skip("set WES_BACKEND_RUNTIME_IMAGE to inspect a built production image")

    docker = which("docker")
    assert docker is not None
    archive = tmp_path / "backend-image.tar"
    subprocess.run(
        [docker, "image", "save", "--output", str(archive), image],
        check=True,
    )

    forbidden = {
        "app/.agents",
        "app/.superpowers",
        "app/reports/release-provider",
        "app/tools/release_checker",
    }
    leaked: set[str] = set()
    with tarfile.open(archive) as image_tar:
        manifest_file = image_tar.extractfile("manifest.json")
        assert manifest_file is not None
        manifest = json.load(manifest_file)
        assert len(manifest) == 1
        for layer_name in manifest[0]["Layers"]:
            layer_file = image_tar.extractfile(layer_name)
            assert layer_file is not None
            with tarfile.open(fileobj=layer_file) as layer_tar:
                for member in layer_tar.getmembers():
                    path = member.name.removeprefix("./").rstrip("/")
                    if path.endswith("fingerprints.json") or any(
                        path == target or path.startswith(f"{target}/") for target in forbidden
                    ):
                        leaked.add(path)

    assert leaked == set()


def test_backend_ci_exports_provider_once_before_build_and_uses_exporter_fingerprints() -> None:
    jenkinsfile = (REPO_ROOT / "Jenkinsfile.backend-ci").read_text(encoding="utf-8")
    export_body = _stage_body(jenkinsfile, "Export Release Provider", "Build Runtime Image")
    build_body = _stage_body(jenkinsfile, "Build Runtime Image", "Push Runtime Image")

    export_command = "uv run --no-sync python scripts/export_release_provider.py --out-dir reports/release-provider"
    assert jenkinsfile.count(export_command) == 1
    assert jenkinsfile.index("stage('Export Release Provider')") < jenkinsfile.index("stage('Build Runtime Image')")
    assert "validate_release_provider_artifacts" in export_body
    assert "archiveArtifacts" in export_body
    for argument in (
        "WES_PROVIDER_OPENAPI_SHA256",
        "WES_PROVIDED_PERMISSIONS_SHA256",
        "WES_MIGRATION_TREE_SHA256",
        "WES_BACKEND_DEPENDENCIES_SHA256",
        "WES_BACKEND_RECIPE_SHA256",
        "WES_EXPECTED_SCHEMA_HEAD",
    ):
        assert f'--build-arg "{argument}=${{{argument}}}"' in build_body
    assert "sha256sum" not in export_body
    assert "create_app" not in export_body
    assert "permission_scanner" not in export_body
    assert "provider-build.env" not in jenkinsfile
    assert ". reports/" not in jenkinsfile
    assert "returnStdout: true" in export_body
    assert "split('\\n', -1)" in export_body
    assert "^[0-9a-f]{64}$" in export_body
    assert "^[0-9a-f]{12}$" in export_body


def test_backend_producer_publishes_full_commit_and_develop_channel_without_deploying() -> None:
    jenkinsfile = (REPO_ROOT / "Jenkinsfile.backend-ci").read_text(encoding="utf-8")
    checkout_body = _stage_body(jenkinsfile, "Checkout Source", "Build CI Image")
    push_body = jenkinsfile.split("stage('Push Runtime Image')", maxsplit=1)[1].split("\n    post {", maxsplit=1)[0]

    assert 'env.RUNTIME_IMAGE_COMMIT = "${env.IMAGE_REPO}:${fullCommit}"' in checkout_body
    assert 'env.RUNTIME_IMAGE_CHANNEL = "${env.IMAGE_REPO}:develop"' in checkout_body
    assert "env.CI_EVENT_TYPE == 'PUSH'" in push_body
    assert "env.CI_IS_MERGE_REQUEST != 'true'" in push_body
    assert "env.CI_SOURCE_BRANCH == 'develop'" in push_body
    assert "env.CI_RELEASE_GATE_READY == 'true'" in push_body
    assert "stage('Trigger Test Deploy')" not in jenkinsfile
    assert "FRONTEND_IMAGE" not in push_body
    assert "FRONTEND_COMMIT" not in jenkinsfile
