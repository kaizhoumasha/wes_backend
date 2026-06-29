# 阶段 2 burn-down C5b 镜像:src.workline_runtime.plugin_context 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像与 wlr 副本合并 / 删除。
# 自引用 src.workline_runtime.{diagnostics, plugin_next, plugin_sdk, run_mode,
# services, topology, trace_context} 已重定向到 C5b 同目录 / C2/C4/C5a 镜像。

"""
PluginContext - 插件上下文

编排器构建并传递给插件，包含执行所需的所有上下文信息。

设计参考: 设计文档 phase2-orchestrator
"""

from collections.abc import Callable
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from src.app.runtime.orchestration.consumers.diagnostics_bridge import DiagnosticContext, build_diagnostic_context
from src.app.runtime.orchestration.topology_bridge import WorklineTopologyView
from src.app.workline.plugins.plugin_next import PluginNext
from src.app.workline.plugins.plugin_sdk import normalize_inbox_input, resolve_execution_context
from src.app.workline.plugins.plugin_sdk.contracts import ResolvedExecutionContext
from src.app.workline.plugins.run_mode import normalize_run_mode
from src.app.workline.runtime_services import WorklineRuntimeServices
from src.app.workline.trace_context import TraceContext
from src.core.logger import logger as default_logger
from src.utils.value_normalization import (
    as_dict,
    dict_attr,
    optional_int_attr,
    optional_str,
    optional_str_attr,
)


def _source_device_code_from_session(session: Any | None) -> str | None:
    context = dict_attr(session, "context_json")
    rack_operation = as_dict(context.get("rack_operation"))
    return optional_str(rack_operation.get("resume_source_device_code")) or optional_str(
        context.get("resume_source_device_code")
    )


def _normalize_workline_for_runtime(workline: Any) -> Any:
    if workline is None:
        return None

    line_type = getattr(workline, "line_type", None)
    line_type_value = getattr(line_type, "value", line_type)
    return SimpleNamespace(
        id=optional_int_attr(workline, "id"),
        line_code=optional_str_attr(workline, "line_code"),
        line_name=optional_str_attr(workline, "line_name"),
        line_type=optional_str(line_type_value),
        plugin_key=optional_str_attr(workline, "plugin_key"),
        contract_version=optional_str_attr(workline, "contract_version"),
        run_mode=normalize_run_mode(getattr(workline, "run_mode", None)),
        config=dict_attr(workline, "config"),
        runtime_config_json=dict_attr(workline, "runtime_config_json"),
        diagnostic_profile=dict_attr(workline, "diagnostic_profile"),
    )


def _normalize_device_protocol(device: Any) -> str | None:
    protocol_value = getattr(device, "protocol", None)
    return optional_str(getattr(protocol_value, "value", protocol_value))


def _normalize_device_for_runtime(device: Any, workline: Any | None) -> Any:
    if device is None:
        return None

    return SimpleNamespace(
        id=optional_int_attr(device, "id"),
        device_code=optional_str_attr(device, "device_code"),
        device_name=optional_str_attr(device, "device_name"),
        device_role=optional_str_attr(device, "device_role"),
        role_index=optional_int_attr(device, "role_index"),
        upstream_device_id=optional_int_attr(device, "upstream_device_id"),
        work_line_id=optional_int_attr(device, "work_line_id") or optional_int_attr(workline, "id"),
        protocol=_normalize_device_protocol(device),
        host=optional_str_attr(device, "host"),
        port=optional_int_attr(device, "port"),
        timeout=optional_int_attr(device, "timeout"),
        callback_path=optional_str_attr(device, "callback_path"),
        maintenance_mode=bool(getattr(device, "maintenance_mode", False)),
        capabilities_json=dict_attr(device, "capabilities_json"),
        diagnostic_profile=dict_attr(device, "diagnostic_profile"),
    )


