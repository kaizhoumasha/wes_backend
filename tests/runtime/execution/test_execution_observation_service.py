"""可靠事实只读映射，不依赖 Redis、worker 或业务插件。"""

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.execution.services.execution_observation_service import ExecutionObservationService


@asynccontextmanager
async def sessions():
    yield object()


@pytest.mark.asyncio
@pytest.mark.parametrize("updated", [None, datetime(2026, 9, 9, 12)])
async def test_confirmation_mapping_preserves_unfinished_response_and_identity(updated):
    now = datetime(2026, 9, 9, 12)
    record = SimpleNamespace(
        operation="sample.action@v1",
        operation_id="original",
        status="RECONCILING",
        attempt_count=2,
        retry_eligible=False,
        next_attempt_at=None,
        deadline_at=now,
        last_dispatch_at=now,
        response_evidence_id=7,
        response_result="RECEIVED",
        updated_at=updated,
    )
    repository = SimpleNamespace(get_confirmation=AsyncMock(return_value=record))
    service = ExecutionObservationService(sessions, repository)
    result = await service.get_confirmation(record.operation, record.operation_id)
    assert result.status == "RECONCILING"
    assert result.response_result == "RECEIVED"
    assert result.deadline_at == "2026-09-09T12:00:00+00:00"
    assert result.updated_at == ("2026-09-09T12:00:00+00:00" if updated else None)
    assert result.next_attempt_at is None
    assert result.attempt_count == 2
    assert repository.get_confirmation.call_args.args[1:] == (record.operation, "original")


@pytest.mark.asyncio
async def test_evidence_mapping_does_not_infer_application_or_publication():
    now = datetime(2026, 9, 9, 12)
    record = SimpleNamespace(
        operation="sample.action@v1",
        operation_id="original",
        apply_status="PENDING",
        received_at=now,
        processed_at=None,
        published_at=None,
        decision_attempt_count=0,
        decision_next_attempt_at=None,
    )
    repository = SimpleNamespace(get_evidence=AsyncMock(return_value=record))
    result = await ExecutionObservationService(sessions, repository).get_evidence(record.operation, "original")
    assert result.apply_status == "PENDING"
    assert result.processed_at is None
    assert result.published_at is None
    assert "reason_code" not in result.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_confirmation", "get_evidence"])
async def test_absence_is_not_a_synthetic_state(method):
    repository = SimpleNamespace(**{method: AsyncMock(return_value=None)})
    assert await getattr(ExecutionObservationService(sessions, repository), method)("sample@v1", "original") is None
