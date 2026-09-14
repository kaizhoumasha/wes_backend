"""部署期显式加载静态声明；不构造运行对象。"""

from wes_plugin_sdk import PluginDefinition


def load_plugin_definitions(enabled_plugin_keys: tuple[str, ...]) -> tuple[PluginDefinition, ...]:
    if len(set(enabled_plugin_keys)) != len(enabled_plugin_keys):
        raise ValueError("duplicate enabled plugin keys")
    unknown = set(enabled_plugin_keys) - {"manual-picking"}
    if unknown:
        raise ValueError(f"unknown enabled plugins: {sorted(unknown)}")
    definitions: tuple[PluginDefinition, ...] = ()
    if "manual-picking" in enabled_plugin_keys:
        from manual_picking.definition import DEFINITION

        definitions += (DEFINITION,)
    return definitions
