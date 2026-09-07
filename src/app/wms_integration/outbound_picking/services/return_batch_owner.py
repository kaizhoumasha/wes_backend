"""退箱确认的 Epoch 身份绑定；不判断 FIFO、容量或实际搬运。"""

from src.app.wms_adapter.outbound_picking.return_batch_wire import parse_bin_return_batch_request
from src.app.workline.models.line_run_epoch import LineRunEpochStatus
from src.app.workline.repositories import LineRunEpochRepository, WorkLineRepository


class ReturnBatchOwnerService:
    def __init__(self, epochs=None, worklines=None):
        self._epochs = epochs or LineRunEpochRepository()
        self._worklines = worklines or WorkLineRepository()

    async def validate_owner(self, db, *, line_run_epoch_id, request_payload):
        try:
            request = parse_bin_return_batch_request(request_payload)
        except (ValueError, TypeError):
            return False
        epoch = await self._epochs.get_by_id(db, line_run_epoch_id)
        if epoch is None:
            return False
        # 与 WorkLine 停用保持相同锁顺序，冻结后再复核 Epoch。
        workline = await self._worklines.get_for_update(db, epoch.workline_id)
        await self._epochs.lock_epoch_lifecycle(db, line_run_epoch_id)
        epoch = await self._epochs.get_by_id_for_update(db, line_run_epoch_id)
        return bool(
            epoch is not None
            and workline is not None
            and epoch.workline_id == workline.id
            and epoch.epoch_code == request.data.line_run_epoch_id
            and workline.line_code == request.data.workline_code
            and epoch.status == LineRunEpochStatus.ACTIVE
        )
