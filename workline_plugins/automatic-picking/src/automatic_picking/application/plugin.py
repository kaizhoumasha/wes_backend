"""自动业务显式关联；公共可靠能力由部署组合根注入。"""

from automatic_picking.application.transport_outcome import AutomaticPickingTransportOutcomePublisher
from automatic_picking.definition import DEFINITION
from automatic_picking.handlers import PickingTaskPlanAppliedHandler
from automatic_picking.handlers.material_flow import material_decided, material_scanned
from automatic_picking.handlers.placement import material_put, placement_pending
from automatic_picking.prepare_policy import AutomaticPickingPreparePolicy
from src.app.execution.plugin_binding import PluginRuntimeBinding
from src.app.workline.installed_plugin import InstalledWorkLinePlugin


def build_plugin(*, scan_flow, batch_driver, works, material_flow=None):
    return InstalledWorkLinePlugin(
        definition=DEFINITION,
        runtime_binding=PluginRuntimeBinding(
            plugin_key=DEFINITION.plugin_key,
            plugin_version=DEFINITION.plugin_version,
            handlers=(material_scanned, material_decided, material_put, placement_pending)
            if material_flow is not None
            else (),
            fact_factory=material_flow,
            decision_application=material_flow,
            business_evidence_consumer=scan_flow,
            business_wms_operations=("outbound.bin.inbound_batch@v1", "outbound.bin.work_plan@v1"),
        ),
        picking_task_prepare_policy=AutomaticPickingPreparePolicy(),
        picking_task_plan_applied_handler=PickingTaskPlanAppliedHandler(),
        picking_task_batch_driver=batch_driver,
        transport_outcome_publisher=AutomaticPickingTransportOutcomePublisher(),
        business_blocker=works,
        business_archiver=works,
    )
