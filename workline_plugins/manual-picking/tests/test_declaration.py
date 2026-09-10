"""人工拣料声明通过部署入口提供可装配资源。"""

from types import SimpleNamespace

from deployment.plugin_composition import build_deployment_runtime
from src.app.workline.models.workline import LineType


def test_manual_picking_can_be_selected_without_business_implementation():
    runtime = build_deployment_runtime(
        enabled_plugin_keys=("manual-picking",),
        session_factory=object(),
        transport_runtime=SimpleNamespace(service=object(), position_projection_service=object(), client=object()),
        device_command_service=object(),
    )
    line = SimpleNamespace(line_type=LineType.MANUAL)
    summaries = runtime.workline_configuration_service._summarize_plugins(line)
    assert len(summaries) == 1
    summary = summaries[0].model_dump(mode="json")
    assert summary["plugin_key"] == "manual-picking"
    assert summary["display_name"] == "人工拣料"
    assert summary["compatible"] is True
    assert [role["role_key"] for role in summary["device_roles"]] == ["SCAN1", "SCAN2", "SCAN3", "SCAN4"]
    assert [slot["slot_key"] for slot in summary["position_slots"]] == [
        "FIVE_RACK",
        "RETURN_RACK",
        "TRANSFER_RACK",
        "INLET",
        "OUTLET",
    ]
    assert runtime.plugins[0].runtime_binding is None
    assert runtime.wms_recovery_event_handler is None


def test_deployment_can_load_manual_declaration_without_runtime_imports():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from deployment.plugin_definitions import load_plugin_definitions
from manual_picking.definition import DEFINITION
assert load_plugin_definitions(("manual-picking",)) == (DEFINITION,)
assert not any(name == "src.app" or name.startswith(("src.app.", "sqlalchemy", "celery", "fastapi")) for name in sys.modules)
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
