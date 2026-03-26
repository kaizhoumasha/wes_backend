"""
PluginContextBuilder 单元测试

测试 PluginContextBuilder 组件：
- 构建带有所有必需字段的 PluginContext
- 从 WorkLine 提取 config
- get_device_by_role 方法行为
- logger 和 clock 默认值

设计参考:
- 设计文档: phase2-orchestrator
"""

import logging
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.workline_runtime.plugin_context import PluginContext, PluginContextBuilder


class TestPluginContextBuilder:
    """PluginContextBuilder 测试"""

    @pytest.fixture
    def builder(self):
        """创建构建器实例"""
        return PluginContextBuilder()

    @pytest.fixture
    def mock_workline(self):
        """创建模拟的工作线"""
        workline = MagicMock()
        workline.id = 1
        workline.line_code = "SMT-001"
        workline.config = {"scan_timeout": 30, "retry_count": 3}
        return workline

    @pytest.fixture
    def mock_session(self):
        """创建模拟的 Session"""
        session = MagicMock()
        session.id = 123
        session.status = "RUNNING"
        session.context_json = {"barcode": "ABC123"}
        return session

    @pytest.fixture
    def mock_devices_by_role(self):
        """创建模拟的设备映射"""
        scanner1 = MagicMock(id=1, device_code="SCAN-001", device_role="SCANNER")
        scanner2 = MagicMock(id=2, device_code="SCAN-002", device_role="SCANNER")
        conveyor = MagicMock(id=3, device_code="CONV-001", device_role="CONVEYOR")
        return {
            "SCANNER": [scanner1, scanner2],
            "CONVEYOR": [conveyor],
        }

    @pytest.fixture
    def mock_services(self):
        """创建模拟的服务容器"""
        services = MagicMock()
        services.inbox_service = MagicMock()
        services.outbox_service = MagicMock()
        return services

    def test_build_creates_context_with_all_fields(
        self, builder, mock_session, mock_workline, mock_devices_by_role, mock_services
    ):
        """测试构建的上下文包含所有必需字段"""
        ctx = builder.build(
            session=mock_session,
            workline=mock_workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="corr-123",
        )

        # 验证核心实体
        assert ctx.workline == mock_workline
        assert ctx.session == mock_session
        assert ctx.devices_by_role == mock_devices_by_role

        # 验证追踪信息
        assert ctx.correlation_id == "corr-123"

        # 验证服务依赖
        assert ctx.services == mock_services

        # 验证工具存在
        assert isinstance(ctx.logger, logging.Logger)
        assert callable(ctx.clock)

    def test_build_extracts_config_from_workline(
        self, builder, mock_session, mock_workline, mock_devices_by_role, mock_services
    ):
        """测试从 WorkLine 提取 config"""
        ctx = builder.build(
            session=mock_session,
            workline=mock_workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="corr-456",
        )

        # config 应该从 workline.config 提取
        assert ctx.config == {"scan_timeout": 30, "retry_count": 3}

    def test_build_with_empty_workline_config(
        self, builder, mock_session, mock_devices_by_role, mock_services
    ):
        """测试 WorkLine config 为空时使用空字典"""
        workline = MagicMock()
        workline.config = None

        ctx = builder.build(
            session=mock_session,
            workline=workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="corr-789",
        )

        # config 应该是空字典
        assert ctx.config == {}

    def test_get_device_by_role_returns_correct_device(
        self, builder, mock_session, mock_workline, mock_devices_by_role, mock_services
    ):
        """测试 get_device_by_role 返回正确的设备"""
        ctx = builder.build(
            session=mock_session,
            workline=mock_workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="corr-123",
        )

        # 获取第一个 SCANNER
        device = ctx.get_device_by_role("SCANNER")
        assert device.id == 1

        # 获取第二个 SCANNER
        device = ctx.get_device_by_role("SCANNER", index=1)
        assert device.id == 2

        # 获取 CONVEYOR
        device = ctx.get_device_by_role("CONVEYOR")
        assert device.id == 3

    def test_get_device_by_role_returns_none_for_missing_role(
        self, builder, mock_session, mock_workline, mock_devices_by_role, mock_services
    ):
        """测试 get_device_by_role 对不存在的角色返回 None"""
        ctx = builder.build(
            session=mock_session,
            workline=mock_workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="corr-123",
        )

        # 不存在的角色
        device = ctx.get_device_by_role("ROBOT")
        assert device is None

        # 索引越界
        device = ctx.get_device_by_role("CONVEYOR", index=10)
        assert device is None

    def test_context_has_logger_and_clock(
        self, builder, mock_session, mock_workline, mock_devices_by_role, mock_services
    ):
        """测试上下文有 logger 和 clock"""
        ctx = builder.build(
            session=mock_session,
            workline=mock_workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="corr-123",
        )

        # logger 应该是 logging.Logger 实例
        assert isinstance(ctx.logger, logging.Logger)

        # clock 应该是可调用对象
        assert callable(ctx.clock)

        # clock 返回 datetime
        result = ctx.clock()
        assert isinstance(result, datetime)

    def test_build_with_default_correlation_id(
        self, builder, mock_session, mock_workline, mock_devices_by_role, mock_services
    ):
        """测试 correlation_id 默认值为空字符串"""
        ctx = builder.build(
            session=mock_session,
            workline=mock_workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            # 不传 correlation_id
        )

        assert ctx.correlation_id == ""

    def test_build_binding_config_default_empty(
        self, builder, mock_session, mock_workline, mock_devices_by_role, mock_services
    ):
        """测试 binding_config 默认为空字典"""
        ctx = builder.build(
            session=mock_session,
            workline=mock_workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="corr-123",
        )

        # binding_config 应该默认为空字典
        assert ctx.binding_config == {}

    def test_build_with_custom_logger(
        self, builder, mock_session, mock_workline, mock_devices_by_role, mock_services
    ):
        """测试使用自定义 logger"""
        custom_logger = logging.getLogger("custom_plugin_logger")

        ctx = builder.build(
            session=mock_session,
            workline=mock_workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="corr-123",
            logger=custom_logger,
        )

        assert ctx.logger == custom_logger

    def test_build_with_custom_clock(
        self, builder, mock_session, mock_workline, mock_devices_by_role, mock_services
    ):
        """测试使用自定义 clock"""
        fixed_time = datetime(2026, 1, 1, 12, 0, 0)

        ctx = builder.build(
            session=mock_session,
            workline=mock_workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="corr-123",
            clock=lambda: fixed_time,
        )

        assert ctx.clock() == fixed_time

    def test_build_returns_plugin_context_instance(
        self, builder, mock_session, mock_workline, mock_devices_by_role, mock_services
    ):
        """测试 build 返回 PluginContext 实例"""
        ctx = builder.build(
            session=mock_session,
            workline=mock_workline,
            devices_by_role=mock_devices_by_role,
            services=mock_services,
            correlation_id="corr-123",
        )

        assert isinstance(ctx, PluginContext)
