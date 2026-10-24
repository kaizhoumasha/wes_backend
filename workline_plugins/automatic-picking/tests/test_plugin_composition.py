from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    assert "outbound.return_rack.task_completed@v1" in automatic.runtime_binding.business_wms_operations
    assert automatic.picking_task_batch_driver.return_batch_flow is not None
    assert automatic.picking_task_batch_driver.drain_flow is not None
    assert (
        automatic.runtime_binding.business_evidence_consumer.return_batch_flow
        is automatic.picking_task_batch_driver.return_batch_flow
    )
    assert automatic.picking_task_batch_driver.rack_motion_flow is not None
    assert automatic.picking_task_batch_driver.rack_departure_flow is not None
    assert automatic.picking_task_batch_driver.source_flow is not None
    assert automatic.picking_task_batch_driver.material_flow is not None
    assert automatic.picking_task_batch_driver.target_flow is not None
    assert automatic.runtime_binding.handlers
    assert automatic.runtime_binding.fact_factory is automatic.picking_task_batch_driver.material_flow
    assert automatic.runtime_binding.decision_application is automatic.picking_task_batch_driver.material_flow
    assert automatic.picking_task_batch_driver.direct_pick_flow is None
    assert automatic.picking_task_batch_driver.material_flow.scan_reader.source_commands is None


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


@pytest.mark.parametrize("keys", [("automatic-picking",), ("manual-picking", "automatic-picking")])
def test_prepared_full_runtime_shares_one_plugin_across_all_existing_entrypoints(keys):
    from automatic_picking.application.picking_device_ports import PickingArmBindingReader, PickingSourceReader

    association = SimpleNamespace(read_in_session=AsyncMock())
    runtime = build_deployment_runtime(
        enabled_plugin_keys=keys,
        session_factory=object(),
        transport_runtime=SimpleNamespace(service=object(), position_projection_service=object(), client=object()),
        device_command_service=object(),
        automatic_picking_source_commands=association,
    )
    plugin = next(p for p in runtime.plugins if p.plugin_key == "automatic-picking")
    batch = plugin.picking_task_batch_driver
    scan = plugin.runtime_binding.business_evidence_consumer
    material = batch.material_flow
    identity = (plugin.plugin_key, plugin.plugin_version)
    assert batch.source_flow is scan.source_flow
    assert material is scan.material_flow is plugin.runtime_binding.fact_factory
    assert material is plugin.runtime_binding.decision_application
    assert material.scan_reader.source_commands is association
    assert isinstance(batch.source_flow.repository.physical_reader, PickingSourceReader)
    assert isinstance(batch.source_flow.request_factory.__self__.bindings, PickingArmBindingReader)
    assert batch.target_flow.rotation_flow.position_reader is None  # 只限制新旋转，原动作仍由既有 flow 恢复。
    assert batch.target_flow.replacement_flow is not None
    assert batch.direct_pick_flow is None
    assert batch.return_rack_flow is not None and batch.final_target_flow is not None
    activation = runtime.picking_task_plan_activation_service
    assert activation._batch_drivers[identity] is batch
    assert activation._completion_drivers[identity] is plugin.picking_task_completion_driver
    assert batch.completion_flow is plugin.picking_task_completion_driver
    assert batch.completion_flow.first_plan_timeout_ms is None
    assert runtime.execution.fact_processor._plugins.resolve_business_evidence_consumer(*identity) is scan
    assert runtime.execution.fact_processor._plugins.resolve_fact_factory(*identity) is material
    assert runtime.transport_outcome_publisher._plugins is runtime.plugins


@pytest.mark.parametrize(
    "keys,association", [((), SimpleNamespace(read_in_session=AsyncMock())), (("automatic-picking",), object())]
)
def test_full_runtime_dependency_must_be_explicit_and_installed(keys, association):
    with pytest.raises(ValueError, match="automatic picking"):
        build_deployment_runtime(
            enabled_plugin_keys=keys,
            session_factory=object(),
            transport_runtime=SimpleNamespace(service=object(), position_projection_service=object(), client=object()),
            device_command_service=object(),
            automatic_picking_source_commands=association,
        )


def test_explicit_first_plan_timeout_reaches_same_owner_without_device_defaults():
    kwargs = {
        "enabled_plugin_keys": ("automatic-picking",),
        "session_factory": object(),
        "transport_runtime": SimpleNamespace(service=object(), position_projection_service=object(), client=object()),
        "device_command_service": object(),
        "automatic_picking_first_plan_timeout_ms": 1234,
    }
    runtime = build_deployment_runtime(**kwargs)
    plugin = runtime.plugins[0]
    assert plugin.picking_task_completion_driver.first_plan_timeout_ms == 1234
    assert plugin.picking_task_completion_driver is plugin.picking_task_batch_driver.completion_flow


def test_return_position_adapter_is_explicit_and_does_not_gate_normal_picking():
    request_factory = AsyncMock()
    runtime = build_deployment_runtime(
        enabled_plugin_keys=("automatic-picking",),
        session_factory=object(),
        transport_runtime=SimpleNamespace(service=object(), position_projection_service=object(), client=object()),
        device_command_service=object(),
        automatic_picking_direct_pick_requests=request_factory,
    )
    batch = runtime.plugins[0].picking_task_batch_driver
    assert batch.direct_pick_flow.request_factory is request_factory
    assert batch.direct_pick_flow.repository is batch.source_flow.repository


@pytest.mark.parametrize("timeout_ms", [True, 0, -1, 1.5, "1000"])
def test_first_plan_timeout_rejects_nonpositive_or_noninteger_configuration(timeout_ms):
    from automatic_picking.application.completion_flow import AutomaticPickingCompletionFlow

    with pytest.raises(ValueError, match="positive milliseconds"):
        AutomaticPickingCompletionFlow(object(), first_plan_timeout_ms=timeout_ms)
