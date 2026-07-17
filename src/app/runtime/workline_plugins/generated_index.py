"""由 scripts/generate_runtime_extensions.py 生成；禁止手工编辑。"""

from types import MappingProxyType

from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION as _DEFINITION_0

WORKLINE_PLUGIN_IDENTITIES = (("rough_sorter", "rough_sorter.v2"),)
WORKLINE_PLUGIN_INDEX_DIGEST = "b39f78bf3a7821de989a6cb12c0d28e378c1842dfe171df75a89b510a2ffe765"
WORKLINE_PLUGIN_INDEX = MappingProxyType(
    {
        ("rough_sorter", "rough_sorter.v2"): _DEFINITION_0,
    }
)

__all__ = [
    "WORKLINE_PLUGIN_IDENTITIES",
    "WORKLINE_PLUGIN_INDEX",
    "WORKLINE_PLUGIN_INDEX_DIGEST",
]
