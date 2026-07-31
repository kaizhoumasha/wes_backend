"""SystemOutbox canonical payload 的 PostgreSQL BYTEA 往返合同。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlmodel import select

from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType
from tests.support.external_http import frozen_external_http_binding

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
    frozen_binding = frozen_external_http_binding(
        operation_identity="tests.canonical-roundtrip.effect@v1",
        target_code="WMS_TEST_CANONICAL_ROUNDTRIP",
    )
    dispatch_key = f"canonical-roundtrip:{uuid4().hex}"
    idempotency_key = f"intent:{uuid4().hex}"
    integration_db_session.add(
        SystemOutbox(
            **frozen_binding.as_persisted_fields(),
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            dispatch_key=dispatch_key,
            idempotency_key=idempotency_key,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
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
    assert persisted.idempotency_key == idempotency_key
    assert persisted.provider_profile_hash == frozen_binding.provider_profile_hash
    assert persisted.binding_revision == frozen_binding.binding_revision
    assert persisted.target_snapshot_json == frozen_binding.target_snapshot.as_json()
    assert persisted.target_snapshot_hash == frozen_binding.target_snapshot_hash
    assert persisted.auth_scheme == "HMAC_SHA256"
    assert persisted.credential_reference == frozen_binding.credential_reference


@pytest.mark.asyncio
async def test_external_http_frozen_binding_cannot_change_after_insert(
    integration_db_session: AsyncSession,
) -> None:
    projection = {"request_id": f"IMMUTABLE-{uuid4().hex}"}
    canonical = CanonicalPayload.from_projection(projection)
    frozen_binding = frozen_external_http_binding(
        operation_identity="tests.frozen-binding.effect@v1",
        target_code="WMS_TEST_FROZEN_BINDING",
    )
    outbox = SystemOutbox(
        **frozen_binding.as_persisted_fields(),
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=f"frozen-binding-immutable:{uuid4().hex}",
        idempotency_key=f"intent:{uuid4().hex}",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        payload_json=projection,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        operation_domain="HANDLING",
    )
    integration_db_session.add(outbox)
    await integration_db_session.flush()

    outbox.credential_reference = "secret://wms/legacy-transport-production-hmac@v2"
    with pytest.raises(ValueError, match="scheduling identity persisted fields are immutable"):
        await integration_db_session.flush()


@pytest.mark.asyncio
async def test_external_http_idempotency_key_cannot_change_after_insert(
    integration_db_session: AsyncSession,
) -> None:
    projection = {"request_id": f"IMMUTABLE-IDEMPOTENCY-{uuid4().hex}"}
    canonical = CanonicalPayload.from_projection(projection)
    frozen_binding = frozen_external_http_binding(
        operation_identity="tests.idempotency-immutable.effect@v1",
        target_code="WMS_TEST_IDEMPOTENCY_IMMUTABLE",
    )
    outbox = SystemOutbox(
        **frozen_binding.as_persisted_fields(),
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=f"idempotency-immutable:{uuid4().hex}",
        idempotency_key=f"intent:{uuid4().hex}",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        payload_json=projection,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        operation_domain="HANDLING",
    )
    integration_db_session.add(outbox)
    await integration_db_session.flush()

    outbox.idempotency_key = f"mutated:{uuid4().hex}"
    with pytest.raises(ValueError, match="scheduling identity persisted fields are immutable"):
        await integration_db_session.flush()
