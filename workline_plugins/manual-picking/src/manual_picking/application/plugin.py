"""人工拣料插件的显式、无副作用业务装配。"""

from manual_picking.application.transport_outcome import ManualPickingTransportOutcomePublisher
from manual_picking.definition import DEFINITION
from manual_picking.handlers import PickingTaskPlanAppliedHandler
from manual_picking.prepare_policy import ManualPickingPreparePolicy
from src.app.workline.installed_plugin import InstalledWorkLinePlugin


def build_plugin() -> InstalledWorkLinePlugin:
    return InstalledWorkLinePlugin(
        definition=DEFINITION,
        picking_task_prepare_policy=ManualPickingPreparePolicy(),
        picking_task_plan_applied_handler=PickingTaskPlanAppliedHandler(),
        transport_outcome_publisher=ManualPickingTransportOutcomePublisher(),
    )


__all__ = ["build_plugin"]
