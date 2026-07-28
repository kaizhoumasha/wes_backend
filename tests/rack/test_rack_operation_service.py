"""旧 Rack operation producer 的 fail-closed 回归。"""

from __future__ import annotations

import pytest

from src.app.rack.services import RackOperationMigrationRequiredError, RackOperationService


@pytest.mark.asyncio
async def test_rack_operation_rejects_before_persisting_legacy_transport() -> None:
    with pytest.raises(RackOperationMigrationRequiredError, match="T5 dispatcher is not implemented"):
        await RackOperationService().request_operation_tasks(
            None,
            operation_key="rack-removed-001",
            operation_type="RACK_TRANSPORT",
            session=None,
            trace_id="trace-rack-removed",
            task_specs=[],
        )
