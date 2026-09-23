"""排空可靠义务的 WorkLine owner；不依赖插件安装注册表。"""

from src.app.wms_adapter.return_buffer_drain.wire import parse_request
from src.app.workline.repositories import WorkLineRepository


class ReturnBufferDrainOwnerService:
    def __init__(self, worklines=None):
        self._worklines = worklines or WorkLineRepository()

    async def validate_owner(self, db, *, workline_id, request_payload):
        try:
            request = parse_request(request_payload)
        except (ValueError, TypeError):
            return False
        line = await self._worklines.get_for_authority_update(db, workline_id, populate_existing=True)
        return bool(
            line is not None
            and line.id == workline_id
            and line.is_active
            and line.line_code == request.data.workline_code
        )
