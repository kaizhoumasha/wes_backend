from types import SimpleNamespace

import pytest

from deployment.plugin_composition import build_deployment_runtime


@pytest.mark.parametrize("keys", [("automatic-picking",), ("manual-picking", "automatic-picking")])
def test_automatic_installation_preserves_static_identity_and_runtime_ports(keys):
    runtime = build_deployment_runtime(
        enabled_plugin_keys=keys,
        session_factory=object(),
        transport_runtime=SimpleNamespace(service=object(), position_projection_service=object(), client=object()),
        device_command_service=object(),
    )
    assert {p.plugin_key for p in runtime.plugins} == set(keys)
    automatic = next(p for p in runtime.plugins if p.plugin_key == "automatic-picking")
    assert automatic.picking_task_prepare_policy is not None
    assert automatic.picking_task_plan_applied_handler is not None
    assert automatic.picking_task_batch_driver is not None
    assert automatic.transport_outcome_publisher is not None
    assert automatic.runtime_binding.business_wms_operations == (
        "outbound.bin.inbound_batch@v1",
        "outbound.bin.work_plan@v1",
    )
