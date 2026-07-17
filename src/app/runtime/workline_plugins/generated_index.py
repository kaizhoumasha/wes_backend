"""由 scripts/generate_runtime_extensions.py 生成；禁止手工编辑。"""

from types import MappingProxyType

from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION as _DEFINITION_0

WORKLINE_PLUGIN_IDENTITIES = (("rough_sorter", "rough_sorter.v2"),)
WORKLINE_PLUGIN_INDEX_DIGEST = "463e1d5b0c4be87c26f8abbf20342b9ead772bed9b45484d19f50500dedf0b51"
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
