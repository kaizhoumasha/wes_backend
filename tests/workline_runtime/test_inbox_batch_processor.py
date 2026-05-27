from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.services.inbox_batch_processor import (
    InboxBatchProcessor,
    _backfill_workline_from_device,
    _load_command_entity,
    _load_device_entity,
    _load_devices_by_role,
    _load_workline_entity,
    _load_workline_session,
)
from src.workline_runtime.diagnostics import ErrorCode


@pytest.fixture
def mock_inbox_service():
    with patch("src.app.workline.services.inbox_service.inbox_service", new_callable=AsyncMock) as mock_inbox_service:
        yield mock_inbox_service


@pytest.fixture
def mock_db():
    return AsyncMock()


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
        processor = InboxBatchProcessor()
        result = await processor.process_batch(mock_db, limit=1)

        assert result["failed"] == 1
        mock_inbox_service.mark_as_failed.assert_awaited_once_with(
            mock_db,
            1,
            "SCAN_COMPLETED 缺少条码信息（HHPN/MfrPN/Qty/DateCode/LotCode/PkgID 或 barcode）",
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
            processor = InboxBatchProcessor()
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

            processor = InboxBatchProcessor()
            result = await processor.process_batch(mock_db, limit=1)

            assert result["success"] == 1
            mock_inbox_service.mark_as_processed.assert_awaited_once_with(mock_db, 1, auto_commit=False)


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
            processor = InboxBatchProcessor()
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
            processor = InboxBatchProcessor()
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
                    processor = InboxBatchProcessor()
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
                    processor = InboxBatchProcessor()
                    result = await processor.process_batch(mock_db, limit=1)

                    assert result["success"] == 1
                    mock_record.assert_awaited_once()
