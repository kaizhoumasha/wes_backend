"""WMS registry QUERY 的 System Capability Definition 构造器。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.runtime.system_capabilities.wms.query_handler import WmsRegistryQueryCapabilityHandler
from src.app.wms_integration.operation_contract import WmsOperationDefinition
from src.app.wms_integration.ports.query_execution import WmsQueryExecutionPort
from src.app.wms_integration.provider_profile import WMS_PROVIDER_CONTRACT_VERSION


def build_wms_query_capability_definition(
    operation: WmsOperationDefinition,
) -> SystemCapabilityDefinition:
    """把单项静态 QUERY Definition 投影为独立 System Capability identity。"""

    capability_key, contract_version = operation.identity.rsplit("@", maxsplit=1)
    return SystemCapabilityDefinition(
        capability_key=capability_key,
        contract_version=contract_version,
        mode=SystemCapabilityMode.QUERY,
        input_model=operation.request_model,
        output_model=operation.result_model,
        handler_factory=WmsRegistryQueryCapabilityHandler,
        required_ports=(WmsQueryExecutionPort,),
        admission=f"wms.{WMS_PROVIDER_CONTRACT_VERSION}",
        timeout_seconds=operation.budget.deadline_seconds,
        completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
        audit_policy="metadata",
    )


__all__ = ["build_wms_query_capability_definition"]
