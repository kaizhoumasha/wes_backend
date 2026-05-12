"""PluginContext runtime facts 测试。"""

from types import SimpleNamespace

from src.workline_runtime.plugin_context import PluginContextBuilder
from src.workline_runtime.services import WorklineRuntimeServices


def test_build_resolves_source_device_from_inbox_device_code():
    """Builder 应从 inbox payload 的 device_code 解析 source device。"""

    session = SimpleNamespace(
        id=1,
        run_mode="AUTO",
        context_json={},
        trace_id=None,
        workline_id=1,
        plugin_key="test-plugin",
        contract_version="1.0",
        last_request_id=None,
    )
    workline = SimpleNamespace(
        id=1,
        line_code="WL-001",
        line_name="测试线",
        line_type="WORKLINE",
        plugin_key="test-plugin",
        contract_version="1.0",
        run_mode="AUTO",
        config={},
        runtime_config_json={},
        diagnostic_profile={},
    )
    source_device = SimpleNamespace(
        id=10,
        device_code="ARM01",
        device_name="上料机械臂",
        device_role="INPUT_ARM",
        role_index=0,
        upstream_device_id=None,
        work_line_id=1,
        protocol="HTTP",
        host="127.0.0.1",
        port=9001,
        timeout=30,
        callback_path="/callback",
        maintenance_mode=False,
        capabilities_json={},
        diagnostic_profile={},
    )
    devices_by_role = {
        "INPUT_ARM": [source_device],
        "OUTPUT_ARM": [
            SimpleNamespace(
                id=11,
                device_code="ARM02",
                device_role="OUTPUT_ARM",
                role_index=0,
                upstream_device_id=None,
                work_line_id=1,
            )
        ],
    }
    inbox = SimpleNamespace(
        id=100,
        kind="DEVICE_EVENT",
        payload_json={"device_code": "ARM01", "event_type": "MATERIAL_ARRIVED"},
        trace_id="trace-runtime-facts",
        source_message_id=None,
        event_id=None,
        causation_id=None,
        workline_id=1,
        session_id=1,
        device_id=10,
        command_id=None,
    )

    ctx = PluginContextBuilder().build(
        session=session,
        workline=workline,
        devices_by_role=devices_by_role,
        services=WorklineRuntimeServices(),
        trace_id="trace-runtime-facts",
        inbox=inbox,
    )

    assert ctx.source_device.device_code == "ARM01"
    assert ctx.source_device_role == "INPUT_ARM"
