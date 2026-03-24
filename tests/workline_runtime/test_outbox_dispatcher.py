"""
OutboxDispatcher 单元测试

测试 Celery 任务派发 WorklineOutbox 的流程：
- 获取待派发消息
- 根据类型派发（设备指令/外部HTTP/内部信号）
- 更新派发状态
- 重试机制

设计参考: 设计文档 phase2-orchestrator
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.models.outbox import DispatchType, OutboxStatus, TargetType


class MockOutbox:
    """模拟 WorklineOutbox"""

    def __init__(
        self,
        outbox_id: int = 1,
        dispatch_type: DispatchType = DispatchType.DEVICE_COMMAND,
        target_type: TargetType = TargetType.DEVICE,
        target_code: str = "DEVICE_001",
        status: OutboxStatus = OutboxStatus.NEW,
        session_id: int | None = None,
        workline_id: int | None = None,
        payload_json: dict | None = None,
        attempt_count: int = 0,
    ):
        self.id = outbox_id
        self.dispatch_type = dispatch_type
        self.target_type = target_type
        self.target_code = target_code
        self.status = status
        self.session_id = session_id
        self.workline_id = workline_id
        self.payload_json = payload_json or {}
        self.attempt_count = attempt_count
        self.next_retry_at = None
        self.last_error = None


class TestOutboxDispatcher:
    """OutboxDispatcher 任务测试"""

    @pytest.fixture
    def mock_db(self):
        """创建模拟数据库会话"""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        return db

    @pytest.fixture
    def mock_outbox_repo(self):
        """创建模拟 OutboxRepository"""
        repo = MagicMock()
        repo.get_pending_messages = AsyncMock(return_value=[])
        repo.mark_as_dispatching = AsyncMock(return_value=MagicMock())
        repo.mark_as_sent = AsyncMock(return_value=MagicMock())
        repo.mark_as_failed = AsyncMock(return_value=MagicMock())
        return repo

    @pytest.fixture
    def mock_device_service(self):
        """创建模拟设备服务"""
        service = MagicMock()
        service.send_command = AsyncMock(return_value={"success": True})
        return service

    @pytest.mark.asyncio
    async def test_dispatch_no_pending_messages(self, mock_db, mock_outbox_repo):
        """测试无待派发消息时正常退出"""
        from src.celery_app.tasks.workline import dispatch_outbox

        with patch(
            "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
            return_value=mock_outbox_repo,
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 0
        assert result["success"] == 0
        assert result["failed"] == 0
        mock_outbox_repo.get_pending_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_single_device_command(
        self,
        mock_db,
        mock_outbox_repo,
        mock_device_service,
    ):
        """测试派发单个设备指令"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=1,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="SCANNER_001",
            payload_json={"command": "SCAN"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        with (
            patch(
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.services.device_service.device_service",
                mock_device_service,
            ),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 1
        assert result["failed"] == 0
        mock_device_service.send_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_multiple_messages(
        self,
        mock_db,
        mock_outbox_repo,
        mock_device_service,
    ):
        """测试批量派发多条消息"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outboxes = [
            MockOutbox(
                outbox_id=i,
                dispatch_type=DispatchType.DEVICE_COMMAND,
                target_code=f"DEVICE_{i}",
            )
            for i in range(1, 4)
        ]
        mock_outbox_repo.get_pending_messages.return_value = outboxes

        with (
            patch(
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.services.device_service.device_service",
                mock_device_service,
            ),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 3
        assert result["success"] == 3
        assert result["failed"] == 0
        assert mock_device_service.send_command.call_count == 3

    @pytest.mark.asyncio
    async def test_dispatch_handles_failure_with_retry(
        self,
        mock_db,
        mock_outbox_repo,
        mock_device_service,
    ):
        """测试派发失败时的重试机制"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=1,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_code="DEVICE_001",
            attempt_count=0,
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        # 模拟派发失败
        mock_device_service.send_command.side_effect = Exception("Device offline")

        # 让 mark_as_failed 实际修改 outbox 对象
        async def mock_mark_failed(db, outbox_id, error, max_retries):
            outbox.attempt_count += 1
            outbox.last_error = error
            return outbox

        mock_outbox_repo.mark_as_failed = AsyncMock(side_effect=mock_mark_failed)

        with (
            patch(
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.services.device_service.device_service",
                mock_device_service,
            ),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        # 应该记录失败并设置重试
        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        assert outbox.attempt_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_marks_failed_after_max_retries(
        self,
        mock_db,
        mock_outbox_repo,
        mock_device_service,
    ):
        """测试达到最大重试次数后标记为失败"""
        from src.celery_app.tasks.workline import dispatch_outbox

        # 已重试 3 次（达到上限）
        outbox = MockOutbox(
            outbox_id=1,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_code="DEVICE_001",
            attempt_count=3,
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]
        mock_device_service.send_command.side_effect = Exception("Device offline")

        # 让 mark_as_failed 实际修改 outbox 对象
        async def mock_mark_failed(db, outbox_id, error, max_retries):
            outbox.attempt_count += 1
            outbox.last_error = error
            if outbox.attempt_count >= max_retries:
                outbox.status = OutboxStatus.FAILED
            return outbox

        mock_outbox_repo.mark_as_failed = AsyncMock(side_effect=mock_mark_failed)

        with (
            patch(
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.services.device_service.device_service",
                mock_device_service,
            ),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["failed"] == 1
        # 达到最大重试次数，状态应为 FAILED
        assert outbox.status == OutboxStatus.FAILED

    @pytest.mark.asyncio
    async def test_dispatch_skips_dispatching_status(self, mock_db, mock_outbox_repo):
        """测试跳过正在派发中的消息（并发安全）"""
        from src.celery_app.tasks.workline import dispatch_outbox

        # DISPATCHING 状态的消息应被跳过
        outbox = MockOutbox(
            outbox_id=1,
            status=OutboxStatus.DISPATCHING,
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        # mark_as_dispatching 返回 None 表示已被其他进程处理
        mock_outbox_repo.mark_as_dispatching = AsyncMock(return_value=None)

        with patch(
            "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
            return_value=mock_outbox_repo,
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        # 应该跳过这条消息
        assert result["dispatched"] == 0
        assert result["skipped"] == 1


class TestDispatchByType:
    """测试不同派发类型的处理"""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_dispatch_device_command(self, mock_db):
        """测试设备指令派发"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ROBOT_001",
            payload_json={"action": "MOVE", "position": {"x": 100, "y": 200}},
        )

        mock_device_service = MagicMock()
        mock_device_service.send_command = AsyncMock(return_value={"success": True})

        with patch(
            "src.app.device.services.device_service.device_service",
            mock_device_service,
        ):
            result = await dispatch_outbox._dispatch_single(mock_db, outbox)

        assert result is True
        mock_device_service.send_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_external_http(self, mock_db):
        """测试外部 HTTP 调用派发"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            dispatch_type=DispatchType.EXTERNAL_HTTP,
            target_type=TargetType.HTTP_ENDPOINT,
            target_code="https://api.example.com/callback",
            payload_json={"event": "completed", "data": {"id": 123}},
        )

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await dispatch_outbox._dispatch_single(mock_db, outbox)

        assert result is True

    @pytest.mark.asyncio
    async def test_dispatch_internal_signal(self, mock_db):
        """测试内部信号派发"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            dispatch_type=DispatchType.INTERNAL_SIGNAL,
            target_type=TargetType.INTERNAL_SERVICE,
            target_code="notification_service",
            payload_json={"type": "alert", "message": "Task completed"},
        )

        # 内部信号派发到 Celery 任务队列
        with patch("src.celery_app.app.celery_app.send_task") as mock_send:
            result = await dispatch_outbox._dispatch_single(mock_db, outbox)

        assert result is True
        mock_send.assert_called_once()
