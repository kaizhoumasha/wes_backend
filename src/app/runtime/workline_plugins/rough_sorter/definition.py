"""粗分机 Workline Plugin 唯一作者态 Definition。"""

from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition

from .config import RoughSorterConfig
from .handlers import RoughSorterFacts, decide
from .inputs import parse_business_timeout, parse_pick_and_put_result, parse_replay_request, parse_scan_completed
from .state import RoughSorterState

DEFINITION = WorklinePluginDefinition(
    plugin_key="rough_sorter",
    contract_version="rough_sorter.v2",
    config_model=RoughSorterConfig,
    state_model=RoughSorterState,
    routes=("SCAN_COMPLETED", "PICK_AND_PUT_RESULT", "BUSINESS_TIMEOUT", "REPLAY_REQUEST"),
    allowed_capabilities=(
        ("device.device_command_write", "v1"),
        ("material_flow.material_unit_write", "v1"),
        ("runtime.session_hold", "v1"),
        ("wms.rough_sorter_inventory_admission", "v1"),
    ),
    parsers={
        "SCAN_COMPLETED": parse_scan_completed,
        "PICK_AND_PUT_RESULT": parse_pick_and_put_result,
        "BUSINESS_TIMEOUT": parse_business_timeout,
        "REPLAY_REQUEST": parse_replay_request,
    },
)

ROUTE_HANDLERS = {
    (DEFINITION.plugin_key, DEFINITION.contract_version, route): ((decide, RoughSorterFacts),)
    for route in DEFINITION.routes
}

__all__ = ["DEFINITION", "ROUTE_HANDLERS"]
