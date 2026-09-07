"""空装配与显式插件选择的基础合同，不导入具体业务插件。"""

from types import SimpleNamespace

import pytest

from deployment.plugin_composition import build_deployment_runtime
from src.app.wms_adapter.client import WmsClient
from src.app.wms_adapter.dispatch import WmsDispatchCode
from tests.contracts.wms_adapter.inbound_material.support import OPERATION_ID, _digest, _request, _response, _Transport


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


@pytest.mark.parametrize("keys", [("unknown-plugin",), ("rough_sorter", "rough_sorter")])
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
