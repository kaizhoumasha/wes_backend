import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.app.workline.constants import WORKLINE_INBOX_PROCESSING_STALE_SECONDS
from src.app.workline.models.inbox import InboxKind
from src.app.workline.repositories.inbox_repository import WorklineInboxClaim
from src.app.workline.services.inbox_batch_processor import (
    InboxBatchProcessor,
    ProcessResult,
    _backfill_workline_from_device,
    _build_inbox_bucket_lock_provider,
    _inbox_bucket_lock_ttl_seconds,
    _load_command_entity,
    _load_device_entity,
    _load_devices_by_role,
    _load_workline_entity,
    _load_workline_session,
)
from src.workline_runtime.diagnostics import ErrorCode
from src.workline_runtime.orchestrator import OrchestratorResult


@pytest.fixture
def mock_inbox_service():
    with patch("src.app.workline.services.inbox_service.inbox_service", new_callable=AsyncMock) as mock_inbox_service:
        claimed_by_id: dict[int, object] = {}

        async def _claim_pending_messages(_db: object, *, limit: int, processor_token: str, **kwargs):
            _ = _db, limit, kwargs
            messages = mock_inbox_service.get_new_messages.return_value
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
    mock_inbox_service.get_new_messages.return_value = []

    processor = InboxBatchProcessor()
    result = await processor.process_batch(mock_db, limit=0)

    assert result == {
        "processed": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
    }


@pytest.mark.asyncio
async def test_process_batch_parallelism_two_uses_distinct_bucket_sessions(mock_db, mock_inbox_service):
    claims = [
        WorklineInboxClaim(
            id=1,
            processor_token="token",
            received_at=None,
            session_id=None,
            workline_id=1,
            device_id=101,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={},
        ),
        WorklineInboxClaim(
            id=2,
            processor_token="token",
            received_at=None,
            session_id=None,
            workline_id=1,
            device_id=202,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={},
        ),
    ]
    mock_inbox_service.claim_pending_messages.side_effect = None
    mock_inbox_service.claim_pending_messages.return_value = claims
    created_sessions: list[object] = []
    seen_sessions: list[object] = []

    async def _process(db: object, claim: WorklineInboxClaim) -> ProcessResult:
        _ = claim
        seen_sessions.append(db)
        return {"processed": 1, "success": 1, "failed": 0, "skipped": 0}

    processor = InboxBatchProcessor(session_factory=_session_factory_factory(created_sessions))
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    result = await processor.process_batch(mock_db, limit=2, parallelism=2)

    assert result == {"processed": 2, "success": 2, "failed": 0, "skipped": 0}
    assert len(created_sessions) == 2
    assert seen_sessions == created_sessions


@pytest.mark.asyncio
async def test_process_batch_same_device_bucket_is_serial(mock_db, mock_inbox_service):
    claims = [
        WorklineInboxClaim(
            id=1,
            processor_token="token",
            received_at=None,
            session_id=None,
            workline_id=1,
            device_id=101,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={},
        ),
        WorklineInboxClaim(
            id=2,
            processor_token="token",
            received_at=None,
            session_id=None,
            workline_id=1,
            device_id=101,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={},
        ),
    ]
    mock_inbox_service.claim_pending_messages.side_effect = None
    mock_inbox_service.claim_pending_messages.return_value = claims
    running = 0
    max_running = 0

    async def _process(_db: object, _claim: WorklineInboxClaim) -> ProcessResult:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.01)
        running -= 1
        return {"processed": 1, "success": 1, "failed": 0, "skipped": 0}

    processor = InboxBatchProcessor(session_factory=_session_factory_factory([]))
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    result = await processor.process_batch(mock_db, limit=2, parallelism=2)

    assert result["processed"] == 2
    assert max_running == 1


@pytest.mark.asyncio
async def test_process_batch_different_device_buckets_can_run_concurrently(mock_db, mock_inbox_service):
    claims = [
        WorklineInboxClaim(
            id=1,
            processor_token="token",
            received_at=None,
            session_id=None,
            workline_id=1,
            device_id=101,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={},
        ),
        WorklineInboxClaim(
            id=2,
            processor_token="token",
            received_at=None,
            session_id=None,
            workline_id=1,
            device_id=202,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={},
        ),
    ]
    mock_inbox_service.claim_pending_messages.side_effect = None
    mock_inbox_service.claim_pending_messages.return_value = claims
    running = 0
    max_running = 0

    async def _process(_db: object, _claim: WorklineInboxClaim) -> ProcessResult:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.01)
        running -= 1
        return {"processed": 1, "success": 1, "failed": 0, "skipped": 0}

    processor = InboxBatchProcessor(session_factory=_session_factory_factory([]))
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    result = await processor.process_batch(mock_db, limit=2, parallelism=2)

    assert result["processed"] == 2
    assert max_running == 2


