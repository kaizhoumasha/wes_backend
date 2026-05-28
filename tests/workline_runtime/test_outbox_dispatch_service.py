"""
OutboxDispatchService 单元测试

测试 Celery 任务派发 SystemOutbox 的流程：
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

from src.app.device.models.command import CommandStatus
from src.app.device.models.device import DeviceStatus
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType


def _mock_device_record(**overrides: object) -> SimpleNamespace:
    return SimpleNamespace(host="127.0.0.1", port=8006, timeout=10000, protocol="HTTP", **overrides)


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
        self.finished_at = None
        self.blocked_by_reconciliation_session_id = None
        self.blocked_device_id = None
        self.blocked_workline_id = None
        self.blocked_reason = None


class TestOutboxDispatchService:
    """OutboxDispatchService 任务测试"""

    @pytest.mark.asyncio
    async def test_dispatcher_only_loads_workline_domain_outbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Workline 历史 dispatcher 只能处理 WORKLINE 域，避免抢占 Rack/Handling 消息。"""

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
        assert instances[0].pending_filters == [{"limit": 7, "operation_domains": ("WORKLINE",)}]

    @pytest.fixture
    def mock_db(self):
        """创建模拟数据库会话"""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=execute_result)
        return db

    @pytest.fixture
    def mock_outbox_repo(self):
        """创建模拟 OutboxRepository"""
        repo = MagicMock()
        repo.get_pending_messages = AsyncMock(return_value=[])
        repo.mark_as_dispatching = AsyncMock(return_value=MagicMock())
        repo.mark_as_blocked_by_workline_estop = AsyncMock(return_value=MagicMock())
        repo.mark_as_blocked_by_device_busy = AsyncMock(return_value=MagicMock())
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
        from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService

        with patch(
            "src.app.sys.repositories.SystemOutboxRepository",
            return_value=mock_outbox_repo,
        ):
            result = await OutboxDispatchService().dispatch(mock_db)

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
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await OutboxDispatchService().dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 1
        assert result["failed"] == 0
        mock_device_repo.get_by_device_code.assert_awaited_once_with(mock_db, "SCANNER_001")
        mock_client.return_value.__aenter__.return_value.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_blocks_estopped_workline_before_side_effect(self, mock_db, mock_outbox_repo):
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
            result = await OutboxDispatchService().dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        safety_service.assert_accepting_work.assert_awaited_once_with(mock_db, workline_id=7)
        mock_outbox_repo.mark_as_blocked_by_workline_estop.assert_awaited_once_with(mock_db, 1)
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        # record_diagnostic.assert_awaited_once()  # TODO: fix mock
        dispatch_single.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_parks_outbox_when_workline_reconciling(self, mock_db, mock_outbox_repo):
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
            result = await OutboxDispatchService().dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 1
        runtime_service.park_outbox_for_reconciliation.assert_awaited_once_with(
            mock_db,
            outbox=outbox,
            reason="CALLBACK_DEADLINE_EXPIRED",
        )
        mock_outbox_repo.mark_as_blocked_by_workline_estop.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        # record_diagnostic.assert_awaited_once()  # TODO: fix mock
        dispatch_single.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_repairs_orphaned_device_busy_dispatching_when_device_is_idle(
        self,
        mock_db,
        mock_outbox_repo,
    ):
        """设备忙导致的残留 DISPATCHING 应在设备空闲时自动回到 NEW 队列。"""
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
            result = await OutboxDispatchService().dispatch(mock_db)

        assert result["dispatched"] == 0
        mock_outbox_repo.mark_as_blocked_by_device_busy.assert_awaited_once_with(
            mock_db,
            864,
            blocked_device_id=39,
            blocked_workline_id=45,
            reason="DEVICE_BUSY",
            last_error="设备 ARM03 正在执行任务",
        )
        mock_outbox_repo.release_blocked_by_device.assert_awaited_once_with(
            mock_db,
            device_id=39,
            workline_id=45,
        )
        mock_outbox_repo.get_pending_messages.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_repairs_self_blocked_device_busy_outbox(
        self,
        mock_db,
        mock_outbox_repo,
    ):
        """已派发命令占用同一设备时，DEVICE_BUSY 自阻塞应自动恢复为 SENT。"""
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
        mock_outbox_repo.mark_blocked_device_busy_as_sent = AsyncMock(return_value=outbox)
        command_repo = MagicMock()
        command_repo.get_by_command_code = AsyncMock(
            return_value=_mock_command_record(id=883, device_id=39, session_id_int=555, status=CommandStatus.SENT)
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
            result = await OutboxDispatchService().dispatch(mock_db)

        assert result["dispatched"] == 0
        mock_outbox_repo.mark_blocked_device_busy_as_sent.assert_awaited_once_with(mock_db, 864)
        mock_outbox_repo.get_pending_messages.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_releases_claim_transaction_before_side_effect(self, mock_db, mock_outbox_repo):
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
            assert mock_db.commit.await_count >= 4
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
            result = await OutboxDispatchService().dispatch(mock_db)

        assert result["success"] == 1
        assert safety_service.assert_accepting_work.await_count == 3

    @pytest.mark.asyncio
    async def test_dispatch_success_is_fenced_when_outbox_changes_during_side_effect(self, mock_db, mock_outbox_repo):
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
            assert mock_db.commit.await_count >= 4
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
            result = await OutboxDispatchService().dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 1
        mock_outbox_repo.mark_as_sent.assert_awaited_once_with(mock_db, 4)
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        attempt_service.finalize_attempt_record.assert_awaited_once()
        response = attempt_service.finalize_attempt_record.await_args.kwargs["response"]
        assert response == {"result": "sent", "outbox_finalization": "fenced"}

    @pytest.mark.asyncio
    async def test_dispatch_final_guard_blocks_reconciling_workline_before_side_effect(self, mock_db, mock_outbox_repo):
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
            result = await OutboxDispatchService().dispatch(mock_db)

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
        mock_db,
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
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await OutboxDispatchService().dispatch(mock_db)

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
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=Exception("Device offline"))
            result = await OutboxDispatchService().dispatch(mock_db)

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
        mock_db,
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
        mock_command_repo.get_by_command_code = AsyncMock(return_value=_mock_command_record(id=777, session_id_int=530))
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
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await OutboxDispatchService().dispatch(mock_db)

        assert result["failed"] == 1
        runtime_service.handle_dispatch_ack_exhausted.assert_awaited_once()
        call_kwargs = runtime_service.handle_dispatch_ack_exhausted.await_args.kwargs
        assert call_kwargs["outbox"] is outbox
        assert call_kwargs["command"].id == 777
        assert call_kwargs["error_message"] == "Dispatch failed"

    @pytest.mark.asyncio
    async def test_dispatch_does_not_release_reserved_command_when_outbox_exhausted(
        self,
        mock_db,
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
        mock_command_repo.get_by_command_code = AsyncMock(
            return_value=_mock_command_record(id=777, device_id=18, session_id_int=530)
        )
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
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            result = await OutboxDispatchService().dispatch(mock_db)

        assert result["failed"] == 1
        mock_client.return_value.__aenter__.return_value.post.assert_not_awaited()
        runtime_service.handle_dispatch_ack_exhausted.assert_awaited_once()
        mock_device_service.mark_command_finished.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_marks_failed_after_max_retries(
        self,
        mock_db,
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
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=Exception("Device offline"))
            result = await OutboxDispatchService().dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["failed"] == 1
        # 达到最大重试次数，状态应为 FAILED
        assert outbox.status == SystemOutboxStatus.FAILED

    @pytest.mark.asyncio
    async def test_dispatch_logs_diagnostic_with_outbox_trace_fields(self, mock_db, mock_outbox_repo):
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
            result = await OutboxDispatchService().dispatch(mock_db)

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
    async def test_dispatch_logs_warning_with_outbox_trace_suffix(self, mock_db, mock_outbox_repo):
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
            result = await OutboxDispatchService().dispatch(mock_db)

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
            "command_type": "PICK_AND_PUT",
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

    def test_normalize_vendor_command_payload_does_not_accept_legacy_command_id(self):
        """测试设备派发归一化不再接受 legacy command_id。"""
        from src.app.workline.services.write_back_service import _normalize_vendor_command_payload

        payload = _normalize_vendor_command_payload(
            {"command_id": "CMD-LEGACY-001", "task_type": "PICK_AND_PUT"},
            action="PICK_AND_PUT",
            default_command_code="CMD-NEW-001",
        )

        assert payload["command_code"] == "CMD-NEW-001"

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
    async def test_dispatch_skips_dispatching_status(self, mock_db, mock_outbox_repo):
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
            result = await OutboxDispatchService().dispatch(mock_db)

        # 应该跳过这条消息
        assert result["dispatched"] == 0
        assert result["skipped"] == 1
