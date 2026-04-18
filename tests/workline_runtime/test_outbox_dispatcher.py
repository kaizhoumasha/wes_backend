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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.models.outbox import DispatchType, OutboxStatus, TargetType


def _mock_device_record(**overrides: object) -> SimpleNamespace:
    return SimpleNamespace(host="127.0.0.1", port=8006, timeout=10000, protocol="HTTP", **overrides)


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
        dispatch_key: str | None = None,
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
        self.dispatch_key = dispatch_key or f"outbox:{outbox_id}"
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
    def mock_device_repo(self):
        repo = MagicMock()
        repo.get_by_device_code = AsyncMock(return_value=_mock_device_record())
        return repo

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
        mock_device_repo,
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
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "httpx.AsyncClient",
            ) as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 1
        assert result["failed"] == 0
        mock_device_repo.get_by_device_code.assert_awaited_once_with(mock_db, "SCANNER_001")
        mock_client.return_value.__aenter__.return_value.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_multiple_messages(
        self,
        mock_db,
        mock_outbox_repo,
        mock_device_repo,
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
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "httpx.AsyncClient",
            ) as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 3
        assert result["success"] == 3
        assert result["failed"] == 0
        assert mock_device_repo.get_by_device_code.await_count == 3
        assert mock_client.return_value.__aenter__.return_value.post.await_count == 3

    @pytest.mark.asyncio
    async def test_dispatch_handles_failure_with_retry(
        self,
        mock_db,
        mock_outbox_repo,
        mock_device_repo,
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
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "httpx.AsyncClient",
            ) as mock_client,
        ):
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=Exception("Device offline"))
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
        mock_device_repo,
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
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "httpx.AsyncClient",
            ) as mock_client,
        ):
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=Exception("Device offline"))
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["failed"] == 1
        # 达到最大重试次数，状态应为 FAILED
        assert outbox.status == OutboxStatus.FAILED

    @pytest.mark.asyncio
    async def test_dispatch_logs_diagnostic_with_outbox_trace_fields(self, mock_db, mock_outbox_repo):
        """测试 Outbox 派发失败时会输出稳定 trace 字段，便于 replay/debug。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=7,
            dispatch_type=DispatchType.EXTERNAL_HTTP,
            target_type=TargetType.HTTP_ENDPOINT,
            target_code="https://api.example.com/callback",
            dispatch_key="external-http:TRACE-007",
            payload_json={"event": "completed"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        with (
            patch(
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.celery_app.tasks.workline.OutboxDispatcher._dispatch_single", new=AsyncMock(return_value=False)),
            patch("src.celery_app.tasks.workline._log_diagnostic") as mock_log_diagnostic,
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["failed"] == 1
        mock_log_diagnostic.assert_called_once()
        kwargs = mock_log_diagnostic.call_args.kwargs
        assert kwargs["outbox"] is outbox
        assert kwargs["extra"] == {
            "outbox_id": 7,
            "dispatch_key": "external-http:TRACE-007",
            "dispatch_type": DispatchType.EXTERNAL_HTTP.value,
            "target_code": "https://api.example.com/callback",
        }

    @pytest.mark.asyncio
    async def test_dispatch_logs_warning_with_outbox_trace_suffix(self, mock_db, mock_outbox_repo):
        """测试 Outbox 派发失败 warning 日志也带稳定 trace 字段。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=8,
            dispatch_type=DispatchType.EXTERNAL_HTTP,
            target_type=TargetType.HTTP_ENDPOINT,
            target_code="https://api.example.com/notify",
            dispatch_key="external-http:TRACE-008",
            payload_json={"event": "failed"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        with (
            patch(
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.celery_app.tasks.workline.OutboxDispatcher._dispatch_single", new=AsyncMock(return_value=False)),
            patch("src.celery_app.tasks.workline._log_diagnostic"),
            patch("src.celery_app.tasks.workline.logger.warning") as mock_warning,
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["failed"] == 1
        mock_warning.assert_called_once_with(
            "Outbox 8 派发失败 (dispatch_type=EXTERNAL_HTTP, "
            "dispatch_key=external-http:TRACE-008, "
            "target_code=https://api.example.com/notify)"
        )

    def test_outbox_model_created_at_uses_db_clock(self):
        """测试 Outbox 创建时间使用统一的 UTC-naive DB 时间工具。"""
        from src.app.workline.models.outbox import DispatchType, TargetType, WorklineOutbox

        fixed_now = datetime(2026, 4, 17, 1, 57, 12)

        with patch("src.app.workline.models.outbox.timezone.now_for_db", return_value=fixed_now):
            outbox = WorklineOutbox(
                session_id=417,
                workline_id=45,
                dispatch_type=DispatchType.DEVICE_COMMAND,
                dispatch_key="device-command:CMD-TEST-001",
                target_type=TargetType.DEVICE,
                target_code="ARM03",
                payload_json={"command_code": "CMD-TEST-001"},
            )

        assert outbox.created_at == fixed_now

    def test_build_outbox_payload_passes_vendor_payload_as_is(self):
        """测试发往设备的 payload 直接使用命令里保存的 vendor payload。"""
        from src.celery_app.tasks.workline import _build_outbox_payload

        command = MagicMock()
        command.command_code = "CMD-001"
        command.task_type = "PICK_AND_PLACE"
        command.priority = 5
        command.timeout_ms = 300000
        command.params = {
            "command_code": "CMD-VENDOR-001",
            "task_type": "PICK_AND_PUT",
            "priority": 1,
            "timeout": 30000,
            "source": {"location_type": "INPUT_PLATFORM", "location_id": "STATION_INPUT1"},
            "target": {"location_type": "NG_PLATFORM", "location_id": "STATION_NG_PLATFORM1"},
            "params": {
                "source": {"location_type": "INPUT_PLATFORM", "location_id": "STATION_INPUT1"},
                "target": {"location_type": "NG_PLATFORM", "location_id": "STATION_NG_PLATFORM1"},
            },
            "timestamp": 1743235200000,
        }

        payload = _build_outbox_payload(command)

        assert payload == command.params

    def test_build_outbox_payload_injects_device_code_into_vendor_payload(self):
        """测试发往设备的 payload 会补齐 target device_code。"""
        from src.celery_app.tasks.workline import _build_outbox_payload

        command = MagicMock()
        command.command_code = "CMD-VENDOR-DEVICE-001"
        command.task_type = "PICK_AND_PLACE"
        command.priority = 5
        command.timeout_ms = 300000
        command.params = {
            "command_code": "CMD-VENDOR-DEVICE-001",
            "task_type": "PICK_AND_PUT",
            "priority": 1,
            "timeout": 30000,
            "params": {},
            "timestamp": 1743235200000,
        }

        payload = _build_outbox_payload(command, device_code="ARM03")

        assert payload["device_code"] == "ARM03"
        assert payload["command_code"] == "CMD-VENDOR-DEVICE-001"

    def test_build_outbox_payload_legacy_command_fallback(self):
        """测试历史命令 params 为空时回退到基础字段组装。"""
        from src.celery_app.tasks.workline import _build_outbox_payload

        command = MagicMock()
        command.command_code = "CMD-LEGACY-001"
        command.task_type = "PROCESS"
        command.priority = 3
        command.timeout_ms = 120000
        command.params = {}

        payload = _build_outbox_payload(command, device_code="PIPELINE02")

        assert payload["device_code"] == "PIPELINE02"
        assert payload["command_code"] == "CMD-LEGACY-001"
        assert payload["task_type"] == "PROCESS"
        assert payload["priority"] == 3
        assert payload["timeout"] == 120000
        assert payload["params"] == {}
        assert isinstance(payload["timestamp"], int)

    def test_normalize_vendor_command_payload_does_not_accept_legacy_command_id(self):
        """测试设备派发归一化不再接受 legacy command_id。"""
        from src.celery_app.tasks.workline import _normalize_vendor_command_payload

        payload = _normalize_vendor_command_payload(
            {"command_id": "CMD-LEGACY-001", "task_type": "PICK_AND_PUT"},
            action="PICK_AND_PUT",
            default_command_code="CMD-NEW-001",
        )

        assert payload["command_code"] == "CMD-NEW-001"

    def test_sync_session_contract_snapshot_prefers_workline_contract_version(self):
        """测试 session snapshot 优先使用 workline.contract_version。"""
        from src.celery_app.tasks.workline import _sync_session_contract_snapshot

        session = SimpleNamespace(plugin_key="smt_classifier", contract_version="legacy-0.9", step_code=None)
        workline = SimpleNamespace(plugin_key="smt_classifier", contract_version="wl-2.0")

        with patch(
            "src.celery_app.tasks.workline.get_plugin_contract_version",
            return_value="registry-1.0",
        ):
            _sync_session_contract_snapshot(
                session,
                workline=workline,
                context={"step_code": "WAITING_PICK_PLACE"},
            )

        assert session.contract_version == "wl-2.0"
        assert session.step_code == "WAITING_PICK_PLACE"

    def test_sync_session_contract_snapshot_falls_back_to_registry(self):
        """测试 workline.contract_version 缺失时回退 registry。"""
        from src.celery_app.tasks.workline import _sync_session_contract_snapshot

        session = SimpleNamespace(plugin_key="smt_classifier", contract_version=None, step_code=None)
        workline = SimpleNamespace(plugin_key="smt_classifier", contract_version=None)

        with patch(
            "src.celery_app.tasks.workline.get_plugin_contract_version",
            return_value="1.0",
        ):
            _sync_session_contract_snapshot(
                session,
                workline=workline,
                context={"step_code": "WAITING_PICK_PLACE"},
            )

        assert session.contract_version == "1.0"
        assert session.step_code == "WAITING_PICK_PLACE"

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

        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code = AsyncMock(
            return_value=SimpleNamespace(host="127.0.0.1", port=8006, timeout=10000, protocol="HTTP")
        )

        with (
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await dispatch_outbox._dispatch_single(mock_db, outbox)

        assert result is True
        mock_device_repo.get_by_device_code.assert_awaited_once_with(mock_db, "ROBOT_001")

    @pytest.mark.asyncio
    async def test_dispatch_device_command_logs_response_body_on_http_error(self, mock_db):
        """测试设备派发失败时会记录响应体，便于排查 4xx/5xx。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ROBOT_001",
            payload_json={"command_code": "CMD-422-001"},
        )

        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code = AsyncMock(
            return_value=SimpleNamespace(host="127.0.0.1", port=8006, timeout=10000, protocol="HTTP")
        )

        with (
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch("httpx.AsyncClient") as mock_client,
            patch("src.celery_app.tasks.workline.logger.warning") as mock_warning,
        ):
            mock_response = MagicMock(
                status_code=422, text='{"detail":[{"loc":["body","device_code"],"msg":"Field required"}]}'
            )
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = await dispatch_outbox._dispatch_single(mock_db, outbox)

        assert result is False
        mock_warning.assert_called_with(
            '设备指令发送失败: HTTP 422, body={"detail":[{"loc":["body","device_code"],"msg":"Field required"}]}'
        )

    @pytest.mark.asyncio
    async def test_dispatch_device_command_uses_device_callback_path(self, mock_db):
        """callback_path 配置后，派发 URL 必须优先走设备自定义路径。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ROBOT_001",
            payload_json={"command_code": "CMD-CB-001", "task_type": "PICK_AND_PUT"},
        )

        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code = AsyncMock(
            return_value=SimpleNamespace(
                host="127.0.0.1",
                port=8006,
                timeout=10000,
                protocol="HTTP",
                callback_path="/api/v1/vendor-command",
                capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
                maintenance_mode=False,
                device_code="ROBOT_001",
            )
        )

        with (
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = await dispatch_outbox._dispatch_single(mock_db, outbox)

        assert result is True
        mock_client.return_value.__aenter__.return_value.post.assert_awaited_once_with(
            "http://127.0.0.1:8006/api/v1/vendor-command",
            json={"command_code": "CMD-CB-001", "task_type": "PICK_AND_PUT"},
        )

    @pytest.mark.asyncio
    async def test_dispatch_device_command_falls_back_to_default_path(self, mock_db):
        """未配置 callback_path 时，仍回退默认设备命令路径。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ROBOT_001",
            payload_json={"command_code": "CMD-CB-002", "task_type": "PICK_AND_PUT"},
        )

        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code = AsyncMock(
            return_value=SimpleNamespace(
                host="127.0.0.1",
                port=8006,
                timeout=10000,
                protocol="HTTP",
                callback_path=None,
                capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
                maintenance_mode=False,
                device_code="ROBOT_001",
            )
        )

        with (
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = await dispatch_outbox._dispatch_single(mock_db, outbox)

        assert result is True
        mock_client.return_value.__aenter__.return_value.post.assert_awaited_once_with(
            "http://127.0.0.1:8006/api/v1/device/command",
            json={"command_code": "CMD-CB-002", "task_type": "PICK_AND_PUT"},
        )

    @pytest.mark.asyncio
    async def test_dispatch_device_command_rejects_maintenance_mode(self, mock_db):
        """maintenance_mode 打开时，运行时必须拒绝设备派发。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=11,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ROBOT_001",
            payload_json={"command_code": "CMD-MAINT-001", "task_type": "PICK_AND_PUT"},
        )
        mock_outbox_repo = MagicMock()
        mock_outbox_repo.get_pending_messages = AsyncMock(return_value=[outbox])
        mock_outbox_repo.mark_as_dispatching = AsyncMock(return_value=outbox)
        mock_outbox_repo.mark_as_sent = AsyncMock(return_value=outbox)
        mock_outbox_repo.mark_as_failed = AsyncMock(return_value=outbox)

        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code = AsyncMock(
            return_value=SimpleNamespace(
                host="127.0.0.1",
                port=8006,
                timeout=10000,
                protocol="HTTP",
                callback_path=None,
                capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
                maintenance_mode=True,
                device_code="ROBOT_001",
            )
        )

        with (
            patch(
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        mark_failed_args = mock_outbox_repo.mark_as_failed.await_args.args
        assert mark_failed_args[1] == 11
        assert mark_failed_args[2] == "设备 ROBOT_001 处于 maintenance_mode，拒绝命令派发: command_type=PICK_AND_PUT"

    @pytest.mark.asyncio
    async def test_dispatch_device_command_rejects_unsupported_command_type(self, mock_db):
        """supports_command_types 必须在设备派发前被运行时消费。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=12,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ROBOT_001",
            payload_json={"command_code": "CMD-UNSUPPORTED-001", "task_type": "PICK_AND_PUT"},
        )
        mock_outbox_repo = MagicMock()
        mock_outbox_repo.get_pending_messages = AsyncMock(return_value=[outbox])
        mock_outbox_repo.mark_as_dispatching = AsyncMock(return_value=outbox)
        mock_outbox_repo.mark_as_sent = AsyncMock(return_value=outbox)
        mock_outbox_repo.mark_as_failed = AsyncMock(return_value=outbox)

        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code = AsyncMock(
            return_value=SimpleNamespace(
                host="127.0.0.1",
                port=8006,
                timeout=10000,
                protocol="HTTP",
                callback_path=None,
                capabilities_json={"supports_command_types": ["MOVE_FORWARD"]},
                maintenance_mode=False,
                device_code="ROBOT_001",
            )
        )

        with (
            patch(
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        mark_failed_args = mock_outbox_repo.mark_as_failed.await_args.args
        assert mark_failed_args[1] == 12
        assert mark_failed_args[2] == "设备 ROBOT_001 不支持 command_type=PICK_AND_PUT，拒绝命令派发"

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
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

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
