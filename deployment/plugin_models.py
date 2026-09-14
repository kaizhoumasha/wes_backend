"""只在部署启用业务插件时注册其迁移模型。"""


def load_plugin_models(enabled_plugin_keys: tuple[str, ...]) -> None:
    if "manual-picking" in enabled_plugin_keys:
        from manual_picking.application.passage_model import ManualPickingPassage  # noqa: F401


def include_plugin_table(table_name: str, enabled_plugin_keys: tuple[str, ...]) -> bool:
    return table_name != "manual_picking_passages" or "manual-picking" in enabled_plugin_keys


__all__ = ["include_plugin_table", "load_plugin_models"]
