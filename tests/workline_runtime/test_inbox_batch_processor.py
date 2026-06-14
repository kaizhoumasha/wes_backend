import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.app.workline.models.inbox import InboxKind
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.repositories.inbox_repository import WorklineInboxClaim
from src.app.workline.services.inbox_batch_processor import (
    _ENTRY_DEVICE_EVENT_TYPES,
    InboxBatchProcessor,
    ProcessResult,
    _backfill_workline_from_device,
    _entry_event_types_for_workline,
    _load_command_entity,
    _load_device_entity,
    _load_devices_by_role,
    _load_workline_entity,
    _load_workline_session,
)
from src.workline_runtime.diagnostics import ErrorCode
from src.workline_runtime.effect_result import RuntimeIntentEffectResult
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.plugin_manifest import EventCategory


@pytest.fixture
def mock_inbox_service():
    with patch("src.app.workline.services.inbox_service.inbox_service", new_callable=AsyncMock) as mock_inbox_service:
        claimed_by_id: dict[int, object] = {}

        async def _claim_pending_messages(_db: object, *, limit: int, processor_token: str, **kwargs):
            _ = _db, limit, kwargs
            messages = list(getattr(mock_inbox_service, "pending_messages", []))
            claimed_by_id.clear()
            for message in messages:
                claimed_by_id[int(message.id)] = message
            return [
                WorklineInboxClaim(
                    id=int(message.id),
                    processor_token=processor_token,
                    received_at=getattr(message, "received_at", None),
                    session_id=getattr(message, "session_id", None),
                    workline_id=getattr(message, "workline_id", None),
                    device_id=getattr(message, "device_id", None),
                    kind=getattr(message, "kind", InboxKind.DEVICE_EVENT),
                    payload_json=dict(getattr(message, "payload_json", {}) or {}),
                )
                for message in messages
            ]

        async def _get_by_id(_db: object, inbox_id: int):
            return claimed_by_id.get(inbox_id)

        mock_inbox_service.claim_pending_messages.side_effect = _claim_pending_messages
        mock_inbox_service.repo = SimpleNamespace(get_by_id=AsyncMock(side_effect=_get_by_id))
        yield mock_inbox_service


@pytest.fixture
def mock_db():
    return AsyncMock()


class _SessionContext:
    def __init__(self, db: object) -> None:
        self.db = db

    async def __aenter__(self) -> object:
        return self.db

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


def _session_factory_factory(created_sessions: list[object]):
    def _factory() -> _SessionContext:
        db = AsyncMock()
        created_sessions.append(db)
        return _SessionContext(db)

    return _factory


def _processor_for_db(db: object, *, write_back_service: object | None = None) -> InboxBatchProcessor:
    return InboxBatchProcessor(write_back_service=write_back_service, session_factory=lambda: _SessionContext(db))


class _CommitTrackingDb:
    def __init__(self) -> None:
        self.committed = False
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()

    async def commit(self) -> None:
        self.committed = True


class _CommitExpiredEntity:
    def __init__(self, db: _CommitTrackingDb, *, entity_id: int, **attrs: object) -> None:
        self._db = db
        self._entity_id = entity_id
        self.__dict__.update(attrs)

    @property
    def id(self) -> int:
        if self._db.committed:
            raise RuntimeError("greenlet_spawn has not been called; can't call await_only() here")
        return self._entity_id


def _claim(
    inbox_id: int,
    *,
    device_id: int = 101,
    processor_token: str | None = None,
) -> WorklineInboxClaim:
    return WorklineInboxClaim(
        id=inbox_id,
        processor_token=processor_token or f"processor-{inbox_id}",
        received_at=None,
        session_id=None,
        workline_id=1,
        device_id=device_id,
        kind=InboxKind.DEVICE_EVENT,
        payload_json={},
    )


@pytest.mark.asyncio
async def test_process_batch_returns_empty_stats_on_zero_limit(mock_db, mock_inbox_service):
    mock_inbox_service.pending_messages = []

    processor = InboxBatchProcessor()
    result = await processor.process_batch(mock_db, limit=0)

    assert result == {
        "processed": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "resource_wait": 0,
    }


