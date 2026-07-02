"""Outbox dispatch production observability contract tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxTargetType


@pytest.mark.asyncio
async def test_outbox_dispatch_single_emits_runtime_intent_dispatch_observability(monkeypatch) -> None:
    """Outbox 进入物理派发出口时必须发出 runtime_intent.dispatch 稳定观测信号。"""

    from src.app.runtime.orchestration.services.inbox.outbox_dispatch_service import OutboxDispatchService

    outbox = SimpleNamespace(
        id=201,
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_FULFILLMENT",
        dispatch_key="wms-fulfillment:REQ-201",
        operation_domain="WORKLINE",
        operation_key="REQ-201",
        session_id=31,
        workline_id=41,
        device_id=None,
        trace_id="trace-dispatch-201",
        payload_json={
            "correlation_id": "corr-dispatch-201",
            "provider_code": "WMS",
            "operation_kind": "fulfillment",
        },
    )
    service = OutboxDispatchService()
    monkeypatch.setattr(service, "_dispatch_external_http", AsyncMock(return_value=True))
    emitted: list[tuple[str, dict[str, object]]] = []

    def emit(name: str, attributes: dict[str, object]) -> object:
        emitted.append((name, attributes))
        return object()

    monkeypatch.setattr(
        "src.app.runtime.orchestration.observability.runtime_observability_registry.emit",
        emit,
    )

    dispatched = await service._dispatch_single(db=object(), outbox=outbox)

    assert dispatched is True
    assert emitted == [
        (
            "runtime_intent.dispatch",
            {
                "trace_id": "trace-dispatch-201",
                "correlation_id": "corr-dispatch-201",
                "provider_code": "WMS",
                "operation_kind": "fulfillment",
            },
        )
    ]
