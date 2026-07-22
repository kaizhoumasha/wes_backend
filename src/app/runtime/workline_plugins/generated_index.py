"""由 scripts/generate_runtime_extensions.py 生成；禁止手工编辑。"""

from types import MappingProxyType

from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION as _DEFINITION_0
from src.app.runtime.workline_plugins.rough_sorter.definition import ROUTE_HANDLERS as _ROUTE_HANDLERS_0

WORKLINE_PLUGIN_IDENTITIES = (("rough_sorter", "rough_sorter.v2"),)
WORKLINE_PLUGIN_INDEX_DIGEST = "c4bbb5f5fce565bbc835da7c4a9524540b58f53b89b12eac92739e861a8afe40"
WORKLINE_PLUGIN_INDEX = MappingProxyType(
    {
        ("rough_sorter", "rough_sorter.v2"): _DEFINITION_0,
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
        ("rough_sorter", "rough_sorter.v2", "PICK_AND_PUT_RESULT"): _ROUTE_HANDLERS_0.get(
            ("rough_sorter", "rough_sorter.v2", "PICK_AND_PUT_RESULT"), ()
        ),
        ("rough_sorter", "rough_sorter.v2", "REPLAY_REQUEST"): _ROUTE_HANDLERS_0.get(
            ("rough_sorter", "rough_sorter.v2", "REPLAY_REQUEST"), ()
        ),
        ("rough_sorter", "rough_sorter.v2", "SCAN_COMPLETED"): _ROUTE_HANDLERS_0.get(
            ("rough_sorter", "rough_sorter.v2", "SCAN_COMPLETED"), ()
        ),
    }
)

__all__ = [
    "WORKLINE_PLUGIN_HANDLER_REGISTRATIONS",
    "WORKLINE_PLUGIN_IDENTITIES",
    "WORKLINE_PLUGIN_INDEX",
    "WORKLINE_PLUGIN_INDEX_DIGEST",
]
