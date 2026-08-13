"""Callback ingress response models."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.core.response import ResponseSchemaModel


class CallbackRejectedResponse(BaseModel):
    """Callback 入口拒收响应数据。"""

    ack: Literal[False] = Field(default=False, description="入口是否接收")
    reason_code: str | None = Field(default=None, description="拒收原因代码")
    diagnostic: dict[str, Any] | None = Field(default=None, description="拒收诊断信息")


class CallbackHTTPExceptionResponse(BaseModel):
    """Callback 入口由 HTTPException 返回的传输层错误。"""

    detail: str = Field(description="可重试或请求体限制错误说明")


class CallbackExternalAcceptedResponse(BaseModel):
    """外部系统回调接收响应数据。"""

    status: Literal["submitted", "duplicate"] = Field(description="入口处理状态")
    callback_type: str = Field(description="外部回调类型")
    request_id: str | None = Field(default=None, description="入口请求 ID")
    trace_id: str | None = Field(default=None, description="Trace ID")
    event_id: str | None = Field(default=None, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, description="因果事件 ID")


type CallbackRejectedIngressResponse = ResponseSchemaModel[CallbackRejectedResponse]
type CallbackExternalIngressResponse = ResponseSchemaModel[CallbackExternalAcceptedResponse | CallbackRejectedResponse]


def build_callback_rejected_response(
    reason_code: str | None = None,
    diagnostic: dict[str, Any] | None = None,
) -> CallbackRejectedResponse:
    return CallbackRejectedResponse(reason_code=reason_code, diagnostic=diagnostic)


def build_callback_external_accepted_response(
    *,
    status: Literal["submitted", "duplicate"],
    callback_type: str,
    request_id: str | None,
    trace_id: str | None,
    event_id: str | None,
    causation_id: str | None,
) -> CallbackExternalAcceptedResponse:
    return CallbackExternalAcceptedResponse(
        status=status,
        callback_type=callback_type,
        request_id=request_id,
        trace_id=trace_id,
        event_id=event_id,
        causation_id=causation_id,
    )


__all__ = [
    "CallbackExternalAcceptedResponse",
    "CallbackExternalIngressResponse",
    "CallbackHTTPExceptionResponse",
    "CallbackRejectedIngressResponse",
    "CallbackRejectedResponse",
    "build_callback_external_accepted_response",
    "build_callback_rejected_response",
]