@pytest.mark.asyncio
async def test_process_batch_does_not_accept_parallelism_kwarg(mock_db, mock_inbox_service):
    processor = InboxBatchProcessor()

    with pytest.raises(TypeError):
        await processor.process_batch(mock_db, limit=1, parallelism=2)  # type: ignore[call-arg]

    mock_inbox_service.claim_pending_messages.assert_not_awaited()


def test_processor_does_not_accept_bucket_lock_provider() -> None:
    with pytest.raises(TypeError):
        InboxBatchProcessor(bucket_lock_provider=object())  # type: ignore[call-arg]


def test_entry_event_types_for_workline_derive_only_from_entry_device_events(monkeypatch) -> None:
    import src.app.workline.services.inbox_batch_processor as processor_module

    manifest = SimpleNamespace(
        events=(
            SimpleNamespace(event="ENTRY_SCAN", category=EventCategory.ENTRY_DEVICE),
            SimpleNamespace(event="INTERNAL_RETRY", category=EventCategory.INTERNAL),
            SimpleNamespace(event="COMMAND_DONE", category=EventCategory.COMMAND_RESULT),
            SimpleNamespace(event="OPERATOR_OVERRIDE", category="OPERATOR"),
            SimpleNamespace(event="SAFETY_STOPPED", category="SAFETY"),
        )
    )
    monkeypatch.setattr(
        processor_module,
        "get_workline_plugin_definition",
        lambda plugin_key: SimpleNamespace(manifest=manifest) if plugin_key == "manifest_events" else None,
    )

    assert _entry_event_types_for_workline(SimpleNamespace(plugin_key="manifest_events")) == frozenset({"ENTRY_SCAN"})


def test_entry_event_types_for_workline_falls_back_for_unknown_plugin(monkeypatch) -> None:
    import src.app.workline.services.inbox_batch_processor as processor_module

    monkeypatch.setattr(processor_module, "get_workline_plugin_definition", lambda plugin_key: None)

    assert _entry_event_types_for_workline(SimpleNamespace(plugin_key="missing_plugin")) == _ENTRY_DEVICE_EVENT_TYPES


def test_entry_event_types_for_workline_keeps_explicit_no_entry_manifest_empty(monkeypatch) -> None:
    import src.app.workline.services.inbox_batch_processor as processor_module

    manifest = SimpleNamespace(
        events=(
            SimpleNamespace(event="INTERNAL_RETRY", category=EventCategory.INTERNAL),
            SimpleNamespace(event="COMMAND_DONE", category=EventCategory.COMMAND_RESULT),
        )
    )
    monkeypatch.setattr(
        processor_module,
        "get_workline_plugin_definition",
        lambda plugin_key: SimpleNamespace(manifest=manifest) if plugin_key == "no_entry_plugin" else None,
    )

    assert _entry_event_types_for_workline(SimpleNamespace(plugin_key="no_entry_plugin")) == frozenset()


@pytest.mark.asyncio
async def test_process_batch_claims_one_message_at_a_time_until_limit(mock_db, mock_inbox_service):
    claim_calls: list[int] = []
    claim_batches = [
        [_claim(1, device_id=101)],
        [_claim(2, device_id=202)],
        [_claim(3, device_id=303)],
    ]

    async def _claim_pending_messages(_db: object, *, limit: int, **kwargs):
        _ = _db, kwargs
        claim_calls.append(limit)
        return claim_batches.pop(0) if claim_batches else []

    async def _process(_db: object, _claim_item: WorklineInboxClaim) -> ProcessResult:
        return {"processed": 1, "success": 1, "failed": 0, "skipped": 0}

    mock_inbox_service.claim_pending_messages.side_effect = _claim_pending_messages
    processor = InboxBatchProcessor()
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    result = await processor.process_batch(mock_db, limit=3)

    assert result == {"processed": 3, "success": 3, "failed": 0, "skipped": 0, "resource_wait": 0}
    assert claim_calls == [1, 1, 1]


