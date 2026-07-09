from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.effect_result import RuntimeIntentEffectResult
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
from src.app.runtime.orchestration.repositories.inbox_repository import WorklineInboxClaim
from src.app.runtime.orchestration.services.inbox import inbox_batch_processor as processor_module
from src.app.runtime.orchestration.services.inbox.inbox_batch_processor import InboxBatchProcessor

inbox_service_module = importlib.import_module("src.app.runtime.orchestration.services.inbox.inbox_service")


@pytest.mark.asyncio
async def test_process_claimed_message_counts_resource_retry_as_resource_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = SimpleNamespace(
        id=1,
        kind="DEVICE_EVENT",
        payload_json={"event_type": "SORTING_STATUS_CHANGED"},
        source_message_id="msg-001",
        trace_id="trace-resource-wait",
        event_id="evt-001",
        causation_id=None,
        workline_id=20,
        session_id=10,
        device_id=None,
        command_id=None,
        attempt_count=0,
    )
    session = SimpleNamespace(
        id=10,
        status="RUNNING",
        awaiting_device_command_code=None,
        current_wait_type=None,
        context_json={},
    )
    workline = SimpleNamespace(id=20)

    class FakeInboxRepository:
        async def get_by_id(self, db: object, inbox_id: int) -> object:
            assert inbox_id == 1
            return inbox

    class FakeInboxService:
        repo = FakeInboxRepository()

        async def park_for_retry(self, *args: object, **kwargs: object) -> object:
            return SimpleNamespace(id=1)

        async def mark_as_processed(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("RESOURCE_RETRY must not mark inbox as processed")

    class FakeOrchestratorService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def process_inbox(self, *args: object, write_callback: object, **kwargs: object) -> OrchestratorResult:
            result = OrchestratorResult(success=True, intents=[])
            await write_callback(result)
            return result

    class FakeWriteBackService:
        async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
            return RuntimeIntentEffectResult.resource_retry()

    class FakeDb:
        async def refresh(self, value: object) -> None:
            _ = value

        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    async def fake_load_related_entities(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "session": session,
            "workline": workline,
            "device": None,
            "command": None,
            "devices_by_role": {},
            "services": SimpleNamespace(),
            "safety_checked": True,
        }

    monkeypatch.setattr(inbox_service_module, "inbox_service", FakeInboxService())
    monkeypatch.setattr(processor_module, "_load_related_entities", fake_load_related_entities)
    monkeypatch.setattr(processor_module, "OrchestratorService", FakeOrchestratorService)

    result = await InboxBatchProcessor(write_back_service=FakeWriteBackService())._process_claimed_message(
        FakeDb(),
        WorklineInboxClaim(
            id=1,
            processor_token="token-001",
            received_at=None,
            session_id=10,
            workline_id=20,
            device_id=None,
            kind="DEVICE_EVENT",
            payload_json=inbox.payload_json,
            trace_id="trace-resource-wait",
        ),
    )

    assert result == {
        "processed": 1,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "resource_wait": 1,
    }
