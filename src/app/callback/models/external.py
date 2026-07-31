"""外部 callback 公共请求文档模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CallbackExternalRequest(BaseModel):
    """仅用于公开多供应商 callback 的公共包络；运行时仍校验具体 typed contract。"""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "callback_type": "WMS_EFFECT_STATUS_HINT",
                    "trace_id": "trace-01JQA",
                    "source_event_id": "wms-event-01JQA",
                    "source_system": "WMS",
                    "occurred_at": "2026-07-30T08:00:00Z",
                    "data": {
                        "operation_identity": "wms.fulfillment.request_rack_supply@v1",
                        "idempotency_key": "idem-01JQA",
                        "dispatch_key": "dispatch-01JQA",
                    },
                }
            ]
        },
    )

    callback_type: str = Field(min_length=1, max_length=120, description="已注册的外部 callback 类型")
    trace_id: str | None = Field(default=None, max_length=120, description="端到端追踪 ID")
    event_id: str | None = Field(default=None, max_length=200, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, max_length=200, description="因果事件 ID")
    source_event_id: str | None = Field(default=None, max_length=160, description="供应商幂等事件 ID")
    occurred_at: str | None = Field(default=None, description="外部事件发生时间（offset-aware ISO 8601）")
    dispatch_key: str | None = Field(default=None, max_length=240, description="异步 EFFECT 调度身份")
    data: dict[str, Any] | None = Field(default=None, description="供应商业务载荷；具体字段由 callback_type 决定")


__all__ = ["CallbackExternalRequest"]
