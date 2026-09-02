from pathlib import Path


def _dockerfile_copy_lines() -> set[str]:
    dockerfile = Path(__file__).resolve().parents[2] / "tests" / "mock" / "Dockerfile"

    return {stripped for line in dockerfile.read_text().splitlines() if (stripped := line.strip()).startswith("COPY ")}


def test_mock_dockerfile_copies_shared_runtime_dependencies() -> None:
    copy_lines = _dockerfile_copy_lines()

    assert "COPY packages/wes_plugin_sdk/src/wes_plugin_sdk/ /app/wes_plugin_sdk/" in copy_lines
    assert "COPY src/app/wms_adapter/strict_json.py /app/src/app/wms_adapter/strict_json.py" in copy_lines
    assert "COPY src/app/transport/callback_json.py /app/src/app/transport/callback_json.py" in copy_lines
    assert "COPY src/core/uuid7.py /app/src/core/uuid7.py" in copy_lines
    assert (
        "COPY src/app/callback/contracts/runtime_events.py /app/src/app/callback/contracts/runtime_events.py"
    ) in copy_lines
    assert all("src/app/wms_integration/" not in line for line in copy_lines)
    assert all("sandbox_catalog_bridge.py" not in line for line in copy_lines)
    assert all("src/workline_runtime/" not in line for line in copy_lines)


def test_mock_dockerfile_packages_local_swagger_ui_assets() -> None:
    assert "COPY src/static/swagger-ui/ /app/src/static/swagger-ui/" in _dockerfile_copy_lines()


def test_mock_dockerfile_copies_transport_callback_openapi_dependencies() -> None:
    copy_lines = _dockerfile_copy_lines()

    assert "COPY src/app/wms_adapter/transport_openapi.py /app/src/app/wms_adapter/transport_openapi.py" in copy_lines
    assert "COPY src/app/wms_adapter/transport_wire.py /app/src/app/wms_adapter/transport_wire.py" in copy_lines
    assert "COPY src/app/transport/contracts.py /app/src/app/transport/contracts.py" in copy_lines
