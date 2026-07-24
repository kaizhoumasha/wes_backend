"""WMS EFFECT status claim 的显式 capability 身份合同。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from src.app.runtime.orchestration.repositories.wms_effect_status_repository import WmsEffectStatusRepository


@pytest.mark.asyncio
async def test_claim_uses_paired_wms_operation_identity_and_never_filters_corrupt_binding() -> None:
    intent = SimpleNamespace(
        id=1,
        capability_key="wms.fulfillment.notify_pkg_binding",
        capability_contract_version="v1",
        operation_identity="WMS:PKG-001:PALLET-001",
        status_binding_snapshot_json=None,
        status_binding_snapshot_hash=None,
        status_check_started_at=None,
        status_check_count=0,
        status_check_lease_token=None,
        status_check_lease_until=None,
    )
    outbox = SimpleNamespace(operation_identity="wms.fulfillment.notify_pkg_binding@v1")

    class Result:
        @staticmethod
        def all() -> list[tuple[Any, Any]]:
            return [(intent, outbox)]

    class Db:
        def __init__(self) -> None:
            self.statement: Any | None = None
            self.flushes = 0

        async def execute(self, statement: Any) -> Result:
            self.statement = statement
            return Result()

        async def flush(self) -> None:
            self.flushes += 1

    db = Db()
    claims = await WmsEffectStatusRepository().claim_due_batch(
        db,
        now=datetime(2026, 7, 24, 12, 0),
        lease_seconds=10,
        limit=10,
    )

    compiled = str(
        db.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert len(claims) == 1
    assert "status_binding_snapshot_hash IS NOT NULL" not in compiled
    assert "runtime_intent_logs.capability_key = 'wms.fulfillment.notify_pkg_binding'" in compiled
    assert "runtime_intent_logs.capability_contract_version = 'v1'" in compiled
    assert "system_outbox.operation_identity = 'wms.fulfillment.notify_pkg_binding@v1'" in compiled
    assert db.flushes == 1
