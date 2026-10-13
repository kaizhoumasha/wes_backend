"""固定 operation 的 typed Fact 边界；插件不读取原始 WMS JSON。"""

from src.app.wms_adapter.return_rack_task_completion.wire import (
    RETURN_RACK_TASK_COMPLETED_OPERATION,
    ReturnRackTaskCompletedEvent,
)

from .types import ReturnRackTaskCompletedFact


class ReturnRackTaskCompletedReader:
    def read(self, evidence) -> ReturnRackTaskCompletedFact | None:
        if evidence.operation != RETURN_RACK_TASK_COMPLETED_OPERATION:
            return None
        event = ReturnRackTaskCompletedEvent.model_validate(evidence.normalized_payload)
        if event.operation_id != evidence.operation_id or evidence.picking_task_id is None:
            raise ValueError("整架完成 Evidence 身份未绑定")
        return ReturnRackTaskCompletedFact(
            evidence.id,
            evidence.picking_task_id,
            event.data.task_id,
            event.data.rack_id,
            event.data.through_plan_revision,
            event.data.completed_at,
            event.operation_id,
        )
