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


def test_build_resolves_external_http_source_device_from_session_rack_exchange_resume_code():
    session = SimpleNamespace(
        id=38,
        run_mode="SIMULATION",
        context_json={
            "rack_exchange": {
                "status": "REQUESTED",
                "resume_source_device_code": "PIPELINE02",
                "resume_source_device_role": "STALE_ROLE",
            }
        },
        trace_id="trace-smt-full",
        workline_id=45,
        plugin_key="smt_classifier",
        contract_version="1.0",
        last_request_id=None,
    )
    workline = SimpleNamespace(
        id=45,
        line_code="WL-CONVEYOR-02",
        line_name="SMT 粗分右线",
        line_type="CONVEYOR",
        plugin_key="smt_classifier",
        contract_version="1.0",
        run_mode="SIMULATION",
        config={},
        runtime_config_json={},
        diagnostic_profile={},
    )
    conveyor = SimpleNamespace(
        id=40,
        device_code="PIPELINE02",
        device_name="右线输送线",
        device_role="CONVEYOR",
        role_index=0,
        upstream_device_id=39,
        work_line_id=45,
        protocol="HTTP",
        host="127.0.0.1",
        port=9002,
        timeout=30,
        callback_path="/callback",
        maintenance_mode=False,
        capabilities_json={},
        diagnostic_profile={},
    )
    output_arm = SimpleNamespace(
        id=41,
        device_code="ARM04",
        device_name="右线出料臂",
        device_role="OUTPUT_ARM",
        role_index=0,
        upstream_device_id=40,
        work_line_id=45,
        protocol="HTTP",
        host="127.0.0.1",
        port=9003,
        timeout=30,
        callback_path="/callback",
        maintenance_mode=False,
        capabilities_json={},
        diagnostic_profile={},
    )
    inbox = SimpleNamespace(
        id=145,
        kind="EXTERNAL_HTTP",
        payload_json={
            "callback_type": "WMS_RACK_ARRIVED",
            "dispatch_key": "external:smt_classifier:trace-smt-full:RACK_EXCHANGE_AND_SUPPLY",
            "active_bin_rack": {"rack_id": "RACK-NEXT-01", "cells": []},
        },
        trace_id="trace-smt-full",
        source_message_id=None,
        event_id=None,
        causation_id=None,
        workline_id=45,
        session_id=38,
        device_id=None,
        command_id=None,
    )
    ctx = PluginContextBuilder().build(
        session=session,
        workline=workline,
        devices_by_role={"CONVEYOR": [conveyor], "OUTPUT_ARM": [output_arm]},
        services=WorklineRuntimeServices(),
        trace_id="trace-smt-full",
        inbox=inbox,
    )
    assert ctx.source_device.device_code == "PIPELINE02"
    assert ctx.source_device_role == "CONVEYOR"


def test_build_resolves_external_http_source_device_from_session_rack_supply_resume_code():
    session = SimpleNamespace(
        id=39,
        run_mode="AUTO",
        context_json={
            "rack_supply": {
                "status": "REQUESTED",
                "resume_source_device_code": "PIPELINE01",
                "resume_source_device_role": "CONVEYOR",
            }
        },
        trace_id="trace-smt-supply",
        workline_id=30,
        plugin_key="smt_classifier",
        contract_version="1.0",
        last_request_id=None,
    )
    workline = SimpleNamespace(
        id=30,
        line_code="WL-CONVEYOR-01",
        line_name="SMT 粗分左线",
        line_type="CONVEYOR",
        plugin_key="smt_classifier",
        contract_version="1.0",
        run_mode="AUTO",
        config={},
        runtime_config_json={},
        diagnostic_profile={},
    )
    conveyor = SimpleNamespace(
        id=19,
        device_code="PIPELINE01",
        device_name="左线输送线",
        device_role="CONVEYOR",
        role_index=0,
        upstream_device_id=18,
        work_line_id=30,
        protocol="HTTP",
        host="127.0.0.1",
        port=8005,
        timeout=30,
        callback_path="/callback",
        maintenance_mode=False,
        capabilities_json={},
        diagnostic_profile={},
    )
    output_arm = SimpleNamespace(
        id=20,
        device_code="ARM02",
        device_name="左线出料臂",
        device_role="OUTPUT_ARM",
        role_index=0,
        upstream_device_id=19,
        work_line_id=30,
        protocol="HTTP",
        host="127.0.0.1",
        port=8007,
        timeout=30,
        callback_path="/callback",
        maintenance_mode=False,
        capabilities_json={},
        diagnostic_profile={},
    )
    inbox = SimpleNamespace(
        id=146,
        kind="EXTERNAL_HTTP",
        payload_json={
            "callback_type": "WMS_RACK_ARRIVED",
            "dispatch_key": "external:smt_classifier:trace-smt-supply:RACK_SUPPLY",
            "active_bin_rack": {"rack_id": "RACK-NEXT-02", "cells": []},
        },
        trace_id="trace-smt-supply",
        source_message_id=None,
        event_id=None,
        causation_id=None,
        workline_id=30,
        session_id=39,
        device_id=None,
        command_id=None,
    )
    ctx = PluginContextBuilder().build(
        session=session,
        workline=workline,
        devices_by_role={"CONVEYOR": [conveyor], "OUTPUT_ARM": [output_arm]},
        services=WorklineRuntimeServices(),
        trace_id="trace-smt-supply",
        inbox=inbox,
    )
    assert ctx.source_device.device_code == "PIPELINE01"
    assert ctx.source_device_role == "CONVEYOR"
