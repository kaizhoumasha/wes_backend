"""只在部署启用业务插件时注册其迁移模型。"""


def load_plugin_models(enabled_plugin_keys: tuple[str, ...]) -> None:
    if "manual-picking" in enabled_plugin_keys:
        from manual_picking.application.batch_progress_model import (  # noqa: F401
            ManualPickingInboundBatch,
            ManualPickingInboundBatchScan,
        )
        from manual_picking.application.bin_line.return_model import BinLineReturn  # noqa: F401
        from manual_picking.application.passage_model import ManualPickingPassage  # noqa: F401


def include_plugin_table(table_name: str, enabled_plugin_keys: tuple[str, ...]) -> bool:
    return (
        table_name
        not in {
            "manual_picking_passages",
            "bin_line_returns",
            "manual_picking_inbound_batches",
            "manual_picking_inbound_batch_scans",
        }
        or "manual-picking" in enabled_plugin_keys
    )


__all__ = ["include_plugin_table", "load_plugin_models"]
