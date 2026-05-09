"""
OrchestratorService 单元测试

测试编排器核心流程：
- Phase 2 默认行为（无状态机）
- 分布式锁获取与释放
- 插件调用与结果处理
- PluginResult 各字段处理（transition, commands, wait, failure, complete）

设计参考: 设计文档 phase2-orchestrator
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.models.inbox import InboxKind
from src.workline_runtime.services import WorklineRuntimeServices
from src.workline_runtime.types import (
    BusinessDecisionIntent,
    CommandIntent,
    FailureIntent,
    PluginResult,
    WaitIntent,
)


def make_noop_lock():
    """创建空操作锁上下文管理器"""

    @asynccontextmanager
    async def noop_lock():
        yield

    return noop_lock()


def make_failing_lock(error: Exception):
    """创建失败的锁上下文管理器"""

    @asynccontextmanager
    async def failing_lock():
        raise error
        yield  # never reached

    return failing_lock()


class TestOrchestratorServicePhase2:
    """OrchestratorService Phase 2 行为测试"""

    @pytest.fixture(autouse=True)
    def allow_null_plugin(self):
        """默认允许 NullPlugin（用于 Phase 2 测试）"""
        from src.workline_runtime.orchestrator import set_allow_null_plugin

        set_allow_null_plugin(True)
        yield
        set_allow_null_plugin(False)

    @pytest.fixture
    def mock_session(self):
        """创建模拟 Session"""
        session = MagicMock()
        session.id = 12345
        session.status = "RUNNING"
        session.context_json = {"key": "value"}
        return session

    @pytest.fixture
    def mock_workline(self):
        """创建模拟 WorkLine"""
        workline = MagicMock()
        workline.id = 1
        workline.plugin_class = None  # Phase 2: 无插件
        workline.state_machine_class = None  # Phase 2: 无状态机
        return workline

    @pytest.fixture
    def mock_inbox(self):
        """创建模拟 Inbox"""
        inbox = MagicMock()
        inbox.id = 100
        inbox.kind = InboxKind.DEVICE_EVENT
        inbox.payload_json = {"message_type": "DEVICE_EVENT", "event": "scan_complete"}
        return inbox

    @pytest.fixture
    def mock_devices_by_role(self):
        """创建模拟设备映射"""
        return {
            "SCANNER": [MagicMock(id=1, name="Scanner1")],
            "CONVEYOR": [MagicMock(id=2, name="Conveyor1")],
        }

    @pytest.fixture
    def mock_services(self):
        """创建模拟领域服务容器"""
        return WorklineRuntimeServices()

    @pytest.fixture
    def orchestrator(self):
        """创建 OrchestratorService 实例（带 noop lock）"""
        from src.workline_runtime.orchestrator import OrchestratorService

        return OrchestratorService(lock_provider=lambda key: make_noop_lock())

    @pytest.mark.asyncio
    async def test_process_inbox_with_null_plugin(
        self,
        orchestrator,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """测试无插件时使用 NullPlugin 处理"""
        result = await orchestrator.process_inbox(
            session=mock_session,
            workline=mock_workline,
            inbox=mock_inbox,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            trace_id="test-trace-id",
        )

        assert result is not None
        assert result.success is True

    @pytest.mark.asyncio
    async def test_manual_operation_uses_runtime_transition_without_plugin_override(
        self,
        orchestrator,
        mock_devices_by_role,
        mock_services,
    ):
        """人工操作 inbox 应由 runtime 默认转成稳定 transition。"""
        from src.workline_plugins.smt_classifier.state_machine import SmtClassifierStateMachine

        session = MagicMock()
        session.id = 12345
        session.status = "MANUAL_HOLD"
        session.plugin_state = "WAITING_OUTPUT"
        session.context_json = {}

        workline = MagicMock()
        workline.id = 1
        workline.plugin_class = None
        workline.state_machine_class = SmtClassifierStateMachine

        inbox = MagicMock()
        inbox.id = 101
        inbox.kind = InboxKind.MANUAL_CANCEL
        inbox.payload_json = {
            "message_type": "MANUAL_OPERATION",
            "operation": "CANCEL",
            "operator_id": "ops-1",
            "reason": "现场确认取消",
        }

        result = await orchestrator.process_inbox(
            session=session,
            workline=workline,
            inbox=inbox,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            trace_id="trace-manual-001",
        )

        assert result.success is True
        assert result.transition == "manual_cancel"
        assert result.context_patch == {
            "cancelled": True,
            "cancel_reason": "现场确认取消",
            "manual_operator_id": "ops-1",
        }

    @pytest.mark.asyncio
    async def test_process_inbox_uses_single_session_lock(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """Phase 1: 单阶段锁获取 once per session。"""
        from src.workline_runtime.orchestrator import OrchestratorService

        lock_keys: list[str] = []

        @asynccontextmanager
        async def test_lock(key):
            lock_keys.append(key)
            yield

        orchestrator = OrchestratorService(lock_provider=test_lock)

        _ = await orchestrator.process_inbox(
            session=mock_session,
            workline=mock_workline,
            inbox=mock_inbox,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            trace_id="test-trace-id",
        )

        # Phase 1: 单阶段锁，只获取一次
        assert lock_keys == ["session:12345"]

    @pytest.mark.asyncio
    async def test_process_inbox_write_callback_runs_inside_session_lock(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """Phase 1: 写回发生在单阶段锁临界区内。"""
        from src.workline_runtime.orchestrator import OrchestratorService

        stages: list[str] = []

        @asynccontextmanager
        async def test_lock(key):
            stages.append(f"enter:{key}")
            try:
                yield
            finally:
                stages.append(f"exit:{key}")

        orchestrator = OrchestratorService(lock_provider=test_lock)
        write_callback = AsyncMock(side_effect=lambda result: stages.append(f"write_callback:{result.success}"))

        result = await orchestrator.process_inbox(
            session=mock_session,
            workline=mock_workline,
            inbox=mock_inbox,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            trace_id="test-trace-id",
            write_callback=write_callback,
        )

        assert result.success is True
        # Phase 1: 单阶段锁，callback 在锁内执行
        assert stages == [
            "enter:session:12345",
            "write_callback:True",
            "exit:session:12345",
        ]
        write_callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_same_session_messages_are_serialized(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """Phase 1: 同一 session 的消息串行处理（单阶段锁）。"""
        from src.workline_runtime.orchestrator import OrchestratorResult, OrchestratorService

        locks: dict[str, asyncio.Lock] = {}
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        @asynccontextmanager
        async def keyed_lock(key):
            lock = locks.setdefault(key, asyncio.Lock())
            _ = await lock.acquire()
            try:
                yield
            finally:
                lock.release()

        orchestrator = OrchestratorService(lock_provider=keyed_lock)

        async def fake_process(*args, **kwargs):
            first_entered.set()
            _ = await release_first.wait()
            return OrchestratorResult(success=True)

        with patch.object(orchestrator, "_process_read_phase", side_effect=fake_process):
            first_task = asyncio.create_task(
                orchestrator.process_inbox(
                    session=mock_session,
                    workline=mock_workline,
                    inbox=mock_inbox,
                    devices_by_role=mock_devices_by_role,
                    services=mock_services,
                    trace_id="trace-1",
                )
            )
            _ = await first_entered.wait()

            second_task = asyncio.create_task(
                orchestrator.process_inbox(
                    session=mock_session,
                    workline=mock_workline,
                    inbox=mock_inbox,
                    devices_by_role=mock_devices_by_role,
                    services=mock_services,
                    trace_id="trace-2",
                )
            )

            await asyncio.sleep(0.05)
            # 第二条消息在第一条完成前不应进入
            assert second_task.done() is False

            release_first.set()
            first_result = await first_task
            second_result = await second_task

        assert first_result.success is True
        assert second_result.success is True

    @pytest.mark.asyncio
    async def test_process_inbox_lock_failure(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """测试锁获取失败时的处理"""
        from src.workline_runtime.lock import LockAcquireError
        from src.workline_runtime.orchestrator import OrchestratorService

        @asynccontextmanager
        async def failing_lock(key):
            raise LockAcquireError("Lock timeout")
            yield  # never reached

        orchestrator = OrchestratorService(lock_provider=failing_lock)

        result = await orchestrator.process_inbox(
            session=mock_session,
            workline=mock_workline,
            inbox=mock_inbox,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            trace_id="test-trace-id",
        )

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_process_result_with_transition(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """测试处理包含 transition 的 PluginResult"""
        from src.workline_runtime.orchestrator import OrchestratorService

        orchestrator = OrchestratorService(lock_provider=lambda key: make_noop_lock())

        # 模拟插件返回 transition
        with patch.object(orchestrator, "_load_plugin") as mock_load_plugin:
            mock_plugin = MagicMock()
            mock_plugin.on_device_event = AsyncMock(return_value=PluginResult(transition="scan_ok"))
            mock_load_plugin.return_value = mock_plugin

            result = await orchestrator.process_inbox(
                session=mock_session,
                workline=mock_workline,
                inbox=mock_inbox,
                devices_by_role=mock_devices_by_role,
                services=mock_services,
                trace_id="test-trace-id",
            )

            assert result.success is True
            assert result.transition == "scan_ok"

    @pytest.mark.asyncio
    async def test_process_result_with_business_decisions(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """测试处理包含业务判定的 PluginResult。"""
        from src.workline_runtime.orchestrator import OrchestratorService

        decision = BusinessDecisionIntent(
            reason_code="SCAN_NG",
            message="扫码判定 NG",
            business_key="PKG-001",
        )
        orchestrator = OrchestratorService(lock_provider=lambda key: make_noop_lock())

        with patch.object(orchestrator, "_load_plugin") as mock_load_plugin:
            mock_plugin = MagicMock()
            mock_plugin.on_device_event = AsyncMock(return_value=PluginResult(business_decisions=[decision]))
            mock_load_plugin.return_value = mock_plugin

            result = await orchestrator.process_inbox(
                session=mock_session,
                workline=mock_workline,
                inbox=mock_inbox,
                devices_by_role=mock_devices_by_role,
                services=mock_services,
                trace_id="test-trace-id",
            )

            assert result.success is True
            assert result.business_decisions == [decision]
            assert result.failure is None

    @pytest.mark.asyncio
    async def test_process_result_with_commands(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """测试处理包含 commands 的 PluginResult"""
        from src.workline_runtime.orchestrator import OrchestratorService

        command = CommandIntent(
            target_device_id=1,
            action="PICK_AND_PUT",
            parameters={"position": "A1"},
        )

        orchestrator = OrchestratorService(lock_provider=lambda key: make_noop_lock())

        with patch.object(orchestrator, "_load_plugin") as mock_load_plugin:
            mock_plugin = MagicMock()
            mock_plugin.on_device_event = AsyncMock(return_value=PluginResult(commands=[command]))
            mock_load_plugin.return_value = mock_plugin

            result = await orchestrator.process_inbox(
                session=mock_session,
                workline=mock_workline,
                inbox=mock_inbox,
                devices_by_role=mock_devices_by_role,
                services=mock_services,
                trace_id="test-trace-id",
            )

            assert result.success is True
            assert result.commands is not None
            assert len(result.commands) == 1
            assert result.commands[0].target_device_id == 1

    @pytest.mark.asyncio
    async def test_process_result_with_wait(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """测试处理包含 wait 的 PluginResult"""
        from src.workline_runtime.orchestrator import OrchestratorService

        wait_intent = WaitIntent(
            wait_type="COMMAND_RESULT",
            wait_token="token-123",
            deadline_seconds=300,
        )

        orchestrator = OrchestratorService(lock_provider=lambda key: make_noop_lock())

        with patch.object(orchestrator, "_load_plugin") as mock_load_plugin:
            mock_plugin = MagicMock()
            mock_plugin.on_device_event = AsyncMock(return_value=PluginResult(wait=wait_intent))
            mock_load_plugin.return_value = mock_plugin

            result = await orchestrator.process_inbox(
                session=mock_session,
                workline=mock_workline,
                inbox=mock_inbox,
                devices_by_role=mock_devices_by_role,
                services=mock_services,
                trace_id="test-trace-id",
            )

            assert result.success is True
            assert result.wait is not None
            assert result.wait.wait_type == "COMMAND_RESULT"

    @pytest.mark.asyncio
    async def test_process_result_with_failure(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """测试处理包含 failure 的 PluginResult"""
        from src.workline_runtime.orchestrator import OrchestratorService

        failure = FailureIntent(
            domain="HARDWARE",
            code="DEVICE_OFFLINE",
            message="Scanner device is offline",
        )

        orchestrator = OrchestratorService(lock_provider=lambda key: make_noop_lock())

        with patch.object(orchestrator, "_load_plugin") as mock_load_plugin:
            mock_plugin = MagicMock()
            mock_plugin.on_device_event = AsyncMock(return_value=PluginResult(failure=failure))
            mock_load_plugin.return_value = mock_plugin

            result = await orchestrator.process_inbox(
                session=mock_session,
                workline=mock_workline,
                inbox=mock_inbox,
                devices_by_role=mock_devices_by_role,
                services=mock_services,
                trace_id="test-trace-id",
            )

            assert result.success is True
            assert result.failure is not None
            assert result.failure.domain == "HARDWARE"

    @pytest.mark.asyncio
    async def test_process_result_with_complete(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """测试处理包含 complete=True 的 PluginResult"""
        from src.workline_runtime.orchestrator import OrchestratorService

        orchestrator = OrchestratorService(lock_provider=lambda key: make_noop_lock())

        with patch.object(orchestrator, "_load_plugin") as mock_load_plugin:
            mock_plugin = MagicMock()
            mock_plugin.on_device_event = AsyncMock(return_value=PluginResult(complete=True))
            mock_load_plugin.return_value = mock_plugin

            result = await orchestrator.process_inbox(
                session=mock_session,
                workline=mock_workline,
                inbox=mock_inbox,
                devices_by_role=mock_devices_by_role,
                services=mock_services,
                trace_id="test-trace-id",
            )

            assert result.success is True
            assert result.complete is True

    @pytest.mark.asyncio
    async def test_process_result_with_context_patch(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """测试处理包含 context_patch 的 PluginResult"""
        from src.workline_runtime.orchestrator import OrchestratorService

        orchestrator = OrchestratorService(lock_provider=lambda key: make_noop_lock())

        with patch.object(orchestrator, "_load_plugin") as mock_load_plugin:
            mock_plugin = MagicMock()
            mock_plugin.on_device_event = AsyncMock(
                return_value=PluginResult(context_patch={"new_key": "new_value", "count": 42})
            )
            mock_load_plugin.return_value = mock_plugin

            result = await orchestrator.process_inbox(
                session=mock_session,
                workline=mock_workline,
                inbox=mock_inbox,
                devices_by_role=mock_devices_by_role,
                services=mock_services,
                trace_id="test-trace-id",
            )

            assert result.success is True
            assert result.context_patch == {"new_key": "new_value", "count": 42}


class TestOrchestratorServicePluginLoading:
    """OrchestratorService 插件加载测试"""

    @pytest.fixture(autouse=True)
    def allow_null_plugin(self):
        """默认允许 NullPlugin（用于测试）"""
        from src.workline_runtime.orchestrator import set_allow_null_plugin

        set_allow_null_plugin(True)
        yield
        set_allow_null_plugin(False)

    @pytest.fixture
    def orchestrator(self):
        """创建 OrchestratorService 实例"""
        from src.workline_runtime.orchestrator import OrchestratorService

        return OrchestratorService(lock_provider=lambda key: make_noop_lock())

    def test_load_plugin_returns_null_when_no_plugin_class(self, orchestrator):
        """测试无插件类时返回 NullPlugin（当允许时）"""
        from src.workline_runtime.null_plugin import NullPlugin

        plugin = orchestrator._load_plugin(None)
        assert isinstance(plugin, NullPlugin)

    def test_load_plugin_returns_custom_plugin(self, orchestrator):
        """测试加载自定义插件"""

        class CustomPlugin:
            plugin_key = "custom"

            async def on_device_event(self, ctx, inbox):
                return PluginResult()

        plugin = orchestrator._load_plugin(CustomPlugin)
        assert isinstance(plugin, CustomPlugin)


class TestOrchestratorServiceEdgeCases:
    """OrchestratorService 边界情况测试"""

    @pytest.fixture(autouse=True)
    def allow_null_plugin(self):
        """默认允许 NullPlugin（用于测试）"""
        from src.workline_runtime.orchestrator import set_allow_null_plugin

        set_allow_null_plugin(True)
        yield
        set_allow_null_plugin(False)

    @pytest.fixture
    def orchestrator(self):
        """创建 OrchestratorService 实例"""
        from src.workline_runtime.orchestrator import OrchestratorService

        return OrchestratorService(lock_provider=lambda key: make_noop_lock())

    @pytest.mark.asyncio
    async def test_plugin_exception_handling(self, orchestrator):
        """测试插件抛出异常时的处理"""
        mock_session = MagicMock()
        mock_session.id = 12345
        mock_session.status = "RUNNING"
        mock_session.context = {}

        mock_workline = MagicMock()
        mock_workline.plugin_class = None

        mock_inbox = MagicMock()
        mock_inbox.id = 100
        mock_inbox.kind = InboxKind.DEVICE_EVENT
        mock_inbox.payload_json = {"message_type": "DEVICE_EVENT"}

        with patch.object(orchestrator, "_load_plugin") as mock_load_plugin:
            mock_plugin = MagicMock()
            mock_plugin.on_device_event = AsyncMock(side_effect=ValueError("Plugin error"))
            mock_load_plugin.return_value = mock_plugin

            result = await orchestrator.process_inbox(
                session=mock_session,
                workline=mock_workline,
                inbox=mock_inbox,
                devices_by_role={},
                services=WorklineRuntimeServices(),
                trace_id="test-trace-id",
            )

            assert result.success is False
            assert result.error is not None

    @pytest.mark.asyncio
    async def test_empty_devices_by_role(self, orchestrator):
        """测试空设备映射"""
        mock_session = MagicMock()
        mock_session.id = 12345
        mock_session.status = "RUNNING"
        mock_session.context = {}

        mock_workline = MagicMock()
        mock_workline.plugin_class = None

        mock_inbox = MagicMock()
        mock_inbox.id = 100
        mock_inbox.kind = InboxKind.DEVICE_EVENT
        mock_inbox.payload_json = {"message_type": "DEVICE_EVENT"}

        result = await orchestrator.process_inbox(
            session=mock_session,
            workline=mock_workline,
            inbox=mock_inbox,
            devices_by_role={},  # 空设备映射
            services=WorklineRuntimeServices(),
            trace_id="test-trace-id",
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_process_inbox_without_lock_provider_fails_closed(self):
        """默认构造必须失败关闭，不能静默退化为无锁。"""
        from src.workline_runtime.orchestrator import OrchestratorService

        mock_session = MagicMock()
        mock_session.id = 12345
        mock_session.status = "RUNNING"
        mock_session.context = {}

        mock_workline = MagicMock()
        mock_workline.plugin_class = None

        mock_inbox = MagicMock()
        mock_inbox.id = 100
        mock_inbox.kind = InboxKind.DEVICE_EVENT
        mock_inbox.payload_json = {"message_type": "DEVICE_EVENT"}

        orchestrator = OrchestratorService()
        result = await orchestrator.process_inbox(
            session=mock_session,
            workline=mock_workline,
            inbox=mock_inbox,
            devices_by_role={},
            services=WorklineRuntimeServices(),
            trace_id="test-trace-id",
        )

        assert result.success is False
        assert result.error_code == "UNKNOWN"
        assert result.error_domain == "SYSTEM"

    def test_resolve_inbox_type_does_not_guess_command_result_from_device_event_payload(self, orchestrator):
        """DEVICE_EVENT 必须保持 DEVICE_EVENT，不允许根据 payload 猜成 COMMAND_RESULT。"""
        inbox = MagicMock()
        inbox.kind = InboxKind.DEVICE_EVENT
        inbox.payload_json = {
            "device_code": "ARM_01",
            "command_code": "CMD-001",
            "finish_time": 1702627250000,
        }

        assert orchestrator._resolve_inbox_type(inbox) == "DEVICE_EVENT"


class TestContractVersionMismatch:
    """契约版本不兼容检测测试"""

    @pytest.fixture(autouse=True)
    def allow_null_plugin(self):
        """默认允许 NullPlugin（用于测试）"""
        from src.workline_runtime.orchestrator import set_allow_null_plugin

        set_allow_null_plugin(True)
        yield
        set_allow_null_plugin(False)

    @pytest.fixture
    def orchestrator(self):
        from src.workline_runtime.orchestrator import OrchestratorService

        return OrchestratorService(lock_provider=lambda key: make_noop_lock())

    @pytest.mark.asyncio
    async def test_process_inbox_contract_mismatch_returns_failure(self, orchestrator):
        """契约版本不匹配时返回 CONTRACT_MISMATCH 失败"""

        # 创建一个 Mock 插件类，带有 contract_version = "2.0"
        class MockPlugin:
            contract_version = "2.0"

            async def on_device_event(self, ctx, inbox):
                return PluginResult()

        mock_session = MagicMock()
        mock_session.id = 12345
        mock_session.status = "RUNNING"
        mock_session.context = {}
        mock_session.contract_version = "1.0"

        mock_workline = MagicMock()
        mock_workline.plugin_key = "mock_plugin"
        mock_workline.plugin_class = MockPlugin  # 使用 Mock 插件

        mock_inbox = MagicMock()
        mock_inbox.id = 100
        mock_inbox.kind = InboxKind.DEVICE_EVENT
        mock_inbox.payload_json = {"message_type": "DEVICE_EVENT"}

        result = await orchestrator.process_inbox(
            session=mock_session,
            workline=mock_workline,
            inbox=mock_inbox,
            devices_by_role={},
            services=WorklineRuntimeServices(),
            trace_id="test-trace-id",
        )

        assert result.success is False
        assert result.failure is not None
        assert result.failure.code == "CONTRACT_MISMATCH"
        assert "1.0" in result.failure.message
        assert "2.0" in result.failure.message

    @pytest.mark.asyncio
    async def test_process_inbox_contract_match_proceeds_normally(self, orchestrator):
        """契约版本匹配时正常继续处理"""

        class MockPlugin:
            contract_version = "1.0"

            async def on_device_event(self, ctx, inbox):
                return PluginResult()

        mock_session = MagicMock()
        mock_session.id = 12345
        mock_session.status = "RUNNING"
        mock_session.context = {}
        mock_session.contract_version = "1.0"

        mock_workline = MagicMock()
        mock_workline.plugin_key = "mock_plugin"
        mock_workline.plugin_class = MockPlugin

        mock_inbox = MagicMock()
        mock_inbox.id = 100
        mock_inbox.kind = InboxKind.DEVICE_EVENT
        mock_inbox.payload_json = {"message_type": "DEVICE_EVENT"}

        result = await orchestrator.process_inbox(
            session=mock_session,
            workline=mock_workline,
            inbox=mock_inbox,
            devices_by_role={},
            services=WorklineRuntimeServices(),
            trace_id="test-trace-id",
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_process_inbox_no_session_contract_proceeds(self, orchestrator):
        """旧 Session 无 contract_version 字段时跳过检测（向后兼容）"""
        mock_session = MagicMock()
        mock_session.id = 12345
        mock_session.status = "RUNNING"
        mock_session.context = {}
        # 旧 Session 没有 contract_version 字段
        del mock_session.contract_version

        mock_workline = MagicMock()
        mock_workline.plugin_key = "smt_classifier"
        mock_workline.plugin_class = None

        mock_inbox = MagicMock()
        mock_inbox.id = 100
        mock_inbox.kind = InboxKind.DEVICE_EVENT
        mock_inbox.payload_json = {"message_type": "DEVICE_EVENT"}

        result = await orchestrator.process_inbox(
            session=mock_session,
            workline=mock_workline,
            inbox=mock_inbox,
            devices_by_role={},
            services=WorklineRuntimeServices(),
            trace_id="test-trace-id",
        )

        # 应该正常继续处理，不触发契约检测
        assert result.success is True
