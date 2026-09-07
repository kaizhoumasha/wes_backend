"""宿主可靠义务的静态 Adapter 选择；插件不消费持久化 wire 信封。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult
from src.app.wms_adapter.inbound_material.adapter import InboundMaterialAdapter
from src.app.wms_adapter.inbound_material.wire import parse_outbound_request
from src.app.wms_adapter.outbound_picking.adapter import PickingTaskPrepareAdapter
from src.app.wms_adapter.outbound_picking.arrival_report_adapter import (
    ReturnRackArrivalReportAdapter,
)
from src.app.wms_adapter.outbound_picking.arrival_report_wire import RETURN_RACK_ARRIVAL_REPORT_OPERATION
from src.app.wms_adapter.outbound_picking.departure_adapter import RackDepartureAdapter
from src.app.wms_adapter.outbound_picking.departure_wire import RACK_DEPARTURE_OPERATION
from src.app.wms_adapter.outbound_picking.inbound_batch_adapter import BinInboundBatchAdapter
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.material_decide_adapter import PickingMaterialDecideAdapter
from src.app.wms_adapter.outbound_picking.material_decide_wire import MATERIAL_DECIDE_OPERATION
from src.app.wms_adapter.outbound_picking.return_batch_adapter import BinReturnBatchAdapter
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.source_empty_adapter import SourceEmptyAdapter
from src.app.wms_adapter.outbound_picking.source_empty_wire import SOURCE_EMPTY_OPERATION
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_adapter.outbound_picking.work_plan_adapter import BinWorkPlanAdapter
from src.app.wms_adapter.outbound_picking.work_plan_wire import BIN_WORK_PLAN_OPERATION

if TYPE_CHECKING:
    from src.app.wms_adapter.client import WmsClient


class WmsConfirmationAdapter:
    """只关联已启用的固定 operation，未装配能力 fail closed。"""

    def __init__(self, client: WmsClient) -> None:
        self._inbound = InboundMaterialAdapter(client)
        self._prepare = PickingTaskPrepareAdapter(client)
        self._arrival_report = ReturnRackArrivalReportAdapter(client)
        self._inbound_batch = BinInboundBatchAdapter(client)
        self._return_batch = BinReturnBatchAdapter(client)
        self._work_plan = BinWorkPlanAdapter(client)
        self._departure = RackDepartureAdapter(client)
        self._material_decide = PickingMaterialDecideAdapter(client)
        self._source_empty = SourceEmptyAdapter(client)

    async def dispatch(
        self,
        *,
        operation: str,
        operation_id: str,
        request_payload: dict[str, Any],
        request_digest: str,
    ) -> WmsDispatchResult:
        adapter: (
            BinInboundBatchAdapter
            | ReturnRackArrivalReportAdapter
            | PickingTaskPrepareAdapter
            | BinWorkPlanAdapter
            | RackDepartureAdapter
            | PickingMaterialDecideAdapter
            | SourceEmptyAdapter
        )
        if operation == BIN_RETURN_BATCH_OPERATION:
            return await self._return_batch.dispatch(
                operation=operation,
                operation_id=operation_id,
                request_payload=request_payload,
                request_digest=request_digest,
            )
        if operation == SOURCE_EMPTY_OPERATION:
            adapter = self._source_empty
        elif operation == MATERIAL_DECIDE_OPERATION:
            adapter = self._material_decide
        elif operation == RACK_DEPARTURE_OPERATION:
            adapter = self._departure
        elif operation == BIN_WORK_PLAN_OPERATION:
            adapter = self._work_plan
        elif operation == BIN_INBOUND_BATCH_OPERATION:
            adapter = self._inbound_batch
        elif operation == RETURN_RACK_ARRIVAL_REPORT_OPERATION:
            adapter = self._arrival_report
        elif operation == PICKING_TASK_PREPARE_OPERATION:
            adapter = self._prepare
        else:
            try:
                request = parse_outbound_request(request_payload)
            except (ValueError, TypeError):
                return WmsDispatchResult(WmsDispatchCode.RECONCILING)
            if request.operation != operation or request.operation_id != operation_id:
                return WmsDispatchResult(WmsDispatchCode.RECONCILING)
            return await self._inbound.send(request=request, request_digest=request_digest)
        return await adapter.dispatch(
            operation=operation,
            operation_id=operation_id,
            request_payload=request_payload,
            request_digest=request_digest,
        )
