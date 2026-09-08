import shutil
import subprocess
import sys
from pathlib import Path


def _dockerfile_copy_lines() -> set[str]:
    dockerfile = Path(__file__).resolve().parents[2] / "tests" / "mock" / "Dockerfile"

    return {stripped for line in dockerfile.read_text().splitlines() if (stripped := line.strip()).startswith("COPY ")}


def test_mock_dockerfile_copies_shared_runtime_dependencies() -> None:
    copy_lines = _dockerfile_copy_lines()

    assert "COPY src/wes_plugin_sdk/src/wes_plugin_sdk/ /app/wes_plugin_sdk/" in copy_lines
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


def test_packaged_mock_imports_without_host_runtime_configuration(tmp_path: Path) -> None:
    """按 Docker COPY 清单验证真实 import 闭包，而非只核对固定文件名。"""
    root = Path(__file__).resolve().parents[2]
    for line in _dockerfile_copy_lines():
        _, source, destination = line.split()
        source_path = root / source
        target = tmp_path / destination.removeprefix("/app/")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_path.is_dir():
            shutil.copytree(source_path, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copyfile(source_path, target)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import tests.mock.wms_mock_server; import tests.mock.ecs_mock_server; "
            "assert 'src.core.conf' not in sys.modules",
            str(tmp_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
