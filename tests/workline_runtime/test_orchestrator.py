"""
OrchestratorService 单元测试

测试编排器核心流程：
- Phase 2 默认行为（无状态机）
- 分布式锁获取与释放
- 插件调用与结果处理
- PluginResult 各字段处理（transition, commands, wait, failure, complete）

设计参考: 设计文档 phase2-orchestrator
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.models.inbox import InboxKind
from src.workline_runtime.types import (
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

    @pytest.fixture
    def mock_session(self):
        """创建模拟 Session"""
        session = MagicMock()
        session.id = 12345
        session.status = "RUNNING"
        session.context_json = {"key": "value"}
        session.version = 1
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
        return MagicMock()

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
            correlation_id="test-correlation-id",
        )

        assert result is not None
        assert result.success is True

    @pytest.mark.asyncio
    async def test_process_inbox_acquires_lock(
        self,
        mock_session,
        mock_workline,
        mock_inbox,
        mock_devices_by_role,
        mock_services,
    ):
        """测试处理 Inbox 时获取分布式锁"""
        from src.workline_runtime.orchestrator import OrchestratorService

        lock_acquired = False
        lock_key_received = None

        @asynccontextmanager
        async def test_lock(key):
            nonlocal lock_acquired, lock_key_received
            lock_acquired = True
            lock_key_received = key
            yield

        orchestrator = OrchestratorService(lock_provider=test_lock)

        await orchestrator.process_inbox(
            session=mock_session,
            workline=mock_workline,
            inbox=mock_inbox,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="test-correlation-id",
        )

        assert lock_acquired is True
        assert "session:12345" in lock_key_received

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
            correlation_id="test-correlation-id",
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
                correlation_id="test-correlation-id",
            )

            assert result.success is True
            assert result.transition == "scan_ok"

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
                correlation_id="test-correlation-id",
            )

            assert result.success is True
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
                correlation_id="test-correlation-id",
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
                correlation_id="test-correlation-id",
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
                correlation_id="test-correlation-id",
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
                correlation_id="test-correlation-id",
            )

            assert result.success is True
            assert result.context_patch == {"new_key": "new_value", "count": 42}


class TestOrchestratorServicePluginLoading:
    """OrchestratorService 插件加载测试"""

    @pytest.fixture
    def orchestrator(self):
        """创建 OrchestratorService 实例"""
        from src.workline_runtime.orchestrator import OrchestratorService

        return OrchestratorService(lock_provider=lambda key: make_noop_lock())

    def test_load_plugin_returns_null_when_no_plugin_class(self, orchestrator):
        """测试无插件类时返回 NullPlugin"""
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
                services=MagicMock(),
                correlation_id="test-correlation-id",
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
            services=MagicMock(),
            correlation_id="test-correlation-id",
        )

        assert result.success is True

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
