"""只在部署启用业务插件时注册其迁移模型。"""


def load_plugin_models(enabled_plugin_keys: tuple[str, ...]) -> None:
    if "automatic-picking" in enabled_plugin_keys:
        from automatic_picking.application.drain_model import AutomaticPickingDrain  # noqa: F401
        from automatic_picking.application.feed_model import (  # noqa: F401
            AutomaticPickingFeed,
            AutomaticPickingFeedScan,
        )
        from automatic_picking.application.pickup_model import AutomaticPickingPickup  # noqa: F401
        from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation  # noqa: F401
        from automatic_picking.application.return_model import AutomaticPickingReturn  # noqa: F401
        from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion  # noqa: F401
        from automatic_picking.application.work_model import AutomaticPickingWork  # noqa: F401

    if "manual-picking" in enabled_plugin_keys:
        from manual_picking.application.batch_progress_model import (  # noqa: F401
            ManualPickingInboundBatch,
            ManualPickingInboundBatchScan,
        )
        from manual_picking.application.bin_line.return_model import BinLineReturn  # noqa: F401
        from manual_picking.application.drain_execution_model import (  # noqa: F401
            ManualPickingDrainExecution,
            ManualPickingDrainReturnMember,
        )
        from manual_picking.application.passage_model import ManualPickingPassage  # noqa: F401


def include_plugin_table(table_name: str, enabled_plugin_keys: tuple[str, ...]) -> bool:
    if table_name in {
        "automatic_picking_works",
        "automatic_picking_feeds",
        "automatic_picking_feed_scans",
        "automatic_picking_pickups",
        "automatic_picking_returns",
        "automatic_picking_return_allocations",
        "automatic_picking_return_motions",
        "automatic_picking_drains",
    }:
        return "automatic-picking" in enabled_plugin_keys
    return (
        table_name
        not in {
            "manual_picking_passages",
            "bin_line_returns",
            "manual_picking_inbound_batches",
            "manual_picking_inbound_batch_scans",
            "manual_picking_drain_executions",
            "manual_picking_drain_return_members",
        }
        or "manual-picking" in enabled_plugin_keys
    )


__all__ = ["include_plugin_table", "load_plugin_models"]
