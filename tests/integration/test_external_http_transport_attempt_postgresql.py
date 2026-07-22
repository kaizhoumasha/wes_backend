"""EXTERNAL_HTTP typed attempt evidence 的 PostgreSQL 往返合同。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlmodel import select

from src.app.runtime.orchestration.models.dispatch_attempt import (
    DispatchAttemptStatus,
    WorklineDispatchAttempt,
)
from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import (
    workline_dispatch_attempt_service,
)
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.external_http_transport import ExternalHttpTransportPhase, ExternalHttpTransportResult
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_ambiguous_external_http_attempt_evidence_round_trips(integration_db_session: AsyncSession) -> None:
    dispatch_key = f"typed-transport-attempt:{uuid4().hex}"
    projection = {"request_id": dispatch_key}
    canonical = CanonicalPayload.from_projection(projection)
    outbox = SystemOutbox(
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=dispatch_key,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_RCS_BIN_OPERATION",
        provider_profile_identity="wms.legacy-transport.production",
        operation_identity="wms.transport.handling@v1",
        payload_json=projection,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        operation_domain="HANDLING",
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token="integration-attempt-owner",
        lease_expires_at=timezone.now_for_db() + timedelta(minutes=5),
    )
    integration_db_session.add(outbox)
    await integration_db_session.flush()
    attempt = await workline_dispatch_attempt_service.create_attempt(
        integration_db_session,
        outbox=outbox,
        auto_commit=False,
    )
    transport_result = ExternalHttpTransportResult.ambiguous(
        phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
        error_code="READ_TIMEOUT",
        error_message="response timeout",
    )
    await workline_dispatch_attempt_service.finalize_external_http_attempt_record(
        integration_db_session,
        attempt=attempt,
        lease_owner_token="integration-attempt-owner",
        result=transport_result,
        outbox_finalization="unknown",
        auto_commit=False,
    )
    await integration_db_session.flush()
    attempt_id = attempt.id
    integration_db_session.expire_all()

    persisted = await integration_db_session.scalar(
        select(WorklineDispatchAttempt).where(WorklineDispatchAttempt.id == attempt_id)
    )

    assert persisted is not None
    assert persisted.status is DispatchAttemptStatus.UNKNOWN
    assert persisted.transport_outcome == "AMBIGUOUS"
    assert persisted.transport_phase == "AWAITING_RESPONSE"
    assert persisted.protocol_result == "NOT_AVAILABLE"
    assert persisted.safe_to_retry is False
    assert persisted.http_status_code is None
    assert persisted.response_json == {
        "transport": transport_result.evidence_json(),
        "outbox_finalization": "unknown",
    }