def _resolve_source_device(
    devices_by_role: dict[str, list[Any]], inbox: Any | None, session: Any | None = None
) -> Any | None:
    payload = dict_attr(inbox, "payload_json")
    device_code = optional_str(payload.get("device_code")) or optional_str(payload.get("location"))
    if not device_code:
        normalized_input = getattr(inbox, "normalized_input", None)
        device_code = optional_str_attr(normalized_input, "device_code")
    if not device_code:
        device_code = _source_device_code_from_session(session)
    if not device_code:
        return None
    for devices in devices_by_role.values():
        for device in devices:
            if optional_str_attr(device, "device_code") == device_code:
                return device
    return None


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
    topology: WorklineTopologyView = Field(default_factory=lambda: WorklineTopologyView.from_devices([]))

    # 追踪信息
    trace: TraceContext = Field(default_factory=TraceContext)
    trace_id: str

    # 配置
    config: dict[str, Any]  # 工作线配置（由插件模型验证）
    binding_config: dict[str, Any]  # 设备绑定配置
    runtime: ResolvedExecutionContext  # 解析后的统一运行时配置
    run_mode: str = "AUTO"  # WORKLINE 运行模式快照，插件只能通过 runtime/context 感知
    source_device: Any | None = None  # 触发当前 inbox 的源设备
    source_device_role: str | None = None  # 源设备角色快照
    normalized_input: Any | None = None  # 标准化后的 inbox 输入
    diagnostics: DiagnosticContext | None = None  # 统一诊断上下文

    # 服务依赖
    services: WorklineRuntimeServices

    # 工具
    next: PluginNext = Field(default_factory=PluginNext)
    logger: Any
    clock: Callable[[], datetime]

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_device_by_role(self, role: str, index: int = 0) -> Any | None:
        """按角色和序号获取设备"""
        devices = self.devices_by_role.get(role, [])
        return devices[index] if index < len(devices) else None

    def get_topology_devices_by_role(self, role: str) -> tuple[Any, ...]:
        """按角色读取运行时拓扑快照。"""

        return self.topology.devices_for_role(role)


class PluginContextBuilder:
    """PluginContext 构建器"""

    def build(
        self,
        session: Any,
        workline: Any,
        devices_by_role: dict[str, list[Any]],
        services: WorklineRuntimeServices,
        trace_id: str = "",
        logger: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        binding_config: dict[str, Any] | None = None,
        inbox: Any | None = None,
        trace: TraceContext | None = None,
    ) -> PluginContext:
        """构建插件上下文"""
        # 只接受真实 dict，避免 MagicMock 等测试替身被错误当作配置对象
        raw_config = getattr(workline, "config", None)
        config = cast("dict[str, Any]", raw_config) if isinstance(raw_config, dict) else {}

        # 使用默认值
        if logger is None:
            logger = default_logger

        if clock is None:
            clock = datetime.now

        if binding_config is None:
            binding_config = {}

        resolved_trace = trace or TraceContext.from_runtime(
            session=session,
            workline=workline,
            inbox=inbox,
            trace_id=trace_id,
        )
        if inbox is not None:
            resolved_trace = resolved_trace.with_inbox(inbox)

        runtime_workline = _normalize_workline_for_runtime(workline)
        runtime_devices_by_role = {
            role: [_normalize_device_for_runtime(device, runtime_workline) for device in devices]
            for role, devices in devices_by_role.items()
        }
        topology = WorklineTopologyView.from_devices(
            [device for role_devices in devices_by_role.values() for device in role_devices]
        )
        runtime = resolve_execution_context(runtime_workline, runtime_devices_by_role)
        session_run_mode = normalize_run_mode(getattr(session, "run_mode", None))
        if runtime.workline is not None:
            runtime.workline.run_mode = session_run_mode
        source_device = _resolve_source_device(devices_by_role, inbox, session)
        source_device_role = optional_str_attr(source_device, "device_role")
        normalized_input = None
        if inbox is not None:
            normalized_input = normalize_inbox_input(
                inbox,
                trace_id=resolved_trace.trace_id or trace_id,
                plugin_key=optional_str_attr(workline, "plugin_key"),
            )
        diagnostics = build_diagnostic_context(
            trace=resolved_trace,
            session=session,
            inbox=inbox,
            workline=workline,
            canonical_event_type=getattr(normalized_input, "canonical_event_type", None),
        )

        return PluginContext(
            workline=workline,
            session=session,
            devices_by_role=devices_by_role,
            topology=topology,
            trace=resolved_trace,
            trace_id=resolved_trace.trace_id or trace_id or "",
            config=config,
            binding_config=binding_config,
            runtime=runtime,
            run_mode=session_run_mode,
            source_device=source_device,
            source_device_role=source_device_role,
            normalized_input=normalized_input,
            diagnostics=diagnostics,
            services=services,
            next=PluginNext(),
            logger=logger,
            clock=clock,
        )


__all__ = ["PluginContext", "PluginContextBuilder"]
