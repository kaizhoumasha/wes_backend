"""SMT 分拣入库的 schema、routes 与静态 registrations。"""

from src.app.runtime.capabilities.material_flow.contracts.smt_sorting_inbound import (
    list_smt_sorting_inbound_ng_reasons,
)
from src.app.runtime.workline_plugins.contracts import CapabilityEffectResultInput, CommandResultInput
from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition
from src.app.runtime.workline_plugins.schema import (
    CommandBinding,
    DeviceRequirement,
    RackPosition,
    RackPositionCarrierCapability,
    ResourceBoundary,
    WorklinePluginSchema,
)

from .contracts import SmtSortingInboundConfig, SmtSortingInboundFacts, SmtSortingInboundState, SourcePickRequestInput
from .handlers import build_facts, decide

SMT_SORTING_INBOUND_SCHEMA = WorklinePluginSchema(
    devices=(DeviceRequirement("SORTING_SOURCE_ARM", 1, 1),),
    rack_positions=(
        RackPosition(
            code="SOURCE_STATION_A",
            role="SORTING_INBOUND_SOURCE",
            station_code="SOURCE_STATION_A",
            carrier_capability=RackPositionCarrierCapability(("SINGLE_LAYER",)),
        ),
        RackPosition(
            code="SOURCE_STATION_B",
            role="SORTING_INBOUND_SOURCE",
            station_code="SOURCE_STATION_B",
            carrier_capability=RackPositionCarrierCapability(("SINGLE_LAYER",)),
        ),
        RackPosition(
            code="TARGET_STATION",
            role="SORTING_INBOUND_TARGET",
            station_code="TARGET_STATION",
            carrier_capability=RackPositionCarrierCapability(("FIVE_LAYER",)),
        ),
    ),
    commands=(CommandBinding("SORTING_SOURCE_PICK", "SORTING_SOURCE_ARM"),),
    resource_boundaries=(
        ResourceBoundary(
            "SOURCE_STATION_A",
            "SINGLE_LAYER",
            "SORTING_INBOUND_SOURCE",
            "SORTING_INBOUND",
            "SOURCE_RACK",
            "STATION",
        ),
        ResourceBoundary(
            "SOURCE_STATION_B",
            "SINGLE_LAYER",
            "SORTING_INBOUND_SOURCE",
            "SORTING_INBOUND",
            "SOURCE_RACK",
            "STATION",
        ),
        ResourceBoundary(
            "TARGET_STATION",
            "FIVE_LAYER",
            "SORTING_INBOUND_TARGET",
            "SORTING_INBOUND",
            "TARGET_RACK",
            "STATION",
        ),
    ),
)

DEFINITION = WorklinePluginDefinition(
    plugin_key="smt_sorting_inbound",
    contract_version="smt_sorting_inbound.v1",
    config_model=SmtSortingInboundConfig,
    state_model=SmtSortingInboundState,
    routes=("SOURCE_PICK_REQUESTED", "COMMAND_RESULT", "CAPABILITY_EFFECT_RESULT"),
    allowed_capabilities=(
        ("material_flow.smt_source_pick_command", "v1"),
        ("material_flow.smt_source_pick_ledger", "v1"),
        ("runtime.session_hold", "v1"),
        ("wms.fulfillment.move_bins_to_conveyor_entry", "v1"),
        ("wms.fulfillment.move_bins_from_conveyor_exit", "v1"),
    ),
    parsers={
        "SOURCE_PICK_REQUESTED": SourcePickRequestInput.model_validate,
        "COMMAND_RESULT": CommandResultInput.model_validate,
        "CAPABILITY_EFFECT_RESULT": CapabilityEffectResultInput.model_validate,
    },
    schema=SMT_SORTING_INBOUND_SCHEMA,
    ng_reason_resolver=list_smt_sorting_inbound_ng_reasons,
)

ROUTE_HANDLERS = {
    (DEFINITION.plugin_key, DEFINITION.contract_version, route): ((decide, SmtSortingInboundFacts, build_facts),)
    for route in DEFINITION.routes
}

__all__ = ["DEFINITION", "ROUTE_HANDLERS", "SMT_SORTING_INBOUND_SCHEMA"]