@pytest.mark.asyncio
async def test_process_batch_processes_claims_sequentially(mock_db, mock_inbox_service):
    claim_batches = [
        [_claim(1, device_id=101)],
        [_claim(2, device_id=202)],
    ]
    process_order: list[int] = []
    running = 0
    max_running = 0

    async def _claim_pending_messages(_db: object, *, limit: int, **kwargs):
        _ = _db, limit, kwargs
        return claim_batches.pop(0) if claim_batches else []

    async def _process(_db: object, claim: WorklineInboxClaim) -> ProcessResult:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.01)
        process_order.append(claim.id)
        running -= 1
        return {"processed": 1, "success": 1, "failed": 0, "skipped": 0}

    mock_inbox_service.claim_pending_messages.side_effect = _claim_pending_messages
    processor = InboxBatchProcessor()
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    result = await processor.process_batch(mock_db, limit=2)

    assert result["processed"] == 2
    assert max_running == 1
    assert process_order == [1, 2]


@pytest.mark.asyncio
async def test_process_batch_stops_when_claim_returns_empty(mock_db, mock_inbox_service):
    claim_calls: list[int] = []
    claim_batches = [
        [_claim(1, device_id=101)],
        [],
    ]

    async def _claim_pending_messages(_db: object, *, limit: int, **kwargs):
        _ = _db, kwargs
        claim_calls.append(limit)
        return claim_batches.pop(0) if claim_batches else []

    async def _process(_db: object, _claim_item: WorklineInboxClaim) -> ProcessResult:
        return {"processed": 1, "success": 1, "failed": 0, "skipped": 0}

    mock_inbox_service.claim_pending_messages.side_effect = _claim_pending_messages
    processor = InboxBatchProcessor()
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    result = await processor.process_batch(mock_db, limit=3)

    assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
    assert claim_calls == [1, 1]


@pytest.mark.asyncio
async def test_process_batch_propagates_cancelled_claim_without_processed_stats(mock_db, mock_inbox_service):
    claims = [_claim(1, device_id=101)]
    mock_inbox_service.claim_pending_messages.side_effect = None
    mock_inbox_service.claim_pending_messages.return_value = claims

    async def _process(_db: object, _claim: WorklineInboxClaim) -> ProcessResult:
        raise asyncio.CancelledError

    processor = InboxBatchProcessor()
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await processor.process_batch(mock_db, limit=1)

    mock_inbox_service.mark_as_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_related_entity_primary_key_loads_use_base_repository_get_by_id(mock_db):
    session = SimpleNamespace(id=10, workline_id=20)
    workline = SimpleNamespace(id=20)
    command = SimpleNamespace(id=30)
    device = SimpleNamespace(id=40)
    inbox = SimpleNamespace(session_id=10, workline_id=20, command_id=30, device_id=40, payload_json={})

    session_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=session))
    workline_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=workline))
    command_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=command), get_by_command_code=AsyncMock())
    device_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=device), get_by_device_code=AsyncMock())

    assert await _load_workline_session(mock_db, inbox, session_repo) is session
    assert await _load_workline_entity(mock_db, inbox, session, workline_repo) is workline
    assert await _load_command_entity(mock_db, inbox, command_repo) is command
    assert await _load_device_entity(mock_db, inbox, device_repo) is device

    session_repo.get_by_id.assert_awaited_once_with(mock_db, 10)
    workline_repo.get_by_id.assert_awaited_once_with(mock_db, 20)
    command_repo.get_by_id.assert_awaited_once_with(mock_db, 30)
    device_repo.get_by_id.assert_awaited_once_with(mock_db, 40)
    command_repo.get_by_command_code.assert_not_called()
    device_repo.get_by_device_code.assert_not_called()


@pytest.mark.asyncio
async def test_load_device_entity_uses_device_code_lookup(mock_db):
    device = SimpleNamespace(id=40, device_code="SCN-01")
    inbox = SimpleNamespace(device_id=None, payload_json={"device_code": "SCN-01"})
    device_repo = SimpleNamespace(get_by_id=AsyncMock(), get_by_device_code=AsyncMock(return_value=device))

    assert await _load_device_entity(mock_db, inbox, device_repo) is device

    device_repo.get_by_device_code.assert_awaited_once_with(mock_db, "SCN-01")
    device_repo.get_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_workline_uses_device_work_line_id(mock_db):
    workline = SimpleNamespace(id=20)
    inbox = SimpleNamespace(workline_id=None)
    device = SimpleNamespace(id=40, work_line_id=20)
    workline_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=workline))

    assert await _backfill_workline_from_device(mock_db, inbox, device, workline_repo) is workline

    assert inbox.workline_id == 20
    workline_repo.get_by_id.assert_awaited_once_with(mock_db, 20)


