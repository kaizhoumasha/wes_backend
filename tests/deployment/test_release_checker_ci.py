from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "tools/release_checker/Dockerfile"
JENKINSFILE = REPO_ROOT / "Jenkinsfile.release-checker-ci"


def test_checker_image_pins_python_oasdiff_and_verified_linux_archives() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "python:3.13" in dockerfile
    assert (
        "python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d"
        in dockerfile
    )
    assert "OASDIFF_VERSION=1.28.0" in dockerfile
    assert "e0ef076f2cf953d922addc04be9c3851cf3ec18f7678d2b94d44cea23dca51b5" in dockerfile
    assert "cb15a381472321ac602cc252e65018d03feba7e6449a0854e1181680444d4051" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "oasdiff --version" in dockerfile
    assert 'test "$(/oasdiff --version)" = "oasdiff version ${OASDIFF_VERSION}"' in dockerfile


def test_production_checker_image_contains_only_checker_and_pinned_binary() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    production = dockerfile.split("AS production", maxsplit=1)[1]

    assert "COPY --from=oasdiff-download /oasdiff /usr/local/bin/oasdiff" in dockerfile
    assert "ENV PYTHONPATH=/opt" in dockerfile
    assert "COPY release_checker.py /opt/tools/release_checker/release_checker.py" in dockerfile
    assert "COPY . ." not in dockerfile
    assert "src/" not in dockerfile
    assert "wes_frontend" not in dockerfile
    assert "requirements" not in production
    assert "pytest" not in production
    assert 'ENTRYPOINT ["python", "/opt/tools/release_checker/release_checker.py"]' in production


def test_checker_ci_is_scoped_to_checker_inputs_and_owns_its_tests() -> None:
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    for path in (
        "tools/release_checker/**",
        "Jenkinsfile.release-checker-ci",
        "tests/deployment/test_release_checker_ci.py",
    ):
        assert f'changeset "{path}"' in pipeline
    assert "--target testing" in pipeline
    assert "/opt/tools/release_checker/tests" in pipeline
    assert "tests/deployment/test_release_checker_ci.py" in pipeline
    assert "--target production" in pipeline
    assert "tools/release_checker" in pipeline


def test_checker_ci_pushes_immutable_and_channel_tags_and_records_digest() -> None:
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    assert "CHECKER_IMAGE_IMMUTABLE" in pipeline
    assert "CHECKER_IMAGE_CHANNEL" in pipeline
    assert 'docker push "${CHECKER_IMAGE_IMMUTABLE}"' in pipeline
    assert 'docker push "${CHECKER_IMAGE_CHANNEL}"' in pipeline
    assert "docker inspect --format='{{index .RepoDigests 0}}'" in pipeline
    assert "reports/release-checker-image-digest.txt" in pipeline
    assert "archiveArtifacts" in pipeline


def test_checker_ci_never_calls_producers_or_deployment_jobs() -> None:
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    forbidden = (
        "Jenkinsfile.backend-ci",
        "Jenkinsfile.test-deploy",
        "wes_frontend",
        "build job:",
    )
    assert all(token not in pipeline for token in forbidden)
