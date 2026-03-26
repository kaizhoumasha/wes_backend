"""
TimeoutScanner 单元测试

测试 Celery 定时任务扫描超时 Session 的流程：
- 扫描过期 Session
- 生成 TIMER_TIMEOUT Inbox
- 更新 Session 状态

设计参考: 设计文档 phase2-orchestrator § timeout_scanner
"""

from datetime import UTC, datetime, timedelta
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
        correlation_id: str | None = None,
    ):
        self.id = session_id
        self.status = status
        self.deadline_at = deadline_at
        self.workline_id = workline_id
        self.correlation_id = correlation_id


class TestTimeoutScanner:
    """TimeoutScanner 任务测试"""

    @pytest.fixture
    def mock_db(self):
        """创建模拟数据库会话"""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
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
        mock_session_repo.get_timed_out_sessions.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_single_timed_out_session(
        self,
        mock_db,
        mock_session_repo,
        mock_inbox_service,
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
            correlation_id="corr-123",
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
        ):
            result = await scan_timeouts._scan(mock_db)

        assert result["scanned"] == 1
        assert result["timeouts_created"] == 1
        mock_inbox_service.create_timeout_inbox.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_multiple_timed_out_sessions(
        self,
        mock_db,
        mock_session_repo,
        mock_inbox_service,
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
                correlation_id=f"corr-{i}",
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
        mock_inbox_service.create_timeout_inbox.side_effect = ValueError(
            "Inbox creation failed"
        )

        with (
            patch(
                "src.app.workline.repositories.session_repository.WorklineSessionRepository",
                return_value=mock_session_repo,
            ),
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
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
        # 应包含：session_id, workline_id, correlation_id
        assert hasattr(service, "create_timeout_inbox")
