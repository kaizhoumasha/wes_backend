"""Runtime external fulfillment idempotency hot-path tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from src.app.runtime.orchestration.effect_result import WriteBackDisposition
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.app.runtime.orchestration.models.session import SessionStatus
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_effects import RuntimeIntentEffectApplier
from src.app.runtime.orchestration.services.idempotency_guard import IdempotencyConflict
from src.app.sys.models import SystemOutbox
from src.app.wms_integration.services.redaction import canonical_sha256
from src.app.workline.services.write_back_service import _build_effect_apply_context


class _CollectingDb:
    """把 outbox add 留在内存, 其余数据库操作委托给真实 AsyncSession。"""

    def __init__(self, db_session: Any) -> None:
        self._db_session = db_session
        self.added: list[Any] = []

    @property
    def no_autoflush(self) -> Any:
        return self._db_session.no_autoflush

    def add(self, value: Any) -> None:
        self.added.append(value)

    def get_bind(self) -> Any:
        return self._db_session.get_bind()

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return await self._db_session.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db_session, name)


async def _seed_execution_correlation(db_session: Any, *, correlation_id: str) -> ExecutionCorrelation:
    session = ExecutionSession(workline_id=1, manifest_version="v1", state="RUNNING")
    db_session.add(session)
    await db_session.flush()
    correlation = ExecutionCorrelation(
        correlation_id=correlation_id,
        execution_session_id=session.id,
        trace_id=f"trace-{correlation_id}",
    )
    db_session.add(correlation)
    await db_session.flush()
    return correlation


def _ctx(db: Any, correlation: ExecutionCorrelation) -> dict[str, Any]:
    session = SimpleNamespace(
        id=31,
        workline_id=41,
        status=SessionStatus.RUNNING.value,
        plugin_key="runtime-test",
        contract_version="v1",
        context_json={},
        trace_id=correlation.trace_id,
        current_wait_type=None,
        waiting_since=None,
        deadline_at=None,
        current_wait_timeout_seconds=None,
        awaiting_device_command_code=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
        ended_at=None,
    )
    workline = SimpleNamespace(id=41, plugin_key="runtime-test", contract_version="v1")
    inbox = SimpleNamespace(
        id=501,
        trace_id=correlation.trace_id,
        payload_json={"correlation_id": correlation.correlation_id, "event_type": "DEVICE_EVENT"},
    )
    return _build_effect_apply_context(
        db=db,
        session=session,
        workline=workline,
        inbox=inbox,
        devices_by_role={},
        source_device=None,
        orch_result=OrchestratorResult(success=True, intents=[]),
    )


def _fulfillment_intent(*, payload: dict[str, Any]) -> RuntimeIntent:
    return RuntimeIntent.external_request(
        dispatch_key="wms-fulfillment:REQ-501",
        target_code="WMS_FULFILLMENT",
        source_system="WMS",
        payload=payload,
        timeout_seconds=30,
    )


@pytest.mark.asyncio
async def test_external_wms_fulfillment_request_claims_idempotency_before_outbox(db_session) -> None:
    """WMS fulfillment 实际发起热路径必须先 claim 幂等键再创建 outbox。"""

    correlation = await _seed_execution_correlation(db_session, correlation_id="corr-external-fulfillment")
    db = _CollectingDb(db_session)
    payload = {
        "correlation_id": correlation.correlation_id,
        "request_id": "wms-fulfillment:REQ-501",
        "provider_code": "WMS",
        "operation_kind": "fulfillment",
        "fulfillment_kind": "FULL_BOX_EXCHANGE",
        "box_code": "BOX-501",
    }

    result = await RuntimeIntentEffectApplier().apply(_ctx(db, correlation), [_fulfillment_intent(payload=payload)])

    assert result.disposition is WriteBackDisposition.PROCESSED
    outboxes = [item for item in db.added if isinstance(item, SystemOutbox)]
    assert len(outboxes) == 1
    assert outboxes[0].dispatch_key == "wms-fulfillment:REQ-501"
    stored = (
        await db_session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.provider_code == "WMS",
                IdempotencyKey.operation_kind == "fulfillment",
                IdempotencyKey.idempotency_key == "wms-fulfillment:REQ-501",
            )
        )
    ).scalar_one()
    assert stored.execution_correlation_id == correlation.correlation_id
    assert stored.request_hash == canonical_sha256(payload)
    assert stored.business_owner_key == "fulfillment:wms-fulfillment:REQ-501"


@pytest.mark.asyncio
async def test_external_wms_fulfillment_request_match_skips_outbox(db_session) -> None:
    """同 fulfillment 幂等键同 payload 重放必须跳过 outbox 创建。"""

    correlation = await _seed_execution_correlation(db_session, correlation_id="corr-external-fulfillment-replay")
    payload = {
        "correlation_id": correlation.correlation_id,
        "request_id": "wms-fulfillment:REQ-501",
        "provider_code": "WMS",
        "operation_kind": "fulfillment",
        "fulfillment_kind": "FULL_BOX_EXCHANGE",
        "box_code": "BOX-501",
    }
    first_db = _CollectingDb(db_session)
    await RuntimeIntentEffectApplier().apply(_ctx(first_db, correlation), [_fulfillment_intent(payload=payload)])

    replay_db = _CollectingDb(db_session)
    result = await RuntimeIntentEffectApplier().apply(
        _ctx(replay_db, correlation),
        [_fulfillment_intent(payload=payload)],
    )

    assert result.disposition is WriteBackDisposition.PROCESSED
    assert [item for item in replay_db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_external_wms_fulfillment_request_rejects_same_key_different_payload_before_outbox(
    db_session,
) -> None:
    """同 fulfillment request_id 不同 payload 必须 409, 且不创建第二条 outbox。"""

    correlation = await _seed_execution_correlation(db_session, correlation_id="corr-external-fulfillment-conflict")
    first_payload = {
        "correlation_id": correlation.correlation_id,
        "request_id": "wms-fulfillment:REQ-501",
        "provider_code": "WMS",
        "operation_kind": "fulfillment",
        "fulfillment_kind": "FULL_BOX_EXCHANGE",
        "box_code": "BOX-501",
    }
    tampered_payload = {**first_payload, "box_code": "BOX-TAMPERED"}
    first_db = _CollectingDb(db_session)
    await RuntimeIntentEffectApplier().apply(_ctx(first_db, correlation), [_fulfillment_intent(payload=first_payload)])

    second_db = _CollectingDb(db_session)
    with pytest.raises(IdempotencyConflict) as exc_info:
        await RuntimeIntentEffectApplier().apply(
            _ctx(second_db, correlation),
            [_fulfillment_intent(payload=tampered_payload)],
        )

    assert [item for item in second_db.added if isinstance(item, SystemOutbox)] == []
    audit_event = exc_info.value.to_audit_event()
    assert audit_event["normalized_operation_kind"] == "fulfillment"
    assert audit_event["domain"] == "wms_integration"
    assert audit_event["status_code"] == 409
    assert audit_event["incoming_request_hash"] == canonical_sha256(tampered_payload)
