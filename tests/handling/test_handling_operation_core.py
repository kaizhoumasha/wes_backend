"""旧 handling transport producer 的 fail-closed 回归。"""

from __future__ import annotations

import pytest

from src.app.handling.services import HandlingOperationMigrationRequiredError, HandlingOperationService


@pytest.mark.asyncio
async def test_handling_operation_rejects_before_persisting_legacy_transport() -> None:
    with pytest.raises(HandlingOperationMigrationRequiredError, match="T5 dispatcher is not implemented"):
        await HandlingOperationService().request_bin_operation(
            None,
            operation_type="BIN_MOVE",
            operation_key="handling-removed-001",
            moves=[],
            trace_id="trace-handling-removed",
        )
