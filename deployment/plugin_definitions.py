"""部署期显式加载静态声明；不构造运行对象。"""

from wes_plugin_sdk import PluginDefinition


def load_plugin_definitions(enabled_plugin_keys: tuple[str, ...]) -> tuple[PluginDefinition, ...]:
    if len(set(enabled_plugin_keys)) != len(enabled_plugin_keys):
        raise ValueError("duplicate enabled plugin keys")
    unknown = set(enabled_plugin_keys) - {"rough_sorter", "manual-picking"}
    if unknown:
        raise ValueError(f"unknown enabled plugins: {sorted(unknown)}")
    definitions: tuple[PluginDefinition, ...] = ()
    if "rough_sorter" in enabled_plugin_keys:
        # 粗分声明尚在启动类中；独立声明模块的迁移见 TODOS.md，本期不改其业务包。
        from rough_sorter.application.start_plan import RoughSorterStartPlanBuilder
        from rough_sorter.plugin import PLUGIN_KEY, PLUGIN_VERSION

        definitions += (
            PluginDefinition(
                plugin_key=PLUGIN_KEY,
                plugin_version=PLUGIN_VERSION,
                display_name="粗分业务",
                supported_line_types=("AUTO", "MANUAL", "HYBRID"),
                device_roles=RoughSorterStartPlanBuilder.device_roles,
                position_slots=RoughSorterStartPlanBuilder.position_slots,
            ),
        )
    if "manual-picking" in enabled_plugin_keys:
        from manual_picking.definition import DEFINITION

        definitions += (DEFINITION,)
    return definitions
