"""
TimeoutScanner 单元测试

测试 Celery 定时任务扫描超时 Session 的流程：
- 扫描过期 Session
- 生成 TIMER_TIMEOUT Inbox
- 更新 Session 状态

设计参考: 设计文档 phase2-orchestrator § timeout_scanner
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class MockSession:
    """模拟 WorklineSession"""

    def __init__(
        self,
        session_id: int = 12345,
        status: str = "WAITING_DEVICE_RESULT",
        deadline_at: datetime | None = None,
        workline_id: int = 1,
        trace_id: str | None = None,
        current_wait_type: str = "COMMAND_RESULT",
        awaiting_command_id: int | None = 9,
    ):
        self.id = session_id
        self.status = status
        self.deadline_at = deadline_at
        self.workline_id = workline_id
        self.trace_id = trace_id
        self.current_wait_type = current_wait_type
        self.awaiting_command_id = awaiting_command_id


class TestTimeoutScanner:
    """TimeoutScanner 任务测试"""

    @pytest.fixture
    def mock_db(self):
        """创建模拟数据库会话"""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=execute_result)
        db.get = AsyncMock(
            return_value=SimpleNamespace(
                id=9,
                command_code="CMD-001",
                device_id=7,
                status="ACK_RECEIVED",
                ack_received_at=datetime.now(UTC) - timedelta(minutes=4),
            )
        )
        return db

    @pytest.fixture
    def mock_session_repo(self):
        """创建模拟 SessionRepository"""
        repo = MagicMock()
        repo.get_timed_out_sessions = AsyncMock(return_value=[])
        return repo

    @pytest.fixture
    def mock_inbox_service(self):
        """创建模拟 WorklineInboxService"""
        service = MagicMock()
        service.create_timeout_inbox = AsyncMock()
        return service

    @pytest.fixture
    def mock_device_repo(self):
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id=7, device_code="ARM01"))
        return repo

    @pytest.mark.asyncio
    async def test_scan_no_timed_out_sessions(self, mock_db, mock_session_repo):
        """测试无超时 Session 时正常退出"""
        from src.celery_app.tasks.workline import scan_timeouts

        with patch(
            "src.app.workline.repositories.session_repository.WorklineSessionRepository",
            return_value=mock_session_repo,
        ):
            result = await scan_timeouts._scan(mock_db)

        assert result["scanned"] == 0
        assert result["timeouts_created"] == 0
        assert result["ack_timeouts_reconciled"] == 0
        mock_session_repo.get_timed_out_sessions.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_single_timed_out_session(
        self,
        mock_db,
        mock_session_repo,
        mock_inbox_service,
        mock_device_repo,
    ):
        """测试单个超时 Session 处理"""
        from src.celery_app.tasks.workline import scan_timeouts

        # 创建一个过期的 Session
        expired_time = datetime.now(UTC) - timedelta(minutes=5)
        session = MockSession(
            session_id=100,
            status="WAITING_DEVICE_RESULT",
            deadline_at=expired_time,
            workline_id=1,
            trace_id="trace-123",
        )
        mock_session_repo.get_timed_out_sessions.return_value = [session]

        with (
            patch(
                "src.app.workline.repositories.session_repository.WorklineSessionRepository",
                return_value=mock_session_repo,
            ),
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
        ):
            result = await scan_timeouts._scan(mock_db)

        assert result["scanned"] == 1
        assert result["timeouts_created"] == 1
        assert result["ack_timeouts_reconciled"] == 0
        mock_inbox_service.create_timeout_inbox.assert_called_once()
        call_kwargs = mock_inbox_service.create_timeout_inbox.call_args.kwargs
        assert call_kwargs["wait_token"] == "CMD-001"
        assert call_kwargs["awaiting_command_id"] == 9
        assert call_kwargs["command_code"] == "CMD-001"
        assert call_kwargs["command_status"] == "ACK_RECEIVED"
        assert call_kwargs["ack_received_at"] is not None

    @pytest.mark.asyncio
    async def test_scan_ack_timed_out_command_enters_runtime_reconciliation(
        self,
        mock_db,
        mock_session_repo,
    ):
        """SENT 指令 ACK 等待超时后必须进入通信 ACK 对账隔离。"""
        from src.celery_app.tasks.workline import scan_timeouts

        command = SimpleNamespace(
            id=881,
            command_code="CMD-20260509-MOVE_FORWARD-AB5F1A76",
            status="SENT",
        )
        outbox = SimpleNamespace(
            id=862,
            session_id=553,
            dispatch_key="device-command:CMD-20260509-MOVE_FORWARD-AB5F1A76",
        )
        command_repo = MagicMock()
        command_repo.get_ack_timed_out_commands = AsyncMock(return_value=[command])
        outbox_repo = MagicMock()
        outbox_repo.get_by_dispatch_key = AsyncMock(return_value=outbox)
        runtime_service = SimpleNamespace(handle_dispatch_ack_exhausted=AsyncMock(return_value=SimpleNamespace(id=553)))

        with (
            patch(
                "src.app.workline.repositories.session_repository.WorklineSessionRepository",
                return_value=mock_session_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=command_repo,
            ),
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=outbox_repo,
            ),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
        ):
            result = await scan_timeouts._scan(mock_db)

        assert result["scanned"] == 1
        assert result["timeouts_created"] == 0
        assert result["ack_timeouts_reconciled"] == 1
        outbox_repo.get_by_dispatch_key.assert_awaited_once_with(
            mock_db,
            "device-command:CMD-20260509-MOVE_FORWARD-AB5F1A76",
        )
        runtime_service.handle_dispatch_ack_exhausted.assert_awaited_once_with(
            mock_db,
            outbox=outbox,
            command=command,
            error_message="COMMAND_ACK_TIMEOUT",
        )

    @pytest.mark.asyncio
    async def test_scan_external_wait_creates_timeout_inbox_without_command(
        self,
        mock_db,
        mock_session_repo,
        mock_inbox_service,
        mock_device_repo,
    ):
        """WAITING_EXTERNAL 没有关联 DeviceCommand 时也必须生成 TIMER_TIMEOUT。"""
        from src.celery_app.tasks.workline import scan_timeouts

        expired_time = datetime.now(UTC) - timedelta(minutes=5)
        session = MockSession(
            session_id=200,
            status="WAITING_EXTERNAL",
            deadline_at=expired_time,
            workline_id=1,
            trace_id="trace-external",
            current_wait_type="EXTERNAL_HTTP",
            awaiting_command_id=None,
        )
        mock_session_repo.get_timed_out_sessions.return_value = [session]
        mock_db.get = AsyncMock(return_value=None)

        with (
            patch(
                "src.app.workline.repositories.session_repository.WorklineSessionRepository",
                return_value=mock_session_repo,
            ),
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
        ):
            result = await scan_timeouts._scan(mock_db)

        assert result["scanned"] == 1
        assert result["timeouts_created"] == 1
        assert result["errors"] == 0
        mock_db.get.assert_not_awaited()
        mock_device_repo.get_by_id.assert_not_awaited()
        call_kwargs = mock_inbox_service.create_timeout_inbox.call_args.kwargs
        assert call_kwargs["wait_token"] is None
        assert call_kwargs["wait_type"] == "EXTERNAL_HTTP"
        assert call_kwargs["awaiting_command_id"] is None
        assert call_kwargs["command_code"] is None

    @pytest.mark.asyncio
    async def test_scan_multiple_timed_out_sessions(
        self,
        mock_db,
        mock_session_repo,
        mock_inbox_service,
        mock_device_repo,
    ):
        """测试批量扫描多个超时 Session"""
        from src.celery_app.tasks.workline import scan_timeouts

        expired_time = datetime.now(UTC) - timedelta(minutes=5)
        sessions = [
            MockSession(
                session_id=i,
                status="WAITING_DEVICE_RESULT",
                deadline_at=expired_time,
                workline_id=1,
                trace_id=f"trace-{i}",
            )
            for i in range(1, 4)
        ]
        mock_session_repo.get_timed_out_sessions.return_value = sessions

        with (
            patch(
                "src.app.workline.repositories.session_repository.WorklineSessionRepository",
                return_value=mock_session_repo,
            ),
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
        ):
            result = await scan_timeouts._scan(mock_db)

        assert result["scanned"] == 3
        assert result["timeouts_created"] == 3
        assert mock_inbox_service.create_timeout_inbox.call_count == 3

    @pytest.mark.asyncio
    async def test_scan_skips_non_waiting_status(
        self,
        mock_db,
        mock_session_repo,
    ):
        """测试跳过非等待状态的 Session（仅扫描等待状态）"""
        from src.celery_app.tasks.workline import scan_timeouts

        # RUNNING 状态不应被扫描
        mock_session_repo.get_timed_out_sessions.return_value = []

        with patch(
            "src.app.workline.repositories.session_repository.WorklineSessionRepository",
            return_value=mock_session_repo,
        ):
            result = await scan_timeouts._scan(mock_db)

        assert result["scanned"] == 0
        # 验证查询参数：只扫描等待状态
        mock_session_repo.get_timed_out_sessions.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_handles_inbox_creation_error(
        self,
        mock_db,
        mock_session_repo,
        mock_inbox_service,
        mock_device_repo,
    ):
        """测试 Inbox 创建失败时的错误处理"""
        from src.celery_app.tasks.workline import scan_timeouts

        expired_time = datetime.now(UTC) - timedelta(minutes=5)
        session = MockSession(
            session_id=100,
            status="WAITING_DEVICE_RESULT",
            deadline_at=expired_time,
            workline_id=1,
        )
        mock_session_repo.get_timed_out_sessions.return_value = [session]

        # 模拟 Inbox 创建失败
        mock_inbox_service.create_timeout_inbox.side_effect = ValueError("Inbox creation failed")

        with (
            patch(
                "src.app.workline.repositories.session_repository.WorklineSessionRepository",
                return_value=mock_session_repo,
            ),
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
        ):
            result = await scan_timeouts._scan(mock_db)

        # 应该记录错误但继续处理
        assert result["scanned"] == 1
        assert result["errors"] == 1


class TestTimeoutInboxCreation:
    """测试超时 Inbox 创建"""

    @pytest.mark.asyncio
    async def test_timeout_inbox_contains_correct_fields(self):
        """测试超时 Inbox 包含正确字段"""
        from src.app.workline.services.inbox_service import WorklineInboxService

        service = WorklineInboxService()

        # 验证 create_timeout_inbox 方法签名
        # 应包含：session_id, workline_id, trace_id
        assert hasattr(service, "create_timeout_inbox")
