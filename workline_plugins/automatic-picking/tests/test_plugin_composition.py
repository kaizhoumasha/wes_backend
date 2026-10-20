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
        "outbound.bin.return_batch@v1",
        "outbound.rack.departure_decide@v1",
        "workline.return_buffer.drain_rack_decide@v1",
    )
    assert automatic.picking_task_batch_driver.return_batch_flow is not None
    assert automatic.picking_task_batch_driver.drain_flow is not None
    assert (
        automatic.runtime_binding.business_evidence_consumer.return_batch_flow
        is automatic.picking_task_batch_driver.return_batch_flow
    )
    assert automatic.picking_task_batch_driver.rack_motion_flow is not None
    assert automatic.picking_task_batch_driver.rack_departure_flow is not None
    assert automatic.picking_task_batch_driver.source_flow is None
    assert automatic.picking_task_batch_driver.material_flow is None
    assert automatic.picking_task_batch_driver.target_flow is None
    assert automatic.runtime_binding.handlers == ()
    assert automatic.runtime_binding.fact_factory is None
    assert automatic.runtime_binding.decision_application is None


def test_material_runtime_requires_explicit_injection():
    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.handlers.material_flow import material_decided, material_scanned
    from automatic_picking.handlers.placement import material_put, material_reported, placement_pending

    material = object()
    plugin = build_plugin(scan_flow=object(), batch_driver=object(), works=object(), material_flow=material)
    assert plugin.runtime_binding.handlers == (
        material_scanned,
        material_decided,
        material_put,
        material_reported,
        placement_pending,
    )
    assert plugin.runtime_binding.fact_factory is material
    assert plugin.runtime_binding.decision_application is material
