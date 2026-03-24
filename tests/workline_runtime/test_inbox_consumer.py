"""
InboxConsumer 单元测试

测试 Celery 任务消费 WorklineInbox 的流程：
- 获取待处理消息
- 标记处理状态
- 调用 OrchestratorService
- 处理结果记录

设计参考: 设计文档 phase2-orchestrator
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workline_runtime.enums import InboundKind, InboxStatus
from src.workline_runtime.orchestrator import OrchestratorResult


def make_noop_lock():
    """创建空操作锁上下文管理器"""

    @asynccontextmanager
    async def noop_lock():
        yield

    return noop_lock()


class MockInbox:
    """模拟 WorklineInbox"""

    def __init__(
        self,
        inbox_id: int = 1,
        kind: InboundKind = InboundKind.DEVICE_EVENT,
        status: InboxStatus = InboxStatus.NEW,
        session_id: int | None = None,
        workline_id: int | None = None,
        device_id: int | None = None,
        command_id: int | None = None,
        correlation_id: str | None = None,
        payload_json: dict[str, Any] | None = None,
    ):
        self.id = inbox_id
        self.kind = kind
        self.status = status
        self.session_id = session_id
        self.workline_id = workline_id
        self.device_id = device_id
        self.command_id = command_id
        self.correlation_id = correlation_id
        self.payload_json = payload_json or {}
        self.processor_token = None
        self.processed_at = None
        self.error_message = None


class MockSession:
    """模拟 WorklineSession"""

    def __init__(
        self,
        session_id: int = 12345,
        status: str = "RUNNING",
        context: dict[str, Any] | None = None,
        version: int = 1,
    ):
        self.session_id = session_id
        self.status = status
        self.context = context or {}
        self.version = version


class MockWorkline:
    """模拟 WorkLine"""

    def __init__(
        self,
        workline_id: int = 1,
        plugin_class: type[Any] | None = None,
        state_machine_class: type[Any] | None = None,
    ):
        self.id = workline_id
        self.plugin_class = plugin_class
        self.state_machine_class = state_machine_class


class TestInboxConsumer:
    """InboxConsumer 任务测试"""

    @pytest.fixture
    def mock_db(self):
        """创建模拟数据库会话"""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        return db

    @pytest.fixture
    def mock_inbox_service(self):
        """创建模拟 InboxService"""
        service = MagicMock()
        service.get_new_messages = AsyncMock(return_value=[])
        service.mark_as_processing = AsyncMock()
        service.mark_as_processed = AsyncMock()
        service.mark_as_failed = AsyncMock()
        return service

    @pytest.fixture
    def mock_orchestrator(self):
        """创建模拟 OrchestratorService"""
        orchestrator = MagicMock()
        orchestrator.process_inbox = AsyncMock(
            return_value=OrchestratorResult(success=True)
        )
        return orchestrator

    @pytest.mark.asyncio
    async def test_process_inbox_no_messages(self, mock_db, mock_inbox_service):
        """测试无待处理消息时正常退出"""
        from src.celery_app.tasks.workline import process_inbox_messages

        with patch(
            "src.app.workline.services.inbox_service.inbox_service", mock_inbox_service
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 0
        assert result["success"] == 0
        assert result["failed"] == 0
        mock_inbox_service.get_new_messages.assert_called_once_with(mock_db, limit=10)

    @pytest.mark.asyncio
    async def test_process_inbox_single_message_success(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """测试单条消息处理成功"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(inbox_id=1, kind=InboundKind.DEVICE_EVENT)
        mock_inbox_service.get_new_messages.return_value = [inbox]

        with (
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.celery_app.tasks.workline.OrchestratorService",
                return_value=mock_orchestrator,
            ),
            patch(
                "src.celery_app.tasks.workline._load_related_entities",
                AsyncMock(
                    return_value={
                        "session": MockSession(),
                        "workline": MockWorkline(),
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 1
        assert result["failed"] == 0

        mock_inbox_service.mark_as_processing.assert_called_once()
        mock_inbox_service.mark_as_processed.assert_called_once()
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_process_inbox_single_message_failure(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """测试单条消息处理失败"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(inbox_id=1, kind=InboundKind.DEVICE_EVENT)
        mock_inbox_service.get_new_messages.return_value = [inbox]
        mock_orchestrator.process_inbox.return_value = OrchestratorResult(
            success=False,
            error="Processing failed",
        )

        with (
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.celery_app.tasks.workline.OrchestratorService",
                return_value=mock_orchestrator,
            ),
            patch(
                "src.celery_app.tasks.workline._load_related_entities",
                AsyncMock(
                    return_value={
                        "session": MockSession(),
                        "workline": MockWorkline(),
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1

        mock_inbox_service.mark_as_failed.assert_called_once()
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_process_inbox_exception_handling(
        self,
        mock_db,
        mock_inbox_service,
    ):
        """测试处理过程中异常"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(inbox_id=1, kind=InboundKind.DEVICE_EVENT)
        mock_inbox_service.get_new_messages.return_value = [inbox]

        with (
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.celery_app.tasks.workline._load_related_entities",
                AsyncMock(side_effect=ValueError("Entity not found")),
            ),
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1

        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            "Entity not found",
        )
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_process_inbox_multiple_messages(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """测试批量处理多条消息"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inboxes = [
            MockInbox(inbox_id=i, kind=InboundKind.DEVICE_EVENT)
            for i in range(1, 4)
        ]
        mock_inbox_service.get_new_messages.return_value = inboxes

        with (
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.celery_app.tasks.workline.OrchestratorService",
                return_value=mock_orchestrator,
            ),
            patch(
                "src.celery_app.tasks.workline._load_related_entities",
                AsyncMock(
                    return_value={
                        "session": MockSession(),
                        "workline": MockWorkline(),
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 3
        assert result["success"] == 3
        assert result["failed"] == 0
        assert mock_inbox_service.mark_as_processed.call_count == 3

    @pytest.mark.asyncio
    async def test_process_inbox_skips_already_processing(
        self,
        mock_db,
        mock_inbox_service,
    ):
        """测试跳过已处理中的消息（并发安全）"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(inbox_id=1, status=InboxStatus.PROCESSING)
        mock_inbox_service.get_new_messages.return_value = [inbox]

        # mark_as_processing 应该抛出异常或返回 None 表示已被其他 worker 处理
        mock_inbox_service.mark_as_processing.side_effect = ValueError(
            "Already processing"
        )

        with patch(
            "src.app.workline.services.inbox_service.inbox_service", mock_inbox_service
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        # 应该跳过这条消息，不算作失败
        assert result["processed"] == 0
        assert result["skipped"] == 1


class TestLoadRelatedEntities:
    """测试加载关联实体"""

    @pytest.fixture
    def mock_db(self):
        """创建模拟数据库会话"""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_load_session_and_workline(self, mock_db):
        """测试加载 Session 和 Workline"""
        from src.celery_app.tasks.workline import _load_related_entities

        inbox = MockInbox(
            inbox_id=1,
            session_id=100,
            workline_id=1,
        )

        mock_session = MockSession(session_id=100)
        mock_workline = MockWorkline(workline_id=1)

        with (
            patch(
                "src.app.workline.repositories.session_repository.WorklineSessionRepository",
            ) as MockSessionRepo,
            patch(
                "src.app.workline.repositories.WorkLineRepository",
            ) as MockWorklineRepo,
            patch(
                "src.app.device.repositories.DeviceRepository",
            ) as MockDeviceRepo,
        ):
            # 配置 repository mock 实例
            mock_session_repo_instance = AsyncMock()
            mock_session_repo_instance.get_by_id = AsyncMock(return_value=mock_session)
            MockSessionRepo.return_value = mock_session_repo_instance

            mock_workline_repo_instance = AsyncMock()
            mock_workline_repo_instance.get_by_id = AsyncMock(return_value=mock_workline)
            MockWorklineRepo.return_value = mock_workline_repo_instance

            mock_device_repo_instance = AsyncMock()
            mock_device_repo_instance.get_by_work_line_id = AsyncMock(return_value=[])
            MockDeviceRepo.return_value = mock_device_repo_instance

            entities = await _load_related_entities(mock_db, inbox)

        assert entities["session"] is not None
        assert entities["session"].session_id == 100
        assert entities["workline"] is not None
        assert entities["workline"].id == 1

    @pytest.mark.asyncio
    async def test_load_devices_by_role(self, mock_db):
        """测试按角色加载设备"""
        from src.celery_app.tasks.workline import _load_related_entities

        inbox = MockInbox(
            inbox_id=1,
            workline_id=1,
        )

        mock_device1 = MagicMock()
        mock_device1.id = 1
        mock_device1.role = "SCANNER"

        mock_device2 = MagicMock()
        mock_device2.id = 2
        mock_device2.role = "ROBOT_ARM"

        mock_workline = MockWorkline(workline_id=1)

        with (
            patch(
                "src.app.workline.repositories.WorkLineRepository",
            ) as MockWorklineRepo,
            patch(
                "src.app.device.repositories.DeviceRepository",
            ) as MockDeviceRepo,
        ):
            mock_workline_repo_instance = AsyncMock()
            mock_workline_repo_instance.get_by_id = AsyncMock(return_value=mock_workline)
            MockWorklineRepo.return_value = mock_workline_repo_instance

            mock_device_repo_instance = AsyncMock()
            mock_device_repo_instance.get_by_work_line_id = AsyncMock(
                return_value=[mock_device1, mock_device2]
            )
            MockDeviceRepo.return_value = mock_device_repo_instance

            entities = await _load_related_entities(mock_db, inbox)

        assert "SCANNER" in entities["devices_by_role"]
        assert "ROBOT_ARM" in entities["devices_by_role"]
        assert len(entities["devices_by_role"]["SCANNER"]) == 1
