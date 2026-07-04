"""RuntimeLocationEvent 位置事实合同。"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.app.runtime.orchestration.models.runtime_location_event import RuntimeLocationEvent
from src.app.runtime.orchestration.services.runtime_location_event_service import RuntimeLocationEventService
from src.utils.timezone import timezone


async def _event_count(db_session) -> int:
    result = await db_session.execute(select(func.count()).select_from(RuntimeLocationEvent))
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_runtime_location_event_records_idempotently_and_keeps_append_only_history(db_session) -> None:
    """同 idempotency_key 重放复用事实，不同 key 追加历史事实。"""

    service = RuntimeLocationEventService()
    occurred_at = timezone.now_for_db()

    first = await service.record(
        db_session,
        object_type="PKG",
        object_key="PKG-P4-001",
        location_scope="BIN_CELL",
        location_code="BIN-P4-001:C01",
        business_step="SORTER_INBOUND",
        source="ECS",
        evidence_json={"source_event_id": "evt-location-1"},
        correlation_id="corr-location-001",
        source_event_id="evt-location-1",
        source_version="1",
        idempotency_key="idem-location-1",
        external_reference_type="WMS_DOCUMENT",
        external_reference_value="wms-doc-001",
        provider_code="WMS",
        occurred_at=occurred_at,
        auto_commit=False,
    )
    replay = await service.record(
        db_session,
        object_type="PKG",
        object_key="PKG-P4-001",
        location_scope="BIN_CELL",
        location_code="BIN-P4-001:C01",
        business_step="SORTER_INBOUND",
        source="ECS",
        evidence_json={"source_event_id": "evt-location-1"},
        correlation_id="corr-location-001",
        source_event_id="evt-location-1",
        source_version="1",
        idempotency_key="idem-location-1",
        external_reference_type="WMS_DOCUMENT",
        external_reference_value="wms-doc-001",
        provider_code="WMS",
        occurred_at=occurred_at,
        auto_commit=False,
    )
    moved = await service.record(
        db_session,
        object_type="PKG",
        object_key="PKG-P4-001",
        location_scope="WORK_POSITION",
        location_code="WP-P4-01",
        business_step="SORTER_CONFIRM",
        source="ECS",
        evidence_json={"source_event_id": "evt-location-2"},
        correlation_id="corr-location-001",
        source_event_id="evt-location-2",
        source_version="2",
        idempotency_key="idem-location-2",
        external_reference_type="WMS_DOCUMENT",
        external_reference_value="wms-doc-001",
        provider_code="WMS",
        occurred_at=timezone.now_for_db(),
        auto_commit=False,
    )

    assert replay.id == first.id
    assert moved.id != first.id
    assert await _event_count(db_session) == 2

    by_object = await service.list_by_object(db_session, object_type="PKG", object_key="PKG-P4-001")
    assert [event.location_code for event in by_object] == ["BIN-P4-001:C01", "WP-P4-01"]

    by_correlation = await service.list_by_correlation_id(db_session, correlation_id="corr-location-001")
    assert [event.id for event in by_correlation] == [first.id, moved.id]

    by_external_reference = await service.list_by_external_reference(
        db_session,
        external_reference_type="WMS_DOCUMENT",
        external_reference_value="wms-doc-001",
        provider_code="WMS",
    )
    assert [event.id for event in by_external_reference] == [first.id, moved.id]