@pytest.mark.asyncio
async def test_process_batch_claims_in_parallelism_sized_waves(mock_db, mock_inbox_service):
    claim_calls: list[int] = []
    claim_batches = [
        [_claim(1, device_id=101), _claim(2, device_id=202)],
        [_claim(3, device_id=303), _claim(4, device_id=404)],
        [_claim(5, device_id=505)],
    ]

    async def _claim_pending_messages(_db: object, *, limit: int, **kwargs):
        _ = _db, kwargs
        claim_calls.append(limit)
        return claim_batches.pop(0) if claim_batches else []

    async def _process(_db: object, _claim_item: WorklineInboxClaim) -> ProcessResult:
        return {"processed": 1, "success": 1, "failed": 0, "skipped": 0}

    mock_inbox_service.claim_pending_messages.side_effect = _claim_pending_messages
    processor = InboxBatchProcessor(session_factory=_session_factory_factory([]))
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    result = await processor.process_batch(mock_db, limit=5, parallelism=2)

    assert result == {"processed": 5, "success": 5, "failed": 0, "skipped": 0}
    assert claim_calls == [2, 2, 1]


@pytest.mark.asyncio
async def test_process_batch_continues_after_short_claim_wave(mock_db, mock_inbox_service):
    claim_calls: list[int] = []
    claim_batches = [
        [_claim(1, device_id=101)],
        [_claim(2, device_id=101)],
        [_claim(3, device_id=101)],
    ]

    async def _claim_pending_messages(_db: object, *, limit: int, **kwargs):
        _ = _db, kwargs
        claim_calls.append(limit)
        return claim_batches.pop(0) if claim_batches else []

    async def _process(_db: object, _claim_item: WorklineInboxClaim) -> ProcessResult:
        return {"processed": 1, "success": 1, "failed": 0, "skipped": 0}

    mock_inbox_service.claim_pending_messages.side_effect = _claim_pending_messages
    processor = InboxBatchProcessor(session_factory=_session_factory_factory([]))
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    result = await processor.process_batch(mock_db, limit=3, parallelism=4)

    assert result == {"processed": 3, "success": 3, "failed": 0, "skipped": 0}
    assert claim_calls == [3, 2, 1]


@pytest.mark.asyncio
async def test_process_batch_bucket_lock_serializes_same_bucket_across_processors(mock_db, mock_inbox_service):
    lock_by_key: dict[str, asyncio.Lock] = {}
    running = 0
    max_running = 0

    @asynccontextmanager
    async def _bucket_lock(_db: object, bucket_key: str):
        lock = lock_by_key.setdefault(bucket_key, asyncio.Lock())
        async with lock:
            yield

    async def _process(_db: object, _claim_item: WorklineInboxClaim) -> ProcessResult:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.01)
        running -= 1
        return {"processed": 1, "success": 1, "failed": 0, "skipped": 0}

    processor_a = InboxBatchProcessor(
        session_factory=_session_factory_factory([]),
        bucket_lock_provider=_bucket_lock,
    )
    processor_b = InboxBatchProcessor(
        session_factory=_session_factory_factory([]),
        bucket_lock_provider=_bucket_lock,
    )
    processor_a._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]
    processor_b._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    async def _claim_for_a(_db: object, *, limit: int, processor_token: str, **kwargs):
        _ = _db, limit, processor_token, kwargs
        return [_claim(1, device_id=101)]

    async def _claim_for_b(_db: object, *, limit: int, processor_token: str, **kwargs):
        _ = _db, limit, processor_token, kwargs
        return [_claim(2, device_id=101)]

    with patch("src.app.workline.services.inbox_service.inbox_service", new_callable=AsyncMock) as service_a:
        service_a.claim_pending_messages.side_effect = _claim_for_a
        task_a = asyncio.create_task(processor_a.process_batch(mock_db, limit=1, parallelism=1))
        await asyncio.sleep(0)
        with patch("src.app.workline.services.inbox_service.inbox_service", new_callable=AsyncMock) as service_b:
            service_b.claim_pending_messages.side_effect = _claim_for_b
            task_b = asyncio.create_task(processor_b.process_batch(mock_db, limit=1, parallelism=1))
            result_a, result_b = await asyncio.gather(task_a, task_b)

    assert result_a["processed"] == 1
    assert result_b["processed"] == 1
    assert max_running == 1


