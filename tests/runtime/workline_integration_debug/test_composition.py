from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.app.workline_integration_debug.composition import CombinedWorkLineConfirmationOwner


@pytest.mark.asyncio
async def test_combined_owner_checks_debug_identity_after_existing_owner_declines() -> None:
    existing = AsyncMock()
    existing.validate_owner.return_value = False
    debug = AsyncMock()
    debug.validate_owner.return_value = True
    owner = CombinedWorkLineConfirmationOwner(existing, debug)
    payload = {"operation": "outbound.manual_bin.work_admission_decide@v1", "operation_id": "op-1"}

    assert await owner.validate_owner(object(), workline_id=3, request_payload=payload) is True  # type: ignore[arg-type]
    existing.validate_owner.assert_awaited_once()
    debug.validate_owner.assert_awaited_once()
