"""由 scripts/generate_runtime_extensions.py 生成；禁止手工编辑。"""

from types import MappingProxyType

from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION as _DEFINITION_0
from src.app.runtime.workline_plugins.rough_sorter.definition import ROUTE_HANDLERS as _ROUTE_HANDLERS_0
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION as _DEFINITION_1
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import ROUTE_HANDLERS as _ROUTE_HANDLERS_1

WORKLINE_PLUGIN_IDENTITIES = (
    ("rough_sorter", "rough_sorter.v2"),
    ("smt_sorting_inbound", "smt_sorting_inbound.v1"),
)
WORKLINE_PLUGIN_INDEX_DIGEST = "e3adadf43b0c8ac61a2cfacf95bde685b74c7817734de876735c59d55f97c9e7"
WORKLINE_PLUGIN_INDEX = MappingProxyType(
    {
        ("rough_sorter", "rough_sorter.v2"): _DEFINITION_0,
        ("smt_sorting_inbound", "smt_sorting_inbound.v1"): _DEFINITION_1,
    }
)
WORKLINE_PLUGIN_HANDLER_REGISTRATIONS = MappingProxyType(
    {
        ("rough_sorter", "rough_sorter.v2", "BUSINESS_TIMEOUT"): _ROUTE_HANDLERS_0.get(
            ("rough_sorter", "rough_sorter.v2", "BUSINESS_TIMEOUT"), ()
        ),
        ("rough_sorter", "rough_sorter.v2", "CAPABILITY_EFFECT_RESULT"): _ROUTE_HANDLERS_0.get(
            ("rough_sorter", "rough_sorter.v2", "CAPABILITY_EFFECT_RESULT"), ()
        ),
        ("rough_sorter", "rough_sorter.v2", "COMMAND_RESULT"): _ROUTE_HANDLERS_0.get(
            ("rough_sorter", "rough_sorter.v2", "COMMAND_RESULT"), ()
        ),
        ("rough_sorter", "rough_sorter.v2", "REPLAY_REQUEST"): _ROUTE_HANDLERS_0.get(
            ("rough_sorter", "rough_sorter.v2", "REPLAY_REQUEST"), ()
        ),
        ("rough_sorter", "rough_sorter.v2", "SCAN_COMPLETED"): _ROUTE_HANDLERS_0.get(
            ("rough_sorter", "rough_sorter.v2", "SCAN_COMPLETED"), ()
        ),
        ("smt_sorting_inbound", "smt_sorting_inbound.v1", "CAPABILITY_EFFECT_RESULT"): _ROUTE_HANDLERS_1.get(
            ("smt_sorting_inbound", "smt_sorting_inbound.v1", "CAPABILITY_EFFECT_RESULT"), ()
        ),
        ("smt_sorting_inbound", "smt_sorting_inbound.v1", "COMMAND_RESULT"): _ROUTE_HANDLERS_1.get(
            ("smt_sorting_inbound", "smt_sorting_inbound.v1", "COMMAND_RESULT"), ()
        ),
        ("smt_sorting_inbound", "smt_sorting_inbound.v1", "SOURCE_PICK_REQUESTED"): _ROUTE_HANDLERS_1.get(
            ("smt_sorting_inbound", "smt_sorting_inbound.v1", "SOURCE_PICK_REQUESTED"), ()
        ),
    }
)

__all__ = [
    "WORKLINE_PLUGIN_HANDLER_REGISTRATIONS",
    "WORKLINE_PLUGIN_IDENTITIES",
    "WORKLINE_PLUGIN_INDEX",
    "WORKLINE_PLUGIN_INDEX_DIGEST",
]
