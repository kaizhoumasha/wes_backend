from pathlib import Path


def _dockerfile_copy_lines() -> set[str]:
    dockerfile = Path(__file__).resolve().parents[2] / "tests" / "mock" / "Dockerfile"

    return {stripped for line in dockerfile.read_text().splitlines() if (stripped := line.strip()).startswith("COPY ")}


def test_mock_dockerfile_copies_shared_runtime_dependencies() -> None:
    copy_lines = _dockerfile_copy_lines()

    assert (
        "COPY src/app/runtime/orchestration/sandbox_catalog_bridge.py "
        "/app/src/app/runtime/orchestration/sandbox_catalog_bridge.py"
    ) in copy_lines
    assert (
        "COPY src/app/callback/contracts/runtime_events.py /app/src/app/callback/contracts/runtime_events.py"
    ) in copy_lines
    assert all("src/workline_runtime/" not in line for line in copy_lines)
