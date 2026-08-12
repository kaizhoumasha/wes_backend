"""11 项 WMS EFFECT 的 System Capability 静态索引合同。"""

from __future__ import annotations

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityMode,
)
from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX
from src.app.runtime.system_capabilities.wms.effect_runtime import (
    WmsEffectDispatchAccepted,
    WmsRegistryEffectCapabilityHandler,
)
from src.app.wms_integration.operation_contract import WmsCompletionMode, WmsExecutionLane
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS
from src.app.wms_integration.ports.effect_preparation import WmsEffectPreparationPort


def test_all_effect_operations_are_thin_shared_capability_compositions() -> None:
    assert len(EFFECT_OPERATIONS) == 11

    for operation in EFFECT_OPERATIONS:
        capability_key, contract_version = operation.identity.rsplit("@", maxsplit=1)
        definition = SYSTEM_CAPABILITY_INDEX[(capability_key, contract_version)]

        assert definition.mode is SystemCapabilityMode.EFFECT
        assert definition.input_model is operation.request_model
        assert definition.output_model is WmsEffectDispatchAccepted
        assert definition.handler_factory is WmsRegistryEffectCapabilityHandler
        assert definition.required_ports == (WmsEffectPreparationPort,)
        assert definition.completion_mode is EffectCompletionMode.OUTBOX_ASYNC
        assert definition.timeout_seconds == operation.budget.deadline_seconds


def test_effect_mode_and_lane_are_the_exact_static_nine_two_matrix() -> None:
    sync_identities = {
        operation.identity
        for operation in EFFECT_OPERATIONS
        if operation.completion_mode is WmsCompletionMode.SYNC_RESULT
    }
    async_identities = {
        operation.identity
        for operation in EFFECT_OPERATIONS
        if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
    }
    data_identities = {
        operation.identity for operation in EFFECT_OPERATIONS if operation.execution_lane is WmsExecutionLane.WMS_DATA
    }
    fulfillment_identities = {
        operation.identity
        for operation in EFFECT_OPERATIONS
        if operation.execution_lane is WmsExecutionLane.WMS_FULFILLMENT
    }

    assert len(sync_identities) == 9
    assert len(async_identities) == 2
    assert len(data_identities) == 8
    assert len(fulfillment_identities) == 3
    assert "wms.fulfillment.publish_manual_task@v1" in sync_identities & data_identities
    assert "wms.fulfillment.cancel_request@v1" in sync_identities & fulfillment_identities
    assert async_identities <= fulfillment_identities
