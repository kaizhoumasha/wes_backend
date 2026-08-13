"""工作线操作请求/响应 Schema。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic requires runtime type access
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.app.runtime.orchestration.wms_sync_obligation import (  # noqa: TC001 - Pydantic 运行时解析字段类型
    WmsSyncObligationResolution,
)


class SandboxWorklineStartRequest(BaseModel):
    """沙箱 WorkLine START 请求。"""

    device_code: str = Field(min_length=1, max_length=100, description="触发 START 的设备编码")
    trace_id: str | None = Field(default=None, max_length=200, description="Trace ID（可选，自动生成）")


class SandboxWorklineStartResponse(BaseModel):
    """沙箱 WorkLine START 准入结果。"""

    status: str | None = None
    ack: bool | None = None
    device_code: str | None = None
    trace_id: str | None = None
    reason_code: str | None = None
    diagnostic: dict[str, Any] | None = None


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

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("request_id", mode="before")
    @classmethod
    def normalize_request_id(cls, value: object) -> object:
        """规范化调用方稳定 request identity。"""

        return value.strip() if isinstance(value, str) else value


class ResolveRuntimeReconciliationRequest(BaseModel):
    """人工运行时对账解除请求。"""

    resolution: str = Field(pattern="^(COMPLETED|FAILED|CANCELLED)$", description="人工对账决议")
    checks: dict[str, bool] = Field(description="按 reconciliation_reason 要求确认的 checklist")
    operator_note: str = Field(min_length=1, max_length=1000, description="现场确认说明")
    result_payload: dict[str, Any] | None = Field(default=None, description="COMPLETED 时可补录业务结果摘要")
    confirmed_at: datetime = Field(description="现场确认时间")


class ResolveEffectReconciliationRequest(BaseModel):
    """人工 EFFECT 对账决议请求。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str | None = Field(default=None, min_length=1, max_length=100, description="通用决议稳定幂等请求 ID")
    resolution: str | None = Field(
        default=None,
        pattern="^(COMPLETED|REJECTED)$",
        description="非 E03/E07 EFFECT 最终决议",
    )
    obligation_resolution: WmsSyncObligationResolution | None = Field(
        default=None,
        description="E03/E07 同步义务 typed 对账裁决",
    )
    operator_note: str = Field(min_length=1, max_length=1000, description="人工核验说明")

    @field_validator("request_id", mode="before")
    @classmethod
    def normalize_request_id(cls, value: object) -> object:
        """规范化人工决议的稳定幂等身份。"""

        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_resolution_variant(self) -> ResolveEffectReconciliationRequest:
        has_generic = self.request_id is not None or self.resolution is not None
        if self.obligation_resolution is not None:
            if has_generic:
                raise ValueError("typed obligation resolution cannot mix with generic EFFECT resolution")
            return self
        if self.request_id is None or self.resolution is None:
            raise ValueError("generic EFFECT resolution requires request_id and resolution")
        return self


__all__ = [
    "ReplayInboxRequest",
    "ResolveEffectReconciliationRequest",
    "ResolveRuntimeReconciliationRequest",
    "SandboxAckRequest",
    "SandboxExternalCallbackRequest",
    "SandboxWorklineStartRequest",
    "SandboxWorklineStartResponse",
]
