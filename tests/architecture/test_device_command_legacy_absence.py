"""DeviceCommand 原子切换后旧设备执行闭包必须物理缺席。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

RETIRED_PATHS = (
    "src/app/device/services/runtime_state_policy.py",
    "src/app/runtime/orchestration/device_runtime_projection.py",
    "src/app/runtime/orchestration/repositories/device_runtime_projection_repository.py",
    "src/app/runtime/orchestration/services/device_command_gateway.py",
    "src/app/runtime/orchestration/services/device_command_lease.py",
    "src/app/runtime/orchestration/services/device_runtime_projection_writer_service.py",
    "src/app/runtime/system_capabilities/device/" + "device_command" + "_write",
)
RETIRED_PRODUCTION_TOKENS = (
    "DeviceCommand" + "Gateway",
    "device_command_gateway",
    "device.device_command" + "_write",
    "RuntimeIntentKind.COMMAND",
    "RuntimeIntentKind.DEVICE_EVENT",
    "SystemOutboxDispatchType." + "DEVICE_COMMAND",
    "SystemOutboxTargetType." + "DEVICE",
)


def test_retired_device_execution_owners_are_absent() -> None:
    violations = []
    for path in RETIRED_PATHS:
        target = REPO_ROOT / path
        if target.is_dir():
            if any(target.rglob("*.py")):
                violations.append(path)
        elif target.exists():
            violations.append(path)
    assert violations == []


def test_production_has_no_retired_device_execution_reference() -> None:
    violations: list[str] = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for token in RETIRED_PRODUCTION_TOKENS:
            if token in content:
                violations.append(f"{path.relative_to(REPO_ROOT)}: {token}")
    assert violations == []


def test_static_device_model_has_no_runtime_or_transport_fields() -> None:
    content = (REPO_ROOT / "src/app/device/models/device.py").read_text(encoding="utf-8")
    for token in (
        "current_command_id",
        "callback_path",
        "auth_token",
        "DeviceProtocol",
        "vendor_type",
        "device_status",
        "last_heartbeat_at",
    ):
        assert token not in content


def test_runtime_inbox_has_no_device_command_or_evidence_ingress() -> None:
    inbox_model_path = REPO_ROOT / "src/app/runtime/orchestration/runtime_inbox.py"
    inbox_service_path = REPO_ROOT / "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py"
    input_normalizer = (REPO_ROOT / "src/app/runtime/normalization/normalizers/input_normalizer.py").read_text(
        encoding="utf-8"
    )

    assert not inbox_model_path.exists()
    assert not inbox_service_path.exists()
    assert "NormalizedCommandResult" not in input_normalizer
    assert 'kind == "COMMAND_RESULT"' not in input_normalizer