@pytest.mark.asyncio
async def test_inbox_bucket_pg_degraded_provider_does_not_hold_session_advisory_lock():
    mock_db = AsyncMock()

    with patch("src.app.workline.services.inbox_batch_processor.get_redis", return_value=None):
        provider = _build_inbox_bucket_lock_provider(mock_db)
        async with provider("device:101"):
            await mock_db.commit()

    mock_db.execute.assert_not_called()
    mock_db.commit.assert_awaited_once()


def test_inbox_bucket_redis_lock_ttl_does_not_exceed_stale_reclaim_window():
    assert _inbox_bucket_lock_ttl_seconds() <= WORKLINE_INBOX_PROCESSING_STALE_SECONDS


@pytest.mark.asyncio
async def test_process_batch_terminalizes_claims_when_bucket_lock_fails(mock_db, mock_inbox_service):
    claims = [
        WorklineInboxClaim(
            id=1,
            processor_token="token-1",
            received_at=None,
            session_id=None,
            workline_id=1,
            device_id=101,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={},
        )
    ]
    mock_inbox_service.claim_pending_messages.side_effect = None
    mock_inbox_service.claim_pending_messages.return_value = claims

    @asynccontextmanager
    async def _failing_bucket_lock(_db: object, _bucket_key: str):
        raise RuntimeError("bucket lock down")
        yield

    async def _process(_db: object, _claim: WorklineInboxClaim) -> ProcessResult:
        raise AssertionError("message handler must not run when bucket setup fails")

    processor = InboxBatchProcessor(
        session_factory=_session_factory_factory([]),
        bucket_lock_provider=_failing_bucket_lock,
    )
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    result = await processor.process_batch(mock_db, limit=1, parallelism=1)

    assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0}
    mock_inbox_service.mark_as_failed.assert_awaited_once_with(
        mock_db,
        1,
        "Inbox bucket processing failed before message handler: bucket lock down",
        processor_token="token-1",
        auto_commit=False,
    )
    mock_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_process_batch_propagates_cancelled_bucket_without_processed_stats(mock_db, mock_inbox_service):
    claims = [
        WorklineInboxClaim(
            id=1,
            processor_token="token",
            received_at=None,
            session_id=None,
            workline_id=1,
            device_id=101,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={},
        )
    ]
    mock_inbox_service.claim_pending_messages.side_effect = None
    mock_inbox_service.claim_pending_messages.return_value = claims

    async def _process(_db: object, _claim: WorklineInboxClaim) -> ProcessResult:
        raise asyncio.CancelledError

    processor = InboxBatchProcessor(session_factory=_session_factory_factory([]))
    processor._process_claimed_message = AsyncMock(side_effect=_process)  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await processor.process_batch(mock_db, limit=1, parallelism=1)

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
    mock_inbox_service.get_new_messages.return_value = [inbox]

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
    mock_inbox_service.get_new_messages.return_value = [inbox]

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
    mock_inbox_service.get_new_messages.return_value = [inbox]

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
    mock_inbox_service.get_new_messages.return_value = [inbox]

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
    mock_inbox_service.get_new_messages.return_value = [inbox]

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
    mock_inbox_service.get_new_messages.return_value = [inbox]

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
    mock_inbox_service.get_new_messages.return_value = [inbox]

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
    injected_write_back = SimpleNamespace(write_back=AsyncMock())
    mock_inbox_service.get_new_messages.return_value = [inbox]

    class FakeOrchestratorService:
        def __init__(self, *args, **kwargs):
            pass

        async def process_inbox(self, *args, write_callback, **kwargs):
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
async def test_process_batch_workline_safety_blocked(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.attempt_count = 2
    inbox.payload_json = {"event_type": "SOME_EVENT"}
    mock_inbox_service.get_new_messages.return_value = [inbox]
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
