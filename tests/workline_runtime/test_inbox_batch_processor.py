from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.services.inbox_batch_processor import InboxBatchProcessor
from src.celery_app.tasks.workline import ErrorCode


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
async def test_process_batch_scan_completed_missing_payload(mock_db, mock_inbox_service):
    inbox = MagicMock()
    inbox.id = 1
    inbox.payload_json = {"event_type": "SCAN_COMPLETED"}
    mock_inbox_service.get_new_messages.return_value = [inbox]

    # Must patch _record_diagnostic
    with patch("src.celery_app.tasks.workline._record_diagnostic", new_callable=AsyncMock) as mock_record_diag:
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

    with patch("src.celery_app.tasks.workline._load_related_entities", new_callable=AsyncMock) as mock_load:
        mock_load.return_value = {
            "session": None,
            "workline": None,
            "device": None,
            "command": None,
            "devices_by_role": {},
        }

        with patch("src.celery_app.tasks.workline._record_diagnostic", new_callable=AsyncMock) as mock_record_diag:
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

    with patch("src.celery_app.tasks.workline._load_related_entities", new_callable=AsyncMock) as mock_load:
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

    with patch("src.celery_app.tasks.workline._load_related_entities", new_callable=AsyncMock) as mock_load:
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

    with patch("src.celery_app.tasks.workline._load_related_entities", new_callable=AsyncMock) as mock_load:
        mock_load.return_value = {
            "session": None,
            "workline": None,
            "device": None,
            "command": None,
            "devices_by_role": {},
            "safety_checked": True,
        }

        with patch("src.celery_app.tasks.workline._record_diagnostic", new_callable=AsyncMock) as mock_record_diag:
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

    with patch("src.celery_app.tasks.workline._load_related_entities", new_callable=AsyncMock) as mock_load:
        mock_load.return_value = {
            "session": MagicMock(),
            "workline": MagicMock(),
            "device": None,
            "command": None,
            "devices_by_role": {},
            "safety_checked": True,
        }

        with patch("src.celery_app.tasks.workline._is_duplicate_entry_event_for_session", return_value=True):
            with patch("src.celery_app.tasks.workline._duplicate_entry_material_conflict", return_value=None):
                with patch(
                    "src.celery_app.tasks.workline._record_duplicate_entry_archive_timeline", new_callable=AsyncMock
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

    with patch("src.celery_app.tasks.workline._load_related_entities", new_callable=AsyncMock) as mock_load:
        mock_load.return_value = {
            "session": MagicMock(),
            "workline": MagicMock(),
            "device": None,
            "command": None,
            "devices_by_role": {},
            "safety_checked": True,
        }

        with patch("src.celery_app.tasks.workline._is_duplicate_entry_event_for_session", return_value=False):
            with patch(
                "src.celery_app.tasks.workline._is_late_or_duplicate_command_result_for_session", return_value=True
            ):
                with patch(
                    "src.celery_app.tasks.workline._record_late_command_result_archive_timeline", new_callable=AsyncMock
                ) as mock_record:
                    processor = InboxBatchProcessor()
                    result = await processor.process_batch(mock_db, limit=1)

                    assert result["success"] == 1
                    mock_record.assert_awaited_once()
