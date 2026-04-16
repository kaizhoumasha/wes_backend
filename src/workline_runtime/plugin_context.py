"""
PluginContext - 插件上下文

编排器构建并传递给插件，包含执行所需的所有上下文信息。

设计参考: 设计文档 phase2-orchestrator
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel, ConfigDict


class PluginContext(BaseModel):
    """插件上下文 - 编排器构建，传递给插件

    包含插件执行所需的所有上下文信息：
    - 工作线和 Session 实体
    - 设备映射
    - 追踪信息
    - 配置
    - 服务依赖
    - 工具（logger, clock）

    Attributes:
        workline: 工作线实体
        session: Session 实体
        devices_by_role: 按角色分组的设备映射
        correlation_id: 关联 ID（用于日志串联）
        config: 工作线配置（由插件模型验证）
        binding_config: 设备绑定配置
        services: 领域服务容器
        logger: 日志器
        clock: 时钟函数（用于测试注入）
    """

    # 核心实体
    workline: Any  # WorkLine - 使用 Any 避免 TYPE_CHECKING 问题
    session: Any  # WorklineSession
    devices_by_role: dict[str, list[Any]]  # dict[str, list[Device]]

    # 追踪信息
    correlation_id: str

    # 配置
    config: dict[str, Any]  # 工作线配置（由插件模型验证）
    binding_config: dict[str, Any]  # 设备绑定配置

    # 服务依赖
    services: Any  # DomainServices - 领域服务容器

    # 工具
    logger: logging.Logger
    clock: Callable[[], datetime]

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_device_by_role(self, role: str, index: int = 0) -> Any | None:
        """按角色和序号获取设备

        Args:
            role: 设备角色 (SCANNER, CONVEYOR, etc.)
            index: 设备序号（默认 0）

        Returns:
            Device 或 None（未找到时）

        Example:
            scanner = ctx.get_device_by_role("SCANNER")
            second_scanner = ctx.get_device_by_role("SCANNER", index=1)
        """
        devices = self.devices_by_role.get(role, [])
        return devices[index] if index < len(devices) else None


class PluginContextBuilder:
    """PluginContext 构建器

    编排器使用此构建器创建 PluginContext 实例，传递给插件执行。

    构建器负责：
    - 从 WorkLine 实体提取 config
    - 提供默认的 logger 和 clock
    - 组装所有上下文信息

    Example:
        builder = PluginContextBuilder()
        ctx = builder.build(
            session=session,
            workline=workline,
            devices_by_role=devices_by_role,
            services=services,
            correlation_id="corr-123",
        )
    """

    def build(
        self,
        session: Any,
        workline: Any,
        devices_by_role: dict[str, list[Any]],
        services: Any,
        correlation_id: str = "",
        logger: logging.Logger | None = None,
        clock: Callable[[], datetime] | None = None,
        binding_config: dict[str, Any] | None = None,
    ) -> PluginContext:
        """构建插件上下文

        Args:
            session: WorklineSession 实体
            workline: WorkLine 实体
            devices_by_role: 按角色分组的设备映射
            services: 领域服务容器
            correlation_id: 关联 ID（用于日志串联）
            logger: 日志器（可选，默认使用 workline_runtime logger）
            clock: 时钟函数（可选，默认使用 datetime.now）
            binding_config: 设备绑定配置（可选，默认空字典）

        Returns:
            构建好的 PluginContext 实例
        """
        # 只接受真实 dict，避免 MagicMock 等测试替身被错误当作配置对象
        raw_config = getattr(workline, "config", None)
        config = cast("dict[str, Any]", raw_config) if isinstance(raw_config, dict) else {}

        # 使用默认值
        if logger is None:
            logger = logging.getLogger("workline_runtime")

        if clock is None:
            clock = datetime.now

        if binding_config is None:
            binding_config = {}

        return PluginContext(
            workline=workline,
            session=session,
            devices_by_role=devices_by_role,
            correlation_id=correlation_id,
            config=config,
            binding_config=binding_config,
            services=services,
            logger=logger,
            clock=clock,
        )


__all__ = ["PluginContext", "PluginContextBuilder"]
