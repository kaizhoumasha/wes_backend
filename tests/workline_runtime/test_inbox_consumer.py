"""
InboxConsumer 单元测试

测试 Celery 任务消费 WorklineInbox 的流程：
- 获取待处理消息
- 标记处理状态
- 调用 OrchestratorService
- 处理结果记录

设计参考: 设计文档 phase2-orchestrator
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import MissingGreenlet

from src.app.workline.models.inbox import InboxKind, InboxStatus
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.types import CommandIntent, CommandTargetScope, WaitIntent


def make_noop_lock():
    """创建空操作锁上下文管理器"""

    @asynccontextmanager
    async def noop_lock():
        yield

    return noop_lock()


def _loaded_entities(
    *,
    session: Any | None = None,
    workline: Any | None = None,
    device: Any | None = None,
    command: Any | None = None,
    devices_by_role: dict[str, list[Any]] | None = None,
    services: Any | None = None,
    safety_checked: bool = True,
) -> dict[str, Any]:
    return {
        "session": session,
        "workline": workline,
        "device": device,
        "command": command,
        "devices_by_role": devices_by_role or {},
        "services": services or MagicMock(),
        "safety_checked": safety_checked,
    }


def _required_await_args(mock: Any) -> Any:
    await_args = mock.await_args
    assert await_args is not None
    return await_args


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
        trace_id: str | None = None,
        payload_json: dict[str, Any] | None = None,
    ):
        self.id: int = inbox_id
        self.kind: InboxKind = kind
        self.status: InboxStatus = status
        self.session_id: int | None = session_id
        self.workline_id: int | None = workline_id
        self.device_id: int | None = device_id
        self.command_id: int | None = command_id
        self.trace_id: str | None = trace_id
        self.source_message_id: str | None = None
        self.event_id: str | None = None
        self.causation_id: str | None = None
        self._payload_json: dict[str, Any] = payload_json or {}
        self.processor_token: str | None = None
        self.processed_at: datetime | None = None
        self.error_message: str | None = None

    @property
    def payload_json(self) -> dict[str, Any]:
        return self._payload_json

    @payload_json.setter
    def payload_json(self, value: dict[str, Any]) -> None:
        self._payload_json = value


class RollbackExpiredInbox(MockInbox):
    """模拟 AsyncSession rollback 后 ORM 字段过期的 Inbox。"""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.expired = False

    @property
    def payload_json(self) -> dict[str, Any]:
        if self.expired:
            raise MissingGreenlet("expired payload_json attempted lazy load")
        return self._payload_json

    @payload_json.setter
    def payload_json(self, value: dict[str, Any]) -> None:
        self._payload_json = value


class MockSession:
    """模拟 WorklineSession"""

    def __init__(
        self,
        session_id: int = 12345,
        status: str = "RUNNING",
        context: dict[str, Any] | None = None,
    ):
        self.id: int = session_id
        self.workline_id: int | None = None
        self.status: str = status
        self.context: dict[str, Any] = context or {}
        self.trace_id: str | None = None
        self.plugin_key: str | None = None
        self.contract_version: str | None = None
        self.step_code: str | None = None
        self.awaiting_command_id: int | None = None
        self.ingress_count: int | None = None
        self.last_request_id: str | None = None
        self.last_ingress_at: datetime | None = None
        self._pending_session_ingress_metadata: dict[str, Any] | None = None


class MockWorkline:
    """模拟 WorkLine"""

    def __init__(
        self,
        workline_id: int = 1,
        plugin_class: type[Any] | None = None,
        state_machine_class: type[Any] | None = None,
    ):
        self.id: int = workline_id
        self.plugin_key: str | None = None
        self.contract_version: str | None = None
        self.plugin_class: type[Any] | None = plugin_class
        self.state_machine_class: type[Any] | None = state_machine_class


class CommitAwareDb:
    """最小提交感知 DB，用于验证失败状态是否真的在 commit 后持久化。"""

    def __init__(self) -> None:
        self._pending_actions: list[Any] = []
        self.commit = AsyncMock(side_effect=self._commit)
        self.rollback = AsyncMock(side_effect=self._rollback)

    def stage(self, action) -> None:
        self._pending_actions.append(action)

    async def _commit(self) -> None:
        for action in self._pending_actions:
            action()
        self._pending_actions.clear()

    async def _rollback(self) -> None:
        self._pending_actions.clear()


class CommitAwareInboxService:
    """最小 inbox service，只有 commit 后才会把失败状态真正落到 inbox 对象上。"""

    def __init__(self, inbox: MockInbox) -> None:
        self._inbox = inbox
        self.get_new_messages = AsyncMock(return_value=[inbox])
        self.mark_as_processed = AsyncMock()
        self.mark_as_failed = AsyncMock(side_effect=self._mark_as_failed)

    async def mark_as_processing(self, _db, inbox_id: int, processor_token: str, auto_commit: bool = False):
        assert auto_commit is False
        assert inbox_id == self._inbox.id
        self._inbox.status = InboxStatus.PROCESSING
        self._inbox.processor_token = processor_token
        return self._inbox

    async def _mark_as_failed(self, db, inbox_id: int, error_message: str, auto_commit: bool = False):
        assert auto_commit is False
        assert inbox_id == self._inbox.id

        def persist_failure() -> None:
            self._inbox.status = InboxStatus.FAILED
            self._inbox.error_message = error_message

        db.stage(persist_failure)
        return self._inbox


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
        process_inbox = AsyncMock(return_value=OrchestratorResult(success=True))

        async def _process_inbox(*args, **kwargs):
            _ = args
            result = process_inbox.return_value
            write_callback = kwargs.get("write_callback")
            if write_callback is not None and isinstance(result, OrchestratorResult) and result.success:
                await write_callback(result)
            return result

        process_inbox.side_effect = _process_inbox
        orchestrator.process_inbox = process_inbox
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
                AsyncMock(return_value=_loaded_entities(session=MockSession(), workline=MockWorkline())),
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

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            trace_id="trace-failed-001",
            payload_json={
                "canonical_event_type": "SCAN_COMPLETED",
                "data": {"LotCode": "LOT-FAILED-001"},
            },
        )
        inbox.source_message_id = "req-failed-001"
        mock_inbox_service.get_new_messages.return_value = [inbox]
        mock_orchestrator.process_inbox.return_value = OrchestratorResult(
            success=False,
            error="Processing failed",
            transition="dispatch_robot",
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
                        "device": None,
                        "command": None,
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
            patch("src.celery_app.tasks.workline._log_diagnostic") as mock_log_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1

        mock_inbox_service.mark_as_failed.assert_called_once()
        mock_log_diagnostic.assert_called_once()
        assert mock_log_diagnostic.call_args.kwargs["inbox"] is inbox
        assert mock_log_diagnostic.call_args.kwargs["transition"] == "dispatch_robot"
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_process_inbox_single_message_failure_preserves_mapped_device_domain(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """设备类 UNKNOWN failure 也应把 mapper 的 DEVICE 诊断域透传到最终诊断链路。"""
        from src.celery_app.tasks.workline import process_inbox_messages
        from src.workline_runtime.diagnostics import ErrorCode, ErrorDomain, ProblemClass
        from src.workline_runtime.types import FailureIntent

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            trace_id="trace-device-fault-001",
            payload_json={
                "canonical_event_type": "SCAN_COMPLETED",
                "device_code": "ARM01",
                "data": {"barcode": "BC-DEVICE-FAULT-001"},
            },
        )
        inbox.source_message_id = "req-device-fault-001"
        mock_inbox_service.get_new_messages.return_value = [inbox]
        mock_orchestrator.process_inbox.return_value = OrchestratorResult(
            success=False,
            error="设备故障: ARM01",
            failure=FailureIntent(domain="HARDWARE", code="DEVICE_FAULT", message="设备故障: ARM01"),
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
                        "device": None,
                        "command": None,
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
            patch("src.celery_app.tasks.workline._log_diagnostic") as mock_log_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        mock_log_diagnostic.assert_called_once()
        assert mock_log_diagnostic.call_args.kwargs["error_code"] == ErrorCode.UNKNOWN
        assert mock_log_diagnostic.call_args.kwargs["error_domain"] == ErrorDomain.DEVICE
        assert mock_log_diagnostic.call_args.kwargs["problem_class"] == ProblemClass.HARDWARE

    @pytest.mark.asyncio
    async def test_process_inbox_estop_short_circuits_to_safety_service(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """ESTOP_PRESSED 不进入 SessionResolver/插件编排，直接触发 WorkLine 安全服务。"""
        from src.celery_app.tasks.workline import process_inbox_messages

        payload = {
            "canonical_event_type": "ESTOP_PRESSED",
            "device_code": "ARM01",
            "data": None,
        }
        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            workline_id=7,
            device_id=11,
            command_id=22,
            trace_id="trace-estop-001",
            payload_json=payload,
        )
        mock_inbox_service.get_new_messages.return_value = [inbox]
        safety_service = MagicMock()
        safety_service.handle_estop = AsyncMock(return_value=SimpleNamespace(id=99))

        with (
            patch("src.app.workline.services.inbox_service.inbox_service", mock_inbox_service),
            patch("src.celery_app.tasks.workline.OrchestratorService", return_value=mock_orchestrator),
            patch(
                "src.celery_app.tasks.workline._load_related_entities",
                AsyncMock(
                    return_value=_loaded_entities(
                        session=None,
                        workline=MockWorkline(workline_id=7),
                        device=SimpleNamespace(id=11),
                        command=SimpleNamespace(id=22),
                    )
                ),
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 1
        assert result["failed"] == 0
        safety_service.handle_estop.assert_awaited_once_with(
            mock_db,
            workline_id=7,
            source_inbox_id=1,
            source_device_id=11,
            source_command_id=22,
            trigger_payload=payload,
        )
        mock_inbox_service.mark_as_processed.assert_called_once()
        mock_orchestrator.process_inbox.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_inbox_rejects_non_estop_when_workline_is_estopped(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """WorkLine 急停冻结后，非 ESTOP 事件不能再进入插件编排。"""
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked
        from src.celery_app.tasks.workline import process_inbox_messages

        payload = {
            "canonical_event_type": "SCAN_COMPLETED",
            "data": {"PkgID": "PKG-001"},
        }
        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            workline_id=7,
            trace_id="trace-estop-blocked-001",
            payload_json=payload,
        )
        mock_inbox_service.get_new_messages.return_value = [inbox]
        mock_orchestrator.process_inbox = AsyncMock(
            return_value=OrchestratorResult(success=False, error="should not orchestrate")
        )
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(
            side_effect=WorkLineSafetyBlocked("WORKLINE_ESTOPPED: workline_id=7")
        )

        with (
            patch("src.app.workline.services.inbox_service.inbox_service", mock_inbox_service),
            patch("src.celery_app.tasks.workline.OrchestratorService", return_value=mock_orchestrator),
            patch(
                "src.celery_app.tasks.workline._load_related_entities",
                AsyncMock(
                    return_value=_loaded_entities(
                        session=MockSession(),
                        workline=MockWorkline(workline_id=7),
                        safety_checked=False,
                    )
                ),
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()) as mock_record_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        safety_service.assert_accepting_work.assert_awaited_once_with(mock_db, workline_id=7)
        mock_orchestrator.process_inbox.assert_not_called()
        mock_record_diagnostic.assert_awaited_once()
        mock_inbox_service.mark_as_failed.assert_awaited_once()
        assert "WORKLINE_ESTOPPED" in mock_inbox_service.mark_as_failed.await_args.args[2]

    @pytest.mark.asyncio
    async def test_process_inbox_rejects_scan_finish_when_canonical_scan_completed_missing_barcode(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """canonical_event_type=SCAN_COMPLETED 时，vendor event 也必须命中扫码前置校验。"""
        from src.celery_app.tasks.workline import process_inbox_messages
        from src.workline_runtime.diagnostics import ErrorCode

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={
                "event_type": "SCAN_FINISH",
                "canonical_event_type": "SCAN_COMPLETED",
                "data": {},
            },
        )
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
            patch("src.celery_app.tasks.workline._log_diagnostic") as mock_log_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0}
        mock_orchestrator.process_inbox.assert_not_called()
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            "SCAN_COMPLETED 缺少条码信息（HHPN/MfrPN/Qty/DateCode/LotCode/PkgID 或 barcode）",
            auto_commit=False,
        )
        mock_log_diagnostic.assert_called_once_with(
            inbox=inbox,
            error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
            message="SCAN_COMPLETED 缺少条码信息（HHPN/MfrPN/Qty/DateCode/LotCode/PkgID 或 barcode）",
        )

    @pytest.mark.asyncio
    async def test_process_inbox_still_validates_legacy_scan_completed_missing_barcode(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """旧 payload 仅有 event_type=SCAN_COMPLETED 时，仍保持原有前置校验行为。"""
        from src.celery_app.tasks.workline import process_inbox_messages
        from src.workline_runtime.diagnostics import ErrorCode

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={
                "event_type": "SCAN_COMPLETED",
                "data": {},
            },
        )
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
            patch("src.celery_app.tasks.workline._log_diagnostic") as mock_log_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0}
        mock_orchestrator.process_inbox.assert_not_called()
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            "SCAN_COMPLETED 缺少条码信息（HHPN/MfrPN/Qty/DateCode/LotCode/PkgID 或 barcode）",
            auto_commit=False,
        )
        mock_log_diagnostic.assert_called_once_with(
            inbox=inbox,
            error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
            message="SCAN_COMPLETED 缺少条码信息（HHPN/MfrPN/Qty/DateCode/LotCode/PkgID 或 barcode）",
        )

    @pytest.mark.asyncio
    async def test_process_inbox_scan_completed_failure_is_persisted_after_commit(self):
        """malformed SCAN_COMPLETED 前置校验失败后，FAILED 状态必须在 commit 后真正落到 inbox。"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            status=InboxStatus.NEW,
            payload_json={
                "event_type": "SCAN_COMPLETED",
                "data": {},
            },
        )
        db = CommitAwareDb()
        inbox_service = CommitAwareInboxService(inbox)
        mock_orchestrator = MagicMock()
        mock_orchestrator.process_inbox = AsyncMock()

        with (
            patch("src.app.workline.services.inbox_service.inbox_service", inbox_service),
            patch(
                "src.celery_app.tasks.workline.OrchestratorService",
                return_value=mock_orchestrator,
            ),
            patch("src.celery_app.tasks.workline._log_diagnostic"),
        ):
            result = await process_inbox_messages._process_batch(db, limit=10)

        assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0}
        assert inbox.status == InboxStatus.FAILED
        assert inbox.error_message == "SCAN_COMPLETED 缺少条码信息（HHPN/MfrPN/Qty/DateCode/LotCode/PkgID 或 barcode）"
        db.commit.assert_awaited_once()
        mock_orchestrator.process_inbox.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_inbox_scan_completed_without_plugin_key_does_not_mask_orchestrator_failure(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """plugin_key 缺失时，SCAN_COMPLETED 通用 gate 不应抢先覆盖编排失败归因。"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            payload_json={
                "canonical_event_type": "SCAN_COMPLETED",
                "data": {"LotCode": "LOT-NO-PLUGIN-001"},
            },
        )
        mock_inbox_service.get_new_messages.return_value = [inbox]
        mock_orchestrator.process_inbox.return_value = OrchestratorResult(
            success=False,
            error="Processing failed",
            transition="dispatch_robot",
        )

        workline = MockWorkline()
        workline.plugin_key = None

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
                        "workline": workline,
                        "device": None,
                        "command": None,
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
            patch("src.celery_app.tasks.workline._log_diagnostic") as mock_log_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0}
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            "Processing failed",
            auto_commit=False,
        )
        assert mock_log_diagnostic.call_args.kwargs["transition"] == "dispatch_robot"

    async def test_process_inbox_missing_session_context_logs_once_with_trace_anchor(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """session/workline 缺失应单次明确归因，不再掉进 UNKNOWN 二次诊断。"""
        from src.celery_app.tasks.workline import process_inbox_messages
        from src.workline_runtime.diagnostics import ErrorCode

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            trace_id="trace-missing-001",
            payload_json={
                "canonical_event_type": "SCAN_COMPLETED",
                "data": {"LotCode": "LOT-MISSING-001"},
            },
        )
        inbox.source_message_id = "req-missing-001"
        mock_inbox_service.get_new_messages.return_value = [inbox]
        mock_orchestrator.process_inbox.return_value = OrchestratorResult(success=True)

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
                        "session": None,
                        "workline": MockWorkline(),
                        "device": None,
                        "command": None,
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
            patch("src.celery_app.tasks.workline._log_diagnostic") as mock_log_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            "Inbox processing missing session/workline context",
            auto_commit=False,
        )
        mock_log_diagnostic.assert_called_once_with(
            inbox=inbox,
            error_code=ErrorCode.SESSION_CONTEXT_MISSING,
            message="Inbox processing missing session/workline context",
            session=None,
            workline=mock_log_diagnostic.call_args.kwargs["workline"],
            device=None,
            command=None,
        )
        assert mock_log_diagnostic.call_args.kwargs["inbox"] is inbox
        assert mock_log_diagnostic.call_args.kwargs["workline"] is not None
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
                        "device": None,
                        "command": None,
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
    async def test_process_inbox_session_resolve_failure_maps_to_session_resolve_failed(
        self,
        mock_db,
        mock_inbox_service,
    ):
        """session 归属解析失败应明确归因到 SESSION_RESOLVE_FAILED，而不是 UNKNOWN。"""
        from src.celery_app.tasks.workline import process_inbox_messages
        from src.workline_runtime.diagnostics import ErrorCode
        from src.workline_runtime.session_resolver import SessionResolveError

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            trace_id="trace-resolve-failed-001",
            payload_json={
                "canonical_event_type": "BIN_ARRIVED",
                "data": {"location": "STATION_INPUT1"},
            },
        )
        inbox.source_message_id = "req-resolve-failed-001"
        mock_inbox_service.get_new_messages.return_value = [inbox]

        with (
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.celery_app.tasks.workline._load_related_entities",
                AsyncMock(
                    side_effect=SessionResolveError(
                        "Unable to resolve stable business_key from payload: missing business_key, barcode, and canonical Six-In-One data"
                    )
                ),
            ),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()) as mock_record_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            "Unable to resolve stable business_key from payload: missing business_key, barcode, and canonical Six-In-One data",
            auto_commit=False,
        )
        mock_record_diagnostic.assert_awaited_once()
        await_args = _required_await_args(mock_record_diagnostic)
        assert await_args.args[0] is mock_db
        diagnostic_kwargs = await_args.kwargs
        assert diagnostic_kwargs["inbox"].id == inbox.id
        assert diagnostic_kwargs["inbox"].trace_id == inbox.trace_id
        assert diagnostic_kwargs["error_code"] == ErrorCode.SESSION_RESOLVE_FAILED
        assert (
            diagnostic_kwargs["message"]
            == "Unable to resolve stable business_key from payload: missing business_key, barcode, and canonical Six-In-One data"
        )
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_process_inbox_session_resolve_failure_uses_snapshot_after_rollback(
        self,
        mock_db,
        mock_inbox_service,
    ):
        """rollback 后 ORM 字段可能过期，诊断必须使用事前快照。"""
        from src.celery_app.tasks.workline import process_inbox_messages, workline_diagnostic_service
        from src.workline_runtime.diagnostics import ErrorCode
        from src.workline_runtime.session_resolver import SessionResolveError

        inbox = RollbackExpiredInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            trace_id="trace-expired-rollback-001",
            payload_json={
                "canonical_event_type": "SCAN_COMPLETED",
                "device_code": "ARM01",
                "data": {"LotCode": "LOT-EXPIRED-001"},
            },
        )
        inbox.source_message_id = "req-expired-rollback-001"
        mock_inbox_service.get_new_messages.return_value = [inbox]

        async def expire_inbox_on_rollback() -> None:
            inbox.expired = True

        mock_db.rollback = AsyncMock(side_effect=expire_inbox_on_rollback)

        with (
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.celery_app.tasks.workline._load_related_entities",
                AsyncMock(
                    side_effect=SessionResolveError(
                        "Unable to resolve stable business_key from payload: missing plugin business key"
                    )
                ),
            ),
            patch.object(workline_diagnostic_service, "record_event", new=AsyncMock()) as mock_record_event,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["failed"] == 1
        mock_db.rollback.assert_awaited()
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            "Unable to resolve stable business_key from payload: missing plugin business key",
            auto_commit=False,
        )
        mock_record_event.assert_awaited_once()
        event = _required_await_args(mock_record_event).kwargs["event"]
        assert event.error_code == ErrorCode.SESSION_RESOLVE_FAILED
        assert event.context.trace_id == "trace-expired-rollback-001"
        assert event.context.request_id == "req-expired-rollback-001"
        assert event.context.canonical_event_type == "SCAN_COMPLETED"

    @pytest.mark.asyncio
    async def test_process_inbox_exception_handling(
        self,
        mock_db,
        mock_inbox_service,
    ):
        """测试处理过程中异常"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            trace_id="trace-exception-001",
            payload_json={
                "canonical_event_type": "SCAN_COMPLETED",
                "data": {"LotCode": "LOT-EXCEPTION-001"},
            },
        )
        inbox.source_message_id = "req-exception-001"
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
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()) as mock_record_diagnostic,
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
        mock_record_diagnostic.assert_awaited_once()
        await_args = _required_await_args(mock_record_diagnostic)
        assert await_args.args[0] is mock_db
        diagnostic_kwargs = await_args.kwargs
        assert diagnostic_kwargs["inbox"].id == inbox.id
        assert diagnostic_kwargs["inbox"].trace_id == inbox.trace_id
        assert diagnostic_kwargs["message"] == "Entity not found"
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_process_inbox_generic_exception_uses_snapshot_after_rollback(
        self,
        mock_db,
        mock_inbox_service,
    ):
        """通用异常分支 rollback 后也不能访问已过期 ORM 字段。"""
        from src.celery_app.tasks.workline import process_inbox_messages, workline_diagnostic_service
        from src.workline_runtime.diagnostics import ErrorCode

        inbox = RollbackExpiredInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            trace_id="trace-generic-expired-001",
            payload_json={
                "canonical_event_type": "SCAN_COMPLETED",
                "device_code": "ARM01",
                "data": {"LotCode": "LOT-GENERIC-EXPIRED-001"},
            },
        )
        inbox.source_message_id = "req-generic-expired-001"
        mock_inbox_service.get_new_messages.return_value = [inbox]

        async def expire_inbox_on_rollback() -> None:
            inbox.expired = True

        mock_db.rollback = AsyncMock(side_effect=expire_inbox_on_rollback)

        with (
            patch(
                "src.app.workline.services.inbox_service.inbox_service",
                mock_inbox_service,
            ),
            patch(
                "src.celery_app.tasks.workline._load_related_entities",
                AsyncMock(side_effect=ValueError("Entity not found")),
            ),
            patch.object(workline_diagnostic_service, "record_event", new=AsyncMock()) as mock_record_event,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["failed"] == 1
        mock_db.rollback.assert_awaited()
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            "Entity not found",
            auto_commit=False,
        )
        mock_record_event.assert_awaited_once()
        event = _required_await_args(mock_record_event).kwargs["event"]
        assert event.error_code == ErrorCode.UNKNOWN
        assert event.context.trace_id == "trace-generic-expired-001"
        assert event.context.request_id == "req-generic-expired-001"
        assert event.context.canonical_event_type == "SCAN_COMPLETED"

    @pytest.mark.asyncio
    async def test_process_inbox_timeout_logs_diagnostic_with_trace_anchor(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """timeout 分支也必须把原始 inbox trace 锚点透传给 diagnostics。"""
        from src.celery_app.constants import INBOX_PROCESS_TIMEOUT_SECONDS
        from src.celery_app.tasks.workline import process_inbox_messages
        from src.workline_runtime.diagnostics import ErrorCode

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            trace_id="trace-timeout-001",
            payload_json={
                "canonical_event_type": "SCAN_COMPLETED",
                "data": {"LotCode": "LOT-TIMEOUT-001"},
            },
        )
        inbox.source_message_id = "req-timeout-001"
        mock_inbox_service.get_new_messages.return_value = [inbox]

        async def raise_timeout(coro, timeout):
            _ = timeout
            coro.close()
            raise TimeoutError

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
                        "device": None,
                        "command": None,
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
            patch(
                "src.celery_app.tasks.workline.asyncio.wait_for",
                new=AsyncMock(side_effect=raise_timeout),
            ),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()) as mock_record_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            f"处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
            auto_commit=False,
        )
        mock_record_diagnostic.assert_awaited_once()
        await_args = _required_await_args(mock_record_diagnostic)
        assert await_args.args[0] is mock_db
        diagnostic_kwargs = await_args.kwargs
        assert diagnostic_kwargs["inbox"].id == inbox.id
        assert diagnostic_kwargs["inbox"].trace_id == inbox.trace_id
        assert diagnostic_kwargs["error_code"] == ErrorCode.DEVICE_TIMEOUT
        assert diagnostic_kwargs["message"] == f"Inbox processing timeout (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)"
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_process_inbox_timeout_uses_snapshot_after_rollback(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """timeout 分支 rollback 后也必须使用 Inbox 快照写诊断。"""
        from src.celery_app.constants import INBOX_PROCESS_TIMEOUT_SECONDS
        from src.celery_app.tasks.workline import process_inbox_messages, workline_diagnostic_service
        from src.workline_runtime.diagnostics import ErrorCode

        inbox = RollbackExpiredInbox(
            inbox_id=1,
            kind=InboxKind.DEVICE_EVENT,
            trace_id="trace-timeout-expired-001",
            payload_json={
                "canonical_event_type": "SCAN_COMPLETED",
                "device_code": "ARM01",
                "data": {"LotCode": "LOT-TIMEOUT-EXPIRED-001"},
            },
        )
        inbox.source_message_id = "req-timeout-expired-001"
        mock_inbox_service.get_new_messages.return_value = [inbox]

        async def expire_inbox_on_rollback() -> None:
            inbox.expired = True

        async def raise_timeout(coro, timeout):
            _ = timeout
            coro.close()
            raise TimeoutError

        mock_db.rollback = AsyncMock(side_effect=expire_inbox_on_rollback)

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
                        "device": None,
                        "command": None,
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
            patch(
                "src.celery_app.tasks.workline.asyncio.wait_for",
                new=AsyncMock(side_effect=raise_timeout),
            ),
            patch.object(workline_diagnostic_service, "record_event", new=AsyncMock()) as mock_record_event,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["failed"] == 1
        mock_db.rollback.assert_awaited()
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            f"处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
            auto_commit=False,
        )
        mock_record_event.assert_awaited_once()
        event = _required_await_args(mock_record_event).kwargs["event"]
        assert event.error_code == ErrorCode.DEVICE_TIMEOUT
        assert event.context.trace_id == "trace-timeout-expired-001"
        assert event.context.request_id == "req-timeout-expired-001"
        assert event.context.canonical_event_type == "SCAN_COMPLETED"

    def test_log_diagnostic_uses_inbox_trace_fields(self) -> None:
        """Workline diagnostics 应直接复用 inbox trace 字段，而不是重新发明 request 锚点。"""
        from src.celery_app.tasks.workline import _log_diagnostic
        from src.workline_runtime.diagnostics import ErrorCode

        inbox = SimpleNamespace(
            id=303,
            source_message_id="req-diag-001",
            trace_id="trace-diag-001",
            payload_json={"canonical_event_type": "SCAN_COMPLETED"},
        )
        session = SimpleNamespace(id=123, trace_id="trace-diag-001")
        workline = SimpleNamespace(id=1, line_code="WL-01", plugin_key="smt_classifier")

        with patch("src.celery_app.tasks.workline.logger.warning") as mock_warning:
            _log_diagnostic(
                inbox=inbox,
                error_code=ErrorCode.DEVICE_TIMEOUT,
                message="device timeout",
                session=session,
                workline=workline,
                transition="wait_device_result",
            )

        mock_warning.assert_called_once()
        log_message = mock_warning.call_args.args[0]
        assert log_message.startswith("[WorklineDiagnostic] ")
        payload = json.loads(log_message.removeprefix("[WorklineDiagnostic] "))
        assert payload["context"]["request_id"] == "req-diag-001"
        assert payload["context"]["trace_id"] == "trace-diag-001"
        assert payload["context"]["canonical_event_type"] == "SCAN_COMPLETED"
        assert payload["context"]["transition"] == "wait_device_result"
        assert payload["context"]["inbox_id"] == 303

    @pytest.mark.asyncio
    async def test_process_inbox_write_callback_rolls_back_and_marks_failed(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """锁内写回异常必须回滚，不能保留半写入状态。"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(inbox_id=1, kind=InboxKind.DEVICE_EVENT)
        mock_inbox_service.get_new_messages.return_value = [inbox]
        mock_orchestrator.process_inbox.return_value = OrchestratorResult(success=True)

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
                        "device": None,
                        "command": None,
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
            patch(
                "src.celery_app.tasks.workline._apply_orchestrator_effects",
                AsyncMock(side_effect=RuntimeError("write failed")),
            ),
            patch("src.celery_app.tasks.workline._log_diagnostic") as mock_log_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        mock_db.rollback.assert_awaited()
        mock_inbox_service.mark_as_processed.assert_not_called()
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            "write failed",
            auto_commit=False,
        )
        mock_log_diagnostic.assert_called_once()
        assert mock_log_diagnostic.call_args.kwargs["message"] == "write failed"

    @pytest.mark.asyncio
    async def test_process_inbox_rejects_stale_session_write_under_write_lock(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """若同 session 已被其他事务推进，当前 stale 编排结果不得继续写入。"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(inbox_id=1, kind=InboxKind.DEVICE_EVENT)
        mock_inbox_service.get_new_messages.return_value = [inbox]
        mock_orchestrator.process_inbox.return_value = OrchestratorResult(success=True)
        session = MockSession(status="RUNNING")
        session.step_code = "WAITING_DEVICE_RESULT"
        session.awaiting_command_id = None

        async def refresh_session(target):
            target.status = "WAITING_DEVICE_RESULT"

        mock_db.refresh = AsyncMock(side_effect=refresh_session)

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
                        "session": session,
                        "workline": MockWorkline(),
                        "device": None,
                        "command": None,
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
            patch(
                "src.celery_app.tasks.workline._apply_orchestrator_effects",
                AsyncMock(),
            ) as mock_apply_effects,
            patch("src.celery_app.tasks.workline._log_diagnostic") as mock_log_diagnostic,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        mock_db.rollback.assert_awaited()
        mock_apply_effects.assert_not_called()
        mock_inbox_service.mark_as_failed.assert_called_once_with(
            mock_db,
            inbox.id,
            "Session state changed before WRITE apply; refusing stale orchestrator effects",
            auto_commit=False,
        )
        assert mock_log_diagnostic.call_args.kwargs["message"] == (
            "Session state changed before WRITE apply; refusing stale orchestrator effects"
        )

    @pytest.mark.asyncio
    async def test_process_inbox_reapplies_pending_ingress_metadata_after_refresh(
        self,
        mock_db,
        mock_inbox_service,
        mock_orchestrator,
    ):
        """复用已有 session 的 ingress 元数据在锁内 refresh 后仍必须可靠持久化。"""
        from src.celery_app.tasks.workline import process_inbox_messages

        inbox = MockInbox(inbox_id=1, kind=InboxKind.DEVICE_EVENT)
        mock_inbox_service.get_new_messages.return_value = [inbox]
        mock_orchestrator.process_inbox.return_value = OrchestratorResult(success=True)

        observed_at = datetime.now()
        session = MockSession(status="RUNNING")
        session.step_code = "WAITING_DEVICE_RESULT"
        session.awaiting_command_id = None
        session.ingress_count = 2
        session.last_request_id = "req-new"
        session.last_ingress_at = observed_at
        session._pending_session_ingress_metadata = {
            "ingress_count": 2,
            "last_request_id": "req-new",
            "last_ingress_at": observed_at,
        }

        async def refresh_session(target):
            target.status = "RUNNING"
            target.step_code = "WAITING_DEVICE_RESULT"
            target.awaiting_command_id = None
            target.ingress_count = 1
            target.last_request_id = "req-old"
            target.last_ingress_at = None

        async def assert_ingress_reapplied(_db, *, session, **kwargs):
            _ = kwargs
            assert session.ingress_count == 2
            assert session.last_request_id == "req-new"
            assert session.last_ingress_at == observed_at

        mock_db.refresh = AsyncMock(side_effect=refresh_session)

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
                        "session": session,
                        "workline": MockWorkline(),
                        "device": None,
                        "command": None,
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
            patch(
                "src.celery_app.tasks.workline._apply_orchestrator_effects",
                AsyncMock(side_effect=assert_ingress_reapplied),
            ) as mock_apply_effects,
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 1
        assert result["success"] == 1
        assert result["failed"] == 0
        mock_db.refresh.assert_awaited_once_with(session)
        mock_apply_effects.assert_awaited_once()
        mock_inbox_service.mark_as_processed.assert_called_once_with(mock_db, inbox.id, auto_commit=False)
        assert session.ingress_count == 2
        assert session.last_request_id == "req-new"
        assert session.last_ingress_at == observed_at

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
                    side_effect=lambda *_args, **_kwargs: {
                        "session": MockSession(),
                        "workline": MockWorkline(),
                        "device": None,
                        "command": None,
                        "devices_by_role": {},
                        "services": MagicMock(),
                    }
                ),
            ),
            patch(
                "src.celery_app.tasks.workline._apply_orchestrator_effects",
                new=AsyncMock(),
            ),
        ):
            result = await process_inbox_messages._process_batch(mock_db, limit=10)

        assert result["processed"] == 3
        assert result["success"] == 3
        assert result["failed"] == 0
        assert mock_inbox_service.mark_as_processed.call_count == 3

    @pytest.mark.asyncio
    async def test_emit_timeline_assigns_monotonic_seq_no_within_same_batch(self, mock_db):
        """同一批 effect 内的多条 timeline 必须共享同一条递增序列。"""
        from src.celery_app.tasks.workline import EffectApplyContext, _emit_timeline
        from src.workline_runtime.trace_context import TraceContext

        session = SimpleNamespace(id=123, workline_id=1)
        captured_timelines: list[Any] = []

        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = 5
        mock_db.execute = AsyncMock(return_value=scalar_result)
        mock_db.add = MagicMock(side_effect=lambda timeline: captured_timelines.append(timeline))

        ctx: EffectApplyContext = {
            "db": mock_db,
            "session": session,
            "workline": None,
            "inbox": None,
            "devices_by_role": {},
            "source_device": None,
            "orch_result": OrchestratorResult(success=True),
            "current_status": None,
            "trace_id": None,
            "trace": TraceContext.from_runtime(session=session),
            "session_ctx": {},
            "now": datetime.now(),
            "awaiting_command_id": None,
            "awaiting_command_code": None,
            "next_timeline_seq_no": None,
        }

        with patch(
            "src.workline_runtime.timeline_generator.timeline_generator.generate",
            side_effect=[
                SimpleNamespace(session_id=123, seq_no=None, action_type="COMMAND_SENT"),
                SimpleNamespace(session_id=123, seq_no=None, action_type="WAIT_STARTED"),
            ],
        ):
            await _emit_timeline(ctx, stage="DISPATCH_PREPARE", action_type="COMMAND_SENT")
            await _emit_timeline(ctx, stage="WAITING", action_type="WAIT_STARTED")

        assert [item.seq_no for item in captured_timelines] == [6, 7]
        assert ctx["next_timeline_seq_no"] == 8
        mock_db.execute.assert_called_once()

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
            trace_id=None,
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
        inbox = SimpleNamespace(id=1, trace_id="trace-001")
        orch_result = OrchestratorResult(
            success=True,
            transition="manual_hold",
            context_patch={"manual_hold": True},
        )

        with (
            patch("src.celery_app.tasks.workline._add_timeline", new=AsyncMock()),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate",
                return_value=SimpleNamespace(),
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={},
                source_device=None,
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
            trace_id=None,
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
        inbox = SimpleNamespace(id=1, trace_id="trace-001")
        orch_result = OrchestratorResult(
            success=True,
            transition="manual_resume",
            context_patch={"manual_hold": False},
        )

        with (
            patch("src.celery_app.tasks.workline._add_timeline", new=AsyncMock()),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate",
                return_value=SimpleNamespace(),
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={},
                source_device=None,
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
            trace_id=None,
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
        inbox = SimpleNamespace(id=1, trace_id="trace-001")
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
                new=AsyncMock(side_effect=lambda _db, t, **_kwargs: captured_timelines.append(t)),
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
                source_device=None,
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
            trace_id="trace-external-001",
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
        inbox = SimpleNamespace(
            id=99,
            trace_id="trace-external-001",
            source_message_id="req-external-001",
            payload_json={"canonical_event_type": "AGV_REQUESTED"},
        )
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
            wait=WaitIntent(
                wait_type="EXTERNAL_HTTP",
                wait_token="AGV-REQ-001",
                deadline_seconds=300,
            ),
            context_patch={"stage": "WAITING_AGV_DELIVERY"},
        )

        def capture_add(model: Any) -> None:
            added_models.append(model)

        mock_db.add = MagicMock(side_effect=capture_add)

        with (
            patch(
                "src.celery_app.tasks.workline._add_timeline",
                new=AsyncMock(side_effect=lambda _db, t, **_kwargs: captured_timelines.append(t)),
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
                source_device=None,
                orch_result=orch_result,
            )

        outboxes = [model for model in added_models if getattr(model, "dispatch_type", None) is not None]
        assert len(outboxes) == 1
        assert outboxes[0].dispatch_type == DispatchType.EXTERNAL_HTTP
        assert outboxes[0].target_type == TargetType.HTTP_ENDPOINT
        assert outboxes[0].dispatch_key == "external-http:AGV-REQ-001"
        assert outboxes[0].target_code == "http://agv.mock/api/v1/device/command"
        assert outboxes[0].payload_json == {"command_code": "AGV-REQ-001"}
        assert session.status == "WAITING_EXTERNAL"
        assert session.current_wait_type == "EXTERNAL_HTTP"
        assert session.current_wait_token == "AGV-REQ-001"
        assert TimelineActionType.EXTERNAL_CALL_STARTED in [item.action_type for item in captured_timelines]
        external_timeline = next(
            item for item in captured_timelines if item.action_type == TimelineActionType.EXTERNAL_CALL_STARTED
        )
        assert external_timeline.payload["request_id"] == "req-external-001"
        assert external_timeline.payload["trace_id"] == "trace-external-001"
        assert external_timeline.payload["canonical_event_type"] == "AGV_REQUESTED"
        assert external_timeline.payload["dispatch_key"] == "external-http:AGV-REQ-001"

    @pytest.mark.asyncio
    async def test_apply_orchestrator_effects_creates_command_before_wait_transition(self, mock_db):
        """命令 effect 应先创建 command/outbox，再把 Session 推进到等待态。

        这个顺序是 Phase 2 拆分后最关键的执行边界：
        - context patch 先写入 session
        - command create payload 读取更新后的 plugin_state 投影 / plugin snapshot
        - wait transition 再引用首条 awaiting_command_id
        """
        from src.app.workline.models.outbox import DispatchType
        from src.app.workline.models.timeline import TimelineActionType
        from src.celery_app.tasks.workline import _apply_orchestrator_effects

        captured_timelines: list[Any] = []
        added_models: list[Any] = []
        created_command_payloads: list[dict[str, Any]] = []

        session = SimpleNamespace(
            id=123,
            workline_id=1,
            status="RUNNING",
            context_json={"stage": "PREPARE"},
            trace_id="trace-session-legacy-001",
            plugin_key=None,
            contract_version="legacy-0.9",
            step_code=None,
            last_inbox_id=None,
            current_wait_type=None,
            current_wait_token=None,
            waiting_since=None,
            deadline_at=None,
            awaiting_command_id=None,
            ended_at=None,
            failure_domain="OLD",
            failure_code="OLD",
            failure_message="OLD",
        )
        workline = SimpleNamespace(plugin_key="demo_plugin", contract_version="wl-v2026.04")
        inbox = SimpleNamespace(
            id=101,
            trace_id="trace-inbox-001",
            source_message_id="req-command-001",
            payload_json={"canonical_event_type": "SCAN_COMPLETED"},
        )
        target_device = SimpleNamespace(id=8, device_code="ROBOT-001", device_role="ROBOT")
        orch_result = OrchestratorResult(
            success=True,
            transition="dispatch_robot",
            context_patch={"plugin_state": "SCAN_01", "barcode": "BC-001"},
            commands=[
                CommandIntent(
                    target_device_id=8,
                    action="PICK_AND_PUT",
                    parameters={"command_code": "VENDOR-CMD-001", "priority": 9, "barcode": "BC-001"},
                )
            ],
            wait=WaitIntent(
                wait_type="COMMAND_RESULT",
                wait_token="VENDOR-CMD-001",
                deadline_seconds=180,
            ),
        )

        mock_command_repo = MagicMock()

        def create_command(_db: Any, payload: dict[str, Any]) -> Any:
            created_command_payloads.append(payload)
            return SimpleNamespace(
                id=321,
                command_code=payload["command_code"],
                params=payload["params"],
                task_type=payload["task_type"],
                priority=payload["priority"],
                timeout_ms=payload["timeout_ms"],
            )

        mock_command_repo.create = AsyncMock(side_effect=create_command)

        def capture_add(model: Any) -> None:
            added_models.append(model)

        mock_db.add = MagicMock(side_effect=capture_add)

        with (
            patch(
                "src.celery_app.tasks.workline._add_timeline",
                new=AsyncMock(side_effect=lambda _db, t, **_kwargs: captured_timelines.append(t)),
            ),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            patch(
                "src.celery_app.tasks.workline.get_plugin_contract_version",
                return_value="registry-v1999.01",
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={"ROBOT": [target_device]},
                source_device=None,
                orch_result=orch_result,
            )

        outboxes = [model for model in added_models if getattr(model, "dispatch_type", None) is not None]
        assert len(outboxes) == 1
        assert outboxes[0].dispatch_type == DispatchType.DEVICE_COMMAND
        assert outboxes[0].dispatch_key == "device-command:VENDOR-CMD-001"
        assert outboxes[0].target_code == "ROBOT-001"
        assert outboxes[0].payload_json["command_code"] == "VENDOR-CMD-001"
        assert outboxes[0].payload_json["device_code"] == "ROBOT-001"
        assert outboxes[0].payload_json["task_type"] == "PICK_AND_PUT"
        assert outboxes[0].payload_json["params"] == {"barcode": "BC-001"}

        assert len(created_command_payloads) == 1
        assert created_command_payloads[0]["plugin_key"] == "demo_plugin"
        assert created_command_payloads[0]["contract_version"] == "wl-v2026.04"
        assert created_command_payloads[0]["step_code"] == "SCAN_01"
        assert created_command_payloads[0]["task_type"] == "PICK_AND_PUT"
        assert created_command_payloads[0]["trace_id"] == "trace-inbox-001"
        assert created_command_payloads[0]["params"] == {"barcode": "BC-001"}

        assert session.trace_id == "trace-inbox-001"
        assert session.plugin_key == "demo_plugin"
        assert session.contract_version == "wl-v2026.04"
        assert session.step_code == "SCAN_01"
        assert session.barcode == "BC-001"
        assert session.status == "WAITING_DEVICE_RESULT"
        assert session.current_wait_type == "COMMAND_RESULT"
        assert session.current_wait_token == "VENDOR-CMD-001"
        assert session.awaiting_command_id == 321
        assert session.failure_domain is None
        assert session.failure_code is None
        assert session.failure_message is None
        assert [item.action_type for item in captured_timelines] == [
            TimelineActionType.DECISION_MADE,
            TimelineActionType.COMMAND_SENT,
            TimelineActionType.WAIT_STARTED,
        ]
        assert captured_timelines[0].payload == {
            "request_id": "req-command-001",
            "trace_id": "trace-inbox-001",
            "event_id": None,
            "causation_id": None,
            "canonical_event_type": "SCAN_COMPLETED",
            "transition": "dispatch_robot",
            "context_patch": {"plugin_state": "SCAN_01", "barcode": "BC-001"},
        }
        assert captured_timelines[1].payload == {
            "request_id": "req-command-001",
            "trace_id": "trace-inbox-001",
            "event_id": None,
            "causation_id": None,
            "canonical_event_type": "SCAN_COMPLETED",
            "command_code": "VENDOR-CMD-001",
            "command_type": "PICK_AND_PUT",
            "dispatch_key": "device-command:VENDOR-CMD-001",
            "parameters": {"barcode": "BC-001"},
        }
        assert captured_timelines[2].payload == {
            "request_id": "req-command-001",
            "trace_id": "trace-inbox-001",
            "event_id": None,
            "causation_id": None,
            "canonical_event_type": "SCAN_COMPLETED",
            "wait_type": "COMMAND_RESULT",
            "wait_token": "VENDOR-CMD-001",
            "deadline_seconds": 180,
        }

    @pytest.mark.asyncio
    async def test_apply_orchestrator_effects_resolves_downstream_target_from_topology(self, mock_db):
        """scope + role 命令应在 runtime 中基于 source device 和拓扑解析目标设备。"""
        from src.app.workline.models.outbox import DispatchType
        from src.celery_app.tasks.workline import _apply_orchestrator_effects

        added_models: list[Any] = []
        created_command_payloads: list[dict[str, Any]] = []

        session = SimpleNamespace(
            id=223,
            workline_id=1,
            status="RUNNING",
            context_json={"stage": "PREPARE"},
            trace_id="trace-session-topology-001",
            plugin_key=None,
            contract_version="legacy-0.9",
            step_code=None,
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
        workline = SimpleNamespace(plugin_key="demo_plugin", contract_version="wl-v2026.04")
        inbox = SimpleNamespace(
            id=202,
            trace_id="trace-inbox-topology-001",
            source_message_id="req-topology-001",
            payload_json={"canonical_event_type": "MEASUREMENT_REEL"},
        )
        source_device = SimpleNamespace(id=7, device_code="INPUT-ARM-01", device_role="INPUT_ARM")
        conveyor = SimpleNamespace(
            id=8,
            device_code="CONVEYOR-01",
            device_role="CONVEYOR",
            upstream_device_id=7,
            role_index=1,
            sort_order=1,
        )
        orch_result = OrchestratorResult(
            success=True,
            commands=[
                CommandIntent(
                    action="MOVE_FORWARD",
                    target_scope=CommandTargetScope.DOWNSTREAM,
                    device_role="CONVEYOR",
                    parameters={"command_code": "VENDOR-CMD-TOPO-001"},
                )
            ],
        )

        command_model = SimpleNamespace(
            id=654,
            command_code="VENDOR-CMD-TOPO-001",
            params={
                "command_code": "VENDOR-CMD-TOPO-001",
                "task_type": "MOVE_FORWARD",
                "timeout": 300000,
                "timestamp": 1710000000000,
            },
            task_type="MOVE_FORWARD",
            priority=5,
            timeout_ms=300000,
        )
        mock_command_repo = MagicMock()
        mock_command_repo.create = AsyncMock(
            side_effect=lambda _db, payload: created_command_payloads.append(payload) or command_model
        )
        mock_db.add = MagicMock(side_effect=lambda model: added_models.append(model))

        with (
            patch(
                "src.celery_app.tasks.workline._add_timeline",
                new=AsyncMock(side_effect=lambda *_args, **_kwargs: None),
            ),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            patch(
                "src.celery_app.tasks.workline.get_plugin_contract_version",
                return_value="registry-v1999.01",
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={"INPUT_ARM": [source_device], "CONVEYOR": [conveyor]},
                source_device=source_device,
                orch_result=orch_result,
            )

        outboxes = [model for model in added_models if getattr(model, "dispatch_type", None) is not None]
        assert len(created_command_payloads) == 1
        assert created_command_payloads[0]["device_id"] == 8
        assert created_command_payloads[0]["task_type"] == "MOVE_FORWARD"
        assert len(outboxes) == 1
        assert outboxes[0].dispatch_type == DispatchType.DEVICE_COMMAND
        assert outboxes[0].target_code == "CONVEYOR-01"

    @pytest.mark.asyncio
    async def test_apply_orchestrator_effects_rejects_unsupported_command_type(self, mock_db):
        """supports_command_types 必须在命令创建前被运行时消费。"""
        from src.app.workline.models.timeline import TimelineActionType
        from src.celery_app.tasks.workline import _apply_orchestrator_effects

        captured_timelines: list[Any] = []
        added_models: list[Any] = []

        session = SimpleNamespace(
            id=456,
            workline_id=1,
            status="RUNNING",
            context_json={},
            trace_id=None,
            plugin_key=None,
            contract_version=None,
            step_code=None,
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
        workline = SimpleNamespace(plugin_key="demo_plugin")
        inbox = SimpleNamespace(
            id=201,
            trace_id="trace-unsupported-001",
            source_message_id="req-unsupported-001",
            payload_json={"canonical_event_type": "SCAN_COMPLETED"},
        )
        target_device = SimpleNamespace(
            id=99,
            device_code="ROBOT-001",
            capabilities_json={"supports_command_types": ["MOVE_FORWARD"]},
            maintenance_mode=False,
        )
        orch_result = OrchestratorResult(
            success=True,
            transition="dispatch_robot",
            context_patch={"plugin_state": "SCAN_01"},
            commands=[
                CommandIntent(
                    target_device_id=99,
                    action="PICK_AND_PUT",
                    parameters={"command_code": "VENDOR-CMD-001"},
                )
            ],
            wait=WaitIntent(
                wait_type="COMMAND_RESULT",
                wait_token="VENDOR-CMD-001",
                deadline_seconds=180,
            ),
        )
        mock_command_repo = MagicMock()
        mock_command_repo.create = AsyncMock()
        mock_db.add = MagicMock(side_effect=lambda model: added_models.append(model))

        with (
            patch(
                "src.celery_app.tasks.workline._add_timeline",
                new=AsyncMock(side_effect=lambda _db, t, **_kwargs: captured_timelines.append(t)),
            ),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={"ROBOT": [target_device]},
                source_device=None,
                orch_result=orch_result,
            )

        outboxes = [model for model in added_models if getattr(model, "dispatch_type", None) is not None]
        assert outboxes == []
        mock_command_repo.create.assert_not_called()
        assert session.status == "FAILED"
        assert session.current_wait_type is None
        assert session.current_wait_token is None
        assert session.awaiting_command_id is None
        assert session.failure_domain == "CONFIG"
        assert session.failure_code == "UNSUPPORTED_COMMAND_TYPE"
        assert session.failure_message == "设备 ROBOT-001 不支持 command_type=PICK_AND_PUT，拒绝命令创建"
        assert [item.action_type for item in captured_timelines] == [
            TimelineActionType.DECISION_MADE,
            TimelineActionType.SESSION_FAILED,
        ]
        assert captured_timelines[-1].payload == {
            "request_id": "req-unsupported-001",
            "trace_id": "trace-unsupported-001",
            "event_id": None,
            "causation_id": None,
            "canonical_event_type": "SCAN_COMPLETED",
            "message": "设备 ROBOT-001 不支持 command_type=PICK_AND_PUT，拒绝命令创建",
        }

    @pytest.mark.asyncio
    async def test_apply_orchestrator_effects_rejects_device_in_maintenance_mode(self, mock_db):
        """maintenance_mode 必须在命令创建前被运行时消费。"""
        from src.app.workline.models.timeline import TimelineActionType
        from src.celery_app.tasks.workline import _apply_orchestrator_effects

        captured_timelines: list[Any] = []

        session = SimpleNamespace(
            id=457,
            workline_id=1,
            status="RUNNING",
            context_json={},
            trace_id=None,
            plugin_key=None,
            contract_version=None,
            step_code=None,
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
        workline = SimpleNamespace(plugin_key="demo_plugin")
        inbox = SimpleNamespace(
            id=202,
            trace_id="trace-maint-001",
            source_message_id="req-maint-001",
            payload_json={"canonical_event_type": "SCAN_COMPLETED"},
        )
        target_device = SimpleNamespace(
            id=100,
            device_code="ROBOT-002",
            capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
            maintenance_mode=True,
        )
        orch_result = OrchestratorResult(
            success=True,
            transition="dispatch_robot",
            commands=[
                CommandIntent(
                    target_device_id=100,
                    action="PICK_AND_PUT",
                    parameters={"command_code": "VENDOR-CMD-002"},
                )
            ],
            wait=WaitIntent(
                wait_type="COMMAND_RESULT",
                wait_token="VENDOR-CMD-002",
                deadline_seconds=180,
            ),
        )
        mock_command_repo = MagicMock()
        mock_command_repo.create = AsyncMock()

        with (
            patch(
                "src.celery_app.tasks.workline._add_timeline",
                new=AsyncMock(side_effect=lambda _db, t, **_kwargs: captured_timelines.append(t)),
            ),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={"ROBOT": [target_device]},
                source_device=None,
                orch_result=orch_result,
            )

        mock_command_repo.create.assert_not_called()
        assert session.status == "FAILED"
        assert session.failure_domain == "MANUAL_INTERVENTION"
        assert session.failure_code == "DEVICE_MAINTENANCE_MODE"
        assert (
            session.failure_message == "设备 ROBOT-002 处于 maintenance_mode，拒绝命令创建: command_type=PICK_AND_PUT"
        )
        assert [item.action_type for item in captured_timelines] == [
            TimelineActionType.DECISION_MADE,
            TimelineActionType.SESSION_FAILED,
        ]
        assert captured_timelines[-1].payload == {
            "request_id": "req-maint-001",
            "trace_id": "trace-maint-001",
            "event_id": None,
            "causation_id": None,
            "canonical_event_type": "SCAN_COMPLETED",
            "message": "设备 ROBOT-002 处于 maintenance_mode，拒绝命令创建: command_type=PICK_AND_PUT",
        }

    @pytest.mark.asyncio
    async def test_apply_orchestrator_effects_failure_syncs_trace_to_session_and_timeline(self, mock_db):
        """失败路径也必须保留入口 trace 主链，便于 replay/debug。"""
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineStage,
            TimelineStatus,
        )
        from src.celery_app.tasks.workline import _apply_orchestrator_effects
        from src.workline_runtime.types import FailureIntent

        captured_timelines: list[Any] = []

        session = SimpleNamespace(
            id=456,
            workline_id=1,
            status="RUNNING",
            context_json={"stage": "WAITING_PICK_PLACE"},
            trace_id=None,
            plugin_key=None,
            contract_version=None,
            step_code=None,
            last_inbox_id=None,
            current_wait_type="COMMAND_RESULT",
            current_wait_token="wait-token-001",
            waiting_since=None,
            deadline_at=None,
            awaiting_command_id=88,
            ended_at=None,
            failure_domain=None,
            failure_code=None,
            failure_message=None,
        )
        workline = SimpleNamespace(plugin_key="demo_plugin")
        inbox = SimpleNamespace(
            id=202,
            trace_id="trace-failure-001",
            source_message_id="req-failure-001",
            payload_json={"canonical_event_type": "MOVE_FORWARD"},
        )
        orch_result = OrchestratorResult(
            success=True,
            failure=FailureIntent(
                domain="HARDWARE",
                code="DEVICE_TIMEOUT",
                message="device timed out",
            ),
        )
        mock_outbox_repo = MagicMock()
        mock_outbox_repo.cancel_active_by_session = AsyncMock(return_value=1)

        with (
            patch(
                "src.celery_app.tasks.workline._add_timeline",
                new=AsyncMock(side_effect=lambda _db, t, **_kwargs: captured_timelines.append(t)),
            ),
            patch(
                "src.workline_runtime.timeline_generator.timeline_generator.generate",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            patch(
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
        ):
            await _apply_orchestrator_effects(
                mock_db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role={},
                source_device=None,
                orch_result=orch_result,
            )

        assert session.trace_id == "trace-failure-001"
        assert session.last_inbox_id == 202
        assert session.status == "FAILED"
        assert session.current_wait_type is None
        assert session.current_wait_token is None
        assert session.awaiting_command_id is None
        assert session.failure_domain == "HARDWARE"
        assert session.failure_code == "DEVICE_TIMEOUT"
        assert session.failure_message == "device timed out"
        mock_outbox_repo.cancel_active_by_session.assert_awaited_once_with(
            mock_db,
            session_id=456,
            reason="DEVICE_TIMEOUT",
        )
        assert len(captured_timelines) == 1
        assert captured_timelines[0].action_type == TimelineActionType.SESSION_FAILED
        assert captured_timelines[0].stage == TimelineStage.FAIL
        assert captured_timelines[0].status == TimelineStatus.FAILED
        assert captured_timelines[0].related_inbox_id == 202
        assert captured_timelines[0].payload == {
            "request_id": "req-failure-001",
            "trace_id": "trace-failure-001",
            "event_id": None,
            "causation_id": None,
            "canonical_event_type": "MOVE_FORWARD",
            "message": "device timed out",
        }

    @pytest.mark.asyncio
    async def test_apply_orchestrator_effects_business_decision_keeps_session_running(self, mock_db):
        """业务 NG 只记录业务判定 timeline，不写失败归因。"""
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.celery_app.tasks.workline import _apply_orchestrator_effects
        from src.workline_runtime.types import BusinessDecisionIntent

        captured_timelines: list[Any] = []

        session = SimpleNamespace(
            id=457,
            workline_id=1,
            status="WAITING",
            context_json={"stage": "WAITING_PICK_PLACE"},
            trace_id=None,
            plugin_key=None,
            contract_version=None,
            step_code=None,
            last_inbox_id=None,
            current_wait_type="COMMAND_RESULT",
            current_wait_token="wait-token-002",
            waiting_since=None,
            deadline_at=None,
            awaiting_command_id=89,
            ended_at=None,
            failure_domain="HARDWARE",
            failure_code="OLD_FAILURE",
            failure_message="old failure",
        )
        workline = SimpleNamespace(plugin_key="demo_plugin")
        inbox = SimpleNamespace(
            id=203,
            trace_id="trace-business-001",
            source_message_id="req-business-001",
            payload_json={"canonical_event_type": "SCAN_COMPLETED"},
        )
        orch_result = OrchestratorResult(
            success=True,
            business_decisions=[
                BusinessDecisionIntent(
                    reason_code="SCAN_NG",
                    message="扫码判定 NG",
                    business_key="PKG-001",
                    evidence={"barcode": "PKG-001"},
                )
            ],
        )

        with (
            patch(
                "src.celery_app.tasks.workline._add_timeline",
                new=AsyncMock(side_effect=lambda _db, t, **_kwargs: captured_timelines.append(t)),
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
                source_device=None,
                orch_result=orch_result,
            )

        assert session.status == "RUNNING"
        assert session.current_wait_type is None
        assert session.current_wait_token is None
        assert session.awaiting_command_id is None
        assert session.failure_domain is None
        assert session.failure_code is None
        assert session.failure_message is None
        assert len(captured_timelines) == 1
        assert captured_timelines[0].stage == TimelineStage.DECISION
        assert captured_timelines[0].action_type == TimelineActionType.DECISION_MADE
        assert captured_timelines[0].actor_type == TimelineActorType.PLUGIN
        assert captured_timelines[0].actor_code == "demo_plugin"
        assert captured_timelines[0].status == TimelineStatus.SUCCESS
        assert captured_timelines[0].related_inbox_id == 203
        assert captured_timelines[0].payload == {
            "request_id": "req-business-001",
            "trace_id": "trace-business-001",
            "event_id": None,
            "causation_id": None,
            "canonical_event_type": "SCAN_COMPLETED",
            "classification": "business_decision",
            "reason_code": "SCAN_NG",
            "message": "扫码判定 NG",
            "evidence": {"barcode": "PKG-001"},
            "business_key": "PKG-001",
        }

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
    async def test_load_related_entities_checks_safety_before_session_resolver(self, mock_db):
        """非 ESTOP 事件在冻结 WorkLine 上不能先创建 Session。"""
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked
        from src.celery_app.tasks.workline import _load_related_entities

        inbox = MockInbox(
            inbox_id=1,
            workline_id=7,
            payload_json={"canonical_event_type": "SCAN_COMPLETED", "data": {"PkgID": "PKG-001"}},
        )
        mock_workline = MockWorkline(workline_id=7)
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(
            side_effect=WorkLineSafetyBlocked("WORKLINE_ESTOPPED: workline_id=7")
        )

        with (
            patch("src.app.workline.repositories.WorkLineRepository") as MockWorklineRepo,
            patch("src.app.device.repositories.DeviceRepository") as MockDeviceRepo,
            patch("src.app.workline.repositories.session_repository.WorklineSessionRepository") as MockSessionRepo,
            patch(
                "src.workline_runtime.session_resolver.session_resolver.resolve_or_create",
                new=AsyncMock(return_value=MockSession()),
            ) as mock_resolve_or_create,
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
        ):
            mock_session_repo_instance = AsyncMock()
            mock_session_repo_instance.get_by_id = AsyncMock(return_value=None)
            MockSessionRepo.return_value = mock_session_repo_instance

            mock_workline_repo_instance = AsyncMock()
            mock_workline_repo_instance.get_by_id = AsyncMock(return_value=mock_workline)
            MockWorklineRepo.return_value = mock_workline_repo_instance

            mock_device_repo_instance = AsyncMock()
            mock_device_repo_instance.get_by_id = AsyncMock(return_value=None)
            mock_device_repo_instance.get_by_work_line_id = AsyncMock(return_value=[])
            MockDeviceRepo.return_value = mock_device_repo_instance

            with pytest.raises(WorkLineSafetyBlocked, match="WORKLINE_ESTOPPED"):
                await _load_related_entities(mock_db, inbox, resolved_event_type="SCAN_COMPLETED")

        safety_service.assert_accepting_work.assert_awaited_once_with(mock_db, workline_id=7)
        mock_resolve_or_create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_load_external_http_entities_from_trace_id(self, mock_db):
        """测试 EXTERNAL_HTTP 可先按 trace_id 恢复 session，再回填 workline 和设备。"""
        from src.celery_app.tasks.workline import _load_related_entities

        inbox = MockInbox(
            inbox_id=1,
            kind=InboxKind.EXTERNAL_HTTP,
            trace_id="trace-external-001",
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
