"""
PluginContext 单元测试

测试插件上下文的构建和功能：
- 基本字段创建
- get_device_by_role 方法
- Pydantic 模型验证

设计参考:
- 设计文档: phase2-orchestrator design doc
"""

import logging
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.workline_runtime.plugin_context import PluginContext
from src.workline_runtime.plugin_sdk.contracts import ResolvedExecutionContext
from src.workline_runtime.services import WorklineRuntimeServices


class TestPluginContextCreation:
    """PluginContext 创建测试"""

    @pytest.fixture
    def mock_services(self):
        """创建运行时服务容器"""
        return WorklineRuntimeServices()

    @pytest.fixture
    def mock_runtime(self):
        """创建模拟的运行时上下文"""
        return ResolvedExecutionContext()

    def test_create_plugin_context(
        self, workline_runtime_workline, workline_runtime_session, workline_runtime_device, mock_services, mock_runtime
    ):
        """测试创建插件上下文"""
        devices_by_role = {"SCANNER": [workline_runtime_device]}

        ctx = PluginContext(
            workline=workline_runtime_workline,
            session=workline_runtime_session,
            devices_by_role=devices_by_role,
            trace_id="trace-123",
            config={"scan_timeout": 30},
            binding_config={"SCANNER": {"device_id": 1}},
            runtime=mock_runtime,
            services=mock_services,
            logger=logging.getLogger("test"),
            clock=lambda: datetime(2026, 1, 1, 12, 0, 0),
        )

        assert ctx.workline == workline_runtime_workline
        assert ctx.session == workline_runtime_session
        assert ctx.devices_by_role["SCANNER"][0] == workline_runtime_device
        assert ctx.trace_id == "trace-123"
        assert ctx.config["scan_timeout"] == 30

    def test_get_device_by_role_found(
        self, workline_runtime_workline, workline_runtime_session, mock_services, mock_runtime
    ):
        """测试按角色获取设备 - 找到"""
        device1 = MagicMock(id=1, device_role="SCANNER")
        device2 = MagicMock(id=2, device_role="SCANNER")
        devices_by_role = {"SCANNER": [device1, device2]}

        ctx = PluginContext(
            workline=workline_runtime_workline,
            session=workline_runtime_session,
            devices_by_role=devices_by_role,
            trace_id="trace-123",
            config={},
            binding_config={},
            runtime=mock_runtime,
            services=mock_services,
            logger=logging.getLogger("test"),
            clock=lambda: datetime.now(),
        )

        # 获取第一个设备
        found = ctx.get_device_by_role("SCANNER")
        assert found == device1

        # 获取第二个设备
        found = ctx.get_device_by_role("SCANNER", index=1)
        assert found == device2

    def test_get_device_by_role_not_found(
        self, workline_runtime_workline, workline_runtime_session, mock_services, mock_runtime
    ):
        """测试按角色获取设备 - 未找到"""
        ctx = PluginContext(
            workline=workline_runtime_workline,
            session=workline_runtime_session,
            devices_by_role={},
            trace_id="trace-123",
            config={},
            binding_config={},
            runtime=mock_runtime,
            services=mock_services,
            logger=logging.getLogger("test"),
            clock=lambda: datetime.now(),
        )

        # 角色不存在
        found = ctx.get_device_by_role("CONVEYOR")
        assert found is None

    def test_get_device_by_role_index_out_of_range(
        self, workline_runtime_workline, workline_runtime_session, mock_services, mock_runtime
    ):
        """测试按角色获取设备 - 索引越界"""
        device = MagicMock(id=1, device_role="SCANNER")
        devices_by_role = {"SCANNER": [device]}

        ctx = PluginContext(
            workline=workline_runtime_workline,
            session=workline_runtime_session,
            devices_by_role=devices_by_role,
            trace_id="trace-123",
            config={},
            binding_config={},
            runtime=mock_runtime,
            services=mock_services,
            logger=logging.getLogger("test"),
            clock=lambda: datetime.now(),
        )

        # 索引越界
        found = ctx.get_device_by_role("SCANNER", index=5)
        assert found is None

    def test_logger_is_logging_logger(
        self, workline_runtime_workline, workline_runtime_session, mock_services, mock_runtime
    ):
        """测试 logger 是 logging.Logger 类型"""
        logger = logging.getLogger("test_plugin")
        ctx = PluginContext(
            workline=workline_runtime_workline,
            session=workline_runtime_session,
            devices_by_role={},
            trace_id="trace-123",
            config={},
            binding_config={},
            runtime=mock_runtime,
            services=mock_services,
            logger=logger,
            clock=lambda: datetime.now(),
        )

        assert isinstance(ctx.logger, logging.Logger)

    def test_clock_returns_datetime(
        self, workline_runtime_workline, workline_runtime_session, mock_services, mock_runtime
    ):
        """测试 clock 返回 datetime"""
        fixed_time = datetime(2026, 1, 1, 12, 0, 0)
        ctx = PluginContext(
            workline=workline_runtime_workline,
            session=workline_runtime_session,
            devices_by_role={},
            trace_id="trace-123",
            config={},
            binding_config={},
            runtime=mock_runtime,
            services=mock_services,
            logger=logging.getLogger("test"),
            clock=lambda: fixed_time,
        )

        result = ctx.clock()
        assert isinstance(result, datetime)
        assert result == fixed_time

    def test_arbitrary_types_allowed(
        self, workline_runtime_workline, workline_runtime_session, mock_services, mock_runtime
    ):
        """测试 arbitrary_types_allowed 配置允许任意类型"""
        # MagicMock 不是 Pydantic 默认支持的类型
        # 但 Config.arbitrary_types_allowed = True 应该允许业务实体字段
        ctx = PluginContext(
            workline=workline_runtime_workline,  # MagicMock
            session=workline_runtime_session,  # MagicMock
            devices_by_role={},  # dict[str, list[MagicMock]]
            trace_id="trace-123",
            config={},
            binding_config={},
            runtime=mock_runtime,
            services=mock_services,
            logger=logging.getLogger("test"),
            clock=lambda: datetime.now(),
        )

        # 不应该抛出 ValidationError
        assert ctx is not None
