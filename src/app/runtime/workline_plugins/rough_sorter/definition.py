"""粗分机 Workline Plugin 唯一作者态 Definition。"""

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_context import RoughSorterContext
from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition
from src.app.runtime.workline_plugins.schema import (
    CommandBinding,
    DeviceRequirement,
    EventBinding,
    FlowEdge,
    NodeRef,
    PipelineQueue,
    RackPosition,
    RackPositionCarrierCapability,
    ResourceBoundary,
    SessionSubject,
    StateMachine,
    StateMachineOwner,
    StateMachineSubject,
    StateMachineTransition,
    TopologySpec,
    WorklinePluginSchema,
)

from .config import RoughSorterConfig
from .domain_contract import (
    classify_rough_sorter_result,
    list_ng_reasons,
    parse_six_in_one,
    resolve_material_identity,
    resolve_rough_sorter_business_key,
)
from .handlers import RoughSorterFacts, decide
from .inputs import (
    parse_business_timeout,
    parse_capability_effect_result,
    parse_pick_and_put_result,
    parse_replay_request,
    parse_scan_completed,
)
from .state import RoughSorterState

_MATERIAL_TRANSITIONS = (
    StateMachineTransition("IN_TRANSIT", ("STORED", "COMPLETED", "NG", "RECONCILING")),
    StateMachineTransition("STORED", ("IN_TRANSIT", "NG", "RECONCILING")),
    StateMachineTransition("RECONCILING", ("IN_TRANSIT", "STORED", "COMPLETED", "NG")),
    StateMachineTransition("NG", ()),
    StateMachineTransition("COMPLETED", ()),
)

ROUGH_SORTER_SCHEMA = WorklinePluginSchema(
    devices=(
        DeviceRequirement("ROUGH_SORTER_INPUT_ARM", 1, 1),
        DeviceRequirement("ROUGH_SORTER_CONVEYOR", 1, 1),
        DeviceRequirement("ROUGH_SORTER_OUTPUT_ARM", 1, 1),
    ),
    rack_positions=(
        RackPosition(
            code="SINGLE_LAYER_A",
            role="CLASSIFIER_WORK",
            station_code="CLASSIFIER_WORK_POSITION",
            carrier_capability=RackPositionCarrierCapability(("SINGLE_LAYER",)),
        ),
    ),
    topology=TopologySpec(
        (
            FlowEdge(
                NodeRef("DEVICE_ROLE", "ROUGH_SORTER_INPUT_ARM"),
                NodeRef("DEVICE_ROLE", "ROUGH_SORTER_CONVEYOR"),
                "OPERATION",
            ),
            FlowEdge(
                NodeRef("DEVICE_ROLE", "ROUGH_SORTER_CONVEYOR"),
                NodeRef("DEVICE_ROLE", "ROUGH_SORTER_OUTPUT_ARM"),
                "OPERATION",
            ),
            FlowEdge(
                NodeRef("DEVICE_ROLE", "ROUGH_SORTER_OUTPUT_ARM"),
                NodeRef("RACK_POSITION", "SINGLE_LAYER_A"),
                "OPERATION",
            ),
        )
    ),
    events=(
        EventBinding("SCAN_COMPLETED", ("ROUGH_SORTER_INPUT_ARM",), "ENTRY_DEVICE"),
        EventBinding("ROUGH_SORTER_STORAGE_RETRY", ("ROUGH_SORTER_OUTPUT_ARM",), "INTERNAL"),
    ),
    commands=(
        CommandBinding("PICK_AND_PUT", "ROUGH_SORTER_INPUT_ARM"),
        CommandBinding("MOVE_TO_NG", "ROUGH_SORTER_INPUT_ARM"),
        CommandBinding("MOVE_FORWARD", "ROUGH_SORTER_CONVEYOR"),
        CommandBinding("PUT_TO_BIN", "ROUGH_SORTER_OUTPUT_ARM"),
    ),
    resource_boundaries=(
        ResourceBoundary(
            "SINGLE_LAYER_A",
            "SINGLE_LAYER",
            "ROUGH_SORTER_BIN_ALLOCATION",
            "REPLACE_CLASSIFIER_WORK_RACK",
            "ACTIVE_CLASSIFIER_BIN_RACK",
            "STATION",
        ),
    ),
    session_subject=SessionSubject("MATERIAL_UNIT", "REEL", ("PkgID", "material_identity_key")),
    state_machines=(
        StateMachine(
            "rough_sorter_material_unit_reel",
            StateMachineSubject("MATERIAL_UNIT", "MATERIAL_UNIT", "REEL"),
            StateMachineOwner("MaterialUnit", "status"),
            "MATERIAL_LIFECYCLE",
            _MATERIAL_TRANSITIONS,
        ),
    ),
    pipeline_queues=(
        PipelineQueue("SCAN_WORKSTATION", "WORKSTATION", 1, "FIFO"),
        PipelineQueue("OUTPUT_BUFFER", "BUFFER", "MANY", "FIFO"),
    ),
)

DEFINITION = WorklinePluginDefinition(
    plugin_key="rough_sorter",
    contract_version="rough_sorter.v2",
    config_model=RoughSorterConfig,
    state_model=RoughSorterState,
    routes=(
        "SCAN_COMPLETED",
        "PICK_AND_PUT_RESULT",
        "BUSINESS_TIMEOUT",
        "REPLAY_REQUEST",
        "CAPABILITY_EFFECT_RESULT",
    ),
    allowed_capabilities=(
        ("device.device_command_write", "v1"),
        ("material_flow.material_unit_write", "v1"),
        ("runtime.session_hold", "v1"),
        ("wms.fulfillment.notify_pkg_binding", "v1"),
        ("wms.inventory.confirm_inbound", "v1"),
        ("wms.inventory.query_inventory", "v1"),
    ),
    parsers={
        "SCAN_COMPLETED": parse_scan_completed,
        "PICK_AND_PUT_RESULT": parse_pick_and_put_result,
        "BUSINESS_TIMEOUT": parse_business_timeout,
        "REPLAY_REQUEST": parse_replay_request,
        "CAPABILITY_EFFECT_RESULT": parse_capability_effect_result,
    },
    schema=ROUGH_SORTER_SCHEMA,
    context_model=RoughSorterContext,
    business_key_resolver=resolve_rough_sorter_business_key,
    result_classifier=classify_rough_sorter_result,
    material_identity_resolver=resolve_material_identity,
    ng_reason_resolver=list_ng_reasons,
    input_evidence_parser=parse_six_in_one,
)

ROUTE_HANDLERS = {
    (DEFINITION.plugin_key, DEFINITION.contract_version, route): ((decide, RoughSorterFacts),)
    for route in DEFINITION.routes
}

__all__ = ["DEFINITION", "ROUGH_SORTER_SCHEMA", "ROUTE_HANDLERS"]
