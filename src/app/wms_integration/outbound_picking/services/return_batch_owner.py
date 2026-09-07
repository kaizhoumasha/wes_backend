"""退箱可靠义务由活动 WorkLine 承担，业务 FIFO 归插件。"""

from src.app.wms_adapter.outbound_picking.return_batch_wire import parse_bin_return_batch_request
from src.app.workline.repositories import WorkLineRepository


class ReturnBatchOwnerService:
    def __init__(self, worklines=None):
        self._worklines = worklines or WorkLineRepository()

    async def validate_owner(self, db, *, workline_id, request_payload):
        try:
            request = parse_bin_return_batch_request(request_payload)
        except (ValueError, TypeError):
            return False
        workline = await self._worklines.get_for_update(db, workline_id, populate_existing=True)
        return bool(workline is not None and workline.is_active and workline.line_code == request.data.workline_code)
