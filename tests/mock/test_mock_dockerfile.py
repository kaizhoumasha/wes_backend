from pathlib import Path


def _dockerfile_copy_lines() -> set[str]:
    dockerfile = Path(__file__).resolve().parents[2] / "tests" / "mock" / "Dockerfile"

    return {stripped for line in dockerfile.read_text().splitlines() if (stripped := line.strip()).startswith("COPY ")}


def test_mock_dockerfile_copies_shared_runtime_dependencies() -> None:
    copy_lines = _dockerfile_copy_lines()

    assert "COPY src/workline_runtime/sandbox_catalog.py /app/src/workline_runtime/sandbox_catalog.py" in copy_lines
    assert "COPY src/workline_runtime/runtime_events.py /app/src/workline_runtime/runtime_events.py" in copy_lines
