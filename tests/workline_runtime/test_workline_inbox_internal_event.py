"""WorkLine 内部事件 Inbox 合同测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models.inbox import InboxKind, SourceSystem
from src.app.workline.services.inbox_batch_processor import _load_workline_entity, _should_resolve_session
from src.app.workline.services.inbox_service import WorklineInboxService
from src.workline_runtime.session_resolver import SessionResolver


class _FakeInboxRepo:
    def __init__(self) -> None:
        self.created_data: dict[str, object] | None = None
        self._by_idempotency_key: dict[str, object] = {}

    async def get_by_idempotency_key(self, db: object, idempotency_key: str) -> object | None:
        _ = db
        return self._by_idempotency_key.get(idempotency_key)

    async def create(self, db: object, data: dict[str, object]) -> object:
        _ = db
        self.created_data = data
        inbox = SimpleNamespace(id=1001 + len(self._by_idempotency_key), **data)
        idempotency_key = data.get("idempotency_key")
        if isinstance(idempotency_key, str):
            self._by_idempotency_key[idempotency_key] = inbox
        return inbox

    async def create_idempotent(
        self,
        db: object,
        data: dict[str, object],
        *,
        idempotency_key: str,
    ) -> object:
        existing = await self.get_by_idempotency_key(db, idempotency_key)
        if existing is not None:
            return existing
        return await self.create(db, data)


@pytest.mark.asyncio
async def test_create_internal_event_inbox_builds_routable_session_bound_envelope() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo()
    service.repo = fake_repo  # type: ignore[assignment]
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    inbox = await service.create_internal_event_inbox(
        db,
        event_type="SORTING_SOURCE_PICK_REQUESTED",
        data={
            "handoff_demand_id": 11,
            "handoff_source_item_id": 22,
            "claim_attempt_no": 2,
            "bin_code": "BIN-A",
            "bin_cell_code": "A01",
            "material_identity_key": "mat-1",
            "pkg_code": "PKG-1",
        },
        session_id=123,
        workline_id=456,
        trace_id="trace-handoff-1",
        causation_id="handoff-source-item:22",
        event_id="smt-inbound-handoff-source-item:22:claim:2",
    )

    created = fake_repo.created_data
    assert created is not None
    assert inbox.kind == InboxKind.INTERNAL_EVENT
    assert created["kind"] == InboxKind.INTERNAL_EVENT
    assert created["source_system"] == SourceSystem.SYSTEM
    assert created["session_id"] == 123
    assert created["workline_id"] == 456
    assert created["claim_bucket_key"] == "session:123"
    assert created["claim_bucket_key"] != "serial:unknown"
    assert (
        created["idempotency_key"]
        == "internal_event:SORTING_SOURCE_PICK_REQUESTED:smt-inbound-handoff-source-item:22:claim:2"
    )

    payload = created["payload_json"]
    assert isinstance(payload, dict)
    assert payload["message_type"] == "INTERNAL_EVENT"
    assert payload["event_type"] == "SORTING_SOURCE_PICK_REQUESTED"
    assert payload["canonical_event_type"] == "SORTING_SOURCE_PICK_REQUESTED"
    assert payload["event_id"] == "smt-inbound-handoff-source-item:22:claim:2"
    assert payload["causation_id"] == "handoff-source-item:22"
    assert payload["trace_id"] == "trace-handoff-1"
    assert payload["data"]["handoff_source_item_id"] == 22
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_internal_event_inbox_rejects_missing_handoff_correlation() -> None:
    service = WorklineInboxService()
    service.repo = _FakeInboxRepo()  # type: ignore[assignment]
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    with pytest.raises(ValueError, match="claim_attempt_no"):
        await service.create_internal_event_inbox(
            db,
            event_type="SORTING_SOURCE_PICK_REQUESTED",
            data={
                "handoff_demand_id": 11,
                "handoff_source_item_id": 22,
            },
            session_id=123,
            workline_id=456,
            trace_id="trace-handoff-1",
            causation_id="handoff-source-item:22",
            event_id="smt-inbound-handoff-source-item:22:claim:2",
        )

    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_internal_event_inbox_reuses_duplicate_without_rolling_back_outer_transaction() -> None:
    service = WorklineInboxService()
    service.repo = _FakeInboxRepo()  # type: ignore[assignment]
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    kwargs = {
        "event_type": "SORTING_SOURCE_PICK_REQUESTED",
        "data": {
            "handoff_demand_id": 11,
            "handoff_source_item_id": 22,
            "claim_attempt_no": 2,
        },
        "session_id": 123,
        "workline_id": 456,
        "trace_id": "trace-handoff-1",
        "causation_id": "handoff-source-item:22",
        "event_id": "smt-inbound-handoff-source-item:22:claim:2",
        "auto_commit": False,
    }

    first = await service.create_internal_event_inbox(db, **kwargs)
    second = await service.create_internal_event_inbox(db, **kwargs)

    assert second is first
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


def test_internal_event_batch_processor_resolves_by_existing_session_binding() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="INTERNAL_EVENT"),
        session_id=123,
        workline_id=456,
        payload_json={
            "message_type": "INTERNAL_EVENT",
            "event_type": "SORTING_SOURCE_PICK_REQUESTED",
            "canonical_event_type": "SORTING_SOURCE_PICK_REQUESTED",
            "data": {"handoff_source_item_id": 22},
        },
    )

    assert _should_resolve_session(inbox) is True


@pytest.mark.asyncio
async def test_internal_event_session_resolver_uses_session_id() -> None:
    expected_session = SimpleNamespace(id=123, workline_id=456)
    session_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=expected_session))
    resolver = SessionResolver(
        session_repo=session_repo,
        command_repo=SimpleNamespace(),
        outbox_repo=SimpleNamespace(),
        rack_task_repo=SimpleNamespace(),
        handling_step_repo=SimpleNamespace(),
        handling_operation_repo=SimpleNamespace(),
    )
    inbox = SimpleNamespace(
        kind=InboxKind.INTERNAL_EVENT,
        session_id=123,
        workline_id=456,
        payload_json={"message_type": "INTERNAL_EVENT", "event_type": "SORTING_SOURCE_PICK_REQUESTED", "data": {}},
    )
    db = object()

    session = await resolver.resolve_or_create(db, inbox, workline=None, devices_by_role={})  # type: ignore[arg-type]

    assert session is expected_session
    session_repo.get_by_id.assert_awaited_once_with(db, 123)


@pytest.mark.asyncio
async def test_internal_event_session_resolver_rejects_workline_mismatch() -> None:
    session_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(id=123, workline_id=456)))
    resolver = SessionResolver(
        session_repo=session_repo,
        command_repo=SimpleNamespace(),
        outbox_repo=SimpleNamespace(),
        rack_task_repo=SimpleNamespace(),
        handling_step_repo=SimpleNamespace(),
        handling_operation_repo=SimpleNamespace(),
    )
    inbox = SimpleNamespace(
        kind=InboxKind.INTERNAL_EVENT,
        session_id=123,
        workline_id=999,
        payload_json={"message_type": "INTERNAL_EVENT", "event_type": "SORTING_SOURCE_PICK_REQUESTED", "data": {}},
    )

    with pytest.raises(ValueError, match="workline_id mismatch"):
        await resolver.resolve_or_create(object(), inbox, workline=None, devices_by_role={})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_internal_event_loads_workline_from_session_ownership() -> None:
    calls: list[int] = []

    class _WorklineRepo:
        async def get_by_id(self, db: object, workline_id: int) -> object:
            _ = db
            calls.append(workline_id)
            return SimpleNamespace(id=workline_id)

    inbox = SimpleNamespace(
        kind=InboxKind.INTERNAL_EVENT,
        session_id=123,
        workline_id=None,
        payload_json={"message_type": "INTERNAL_EVENT", "event_type": "SORTING_SOURCE_PICK_REQUESTED", "data": {}},
    )
    session = SimpleNamespace(id=123, workline_id=456)

    workline = await _load_workline_entity(object(), inbox, session, _WorklineRepo())

    assert workline.id == 456
    assert calls == [456]


@pytest.mark.asyncio
async def test_internal_event_load_workline_rejects_inbox_session_workline_mismatch() -> None:
    class _WorklineRepo:
        async def get_by_id(self, db: object, workline_id: int) -> object:
            _ = db
            return SimpleNamespace(id=workline_id)

    inbox = SimpleNamespace(
        kind=InboxKind.INTERNAL_EVENT,
        session_id=123,
        workline_id=999,
        payload_json={"message_type": "INTERNAL_EVENT", "event_type": "SORTING_SOURCE_PICK_REQUESTED", "data": {}},
    )
    session = SimpleNamespace(id=123, workline_id=456)

    with pytest.raises(ValueError, match="workline_id mismatch"):
        await _load_workline_entity(object(), inbox, session, _WorklineRepo())
