"""空装配与显式插件选择的基础合同，不导入具体业务插件。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deployment.plugin_composition import CombinedWorkLineConfirmationOwner, build_deployment_runtime
from src.app.wms_adapter.client import WmsClient
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.app.wms_integration.outbound_picking.composition import build_outbound_picking_runtime
from tests.contracts.wms_adapter.inbound_material.support import OPERATION_ID, _digest, _request, _response, _Transport


@pytest.mark.asyncio
async def test_combined_workline_owner_preserves_existing_owner_order() -> None:
    first = SimpleNamespace(validate_owner=AsyncMock(return_value=False))
    second = SimpleNamespace(validate_owner=AsyncMock(return_value=True))
    owner = CombinedWorkLineConfirmationOwner(first, second)

    assert await owner.validate_owner(object(), workline_id=7, request_payload={}) is True
    first.validate_owner.assert_awaited_once()
    second.validate_owner.assert_awaited_once()


def test_empty_composition_builds_core_without_a_business_handler() -> None:
    runtime = build_deployment_runtime(
        enabled_plugin_keys=(),
        session_factory=object(),
        transport_runtime=SimpleNamespace(service=object(), position_projection_service=object(), client=object()),
        device_command_service=object(),
    )
    assert runtime.plugins == ()
    assert runtime.wms_recovery_event_handler is None
    assert runtime.execution.fact_processor is not None


def test_manual_picking_composition_installs_prepare_and_plan_activation_without_plugin_celery() -> None:
    runtime = build_deployment_runtime(
        enabled_plugin_keys=("manual-picking",),
        session_factory=object(),
        transport_runtime=SimpleNamespace(service=object(), position_projection_service=object(), client=object()),
        device_command_service=object(),
    )

    assert len(runtime.plugins) == 1
    plugin = runtime.plugins[0]
    assert plugin.picking_task_prepare_policy is not None
    assert plugin.picking_task_plan_applied_handler is not None
    assert plugin.transport_outcome_publisher is not None
    assert runtime.picking_task_plan_activation_service.plugin_identities == (("manual-picking", "0.1.0"),)


def test_manual_picking_admission_policy_maps_into_plan_delta_runtime() -> None:
    transport_runtime = SimpleNamespace(service=object(), position_projection_service=object(), client=object())
    deployment = build_deployment_runtime(
        enabled_plugin_keys=("manual-picking",),
        session_factory=object(),
        transport_runtime=transport_runtime,
        device_command_service=object(),
    )
    plugin = deployment.plugins[0]
    policy = plugin.picking_task_plan_admission_policy
    assert policy is not None
    policies = {(plugin.definition.plugin_key, plugin.definition.plugin_version): policy}

    outbound = build_outbound_picking_runtime(session_factory=object(), plan_admission_policies=policies)
    plan_delta_service = outbound.picking_task_plan_delta_handler._recorder
    assert plan_delta_service._plan_admission_policies == policies
    assert plan_delta_service._plan_admission_policies[("manual-picking", "0.1.0")] is policy


@pytest.mark.asyncio
async def test_zero_plugins_keep_the_wms_adapter_available_for_existing_confirmations() -> None:
    transport = _Transport(
        _response(
            {
                "operation_id": OPERATION_ID,
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
            }
        )
    )
    runtime = build_deployment_runtime(
        enabled_plugin_keys=(),
        session_factory=object(),
        device_command_service=object(),
        transport_runtime=SimpleNamespace(
            service=object(), position_projection_service=object(), client=WmsClient(transport)
        ),
    )
    payload = _request()
    adapter = runtime.execution.wms_confirmation_service._adapter
    assert adapter is not None
    result = await adapter.dispatch(
        operation=payload["operation"],
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )
    assert runtime.plugins == ()
    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.response_result == "ACCEPT"
    assert result.normalized_response["operation_id"] == OPERATION_ID
    assert len(transport.requests) == 1
    assert transport.requests[0].path == "/api/v1/wes/decisions"


@pytest.mark.parametrize("keys", [("unknown-plugin",), ("manual-picking", "manual-picking")])
def test_invalid_plugin_selection_fails_before_constructing_resources(keys: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        build_deployment_runtime(
            enabled_plugin_keys=keys,
            session_factory=object(),
            transport_runtime=object(),
            device_command_service=object(),
        )


@pytest.mark.asyncio
async def test_zero_plugins_dispatch_existing_prepare_and_install_picking_owner() -> None:
    transport = _Transport(
        _response(
            {"operation_id": OPERATION_ID, "code": "PREPARE_ACCEPTED", "timestamp": 2, "data": {}},
            status=202,
        )
    )
    runtime = build_deployment_runtime(
        enabled_plugin_keys=(),
        session_factory=object(),
        device_command_service=object(),
        transport_runtime=SimpleNamespace(
            service=object(), position_projection_service=object(), client=WmsClient(transport)
        ),
    )
    payload = {
        "operation_id": OPERATION_ID,
        "operation": "outbound.picking_task.prepare@v1",
        "timestamp": 1,
        "data": {"task_id": "PICK-1", "workline_code": "LINE-1"},
    }
    service = runtime.execution.wms_confirmation_service
    result = await service._adapter.dispatch(
        operation=payload["operation"],
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )
    assert result.response_result == "PREPARE_ACCEPTED"
    assert service._picking_task_owner is not None
    assert runtime.plugins == ()
    assert len(transport.requests) == 1
