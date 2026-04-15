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
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.models.inbox import InboxKind, InboxStatus
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
        kind: InboxKind = InboxKind.DEVICE_EVENT,
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
        self.id = session_id
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
        """创建模拟 WorklineInboxService"""
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
        orchestrator.process_inbox = AsyncMock(return_value=OrchestratorResult(success=True))
        return orchestrator

    @pytest.mark.asyncio
    async def test_process_inbox_no_messages(self, mock_db, mock_inbox_service):
        """测试无待处理消息时正常退出"""
        from src.celery_app.tasks.workline import process_inbox_messages

        with patch("src.app.workline.services.inbox_service.inbox_service", mock_inbox_service):
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

        inbox = MockInbox(inbox_id=1, kind=InboxKind.DEVICE_EVENT)
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

        inbox = MockInbox(inbox_id=1, kind=InboxKind.DEVICE_EVENT)
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
    async def test_process_inbox_business_failure_marks_message_processed(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """业务失败归因应视为已处理，不应把 Inbox 标成失败。"""
        from src.celery_app.tasks.workline import process_inbox_messages
        from src.workline_runtime.types import FailureIntent

        inbox = MockInbox(inbox_id=1, kind=InboxKind.COMMAND_RESULT)
        mock_inbox_service.get_new_messages.return_value = [inbox]
        mock_orchestrator.process_inbox.return_value = OrchestratorResult(
            success=True,
            failure=FailureIntent(
                domain="HARDWARE",
                code="DEVICE_OFFLINE",
                message="device offline",
            ),
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
            patch(
                "src.celery_app.tasks.workline._apply_orchestrator_effects",
                AsyncMock(),
            ) as mock_apply_effects,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 1
        assert result["failed"] == 0
        mock_apply_effects.assert_called_once()
        mock_inbox_service.mark_as_processed.assert_called_once()
        mock_inbox_service.mark_as_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_inbox_exception_handling(
        self,
        mock_db,
        mock_inbox_service,
    ):
        """测试处理过程中异常"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(inbox_id=1, kind=InboxKind.DEVICE_EVENT)
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
            auto_commit=False,
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

        inboxes = [MockInbox(inbox_id=i, kind=InboxKind.DEVICE_EVENT) for i in range(1, 4)]
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
    async def test_apply_orchestrator_effects_preserves_wait_on_manual_hold(self, mock_db):
        """人工暂停不应清空当前等待中的上下文。"""
        from src.celery_app.tasks.workline import _apply_orchestrator_effects

        waiting_since = datetime.now()
        deadline_at = waiting_since + timedelta(seconds=60)
        session = SimpleNamespace(
            id=123,
            workline_id=1,
            status="WAITING_DEVICE_RESULT",
            context_json={"stage": "WAITING_PICK_PLACE"},
            correlation_id=None,
            last_inbox_id=None,
            current_wait_type="COMMAND_RESULT",
            current_wait_token="wait-token",
            waiting_since=waiting_since,
            deadline_at=deadline_at,
            awaiting_command_id=88,
            ended_at=None,
            failure_domain=None,
            failure_code=None,
            failure_message=None,
        )
        workline = SimpleNamespace(plugin_key="smt_classifier")
        inbox = SimpleNamespace(id=1, correlation_id="corr-001")
        orch_result = OrchestratorResult(
            success=True,
            transition="manual_hold",
            context_patch={"manual_hold": True},
        )

        with (
            patch("src.celery_app.tasks.workline._add_timeline", new=AsyncMock()),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate", return_value=SimpleNamespace()
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={},
                orch_result=orch_result,
            )

        assert session.status == "MANUAL_HOLD"
        assert session.current_wait_type == "COMMAND_RESULT"
        assert session.current_wait_token == "wait-token"
        assert session.waiting_since == waiting_since
        assert session.deadline_at == deadline_at
        assert session.awaiting_command_id == 88
        assert session.context_json["manual_hold"] is True

    @pytest.mark.asyncio
    async def test_apply_orchestrator_effects_restores_wait_on_manual_resume(self, mock_db):
        """人工恢复应回到原等待态，而不是错误地改成 RUNNING。"""
        from src.celery_app.tasks.workline import _apply_orchestrator_effects

        waiting_since = datetime.now()
        deadline_at = waiting_since + timedelta(seconds=60)
        session = SimpleNamespace(
            id=123,
            workline_id=1,
            status="MANUAL_HOLD",
            context_json={"stage": "WAITING_PICK_PLACE", "manual_hold": True},
            correlation_id=None,
            last_inbox_id=None,
            current_wait_type="COMMAND_RESULT",
            current_wait_token="wait-token",
            waiting_since=waiting_since,
            deadline_at=deadline_at,
            awaiting_command_id=88,
            ended_at=None,
            failure_domain=None,
            failure_code=None,
            failure_message=None,
        )
        workline = SimpleNamespace(plugin_key="smt_classifier")
        inbox = SimpleNamespace(id=1, correlation_id="corr-001")
        orch_result = OrchestratorResult(
            success=True,
            transition="manual_resume",
            context_patch={"manual_hold": False},
        )

        with (
            patch("src.celery_app.tasks.workline._add_timeline", new=AsyncMock()),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate", return_value=SimpleNamespace()
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={},
                orch_result=orch_result,
            )

        assert session.status == "WAITING_DEVICE_RESULT"
        assert session.current_wait_type == "COMMAND_RESULT"
        assert session.current_wait_token == "wait-token"
        assert session.waiting_since == waiting_since
        assert session.deadline_at == deadline_at
        assert session.awaiting_command_id == 88
        assert session.context_json["manual_hold"] is False

    @pytest.mark.asyncio
    async def test_apply_orchestrator_effects_marks_manual_cancel_as_cancelled(self, mock_db):
        """人工取消应落 CANCELLED，并写入取消时间线而不是完成时间线。"""
        from src.app.workline.models.timeline import TimelineActionType, TimelineStage
        from src.celery_app.tasks.workline import _apply_orchestrator_effects

        session = SimpleNamespace(
            id=123,
            workline_id=1,
            status="RUNNING",
            context_json={"stage": "WAITING_PICK_PLACE"},
            correlation_id=None,
            last_inbox_id=None,
            current_wait_type="COMMAND_RESULT",
            current_wait_token="wait-token",
            waiting_since=datetime.now(),
            deadline_at=datetime.now() + timedelta(seconds=60),
            awaiting_command_id=88,
            ended_at=None,
            failure_domain=None,
            failure_code=None,
            failure_message=None,
        )
        workline = SimpleNamespace(plugin_key="smt_classifier")
        inbox = SimpleNamespace(id=1, correlation_id="corr-001")
        orch_result = OrchestratorResult(
            success=True,
            transition="manual_cancel",
            context_patch={"cancelled": True, "cancel_reason": "Quality issue"},
            complete=True,
        )

        captured_timelines: list[Any] = []

        with (
            patch(
                "src.celery_app.tasks.workline._add_timeline",
                new=AsyncMock(side_effect=lambda _db, t: captured_timelines.append(t)),
            ),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={},
                orch_result=orch_result,
            )

        assert session.status == "CANCELLED"
        assert session.ended_at is not None
        assert session.current_wait_type is None
        assert session.current_wait_token is None
        assert session.awaiting_command_id is None
        assert session.context_json["cancelled"] is True
        assert session.context_json["cancel_reason"] == "Quality issue"
        assert [item.action_type for item in captured_timelines] == [
            TimelineActionType.DECISION_MADE,
            TimelineActionType.SESSION_CANCELLED,
        ]
        assert captured_timelines[-1].stage == TimelineStage.MANUAL
        assert captured_timelines[-1].to_status == "CANCELLED"

    @pytest.mark.asyncio
    async def test_apply_orchestrator_effects_creates_external_http_outbox(self, mock_db):
        """Plugin decisions 中的 EXTERNAL_HTTP 请求应落成 Outbox。

        并让 Session 进入 WAITING_EXTERNAL。
        """
        from src.app.workline.models.outbox import DispatchType, TargetType
        from src.app.workline.models.timeline import TimelineActionType
        from src.celery_app.tasks.workline import _apply_orchestrator_effects

        captured_timelines: list[Any] = []
        added_models: list[Any] = []

        session = SimpleNamespace(
            id=123,
            workline_id=1,
            status="RUNNING",
            context_json={"stage": "WAITING_CONVEYOR"},
            correlation_id="corr-external-001",
            last_inbox_id=None,
            current_wait_type=None,
            current_wait_token=None,
            waiting_since=None,
            deadline_at=None,
            awaiting_command_id=None,
            ended_at=None,
            failure_domain=None,
            failure_code=None,
            failure_message=None,
        )
        workline = SimpleNamespace(plugin_key="smt_classifier")
        inbox = SimpleNamespace(id=99, correlation_id="corr-external-001")
        orch_result = OrchestratorResult(
            success=True,
            transition="agv_requested",
            decisions=[
                {
                    "decision_type": "EXTERNAL_HTTP_REQUEST",
                    "dispatch_key": "external-http:AGV-REQ-001",
                    "target_code": "http://agv.mock/api/v1/device/command",
                    "payload": {"command_code": "AGV-REQ-001"},
                    "source_system": "AGV",
                }
            ],
            wait=SimpleNamespace(wait_type="EXTERNAL_HTTP", wait_token="AGV-REQ-001", deadline_seconds=300),
            context_patch={"stage": "WAITING_AGV_DELIVERY"},
        )

        def capture_add(model: Any) -> None:
            added_models.append(model)

        mock_db.add = MagicMock(side_effect=capture_add)

        with (
            patch(
                "src.celery_app.tasks.workline._add_timeline",
                new=AsyncMock(side_effect=lambda _db, t: captured_timelines.append(t)),
            ),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={},
                orch_result=orch_result,
            )

        outboxes = [model for model in added_models if getattr(model, "dispatch_type", None) is not None]
        assert len(outboxes) == 1
        assert outboxes[0].dispatch_type == DispatchType.EXTERNAL_HTTP
        assert outboxes[0].target_type == TargetType.HTTP_ENDPOINT
        assert outboxes[0].dispatch_key == "external-http:AGV-REQ-001"
        assert session.status == "WAITING_EXTERNAL"
        assert session.current_wait_type == "EXTERNAL_HTTP"
        assert session.current_wait_token == "AGV-REQ-001"
        assert TimelineActionType.EXTERNAL_CALL_STARTED in [item.action_type for item in captured_timelines]

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
        mock_inbox_service.mark_as_processing.side_effect = ValueError("Already processing")

        with patch("src.app.workline.services.inbox_service.inbox_service", mock_inbox_service):
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
        assert entities["session"].id == 100
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
        mock_device1.device_role = "SCANNER"

        mock_device2 = MagicMock()
        mock_device2.id = 2
        mock_device2.device_role = "ROBOT_ARM"

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
            mock_device_repo_instance.get_by_work_line_id = AsyncMock(return_value=[mock_device1, mock_device2])
            MockDeviceRepo.return_value = mock_device_repo_instance

            entities = await _load_related_entities(mock_db, inbox)

        assert "SCANNER" in entities["devices_by_role"]
        assert "ROBOT_ARM" in entities["devices_by_role"]
        assert len(entities["devices_by_role"]["SCANNER"]) == 1

    @pytest.mark.asyncio
    async def test_load_external_http_entities_from_correlation_id(self, mock_db):
        """测试 EXTERNAL_HTTP 可先按 correlation_id 恢复 session，再回填 workline 和设备。"""
        from src.celery_app.tasks.workline import _load_related_entities

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.EXTERNAL_HTTP,
            correlation_id="corr-external-001",
            payload_json={"callback_type": "AGV_TASK_RESULT"},
        )

        mock_session = MockSession(session_id=100)
        mock_session.workline_id = 1
        mock_workline = MockWorkline(workline_id=1)

        mock_device = MagicMock()
        mock_device.id = 18
        mock_device.device_role = "OUTPUT_ARM"

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
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
            ) as MockCommandRepo,
            patch(
                "src.workline_runtime.session_resolver.session_resolver.resolve_or_create",
                new=AsyncMock(return_value=mock_session),
            ) as mock_resolve_or_create,
        ):
            mock_session_repo_instance = AsyncMock()
            mock_session_repo_instance.get_by_id = AsyncMock(return_value=None)
            MockSessionRepo.return_value = mock_session_repo_instance

            mock_workline_repo_instance = AsyncMock()
            mock_workline_repo_instance.get_by_id = AsyncMock(
                side_effect=lambda _db, workline_id: mock_workline if workline_id == 1 else None
            )
            MockWorklineRepo.return_value = mock_workline_repo_instance

            mock_device_repo_instance = AsyncMock()
            mock_device_repo_instance.get_by_id = AsyncMock(return_value=None)
            mock_device_repo_instance.get_by_work_line_id = AsyncMock(return_value=[mock_device])
            MockDeviceRepo.return_value = mock_device_repo_instance

            mock_command_repo_instance = AsyncMock()
            mock_command_repo_instance.get_by_id = AsyncMock(return_value=None)
            MockCommandRepo.return_value = mock_command_repo_instance

            entities = await _load_related_entities(mock_db, inbox)

        mock_resolve_or_create.assert_awaited_once()
        assert entities["session"] is mock_session
        assert entities["workline"] is mock_workline
        assert entities["devices_by_role"]["OUTPUT_ARM"] == [mock_device]