@pytest.mark.asyncio
async def test_load_devices_by_role_uses_work_line_lookup(mock_db):
    workline = SimpleNamespace(id=20)
    scanner = SimpleNamespace(id=1, device_role="SCANNER")
    robot = SimpleNamespace(id=2, device_role="ROBOT")
    ignored = SimpleNamespace(id=3, device_role="")
    device_repo = SimpleNamespace(get_by_work_line_id=AsyncMock(return_value=[scanner, robot, ignored]))

    assert await _load_devices_by_role(mock_db, workline, device_repo) == {
        "SCANNER": [scanner],
        "ROBOT": [robot],
    }

    device_repo.get_by_work_line_id.assert_awaited_once_with(mock_db, 20)


@pytest.mark.asyncio
async def test_process_batch_scan_completed_missing_payload(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.payload_json = {"event_type": "SCAN_COMPLETED"}
    mock_inbox_service.pending_messages = [inbox]

    with patch(
        "src.app.workline.services.inbox_batch_processor._record_diagnostic", new_callable=AsyncMock
    ) as mock_record_diag:
        processor = _processor_for_db(mock_db)
        result = await processor.process_batch(mock_db, limit=1)

        assert result["failed"] == 1
        mock_inbox_service.mark_as_failed.assert_awaited_once_with(
            mock_db,
            1,
            "SCAN_COMPLETED 缺少条码信息（HHPN/MfrPN/Qty/DateCode/LotCode/PkgID 或 barcode）",
            processor_token=ANY,
            auto_commit=False,
        )
        mock_record_diag.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_batch_estop_pressed_no_workline(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.payload_json = {"event_type": "ESTOP_PRESSED"}
    mock_inbox_service.pending_messages = [inbox]

    with patch(
        "src.app.workline.services.inbox_batch_processor._load_related_entities", new_callable=AsyncMock
    ) as mock_load:
        mock_load.return_value = {
            "session": None,
            "workline": None,
            "device": None,
            "command": None,
            "devices_by_role": {},
        }

        with patch(
            "src.app.workline.services.inbox_batch_processor._record_diagnostic", new_callable=AsyncMock
        ) as mock_record_diag:
            processor = _processor_for_db(mock_db)
            result = await processor.process_batch(mock_db, limit=1)

            assert result["failed"] == 1
            mock_inbox_service.mark_as_failed.assert_awaited_once()
            mock_record_diag.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_batch_estop_pressed_success(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.payload_json = {"event_type": "ESTOP_PRESSED"}
    mock_inbox_service.pending_messages = [inbox]

    with patch(
        "src.app.workline.services.inbox_batch_processor._load_related_entities", new_callable=AsyncMock
    ) as mock_load:
        workline = MagicMock()
        workline.id = 100
        mock_load.return_value = {
            "session": None,
            "workline": workline,
            "device": MagicMock(id=200),
            "command": None,
            "devices_by_role": {},
        }

        with patch(
            "src.app.workline.services.safety_service.workline_safety_service.handle_estop", new_callable=AsyncMock
        ) as mock_handle_estop:
            mock_handle_estop.return_value = MagicMock(id=999)

            processor = _processor_for_db(mock_db)
            result = await processor.process_batch(mock_db, limit=1)

            assert result["success"] == 1
            mock_inbox_service.mark_as_processed.assert_awaited_once_with(
                mock_db, 1, processor_token=ANY, auto_commit=False
            )


@pytest.mark.asyncio
async def test_process_batch_timer_timeout(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.kind = MagicMock()
    inbox.kind.value = "TIMER_TIMEOUT"
    inbox.payload_json = {"event_type": "TIMER_TIMEOUT"}
    mock_inbox_service.pending_messages = [inbox]

    with patch(
        "src.app.workline.services.inbox_batch_processor._load_related_entities", new_callable=AsyncMock
    ) as mock_load:
        workline = MagicMock()
        mock_load.return_value = {
            "session": MagicMock(),
            "workline": workline,
            "device": None,
            "command": None,
            "devices_by_role": {},
            "safety_checked": True,
        }

        with patch(
            "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service.handle_timer_timeout",
            new_callable=AsyncMock,
        ) as mock_handle:
            processor = _processor_for_db(mock_db)
            result = await processor.process_batch(mock_db, limit=1)

            assert result["success"] == 1
            mock_handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_batch_missing_context(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.payload_json = {"event_type": "SOME_EVENT"}
    mock_inbox_service.pending_messages = [inbox]

    with patch(
        "src.app.workline.services.inbox_batch_processor._load_related_entities", new_callable=AsyncMock
    ) as mock_load:
        mock_load.return_value = {
            "session": None,
            "workline": None,
            "device": None,
            "command": None,
            "devices_by_role": {},
            "safety_checked": True,
        }

        with patch(
            "src.app.workline.services.inbox_batch_processor._record_diagnostic", new_callable=AsyncMock
        ) as mock_record_diag:
            processor = _processor_for_db(mock_db)
            result = await processor.process_batch(mock_db, limit=1)

            assert result["failed"] == 1
            mock_inbox_service.mark_as_failed.assert_awaited_once()
            mock_record_diag.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_batch_duplicate_entry(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.payload_json = {"event_type": "SOME_EVENT"}
    mock_inbox_service.pending_messages = [inbox]

    with patch(
        "src.app.workline.services.inbox_batch_processor._load_related_entities", new_callable=AsyncMock
    ) as mock_load:
        mock_load.return_value = {
            "session": MagicMock(),
            "workline": MagicMock(),
            "device": None,
            "command": None,
            "devices_by_role": {},
            "safety_checked": True,
        }

        with patch(
            "src.app.workline.services.inbox_batch_processor._is_duplicate_entry_event_for_session", return_value=True
        ):
            with patch(
                "src.app.workline.services.inbox_batch_processor._duplicate_entry_material_conflict", return_value=None
            ):
                with patch(
                    "src.app.workline.services.inbox_batch_processor._record_duplicate_entry_archive_timeline",
                    new_callable=AsyncMock,
                ) as mock_record:
                    processor = _processor_for_db(mock_db)
                    result = await processor.process_batch(mock_db, limit=1)

                    assert result["success"] == 1
                    mock_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_batch_late_command(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.payload_json = {"event_type": "SOME_EVENT"}
    mock_inbox_service.pending_messages = [inbox]

    with patch(
        "src.app.workline.services.inbox_batch_processor._load_related_entities", new_callable=AsyncMock
    ) as mock_load:
        mock_load.return_value = {
            "session": MagicMock(),
            "workline": MagicMock(),
            "device": None,
            "command": None,
            "devices_by_role": {},
            "safety_checked": True,
        }

        with patch(
            "src.app.workline.services.inbox_batch_processor._is_duplicate_entry_event_for_session", return_value=False
        ):
            with patch(
                "src.app.workline.services.inbox_batch_processor._is_late_or_duplicate_command_result_for_session",
                return_value=True,
            ):
                with patch(
                    "src.app.workline.services.inbox_batch_processor._record_late_command_result_archive_timeline",
                    new_callable=AsyncMock,
                ) as mock_record:
                    processor = _processor_for_db(mock_db)
                    result = await processor.process_batch(mock_db, limit=1)

                    assert result["success"] == 1
                    mock_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_batch_uses_injected_write_back_service(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.trace_id = "trace-1"
    inbox.payload_json = {"event_type": "SOME_EVENT"}
    session = SimpleNamespace(id=10, status="RUNNING", awaiting_command_id=None)
    workline = SimpleNamespace(id=20)
    injected_write_back = SimpleNamespace(write_back=AsyncMock(return_value=RuntimeIntentEffectResult.processed()))
    mock_inbox_service.pending_messages = [inbox]

    class FakeOrchestratorService:
        def __init__(self, *args, **kwargs):
            pass

        async def process_inbox(
            self,
            *args: object,
            write_callback: Callable[[OrchestratorResult], Awaitable[None]],
            **kwargs: object,
        ) -> OrchestratorResult:
            _ = args, kwargs
            result = OrchestratorResult(success=True, intents=[])
            await write_callback(result)
            return result

    with (
        patch(
            "src.app.workline.services.inbox_batch_processor._load_related_entities",
            new_callable=AsyncMock,
        ) as mock_load,
        patch(
            "src.app.workline.services.inbox_batch_processor._is_duplicate_entry_event_for_session",
            return_value=False,
        ),
        patch(
            "src.app.workline.services.inbox_batch_processor._is_late_or_duplicate_command_result_for_session",
            return_value=False,
        ),
        patch("src.app.workline.services.inbox_batch_processor.OrchestratorService", FakeOrchestratorService),
        patch(
            "src.app.workline.services.write_back_service.orchestrator_write_back_service.write_back",
            new_callable=AsyncMock,
        ) as default_write_back,
    ):
        mock_load.return_value = {
            "session": session,
            "workline": workline,
            "device": None,
            "command": None,
            "devices_by_role": {},
            "services": {},
            "safety_checked": True,
        }

        processor = _processor_for_db(mock_db, write_back_service=injected_write_back)
        result = await processor.process_batch(mock_db, limit=1)

    assert result["success"] == 1
    injected_write_back.write_back.assert_awaited_once()
    default_write_back.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_retry_disposition_parks_inbox_and_does_not_mark_processed(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.trace_id = "trace-resource-wait"
    inbox.payload_json = {"event_type": "SOME_EVENT"}
    session = SimpleNamespace(
        id=10, status="WAITING_EXTERNAL", current_wait_type="RESOURCE_WAIT", awaiting_command_id=None
    )
    workline = SimpleNamespace(id=20)
    injected_write_back = SimpleNamespace(write_back=AsyncMock(return_value=RuntimeIntentEffectResult.resource_retry()))
    mock_inbox_service.pending_messages = [inbox]

    class FakeOrchestratorService:
        def __init__(self, *args, **kwargs):
            pass

        async def process_inbox(
            self,
            *args: object,
            write_callback: Callable[[OrchestratorResult], Awaitable[None]],
            **kwargs: object,
        ) -> OrchestratorResult:
            _ = args, kwargs
            result = OrchestratorResult(success=True, intents=[])
            await write_callback(result)
            return result

    with (
        patch(
            "src.app.workline.services.inbox_batch_processor._load_related_entities",
            new_callable=AsyncMock,
        ) as mock_load,
        patch(
            "src.app.workline.services.inbox_batch_processor._is_duplicate_entry_event_for_session",
            return_value=False,
        ),
        patch(
            "src.app.workline.services.inbox_batch_processor._is_late_or_duplicate_command_result_for_session",
            return_value=False,
        ),
        patch("src.app.workline.services.inbox_batch_processor.OrchestratorService", FakeOrchestratorService),
    ):
        mock_load.return_value = {
            "session": session,
            "workline": workline,
            "device": None,
            "command": None,
            "devices_by_role": {},
            "services": {},
            "safety_checked": True,
        }

        processor = _processor_for_db(mock_db, write_back_service=injected_write_back)
        result = await processor.process_batch(mock_db, limit=1)

    assert result == {"processed": 1, "success": 0, "failed": 0, "skipped": 0, "resource_wait": 1}
    mock_inbox_service.park_for_retry.assert_awaited_once()
    mock_inbox_service.mark_as_processed.assert_not_awaited()


@pytest.mark.asyncio
async def test_processed_resource_wait_resolves_diagnostic_and_clears_session_context(db_session, mock_inbox_service):
    session = WorklineSession(
        session_code="test-resource-wait-resolved",
        workline_id=20,
        plugin_key="test_plugin",
        status=SessionStatus.RUNNING,
        context_json={"resource_wait": {"inbox_id": 1, "resource_key": "station:S1"}, "keep": "value"},
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    inbox = MagicMock()
    inbox.id = 1
    inbox.trace_id = "trace-resource-wait-resolved"
    inbox.payload_json = {"event_type": "SOME_EVENT"}
    workline = SimpleNamespace(id=20)
    injected_write_back = SimpleNamespace(write_back=AsyncMock(return_value=RuntimeIntentEffectResult.processed()))
    mock_inbox_service.pending_messages = [inbox]

    class FakeOrchestratorService:
        def __init__(self, *args, **kwargs):
            pass

        async def process_inbox(
            self,
            *args: object,
            write_callback: Callable[[OrchestratorResult], Awaitable[None]],
            **kwargs: object,
        ) -> OrchestratorResult:
            _ = args, kwargs
            result = OrchestratorResult(success=True, intents=[])
            await write_callback(result)
            return result

    with (
        patch(
            "src.app.workline.services.inbox_batch_processor._load_related_entities",
            new_callable=AsyncMock,
        ) as mock_load,
        patch(
            "src.app.workline.services.inbox_batch_processor._is_duplicate_entry_event_for_session",
            return_value=False,
        ),
        patch(
            "src.app.workline.services.inbox_batch_processor._is_late_or_duplicate_command_result_for_session",
            return_value=False,
        ),
        patch("src.app.workline.services.inbox_batch_processor.OrchestratorService", FakeOrchestratorService),
        patch(
            "src.app.workline.services.diagnostic_service.workline_diagnostic_service.resolve_resource_wait_diagnostics",
            new_callable=AsyncMock,
        ) as resolve_diagnostic,
        patch("src.app.sys.services.event_stream_service.defer_sse_event"),
        patch(
            "src.app.sys.services.event_stream_service.publish_deferred_sse_events",
            new_callable=AsyncMock,
        ),
    ):
        mock_load.return_value = {
            "session": session,
            "workline": workline,
            "device": None,
            "command": None,
            "devices_by_role": {},
            "services": {},
            "safety_checked": True,
        }

        processor = _processor_for_db(db_session, write_back_service=injected_write_back)
        result = await processor.process_batch(db_session, limit=1)

    assert result["success"] == 1
    resolve_diagnostic.assert_awaited_once_with(
        db_session,
        inbox_id=1,
        resource_key="station:S1",
        auto_commit=False,
    )
    await db_session.refresh(session)
    assert session.context_json == {"keep": "value"}


@pytest.mark.asyncio
async def test_process_batch_sse_payload_uses_precommit_identity_snapshot(mock_inbox_service):
    db = _CommitTrackingDb()
    inbox = MagicMock()
    inbox.id = 1
    inbox.trace_id = "trace-1"
    inbox.payload_json = {"event_type": "SOME_EVENT"}
    session = _CommitExpiredEntity(db, entity_id=10, status="RUNNING", awaiting_command_id=None)
    workline = _CommitExpiredEntity(db, entity_id=20)
    injected_write_back = SimpleNamespace(write_back=AsyncMock(return_value=RuntimeIntentEffectResult.processed()))
    mock_inbox_service.pending_messages = [inbox]

    class FakeOrchestratorService:
        def __init__(self, *args, **kwargs):
            pass

        async def process_inbox(
            self,
            *args: object,
            write_callback: Callable[[OrchestratorResult], Awaitable[None]],
            **kwargs: object,
        ) -> OrchestratorResult:
            _ = args, kwargs
            result = OrchestratorResult(success=True, intents=[])
            await write_callback(result)
            return result

    with (
        patch(
            "src.app.workline.services.inbox_batch_processor._load_related_entities",
            new_callable=AsyncMock,
        ) as mock_load,
        patch(
            "src.app.workline.services.inbox_batch_processor._is_duplicate_entry_event_for_session",
            return_value=False,
        ),
        patch(
            "src.app.workline.services.inbox_batch_processor._is_late_or_duplicate_command_result_for_session",
            return_value=False,
        ),
        patch("src.app.workline.services.inbox_batch_processor.OrchestratorService", FakeOrchestratorService),
        patch("src.app.sys.services.event_stream_service.defer_sse_event") as defer_sse_event,
        patch(
            "src.app.sys.services.event_stream_service.publish_deferred_sse_events",
            new_callable=AsyncMock,
        ),
    ):
        mock_load.return_value = {
            "session": session,
            "workline": workline,
            "device": None,
            "command": None,
            "devices_by_role": {},
            "services": {},
            "safety_checked": True,
        }

        processor = _processor_for_db(db, write_back_service=injected_write_back)
        result = await processor.process_batch(db, limit=1)

    assert result["success"] == 1
    mock_inbox_service.mark_as_failed.assert_not_awaited()
    defer_sse_event.assert_called_once()
    event_payload = defer_sse_event.call_args.args[2]
    assert event_payload["keys"] == {"workline_id": 20, "session_id": 10}


@pytest.mark.asyncio
async def test_process_batch_workline_safety_blocked(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.attempt_count = 2
    inbox.payload_json = {"event_type": "SOME_EVENT"}
    mock_inbox_service.pending_messages = [inbox]
    mock_inbox_service.repo.get_by_id.return_value = inbox

    from src.app.workline.services.safety_service import WorkLineSafetyBlocked

    with patch(
        "src.app.workline.services.inbox_batch_processor._load_related_entities", new_callable=AsyncMock
    ) as mock_load:
        mock_load.side_effect = WorkLineSafetyBlocked("Workline is stopped")

        with patch(
            "src.app.workline.services.inbox_batch_processor._record_diagnostic", new_callable=AsyncMock
        ) as mock_record_diag:
            processor = _processor_for_db(mock_db)
            result = await processor.process_batch(mock_db, limit=1)

            assert result["failed"] == 1
            mock_inbox_service.park_for_retry.assert_awaited_once_with(
                mock_db, 1, "Workline is stopped", processor_token=ANY, auto_commit=False, delay_seconds=40
            )
            mock_record_diag.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_batch_exception_diagnostic_snapshot_exposes_payload_json(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.trace_id = "trace-exception-snapshot"
    inbox.payload_json = {"event_type": "COMMAND_RESULT", "command_code": "CMD-1"}
    mock_inbox_service.pending_messages = [inbox]
    mock_inbox_service.repo.get_by_id.return_value = inbox

    with patch(
        "src.app.workline.services.inbox_batch_processor._load_related_entities",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        with patch(
            "src.app.workline.services.inbox_batch_processor._record_diagnostic", new_callable=AsyncMock
        ) as mock_record_diag:
            processor = _processor_for_db(mock_db)
            result = await processor.process_batch(mock_db, limit=1)

    assert result["failed"] == 1
    diagnostic_inbox = mock_record_diag.await_args.kwargs["inbox"]
    assert diagnostic_inbox.payload_json == {"event_type": "COMMAND_RESULT", "command_code": "CMD-1"}


@pytest.mark.asyncio
async def test_process_batch_orchestrator_failure_records_diagnostic_with_snapshot(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.trace_id = "trace-orchestrator-failure"
    inbox.payload_json = {"event_type": "COMMAND_RESULT", "command_code": "CMD-1"}
    session = SimpleNamespace(id=10, status="RUNNING", awaiting_command_id=None)
    workline = SimpleNamespace(id=20)
    mock_inbox_service.pending_messages = [inbox]
    mock_inbox_service.repo.get_by_id.return_value = inbox

    class FakeOrchestratorService:
        def __init__(self, *args, **kwargs):
            pass

        async def process_inbox(self, *args, **kwargs):
            return OrchestratorResult(success=False, error="rack task failed")

    with (
        patch(
            "src.app.workline.services.inbox_batch_processor._load_related_entities",
            new_callable=AsyncMock,
        ) as mock_load,
        patch(
            "src.app.workline.services.inbox_batch_processor._is_duplicate_entry_event_for_session",
            return_value=False,
        ),
        patch(
            "src.app.workline.services.inbox_batch_processor._is_late_or_duplicate_command_result_for_session",
            return_value=False,
        ),
        patch("src.app.workline.services.inbox_batch_processor.OrchestratorService", FakeOrchestratorService),
        patch(
            "src.app.workline.services.inbox_batch_processor._record_diagnostic", new_callable=AsyncMock
        ) as mock_record_diag,
    ):
        mock_load.return_value = {
            "session": session,
            "workline": workline,
            "device": None,
            "command": None,
            "devices_by_role": {},
            "services": {},
            "safety_checked": True,
        }

        processor = _processor_for_db(mock_db)
        result = await processor.process_batch(mock_db, limit=1)

    assert result["failed"] == 1
    diagnostic_inbox = mock_record_diag.await_args.kwargs["inbox"]
    assert diagnostic_inbox.payload_json == {"event_type": "COMMAND_RESULT", "command_code": "CMD-1"}
    assert diagnostic_inbox is not inbox
