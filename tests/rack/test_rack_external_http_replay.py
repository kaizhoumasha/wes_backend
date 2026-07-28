"""旧 Rack transport 不允许通过 replay 恢复。"""

from __future__ import annotations

import pytest

from src.app.rack.services import RackOperationService


@pytest.mark.asyncio
async def test_removed_rack_transport_replay_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="T5 dispatcher is not implemented"):
        await RackOperationService().request_operation_tasks(
            None,
            operation_key="rack-replay-removed",
            operation_type="RACK_TRANSPORT",
            session=None,
            trace_id="trace-rack-replay",
            task_specs=[],
        )
