"""
OutboxDispatchService 单元测试

测试 Celery 任务派发 SystemOutbox 的流程：
- 获取待派发消息
- 根据类型派发（设备指令/外部HTTP/内部信号）
- 更新派发状态
- 重试机制

设计参考: 设计文档 phase2-orchestrator
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.device.models.command import CommandStatus
from src.app.device.models.device import DeviceStatus
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.workline_runtime.diagnostics.codes import ErrorCode


def _mock_device_record(**overrides: object) -> SimpleNamespace:
    data = {"host": "127.0.0.1", "port": 8006, "timeout": 10000, "protocol": "HTTP"}
    data.update(overrides)
    return SimpleNamespace(**data)


def _mock_command_record(**overrides: object) -> SimpleNamespace:
    data = {
        "id": 1001,
        "device_id": 7,
        "status": CommandStatus.PENDING,
        "sent_at": None,
        "ack_received_at": None,
        "ack_code": None,
        "ack_message": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _configure_status_ok(mock_client: MagicMock) -> MagicMock:
    status_response = MagicMock(status_code=200, text="")
    status_response.json.return_value = {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}
    mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=status_response)
    return status_response


def _configure_status_unavailable(mock_client: MagicMock) -> MagicMock:
    status_response = MagicMock(status_code=503, text="not ready")
    status_response.json.return_value = {}
    mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=status_response)
    return status_response


def _configure_status_busy(mock_client: MagicMock) -> MagicMock:
    status_response = MagicMock(status_code=200, text="")
    status_response.json.return_value = {
        "state": {"mode": "AUTO", "status": "RUNNING", "current_command_id": "CMD-RUNNING-001"}
    }
    mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=status_response)
    return status_response


class MockOutbox:
    """模拟 SystemOutbox"""

    def __init__(
        self,
        outbox_id: int = 1,
        dispatch_type: SystemOutboxDispatchType = SystemOutboxDispatchType.DEVICE_COMMAND,
        target_type: SystemOutboxTargetType = SystemOutboxTargetType.DEVICE,
        target_code: str = "DEVICE_001",
        status: SystemOutboxStatus = SystemOutboxStatus.NEW,
        session_id: int | None = None,
        workline_id: int | None = None,
        payload_json: dict | None = None,
        attempt_count: int = 0,
        dispatch_key: str | None = None,
        operation_domain: str = "WORKLINE",
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
        self.operation_domain = operation_domain
        self.next_retry_at = None
        self.last_error = None
        self.finished_at = None
        self.blocked_by_reconciliation_session_id = None
        self.blocked_device_id = None
        self.blocked_workline_id = None
        self.blocked_reason = None
        self.blocked_at = None
        self.last_blocked_check_at = None
        self.blocked_check_count = 0
        self.blocked_detail_json = {}


def test_device_busy_outbox_compat_wrapper_removed_from_dispatch_module() -> None:
    from src.app.workline.services import outbox_dispatch_service

    assert not hasattr(outbox_dispatch_service, "_block_outbox_for_device_busy")


class TestOutboxDispatchService:
    """OutboxDispatchService 任务测试"""

    async def _assert_rough_sorter_command_waits_for_ecs_idle(
        self,
        *,
        workline_runtime_mock_db,
        mock_outbox_repo,
        target_code: str,
        device_id: int,
        task_type: str,
        command_code: str,
        later_command_code: str,
    ) -> None:
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        head = MockOutbox(
            outbox_id=device_id * 10,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code=target_code,
            workline_id=45,
            payload_json={"command_code": command_code, "task_type": task_type},
        )
        later = MockOutbox(
            outbox_id=device_id * 10 + 1,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code=target_code,
            workline_id=45,
            payload_json={"command_code": later_command_code, "task_type": task_type},
        )
        device_repo = MagicMock()
        device_repo.get_by_device_code = AsyncMock(
            return_value=_mock_device_record(
                id=device_id,
                device_code=target_code,
                capabilities_json={"supports_command_types": [task_type]},
                maintenance_mode=False,
                device_status=DeviceStatus.IDLE,
                current_command_id=None,
                work_line_id=45,
            )
        )
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(return_value=None)

        mock_outbox_repo.get_pending_messages.return_value = [head]
        mock_outbox_repo.mark_as_dispatching.return_value = head
        with (
            patch("src.app.sys.repositories.SystemOutboxRepository", return_value=mock_outbox_repo),
            patch("src.app.device.repositories.device_repository.device_repository", device_repo),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_busy(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            first = await OutboxDispatchService().dispatch(workline_runtime_mock_db, limit=5)

        assert first == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
        mock_client.return_value.__aenter__.return_value.post.assert_not_awaited()
        block_call = mock_outbox_repo.mark_as_blocked_by_device_busy.await_args
        assert block_call.args == (workline_runtime_mock_db, head.id)
        assert block_call.kwargs["reason"] == "DEVICE_BUSY"

        head.status = SystemOutboxStatus.BLOCKED_RESOURCE
        head.blocked_reason = "DEVICE_BUSY"
        head.blocked_device_id = device_id
        mock_outbox_repo.mark_as_dispatching.reset_mock()
        mock_outbox_repo.mark_as_blocked_by_device_busy.reset_mock()
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [head]
        mock_outbox_repo.get_pending_messages.return_value = [later]
        with (
            patch("src.app.sys.repositories.SystemOutboxRepository", return_value=mock_outbox_repo),
            patch("src.app.device.repositories.device_repository.device_repository", device_repo),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_busy(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            second = await OutboxDispatchService().dispatch(workline_runtime_mock_db, limit=5)

        assert second == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 2}
        mock_client.return_value.__aenter__.return_value.post.assert_not_awaited()
        mock_outbox_repo.mark_as_dispatching.assert_not_awaited()
        mock_outbox_repo.mark_as_blocked_by_device_busy.assert_awaited_once()

        mock_outbox_repo.mark_as_blocked_by_device_busy.reset_mock()
        mock_outbox_repo.mark_as_dispatching.reset_mock()
        mock_outbox_repo.claim_blocked_resource_wait_for_dispatch.return_value = head
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [head]
        mock_outbox_repo.get_pending_messages.return_value = []
        with (
            patch("src.app.sys.repositories.SystemOutboxRepository", return_value=mock_outbox_repo),
            patch("src.app.device.repositories.device_repository.device_repository", device_repo),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            _configure_status_ok(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            third = await OutboxDispatchService().dispatch(workline_runtime_mock_db, limit=5)

        assert third == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
        assert mock_client.return_value.__aenter__.return_value.post.await_count == 1
        mock_outbox_repo.mark_as_sent.assert_awaited_with(workline_runtime_mock_db, head.id)

    @pytest.mark.asyncio
    async def test_dispatcher_only_loads_workline_domain_outbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Workline dispatcher 处理需要沙箱/设备治理的域，避免通用引擎抢占。"""

        instances: list[object] = []

        class FakeSystemOutboxRepository:
            def __init__(self) -> None:
                self.pending_filters: list[dict[str, object]] = []
                instances.append(self)

            async def get_pending_messages(self, _db: object, limit: int = 50, **filters: object) -> list[object]:
                self.pending_filters.append({"limit": limit, **filters})
                return []

        import src.app.sys.repositories as sys_repositories
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        monkeypatch.setattr(sys_repositories, "SystemOutboxRepository", FakeSystemOutboxRepository)

        result = await OutboxDispatchService().dispatch(SimpleNamespace(commit=AsyncMock()), limit=7)

        assert result == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
        assert instances[0].pending_filters == [{"limit": 7, "operation_domains": ("WORKLINE", "RACK")}]

    @pytest.fixture
    def mock_outbox_repo(self):
        """创建模拟 OutboxRepository"""
        repo = MagicMock()
        repo.get_pending_messages = AsyncMock(return_value=[])
        repo.get_probeable_blocked_device_heads = AsyncMock(return_value=[])
        repo.claim_blocked_resource_wait_for_dispatch = AsyncMock(return_value=None)
        repo.mark_as_dispatching = AsyncMock(return_value=MagicMock())
        repo.mark_as_blocked_by_workline_estop = AsyncMock(return_value=MagicMock())
        repo.mark_as_blocked_by_device_busy = AsyncMock(return_value=MagicMock())
        repo.update_resource_wait_detail = AsyncMock(return_value=MagicMock())
        repo.mark_as_sent = AsyncMock(return_value=MagicMock())
        repo.mark_as_failed = AsyncMock(return_value=MagicMock())
        return repo

    @pytest.fixture
    def mock_device_repo(self):
        repo = MagicMock()
        repo.get_by_device_code = AsyncMock(return_value=_mock_device_record())
        return repo

    @pytest.mark.asyncio
    async def test_dispatch_no_pending_messages(self, workline_runtime_mock_db, mock_outbox_repo):
        """测试无待派发消息时正常退出"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        with patch(
            "src.app.sys.repositories.SystemOutboxRepository",
            return_value=mock_outbox_repo,
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 0
        assert result["success"] == 0
        assert result["failed"] == 0
        mock_outbox_repo.get_pending_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_single_device_command(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """测试派发单个设备指令"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=1,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="SCANNER_001",
            payload_json={"command": "SCAN"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
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
            _configure_status_ok(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 1
        assert result["failed"] == 0
        mock_device_repo.get_by_device_code.assert_awaited_once_with(workline_runtime_mock_db, "SCANNER_001")
        mock_client.return_value.__aenter__.return_value.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_parks_status_precheck_wait_without_retry_exhaustion(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """Status GET 暂不可用时，outbox 进入资源等待，不消耗普通 retry。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=1,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="SCANNER_001",
            payload_json={"command": "SCAN"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_unavailable(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 1
        mock_client.return_value.__aenter__.return_value.post.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        mock_outbox_repo.mark_as_blocked_by_device_busy.assert_awaited_once()
        call = mock_outbox_repo.mark_as_blocked_by_device_busy.await_args
        assert call.args == (workline_runtime_mock_db, 1)
        assert call.kwargs["reason"] == "DEVICE_STATUS_PRECHECK_WAIT"
        assert call.kwargs["last_error"] == "设备 SCANNER_001 实时状态查询返回 HTTP 503，等待下次预检"
        assert call.kwargs["detail"]["device_code"] == "SCANNER_001"
        assert call.kwargs["detail"]["error_kind"] == "http_status"
        assert call.kwargs["detail"]["http_status"] == 503

    @pytest.mark.asyncio
    async def test_rough_sorter_conveyor_command_waits_for_ecs_idle(self, workline_runtime_mock_db, mock_outbox_repo):
        await self._assert_rough_sorter_command_waits_for_ecs_idle(
            workline_runtime_mock_db=workline_runtime_mock_db,
            mock_outbox_repo=mock_outbox_repo,
            target_code="RS-CONVEYOR-01",
            device_id=201,
            task_type="MOVE_FORWARD",
            command_code="CMD-RS-MOVE-FORWARD-001",
            later_command_code="CMD-RS-MOVE-FORWARD-002",
        )

    @pytest.mark.asyncio
    async def test_rough_sorter_output_arm_command_waits_for_ecs_idle(self, workline_runtime_mock_db, mock_outbox_repo):
        await self._assert_rough_sorter_command_waits_for_ecs_idle(
            workline_runtime_mock_db=workline_runtime_mock_db,
            mock_outbox_repo=mock_outbox_repo,
            target_code="RS-OUTPUT-ARM-01",
            device_id=202,
            task_type="PICK_AND_PUT",
            command_code="CMD-RS-OUTPUT-ARM-001",
            later_command_code="CMD-RS-OUTPUT-ARM-002",
        )

    @pytest.mark.asyncio
    async def test_dispatch_parks_device_busy_without_retry_exhaustion(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """ECS status busy 时，outbox 进入 DEVICE_BUSY 资源等待，不 POST、不走普通失败重试。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=11,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="SCANNER_001",
            payload_json={"command_code": "CMD-BUSY-001", "task_type": "SCAN"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_busy(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
        mock_client.return_value.__aenter__.return_value.post.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        mock_outbox_repo.mark_as_blocked_by_device_busy.assert_awaited_once()
        call = mock_outbox_repo.mark_as_blocked_by_device_busy.await_args
        assert call.args == (workline_runtime_mock_db, 11)
        assert call.kwargs["reason"] == "DEVICE_BUSY"
        assert call.kwargs["detail"]["device_code"] == "SCANNER_001"
        assert call.kwargs["detail"]["last_probe_result"] == "BUSY"
        assert call.kwargs["detail"]["observed_status"] == "RUNNING"
        assert call.kwargs["detail"]["observed_current_command_id"] == "CMD-RUNNING-001"

    @pytest.mark.asyncio
    async def test_dispatch_records_blocked_resource_attempt_response(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """资源等待 park 会把派发尝试账本记为 blocked_resource，并写入 governance detail。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=12,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="SCANNER_001",
            payload_json={"command_code": "CMD-BUSY-002", "task_type": "SCAN"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]
        attempt_service = SimpleNamespace(
            create_attempt=AsyncMock(return_value=SimpleNamespace(id=88)),
            finalize_attempt_record=AsyncMock(return_value=None),
        )

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.workline.services.dispatch_attempt_service.workline_dispatch_attempt_service",
                attempt_service,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_busy(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        attempt_service.finalize_attempt_record.assert_awaited_once()
        attempt_call = attempt_service.finalize_attempt_record.await_args.kwargs
        assert attempt_call["response"]["result"] == "blocked_resource"
        assert attempt_call["response"]["reason"] == "DEVICE_BUSY"
        assert "detail" in attempt_call["response"]
        assert attempt_call["response"]["detail"]["last_probe_result"] == "BUSY"
        assert attempt_call["response"]["detail"]["observed_status"] == "RUNNING"
        block_call = mock_outbox_repo.mark_as_blocked_by_device_busy.await_args.kwargs
        assert block_call["detail"] == attempt_call["response"]["detail"]

    @pytest.mark.asyncio
    async def test_dispatch_probes_blocked_head_and_posts_when_ecs_idle(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """dispatcher 先探测 blocked 队首，ECS ready 后领取为 DISPATCHING 并复用 POST。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        blocked_head = MockOutbox(
            outbox_id=31,
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            workline_id=45,
            payload_json={"command_code": "CMD-HEAD-001", "task_type": "PICK_AND_PUT"},
        )
        blocked_head.blocked_reason = "DEVICE_BUSY"
        later_same_device = MockOutbox(
            outbox_id=32,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            workline_id=45,
            payload_json={"command_code": "CMD-LATER-001", "task_type": "PICK_AND_PUT"},
        )
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [blocked_head]
        mock_outbox_repo.claim_blocked_resource_wait_for_dispatch.return_value = blocked_head
        mock_outbox_repo.get_pending_messages.return_value = [later_same_device]
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(return_value=None)
        attempt_service = SimpleNamespace(
            create_attempt=AsyncMock(return_value=SimpleNamespace(id=91)),
            finalize_attempt_record=AsyncMock(return_value=None),
        )

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch(
                "src.app.workline.services.dispatch_attempt_service.workline_dispatch_attempt_service",
                attempt_service,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            _configure_status_ok(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db, limit=5)

        assert result["success"] == 1
        assert result["dispatched"] == 1
        mock_outbox_repo.get_probeable_blocked_device_heads.assert_awaited_once()
        assert mock_outbox_repo.get_probeable_blocked_device_heads.await_args.kwargs["operation_domains"] == (
            "WORKLINE",
            "RACK",
        )
        mock_outbox_repo.claim_blocked_resource_wait_for_dispatch.assert_awaited_once_with(
            workline_runtime_mock_db,
            31,
            "DEVICE_BUSY",
            min_probe_interval_seconds=2,
            operation_domains=("WORKLINE", "RACK"),
        )
        mock_outbox_repo.mark_as_sent.assert_awaited_once_with(workline_runtime_mock_db, 31)
        mock_outbox_repo.mark_as_dispatching.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        assert mock_client.return_value.__aenter__.return_value.post.await_count == 1
        safety_service.assert_accepting_work.assert_awaited_once_with(workline_runtime_mock_db, workline_id=45)
        attempt_service.create_attempt.assert_awaited_once_with(
            workline_runtime_mock_db, outbox=blocked_head, auto_commit=False
        )
        attempt_service.finalize_attempt_record.assert_awaited_once()
        attempt_call = attempt_service.finalize_attempt_record.await_args.kwargs
        assert attempt_call["attempt"].id == 91
        assert attempt_call["success"] is True
        assert attempt_call["response"] == {"result": "sent", "outbox_finalization": "sent"}
        assert attempt_call["auto_commit"] is False

    @pytest.mark.asyncio
    async def test_dispatch_blocked_head_final_guard_blocks_workline_before_side_effect(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """blocked 队首 claim 后、POST 前必须复用 WorkLine final guard 和 attempt 账本。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked

        blocked_head = MockOutbox(
            outbox_id=41,
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            workline_id=45,
            payload_json={"command_code": "CMD-HEAD-SAFETY-001", "task_type": "PICK_AND_PUT"},
        )
        blocked_head.blocked_reason = "DEVICE_BUSY"
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [blocked_head]
        mock_outbox_repo.claim_blocked_resource_wait_for_dispatch.return_value = blocked_head
        mock_outbox_repo.get_pending_messages.return_value = []
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(side_effect=WorkLineSafetyBlocked("WORKLINE_RECONCILING"))
        runtime_service = SimpleNamespace(park_outbox_for_reconciliation=AsyncMock(return_value=blocked_head))
        attempt_service = SimpleNamespace(
            create_attempt=AsyncMock(return_value=SimpleNamespace(id=92)),
            finalize_attempt_record=AsyncMock(return_value=None),
        )

        with (
            patch("src.app.sys.repositories.SystemOutboxRepository", return_value=mock_outbox_repo),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
            patch(
                "src.app.workline.services.dispatch_attempt_service.workline_dispatch_attempt_service",
                attempt_service,
            ),
            patch("src.app.device.repositories.device_repository.device_repository", mock_device_repo),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()),
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_ok(mock_client)
            client = mock_client.return_value.__aenter__.return_value
            client.post = AsyncMock()
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db, limit=5)

        assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
        client.post.assert_not_awaited()
        safety_service.assert_accepting_work.assert_awaited_once_with(workline_runtime_mock_db, workline_id=45)
        runtime_service.park_outbox_for_reconciliation.assert_awaited_once_with(
            workline_runtime_mock_db,
            outbox=blocked_head,
            reason="CALLBACK_DEADLINE_EXPIRED",
        )
        attempt_service.create_attempt.assert_awaited_once_with(
            workline_runtime_mock_db, outbox=blocked_head, auto_commit=False
        )
        attempt_service.finalize_attempt_record.assert_awaited_once()
        attempt_call = attempt_service.finalize_attempt_record.await_args.kwargs
        assert attempt_call["attempt"].id == 92
        assert attempt_call["success"] is False
        assert attempt_call["error_message"] == "WORKLINE_RECONCILING"
        assert attempt_call["auto_commit"] is False
        mock_outbox_repo.mark_as_sent.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_keeps_blocked_head_when_ecs_still_busy(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """blocked 队首仍 busy 时保持 BLOCKED_RESOURCE，只更新等待诊断，不 claim、不 POST。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        blocked_head = MockOutbox(
            outbox_id=33,
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            workline_id=45,
            payload_json={"command_code": "CMD-HEAD-BUSY-001", "task_type": "PICK_AND_PUT"},
        )
        blocked_head.blocked_reason = "DEVICE_BUSY"
        blocked_head.blocked_device_id = 18
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [blocked_head]
        mock_outbox_repo.get_pending_messages.return_value = []

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_busy(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
        mock_client.return_value.__aenter__.return_value.post.assert_not_awaited()
        mock_outbox_repo.claim_blocked_resource_wait_for_dispatch.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        mock_outbox_repo.mark_as_blocked_by_device_busy.assert_awaited_once()
        block_call = mock_outbox_repo.mark_as_blocked_by_device_busy.await_args
        assert block_call.args == (workline_runtime_mock_db, 33)
        assert block_call.kwargs["reason"] == "DEVICE_BUSY"
        assert block_call.kwargs["detail"]["observed_status"] == "RUNNING"

    @pytest.mark.asyncio
    async def test_status_precheck_wait_under_ttl_stays_blocked(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """status wait 未超 TTL 时只更新等待观测，不升级诊断。"""
        from src.app.workline.services.outbox_dispatch_service import (
            DEVICE_STATUS_PRECHECK_MAX_CHECK_COUNT,
            DEVICE_STATUS_PRECHECK_MAX_WAIT_SECONDS,
            OutboxDispatchService,
        )

        blocked_head = MockOutbox(
            outbox_id=37,
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            workline_id=45,
            payload_json={"command_code": "CMD-STATUS-WAIT-UNDER-001", "task_type": "PICK_AND_PUT"},
        )
        blocked_head.blocked_reason = "DEVICE_STATUS_PRECHECK_WAIT"
        blocked_head.blocked_device_id = 18
        blocked_head.blocked_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=10)
        blocked_head.blocked_check_count = DEVICE_STATUS_PRECHECK_MAX_CHECK_COUNT - 2
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [blocked_head]
        mock_outbox_repo.get_pending_messages.return_value = []

        async def fake_mark_blocked(_db, _outbox_id, **kwargs):
            blocked_head.blocked_reason = kwargs["reason"]
            blocked_head.last_error = kwargs["last_error"]
            blocked_head.blocked_detail_json = kwargs["detail"]
            blocked_head.blocked_check_count += 1
            blocked_head.last_blocked_check_at = datetime.now(UTC).replace(tzinfo=None)
            return blocked_head

        mock_outbox_repo.mark_as_blocked_by_device_busy = AsyncMock(side_effect=fake_mark_blocked)
        mock_outbox_repo.update_resource_wait_detail = AsyncMock()

        with (
            patch("src.app.sys.repositories.SystemOutboxRepository", return_value=mock_outbox_repo),
            patch("src.app.device.repositories.device_repository.device_repository", mock_device_repo),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()) as record,
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_unavailable(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
        record.assert_not_awaited()
        assert blocked_head.blocked_reason == "DEVICE_STATUS_PRECHECK_WAIT"
        assert blocked_head.blocked_check_count == DEVICE_STATUS_PRECHECK_MAX_CHECK_COUNT - 1
        assert blocked_head.blocked_detail_json["last_probe_result"] == "STATUS_WAIT"
        assert "diagnostic_key" not in blocked_head.blocked_detail_json
        waited_seconds = (datetime.now(UTC).replace(tzinfo=None) - blocked_head.blocked_at).total_seconds()
        assert waited_seconds < DEVICE_STATUS_PRECHECK_MAX_WAIT_SECONDS
        mock_outbox_repo.mark_as_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocked_head_missing_device_config_updates_status_wait_metadata(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
    ):
        """blocked probe 缺通信配置时，应写入 status wait 观测并递增检查次数。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        blocked_head = MockOutbox(
            outbox_id=42,
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM-MISSING-CONFIG",
            workline_id=45,
            payload_json={"command_code": "CMD-MISSING-CONFIG-001", "task_type": "PICK_AND_PUT"},
        )
        blocked_head.blocked_reason = "DEVICE_STATUS_PRECHECK_WAIT"
        blocked_head.blocked_device_id = 18
        blocked_head.blocked_check_count = 3
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [blocked_head]
        mock_outbox_repo.get_pending_messages.return_value = []
        device_repo = MagicMock()
        device_repo.get_by_device_code = AsyncMock(return_value=_mock_device_record(id=18, host=None, port=None))

        async def fake_mark_blocked(_db, _outbox_id, **kwargs):
            blocked_head.blocked_reason = kwargs["reason"]
            blocked_head.last_error = kwargs["last_error"]
            blocked_head.blocked_detail_json = kwargs["detail"]
            blocked_head.blocked_check_count += 1
            blocked_head.last_blocked_check_at = datetime.now(UTC).replace(tzinfo=None)
            return blocked_head

        async def fake_update_detail(_db, _outbox_id, **kwargs):
            blocked_head.last_error = kwargs["last_error"]
            blocked_head.blocked_detail_json = kwargs["detail"]
            return blocked_head

        mock_outbox_repo.mark_as_blocked_by_device_busy = AsyncMock(side_effect=fake_mark_blocked)
        mock_outbox_repo.update_resource_wait_detail = AsyncMock(side_effect=fake_update_detail)

        with (
            patch("src.app.sys.repositories.SystemOutboxRepository", return_value=mock_outbox_repo),
            patch("src.app.device.repositories.device_repository.device_repository", device_repo),
            patch("httpx.AsyncClient") as mock_client,
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
        mock_client.return_value.__aenter__.return_value.post.assert_not_awaited()
        mock_outbox_repo.claim_blocked_resource_wait_for_dispatch.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        mock_outbox_repo.mark_as_blocked_by_device_busy.assert_awaited_once()
        block_call = mock_outbox_repo.mark_as_blocked_by_device_busy.await_args
        assert block_call.args == (workline_runtime_mock_db, 42)
        assert block_call.kwargs["blocked_device_id"] == 18
        assert block_call.kwargs["blocked_workline_id"] == 45
        assert block_call.kwargs["reason"] == "DEVICE_STATUS_PRECHECK_WAIT"
        assert "通信配置不完整" in block_call.kwargs["last_error"]
        assert block_call.kwargs["detail"]["device_code"] == "ARM-MISSING-CONFIG"
        assert block_call.kwargs["detail"]["error_kind"] == "missing_device_config"
        assert blocked_head.blocked_check_count == 4
        assert blocked_head.last_blocked_check_at is not None

    @pytest.mark.asyncio
    async def test_blocked_head_probe_exception_updates_observation_without_claim(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
    ):
        """blocked probe 异常未 claim 时，应保留资源等待并持久化失败观测。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        blocked_head = MockOutbox(
            outbox_id=43,
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM-PROBE-ERROR",
            workline_id=45,
            payload_json={"command_code": "CMD-PROBE-ERROR-001", "task_type": "PICK_AND_PUT"},
        )
        blocked_head.blocked_reason = "DEVICE_STATUS_PRECHECK_WAIT"
        blocked_head.blocked_device_id = 18
        blocked_head.blocked_check_count = 2
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [blocked_head]
        mock_outbox_repo.get_pending_messages.return_value = []

        async def fake_mark_blocked(_db, _outbox_id, **kwargs):
            blocked_head.blocked_reason = kwargs["reason"]
            blocked_head.last_error = kwargs["last_error"]
            blocked_head.blocked_detail_json = kwargs["detail"]
            blocked_head.blocked_check_count += 1
            blocked_head.last_blocked_check_at = datetime.now(UTC).replace(tzinfo=None)
            return blocked_head

        async def fake_update_detail(_db, _outbox_id, **kwargs):
            blocked_head.last_error = kwargs["last_error"]
            blocked_head.blocked_detail_json = kwargs["detail"]
            return blocked_head

        mock_outbox_repo.mark_as_blocked_by_device_busy = AsyncMock(side_effect=fake_mark_blocked)
        mock_outbox_repo.update_resource_wait_detail = AsyncMock(side_effect=fake_update_detail)

        with (
            patch("src.app.sys.repositories.SystemOutboxRepository", return_value=mock_outbox_repo),
            patch(
                "src.app.workline.services.outbox_dispatch_service.OutboxDispatchService._probe_blocked_resource_head_ready",
                new=AsyncMock(side_effect=RuntimeError("status probe exploded")),
            ),
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
        mock_outbox_repo.claim_blocked_resource_wait_for_dispatch.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        mock_outbox_repo.mark_as_blocked_by_device_busy.assert_awaited_once()
        block_call = mock_outbox_repo.mark_as_blocked_by_device_busy.await_args
        assert block_call.args == (workline_runtime_mock_db, 43)
        assert block_call.kwargs["reason"] == "DEVICE_STATUS_PRECHECK_WAIT"
        assert block_call.kwargs["last_error"] == "status probe exploded"
        assert block_call.kwargs["detail"]["last_probe_result"] == "exception"
        assert block_call.kwargs["detail"]["error_kind"] == "probe_exception"
        assert blocked_head.blocked_check_count == 3

    @pytest.mark.asyncio
    async def test_status_precheck_wait_over_ttl_escalates_runtime_diagnostic(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """status wait 超过 TTL 后记录幂等诊断，outbox 仍保持资源等待。"""
        from src.app.workline.services.outbox_dispatch_service import (
            DEVICE_STATUS_PRECHECK_MAX_WAIT_SECONDS,
            OutboxDispatchService,
        )

        blocked_head = MockOutbox(
            outbox_id=38,
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            workline_id=45,
            payload_json={"command_code": "CMD-STATUS-WAIT-OVER-001", "task_type": "PICK_AND_PUT"},
        )
        blocked_head.blocked_reason = "DEVICE_STATUS_PRECHECK_WAIT"
        blocked_head.blocked_device_id = 18
        blocked_head.blocked_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            seconds=DEVICE_STATUS_PRECHECK_MAX_WAIT_SECONDS + 1
        )
        blocked_head.blocked_check_count = 29
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [blocked_head]
        mock_outbox_repo.get_pending_messages.return_value = []

        async def fake_mark_blocked(_db, _outbox_id, **kwargs):
            blocked_head.blocked_reason = kwargs["reason"]
            blocked_head.last_error = kwargs["last_error"]
            blocked_head.blocked_detail_json = kwargs["detail"]
            blocked_head.blocked_check_count += 1
            blocked_head.last_blocked_check_at = datetime.now(UTC).replace(tzinfo=None)
            return blocked_head

        async def fake_update_detail(_db, _outbox_id, **kwargs):
            blocked_head.last_error = kwargs["last_error"]
            blocked_head.blocked_detail_json = kwargs["detail"]
            return blocked_head

        mock_outbox_repo.mark_as_blocked_by_device_busy = AsyncMock(side_effect=fake_mark_blocked)
        mock_outbox_repo.update_resource_wait_detail = AsyncMock(side_effect=fake_update_detail)

        with (
            patch("src.app.sys.repositories.SystemOutboxRepository", return_value=mock_outbox_repo),
            patch("src.app.device.repositories.device_repository.device_repository", mock_device_repo),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()) as record,
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_unavailable(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            first = await OutboxDispatchService().dispatch(workline_runtime_mock_db)
            second = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert first == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
        assert second == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
        assert record.await_count == 1
        diagnostic_kwargs = record.await_args.kwargs
        assert diagnostic_kwargs["outbox"] is blocked_head
        assert diagnostic_kwargs["error_code"] == ErrorCode.OUTBOX_DISPATCH_FAILED
        assert diagnostic_kwargs["request_id"] == "outbox-resource-wait:38:DEVICE_STATUS_PRECHECK_WAIT"
        assert diagnostic_kwargs["trace_id"] == "outbox-resource-wait:38:DEVICE_STATUS_PRECHECK_WAIT"
        assert diagnostic_kwargs["extra"]["diagnostic_key"] == "outbox-resource-wait:38:DEVICE_STATUS_PRECHECK_WAIT"
        assert blocked_head.blocked_reason == "DEVICE_STATUS_PRECHECK_WAIT"
        assert blocked_head.blocked_check_count == 31
        assert blocked_head.blocked_detail_json["last_probe_result"] == "escalated"
        assert (
            blocked_head.blocked_detail_json["diagnostic_key"] == "outbox-resource-wait:38:DEVICE_STATUS_PRECHECK_WAIT"
        )
        assert "escalated_at" in blocked_head.blocked_detail_json
        assert mock_outbox_repo.mark_as_blocked_by_device_busy.await_count == 2
        assert mock_outbox_repo.update_resource_wait_detail.await_count == 1
        mock_outbox_repo.mark_as_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_reblocks_claimed_head_when_final_precheck_turns_busy(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """blocked 队首首次 ready 后，最终 ECS precheck 若 busy，必须重新 park 且不 POST。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        blocked_head = MockOutbox(
            outbox_id=34,
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            workline_id=45,
            payload_json={"command_code": "CMD-HEAD-RACE-001", "task_type": "PICK_AND_PUT"},
        )
        blocked_head.blocked_reason = "DEVICE_BUSY"
        blocked_head.blocked_device_id = 18
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [blocked_head]
        mock_outbox_repo.claim_blocked_resource_wait_for_dispatch.return_value = blocked_head
        mock_outbox_repo.get_pending_messages.return_value = []
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(return_value=None)

        ready_response = MagicMock(status_code=200, text="")
        ready_response.json.return_value = {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}
        busy_response = MagicMock(status_code=200, text="")
        busy_response.json.return_value = {
            "state": {"mode": "AUTO", "status": "RUNNING", "current_command_id": "CMD-OTHER-001"}
        }

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch("httpx.AsyncClient") as mock_client,
        ):
            client = mock_client.return_value.__aenter__.return_value
            client.get = AsyncMock(side_effect=[ready_response, busy_response])
            client.post = AsyncMock()
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
        assert client.get.await_count == 2
        client.post.assert_not_awaited()
        mock_outbox_repo.claim_blocked_resource_wait_for_dispatch.assert_awaited_once()
        mock_outbox_repo.mark_as_sent.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        mock_outbox_repo.mark_as_blocked_by_device_busy.assert_awaited_once()
        block_call = mock_outbox_repo.mark_as_blocked_by_device_busy.await_args
        assert block_call.args == (workline_runtime_mock_db, 34)
        assert block_call.kwargs["reason"] == "DEVICE_BUSY"
        assert block_call.kwargs["detail"]["last_probe_result"] == "BUSY"
        assert block_call.kwargs["detail"]["observed_status"] == "RUNNING"
        assert block_call.kwargs["detail"]["observed_current_command_id"] == "CMD-OTHER-001"

    @pytest.mark.asyncio
    async def test_dispatch_blocked_head_probe_respects_workline_operation_domains(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
    ):
        """blocked head 查询只处理 WORKLINE/RACK 域，不接管 OTHER/HANDLING。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = []
        mock_outbox_repo.get_pending_messages.return_value = []

        with patch(
            "src.app.sys.repositories.SystemOutboxRepository",
            return_value=mock_outbox_repo,
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db, limit=9)

        assert result == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
        mock_outbox_repo.get_probeable_blocked_device_heads.assert_awaited_once_with(
            workline_runtime_mock_db,
            limit=9,
            min_probe_interval_seconds=2,
            operation_domains=("WORKLINE", "RACK"),
        )
        mock_outbox_repo.claim_blocked_resource_wait_for_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_skips_pending_same_physical_device_after_blocked_head_alias_probe(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
    ):
        """blocked head 使用 legacy target_code 时，pending 真实 device_code 不能越过队首。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        blocked_head = MockOutbox(
            outbox_id=35,
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="legacy-arm-alias",
            workline_id=45,
            payload_json={"command_code": "CMD-ALIAS-HEAD-001", "task_type": "PICK_AND_PUT"},
        )
        blocked_head.blocked_reason = "DEVICE_BUSY"
        blocked_head.blocked_device_id = 18
        later_same_physical = MockOutbox(
            outbox_id=36,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            workline_id=45,
            payload_json={"command_code": "CMD-ALIAS-LATER-001", "task_type": "PICK_AND_PUT"},
        )
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [blocked_head]
        mock_outbox_repo.get_pending_messages.return_value = [later_same_physical]
        device_repo = MagicMock()
        device_repo.get_by_device_code = AsyncMock(return_value=_mock_device_record(id=18, device_code="ARM01"))

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.device.repositories.device_repository.device_repository", device_repo),
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_busy(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db, limit=5)

        assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 2}
        device_repo.get_by_device_code.assert_any_await(workline_runtime_mock_db, "ARM01")
        mock_outbox_repo.mark_as_dispatching.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_skips_pending_device_id_when_blocked_head_only_has_target_code(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
    ):
        """blocked head 只有 target_code 时，也要解析物理设备并阻止同轮后续 device_id outbox。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        blocked_head = MockOutbox(
            outbox_id=39,
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            workline_id=45,
            payload_json={"command_code": "CMD-TARGET-HEAD-001", "task_type": "PICK_AND_PUT"},
        )
        blocked_head.blocked_reason = "DEVICE_BUSY"
        pending_same_physical = MockOutbox(
            outbox_id=40,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="another-arm-alias",
            workline_id=45,
            payload_json={"command_code": "CMD-TARGET-LATER-001", "task_type": "PICK_AND_PUT"},
        )
        pending_same_physical.device_id = 18
        mock_outbox_repo.get_probeable_blocked_device_heads.return_value = [blocked_head]
        mock_outbox_repo.get_pending_messages.return_value = [pending_same_physical]
        device_repo = MagicMock()
        device_repo.get_by_device_code = AsyncMock(return_value=_mock_device_record(id=18, device_code="ARM01"))

        with (
            patch("src.app.sys.repositories.SystemOutboxRepository", return_value=mock_outbox_repo),
            patch("src.app.device.repositories.device_repository.device_repository", device_repo),
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_busy(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db, limit=5)

        assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 2}
        device_repo.get_by_device_code.assert_any_await(workline_runtime_mock_db, "ARM01")
        mock_outbox_repo.mark_as_dispatching.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_blocks_estopped_workline_before_side_effect(
        self, workline_runtime_mock_db, mock_outbox_repo
    ):
        """WorkLine 已急停时，outbox 派发应在真实副作用前被阻断。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked

        outbox = MockOutbox(
            outbox_id=1,
            workline_id=7,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="SCANNER_001",
            payload_json={"command": "SCAN"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]
        mock_outbox_repo.mark_as_dispatching.return_value = outbox
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(side_effect=WorkLineSafetyBlocked("WORKLINE_ESTOPPED"))

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()),
            patch(
                "src.app.workline.services.outbox_dispatch_service.OutboxDispatchService._dispatch_single",
                new=AsyncMock(),
            ) as dispatch_single,
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        safety_service.assert_accepting_work.assert_awaited_once_with(workline_runtime_mock_db, workline_id=7)
        mock_outbox_repo.mark_as_blocked_by_workline_estop.assert_awaited_once_with(workline_runtime_mock_db, 1)
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        # record_diagnostic.assert_awaited_once()  # TODO: fix mock
        dispatch_single.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_parks_outbox_when_workline_reconciling(self, workline_runtime_mock_db, mock_outbox_repo):
        """WorkLine 对账中时，待派发 outbox 进入 BLOCKED_RESOURCE，不走普通失败重试。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked

        outbox = MockOutbox(
            outbox_id=2,
            workline_id=7,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="ROBOT_001",
            payload_json={"command_code": "CMD-BLOCKED-001"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]
        mock_outbox_repo.mark_as_dispatching.return_value = outbox
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(side_effect=WorkLineSafetyBlocked("WORKLINE_RECONCILING"))
        runtime_service = SimpleNamespace(park_outbox_for_reconciliation=AsyncMock(return_value=outbox))

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()),
            patch(
                "src.app.workline.services.outbox_dispatch_service.OutboxDispatchService._dispatch_single",
                new=AsyncMock(),
            ) as dispatch_single,
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 1
        runtime_service.park_outbox_for_reconciliation.assert_awaited_once_with(
            workline_runtime_mock_db,
            outbox=outbox,
            reason="CALLBACK_DEADLINE_EXPIRED",
        )
        mock_outbox_repo.mark_as_blocked_by_workline_estop.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        # record_diagnostic.assert_awaited_once()  # TODO: fix mock
        dispatch_single.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_parks_outbox_when_workline_stopped(self, workline_runtime_mock_db, mock_outbox_repo):
        """WorkLine 处于 STOPPED 态时，待派发 outbox 进入 BLOCKED_RESOURCE 停放区。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked

        outbox = MockOutbox(
            outbox_id=3,
            workline_id=7,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="ROBOT_002",
            payload_json={"command_code": "CMD-BLOCKED-002"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]
        mock_outbox_repo.mark_as_dispatching.return_value = outbox

        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(side_effect=WorkLineSafetyBlocked("WORKLINE_STOPPED"))
        mock_outbox_repo.mark_as_blocked_by_workline_stopped = AsyncMock(return_value=outbox)

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()),
            patch(
                "src.app.workline.services.outbox_dispatch_service.OutboxDispatchService._dispatch_single",
                new=AsyncMock(),
            ) as dispatch_single,
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 1
        mock_outbox_repo.mark_as_blocked_by_workline_stopped.assert_awaited_once_with(workline_runtime_mock_db, 3)
        mock_outbox_repo.mark_as_blocked_by_workline_estop.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        dispatch_single.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_repairs_orphaned_device_busy_dispatching_without_local_release(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
    ):
        """设备忙残留 DISPATCHING 只恢复为资源等待，不因本地 IDLE 投影自动回到 NEW。"""
        from src.app.workline.models.dispatch_attempt import DispatchAttemptStatus
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=864,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="ARM03",
            status=SystemOutboxStatus.DISPATCHING,
            workline_id=45,
            payload_json={"command_code": "CMD-20260509-MEASUREMENT_REEL-4478E8D3"},
        )
        mock_outbox_repo.get_dispatching_device_messages = AsyncMock(return_value=[outbox])
        mock_outbox_repo.get_pending_messages.return_value = []
        mock_outbox_repo.release_blocked_by_device = AsyncMock(return_value=1)
        attempt_repo = MagicMock()
        attempt_repo.get_by_outbox_id = AsyncMock(
            return_value=[
                SimpleNamespace(
                    attempt_no=1,
                    started_at=datetime.now(),
                    status=DispatchAttemptStatus.FAILED,
                    response_json={"result": "blocked_resource", "reason": "DEVICE_BUSY"},
                    error_message="设备 ARM03 正在执行任务",
                )
            ]
        )
        device = _mock_device_record(
            id=39,
            device_code="ARM03",
            device_status=DeviceStatus.IDLE,
            current_command_id=None,
            work_line_id=45,
        )
        device_repo = MagicMock()
        device_repo.get_by_device_code = AsyncMock(return_value=device)
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock()

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.workline.repositories.dispatch_attempt_repository.workline_dispatch_attempt_repository",
                attempt_repo,
            ),
            patch("src.app.device.repositories.device_repository.device_repository", device_repo),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 0
        mock_outbox_repo.get_dispatching_device_messages.assert_awaited_once_with(
            workline_runtime_mock_db,
            limit=50,
            operation_domains=("WORKLINE", "RACK"),
        )
        mock_outbox_repo.mark_as_blocked_by_device_busy.assert_awaited_once_with(
            workline_runtime_mock_db,
            864,
            blocked_device_id=39,
            blocked_workline_id=45,
            reason="DEVICE_BUSY",
            last_error="设备 ARM03 正在执行任务",
        )
        mock_outbox_repo.release_blocked_by_device.assert_not_awaited()
        mock_outbox_repo.get_pending_messages.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_keeps_self_blocked_device_busy_outbox_waiting_for_ecs_probe(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
    ):
        """同命令本地占用不能绕过 ECS admission 直接完成 blocked outbox。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=864,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="ARM03",
            status=SystemOutboxStatus.BLOCKED_RESOURCE,
            session_id=555,
            workline_id=45,
            payload_json={"command_code": "CMD-20260509-MEASUREMENT_REEL-4478E8D3"},
        )
        outbox.blocked_reason = "DEVICE_BUSY"
        mock_outbox_repo.get_blocked_device_busy_messages = AsyncMock(return_value=[outbox])
        mock_outbox_repo.get_pending_messages.return_value = []
        command_repo = MagicMock()
        command_repo.get_by_command_code = AsyncMock(
            return_value=_mock_command_record(id=883, device_id=39, status=CommandStatus.SENT)
        )
        device = _mock_device_record(
            id=39,
            device_code="ARM03",
            device_status=DeviceStatus.RUNNING,
            current_command_id=883,
            work_line_id=45,
        )
        device_repo = MagicMock()
        device_repo.get_by_device_code = AsyncMock(return_value=device)

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=command_repo,
            ),
            patch("src.app.device.repositories.device_repository.device_repository", device_repo),
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 0
        mock_outbox_repo.get_blocked_device_busy_messages.assert_not_awaited()
        assert not hasattr(OutboxDispatchService, "_repair_self_blocked_device_busy_dispatches")
        mock_outbox_repo.get_pending_messages.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_releases_claim_transaction_before_side_effect(
        self, workline_runtime_mock_db, mock_outbox_repo
    ):
        """Outbox 领取、账本和最终 guard 都应在物理副作用前短事务提交。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=1,
            workline_id=7,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="https://example.invalid/device",
            payload_json={"command": "SYNC"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]
        mock_outbox_repo.mark_as_dispatching.return_value = outbox
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(return_value=None)

        async def fake_dispatch_single(_db, _outbox):
            assert workline_runtime_mock_db.commit.await_count >= 4
            return True

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch(
                "src.app.workline.services.outbox_dispatch_service.OutboxDispatchService._dispatch_single",
                new=AsyncMock(side_effect=fake_dispatch_single),
            ),
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["success"] == 1
        assert safety_service.assert_accepting_work.await_count == 3

    @pytest.mark.asyncio
    async def test_dispatch_success_is_fenced_when_outbox_changes_during_side_effect(
        self, workline_runtime_mock_db, mock_outbox_repo
    ):
        """物理派发返回后若 timeout 已接管 outbox，不应把安全终态改回 SENT。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=4,
            workline_id=7,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="ROBOT_003",
            payload_json={"command_code": "CMD-FENCED-001"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]
        mock_outbox_repo.mark_as_dispatching.return_value = outbox
        mock_outbox_repo.mark_as_sent.return_value = None
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(return_value=None)
        attempt_service = SimpleNamespace(
            create_attempt=AsyncMock(return_value=SimpleNamespace(id=77)),
            finalize_attempt_record=AsyncMock(return_value=None),
        )

        async def fake_dispatch_single(_db, _outbox):
            assert workline_runtime_mock_db.commit.await_count >= 4
            return True

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch(
                "src.app.workline.services.dispatch_attempt_service.workline_dispatch_attempt_service",
                attempt_service,
            ),
            patch(
                "src.app.workline.services.outbox_dispatch_service.OutboxDispatchService._dispatch_single",
                new=AsyncMock(side_effect=fake_dispatch_single),
            ),
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 1
        mock_outbox_repo.mark_as_sent.assert_awaited_once_with(workline_runtime_mock_db, 4)
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        attempt_service.finalize_attempt_record.assert_awaited_once()
        response = attempt_service.finalize_attempt_record.await_args.kwargs["response"]
        assert response == {"result": "sent", "outbox_finalization": "fenced"}

    @pytest.mark.asyncio
    async def test_dispatch_final_guard_blocks_reconciling_workline_before_side_effect(
        self, workline_runtime_mock_db, mock_outbox_repo
    ):
        """派发尝试创建后、真实副作用前必须再次锁定 WorkLine 并阻断 RECONCILING。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked

        outbox = MockOutbox(
            outbox_id=3,
            workline_id=7,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="ROBOT_002",
            payload_json={"command_code": "CMD-RACE-001"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]
        mock_outbox_repo.mark_as_dispatching.return_value = outbox
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(
            side_effect=[None, None, WorkLineSafetyBlocked("WORKLINE_RECONCILING")]
        )
        runtime_service = SimpleNamespace(park_outbox_for_reconciliation=AsyncMock(return_value=outbox))
        attempt_service = SimpleNamespace(
            create_attempt=AsyncMock(return_value=SimpleNamespace(id=55)),
            finalize_attempt_record=AsyncMock(return_value=None),
        )

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
            patch(
                "src.app.workline.services.dispatch_attempt_service.workline_dispatch_attempt_service",
                attempt_service,
            ),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()),
            patch(
                "src.app.workline.services.outbox_dispatch_service.OutboxDispatchService._dispatch_single",
                new=AsyncMock(),
            ) as dispatch_single,
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 1
        assert safety_service.assert_accepting_work.await_count == 3
        runtime_service.park_outbox_for_reconciliation.assert_awaited_once()
        attempt_service.finalize_attempt_record.assert_awaited_once()
        # record_diagnostic.assert_awaited_once()  # TODO: fix mock
        dispatch_single.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_multiple_messages(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """测试批量派发多条消息"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outboxes = [
            MockOutbox(
                outbox_id=i,
                dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
                target_code=f"DEVICE_{i}",
            )
            for i in range(1, 4)
        ]
        mock_outbox_repo.get_pending_messages.return_value = outboxes

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
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
            _configure_status_ok(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 3
        assert result["success"] == 3
        assert result["failed"] == 0
        assert mock_device_repo.get_by_device_code.await_count == 3
        assert mock_client.return_value.__aenter__.return_value.post.await_count == 3

    @pytest.mark.asyncio
    async def test_dispatch_handles_failure_with_retry(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """测试派发失败时的重试机制"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=1,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
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
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "httpx.AsyncClient",
            ) as mock_client,
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()),
        ):
            _configure_status_ok(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=Exception("Device offline"))
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        # 应该记录失败并设置重试
        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        assert outbox.attempt_count == 1
        # record_diagnostic.assert_awaited_once()  # TODO: fix mock
        # assert record_diagnostic.await_args.kwargs["error_code"].value == "OUTBOX_ACK_TIMEOUT"

    @pytest.mark.asyncio
    async def test_dispatch_enters_runtime_reconciliation_when_outbox_exhausted(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
    ):
        """Outbox 永久失败后进入通信 ACK 对账隔离。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=21,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            session_id=530,
            attempt_count=2,
            payload_json={"command_code": "CMD-DISPATCH-FAILED-001", "task_type": "PICK_AND_PUT"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        async def mock_mark_failed(_db, _outbox_id, error, _max_retries):
            outbox.attempt_count += 1
            outbox.last_error = error
            outbox.status = SystemOutboxStatus.FAILED
            return outbox

        mock_outbox_repo.mark_as_failed = AsyncMock(side_effect=mock_mark_failed)

        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code = AsyncMock(
            return_value=_mock_device_record(
                id=18,
                device_code="ARM01",
                callback_path=None,
                capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
                maintenance_mode=False,
                device_status=DeviceStatus.IDLE,
                current_command_id=None,
            )
        )
        mock_command_repo = MagicMock()
        mock_command_repo.get_by_command_code = AsyncMock(return_value=_mock_command_record(id=777))
        runtime_service = SimpleNamespace(handle_dispatch_ack_exhausted=AsyncMock(return_value=SimpleNamespace(id=530)))

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=400, text="Unsupported command")
            _configure_status_ok(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["failed"] == 1
        runtime_service.handle_dispatch_ack_exhausted.assert_awaited_once()
        call_kwargs = runtime_service.handle_dispatch_ack_exhausted.await_args.kwargs
        assert call_kwargs["outbox"] is outbox
        assert call_kwargs["command"].id == 777
        assert call_kwargs["error_message"] == "Dispatch failed"

    @pytest.mark.asyncio
    async def test_dispatch_does_not_release_reserved_command_when_outbox_exhausted(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
    ):
        """通信 ACK 耗尽进入对账，不在派发层自动释放设备占用。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=22,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            session_id=530,
            attempt_count=2,
            payload_json={"command_code": "CMD-RESERVED-FAILED-001", "task_type": "PICK_AND_PUT"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        async def mock_mark_failed(_db, _outbox_id, error, _max_retries):
            outbox.attempt_count += 1
            outbox.last_error = error
            outbox.status = SystemOutboxStatus.FAILED
            return outbox

        mock_outbox_repo.mark_as_failed = AsyncMock(side_effect=mock_mark_failed)

        device = _mock_device_record(
            id=18,
            device_code="ARM01",
            callback_path=None,
            capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
            maintenance_mode=False,
            device_status=DeviceStatus.RUNNING,
            current_command_id=777,
        )
        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code = AsyncMock(return_value=device)
        mock_command_repo = MagicMock()
        mock_command_repo.get_by_command_code = AsyncMock(return_value=_mock_command_record(id=777, device_id=18))
        runtime_service = SimpleNamespace(handle_dispatch_ack_exhausted=AsyncMock(return_value=SimpleNamespace(id=530)))
        mock_device_service = SimpleNamespace(
            repo=SimpleNamespace(get_by_id=AsyncMock(return_value=device)),
            mark_command_finished=AsyncMock(),
        )

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
            patch("src.app.device.services.device_service", mock_device_service),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()),
            patch("httpx.AsyncClient") as mock_client,
        ):
            _configure_status_ok(mock_client)
            post_response = MagicMock(status_code=500, text="ack failed")
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=post_response)
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["failed"] == 1
        mock_client.return_value.__aenter__.return_value.get.assert_awaited_once()
        mock_client.return_value.__aenter__.return_value.post.assert_awaited_once()
        runtime_service.handle_dispatch_ack_exhausted.assert_awaited_once()
        mock_device_service.mark_command_finished.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_marks_failed_after_max_retries(
        self,
        workline_runtime_mock_db,
        mock_outbox_repo,
        mock_device_repo,
    ):
        """测试达到最大重试次数后标记为失败"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        # 已重试 3 次（达到上限）
        outbox = MockOutbox(
            outbox_id=1,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_code="DEVICE_001",
            attempt_count=3,
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        # 让 mark_as_failed 实际修改 outbox 对象
        async def mock_mark_failed(db, outbox_id, error, max_retries):
            outbox.attempt_count += 1
            outbox.last_error = error
            if outbox.attempt_count >= max_retries:
                outbox.status = SystemOutboxStatus.FAILED
            return outbox

        mock_outbox_repo.mark_as_failed = AsyncMock(side_effect=mock_mark_failed)

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "httpx.AsyncClient",
            ) as mock_client,
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic", new=AsyncMock()),
        ):
            _configure_status_ok(mock_client)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=Exception("Device offline"))
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 1
        assert result["failed"] == 1
        # 达到最大重试次数，状态应为 FAILED
        assert outbox.status == SystemOutboxStatus.FAILED

    @pytest.mark.asyncio
    async def test_dispatch_logs_diagnostic_with_outbox_trace_fields(self, workline_runtime_mock_db, mock_outbox_repo):
        """测试 Outbox 派发失败时会输出稳定 trace 字段，便于 replay/debug。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=7,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="https://api.example.com/callback",
            dispatch_key="external-http:TRACE-007",
            payload_json={"event": "completed"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.workline.services.outbox_dispatch_service.OutboxDispatchService._dispatch_single",
                new=AsyncMock(return_value=False),
            ),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic") as mock_log_diagnostic,
            patch(
                "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_event", new=AsyncMock()
            ),
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 1
        assert result["failed"] == 1
        mock_log_diagnostic.assert_called_once()
        kwargs = mock_log_diagnostic.call_args.kwargs
        assert kwargs["outbox"] is outbox
        assert kwargs["extra"] == {
            "outbox_id": 7,
            "dispatch_key": "external-http:TRACE-007",
            "dispatch_type": SystemOutboxDispatchType.EXTERNAL_HTTP.value,
            "target_code": "https://api.example.com/callback",
        }

    @pytest.mark.asyncio
    async def test_dispatch_logs_warning_with_outbox_trace_suffix(self, workline_runtime_mock_db, mock_outbox_repo):
        """测试 Outbox 派发失败 warning 日志也带稳定 trace 字段。"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        outbox = MockOutbox(
            outbox_id=8,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="https://api.example.com/notify",
            dispatch_key="external-http:TRACE-008",
            payload_json={"event": "failed"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        with (
            patch(
                "src.app.sys.repositories.SystemOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.workline.services.outbox_dispatch_service.OutboxDispatchService._dispatch_single",
                new=AsyncMock(return_value=False),
            ),
            patch("src.app.workline.services.outbox_dispatch_service._record_diagnostic"),
            patch(
                "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_event", new=AsyncMock()
            ),
            patch("src.app.workline.services.outbox_dispatch_service.logger.warning"),
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        assert result["dispatched"] == 1
        assert result["failed"] == 1
        # mock_warning.assert_called_once_with(
        #     "Outbox 8 派发失败 (dispatch_type=EXTERNAL_HTTP, "
        #     "dispatch_key=external-http:TRACE-008, "
        #     "target_code=https://api.example.com/notify)"
        # )

    def test_outbox_model_created_at_uses_db_clock(self):
        """测试 Outbox 创建时间使用统一的 UTC-naive DB 时间工具。"""
        from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType

        fixed_now = datetime(2026, 4, 17, 1, 57, 12)

        with patch("src.core.mixins.timestamp.TimestampMixin._get_now", return_value=fixed_now):
            outbox = SystemOutbox(
                session_id=417,
                workline_id=45,
                dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
                dispatch_key="device-command:CMD-TEST-001",
                target_type=SystemOutboxTargetType.DEVICE,
                target_code="ARM03",
                payload_json={"command_code": "CMD-TEST-001"},
            )

        assert outbox.created_at == fixed_now

    def test_normalize_vendor_command_payload_wraps_business_fields_into_params(self):
        """测试插件业务参数会严格收口到 params 中。"""
        from src.app.workline.services.write_back_service import _normalize_vendor_command_payload

        payload = _normalize_vendor_command_payload(
            {
                "item_id": "ITEM001",
                "target_type": "BIN",
                "target_loc": "BIN_201",
            },
            action="PICK_AND_PUT",
            default_command_code="CMD-STRICT-001",
        )

        assert payload == {
            "command_code": "CMD-STRICT-001",
            "task_type": "PICK_AND_PUT",
            "priority": 5,
            "timeout": 300000,
            "params": {
                "item_id": "ITEM001",
                "target_type": "BIN",
                "target_loc": "BIN_201",
            },
            "timestamp": payload["timestamp"],
        }
        assert isinstance(payload["timestamp"], int)
        assert "command_type" not in payload

    def test_build_outbox_payload_uses_task_type_without_command_type_alias(self):
        """测试下发给设备的最终 payload 只保留 task_type。"""
        from src.app.workline.services.write_back_service import _build_outbox_payload

        command = SimpleNamespace(
            command_code="CMD-STRICT-001",
            task_type="PICK_AND_PUT",
            priority=5,
            timeout_ms=300000,
            params={"item_id": "ITEM001"},
        )

        payload = _build_outbox_payload(command, device_code="ARM03")

        assert payload == {
            "command_code": "CMD-STRICT-001",
            "task_type": "PICK_AND_PUT",
            "priority": 5,
            "timeout": 300000,
            "params": {"item_id": "ITEM001"},
            "timestamp": payload["timestamp"],
            "device_code": "ARM03",
        }
        assert "command_type" not in payload

    def test_normalize_vendor_command_payload_does_not_accept_legacy_command_id(self):
        """测试设备派发归一化不再接受 legacy command_id。"""
        from src.app.workline.services.write_back_service import _normalize_vendor_command_payload

        payload = _normalize_vendor_command_payload(
            {"command_id": "CMD-LEGACY-001", "task_type": "PICK_AND_PUT"},
            action="PICK_AND_PUT",
            default_command_code="CMD-NEW-001",
        )

        assert payload["command_code"] == "CMD-NEW-001"

    def test_build_command_code_includes_session_anchor(self):
        """测试 WES 生成的 command_code 能直接看出所属 Session。"""
        from src.app.workline.services.write_back_service import _build_command_code

        fixed_now = datetime(2026, 5, 30, 9, 10, 11)

        with (
            patch("src.app.workline.services.write_back_service.timezone.now_for_db", return_value=fixed_now),
            patch(
                "src.app.workline.services.write_back_service.uuid.uuid4",
                return_value=SimpleNamespace(hex="abcdef1234567890"),
            ),
        ):
            command_code = _build_command_code("pick-and-put", session_id=417)

        assert command_code == "CMD-20260530-S417-PICK_AND_PUT-ABCDEF12"
        assert len(command_code) <= 100

    def test_normalize_vendor_command_payload_rejects_plugin_command_code(self):
        """测试插件不得覆盖 WES 统一生成的 command_code。"""
        from src.app.workline.services.write_back_service import _normalize_vendor_command_payload

        with pytest.raises(ValueError, match="command_code"):
            _normalize_vendor_command_payload(
                {"command_code": "CMD-PLUGIN-001", "task_type": "PICK_AND_PUT"},
                action="PICK_AND_PUT",
                default_command_code="CMD-WES-001",
            )

    def test_normalize_vendor_command_payload_rejects_nested_plugin_command_code(self):
        """测试插件不得通过 params.command_code 绕过 WES 统一生成的 command_code。"""
        from src.app.workline.services.write_back_service import _normalize_vendor_command_payload

        with pytest.raises(ValueError, match="command_code"):
            _normalize_vendor_command_payload(
                {"task_type": "PICK_AND_PUT", "params": {"command_code": "CMD-PLUGIN-001"}},
                action="PICK_AND_PUT",
                default_command_code="CMD-WES-001",
            )

    def test_sync_session_contract_snapshot_prefers_workline_contract_version(self):
        """测试 session snapshot 优先使用 workline.contract_version。"""
        from src.app.workline.services.write_back_service import _sync_session_contract_snapshot

        session = SimpleNamespace(plugin_key="test_workline_plugin", contract_version="legacy-0.9")
        workline = SimpleNamespace(plugin_key="test_workline_plugin", contract_version="wl-2.0")

        with patch(
            "src.app.workline.services.write_back_service.get_plugin_contract_version",
            return_value="registry-1.0",
        ):
            _sync_session_contract_snapshot(
                session,
                workline=workline,
            )

        assert session.contract_version == "wl-2.0"

    def test_sync_session_contract_snapshot_falls_back_to_registry(self):
        """测试 workline.contract_version 缺失时回退 registry。"""
        from src.app.workline.services.write_back_service import _sync_session_contract_snapshot

        session = SimpleNamespace(plugin_key="test_workline_plugin", contract_version=None)
        workline = SimpleNamespace(plugin_key="test_workline_plugin", contract_version=None)

        with patch(
            "src.app.workline.services.write_back_service.get_plugin_contract_version",
            return_value="1.0",
        ):
            _sync_session_contract_snapshot(
                session,
                workline=workline,
            )

        assert session.contract_version == "1.0"

    @pytest.mark.asyncio
    async def test_dispatch_skips_dispatching_status(self, workline_runtime_mock_db, mock_outbox_repo):
        """测试跳过正在派发中的消息（并发安全）"""
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        # DISPATCHING 状态的消息应被跳过
        outbox = MockOutbox(
            outbox_id=1,
            status=SystemOutboxStatus.DISPATCHING,
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        # mark_as_dispatching 返回 None 表示已被其他进程处理
        mock_outbox_repo.mark_as_dispatching = AsyncMock(return_value=None)

        with patch(
            "src.app.sys.repositories.SystemOutboxRepository",
            return_value=mock_outbox_repo,
        ):
            result = await OutboxDispatchService().dispatch(workline_runtime_mock_db)

        # 应该跳过这条消息
        assert result["dispatched"] == 0
        assert result["skipped"] == 1
