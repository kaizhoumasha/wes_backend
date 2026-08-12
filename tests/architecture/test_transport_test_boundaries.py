"""Transport 基础能力测试不得直接依赖业务、设备或插件实现。"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSPORT_TEST_DIRECTORIES = (
    REPO_ROOT / "tests/runtime/transport",
    REPO_ROOT / "tests/contracts/wms_adapter",
    REPO_ROOT / "tests/integration/transport",
)
TRANSPORT_TEST_FILES = tuple(
    REPO_ROOT / relative_path
    for relative_path in (
        "tests/api/test_wms_transport_events.py",
        "tests/core/test_outbox_dispatch_target_gateway.py",
        "tests/core/test_uuid7.py",
        "tests/e2e/transport/test_transport_production_wiring.py",
        "tests/integration/test_transport_broker_harness_cleanup.py",
        "tests/integration/test_transport_fastapi_lifespan.py",
        "tests/integration/test_transport_fulfillment_queue.py",
        "tests/integration/test_celery_async_runtime.py",
        "tests/integration/test_celery_async_runtime_postgresql.py",
        "tests/integration/test_celery_prefork_harness_cleanup.py",
        "tests/integration/test_wms_deployment_attestation.py",
        "tests/deployment/test_celery_task_runtime_contract.py",
        "tests/deployment/test_wms_effect_lane_dispatch.py",
        "tests/deployment/test_wms_transport_startup.py",
        "tests/support/transport_broker.py",
    )
)
FORBIDDEN_IMPORT_PREFIXES = (
    "src.app.device",
    "src.app.workline",
    "src.app.picking",
    "workline_plugins",
)


def test_transport_test_asset_set_has_exact_frozen_core_owners() -> None:
    frozen_core_owners = {
        REPO_ROOT / "tests/core/test_uuid7.py",
        REPO_ROOT / "tests/core/test_outbox_dispatch_target_gateway.py",
    }
    discovered_core_owners = {
        candidate for candidate in TRANSPORT_TEST_FILES if candidate.relative_to(REPO_ROOT).parent == Path("tests/core")
    }

    assert discovered_core_owners == frozen_core_owners


def test_transport_tests_do_not_directly_import_business_device_or_plugin_implementations() -> None:
    violations: list[str] = []
    test_paths = list(TRANSPORT_TEST_FILES)

    for root in TRANSPORT_TEST_DIRECTORIES:
        assert root.is_dir(), root
        test_paths.extend(sorted(root.glob("test_*.py")))
    assert all(path.is_file() for path in TRANSPORT_TEST_FILES)

    for path in sorted(set(test_paths)):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...]
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            else:
                continue
            for module in modules:
                if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {module}")

    assert violations == []
