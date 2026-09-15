"""人工拣料插件的显式、无副作用业务装配。"""

from manual_picking.application.passage_repository import PassageRepository
from manual_picking.application.transport_outcome import ManualPickingTransportOutcomePublisher
from manual_picking.definition import DEFINITION
from manual_picking.handlers import PickingTaskPlanAppliedHandler
from manual_picking.prepare_policy import ManualPickingPreparePolicy
from src.app.execution.plugin_binding import BusinessEvidenceConsumer, PluginRuntimeBinding
from src.app.wms_adapter.outbound_picking.departure_wire import RACK_DEPARTURE_OPERATION
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.manual_bin_admission_wire import MANUAL_BIN_ADMISSION_OPERATION
from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import MANUAL_BIN_COMPLETED_OPERATION
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.app.workline.installed_plugin import InstalledWorkLinePlugin


def build_plugin(
    *, scan_flow: BusinessEvidenceConsumer, batch_driver: object, completion_driver: object
) -> InstalledWorkLinePlugin:
    passages = PassageRepository()
    return InstalledWorkLinePlugin(
        definition=DEFINITION,
        runtime_binding=PluginRuntimeBinding(
            plugin_key=DEFINITION.plugin_key,
            plugin_version=DEFINITION.plugin_version,
            handlers=(),
            business_evidence_consumer=scan_flow,
            business_wms_operations=(
                MANUAL_BIN_ADMISSION_OPERATION,
                MANUAL_BIN_COMPLETED_OPERATION,
                BIN_INBOUND_BATCH_OPERATION,
                BIN_RETURN_BATCH_OPERATION,
                RACK_DEPARTURE_OPERATION,
            ),
        ),
        picking_task_prepare_policy=ManualPickingPreparePolicy(),
        picking_task_plan_applied_handler=PickingTaskPlanAppliedHandler(),
        picking_task_batch_driver=batch_driver,
        picking_task_completion_driver=completion_driver,
        transport_outcome_publisher=ManualPickingTransportOutcomePublisher(),
        business_blocker=passages,
        business_archiver=passages,
    )


__all__ = ["build_plugin"]
