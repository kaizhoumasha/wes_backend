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


class SandboxCleanupRequest(BaseModel):
    """沙箱工作线清理请求。"""

    dry_run: bool = Field(default=True, description="true 仅返回影响范围；false 执行清理")
    confirmation: str | None = Field(default=None, max_length=200, description="执行清理时必须等于工作线编码")


class SandboxCleanupResponse(BaseModel):
    """沙箱工作线清理响应。"""

    workline_id: int = Field(description="工作线 ID")
    dry_run: bool = Field(description="是否仅预览影响范围")
    deleted: bool = Field(description="是否已执行删除")
    counts: dict[str, int] = Field(default_factory=dict, description="影响数据计数")
    affected_session_ids: list[int] = Field(default_factory=list, description="受影响 Session ID")
    message: str = Field(description="清理结果消息")


class SandboxAckRequest(BaseModel):
    """沙箱 Command ACK 模拟请求。"""

    dispatch_key: str = Field(min_length=1, max_length=200, description="Dispatch Key")


class SandboxExternalCallbackRequest(BaseModel):
    """沙箱 External HTTP 回调模拟请求。"""

    dispatch_key: str = Field(min_length=1, max_length=200, description="External HTTP Outbox Dispatch Key")
    callback_type: str | None = Field(
        default=None,
        max_length=100,
        description="外部回调类型；为空时优先使用 Outbox payload.resume_callback_type",
    )
    payload: dict[str, Any] = Field(default_factory=dict, description="回调 Payload 增量字段")
    source_system: str = Field(default="WMS", pattern="^(WMS|RCS)$", description="外部来源系统")
    source_event_id: str | None = Field(default=None, max_length=200, description="外部事件 ID；为空时自动生成")
    source_version: str = Field(default="1", max_length=50, description="外部来源版本")
    request_id: str | None = Field(default=None, max_length=200, description="外部请求 ID；为空时自动生成")
    occurred_at: datetime | None = Field(default=None, description="外部事件发生时间")
    timestamp: datetime | None = Field(default=None, description="外部回调时间")
    signature: str = Field(default="sandbox", max_length=500, description="沙箱签名占位")


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
    "SandboxCleanupRequest",
    "SandboxCleanupResponse",
    "SandboxEventRequest",
    "SandboxEventTemplate",
    "SandboxExternalCallbackRequest",
    "SandboxResultRequest",
    "SandboxResultTemplate",
    "SandboxTemplatesResponse",
]
