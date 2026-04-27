from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models.inbox import InboxKind
from src.app.workline.models.session import SessionStatus


class _InboxRepoStub:
    def __init__(self, original: object | None = None) -> None:
        self.original = original
        self.created: dict[str, Any] | None = None
        self.get_by_id = AsyncMock(return_value=original)
        self.create = AsyncMock(side_effect=self._create)

    async def _create(self, _db: object, data: dict[str, Any]) -> Any:
        self.created = data
        return SimpleNamespace(id=88, **data)


class _SessionRepoStub:
    def __init__(self, session: object | None = None) -> None:
        self.session = session
        self.get_by_id = AsyncMock(return_value=session)


@pytest.mark.asyncio
async def test_replay_clones_original_inbox_for_runtime_processing_and_does_not_mutate_original() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    original_payload = {"message_type": "DEVICE_EVENT", "device_code": "ARM01"}
    original = SimpleNamespace(
        id=10,
        kind=InboxKind.DEVICE_EVENT,
        payload_json=original_payload,
        trace_id="trace-001",
        event_id="event-original",
        causation_id=None,
        workline_id=1,
        session_id=2,
        source_message_id="req-001",
    )
    inbox_repo = _InboxRepoStub(original)
    service = WorklineOperationService(inbox_repo=cast("Any", inbox_repo))

    replay = await service.replay_inbox(
        object(), inbox_id=10, reason="重新诊断", operator_id="ops-1", auto_commit=False
    )

    assert replay.id == 88
    assert inbox_repo.created is not None
    assert inbox_repo.created["kind"] == InboxKind.DEVICE_EVENT
    assert inbox_repo.created["trace_id"] == "trace-001"
    assert inbox_repo.created["event_id"].startswith("replay:event-original:")
    assert inbox_repo.created["causation_id"] == "event-original"
    assert inbox_repo.created["payload_json"]["replay_of_event_id"] == "event-original"
    assert inbox_repo.created["payload_json"]["message_type"] == original_payload["message_type"]
    assert original.payload_json == original_payload


@pytest.mark.asyncio
async def test_manual_operation_requires_open_session_state() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    session = SimpleNamespace(id=20, status=SessionStatus.COMPLETED, workline_id=1, trace_id="trace-closed")
    service = WorklineOperationService(
        inbox_repo=cast("Any", _InboxRepoStub()),
        session_repo=cast("Any", _SessionRepoStub(session)),
    )

    with pytest.raises(ValueError, match="当前会话状态不允许人工操作"):
        await service.create_manual_operation(
            object(),
            session_id=20,
            operation="HOLD",
            operator_id="ops-1",
            reason="需要检查",
            auto_commit=False,
        )
