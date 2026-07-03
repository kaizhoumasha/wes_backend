"""WorklineInbox production observability contract tests."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.models.inbox import InboxKind
from src.app.runtime.orchestration.repositories.inbox_repository import WorklineInboxClaim


@pytest.mark.asyncio
async def test_workline_inbox_claim_emits_runtime_observability(monkeypatch) -> None:
    """claim 成功提交后必须发出 runtime_inbox.claim 稳定观测信号。"""

    from src.app.runtime.orchestration.services.inbox.inbox_service import WorklineInboxService

    claim = WorklineInboxClaim(
        id=101,
        processor_token="worker-1",
        received_at=datetime(2026, 7, 2, 9, 0, 0),
        session_id=11,
        workline_id=22,
        device_id=33,
        kind=InboxKind.DEVICE_EVENT,
        payload_json={"correlation_id": "corr-101"},
        claim_bucket_key="session:11",
        trace_id="trace-101",
    )
    repo = SimpleNamespace(claim_pending_messages=AsyncMock(return_value=[claim]))
    service = WorklineInboxService()
    service.repo = repo
    commit = AsyncMock()
    monkeypatch.setattr(service, "_commit_inbox_mutation", commit)
    emitted: list[tuple[str, dict[str, object]]] = []

    def emit(name: str, attributes: dict[str, object]) -> object:
        emitted.append((name, attributes))
        return object()

    monkeypatch.setattr(
        "src.app.runtime.orchestration.observability.runtime_observability_registry.emit",
        emit,
    )

    claims = await service.claim_pending_messages(
        db=object(),
        limit=1,
        processor_token="worker-1",
        auto_commit=True,
    )

    assert claims == [claim]
    repo.claim_pending_messages.assert_awaited_once()
    commit.assert_awaited_once()
    assert emitted == [
        (
            "runtime_inbox.claim",
            {
                "trace_id": "trace-101",
                "correlation_id": "corr-101",
                "operation_kind": "DEVICE_EVENT",
                "inbox_id": 101,
            },
        )
    ]
