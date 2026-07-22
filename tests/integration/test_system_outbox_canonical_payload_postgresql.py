"""SystemOutbox canonical payload 的 PostgreSQL BYTEA 往返合同。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlmodel import select

from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_external_http_canonical_bytes_round_trip_exactly(integration_db_session: AsyncSession) -> None:
    projection = {
        "request_id": f"REQ-{uuid4().hex}",
        "quantity": "1.2300",
        "labels": ["入库", "📦"],
    }
    canonical = CanonicalPayload.from_projection(projection)
    dispatch_key = f"canonical-roundtrip:{uuid4().hex}"
    integration_db_session.add(
        SystemOutbox(
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            dispatch_key=dispatch_key,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS_RCS_BIN_OPERATION",
            payload_json=projection,
            canonical_payload_bytes=canonical.body,
            payload_hash=canonical.sha256,
            operation_domain="HANDLING",
        )
    )
    await integration_db_session.flush()
    integration_db_session.expire_all()

    persisted = await integration_db_session.scalar(
        select(SystemOutbox).where(SystemOutbox.dispatch_key == dispatch_key)
    )

    assert persisted is not None
    assert persisted.canonical_payload_bytes == canonical.body
    assert persisted.payload_hash == canonical.sha256
    assert persisted.payload_json == projection
