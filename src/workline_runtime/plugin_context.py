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

from src.workline_runtime.diagnostics import DiagnosticContext, build_diagnostic_context
from src.workline_runtime.plugin_sdk import normalize_inbox_input, resolve_execution_context
from src.workline_runtime.plugin_sdk.contracts import ResolvedExecutionContext


class PluginContext(BaseModel):
    """插件上下文 - 编排器构建，传递给插件

    包含插件执行所需的所有上下文信息：
    - 工作线和 Session 实体
    - 设备映射
    - 追踪信息
    - 配置
    - 解析后的运行时快照
    - 标准化输入与诊断上下文
    - 服务依赖
    - 工具（logger, clock）
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
    runtime: ResolvedExecutionContext  # 解析后的统一运行时配置
    normalized_input: Any | None = None  # 标准化后的 inbox 输入
    diagnostics: DiagnosticContext | None = None  # 统一诊断上下文

    # 服务依赖
    services: Any  # DomainServices - 领域服务容器

    # 工具
    logger: logging.Logger
    clock: Callable[[], datetime]

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_device_by_role(self, role: str, index: int = 0) -> Any | None:
        """按角色和序号获取设备"""
        devices = self.devices_by_role.get(role, [])
        return devices[index] if index < len(devices) else None


class PluginContextBuilder:
    """PluginContext 构建器"""

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
        inbox: Any | None = None,
    ) -> PluginContext:
        """构建插件上下文"""
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

        runtime = resolve_execution_context(workline, devices_by_role)
        normalized_input = normalize_inbox_input(inbox, correlation_id=correlation_id) if inbox is not None else None
        diagnostics = build_diagnostic_context(
            correlation_id=correlation_id,
            session=session,
            inbox=inbox,
            workline=workline,
            canonical_event_type=getattr(normalized_input, "canonical_event_type", None),
        )

        return PluginContext(
            workline=workline,
            session=session,
            devices_by_role=devices_by_role,
            correlation_id=correlation_id,
            config=config,
            binding_config=binding_config,
            runtime=runtime,
            normalized_input=normalized_input,
            diagnostics=diagnostics,
            services=services,
            logger=logger,
            clock=clock,
        )


__all__ = ["PluginContext", "PluginContextBuilder"]
