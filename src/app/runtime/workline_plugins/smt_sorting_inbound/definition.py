"""SMT 分拣入库的 schema、routes 与静态 registrations。"""

from src.app.runtime.workline_plugins.contracts import CapabilityEffectResultInput, CommandResultInput
from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition
from src.app.runtime.workline_plugins.schema import CommandBinding, DeviceRequirement, WorklinePluginSchema

from .contracts import SmtSortingInboundConfig, SmtSortingInboundFacts, SmtSortingInboundState, SourcePickRequestInput
from .handlers import build_facts, decide

SMT_SORTING_INBOUND_SCHEMA = WorklinePluginSchema(
    devices=(DeviceRequirement("SORTING_SOURCE_ARM", 1, 1),),
    commands=(CommandBinding("SORTING_SOURCE_PICK", "SORTING_SOURCE_ARM"),),
)

DEFINITION = WorklinePluginDefinition(
    plugin_key="smt_sorting_inbound",
    contract_version="smt_sorting_inbound.v1",
    config_model=SmtSortingInboundConfig,
    state_model=SmtSortingInboundState,
    routes=("SOURCE_PICK_REQUESTED", "COMMAND_RESULT", "CAPABILITY_EFFECT_RESULT"),
    allowed_capabilities=(
        ("device.device_command_write", "v1"),
        ("runtime.session_hold", "v1"),
    ),
    parsers={
        "SOURCE_PICK_REQUESTED": SourcePickRequestInput.model_validate,
        "COMMAND_RESULT": CommandResultInput.model_validate,
        "CAPABILITY_EFFECT_RESULT": CapabilityEffectResultInput.model_validate,
    },
    schema=SMT_SORTING_INBOUND_SCHEMA,
)

ROUTE_HANDLERS = {
    (DEFINITION.plugin_key, DEFINITION.contract_version, route): ((decide, SmtSortingInboundFacts, build_facts),)
    for route in DEFINITION.routes
}

__all__ = ["DEFINITION", "ROUTE_HANDLERS", "SMT_SORTING_INBOUND_SCHEMA"]
