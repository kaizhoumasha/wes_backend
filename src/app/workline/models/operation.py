"""工作线操作请求/响应 Schema。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic requires runtime type access
from typing import Any

from pydantic import BaseModel, Field


class SandboxEventRequest(BaseModel):
    """沙箱 Event 发送请求。"""

    workline_id: int = Field(description="工作线 ID")
    device_id: int = Field(description="目标设备 ID")
    event_type: str = Field(min_length=1, max_length=100, description="事件类型")
    trace_id: str | None = Field(default=None, max_length=200, description="Trace ID（可选，自动生成）")
    session_id: int | None = Field(default=None, description="Session ID（可选）")
    payload: dict[str, Any] = Field(default_factory=dict, description="事件 Payload")
    timestamp: datetime | None = Field(default=None, description="事件时间戳（默认当前时间）")


class SandboxAckRequest(BaseModel):
    """沙箱 Command ACK 模拟请求。"""

    dispatch_key: str = Field(min_length=1, max_length=200, description="Dispatch Key")


class ReplayInboxRequest(BaseModel):
    """Replay 请求。"""

    reason: str = Field(min_length=1, max_length=500)
    operator_id: str | None = Field(default=None, max_length=100)


class ManualOperationRequest(BaseModel):
    """人工操作请求。"""

    operation: str = Field(pattern="^(HOLD|RESUME|CANCEL)$")
    operator_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class ResolveRuntimeReconciliationRequest(BaseModel):
    """人工运行时对账解除请求。"""

    resolution: str = Field(pattern="^(COMPLETED|FAILED|CANCELLED)$", description="人工对账决议")
    checks: dict[str, bool] = Field(description="按 reconciliation_reason 要求确认的 checklist")
    operator_note: str = Field(min_length=1, max_length=1000, description="现场确认说明")
    result_payload: dict[str, Any] | None = Field(default=None, description="COMPLETED 时可补录业务结果摘要")
    confirmed_at: datetime = Field(description="现场确认时间")


class SandboxResultRequest(BaseModel):
    """沙箱 Command Result 模拟请求。"""

    command_code: str = Field(min_length=1, max_length=100, description="Command Code")
    device_code: str = Field(min_length=1, max_length=100, description="设备 Code")
    result: str = Field(pattern="^(SUCCESS|FAILED)$", description="结果状态")
    payload: dict[str, Any] = Field(default_factory=dict, description="Result Payload")
    error_detail: str | None = Field(default=None, max_length=500, description="错误详情（FAILED 时）")
    timestamp: datetime | None = Field(default=None, description="结果时间戳（默认当前时间）")


class SandboxEventTemplate(BaseModel):
    """沙箱 Event 模板。"""

    event_type: str = Field(description="事件类型标识")
    label: str = Field(description="事件类型显示名称")
    payload_template: dict[str, Any] = Field(default_factory=dict, description="Payload 模板")


class SandboxResultTemplate(BaseModel):
    """沙箱 Result 模板。"""

    command_type: str = Field(description="Command 类型标识")
    label: str = Field(description="Command 类型显示名称")
    success_payload_template: dict[str, Any] = Field(default_factory=dict, description="成功 Payload 模板")
    failed_payload_template: dict[str, Any] = Field(default_factory=dict, description="失败 Payload 模板")
    error_template: str | None = Field(default=None, description="错误信息模板")


class SandboxTemplatesResponse(BaseModel):
    """沙箱模板响应。"""

    event_templates: list[SandboxEventTemplate] = Field(default_factory=list, description="Event 模板列表")
    result_templates: list[SandboxResultTemplate] = Field(default_factory=list, description="Result 模板列表")


__all__ = [
    "ManualOperationRequest",
    "ReplayInboxRequest",
    "ResolveRuntimeReconciliationRequest",
    "SandboxAckRequest",
    "SandboxEventRequest",
    "SandboxEventTemplate",
    "SandboxResultRequest",
    "SandboxResultTemplate",
    "SandboxTemplatesResponse",
]
