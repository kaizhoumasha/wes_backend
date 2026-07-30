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


class CallbackResultAcceptedResponse(BaseModel):
    """设备结果回调接收响应数据。"""

    ack: Literal[True] = Field(default=True, description="入口是否接收")
    request_id: str | None = Field(default=None, description="入口请求 ID")
    trace_id: str | None = Field(default=None, description="Trace ID")
    event_id: str | None = Field(default=None, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, description="因果事件 ID")


class CallbackEventAcceptedResponse(BaseModel):
    """设备或普通外部事件回调接收响应数据。"""

    status: Literal["submitted", "duplicate", "accepted"] = Field(description="入口处理状态")
    device_code: str | None = Field(default=None, description="设备事件的设备编码")
    source_system: str | None = Field(default=None, description="外部普通事件来源系统")
    event_type: str | None = Field(default=None, description="外部普通事件类型")
    request_id: str | None = Field(default=None, description="入口请求 ID")
    trace_id: str | None = Field(default=None, description="Trace ID")
    event_id: str | None = Field(default=None, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, description="因果事件 ID")
    diagnostic: dict[str, Any] | None = Field(default=None, description="START 准入诊断信息")


class CallbackExternalAcceptedResponse(BaseModel):
    """外部系统回调接收响应数据。"""

    status: Literal["submitted", "duplicate"] = Field(description="入口处理状态")
    callback_type: str = Field(description="外部回调类型")
    request_id: str | None = Field(default=None, description="入口请求 ID")
    trace_id: str | None = Field(default=None, description="Trace ID")
    event_id: str | None = Field(default=None, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, description="因果事件 ID")


type CallbackRejectedIngressResponse = ResponseSchemaModel[CallbackRejectedResponse]
type CallbackResultIngressResponse = ResponseSchemaModel[CallbackResultAcceptedResponse | CallbackRejectedResponse]
type CallbackEventIngressResponse = ResponseSchemaModel[CallbackEventAcceptedResponse | CallbackRejectedResponse]
type CallbackExternalIngressResponse = ResponseSchemaModel[CallbackExternalAcceptedResponse | CallbackRejectedResponse]


def build_callback_rejected_response(
    reason_code: str | None = None,
    diagnostic: dict[str, Any] | None = None,
) -> CallbackRejectedResponse:
    return CallbackRejectedResponse(reason_code=reason_code, diagnostic=diagnostic)


def build_callback_result_accepted_response(
    *,
    request_id: str | None,
    trace_id: str | None,
    event_id: str | None,
    causation_id: str | None,
) -> CallbackResultAcceptedResponse:
    return CallbackResultAcceptedResponse(
        request_id=request_id,
        trace_id=trace_id,
        event_id=event_id,
        causation_id=causation_id,
    )


def build_callback_event_accepted_response(
    *,
    status: Literal["submitted", "duplicate", "accepted"],
    device_code: str | None,
    source_system: str | None = None,
    event_type: str | None = None,
    request_id: str | None,
    trace_id: str | None,
    event_id: str | None,
    causation_id: str | None,
    diagnostic: dict[str, Any] | None = None,
) -> CallbackEventAcceptedResponse:
    return CallbackEventAcceptedResponse(
        status=status,
        device_code=device_code,
        source_system=source_system,
        event_type=event_type,
        request_id=request_id,
        trace_id=trace_id,
        event_id=event_id,
        causation_id=causation_id,
        diagnostic=diagnostic,
    )


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
    "CallbackEventAcceptedResponse",
    "CallbackEventIngressResponse",
    "CallbackExternalAcceptedResponse",
    "CallbackExternalIngressResponse",
    "CallbackHTTPExceptionResponse",
    "CallbackRejectedIngressResponse",
    "CallbackRejectedResponse",
    "CallbackResultAcceptedResponse",
    "CallbackResultIngressResponse",
    "build_callback_event_accepted_response",
    "build_callback_external_accepted_response",
    "build_callback_rejected_response",
    "build_callback_result_accepted_response",
]
