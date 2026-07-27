"""SMT 分拣入库的 schema、routes 与静态 registrations。"""

from src.app.runtime.workline_plugins.contracts import CapabilityEffectResultInput, CommandResultInput
from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition

from .contracts import SmtSortingInboundConfig, SmtSortingInboundFacts, SmtSortingInboundState, SourcePickRequestInput
from .handlers import build_facts, decide

DEFINITION = WorklinePluginDefinition(
    plugin_key="smt_sorting_inbound",
    contract_version="v1",
    config_model=SmtSortingInboundConfig,
    state_model=SmtSortingInboundState,
    routes=("SOURCE_PICK_REQUESTED", "COMMAND_RESULT", "CAPABILITY_EFFECT_RESULT"),
    allowed_capabilities=(),
    parsers={
        "SOURCE_PICK_REQUESTED": SourcePickRequestInput.model_validate,
        "COMMAND_RESULT": CommandResultInput.model_validate,
        "CAPABILITY_EFFECT_RESULT": CapabilityEffectResultInput.model_validate,
    },
)

ROUTE_HANDLERS = {
    (DEFINITION.plugin_key, DEFINITION.contract_version, route): ((decide, SmtSortingInboundFacts, build_facts),)
    for route in DEFINITION.routes
}

__all__ = ["DEFINITION", "ROUTE_HANDLERS"]
