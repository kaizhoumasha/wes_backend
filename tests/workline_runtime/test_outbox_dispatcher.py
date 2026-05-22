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

from src.app.device.models.command import CommandStatus
from src.app.device.models.device import DeviceStatus
from src.app.workline.models.outbox import DispatchType, OutboxStatus, TargetType


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
        self.finished_at = None
        self.blocked_by_reconciliation_session_id = None
        self.blocked_device_id = None
        self.blocked_workline_id = None
        self.blocked_reason = None


class TestOutboxDispatcher:
    """OutboxDispatcher 任务测试"""

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
    async def test_dispatch_blocks_estopped_workline_before_side_effect(self, mock_db, mock_outbox_repo):
        """WorkLine 已急停时，outbox 派发应在真实副作用前被阻断。"""
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=1,
            workline_id=7,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="SCANNER_001",
            payload_json={"command": "SCAN"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]
        mock_outbox_repo.mark_as_dispatching.return_value = outbox
        safety_service = MagicMock()
        safety_service.assert_accepting_work = AsyncMock(side_effect=WorkLineSafetyBlocked("WORKLINE_ESTOPPED"))

        with (
            patch(
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()) as record_diagnostic,
            patch(
                "src.celery_app.tasks.workline.OutboxDispatcher._dispatch_single", new=AsyncMock()
            ) as dispatch_single,
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        safety_service.assert_accepting_work.assert_awaited_once_with(mock_db, workline_id=7)
        mock_outbox_repo.mark_as_blocked_by_workline_estop.assert_awaited_once_with(mock_db, 1)
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        record_diagnostic.assert_awaited_once()
        dispatch_single.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_parks_outbox_when_workline_reconciling(self, mock_db, mock_outbox_repo):
        """WorkLine 对账中时，待派发 outbox 进入 BLOCKED_RESOURCE，不走普通失败重试。"""
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=2,
            workline_id=7,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
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
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()) as record_diagnostic,
            patch(
                "src.celery_app.tasks.workline.OutboxDispatcher._dispatch_single", new=AsyncMock()
            ) as dispatch_single,
        ):
            result = await dispatch_outbox._dispatch(mock_db)

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
        record_diagnostic.assert_awaited_once()
        dispatch_single.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_repairs_orphaned_device_busy_dispatching_when_device_is_idle(
        self,
        mock_db,
        mock_outbox_repo,
    ):
        """设备忙导致的残留 DISPATCHING 应在设备空闲时自动回到 NEW 队列。"""
        from src.app.workline.models.dispatch_attempt import DispatchAttemptStatus
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=864,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ARM03",
            status=OutboxStatus.DISPATCHING,
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
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.workline.repositories.dispatch_attempt_repository.workline_dispatch_attempt_repository",
                attempt_repo,
            ),
            patch("src.app.device.repositories.device_repository.device_repository", device_repo),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

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
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=864,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ARM03",
            status=OutboxStatus.BLOCKED_RESOURCE,
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
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=command_repo,
            ),
            patch("src.app.device.repositories.device_repository.device_repository", device_repo),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 0
        mock_outbox_repo.mark_blocked_device_busy_as_sent.assert_awaited_once_with(mock_db, 864)
        mock_outbox_repo.get_pending_messages.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_releases_claim_transaction_before_side_effect(self, mock_db, mock_outbox_repo):
        """Outbox 领取、账本和最终 guard 都应在物理副作用前短事务提交。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=1,
            workline_id=7,
            dispatch_type=DispatchType.EXTERNAL_HTTP,
            target_type=TargetType.HTTP_ENDPOINT,
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
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch(
                "src.celery_app.tasks.workline.OutboxDispatcher._dispatch_single",
                new=AsyncMock(side_effect=fake_dispatch_single),
            ),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["success"] == 1
        assert safety_service.assert_accepting_work.await_count == 3

    @pytest.mark.asyncio
    async def test_dispatch_success_is_fenced_when_outbox_changes_during_side_effect(self, mock_db, mock_outbox_repo):
        """物理派发返回后若 timeout 已接管 outbox，不应把安全终态改回 SENT。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=4,
            workline_id=7,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
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
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
                return_value=mock_outbox_repo,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch(
                "src.app.workline.services.dispatch_attempt_service.workline_dispatch_attempt_service",
                attempt_service,
            ),
            patch(
                "src.celery_app.tasks.workline.OutboxDispatcher._dispatch_single",
                new=AsyncMock(side_effect=fake_dispatch_single),
            ),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

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
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=3,
            workline_id=7,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
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
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
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
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()) as record_diagnostic,
            patch(
                "src.celery_app.tasks.workline.OutboxDispatcher._dispatch_single", new=AsyncMock()
            ) as dispatch_single,
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 1
        assert safety_service.assert_accepting_work.await_count == 3
        runtime_service.park_outbox_for_reconciliation.assert_awaited_once()
        attempt_service.finalize_attempt_record.assert_awaited_once()
        record_diagnostic.assert_awaited_once()
        dispatch_single.assert_not_awaited()

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
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()) as record_diagnostic,
        ):
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=Exception("Device offline"))
            result = await dispatch_outbox._dispatch(mock_db)

        # 应该记录失败并设置重试
        assert result["dispatched"] == 1
        assert result["success"] == 0
        assert result["failed"] == 1
        assert outbox.attempt_count == 1
        record_diagnostic.assert_awaited_once()
        assert record_diagnostic.await_args.kwargs["error_code"].value == "OUTBOX_ACK_TIMEOUT"

    @pytest.mark.asyncio
    async def test_dispatch_enters_runtime_reconciliation_when_outbox_exhausted(
        self,
        mock_db,
        mock_outbox_repo,
    ):
        """Outbox 永久失败后进入通信 ACK 对账隔离。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=21,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            session_id=530,
            attempt_count=2,
            payload_json={"command_code": "CMD-DISPATCH-FAILED-001", "task_type": "PICK_AND_PUT"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        async def mock_mark_failed(_db, _outbox_id, error, _max_retries):
            outbox.attempt_count += 1
            outbox.last_error = error
            outbox.status = OutboxStatus.FAILED
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
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
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
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=400, text="Unsupported command")
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await dispatch_outbox._dispatch(mock_db)

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
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=22,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_code="ARM01",
            session_id=530,
            attempt_count=2,
            payload_json={"command_code": "CMD-RESERVED-FAILED-001", "task_type": "PICK_AND_PUT"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [outbox]

        async def mock_mark_failed(_db, _outbox_id, error, _max_retries):
            outbox.attempt_count += 1
            outbox.last_error = error
            outbox.status = OutboxStatus.FAILED
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
                "src.app.workline.repositories.outbox_repository.WorklineOutboxRepository",
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
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_client.return_value.__aenter__.return_value.post = AsyncMock()
            result = await dispatch_outbox._dispatch(mock_db)

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
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
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
            patch("src.celery_app.tasks.workline.workline_diagnostic_service.record_event", new=AsyncMock()),
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
            patch("src.celery_app.tasks.workline.workline_diagnostic_service.record_event", new=AsyncMock()),
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

        with patch("src.core.mixins.timestamp.TimestampMixin._get_now", return_value=fixed_now):
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

    def test_build_outbox_payload_wraps_device_command_params(self):
        """测试 DeviceCommand.params 只作为派发包络 params。"""
        from src.celery_app.tasks.workline import _build_outbox_payload

        command = MagicMock()
        command.command_code = "CMD-001"
        command.task_type = "PICK_AND_PLACE"
        command.priority = 5
        command.timeout_ms = 300000
        command.params = {
            "source": {"location_type": "INPUT_PLATFORM", "location_id": "STATION_INPUT1"},
            "target": {"location_type": "NG_PLATFORM", "location_id": "STATION_NG_PLATFORM1"},
        }

        payload = _build_outbox_payload(command)

        assert "source" not in payload
        assert "target" not in payload
        assert payload["command_code"] == "CMD-001"
        assert payload["task_type"] == "PICK_AND_PLACE"
        assert payload["priority"] == 5
        assert payload["timeout"] == 300000
        assert payload["params"] == {
            "source": {"location_type": "INPUT_PLATFORM", "location_id": "STATION_INPUT1"},
            "target": {"location_type": "NG_PLATFORM", "location_id": "STATION_NG_PLATFORM1"},
        }
        assert isinstance(payload["timestamp"], int)

    def test_build_outbox_payload_injects_device_code_into_vendor_payload(self):
        """测试发往设备的 payload 会补齐 target device_code。"""
        from src.celery_app.tasks.workline import _build_outbox_payload

        command = MagicMock()
        command.command_code = "CMD-VENDOR-DEVICE-001"
        command.task_type = "PICK_AND_PLACE"
        command.priority = 5
        command.timeout_ms = 300000
        command.params = {
            "barcode": "PKG-001",
        }

        payload = _build_outbox_payload(command, device_code="ARM03")

        assert payload["device_code"] == "ARM03"
        assert payload["command_code"] == "CMD-VENDOR-DEVICE-001"
        assert payload["params"] == {"barcode": "PKG-001"}

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

    def test_normalize_vendor_command_payload_wraps_business_fields_into_params(self):
        """测试插件业务参数会严格收口到 params 中。"""
        from src.celery_app.tasks.workline import _normalize_vendor_command_payload

        payload = _normalize_vendor_command_payload(
            {
                "pkg_id": "PKG001",
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
                "pkg_id": "PKG001",
                "target_type": "BIN",
                "target_loc": "BIN_201",
            },
            "timestamp": payload["timestamp"],
        }
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

        session = SimpleNamespace(plugin_key="smt_classifier", contract_version="legacy-0.9")
        workline = SimpleNamespace(plugin_key="smt_classifier", contract_version="wl-2.0")

        with patch(
            "src.celery_app.tasks.workline.get_plugin_contract_version",
            return_value="registry-1.0",
        ):
            _sync_session_contract_snapshot(
                session,
                workline=workline,
            )

        assert session.contract_version == "wl-2.0"

    def test_sync_session_contract_snapshot_falls_back_to_registry(self):
        """测试 workline.contract_version 缺失时回退 registry。"""
        from src.celery_app.tasks.workline import _sync_session_contract_snapshot

        session = SimpleNamespace(plugin_key="smt_classifier", contract_version=None)
        workline = SimpleNamespace(plugin_key="smt_classifier", contract_version=None)

        with patch(
            "src.celery_app.tasks.workline.get_plugin_contract_version",
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
        db = AsyncMock()
        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=execute_result)
        return db

    @pytest.mark.parametrize(
        ("device_status", "current_command_id", "expected_code"),
        [
            (DeviceStatus.RUNNING, 1001, "DEVICE_BUSY"),
            (DeviceStatus.ERROR, None, "DEVICE_ERROR_STATE"),
            (DeviceStatus.OFFLINE, None, "DEVICE_OFFLINE"),
            (DeviceStatus.MAINTENANCE, None, "DEVICE_MAINTENANCE_MODE"),
        ],
    )
    def test_device_command_governance_rejects_non_idle_device_status(
        self,
        device_status: DeviceStatus,
        current_command_id: int | None,
        expected_code: str,
    ):
        """设备非 IDLE 时，运行时必须在创建/派发前拒绝新命令。"""
        from src.celery_app.tasks.workline import _DeviceCommandGovernanceError, _enforce_device_command_governance

        device = _mock_device_record(
            device_code="ROBOT_001",
            capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
            maintenance_mode=False,
            device_status=device_status,
            current_command_id=current_command_id,
        )

        with pytest.raises(_DeviceCommandGovernanceError) as exc_info:
            _enforce_device_command_governance(
                device,
                command_type="PICK_AND_PUT",
                stage_label="命令派发",
            )

        assert exc_info.value.code == expected_code

    def test_device_command_governance_rejects_stale_current_command_on_idle_device(self):
        """即使状态被误置为 IDLE，仍不能给已有 current_command_id 的设备继续派发。"""
        from src.celery_app.tasks.workline import _DeviceCommandGovernanceError, _enforce_device_command_governance

        device = _mock_device_record(
            device_code="ROBOT_001",
            capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
            maintenance_mode=False,
            device_status=DeviceStatus.IDLE,
            current_command_id=1001,
        )

        with pytest.raises(_DeviceCommandGovernanceError) as exc_info:
            _enforce_device_command_governance(
                device,
                command_type="PICK_AND_PUT",
                stage_label="命令派发",
            )

        assert exc_info.value.code == "DEVICE_BUSY"

    def test_device_command_governance_rejects_reserved_current_command_dispatch(self):
        """current_command_id 只代表硬件侧当前任务，完成回调前不允许下发同一设备的后续命令。"""
        from src.celery_app.tasks.workline import _DeviceCommandGovernanceError, _enforce_device_command_governance

        device = _mock_device_record(
            device_code="ROBOT_001",
            capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
            maintenance_mode=False,
            device_status=DeviceStatus.RUNNING,
            current_command_id=1001,
        )

        with pytest.raises(_DeviceCommandGovernanceError) as exc_info:
            _enforce_device_command_governance(
                device,
                command_type="PICK_AND_PUT",
                stage_label="命令派发",
            )

        assert exc_info.value.code == "DEVICE_BUSY"

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
    async def test_dispatch_device_command_locks_device_row_before_http(self, mock_db):
        """真实设备派发前必须锁定设备行，避免多 worker 并发下发同一设备命令。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ARM01",
            payload_json={"command_code": "CMD-LOCK-001", "task_type": "PICK_AND_PUT"},
        )
        device = _mock_device_record(
            id=7,
            device_code="ARM01",
            callback_path=None,
            capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
            maintenance_mode=False,
            device_status=DeviceStatus.IDLE,
            current_command_id=None,
        )
        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code_for_update = AsyncMock(return_value=device)
        mock_device_repo.get_by_device_code = AsyncMock(side_effect=AssertionError("dispatch must lock device row"))
        mock_command_repo = MagicMock()
        mock_command_repo.get_by_command_code = AsyncMock(return_value=_mock_command_record(id=1001, device_id=7))
        mock_device_service = SimpleNamespace(mark_command_dispatched=AsyncMock())
        runtime_service = SimpleNamespace(activate_execution_deadline_after_ack=AsyncMock())

        with (
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.device.services.device_service",
                mock_device_service,
            ),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await dispatch_outbox._dispatch_single(mock_db, outbox)

        assert result is True
        mock_device_repo.get_by_device_code_for_update.assert_awaited_once_with(mock_db, "ARM01")
        mock_device_repo.get_by_device_code.assert_not_awaited()
        mock_client.return_value.__aenter__.return_value.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_device_command_marks_device_running_after_ack(self, mock_db):
        """设备 ACK 成功后，WES 侧设备状态必须进入 RUNNING。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ARM01",
            payload_json={"command_code": "CMD-RUN-001", "task_type": "PICK_AND_PUT"},
        )

        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code = AsyncMock(
            return_value=SimpleNamespace(
                id=7,
                host="127.0.0.1",
                port=8006,
                timeout=10000,
                protocol="HTTP",
                callback_path=None,
                capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
                maintenance_mode=False,
                device_code="ARM01",
            )
        )
        mock_command_repo = MagicMock()
        mock_command_repo.get_by_command_code = AsyncMock(return_value=_mock_command_record(id=1001, device_id=7))
        mock_device_service = SimpleNamespace(mark_command_dispatched=AsyncMock())
        runtime_service = SimpleNamespace(activate_execution_deadline_after_ack=AsyncMock())

        with (
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.device.services.device_service",
                mock_device_service,
            ),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await dispatch_outbox._dispatch_single(mock_db, outbox)

        assert result is True
        mock_device_service.mark_command_dispatched.assert_awaited_once_with(
            mock_db,
            device_id=7,
            command_id=1001,
            auto_commit=False,
        )

    @pytest.mark.asyncio
    async def test_dispatch_device_command_does_not_reopen_device_when_fast_callback_already_completed(self, mock_db):
        """设备结果回调快于 ACK 投影时，不允许 dispatcher 把设备覆盖回 RUNNING。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ARM01",
            payload_json={"command_code": "CMD-FAST-CALLBACK-001", "task_type": "PICK_AND_PUT"},
        )
        command = _mock_command_record(id=1001, device_id=7, status=CommandStatus.PENDING)

        async def refresh_command(obj):
            obj.status = CommandStatus.COMPLETED

        mock_db.refresh = AsyncMock(side_effect=refresh_command)
        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code_for_update = AsyncMock(
            return_value=_mock_device_record(
                id=7,
                callback_path=None,
                capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
                maintenance_mode=False,
                device_code="ARM01",
                device_status=DeviceStatus.IDLE,
                current_command_id=None,
            )
        )
        mock_command_repo = MagicMock()
        mock_command_repo.get_by_command_code = AsyncMock(return_value=command)
        mock_device_service = SimpleNamespace(mark_command_dispatched=AsyncMock())
        runtime_service = SimpleNamespace(activate_execution_deadline_after_ack=AsyncMock())

        with (
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.device.services.device_service",
                mock_device_service,
            ),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await dispatch_outbox._dispatch_single(mock_db, outbox)

        assert result is True
        mock_db.refresh.assert_awaited_once_with(command)
        mock_device_service.mark_command_dispatched.assert_not_awaited()
        runtime_service.activate_execution_deadline_after_ack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_blocks_next_same_device_command_until_current_finishes(self, mock_db):
        """同一设备上一条硬件任务完成回调前，后续 outbox 不能进入设备侧。"""
        from src.celery_app.tasks.workline import dispatch_outbox

        mock_outbox_repo = MagicMock()
        mock_outbox_repo.get_pending_messages = AsyncMock()
        mock_outbox_repo.mark_as_sent = AsyncMock(return_value=MagicMock())
        first_outbox = MockOutbox(
            outbox_id=31,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ARM01",
            payload_json={"command_code": "CMD-QUEUE-001", "task_type": "PICK_AND_PUT"},
        )
        second_outbox = MockOutbox(
            outbox_id=32,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ARM01",
            payload_json={"command_code": "CMD-QUEUE-002", "task_type": "PICK_AND_PUT"},
        )
        mock_outbox_repo.get_pending_messages.return_value = [first_outbox, second_outbox]
        mock_outbox_repo.mark_as_dispatching = AsyncMock(side_effect=[first_outbox, second_outbox])

        async def mock_mark_blocked(_db, outbox_id, *, blocked_device_id, blocked_workline_id, reason, last_error):
            blocked_outbox = second_outbox if outbox_id == 32 else first_outbox
            blocked_outbox.status = OutboxStatus.BLOCKED_RESOURCE
            blocked_outbox.blocked_device_id = blocked_device_id
            blocked_outbox.blocked_workline_id = blocked_workline_id
            blocked_outbox.blocked_reason = reason
            blocked_outbox.last_error = last_error
            return blocked_outbox

        mock_outbox_repo.mark_as_blocked_by_device_busy = AsyncMock(side_effect=mock_mark_blocked)
        mock_outbox_repo.mark_as_failed = AsyncMock()

        device = _mock_device_record(
            id=7,
            device_code="ARM01",
            callback_path=None,
            capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
            maintenance_mode=False,
            device_status=DeviceStatus.IDLE,
            current_command_id=None,
        )
        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code = AsyncMock(return_value=device)
        mock_command_repo = MagicMock()
        mock_command_repo.get_by_command_code = AsyncMock(return_value=_mock_command_record(id=1001, device_id=7))

        async def mark_dispatched(_db, *, device_id, command_id, auto_commit):
            assert device_id == 7
            assert command_id == 1001
            assert auto_commit is False
            device.device_status = DeviceStatus.RUNNING
            device.current_command_id = command_id
            return device

        mock_device_service = SimpleNamespace(mark_command_dispatched=AsyncMock(side_effect=mark_dispatched))
        runtime_service = SimpleNamespace(activate_execution_deadline_after_ack=AsyncMock())

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
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.device.services.device_service",
                mock_device_service,
            ),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
            ),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_response = MagicMock(status_code=200)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 2
        assert result["success"] == 1
        assert result["failed"] == 0
        assert result["skipped"] == 1
        assert second_outbox.status == OutboxStatus.BLOCKED_RESOURCE
        assert second_outbox.blocked_device_id == 7
        assert second_outbox.blocked_reason == "DEVICE_BUSY"
        assert second_outbox.last_error is not None
        assert "正在执行任务" in second_outbox.last_error
        assert mock_client.return_value.__aenter__.return_value.post.await_count == 1
        mock_device_service.mark_command_dispatched.assert_awaited_once()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_device_command_routes_to_sandbox_without_payload_flag(self, mock_db):
        """SIMULATION Session 的设备指令应派发到沙箱出口，且不改写 payload。"""
        from src.app.workline.models.session import RunMode
        from src.celery_app.tasks.workline import OutboxDispatcher

        payload = {"command_code": "CMD-SIM-001", "task_type": "PICK_AND_PUT", "params": {"pkg_id": "PKG001"}}
        outbox = MockOutbox(
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ROBOT_001",
            payload_json=dict(payload),
        )
        outbox.session = SimpleNamespace(run_mode=RunMode.SIMULATION)
        device = _mock_device_record(
            id=7,
            device_code="ROBOT_001",
            capabilities_json={"supports_command_types": ["PICK_AND_PUT"]},
            maintenance_mode=False,
            device_status=DeviceStatus.IDLE,
            current_command_id=None,
        )
        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code_for_update = AsyncMock(return_value=device)
        mock_command_repo = MagicMock()
        command = _mock_command_record(id=1001, device_id=7)
        mock_command_repo.get_by_command_code = AsyncMock(return_value=command)
        mock_device_service = SimpleNamespace(mark_command_dispatched=AsyncMock())

        with (
            patch.object(
                OutboxDispatcher,
                "_dispatch_device_command",
                new=AsyncMock(return_value=False),
            ) as live_dispatch,
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.device.services.device_service",
                mock_device_service,
            ),
        ):
            result = await OutboxDispatcher._dispatch_single(mock_db, outbox)

        assert result is True
        assert outbox.payload_json == payload
        assert "sandbox" not in outbox.payload_json
        live_dispatch.assert_not_awaited()
        mock_device_repo.get_by_device_code_for_update.assert_awaited_once_with(mock_db, "ROBOT_001")
        assert command.status == CommandStatus.SENT
        assert command.sent_at is not None
        mock_device_service.mark_command_dispatched.assert_awaited_once_with(
            mock_db,
            device_id=7,
            command_id=1001,
            auto_commit=False,
        )

    @pytest.mark.asyncio
    async def test_sandbox_dispatch_blocks_next_same_device_command_until_result(self, mock_db):
        """沙箱待回传命令也必须占用设备，避免同设备假并发。"""
        from src.app.workline.models.session import RunMode
        from src.celery_app.tasks.workline import dispatch_outbox

        mock_outbox_repo = MagicMock()
        first_outbox = MockOutbox(
            outbox_id=31,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ARM03",
            payload_json={"command_code": "CMD-SANDBOX-001", "task_type": "MEASUREMENT_REEL"},
        )
        first_outbox.session = SimpleNamespace(run_mode=RunMode.SIMULATION)
        second_outbox = MockOutbox(
            outbox_id=32,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ARM03",
            payload_json={"command_code": "CMD-SANDBOX-002", "task_type": "MEASUREMENT_REEL"},
        )
        second_outbox.session = SimpleNamespace(run_mode=RunMode.SIMULATION)
        mock_outbox_repo.get_pending_messages = AsyncMock(return_value=[first_outbox, second_outbox])
        mock_outbox_repo.mark_as_dispatching = AsyncMock(side_effect=[first_outbox, second_outbox])
        mock_outbox_repo.mark_as_sent = AsyncMock(return_value=MagicMock())

        async def mock_mark_blocked(_db, outbox_id, *, blocked_device_id, blocked_workline_id, reason, last_error):
            blocked_outbox = second_outbox if outbox_id == 32 else first_outbox
            blocked_outbox.status = OutboxStatus.BLOCKED_RESOURCE
            blocked_outbox.blocked_device_id = blocked_device_id
            blocked_outbox.blocked_workline_id = blocked_workline_id
            blocked_outbox.blocked_reason = reason
            blocked_outbox.last_error = last_error
            return blocked_outbox

        mock_outbox_repo.mark_as_blocked_by_device_busy = AsyncMock(side_effect=mock_mark_blocked)
        mock_outbox_repo.mark_as_failed = AsyncMock()
        device = _mock_device_record(
            id=7,
            device_code="ARM03",
            capabilities_json={"supports_command_types": ["MEASUREMENT_REEL"]},
            maintenance_mode=False,
            device_status=DeviceStatus.IDLE,
            current_command_id=None,
        )
        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code_for_update = AsyncMock(return_value=device)
        mock_command_repo = MagicMock()
        mock_command_repo.get_by_command_code = AsyncMock(
            side_effect=[
                _mock_command_record(id=1001, device_id=7),
                _mock_command_record(id=1002, device_id=7),
            ]
        )

        async def mark_dispatched(_db, *, device_id, command_id, auto_commit):
            assert device_id == 7
            assert command_id == 1001
            assert auto_commit is False
            device.device_status = DeviceStatus.RUNNING
            device.current_command_id = command_id
            return device

        mock_device_service = SimpleNamespace(mark_command_dispatched=AsyncMock(side_effect=mark_dispatched))

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
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.device.services.device_service",
                mock_device_service,
            ),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 2
        assert result["success"] == 1
        assert result["failed"] == 0
        assert result["skipped"] == 1
        assert second_outbox.status == OutboxStatus.BLOCKED_RESOURCE
        assert second_outbox.blocked_device_id == 7
        assert second_outbox.blocked_reason == "DEVICE_BUSY"
        assert second_outbox.last_error is not None
        assert "正在执行任务" in second_outbox.last_error
        mock_device_service.mark_command_dispatched.assert_awaited_once()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sandbox_dispatch_treats_same_running_command_as_already_sent(self, mock_db):
        """同一沙箱命令已占用设备运行态时，重复派发应幂等收敛为 SENT。"""
        from src.app.workline.models.session import RunMode
        from src.celery_app.tasks.workline import dispatch_outbox

        outbox = MockOutbox(
            outbox_id=864,
            dispatch_type=DispatchType.DEVICE_COMMAND,
            target_type=TargetType.DEVICE,
            target_code="ARM03",
            session_id=555,
            workline_id=45,
            payload_json={"command_code": "CMD-20260509-MEASUREMENT_REEL-4478E8D3", "task_type": "MEASUREMENT_REEL"},
        )
        outbox.session = SimpleNamespace(run_mode=RunMode.SIMULATION)
        mock_outbox_repo = MagicMock()
        mock_outbox_repo.get_pending_messages = AsyncMock(return_value=[outbox])
        mock_outbox_repo.mark_as_dispatching = AsyncMock(return_value=outbox)
        mock_outbox_repo.mark_as_sent = AsyncMock(return_value=outbox)
        mock_outbox_repo.mark_as_blocked_by_device_busy = AsyncMock()
        mock_outbox_repo.mark_as_failed = AsyncMock()

        device = _mock_device_record(
            id=39,
            device_code="ARM03",
            capabilities_json={"supports_command_types": ["MEASUREMENT_REEL"]},
            maintenance_mode=False,
            device_status=DeviceStatus.RUNNING,
            current_command_id=883,
        )
        mock_device_repo = MagicMock()
        mock_device_repo.get_by_device_code_for_update = AsyncMock(return_value=device)
        mock_command_repo = MagicMock()
        mock_command_repo.get_by_command_code = AsyncMock(
            return_value=_mock_command_record(
                id=883,
                device_id=39,
                session_id="SES-555",
                session_id_int=555,
                status=CommandStatus.SENT,
            )
        )
        mock_device_service = SimpleNamespace(mark_command_dispatched=AsyncMock())
        safety_service = SimpleNamespace(assert_accepting_work=AsyncMock())

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
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.device.services.device_service",
                mock_device_service,
            ),
            patch("src.app.workline.services.safety_service.workline_safety_service", safety_service),
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
        ):
            result = await dispatch_outbox._dispatch(mock_db)

        assert result["dispatched"] == 1
        assert result["success"] == 1
        assert result["failed"] == 0
        assert result["skipped"] == 0
        mock_outbox_repo.mark_as_sent.assert_awaited_once_with(mock_db, 864)
        mock_outbox_repo.mark_as_blocked_by_device_busy.assert_not_awaited()
        mock_outbox_repo.mark_as_failed.assert_not_awaited()
        mock_device_service.mark_command_dispatched.assert_not_awaited()
        assert safety_service.assert_accepting_work.await_count == 3

    @pytest.mark.asyncio
    async def test_dispatch_external_http_routes_to_sandbox(self, mock_db):
        """SIMULATION Session 的外部 HTTP 请求应进入沙箱出口。"""
        from src.app.workline.models.session import RunMode
        from src.celery_app.tasks.workline import OutboxDispatcher

        outbox = MockOutbox(
            dispatch_type=DispatchType.EXTERNAL_HTTP,
            target_type=TargetType.HTTP_ENDPOINT,
            target_code="https://example.test/callback",
            payload_json={"event": "ready", "data": {"pkg_id": "PKG001"}},
        )
        outbox.session = SimpleNamespace(run_mode=RunMode.SIMULATION)

        with patch.object(
            OutboxDispatcher,
            "_dispatch_external_http",
            new=AsyncMock(return_value=False),
        ) as live_dispatch:
            result = await OutboxDispatcher._dispatch_single(mock_db, outbox)

        assert result is True
        live_dispatch.assert_not_awaited()

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
                id=7,
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
        mock_command_repo = MagicMock()
        mock_command_repo.get_by_command_code = AsyncMock(return_value=_mock_command_record(id=1001, device_id=7))
        mock_device_service = SimpleNamespace(mark_command_dispatched=AsyncMock())
        runtime_service = SimpleNamespace(activate_execution_deadline_after_ack=AsyncMock())

        with (
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.device.services.device_service",
                mock_device_service,
            ),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
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
                id=7,
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
        mock_command_repo = MagicMock()
        mock_command_repo.get_by_command_code = AsyncMock(return_value=_mock_command_record(id=1002, device_id=7))
        mock_device_service = SimpleNamespace(mark_command_dispatched=AsyncMock())
        runtime_service = SimpleNamespace(activate_execution_deadline_after_ack=AsyncMock())

        with (
            patch(
                "src.app.device.repositories.device_repository.device_repository",
                mock_device_repo,
            ),
            patch(
                "src.app.device.repositories.command_repository.DeviceCommandRepository",
                return_value=mock_command_repo,
            ),
            patch(
                "src.app.device.services.device_service",
                mock_device_service,
            ),
            patch(
                "src.app.workline.services.runtime_reconciliation_service.workline_runtime_reconciliation_service",
                runtime_service,
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
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
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
            patch("src.celery_app.tasks.workline._record_diagnostic", new=AsyncMock()),
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
