"""北向只读运维聚合的 PostgreSQL 17 租户隔离合同。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from src.app.runtime.orchestration.repositories.northbound_operations_repository import (
    NorthboundOperationsRepository,
)
from src.app.sys.models import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.workline.models.workline import LineType, WorkLine
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _outbox(*, suffix: str, workline_id: int, created_at_offset_seconds: int) -> SystemOutbox:
    return SystemOutbox(
        workline_id=workline_id,
        operation_domain="NORTHBOUND_OBSERVABILITY_INTEGRATION",
        dispatch_type=SystemOutboxDispatchType.INTERNAL_SIGNAL,
        dispatch_key=f"northbound-operations:{suffix}:{uuid4().hex}",
        target_type=SystemOutboxTargetType.INTERNAL_SERVICE,
        target_code="northbound-operations-integration",
        provider_profile_identity="wms.2026-07-06.material-flow.production",
        operation_identity="wms.inventory.confirm_inbound@v1",
        payload_json={"must_not_be_read": f"tenant-secret-{suffix}"},
        status=SystemOutboxStatus.NEW,
        created_at=timezone.now_for_db() - timedelta(seconds=created_at_offset_seconds),
    )


@pytest.mark.asyncio
async def test_postgresql_snapshot_is_owner_scoped_and_reports_operation_mode(
    integration_db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex
    owner_workline = WorkLine(
        line_code=f"NB-OWNER-{suffix}",
        line_name="北向运维 owner scope",
        line_type=LineType.AUTO,
        created_by=101,
    )
    other_workline = WorkLine(
        line_code=f"NB-OTHER-{suffix}",
        line_name="北向运维 other scope",
        line_type=LineType.AUTO,
        created_by=202,
    )
    integration_db_session.add_all((owner_workline, other_workline))
    await integration_db_session.flush()
    integration_db_session.add_all(
        (
            _outbox(suffix=f"{suffix}:owner", workline_id=owner_workline.id, created_at_offset_seconds=30),
            _outbox(suffix=f"{suffix}:other", workline_id=other_workline.id, created_at_offset_seconds=90),
        )
    )
    await integration_db_session.flush()

    rows = await NorthboundOperationsRepository().load_snapshot(
        integration_db_session,
        tenant_id=101,
        workline_id=None,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.operation_identity == "wms.inventory.confirm_inbound@v1"
    assert row.mode == "EFFECT"
    assert row.backlog_count == 1
    assert 25 <= row.oldest_queue_age_seconds < 60
